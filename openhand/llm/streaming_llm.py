"""
流式LLM模块

该模块提供了流式LLM（大语言Model）功能，继承自AsyncLLM类。
支持实时流式响应，允许在生成过程中逐步获取结果，并支持用户取消操作。
"""

import asyncio  # 导入异步IO模块
from functools import partial  # 导入partial函数，用于创建偏函数
from typing import Any, Callable  # 导入类型注解模块

from openhands.core.exceptions import UserCancelledError  # 导入用户取消异常
from openhands.core.logger import openhands_logger as logger  # 导入日志记录器
from openhands.llm.async_llm import LLM_RETRY_EXCEPTIONS, AsyncLLM  # 导入LLM重试异常和异步LLM基类
from openhands.llm.llm import REASONING_EFFORT_SUPPORTED_MODELS  # 导入支持推理努力的Model列表


class StreamingLLM(AsyncLLM):
    """
    流式LLM类
    
    继承自AsyncLLM，提供流式响应功能。支持实时获取生成结果，
    用户可以在生成过程中看到逐步输出，也可以随时取消操作。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        初始化StreamingLLM实例
        
        Args:
            *args: 传递给父类的位置参数
            **kwargs: 传递给父类的关键字参数
        """
        # 调用父类构造函数
        super().__init__(*args, **kwargs)

        # 创建异步流式completion的偏函数，预设所有必要的配置参数
        self._async_streaming_completion = partial(
            self._call_acompletion,  # 调用异步completion方法
            model=self.config.model,  # Model名称
            api_key=self.config.api_key.get_secret_value()  # API密钥（如果存在）
            if self.config.api_key
            else None,
            base_url=self.config.base_url,  # API基础URL
            api_version=self.config.api_version,  # API版本
            custom_llm_provider=self.config.custom_llm_provider,  # 自定义LLM提供商
            max_tokens=self.config.max_output_tokens,  # 最大输出Token数
            timeout=self.config.timeout,  # 超时时间
            temperature=self.config.temperature,  # 温度参数
            top_p=self.config.top_p,  # top_p参数
            drop_params=self.config.drop_params,  # 丢弃参数配置
            stream=True,  # 确保启用流式输出
        )

        # 保存未包装的异步流式completion函数引用
        async_streaming_completion_unwrapped = self._async_streaming_completion

        # 使用重试装饰器包装异步流式completion函数
        @self.retry_decorator(
            num_retries=self.config.num_retries,  # 重试次数
            retry_exceptions=LLM_RETRY_EXCEPTIONS,  # 重试异常类型
            retry_min_wait=self.config.retry_min_wait,  # 最小等待时间
            retry_max_wait=self.config.retry_max_wait,  # 最大等待时间
            retry_multiplier=self.config.retry_multiplier,  # 等待时间乘数
        )
        async def async_streaming_completion_wrapper(*args: Any, **kwargs: Any) -> Any:
            """
            异步流式completion包装函数
            
            处理参数解析、消息验证、推理努力设置、日志记录、
            流式响应处理和用户取消检查等功能。
            
            Args:
                *args: 位置参数，可能包含model和messages
                **kwargs: 关键字参数
                
            Yields:
                dict: 流式响应的数据块
                
            Raises:
                ValueError: 当messages列表为空时
                UserCancelledError: 当用户取消请求时
            """
            messages: list[dict[str, Any]] | dict[str, Any] = []

            # 某些调用者可能直接发送model和messages
            # litellm允许位置参数，如completion(model, messages, **kwargs)
            # 详见llm.py了解更多细节
            if len(args) > 1:
                messages = args[1] if len(args) > 1 else args[0]  # 获取messages参数
                kwargs['messages'] = messages  # 将messages添加到kwargs中

                # 移除前面的参数，它们已经在kwargs中发送
                args = args[2:]
            elif 'messages' in kwargs:
                messages = kwargs['messages']  # 从kwargs中获取messages

            # 确保我们处理的是消息列表
            messages = messages if isinstance(messages, list) else [messages]

            # 如果没有消息，说明出现了严重错误
            if not messages:
                raise ValueError(
                    '消息列表为空。至少需要一条消息。'
                )

            # 为支持推理努力的Model设置reasoning_effort参数
            if self.config.model.lower() in REASONING_EFFORT_SUPPORTED_MODELS:
                kwargs['reasoning_effort'] = self.config.reasoning_effort

            # 记录提示词到日志
            self.log_prompt(messages)

            try:
                # 直接调用并等待litellm_acompletion
                resp = await async_streaming_completion_unwrapped(*args, **kwargs)

                # 对于流式响应，我们遍历数据块
                async for chunk in resp:
                    # 在产生数据块之前检查是否被取消
                    if (
                        hasattr(self.config, 'on_cancel_requested_fn')
                        and self.config.on_cancel_requested_fn is not None
                        and await self.config.on_cancel_requested_fn()
                    ):
                        raise UserCancelledError(
                            '由于CANCELLED状态，LLM请求被取消'
                        )
                    
                    # 对于流式响应，使用"delta"而不是"message"！
                    message_back = chunk['choices'][0]['delta'].get('content', '')
                    if message_back:
                        self.log_response(message_back)  # 记录响应内容到日志
                    
                    # 执行completion后处理
                    self._post_completion(chunk)

                    # 产生数据块给调用者
                    yield chunk

            except UserCancelledError:
                logger.debug('LLM请求被用户取消。')
                raise  # 重新抛出用户取消异常
            except Exception as e:
                logger.error(f'Completion错误发生：\n{e}')
                raise  # 重新抛出其他异常

            finally:
                # 休眠0.1秒以允许流被刷新
                if kwargs.get('stream', False):
                    await asyncio.sleep(0.1)

        # 将包装后的函数赋值给实例属性
        self._async_streaming_completion = async_streaming_completion_wrapper

    @property
    def async_streaming_completion(self) -> Callable:
        """
        异步litellm acompletion函数的流式装饰器属性
        
        Returns:
            Callable: 配置了流式功能的异步completion函数
        """
        return self._async_streaming_completion