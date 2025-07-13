"""
重试机制混入类模块

该模块提供了重试逻辑的混入类，主要用于处理LLM调用时的重试机制。
支持可定制的重试参数，包括重试次数、等待时间、重试条件等。
"""

from typing import Any, Callable  # 导入类型注解相关模块

# 导入tenacity库的重试相关功能
from tenacity import (
    retry,  # 重试装饰器
    retry_if_exception_type,  # 基于异常类型的重试条件
    stop_after_attempt,  # 基于尝试次数的停止条件
    wait_exponential,  # 指数退避等待策略
)

from openhands.core.exceptions import LLMNoResponseError  # 导入LLM无响应异常
from openhands.core.logger import openhands_logger as logger  # 导入日志记录器
from openhands.utils.tenacity_stop import stop_if_should_exit  # 导入退出条件检查函数


class RetryMixin:
    """
    重试逻辑混入类
    
    提供可复用的重试机制，主要用于LLM调用失败时的重试处理。
    支持自定义重试参数，如重试次数、等待时间、重试条件等。
    """

    def retry_decorator(self, **kwargs: Any) -> Callable:
        """
        创建一个可定制参数的LLM重试装饰器
        
        该装饰器用于处理429错误和LLM类中的其他一些异常。
        
        Args:
            **kwargs: 用于覆盖默认重试行为的关键字参数
                num_retries: 最大重试次数
                retry_exceptions: 需要重试的异常类型元组
                retry_min_wait: 最小等待时间
                retry_max_wait: 最大等待时间
                retry_multiplier: 等待时间乘数
                retry_listener: 重试监听器函数
                
        Returns:
            Callable: 配置了重试参数的重试装饰器
        """
        # 从kwargs中提取重试配置参数
        num_retries = kwargs.get('num_retries')  # 最大重试次数
        retry_exceptions: tuple = kwargs.get('retry_exceptions', ())  # 需要重试的异常类型
        retry_min_wait = kwargs.get('retry_min_wait')  # 最小等待时间
        retry_max_wait = kwargs.get('retry_max_wait')  # 最大等待时间
        retry_multiplier = kwargs.get('retry_multiplier')  # 等待时间乘数
        retry_listener = kwargs.get('retry_listener')  # 重试监听器

        def before_sleep(retry_state: Any) -> None:
            """
            重试前的回调函数
            
            Args:
                retry_state: tenacity重试状态对象
                
            Note:
                - 记录重试尝试
                - 调用重试监听器（如果存在）
                - 对LLMNoResponseError进行特殊处理
            """
            # 记录重试尝试
            self.log_retry_attempt(retry_state)
            
            # 如果存在重试监听器，则调用它
            if retry_listener:
                retry_listener(retry_state.attempt_number, num_retries)

            # 检查异常是否为LLMNoResponseError
            exception = retry_state.outcome.exception()
            if isinstance(exception, LLMNoResponseError):
                # 检查重试状态是否包含kwargs参数
                if hasattr(retry_state, 'kwargs'):
                    # 只有当temperature为0或未设置时才修改
                    current_temp = retry_state.kwargs.get('temperature', 0)
                    if current_temp == 0:
                        # 将temperature设置为1.0以增加随机性
                        retry_state.kwargs['temperature'] = 1.0
                        logger.warning(
                            '检测到LLMNoResponseError且temperature=0，将temperature设置为1.0进行下次尝试。'
                        )
                    else:
                        logger.warning(
                            f'检测到LLMNoResponseError且temperature={current_temp}，保持原始temperature值'
                        )

        # 创建重试装饰器
        retry_decorator: Callable = retry(
            before_sleep=before_sleep,  # 重试前执行的回调函数
            stop=stop_after_attempt(num_retries) | stop_if_should_exit(),  # 停止条件：达到最大重试次数或应该退出
            reraise=True,  # 重新抛出最后的异常
            retry=(
                retry_if_exception_type(retry_exceptions)  # 只对指定类型的异常进行重试
            ),
            wait=wait_exponential(  # 指数退避等待策略
                multiplier=retry_multiplier,  # 乘数
                min=retry_min_wait,  # 最小等待时间
                max=retry_max_wait,  # 最大等待时间
            ),
        )
        return retry_decorator

    def log_retry_attempt(self, retry_state: Any) -> None:
        """
        记录重试尝试信息
        
        Args:
            retry_state: tenacity重试状态对象
            
        Note:
            - 从重试状态中提取异常信息
            - 向异常对象添加重试信息（如果可能）
            - 记录错误日志
        """
        exception = retry_state.outcome.exception()

        # 向异常添加重试尝试次数和最大重试次数信息，供后续使用
        if hasattr(retry_state, 'retry_object') and hasattr(
            retry_state.retry_object, 'stop'
        ):
            # 从stop_after_attempt获取最大重试次数
            stop_condition = retry_state.retry_object.stop

            # 处理单个停止条件和stop_any（组合条件）
            stop_funcs = []
            if hasattr(stop_condition, 'stops'):
                # 这是一个包含多个停止条件的stop_any对象
                stop_funcs = stop_condition.stops
            else:
                # 这是单个停止条件
                stop_funcs = [stop_condition]

            # 遍历停止条件，寻找max_attempts属性
            for stop_func in stop_funcs:
                if hasattr(stop_func, 'max_attempts'):
                    # 向异常添加重试信息
                    exception.retry_attempt = retry_state.attempt_number
                    exception.max_retries = stop_func.max_attempts
                    break

        # 记录错误日志
        logger.error(
            f'{exception}. 尝试 #{retry_state.attempt_number} | 您可以在配置中自定义重试值。',
        )