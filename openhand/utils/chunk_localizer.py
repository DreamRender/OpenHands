"""文件块定位器模块。

此模块用于帮助定位文件中最相关的代码块。
主要用于根据给定查询（例如Agent生成的编辑草案）
定位文件中最相关的代码块。
"""

from pydantic import BaseModel
from rapidfuzz.distance import LCSseq
from tree_sitter_language_pack import get_parser

from openhands.core.logger import openhands_logger as logger


class Chunk(BaseModel):
    """代码块数据模型。
    
    表示文件中的一个连续代码块，包含文本内容、行范围和相似度分数。
    """
    
    text: str
    """代码块的文本内容。"""
    
    line_range: tuple[int, int]
    """代码块的行范围。
    
    格式为(start_line, end_line)，使用1-index，区间包含两端。
    例如(5, 10)表示从第5行到第10行（包含第5行和第10行）。
    """
    
    normalized_lcs: float | None = None
    """标准化的最长公共子序列分数。
    
    用于表示此代码块与查询文本的相似度，范围为0.0到1.0。
    值越高表示与查询越相似。
    """

    def visualize(self) -> str:
        """可视化代码块内容。

        返回带有行号的代码块内容，便于调试和查看。

        Returns:
            str: 格式化的代码块内容，每行前面都有行号

        Example:
            5|def hello():
            6|    print("Hello, World!")
            7|    return True
        """
        lines = self.text.split('\n')
        # 验证行数与行范围一致
        assert len(lines) == self.line_range[1] - self.line_range[0] + 1
        
        ret = ''
        for i, line in enumerate(lines):
            # 计算实际行号并格式化输出
            actual_line_num = self.line_range[0] + i
            ret += f'{actual_line_num}|{line}\n'
        return ret


def _create_chunks_from_raw_string(content: str, size: int):
    """从原始字符串创建代码块列表。

    这是一个备用方案，当无法使用tree-sitter解析时使用。
    简单地按行数分割文本内容。

    Args:
        content (str): 要分块的文本内容
        size (int): 每个块的最大行数

    Returns:
        list[Chunk]: 创建的代码块列表

    Note:
        此方法不考虑代码的语法结构，只是机械地按行数分割。
    """
    lines = content.split('\n')
    ret = []
    
    # 按指定大小分割行
    for i in range(0, len(lines), size):
        _cur_lines = lines[i : i + size]
        ret.append(
            Chunk(
                text='\n'.join(_cur_lines),
                line_range=(i + 1, i + len(_cur_lines)),  # 转换为1-index
            )
        )
    return ret


def create_chunks(
    text: str, size: int = 100, language: str | None = None
) -> list[Chunk]:
    """创建文本的代码块列表。

    根据指定的语言尝试使用tree-sitter进行智能分块，
    如果失败则回退到基于行数的简单分块。

    Args:
        text (str): 要分块的文本内容
        size (int, optional): 每个块的最大行数。默认为100
        language (str | None, optional): 编程语言类型，用于tree-sitter解析

    Returns:
        list[Chunk]: 创建的代码块列表

    Note:
        目前tree-sitter分块功能尚未实现，总是回退到简单的行分割方式。
    """
    try:
        # 尝试获取指定语言的tree-sitter解析器
        parser = get_parser(language) if language is not None else None
    except AttributeError:
        # 如果语言不支持，记录调试信息并回退到原始字符串方式
        logger.debug(f'Language {language} not supported. Falling back to raw string.')
        parser = None

    if parser is None:
        # 回退到原始字符串分块方式
        return _create_chunks_from_raw_string(text, size)

    # TODO: 实现tree-sitter分块
    # return _create_chunks_from_tree_sitter(parser.parse(bytes(text, 'utf-8')), max_chunk_lines=size)
    raise NotImplementedError('Tree-sitter chunking not implemented yet.')


def normalized_lcs(chunk: str, query: str) -> float:
    """计算标准化的最长公共子序列（LCS）来比较文件块与查询的相似度。

    我们通过代码块的长度来标准化最长公共子序列（LCS），
    以检查代码块中有**多少**内容被查询覆盖。

    Args:
        chunk (str): 要比较的代码块文本
        query (str): 查询文本（例如编辑草案）

    Returns:
        float: 标准化的相似度分数，范围为0.0到1.0
               0.0表示完全不相似，1.0表示完全匹配

    Note:
        标准化是通过将LCS分数除以代码块长度来实现的，
        这样可以衡量代码块中有多大比例与查询相关。
    """
    if len(chunk) == 0:
        return 0.0

    # 计算LCS相似度分数
    _score = LCSseq.similarity(chunk, query)

    # 通过代码块长度进行标准化
    return _score / len(chunk)


def get_top_k_chunk_matches(
    text: str, query: str, k: int = 3, max_chunk_size: int = 100
) -> list[Chunk]:
    """获取文本中与查询最匹配的前k个代码块。

    查询可能是一个代码编辑草案的字符串。

    Args:
        text (str): 要搜索的文本内容
        query (str): 要在文本中搜索的查询内容
        k (int, optional): 要返回的顶部代码块数量。默认为3
        max_chunk_size (int, optional): 代码块的最大行数。默认为100

    Returns:
        list[Chunk]: 按相似度降序排列的前k个代码块

    Note:
        - 首先将文本分解为代码块
        - 计算每个代码块与查询的相似度
        - 按相似度降序排序并返回前k个结果
        - 返回的代码块包含相似度分数，便于进一步分析
    """
    # 创建原始代码块
    raw_chunks = create_chunks(text, max_chunk_size)
    
    # 为每个代码块计算LCS相似度分数
    chunks_with_lcs: list[Chunk] = [
        Chunk(
            text=chunk.text,
            line_range=chunk.line_range,
            normalized_lcs=normalized_lcs(chunk.text, query),
        )
        for chunk in raw_chunks
    ]
    
    # 按相似度分数降序排序
    sorted_chunks = sorted(
        chunks_with_lcs,
        key=lambda x: x.normalized_lcs,  # type: ignore
        reverse=True,  # 降序排列，相似度高的在前
    )
    
    # 返回前k个最相似的代码块
    return sorted_chunks[:k]
