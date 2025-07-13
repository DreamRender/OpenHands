from dataclasses import dataclass

from openhands.core.schema import ObservationType
from openhands.events.observation.observation import Observation


@dataclass
class NullObservation(Observation):
    """空观察类
    
    这个数据类表示一个空的观察结果。
    当产生的Action不可执行时使用此观察类型。
    在系统中，如果某个Action因为各种原因无法被执行（如参数错误、环境不满足等），
    就会返回NullObservation来表示没有产生有效的观察结果。
    
    Attributes:
        observation (str): 观察类型，固定为NULL
    """

    observation: str = ObservationType.NULL  # 观察类型标识

    @property
    def message(self) -> str:
        """返回消息内容
        
        Returns:
            str: 返回固定的无观察消息
        """
        return 'No observation'