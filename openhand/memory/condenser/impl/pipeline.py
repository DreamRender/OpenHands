from __future__ import annotations

from contextlib import contextmanager

from openhands.controller.state.state import State
from openhands.core.config.condenser_config import CondenserPipelineConfig
from openhands.memory.condenser.condenser import Condensation, Condenser
from openhands.memory.view import View


class CondenserPipeline(Condenser):
    """
    将多个Condenser组合成单个Condenser的管道。

    这对于创建可以链式连接的Condenser管道非常有用，以实现非常特定的压缩目标。
    每个Condenser按顺序运行，将一个的输出View传递给下一个，直到到达管道末尾
    或返回CondensationAction为止。

    Attributes:
        condensers (list[Condenser]): 管道中的Condenser列表，按执行顺序排列
    """

    def __init__(self, *condenser: Condenser) -> None:
        """
        初始化CondenserPipeline。

        Args:
            *condenser (Condenser): 可变数量的Condenser实例，将按顺序执行
        """
        # 将传入的Condenser转换为列表存储
        self.condensers = list(condenser)

        # 调用父类构造函数
        super().__init__()

    @contextmanager
    def metadata_batch(self, state: State):
        """
        管理metadata批处理的上下文管理器。

        由于我们没有将State对象传递给管道中的每个步骤，我们需要在管道
        执行完成后遍历管道并手动收集相关的metadata。

        Args:
            state (State): 用于写入metadata的State对象

        Yields:
            None: 上下文管理器，在finally块中收集metadata
        """
        try:
            yield
        finally:
            # 父类假设metadata存储在"调用的Condenser"中
            # 由于我们没有将State线程化传递给管道中的每个步骤，
            # 我们需要遍历管道并手动收集相关的metadata
            for condenser in self.condensers:
                condenser.write_metadata(state)

    def condense(self, view: View) -> View | Condensation:
        """
        执行管道中的所有Condenser。

        按顺序执行管道中的每个Condenser，将前一个的输出作为下一个的输入。
        如果任何Condenser返回Condensation对象，则停止执行并返回该结果。

        Args:
            view (View): 初始输入的事件View

        Returns:
            View | Condensation: 最终的压缩结果，可能是View或Condensation对象
        """
        # 初始结果为输入的view
        result: View | Condensation = view

        # 遍历管道中的每个Condenser
        for condenser in self.condensers:
            # 执行当前Condenser的压缩操作
            result = condenser.condense(result)

            # 如果返回Condensation对象，则停止管道执行
            # 这表示某个Condenser已经执行了实际的压缩操作
            if isinstance(result, Condensation):
                break

        return result

    @classmethod
    def from_config(cls, config: CondenserPipelineConfig) -> CondenserPipeline:
        """
        从配置对象创建CondenserPipeline实例。

        Args:
            config (CondenserPipelineConfig): 包含管道配置的配置对象

        Returns:
            CondenserPipeline: 新创建的管道实例
        """
        # 从配置中创建所有Condenser实例
        condensers = [Condenser.from_config(c) for c in config.condensers]

        # 创建并返回管道实例
        return CondenserPipeline(*condensers)


# 注册配置类，使得可以通过配置系统创建该Condenser
CondenserPipeline.register_config(CondenserPipelineConfig)
