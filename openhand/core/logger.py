"""
OpenHands 日志系统模块

此模块提供了 OpenHands 系统的完整日志功能，包括：
- 控制台和文件日志记录
- 彩色输出和格式化
- 敏感数据过滤
- LLM 特定的日志处理
- JSON 格式日志支持
- 滚动日志显示
"""

import copy
import logging
import os
import re
import sys
import traceback
from datetime import datetime
from types import TracebackType
from typing import Any, Literal, Mapping, MutableMapping, TextIO

import litellm
from pythonjsonlogger.json import JsonFormatter
from termcolor import colored

# 从环境变量获取日志配置
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()  # 日志级别
DEBUG = os.getenv('DEBUG', 'False').lower() in ['true', '1', 'yes']  # 调试模式
DEBUG_LLM = os.getenv('DEBUG_LLM', 'False').lower() in ['true', '1', 'yes']  # LLM 调试模式

# 结构化日志（JSON 格式），默认禁用
LOG_JSON = os.getenv('LOG_JSON', 'False').lower() in ['true', '1', 'yes']
LOG_JSON_LEVEL_KEY = os.getenv('LOG_JSON_LEVEL_KEY', 'level')  # JSON 日志级别键名


# 根据 DEBUG_LLM 配置 litellm 日志
if DEBUG_LLM:
    confirmation = input(
        '\n⚠️ 警告：你正在启用 DEBUG_LLM，这可能会暴露敏感信息，如 API 密钥。\n'
        '这在生产环境中绝对不应该启用。\n'
        "输入 'y' 确认你理解这些风险： "
    )
    if confirmation.lower() == 'y':
        litellm.suppress_debug_info = False
        litellm.set_verbose = True
    else:
        print('由于缺乏确认，DEBUG_LLM 已禁用')
        litellm.suppress_debug_info = True
        litellm.set_verbose = False
else:
    litellm.suppress_debug_info = True
    litellm.set_verbose = False

# 如果启用调试模式，将日志级别设为 DEBUG
if DEBUG:
    LOG_LEVEL = 'DEBUG'

# 其他日志配置
LOG_TO_FILE = os.getenv('LOG_TO_FILE', 'False').lower() in ['true', '1', 'yes']  # 是否记录到文件
DISABLE_COLOR_PRINTING = False  # 是否禁用彩色打印

LOG_ALL_EVENTS = os.getenv('LOG_ALL_EVENTS', 'False').lower() in ['true', '1', 'yes']  # 是否记录所有事件

# 控制是否流式传输 Docker 容器日志
DEBUG_RUNTIME = os.getenv('DEBUG_RUNTIME', 'False').lower() in ['true', '1', 'yes']

# 颜色类型定义
ColorType = Literal[
    'red',
    'green',
    'yellow',
    'blue',
    'magenta',
    'cyan',
    'light_grey',
    'dark_grey',
    'light_red',
    'light_green',
    'light_yellow',
    'light_blue',
    'light_magenta',
    'light_cyan',
    'white',
]

# 日志颜色映射
LOG_COLORS: Mapping[str, ColorType] = {
    'ACTION': 'green',              # 操作
    'USER_ACTION': 'light_red',     # 用户操作
    'OBSERVATION': 'yellow',        # 观察
    'USER_OBSERVATION': 'light_green',  # 用户观察
    'DETAIL': 'cyan',               # 详细信息
    'ERROR': 'red',                 # 错误
    'PLAN': 'light_magenta',        # 计划
}


class StackInfoFilter(logging.Filter):
    """
    堆栈信息过滤器。
    
    为错误级别的日志记录添加堆栈跟踪信息。
    """
    
    def filter(self, record: logging.LogRecord) -> bool:
        """
        过滤日志记录，为错误级别添加堆栈信息。
        
        Args:
            record: 日志记录对象
            
        Returns:
            总是返回 True，允许所有记录通过
        """
        if record.levelno >= logging.ERROR:
            # 仅在有实际异常时添加堆栈跟踪信息
            exc_info = sys.exc_info()
            if exc_info and exc_info[0] is not None:
                # 捕获当前堆栈跟踪作为字符串
                stack = traceback.format_stack()
                # 移除与日志机制相关的最后几个条目
                stack = stack[:-3]  # 如果需要，调整此数字
                # 将堆栈帧连接为单个字符串
                stack_str = ''.join(stack)
                setattr(record, 'stack_info', stack_str)
                setattr(record, 'exc_info', exc_info)
        return True


class NoColorFormatter(logging.Formatter):
    """文件中非彩色日志记录的格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        """
        格式化日志记录。
        
        Args:
            record: 要格式化的日志记录
            
        Returns:
            格式化后的日志字符串
        """
        # 创建记录的深拷贝以避免修改原始记录
        new_record = _fix_record(record)

        # 从消息中去除 ANSI 颜色代码
        new_record.msg = strip_ansi(new_record.msg)

        return super().format(new_record)


def strip_ansi(s: str) -> str:
    """
    从字符串中移除 ANSI 转义序列（终端颜色/格式代码）。

    移除字符串中的 ANSI 转义序列，如 ECMA-048 中定义的
    http://www.ecma-international.org/publications/files/ECMA-ST/Ecma-048.pdf
    # https://github.com/ewen-lbh/python-strip-ansi/blob/master/strip_ansi/__init__.py
    
    Args:
        s: 包含 ANSI 代码的字符串
        
    Returns:
        去除 ANSI 代码后的字符串
    """
    pattern = re.compile(r'\x1B\[\d+(;\d+){0,2}m')
    stripped = pattern.sub('', s)
    return stripped


class ColoredFormatter(logging.Formatter):
    """彩色日志格式化器。"""
    
    def format(self, record: logging.LogRecord) -> str:
        """
        格式化日志记录，添加颜色。
        
        Args:
            record: 要格式化的日志记录
            
        Returns:
            格式化后的彩色日志字符串
        """
        # 获取消息类型和事件源
        msg_type = record.__dict__.get('msg_type', '')
        event_source = record.__dict__.get('event_source', '')
        
        # 构建新的消息类型
        if event_source:
            new_msg_type = f'{event_source.upper()}_{msg_type}'
            if new_msg_type in LOG_COLORS:
                msg_type = new_msg_type
                
        # 应用颜色格式
        if msg_type in LOG_COLORS and not DISABLE_COLOR_PRINTING:
            msg_type_color = colored(msg_type, LOG_COLORS[msg_type])
            msg = colored(record.msg, LOG_COLORS[msg_type])
            time_str = colored(
                self.formatTime(record, self.datefmt), LOG_COLORS[msg_type]
            )
            name_str = colored(record.name, LOG_COLORS[msg_type])
            level_str = colored(record.levelname, LOG_COLORS[msg_type])
            
            # 错误或调试模式下显示更多信息
            if msg_type in ['ERROR'] or DEBUG:
                return f'{time_str} - {name_str}:{level_str}: {record.filename}:{record.lineno}\n{msg_type_color}\n{msg}'
            return f'{time_str} - {msg_type_color}\n{msg}'
        elif msg_type == 'STEP':
            # 步骤消息的特殊处理
            if LOG_ALL_EVENTS:
                msg = '\n\n==============\n' + record.msg + '\n'
                return f'{msg}'
            else:
                return record.msg

        # 默认格式化
        new_record = _fix_record(record)
        return super().format(new_record)


def _fix_record(record: logging.LogRecord) -> logging.LogRecord:
    """
    修复日志记录中的布尔值问题。
    
    Args:
        record: 原始日志记录
        
    Returns:
        修复后的日志记录
    """
    new_record = copy.copy(record)
    # 格式化器期望非布尔值，如果有布尔值会引发异常 - 所以我们修复这些
    # LogRecord 属性是动态类型的
    if getattr(new_record, 'exc_info', None) is True:
        setattr(new_record, 'exc_info', sys.exc_info())
        setattr(new_record, 'stack_info', None)
    return new_record


# 文件格式化器和 LLM 格式化器
file_formatter = NoColorFormatter(
    '%(asctime)s - %(name)s:%(levelname)s: %(filename)s:%(lineno)s - %(message)s',
    datefmt='%H:%M:%S',
)
llm_formatter = logging.Formatter('%(message)s')


class RollingLogger:
    """
    滚动日志记录器，用于在控制台中显示滚动的日志行。
    
    Attributes:
        max_lines: 最大显示行数
        char_limit: 每行字符限制
        log_lines: 日志行列表
        all_lines: 所有日志行的字符串
    """
    max_lines: int
    char_limit: int
    log_lines: list[str]
    all_lines: str

    def __init__(self, max_lines: int = 10, char_limit: int = 80) -> None:
        """
        初始化滚动日志记录器。
        
        Args:
            max_lines: 最大显示行数
            char_limit: 每行字符限制
        """
        self.max_lines = max_lines
        self.char_limit = char_limit
        self.log_lines = [''] * self.max_lines
        self.all_lines = ''

    def is_enabled(self) -> bool:
        """
        检查滚动日志是否启用。
        
        Returns:
            如果在调试模式且输出是终端则返回 True
        """
        return DEBUG and sys.stdout.isatty()

    def start(self, message: str = '') -> None:
        """
        开始滚动日志显示。
        
        Args:
            message: 可选的启动消息
        """
        if message:
            print(message)
        self._write('\n' * self.max_lines)
        self._flush()

    def add_line(self, line: str) -> None:
        """
        添加新的日志行。
        
        Args:
            line: 要添加的日志行
        """
        self.log_lines.pop(0)
        self.log_lines.append(line[: self.char_limit])
        self.print_lines()
        self.all_lines += line + '\n'

    def write_immediately(self, line: str) -> None:
        """
        立即写入日志行。
        
        Args:
            line: 要写入的日志行
        """
        self._write(line)
        self._flush()

    def print_lines(self) -> None:
        """
        在控制台中显示最后 n 行日志（不用于文件日志记录）。

        这将在控制台中创建滚动显示的效果。
        """
        self.move_back()
        for line in self.log_lines:
            self.replace_current_line(line)

    def move_back(self, amount: int = -1) -> None:
        """
        向上移动光标。
        
        r'\033[F' 将光标向上移动一行。
        
        Args:
            amount: 要移动的行数，-1 表示使用 max_lines
        """
        if amount == -1:
            amount = self.max_lines
        self._write('\033[F' * (self.max_lines))
        self._flush()

    def replace_current_line(self, line: str = '') -> None:
        """
        替换当前行。
        
        r'\033[2K\r' 清除行并将光标移动到行的开头。
        
        Args:
            line: 要显示的新行内容
        """
        self._write('\033[2K' + line + '\n')
        self._flush()

    def _write(self, line: str) -> None:
        """
        内部写入方法。
        
        Args:
            line: 要写入的内容
        """
        if not self.is_enabled():
            return
        sys.stdout.write(line)

    def _flush(self) -> None:
        """内部刷新方法。"""
        if not self.is_enabled():
            return
        sys.stdout.flush()


class SensitiveDataFilter(logging.Filter):
    """敏感数据过滤器，用于从日志中移除敏感信息。"""
    
    def filter(self, record: logging.LogRecord) -> bool:
        """
        过滤日志记录中的敏感数据。
        
        Args:
            record: 要过滤的日志记录
            
        Returns:
            总是返回 True，允许记录通过（但已清理敏感数据）
        """
        # 收集不应出现在日志中的敏感值
        sensitive_values = []
        for key, value in os.environ.items():
            key_upper = key.upper()
            if (
                len(value) > 2
                and value != 'default'
                and any(s in key_upper for s in ('SECRET', '_KEY', '_CODE', '_TOKEN'))
            ):
                sensitive_values.append(value)

        # 从环境变量替换敏感值！
        msg = record.getMessage()
        for sensitive_value in sensitive_values:
            msg = msg.replace(sensitive_value, '******')

        # 从日志本身替换明显的敏感值...
        sensitive_patterns = [
            'api_key',
            'aws_access_key_id',
            'aws_secret_access_key',
            'e2b_api_key',
            'github_token',
            'jwt_secret',
            'modal_api_token_id',
            'modal_api_token_secret',
            'llm_api_key',
            'sandbox_env_github_token',
            'runloop_api_key',
            'daytona_api_key',
        ]

        # 添加环境变量名称
        env_vars = [attr.upper() for attr in sensitive_patterns]
        sensitive_patterns.extend(env_vars)

        # 使用正则表达式替换敏感模式
        for attr in sensitive_patterns:
            pattern = rf"{attr}='?([\w-]+)'?"
            msg = re.sub(pattern, f"{attr}='******'", msg)

        # 更新记录
        record.msg = msg
        record.args = ()

        return True


def get_console_handler(log_level: int = logging.INFO) -> logging.StreamHandler:
    """
    返回用于日志记录的控制台处理器。
    
    Args:
        log_level: 日志级别
        
    Returns:
        配置好的控制台处理器
    """
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    formatter_str = '\033[92m%(asctime)s - %(name)s:%(levelname)s\033[0m: %(filename)s:%(lineno)s - %(message)s'
    console_handler.setFormatter(ColoredFormatter(formatter_str, datefmt='%H:%M:%S'))
    return console_handler


def get_file_handler(
    log_dir: str, log_level: int = logging.INFO
) -> logging.FileHandler:
    """
    返回用于日志记录的文件处理器。
    
    Args:
        log_dir: 日志目录
        log_level: 日志级别
        
    Returns:
        配置好的文件处理器
    """
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y-%m-%d')
    file_name = f'openhands_{timestamp}.log'
    file_handler = logging.FileHandler(os.path.join(log_dir, file_name))
    file_handler.setLevel(log_level)
    if LOG_JSON:
        file_handler.setFormatter(json_formatter())
    else:
        file_handler.setFormatter(file_formatter)
    return file_handler


def json_formatter() -> JsonFormatter:
    """
    创建 JSON 格式化器。
    
    Returns:
        配置好的 JSON 格式化器
    """
    return JsonFormatter(
        '{message}{levelname}',
        style='{',
        rename_fields={'levelname': LOG_JSON_LEVEL_KEY},
        timestamp=True,
    )


def json_log_handler(
    level: int = logging.INFO,
    _out: TextIO = sys.stdout,
) -> logging.Handler:
    """
    配置用于结构化日志记录（JSON 行）的日志记录器实例。
    
    Args:
        level: 日志级别
        _out: 输出流
        
    Returns:
        配置好的日志处理器
    """
    handler = logging.StreamHandler(_out)
    handler.setLevel(level)
    handler.setFormatter(json_formatter())
    return handler


# 设置基本日志记录
logging.basicConfig(level=logging.ERROR)


def log_uncaught_exceptions(
    ex_cls: type[BaseException], ex: BaseException, tb: TracebackType | None
) -> Any:
    """
    记录未捕获的异常及其回溯。

    Args:
        ex_cls: 异常的类型
        ex: 异常实例
        tb: 回溯对象

    Returns:
        None
    """
    if tb:  # 添加检查，因为 tb 可能为 None
        logging.error(''.join(traceback.format_tb(tb)))
    logging.error('{0}: {1}'.format(ex_cls, ex))


# 设置未捕获异常处理
sys.excepthook = log_uncaught_exceptions

# 创建主日志记录器
openhands_logger = logging.getLogger('openhands')
current_log_level = logging.INFO

# 设置日志级别
if LOG_LEVEL in logging.getLevelNamesMapping():
    current_log_level = logging.getLevelNamesMapping()[LOG_LEVEL]
openhands_logger.setLevel(current_log_level)

# 添加堆栈信息过滤器（如果在调试模式）
if DEBUG:
    openhands_logger.addFilter(StackInfoFilter())

# 如果是调试级别，启用文件日志
if current_log_level == logging.DEBUG:
    LOG_TO_FILE = True
    openhands_logger.debug('调试模式已启用。')

# 添加处理器
if LOG_JSON:
    openhands_logger.addHandler(json_log_handler(current_log_level))
else:
    openhands_logger.addHandler(get_console_handler(current_log_level))

# 添加敏感数据过滤器
openhands_logger.addFilter(SensitiveDataFilter(openhands_logger.name))
openhands_logger.propagate = False
openhands_logger.debug('日志记录已初始化')

# 日志目录
LOG_DIR = os.path.join(
    # openhands/core 的父目录（即 repo 的根目录）
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'logs',
)

# 如果启用文件日志，添加文件处理器
if LOG_TO_FILE:
    openhands_logger.addHandler(
        get_file_handler(LOG_DIR, current_log_level)
    )  # 默认日志到项目根目录
    openhands_logger.debug(f'正在记录到文件：{LOG_DIR}')

# 排除 LiteLLM 的日志输出，因为它可能泄漏密钥
logging.getLogger('LiteLLM').disabled = True
logging.getLogger('LiteLLM Router').disabled = True
logging.getLogger('LiteLLM Proxy').disabled = True

# 排除冗长的日志记录器
LOQUACIOUS_LOGGERS = [
    'engineio',
    'engineio.server',
    'socketio',
    'socketio.client',
    'socketio.server',
]

for logger_name in LOQUACIOUS_LOGGERS:
    logging.getLogger(logger_name).setLevel('WARNING')


class LlmFileHandler(logging.FileHandler):
    """LLM 提示和响应日志记录的文件处理器。"""

    def __init__(
        self,
        filename: str,
        mode: str = 'a',
        encoding: str = 'utf-8',
        delay: bool = False,
    ) -> None:
        """
        初始化 LlmFileHandler 实例。

        Args:
            filename: 日志文件名
            mode: 文件模式，默认为 'a'
            encoding: 文件编码，默认为 'utf-8'
            delay: 是否延迟文件打开，默认为 False
        """
        self.filename = filename
        self.message_counter = 1
        if DEBUG:
            self.session = datetime.now().strftime('%y-%m-%d_%H-%M')
        else:
            self.session = 'default'
        self.log_directory = os.path.join(LOG_DIR, 'llm', self.session)
        os.makedirs(self.log_directory, exist_ok=True)
        
        if not DEBUG:
            # 如果不在调试模式，清除日志目录
            for file in os.listdir(self.log_directory):
                file_path = os.path.join(self.log_directory, file)
                try:
                    os.unlink(file_path)
                except Exception as e:
                    openhands_logger.error(
                        '删除 %s 失败。原因：%s', file_path, e
                    )
        
        filename = f'{self.filename}_{self.message_counter:03}.log'
        self.baseFilename = os.path.join(self.log_directory, filename)
        super().__init__(self.baseFilename, mode, encoding, delay)

    def emit(self, record: logging.LogRecord) -> None:
        """
        发出日志记录。

        Args:
            record: 要发出的日志记录
        """
        filename = f'{self.filename}_{self.message_counter:03}.log'
        self.baseFilename = os.path.join(self.log_directory, filename)
        self.stream = self._open()
        super().emit(record)
        self.stream.close()
        openhands_logger.debug('正在记录到 %s', self.baseFilename)
        self.message_counter += 1


def _get_llm_file_handler(name: str, log_level: int) -> LlmFileHandler:
    """
    获取 LLM 文件处理器。
    
    Args:
        name: 处理器名称
        log_level: 日志级别
        
    Returns:
        配置好的 LLM 文件处理器
    """
    # 'delay' 参数设置为 True 时，推迟日志文件的打开
    # 直到发出第一条日志消息。
    llm_file_handler = LlmFileHandler(name, delay=True)
    llm_file_handler.setFormatter(llm_formatter)
    llm_file_handler.setLevel(log_level)
    return llm_file_handler


def _setup_llm_logger(name: str, log_level: int) -> logging.Logger:
    """
    设置 LLM 日志记录器。
    
    Args:
        name: 日志记录器名称
        log_level: 日志级别
        
    Returns:
        配置好的日志记录器
    """
    logger = logging.getLogger(name)
    logger.propagate = False
    logger.setLevel(log_level)
    if LOG_TO_FILE:
        logger.addHandler(_get_llm_file_handler(name, log_level))
    return logger


# 创建 LLM 特定的日志记录器
llm_prompt_logger = _setup_llm_logger('prompt', current_log_level)
llm_response_logger = _setup_llm_logger('response', current_log_level)


class OpenHandsLoggerAdapter(logging.LoggerAdapter):
    """
    OpenHands 日志记录器适配器。
    
    Attributes:
        extra: 额外的日志记录信息字典
    """
    extra: dict

    def __init__(
        self, logger: logging.Logger = openhands_logger, extra: dict | None = None
    ) -> None:
        """
        初始化日志记录器适配器。
        
        Args:
            logger: 基础日志记录器
            extra: 额外的日志记录信息
        """
        self.logger = logger
        self.extra = extra or {}

    def process(
        self, msg: str, kwargs: MutableMapping[str, Any]
    ) -> tuple[str, MutableMapping[str, Any]]:
        """
        处理日志消息和参数。
        
        如果在 kwargs 中提供了 'extra'，将其与适配器的 'extra' 字典合并。
        从 Python 3.13 开始，LoggerAdapter 的 merge_extra 选项将执行此操作。
        
        Args:
            msg: 日志消息
            kwargs: 关键字参数
            
        Returns:
            处理后的消息和参数元组
        """
        if 'extra' in kwargs and isinstance(kwargs['extra'], dict):
            kwargs['extra'] = {**self.extra, **kwargs['extra']}
        else:
            kwargs['extra'] = self.extra
        return msg, kwargs