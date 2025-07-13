from __future__ import annotations

from abc import ABC, abstractmethod

from openhands.core.config.openhands_config import OpenHandsConfig
from openhands.storage.data_models.user_secrets import UserSecrets


class SecretsStore(ABC):
    """用于存储用户密钥的抽象基类。

    这是OpenHands中的一个扩展点，允许应用程序自定义用户密钥的存储方式。
    应用程序可以通过以下方式替换自己的实现：
    1. 创建一个继承自SecretsStore的类
    2. 实现所有必需的方法
    3. 将server_config.secret_store_class设置为该类的完全限定名称

    该类通过openhands.server.shared.py中的get_impl()进行实例化。

    根据环境的不同，该实现可能支持也可能不支持多用户。
    """

    @abstractmethod
    async def load(self) -> UserSecrets | None:
        """加载密钥。
        
        Returns:
            加载的UserSecrets对象，如果没有找到密钥则返回None
        """

    @abstractmethod
    async def store(self, secrets: UserSecrets) -> None:
        """存储密钥。
        
        Args:
            secrets: 要存储的UserSecrets对象
        """

    @classmethod
    @abstractmethod
    async def get_instance(
        cls, config: OpenHandsConfig, user_id: str | None
    ) -> SecretsStore:
        """获取给定用户token所代表的用户的存储实例。
        
        Args:
            config: OpenHands配置对象
            user_id: 用户的唯一标识符，可能为None
            
        Returns:
            SecretsStore的实例
        """