from openhands.critic.base import BaseCritic, CriticResult
from openhands.events import Event
from openhands.events.action import Action, AgentFinishAction


class AgentFinishedCritic(BaseCritic):
    """
    Agent完成状态Critic类。
    
    这是一个基于简单规则的Critic实现，用于检查Agent是否正确完成了任务。
    主要检查逻辑：
    1. 检查Event序列中的最后一个Action是否为AgentFinishAction
    2. 如果提供了git补丁，检查补丁是否为空
    
    评估规则：
    - 如果git补丁提供但为空：返回分数0，消息指出补丁为空
    - 如果最后一个Action是AgentFinishAction：返回分数1，表示Agent已完成
    - 如果最后一个Action不是AgentFinishAction：返回分数0，表示Agent未完成
    """

    def __init__(self):
        """
        初始化AgentFinishedCritic实例。
        
        这个Critic不需要任何配置参数，因此初始化方法为空。
        """
        pass

    def evaluate(
        self, events: list[Event], git_patch: str | None = None
    ) -> CriticResult:
        """
        评估Agent是否成功完成任务。
        
        通过检查Event序列和git补丁来判断Agent的完成状态。
        首先检查git补丁的有效性，然后检查最后一个Action的类型。
        
        Args:
            events (list[Event]): Agent执行过程中产生的Event列表
            git_patch (str | None, optional): 可选的git补丁字符串。默认为None
            
        Returns:
            CriticResult: 评估结果，包含以下情况：
                - score=0, message='Git patch is empty.': git补丁为空
                - score=1, message='Agent finished.': Agent成功完成
                - score=0, message='Agent did not finish.': Agent未完成
        """
        # 从Event列表末尾开始查找最后一个Action类型的Event
        # 使用reversed()倒序遍历，找到第一个Action类型的Event即为最后一个Action
        last_action = next((h for h in reversed(events) if isinstance(h, Action)), None)

        # 如果提供了git补丁但补丁内容为空（去除空白字符后长度为0）
        # 则认为没有实际的代码变更，返回失败评估
        if git_patch is not None and len(git_patch.strip()) == 0:
            return CriticResult(score=0, message='Git patch is empty.')

        # 检查最后一个Action是否为AgentFinishAction类型
        # 如果是，说明Agent主动表示任务已完成
        if isinstance(last_action, AgentFinishAction):
            return CriticResult(score=1, message='Agent finished.')
        else:
            # 如果最后一个Action不是AgentFinishAction，说明Agent没有明确表示完成
            return CriticResult(score=0, message='Agent did not finish.')