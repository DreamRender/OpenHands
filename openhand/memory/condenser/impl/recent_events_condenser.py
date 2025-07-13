from __future__ import annotations

from openhands.core.config.condenser_config import RecentEventsCondenserConfig
from openhands.memory.condenser.condenser import Condensation, Condenser, View


class RecentEventsCondenser(Condenser):
    """
    一个只保留指定数量最近事件的Condenser。

    该Condenser实现了简单的事件数量限制策略，保留开头的一定数量事件
    （通常是重要的初始化事件）和最近的一定数量事件，丢弃中间的事件。

    Attributes:
        keep_first (int): 从开头保留的事件数量
        max_events (int): 总共保留的最大事件数量
    """

    def __init__(self, keep_first: int = 1, max_events: int = 10):
        """
        初始化RecentEventsCondenser。

        Args:
            keep_first (int, optional): 从开头保留的事件数量，默认为1
            max_events (int, optional): 总共保留的最大事件数量，默认为10
        """
        self.keep_first = keep_first
        self.max_events = max_events

        # 调用父类构造函数
        super().__init__()

    def condense(self, view: View) -> View | Condensation:
        """
        只保留最近的事件（最多max_events个）。

        该方法实现了以下逻辑：
        1. 保留开头的keep_first个事件
        2. 计算剩余可用空间
        3. 从尾部保留相应数量的最近事件

        Args:
            view (View): 输入的事件View对象

        Returns:
            View | Condensation: 包含筛选后事件的新View对象
        """
        # 获取头部需要保留的事件
        head = view[: self.keep_first]

        # 计算尾部可以保留的事件数量
        # 确保不为负数：max_events减去已保留的头部事件数量
        tail_length = max(0, self.max_events - len(head))

        # 获取尾部需要保留的最近事件
        tail = view[-tail_length:]

        # 合并头部和尾部事件，创建新的View
        return View(events=head + tail)

    @classmethod
    def from_config(cls, config: RecentEventsCondenserConfig) -> RecentEventsCondenser:
        """
        从配置对象创建RecentEventsCondenser实例。

        Args:
            config (RecentEventsCondenserConfig): 配置对象，包含keep_first和max_events等参数

        Returns:
            RecentEventsCondenser: 新创建的实例
        """
        # 使用配置对象的参数创建实例，排除'type'字段
        return RecentEventsCondenser(**config.model_dump(exclude={'type'}))


# 注册配置类，使得可以通过配置系统创建该Condenser
RecentEventsCondenser.register_config(RecentEventsCondenserConfig)
