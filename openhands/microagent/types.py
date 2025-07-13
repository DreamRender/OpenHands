from enum import Enum

from pydantic import BaseModel, Field

from openhands.core.config.mcp_config import (
    MCPConfig,
)


class MicroagentType(str, Enum):
    """Microagent的类型枚举。
    
    定义了系统中支持的所有Microagent类型，每种类型具有不同的激活条件和行为模式。
    """

    KNOWLEDGE = 'knowledge'  # 可选的Microagent，通过关键词触发
    """Knowledge类型Microagent
    
    这是可选的Microagent，通过在对话中检测到特定关键词时激活。
    主要用于提供专业领域知识、最佳实践指导等。
    """
    
    REPO_KNOWLEDGE = 'repo'  # 始终活动的Microagent
    """Repository Knowledge类型Microagent
    
    这是始终活动的Microagent，一旦加载就持续生效。
    主要用于提供Repository特定的规则、约定和指导原则。
    """
    
    TASK = 'task'  # 需要用户输入的特殊类型Task Microagent
    """Task类型Microagent
    
    这是需要用户输入的特殊类型Microagent。
    通过特定格式（如/{agent_name}）触发，并可能需要用户提供额外参数。
    """


class InputMetadata(BaseModel):
    """Task Microagent输入的元数据。
    
    定义了Task类型Microagent所需输入参数的结构化信息，
    包括参数名称和描述，用于指导用户提供正确的输入。
    """

    name: str
    """输入参数的名称
    
    用作参数的唯一标识符，通常用于变量替换和用户界面显示。
    """
    
    description: str
    """输入参数的描述
    
    向用户说明此参数的用途、期望格式和可能的取值范围。
    """


class MicroagentMetadata(BaseModel):
    """所有Microagent的元数据。
    
    这是Microagent的核心配置结构，包含了Microagent的所有配置信息，
    包括基本信息、行为配置和扩展功能配置。
    """

    name: str = 'default'
    """Microagent的名称
    
    用作Microagent的唯一标识符，默认值为'default'。
    在文件系统中通常从文件路径派生，也可以在元数据中显式指定。
    """
    
    type: MicroagentType = Field(default=MicroagentType.REPO_KNOWLEDGE)
    """Microagent的类型
    
    决定了Microagent的激活条件和行为模式。
    默认为REPO_KNOWLEDGE类型，表示始终活动的Repository知识型Agent。
    """
    
    version: str = Field(default='1.0.0')
    """Microagent的版本号
    
    用于版本管理和兼容性检查，遵循语义化版本规范。
    默认版本为'1.0.0'。
    """
    
    agent: str = Field(default='CodeActAgent')
    """使用的Agent类型
    
    指定此Microagent应该与哪种Agent类型配合使用。
    默认为'CodeActAgent'，这是一个专门处理代码相关任务的Agent。
    """
    
    triggers: list[str] = []  # optional, only exists for knowledge microagents
    """触发器列表
    
    可选字段，仅对knowledge类型的Microagent存在。
    包含一组关键词或短语，当在对话中检测到这些词时会激活此Microagent。
    """
    
    inputs: list[InputMetadata] = []  # optional, only exists for task microagents
    """输入参数列表
    
    可选字段，仅对task类型的Microagent存在。
    定义了此Microagent执行任务时需要的用户输入参数。
    """
    
    mcp_tools: MCPConfig | None = (
        None  # optional, for microagents that provide additional MCP tools
    )
    """MCP工具配置
    
    可选字段，用于为提供额外MCP（Model Context Protocol）工具的Microagent进行配置。
    MCP工具可以扩展Microagent的功能，提供额外的工具和服务集成能力。
    """