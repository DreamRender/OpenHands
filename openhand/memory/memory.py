import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Callable

import openhands
from openhands.core.config import MCPConfig
from openhands.core.logger import openhands_logger as logger
from openhands.events import EventStream, EventStreamSubscriber, Event, EventSource, RecallType
from openhands.events.action import RecallAction
from openhands.events.observation import RecallObservation, NullObservation
from openhands.events.observation.agent import MicroagentKnowledge
from openhands.microagent import RepoMicroagent, KnowledgeMicroagent, BaseMicroagent, load_microagents_from_dir
from openhands.runtime import Runtime
from openhands.utils.prompt import RepositoryInfo, RuntimeInfo, ConversationInstructions

GLOBAL_MICROAGENTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(openhands.__file__)),
    'microagents',
)
"""全局MicroAgent目录"""

USER_MICROAGENTS_DIR = Path.home() / '.openhands' / 'microagents'
"""
用户MicroAgent目录

Windows: C:\\Users\\用户名\\.openhands\\microagents
Linux: /home/用户名/.openhands/microagents
macOS: /Users/用户名/.openhands/microagents
"""


class Memory:
    """
    Memory 类是一个核心组件，它充当了 Agent 的长期记忆和上下文信息检索系统。
    它并非短期对话历史记录的存储器（该功能由 ConversationMemory 负责），
    而是作为一个动态的信息提供者，根据需要向 Agent 的上下文中注入关键信息。

    核心职责:
    1. 监听检索请求：Memory模块通过订阅EventStream，专门监听RecallAction类型的事件
    2. 提供上下文信息：响应RecallAction，提供两种主要类型的上下文信息：
        - Workspace Context：在对话开始时，提供关于代码仓库、运行时环境和任务指令的全面信息
        - Knowledge：在对话过程中，根据用户或Agent信息中的关键词，动态触发并提供来自MicroAgents的专业知识
    3. 管理MicroAgents：负责加载和管理来自不同来源（全局、用户自定义、工作区）的MicroAgents，这些MicroAgents是Memory的知识主要来源
    4. 发布观察结果：将检索到的信息打包成RecallObservation，并发布到EventStream中，供系统的其他部分（主要是 ConversationMemory）消费
    """

    sid: str
    """
    Session ID
    用于唯一地标识当前的对话或任务会话。Memory 实例使用它来确保它只监听和响应属于自己会话的事件
    """

    event_stream: EventStream
    """
    事件流实例
    这是整个系统的中央消息总线。Memory类通过subscribe这个事件流来接收RecallAction，并在处理完请求之后，通过add_event发布一个包含所需信息的RecallObservation
    """

    status_callback: Callable | None
    """
    状态回调函数（可选）
    当Memory模块在处理过程中遇到错误，会调用这个函数将错误信息发送到前端或日志系统
    """

    loop: asyncio.AbstractEventLoop | None
    """
    异步事件循环的引用，用于跨线程异步调用。

    为什么需要缓存事件循环：
    1. 避免每次调用 get_running_loop() 的开销
    2. 确保所有异步操作在同一个事件循环中执行
    3. 支持从不同线程安全地提交异步任务

    使用场景：
    - send_error_message() 中的跨线程异步调用
    - 确保 status_callback 在正确的上下文中执行
    """

    repo_microagents: dict[str, RepoMicroagent]
    """
    与当前代码仓库相关的MicroAgents字典
    用于存储与当前代码库紧密相关的RepoMicroagent。这些MicroAgent通常包含项目的构建说明、编码规范等，他们的内容会在处理Workspace Context的RecallAction时被加载并提供给Agent
    """

    knowledge_microagents: dict[str, KnowledgeMicroagent]
    """
    通用专业知识MicroAgents字典
    用于存储通用的或特定领域的KnowledgeMicroagent。这些Microagent通过预设的关键词triggers触发。当Agent的对话或思考中出现这些关键词时，Memory模块会检索相对应的Microagent的内容，并作为上下文提供给Agent
    """

    repository_info: RepositoryInfo | None
    """
    存储当前正在操作的代码仓库的基本信息
    """

    runtime_info: RuntimeInfo | None
    """
    存储 Agent 运行时的环境信息
    """

    conversation_instructions: ConversationInstructions | None
    """
    存储针对整个对话的特定指令，这些指令通常比用户的初始任务更具指导性或约束性
    """

    def __init__(self, event_stream: EventStream, sid: str, status_callback: Callable | None = None):
        """
        初始化Memory模块

        Args:
            event_stream: 事件流实例
            sid: Session ID
            status_callback: 状态回调函数（可选）
        """
        self.event_stream = event_stream
        self.sid = sid
        self.status_callback = status_callback

        # 初始化dict
        self.repo_microagents = {}
        self.knowledge_microagents = {}

        # 订阅EventStream
        self.event_stream.subscribe(
            EventStreamSubscriber.MEMORY,
            self.on_event,
            self.sid,
        )

        # 加载MicroAgents
        self._load_global_microagents()
        self._load_user_microagents()

    def on_event(self, event: Event):
        """
         同步事件处理入口点。

         这个方法是 EventStream 的回调接口，必须是同步的，因为：
         1. EventStream 的订阅机制期望同步回调函数
         2. 事件处理可能在不同的线程中被调用
         3. 需要与现有的同步代码兼容

         但实际的事件处理逻辑 (_on_event) 是异步的，因为：
         1. 可能需要进行异步 I/O 操作（文件读取、网络请求等）
         2. 需要与其他异步组件交互
         3. 避免阻塞事件循环

         解决方案：使用 run_until_complete() 在同步上下文中运行异步代码

         Args:
             event: 来自 EventStream 的事件对象
         """
        # asyncio.get_event_loop().run_until_complete() 的作用：
        # 1. 获取当前线程的事件循环
        # 2. 运行异步协程直到完成
        # 3. 返回协程的结果
        # 4. 如果当前线程没有事件循环，会创建一个新的
        asyncio.get_event_loop().run_until_complete(self._on_event(event))

    async def _on_event(self, event: Event):
        """
        异步处理来自 EventStream 的事件。

        这是实际的事件处理逻辑，设计为异步的原因：
        1. 可能需要异步 I/O 操作（读取 microagent 文件、网络请求等）
        2. 避免阻塞主事件循环，保持系统响应性
        3. 支持并发处理多个事件
        4. 与其他异步组件（如 EventStream）更好地集成

        主要处理 RecallAction 事件：
        - WORKSPACE_CONTEXT: 提供工作空间上下文信息
        - KNOWLEDGE: 提供 microagent 知识信息

        Args:
            event: 需要处理的事件对象
        """
        try:
            if not isinstance(event, RecallAction):
                # Memory模块只处理RecallAction事件
                return

            observe: RecallObservation | NullObservation | None = None

            # 如果是Workspace Context Recall事件（在第一个用户消息时触发）
            # 创建并添加一个RecallObservation，包含仓库、运行时、指令等信息
            # 也包括任何匹配到的KnowledgeMicroagent信息
            if (event.source == EventSource.USER
                    and event.recall_type == RecallType.WORKSPACE_CONTEXT):
                logger.debug('Workspace context recall')
                observe = self._on_workspace_context_recall(event)
            elif (
                    event.source == EventSource.USER
                    or event.source == EventSource.AGENT
            ) and event.recall_type == RecallType.KNOWLEDGE:
                logger.debug(
                    f'Microagent knowledge recall from {event.source} message'
                )
                observe = self._on_microagent_recall(event)
            else:
                return

            if observe is None:
                observe = NullObservation(content='')

            # 当Agent需要上下文信息时，会发布一个RecallAction Event。这个Event有一个唯一的id。主流程执行到此时会暂停，等待一个与这个id相关的响应。
            # Memory模块监听到这个RecallAction，并开始准备需要的数据，准备好之后会创建一个RecallObservation事件
            # 而这一行的作用就是，将RecallObservation的_cause属性设置为原始请求动作RecallAction的id，简历一个明确的配对关系
            # 系统的事件管理器或者其他等待方可以通过这个_cause链接，识别出这个观察结果正是他等待的那个特定请求的响应，从而结束等待状态
            observe._cause = event.id
            # 发布观察结果
            self.event_stream.add_event(observe, EventSource.ENVIRONMENT)

        except Exception as e:
            error_str = f'Error: {str(e.__class__.__name__)}'
            logger.error(error_str)
            self.send_error_message('STATUS$ERROR_MEMORY', error_str)
            return

    def _on_workspace_context_recall(self, event: RecallAction) -> RecallObservation | None:
        """
        将Repository和Runtime信息作为RecallObservation添加到EventStream中
        支持多个Repository MicroAgent，不同的MicroAgent的内容通过换行符连接

        Workspace Context包含：
        1、Repository Info
        2、Runtime Info
        3、Repository Instructions
        4、MicroAgent Knowledge
        """

        repo_instructions = ''

        # 从所有Repo MicroAgent中收集信息
        for microagent in self.repo_microagents.values():
            if repo_instructions:
                repo_instructions += '\n\n'
            repo_instructions += microagent.content

        microagent_knowledge = self._find_microagent_knowledge(event.query)

        if (
                self.repository_info
                or self.runtime_info
                or repo_instructions
                or microagent_knowledge
                or self.conversation_instructions
        ):
            obs = RecallObservation(
                recall_type=RecallType.WORKSPACE_CONTEXT,
                repo_name=self.repository_info.repo_name
                if self.repository_info and self.repository_info.repo_name is not None
                else '',
                repo_directory=self.repository_info.repo_directory
                if self.repository_info
                   and self.repository_info.repo_directory is not None
                else '',
                repo_instructions=repo_instructions if repo_instructions else '',
                runtime_hosts=self.runtime_info.available_hosts
                if self.runtime_info and self.runtime_info.available_hosts is not None
                else {},
                additional_agent_instructions=self.runtime_info.additional_agent_instructions
                if self.runtime_info
                   and self.runtime_info.additional_agent_instructions is not None
                else '',
                microagent_knowledge=microagent_knowledge,
                content='Added workspace context',
                date=self.runtime_info.date if self.runtime_info is not None else '',
                custom_secrets_descriptions=self.runtime_info.custom_secrets_descriptions
                if self.runtime_info is not None
                else {},
                conversation_instructions=self.conversation_instructions.content
                if self.conversation_instructions is not None
                else '',
            )
            return obs
        return None

    def _on_microagent_recall(
            self,
            event: RecallAction,
    ) -> RecallObservation | None:
        """
        当微代理动作触发微代理时，创建一个带有结构化数据的 RecallObservation。
        """

        # 根据查询找到任何匹配的微代理
        microagent_knowledge = self._find_microagent_knowledge(event.query)

        # 如果有任何信息，则创建观察事件
        if microagent_knowledge:
            obs = RecallObservation(
                recall_type=RecallType.KNOWLEDGE,
                microagent_knowledge=microagent_knowledge,
                content='Retrieved knowledge from microagents',
            )
            return obs
        return None

    def _find_microagent_knowledge(self, query: str) -> list[MicroagentKnowledge]:
        """
        根据查询找到MicroAgent Knowledge。

        Args:
            query: 用于搜索MicroAgent触发器的查询。

        Returns:
            匹配触发器的 MicroagentKnowledge 对象列表。
        """
        recalled_content: list[MicroagentKnowledge] = []

        # 跳过空查询
        if not query:
            return recalled_content

        # 在查询中搜索微代理触发器
        for name, microagent in self.knowledge_microagents.items():
            # MicroAgent Match Trigger的方法是将Query转换成小写之后，检查MicroAgent的triggers（list[str]）里面有没有对应的匹配
            trigger = microagent.match_trigger(query)

            if trigger:
                logger.info("Microagent '%s' triggered by keyword '%s'", name, trigger)
                recalled_content.append(
                    MicroagentKnowledge(
                        name=microagent.name,
                        trigger=trigger,
                        content=microagent.content,
                    )
                )

        return recalled_content

    def load_user_workspace_microagents(
            self, user_microagents: list[BaseMicroagent]
    ) -> None:
        """
        此方法从用户clone的Repository或Workspace目录中加载MicroAgent。
        通常在克隆工作区后由 agent_session 或 setup 调用。
        """
        logger.info(
            'Loading user workspace microagents: %s', [m.name for m in user_microagents]
        )
        for user_microagent in user_microagents:
            if isinstance(user_microagent, KnowledgeMicroagent):
                self.knowledge_microagents[user_microagent.name] = user_microagent
            elif isinstance(user_microagent, RepoMicroagent):
                self.repo_microagents[user_microagent.name] = user_microagent

    def _load_global_microagents(self) -> None:
        """从全局 microagents_dir 加载 MicroAgent"""
        repo_agents, knowledge_agents = load_microagents_from_dir(
            GLOBAL_MICROAGENTS_DIR
        )
        for name, agent_knowledge in knowledge_agents.items():
            self.knowledge_microagents[name] = agent_knowledge
        for name, agent_repo in repo_agents.items():
            self.repo_microagents[name] = agent_repo

    def _load_user_microagents(self) -> None:
        """
        从用户主目录 (~/.openhands/microagents/) 加载MicroAgent。
        如果目录不存在，则创建它。
        """
        try:
            # 如果用户MicroAgent目录不存在，则创建
            os.makedirs(USER_MICROAGENTS_DIR, exist_ok=True)

            # 从用户目录加载MicroAgent
            repo_agents, knowledge_agents = load_microagents_from_dir(
                USER_MICROAGENTS_DIR
            )

            for name, agent_knowledge in knowledge_agents.items():
                self.knowledge_microagents[name] = agent_knowledge
            for name, agent_repo in repo_agents.items():
                self.repo_microagents[name] = agent_repo
        except Exception as e:
            logger.warning(
                f'Failed to load user microagents from {USER_MICROAGENTS_DIR}: {str(e)}'
            )

    def get_microagent_mcp_tools(self) -> list[MCPConfig]:
        """
        从所有RepoMicroagent中收集并返回 MCP 工具的配置列表。

        这个函数专门设计用于聚合那些与当前工作仓库直接相关的、应始终可用的工具。
        它通过遍历 `self.repo_microagents` 字典来实现这一功能。`RepoMicroagent` 被认为是“始终激活”的，
        因为它们提供了特定于当前代码仓库的上下文和功能，与需要特定关键词触发的KnowledgeMicroagent不同。

        Returns:
            list[MCPConfig]: 一个包含从所有激活的RepoMicroagent中收集到的 `MCPConfig` 对象的列表。
                           这个列表后续将被用于配置 MCP 代理，从而使这些工具可供主 Agent 调用。
        """
        mcp_configs: list[MCPConfig] = []

        # 该函数会检查每个RepoMicroagent的元数据中是否定义了 `mcp_tools`。如果存在，便将其 `MCPConfig` 添加到返回的列表中
        for agent in self.repo_microagents.values():
            if agent.metadata.mcp_tools:
                mcp_configs.append(agent.metadata.mcp_tools)
                logger.debug(
                    f'Found MCP tools in repo microagent {agent.name}: {agent.metadata.mcp_tools}'
                )

        return mcp_configs

    def set_repository_info(self, repo_name: str, repo_directory: str) -> None:
        """存储仓库信息，以便在Observation中引用"""
        if repo_name or repo_directory:
            self.repository_info = RepositoryInfo(repo_name, repo_directory)
        else:
            self.repository_info = None

    def set_runtime_info(
        self,
        runtime: Runtime,
        custom_secrets_descriptions: dict[str, str],
    ) -> None:
        """存储运行时信息（例如 web 主机、端口等）。"""
        utc_now = datetime.now(timezone.utc)
        date = str(utc_now.date())

        if runtime.web_hosts or runtime.additional_agent_instructions:
            self.runtime_info = RuntimeInfo(
                available_hosts=runtime.web_hosts,
                additional_agent_instructions=runtime.additional_agent_instructions,
                date=date,
                custom_secrets_descriptions=custom_secrets_descriptions,
            )
        else:
            self.runtime_info = RuntimeInfo(
                date=date,
                custom_secrets_descriptions=custom_secrets_descriptions,
            )

    def set_conversation_instructions(
        self, conversation_instructions: str | None
    ) -> None:
        """
        设置对话的上下文信息。
        这是 agent 可能需要的信息。
        """
        self.conversation_instructions = ConversationInstructions(
            content=conversation_instructions or ''
        )

    def send_error_message(self, message_id: str, message: str):
        """
        发送错误消息到前端客户端。

        这个方法解决了一个复杂的异步编程问题：
        - 本方法是同步的，可能在任何线程中被调用
        - 但 status_callback 可能需要在特定的异步事件循环中执行
        - 需要确保跨线程调用的安全性

        Args:
            message_id: 消息ID，用于前端识别和处理
            message: 错误消息内容
        """
        if self.status_callback:
            try:
                # 步骤1: 获取当前线程的事件循环
                # 如果 self.loop 为空，说明这是第一次调用，需要获取并缓存事件循环
                if self.loop is None:
                    # asyncio.get_running_loop() 获取当前线程中正在运行的事件循环
                    # 如果当前线程没有运行事件循环，会抛出 RuntimeError
                    self.loop = asyncio.get_running_loop()

                # 步骤2: 跨线程安全地执行异步函数
                # asyncio.run_coroutine_threadsafe() 的作用：
                # 1. 允许从任何线程向指定的事件循环提交协程
                # 2. 返回一个 concurrent.futures.Future 对象
                # 3. 确保线程安全，避免竞态条件
                #
                # 参数说明：
                # - 第一个参数：要执行的协程 (_send_status_message)
                # - 第二个参数：目标事件循环 (self.loop)
                asyncio.run_coroutine_threadsafe(
                    self._send_status_message('error', message_id, message),
                    self.loop
                )

            except RuntimeError as e:
                # 可能的 RuntimeError 情况：
                # 1. 当前线程没有运行的事件循环 (get_running_loop 失败)
                # 2. 目标事件循环已经关闭或无效
                # 3. 跨线程调用时的其他异步相关错误
                logger.error(
                    f'Error sending status message: {e.__class__.__name__}',
                    stack_info=False,
                )

    async def _send_status_message(self, msg_type: str, id: str, message: str):
        """
        异步发送状态消息到客户端。

        这是一个异步方法，必须在事件循环中运行。
        通过 send_error_message() 的跨线程机制调用，确保：
        1. 在正确的事件循环中执行
        2. 不阻塞调用线程
        3. 支持异步的 status_callback

        Args:
            msg_type: 消息类型 ('error', 'info', 'warning' 等)
            id: 消息ID，用于前端消息去重和状态跟踪
            message: 消息内容
        """
        if self.status_callback:
            self.status_callback(msg_type, id, message)
