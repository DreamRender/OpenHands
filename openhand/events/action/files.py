from dataclasses import dataclass
from typing import ClassVar

from openhands.core.schema import ActionType
from openhands.events.action.action import Action, ActionSecurityRisk
from openhands.events.event import FileEditSource, FileReadSource


@dataclass
class FileReadAction(Action):
    """文件读取Action
    
    从指定路径读取文件内容的Action。
    支持指定起始和结束行号来读取文件的特定部分。
    默认读取整个文件（行号 0:-1）。
    
    Attributes:
        path (str): 要读取的文件路径
        start (int): 开始行号，默认为0（文件开头）
        end (int): 结束行号，默认为-1（文件结尾）
        thought (str): Agent的思考过程，默认为空字符串
        action (str): Action类型，固定为ActionType.READ
        runnable (ClassVar[bool]): 类变量，表示此Action可以被执行
        security_risk (ActionSecurityRisk | None): 安全风险等级评估
        impl_source (FileReadSource): 实现来源，默认为DEFAULT
        view_range (list[int] | None): 视图范围，仅在OH_ACI模式下使用
    """

    path: str  # 文件路径
    start: int = 0  # 起始行号（包含）
    end: int = -1  # 结束行号（包含，-1表示文件末尾）
    thought: str = ''  # Agent的思考过程
    action: str = ActionType.READ  # Action类型标识
    runnable: ClassVar[bool] = True  # 标识此Action可执行
    security_risk: ActionSecurityRisk | None = None  # 安全风险评估
    impl_source: FileReadSource = FileReadSource.DEFAULT  # 实现来源
    view_range: list[int] | None = None  # 视图范围（仅OH_ACI模式使用）

    @property
    def message(self) -> str:
        """获取文件读取的消息
        
        Returns:
            str: 格式化的文件读取消息
        """
        return f'Reading file: {self.path}'


@dataclass
class FileWriteAction(Action):
    """文件写入Action
    
    向指定路径写入文件内容的Action。
    支持指定起始和结束行号来写入文件的特定部分。
    默认写入整个文件（行号 0:-1）。
    
    Attributes:
        path (str): 要写入的文件路径
        content (str): 要写入的文件内容
        start (int): 开始行号，默认为0（文件开头）
        end (int): 结束行号，默认为-1（文件结尾）
        thought (str): Agent的思考过程，默认为空字符串
        action (str): Action类型，固定为ActionType.WRITE
        runnable (ClassVar[bool]): 类变量，表示此Action可以被执行
        security_risk (ActionSecurityRisk | None): 安全风险等级评估
    """

    path: str  # 文件路径
    content: str  # 要写入的内容
    start: int = 0  # 起始行号（包含）
    end: int = -1  # 结束行号（包含，-1表示文件末尾）
    thought: str = ''  # Agent的思考过程
    action: str = ActionType.WRITE  # Action类型标识
    runnable: ClassVar[bool] = True  # 标识此Action可执行
    security_risk: ActionSecurityRisk | None = None  # 安全风险评估

    @property
    def message(self) -> str:
        """获取文件写入的消息
        
        Returns:
            str: 格式化的文件写入消息
        """
        return f'Writing file: {self.path}'

    def __repr__(self) -> str:
        """获取FileWriteAction的详细字符串表示
        
        Returns:
            str: 包含路径、行号范围、思考过程和内容的详细描述
        """
        return (
            f'**FileWriteAction**\n'
            f'Path: {self.path}\n'
            f'Range: [L{self.start}:L{self.end}]\n'
            f'Thought: {self.thought}\n'
            f'Content:\n```\n{self.content}\n```\n'
        )


@dataclass
class FileEditAction(Action):
    """文件编辑Action
    
    使用各种命令编辑文件的Action，包括view、create、str_replace、insert和undo_edit。
    
    此类支持两种主要操作模式：
    1. 基于LLM的编辑 (impl_source = FileEditSource.LLM_BASED_EDIT)
    2. 基于ACI的编辑 (impl_source = FileEditSource.OH_ACI)

    Attributes:
        path (str): 要编辑的文件路径。适用于基于LLM和OH_ACI的编辑。
        
        仅OH_ACI参数:
            command (str): 要执行的编辑命令 (view, create, str_replace, insert, undo_edit, write)
            file_text (str): 要创建的文件内容（在OH_ACI模式下使用'create'命令时使用）
            old_str (str): 要被替换的字符串（在OH_ACI模式下使用'str_replace'命令时使用）
            new_str (str): 替换old_str的字符串（在OH_ACI模式下使用'str_replace'和'insert'命令时使用）
            insert_line (int): 插入new_str的行号（在OH_ACI模式下使用'insert'命令时使用）
            
        基于LLM编辑的参数:
            content (str): 要写入或编辑到文件中的内容（用于基于LLM的编辑和'write'命令）
            start (int): 编辑的起始行（从1开始，包含）。默认为1
            end (int): 编辑的结束行（从1开始，包含）。默认为-1（文件末尾）
            thought (str): 编辑操作背后的推理
            action (str): 执行的操作类型（总是ActionType.EDIT）
            
        runnable (bool): 指示该操作是否可以执行（总是True）
        security_risk (ActionSecurityRisk | None): 指示与操作相关的任何安全风险
        impl_source (FileEditSource): 实现来源（LLM_BASED_EDIT或OH_ACI）

    用法:
        - 对于基于LLM的编辑：使用path、content、start和end属性
        - 对于基于ACI的编辑：使用path、command和特定命令的相应属性

    注意:
        - 如果在基于LLM的编辑中start设置为-1，内容将被附加到文件
        - 'write'命令的行为类似于基于LLM的编辑，使用content、start和end属性
    """

    path: str  # 文件路径

    # OH_ACI专用参数
    command: str = ''  # 编辑命令
    file_text: str | None = None  # 创建文件时的文本内容
    old_str: str | None = None  # 要替换的旧字符串
    new_str: str | None = None  # 替换用的新字符串
    insert_line: int | None = None  # 插入行号

    # 基于LLM编辑的参数
    content: str = ''  # 文件内容
    start: int = 1  # 起始行号（从1开始）
    end: int = -1  # 结束行号（-1表示文件末尾）

    # 共享参数
    thought: str = ''  # 思考过程
    action: str = ActionType.EDIT  # Action类型标识
    runnable: ClassVar[bool] = True  # 标识此Action可执行
    security_risk: ActionSecurityRisk | None = None  # 安全风险评估
    impl_source: FileEditSource = FileEditSource.OH_ACI  # 实现来源，默认为OH_ACI

    def __repr__(self) -> str:
        """获取FileEditAction的详细字符串表示
        
        根据不同的实现来源（LLM_BASED_EDIT或OH_ACI）返回相应的格式化描述。
        
        Returns:
            str: 包含路径、思考过程和具体操作内容的详细描述
        """
        ret = '**FileEditAction**\n'
        ret += f'Path: [{self.path}]\n'
        ret += f'Thought: {self.thought}\n'

        if self.impl_source == FileEditSource.LLM_BASED_EDIT:
            # 基于LLM的编辑模式：显示行号范围和内容
            ret += f'Range: [L{self.start}:L{self.end}]\n'
            ret += f'Content:\n```\n{self.content}\n```\n'
        else:  # OH_ACI模式
            ret += f'Command: {self.command}\n'
            if self.command == 'create':
                # 创建文件命令：显示创建的文件内容
                ret += f'Created File with Text:\n```\n{self.file_text}\n```\n'
            elif self.command == 'str_replace':
                # 字符串替换命令：显示旧字符串和新字符串
                ret += f'Old String: ```\n{self.old_str}\n```\n'
                ret += f'New String: ```\n{self.new_str}\n```\n'
            elif self.command == 'insert':
                # 插入命令：显示插入行号和新字符串
                ret += f'Insert Line: {self.insert_line}\n'
                ret += f'New String: ```\n{self.new_str}\n```\n'
            elif self.command == 'undo_edit':
                # 撤销编辑命令
                ret += 'Undo Edit\n'
            # 忽略"view"命令，因为它会被映射到FileReadAction
        return ret