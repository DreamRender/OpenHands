# -*- coding: utf-8 -*-
"""
patch.py - patch文件解析模块

该模块提供了解析各种格式的patch文件的功能，包括：
- unified diff格式
- context diff格式  
- git diff格式
- svn diff格式
- cvs diff格式
- ed格式
- 二进制diff格式

支持从文本中提取diff信息并转换为结构化的数据对象。
"""

import base64
import re
import zlib
from collections import namedtuple
from typing import Iterable

from . import exceptions
from .snippets import findall_regex, split_by_regex

# ============================================================================
# 数据结构定义
# ============================================================================

header = namedtuple(
    'header',
    'index_path old_path old_version new_path new_version',
)
"""
patch header信息的命名元组

Fields:
    index_path (str): 索引路径，通常在版本控制系统中使用
    old_path (str): 原始文件路径
    old_version (str): 原始文件版本
    new_path (str): 新文件路径  
    new_version (str): 新文件版本
"""

diffobj = namedtuple('diffobj', 'header changes text')
"""
diff对象的命名元组

Fields:
    header: patch header信息
    changes (list[Change]): 变更列表
    text (str): 原始diff文本
"""

Change = namedtuple('Change', 'old new line hunk')
"""
单个变更的命名元组

Fields:
    old (int | None): 原始文件中的行号，None表示新增行
    new (int | None): 新文件中的行号，None表示删除行
    line (str): 行内容
    hunk (int): 所属的hunk编号
"""

# ============================================================================
# 正则表达式定义
# ============================================================================

# 文件时间戳格式的通用正则表达式
file_timestamp_str = '(.+?)(?:\t|:|  +)(.*)'
# .+? 之前是 [^:\t\n\r\f\v]+，现在使用非贪婪匹配更灵活

# 通用diff格式的正则表达式
diffcmd_header = re.compile('^diff.* (.+) (.+)$')
"""匹配diff命令头部，如：diff -u file1 file2"""

# unified diff格式的正则表达式
unified_header_index = re.compile('^Index: (.+)$')
"""匹配Index行，如：Index: filename"""

unified_header_old_line = re.compile(r'^--- ' + file_timestamp_str + '$')
"""匹配原始文件行，如：--- filename timestamp"""

unified_header_new_line = re.compile(r'^\+\+\+ ' + file_timestamp_str + '$')
"""匹配新文件行，如：+++ filename timestamp"""

unified_hunk_start = re.compile(r'^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@(.*)$')
"""匹配unified diff的hunk开始行，如：@@ -1,6 +1,6 @@"""

unified_change = re.compile('^([-+ ])(.*)$', re.MULTILINE)
"""匹配unified diff中的变更行，包括删除(-)、新增(+)和上下文( )"""

# context diff格式的正则表达式
context_header_old_line = re.compile(r'^\*\*\* ' + file_timestamp_str + '$')
"""匹配context diff的原始文件行"""

context_header_new_line = re.compile('^--- ' + file_timestamp_str + '$')
"""匹配context diff的新文件行"""

context_hunk_start = re.compile(r'^\*\*\*\*\*\*\*\*\*\*\*\*\*\*\*$')
"""匹配context diff的hunk分隔符"""

context_hunk_old = re.compile(r'^\*\*\* (\d+),?(\d*) \*\*\*\*$')
"""匹配context diff中原始文件的行号范围"""

context_hunk_new = re.compile(r'^--- (\d+),?(\d*) ----$')
"""匹配context diff中新文件的行号范围"""

context_change = re.compile('^([-+ !]) (.*)$')
"""匹配context diff中的变更行"""

# ed格式的正则表达式
ed_hunk_start = re.compile(r'^(\d+),?(\d*)([acd])$')
"""匹配ed格式的hunk开始，包括行号和操作类型(a/c/d)"""

ed_hunk_end = re.compile('^.$')
"""匹配ed格式的hunk结束标记"""

# RCS ed格式的正则表达式（类似ed但没有'c'类型）
rcs_ed_hunk_start = re.compile(r'^([ad])(\d+) ?(\d*)$')
"""匹配RCS ed格式的hunk开始"""

# 默认diff格式的正则表达式
default_hunk_start = re.compile(r'^(\d+),?(\d*)([acd])(\d+),?(\d*)$')
"""匹配默认diff格式的hunk开始"""

default_hunk_mid = re.compile('^---$')
"""匹配默认diff格式的hunk中间分隔符"""

default_change = re.compile('^([><]) (.*)$')
"""匹配默认diff格式的变更行"""

# ============================================================================
# Git相关的正则表达式
# ============================================================================

# Git有特殊的index header且没有结尾部分
git_diffcmd_header = re.compile('^diff --git a/(.+) b/(.+)$')
"""匹配git diff命令头部"""

git_header_index = re.compile(r'^index ([a-f0-9]+)..([a-f0-9]+) ?(\d*)$')
"""匹配git的index行，包含对象hash"""

git_header_old_line = re.compile('^--- (.+)$')
"""匹配git diff的原始文件行"""

git_header_new_line = re.compile(r'^\+\+\+ (.+)$')
"""匹配git diff的新文件行"""

git_header_file_mode = re.compile(r'^(new|deleted) file mode \d{6}$')
"""匹配git的文件模式行"""

git_header_binary_file = re.compile('^Binary files (.+) and (.+) differ')
"""匹配git的二进制文件差异行"""

git_binary_patch_start = re.compile(r'^GIT binary patch$')
"""匹配git二进制patch的开始"""

git_binary_literal_start = re.compile(r'^literal (\d+)$')
"""匹配git二进制literal块的开始"""

git_binary_delta_start = re.compile(r'^delta (\d+)$')
"""匹配git二进制delta块的开始"""

base85string = re.compile(r'^[0-9A-Za-z!#$%&()*+;<=>?@^_`{|}~-]+$')
"""匹配base85编码的字符串"""

# ============================================================================
# 其他版本控制系统的正则表达式
# ============================================================================

# Bazaar相关
bzr_header_index = re.compile('=== (.+)')
"""匹配Bazaar的文件标识行"""

bzr_header_old_line = unified_header_old_line
"""Bazaar使用与unified diff相同的原始文件行格式"""

bzr_header_new_line = unified_header_new_line
"""Bazaar使用与unified diff相同的新文件行格式"""

# SVN相关
svn_header_index = unified_header_index
"""SVN使用与unified diff相同的Index行格式"""

svn_header_timestamp_version = re.compile(r'\((?:working copy|revision (\d+))\)')
"""匹配SVN的时间戳中的版本信息"""

svn_header_timestamp = re.compile(r'.*(\(.*\))$')
"""匹配SVN的完整时间戳"""

# CVS相关
cvs_header_index = unified_header_index
"""CVS使用与unified diff相同的Index行格式"""

cvs_header_rcs = re.compile(r'^RCS file: (.+)(?:,\w{1}$|$)')
"""匹配CVS的RCS文件行"""

cvs_header_timestamp = re.compile(r'(.+)\t([\d.]+)')
"""匹配CVS的时间戳格式"""

cvs_header_timestamp_colon = re.compile(r':([\d.]+)\t(.+)')
"""匹配CVS的带冒号的时间戳格式"""

old_cvs_diffcmd_header = re.compile('^diff.* (.+):(.*) (.+):(.*)$')
"""匹配旧式CVS diff命令头部"""


# ============================================================================
# 主要解析函数
# ============================================================================

def parse_patch(text: str | list[str]) -> Iterable[diffobj]:
    """
    解析patch文本，返回diff对象的迭代器
    
    Args:
        text: patch文本，可以是字符串或字符串列表
        
    Yields:
        diffobj: 解析出的diff对象
        
    该函数会自动识别patch的格式并相应地解析。
    """
    # 将输入文本标准化为行列表
    lines = text.splitlines() if isinstance(text, str) else text

    # 移除行尾的换行符，确保每行都是纯文本
    # 也许可以用这个来清除所有的换行符？
    # lines = [x.splitlines()[0] for x in lines]
    lines = [x if len(x) == 0 else x.splitlines()[0] for x in lines]

    # 尝试不同的header格式来分割diff
    check = [
        unified_header_index,     # unified diff的Index行
        diffcmd_header,          # 通用diff命令行
        cvs_header_rcs,          # CVS的RCS文件行
        git_header_index,        # git的index行
        context_header_old_line, # context diff的原始文件行
        unified_header_old_line, # unified diff的原始文件行
    ]

    # 使用不同的正则表达式尝试分割文本
    diffs = []
    for c in check:
        diffs = split_by_regex(lines, c)
        if len(diffs) > 1:
            # 如果找到了分割点，就使用这个结果
            break

    # 逐个处理分割出的diff块
    for diff in diffs:
        # 重建diff文本
        difftext = '\n'.join(diff) + '\n'
        # 解析header
        h = parse_header(diff)
        # 解析变更内容
        d = parse_diff(diff)
        # 如果解析出了header或变更内容，就生成diff对象
        if h or d:
            yield diffobj(header=h, changes=d, text=difftext)


def parse_header(text: str | list[str]) -> header | None:
    """
    解析patch的header信息
    
    Args:
        text: patch文本
        
    Returns:
        header | None: 解析出的header信息，如果没有找到则返回None
    """
    # 首先尝试解析版本控制系统特定的header
    h = parse_scm_header(text)
    if h is None:
        # 如果没有找到，则尝试解析通用的diff header
        h = parse_diff_header(text)
    return h


def parse_scm_header(text: str | list[str]) -> header | None:
    """
    解析版本控制系统特定的header
    
    Args:
        text: patch文本
        
    Returns:
        header | None: 解析出的header信息
    """
    lines = text.splitlines() if isinstance(text, str) else text

    # 定义不同版本控制系统的解析器
    check = [
        (git_header_index, parse_git_header),
        (old_cvs_diffcmd_header, parse_cvs_header),
        (cvs_header_rcs, parse_cvs_header),
        (svn_header_index, parse_svn_header),
    ]

    # 尝试不同的解析器
    for regex, parser in check:
        diffs = findall_regex(lines, regex)
        if len(diffs) > 0:
            # 检查是否有git命令行
            git_opt = findall_regex(lines, git_diffcmd_header)
            if len(git_opt) > 0:
                # 如果有git命令行，需要特殊处理路径前缀
                res = parser(lines)
                if res:
                    old_path = res.old_path
                    new_path = res.new_path
                    # 移除git的a/和b/前缀
                    if old_path.startswith('a/'):
                        old_path = old_path[2:]

                    if new_path.startswith('b/'):
                        new_path = new_path[2:]

                    return header(
                        index_path=res.index_path,
                        old_path=old_path,
                        old_version=res.old_version,
                        new_path=new_path,
                        new_version=res.new_version,
                    )
            else:
                res = parser(lines)

            return res

    return None


def parse_diff_header(text: str | list[str]) -> header | None:
    """
    解析通用的diff header
    
    Args:
        text: patch文本
        
    Returns:
        header | None: 解析出的header信息
    """
    lines = text.splitlines() if isinstance(text, str) else text

    # 定义不同格式的header解析器
    check = [
        (unified_header_new_line, parse_unified_header),
        (context_header_old_line, parse_context_header),
        (diffcmd_header, parse_diffcmd_header),
        # TODO:
        # git_header可以处理无版本的unified header，但如果存在a/和b/前缀会被删除
        (git_header_new_line, parse_git_header),
    ]

    # 尝试不同的解析器
    for regex, parser in check:
        diffs = findall_regex(lines, regex)
        if len(diffs) > 0:
            return parser(lines)

    return None  # 没有找到header


def parse_diff(text: str | list[str]) -> list[Change] | None:
    """
    解析diff的变更内容
    
    Args:
        text: patch文本
        
    Returns:
        list[Change] | None: 解析出的变更列表，如果没有找到则返回None
    """
    if isinstance(text, str):
        lines = text.splitlines()
    else:
        lines = text

    # 定义不同格式的diff解析器
    check = [
        (unified_hunk_start, parse_unified_diff),
        (context_hunk_start, parse_context_diff),
        (default_hunk_start, parse_default_diff),
        (ed_hunk_start, parse_ed_diff),
        (rcs_ed_hunk_start, parse_rcs_ed_diff),
        (git_binary_patch_start, parse_git_binary_diff),
    ]

    # 尝试不同的解析器
    for hunk, parser in check:
        diffs = findall_regex(lines, hunk)
        if len(diffs) > 0:
            return parser(lines)
    return None


# ============================================================================
# 具体格式的header解析函数
# ============================================================================

def parse_git_header(text: str | list[str]) -> header | None:
    """
    解析git格式的header
    
    Args:
        text: patch文本
        
    Returns:
        header | None: 解析出的git header信息
    """
    lines = text.splitlines() if isinstance(text, str) else text

    # 初始化变量
    old_version = None
    new_version = None
    old_path = None
    new_path = None
    cmd_old_path = None
    cmd_new_path = None
    
    # 逐行解析git header
    for line in lines:
        # 解析git diff命令行
        hm = git_diffcmd_header.match(line)
        if hm:
            cmd_old_path = hm.group(1)
            cmd_new_path = hm.group(2)
            continue

        # 解析git index行（包含对象hash）
        g = git_header_index.match(line)
        if g:
            old_version = g.group(1)
            new_version = g.group(2)
            continue

        # git总是有自己的特殊header
        # 解析原始文件路径
        o = git_header_old_line.match(line)
        if o:
            old_path = o.group(1)

        # 解析新文件路径
        n = git_header_new_line.match(line)
        if n:
            new_path = n.group(1)

        # 解析二进制文件差异行
        binary = git_header_binary_file.match(line)
        if binary:
            old_path = binary.group(1)
            new_path = binary.group(2)

        # 如果已经找到了old_path和new_path，可以构建header
        if old_path and new_path:
            # 移除git的路径前缀
            if old_path.startswith('a/'):
                old_path = old_path[2:]

            if new_path.startswith('b/'):
                new_path = new_path[2:]
            return header(
                index_path=None,
                old_path=old_path,
                old_version=old_version,
                new_path=new_path,
                new_version=new_version,
            )

    # 如果遍历完所有文本都没找到正常信息，使用命令行信息（如果可用）
    if cmd_old_path and cmd_new_path and old_version and new_version:
        # 移除git的路径前缀
        if cmd_old_path.startswith('a/'):
            cmd_old_path = cmd_old_path[2:]

        if cmd_new_path.startswith('b/'):
            cmd_new_path = cmd_new_path[2:]

        return header(
            index_path=None,
            # 哇，我有点讨厌这个做法：
            # 如果版本被置零，则假设是/dev/null
            old_path='/dev/null' if old_version == '0000000' else cmd_old_path,
            old_version=old_version,
            new_path='/dev/null' if new_version == '0000000' else cmd_new_path,
            new_version=new_version,
        )

    return None


def parse_svn_header(text: str | list[str]) -> header | None:
    """
    解析SVN格式的header
    
    Args:
        text: patch文本
        
    Returns:
        header | None: 解析出的SVN header信息
    """
    lines = text.splitlines() if isinstance(text, str) else text

    # 查找SVN的Index行
    headers = findall_regex(lines, svn_header_index)
    if len(headers) == 0:
        return None

    # 处理SVN header
    while len(lines) > 0:
        i = svn_header_index.match(lines[0])
        del lines[0]
        if not i:
            continue

        # 尝试解析剩余的diff header
        diff_header = parse_diff_header(lines)
        if not diff_header:
            # 如果没有找到diff header，使用Index路径作为默认值
            return header(
                index_path=i.group(1),
                old_path=i.group(1),
                old_version=None,
                new_path=i.group(1),
                new_version=None,
            )

        # 处理原始文件的路径和版本
        opath = diff_header.old_path
        over = diff_header.old_version
        if over:
            # 从版本字符串中提取SVN修订号
            oend = svn_header_timestamp_version.match(over)
            if oend and oend.group(1):
                over = int(oend.group(1))
        elif opath:
            # 从路径字符串中提取时间戳和版本信息
            ts = svn_header_timestamp.match(opath)
            if ts:
                opath = opath[: -len(ts.group(1))]
                oend = svn_header_timestamp_version.match(ts.group(1))
                if oend and oend.group(1):
                    over = int(oend.group(1))

        # 处理新文件的路径和版本
        npath = diff_header.new_path
        nver = diff_header.new_version
        if nver:
            # 从版本字符串中提取SVN修订号
            nend = svn_header_timestamp_version.match(diff_header.new_version)
            if nend and nend.group(1):
                nver = int(nend.group(1))
        elif npath:
            # 从路径字符串中提取时间戳和版本信息
            ts = svn_header_timestamp.match(npath)
            if ts:
                npath = npath[: -len(ts.group(1))]
                nend = svn_header_timestamp_version.match(ts.group(1))
                if nend and nend.group(1):
                    nver = int(nend.group(1))

        # 确保版本号是整数或None
        if not isinstance(over, int):
            over = None

        if not isinstance(nver, int):
            nver = None

        return header(
            index_path=i.group(1),
            old_path=opath,
            old_version=over,
            new_path=npath,
            new_version=nver,
        )

    return None


def parse_cvs_header(text: str | list[str]) -> header | None:
    """
    解析CVS格式的header
    
    Args:
        text: patch文本
        
    Returns:
        header | None: 解析出的CVS header信息
    """
    lines = text.splitlines() if isinstance(text, str) else text

    # 查找CVS的不同header格式
    headers = findall_regex(lines, cvs_header_rcs)
    headers_old = findall_regex(lines, old_cvs_diffcmd_header)

    if headers:
        # 解析RCS样式的header
        while len(lines) > 0:
            i = cvs_header_index.match(lines[0])
            del lines[0]
            if not i:
                continue

            # 尝试解析diff header
            diff_header = parse_diff_header(lines)
            if diff_header:
                # 处理原始文件版本
                over = diff_header.old_version
                if over:
                    oend = cvs_header_timestamp.match(over)
                    oend_c = cvs_header_timestamp_colon.match(over)
                    if oend:
                        over = oend.group(2)
                    elif oend_c:
                        over = oend_c.group(1)

                # 处理新文件版本
                nver = diff_header.new_version
                if nver:
                    nend = cvs_header_timestamp.match(nver)
                    nend_c = cvs_header_timestamp_colon.match(nver)
                    if nend:
                        nver = nend.group(2)
                    elif nend_c:
                        nver = nend_c.group(1)

                return header(
                    index_path=i.group(1),
                    old_path=diff_header.old_path,
                    old_version=over,
                    new_path=diff_header.new_path,
                    new_version=nver,
                )
            return header(
                index_path=i.group(1),
                old_path=i.group(1),
                old_version=None,
                new_path=i.group(1),
                new_version=None,
            )
    elif headers_old:
        # 解析旧式header
        while len(lines) > 0:
            i = cvs_header_index.match(lines[0])
            del lines[0]
            if not i:
                continue

            d = old_cvs_diffcmd_header.match(lines[0])
            if not d:
                return header(
                    index_path=i.group(1),
                    old_path=i.group(1),
                    old_version=None,
                    new_path=i.group(1),
                    new_version=None,
                )

            # 这会为我们清除无用的内容
            parse_diff_header(lines)
            over = d.group(2) if d.group(2) else None
            nver = d.group(4) if d.group(4) else None
            return header(
                index_path=i.group(1),
                old_path=d.group(1),
                old_version=over,
                new_path=d.group(3),
                new_version=nver,
            )

    return None


def parse_diffcmd_header(text: str | list[str]) -> header | None:
    """
    解析diff命令的header
    
    Args:
        text: patch文本
        
    Returns:
        header | None: 解析出的diff命令header信息
    """
    lines = text.splitlines() if isinstance(text, str) else text

    # 查找diff命令行
    headers = findall_regex(lines, diffcmd_header)
    if len(headers) == 0:
        return None

    # 解析diff命令行
    while len(lines) > 0:
        d = diffcmd_header.match(lines[0])
        del lines[0]
        if d:
            return header(
                index_path=None,
                old_path=d.group(1),
                old_version=None,
                new_path=d.group(2),
                new_version=None,
            )
    return None


def parse_unified_header(text: str | list[str]) -> header | None:
    """
    解析unified diff格式的header
    
    Args:
        text: patch文本
        
    Returns:
        header | None: 解析出的unified header信息
    """
    lines = text.splitlines() if isinstance(text, str) else text

    # 查找unified diff的新文件行
    headers = findall_regex(lines, unified_header_new_line)
    if len(headers) == 0:
        return None

    # 解析unified header（需要原始文件行和新文件行成对出现）
    while len(lines) > 1:
        o = unified_header_old_line.match(lines[0])
        del lines[0]
        if o:
            n = unified_header_new_line.match(lines[0])
            del lines[0]
            if n:
                # 处理版本信息（可能为空）
                over = o.group(2)
                if len(over) == 0:
                    over = None

                nver = n.group(2)
                if len(nver) == 0:
                    nver = None

                return header(
                    index_path=None,
                    old_path=o.group(1),
                    old_version=over,
                    new_path=n.group(1),
                    new_version=nver,
                )

    return None


def parse_context_header(text: str | list[str]) -> header | None:
    """
    解析context diff格式的header
    
    Args:
        text: patch文本
        
    Returns:
        header | None: 解析出的context header信息
    """
    lines = text.splitlines() if isinstance(text, str) else text

    # 查找context diff的原始文件行
    headers = findall_regex(lines, context_header_old_line)
    if len(headers) == 0:
        return None

    # 解析context header（需要原始文件行和新文件行成对出现）
    while len(lines) > 1:
        o = context_header_old_line.match(lines[0])
        del lines[0]
        if o:
            n = context_header_new_line.match(lines[0])
            del lines[0]
            if n:
                # 处理版本信息（可能为空）
                over = o.group(2)
                if len(over) == 0:
                    over = None

                nver = n.group(2)
                if len(nver) == 0:
                    nver = None

                return header(
                    index_path=None,
                    old_path=o.group(1),
                    old_version=over,
                    new_path=n.group(1),
                    new_version=nver,
                )

    return None


# ============================================================================
# 具体格式的diff解析函数
# ============================================================================

def parse_default_diff(text: str | list[str]) -> list[Change] | None:
    """
    解析默认格式的diff（传统diff格式）
    
    Args:
        text: patch文本
        
    Returns:
        list[Change] | None: 解析出的变更列表
    """
    lines = text.splitlines() if isinstance(text, str) else text

    # 初始化变量
    old = 0      # 原始文件当前行号
    new = 0      # 新文件当前行号
    old_len = 0  # 原始文件hunk长度
    new_len = 0  # 新文件hunk长度
    r = 0        # 删除计数器
    i = 0        # 插入计数器

    changes = list()

    # 按hunk分割文本
    hunks = split_by_regex(lines, default_hunk_start)
    for hunk_n, hunk in enumerate(hunks):
        if not len(hunk):
            continue

        # 重置计数器
        r = 0
        i = 0
        # 处理hunk中的每一行
        while len(hunk) > 0:
            h = default_hunk_start.match(hunk[0])
            c = default_change.match(hunk[0])
            del hunk[0]
            if h:
                # 解析hunk头部信息
                old = int(h.group(1))
                if len(h.group(2)) > 0:
                    old_len = int(h.group(2)) - old + 1
                else:
                    old_len = 0

                new = int(h.group(4))
                if len(h.group(5)) > 0:
                    new_len = int(h.group(5)) - new + 1
                else:
                    new_len = 0

            elif c:
                # 处理变更行
                kind = c.group(1)  # 变更类型：< 或 >
                line = c.group(2)  # 行内容

                if kind == '<' and (r != old_len or r == 0):
                    # 删除的行（在原始文件中存在，新文件中不存在）
                    changes.append(Change(old + r, None, line, hunk_n))
                    r += 1
                elif kind == '>' and (i != new_len or i == 0):
                    # 新增的行（在新文件中存在，原始文件中不存在）
                    changes.append(Change(None, new + i, line, hunk_n))
                    i += 1

    if len(changes) > 0:
        return changes

    return None


def parse_unified_diff(text: str | list[str]) -> list[Change] | None:
    """
    解析unified diff格式的变更内容
    
    Args:
        text: patch文本
        
    Returns:
        list[Change] | None: 解析出的变更列表
    """
    lines = text.splitlines() if isinstance(text, str) else text

    # 初始化变量
    old = 0      # 原始文件起始行号
    new = 0      # 新文件起始行号
    r = 0        # 原始文件当前偏移
    i = 0        # 新文件当前偏移
    old_len = 0  # 原始文件hunk长度
    new_len = 0  # 新文件hunk长度

    changes = list()

    # 按hunk分割文本
    hunks = split_by_regex(lines, unified_hunk_start)
    for hunk_n, hunk in enumerate(hunks):
        # 重置计数器
        r = 0
        i = 0
        # 处理hunk头部
        while len(hunk) > 0:
            h = unified_hunk_start.match(hunk[0])
            del hunk[0]
            if h:
                # hunk头部 @@ -1,6 +1,6 @@ 的含义：
                # - 从原始文件第1行开始，显示6行
                # - 从新文件第1行开始，显示6行
                old = int(h.group(1))  # 原始文件起始行号
                old_len = (
                    int(h.group(2)) if len(h.group(2)) > 0 else 1
                )  # 原始文件行数

                new = int(h.group(3))  # 新文件起始行号
                new_len = (
                    int(h.group(4)) if len(h.group(4)) > 0 else 1
                )  # 新文件行数

                h = None
                break

        # 处理hunk中的每一行
        for n in hunk:
            # unified diff中每行的第一个字符表示变更类型：
            # 空格（上下文）、+（新增）、-（删除）
            # 第一个字符是类型，其余是行内容
            kind = (
                n[0] if len(n) > 0 else ' '
            )  # 空行在hunk中被视为上下文行
            line = n[1:] if len(n) > 1 else ''

            # 根据类型处理行
            if kind == '-' and (r != old_len or r == 0):
                # 从原始文件中删除的行
                changes.append(Change(old + r, None, line, hunk_n))
                r += 1
            elif kind == '+' and (i != new_len or i == 0):
                # 在新文件中新增的行
                changes.append(Change(None, new + i, line, hunk_n))
                i += 1
            elif kind == ' ':
                # 上下文行 - 在原始文件和新文件中都存在
                changes.append(Change(old + r, new + i, line, hunk_n))
                r += 1
                i += 1

    if len(changes) > 0:
        return changes

    return None


def parse_context_diff(text: str | list[str]) -> list[Change] | None:
    """
    解析context diff格式的变更内容
    
    Args:
        text: patch文本
        
    Returns:
        list[Change] | None: 解析出的变更列表
        
    Raises:
        ParseException: 当context diff格式无效时
    """
    lines = text.splitlines() if isinstance(text, str) else text

    # 初始化变量
    old = 0  # 原始文件起始行号
    new = 0  # 新文件起始行号
    j = 0    # 原始文件计数器
    k = 0    # 新文件计数器

    changes = list()

    # 按hunk分割文本
    hunks = split_by_regex(lines, context_hunk_start)
    for hunk_n, hunk in enumerate(hunks):
        if not len(hunk):
            continue

        # 重置计数器
        j = 0
        k = 0
        # 将hunk分为原始部分和新部分
        parts = split_by_regex(hunk, context_hunk_new)
        if len(parts) != 2:
            raise exceptions.ParseException('Context diff invalid', hunk_n)

        old_hunk = parts[0]  # 原始文件部分
        new_hunk = parts[1]  # 新文件部分

        # 解析原始文件hunk头部
        while len(old_hunk) > 0:
            o = context_hunk_old.match(old_hunk[0])
            del old_hunk[0]

            if not o:
                continue

            old = int(o.group(1))
            old_len = int(o.group(2)) + 1 - old
            # 解析新文件hunk头部
            while len(new_hunk) > 0:
                n = context_hunk_new.match(new_hunk[0])
                del new_hunk[0]

                if not n:
                    continue

                new = int(n.group(1))
                new_len = int(n.group(2)) + 1 - new
                break
            break

        # 现在old和new已设置，可以开始处理变更
        if len(old_hunk) > 0 and len(new_hunk) == 0:
            msg = 'Got unexpected change in removal hunk: '
            # 只有删除的情况
            while len(old_hunk) > 0:
                c = context_change.match(old_hunk[0])
                del old_hunk[0]

                if not c:
                    continue

                kind = c.group(1)  # 变更类型
                line = c.group(2)  # 行内容

                if kind == '-' and (j != old_len or j == 0):
                    # 删除的行
                    changes.append(Change(old + j, None, line, hunk_n))
                    j += 1
                elif kind == ' ' and (
                    (j != old_len and k != new_len) or (j == 0 or k == 0)
                ):
                    # 上下文行
                    changes.append(Change(old + j, new + k, line, hunk_n))
                    j += 1
                    k += 1
                elif kind == '+' or kind == '!':
                    # 在删除hunk中出现新增或修改，这是错误的
                    raise exceptions.ParseException(msg + kind, hunk_n)

            continue

        if len(old_hunk) == 0 and len(new_hunk) > 0:
            msg = 'Got unexpected change in removal hunk: '
            # 只有插入的情况
            while len(new_hunk) > 0:
                c = context_change.match(new_hunk[0])
                del new_hunk[0]

                if not c:
                    continue

                kind = c.group(1)  # 变更类型
                line = c.group(2)  # 行内容

                if kind == '+' and (k != new_len or k == 0):
                    # 新增的行
                    changes.append(Change(None, new + k, line, hunk_n))
                    k += 1
                elif kind == ' ' and (
                    (j != old_len and k != new_len) or (j == 0 or k == 0)
                ):
                    # 上下文行
                    changes.append(Change(old + j, new + k, line, hunk_n))
                    j += 1
                    k += 1
                elif kind == '-' or kind == '!':
                    # 在插入hunk中出现删除或修改，这是错误的
                    raise exceptions.ParseException(msg + kind, hunk_n)
            continue

        # 同时有原始和新部分的情况
        while len(old_hunk) > 0 and len(new_hunk) > 0:
            oc = context_change.match(old_hunk[0])
            nc = context_change.match(new_hunk[0])
            okind = None
            nkind = None

            if oc:
                okind = oc.group(1)
                oline = oc.group(2)

            if nc:
                nkind = nc.group(1)
                nline = nc.group(2)

            # 处理不同的情况组合
            if not (oc or nc):
                # 两边都没有匹配，跳过这些行
                del old_hunk[0]
                del new_hunk[0]
            elif okind == ' ' and nkind == ' ' and oline == nline:
                # 上下文行，两边相同
                changes.append(Change(old + j, new + k, oline, hunk_n))
                j += 1
                k += 1
                del old_hunk[0]
                del new_hunk[0]
            elif okind == '-' or okind == '!' and (j != old_len or j == 0):
                # 删除或修改的行（原始文件侧）
                changes.append(Change(old + j, None, oline, hunk_n))
                j += 1
                del old_hunk[0]
            elif nkind == '+' or nkind == '!' and (k != new_len or k == 0):
                # 新增或修改的行（新文件侧）
                changes.append(Change(None, new + k, nline, hunk_n))
                k += 1
                del new_hunk[0]
            else:
                # 无法处理的情况
                return None

    if len(changes) > 0:
        return changes

    return None


def parse_ed_diff(text: str | list[str]) -> list[Change] | None:
    """
    解析ed格式的diff
    
    Args:
        text: patch文本
        
    Returns:
        list[Change] | None: 解析出的变更列表
    """
    lines = text.splitlines() if isinstance(text, str) else text

    # 初始化变量
    old = 0  # 原始文件行号
    j = 0    # 计数器j
    k = 0    # 计数器k
    r = 0    # 删除计数器
    i = 0    # 插入计数器

    changes = list()

    # 按hunk分割文本并反转顺序（ed格式需要从后往前处理）
    hunks = split_by_regex(lines, ed_hunk_start)
    hunks.reverse()
    for hunk_n, hunk in enumerate(hunks):
        if not len(hunk):
            continue
        # 重置计数器
        j = 0
        k = 0
        # 处理hunk
        while len(hunk) > 0:
            o = ed_hunk_start.match(hunk[0])
            del hunk[0]

            if not o:
                continue

            # 解析ed命令
            old = int(o.group(1))
            old_end = int(o.group(2)) if len(o.group(2)) else old

            hunk_kind = o.group(3)  # 操作类型：a(add)、c(change)、d(delete)
            if hunk_kind == 'd':
                # 删除操作
                k = 0
                while old_end >= old:
                    changes.append(Change(old + k, None, None, hunk_n))
                    r += 1
                    k += 1
                    old_end -= 1
                continue

            # 处理hunk内容
            while len(hunk) > 0:
                e = ed_hunk_end.match(hunk[0])
                if not e and hunk_kind == 'c':
                    # 修改操作：先删除旧行
                    k = 0
                    while old_end >= old:
                        changes.append(Change(old + k, None, None, hunk_n))
                        r += 1
                        k += 1
                        old_end -= 1

                    # 基本不知道为什么这样能工作，但测试通过了
                    changes.append(
                        Change(
                            None,
                            old - r + i + k + j,
                            hunk[0],
                            hunk_n,
                        )
                    )
                    i += 1
                    j += 1
                if not e and hunk_kind == 'a':
                    # 新增操作
                    changes.append(
                        Change(
                            None,
                            old - r + i + 1,
                            hunk[0],
                            hunk_n,
                        )
                    )
                    i += 1

                del hunk[0]

    if len(changes) > 0:
        return changes

    return None


def parse_rcs_ed_diff(text: str | list[str]) -> list[Change] | None:
    """
    解析RCS ed格式的diff（类似ed格式但没有'c'类型）
    
    Args:
        text: patch文本
        
    Returns:
        list[Change] | None: 解析出的变更列表
    """
    lines = text.splitlines() if isinstance(text, str) else text

    # 初始化变量
    old = 0                # 原始文件行号
    j = 0                  # 计数器
    size = 0               # 操作大小
    total_change_size = 0  # 总变更大小

    changes = list()

    # 按hunk分割文本
    hunks = split_by_regex(lines, rcs_ed_hunk_start)
    for hunk_n, hunk in enumerate(hunks):
        if len(hunk):
            j = 0
            # 处理hunk
            while len(hunk) > 0:
                o = rcs_ed_hunk_start.match(hunk[0])
                del hunk[0]

                if not o:
                    continue

                # 解析RCS ed命令
                hunk_kind = o.group(1)  # 操作类型：a(add)、d(delete)
                old = int(o.group(2))   # 行号
                size = int(o.group(3)) if o.group(3) else 0  # 操作大小

                if hunk_kind == 'a':
                    # 新增操作
                    old += total_change_size + 1
                    total_change_size += size
                    while size > 0 and len(hunk) > 0:
                        changes.append(Change(None, old + j, hunk[0], hunk_n))
                        j += 1
                        size -= 1

                        del hunk[0]

                elif hunk_kind == 'd':
                    # 删除操作
                    total_change_size -= size
                    while size > 0:
                        changes.append(Change(old + j, None, None, hunk_n))
                        j += 1
                        size -= 1

    if len(changes) > 0:
        return changes
    return None


def parse_git_binary_diff(text: str | list[str]) -> list[Change] | None:
    """
    解析git二进制diff格式
    
    Args:
        text: patch文本
        
    Returns:
        list[Change] | None: 解析出的变更列表（用于二进制数据）
    """
    lines = text.splitlines() if isinstance(text, str) else text

    changes: list[Change] = list()

    # 初始化变量
    old_version = None
    new_version = None
    cmd_old_path = None
    cmd_new_path = None
    # 这些大小用作状态锁存
    new_size = 0
    old_size = 0
    old_encoded = ''
    new_encoded = ''
    
    # 逐行解析git二进制patch
    for line in lines:
        # 解析git命令行（如果还没找到路径）
        if cmd_old_path is None and cmd_new_path is None:
            hm = git_diffcmd_header.match(line)
            if hm:
                cmd_old_path = hm.group(1)
                cmd_new_path = hm.group(2)
                continue

        # 解析git index行（如果还没找到版本）
        if old_version is None and new_version is None:
            g = git_header_index.match(line)
            if g:
                old_version = g.group(1)
                new_version = g.group(2)
                continue

        # 处理新增文件的二进制数据
        if new_size == 0:
            literal = git_binary_literal_start.match(line)
            if literal:
                new_size = int(literal.group(1))
                continue
            delta = git_binary_delta_start.match(line)
            if delta:
                # delta格式暂不支持
                new_size = 0
                continue
        elif new_size > 0:
            if base85string.match(line):
                # base85编码的行，格式验证
                assert len(line) >= 6 and ((len(line) - 1) % 5) == 0
                new_encoded += line[1:]  # 跳过第一个字符（长度标识）
            elif 0 == len(line):
                # 空行表示块结束
                if new_encoded:
                    # 解码base85并解压缩
                    decoded = base64.b85decode(new_encoded)
                    added_data = zlib.decompress(decoded)
                    assert new_size == len(added_data)
                    change = Change(None, 0, added_data, None)
                    changes.append(change)
                # 重置状态
                new_size = 0
                new_encoded = ''
            else:
                # 无效行格式，重置状态
                new_size = 0
                new_encoded = ''

        # 处理删除文件的二进制数据
        if old_size == 0:
            literal = git_binary_literal_start.match(line)
            if literal:
                old_size = int(literal.group(1))
            delta = git_binary_delta_start.match(line)
            if delta:
                # delta格式暂不支持
                old_size = 0
                continue
        elif old_size > 0:
            if base85string.match(line):
                # base85编码的行，格式验证
                assert len(line) >= 6 and ((len(line) - 1) % 5) == 0
                old_encoded += line[1:]  # 跳过第一个字符（长度标识）
            elif 0 == len(line):
                # 空行表示块结束
                if old_encoded:
                    # 解码base85并解压缩
                    decoded = base64.b85decode(old_encoded)
                    removed_data = zlib.decompress(decoded)
                    assert old_size == len(removed_data)
                    change = Change(0, None, None, removed_data)
                    changes.append(change)
                # 重置状态
                old_size = 0
                old_encoded = ''
            else:
                # 无效行格式，重置状态
                old_size = 0
                old_encoded = ''

    return changes