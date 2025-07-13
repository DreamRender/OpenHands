import httpx
import tenacity

from openhands.storage.files import FileStore
from openhands.utils.async_utils import EXECUTOR


class WebHookFileStore(FileStore):
    """
    带Webhook通知的文件存储包装器类
    
    这个类包装了另一个FileStore实现，并在文件发生变化时发送HTTP请求到指定的URL。
    当文件被写入或删除时，会触发相应的webhook通知。
    
    这种设计模式是装饰器模式的体现，允许在不修改原有FileStore实现的情况下，
    为其添加webhook通知功能。
    
    Attributes:
        file_store (FileStore): 底层的FileStore实现
        base_url (str): webhook请求的基础URL
        client (httpx.Client): 用于发送webhook请求的HTTP客户端
    """

    file_store: FileStore  # 底层的FileStore实现
    base_url: str         # webhook请求的基础URL  
    client: httpx.Client  # 用于发送webhook请求的HTTP客户端

    def __init__(
        self, file_store: FileStore, base_url: str, client: httpx.Client | None = None
    ):
        """
        初始化WebHookFileStore
        
        Args:
            file_store (FileStore): 底层的FileStore实现，负责实际的文件操作
            base_url (str): webhook请求的基础URL，文件路径会被附加到此URL后
            client (httpx.Client | None): 可选的HTTP客户端。如果为None，会创建一个新的客户端
        """
        self.file_store = file_store
        self.base_url = base_url
        # 如果没有提供HTTP客户端，创建一个新的
        if client is None:
            client = httpx.Client()
        self.client = client

    def write(self, path: str, contents: str | bytes) -> None:
        """
        写入文件内容并触发webhook通知
        
        首先调用底层FileStore的write方法执行实际的写入操作，
        然后异步发送POST请求到webhook URL通知文件已被写入。
        
        Args:
            path (str): 要写入的文件路径
            contents (str | bytes): 要写入的文件内容
        """
        # 先执行实际的文件写入操作
        self.file_store.write(path, contents)
        # 异步提交webhook通知任务到线程池执行器
        EXECUTOR.submit(self._on_write, path, contents)

    def read(self, path: str) -> str:
        """
        读取文件内容
        
        直接委托给底层FileStore的read方法，读取操作不会触发webhook。
        
        Args:
            path (str): 要读取的文件路径
            
        Returns:
            str: 文件的字符串内容
        """
        return self.file_store.read(path)

    def list(self, path: str) -> list[str]:
        """
        列出目录中的文件
        
        直接委托给底层FileStore的list方法，列表操作不会触发webhook。
        
        Args:
            path (str): 要列出内容的目录路径
            
        Returns:
            list[str]: 包含该路径下所有文件和目录路径的列表
        """
        return self.file_store.list(path)

    def delete(self, path: str) -> None:
        """
        删除文件并触发webhook通知
        
        首先调用底层FileStore的delete方法执行实际的删除操作，
        然后异步发送DELETE请求到webhook URL通知文件已被删除。
        
        Args:
            path (str): 要删除的文件路径
        """
        # 先执行实际的文件删除操作
        self.file_store.delete(path)
        # 异步提交webhook通知任务到线程池执行器
        EXECUTOR.submit(self._on_delete, path)

    @tenacity.retry(
        wait=tenacity.wait_fixed(1),      # 每次重试间隔1秒
        stop=tenacity.stop_after_attempt(3),  # 最多重试3次
    )
    def _on_write(self, path: str, contents: str | bytes) -> None:
        """
        文件写入时的webhook通知方法
        
        向webhook URL发送POST请求，请求体包含写入的文件内容。
        使用tenacity库实现重试机制，最多重试3次，每次间隔1秒。
        
        Args:
            path (str): 被写入的文件路径
            contents (str | bytes): 写入的文件内容
            
        Raises:
            httpx.HTTPStatusError: 如果webhook请求失败且重试次数用尽
        """
        # 构建完整的webhook URL（基础URL + 文件路径）
        base_url = self.base_url + path
        # 发送POST请求，将文件内容作为请求体
        response = self.client.post(base_url, content=contents)
        # 如果响应状态码表示错误，抛出异常（触发重试机制）
        response.raise_for_status()

    @tenacity.retry(
        wait=tenacity.wait_fixed(1),      # 每次重试间隔1秒
        stop=tenacity.stop_after_attempt(3),  # 最多重试3次
    )
    def _on_delete(self, path: str) -> None:
        """
        文件删除时的webhook通知方法
        
        向webhook URL发送DELETE请求，通知指定路径的文件已被删除。
        使用tenacity库实现重试机制，最多重试3次，每次间隔1秒。
        
        Args:
            path (str): 被删除的文件路径
            
        Raises:
            httpx.HTTPStatusError: 如果webhook请求失败且重试次数用尽
        """
        # 构建完整的webhook URL（基础URL + 文件路径）
        base_url = self.base_url + path
        # 发送DELETE请求
        response = self.client.delete(base_url)
        # 如果响应状态码表示错误，抛出异常（触发重试机制）
        response.raise_for_status()