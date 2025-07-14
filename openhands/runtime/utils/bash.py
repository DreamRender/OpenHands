import os
import re
import time
import traceback
import uuid
from enum import Enum
from typing import Any

import bashlex
import libtmux

from openhands.core.logger import openhands_logger as logger
from openhands.events.action import CmdRunAction
from openhands.events.observation import ErrorObservation
from openhands.events.observation.commands import (
    CMD_OUTPUT_PS1_END,
    CmdOutputMetadata,
    CmdOutputObservation,
)
from openhands.runtime.utils.bash_constants import TIMEOUT_MESSAGE_TEMPLATE
from openhands.utils.shutdown_listener import should_continue


def split_bash_commands(commands: str) -> list[str]:
    """
    将bash命令字符串拆分为单个命令列表。
    
    使用bashlex库解析命令，如果解析失败则返回原始命令。
    
    Args:
        commands (str): 要拆分的bash命令字符串
        
    Returns:
        list[str]: 拆分后的命令列表
    """
    if not commands.strip():
        return ['']
    try:
        # 使用bashlex解析bash命令
        parsed = bashlex.parse(commands)
    except (
        bashlex.errors.ParsingError,
        NotImplementedError,
        TypeError,
        AttributeError,
    ):
        # 添加了AttributeError来捕获'str' object has no attribute 'kind'错误 (issue #8369)
        logger.debug(
            f'Failed to parse bash commands\n'
            f'[input]: {commands}\n'
            f'[warning]: {traceback.format_exc()}\n'
            f'The original command will be returned as is.'
        )
        # 如果解析失败，返回原始命令
        return [commands]

    result: list[str] = []
    last_end = 0

    # 遍历解析后的节点
    for node in parsed:
        start, end = node.pos

        # 包含上一个命令和当前命令之间的任何文本
        if start > last_end:
            between = commands[last_end:start]
            logger.debug(f'BASH PARSING between: {between}')
            if result:
                result[-1] += between.rstrip()
            elif between.strip():
                # 这种情况不应该发生
                result.append(between.rstrip())

        # 提取命令，保留原始格式
        command = commands[start:end].rstrip()
        logger.debug(f'BASH PARSING command: {command}')
        result.append(command)

        last_end = end

    # 将最后一个命令之后的任何剩余文本添加到最后一个命令
    remaining = commands[last_end:].rstrip()
    logger.debug(f'BASH PARSING remaining: {remaining}')
    if last_end < len(commands) and result:
        result[-1] += remaining
        logger.debug(f'BASH PARSING result[-1] += remaining: {result[-1]}')
    elif last_end < len(commands):
        if remaining:
            result.append(remaining)
            logger.debug(f'BASH PARSING result.append(remaining): {result[-1]}')
    return result


def escape_bash_special_chars(command: str) -> str:
    r"""
    转义在bash和python中有不同解释的字符。
    专门处理转义序列，如\;, \|, \&等。
    
    Args:
        command (str): 要转义的命令字符串
        
    Returns:
        str: 转义后的命令字符串
    """
    if command.strip() == '':
        return ''

    try:
        parts = []
        last_pos = 0

        def visit_node(node: Any) -> None:
            """
            递归访问AST节点并处理特殊字符转义。
            
            Args:
                node (Any): bashlex AST节点
            """
            nonlocal last_pos
            # 处理heredoc重定向
            if (
                node.kind == 'redirect'
                and hasattr(node, 'heredoc')
                and node.heredoc is not None
            ):
                # 我们进入了heredoc - 保持所有内容不变直到看到EOF
                # 存储heredoc结束标记（通常是'EOF'但可能不同）
                between = command[last_pos : node.pos[0]]
                parts.append(between)
                # 添加heredoc开始标记
                parts.append(command[node.pos[0] : node.heredoc.pos[0]])
                # 原样添加heredoc内容
                parts.append(command[node.heredoc.pos[0] : node.heredoc.pos[1]])
                last_pos = node.pos[1]
                return

            # 处理word节点
            if node.kind == 'word':
                # 获取最后位置和当前word之间的原始文本
                between = command[last_pos : node.pos[0]]
                word_text = command[node.pos[0] : node.pos[1]]

                # 添加between文本，转义特殊字符
                between = re.sub(r'\\([;&|><])', r'\\\\\1', between)
                parts.append(between)

                # 检查word_text是否为引用字符串或命令替换
                if (
                    (word_text.startswith('"') and word_text.endswith('"'))
                    or (word_text.startswith("'") and word_text.endswith("'"))
                    or (word_text.startswith('$(') and word_text.endswith(')'))
                    or (word_text.startswith('`') and word_text.endswith('`'))
                ):
                    # 保持引用字符串、命令替换和heredoc内容不变
                    parts.append(word_text)
                else:
                    # 在未引用文本中转义特殊字符
                    word_text = re.sub(r'\\([;&|><])', r'\\\\\1', word_text)
                    parts.append(word_text)

                last_pos = node.pos[1]
                return

            # 访问子节点
            if hasattr(node, 'parts'):
                for part in node.parts:
                    visit_node(part)

        # 处理AST中的所有节点
        nodes = list(bashlex.parse(command))
        for node in nodes:
            between = command[last_pos : node.pos[0]]
            between = re.sub(r'\\([;&|><])', r'\\\\\1', between)
            parts.append(between)
            last_pos = node.pos[0]
            visit_node(node)

        # 处理最后一个word之后的任何剩余文本
        remaining = command[last_pos:]
        parts.append(remaining)
        return ''.join(parts)
    except (bashlex.errors.ParsingError, NotImplementedError, TypeError):
        logger.debug(
            f'Failed to parse bash commands for special characters escape\n'
            f'[input]: {command}\n'
            f'[warning]: {traceback.format_exc()}\n'
            f'The original command will be returned as is.'
        )
        return command


class BashCommandStatus(Enum):
    """
    Bash命令执行状态枚举。
    
    定义了命令执行的各种状态，用于跟踪命令的生命周期。
    """
    CONTINUE = 'continue'  # 命令继续执行中
    COMPLETED = 'completed'  # 命令已完成
    NO_CHANGE_TIMEOUT = 'no_change_timeout'  # 无变化超时
    HARD_TIMEOUT = 'hard_timeout'  # 硬超时


def _remove_command_prefix(command_output: str, command: str) -> str:
    """
    从命令输出中移除命令前缀。
    
    Args:
        command_output (str): 命令输出
        command (str): 执行的命令
        
    Returns:
        str: 移除前缀后的输出
    """
    return command_output.lstrip().removeprefix(command.lstrip()).lstrip()


class BashSession:
    """
    Bash会话管理类。
    
    使用tmux管理bash会话，提供命令执行、超时处理等功能。
    """
    
    # 类常量定义
    POLL_INTERVAL = 0.5  # 轮询间隔（秒）
    HISTORY_LIMIT = 10_000  # 历史记录限制
    PS1 = CmdOutputMetadata.to_ps1_prompt()  # 提示符格式

    def __init__(
        self,
        work_dir: str,
        username: str | None = None,
        no_change_timeout_seconds: int = 30,
        max_memory_mb: int | None = None,
    ):
        """
        初始化Bash会话。
        
        Args:
            work_dir (str): 工作目录
            username (str | None): 用户名，默认为None
            no_change_timeout_seconds (int): 无变化超时时间（秒），默认30秒
            max_memory_mb (int | None): 最大内存限制（MB），默认为None
        """
        self.NO_CHANGE_TIMEOUT_SECONDS = no_change_timeout_seconds
        self.work_dir = work_dir
        self.username = username
        self._initialized = False  # 初始化状态标志
        self.max_memory_mb = max_memory_mb

    def initialize(self) -> None:
        """
        初始化tmux会话和bash环境。
        
        创建tmux服务器、会话、窗口和窗格，配置bash环境。
        """
        # 创建tmux服务器
        self.server = libtmux.Server()
        _shell_command = '/bin/bash'
        
        # 根据用户名选择shell命令
        if self.username in ['root', 'openhands']:
            # 这会为给定用户启动一个非登录（新）shell
            _shell_command = f'su {self.username} -'

        # 修复：我们将在即将到来的PR中使用sysbox-runc引入内存限制
        # # 否则，我们以当前用户身份运行（例如，运行LocalRuntime时）
        # if self.max_memory_mb is not None:
        #     window_command = (
        #         f'prlimit --as={self.max_memory_mb * 1024 * 1024} {_shell_command}'
        #     )
        # else:
        window_command = _shell_command

        logger.debug(f'Initializing bash session with command: {window_command}')
        # 创建唯一的会话名称
        session_name = f'openhands-{self.username}-{uuid.uuid4()}'
        
        # 创建新的tmux会话
        self.session = self.server.new_session(
            session_name=session_name,
            start_directory=self.work_dir,  # libtmux支持此参数
            kill_session=True,
            x=1000,
            y=1000,
        )

        # 设置历史记录限制为大数字以避免丢失历史记录
        # https://unix.stackexchange.com/questions/43414/unlimited-history-in-tmux
        self.session.set_option('history-limit', str(self.HISTORY_LIMIT), _global=True)
        self.session.history_limit = self.HISTORY_LIMIT
        
        # 我们需要创建一个新窗格，因为初始窗格的历史记录限制是（默认）2000
        _initial_window = self.session.active_window
        self.window = self.session.new_window(
            window_name='bash',
            window_shell=window_command,
            start_directory=self.work_dir,  # libtmux支持此参数
        )
        self.pane = self.window.active_pane
        logger.debug(f'pane: {self.pane}; history_limit: {self.session.history_limit}')
        _initial_window.kill()

        # 配置bash使用简单的PS1并禁用PS2
        self.pane.send_keys(
            f'export PROMPT_COMMAND=\'export PS1="{self.PS1}"\'; export PS2=""'
        )
        time.sleep(0.1)  # 等待命令生效
        self._clear_screen()

        # 存储用于交互式输入处理的最后一个命令
        self.prev_status: BashCommandStatus | None = None
        self.prev_output: str = ''
        self._closed: bool = False
        logger.debug(f'Bash session initialized with work dir: {self.work_dir}')

        # 维护当前工作目录
        self._cwd = os.path.abspath(self.work_dir)
        self._initialized = True

    def __del__(self) -> None:
        """确保对象销毁时关闭会话。"""
        self.close()

    def _get_pane_content(self) -> str:
        """
        捕获当前窗格内容并更新缓冲区。
        
        Returns:
            str: 窗格内容
        """
        content = '\n'.join(
            map(
                # 避免双重换行
                lambda line: line.rstrip(),
                self.pane.cmd('capture-pane', '-J', '-pS', '-').stdout,
            )
        )
        return content

    def close(self) -> None:
        """清理会话。"""
        if self._closed:
            return
        self.session.kill()
        self._closed = True

    @property
    def cwd(self) -> str:
        """
        获取当前工作目录。
        
        Returns:
            str: 当前工作目录路径
        """
        return self._cwd

    def _is_special_key(self, command: str) -> bool:
        """
        检查命令是否为特殊按键。
        
        Args:
            command (str): 要检查的命令
            
        Returns:
            bool: 如果是特殊按键则返回True
        """
        # 特殊按键的形式为C-<key>
        _command = command.strip()
        return _command.startswith('C-') and len(_command) == 3

    def _clear_screen(self) -> None:
        """清除tmux窗格屏幕和历史记录。"""
        self.pane.send_keys('C-l', enter=False)
        time.sleep(0.1)
        self.pane.cmd('clear-history')

    def _get_command_output(
        self,
        command: str,
        raw_command_output: str,
        metadata: CmdOutputMetadata,
        continue_prefix: str = '',
    ) -> str:
        """
        获取移除了前一个命令输出的命令输出。

        Args:
            command (str): 执行的命令
            raw_command_output (str): 来自命令的原始输出
            metadata (CmdOutputMetadata): 存储前缀/后缀的metadata对象
            continue_prefix (str): 如果是前一个命令的继续，添加到命令输出的前缀

        Returns:
            str: 处理后的命令输出
        """
        # 如果有的话，从新输出中移除前一个命令输出
        if self.prev_output:
            command_output = raw_command_output.removeprefix(self.prev_output)
            metadata.prefix = continue_prefix
        else:
            command_output = raw_command_output
        self.prev_output = raw_command_output  # 无论如何更新当前命令输出
        command_output = _remove_command_prefix(command_output, command)
        return command_output.rstrip()

    def _handle_completed_command(
        self, command: str, pane_content: str, ps1_matches: list[re.Match]
    ) -> CmdOutputObservation:
        """
        处理已完成的命令。
        
        Args:
            command (str): 执行的命令
            pane_content (str): 窗格内容
            ps1_matches (list[re.Match]): PS1匹配列表
            
        Returns:
            CmdOutputObservation: 命令输出观察对象
        """
        is_special_key = self._is_special_key(command)
        assert len(ps1_matches) >= 1, (
            f'Expected at least one PS1 metadata block, but got {len(ps1_matches)}.\n'
            f'---FULL OUTPUT---\n{pane_content!r}\n---END OF OUTPUT---'
        )
        metadata = CmdOutputMetadata.from_ps1_match(ps1_matches[-1])

        # 前一个命令输出由于历史记录限制而被截断的特殊情况
        # 我们应该获取最后一个PS1提示符之前的内容
        get_content_before_last_match = bool(len(ps1_matches) == 1)

        # 如果当前工作目录已更改，则更新它
        if metadata.working_dir != self._cwd and metadata.working_dir:
            self._cwd = metadata.working_dir

        logger.debug(f'COMMAND OUTPUT: {pane_content}')
        # 提取两个PS1提示符之间的命令输出
        raw_command_output = self._combine_outputs_between_matches(
            pane_content,
            ps1_matches,
            get_content_before_last_match=get_content_before_last_match,
        )

        if get_content_before_last_match:
            # 计算截断输出中的行数
            num_lines = len(raw_command_output.splitlines())
            metadata.prefix = f'[Previous command outputs are truncated. Showing the last {num_lines} lines of the output below.]\n'
            # 翻译：[前一个命令输出已被截断。显示下面输出的最后{num_lines}行。]

        metadata.suffix = (
            f'\n[The command completed with exit code {metadata.exit_code}.]'
            if not is_special_key
            else f'\n[The command completed with exit code {metadata.exit_code}. CTRL+{command[-1].upper()} was sent.]'
        )
        # 翻译：[命令完成，退出代码为{metadata.exit_code}。] 或 [命令完成，退出代码为{metadata.exit_code}。发送了CTRL+{command[-1].upper()}。]
        
        command_output = self._get_command_output(
            command,
            raw_command_output,
            metadata,
        )
        self.prev_status = BashCommandStatus.COMPLETED
        self.prev_output = ''  # 重置前一个命令输出
        self._ready_for_next_command()
        return CmdOutputObservation(
            content=command_output,
            command=command,
            metadata=metadata,
        )

    def _handle_nochange_timeout_command(
        self,
        command: str,
        pane_content: str,
        ps1_matches: list[re.Match],
    ) -> CmdOutputObservation:
        """
        处理无变化超时的命令。
        
        Args:
            command (str): 执行的命令
            pane_content (str): 窗格内容
            ps1_matches (list[re.Match]): PS1匹配列表
            
        Returns:
            CmdOutputObservation: 命令输出观察对象
        """
        self.prev_status = BashCommandStatus.NO_CHANGE_TIMEOUT
        if len(ps1_matches) != 1:
            logger.warning(
                'Expected exactly one PS1 metadata block BEFORE the execution of a command, '
                f'but got {len(ps1_matches)} PS1 metadata blocks:\n---\n{pane_content!r}\n---'
            )
        raw_command_output = self._combine_outputs_between_matches(
            pane_content, ps1_matches
        )
        metadata = CmdOutputMetadata()  # 无metadata可用
        metadata.suffix = (
            f'\n[The command has no new output after {self.NO_CHANGE_TIMEOUT_SECONDS} seconds. '
            f'{TIMEOUT_MESSAGE_TEMPLATE}]'
        )
        # 翻译：[命令在{self.NO_CHANGE_TIMEOUT_SECONDS}秒后没有新输出。{TIMEOUT_MESSAGE_TEMPLATE}]
        
        command_output = self._get_command_output(
            command,
            raw_command_output,
            metadata,
            continue_prefix='[Below is the output of the previous command.]\n',
            # 翻译：[下面是前一个命令的输出。]
        )
        return CmdOutputObservation(
            content=command_output,
            command=command,
            metadata=metadata,
        )

    def _handle_hard_timeout_command(
        self,
        command: str,
        pane_content: str,
        ps1_matches: list[re.Match],
        timeout: float,
    ) -> CmdOutputObservation:
        """
        处理硬超时的命令。
        
        Args:
            command (str): 执行的命令
            pane_content (str): 窗格内容
            ps1_matches (list[re.Match]): PS1匹配列表
            timeout (float): 超时时间
            
        Returns:
            CmdOutputObservation: 命令输出观察对象
        """
        self.prev_status = BashCommandStatus.HARD_TIMEOUT
        if len(ps1_matches) != 1:
            logger.warning(
                'Expected exactly one PS1 metadata block BEFORE the execution of a command, '
                f'but got {len(ps1_matches)} PS1 metadata blocks:\n---\n{pane_content!r}\n---'
            )
        raw_command_output = self._combine_outputs_between_matches(
            pane_content, ps1_matches
        )
        metadata = CmdOutputMetadata()  # 无metadata可用
        metadata.suffix = (
            f'\n[The command timed out after {timeout} seconds. '
            f'{TIMEOUT_MESSAGE_TEMPLATE}]'
        )
        # 翻译：[命令在{timeout}秒后超时。{TIMEOUT_MESSAGE_TEMPLATE}]
        
        command_output = self._get_command_output(
            command,
            raw_command_output,
            metadata,
            continue_prefix='[Below is the output of the previous command.]\n',
            # 翻译：[下面是前一个命令的输出。]
        )

        return CmdOutputObservation(
            command=command,
            content=command_output,
            metadata=metadata,
        )

    def _ready_for_next_command(self) -> None:
        """为新命令重置内容缓冲区。"""
        # 清除当前内容
        self._clear_screen()

    def _combine_outputs_between_matches(
        self,
        pane_content: str,
        ps1_matches: list[re.Match],
        get_content_before_last_match: bool = False,
    ) -> str:
        """
        组合PS1匹配之间的所有输出。

        Args:
            pane_content (str): 包含PS1提示符和命令输出的完整窗格内容
            ps1_matches (list[re.Match]): PS1提示符的正则表达式匹配列表
            get_content_before_last_match (bool): 当只有一个PS1匹配时，是否获取
                最后一个PS1提示符之前的内容（True）还是之后的内容（False）
                
        Returns:
            str: 匹配之间所有输出的组合字符串
        """
        if len(ps1_matches) == 1:
            if get_content_before_last_match:
                # 命令输出是最后一个PS1提示符之前的内容
                return pane_content[: ps1_matches[0].start()]
            else:
                # 命令输出是最后一个PS1提示符之后的内容
                return pane_content[ps1_matches[0].end() + 1 :]
        elif len(ps1_matches) == 0:
            return pane_content
        
        combined_output = ''
        for i in range(len(ps1_matches) - 1):
            # 提取当前和下一个PS1提示符之间的内容
            output_segment = pane_content[
                ps1_matches[i].end() + 1 : ps1_matches[i + 1].start()
            ]
            combined_output += output_segment + '\n'
        # 添加最后一个PS1提示符之后的内容
        combined_output += pane_content[ps1_matches[-1].end() + 1 :]
        logger.debug(f'COMBINED OUTPUT: {combined_output}')
        return combined_output

    def execute(self, action: CmdRunAction) -> CmdOutputObservation | ErrorObservation:
        """
        在bash会话中执行命令。
        
        Args:
            action (CmdRunAction): 要执行的命令Action
            
        Returns:
            CmdOutputObservation | ErrorObservation: 命令输出观察对象或错误观察对象
        """
        if not self._initialized:
            raise RuntimeError('Bash session is not initialized')

        # 去除命令的前导/尾随空格
        logger.debug(f'RECEIVED ACTION: {action}')
        command = action.command.strip()
        is_input: bool = action.is_input

        # 如果前一个命令未完成，我们需要检查命令是否为空
        if self.prev_status not in {
            BashCommandStatus.CONTINUE,
            BashCommandStatus.NO_CHANGE_TIMEOUT,
            BashCommandStatus.HARD_TIMEOUT,
        }:
            if command == '':
                return CmdOutputObservation(
                    content='ERROR: No previous running command to retrieve logs from.',
                    # 翻译：错误：没有正在运行的前一个命令可以检索日志。
                    command='',
                    metadata=CmdOutputMetadata(),
                )
            if is_input:
                return CmdOutputObservation(
                    content='ERROR: No previous running command to interact with.',
                    # 翻译：错误：没有正在运行的前一个命令可以交互。
                    command='',
                    metadata=CmdOutputMetadata(),
                )

        # 检查命令是单个命令还是多个命令
        splited_commands = split_bash_commands(command)
        if len(splited_commands) > 1:
            return ErrorObservation(
                content=(
                    f'ERROR: Cannot execute multiple commands at once.\n'
                    f'Please run each command separately OR chain them into a single command via && or ;\n'
                    f'Provided commands:\n{"\n".join(f"({i + 1}) {cmd}" for i, cmd in enumerate(splited_commands))}'
                )
                # 翻译：错误：不能同时执行多个命令。请分别运行每个命令或通过&&或;将它们链接成一个命令。
            )

        # 在发送命令之前获取初始状态
        initial_pane_output = self._get_pane_content()
        initial_ps1_matches = CmdOutputMetadata.matches_ps1_metadata(
            initial_pane_output
        )
        initial_ps1_count = len(initial_ps1_matches)
        logger.debug(f'Initial PS1 count: {initial_ps1_count}')

        start_time = time.time()
        last_change_time = start_time
        last_pane_output = (
            initial_pane_output  # 使用初始输出作为起点
        )

        # 当前一个命令仍在运行时，我们试图发送一个新命令
        if (
            self.prev_status
            in {
                BashCommandStatus.HARD_TIMEOUT,
                BashCommandStatus.NO_CHANGE_TIMEOUT,
            }
            and not last_pane_output.rstrip().endswith(
                CMD_OUTPUT_PS1_END.rstrip()
            )  # 前一个命令未完成
            and not is_input
            and command != ''  # 不是输入且不是空命令
        ):
            _ps1_matches = CmdOutputMetadata.matches_ps1_metadata(last_pane_output)
            # 如果_ps1_matches为空，使用initial_ps1_matches，否则使用_ps1_matches
            # 这处理了提示符可能滚动出屏幕但之前存在的情况
            current_matches_for_output = (
                _ps1_matches if _ps1_matches else initial_ps1_matches
            )
            raw_command_output = self._combine_outputs_between_matches(
                last_pane_output, current_matches_for_output
            )
            metadata = CmdOutputMetadata()  # 无metadata可用
            metadata.suffix = (
                f'\n[Your command "{command}" is NOT executed. '
                f'The previous command is still running - You CANNOT send new commands until the previous command is completed. '
                'By setting `is_input` to `true`, you can interact with the current process: '
                "You may wait longer to see additional output of the previous command by sending empty command '', "
                'send other commands to interact with the current process, '
                'or send keys ("C-c", "C-z", "C-d") to interrupt/kill the previous command before sending your new command.]'
            )
            # 翻译：[您的命令"{command}"未被执行。前一个命令仍在运行 - 在前一个命令完成之前，您不能发送新命令。通过将`is_input`设置为`true`，您可以与当前进程交互：您可以通过发送空命令''等待更长时间以查看前一个命令的额外输出，发送其他命令与当前进程交互，或发送按键（"C-c"、"C-z"、"C-d"）来中断/终止前一个命令，然后发送您的新命令。]
            
            logger.debug(f'PREVIOUS COMMAND OUTPUT: {raw_command_output}')
            command_output = self._get_command_output(
                command,
                raw_command_output,
                metadata,
                continue_prefix='[Below is the output of the previous command.]\n',
                # 翻译：[下面是前一个命令的输出。]
            )
            return CmdOutputObservation(
                command=command,
                content=command_output,
                metadata=metadata,
            )

        # 向窗格发送实际命令/输入
        if command != '':
            is_special_key = self._is_special_key(command)
            if is_input:
                logger.debug(f'SENDING INPUT TO RUNNING PROCESS: {command!r}')
                self.pane.send_keys(
                    command,
                    enter=not is_special_key,
                )
            else:
                # 将命令转换为原始字符串
                command = escape_bash_special_chars(command)
                logger.debug(f'SENDING COMMAND: {command!r}')
                self.pane.send_keys(
                    command,
                    enter=not is_special_key,
                )

        # 循环直到命令完成或超时
        while should_continue():
            _start_time = time.time()
            logger.debug(f'GETTING PANE CONTENT at {_start_time}')
            cur_pane_output = self._get_pane_content()
            logger.debug(
                f'PANE CONTENT GOT after {time.time() - _start_time:.2f} seconds'
            )
            logger.debug(f'BEGIN OF PANE CONTENT: {cur_pane_output.split("\n")[:10]}')
            logger.debug(f'END OF PANE CONTENT: {cur_pane_output.split("\n")[-10:]}')
            ps1_matches = CmdOutputMetadata.matches_ps1_metadata(cur_pane_output)
            current_ps1_count = len(ps1_matches)

            if cur_pane_output != last_pane_output:
                last_pane_output = cur_pane_output
                last_change_time = time.time()
                logger.debug(f'CONTENT UPDATED DETECTED at {last_change_time}')

            # 1) 执行完成：
            # 条件1：自命令开始以来出现了新提示符。
            # 条件2：提示符计数没有增加（可能因为初始提示符滚动出去了），
            # 但*当前*可见窗格以提示符结尾，表示完成。
            if (
                current_ps1_count > initial_ps1_count
                or cur_pane_output.rstrip().endswith(CMD_OUTPUT_PS1_END.rstrip())
            ):
                return self._handle_completed_command(
                    command,
                    pane_content=cur_pane_output,
                    ps1_matches=ps1_matches,
                )

            # 超时检查应该只在新提示符尚未出现时触发。

            # 2) 执行超时，因为输出在一段时间内没有变化（self.NO_CHANGE_TIMEOUT_SECONDS）
            # 如果命令是*阻塞*的，我们忽略这个
            time_since_last_change = time.time() - last_change_time
            logger.debug(
                f'CHECKING NO CHANGE TIMEOUT ({self.NO_CHANGE_TIMEOUT_SECONDS}s): elapsed {time_since_last_change}. Action blocking: {action.blocking}'
            )
            if (
                not action.blocking
                and time_since_last_change >= self.NO_CHANGE_TIMEOUT_SECONDS
            ):
                return self._handle_nochange_timeout_command(
                    command,
                    pane_content=cur_pane_output,
                    ps1_matches=ps1_matches,
                )

            # 3) 由于硬超时而执行超时
            elapsed_time = time.time() - start_time
            logger.debug(
                f'CHECKING HARD TIMEOUT ({action.timeout}s): elapsed {elapsed_time:.2f}'
            )
            if action.timeout and elapsed_time >= action.timeout:
                logger.debug('Hard timeout triggered.')
                return self._handle_hard_timeout_command(
                    command,
                    pane_content=cur_pane_output,
                    ps1_matches=ps1_matches,
                    timeout=action.timeout,
                )

            logger.debug(f'SLEEPING for {self.POLL_INTERVAL} seconds for next poll')
            time.sleep(self.POLL_INTERVAL)
        raise RuntimeError('Bash session was likely interrupted...')
        # 翻译：Bash会话可能被中断...
