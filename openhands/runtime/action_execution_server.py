"""
这是运行时客户端的主要文件。
它负责执行从 OpenHands 后端接收到的 Action 并产生 Observation。

注意：这将在 docker 沙盒内部执行。
"""
# 这是运行时客户端的主要文件，负责处理从 OpenHands 后端接收的 Action 并产生相应的 Observation

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import shutil
import sys
import tempfile
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from zipfile import ZipFile

import puremagic
from binaryornot.check import is_binary
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from openhands_aci.editor.editor import OHEditor
from openhands_aci.editor.exceptions import ToolError
from openhands_aci.editor.results import ToolResult
from openhands_aci.utils.diff import get_diff
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException
from uvicorn import run

from openhands.core.config.mcp_config import MCPStdioServerConfig
from openhands.core.exceptions import BrowserUnavailableException
from openhands.core.logger import openhands_logger as logger
from openhands.events.action import (
    Action,
    BrowseInteractiveAction,
    BrowseURLAction,
    CmdRunAction,
    FileEditAction,
    FileReadAction,
    FileWriteAction,
    IPythonRunCellAction,
)
from openhands.events.event import FileEditSource, FileReadSource
from openhands.events.observation import (
    CmdOutputObservation,
    ErrorObservation,
    FileDownloadObservation,
    FileEditObservation,
    FileReadObservation,
    FileWriteObservation,
    IPythonRunCellObservation,
    Observation,
)
from openhands.events.serialization import event_from_dict, event_to_dict
from openhands.runtime.browser import browse
from openhands.runtime.browser.browser_env import BrowserEnv
from openhands.runtime.file_viewer_server import start_file_viewer_server

# 导入自定义的 MCP Proxy Manager
from openhands.runtime.mcp.proxy import MCPProxyManager
from openhands.runtime.plugins import ALL_PLUGINS, JupyterPlugin, Plugin, VSCodePlugin
from openhands.runtime.utils import find_available_tcp_port
from openhands.runtime.utils.bash import BashSession
from openhands.runtime.utils.files import insert_lines, read_lines
from openhands.runtime.utils.memory_monitor import MemoryMonitor
from openhands.runtime.utils.runtime_init import init_user_and_working_directory
from openhands.runtime.utils.system_stats import get_system_stats
from openhands.utils.async_utils import call_sync_from_async, wait_all

# 在 Windows 平台上导入 Windows 特定的 PowerShell Session
if sys.platform == 'win32':
    from openhands.runtime.utils.windows_bash import WindowsPowershellSession


class ActionRequest(BaseModel):
    """
    Action 请求的数据模型。

    用于定义从客户端接收的 Action 请求的结构。

    Attributes:
        action (dict): 包含 Action 信息的字典
    """
    action: dict


# 根用户组 ID，用于权限管理
ROOT_GID = 0

# 从环境变量中获取 Session API 密钥，用于身份验证
SESSION_API_KEY = os.environ.get('SESSION_API_KEY')
# 创建 API 密钥头部验证器
api_key_header = APIKeyHeader(name='X-Session-API-Key', auto_error=False)


def verify_api_key(api_key: str = Depends(api_key_header)):
    """
    验证 API 密钥。

    Args:
        api_key (str): 从请求头中提取的 API 密钥

    Returns:
        str: 验证通过的 API 密钥

    Raises:
        HTTPException: 当 API 密钥无效时抛出 403 错误
    """
    if SESSION_API_KEY and api_key != SESSION_API_KEY:
        raise HTTPException(status_code=403, detail='Invalid API Key')
        # 当 API 密钥无效时返回 403 错误
    return api_key


def _execute_file_editor(
    editor: OHEditor,
    command: str,
    path: str,
    file_text: str | None = None,
    view_range: list[int] | None = None,
    old_str: str | None = None,
    new_str: str | None = None,
    insert_line: int | str | None = None,
    enable_linting: bool = False,
) -> tuple[str, tuple[str | None, str | None]]:
    """
    执行文件编辑器命令并处理异常。

    Args:
        editor (OHEditor): OHEditor 实例
        command (str): 要执行的编辑器命令
        path (str): 文件路径
        file_text (str | None): 可选的文件文本内容
        view_range (list[int] | None): 可选的查看范围元组 (开始, 结束)
        old_str (str | None): 可选的要替换的字符串
        new_str (str | None): 可选的替换字符串
        insert_line (int | str | None): 可选的插入行号 (可以是 int 或 str)
        enable_linting (bool): 是否启用语法检查

    Returns:
        tuple: 包含输出字符串和 (旧文件内容, 新文件内容) 元组的元组
    """
    result: ToolResult | None = None

    # 如果需要，将 insert_line 从字符串转换为 int
    if insert_line is not None and isinstance(insert_line, str):
        try:
            insert_line = int(insert_line)
        except ValueError:
            return (
                f"ERROR:\nInvalid insert_line value: '{insert_line}'. Expected an integer.",
                # 无效的 insert_line 值错误信息
                (None, None),
            )

    try:
        # 调用编辑器执行命令
        result = editor(
            command=command,
            path=path,
            file_text=file_text,
            view_range=view_range,
            old_str=old_str,
            new_str=new_str,
            insert_line=insert_line,
            enable_linting=enable_linting,
        )
    except ToolError as e:
        # 处理工具错误
        result = ToolResult(error=e.message)
    except TypeError as e:
        # 处理意外的参数或类型错误
        return f'ERROR:\n{str(e)}', (None, None)

    # 如果结果有错误，返回错误信息
    if result.error:
        return f'ERROR:\n{result.error}', (None, None)

    # 如果没有输出，记录警告
    if not result.output:
        logger.warning(f'No output from file_editor for {path}')
        # 文件编辑器没有输出的警告
        return '', (None, None)

    return result.output, (result.old_content, result.new_content)


class ActionExecutor:
    """
    ActionExecutor 运行在 docker 沙盒内部。
    它负责执行从 OpenHands 后端接收到的 Action 并产生 Observation。

    Attributes:
        plugins_to_load (list[Plugin]): 要加载的插件列表
        _initial_cwd (str): 初始工作目录
        username (str): 用户名
        user_id (int): 用户 ID
        bash_session (BashSession | WindowsPowershellSession | None): Bash 或 PowerShell session
        lock (asyncio.Lock): 异步锁，用于同步访问
        plugins (dict[str, Plugin]): 已加载的插件字典
        file_editor (OHEditor): 文件编辑器实例
        enable_browser (bool): 是否启用浏览器功能
        browser (BrowserEnv | None): 浏览器环境实例
        browser_init_task (asyncio.Task | None): 浏览器初始化任务
        browsergym_eval_env (str | None): BrowserGym 评估环境
        start_time (float): 启动时间
        last_execution_time (float): 最后执行时间
        _initialized (bool): 是否已初始化
        downloaded_files (list[str]): 已下载文件列表
        downloads_directory (str): 下载目录
        max_memory_gb (int | None): 最大内存限制（GB）
        memory_monitor (MemoryMonitor): 内存监控器
    """

    def __init__(
        self,
        plugins_to_load: list[Plugin],
        work_dir: str,
        username: str,
        user_id: int,
        enable_browser: bool,
        browsergym_eval_env: str | None,
    ) -> None:
        """
        初始化 ActionExecutor。

        Args:
            plugins_to_load (list[Plugin]): 要加载的插件列表
            work_dir (str): 工作目录
            username (str): 用户名
            user_id (int): 用户 ID
            enable_browser (bool): 是否启用浏览器
            browsergym_eval_env (str | None): BrowserGym 评估环境
        """
        self.plugins_to_load = plugins_to_load
        self._initial_cwd = work_dir
        self.username = username
        self.user_id = user_id

        # 初始化用户和工作目录
        _updated_user_id = init_user_and_working_directory(
            username=username, user_id=self.user_id, initial_cwd=work_dir
        )
        if _updated_user_id is not None:
            self.user_id = _updated_user_id

        # 初始化各种组件
        self.bash_session: BashSession | 'WindowsPowershellSession' | None = None  # type: ignore[name-defined]
        self.lock = asyncio.Lock()  # 用于同步访问的异步锁
        self.plugins: dict[str, Plugin] = {}  # 插件字典
        self.file_editor = OHEditor(workspace_root=self._initial_cwd)  # 文件编辑器
        self.enable_browser = enable_browser  # 是否启用浏览器
        self.browser: BrowserEnv | None = None  # 浏览器环境
        self.browser_init_task: asyncio.Task | None = None  # 浏览器初始化任务
        self.browsergym_eval_env = browsergym_eval_env  # BrowserGym 评估环境

        # 检查浏览器配置的一致性
        if (not self.enable_browser) and self.browsergym_eval_env:
            raise BrowserUnavailableException(
                'Browser environment is not enabled in config, but browsergym_eval_env is set'
            )
            # 浏览器环境未启用但设置了 browsergym_eval_env 时抛出异常

        # 初始化时间记录
        self.start_time = time.time()
        self.last_execution_time = self.start_time
        self._initialized = False  # 初始化状态标志
        self.downloaded_files: list[str] = []  # 已下载文件列表
        self.downloads_directory = '/workspace/.downloads'  # 下载目录

        # 设置内存限制
        self.max_memory_gb: int | None = None
        if _override_max_memory_gb := os.environ.get('RUNTIME_MAX_MEMORY_GB', None):
            self.max_memory_gb = int(_override_max_memory_gb)
            logger.info(
                f'Setting max memory to {self.max_memory_gb}GB (according to the RUNTIME_MAX_MEMORY_GB environment variable)'
            )
            # 根据 RUNTIME_MAX_MEMORY_GB 环境变量设置最大内存
        else:
            logger.info('No max memory limit set, using all available system memory')
            # 没有设置最大内存限制，使用所有可用系统内存

        # 初始化内存监控器
        self.memory_monitor = MemoryMonitor(
            enable=os.environ.get('RUNTIME_MEMORY_MONITOR', 'False').lower()
            in ['true', '1', 'yes']
        )
        self.memory_monitor.start_monitoring()

    @property
    def initial_cwd(self):
        """
        获取初始工作目录。

        Returns:
            str: 初始工作目录路径
        """
        return self._initial_cwd

    async def _init_browser_async(self):
        """
        异步初始化浏览器。

        在后台异步初始化浏览器环境，以避免阻塞主线程。
        """
        if not self.enable_browser:
            logger.info('Browser environment is not enabled in config')
            # 浏览器环境未在配置中启用
            return

        if sys.platform == 'win32':
            logger.warning('Browser environment not supported on windows')
            # Windows 上不支持浏览器环境
            return

        logger.debug('Initializing browser asynchronously')
        # 异步初始化浏览器
        try:
            self.browser = BrowserEnv(self.browsergym_eval_env)
            logger.debug('Browser initialized asynchronously')
            # 浏览器异步初始化完成
        except Exception as e:
            logger.error(f'Failed to initialize browser: {e}')
            # 初始化浏览器失败
            self.browser = None

    async def _ensure_browser_ready(self):
        """
        确保浏览器准备就绪。

        检查浏览器状态，如果未初始化则启动初始化过程，并等待完成。

        Raises:
            BrowserUnavailableException: 当浏览器初始化失败时抛出
        """
        if self.browser is None:
            if self.browser_init_task is None:
                # 如果浏览器初始化任务尚未启动，则启动它
                self.browser_init_task = asyncio.create_task(self._init_browser_async())
            elif self.browser_init_task.done():
                # 如果任务已完成但浏览器仍为 None，则重新启动初始化
                self.browser_init_task = asyncio.create_task(self._init_browser_async())

            # 等待浏览器初始化完成
            if self.browser_init_task:
                logger.debug('Waiting for browser to be ready...')
                # 等待浏览器准备就绪
                await self.browser_init_task

            # 检查浏览器是否成功初始化
            if self.browser is None:
                raise BrowserUnavailableException('Browser initialization failed')
                # 浏览器初始化失败

        # 如果执行到这里，浏览器已准备就绪
        logger.debug('Browser is ready')

    def _create_bash_session(self, cwd: str | None = None):
        """
        创建 Bash 或 PowerShell session。

        根据操作系统类型创建相应的 shell session。

        Args:
            cwd (str | None): 工作目录，如果为 None 则使用初始工作目录

        Returns:
            BashSession | WindowsPowershellSession: 创建的 session 实例
        """
        if sys.platform == 'win32':
            # Windows 平台使用 PowerShell Session
            return WindowsPowershellSession(  # type: ignore[name-defined]
                work_dir=cwd or self._initial_cwd,
                username=self.username,
                no_change_timeout_seconds=int(
                    os.environ.get('NO_CHANGE_TIMEOUT_SECONDS', 10)
                ),
                max_memory_mb=self.max_memory_gb * 1024 if self.max_memory_gb else None,
            )
        else:
            # 非 Windows 平台使用 Bash Session
            bash_session = BashSession(
                work_dir=cwd or self._initial_cwd,
                username=self.username,
                no_change_timeout_seconds=int(
                    os.environ.get('NO_CHANGE_TIMEOUT_SECONDS', 10)
                ),
                max_memory_mb=self.max_memory_gb * 1024 if self.max_memory_gb else None,
            )
            bash_session.initialize()
            return bash_session

    async def ainit(self):
        """
        异步初始化 ActionExecutor。

        按顺序初始化各个组件：bash session、浏览器、插件、Agent Skills 和 bash 命令。
        """
        # bash 需要首先初始化
        logger.debug('Initializing bash session')
        self.bash_session = self._create_bash_session()
        logger.debug('Bash session initialized')

        # 在后台启动浏览器初始化
        self.browser_init_task = asyncio.create_task(self._init_browser_async())
        logger.debug('Browser initialization started in background')

        # 并行初始化所有插件
        await wait_all(
            (self._init_plugin(plugin) for plugin in self.plugins_to_load),
            timeout=int(os.environ.get('INIT_PLUGIN_TIMEOUT', '120')),
        )
        logger.debug('All plugins initialized')

        # 这是一个临时解决方案
        # TODO: 将 AgentSkills 重构为 JupyterPlugin 的一部分
        # 在 ServerRuntime 弃用后
        logger.debug('Initializing AgentSkills')
        if 'agent_skills' in self.plugins and 'jupyter' in self.plugins:
            obs = await self.run_ipython(
                IPythonRunCellAction(
                    code='from openhands.runtime.plugins.agent_skills.agentskills import *\n'
                )
            )
            logger.debug(f'AgentSkills initialized: {obs}')

        # 初始化 bash 命令
        logger.debug('Initializing bash commands')
        await self._init_bash_commands()

        logger.debug('Runtime client initialized.')
        # 运行时客户端初始化完成
        self._initialized = True

    @property
    def initialized(self) -> bool:
        """
        检查是否已初始化。

        Returns:
            bool: 初始化状态
        """
        return self._initialized

    async def _init_plugin(self, plugin: Plugin):
        """
        初始化单个插件。

        Args:
            plugin (Plugin): 要初始化的插件实例
        """
        assert self.bash_session is not None
        await plugin.initialize(self.username)
        self.plugins[plugin.name] = plugin
        logger.debug(f'Initializing plugin: {plugin.name}')

        # 如果是 Jupyter 插件，设置工作目录
        if isinstance(plugin, JupyterPlugin):
            # 在 Windows 路径中转义反斜杠
            cwd = self.bash_session.cwd.replace('\\', '/')
            await self.run_ipython(
                IPythonRunCellAction(code=f'import os; os.chdir(r"{cwd}")')
            )

    async def _init_bash_commands(self):
        """
        初始化 bash 命令。

        根据操作系统和运行时模式配置 git 和其他基础命令。
        """
        INIT_COMMANDS = []
        is_local_runtime = os.environ.get('LOCAL_RUNTIME_MODE') == '1'
        is_windows = sys.platform == 'win32'

        # 根据平台和运行时模式确定 git 配置命令
        if is_local_runtime:
            if is_windows:
                # Windows 本地运行时 - 分割成单独的命令
                INIT_COMMANDS.append(
                    'git config --file ./.git_config user.name "openhands"'
                )
                INIT_COMMANDS.append(
                    'git config --file ./.git_config user.email "openhands@all-hands.dev"'
                )
                INIT_COMMANDS.append(
                    '$env:GIT_CONFIG = (Join-Path (Get-Location) ".git_config")'
                )
            else:
                # Linux/macOS 本地运行时
                base_git_config = (
                    'git config --file ./.git_config user.name "openhands" && '
                    'git config --file ./.git_config user.email "openhands@all-hands.dev" && '
                    'export GIT_CONFIG=$(pwd)/.git_config'
                )
                INIT_COMMANDS.append(base_git_config)
        else:
            # 非本地运行时（意味着 Linux/macOS）
            base_git_config = (
                'git config --global user.name "openhands" && '
                'git config --global user.email "openhands@all-hands.dev"'
            )
            INIT_COMMANDS.append(base_git_config)

        # 确定 no-pager 命令
        if is_windows:
            no_pager_cmd = 'function git { git.exe --no-pager $args }'
        else:
            no_pager_cmd = 'alias git="git --no-pager"'

        INIT_COMMANDS.append(no_pager_cmd)

        logger.info(f'Initializing by running {len(INIT_COMMANDS)} bash commands...')
        # 通过运行 bash 命令进行初始化
        for command in INIT_COMMANDS:
            action = CmdRunAction(command=command)
            action.set_hard_timeout(300)  # 设置 5 分钟超时
            logger.debug(f'Executing init command: {command}')
            obs = await self.run(action)
            assert isinstance(obs, CmdOutputObservation)
            logger.debug(
                f'Init command outputs (exit code: {obs.exit_code}): {obs.content}'
            )
            # 初始化命令输出
            assert obs.exit_code == 0  # 确保命令成功执行
        logger.debug('Bash init commands completed')

    async def run_action(self, action) -> Observation:
        """
        运行 Action 并返回 Observation。

        Args:
            action: 要执行的 Action 实例

        Returns:
            Observation: 执行结果的 Observation
        """
        async with self.lock:  # 使用异步锁确保线程安全
            action_type = action.action
            observation = await getattr(self, action_type)(action)
            return observation

    async def run(
        self, action: CmdRunAction
    ) -> CmdOutputObservation | ErrorObservation:
        """
        运行命令 Action。

        Args:
            action (CmdRunAction): 要执行的命令 Action

        Returns:
            CmdOutputObservation | ErrorObservation: 命令执行结果或错误信息
        """
        try:
            bash_session = self.bash_session
            # 如果是静态命令，创建新的 bash session
            if action.is_static:
                bash_session = self._create_bash_session(action.cwd)
            assert bash_session is not None
            obs = await call_sync_from_async(bash_session.execute, action)
            return obs
        except Exception as e:
            logger.error(f'Error running command: {e}')
            return ErrorObservation(str(e))

    async def run_ipython(self, action: IPythonRunCellAction) -> Observation:
        """
        运行 IPython 代码。

        Args:
            action (IPythonRunCellAction): 要执行的 IPython Action

        Returns:
            Observation: 执行结果的 Observation

        Raises:
            RuntimeError: 当 Jupyter 插件未找到时抛出
        """
        assert self.bash_session is not None
        if 'jupyter' in self.plugins:
            _jupyter_plugin: JupyterPlugin = self.plugins['jupyter']  # type: ignore
            # 这用于让 Jupyter 中的 AgentSkills 知道
            # Bash 中的当前工作目录
            jupyter_cwd = getattr(self, '_jupyter_cwd', None)
            if self.bash_session.cwd != jupyter_cwd:
                logger.debug(
                    f'{self.bash_session.cwd} != {jupyter_cwd} -> reset Jupyter PWD'
                )
                # 转义 windows 路径
                cwd = self.bash_session.cwd.replace('\\', '/')
                reset_jupyter_cwd_code = f'import os; os.chdir("{cwd}")'
                _aux_action = IPythonRunCellAction(code=reset_jupyter_cwd_code)
                _reset_obs: IPythonRunCellObservation = await _jupyter_plugin.run(
                    _aux_action
                )
                logger.debug(
                    f'Changed working directory in IPython to: {self.bash_session.cwd}. Output: {_reset_obs}'
                )
                # 在 IPython 中更改工作目录
                self._jupyter_cwd = self.bash_session.cwd

            obs: IPythonRunCellObservation = await _jupyter_plugin.run(action)
            obs.content = obs.content.rstrip()  # 去除尾部空白字符

            # 如果需要包含额外信息
            if action.include_extra:
                obs.content += (
                    f'\n[Jupyter current working directory: {self.bash_session.cwd}]'
                )
                obs.content += f'\n[Jupyter Python interpreter: {_jupyter_plugin.python_interpreter_path}]'
            return obs
        else:
            raise RuntimeError(
                'JupyterRequirement not found. Unable to run IPython action.'
            )
            # 未找到 Jupyter 要求，无法运行 IPython Action

    def _resolve_path(self, path: str, working_dir: str) -> str:
        """
        解析文件路径。

        将相对路径转换为绝对路径。

        Args:
            path (str): 要解析的路径
            working_dir (str): 工作目录

        Returns:
            str: 解析后的绝对路径
        """
        filepath = Path(path)
        if not filepath.is_absolute():
            return str(Path(working_dir) / filepath)
        return str(filepath)

    async def read(self, action: FileReadAction) -> Observation:
        """
        读取文件内容。

        Args:
            action (FileReadAction): 文件读取 Action

        Returns:
            Observation: 文件读取结果的 Observation
        """
        assert self.bash_session is not None

        # 无法读取二进制文件
        if is_binary(action.path):
            return ErrorObservation('ERROR_BINARY_FILE')

        # 使用 OH_ACI 实现源
        if action.impl_source == FileReadSource.OH_ACI:
            result_str, _ = _execute_file_editor(
                self.file_editor,
                command='view',
                path=action.path,
                view_range=action.view_range,
            )

            return FileReadObservation(
                content=result_str,
                path=action.path,
                impl_source=FileReadSource.OH_ACI,
            )

        # 注意：客户端代码运行在沙盒内部，
        # 因此无需检查权限
        working_dir = self.bash_session.cwd
        filepath = self._resolve_path(action.path, working_dir)
        try:
            # 处理图像文件
            if filepath.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif')):
                with open(filepath, 'rb') as file:
                    image_data = file.read()
                    encoded_image = base64.b64encode(image_data).decode('utf-8')
                    mime_type, _ = mimetypes.guess_type(filepath)
                    if mime_type is None:
                        mime_type = 'image/png'  # 如果无法确定 mime 类型，默认为 PNG
                    encoded_image = f'data:{mime_type};base64,{encoded_image}'

                return FileReadObservation(path=filepath, content=encoded_image)
            # 处理 PDF 文件
            elif filepath.lower().endswith('.pdf'):
                with open(filepath, 'rb') as file:
                    pdf_data = file.read()
                    encoded_pdf = base64.b64encode(pdf_data).decode('utf-8')
                    encoded_pdf = f'data:application/pdf;base64,{encoded_pdf}'
                return FileReadObservation(path=filepath, content=encoded_pdf)
            # 处理视频文件
            elif filepath.lower().endswith(('.mp4', '.webm', '.ogg')):
                with open(filepath, 'rb') as file:
                    video_data = file.read()
                    encoded_video = base64.b64encode(video_data).decode('utf-8')
                    mime_type, _ = mimetypes.guess_type(filepath)
                    if mime_type is None:
                        mime_type = 'video/mp4'  # 如果无法确定 MIME 类型，默认为 MP4
                    encoded_video = f'data:{mime_type};base64,{encoded_video}'

                return FileReadObservation(path=filepath, content=encoded_video)

            # 处理普通文本文件
            with open(filepath, 'r', encoding='utf-8') as file:
                lines = read_lines(file.readlines(), action.start, action.end)
        except FileNotFoundError:
            return ErrorObservation(
                f'File not found: {filepath}. Your current working directory is {working_dir}.'
            )
        except UnicodeDecodeError:
            return ErrorObservation(f'File could not be decoded as utf-8: {filepath}.')
        except IsADirectoryError:
            return ErrorObservation(
                f'Path is a directory: {filepath}. You can only read files'
            )

        code_view = ''.join(lines)
        return FileReadObservation(path=filepath, content=code_view)

    async def write(self, action: FileWriteAction) -> Observation:
        """
        写入文件内容。

        Args:
            action (FileWriteAction): 文件写入 Action

        Returns:
            Observation: 文件写入结果的 Observation
        """
        assert self.bash_session is not None
        working_dir = self.bash_session.cwd
        filepath = self._resolve_path(action.path, working_dir)

        insert = action.content.split('\n')
        # 如果目录不存在，创建目录
        if not os.path.exists(os.path.dirname(filepath)):
            os.makedirs(os.path.dirname(filepath))

        file_exists = os.path.exists(filepath)
        if file_exists:
            file_stat = os.stat(filepath)  # 获取文件状态信息
        else:
            file_stat = None

        mode = 'w' if not file_exists else 'r+'  # 根据文件是否存在选择打开模式
        try:
            with open(filepath, mode, encoding='utf-8') as file:
                if mode != 'w':
                    # 如果是编辑模式，读取所有行并插入新内容
                    all_lines = file.readlines()
                    new_file = insert_lines(insert, all_lines, action.start, action.end)
                else:
                    # 如果是新建模式，直接写入内容
                    new_file = [i + '\n' for i in insert]

                file.seek(0)  # 定位到文件开头
                file.writelines(new_file)
                file.truncate()  # 截断文件到当前位置

        except FileNotFoundError:
            return ErrorObservation(f'File not found: {filepath}')
        except IsADirectoryError:
            return ErrorObservation(
                f'Path is a directory: {filepath}. You can only write to files'
            )
        except UnicodeDecodeError:
            return ErrorObservation(f'File could not be decoded as utf-8: {filepath}')

        # 尝试处理文件权限
        try:
            if file_exists:
                assert file_stat is not None
                # 如果文件已存在，恢复原始文件权限
                os.chmod(filepath, file_stat.st_mode)
                os.chown(filepath, file_stat.st_uid, file_stat.st_gid)
            else:
                # 如果是新文件，设置新的文件权限
                os.chmod(filepath, 0o664)
                os.chown(filepath, self.user_id, self.user_id)
        except PermissionError as e:
            return ErrorObservation(
                f'File {filepath} written, but failed to change ownership and permissions: {e}'
            )
        return FileWriteObservation(content='', path=filepath)

    async def edit(self, action: FileEditAction) -> Observation:
        """
        编辑文件内容。

        Args:
            action (FileEditAction): 文件编辑 Action

        Returns:
            Observation: 文件编辑结果的 Observation
        """
        assert action.impl_source == FileEditSource.OH_ACI
        result_str, (old_content, new_content) = _execute_file_editor(
            self.file_editor,
            command=action.command,
            path=action.path,
            file_text=action.file_text,
            old_str=action.old_str,
            new_str=action.new_str,
            insert_line=action.insert_line,
            enable_linting=False,
        )

        return FileEditObservation(
            content=result_str,
            path=action.path,
            old_content=action.old_str,
            new_content=action.new_str,
            impl_source=FileEditSource.OH_ACI,
            diff=get_diff(
                old_contents=old_content or '',
                new_contents=new_content or '',
                filepath=action.path,
            ),
        )

    async def browse(self, action: BrowseURLAction) -> Observation:
        """
        浏览 URL。

        Args:
            action (BrowseURLAction): 浏览 URL Action

        Returns:
            Observation: 浏览结果的 Observation
        """
        if self.browser is None:
            return ErrorObservation(
                'Browser functionality is not supported or disabled.'
            )
            # 浏览器功能不受支持或已禁用
        await self._ensure_browser_ready()
        return await browse(action, self.browser, self.initial_cwd)

    async def browse_interactive(self, action: BrowseInteractiveAction) -> Observation:
        """
        交互式浏览。

        Args:
            action (BrowseInteractiveAction): 交互式浏览 Action

        Returns:
            Observation: 交互式浏览结果的 Observation
        """
        if self.browser is None:
            return ErrorObservation(
                'Browser functionality is not supported or disabled.'
            )
        await self._ensure_browser_ready()
        browser_observation = await browse(action, self.browser, self.initial_cwd)
        if not browser_observation.error:
            return browser_observation
        else:
            # 检查是否有新的文件下载
            curr_files = os.listdir(self.downloads_directory)
            new_download = False
            for file in curr_files:
                if file not in self.downloaded_files:
                    new_download = True
                    self.downloaded_files.append(file)
                    break  # 修复：为了简单起见，假设只下载一个文件

            if not new_download:
                return browser_observation
            else:
                # 新文件下载到 self.downloads_directory，将文件移动到 /workspace
                src_path = os.path.join(
                    self.downloads_directory, self.downloaded_files[-1]
                )
                # 使用 puremagic 猜测文件扩展名并添加到目标路径文件名
                file_ext = ''
                try:
                    guesses = puremagic.magic_file(src_path)
                    if len(guesses) > 0:
                        ext = guesses[0].extension.strip()
                        if len(ext) > 0:
                            file_ext = ext
                except Exception as _:
                    pass

                tgt_path = os.path.join(
                    '/workspace', f'file_{len(self.downloaded_files)}{file_ext}'
                )
                shutil.copy(src_path, tgt_path)
                file_download_obs = FileDownloadObservation(
                    content=f'Execution of the previous action {action.browser_actions} resulted in a file download. The downloaded file is saved at location: {tgt_path}',
                    # 前一个 Action 的执行导致了文件下载
                    file_path=tgt_path,
                )
                return file_download_obs

    def close(self):
        """
        关闭 ActionExecutor 并清理资源。

        停止内存监控、关闭 bash session 和浏览器。
        """
        self.memory_monitor.stop_monitoring()
        if self.bash_session is not None:
            self.bash_session.close()
        if self.browser is not None:
            self.browser.close()


if __name__ == '__main__':
    logger.warning('Starting Action Execution Server')
    # 启动 Action 执行服务器

    # 解析命令行参数
    parser = argparse.ArgumentParser()
    parser.add_argument('port', type=int, help='Port to listen on')
    # 监听端口
    parser.add_argument('--working-dir', type=str, help='Working directory')
    # 工作目录
    parser.add_argument('--plugins', type=str, help='Plugins to initialize', nargs='+')
    # 要初始化的插件
    parser.add_argument(
        '--username', type=str, help='User to run as', default='openhands'
    )
    # 运行用户
    parser.add_argument('--user-id', type=int, help='User ID to run as', default=1000)
    # 用户 ID
    parser.add_argument(
        '--enable-browser',
        action=argparse.BooleanOptionalAction,
        default=True,
        help='Enable the browser environment',
    )
    # 启用浏览器环境
    parser.add_argument(
        '--browsergym-eval-env',
        type=str,
        help='BrowserGym environment used for browser evaluation',
        default=None,
    )
    # 用于浏览器评估的 BrowserGym 环境

    # 示例：python client.py 8000 --working-dir /workspace --plugins JupyterRequirement
    args = parser.parse_args()

    # 在单独的线程中启动文件查看器服务器
    logger.info('Starting file viewer server')
    _file_viewer_port = find_available_tcp_port(
        min_port=args.port + 1, max_port=min(args.port + 1024, 65535)
    )
    server_url, _ = start_file_viewer_server(port=_file_viewer_port)
    logger.info(f'File viewer server started at {server_url}')

    # 加载插件
    plugins_to_load: list[Plugin] = []
    if args.plugins:
        for plugin in args.plugins:
            if plugin not in ALL_PLUGINS:
                raise ValueError(f'Plugin {plugin} not found')
                # 插件未找到
            plugins_to_load.append(ALL_PLUGINS[plugin]())  # type: ignore

    # 全局变量，用于存储 ActionExecutor 和 MCP Proxy Manager 实例
    client: ActionExecutor | None = None
    mcp_proxy_manager: MCPProxyManager | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """
        FastAPI 应用生命周期管理器。

        Args:
            app (FastAPI): FastAPI 应用实例

        Yields:
            None: 在应用启动和关闭之间的生命周期
        """
        global client, mcp_proxy_manager
        logger.info('Initializing ActionExecutor...')
        # 初始化 ActionExecutor
        client = ActionExecutor(
            plugins_to_load,
            work_dir=args.working_dir,
            username=args.username,
            user_id=args.user_id,
            enable_browser=args.enable_browser,
            browsergym_eval_env=args.browsergym_eval_env,
        )
        await client.ainit()
        logger.info('ActionExecutor initialized.')

        # 检查是否在 Windows 上运行
        is_windows = sys.platform == 'win32'

        # 初始化并挂载 MCP Proxy Manager（在 Windows 上跳过）
        if is_windows:
            logger.info('Skipping MCP Proxy initialization on Windows')
            # 在 Windows 上跳过 MCP Proxy 初始化
            mcp_proxy_manager = None
        else:
            logger.info('Initializing MCP Proxy Manager...')
            # 创建 MCP Proxy Manager
            mcp_proxy_manager = MCPProxyManager(
                auth_enabled=bool(SESSION_API_KEY),
                api_key=SESSION_API_KEY,
                logger_level=logger.getEffectiveLevel(),
            )
            mcp_proxy_manager.initialize()
            # 将代理挂载到应用程序
            allowed_origins = ['*']
            # 允许的来源
            try:
                await mcp_proxy_manager.mount_to_app(app, allowed_origins)
            except Exception as e:
                logger.error(f'Error mounting MCP Proxy: {e}', exc_info=True)
                raise RuntimeError(f'Cannot mount MCP Proxy: {e}')

        yield

        # 清理并释放资源
        logger.info('Shutting down MCP Proxy Manager...')
        if mcp_proxy_manager:
            del mcp_proxy_manager
            mcp_proxy_manager = None
        else:
            logger.info('MCP Proxy Manager instance not found for shutdown.')
            # 未找到 MCP Proxy Manager 实例进行关闭

        logger.info('Closing ActionExecutor...')
        if client:
            try:
                client.close()
                logger.info('ActionExecutor closed successfully.')
            except Exception as e:
                logger.error(f'Error closing ActionExecutor: {e}', exc_info=True)
        else:
            logger.info('ActionExecutor instance not found for closing.')
            # 未找到 ActionExecutor 实例进行关闭
        logger.info('Shutdown complete.')

    # 创建 FastAPI 应用
    app = FastAPI(lifespan=lifespan)

    # TODO：以下 3 个异常处理程序是 Sonnet 推荐的。
    # 这些是我们应该保留的吗？
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """
        全局异常处理程序。

        Args:
            request (Request): HTTP 请求
            exc (Exception): 异常实例

        Returns:
            JSONResponse: 包含错误详情的 JSON 响应
        """
        logger.exception('Unhandled exception occurred:')
        # 发生未处理的异常
        return JSONResponse(
            status_code=500,
            content={'detail': 'An unexpected error occurred. Please try again later.'},
            # 发生意外错误，请稍后重试
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """
        HTTP 异常处理程序。

        Args:
            request (Request): HTTP 请求
            exc (StarletteHTTPException): HTTP 异常实例

        Returns:
            JSONResponse: 包含错误详情的 JSON 响应
        """
        logger.error(f'HTTP exception occurred: {exc.detail}')
        # 发生 HTTP 异常
        return JSONResponse(status_code=exc.status_code, content={'detail': exc.detail})

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        """
        请求验证异常处理程序。

        Args:
            request (Request): HTTP 请求
            exc (RequestValidationError): 请求验证异常实例

        Returns:
            JSONResponse: 包含错误详情的 JSON 响应
        """
        logger.error(f'Validation error occurred: {exc}')
        # 发生验证错误
        return JSONResponse(
            status_code=422,
            content={
                'detail': 'Invalid request parameters',
                # 无效的请求参数
                'errors': str(exc.errors()),
            },
        )

    @app.middleware('http')
    async def authenticate_requests(request: Request, call_next):
        """
        HTTP 请求身份验证中间件。

        Args:
            request (Request): HTTP 请求
            call_next: 下一个中间件或处理程序

        Returns:
            Response: HTTP 响应
        """
        # 跳过某些路径的身份验证
        if request.url.path != '/alive' and request.url.path != '/server_info':
            try:
                verify_api_key(request.headers.get('X-Session-API-Key'))
            except HTTPException as e:
                return JSONResponse(
                    status_code=e.status_code, content={'detail': e.detail}
                )
        response = await call_next(request)
        return response

    @app.get('/server_info')
    async def get_server_info():
        """
        获取服务器信息。

        Returns:
            dict: 包含服务器运行时间、空闲时间和资源信息的字典
        """
        assert client is not None
        current_time = time.time()
        uptime = current_time - client.start_time  # 运行时间
        idle_time = current_time - client.last_execution_time  # 空闲时间

        response = {
            'uptime': uptime,
            'idle_time': idle_time,
            'resources': get_system_stats(),
        }
        logger.info('Server info endpoint response: %s', response)
        return response

    @app.post('/execute_action')
    async def execute_action(action_request: ActionRequest):
        """
        执行 Action。

        Args:
            action_request (ActionRequest): Action 请求

        Returns:
            dict: 序列化的 Observation 结果

        Raises:
            HTTPException: 当 Action 类型无效或执行出错时抛出
        """
        assert client is not None
        try:
            action = event_from_dict(action_request.action)
            if not isinstance(action, Action):
                raise HTTPException(status_code=400, detail='Invalid action type')
                # 无效的 Action 类型
            client.last_execution_time = time.time()
            observation = await client.run_action(action)
            return event_to_dict(observation)
        except Exception as e:
            logger.error(f'Error while running /execute_action: {str(e)}')
            raise HTTPException(
                status_code=500,
                detail=traceback.format_exc(),
            )

    @app.post('/update_mcp_server')
    async def update_mcp_server(request: Request):
        """
        更新 MCP 服务器。

        Args:
            request (Request): HTTP 请求

        Returns:
            JSONResponse: 更新结果的 JSON 响应

        Raises:
            HTTPException: 当 MCP Proxy Manager 未初始化或请求格式错误时抛出
        """
        # 检查是否在 Windows 上运行
        is_windows = sys.platform == 'win32'

        # 访问全局 mcp_proxy_manager 变量
        global mcp_proxy_manager

        if is_windows:
            # 在 Windows 上，只返回成功响应而不做任何操作
            logger.info(
                'MCP server update request received on Windows - skipping as MCP is disabled'
            )
            # 在 Windows 上收到 MCP 服务器更新请求 - 跳过，因为 MCP 已禁用
            return JSONResponse(
                status_code=200,
                content={
                    'detail': 'MCP server update skipped (MCP is disabled on Windows)',
                    # MCP 服务器更新已跳过（Windows 上禁用了 MCP）
                    'router_error_log': '',
                },
            )

        # 非 Windows 实现
        if mcp_proxy_manager is None:
            raise HTTPException(
                status_code=500, detail='MCP Proxy Manager is not initialized'
            )
            # MCP Proxy Manager 未初始化

        # 获取请求主体
        mcp_tools_to_sync = await request.json()
        if not isinstance(mcp_tools_to_sync, list):
            raise HTTPException(
                status_code=400, detail='Request must be a list of MCP tools to sync'
            )
            # 请求必须是要同步的 MCP 工具列表
        logger.info(
            f'Updating MCP server with tools: {json.dumps(mcp_tools_to_sync, indent=2)}'
        )
        # 使用工具更新 MCP 服务器
        mcp_tools_to_sync = [MCPStdioServerConfig(**tool) for tool in mcp_tools_to_sync]
        try:
            await mcp_proxy_manager.update_and_remount(app, mcp_tools_to_sync, ['*'])
            logger.info('MCP Proxy Manager updated and remounted successfully')
            # MCP Proxy Manager 更新和重新挂载成功
            router_error_log = ''
        except Exception as e:
            logger.error(f'Error updating MCP Proxy Manager: {e}', exc_info=True)
            router_error_log = str(e)

        return JSONResponse(
            status_code=200,
            content={
                'detail': 'MCP server updated successfully',
                # MCP 服务器更新成功
                'router_error_log': router_error_log,
            },
        )

    @app.post('/upload_file')
    async def upload_file(
        file: UploadFile, destination: str = '/', recursive: bool = False
    ):
        """
        上传文件。

        Args:
            file (UploadFile): 要上传的文件
            destination (str): 目标路径，默认为 '/'
            recursive (bool): 是否递归上传，默认为 False

        Returns:
            JSONResponse: 上传结果的 JSON 响应

        Raises:
            HTTPException: 当目标路径不是绝对路径或上传失败时抛出
        """
        assert client is not None

        try:
            # 确保目标目录存在
            if not os.path.isabs(destination):
                raise HTTPException(
                    status_code=400, detail='Destination must be an absolute path'
                )
                # 目标必须是绝对路径

            full_dest_path = destination
            if not os.path.exists(full_dest_path):
                os.makedirs(full_dest_path, exist_ok=True)

            if recursive or file.filename.endswith('.zip'):
                # 对于递归上传，我们期望一个 zip 文件
                if not file.filename.endswith('.zip'):
                    raise HTTPException(
                        status_code=400, detail='Recursive uploads must be zip files'
                    )
                    # 递归上传必须是 zip 文件

                zip_path = os.path.join(full_dest_path, file.filename)
                with open(zip_path, 'wb') as buffer:
                    shutil.copyfileobj(file.file, buffer)

                # 解压 zip 文件
                shutil.unpack_archive(zip_path, full_dest_path)
                os.remove(zip_path)  # 解压后删除 zip 文件

                logger.debug(
                    f'Uploaded file {file.filename} and extracted to {destination}'
                )
                # 上传文件并解压到目标目录
            else:
                # 单文件上传
                file_path = os.path.join(full_dest_path, file.filename)
                with open(file_path, 'wb') as buffer:
                    shutil.copyfileobj(file.file, buffer)
                logger.debug(f'Uploaded file {file.filename} to {destination}')

            return JSONResponse(
                content={
                    'filename': file.filename,
                    'destination': destination,
                    'recursive': recursive,
                },
                status_code=200,
            )

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get('/download_files')
    def download_file(path: str):
        """
        下载文件。

        Args:
            path (str): 要下载的文件路径

        Returns:
            FileResponse: 文件响应

        Raises:
            HTTPException: 当路径不是绝对路径或文件不存在时抛出
        """
        logger.debug('Downloading files')
        # 下载文件
        try:
            if not os.path.isabs(path):
                raise HTTPException(
                    status_code=400, detail='Path must be an absolute path'
                )
                # 路径必须是绝对路径

            if not os.path.exists(path):
                raise HTTPException(status_code=404, detail='File not found')
                # 文件未找到

            # 创建临时 zip 文件
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as temp_zip:
                with ZipFile(temp_zip, 'w') as zipf:
                    for root, _, files in os.walk(path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            zipf.write(
                                file_path, arcname=os.path.relpath(file_path, path)
                            )
                return FileResponse(
                    path=temp_zip.name,
                    media_type='application/zip',
                    filename=f'{os.path.basename(path)}.zip',
                    background=BackgroundTask(lambda: os.unlink(temp_zip.name)),
                )

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get('/alive')
    async def alive():
        """
        检查服务器是否存活。

        Returns:
            dict: 包含状态信息的字典
        """
        if client is None or not client.initialized:
            return {'status': 'not initialized'}
            # 未初始化
        return {'status': 'ok'}

    # ================================
    # VSCode 特定操作
    # ================================

    @app.get('/vscode/connection_token')
    async def get_vscode_connection_token():
        """
        获取 VSCode 连接令牌。

        Returns:
            dict: 包含令牌的字典
        """
        assert client is not None
        if 'vscode' in client.plugins:
            plugin: VSCodePlugin = client.plugins['vscode']  # type: ignore
            return {'token': plugin.vscode_connection_token}
        else:
            return {'token': None}

    # ================================
    # 面向 UI 的文件特定操作
    # ================================

    @app.post('/list_files')
    async def list_files(request: Request):
        """
        列出指定路径中的文件。

        此函数从 Agent 的运行时文件存储中检索文件列表，
        排除某些系统和隐藏文件/目录。

        要列出文件：
        ```sh
        curl -X POST -d '{"path": "/"}' http://localhost:3000/list_files
        ```

        Args:
            request (Request): 传入的请求对象
            path (str, optional): 要列出文件的路径。默认为 '/'

        Returns:
            list: 指定路径中的文件名列表

        Raises:
            HTTPException: 如果列出文件时出错
        """
        assert client is not None

        # 将请求作为字典获取
        request_dict = await request.json()
        path = request_dict.get('path', None)

        # 获取请求目录的完整路径
        if path is None:
            full_path = client.initial_cwd
        elif os.path.isabs(path):
            full_path = path
        else:
            full_path = os.path.join(client.initial_cwd, path)

        if not os.path.exists(full_path):
            # 如果用户刚刚删除了文件夹，防止 UI 中的服务器错误 500
            return JSONResponse(content=[])

        try:
            # 检查目录是否存在
            if not os.path.exists(full_path) or not os.path.isdir(full_path):
                return JSONResponse(content=[])

            entries = os.listdir(full_path)

            # 分离目录和文件
            directories = []
            files = []
            for entry in entries:
                # 删除前导斜杠和任何父目录组件
                entry_relative = entry.lstrip('/').split('/')[-1]

                # 通过将基础路径与相对条目路径连接来构造完整路径
                full_entry_path = os.path.join(full_path, entry_relative)
                if os.path.exists(full_entry_path):
                    is_dir = os.path.isdir(full_entry_path)
                    if is_dir:
                        # 在目录后添加尾部斜杠
                        # 前端需要区分目录和文件
                        entry = entry.rstrip('/') + '/'
                        directories.append(entry)
                    else:
                        files.append(entry)

            # 分别对目录和文件进行排序
            directories.sort(key=lambda s: s.lower())
            files.sort(key=lambda s: s.lower())

            # 合并排序后的目录和文件
            sorted_entries = directories + files
            return JSONResponse(content=sorted_entries)

        except Exception as e:
            logger.error(f'Error listing files: {e}')
            return JSONResponse(content=[])

    logger.debug(f'Starting action execution API on port {args.port}')
    # 在端口上启动 Action 执行 API
    run(app, host='0.0.0.0', port=args.port)
