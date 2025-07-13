from dataclasses import dataclass, field
from typing import MutableMapping

import httpx

from openhands.core.logger import openhands_logger as logger

# 全局HTTP客户端实例
# 使用单一的全局客户端可以复用连接，提高性能
CLIENT = httpx.Client()


@dataclass
class HttpSession:
    """HTTP会话包装器类。

    request.Session在关闭后仍然可以重用，这种行为容易导致文件描述符泄漏
    （特别是与tenacity重试机制结合使用时）。
    我们包装Session以确保它在关闭后不可用，防止资源泄漏。

    Attributes:
        _is_closed (bool): 标识会话是否已关闭
        headers (MutableMapping[str, str]): 默认的HTTP头信息，会在每个请求中使用
    """

    _is_closed: bool = False
    """会话关闭状态标志。
    
    当为True时表示会话已关闭，此时使用会话会记录错误并重置状态。
    """
    
    headers: MutableMapping[str, str] = field(default_factory=dict)
    """默认HTTP头信息。
    
    这些头信息会被添加到每个通过此会话发送的请求中。
    可以用于设置认证信息、用户代理等通用头信息。
    """

    def request(self, *args, **kwargs):
        """发送HTTP请求。

        这是所有其他HTTP方法的基础方法，处理头信息合并和会话状态检查。

        Args:
            *args: 传递给httpx.Client.request的位置参数
            **kwargs: 传递给httpx.Client.request的关键字参数

        Returns:
            httpx.Response: HTTP响应对象

        Note:
            如果会话已关闭，会记录错误并重置关闭状态，然后继续执行请求。
            这种设计允许会话在错误使用后能够恢复。
        """
        if self._is_closed:
            # 记录会话在关闭后被使用的错误，包含堆栈信息和异常信息
            logger.error(
                'Session is being used after close!', stack_info=True, exc_info=True
            )
            # 重置关闭状态，允许会话继续工作
            self._is_closed = False
            
        # 合并默认头信息和请求特定的头信息
        headers = kwargs.get('headers') or {}
        headers = {**self.headers, **headers}  # 默认头信息优先级较低
        kwargs['headers'] = headers
        
        # 使用全局客户端发送请求
        return CLIENT.request(*args, **kwargs)

    def stream(self, *args, **kwargs):
        """发送流式HTTP请求。

        用于处理大文件下载或需要流式处理响应的场景。

        Args:
            *args: 传递给httpx.Client.stream的位置参数
            **kwargs: 传递给httpx.Client.stream的关键字参数

        Returns:
            httpx流响应对象

        Note:
            与request方法类似，也会检查会话状态和合并头信息。
        """
        if self._is_closed:
            logger.error(
                'Session is being used after close!', stack_info=True, exc_info=True
            )
            self._is_closed = False
            
        # 合并头信息
        headers = kwargs.get('headers') or {}
        headers = {**self.headers, **headers}
        kwargs['headers'] = headers
        
        # 返回流式响应
        return CLIENT.stream(*args, **kwargs)

    def get(self, *args, **kwargs):
        """发送GET请求。

        Args:
            *args: URL和其他位置参数
            **kwargs: 其他关键字参数

        Returns:
            httpx.Response: HTTP响应对象
        """
        return self.request('GET', *args, **kwargs)

    def post(self, *args, **kwargs):
        """发送POST请求。

        Args:
            *args: URL和其他位置参数
            **kwargs: 其他关键字参数（如data、json等）

        Returns:
            httpx.Response: HTTP响应对象
        """
        return self.request('POST', *args, **kwargs)

    def patch(self, *args, **kwargs):
        """发送PATCH请求。

        Args:
            *args: URL和其他位置参数
            **kwargs: 其他关键字参数

        Returns:
            httpx.Response: HTTP响应对象
        """
        return self.request('PATCH', *args, **kwargs)

    def put(self, *args, **kwargs):
        """发送PUT请求。

        Args:
            *args: URL和其他位置参数
            **kwargs: 其他关键字参数

        Returns:
            httpx.Response: HTTP响应对象
        """
        return self.request('PUT', *args, **kwargs)

    def delete(self, *args, **kwargs):
        """发送DELETE请求。

        Args:
            *args: URL和其他位置参数
            **kwargs: 其他关键字参数

        Returns:
            httpx.Response: HTTP响应对象
        """
        return self.request('DELETE', *args, **kwargs)

    def options(self, *args, **kwargs):
        """发送OPTIONS请求。

        Args:
            *args: URL和其他位置参数
            **kwargs: 其他关键字参数

        Returns:
            httpx.Response: HTTP响应对象
        """
        return self.request('OPTIONS', *args, **kwargs)

    def close(self) -> None:
        """关闭HTTP会话。

        设置关闭标志，使后续的请求调用会被检测到并记录错误。
        注意：这里不会实际关闭底层的httpx.Client，因为它是全局共享的。

        Note:
            关闭操作主要是设置状态标志，实际的连接管理由全局CLIENT处理。
            这种设计允许多个HttpSession实例共享同一个底层客户端。
        """
        self._is_closed = True
