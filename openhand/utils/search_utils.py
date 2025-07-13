import base64
from typing import AsyncIterator, Callable


def offset_to_page_id(offset: int, has_next: bool) -> str | None:
    """将偏移量转换为页面ID。

    将数值型的偏移量编码为base64字符串作为页面ID，用于分页查询。
    这种设计隐藏了内部的偏移量实现细节，提供了更安全的分页机制。

    Args:
        offset (int): 当前的偏移量（从0开始的索引位置）
        has_next (bool): 是否还有下一页数据

    Returns:
        str | None: 如果有下一页则返回编码后的页面ID字符串，
                   否则返回None表示没有更多页面

    Note:
        使用base64编码可以避免直接暴露内部的偏移量数值，
        同时确保页面ID是URL安全的字符串。
    """
    if not has_next:
        return None
    # 将偏移量转换为字符串，然后进行base64编码
    next_page_id = base64.b64encode(str(offset).encode()).decode()
    return next_page_id


def page_id_to_offset(page_id: str | None) -> int:
    """将页面ID转换为偏移量。

    将base64编码的页面ID解码回原始的偏移量数值。
    这是offset_to_page_id的逆向操作。

    Args:
        page_id (str | None): base64编码的页面ID，可能为None

    Returns:
        int: 解码后的偏移量。如果page_id为None或空，则返回0（第一页）

    Note:
        当page_id为None时返回0，这对应于查询第一页的情况。
    """
    if not page_id:
        return 0
    # 解码base64字符串并转换为整数
    offset = int(base64.b64decode(page_id).decode())
    return offset


async def iterate(fn: Callable, **kwargs) -> AsyncIterator:
    """遍历分页结果集的异步迭代器。

    这是一个通用的分页遍历工具，自动处理分页逻辑，逐个产出结果项。
    假设结果集包含一个results数组和一个next_page_id字段。

    Args:
        fn (Callable): 异步函数，用于获取分页数据。
                      该函数应该接受page_id参数并返回包含results和next_page_id的对象
        **kwargs: 传递给fn函数的其他关键字参数

    Yields:
        每个结果项：从所有页面的results数组中逐个产出每个项目

    Example:
        >>> async def fetch_data(page_id=None, search_term=""):
        ...     # 返回包含results和next_page_id的对象
        ...     pass
        >>> 
        >>> async for item in iterate(fetch_data, search_term="example"):
        ...     print(item)

    Note:
        - 函数会自动处理分页，从第一页开始（page_id=None）
        - 持续获取数据直到next_page_id为None
        - 对于每一页的results，逐个产出每个结果项
    """
    # 复制传入的参数，避免修改原始参数
    kwargs = {**kwargs}
    # 从第一页开始
    kwargs['page_id'] = None
    
    while True:
        # 获取当前页的结果集
        result_set = await fn(**kwargs)
        
        # 逐个产出当前页面的所有结果
        for result in result_set.results:
            yield result
            
        # 检查是否还有下一页
        if result_set.next_page_id is None:
            return
            
        # 更新页面ID以获取下一页
        kwargs['page_id'] = result_set.next_page_id
