from __future__ import annotations

import asyncio
import copy
import os
import time
import traceback
from typing import Callable

# 导入LiteLLM异常类，用于处理各种大语言模型相关的错误
from litellm.exceptions import (  # noqa
    APIConnectionError,          # API连接错误
    APIError,                   # API通用错误
    AuthenticationError,        # 认证错误
    BadRequestError,           # 错误的请求
    ContentPolicyViolationError, # 内容政策违规错误
    ContextWindowExceededError,  # 上下文窗口超出限制错误
    InternalServerError,        # 内部服务器错误
    NotFoundError,             # 资源未找到错误
    OpenAIError,               # OpenAI相关错误
    RateLimitError,            # 速率限制错误
    ServiceUnavailableError,   # 服务不可用错误
    Timeout,                   # 超时错误
)

# 导入OpenHands核心模块
from openhands.controller.agent import Agent                    # Agent基类
from openhands.controller.replay import ReplayManager          # 重放管理器
from openhands.controller.state.state import State             # 状态管理
from openhands.controller.state.state_tracker import StateTracker  # 状态跟踪器
from openhands.controller.stuck import StuckDetector           # 卡死检测器
from openhands.core.config import AgentConfig, LLMConfig       # 配置类
from openhands.core.exceptions import (                        # 核心异常类
    AgentStuckInLoopError,                                     # Agent陷入循环错误
    FunctionCallNotExistsError,                                # 函数调用不存在错误
    FunctionCallValidationError,                               # 函数调用验证错误
    LLMContextWindowExceedError,                               # LLM上下文窗口超出错误
    LLMMalformedActionError,                                   # LLM格式错误的动作
    LLMNoActionError,                                          # LLM未返回动作错误
    LLMResponseError,                                          # LLM响应错误
)
from openhands.core.logger import LOG_ALL_EVENTS              # 事件日志配置
from openhands.core.logger import openhands_logger as logger  # 日志记录器
from openhands.core.schema import AgentState                  # Agent状态枚举
from openhands.events import (                                # 事件相关模块
    EventSource,                                              # 事件源枚举
    EventStream,                                              # 事件流
    EventStreamSubscriber,                                    # 事件流订阅者
    RecallType,                                               # 回忆类型
)
from openhands.events.action import (                         # 动作事件
    Action,                                                   # 动作基类
    ActionConfirmationStatus,                                 # 动作确认状态
    AgentDelegateAction,                                      # Agent委托动作
    AgentFinishAction,                                        # Agent完成动作
    AgentRejectAction,                                        # Agent拒绝动作
    ChangeAgentStateAction,                                   # 改变Agent状态动作
    CmdRunAction,                                             # 命令执行动作
    IPythonRunCellAction,                                     # IPython单元执行动作
    MessageAction,                                            # 消息动作
    NullAction,                                               # 空动作
    SystemMessageAction,                                      # 系统消息动作
)
from openhands.events.action.agent import (                  # Agent特定动作
    CondensationAction,                                       # 压缩动作
    CondensationRequestAction,                                # 压缩请求动作
    RecallAction,                                             # 回忆动作
)
from openhands.events.event import Event                     # 事件基类
from openhands.events.observation import (                   # 观察事件
    AgentDelegateObservation,                                # Agent委托观察
    AgentStateChangedObservation,                            # Agent状态改变观察
    ErrorObservation,                                        # 错误观察
    NullObservation,                                         # 空观察
    Observation,                                             # 观察基类
)
from openhands.events.serialization.event import truncate_content  # 内容截断工具
from openhands.llm.llm import LLM                           # 大语言模型类
from openhands.llm.metrics import Metrics                  # 指标统计类
from openhands.storage.files import FileStore              # 文件存储类

# 注意：RESUME功能仅在Web GUI中可用
TRAFFIC_CONTROL_REMINDER = (
    "Please click on resume button if you'd like to continue, or start a new task."
)
"""str: 流量控制提醒消息，当需要用户手动恢复时显示"""

ERROR_ACTION_NOT_EXECUTED_ID = 'AGENT_ERROR$ERROR_ACTION_NOT_EXECUTED'
"""str: 动作未执行错误的唯一标识符"""

ERROR_ACTION_NOT_EXECUTED = 'The action has not been executed. This may have occurred because the user pressed the stop button, or because the runtime system crashed and restarted due to resource constraints. Any previously established system state, dependencies, or environment variables may have been lost.'
"""str: 动作未执行时的错误消息，通常发生在用户停止操作或系统崩溃重启时"""


class AgentController:
    """Agent控制器类
    
    负责管理Agent的整个生命周期，包括状态管理、事件处理、动作执行、
    委托管理等核心功能。这是OpenHands系统中最重要的组件之一。
    
    Attributes:
        id (str): 控制器的唯一标识符
        agent (Agent): 被控制的Agent实例
        max_iterations (int): 最大迭代次数
        event_stream (EventStream): 事件流实例
        state (State): 当前状态对象
        confirmation_mode (bool): 是否启用确认模式
        agent_to_llm_config (dict[str, LLMConfig]): Agent名称到LLM配置的映射
        agent_configs (dict[str, AgentConfig]): Agent名称到Agent配置的映射
        parent (AgentController | None): 父控制器引用，用于委托场景
        delegate (AgentController | None): 委托控制器引用
        _pending_action_info (tuple[Action, float] | None): 待执行动作信息(动作, 时间戳)
        _closed (bool): 控制器是否已关闭
        _cached_first_user_message (MessageAction | None): 缓存的第一条用户消息
    """
    
    id: str
    agent: Agent
    max_iterations: int
    event_stream: EventStream
    state: State
    confirmation_mode: bool
    agent_to_llm_config: dict[str, LLMConfig]
    agent_configs: dict[str, AgentConfig]
    parent: 'AgentController | None' = None
    delegate: 'AgentController | None' = None
    _pending_action_info: tuple[Action, float] | None = None  # (action, timestamp)
    _closed: bool = False
    _cached_first_user_message: MessageAction | None = None

    def __init__(
        self,
        agent: Agent,
        event_stream: EventStream,
        iteration_delta: int,
        budget_per_task_delta: float | None = None,
        agent_to_llm_config: dict[str, LLMConfig] | None = None,
        agent_configs: dict[str, AgentConfig] | None = None,
        sid: str | None = None,
        file_store: FileStore | None = None,
        user_id: str | None = None,
        confirmation_mode: bool = False,
        initial_state: State | None = None,
        is_delegate: bool = False,
        headless_mode: bool = True,
        status_callback: Callable | None = None,
        replay_events: list[Event] | None = None,
    ):
        """初始化AgentController实例
        
        Args:
            agent: 要控制的Agent实例
            event_stream: 用于发布事件的事件流
            iteration_delta: Agent可运行的最大迭代次数增量
            budget_per_task_delta: 每个任务允许的最大预算增量(美元)，超过后Agent将停止
            agent_to_llm_config: 当需要委托给其他Agent时，Agent名称到LLM配置的映射字典
            agent_configs: 当需要委托给其他Agent时，Agent名称到Agent配置的映射字典  
            sid: Agent的会话ID
            file_store: 文件存储实例
            user_id: 用户ID
            confirmation_mode: 是否为Agent动作启用确认模式
            initial_state: 控制器的初始状态
            is_delegate: 此控制器是否为委托控制器
            headless_mode: Agent是否在无头模式下运行
            status_callback: 处理状态更新的可选回调函数
            replay_events: 要重放的事件日志列表
        """

        # 设置基本属性
        self.id = sid or event_stream.sid
        self.user_id = user_id
        self.file_store = file_store
        self.agent = agent
        self.headless_mode = headless_mode
        self.is_delegate = is_delegate

        # 事件流必须在可能订阅之前设置
        self.event_stream = event_stream

        # 如果这不是委托控制器，则订阅事件流
        if not self.is_delegate:
            self.event_stream.subscribe(
                EventStreamSubscriber.AGENT_CONTROLLER, self.on_event, self.id
            )

        # 初始化状态跟踪器
        self.state_tracker = StateTracker(sid, file_store, user_id)

        # 设置初始状态：来自前一个会话的状态、来自父Agent的状态或全新状态
        self.set_initial_state(
            state=initial_state,
            max_iterations=iteration_delta,
            max_budget_per_task=budget_per_task_delta,
            confirmation_mode=confirmation_mode,
        )

        # TODO: 在管理器和控制器之间共享状态以保持向后兼容性；
        # 理想情况下，我们应该将所有状态相关逻辑移到状态管理器中
        self.state = self.state_tracker.state

        # 设置配置映射
        self.agent_to_llm_config = agent_to_llm_config if agent_to_llm_config else {}
        self.agent_configs = agent_configs if agent_configs else {}
        self._initial_max_iterations = iteration_delta
        self._initial_max_budget_per_task = budget_per_task_delta

        # 初始化卡死检测器
        self._stuck_detector = StuckDetector(self.state)
        self.status_callback = status_callback

        # 初始化重放管理器
        self._replay_manager = ReplayManager(replay_events)

        # 将系统消息添加到事件流
        self._add_system_message()

    def _add_system_message(self):
        """添加系统消息到事件流
        
        检查事件流中是否已存在系统消息，如果不存在则添加Agent的系统消息。
        对于所有Agent（包括委托Agent）都应该执行此操作。
        """
        # 搜索事件流中的现有事件
        for event in self.event_stream.search_events(start_id=self.state.start_id):
            if isinstance(event, MessageAction) and event.source == EventSource.USER:
                # FIXME: 在2025年6月1日后移除此代码
                # 如果我们首先遇到用户消息，则不要尝试添加系统消息
                # 这意味着事件流在引入SystemMessageAction之前就存在了
                # 我们期望*agent*能够优雅地处理这种情况
                return

            if isinstance(event, SystemMessageAction):
                # 如果系统消息已存在，则不要尝试添加
                return

        # 将系统消息添加到事件流
        # 这应该对所有Agent执行，包括委托Agent
        system_message = self.agent.get_system_message()
        if system_message and system_message.content:
            # 创建消息预览用于日志记录
            preview = (
                system_message.content[:50] + '...'
                if len(system_message.content) > 50
                else system_message.content
            )
            logger.debug(f'System message: {preview}')
            self.event_stream.add_event(system_message, EventSource.AGENT)

    async def close(self, set_stop_state: bool = True) -> None:
        """关闭Agent控制器
        
        取消任何正在进行的任务并取消订阅事件流。
        注意：正确关闭非常重要，否则状态会不完整。
        
        Args:
            set_stop_state: 是否将Agent状态设置为STOPPED
        """
        if set_stop_state:
            await self.set_agent_state_to(AgentState.STOPPED)

        # 关闭状态跟踪器
        self.state_tracker.close(self.event_stream)

        # 取消订阅事件流
        # 只有根父控制器订阅事件流
        if not self.is_delegate:
            self.event_stream.unsubscribe(
                EventStreamSubscriber.AGENT_CONTROLLER, self.id
            )
        self._closed = True

    def log(self, level: str, message: str, extra: dict | None = None) -> None:
        """记录消息到Agent控制器的日志记录器
        
        Args:
            level: 要使用的日志级别（例如'info'、'debug'、'error'）
            message: 要记录的消息
            extra: 要记录的额外字段，默认包含session_id
        """
        message = f'[Agent Controller {self.id}] {message}'
        if extra is None:
            extra = {}
        # 合并额外字段，包含会话ID
        extra_merged = {'session_id': self.id, **extra}
        getattr(logger, level)(message, extra=extra_merged, stacklevel=2)

    async def _react_to_exception(
        self,
        e: Exception,
    ) -> None:
        """对异常做出反应
        
        通过设置Agent状态为错误并发送状态消息来处理异常。
        
        Args:
            e: 要处理的异常
        """
        # 在设置Agent状态之前存储错误原因
        self.state.last_error = f'{type(e).__name__}: {str(e)}'

        if self.status_callback is not None:
            err_id = ''
            # 根据异常类型设置相应的错误ID
            if isinstance(e, AuthenticationError):
                err_id = 'STATUS$ERROR_LLM_AUTHENTICATION'
                self.state.last_error = err_id
            elif isinstance(
                e,
                (
                    ServiceUnavailableError,
                    APIConnectionError,
                    APIError,
                ),
            ):
                err_id = 'STATUS$ERROR_LLM_SERVICE_UNAVAILABLE'
                self.state.last_error = err_id
            elif isinstance(e, InternalServerError):
                err_id = 'STATUS$ERROR_LLM_INTERNAL_SERVER_ERROR'
                self.state.last_error = err_id
            elif isinstance(e, BadRequestError) and 'ExceededBudget' in str(e):
                err_id = 'STATUS$ERROR_LLM_OUT_OF_CREDITS'
                self.state.last_error = err_id
            elif isinstance(e, ContentPolicyViolationError) or (
                isinstance(e, BadRequestError)
                and 'ContentPolicyViolationError' in str(e)
            ):
                err_id = 'STATUS$ERROR_LLM_CONTENT_POLICY_VIOLATION'
                self.state.last_error = err_id
            elif isinstance(e, RateLimitError):
                # 检查是否是最后一次重试尝试
                if (
                    hasattr(e, 'retry_attempt')
                    and hasattr(e, 'max_retries')
                    and e.retry_attempt >= e.max_retries
                ):
                    # 所有重试都已用尽，设置为ERROR状态并显示特殊消息
                    self.state.last_error = (
                        'CHAT_INTERFACE$AGENT_RATE_LIMITED_STOPPED_MESSAGE'
                    )
                    await self.set_agent_state_to(AgentState.ERROR)
                else:
                    # 仍在重试，设置为RATE_LIMITED状态
                    await self.set_agent_state_to(AgentState.RATE_LIMITED)
                return
            # 调用状态回调函数
            self.status_callback('error', err_id, self.state.last_error)

        # 在存储原因后将Agent状态设置为ERROR
        await self.set_agent_state_to(AgentState.ERROR)

    def step(self) -> None:
        """执行一个步骤
        
        创建异步任务来执行步骤，并处理可能出现的异常。
        """
        asyncio.create_task(self._step_with_exception_handling())

    async def _step_with_exception_handling(self) -> None:
        """带异常处理的步骤执行
        
        包装实际的步骤执行逻辑，捕获并处理可能出现的各种异常。
        """
        try:
            await self._step()
        except Exception as e:
            # 记录错误日志
            self.log(
                'error',
                f'Error while running the agent (session ID: {self.id}): {e}. '
                f'Traceback: {traceback.format_exc()}',
            )
            # 创建要报告的错误
            reported = RuntimeError(
                f'There was an unexpected error while running the agent: {e.__class__.__name__}. You can refresh the page or ask the agent to try again.'
            )
            # 对于特定类型的异常，直接报告原始异常
            if (
                isinstance(e, Timeout)
                or isinstance(e, APIError)
                or isinstance(e, BadRequestError)
                or isinstance(e, NotFoundError)
                or isinstance(e, InternalServerError)
                or isinstance(e, AuthenticationError)
                or isinstance(e, RateLimitError)
                or isinstance(e, ContentPolicyViolationError)
                or isinstance(e, LLMContextWindowExceedError)
            ):
                reported = e
            else:
                self.log(
                    'warning',
                    f'Unknown exception type while running the agent: {type(e).__name__}.',
                )
            await self._react_to_exception(reported)

    def should_step(self, event: Event) -> bool:
        """判断Agent是否应该基于事件执行步骤
        
        一般来说，如果Agent收到用户消息或在环境中观察到某些内容（在行动后），
        Agent就应该执行步骤。
        
        Args:
            event: 要评估的事件
            
        Returns:
            bool: 如果Agent应该执行步骤则返回True，否则返回False
        """
        # 可能是委托Agent的执行时机
        if self.delegate is not None:
            return False

        if isinstance(event, Action):
            # 用户消息总是触发步骤
            if isinstance(event, MessageAction) and event.source == EventSource.USER:
                return True
            # 非等待用户输入状态下的消息动作
            if (
                isinstance(event, MessageAction)
                and self.get_agent_state() != AgentState.AWAITING_USER_INPUT
            ):
                # TODO: 这很脆弱，但还有其他检查方式吗？
                return True
            # Agent委托动作
            if isinstance(event, AgentDelegateAction):
                return True
            # 压缩相关动作
            if isinstance(event, CondensationAction):
                return True
            if isinstance(event, CondensationRequestAction):
                return True
            return False
        
        if isinstance(event, Observation):
            # 具有cause > 0的NullObservation（RecallAction），不是0（用户消息）
            if (
                isinstance(event, NullObservation)
                and event.cause is not None
                and event.cause > 0
            ):
                return True
            # Agent状态改变观察或空观察不触发步骤
            if isinstance(event, AgentStateChangedObservation) or isinstance(
                event, NullObservation
            ):
                return False
            return True
        return False

    def on_event(self, event: Event) -> None:
        """来自事件流的回调
        
        通知控制器有传入事件。处理委托转发和事件路由逻辑。
        
        Args:
            event: 要处理的传入事件
        """
        # 如果有未完成或未出错的委托，将事件转发给它
        if self.delegate is not None:
            delegate_state = self.delegate.get_agent_state()
            if (
                delegate_state
                not in (
                    AgentState.FINISHED,
                    AgentState.ERROR,
                    AgentState.REJECTED,
                )
                or 'RuntimeError: Agent reached maximum iteration.'
                in self.delegate.state.last_error
                or 'RuntimeError:Agent reached maximum budget for conversation'
                in self.delegate.state.last_error
            ):
                # 将事件转发给委托并跳过父处理
                asyncio.get_event_loop().run_until_complete(
                    self.delegate._on_event(event)
                )
                return
            else:
                # 委托已完成或出错，结束它
                self.end_delegate()
                return

        # 只有在没有活动委托时才继续父处理
        asyncio.get_event_loop().run_until_complete(self._on_event(event))

    async def _on_event(self, event: Event) -> None:
        """内部事件处理方法
        
        处理传入事件的核心逻辑，包括历史记录更新和事件分发。
        
        Args:
            event: 要处理的事件
        """
        # 跳过隐藏事件
        if hasattr(event, 'hidden') and event.hidden:
            return

        # 将事件添加到历史记录
        self.state_tracker.add_history(event)

        # 根据事件类型进行处理
        if isinstance(event, Action):
            await self._handle_action(event)
        elif isinstance(event, Observation):
            await self._handle_observation(event)

        # 判断是否应该执行步骤
        should_step = self.should_step(event)
        if should_step:
            self.log(
                'debug',
                f'Stepping agent after event: {type(event).__name__}',
                extra={'msg_type': 'STEPPING_AGENT'},
            )
            await self._step_with_exception_handling()
        elif isinstance(event, MessageAction) and event.source == EventSource.USER:
            # 如果收到用户消息但没有执行步骤，记录原因
            self.log(
                'warning',
                f'Not stepping agent after user message. Current state: {self.get_agent_state()}',
                extra={'msg_type': 'NOT_STEPPING_AFTER_USER_MESSAGE'},
            )

    async def _handle_action(self, action: Action) -> None:
        """处理来自Agent或委托的动作
        
        Args:
            action: 要处理的动作
        """
        if isinstance(action, ChangeAgentStateAction):
            # 改变Agent状态动作
            await self.set_agent_state_to(action.agent_state)  # type: ignore
        elif isinstance(action, MessageAction):
            # 消息动作
            await self._handle_message_action(action)
        elif isinstance(action, AgentDelegateAction):
            # Agent委托动作
            await self.start_delegate(action)
            assert self.delegate is not None
            # 为委托发布带有任务的MessageAction
            if 'task' in action.inputs:
                self.event_stream.add_event(
                    MessageAction(content='TASK: ' + action.inputs['task']),
                    EventSource.USER,
                )
                await self.delegate.set_agent_state_to(AgentState.RUNNING)
            return
        elif isinstance(action, AgentFinishAction):
            # Agent完成动作
            self.state.outputs = action.outputs
            await self.set_agent_state_to(AgentState.FINISHED)
        elif isinstance(action, AgentRejectAction):
            # Agent拒绝动作
            self.state.outputs = action.outputs
            await self.set_agent_state_to(AgentState.REJECTED)

    async def _handle_observation(self, observation: Observation) -> None:
        """处理来自事件流的观察
        
        Args:
            observation: 要处理的观察
        """
        # 创建观察的深拷贝用于打印
        observation_to_print = copy.deepcopy(observation)
        # 如果内容过长则截断
        if len(observation_to_print.content) > self.agent.llm.config.max_message_chars:
            observation_to_print.content = truncate_content(
                observation_to_print.content, self.agent.llm.config.max_message_chars
            )
        # 根据LOG_ALL_EVENTS设置使用info还是debug级别
        log_level = 'info' if os.getenv('LOG_ALL_EVENTS') in ('true', '1') else 'debug'
        self.log(
            log_level, str(observation_to_print), extra={'msg_type': 'OBSERVATION'}
        )

        # TODO: 这些指标来自草稿编辑器，它们被累积到控制器的状态指标和Agent的llm指标中
        # 将来，我们应该有一个更有原则的方式在给定对话的所有LLM实例之间共享指标
        if observation.llm_metrics is not None:
            self.state_tracker.merge_metrics(observation.llm_metrics)

        # 这发生在可运行动作和microagent动作中
        if self._pending_action and self._pending_action.id == observation.cause:
            if self.state.agent_state == AgentState.AWAITING_USER_CONFIRMATION:
                return

            self._pending_action = None

            # 根据用户确认状态更新Agent状态
            if self.state.agent_state == AgentState.USER_CONFIRMED:
                await self.set_agent_state_to(AgentState.RUNNING)
            if self.state.agent_state == AgentState.USER_REJECTED:
                await self.set_agent_state_to(AgentState.AWAITING_USER_INPUT)
            return

    async def _handle_message_action(self, action: MessageAction) -> None:
        """处理来自事件流的消息动作
        
        Args:
            action: 要处理的消息动作
        """
        if action.source == EventSource.USER:
            # 根据LOG_ALL_EVENTS设置使用info还是debug级别
            log_level = (
                'info' if os.getenv('LOG_ALL_EVENTS') in ('true', '1') else 'debug'
            )
            self.log(
                log_level,
                str(action),
                extra={'msg_type': 'ACTION', 'event_source': EventSource.USER},
            )

            # 如果这是此Agent的第一条用户消息，对microagent信息类型很重要
            first_user_message = self._first_user_message()
            is_first_user_message = (
                action.id == first_user_message.id if first_user_message else False
            )
            # 根据是否为第一条消息确定回忆类型
            recall_type = (
                RecallType.WORKSPACE_CONTEXT
                if is_first_user_message
                else RecallType.KNOWLEDGE
            )

            # 创建回忆动作
            recall_action = RecallAction(query=action.content, recall_type=recall_type)
            self._pending_action = recall_action
            # 这是source=USER，因为用户消息是microagent检索的触发器
            self.event_stream.add_event(recall_action, EventSource.USER)

            # 如果Agent不在运行状态，设置为运行状态
            if self.get_agent_state() != AgentState.RUNNING:
                await self.set_agent_state_to(AgentState.RUNNING)

        elif action.source == EventSource.AGENT:
            # 如果Agent正在等待响应，设置适当的状态
            if action.wait_for_response:
                await self.set_agent_state_to(AgentState.AWAITING_USER_INPUT)

    def _reset(self) -> None:
        """重置Agent控制器
        
        清理待执行动作，为未完成的可运行动作创建错误观察，
        并重置Agent状态。
        """
        # 可运行动作需要一个观察
        # 确保有一个带有工具调用Metadata的观察被Agent识别
        # 否则在历史中找到待执行动作，但没有带有工具结果的观察就不完整
        if self._pending_action and hasattr(self._pending_action, 'tool_call_metadata'):
            # 查找是否已经有具有相同工具调用Metadata的观察
            found_observation = False
            for event in self.state.history:
                if (
                    isinstance(event, Observation)
                    and event.tool_call_metadata
                    == self._pending_action.tool_call_metadata
                ):
                    found_observation = True
                    break

            # 如果没有找到，创建一个带有工具调用Metadata的新ErrorObservation
            if not found_observation:
                obs = ErrorObservation(
                    content=ERROR_ACTION_NOT_EXECUTED,
                    error_id=ERROR_ACTION_NOT_EXECUTED_ID,
                )
                obs.tool_call_metadata = self._pending_action.tool_call_metadata
                obs._cause = self._pending_action.id  # type: ignore[attr-defined]
                self.event_stream.add_event(obs, EventSource.AGENT)

        # 注意：RecallActions在重置时不需要ErrorObservation，只要它们没有工具调用

        # 重置待执行动作，这将在Agent为STOPPED或ERROR时被调用
        self._pending_action = None
        self.agent.reset()

    async def set_agent_state_to(self, new_state: AgentState) -> None:
        """更新Agent状态并处理副作用
        
        可以向事件流发出事件。处理状态转换逻辑和相关的清理工作。
        
        Args:
            new_state: 要为Agent设置的新状态
        """
        self.log(
            'info',
            f'Setting agent({self.agent.name}) state from {self.state.agent_state} to {new_state}',
        )

        # 如果状态没有变化，直接返回
        if new_state == self.state.agent_state:
            return

        # 在停止或错误状态时重置
        if new_state in (AgentState.STOPPED, AgentState.ERROR):
            self._reset()

        # 用户允许检查控制限制并在适用时扩展它们
        if (
            self.state.agent_state == AgentState.ERROR
            and new_state == AgentState.RUNNING
        ):
            self.state_tracker.maybe_increase_control_flags_limits(self.headless_mode)

        # 处理用户确认或拒绝的待执行动作
        if self._pending_action is not None and (
            new_state in (AgentState.USER_CONFIRMED, AgentState.USER_REJECTED)
        ):
            # 清空thought字段
            if hasattr(self._pending_action, 'thought'):
                self._pending_action.thought = ''  # type: ignore[union-attr]
            # 设置确认状态
            if new_state == AgentState.USER_CONFIRMED:
                confirmation_state = ActionConfirmationStatus.CONFIRMED
            else:
                confirmation_state = ActionConfirmationStatus.REJECTED
            self._pending_action.confirmation_state = confirmation_state  # type: ignore[attr-defined]
            self._pending_action._id = None  # type: ignore[attr-defined]
            self.event_stream.add_event(self._pending_action, EventSource.AGENT)

        # 更新状态
        self.state.agent_state = new_state

        # 如果是错误状态，创建带有原因字段的观察
        reason = ''
        if new_state == AgentState.ERROR:
            reason = self.state.last_error

        # 发出状态改变事件
        self.event_stream.add_event(
            AgentStateChangedObservation('', self.state.agent_state, reason),
            EventSource.ENVIRONMENT,
        )

        # 每当Agent状态改变时保存状态，确保在崩溃或意外情况下不会丢失状态
        self.save_state()

    def get_agent_state(self) -> AgentState:
        """返回Agent的当前状态
        
        Returns:
            AgentState: Agent的当前状态
        """
        return self.state.agent_state

    async def start_delegate(self, action: AgentDelegateAction) -> None:
        """启动委托Agent来处理子任务
        
        OpenHands是一个多Agent系统。一个`task`是OpenHands（整个系统）
        和用户之间的对话，可能涉及用户的一个或多个输入。它以用户的初始输入
        （通常是任务陈述）开始，以Agent发起的`AgentFinishAction`、
        用户发起的停止或错误结束。
        
        一个`subtask`是Agent和用户或另一个Agent之间的对话。如果一个`task`
        由单个Agent执行，那么它也是一个`subtask`。否则，一个`task`由
        多个`subtasks`组成，每个由一个Agent执行。
        
        Args:
            action: 包含要启动的委托Agent信息的动作
        """
        # 获取Agent类和配置
        agent_cls: type[Agent] = Agent.get_cls(action.agent)
        agent_config = self.agent_configs.get(action.agent, self.agent.config)
        llm_config = self.agent_to_llm_config.get(action.agent, self.agent.llm.config)
        
        # 确保指标在父子之间共享以进行全局累积
        llm = LLM(
            config=llm_config,
            retry_listener=self.agent.llm.retry_listener,
            metrics=self.state.metrics,
        )
        delegate_agent = agent_cls(llm=llm, config=agent_config)

        # 在启动委托之前拍摄当前指标的快照
        state = State(
            session_id=self.id.removesuffix('-delegate'),
            inputs=action.inputs or {},
            iteration_flag=self.state.iteration_flag,
            budget_flag=self.state.budget_flag,
            delegate_level=self.state.delegate_level + 1,
            # 全局指标应在父子之间共享
            metrics=self.state.metrics,
            # 从流的顶部开始
            start_id=self.event_stream.get_latest_event_id() + 1,
            parent_metrics_snapshot=self.state_tracker.get_metrics_snapshot(),
            parent_iteration=self.state.iteration_flag.current_value,
        )
        self.log(
            'debug',
            f'start delegate, creating agent {delegate_agent.name} using LLM {llm}',
        )

        # 创建带有is_delegate=True的委托，这样它就不会直接订阅
        self.delegate = AgentController(
            sid=self.id + '-delegate',
            file_store=self.file_store,
            user_id=self.user_id,
            agent=delegate_agent,
            event_stream=self.event_stream,
            iteration_delta=self._initial_max_iterations,
            budget_per_task_delta=self._initial_max_budget_per_task,
            agent_to_llm_config=self.agent_to_llm_config,
            agent_configs=self.agent_configs,
            initial_state=state,
            is_delegate=True,
            headless_mode=self.headless_mode,
        )

    def end_delegate(self) -> None:
        """结束当前活动的委托
        
        例如，如果委托已完成或出错，结束委托以便此控制器可以恢复正常操作。
        """
        if self.delegate is None:
            return

        delegate_state = self.delegate.get_agent_state()

        # 更新在Agent之间共享的迭代计数
        self.state.iteration_flag.current_value = (
            self.delegate.state.iteration_flag.current_value
        )

        # 在关闭委托之前计算委托特定的指标
        delegate_metrics = self.state.get_local_metrics()
        logger.info(f'Local metrics for delegate: {delegate_metrics}')

        # 在添加新事件之前关闭委托控制器
        asyncio.get_event_loop().run_until_complete(self.delegate.close())

        if delegate_state in (AgentState.FINISHED, AgentState.REJECTED):
            # 检索委托结果
            delegate_outputs = (
                self.delegate.state.outputs if self.delegate.state else {}
            )

            # 准备委托结果观察
            # TODO: 用AI生成的摘要替换此内容 (#2395)
            # 从格式化输出中过滤指标以避免混乱
            display_outputs = {
                k: v for k, v in delegate_outputs.items() if k != 'metrics'
            }
            formatted_output = ', '.join(
                f'{key}: {value}' for key, value in display_outputs.items()
            )
            content = (
                f'{self.delegate.agent.name} finishes task with {formatted_output}'
            )
        else:
            # 委托状态是ERROR
            # 发出带有错误内容的AgentDelegateObservation
            delegate_outputs = (
                self.delegate.state.outputs if self.delegate.state else {}
            )
            content = (
                f'{self.delegate.agent.name} encountered an error during execution.'
            )

        content = f'Delegated agent finished with result:\n\n{content}'

        # 发出委托结果观察
        obs = AgentDelegateObservation(outputs=delegate_outputs, content=content)

        # 将委托动作与发起的工具调用关联
        for event in reversed(self.state.history):
            if isinstance(event, AgentDelegateAction):
                delegate_action = event
                obs.tool_call_metadata = delegate_action.tool_call_metadata
                break

        self.event_stream.add_event(obs, EventSource.AGENT)

        # 取消设置委托，这样父控制器可以恢复正常处理
        self.delegate = None

    async def _step(self) -> None:
        """执行父或委托Agent的单个步骤
        
        检测卡死的Agent以及迭代次数和任务预算的限制。
        处理重放模式和正常Agent步骤执行。
        """
        # 检查Agent状态是否允许执行步骤
        if self.get_agent_state() != AgentState.RUNNING:
            self.log(
                'debug',
                f'Agent not stepping because state is {self.get_agent_state()} (not RUNNING)',
                extra={'msg_type': 'STEP_BLOCKED_STATE'},
            )
            return

        # 检查是否有待执行动作
        if self._pending_action:
            action_id = getattr(self._pending_action, 'id', 'unknown')
            action_type = type(self._pending_action).__name__
            self.log(
                'debug',
                f'Agent not stepping because of pending action: {action_type} (id={action_id})',
                extra={'msg_type': 'STEP_BLOCKED_PENDING_ACTION'},
            )
            return

        # 记录步骤信息
        self.log(
            'debug',
            f'LEVEL {self.state.delegate_level} LOCAL STEP {self.state.get_local_step()} GLOBAL STEP {self.state.iteration_flag.current_value}',
            extra={'msg_type': 'STEP'},
        )

        # 确保预算控制标志与最新指标同步
        # 将来，我们应该集中使用每次对话一个LLM对象
        # 这将帮助我们统一自动生成标题、运行压缩器等的成本
        # 在许多微服务接触同一个llm成本字段之前，我们应该与控制器的预算标志同步
        # 并在执行Agent步骤之前检查我们没有超出预算
        self.state_tracker.sync_budget_flag_with_metrics()

        # 检查Agent是否卡死
        if self._is_stuck():
            await self._react_to_exception(
                AgentStuckInLoopError('Agent got stuck in a loop')
            )
            return

        # 运行控制标志检查
        try:
            self.state_tracker.run_control_flags()
        except Exception as e:
            logger.warning('Control flag limits hit')
            await self._react_to_exception(e)
            return

        action: Action = NullAction()

        # 处理重放模式或正常Agent步骤
        if self._replay_manager.should_replay():
            # 在重放模式下，我们不让Agent继续
            # 相反，我们从重放轨迹重放动作
            action = self._replay_manager.step()
        else:
            try:
                # 让Agent执行步骤
                action = self.agent.step(self.state)
                if action is None:
                    raise LLMNoActionError('No action was returned')
                action._source = EventSource.AGENT  # type: ignore [attr-defined]
            except (
                LLMMalformedActionError,
                LLMNoActionError,
                LLMResponseError,
                FunctionCallValidationError,
                FunctionCallNotExistsError,
            ) as e:
                # 对于这些异常，添加错误观察并返回
                self.event_stream.add_event(
                    ErrorObservation(
                        content=str(e),
                    ),
                    EventSource.AGENT,
                )
                return
            except (ContextWindowExceededError, BadRequestError, OpenAIError) as e:
                # FIXME: 这是一个临时解决方案，直到确认litellm修复
                # 检查这是否是嵌套的上下文窗口错误
                # 我们必须依赖字符串匹配，因为LiteLLM不一致地
                # 将失败包装在ContextWindowExceededError中
                error_str = str(e).lower()
                if (
                    'contextwindowexceedederror' in error_str
                    or 'prompt is too long' in error_str
                    or 'input length and `max_tokens` exceed context limit' in error_str
                    or 'please reduce the length of either one'
                    in error_str  # 对于OpenRouter上下文窗口错误
                    or (
                        'sambanovaexception' in error_str
                        and 'maximum context length' in error_str
                    )
                    # 对于SambaNova上下文窗口错误 - 只有当两个模式都存在时才匹配
                    or isinstance(e, ContextWindowExceededError)
                ):
                    # 检查是否启用了历史截断
                    if self.agent.config.enable_history_truncation:
                        self.event_stream.add_event(
                            CondensationRequestAction(), EventSource.AGENT
                        )
                        return
                    else:
                        raise LLMContextWindowExceedError()
                else:
                    raise e

        # 处理可运行动作的确认模式
        if action.runnable:
            if self.state.confirmation_mode and (
                type(action) is CmdRunAction or type(action) is IPythonRunCellAction
            ):
                action.confirmation_state = (
                    ActionConfirmationStatus.AWAITING_CONFIRMATION
                )
            self._pending_action = action

        # 发布动作事件（非空动作）
        if not isinstance(action, NullAction):
            # 处理需要用户确认的动作
            if (
                hasattr(action, 'confirmation_state')
                and action.confirmation_state
                == ActionConfirmationStatus.AWAITING_CONFIRMATION
            ):
                await self.set_agent_state_to(AgentState.AWAITING_USER_CONFIRMATION)

            # 为前端显示创建和记录指标
            self._prepare_metrics_for_frontend(action)

            self.event_stream.add_event(action, action._source)  # type: ignore [attr-defined]

        # 记录动作
        log_level = 'info' if LOG_ALL_EVENTS else 'debug'
        self.log(log_level, str(action), extra={'msg_type': 'ACTION'})

    @property
    def _pending_action(self) -> Action | None:
        """获取带有时间跟踪的当前待执行动作
        
        Returns:
            Action | None: 当前待执行动作，如果没有则返回None
        """
        if self._pending_action_info is None:
            return None

        action, timestamp = self._pending_action_info
        current_time = time.time()
        elapsed_time = current_time - timestamp

        # 如果待执行动作已经活动很长时间则记录日志（但不清除它）
        if elapsed_time > 60.0:  # 1分钟 - 仅用于日志记录目的
            action_id = getattr(action, 'id', 'unknown')
            action_type = type(action).__name__
            self.log(
                'info',
                f'Pending action active for {elapsed_time:.2f}s: {action_type} (id={action_id})',
                extra={'msg_type': 'PENDING_ACTION_TIMEOUT'},
            )

        return action

    @_pending_action.setter
    def _pending_action(self, action: Action | None) -> None:
        """设置或清除带有时间戳和日志记录的待执行动作
        
        Args:
            action: 要设置为待执行的动作，或None表示清除
        """
        if action is None:
            if self._pending_action_info is not None:
                prev_action, timestamp = self._pending_action_info
                action_id = getattr(prev_action, 'id', 'unknown')
                action_type = type(prev_action).__name__
                elapsed_time = time.time() - timestamp
                self.log(
                    'debug',
                    f'Cleared pending action after {elapsed_time:.2f}s: {action_type} (id={action_id})',
                    extra={'msg_type': 'PENDING_ACTION_CLEARED'},
                )
            self._pending_action_info = None
        else:
            action_id = getattr(action, 'id', 'unknown')
            action_type = type(action).__name__
            self.log(
                'debug',
                f'Set pending action: {action_type} (id={action_id})',
                extra={'msg_type': 'PENDING_ACTION_SET'},
            )
            self._pending_action_info = (action, time.time())

    def get_state(self) -> State:
        """返回当前运行的状态对象
        
        Returns:
            State: 当前状态对象
        """
        return self.state

    def set_initial_state(
        self,
        state: State | None,
        max_iterations: int,
        max_budget_per_task: float | None,
        confirmation_mode: bool = False,
    ):
        """设置初始状态
        
        Args:
            state: 初始状态对象，如果为None则创建新状态
            max_iterations: 最大迭代次数
            max_budget_per_task: 每个任务的最大预算
            confirmation_mode: 是否启用确认模式
        """
        self.state_tracker.set_initial_state(
            self.id,
            self.agent,
            state,
            max_iterations,
            max_budget_per_task,
            confirmation_mode,
        )
        # 始终从事件流加载以避免丢失历史记录
        self.state_tracker._init_history(
            self.event_stream,
        )

    def get_trajectory(self, include_screenshots: bool = False) -> list[dict]:
        """获取轨迹数据
        
        Args:
            include_screenshots: 是否包含屏幕截图
            
        Returns:
            list[dict]: 轨迹数据列表
            
        Note:
            在控制器关闭之前，状态历史可能被部分隐藏/截断
        """
        # 状态历史可能在控制器关闭之前被部分隐藏/截断
        assert self._closed
        return self.state_tracker.get_trajectory(include_screenshots)

    def _is_stuck(self) -> bool:
        """检查Agent或其委托是否卡在循环中
        
        Returns:
            bool: 如果Agent卡住则返回True，否则返回False
        """
        # 检查委托是否卡住
        if self.delegate and self.delegate._is_stuck():
            return True

        return self._stuck_detector.is_stuck(self.headless_mode)

    def _prepare_metrics_for_frontend(self, action: Action) -> None:
        """为前端显示创建最小指标对象并记录
        
        为了避免长对话的性能问题，我们只保留：
        - accumulated_cost: 当前总成本
        - accumulated_token_usage: 所有API调用的累积token统计
        - max_budget_per_task: 任务允许的最大预算
        
        这包括来自Agent的LLM和Condenser的LLM（如果存在）的指标。
        
        Args:
            action: 要附加指标的动作
        """
        # 从Agent LLM获取指标
        agent_metrics = self.state.metrics

        # 如果存在，从Condenser LLM获取指标
        condenser_metrics: Metrics | None = None
        if hasattr(self.agent, 'condenser') and hasattr(self.agent.condenser, 'llm'):
            condenser_metrics = self.agent.condenser.llm.metrics

        # 创建一个新的最小指标对象，只包含前端需要的内容
        metrics = Metrics(model_name=agent_metrics.model_name)

        # 设置累积成本（Agent和Condenser成本的总和）
        metrics.accumulated_cost = agent_metrics.accumulated_cost
        if condenser_metrics:
            metrics.accumulated_cost += condenser_metrics.accumulated_cost

        # 将max_budget_per_task添加到指标中
        if self.state.budget_flag:
            metrics.max_budget_per_task = self.state.budget_flag.max_value

        # 设置累积token使用量（Agent和Condenser token使用量的总和）
        # 使用深拷贝确保我们不修改原始对象
        metrics._accumulated_token_usage = (
            agent_metrics.accumulated_token_usage.model_copy(deep=True)
        )
        if condenser_metrics:
            metrics._accumulated_token_usage = (
                metrics._accumulated_token_usage
                + condenser_metrics.accumulated_token_usage
            )

        action.llm_metrics = metrics

        # 记录指标信息用于调试
        # 直接从Agent的指标获取最新使用量
        latest_usage = None
        if self.state.metrics.token_usages:
            latest_usage = self.state.metrics.token_usages[-1]

        accumulated_usage = self.state.metrics.accumulated_token_usage
        self.log(
            'debug',
            f'Action metrics - accumulated_cost: {metrics.accumulated_cost}, max_budget: {metrics.max_budget_per_task}, '
            f'latest tokens (prompt/completion/cache_read/cache_write): '
            f'{latest_usage.prompt_tokens if latest_usage else 0}/'
            f'{latest_usage.completion_tokens if latest_usage else 0}/'
            f'{latest_usage.cache_read_tokens if latest_usage else 0}/'
            f'{latest_usage.cache_write_tokens if latest_usage else 0}, '
            f'accumulated tokens (prompt/completion): '
            f'{accumulated_usage.prompt_tokens}/'
            f'{accumulated_usage.completion_tokens}',
            extra={'msg_type': 'METRICS'},
        )

    def __repr__(self) -> str:
        """返回AgentController的字符串表示
        
        Returns:
            str: 包含关键属性的格式化字符串
        """
        pending_action_info = '<none>'
        if (
            hasattr(self, '_pending_action_info')
            and self._pending_action_info is not None
        ):
            action, timestamp = self._pending_action_info
            action_id = getattr(action, 'id', 'unknown')
            action_type = type(action).__name__
            elapsed_time = time.time() - timestamp
            pending_action_info = (
                f'{action_type}(id={action_id}, elapsed={elapsed_time:.2f}s)'
            )

        return (
            f'AgentController(id={getattr(self, "id", "<uninitialized>")}, '
            f'agent={getattr(self, "agent", "<uninitialized>")!r}, '
            f'event_stream={getattr(self, "event_stream", "<uninitialized>")!r}, '
            f'state={getattr(self, "state", "<uninitialized>")!r}, '
            f'delegate={getattr(self, "delegate", "<uninitialized>")!r}, '
            f'_pending_action={pending_action_info})'
        )

    def _is_awaiting_observation(self) -> bool:
        """检查是否正在等待观察
        
        Returns:
            bool: 如果正在等待观察则返回True
        """
        events = self.event_stream.search_events(reverse=True)
        for event in events:
            if isinstance(event, AgentStateChangedObservation):
                result = event.agent_state == AgentState.RUNNING
                return result
        return False

    def _first_user_message(
        self, events: list[Event] | None = None
    ) -> MessageAction | None:
        """获取此Agent的第一条用户消息
        
        对于常规Agent，这是从开始（start_id=0）的第一条用户消息。
        对于委托Agent，这是委托的start_id之后的第一条用户消息。
        
        Args:
            events: 要搜索的可选事件列表。如果为None，则使用事件流
            
        Returns:
            MessageAction | None: 第一条用户消息，如果没有找到用户消息则返回None
        """
        # 如果提供了事件列表，则搜索它
        if events is not None:
            return next(
                (
                    e
                    for e in events
                    if isinstance(e, MessageAction) and e.source == EventSource.USER
                ),
                None,
            )

        # 否则，使用带有缓存的原始事件流逻辑
        # 如果有缓存消息则返回
        if self._cached_first_user_message is not None:
            return self._cached_first_user_message

        # 查找第一条用户消息
        self._cached_first_user_message = next(
            (
                e
                for e in self.event_stream.search_events(
                    start_id=self.state.start_id,
                )
                if isinstance(e, MessageAction) and e.source == EventSource.USER
            ),
            None,
        )
        return self._cached_first_user_message

    def save_state(self):
        """保存当前状态
        
        将当前状态保存到存储中，以确保在系统崩溃或重启时不会丢失状态信息。
        """
        self.state_tracker.save_state()
