from dataclasses import dataclass
from typing import Any

import openhands
from openhands.core.schema import ActionType
from openhands.events.action.action import Action, ActionSecurityRisk


@dataclass
class MessageAction(Action):
    """消息Action
    
    用于发送消息的Action，支持文本内容、文件URL和图片URL。
    可以设置是否等待响应，用于Agent与用户或其他系统的通信。
    
    Attributes:
        content (str): 消息的文本内容
        file_urls (list[str] | None): 附加的文件URL列表，可选
        image_urls (list[str] | None): 附加的图片URL列表，可选
        wait_for_response (bool): 是否等待响应，默认为False
        action (str): Action类型，固定为ActionType.MESSAGE
        security_risk (ActionSecurityRisk | None): 安全风险等级评估
    """
    
    content: str  # 消息文本内容
    file_urls: list[str] | None = None  # 附加文件URL列表
    image_urls: list[str] | None = None  # 附加图片URL列表
    wait_for_response: bool = False  # 是否等待响应
    action: str = ActionType.MESSAGE  # Action类型标识
    security_risk: ActionSecurityRisk | None = None  # 安全风险评估

    @property
    def message(self) -> str:
        """获取消息内容
        
        Returns:
            str: 消息的文本内容
        """
        return self.content

    @property
    def images_urls(self) -> list[str] | None:
        """获取图片URL列表（向后兼容的已弃用别名）
        
        Returns:
            list[str] | None: 图片URL列表
        """
        # 为向后兼容性提供的已弃用别名
        return self.image_urls

    @images_urls.setter
    def images_urls(self, value: list[str] | None) -> None:
        """设置图片URL列表（向后兼容的已弃用别名）
        
        Args:
            value (list[str] | None): 要设置的图片URL列表
        """
        self.image_urls = value

    def __str__(self) -> str:
        """获取MessageAction的字符串表示
        
        Returns:
            str: 格式化的Action描述，包含来源、内容、图片URL和文件URL
        """
        ret = f'**MessageAction** (source={self.source})\n'
        ret += f'CONTENT: {self.content}'
        if self.image_urls:
            # 添加所有图片URL
            for url in self.image_urls:
                ret += f'\nIMAGE_URL: {url}'
        if self.file_urls:
            # 添加所有文件URL
            for url in self.file_urls:
                ret += f'\nFILE_URL: {url}'
        return ret


@dataclass
class SystemMessageAction(Action):
    """系统消息Action
    
    表示Agent的系统消息Action，包括系统提示和可用工具。
    这应该是事件流中的第一条消息。
    
    Attributes:
        content (str): 系统消息的文本内容
        tools (list[Any] | None): 可用工具列表，可选
        openhands_version (str | None): Openhands版本信息，默认为当前版本
        agent_class (str | None): Agent类名，可选
        action (ActionType): Action类型，固定为ActionType.SYSTEM
    """
    
    content: str  # 系统消息内容
    tools: list[Any] | None = None  # 可用工具列表
    openhands_version: str | None = openhands.__version__  # Openhands版本信息
    agent_class: str | None = None  # Agent类名
    action: ActionType = ActionType.SYSTEM  # Action类型标识

    @property
    def message(self) -> str:
        """获取系统消息内容
        
        Returns:
            str: 系统消息的文本内容
        """
        return self.content

    def __str__(self) -> str:
        """获取SystemMessageAction的字符串表示
        
        Returns:
            str: 格式化的Action描述，包含来源、内容、工具数量和Agent类名
        """
        ret = f'**SystemMessageAction** (source={self.source})\n'
        ret += f'CONTENT: {self.content}'
        if self.tools:
            # 显示可用工具数量
            ret += f'\nTOOLS: {len(self.tools)} tools available'
        if self.agent_class:
            # 显示Agent类名
            ret += f'\nAGENT_CLASS: {self.agent_class}'
        return ret