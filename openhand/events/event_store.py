import json
from dataclasses import dataclass
from typing import Iterable

from openhands.core.logger import openhands_logger as logger
from openhands.events.event import Event, EventSource
from openhands.events.event_filter import EventFilter
from openhands.events.event_store_abc import EventStoreABC
from openhands.events.serialization.event import event_from_dict
from openhands.storage.files import FileStore
from openhands.storage.locations import (
    get_conversation_dir,
    get_conversation_event_filename,
    get_conversation_events_dir,
)
from openhands.utils.shutdown_listener import should_continue


@dataclass(frozen=True)
class _CachePage:
    """
    缓存页数据类
    
    用于存储一页Event数据的缓存信息。为了提高性能，当有大量Event时，
    逐个读取Event会很慢，所以使用页面缓存机制。
    
    Attributes:
        events (list[dict] | None): Event数据列表，如果没有缓存则为None
        start (int): 此页面的起始Event ID
        end (int): 此页面的结束Event ID（不包含）
    """
    events: list[dict] | None  # Event数据列表
    start: int  # 页面起始ID
    end: int    # 页面结束ID

    def covers(self, global_index: int) -> bool:
        """
        检查此缓存页是否覆盖指定的全局索引
        
        Args:
            global_index (int): 要检查的全局Event索引
            
        Returns:
            bool: 如果此页面覆盖指定索引则返回True，否则返回False
        """
        # 如果索引小于起始位置，不覆盖
        if global_index < self.start:
            return False
        # 如果索引大于等于结束位置，不覆盖
        if global_index >= self.end:
            return False
        return True

    def get_event(self, global_index: int) -> Event | None:
        """
        从缓存页中获取指定索引的Event
        
        Args:
            global_index (int): 要获取的Event的全局索引
            
        Returns:
            Event | None: 如果找到Event则返回Event对象，否则返回None
        """
        # 如果没有实际的缓存页数据，返回None
        if not self.events:
            return None
        # 计算在页面内的本地索引
        local_index = global_index - self.start
        # 从字典数据重建Event对象并返回
        return event_from_dict(self.events[local_index])


# 虚拟缓存页常量，用于表示无效的缓存页
_DUMMY_PAGE = _CachePage(None, 1, -1)


@dataclass
class EventStore(EventStoreABC):
    """
    支持会话的Event存储列表
    
    EventStore是Event存储的主要实现，提供了Event的持久化存储、
    检索和缓存功能。它使用文件系统来存储Event数据，并实现了
    页面缓存机制来提高读取性能。
    
    Attributes:
        sid (str): Session ID，用于标识会话
        file_store (FileStore): 文件存储接口
        user_id (str | None): 用户ID，可以为None
        cache_size (int): 缓存页面大小，默认为25
        _cur_id (int | None): 当前Event ID的私有字段，用于缓存计算值
    """

    sid: str  # Session ID
    file_store: FileStore  # 文件存储接口
    user_id: str | None  # 用户ID
    cache_size: int = 25  # 缓存页面大小
    _cur_id: int | None = None  # 当前Event ID的私有缓存字段

    @property
    def cur_id(self) -> int:
        """
        当前Event ID的延迟计算属性
        
        Returns:
            int: 下一个要分配的Event ID
        """
        # 如果还没有计算过，则进行计算
        if self._cur_id is None:
            self._cur_id = self._calculate_cur_id()
        return self._cur_id

    @cur_id.setter
    def cur_id(self, value: int) -> None:
        """
        设置当前Event ID
        
        Args:
            value (int): 要设置的Event ID值
        """
        self._cur_id = value

    def _calculate_cur_id(self) -> int:
        """
        基于文件系统内容计算当前Event ID
        
        通过扫描Event目录中的文件来确定下一个可用的Event ID。
        
        Returns:
            int: 下一个可用的Event ID
        """
        events = []
        try:
            # 获取Event目录路径
            events_dir = get_conversation_events_dir(self.sid, self.user_id)
            # 列出Event目录中的所有文件
            events = self.file_store.list(events_dir)
        except FileNotFoundError:
            # 如果目录不存在，记录调试信息
            logger.debug(f'No events found for session {self.sid} at {events_dir}')

        # 如果没有Event文件，返回0作为起始ID
        if not events:
            return 0

        # 如果有Event文件，需要找到最高的ID来为新Event准备
        max_id = -1
        for event_str in events:
            # 从文件名中提取Event ID
            id = self._get_id_from_filename(event_str)
            if id >= max_id:
                max_id = id
        # 返回最大ID加1作为下一个可用ID
        return max_id + 1

    def search_events(
        self,
        start_id: int = 0,
        end_id: int | None = None,
        reverse: bool = False,
        filter: EventFilter | None = None,
        limit: int | None = None,
    ) -> Iterable[Event]:
        """
        从Event流中检索Event，可选择过滤掉给定类型的Event和标记为隐藏的Event
        
        Args:
            start_id (int): 要检索的第一个Event的ID。默认为0。
            end_id (int | None): 要检索的最后一个Event的ID。默认为流中的最后一个Event。
            reverse (bool): 是否以相反顺序检索Event。默认为False。
            filter (EventFilter | None): 要使用的EventFilter过滤器
            limit (int | None): 最大返回Event数量限制

        Yields:
            Event: 匹配条件的Event流中的Event
        """

        # 如果没有指定结束ID，使用当前ID
        if end_id is None:
            end_id = self.cur_id
        else:
            end_id += 1  # 从包含转换为排除

        # 处理反向遍历的参数调整
        if reverse:
            step = -1  # 反向步长
            start_id, end_id = end_id, start_id  # 交换起始和结束位置
            start_id -= 1
            end_id -= 1
        else:
            step = 1  # 正向步长

        # 初始化为虚拟缓存页
        cache_page = _DUMMY_PAGE
        num_results = 0  # 结果计数器
        
        # 遍历指定范围内的Event ID
        for index in range(start_id, end_id, step):
            # 检查是否应该继续执行（用于优雅关闭）
            if not should_continue():
                return
                
            # 如果当前缓存页不覆盖此索引，加载新的缓存页
            if not cache_page.covers(index):
                cache_page = self._load_cache_page_for_index(index)
                
            # 尝试从缓存页获取Event
            event = cache_page.get_event(index)
            if event is None:
                try:
                    # 如果缓存中没有，直接从文件获取Event
                    event = self.get_event(index)
                except FileNotFoundError:
                    # 如果文件不存在，设置为None
                    event = None
                    
            # 如果成功获取到Event
            if event:
                # 应用过滤器（如果有）
                if not filter or filter.include(event):
                    yield event
                    num_results += 1
                    # 检查是否达到限制数量
                    if limit and limit <= num_results:
                        return

    def get_event(self, id: int) -> Event:
        """
        获取指定ID的单个Event
        
        Args:
            id (int): 要获取的Event ID
            
        Returns:
            Event: 对应ID的Event对象
            
        Raises:
            FileNotFoundError: 如果指定ID的Event不存在
        """
        # 获取Event文件名
        filename = self._get_filename_for_id(id, self.user_id)
        # 读取文件内容
        content = self.file_store.read(filename)
        # 解析JSON数据
        data = json.loads(content)
        # 从字典重建Event对象
        return event_from_dict(data)

    def get_latest_event(self) -> Event:
        """
        获取最新的Event
        
        Returns:
            Event: 最新的Event对象
        """
        # 获取当前ID减1的Event（最新的Event）
        return self.get_event(self.cur_id - 1)

    def get_latest_event_id(self) -> int:
        """
        获取最新Event的ID
        
        Returns:
            int: 最新Event的ID
        """
        # 返回当前ID减1（最新Event的ID）
        return self.cur_id - 1

    def filtered_events_by_source(self, source: EventSource) -> Iterable[Event]:
        """
        根据来源过滤Event
        
        Args:
            source (EventSource): 要过滤的Event来源
            
        Yields:
            Event: 匹配指定来源的Event
        """
        # 遍历所有Event并过滤指定来源
        for event in self.search_events():
            if event.source == source:
                yield event

    def _get_filename_for_id(self, id: int, user_id: str | None) -> str:
        """
        获取指定Event ID的文件名
        
        Args:
            id (int): Event ID
            user_id (str | None): 用户ID
            
        Returns:
            str: Event文件的完整路径
        """
        return get_conversation_event_filename(self.sid, id, user_id)

    def _get_filename_for_cache(self, start: int, end: int) -> str:
        """
        获取缓存文件的文件名
        
        Args:
            start (int): 缓存页起始ID
            end (int): 缓存页结束ID
            
        Returns:
            str: 缓存文件的完整路径
        """
        return f'{get_conversation_dir(self.sid, self.user_id)}event_cache/{start}-{end}.json'

    def _load_cache_page(self, start: int, end: int) -> _CachePage:
        """
        从缓存中读取一页数据
        
        当有大量Event时，逐个读取Event会很慢，所以使用页面缓存。
        
        Args:
            start (int): 页面起始ID
            end (int): 页面结束ID
            
        Returns:
            _CachePage: 缓存页对象
        """
        # 获取缓存文件名
        cache_filename = self._get_filename_for_cache(start, end)
        try:
            # 尝试读取缓存文件
            content = self.file_store.read(cache_filename)
            events = json.loads(content)
        except FileNotFoundError:
            # 如果缓存文件不存在，设置为None
            events = None
        # 创建并返回缓存页对象
        page = _CachePage(events, start, end)
        return page

    def _load_cache_page_for_index(self, index: int) -> _CachePage:
        """
        为指定索引加载缓存页
        
        根据缓存页大小计算应该加载哪个缓存页。
        
        Args:
            index (int): 要加载的Event索引
            
        Returns:
            _CachePage: 包含指定索引的缓存页
        """
        # 计算在页面内的偏移量
        offset = index % self.cache_size
        # 调整索引到页面边界
        index -= offset
        # 加载对应的缓存页
        return self._load_cache_page(index, index + self.cache_size)

    @staticmethod
    def _get_id_from_filename(filename: str) -> int:
        """
        从文件名中提取Event ID
        
        Args:
            filename (str): Event文件名
            
        Returns:
            int: 提取的Event ID，如果提取失败返回-1
        """
        try:
            # 从文件名中提取ID：分割路径，取文件名，分割扩展名，转换为整数
            return int(filename.split('/')[-1].split('.')[0])
        except ValueError:
            # 如果转换失败，记录警告并返回-1
            logger.warning(f'get id from filename ({filename}) failed.')
            return -1