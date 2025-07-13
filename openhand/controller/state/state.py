from __future__ import annotations

import base64
import os
import pickle
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import openhands
from openhands.controller.state.control_flags import (
    BudgetControlFlag,
    IterationControlFlag,
)
from openhands.core.logger import openhands_logger as logger
from openhands.core.schema import AgentState
from openhands.events.action import (
    MessageAction,
)
from openhands.events.action.agent import AgentFinishAction
from openhands.events.event import Event, EventSource
from openhands.llm.metrics import Metrics
from openhands.memory.view import View
from openhands.storage.files import FileStore
from openhands.storage.locations import get_conversation_agent_state_filename

# 定义可恢复的Agent状态列表
# 这些状态下的Agent可以被暂停并在稍后恢复执行
RESUMABLE_STATES = [
    AgentState.RUNNING,           # 运行中
    AgentState.PAUSED,            # 已暂停
    AgentState.AWAITING_USER_INPUT,  # 等待用户输入
    AgentState.FINISHED,          # 已完成
]


# 注意：此枚举已弃用
class TrafficControlState(str, Enum):
    """流量控制状态枚举（已弃用）。
    
    用于表示Agent在流量控制机制下的不同状态。
    此枚举已被标记为弃用，未来版本中可能会被移除。
    """
    
    # 默认状态，无流量限制
    NORMAL = 'normal'

    # 由于流量控制而暂停任务
    THROTTLING = 'throttling'

    # 流量控制暂时暂停
    PAUSED = 'paused'


@dataclass
class State:
    """表示OpenHands系统中Agent的运行状态，保存其操作和内存数据。
    
    此类负责管理Agent的完整状态信息，包括：
    
    多Agent/委托状态：
    - 存储任务（Agent与用户之间的对话）
    - 子任务（Agent与用户或其他Agent之间的对话）
    - 全局和本地迭代计数
    - 多Agent交互的委托级别
    - 几乎卡住的状态
    
    Agent运行状态：
    - 当前Agent状态（如LOADING、RUNNING、PAUSED）
    - 用于速率限制的流量控制状态
    - 确认模式
    - 遇到的最后一个错误
    
    保存和恢复Agent的数据：
    - 保存到session并从session恢复
    - 使用pickle和base64进行序列化
    
    保存/恢复消息历史数据：
    - Agent历史中事件的开始和结束ID
    - 摘要和委托摘要
    
    Metrics指标：
    - 当前任务的全局指标
    - 当前子任务的本地指标
    
    额外数据：
    - 特定于任务的其他数据
    
    Attributes:
        session_id (str): 会话ID
        iteration_flag (IterationControlFlag): 迭代控制标志
        budget_flag (BudgetControlFlag | None): 预算控制标志，可为None
        confirmation_mode (bool): 是否启用确认模式
        history (list[Event]): 事件历史记录列表
        inputs (dict): 输入数据字典
        outputs (dict): 输出数据字典
        agent_state (AgentState): 当前Agent状态
        resume_state (AgentState | None): 恢复状态，可为None
        metrics (Metrics): 当前任务的全局指标
        delegate_level (int): 委托级别，根Agent为0，每次委托增加1
        start_id (int): 历史中事件的起始ID
        end_id (int): 历史中事件的结束ID
        parent_metrics_snapshot (Metrics | None): 父级指标快照
        parent_iteration (int): 父级迭代次数
        extra_data (dict[str, Any]): 存储额外数据的字典
        last_error (str): 最后遇到的错误信息
    """

    session_id: str = ''  # 会话标识符
    iteration_flag: IterationControlFlag = field(
        default_factory=lambda: IterationControlFlag(
            limit_increase_amount=100, current_value=0, max_value=100
        )
    )  # 迭代控制标志，默认最大100次迭代
    budget_flag: BudgetControlFlag | None = None  # 预算控制标志，可选
    confirmation_mode: bool = False  # 确认模式标志
    history: list[Event] = field(default_factory=list)  # 事件历史记录
    inputs: dict = field(default_factory=dict)  # 输入数据
    outputs: dict = field(default_factory=dict)  # 输出数据
    agent_state: AgentState = AgentState.LOADING  # 当前Agent状态
    resume_state: AgentState | None = None  # 恢复时的状态
    # 当前任务的全局指标
    metrics: Metrics = field(default_factory=Metrics)
    # 根Agent级别为0，每次委托增加1
    delegate_level: int = 0
    # start_id和end_id跟踪历史中事件的范围
    start_id: int = -1  # 历史事件起始ID
    end_id: int = -1    # 历史事件结束ID

    parent_metrics_snapshot: Metrics | None = None  # 父级指标快照
    parent_iteration: int = 100  # 父级迭代次数

    # 注意：Controller使用此字段在委托前跟踪父级指标快照
    # 评估任务存储跟踪任务进度/状态所需的额外数据
    extra_data: dict[str, Any] = field(default_factory=dict)
    last_error: str = ''  # 最后的错误信息

    # 注意：以下为弃用参数，暂时保留以保持向后兼容性
    # 将在30天后移除
    iteration: int | None = None  # 弃用：迭代次数
    local_iteration: int | None = None  # 弃用：本地迭代次数
    max_iterations: int | None = None  # 弃用：最大迭代次数
    traffic_control_state: TrafficControlState | None = None  # 弃用：流量控制状态
    local_metrics: Metrics | None = None  # 弃用：本地指标
    delegates: dict[tuple[int, int], tuple[str, str]] | None = None  # 弃用：委托信息

    def save_to_session(
        self, sid: str, file_store: FileStore, user_id: str | None
    ) -> None:
        """将状态保存到会话中。
        
        使用pickle序列化状态对象，然后用base64编码保存到文件存储中。
        支持新的用户ID机制和旧版本的兼容性处理。
        
        Args:
            sid (str): 会话ID
            file_store (FileStore): 文件存储实例
            user_id (str | None): 用户ID，可为None
            
        Raises:
            Exception: 保存失败时抛出异常
        """
        # 使用pickle序列化状态对象
        pickled = pickle.dumps(self)
        logger.debug(f'Saving state to session {sid}:{self.agent_state}')
        # 使用base64编码序列化后的数据
        encoded = base64.b64encode(pickled).decode('utf-8')
        try:
            # 写入文件存储
            file_store.write(
                get_conversation_agent_state_filename(sid, user_id), encoded
            )

            # 检查旧目录中是否存在状态文件（用于SaaS/远程使用场景）并删除
            if user_id:
                filename = get_conversation_agent_state_filename(sid)
                try:
                    file_store.delete(filename)
                except Exception:
                    # 删除失败时忽略异常
                    pass
        except Exception as e:
            logger.error(f'Failed to save state to session: {e}')
            raise e

    @staticmethod
    def restore_from_session(
        sid: str, file_store: FileStore, user_id: str | None = None
    ) -> 'State':
        """从之前保存的会话中恢复状态。
        
        尝试从文件存储中读取状态文件，反序列化并恢复State对象。
        支持新旧两种文件路径格式的兼容性处理。
        
        Args:
            sid (str): 会话ID
            file_store (FileStore): 文件存储实例
            user_id (str | None): 用户ID，可为None
            
        Returns:
            State: 恢复的状态对象
            
        Raises:
            FileNotFoundError: 当找不到状态文件时抛出
            Exception: 其他恢复错误时抛出
        """
        state: State
        try:
            # 尝试从新路径读取状态文件
            encoded = file_store.read(
                get_conversation_agent_state_filename(sid, user_id)
            )
            # base64解码
            pickled = base64.b64decode(encoded)
            # pickle反序列化
            state = pickle.loads(pickled)
        except FileNotFoundError:
            # 如果提供了user_id，说明是SaaS/远程使用场景
            # 需要检查旧目录中是否存在状态文件
            if user_id:
                filename = get_conversation_agent_state_filename(sid)
                encoded = file_store.read(filename)
                pickled = base64.b64decode(encoded)
                state = pickle.loads(pickled)
            else:
                raise FileNotFoundError(
                    f'Could not restore state from session file for sid: {sid}'
                )
        except Exception as e:
            logger.debug(f'Could not restore state from session: {e}')
            raise e

        # 更新状态
        if state.agent_state in RESUMABLE_STATES:
            # 如果当前状态可恢复，保存为恢复状态
            state.resume_state = state.agent_state
        else:
            state.resume_state = None

        # 恢复后的第一个状态
        state.agent_state = AgentState.LOADING

        # 这里不需要清理弃用字段
        # 它们将在再次保存状态时由__getstate__处理

        return state

    def __getstate__(self) -> dict:
        """获取用于pickle序列化的状态字典。
        
        在序列化前清理不需要持久化的数据，包括：
        - 历史记录（将从事件流中恢复）
        - 视图缓存属性
        - 弃用的字段
        
        Returns:
            dict: 清理后的状态字典
        """
        # 不要pickle历史记录，它将从事件流中恢复
        state = self.__dict__.copy()
        state['history'] = []

        # 移除任何视图缓存属性，它们将在历史重新加载后重建
        state.pop('_history_checksum', None)
        state.pop('_view', None)

        # 在pickle前移除弃用字段
        state.pop('iteration', None)
        state.pop('local_iteration', None)
        state.pop('max_iterations', None)
        state.pop('traffic_control_state', None)
        state.pop('local_metrics', None)
        state.pop('delegates', None)

        return state

    def __setstate__(self, state: dict) -> None:
        """从pickle反序列化设置状态。
        
        处理版本兼容性，将旧版本的迭代跟踪转换为新的控制标志格式。
        确保所有必要的属性都存在并具有默认值。
        
        Args:
            state (dict): 反序列化的状态字典
        """
        # 检查是否从旧版本恢复（控制标志之前的版本）
        is_old_version = 'iteration' in state

        # 如果需要，将旧的迭代跟踪转换为新的iteration_flag
        if is_old_version:
            # 从旧值创建iteration_flag
            max_iterations = state.get('max_iterations', 100)
            current_iteration = state.get('iteration', 0)

            # 将iteration_flag添加到状态中
            state['iteration_flag'] = IterationControlFlag(
                limit_increase_amount=max_iterations,
                current_value=current_iteration,
                max_value=max_iterations,
            )

        # 更新状态
        self.__dict__.update(state)

        # 保留弃用字段以保持向后兼容性
        # 它们将在再次保存状态时被__getstate__移除

        # 确保我们总是有history属性
        if not hasattr(self, 'history'):
            self.history = []

        # 如果缺少新字段，确保有默认值
        if not hasattr(self, 'iteration_flag'):
            self.iteration_flag = IterationControlFlag(
                limit_increase_amount=100, current_value=0, max_value=100
            )

        if not hasattr(self, 'budget_flag'):
            self.budget_flag = None

    def get_current_user_intent(self) -> tuple[str | None, list[str] | None]:
        """获取最新的用户消息和图片（如果提供）。
        
        返回在FinishAction之后出现的最新用户消息，如果还没有完成任何操作则返回第一个（任务）。
        这用于确定用户当前的意图和需求。
        
        Returns:
            tuple[str | None, list[str] | None]: 包含用户消息内容和图片URL列表的元组
        """
        last_user_message = None
        last_user_message_image_urls: list[str] | None = []
        
        # 从最新事件开始向前搜索
        for event in reversed(self.view):
            if isinstance(event, MessageAction) and event.source == 'user':
                # 找到用户消息
                last_user_message = event.content
                last_user_message_image_urls = event.image_urls
            elif isinstance(event, AgentFinishAction):
                # 如果遇到完成操作且已找到用户消息，返回该消息
                if last_user_message is not None:
                    return last_user_message, None

        # 返回最后找到的用户消息和图片
        return last_user_message, last_user_message_image_urls

    def get_last_agent_message(self) -> MessageAction | None:
        """获取最后一条Agent消息。
        
        从历史记录中搜索最近的Agent发送的消息。
        
        Returns:
            MessageAction | None: 最后一条Agent消息，如果没有则返回None
        """
        # 从最新事件开始搜索
        for event in reversed(self.view):
            if isinstance(event, MessageAction) and event.source == EventSource.AGENT:
                return event
        return None

    def get_last_user_message(self) -> MessageAction | None:
        """获取最后一条用户消息。
        
        从历史记录中搜索最近的用户发送的消息。
        
        Returns:
            MessageAction | None: 最后一条用户消息，如果没有则返回None
        """
        # 从最新事件开始搜索
        for event in reversed(self.view):
            if isinstance(event, MessageAction) and event.source == EventSource.USER:
                return event
        return None

    def to_llm_metadata(self, agent_name: str) -> dict:
        """生成用于LLM的Metadata信息。
        
        创建包含会话信息、版本信息和标签的Metadata字典，
        用于在LLM调用时提供上下文信息。
        
        Args:
            agent_name (str): Agent名称
            
        Returns:
            dict: LLM Metadata字典
        """
        return {
            'session_id': self.session_id,
            'trace_version': openhands.__version__,
            'tags': [
                f'agent:{agent_name}',
                f'web_host:{os.environ.get("WEB_HOST", "unspecified")}',
                f'openhands_version:{openhands.__version__}',
            ],
        }

    def get_local_step(self):
        """获取本地步骤数。
        
        计算当前Agent在本地执行的步骤数。
        如果没有父级迭代，返回当前迭代值；否则返回相对于父级的增量。
        
        Returns:
            int: 本地步骤数
        """
        if not self.parent_iteration:
            return self.iteration_flag.current_value

        return self.iteration_flag.current_value - self.parent_iteration

    def get_local_metrics(self):
        """获取本地Metrics指标。
        
        如果没有父级指标快照，返回当前指标；
        否则返回当前指标与父级快照的差值。
        
        Returns:
            Metrics: 本地指标对象
        """
        if not self.parent_metrics_snapshot:
            return self.metrics
        return self.metrics.diff(self.parent_metrics_snapshot)

    @property
    def view(self) -> View:
        """获取历史事件的View视图。
        
        计算历史的简单校验和以查看是否可以重用任何缓存的视图。
        如果历史已更改，需要重新创建视图并更新缓存。
        
        Returns:
            View: 事件视图对象
        """
        # 从历史计算简单校验和以查看是否可以重用缓存的视图
        history_checksum = len(self.history)
        old_history_checksum = getattr(self, '_history_checksum', -1)

        # 如果历史已更改，需要重新创建视图并更新缓存
        if history_checksum != old_history_checksum:
            self._history_checksum = history_checksum
            self._view = View.from_events(self.history)

        return self._view
