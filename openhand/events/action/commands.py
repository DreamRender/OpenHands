from dataclasses import dataclass
from typing import ClassVar

from openhands.core.schema import ActionType
from openhands.events.action.action import (
    Action,
    ActionConfirmationStatus,
    ActionSecurityRisk,
)


@dataclass
class CmdRunAction(Action):
    """命令执行Action
    
    用于在系统中执行shell命令的Action。支持多种执行模式，
    包括交互式输入、阻塞执行、静态执行等。
    
    Attributes:
        command (str): 要执行的命令。当command为空时，将用于打印当前tmux窗口
        is_input (bool): 如果为True，命令将作为输入发送给正在运行的进程
        thought (str): Agent的思考过程，默认为空字符串
        blocking (bool): 如果为True，命令将以阻塞方式运行，但必须通过_set_hard_timeout设置超时
        is_static (bool): 如果为True，在单独的进程中运行命令
        cwd (str | None): 当前工作目录，仅在is_static为True时使用
        hidden (bool): 是否隐藏命令执行，默认为False
        action (str): Action类型，固定为ActionType.RUN
        runnable (ClassVar[bool]): 类变量，表示此Action可以被执行
        confirmation_state (ActionConfirmationStatus): 确认状态，默认为已确认
        security_risk (ActionSecurityRisk | None): 安全风险等级评估
    """
    
    command: str  # 要执行的命令（空命令时打印当前tmux窗口）
    
    is_input: bool = False  # 是否作为输入发送给运行中的进程
    thought: str = ''  # Agent的思考过程
    blocking: bool = False  # 是否以阻塞方式运行（需设置超时）
    is_static: bool = False  # 是否在单独进程中运行
    cwd: str | None = None  # 工作目录（仅is_static为True时有效）
    hidden: bool = False  # 是否隐藏命令执行
    action: str = ActionType.RUN  # Action类型标识
    runnable: ClassVar[bool] = True  # 标识此Action可执行
    confirmation_state: ActionConfirmationStatus = ActionConfirmationStatus.CONFIRMED  # 确认状态
    security_risk: ActionSecurityRisk | None = None  # 安全风险评估

    @property
    def message(self) -> str:
        """获取命令执行的消息
        
        Returns:
            str: 格式化的命令执行消息
        """
        return f'Running command: {self.command}'

    def __str__(self) -> str:
        """获取CmdRunAction的字符串表示
        
        Returns:
            str: 格式化的Action描述，包含来源、是否为输入、思考过程和命令内容
        """
        ret = f'**CmdRunAction (source={self.source}, is_input={self.is_input})**\n'
        if self.thought:
            ret += f'THOUGHT: {self.thought}\n'
        ret += f'COMMAND:\n{self.command}'
        return ret


@dataclass
class IPythonRunCellAction(Action):
    """IPython代码执行Action
    
    用于在IPython环境中执行Python代码的Action。
    支持交互式代码执行和内核管理。
    
    Attributes:
        code (str): 要执行的Python代码
        thought (str): Agent的思考过程，默认为空字符串
        include_extra (bool): 是否在输出中包含当前工作目录和Python解释器信息，默认为True
        action (str): Action类型，固定为ActionType.RUN_IPYTHON
        runnable (ClassVar[bool]): 类变量，表示此Action可以被执行
        confirmation_state (ActionConfirmationStatus): 确认状态，默认为已确认
        security_risk (ActionSecurityRisk | None): 安全风险等级评估
        kernel_init_code (str): 内核初始化代码（如果内核重启时运行）
    """
    
    code: str  # 要执行的Python代码
    thought: str = ''  # Agent的思考过程
    include_extra: bool = True  # 是否在输出中包含CWD和Python解释器信息
    action: str = ActionType.RUN_IPYTHON  # Action类型标识
    runnable: ClassVar[bool] = True  # 标识此Action可执行
    confirmation_state: ActionConfirmationStatus = ActionConfirmationStatus.CONFIRMED  # 确认状态
    security_risk: ActionSecurityRisk | None = None  # 安全风险评估
    kernel_init_code: str = ''  # 内核初始化时运行的代码

    def __str__(self) -> str:
        """获取IPythonRunCellAction的字符串表示
        
        Returns:
            str: 格式化的Action描述，包含思考过程（如果有）和代码内容
        """
        ret = '**IPythonRunCellAction**\n'
        if self.thought:
            ret += f'THOUGHT: {self.thought}\n'
        ret += f'CODE:\n{self.code}'
        return ret

    @property
    def message(self) -> str:
        """获取Python代码执行的消息
        
        Returns:
            str: 格式化的代码执行消息
        """
        return f'Running Python code interactively: {self.code}'