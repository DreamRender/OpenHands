from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from openhands.core.config.openhands_config import OpenHandsConfig
from openhands.core.logger import openhands_logger as logger
from openhands.storage import get_file_store
from openhands.storage.conversation.conversation_store import ConversationStore
from openhands.storage.data_models.conversation_metadata import ConversationMetadata
from openhands.storage.data_models.conversation_metadata_result_set import (
    ConversationMetadataResultSet,
)
from openhands.storage.files import FileStore
from openhands.storage.locations import (
    CONVERSATION_BASE_DIR,
    get_conversation_metadata_filename,
)
from openhands.utils.async_utils import call_sync_from_async
from openhands.utils.search_utils import offset_to_page_id, page_id_to_offset

# 创建ConversationMetadata的类型适配器，用于JSON序列化和反序列化
conversation_metadata_type_adapter = TypeAdapter(ConversationMetadata)


@dataclass
class FileConversationStore(ConversationStore):
    """基于文件系统的对话存储实现。
    
    该类实现了ConversationStore抽象基类，使用文件系统来存储和管理对话metadata。
    每个对话的metadata都存储在单独的JSON文件中。
    """
    
    file_store: FileStore  # 文件存储接口，用于实际的文件操作

    async def save_metadata(self, metadata: ConversationMetadata) -> None:
        """保存对话metadata到文件系统。
        
        Args:
            metadata: 要保存的对话metadata对象
        """
        # 将metadata对象序列化为JSON字符串
        json_str = conversation_metadata_type_adapter.dump_json(metadata)
        # 获取对话metadata文件的路径
        path = self.get_conversation_metadata_filename(metadata.conversation_id)
        # 异步写入文件
        await call_sync_from_async(self.file_store.write, path, json_str)

    async def get_metadata(self, conversation_id: str) -> ConversationMetadata:
        """从文件系统加载对话metadata。
        
        Args:
            conversation_id: 对话的唯一标识符
            
        Returns:
            对应的ConversationMetadata对象
            
        Raises:
            FileNotFoundError: 当对话metadata文件不存在时
        """
        # 获取对话metadata文件的路径
        path = self.get_conversation_metadata_filename(conversation_id)
        # 异步读取文件内容
        json_str = await call_sync_from_async(self.file_store.read, path)

        # 验证JSON格式并检查必要字段
        json_obj = json.loads(json_str)
        if 'created_at' not in json_obj:
            # 如果缺少created_at字段，认为文件无效
            raise FileNotFoundError(path)

        # 移除过时的github_user_id字段（如果存在）
        if 'github_user_id' in json_obj:
            json_obj.pop('github_user_id')

        # 使用类型适配器验证并转换JSON对象为ConversationMetadata
        result = conversation_metadata_type_adapter.validate_python(json_obj)
        return result

    async def delete_metadata(self, conversation_id: str) -> None:
        """删除对话metadata及其父目录。
        
        Args:
            conversation_id: 要删除的对话的唯一标识符
        """
        # 获取metadata文件的父目录路径
        path = str(
            Path(self.get_conversation_metadata_filename(conversation_id)).parent
        )
        # 删除整个对话目录
        await call_sync_from_async(self.file_store.delete, path)

    async def exists(self, conversation_id: str) -> bool:
        """检查对话metadata文件是否存在。
        
        Args:
            conversation_id: 对话的唯一标识符
            
        Returns:
            如果对话存在则返回True，否则返回False
        """
        # 获取metadata文件路径
        path = self.get_conversation_metadata_filename(conversation_id)
        try:
            # 尝试读取文件，如果成功则表示文件存在
            await call_sync_from_async(self.file_store.read, path)
            return True
        except FileNotFoundError:
            # 文件不存在
            return False

    async def search(
        self,
        page_id: str | None = None,
        limit: int = 20,
    ) -> ConversationMetadataResultSet:
        """搜索并分页返回对话metadata。
        
        Args:
            page_id: 分页标识符，用于获取特定页面的结果
            limit: 每页返回的最大结果数，默认为20
            
        Returns:
            包含搜索结果和下一页标识符的ConversationMetadataResultSet对象
        """
        conversations: list[ConversationMetadata] = []
        # 获取对话metadata存储目录
        metadata_dir = self.get_conversation_metadata_dir()
        try:
            # 列出目录中的所有对话ID
            # 从文件路径中提取对话ID（路径格式：.../conversation_id/metadata.json）
            conversation_ids = [
                path.split('/')[-2]
                for path in self.file_store.list(metadata_dir)
                if not path.startswith(f'{metadata_dir}/.')  # 排除隐藏文件和目录
            ]
        except FileNotFoundError:
            # 如果metadata目录不存在，返回空结果集
            return ConversationMetadataResultSet([])
            
        # 计算分页参数
        num_conversations = len(conversation_ids)
        start = page_id_to_offset(page_id)  # 将页面ID转换为偏移量
        end = min(limit + start, num_conversations)  # 计算结束位置
        conversations = []
        
        # 加载所有对话的metadata
        for conversation_id in conversation_ids:
            try:
                conversations.append(await self.get_metadata(conversation_id))
            except Exception:
                # 如果某个对话的metadata无法加载，记录警告并跳过
                logger.warning(
                    f'Could not load conversation metadata: {conversation_id}'
                )
                
        # 按创建时间降序排序（最新的在前）
        conversations.sort(key=_sort_key, reverse=True)
        # 应用分页
        conversations = conversations[start:end]
        # 计算下一页的页面ID
        next_page_id = offset_to_page_id(end, end < num_conversations)
        return ConversationMetadataResultSet(conversations, next_page_id)

    def get_conversation_metadata_dir(self) -> str:
        """获取对话metadata存储的基础目录。
        
        Returns:
            对话metadata存储的目录路径
        """
        return CONVERSATION_BASE_DIR

    def get_conversation_metadata_filename(self, conversation_id: str) -> str:
        """获取指定对话的metadata文件路径。
        
        Args:
            conversation_id: 对话的唯一标识符
            
        Returns:
            该对话metadata文件的完整路径
        """
        return get_conversation_metadata_filename(conversation_id)

    @classmethod
    async def get_instance(
        cls, config: OpenHandsConfig, user_id: str | None
    ) -> FileConversationStore:
        """创建FileConversationStore实例。
        
        Args:
            config: OpenHands配置对象
            user_id: 用户的唯一标识符，可能为None
            
        Returns:
            FileConversationStore的实例
        """
        # 根据配置创建文件存储实例
        file_store = get_file_store(
            config.file_store,
            config.file_store_path,
            config.file_store_web_hook_url,
            config.file_store_web_hook_headers,
        )
        return FileConversationStore(file_store)


def _sort_key(conversation: ConversationMetadata) -> str:
    """用于对话排序的键函数。
    
    根据对话的创建时间生成排序键，如果没有创建时间则返回空字符串。
    
    Args:
        conversation: 对话metadata对象
        
    Returns:
        用于排序的字符串键（ISO格式的时间戳）
    """
    created_at = conversation.created_at
    if created_at:
        return created_at.isoformat()  # 返回YYYY-MM-DDTHH:MM:SS格式，适合排序
    return ''  # 没有创建时间的对话排在最后