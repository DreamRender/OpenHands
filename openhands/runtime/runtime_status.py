"""
运行时状态枚举模块。

该模块定义了运行时(Runtime)在不同生命周期阶段的状态值，
用于跟踪和管理运行时环境的启动、配置和就绪状态。
"""

# 标准库导入
from enum import Enum  # 枚举类型支持


class RuntimeStatus(Enum):
    """
    运行时状态枚举类。

    该枚举定义了运行时环境在启动和配置过程中的各种状态。
    每个状态包含一个唯一的状态值和用户友好的消息描述。

    Attributes:
        _value_ (str): 状态的内部标识符
        message (str): 状态的描述消息
    """

    def __init__(self, value: str, message: str):
        """
        初始化运行时状态枚举项。

        Args:
            value (str): 状态的内部标识符，用于系统内部识别
            message (str): 状态的描述消息，用于用户界面显示
        """
        self._value_ = value  # 设置枚举项的内部值
        self.message = message  # 设置枚举项的描述消息

    # 运行时已停止状态
    STOPPED = 'STATUS$STOPPED', 'Stopped'
    # 状态含义：运行时环境已停止运行

    # 运行时构建中状态
    BUILDING_RUNTIME = 'STATUS$BUILDING_RUNTIME', 'Building runtime...'
    # 状态含义：正在构建运行时环境

    # 运行时启动中状态
    STARTING_RUNTIME = 'STATUS$STARTING_RUNTIME', 'Starting runtime...'
    # 状态含义：正在启动运行时环境

    # 运行时已启动状态
    RUNTIME_STARTED = 'STATUS$RUNTIME_STARTED', 'Runtime started...'
    # 状态含义：运行时环境已成功启动

    # Workspace设置中状态
    SETTING_UP_WORKSPACE = 'STATUS$SETTING_UP_WORKSPACE', 'Setting up workspace...'
    # 状态含义：正在设置工作空间环境

    # Git hooks设置中状态
    SETTING_UP_GIT_HOOKS = 'STATUS$SETTING_UP_GIT_HOOKS', 'Setting up git hooks...'
    # 状态含义：正在设置Git钩子

    # 就绪状态
    READY = 'STATUS$READY', 'Ready...'
    # 状态含义：运行时环境已完全就绪，可以接受请求
