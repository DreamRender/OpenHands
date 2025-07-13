from openhands.controller.state.state import State
from openhands.core.logger import openhands_logger as logger
from openhands.events.action.action import Action
from openhands.events.action.commands import IPythonRunCellAction
from openhands.events.action.empty import NullAction
from openhands.events.action.message import MessageAction
from openhands.events.event import Event, EventSource
from openhands.events.observation import (
    CmdOutputObservation,
    IPythonRunCellObservation,
)
from openhands.events.observation.agent import AgentCondensationObservation
from openhands.events.observation.empty import NullObservation
from openhands.events.observation.error import ErrorObservation
from openhands.events.observation.observation import Observation


class StuckDetector:
    """卡死检测器，用于检测Agent是否陷入循环状态。
    
    该类实现了多种循环检测算法，能够识别Agent在执行过程中可能出现的各种卡死情况，
    包括重复的Action-Observation模式、错误循环、独白循环等。
    
    检测的主要场景包括：
    1. 相同Action和相同Observation的重复循环
    2. 相同Action导致持续错误的循环
    3. Agent自言自语的独白循环
    4. 交替Action-Observation模式的循环
    5. 上下文窗口错误导致的循环
    
    Attributes:
        SYNTAX_ERROR_MESSAGES (list): 用于检测的语法错误消息模式列表
        state (State): 当前的执行状态
    """
    
    # 用于检测语法错误循环的错误消息模式
    SYNTAX_ERROR_MESSAGES = [
        'SyntaxError: unterminated string literal (detected at line',
        'SyntaxError: invalid syntax. Perhaps you forgot a comma?',
        'SyntaxError: incomplete input',
    ]

    def __init__(self, state: State):
        """初始化卡死检测器。
        
        Args:
            state (State): 当前的执行状态，包含事件历史记录
        """
        self.state = state

    def is_stuck(self, headless_mode: bool = True) -> bool:
        """检查Agent是否陷入循环。

        Args:
            headless_mode (bool): 匹配AgentController的headless_mode参数
                                True: 考虑所有历史记录（自动化/测试模式）
                                False: 只考虑最后一条用户消息后的历史记录（交互模式）

        Returns:
            bool: 如果Agent陷入循环则返回True，否则返回False
        """
        if not headless_mode:
            # 在交互模式下，只查看最后一条用户消息之后的历史记录
            last_user_msg_idx = -1
            
            # 从后往前查找最后一条用户消息
            for i, event in enumerate(reversed(self.state.history)):
                if (
                    isinstance(event, MessageAction)
                    and event.source == EventSource.USER
                ):
                    last_user_msg_idx = len(self.state.history) - i - 1
                    break

            # 只检查用户消息之后的历史记录
            history_to_check = self.state.history[last_user_msg_idx + 1 :]
        else:
            # 在无头模式下，查看所有历史记录
            history_to_check = self.state.history

        # 过滤掉用户消息和空事件
        filtered_history = [
            event
            for event in history_to_check
            if not (
                # 过滤器在两种模式下都能优雅工作：
                # - 在headless模式下：主动从完整历史中过滤掉用户消息
                # - 在非headless模式下：由于我们已经在最后一条用户消息后切片，所以这是无操作
                (isinstance(event, MessageAction) and event.source == EventSource.USER)
                # 目前历史记录中可能存在一些NullAction或NullObservation
                or isinstance(event, (NullAction, NullObservation))
            )
        ]

        # 检测循环至少需要3个Action，否则无需继续
        if len(filtered_history) < 3:
            return False

        # 前几个场景检测3或4个重复步骤
        # 准备最后4个Action和Observation以进行检查
        last_actions: list[Event] = []
        last_observations: list[Event] = []

        # 从历史记录末尾开始检索最后四个Action和Observation
        for event in reversed(filtered_history):
            if isinstance(event, Action) and len(last_actions) < 4:
                last_actions.append(event)
            elif isinstance(event, Observation) and len(last_observations) < 4:
                last_observations.append(event)

            # 如果已收集足够的Action和Observation，停止搜索
            if len(last_actions) == 4 and len(last_observations) == 4:
                break

        # 场景1：相同Action，相同Observation
        if self._is_stuck_repeating_action_observation(last_actions, last_observations):
            return True

        # 场景2：相同Action，错误结果
        if self._is_stuck_repeating_action_error(last_actions, last_observations):
            return True

        # 场景3：独白（Agent自言自语）
        if self._is_stuck_monologue(filtered_history):
            return True

        # 场景4：最后六步的Action、Observation模式
        if len(filtered_history) >= 6:
            if self._is_stuck_action_observation_pattern(filtered_history):
                return True

        # 场景5：上下文窗口错误循环
        if len(filtered_history) >= 10:
            if self._is_stuck_context_window_error(filtered_history):
                return True

        return False

    def _is_stuck_repeating_action_observation(
        self, last_actions: list[Event], last_observations: list[Event]
    ) -> bool:
        """检测场景1：相同Action，相同Observation的重复循环。
        
        这种情况发生在Agent重复执行相同的Action并得到相同的Observation时。
        需要4个Action和4个Observation来检测循环。
        
        Args:
            last_actions (list[Event]): 最近的Action列表
            last_observations (list[Event]): 最近的Observation列表
            
        Returns:
            bool: 如果检测到重复的Action-Observation循环则返回True
        """
        # 检查是否有4个相同的Action-Observation对的循环
        if len(last_actions) == 4 and len(last_observations) == 4:
            # 检查所有Action是否相同（忽略进程ID）
            actions_equal = all(
                self._eq_no_pid(last_actions[0], action) for action in last_actions
            )
            # 检查所有Observation是否相同（忽略进程ID）
            observations_equal = all(
                self._eq_no_pid(last_observations[0], observation)
                for observation in last_observations
            )

            if actions_equal and observations_equal:
                logger.warning('Action, Observation loop detected')
                return True

        return False

    def _is_stuck_repeating_action_error(
        self, last_actions: list[Event], last_observations: list[Event]
    ) -> bool:
        """检测场景2：相同Action导致持续错误的循环。
        
        这种情况发生在Agent重复执行相同的Action但持续得到错误结果时。
        需要3个Action和3个Observation来检测循环。
        
        Args:
            last_actions (list[Event]): 最近的Action列表
            last_observations (list[Event]): 最近的Observation列表
            
        Returns:
            bool: 如果检测到Action错误循环则返回True
        """
        if len(last_actions) < 3 or len(last_observations) < 3:
            return False

        # 检查最后三个Action是否"相同"
        if all(self._eq_no_pid(last_actions[0], action) for action in last_actions[:3]):
            # 检查最后三个Observation是否都是错误
            if all(isinstance(obs, ErrorObservation) for obs in last_observations[:3]):
                logger.warning('Action, ErrorObservation loop detected')
                return True
            # 或者，检查最后三个Observation是否都是带有语法错误的IPythonRunCellObservation
            elif all(
                isinstance(obs, IPythonRunCellObservation)
                for obs in last_observations[:3]
            ):
                warning = 'Action, IPythonRunCellObservation loop detected'
                
                # 检查各种语法错误消息模式
                for error_message in self.SYNTAX_ERROR_MESSAGES:
                    if error_message.startswith(
                        'SyntaxError: unterminated string literal (detected at line'
                    ):
                        # 检查行号一致的错误
                        if self._check_for_consistent_line_error(
                            [
                                obs
                                for obs in last_observations[:3]
                                if isinstance(obs, IPythonRunCellObservation)
                            ],
                            error_message,
                        ):
                            logger.warning(warning)
                            return True
                    elif error_message in (
                        'SyntaxError: invalid syntax. Perhaps you forgot a comma?',
                        'SyntaxError: incomplete input',
                    ) and self._check_for_consistent_invalid_syntax(
                        [
                            obs
                            for obs in last_observations[:3]
                            if isinstance(obs, IPythonRunCellObservation)
                        ],
                        error_message,
                    ):
                        logger.warning(warning)
                        return True
        return False

    def _check_for_consistent_invalid_syntax(
        self, observations: list[IPythonRunCellObservation], error_message: str
    ) -> bool:
        """检查一致的无效语法错误。
        
        验证多个IPythonRunCellObservation是否包含相同的语法错误信息。
        
        Args:
            observations (list[IPythonRunCellObservation]): 要检查的Observation列表
            error_message (str): 要查找的错误消息模式
            
        Returns:
            bool: 如果找到一致的语法错误则返回True
        """
        first_lines = []
        valid_observations = []

        # 检查每个Observation
        for obs in observations:
            content = obs.content
            lines = content.strip().split('\n')

            # 真正的语法错误至少有6行
            if len(lines) < 6:
                return False

            line1 = lines[0].strip()
            # 第一行应该以'Cell In[1], line'开头
            if not line1.startswith('Cell In[1], line'):
                return False

            first_lines.append(line1)  # 存储每个Observation的第一行

            # 检查最后三行的格式
            if (
                lines[-1].startswith('[Jupyter Python interpreter:')
                and lines[-2].startswith('[Jupyter current working directory:')
                and error_message in lines[-3]
            ):
                valid_observations.append(obs)

        # 检查是否：
        # 1. 所有第一行都相同
        # 2. 我们恰好有3个有效的Observation
        # 3. 所有有效Observation中的错误消息行都相同
        return (
            len(set(first_lines)) == 1
            and len(valid_observations) == 3
            and len(
                set(
                    obs.content.strip().split('\n')[:-2][-1]
                    for obs in valid_observations
                )
            )
            == 1
        )

    def _check_for_consistent_line_error(
        self, observations: list[IPythonRunCellObservation], error_message: str
    ) -> bool:
        """检查一致的行错误。
        
        验证多个IPythonRunCellObservation是否在相同行号上包含相同的错误。
        
        Args:
            observations (list[IPythonRunCellObservation]): 要检查的Observation列表
            error_message (str): 要查找的错误消息模式
            
        Returns:
            bool: 如果找到一致的行错误则返回True
        """
        error_lines = []

        # 检查每个Observation
        for obs in observations:
            content = obs.content
            lines = content.strip().split('\n')

            if len(lines) < 3:
                return False

            last_lines = lines[-3:]

            # 检查最后两行是否是我们自己的标准格式
            if not (
                last_lines[-2].startswith('[Jupyter current working directory:')
                and last_lines[-1].startswith('[Jupyter Python interpreter:')
            ):
                return False

            # 在倒数第三行中检查错误消息
            if error_message in last_lines[-3]:
                error_lines.append(last_lines[-3])

        # 检查是否在所有3个Observation中都找到了错误消息
        # 且倒数第三行在所有出现中都相同
        return len(error_lines) == 3 and len(set(error_lines)) == 1

    def _is_stuck_monologue(self, filtered_history: list[Event]) -> bool:
        """检测场景3：独白循环。
        
        检查Agent是否在重复发送相同的MessageAction（source=AGENT），
        即Agent在告诉自己同样的话。
        
        Args:
            filtered_history (list[Event]): 过滤后的历史事件列表
            
        Returns:
            bool: 如果检测到独白循环则返回True
        """
        # 查找所有来源为AGENT的MessageAction
        agent_message_actions = [
            (i, event)
            for i, event in enumerate(filtered_history)
            if isinstance(event, MessageAction) and event.source == EventSource.AGENT
        ]

        # 最后三个消息Action足以进行此检查
        if len(agent_message_actions) >= 3:
            last_agent_message_actions = agent_message_actions[-3:]

            # 检查是否都是相同的消息
            if all(
                (last_agent_message_actions[0][1] == action[1])
                for action in last_agent_message_actions
            ):
                # 检查重复MessageAction之间是否有任何Observation
                # 如果有，那么还不是循环，也许可以恢复
                start_index = last_agent_message_actions[0][0]
                end_index = last_agent_message_actions[-1][0]

                has_observation_between = False
                for event in filtered_history[start_index + 1 : end_index]:
                    if isinstance(event, Observation):
                        has_observation_between = True
                        break

                if not has_observation_between:
                    logger.warning('Repeated MessageAction with source=AGENT detected')
                    return True
        return False

    def _is_stuck_action_observation_pattern(
        self, filtered_history: list[Event]
    ) -> bool:
        """检测场景4：最后六步中的Action、Observation模式循环。
        
        检查Agent是否在最后六步中每隔一步重复相同的(Action, Observation)。
        
        Args:
            filtered_history (list[Event]): 过滤后的历史事件列表
            
        Returns:
            bool: 如果检测到Action-Observation模式循环则返回True
        """
        last_six_actions: list[Event] = []
        last_six_observations: list[Event] = []

        # 历史记录的末尾最有趣
        for event in reversed(filtered_history):
            if isinstance(event, Action) and len(last_six_actions) < 6:
                last_six_actions.append(event)
            elif isinstance(event, Observation) and len(last_six_observations) < 6:
                last_six_observations.append(event)

            if len(last_six_actions) == 6 and len(last_six_observations) == 6:
                break

        # 这种模式是每隔一步，如：
        # (action_1, obs_1), (action_2, obs_2), (action_1, obs_1), (action_2, obs_2),...
        if len(last_six_actions) == 6 and len(last_six_observations) == 6:
            actions_equal = (
                # action_0 == action_2 == action_4
                self._eq_no_pid(last_six_actions[0], last_six_actions[2])
                and self._eq_no_pid(last_six_actions[0], last_six_actions[4])
                # action_1 == action_3 == action_5
                and self._eq_no_pid(last_six_actions[1], last_six_actions[3])
                and self._eq_no_pid(last_six_actions[1], last_six_actions[5])
            )
            observations_equal = (
                # obs_0 == obs_2 == obs_4
                self._eq_no_pid(last_six_observations[0], last_six_observations[2])
                and self._eq_no_pid(last_six_observations[0], last_six_observations[4])
                # obs_1 == obs_3 == obs_5
                and self._eq_no_pid(last_six_observations[1], last_six_observations[3])
                and self._eq_no_pid(last_six_observations[1], last_six_observations[5])
            )

            if actions_equal and observations_equal:
                logger.warning('Action, Observation pattern detected')
                return True
        return False

    def _is_stuck_context_window_error(self, filtered_history: list[Event]) -> bool:
        """检测是否陷入上下文窗口错误循环。

        当我们反复遇到上下文窗口错误并尝试修剪时就会发生这种情况，
        但修剪不起作用，导致我们遇到更多上下文窗口错误。
        模式是重复的AgentCondensationObservation事件，它们之间没有其他事件。

        Args:
            filtered_history (list[Event]): 要检查的过滤事件列表

        Returns:
            bool: 如果检测到上下文窗口错误循环则返回True
        """
        # 查找AgentCondensationObservation事件
        condensation_events = [
            (i, event)
            for i, event in enumerate(filtered_history)
            if isinstance(event, AgentCondensationObservation)
        ]

        # 至少需要10个压缩事件才能检测循环
        if len(condensation_events) < 10:
            return False

        # 获取最后10个压缩事件
        last_condensation_events = condensation_events[-10:]

        # 检查它们之间是否有任何非压缩事件
        for i in range(len(last_condensation_events) - 1):
            start_idx = last_condensation_events[i][0]
            end_idx = last_condensation_events[i + 1][0]

            # 在这两个压缩事件之间查找任何非压缩事件
            has_other_events = False
            for event in filtered_history[start_idx + 1 : end_idx]:
                if not isinstance(event, AgentCondensationObservation):
                    has_other_events = True
                    break

            if not has_other_events:
                logger.warning(
                    'Context window error loop detected - repeated condensation events'
                )
                return True

        return False

    def _eq_no_pid(self, obj1: Event, obj2: Event) -> bool:
        """比较两个事件是否相等，忽略进程ID等可变字段。
        
        这个方法用于循环检测中的事件比较，会忽略一些在重复执行中
        可能发生变化但不影响本质相等性的字段（如进程ID）。
        
        Args:
            obj1 (Event): 第一个要比较的事件
            obj2 (Event): 第二个要比较的事件
            
        Returns:
            bool: 如果两个事件在忽略PID后相等则返回True
        """
        if isinstance(obj1, IPythonRunCellAction) and isinstance(
            obj2, IPythonRunCellAction
        ):
            # 对于编辑Action的循环检测，忽略思考过程，比较一些代码
            # 代码应该至少有3行，以避免简单的单行代码
            if (
                'edit_file_by_replace(' in obj1.code
                and 'edit_file_by_replace(' in obj2.code
            ):
                return (
                    len(obj1.code.split('\n')) > 2
                    and obj1.code.split('\n')[:3] == obj2.code.split('\n')[:3]
                )
            else:
                # 默认比较
                return obj1 == obj2
        elif isinstance(obj1, CmdOutputObservation) and isinstance(
            obj2, CmdOutputObservation
        ):
            # 对于循环检测，忽略command_id（即进程ID）
            return obj1.command == obj2.command and obj1.exit_code == obj2.exit_code
        else:
            # 这是默认比较
            return obj1 == obj2