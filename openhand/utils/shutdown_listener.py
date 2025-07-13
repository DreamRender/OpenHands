"""
系统关闭信号监听模块。

此模块监听应用程序的关闭信号。之所以存在这个模块，是因为Python的atexit模块
与Starlette/Uvicorn的关闭信号处理机制不能很好地协同工作。

这个模块提供了一个统一的关闭信号处理机制，确保所有组件都能正确响应系统关闭请求。
"""

import asyncio
import signal
import threading
import time
from types import FrameType
from typing import Callable
from uuid import UUID, uuid4

from uvicorn.server import HANDLED_SIGNALS

from openhands.core.logger import openhands_logger as logger

# 全局变量：标识系统是否应该退出
_should_exit = None

# 全局变量：存储所有注册的关闭监听器回调函数
# 使用UUID作为键，便于后续移除特定的监听器
_shutdown_listeners: dict[UUID, Callable] = {}


def _register_signal_handler(sig: signal.Signals) -> None:
    """为指定信号注册处理器。

    为给定的信号注册一个处理函数，当接收到该信号时，
    会设置should_exit标志并调用所有注册的关闭监听器。

    Args:
        sig (signal.Signals): 需要处理的信号类型（如SIGTERM、SIGINT等）

    Note:
        此函数会保存原始的信号处理器，并在处理完自定义逻辑后调用原始处理器。
    """
    original_handler = None

    def handler(sig_: int, frame: FrameType | None) -> None:
        """信号处理器函数。

        当接收到信号时被调用，执行以下操作：
        1. 记录接收到的信号
        2. 设置全局退出标志
        3. 依次调用所有注册的关闭监听器
        4. 调用原始的信号处理器（如果存在）

        Args:
            sig_ (int): 接收到的信号编号
            frame (FrameType | None): 当前执行帧的信息，可能为None
        """
        logger.debug(f'shutdown_signal:{sig_}')
        global _should_exit
        if not _should_exit:
            _should_exit = True
            # 获取所有监听器的副本，避免在迭代过程中字典被修改
            listeners = list(_shutdown_listeners.values())
            for callable in listeners:
                try:
                    callable()
                except Exception:
                    # 记录异常但不中断其他监听器的执行
                    logger.exception('Error calling shutdown listener')
            if original_handler:
                original_handler(sig_, frame)  # type: ignore[unreachable]

    # 注册新的信号处理器，并保存原始处理器
    original_handler = signal.signal(sig, handler)


def _register_signal_handlers() -> None:
    """注册所有支持的信号处理器。

    初始化信号处理系统，为所有Uvicorn支持的信号注册处理器。
    只有在主线程中才能注册信号处理器，这是Python信号处理的限制。

    Note:
        此函数确保只初始化一次，通过检查_should_exit是否为None来判断。
        如果不在主线程中，会记录调试信息但不注册信号处理器。
    """
    global _should_exit
    if _should_exit is not None:
        return
    _should_exit = False

    logger.debug('_register_signal_handlers')

    # 检查是否在主进程的主线程中
    # 只有主线程才能注册信号处理器
    if threading.current_thread() is threading.main_thread():
        logger.debug('_register_signal_handlers:main_thread')
        # 为所有Uvicorn处理的信号注册处理器
        for sig in HANDLED_SIGNALS:
            _register_signal_handler(sig)
    else:
        logger.debug('_register_signal_handlers:not_main_thread')


def should_exit() -> bool:
    """检查系统是否应该退出。

    这是一个公共接口，用于查询系统是否接收到了关闭信号。
    会自动初始化信号处理器（如果尚未初始化）。

    Returns:
        bool: 如果系统应该退出返回True，否则返回False

    Note:
        此函数是线程安全的，可以在任何线程中调用。
    """
    _register_signal_handlers()
    return bool(_should_exit)


def should_continue() -> bool:
    """检查系统是否应该继续运行。

    这是should_exit()的反向版本，提供更直观的语义。
    当系统没有接收到关闭信号时返回True。

    Returns:
        bool: 如果系统应该继续运行返回True，否则返回False
    """
    _register_signal_handlers()
    return not _should_exit


def sleep_if_should_continue(timeout: float) -> None:
    """在系统应该继续运行时进行睡眠等待。

    这是一个可中断的睡眠函数，会定期检查系统是否接收到关闭信号。
    如果在睡眠期间接收到关闭信号，会立即返回而不是等待完整的超时时间。

    Args:
        timeout (float): 睡眠的最大时间（秒）

    Note:
        - 对于小于等于1秒的超时，直接使用time.sleep()
        - 对于较长的超时，每秒检查一次是否应该继续
        - 这样设计可以确保响应性，避免长时间阻塞
    """
    if timeout <= 1:
        time.sleep(timeout)
        return
    start_time = time.time()
    # 每秒检查一次是否应该继续，确保及时响应关闭信号
    while (time.time() - start_time) < timeout and should_continue():
        time.sleep(1)


async def async_sleep_if_should_continue(timeout: float) -> None:
    """异步版本的可中断睡眠函数。

    功能与sleep_if_should_continue相同，但使用异步睡眠，
    不会阻塞事件循环，适用于异步代码中使用。

    Args:
        timeout (float): 睡眠的最大时间（秒）

    Note:
        使用asyncio.sleep()而不是time.sleep()，确保不阻塞事件循环。
    """
    if timeout <= 1:
        await asyncio.sleep(timeout)
        return
    start_time = time.time()
    # 异步等待，每秒检查一次是否应该继续
    while time.time() - start_time < timeout and should_continue():
        await asyncio.sleep(1)


def add_shutdown_listener(callable: Callable) -> UUID:
    """添加一个关闭监听器。

    注册一个回调函数，当系统接收到关闭信号时会被调用。
    这允许组件在系统关闭时执行清理操作。

    Args:
        callable (Callable): 关闭时要调用的回调函数，
                           该函数不应该接受任何参数

    Returns:
        UUID: 监听器的唯一标识符，可用于后续移除该监听器

    Example:
        >>> def cleanup():
        ...     print("正在清理资源...")
        >>> listener_id = add_shutdown_listener(cleanup)
        >>> # 稍后可以移除监听器
        >>> remove_shutdown_listener(listener_id)
    """
    id_ = uuid4()
    _shutdown_listeners[id_] = callable
    return id_


def remove_shutdown_listener(id_: UUID) -> bool:
    """移除指定的关闭监听器。

    根据之前添加监听器时返回的UUID，移除对应的关闭监听器。

    Args:
        id_ (UUID): 要移除的监听器的唯一标识符

    Returns:
        bool: 如果成功移除监听器返回True，如果找不到对应的监听器返回False

    Note:
        如果提供的UUID不存在，函数会安全地返回False而不会抛出异常。
    """
    return _shutdown_listeners.pop(id_, None) is not None
