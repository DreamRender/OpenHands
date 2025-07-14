import os
from pathlib import Path

from openhands.events.observation import (
    ErrorObservation,
    FileReadObservation,
    FileWriteObservation,
    Observation,
)


def resolve_path(
    file_path: str,
    working_directory: str,
    workspace_base: str,
    workspace_mount_path_in_sandbox: str,
) -> Path:
    """
    将文件路径解析为主机文件系统上的路径。

    Args:
        file_path (str): 要解析的路径
        working_directory (str): Agent的工作目录
        workspace_mount_path_in_sandbox (str): sandbox内workspace的路径
        workspace_base (str): 主机文件系统上workspace的基础路径

    Returns:
        Path: 主机文件系统上的解析路径

    Raises:
        PermissionError: 如果路径在workspace外部
    """
    path_in_sandbox = Path(file_path)

    # 应用工作目录
    if not path_in_sandbox.is_absolute():
        path_in_sandbox = Path(working_directory) / path_in_sandbox

    # 相对于完整sandbox根目录对路径进行清理
    # （拒绝任何..路径遍历到sandbox父目录）
    abs_path_in_sandbox = path_in_sandbox.resolve()

    # 如果路径在workspace外部，拒绝访问
    if not abs_path_in_sandbox.is_relative_to(workspace_mount_path_in_sandbox):
        raise PermissionError(f'File access not permitted: {file_path}')
        # 翻译：不允许文件访问：{file_path}

    # 获取相对于sandbox内workspace根目录的路径
    path_in_workspace = abs_path_in_sandbox.relative_to(
        Path(workspace_mount_path_in_sandbox)
    )

    # 获取相对于主机的路径
    path_in_host_workspace = Path(workspace_base) / path_in_workspace

    return path_in_host_workspace


def read_lines(all_lines: list[str], start: int = 0, end: int = -1) -> list[str]:
    """
    从行列表中读取指定范围的行。

    Args:
        all_lines (list[str]): 所有行的列表
        start (int): 开始行号（0-indexed），默认为0
        end (int): 结束行号（0-indexed），-1表示到末尾，默认为-1

    Returns:
        list[str]: 指定范围内的行列表
    """
    # 确保start在有效范围内
    start = max(start, 0)
    start = min(start, len(all_lines))
    
    # 处理end参数
    end = -1 if end == -1 else max(end, 0)
    end = min(end, len(all_lines))
    
    if end == -1:
        if start == 0:
            return all_lines
        else:
            return all_lines[start:]
    else:
        num_lines = len(all_lines)
        begin = max(0, min(start, num_lines - 2))
        end = -1 if end > num_lines else max(begin + 1, end)
        return all_lines[begin:end]


async def read_file(
    path: str,
    workdir: str,
    workspace_base: str,
    workspace_mount_path_in_sandbox: str,
    start: int = 0,
    end: int = -1,
) -> Observation:
    """
    异步读取文件内容。

    Args:
        path (str): 文件路径
        workdir (str): 工作目录
        workspace_base (str): workspace基础路径
        workspace_mount_path_in_sandbox (str): sandbox内workspace挂载路径
        start (int): 开始行号，默认为0
        end (int): 结束行号，-1表示到末尾，默认为-1

    Returns:
        Observation: 文件读取观察对象或错误观察对象
    """
    try:
        # 解析文件路径
        whole_path = resolve_path(
            path, workdir, workspace_base, workspace_mount_path_in_sandbox
        )
    except PermissionError:
        return ErrorObservation(
            f"You're not allowed to access this path: {path}. You can only access paths inside the workspace."
        )
        # 翻译：您不被允许访问此路径：{path}。您只能访问workspace内的路径。

    try:
        # 读取文件
        with open(whole_path, 'r', encoding='utf-8') as file:  # noqa: ASYNC101
            lines = read_lines(file.readlines(), start, end)
    except FileNotFoundError:
        return ErrorObservation(f'File not found: {path}')
        # 翻译：文件未找到：{path}
    except UnicodeDecodeError:
        return ErrorObservation(f'File could not be decoded as utf-8: {path}')
        # 翻译：文件无法解码为utf-8：{path}
    except IsADirectoryError:
        return ErrorObservation(f'Path is a directory: {path}. You can only read files')
        # 翻译：路径是目录：{path}。您只能读取文件
    
    # 将行列表合并为字符串
    code_view = ''.join(lines)
    return FileReadObservation(path=path, content=code_view)


def insert_lines(
    to_insert: list[str], original: list[str], start: int = 0, end: int = -1
) -> list[str]:
    """
    基于start和end将新内容插入到原始内容中。

    Args:
        to_insert (list[str]): 要插入的行列表
        original (list[str]): 原始行列表
        start (int): 开始位置，默认为0
        end (int): 结束位置，-1表示到末尾，默认为-1

    Returns:
        list[str]: 插入后的行列表
    """
    # 构建新的行列表
    new_lines = [''] if start == 0 else original[:start]
    new_lines += [i + '\n' for i in to_insert]
    new_lines += [''] if end == -1 else original[end:]
    return new_lines


async def write_file(
    path: str,
    workdir: str,
    workspace_base: str,
    workspace_mount_path_in_sandbox: str,
    content: str,
    start: int = 0,
    end: int = -1,
) -> Observation:
    """
    异步写入文件内容。

    Args:
        path (str): 文件路径
        workdir (str): 工作目录
        workspace_base (str): workspace基础路径
        workspace_mount_path_in_sandbox (str): sandbox内workspace挂载路径
        content (str): 要写入的内容
        start (int): 开始行号，默认为0
        end (int): 结束行号，-1表示到末尾，默认为-1

    Returns:
        Observation: 文件写入观察对象或错误观察对象
    """
    # 将内容按行分割
    insert = content.split('\n')

    try:
        # 解析文件路径
        whole_path = resolve_path(
            path, workdir, workspace_base, workspace_mount_path_in_sandbox
        )
        
        # 如果目录不存在，创建目录
        if not os.path.exists(os.path.dirname(whole_path)):
            os.makedirs(os.path.dirname(whole_path))
        
        # 确定文件打开模式
        mode = 'w' if not os.path.exists(whole_path) else 'r+'
        
        try:
            # 写入文件
            with open(whole_path, mode, encoding='utf-8') as file:  # noqa: ASYNC101
                if mode != 'w':
                    # 如果文件已存在，读取现有内容
                    all_lines = file.readlines()
                    new_file = insert_lines(insert, all_lines, start, end)
                else:
                    # 如果是新文件，直接写入
                    new_file = [i + '\n' for i in insert]

                # 写入新内容
                file.seek(0)
                file.writelines(new_file)
                file.truncate()
        except FileNotFoundError:
            return ErrorObservation(f'File not found: {path}')
            # 翻译：文件未找到：{path}
        except IsADirectoryError:
            return ErrorObservation(
                f'Path is a directory: {path}. You can only write to files'
            )
            # 翻译：路径是目录：{path}。您只能写入文件
        except UnicodeDecodeError:
            return ErrorObservation(f'File could not be decoded as utf-8: {path}')
            # 翻译：文件无法解码为utf-8：{path}
    except PermissionError as e:
        return ErrorObservation(f'Permission error on {path}: {e}')
        # 翻译：{path}权限错误：{e}
    
    return FileWriteObservation(content='', path=path)
