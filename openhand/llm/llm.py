import copy
import os
import time
import warnings
from functools import partial
from typing import Any, Callable

import httpx

from openhands.core.config import LLMConfig

# 忽略warnings以避免在导入litellm时出现不必要的警告信息
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    import litellm

from litellm import ChatCompletionMessageToolCall, ModelInfo, PromptTokensDetails
from litellm import Message as LiteLLMMessage
from litellm import completion as litellm_completion
from litellm import completion_cost as litellm_completion_cost
from litellm.exceptions import (
    RateLimitError,
    ServiceUnavailableError,
)
from litellm.types.utils import CostPerToken, ModelResponse, Usage
from litellm.utils import create_pretrained_tokenizer

from openhands.core.exceptions import LLMNoResponseError
from openhands.core.logger import openhands_logger as logger
from openhands.core.message import Message
from openhands.llm.debug_mixin import DebugMixin
from openhands.llm.fn_call_converter import (
    STOP_WORDS,
    convert_fncall_messages_to_non_fncall_messages,
    convert_non_fncall_messages_to_fncall_messages,
)
from openhands.llm.metrics import Metrics
from openhands.llm.retry_mixin import RetryMixin

__all__ = ['LLM']

# LLM重试异常元组：定义在出现以下异常时需要重试的异常类型
LLM_RETRY_EXCEPTIONS: tuple[type[Exception], ...] = (
    RateLimitError,            # 速率限制错误
    ServiceUnavailableError,   # 服务不可用错误
    litellm.Timeout,           # 超时错误
    litellm.InternalServerError,  # 内部服务器错误
    LLMNoResponseError,        # LLM无响应错误
)

# 支持提示缓存的Model列表
# 注意：当gemini和deepseek支持后需要移除此限制
CACHE_PROMPT_SUPPORTED_MODELS = [
    'claude-3-7-sonnet-20250219',
    'claude-sonnet-3-7-latest',
    'claude-3.7-sonnet',
    'claude-3-5-sonnet-20241022',
    'claude-3-5-sonnet-20240620',
    'claude-3-5-haiku-20241022',
    'claude-3-haiku-20240307',
    'claude-3-opus-20240229',
    'claude-sonnet-4-20250514',
    'claude-opus-4-20250514',
]

# 支持函数调用的Model列表
FUNCTION_CALLING_SUPPORTED_MODELS = [
    'claude-3-7-sonnet-20250219',
    'claude-sonnet-3-7-latest',
    'claude-3-5-sonnet',
    'claude-3-5-sonnet-20240620',
    'claude-3-5-sonnet-20241022',
    'claude-3.5-haiku',
    'claude-3-5-haiku-20241022',
    'claude-sonnet-4-20250514',
    'claude-opus-4-20250514',
    'gpt-4o-mini',
    'gpt-4o',
    'o1-2024-12-17',
    'o3-mini-2025-01-31',
    'o3-mini',
    'o3',
    'o3-2025-04-16',
    'o4-mini',
    'o4-mini-2025-04-16',
    'gemini-2.5-pro',
    'gpt-4.1',
]

# 支持推理努力参数的Model列表
REASONING_EFFORT_SUPPORTED_MODELS = [
    'o1-2024-12-17',
    'o1',
    'o3',
    'o3-2025-04-16',
    'o3-mini-2025-01-31',
    'o3-mini',
    'o4-mini',
    'o4-mini-2025-04-16',
    'gemini-2.5-flash',
    'gemini-2.5-pro',
]

# 不支持停止词的Model列表
MODELS_WITHOUT_STOP_WORDS = [
    'o1-mini',
    'o1-preview',
    'o1',
    'o1-2024-12-17',
]


class LLM(RetryMixin, DebugMixin):
    """LLM类代表一个语言Model实例。
    
    该类封装了与各种语言Model的交互功能，包括：
    - 支持多种Model提供商（OpenAI、Anthropic、Google等）
    - 函数调用能力
    - 视觉处理能力
    - 提示缓存
    - 重试机制
    - 成本计算和指标追踪
    
    Attributes:
        config (LLMConfig): 指定LLM配置的LLMConfig对象
        metrics (Metrics): 用于追踪使用指标的对象
        model_info (ModelInfo | None): Model信息，包含Model的能力和限制
        cost_metric_supported (bool): 是否支持成本指标计算
        tokenizer: 自定义tokenizer实例（如果配置了的话）
    """

    def __init__(
        self,
        config: LLMConfig,
        metrics: Metrics | None = None,
        retry_listener: Callable[[int, int], None] | None = None,
    ) -> None:
        """初始化LLM实例。
        
        如果传递了LLMConfig，其值将作为后备配置。
        直接传递的简单参数总是会覆盖配置中的值。

        Args:
            config (LLMConfig): LLM配置对象
            metrics (Metrics | None): 要使用的指标对象，如果为None则创建新的
            retry_listener (Callable[[int, int], None] | None): 重试监听器回调函数
        """
        # 标记是否已尝试获取Model信息
        self._tried_model_info = False
        
        # 初始化指标对象，如果没有提供则创建新的
        self.metrics: Metrics = (
            metrics if metrics is not None else Metrics(model_name=config.model)
        )
        
        # 标记是否支持成本指标计算
        self.cost_metric_supported: bool = True
        
        # 深度复制配置以避免外部修改影响
        self.config: LLMConfig = copy.deepcopy(config)

        # Model信息，用于存储Model的能力和限制
        self.model_info: ModelInfo | None = None
        
        # 重试监听器
        self.retry_listener = retry_listener
        
        # 如果启用了完成日志记录，确保日志文件夹存在
        if self.config.log_completions:
            if self.config.log_completions_folder is None:
                raise RuntimeError(
                    'log_completions_folder is required when log_completions is enabled'
                )
            os.makedirs(self.config.log_completions_folder, exist_ok=True)

        # 调用init_model_info来初始化config.max_output_tokens
        # 这在偏函数中使用
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            self.init_model_info()
            
        # 记录Model的各种能力状态
        if self.vision_is_active():
            logger.debug('LLM: model has vision enabled')
        if self.is_caching_prompt_active():
            logger.debug('LLM: caching prompt enabled')
        if self.is_function_calling_active():
            logger.debug('LLM: model supports function calling')

        # 如果使用自定义tokenizer，确保其已加载并以litellm期望的格式可访问
        if self.config.custom_tokenizer is not None:
            self.tokenizer = create_pretrained_tokenizer(self.config.custom_tokenizer)
        else:
            self.tokenizer = None

        # 设置完成函数的基础参数
        kwargs: dict[str, Any] = {
            'temperature': self.config.temperature,
            'max_completion_tokens': self.config.max_output_tokens,
        }
        
        # 如果配置了top_k参数，添加到kwargs中
        # 注意：openai不暴露top_k参数，litellm会以不同于openai兼容参数的方式处理
        if self.config.top_k is not None:
            kwargs['top_k'] = self.config.top_k

        # 为支持推理努力的Model添加相关参数
        if (
            self.config.model.lower() in REASONING_EFFORT_SUPPORTED_MODELS
            or self.config.model.split('/')[-1] in REASONING_EFFORT_SUPPORTED_MODELS
        ):
            kwargs['reasoning_effort'] = self.config.reasoning_effort
            # 推理Model不支持temperature参数
            kwargs.pop('temperature')
            
        # Azure问题修复：https://github.com/All-Hands-AI/OpenHands/issues/6777
        if self.config.model.startswith('azure'):
            kwargs['max_tokens'] = self.config.max_output_tokens
            kwargs.pop('max_completion_tokens')

        # 为支持安全设置的Model添加安全设置
        if 'mistral' in self.config.model.lower() and self.config.safety_settings:
            kwargs['safety_settings'] = self.config.safety_settings
        elif 'gemini' in self.config.model.lower() and self.config.safety_settings:
            kwargs['safety_settings'] = self.config.safety_settings

        # 创建litellm completion的偏函数，预设常用参数
        self._completion = partial(
            litellm_completion,
            model=self.config.model,
            api_key=self.config.api_key.get_secret_value()
            if self.config.api_key
            else None,
            base_url=self.config.base_url,
            api_version=self.config.api_version,
            custom_llm_provider=self.config.custom_llm_provider,
            timeout=self.config.timeout,
            top_p=self.config.top_p,
            drop_params=self.config.drop_params,
            seed=self.config.seed,
            **kwargs,
        )

        # 保存未包装的completion函数引用
        self._completion_unwrapped = self._completion

        # 使用重试装饰器包装completion函数
        @self.retry_decorator(
            num_retries=self.config.num_retries,
            retry_exceptions=LLM_RETRY_EXCEPTIONS,
            retry_min_wait=self.config.retry_min_wait,
            retry_max_wait=self.config.retry_max_wait,
            retry_multiplier=self.config.retry_multiplier,
            retry_listener=self.retry_listener,
        )
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            """litellm completion函数的包装器。
            
            记录completion函数的输入和输出，处理函数调用转换，
            计算成本和延迟指标。
            
            Args:
                *args: 位置参数
                **kwargs: 关键字参数
                
            Returns:
                ModelResponse: litellm的Model响应对象
                
            Raises:
                ValueError: 当消息列表为空时
                LLMNoResponseError: 当响应选择少于1个时
            """
            from openhands.io import json

            # 提取消息参数
            messages_kwarg: list[dict[str, Any]] | dict[str, Any] = []
            mock_function_calling = not self.is_function_calling_active()

            # 一些调用者可能直接发送model和messages
            # litellm允许位置参数，如completion(model, messages, **kwargs)
            if len(args) > 1:
                # 如果提供了第一个参数则忽略它（那会是model）
                # 设计上：我们不允许覆盖配置的值
                # 实现上：偏函数已经将model设置为kwarg
                messages_kwarg = args[1] if len(args) > 1 else args[0]
                kwargs['messages'] = messages_kwarg

                # 移除前面的参数，它们在kwargs中发送
                args = args[2:]
            elif 'messages' in kwargs:
                messages_kwarg = kwargs['messages']

            # 确保我们使用消息列表
            messages: list[dict[str, Any]] = (
                messages_kwarg if isinstance(messages_kwarg, list) else [messages_kwarg]
            )

            # 如果需要，处理转换为非函数调用消息
            original_fncall_messages = copy.deepcopy(messages)
            mock_fncall_tools = None
            
            # 如果Agent或调用者定义了工具，并且我们通过提示模拟，转换消息
            if mock_function_calling and 'tools' in kwargs:
                add_in_context_learning_example = True
                # 某些Model不需要上下文学习示例
                if (
                    'openhands-lm' in self.config.model
                    or 'devstral' in self.config.model
                ):
                    add_in_context_learning_example = False

                # 转换函数调用消息为非函数调用格式
                messages = convert_fncall_messages_to_non_fncall_messages(
                    messages,
                    kwargs['tools'],
                    add_in_context_learning_example=add_in_context_learning_example,
                )
                kwargs['messages'] = messages

                # 如果Model支持，添加停止词
                if self.config.model not in MODELS_WITHOUT_STOP_WORDS:
                    kwargs['stop'] = STOP_WORDS

                mock_fncall_tools = kwargs.pop('tools')
                if 'openhands-lm' in self.config.model:
                    # 如果没有这个，在使用SGLang为openhands-lm提供服务时可能会遇到问题
                    kwargs['tool_choice'] = 'none'
                else:
                    # 模拟函数调用时不应指定tool_choice
                    kwargs.pop('tool_choice', None)

            # 如果没有消息，说明出了严重问题
            if not messages:
                raise ValueError(
                    'The messages list is empty. At least one message is required.'
                )

            # 记录整个LLM提示
            self.log_prompt(messages)

            # 将litellm modify_params设置为配置的值
            # 默认为True，允许litellm进行转换，如在消息为空时添加默认消息
            # 注意：此设置是全局的；与drop_params不同，它不能在litellm completion偏函数中覆盖
            litellm.modify_params = self.config.modify_params

            # 如果不使用litellm proxy，移除extra_body
            if 'litellm_proxy' not in self.config.model:
                kwargs.pop('extra_body', None)

            # 记录开始时间以测量延迟
            start_time = time.time()
            
            # 我们这里不支持流式传输，因此我们得到一个ModelResponse
            # 在LiteLLM调用期间抑制httpx弃用警告
            # 这可以防止在LiteLLM向LLM提供商发出HTTP请求时出现
            # "Use 'content=<...>' to upload raw bytes/text content"警告
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    'ignore', category=DeprecationWarning, module='httpx.*'
                )
                warnings.filterwarnings(
                    'ignore',
                    message=r'.*content=.*upload.*',
                    category=DeprecationWarning,
                )
                resp: ModelResponse = self._completion_unwrapped(*args, **kwargs)

            # 计算并记录延迟
            latency = time.time() - start_time
            response_id = resp.get('id', 'unknown')
            self.metrics.add_response_latency(latency, response_id)

            # 保存非函数调用响应的副本
            non_fncall_response = copy.deepcopy(resp)

            # 如果我们模拟了函数调用，并且有工具，将响应转换回函数调用格式
            if mock_function_calling and mock_fncall_tools is not None:
                if len(resp.choices) < 1:
                    raise LLMNoResponseError(
                        'Response choices is less than 1 - This is only seen in Gemini models so far. Response: '
                        + str(resp)
                    )

                non_fncall_response_message = resp.choices[0].message
                # 将非函数调用消息转换为函数调用格式
                fn_call_messages_with_response = (
                    convert_non_fncall_messages_to_fncall_messages(
                        messages + [non_fncall_response_message], mock_fncall_tools
                    )
                )
                fn_call_response_message = fn_call_messages_with_response[-1]
                if not isinstance(fn_call_response_message, LiteLLMMessage):
                    fn_call_response_message = LiteLLMMessage(
                        **fn_call_response_message
                    )
                resp.choices[0].message = fn_call_response_message

            # 检查响应是否有'choices'键且至少有一个项目
            if not resp.get('choices') or len(resp['choices']) < 1:
                raise LLMNoResponseError(
                    'Response choices is less than 1 - This is only seen in Gemini models so far. Response: '
                    + str(resp)
                )

            # 提取响应消息内容
            message_back: str = resp['choices'][0]['message']['content'] or ''
            tool_calls: list[ChatCompletionMessageToolCall] = resp['choices'][0][
                'message'
            ].get('tool_calls', [])
            
            # 如果有工具调用，添加到消息中
            if tool_calls:
                for tool_call in tool_calls:
                    fn_name = tool_call.function.name
                    fn_args = tool_call.function.arguments
                    message_back += f'\nFunction call: {fn_name}({fn_args})'

            # 记录LLM响应
            self.log_response(message_back)

            # 先后处理响应以计算成本
            cost = self._post_completion(resp)

            # 为评估或其他需要原始完成的脚本记录日志
            if self.config.log_completions:
                assert self.config.log_completions_folder is not None
                log_file = os.path.join(
                    self.config.log_completions_folder,
                    # 使用指标Model名称（用于draft editor）
                    f'{self.metrics.model_name.replace("/", "__")}-{time.time()}.json',
                )

                # 设置要记录的字典
                _d = {
                    'messages': messages,
                    'response': resp,
                    'args': args,
                    'kwargs': {
                        k: v
                        for k, v in kwargs.items()
                        if k not in ('messages', 'client')
                    },
                    'timestamp': time.time(),
                    'cost': cost,
                }

                # 如果是非原生函数调用，分别保存消息/响应
                if mock_function_calling:
                    # 覆盖响应为非函数调用以与消息保持一致
                    _d['response'] = non_fncall_response

                    # 分别保存函数调用消息/响应
                    _d['fncall_messages'] = original_fncall_messages
                    _d['fncall_response'] = resp
                    
                # 写入日志文件
                with open(log_file, 'w') as f:
                    f.write(json.dumps(_d))

            return resp

        # 用包装器替换completion函数
        self._completion = wrapper

    @property
    def completion(self) -> Callable:
        """litellm completion函数的装饰器属性。

        查看完整文档：https://litellm.vercel.app/docs/completion
        
        Returns:
            Callable: 被装饰的completion函数
        """
        return self._completion

    def init_model_info(self) -> None:
        """初始化Model信息。
        
        尝试从各种来源获取Model信息，包括：
        - OpenRouter的Model信息API
        - LiteLLM代理的Model信息
        - LiteLLM的内置Model信息
        
        根据获取的信息设置max_input_tokens和max_output_tokens等配置。
        """
        # 如果已经尝试过获取Model信息，直接返回
        if self._tried_model_info:
            return
        self._tried_model_info = True
        
        # 尝试从OpenRouter获取Model信息
        try:
            if self.config.model.startswith('openrouter'):
                self.model_info = litellm.get_model_info(self.config.model)
        except Exception as e:
            logger.debug(f'Error getting model info: {e}')

        # 如果使用LiteLLM代理，从LiteLLM代理获取Model信息
        if self.config.model.startswith('litellm_proxy/'):
            # 使用litellm_model_id作为路径参数向{base_url}/v1/model/info发送GET请求
            base_url = self.config.base_url.strip() if self.config.base_url else ''
            if not base_url.startswith(('http://', 'https://')):
                base_url = 'http://' + base_url

            response = httpx.get(
                f'{base_url}/v1/model/info',
                headers={
                    'Authorization': f'Bearer {self.config.api_key.get_secret_value() if self.config.api_key else None}'
                },
            )

            resp_json = response.json()
            if 'data' not in resp_json:
                logger.error(
                    f'Error getting model info from LiteLLM proxy: {resp_json}'
                )
            all_model_info = resp_json.get('data', [])
            current_model_info = next(
                (
                    info
                    for info in all_model_info
                    if info['model_name']
                    == self.config.model.removeprefix('litellm_proxy/')
                ),
                None,
            )
            if current_model_info:
                self.model_info = current_model_info['model_info']
                logger.debug(f'Got model info from litellm proxy: {self.model_info}')

        # 从Model名称获取Model信息的最后两次尝试
        if not self.model_info:
            try:
                # 尝试使用冒号前的Model名称
                self.model_info = litellm.get_model_info(
                    self.config.model.split(':')[0]
                )
            # noinspection PyBroadException
            except Exception:
                pass
                
        if not self.model_info:
            try:
                # 尝试使用斜杠后的Model名称
                self.model_info = litellm.get_model_info(
                    self.config.model.split('/')[-1]
                )
            # noinspection PyBroadException
            except Exception:
                pass
                
        # 记录Model信息
        from openhands.io import json
        logger.debug(
            f'Model info: {json.dumps({"model": self.config.model, "base_url": self.config.base_url}, indent=2)}'
        )

        # Hugging Face特殊处理
        if self.config.model.startswith('huggingface'):
            # HF不支持OpenAI的top_p默认值(1)
            logger.debug(
                f'Setting top_p to 0.9 for Hugging Face model: {self.config.model}'
            )
            self.config.top_p = 0.9 if self.config.top_p == 1 else self.config.top_p

        # 如果未明确设置，从Model信息设置max_input_tokens
        if (
            self.config.max_input_tokens is None
            and self.model_info is not None
            and 'max_input_tokens' in self.model_info
            and isinstance(self.model_info['max_input_tokens'], int)
        ):
            self.config.max_input_tokens = self.model_info['max_input_tokens']

        # 如果未明确设置，设置max_output_tokens
        if self.config.max_output_tokens is None:
            # Claude 3.7 Sonnet Model的特殊情况
            if any(
                model in self.config.model
                for model in ['claude-3-7-sonnet', 'claude-3.7-sonnet']
            ):
                # litellm将最大值设置为128k，但这需要设置头部
                self.config.max_output_tokens = 64000
            # 尝试从Model信息获取
            elif self.model_info is not None:
                # max_output_tokens优先于max_tokens
                if 'max_output_tokens' in self.model_info and isinstance(
                    self.model_info['max_output_tokens'], int
                ):
                    self.config.max_output_tokens = self.model_info['max_output_tokens']
                elif 'max_tokens' in self.model_info and isinstance(
                    self.model_info['max_tokens'], int
                ):
                    self.config.max_output_tokens = self.model_info['max_tokens']

        # 初始化函数调用能力
        # 检查Model名称是否在我们支持的列表中
        model_name_supported = (
            self.config.model in FUNCTION_CALLING_SUPPORTED_MODELS
            or self.config.model.split('/')[-1] in FUNCTION_CALLING_SUPPORTED_MODELS
            or any(m in self.config.model for m in FUNCTION_CALLING_SUPPORTED_MODELS)
        )

        # 处理用户定义的native_tool_calling配置
        if self.config.native_tool_calling is None:
            self._function_calling_active = model_name_supported
        else:
            self._function_calling_active = self.config.native_tool_calling

    def vision_is_active(self) -> bool:
        """检查视觉功能是否激活。
        
        Returns:
            bool: 如果视觉功能激活则返回True
        """
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            return not self.config.disable_vision and self._supports_vision()

    def _supports_vision(self) -> bool:
        """从litellm获取Model是否具有视觉能力。

        Returns:
            bool: 如果Model具有视觉能力则返回True。如果Model不被litellm支持则返回False。
        """
        # litellm.supports_vision目前对'openai/gpt-...'或'anthropic/claude-...'（带前缀）返回False
        # 但出于某种原因，model_info会有正确的值。
        # 我们可以使用它，但如果model_info对Vertex或其他提供商不正确，我们需要密切关注
        # 当litellm更新以修复https://github.com/BerriAI/litellm/issues/5608时移除
        # 检查完整Model名称和代理前缀后的名称的视觉支持
        return (
            litellm.supports_vision(self.config.model)
            or litellm.supports_vision(self.config.model.split('/')[-1])
            or (
                self.model_info is not None
                and self.model_info.get('supports_vision', False)
            )
        )

    def is_caching_prompt_active(self) -> bool:
        """检查当前Model是否支持并启用了提示缓存。

        Returns:
            bool: 如果给定Model支持并启用了提示缓存则返回True。
        """
        return (
            self.config.caching_prompt is True
            and (
                self.config.model in CACHE_PROMPT_SUPPORTED_MODELS
                or self.config.model.split('/')[-1] in CACHE_PROMPT_SUPPORTED_MODELS
            )
            # 我们不需要查找model_info，因为只有Anthropic Model需要显式缓存断点
        )

    def is_function_calling_active(self) -> bool:
        """返回此LLM实例是否支持并启用了函数调用。

        为了性能，结果在初始化期间被缓存。
        
        Returns:
            bool: 如果支持并启用了函数调用则返回True
        """
        return self._function_calling_active

    def _post_completion(self, response: ModelResponse) -> float:
        """后处理completion响应。

        记录completion调用的成本和使用统计信息。
        
        Args:
            response (ModelResponse): litellm的Model响应对象
            
        Returns:
            float: 此次调用的成本
        """
        # 尝试计算成本
        try:
            cur_cost = self._completion_cost(response)
        except Exception:
            cur_cost = 0

        # 构建统计信息字符串
        stats = ''
        if self.cost_metric_supported:
            # 跟踪成本
            stats = 'Cost: %.2f USD | Accumulated Cost: %.2f USD\n' % (
                cur_cost,
                self.metrics.accumulated_cost,
            )

        # 如果可用，将延迟添加到统计信息
        if self.metrics.response_latencies:
            latest_latency = self.metrics.response_latencies[-1]
            stats += 'Response Latency: %.3f seconds\n' % latest_latency.latency

        # 获取使用情况和响应ID
        usage: Usage | None = response.get('usage')
        response_id = response.get('id', 'unknown')

        if usage:
            # 跟踪输入和输出token
            prompt_tokens = usage.get('prompt_tokens', 0)
            completion_tokens = usage.get('completion_tokens', 0)

            if prompt_tokens:
                stats += 'Input tokens: ' + str(prompt_tokens)

            if completion_tokens:
                stats += (
                    (' | ' if prompt_tokens else '')
                    + 'Output tokens: '
                    + str(completion_tokens)
                    + '\n'
                )

            # 读取提示缓存命中（如果有）
            prompt_tokens_details: PromptTokensDetails = usage.get(
                'prompt_tokens_details'
            )
            cache_hit_tokens = (
                prompt_tokens_details.cached_tokens
                if prompt_tokens_details and prompt_tokens_details.cached_tokens
                else 0
            )
            if cache_hit_tokens:
                stats += 'Input tokens (cache hit): ' + str(cache_hit_tokens) + '\n'

            # 对于Anthropic，缓存写入与常规输入token的成本不同
            # 但litellm在使用统计中没有分开它们
            # 我们可以从提供商特定的额外字段中读取它
            model_extra = usage.get('model_extra', {})
            cache_write_tokens = model_extra.get('cache_creation_input_tokens', 0)
            if cache_write_tokens:
                stats += 'Input tokens (cache write): ' + str(cache_write_tokens) + '\n'

            # 从Model信息获取上下文窗口
            context_window = 0
            if self.model_info and 'max_input_tokens' in self.model_info:
                context_window = self.model_info['max_input_tokens']
                logger.debug(f'Using context window: {context_window}')

            # 记录在指标中
            # 我们将cache_hit_tokens视为"缓存读取"，cache_write_tokens视为"缓存写入"
            self.metrics.add_token_usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cache_read_tokens=cache_hit_tokens,
                cache_write_tokens=cache_write_tokens,
                context_window=context_window,
                response_id=response_id,
            )

        # 记录统计信息
        if stats:
            logger.debug(stats)

        return cur_cost

    def get_token_count(self, messages: list[dict] | list[Message]) -> int:
        """获取消息列表中的token数量。使用字典以获得更好的token计数。

        Args:
            messages (list): 消息列表，可以是字典列表或Message对象列表。
            
        Returns:
            int: token数量。
        """
        # 尝试将Message对象转换为字典，litellm期望字典
        if (
            isinstance(messages, list)
            and len(messages) > 0
            and isinstance(messages[0], Message)
        ):
            logger.info(
                'Message objects now include serialized tool calls in token counting'
            )
            # 断言format_messages_for_llm的预期类型
            assert isinstance(messages, list) and all(
                isinstance(m, Message) for m in messages
            ), 'Expected list of Message objects'

            # 我们已经断言messages是Message对象列表
            # 使用显式类型以满足mypy
            messages_typed: list[Message] = messages  # type: ignore
            messages = self.format_messages_for_llm(messages_typed)

        # 尝试使用默认的litellm tokenizer获取token计数
        # 或者如果为此LLM配置设置了自定义tokenizer
        try:
            return int(
                litellm.token_counter(
                    model=self.config.model,
                    messages=messages,
                    custom_tokenizer=self.tokenizer,
                )
            )
        except Exception as e:
            # 限制在不支持token计数的情况下的日志垃圾邮件
            logger.error(
                f'Error getting token count for\n model {self.config.model}\n{e}'
                + (
                    f'\ncustom_tokenizer: {self.config.custom_tokenizer}'
                    if self.config.custom_tokenizer is not None
                    else ''
                )
            )
            return 0

    def _is_local(self) -> bool:
        """确定系统是否使用本地运行的LLM。

        Returns:
            bool: 如果执行本地Model则返回True。
        """
        # 检查base_url是否包含本地地址
        if self.config.base_url is not None:
            for substring in ['localhost', '127.0.0.1', '0.0.0.0']:
                if substring in self.config.base_url:
                    return True
        # 检查是否为ollama Model
        elif self.config.model is not None:
            if self.config.model.startswith('ollama'):
                return True
        return False

    def _completion_cost(self, response: Any) -> float:
        """计算completion成本并用运行总数更新指标。

        根据Model计算completion响应的成本。本地Model被视为免费。
        将当前成本添加到指标中的总成本。

        Args:
            response: Model调用的响应。

        Returns:
            float: 响应的成本。
        """
        # 如果不支持成本指标，返回0
        if not self.cost_metric_supported:
            return 0.0

        # 准备额外的参数
        extra_kwargs = {}
        if (
            self.config.input_cost_per_token is not None
            and self.config.output_cost_per_token is not None
        ):
            cost_per_token = CostPerToken(
                input_cost_per_token=self.config.input_cost_per_token,
                output_cost_per_token=self.config.output_cost_per_token,
            )
            logger.debug(f'Using custom cost per token: {cost_per_token}')
            extra_kwargs['custom_cost_per_token'] = cost_per_token

        # 尝试直接从响应获取response_cost
        _hidden_params = getattr(response, '_hidden_params', {})
        cost = _hidden_params.get('additional_headers', {}).get(
            'llm_provider-x-litellm-response-cost', None
        )
        if cost is not None:
            cost = float(cost)
            logger.debug(f'Got response_cost from response: {cost}')

        try:
            # 如果没有从响应中获取到成本，使用litellm计算
            if cost is None:
                try:
                    cost = litellm_completion_cost(
                        completion_response=response, **extra_kwargs
                    )
                except Exception as e:
                    logger.debug(f'Error getting cost from litellm: {e}')

            # 如果仍然没有成本，尝试使用fallback Model名称
            if cost is None:
                _model_name = '/'.join(self.config.model.split('/')[1:])
                cost = litellm_completion_cost(
                    completion_response=response, model=_model_name, **extra_kwargs
                )
                logger.debug(
                    f'Using fallback model name {_model_name} to get cost: {cost}'
                )
            # 将成本添加到指标并返回
            self.metrics.add_cost(float(cost))
            return float(cost)
        except Exception:
            # 如果计算失败，标记不支持成本计算
            self.cost_metric_supported = False
            logger.debug('Cost calculation not supported for this model.')
        return 0.0

    def __str__(self) -> str:
        """返回LLM实例的字符串表示。
        
        Returns:
            str: LLM实例的描述字符串
        """
        if self.config.api_version:
            return f'LLM(model={self.config.model}, api_version={self.config.api_version}, base_url={self.config.base_url})'
        elif self.config.base_url:
            return f'LLM(model={self.config.model}, base_url={self.config.base_url})'
        return f'LLM(model={self.config.model})'

    def __repr__(self) -> str:
        """返回LLM实例的repr表示。
        
        Returns:
            str: LLM实例的repr字符串
        """
        return str(self)

    def format_messages_for_llm(self, messages: Message | list[Message]) -> list[dict]:
        """为LLM格式化消息。
        
        设置Message对象的各种能力标志，然后序列化为字典列表。
        
        Args:
            messages (Message | list[Message]): 要格式化的消息或消息列表
            
        Returns:
            list[dict]: 格式化后的消息字典列表
        """
        # 确保messages是列表
        if isinstance(messages, Message):
            messages = [messages]

        # 设置标志以了解如何序列化消息
        for message in messages:
            message.cache_enabled = self.is_caching_prompt_active()
            message.vision_enabled = self.vision_is_active()
            message.function_calling_enabled = self.is_function_calling_active()
            # deepseek Model的特殊处理
            if 'deepseek' in self.config.model:
                message.force_string_serializer = True

        # 让pydantic处理序列化
        return [message.model_dump() for message in messages]