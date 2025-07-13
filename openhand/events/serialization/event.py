"""
event.py - Event序列化模块

该模块负责处理Event对象与字典之间的序列化和反序列化操作。
包含了Event的完整序列化机制，支持轨迹数据的生成和内容截断功能。
"""

from dataclasses import asdict
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

from openhands.events import Event, EventSource
from openhands.events.serialization.action import action_from_dict
from openhands.events.serialization.observation import observation_from_dict
from openhands.events.serialization.utils import remove_fields
from openhands.events.tool import ToolCallMetadata
from openhands.llm.metrics import Cost, Metrics, ResponseLatency, TokenUsage

# 顶级键列表，定义了事件序列化时需要处理的主要字段
# TODO: 将'content'字段移动到'extras'中，以优化数据结构
TOP_KEYS = [
    'id',                   # 事件唯一标识符
    'timestamp',            # 事件时间戳
    'source',              # 事件来源
    'message',             # 事件消息内容
    'cause',               # 事件触发原因
    'action',              # 关联的Action
    'observation',         # 关联的Observation
    'tool_call_metadata',  # 工具调用Metadata
    'llm_metrics',         # LLM性能指标
]

# 带下划线前缀的私有字段列表
# 这些字段在对象内部以下划线前缀存储
UNDERSCORE_KEYS = [
    'id',                   # 私有ID字段
    'timestamp',            # 私有时间戳字段
    'source',              # 私有来源字段
    'cause',               # 私有原因字段
    'tool_call_metadata',  # 私有工具调用Metadata字段
    'llm_metrics',         # 私有LLM指标字段
]

# 从轨迹extras中删除的字段集合
# 这些字段包含浏览器相关的临时数据，不需要保存到轨迹中
DELETE_FROM_TRAJECTORY_EXTRAS = {
    'dom_object',                # DOM对象
    'axtree_object',            # 可访问性树对象
    'active_page_index',        # 活动页面索引
    'last_browser_action',      # 最后一次浏览器动作
    'last_browser_action_error', # 最后一次浏览器动作错误
    'focused_element_bid',      # 聚焦元素的浏览器ID
    'extra_element_properties', # 额外的元素属性
}

# 从轨迹extras和截图中删除的字段集合
# 包含DELETE_FROM_TRAJECTORY_EXTRAS的所有字段，以及截图相关数据
DELETE_FROM_TRAJECTORY_EXTRAS_AND_SCREENSHOTS = DELETE_FROM_TRAJECTORY_EXTRAS | {
    'screenshot',           # 截图数据
    'set_of_marks',        # 标记集合
}


def event_from_dict(data: dict[str, Any]) -> 'Event':
    """
    从字典创建Event对象。
    
    该函数是Event反序列化的入口点，根据字典内容判断Event类型
    （Action或Observation），并调用相应的反序列化函数。
    
    Args:
        data (dict[str, Any]): 包含Event信息的字典
        
    Returns:
        Event: 创建的Event对象实例
        
    Raises:
        ValueError: 当字典格式不正确或Event类型未知时抛出
    """
    evt: Event
    
    # 根据字典内容判断Event类型并进行反序列化
    if 'action' in data:
        # 如果包含'action'键，则为Action类型
        evt = action_from_dict(data)
    elif 'observation' in data:
        # 如果包含'observation'键，则为Observation类型
        evt = observation_from_dict(data)
    else:
        # 既不是Action也不是Observation，抛出错误
        raise ValueError(f'Unknown event type: {data}')
    
    # 处理带下划线前缀的私有字段
    for key in UNDERSCORE_KEYS:
        if key in data:
            value = data[key]
            
            # 时间戳字段的特殊处理：转换为ISO格式字符串
            if key == 'timestamp' and isinstance(value, datetime):
                value = value.isoformat()
            
            # 事件源字段的特殊处理：转换为EventSource枚举
            if key == 'source':
                value = EventSource(value)
            
            # 工具调用Metadata字段的特殊处理：创建ToolCallMetadata对象
            if key == 'tool_call_metadata':
                value = ToolCallMetadata(**value)
            
            # LLM指标字段的特殊处理：重建Metrics对象
            if key == 'llm_metrics':
                metrics = Metrics()
                if isinstance(value, dict):
                    # 设置累计成本
                    metrics.accumulated_cost = value.get('accumulated_cost', 0.0)
                    # 设置每任务最大预算（如果可用）
                    metrics.max_budget_per_task = value.get('max_budget_per_task')
                    
                    # 重建成本列表
                    for cost in value.get('costs', []):
                        metrics._costs.append(Cost(**cost))
                    
                    # 重建响应延迟列表
                    metrics.response_latencies = [
                        ResponseLatency(**latency)
                        for latency in value.get('response_latencies', [])
                    ]
                    
                    # 重建令牌使用情况列表
                    metrics.token_usages = [
                        TokenUsage(**usage) for usage in value.get('token_usages', [])
                    ]
                    
                    # 设置累计令牌使用情况（如果可用）
                    if 'accumulated_token_usage' in value:
                        metrics._accumulated_token_usage = TokenUsage(
                            **value.get('accumulated_token_usage', {})
                        )
                value = metrics
            
            # 设置私有属性，属性名前加下划线
            setattr(evt, '_' + key, value)
    
    return evt


def _convert_pydantic_to_dict(obj: BaseModel | dict) -> dict:
    """
    将Pydantic模型转换为字典。
    
    该辅助函数用于处理复杂对象的序列化，确保Pydantic模型
    能够正确转换为字典格式。
    
    Args:
        obj (BaseModel | dict): 要转换的对象，可以是Pydantic模型或字典
        
    Returns:
        dict: 转换后的字典
    """
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    return obj


def event_to_dict(event: 'Event') -> dict:
    """
    将Event对象转换为字典。
    
    该函数是Event序列化的核心方法，将Event对象的所有属性
    转换为字典格式，处理各种特殊字段的序列化。
    
    Args:
        event (Event): 要序列化的Event对象
        
    Returns:
        dict: 序列化后的字典
        
    Raises:
        ValueError: 当Event既不是Action也不是Observation时抛出
    """
    # 使用dataclasses.asdict()获取Event的所有属性
    props = asdict(event)
    d = {}
    
    # 处理顶级键字段
    for key in TOP_KEYS:
        # 检查公有属性或私有属性是否存在且不为None
        if hasattr(event, key) and getattr(event, key) is not None:
            d[key] = getattr(event, key)
        elif hasattr(event, f'_{key}') and getattr(event, f'_{key}') is not None:
            d[key] = getattr(event, f'_{key}')
        
        # ID字段的特殊处理：如果ID为-1则移除
        if key == 'id' and d.get('id') == -1:
            d.pop('id', None)
        
        # 时间戳字段的特殊处理：转换为ISO格式字符串
        if key == 'timestamp' and 'timestamp' in d:
            if isinstance(d['timestamp'], datetime):
                d['timestamp'] = d['timestamp'].isoformat()
        
        # 事件源字段的特殊处理：获取枚举值
        if key == 'source' and 'source' in d:
            d['source'] = d['source'].value
        
        # 回忆类型字段的特殊处理：获取枚举值
        if key == 'recall_type' and 'recall_type' in d:
            d['recall_type'] = d['recall_type'].value
        
        # 工具调用Metadata字段的特殊处理：转换为字典
        if key == 'tool_call_metadata' and 'tool_call_metadata' in d:
            d['tool_call_metadata'] = d['tool_call_metadata'].model_dump()
        
        # LLM指标字段的特殊处理：获取指标数据
        if key == 'llm_metrics' and 'llm_metrics' in d:
            d['llm_metrics'] = d['llm_metrics'].get()
        
        # 从props中移除已处理的键
        props.pop(key, None)
    
    # 清理安全风险字段：如果为None则移除
    if 'security_risk' in props and props['security_risk'] is None:
        props.pop('security_risk')
    
    # 根据Event类型进行不同的处理
    if 'action' in d:
        # Action类型：将剩余属性作为args
        d['args'] = props
        # 如果有超时设置，添加到字典中
        if event.timeout is not None:
            d['timeout'] = event.timeout
    elif 'observation' in d:
        # Observation类型：设置content和extras
        d['content'] = props.pop('content', '')

        # props字典的值可能包含复杂对象，如BaseModel子类的实例
        # 例如CmdOutputMetadata，需要序列化这些对象
        # 同时处理RecallObservation的枚举转换
        d['extras'] = {
            k: (v.value if isinstance(v, Enum) else _convert_pydantic_to_dict(v))
            for k, v in props.items()
        }
        
        # 为CmdOutputObservation包含success字段
        if hasattr(event, 'success'):
            d['success'] = event.success
    else:
        # 既不是Action也不是Observation，抛出错误
        raise ValueError(f'Event must be either action or observation. has: {event}')
    
    return d


def event_to_trajectory(event: 'Event', include_screenshots: bool = False) -> dict:
    """
    将Event转换为轨迹数据格式。
    
    该函数生成用于轨迹记录的Event数据，会根据需要移除不必要的字段，
    以减少数据大小和保护隐私。
    
    Args:
        event (Event): 要转换的Event对象
        include_screenshots (bool, optional): 是否包含截图数据。默认为False
        
    Returns:
        dict: 轨迹格式的字典数据
    """
    # 首先获取完整的Event字典
    d = event_to_dict(event)
    
    # 如果存在extras字段，移除不需要的字段
    if 'extras' in d:
        remove_fields(
            d['extras'],
            DELETE_FROM_TRAJECTORY_EXTRAS
            if include_screenshots
            else DELETE_FROM_TRAJECTORY_EXTRAS_AND_SCREENSHOTS,
        )
    
    return d


def truncate_content(content: str, max_chars: int | None = None) -> str:
    """
    截断观察内容的中间部分（如果内容过长）。
    
    当内容超过指定长度时，保留前半部分和后半部分，
    中间插入截断提示信息，以便LLM了解内容被截断的情况。
    
    Args:
        content (str): 要截断的内容字符串
        max_chars (int | None, optional): 最大字符数限制。
                                         如果为None或小于0，则不进行截断
        
    Returns:
        str: 截断后的内容字符串，如果不需要截断则返回原始内容
    """
    # 如果未指定最大字符数，或内容长度未超限，或最大字符数小于0，则直接返回原内容
    if max_chars is None or len(content) <= max_chars or max_chars < 0:
        return content

    # 截断中间部分，并向LLM说明内容被截断
    half = max_chars // 2  # 计算前后各保留的字符数
    return (
        content[:half]                                           # 前半部分
        + '\n[... Observation truncated due to length ...]\n'   # 截断提示信息
        + content[-half:]                                        # 后半部分
    )