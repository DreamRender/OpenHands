# CLI TUI输入输出函数
# 处理所有控制台的输入和输出
# CLI设置在cli_settings.py中单独处理

import asyncio
import contextlib
import sys
import threading
import time
from typing import Generator

from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.application import Application
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML, FormattedText, StyleAndTextTuples
from prompt_toolkit.input import create_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import print_container
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, TextArea

from openhands import __version__
from openhands.core.config import OpenHandsConfig
from openhands.core.schema import AgentState
from openhands.events import EventSource, EventStream
from openhands.events.action import (
    Action,
    ActionConfirmationStatus,
    ChangeAgentStateAction,
    CmdRunAction,
    MessageAction,
)
from openhands.events.event import Event
from openhands.events.observation import (
    AgentStateChangedObservation,
    CmdOutputObservation,
    ErrorObservation,
    FileEditObservation,
    FileReadObservation,
)
from openhands.llm.metrics import Metrics

ENABLE_STREAMING = False  # FIXME: 这个功能还不能正常工作

# 全局变量：用于流式输出的TextArea
streaming_output_text_area: TextArea | None = None

# 跟踪最近的思考内容以防止重复显示
recent_thoughts: list[str] = []
MAX_RECENT_THOUGHTS = 5  # 最多保存的最近思考数量

# 颜色和样式常量
COLOR_GOLD = '#FFD700'  # 金色
COLOR_GREY = '#808080'  # 灰色
DEFAULT_STYLE = Style.from_dict(
    {
        'gold': COLOR_GOLD,
        'grey': COLOR_GREY,
        'prompt': f'{COLOR_GOLD} bold',
    }
)

# 可用命令及其描述
COMMANDS = {
    '/exit': 'Exit the application',
    '/help': 'Display available commands',
    '/init': 'Initialize a new repository',
    '/status': 'Display conversation details and usage metrics',
    '/new': 'Create a new conversation',
    '/settings': 'Display and modify current settings',
    '/resume': 'Resume the agent when paused',
}

print_lock = threading.Lock()  # 打印锁，确保多线程打印的安全性

pause_task: asyncio.Task | None = None  # 最多只有一个暂停任务


class UsageMetrics:
    """
    使用指标统计类。
    
    跟踪会话的使用统计信息，包括成本、token使用量和会话持续时间。
    
    Attributes:
        metrics: LLM使用指标对象，包含成本和token统计
        session_init_time: 会话初始化时间戳
    """
    def __init__(self) -> None:
        self.metrics: Metrics = Metrics()
        self.session_init_time: float = time.time()


class CustomDiffLexer(Lexer):
    """
    特定diff格式的自定义词法分析器。
    
    为文件差异显示提供语法高亮，区分添加、删除和元数据行。
    """

    def lex_document(self, document: Document) -> StyleAndTextTuples:
        """
        对文档进行词法分析。
        
        Args:
            document: 要分析的文档对象
            
        Returns:
            StyleAndTextTuples: 样式和文本元组列表
        """
        lines = document.lines

        def get_line(lineno: int) -> StyleAndTextTuples:
            """
            获取指定行号的样式化文本。
            
            Args:
                lineno: 行号
                
            Returns:
                StyleAndTextTuples: 该行的样式和文本元组
            """
            line = lines[lineno]
            if line.startswith('+'):
                # 添加的行用绿色显示
                return [('ansigreen', line)]
            elif line.startswith('-'):
                # 删除的行用红色显示
                return [('ansired', line)]
            elif line.startswith('[') or line.startswith('('):
                # 元数据行如[Existing file...]或(content...)用粗体显示
                return [('bold', line)]
            else:
                # 其他行使用默认样式
                return [('', line)]

        return get_line


# CLI初始化和启动显示函数

def display_runtime_initialization_message(runtime: str) -> None:
    """
    显示Runtime初始化消息。
    
    根据不同的Runtime类型显示相应的初始化提示信息。
    
    Args:
        runtime: Runtime类型（'local'、'docker'等）
    """
    print_formatted_text('')
    if runtime == 'local':
        print_formatted_text(HTML('<grey>⚙️ Starting local runtime...</grey>'))
    elif runtime == 'docker':
        print_formatted_text(HTML('<grey>🐳 Starting Docker runtime...</grey>'))
    print_formatted_text('')


def display_initialization_animation(text: str, is_loaded: asyncio.Event) -> None:
    """
    显示初始化动画。
    
    在控制台显示旋转的加载动画，直到加载完成事件被设置。
    
    Args:
        text: 显示的文本内容
        is_loaded: 加载完成事件，设置后停止动画
    """
    # 动画帧：旋转的字符序列
    ANIMATION_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

    i = 0
    while not is_loaded.is_set():
        # 使用ANSI转义序列显示动画
        sys.stdout.write('\n')
        sys.stdout.write(
            f'\033[s\033[J\033[38;2;255;215;0m[{ANIMATION_FRAMES[i % len(ANIMATION_FRAMES)]}] {text}\033[0m\033[u\033[1A'
        )
        sys.stdout.flush()
        time.sleep(0.1)  # 动画间隔
        i += 1

    # 清除动画显示
    sys.stdout.write('\r' + ' ' * (len(text) + 10) + '\r')
    sys.stdout.flush()


def display_banner(session_id: str) -> None:
    """
    显示OpenHands的ASCII艺术banner。
    
    显示应用名称、版本信息和session ID。
    
    Args:
        session_id: 当前会话的ID
    """
    # ASCII艺术logo
    print_formatted_text(
        HTML(r"""<gold>
     ___                    _   _                 _
    /  _ \ _ __   ___ _ __ | | | | __ _ _ __   __| |___
    | | | | '_ \ / _ \ '_ \| |_| |/ _` | '_ \ / _` / __|
    | |_| | |_) |  __/ | | |  _  | (_| | | | | (_| \__ \
    \___ /| .__/ \___|_| |_|_| |_|\__,_|_| |_|\__,_|___/
          |_|
    </gold>"""),
        style=DEFAULT_STYLE,
    )

    # 版本信息
    print_formatted_text(HTML(f'<grey>OpenHands CLI v{__version__}</grey>'))

    print_formatted_text('')
    # 会话ID信息
    print_formatted_text(HTML(f'<grey>Initialized conversation {session_id}</grey>'))
    print_formatted_text('')


def display_welcome_message(message: str = '') -> None:
    """
    显示欢迎消息。
    
    Args:
        message: 可选的自定义欢迎消息
    """
    print_formatted_text(
        HTML("<gold>Let's start building!</gold>\n"), style=DEFAULT_STYLE
    )
    if message:
        print_formatted_text(
            HTML(f'{message} <grey>Type /help for help</grey>'),
            style=DEFAULT_STYLE,
        )
    else:
        print_formatted_text(
            HTML('What do you want to build? <grey>Type /help for help</grey>'),
            style=DEFAULT_STYLE,
        )


def display_initial_user_prompt(prompt: str) -> None:
    """
    显示初始用户提示。
    
    以特定格式显示用户的初始输入提示。
    
    Args:
        prompt: 要显示的提示内容
    """
    print_formatted_text(
        FormattedText(
            [
                ('', '\n'),
                (COLOR_GOLD, '> '),  # 金色提示符
                ('', prompt),
            ]
        )
    )


# 提示输出显示函数

def display_thought_if_new(thought: str) -> None:
    """
    仅在思考内容为新内容时显示。
    
    避免显示重复的思考内容，维护一个最近思考的列表。
    
    Args:
        thought: 要显示的思考内容
    """
    global recent_thoughts
    if thought and thought.strip():
        # 检查这个思考是否最近已经显示过
        if thought not in recent_thoughts:
            display_message(thought)
            recent_thoughts.append(thought)
            # 只保留最近的思考
            if len(recent_thoughts) > MAX_RECENT_THOUGHTS:
                recent_thoughts.pop(0)


def display_event(event: Event, config: OpenHandsConfig) -> None:
    """
    显示事件信息。
    
    根据事件类型显示相应的内容，包括Action、Observation等。
    
    Args:
        event: 要显示的事件对象
        config: OpenHands配置对象
    """
    global streaming_output_text_area
    with print_lock:  # 使用线程锁确保打印安全
        if isinstance(event, CmdRunAction):
            # 对于CmdRunAction，先显示思考，然后显示命令
            if hasattr(event, 'thought') and event.thought:
                display_message(event.thought)

            # 只有在命令还没有确认时才显示命令
            # 命令在AWAITING_CONFIRMATION时总是显示，所以CONFIRMED时不需要再次显示
            if event.confirmation_state != ActionConfirmationStatus.CONFIRMED:
                display_command(event)

            if event.confirmation_state == ActionConfirmationStatus.CONFIRMED:
                initialize_streaming_output()
        elif isinstance(event, Action):
            # 对于其他Action，正常显示思考
            if hasattr(event, 'thought') and event.thought:
                display_message(event.thought)
            if hasattr(event, 'final_thought') and event.final_thought:
                display_message(event.final_thought)

        if isinstance(event, MessageAction):
            if event.source == EventSource.AGENT:
                # 检查消息内容是否为重复的思考
                display_thought_if_new(event.content)
        elif isinstance(event, CmdOutputObservation):
            display_command_output(event.content)
        elif isinstance(event, FileEditObservation):
            display_file_edit(event)
        elif isinstance(event, FileReadObservation):
            display_file_read(event)
        elif isinstance(event, AgentStateChangedObservation):
            display_agent_state_change_message(event.agent_state)
        elif isinstance(event, ErrorObservation):
            display_error(event.content)


def display_message(message: str) -> None:
    """
    显示普通消息。
    
    Args:
        message: 要显示的消息内容
    """
    message = message.strip()

    if message:
        print_formatted_text(f'\n{message}')


def display_error(error: str) -> None:
    """
    显示错误信息。
    
    在红色框架中显示错误内容。
    
    Args:
        error: 错误信息内容
    """
    error = error.strip()

    if error:
        container = Frame(
            TextArea(
                text=error,
                read_only=True,
                style='ansired',  # 红色样式
                wrap_lines=True,
            ),
            title='Error',
            style='ansired',
        )
        print_formatted_text('')
        print_container(container)


def display_command(event: CmdRunAction) -> None:
    """
    显示命令信息。
    
    在蓝色框架中显示要执行的命令。
    
    Args:
        event: 命令运行Action事件
    """
    container = Frame(
        TextArea(
            text=f'$ {event.command}',
            read_only=True,
            style=COLOR_GREY,
            wrap_lines=True,
        ),
        title='Command',
        style='ansiblue',  # 蓝色样式
    )
    print_formatted_text('')
    print_container(container)


def display_command_output(output: str) -> None:
    """
    显示命令输出。
    
    过滤掉特定的系统提示符行，在灰色框架中显示命令执行结果。
    
    Args:
        output: 命令输出内容
    """
    lines = output.split('\n')
    formatted_lines = []
    for line in lines:
        # 过滤掉特定的系统行
        if line.startswith('[Python Interpreter') or line.startswith('openhands@'):
            # TODO: 一旦清理了终端输出，就清理这个部分
            continue
        formatted_lines.append(line)
        formatted_lines.append('\n')

    # 如果存在，移除最后的换行符
    if formatted_lines:
        formatted_lines.pop()

    container = Frame(
        TextArea(
            text=''.join(formatted_lines),
            read_only=True,
            style=COLOR_GREY,
            wrap_lines=True,
        ),
        title='Command Output',
        style=f'fg:{COLOR_GREY}',
    )
    print_formatted_text('')
    print_container(container)


def display_file_edit(event: FileEditObservation) -> None:
    """
    显示文件编辑信息。
    
    使用diff格式显示文件的修改内容，支持语法高亮。
    
    Args:
        event: 文件编辑Observation事件
    """
    container = Frame(
        TextArea(
            text=event.visualize_diff(n_context_lines=4),  # 显示4行上下文
            read_only=True,
            wrap_lines=True,
            lexer=CustomDiffLexer(),  # 使用自定义词法分析器
        ),
        title='File Edit',
        style=f'fg:{COLOR_GREY}',
    )
    print_formatted_text('')
    print_container(container)


def display_file_read(event: FileReadObservation) -> None:
    """
    显示文件读取信息。
    
    在灰色框架中显示文件内容，将制表符转换为空格。
    
    Args:
        event: 文件读取Observation事件
    """
    content = event.content.replace('\t', ' ')  # 将制表符替换为空格
    container = Frame(
        TextArea(
            text=content,
            read_only=True,
            style=COLOR_GREY,
            wrap_lines=True,
        ),
        title='File Read',
        style=f'fg:{COLOR_GREY}',
    )
    print_formatted_text('')
    print_container(container)


def initialize_streaming_output():
    """
    初始化流式输出的TextArea。
    
    创建用于显示实时输出的文本区域。
    """
    if not ENABLE_STREAMING:
        return
    global streaming_output_text_area
    streaming_output_text_area = TextArea(
        text='',
        read_only=True,
        style=COLOR_GREY,
        wrap_lines=True,
    )
    container = Frame(
        streaming_output_text_area,
        title='Streaming Output',
        style=f'fg:{COLOR_GREY}',
    )
    print_formatted_text('')
    print_container(container)


def update_streaming_output(text: str):
    """
    更新流式输出TextArea的内容。
    
    将新文本追加到现有内容。
    
    Args:
        text: 要追加的新文本
    """
    global streaming_output_text_area

    # 将新文本追加到现有内容
    if streaming_output_text_area is not None:
        current_text = streaming_output_text_area.text
        streaming_output_text_area.text = current_text + text


# 交互式命令输出显示函数

def display_help() -> None:
    """
    显示帮助信息。
    
    展示版本信息、使用示例、技巧和可用命令列表。
    """
    # 版本头部和介绍
    print_formatted_text(
        HTML(
            f'\n<grey>OpenHands CLI v{__version__}</grey>\n'
            '<gold>OpenHands CLI lets you interact with the OpenHands agent from the command line.</gold>\n'
        )
    )

    # 使用示例
    print_formatted_text('Things that you can try:')
    print_formatted_text(
        HTML(
            '• Ask questions about the codebase <grey>> How does main.py work?</grey>\n'
            '• Edit files or add new features <grey>> Add a new function to ...</grey>\n'
            '• Find and fix issues <grey>> Fix the type error in ...</grey>\n'
        )
    )

    # 技巧部分
    print_formatted_text(
        'Some tips to get the most out of OpenHands:\n'
        '• Be as specific as possible about the desired outcome or the problem to be solved.\n'
        '• Provide context, including relevant file paths and line numbers if available.\n'
        '• Break large tasks into smaller, manageable prompts.\n'
        '• Include relevant error messages or logs.\n'
        '• Specify the programming language or framework, if not obvious.\n'
    )

    # 命令部分
    print_formatted_text(HTML('Interactive commands:'))
    commands_html = ''
    for command, description in COMMANDS.items():
        commands_html += f'<gold><b>{command}</b></gold> - <grey>{description}</grey>\n'
    print_formatted_text(HTML(commands_html))

    # 页脚
    print_formatted_text(
        HTML(
            '<grey>Learn more at: https://docs.all-hands.dev/usage/getting-started</grey>'
        )
    )


def display_usage_metrics(usage_metrics: UsageMetrics) -> None:
    """
    显示使用指标统计。
    
    在表格中展示成本、token使用量等统计信息。
    
    Args:
        usage_metrics: 使用指标统计对象
    """
    # 格式化各项统计数据
    cost_str = f'${usage_metrics.metrics.accumulated_cost:.6f}'
    input_tokens_str = (
        f'{usage_metrics.metrics.accumulated_token_usage.prompt_tokens:,}'
    )
    cache_read_str = (
        f'{usage_metrics.metrics.accumulated_token_usage.cache_read_tokens:,}'
    )
    cache_write_str = (
        f'{usage_metrics.metrics.accumulated_token_usage.cache_write_tokens:,}'
    )
    output_tokens_str = (
        f'{usage_metrics.metrics.accumulated_token_usage.completion_tokens:,}'
    )
    total_tokens_str = f'{usage_metrics.metrics.accumulated_token_usage.prompt_tokens + usage_metrics.metrics.accumulated_token_usage.completion_tokens:,}'

    # 构建标签和值的列表
    labels_and_values = [
        ('   Total Cost (USD):', cost_str),
        ('', ''),  # 空行分隔
        ('   Total Input Tokens:', input_tokens_str),
        ('      Cache Hits:', cache_read_str),
        ('      Cache Writes:', cache_write_str),
        ('   Total Output Tokens:', output_tokens_str),
        ('', ''),  # 空行分隔
        ('   Total Tokens:', total_tokens_str),
    ]

    # 计算对齐的最大宽度
    max_label_width = max(len(label) for label, _ in labels_and_values)
    max_value_width = max(len(value) for _, value in labels_and_values)

    # 构建带对齐列的摘要文本
    summary_lines = [
        f'{label:<{max_label_width}} {value:<{max_value_width}}'
        for label, value in labels_and_values
    ]
    summary_text = '\n'.join(summary_lines)

    # 显示使用指标容器
    container = Frame(
        TextArea(
            text=summary_text,
            read_only=True,
            style=COLOR_GREY,
            wrap_lines=True,
        ),
        title='Usage Metrics',
        style=f'fg:{COLOR_GREY}',
    )

    print_container(container)


def get_session_duration(session_init_time: float) -> str:
    """
    获取会话持续时间的格式化字符串。
    
    Args:
        session_init_time: 会话初始化时间戳
        
    Returns:
        str: 格式化的持续时间字符串（如"1h 23m 45s"）
    """
    current_time = time.time()
    session_duration = current_time - session_init_time
    hours, remainder = divmod(session_duration, 3600)
    minutes, seconds = divmod(remainder, 60)

    return f'{int(hours)}h {int(minutes)}m {int(seconds)}s'


def display_shutdown_message(usage_metrics: UsageMetrics, session_id: str) -> None:
    """
    显示会话关闭消息。
    
    展示会话结束信息，包括使用统计和会话持续时间。
    
    Args:
        usage_metrics: 使用指标统计对象
        session_id: 会话ID
    """
    duration_str = get_session_duration(usage_metrics.session_init_time)

    print_formatted_text(HTML('<grey>Closing current conversation...</grey>'))
    print_formatted_text('')
    display_usage_metrics(usage_metrics)
    print_formatted_text('')
    print_formatted_text(HTML(f'<grey>Conversation duration: {duration_str}</grey>'))
    print_formatted_text('')
    print_formatted_text(HTML(f'<grey>Closed conversation {session_id}</grey>'))
    print_formatted_text('')


def display_status(usage_metrics: UsageMetrics, session_id: str) -> None:
    """
    显示会话状态信息。
    
    展示当前会话ID、运行时间和使用统计。
    
    Args:
        usage_metrics: 使用指标统计对象
        session_id: 会话ID
    """
    duration_str = get_session_duration(usage_metrics.session_init_time)

    print_formatted_text('')
    print_formatted_text(HTML(f'<grey>Conversation ID: {session_id}</grey>'))
    print_formatted_text(HTML(f'<grey>Uptime:          {duration_str}</grey>'))
    print_formatted_text('')
    display_usage_metrics(usage_metrics)


def display_agent_running_message() -> None:
    """
    显示Agent正在运行的消息。
    
    提示用户Agent正在运行，并说明暂停操作。
    """
    print_formatted_text('')
    print_formatted_text(
        HTML('<gold>Agent running...</gold> <grey>(Press Ctrl-P to pause)</grey>')
    )


def display_agent_state_change_message(agent_state: str) -> None:
    """
    显示Agent状态变化消息。
    
    根据不同的Agent状态显示相应的提示信息。
    
    Args:
        agent_state: Agent状态字符串
    """
    if agent_state == AgentState.PAUSED:
        print_formatted_text('')
        print_formatted_text(
            HTML(
                '<gold>Agent paused...</gold> <grey>(Enter /resume to continue)</grey>'
            )
        )
    elif agent_state == AgentState.FINISHED:
        print_formatted_text('')
        print_formatted_text(HTML('<gold>Task completed...</gold>'))
    elif agent_state == AgentState.AWAITING_USER_INPUT:
        print_formatted_text('')
        print_formatted_text(HTML('<gold>Agent is waiting for your input...</gold>'))


# 通用输入函数

class CommandCompleter(Completer):
    """
    命令自动补全器。
    
    为CLI命令提供智能补全功能，根据Agent状态过滤可用命令。
    
    Attributes:
        agent_state: 当前Agent状态
    """

    def __init__(self, agent_state: str) -> None:
        """
        初始化命令补全器。
        
        Args:
            agent_state: 当前Agent状态
        """
        super().__init__()
        self.agent_state = agent_state

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Generator[Completion, None, None]:
        """
        获取补全建议。
        
        根据用户输入和Agent状态提供相应的命令补全。
        
        Args:
            document: 当前文档对象
            complete_event: 补全事件
            
        Yields:
            Completion: 补全建议对象
        """
        text = document.text_before_cursor.lstrip()
        if text.startswith('/'):
            # 获取可用命令列表
            available_commands = dict(COMMANDS)
            # 如果Agent不在暂停状态，移除/resume命令
            if self.agent_state != AgentState.PAUSED:
                available_commands.pop('/resume', None)

            # 生成匹配的补全建议
            for command, description in available_commands.items():
                if command.startswith(text):
                    yield Completion(
                        command,
                        start_position=-len(text),
                        display_meta=description,
                        style='bg:ansidarkgray fg:gold',  # 金色前景，深灰背景
                    )


def create_prompt_session(config: OpenHandsConfig) -> PromptSession[str]:
    """
    创建提示会话，如果配置中指定则启用VI模式。
    
    Args:
        config: OpenHands配置对象
        
    Returns:
        PromptSession[str]: 配置好的提示会话对象
    """
    return PromptSession(style=DEFAULT_STYLE, vi_mode=config.cli.vi_mode)


async def read_prompt_input(
    config: OpenHandsConfig, agent_state: str, multiline: bool = False
) -> str:
    """
    读取用户输入的提示内容。
    
    支持单行和多行输入模式，提供命令补全功能。
    
    Args:
        config: OpenHands配置对象
        agent_state: 当前Agent状态
        multiline: 是否启用多行输入模式
        
    Returns:
        str: 用户输入的内容，如果用户中断则返回'/exit'
    """
    try:
        prompt_session = create_prompt_session(config)
        # 在非多行模式下提供命令补全
        prompt_session.completer = (
            CommandCompleter(agent_state) if not multiline else None
        )

        if multiline:
            # 多行输入模式
            kb = KeyBindings()

            @kb.add('c-d')  # Ctrl+D快捷键
            def _(event: KeyPressEvent) -> None:
                event.current_buffer.validate_and_handle()

            with patch_stdout():
                print_formatted_text('')
                message = await prompt_session.prompt_async(
                    HTML(
                        '<gold>Enter your message and press Ctrl-D to finish:</gold>\n'
                    ),
                    multiline=True,
                    key_bindings=kb,
                )
        else:
            # 单行输入模式
            with patch_stdout():
                print_formatted_text('')
                message = await prompt_session.prompt_async(
                    HTML('<gold>> </gold>'),
                )
        return message if message is not None else ''
    except (KeyboardInterrupt, EOFError):
        # 用户中断输入，返回退出命令
        return '/exit'


async def read_confirmation_input(config: OpenHandsConfig) -> str:
    """
    读取用户确认输入。
    
    提示用户选择是否确认执行操作，支持一次性确认和总是确认模式。
    
    Args:
        config: OpenHands配置对象
        
    Returns:
        str: 用户选择（'yes'、'no'、'always'或'no'如果中断）
    """
    try:
        prompt_session = create_prompt_session(config)

        while True:
            with patch_stdout():
                print_formatted_text('')
                confirmation: str = await prompt_session.prompt_async(
                    HTML('<gold>Proceed with action? (y)es/(n)o/(a)lways > </gold>'),
                )

                confirmation = (
                    '' if confirmation is None else confirmation.strip().lower()
                )

                if confirmation in ['y', 'yes']:
                    return 'yes'
                elif confirmation in ['n', 'no']:
                    return 'no'
                elif confirmation in ['a', 'always']:
                    return 'always'
                else:
                    # 无效输入，显示错误消息
                    print_formatted_text('')
                    print_formatted_text(
                        HTML(
                            '<ansired>Invalid input. Please enter (y)es, (n)o, or (a)lways.</ansired>'
                        )
                    )
                    # 继续循环重新提示
    except (KeyboardInterrupt, EOFError):
        return 'no'


def start_pause_listener(
    loop: asyncio.AbstractEventLoop,
    done_event: asyncio.Event,
    event_stream,
) -> None:
    """
    启动暂停监听器。
    
    创建一个异步任务来监听用户的暂停请求。
    
    Args:
        loop: 异步事件循环
        done_event: 完成事件
        event_stream: 事件流对象
    """
    global pause_task
    if pause_task is None or pause_task.done():
        pause_task = loop.create_task(
            process_agent_pause(done_event, event_stream)
        )  # 创建任务跟踪用户的Agent暂停请求


async def stop_pause_listener() -> None:
    """
    停止暂停监听器。
    
    取消当前的暂停监听任务。
    """
    global pause_task
    if pause_task and not pause_task.done():
        pause_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pause_task
        await asyncio.sleep(0)
    pause_task = None


async def process_agent_pause(done: asyncio.Event, event_stream: EventStream) -> None:
    """
    处理Agent暂停请求。
    
    监听特定的按键组合（Ctrl+P、Ctrl+C、Ctrl+D）来暂停Agent。
    
    Args:
        done: 完成事件，设置后停止监听
        event_stream: 事件流对象
    """
    input = create_input()

    def keys_ready() -> None:
        """
        按键就绪回调函数。
        
        检查按键并在检测到暂停键时触发暂停操作。
        """
        for key_press in input.read_keys():
            if (
                key_press.key == Keys.ControlP
                or key_press.key == Keys.ControlC
                or key_press.key == Keys.ControlD
            ):
                print_formatted_text('')
                print_formatted_text(HTML('<gold>Pausing the agent...</gold>'))
                # 向事件流添加暂停Agent的事件
                event_stream.add_event(
                    ChangeAgentStateAction(AgentState.PAUSED),
                    EventSource.USER,
                )
                done.set()  # 设置完成事件

    try:
        # 在原始模式下监听按键
        with input.raw_mode():
            with input.attach(keys_ready):
                await done.wait()
    finally:
        input.close()


def cli_confirm(
    config: OpenHandsConfig,
    question: str = 'Are you sure?',
    choices: list[str] | None = None,
) -> int:
    """
    显示带有给定问题和选项的确认提示。
    
    提供一个交互式选择界面，支持键盘导航。
    
    Args:
        config: OpenHands配置对象
        question: 要显示的问题文本
        choices: 可选择的选项列表，默认为['Yes', 'No']
        
    Returns:
        int: 选择的选项索引
    """
    if choices is None:
        choices = ['Yes', 'No']
    selected = [0]  # 使用列表以允许在闭包中修改

    def get_choice_text() -> list:
        """
        获取选择文本的格式化列表。
        
        Returns:
            list: 格式化的文本和样式列表
        """
        return [
            ('class:question', f'{question}\n\n'),
        ] + [
            (
                'class:selected' if i == selected[0] else 'class:unselected',
                f'{"> " if i == selected[0] else "  "}{choice}\n',
            )
            for i, choice in enumerate(choices)
        ]

    # 设置键绑定
    kb = KeyBindings()

    @kb.add('up')  # 上箭头键
    def _handle_up(event: KeyPressEvent) -> None:
        selected[0] = (selected[0] - 1) % len(choices)

    if config.cli.vi_mode:
        @kb.add('k')  # VI模式下的k键
        def _handle_k(event: KeyPressEvent) -> None:
            selected[0] = (selected[0] - 1) % len(choices)

    @kb.add('down')  # 下箭头键
    def _handle_down(event: KeyPressEvent) -> None:
        selected[0] = (selected[0] + 1) % len(choices)

    if config.cli.vi_mode:
        @kb.add('j')  # VI模式下的j键
        def _handle_j(event: KeyPressEvent) -> None:
            selected[0] = (selected[0] + 1) % len(choices)

    @kb.add('enter')  # 回车键确认选择
    def _handle_enter(event: KeyPressEvent) -> None:
        event.app.exit(result=selected[0])

    # 定义样式
    style = Style.from_dict({'selected': COLOR_GOLD, 'unselected': ''})

    # 创建布局
    layout = Layout(
        HSplit(
            [
                Window(
                    FormattedTextControl(get_choice_text),
                    always_hide_cursor=True,  # 总是隐藏光标
                )
            ]
        )
    )

    # 创建并运行应用
    app = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        mouse_support=True,
        full_screen=False,
    )

    return app.run(in_thread=True)


def kb_cancel() -> KeyBindings:
    """
    创建处理ESC键作为用户取消操作的自定义键绑定。
    
    Returns:
        KeyBindings: 配置好的键绑定对象
    """
    bindings = KeyBindings()

    @bindings.add('escape')  # ESC键
    def _(event: KeyPressEvent) -> None:
        event.app.exit(exception=UserCancelledError, style='class:aborting')

    return bindings


class UserCancelledError(Exception):
    """
    用户通过键绑定取消操作时引发的异常。
    
    用于表示用户主动取消了当前操作。
    """

    pass