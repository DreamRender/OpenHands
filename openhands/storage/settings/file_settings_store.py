from __future__ import annotations

import json
from dataclasses import dataclass

from openhands.core.config.openhands_config import OpenHandsConfig
from openhands.storage import get_file_store
from openhands.storage.data_models.settings import Settings
from openhands.storage.files import FileStore
from openhands.storage.settings.settings_store import SettingsStore
from openhands.utils.async_utils import call_sync_from_async


@dataclass
class FileSettingsStore(SettingsStore):
    """基于文件系统的设置存储实现。
    
    该类实现了SettingsStore抽象基类，使用文件系统来存储和管理用户设置。
    设置以JSON格式存储在指定的文件中。
    """
    
    file_store: FileStore               # 文件存储接口，用于实际的文件操作
    path: str = 'settings.json'        # 设置文件的路径，默认为'settings.json'

    async def load(self) -> Settings | None:
        """从文件系统加载用户设置。
        
        Returns:
            加载的Settings对象，如果文件不存在则返回None
        """
        try:
            # 异步读取设置文件内容
            json_str = await call_sync_from_async(self.file_store.read, self.path)
            # 解析JSON字符串为字典
            kwargs = json.loads(json_str)
            # 创建并返回Settings对象
            settings = Settings(**kwargs)
            return settings
        except FileNotFoundError:
            # 如果设置文件不存在，返回None
            return None

    async def store(self, settings: Settings) -> None:
        """将用户设置存储到文件系统。
        
        Args:
            settings: 要存储的Settings对象
        """
        # 将设置对象序列化为JSON字符串，暴露真实的密钥值
        json_str = settings.model_dump_json(context={'expose_secrets': True})
        # 异步写入文件
        await call_sync_from_async(self.file_store.write, self.path, json_str)

    @classmethod
    async def get_instance(
        cls, config: OpenHandsConfig, user_id: str | None
    ) -> FileSettingsStore:
        """创建FileSettingsStore实例。
        
        Args:
            config: OpenHands配置对象
            user_id: 用户的唯一标识符，可能为None
            
        Returns:
            FileSettingsStore的实例
        """
        # 根据配置创建文件存储实例
        # 注意：这里有重复的赋值，可能是笔误，但保持原代码不变
        file_store = file_store = get_file_store(
            config.file_store,
            config.file_store_path,
            config.file_store_web_hook_url,
            config.file_store_web_hook_headers,
        )
        return FileSettingsStore(file_store)