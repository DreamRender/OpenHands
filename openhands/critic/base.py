import abc

from pydantic import BaseModel

from openhands.events import Event


class CriticResult(BaseModel):
    """
    Critic结果类，用于封装评估结果。
    
    这个类继承自Pydantic的BaseModel，用于表示Critic对Agent执行结果的评估。
    包含一个评分和一个描述性消息。
    
    Attributes:
        score (float): 评估得分，范围通常为0-1之间，数值越高表示质量越好
        message (str): 评估结果的描述性消息，用于解释评分的原因
    """

    score: float  # 评估得分
    message: str  # 评估消息

    @property
    def success(self) -> bool:
        """
        判断Agent是否成功完成任务。
        
        基于评分来判断Agent的执行是否成功。当评分大于等于0.5时认为成功。
        
        Returns:
            bool: 如果评分>=0.5返回True，否则返回False
        """
        return self.score >= 0.5


class BaseCritic(abc.ABC):
    """
    Critic基类，定义了评估Agent执行质量的抽象接口。
    
    这是一个抽象基类，用于定义Critic的通用行为。Critic是一个函数，
    它接收一系列Event和可选的git补丁，然后返回对这些Event质量的评估结果。
    所有具体的Critic实现都必须继承这个基类并实现evaluate方法。
    """

    @abc.abstractmethod
    def evaluate(
        self, events: list[Event], git_patch: str | None = None
    ) -> CriticResult:
        """
        评估Event列表的质量。
        
        这是一个抽象方法，必须在子类中实现。用于分析Agent执行过程中产生的
        Event序列，并可能结合git补丁信息来评估整体执行质量。
        
        Args:
            events (list[Event]): Agent执行过程中产生的Event列表，记录了所有操作和观察
            git_patch (str | None, optional): 可选的git补丁信息，用于评估代码变更质量。默认为None
            
        Returns:
            CriticResult: 包含评分和评估消息的结果对象
            
        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        pass