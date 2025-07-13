from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from openhands.core.logger import LOG_DIR
from openhands.core.logger import openhands_logger as logger


class LLMConfig(BaseModel):
    """LLM模型的配置类。

    这个类定义了与大语言模型交互所需的各种配置参数，包括API设置、
    重试策略、性能参数、成本控制等。支持多种LLM提供商和部署方式。

    Attributes:
        model: 要使用的模型名称
        api_key: 使用的API密钥
        base_url: API的基础URL。对于本地LLM这是必需的
        api_version: API的版本
        aws_access_key_id: AWS访问密钥ID
        aws_secret_access_key: AWS秘密访问密钥
        aws_region_name: AWS区域名称
        num_retries: 尝试重试的次数
        retry_multiplier: 指数退避的乘数
        retry_min_wait: 重试之间的最小等待时间（秒）。这是指数退避最小值。对于限制很低的模型，可以设置为15-20
        retry_max_wait: 重试之间的最大等待时间（秒）。这是指数退避最大值
        timeout: API的超时时间
        max_message_chars: 包含在LLM提示中的事件内容的大概最大字符数。较大的观察结果会被截断
        temperature: API的温度参数
        top_p: API的top p参数
        top_k: API的top k参数
        custom_llm_provider: 要使用的自定义LLM提供商。这在openhands中未文档化，通常不使用。在litellm端有文档
        max_input_tokens: 最大输入token数。注意这目前未使用，运行时的实际值是OpenAI中的总token数（例如GPT-4的128,000 token）
        max_output_tokens: 最大输出token数。这会发送给LLM
        input_cost_per_token: 每个输入token的成本。这将在日志中提供给用户检查
        output_cost_per_token: 每个输出token的成本。这将在日志中提供给用户检查
        ollama_base_url: OLLAMA API的基础URL
        drop_params: 丢弃任何未映射（不支持）的参数而不引起异常
        modify_params: 修改参数允许litellm进行转换，如在消息为空时添加默认消息
        disable_vision: 如果模型具有视觉能力，此选项允许禁用图像处理（有助于降低成本）
        caching_prompt: 如果LLM提供并且提供商支持，使用提示缓存功能
        log_completions: 是否将LLM完成结果记录到State
        log_completions_folder: 记录LLM完成结果的文件夹。如果log_completions为True则必需
        custom_tokenizer: 用于token计数的自定义tokenizer
        native_tool_calling: 如果模型支持，是否使用原生工具调用。可以是True、False或未设置
        reasoning_effort: 推理的努力程度。这是一个字符串，可以是'low'、'medium'、'high'或'none'之一。专用于o1模型
        seed: 用于LLM的种子值
        safety_settings: 支持安全设置的模型的安全设置（如Mistral AI和Gemini）
    """

    model: str = Field(default='claude-sonnet-4-20250514')
    """使用的模型名称，默认为Claude Sonnet 4"""
    
    api_key: SecretStr | None = Field(default=None)
    """API访问密钥，保密字符串类型"""
    
    base_url: str | None = Field(default=None)
    """API服务的基础URL，对于本地部署的LLM必需"""
    
    api_version: str | None = Field(default=None)
    """API版本号"""
    
    aws_access_key_id: SecretStr | None = Field(default=None)
    """AWS访问密钥ID，用于AWS上的LLM服务"""
    
    aws_secret_access_key: SecretStr | None = Field(default=None)
    """AWS秘密访问密钥"""
    
    aws_region_name: str | None = Field(default=None)
    """AWS区域名称"""
    
    openrouter_site_url: str = Field(default='https://docs.all-hands.dev/')
    """OpenRouter站点URL"""
    
    openrouter_app_name: str = Field(default='OpenHands')
    """OpenRouter应用名称"""
    
    # 总等待时间：5 + 10 + 20 + 30 = 65秒
    num_retries: int = Field(default=4)
    """API请求失败时的重试次数"""
    
    retry_multiplier: float = Field(default=2)
    """指数退避的乘数因子"""
    
    retry_min_wait: int = Field(default=5)
    """重试间的最小等待时间（秒）"""
    
    retry_max_wait: int = Field(default=30)
    """重试间的最大等待时间（秒）"""
    
    timeout: int | None = Field(default=None)
    """API请求超时时间（秒）"""
    
    max_message_chars: int = Field(
        default=30_000
    )  # 发送给LLM时观察内容的最大字符数
    """单个观察结果内容的最大字符数限制"""
    
    temperature: float = Field(default=0.0)
    """控制输出随机性的温度参数，0.0表示确定性输出"""
    
    top_p: float = Field(default=1.0)
    """核采样参数，控制考虑的token概率质量"""
    
    top_k: float | None = Field(default=None)
    """Top-k采样参数，限制考虑的候选token数量"""
    
    custom_llm_provider: str | None = Field(default=None)
    """自定义LLM提供商标识符"""
    
    max_input_tokens: int | None = Field(default=None)
    """最大输入token数限制"""
    
    max_output_tokens: int | None = Field(default=None)
    """最大输出token数限制"""
    
    input_cost_per_token: float | None = Field(default=None)
    """每个输入token的成本（用于成本跟踪）"""
    
    output_cost_per_token: float | None = Field(default=None)
    """每个输出token的成本（用于成本跟踪）"""
    
    ollama_base_url: str | None = Field(default=None)
    """Ollama服务的基础URL"""
    
    # 此设置可以在每次调用litellm时发送
    drop_params: bool = Field(default=True)
    """是否丢弃不支持的参数而不抛出异常"""
    
    # 注意：此设置实际上是全局的，与drop_params不同
    modify_params: bool = Field(default=True)
    """是否允许litellm修改参数（如添加默认消息）"""
    
    disable_vision: bool | None = Field(default=None)
    """是否禁用视觉功能（即使模型支持）"""
    
    caching_prompt: bool = Field(default=True)
    """是否使用提示缓存功能（如果提供商支持）"""
    
    log_completions: bool = Field(default=False)
    """是否记录LLM的完整响应"""
    
    log_completions_folder: str = Field(default=os.path.join(LOG_DIR, 'completions'))
    """记录LLM完成结果的文件夹路径"""
    
    custom_tokenizer: str | None = Field(default=None)
    """自定义tokenizer的标识符"""
    
    native_tool_calling: bool | None = Field(default=None)
    """是否使用模型的原生工具调用功能"""
    
    reasoning_effort: str | None = Field(default='high')
    """推理努力程度，专用于o1系列模型"""
    
    seed: int | None = Field(default=None)
    """随机种子，用于可重现的输出"""
    
    safety_settings: list[dict[str, str]] | None = Field(
        default=None,
        description='支持安全设置的模型的安全配置（如Mistral AI和Gemini）',
    )
    """模型安全设置配置列表"""

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')

    @classmethod
    def from_toml_section(cls, data: dict) -> dict[str, LLMConfig]:
        """从表示[llm]部分的toml字典创建LLMConfig实例的映射。

        默认配置是从data中的所有非字典键构建的。
        然后，每个具有字典值的键（例如[llm.random_name]）被视为自定义LLM配置，
        其值覆盖默认配置。

        Example:
            应用通用LLM配置与自定义LLM覆盖，例如：
            [llm]
            model=...
            num_retries = 5
            [llm.claude]
            model="claude-3-5-sonnet"
            结果是num_retries应用到claude-3-5-sonnet。

        Args:
            data: 包含LLM配置的字典
            
        Returns:
            dict[str, LLMConfig]: 一个映射，其中键"llm"对应默认配置，
            其他键表示自定义配置
        """

        # 初始化结果映射
        llm_mapping: dict[str, LLMConfig] = {}

        # 提取基础配置数据（非字典值）
        base_data = {}
        custom_sections: dict[str, dict] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                # 如果值是字典，说明是自定义LLM配置部分
                custom_sections[key] = value
            else:
                # 如果值不是字典，说明是基础配置项
                base_data[key] = value

        # 尝试创建基础配置
        try:
            base_config = cls.model_validate(base_data)
            llm_mapping['llm'] = base_config
        except ValidationError:
            logger.warning(
                'Cannot parse [llm] config from toml. Continuing with defaults.'
            )
            # 如果基础配置失败，创建默认配置
            base_config = cls()
            # 仍然添加到映射中
            llm_mapping['llm'] = base_config

        # 独立处理每个自定义部分
        for name, overrides in custom_sections.items():
            try:
                # 将基础配置与覆盖配置合并
                merged = {**base_config.model_dump(), **overrides}
                custom_config = cls.model_validate(merged)
                llm_mapping[name] = custom_config
            except ValidationError:
                logger.warning(
                    f'Cannot parse [{name}] config from toml. This section will be skipped.'
                )
                # 跳过此自定义部分但继续处理其他部分
                continue

        return llm_mapping

    def model_post_init(self, __context: Any) -> None:
        """初始化后的钩子函数，用于分配OpenRouter相关变量到环境变量。

        这确保这些值在运行时对litellm可访问。
        
        Args:
            __context: Pydantic上下文对象
        """
        super().model_post_init(__context)

        # 将OpenRouter特定变量分配给环境变量
        if self.openrouter_site_url:
            os.environ['OR_SITE_URL'] = self.openrouter_site_url
        if self.openrouter_app_name:
            os.environ['OR_APP_NAME'] = self.openrouter_app_name

        # 默认为Azure模型设置API版本
        # 新模型需要此设置
        # Azure问题：https://github.com/All-Hands-AI/OpenHands/issues/7755
        if self.model.startswith('azure') and self.api_version is None:
            self.api_version = '2024-12-01-preview'