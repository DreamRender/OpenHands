from dataclasses import dataclass

from openhands.core.schema import ActionType
from openhands.events.action.action import Action


@dataclass
class NullAction(Action):
    """空Action类
    
    表示不执行任何操作的Action。通常用于占位或表示Agent
    选择暂时不采取任何行动的情况。
    
    Attributes:
        action (str): Action类型，固定为ActionType.NULL
    """

    action: str = ActionType.NULL  # Action类型标识，表示空操作

    @property
    def message(self) -> str:
        """获取空Action的消息
        
        Returns:
            str: 固定返回"No action"表示无操作
        """
        return 'No action'