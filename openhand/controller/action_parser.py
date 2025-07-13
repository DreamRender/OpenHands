from abc import ABC, abstractmethod
from typing import Any

from openhands.events.action import Action


class ActionParseError(Exception):
    """当LLM的响应无法解析为Action时抛出的异常。
    
    该异常用于表示在将LLM的原始响应转换为具体的Action对象时发生的错误。
    通常在解析过程中遇到格式不正确、缺少必要字段或类型不匹配等情况时抛出。
    """

    def __init__(self, error: str):
        """初始化Action解析错误。
        
        Args:
            error (str): 错误描述信息，说明解析失败的具体原因
        """
        self.error = error

    def __str__(self) -> str:
        """返回错误的字符串表示。
        
        Returns:
            str: 错误描述信息
        """
        return self.error


class ResponseParser(ABC):
    """响应解析器的抽象基类。
    
    这个抽象基类为专门用于从LLM响应中解析Action的响应解析器提供了通用接口。
    它定义了解析流程的标准方法，包括响应预处理和Action构建两个主要阶段。
    
    解析流程通常分为两步：
    1. parse_response: 从原始LLM响应中提取Action字符串
    2. parse_action: 将Action字符串转换为具体的Action对象
    """

    def __init__(
        self,
    ) -> None:
        """初始化响应解析器。
        
        注意：需要关注self.action_parsers列表中解析器的顺序，
        因为解析器会按照列表顺序依次尝试解析Action字符串。
        """
        # Action解析器列表，用于按顺序尝试解析不同格式的Action字符串
        self.action_parsers: list[ActionParser] = []

    @abstractmethod
    def parse(self, response: Any) -> Action:
        """从LLM响应中解析出Action对象。

        这是响应解析的主入口方法，整合了响应预处理和Action解析两个步骤。

        Args:
            response: LLM的原始响应，可以是字符串或字典格式

        Returns:
            Action: 从响应中解析得到的Action对象

        Raises:
            ActionParseError: 当响应无法解析为有效Action时抛出
        """
        pass

    @abstractmethod
    def parse_response(self, response: Any) -> str:
        """从LLM响应中提取Action字符串。

        这个方法负责从LLM的原始响应中提取包含Action信息的字符串部分。
        不同的LLM可能返回不同格式的响应（如纯文本、JSON等），
        此方法需要处理这些格式差异。

        Args:
            response: LLM的原始响应，可以是字符串或字典格式

        Returns:
            str: 提取出的Action字符串，包含Action的具体信息

        Raises:
            ActionParseError: 当无法从响应中提取有效Action字符串时抛出
        """
        pass

    @abstractmethod
    def parse_action(self, action_str: str) -> Action:
        """将Action字符串解析为Action对象。

        这个方法负责将经过预处理的Action字符串转换为具体的Action对象。
        会依次尝试使用action_parsers列表中的解析器进行解析。

        Args:
            action_str (str): 包含Action信息的字符串

        Returns:
            Action: 解析得到的Action对象

        Raises:
            ActionParseError: 当Action字符串无法解析为有效Action时抛出
        """
        pass


class ActionParser(ABC):
    """Action解析器的抽象基类。
    
    这个抽象基类为专门用于从Action字符串中解析特定类型Action的解析器提供了通用接口。
    每个具体的解析器负责识别和解析特定格式或类型的Action字符串。
    
    典型的使用流程：
    1. 使用check_condition检查字符串是否匹配该解析器
    2. 如果匹配，使用parse方法进行具体解析
    """

    @abstractmethod
    def check_condition(self, action_str: str) -> bool:
        """检查Action字符串是否可以被此解析器解析。
        
        这个方法用于判断给定的Action字符串是否符合当前解析器的解析条件。
        通常通过检查字符串的格式、关键字或结构来判断。
        
        Args:
            action_str (str): 待检查的Action字符串
            
        Returns:
            bool: 如果可以解析返回True，否则返回False
        """
        pass

    @abstractmethod
    def parse(self, action_str: str) -> Action:
        """从Action字符串中解析出Action对象。
        
        这个方法执行具体的解析逻辑，将符合条件的Action字符串
        转换为对应的Action对象。调用前应先通过check_condition确认可解析性。
        
        Args:
            action_str (str): 来自LLM响应的Action字符串
            
        Returns:
            Action: 解析得到的Action对象
            
        Raises:
            ActionParseError: 当解析过程中发生错误时抛出
        """
        pass