from __future__ import annotations

from openhands.core.config.condenser_config import ObservationMaskingCondenserConfig
from openhands.events.event import Event
from openhands.events.observation import Observation
from openhands.events.observation.agent import AgentCondensationObservation
from openhands.memory.condenser.condenser import Condensation, Condenser, View


class ObservationMaskingCondenser(Condenser):
    """
    一个对最近注意力窗口外的Observation值进行遮盖的Condenser。

    该Condenser保留最近的一定数量的Observation事件的完整内容，
    而将超出注意力窗口的Observation事件的内容替换为占位符。
    这有助于减少上下文长度，同时保留最相关的最近信息。

    Attributes:
        attention_window (int): 注意力窗口的大小，即保留完整内容的最近事件数量
    """

    def __init__(self, attention_window: int = 5):
        """
        初始化ObservationMaskingCondenser。

        Args:
            attention_window (int, optional): 注意力窗口大小，默认为5。
                                            表示保留最近5个事件的完整内容
        """
        self.attention_window = attention_window

        # 调用父类构造函数
        super().__init__()

    def condense(self, view: View) -> View | Condensation:
        """
        将注意力窗口外的Observation内容替换为占位符。

        遍历所有事件，对于在注意力窗口外的Observation事件，
        将其替换为包含'<MASKED>'占位符的AgentCondensationObservation。

        Args:
            view (View): 输入的事件View对象

        Returns:
            View | Condensation: 新的View对象，其中窗口外的Observation被遮盖
        """
        results: list[Event] = []

        # 遍历所有事件及其索引
        for i, event in enumerate(view):
            # 检查是否为Observation且在注意力窗口外
            # 注意力窗口外的定义：索引小于(总长度 - 窗口大小)
            if isinstance(event, Observation) and i < len(view) - self.attention_window:
                # 将窗口外的Observation替换为遮盖占位符
                results.append(AgentCondensationObservation('<MASKED>'))
            else:
                # 保留窗口内的事件或非Observation事件
                results.append(event)

        # 返回新的View对象
        return View(events=results)

    @classmethod
    def from_config(
            cls, config: ObservationMaskingCondenserConfig
    ) -> ObservationMaskingCondenser:
        """
        从配置对象创建ObservationMaskingCondenser实例。

        Args:
            config (ObservationMaskingCondenserConfig): 配置对象，包含注意力窗口等参数

        Returns:
            ObservationMaskingCondenser: 新创建的实例
        """
        # 使用配置对象的参数创建实例，排除'type'字段
        return ObservationMaskingCondenser(**config.model_dump(exclude={'type'}))


# 注册配置类，使得可以通过配置系统创建该Condenser
ObservationMaskingCondenser.register_config(ObservationMaskingCondenserConfig)
