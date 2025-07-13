import json
import re
import traceback
from dataclasses import dataclass, field
from typing import Any, Self

from pydantic import BaseModel

from openhands.core.logger import openhands_logger as logger
from openhands.core.schema import ObservationType
from openhands.events.observation.observation import Observation

# PS1提示符相关的常量定义，用于从命令输出中提取Metadata
CMD_OUTPUT_PS1_BEGIN = '\n###PS1JSON###\n'  # PS1 JSON开始标记
CMD_OUTPUT_PS1_END = '\n###PS1END###'        # PS1 JSON结束标记

# 用于匹配PS1 Metadata的正则表达式
CMD_OUTPUT_METADATA_PS1_REGEX = re.compile(
    f'^{CMD_OUTPUT_PS1_BEGIN.strip()}(.*?){CMD_OUTPUT_PS1_END.strip()}',
    re.DOTALL | re.MULTILINE,  # DOTALL使.匹配换行符，MULTILINE使^和$匹配每行的开始和结束
)


class CmdOutputMetadata(BaseModel):
    """命令输出Metadata类
    
    从PS1提示符中捕获的附加Metadata信息。
    PS1是Linux/Unix shell的主提示符，可以配置为输出结构化信息。
    
    Attributes:
        exit_code (int): 命令的退出码，-1表示未知
        pid (int): 进程ID，-1表示未知
        username (str | None): 执行命令的用户名
        hostname (str | None): 执行命令的主机名
        working_dir (str | None): 命令执行时的工作目录
        py_interpreter_path (str | None): Python解释器的路径
        prefix (str): 添加到命令输出前面的前缀
        suffix (str): 添加到命令输出后面的后缀
    """

    exit_code: int = -1  # 命令退出码，0表示成功，非0表示失败
    pid: int = -1  # 进程ID标识符
    username: str | None = None  # 执行用户名
    hostname: str | None = None  # 主机名
    working_dir: str | None = None  # 工作目录路径
    py_interpreter_path: str | None = None  # Python解释器路径
    prefix: str = ''  # 输出前缀内容
    suffix: str = ''  # 输出后缀内容

    @classmethod
    def to_ps1_prompt(cls) -> str:
        """生成PS1提示符字符串
        
        将所需的Metadata转换为PS1提示符格式。
        这个方法生成一个特殊格式的字符串，可以设置为shell的PS1变量，
        使得每次命令执行后都会输出结构化的Metadata信息。
        
        Returns:
            str: 格式化的PS1提示符字符串
        """
        prompt = CMD_OUTPUT_PS1_BEGIN
        
        # 构建包含shell变量的JSON字符串
        json_str = json.dumps(
            {
                'pid': '$!',  # 最后一个后台进程的PID
                'exit_code': '$?',  # 上一个命令的退出状态
                'username': r'\u',  # 当前用户名
                'hostname': r'\h',  # 主机名
                'working_dir': r'$(pwd)',  # 当前工作目录
                'py_interpreter_path': r'$(which python 2>/dev/null || echo "")',  # Python路径
            },
            indent=2,
        )
        
        # 转义JSON字符串中的双引号，确保PS1能正确保留它们
        prompt += json_str.replace('"', r'\"')
        prompt += CMD_OUTPUT_PS1_END + '\n'  # 确保末尾有换行符
        return prompt

    @classmethod
    def matches_ps1_metadata(cls, string: str) -> list[re.Match[str]]:
        """匹配字符串中的PS1 Metadata
        
        在给定字符串中查找所有符合PS1 Metadata格式的匹配项。
        
        Args:
            string (str): 要搜索的字符串
            
        Returns:
            list[re.Match[str]]: 所有有效的匹配项列表
        """
        matches = []
        
        # 查找所有匹配的PS1 Metadata块
        for match in CMD_OUTPUT_METADATA_PS1_REGEX.finditer(string):
            try:
                # 尝试解析为JSON，验证格式正确性
                json.loads(match.group(1).strip())
                matches.append(match)
            except json.JSONDecodeError:
                # 如果JSON解析失败，记录警告并跳过
                logger.warning(
                    f'Failed to parse PS1 metadata: {match.group(1)}. Skipping.'
                    + traceback.format_exc()
                )
                continue
        return matches

    @classmethod
    def from_ps1_match(cls, match: re.Match[str]) -> Self:
        """从PS1匹配项中提取Metadata
        
        从正则表达式匹配结果中解析并创建CmdOutputMetadata实例。
        
        Args:
            match (re.Match[str]): PS1 Metadata的正则匹配结果
            
        Returns:
            Self: 解析后的CmdOutputMetadata实例
        """
        # 解析JSON Metadata
        metadata = json.loads(match.group(1))
        
        # 创建副本以避免修改原始数据
        processed = metadata.copy()
        
        # 转换数值字段，处理可能的格式错误
        if 'pid' in metadata:
            try:
                processed['pid'] = int(float(str(metadata['pid'])))
            except (ValueError, TypeError):
                processed['pid'] = -1
                
        if 'exit_code' in metadata:
            try:
                processed['exit_code'] = int(float(str(metadata['exit_code'])))
            except (ValueError, TypeError):
                logger.warning(
                    f'Failed to parse exit code: {metadata["exit_code"]}. Setting to -1.'
                )
                processed['exit_code'] = -1
                
        return cls(**processed)


@dataclass
class CmdOutputObservation(Observation):
    """命令输出观察类
    
    这个数据类表示命令执行的输出结果。
    包含命令本身、执行结果以及从PS1中提取的Metadata信息。
    
    Attributes:
        command (str): 执行的命令字符串
        observation (str): 观察类型，固定为RUN
        metadata (CmdOutputMetadata): 从PS1提取的附加Metadata
        hidden (bool): 命令输出是否应该对用户隐藏
    """

    command: str  # 执行的命令字符串
    observation: str = ObservationType.RUN  # 观察类型标识
    metadata: CmdOutputMetadata = field(default_factory=CmdOutputMetadata)  # 命令执行的Metadata
    hidden: bool = False  # 是否隐藏命令输出

    def __init__(
        self,
        content: str,
        command: str,
        observation: str = ObservationType.RUN,
        metadata: dict[str, Any] | CmdOutputMetadata | None = None,
        hidden: bool = False,
        **kwargs: Any,
    ) -> None:
        """初始化命令输出观察实例
        
        Args:
            content (str): 命令的输出内容
            command (str): 执行的命令
            observation (str): 观察类型
            metadata (dict | CmdOutputMetadata | None): Metadata信息
            hidden (bool): 是否隐藏输出
            **kwargs: 其他参数，包括向后兼容的字段
        """
        super().__init__(content)
        self.command = command
        self.observation = observation
        self.hidden = hidden
        
        # 处理Metadata参数，支持字典或对象形式
        if isinstance(metadata, dict):
            self.metadata = CmdOutputMetadata(**metadata)
        else:
            self.metadata = metadata or CmdOutputMetadata()

        # 处理向后兼容的属性
        if 'exit_code' in kwargs:
            self.metadata.exit_code = kwargs['exit_code']
        if 'command_id' in kwargs:
            self.metadata.pid = kwargs['command_id']

    @property
    def command_id(self) -> int:
        """获取命令ID
        
        Returns:
            int: 进程ID，用作命令标识符
        """
        return self.metadata.pid

    @property
    def exit_code(self) -> int:
        """获取退出码
        
        Returns:
            int: 命令的退出码
        """
        return self.metadata.exit_code

    @property
    def error(self) -> bool:
        """判断命令是否出错
        
        Returns:
            bool: 如果退出码不为0则表示出错
        """
        return self.exit_code != 0

    @property
    def message(self) -> str:
        """返回消息内容
        
        Returns:
            str: 包含命令和退出码的格式化消息
        """
        return f'Command `{self.command}` executed with exit code {self.exit_code}.'

    @property
    def success(self) -> bool:
        """判断命令是否成功执行
        
        Returns:
            bool: 与error属性相反，成功时为True
        """
        return not self.error

    def __str__(self) -> str:
        """返回字符串表示
        
        Returns:
            str: 详细的格式化字符串表示
        """
        return (
            f'**CmdOutputObservation (source={self.source}, exit code={self.exit_code}, '
            f'metadata={json.dumps(self.metadata.model_dump(), indent=2)})**\n'
            '--BEGIN AGENT OBSERVATION--\n'
            f'{self.to_agent_observation()}\n'
            '--END AGENT OBSERVATION--'
        )

    def to_agent_observation(self) -> str:
        """生成给Agent的观察内容
        
        将命令输出格式化为Agent可读的形式，包含附加的上下文信息。
        
        Returns:
            str: 格式化后的Agent观察内容
        """
        # 基础内容包含前缀、输出内容和后缀
        ret = f'{self.metadata.prefix}{self.content}{self.metadata.suffix}'
        
        # 添加当前工作目录信息
        if self.metadata.working_dir:
            ret += f'\n[Current working directory: {self.metadata.working_dir}]'
            
        # 添加Python解释器路径信息
        if self.metadata.py_interpreter_path:
            ret += f'\n[Python interpreter: {self.metadata.py_interpreter_path}]'
            
        # 添加退出码信息（如果可用）
        if self.metadata.exit_code != -1:
            ret += f'\n[Command finished with exit code {self.metadata.exit_code}]'
            
        return ret


@dataclass
class IPythonRunCellObservation(Observation):
    """IPython执行单元观察类
    
    这个数据类表示IPythonRunCellAction的输出结果。
    IPython单元格是交互式Python执行环境中的代码块。
    
    Attributes:
        code (str): 执行的Python代码
        observation (str): 观察类型，固定为RUN_IPYTHON
        image_urls (list[str] | None): 生成的图片URL列表，可能为None
    """

    code: str  # 执行的Python代码内容
    observation: str = ObservationType.RUN_IPYTHON  # 观察类型标识
    image_urls: list[str] | None = None  # 执行过程中生成的图片URL列表

    @property
    def error(self) -> bool:
        """判断是否有错误
        
        IPython单元格不返回退出码，所以总是返回False。
        
        Returns:
            bool: 总是返回False，表示IPython单元格不会报告错误状态
        """
        return False

    @property
    def message(self) -> str:
        """返回消息内容
        
        Returns:
            str: 固定的执行确认消息
        """
        return 'Code executed in IPython cell.'

    @property
    def success(self) -> bool:
        """判断执行是否成功
        
        IPython单元格总是被认为是成功的，即使代码执行过程中可能有异常。
        
        Returns:
            bool: 总是返回True
        """
        return True

    def __str__(self) -> str:
        """返回字符串表示
        
        Returns:
            str: 包含执行结果和可能的图片信息的格式化字符串
        """
        result = f'**IPythonRunCellObservation**\n{self.content}'
        
        # 如果有生成的图片，添加图片数量信息
        if self.image_urls:
            result += f'\nImages: {len(self.image_urls)}'
            
        return result