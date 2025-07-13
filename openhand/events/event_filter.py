import json
from dataclasses import dataclass

from openhands.events.event import Event
from openhands.events.serialization.event import event_to_dict


@dataclass
class EventFilter:
    """
    Event对象过滤器类
    
    EventFilter为Event流提供了灵活的过滤方式，可以根据各种条件如Event类型、
    来源、日期范围和内容来包含或排除Event。它可以用于根据指定条件从搜索结果中
    包含或排除Event。

    Attributes:
        exclude_hidden (bool): 是否排除标记为隐藏的Event。默认为False。
        query (str | None): 在Event内容中搜索的文本字符串。不区分大小写。默认为None。
        include_types (tuple[type[Event], ...] | None): 要包含的Event类型元组。
            只有这些类型的Event才会通过过滤器。默认为None（包含所有类型）。
        exclude_types (tuple[type[Event], ...] | None): 要排除的Event类型元组。
            这些类型的Event将被过滤掉。默认为None（不排除任何类型）。
        source (str | None): 根据Event来源进行过滤（例如，'agent'、'user'、'environment'）。
            默认为None。
        start_date (str | None): ISO格式的日期字符串。只有在此日期之后的Event才会通过过滤器。
            默认为None。
        end_date (str | None): ISO格式的日期字符串。只有在此日期之前的Event才会通过过滤器。
            默认为None。
    """

    exclude_hidden: bool = False  # 是否排除隐藏的Event
    query: str | None = None  # 文本搜索查询
    include_types: tuple[type[Event], ...] | None = None  # 要包含的Event类型
    exclude_types: tuple[type[Event], ...] | None = None  # 要排除的Event类型
    source: str | None = None  # Event来源过滤器
    start_date: str | None = None  # 开始日期过滤器
    end_date: str | None = None  # 结束日期过滤器

    def include(self, event: Event) -> bool:
        """
        根据过滤条件确定是否应该包含某个Event
        
        此方法检查给定的Event是否匹配所有过滤条件。如果任何条件失败，
        则排除该Event。

        Args:
            event (Event): 要检查的Event对象

        Returns:
            bool: 如果Event通过所有过滤条件应该被包含则返回True，否则返回False
        """
        # 检查Event类型包含过滤器
        if self.include_types and not isinstance(event, self.include_types):
            return False

        # 检查Event类型排除过滤器
        if self.exclude_types is not None and isinstance(event, self.exclude_types):
            return False

        # 检查Event来源过滤器
        if self.source:
            # 如果Event没有来源或来源不匹配，则排除
            if event.source is None or event.source.value != self.source:
                return False

        # 检查开始日期过滤器
        if (
            self.start_date
            and event.timestamp is not None
            and event.timestamp < self.start_date
        ):
            return False

        # 检查结束日期过滤器
        if (
            self.end_date
            and event.timestamp is not None
            and event.timestamp > self.end_date
        ):
            return False

        # 检查是否排除隐藏的Event
        if self.exclude_hidden and getattr(event, 'hidden', False):
            return False

        # 如果提供了查询字符串，在Event内容中进行文本搜索
        if self.query:
            # 将Event转换为字典格式
            event_dict = event_to_dict(event)
            # 将Event字典转换为JSON字符串并转为小写用于搜索
            event_str = json.dumps(event_dict).lower()
            # 检查查询字符串是否在Event内容中
            if self.query.lower() not in event_str:
                return False

        # 如果通过了所有检查，则包含此Event
        return True

    def exclude(self, event: Event) -> bool:
        """
        根据过滤条件确定是否应该排除某个Event
        
        这是include方法的相反操作。

        Args:
            event (Event): 要检查的Event对象

        Returns:
            bool: 如果Event应该被排除则返回True，如果应该被包含则返回False
        """
        # 返回include方法的相反结果
        return not self.include(event)