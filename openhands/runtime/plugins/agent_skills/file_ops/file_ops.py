"""OpenHands Agent的文件操作模块

该模块为OpenHands Agent提供了一系列文件操作技能，
使Agent能够执行各种文件操作，如打开、搜索和浏览文件及目录。

功能函数:
- open_file(path: str, line_number: int | None = 1, context_lines: int = 100): 打开文件并可选择移动到指定行
- goto_line(line_number: int): 移动窗口以显示指定行号
- scroll_down(): 向下移动窗口WINDOW指定的行数
- scroll_up(): 向上移动窗口WINDOW指定的行数
- search_dir(search_term: str, dir_path: str = './'): 在指定目录的所有文件中搜索术语
- search_file(search_term: str, file_path: str | None = None): 在指定文件或当前打开的文件中搜索术语
- find_file(file_name: str, dir_path: str = './'): 在指定目录中查找所有具有给定名称的文件

注意:
    所有函数都返回其结果的字符串表示形式。
"""

import os

from openhands.linter import DefaultLinter, LintResult

# 全局变量：当前打开文件的路径，初始值为None表示没有文件打开
CURRENT_FILE: str | None = None

# 全局变量：当前文件中的行号位置，默认从第1行开始
CURRENT_LINE = 1

# 全局变量：显示窗口的行数大小，默认显示100行
WINDOW = 100

# 单元测试中也会使用这个消息模板！
# 文件更新消息模板，用于通知用户文件已在指定行号处被编辑
MSG_FILE_UPDATED = '[File updated (edited at line {line_number}). Please review the changes and make sure they are correct (correct indentation, no duplicate lines, etc). Edit the file again if necessary.]'

# 语法检查错误消息，当编辑引入语法错误时显示
LINTER_ERROR_MSG = '[Your proposed edit has introduced new syntax error(s). Please understand the errors and retry your edit command.]\n'


# ==================================================================================================


def _output_error(error_msg: str) -> bool:
    """输出错误消息的辅助函数
    
    该函数格式化并打印错误消息，并返回False表示操作失败。
    
    Args:
        error_msg: 要输出的错误消息字符串
        
    Returns:
        bool: 总是返回False，表示错误状态
    """
    print(f'ERROR: {error_msg}')
    return False


def _is_valid_filename(file_name: str) -> bool:
    """验证文件名是否有效
    
    检查文件名是否为空、是否包含无效字符等，
    根据不同操作系统应用相应的文件名验证规则。
    
    Args:
        file_name: 要验证的文件名
        
    Returns:
        bool: 文件名有效返回True，否则返回False
    """
    # 检查文件名是否为空或不是字符串类型
    if not file_name or not isinstance(file_name, str) or not file_name.strip():
        return False
    
    # 默认无效字符集
    invalid_chars = '<>:"/\\|?*'
    
    # 根据操作系统设置无效字符
    if os.name == 'nt':  # Windows系统
        invalid_chars = '<>:"/\\|?*'
    elif os.name == 'posix':  # Unix-like系统（Linux、macOS等）
        invalid_chars = '\0'  # 只有空字符是无效的

    # 检查文件名中是否包含无效字符
    for char in invalid_chars:
        if char in file_name:
            return False
    return True


def _is_valid_path(path: str) -> bool:
    """验证路径是否有效且存在
    
    检查给定路径是否为有效的字符串，并且该路径在文件系统中确实存在。
    
    Args:
        path: 要验证的路径字符串
        
    Returns:
        bool: 路径有效且存在返回True，否则返回False
    """
    # 检查路径是否为空或不是字符串类型
    if not path or not isinstance(path, str):
        return False
    try:
        # 标准化路径并检查是否存在
        return os.path.exists(os.path.normpath(path))
    except PermissionError:
        # 如果没有权限访问，返回False
        return False


def _create_paths(file_name: str) -> bool:
    """创建文件路径中的目录结构
    
    根据给定的文件名，创建其路径中所需的所有目录。
    如果目录已存在则不会重复创建。
    
    Args:
        file_name: 文件的完整路径名
        
    Returns:
        bool: 目录创建成功返回True，否则返回False
    """
    try:
        # 获取文件的目录部分
        dirname = os.path.dirname(file_name)
        if dirname:
            # 递归创建目录结构，exist_ok=True表示目录存在时不报错
            os.makedirs(dirname, exist_ok=True)
        return True
    except PermissionError:
        # 如果没有权限创建目录，返回False
        return False


def _check_current_file(file_path: str | None = None) -> bool:
    """检查当前文件是否有效
    
    验证当前打开的文件或指定的文件路径是否存在且为有效文件。
    如果没有文件打开或文件不存在，会输出错误消息。
    
    Args:
        file_path: 可选的文件路径，如果不提供则使用当前打开的文件
        
    Returns:
        bool: 文件有效返回True，否则返回False
    """
    global CURRENT_FILE
    
    # 如果没有提供文件路径，使用当前打开的文件
    if not file_path:
        file_path = CURRENT_FILE
    
    # 检查文件路径是否有效且文件存在
    if not file_path or not os.path.isfile(file_path):
        return _output_error('No file open. Use the open_file function first.')
    return True


def _clamp(value: int, min_value: int, max_value: int) -> int:
    """将数值限制在指定范围内
    
    确保给定值在最小值和最大值之间，如果超出范围则调整到边界值。
    
    Args:
        value: 要限制的数值
        min_value: 允许的最小值
        max_value: 允许的最大值
        
    Returns:
        int: 限制后的数值
    """
    return max(min_value, min(value, max_value))


def _lint_file(file_path: str) -> tuple[str | None, int | None]:
    """对文件执行语法检查并识别第一个错误位置
    
    对给定路径的文件进行语法检查，返回是否存在错误的信息，
    以及第一个错误的行号（如果有的话）。
    
    Args:
        file_path: 要检查的文件路径
        
    Returns:
        tuple[str | None, int | None]: 包含以下内容的元组:
            - 如果发现语法错误则返回错误消息，否则返回None
            - 第一个错误的行号，如果没有错误则返回None
    """
    # 创建默认语法检查器实例
    linter = DefaultLinter()
    # 执行语法检查
    lint_error: list[LintResult] = linter.lint(file_path)
    
    if not lint_error:
        # 语法检查成功，没有发现问题
        return None, None
    
    # 获取第一个错误的行号
    first_error_line = lint_error[0].line if len(lint_error) > 0 else None
    
    # 构建错误信息文本
    error_text = 'ERRORS:\n' + '\n'.join(
        [f'{file_path}:{err.line}:{err.column}: {err.message}' for err in lint_error]
    )
    return error_text, first_error_line


def _print_window(
    file_path: str | None,
    targeted_line: int,
    window: int,
    return_str: bool = False,
    ignore_window: bool = False,
) -> str:
    """打印文件内容的窗口视图
    
    显示文件中指定行周围的内容窗口，可以配置窗口大小和显示模式。
    
    Args:
        file_path: 文件路径
        targeted_line: 目标行号（窗口中心或起始位置）
        window: 窗口大小（显示的行数）
        return_str: 是否返回字符串而不是直接打印，默认为False
        ignore_window: 是否忽略窗口居中，使用targeted_line作为起始行，默认为False
        
    Returns:
        str: 如果return_str为True则返回格式化的字符串，否则返回空字符串
    """
    global CURRENT_LINE
    
    # 检查文件是否有效
    if not _check_current_file(file_path) or file_path is None:
        return ''
    
    # 读取文件内容
    with open(file_path) as file:
        content = file.read()

        # 确保内容以换行符结尾
        if not content.endswith('\n'):
            content += '\n'

        # 分割成行，保留行结束符
        lines = content.splitlines(True)
        total_lines = len(lines)

        # 处理边界情况，确保当前行在有效范围内
        CURRENT_LINE = _clamp(targeted_line, 1, total_lines)
        half_window = max(1, window // 2)
        
        if ignore_window:
            # 使用CURRENT_LINE作为起始行（用于scroll_down等操作）
            start = max(1, CURRENT_LINE)
            end = min(total_lines, CURRENT_LINE + window)
        else:
            # 确保目标行上下至少各有一行
            start = max(1, CURRENT_LINE - half_window)
            end = min(total_lines, CURRENT_LINE + half_window)

        # 调整起始和结束位置以确保上下至少各有一行
        if start == 1:
            end = min(total_lines, start + window - 1)
        if end == total_lines:
            start = max(1, end - window + 1)

        output = ''

        # 只有当上方还有行时才显示提示信息
        if start > 1:
            output += f'({start - 1} more lines above)\n'
        else:
            output += '(this is the beginning of the file)\n'
        
        # 输出窗口范围内的行，添加行号前缀
        for i in range(start, end + 1):
            _new_line = f'{i}|{lines[i - 1]}'
            if not _new_line.endswith('\n'):
                _new_line += '\n'
            output += _new_line
        
        # 显示下方还有多少行或文件结束提示
        if end < total_lines:
            output += f'({total_lines - end} more lines below)\n'
        else:
            output += '(this is the end of the file)\n'
        
        # 去除末尾空白
        output = output.rstrip()

        if return_str:
            return output
        else:
            print(output)
            return ''


def _cur_file_header(current_file: str | None, total_lines: int) -> str:
    """生成当前文件的头部信息
    
    创建包含文件绝对路径和总行数的头部信息字符串。
    
    Args:
        current_file: 当前文件路径
        total_lines: 文件总行数
        
    Returns:
        str: 格式化的文件头部信息字符串
    """
    if not current_file:
        return ''
    return f'[File: {os.path.abspath(current_file)} ({total_lines} lines total)]\n'


def open_file(
    path: str, line_number: int | None = 1, context_lines: int | None = WINDOW
) -> None:
    """在编辑器中打开文件并可选择定位到指定行
    
    该函数显示有限的内容窗口，如果提供了行号则围绕该行号居中显示。
    要查看完整的文件内容，Agent应该使用scroll_down和scroll_up命令迭代浏览。
    
    Args:
        path: 要打开的文件路径，建议使用绝对路径
        line_number: 要居中显示的目标行号，默认为1
        context_lines: 视图窗口中显示的最大行数，限制为100行，默认为100
        
    Returns:
        None
    """
    global CURRENT_FILE, CURRENT_LINE, WINDOW

    # 检查文件是否存在
    if not os.path.isfile(path):
        _output_error(f'File {path} not found.')
        return

    # 设置当前文件为绝对路径
    CURRENT_FILE = os.path.abspath(path)
    
    # 计算文件总行数
    with open(CURRENT_FILE) as file:
        total_lines = max(1, sum(1 for _ in file))

    # 验证行号的有效性
    if not isinstance(line_number, int) or line_number < 1 or line_number > total_lines:
        _output_error(f'Line number must be between 1 and {total_lines}')
        return
    CURRENT_LINE = line_number

    # 使用context_lines覆盖默认WINDOW设置
    if context_lines is None or context_lines < 1:
        context_lines = WINDOW

    # 生成文件头部信息和内容窗口
    output = _cur_file_header(CURRENT_FILE, total_lines)
    output += _print_window(
        CURRENT_FILE,
        CURRENT_LINE,
        _clamp(context_lines, 1, 100),  # 限制最大显示100行
        return_str=True,
        ignore_window=False,
    )
    
    # 如果下方还有更多行，提示使用scroll_down查看
    if output.strip().endswith('more lines below)'):
        output += '\n[Use `scroll_down` to view the next 100 lines of the file!]'
    print(output)


def goto_line(line_number: int) -> None:
    """移动窗口以显示指定行号
    
    将当前文件的视图窗口移动到指定行号，并围绕该行号显示内容。
    
    Args:
        line_number: 要移动到的行号
        
    Returns:
        None
    """
    global CURRENT_FILE, CURRENT_LINE, WINDOW
    
    # 检查是否有文件打开
    if not _check_current_file():
        return

    # 计算文件总行数
    with open(str(CURRENT_FILE)) as file:
        total_lines = max(1, sum(1 for _ in file))
    
    # 验证行号的有效性
    if not isinstance(line_number, int) or line_number < 1 or line_number > total_lines:
        _output_error(f'Line number must be between 1 and {total_lines}.')
        return

    # 更新当前行位置
    CURRENT_LINE = _clamp(line_number, 1, total_lines)
    
    # 生成并显示文件内容
    output = _cur_file_header(CURRENT_FILE, total_lines)
    output += _print_window(
        CURRENT_FILE, CURRENT_LINE, WINDOW, return_str=True, ignore_window=False
    )
    print(output)


def scroll_down() -> None:
    """向下移动窗口100行
    
    将当前文件的视图窗口向下滚动WINDOW（100）行，
    显示文件的后续内容。
    
    Args:
        None
        
    Returns:
        None
    """
    global CURRENT_FILE, CURRENT_LINE, WINDOW
    
    # 检查是否有文件打开
    if not _check_current_file():
        return
    
    # 计算文件总行数
    with open(str(CURRENT_FILE)) as file:
        total_lines = max(1, sum(1 for _ in file))
    
    # 向下移动当前行位置
    CURRENT_LINE = _clamp(CURRENT_LINE + WINDOW, 1, total_lines)
    
    # 生成并显示文件内容
    output = _cur_file_header(CURRENT_FILE, total_lines)
    output += _print_window(
        CURRENT_FILE, CURRENT_LINE, WINDOW, return_str=True, ignore_window=True
    )
    print(output)


def scroll_up() -> None:
    """向上移动窗口100行
    
    将当前文件的视图窗口向上滚动WINDOW（100）行，
    显示文件的前面内容。
    
    Args:
        None
        
    Returns:
        None
    """
    global CURRENT_FILE, CURRENT_LINE, WINDOW
    
    # 检查是否有文件打开
    if not _check_current_file():
        return
    
    # 计算文件总行数
    with open(str(CURRENT_FILE)) as file:
        total_lines = max(1, sum(1 for _ in file))
    
    # 向上移动当前行位置
    CURRENT_LINE = _clamp(CURRENT_LINE - WINDOW, 1, total_lines)
    
    # 生成并显示文件内容
    output = _cur_file_header(CURRENT_FILE, total_lines)
    output += _print_window(
        CURRENT_FILE, CURRENT_LINE, WINDOW, return_str=True, ignore_window=True
    )
    print(output)


class LineNumberError(Exception):
    """行号错误异常类
    
    当行号超出有效范围或格式不正确时抛出的自定义异常。
    """
    pass


def search_dir(search_term: str, dir_path: str = './') -> None:
    """在目录中的所有文件中搜索指定术语
    
    如果没有提供目录路径，则在当前目录中搜索。
    该函数会递归搜索指定目录及其子目录中的所有文件。
    
    Args:
        search_term: 要搜索的术语
        dir_path: 要搜索的目录路径，默认为当前目录
        
    Returns:
        None
    """
    # 检查目录是否存在
    if not os.path.isdir(dir_path):
        _output_error(f'Directory {dir_path} not found')
        return
    
    matches = []
    
    # 递归遍历目录树
    for root, _, files in os.walk(dir_path):
        for file in files:
            # 跳过隐藏文件（以.开头的文件）
            if file.startswith('.'):
                continue
            
            file_path = os.path.join(root, file)
            
            # 在文件中搜索术语，忽略编码错误
            with open(file_path, 'r', errors='ignore') as f:
                for line_num, line in enumerate(f, 1):
                    if search_term in line:
                        # 记录匹配结果：文件路径、行号、行内容
                        matches.append((file_path, line_num, line.strip()))

    # 如果没有找到匹配结果
    if not matches:
        print(f'No matches found for "{search_term}" in {dir_path}')
        return

    # 统计匹配信息
    num_matches = len(matches)
    num_files = len(set(match[0] for match in matches))

    # 如果匹配的文件数量过多，建议缩小搜索范围
    if num_files > 100:
        print(
            f'More than {num_files} files matched for "{search_term}" in {dir_path}. Please narrow your search.'
        )
        return

    # 输出搜索结果
    print(f'[Found {num_matches} matches for "{search_term}" in {dir_path}]')
    for file_path, line_num, line in matches:
        print(f'{file_path} (Line {line_num}): {line}')
    print(f'[End of matches for "{search_term}" in {dir_path}]')


def search_file(search_term: str, file_path: str | None = None) -> None:
    """在文件中搜索指定术语
    
    如果没有提供文件路径，则在当前打开的文件中搜索。
    
    Args:
        search_term: 要搜索的术语
        file_path: 要搜索的文件路径，如果为None则使用当前打开的文件
        
    Returns:
        None
    """
    global CURRENT_FILE
    
    # 如果没有提供文件路径，使用当前打开的文件
    if file_path is None:
        file_path = CURRENT_FILE
    
    # 检查文件是否指定或打开
    if file_path is None:
        _output_error('No file specified or open. Use the open_file function first.')
        return
    
    # 检查文件是否存在
    if not os.path.isfile(file_path):
        _output_error(f'File {file_path} not found.')
        return

    matches = []
    
    # 在文件中搜索术语
    with open(file_path) as file:
        for i, line in enumerate(file, 1):
            if search_term in line:
                # 记录匹配结果：行号、行内容
                matches.append((i, line.strip()))

    # 输出搜索结果
    if matches:
        print(f'[Found {len(matches)} matches for "{search_term}" in {file_path}]')
        for match in matches:
            print(f'Line {match[0]}: {match[1]}')
        print(f'[End of matches for "{search_term}" in {file_path}]')
    else:
        print(f'[No matches found for "{search_term}" in {file_path}]')


def find_file(file_name: str, dir_path: str = './') -> None:
    """在指定目录中查找具有给定名称的所有文件
    
    该函数会递归搜索指定目录及其子目录，
    查找文件名中包含指定字符串的所有文件。
    
    Args:
        file_name: 要查找的文件名（支持部分匹配）
        dir_path: 要搜索的目录路径，默认为当前目录
        
    Returns:
        None
    """
    # 检查目录是否存在
    if not os.path.isdir(dir_path):
        _output_error(f'Directory {dir_path} not found')
        return

    matches = []
    
    # 递归遍历目录树查找匹配的文件
    for root, _, files in os.walk(dir_path):
        for file in files:
            if file_name in file:
                # 记录匹配的文件完整路径
                matches.append(os.path.join(root, file))

    # 输出查找结果
    if matches:
        print(f'[Found {len(matches)} matches for "{file_name}" in {dir_path}]')
        for match in matches:
            print(f'{match}')
        print(f'[End of matches for "{file_name}" in {dir_path}]')
    else:
        print(f'[No matches found for "{file_name}" in {dir_path}]')


# 定义模块对外暴露的公共接口
__all__ = [
    'open_file',    # 打开文件功能
    'goto_line',    # 跳转到指定行功能
    'scroll_down',  # 向下滚动功能
    'scroll_up',    # 向上滚动功能
    'search_dir',   # 目录搜索功能
    'search_file',  # 文件内搜索功能
    'find_file',    # 文件查找功能
]
