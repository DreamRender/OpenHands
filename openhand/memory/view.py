from __future__ import annotations

from typing import overload

from pydantic import BaseModel

from openhands.core.logger import openhands_logger as logger
from openhands.events import Event
from openhands.events.action.agent import CondensationAction, CondensationRequestAction
from openhands.events.observation import AgentCondensationObservation


class View(BaseModel):
    """
    事件的线性视图。

    由压缩器生成，将事件按照线性顺序进行排列，经过压缩器处理后，这些事件就可以作为LLM的输入数据了。
    """

    events: list[Event]
    """
    事件列表

    事件（Event）是Openhands系统中的核心类，用于封装agent在操作过程中发生的各种事件信息
    包含：
    id（int）：每个事件的唯一标识符
    timestamp（str|None）：事件发生的时间，以ISO格式表示
    source（EventSource|None）：事件的来源（AGENT、USER、ENVIRONMENT）
    case（int|None）：触发此事件的其他事件的ID
    message（str|None）：事件附带的可读性的信息
    timeout（float|None）：事件的超时时间，单位为秒
    llm_metrics（Metrics|None）：事件相关的LLM指标信息，包含成本和token使用情况
    tool_call_metadata（ToolCallMetadata|None）：事件相关的工具调用元数据，包含function_name，tool_call_id，model_response，total_calls_in_response
    response_id（str|None）：来自LLM的响应的ID，用于将Event与特定的LLM响应关联起来
    """
    unhandled_condensation_request: bool = False
    """
    通知压缩器有一个待处理的压缩请求，需要对当前的EventList进行压缩

    在此之前我们需要知道两个Action：
    1. CondensationRequestAction：压缩请求Action，用于通知压缩器有一个待处理的压缩请求
    2. CondensationAction：这是一个压缩完成的记录。它代表系统已经成功完成了一次历史记录的压缩和整理工作

    作用流程：
    1. 请求压缩：当Agent的Context Window达到了上线，或者代理主动决定需要整理历史记录时，系统会创建一个CondensationRequestAction事件
    2. 设置Flag：View.from_events这个静态方法在处理事件列表的时候，会检查事件列表中是否有CondensationRequestAction事件。
        如果在整个event列表中，CondensationRequestAction事件出现在CondensationAction事件之前，那么就说明这个压缩请求已经被处理了。
        但如果有一个CondensationRequestAction事件出现在CondensationAction事件之后，那么就说明这个压缩请求还没有被处理，需要对当前的时间历史进行压缩。
    3. 触发压缩：有且只有ConversationWindowCondenser.should_condense会直接检查View.unhandled_condensation_request这个标志位，如果为True，就说明需要对当前的时间历史进行压缩
    """

    def __len__(self) -> int:
        """
        返回EventList的长度
        """
        return len(self.events)

    def __iter__(self):
        """
        返回EventList的迭代器
        """
        return iter(self.events)

    @overload
    def __getitem__(self, key: slice) -> list[Event]:
        """
        告诉类型检查器当 key 是 slice 时，返回 list[Event]

        @overload + ... 是Python的类型提示语法，用于：
        1. 声明不同参数类型对应的返回类型
        2. 帮助类型检查器进行精确的类型推断
        3. 不包含实际执行代码（实际代码在最后一个同名函数中）
        """
        ...

    @overload
    def __getitem__(self, key: int) -> Event:
        """
        告诉类型检查器当 key 是 int 时，返回 Event
        """
        ...

    def __getitem__(self, key: int | slice) -> Event | list[Event]:
        """
        实现索引操作，支持整数和切片两种方式

        Args:
            key: 索引值，可以是整数或切片对象

        Returns:
            如果是整数索引，返回对应的Event对象
            如果是切片索引，返回对应的Event列表
        """
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            return [self[i] for i in range(start, stop, step)]
        elif isinstance(key, int):
            return self.events[key]
        else:
            raise ValueError(f'Invalid key type: {type(key)}')

    @staticmethod
    def from_events(events: list[Event]) -> View:
        forgotten_event_ids: set[int] = set()

        for event in events:
            if isinstance(event, CondensationAction):
                # 添加被压缩的原始事件
                forgotten_event_ids.update(event.forgotten)
                # 添加压缩事件本身
                forgotten_event_ids.add(event.id)
            if isinstance(event, CondensationRequestAction):
                # 添加压缩请求事件
                forgotten_event_ids.add(event.id)

        # 计算得出需要被保留的事件
        kept_events = [event for event in events if event.id not in forgotten_event_ids]

        # 摘要信息
        summary: str | None = None
        # 插入摘要信息的位置
        summary_offset: int | None = None

        # 计算反向的event list
        # 因为我们需要查找的摘要总是在最后一个压缩事件中
        reversed_events = reversed(events)

        for event in reversed_events:
            if isinstance(event, CondensationAction):
                if event.summary is not None and event.summary_offset is not None:
                    # 根据压缩事件，设置摘要信息和插入位置
                    summary = event.summary
                    # CondensationAction的summary_offset确保了无论压缩掉多少中间事件，新生成的摘要总能被准确地安插在初始事件之后
                    # 以LLMSummarizingCondenser为例，它在初始化时有一个参数keep_first，代表“总是在历史记录的开头保留前几个事件，永远不要压缩它们”
                    # 当这个压缩器生成CondensationAction的时候，他会将summary_offset设置为keep_first
                    summary_offset = event.summary_offset
                    break

        if summary is not None and summary_offset is not None:
            logger.info(f'Inserting summary at offset {summary_offset}')

            # 将需要被替换的内容的位置修改为AgentCondensationObservation
            kept_events.insert(summary_offset, AgentCondensationObservation(content=summary))

        # 检查是否有未处理的压缩请求
        unhandled_condensation_request = False
        # 这个逻辑是，从最后一个开始检查，如果先遇到了CondensationAction，那么就说明所有的CondensationRequestAction都被处理了
        # 如果先遇到了CondensationRequestAction，那么就说明在event list里，存在压缩请求没有被处理
        for event in reversed_events:
            if isinstance(event, CondensationAction):
                break
            if isinstance(event, CondensationRequestAction):
                unhandled_condensation_request = True
                break

        return View(
            events=kept_events,
            unhandled_condensation_request=unhandled_condensation_request,
        )
