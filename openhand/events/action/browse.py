from dataclasses import dataclass
from typing import ClassVar

from openhands.core.schema import ActionType
from openhands.events.action.action import Action, ActionSecurityRisk


@dataclass
class BrowseURLAction(Action):
    """浏览URL Action
    
    用于在浏览器中打开和浏览指定URL的Action。
    支持安全风险评估和返回可访问性树（accessibility tree）的选项。
    
    Attributes:
        url (str): 要浏览的URL地址
        thought (str): Agent的思考过程，默认为空字符串
        action (str): Action类型，固定为ActionType.BROWSE
        runnable (ClassVar[bool]): 类变量，表示此Action可以被执行
        security_risk (ActionSecurityRisk | None): 安全风险等级评估
        return_axtree (bool): 是否返回可访问性树，默认为False
    """
    
    url: str  # 目标URL地址
    thought: str = ''  # Agent的思考过程
    action: str = ActionType.BROWSE  # Action类型标识
    runnable: ClassVar[bool] = True  # 标识此Action可执行
    security_risk: ActionSecurityRisk | None = None  # 安全风险评估
    return_axtree: bool = False  # 是否返回可访问性树结构

    @property
    def message(self) -> str:
        """获取浏览URL的消息
        
        Returns:
            str: 格式化的浏览消息，包含目标URL
        """
        return f'I am browsing the URL: {self.url}'

    def __str__(self) -> str:
        """获取BrowseURLAction的字符串表示
        
        Returns:
            str: 格式化的Action描述，包含思考过程（如果有）和URL
        """
        ret = '**BrowseURLAction**\n'
        if self.thought:
            ret += f'THOUGHT: {self.thought}\n'
        ret += f'URL: {self.url}'
        return ret


@dataclass
class BrowseInteractiveAction(Action):
    """交互式浏览Action
    
    用于在浏览器中执行交互式操作的Action，如点击、输入文本、滚动等。
    支持与BrowserGym环境的集成，可以发送消息给用户。
    
    Attributes:
        browser_actions (str): 要执行的浏览器操作指令
        thought (str): Agent的思考过程，默认为空字符串
        browsergym_send_msg_to_user (str): 发送给用户的BrowserGym消息，默认为空字符串
        action (str): Action类型，固定为ActionType.BROWSE_INTERACTIVE
        runnable (ClassVar[bool]): 类变量，表示此Action可以被执行
        security_risk (ActionSecurityRisk | None): 安全风险等级评估
        return_axtree (bool): 是否返回可访问性树，默认为False
    """
    
    browser_actions: str  # 浏览器操作指令
    thought: str = ''  # Agent的思考过程
    browsergym_send_msg_to_user: str = ''  # 发送给用户的BrowserGym消息
    action: str = ActionType.BROWSE_INTERACTIVE  # Action类型标识
    runnable: ClassVar[bool] = True  # 标识此Action可执行
    security_risk: ActionSecurityRisk | None = None  # 安全风险评估
    return_axtree: bool = False  # 是否返回可访问性树结构

    @property
    def message(self) -> str:
        """获取交互式浏览的消息
        
        Returns:
            str: 格式化的交互消息，包含浏览器操作指令的代码块
        """
        return f'I am interacting with the browser:\n```\n{self.browser_actions}\n```'

    def __str__(self) -> str:
        """获取BrowseInteractiveAction的字符串表示
        
        Returns:
            str: 格式化的Action描述，包含思考过程（如果有）和浏览器操作指令
        """
        ret = '**BrowseInteractiveAction**\n'
        if self.thought:
            ret += f'THOUGHT: {self.thought}\n'
        ret += f'BROWSER_ACTIONS: {self.browser_actions}'
        return ret