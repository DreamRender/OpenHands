"""
OpenHands 主程序模块

此模块是 OpenHands 的主入口点，提供了运行 Agent 控制器的核心功能。
包括控制器的创建、运行、事件处理和轨迹保存等功能。
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Callable, Protocol

import openhands.agenthub  # noqa F401 (导入此模块以注册所有 agents)
import openhands.cli.suppress_warnings  # noqa: F401
from openhands.controller.agent import Agent
from openhands.controller.replay import ReplayManager
from openhands.controller.state.state import State
from openhands.core.config import (
    OpenHandsConfig,
    parse_arguments,
    setup_config_from_args,
)
from openhands.core.config.mcp_config import OpenHandsMCPConfigImpl
from openhands.core.logger import openhands_logger as logger
from openhands.core.loop import run_agent_until_done
from openhands.core.schema import AgentState
from openhands.core.setup import (
    create_agent,
    create_controller,
    create_memory,
    create_runtime,
    generate_sid,
    initialize_repository_for_runtime,
)
from openhands.events import EventSource, EventStreamSubscriber
from openhands.events.action import MessageAction, NullAction
from openhands.events.action.action import Action
from openhands.events.event import Event
from openhands.events.observation import AgentStateChangedObservation
from openhands.io import read_input, read_task
from openhands.mcp import add_mcp_tools_to_agent
from openhands.memory.memory import Memory
from openhands.runtime.base import Runtime
from openhands.utils.async_utils import call_async_from_sync


class FakeUserResponseFunc(Protocol):
    """
    伪造用户响应函数的协议定义。
    
    这个协议定义了生成伪造用户响应的函数签名。
    """
    def __call__(
        self,
        state: State,
        encapsulate_solution: bool = False,
        try_parse: Callable[[Action | None], str] | None = None,
    ) -> str: ...


async def run_controller(
    config: OpenHandsConfig,
    initial_user_action: Action,
    sid: str | None = None,
    runtime: Runtime | None = None,
    agent: Agent | None = None,
    exit_on_message: bool = False,
    fake_user_response_fn: FakeUserResponseFunc | None = None,
    headless_mode: bool = True,
    memory: Memory | None = None,
    conversation_instructions: str | None = None,
) -> State | None:
    """
    运行 Agent 控制器的主协程，具有任务输入灵活性。

    仅在通过命令行直接启动 openhands 后端时使用。

    Args:
        config: 应用程序配置对象
        initial_user_action: 包含初始用户输入的 Action 对象
        sid: 可选的 Session ID。重要提示：除非你明确知道自己在做什么，否则请不要设置此参数。
             设置不兼容的值会在 RemoteRuntime 上导致意外行为。
        runtime: 可选的 Agent 运行时环境
        agent: 可选的要运行的 Agent
        exit_on_message: 如果 Agent 请求用户消息时是否退出（可选）
        fake_user_response_fn: 可选的函数，接收当前状态（可能为 None）并返回伪造的用户响应
        headless_mode: Agent 是否在无头模式下运行
        memory: 可选的 Memory 对象
        conversation_instructions: 可选的对话指令

    Returns:
        Agent 的最终状态，如果发生错误则返回 None。

    Raises:
        AssertionError: 如果 initial_user_action 不是 Action 实例。
        Exception: 执行过程中可能引发各种异常并将被记录。

    Notes:
        - State 持久化：如果设置了 config.file_store，Agent 的状态将在 Session 之间保存。
        - 轨迹：如果设置了 config.trajectories_path，执行历史将保存为 JSON 用于分析。
        - 预算控制：执行受 config.max_iterations 和 config.max_budget_per_task 限制。

    Example:
        >>> config = load_openhands_config()
        >>> action = MessageAction(content="写一个 hello world 程序")
        >>> state = await run_controller(config=config, initial_user_action=action)
    """
    # 如果没有提供 sid，则生成一个
    sid = sid or generate_sid(config)

    # 如果没有提供 Agent，则创建一个
    if agent is None:
        agent = create_agent(config)

    # 当创建运行时时，它将被连接并克隆选定的 Repository
    repo_directory = None
    if runtime is None:
        # 创建运行时环境
        runtime = create_runtime(
            config,
            sid=sid,
            headless_mode=headless_mode,
            agent=agent,
        )
        # 连接到运行时
        call_async_from_sync(runtime.connect)

        # 如果需要，初始化 Repository
        if config.sandbox.selected_repo:
            repo_directory = initialize_repository_for_runtime(
                runtime,
                selected_repository=config.sandbox.selected_repo,
            )

    # 获取事件流
    event_stream = runtime.event_stream

    # 当创建 Memory 时，它将从选定的 Repository 加载 microagents
    if memory is None:
        memory = create_memory(
            runtime=runtime,
            event_stream=event_stream,
            sid=sid,
            selected_repository=config.sandbox.selected_repo,
            repo_directory=repo_directory,
            conversation_instructions=conversation_instructions,
        )

    # 向 Agent 添加 MCP 工具
    if agent.config.enable_mcp:
        # 默认添加 OpenHands 的 MCP 服务器
        _, openhands_mcp_stdio_servers = (
            OpenHandsMCPConfigImpl.create_default_mcp_server_config(
                config.mcp_host, config, None
            )
        )
        runtime.config.mcp.stdio_servers.extend(openhands_mcp_stdio_servers)

        await add_mcp_tools_to_agent(agent, runtime, memory)

    # 处理轨迹重放
    replay_events: list[Event] | None = None
    if config.replay_trajectory_path:
        logger.info('轨迹重放已启用')
        assert isinstance(initial_user_action, NullAction)
        replay_events, initial_user_action = load_replay_log(
            config.replay_trajectory_path
        )

    # 创建控制器
    controller, initial_state = create_controller(
        agent, runtime, config, replay_events=replay_events
    )

    # 验证初始用户操作
    assert isinstance(initial_user_action, Action), (
        f'初始用户操作必须是 Action，得到的是 {type(initial_user_action)}'
    )
    logger.debug(
        f'Agent 控制器已初始化：正在运行 Agent {agent.name}，模型 '
        f'{agent.llm.config.model}，操作：{initial_user_action}'
    )

    # 开始事件是包含任务的 MessageAction，无论是恢复的还是新的
    if initial_state is not None and initial_state.last_error:
        # 我们正在恢复之前的 Session
        event_stream.add_event(
            MessageAction(
                content=(
                    "让我们回到正轨。如果你之前遇到错误，请不要"
                    '恢复你的任务。问我相关问题。'
                ),
            ),
            EventSource.USER,
        )
    else:
        # 使用提供的操作初始化
        event_stream.add_event(initial_user_action, EventSource.USER)

    def on_event(event: Event) -> None:
        """
        事件处理回调函数。
        
        Args:
            event: 要处理的事件对象
        """
        # 处理 Agent 状态变化事件
        if isinstance(event, AgentStateChangedObservation):
            if event.agent_state == AgentState.AWAITING_USER_INPUT:
                # Agent 等待用户输入时的处理
                if exit_on_message:
                    message = '/exit'
                elif fake_user_response_fn is None:
                    # 从用户读取输入
                    message = read_input(config.cli_multiline_input)
                else:
                    # 使用伪造的用户响应函数
                    message = fake_user_response_fn(controller.get_state())
                
                # 创建消息操作并添加到事件流
                action = MessageAction(content=message)
                event_stream.add_event(action, EventSource.USER)

    # 订阅事件流
    event_stream.subscribe(EventStreamSubscriber.MAIN, on_event, sid)

    # 定义结束状态
    end_states = [
        AgentState.FINISHED,    # 已完成
        AgentState.REJECTED,    # 已拒绝
        AgentState.ERROR,       # 错误
        AgentState.PAUSED,      # 已暂停
        AgentState.STOPPED,     # 已停止
    ]

    try:
        # 运行 Agent 直到达到结束状态
        await run_agent_until_done(controller, runtime, memory, end_states)
    except Exception as e:
        logger.error(f'主循环中的异常: {e}')

    # 在即将关闭时保存 Session
    if config.file_store is not None and config.file_store != 'memory':
        end_state = controller.get_state()
        # 注意：保存的状态不包括委托事件
        end_state.save_to_session(
            event_stream.sid, event_stream.file_store, event_stream.user_id
        )

    # 关闭控制器
    await controller.close(set_stop_state=False)

    # 获取最终状态
    state = controller.get_state()

    # 如果适用，保存轨迹
    if config.save_trajectory_path is not None:
        # 如果 save_trajectory_path 是文件夹，使用 Session ID 作为文件名
        if os.path.isdir(config.save_trajectory_path):
            file_path = os.path.join(config.save_trajectory_path, sid + '.json')
        else:
            file_path = config.save_trajectory_path
        
        # 确保目录存在
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # 获取历史轨迹并保存
        histories = controller.get_trajectory(config.save_screenshots_in_trajectory)
        with open(file_path, 'w') as f:  # noqa: ASYNC101
            json.dump(histories, f, indent=4)

    return state


def auto_continue_response(
    state: State,
    encapsulate_solution: bool = False,
    try_parse: Callable[[Action | None], str] | None = None,
) -> str:
    """
    生成用户响应的默认函数。
    告诉 Agent 继续进行任何它认为合适的方法，或完成交互。
    
    Args:
        state: 当前状态对象
        encapsulate_solution: 是否封装解决方案（未使用）
        try_parse: 尝试解析的回调函数（未使用）
        
    Returns:
        自动继续的响应消息
    """
    message = (
        '请继续你认为合适的任何方法。\n'
        '如果你认为已经解决了任务，请完成交互。\n'
        '重要提示：你永远不应该要求人类响应。\n'
    )
    return message


def load_replay_log(trajectory_path: str) -> tuple[list[Event] | None, Action]:
    """
    从给定路径加载轨迹，将其序列化为事件列表，并返回两个内容：
    1) 除了第一个操作之外的事件列表
    2) 第一个操作（用户消息，即初始任务）
    
    Args:
        trajectory_path: 轨迹文件路径
        
    Returns:
        包含事件列表和初始操作的元组
        
    Raises:
        ValueError: 当文件不存在、不是文件或 JSON 格式无效时
    """
    try:
        # 解析文件路径
        path = Path(trajectory_path).resolve()

        # 检查文件是否存在
        if not path.exists():
            raise ValueError(f'轨迹文件未找到: {path}')

        # 检查是否为文件
        if not path.is_file():
            raise ValueError(f'轨迹路径是目录，不是文件: {path}')

        # 读取并解析轨迹文件
        with open(path, 'r', encoding='utf-8') as file:
            events = ReplayManager.get_replay_events(json.load(file))
            assert isinstance(events[0], MessageAction)
            return events[1:], events[0]
    except json.JSONDecodeError as e:
        raise ValueError(f'{trajectory_path} 中的 JSON 格式无效: {e}')


if __name__ == '__main__':
    """主程序入口点"""
    # 解析命令行参数
    args = parse_arguments()

    # 设置配置
    config: OpenHandsConfig = setup_config_from_args(args)

    # 从文件、CLI 参数或标准输入读取任务
    task_str = read_task(args, config.cli_multiline_input)

    # 初始化用户操作
    initial_user_action: Action = NullAction()
    
    if config.replay_trajectory_path:
        # 轨迹重放模式
        if task_str:
            raise ValueError(
                '轨迹重放模式下不支持用户指定的任务'
            )
    else:
        # 正常模式
        if not task_str:
            raise ValueError('未提供任务。请通过 -t、-f 指定任务。')

        # 创建实际的初始用户操作
        initial_user_action = MessageAction(content=task_str)

    # 设置 Session 名称
    session_name = args.name
    sid = generate_sid(config, session_name)

    # 运行控制器
    asyncio.run(
        run_controller(
            config=config,
            initial_user_action=initial_user_action,
            sid=sid,
            fake_user_response_fn=None
            if args.no_auto_continue
            else auto_continue_response,
        )
    )