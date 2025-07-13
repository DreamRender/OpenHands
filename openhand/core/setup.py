"""
OpenHands 核心设置模块

此模块提供了创建和初始化 OpenHands 系统各个核心组件的函数，
包括运行时环境、Repository 初始化、Memory 创建、Agent 创建和控制器创建等。
"""

import hashlib
import os
import uuid
from typing import Callable

from pydantic import SecretStr

import openhands.agenthub  # noqa F401 (导入此模块以注册所有 agents)
from openhands.controller import AgentController
from openhands.controller.agent import Agent
from openhands.controller.state.state import State
from openhands.core.config import (
    OpenHandsConfig,
)
from openhands.core.logger import openhands_logger as logger
from openhands.events import EventStream
from openhands.events.event import Event
from openhands.integrations.provider import ProviderToken, ProviderType
from openhands.llm.llm import LLM
from openhands.memory.memory import Memory
from openhands.microagent.microagent import BaseMicroagent
from openhands.runtime import get_runtime_cls
from openhands.runtime.base import Runtime
from openhands.security import SecurityAnalyzer, options
from openhands.storage import get_file_store
from openhands.storage.data_models.user_secrets import UserSecrets
from openhands.utils.async_utils import GENERAL_TIMEOUT, call_async_from_sync


def create_runtime(
    config: OpenHandsConfig,
    sid: str | None = None,
    headless_mode: bool = True,
    agent: Agent | None = None,
) -> Runtime:
    """
    为 Agent 创建运行时环境。

    Args:
        config: 应用程序配置对象
        sid: 可选的 Session ID。重要提示：除非你明确知道自己在做什么，否则请不要设置此参数。
             设置不兼容的值会在 RemoteRuntime 上导致意外行为。
        headless_mode: Agent 是否在无头模式下运行。通常 `create_runtime` 在评估脚本中调用，
                      我们不希望打开 VSCode UI，因此默认为 True。
        agent: 可选的 Agent 实例，用于配置运行时环境。

    Returns:
        创建的 Runtime 实例（尚未连接或初始化）。
    """
    # 如果在命令行中提供了 sid，则将其用作事件流的名称
    # 否则基于配置的 jwt_secret 生成它
    # 我们可以做得更好，这只是为了在我们想要恢复 Session 时检索 sid
    session_id = sid or generate_sid(config)

    # 设置事件流
    file_store = get_file_store(config.file_store, config.file_store_path)
    event_stream = EventStream(session_id, file_store)

    # 设置安全分析器
    if config.security.security_analyzer:
        options.SecurityAnalyzers.get(
            config.security.security_analyzer, SecurityAnalyzer
        )(event_stream)

    # Agent 类
    if agent:
        agent_cls = type(agent)
    else:
        agent_cls = Agent.get_cls(config.default_agent)

    # 运行时和工具
    runtime_cls = get_runtime_cls(config.runtime)
    logger.debug(f'正在初始化运行时: {runtime_cls.__name__}')
    runtime: Runtime = runtime_cls(
        config=config,
        event_stream=event_stream,
        sid=session_id,
        plugins=agent_cls.sandbox_plugins,
        headless_mode=headless_mode,
    )

    logger.debug(
        f'已创建运行时，包含插件: {[plugin.name for plugin in runtime.plugins]}'
    )

    return runtime


def initialize_repository_for_runtime(
    runtime: Runtime, selected_repository: str | None = None
) -> str | None:
    """
    为运行时初始化 Repository。

    Args:
        runtime: 要为其初始化 Repository 的运行时对象
        selected_repository: 可选的 GitHub Repository 地址

    Returns:
        如果克隆了 Repository，返回 Repository 目录路径，否则返回 None。
    """
    # 如果提供了选定的 Repository，则克隆它
    provider_tokens = {}
    
    # 从环境变量中获取 GitHub token
    if 'GITHUB_TOKEN' in os.environ:
        github_token = SecretStr(os.environ['GITHUB_TOKEN'])
        provider_tokens[ProviderType.GITHUB] = ProviderToken(token=github_token)

    # 从环境变量中获取 GitLab token
    if 'GITLAB_TOKEN' in os.environ:
        gitlab_token = SecretStr(os.environ['GITLAB_TOKEN'])
        provider_tokens[ProviderType.GITLAB] = ProviderToken(token=gitlab_token)

    # 从环境变量中获取 Bitbucket token
    if 'BITBUCKET_TOKEN' in os.environ:
        bitbucket_token = SecretStr(os.environ['BITBUCKET_TOKEN'])
        provider_tokens[ProviderType.BITBUCKET] = ProviderToken(token=bitbucket_token)

    # 创建密钥存储对象
    secret_store = (
        UserSecrets(provider_tokens=provider_tokens) if provider_tokens else None  # type: ignore[arg-type]
    )
    immutable_provider_tokens = secret_store.provider_tokens if secret_store else None

    logger.debug(f'选定的 Repository {selected_repository}.')
    
    # 异步克隆或初始化 Repository
    repo_directory = call_async_from_sync(
        runtime.clone_or_init_repo,
        GENERAL_TIMEOUT,
        immutable_provider_tokens,
        selected_repository,
        None,
    )
    
    # 如果存在设置脚本，则运行它
    runtime.maybe_run_setup_script()
    
    # 如果存在 pre-commit.sh，则设置 git hooks
    runtime.maybe_setup_git_hooks()

    return repo_directory


def create_memory(
    runtime: Runtime,
    event_stream: EventStream,
    sid: str,
    selected_repository: str | None = None,
    repo_directory: str | None = None,
    status_callback: Callable | None = None,
    conversation_instructions: str | None = None,
) -> Memory:
    """
    为 Agent 创建 Memory 对象。

    Args:
        runtime: 要使用的运行时对象
        event_stream: Memory 将订阅的事件流
        sid: Session ID
        selected_repository: 要克隆并开始使用的 Repository（如果有）
        repo_directory: Repository 目录（如果有）
        status_callback: 可选的回调函数，用于处理状态更新
        conversation_instructions: 可选的传递给 Agent 的指令
        
    Returns:
        创建的 Memory 实例
    """
    # 创建 Memory 实例
    memory = Memory(
        event_stream=event_stream,
        sid=sid,
        status_callback=status_callback,
    )

    # 设置对话指令
    memory.set_conversation_instructions(conversation_instructions)

    if runtime:
        # 设置可用的主机
        memory.set_runtime_info(runtime, {})

        # 从 repo/.openhands/microagents 加载 microagents
        microagents: list[BaseMicroagent] = runtime.get_microagents_from_selected_repo(
            selected_repository
        )
        memory.load_user_workspace_microagents(microagents)

        # 如果有选定的 Repository 和目录，设置 Repository 信息
        if selected_repository and repo_directory:
            memory.set_repository_info(selected_repository, repo_directory)

    return memory


def create_agent(config: OpenHandsConfig) -> Agent:
    """
    创建 Agent 实例。
    
    Args:
        config: OpenHands 配置对象
        
    Returns:
        创建的 Agent 实例
    """
    # 获取 Agent 类
    agent_cls: type[Agent] = Agent.get_cls(config.default_agent)
    
    # 获取 Agent 配置
    agent_config = config.get_agent_config(config.default_agent)
    
    # 获取 LLM 配置
    llm_config = config.get_llm_config_from_agent(config.default_agent)

    # 创建 Agent 实例
    agent = agent_cls(
        llm=LLM(config=llm_config),
        config=agent_config,
    )

    return agent


def create_controller(
    agent: Agent,
    runtime: Runtime,
    config: OpenHandsConfig,
    headless_mode: bool = True,
    replay_events: list[Event] | None = None,
) -> tuple[AgentController, State | None]:
    """
    创建 Agent 控制器。
    
    Args:
        agent: Agent 实例
        runtime: Runtime 实例
        config: OpenHands 配置对象
        headless_mode: 是否在无头模式下运行
        replay_events: 可选的重放事件列表
        
    Returns:
        包含 AgentController 和初始 State 的元组
    """
    # 获取事件流
    event_stream = runtime.event_stream
    initial_state = None
    
    try:
        logger.debug(
            f'尝试从 Session {event_stream.sid} 恢复 Agent State（如果可用）'
        )
        # 尝试从 Session 恢复初始状态
        initial_state = State.restore_from_session(
            event_stream.sid, event_stream.file_store
        )
    except Exception as e:
        logger.debug(f'无法恢复 Agent State: {e}')

    # 创建控制器
    controller = AgentController(
        agent=agent,
        iteration_delta=config.max_iterations,
        budget_per_task_delta=config.max_budget_per_task,
        agent_to_llm_config=config.get_agent_to_llm_config_map(),
        event_stream=event_stream,
        initial_state=initial_state,
        headless_mode=headless_mode,
        confirmation_mode=config.security.confirmation_mode,
        replay_events=replay_events,
    )
    return (controller, initial_state)


def generate_sid(config: OpenHandsConfig, session_name: str | None = None) -> str:
    """
    基于 Session 名称和 JWT 密钥生成 Session ID。
    
    Args:
        config: OpenHands 配置对象
        session_name: 可选的 Session 名称
        
    Returns:
        生成的 Session ID 字符串
    """
    # 如果没有提供 session_name，则生成一个随机的 UUID
    session_name = session_name or str(uuid.uuid4())
    jwt_secret = config.jwt_secret

    # 使用 SHA256 生成哈希值
    hash_str = hashlib.sha256(f'{session_name}{jwt_secret}'.encode('utf-8')).hexdigest()
    
    # 返回格式化的 Session ID
    return f'{session_name}-{hash_str[:16]}'