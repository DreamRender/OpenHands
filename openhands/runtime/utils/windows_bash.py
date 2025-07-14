"""Windows特定的PowerShell会话实现模块。

此模块提供了Windows特定的命令执行实现，使用pythonnet库与.NET PowerShell SDK
直接交互，在PowerShell会话中运行命令。这旨在提供一种比使用临时脚本文件更强健
和集成的方式来管理PowerShell进程。
"""

import os
import time
import traceback
from pathlib import Path
from threading import RLock

import pythonnet

from openhands.core.logger import openhands_logger as logger
from openhands.events.action import CmdRunAction
from openhands.events.observation import ErrorObservation
from openhands.events.observation.commands import (
    CmdOutputMetadata,
    CmdOutputObservation,
)
from openhands.runtime.utils.bash_constants import TIMEOUT_MESSAGE_TEMPLATE
from openhands.runtime.utils.windows_exceptions import DotNetMissingError
from openhands.utils.shutdown_listener import should_continue

# 尝试加载CoreCLR运行时
try:
    # 加载CoreCLR运行时，这是.NET Core的核心运行时
    pythonnet.load('coreclr')
    logger.info("Successfully called pythonnet.load('coreclr')")
    # 成功调用pythonnet.load('coreclr')

    # 现在pythonnet已初始化，导入clr和System
    try:
        import clr

        logger.debug(f'Imported clr module from: {clr.__file__}')
        # 从以下位置导入clr模块：{clr.__file__}
        
        # 在pythonnet初始化*之后*加载System程序集
        clr.AddReference('System')
        import System
    except Exception as clr_sys_ex:
        error_msg = 'Failed to import .NET components.'
        # 导入.NET组件失败
        details = str(clr_sys_ex)
        logger.error(f'{error_msg} Details: {details}')
        raise DotNetMissingError(error_msg, details)
except Exception as coreclr_ex:
    error_msg = 'Failed to load CoreCLR.'
    # 加载CoreCLR失败
    details = str(coreclr_ex)
    logger.error(f'{error_msg} Details: {details}')
    raise DotNetMissingError(error_msg, details)

# 仅在clr和System加载成功后才尝试加载PowerShell SDK程序集
ps_sdk_path = None
try:
    # 如果可用，优先选择PowerShell 7+（如有必要，请调整路径）
    pwsh7_path = (
        Path(os.environ.get('ProgramFiles', 'C:\\Program Files'))
        / 'PowerShell'
        / '7'
        / 'System.Management.Automation.dll'
    )
    if pwsh7_path.exists():
        ps_sdk_path = str(pwsh7_path)
        clr.AddReference(ps_sdk_path)
        logger.info(f'Loaded PowerShell SDK (Core): {ps_sdk_path}')
        # 已加载PowerShell SDK (Core)：{ps_sdk_path}
    else:
        # 回退到Windows自带的Windows PowerShell 5.1
        winps_path = (
            Path(os.environ.get('SystemRoot', 'C:\\Windows'))
            / 'System32'
            / 'WindowsPowerShell'
            / 'v1.0'
            / 'System.Management.Automation.dll'
        )
        if winps_path.exists():
            ps_sdk_path = str(winps_path)
            clr.AddReference(ps_sdk_path)
            logger.debug(f'Loaded PowerShell SDK (Desktop): {ps_sdk_path}')
            # 已加载PowerShell SDK (Desktop)：{ps_sdk_path}
        else:
            # 最后的手段：尝试按程序集名称加载（如果在GAC或路径中可能有效）
            clr.AddReference('System.Management.Automation')
            logger.info(
                'Attempted to load PowerShell SDK by name (System.Management.Automation)'
            )
            # 尝试按名称加载PowerShell SDK (System.Management.Automation)

    # 导入PowerShell相关的.NET类型
    from System.Management.Automation import JobState, PowerShell
    from System.Management.Automation.Language import Parser
    from System.Management.Automation.Runspaces import (
        RunspaceFactory,
        RunspaceState,
    )
except Exception as e:
    error_msg = 'Failed to load PowerShell SDK components.'
    # 加载PowerShell SDK组件失败
    details = f'{str(e)} (Path searched: {ps_sdk_path})'
    logger.error(f'{error_msg} Details: {details}')
    raise DotNetMissingError(error_msg, details)


class WindowsPowershellSession:
    """使用.NET SDK通过pythonnet管理持久PowerShell会话的类。

    此类允许在单个运行空间内执行命令，在调用之间保持状态
    （变量、当前目录）。处理基本超时并捕获输出/错误流。
    
    Attributes:
        work_dir (str): 会话的工作目录
        username (str | None): 执行用户名（目前被忽略）
        _cwd (str): 当前工作目录
        NO_CHANGE_TIMEOUT_SECONDS (int): 无变化超时秒数
        max_memory_mb (int | None): 最大内存限制（目前被忽略）
        active_job: 当前活动的PowerShell作业
        _job_lock (RLock): 作业操作的线程锁
        _last_job_output (str): 上次作业观察中返回的累积输出
        _last_job_error (list[str]): 上次作业观察中返回的累积错误
        runspace: PowerShell运行空间对象
        _closed (bool): 会话是否已关闭
        _initialized (bool): 会话是否已初始化
    """

    def __init__(
        self,
        work_dir: str,
        username: str | None = None,
        no_change_timeout_seconds: int = 30,
        max_memory_mb: int | None = None,
    ):
        """初始化PowerShell会话。

        Args:
            work_dir (str): 会话的起始工作目录
            username (str | None): 执行用户名（目前被忽略）。PowerShell SDK通常以当前用户身份运行
            no_change_timeout_seconds (int): 如果没有检测到输出变化的超时秒数（目前未完全实现）
            max_memory_mb (int | None): 进程的最大内存限制（目前被忽略）
        """
        # 早期初始化状态标志以防止在init失败时__del__中出现AttributeError
        self._closed = False
        self._initialized = False
        self.runspace = None  # 将runspace初始化为None

        # 检查SDK加载是否在模块导入期间失败
        if PowerShell is None:
            # 在导入期间记录了严重错误，在这里抛出异常以防止实例化
            error_msg = (
                'PowerShell SDK (System.Management.Automation.dll) could not be loaded.'
            )
            # PowerShell SDK (System.Management.Automation.dll) 无法加载
            logger.error(error_msg)
            raise DotNetMissingError(error_msg)

        # 设置基本属性
        self.work_dir = os.path.abspath(work_dir)
        self.username = username
        self._cwd = self.work_dir
        self.NO_CHANGE_TIMEOUT_SECONDS = no_change_timeout_seconds
        self.max_memory_mb = max_memory_mb  # 已存储，但尚未使用

        # 初始化作业相关属性
        self.active_job = None
        self._job_lock = RLock()
        self._last_job_output = ''  # 存储上次作业观察中返回的累积输出
        self._last_job_error: list[
            str
        ] = []  # 存储上次作业观察中返回的累积错误

        # 创建并打开持久运行空间
        try:
            # 考虑使用InitialSessionState获得更多控制（例如，执行策略）
            # iss = InitialSessionState.CreateDefault()
            # iss.ExecutionPolicy = Microsoft.PowerShell.ExecutionPolicy.Unrestricted # 需要导入Microsoft.PowerShell命名空间
            # self.runspace = RunspaceFactory.CreateRunspace(iss)
            self.runspace = RunspaceFactory.CreateRunspace()
            self.runspace.Open()
            # 在运行空间内设置初始工作目录
            self._set_initial_cwd()
            self._initialized = True  # 只有在成功初始化后才设置为True
            logger.info(f'PowerShell runspace created. Initial CWD set to: {self._cwd}')
            # PowerShell运行空间已创建。初始CWD设置为：{self._cwd}
        except Exception as e:
            logger.error(f'Failed to create or open PowerShell runspace: {e}')
            # 创建或打开PowerShell运行空间失败：{e}
            logger.error(traceback.format_exc())
            self.close()  # 如果init部分失败，确保清理
            raise RuntimeError(f'Failed to initialize PowerShell runspace: {e}')

    def _set_initial_cwd(self) -> None:
        """在运行空间中设置初始工作目录。"""
        ps = None
        try:
            ps = PowerShell.Create()
            ps.Runspace = self.runspace
            # 使用Set-Location命令设置工作目录
            ps.AddScript(f'Set-Location -Path "{self._cwd}"').Invoke()
            # 检查是否有错误
            if ps.Streams.Error:
                errors = '\n'.join([str(err) for err in ps.Streams.Error])
                logger.warning(f"Error setting initial CWD to '{self._cwd}': {errors}")
                # 设置初始CWD到'{self._cwd}'时出错：{errors}
                # 如果设置失败，确认实际的CWD
                self._confirm_cwd()
            else:
                logger.debug(f'Successfully set initial runspace CWD to {self._cwd}')
                # 成功将初始运行空间CWD设置为{self._cwd}
                # 可选：即使成功也确认CWD以确保健壮性
                # self._confirm_cwd()
        except Exception as e:
            logger.error(f'Exception setting initial CWD: {e}')
            # 设置初始CWD时出现异常：{e}
            logger.error(traceback.format_exc())
            # 即使设置抛出异常，也尝试确认CWD
            self._confirm_cwd()
        finally:
            if ps:
                ps.Dispose()

    def _confirm_cwd(self) -> None:
        """确认运行空间中的实际CWD并更新self._cwd。"""
        ps_confirm = None
        try:
            ps_confirm = PowerShell.Create()
            ps_confirm.Runspace = self.runspace
            # 使用Get-Location获取当前位置
            ps_confirm.AddScript('Get-Location')
            results = ps_confirm.Invoke()
            # 检查结果并更新CWD
            if results and results.Count > 0 and hasattr(results[0], 'Path'):
                actual_cwd = str(results[0].Path)
                if os.path.isdir(actual_cwd):
                    if actual_cwd != self._cwd:
                        logger.warning(
                            f'Runspace CWD ({actual_cwd}) differs from expected ({self._cwd}). Updating session CWD.'
                        )
                        # 运行空间CWD ({actual_cwd}) 与预期 ({self._cwd}) 不同。正在更新会话CWD
                        self._cwd = actual_cwd
                    else:
                        logger.debug(f'Confirmed runspace CWD is {self._cwd}')
                        # 确认运行空间CWD是{self._cwd}
                else:
                    logger.error(
                        f'Get-Location returned an invalid path: {actual_cwd}. Session CWD may be inaccurate.'
                    )
                    # Get-Location返回了无效路径：{actual_cwd}。会话CWD可能不准确
            elif ps_confirm.Streams.Error:
                errors = '\n'.join([str(err) for err in ps_confirm.Streams.Error])
                logger.error(f'Error confirming runspace CWD: {errors}')
                # 确认运行空间CWD时出错：{errors}
            else:
                logger.error('Could not confirm runspace CWD (No result or error).')
                # 无法确认运行空间CWD（无结果或错误）
        except Exception as e:
            logger.error(f'Exception confirming CWD: {e}')
            # 确认CWD时出现异常：{e}
        finally:
            if ps_confirm:
                ps_confirm.Dispose()

    @property
    def cwd(self) -> str:
        """获取会话的最后已知工作目录。
        
        Returns:
            str: 当前工作目录路径
        """
        return self._cwd

    def _run_ps_command(
        self, script: str, log_output: bool = True
    ) -> list[System.Management.Automation.PSObject]:
        """在运行空间中运行简单同步命令的帮助方法。
        
        Args:
            script (str): 要执行的PowerShell脚本
            log_output (bool): 是否记录输出日志
            
        Returns:
            list[System.Management.Automation.PSObject]: 命令执行结果
        """
        if log_output:
            logger.debug(f"Running PS command: '{script}'")
            # 运行PS命令：'{script}'
        ps = None
        results = []
        try:
            ps = PowerShell.Create()
            ps.Runspace = self.runspace
            ps.AddScript(script)
            results = ps.Invoke()
        except Exception as e:
            logger.error(f'Exception running script: {script}\n{e}')
            # 运行脚本时出现异常：{script}\n{e}
        finally:
            if ps:
                ps.Dispose()
        return results if results else []

    def _get_job_object(
        self, job_id: int | None
    ) -> System.Management.Automation.Job | None:
        """通过ID检索作业对象。
        
        Args:
            job_id (int | None): 作业ID
            
        Returns:
            System.Management.Automation.Job | None: 作业对象或None
        """
        script = f'Get-Job -Id {job_id}'
        results = self._run_ps_command(script, log_output=False)
        if results and len(results) > 0:
            potential_job_wrapper = results[0]
            try:
                underlying_job = potential_job_wrapper.BaseObject
                # 在返回之前对类似作业的属性进行基本检查
                _ = underlying_job.Id
                _ = underlying_job.JobStateInfo.State
                return underlying_job
            except AttributeError:
                logger.warning(f'Retrieved object is not a valid job. ID: {job_id}')
                # 检索的对象不是有效的作业。ID：{job_id}
                return None
        return None

    def _receive_job_output(
        self, job: System.Management.Automation.Job, keep: bool = False
    ) -> tuple[str, list[str]]:
        """从作业接收输出和错误。
        
        Args:
            job (System.Management.Automation.Job): 作业对象
            keep (bool): 是否保持输出（不从作业中清除）
            
        Returns:
            tuple[str, list[str]]: 输出和错误列表的元组
        """
        if not job:
            return '', []

        output_parts = []
        error_parts = []

        # 如果可用，直接从作业对象获取错误流
        try:
            current_job_obj = self._get_job_object(job.Id)
            if current_job_obj and current_job_obj.Error:
                error_records = current_job_obj.Error.ReadAll()
                if error_records:
                    error_parts.extend([str(e) for e in error_records])
        except Exception as read_err:
            logger.error(
                f'Failed to read job error stream directly for Job {job.Id}: {read_err}'
            )
            # 直接读取作业{job.Id}的错误流失败：{read_err}
            error_parts.append(f'[Direct Error Stream Read Exception: {read_err}]')

        # 为输出流运行Receive-Job
        keep_switch = '-Keep' if keep else ''
        script = f'Receive-Job -Job (Get-Job -Id {job.Id}) {keep_switch}'

        ps_receive = None
        try:
            ps_receive = PowerShell.Create()
            ps_receive.Runspace = self.runspace
            ps_receive.AddScript(script)

            # 收集输出
            results = ps_receive.Invoke()
            if results:
                output_parts = [str(r) for r in results]

            # 收集来自Receive-Job命令的错误
            if ps_receive.Streams.Error:
                receive_job_errors = [str(e) for e in ps_receive.Streams.Error]
                logger.warning(
                    f'Errors during Receive-Job for Job ID {job.Id}: {receive_job_errors}'
                )
                # 作业ID {job.Id}的Receive-Job期间出错：{receive_job_errors}
                error_parts.extend(receive_job_errors)

        except Exception as e:
            logger.error(f'Exception during Receive-Job for Job ID {job.Id}: {e}')
            # 作业ID {job.Id}的Receive-Job期间出现异常：{e}
            error_parts.append(f'[Receive-Job Exception: {e}]')
        finally:
            if ps_receive:
                ps_receive.Dispose()

        # 合并输出和错误
        final_combined_output = '\n'.join(output_parts)
        return final_combined_output, error_parts

    def _stop_active_job(self) -> CmdOutputObservation | ErrorObservation:
        """停止活动作业，收集最终输出，并清理。
        
        Returns:
            CmdOutputObservation | ErrorObservation: 命令输出观察或错误观察
        """
        with self._job_lock:
            job = self.active_job
            if not job:
                return ErrorObservation(
                    content='ERROR: No previous running command to interact with.'
                )
                # 错误：没有之前运行的命令可以交互

            job_id = job.Id  # type: ignore[unreachable]
            logger.info(f'Attempting to stop job ID: {job_id} via C-c.')
            # 尝试通过C-c停止作业ID：{job_id}

            # 尝试优雅停止
            stop_script = f'Stop-Job -Job (Get-Job -Id {job_id})'
            self._run_ps_command(stop_script)

            # 给进程时间可能打印关闭消息
            time.sleep(0.5)

            # 获取最终输出和错误
            final_output, final_errors = self._receive_job_output(job, keep=False)

            combined_output = final_output
            combined_errors = final_errors

            # 停止后检查作业状态
            final_job = self._get_job_object(job_id)
            final_state = final_job.JobStateInfo.State if final_job else JobState.Failed

            logger.info(f'Job {job_id} final state after stop attempt: {final_state}')
            # 作业{job_id}停止尝试后的最终状态：{final_state}

            # 清理作业
            remove_script = f'Remove-Job -Job (Get-Job -Id {job_id})'
            self._run_ps_command(remove_script)

            # 清除活动作业引用
            self.active_job = None

            # 构建结果
            output_builder = [combined_output] if combined_output else []
            if combined_errors:
                output_builder.append('\n[ERROR STREAM]')
                output_builder.extend(combined_errors)

            # 确定退出代码 - 如果是Stopped/Completed则为0，否则为1
            exit_code = (
                0 if final_state in [JobState.Stopped, JobState.Completed] else 1
            )

            final_content = '\n'.join(output_builder).strip()

            current_cwd = self._cwd
            python_safe_cwd = current_cwd.replace('\\\\', '\\\\\\\\')
            metadata = CmdOutputMetadata(
                exit_code=exit_code, working_dir=python_safe_cwd
            )
            metadata.suffix = f'\n[The command completed with exit code {exit_code}. CTRL+C was sent.]'
            # 命令完成，退出代码为{exit_code}。已发送CTRL+C

            return CmdOutputObservation(
                content=final_content,
                command='C-c',
                metadata=metadata,
            )

    def _check_active_job(
        self, timeout_seconds: int
    ) -> CmdOutputObservation | ErrorObservation:
        """检查活动作业的新输出和状态，等待最多timeout_seconds秒。
        
        Args:
            timeout_seconds (int): 超时秒数
            
        Returns:
            CmdOutputObservation | ErrorObservation: 命令输出观察或错误观察
        """
        with self._job_lock:
            if not self.active_job:
                return ErrorObservation(
                    content='ERROR: No previous running command to retrieve logs from.'
                )
                # 错误：没有之前运行的命令可以检索日志

            job_id = self.active_job.Id  # type: ignore[unreachable]
            logger.info(
                f'Checking active job ID: {job_id} for new output (timeout={timeout_seconds}s).'
            )
            # 检查活动作业ID：{job_id}是否有新输出（超时={timeout_seconds}秒）

            # 初始化监控循环变量
            start_time = time.monotonic()
            monitoring_loop_finished = False
            accumulated_new_output_builder = []
            accumulated_new_errors = []
            exit_code = -1  # 假设正在运行
            final_state = JobState.Running
            latest_cumulative_output = self._last_job_output
            latest_cumulative_errors = list(self._last_job_error)

            # 作业监控循环
            while not monitoring_loop_finished:
                # 检查关闭信号
                if not should_continue():
                    logger.warning('Shutdown signal received during job check.')
                    # 在作业检查期间收到关闭信号
                    monitoring_loop_finished = True
                    continue

                # 检查超时
                elapsed_seconds = time.monotonic() - start_time
                if elapsed_seconds > timeout_seconds:
                    logger.warning(f'Job check timed out after {timeout_seconds}s.')
                    # 作业检查在{timeout_seconds}秒后超时
                    monitoring_loop_finished = True
                    continue

                # 获取当前作业对象
                current_job_obj = self._get_job_object(job_id)
                if not current_job_obj:
                    logger.error(f'Job {job_id} object disappeared during check.')
                    # 作业{job_id}对象在检查期间消失
                    accumulated_new_errors.append('[Job object lost during check]')
                    monitoring_loop_finished = True
                    exit_code = 1
                    final_state = JobState.Failed
                    if self.active_job and self.active_job.Id == job_id:
                        self.active_job = None
                    continue

                # 使用keep=True轮询输出（返回累积输出/错误）
                polled_cumulative_output, polled_cumulative_errors = (
                    self._receive_job_output(current_job_obj, keep=True)
                )

                # 检测自上次轮询以来的新输出
                new_output_detected = ''
                if polled_cumulative_output != latest_cumulative_output:
                    if polled_cumulative_output.startswith(latest_cumulative_output):
                        new_output_detected = polled_cumulative_output[
                            len(latest_cumulative_output) :
                        ]
                    else:
                        logger.warning(
                            f'Job {job_id} check: Cumulative output changed unexpectedly'
                        )
                        # 作业{job_id}检查：累积输出意外更改
                        new_output_detected = polled_cumulative_output.removeprefix(
                            self._last_job_output
                        )

                    if new_output_detected.strip():
                        accumulated_new_output_builder.append(
                            new_output_detected.strip()
                        )

                # 检测新错误
                latest_cumulative_errors_set = set(latest_cumulative_errors)
                new_errors_detected = [
                    e
                    for e in polled_cumulative_errors
                    if e not in latest_cumulative_errors_set
                ]
                if new_errors_detected:
                    accumulated_new_errors.extend(new_errors_detected)

                # 更新最新累积状态
                latest_cumulative_output = polled_cumulative_output
                latest_cumulative_errors = polled_cumulative_errors

                # 检查作业状态
                current_state = current_job_obj.JobStateInfo.State
                if current_state not in [JobState.Running, JobState.NotStarted]:
                    logger.info(
                        f'Job {job_id} finished check loop with state: {current_state}'
                    )
                    # 作业{job_id}完成检查循环，状态：{current_state}
                    monitoring_loop_finished = True
                    final_state = current_state
                    continue

                # 防止忙等待
                time.sleep(0.1)

            # 循环完成后处理结果
            is_finished = final_state not in [JobState.Running, JobState.NotStarted]
            final_content = '\n'.join(accumulated_new_output_builder).strip()
            final_errors = list(accumulated_new_errors)

            if is_finished:
                logger.info(f'Job {job_id} has finished. Collecting final output.')
                # 作业{job_id}已完成。收集最终输出
                final_job_obj = self._get_job_object(job_id)
                if final_job_obj:
                    # 使用keep=False进行最终接收以消费剩余输出
                    final_cumulative_output, final_cumulative_errors = (
                        self._receive_job_output(final_job_obj, keep=False)
                    )

                    # 检查最终块中的新输出
                    final_new_output_chunk = ''
                    if final_cumulative_output.startswith(latest_cumulative_output):
                        final_new_output_chunk = final_cumulative_output[
                            len(latest_cumulative_output) :
                        ]
                    elif final_cumulative_output:
                        final_new_output_chunk = final_cumulative_output.removeprefix(
                            self._last_job_output
                        )

                    if final_new_output_chunk.strip():
                        final_content = '\n'.join(
                            filter(
                                None, [final_content, final_new_output_chunk.strip()]
                            )
                        )

                    # 检查最终块中的新错误
                    latest_cumulative_errors_set = set(latest_cumulative_errors)
                    new_final_errors = [
                        e
                        for e in final_cumulative_errors
                        if e not in latest_cumulative_errors_set
                    ]
                    if new_final_errors:
                        final_errors.extend(new_final_errors)

                    # 根据状态确定退出代码
                    exit_code = 0 if final_state == JobState.Completed else 1

                    # 清理作业
                    remove_script = f'Remove-Job -Job (Get-Job -Id {job_id})'
                    self._run_ps_command(remove_script)
                    if self.active_job and self.active_job.Id == job_id:
                        self.active_job = None
                    self._last_job_output = ''
                    self._last_job_error = []
                else:
                    logger.warning(f'Could not get final job object {job_id}')
                    # 无法获取最终作业对象{job_id}
                    exit_code = 1
                    if self.active_job and self.active_job.Id == job_id:
                        self.active_job = None
                    self._last_job_output = ''
                    self._last_job_error = []
            else:
                # 使用最新累积值更新持久状态
                self._last_job_output = latest_cumulative_output
                self._last_job_error = list(set(latest_cumulative_errors))

            # 将错误附加到最终内容
            if final_errors:
                error_stream_text = '\n'.join(final_errors)
                if final_content:
                    final_content += f'\n[ERROR STREAM]\n{error_stream_text}'
                else:
                    final_content = f'[ERROR STREAM]\n{error_stream_text}'
                # 如果发生错误，确保退出代码为非零
                if exit_code == 0 and final_state != JobState.Completed:
                    exit_code = 1

            # 构建元数据
            current_cwd = self._cwd
            python_safe_cwd = current_cwd.replace('\\\\', '\\\\\\\\')
            metadata = CmdOutputMetadata(
                exit_code=exit_code, working_dir=python_safe_cwd
            )
            metadata.prefix = '[Below is the output of the previous command.]\n'
            # 以下是之前命令的输出

            if is_finished:
                metadata.suffix = (
                    f'\n[The command completed with exit code {exit_code}.]'
                )
                # 命令完成，退出代码为{exit_code}
            else:
                metadata.suffix = (
                    f'\n[The command timed out after {timeout_seconds} seconds. '
                    f'{TIMEOUT_MESSAGE_TEMPLATE}]'
                )
                # 命令在{timeout_seconds}秒后超时

            return CmdOutputObservation(
                content=final_content,
                command='',
                metadata=metadata,
            )

    def _get_current_cwd(self) -> str:
        """从运行空间获取当前工作目录。
        
        Returns:
            str: 当前工作目录路径
        """
        # 使用帮助程序运行Get-Location
        results = self._run_ps_command('Get-Location')

        # 添加更详细的检查日志
        if results and results.Count > 0:  # type: ignore[attr-defined]
            first_result = results[0]
            has_path_attr = hasattr(first_result, 'Path')

            if has_path_attr:
                # 如果hasattr为True，原始逻辑在这里恢复
                fetched_cwd = str(first_result.Path)
                if os.path.isdir(fetched_cwd):
                    if fetched_cwd != self._cwd:
                        logger.info(
                            f"_get_current_cwd: Fetched CWD '{fetched_cwd}' differs from cached '{self._cwd}'. Updating cache."
                        )
                        # _get_current_cwd：获取的CWD '{fetched_cwd}' 与缓存的 '{self._cwd}' 不同。正在更新缓存
                        self._cwd = fetched_cwd
                    return self._cwd
                else:
                    logger.warning(
                        f"_get_current_cwd: Path '{fetched_cwd}' is not a valid directory. Returning cached CWD: {self._cwd}"
                    )
                    # _get_current_cwd：路径 '{fetched_cwd}' 不是有效目录。返回缓存的CWD：{self._cwd}
                    return self._cwd
            else:
                # 处理缺少Path属性的情况（例如，意外的对象类型）
                # 也许路径在BaseObject中？
                try:
                    base_object = first_result.BaseObject
                    if hasattr(base_object, 'Path'):
                        fetched_cwd = str(base_object.Path)
                        if os.path.isdir(fetched_cwd):
                            if fetched_cwd != self._cwd:
                                logger.info(
                                    f"_get_current_cwd: Fetched CWD '{fetched_cwd}' (from BaseObject) differs from cached '{self._cwd}'. Updating cache."
                                )
                                # _get_current_cwd：获取的CWD '{fetched_cwd}' （来自BaseObject）与缓存的 '{self._cwd}' 不同。正在更新缓存
                                self._cwd = fetched_cwd
                            return self._cwd
                        else:
                            logger.warning(
                                f"_get_current_cwd: Path '{fetched_cwd}' (from BaseObject) is not a valid directory. Returning cached CWD: {self._cwd}"
                            )
                            # _get_current_cwd：路径 '{fetched_cwd}' （来自BaseObject）不是有效目录。返回缓存的CWD：{self._cwd}
                            return self._cwd
                    else:
                        logger.error(
                            f'_get_current_cwd: BaseObject also lacks Path attribute. Cannot determine CWD from result: {first_result}'
                        )
                        # _get_current_cwd：BaseObject也缺少Path属性。无法从结果确定CWD：{first_result}
                        return self._cwd  # 返回缓存
                except AttributeError as ae:
                    logger.error(
                        f'_get_current_cwd: Error accessing BaseObject or its Path: {ae}. Result: {first_result}'
                    )
                    # _get_current_cwd：访问BaseObject或其Path时出错：{ae}。结果：{first_result}
                    return self._cwd  # 返回缓存
                except Exception as ex:
                    logger.error(
                        f'_get_current_cwd: Unexpected error checking BaseObject: {ex}. Result: {first_result}'
                    )
                    # _get_current_cwd：检查BaseObject时出现意外错误：{ex}。结果：{first_result}
                    return self._cwd  # 返回缓存

        # 如果_run_ps_command返回[]或results.Count为0，则执行此路径
        logger.error(
            f'_get_current_cwd: No valid results received from Get-Location call. Returning cached CWD: {self._cwd}'
        )
        # _get_current_cwd：从Get-Location调用中没有收到有效结果。返回缓存的CWD：{self._cwd}
        return self._cwd

    def execute(self, action: CmdRunAction) -> CmdOutputObservation | ErrorObservation:
        """执行命令，可能作为PowerShell后台作业用于长时间运行的任务。
        
        与bash.py在命令执行和消息方面的行为保持一致。

        Args:
            action (CmdRunAction): 命令执行动作

        Returns:
            CmdOutputObservation | ErrorObservation: 命令输出观察或错误观察
        """
        # 检查会话状态
        if not self._initialized or self._closed:
            return ErrorObservation(
                content='PowerShell session is not initialized or has been closed.'
            )
            # PowerShell会话未初始化或已关闭

        # 解析命令参数
        command = action.command.strip()
        timeout_seconds = action.timeout or 60  # 默认60秒硬超时
        is_input = action.is_input  # 检查是否为输入

        # 检测是否为后台命令（以&结尾）
        run_in_background = False
        if command.endswith('&'):
            run_in_background = True
            command = command[:-1].strip()  # 移除&和额外空格
            logger.info(f"Detected background command: '{command}'")
            # 检测到后台命令：'{command}'

        logger.info(
            f"Received command: '{command}', Timeout: {timeout_seconds}s, is_input: {is_input}, background: {run_in_background}"
        )
        # 收到命令：'{command}'，超时：{timeout_seconds}秒，is_input：{is_input}，后台：{run_in_background}

        # 简化的活动作业处理（与bash.py对齐）
        with self._job_lock:
            if self.active_job:
                active_job_obj = self._get_job_object(self.active_job.Id)  # type: ignore[unreachable]
                job_is_finished = False
                final_output = ''  # 在条件赋值之前初始化
                final_errors = []  # 在条件赋值之前初始化
                current_job_state = None  # 初始化
                finished_job_id = (
                    self.active_job.Id
                )  # 在可能清除self.active_job之前存储ID

                if active_job_obj:
                    current_job_state = active_job_obj.JobStateInfo.State
                    if current_job_state not in [JobState.Running, JobState.NotStarted]:
                        job_is_finished = True
                        logger.info(
                            f'Active job {finished_job_id} was finished ({current_job_state}) before receiving new command. Cleaning up.'
                        )
                        # 活动作业{finished_job_id}在收到新命令之前已完成（{current_job_state}）。正在清理
                        # 在此处分配最终输出/错误
                        final_output, final_errors = self._receive_job_output(
                            active_job_obj, keep=False
                        )  # 消费最终输出
                        remove_script = (
                            f'Remove-Job -Job (Get-Job -Id {finished_job_id})'
                        )
                        self._run_ps_command(remove_script)
                        # 重置持久状态
                        self._last_job_output = ''
                        self._last_job_error = []
                        self.active_job = None
                    # else: 作业仍在运行，job_is_finished保持False
                else:
                    # 作业对象消失，认为它已完成/消失
                    logger.warning(
                        f'Could not retrieve active job object {finished_job_id}. Assuming finished and clearing.'
                    )
                    # 无法检索活动作业对象{finished_job_id}。假设已完成并清除
                    job_is_finished = True
                    current_job_state = (
                        JobState.Failed
                    )  # 如果对象消失，假设失败
                    # 在此处分配最终输出/错误
                    final_output = ''  # 无法检索输出
                    final_errors = ['[ERROR: Job object disappeared during check]']
                    # 重置持久状态
                    self._last_job_output = ''
                    self._last_job_error = []
                    self.active_job = None

                # 如果作业在此检查期间被发现已完成，现在返回其最终状态
                if job_is_finished:
                    # 计算最终新输出/错误
                    new_output = final_output.removeprefix(
                        self._last_job_output
                    )  # final_output来自keep=False
                    last_error_set = set(
                        self._last_job_error
                    )  # 使用重置*之前*的状态
                    new_errors = [e for e in final_errors if e not in last_error_set]

                    # 使用清理期间捕获的状态构建并返回已完成作业的观察
                    exit_code = 0 if current_job_state == JobState.Completed else 1
                    output_builder = [new_output] if new_output else []
                    if new_errors:
                        output_builder.append('\\n[ERROR STREAM]')
                        output_builder.extend(new_errors)
                    content_for_return = '\\n'.join(output_builder).strip()

                    current_cwd = self._cwd  # 使用缓存的CWD，因为作业已消失
                    python_safe_cwd = current_cwd.replace('\\\\', '\\\\\\\\')
                    metadata = CmdOutputMetadata(
                        exit_code=exit_code, working_dir=python_safe_cwd
                    )
                    # 表示此输出来自刚刚完成的*之前*命令
                    metadata.prefix = (
                        '[Below is the output of the previous command.]\\n'
                    )
                    # 以下是之前命令的输出
                    metadata.suffix = (
                        f'\\n[The command completed with exit code {exit_code}.]'
                    )
                    # 命令完成，退出代码为{exit_code}
                    logger.info(
                        f"Returning final output for job {finished_job_id} which finished before command '{command}' was processed."
                    )
                    # 返回作业{finished_job_id}的最终输出，该作业在处理命令'{command}'之前已完成
                    return CmdOutputObservation(
                        content=content_for_return,
                        command=action.command,  # 触发此检查的命令（例如，''）
                        metadata=metadata,
                    )

                # 如果作业未完成，检查传入的命令
                # 此块仅在作业仍处于活动状态时运行（job_is_finished为False）
                if not job_is_finished:
                    if command == '':
                        logger.info(
                            'Received empty command while job running. Checking job status.'
                        )
                        # 作业运行时收到空命令。检查作业状态
                        # 将来自空命令动作的超时传递给_check_active_job
                        return self._check_active_job(timeout_seconds)
                    elif command == 'C-c':
                        logger.info('Received C-c while job running. Stopping job.')
                        # 作业运行时收到C-c。停止作业
                        return self._stop_active_job()
                    elif is_input:
                        # PowerShell会话不直接支持像bash.py/tmux那样的stdin注入
                        # 这需要不同的方法（例如，命名管道或特定的cmdlet）
                        # 目前，返回错误表示此限制
                        logger.warning(
                            f"Received input command '{command}' while job active, but direct input injection is not supported in this implementation."
                        )
                        # 作业活动时收到输入命令'{command}'，但此实现不支持直接输入注入
                        # 获取自上次观察以来的*新*输出以提供上下文
                        cumulative_output, cumulative_errors = self._receive_job_output(
                            self.active_job, keep=True
                        )
                        new_output = cumulative_output.removeprefix(
                            self._last_job_output
                        )
                        last_error_set = set(self._last_job_error)
                        new_errors = [
                            e for e in cumulative_errors if e not in last_error_set
                        ]
                        output_builder = [new_output] if new_output else []
                        if new_errors:
                            output_builder.append('\\n[ERROR STREAM]')
                            output_builder.extend(new_errors)
                        # 更新持久状态
                        # 即使输入失败，用户现在也看到了此输出
                        self._last_job_output = cumulative_output
                        self._last_job_error = list(set(cumulative_errors))
                        current_cwd = self._cwd
                        python_safe_cwd = current_cwd.replace('\\\\', '\\\\\\\\')
                        metadata = CmdOutputMetadata(
                            exit_code=-1, working_dir=python_safe_cwd
                        )  # 仍在运行
                        metadata.prefix = (
                            '[Below is the output of the previous command.]\\n'
                        )
                        metadata.suffix = (
                            f"\\n[Your input command '{command}' was NOT processed. Direct input to running processes (is_input=True) "
                            'is not supported by this PowerShell session implementation. You can use C-c to stop the process.]'
                        )
                        # 您的输入命令'{command}'未被处理。此PowerShell会话实现不支持对运行中进程的直接输入（is_input=True）。您可以使用C-c停止进程
                        return CmdOutputObservation(
                            content='\\n'.join(output_builder).strip(),
                            command=action.command,
                            metadata=metadata,
                        )

                    else:
                        # 作业运行时到达任何其他命令 -> 拒绝它（bash.py行为）
                        logger.warning(
                            f"Received new command '{command}' while job {self.active_job.Id} is active. New command NOT executed."
                        )
                        # 作业{self.active_job.Id}活动时收到新命令'{command}'。新命令未执行
                        # 获取自上次观察以来的*新*输出以提供上下文
                        cumulative_output, cumulative_errors = self._receive_job_output(
                            self.active_job, keep=True
                        )
                        new_output = cumulative_output.removeprefix(
                            self._last_job_output
                        )
                        last_error_set = set(self._last_job_error)
                        new_errors = [
                            e for e in cumulative_errors if e not in last_error_set
                        ]
                        output_builder = [new_output] if new_output else []
                        if new_errors:
                            output_builder.append('\\n[ERROR STREAM]')
                            output_builder.extend(new_errors)
                        # 更新持久状态
                        # 即使命令失败，用户现在也看到了此输出
                        self._last_job_output = cumulative_output
                        self._last_job_error = list(set(cumulative_errors))

                        current_cwd = self._cwd  # 使用缓存的CWD
                        python_safe_cwd = current_cwd.replace('\\\\', '\\\\\\\\')
                        metadata = CmdOutputMetadata(
                            exit_code=-1, working_dir=python_safe_cwd
                        )  # 退出代码-1表示仍在运行
                        metadata.prefix = (
                            '[Below is the output of the previous command.]\n'
                        )
                        metadata.suffix = (
                            f'\n[Your command "{command}" is NOT executed. '
                            f'The previous command is still running - You CANNOT send new commands until the previous command is completed. '
                            'By setting `is_input` to `true`, you can interact with the current process: '
                            "You may wait longer to see additional output of the previous command by sending empty command '', "
                            'send other commands to interact with the current process, '
                            'or send keys ("C-c", "C-z", "C-d") to interrupt/kill the previous command before sending your new command.]'
                        )
                        # 您的命令"{command}"未被执行。之前的命令仍在运行 - 在之前的命令完成之前您无法发送新命令。通过将`is_input`设置为`true`，您可以与当前进程交互：您可以通过发送空命令''等待更长时间以查看之前命令的其他输出，发送其他命令与当前进程交互，或发送键（"C-c"、"C-z"、"C-d"）以在发送新命令之前中断/终止之前的命令

                        return CmdOutputObservation(
                            content='\\n'.join(output_builder).strip(),
                            command=action.command,  # 返回尝试的命令
                            metadata=metadata,
                        )
            # 活动作业处理结束

        # 如果我们到达这里，没有活动作业

        # 没有活动作业时处理空命令
        if command == '':
            logger.warning('Received empty command string (no active job).')
            # 收到空命令字符串（无活动作业）
            current_cwd = self._get_current_cwd()  # 以防万一更新CWD
            python_safe_cwd = current_cwd.replace('\\\\', '\\\\\\\\')
            metadata = CmdOutputMetadata(exit_code=0, working_dir=python_safe_cwd)
            # 与bash.py对齐错误消息
            error_content = 'ERROR: No previous running command to retrieve logs from.'
            # 错误：没有之前运行的命令可以检索日志
            logger.warning(
                f'Returning specific error message for empty command: {error_content}'
            )
            # 为空命令返回特定错误消息：{error_content}
            # 不需要额外的后缀
            # metadata.suffix = f"\n[Empty command received (no active job). CWD: {metadata.working_dir}]"
            return CmdOutputObservation(
                content=error_content, command='', metadata=metadata
            )

        # 没有活动作业时处理C-*
        if command.startswith('C-') and len(command) == 3:
            logger.warning(
                f'Received control character command: {command}. Not supported when no job active.'
            )
            # 收到控制字符命令：{command}。无活动作业时不支持
            current_cwd = self._cwd  # 使用缓存的CWD
            python_safe_cwd = current_cwd.replace('\\\\', '\\\\\\\\')
            # 与bash.py对齐错误消息（没有运行的命令可以交互）
            return ErrorObservation(
                content='ERROR: No previous running command to interact with.'
            )
            # 错误：没有之前运行的命令可以交互

        # 使用PowerShell Parser验证命令结构
        # （保持现有的验证逻辑，因为它是PowerShell特定的且有用）
        parse_errors = None
        statements = None
        try:
            # 解析输入命令字符串
            ast, _, parse_errors = Parser.ParseInput(command, None)
            if parse_errors and parse_errors.Length > 0:
                error_messages = '\n'.join(
                    [
                        f'  - {err.Message} at Line {err.Extent.StartLineNumber}, Column {err.Extent.StartColumnNumber}'
                        for err in parse_errors
                    ]
                )
                logger.error(f'Command failed PowerShell parsing:\n{error_messages}')
                # 命令PowerShell解析失败：\n{error_messages}
                return ErrorObservation(
                    content=(
                        f'ERROR: Command could not be parsed by PowerShell.\n'
                        f'Syntax errors detected:\n{error_messages}'
                    )
                )
                # 错误：命令无法被PowerShell解析。检测到语法错误
            statements = ast.EndBlock.Statements
            if statements.Count > 1:
                logger.error(
                    f'Detected {statements.Count} statements in the command. Only one is allowed.'
                )
                # 在命令中检测到{statements.Count}个语句。只允许一个
                # 与bash.py对齐错误消息
                splited_cmds = [
                    str(s.Extent.Text) for s in statements
                ]  # 尝试获取文本
                return ErrorObservation(
                    content=(
                        f'ERROR: Cannot execute multiple commands at once.\n'
                        f'Please run each command separately OR chain them into a single command via PowerShell operators (e.g., ; or |).\n'
                        f'Detected commands:\n{"\n".join(f"({i + 1}) {cmd}" for i, cmd in enumerate(splited_cmds))}'
                    )
                )
                # 错误：无法同时执行多个命令。请分别运行每个命令或通过PowerShell运算符（例如；或|）将它们链接成单个命令
            elif statements.Count == 0 and not command.strip().startswith('#'):
                logger.warning(
                    'Received command that resulted in zero executable statements (likely whitespace or comment).'
                )
                # 收到导致零个可执行语句的命令（可能是空格或注释）
                # 如果解析为空，则视为空命令
                return CmdOutputObservation(
                    content='',
                    command=command,
                    metadata=CmdOutputMetadata(exit_code=0, working_dir=self._cwd),
                )

        except Exception as parse_ex:
            logger.error(f'Exception during PowerShell command parsing: {parse_ex}')
            # PowerShell命令解析期间出现异常：{parse_ex}
            logger.error(traceback.format_exc())
            return ErrorObservation(
                content=f'ERROR: An exception occurred while parsing the command: {parse_ex}'
            )
            # 错误：解析命令时发生异常：{parse_ex}
        # 验证结束

        # 同步执行路径（用于CWD命令）
        if statements and statements.Count == 1:
            statement = statements[0]
            try:
                from System.Management.Automation.Language import (
                    CommandAst,
                    PipelineAst,
                )

                # 检查PipelineAst
                if isinstance(statement, PipelineAst):
                    pipeline_elements = statement.PipelineElements
                    if (
                        pipeline_elements
                        and pipeline_elements.Count == 1
                        and isinstance(pipeline_elements[0], CommandAst)
                    ):
                        command_ast = pipeline_elements[0]
                        command_name = command_ast.GetCommandName()
                        if command_name and command_name.lower() in [
                            'set-location',
                            'cd',
                            'push-location',
                            'pop-location',
                        ]:
                            logger.info(
                                f'execute: Identified CWD command via PipelineAst: {command_name}'
                            )
                            # execute：通过PipelineAst识别CWD命令：{command_name}
                            # 运行命令并准备适当的CmdOutputObservation
                            ps_results = self._run_ps_command(command)
                            # 在CWD命令后获取当前工作目录
                            current_cwd = self._get_current_cwd()
                            python_safe_cwd = current_cwd.replace('\\\\', '\\\\\\\\')

                            # 如果有结果，将其转换为字符串输出
                            output = (
                                '\n'.join([str(r) for r in ps_results])
                                if ps_results
                                else ''
                            )

                            return CmdOutputObservation(
                                content=output,
                                command=command,
                                metadata=CmdOutputMetadata(
                                    exit_code=0, working_dir=python_safe_cwd
                                ),
                            )
                # 检查直接CommandAst
                elif isinstance(statement, CommandAst):
                    command_name = statement.GetCommandName()
                    if command_name and command_name.lower() in [
                        'set-location',
                        'cd',
                        'push-location',
                        'pop-location',
                    ]:
                        logger.info(
                            f'execute: Identified CWD command via direct CommandAst: {command_name}'
                        )
                        # execute：通过直接CommandAst识别CWD命令：{command_name}
                        # 运行命令并准备适当的CmdOutputObservation
                        ps_results = self._run_ps_command(command)
                        # 在CWD命令后获取当前工作目录
                        current_cwd = self._get_current_cwd()
                        python_safe_cwd = current_cwd.replace('\\\\', '\\\\\\\\')

                        # 如果有结果，将其转换为字符串输出
                        output = (
                            '\n'.join([str(r) for r in ps_results])
                            if ps_results
                            else ''
                        )

                        return CmdOutputObservation(
                            content=output,
                            command=command,
                            metadata=CmdOutputMetadata(
                                exit_code=0, working_dir=python_safe_cwd
                            ),
                        )
            except ImportError as imp_err:
                logger.error(
                    f'execute: Failed to import CommandAst: {imp_err}. Cannot check for CWD commands.'
                )
                # execute：导入CommandAst失败：{imp_err}。无法检查CWD命令
            except Exception as ast_err:
                logger.error(f'execute: Error checking command AST: {ast_err}')
                # execute：检查命令AST时出错：{ast_err}

        # 异步执行路径（用于非CWD命令）
        logger.info(
            f"execute: Entering asynchronous execution path for command: '{command}'"
        )
        # execute：进入命令的异步执行路径：'{command}'

        # 将命令作为新的异步作业启动
        # 为新作业重置状态
        self._last_job_output = ''
        self._last_job_error = []

        # 初始化作业执行变量
        ps_start = None
        job = None
        output_builder = []
        all_errors = []
        exit_code = 1
        timed_out = False
        job_start_failed = False
        job_id = None

        try:
            ps_start = PowerShell.Create()
            ps_start.Runspace = self.runspace
            escaped_cwd = self._cwd.replace("'", "''")
            # 在命令后检查$?。如果为false，则退出1
            start_job_script = f"Start-Job -ScriptBlock {{ Set-Location '{escaped_cwd}'; {command}; if (-not $?) {{ exit 1 }} }}"

            logger.info(f'Starting command as PowerShell job: {command}')
            # 将命令作为PowerShell作业启动：{command}
            ps_start.AddScript(start_job_script)
            start_results = ps_start.Invoke()

            # 检查Start-Job执行期间的错误
            if ps_start.Streams.Error:
                errors = [str(e) for e in ps_start.Streams.Error]
                logger.error(f'Errors during Start-Job execution: {errors}')
                # Start-Job执行期间出错：{errors}
                all_errors.extend(errors)

            # 获取最新的作业
            ps_get = PowerShell.Create()
            ps_get.Runspace = self.runspace
            get_job_script = 'Get-Job | Sort-Object -Property Id -Descending | Select-Object -First 1'
            ps_get.AddScript(get_job_script)
            get_results = ps_get.Invoke()

            # 检查获取作业期间的错误
            if ps_get.Streams.Error:
                errors = [str(e) for e in ps_get.Streams.Error]
                logger.error(f'Errors getting latest job: {errors}')
                # 获取最新作业时出错：{errors}
                all_errors.extend(errors)
                job_start_failed = True

            # 验证作业对象
            if not job_start_failed and get_results and len(get_results) > 0:
                potential_job = get_results[0]
                try:
                    underlying_job = potential_job.BaseObject
                    job_state_test = underlying_job.JobStateInfo.State
                    job = underlying_job
                    job_id = job.Id

                    # 对于后台命令，不在会话中跟踪作业
                    if not run_in_background:
                        with self._job_lock:
                            self.active_job = job

                    logger.info(
                        f'Job retrieved successfully. Job ID: {job.Id}, State: {job_state_test}, Background: {run_in_background}'
                    )
                    # 作业检索成功。作业ID：{job.Id}，状态：{job_state_test}，后台：{run_in_background}

                    # 检查作业是否立即失败
                    if job_state_test == JobState.Failed:
                        logger.error(f'Job {job.Id} failed immediately after starting.')
                        # 作业{job.Id}启动后立即失败
                        output_chunk, error_chunk = self._receive_job_output(
                            job, keep=False
                        )
                        if output_chunk:
                            output_builder.append(output_chunk)
                        if error_chunk:
                            all_errors.extend(error_chunk)
                        job_start_failed = True
                        remove_script = f'Remove-Job -Job (Get-Job -Id {job.Id})'
                        self._run_ps_command(remove_script)
                        with self._job_lock:
                            self.active_job = None
                except AttributeError as e:
                    logger.error(
                        f'Get-Job returned an object without expected properties on BaseObject: {e}'
                    )
                    # Get-Job返回了BaseObject上没有预期属性的对象：{e}
                    logger.error(traceback.format_exc())
                    all_errors.append('Get-Job did not return a valid Job object.')
                    # Get-Job没有返回有效的Job对象
                    job_start_failed = True

            elif not job_start_failed:
                logger.error('Get-Job did not return any results.')
                # Get-Job没有返回任何结果
                all_errors.append('Get-Job did not return any results.')
                job_start_failed = True

        except Exception as start_ex:
            logger.error(f'Exception during job start/retrieval: {start_ex}')
            # 作业启动/检索期间出现异常：{start_ex}
            logger.error(traceback.format_exc())
            all_errors.append(f'[Job Start/Get Exception: {start_ex}]')
            job_start_failed = True
        finally:
            if ps_start:
                ps_start.Dispose()
            if 'ps_get' in locals() and ps_get:
                ps_get.Dispose()

        # 如果作业启动失败，返回错误
        if job_start_failed:
            current_cwd = self._get_current_cwd()
            python_safe_cwd = current_cwd.replace('\\\\', '\\\\\\\\')
            metadata = CmdOutputMetadata(exit_code=1, working_dir=python_safe_cwd)
            # 对于作业启动等关键失败，使用ErrorObservation
            return ErrorObservation(
                content='Failed to start PowerShell job.\n[ERRORS]\n'
                + '\n'.join(all_errors)
            )
            # 启动PowerShell作业失败

        # 对于后台命令，立即返回成功
        if run_in_background:
            current_cwd = self._get_current_cwd()
            python_safe_cwd = current_cwd.replace('\\\\', '\\\\\\\\')
            metadata = CmdOutputMetadata(exit_code=0, working_dir=python_safe_cwd)
            metadata.suffix = f'\n[Command started as background job {job_id}.]'
            # 命令作为后台作业{job_id}启动
            return CmdOutputObservation(
                content=f'[Started background job {job_id}]',
                command=f'{command} &',
                metadata=metadata,
            )
            # 已启动后台作业{job_id}

        # 监控作业
        start_time = time.monotonic()
        monitoring_loop_finished = False
        shutdown_requested = False
        final_state = JobState.Failed

        latest_cumulative_output = (
            ''  # 跟踪此循环中看到的绝对最新累积输出
        )
        latest_cumulative_errors = []  # 跟踪此循环中看到的绝对最新累积错误

        while not monitoring_loop_finished:
            # 检查关闭信号
            if not should_continue():
                logger.warning('Shutdown signal received during job monitoring.')
                # 作业监控期间收到关闭信号
                shutdown_requested = True
                monitoring_loop_finished = True
                exit_code = -1
                continue

            # 检查超时
            elapsed_seconds = time.monotonic() - start_time
            if elapsed_seconds > timeout_seconds:
                logger.warning(
                    f'Command job monitoring exceeded timeout ({timeout_seconds}s). Leaving job running.'
                )
                # 命令作业监控超过超时（{timeout_seconds}秒）。让作业继续运行
                timed_out = True
                monitoring_loop_finished = True
                exit_code = -1
                continue

            # 获取作业对象
            current_job_obj = self._get_job_object(job_id)
            if not current_job_obj:
                logger.error(f'Job {job_id} object disappeared during monitoring.')
                # 作业{job_id}对象在监控期间消失
                all_errors.append('[Job object lost during monitoring]')
                monitoring_loop_finished = True
                exit_code = 1
                final_state = JobState.Failed
                # 重置状态，因为作业已消失
                self._last_job_output = ''
                self._last_job_error = []
                continue

            # 轮询输出（keep=True）-> 返回累积输出/错误
            polled_cumulative_output, polled_cumulative_errors = (
                self._receive_job_output(current_job_obj, keep=True)
            )

            # 更新此循环中看到的最新累积状态
            latest_cumulative_output = polled_cumulative_output
            latest_cumulative_errors = polled_cumulative_errors

            # 检查作业状态
            current_state = current_job_obj.JobStateInfo.State
            if current_state not in [JobState.Running, JobState.NotStarted]:
                logger.info(
                    f'Job {job_id} finished monitoring loop with state: {current_state}'
                )
                # 作业{job_id}完成监控循环，状态：{current_state}
                monitoring_loop_finished = True
                final_state = current_state
                continue

            # 短暂休眠避免忙等待
            time.sleep(0.1)

        # 监控循环完成

        job_finished_naturally = (
            not timed_out
            and not shutdown_requested
            and final_state in [JobState.Completed, JobState.Stopped, JobState.Failed]
        )

        determined_cwd = self._cwd
        final_output_content = ''
        final_error_content = []

        if job_finished_naturally:
            logger.info(
                f'Job {job_id} finished naturally with state: {final_state}. Clearing final output buffer.'
            )
            # 作业{job_id}自然完成，状态：{final_state}。清除最终输出缓冲区
            final_cumulative_output = ''
            final_cumulative_errors: list[str] = []
            final_job_obj = self._get_job_object(job_id)
            if final_job_obj:
                # 使用keep=False获取最终输出/错误
                final_cumulative_output, final_cumulative_errors = (
                    self._receive_job_output(final_job_obj, keep=False)
                )
                # 始终计算相对于上次返回观察的输出
                final_output_content = final_cumulative_output.removeprefix(
                    self._last_job_output
                )
                # 也计算相对于上次返回观察的最终错误
                last_error_set = set(self._last_job_error)
                final_error_content = [
                    e for e in final_cumulative_errors if e not in last_error_set
                ]
            else:
                logger.warning(
                    f'Could not get final job object {job_id} to clear output buffer.'
                )
                # 无法获取最终作业对象{job_id}以清除输出缓冲区
                # 如果对象消失，输出是相对于上次观察的最后所见
                final_output_content = latest_cumulative_output.removeprefix(
                    self._last_job_output
                )
                last_error_set = set(self._last_job_error)
                final_error_content = [
                    e for e in latest_cumulative_errors if e not in last_error_set
                ]

            # 根据状态确定退出代码
            exit_code = 0 if final_state == JobState.Completed else 1

            if final_state == JobState.Completed:
                logger.info(f'Job {job_id} completed successfully. Querying final CWD.')
                # 作业{job_id}成功完成。查询最终CWD
                determined_cwd = self._get_current_cwd()
            else:
                logger.info(
                    f'Job {job_id} finished but did not complete successfully ({final_state}). Using cached CWD: {self._cwd}'
                )
                # 作业{job_id}完成但未成功完成（{final_state}）。使用缓存的CWD：{self._cwd}
                determined_cwd = self._cwd

            # 清理完成的作业
            with self._job_lock:
                remove_script = f'Remove-Job -Job (Get-Job -Id {job_id})'
                self._run_ps_command(remove_script)
                self.active_job = None
                logger.info(f'Cleaned up finished job {job_id}')
                # 清理完成的作业{job_id}

        else:
            logger.info(
                f'Job {job_id} did not finish naturally (timeout={timed_out}, shutdown={shutdown_requested}). Using cached CWD: {self._cwd}'
            )
            # 作业{job_id}未自然完成（超时={timed_out}，关闭={shutdown_requested}）。使用缓存的CWD：{self._cwd}
            determined_cwd = self._cwd
            # 退出代码已经是从循环退出原因得出的-1

            # 计算相对于上次观察的新输出/错误（使用循环中的最新值）
            final_output_content = latest_cumulative_output.removeprefix(
                self._last_job_output
            )
            final_error_content = [
                e for e in latest_cumulative_errors if e not in self._last_job_error
            ]

            # 更新持久状态
            self._last_job_output = latest_cumulative_output
            self._last_job_error = list(
                set(latest_cumulative_errors)
            )  # 存储唯一错误

        # 构建最终观察
        python_safe_cwd = determined_cwd.replace('\\\\', '\\\\\\\\')

        # 为最终观察合并唯一输出块
        # 使用集合确保唯一性，如果块在轮询中相同
        # 连接累积的output_builder部分
        final_output = final_output_content
        if final_error_content:  # 使用计算的最终*新*错误
            error_stream_text = '\n'.join(final_error_content)
            if final_output:
                final_output += f'\n[ERROR STREAM]\n{error_stream_text}'
            else:
                final_output = f'[ERROR STREAM]\n{error_stream_text}'
            if exit_code == 0:  # 只有在作业自然完成时才检查退出代码
                logger.info(
                    f'Detected errors in stream ({len(final_error_content)} records) but job state was Completed. Forcing exit_code to 1.'
                )
                # 在流中检测到错误（{len(final_error_content)}条记录），但作业状态为Completed。强制exit_code为1
                exit_code = 1

        # 创建元数据
        metadata = CmdOutputMetadata(exit_code=exit_code, working_dir=python_safe_cwd)

        # 确定后缀
        if timed_out:
            # 与bash.py超时消息对齐后缀
            suffix = (
                f'\n[The command timed out after {timeout_seconds} seconds. '
                f'{TIMEOUT_MESSAGE_TEMPLATE}]'
            )
            # 命令在{timeout_seconds}秒后超时
        elif shutdown_requested:
            # 与bash.py等效对齐后缀（尽管bash.py可能没有特定的关闭消息）
            suffix = f'\n[Command execution cancelled due to shutdown signal. Exit Code: {exit_code}]'
            # 由于关闭信号，命令执行被取消。退出代码：{exit_code}
        elif job_finished_naturally:
            # 与bash.py完成消息对齐后缀
            suffix = f'\n[The command completed with exit code {exit_code}.]'
            # 命令完成，退出代码为{exit_code}
        else:  # 不应该发生，但防御性回退
            suffix = f'\n[Command execution finished. State: {final_state}, Exit Code: {exit_code}]'
            # 命令执行完成。状态：{final_state}，退出代码：{exit_code}

        metadata.suffix = suffix

        return CmdOutputObservation(
            content=final_output, command=command, metadata=metadata
        )

    def close(self) -> None:
        """关闭PowerShell运行空间并释放资源，停止任何活动作业。"""
        if self._closed:
            return

        logger.info('Closing PowerShell session runspace.')
        # 关闭PowerShell会话运行空间

        # 在关闭运行空间之前停止并移除任何活动作业
        with self._job_lock:
            if self.active_job:
                logger.warning(  # type: ignore[unreachable]
                    f'Session closing with active job {self.active_job.Id}. Attempting to stop and remove.'
                )
                # 会话关闭时有活动作业{self.active_job.Id}。尝试停止并移除
                job_id = self.active_job.Id
                try:
                    # 在尝试停止/移除之前确保作业对象存在
                    active_job_obj = self._get_job_object(job_id)
                    if active_job_obj:
                        stop_script = f'Stop-Job -Job (Get-Job -Id {job_id})'
                        self._run_ps_command(
                            stop_script
                        )  # 在运行空间关闭之前使用帮助程序
                        time.sleep(0.1)
                        remove_script = f'Remove-Job -Job (Get-Job -Id {job_id})'
                        self._run_ps_command(remove_script)
                        logger.info(
                            f'Stopped and removed active job {job_id} during close.'
                        )
                        # 关闭期间停止并移除活动作业{job_id}
                    else:
                        logger.warning(
                            f'Could not find job object {job_id} to stop/remove during close.'
                        )
                        # 关闭期间无法找到作业对象{job_id}以停止/移除
                except Exception as e:
                    logger.error(
                        f'Error stopping/removing job {job_id} during close: {e}'
                    )
                    # 关闭期间停止/移除作业{job_id}时出错：{e}
                # 即使停止/移除失败也重置状态
                self._last_job_output = ''
                self._last_job_error = []
                self.active_job = None

        # 关闭并释放运行空间
        if hasattr(self, 'runspace') and self.runspace:
            try:
                # 使用System.Management.Automation.Runspaces命名空间检查状态
                # 首先获取状态信息对象以避免嵌套访问的潜在pythonnet问题
                runspace_state_info = self.runspace.RunspaceStateInfo
                if runspace_state_info.State == RunspaceState.Opened:
                    self.runspace.Close()
                self.runspace.Dispose()
                logger.info('PowerShell runspace closed and disposed.')
                # PowerShell运行空间已关闭并释放
            except Exception as e:
                logger.error(f'Error closing/disposing PowerShell runspace: {e}')
                # 关闭/释放PowerShell运行空间时出错：{e}
                logger.error(traceback.format_exc())

        # 重置状态
        self.runspace = None
        self._initialized = False
        self._closed = True

    def __del__(self) -> None:
        """析构函数确保运行空间被关闭。"""
        self.close()
