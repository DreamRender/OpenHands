import os
from dataclasses import dataclass, field
from itertools import islice

from jinja2 import Template

from openhands.controller.state.state import State
from openhands.core.message import Message, TextContent
from openhands.events.observation.agent import MicroagentKnowledge


@dataclass
class RuntimeInfo:
    """运行时信息数据类。
    
    包含Agent运行时需要的各种环境信息和配置，
    这些信息会被注入到提示词模板中，帮助Agent了解当前的运行环境。
    """
    
    date: str
    """当前日期。
    
    提供给Agent当前的日期信息，帮助Agent在需要时间相关操作时使用正确的日期。
    """
    
    available_hosts: dict[str, int] = field(default_factory=dict)
    """Agent可以访问的网络主机和端口映射。
    
    字典格式为 {主机名: 端口号}，例如一个正在运行的Web应用的地址。
    这允许Agent知道可以连接到哪些服务，如本地开发服务器等。
    """
    
    additional_agent_instructions: str = ''
    """额外的Agent指令。
    
    任何需要告知Agent的额外指令或约束条件。
    这些指令会被添加到Agent的系统提示词中，指导Agent的行为。
    """
    
    custom_secrets_descriptions: dict[str, str] = field(default_factory=dict)
    """用户自定义密钥的描述信息。
    
    字典格式为 {密钥名称: 描述信息}，用于告知Agent可用的密钥及其用途，
    但不包含实际的密钥值以确保安全性。
    """


@dataclass
class RepositoryInfo:
    """GitHub仓库信息数据类。
    
    包含已克隆的GitHub仓库的相关信息，
    这些信息帮助Agent了解当前工作的代码仓库环境。
    """

    repo_name: str | None = None
    """仓库的名称。
    
    GitHub仓库的名称，通常格式为 "用户名/仓库名" 或 "组织名/仓库名"。
    """
    
    repo_directory: str | None = None
    """仓库的本地目录路径。
    
    仓库在运行时环境中被克隆到的本地目录的完整路径。
    Agent可以使用此路径来访问和操作仓库中的文件。
    """


@dataclass
class ConversationInstructions:
    """对话指令数据类。
    
    包含Agent在整个对话过程中必须遵循的可选指令，
    这些指令用于指导Agent如何处理用户的初始任务。

    示例用法：
        1. Resolver指令：你正在响应GitHub issue #1234，完成后请确保开启一个PR
        2. Slack指令：请检查附加的上下文消息是否与任务相关 <context_messages>
    """

    content: str = ''
    """指令内容。
    
    具体的指令文本，会被注入到Agent的提示词中。
    """


class PromptManager:
    """提示词管理器类。

    负责管理提示词模板并整合来自用户Workspace中的MicroAgent和全局MicroAgent的信息。

    这个类专门负责加载和渲染提示词（系统提示词、用户提示词等），
    是OpenHands中提示词处理的核心组件。

    Attributes:
        prompt_dir (str): 包含提示词模板文件的目录路径
        system_template (Template): 系统提示词的Jinja2模板对象
        user_template (Template): 用户提示词的Jinja2模板对象
        additional_info_template (Template): 附加信息的Jinja2模板对象
        microagent_info_template (Template): MicroAgent信息的Jinja2模板对象
    """

    def __init__(
        self,
        prompt_dir: str,
        system_prompt_filename: str = 'system_prompt.j2',
    ):
        """初始化提示词管理器。

        Args:
            prompt_dir (str): 提示词模板文件所在的目录路径
            system_prompt_filename (str, optional): 系统提示词文件名。
                                                   默认为'system_prompt.j2'

        Note:
            初始化过程中会加载所有必需的模板文件，如果模板文件不存在会抛出异常。
        """
        self.prompt_dir: str = prompt_dir
        # 加载各种模板文件
        self.system_template: Template = self._load_system_template(
            system_prompt_filename
        )
        self.user_template: Template = self._load_template('user_prompt')
        self.additional_info_template: Template = self._load_template('additional_info')
        self.microagent_info_template: Template = self._load_template('microagent_info')

    def _load_system_template(self, system_prompt_filename: str) -> Template:
        """加载系统提示词模板。

        使用指定的文件名加载系统提示词模板，提供更具体的错误信息。

        Args:
            system_prompt_filename (str): 系统提示词文件名

        Returns:
            Template: 加载的Jinja2模板对象

        Raises:
            FileNotFoundError: 当系统提示词文件不存在时抛出，包含详细的错误信息
        """
        # 移除.j2扩展名（如果存在）以便使用_load_template方法
        template_name = system_prompt_filename
        if template_name.endswith('.j2'):
            template_name = template_name[:-3]

        try:
            return self._load_template(template_name)
        except FileNotFoundError:
            # 为系统提示词文件提供更具体的错误信息
            template_path = os.path.join(self.prompt_dir, f'{template_name}.j2')
            raise FileNotFoundError(
                f'System prompt file "{system_prompt_filename}" not found at {template_path}. '
                f'Please ensure the file exists in the prompt directory: {self.prompt_dir}'
            )

    def _load_template(self, template_name: str) -> Template:
        """加载指定名称的模板文件。

        从提示词目录中加载指定的Jinja2模板文件。

        Args:
            template_name (str): 模板文件名（不包含.j2扩展名）

        Returns:
            Template: 加载的Jinja2模板对象

        Raises:
            ValueError: 当提示词目录未设置时抛出
            FileNotFoundError: 当模板文件不存在时抛出
        """
        if self.prompt_dir is None:
            raise ValueError('Prompt directory is not set')
        
        # 构建完整的模板文件路径
        template_path = os.path.join(self.prompt_dir, f'{template_name}.j2')
        if not os.path.exists(template_path):
            raise FileNotFoundError(f'Prompt file {template_path} not found')
            
        # 读取并创建模板对象
        with open(template_path, 'r') as file:
            return Template(file.read())

    def get_system_message(self) -> str:
        """获取渲染后的系统消息。

        渲染系统提示词模板并返回最终的系统消息文本。

        Returns:
            str: 渲染后的系统消息，已去除首尾空白字符
        """
        return self.system_template.render().strip()

    def get_example_user_message(self) -> str:
        """获取示例用户消息。

        这是一个可以在*实际*用户指令提供之前提供给Agent的初始用户消息。

        它可以用来演示Agent应该如何行为以解决用户的任务。
        它也可以选择性地包含一些关于用户任务的额外上下文。
        这些额外的上下文将把当前的通用Agent转换为更适合用户任务的专门化Agent。

        Returns:
            str: 渲染后的示例用户消息，已去除首尾空白字符
        """
        return self.user_template.render().strip()

    def build_workspace_context(
        self,
        repository_info: RepositoryInfo | None,
        runtime_info: RuntimeInfo | None,
        conversation_instructions: ConversationInstructions | None,
        repo_instructions: str = '',
    ) -> str:
        """构建Workspace上下文信息。

        使用存储的仓库/运行时信息渲染附加信息模板。

        Args:
            repository_info (RepositoryInfo | None): 仓库信息对象
            runtime_info (RuntimeInfo | None): 运行时信息对象
            conversation_instructions (ConversationInstructions | None): 对话指令对象
            repo_instructions (str, optional): 仓库相关的指令。默认为空字符串

        Returns:
            str: 渲染后的Workspace上下文信息，已去除首尾空白字符

        Note:
            这个方法整合了所有与当前工作环境相关的信息，
            为Agent提供完整的上下文信息。
        """
        return self.additional_info_template.render(
            repository_info=repository_info,
            repository_instructions=repo_instructions,
            runtime_info=runtime_info,
            conversation_instructions=conversation_instructions,
        ).strip()

    def build_microagent_info(
        self,
        triggered_agents: list[MicroagentKnowledge],
    ) -> str:
        """构建MicroAgent信息。

        使用触发的MicroAgent列表渲染MicroAgent信息模板。

        Args:
            triggered_agents (list[MicroagentKnowledge]): 包含已触发的MicroAgent信息的列表，
                                                        每个对象包含MicroAgent的知识和能力信息

        Returns:
            str: 渲染后的MicroAgent信息文本，已去除首尾空白字符

        Note:
            MicroAgent是OpenHands中的小型专门化Agent，
            它们可以为主Agent提供特定领域的知识和能力。
        """
        return self.microagent_info_template.render(
            triggered_agents=triggered_agents
        ).strip()

    def add_turns_left_reminder(self, messages: list[Message], state: State) -> None:
        """添加剩余轮次提醒。

        在最新的用户消息中添加剩余轮次的提醒文本，
        告知Agent还有多少轮次来完成任务。

        Args:
            messages (list[Message]): 消息列表，会被就地修改
            state (State): 当前的Agent状态，包含迭代计数信息

        Note:
            - 此方法会修改传入的messages列表
            - 只会在最近的包含文本内容的用户消息中添加提醒
            - 提醒文本包含剩余轮次数和完成任务的指示
        """
        # 从消息列表末尾开始查找最新的用户消息
        latest_user_message = next(
            islice(
                (
                    m
                    for m in reversed(messages)  # 从后往前遍历
                    if m.role == 'user'  # 只查找用户消息
                    and any(isinstance(c, TextContent) for c in m.content)  # 包含文本内容
                ),
                1,  # 只取第一个（最新的）匹配项
            ),
            None,  # 如果没有找到则返回None
        )
        
        if latest_user_message:
            # 计算剩余轮次并构建提醒文本
            remaining_turns = state.iteration_flag.max_value - state.iteration_flag.current_value
            reminder_text = f'\n\nENVIRONMENT REMINDER: You have {remaining_turns} turns left to complete the task. When finished reply with <finish></finish>.'
            
            # 将提醒文本添加到用户消息的内容中
            latest_user_message.content.append(TextContent(text=reminder_text))
