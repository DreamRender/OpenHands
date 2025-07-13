import importlib
from functools import lru_cache
from typing import TypeVar

# 定义类型变量，用于泛型类型约束
T = TypeVar('T')


def import_from(qual_name: str):
    """从完全限定名称导入值。

    这是一个用于动态导入任何Python值（类、函数、变量）的工具函数。
    通过完全限定名称来导入对象，例如'openhands.server.user_auth.UserAuth'
    会从openhands.server.user_auth模块导入UserAuth类。

    Args:
        qual_name (str): 完全限定名称，格式为'module.submodule.name'
                        例如: 'openhands.server.user_auth.UserAuth'

    Returns:
        导入的值（类、函数或变量）

    Example:
        >>> UserAuth = import_from('openhands.server.user_auth.UserAuth')
        >>> auth = UserAuth()

    Note:
        此函数通过字符串解析模块路径和对象名称，
        然后使用importlib动态导入模块并获取指定的属性。
    """
    # 将完全限定名称分割为模块路径和对象名称
    parts = qual_name.split('.')
    module_name = '.'.join(parts[:-1])  # 除最后一部分外的所有部分作为模块名
    
    # 动态导入模块
    module = importlib.import_module(module_name)
    
    # 从模块中获取指定名称的属性（类、函数或变量）
    result = getattr(module, parts[-1])
    return result


@lru_cache()
def get_impl(cls: type[T], impl_name: str | None) -> type[T]:
    """导入并验证基类的命名实现。

    这是OpenHands中的一个扩展性机制，允许运行时替换实现。
    它使应用程序能够通过提供自己的OpenHands基类实现来自定义行为。

    该函数通过验证导入的类是否与指定的基类相同或是其子类来确保类型安全。

    Args:
        cls (type[T]): 定义接口的基类
        impl_name (str | None): 实现类的完全限定名称，或None表示使用基类
                               例如: 'openhands.server.conversation_manager.StandaloneConversationManager'

    Returns:
        type[T]: 实现类，保证是cls的子类

    Example:
        >>> # 获取默认实现
        >>> ConversationManager = get_impl(ConversationManager, None)
        >>> # 获取自定义实现
        >>> CustomManager = get_impl(ConversationManager, 'myapp.CustomConversationManager')

    常见用例:
        - 服务器组件（ConversationManager、UserAuth等）
        - 存储实现（ConversationStore、SettingsStore等）
        - 服务集成（GitHub、GitLab、Bitbucket服务）

    Note:
        实现会被缓存以避免重复导入相同的类。
        使用@lru_cache()装饰器确保相同参数的调用返回缓存的结果。
    """
    if impl_name is None:
        # 如果没有指定实现名称，返回基类本身
        return cls
        
    # 动态导入指定的实现类
    impl_class = import_from(impl_name)
    
    # 验证导入的类是基类本身或其子类
    # 这确保了类型安全和接口兼容性
    assert cls == impl_class or issubclass(impl_class, cls)
    
    return impl_class
