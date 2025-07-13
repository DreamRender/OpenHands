import asyncio
from typing import Any
from uuid import uuid4

from fastapi import Request

from openhands.core.logger import openhands_logger as logger
from openhands.events.action.action import Action, ActionSecurityRisk
from openhands.events.event import Event
from openhands.events.stream import EventStream, EventStreamSubscriber


class SecurityAnalyzer:
    """安全分析器基类
    
    该类是所有安全分析器的基类，负责接收所有事件并分析Agent的Action
    是否存在安全风险。它通过事件流订阅机制监听系统中的所有事件，
    并在接收到Action时进行安全风险评估。
    """

    def __init__(self, event_stream: EventStream) -> None:
        """初始化SecurityAnalyzer实例
        
        Args:
            event_stream: 用于监听事件的事件流对象
            
        该构造函数会自动订阅事件流，当有新事件产生时会异步调用on_event方法。
        使用同步包装器确保异步事件处理能正确执行。
        """
        self.event_stream = event_stream

        def sync_on_event(event: Event) -> None:
            """同步事件处理包装器
            
            Args:
                event: 接收到的事件
                
            由于事件流的订阅机制是同步的，但事件处理需要异步执行，
            因此使用此包装器将同步调用转换为异步任务。
            """
            asyncio.create_task(self.on_event(event))

        # 订阅事件流，使用SECURITY_ANALYZER类型和唯一ID
        self.event_stream.subscribe(
            EventStreamSubscriber.SECURITY_ANALYZER, sync_on_event, str(uuid4())
        )

    async def on_event(self, event: Event) -> None:
        """处理接收到的事件
        
        Args:
            event: 接收到的事件对象
            
        当接收到Action类型的事件时，会进行安全风险分析。
        该方法是事件处理的主入口，负责协调各种安全检查操作。
        """
        logger.debug(f'SecurityAnalyzer received event: {event}')
        
        # 记录事件到日志系统
        await self.log_event(event)
        
        # 只处理Action类型的事件
        if not isinstance(event, Action):
            return

        try:
            # 分析Action的安全风险并设置security_risk属性
            event.security_risk = await self.security_risk(event)  # type: ignore [attr-defined]
            
            # 基于分析结果执行相应的Action
            await self.act(event)
        except Exception as e:
            # 记录分析过程中发生的错误
            logger.error(f'Error occurred while analyzing the event: {e}')

    async def handle_api_request(self, request: Request) -> Any:
        """处理传入的API请求
        
        Args:
            request: FastAPI请求对象
            
        Returns:
            Any: 处理结果
            
        Raises:
            NotImplementedError: 子类必须实现此方法
            
        该方法用于处理外部API请求，子类需要根据具体需求实现。
        """
        raise NotImplementedError(
            'Need to implement handle_api_request method in SecurityAnalyzer subclass'
        )

    async def log_event(self, event: Event) -> None:
        """记录传入的事件
        
        Args:
            event: 需要记录的事件
            
        该方法用于记录事件到分析器的内部存储中，
        子类可以重写此方法来实现特定的事件记录逻辑。
        默认实现为空，不进行任何操作。
        """
        pass

    async def act(self, event: Event) -> None:
        """基于分析的事件执行相应的Action
        
        Args:
            event: 已分析的事件
            
        该方法在安全风险评估完成后被调用，用于执行相应的安全措施。
        例如：阻止高风险Action、请求用户确认等。
        默认实现为空，子类可以重写以实现特定的安全响应逻辑。
        """
        pass

    async def security_risk(self, event: Action) -> ActionSecurityRisk:
        """评估Action的安全风险等级
        
        Args:
            event: 需要评估的Action对象
            
        Returns:
            ActionSecurityRisk: 评估出的安全风险等级
            
        Raises:
            NotImplementedError: 子类必须实现此方法
            
        这是安全分析器的核心方法，子类必须实现具体的风险评估逻辑。
        返回值应该是ActionSecurityRisk枚举中的一个值，表示风险等级。
        """
        raise NotImplementedError(
            'Need to implement security_risk method in SecurityAnalyzer subclass'
        )

    async def close(self) -> None:
        """清理SecurityAnalyzer分配的资源
        
        该方法在SecurityAnalyzer不再需要时被调用，用于清理各种资源，
        如关闭网络连接、停止后台进程、释放内存等。
        默认实现为空，子类可以根据需要重写。
        """
        pass