from typing import Any, Iterable

from pydantic import BaseModel, Field
from pydantic.dataclasses import dataclass


@dataclass
class LLM:
    """大语言Model的数据类
    
    用于表示LLM的基本信息，包括供应商和Model名称
    """
    vendor: str  # LLM供应商名称（如：OpenAI、Anthropic等）
    model: str   # Model名称（如：gpt-4、claude-3等）


class Event(BaseModel):
    """事件基类
    
    所有事件类型的基础类，包含Metadata信息。
    Metadata用于存储与事件相关的额外信息。
    """
    
    metadata: dict[str, Any] | None = Field(
        default_factory=lambda: dict(), 
        description='与事件相关的Metadata'
    )


class Function(BaseModel):
    """函数调用信息Model
    
    表示一个函数调用，包含函数名和参数信息。
    主要用于工具调用场景，记录具体的函数名和传递的参数。
    """
    
    name: str  # 函数名称
    arguments: dict[str, Any]  # 函数参数字典，键为参数名，值为参数值


class ToolCall(Event):
    """工具调用事件
    
    继承自Event，表示一个工具调用操作。包含调用ID、类型和具体的函数信息。
    在Agent与工具交互时使用，用于记录工具调用的详细信息。
    """
    
    id: str  # 工具调用的唯一标识符
    type: str  # 调用类型（通常为"function"）
    function: Function  # 具体的函数调用信息


class Message(Event):
    """消息事件
    
    继承自Event，表示对话中的一条消息。可以是用户消息、助手消息或系统消息。
    支持包含工具调用信息，用于多轮对话和工具交互场景。
    """
    
    role: str  # 消息角色（如：user、assistant、system）
    content: str | None  # 消息内容，可以为空（当只有工具调用时）
    tool_calls: list[ToolCall] | None = None  # 可选的工具调用列表

    def __rich_repr__(  # type: ignore[override]
        self,
    ) -> Iterable[Any | tuple[Any] | tuple[str, Any] | tuple[str, Any, Any]]:
        """自定义rich库的显示格式
        
        该方法定义了Message对象在使用rich库打印时的显示格式，
        每个字段会在单独的行上显示，便于调试和日志查看。
        
        Yields:
            各个字段的显示信息元组
        """
        # 分行显示各个字段
        yield 'role', self.role
        yield 'content', self.content
        yield 'tool_calls', self.tool_calls


class ToolOutput(Event):
    """工具输出事件
    
    继承自Event，表示工具执行后的输出结果。
    与ToolCall配对使用，记录工具调用的返回结果。
    """
    
    role: str  # 输出角色（通常为"tool"）
    content: str  # 工具执行的输出内容
    tool_call_id: str | None = None  # 关联的工具调用ID，用于匹配调用和输出

    _tool_call: ToolCall | None = None  # 私有字段：关联的ToolCall对象引用