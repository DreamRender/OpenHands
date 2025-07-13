from __future__ import annotations

import json
from dataclasses import dataclass

from openhands.core.config.openhands_config import OpenHandsConfig
from openhands.storage import get_file_store
from openhands.storage.data_models.user_secrets import UserSecrets
from openhands.storage.files import FileStore
from openhands.storage.secrets.secrets_store import SecretsStore
from openhands.utils.async_utils import call_sync_from_async


@dataclass
class FileSecretsStore(SecretsStore):
    """基于文件系统的密钥存储实现。
    
    该类实现了SecretsStore抽象基类，使用文件系统来存储和管理用户密钥。
    密钥以JSON格式存储在指定的文件中。
    """
    
    file_store: FileStore                # 文件存储接口，用于实际的文件操作
    path: str = 'secrets.json'          # 密钥文件的路径，默认为'secrets.json'

    async def load(self) -> UserSecrets | None:
        """从文件系统加载用户密钥。
        
        Returns:
            加载的UserSecrets对象，如果文件不存在则返回None
        """
        try:
            # 异步读取密钥文件内容
            json_str = await call_sync_from_async(self.file_store.read, self.path)
            # 解析JSON字符串
            kwargs = json.loads(json_str)
            
            # 过滤掉没有token的服务提供商条目
            provider_tokens = {
                k: v
                for k, v in (kwargs.get('provider_tokens') or {}).items()
                if v.get('token')  # 只保留有token的条目
            }
            kwargs['provider_tokens'] = provider_tokens
            
            # 创建并返回UserSecrets对象
            secrets = UserSecrets(**kwargs)
            return secrets
        except FileNotFoundError:
            # 如果密钥文件不存在，返回None
            return None

    async def store(self, secrets: UserSecrets) -> None:
        """将用户密钥存储到文件系统。
        
        Args:
            secrets: 要存储的UserSecrets对象
        """
        # 将密钥对象序列化为JSON字符串，暴露真实的密钥值
        json_str = secrets.model_dump_json(context={'expose_secrets': True})
        # 异步写入文件
        await call_sync_from_async(self.file_store.write, self.path, json_str)

    @classmethod
    async def get_instance(
        cls, config: OpenHandsConfig, user_id: str | None
    ) -> FileSecretsStore:
        """创建FileSecretsStore实例。
        
        Args:
            config: OpenHands配置对象
            user_id: 用户的唯一标识符，可能为None
            
        Returns:
            FileSecretsStore的实例
        """
        # 根据配置创建文件存储实例
        # 注意：这里有重复的赋值，可能是笔误，但保持原代码不变
        file_store = file_store = get_file_store(
            config.file_store,
            config.file_store_path,
            config.file_store_web_hook_url,
            config.file_store_web_hook_headers,
        )
        return FileSecretsStore(file_store)