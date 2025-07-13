"""
utils.py - 序列化工具模块

该模块包含序列化过程中使用的工具函数，主要用于数据结构的清理和字段移除操作。
"""


def remove_fields(obj: dict | list | tuple, fields: set[str]) -> None:
    """
    从对象中递归移除指定的字段。
    
    该函数用于清理数据结构，递归地从字典、列表或元组中移除指定的字段名。
    主要用于在序列化过程中清除不需要的字段，以减少数据大小或保护敏感信息。
    
    Args:
        obj (dict | list | tuple): 要处理的对象，可以是字典、列表或元组
        fields (set[str]): 要移除的字段名集合
        
    Raises:
        ValueError: 当对象包含dataclass时抛出，建议先转换为字典
        
    Note:
        - 该函数会就地修改输入对象，不返回新对象
        - 对于字典：直接删除指定的键
        - 对于列表/元组：递归处理每个元素
        - 不支持dataclass对象，需要先转换为字典
        
    Examples:
        >>> data = {'name': 'test', 'password': 'secret', 'info': {'id': 1, 'token': 'abc'}}
        >>> remove_fields(data, {'password', 'token'})
        >>> print(data)  # {'name': 'test', 'info': {'id': 1}}
    """
    if isinstance(obj, dict):
        # 处理字典类型：移除指定字段并递归处理值
        for field in fields:
            if field in obj:
                del obj[field]  # 删除指定的字段
        
        # 递归处理字典中的每个值
        for _, value in obj.items():
            remove_fields(value, fields)
            
    elif isinstance(obj, (list, tuple)):
        # 处理列表和元组类型：递归处理每个元素
        for item in obj:
            remove_fields(item, fields)
    
    # 检查对象是否为dataclass，如果是则抛出错误
    # dataclass对象具有__dataclass_fields__属性
    if hasattr(obj, '__dataclass_fields__'):
        raise ValueError(
            'Object must not contain dataclass, consider converting to dict first'
        )