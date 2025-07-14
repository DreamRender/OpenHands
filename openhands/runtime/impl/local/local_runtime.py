"""
该运行时直接在本地机器上运行 action_execution_server，不使用 Docker。

这是一个实验性功能，用于在受控环境中运行 OpenHands，
避免了 Docker 的复杂性和开销。
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

import httpx
import tenacity

import openhands
from openhands.core.config import OpenHandsConfig
from openhands.core.exceptions import AgentRuntimeDisconnectedError
from openhands.core.logger import openhands_logger as logger
from openhands.events import EventStream
from openhands.events.action import (
    Action,
)
from openhands.events.observation import (
    Observation,
)
from openhands.events.serialization import event_to_dict, observation_from_dict
from openhands.integrations.provider import PROVIDER_TOKEN_TYPE
from openhands.runtime.impl.action_execution.action_execution_client import (
    ActionExecutionClient,
)
from openhands.runtime.impl.docker.docker_runtime import (
    APP_PORT_RANGE_1,
    APP_PORT_RANGE_2,
    EXECUTION_SERVER_PORT_RANGE,
    VSCODE_PORT_RANGE,
)
from openhands.runtime.plugins import PluginRequirement
from openhands.runtime.runtime_status import RuntimeStatus
from openhands.runtime.utils import find_available_tcp_port
from openhands.runtime.utils.command import get_action_execution_server_startup_command
from openhands.utils.async_utils import call_sync_from_async
from openhands.utils.tenacity_stop import stop_if_should_exit


@dataclass
class ActionExecutionServerInfo:
    """
    关于正在运行的服务器进程的信息。
    
    该数据类存储了运行中的 action execution server 的所有相关信息，
    包括进程、端口、线程和工作空间等。
    
    Attributes:
        process (subprocess.Popen): 服务器进程对象
        execution_server_port (int): 执行服务器端口号
        vscode_port (int): VSCode 服务器端口号
        app_ports (list[int]): 应用端口列表
        log_thread (threading.Thread): 日志输出线程
        log_thread_exit_event (threading.Event): 日志线程退出事件
        temp_workspace (str | None): 临时工作空间路径，如果使用的话
        workspace_mount_path (str): 工作空间挂载路径
    """

    process: subprocess.Popen
    execution_server_port: int
    vscode_port: int
    app_ports: list[int]
    log_thread: threading.Thread
    log_thread_exit_event: threading.Event
    temp_workspace: str | None
    workspace_mount_path: str


# 全局字典，通过 Session ID 跟踪正在运行的服务器进程
_RUNNING_SERVERS: dict[str, ActionExecutionServerInfo] = {}


def get_user_info() -> tuple[int, str | None]:
    """
    以跨平台的方式获取用户 ID 和用户名。
    
    Returns:
        tuple[int, str | None]: 用户 ID 和用户名的元组
    """
    username = os.getenv('USER')
    if sys.platform == 'win32':
        # 在 Windows 上，我们不以相同的方式使用用户 ID
        # 返回一个不会导致问题的默认值
        return 1000, username
    else:
        # 在 Unix 系统上，使用 os.getuid()
        return os.getuid(), username


def check_dependencies(code_repo_path: str, check_browser: bool) -> None:
    """
    检查运行 LocalRuntime 所需的依赖项。
    
    Args:
        code_repo_path (str): 代码仓库路径
        check_browser (bool): 是否检查浏览器依赖
        
    Raises:
        ValueError: 如果依赖项检查失败
    """
    ERROR_MESSAGE = 'Please follow the instructions in https://github.com/All-Hands-AI/OpenHands/blob/main/Development.md to install OpenHands.'
    
    # 检查代码仓库路径是否存在
    if not os.path.exists(code_repo_path):
        raise ValueError(
            f'Code repo path {code_repo_path} does not exist. ' + ERROR_MESSAGE
        )
    
    # 检查 Jupyter 是否已安装
    logger.debug('Checking dependencies: Jupyter')
    output = subprocess.check_output(
        [sys.executable, '-m', 'jupyter', '--version'],
        text=True,
        cwd=code_repo_path,
    )
    logger.debug(f'Jupyter output: {output}')
    if 'jupyter' not in output.lower():
        raise ValueError('Jupyter is not properly installed. ' + ERROR_MESSAGE)

    # 检查 libtmux 是否已安装（在 Windows 上跳过）
    if sys.platform != 'win32':
        logger.debug('Checking dependencies: libtmux')
        import libtmux

        server = libtmux.Server()
        try:
            # 创建测试 session
            session = server.new_session(session_name='test-session')
        except Exception:
            raise ValueError('tmux is not properly installed or available on the path.')
        
        # 测试 tmux 功能
        pane = session.attached_pane
        pane.send_keys('echo "test"')
        pane_output = '\n'.join(pane.cmd('capture-pane', '-p').stdout)
        session.kill_session()
        if 'test' not in pane_output:
            raise ValueError('libtmux is not properly installed. ' + ERROR_MESSAGE)

    # 如果需要检查浏览器
    if check_browser:
        logger.debug('Checking dependencies: browser')
        from openhands.runtime.browser.browser_env import BrowserEnv

        browser = BrowserEnv()
        browser.close()


class LocalRuntime(ActionExecutionClient):
    """
    本地运行时实现，直接在本地机器上运行 action_execution_server。
    
    当接收到事件时，会通过 HTTP 将事件发送到服务器。
    这是一个实验性功能，不提供沙箱保护。

    Args:
        config (OpenHandsConfig): 应用配置对象
        event_stream (EventStream): 用于订阅的事件流
        sid (str, optional): Session ID。默认为 'default'
        plugins (list[PluginRequirement] | None, optional): 插件需求列表。默认为 None
        env_vars (dict[str, str] | None, optional): 要设置的环境变量。默认为 None
    """

    def __init__(
        self,
        config: OpenHandsConfig,
        event_stream: EventStream,
        sid: str = 'default',
        plugins: list[PluginRequirement] | None = None,
        env_vars: dict[str, str] | None = None,
        status_callback: Callable[[str, str, str], None] | None = None,
        attach_to_existing: bool = False,
        headless_mode: bool = True,
        user_id: str | None = None,
        git_provider_tokens: PROVIDER_TOKEN_TYPE | None = None,
    ) -> None:
        # 检查是否在 Windows 系统上运行
        self.is_windows = sys.platform == 'win32'
        if self.is_windows:
            logger.warning(
                'Running on Windows - some features that require tmux will be limited. '
                'For full functionality, please consider using WSL or Docker runtime.'
            )

        # 保存配置对象
        self.config = config
        # 获取用户信息
        self._user_id, self._username = get_user_info()

        logger.warning(
            'Initializing LocalRuntime. WARNING: NO SANDBOX IS USED. '
            'This is an experimental feature, please report issues to https://github.com/All-Hands-AI/OpenHands/issues. '
            '`run_as_openhands` will be ignored since the current user will be used to launch the server. '
            'We highly recommend using a sandbox (eg. DockerRuntime) unless you '
            'are running in a controlled environment.\n'
            f'User ID: {self._user_id}. '
            f'Username: {self._username}.'
        )

        # 初始化这些值，将在 connect() 方法中设置
        self._temp_workspace: str | None = None     # 临时工作空间路径
        self._execution_server_port = -1            # 执行服务器端口
        self._vscode_port = -1                      # VSCode 端口
        self._app_ports: list[int] = []             # 应用端口列表

        # 初始化 API URL
        self.api_url = (
            f'{self.config.sandbox.local_runtime_url}:{self._execution_server_port}'
        )
        # 状态回调函数
        self.status_callback = status_callback
        # 服务器进程对象
        self.server_process: subprocess.Popen[str] | None = None
        # 确保一次只执行一个动作的信号量
        self.action_semaphore = threading.Semaphore(1)
        # 日志线程退出事件
        self._log_thread_exit_event = threading.Event()

        # 更新环境变量
        if self.config.sandbox.runtime_startup_env_vars:
            os.environ.update(self.config.sandbox.runtime_startup_env_vars)

        # 初始化 action_execution_server
        super().__init__(
            config,
            event_stream,
            sid,
            plugins,
            env_vars,
            status_callback,
            attach_to_existing,
            headless_mode,
            user_id,
            git_provider_tokens,
        )

        # 如果环境中有 API 密钥，在请求运行时时使用
        session_api_key = os.getenv('SESSION_API_KEY')
        if session_api_key:
            self.session.headers['X-Session-API-Key'] = session_api_key

    @property
    def action_execution_server_url(self) -> str:
        """
        获取 Action 执行服务器的 URL。
        
        Returns:
            str: API URL
        """
        return self.api_url

    async def connect(self) -> None:
        """
        在本地机器上启动 action_execution_server 或连接到现有的服务器。
        
        该方法会检查是否已有运行中的服务器，如果没有则创建新的服务器进程。
        """
        self.set_runtime_status(RuntimeStatus.STARTING_RUNTIME)

        # 检查该 Session ID 是否已有运行中的服务器
        if self.sid in _RUNNING_SERVERS:
            self.log('info', f'Connecting to existing server for session {self.sid}')
            # 获取现有服务器信息
            server_info = _RUNNING_SERVERS[self.sid]
            self.server_process = server_info.process
            self._execution_server_port = server_info.execution_server_port
            self._log_thread = server_info.log_thread
            self._log_thread_exit_event = server_info.log_thread_exit_event
            self._vscode_port = server_info.vscode_port
            self._app_ports = server_info.app_ports
            self._temp_workspace = server_info.temp_workspace
            self.config.workspace_mount_path_in_sandbox = (
                server_info.workspace_mount_path
            )
            # 更新 API URL
            self.api_url = (
                f'{self.config.sandbox.local_runtime_url}:{self._execution_server_port}'
            )
        elif self.attach_to_existing:
            # 如果应该附加到现有服务器但找不到，抛出错误
            self.log('error', f'No existing server found for session {self.sid}')
            raise AgentRuntimeDisconnectedError(
                f'No existing server found for session {self.sid}'
            )
        else:
            # 设置工作空间目录
            if self.config.workspace_base is not None:
                logger.warning(
                    f'Workspace base path is set to {self.config.workspace_base}. '
                    'It will be used as the path for the agent to run in. '
                    'Be careful, the agent can EDIT files in this directory!'
                )
                # 使用配置的工作空间路径
                self.config.workspace_mount_path_in_sandbox = self.config.workspace_base
                self._temp_workspace = None
            else:
                # 为 Agent 创建临时目录
                logger.warning(
                    'Workspace base path is NOT set. Agent will run in a temporary directory.'
                )
                self._temp_workspace = tempfile.mkdtemp(
                    prefix=f'openhands_workspace_{self.sid}',
                )
                self.config.workspace_mount_path_in_sandbox = self._temp_workspace

            logger.info(
                f'Using workspace directory: {self.config.workspace_mount_path_in_sandbox}'
            )

            # 启动新服务器
            self._execution_server_port = self._find_available_port(
                EXECUTION_SERVER_PORT_RANGE
            )
            self._vscode_port = int(
                os.getenv('VSCODE_PORT')
                or str(self._find_available_port(VSCODE_PORT_RANGE))
            )
            self._app_ports = [
                int(
                    os.getenv('APP_PORT_1')
                    or str(self._find_available_port(APP_PORT_RANGE_1))
                ),
                int(
                    os.getenv('APP_PORT_2')
                    or str(self._find_available_port(APP_PORT_RANGE_2))
                ),
            ]
            # 更新 API URL
            self.api_url = (
                f'{self.config.sandbox.local_runtime_url}:{self._execution_server_port}'
            )

            # 启动服务器进程
            cmd = get_action_execution_server_startup_command(
                server_port=self._execution_server_port,
                plugins=self.plugins,
                app_config=self.config,
                python_prefix=[],                    # 不使用前缀
                python_executable=sys.executable,   # 使用当前 Python 解释器
                override_user_id=self._user_id,     # 使用当前用户 ID
                override_username=self._username,   # 使用当前用户名
            )

            self.log('info', f'Starting server with command: {cmd}')
            # 准备环境变量
            env = os.environ.copy()
            # 获取代码仓库路径
            code_repo_path = os.path.dirname(os.path.dirname(openhands.__file__))
            env['PYTHONPATH'] = os.pathsep.join(
                [code_repo_path, env.get('PYTHONPATH', '')]
            )
            env['OPENHANDS_REPO_PATH'] = code_repo_path
            env['LOCAL_RUNTIME_MODE'] = '1'
            env['VSCODE_PORT'] = str(self._vscode_port)

            # 使用 sys.executable 派生环境路径
            interpreter_path = sys.executable
            python_bin_path = os.path.dirname(interpreter_path)

            # 将解释器的 bin 目录添加到子进程的 PATH 前面
            env['PATH'] = f'{python_bin_path}{os.pathsep}{env.get("PATH", "")}'
            logger.debug(f'Updated PATH for subprocesses: {env["PATH"]}')

            # 如果未跳过依赖检查，则使用派生的环境路径检查依赖
            if os.getenv('SKIP_DEPENDENCY_CHECK', '') != '1':
                check_browser = self.config.enable_browser and sys.platform != 'win32'
                check_dependencies(code_repo_path, check_browser)

            # 启动服务器进程
            self.server_process = subprocess.Popen(  # noqa: S603
                cmd,
                stdout=subprocess.PIPE,     # 捕获标准输出
                stderr=subprocess.STDOUT,   # 将标准错误重定向到标准输出
                universal_newlines=True,    # 使用文本模式
                bufsize=1,                  # 行缓冲
                env=env,                    # 环境变量
                cwd=code_repo_path,         # 显式设置工作目录
            )

            # 启动线程来读取和记录服务器输出
            def log_output() -> None:
                """日志输出线程函数，负责读取服务器进程的输出并记录。"""
                if not self.server_process or not self.server_process.stdout:
                    self.log(
                        'error', 'Server process or stdout not available for logging.'
                    )
                    return

                try:
                    # 在进程运行且标准输出可用时读取行
                    while self.server_process.poll() is None:
                        # 检查退出事件
                        if self._log_thread_exit_event.is_set():
                            self.log('info', 'Log thread received exit signal.')
                            break  # 如果收到信号则退出循环
                        
                        line = self.server_process.stdout.readline()
                        if not line:
                            # 进程可能在 poll() 和 readline() 之间退出
                            break
                        self.log('info', f'Server: {line.strip()}')

                    # 在进程退出后或收到信号时捕获任何剩余输出
                    if not self._log_thread_exit_event.is_set():
                        self.log(
                            'info', 'Server process exited, reading remaining output.'
                        )
                        for line in self.server_process.stdout:
                            # 在循环内也检查
                            if self._log_thread_exit_event.is_set():
                                self.log(
                                    'info',
                                    'Log thread received exit signal while reading remaining output.',
                                )
                                break
                            self.log('info', f'Server (remaining): {line.strip()}')

                except Exception as e:
                    # 记录错误，但不阻止线程可能退出
                    self.log('error', f'Error reading server output: {e}')
                finally:
                    # 为线程退出添加日志
                    self.log('info', 'Log output thread finished.')

            # 创建并启动日志线程
            self._log_thread = threading.Thread(target=log_output, daemon=True)
            self._log_thread.start()

            # 在全局字典中存储服务器进程信息
            _RUNNING_SERVERS[self.sid] = ActionExecutionServerInfo(
                process=self.server_process,
                execution_server_port=self._execution_server_port,
                vscode_port=self._vscode_port,
                app_ports=self._app_ports,
                log_thread=self._log_thread,
                log_thread_exit_event=self._log_thread_exit_event,
                temp_workspace=self._temp_workspace,
                workspace_mount_path=self.config.workspace_mount_path_in_sandbox,
            )

        self.log('info', f'Waiting for server to become ready at {self.api_url}...')
        self.set_runtime_status(RuntimeStatus.STARTING_RUNTIME)

        # 等待服务器就绪
        await call_sync_from_async(self._wait_until_alive)

        if not self.attach_to_existing:
            # 设置初始环境
            await call_sync_from_async(self.setup_initial_env)

        self.log(
            'debug',
            f'Server initialized with plugins: {[plugin.name for plugin in self.plugins]}',
        )
        if not self.attach_to_existing:
            self.set_runtime_status(RuntimeStatus.READY)
        self._runtime_initialized = True

    def _find_available_port(
        self, port_range: tuple[int, int], max_attempts: int = 5
    ) -> int:
        """
        在指定范围内查找可用端口。
        
        Args:
            port_range (tuple[int, int]): 端口范围 (最小值, 最大值)
            max_attempts (int, optional): 最大尝试次数。默认为 5
            
        Returns:
            int: 可用的端口号
        """
        port = port_range[1]
        for _ in range(max_attempts):
            port = find_available_tcp_port(port_range[0], port_range[1])
            return port
        return port

    @tenacity.retry(
        wait=tenacity.wait_fixed(2),
        stop=tenacity.stop_after_delay(120) | stop_if_should_exit(),
        before_sleep=lambda retry_state: logger.debug(
            f'Waiting for server to be ready... (attempt {retry_state.attempt_number})'
        ),
    )
    def _wait_until_alive(self) -> bool:
        """
        等待服务器准备好接受请求。
        
        使用 tenacity 装饰器进行重试，每 2 秒检查一次，最多等待 120 秒。
        
        Returns:
            bool: 如果服务器就绪返回 True
            
        Raises:
            RuntimeError: 如果服务器进程死亡
            Exception: 如果服务器未就绪
        """
        # 检查服务器进程是否仍在运行
        if self.server_process and self.server_process.poll() is not None:
            raise RuntimeError('Server process died')

        try:
            # 发送健康检查请求
            response = self.session.get(f'{self.api_url}/alive')
            response.raise_for_status()
            return True
        except Exception as e:
            self.log('debug', f'Server not ready yet: {e}')
            raise

    async def execute_action(self, action: Action) -> Observation:
        """
        通过向服务器发送请求来执行动作。
        
        Args:
            action (Action): 要执行的动作
            
        Returns:
            Observation: 执行结果的观察
            
        Raises:
            AgentRuntimeDisconnectedError: 如果运行时未初始化或连接丢失
        """
        if not self.runtime_initialized:
            raise AgentRuntimeDisconnectedError('Runtime not initialized')

        # 检查我们的服务器进程是否仍然有效
        if self.server_process is None:
            # 检查全局字典中是否有服务器
            if self.sid in _RUNNING_SERVERS:
                self.server_process = _RUNNING_SERVERS[self.sid].process
            else:
                raise AgentRuntimeDisconnectedError('Server process not found')

        # 检查服务器进程是否仍在运行
        if self.server_process.poll() is not None:
            # 如果进程死亡，从全局字典中删除
            if self.sid in _RUNNING_SERVERS:
                del _RUNNING_SERVERS[self.sid]
            raise AgentRuntimeDisconnectedError('Server process died')

        # 使用信号量确保一次只执行一个动作
        with self.action_semaphore:
            try:
                # 发送动作执行请求
                response = await call_sync_from_async(
                    lambda: self.session.post(
                        f'{self.api_url}/execute_action',
                        json={'action': event_to_dict(action)},
                    )
                )
                # 将响应转换为观察对象
                return observation_from_dict(response.json())
            except httpx.NetworkError:
                raise AgentRuntimeDisconnectedError('Server connection lost')

    def close(self) -> None:
        """
        如果不在 attach_to_existing 模式下，停止服务器进程。
        
        该方法会根据配置决定是否关闭服务器进程，并清理相关资源。
        """
        # 如果在 attach_to_existing 模式下，不关闭服务器
        if self.attach_to_existing:
            self.log(
                'info',
                f'Not closing server for session {self.sid} (attach_to_existing=True)',
            )
            # 只清理我们对进程的引用，但保持进程运行
            self.server_process = None
            # 当 attach_to_existing=True 时不清理临时工作空间
            super().close()
            return

        # 向日志线程发送退出信号
        self._log_thread_exit_event.set()

        # 从全局字典中删除
        if self.sid in _RUNNING_SERVERS:
            del _RUNNING_SERVERS[self.sid]

        # 终止服务器进程
        if self.server_process:
            self.server_process.terminate()
            try:
                # 等待进程优雅退出
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # 如果进程没有在超时时间内退出，强制杀死
                self.server_process.kill()
            self.server_process = None
            # 等待日志线程结束，设置超时
            self._log_thread.join(timeout=5)

        # 如果存在临时工作空间且我们创建了它，则清理
        if self._temp_workspace and not self.attach_to_existing:
            shutil.rmtree(self._temp_workspace)
            self._temp_workspace = None

        super().close()

    @classmethod
    async def delete(cls, conversation_id: str) -> None:
        """
        删除对话的运行时。
        
        Args:
            conversation_id (str): 要删除的对话 ID
        """
        if conversation_id in _RUNNING_SERVERS:
            logger.info(f'Deleting LocalRuntime for conversation {conversation_id}')
            server_info = _RUNNING_SERVERS[conversation_id]

            # 向日志线程发送退出信号
            server_info.log_thread_exit_event.set()

            # 终止服务器进程
            if server_info.process:
                server_info.process.terminate()
                try:
                    server_info.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server_info.process.kill()

            # 等待日志线程结束
            server_info.log_thread.join(timeout=5)

            # 从全局字典中删除
            del _RUNNING_SERVERS[conversation_id]
            logger.info(f'LocalRuntime for conversation {conversation_id} deleted')

    @property
    def runtime_url(self) -> str:
        """
        获取运行时 URL。
        
        根据环境变量或配置返回适当的运行时 URL。
        
        Returns:
            str: 运行时 URL
        """
        # 首先检查环境变量
        runtime_url = os.getenv('RUNTIME_URL')
        if runtime_url:
            return runtime_url

        # TODO: 如果我们在 K8 环境中有包含 RUNTIME_URL 的直接变量，这可以被移除
        runtime_url_pattern = os.getenv('RUNTIME_URL_PATTERN')
        hostname = os.getenv('HOSTNAME')
        if runtime_url_pattern and hostname:
            # 从主机名中提取运行时 ID
            runtime_id = hostname.split('-')[1]
            runtime_url = runtime_url_pattern.format(runtime_id=runtime_id)
            return runtime_url

        # 回退到 localhost
        return self.config.sandbox.local_runtime_url

    @property
    def vscode_url(self) -> str | None:
        """
        获取 VSCode 服务器的 URL。
        
        Returns:
            str | None: VSCode URL，如果没有 token 则返回 None
        """
        token = super().get_vscode_token()
        if not token:
            return None
        
        runtime_url = self.runtime_url
        if 'localhost' in runtime_url:
            # 本地运行时情况
            vscode_url = f'{self.runtime_url}:{self._vscode_port}'
        else:
            # 类似于远程运行时的情况...
            parsed_url = urlparse(runtime_url)
            vscode_url = f'{parsed_url.scheme}://vscode-{parsed_url.netloc}'
        
        return f'{vscode_url}/?tkn={token}&folder={self.config.workspace_mount_path_in_sandbox}'

    @property
    def web_hosts(self) -> dict[str, int]:
        """
        获取 web hosts 字典。
        
        Returns:
            dict[str, int]: 主机到端口的映射字典
        """
        hosts: dict[str, int] = {}
        # 为每个应用端口创建主机映射
        for port in self._app_ports:
            hosts[f'{self.runtime_url}:{port}'] = port
        return hosts