"""
Openhands Runtime 模块初始化文件
本模块负责管理和注册各种运行时环境，包括核心运行时和第三方运行时
支持动态发现和加载第三方运行时实现
"""

import importlib

# 导入核心运行时基类和各种实现
from openhands.runtime.base import Runtime  # 运行时基类，定义了运行时的基本接口
from openhands.runtime.impl.cli.cli_runtime import CLIRuntime  # CLI运行时，通过命令行接口执行
from openhands.runtime.impl.docker.docker_runtime import (
    DockerRuntime,  # Docker运行时，在Docker容器中执行代码
)
from openhands.runtime.impl.kubernetes.kubernetes_runtime import KubernetesRuntime  # Kubernetes运行时，在K8s集群中执行
from openhands.runtime.impl.local.local_runtime import LocalRuntime  # 本地运行时，在本地环境中直接执行
from openhands.runtime.impl.remote.remote_runtime import RemoteRuntime  # 远程运行时，连接到远程服务器执行
from openhands.utils.import_utils import get_impl  # 工具函数，用于动态导入指定的实现类

# mypy: disable-error-code="type-abstract"
# 这个注释告诉mypy忽略抽象类型错误，因为我们在这里存储的是具体的实现类

_DEFAULT_RUNTIME_CLASSES: dict[str, type[Runtime]] = {
    """
    默认的Runtime类映射字典
    将运行时名称映射到对应的Runtime类实现

    支持的运行时类型：
    - eventstream: 基于事件流的Docker运行时
    - docker: 标准Docker运行时
    - remote: 远程服务器运行时
    - local: 本地环境运行时
    - kubernetes: Kubernetes集群运行时
    - cli: 命令行接口运行时
    """
    'eventstream': DockerRuntime,  # 事件流模式使用Docker运行时
    'docker': DockerRuntime,  # 标准Docker运行时
    'remote': RemoteRuntime,  # 远程运行时
    'local': LocalRuntime,  # 本地运行时
    'kubernetes': KubernetesRuntime,  # Kubernetes运行时
    'cli': CLIRuntime,  # CLI运行时
}

# 尝试导入第三方运行时（如果可用）
_THIRD_PARTY_RUNTIME_CLASSES: dict[str, type[Runtime]] = {}
"""
第三方Runtime类映射字典
存储从第三方包中动态发现和加载的Runtime实现
这些运行时是可选的，不是核心功能的一部分
"""

# 动态发现和导入第三方运行时

# 检查第三方包是否存在并发现运行时
try:
    import third_party.runtime.impl

    # 导入第三方运行时实现包

    third_party_base = 'third_party.runtime.impl'
    # 第三方运行时包的基础路径

    # 用于尝试的潜在第三方运行时模块列表
    # 这些是从third_party目录结构中发现的
    potential_runtimes = []
    try:
        import pkgutil

        # 导入pkgutil模块，用于包的动态发现

        # 遍历第三方运行时包中的所有子包
        for importer, modname, ispkg in pkgutil.iter_modules(
                third_party.runtime.impl.__path__
        ):
            if ispkg:  # 只处理包（不是单个模块文件）
                potential_runtimes.append(modname)
                # 将发现的包名添加到潜在运行时列表中
    except Exception:
        # 如果发现失败，不会加载任何第三方运行时
        potential_runtimes = []

    # 尝试导入每个发现的运行时
    for runtime_name in potential_runtimes:
        try:
            # 构造模块路径，例如：third_party.runtime.impl.e2b.e2b_runtime
            module_path = f'{third_party_base}.{runtime_name}.{runtime_name}_runtime'
            module = importlib.import_module(module_path)
            # 动态导入运行时模块

            # 尝试不同的类名模式
            possible_class_names = [
                f'{runtime_name.upper()}Runtime',  # 例如：E2BRuntime（全大写）
                f'{runtime_name.capitalize()}Runtime',  # 例如：E2bRuntime, DaytonaRuntime（首字母大写）
            ]

            runtime_class = None
            # 遍历可能的类名，尝试获取运行时类
            for class_name in possible_class_names:
                try:
                    runtime_class = getattr(module, class_name)
                    # 尝试从模块中获取对应的类
                    break  # 找到类后立即跳出循环
                except AttributeError:
                    continue  # 如果类不存在，继续尝试下一个类名

            if runtime_class:
                # 如果成功找到运行时类，将其添加到第三方运行时字典中
                _THIRD_PARTY_RUNTIME_CLASSES[runtime_name] = runtime_class

        except ImportError:
            # ImportError意味着库没有安装（这是可选依赖的预期行为）
            pass
        except Exception as e:
            # 其他异常意味着库存在但有问题，应该记录警告
            from openhands.core.logger import openhands_logger as logger

            logger.warning(f'Failed to import third-party runtime {module_path}: {e}')
            # 记录导入第三方运行时失败的警告信息
            pass

except ImportError:
    # third_party包不可用
    pass

# 合并核心和第三方运行时
_ALL_RUNTIME_CLASSES = {**_DEFAULT_RUNTIME_CLASSES, **_THIRD_PARTY_RUNTIME_CLASSES}
"""
所有可用的Runtime类映射字典
包含核心运行时和第三方运行时的完整映射
这是运行时查找的最终字典
"""


def get_runtime_cls(name: str) -> type[Runtime]:
    """
    根据名称获取Runtime类

    Args:
        name (str): 运行时名称，可以是预定义的名称（如'docker'）或自定义的Runtime子类名

    Returns:
        type[Runtime]: 对应的Runtime类

    Raises:
        ValueError: 当指定的运行时名称不受支持时抛出

    说明:
        如果name是预定义的运行时名称之一（例如'docker'），返回其对应的类。
        否则尝试将name解析为Runtime的子类并返回它。
        如果选择无效则抛出异常。
    """
    if name in _ALL_RUNTIME_CLASSES:
        # 如果是已注册的运行时名称，直接返回对应的类
        return _ALL_RUNTIME_CLASSES[name]
    try:
        # 尝试将name作为Runtime子类的完整路径来解析
        return get_impl(Runtime, name)
    except Exception as e:
        # 如果解析失败，抛出详细的错误信息
        known_keys = _ALL_RUNTIME_CLASSES.keys()
        raise ValueError(
            f'Runtime {name} not supported, known are: {known_keys}'
            # 运行时{name}不受支持，已知的有：{known_keys}
        ) from e


# 根据可用的运行时动态构建__all__列表
__all__ = [
    'Runtime',  # 运行时基类
    'RemoteRuntime',  # 远程运行时
    'DockerRuntime',  # Docker运行时
    'KubernetesRuntime',  # Kubernetes运行时
    'CLIRuntime',  # CLI运行时
    'LocalRuntime',  # 本地运行时
    'get_runtime_cls',  # 获取运行时类的函数
]

# 如果第三方运行时可用，将它们添加到__all__中
for runtime_name, runtime_class in _THIRD_PARTY_RUNTIME_CLASSES.items():
    __all__.append(runtime_class.__name__)
    # 将第三方运行时类名添加到公共API列表中
