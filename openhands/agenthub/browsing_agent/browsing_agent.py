import os

from browsergym.core.action.highlevel import HighLevelActionSet
from browsergym.utils.obs import flatten_axtree_to_str

from openhands.agenthub.browsing_agent.response_parser import BrowsingResponseParser
from openhands.controller.agent import Agent
from openhands.controller.state.state import State
from openhands.core.config import AgentConfig
from openhands.core.logger import openhands_logger as logger
from openhands.core.message import Message, TextContent
from openhands.events.action import (
    Action,
    AgentFinishAction,
    BrowseInteractiveAction,
    MessageAction,
)
from openhands.events.event import EventSource
from openhands.events.observation import BrowserOutputObservation
from openhands.events.observation.observation import Observation
from openhands.llm.llm import LLM
from openhands.runtime.plugins import (
    PluginRequirement,
)

# 全局变量：是否启用导航功能，仅在运行 webarena 和 miniwob benchmarks 时禁用 NAV actions
USE_NAV = (
    os.environ.get('USE_NAV', 'true') == 'true'
)

# 全局变量：是否仅返回简洁答案，仅在运行 webarena 和 miniwob benchmarks 时启用
USE_CONCISE_ANSWER = (
    os.environ.get('USE_CONCISE_ANSWER', 'false') == 'true'
)

# 全局变量：评估模式标志，当禁用导航功能且只返回简洁答案时，用于 webarena 和 miniwob benchmarks
if not USE_NAV and USE_CONCISE_ANSWER:
    EVAL_MODE = True  # 禁用NAV actions且只返回简洁答案，适用于webarena和miniwob benchmarks
else:
    EVAL_MODE = False


def get_error_prefix(last_browser_action: str) -> str:
    """
    获取错误前缀信息
    
    当上一个浏览器操作失败时，生成错误提示前缀，帮助Agent重新思考当前页面状态
    
    Args:
        last_browser_action (str): 上一个失败的浏览器操作
        
    Returns:
        str: 包含错误信息的提示前缀
    """
    return f'IMPORTANT! Last action is incorrect:\n{last_browser_action}\nThink again with the current observation of the page.\n'


def get_system_message(goal: str, action_space: str) -> str:
    """
    构建系统消息
    
    为Agent生成系统级指令，包含目标说明和可用的操作空间描述
    
    Args:
        goal (str): 要完成的目标任务
        action_space (str): 可用的操作空间描述
        
    Returns:
        str: 格式化的系统消息
    """
    return f"""\
# Instructions
Review the current state of the page and all other information to find the best
possible next action to accomplish your goal. Your answer will be interpreted
and executed by a program, make sure to follow the formatting instructions.

# Goal:
{goal}

# Action Space
{action_space}
"""


# 全局常量：简洁指令模板，用于演示如何向用户提供简洁答案
CONCISE_INSTRUCTION = """\

Here is another example with chain of thought of a valid action when providing a concise answer to user:
"
In order to accomplish my goal I need to send the information asked back to the user. This page list the information of HP Inkjet Fax Machine, which is the product identified in the objective. Its price is $279.49. I will send a message back to user with the answer.
```send_msg_to_user("$279.49")```
"
"""


def get_prompt(
    error_prefix: str, cur_url: str, cur_axtree_txt: str, prev_action_str: str
) -> str:
    """
    构建完整的用户提示信息
    
    生成包含当前页面状态、错误信息和历史操作的完整提示，供Agent决策使用
    
    Args:
        error_prefix (str): 错误前缀信息，如果存在错误
        cur_url (str): 当前页面URL
        cur_axtree_txt (str): 当前页面的可访问性树文本
        prev_action_str (str): 之前执行的操作历史
        
    Returns:
        str: 格式化的完整提示信息
    """
    prompt = f"""\
{error_prefix}

# Current Page URL:
{cur_url}

# Current Accessibility Tree:
{cur_axtree_txt}

# Previous Actions
{prev_action_str}

Here is an example with chain of thought of a valid action when clicking on a button:
"
In order to accomplish my goal I need to click on the button with bid 12
```click("12")```
"
""".strip()
    # 如果启用简洁答案模式，添加简洁指令示例
    if USE_CONCISE_ANSWER:
        prompt += CONCISE_INSTRUCTION
    return prompt


class BrowsingAgent(Agent):
    """
    浏览器交互Agent类
    
    专门设计用于与Web浏览器进行交互的Agent，继承自基础Agent类。
    可以执行网页导航、点击、输入等浏览器操作，并通过可访问性树理解页面结构。
    
    Attributes:
        VERSION (str): Agent版本号
        sandbox_plugins (list[PluginRequirement]): 沙箱插件要求列表
        response_parser (BrowsingResponseParser): 响应解析器实例
        action_space (HighLevelActionSet): 高级操作集合，定义Agent可执行的操作
        error_accumulator (int): 错误累积计数器，用于跟踪连续错误次数
    """
    
    VERSION = '1.0'
    
    # 类属性：沙箱插件要求列表，当前为空
    sandbox_plugins: list[PluginRequirement] = []
    
    # 类属性：响应解析器，用于解析Agent的响应
    response_parser = BrowsingResponseParser()

    def __init__(
        self,
        llm: LLM,
        config: AgentConfig,
    ) -> None:
        """
        初始化BrowsingAgent实例
        
        创建新的浏览器Agent实例，配置LLM和操作空间
        
        Args:
            llm (LLM): Agent使用的大语言模型
            config (AgentConfig): Agent配置对象
        """
        # 调用父类初始化方法
        super().__init__(llm, config)
        
        # 定义可配置的操作空间，包括聊天功能、网页导航和基于可访问性树和HTML的网页定位
        # 详见: https://github.com/ServiceNow/BrowserGym/blob/main/core/src/browsergym/core/action/highlevel.py
        action_subsets = ['chat', 'bid']  # 基础操作子集：聊天和browser id点击
        
        # 根据环境变量决定是否添加导航功能
        if USE_NAV:
            action_subsets.append('nav')
            
        # 初始化高级操作集合
        self.action_space = HighLevelActionSet(
            subsets=action_subsets,
            strict=False,  # 对操作解析采用较宽松的模式
            multiaction=True,  # 允许Agent一次执行多个操作
        )

        # 重置Agent状态
        self.reset()

    def reset(self) -> None:
        """
        重置BrowsingAgent的内部状态
        
        重置Agent特定的计数器，但不重置LLM指标
        """
        super().reset()
        # 重置错误累积计数器
        self.error_accumulator = 0

    def step(self, state: State) -> Action:
        """
        执行一步操作
        
        使用BrowsingAgent执行一步操作，包括收集之前步骤的信息，
        并提示模型生成要执行的浏览器命令
        
        Args:
            state (State): 用于获取更新信息的状态对象
            
        Returns:
            Action: 返回以下Action类型之一：
                - BrowseInteractiveAction: 要运行的BrowserGym命令
                - MessageAction: 要运行的消息操作（如请求澄清）
                - AgentFinishAction: 结束交互
        """
        # 初始化消息列表和状态变量
        messages: list[Message] = []
        prev_actions = []  # 存储之前的操作历史
        cur_url = ''  # 当前页面URL
        cur_axtree_txt = ''  # 当前页面可访问性树文本
        error_prefix = ''  # 错误前缀信息
        last_obs = None  # 最后一个观察结果
        last_action = None  # 最后一个操作

        # 在评估模式下且只有一个事件时，初始化浏览器环境
        if EVAL_MODE and len(state.view) == 1:
            # 对于webarena和miniwob++评估，需要检索浏览器环境中已存在的初始观察
            # 通过发出noop操作来初始化并检索第一个观察结果
            # 对于非基准测试的浏览，浏览器环境从空白页开始，Agent需要首先导航到所需网站
            return BrowseInteractiveAction(browser_actions='noop()')

        # 遍历状态视图中的所有事件，收集操作历史和观察结果
        for event in state.view:
            if isinstance(event, BrowseInteractiveAction):
                # 收集浏览器交互操作历史
                prev_actions.append(event.browser_actions)
                last_action = event
            elif isinstance(event, MessageAction) and event.source == EventSource.AGENT:
                # Agent已响应，任务完成，返回结束操作
                return AgentFinishAction(outputs={'content': event.content})
            elif isinstance(event, Observation):
                # 记录最后一个观察结果
                last_obs = event

        # 在评估模式下移除第一个noop操作
        if EVAL_MODE:
            prev_actions = prev_actions[1:]

        # 将操作历史转换为字符串
        prev_action_str = '\n'.join(prev_actions)
        
        # 如果最后的BrowserInteractiveAction执行了BrowserGym的send_msg_to_user，
        # 我们也应该在OpenHands中向用户发送消息并结束任务
        if (
            isinstance(last_action, BrowseInteractiveAction)
            and last_action.browsergym_send_msg_to_user
        ):
            return MessageAction(last_action.browsergym_send_msg_to_user)

        # 处理浏览器输出观察结果
        if isinstance(last_obs, BrowserOutputObservation):
            if last_obs.error:
                # 添加错误恢复提示前缀
                error_prefix = get_error_prefix(last_obs.last_browser_action)
                self.error_accumulator += 1
                # 如果错误次数过多，返回失败消息
                if self.error_accumulator > 5:
                    return MessageAction('Too many errors encountered. Task failed.')

            # 获取当前页面URL
            cur_url = last_obs.url

            try:
                # 将可访问性树对象转换为字符串表示
                cur_axtree_txt = flatten_axtree_to_str(
                    last_obs.axtree_object,
                    extra_properties=last_obs.extra_element_properties,
                    with_clickable=True,  # 包含可点击信息
                    filter_visible_only=True,  # 仅过滤可见元素
                )
            except Exception as e:
                # 处理可访问性树转换错误
                logger.error(
                    'Error when trying to process the accessibility tree: %s', e
                )
                return MessageAction('Error encountered when browsing.')

        # 获取当前用户意图（目标）
        goal, _ = state.get_current_user_intent()

        # 如果没有获取到目标，从状态输入中获取任务
        if goal is None:
            goal = state.inputs['task']

        # 构建系统消息
        system_msg = get_system_message(
            goal,
            self.action_space.describe(with_long_description=False, with_examples=True),
        )

        # 添加系统消息到消息列表
        messages.append(Message(role='system', content=[TextContent(text=system_msg)]))

        # 构建用户提示并添加到消息列表
        prompt = get_prompt(error_prefix, cur_url, cur_axtree_txt, prev_action_str)
        messages.append(Message(role='user', content=[TextContent(text=prompt)]))

        # 调用LLM生成响应
        response = self.llm.completion(
            messages=self.llm.format_messages_for_llm(messages),
            stop=[')```', ')\n```'],  # 停止标记，确保正确的代码块结束
        )
        
        # 解析响应并返回对应的Action
        return self.response_parser.parse(response)