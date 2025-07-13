from dataclasses import dataclass

from openhands.core.schema import ObservationType
from openhands.events.observation.observation import Observation


@dataclass
class ErrorObservation(Observation):
    """错误观察类

    这个数据类表示Agent遇到的错误。

    这是LLM可以从中恢复的错误类型。
    例如，编辑文件后的语法检查器错误。
    这种错误不是致命的，Agent可以根据错误信息调整后续操作来解决问题。

    Attributes:
        observation (str): 观察类型，固定为ERROR
        error_id (str): 错误标识符，用于唯一标识特定的错误，默认为空字符串
    """

    observation: str = ObservationType.ERROR  # 观察类型标识
    error_id: str = ''  # 错误的唯一标识符

    @property
    def message(self) -> str:
        """返回消息内容
        
        Returns:
            str: 返回错误的具体内容描述
        """
        return self.content

    def __str__(self) -> str:
        """返回字符串表示
        
        Returns:
            str: 格式化的错误观察字符串表示
        """
        return f'**ErrorObservation**\n{self.content}'