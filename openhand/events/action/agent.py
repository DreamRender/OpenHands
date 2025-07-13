from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from openhands.core.schema import ActionType
from openhands.events.action.action import Action
from openhands.events.event import RecallType


@dataclass
class ChangeAgentStateAction(Action):
    """Agent状态变更Action
    
    这是一个伪Action，主要用于通知客户端Agent的任务状态发生了变化。
    不会实际执行任何操作，仅用于状态同步。
    
    Attributes:
        agent_state (str): 新的Agent状态
        thought (str): Agent的思考过程说明，默认为空字符串
        action (str): Action类型，固定为ActionType.CHANGE_AGENT_STATE
    """

    agent_state: str  # Agent的新状态
    thought: str = ''  # 思考过程，可选
    action: str = ActionType.CHANGE_AGENT_STATE  # Action类型标识

    @property
    def message(self) -> str:
        """获取状态变更的消息描述
        
        Returns:
            str: 格式化的状态变更消息
        """
        return f'Agent state changed to {self.agent_state}'


class AgentFinishTaskCompleted(Enum):
    """Agent任务完成状态枚举类
    
    用于表示Agent认为任务的完成程度。
    """
    
    FALSE = 'false'     # 任务未完成
    PARTIAL = 'partial' # 任务部分完成
    TRUE = 'true'       # 任务完全完成


@dataclass
class AgentFinishAction(Action):
    """Agent完成任务Action
    
    当Agent认为任务已经完成时使用的Action。包含了任务完成的相关信息，
    如最终想法、完成状态和输出结果等。

    Attributes:
        final_thought (str): 发送给用户的最终消息
        task_completed (AgentFinishTaskCompleted | None): Agent认为任务是否已完成的状态
        outputs (dict): Agent的其他输出，例如"content"等
        thought (str): Agent对其行为的解释
        action (str): Action类型，固定为ActionType.FINISH
    """

    final_thought: str = ''  # 最终想法或总结
    task_completed: AgentFinishTaskCompleted | None = None  # 任务完成状态
    outputs: dict[str, Any] = field(default_factory=dict)  # 其他输出内容
    thought: str = ''  # 思考过程
    action: str = ActionType.FINISH  # Action类型标识

    @property
    def message(self) -> str:
        """获取完成任务的消息
        
        Returns:
            str: 如果有思考内容则返回思考内容，否则返回默认完成消息
        """
        if self.thought != '':
            return self.thought
        return "All done! What's next on the agenda?"


@dataclass
class AgentThinkAction(Action):
    """Agent思考Action
    
    用于记录Agent的思考过程。这种Action不会执行实际操作，
    而是用来展示Agent的推理和决策过程。

    Attributes:
        thought (str): Agent对其行为的解释
        action (str): Action类型，固定为ActionType.THINK
    """

    thought: str = ''  # Agent的思考内容
    action: str = ActionType.THINK  # Action类型标识

    @property
    def message(self) -> str:
        """获取思考过程的消息
        
        Returns:
            str: 格式化的思考消息
        """
        return f'I am thinking...: {self.thought}'


@dataclass
class AgentRejectAction(Action):
    """Agent拒绝任务Action
    
    当Agent拒绝执行某个任务时使用的Action。
    可以包含拒绝的原因和其他相关输出。
    
    Attributes:
        outputs (dict): 输出信息，可包含拒绝原因等
        thought (str): 思考过程
        action (str): Action类型，固定为ActionType.REJECT
    """
    
    outputs: dict = field(default_factory=dict)  # 输出信息，如拒绝原因
    thought: str = ''  # 思考过程
    action: str = ActionType.REJECT  # Action类型标识

    @property
    def message(self) -> str:
        """获取拒绝任务的消息
        
        Returns:
            str: 拒绝消息，如果outputs中包含原因则会附加原因说明
        """
        msg: str = 'Task is rejected by the agent.'
        if 'reason' in self.outputs:
            msg += ' Reason: ' + self.outputs['reason']
        return msg


@dataclass
class AgentDelegateAction(Action):
    """Agent委托任务Action
    
    当Agent需要将任务委托给其他Agent时使用的Action。
    用于实现Agent之间的协作和任务分配。
    
    Attributes:
        agent (str): 被委托的Agent标识
        inputs (dict): 传递给被委托Agent的输入参数
        thought (str): 委托的思考过程
        action (str): Action类型，固定为ActionType.DELEGATE
    """
    
    agent: str  # 被委托的Agent名称或标识
    inputs: dict  # 传递给被委托Agent的输入数据
    thought: str = ''  # 委托决策的思考过程
    action: str = ActionType.DELEGATE  # Action类型标识

    @property
    def message(self) -> str:
        """获取委托任务的消息
        
        Returns:
            str: 格式化的委托消息
        """
        return f"I'm asking {self.agent} for help with this task."


@dataclass
class RecallAction(Action):
    """内容检索Action
    
    用于从全局目录或用户Workspace中检索内容。
    支持不同类型的检索操作，如文档搜索、记忆查询等。
    
    Attributes:
        recall_type (RecallType): 检索类型，定义了检索的来源和方式
        query (str): 检索查询字符串
        thought (str): 检索的思考过程
        action (str): Action类型，固定为ActionType.RECALL
    """

    recall_type: RecallType  # 检索类型
    query: str = ''  # 检索查询内容
    thought: str = ''  # 检索的思考过程
    action: str = ActionType.RECALL  # Action类型标识

    @property
    def message(self) -> str:
        """获取检索操作的消息
        
        Returns:
            str: 包含查询内容前50个字符的检索消息
        """
        return f'Retrieving content for: {self.query[:50]}'

    def __str__(self) -> str:
        """获取RecallAction的字符串表示
        
        Returns:
            str: 格式化的RecallAction描述
        """
        ret = '**RecallAction**\n'
        ret += f'QUERY: {self.query[:50]}'
        return ret


@dataclass
class CondensationAction(Action):
    """对话历史压缩Action
    
    用于表示对话历史正在被压缩的操作。有两种方式指定要遗忘的事件：
    1. 提供事件ID列表
    2. 提供事件ID的起始和结束范围
    
    在第二种情况下，假设事件ID是单调递增的，起始和结束ID之间的所有事件都将被遗忘。

    Attributes:
        action (str): Action类型，固定为ActionType.CONDENSATION
        forgotten_event_ids (list[int] | None): 被遗忘的事件ID列表
        forgotten_events_start_id (int | None): 要遗忘的事件范围的起始ID
        forgotten_events_end_id (int | None): 要遗忘的事件范围的结束ID
        summary (str | None): 被遗忘事件的可选摘要
        summary_offset (int | None): 摘要在结果View中的插入位置偏移量
    
    Raises:
        ValueError: 当可选字段的配置无效时抛出异常
    """

    action: str = ActionType.CONDENSATION  # Action类型标识

    forgotten_event_ids: list[int] | None = None
    """被遗忘的事件ID列表（从LLM的View中移除）"""

    forgotten_events_start_id: int | None = None
    """事件范围中第一个要遗忘的事件ID"""

    forgotten_events_end_id: int | None = None
    """事件范围中最后一个要遗忘的事件ID"""

    summary: str | None = None
    """被遗忘事件的可选摘要"""

    summary_offset: int | None = None
    """摘要在结果View中应该插入位置的可选偏移量"""

    def _validate_field_polymorphism(self) -> bool:
        """检查可选字段是否在有效配置中实例化
        
        验证遗忘事件的配置和摘要配置是否有效：
        - 遗忘事件配置：只能使用事件ID列表或事件范围二者之一
        - 摘要配置：summary和summary_offset必须同时存在或同时为None
        
        Returns:
            bool: 如果配置有效返回True，否则返回False
        """
        # 对于遗忘的事件，只有两种有效配置：
        # 1. 基于提供的ID列表遗忘事件
        using_event_ids = self.forgotten_event_ids is not None
        # 2. 基于ID范围遗忘事件
        using_event_range = (
            self.forgotten_events_start_id is not None
            and self.forgotten_events_end_id is not None
        )

        # 两种配置只能选择其中一种（异或操作）
        forgotten_event_configuration = using_event_ids ^ using_event_range

        # 检查如果提供了摘要，也必须提供偏移量（反之亦然）
        summary_configuration = (
            self.summary is None and self.summary_offset is None
        ) or (self.summary is not None and self.summary_offset is not None)

        return forgotten_event_configuration and summary_configuration

    def __post_init__(self):
        """数据类初始化后的验证
        
        在对象创建后验证字段配置的有效性。
        
        Raises:
            ValueError: 当可选字段配置无效时抛出异常
        """
        if not self._validate_field_polymorphism():
            raise ValueError('Invalid configuration of the optional fields.')

    @property
    def forgotten(self) -> list[int]:
        """获取应该被遗忘的事件ID列表
        
        根据配置的方式（ID列表或ID范围）返回所有应该被遗忘的事件ID。
        
        Returns:
            list[int]: 应该被遗忘的事件ID列表
            
        Raises:
            ValueError: 当字段配置无效时抛出异常
        """
        # 确保字段配置仍然有效（数据类不是不可变的，需要再次检查）
        if not self._validate_field_polymorphism():
            raise ValueError('Invalid configuration of the optional fields.')

        # 如果使用事件ID列表方式
        if self.forgotten_event_ids is not None:
            return self.forgotten_event_ids

        # 如果使用事件ID范围方式，起始和结束ID此时不应为None
        assert self.forgotten_events_start_id is not None
        assert self.forgotten_events_end_id is not None
        return list(
            range(self.forgotten_events_start_id, self.forgotten_events_end_id + 1)
        )

    @property
    def message(self) -> str:
        """获取压缩操作的消息
        
        Returns:
            str: 如果有摘要则返回摘要，否则返回被删除的事件列表
        """
        if self.summary:
            return f'Summary: {self.summary}'
        return f'Condenser is dropping the events: {self.forgotten}.'


@dataclass
class CondensationRequestAction(Action):
    """请求对话历史压缩Action
    
    用于请求对对话历史进行压缩的Action。
    这通常在对话历史过长需要优化时使用。

    Attributes:
        action (str): Action类型，固定为ActionType.CONDENSATION_REQUEST
    """

    action: str = ActionType.CONDENSATION_REQUEST  # Action类型标识

    @property
    def message(self) -> str:
        """获取压缩请求的消息
        
        Returns:
            str: 压缩请求的标准消息
        """
        return 'Requesting a condensation of the conversation history.'