from dataclasses import dataclass

from openhands.core.schema import ObservationType
from openhands.events.observation.observation import Observation


@dataclass
class SuccessObservation(Observation):
    """成功观察类
    
    这个数据类表示成功Action的结果。
    当Agent执行的Action成功完成时，会生成此观察来标示操作的成功状态。
    这是一个通用的成功状态指示器，用于明确告知Agent操作已成功执行。
    
    Attributes:
        observation (str): 观察类型，固定为SUCCESS
    """

    observation: str = ObservationType.SUCCESS  # 观察类型标识

    @property
    def message(self) -> str:
        """返回消息内容
        
        Returns:
            str: 返回成功操作的具体内容描述
        """
        return self.content