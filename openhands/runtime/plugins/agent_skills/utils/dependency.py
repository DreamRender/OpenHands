"""模块依赖导入工具

该模块提供了从指定模块中动态导入函数并添加到目标全局命名空间的功能。
主要用于动态加载和注册模块中的特定函数到当前环境中。
"""

from types import ModuleType


def import_functions(
    module: ModuleType, function_names: list[str], target_globals: dict[str, object]
) -> None:
    """从指定模块中导入函数列表到目标全局命名空间
    
    该函数会遍历提供的函数名列表，从源模块中获取对应的函数对象，
    并将它们添加到目标全局字典中。如果某个函数不存在，会抛出ValueError异常。
    
    Args:
        module: 源模块对象，包含待导入函数的模块
        function_names: 需要导入的函数名称列表
        target_globals: 目标全局命名空间字典，导入的函数将被添加到此字典中
        
    Raises:
        ValueError: 当指定的函数在源模块中不存在时抛出
        
    Returns:
        None
    """
    # 遍历所有需要导入的函数名
    for name in function_names:
        # 检查模块中是否存在该函数
        if hasattr(module, name):
            # 获取函数对象并添加到目标全局命名空间
            target_globals[name] = getattr(module, name)
        else:
            # 如果函数不存在，抛出异常并提供详细错误信息
            raise ValueError(f'Function {name} not found in {module.__name__}')
