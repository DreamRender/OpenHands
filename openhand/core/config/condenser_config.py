from __future__ import annotations

from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openhands.core import logger
from openhands.core.config.llm_config import LLMConfig


class NoOpCondenserConfig(BaseModel):
    """NoOpCondenser的配置类。
    
    NoOpCondenser是一个空操作的压缩器，不执行任何实际的压缩操作。
    通常用作默认配置或当不需要历史压缩时使用。
    """

    type: Literal['noop'] = Field(default='noop')
    """Condenser类型标识符，固定为'noop'"""

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')


class ObservationMaskingCondenserConfig(BaseModel):
    """ObservationMaskingCondenser的配置类。
    
    这个Condenser通过屏蔽历史观察结果来减少上下文长度，
    只保留最近的一定数量的事件中的观察结果不被屏蔽。
    """

    type: Literal['observation_masking'] = Field(default='observation_masking')
    """Condenser类型标识符，固定为'observation_masking'"""
    
    attention_window: int = Field(
        default=100,
        description='最近事件的数量，在这个窗口内的观察结果不会被屏蔽',
        ge=1,
    )
    """注意力窗口大小，必须大于等于1"""

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')


class BrowserOutputCondenserConfig(BaseModel):
    """BrowserOutputCondenser的配置类。
    
    专门用于处理浏览器输出的Condenser，通过屏蔽较旧的浏览器输出观察结果来减少上下文。
    """

    type: Literal['browser_output_masking'] = Field(default='browser_output_masking')
    """Condenser类型标识符，固定为'browser_output_masking'"""
    
    attention_window: int = Field(
        default=1,
        description='不会被屏蔽的最近浏览器输出观察结果的数量',
        ge=1,
    )
    """注意力窗口大小，默认只保留最近1个浏览器输出"""


class RecentEventsCondenserConfig(BaseModel):
    """RecentEventsCondenser的配置类。
    
    这个Condenser只保留最近的一定数量的事件，丢弃更早的事件来控制上下文长度。
    """

    type: Literal['recent'] = Field(default='recent')
    """Condenser类型标识符，固定为'recent'"""

    # 默认至少保留一个事件，因为最好的猜测是它是用户任务
    keep_first: int = Field(
        default=1,
        description='要压缩的初始事件数量',
        ge=0,
    )
    """始终保留的初始事件数量，默认保留第一个事件（通常是用户任务）"""
    
    max_events: int = Field(
        default=100, description='要保留的最大事件数量', ge=1
    )
    """保留的最大事件数量，超过此数量的旧事件会被丢弃"""

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')


class LLMSummarizingCondenserConfig(BaseModel):
    """LLMCondenser的配置类。
    
    使用LLM对历史事件进行总结压缩，通过AI模型的理解能力生成简洁的历史摘要。
    """

    type: Literal['llm'] = Field(default='llm')
    """Condenser类型标识符，固定为'llm'"""
    
    llm_config: LLMConfig = Field(
        ..., description='用于压缩的LLM配置'
    )
    """执行压缩任务的LLM配置，必须提供"""

    # 默认至少保留一个事件，因为最好的猜测是它是用户任务
    keep_first: int = Field(
        default=1,
        description='始终保留在历史中的初始事件数量',
        ge=0,
    )
    """始终保留的初始事件数量，通常是用户的原始任务"""
    
    max_size: int = Field(
        default=100,
        description='触发遗忘之前压缩历史的最大大小',
        ge=2,
    )
    """触发压缩操作的最大历史大小阈值"""
    
    max_event_length: int = Field(
        default=10_000,
        description='传递给LLM的事件表示的最大长度',
    )
    """单个事件在传递给LLM进行处理时的最大字符长度"""

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')


class AmortizedForgettingCondenserConfig(BaseModel):
    """AmortizedForgettingCondenser的配置类。
    
    通过分摊遗忘机制来管理历史长度，逐步丢弃较旧的事件。
    """

    type: Literal['amortized'] = Field(default='amortized')
    """Condenser类型标识符，固定为'amortized'"""
    
    max_size: int = Field(
        default=100,
        description='触发遗忘之前压缩历史的最大大小',
        ge=2,
    )
    """触发遗忘操作的最大历史大小阈值"""

    # 默认至少保留一个事件，因为最好的猜测是它是用户任务
    keep_first: int = Field(
        default=1,
        description='始终保留在历史中的初始事件数量',
        ge=0,
    )
    """始终保留的初始事件数量，通常是用户的原始任务"""

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')


class LLMAttentionCondenserConfig(BaseModel):
    """LLMAttentionCondenser的配置类。
    
    使用LLM的注意力机制来选择重要的历史事件进行保留。
    """

    type: Literal['llm_attention'] = Field(default='llm_attention')
    """Condenser类型标识符，固定为'llm_attention'"""
    
    llm_config: LLMConfig = Field(
        ..., description='用于注意力计算的LLM配置'
    )
    """执行注意力计算的LLM配置，必须提供"""
    
    max_size: int = Field(
        default=100,
        description='触发遗忘之前压缩历史的最大大小',
        ge=2,
    )
    """触发遗忘操作的最大历史大小阈值"""

    # 默认至少保留一个事件，因为最好的猜测是它是用户任务
    keep_first: int = Field(
        default=1,
        description='始终保留在历史中的初始事件数量',
        ge=0,
    )
    """始终保留的初始事件数量，通常是用户的原始任务"""

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')


class StructuredSummaryCondenserConfig(BaseModel):
    """StructuredSummaryCondenser实例的配置类。
    
    生成结构化的历史摘要，使用LLM创建有组织的历史总结。
    """

    type: Literal['structured'] = Field(default='structured')
    """Condenser类型标识符，固定为'structured'"""
    
    llm_config: LLMConfig = Field(
        ..., description='用于压缩的LLM配置'
    )
    """执行结构化总结的LLM配置，必须提供"""

    # 默认至少保留一个事件，因为最好的猜测是它是用户任务
    keep_first: int = Field(
        default=1,
        description='始终保留在历史中的初始事件数量',
        ge=0,
    )
    """始终保留的初始事件数量，通常是用户的原始任务"""
    
    max_size: int = Field(
        default=100,
        description='触发遗忘之前压缩历史的最大大小',
        ge=2,
    )
    """触发压缩操作的最大历史大小阈值"""
    
    max_event_length: int = Field(
        default=10_000,
        description='传递给LLM的事件表示的最大长度',
    )
    """单个事件在传递给LLM进行处理时的最大字符长度"""

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')


class CondenserPipelineConfig(BaseModel):
    """CondenserPipeline的配置类。
    
    定义一个压缩器管道，可以按顺序执行多个压缩器。
    """

    type: Literal['pipeline'] = Field(default='pipeline')
    """Condenser类型标识符，固定为'pipeline'"""
    
    condensers: list[CondenserConfig] = Field(
        default_factory=list,
        description='管道中使用的压缩器配置列表',
    )
    """组成管道的压缩器配置列表，按顺序执行"""

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')


class ConversationWindowCondenserConfig(BaseModel):
    """ConversationWindowCondenser的配置类。

    目前不被TOML或ENV_VAR配置策略支持。
    这个Condenser维护一个对话窗口，只保留最近的对话内容。
    """

    type: Literal['conversation_window'] = Field(default='conversation_window')
    """Condenser类型标识符，固定为'conversation_window'"""

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')


# 便于使用的类型别名，包含所有可能的Condenser配置类型
CondenserConfig = (
    NoOpCondenserConfig
    | ObservationMaskingCondenserConfig
    | BrowserOutputCondenserConfig
    | RecentEventsCondenserConfig
    | LLMSummarizingCondenserConfig
    | AmortizedForgettingCondenserConfig
    | LLMAttentionCondenserConfig
    | StructuredSummaryCondenserConfig
    | CondenserPipelineConfig
    | ConversationWindowCondenserConfig
)


def condenser_config_from_toml_section(
    data: dict, llm_configs: dict | None = None
) -> dict[str, CondenserConfig]:
    """从表示[condenser]部分的toml字典创建CondenserConfig实例。

    对于CondenserConfig，处理方式不同，因为它是一个联合类型。
    Condenser的类型由部分中的'type'字段确定。

    Example:
        解析如下的condenser配置：
        [condenser]
        type = "noop"

        对于需要LLM配置的压缩器，可以指定LLM配置的名称：
        [condenser]
        type = "llm"
        llm_config = "my_llm"  # 引用[llm.my_llm]部分

    Args:
        data: 表示[condenser]部分的TOML字典
        llm_configs: 按名称键入的LLMConfig对象的可选字典

    Returns:
        dict[str, CondenserConfig]: 一个映射，其中键"condenser"对应配置
    """
    # 初始化结果映射
    condenser_mapping: dict[str, CondenserConfig] = {}

    # 处理配置
    try:
        # 根据'type'字段确定要使用的Condenser类型
        condenser_type = data.get('type', 'noop')

        # 如果需要，处理LLM配置引用
        if (
            condenser_type in ('llm', 'llm_attention')
            and 'llm_config' in data
            and isinstance(data['llm_config'], str)
        ):
            # 获取引用的LLM配置名称
            llm_config_name = data['llm_config']
            if llm_configs and llm_config_name in llm_configs:
                # 用实际的LLMConfig对象替换字符串引用
                data_copy = data.copy()
                data_copy['llm_config'] = llm_configs[llm_config_name]
                config = create_condenser_config(condenser_type, data_copy)
            else:
                logger.openhands_logger.warning(
                    f"LLM config '{llm_config_name}' not found for condenser. Using default LLMConfig."
                )
                # 如果引用的配置不存在，创建默认的LLMConfig
                data_copy = data.copy()
                # 尝试使用后备的'llm'配置
                if llm_configs is not None:
                    data_copy['llm_config'] = llm_configs.get('llm')
                config = create_condenser_config(condenser_type, data_copy)
        else:
            # 创建标准配置
            config = create_condenser_config(condenser_type, data)

        condenser_mapping['condenser'] = config
    except (ValidationError, ValueError) as e:
        logger.openhands_logger.warning(
            f'Invalid condenser configuration: {e}. Using NoOpCondenserConfig.'
        )
        # 如果配置失败，默认使用NoOpCondenserConfig
        config = NoOpCondenserConfig(type='noop')
        condenser_mapping['condenser'] = config

    return condenser_mapping


# 向后兼容性
from_toml_section = condenser_config_from_toml_section


def create_condenser_config(condenser_type: str, data: dict) -> CondenserConfig:
    """基于指定类型创建CondenserConfig实例。

    Args:
        condenser_type: 要创建的Condenser类型
        data: 配置数据

    Returns:
        CondenserConfig: CondenserConfig实例

    Raises:
        ValueError: 如果Condenser类型未知
        ValidationError: 如果提供的数据对于Condenser类型验证失败
    """
    # Condenser类型到其配置类的映射
    condenser_classes = {
        'noop': NoOpCondenserConfig,
        'observation_masking': ObservationMaskingCondenserConfig,
        'recent': RecentEventsCondenserConfig,
        'llm': LLMSummarizingCondenserConfig,
        'amortized': AmortizedForgettingCondenserConfig,
        'llm_attention': LLMAttentionCondenserConfig,
        'structured': StructuredSummaryCondenserConfig,
        'pipeline': CondenserPipelineConfig,
        'conversation_window': ConversationWindowCondenserConfig,
        'browser_output_masking': BrowserOutputCondenserConfig,
    }

    if condenser_type not in condenser_classes:
        raise ValueError(f'Unknown condenser type: {condenser_type}')

    # 使用直接实例化创建并验证配置
    # 显式处理ValidationError以提供更多上下文
    try:
        config_class = condenser_classes[condenser_type]
        # 使用类型转换帮助mypy理解返回类型
        return cast(CondenserConfig, config_class(**data))
    except ValidationError as e:
        # 重新抛出更描述性的消息，但不尝试传递错误
        # 这可以避免与不同pydantic版本的兼容性问题
        raise ValueError(
            f"Validation failed for condenser type '{condenser_type}': {e}"
        )