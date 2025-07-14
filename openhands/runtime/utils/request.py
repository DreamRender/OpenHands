"""
HTTP请求处理模块。

该模块提供了带有重试机制的HTTP请求功能，以及自定义的HTTP错误处理。
"""

import json
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from openhands.utils.http_session import HttpSession
from openhands.utils.tenacity_stop import stop_if_should_exit


class RequestHTTPError(httpx.HTTPStatusError):
    """
    当请求发生错误时抛出的异常，包含详细信息。
    
    该异常类继承自httpx.HTTPStatusError，并添加了额外的详细信息字段。
    """

    def __init__(self, *args: Any, detail: Any = None, **kwargs: Any) -> None:
        """
        初始化RequestHTTPError异常。
        
        Args:
            *args: 传递给父类的位置参数
            detail (Any): 错误的详细信息，默认为None
            **kwargs: 传递给父类的关键字参数
        """
        super().__init__(*args, **kwargs)
        # 存储错误的详细信息
        self.detail = detail

    def __str__(self) -> str:
        """
        返回异常的字符串表示形式。
        
        Returns:
            str: 异常的字符串描述，包含详细信息（如果有的话）
        """
        # 获取父类的字符串表示
        s = super().__str__()
        # 如果有详细信息，则添加到字符串中
        if self.detail is not None:
            s += f'\nDetails: {self.detail}'
            # 中文说明：详细信息
        return str(s)


def is_retryable_error(exception: Any) -> bool:
    """
    判断异常是否可重试。
    
    Args:
        exception (Any): 要检查的异常对象
    
    Returns:
        bool: 如果异常是可重试的HTTP状态错误（状态码429），返回True；否则返回False
    """
    # 只有当异常是HTTPStatusError且状态码为429（Too Many Requests）时才重试
    return (
        isinstance(exception, httpx.HTTPStatusError)
        and exception.response.status_code == 429
    )


@retry(
    # 只有当异常满足is_retryable_error条件时才重试
    retry=retry_if_exception(is_retryable_error),
    # 最多重试3次，或者当应该退出时停止
    stop=stop_after_attempt(3) | stop_if_should_exit(),
    # 使用指数退避策略，基础间隔1秒，最小4秒，最大60秒
    wait=wait_exponential(multiplier=1, min=4, max=60),
)
def send_request(
    session: HttpSession,
    method: str,
    url: str,
    timeout: int = 60,
    **kwargs: Any,
) -> httpx.Response:
    """
    发送HTTP请求，具有自动重试机制。
    
    该函数使用tenacity库提供的重试装饰器，在遇到可重试的错误时会自动重试。
    
    Args:
        session (HttpSession): HTTP会话对象
        method (str): HTTP方法（如GET、POST等）
        url (str): 请求的URL
        timeout (int): 请求超时时间（秒），默认为60秒
        **kwargs: 传递给HTTP请求的其他参数
    
    Returns:
        httpx.Response: HTTP响应对象
    
    Raises:
        RequestHTTPError: 当HTTP请求失败时抛出，包含详细的错误信息
    """
    # 使用会话发送HTTP请求
    response = session.request(method, url, timeout=timeout, **kwargs)
    
    try:
        # 检查响应状态，如果状态码表示错误则抛出异常
        response.raise_for_status()
    except httpx.HTTPError as e:
        # 尝试从响应中解析JSON错误详情
        try:
            _json = response.json()
        except json.decoder.JSONDecodeError:
            # 如果JSON解析失败，将详情设为None
            _json = None
        finally:
            # 关闭响应连接
            response.close()
        
        # 抛出自定义的HTTP错误异常，包含详细信息
        raise RequestHTTPError(
            e,
            request=e.request,
            response=e.response,
            # 如果JSON解析成功且包含'detail'字段，则提取详细信息
            detail=_json.get('detail') if _json is not None else None,
        ) from e
    
    return response
