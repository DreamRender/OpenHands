from __future__ import annotations

from abc import ABC, abstractmethod

from openhands.core.config.openhands_config import OpenHandsConfig
from openhands.storage.data_models.settings import Settings


class SettingsStore(ABC):
    """用于存储用户设置的抽象基类。

    这是OpenHands中的一个扩展点，允许应用程序自定义用户设置的存储方式。
    应用程序可以通过以下方式替换自己的实现：
    1. 创建一个继承自SettingsStore的类
    2. 实现所有必需的方法
    3. 将server_config.settings_store_class设置为该类的完全限定名称

    该类通过openhands.server.shared.py中的get_impl()进行实例化。

    根据环境的不同，该实现可能支持也可能不支持多用户。
    """

    @abstractmethod
    async def load(self) -> Settings | None:
        """加载Session初始化数据。
        
        Returns:
            加载的Settings对象，如果没有找到设置则返回None
        """

    @abstractmethod
    async def store(self, settings: Settings) -> None:
        """存储Session初始化数据。
        
        Args:
            settings: 要存储的Settings对象
        """

    @classmethod
    @abstractmethod
    async def get_instance(
        cls, config: OpenHandsConfig, user_id: str | None
    ) -> SettingsStore:
        """获取给定用户token所代表的用户的存储实例。
        
        Args:
            config: OpenHands配置对象
            user_id: 用户的唯一标识符，可能为None
            
        Returns:
            SettingsStore的实例
        """