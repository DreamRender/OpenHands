#!/usr/bin/env python3
"""OpenHands Jupyter内核执行服务器

该模块实现了一个基于Tornado框架的HTTP服务器，用于与Jupyter内核进行通信。
服务器提供代码执行功能，支持通过HTTP API接口向Jupyter内核发送代码并获取执行结果。

主要功能:
    - 与Jupyter内核建立WebSocket连接
    - 接收HTTP请求并转发到Jupyter内核执行代码
    - 处理执行结果，包括文本输出和图像输出
    - 提供心跳机制确保连接稳定性
    - 支持代码执行超时和中断机制

技术栈:
    - Tornado: 异步Web框架，用于HTTP服务和WebSocket通信
    - Jupyter内核: 代码执行环境
    - Tenacity: 重试机制库

架构说明:
    客户端 -> HTTP请求 -> ExecuteHandler -> JupyterKernel -> WebSocket -> Jupyter内核
"""

# 标准库导入
import asyncio  # 异步编程支持
import logging  # 日志记录
import os  # 操作系统接口
import re  # 正则表达式
from uuid import uuid4  # UUID生成器

# Tornado框架相关导入
import tornado
import tornado.websocket  # WebSocket支持
from tornado.escape import json_decode, json_encode, url_escape  # JSON和URL编码工具
from tornado.httpclient import AsyncHTTPClient, HTTPRequest  # 异步HTTP客户端
from tornado.ioloop import PeriodicCallback  # 定时回调
from tornado.websocket import websocket_connect  # WebSocket连接

# 重试机制库导入
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

# 配置日志记录格式和级别
logging.basicConfig(level=logging.INFO)


def strip_ansi(o: str) -> str:
    """从字符串中移除ANSI转义序列

    该函数用于清理终端输出中的颜色代码和格式控制字符，
    根据ECMA-048标准定义的ANSI转义序列进行匹配和移除。

    Args:
        o (str): 包含ANSI转义序列的原始字符串

    Returns:
        str: 移除ANSI转义序列后的纯文本字符串

    Examples:
        >>> strip_ansi("\\033[33mLorem ipsum\\033[0m")
        'Lorem ipsum'

        >>> strip_ansi("\\x1b[38;5;32mLorem ipsum\\x1b[0m")
        'Lorem ipsum'

        >>> strip_ansi("Lorem")
        'Lorem'

    注意:
        该函数基于正则表达式匹配ANSI转义序列，
        参考了ECMA-048标准：http://www.ecma-international.org/publications/files/ECMA-ST/Ecma-048.pdf
    """
    # 定义ANSI转义序列的正则表达式模式
    # \x1B\[ 匹配转义序列开始标记
    # \d+(;\d+){0,2} 匹配参数部分（数字和分号）
    # m 匹配结束字符
    pattern = re.compile(r'\x1B\[\d+(;\d+){0,2}m')

    # 使用正则表达式替换所有匹配的ANSI序列为空字符串
    stripped = pattern.sub('', o)
    return stripped


class JupyterKernel:
    """Jupyter内核管理器

    该类负责与Jupyter内核建立连接、管理WebSocket通信、执行代码，
    并处理执行结果。提供了完整的内核生命周期管理功能。

    Attributes:
        base_url (str): Jupyter服务器的HTTP基础URL
        base_ws_url (str): Jupyter服务器的WebSocket基础URL
        lang (str): 内核使用的编程语言，默认为'python'
        kernel_id (str | None): 当前内核的唯一标识符
        ws (tornado.websocket.WebSocketClientConnection | None): WebSocket连接对象
        convid (str): 会话标识符，用于日志记录和调试
        heartbeat_interval (int): 心跳间隔时间（毫秒）
        heartbeat_callback (PeriodicCallback | None): 心跳定时器对象
        initialized (bool): 内核是否已完成初始化
        tools_to_run (list[str]): 预定义工具代码列表
    """

    def __init__(self, url_suffix: str, convid: str, lang: str = 'python') -> None:
        """初始化Jupyter内核管理器

        Args:
            url_suffix (str): Jupyter服务器的地址和端口，格式为'host:port'
            convid (str): 会话标识符，用于标识当前对话或会话
            lang (str, optional): 内核语言类型，默认为'python'
        """
        # 构建HTTP和WebSocket的基础URL
        self.base_url = f'http://{url_suffix}'
        self.base_ws_url = f'ws://{url_suffix}'

        # 设置内核语言和会话信息
        self.lang = lang
        self.convid = convid

        # 初始化连接相关属性
        self.kernel_id: str | None = None  # 内核ID，连接建立后设置
        self.ws: tornado.websocket.WebSocketClientConnection | None = None  # WebSocket连接

        # 记录初始化信息
        logging.info(
            f'Jupyter kernel created for conversation {convid} at {url_suffix}'
        )

        # 配置心跳机制，用于保持WebSocket连接活跃
        self.heartbeat_interval = 10000  # 心跳间隔：10秒
        self.heartbeat_callback: PeriodicCallback | None = None  # 心跳定时器

        # 初始化状态标志
        self.initialized = False

    async def initialize(self) -> None:
        """初始化内核环境和预定义工具

        该方法在内核连接建立后调用，用于设置内核环境和加载预定义工具。
        包括设置颜色输出选项和运行预定义的工具代码。

        Raises:
            Exception: 当工具初始化失败时
        """
        # 设置Jupyter内核的颜色输出为无颜色模式
        # 这样可以避免输出中包含ANSI转义序列
        await self.execute(r'%colors nocolor')

        # 预定义工具列表，可以在这里添加需要预加载的代码
        self.tools_to_run: list[str] = [
            # TODO: 在这里可以添加预定义工具的代码
            # 例如: 'import pandas as pd'
            # 例如: 'import numpy as np'
        ]

        # 逐个执行预定义工具的初始化代码
        for tool in self.tools_to_run:
            res = await self.execute(tool)
            logging.info(f'Tool [{tool}] initialized:\n{res}')

        # 标记初始化完成
        self.initialized = True

    async def _send_heartbeat(self) -> None:
        """发送WebSocket心跳包

        该方法定期发送ping消息到Jupyter内核，用于保持连接活跃。
        如果连接断开，会尝试重新连接。

        注意:
            该方法由定时器自动调用，不应手动调用
        """
        # 检查WebSocket连接是否存在
        if not self.ws:
            return

        try:
            # 发送ping消息保持连接活跃
            self.ws.ping()
            # logging.info('Heartbeat sent...')  # 可选的调试日志
        except tornado.iostream.StreamClosedError:
            # 如果连接已关闭，尝试重新连接
            # logging.info('Heartbeat failed, reconnecting...')  # 可选的调试日志
            try:
                await self._connect()
            except ConnectionRefusedError:
                # 重连失败，记录错误信息
                logging.info(
                    'ConnectionRefusedError: Failed to reconnect to kernel websocket - Is the kernel still running?'
                )

    async def _connect(self) -> None:
        """建立与Jupyter内核的连接

        该方法负责创建新的内核（如果不存在）并建立WebSocket连接。
        包括内核创建、WebSocket连接建立和心跳机制启动。

        Raises:
            ConnectionRefusedError: 当无法连接到Jupyter服务器时
        """
        # 如果已有WebSocket连接，先关闭它
        if self.ws:
            self.ws.close()
            self.ws = None

        # 创建异步HTTP客户端用于API调用
        client = AsyncHTTPClient()

        # 如果还没有内核ID，需要创建新的内核
        if not self.kernel_id:
            n_tries = 5  # 最大重试次数

            # 尝试创建新的内核，重试机制处理服务未就绪的情况
            while n_tries > 0:
                try:
                    # 发送POST请求创建新的内核
                    response = await client.fetch(
                        '{}/api/kernels'.format(self.base_url),
                        method='POST',
                        body=json_encode({'name': self.lang}),  # 指定内核语言
                    )

                    # 解析响应并获取内核ID
                    kernel = json_decode(response.body)
                    self.kernel_id = kernel['id']
                    break  # 成功创建内核，退出重试循环

                except Exception:
                    # 内核服务可能还未就绪，等待后重试
                    n_tries -= 1
                    await asyncio.sleep(1)

            # 如果重试次数用完仍未成功，抛出连接异常
            if n_tries == 0:
                raise ConnectionRefusedError('Failed to connect to kernel')

        # 构建WebSocket连接请求
        ws_req = HTTPRequest(
            url='{}/api/kernels/{}/channels'.format(
                self.base_ws_url, url_escape(self.kernel_id)
            )
        )

        # 建立WebSocket连接
        self.ws = await websocket_connect(ws_req)
        logging.info('Connected to kernel websocket')

        # 设置心跳机制以保持连接活跃
        if self.heartbeat_callback:
            self.heartbeat_callback.stop()  # 停止之前的心跳定时器

        # 创建新的心跳定时器
        self.heartbeat_callback = PeriodicCallback(
            self._send_heartbeat, self.heartbeat_interval
        )
        self.heartbeat_callback.start()  # 启动心跳定时器

    @retry(
        retry=retry_if_exception_type(ConnectionRefusedError),  # 只重试连接错误
        stop=stop_after_attempt(3),  # 最多重试3次
        wait=wait_fixed(2),  # 每次重试间隔2秒
    )  # type: ignore
    async def execute(
            self, code: str, timeout: int = 120
    ) -> dict[str, list[str] | str]:
        """执行代码并返回结果

        该方法向Jupyter内核发送代码执行请求，等待执行完成，
        并收集所有输出结果（包括文本和图像）。

        Args:
            code (str): 要执行的代码字符串
            timeout (int, optional): 执行超时时间（秒），默认120秒

        Returns:
            dict[str, list[str] | str]: 执行结果字典，包含：
                - 'text': 文本输出内容
                - 'images': 图像输出列表（base64编码的数据URL）

        Raises:
            ConnectionRefusedError: 当无法连接到内核时（会自动重试）

        注意:
            该方法使用重试装饰器，在连接失败时会自动重试最多3次
        """
        # 检查WebSocket连接状态，如果连接断开则重新连接
        if not self.ws or self.ws.stream.closed():
            await self._connect()

        # 生成唯一的消息ID，用于标识这次执行请求
        msg_id = uuid4().hex
        assert self.ws is not None  # 类型检查断言

        # 向Jupyter内核发送代码执行请求
        # 消息格式遵循Jupyter消息协议规范
        res = await self.ws.write_message(
            json_encode(
                {
                    'header': {
                        'username': '',  # 用户名（空值）
                        'version': '5.0',  # 协议版本
                        'session': '',  # 会话ID（空值）
                        'msg_id': msg_id,  # 消息唯一标识
                        'msg_type': 'execute_request',  # 消息类型：执行请求
                    },
                    'parent_header': {},  # 父消息头（空）
                    'channel': 'shell',  # 通信通道：shell
                    'content': {
                        'code': code,  # 要执行的代码
                        'silent': False,  # 是否静默执行
                        'store_history': False,  # 是否存储到历史记录
                        'user_expressions': {},  # 用户表达式（空）
                        'allow_stdin': False,  # 是否允许标准输入
                    },
                    'metadata': {},  # 元数据（空）
                    'buffers': {},  # 缓冲区（空）
                }
            )
        )
        logging.info(f'Executed code in jupyter kernel:\n{res}')

        # 初始化输出收集列表
        outputs: list[dict] = []

        async def wait_for_messages() -> bool:
            """等待并收集内核执行消息

            该内部函数负责接收内核返回的所有消息，
            包括执行结果、错误信息、输出流等。

            Returns:
                bool: 执行是否完成
            """
            execution_done = False

            # 持续接收消息直到执行完成
            while not execution_done:
                assert self.ws is not None

                # 读取下一条WebSocket消息
                msg = await self.ws.read_message()
                if msg is None:
                    continue  # 如果消息为空，继续等待

                # 解析JSON消息
                msg_dict = json_decode(msg)
                msg_type = msg_dict['msg_type']
                parent_msg_id = msg_dict['parent_header'].get('msg_id', None)

                # 只处理与当前执行请求相关的消息
                if parent_msg_id != msg_id:
                    continue

                # 调试模式下输出详细的消息信息
                if os.environ.get('DEBUG'):
                    logging.info(
                        f'MSG TYPE: {msg_type.upper()} DONE:{execution_done}\nCONTENT: {msg_dict["content"]}'
                    )

                # 处理不同类型的消息
                if msg_type == 'error':
                    # 错误消息：提取堆栈跟踪信息
                    traceback = '\n'.join(msg_dict['content']['traceback'])
                    outputs.append({'type': 'text', 'content': traceback})
                    execution_done = True

                elif msg_type == 'stream':
                    # 流输出：标准输出或标准错误
                    outputs.append(
                        {'type': 'text', 'content': msg_dict['content']['text']}
                    )

                elif msg_type in ['execute_result', 'display_data']:
                    # 执行结果或显示数据
                    outputs.append(
                        {
                            'type': 'text',
                            'content': msg_dict['content']['data']['text/plain'],
                        }
                    )

                    # 检查是否包含图像数据
                    if 'image/png' in msg_dict['content']['data']:
                        # 构造图像数据URL并添加到输出
                        image_url = f'data:image/png;base64,{msg_dict["content"]["data"]["image/png"]}'
                        outputs.append({'type': 'image', 'content': image_url})

                elif msg_type == 'execute_reply':
                    # 执行完成消息
                    execution_done = True

            return execution_done

        async def interrupt_kernel() -> None:
            """中断内核执行

            当代码执行超时时，向内核发送中断请求停止当前执行。
            """
            client = AsyncHTTPClient()
            if self.kernel_id is None:
                return

            # 发送中断请求到内核
            interrupt_response = await client.fetch(
                f'{self.base_url}/api/kernels/{self.kernel_id}/interrupt',
                method='POST',
                body=json_encode({'kernel_id': self.kernel_id}),
            )
            logging.info(f'Kernel interrupted: {interrupt_response}')

        # 等待执行完成或超时
        try:
            execution_done = await asyncio.wait_for(wait_for_messages(), timeout)
        except asyncio.TimeoutError:
            # 执行超时，中断内核并返回超时消息
            await interrupt_kernel()
            return {'text': f'[Execution timed out ({timeout} seconds).]', 'images': []}

        # 处理和分类输出结果
        text_outputs = []  # 文本输出列表
        image_outputs = []  # 图像输出列表

        # 遍历所有输出，按类型分类
        for output in outputs:
            if output['type'] == 'text':
                text_outputs.append(output['content'])
            elif output['type'] == 'image':
                image_outputs.append(output['content'])

        # 处理文本内容
        if not text_outputs and execution_done:
            # 如果没有文本输出但执行成功，显示成功消息
            text_content = '[Code executed successfully with no output]'
        else:
            # 合并所有文本输出
            text_content = ''.join(text_outputs)

        # 移除文本内容中的ANSI转义序列
        text_content = strip_ansi(text_content)

        # 返回结构化的执行结果
        return {'text': text_content, 'images': image_outputs}

    async def shutdown_async(self) -> None:
        """异步关闭内核和清理资源

        该方法负责正确关闭Jupyter内核、断开WebSocket连接，
        并清理所有相关资源。
        """
        # 如果存在内核ID，删除内核
        if self.kernel_id:
            client = AsyncHTTPClient()
            await client.fetch(
                '{}/api/kernels/{}'.format(self.base_url, self.kernel_id),
                method='DELETE',
            )
            self.kernel_id = None  # 清除内核ID

        # 如果存在WebSocket连接，关闭连接
        if self.ws:
            self.ws.close()
            self.ws = None


class ExecuteHandler(tornado.web.RequestHandler):
    """代码执行HTTP请求处理器

    该类处理来自客户端的HTTP POST请求，将代码发送到Jupyter内核执行，
    并返回结构化的执行结果。支持文本和图像输出的处理。

    Attributes:
        jupyter_kernel (JupyterKernel): Jupyter内核管理器实例
    """

    def initialize(self, jupyter_kernel: JupyterKernel) -> None:
        """初始化请求处理器

        该方法在Tornado创建处理器实例时调用，
        用于注入Jupyter内核管理器依赖。

        Args:
            jupyter_kernel (JupyterKernel): Jupyter内核管理器实例
        """
        self.jupyter_kernel = jupyter_kernel

    async def post(self) -> None:
        """处理代码执行POST请求

        该方法接收包含代码的JSON请求，将代码发送到Jupyter内核执行，
        并返回JSON格式的执行结果。

        请求格式:
            {
                "code": "print('Hello, World!')"
            }

        响应格式:
            {
                "text": "Hello, World!\n",
                "images": []
            }

        HTTP状态码:
            200: 执行成功
            400: 请求格式错误（缺少code字段）
        """
        # 解析请求体中的JSON数据
        data = json_decode(self.request.body)
        code = data.get('code')

        # 验证请求数据
        if not code:
            # 如果没有提供代码，返回400错误
            self.set_status(400)
            self.write('Missing code')
            return

        # 执行代码并获取结果
        output = await self.jupyter_kernel.execute(code)

        # 设置响应头为JSON格式并返回结构化输出
        self.set_header('Content-Type', 'application/json')
        self.write(json_encode(output))


def make_app() -> tornado.web.Application:
    """创建并配置Tornado Web应用

    该函数创建Jupyter内核管理器实例，初始化内核环境，
    并配置Web应用的路由映射。

    Returns:
        tornado.web.Application: 配置好的Tornado Web应用实例

    环境变量:
        JUPYTER_GATEWAY_PORT: Jupyter网关端口，默认8888
        JUPYTER_GATEWAY_KERNEL_ID: 内核标识符，默认'default'
    """
    # 创建Jupyter内核管理器实例
    # 从环境变量获取配置，提供默认值
    jupyter_kernel = JupyterKernel(
        f'localhost:{os.environ.get("JUPYTER_GATEWAY_PORT", "8888")}',  # Jupyter服务地址
        os.environ.get('JUPYTER_GATEWAY_KERNEL_ID', 'default'),  # 会话标识符
    )

    # 同步初始化内核环境
    # 注意：这里使用run_until_complete在同步上下文中运行异步初始化
    asyncio.get_event_loop().run_until_complete(jupyter_kernel.initialize())

    # 创建并返回Tornado Web应用
    # 配置路由映射：/execute -> ExecuteHandler
    return tornado.web.Application(
        [
            (r'/execute', ExecuteHandler, {'jupyter_kernel': jupyter_kernel}),
        ]
    )


# 主程序入口点
if __name__ == '__main__':
    """应用程序主入口

    创建Web应用、启动HTTP服务器并开始事件循环。

    环境变量:
        JUPYTER_EXEC_SERVER_PORT: 执行服务器监听端口
    """
    # 创建Web应用实例
    app = make_app()

    # 启动HTTP服务器，监听指定端口
    # 端口号从环境变量获取
    app.listen(os.environ.get('JUPYTER_EXEC_SERVER_PORT'))

    # 启动Tornado事件循环，开始处理请求
    tornado.ioloop.IOLoop.current().start()
