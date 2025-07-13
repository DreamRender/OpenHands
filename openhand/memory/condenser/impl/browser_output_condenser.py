from __future__ import annotations

# 导入相关依赖模块
from openhands.core.config.condenser_config import BrowserOutputCondenserConfig
from openhands.events.event import Event
from openhands.events.observation import BrowserOutputObservation
from openhands.events.observation.agent import AgentCondensationObservation
from openhands.memory.condenser.condenser import Condensation, Condenser, View


class BrowserOutputCondenser(Condenser):
    """浏览器输出压缩器类

    这是一个用于屏蔽最近注意力窗口外的浏览器输出观察数据的压缩器。

    设计意图是只屏蔽浏览器输出，而保持其他所有内容不变。这一点很重要，因为当前我们
    将截图和可访问性树作为浏览器观察的输入提供给模型。这些数据非常庞大，消耗大量token，
    但对性能没有任何好处。因此，我们希望屏蔽所有之前timestamps的此类观察数据，只在上下文中
    保留最新的一个。

    继承自:
        Condenser: 基础压缩器类
    """

    def __init__(self, attention_window: int = 1):
        """初始化浏览器输出压缩器

        Args:
            attention_window (int): 注意力窗口大小，默认为1。
                                  表示保留最近多少个浏览器输出观察不被屏蔽
        """
        # 设置注意力窗口大小，决定保留多少个最近的浏览器输出
        self.attention_window = attention_window
        # 调用父类初始化方法
        super().__init__()

    def condense(self, view: View) -> View | Condensation:
        """压缩处理方法

        将注意力窗口外的浏览器观察内容替换为占位符。
        该方法遍历事件列表，对超出注意力窗口的BrowserOutputObservation进行压缩处理。

        Args:
            view (View): 包含事件序列的视图对象

        Returns:
            View | Condensation: 处理后的视图对象，其中超出注意力窗口的浏览器输出
                               被替换为简化的AgentCondensationObservation
        """
        # 存储处理结果的事件列表
        results: list[Event] = []
        # 计数器，用于跟踪已处理的浏览器输出观察数量
        cnt: int = 0

        # 反向遍历视图中的事件（从最新到最旧）
        for event in reversed(view):
            # 检查当前事件是否为浏览器输出观察，且已超出注意力窗口
            if (
                    isinstance(event, BrowserOutputObservation)
                    and cnt >= self.attention_window
            ):
                # 创建压缩观察对象，用简化信息替换原始浏览器输出
                # 只保留访问的URL信息，省略具体内容以节省token
                results.append(
                    AgentCondensationObservation(
                        f'Visited URL {event.url}\nContent omitted'
                    )
                )
            else:
                # 对于注意力窗口内的事件或非浏览器输出事件，直接保留
                results.append(event)
                # 如果是浏览器输出观察，增加计数器
                if isinstance(event, BrowserOutputObservation):
                    cnt += 1

        # 将结果列表重新反转，恢复原始时间顺序，并返回新的View对象
        return View(events=list(reversed(results)))

    @classmethod
    def from_config(
            cls, config: BrowserOutputCondenserConfig
    ) -> BrowserOutputCondenser:
        """从配置对象创建BrowserOutputCondenser实例

        这是一个类方法，用于根据配置对象创建压缩器实例。

        Args:
            config (BrowserOutputCondenserConfig): 浏览器输出压缩器配置对象

        Returns:
            BrowserOutputCondenser: 根据配置创建的压缩器实例
        """
        # 使用配置对象的参数创建实例，排除'type'字段
        # model_dump()方法将配置对象转换为字典，exclude参数排除指定字段
        return BrowserOutputCondenser(**config.model_dump(exclude={'type'}))


# 将BrowserOutputCondenserConfig注册到BrowserOutputCondenser类
# 这使得系统能够识别和使用相应的配置类型
BrowserOutputCondenser.register_config(BrowserOutputCondenserConfig)
