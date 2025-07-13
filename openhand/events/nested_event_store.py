from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlencode

import httpx  # type: ignore
from fastapi import status

from openhands.events.event import Event
from openhands.events.event_filter import EventFilter
from openhands.events.event_store_abc import EventStoreABC
from openhands.events.serialization.event import event_from_dict


@dataclass
class NestedEventStore(EventStoreABC):
    """
    基于HTTP的嵌套Event存储实现
    
    NestedEventStore是EventStoreABC的一个实现，它通过HTTP API来访问远程的Event存储服务。
    这种实现适用于分布式环境，其中Event存储服务可能运行在不同的服务器上。
    
    Attributes:
        base_url (str): Event存储服务的基础URL
        sid (str): Session ID，用于标识会话
        user_id (str | None): 用户ID，可以为None
        session_api_key (str | None): Session API密钥，用于身份验证，可以为None
    """

    base_url: str  # 基础URL
    sid: str  # Session ID
    user_id: str | None  # 用户ID
    session_api_key: str | None = None  # API密钥

    def search_events(
        self,
        start_id: int = 0,
        end_id: int | None = None,
        reverse: bool = False,
        filter: EventFilter | None = None,
        limit: int | None = None,
    ) -> Iterable[Event]:
        """
        通过HTTP API搜索Event
        
        此方法向远程Event存储服务发送HTTP请求来检索Event。
        它支持分页机制来处理大量Event数据。
        
        Args:
            start_id (int): 起始Event ID，默认为0
            end_id (int | None): 结束Event ID，默认为None
            reverse (bool): 是否反向遍历，默认为False
            filter (EventFilter | None): Event过滤器，默认为None
            limit (int | None): 返回数量限制，默认为None
            
        Yields:
            Event: 从远程服务检索到的Event对象
        """
        # 持续循环直到获取所有数据或遇到结束条件
        while True:
            # 构建搜索参数
            search_params = {
                'start_id': start_id,  # 起始ID
                'reverse': reverse,    # 是否反向
            }
            # 如果有限制数量，添加到参数中（最大100）
            if limit is not None:
                search_params['limit'] = min(100, limit)
                
            # 将参数编码为URL查询字符串
            search_str = urlencode(search_params)
            # 构建完整的请求URL
            url = f'{self.base_url}/events?{search_str}'
            
            # 准备请求头
            headers = {}
            if self.session_api_key:
                # 如果有API密钥，添加到请求头中
                headers['X-Session-API-Key'] = self.session_api_key
                
            # 发送HTTP GET请求
            response = httpx.get(url, headers=headers)
            
            # 如果返回404，按照EventStore的模式不抛出错误，直接返回
            if response.status_code == status.HTTP_404_NOT_FOUND:
                return
                
            # 解析响应JSON数据
            result_set = response.json()
            
            # 遍历返回的Event数据
            for result in result_set['events']:
                # 从字典重建Event对象
                event = event_from_dict(result)
                # 更新start_id为当前Event ID + 1，为下次请求做准备
                start_id = max(start_id, event.id + 1)
                
                # 检查是否达到结束ID
                if end_id == event.id:
                    # 如果有过滤器且Event通过过滤器，则yield此Event
                    if not filter or filter.include(event):
                        yield event
                    return
                    
                # 应用过滤器，如果Event被排除则跳过
                if filter and filter.exclude(event):
                    continue
                    
                # yield当前Event
                yield event
                
                # 如果有数量限制，递减计数器
                if limit is not None:
                    limit -= 1
                    # 如果达到限制，返回
                    if limit <= 0:
                        return
                        
            # 如果没有更多数据，退出循环
            if not result_set['has_more']:
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
        # 通过search_events方法获取单个Event
        events = list(self.search_events(start_id=id, limit=1))
        if not events:
            # 如果没有找到Event，抛出FileNotFoundError
            raise FileNotFoundError('no_event')
        return events[0]

    def get_latest_event(self) -> Event:
        """
        获取最新的Event
        
        Returns:
            Event: 最新的Event对象
            
        Raises:
            FileNotFoundError: 如果没有Event存在
        """
        # 通过反向搜索获取最新的Event
        events = list(self.search_events(reverse=True, limit=1))
        if not events:
            # 如果没有找到Event，抛出FileNotFoundError
            raise FileNotFoundError('no_event')
        return events[0]

    def get_latest_event_id(self) -> int:
        """
        获取最新Event的ID
        
        Returns:
            int: 最新Event的ID
        """
        # 获取最新Event并返回其ID
        event = self.get_latest_event()
        return event.id