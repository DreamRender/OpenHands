# 操作系统接口模块，用于文件和目录操作
import os
# 临时文件和目录创建模块
import tempfile
# 线程相关操作模块，用于并发控制
import threading
# 面向对象的文件系统路径操作
from pathlib import Path
# 类型提示支持
from typing import Any
# ZIP文件压缩和解压缩处理
from zipfile import ZipFile

# HTTP核心库，提供底层HTTP协议支持
import httpcore
# 现代异步HTTP客户端库
import httpx
# 重试机制库，提供装饰器和策略
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

# OpenHands核心配置类
from openhands.core.config import OpenHandsConfig
# MCP (Model Context Protocol) 相关配置
from openhands.core.config.mcp_config import (
    MCPConfig,
    MCPSSEServerConfig,
    MCPStdioServerConfig,
)
# OpenHands核心异常定义
from openhands.core.exceptions import (
    AgentRuntimeTimeoutError,
)
# 事件流处理，用于事件的发布和订阅
from openhands.events import EventStream
# 各种Action类型定义，表示Agent可以执行的操作
from openhands.events.action import (
    ActionConfirmationStatus,
    AgentThinkAction,
    BrowseInteractiveAction,
    BrowseURLAction,
    CmdRunAction,
    FileEditAction,
    FileReadAction,
    FileWriteAction,
    IPythonRunCellAction,
)
# Action基类定义
from openhands.events.action.action import Action
# 文件编辑相关的枚举和配置
from openhands.events.action.files import FileEditSource
# MCP相关的Action定义
from openhands.events.action.mcp import MCPAction
# 各种Observation类型定义，表示Action执行后的结果
from openhands.events.observation import (
    AgentThinkObservation,
    ErrorObservation,
    NullObservation,
    Observation,
    UserRejectObservation,
)
# 事件序列化和反序列化工具
from openhands.events.serialization import event_to_dict, observation_from_dict
# Action类型到类的映射关系
from openhands.events.serialization.action import ACTION_TYPE_TO_CLASS
# 第三方服务提供商的Token类型定义
from openhands.integrations.provider import PROVIDER_TOKEN_TYPE
# Runtime基类，定义了运行时环境的接口
from openhands.runtime.base import Runtime
# 插件需求定义
from openhands.runtime.plugins import PluginRequirement
# HTTP请求发送工具
from openhands.runtime.utils.request import send_request
# HTTP会话管理工具
from openhands.utils.http_session import HttpSession
# 重试停止条件工具
from openhands.utils.tenacity_stop import stop_if_should_exit


def _is_retryable_error(exception):
    """判断异常是否可重试
    
    检查异常是否为网络协议错误，这类错误通常是临时性的，可以通过重试解决。
    
    Args:
        exception: 要检查的异常对象
        
    Returns:
        bool: 如果异常可重试返回True，否则返回False
    """
    return isinstance(
        exception, (httpx.RemoteProtocolError, httpcore.RemoteProtocolError)
    )


class ActionExecutionClient(Runtime):
    """Action执行客户端基类
    
    这个类是与Action执行服务器交互的Runtime的基类。
    它包含了DockerRuntime和RemoteRuntime与action_execution_server.py中定义的HTTP服务器
    交互的共享逻辑。
    
    这个类负责：
    1. 管理与Action执行服务器的HTTP连接
    2. 序列化Action并发送到服务器执行
    3. 反序列化服务器返回的Observation结果
    4. 处理文件传输、VSCode集成、MCP协议等功能
    """

    def __init__(
        self,
        config: OpenHandsConfig,
        event_stream: EventStream,
        sid: str = 'default',
        plugins: list[PluginRequirement] | None = None,
        env_vars: dict[str, str] | None = None,
        status_callback: Any | None = None,
        attach_to_existing: bool = False,
        headless_mode: bool = True,
        user_id: str | None = None,
        git_provider_tokens: PROVIDER_TOKEN_TYPE | None = None,
    ):
        """初始化Action执行客户端
        
        Args:
            config: OpenHands配置对象
            event_stream: 事件流对象，用于事件的发布和订阅
            sid: 会话ID，默认为'default'
            plugins: 插件需求列表，可选
            env_vars: 环境变量字典，可选
            status_callback: 状态回调函数，可选
            attach_to_existing: 是否附加到现有实例，默认为False
            headless_mode: 是否为无头模式，默认为True
            user_id: 用户ID，可选
            git_provider_tokens: Git提供商的Token，可选
        """
        # HTTP会话管理器，用于与Action执行服务器通信
        self.session = HttpSession()
        # 信号量，确保同一时间只执行一个Action，避免并发冲突
        self.action_semaphore = threading.Semaphore(1)
        # Runtime关闭状态标志，避免重复关闭
        self._runtime_closed: bool = False
        # VSCode访问Token的缓存，初始为None
        self._vscode_token: str | None = None
        # 上次更新的MCP stdio服务器配置列表，用于增量更新
        self._last_updated_mcp_stdio_servers: list[MCPStdioServerConfig] = []
        
        # 调用父类初始化方法
        super().__init__(
            config,
            event_stream,
            sid,
            plugins,
            env_vars,
            status_callback,
            attach_to_existing,
            headless_mode,
            user_id,
            git_provider_tokens,
        )

    @property
    def action_execution_server_url(self) -> str:
        """获取Action执行服务器的URL
        
        这是一个抽象属性，子类必须实现此方法来提供具体的服务器URL。
        
        Returns:
            str: Action执行服务器的URL
            
        Raises:
            NotImplementedError: 子类未实现此方法时抛出
        """
        raise NotImplementedError('Action execution server URL is not implemented')

    @retry(
        retry=retry_if_exception(_is_retryable_error),  # 仅在可重试的错误时重试
        stop=stop_after_attempt(5) | stop_if_should_exit(),  # 最多重试5次或收到退出信号时停止
        wait=wait_exponential(multiplier=1, min=4, max=15),  # 指数退避，最小4秒，最大15秒
    )
    def _send_action_server_request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """向Action执行服务器发送HTTP请求
        
        这个方法包含了重试机制，能够在遇到临时性网络错误时自动重试。
        
        Args:
            method: HTTP方法（GET、POST等）
            url: 要发送请求的URL
            **kwargs: 传递给requests.request()的额外参数
            
        Returns:
            httpx.Response: 服务器的响应对象
            
        Raises:
            AgentRuntimeError: 当请求失败时抛出
        """
        return send_request(self.session, method, url, **kwargs)

    def check_if_alive(self) -> None:
        """检查Action执行服务器是否存活
        
        向服务器发送健康检查请求，确认服务器正常运行。
        
        Raises:
            Exception: 当服务器不可用时抛出异常
        """
        response = self._send_action_server_request(
            'GET',
            f'{self.action_execution_server_url}/alive',
            timeout=5,  # 5秒超时
        )
        assert response.is_closed

    def list_files(self, path: str | None = None) -> list[str]:
        """列出沙箱环境中的文件
        
        如果path为None，则列出沙箱初始工作目录（例如/workspace）中的文件。
        
        Args:
            path: 要列出文件的路径，如果为None则使用默认工作目录
            
        Returns:
            list[str]: 文件路径列表
            
        Raises:
            TimeoutError: 当操作超时时抛出
        """
        try:
            # 构造请求数据
            data = {}
            if path is not None:
                data['path'] = path

            # 发送列出文件的请求
            response = self._send_action_server_request(
                'POST',
                f'{self.action_execution_server_url}/list_files',
                json=data,
                timeout=10,  # 10秒超时
            )
            assert response.is_closed
            
            # 解析响应
            response_json = response.json()
            assert isinstance(response_json, list)
            return response_json
        except httpx.TimeoutException:
            raise TimeoutError('List files operation timed out')

    def copy_from(self, path: str) -> Path:
        """从沙箱环境复制文件到本地
        
        将沙箱中指定路径的所有文件打包成ZIP格式并下载到本地临时文件。
        
        Args:
            path: 沙箱中要复制的文件或目录路径
            
        Returns:
            Path: 包含复制文件的临时ZIP文件路径
            
        Raises:
            TimeoutError: 当复制操作超时时抛出
        """
        try:
            # 设置请求参数
            params = {'path': path}
            
            # 使用流式下载以处理大文件
            with self.session.stream(
                'GET',
                f'{self.action_execution_server_url}/download_files',
                params=params,
                timeout=30,  # 30秒超时
            ) as response:
                # 创建临时文件来保存下载的内容
                with tempfile.NamedTemporaryFile(
                    suffix='.zip', delete=False
                ) as temp_file:
                    # 分块写入文件，避免内存占用过大
                    for chunk in response.iter_bytes():
                        temp_file.write(chunk)
                    temp_file.flush()
                    return Path(temp_file.name)
        except httpx.TimeoutException:
            raise TimeoutError('Copy operation timed out')

    def copy_to(
        self, host_src: str, sandbox_dest: str, recursive: bool = False
    ) -> None:
        """从本地复制文件到沙箱环境
        
        将本地文件或目录上传到沙箱环境的指定位置。
        对于目录复制，会先创建ZIP包再上传。
        
        Args:
            host_src: 本地源文件或目录路径
            sandbox_dest: 沙箱中的目标路径
            recursive: 是否递归复制目录，默认为False
            
        Raises:
            FileNotFoundError: 当源文件不存在时抛出
        """
        # 检查源文件是否存在
        if not os.path.exists(host_src):
            raise FileNotFoundError(f'Source file {host_src} does not exist')

        # 在try块外定义临时ZIP文件路径，确保在finally块中能够访问
        temp_zip_path: str | None = None

        try:
            # 设置请求参数
            params = {'destination': sandbox_dest, 'recursive': str(recursive).lower()}
            file_to_upload = None
            upload_data = {}

            if recursive:
                # 递归复制模式：创建临时ZIP文件
                with tempfile.NamedTemporaryFile(
                    suffix='.zip', delete=False
                ) as temp_zip:
                    temp_zip_path = temp_zip.name

                try:
                    # 将源目录打包成ZIP文件
                    with ZipFile(temp_zip_path, 'w') as zipf:
                        for root, _, files in os.walk(host_src):
                            for file in files:
                                file_path = os.path.join(root, file)
                                # 计算相对路径，保持目录结构
                                arcname = os.path.relpath(
                                    file_path, os.path.dirname(host_src)
                                )
                                zipf.write(file_path, arcname)

                    self.log(
                        'debug',
                        f'Opening temporary zip file for upload: {temp_zip_path}',
                    )
                    # 打开ZIP文件准备上传
                    file_to_upload = open(temp_zip_path, 'rb')
                    upload_data = {'file': file_to_upload}
                except Exception as e:
                    # 如果ZIP创建失败，确保清理临时文件
                    if temp_zip_path and os.path.exists(temp_zip_path):
                        os.unlink(temp_zip_path)
                    raise e  # 重新抛出异常
            else:
                # 单文件复制模式：直接上传文件
                file_to_upload = open(host_src, 'rb')
                upload_data = {'file': file_to_upload}

            # 重新设置参数（确保参数正确）
            params = {'destination': sandbox_dest, 'recursive': str(recursive).lower()}

            # 发送上传请求
            response = self._send_action_server_request(
                'POST',
                f'{self.action_execution_server_url}/upload_file',
                files=upload_data,
                params=params,
                timeout=300,  # 5分钟超时，适应大文件上传
            )
            self.log(
                'debug',
                f'Copy completed: host:{host_src} -> runtime:{sandbox_dest}. Response: {response.text}',
            )
        finally:
            # 确保关闭打开的文件
            if file_to_upload:
                file_to_upload.close()

            # 清理临时ZIP文件
            if temp_zip_path and os.path.exists(temp_zip_path):
                try:
                    os.unlink(temp_zip_path)
                except Exception as e:
                    self.log(
                        'error',
                        f'Failed to delete temporary zip file {temp_zip_path}: {e}',
                    )

    def get_vscode_token(self) -> str:
        """获取VSCode连接Token
        
        获取用于连接VSCode服务器的身份验证Token。
        Token会被缓存以避免重复请求。
        
        Returns:
            str: VSCode连接Token，如果VSCode未启用或未初始化则返回空字符串
        """
        if self.vscode_enabled and self.runtime_initialized:
            # 如果有缓存的Token，直接返回
            if self._vscode_token is not None:
                return self._vscode_token
                
            # 从服务器获取新的Token
            response = self._send_action_server_request(
                'GET',
                f'{self.action_execution_server_url}/vscode/connection_token',
                timeout=10,
            )
            response_json = response.json()
            assert isinstance(response_json, dict)
            
            # 处理Token为空的情况
            if response_json['token'] is None:
                return ''
                
            # 缓存Token并返回
            self._vscode_token = response_json['token']
            return response_json['token']
        else:
            return ''

    def send_action_for_execution(self, action: Action) -> Observation:
        """发送Action到服务器执行并返回Observation结果
        
        这是核心方法，负责将Action序列化后发送到执行服务器，
        并将返回的结果反序列化为Observation对象。
        
        Args:
            action: 要执行的Action对象
            
        Returns:
            Observation: Action执行后的观察结果
            
        Raises:
            RuntimeError: 当阻塞命令没有设置超时时抛出
            ValueError: 当Action类型不存在时抛出
            AgentRuntimeTimeoutError: 当执行超时时抛出
        """
        # 特殊处理：基于LLM的文件编辑操作
        if (
            isinstance(action, FileEditAction)
            and action.impl_source == FileEditSource.LLM_BASED_EDIT
        ):
            return self.llm_based_edit(action)

        # 设置默认超时时间
        if action.timeout is None:
            if isinstance(action, CmdRunAction) and action.blocking:
                raise RuntimeError('Blocking command with no timeout set')
            # 如果是默认超时的Action，我们不阻塞命令
            action.set_hard_timeout(self.config.sandbox.timeout, blocking=False)

        # 使用信号量确保同一时间只执行一个Action
        with self.action_semaphore:
            # 检查Action是否可运行
            if not action.runnable:
                if isinstance(action, AgentThinkAction):
                    return AgentThinkObservation('Your thought has been logged.')
                return NullObservation('')
                
            # 检查Action是否等待确认
            if (
                hasattr(action, 'confirmation_state')
                and action.confirmation_state
                == ActionConfirmationStatus.AWAITING_CONFIRMATION
            ):
                return NullObservation('')
                
            # 验证Action类型是否有效
            action_type = action.action  # type: ignore[attr-defined]
            if action_type not in ACTION_TYPE_TO_CLASS:
                raise ValueError(f'Action {action_type} does not exist.')
                
            # 检查当前Runtime是否支持此Action类型
            if not hasattr(self, action_type):
                return ErrorObservation(
                    f'Action {action_type} is not supported in the current runtime.',
                    error_id='AGENT_ERROR$BAD_ACTION',
                )
                
            # 检查Action是否被用户拒绝
            if (
                getattr(action, 'confirmation_state', None)
                == ActionConfirmationStatus.REJECTED
            ):
                return UserRejectObservation(
                    'Action has been rejected by the user! Waiting for further user input.'
                )

            assert action.timeout is not None

            try:
                # 构造执行请求的数据体
                execution_action_body: dict[str, Any] = {
                    'action': event_to_dict(action),
                }
                
                # 发送执行请求
                response = self._send_action_server_request(
                    'POST',
                    f'{self.action_execution_server_url}/execute_action',
                    json=execution_action_body,
                    # 在客户端设置稍长的超时时间，以便能收到服务器端的超时错误
                    timeout=action.timeout + 5,
                )
                assert response.is_closed
                
                # 解析响应并创建Observation对象
                output = response.json()
                obs = observation_from_dict(output)
                obs._cause = action.id  # type: ignore[attr-defined]
            except httpx.TimeoutException:
                raise AgentRuntimeTimeoutError(
                    f'Runtime failed to return execute_action before the requested timeout of {action.timeout}s'
                )
            return obs

    def run(self, action: CmdRunAction) -> Observation:
        """执行命令行Action
        
        Args:
            action: 命令行执行Action
            
        Returns:
            Observation: 命令执行结果的观察
        """
        return self.send_action_for_execution(action)

    def run_ipython(self, action: IPythonRunCellAction) -> Observation:
        """执行IPython单元格Action
        
        Args:
            action: IPython单元格执行Action
            
        Returns:
            Observation: IPython执行结果的观察
        """
        return self.send_action_for_execution(action)

    def read(self, action: FileReadAction) -> Observation:
        """执行文件读取Action
        
        Args:
            action: 文件读取Action
            
        Returns:
            Observation: 文件读取结果的观察
        """
        return self.send_action_for_execution(action)

    def write(self, action: FileWriteAction) -> Observation:
        """执行文件写入Action
        
        Args:
            action: 文件写入Action
            
        Returns:
            Observation: 文件写入结果的观察
        """
        return self.send_action_for_execution(action)

    def edit(self, action: FileEditAction) -> Observation:
        """执行文件编辑Action
        
        Args:
            action: 文件编辑Action
            
        Returns:
            Observation: 文件编辑结果的观察
        """
        return self.send_action_for_execution(action)

    def browse(self, action: BrowseURLAction) -> Observation:
        """执行URL浏览Action
        
        Args:
            action: URL浏览Action
            
        Returns:
            Observation: 浏览结果的观察
        """
        return self.send_action_for_execution(action)

    def browse_interactive(self, action: BrowseInteractiveAction) -> Observation:
        """执行交互式浏览Action
        
        Args:
            action: 交互式浏览Action
            
        Returns:
            Observation: 交互式浏览结果的观察
        """
        return self.send_action_for_execution(action)

    def get_mcp_config(
        self, extra_stdio_servers: list[MCPStdioServerConfig] | None = None
    ) -> MCPConfig:
        """获取MCP (Model Context Protocol) 配置
        
        构建并返回包含所有MCP服务器配置的MCPConfig对象。
        该方法会增量更新MCP服务器配置，只有新增的服务器才会被发送到执行服务器。
        
        Args:
            extra_stdio_servers: 额外的stdio服务器配置列表，可选
            
        Returns:
            MCPConfig: 完整的MCP配置对象
        """
        import sys

        # 检查是否在Windows平台 - Windows上禁用MCP功能
        if sys.platform == 'win32':
            # 在Windows上返回空的MCP配置
            self.log('debug', 'MCP is disabled on Windows, returning empty config')
            return MCPConfig(sse_servers=[], stdio_servers=[])

        # 将Runtime添加为另一个MCP服务器
        updated_mcp_config = self.config.mcp.model_copy()

        # 获取当前的stdio服务器列表
        current_stdio_servers: list[MCPStdioServerConfig] = list(
            updated_mcp_config.stdio_servers
        )
        if extra_stdio_servers:
            current_stdio_servers.extend(extra_stdio_servers)

        # 使用__eq__操作符检查是否有新的服务器
        new_servers = [
            server
            for server in current_stdio_servers
            if server not in self._last_updated_mcp_stdio_servers
        ]

        self.log(
            'debug',
            f'adding {len(new_servers)} new stdio servers to MCP config: {new_servers}',
        )

        # 只有在有新服务器时才发送更新请求
        if new_servers:
            # 使用当前服务器和上次更新服务器的并集进行更新
            # 这确保我们不会丢失任何可能在列表中缺失的服务器
            combined_servers = current_stdio_servers.copy()
            for server in self._last_updated_mcp_stdio_servers:
                if server not in combined_servers:
                    combined_servers.append(server)

            # 将服务器配置转换为JSON格式
            stdio_tools = [
                server.model_dump(mode='json') for server in combined_servers
            ]
            stdio_tools.sort(key=lambda x: x.get('name', ''))  # 按服务器名称排序

            self.log(
                'debug',
                f'Updating MCP server with {len(new_servers)} new stdio servers (total: {len(combined_servers)})',
            )
            
            # 发送MCP服务器更新请求
            response = self._send_action_server_request(
                'POST',
                f'{self.action_execution_server_url}/update_mcp_server',
                json=stdio_tools,
                timeout=60,  # 60秒超时
            )
            result = response.json()
            
            # 处理更新结果
            if response.status_code != 200:
                self.log('warning', f'Failed to update MCP server: {response.text}')
            else:
                if result.get('router_error_log'):
                    self.log(
                        'warning',
                        f'Some MCP servers failed to be added: {result["router_error_log"]}',
                    )

                # 成功更新后，更新我们的缓存列表
                self._last_updated_mcp_stdio_servers = combined_servers.copy()
                self.log(
                    'debug',
                    f'Successfully updated MCP stdio servers, now tracking {len(combined_servers)} servers',
                )
            self.log(
                'info',
                f'Updated MCP config: {updated_mcp_config.sse_servers}',
            )
        else:
            self.log('debug', 'No new stdio servers to update')

        # 当有stdio服务器时，总是将runtime作为MCP服务器包含在内
        if len(self._last_updated_mcp_stdio_servers) > 0:
            updated_mcp_config.sse_servers.append(
                MCPSSEServerConfig(
                    url=self.action_execution_server_url.rstrip('/') + '/mcp/sse',
                    api_key=self.session_api_key,
                )
            )

        return updated_mcp_config

    async def call_tool_mcp(self, action: MCPAction) -> Observation:
        """通过MCP协议调用工具
        
        这是一个异步方法，用于通过Model Context Protocol调用外部工具。
        该方法会创建MCP客户端连接，执行工具调用，并返回结果。
        
        Args:
            action: MCP工具调用Action
            
        Returns:
            Observation: 工具调用结果的观察
        """
        import sys

        from openhands.events.observation import ErrorObservation

        # 检查是否在Windows平台 - Windows上禁用MCP功能
        if sys.platform == 'win32':
            self.log('info', 'MCP functionality is disabled on Windows')
            return ErrorObservation('MCP functionality is not available on Windows')

        # 在此处导入以避免循环导入
        from openhands.mcp.utils import call_tool_mcp as call_tool_mcp_handler
        from openhands.mcp.utils import create_mcp_clients

        # 获取更新的MCP配置
        updated_mcp_config = self.get_mcp_config()
        self.log(
            'debug',
            f'Creating MCP clients with servers: {updated_mcp_config.sse_servers}',
        )

        # 为此特定操作创建客户端
        mcp_clients = await create_mcp_clients(
            updated_mcp_config.sse_servers, updated_mcp_config.shttp_servers, self.sid
        )

        # 调用工具并返回结果
        # 不需要try/finally，因为disconnect()现在只是重置状态
        result = await call_tool_mcp_handler(mcp_clients, action)
        return result

    def close(self) -> None:
        """关闭Runtime和清理资源
        
        确保不会多次关闭会话，这在评估过程中可能会发生。
        设置关闭标志并清理HTTP会话资源。
        """
        # 确保我们不会多次关闭会话
        # 这在评估过程中可能会发生
        if self._runtime_closed:
            return
        self._runtime_closed = True
        self.session.close()
