import asyncio
from typing import Any, AsyncIterator

from openhands.events.event import Event
from openhands.events.event_store import EventStore


class AsyncEventStoreWrapper:
    """
    异步Event Store包装器类
    
    这个类将同步的EventStore包装成异步接口，使得可以在异步环境中
    使用EventStore的功能，通过线程池执行器来避免阻塞事件循环。
    
    Attributes:
        event_store (EventStore): 被包装的同步EventStore实例
        args (tuple): 传递给search_events方法的位置参数
        kwargs (dict): 传递给search_events方法的关键字参数
    """
    
    def __init__(self, event_store: EventStore, *args: Any, **kwargs: Any) -> None:
        """
        初始化异步EventStore包装器
        
        Args:
            event_store (EventStore): 要包装的EventStore实例
            *args: 传递给search_events方法的位置参数
            **kwargs: 传递给search_events方法的关键字参数
        """
        self.event_store = event_store  # 存储EventStore实例
        self.args = args  # 存储位置参数
        self.kwargs = kwargs  # 存储关键字参数

    async def __aiter__(self) -> AsyncIterator[Event]:
        """
        异步迭代器方法，使该类可以在async for循环中使用
        
        通过将同步的search_events操作放在线程池中执行，
        避免阻塞主事件循环，实现真正的异步迭代。
        
        Yields:
            Event: 从EventStore中检索到的Event对象
        """
        # 获取当前运行的事件循环
        loop = asyncio.get_running_loop()

        # 创建一个异步生成器来yield事件
        # 遍历EventStore中的所有事件
        for event in self.event_store.search_events(*self.args, **self.kwargs):
            # 在线程池中运行阻塞的search_events()方法
            # 这里使用闭包来确保每个event变量的值被正确捕获
            def get_event(e: Event = event) -> Event:
                """
                简单的事件获取函数，用于在线程池中执行
                
                Args:
                    e (Event): 要返回的Event对象，默认为当前迭代的event
                    
                Returns:
                    Event: 传入的Event对象
                """
                return e

            # 使用线程池执行器运行get_event函数，避免阻塞事件循环
            yield await loop.run_in_executor(None, get_event)