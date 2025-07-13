from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openhands.controller.state.state import State
    from openhands.events.action import Action
    from openhands.events.action.message import SystemMessageAction
    from openhands.utils.prompt import PromptManager
from litellm import ChatCompletionToolParam

from openhands.core.config import AgentConfig
from openhands.core.exceptions import (
    AgentAlreadyRegisteredError,
    AgentNotRegisteredError,
)
from openhands.core.logger import openhands_logger as logger
from openhands.events.event import EventSource
from openhands.llm.llm import LLM
from openhands.runtime.plugins import PluginRequirement


class Agent(ABC):
    """Agent的抽象基类。
    
    这个抽象基类为专门用于执行特定指令并允许人类在执行过程中与Agent交互的Agent提供了通用接口。
    它跟踪执行状态并维护交互历史记录。
    
    Agent类采用注册机制管理不同类型的Agent实现，支持插件系统，
    并提供与LLM交互的标准化接口。每个Agent都有自己的配置、工具集和执行状态。
    
    Attributes:
        DEPRECATED (bool): 标记该Agent是否已被弃用
        _registry (dict): 类级别的Agent注册表，存储所有已注册的Agent类
        sandbox_plugins (list): 沙箱插件需求列表
        config_model (type): 指定Agent使用的配置模型类型
    """
    
    # 标记Agent是否已被弃用
    DEPRECATED = False
    
    # 类级别的Agent注册表，键为Agent名称，值为Agent类
    _registry: dict[str, type['Agent']] = {}
    
    # 沙箱插件需求列表，定义Agent运行时需要的插件
    sandbox_plugins: list[PluginRequirement] = []

    # 指定Agent使用的配置模型类，子类可以通过派生配置模型来覆盖此字段
    config_model: type[AgentConfig] = AgentConfig

    def __init__(
        self,
        llm: LLM,
        config: AgentConfig,
    ):
        """初始化Agent实例。
        
        Args:
            llm (LLM): 用于与语言模型交互的LLM实例
            config (AgentConfig): Agent的配置信息
        """
        # 语言模型实例，用于生成响应和处理对话
        self.llm = llm
        
        # Agent配置，包含各种运行参数和设置
        self.config = config
        
        # 执行完成状态标志，表示当前指令是否已完成执行
        self._complete = False
        
        # 提示管理器，负责管理系统提示和用户提示的生成
        self._prompt_manager: 'PromptManager' | None = None
        
        # MCP (Model Control Protocol) 工具字典，键为工具名称，值为工具参数
        self.mcp_tools: dict[str, ChatCompletionToolParam] = {}
        
        # 可用工具列表，包含Agent可以调用的所有工具
        self.tools: list = []

    @property
    def prompt_manager(self) -> 'PromptManager':
        """获取提示管理器实例。
        
        提示管理器负责生成系统消息、格式化用户输入和管理对话历史。
        如果尚未初始化，则抛出异常。
        
        Returns:
            PromptManager: 提示管理器实例
            
        Raises:
            ValueError: 当提示管理器未初始化时抛出
        """
        if self._prompt_manager is None:
            raise ValueError(f'Prompt manager not initialized for agent {self.name}')
        return self._prompt_manager

    def get_system_message(self) -> 'SystemMessageAction | None':
        """生成并返回包含系统消息和工具的SystemMessageAction。
        
        系统消息将作为事件流中的第一条消息添加，包含Agent的基本指令、
        可用工具列表和其他初始化信息。
        
        Returns:
            SystemMessageAction: 包含系统消息内容和工具的系统消息Action
            None: 如果生成系统消息时发生错误则返回None
        """
        # 在此处导入以避免循环导入
        from openhands.events.action.message import SystemMessageAction

        try:
            # 检查提示管理器是否已初始化
            if not self.prompt_manager:
                logger.warning(
                    f'[{self.name}] Prompt manager not initialized before getting system message'
                )
                return None

            # 从提示管理器获取系统消息内容
            system_message = self.prompt_manager.get_system_message()

            # 获取可用工具列表（如果存在）
            tools = getattr(self, 'tools', None)

            # 创建系统消息Action，包含消息内容、工具和Agent类名
            system_message_action = SystemMessageAction(
                content=system_message, tools=tools, agent_class=self.name
            )
            # 设置事件源为Agent
            system_message_action._source = EventSource.AGENT  # type: ignore

            return system_message_action
        except Exception as e:
            logger.warning(f'[{self.name}] Failed to generate system message: {e}')
            return None

    @property
    def complete(self) -> bool:
        """指示当前指令执行是否完成。

        Returns:
            bool: 如果执行完成返回True，否则返回False
        """
        return self._complete

    @abstractmethod
    def step(self, state: 'State') -> 'Action':
        """开始执行分配的指令。
        
        这个方法应该由子类实现来定义具体的执行逻辑。
        每次调用step都应该根据当前State产生一个Action，
        这个Action将被执行并可能改变环境状态。
        
        Args:
            state (State): 当前的执行状态，包含历史记录、环境信息等
            
        Returns:
            Action: 根据当前状态决定的下一个Action
        """
        pass

    def reset(self) -> None:
        """重置Agent的执行状态。
        
        仅重置完成状态，不重置LLM指标。
        这允许Agent在保持性能统计的同时重新开始执行新任务。
        """
        self._complete = False

    @property
    def name(self) -> str:
        """获取Agent的名称。
        
        Agent名称默认为类名，用于标识和注册。
        
        Returns:
            str: Agent的类名
        """
        return self.__class__.__name__

    @classmethod
    def register(cls, name: str, agent_cls: type['Agent']) -> None:
        """在注册表中注册Agent类。

        这个类方法用于将Agent类注册到全局注册表中，
        使其可以通过名称进行查找和实例化。

        Args:
            name (str): 注册Agent时使用的名称标识符
            agent_cls (Type['Agent']): 要注册的Agent类

        Raises:
            AgentAlreadyRegisteredError: 如果名称已经被注册则抛出此异常
        """
        if name in cls._registry:
            raise AgentAlreadyRegisteredError(name)
        cls._registry[name] = agent_cls

    @classmethod
    def get_cls(cls, name: str) -> type['Agent']:
        """从注册表中检索Agent类。

        通过名称查找并返回对应的Agent类，用于动态创建Agent实例。

        Args:
            name (str): 要检索的Agent类名称

        Returns:
            type['Agent']: 在指定名称下注册的Agent类

        Raises:
            AgentNotRegisteredError: 如果名称未注册则抛出此异常
        """
        if name not in cls._registry:
            raise AgentNotRegisteredError(name)
        return cls._registry[name]

    @classmethod
    def list_agents(cls) -> list[str]:
        """从注册表中检索所有Agent名称列表。

        返回当前已注册的所有Agent名称，用于显示可用Agent或进行选择。

        Returns:
            list[str]: 所有已注册Agent的名称列表

        Raises:
            AgentNotRegisteredError: 如果没有Agent被注册则抛出此异常
        """
        if not bool(cls._registry):
            raise AgentNotRegisteredError()
        return list(cls._registry.keys())

    def set_mcp_tools(self, mcp_tools: list[dict]) -> None:
        """为Agent设置MCP工具列表。

        MCP (Model Control Protocol) 工具是Agent可以调用的外部功能接口，
        这个方法将工具配置转换为标准格式并添加到Agent的工具集中。

        Args:
            mcp_tools (list[dict]): MCP工具配置的字典列表，
                                   每个字典包含工具的名称、描述和参数定义
        """
        logger.info(
            f'Setting {len(mcp_tools)} MCP tools for agent {self.name}: {[tool["function"]["name"] for tool in mcp_tools]}'
        )
        
        # 遍历所有工具配置
        for tool in mcp_tools:
            # 将字典转换为标准的ChatCompletionToolParam格式
            _tool = ChatCompletionToolParam(**tool)
            
            # 检查工具是否已存在，避免重复添加
            if _tool['function']['name'] in self.mcp_tools:
                logger.warning(
                    f'Tool {_tool["function"]["name"]} already exists, skipping'
                )
                continue
                
            # 添加工具到MCP工具字典和工具列表
            self.mcp_tools[_tool['function']['name']] = _tool
            self.tools.append(_tool)
            
        logger.info(
            f'Tools updated for agent {self.name}, total {len(self.tools)}: {[tool["function"]["name"] for tool in self.tools]}'
        )