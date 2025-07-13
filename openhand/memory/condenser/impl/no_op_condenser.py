from __future__ import annotations

from openhands.core.config.condenser_config import NoOpCondenserConfig
from openhands.memory.condenser.condenser import Condensation, Condenser, View


class NoOpCondenser(Condenser):
    """
    一个不对事件序列执行任何操作的Condenser。

    这个Condenser实现了"无操作"模式，即简单地返回原始的事件序列
    而不做任何修改。主要用于测试或当不需要任何压缩时的场景。
    """

    def condense(self, view: View) -> View | Condensation:
        """
        返回未修改的事件列表。

        这个方法实现了Condenser接口的condense方法，但实际上不执行
        任何压缩操作，直接返回输入的View对象。

        Args:
            view (View): 输入的事件View对象

        Returns:
            View | Condensation: 原始的View对象，未做任何修改
        """
        return view

    @classmethod
    def from_config(cls, config: NoOpCondenserConfig) -> NoOpCondenser:
        """
        从配置对象创建NoOpCondenser实例。

        由于NoOpCondenser不需要任何配置参数，这个方法简单地创建
        并返回一个新的NoOpCondenser实例。

        Args:
            config (NoOpCondenserConfig): 配置对象（在此实现中未使用）

        Returns:
            NoOpCondenser: 新创建的NoOpCondenser实例
        """
        return NoOpCondenser()


# 注册配置类，使得可以通过配置系统创建该Condenser
NoOpCondenser.register_config(NoOpCondenserConfig)
