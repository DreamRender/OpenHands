import argparse
import sys


def read_input(cli_multiline_input: bool = False) -> str:
    """
    根据配置设置从用户读取输入。
    
    Args:
        cli_multiline_input (bool): 是否启用多行输入模式，默认为False
        
    Returns:
        str: 用户输入的字符串内容
        
    Notes:
        如果启用多行输入模式，用户需要在新行输入"/exit"来结束输入。
        如果是单行模式，用户输入一行后直接返回。
    """
    if cli_multiline_input:
        # 提示用户进入多行输入模式
        print('Enter your message (enter "/exit" on a new line to finish):')
        lines = []  # 存储所有输入行的列表
        while True:
            # 读取一行输入并去除右侧空白字符
            line = input('>> ').rstrip()
            if line == '/exit':  # 检查是否为结束输入的标记
                break
            lines.append(line)  # 将当前行添加到列表中
        # 将所有行用换行符连接成一个字符串返回
        return '\n'.join(lines)
    else:
        # 单行输入模式：读取一行并去除右侧空白字符
        return input('>> ').rstrip()


def read_task_from_file(file_path: str) -> str:
    """
    从指定文件读取任务内容。
    
    Args:
        file_path (str): 要读取的文件路径
        
    Returns:
        str: 文件中的任务内容
        
    Raises:
        FileNotFoundError: 当指定文件不存在时
        IOError: 当文件读取失败时
    """
    # 以UTF-8编码打开文件并读取全部内容
    with open(file_path, 'r', encoding='utf-8') as file:
        return file.read()


def read_task(args: argparse.Namespace, cli_multiline_input: bool) -> str:
    """
    从CLI参数、文件或标准输入读取任务内容。
    
    Args:
        args (argparse.Namespace): 命令行参数对象，包含file和task属性
        cli_multiline_input (bool): 是否启用多行输入模式
        
    Returns:
        str: 读取到的任务字符串
        
    Notes:
        读取优先级：
        1. 如果args.file存在，从文件读取
        2. 如果args.task存在，直接使用该任务字符串
        3. 如果标准输入不是终端（即有管道输入），从标准输入读取
        4. 否则返回空字符串
    """
    # 初始化任务字符串
    task_str = ''
    
    if args.file:
        # 优先级1：从指定文件读取任务
        task_str = read_task_from_file(args.file)
    elif args.task:
        # 优先级2：直接使用命令行提供的任务字符串
        task_str = args.task
    elif not sys.stdin.isatty():
        # 优先级3：如果标准输入不是终端（有管道输入），从标准输入读取
        task_str = read_input(cli_multiline_input)

    return task_str