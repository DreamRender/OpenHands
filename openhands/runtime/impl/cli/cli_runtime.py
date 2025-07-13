"""
这个Runtime在本地使用subprocess运行命令，并使用Python标准库执行文件操作。
它不实现浏览器功能。

CLIRuntime是一个本地运行时实现，它直接在宿主机上执行命令和文件操作，
不提供沙箱隔离，因此在使用时需要格外谨慎。
"""

import asyncio
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from binaryornot.check import is_binary
from openhands_aci.editor.editor import OHEditor
from openhands_aci.editor.exceptions import ToolError
from openhands_aci.editor.results import ToolResult
from openhands_aci.utils.diff import get_diff
from pydantic import SecretStr

from openhands.core.config import OpenHandsConfig
from openhands.core.config.mcp_config import MCPConfig, MCPStdioServerConfig
from openhands.core.exceptions import LLMMalformedActionError
from openhands.core.logger import openhands_logger as logger
from openhands.events import EventStream
from openhands.events.action import (
    BrowseInteractiveAction,
    BrowseURLAction,
    CmdRunAction,
    FileEditAction,
    FileReadAction,
    FileWriteAction,
    IPythonRunCellAction,
)
from openhands.events.action.mcp import MCPAction
from openhands.events.event import FileEditSource, FileReadSource
from openhands.events.observation import (
    CmdOutputObservation,
    ErrorObservation,
    FileEditObservation,
    FileReadObservation,
    FileWriteObservation,
    Observation,
)
from openhands.integrations.provider import PROVIDER_TOKEN_TYPE
from openhands.runtime.base import Runtime
from openhands.runtime.plugins import PluginRequirement
from openhands.runtime.runtime_status import RuntimeStatus

if TYPE_CHECKING:
    from openhands.runtime.utils.windows_bash import WindowsPowershellSession

# 在Windows平台上导入PowerShell支持模块
if sys.platform == 'win32':
    try:
        from openhands.runtime.utils.windows_bash import WindowsPowershellSession
        from openhands.runtime.utils.windows_exceptions import DotNetMissingError
    except (ImportError, DotNetMissingError) as err:
        # 打印用户友好的错误信息，不显示堆栈跟踪
        friendly_message = """
错误: 需要PowerShell和.NET SDK，但未正确配置

在Windows上使用OpenHands CLI需要.NET SDK和PowerShell。
没有.NET Core，PowerShell集成无法正常工作。

请按照以下说明安装.NET SDK:
https://docs.all-hands.dev/usage/windows-without-wsl

安装.NET SDK后，重启终端并重试。
"""
        print(friendly_message, file=sys.stderr)
        logger.error(
            f'Windows runtime初始化失败: {type(err).__name__}: {str(err)}'
        )
        if (
            isinstance(err, DotNetMissingError)
            and hasattr(err, 'details')
            and err.details
        ):
            logger.debug(f'详细信息: {err.details}')

        # 以错误代码退出程序
        sys.exit(1)


class CLIRuntime(Runtime):
    """
    本地命令行Runtime实现类。
    
    这个Runtime实现类在本地使用subprocess运行命令，使用Python标准库执行文件操作。
    它不实现浏览器功能，直接在宿主机上运行，没有沙箱隔离。
    
    主要特性:
    - 直接在本地文件系统上操作
    - 支持Windows PowerShell和Unix shell
    - 提供文件读写、编辑功能
    - 支持命令执行和输出流式传输
    - 工作区管理和文件操作
    
    警告: 此Runtime没有沙箱保护，会直接在宿主机上执行命令，使用时需要格外小心。

    Args:
        config (OpenHandsConfig): 应用程序配置对象
        event_stream (EventStream): 用于订阅的事件流
        sid (str, optional): Session ID，会话标识符。默认为'default'
        plugins (list[PluginRequirement] | None, optional): 插件需求列表。默认为None
        env_vars (dict[str, str] | None, optional): 要设置的环境变量。默认为None
        status_callback (Callable | None, optional): 状态更新回调函数。默认为None
        attach_to_existing (bool, optional): 是否附加到现有Session。默认为False
        headless_mode (bool, optional): 是否以无头模式运行。默认为False
        user_id (str | None, optional): 用户身份验证ID。默认为None
        git_provider_tokens (PROVIDER_TOKEN_TYPE | None, optional): Git提供商Token。默认为None
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
        headless_mode: bool = False,
        user_id: str | None = None,
        git_provider_tokens: PROVIDER_TOKEN_TYPE | None = None,
    ):
        """
        初始化CLIRuntime实例。
        
        设置工作区路径、文件编辑器和平台特定的shell环境。
        """
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

        # 设置工作区路径
        if self.config.workspace_base is not None:
            logger.warning(
                f'Workspace基础路径设置为 {self.config.workspace_base}。 '
                '它将被用作Agent运行的路径。 '
                '请小心，Agent可以编辑此目录中的文件！'
            )
            self._workspace_path = self.config.workspace_base  # 工作区根目录路径
        else:
            # 为工作区创建临时目录
            self._workspace_path = tempfile.mkdtemp(
                prefix=f'openhands_workspace_{sid}_'
            )
            logger.info(f'在 {self._workspace_path} 创建临时工作区')

        # Runtime测试依赖于此设置的正确性
        self.config.workspace_mount_path_in_sandbox = self._workspace_path

        # 初始化runtime状态
        self._runtime_initialized = False  # Runtime是否已初始化标志
        self.file_editor = OHEditor(workspace_root=self._workspace_path)  # 文件编辑器实例
        self._shell_stream_callback: Callable[[str], None] | None = None  # shell输出流回调函数

        # 在Windows上初始化PowerShell session
        self._is_windows = sys.platform == 'win32'  # 是否为Windows平台标志
        self._powershell_session: WindowsPowershellSession | None = None  # PowerShell会话对象

        logger.warning(
            '正在初始化CLIRuntime。警告: 没有使用沙箱。 '
            '此Runtime直接在本地系统上执行命令。 '
            '在不受信任的环境中使用时请谨慎。'
        )

    async def connect(self) -> None:
        """
        初始化Runtime连接。
        
        创建工作区目录，切换到工作区，初始化平台特定的shell环境，
        并设置初始环境。
        """
        self.set_runtime_status(RuntimeStatus.STARTING_RUNTIME)

        # 确保工作区目录存在
        os.makedirs(self._workspace_path, exist_ok=True)

        # 切换到工作区目录
        os.chdir(self._workspace_path)

        # 在Windows上初始化PowerShell session
        if self._is_windows:
            self._powershell_session = WindowsPowershellSession(
                work_dir=self._workspace_path,
                username=None,  # 使用当前用户
                no_change_timeout_seconds=30,  # 无变化超时时间
                max_memory_mb=None,  # 最大内存限制
            )

        # 如果不是附加到现有session，则设置初始环境
        if not self.attach_to_existing:
            await asyncio.to_thread(self.setup_initial_env)

        self._runtime_initialized = True
        self.set_runtime_status(RuntimeStatus.RUNTIME_STARTED)
        logger.info(f'CLIRuntime初始化完成，工作区位于 {self._workspace_path}')

    def add_env_vars(self, env_vars: dict[str, Any]) -> None:
        """
        向当前Runtime环境添加环境变量。
        
        对于CLIRuntime，这意味着更新当前进程的os.environ，
        以便后续命令继承这些变量。
        这覆盖了BaseRuntime的行为，BaseRuntime会在初始化之前尝试运行shell命令
        并修改.bashrc，这对于本地CLI来说不太理想。
        
        Args:
            env_vars (dict[str, Any]): 要添加的环境变量字典
        """
        if not env_vars:
            return

        # 我们只记录键名以避免敏感值如token泄露到日志中
        logger.info(
            f'[CLIRuntime] 为此Session设置环境变量: {list(env_vars.keys())}'
        )

        for key, value in env_vars.items():
            if isinstance(value, SecretStr):
                # 处理SecretStr类型的敏感值
                os.environ[key] = value.get_secret_value()
                logger.warning(f'[CLIRuntime] 设置 os.environ["{key}"] (来自SecretStr)')
            else:
                # 处理普通字符串值
                os.environ[key] = value
                logger.debug(f'[CLIRuntime] 设置 os.environ["{key}"]')

        # 我们这里不使用self.run()，因为此方法在初始化期间调用，
        # 此时self._runtime_initialized为False

    def _safe_terminate_process(self, process_obj, signal_to_send=signal.SIGTERM):
        """
        安全地尝试终止/杀死进程组或单个进程。
        
        首先尝试终止整个进程组，如果失败则回退到终止单个进程。
        这确保了子进程也能被正确清理。

        Args:
            process_obj: 以start_new_session=True启动的subprocess.Popen对象
            signal_to_send: 要发送给进程组或进程的信号，默认为SIGTERM
        """
        pid = getattr(process_obj, 'pid', None)
        if pid is None:
            return

        # 根据信号类型生成描述信息
        group_desc = (
            'kill process group'
            if signal_to_send == signal.SIGKILL
            else 'terminate process group'
        )
        process_desc = (
            'kill process' if signal_to_send == signal.SIGKILL else 'terminate process'
        )

        try:
            # 尝试终止/杀死整个进程组
            logger.debug(f'[_safe_terminate_process] 要操作的原始PID: {pid}')
            pgid_to_kill = os.getpgid(
                pid
            )  # 如果pid已经不存在，这可能会抛出ProcessLookupError
            logger.debug(
                f'[_safe_terminate_process] 尝试对PID {pid} (PGID: {pgid_to_kill}) {group_desc}，信号 {signal_to_send}。'
            )
            os.killpg(pgid_to_kill, signal_to_send)
            logger.debug(
                f'[_safe_terminate_process] 成功向PGID {pgid_to_kill}发送信号 {signal_to_send} (原始PID: {pid})。'
            )
        except ProcessLookupError as e_pgid:
            # 进程可能已经退出，回退到直接终止
            logger.warning(
                f'[_safe_terminate_process] 获取PID {pid}的PGID时发生ProcessLookupError (可能已经退出): {e_pgid}。回退到直接kill/terminate。'
            )
            try:
                if signal_to_send == signal.SIGKILL:
                    process_obj.kill()
                else:
                    process_obj.terminate()
                logger.debug(
                    f'[_safe_terminate_process] 回退: 终止了{process_desc} (PID: {pid})。'
                )
            except Exception as e_fallback:
                logger.error(
                    f'[_safe_terminate_process] 回退: {process_desc}期间出错 (PID: {pid}): {e_fallback}'
                )
        except (AttributeError, OSError) as e_os:
            # 系统调用错误，回退到直接终止
            logger.error(
                f'[_safe_terminate_process] {group_desc}期间发生OSError/AttributeError，PID {pid}: {e_os}。正在回退。'
            )
            # 回退: 尝试直接终止/杀死主进程
            try:
                if signal_to_send == signal.SIGKILL:
                    process_obj.kill()
                else:
                    process_obj.terminate()
                logger.debug(
                    f'[_safe_terminate_process] 回退: 终止了{process_desc} (PID: {pid})。'
                )
            except Exception as e_fallback:
                logger.error(
                    f'[_safe_terminate_process] 回退: {process_desc}期间出错 (PID: {pid}): {e_fallback}'
                )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:
            logger.error(f'错误: {e}')

    def _execute_powershell_command(
        self, command: str, timeout: float
    ) -> CmdOutputObservation | ErrorObservation:
        """
        在Windows上使用PowerShell session执行命令。
        
        创建PowerShell session并执行指定命令，处理超时和错误情况。
        
        Args:
            command (str): 要执行的命令
            timeout (float): 命令的超时时间（秒）
            
        Returns:
            CmdOutputObservation | ErrorObservation: 包含完整输出和退出代码的观察结果
        """
        if self._powershell_session is None:
            return ErrorObservation(
                content='PowerShell session不可用。',
                error_id='POWERSHELL_SESSION_ERROR',
            )

        try:
            # 为PowerShell session创建CmdRunAction
            from openhands.events.action import CmdRunAction

            ps_action = CmdRunAction(command=command)
            ps_action.set_hard_timeout(timeout)

            # 使用PowerShell session执行命令
            return self._powershell_session.execute(ps_action)

        except Exception as e:
            logger.error(f'执行PowerShell命令 "{command}" 时出错: {e}')
            return ErrorObservation(
                content=f'执行PowerShell命令 "{command}" 时出错: {str(e)}',
                error_id='POWERSHELL_EXECUTION_ERROR',
            )

    def _execute_shell_command(
        self, command: str, timeout: float
    ) -> CmdOutputObservation:
        """
        执行shell命令并将其输出流式传输到回调函数。
        
        使用subprocess启动bash进程执行命令，实时读取输出并支持超时处理。
        
        Args:
            command (str): 要执行的shell命令
            timeout (float): 命令的超时时间（秒）
            
        Returns:
            CmdOutputObservation: 包含完整输出和退出代码的观察结果
        """
        output_lines = []  # 存储输出行
        timed_out = False  # 超时标志
        start_time = time.monotonic()  # 记录开始时间

        # 使用shell=True运行复杂的bash命令
        process = subprocess.Popen(
            ['bash', '-c', command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 将stderr重定向到stdout
            text=True,
            bufsize=1,  # 文本模式下明确设置行缓冲
            universal_newlines=True,
            start_new_session=True,  # 创建新的进程组，便于管理子进程
        )
        logger.debug(
            f'[_execute_shell_command] bash -c的PID: {process.pid}，命令: "{command}"'
        )

        exit_code = None

        try:
            if process.stdout:
                # 持续读取进程输出直到进程结束
                while process.poll() is None:
                    # 检查是否超时
                    if (
                        timeout is not None
                        and (time.monotonic() - start_time) > timeout
                    ):
                        logger.debug(
                            f'命令 "{command}" 在 {timeout:.1f} 秒后超时。正在终止。'
                        )
                        # 尝试终止进程组 (SIGTERM)
                        self._safe_terminate_process(
                            process, signal_to_send=signal.SIGTERM
                        )
                        timed_out = True
                        break

                    # 使用select检查是否有数据可读
                    ready_to_read, _, _ = select.select([process.stdout], [], [], 0.1)

                    if ready_to_read:
                        line = process.stdout.readline()
                        if line:
                            logger.debug(f'LINE: {line}')
                            output_lines.append(line)
                            # 如果设置了回调函数，调用它处理输出行
                            if self._shell_stream_callback:
                                self._shell_stream_callback(line)

            # 尝试读取stdout中的任何剩余数据
            if process.stdout and not process.stdout.closed:
                try:
                    while line:
                        line = process.stdout.readline()
                        if line:
                            logger.debug(f'LINE: {line}')
                            output_lines.append(line)
                            if self._shell_stream_callback:
                                self._shell_stream_callback(line)
                except Exception as e:
                    logger.warning(
                        f'循环后从stdout直接读取 "{command}" 时出错: {e}'
                    )

            exit_code = process.returncode

            # 如果发生超时，确保exit_code反映这一点
            if timed_out:
                exit_code = -1

        except Exception as e:
            logger.error(
                f'_execute_shell_command中的外部异常，命令 "{command}": {e}'
            )
            # 如果进程仍在运行，强制终止
            if process and process.poll() is None:
                self._safe_terminate_process(process, signal_to_send=signal.SIGKILL)
            return CmdOutputObservation(
                command=command,
                content=''.join(output_lines) + f'\n执行期间出错: {e}',
                exit_code=-1,
            )

        complete_output = ''.join(output_lines)
        logger.debug(
            f'[_execute_shell_command] 命令 "{command}" 的完整输出 (长度: {len(complete_output)}): {complete_output!r}'
        )
        
        # 准备观察结果的Metadata
        obs_metadata = {'working_dir': self._workspace_path}
        if timed_out:
            obs_metadata['suffix'] = (
                f'[命令在 {timeout:.1f} 秒后超时。]'
            )
            # exit_code = -1 # 如果timed_out为True，这已经设置了

        return CmdOutputObservation(
            command=command,
            content=complete_output,
            exit_code=exit_code,
            metadata=obs_metadata,
        )

    def run(self, action: CmdRunAction) -> Observation:
        """
        使用subprocess运行命令。
        
        根据平台选择使用PowerShell或标准shell执行命令，
        支持超时处理和输入验证。
        
        Args:
            action (CmdRunAction): 包含要执行命令的Action对象
            
        Returns:
            Observation: 命令执行结果的观察对象
        """
        if not self._runtime_initialized:
            return ErrorObservation(
                f'Runtime未初始化，命令: {action.command}'
            )

        # CLIRuntime不支持交互式输入
        if action.is_input:
            logger.warning(
                f"CLIRuntime收到一个`is_input=True`的action (命令: '{action.command}')。 "
                'CLIRuntime目前不支持向活动进程发送输入或信号。 '
                '此action将被忽略并返回错误观察结果。'
            )
            return ErrorObservation(
                content=f"CLIRuntime不支持Agent的交互式输入 (例如 'C-c')。命令 '{action.command}' 未发送到任何进程。",
                error_id='AGENT_ERROR$BAD_ACTION',
            )

        try:
            # 确定有效的超时时间
            effective_timeout = (
                action.timeout
                if action.timeout is not None
                else self.config.sandbox.timeout
            )

            logger.debug(
                f'在CLIRuntime中运行命令: "{action.command}"，有效超时时间: {effective_timeout}s'
            )

            # 在Windows上使用PowerShell（如果可用），否则使用subprocess
            if self._is_windows and self._powershell_session is not None:
                return self._execute_powershell_command(
                    action.command, timeout=effective_timeout
                )
            else:
                return self._execute_shell_command(
                    action.command, timeout=effective_timeout
                )
        except Exception as e:
            logger.error(
                f'CLIRuntime.run中执行命令 "{action.command}" 时出错: {str(e)}'
            )
            return ErrorObservation(
                f'运行命令 "{action.command}" 时出错: {str(e)}'
            )

    def run_ipython(self, action: IPythonRunCellAction) -> Observation:
        """
        运行Python代码单元格。
        
        此功能在CLIRuntime中未实现。
        用户还应该在AgentConfig中禁用Jupyter插件。
        
        Args:
            action (IPythonRunCellAction): IPython执行Action
            
        Returns:
            ErrorObservation: 说明功能未实现的错误观察结果
        """
        # 此功能在CLIRuntime中未实现
        # 如果您需要运行IPython/Jupyter单元格，请考虑使用不同的runtime
        # 或确保在AgentConfig中禁用Jupyter插件以避免尝试使用此禁用的功能
        logger.warning(
            "在CLIRuntime上调用了run_ipython，但它未实现。 "
            '请在AgentConfig中禁用Jupyter插件。'
        )
        return ErrorObservation(
            '在CLIRuntime中未实现执行IPython单元格的功能。 '
        )

    def _sanitize_filename(self, filename: str) -> str:
        """
        清理和验证文件名，确保其在工作区范围内。
        
        将相对路径转换为绝对路径，处理/workspace的映射，
        并防止路径遍历攻击。
        
        Args:
            filename (str): 要清理的文件名或路径
            
        Returns:
            str: 清理后的绝对路径
            
        Raises:
            LLMMalformedActionError: 当路径不安全或超出工作区范围时抛出
        """
        # 如果路径是绝对路径，确保它以_workspace_path开头
        if filename == '/workspace':
            actual_filename = self._workspace_path
        elif filename.startswith('/workspace/'):
            # 将/workspace/映射到实际的工作区路径
            # 注意: /workspace被广泛使用，所以我们映射它以允许在CLIRuntime中使用
            actual_filename = os.path.join(
                self._workspace_path, filename[len('/workspace/') :]
            )
        elif filename.startswith('/'):
            # 检查绝对路径是否在工作区内
            if not filename.startswith(self._workspace_path):
                raise LLMMalformedActionError(
                    f'无效路径: {filename}。您只能使用 {self._workspace_path} 中的文件。'
                )
            actual_filename = filename
        else:
            # 相对路径，相对于工作区根目录
            actual_filename = os.path.join(self._workspace_path, filename.lstrip('/'))

        # 解析路径以处理任何'..'或'.'组件
        resolved_path = os.path.realpath(actual_filename)

        # 检查解析后的路径是否仍在工作区内
        if not resolved_path.startswith(self._workspace_path):
            raise LLMMalformedActionError(
                f'无效的路径遍历: {filename}。路径解析到工作区外部。解析后: {resolved_path}，工作区: {self._workspace_path}'
            )

        return resolved_path

    def read(self, action: FileReadAction) -> Observation:
        """
        使用Python标准库或OHEditor读取文件。
        
        支持指定实现源和视图范围，自动检测二进制文件并拒绝读取。
        
        Args:
            action (FileReadAction): 文件读取Action对象
            
        Returns:
            Observation: 文件读取结果的观察对象
        """
        if not self._runtime_initialized:
            return ErrorObservation('Runtime未初始化')

        file_path = self._sanitize_filename(action.path)

        # 无法读取二进制文件
        if os.path.exists(file_path) and is_binary(file_path):
            return ErrorObservation('ERROR_BINARY_FILE')

        # 使用OHEditor作为OH_ACI实现源
        if action.impl_source == FileReadSource.OH_ACI:
            result_str, _ = self._execute_file_editor(
                command='view',
                path=file_path,
                view_range=action.view_range,
            )

            return FileReadObservation(
                content=result_str,
                path=action.path,
                impl_source=FileReadSource.OH_ACI,
            )

        try:
            # 检查文件是否存在
            if not os.path.exists(file_path):
                return ErrorObservation(f'文件未找到: {action.path}')

            # 检查是否为目录
            if os.path.isdir(file_path):
                return ErrorObservation(f'无法读取目录: {action.path}')

            # 读取文件
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()

            return FileReadObservation(content=content, path=action.path)
        except Exception as e:
            logger.error(f'读取文件时出错: {str(e)}')
            return ErrorObservation(f'读取文件 {action.path} 时出错: {str(e)}')

    def write(self, action: FileWriteAction) -> Observation:
        """
        使用Python标准库写入文件。
        
        自动创建必要的父目录，使用UTF-8编码写入文件内容。
        
        Args:
            action (FileWriteAction): 文件写入Action对象
            
        Returns:
            Observation: 文件写入结果的观察对象
        """
        if not self._runtime_initialized:
            return ErrorObservation('Runtime未初始化')

        file_path = self._sanitize_filename(action.path)

        try:
            # 如果父目录不存在则创建
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            # 写入文件
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(action.content)

            return FileWriteObservation(content='', path=action.path)
        except Exception as e:
            logger.error(f'写入文件时出错: {str(e)}')
            return ErrorObservation(f'写入文件 {action.path} 时出错: {str(e)}')

    def browse(self, action: BrowseURLAction) -> Observation:
        """
        浏览器功能未在CLI runtime中实现。
        
        Args:
            action (BrowseURLAction): 浏览URL的Action对象
            
        Returns:
            ErrorObservation: 说明功能未实现的错误观察结果
        """
        return ErrorObservation(
            '浏览器功能未在CLIRuntime中实现'
        )

    def browse_interactive(self, action: BrowseInteractiveAction) -> Observation:
        """
        交互式浏览器功能未在CLI runtime中实现。
        
        Args:
            action (BrowseInteractiveAction): 交互式浏览Action对象
            
        Returns:
            ErrorObservation: 说明功能未实现的错误观察结果
        """
        return ErrorObservation(
            '浏览器功能未在CLIRuntime中实现'
        )

    def _execute_file_editor(
        self,
        command: str,
        path: str,
        file_text: str | None = None,
        view_range: list[int] | None = None,
        old_str: str | None = None,
        new_str: str | None = None,
        insert_line: int | None = None,
        enable_linting: bool = False,
    ) -> tuple[str, tuple[str | None, str | None]]:
        """
        执行文件编辑器命令并处理异常。
        
        包装OHEditor调用，提供统一的错误处理和结果格式化。

        Args:
            command (str): 要执行的编辑器命令
            path (str): 文件路径
            file_text (str | None, optional): 可选的文件文本内容
            view_range (list[int] | None, optional): 可选的视图范围 (开始, 结束)
            old_str (str | None, optional): 要替换的可选字符串
            new_str (str | None, optional): 可选的替换字符串
            insert_line (int | None, optional): 插入的可选行号
            enable_linting (bool, optional): 是否启用代码检查

        Returns:
            tuple: 包含输出字符串和(旧文件内容, 新文件内容)元组的元组
        """
        result: ToolResult | None = None
        try:
            # 调用文件编辑器执行操作
            result = self.file_editor(
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
            # 捕获并包装工具错误
            result = ToolResult(error=e.message)

        if result.error:
            return f'错误:\n{result.error}', (None, None)

        if not result.output:
            logger.warning(f'文件编辑器对 {path} 没有输出')
            return '', (None, None)

        return result.output, (result.old_content, result.new_content)

    def edit(self, action: FileEditAction) -> Observation:
        """
        使用OHEditor编辑文件。
        
        执行文件编辑操作，包括查看、编辑、替换等，
        并生成包含差异信息的观察结果。
        
        Args:
            action (FileEditAction): 文件编辑Action对象
            
        Returns:
            Observation: 文件编辑结果的观察对象
        """
        if not self._runtime_initialized:
            return ErrorObservation('Runtime未初始化')

        # 确保路径在工作区内
        file_path = self._sanitize_filename(action.path)

        # 检查是否为二进制文件
        if os.path.exists(file_path) and is_binary(file_path):
            return ErrorObservation('ERROR_BINARY_FILE')

        assert action.impl_source == FileEditSource.OH_ACI

        # 执行文件编辑操作
        result_str, (old_content, new_content) = self._execute_file_editor(
            command=action.command,
            path=file_path,
            file_text=action.file_text,
            old_str=action.old_str,
            new_str=action.new_str,
            insert_line=action.insert_line,
            enable_linting=False,  # 默认禁用代码检查
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

    async def call_tool_mcp(self, action: MCPAction) -> Observation:
        """
        MCP功能未在CLI runtime中实现。
        
        Args:
            action (MCPAction): MCP Action对象
            
        Returns:
            ErrorObservation: 说明功能未实现的错误观察结果
        """
        return ErrorObservation('MCP功能未在CLIRuntime中实现')

    @property
    def workspace_root(self) -> Path:
        """
        返回工作区根路径。
        
        Returns:
            Path: 工作区根目录的Path对象
        """
        return Path(os.path.abspath(self._workspace_path))

    def copy_to(self, host_src: str, sandbox_dest: str, recursive: bool = False):
        """
        从宿主机复制文件或目录到sandbox。
        
        支持文件和目录的复制，处理各种目标路径情况，
        包括目录合并和文件重命名。
        
        Args:
            host_src (str): 宿主机源路径
            sandbox_dest (str): sandbox目标路径
            recursive (bool, optional): 是否递归复制目录。默认为False
            
        Raises:
            RuntimeError: 当Runtime未初始化时
            FileNotFoundError: 当源路径不存在时
            RuntimeError: 当复制过程中发生意外错误时
        """
        if not self._runtime_initialized:
            raise RuntimeError('Runtime未初始化')
        if not os.path.exists(host_src):  # 源文件必须在宿主机上存在
            raise FileNotFoundError(f"源路径 '{host_src}' 不存在。")

        dest = self._sanitize_filename(sandbox_dest)

        try:
            # 情况1: 源是目录且递归复制
            if os.path.isdir(host_src) and recursive:
                # 目标是 dest / basename(host_src)
                final_target_dir = os.path.join(dest, os.path.basename(host_src))

                # 如果源和最终目标相同，跳过
                if os.path.realpath(host_src) == os.path.realpath(final_target_dir):
                    logger.debug(
                        '跳过递归复制: 源和目标相同。'
                    )
                    pass
                else:
                    # 确保final_target_dir的父目录存在
                    os.makedirs(dest, exist_ok=True)
                    shutil.copytree(host_src, final_target_dir, dirs_exist_ok=True)
                    # 原因: 将目录host_src复制到dest中。如果目标存在则合并。

            # 情况2: 源是文件
            elif os.path.isfile(host_src):
                final_target_file_path: str
                # 场景A: sandbox_dest明确是一个目录
                if os.path.isdir(dest) or (sandbox_dest.endswith(('/', os.sep))):
                    target_dir = dest
                    os.makedirs(target_dir, exist_ok=True)
                    final_target_file_path = os.path.join(
                        target_dir, os.path.basename(host_src)
                    )
                    # 原因: 将文件复制到指定目录中

                # 场景B: sandbox_dest可能是一个新目录 (例如 'new_dir')
                elif not os.path.exists(dest) and '.' not in os.path.basename(dest):
                    target_dir = dest
                    os.makedirs(target_dir, exist_ok=True)
                    final_target_file_path = os.path.join(
                        target_dir, os.path.basename(host_src)
                    )
                    # 原因: 创建'new_dir'并将文件复制到其中

                # 场景C: sandbox_dest是完整的文件路径
                else:
                    final_target_file_path = dest
                    os.makedirs(os.path.dirname(final_target_file_path), exist_ok=True)
                    # 原因: 将文件复制到特定路径，可能重命名

                shutil.copy2(host_src, final_target_file_path)

            else:  # 源不是有效的文件或目录
                raise FileNotFoundError(
                    f"源路径 '{host_src}' 不是有效的文件或目录。"
                )

        except FileNotFoundError as e:
            logger.error(f'复制期间文件未找到: {str(e)}')
            raise
        except shutil.SameFileError as e:
            # 我们可以在这里宽松处理，只忽略此错误
            logger.debug(
                f'跳过复制，因为源和目标相同: {str(e)}'
            )
            pass
        except Exception as e:
            logger.error(f'复制文件时发生意外错误: {str(e)}')
            raise RuntimeError(f'复制文件时发生意外错误: {str(e)}')

    def list_files(self, path: str | None = None) -> list[str]:
        """
        列出sandbox中的文件。
        
        列出指定路径或工作区根目录中的所有文件和目录。
        
        Args:
            path (str | None, optional): 要列出的路径，默认为工作区根目录
            
        Returns:
            list[str]: 文件和目录路径列表
            
        Raises:
            RuntimeError: 当Runtime未初始化时
        """
        if not self._runtime_initialized:
            raise RuntimeError('Runtime未初始化')

        if path is None:
            dir_path = self._workspace_path
        else:
            dir_path = self._sanitize_filename(path)

        try:
            if not os.path.exists(dir_path):
                return []

            if not os.path.isdir(dir_path):
                return [dir_path]

            # 列出目录中的文件
            return [os.path.join(dir_path, f) for f in os.listdir(dir_path)]
        except Exception as e:
            logger.error(f'列出文件时出错: {str(e)}')
            return []

    def copy_from(self, path: str) -> Path:
        """
        压缩sandbox中的所有文件并返回本地文件系统中的路径。
        
        创建包含指定路径下所有文件的zip文件，用于导出数据。
        
        Args:
            path (str): 要复制的sandbox路径
            
        Returns:
            Path: 临时zip文件的路径
            
        Raises:
            RuntimeError: 当Runtime未初始化时
            FileNotFoundError: 当路径不存在时
            RuntimeError: 当创建zip文件过程中发生错误时
        """
        if not self._runtime_initialized:
            raise RuntimeError('Runtime未初始化')

        source_path = self._sanitize_filename(path)

        if not os.path.exists(source_path):
            raise FileNotFoundError(f'路径未找到: {path}')

        # 创建临时zip文件
        temp_zip = tempfile.NamedTemporaryFile(suffix='.zip', delete=False)
        temp_zip.close()

        try:
            with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                if os.path.isdir(source_path):
                    # 添加目录中的所有文件
                    for root, _, files in os.walk(source_path):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, source_path)
                            zipf.write(file_path, arcname)
                else:
                    # 添加单个文件
                    zipf.write(source_path, os.path.basename(source_path))

            return Path(temp_zip.name)
        except Exception as e:
            logger.error(f'创建zip文件时出错: {str(e)}')
            raise RuntimeError(f'创建zip文件时出错: {str(e)}')

    def close(self) -> None:
        """
        关闭Runtime并清理资源。
        
        关闭PowerShell session（如果存在），重置初始化状态，
        并调用父类的关闭方法。
        """
        # 如果PowerShell session存在则清理它
        if self._powershell_session is not None:
            try:
                self._powershell_session.close()
                logger.debug('PowerShell session成功关闭。')
            except Exception as e:
                logger.warning(f'关闭PowerShell session时出错: {e}')
            finally:
                self._powershell_session = None

        self._runtime_initialized = False
        super().close()

    @classmethod
    async def delete(cls, conversation_id: str) -> None:
        """
        删除与对话相关的任何资源。
        
        查找并删除可能与此对话相关的临时目录。
        
        Args:
            conversation_id (str): 对话ID
        """
        # 查找可能与此对话相关的临时目录
        temp_dir = tempfile.gettempdir()
        prefix = f'openhands_workspace_{conversation_id}_'

        for item in os.listdir(temp_dir):
            if item.startswith(prefix):
                try:
                    path = os.path.join(temp_dir, item)
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                        logger.info(f'删除工作区目录: {path}')
                except Exception as e:
                    logger.error(f'删除工作区目录时出错: {str(e)}')

    @property
    def additional_agent_instructions(self) -> str:
        """
        获取为Agent提供的额外指令。
        
        Returns:
            str: 包含工作目录信息和环境说明的指令文本
        """
        return '\n\n'.join(
            [
                f'您的工作目录是 {self._workspace_path}。您只能读取和写入此目录中的文件。',
                "您正在直接在用户的机器上工作。在大多数情况下，工作环境已经设置好了。",
            ]
        )

    def get_mcp_config(
        self, extra_stdio_servers: list[MCPStdioServerConfig] | None = None
    ) -> MCPConfig:
        """
        获取MCP配置。
        
        Args:
            extra_stdio_servers (list[MCPStdioServerConfig] | None, optional): 额外的stdio服务器配置
            
        Returns:
            MCPConfig: MCP配置对象
            
        Note:
            TODO: 从本地文件加载MCP配置
        """
        # TODO: 从本地文件加载MCP配置
        return MCPConfig()

    def subscribe_to_shell_stream(
        self, callback: Callable[[str], None] | None = None
    ) -> bool:
        """
        订阅shell命令输出流。
        
        设置回调函数来接收shell命令的实时输出。
        
        Args:
            callback (Callable[[str], None] | None): 将被每行shell命令输出调用的函数。
                     如果为None，将移除任何现有的订阅。
                     
        Returns:
            bool: 始终返回True，表示订阅成功
        """
        self._shell_stream_callback = callback
        return True
