from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from openhands.events.tool import ToolCallMetadata
from openhands.llm.metrics import Metrics


class EventSource(str, Enum):
    """
    Event来源枚举类
    
    定义了Event可能的来源类型，用于标识Event是由哪个组件产生的。
    """
    AGENT = 'agent'  # 由Agent产生的Event
    USER = 'user'    # 由用户产生的Event
    ENVIRONMENT = 'environment'  # 由环境产生的Event


class FileEditSource(str, Enum):
    """
    文件编辑来源枚举类
    
    定义了文件编辑操作的不同来源类型。
    """
    LLM_BASED_EDIT = 'llm_based_edit'  # 基于LLM的编辑操作
    OH_ACI = 'oh_aci'  # 来自openhands-aci的编辑操作


class FileReadSource(str, Enum):
    """
    文件读取来源枚举类
    
    定义了文件读取操作的不同来源类型。
    """
    OH_ACI = 'oh_aci'  # 来自openhands-aci的读取操作
    DEFAULT = 'default'  # 默认的读取操作


class RecallType(str, Enum):
    """
    回忆类型枚举类
    
    定义了可以从MicroAgent中检索的信息类型。
    """

    WORKSPACE_CONTEXT = 'workspace_context'
    """Workspace上下文信息（仓库指令、运行时环境等）"""

    KNOWLEDGE = 'knowledge'
    """知识类型的MicroAgent"""


@dataclass
class Event:
    """
    Event基类
    
    这是所有Event类型的基础类，提供了Event的核心属性和方法。
    Event代表系统中发生的一个具体事件，包含时间戳、来源、消息等信息。
    
    Class Attributes:
        INVALID_ID (int): 表示无效Event ID的常量值
    """
    INVALID_ID = -1  # 无效Event ID的常量

    @property
    def message(self) -> str | None:
        """
        获取Event的消息内容
        
        Returns:
            str | None: Event的消息内容，如果没有消息则返回None
        """
        # 检查是否存在_message属性
        if hasattr(self, '_message'):
            msg = getattr(self, '_message')
            # 如果消息不为None，转换为字符串，否则返回None
            return str(msg) if msg is not None else None
        return ''  # 如果没有_message属性，返回空字符串

    @property
    def id(self) -> int:
        """
        获取Event的唯一标识符
        
        Returns:
            int: Event的ID，如果没有ID则返回INVALID_ID
        """
        # 检查是否存在_id属性
        if hasattr(self, '_id'):
            id_val = getattr(self, '_id')
            # 如果ID不为None，转换为整数，否则返回INVALID_ID
            return int(id_val) if id_val is not None else Event.INVALID_ID
        return Event.INVALID_ID  # 如果没有_id属性，返回INVALID_ID

    @property
    def timestamp(self) -> str | None:
        """
        获取Event的时间戳
        
        Returns:
            str | None: ISO格式的时间戳字符串，如果没有时间戳则返回None
        """
        # 检查是否存在_timestamp属性且为字符串类型
        if hasattr(self, '_timestamp') and isinstance(self._timestamp, str):
            ts = getattr(self, '_timestamp')
            # 如果时间戳不为None，转换为字符串，否则返回None
            return str(ts) if ts is not None else None
        return None  # 如果没有_timestamp属性或类型不正确，返回None

    @timestamp.setter
    def timestamp(self, value: datetime) -> None:
        """
        设置Event的时间戳
        
        Args:
            value (datetime): 要设置的datetime对象
        """
        # 确保传入的值是datetime类型
        if isinstance(value, datetime):
            # 将datetime对象转换为ISO格式字符串存储
            self._timestamp = value.isoformat()

    @property
    def source(self) -> EventSource | None:
        """
        获取Event的来源
        
        Returns:
            EventSource | None: Event的来源枚举值，如果没有来源则返回None
        """
        # 检查是否存在_source属性
        if hasattr(self, '_source'):
            src = getattr(self, '_source')
            # 如果来源不为None，转换为EventSource枚举，否则返回None
            return EventSource(src) if src is not None else None
        return None  # 如果没有_source属性，返回None

    @property
    def cause(self) -> int | None:
        """
        获取引起此Event的原因Event的ID
        
        Returns:
            int | None: 原因Event的ID，如果没有原因则返回None
        """
        # 检查是否存在_cause属性
        if hasattr(self, '_cause'):
            cause_val = getattr(self, '_cause')
            # 如果原因不为None，转换为整数，否则返回None
            return int(cause_val) if cause_val is not None else None
        return None  # 如果没有_cause属性，返回None

    @property
    def timeout(self) -> float | None:
        """
        获取Event的超时时间
        
        Returns:
            float | None: 超时时间（秒），如果没有设置超时则返回None
        """
        # 检查是否存在_timeout属性
        if hasattr(self, '_timeout'):
            timeout_val = getattr(self, '_timeout')
            # 如果超时值不为None，转换为浮点数，否则返回None
            return float(timeout_val) if timeout_val is not None else None
        return None  # 如果没有_timeout属性，返回None

    def set_hard_timeout(self, value: float | None, blocking: bool = True) -> None:
        """
        设置Event的硬超时时间
        
        注意：这是一个硬超时，意味着Event将被阻塞直到超时时间到达。
        
        Args:
            value (float | None): 超时时间（秒），None表示无超时
            blocking (bool): 是否阻塞，默认为True
        """
        self._timeout = value  # 设置超时值
        
        # 如果超时时间大于600秒，记录警告信息
        if value is not None and value > 600:
            from openhands.core.logger import openhands_logger as logger

            logger.warning(
                'Timeout greater than 600 seconds may not be supported by '
                'the runtime. Consider setting a lower timeout.'
            )

        # 检查Event是否有blocking属性
        if hasattr(self, 'blocking'):
            # 如果设置了超时，blocking需要设置为True
            self.blocking = blocking

    @property
    def llm_metrics(self) -> Metrics | None:
        """
        获取LLM调用的性能指标
        
        这是一个可选的元数据，包含LLM编辑操作的成本信息。
        
        Returns:
            Metrics | None: LLM性能指标对象，如果没有则返回None
        """
        # 检查是否存在_llm_metrics属性
        if hasattr(self, '_llm_metrics'):
            metrics = getattr(self, '_llm_metrics')
            # 确保返回的是Metrics类型对象
            return metrics if isinstance(metrics, Metrics) else None
        return None  # 如果没有_llm_metrics属性，返回None

    @llm_metrics.setter
    def llm_metrics(self, value: Metrics) -> None:
        """
        设置LLM调用的性能指标
        
        Args:
            value (Metrics): 要设置的Metrics对象
        """
        self._llm_metrics = value

    @property
    def tool_call_metadata(self) -> ToolCallMetadata | None:
        """
        获取工具调用的元数据
        
        这是一个可选字段，如果Event包含工具调用，则存储相关的元数据信息。
        
        Returns:
            ToolCallMetadata | None: 工具调用元数据对象，如果没有则返回None
        """
        # 检查是否存在_tool_call_metadata属性
        if hasattr(self, '_tool_call_metadata'):
            metadata = getattr(self, '_tool_call_metadata')
            # 确保返回的是ToolCallMetadata类型对象
            return metadata if isinstance(metadata, ToolCallMetadata) else None
        return None  # 如果没有_tool_call_metadata属性，返回None

    @tool_call_metadata.setter
    def tool_call_metadata(self, value: ToolCallMetadata) -> None:
        """
        设置工具调用的元数据
        
        Args:
            value (ToolCallMetadata): 要设置的ToolCallMetadata对象
        """
        self._tool_call_metadata = value

    @property
    def response_id(self) -> str | None:
        """
        获取LLM响应的ID
        
        这是一个可选字段，存储来自LLM的响应ID。
        
        Returns:
            str | None: LLM响应ID，如果没有则返回None
        """
        # 检查是否存在_response_id属性
        if hasattr(self, '_response_id'):
            return self._response_id  # type: ignore[attr-defined]
        return None  # 如果没有_response_id属性，返回None

    @response_id.setter
    def response_id(self, value: str) -> None:
        """
        设置LLM响应的ID
        
        Args:
            value (str): 要设置的响应ID
        """
        self._response_id = value