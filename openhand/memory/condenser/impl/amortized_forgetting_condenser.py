from __future__ import annotations

# 导入Condenser配置类，用于配置摊销遗忘Condenser的参数
from openhands.core.config.condenser_config import AmortizedForgettingCondenserConfig
# 导入Condensation Action，用于表示压缩操作的Action类型
from openhands.events.action.agent import CondensationAction
# 导入Condenser相关的核心类：压缩结果、滚动Condenser基类、视图类
from openhands.memory.condenser.condenser import (
    Condensation,  # 压缩操作的结果类
    RollingCondenser,  # 滚动式Condenser的基类
    View,  # 事件视图类，表示一系列事件的集合
)


class AmortizedForgettingCondenser(RollingCondenser):
    """
    摊销遗忘Condenser类

    一个维护压缩历史记录的Condenser，当历史记录增长过大时会遗忘旧事件。
    采用摊销策略，即不是每次都进行压缩，而是当达到阈值时批量处理，
    以平摊压缩操作的成本。遗忘策略是保留开头和结尾的事件，丢弃中间部分。

    继承自RollingCondenser，实现了特定的压缩和遗忘逻辑。
    """

    def __init__(self, max_size: int = 100, keep_first: int = 0):
        """
        初始化摊销遗忘Condenser

        Args:
            max_size (int): 触发遗忘操作前历史记录的最大尺寸，默认为100
            keep_first (int): 始终保留的初始事件数量，默认为0

        Raises:
            ValueError: 当keep_first大于等于max_size的一半时抛出异常
            ValueError: 当keep_first为负数时抛出异常
            ValueError: 当max_size非正数时抛出异常

        Note:
            keep_first必须小于max_size的一半，这样可以确保在压缩时
            有足够的空间同时保留开头和结尾的事件
        """
        # 验证keep_first不能大于等于max_size的一半
        # 这样可以确保压缩后至少有一半空间用于保留尾部事件
        if keep_first >= max_size // 2:
            raise ValueError(
                f'keep_first ({keep_first}) must be less than half of max_size ({max_size})'
            )
        # 验证keep_first不能为负数
        if keep_first < 0:
            raise ValueError(f'keep_first ({keep_first}) cannot be negative')
        # 验证max_size必须为正数
        if max_size < 1:
            raise ValueError(f'max_size ({keep_first}) cannot be non-positive')

        # 历史记录达到此大小时触发遗忘操作
        self.max_size = max_size
        # 始终保留的开头事件数量
        self.keep_first = keep_first

        # 调用父类构造函数完成基础初始化
        super().__init__()

    def get_condensation(self, view: View) -> Condensation:
        """
        获取压缩操作的结果

        执行摊销遗忘策略：保留开头的keep_first个事件和结尾的若干事件，
        删除中间部分的事件，使总数减少到max_size的一半。

        Args:
            view (View): 需要进行压缩的事件视图

        Returns:
            Condensation: 包含压缩操作信息的对象，描述了哪些事件被遗忘

        Note:
            压缩策略是双端保留：保留开头的关键事件（如初始化事件）
            和最近的事件（保持上下文连续性），删除中间的事件
        """
        # 计算压缩后的目标大小（max_size的一半）
        target_size = self.max_size // 2

        # 从view开头获取需要保留的事件（数量为keep_first）
        head = view[: self.keep_first]

        # 计算需要从尾部保留的事件数量
        # 总的保留数量(target_size) - 已保留的头部事件数量
        events_from_tail = target_size - len(head)
        # 从view尾部获取需要保留的事件
        tail = view[-events_from_tail:]

        # 创建需要保留的事件ID集合（头部+尾部事件的ID）
        event_ids_to_keep = {event.id for event in head + tail}
        # 计算需要遗忘的事件ID集合（所有事件ID - 保留的事件ID）
        event_ids_to_forget = {event.id for event in view} - event_ids_to_keep

        # 创建压缩Action，记录被遗忘事件的ID范围
        # forgotten_events_start_id: 被遗忘事件的最小ID
        # forgotten_events_end_id: 被遗忘事件的最大ID
        event = CondensationAction(
            forgotten_events_start_id=min(event_ids_to_forget),
            forgotten_events_end_id=max(event_ids_to_forget),
        )

        # 返回包含压缩Action的Condensation对象
        return Condensation(action=event)

    def should_condense(self, view: View) -> bool:
        """
        判断是否应该执行压缩操作

        当事件视图的大小超过设定的最大值时，返回True表示需要压缩。
        这是摊销策略的体现：不是每次添加事件都压缩，而是达到阈值才压缩。

        Args:
            view (View): 当前的事件视图

        Returns:
            bool: 如果需要压缩返回True，否则返回False
        """
        # 简单的阈值检查：当前视图大小是否超过最大允许大小
        return len(view) > self.max_size

    @classmethod
    def from_config(
            cls, config: AmortizedForgettingCondenserConfig
    ) -> AmortizedForgettingCondenser:
        """
        从配置对象创建AmortizedForgettingCondenser实例

        这是一个类方法，用于支持依赖注入和配置驱动的对象创建模式。
        从配置对象中提取参数，创建相应的Condenser实例。

        Args:
            config (AmortizedForgettingCondenserConfig): 包含Condenser配置参数的对象

        Returns:
            AmortizedForgettingCondenser: 根据配置创建的Condenser实例

        Note:
            使用model_dump(exclude={'type'})来排除配置中的'type'字段，
            因为该字段用于标识配置类型，不是构造函数的参数
        """
        # 从配置对象中提取除'type'外的所有参数，传递给构造函数
        # model_dump()将配置对象转换为字典，exclude={'type'}排除type字段
        return AmortizedForgettingCondenser(**config.model_dump(exclude={'type'}))


# 将AmortizedForgettingCondenserConfig注册为AmortizedForgettingCondenser的配置类
# 这建立了配置类和实现类之间的关联关系，支持配置驱动的对象创建
AmortizedForgettingCondenser.register_config(AmortizedForgettingCondenserConfig)
