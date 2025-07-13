from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from openhands.core.config.condenser_config import (
    StructuredSummaryCondenserConfig,
)
from openhands.core.logger import openhands_logger as logger
from openhands.core.message import Message, TextContent
from openhands.events.action.agent import CondensationAction
from openhands.events.observation.agent import AgentCondensationObservation
from openhands.events.serialization.event import truncate_content
from openhands.llm import LLM
from openhands.memory.condenser.condenser import (
    Condensation,
    RollingCondenser,
    View,
)


class StateSummary(BaseModel):
    """
    一个结构化表示，用于摘要Agent和任务的State。

    该类定义了一个标准化的结构来捕获和组织Agent执行过程中的
    关键信息，包括用户上下文、任务状态、代码变更、测试状态和
    版本控制信息等。

    Attributes:
        user_context (str): 用户需求、目标和澄清的核心信息
        completed_tasks (str): 已完成的任务列表及简要结果
        pending_tasks (str): 仍需完成的任务列表
        current_state (str): 当前变量、数据结构或其他相关状态信息
        files_modified (str): 已创建或修改的文件列表
        function_changes (str): 已创建或修改的函数列表
        data_structures (str): 正在使用或已修改的关键数据结构列表
        tests_written (str): 是否为变更编写了测试
        tests_passing (str): 所有测试是否当前通过
        failing_tests (str): 任何失败测试的名称或描述列表
        error_messages (str): 遇到的关键错误消息列表
        branch_created (str): 是否为此工作创建了分支
        branch_name (str): 当前工作分支的名称（如果已知）
        commits_made (str): 是否已进行任何提交
        pr_created (str): 是否已创建拉取请求
        pr_status (str): 任何拉取请求的状态
        dependencies (str): 已添加或修改的依赖项或导入列表
        other_relevant_context (str): 不适合上述类别的其他重要信息
    """

    # 必需的核心字段
    user_context: str = Field(
        default='',
        description='Essential user requirements, goals, and clarifications in concise form.',
    )
    completed_tasks: str = Field(
        default='', description='List of tasks completed so far with brief results.'
    )
    pending_tasks: str = Field(
        default='', description='List of tasks that still need to be done.'
    )
    current_state: str = Field(
        default='',
        description='Current variables, data structures, or other relevant state information.',
    )

    # 代码状态字段
    files_modified: str = Field(
        default='', description='List of files that have been created or modified.'
    )
    function_changes: str = Field(
        default='', description='List of functions that have been created or modified.'
    )
    data_structures: str = Field(
        default='', description='List of key data structures in use or modified.'
    )

    # 测试状态字段
    tests_written: str = Field(
        default='',
        description='Whether tests have been written for the changes. True, false, or unknown.',
    )
    tests_passing: str = Field(
        default='',
        description='Whether all tests are currently passing. True, false, or unknown.',
    )
    failing_tests: str = Field(
        default='', description='List of names or descriptions of any failing tests.'
    )
    error_messages: str = Field(
        default='', description='List of key error messages encountered.'
    )

    # 版本控制字段
    branch_created: str = Field(
        default='',
        description='Whether a branch has been created for this work. True, false, or unknown.',
    )
    branch_name: str = Field(
        default='', description='Name of the current working branch if known.'
    )
    commits_made: str = Field(
        default='',
        description='Whether any commits have been made. True, false, or unknown.',
    )
    pr_created: str = Field(
        default='',
        description='Whether a pull request has been created. True, false, or unknown.',
    )
    pr_status: str = Field(
        default='',
        description="Status of any pull request: 'draft', 'open', 'merged', 'closed', or 'unknown'.",
    )

    # 其他字段
    dependencies: str = Field(
        default='',
        description='List of dependencies or imports that have been added or modified.',
    )
    other_relevant_context: str = Field(
        default='',
        description="Any other important information that doesn't fit into the categories above.",
    )

    @classmethod
    def tool_description(cls) -> dict[str, Any]:
        """
        描述一个工具，其参数是该类的字段。

        可以提供给LLM以强制结构化生成。该方法创建了一个符合
        OpenAI函数调用格式的工具描述，用于指导LLM生成结构化摘要。

        Returns:
            dict[str, Any]: 包含工具名称、描述和参数的字典
        """
        properties = {}

        # 从字段信息构建属性字典
        for field_name, field in cls.model_fields.items():
            description = field.description or ''

            properties[field_name] = {'type': 'string', 'description': description}

        return {
            'type': 'function',
            'function': {
                'name': 'create_state_summary',
                'description': 'Creates a comprehensive summary of the current state of the interaction to preserve context when history grows too large. You must include non-empty values for user_context, completed_tasks, and pending_tasks.',
                'parameters': {
                    'type': 'object',
                    'properties': properties,
                    'required': ['user_context', 'completed_tasks', 'pending_tasks'],
                },
            },
        }

    def __str__(self) -> str:
        """
        以清晰的方式格式化State摘要，适合Claude 3.7 Sonnet模型理解。

        Returns:
            str: 格式化的摘要字符串，使用Markdown格式
        """
        sections = [
            '# State Summary',
            '## Core Information',
            f'**User Context**: {self.user_context}',
            f'**Completed Tasks**: {self.completed_tasks}',
            f'**Pending Tasks**: {self.pending_tasks}',
            f'**Current State**: {self.current_state}',
            '## Code Changes',
            f'**Files Modified**: {self.files_modified}',
            f'**Function Changes**: {self.function_changes}',
            f'**Data Structures**: {self.data_structures}',
            f'**Dependencies**: {self.dependencies}',
            '## Testing Status',
            f'**Tests Written**: {self.tests_written}',
            f'**Tests Passing**: {self.tests_passing}',
            f'**Failing Tests**: {self.failing_tests}',
            f'**Error Messages**: {self.error_messages}',
            '## Version Control',
            f'**Branch Created**: {self.branch_created}',
            f'**Branch Name**: {self.branch_name}',
            f'**Commits Made**: {self.commits_made}',
            f'**PR Created**: {self.pr_created}',
            f'**PR Status**: {self.pr_status}',
            '## Additional Context',
            f'**Other Relevant Context**: {self.other_relevant_context}',
        ]

        # 用双换行符连接所有部分
        return '\n\n'.join(sections)


class StructuredSummaryCondenser(RollingCondenser):
    """
    一个使用结构化生成对被遗忘事件进行摘要的Condenser。

    维护一个压缩后的历史记录，当历史记录增长过大时会遗忘旧事件。
    使用函数调用的结构化生成来产生替换被遗忘事件的摘要。

    Attributes:
        max_size (int): 触发压缩的最大事件数量
        keep_first (int): 从开头保留的事件数量
        max_event_length (int): 单个事件内容的最大长度
        llm (LLM): 用于生成摘要的大语言模型实例，必须支持函数调用
    """

    def __init__(
            self,
            llm: LLM,
            max_size: int = 100,
            keep_first: int = 1,
            max_event_length: int = 10_000,
    ):
        """
        初始化StructuredSummaryCondenser。

        Args:
            llm (LLM): 用于生成摘要的大语言模型实例，必须支持函数调用
            max_size (int, optional): 触发压缩的最大事件数量。默认为100
            keep_first (int, optional): 从开头保留的事件数量。默认为1
            max_event_length (int, optional): 单个事件内容的最大字符长度。默认为10,000

        Raises:
            ValueError: 当参数不满足约束条件或LLM不支持函数调用时抛出异常
        """
        # 验证keep_first不能超过max_size的一半
        if keep_first >= max_size // 2:
            raise ValueError(
                f'keep_first ({keep_first}) must be less than half of max_size ({max_size})'
            )
        # 验证keep_first不能为负数
        if keep_first < 0:
            raise ValueError(f'keep_first ({keep_first}) cannot be negative')
        # 验证max_size必须为正数
        if max_size < 1:
            raise ValueError(f'max_size ({max_size}) cannot be non-positive')

        # 验证LLM是否支持函数调用，这是该Condenser的必需功能
        if not llm.is_function_calling_active():
            raise ValueError(
                'LLM must support function calling to use StructuredSummaryCondenser'
            )

        # 设置实例变量
        self.max_size = max_size
        self.keep_first = keep_first
        self.max_event_length = max_event_length
        self.llm = llm

        # 调用父类构造函数
        super().__init__()

    def _truncate(self, content: str) -> str:
        """
        截断内容以适应指定的最大事件长度。

        Args:
            content (str): 需要截断的内容字符串

        Returns:
            str: 截断后的内容字符串
        """
        return truncate_content(content, max_chars=self.max_event_length)

    def get_condensation(self, view: View) -> Condensation:
        """
        生成压缩操作，使用结构化LLM对被遗忘的事件进行摘要。

        该方法使用函数调用功能强制LLM生成结构化的摘要，
        确保输出格式的一致性和可解析性。

        Args:
            view (View): 需要压缩的事件View

        Returns:
            Condensation: 包含压缩操作和结构化摘要信息的Condensation对象
        """
        # 获取头部保留的事件
        head = view[: self.keep_first]

        # 计算目标大小（压缩后的大小）
        target_size = self.max_size // 2

        # 计算从尾部保留的事件数量
        events_from_tail = target_size - len(head) - 1

        # 获取或创建摘要事件
        summary_event = (
            view[self.keep_first]
            if isinstance(view[self.keep_first], AgentCondensationObservation)
            else AgentCondensationObservation('No events summarized')
        )

        # 识别需要被遗忘的事件
        forgotten_events = []
        for event in view[self.keep_first: -events_from_tail]:
            if not isinstance(event, AgentCondensationObservation):
                forgotten_events.append(event)

        # 构建结构化生成的提示词
        prompt = """You are maintaining a context-aware state summary for an interactive software agent. This summary is critical because it:
1. Preserves essential context when conversation history grows too large
2. Prevents lost work when the session length exceeds token limits
3. Helps maintain continuity across multiple interactions

You will be given:
- A list of events (actions taken by the agent)
- The most recent previous summary (if one exists)

Capture all relevant information, especially:
- User requirements that were explicitly stated
- Work that has been completed
- Tasks that remain pending
- Current state of code, variables, and data structures
- The status of any version control operations"""

        prompt += '\n\n'

        # 添加之前的摘要（如果存在）
        summary_event_content = self._truncate(
            summary_event.message if summary_event.message else ''
        )
        prompt += f'<PREVIOUS SUMMARY>\n{summary_event_content}\n</PREVIOUS SUMMARY>\n'

        prompt += '\n\n'

        # 添加所有被遗忘的事件
        for forgotten_event in forgotten_events:
            event_content = self._truncate(str(forgotten_event))
            prompt += f'<EVENT id={forgotten_event.id}>\n{event_content}\n</EVENT>\n'

        # 构建消息对象
        messages = [Message(role='user', content=[TextContent(text=prompt)])]

        # 调用LLM进行结构化生成
        response = self.llm.completion(
            messages=self.llm.format_messages_for_llm(messages),
            tools=[StateSummary.tool_description()],  # 提供工具描述以强制结构化输出
            tool_choice={
                'type': 'function',
                'function': {'name': 'create_state_summary'},
            },
        )

        try:
            # 提取包含工具调用的消息
            message = response.choices[0].message

            # 检查是否存在工具调用
            if not hasattr(message, 'tool_calls') or not message.tool_calls:
                raise ValueError('No tool calls found in response')

            # 查找create_state_summary工具调用
            summary_tool_call = None
            for tool_call in message.tool_calls:
                if tool_call.function.name == 'create_state_summary':
                    summary_tool_call = tool_call
                    break

            if not summary_tool_call:
                raise ValueError('create_state_summary tool call not found')

            # 解析参数
            args_json = summary_tool_call.function.arguments
            args_dict = json.loads(args_json)

            # 创建StateSummary对象
            summary = StateSummary.model_validate(args_dict)

        except (ValueError, AttributeError, KeyError, json.JSONDecodeError) as e:
            # 如果解析失败，记录警告并使用空摘要
            logger.warning(
                f'Failed to parse summary tool call: {e}. Using empty summary.'
            )
            summary = StateSummary()

        # 记录响应和性能指标
        self.add_metadata('response', response.model_dump())
        self.add_metadata('metrics', self.llm.metrics.get())

        # 返回压缩操作对象
        return Condensation(
            action=CondensationAction(
                forgotten_events_start_id=min(event.id for event in forgotten_events),
                forgotten_events_end_id=max(event.id for event in forgotten_events),
                summary=str(summary),  # 使用__str__方法格式化摘要
                summary_offset=self.keep_first,
            )
        )

    def should_condense(self, view: View) -> bool:
        """
        判断是否应该执行压缩操作。

        Args:
            view (View): 当前的事件View

        Returns:
            bool: 如果事件数量超过max_size则返回True，否则返回False
        """
        return len(view) > self.max_size

    @classmethod
    def from_config(
            cls, config: StructuredSummaryCondenserConfig
    ) -> StructuredSummaryCondenser:
        """
        从配置对象创建StructuredSummaryCondenser实例。

        Args:
            config (StructuredSummaryCondenserConfig): 配置对象

        Returns:
            StructuredSummaryCondenser: 新创建的实例
        """
        # 这个Condenser无法利用提示词缓存功能
        # 如果恰好设置了缓存，我们会为缓存写入付费但永远不会有机会在读取时节省费用
        llm_config = config.llm_config.model_copy()
        llm_config.caching_prompt = False

        return StructuredSummaryCondenser(
            llm=LLM(config=llm_config),
            max_size=config.max_size,
            keep_first=config.keep_first,
            max_event_length=config.max_event_length,
        )


# 注册配置类，使得可以通过配置系统创建该Condenser
StructuredSummaryCondenser.register_config(StructuredSummaryCondenserConfig)
