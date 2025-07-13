from abc import abstractmethod
from itertools import islice
from typing import Iterable

from deprecated import deprecated  # type: ignore

from openhands.events.event import Event, EventSource
from openhands.events.event_filter import EventFilter


class EventStoreABC:
    """
    支持会话的Event存储列表抽象基类
    
    定义了EventStore的基本接口，所有具体的EventStore实现都应该继承此类
    并实现其抽象方法。这个类提供了Event存储和检索的标准接口。
    
    Attributes:
        sid (str): Session ID，用于标识会话
        user_id (str | None): 用户ID，可以为None
    """

    sid: str  # Session ID
    user_id: str | None  # 用户ID

    @abstractmethod
    def search_events(
        self,
        start_id: int = 0,
        end_id: int | None = None,
        reverse: bool = False,
        filter: EventFilter | None = None,
        limit: int | None = None,
    ) -> Iterable[Event]:
        """
        从Event流中检索Event，可选择使用过滤器排除Event
        
        这是一个抽象方法，必须在子类中实现具体的Event搜索逻辑。
        
        Args:
            start_id (int): 要检索的第一个Event的ID。默认为0。
            end_id (int | None): 要检索的最后一个Event的ID。默认为流中的最后一个Event。
            reverse (bool): 是否以相反顺序检索Event。默认为False。
            filter (EventFilter | None): 可选的Event过滤器

        Yields:
            Event: 匹配条件的Event流中的Event
        """

    @deprecated('Use search_events instead')
    def get_events(
        self,
        start_id: int = 0,
        end_id: int | None = None,
        reverse: bool = False,
        filter_out_type: tuple[type[Event], ...] | None = None,
        filter_hidden: bool = False,
    ) -> Iterable[Event]:
        """
        获取Event列表（已弃用方法）
        
        这个方法已被弃用，建议使用search_events方法代替。
        内部实现通过调用search_events来保持向后兼容性。
        
        Args:
            start_id (int): 起始Event ID
            end_id (int | None): 结束Event ID
            reverse (bool): 是否反向遍历
            filter_out_type (tuple[type[Event], ...] | None): 要过滤掉的Event类型
            filter_hidden (bool): 是否过滤隐藏的Event
            
        Yields:
            Event: 匹配条件的Event
        """
        # 使用新的search_events方法并传递相应的过滤器参数
        yield from self.search_events(
            start_id,
            end_id,
            reverse,
            EventFilter(exclude_types=filter_out_type, exclude_hidden=filter_hidden),
        )

    @abstractmethod
    def get_event(self, id: int) -> Event:
        """
        从Event流中检索单个Event
        
        这是一个抽象方法，必须在子类中实现。如果不存在对应的Event，
        应该抛出FileNotFoundError异常。
        
        Args:
            id (int): 要检索的Event ID
            
        Returns:
            Event: 对应ID的Event对象
            
        Raises:
            FileNotFoundError: 如果不存在对应的Event
        """

    @abstractmethod
    def get_latest_event(self) -> Event:
        """
        从Event流中获取最新的Event
        
        这是一个抽象方法，必须在子类中实现。
        
        Returns:
            Event: 最新的Event对象
        """

    @abstractmethod
    def get_latest_event_id(self) -> int:
        """
        从Event流中获取最新Event的ID
        
        这是一个抽象方法，必须在子类中实现。
        
        Returns:
            int: 最新Event的ID
        """

    @deprecated('use search_events instead')
    def filtered_events_by_source(self, source: EventSource) -> Iterable[Event]:
        """
        根据来源过滤Event（已弃用方法）
        
        这个方法已被弃用，建议使用search_events方法代替。
        
        Args:
            source (EventSource): 要过滤的Event来源
            
        Yields:
            Event: 匹配指定来源的Event
        """
        # 使用新的search_events方法并传递来源过滤器
        yield from self.search_events(filter=EventFilter(source=source))

    @deprecated('use search_events instead')
    def get_matching_events(
        self,
        query: str | None = None,
        event_types: tuple[type[Event], ...] | None = None,
        source: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        start_id: int = 0,
        limit: int = 100,
        reverse: bool = False,
    ) -> list[Event]:
        """
        根据过滤条件从Event流中获取匹配的Event（已弃用方法）
        
        这个方法已被弃用，建议使用search_events方法代替。
        
        Args:
            query (str | None): 在Event内容中搜索的文本
            event_types (tuple[type[Event], ...] | None): 根据Event类型类进行过滤
                （例如，(FileReadAction, )）。
            source (str | None): 根据Event来源进行过滤
            start_date (str | None): 过滤此日期之后的Event（ISO格式）
            end_date (str | None): 过滤此日期之前的Event（ISO格式）
            start_id (int): Event流中的起始ID。默认为0
            limit (int): 返回的最大Event数量。必须在1到100之间。默认为100
            reverse (bool): 是否以相反顺序检索Event。默认为False。

        Returns:
            list[Event]: 匹配的Event列表

        Raises:
            ValueError: 如果limit不在1到100之间
        """
        # 验证limit参数范围
        if limit < 1 or limit > 100:
            raise ValueError('Limit must be between 1 and 100')

        # 使用search_events方法并应用所有过滤条件
        events = self.search_events(
            start_id=start_id,
            reverse=reverse,
            filter=EventFilter(
                query=query,
                include_types=event_types,
                source=source,
                start_date=start_date,
                end_date=end_date,
            ),
        )
        # 使用islice限制返回的Event数量并转换为列表
        return list(islice(events, limit))