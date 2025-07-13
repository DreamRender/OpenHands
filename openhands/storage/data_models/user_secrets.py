from types import MappingProxyType
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializationInfo,
    field_serializer,
    model_validator,
)
from pydantic.json import pydantic_encoder

from openhands.events.stream import EventStream
from openhands.integrations.provider import (
    CUSTOM_SECRETS_TYPE,
    CUSTOM_SECRETS_TYPE_WITH_JSON_SCHEMA,
    PROVIDER_TOKEN_TYPE,
    PROVIDER_TOKEN_TYPE_WITH_JSON_SCHEMA,
    CustomSecret,
    ProviderToken,
)
from openhands.integrations.service_types import ProviderType


class UserSecrets(BaseModel):
    """用户密钥的数据模型。
    
    管理用户的各种密钥信息，包括服务提供商的token和自定义密钥。
    使用不可变的MappingProxyType来确保数据安全性。
    """
    
    provider_tokens: PROVIDER_TOKEN_TYPE_WITH_JSON_SCHEMA = Field(
        default_factory=lambda: MappingProxyType({})
    )  # 服务提供商token映射，使用不可变代理类型，默认为空映射
    
    custom_secrets: CUSTOM_SECRETS_TYPE_WITH_JSON_SCHEMA = Field(
        default_factory=lambda: MappingProxyType({})
    )  # 自定义密钥映射，使用不可变代理类型，默认为空映射

    model_config = ConfigDict(
        frozen=True,                # 模型实例不可变
        validate_assignment=True,   # 赋值时进行验证
        arbitrary_types_allowed=True,  # 允许任意类型
    )

    @field_serializer('provider_tokens')
    def provider_tokens_serializer(
        self, provider_tokens: PROVIDER_TOKEN_TYPE, info: SerializationInfo
    ) -> dict[str, dict[str, str | Any]]:
        """服务提供商token的序列化器。
        
        根据序列化上下文决定是否暴露真实的token值。
        
        Args:
            provider_tokens: 服务提供商token映射
            info: 序列化信息，包含上下文
            
        Returns:
            序列化后的token字典
        """
        tokens = {}
        # 检查是否需要暴露密钥值
        expose_secrets = info.context and info.context.get('expose_secrets', False)

        for token_type, provider_token in provider_tokens.items():
            # 跳过空的或无效的token
            if not provider_token or not provider_token.token:
                continue

            # 转换token类型为字符串
            token_type_str = (
                token_type.value
                if isinstance(token_type, ProviderType)
                else str(token_type)
            )

            # 根据上下文决定是否暴露真实token值
            token = None
            if provider_token.token:
                token = (
                    provider_token.token.get_secret_value()  # 暴露真实值
                    if expose_secrets
                    else pydantic_encoder(provider_token.token)  # 使用掩码值
                )

            tokens[token_type_str] = {
                'token': token,
                'host': provider_token.host,      # 主机地址
                'user_id': provider_token.user_id,  # 用户ID
            }

        return tokens

    @field_serializer('custom_secrets')
    def custom_secrets_serializer(
        self, custom_secrets: CUSTOM_SECRETS_TYPE, info: SerializationInfo
    ):
        """自定义密钥的序列化器。
        
        Args:
            custom_secrets: 自定义密钥映射
            info: 序列化信息，包含上下文
            
        Returns:
            序列化后的密钥字典
        """
        secrets = {}
        # 检查是否需要暴露密钥值
        expose_secrets = info.context and info.context.get('expose_secrets', False)

        if custom_secrets:
            for secret_name, secret_value in custom_secrets.items():
                secrets[secret_name] = {
                    'secret': secret_value.secret.get_secret_value()  # 暴露真实值
                    if expose_secrets
                    else pydantic_encoder(secret_value.secret),  # 使用掩码值
                    'description': secret_value.description,  # 密钥描述
                }

        return secrets

    @model_validator(mode='before')
    @classmethod
    def convert_dict_to_mappingproxy(
        cls, data: dict[str, dict[str, Any] | MappingProxyType] | PROVIDER_TOKEN_TYPE
    ) -> dict[str, MappingProxyType | None]:
        """自定义反序列化器，将字典转换为MappingProxyType。
        
        Args:
            data: 输入数据，可能是字典或已经是正确类型的数据
            
        Returns:
            转换后的数据字典，包含MappingProxyType类型的字段
            
        Raises:
            ValueError: 当输入数据不是字典时
        """
        if not isinstance(data, dict):
            raise ValueError('UserSecrets must be initialized with a dictionary')

        new_data: dict[str, MappingProxyType | None] = {}

        # 处理provider_tokens字段
        if 'provider_tokens' in data:
            tokens = data['provider_tokens']
            if isinstance(tokens, dict):  # 确保只对字典输入进行转换
                converted_tokens = {}
                for key, value in tokens.items():
                    try:
                        # 转换key为ProviderType枚举
                        provider_type = (
                            ProviderType(key) if isinstance(key, str) else key
                        )
                        # 从值创建ProviderToken对象
                        converted_tokens[provider_type] = ProviderToken.from_value(
                            value
                        )
                    except ValueError:
                        # 跳过无效的提供商类型或token
                        continue

                # 转换为不可变的MappingProxyType
                new_data['provider_tokens'] = MappingProxyType(converted_tokens)
            elif isinstance(tokens, MappingProxyType):
                # 如果已经是MappingProxyType，直接使用
                new_data['provider_tokens'] = tokens

        # 处理custom_secrets字段
        if 'custom_secrets' in data:
            secrets = data['custom_secrets']
            if isinstance(secrets, dict):
                converted_secrets = {}
                for key, value in secrets.items():
                    try:
                        # 从值创建CustomSecret对象
                        converted_secrets[key] = CustomSecret.from_value(value)
                    except ValueError:
                        # 跳过无效的密钥
                        continue

                # 转换为不可变的MappingProxyType
                new_data['custom_secrets'] = MappingProxyType(converted_secrets)
            elif isinstance(secrets, MappingProxyType):
                # 如果已经是MappingProxyType，直接使用
                new_data['custom_secrets'] = secrets

        return new_data

    def set_event_stream_secrets(self, event_stream: EventStream) -> None:
        """将密钥设置到事件流中以进行掩码处理。
        
        这确保了服务提供商token和自定义密钥在事件流中被掩码化，
        防止敏感信息泄露。
        
        Args:
            event_stream: Agent Session的事件流
        """
        # 获取所有密钥的环境变量形式
        secrets = self.get_env_vars()
        # 在事件流中设置这些密钥，用于自动掩码
        event_stream.set_secrets(secrets)

    def get_env_vars(self) -> dict[str, str]:
        """获取密钥的环境变量形式。
        
        将自定义密钥转换为环境变量格式，便于在系统中使用。
        
        Returns:
            密钥名称到密钥值的映射字典
        """
        # 以暴露密钥的方式导出模型数据
        secret_store = self.model_dump(context={'expose_secrets': True})
        custom_secrets = secret_store.get('custom_secrets', {})
        secrets = {}
        # 提取自定义密钥的值
        for secret_name, value in custom_secrets.items():
            secrets[secret_name] = value['secret']

        return secrets

    def get_custom_secrets_descriptions(self) -> dict[str, str]:
        """获取自定义密钥的描述信息。
        
        Returns:
            密钥名称到描述的映射字典
        """
        secrets = {}
        for secret_name, secret in self.custom_secrets.items():
            secrets[secret_name] = secret.description

        return secrets