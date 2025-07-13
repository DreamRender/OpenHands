from dataclasses import dataclass, field

from openhands.core.schema import ObservationType
from openhands.events.event import RecallType
from openhands.events.observation.observation import Observation


@dataclass
class AgentStateChangedObservation(Observation):
    """Agent状态变更观察类
    
    这个数据类表示委托给另一个Agent后返回的结果。
    用于记录Agent在状态转换过程中的信息。
    
    Attributes:
        agent_state (str): Agent的新状态
        reason (str): 状态变更的原因，默认为空字符串
        observation (str): 观察类型，固定为AGENT_STATE_CHANGED
    """

    agent_state: str  # Agent的当前状态
    reason: str = ''  # 状态变更的原因描述
    observation: str = ObservationType.AGENT_STATE_CHANGED  # 观察类型标识

    @property
    def message(self) -> str:
        """返回消息内容
        
        Returns:
            str: 返回空字符串，表示没有特定的消息内容
        """
        return ''


@dataclass
class AgentCondensationObservation(Observation):
    """Agent压缩观察类
    
    Condenser操作的输出结果。
    用于表示Agent执行压缩操作后的观察结果。
    
    Attributes:
        observation (str): 观察类型，固定为CONDENSE
    """

    observation: str = ObservationType.CONDENSE  # 观察类型标识

    @property
    def message(self) -> str:
        """返回消息内容
        
        Returns:
            str: 返回压缩操作的具体内容
        """
        return self.content


@dataclass
class AgentThinkObservation(Observation):
    """Agent思考观察类
    
    思考Action的输出结果。
    
    在实际应用中，这是一个无操作(no-op)，因为它只会向Agent回复一个
    静态消息，确认思考已被记录。主要用于记录Agent的思考过程。
    
    Attributes:
        observation (str): 观察类型，固定为THINK
    """

    observation: str = ObservationType.THINK  # 观察类型标识

    @property
    def message(self) -> str:
        """返回消息内容
        
        Returns:
            str: 返回思考的具体内容
        """
        return self.content


@dataclass
class MicroagentKnowledge:
    """MicroAgent知识容器类
    
    表示从触发的MicroAgent获得的知识信息。
    MicroAgent是系统中的小型专用Agent，每个都有特定的触发词和知识领域。
    
    Attributes:
        name (str): 被触发的MicroAgent的名称
        trigger (str): 触发这个MicroAgent的关键词
        content (str): 从MicroAgent获得的实际内容/知识
    """

    name: str      # MicroAgent的名称标识
    trigger: str   # 触发MicroAgent的关键词
    content: str   # MicroAgent提供的知识内容


@dataclass
class RecallObservation(Observation):
    """回忆观察类
    
    从一个或多个MicroAgent检索内容的结果。
    这个类用于封装从知识库或MicroAgent系统中召回的信息。
    
    Attributes:
        recall_type (RecallType): 回忆的类型（工作空间上下文或MicroAgent知识）
        observation (str): 观察类型，固定为RECALL
        
        # 工作空间上下文相关字段
        repo_name (str): Repository名称
        repo_directory (str): Repository目录路径
        repo_instructions (str): Repository指令说明
        runtime_hosts (dict[str, int]): 运行时主机映射，键为主机名，值为端口号
        additional_agent_instructions (str): 额外的Agent指令
        date (str): 日期信息
        custom_secrets_descriptions (dict[str, str]): 自定义密钥描述映射
        conversation_instructions (str): 对话指令
        
        # 知识相关字段
        microagent_knowledge (list[MicroagentKnowledge]): MicroAgent知识列表
    """

    recall_type: RecallType  # 回忆类型：工作空间上下文或MicroAgent知识
    observation: str = ObservationType.RECALL  # 观察类型标识

    # 工作空间上下文相关字段
    repo_name: str = ''  # Repository的名称
    repo_directory: str = ''  # Repository的目录路径
    repo_instructions: str = ''  # Repository的使用指令
    runtime_hosts: dict[str, int] = field(default_factory=dict)  # 运行时主机配置
    additional_agent_instructions: str = ''  # 给Agent的附加指令
    date: str = ''  # 当前日期
    custom_secrets_descriptions: dict[str, str] = field(default_factory=dict)  # 自定义密钥说明
    conversation_instructions: str = ''  # 对话相关指令

    # 知识相关字段
    microagent_knowledge: list[MicroagentKnowledge] = field(default_factory=list)
    """
    MicroagentKnowledge对象列表，每个对象包含一个被触发的MicroAgent的信息。

    示例:
    [
        MicroagentKnowledge(
            name="python_best_practices",
            trigger="python", 
            content="总是为Python项目使用虚拟环境。"
        ),
        MicroagentKnowledge(
            name="git_workflow",
            trigger="git",
            content="为每个功能或bug修复创建新分支。"
        )
    ]
    """

    @property
    def message(self) -> str:
        """返回消息内容
        
        根据回忆类型返回相应的消息。
        
        Returns:
            str: 如果是工作空间上下文则返回"Added workspace context"，
                 否则返回"Added microagent knowledge"
        """
        return (
            'Added workspace context'
            if self.recall_type == RecallType.WORKSPACE_CONTEXT
            else 'Added microagent knowledge'
        )

    def __str__(self) -> str:
        """返回字符串表示
        
        构建RecallObservation的详细字符串表示，根据回忆类型显示不同的字段信息。
        
        Returns:
            str: 格式化的字符串表示
        """
        # 构建字符串表示
        fields = []
        
        # 根据回忆类型添加相应字段
        if self.recall_type == RecallType.WORKSPACE_CONTEXT:
            fields.extend(
                [
                    f'recall_type={self.recall_type}',
                    f'repo_name={self.repo_name}',
                    f'repo_instructions={self.repo_instructions[:20]}...',  # 只显示前20个字符
                    f'runtime_hosts={self.runtime_hosts}',
                    f'additional_agent_instructions={self.additional_agent_instructions[:20]}...',
                    f'date={self.date}'
                    f'custom_secrets_descriptions={self.custom_secrets_descriptions}',
                    f'conversation_instructions={self.conversation_instructions[0:20]}...',
                ]
            )
        else:
            # 非工作空间上下文类型，只显示基本信息
            fields.extend(
                [
                    f'recall_type={self.recall_type}',
                ]
            )
            
        # 如果有MicroAgent知识，添加其名称列表
        if self.microagent_knowledge:
            fields.extend(
                [
                    f'microagent_knowledge={", ".join([m.name for m in self.microagent_knowledge])}',
                ]
            )

        return f'**RecallObservation**\n{", ".join(fields)}'