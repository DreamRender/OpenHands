from dataclasses import dataclass

from openhands.core.schema import ObservationType
from openhands.events.observation.observation import Observation


@dataclass
class AgentDelegateObservation(Observation):
    """Agent委托观察类

    这个数据类表示委托给另一个Agent后返回的结果。
    在多Agent系统中，一个Agent可以将任务委托给另一个专门的Agent来处理，
    这个类用于封装被委托Agent的执行结果。

    Attributes:
        content (str): 观察的内容，继承自父类Observation
        outputs (dict): 被委托Agent的输出结果，包含执行过程中产生的各种数据
        observation (str): 观察类型，固定为DELEGATE
    """

    outputs: dict  # 被委托Agent返回的输出数据字典
    observation: str = ObservationType.DELEGATE  # 观察类型标识

    @property
    def message(self) -> str:
        """返回消息内容
        
        Returns:
            str: 返回空字符串，表示委托操作没有特定的消息内容
        """
        return ''