from dataclasses import dataclass, field
from typing import Any

from openhands.core.schema import ObservationType
from openhands.events.observation.observation import Observation


@dataclass
class MCPObservation(Observation):
    """MCP观察类
    
    这个数据类表示MCP Server操作的结果。
    MCP (Model Context Protocol) 是一个协议，允许与外部服务器进行交互。
    当Agent调用MCP工具时，会生成此观察来记录操作结果。
    
    Attributes:
        observation (str): 观察类型，固定为MCP
        name (str): 被调用的MCP工具的名称，默认为空字符串
        arguments (dict[str, Any]): 传递给MCP工具的参数字典
    """

    observation: str = ObservationType.MCP  # 观察类型标识
    name: str = ''  # 被调用的MCP工具名称
    arguments: dict[str, Any] = field(
        default_factory=dict
    )  # 传递给MCP工具的参数字典

    @property
    def message(self) -> str:
        """返回消息内容
        
        Returns:
            str: 返回MCP操作的具体内容
        """
        return self.content