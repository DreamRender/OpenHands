from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SecurityConfig(BaseModel):
    """安全相关功能的配置类。

    这个类定义了系统的安全设置，包括确认模式和安全分析器的配置。
    用于控制OpenHands的安全行为和风险管控。

    Attributes:
        confirmation_mode: 是否启用确认模式
        security_analyzer: 要使用的安全分析器
    """

    confirmation_mode: bool = Field(default=False)
    """是否启用确认模式。
    
    当启用确认模式时，系统在执行某些操作前会要求用户确认，
    这有助于防止意外或潜在危险的操作。
    默认为False，即不启用确认模式。
    """
    
    security_analyzer: str | None = Field(default=None)
    """要使用的安全分析器名称。
    
    指定用于分析代码或操作安全性的分析器。
    可以为None表示不使用任何安全分析器，
    或者指定特定的分析器名称。
    """

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')

    @classmethod
    def from_toml_section(cls, data: dict) -> dict[str, 'SecurityConfig']:
        """从表示[security]部分的toml字典创建SecurityConfig实例的映射。

        配置是从data中的所有键构建的。

        Args:
            data: 包含安全配置数据的字典
            
        Returns:
            dict[str, SecurityConfig]: 一个映射，其中键"security"对应[security]配置
            
        Raises:
            ValueError: 当安全配置无效时抛出
        """

        # 初始化结果映射
        security_mapping: dict[str, SecurityConfig] = {}

        # 尝试创建配置实例
        try:
            # 使用模型验证创建安全配置实例
            security_mapping['security'] = cls.model_validate(data)
        except ValidationError as e:
            # 如果验证失败，抛出更友好的错误信息
            raise ValueError(f'Invalid security configuration: {e}')

        return security_mapping