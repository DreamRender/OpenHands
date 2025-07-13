from pydantic import BaseModel, Field

from openhands.core.logger import openhands_logger as logger
from openhands.events.action import (
    Action,
    ChangeAgentStateAction,
    MessageAction,
    NullAction,
)
from openhands.events.event import EventSource
from openhands.events.observation import (
    AgentStateChangedObservation,
    NullObservation,
    Observation,
)
from openhands.events.serialization.event import event_to_dict
from openhands.security.invariant.nodes import Function, Message, ToolCall, ToolOutput

# TraceElement类型别名，表示trace中可能出现的元素类型
TraceElement = Message | ToolCall | ToolOutput | Function


def get_next_id(trace: list[TraceElement]) -> str:
    """获取下一个可用的ID
    
    Args:
        trace: 当前的trace元素列表
        
    Returns:
        str: 下一个可用的ID字符串
        
    该函数遍历trace中所有ToolCall的ID，找到第一个未使用的数字ID。
    从1开始递增，确保ID的唯一性。
    """
    # 收集所有已使用的ToolCall ID
    used_ids = [el.id for el in trace if isinstance(el, ToolCall)]
    
    # 从1开始查找未使用的ID
    for i in range(1, len(used_ids) + 2):
        if str(i) not in used_ids:
            return str(i)
    
    # 如果都被使用了，返回"1"作为默认值
    return '1'


def get_last_id(
    trace: list[TraceElement],
) -> str | None:
    """获取trace中最后一个ToolCall的ID
    
    Args:
        trace: 当前的trace元素列表
        
    Returns:
        str | None: 最后一个ToolCall的ID，如果没有则返回None
        
    该函数从trace的末尾开始向前查找，返回最近一个ToolCall的ID。
    主要用于将ToolOutput与对应的ToolCall关联。
    """
    # 从后向前遍历trace
    for el in reversed(trace):
        if isinstance(el, ToolCall):
            return el.id
    return None


def parse_action(trace: list[TraceElement], action: Action) -> list[TraceElement]:
    """将Action解析为TraceElement列表
    
    Args:
        trace: 当前的trace元素列表，用于生成新的ID
        action: 需要解析的Action对象
        
    Returns:
        list[TraceElement]: 解析后的TraceElement列表
        
    该函数将OpenHands的Action对象转换为Invariant格式的TraceElement。
    不同类型的Action会被转换为不同的TraceElement组合。
    """
    # 获取下一个可用的ID
    next_id = get_next_id(trace)
    inv_trace: list[TraceElement] = []
    
    if isinstance(action, MessageAction):
        # MessageAction转换为Message
        if action.source == EventSource.USER:
            # 用户消息
            inv_trace.append(Message(role='user', content=action.content))
        else:
            # 助手消息
            inv_trace.append(Message(role='assistant', content=action.content))
            
    elif isinstance(action, (NullAction, ChangeAgentStateAction)):
        # 空Action和状态变更Action不需要转换
        pass
        
    elif hasattr(action, 'action') and action.action is not None:
        # 其他有action属性的Action转换为ToolCall
        # 将Action序列化为字典
        event_dict = event_to_dict(action)
        args = event_dict.get('args', {})
        
        # 提取thought字段，如果存在的话
        thought = args.pop('thought', None)

        # 创建Function对象
        function = Function(name=action.action, arguments=args)
        
        # 如果有thought，先添加thought消息
        if thought is not None:
            inv_trace.append(Message(role='assistant', content=thought))
            
        # 添加ToolCall
        inv_trace.append(ToolCall(id=next_id, type='function', function=function))
    else:
        # 未知的Action类型
        logger.error(f'Unknown action type: {type(action)}')
        
    return inv_trace


def parse_observation(
    trace: list[TraceElement], obs: Observation
) -> list[TraceElement]:
    """将Observation解析为TraceElement列表
    
    Args:
        trace: 当前的trace元素列表，用于获取关联的ToolCall ID
        obs: 需要解析的Observation对象
        
    Returns:
        list[TraceElement]: 解析后的TraceElement列表
        
    该函数将OpenHands的Observation对象转换为Invariant格式的TraceElement。
    主要用于表示工具执行的结果。
    """
    # 获取最后一个ToolCall的ID，用于关联
    last_id = get_last_id(trace)
    
    if isinstance(obs, (NullObservation, AgentStateChangedObservation)):
        # 空Observation和状态变更Observation不需要转换
        return []
    elif hasattr(obs, 'content') and obs.content is not None:
        # 有内容的Observation转换为ToolOutput
        return [ToolOutput(role='tool', content=obs.content, tool_call_id=last_id)]
    else:
        # 未知的Observation类型
        logger.error(f'Unknown observation type: {type(obs)}')
    return []


def parse_element(
    trace: list[TraceElement], element: Action | Observation
) -> list[TraceElement]:
    """解析Action或Observation为TraceElement列表
    
    Args:
        trace: 当前的trace元素列表
        element: 需要解析的Action或Observation对象
        
    Returns:
        list[TraceElement]: 解析后的TraceElement列表
        
    这是一个统一的解析入口，根据element的类型分发到相应的解析函数。
    """
    if isinstance(element, Action):
        return parse_action(trace, element)
    return parse_observation(trace, element)


def parse_trace(trace: list[tuple[Action, Observation]]) -> list[TraceElement]:
    """解析完整的trace序列
    
    Args:
        trace: Action和Observation的元组列表
        
    Returns:
        list[TraceElement]: 解析后的完整TraceElement列表
        
    该函数将OpenHands格式的trace（Action-Observation对序列）
    转换为Invariant格式的TraceElement序列。
    """
    inv_trace: list[TraceElement] = []
    
    # 遍历每个Action-Observation对
    for action, obs in trace:
        # 先解析Action
        inv_trace.extend(parse_action(inv_trace, action))
        # 再解析Observation
        inv_trace.extend(parse_observation(inv_trace, obs))
        
    return inv_trace


class InvariantState(BaseModel):
    """Invariant状态管理类
    
    该类用于管理Invariant格式的trace状态，提供添加Action和Observation的方法，
    以及与其他InvariantState合并的功能。
    """
    
    trace: list[TraceElement] = Field(default_factory=list)  # 存储TraceElement的列表

    def add_action(self, action: Action) -> None:
        """添加Action到trace中
        
        Args:
            action: 需要添加的Action对象
            
        该方法将Action解析为TraceElement并添加到当前trace中。
        """
        self.trace.extend(parse_action(self.trace, action))

    def add_observation(self, obs: Observation) -> None:
        """添加Observation到trace中
        
        Args:
            obs: 需要添加的Observation对象
            
        该方法将Observation解析为TraceElement并添加到当前trace中。
        """
        self.trace.extend(parse_observation(self.trace, obs))

    def concatenate(self, other: 'InvariantState') -> None:
        """将另一个InvariantState的trace合并到当前trace中
        
        Args:
            other: 需要合并的InvariantState对象
            
        该方法用于合并多个InvariantState的trace数据。
        """
        self.trace.extend(other.trace)