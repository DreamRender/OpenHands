from types import UnionType
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

# 默认Agent类型常量
OH_DEFAULT_AGENT = 'CodeActAgent'
"""OpenHands系统使用的默认Agent类型"""

# 默认最大迭代次数常量
OH_MAX_ITERATIONS = 500
"""Agent执行任务时的默认最大迭代次数限制"""


def get_field_info(field: FieldInfo) -> dict[str, Any]:
    """提取数据类字段的信息：类型、可选性和默认值。

    这个函数分析Pydantic字段的元信息，提取字段的类型、是否可选、
    以及默认值等信息，主要用于前端UI显示字段信息。

    Args:
        field: 要提取信息的字段

    Returns:
        dict[str, Any]: 包含字段类型、是否可选和默认值的字典
    """
    field_type = field.annotation
    optional = False

    # 对于像str | None这样的类型，找到非None类型并设置optional为True
    # 这对前端了解字段是否可选很有用
    # 并在UI中显示正确的类型
    # 注意：这仅适用于以None作为类型之一的UnionTypes
    if get_origin(field_type) is UnionType:
        types = get_args(field_type)
        non_none_arg = next(
            (t for t in types if t is not None and t is not type(None)), None
        )
        if non_none_arg is not None:
            field_type = non_none_arg
            optional = True

    # 以美观格式显示的类型名称
    type_name = (
        str(field_type)
        if field_type is None
        else (
            field_type.__name__ if hasattr(field_type, '__name__') else str(field_type)
        )
    )

    # 默认值总是存在的
    default = field.default

    # 返回包含前端有用信息的模式
    return {'type': type_name.lower(), 'optional': optional, 'default': default}


def model_defaults_to_dict(model: BaseModel) -> dict[str, Any]:
    """将字段信息序列化为字典供前端使用，包括类型提示、默认值和是否可选。
    
    这个函数递归地处理BaseModel及其嵌套的BaseModel字段，
    生成一个包含所有字段信息的字典结构，便于前端展示配置选项。

    Args:
        model: 要序列化的Pydantic模型实例

    Returns:
        dict[str, Any]: 包含所有字段信息的字典，嵌套模型会递归处理
    """
    result = {}
    for name, field in model.__class__.model_fields.items():
        # 获取字段的当前值
        field_value = getattr(model, name)

        if isinstance(field_value, BaseModel):
            # 如果字段值是另一个BaseModel，递归处理
            result[name] = model_defaults_to_dict(field_value)
        else:
            # 如果是普通字段，提取字段信息
            result[name] = get_field_info(field)

    return result