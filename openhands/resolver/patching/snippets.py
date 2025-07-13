# -*- coding: utf-8 -*-
"""
snippets.py - 通用工具函数模块

该模块提供了一些通用的工具函数，包括：
- 文件和目录的删除操作
- 正则表达式匹配和列表分割功能
- 可执行程序查找功能

这些函数被其他模块广泛使用，提供基础的操作支持。
"""

import os
import re
from shutil import rmtree


def remove(path: str) -> None:
    """
    删除文件或目录
    
    该函数可以安全地删除文件或目录，如果路径不存在则不执行任何操作。
    对于目录，会递归删除整个目录树。
    
    Args:
        path: 要删除的文件或目录路径
    """
    if os.path.exists(path):
        if os.path.isdir(path):
            # 如果是目录，递归删除整个目录树
            rmtree(path)
        else:
            # 如果是文件，直接删除
            os.remove(path)


def findall_regex(items: list[str], regex: re.Pattern[str]) -> list[int]:
    """
    查找字符串列表中所有匹配正则表达式的项的索引
    
    Args:
        items: 要搜索的字符串列表
        regex: 编译后的正则表达式Pattern对象
        
    Returns:
        list[int]: 匹配的项在列表中的索引列表
        
    Example:
        >>> import re
        >>> items = ["hello", "world", "hello world"]
        >>> pattern = re.compile(r"hello")
        >>> findall_regex(items, pattern)
        [0, 2]
    """
    found = list()
    for i in range(0, len(items)):
        k = regex.match(items[i])  # 尝试从字符串开头匹配
        if k:
            found.append(i)
            k = None  # 重置匹配结果

    return found


def split_by_regex(items: list[str], regex: re.Pattern[str]) -> list[list[str]]:
    """
    使用正则表达式分割字符串列表
    
    该函数根据正则表达式匹配的位置将字符串列表分割成多个子列表。
    分割点本身会作为新子列表的第一个元素。
    
    Args:
        items: 要分割的字符串列表
        regex: 用于确定分割位置的正则表达式Pattern对象
        
    Returns:
        list[list[str]]: 分割后的子列表组成的列表
        
    Example:
        >>> import re
        >>> items = ["a", "---", "b", "c", "---", "d"]
        >>> pattern = re.compile(r"---")
        >>> split_by_regex(items, pattern)
        [["a"], ["---", "b", "c"], ["---", "d"]]
    """
    splits = list()
    # 找到所有匹配正则表达式的索引位置
    indices = findall_regex(items, regex)
    if not indices:
        # 如果没有找到匹配项，返回整个列表作为单个子列表
        splits.append(items)
        return splits

    # 添加第一个匹配项之前的内容
    splits.append(items[0 : indices[0]])

    # 添加匹配项之间的内容块
    for i in range(len(indices) - 1):
        splits.append(items[indices[i] : indices[i + 1]])

    # 添加最后一个匹配项之后的内容
    splits.append(items[indices[-1] :])

    return splits


def which(program: str) -> str | None:
    """
    查找可执行程序的完整路径
    
    类似于Unix系统中的which命令，在系统PATH环境变量中查找指定的可执行程序。
    
    Args:
        program: 要查找的程序名称
        
    Returns:
        str | None: 程序的完整路径，如果未找到则返回None
        
    Reference:
        基于 http://stackoverflow.com/questions/377017/test-if-executable-exists-in-python
    """
    def is_exe(fpath: str) -> bool:
        """
        检查文件是否为可执行文件
        
        Args:
            fpath: 文件路径
            
        Returns:
            bool: 如果文件存在且可执行则返回True，否则返回False
        """
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    # 分离路径和文件名
    fpath, fname = os.path.split(program)
    if fpath:
        # 如果程序名包含路径，直接检查该路径下的文件
        if is_exe(program):
            return program
    else:
        # 如果程序名不包含路径，在PATH环境变量中搜索
        for path in os.environ['PATH'].split(os.pathsep):
            path = path.strip('"')  # 移除路径中的引号
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file

    return None