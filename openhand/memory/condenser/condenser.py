from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any

from pydantic import BaseModel

from openhands.controller.state.state import State
from openhands.core.config.condenser_config import CondenserConfig
from openhands.events.action.agent import CondensationAction
from openhands.memory.view import View

# 全局常量：用于标识 metadata 在 State 对象的 extra_data 字段中的存储位置
CONDENSER_METADATA_KEY = 'condenser_meta'
"""用于标识 metadata 在 `State` 对象的 `extra_data` 字段中存储位置的键名。

这个键用于在 State 对象中存储和检索压缩器相关的 metadata 信息，
每次压缩操作的诊断信息都会以批次形式存储在这个键下。
"""


def get_condensation_metadata(state: State) -> list[dict[str, Any]]:
    """从 `State` 对象中检索 metadata 批次列表的工具函数。

    该函数用于获取状态对象中存储的所有压缩器 metadata 批次，
    每个批次代表一次压缩操作的相关信息。

    Args:
        state: 要从中检索 metadata 的状态对象

    Returns:
        list[dict[str, Any]]: metadata 批次列表，每个批次代表一次压缩操作的 metadata
    """
    # 检查状态对象的额外数据中是否存在压缩器 metadata
    if CONDENSER_METADATA_KEY in state.extra_data:
        return state.extra_data[CONDENSER_METADATA_KEY]
    # 如果不存在则返回空列表
    return []


# 全局注册表：存储压缩器配置类型到对应压缩器类的映射关系
CONDENSER_REGISTRY: dict[type[CondenserConfig], type[Condenser]] = {}
"""压缩器配置类型到对应压缩器类的注册表。

这个全局字典用于管理不同类型的压缩器配置与其对应实现类之间的映射关系，
支持动态注册和创建压缩器实例。
"""


class Condensation(BaseModel):
    """由压缩器产生的对象，用于表示历史已被压缩。

    当压缩器决定需要进行压缩操作时，会返回此对象而不是 View 对象。
    Agent 收到此对象后应该返回其中包含的 action，而不是产生自己的 action。
    """

    action: CondensationAction
    """压缩动作：包含压缩操作的具体信息"""


class Condenser(ABC):
    """抽象压缩器接口。

    压缩器接收一个 `Event` 对象列表，并将其减少为一个可能更小的列表。

    Agent 可以使用压缩器来减少在决定执行哪个动作时需要考虑的事件数量。
    要使用压缩器，Agent 可以调用当前考虑的 `State` 对象的 `condensed_history` 方法，
    并使用结果而不是完整的历史记录。

    如果压缩器返回 `Condensation` 而不是 `View`，Agent 应该返回 `Condensation.action`
    而不是产生自己的动作。在下一个 Agent 步骤中，压缩器将使用该压缩事件来产生新的 `View`。
    """

    def __init__(self):
        """初始化压缩器实例。

        创建用于存储当前批次 metadata 和 LLM metadata 的字典。
        """
        # 当前 metadata 批次：存储本次压缩操作的诊断信息
        self._metadata_batch: dict[str, Any] = {}
        # LLM metadata：存储与 LLM 相关的 metadata 信息
        self._llm_metadata: dict[str, Any] = {}

    def add_metadata(self, key: str, value: Any) -> None:
        """向当前 metadata 批次添加信息。

        添加到 metadata 批次的任何键值对都将在当前压缩结束时记录到 `State` 中。
        这些信息用于诊断和分析压缩器的行为。

        Args:
            key: 存储 metadata 的键名
            value: 要存储的 metadata 值
        """
        self._metadata_batch[key] = value

    def write_metadata(self, state: State) -> None:
        """将当前批次的 metadata 写入 `State`。

        重置当前 metadata 批次：此调用后添加的任何 metadata 将存储在新批次中，
        并在下次压缩结束时写入 `State`。

        Args:
            state: 要写入 metadata 的状态对象
        """
        # 如果状态对象的额外数据中还没有压缩器 metadata 键，则创建一个空列表
        if CONDENSER_METADATA_KEY not in state.extra_data:
            state.extra_data[CONDENSER_METADATA_KEY] = []

        # 如果当前批次有 metadata，则添加到状态对象中
        if self._metadata_batch:
            state.extra_data[CONDENSER_METADATA_KEY].append(self._metadata_batch)

        # 批次已写入，清空以备下次压缩使用
        self._metadata_batch = {}

    @contextmanager
    def metadata_batch(self, state: State):
        """上下文管理器，确保批次 metadata 始终写入 `State`。

        使用此上下文管理器可以确保即使在压缩过程中发生异常，
        metadata 也会被正确写入状态对象。

        Args:
            state: 要写入 metadata 的状态对象
        """
        try:
            # 执行压缩操作
            yield
        finally:
            # 无论是否发生异常，都确保 metadata 被写入
            self.write_metadata(state)

    @abstractmethod
    def condense(self, view: View) -> View | Condensation:
        """将事件序列压缩为可能更小的列表。

        新的压缩器策略应该重写此方法来实现自己的压缩逻辑。
        在实现中调用 `self.add_metadata` 来记录任何相关的每次压缩诊断信息。

        Args:
            view: 包含应被压缩的所有事件的历史视图

        Returns:
            View | Condensation: 压缩后的事件视图或表示历史已被压缩的事件
        """

    def condensed_history(self, state: State) -> View | Condensation:
        """压缩状态的历史记录。

        这是压缩器的主要入口点，它会设置 LLM metadata，
        使用 metadata 批次上下文管理器，然后调用具体的压缩实现。

        Args:
            state: 要压缩历史记录的状态对象

        Returns:
            View | Condensation: 压缩后的历史视图或压缩动作
        """
        # 从状态对象中提取 LLM metadata 信息
        self._llm_metadata = state.to_llm_metadata('condenser')

        # 使用上下文管理器确保 metadata 被正确写入
        with self.metadata_batch(state):
            # 调用具体的压缩实现
            return self.condense(state.view)

    @classmethod
    def register_config(cls, configuration_type: type[CondenserConfig]) -> None:
        """注册新的压缩器配置类型。

        已注册配置类型的实例可以传递给 `from_config` 来创建相应压缩器的实例。
        这个机制支持插件式的压缩器扩展。

        Args:
            configuration_type: 用于创建压缩器实例的配置类型

        Raises:
            ValueError: 如果配置类型已经注册过
        """
        # 检查配置类型是否已经注册
        if configuration_type in CONDENSER_REGISTRY:
            raise ValueError(
                f'Condenser 配置 {configuration_type} 已经注册过了'
            )
        # 将配置类型与当前压缩器类关联
        CONDENSER_REGISTRY[configuration_type] = cls

    @classmethod
    def from_config(cls, config: CondenserConfig) -> Condenser:
        """从配置对象创建压缩器。

        根据提供的配置对象类型，从注册表中查找对应的压缩器类，
        并使用该配置创建压缩器实例。

        Args:
            config: 压缩器的配置对象

        Returns:
            Condenser: 压缩器实例

        Raises:
            ValueError: 如果压缩器类型无法识别
        """
        try:
            # 从注册表中获取配置对应的压缩器类
            condenser_class = CONDENSER_REGISTRY[type(config)]
            # 使用配置创建压缩器实例
            return condenser_class.from_config(config)
        except KeyError:
            # 如果配置类型未注册，抛出错误
            raise ValueError(f'未知的压缩器配置: {config}')


class RollingCondenser(Condenser, ABC):
    """应用压缩到滚动历史的专门压缩器策略的基类。

    滚动历史由 `View.from_events` 生成，它分析历史中的所有事件并产生一个
    表示将发送给 LLM 的内容的 `View` 对象。

    如果 `should_condense` 判断需要压缩，压缩器负责从 `View` 对象生成 `Condensation` 对象。
    这将被添加到事件历史中，当传递给 `get_view` 时应该产生要传递给 LLM 的压缩 `View`。
    """

    @abstractmethod
    def should_condense(self, view: View) -> bool:
        """确定视图是否应该被压缩。

        子类需要实现此方法来定义压缩的触发条件，
        例如基于事件数量、内容长度或其他启发式规则。

        Args:
            view: 要判断是否需要压缩的视图

        Returns:
            bool: 如果需要压缩返回 True，否则返回 False
        """

    @abstractmethod
    def get_condensation(self, view: View) -> Condensation:
        """从视图获取压缩结果。

        当 `should_condense` 返回 True 时，此方法负责执行实际的压缩操作，
        生成包含压缩动作的 Condensation 对象。

        Args:
            view: 要压缩的视图

        Returns:
            Condensation: 包含压缩动作的对象
        """

    def condense(self, view: View) -> View | Condensation:
        """执行压缩逻辑的具体实现。

        这个方法实现了滚动压缩器的通用逻辑：
        1. 首先检查是否需要压缩
        2. 如果需要，则执行压缩并返回 Condensation
        3. 如果不需要，则直接返回原始视图

        Args:
            view: 要处理的视图

        Returns:
            View | Condensation: 原始视图或压缩结果
        """
        # 如果触发了压缩器特定的压缩阈值，计算并返回压缩结果
        if self.should_condense(view):
            return self.get_condensation(view)

        # 否则直接返回视图是安全的
        else:
            return view
