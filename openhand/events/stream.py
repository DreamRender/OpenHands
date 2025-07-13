import asyncio
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from enum import Enum
from functools import partial
from typing import Any, Callable

from openhands.core.logger import openhands_logger as logger
from openhands.events.event import Event, EventSource
from openhands.events.event_store import EventStore
from openhands.events.serialization.event import event_from_dict, event_to_dict
from openhands.io import json
from openhands.storage import FileStore
from openhands.storage.locations import (
    get_conversation_dir,
)
from openhands.utils.async_utils import call_sync_from_async
from openhands.utils.shutdown_listener import should_continue


class EventStreamSubscriber(str, Enum):
    """
    Event流订阅者枚举类
    
    定义了可以订阅Event流的不同组件类型。每个订阅者都有一个唯一的标识符，
    用于管理回调函数和线程池。
    """
    AGENT_CONTROLLER = 'agent_controller'  # Agent控制器
    SECURITY_ANALYZER = 'security_analyzer'  # 安全分析器
    RESOLVER = 'openhands_resolver'  # OpenHands解析器
    SERVER = 'server'  # 服务器
    RUNTIME = 'runtime'  # 运行时环境
    MEMORY = 'memory'  # 内存管理
    MAIN = 'main'  # 主程序
    TEST = 'test'  # 测试组件


async def session_exists(
    sid: str, file_store: FileStore, user_id: str | None = None
) -> bool:
    """
    检查指定的Session是否存在
    
    通过检查Session对应的目录是否存在来判断Session是否已经创建。
    
    Args:
        sid (str): Session ID
        file_store (FileStore): 文件存储接口
        user_id (str | None): 用户ID，可以为None
        
    Returns:
        bool: 如果Session存在返回True，否则返回False
    """
    try:
        # 尝试列出Session目录
        await call_sync_from_async(file_store.list, get_conversation_dir(sid, user_id))
        return True
    except FileNotFoundError:
        # 如果目录不存在，Session不存在
        return False


class EventStream(EventStore):
    """
    Event流处理类
    
    EventStream继承自EventStore，除了提供Event存储功能外，还实现了Event流的
    实时分发机制。它支持多个订阅者同时监听Event流，并通过线程池来处理回调函数，
    确保Event处理不会阻塞主线程。
    
    Attributes:
        secrets (dict[str, str]): 敏感信息字典，用于在Event中隐藏敏感数据
        _subscribers (dict[str, dict[str, Callable]]): 订阅者映射，每个订阅者ID对应多个回调函数
        _lock (threading.Lock): 线程锁，用于同步访问共享资源
        _queue (queue.Queue[Event]): Event队列，用于异步处理Event
        _queue_thread (threading.Thread): 队列处理线程
        _queue_loop (asyncio.AbstractEventLoop | None): 队列处理线程的事件循环
        _thread_pools (dict[str, dict[str, ThreadPoolExecutor]]): 线程池映射
        _thread_loops (dict[str, dict[str, asyncio.AbstractEventLoop]]): 事件循环映射
        _write_page_cache (list[dict]): 写入页面缓存
    """

    secrets: dict[str, str]  # 敏感信息字典
    _subscribers: dict[str, dict[str, Callable]]  # 订阅者回调函数映射
    _lock: threading.Lock  # 线程锁
    _queue: queue.Queue[Event]  # Event队列
    _queue_thread: threading.Thread  # 队列处理线程
    _queue_loop: asyncio.AbstractEventLoop | None  # 队列事件循环
    _thread_pools: dict[str, dict[str, ThreadPoolExecutor]]  # 线程池映射
    _thread_loops: dict[str, dict[str, asyncio.AbstractEventLoop]]  # 事件循环映射
    _write_page_cache: list[dict]  # 写入缓存页

    def __init__(self, sid: str, file_store: FileStore, user_id: str | None = None):
        """
        初始化EventStream
        
        Args:
            sid (str): Session ID
            file_store (FileStore): 文件存储接口
            user_id (str | None): 用户ID，可以为None
        """
        # 调用父类构造函数
        super().__init__(sid, file_store, user_id)
        
        # 初始化停止标志
        self._stop_flag = threading.Event()
        # 初始化Event队列
        self._queue: queue.Queue[Event] = queue.Queue()
        # 初始化线程池和事件循环映射
        self._thread_pools = {}
        self._thread_loops = {}
        self._queue_loop = None
        
        # 创建并启动队列处理线程
        self._queue_thread = threading.Thread(target=self._run_queue_loop)
        self._queue_thread.daemon = True  # 设置为守护线程
        self._queue_thread.start()
        
        # 初始化订阅者映射和线程锁
        self._subscribers = {}
        self._lock = threading.Lock()
        # 初始化敏感信息字典和写入缓存
        self.secrets = {}
        self._write_page_cache = []

    def _init_thread_loop(self, subscriber_id: str, callback_id: str) -> None:
        """
        为订阅者回调初始化线程事件循环
        
        每个订阅者的每个回调都在独立的线程中运行，拥有自己的事件循环。
        
        Args:
            subscriber_id (str): 订阅者ID
            callback_id (str): 回调函数ID
        """
        # 创建新的事件循环
        loop = asyncio.new_event_loop()
        # 设置为当前线程的事件循环
        asyncio.set_event_loop(loop)
        
        # 确保订阅者在映射中存在
        if subscriber_id not in self._thread_loops:
            self._thread_loops[subscriber_id] = {}
        # 存储事件循环
        self._thread_loops[subscriber_id][callback_id] = loop

    def close(self) -> None:
        """
        关闭EventStream并清理资源
        
        停止所有线程，关闭所有线程池和事件循环，清理队列。
        """
        # 设置停止标志
        self._stop_flag.set()
        
        # 等待队列处理线程结束
        if self._queue_thread.is_alive():
            self._queue_thread.join()

        # 清理所有订阅者
        subscriber_ids = list(self._subscribers.keys())
        for subscriber_id in subscriber_ids:
            callback_ids = list(self._subscribers[subscriber_id].keys())
            for callback_id in callback_ids:
                self._clean_up_subscriber(subscriber_id, callback_id)

        # 清空队列
        while not self._queue.empty():
            self._queue.get()

    def _clean_up_subscriber(self, subscriber_id: str, callback_id: str) -> None:
        """
        清理指定的订阅者回调
        
        关闭对应的线程池和事件循环，移除订阅者映射。
        
        Args:
            subscriber_id (str): 订阅者ID
            callback_id (str): 回调函数ID
        """
        # 检查订阅者是否存在
        if subscriber_id not in self._subscribers:
            logger.warning(f'Subscriber not found during cleanup: {subscriber_id}')
            return
        if callback_id not in self._subscribers[subscriber_id]:
            logger.warning(f'Callback not found during cleanup: {callback_id}')
            return
            
        # 清理事件循环
        if (
            subscriber_id in self._thread_loops
            and callback_id in self._thread_loops[subscriber_id]
        ):
            loop = self._thread_loops[subscriber_id][callback_id]
            # 获取当前任务
            current_task = asyncio.current_task(loop)
            # 获取所有待处理任务（排除当前任务）
            pending = [
                task for task in asyncio.all_tasks(loop) if task is not current_task
            ]
            # 取消所有待处理任务
            for task in pending:
                task.cancel()
            try:
                # 停止并关闭事件循环
                loop.stop()
                loop.close()
            except Exception as e:
                logger.warning(
                    f'Error closing loop for {subscriber_id}/{callback_id}: {e}'
                )
            # 从映射中删除事件循环
            del self._thread_loops[subscriber_id][callback_id]

        # 清理线程池
        if (
            subscriber_id in self._thread_pools
            and callback_id in self._thread_pools[subscriber_id]
        ):
            pool = self._thread_pools[subscriber_id][callback_id]
            # 关闭线程池
            pool.shutdown()
            # 从映射中删除线程池
            del self._thread_pools[subscriber_id][callback_id]

        # 从订阅者映射中删除回调
        del self._subscribers[subscriber_id][callback_id]

    def subscribe(
        self,
        subscriber_id: EventStreamSubscriber,
        callback: Callable[[Event], None],
        callback_id: str,
    ) -> None:
        """
        订阅Event流
        
        注册一个回调函数来处理Event流中的Event。每个回调都在独立的线程中运行。
        
        Args:
            subscriber_id (EventStreamSubscriber): 订阅者ID
            callback (Callable[[Event], None]): 处理Event的回调函数
            callback_id (str): 回调函数的唯一标识符
            
        Raises:
            ValueError: 如果回调ID已经存在
        """
        # 创建线程初始化器
        initializer = partial(self._init_thread_loop, subscriber_id, callback_id)
        # 创建单线程的线程池
        pool = ThreadPoolExecutor(max_workers=1, initializer=initializer)
        
        # 确保订阅者在映射中存在
        if subscriber_id not in self._subscribers:
            self._subscribers[subscriber_id] = {}
            self._thread_pools[subscriber_id] = {}

        # 检查回调ID是否已存在
        if callback_id in self._subscribers[subscriber_id]:
            raise ValueError(
                f'Callback ID on subscriber {subscriber_id} already exists: {callback_id}'
            )

        # 注册回调函数和线程池
        self._subscribers[subscriber_id][callback_id] = callback
        self._thread_pools[subscriber_id][callback_id] = pool

    def unsubscribe(
        self, subscriber_id: EventStreamSubscriber, callback_id: str
    ) -> None:
        """
        取消订阅Event流
        
        移除指定的回调函数并清理相关资源。
        
        Args:
            subscriber_id (EventStreamSubscriber): 订阅者ID
            callback_id (str): 回调函数ID
        """
        # 检查订阅者是否存在
        if subscriber_id not in self._subscribers:
            logger.warning(f'Subscriber not found during unsubscribe: {subscriber_id}')
            return

        # 检查回调是否存在
        if callback_id not in self._subscribers[subscriber_id]:
            logger.warning(f'Callback not found during unsubscribe: {callback_id}')
            return

        # 清理订阅者
        self._clean_up_subscriber(subscriber_id, callback_id)

    def add_event(self, event: Event, source: EventSource) -> None:
        """
        添加新Event到流中
        
        将Event持久化存储并分发给所有订阅者。此方法是线程安全的。
        
        Args:
            event (Event): 要添加的Event对象
            source (EventSource): Event的来源
            
        Raises:
            ValueError: 如果Event已经有ID（可能导致循环）
        """
        # 检查Event是否已经有ID，防止重复添加
        if event.id != Event.INVALID_ID:
            raise ValueError(
                f'Event already has an ID:{event.id}. It was probably added back to the EventStream from inside a handler, triggering a loop.'
            )
            
        # 设置Event的时间戳和来源
        event._timestamp = datetime.now().isoformat()
        event._source = source  # type: ignore [attr-defined]
        
        # 使用线程锁保护临界区
        with self._lock:
            # 分配Event ID并递增计数器
            event._id = self.cur_id  # type: ignore [attr-defined]
            self.cur_id += 1

            # 获取当前写入页面的副本
            current_write_page = self._write_page_cache

            # 将Event转换为字典格式
            data = event_to_dict(event)
            # 替换敏感信息
            data = self._replace_secrets(data)
            # 从处理后的数据重建Event对象
            event = event_from_dict(data)
            # 添加到当前写入页面
            current_write_page.append(data)

            # 如果页面已满，为未来的Event/其他线程创建新页面
            if len(current_write_page) == self.cache_size:
                self._write_page_cache = []

        # 如果Event有有效ID，进行文件写入
        if event.id is not None:
            # 将Event写入存储 - 这可能需要一些时间
            event_json = json.dumps(data)
            filename = self._get_filename_for_id(event.id, self.user_id)
            
            # 检查Event JSON大小，如果超过1MB记录警告
            if len(event_json) > 1_000_000:  # 大约1MB字节，忽略编码
                logger.warning(
                    f'Saving event JSON over 1MB: {len(event_json):,} bytes, filename: {filename}',
                    extra={
                        'user_id': self.user_id,
                        'session_id': self.sid,
                        'size': len(event_json),
                    },
                )
            # 写入Event文件
            self.file_store.write(filename, event_json)

            # 最后存储缓存页 - 如果在读取时不存在，会被简单绕过
            self._store_cache_page(current_write_page)
            
        # 将Event放入队列等待处理
        self._queue.put(event)

    def _store_cache_page(self, current_write_page: list[dict]):
        """
        在缓存中存储一页数据
        
        当有大量Event时，逐个读取Event会很慢，所以使用页面缓存。
        
        Args:
            current_write_page (list[dict]): 要存储的页面数据
        """
        # 如果页面大小不足，不存储
        if len(current_write_page) < self.cache_size:
            return
            
        # 计算页面范围
        start = current_write_page[0]['id']
        end = start + self.cache_size
        
        # 将页面数据序列化为JSON
        contents = json.dumps(current_write_page)
        # 获取缓存文件名
        cache_filename = self._get_filename_for_cache(start, end)
        # 写入缓存文件
        self.file_store.write(cache_filename, contents)

    def set_secrets(self, secrets: dict[str, str]) -> None:
        """
        设置敏感信息字典
        
        Args:
            secrets (dict[str, str]): 敏感信息映射
        """
        self.secrets = secrets.copy()

    def update_secrets(self, secrets: dict[str, str]) -> None:
        """
        更新敏感信息字典
        
        Args:
            secrets (dict[str, str]): 要更新的敏感信息映射
        """
        self.secrets.update(secrets)

    def _replace_secrets(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        递归替换数据中的敏感信息
        
        遍历数据结构，将所有敏感信息替换为'<secret_hidden>'字符串。
        
        Args:
            data (dict[str, Any]): 要处理的数据字典
            
        Returns:
            dict[str, Any]: 处理后的数据字典
        """
        for key in data:
            if isinstance(data[key], dict):
                # 递归处理嵌套字典
                data[key] = self._replace_secrets(data[key])
            elif isinstance(data[key], str):
                # 替换字符串中的敏感信息
                for secret in self.secrets.values():
                    data[key] = data[key].replace(secret, '<secret_hidden>')
        return data

    def _run_queue_loop(self) -> None:
        """
        运行队列处理循环
        
        在独立线程中运行，处理Event队列中的Event。
        """
        # 创建新的事件循环
        self._queue_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._queue_loop)
        try:
            # 运行队列处理协程
            self._queue_loop.run_until_complete(self._process_queue())
        finally:
            # 关闭事件循环
            self._queue_loop.close()

    async def _process_queue(self) -> None:
        """
        处理Event队列中的Event
        
        从队列中取出Event并分发给所有订阅者的回调函数。
        """
        # 持续处理直到收到停止信号
        while should_continue() and not self._stop_flag.is_set():
            event = None
            try:
                # 尝试从队列获取Event（超时0.1秒）
                event = self._queue.get(timeout=0.1)
            except queue.Empty:
                # 队列为空，继续循环
                continue

            # 按订阅者ID排序，将每个Event传递给每个回调
            for key in sorted(self._subscribers.keys()):
                callbacks = self._subscribers[key]
                # 创建回调ID列表的副本，避免"字典在迭代期间改变大小"错误
                callback_ids = list(callbacks.keys())
                for callback_id in callback_ids:
                    # 检查callback_id是否仍然存在（可能在迭代期间被移除）
                    if callback_id in callbacks:
                        callback = callbacks[callback_id]
                        pool = self._thread_pools[key][callback_id]
                        # 在线程池中提交回调任务
                        future = pool.submit(callback, event)
                        # 添加错误处理回调
                        future.add_done_callback(
                            self._make_error_handler(callback_id, key)
                        )

    def _make_error_handler(
        self, callback_id: str, subscriber_id: str
    ) -> Callable[[Any], None]:
        """
        创建错误处理函数
        
        为回调函数的Future对象创建错误处理器。
        
        Args:
            callback_id (str): 回调函数ID
            subscriber_id (str): 订阅者ID
            
        Returns:
            Callable[[Any], None]: 错误处理函数
        """
        def _handle_callback_error(fut: Any) -> None:
            """
            处理回调函数中的错误
            
            Args:
                fut: Future对象
                
            Raises:
                Exception: 重新抛出回调执行期间发生的异常
            """
            try:
                # 这会抛出回调执行期间发生的任何异常
                fut.result()
            except Exception as e:
                logger.error(
                    f'Error in event callback {callback_id} for subscriber {subscriber_id}: {str(e)}',
                )
                # 在主线程中重新抛出错误，避免错误被吞没
                raise e

        return _handle_callback_error