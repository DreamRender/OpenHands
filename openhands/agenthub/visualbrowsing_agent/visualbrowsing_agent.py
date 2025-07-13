# 导入BrowserGym核心高级Action集合，用于定义浏览器交互操作
from browsergym.core.action.highlevel import HighLevelActionSet
# 导入用于将可访问性树展平为字符串的工具函数
from browsergym.utils.obs import flatten_axtree_to_str

# 导入浏览器Agent的响应解析器
from openhands.agenthub.browsing_agent.response_parser import BrowsingResponseParser
# 导入Agent基类
from openhands.controller.agent import Agent
# 导入State状态类
from openhands.controller.state.state import State
# 导入Agent配置类
from openhands.core.config import AgentConfig
# 导入OpenHands日志记录器
from openhands.core.logger import openhands_logger as logger
# 导入消息相关类：图像内容、消息、文本内容
from openhands.core.message import ImageContent, Message, TextContent
# 导入各种Action类
from openhands.events.action import (
    Action,
    AgentFinishAction,
    BrowseInteractiveAction,
    MessageAction,
)
# 导入事件源枚举
from openhands.events.event import EventSource
# 导入浏览器输出观察类
from openhands.events.observation import BrowserOutputObservation
# 导入观察基类
from openhands.events.observation.observation import Observation
# 导入LLM大语言Model类
from openhands.llm.llm import LLM
# 导入插件需求类
from openhands.runtime.plugins import (
    PluginRequirement,
)


def get_error_prefix(obs: BrowserOutputObservation) -> str:
    """
    从浏览器输出观察中提取错误前缀信息。
    
    这是一个临时修复函数，专门用于OneStopMarket场景，会忽略超时错误。
    如果观察结果中包含超时错误，则返回空字符串；否则返回格式化的错误信息。
    
    Args:
        obs (BrowserOutputObservation): 浏览器输出观察对象，包含上一次浏览器操作的错误信息
        
    Returns:
        str: 如果存在非超时错误，返回格式化的错误信息；如果是超时错误或无错误，返回空字符串
    """
    # 临时修复OneStopMarket，忽略超时错误
    if 'timeout' in obs.last_browser_action_error:
        return ''
    # 返回格式化的错误信息，包含标题和具体错误内容
    return f'## Error from previous action:\n{obs.last_browser_action_error}\n'


def create_goal_prompt(
    goal: str, image_urls: list[str] | None
) -> tuple[str, list[str]]:
    """
    创建目标提示文本和相关图像URL列表。
    
    根据给定的目标描述和可选的图像URL列表，生成结构化的提示文本，
    用于指导Agent执行特定任务。
    
    Args:
        goal (str): 要完成的目标任务描述
        image_urls (list[str] | None): 可选的目标相关图像URL列表
        
    Returns:
        tuple[str, list[str]]: 包含格式化目标文本和目标图像URL列表的元组
    """
    # 构建基础的目标提示文本，包含指令和目标描述
    goal_txt: str = f"""\
# Instructions
Review the current state of the page and all other information to find the best possible next action to accomplish your goal. Your answer will be interpreted and executed by a program, make sure to follow the formatting instructions.

## Goal:
{goal}
"""
    # 初始化目标图像URL列表
    goal_image_urls = []
    # 如果提供了图像URL，则将它们添加到提示文本中
    if image_urls is not None:
        for idx, url in enumerate(image_urls):
            # 为每个图像添加编号标识
            goal_txt = goal_txt + f'Images: Goal input image ({idx + 1})\n'
            goal_image_urls.append(url)
    goal_txt += '\n'
    return goal_txt, goal_image_urls


def create_observation_prompt(
    axtree_txt: str,
    tabs: str,
    focused_element: str,
    error_prefix: str,
    som_screenshot: str | None,
) -> tuple[str, str | None]:
    """
    创建当前观察状态的提示文本。
    
    将当前页面的各种状态信息（可访问性树、标签页、焦点元素、错误信息、截图等）
    组合成结构化的观察提示文本。
    
    Args:
        axtree_txt (str): 可访问性树的文本表示
        tabs (str): 当前打开的标签页信息
        focused_element (str): 当前焦点元素信息
        error_prefix (str): 错误前缀信息
        som_screenshot (str | None): 可选的页面截图URL（SOM: Set of Marks）
        
    Returns:
        tuple[str, str | None]: 包含观察文本和截图URL的元组
    """
    # 构建文本观察信息，包含标签页、可访问性树、焦点元素和错误信息
    txt_observation = f"""
# Observation of current step:
{tabs}{axtree_txt}{focused_element}{error_prefix}
"""

    # 初始化截图URL为None
    screenshot_url = None
    # 如果存在SOM截图且非空，则添加截图相关信息
    if (som_screenshot is not None) and (len(som_screenshot) > 0):
        # 添加截图说明，提醒只显示网页可见部分，可能需要滚动查看其余部分
        txt_observation += 'Image: Current page screenshot (Note that only visible portion of webpage is present in the screenshot. You may need to scroll to view the remaining portion of the web-page.\n'
        screenshot_url = som_screenshot
    else:
        # 如果没有SOM截图，记录信息日志
        logger.info('SOM Screenshot not present in observation!')
    txt_observation += '\n'
    return txt_observation, screenshot_url


def get_tabs(obs: BrowserOutputObservation) -> str:
    """
    获取当前打开的标签页信息。
    
    从浏览器输出观察中提取所有打开的标签页信息，包括URL和是否为活动标签页。
    
    Args:
        obs (BrowserOutputObservation): 浏览器输出观察对象
        
    Returns:
        str: 格式化的标签页信息字符串
    """
    # 初始化提示信息片段列表，包含标题
    prompt_pieces = ['\n## Currently open tabs:']
    # 遍历所有打开的页面URL
    for page_index, page_url in enumerate(obs.open_pages_urls):
        # 判断是否为活动标签页并添加相应标识
        active_or_not = ' (active tab)' if page_index == obs.active_page_index else ''
        # 格式化单个标签页信息
        prompt_piece = f"""\
Tab {page_index}{active_or_not}:
URL: {page_url}
"""
        prompt_pieces.append(prompt_piece)
    # 将所有片段组合成完整的标签页信息字符串
    return '\n'.join(prompt_pieces) + '\n'


def get_axtree(axtree_txt: str) -> str:
    """
    获取格式化的可访问性树信息。
    
    为可访问性树文本添加说明信息，包括bid标识符的使用说明和可见性标签的说明。
    
    Args:
        axtree_txt (str): 原始的可访问性树文本
        
    Returns:
        str: 包含说明信息的格式化可访问性树文本
    """
    # bid标识符说明信息
    bid_info = """\
Note: [bid] is the unique alpha-numeric identifier at the beginning of lines for each element in the AXTree. Always use bid to refer to elements in your actions.

"""
    # 可见性标签说明信息
    visible_tag_info = """\
Note: You can only interact with visible elements. If the "visible" tag is not present, the element is not visible on the page.

"""
    # 返回完整的可访问性树信息，包含说明和树结构
    return f'\n## AXTree:\n{bid_info}{visible_tag_info}{axtree_txt}\n'


def get_action_prompt(action_set: HighLevelActionSet) -> str:
    """
    获取Action操作集合的提示信息。
    
    生成Action空间的描述信息，包括通用说明和具体的操作描述。
    
    Args:
        action_set (HighLevelActionSet): 高级Action集合对象
        
    Returns:
        str: 格式化的Action操作提示信息
    """
    # Action集合的通用说明信息
    action_set_generic_info = """\
Note: This action set allows you to interact with your environment. Most of them are python function executing playwright code. The primary way of referring to elements in the page is through bid which are specified in your observations.

"""
    # 获取Action集合的具体描述，不包含长描述和示例
    action_description = action_set.describe(
        with_long_description=False,
        with_examples=False,
    )
    # 组合成完整的Action提示信息
    action_prompt = f'# Action space:\n{action_set_generic_info}{action_description}\n'
    return action_prompt


def get_history_prompt(prev_actions: list[BrowseInteractiveAction]) -> str:
    """
    获取历史交互记录的提示信息。
    
    将之前所有的浏览器交互操作格式化为历史记录提示，便于Agent理解之前的操作序列。
    
    Args:
        prev_actions (list[BrowseInteractiveAction]): 之前执行的浏览器交互Action列表
        
    Returns:
        str: 格式化的历史交互记录字符串
    """
    # 初始化历史提示列表，包含标题
    history_prompt = ['# History of all previous interactions with the task:\n']
    # 遍历所有之前的操作
    for i in range(len(prev_actions)):
        # 添加步骤编号
        history_prompt.append(f'## step {i + 1}')
        # 添加思考过程和具体操作
        history_prompt.append(
            f'\nOuput thought and action: {prev_actions[i].thought} ```{prev_actions[i].browser_actions}```\n'
        )
    # 将所有历史记录组合成完整字符串
    return '\n'.join(history_prompt) + '\n'


class VisualBrowsingAgent(Agent):
    """
    VisualBrowsing Agent类，继承自Agent基类。
    
    这是一个能够在浏览过程中使用网页截图的可视化浏览Agent。
    它可以通过分析网页的可访问性树和截图来执行复杂的网页交互任务。
    
    Attributes:
        VERSION (str): Agent版本号
        sandbox_plugins (list[PluginRequirement]): 沙箱插件需求列表
        response_parser (BrowsingResponseParser): 响应解析器实例
        action_space (HighLevelActionSet): 高级操作空间
        action_prompt (str): 操作提示信息
        abstract_example (str): 抽象示例
        concrete_example (str): 具体示例
        hints (str): 提示信息
        error_accumulator (int): 错误累加器
    """
    
    VERSION = '1.0'
    """Agent版本号"""
    
    # 原注释：VisualBrowsing Agent that can uses webpage screenshots during browsing.
    # 中文说明：可以在浏览过程中使用网页截图的VisualBrowsing Agent
    
    sandbox_plugins: list[PluginRequirement] = []
    """沙箱插件需求列表，当前为空列表"""
    
    response_parser = BrowsingResponseParser()
    """响应解析器实例，用于解析LLM的响应"""

    def __init__(
        self,
        llm: LLM,
        config: AgentConfig,
    ) -> None:
        """
        初始化VisualBrowsingAgent实例。

        Args:
            llm (LLM): 此Agent要使用的大语言Model
            config (AgentConfig): Agent配置对象
        """
        # 调用父类构造函数
        super().__init__(llm, config)
        
        # 定义可配置的操作空间，包含聊天功能、网页导航和基于可访问性树和HTML的网页定位
        # 更多详情请参见：https://github.com/ServiceNow/BrowserGym/blob/main/core/src/browsergym/core/action/highlevel.py
        action_subsets = [
            'chat',    # 聊天功能
            'bid',     # 元素标识符操作
            'nav',     # 导航操作
            'tab',     # 标签页操作
            'infeas',  # 不可行操作处理
        ]
        # 创建高级操作集合
        self.action_space = HighLevelActionSet(
            subsets=action_subsets,
            strict=False,      # 对操作解析不那么严格
            multiaction=False, # 不支持多重操作
        )
        # 获取操作提示信息
        self.action_prompt = get_action_prompt(self.action_space)
        
        # 抽象示例，描述答案的结构和内容
        self.abstract_example = f"""
# Abstract Example

Here is an abstract version of the answer with description of the content of each tag. Make sure you follow this structure, but replace the content with your answer:

You must mandatorily think step by step. If you need to make calculations such as coordinates, write them here. Describe the effect that your previous action had on the current content of the page. In summary the next action I will perform is ```{self.action_space.example_action(abstract=True)}```
"""
        # 具体示例，展示如何格式化答案
        self.concrete_example = """
# Concrete Example

Here is a concrete example of how to format your answer. Make sure to generate the action in the correct format ensuring that the action is present inside ``````:

Let's think step-by-step. From previous action I tried to set the value of year to "2022", using select_option, but it doesn't appear to be in the form. It may be a dynamic dropdown, I will try using click with the bid "324" and look at the response from the page. In summary the next action I will perform is ```click('324')```
"""
        # 提示信息，提供操作建议
        self.hints = """
Note:
* Make sure to use bid to identify elements when using commands.
* Interacting with combobox, dropdowns and auto-complete fields can be tricky, sometimes you need to use select_option, while other times you need to use fill or click and wait for the reaction of the page.

"""
        # 重置Agent状态
        self.reset()

    def reset(self) -> None:
        """
        重置VisualBrowsingAgent的内部状态。
        
        调用父类的reset方法，并重置Agent特定的计数器（但不重置LLM指标）。
        """
        super().reset()
        # 重置Agent特定的计数器，但不重置LLM指标
        self.error_accumulator = 0

    def step(self, state: State) -> Action:
        """
        使用VisualBrowsingAgent执行一个步骤。

        这包括收集之前步骤的信息，并提示Model生成要执行的浏览命令。

        Args:
            state (State): 用于获取更新信息的状态对象

        Returns:
            Action: 可能返回以下几种Action类型：
                - BrowseInteractiveAction: 要运行的BrowserGym命令
                - MessageAction: 要运行的消息操作（例如请求澄清）
                - AgentFinishAction: 结束交互
        """
        # 初始化消息列表和各种状态变量
        messages: list[Message] = []
        prev_actions = []           # 之前的操作列表
        cur_axtree_txt = ''        # 当前可访问性树文本
        error_prefix = ''          # 错误前缀
        focused_element = ''       # 焦点元素
        tabs = ''                  # 标签页信息
        last_obs = None           # 最后的观察
        last_action = None        # 最后的操作
        set_of_marks = None       # 初始化set_of_marks为None

        # 如果只有一个事件（初始状态）
        if len(state.view) == 1:
            # 对于visualwebarena、webarena和miniwob++评估，需要检索浏览器环境中已存在的初始观察
            # 通过发出noop操作来初始化并检索第一个观察
            # 对于非基准浏览，浏览器环境从空白页开始，Agent应该首先导航到所需的网站
            return BrowseInteractiveAction(
                browser_actions='noop(1000)', return_axtree=True
            )

        # 遍历状态视图中的所有事件
        for event in state.view:
            if isinstance(event, BrowseInteractiveAction):
                # 收集之前的浏览交互操作
                prev_actions.append(event)
                last_action = event
            elif isinstance(event, MessageAction) and event.source == EventSource.AGENT:
                # Agent已响应，任务完成
                return AgentFinishAction(outputs={'content': event.content})
            elif isinstance(event, Observation):
                # 仅处理BrowserOutputObservation，跳过其他观察类型
                if not isinstance(event, BrowserOutputObservation):
                    continue
                last_obs = event

        # 如果有之前的操作（忽略第一个noop操作）
        if len(prev_actions) >= 1:  # 忽略noop()
            prev_actions = prev_actions[1:]  # 移除第一个noop操作

        # 如果最后的BrowserInteractiveAction执行了BrowserGym的send_msg_to_user
        # 我们也应该在OpenHands中发送消息给用户并结束
        if (
            isinstance(last_action, BrowseInteractiveAction)
            and last_action.browsergym_send_msg_to_user
        ):
            return MessageAction(last_action.browsergym_send_msg_to_user)

        # 获取历史提示信息
        history_prompt = get_history_prompt(prev_actions)
        
        # 如果最后的观察是BrowserOutputObservation类型
        if isinstance(last_obs, BrowserOutputObservation):
            # 处理错误信息
            if last_obs.error:
                # 添加错误恢复提示前缀
                error_prefix = get_error_prefix(last_obs)
                if len(error_prefix) > 0:
                    # 增加错误累加器
                    self.error_accumulator += 1
                    # 如果错误次数超过5次，返回失败消息
                    if self.error_accumulator > 5:
                        return MessageAction(
                            'Too many errors encountered. Task failed.'
                        )
            
            # 设置默认焦点元素信息
            focused_element = '## Focused element:\nNone\n'
            # 如果存在焦点元素，更新焦点元素信息
            if last_obs.focused_element_bid is not None:
                focused_element = (
                    f"## Focused element:\nbid='{last_obs.focused_element_bid}'\n"
                )
            
            # 获取标签页信息
            tabs = get_tabs(last_obs)
            
            try:
                # 重要：保持完整网页的可访问性树，添加可见和可点击标签
                cur_axtree_txt = flatten_axtree_to_str(
                    last_obs.axtree_object,                    # 可访问性树对象
                    extra_properties=last_obs.extra_element_properties,  # 额外元素属性
                    with_visible=True,                         # 包含可见性信息
                    with_clickable=True,                       # 包含可点击性信息
                    with_center_coords=False,                  # 不包含中心坐标
                    with_bounding_box_coords=False,            # 不包含边界框坐标
                    filter_visible_only=False,                 # 不仅过滤可见元素
                    filter_with_bid_only=False,                # 不仅过滤带bid的元素
                    filter_som_only=False,                     # 不仅过滤SOM元素
                )
                # 格式化可访问性树文本
                cur_axtree_txt = get_axtree(axtree_txt=cur_axtree_txt)
            except Exception as e:
                # 处理可访问性树时出错，记录错误并返回错误消息
                logger.error(
                    'Error when trying to process the accessibility tree: %s', e
                )
                return MessageAction('Error encountered when browsing.')
            
            # 获取标记集合
            set_of_marks = last_obs.set_of_marks
        
        # 获取当前用户意图和相关图像
        goal, image_urls = state.get_current_user_intent()

        # 如果没有获取到目标，从输入中获取任务
        if goal is None:
            goal = state.inputs['task']
        
        # 创建目标提示和观察提示
        goal_txt, goal_images = create_goal_prompt(goal, image_urls)
        observation_txt, som_screenshot = create_observation_prompt(
            cur_axtree_txt, tabs, focused_element, error_prefix, set_of_marks
        )
        
        # 构建发送给用户的提示内容
        human_prompt: list[TextContent | ImageContent] = [
            TextContent(type='text', text=goal_txt)  # 目标文本
        ]
        # 如果有目标图像，添加到提示中
        if len(goal_images) > 0:
            human_prompt.append(ImageContent(image_urls=goal_images))
        
        # 添加观察文本
        human_prompt.append(TextContent(type='text', text=observation_txt))
        
        # 如果有SOM截图，添加到提示中
        if som_screenshot is not None:
            human_prompt.append(ImageContent(image_urls=[som_screenshot]))
        
        # 构建剩余内容，包含历史、操作、提示和示例
        remaining_content = f"""
{history_prompt}\
{self.action_prompt}\
{self.hints}\
{self.abstract_example}\
{self.concrete_example}\
"""
        human_prompt.append(TextContent(type='text', text=remaining_content))

        # 系统消息，定义Agent的角色和任务
        system_msg = """\
You are an agent trying to solve a web task based on the content of the page and user instructions. You can interact with the page and explore, and send messages to the user when you finish the task. Each time you submit an action it will be sent to the browser and you will receive a new page.
""".strip()

        # 构建完整的消息列表
        messages.append(Message(role='system', content=[TextContent(text=system_msg)]))
        messages.append(Message(role='user', content=human_prompt))

        # 格式化消息以供LLM使用
        flat_messages = self.llm.format_messages_for_llm(messages)

        # 调用LLM生成响应
        response = self.llm.completion(
            messages=flat_messages,
            temperature=0.0,                # 设置温度为0，确保确定性输出
            stop=[')```', ')\n```'],       # 设置停止标记
        )

        # 解析响应并返回相应的Action
        return self.response_parser.parse(response)