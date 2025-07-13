from dataclasses import dataclass

from openhands.core.schema import ObservationType
from openhands.events.observation.observation import Observation


@dataclass
class UserRejectObservation(Observation):
    """用户拒绝观察类
    
    这个数据类表示被拒绝的Action的结果。
    当用户拒绝Agent提出的某个Action时，系统会生成此观察。
    这通常发生在需要用户确认的敏感操作中，用户选择拒绝执行该操作。
    
    Attributes:
        observation (str): 观察类型，固定为USER_REJECTED
    """

    observation: str = ObservationType.USER_REJECTED  # 观察类型标识

    @property
    def message(self) -> str:
        """返回消息内容
        
        Returns:
            str: 返回用户拒绝的具体内容
        """
        return self.content