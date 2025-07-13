"""
消息工具函数模块

此模块提供了用于处理事件和获取 token 使用情况的工具函数。
主要用于从事件中提取 token 使用记录，支持基于事件 ID 和事件对象的查询。
"""

from openhands.events.event import Event
from openhands.llm.metrics import Metrics, TokenUsage


def get_token_usage_for_event(event: Event, metrics: Metrics) -> TokenUsage | None:
    """
    为给定事件返回最多一个 token 使用记录，优先级如下：
      - `tool_call_metadata.model_response.id`（如果可用）
      - 否则使用 event.response_id（如果已设置）

    如果两者都不存在或在 metrics.token_usages 中都没有匹配项，则返回 None。
    
    Args:
        event: 要查找 token 使用情况的事件对象
        metrics: 包含 token 使用记录的 Metrics 对象
        
    Returns:
        匹配的 TokenUsage 对象，如果未找到则返回 None
    """
    # 1) 优先使用 tool_call_metadata 的 response.id（如果存在）
    if event.tool_call_metadata and event.tool_call_metadata.model_response:
        tool_response_id = event.tool_call_metadata.model_response.get('id')
        if tool_response_id:
            # 在 metrics.token_usages 中查找匹配的记录
            usage_rec = next(
                (u for u in metrics.token_usages if u.response_id == tool_response_id),
                None,
            )
            if usage_rec is not None:
                return usage_rec

    # 2) 备选方案：使用顶级的 event.response_id（如果存在）
    if event.response_id:
        return next(
            (u for u in metrics.token_usages if u.response_id == event.response_id),
            None,
        )

    # 如果都没有找到，返回 None
    return None


def get_token_usage_for_event_id(
    events: list[Event], event_id: int, metrics: Metrics
) -> TokenUsage | None:
    """
    从具有 .id == event_id 的事件开始，在 `events` 中向后搜索，
    查找第一个关联的 TokenUsage 记录（如果有），关联方式为：
      - tool_call_metadata.model_response.id，或
      - event.response_id
    返回找到的第一个匹配项，如果没有找到则返回 None。
    
    Args:
        events: 事件列表
        event_id: 要查找的事件 ID
        metrics: 包含 token 使用记录的 Metrics 对象
        
    Returns:
        找到的第一个 TokenUsage 对象，如果未找到则返回 None
    """
    # 查找具有给定 ID 的事件的索引
    idx = next((i for i, e in enumerate(events) if e.id == event_id), None)
    if idx is None:
        return None

    # 从 idx 向后搜索到 0
    for i in range(idx, -1, -1):
        usage = get_token_usage_for_event(events[i], metrics)
        if usage is not None:
            return usage
    
    # 如果没有找到任何匹配项，返回 None
    return None