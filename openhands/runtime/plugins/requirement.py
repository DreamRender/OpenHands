"""OpenHands插件系统基础模块

该模块定义了OpenHands Agent插件系统的核心接口和数据结构。
提供了插件开发的基础框架，包括插件基类和插件需求定义。

主要组件:
    - Plugin: 插件抽象基类，定义了所有插件必须实现的接口
    - PluginRequirement: 插件需求数据类，用于描述插件的依赖关系

设计模式:
    该模块采用抽象基类模式，确保所有插件实现都遵循统一的接口规范。
    使用数据类简化插件需求的定义和管理。

使用场景:
    - 开发新的OpenHands插件时继承Plugin类
    - 定义插件依赖关系时使用PluginRequirement
    - 运行时系统通过这些接口管理插件生命周期
"""

# 标准库导入
from abc import abstractmethod  # 抽象方法装饰器
from dataclasses import dataclass  # 数据类装饰器

# OpenHands核心模块导入
from openhands.events.action import Action  # Action事件类型
from openhands.events.observation import Observation  # Observation事件类型


class Plugin:
    """OpenHands Agent插件系统的抽象基类

    该类定义了所有插件必须实现的核心接口，确保插件系统的一致性和可扩展性。
    所有插件都必须继承此类并实现其抽象方法。

    插件生命周期:
        1. 实例化：运行时客户端创建插件实例
        2. 初始化：调用initialize()方法进行环境设置
        3. 运行：通过run()方法处理Action并返回Observation
        4. 清理：插件生命周期结束时的资源清理

    运行环境:
        插件将在Docker容器内的运行时客户端中被初始化和执行，
        需要考虑容器环境的限制和安全性要求。

    Attributes:
        name (str): 插件的唯一标识名称，用于插件管理和调试
    """

    # 插件名称，每个插件都必须设置唯一的名称
    name: str

    @abstractmethod
    async def initialize(self, username: str) -> None:
        """初始化插件环境和资源

        该方法在插件被加载后首先调用，用于执行插件的初始化操作。
        包括但不限于：环境配置、资源分配、连接建立、权限验证等。

        Args:
            username (str): 当前用户的用户名，用于个性化配置和权限控制

        Returns:
            None: 该方法不返回值，通过异常机制报告初始化失败

        Raises:
            Exception: 当初始化过程中发生错误时，应抛出描述性异常

        实现要求:
            - 必须是异步方法，支持非阻塞初始化
            - 应该具有幂等性，多次调用应该安全
            - 初始化失败时应该清理已分配的资源
            - 应该记录适当的日志信息用于调试

        示例实现:
            ```python
            async def initialize(self, username: str) -> None:
                self.username = username
                self.config = load_user_config(username)
                await self.connect_to_service()
                logging.info(f"Plugin {self.name} initialized for user {username}")
            ```
        """
        pass

    @abstractmethod
    async def run(self, action: Action) -> Observation:
        """执行插件的核心功能逻辑

        该方法是插件的主要入口点，接收Action事件并返回相应的Observation。
        每个Action代表Agent需要执行的一个操作，Observation表示操作的结果或反馈。

        Args:
            action (Action): Agent发起的动作事件，包含操作类型和参数

        Returns:
            Observation: 执行结果的观察事件，包含操作反馈和状态信息

        Raises:
            Exception: 当执行过程中发生错误时，应抛出描述性异常

        实现要求:
            - 必须是异步方法，支持长时间运行的操作
            - 应该根据action的类型执行相应的业务逻辑
            - 返回的Observation应该包含足够的信息供Agent决策
            - 应该处理和转换可能的异常为适当的Observation
            - 执行时间较长的操作应该考虑超时处理

        Action-Observation模式:
            这种模式是Agent系统的核心，允许Agent与环境进行交互：
            - Action表示Agent的意图和指令
            - Observation表示环境对Action的响应
            - 通过这种循环实现Agent的感知-决策-行动循环

        示例实现:
            ```python
            async def run(self, action: Action) -> Observation:
                if isinstance(action, FileReadAction):
                    content = await self.read_file(action.file_path)
                    return FileReadObservation(content=content)
                else:
                    return ErrorObservation(message="Unsupported action type")
            ```
        """
        pass


@dataclass
class PluginRequirement:
    """插件依赖需求的数据结构

    该数据类用于描述插件的依赖关系和需求信息，
    支持插件系统进行依赖解析、版本管理和兼容性检查。

    使用场景:
        - 插件清单文件中声明依赖关系
        - 运行时系统验证插件兼容性
        - 自动安装和配置插件依赖
        - 插件冲突检测和解决

    Attributes:
        name (str): 依赖插件或组件的名称标识符

    扩展可能性:
        未来版本可能会扩展该类以包含更多信息：
        - version: 版本要求规范
        - optional: 是否为可选依赖
        - constraints: 额外的约束条件
        - source: 依赖来源信息

    示例用法:
        ```python
        # 定义插件需求
        requirements = [
            PluginRequirement(name="file_system"),
            PluginRequirement(name="network_client"),
            PluginRequirement(name="database_connector")
        ]

        # 在插件类中使用
        class MyPlugin(Plugin):
            name = "my_custom_plugin"
            requirements = [PluginRequirement(name="file_system")]
        ```
    """

    name: str  # 依赖的插件或组件名称
