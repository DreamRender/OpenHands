from openhands.controller.agent import Agent
from openhands.controller.state.control_flags import (
    BudgetControlFlag,
    IterationControlFlag,
)
from openhands.controller.state.state import State
from openhands.core.logger import openhands_logger as logger
from openhands.events.action.agent import AgentDelegateAction, ChangeAgentStateAction
from openhands.events.action.empty import NullAction
from openhands.events.event import Event
from openhands.events.event_filter import EventFilter
from openhands.events.observation.agent import AgentStateChangedObservation
from openhands.events.observation.delegate import AgentDelegateObservation
from openhands.events.observation.empty import NullObservation
from openhands.events.serialization.event import event_to_trajectory
from openhands.events.stream import EventStream
from openhands.llm.metrics import Metrics
from openhands.storage.files import FileStore


class StateTracker:
    """管理和同步Agent在其生命周期中的状态。
    
    StateTracker负责以下功能：
    1. 在会话间维护Agent状态的持久化
    2. 通过过滤和跟踪相关事件来管理Agent历史（之前由Agent Controller完成）
    3. 在Controller和LLM组件之间同步指标
    4. 更新预算和迭代限制的控制标志
    
    Attributes:
        sid (str | None): 会话ID
        file_store (FileStore | None): 文件存储实例
        user_id (str | None): 用户ID
        agent_history_filter (EventFilter): Agent历史事件过滤器
    """

    def __init__(
        self, sid: str | None, file_store: FileStore | None, user_id: str | None
    ):
        """初始化StateTracker实例。
        
        Args:
            sid (str | None): 会话ID，可为None
            file_store (FileStore | None): 文件存储实例，可为None
            user_id (str | None): 用户ID，可为None
        """
        self.sid = sid
        self.file_store = file_store
        self.user_id = user_id

        # 过滤掉与Agent无关的事件
        # 这样它们就不会被包含在Agent历史中
        self.agent_history_filter = EventFilter(
            exclude_types=(
                NullAction,                    # 空操作
                NullObservation,              # 空观察
                ChangeAgentStateAction,       # 改变Agent状态操作
                AgentStateChangedObservation, # Agent状态改变观察
            ),
            exclude_hidden=True,  # 排除隐藏事件
        )

    def set_initial_state(
        self,
        id: str,
        agent: Agent,
        state: State | None,
        max_iterations: int,
        max_budget_per_task: float | None,
        confirmation_mode: bool = False,
    ) -> None:
        """为Agent设置初始状态，可以从之前的会话、父Agent或创建新状态。
        
        Args:
            id (str): Agent ID
            agent (Agent): Agent实例
            state (State | None): 要初始化的状态，None表示创建新状态
            max_iterations (int): 任务允许的最大迭代次数
            max_budget_per_task (float | None): 每个任务的最大预算，可为None
            confirmation_mode (bool): 是否启用确认模式，默认False
        """
        # 状态可以来自：
        # - 之前的会话，此时它有历史记录
        # - 父Agent，此时它没有历史记录
        # - None / 新状态

        # 如果状态为None，我们创建一个全新的状态，仍然加载事件流以便恢复历史
        if state is None:
            self.state = State(
                session_id=id.removesuffix('-delegate'),  # 移除委托后缀
                inputs={},
                iteration_flag=IterationControlFlag(
                    limit_increase_amount=max_iterations,
                    current_value=0,
                    max_value=max_iterations,
                ),
                budget_flag=None
                if not max_budget_per_task
                else BudgetControlFlag(
                    limit_increase_amount=max_budget_per_task,
                    current_value=0,
                    max_value=max_budget_per_task,
                ),
                confirmation_mode=confirmation_mode,
            )
            self.state.start_id = 0

            logger.info(
                f'AgentController {id} - created new state. start_id: {self.state.start_id}'
            )
        else:
            self.state = state
            # 确保start_id有效
            if self.state.start_id <= -1:
                self.state.start_id = 0

            logger.info(
                f'AgentController {id} initializing history from event {self.state.start_id}',
            )

        # 与Agent的LLM指标共享状态指标
        # 这确保所有累积的指标始终在Controller和LLM之间保持同步
        agent.llm.metrics = self.state.metrics

    def _init_history(self, event_stream: EventStream) -> None:
        """从事件流初始化Agent的历史记录。
        
        历史记录是一个事件列表，满足以下条件：
        - 排除self.filter_out中列出的事件类型
        - 排除具有hidden=True属性的事件
        - 对于委托事件（在AgentDelegateAction和AgentDelegateObservation之间）：
            - 排除操作和观察之间的所有事件
            - 包含委托操作和观察本身
            
        Args:
            event_stream (EventStream): 事件流实例
        """
        # 定义要获取的事件范围
        # 委托以start_id开始，最初不会找到任何事件
        # 否则我们正在恢复之前的会话
        start_id = self.state.start_id if self.state.start_id >= 0 else 0
        end_id = (
            self.state.end_id
            if self.state.end_id >= 0
            else event_stream.get_latest_event_id()
        )

        # 合理性检查
        if start_id > end_id + 1:
            logger.warning(
                f'start_id {start_id} is greater than end_id + 1 ({end_id + 1}). History will be empty.',
            )
            self.state.history = []
            return

        events: list[Event] = []

        # 获取剩余的历史记录
        events_to_add = list(
            event_stream.search_events(
                start_id=start_id,
                end_id=end_id,
                reverse=False,
                filter=self.agent_history_filter,
            )
        )
        events.extend(events_to_add)

        # 查找所有委托操作/观察对
        delegate_ranges: list[tuple[int, int]] = []
        delegate_action_ids: list[int] = []  # 未匹配的委托操作ID栈

        for event in events:
            if isinstance(event, AgentDelegateAction):
                # 将委托操作ID压入栈
                delegate_action_ids.append(event.id)
                # 注意：如果将来需要跟踪，可以获取agent=event.agent和task=event.inputs.get('task','')

            elif isinstance(event, AgentDelegateObservation):
                # 与最近的未匹配委托操作匹配
                if not delegate_action_ids:
                    logger.warning(
                        f'Found AgentDelegateObservation without matching action at id={event.id}',
                    )
                    continue

                # 弹出最近的委托操作ID
                action_id = delegate_action_ids.pop()
                delegate_ranges.append((action_id, event.id))

        # 过滤掉委托操作/观察对之间的事件
        if delegate_ranges:
            filtered_events: list[Event] = []
            current_idx = 0

            for start_id, end_id in sorted(delegate_ranges):
                # 添加委托范围之前的事件
                filtered_events.extend(
                    event for event in events[current_idx:] if event.id < start_id
                )

                # 添加委托操作和观察
                filtered_events.extend(
                    event for event in events if event.id in (start_id, end_id)
                )

                # 更新索引到委托范围之后
                current_idx = next(
                    (i for i, e in enumerate(events) if e.id > end_id), len(events)
                )

            # 添加最后一个委托范围之后的剩余事件
            filtered_events.extend(events[current_idx:])

            self.state.history = filtered_events
        else:
            self.state.history = events

        # 确保历史记录同步
        self.state.start_id = start_id

    def close(self, event_stream: EventStream):
        """关闭StateTracker并完成历史记录。
        
        最终的state.history将被外部脚本（如评估、测试等）使用。
        历史记录需要包含委托事件，但不包括：
        - 'hidden'事件，具有hidden=True的事件
        - 后端事件（默认的'过滤掉'类型，self.filter_out中的类型）
        
        Args:
            event_stream (EventStream): 事件流实例
        """
        # 我们创建了历史，现在是重写它的时候了！
        start_id = self.state.start_id if self.state.start_id >= 0 else 0
        end_id = (
            self.state.end_id
            if self.state.end_id >= 0
            else event_stream.get_latest_event_id()
        )

        # 重新构建完整的历史记录
        self.state.history = list(
            event_stream.search_events(
                start_id=start_id,
                end_id=end_id,
                reverse=False,
                filter=self.agent_history_filter,
            )
        )

    def add_history(self, event: Event):
        """将事件添加到历史记录中。
        
        只有通过过滤器的事件才会被添加到历史记录中。
        
        Args:
            event (Event): 要添加的事件
        """
        # 如果事件没有被过滤掉，将其添加到历史记录
        if self.agent_history_filter.include(event):
            self.state.history.append(event)

    def get_trajectory(self, include_screenshots: bool = False) -> list[dict]:
        """获取轨迹数据。
        
        将历史记录中的事件转换为轨迹格式，用于分析和可视化。
        
        Args:
            include_screenshots (bool): 是否包含截图，默认False
            
        Returns:
            list[dict]: 轨迹数据列表
        """
        return [
            event_to_trajectory(event, include_screenshots)
            for event in self.state.history
        ]

    def maybe_increase_control_flags_limits(self, headless_mode: bool):
        """可能增加控制标志的限制。
        
        迭代和预算扩展是相互独立的。
        如果任何一个控制标志达到或超过其限制，将抛出错误。
        
        Args:
            headless_mode (bool): 是否处于无头模式
        """
        # 尝试增加迭代限制
        self.state.iteration_flag.increase_limit(headless_mode)
        # 如果存在预算标志，尝试增加预算限制
        if self.state.budget_flag:
            self.state.budget_flag.increase_limit(headless_mode)

    def get_metrics_snapshot(self):
        """获取指标快照。
        
        深度复制指标对象，作为创建委托时父级指标的快照。
        这将被存储并用于计算委托的本地指标
        （因为委托现在从其父级停止的地方开始累积指标）。
        
        Returns:
            Metrics: 指标的深度副本
        """
        return self.state.metrics.copy()

    def save_state(self):
        """将当前状态保存到持久存储。
        
        如果存在会话ID和文件存储，将状态保存到session。
        """
        if self.sid and self.file_store:
            self.state.save_to_session(self.sid, self.file_store, self.user_id)

    def run_control_flags(self):
        """执行控制标志的一步操作。
        
        调用迭代和预算控制标志的step方法，检查是否达到限制。
        如果达到限制，相应的控制标志会抛出异常。
        """
        # 执行迭代控制标志的步骤
        self.state.iteration_flag.step()
        # 如果存在预算控制标志，执行其步骤
        if self.state.budget_flag:
            self.state.budget_flag.step()

    def sync_budget_flag_with_metrics(self):
        """确保预算标志与LLM完成的累积成本保持最新。
        
        预算标志将监控预算何时超出限制。
        将指标中的累积成本同步到预算标志的当前值。
        """
        if self.state.budget_flag:
            # 将累积成本同步到预算标志
            self.state.budget_flag.current_value = self.state.metrics.accumulated_cost

    def merge_metrics(self, metrics: Metrics):
        """将指标与状态指标合并。
        
        注意：这在未来应该重构。我们应该让服务（草稿LLM、标题自动完成、Condenser等）
        使用它们自己的LLM，但指标对象应该共享。这样我们对所有服务的累积成本有一个真实来源。
        
        这将防止指标存储的碎片化，如果我们决定引入更多需要LLM完成的专门服务时，
        我们就不必决定在哪里以及如何存储它们。
        
        Args:
            metrics (Metrics): 要合并的指标对象
        """
        # 合并指标到状态指标中
        self.state.metrics.merge(metrics)
        # 如果存在预算标志，更新其当前值
        if self.state.budget_flag:
            self.state.budget_flag.current_value = self.state.metrics.accumulated_cost
