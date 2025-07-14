"""tenacity重试机制的自定义停止条件模块。

此模块提供了基于OpenHands关闭监听器的自定义停止条件，用于在系统
需要退出时停止重试循环。
"""

from tenacity import RetryCallState
from tenacity.stop import stop_base

from openhands.utils.shutdown_listener import should_exit


class stop_if_should_exit(stop_base):
    """如果should_exit标志被设置则停止重试的条件类。
    
    这个类继承自tenacity.stop.stop_base，提供了一个自定义的停止条件。
    当OpenHands系统需要退出时（通过should_exit()函数检测），此条件会
    指示tenacity停止重试操作。这确保了重试循环能够响应系统的关闭信号。
    
    Attributes:
        继承自stop_base的所有属性
    """

    def __call__(self, retry_state: 'RetryCallState') -> bool:
        """检查是否应该停止重试。

        这个方法是tenacity停止条件的核心接口。它会被tenacity框架
        在每次重试之前调用，以确定是否应该停止重试循环。

        Args:
            retry_state (RetryCallState): tenacity提供的重试状态对象，
                包含了重试的历史信息，如重试次数、已经过的时间等。

        Returns:
            bool: 如果should_exit()返回True（系统需要退出），则返回True
                停止重试；否则返回False继续重试。
        """
        # 调用OpenHands的关闭监听器，检查系统是否需要退出
        return should_exit()
