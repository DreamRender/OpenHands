from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from openhands.core.config.openhands_config import OpenHandsConfig
from openhands.storage.data_models.conversation_metadata import ConversationMetadata
from openhands.storage.data_models.conversation_metadata_result_set import (
    ConversationMetadataResultSet,
)
from openhands.utils.async_utils import wait_all


class ConversationStore(ABC):
    """对话Metadata存储的抽象基类。

    这是OpenHands中的一个扩展点，允许应用程序自定义对话metadata的存储方式。
    应用程序可以通过以下方式替换自己的实现：
    1. 创建一个继承自ConversationStore的类
    2. 实现所有必需的方法
    3. 将server_config.conversation_store_class设置为该类的完全限定名称

    该类通过openhands.server.shared.py中的get_impl()进行实例化。

    根据环境的不同，该实现可能支持也可能不支持多用户。
    """

    @abstractmethod
    async def save_metadata(self, metadata: ConversationMetadata) -> None:
        """存储对话metadata。
        
        Args:
            metadata: 要存储的对话metadata对象
        """

    @abstractmethod
    async def get_metadata(self, conversation_id: str) -> ConversationMetadata:
        """加载对话metadata。
        
        Args:
            conversation_id: 对话的唯一标识符
            
        Returns:
            对应的对话metadata对象
        """

    async def validate_metadata(self, conversation_id: str, user_id: str) -> bool:
        """验证对话是否属于当前用户。
        
        Args:
            conversation_id: 对话的唯一标识符
            user_id: 用户的唯一标识符
            
        Returns:
            如果对话属于指定用户则返回True，否则返回False
        """
        # 获取对话的metadata
        metadata = await self.get_metadata(conversation_id)
        # 检查metadata中的user_id是否存在且与当前用户匹配
        if not metadata.user_id or metadata.user_id != user_id:
            return False
        else:
            return True

    @abstractmethod
    async def delete_metadata(self, conversation_id: str) -> None:
        """删除对话metadata。
        
        Args:
            conversation_id: 要删除的对话的唯一标识符
        """

    @abstractmethod
    async def exists(self, conversation_id: str) -> bool:
        """检查对话是否存在。
        
        Args:
            conversation_id: 对话的唯一标识符
            
        Returns:
            如果对话存在则返回True，否则返回False
        """

    @abstractmethod
    async def search(
        self,
        page_id: str | None = None,
        limit: int = 20,
    ) -> ConversationMetadataResultSet:
        """搜索对话。
        
        Args:
            page_id: 分页标识符，用于获取特定页面的结果
            limit: 每页返回的最大结果数，默认为20
            
        Returns:
            包含搜索结果的ConversationMetadataResultSet对象
        """

    async def get_all_metadata(
        self, conversation_ids: Iterable[str]
    ) -> list[ConversationMetadata]:
        """并行获取多个对话的metadata。
        
        Args:
            conversation_ids: 对话ID的可迭代集合
            
        Returns:
            对应的ConversationMetadata对象列表
        """
        # 使用wait_all并行执行多个异步操作，提高性能
        return await wait_all([self.get_metadata(cid) for cid in conversation_ids])

    @classmethod
    @abstractmethod
    async def get_instance(
        cls, config: OpenHandsConfig, user_id: str | None
    ) -> ConversationStore:
        """获取给定用户token所代表的用户的存储实例。
        
        Args:
            config: OpenHands配置对象
            user_id: 用户的唯一标识符，可能为None
            
        Returns:
            ConversationStore的实例
        """