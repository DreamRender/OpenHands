import os
import sys
from collections import deque
from typing import TYPE_CHECKING

# 类型检查相关导入 - 仅在类型检查时导入，避免运行时循环依赖
if TYPE_CHECKING:
    from litellm import ChatCompletionToolParam

    from openhands.events.action import Action
    from openhands.llm.llm import ModelResponse

# 导入 CodeAct Agent 的函数调用功能模块
import openhands.agenthub.codeact_agent.function_calling as codeact_function_calling
# 导入各种工具模块
from openhands.agenthub.codeact_agent.tools.bash import create_cmd_run_tool
from openhands.agenthub.codeact_agent.tools.browser import BrowserTool
from openhands.agenthub.codeact_agent.tools.condensation_request import (
    CondensationRequestTool,
)
from openhands.agenthub.codeact_agent.tools.finish import FinishTool
from openhands.agenthub.codeact_agent.tools.ipython import IPythonTool
from openhands.agenthub.codeact_agent.tools.llm_based_edit import LLMBasedFileEditTool
from openhands.agenthub.codeact_agent.tools.str_replace_editor import (
    create_str_replace_editor_tool,
)
from openhands.agenthub.codeact_agent.tools.think import ThinkTool
# 导入核心框架组件
from openhands.controller.agent import Agent
from openhands.controller.state.state import State
from openhands.core.config import AgentConfig
from openhands.core.logger import openhands_logger as logger
from openhands.core.message import Message
from openhands.events.action import AgentFinishAction, MessageAction
from openhands.events.event import Event
from openhands.llm.llm import LLM
from openhands.llm.llm_utils import check_tools
from openhands.memory.condenser import Condenser
from openhands.memory.condenser.condenser import Condensation, View
from openhands.memory.conversation_memory import ConversationMemory
from openhands.runtime.plugins import (
    AgentSkillsRequirement,
    JupyterRequirement,
    PluginRequirement,
)
from openhands.utils.prompt import PromptManager


class CodeActAgent(Agent):
    """CodeAct Agent 类 - 一个简约化的代理实现。
    
    CodeAct Agent 是一个基于代码动作空间的 AI 代理，实现了 CodeAct 理念
    (论文链接: https://arxiv.org/abs/2402.01030)，该理念将 LLM 代理的动作
    统一到代码动作空间中，兼具简洁性和高性能。
    
    概述:
    在每个回合中，代理可以：
    1. 对话: 用自然语言与人类交流，询问澄清、确认等
    2. 代码动作: 通过执行代码来完成任务
       - 执行任何有效的 Linux bash 命令
       - 使用交互式 Python 解释器执行 Python 代码
    
    继承自:
        Agent: OpenHands 框架的基础 Agent 类
    
    Attributes:
        VERSION (str): Agent 版本号
        sandbox_plugins (list[PluginRequirement]): 沙箱环境所需的插件列表
        pending_actions (deque['Action']): 待执行的动作队列
        tools (list['ChatCompletionToolParam']): 可用工具列表
        conversation_memory (ConversationMemory): 对话内存管理器
        condenser (Condenser): 历史事件压缩器
    """
    
    VERSION = '2.2'
    """Agent 版本标识符"""
    
    # 沙箱环境插件配置
    # 注意：AgentSkillsRequirement 需要在 JupyterRequirement 之前，
    # 因为 AgentSkillsRequirement 提供了大量 Python 函数，
    # 需要在 Jupyter 初始化之前就绪，以便 Jupyter 可以使用这些函数
    sandbox_plugins: list[PluginRequirement] = [
        AgentSkillsRequirement(),  # Agent 技能要求 - 提供基础技能函数
        JupyterRequirement(),      # Jupyter 环境要求 - 提供 Python 代码执行能力
    ]

    def __init__(
        self,
        llm: LLM,
        config: AgentConfig,
    ) -> None:
        """初始化 CodeActAgent 实例。

        Args:
            llm (LLM): 此 Agent 使用的大语言模型实例
            config (AgentConfig): Agent 的配置参数
        """
        # 调用父类初始化方法
        super().__init__(llm, config)
        
        # 初始化待执行动作队列 - 使用 deque 提供高效的队列操作
        self.pending_actions: deque['Action'] = deque()
        
        # 重置 Agent 内部状态
        self.reset()
        
        # 获取并配置可用工具列表
        self.tools = self._get_tools()

        # 创建对话内存管理实例 - 负责管理对话历史和上下文
        self.conversation_memory = ConversationMemory(self.config, self.prompt_manager)

        # 从配置创建 Condenser 实例 - 负责压缩长期历史记录
        self.condenser = Condenser.from_config(self.config.condenser)
        logger.debug(f'Using condenser: {type(self.condenser)}')

    @property
    def prompt_manager(self) -> PromptManager:
        """获取提示管理器实例。
        
        懒加载模式 - 仅在首次访问时创建 PromptManager 实例。
        PromptManager 负责管理系统提示和各种提示模板。
        
        Returns:
            PromptManager: 提示管理器实例
        """
        if self._prompt_manager is None:
            # 基于当前文件目录下的 prompts 文件夹创建 PromptManager
            self._prompt_manager = PromptManager(
                prompt_dir=os.path.join(os.path.dirname(__file__), 'prompts'),
                system_prompt_filename=self.config.system_prompt_filename,
            )

        return self._prompt_manager

    def _get_tools(self) -> list['ChatCompletionToolParam']:
        """获取可用工具列表。
        
        根据配置和模型类型决定使用哪些工具以及工具描述的详细程度。
        对于某些模型（如 GPT 系列），使用简短工具描述以避免超出 token 限制。
        
        Returns:
            list['ChatCompletionToolParam']: 配置好的工具参数列表
        """
        # 对于这些模型，使用简短工具描述（< 1024 tokens）
        # 以避免超出 OpenAI 工具描述的 token 限制
        SHORT_TOOL_DESCRIPTION_LLM_SUBSTRS = ['gpt-', 'o3', 'o1', 'o4']

        # 判断是否使用简短工具描述
        use_short_tool_desc = False
        if self.llm is not None:
            use_short_tool_desc = any(
                model_substr in self.llm.config.model
                for model_substr in SHORT_TOOL_DESCRIPTION_LLM_SUBSTRS
            )

        # 根据配置动态构建工具列表
        tools = []
        
        # 命令行工具 - 执行 bash 命令
        if self.config.enable_cmd:
            tools.append(create_cmd_run_tool(use_short_description=use_short_tool_desc))
        
        # 思考工具 - 记录 Agent 的思考过程
        if self.config.enable_think:
            tools.append(ThinkTool)
        
        # 完成工具 - 标记任务完成
        if self.config.enable_finish:
            tools.append(FinishTool)
        
        # 历史压缩请求工具 - 请求压缩对话历史
        if self.config.enable_condensation_request:
            tools.append(CondensationRequestTool)
        
        # 浏览器工具 - 网页交互功能
        if self.config.enable_browsing:
            if sys.platform == 'win32':
                # Windows 运行时暂不支持浏览功能
                logger.warning('Windows runtime does not support browsing yet')
            else:
                tools.append(BrowserTool)
        
        # Jupyter/IPython 工具 - Python 代码执行
        if self.config.enable_jupyter:
            tools.append(IPythonTool)
        
        # 文件编辑工具 - 选择使用 LLM 基础编辑器或字符串替换编辑器
        if self.config.enable_llm_editor:
            tools.append(LLMBasedFileEditTool)
        elif self.config.enable_editor:
            tools.append(
                create_str_replace_editor_tool(
                    use_short_description=use_short_tool_desc
                )
            )
        
        return tools

    def reset(self) -> None:
        """重置 CodeAct Agent 的内部状态。
        
        清空待执行动作队列，但保留 LLM 性能指标。
        通常在开始新对话或任务时调用。
        """
        super().reset()
        # 只清空待执行动作队列，不清除 LLM 指标
        self.pending_actions.clear()

    def step(self, state: State) -> 'Action':
        """执行 CodeAct Agent 的一个步骤。

        这包括收集前序步骤的信息，并提示模型生成下一个要执行的命令。
        处理流程：
        1. 检查是否有待执行动作
        2. 检查用户是否要求退出
        3. 对历史事件进行压缩处理
        4. 构建消息历史
        5. 调用 LLM 生成响应
        6. 将响应转换为动作

        Args:
            state (State): 用于获取更新信息的状态对象

        Returns:
            Action: 下一个要执行的动作，可能是以下类型之一：
                - CmdRunAction: 要运行的 bash 命令
                - IPythonRunCellAction: 要运行的 IPython 代码
                - AgentDelegateAction: 委托给（子）任务的动作
                - MessageAction: 消息动作（如请求澄清）
                - AgentFinishAction: 结束交互
        """
        # 如果有待执行动作，优先处理
        if self.pending_actions:
            return self.pending_actions.popleft()

        # 检查用户是否要求退出
        latest_user_message = state.get_last_user_message()
        if latest_user_message and latest_user_message.content.strip() == '/exit':
            return AgentFinishAction()

        # 压缩状态中的事件。如果得到视图，将其传递给对话管理器处理；
        # 如果得到压缩事件，则直接返回该事件而不是动作。
        # 控制器将立即要求 Agent 使用新视图再次执行步骤。
        condensed_history: list[Event] = []
        match self.condenser.condensed_history(state):
            case View(events=events):
                # 获得压缩后的事件视图
                condensed_history = events

            case Condensation(action=condensation_action):
                # 需要执行压缩动作
                return condensation_action

        logger.debug(
            f'Processing {len(condensed_history)} events from a total of {len(state.history)} events'
        )

        # 获取初始用户消息
        initial_user_message = self._get_initial_user_message(state.history)
        
        # 构建 LLM 对话消息列表
        messages = self._get_messages(condensed_history, initial_user_message)
        
        # 准备 LLM 调用参数
        params: dict = {
            'messages': self.llm.format_messages_for_llm(messages),
        }
        # 添加工具参数和元数据
        params['tools'] = check_tools(self.tools, self.llm.config)
        params['extra_body'] = {'metadata': state.to_llm_metadata(agent_name=self.name)}
        
        # 调用 LLM 获取响应
        response = self.llm.completion(**params)
        logger.debug(f'Response from LLM: {response}')
        
        # 将 LLM 响应转换为动作列表
        actions = self.response_to_actions(response)
        logger.debug(f'Actions after response_to_actions: {actions}')
        
        # 将动作添加到待执行队列
        for action in actions:
            self.pending_actions.append(action)
            
        # 返回队列中的第一个动作
        return self.pending_actions.popleft()

    def _get_initial_user_message(self, history: list[Event]) -> MessageAction:
        """从完整历史中找到初始用户消息动作。
        
        Args:
            history (list[Event]): 完整的事件历史列表
            
        Returns:
            MessageAction: 第一个用户消息动作
            
        Raises:
            ValueError: 如果在历史中找不到初始用户消息
        """
        initial_user_message: MessageAction | None = None
        # 遍历历史事件，寻找第一个用户消息
        for event in history:
            if isinstance(event, MessageAction) and event.source == 'user':
                initial_user_message = event
                break

        if initial_user_message is None:
            # 这在有效对话中不应该发生
            logger.error(
                f'CRITICAL: Could not find the initial user MessageAction in the full {len(history)} events history.'
            )
            # 根据所需的鲁棒性，可以抛出错误或创建虚拟动作并记录错误
            raise ValueError(
                'Initial user message not found in history. Please report this issue.'
            )
        return initial_user_message

    def _get_messages(
        self, events: list[Event], initial_user_message: MessageAction
    ) -> list[Message]:
        """构建 LLM 对话的消息历史。

        此方法通过处理状态中的事件来构建结构化的对话历史，
        并将其格式化为 LLM 可以理解的消息。它处理常规消息流和函数调用场景。

        方法执行以下步骤：
        1. 检查事件中的 SystemMessageAction，如果缺少则添加一个（遗留支持）
        2. 将事件（Action 和 Observation）处理为消息，包括 SystemMessageAction
        3. 在函数调用模式中处理工具调用及其响应
        4. 管理消息角色交替（user/assistant/tool）
        5. 为特定 LLM 提供商（如 Anthropic）应用缓存
        6. 为非函数调用模式添加环境提醒

        Args:
            events (list[Event]): 要转换为消息的事件列表
            initial_user_message (MessageAction): 初始用户消息

        Returns:
            list[Message]: 格式化的消息列表，准备用于 LLM 消费，包括：
                - 带提示的系统消息（来自 SystemMessageAction）
                - 动作消息（来自用户和助手）
                - 观察消息（包括工具响应）
                - 环境提醒（在非函数调用模式中）

        Note:
            - 在函数调用模式中，会仔细跟踪工具调用及其响应以维持正确的对话流
            - 来自同一角色的消息会被合并以防止连续的同角色消息
            - 对于 Anthropic 模型，根据其文档缓存特定消息
        """
        if not self.prompt_manager:
            raise Exception('Prompt Manager not instantiated.')

        # 使用 ConversationMemory 处理事件（包括 SystemMessageAction）
        messages = self.conversation_memory.process_events(
            condensed_history=events,
            initial_user_action=initial_user_message,
            max_message_chars=self.llm.config.max_message_chars,
            vision_is_active=self.llm.vision_is_active(),
        )

        # 如果 LLM 支持提示缓存，应用缓存策略
        if self.llm.is_caching_prompt_active():
            self.conversation_memory.apply_prompt_caching(messages)

        return messages

    def response_to_actions(self, response: 'ModelResponse') -> list['Action']:
        """将 LLM 响应转换为动作列表。
        
        Args:
            response (ModelResponse): LLM 的响应对象
            
        Returns:
            list['Action']: 从响应中解析出的动作列表
        """
        return codeact_function_calling.response_to_actions(
            response,
            mcp_tool_names=list(self.mcp_tools.keys()),
        )