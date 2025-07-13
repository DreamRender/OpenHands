from typing import Any

from openhands.core.logger import llm_prompt_logger, llm_response_logger
from openhands.core.logger import openhands_logger as logger

# 消息分隔符：用于在日志中分隔不同的消息内容
MESSAGE_SEPARATOR = '\n\n----------\n\n'


class DebugMixin:
    """
    调试混入类，为其他类提供日志记录功能。
    
    该类提供了用于记录LLM(大语言Model)提示和响应的方法，
    支持格式化不同类型的消息内容，包括文本和图像URL。
    """
    
    def log_prompt(self, messages: list[dict[str, Any]] | dict[str, Any]) -> None:
        """
        记录发送给LLM的提示消息。
        
        Args:
            messages: 要记录的消息，可以是单个消息字典或消息字典列表
                     每个消息字典应包含'content'键
        
        Returns:
            None
        """
        # 检查消息是否为空
        if not messages:
            logger.debug('No completion messages!')
            return

        # 确保messages是列表格式，如果是单个字典则转换为列表
        messages = messages if isinstance(messages, list) else [messages]
        
        # 使用消息分隔符连接所有格式化后的消息内容
        # 只处理content不为None的消息
        debug_message = MESSAGE_SEPARATOR.join(
            self._format_message_content(msg)
            for msg in messages
            if msg['content'] is not None
        )

        # 如果有有效的调试消息则记录，否则记录警告
        if debug_message:
            llm_prompt_logger.debug(debug_message)
        else:
            logger.debug('No completion messages!')

    def log_response(self, message_back: str) -> None:
        """
        记录从LLM接收到的响应消息。
        
        Args:
            message_back: LLM返回的响应字符串
            
        Returns:
            None
        """
        # 只有当响应消息不为空时才记录
        if message_back:
            llm_response_logger.debug(message_back)

    def _format_message_content(self, message: dict[str, Any]) -> str:
        """
        格式化单个消息的内容。
        
        Args:
            message: 包含'content'键的消息字典
            
        Returns:
            格式化后的消息内容字符串
        """
        content = message['content']
        
        # 如果content是列表，则格式化每个元素并用换行符连接
        if isinstance(content, list):
            return '\n'.join(
                self._format_content_element(element) for element in content
            )
        
        # 如果content不是列表，直接转换为字符串
        return str(content)

    def _format_content_element(self, element: dict[str, Any] | Any) -> str:
        """
        格式化内容元素，支持文本和图像URL。
        
        Args:
            element: 内容元素，可以是字典或其他类型
            
        Returns:
            格式化后的内容元素字符串
        """
        # 如果元素是字典类型
        if isinstance(element, dict):
            # 处理文本内容
            if 'text' in element:
                return str(element['text'])
            
            # 处理图像URL内容（仅在vision功能激活时）
            if (
                self.vision_is_active()
                and 'image_url' in element
                and 'url' in element['image_url']
            ):
                return str(element['image_url']['url'])
        
        # 对于其他类型的元素，直接转换为字符串
        return str(element)

    def vision_is_active(self) -> bool:
        """
        检查vision功能是否激活。
        
        该方法应该在使用DebugMixin的类中实现，
        用于确定是否应该处理图像相关的内容。
        
        Returns:
            bool: vision功能是否激活
            
        Raises:
            NotImplementedError: 如果在子类中未实现该方法
        """
        raise NotImplementedError