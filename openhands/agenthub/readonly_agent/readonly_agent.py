"""
readonly_agent.py - ReadOnlyAgent类定义模块

ReadOnlyAgent - CodeActAgent的专用版本，仅使用只读工具。

该Agent专为安全地探索代码库而设计，不会进行任何修改。
它只能访问不修改系统的工具：grep、glob、view、think、finish、web_read。

使用场景：
1. 探索代码库以了解其结构
2. 搜索特定模式或代码
3. 在不进行任何更改的情况下进行研究

当您准备进行更改时，请切换到常规的CodeActAgent。
"""

import os
from typing import TYPE_CHECKING

# 类型检查时导入，避免运行时循环导入
if TYPE_CHECKING:
    from litellm import ChatCompletionToolParam

    from openhands.events.action import Action
    from openhands.llm.llm import ModelResponse

from openhands.agenthub.codeact_agent.codeact_agent import CodeActAgent
from openhands.agenthub.readonly_agent import (
    function_calling as readonly_function_calling,
)
from openhands.core.config import AgentConfig
from openhands.core.logger import openhands_logger as logger
from openhands.llm.llm import LLM
from openhands.utils.prompt import PromptManager


class ReadOnlyAgent(CodeActAgent):
    """
    ReadOnlyAgent类 - 只读代理
    
    ReadOnlyAgent是CodeActAgent的专用版本，仅使用只读工具。
    
    该Agent专为安全地探索代码库而设计，不会进行任何修改。
    它只能访问不修改系统的工具：grep、glob、view、think、finish、web_read。
    
    使用此Agent的场景：
    1. 探索代码库以了解其结构
    2. 搜索特定模式或代码
    3. 在不进行任何更改的情况下进行研究
    
    当您准备进行更改时，请切换到常规的CodeActAgent。
    
    Attributes:
        VERSION (str): Agent版本号
    """
    
    VERSION = '1.0'  # Agent版本标识

    def __init__(
        self,
        llm: LLM,
        config: AgentConfig,
    ) -> None:
        """
        初始化ReadOnlyAgent类的新实例。

        Args:
            llm (LLM): 此Agent要使用的语言模型
            config (AgentConfig): 此Agent的配置对象
        """
        # 初始化CodeActAgent类；其中一些功能会被类方法重写
        super().__init__(llm, config)

        # 记录为ReadOnlyAgent加载的工具
        logger.debug(
            f'TOOLS loaded for ReadOnlyAgent: {", ".join([tool.get("function").get("name") for tool in self.tools])}'
        )

    @property
    def prompt_manager(self) -> PromptManager:
        """
        获取或创建PromptManager实例。
        
        设置我们自己的prompt manager，用于管理Agent的提示模板。
        
        Returns:
            PromptManager: 提示管理器实例
        """
        # 设置我们自己的prompt manager
        if self._prompt_manager is None:
            self._prompt_manager = PromptManager(
                # 设置提示目录为当前文件所在目录下的prompts文件夹
                prompt_dir=os.path.join(os.path.dirname(__file__), 'prompts'),
            )
        return self._prompt_manager

    def _get_tools(self) -> list['ChatCompletionToolParam']:
        """
        获取Agent可用的工具列表。
        
        重写父类方法，仅包含只读工具。
        从我们自己的function_calling模块获取只读工具。
        
        Returns:
            list[ChatCompletionToolParam]: 只读工具配置列表
        """
        # 重写工具列表，仅包含只读工具
        # 从我们自己的function_calling模块获取只读工具
        return readonly_function_calling.get_tools()

    def set_mcp_tools(self, mcp_tools: list[dict]) -> None:
        """
        为Agent设置MCP工具列表。
        
        ReadOnlyAgent不支持MCP工具，因此会忽略所有MCP工具设置。

        Args:
            mcp_tools (list[dict]): MCP工具列表（将被忽略）
        """
        logger.warning(
            'ReadOnlyAgent does not support MCP tools. MCP tools will be ignored by the agent.'
        )

    def response_to_actions(self, response: 'ModelResponse') -> list['Action']:
        """
        将Model响应转换为Action列表。
        
        使用ReadOnlyAgent专用的函数调用处理器来解析响应。
        
        Args:
            response (ModelResponse): 语言模型的响应对象
            
        Returns:
            list[Action]: 转换后的Action对象列表
        """
        # 使用readonly专用的function_calling模块处理响应
        return readonly_function_calling.response_to_actions(
            response, mcp_tool_names=list(self.mcp_tools.keys())
        )