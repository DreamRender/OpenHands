"""LocAgent模块：基于位置感知的代码分析Agent实现。

该模块定义了LocAgent类，继承自CodeActAgent，
专门用于代码库的结构分析、实体搜索和依赖关系探索。
"""

from typing import TYPE_CHECKING

import openhands.agenthub.loc_agent.function_calling as locagent_function_calling
from openhands.agenthub.codeact_agent import CodeActAgent
from openhands.core.config import AgentConfig
from openhands.core.logger import openhands_logger as logger
from openhands.llm.llm import LLM

# 类型检查时导入，避免循环导入问题
if TYPE_CHECKING:
    from openhands.events.action import Action
    from openhands.llm.llm import ModelResponse


class LocAgent(CodeActAgent):
    """位置感知代码分析Agent。
    
    LocAgent是专门用于代码库分析的Agent，继承自CodeActAgent。
    它提供了代码结构探索、实体内容搜索和依赖关系分析等功能，
    特别适用于大型代码库的理解和导航。
    
    主要功能：
    1. 代码树结构探索 - 分析目录、文件、类、函数间的层次关系
    2. 实体内容搜索 - 根据实体名称获取完整实现
    3. 代码片段搜索 - 基于关键词或行号搜索相关代码
    4. 依赖关系分析 - 探索上游和下游依赖关系
    
    Attributes:
        VERSION (str): Agent版本号，当前为 '1.0'
        tools (list): Agent支持的工具列表，包含代码分析相关的工具
    
    Note:
        - 基于预构建的代码图谱进行分析
        - 支持多种实体类型：directory、file、class、function
        - 支持多种依赖类型：contains、imports、invokes、inherits
    """
    
    VERSION = '1.0'  # Agent版本号

    def __init__(
        self,
        llm: LLM,
        config: AgentConfig,
    ) -> None:
        """初始化LocAgent实例。

        设置Agent的基本配置，加载专用工具集，并配置日志记录。
        继承父类的初始化逻辑，并添加LocAgent特有的工具配置。

        Args:
            llm (LLM): Agent使用的大语言模型实例，负责处理自然语言交互
            config (AgentConfig): Agent的配置对象，包含行为参数和设置
        
        Note:
            - 调用父类构造函数完成基础初始化
            - 加载LocAgent专用的工具集（代码分析工具）
            - 启用调试日志记录已加载的工具信息
        """
        # 调用父类构造函数，完成基础Agent初始化
        super().__init__(llm, config)

        # 获取LocAgent专用的工具集
        # 包括：探索树结构、搜索代码片段、获取实体内容等工具
        self.tools = locagent_function_calling.get_tools()
        
        # 记录已加载的工具信息，用于调试和监控
        logger.debug(
            f'TOOLS loaded for LocAgent: {", ".join([tool.get("function").get("name") for tool in self.tools])}'
        )

    def response_to_actions(self, response: 'ModelResponse') -> list['Action']:
        """将Model响应转换为Action列表。
        
        处理LLM的响应并将其转换为可执行的Action对象。
        这是Agent与外部系统交互的关键接口，负责解析工具调用和消息内容。
        
        Args:
            response (ModelResponse): LLM生成的响应对象，包含：
                - 工具调用信息（如果有）
                - 文本内容
                - 选择列表和Metadata
        
        Returns:
            list[Action]: 转换后的Action对象列表，可能包含：
                - IPythonRunCellAction: 执行代码分析工具的Action
                - AgentFinishAction: 完成任务的Action  
                - MessageAction: 纯文本消息Action
        
        Note:
            - 委托给locagent_function_calling模块处理具体的转换逻辑
            - 传递MCP工具名称列表以支持扩展工具集成
            - 每个Action都会包含必要的Metadata用于追踪和调试
        """
        return locagent_function_calling.response_to_actions(
            response,
            mcp_tool_names=list(self.mcp_tools.keys()),  # 传递MCP工具名称列表
        )
