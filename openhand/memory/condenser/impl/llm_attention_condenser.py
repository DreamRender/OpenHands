from __future__ import annotations

# 导入LiteLLM库中的响应Schema支持检查函数
from litellm import supports_response_schema
# 导入Pydantic基础模型用于数据验证
from pydantic import BaseModel

# 导入LLMAttentionCondenser的配置类
from openhands.core.config.condenser_config import LLMAttentionCondenserConfig
# 导入Agent的Condensation Action相关类
from openhands.events.action.agent import CondensationAction
# 导入LLM类用于与语言模型交互
from openhands.llm.llm import LLM
# 导入Condenser相关的基础类和接口
from openhands.memory.condenser.condenser import (
    Condensation,
    RollingCondenser,
    View,
)


class ImportantEventSelection(BaseModel):
    """
    重要事件选择模型类

    这是一个Pydantic数据模型，用于`LLMAttentionCondenser`类中强制LLM返回整数列表的格式。
    通过定义结构化的返回格式，确保LLM的响应能够被正确解析为事件ID列表。

    Attributes:
        ids (list[int]): 经LLM筛选后的重要事件ID列表，按重要性排序
    """

    ids: list[int]


class LLMAttentionCondenser(RollingCondenser):
    """
    基于LLM注意力机制的滚动Condenser策略类

    该类继承自RollingCondenser，实现了一种使用大型语言模型来智能选择最重要事件的
    历史压缩策略。当事件历史超过指定大小时，通过LLM分析每个事件的重要性，
    保留最关键的事件并丢弃不重要的事件，从而有效管理Memory使用。

    主要特点：
    - 使用LLM的智能判断能力来评估事件重要性
    - 保证总是保留最初的几个事件（通过keep_first参数）
    - 支持响应Schema功能确保LLM返回格式正确

    Attributes:
        max_size (int): 允许保存的最大事件数量
        keep_first (int): 强制保留的前几个事件数量
        llm (LLM): 用于事件重要性评估的语言模型实例
    """

    def __init__(self, llm: LLM, max_size: int = 100, keep_first: int = 1):
        """
        初始化LLMAttentionCondenser实例

        Args:
            llm (LLM): 用于评估事件重要性的语言模型实例
            max_size (int, optional): 允许保存的最大事件数量。默认为100
            keep_first (int, optional): 强制保留的前几个事件数量。默认为1

        Raises:
            ValueError: 当参数不满足约束条件时抛出异常：
                - keep_first必须小于max_size的一半
                - keep_first不能为负数
                - max_size必须为正数
                - LLM模型必须支持response_schema参数
        """
        # 验证keep_first参数：必须小于max_size的一半，确保有足够空间进行选择
        if keep_first >= max_size // 2:
            raise ValueError(
                f'keep_first ({keep_first}) must be less than half of max_size ({max_size})'
            )
        # 验证keep_first参数：不能为负数
        if keep_first < 0:
            raise ValueError(f'keep_first ({keep_first}) cannot be negative')
        # 验证max_size参数：必须为正数
        if max_size < 1:
            raise ValueError(f'max_size ({keep_first}) cannot be non-positive')

        # 设置实例属性
        self.max_size = max_size  # 最大事件容量
        self.keep_first = keep_first  # 保留前几个事件的数量
        self.llm = llm  # LLM实例用于智能选择

        # 检查LLM模型是否支持response_schema功能
        # 该Condenser依赖于`response_schema`特性来确保LLM返回结构化数据
        if not supports_response_schema(
                model=self.llm.config.model,
                custom_llm_provider=self.llm.config.custom_llm_provider,
        ):
            raise ValueError(
                "The LLM model must support the 'response_schema' parameter to use the LLMAttentionCondenser."
            )

        # 调用父类初始化方法
        super().__init__()

    def get_condensation(self, view: View) -> Condensation:
        """
        获取压缩操作方案

        该方法是核心算法实现，通过LLM分析当前View中的所有事件，
        智能选择最重要的事件保留，生成相应的Condensation操作。

        算法流程：
        1. 计算目标保留事件数量（max_size的一半）
        2. 确定强制保留的头部事件ID
        3. 使用LLM评估剩余事件的重要性
        4. 根据LLM结果选择要保留的事件
        5. 生成包含要遗忘事件ID的Condensation对象

        Args:
            view (View): 当前的事件View，包含所有待处理的事件

        Returns:
            Condensation: 包含压缩操作信息的对象，指定哪些事件应被遗忘
        """
        # 计算目标保留事件数量：设为max_size的一半，为未来事件留出空间
        target_size = self.max_size // 2

        # 获取强制保留的头部事件ID列表
        head_event_ids = [event.id for event in view.events[: self.keep_first]]

        # 计算从尾部需要选择的事件数量
        events_from_tail = target_size - len(head_event_ids)

        # 构建发送给LLM的提示消息
        # 要求LLM根据重要性对事件进行排序
        message: str = """You will be given a list of actions, observations, and thoughts from a coding agent.
        Each item in the list has an identifier. Please sort the identifiers in order of how important the
        contents of the item are for the next step of the coding agent's task, from most important to least
        important."""

        # 调用LLM进行事件重要性评估
        response = self.llm.completion(
            messages=[
                {'content': message, 'role': 'user'},  # 发送指导消息
                # 为每个事件创建一条消息，包含事件ID和内容
                *[
                    {
                        'content': f'<ID>{e.id}</ID>\n<CONTENT>{e.message}</CONTENT>',
                        'role': 'user',
                    }
                    for e in view  # 遍历View中的所有事件
                ],
            ],
            # 指定响应格式为JSON Schema，确保返回结构化数据
            response_format={
                'type': 'json_schema',
                'json_schema': {
                    'name': 'ImportantEventSelection',
                    'schema': ImportantEventSelection.model_json_schema(),
                },
            },
        )

        # 解析LLM返回的事件ID列表
        response_ids = ImportantEventSelection.model_validate_json(
            response.choices[0].message.content
        ).ids

        # 记录LLM使用的Metrics数据用于性能监控
        self.add_metadata('metrics', self.llm.metrics.get())

        # 过滤掉头部事件ID（因为它们已经被强制保留）
        # 并截取到需要的事件数量
        response_ids = [
                           response_id
                           for response_id in response_ids
                           if response_id not in head_event_ids
                       ][:events_from_tail]

        # 如果LLM返回的事件ID数量不足，需要补充
        # 从View末尾开始倒序遍历，添加未被选中的事件ID
        for event in reversed(view):
            # 如果已经收集到足够的事件ID，停止遍历
            if len(response_ids) >= events_from_tail:
                break
            # 如果当前事件没有被选中，将其添加到列表中
            if event.id not in response_ids:
                response_ids.append(event.id)

        # 生成要遗忘的事件ID列表
        # 遗忘的事件 = 所有事件 - 头部保留事件 - LLM选择的重要事件
        event = CondensationAction(
            forgotten_event_ids=[
                event.id
                for event in view
                if event.id not in response_ids and event.id not in head_event_ids
            ],
        )

        # 返回包含压缩操作的Condensation对象
        return Condensation(action=event)

    def should_condense(self, view: View) -> bool:
        """
        判断是否需要进行压缩操作

        这是一个简单的阈值检查方法，当View中的事件数量超过设定的
        最大值时，触发压缩操作。

        Args:
            view (View): 当前的事件View

        Returns:
            bool: 如果View中事件数量超过max_size则返回True，否则返回False
        """
        return len(view) > self.max_size

    @classmethod
    def from_config(cls, config: LLMAttentionCondenserConfig) -> LLMAttentionCondenser:
        """
        从配置对象创建LLMAttentionCondenser实例

        这是一个类方法，用于根据配置文件创建Condenser实例。
        注意：该Condenser无法利用提示缓存功能，因为每次调用LLM时
        都需要发送不同的事件内容，所以这里禁用了缓存功能。

        Args:
            config (LLMAttentionCondenserConfig): Condenser的配置对象

        Returns:
            LLMAttentionCondenser: 配置完成的Condenser实例
        """
        # 复制LLM配置对象以避免修改原始配置
        # 该Condenser无法利用提示缓存功能，如果启用缓存，
        # 我们会为缓存写入付费但永远不会有机会节省读取费用
        llm_config = config.llm_config.model_copy()
        llm_config.caching_prompt = False  # 禁用提示缓存

        # 创建并返回LLMAttentionCondenser实例
        return LLMAttentionCondenser(
            llm=LLM(config=llm_config),  # 使用修改后的配置创建LLM实例
            max_size=config.max_size,  # 设置最大事件数量
            keep_first=config.keep_first,  # 设置保留头部事件数量
        )


# 注册配置类，使得LLMAttentionCondenser可以通过配置系统进行实例化
LLMAttentionCondenser.register_config(LLMAttentionCondenserConfig)
