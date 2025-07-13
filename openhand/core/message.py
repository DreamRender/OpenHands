"""
消息数据结构模块

此模块定义了用于处理 LLM 消息的数据结构，包括内容类型、消息内容和消息本身。
支持文本内容、图像内容以及工具调用等功能。
"""

from enum import Enum
from typing import Any, Literal

from litellm import ChatCompletionMessageToolCall
from pydantic import BaseModel, Field, model_serializer


class ContentType(Enum):
    """内容类型枚举"""
    TEXT = 'text'           # 文本内容
    IMAGE_URL = 'image_url' # 图像URL内容


class Content(BaseModel):
    """
    消息内容的基类。
    
    Attributes:
        type: 内容类型标识符
        cache_prompt: 是否启用提示缓存
    """
    type: str
    cache_prompt: bool = False

    @model_serializer(mode='plain')
    def serialize_model(
        self,
    ) -> dict[str, str | dict[str, str]] | list[dict[str, str | dict[str, str]]]:
        """
        序列化模型为字典或字典列表。
        
        子类应该实现此方法。
        
        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError('子类应该实现此方法。')


class TextContent(Content):
    """
    文本内容类。
    
    Attributes:
        type: 固定为 TEXT 类型
        text: 文本内容字符串
    """
    type: str = ContentType.TEXT.value
    text: str

    @model_serializer(mode='plain')
    def serialize_model(self) -> dict[str, str | dict[str, str]]:
        """
        将文本内容序列化为字典格式。
        
        Returns:
            包含类型和文本的字典，如果启用缓存则包含缓存控制信息
        """
        data: dict[str, str | dict[str, str]] = {
            'type': self.type,
            'text': self.text,
        }
        # 如果启用了提示缓存，添加缓存控制信息
        if self.cache_prompt:
            data['cache_control'] = {'type': 'ephemeral'}
        return data


class ImageContent(Content):
    """
    图像内容类。
    
    Attributes:
        type: 固定为 IMAGE_URL 类型
        image_urls: 图像URL列表
    """
    type: str = ContentType.IMAGE_URL.value
    image_urls: list[str]

    @model_serializer(mode='plain')
    def serialize_model(self) -> list[dict[str, str | dict[str, str]]]:
        """
        将图像内容序列化为字典列表格式。
        
        Returns:
            包含图像URL信息的字典列表，如果启用缓存则在最后一个项目中包含缓存控制信息
        """
        images: list[dict[str, str | dict[str, str]]] = []
        
        # 为每个图像URL创建字典条目
        for url in self.image_urls:
            images.append({'type': self.type, 'image_url': {'url': url}})
        
        # 如果启用了提示缓存且有图像，在最后一个图像上添加缓存控制
        if self.cache_prompt and images:
            images[-1]['cache_control'] = {'type': 'ephemeral'}
        return images


class Message(BaseModel):
    """
    LLM 消息类。
    
    注意：这与 EventSource 不同，这些是 LLM API 中的角色。
    
    Attributes:
        role: 消息角色（用户、系统、助手、工具）
        content: 消息内容列表，可包含文本和图像内容
        cache_enabled: 是否启用缓存
        vision_enabled: 是否启用视觉功能
        function_calling_enabled: 是否启用函数调用
        tool_calls: 来自 LLM 的工具调用列表
        tool_call_id: 工具执行结果的调用 ID
        name: 工具的名称
        force_string_serializer: 是否强制使用字符串序列化器
    """
    # 注意：这与 EventSource 不同
    # 这些是 LLM API 中的角色
    role: Literal['user', 'system', 'assistant', 'tool']
    content: list[TextContent | ImageContent] = Field(default_factory=list)
    cache_enabled: bool = False
    vision_enabled: bool = False
    
    # 函数调用相关
    function_calling_enabled: bool = False
    # - 来自 LLM 的工具调用
    tool_calls: list[ChatCompletionMessageToolCall] | None = None
    # - 给 LLM 的工具执行结果
    tool_call_id: str | None = None
    name: str | None = None  # 工具的名称
    
    # 强制字符串序列化器
    force_string_serializer: bool = False

    @property
    def contains_image(self) -> bool:
        """
        检查消息是否包含图像内容。
        
        Returns:
            如果消息包含图像内容则返回 True，否则返回 False
        """
        return any(isinstance(content, ImageContent) for content in self.content)

    @model_serializer(mode='plain')
    def serialize_model(self) -> dict[str, Any]:
        """
        序列化消息模型。
        
        我们需要两种序列化方式：
        - 序列化为单个字符串：用于不支持内容项列表的提供商（例如没有视觉、没有工具调用）
        - 序列化为内容项列表：支持视觉/提示缓存/工具调用的新版本提供商 API
        
        注意：当 litellm 或提供商支持新 API 时移除此功能
        
        Returns:
            序列化后的消息字典
        """
        if not self.force_string_serializer and (
            self.cache_enabled or self.vision_enabled or self.function_calling_enabled
        ):
            return self._list_serializer()
        # 一些提供商，如 HF 和 Groq/llama，这里不支持列表，而是单个字符串
        return self._string_serializer()

    def _string_serializer(self) -> dict[str, Any]:
        """
        字符串序列化器：将内容转换为单个字符串。
        
        Returns:
            包含字符串内容的消息字典
        """
        # 将内容转换为单个字符串
        content = '\n'.join(
            item.text for item in self.content if isinstance(item, TextContent)
        )
        message_dict: dict[str, Any] = {'content': content, 'role': self.role}

        # 如果有工具调用或响应，添加工具调用键
        return self._add_tool_call_keys(message_dict)

    def _list_serializer(self) -> dict[str, Any]:
        """
        列表序列化器：将内容序列化为内容项列表。
        
        Returns:
            包含内容项列表的消息字典
        """
        content: list[dict[str, Any]] = []
        role_tool_with_prompt_caching = False
        
        for item in self.content:
            d = item.model_dump()
            
            # 对于工具内容，我们必须移除 cache_prompt 并将其移动到消息级别
            # 详细讨论参见：https://github.com/BerriAI/litellm/issues/6422#issuecomment-2438765472
            if self.role == 'tool' and item.cache_prompt:
                role_tool_with_prompt_caching = True
                if isinstance(item, TextContent):
                    d.pop('cache_control', None)
                elif isinstance(item, ImageContent):
                    # ImageContent.model_dump() 总是返回一个列表
                    # 我们知道对于 ImageContent，d 是一个字典列表
                    if hasattr(d, '__iter__'):
                        for d_item in d:
                            if hasattr(d_item, 'pop'):
                                d_item.pop('cache_control', None)

            # 添加文本内容
            if isinstance(item, TextContent):
                content.append(d)
            # 如果启用了视觉功能，添加图像内容
            elif isinstance(item, ImageContent) and self.vision_enabled:
                # ImageContent.model_dump() 总是返回一个列表
                # 我们知道对于 ImageContent，d 是一个列表
                content.extend([d] if isinstance(d, dict) else d)

        message_dict: dict[str, Any] = {'content': content, 'role': self.role}

        # 如果工具角色启用了提示缓存，在消息级别添加缓存控制
        if role_tool_with_prompt_caching:
            message_dict['cache_control'] = {'type': 'ephemeral'}

        # 如果有工具调用或响应，添加工具调用键
        return self._add_tool_call_keys(message_dict)

    def _add_tool_call_keys(self, message_dict: dict[str, Any]) -> dict[str, Any]:
        """
        如果有工具调用或响应，则添加工具调用键。

        注意：这对于原生和非原生工具调用都是必需的
        
        Args:
            message_dict: 要添加工具调用信息的消息字典
            
        Returns:
            添加了工具调用信息的消息字典
        """
        # 助手消息调用工具
        if self.tool_calls is not None:
            message_dict['tool_calls'] = [
                {
                    'id': tool_call.id,
                    'type': 'function',
                    'function': {
                        'name': tool_call.function.name,
                        'arguments': tool_call.function.arguments,
                    },
                }
                for tool_call in self.tool_calls
            ]

        # 带有工具响应的观察消息
        if self.tool_call_id is not None:
            assert self.name is not None, (
                '当 tool_call_id 不为 None 时，name 是必需的'
            )
            message_dict['tool_call_id'] = self.tool_call_id
            message_dict['name'] = self.name

        return message_dict