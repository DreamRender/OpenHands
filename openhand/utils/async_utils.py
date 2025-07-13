import asyncio
from concurrent import futures
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Coroutine, Iterable

# 全局常量：通用超时时间（秒）
GENERAL_TIMEOUT: int = 15

# 全局线程池执行器，用于在后台线程中运行同步或异步函数
EXECUTOR = ThreadPoolExecutor()


async def call_sync_from_async(fn: Callable, *args, **kwargs):
    """从异步上下文中调用同步函数的简化方法。
    
    在默认后台线程池执行器中运行同步函数并等待结果。
    由于同步代码的特性，此函数返回的future是不可取消的。

    Args:
        fn (Callable): 要在后台线程中执行的同步函数
        *args: 传递给函数的位置参数
        **kwargs: 传递给函数的关键字参数

    Returns:
        函数执行的结果

    Note:
        此函数适用于需要在异步代码中调用阻塞的同步函数的场景，
        如文件I/O、数据库操作等。
    """
    # 获取当前事件循环
    loop = asyncio.get_event_loop()
    
    # 在默认线程池中运行同步函数
    coro = loop.run_in_executor(None, lambda: fn(*args, **kwargs))
    result = await coro
    return result


def call_async_from_sync(
    corofn: Callable, timeout: float = GENERAL_TIMEOUT, *args, **kwargs
):
    """从同步上下文中调用协程函数的简化方法。
    
    在后台线程池执行器中运行协程并等待结果。
    这允许同步代码调用异步函数。

    Args:
        corofn (Callable): 要执行的协程函数
        timeout (float, optional): 超时时间（秒）。默认为GENERAL_TIMEOUT
        *args: 传递给协程函数的位置参数
        **kwargs: 传递给协程函数的关键字参数

    Returns:
        协程执行的结果

    Raises:
        ValueError: 如果corofn为None或不是协程函数
        TimeoutError: 如果执行超时

    Note:
        此函数为每个调用创建一个新的事件循环，
        适用于从同步代码中调用异步API的场景。
    """
    if corofn is None:
        raise ValueError('corofn is None')
    if not asyncio.iscoroutinefunction(corofn):
        raise ValueError('corofn is not a coroutine function')

    async def arun():
        """内部异步运行函数。"""
        coro = corofn(*args, **kwargs)
        result = await coro
        return result

    def run():
        """在新事件循环中运行协程的函数。"""
        # 为当前线程创建新的事件循环
        loop_for_thread = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop_for_thread)
            return asyncio.run(arun())
        finally:
            # 确保事件循环被正确关闭
            loop_for_thread.close()

    # 如果执行器已关闭，直接在当前线程运行
    if getattr(EXECUTOR, '_shutdown', False):
        result = run()
        return result

    # 在线程池中提交任务并等待结果
    future = EXECUTOR.submit(run)
    futures.wait([future], timeout=timeout or None)
    result = future.result()
    return result


async def call_coro_in_bg_thread(
    corofn: Callable, timeout: float = GENERAL_TIMEOUT, *args, **kwargs
):
    """在后台线程中运行协程的函数。
    
    这是一个异步函数，用于在后台线程中执行协程。
    
    Args:
        corofn (Callable): 要执行的协程函数
        timeout (float, optional): 超时时间（秒）。默认为GENERAL_TIMEOUT
        *args: 传递给协程函数的位置参数
        **kwargs: 传递给协程函数的关键字参数

    Note:
        这个函数结合了call_sync_from_async和call_async_from_sync的功能，
        允许在当前异步上下文中的后台线程里运行另一个协程。
    """
    await call_sync_from_async(call_async_from_sync, corofn, timeout, *args, **kwargs)


async def wait_all(
    iterable: Iterable[Coroutine], timeout: int = GENERAL_TIMEOUT
) -> list:
    """并行等待可迭代对象中的所有协程的简化方法。
    
    为每个协程创建任务并等待所有任务完成。
    按原始顺序返回结果列表。如果任何单个任务引发异常，将抛出该异常。
    如果多个任务引发异常，将抛出包含所有异常的AsyncException。

    Args:
        iterable (Iterable[Coroutine]): 要等待的协程的可迭代对象
        timeout (int, optional): 超时时间（秒）。默认为GENERAL_TIMEOUT

    Returns:
        list: 按原始顺序排列的结果列表

    Raises:
        asyncio.TimeoutError: 如果操作超时
        Exception: 如果任何任务引发异常
        AsyncException: 如果多个任务引发异常

    Note:
        此函数确保所有任务都会被适当处理，
        超时时会取消所有待处理的任务。
    """
    # 为每个协程创建任务
    tasks = [asyncio.create_task(c) for c in iterable]
    if not tasks:
        return []
        
    # 等待所有任务完成或超时
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    
    # 如果有任务超时，取消所有待处理的任务
    if pending:
        for task in pending:
            task.cancel()
        raise asyncio.TimeoutError()
    
    # 收集结果和异常
    results = []
    errors = []
    for task in tasks:
        try:
            results.append(task.result())
        except Exception as e:
            errors.append(e)
    
    # 处理异常情况
    if errors:
        if len(errors) == 1:
            # 如果只有一个异常，直接抛出
            raise errors[0]
        # 如果有多个异常，抛出聚合异常
        raise AsyncException(errors)
    
    # 返回所有任务的结果
    return [task.result() for task in tasks]


class AsyncException(Exception):
    """聚合异常类。
    
    用于包装多个并发任务中发生的异常。
    """
    
    def __init__(self, exceptions):
        """初始化聚合异常。

        Args:
            exceptions (list): 异常列表
        """
        self.exceptions = exceptions

    def __str__(self):
        """返回所有异常的字符串表示。

        Returns:
            str: 每个异常占一行的字符串
        """
        return '\n'.join(str(e) for e in self.exceptions)


async def run_in_loop(
    coro: Coroutine, loop: asyncio.AbstractEventLoop, timeout: float = GENERAL_TIMEOUT
):
    """缓解"协程在不同事件循环中创建"错误的工具函数。
    
    如果需要，将协程传递给不同的事件循环执行。

    Args:
        coro (Coroutine): 要执行的协程
        loop (asyncio.AbstractEventLoop): 目标事件循环
        timeout (float, optional): 超时时间（秒）。默认为GENERAL_TIMEOUT

    Returns:
        协程执行的结果

    Note:
        如果当前运行的事件循环与目标循环相同，直接执行协程；
        否则使用线程安全的方式在目标循环中执行协程。
    """
    # 获取当前运行的事件循环
    running_loop = asyncio.get_running_loop()
    
    # 如果是同一个事件循环，直接执行
    if running_loop == loop:
        result = await coro
        return result

    # 在不同的事件循环中执行协程
    result = await call_sync_from_async(_run_in_loop, coro, loop, timeout)
    return result


def _run_in_loop(coro: Coroutine, loop: asyncio.AbstractEventLoop, timeout: float):
    """在指定事件循环中运行协程的同步函数。

    Args:
        coro (Coroutine): 要执行的协程
        loop (asyncio.AbstractEventLoop): 目标事件循环
        timeout (float): 超时时间（秒）

    Returns:
        协程执行的结果

    Note:
        使用asyncio.run_coroutine_threadsafe实现线程安全的跨循环调用。
    """
    # 在目标事件循环中安全地运行协程
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    result = future.result(timeout=timeout)
    return result
