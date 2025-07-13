from tenacity import RetryCallState
from tenacity.stop import stop_base

from openhands.utils.shutdown_listener import should_exit


class stop_if_should_exit(stop_base):
    """当should_exit标志被设置时停止重试的条件类。
    
    这是一个自定义的tenacity停止条件，用于在系统需要关闭时
    立即停止正在进行的重试操作。这确保了当接收到关闭信号时，
    所有重试循环都能及时响应并停止执行。
    
    继承自tenacity.stop.stop_base基类，实现了特定的停止逻辑。
    """

    def __call__(self, retry_state: 'RetryCallState') -> bool:
        """检查是否应该停止重试。

        这是tenacity框架要求实现的方法，用于判断重试是否应该停止。
        当系统接收到关闭信号时，此方法会返回True，指示重试应该立即停止。

        Args:
            retry_state (RetryCallState): tenacity框架提供的重试状态对象，
                                        包含当前重试的相关信息（如重试次数、异常等）

        Returns:
            bool: 如果should_exit()返回True（即系统需要关闭），则返回True停止重试；
                 否则返回False继续重试

        Note:
            此方法通过调用should_exit()函数来检查系统是否接收到关闭信号，
            这样可以确保重试操作能够优雅地响应系统关闭请求。
        """
        return bool(should_exit())
