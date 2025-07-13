import asyncio
from pathlib import Path

from prompt_toolkit import print_formatted_text
from prompt_toolkit.shortcuts import clear, print_container
from prompt_toolkit.widgets import Frame, TextArea

from openhands.cli.settings import (
    display_settings,
    modify_llm_settings_advanced,
    modify_llm_settings_basic,
)
from openhands.cli.tui import (
    COLOR_GREY,
    UsageMetrics,
    cli_confirm,
    display_help,
    display_shutdown_message,
    display_status,
)
from openhands.cli.utils import (
    add_local_config_trusted_dir,
    get_local_config_trusted_dirs,
    read_file,
    write_to_file,
)
from openhands.core.config import (
    OpenHandsConfig,
)
from openhands.core.schema import AgentState
from openhands.core.schema.exit_reason import ExitReason
from openhands.events import EventSource
from openhands.events.action import (
    ChangeAgentStateAction,
    MessageAction,
)
from openhands.events.stream import EventStream
from openhands.storage.settings.file_settings_store import FileSettingsStore


async def handle_commands(
    command: str,
    event_stream: EventStream,
    usage_metrics: UsageMetrics,
    sid: str,
    config: OpenHandsConfig,
    current_dir: str,
    settings_store: FileSettingsStore,
) -> tuple[bool, bool, bool, ExitReason]:
    """
    处理CLI命令的主要函数。
    
    解析用户输入的命令并执行相应的操作，包括退出、帮助、初始化等。
    
    Args:
        command: 用户输入的命令字符串
        event_stream: 事件流，用于处理Agent和环境之间的通信
        usage_metrics: 使用指标统计对象
        sid: session ID，会话标识符
        config: OpenHands配置对象
        current_dir: 当前工作目录路径
        settings_store: 设置存储对象，用于持久化配置
        
    Returns:
        tuple[bool, bool, bool, ExitReason]: 包含以下四个布尔值的元组：
            - close_repl: 是否关闭REPL（读取-求值-打印循环）
            - reload_microagents: 是否重新加载MicroAgent
            - new_session_requested: 是否请求新Session
            - exit_reason: 退出原因枚举
    """
    # 初始化返回值变量
    close_repl = False  # 是否关闭REPL的标志
    reload_microagents = False  # 是否重新加载microagents的标志
    new_session_requested = False  # 是否请求新session的标志
    exit_reason = ExitReason.ERROR  # 默认退出原因为错误

    # 根据命令类型执行相应的处理逻辑
    if command == '/exit':
        # 处理退出命令
        close_repl = handle_exit_command(
            config,
            event_stream,
            usage_metrics,
            sid,
        )
        if close_repl:
            exit_reason = ExitReason.INTENTIONAL  # 设置为主动退出
    elif command == '/help':
        # 处理帮助命令
        handle_help_command()
    elif command == '/init':
        # 处理初始化命令
        close_repl, reload_microagents = await handle_init_command(
            config, event_stream, current_dir
        )
    elif command == '/status':
        # 处理状态查询命令
        handle_status_command(usage_metrics, sid)
    elif command == '/new':
        # 处理新建会话命令
        close_repl, new_session_requested = handle_new_command(
            config, event_stream, usage_metrics, sid
        )
        if close_repl:
            exit_reason = ExitReason.INTENTIONAL  # 设置为主动退出
    elif command == '/settings':
        # 处理设置命令
        await handle_settings_command(config, settings_store)
    elif command == '/resume':
        # 处理恢复命令
        close_repl, new_session_requested = await handle_resume_command(event_stream)
    else:
        # 处理普通消息（非命令）
        close_repl = True
        action = MessageAction(content=command)  # 创建消息Action
        event_stream.add_event(action, EventSource.USER)  # 将消息添加到事件流

    return close_repl, reload_microagents, new_session_requested, exit_reason


def handle_exit_command(
    config: OpenHandsConfig,
    event_stream: EventStream,
    usage_metrics: UsageMetrics,
    sid: str,
) -> bool:
    """
    处理退出命令。
    
    显示确认对话框，如果用户确认退出，则停止Agent并显示关闭消息。
    
    Args:
        config: OpenHands配置对象
        event_stream: 事件流对象
        usage_metrics: 使用指标统计对象
        sid: session ID
        
    Returns:
        bool: 如果用户确认退出返回True，否则返回False
    """
    close_repl = False  # 初始化关闭标志

    # 显示确认退出的对话框
    confirm_exit = (
        cli_confirm(config, '\nTerminate session?', ['Yes, proceed', 'No, dismiss'])
        == 0  # 0表示选择了第一个选项（Yes, proceed）
    )

    if confirm_exit:
        # 用户确认退出，向事件流添加停止Agent的事件
        event_stream.add_event(
            ChangeAgentStateAction(AgentState.STOPPED),
            EventSource.ENVIRONMENT,
        )
        # 显示关闭消息，包含使用统计信息
        display_shutdown_message(usage_metrics, sid)
        close_repl = True  # 设置关闭标志

    return close_repl


def handle_help_command() -> None:
    """
    处理帮助命令。
    
    显示帮助信息，包括可用命令和使用说明。
    """
    display_help()


async def handle_init_command(
    config: OpenHandsConfig, event_stream: EventStream, current_dir: str
) -> tuple[bool, bool]:
    """
    处理初始化Repository命令。
    
    在本地或CLI运行时环境下初始化Repository，创建repo.md文件。
    
    Args:
        config: OpenHands配置对象
        event_stream: 事件流对象
        current_dir: 当前工作目录
        
    Returns:
        tuple[bool, bool]: (close_repl, reload_microagents)
            - close_repl: 是否关闭REPL
            - reload_microagents: 是否重新加载MicroAgent
    """
    # Repository描述创建提示词，用于指导Agent创建repo.md文件
    REPO_MD_CREATE_PROMPT = """
        Please explore this repository. Create the file .openhands/microagents/repo.md with:
            - A description of the project
            - An overview of the file structure
            - Any information on how to run tests or other relevant commands
            - Any other information that would be helpful to a brand new developer
        Keep it short--just a few paragraphs will do.
    """
    close_repl = False  # 初始化关闭REPL标志
    reload_microagents = False  # 初始化重新加载microagents标志

    # 检查是否为支持的运行时环境（本地或CLI）
    if config.runtime in ('local', 'cli'):
        # 尝试初始化Repository
        init_repo = await init_repository(config, current_dir)
        if init_repo:
            # 如果用户确认初始化，向事件流添加创建repo.md的消息
            event_stream.add_event(
                MessageAction(content=REPO_MD_CREATE_PROMPT),
                EventSource.USER,
            )
            reload_microagents = True  # 标记需要重新加载microagents
            close_repl = True  # 标记需要关闭REPL以执行Agent任务
    else:
        # 不支持的运行时环境，显示错误提示
        print_formatted_text(
            '\nRepository initialization through the CLI is only supported for CLI and local runtimes.\n'
        )

    return close_repl, reload_microagents


def handle_status_command(usage_metrics: UsageMetrics, sid: str) -> None:
    """
    处理状态查询命令。
    
    显示当前会话的状态信息，包括使用统计和session ID。
    
    Args:
        usage_metrics: 使用指标统计对象
        sid: session ID
    """
    display_status(usage_metrics, sid)


def handle_new_command(
    config: OpenHandsConfig,
    event_stream: EventStream,
    usage_metrics: UsageMetrics,
    sid: str,
) -> tuple[bool, bool]:
    """
    处理新建会话命令。
    
    提示用户确认是否要终止当前会话并创建新会话。
    
    Args:
        config: OpenHands配置对象
        event_stream: 事件流对象
        usage_metrics: 使用指标统计对象
        sid: 当前session ID
        
    Returns:
        tuple[bool, bool]: (close_repl, new_session_requested)
            - close_repl: 是否关闭当前REPL
            - new_session_requested: 是否请求新session
    """
    close_repl = False  # 初始化关闭REPL标志
    new_session_requested = False  # 初始化新session请求标志

    # 显示确认对话框，警告用户会丢失对话历史
    new_session_requested = (
        cli_confirm(
            config,
            '\nCurrent session will be terminated and you will lose the conversation history.\n\nContinue?',
            ['Yes, proceed', 'No, dismiss'],
        )
        == 0  # 0表示选择了第一个选项（Yes, proceed）
    )

    if new_session_requested:
        close_repl = True  # 标记关闭当前REPL
        new_session_requested = True  # 确认请求新session
        # 向事件流添加停止Agent的事件
        event_stream.add_event(
            ChangeAgentStateAction(AgentState.STOPPED),
            EventSource.ENVIRONMENT,
        )
        # 显示当前会话的关闭消息
        display_shutdown_message(usage_metrics, sid)

    return close_repl, new_session_requested


async def handle_settings_command(
    config: OpenHandsConfig,
    settings_store: FileSettingsStore,
) -> None:
    """
    处理设置命令。
    
    显示当前设置并允许用户修改基础或高级设置。
    
    Args:
        config: OpenHands配置对象
        settings_store: 设置存储对象
    """
    # 显示当前设置
    display_settings(config)
    
    # 提供设置修改选项
    modify_settings = cli_confirm(
        config,
        '\nWhich settings would you like to modify?',
        [
            'Basic',      # 基础设置
            'Advanced',   # 高级设置
            'Go back',    # 返回
        ],
    )

    if modify_settings == 0:
        # 用户选择修改基础设置
        await modify_llm_settings_basic(config, settings_store)
    elif modify_settings == 1:
        # 用户选择修改高级设置
        await modify_llm_settings_advanced(config, settings_store)
    # modify_settings == 2时表示用户选择返回，不执行任何操作


# FIXME: 当前'resume'行为存在问题
# 将Agent状态设置为RUNNING会导致Agent冻结而不继续执行剩余任务
# 这是一个临时解决方案，用消息替代状态变更事件，等问题修复后再替换
async def handle_resume_command(
    event_stream: EventStream,
) -> tuple[bool, bool]:
    """
    处理恢复Agent执行命令。
    
    当Agent处于暂停状态时，此命令用于恢复Agent的执行。
    注意：当前实现使用"continue"消息而非状态变更事件，这是一个临时解决方案。
    
    Args:
        event_stream: 事件流对象
        
    Returns:
        tuple[bool, bool]: (close_repl, new_session_requested)
            - close_repl: 总是返回True，让Agent继续处理
            - new_session_requested: 总是返回False，不请求新session
    """
    close_repl = True  # 关闭REPL以让Agent继续执行
    new_session_requested = False  # 不请求新session

    # 向事件流添加"continue"消息来恢复Agent执行
    event_stream.add_event(
        MessageAction(content='continue'),
        EventSource.USER,
    )

    # TODO: 一旦Agent状态变更问题修复，使用以下代码替代上面的消息方式
    # event_stream.add_event(
    #     ChangeAgentStateAction(AgentState.RUNNING),
    #     EventSource.ENVIRONMENT,
    # )

    return close_repl, new_session_requested


async def init_repository(config: OpenHandsConfig, current_dir: str) -> bool:
    """
    初始化Repository的异步函数。
    
    检查repo.md文件是否存在，如果存在则显示内容并询问是否重新初始化，
    如果不存在则询问是否创建。
    
    Args:
        config: OpenHands配置对象
        current_dir: 当前工作目录路径
        
    Returns:
        bool: 如果用户确认初始化返回True，否则返回False
    """
    # 构建repo.md文件的完整路径
    repo_file_path = Path(current_dir) / '.openhands' / 'microagents' / 'repo.md'
    init_repo = False  # 初始化标志

    if repo_file_path.exists():
        # repo.md文件已存在的情况
        try:
            # 异步读取文件内容
            # Path.exists()确保repo_file_path不为None，所以可以安全传递给read_file
            content = await asyncio.get_event_loop().run_in_executor(
                None, read_file, repo_file_path
            )

            # 提示用户文件已存在
            print_formatted_text(
                'Repository instructions file (repo.md) already exists.\n'
            )

            # 在带框架的文本区域中显示文件内容
            container = Frame(
                TextArea(
                    text=content,
                    read_only=True,  # 只读模式
                    style=COLOR_GREY,  # 灰色样式
                    wrap_lines=True,  # 自动换行
                ),
                title='Repository Instructions (repo.md)',  # 框架标题
                style=f'fg:{COLOR_GREY}',  # 框架样式
            )
            print_container(container)
            print_formatted_text('')  # 在框架后添加换行

            # 询问用户是否要重新初始化
            init_repo = (
                cli_confirm(
                    config,
                    'Do you want to re-initialize?',
                    ['Yes, re-initialize', 'No, dismiss'],
                )
                == 0
            )

            if init_repo:
                # 用户确认重新初始化，清空文件内容
                write_to_file(repo_file_path, '')
        except Exception:
            # 读取文件时发生错误
            print_formatted_text('Error reading repository instructions file (repo.md)')
            init_repo = False
    else:
        # repo.md文件不存在的情况
        print_formatted_text(
            '\nRepository instructions file will be created by exploring the repository.\n'
        )

        # 询问用户是否要创建文件
        init_repo = (
            cli_confirm(
                config,
                'Do you want to proceed?',
                ['Yes, create', 'No, dismiss'],
            )
            == 0
        )

    return init_repo


def check_folder_security_agreement(config: OpenHandsConfig, current_dir: str) -> bool:
    """
    检查文件夹安全协议。
    
    验证用户是否信任当前工作目录，如果目录不在信任列表中，
    则显示安全警告并要求用户确认。用户信任的目录会被添加到本地配置中。
    
    Args:
        config: OpenHands配置对象，包含应用级别的信任目录配置
        current_dir: 当前工作目录路径
        
    Returns:
        bool: 如果目录已被信任或用户确认信任返回True，否则返回False
    """
    # 获取用户在CLI中信任的目录列表
    # 本地配置文件 ~/.openhands/config.toml 中的设置会覆盖应用配置

    app_config_trusted_dirs = config.sandbox.trusted_dirs  # 应用配置中的信任目录
    local_config_trusted_dirs = get_local_config_trusted_dirs()  # 本地配置中的信任目录

    # 优先使用本地配置，如果本地配置为空则使用应用配置
    trusted_dirs = local_config_trusted_dirs
    if not local_config_trusted_dirs:
        trusted_dirs = app_config_trusted_dirs

    # 检查当前目录是否在信任列表中
    is_trusted = current_dir in trusted_dirs

    if not is_trusted:
        # 当前目录不被信任，显示安全警告框
        security_frame = Frame(
            TextArea(
                text=(
                    f' Do you trust the files in this folder?\n\n'
                    f'   {current_dir}\n\n'
                    ' OpenHands may read and execute files in this folder with your permission.'
                ),
                style=COLOR_GREY,
                read_only=True,
                wrap_lines=True,
            ),
            style=f'fg:{COLOR_GREY}',
        )

        # 清屏并显示安全警告
        clear()
        print_container(security_frame)
        print_formatted_text('')

        # 询问用户是否要继续
        confirm = (
            cli_confirm(
                config, 'Do you wish to continue?', ['Yes, proceed', 'No, exit']
            )
            == 0
        )

        if confirm:
            # 用户确认信任，将目录添加到本地配置的信任列表
            add_local_config_trusted_dir(current_dir)

        return confirm

    # 目录已被信任，直接返回True
    return True