import asyncio
import logging
import os
import sys

from prompt_toolkit import print_formatted_text
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.shortcuts import clear

import openhands.agenthub  # noqa F401 (导入此模块以注册agents)
import openhands.cli.suppress_warnings  # noqa: F401
from openhands.cli.commands import (
    check_folder_security_agreement,
    handle_commands,
)
from openhands.cli.settings import modify_llm_settings_basic
from openhands.cli.tui import (
    UsageMetrics,
    display_agent_running_message,
    display_banner,
    display_event,
    display_initial_user_prompt,
    display_initialization_animation,
    display_runtime_initialization_message,
    display_welcome_message,
    read_confirmation_input,
    read_prompt_input,
    start_pause_listener,
    stop_pause_listener,
    update_streaming_output,
)
from openhands.cli.utils import (
    update_usage_metrics,
)
from openhands.cli.vscode_extension import attempt_vscode_extension_install
from openhands.controller import AgentController
from openhands.controller.agent import Agent
from openhands.core.config import (
    OpenHandsConfig,
    parse_arguments,
    setup_config_from_args,
)
from openhands.core.config.condenser_config import NoOpCondenserConfig
from openhands.core.config.mcp_config import OpenHandsMCPConfigImpl
from openhands.core.config.utils import finalize_config
from openhands.core.logger import openhands_logger as logger
from openhands.core.loop import run_agent_until_done
from openhands.core.schema import AgentState
from openhands.core.schema.exit_reason import ExitReason
from openhands.core.setup import (
    create_agent,
    create_controller,
    create_memory,
    create_runtime,
    generate_sid,
    initialize_repository_for_runtime,
)
from openhands.events import EventSource, EventStreamSubscriber
from openhands.events.action import (
    ChangeAgentStateAction,
    MessageAction,
)
from openhands.events.event import Event
from openhands.events.observation import (
    AgentStateChangedObservation,
)
from openhands.io import read_task
from openhands.mcp import add_mcp_tools_to_agent
from openhands.memory.condenser.impl.llm_summarizing_condenser import (
    LLMSummarizingCondenserConfig,
)
from openhands.microagent.microagent import BaseMicroagent
from openhands.runtime.base import Runtime
from openhands.storage.settings.file_settings_store import FileSettingsStore


async def cleanup_session(
    loop: asyncio.AbstractEventLoop,
    agent: Agent,
    runtime: Runtime,
    controller: AgentController,
) -> None:
    """
    清理当前会话的所有资源。
    
    保存会话状态，取消未完成的任务，重置Agent和Runtime，关闭Controller。
    
    Args:
        loop: 异步事件循环对象
        agent: Agent实例
        runtime: Runtime实例
        controller: AgentController实例
    """
    # 获取事件流和最终状态
    event_stream = runtime.event_stream
    end_state = controller.get_state()
    
    # 保存会话状态到存储
    end_state.save_to_session(
        event_stream.sid,
        event_stream.file_store,
        event_stream.user_id,
    )

    try:
        # 获取当前任务
        current_task = asyncio.current_task(loop)
        # 获取所有待处理的任务（除了当前任务）
        pending = [task for task in asyncio.all_tasks(loop) if task is not current_task]

        if pending:
            # 等待待处理任务完成或超时（2秒）
            done, pending_set = await asyncio.wait(set(pending), timeout=2.0)
            pending = list(pending_set)

        # 取消所有仍在等待的任务
        for task in pending:
            task.cancel()

        # 重置Agent、关闭Runtime和Controller
        agent.reset()
        runtime.close()
        await controller.close()

    except Exception as e:
        logger.error(f'Error during session cleanup: {e}')


async def run_session(
    loop: asyncio.AbstractEventLoop,
    config: OpenHandsConfig,
    settings_store: FileSettingsStore,
    current_dir: str,
    task_content: str | None = None,
    conversation_instructions: str | None = None,
    session_name: str | None = None,
    skip_banner: bool = False,
) -> bool:
    """
    运行一个完整的OpenHands会话。
    
    初始化所有必要的组件（Agent、Runtime、Controller），处理事件循环，
    管理用户交互，直到会话结束。
    
    Args:
        loop: 异步事件循环对象
        config: OpenHands配置对象
        settings_store: 设置存储对象
        current_dir: 当前工作目录
        task_content: 可选的初始任务内容
        conversation_instructions: 可选的对话指令
        session_name: 可选的会话名称
        skip_banner: 是否跳过显示banner
        
    Returns:
        bool: 如果请求新会话返回True，否则返回False
    """
    # 初始化会话控制变量
    reload_microagents = False  # 是否重新加载MicroAgent的标志
    new_session_requested = False  # 是否请求新会话的标志
    exit_reason = ExitReason.INTENTIONAL  # 默认退出原因

    # 生成会话ID
    sid = generate_sid(config, session_name)
    is_loaded = asyncio.Event()  # 加载完成事件
    is_paused = asyncio.Event()  # Agent暂停请求跟踪事件
    always_confirm_mode = False  # 启用始终确认模式的标志

    # 显示Runtime初始化消息
    display_runtime_initialization_message(config.runtime)

    # 显示初始化加载动画
    loop.run_in_executor(
        None, display_initialization_animation, 'Initializing...', is_loaded
    )

    # 创建核心组件
    agent = create_agent(config)  # 创建Agent实例
    runtime = create_runtime(  # 创建Runtime实例
        config,
        sid=sid,
        headless_mode=True,  # 无头模式，适合CLI使用
        agent=agent,
    )

    def stream_to_console(output: str) -> None:
        """
        将输出流式传输到控制台的回调函数。
        
        Args:
            output: 要输出的字符串内容
        """
        # 不直接打印到stdout，而是传递给TUI模块处理
        update_streaming_output(output)

    # 订阅shell输出流
    runtime.subscribe_to_shell_stream(stream_to_console)

    # 创建Controller和初始状态
    controller, initial_state = create_controller(agent, runtime, config)

    # 获取事件流和使用指标统计对象
    event_stream = runtime.event_stream
    usage_metrics = UsageMetrics()

    async def prompt_for_next_task(agent_state: str) -> None:
        """
        提示用户输入下一个任务的异步函数。
        
        持续读取用户输入直到收到有效命令或消息。
        
        Args:
            agent_state: 当前Agent状态
        """
        nonlocal reload_microagents, new_session_requested, exit_reason
        while True:
            # 读取用户输入
            next_message = await read_prompt_input(
                config, agent_state, multiline=config.cli_multiline_input
            )

            # 忽略空输入
            if not next_message.strip():
                continue

            # 处理用户命令
            (
                close_repl,
                reload_microagents,
                new_session_requested,
                exit_reason,
            ) = await handle_commands(
                next_message,
                event_stream,
                usage_metrics,
                sid,
                config,
                current_dir,
                settings_store,
            )

            # 如果需要关闭REPL，退出输入循环
            if close_repl:
                return

    async def on_event_async(event: Event) -> None:
        """
        异步事件处理函数。
        
        处理来自Agent和Runtime的各种事件，更新UI显示，管理用户交互。
        
        Args:
            event: 要处理的事件对象
        """
        nonlocal reload_microagents, is_paused, always_confirm_mode
        
        # 显示事件并更新使用指标
        display_event(event, config)
        update_usage_metrics(event, usage_metrics)

        # 处理Agent状态变更事件
        if isinstance(event, AgentStateChangedObservation):
            # 如果Agent不在运行或暂停状态，停止暂停监听器
            if event.agent_state not in [AgentState.RUNNING, AgentState.PAUSED]:
                await stop_pause_listener()

        if isinstance(event, AgentStateChangedObservation):
            # 处理等待用户输入或任务完成状态
            if event.agent_state in [
                AgentState.AWAITING_USER_INPUT,
                AgentState.FINISHED,
            ]:
                # 如果Agent已暂停，不提示输入（暂停状态变更会处理）
                if is_paused.is_set():
                    return

                # 在repo.md初始化后重新加载microagents
                if reload_microagents:
                    microagents: list[BaseMicroagent] = (
                        runtime.get_microagents_from_selected_repo(None)
                    )
                    memory.load_user_workspace_microagents(microagents)
                    reload_microagents = False
                await prompt_for_next_task(event.agent_state)

            # 处理等待用户确认状态
            if event.agent_state == AgentState.AWAITING_USER_CONFIRMATION:
                # 如果Agent已暂停，不提示确认（恢复后会重新运行确认步骤）
                if is_paused.is_set():
                    return

                # 如果启用了始终确认模式，自动确认
                if always_confirm_mode:
                    event_stream.add_event(
                        ChangeAgentStateAction(AgentState.USER_CONFIRMED),
                        EventSource.USER,
                    )
                    return

                # 读取用户确认输入
                confirmation_status = await read_confirmation_input(config)
                if confirmation_status == 'yes' or confirmation_status == 'always':
                    event_stream.add_event(
                        ChangeAgentStateAction(AgentState.USER_CONFIRMED),
                        EventSource.USER,
                    )
                else:
                    event_stream.add_event(
                        ChangeAgentStateAction(AgentState.USER_REJECTED),
                        EventSource.USER,
                    )

                # 如果用户选择始终确认，设置始终确认模式标志
                if confirmation_status == 'always':
                    always_confirm_mode = True

            # 处理暂停状态
            if event.agent_state == AgentState.PAUSED:
                is_paused.clear()  # 重置事件状态，然后提示用户输入
                await prompt_for_next_task(event.agent_state)

            # 处理运行状态
            if event.agent_state == AgentState.RUNNING:
                display_agent_running_message()
                start_pause_listener(loop, is_paused, event_stream)

    def on_event(event: Event) -> None:
        """
        事件处理函数包装器。
        
        将同步事件处理转换为异步任务。
        
        Args:
            event: 要处理的事件对象
        """
        loop.create_task(on_event_async(event))

    # 订阅事件流
    event_stream.subscribe(EventStreamSubscriber.MAIN, on_event, sid)

    # 连接Runtime
    await runtime.connect()

    # 如果需要，初始化Repository
    repo_directory = None
    if config.sandbox.selected_repo:
        repo_directory = initialize_repository_for_runtime(
            runtime,
            selected_repository=config.sandbox.selected_repo,
        )

    # 创建内存时，会从选定的Repository加载microagents
    memory = create_memory(
        runtime=runtime,
        event_stream=event_stream,
        sid=sid,
        selected_repository=config.sandbox.selected_repo,
        repo_directory=repo_directory,
        conversation_instructions=conversation_instructions,
    )

    # 将MCP工具添加到Agent
    if agent.config.enable_mcp:
        # 默认添加OpenHands的MCP服务器
        _, openhands_mcp_stdio_servers = (
            OpenHandsMCPConfigImpl.create_default_mcp_server_config(
                config.mcp_host, config, None
            )
        )

        runtime.config.mcp.stdio_servers.extend(openhands_mcp_stdio_servers)

        await add_mcp_tools_to_agent(agent, runtime, memory)

    # 清除加载动画
    is_loaded.set()

    # 清屏
    clear()

    # 如果未跳过，显示OpenHands banner和session ID
    if not skip_banner:
        display_banner(session_id=sid)

    # 设置欢迎消息和初始消息
    welcome_message = 'What do you want to build?'  # 来自应用的欢迎消息
    initial_message = ''  # 来自用户的初始消息

    if task_content:
        initial_message = task_content

    # 如果加载了状态，说明正在恢复之前的会话
    if initial_state is not None:
        logger.info(f'Resuming session: {sid}')

        if initial_state.last_error:
            # 如果上次会话以错误结束，提供提示消息
            initial_message = (
                'NOTE: the last session ended with an error.'
                "Let's get back on track. Do NOT resume your task. Ask me about it."
            )
        else:
            # 如果正在恢复，已经有任务了
            initial_message = ''
            welcome_message += '\nLoading previous conversation.'

    # 显示OpenHands欢迎消息
    display_welcome_message(welcome_message)

    # 如果Agent进入AWAITING_USER_INPUT状态，prompt_for_next_task将被触发
    # 如果恢复的状态已经是AWAITING_USER_INPUT，on_event_async会处理它

    if initial_message:
        # 有初始消息，显示并添加到事件流
        display_initial_user_prompt(initial_message)
        event_stream.add_event(MessageAction(content=initial_message), EventSource.USER)
    else:
        # 没有恢复会话，没有初始动作：提示用户输入第一条消息
        asyncio.create_task(prompt_for_next_task(''))

    # 运行Agent直到完成
    await run_agent_until_done(
        controller, runtime, memory, [AgentState.STOPPED, AgentState.ERROR]
    )

    # 清理会话资源
    await cleanup_session(loop, agent, runtime, controller)

    # 显示会话结束消息
    if exit_reason == ExitReason.INTENTIONAL:
        print_formatted_text('✅ Session terminated successfully.\n')
    else:
        print_formatted_text(f'⚠️ Session was interrupted: {exit_reason.value}\n')

    return new_session_requested


async def run_setup_flow(config: OpenHandsConfig, settings_store: FileSettingsStore):
    """
    运行设置流程来配置初始设置。
    
    当没有找到设置时，引导用户完成基本的LLM设置配置。

    Args:
        config: OpenHands配置对象
        settings_store: 设置存储对象
        
    Returns:
        bool: 如果设置配置成功返回True，否则返回False
    """
    # 首先显示带ASCII艺术的banner
    display_banner(session_id='setup')

    print_formatted_text(
        HTML('<grey>No settings found. Starting initial setup...</grey>\n')
    )

    # 使用现有的设置修改函数进行基本设置
    await modify_llm_settings_basic(config, settings_store)


async def main_with_loop(loop: asyncio.AbstractEventLoop) -> None:
    """
    在CLI模式下运行Agent的主函数。
    
    解析命令行参数，加载配置，运行设置流程（如果需要），
    验证文件夹安全性，然后启动OpenHands会话。
    
    Args:
        loop: 异步事件循环对象
    """
    # 解析命令行参数
    args = parse_arguments()

    # 设置日志级别为WARNING（减少噪音）
    logger.setLevel(logging.WARNING)

    # 从toml文件加载配置并用命令行参数覆盖
    config: OpenHandsConfig = setup_config_from_args(args)

    # 尝试安装VS Code扩展（如果适用，一次性尝试）
    attempt_vscode_extension_install()

    # 从设置存储加载设置
    # TODO: 让这个更通用？
    settings_store = await FileSettingsStore.get_instance(config=config, user_id=None)
    settings = await settings_store.load()

    # 跟踪是否在设置期间显示了banner
    banner_shown = False

    # 如果设置不存在，自动进入设置流程
    if not settings:
        # 在显示banner之前清屏
        clear()

        await run_setup_flow(config, settings_store)
        banner_shown = True

        settings = await settings_store.load()

    # 如果有可用设置，使用设置存储中的设置并用命令行参数覆盖
    if settings:
        if args.agent_cls:
            config.default_agent = str(args.agent_cls)
        else:
            # settings.agent不为None，因为我们在setup_config_from_args中检查过
            assert settings.agent is not None
            config.default_agent = settings.agent
            
        if not args.llm_config and settings.llm_model and settings.llm_api_key:
            llm_config = config.get_llm_config()
            llm_config.model = settings.llm_model
            llm_config.api_key = settings.llm_api_key
            llm_config.base_url = settings.llm_base_url
            config.set_llm_config(llm_config)
            
        config.security.confirmation_mode = (
            settings.confirmation_mode if settings.confirmation_mode else False
        )

        # 配置内存Condenser
        if settings.enable_default_condenser:
            # TODO: 让这个更通用？
            llm_config = config.get_llm_config()
            agent_config = config.get_agent_config(config.default_agent)
            agent_config.condenser = LLMSummarizingCondenserConfig(
                llm_config=llm_config,
                type='llm',
            )
            config.set_agent_config(agent_config)
            config.enable_default_condenser = True
        else:
            agent_config = config.get_agent_config(config.default_agent)
            agent_config.condenser = NoOpCondenserConfig(type='noop')
            config.set_agent_config(agent_config)
            config.enable_default_condenser = False

    # 确定是否应该覆盖CLI默认值
    val_override = args.override_cli_mode
    should_override_cli_defaults = (
        val_override is True
        or (isinstance(val_override, str) and val_override.lower() in ('true', '1'))
        or (isinstance(val_override, int) and val_override == 1)
    )

    if not should_override_cli_defaults:
        # 设置CLI特定的默认配置
        config.runtime = 'cli'
        if not config.workspace_base:
            config.workspace_base = os.getcwd()
        config.security.confirmation_mode = True

        # 将runtime设置为'cli'后需要再次finalize配置
        # 这确保Jupyter插件在CLI runtime下被禁用
        finalize_config(config)

    # TODO: 从配置设置工作目录或使用当前工作目录？
    current_dir = config.workspace_base

    if not current_dir:
        raise ValueError('Workspace base directory not specified')

    # 检查文件夹安全协议
    if not check_folder_security_agreement(config, current_dir):
        # 用户拒绝，退出应用
        return

    # 从文件、CLI参数或stdin读取任务
    if args.file:
        # 对于CLI使用，我们要用提示词增强文件内容
        # 指示Agent首先读取和理解文件
        with open(args.file, 'r', encoding='utf-8') as file:
            file_content = file.read()

        # 创建指示Agent首先读取和理解文件的提示词
        task_str = f"""The user has tagged a file '{args.file}'.
Please read and understand the following file content first:

```
{file_content}
```

After reviewing the file, please ask the user what they would like to do with it."""
    else:
        task_str = read_task(args, config.cli_multiline_input)

    # 运行第一个会话
    new_session_requested = await run_session(
        loop,
        config,
        settings_store,
        current_dir,
        task_str,
        session_name=args.name,
        skip_banner=banner_shown,
    )

    # 如果请求了新会话，继续运行
    while new_session_requested:
        new_session_requested = await run_session(
            loop, config, settings_store, current_dir, None
        )


def main():
    """
    应用程序主入口点。
    
    创建事件循环，运行主逻辑，处理异常和清理。
    """
    # 创建新的事件循环
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # 运行主逻辑
        loop.run_until_complete(main_with_loop(loop))
    except KeyboardInterrupt:
        # 处理Ctrl+C中断
        print_formatted_text('⚠️ Session was interrupted: interrupted\n')
    except ConnectionRefusedError as e:
        # 处理连接拒绝错误
        print(f'Connection refused: {e}')
        sys.exit(1)
    except Exception as e:
        # 处理其他异常
        print(f'An error occurred: {e}')
        sys.exit(1)
    finally:
        try:
            # 取消所有运行中的任务
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()

            # 等待所有任务完成（带超时）
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
        except Exception as e:
            print(f'Error during cleanup: {e}')
            sys.exit(1)


if __name__ == '__main__':
    main()