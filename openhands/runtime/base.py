import asyncio
import atexit
import copy
import json
import os
import random
import shutil
import string
import tempfile
from abc import abstractmethod
from pathlib import Path
from types import MappingProxyType
from typing import Callable, cast
from zipfile import ZipFile

import httpx

from openhands.core.config import OpenHandsConfig, SandboxConfig
from openhands.core.config.mcp_config import MCPConfig, MCPStdioServerConfig
from openhands.core.exceptions import AgentRuntimeDisconnectedError
from openhands.core.logger import openhands_logger as logger
from openhands.events import EventSource, EventStream, EventStreamSubscriber
from openhands.events.action import (
    Action,
    ActionConfirmationStatus,
    AgentThinkAction,
    BrowseInteractiveAction,
    BrowseURLAction,
    CmdRunAction,
    FileEditAction,
    FileReadAction,
    FileWriteAction,
    IPythonRunCellAction,
)
from openhands.events.action.mcp import MCPAction
from openhands.events.event import Event
from openhands.events.observation import (
    AgentThinkObservation,
    CmdOutputObservation,
    ErrorObservation,
    FileReadObservation,
    NullObservation,
    Observation,
    UserRejectObservation,
)
from openhands.events.serialization.action import ACTION_TYPE_TO_CLASS
from openhands.integrations.provider import (
    PROVIDER_TOKEN_TYPE,
    ProviderHandler,
    ProviderType,
)
from openhands.integrations.service_types import AuthenticationError
from openhands.microagent import (
    BaseMicroagent,
    load_microagents_from_dir,
)
from openhands.runtime.plugins import (
    JupyterRequirement,
    PluginRequirement,
    VSCodeRequirement,
)
from openhands.runtime.runtime_status import RuntimeStatus
from openhands.runtime.utils.edit import FileEditRuntimeMixin
from openhands.runtime.utils.git_handler import CommandResult, GitHandler
from openhands.utils.async_utils import (
    GENERAL_TIMEOUT,
    call_async_from_sync,
    call_sync_from_async,
)


def _default_env_vars(sandbox_config: SandboxConfig) -> dict[str, str]:
    """
    获取默认的环境变量配置。

    从系统环境变量中提取以'SANDBOX_ENV_'为前缀的变量，
    并根据sandbox配置设置相关的环境变量。

    Args:
        sandbox_config: Sandbox配置对象

    Returns:
        dict[str, str]: 包含默认环境变量的字典
    """
    ret = {}
    # 遍历所有系统环境变量
    for key in os.environ:
        # 检查是否以'SANDBOX_ENV_'开头
        if key.startswith('SANDBOX_ENV_'):
            # 移除前缀，获得在sandbox中的环境变量名
            sandbox_key = key.removeprefix('SANDBOX_ENV_')
            ret[sandbox_key] = os.environ[key]

    # 如果启用了自动代码检查，设置相应的环境变量
    if sandbox_config.enable_auto_lint:
        ret['ENABLE_AUTO_LINT'] = 'true'
    return ret


class Runtime(FileEditRuntimeMixin):
    """
    Agent运行时环境的抽象基类。

    这是OpenHands中的一个扩展点，允许应用程序自定义Agent与外部环境的交互方式。
    Runtime提供了一个包含以下功能的sandbox：
    - Bash shell访问
    - 浏览器交互
    - 文件系统操作
    - Git操作
    - 环境变量管理

    应用程序可以通过以下方式替换自己的实现：
    1. 创建一个继承自Runtime的类
    2. 实现所有必需的方法
    3. 在配置中设置runtime名称或使用get_runtime_cls()

    该类通过get_runtime_cls()中的get_impl()进行实例化。

    内置实现包括：
    - DockerRuntime: 使用Docker的容器化环境
    - RemoteRuntime: 远程执行环境
    - LocalRuntime: 用于开发的本地执行
    - KubernetesRuntime: 基于Kubernetes的执行环境
    - CLIRuntime: 命令行界面runtime

    Args:
        config: OpenHands配置对象
        event_stream: 事件流对象，用于处理事件的发布和订阅
        sid: 唯一标识当前用户会话的Session ID
        plugins: 插件需求列表，可选
        env_vars: 环境变量字典，可选
        status_callback: 状态回调函数，可选
        attach_to_existing: 是否附加到现有的runtime实例
        headless_mode: 是否在无头模式下运行
        user_id: 用户ID，可选
        git_provider_tokens: Git提供商的token，可选
    """

    # 类成员变量定义
    sid: str  # Session ID，用于标识当前会话
    config: OpenHandsConfig  # OpenHands配置对象
    initial_env_vars: dict[str, str]  # 初始环境变量字典
    attach_to_existing: bool  # 是否附加到现有实例的标志
    status_callback: Callable[[str, str, str], None] | None  # 状态回调函数
    runtime_status: RuntimeStatus | None  # Runtime状态
    _runtime_initialized: bool = False  # Runtime是否已初始化的私有标志

    def __init__(
            self,
            config: OpenHandsConfig,
            event_stream: EventStream,
            sid: str = 'default',
            plugins: list[PluginRequirement] | None = None,
            env_vars: dict[str, str] | None = None,
            status_callback: Callable[[str, str, str], None] | None = None,
            attach_to_existing: bool = False,
            headless_mode: bool = False,
            user_id: str | None = None,
            git_provider_tokens: PROVIDER_TOKEN_TYPE | None = None,
    ):
        """
        初始化Runtime实例。

        Args:
            config: OpenHands配置对象
            event_stream: 事件流，用于处理事件的发布和订阅
            sid: Session ID，默认为'default'
            plugins: 插件需求列表，可选
            env_vars: 额外的环境变量，可选
            status_callback: 状态变化时的回调函数，可选
            attach_to_existing: 是否附加到现有的runtime实例，默认False
            headless_mode: 是否在无头模式下运行，默认False
            user_id: 用户ID，可选
            git_provider_tokens: Git提供商的认证token，可选
        """
        # 初始化Git处理器，用于处理Git相关操作
        self.git_handler = GitHandler(
            execute_shell_fn=self._execute_shell_fn_git_handler
        )

        # 设置基本属性
        self.sid = sid
        self.event_stream = event_stream

        # 如果event_stream存在，订阅Runtime相关事件
        if event_stream:
            event_stream.subscribe(
                EventStreamSubscriber.RUNTIME, self.on_event, self.sid
            )

        # 深拷贝插件列表以避免修改原始列表
        self.plugins = (
            copy.deepcopy(plugins) if plugins is not None and len(plugins) > 0 else []
        )

        # 如果不是无头模式，添加VSCode插件
        if not headless_mode:
            self.plugins.append(VSCodeRequirement())

        # 设置状态回调和附加标志
        self.status_callback = status_callback
        self.attach_to_existing = attach_to_existing

        # 深拷贝配置以避免修改原始配置
        self.config = copy.deepcopy(config)
        # 注册程序退出时的清理函数
        atexit.register(self.close)

        # 获取默认环境变量并添加额外的环境变量
        self.initial_env_vars = _default_env_vars(config.sandbox)
        if env_vars is not None:
            self.initial_env_vars.update(env_vars)

        # 初始化提供商处理器，用于处理Git提供商的认证
        self.provider_handler = ProviderHandler(
            provider_tokens=git_provider_tokens
                            or cast(PROVIDER_TOKEN_TYPE, MappingProxyType({})),
            external_auth_id=user_id,
            external_token_manager=True,
        )

        # 异步获取提供商环境变量并更新到初始环境变量中
        raw_env_vars: dict[str, str] = call_async_from_sync(
            self.provider_handler.get_env_vars, GENERAL_TIMEOUT, True, None, False
        )
        self.initial_env_vars.update(raw_env_vars)

        # 检查是否启用VSCode插件
        self._vscode_enabled = any(
            isinstance(plugin, VSCodeRequirement) for plugin in self.plugins
        )

        # 初始化文件编辑混入类
        FileEditRuntimeMixin.__init__(
            self, enable_llm_editor=config.get_agent_config().enable_llm_editor
        )

        # 设置其他属性
        self.user_id = user_id
        self.git_provider_tokens = git_provider_tokens
        self.runtime_status = None

    @property
    def runtime_initialized(self) -> bool:
        """
        获取Runtime是否已初始化的状态。

        Returns:
            bool: Runtime是否已初始化
        """
        return self._runtime_initialized

    def setup_initial_env(self) -> None:
        """
        设置初始环境变量。

        如果附加到现有实例，则跳过环境变量设置。
        否则，添加初始环境变量和启动时环境变量。
        """
        # 如果是附加到现有实例，跳过环境变量设置
        if self.attach_to_existing:
            return

        # 记录要添加的环境变量
        logger.debug(f'Adding env vars: {self.initial_env_vars.keys()}')
        # 添加初始环境变量
        self.add_env_vars(self.initial_env_vars)

        # 如果配置了启动时环境变量，也添加进去
        if self.config.sandbox.runtime_startup_env_vars:
            self.add_env_vars(self.config.sandbox.runtime_startup_env_vars)

    def close(self) -> None:
        """
        关闭Runtime实例。

        这应该只由对话管理器或关闭会话时调用。
        如果被错误处理等调用，可能会阻止恢复。
        """
        pass

    @classmethod
    async def delete(cls, conversation_id: str) -> None:
        """
        异步删除指定对话的Runtime实例。

        Args:
            conversation_id: 要删除的对话ID
        """
        pass

    def log(self, level: str, message: str) -> None:
        """
        记录日志消息。

        Args:
            level: 日志级别（如'info', 'error', 'debug'等）
            message: 日志消息内容
        """
        # 在消息前加上runtime标识
        message = f'[runtime {self.sid}] {message}'
        # 使用指定级别记录日志
        getattr(logger, level)(message, stacklevel=2)

    def set_runtime_status(self, runtime_status: RuntimeStatus):
        """
        设置Runtime状态并通过回调函数发送状态消息。

        Args:
            runtime_status: 新的Runtime状态
        """
        self.runtime_status = runtime_status
        # 如果有状态回调函数，发送状态消息
        if self.status_callback:
            msg_id: str = runtime_status.value  # type: ignore
            self.status_callback('info', msg_id, runtime_status.message)

    def send_error_message(self, message_id: str, message: str):
        """
        发送错误消息。

        Args:
            message_id: 消息ID
            message: 错误消息内容
        """
        if self.status_callback:
            self.status_callback('error', message_id, message)

    # ====================================================================

    def add_env_vars(self, env_vars: dict[str, str]) -> None:
        """
        添加环境变量到Runtime中。

        支持多种环境（IPython、Windows PowerShell、Unix bash），
        并确保环境变量在会话中持久化。

        Args:
            env_vars: 要添加的环境变量字典
        """
        # 将所有环境变量键转换为大写
        env_vars = {key.upper(): value for key, value in env_vars.items()}

        # 如果使用Jupyter插件，将环境变量添加到IPython shell中
        if any(isinstance(plugin, JupyterRequirement) for plugin in self.plugins):
            code = 'import os\n'
            for key, value in env_vars.items():
                # 使用json.dumps进行安全的字符串转义
                code += f'os.environ["{key}"] = {json.dumps(value)}\n'
            code += '\n'
            self.run_ipython(IPythonRunCellAction(code))
            # 不记录变量值，因为它们可能包含敏感信息
            logger.debug('Added env vars to IPython')

        # 检查是否在Windows系统上
        import os
        import sys

        is_windows = os.name == 'nt' or sys.platform == 'win32'

        if is_windows:
            # 在Windows上使用PowerShell命令添加环境变量
            cmd = ''
            for key, value in env_vars.items():
                # 使用PowerShell的$env:语法设置环境变量
                # json.dumps提供了安全的字符串转义
                cmd += f'$env:{key} = {json.dumps(value)}; '

            if not cmd:
                return

            cmd = cmd.strip()
            logger.debug('Adding env vars to PowerShell')  # 不记录具体值

            obs = self.run(CmdRunAction(cmd))
            if not isinstance(obs, CmdOutputObservation) or obs.exit_code != 0:
                raise RuntimeError(
                    f'Failed to add env vars [{env_vars.keys()}] to environment: {obs.content}'
                )

            # 在Windows上不添加到profile持久化，因为这比较复杂
            # 并且在不同的PowerShell版本之间有所不同
            logger.debug(f'Added env vars to PowerShell session: {env_vars.keys()}')

        else:
            # Unix系统的bash实现
            cmd = ''
            bashrc_cmd = ''
            for key, value in env_vars.items():
                # 使用json.dumps进行安全的字符串转义
                cmd += f'export {key}={json.dumps(value)}; '
                # 如果.bashrc中不存在该变量，则添加到.bashrc中
                bashrc_cmd += f'grep -q "^export {key}=" ~/.bashrc || echo "export {key}={json.dumps(value)}" >> ~/.bashrc; '

            if not cmd:
                return

            cmd = cmd.strip()
            logger.debug('Adding env vars to bash')  # 不记录具体值

            obs = self.run(CmdRunAction(cmd))
            if not isinstance(obs, CmdOutputObservation) or obs.exit_code != 0:
                raise RuntimeError(
                    f'Failed to add env vars [{env_vars.keys()}] to environment: {obs.content}'
                )

            # 添加到.bashrc以实现持久化
            bashrc_cmd = bashrc_cmd.strip()
            logger.debug(f'Adding env var to .bashrc: {env_vars.keys()}')
            obs = self.run(CmdRunAction(bashrc_cmd))
            if not isinstance(obs, CmdOutputObservation) or obs.exit_code != 0:
                raise RuntimeError(
                    f'Failed to add env vars [{env_vars.keys()}] to .bashrc: {obs.content}'
                )

    def on_event(self, event: Event) -> None:
        """
        处理事件流中的事件。

        Args:
            event: 要处理的事件对象
        """
        # 如果事件是Action类型，异步处理该Action
        if isinstance(event, Action):
            asyncio.get_event_loop().run_until_complete(self._handle_action(event))

    async def _export_latest_git_provider_tokens(self, event: Action) -> None:
        """
        当Agent尝试执行包含provider token引用的action时，
        刷新runtime中的provider tokens。

        Args:
            event: 要检查的Action事件
        """
        # 如果没有用户ID，直接返回
        if not self.user_id:
            return

        # 检查action是否调用了provider token
        providers_called = ProviderHandler.check_cmd_action_for_provider_token_ref(
            event
        )

        if not providers_called:
            return

        logger.info(f'Fetching latest provider tokens for runtime: {self.sid}')
        # 获取最新的环境变量
        env_vars = await self.provider_handler.get_env_vars(
            providers=providers_called, expose_secrets=False, get_latest=True
        )

        if len(env_vars) == 0:
            return

        try:
            # 如果有事件流，设置事件流密钥
            if self.event_stream:
                await self.provider_handler.set_event_stream_secrets(
                    self.event_stream, env_vars=env_vars
                )
            # 添加环境变量到runtime中
            self.add_env_vars(self.provider_handler.expose_env_vars(env_vars))
        except Exception as e:
            logger.warning(
                f'Failed export latest github token to runtime: {self.sid}, {e}'
            )

    async def _handle_action(self, event: Action) -> None:
        """
        异步处理Action事件。

        Args:
            event: 要处理的Action事件
        """
        # 如果事件没有设置超时时间，使用默认超时时间
        if event.timeout is None:
            # 对于默认超时的action，我们不阻塞命令执行
            event.set_hard_timeout(self.config.sandbox.timeout, blocking=False)

        assert event.timeout is not None
        try:
            # 导出最新的git provider tokens
            await self._export_latest_git_provider_tokens(event)

            # 根据事件类型执行相应的操作
            if isinstance(event, MCPAction):
                observation: Observation = await self.call_tool_mcp(event)
            else:
                observation = await call_sync_from_async(self.run_action, event)
        except Exception as e:
            err_id = ''
            # 检查是否是网络错误或runtime断开错误
            if isinstance(e, httpx.NetworkError) or isinstance(
                    e, AgentRuntimeDisconnectedError
            ):
                err_id = 'STATUS$ERROR_RUNTIME_DISCONNECTED'

            error_message = f'{type(e).__name__}: {str(e)}'
            self.log('error', f'Unexpected error while running action: {error_message}')
            self.log('error', f'Problematic action: {str(event)}')
            self.send_error_message(err_id, error_message)
            return

        # 设置observation的cause为事件ID
        observation._cause = event.id  # type: ignore[attr-defined]
        observation.tool_call_metadata = event.tool_call_metadata

        # 设置事件源，如果事件有源则使用，否则使用AGENT
        source = event.source if event.source else EventSource.AGENT

        # 如果是NullObservation，不添加到事件流中
        if isinstance(observation, NullObservation):
            return

        # 将observation添加到事件流中
        self.event_stream.add_event(observation, source)  # type: ignore[arg-type]

    async def clone_or_init_repo(
            self,
            git_provider_tokens: PROVIDER_TOKEN_TYPE | None,
            selected_repository: str | None,
            selected_branch: str | None,
    ) -> str:
        """
        克隆或初始化代码仓库。

        如果没有选择仓库，则初始化一个新的git仓库。
        如果选择了仓库，则克隆该仓库到工作空间。

        Args:
            git_provider_tokens: Git提供商的认证token
            selected_repository: 选择的仓库名称
            selected_branch: 选择的分支名称

        Returns:
            str: 克隆的目录名称，如果没有克隆则返回空字符串
        """
        if not selected_repository:
            # 在SaaS模式下（通过user_id存在来判断），总是运行git init
            # 在OSS模式下，只有当workspace_base未设置时才运行git init
            if self.user_id or not self.config.workspace_base:
                logger.debug(
                    'No repository selected. Initializing a new git repository in the workspace.'
                )
                # 没有选择仓库。在工作空间中初始化一个新的git仓库。
                action = CmdRunAction(
                    command=f'git init && git config --global --add safe.directory {self.workspace_root}'
                )
                self.run_action(action)
            else:
                logger.info(
                    'In workspace mount mode, not initializing a new git repository.'
                )
                # 在工作空间挂载模式下，不初始化新的git仓库。
            return ''

        # 获取认证后的Git URL
        remote_repo_url = await self._get_authenticated_git_url(
            selected_repository, git_provider_tokens
        )

        if not remote_repo_url:
            raise ValueError('Missing either Git token or valid repository')
            # 缺少Git token或有效的仓库

        # 如果有状态回调，发送设置工作空间的状态
        if self.status_callback:
            self.status_callback(
                'info', 'STATUS$SETTING_UP_WORKSPACE', 'Setting up workspace...'
            )
            # 设置工作空间...

        # 获取目录名称
        dir_name = selected_repository.split('/')[-1]

        # 生成随机分支名称以避免冲突
        random_str = ''.join(
            random.choices(string.ascii_lowercase + string.digits, k=8)
        )
        openhands_workspace_branch = f'openhands-workspace-{random_str}'

        # 克隆仓库命令
        clone_command = f'git clone {remote_repo_url} {dir_name}'

        # 切换到适当的分支
        checkout_command = (
            f'git checkout {selected_branch}'
            if selected_branch
            else f'git checkout -b {openhands_workspace_branch}'
        )

        # 执行克隆操作
        clone_action = CmdRunAction(command=clone_command)
        self.run_action(clone_action)

        # 切换到目录并检出分支
        cd_checkout_action = CmdRunAction(
            command=f'cd {dir_name} && {checkout_command}'
        )
        action = cd_checkout_action
        self.log('info', f'Cloning repo: {selected_repository}')
        self.run_action(action)
        return dir_name

    def maybe_run_setup_script(self):
        """
        如果工作空间或仓库中存在.openhands/setup.sh，则运行它。

        这个方法用于执行项目特定的设置脚本。
        """
        setup_script = '.openhands/setup.sh'
        # 尝试读取设置脚本
        read_obs = self.read(FileReadAction(path=setup_script))
        if isinstance(read_obs, ErrorObservation):
            # 如果读取失败，直接返回
            return

        # 如果有状态回调，发送设置工作空间的状态
        if self.status_callback:
            self.status_callback(
                'info', 'STATUS$SETTING_UP_WORKSPACE', 'Setting up workspace...'
            )
            # 设置工作空间...

        # 设置脚本超时时间为10分钟
        action = CmdRunAction(
            f'chmod +x {setup_script} && source {setup_script}',
            blocking=True,
            hidden=True,
        )
        action.set_hard_timeout(600)

        # 将action作为ENVIRONMENT事件添加到事件流中
        source = EventSource.ENVIRONMENT
        self.event_stream.add_event(action, source)

        # 执行action
        self.run_action(action)

    @property
    def workspace_root(self) -> Path:
        """
        返回工作空间根路径。

        Returns:
            Path: 工作空间根路径对象
        """
        return Path(self.config.workspace_mount_path_in_sandbox)

    def maybe_setup_git_hooks(self):
        """
        如果工作空间或仓库中存在.openhands/pre-commit.sh，则设置git hooks。

        这个方法用于设置项目特定的Git钩子。
        """
        pre_commit_script = '.openhands/pre-commit.sh'
        # 尝试读取pre-commit脚本
        read_obs = self.read(FileReadAction(path=pre_commit_script))
        if isinstance(read_obs, ErrorObservation):
            # 如果读取失败，直接返回
            return

        # 如果有状态回调，发送设置git hooks的状态
        if self.status_callback:
            self.status_callback(
                'info', 'STATUS$SETTING_UP_GIT_HOOKS', 'Setting up git hooks...'
            )
            # 设置git hooks...

        # 确保git hooks目录存在
        action = CmdRunAction('mkdir -p .git/hooks')
        obs = self.run_action(action)
        if isinstance(obs, CmdOutputObservation) and obs.exit_code != 0:
            self.log('error', f'Failed to create git hooks directory: {obs.content}')
            return

        # 使pre-commit脚本可执行
        action = CmdRunAction(f'chmod +x {pre_commit_script}')
        obs = self.run_action(action)
        if isinstance(obs, CmdOutputObservation) and obs.exit_code != 0:
            self.log(
                'error', f'Failed to make pre-commit script executable: {obs.content}'
            )
            return

        # 检查是否存在现有的pre-commit hook
        pre_commit_hook = '.git/hooks/pre-commit'
        pre_commit_local = '.git/hooks/pre-commit.local'

        # 读取现有的pre-commit hook（如果存在）
        read_obs = self.read(FileReadAction(path=pre_commit_hook))
        if not isinstance(read_obs, ErrorObservation):
            # 如果现有的hook不是由OpenHands创建的，保留它
            if 'This hook was installed by OpenHands' not in read_obs.content:
                # 这个hook是由OpenHands安装的
                self.log('info', 'Preserving existing pre-commit hook')
                # 保留现有的pre-commit hook

                # 将现有的hook移动到pre-commit.local
                action = CmdRunAction(f'mv {pre_commit_hook} {pre_commit_local}')
                obs = self.run_action(action)
                if isinstance(obs, CmdOutputObservation) and obs.exit_code != 0:
                    self.log(
                        'error',
                        f'Failed to preserve existing pre-commit hook: {obs.content}',
                    )
                    return

                # 使其可执行
                action = CmdRunAction(f'chmod +x {pre_commit_local}')
                obs = self.run_action(action)
                if isinstance(obs, CmdOutputObservation) and obs.exit_code != 0:
                    self.log(
                        'error',
                        f'Failed to make preserved hook executable: {obs.content}',
                    )
                    return

        # 创建调用我们脚本的pre-commit hook
        pre_commit_hook_content = f"""#!/bin/bash
# This hook was installed by OpenHands
# It calls the pre-commit script in the .openhands directory

if [ -x "{pre_commit_script}" ]; then
    source "{pre_commit_script}"
    exit $?
else
    echo "Warning: {pre_commit_script} not found or not executable"
    exit 0
fi
"""
        # 这个hook是由OpenHands安装的
        # 它调用.openhands目录中的pre-commit脚本
        # 警告：找不到或不可执行

        # 写入pre-commit hook
        write_obs = self.write(
            FileWriteAction(path=pre_commit_hook, content=pre_commit_hook_content)
        )
        if isinstance(write_obs, ErrorObservation):
            self.log('error', f'Failed to write pre-commit hook: {write_obs.content}')
            return

        # 使pre-commit hook可执行
        action = CmdRunAction(f'chmod +x {pre_commit_hook}')
        obs = self.run_action(action)
        if isinstance(obs, CmdOutputObservation) and obs.exit_code != 0:
            self.log(
                'error', f'Failed to make pre-commit hook executable: {obs.content}'
            )
            return

        self.log('info', 'Git pre-commit hook installed successfully')
        # Git pre-commit hook安装成功

    def _load_microagents_from_directory(
            self, microagents_dir: Path, source_description: str
    ) -> list[BaseMicroagent]:
        """
        从目录中加载microagents。

        Args:
            microagents_dir: 包含microagents的目录路径
            source_description: 用于记录日志的源描述

        Returns:
            list[BaseMicroagent]: 已加载的microagents列表
        """
        loaded_microagents: list[BaseMicroagent] = []

        self.log(
            'info',
            f'Attempting to list files in {source_description} microagents directory: {microagents_dir}',
        )
        # 尝试列出{source_description} microagents目录中的文件

        # 列出目录中的文件
        files = self.list_files(str(microagents_dir))

        if not files:
            self.log(
                'warning',
                f'No files found in {source_description} microagents directory: {microagents_dir}',
            )
            # 在{source_description} microagents目录中没有找到文件
            return loaded_microagents

        self.log(
            'info',
            f'Found {len(files)} files in {source_description} microagents directory',
        )
        # 在{source_description} microagents目录中找到了{len(files)}个文件

        # 从sandbox中复制目录到本地
        zip_path = self.copy_from(str(microagents_dir))
        microagent_folder = tempfile.mkdtemp()

        try:
            # 解压缩文件
            with ZipFile(zip_path, 'r') as zip_file:
                zip_file.extractall(microagent_folder)

            # 删除临时zip文件
            zip_path.unlink()

            # 从目录中加载microagents
            repo_agents, knowledge_agents = load_microagents_from_dir(microagent_folder)

            self.log(
                'info',
                f'Loaded {len(repo_agents)} repo agents and {len(knowledge_agents)} knowledge agents from {source_description}',
            )
            # 从{source_description}加载了{len(repo_agents)}个repo agents和{len(knowledge_agents)}个knowledge agents

            # 添加到已加载的microagents列表中
            loaded_microagents.extend(repo_agents.values())
            loaded_microagents.extend(knowledge_agents.values())
        except Exception as e:
            self.log('error', f'Failed to load agents from {source_description}: {e}')
            # 从{source_description}加载agents失败
        finally:
            # 清理临时目录
            shutil.rmtree(microagent_folder)

        return loaded_microagents

    async def _get_authenticated_git_url(
            self, repo_name: str, git_provider_tokens: PROVIDER_TOKEN_TYPE | None
    ) -> str:
        """
        获取仓库的认证Git URL。

        Args:
            repo_name: 仓库名称（owner/repo）
            git_provider_tokens: Git提供商tokens

        Returns:
            str: 如果有认证凭据则返回认证Git URL，否则返回常规HTTPS URL
        """
        try:
            # 初始化提供商处理器
            provider_handler = ProviderHandler(
                git_provider_tokens or MappingProxyType({})
            )
            # 验证仓库提供商
            repository = await provider_handler.verify_repo_provider(repo_name)
        except AuthenticationError:
            raise Exception('Git provider authentication issue when getting remote URL')
            # 获取远程URL时的Git提供商认证问题

        provider = repository.git_provider
        repo_name = repository.full_name

        # 提供商域名映射
        provider_domains = {
            ProviderType.GITHUB: 'github.com',
            ProviderType.GITLAB: 'gitlab.com',
            ProviderType.BITBUCKET: 'bitbucket.org',
        }

        domain = provider_domains[provider]

        # 如果提供了git_provider_tokens，使用token中的host（如果可用）
        if git_provider_tokens and provider in git_provider_tokens:
            domain = git_provider_tokens[provider].host or domain

        # 如果有token，尝试使用token，否则使用公共URL
        if git_provider_tokens and provider in git_provider_tokens:
            git_token = git_provider_tokens[provider].token
            if git_token:
                token_value = git_token.get_secret_value()
                if provider == ProviderType.GITLAB:
                    # GitLab使用oauth2格式
                    remote_url = (
                        f'https://oauth2:{token_value}@{domain}/{repo_name}.git'
                    )
                elif provider == ProviderType.BITBUCKET:
                    # Bitbucket处理username:app_password格式
                    if ':' in token_value:
                        # App token格式：username:app_password
                        remote_url = f'https://{token_value}@{domain}/{repo_name}.git'
                    else:
                        # Access token格式：使用x-token-auth
                        remote_url = f'https://x-token-auth:{token_value}@{domain}/{repo_name}.git'
                else:
                    # GitHub
                    remote_url = f'https://{token_value}@{domain}/{repo_name}.git'
            else:
                remote_url = f'https://{domain}/{repo_name}.git'
        else:
            remote_url = f'https://{domain}/{repo_name}.git'

        return remote_url

    def _is_gitlab_repository(self, repo_name: str) -> bool:
        """
        检查仓库是否托管在GitLab上。

        Args:
            repo_name: 仓库名称（例如："gitlab.com/org/repo" 或 "org/repo"）

        Returns:
            bool: 如果仓库托管在GitLab上则返回True，否则返回False
        """
        try:
            # 初始化提供商处理器
            provider_handler = ProviderHandler(
                self.git_provider_tokens or MappingProxyType({})
            )
            # 验证仓库提供商
            repository = call_async_from_sync(
                provider_handler.verify_repo_provider,
                GENERAL_TIMEOUT,
                repo_name,
            )
            return repository.git_provider == ProviderType.GITLAB
        except Exception:
            # 如果无法确定提供商，假设它不是GitLab
            # 这是一个安全的回退，因为我们只会使用默认的.openhands
            return False

    def get_microagents_from_org_or_user(
            self, selected_repository: str
    ) -> list[BaseMicroagent]:
        """
        从组织或用户级别的仓库中加载microagents。

        例如，如果仓库是github.com/acme-co/api，这将检查
        github.com/acme-co/.openhands是否存在。如果存在，它将克隆它并从
        ./microagents/文件夹中加载microagents。

        对于GitLab仓库，它将使用openhands-config而不是.openhands，
        因为GitLab不支持以非字母数字字符开头的仓库名称。

        Args:
            selected_repository: 仓库路径（例如："github.com/acme-co/api"）

        Returns:
            list[BaseMicroagent]: 从org/user级别仓库加载的microagents列表
        """
        loaded_microagents: list[BaseMicroagent] = []

        self.log(
            'debug',
            f'Starting org-level microagent loading for repository: {selected_repository}',
        )
        # 开始为仓库加载org级别的microagent

        # 分割仓库路径
        repo_parts = selected_repository.split('/')

        if len(repo_parts) < 2:
            self.log(
                'warning',
                f'Repository path has insufficient parts ({len(repo_parts)} < 2), skipping org-level microagents',
            )
            # 仓库路径部分不足，跳过org级别的microagents
            return loaded_microagents

        # 提取域名和组织/用户名
        org_name = repo_parts[-2]
        self.log(
            'info',
            f'Extracted org/user name: {org_name}',
        )
        # 提取的org/user名称

        # 确定这是否是GitLab仓库
        is_gitlab = self._is_gitlab_repository(selected_repository)
        self.log(
            'debug',
            f'Repository type detection - is_gitlab: {is_gitlab}',
        )
        # 仓库类型检测

        # 对于GitLab，使用openhands-config（因为.openhands不是有效的仓库名称）
        # 对于其他提供商，使用.openhands
        if is_gitlab:
            org_openhands_repo = f'{org_name}/openhands-config'
        else:
            org_openhands_repo = f'{org_name}/.openhands'

        self.log(
            'info',
            f'Checking for org-level microagents at {org_openhands_repo}',
        )
        # 检查org级别的microagents

        # 尝试克隆org级别的仓库
        try:
            # 为org级别的仓库创建临时目录
            org_repo_dir = self.workspace_root / f'org_openhands_{org_name}'
            self.log(
                'debug',
                f'Creating temporary directory for org repo: {org_repo_dir}',
            )
            # 为org仓库创建临时目录

            # 获取认证URL并进行浅克隆（--depth 1）以提高效率
            try:
                remote_url = call_async_from_sync(
                    self._get_authenticated_git_url,
                    GENERAL_TIMEOUT,
                    org_openhands_repo,
                    self.git_provider_tokens,
                )
            except Exception as e:
                self.log(
                    'error',
                    f'Failed to get authenticated URL for {org_openhands_repo}: {str(e)}',
                )
                # 获取认证URL失败
                raise Exception(str(e))

            # 构建克隆命令
            clone_cmd = (
                f'GIT_TERMINAL_PROMPT=0 git clone --depth 1 {remote_url} {org_repo_dir}'
            )
            self.log(
                'info',
                'Executing clone command for org-level repo',
            )
            # 执行org级别仓库的克隆命令

            action = CmdRunAction(command=clone_cmd)
            obs = self.run_action(action)

            if isinstance(obs, CmdOutputObservation) and obs.exit_code == 0:
                self.log(
                    'info',
                    f'Successfully cloned org-level microagents from {org_openhands_repo}',
                )
                # 成功从org级别克隆microagents

                # 从org级别仓库加载microagents
                org_microagents_dir = org_repo_dir / 'microagents'
                self.log(
                    'info',
                    f'Looking for microagents in directory: {org_microagents_dir}',
                )
                # 在目录中查找microagents

                loaded_microagents = self._load_microagents_from_directory(
                    org_microagents_dir, 'org-level'
                )

                self.log(
                    'info',
                    f'Loaded {len(loaded_microagents)} microagents from org-level repository {org_openhands_repo}',
                )
                # 从org级别仓库加载了microagents

                # 清理org仓库目录
                action = CmdRunAction(f'rm -rf {org_repo_dir}')
                self.run_action(action)
            else:
                # 获取克隆错误信息
                clone_error_msg = (
                    obs.content
                    if isinstance(obs, CmdOutputObservation)
                    else 'Unknown error'
                )
                # 未知错误
                exit_code = (
                    obs.exit_code if isinstance(obs, CmdOutputObservation) else 'N/A'
                )
                self.log(
                    'info',
                    f'No org-level microagents found at {org_openhands_repo} (exit_code: {exit_code})',
                )
                # 在org级别没有找到microagents
                self.log(
                    'debug',
                    f'Clone command output: {clone_error_msg}',
                )
                # 克隆命令输出

        except Exception as e:
            self.log(
                'debug',
                f'Error loading org-level microagents from {org_openhands_repo}: {str(e)}',
            )
            # 从org级别加载microagents时出错

        return loaded_microagents

    def get_microagents_from_selected_repo(
            self, selected_repository: str | None
    ) -> list[BaseMicroagent]:
        """
        从选定的仓库中加载microagents。
        如果selected_repository为None，则从当前工作空间加载microagents。
        这是加载microagents的主要入口点。

        此方法还检查存储在仓库中的用户/组织级别的microagents。
        例如，如果仓库是github.com/acme-co/api，它还将检查
        github.com/acme-co/.openhands并从那里加载microagents（如果存在）。

        对于GitLab仓库，它将使用openhands-config而不是.openhands，
        因为GitLab不支持以非字母数字字符开头的仓库名称。

        Args:
            selected_repository: 选定的仓库路径，可选

        Returns:
            list[BaseMicroagent]: 加载的microagents列表
        """
        loaded_microagents: list[BaseMicroagent] = []
        microagents_dir = self.workspace_root / '.openhands' / 'microagents'
        repo_root = None

        # 如果选择了仓库，检查用户/组织级别的microagents
        if selected_repository:
            # 从组织/用户级别的仓库加载microagents
            org_microagents = self.get_microagents_from_org_or_user(selected_repository)
            loaded_microagents.extend(org_microagents)

            # 继续处理仓库特定的microagents
            repo_root = self.workspace_root / selected_repository.split('/')[-1]
            microagents_dir = repo_root / '.openhands' / 'microagents'

        self.log(
            'info',
            f'Selected repo: {selected_repository}, loading microagents from {microagents_dir} (inside runtime)',
        )
        # 选定的仓库，从目录中加载microagents（在runtime内部）

        # 遗留仓库指令
        # 检查遗留的.openhands_instructions文件
        obs = self.read(
            FileReadAction(path=str(self.workspace_root / '.openhands_instructions'))
        )
        if isinstance(obs, ErrorObservation) and repo_root is not None:
            # 如果在工作空间根目录中找不到指令文件，尝试从仓库根目录加载
            self.log(
                'debug',
                f'.openhands_instructions not present, trying to load from repository {microagents_dir=}',
            )
            # .openhands_instructions不存在，尝试从仓库加载
            obs = self.read(
                FileReadAction(path=str(repo_root / '.openhands_instructions'))
            )

        if isinstance(obs, FileReadObservation):
            self.log('info', 'openhands_instructions microagent loaded.')
            # openhands_instructions microagent已加载
            loaded_microagents.append(
                BaseMicroagent.load(
                    path='.openhands_instructions',
                    microagent_dir=None,
                    file_content=obs.content,
                )
            )

        # 从目录加载microagents
        repo_microagents = self._load_microagents_from_directory(
            microagents_dir, 'repository'
        )
        loaded_microagents.extend(repo_microagents)

        return loaded_microagents

    def run_action(self, action: Action) -> Observation:
        """
        运行action并返回结果observation。

        如果action在任何runtime中都不可运行，则返回NullObservation。
        如果action不被当前runtime支持，则返回ErrorObservation。

        Args:
            action: 要运行的Action对象

        Returns:
            Observation: 执行结果的observation
        """
        # 如果action不可运行
        if not action.runnable:
            if isinstance(action, AgentThinkAction):
                return AgentThinkObservation('Your thought has been logged.')
                # 您的想法已被记录。
            return NullObservation('')

        # 如果action等待确认
        if (
                hasattr(action, 'confirmation_state')
                and action.confirmation_state
                == ActionConfirmationStatus.AWAITING_CONFIRMATION
        ):
            return NullObservation('')

        # 获取action类型
        action_type = action.action  # type: ignore[attr-defined]

        # 检查action类型是否存在
        if action_type not in ACTION_TYPE_TO_CLASS:
            return ErrorObservation(f'Action {action_type} does not exist.')
            # Action {action_type}不存在。

        # 检查当前runtime是否支持该action
        if not hasattr(self, action_type):
            return ErrorObservation(
                f'Action {action_type} is not supported in the current runtime.'
            )
            # Action {action_type}在当前runtime中不受支持。

        # 如果action被用户拒绝
        if (
                getattr(action, 'confirmation_state', None)
                == ActionConfirmationStatus.REJECTED
        ):
            return UserRejectObservation(
                'Action has been rejected by the user! Waiting for further user input.'
            )
            # Action已被用户拒绝！等待进一步的用户输入。

        # 执行action
        observation = getattr(self, action_type)(action)
        return observation

    # ====================================================================
    # 上下文管理器
    # ====================================================================

    def __enter__(self) -> 'Runtime':
        """
        进入上下文管理器。

        Returns:
            Runtime: 当前Runtime实例
        """
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """
        退出上下文管理器。

        Args:
            exc_type: 异常类型
            exc_value: 异常值
            traceback: 异常追踪
        """
        self.close()

    @abstractmethod
    async def connect(self) -> None:
        """
        异步连接到Runtime。

        这是一个抽象方法，必须由子类实现。
        """
        pass

    @abstractmethod
    def get_mcp_config(
            self, extra_stdio_servers: list[MCPStdioServerConfig] | None = None
    ) -> MCPConfig:
        """
        获取MCP配置。

        Args:
            extra_stdio_servers: 额外的stdio服务器配置列表，可选

        Returns:
            MCPConfig: MCP配置对象
        """
        pass

    # ====================================================================
    # Action执行抽象方法
    # ====================================================================

    @abstractmethod
    def run(self, action: CmdRunAction) -> Observation:
        """
        运行命令行Action。

        Args:
            action: 要运行的CmdRunAction

        Returns:
            Observation: 执行结果
        """
        pass

    @abstractmethod
    def run_ipython(self, action: IPythonRunCellAction) -> Observation:
        """
        运行IPython代码单元格Action。

        Args:
            action: 要运行的IPythonRunCellAction

        Returns:
            Observation: 执行结果
        """
        pass

    @abstractmethod
    def read(self, action: FileReadAction) -> Observation:
        """
        读取文件Action。

        Args:
            action: 要执行的FileReadAction

        Returns:
            Observation: 读取结果
        """
        pass

    @abstractmethod
    def write(self, action: FileWriteAction) -> Observation:
        """
        写入文件Action。

        Args:
            action: 要执行的FileWriteAction

        Returns:
            Observation: 写入结果
        """
        pass

    @abstractmethod
    def edit(self, action: FileEditAction) -> Observation:
        """
        编辑文件Action。

        Args:
            action: 要执行的FileEditAction

        Returns:
            Observation: 编辑结果
        """
        pass

    @abstractmethod
    def browse(self, action: BrowseURLAction) -> Observation:
        """
        浏览URL Action。

        Args:
            action: 要执行的BrowseURLAction

        Returns:
            Observation: 浏览结果
        """
        pass

    @abstractmethod
    def browse_interactive(self, action: BrowseInteractiveAction) -> Observation:
        """
        交互式浏览Action。

        Args:
            action: 要执行的BrowseInteractiveAction

        Returns:
            Observation: 交互结果
        """
        pass

    @abstractmethod
    async def call_tool_mcp(self, action: MCPAction) -> Observation:
        """
        调用MCP工具Action。

        Args:
            action: 要执行的MCPAction

        Returns:
            Observation: 调用结果
        """
        pass

    # ====================================================================
    # 文件操作抽象方法
    # ====================================================================

    @abstractmethod
    def copy_to(self, host_src: str, sandbox_dest: str, recursive: bool = False):
        """
        将文件从主机复制到sandbox。

        Args:
            host_src: 主机源路径
            sandbox_dest: sandbox目标路径
            recursive: 是否递归复制，默认False

        Raises:
            NotImplementedError: 基类中未实现此方法
        """
        raise NotImplementedError('This method is not implemented in the base class.')
        # 此方法在基类中未实现。

    @abstractmethod
    def list_files(self, path: str | None = None) -> list[str]:
        """
        列出sandbox中的文件。

        如果path为None，则列出sandbox初始工作目录中的文件（例如：/workspace）。

        Args:
            path: 要列出文件的路径，可选

        Returns:
            list[str]: 文件列表

        Raises:
            NotImplementedError: 基类中未实现此方法
        """
        raise NotImplementedError('This method is not implemented in the base class.')
        # 此方法在基类中未实现。

    @abstractmethod
    def copy_from(self, path: str) -> Path:
        """
        压缩sandbox中的所有文件并返回本地文件系统中的路径。

        Args:
            path: 要复制的路径

        Returns:
            Path: 本地文件系统中的路径

        Raises:
            NotImplementedError: 基类中未实现此方法
        """
        raise NotImplementedError('This method is not implemented in the base class.')
        # 此方法在基类中未实现。

    # ====================================================================
    # 认证
    # ====================================================================

    @property
    def session_api_key(self) -> str | None:
        """
        获取会话API密钥。

        Returns:
            str | None: 会话API密钥，如果没有则返回None
        """
        return None

    # ====================================================================
    # VSCode
    # ====================================================================

    @property
    def vscode_enabled(self) -> bool:
        """
        检查VSCode是否启用。

        Returns:
            bool: VSCode是否启用
        """
        return self._vscode_enabled

    @property
    def vscode_url(self) -> str | None:
        """
        获取VSCode的URL。

        Returns:
            str | None: VSCode URL

        Raises:
            NotImplementedError: 基类中未实现此方法
        """
        raise NotImplementedError('This method is not implemented in the base class.')
        # 此方法在基类中未实现。

    @property
    def web_hosts(self) -> dict[str, int]:
        """
        获取Web主机映射。

        Returns:
            dict[str, int]: Web主机到端口的映射
        """
        return {}

    # ====================================================================
    # Git相关方法
    # ====================================================================

    def _execute_shell_fn_git_handler(
            self, command: str, cwd: str | None
    ) -> CommandResult:
        """
        GitHandler使用此函数执行shell命令。

        Args:
            command: 要执行的命令
            cwd: 当前工作目录，可选

        Returns:
            CommandResult: 命令执行结果
        """
        obs = self.run(CmdRunAction(command=command, is_static=True, cwd=cwd))
        exit_code = 0
        content = ''

        # 如果是错误观察，设置退出码为-1
        if isinstance(obs, ErrorObservation):
            exit_code = -1

        # 提取退出码和内容
        if hasattr(obs, 'exit_code'):
            exit_code = obs.exit_code
        if hasattr(obs, 'content'):
            content = obs.content

        return CommandResult(content=content, exit_code=exit_code)

    def get_git_changes(self, cwd: str) -> list[dict[str, str]] | None:
        """
        获取Git变更。

        Args:
            cwd: 当前工作目录

        Returns:
            list[dict[str, str]] | None: Git变更列表，如果没有则返回None
        """
        self.git_handler.set_cwd(cwd)
        return self.git_handler.get_git_changes()

    def get_git_diff(self, file_path: str, cwd: str) -> dict[str, str]:
        """
        获取文件的Git差异。

        Args:
            file_path: 文件路径
            cwd: 当前工作目录

        Returns:
            dict[str, str]: Git差异信息
        """
        self.git_handler.set_cwd(cwd)
        return self.git_handler.get_git_diff(file_path)

    @property
    def additional_agent_instructions(self) -> str:
        """
        获取额外的Agent指令。

        Returns:
            str: 额外的Agent指令，默认为空字符串
        """
        return ''

    def subscribe_to_shell_stream(
            self, callback: Callable[[str], None] | None = None
    ) -> bool:
        """
        订阅shell命令输出流。

        此方法旨在被希望将shell命令输出流式传输到外部消费者的
        runtime实现重写。

        Args:
            callback: 将在每行shell命令输出时调用的函数。
                     如果为None，则移除任何现有的订阅。

        Returns:
            bool: 默认返回False
        """
        return False
