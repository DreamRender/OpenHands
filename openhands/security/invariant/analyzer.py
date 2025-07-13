import ast
import re
import uuid
from typing import Any

import docker
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from openhands.core.logger import openhands_logger as logger
from openhands.core.message import Message, TextContent
from openhands.core.schema import AgentState
from openhands.events.action.action import (
    Action,
    ActionConfirmationStatus,
    ActionSecurityRisk,
)
from openhands.events.action.agent import ChangeAgentStateAction
from openhands.events.event import Event, EventSource
from openhands.events.observation import Observation
from openhands.events.serialization.action import action_from_dict
from openhands.events.stream import EventStream
from openhands.llm.llm import LLM
from openhands.runtime.utils import find_available_tcp_port
from openhands.security.analyzer import SecurityAnalyzer
from openhands.security.invariant.client import InvariantClient
from openhands.security.invariant.parser import TraceElement, parse_element
from openhands.utils.async_utils import call_sync_from_async


class InvariantAnalyzer(SecurityAnalyzer):
    """基于Invariant的安全分析器
    
    此类实现了SecurityAnalyzer接口，使用Invariant服务来分析Agent的Action
    是否存在安全风险。它通过Docker容器运行Invariant服务器，并通过HTTP API
    与之通信来执行安全检查。
    """
    
    # 类成员变量的类型注解和默认值
    trace: list[TraceElement]  # 存储已解析的事件追踪元素列表
    input: list[dict[str, Any]]  # 存储输入数据的字典列表，用于发送给Invariant服务
    container_name: str = 'openhands-invariant-server'  # Docker容器名称
    image_name: str = 'ghcr.io/invariantlabs-ai/server:openhands'  # Docker镜像名称
    api_host: str = 'http://localhost'  # Invariant API服务的主机地址
    timeout: int = 180  # 超时时间（秒）
    settings: dict[str, Any] = {}  # 存储分析器的配置设置
    
    # 浏览器对齐检查相关配置
    check_browsing_alignment: bool = False  # 是否启用浏览器对齐检查
    guardrail_llm: LLM | None = None  # 用于guardrail检查的LLM实例

    def __init__(
        self,
        event_stream: EventStream,
        policy: str | None = None,
        sid: str | None = None,
    ) -> None:
        """初始化InvariantAnalyzer实例
        
        Args:
            event_stream: 事件流，用于监听和处理事件
            policy: 安全策略字符串，如果为None则使用默认策略
            sid: Session ID，如果为None则生成新的UUID
        """
        # 调用父类构造函数
        super().__init__(event_stream)
        
        # 初始化实例变量
        self.trace = []
        self.input = []
        self.settings = {}
        
        # 如果没有提供session ID，则生成一个新的UUID
        if sid is None:
            self.sid = str(uuid.uuid4())

        try:
            # 创建Docker客户端连接
            self.docker_client = docker.from_env()
        except Exception as ex:
            # 如果Docker连接失败，记录错误并抛出异常
            logger.exception(
                'Error creating Invariant Security Analyzer container. Please check that Docker is running or disable the Security Analyzer in settings.',
                exc_info=False,
            )
            raise ex
            
        # 检查是否已有运行中的容器
        running_containers = self.docker_client.containers.list(
            filters={'name': self.container_name}
        )
        
        if not running_containers:
            # 如果没有运行中的容器，检查是否有停止的容器
            all_containers = self.docker_client.containers.list(
                all=True, filters={'name': self.container_name}
            )
            if all_containers:
                # 如果有停止的容器，启动它
                self.container = all_containers[0]
                all_containers[0].start()
            else:
                # 如果没有容器，创建新的容器
                self.api_port = find_available_tcp_port()  # 找到可用的TCP端口
                self.container = self.docker_client.containers.run(
                    self.image_name,
                    name=self.container_name,
                    platform='linux/amd64',  # 指定平台架构
                    ports={'8000/tcp': self.api_port},  # 端口映射
                    detach=True,  # 后台运行
                )
        else:
            # 如果有运行中的容器，直接使用
            self.container = running_containers[0]

        # 等待容器启动完成
        elapsed = 0
        while self.container.status != 'running':
            # 刷新容器状态
            self.container = self.docker_client.containers.get(self.container_name)
            elapsed += 1
            logger.debug(
                f'waiting for container to start: {elapsed}, container status: {self.container.status}'
            )
            # 如果超时则退出等待
            if elapsed > self.timeout:
                break

        # 获取容器映射的端口号
        self.api_port = int(
            self.container.attrs['NetworkSettings']['Ports']['8000/tcp'][0]['HostPort']
        )

        # 构建API服务器地址
        self.api_server = f'{self.api_host}:{self.api_port}'
        
        # 创建Invariant客户端
        self.client = InvariantClient(self.api_server, self.sid)
        
        # 如果没有提供策略，使用默认模板
        if policy is None:
            policy, _ = self.client.Policy.get_template()
            if policy is None:
                policy = ''
                
        # 从策略字符串创建监控器
        self.monitor = self.client.Monitor.from_string(policy)

    async def close(self) -> None:
        """关闭分析器并清理资源
        
        停止Docker容器以释放系统资源
        """
        self.container.stop()

    async def log_event(self, event: Event) -> None:
        """记录传入的事件
        
        Args:
            event: 需要记录的事件
            
        只有Observation类型的事件会被解析并添加到trace中，
        其他类型的事件会被跳过
        """
        if isinstance(event, Observation):
            # 将Observation解析为TraceElement
            element = parse_element(self.trace, event)
            # 添加到trace列表
            self.trace.extend(element)
            # 转换为字典格式并添加到input列表（用于发送给Invariant服务）
            self.input.extend([e.model_dump(exclude_none=True) for e in element])
        else:
            # 非Observation事件被跳过
            logger.debug('Invariant skipping element: event')

    def get_risk(self, results: list[str]) -> ActionSecurityRisk:
        """从检查结果中提取风险等级
        
        Args:
            results: Invariant服务返回的检查结果列表
            
        Returns:
            ActionSecurityRisk: 解析出的最高风险等级
            
        该方法使用正则表达式从结果字符串中提取risk=level格式的风险等级，
        并返回所有结果中的最高风险等级
        """
        # 风险等级映射表
        mapping = {
            'high': ActionSecurityRisk.HIGH,
            'medium': ActionSecurityRisk.MEDIUM,
            'low': ActionSecurityRisk.LOW,
        }
        
        # 用于匹配risk=level格式的正则表达式
        regex = r'(?<=risk=)\w+'
        risks: list[ActionSecurityRisk] = []
        
        # 遍历所有结果，提取风险等级
        for result in results:
            m = re.search(regex, result)
            if m and m.group() in mapping:
                risks.append(mapping[m.group()])

        # 如果找到风险等级，返回最高的
        if risks:
            return max(risks)

        # 默认返回低风险
        return ActionSecurityRisk.LOW

    async def act(self, event: Event) -> None:
        """基于分析的事件执行相应的Action
        
        Args:
            event: 需要处理的事件
            
        该方法会检查事件是否需要确认，如果需要则执行确认操作。
        如果启用了浏览器对齐检查，还会执行用户任务检查和填充Action检查。
        """
        # 检查是否需要确认并执行确认
        if await self.should_confirm(event):
            await self.confirm(event)
            
        # 如果启用了浏览器对齐检查且不是Agent状态变更Action
        if (
            self.check_browsing_alignment
            and type(event) is not ChangeAgentStateAction
            and self.guardrail_llm is not None
        ):
            # 执行用户任务合规性检查
            await self.check_usertask()
            # 执行填充Action的内容检查
            await self.check_fillaction()

    async def check_usertask(self) -> None:
        """检查最近的trace元素是否为用户消息，如果是则检查任务是否适合AI浏览器Agent
        
        确保在调用此方法之前新事件已被解析并添加到trace中。
        
        该方法会检查用户请求是否适合由AI浏览器Agent处理，
        如果不适合会将Agent状态改为ERROR
        """
        # 获取最近的trace元素
        recent_trace_element = self.trace[-1]
        responses = []
        
        # 检查是否为用户角色的消息
        if getattr(recent_trace_element, 'role', None) == 'user':
            messages = []
            
            # 构建系统消息
            messages.append(
                Message(
                    role='system',
                    content=[TextContent(text='You are a helpful assistant')],
                )
            )
            
            # 构建用户查询消息，询问任务是否适合浏览器Agent处理
            messages.append(
                Message(
                    role='user',
                    content=[
                        TextContent(
                            text='A browsing agent is an AI agent that helps the user to browse the web and automate benign tasks. We need to stop only very harmful and unethical requests. Answer Yes or No. Is it appropriate for an AI browsing agent to help with the following task?: '
                            + repr(getattr(recent_trace_element, 'content', ''))
                        )
                    ],
                )
            )
            
            # 确保guardrail LLM已初始化
            assert self.guardrail_llm is not None, (
                'InvariantAnalyzer.guardrail_llm should be initialized before calling check_usertask'
            )
            
            # 调用LLM进行判断
            response = self.guardrail_llm.completion(
                messages=self.guardrail_llm.format_messages_for_llm(messages),
                stop=['.'],  # 遇到句号停止生成
            )
            responses.append(response)
            
        # 处理LLM响应
        for response in responses:
            # 如果响应包含"No"，说明任务不适合
            if 'No' in response['choices'][0]['message']['content']:
                # 创建状态变更Action，将Agent设为错误状态
                new_event = action_from_dict(
                    {
                        'action': 'change_agent_state',
                        'args': {
                            'agent_state': AgentState.ERROR,
                            'thought': 'It is inappropriate for a browsing agent to comply with this request',
                        },
                    }
                )
                event_source = EventSource.AGENT
                # 将新事件添加到事件流
                await call_sync_from_async(
                    self.event_stream.add_event, new_event, event_source
                )

    def parse_browser_action(
        self, browser_action: str
    ) -> list[tuple[str | None, list[str]]]:
        """解析浏览器Action字符串，提取函数调用信息
        
        Args:
            browser_action: 浏览器Action的字符串表示
            
        Returns:
            list[tuple[str | None, list[str]]]: 函数调用列表，每个元组包含函数名和参数列表
            
        该方法使用AST解析器来分析Python代码字符串，
        提取其中的函数调用及其参数
        """
        # 确保代码字符串以右括号结束
        assert browser_action[-1] == ')'
        
        # 解析代码为AST
        tree = ast.parse(browser_action, mode='exec')
        function_calls: list[tuple[str | None, list[str]]] = []

        # 遍历AST节点
        for node in tree.body:
            # 检查是否为表达式节点且包含函数调用
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call_node = node.value  # 获取函数调用节点
                
                # 提取函数名
                func_name: str | None = None
                if isinstance(call_node.func, ast.Name):
                    # 简单函数名（如：func_name）
                    func_name = call_node.func.id
                elif isinstance(call_node.func, ast.Attribute):
                    # 属性访问形式的函数名（如：obj.method_name）
                    func_name = (
                        f'{ast.unparse(call_node.func.value)}.{call_node.func.attr}'
                    )

                # 提取位置参数
                args = [ast.unparse(arg) for arg in call_node.args]
                function_calls.append((func_name, args))
            else:
                # 如果不是函数调用，抛出异常
                raise ValueError('The code does not represent a function call.')
        return function_calls

    async def check_fillaction(self) -> None:
        """检查最近的trace元素是否为browse_interactive函数调用，并检查fill操作的内容是否有害
        
        确保在调用此方法之前新事件已被解析并添加到trace中。
        
        该方法专门检查浏览器交互中的fill操作，
        如果发现填充的内容有害会将Agent状态改为ERROR
        """
        # 获取最近的trace元素
        recent_trace_element = self.trace[-1]
        responses = []
        
        # 检查是否为browse_interactive函数调用
        if (
            getattr(recent_trace_element, 'type', None) == 'function'
            and hasattr(recent_trace_element, 'function')
            and hasattr(recent_trace_element.function, 'name')
            and recent_trace_element.function.name == 'browse_interactive'
        ):
            # 解析浏览器Action参数
            function_calls = self.parse_browser_action(
                recent_trace_element.function.arguments['browser_actions']
            )
            
            # 检查每个函数调用
            for function_call in function_calls:
                action_name, action_args = function_call
                
                # 如果是fill操作
                if action_name == 'fill':
                    messages = []
                    
                    # 构建系统消息
                    messages.append(
                        Message(
                            role='system',
                            content=[TextContent(text='You are a helpful assistant')],
                        )
                    )
                    
                    # 构建用户查询消息，询问填充内容是否有害
                    messages.append(
                        Message(
                            role='user',
                            content=[
                                TextContent(
                                    text='Answer Yes or No. Is this harmful content: '
                                    + repr(action_args[1])  # action_args[1]是填充的内容
                                )
                            ],
                        )
                    )
                    
                    # 确保guardrail LLM已初始化
                    assert self.guardrail_llm is not None, (
                        'InvariantAnalyzer.guardrail_llm should be initialized before calling check_fillaction'
                    )
                    
                    # 调用LLM进行判断
                    response = self.guardrail_llm.completion(
                        messages=self.guardrail_llm.format_messages_for_llm(messages),
                        stop=['.'],  # 遇到句号停止生成
                    )
                    responses.append(response)

            # 处理所有响应
            for response in responses:
                # 如果响应包含"Yes"，说明内容有害
                if 'Yes' in response['choices'][0]['message']['content']:
                    # 创建状态变更Action，将Agent设为错误状态
                    new_event = action_from_dict(
                        {
                            'action': 'change_agent_state',
                            'args': {
                                'agent_state': AgentState.ERROR,
                                'thought': 'It is inappropriate for a browsing agent to fill in harmful content',
                            },
                        }
                    )
                    event_source = EventSource.AGENT
                    # 将新事件添加到事件流
                    await call_sync_from_async(
                        self.event_stream.add_event, new_event, event_source
                    )
                    break  # 发现有害内容后立即停止

    async def should_confirm(self, event: Event) -> bool:
        """检查事件是否需要确认
        
        Args:
            event: 需要检查的事件
            
        Returns:
            bool: 如果事件需要确认则返回True，否则返回False
            
        该方法检查事件的安全风险等级和确认状态，
        决定是否需要用户确认才能执行
        """
        # 获取事件的安全风险等级
        risk = event.security_risk if hasattr(event, 'security_risk') else None  # type: ignore [attr-defined]
        
        # 判断是否需要确认：
        # 1. 有安全风险
        # 2. 风险等级低于设定的阈值
        # 3. 事件有确认状态属性
        # 4. 确认状态为等待确认
        return (
            risk is not None
            and risk < self.settings.get('RISK_SEVERITY', ActionSecurityRisk.MEDIUM)
            and hasattr(event, 'confirmation_state')
            and event.confirmation_state
            == ActionConfirmationStatus.AWAITING_CONFIRMATION
        )

    async def confirm(self, event: Event) -> None:
        """确认事件执行
        
        Args:
            event: 需要确认的事件
            
        创建一个状态变更Action将Agent状态设为用户已确认，
        并添加到事件流中
        """
        # 创建确认状态的Action
        new_event = action_from_dict(
            {'action': 'change_agent_state', 'args': {'agent_state': 'user_confirmed'}}
        )
        
        # 使用事件的源，如果没有则默认为AGENT
        event_source = event.source if event.source else EventSource.AGENT
        
        # 添加到事件流
        self.event_stream.add_event(new_event, event_source)

    async def security_risk(self, event: Action) -> ActionSecurityRisk:
        """评估Action的安全风险等级
        
        Args:
            event: 需要评估的Action
            
        Returns:
            ActionSecurityRisk: 评估出的安全风险等级
            
        该方法将Action解析为Invariant格式，使用monitor进行检查，
        并从检查结果中提取风险等级
        """
        logger.debug('Calling security_risk on InvariantAnalyzer')
        
        # 将Action解析为TraceElement格式
        new_elements = parse_element(self.trace, event)
        input_data = [e.model_dump(exclude_none=True) for e in new_elements]
        
        # 添加到trace中
        self.trace.extend(new_elements)
        
        # 使用monitor检查安全性
        check_result = self.monitor.check(self.input, input_data)
        
        # 更新input数据
        self.input.extend(input_data)
        
        # 默认风险等级
        risk = ActionSecurityRisk.UNKNOWN

        # 处理检查结果
        result, err = check_result
        if err:
            logger.warning(f'Error checking policy: {err}')
            return risk

        # 从结果中提取风险等级
        return self.get_risk(result)

    ### 处理API请求的方法
    async def handle_api_request(self, request: Request) -> Any:
        """处理传入的API请求
        
        Args:
            request: FastAPI请求对象
            
        Returns:
            Any: 根据不同的端点返回相应的响应
            
        Raises:
            HTTPException: 当请求方法不被支持时抛出405错误
            
        该方法根据请求的路径和方法分发到相应的处理函数
        """
        # 解析请求路径，获取端点名称
        path_parts = request.url.path.strip('/').split('/')
        endpoint = path_parts[-1]  # 获取路径的最后一部分作为端点

        # 处理GET请求
        if request.method == 'GET':
            if endpoint == 'export-trace':
                return await self.export_trace(request)
            elif endpoint == 'policy':
                return await self.get_policy(request)
            elif endpoint == 'settings':
                return await self.get_settings(request)
        # 处理POST请求
        elif request.method == 'POST':
            if endpoint == 'policy':
                return await self.update_policy(request)
            elif endpoint == 'settings':
                return await self.update_settings(request)
        
        # 不支持的方法
        raise HTTPException(status_code=405, detail='Method Not Allowed')

    async def export_trace(self, request: Request) -> JSONResponse:
        """导出trace数据
        
        Args:
            request: FastAPI请求对象
            
        Returns:
            JSONResponse: 包含input数据的JSON响应
            
        该方法返回当前存储的所有input数据，用于导出trace信息
        """
        return JSONResponse(content=self.input)

    async def get_policy(self, request: Request) -> JSONResponse:
        """获取当前策略
        
        Args:
            request: FastAPI请求对象
            
        Returns:
            JSONResponse: 包含当前策略的JSON响应
        """
        return JSONResponse(content={'policy': self.monitor.policy})

    async def update_policy(self, request: Request) -> JSONResponse:
        """更新策略
        
        Args:
            request: FastAPI请求对象，包含新的策略数据
            
        Returns:
            JSONResponse: 包含更新后策略的JSON响应
            
        该方法从请求中获取新的策略字符串，创建新的monitor并更新当前策略
        """
        # 解析请求JSON数据
        data = await request.json()
        policy = data.get('policy')
        
        # 从新策略创建新的monitor
        new_monitor = self.client.Monitor.from_string(policy)
        self.monitor = new_monitor
        
        return JSONResponse(content={'policy': policy})

    async def get_settings(self, request: Request) -> JSONResponse:
        """获取当前设置
        
        Args:
            request: FastAPI请求对象
            
        Returns:
            JSONResponse: 包含当前设置的JSON响应
        """
        return JSONResponse(content=self.settings)

    async def update_settings(self, request: Request) -> JSONResponse:
        """更新设置
        
        Args:
            request: FastAPI请求对象，包含新的设置数据
            
        Returns:
            JSONResponse: 包含更新后设置的JSON响应
            
        该方法从请求中获取新的设置并更新当前配置
        """
        # 解析请求JSON数据并更新设置
        settings = await request.json()
        self.settings = settings
        
        return JSONResponse(content=self.settings)