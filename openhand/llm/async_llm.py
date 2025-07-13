import asyncio
from functools import partial
from typing import Any, Callable

from litellm import acompletion as litellm_acompletion

from openhands.core.exceptions import UserCancelledError
from openhands.core.logger import openhands_logger as logger
from openhands.llm.llm import (
    LLM,
    LLM_RETRY_EXCEPTIONS,
    REASONING_EFFORT_SUPPORTED_MODELS,
)
from openhands.utils.shutdown_listener import should_continue


class AsyncLLM(LLM):
    """
    异步LLM类，继承自基础LLM类。
    
    该类提供了异步调用大语言Model的功能，支持重试机制、
    用户取消操作、成本跟踪和日志记录等特性。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        初始化AsyncLLM实例。
        
        Args:
            *args: 传递给父类的位置参数
            **kwargs: 传递给父类的关键字参数
        """
        # 调用父类LLM的初始化方法
        super().__init__(*args, **kwargs)

        # 使用partial函数预设置异步完成调用的参数
        # 这样可以避免在每次调用时重复传递相同的配置参数
        self._async_completion = partial(
            self._call_acompletion,
            model=self.config.model,  # Model名称
            api_key=self.config.api_key.get_secret_value()  # API密钥
            if self.config.api_key
            else None,
            base_url=self.config.base_url,  # API基础URL
            api_version=self.config.api_version,  # API版本
            custom_llm_provider=self.config.custom_llm_provider,  # 自定义LLM提供商
            max_tokens=self.config.max_output_tokens,  # 最大输出token数
            timeout=self.config.timeout,  # 超时时间
            temperature=self.config.temperature,  # 温度参数（控制随机性）
            top_p=self.config.top_p,  # Top-p采样参数
            drop_params=self.config.drop_params,  # 需要丢弃的参数
            seed=self.config.seed,  # 随机种子
        )

        # 保存未包装的异步完成函数引用
        async_completion_unwrapped = self._async_completion

        # 使用装饰器为异步完成函数添加重试机制
        @self.retry_decorator(
            num_retries=self.config.num_retries,  # 重试次数
            retry_exceptions=LLM_RETRY_EXCEPTIONS,  # 需要重试的异常类型
            retry_min_wait=self.config.retry_min_wait,  # 重试最小等待时间
            retry_max_wait=self.config.retry_max_wait,  # 重试最大等待时间
            retry_multiplier=self.config.retry_multiplier,  # 重试等待时间倍数
        )
        async def async_completion_wrapper(*args: Any, **kwargs: Any) -> Any:
            """
            异步完成函数的包装器，添加了日志记录和成本跟踪功能。
            
            Args:
                *args: 位置参数，可能包含model和messages
                **kwargs: 关键字参数
                
            Returns:
                LLM的响应结果
                
            Raises:
                ValueError: 当消息列表为空时
                UserCancelledError: 当用户取消请求时
            """
            # 初始化消息列表
            messages: list[dict[str, Any]] | dict[str, Any] = []

            # 处理位置参数中的消息
            # 某些调用者可能直接发送model和messages作为位置参数
            # litellm允许位置参数，如completion(model, messages, **kwargs)
            # 详见llm.py中的更多说明
            if len(args) > 1:
                # 如果有多个位置参数，第二个是messages，第一个是model
                messages = args[1] if len(args) > 1 else args[0]
                kwargs['messages'] = messages

                # 移除前两个参数，它们已经通过kwargs发送
                args = args[2:]
            elif 'messages' in kwargs:
                # 如果messages在关键字参数中
                messages = kwargs['messages']

            # 为支持reasoning effort的模型设置reasoning_effort参数
            if self.config.model.lower() in REASONING_EFFORT_SUPPORTED_MODELS:
                kwargs['reasoning_effort'] = self.config.reasoning_effort

            # 确保我们处理的是消息列表
            messages = messages if isinstance(messages, list) else [messages]

            # 如果没有消息，说明出现了严重错误
            if not messages:
                raise ValueError(
                    'The messages list is empty. At least one message is required.'
                )

            # 记录发送给LLM的提示消息
            self.log_prompt(messages)

            async def check_stopped() -> None:
                """
                检查是否应该停止执行的异步函数。
                
                持续检查全局停止标志和用户取消请求，
                如果需要停止则退出循环。
                """
                # 持续检查是否应该继续执行
                while should_continue():
                    # 检查是否有用户取消请求的回调函数
                    if (
                        hasattr(self.config, 'on_cancel_requested_fn')
                        and self.config.on_cancel_requested_fn is not None
                        and await self.config.on_cancel_requested_fn()
                    ):
                        return
                    # 短暂休眠以避免过度占用CPU
                    await asyncio.sleep(0.1)

            # 创建停止检查任务
            stop_check_task = asyncio.create_task(check_stopped())

            try:
                # 直接调用并等待litellm异步完成函数
                resp = await async_completion_unwrapped(*args, **kwargs)

                # 从响应中提取消息内容
                message_back = resp['choices'][0]['message']['content']
                
                # 记录LLM的响应
                self.log_response(message_back)

                # 记录成本和使用的token数量
                self._post_completion(resp)

                # 该方法不支持流式传输，因此直接返回响应
                return resp

            except UserCancelledError:
                # 用户取消了LLM请求
                logger.debug('LLM request cancelled by user.')
                raise
            except Exception as e:
                # 记录其他异常
                logger.error(f'Completion Error occurred:\n{e}')
                raise

            finally:
                # 无论成功还是失败都要清理停止检查任务
                await asyncio.sleep(0.1)  # 短暂休眠
                stop_check_task.cancel()  # 取消任务
                try:
                    await stop_check_task  # 等待任务完成
                except asyncio.CancelledError:
                    # 忽略取消异常，这是预期的行为
                    pass

        # 将包装后的函数赋值给实例变量
        self._async_completion = async_completion_wrapper

    async def _call_acompletion(self, *args: Any, **kwargs: Any) -> Any:
        """
        litellm异步完成函数的包装器。
        
        这是对litellm_acompletion的简单包装，
        可能用于测试或其他特殊用途。
        
        Args:
            *args: 传递给litellm_acompletion的位置参数
            **kwargs: 传递给litellm_acompletion的关键字参数
            
        Returns:
            litellm_acompletion的返回结果
        """
        # 直接调用litellm的异步完成函数
        # 在测试中可能会被使用
        return await litellm_acompletion(*args, **kwargs)

    @property
    def async_completion(self) -> Callable:
        """
        异步litellm完成函数的装饰器属性。
        
        Returns:
            Callable: 配置好的异步完成函数
        """
        return self._async_completion