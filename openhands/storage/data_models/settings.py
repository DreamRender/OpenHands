from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    SerializationInfo,
    field_serializer,
    model_validator,
)
from pydantic.json import pydantic_encoder

from openhands.core.config.llm_config import LLMConfig
from openhands.core.config.mcp_config import MCPConfig
from openhands.core.config.utils import load_openhands_config
from openhands.storage.data_models.user_secrets import UserSecrets


class Settings(BaseModel):
    """OpenHands Session的持久化设置数据模型。
    
    包含用户的各种配置选项，如语言设置、Agent配置、LLM配置等。
    这些设置会被持久化存储，在Session之间保持一致。
    """

    language: str | None = None                          # 用户界面语言，可能为None
    agent: str | None = None                            # 选择的Agent类型，可能为None
    max_iterations: int | None = None                   # 最大迭代次数，可能为None
    security_analyzer: str | None = None               # 安全分析器类型，可能为None
    confirmation_mode: bool | None = None              # 确认模式是否启用，可能为None
    llm_model: str | None = None                       # LLM模型名称，可能为None
    llm_api_key: SecretStr | None = None              # LLM API密钥，使用SecretStr保护敏感信息
    llm_base_url: str | None = None                   # LLM API基础URL，可能为None
    remote_runtime_resource_factor: int | None = None # 远程运行时资源因子，可能为None
    
    # 计划从设置中移除的字段
    secrets_store: UserSecrets = Field(default_factory=UserSecrets, frozen=True)  # 密钥存储，不可变
    
    enable_default_condenser: bool = True              # 是否启用默认Condenser，默认为True
    enable_sound_notifications: bool = False           # 是否启用声音通知，默认为False
    enable_proactive_conversation_starters: bool = True  # 是否启用主动对话启动器，默认为True
    user_consents_to_analytics: bool | None = None     # 用户是否同意分析，可能为None
    sandbox_base_container_image: str | None = None    # Sandbox基础容器镜像，可能为None
    sandbox_runtime_container_image: str | None = None # Sandbox运行时容器镜像，可能为None
    mcp_config: MCPConfig | None = None                # MCP配置，可能为None
    search_api_key: SecretStr | None = None           # 搜索API密钥，使用SecretStr保护敏感信息
    sandbox_api_key: SecretStr | None = None          # Sandbox API密钥，使用SecretStr保护敏感信息
    max_budget_per_task: float | None = None          # 每个任务的最大预算，可能为None
    email: str | None = None                          # 用户邮箱，可能为None
    email_verified: bool | None = None                # 邮箱是否已验证，可能为None

    model_config = ConfigDict(
        validate_assignment=True,  # 赋值时进行验证
    )

    @field_serializer('llm_api_key', 'search_api_key')
    def api_key_serializer(self, api_key: SecretStr | None, info: SerializationInfo):
        """API密钥的自定义序列化器。

        要序列化API密钥而不是显示********，需要在序列化上下文中设置expose_secrets为True。
        
        Args:
            api_key: 要序列化的API密钥
            info: 序列化信息，包含上下文
            
        Returns:
            序列化后的API密钥值或掩码
        """
        if api_key is None:
            return None

        context = info.context
        # 检查是否需要暴露真实的密钥值
        if context and context.get('expose_secrets', False):
            return api_key.get_secret_value()  # 返回真实值

        return pydantic_encoder(api_key)  # 返回掩码值

    @model_validator(mode='before')
    @classmethod
    def convert_provider_tokens(cls, data: dict | object) -> dict | object:
        """将服务提供商token从JSON格式转换为UserSecrets格式。
        
        Args:
            data: 输入数据，可能是字典或对象
            
        Returns:
            转换后的数据
        """
        if not isinstance(data, dict):
            return data

        secrets_store = data.get('secrets_store')
        if not isinstance(secrets_store, dict):
            return data

        # 提取自定义密钥和服务提供商token
        custom_secrets = secrets_store.get('custom_secrets')
        tokens = secrets_store.get('provider_tokens')

        # 创建初始的密钥存储对象
        secret_store = UserSecrets(provider_tokens={}, custom_secrets={})  # type: ignore[arg-type]

        # 处理服务提供商token
        if isinstance(tokens, dict):
            converted_store = UserSecrets(provider_tokens=tokens)  # type: ignore[arg-type]
            secret_store = secret_store.model_copy(
                update={'provider_tokens': converted_store.provider_tokens}
            )
        else:
            secret_store.model_copy(update={'provider_tokens': tokens})

        # 处理自定义密钥
        if isinstance(custom_secrets, dict):
            converted_store = UserSecrets(custom_secrets=custom_secrets)  # type: ignore[arg-type]
            secret_store = secret_store.model_copy(
                update={'custom_secrets': converted_store.custom_secrets}
            )
        else:
            secret_store = secret_store.model_copy(
                update={'custom_secrets': custom_secrets}
            )
        data['secret_store'] = secret_store
        return data

    @field_serializer('secrets_store')
    def secrets_store_serializer(self, secrets: UserSecrets, info: SerializationInfo):
        """密钥存储的自定义序列化器。
        
        Args:
            secrets: 用户密钥对象
            info: 序列化信息
            
        Returns:
            序列化后的密钥存储，强制无效化密钥存储以确保安全
        """
        # 强制无效化密钥存储，出于安全考虑只返回空的provider_tokens
        return {'provider_tokens': {}}

    @staticmethod
    def from_config() -> Settings | None:
        """从配置文件创建Settings实例。
        
        根据当前的应用配置创建默认的设置对象。
        
        Returns:
            基于配置文件的Settings实例，如果没有设置API密钥则返回None
        """
        # 加载应用配置
        app_config = load_openhands_config()
        llm_config: LLMConfig = app_config.get_llm_config()
        
        # 如果没有设置API密钥，认为没有合理的默认设置
        if llm_config.api_key is None:
            return None
            
        # 获取安全配置
        security = app_config.security

        # 获取MCP配置（如果可用）
        mcp_config = None
        if hasattr(app_config, 'mcp'):
            mcp_config = app_config.mcp

        # 创建设置对象，使用配置文件中的值
        settings = Settings(
            language='en',                                    # 默认语言为英语
            agent=app_config.default_agent,                  # 默认Agent
            max_iterations=app_config.max_iterations,        # 最大迭代次数
            security_analyzer=security.security_analyzer,    # 安全分析器
            confirmation_mode=security.confirmation_mode,    # 确认模式
            llm_model=llm_config.model,                     # LLM模型
            llm_api_key=llm_config.api_key,                 # LLM API密钥
            llm_base_url=llm_config.base_url,               # LLM基础URL
            remote_runtime_resource_factor=app_config.sandbox.remote_runtime_resource_factor,  # 远程运行时资源因子
            mcp_config=mcp_config,                          # MCP配置
            search_api_key=app_config.search_api_key,       # 搜索API密钥
            max_budget_per_task=app_config.max_budget_per_task,  # 每个任务的最大预算
        )
        return settings