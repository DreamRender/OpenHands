from __future__ import annotations

from openhands.core.logger import openhands_logger as logger
from openhands.events.action.action import Action
from openhands.events.action.message import MessageAction
from openhands.events.event import Event, EventSource
from openhands.events.observation.empty import NullObservation
from openhands.events.serialization.event import event_from_dict


class ReplayManager:
    """ReplayManager管理给定轨迹的重放会话生命周期。

    Replay manager跟踪事件列表，重放Action，并忽略消息和Observation。
    
    重放机制用于复现之前记录的Agent执行过程，这对于调试、测试和分析很有用。
    重放过程中会按顺序执行之前记录的Action，但会跳过某些类型的事件。

    注意：如果以下情况发生，可能会出现意外甚至错误的结果：
    1) 任何Action是非确定性的，或者
    2) 重放会话开始前的初始状态与轨迹的初始状态不同。
    
    Attributes:
        replay_events (list[Event]): 经过过滤的重放事件列表
        replay_mode (bool): 是否处于重放模式
        replay_index (int): 当前重放进度索引
    """

    def __init__(self, events: list[Event] | None):
        """初始化ReplayManager。
        
        Args:
            events (list[Event] | None): 原始事件列表，可以为None
        """
        # 过滤后的重放事件列表
        replay_events = []
        
        # 遍历所有输入事件进行过滤
        for event in events or []:
            # 忽略ENVIRONMENT事件，因为它们不是由用户或Agent发出的，不应该被重放
            if event.source == EventSource.ENVIRONMENT:
                continue
                
            # 忽略NullObservation，因为它们不包含有用信息
            if isinstance(event, NullObservation):
                continue
                
            # 将符合条件的事件添加到重放列表
            replay_events.append(event)

        # 如果有重放事件，进行进一步处理
        if replay_events:
            logger.info(f'Replay events loaded, events length = {len(replay_events)}')
            
            # 处理除最后一个事件外的所有MessageAction
            for index in range(len(replay_events) - 1):
                event = replay_events[index]
                
                # 对于等待响应的MessageAction（不是最后一个事件）
                if isinstance(event, MessageAction) and event.wait_for_response:
                    # 我们将wait_for_response覆盖为False，因为响应已经包含在下一个事件中，
                    # 我们不希望用户干扰重放过程
                    logger.info(
                        'Replay events contains wait_for_response message action, ignoring wait_for_response'
                    )
                    event.wait_for_response = False
                    
        # 存储处理后的重放事件列表
        self.replay_events = replay_events
        
        # 设置重放模式标志
        self.replay_mode = bool(replay_events)
        
        # 初始化重放索引
        self.replay_index = 0

    def _replayable(self) -> bool:
        """检查当前索引位置的事件是否可重放。
        
        只有Action类型的事件才可以被重放，Observation等其他类型的事件会被跳过。
        
        Returns:
            bool: 如果当前事件是可重放的Action则返回True
        """
        return (
            self.replay_events is not None
            and self.replay_index < len(self.replay_events)
            and isinstance(self.replay_events[self.replay_index], Action)
        )

    def should_replay(self) -> bool:
        """判断Controller是否处于轨迹重放模式且重放尚未完成。
        
        注意：重放完成后，用户和Agent可以继续进行消息/Action交互。
        
        这个方法还会将"replay_index"移动到下一个Action（如果适用）。
        它会跳过所有非Action事件，直到找到下一个可重放的Action或到达列表末尾。
        
        Returns:
            bool: 如果应该继续重放则返回True，否则返回False
        """
        # 如果不在重放模式，直接返回False
        if not self.replay_mode:
            return False

        assert self.replay_events is not None
        
        # 跳过所有非可重放事件，直到找到下一个Action或到达末尾
        while self.replay_index < len(self.replay_events) and not self._replayable():
            self.replay_index += 1

        # 返回是否还有可重放的事件
        return self._replayable()

    def step(self) -> Action:
        """执行重放的下一步，返回当前Action并移动索引。
        
        这个方法应该在should_replay()返回True后调用，
        它返回当前索引位置的Action并将索引向前移动一位。
        
        Returns:
            Action: 当前重放位置的Action对象
            
        Raises:
            AssertionError: 如果重放事件列表为None或当前事件不是Action
        """
        assert self.replay_events is not None
        
        # 获取当前索引位置的事件
        event = self.replay_events[self.replay_index]
        
        # 确保当前事件是Action类型
        assert isinstance(event, Action)
        
        # 移动到下一个事件
        self.replay_index += 1
        
        return event

    @staticmethod
    def get_replay_events(trajectory: list[dict]) -> list[Event]:
        """从轨迹字典列表中获取重放事件列表。
        
        这个静态方法将序列化的轨迹数据转换为Event对象列表，
        并过滤掉不适合重放的事件类型。
        
        Args:
            trajectory (list[dict]): 轨迹的字典表示，每个字典代表一个事件
            
        Returns:
            list[Event]: 经过过滤的重放事件列表
            
        Raises:
            ValueError: 如果trajectory不是列表类型
        """
        # 验证输入参数类型
        if not isinstance(trajectory, list):
            raise ValueError(
                f'Expected a list in {trajectory}, got {type(trajectory).__name__}'
            )
            
        replay_events = []
        
        # 遍历轨迹中的每个事件字典
        for item in trajectory:
            # 将字典转换为Event对象
            event = event_from_dict(item)
            
            # 忽略ENVIRONMENT事件，因为它们不是由用户或Agent发出的，不应该被重放
            if event.source == EventSource.ENVIRONMENT:
                continue
                
            # 无法将带有_id的事件添加到事件流中，因此清除_id
            event._id = None  # type: ignore[attr-defined]
            
            # 添加到重放事件列表
            replay_events.append(event)
            
        return replay_events