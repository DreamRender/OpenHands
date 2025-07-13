# -*- coding: utf-8 -*-
"""
apply.py - diff应用模块

该模块提供了将diff应用到文本内容的功能，支持正向和反向应用diff，
并可以选择使用系统的patch程序或内置的Python实现。
"""

import os.path
import subprocess
import tempfile

from .exceptions import HunkApplyException, SubprocessException
from .patch import Change, diffobj
from .snippets import remove, which


def _apply_diff_with_subprocess(
    diff: diffobj, lines: list[str], reverse: bool = False
) -> tuple[list[str], list[str] | None]:
    """
    使用系统的patch程序应用diff到文本行
    
    Args:
        diff: diff对象，包含header、changes和原始文本
        lines: 需要应用diff的文本行列表
        reverse: 是否反向应用diff（撤销更改）
        
    Returns:
        tuple: (处理后的文本行列表, 被拒绝的行列表或None)
        
    Raises:
        SubprocessException: 当找不到patch程序或patch程序执行失败时
    """
    # 调用外部patch程序
    patchexec = which('patch')
    if not patchexec:
        raise SubprocessException('cannot find patch program', code=-1)

    # 获取系统临时目录
    tempdir = tempfile.gettempdir()

    # 根据diff header的hash值生成唯一的临时文件名
    filepath = os.path.join(tempdir, 'wtp-' + str(hash(diff.header)))
    oldfilepath = filepath + '.old'      # 原始文件路径
    newfilepath = filepath + '.new'      # 输出文件路径
    rejfilepath = filepath + '.rej'      # 被拒绝的patch文件路径
    patchfilepath = filepath + '.patch'  # patch文件路径
    
    # 将原始文本写入临时文件
    with open(oldfilepath, 'w') as f:
        f.write('\n'.join(lines) + '\n')

    # 将diff内容写入patch文件
    with open(patchfilepath, 'w') as f:
        f.write(diff.text)

    # 构建patch命令参数
    args = [
        patchexec,
        '--reverse' if reverse else '--forward',  # 选择正向或反向应用
        '--quiet',                                # 静默模式
        '--no-backup-if-mismatch',               # 不匹配时不创建备份
        '-o',                                    # 指定输出文件
        newfilepath,
        '-i',                                    # 指定patch文件
        patchfilepath,
        '-r',                                    # 指定拒绝文件
        rejfilepath,
        oldfilepath,                             # 输入文件
    ]
    # 执行patch命令
    ret = subprocess.call(args)

    # 读取处理后的文件内容
    with open(newfilepath) as f:
        lines = f.read().splitlines()

    # 尝试读取被拒绝的patch内容
    try:
        with open(rejfilepath) as f:
            rejlines = f.read().splitlines()
    except IOError:
        # 如果没有被拒绝的内容，rejlines为None
        rejlines = None

    # 清理临时文件
    remove(oldfilepath)
    remove(newfilepath)
    remove(rejfilepath)
    remove(patchfilepath)

    # 最后检查patch程序的返回值，确保文件已被清理
    if ret != 0:
        raise SubprocessException('patch program failed', code=ret)

    return lines, rejlines


def _reverse(changes: list[Change]) -> list[Change]:
    """
    反转Change对象列表，用于反向应用diff
    
    Args:
        changes: Change对象列表
        
    Returns:
        list[Change]: 反转后的Change对象列表，其中old和new字段互换
    """
    def _reverse_change(c: Change) -> Change:
        """
        反转单个Change对象，将old和new字段互换
        
        Args:
            c: 需要反转的Change对象
            
        Returns:
            Change: 反转后的Change对象
        """
        return c._replace(old=c.new, new=c.old)

    return [_reverse_change(c) for c in changes]


def apply_diff(
    diff: diffobj, text: str | list[str], reverse: bool = False, use_patch: bool = False
) -> list[str]:
    """
    将diff应用到文本内容
    
    Args:
        diff: diff对象，包含要应用的更改
        text: 要应用diff的文本，可以是字符串或字符串列表
        reverse: 是否反向应用diff（默认False）
        use_patch: 是否使用系统的patch程序（默认False，使用内置实现）
        
    Returns:
        list[str]: 应用diff后的文本行列表
        
    Raises:
        HunkApplyException: 当hunk无法应用时（上下文不匹配）
    """
    # 将输入文本统一转换为行列表
    lines = text.splitlines() if isinstance(text, str) else list(text)

    # 如果指定使用patch程序，则调用subprocess实现
    if use_patch:
        lines, _ = _apply_diff_with_subprocess(diff, lines, reverse)
        return lines

    # 获取文本总行数
    n_lines = len(lines)

    # 根据reverse参数决定是否反转changes
    changes = _reverse(diff.changes) if reverse else diff.changes
    
    # 检查源文本是否与diff的上下文匹配
    for old, new, line, hunk in changes:
        # 对于ed脚本，可能line为None，这里需要检查
        if old is not None and line is not None:
            # 检查行号是否超出文件范围
            if old > n_lines:
                raise HunkApplyException(
                    'context line {n}, "{line}" does not exist in source'.format(
                        n=old, line=line
                    ),
                    hunk=hunk,
                )
            # 检查上下文行是否匹配
            if lines[old - 1] != line:
                # 尝试通过标准化空白字符来处理缩进差异
                # 这有助于处理具有不同缩进级别的patch
                normalized_line = ' '.join(line.split())
                normalized_source = ' '.join(lines[old - 1].split())
                if normalized_line != normalized_source:
                    raise HunkApplyException(
                        'context line {n}, "{line}" does not match "{sl}"'.format(
                            n=old, line=line, sl=lines[old - 1]
                        ),
                        hunk=hunk,
                    )

    # 用于计算原始行号的偏移量
    r = 0  # 删除的行数
    i = 0  # 插入的行数

    # 逐个应用changes
    for old, new, line, hunk in changes:
        if old is not None and new is None:
            # 删除操作：old行被删除
            del lines[old - 1 - r + i]
            r += 1
        elif old is None and new is not None:
            # 插入操作：在new位置插入line
            lines.insert(new - 1, line)
            i += 1
        elif old is not None and new is not None:
            # 上下文行或替换操作
            # 有时候，人们会从patch中删除hunks，使这些行号完全不可靠
            # 因为他们是混蛋（原注释保留）
            pass

    return lines