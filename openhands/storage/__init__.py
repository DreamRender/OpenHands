"""
OpenHands 存储模块初始化文件

本模块提供统一的文件存储接口，支持多种存储后端：
- 本地文件系统存储 (LocalFileStore)
- Amazon S3 存储 (S3FileStore)
- Google Cloud 存储 (GoogleCloudFileStore)
- 内存存储 (InMemoryFileStore)
- WebHook 存储装饰器 (WebHookFileStore)

主要功能是通过工厂函数 get_file_store 根据配置参数创建相应的存储实例。
"""

import os

import httpx

# 导入各种文件存储实现类
from openhands.storage.files import FileStore  # 文件存储基类接口
from openhands.storage.google_cloud import GoogleCloudFileStore  # Google Cloud 存储实现
from openhands.storage.local import LocalFileStore  # 本地文件系统存储实现
from openhands.storage.memory import InMemoryFileStore  # 内存存储实现
from openhands.storage.s3 import S3FileStore  # Amazon S3 存储实现
from openhands.storage.web_hook import WebHookFileStore  # WebHook 存储装饰器


def get_file_store(
        file_store_type: str,
        file_store_path: str | None = None,
        file_store_web_hook_url: str | None = None,
        file_store_web_hook_headers: dict | None = None,
) -> FileStore:
    """
    文件存储工厂函数

    根据指定的存储类型和配置参数创建相应的文件存储实例。
    支持多种存储后端，并可选择性地包装 WebHook 功能。

    Args:
        file_store_type (str): 文件存储类型
            - 'local': 本地文件系统存储
            - 's3': Amazon S3 存储
            - 'google_cloud': Google Cloud 存储
            - 其他值: 默认使用内存存储
        file_store_path (str | None, optional): 存储路径
            - 对于本地存储: 必须指定本地目录路径
            - 对于云存储: 可选的存储桶或容器路径
        file_store_web_hook_url (str | None, optional): WebHook 回调 URL
            如果提供，将使用 WebHookFileStore 包装基础存储
        file_store_web_hook_headers (dict | None, optional): WebHook 请求头
            用于 WebHook 请求的自定义 HTTP 头部信息

    Returns:
        FileStore: 配置好的文件存储实例

    Raises:
        ValueError: 当使用本地存储类型但未提供 file_store_path 时抛出

    Examples:
        >>> # 创建本地文件存储
        >>> store = get_file_store('local', '/path/to/storage')

        >>> # 创建带 WebHook 的 S3 存储
        >>> store = get_file_store(
        ...     's3',
        ...     'my-bucket',
        ...     'https://webhook.example.com',
        ...     {'Authorization': 'Bearer token'}
        ... )
    """
    store: FileStore  # 声明存储实例变量

    # 根据存储类型创建相应的存储实例
    if file_store_type == 'local':
        # 本地文件系统存储需要指定存储路径
        if file_store_path is None:
            raise ValueError('file_store_path is required for local file store')
        store = LocalFileStore(file_store_path)
    elif file_store_type == 's3':
        # Amazon S3 存储，路径为可选的存储桶配置
        store = S3FileStore(file_store_path)
    elif file_store_type == 'google_cloud':
        # Google Cloud 存储，路径为可选的存储桶配置
        store = GoogleCloudFileStore(file_store_path)
    else:
        # 默认情况下使用内存存储（适用于测试或临时存储场景）
        store = InMemoryFileStore()

    # 如果指定了 WebHook URL，则使用 WebHookFileStore 包装基础存储
    # WebHook 功能允许在文件操作时发送 HTTP 通知
    if file_store_web_hook_url:
        # 处理 WebHook 请求头配置
        if file_store_web_hook_headers is None:
            # 回退到默认请求头配置
            # 如果环境变量中定义了 Session API Key，则自动添加到请求头中
            file_store_web_hook_headers = {}
            if os.getenv('SESSION_API_KEY'):
                # 添加 Session API Key 到请求头，用于身份验证
                file_store_web_hook_headers['X-Session-API-Key'] = os.getenv(
                    'SESSION_API_KEY'
                )

        # 创建 WebHook 装饰器，包装原始存储实例
        # 这样可以在文件操作时自动发送 WebHook 通知
        store = WebHookFileStore(
            store,  # 被包装的基础存储实例
            file_store_web_hook_url,  # WebHook 回调 URL
            httpx.Client(headers=file_store_web_hook_headers or {}),  # HTTP 客户端配置
        )

    return store  # 返回配置完成的存储实例
