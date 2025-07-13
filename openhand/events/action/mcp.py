from dataclasses import dataclass, field
from typing import Any, ClassVar

from openhands.core.schema import ActionType
from openhands.events.action.action import Action, ActionSecurityRisk


@dataclass
class MCPAction(Action):
    """MCP (Model Context Protocol) Action
    
    用于与MCP服务器进行交互的Action。MCP是一种标准化协议，
    允许Agent与外部服务和工具进行通信。
    
    Attributes:
        name (str): MCP服务器的名称或要调用的功能名称
        arguments (dict[str, Any]): 传递给MCP服务器的参数字典
        thought (str): Agent的思考过程，默认为空字符串
        action (str): Action类型，固定为ActionType.MCP
        runnable (ClassVar[bool]): 类变量，表示此Action可以被执行
        security_risk (ActionSecurityRisk | None): 安全风险等级评估
    """
    
    name: str  # MCP服务器名称或功能名称
    arguments: dict[str, Any] = field(default_factory=dict)  # 传递的参数字典
    thought: str = ''  # Agent的思考过程
    action: str = ActionType.MCP  # Action类型标识
    runnable: ClassVar[bool] = True  # 标识此Action可执行
    security_risk: ActionSecurityRisk | None = None  # 安全风险评估

    @property
    def message(self) -> str:
        """获取MCP交互的消息
        
        Returns:
            str: 格式化的MCP交互消息，包含服务器名称和参数的代码块
        """
        return (
            f'I am interacting with the MCP server with name:\n'
            f'```\n{self.name}\n```\n'
            f'and arguments:\n'
            f'```\n{self.arguments}\n```'
        )

    def __str__(self) -> str:
        """获取MCPAction的字符串表示
        
        Returns:
            str: 格式化的Action描述，包含思考过程（如果有）、名称和参数
        """
        ret = '**MCPAction**\n'
        if self.thought:
            ret += f'THOUGHT: {self.thought}\n'
        ret += f'NAME: {self.name}\n'
        ret += f'ARGUMENTS: {self.arguments}'
        return ret