from __future__ import annotations

# 导入核心配置模块
from openhands.core.config.condenser_config import ConversationWindowCondenserConfig
# 导入日志记录器
from openhands.core.logger import openhands_logger as logger
# 导入Agent相关的Action类
from openhands.events.action.agent import (
    CondensationAction,  # 压缩Action，用于指定需要遗忘的事件
    RecallAction,  # 回忆Action，用于从记忆中检索信息
)
# 导入消息相关的Action类
from openhands.events.action.message import MessageAction, SystemMessageAction
# 导入事件源枚举
from openhands.events.event import EventSource
# 导入观察事件类
from openhands.events.observation import Observation
# 导入内存压缩相关的基础类和接口
from openhands.memory.condenser.condenser import Condensation, RollingCondenser, View


class ConversationWindowCondenser(RollingCondenser):
    """
    对话窗口Condenser类。

    该类实现了基于对话窗口截断的记忆压缩策略，类似于传统的对话窗口方法。
    它会保留对话历史中的关键初始事件（如系统消息、首个用户消息等），
    并保留大约一半的历史记录，确保Action-Observation配对关系得到保持。

    继承自:
        RollingCondenser: 滚动式Condenser的基类
    """

    def __init__(self) -> None:
        """
        初始化ConversationWindowCondenser实例。

        调用父类构造函数完成基础初始化。
        """
        super().__init__()

    def get_condensation(self, view: View) -> Condensation:
        """
        获取对话历史的压缩方案，类似于_apply_conversation_window方法的截断逻辑。

        该方法执行以下步骤：
        1. 识别关键的初始事件（系统消息、首个用户消息、Recall Observation等）
        2. 保留大约一半的历史记录
        3. 确保Action-Observation配对关系得到保持
        4. 返回一个CondensationAction，指定需要遗忘的事件

        Args:
            view (View): 包含事件历史的视图对象

        Returns:
            Condensation: 包含压缩Action的Condensation对象
        """
        events = view.events

        # 处理空历史记录的情况
        if not events:
            # 没有事件需要压缩，返回空的forgotten_event_ids列表
            action = CondensationAction(forgotten_event_ids=[])
            return Condensation(action=action)

        # 1. 识别关键的初始事件
        system_message: SystemMessageAction | None = None  # 系统消息
        first_user_msg: MessageAction | None = None  # 首个用户消息
        recall_action: RecallAction | None = None  # 回忆Action
        recall_observation: Observation | None = None  # 回忆对应的Observation

        # 查找系统消息（如果存在，应该是第一个事件）
        system_message = next(
            (e for e in events if isinstance(e, SystemMessageAction)), None
        )

        # 查找首个用户消息
        first_user_msg = next(
            (
                e
                for e in events
                if isinstance(e, MessageAction) and e.source == EventSource.USER
            ),
            None,
        )

        # 如果没有找到用户消息，记录警告并返回空压缩
        if first_user_msg is None:
            logger.warning(
                'No first user message found in history during condensation.'
            )
            # 如果没有用户消息，返回空的压缩结果
            action = CondensationAction(forgotten_event_ids=[])
            return Condensation(action=action)

        # 查找首个用户消息的索引位置
        first_user_msg_index = -1
        for i, event in enumerate(events):
            if isinstance(event, MessageAction) and event.source == EventSource.USER:
                first_user_msg_index = i
                break

        # 查找与首个用户消息相关的RecallAction和对应的Observation
        for i in range(first_user_msg_index + 1, len(events)):
            event = events[i]
            # 检查是否是查询内容与首个用户消息内容相同的RecallAction
            if (
                    isinstance(event, RecallAction)
                    and event.query == first_user_msg.content
            ):
                recall_action = event
                # 查找该RecallAction对应的Observation
                for j in range(i + 1, len(events)):
                    obs_event = events[j]
                    # 通过cause字段匹配Action和Observation的关联关系
                    if (
                            isinstance(obs_event, Observation)
                            and obs_event.cause == recall_action.id
                    ):
                        recall_observation = obs_event
                        break
                break

        # 收集所有关键事件的ID
        essential_events: list[int] = []  # 存储关键事件的ID列表
        if system_message:
            essential_events.append(system_message.id)
        essential_events.append(first_user_msg.id)
        if recall_action:
            essential_events.append(recall_action.id)
            if recall_observation:
                essential_events.append(recall_observation.id)

        # 2. 确定需要保留的事件
        num_essential_events = len(essential_events)  # 关键事件数量
        total_events = len(events)  # 总事件数量
        num_non_essential_events = total_events - num_essential_events  # 非关键事件数量

        # 保留大约一半的非关键事件
        num_recent_to_keep = max(1, num_non_essential_events // 2)

        # 计算保留的最近事件的起始索引
        slice_start_index = total_events - num_recent_to_keep
        slice_start_index = max(0, slice_start_index)

        # 3. 处理切片开始处的悬空Observation（没有对应Action的Observation）
        # 在切片中查找第一个非Observation事件
        recent_events_slice = events[slice_start_index:]
        first_valid_event_index_in_slice = 0
        for i, event in enumerate(recent_events_slice):
            if not isinstance(event, Observation):
                first_valid_event_index_in_slice = i
                break
        else:
            # 如果切片中的所有事件都是Observation
            first_valid_event_index_in_slice = len(recent_events_slice)

        # 检查最近切片中的所有事件是否都是悬空的Observation
        if first_valid_event_index_in_slice == len(recent_events_slice):
            logger.warning(
                'All recent events are dangling observations, which we truncate. This means the agent has only the essential first events. This should not happen.'
            )

        # 计算在完整事件列表中的实际索引
        first_valid_event_index = slice_start_index + first_valid_event_index_in_slice

        # 如果移除了悬空的Observation，记录调试信息
        if first_valid_event_index_in_slice > 0:
            logger.debug(
                f'Removed {first_valid_event_index_in_slice} dangling observation(s) '
                f'from the start of recent event slice.'
            )

        # 4. 确定要保留的事件和要遗忘的事件
        events_to_keep: set[int] = set(essential_events)  # 初始化为关键事件集合

        # 添加从first_valid_event_index开始的最近事件
        for i in range(first_valid_event_index, total_events):
            events_to_keep.add(events[i].id)

        # 计算需要遗忘的事件ID
        all_event_ids = {e.id for e in events}  # 所有事件ID的集合
        forgotten_event_ids = sorted(all_event_ids - events_to_keep)  # 计算差集并排序

        # 记录压缩统计信息
        logger.info(
            f'ConversationWindowCondenser: Keeping {len(events_to_keep)} events, '
            f'forgetting {len(forgotten_event_ids)} events.'
        )

        # 创建压缩Action
        if forgotten_event_ids:
            # 如果遗忘的事件ID是连续的，使用范围表示方式
            if (
                    len(forgotten_event_ids) > 1
                    and forgotten_event_ids[-1] - forgotten_event_ids[0]
                    == len(forgotten_event_ids) - 1
            ):
                # 使用起始和结束ID表示连续范围
                action = CondensationAction(
                    forgotten_events_start_id=forgotten_event_ids[0],
                    forgotten_events_end_id=forgotten_event_ids[-1],
                )
            else:
                # 使用离散的ID列表
                action = CondensationAction(forgotten_event_ids=forgotten_event_ids)
        else:
            # 没有需要遗忘的事件
            action = CondensationAction(forgotten_event_ids=[])

        return Condensation(action=action)

    def should_condense(self, view: View) -> bool:
        """
        判断是否应该进行记忆压缩。

        Args:
            view (View): 包含事件历史的视图对象

        Returns:
            bool: 如果存在未处理的压缩请求则返回True，否则返回False
        """
        # 检查是否有未处理的压缩请求
        return view.unhandled_condensation_request

    @classmethod
    def from_config(
            cls, _config: ConversationWindowCondenserConfig
    ) -> ConversationWindowCondenser:
        """
        从配置对象创建ConversationWindowCondenser实例。

        Args:
            _config (ConversationWindowCondenserConfig): Condenser配置对象（当前未使用）

        Returns:
            ConversationWindowCondenser: 新创建的Condenser实例
        """
        return ConversationWindowCondenser()


# 向ConversationWindowCondenser类注册对应的配置类
ConversationWindowCondenser.register_config(ConversationWindowCondenserConfig)
