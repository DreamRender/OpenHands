"""
浏览器操作工具模块

该模块提供了与浏览器交互相关的工具函数，包括：
1. Accessibility Tree（可访问性树）的文本化处理
2. Agent观察文本的生成和格式化
3. 浏览器Action的执行和Observation的处理
4. 截图保存和错误处理

主要用于支持Agent与浏览器环境的交互和数据处理。
"""

import base64
import datetime
import os
from pathlib import Path
from typing import Any

from browsergym.utils.obs import flatten_axtree_to_str
from PIL import Image

from openhands.core.exceptions import BrowserUnavailableException
from openhands.core.schema import ActionType
from openhands.events.action import BrowseInteractiveAction, BrowseURLAction
from openhands.events.observation import BrowserOutputObservation
from openhands.runtime.browser.base64 import png_base64_url_to_image
from openhands.runtime.browser.browser_env import BrowserEnv
from openhands.utils.async_utils import call_sync_from_async


def get_axtree_str(
    axtree_object: dict[str, Any],
    extra_element_properties: dict[str, Any],
    filter_visible_only: bool = False,
) -> str:
    """
    获取Accessibility Tree的字符串表示
    
    将浏览器的Accessibility Tree对象转换为结构化的文本字符串，
    便于Agent理解和处理网页元素的层次结构和属性信息。
    
    Args:
        axtree_object (dict[str, Any]): Accessibility Tree对象，包含网页元素的层次结构
        extra_element_properties (dict[str, Any]): 额外的元素属性信息
        filter_visible_only (bool): 是否只包含可见元素，默认为False
            - True: 只显示当前可见的元素
            - False: 显示所有元素（包括不可见的）
    
    Returns:
        str: 格式化的Accessibility Tree文本字符串
        
    Note:
        - 返回的字符串包含元素的bid（浏览器标识符）信息
        - 包含可点击元素的标记信息
        - 不跳过通用元素，保持完整的结构信息
    """
    # 使用BrowserGym的工具函数将axtree展平为字符串
    cur_axtree_txt = flatten_axtree_to_str(
        axtree_object,
        extra_properties=extra_element_properties,  # 包含额外属性
        with_clickable=True,  # 标记可点击的元素
        skip_generic=False,  # 不跳过通用元素
        filter_visible_only=filter_visible_only,  # 根据参数过滤可见性
    )
    return str(cur_axtree_txt)


def get_agent_obs_text(obs: BrowserOutputObservation) -> str:
    """
    获取Agent观察文本
    
    将BrowserOutputObservation转换为Agent可以理解的结构化文本格式，
    包含当前网页状态、错误信息、Accessibility Tree等关键信息。
    
    Args:
        obs (BrowserOutputObservation): 浏览器输出的观察数据
        
    Returns:
        str: 格式化的Agent观察文本
        
    Raises:
        ValueError: 当触发Action类型无效时抛出
        
    Note:
        - 根据不同的触发Action类型生成不同格式的文本
        - 包含错误处理和成功状态的反馈
        - 提供详细的网页结构信息供Agent分析
    """
    # 处理交互式浏览Action触发的观察
    if obs.trigger_by_action == ActionType.BROWSE_INTERACTIVE:
        # 构建基础信息文本
        text = f'[Current URL: {obs.url}]\n'
        text += f'[Focused element bid: {obs.focused_element_bid}]\n'

        # 如果有截图路径，添加截图信息
        if obs.screenshot_path:
            text += f'[Screenshot saved to: {obs.screenshot_path}]\n'

        text += '\n'

        # 根据是否有错误显示不同的状态信息
        if obs.error:
            # 显示错误信息，使用分隔符包围便于识别
            text += (
                '================ BEGIN error message ===============\n'
                'The following error occurred when executing the last action:\n'
                f'{obs.last_browser_action_error}\n'
                '================ END error message ===============\n'
            )
        else:
            # 显示Action执行成功的信息
            text += '[Action executed successfully.]\n'
        
        try:
            # 获取Accessibility Tree的文本表示
            # 注意：这里不过滤只可见元素，为了简化给Agent展示完整的网页内容
            # FIXME: 需要处理网页过大的情况
            cur_axtree_txt = get_axtree_str(
                obs.axtree_object,
                obs.extra_element_properties,
                filter_visible_only=obs.filter_visible_only,
            )
            
            # 根据是否过滤可见元素添加不同的说明文本
            if not obs.filter_visible_only:
                text += (
                    f'Accessibility tree of the COMPLETE webpage:\nNote: [bid] is the unique alpha-numeric identifier at the beginning of lines for each element in the AXTree. Always use bid to refer to elements in your actions.\n'
                    f'============== BEGIN accessibility tree ==============\n'
                    f'{cur_axtree_txt}\n'
                    f'============== END accessibility tree ==============\n'
                )
            else:
                text += (
                    f'Accessibility tree of the VISIBLE portion of the webpage (accessibility tree of complete webpage is too large and you may need to scroll to view remaining portion of the webpage):\nNote: [bid] is the unique alpha-numeric identifier at the beginning of lines for each element in the AXTree. Always use bid to refer to elements in your actions.\n'
                    f'============== BEGIN accessibility tree ==============\n'
                    f'{cur_axtree_txt}\n'
                    f'============== END accessibility tree ==============\n'
                )
        except Exception as e:
            # 如果处理Accessibility Tree时发生错误，添加错误信息
            text += f'\n[Error encountered when processing the accessibility tree: {e}]'
        return text

    # 处理普通浏览Action触发的观察
    elif obs.trigger_by_action == ActionType.BROWSE:
        text = f'[Current URL: {obs.url}]\n'

        # 如果有错误，显示错误信息
        if obs.error:
            text += (
                '================ BEGIN error message ===============\n'
                'The following error occurred when trying to visit the URL:\n'
                f'{obs.last_browser_action_error}\n'
                '================ END error message ===============\n'
            )
        
        # 添加网页内容（通常是文本化的HTML内容）
        text += '============== BEGIN webpage content ==============\n'
        text += obs.content
        text += '\n============== END webpage content ==============\n'
        return text
    else:
        # 无效的触发Action类型
        raise ValueError(f'Invalid trigger_by_action: {obs.trigger_by_action}')


async def browse(
    action: BrowseURLAction | BrowseInteractiveAction,
    browser: BrowserEnv | None,
    workspace_dir: str | None = None,
) -> BrowserOutputObservation:
    """
    执行浏览器Action并返回观察结果
    
    该函数是浏览器操作的核心接口，负责：
    1. 处理不同类型的浏览器Action
    2. 与浏览器环境交互执行Action
    3. 处理返回的观察数据
    4. 保存截图文件
    5. 错误处理和恢复
    
    Args:
        action (BrowseURLAction | BrowseInteractiveAction): 要执行的浏览器Action
        browser (BrowserEnv | None): 浏览器环境实例
        workspace_dir (str | None): 工作空间目录，用于保存截图
        
    Returns:
        BrowserOutputObservation: 包含执行结果和网页状态的观察对象
        
    Raises:
        BrowserUnavailableException: 当浏览器环境不可用时抛出
        ValueError: 当Action类型无效时抛出
        
    Note:
        - 支持传统的BrowseURLAction和新的BrowseInteractiveAction
        - 自动处理截图保存和路径管理
        - 提供完整的错误处理机制
    """
    # 检查浏览器环境是否可用
    if browser is None:
        raise BrowserUnavailableException()

    # 根据Action类型构建相应的action字符串
    if isinstance(action, BrowseURLAction):
        # 处理传统的URL浏览Action
        asked_url = action.url
        # 如果URL不以http开头，转换为绝对路径
        if not asked_url.startswith('http'):
            asked_url = os.path.abspath(os.curdir) + action.url
        # 构建BrowserGym格式的Action字符串
        action_str = f'goto("{asked_url}")'

    elif isinstance(action, BrowseInteractiveAction):
        # 处理新的交互式浏览Action，支持完整的BrowserGym Action功能
        # BrowserGym Action参考: https://github.com/ServiceNow/BrowserGym/blob/main/core/src/browsergym/core/action/functions.py
        action_str = action.browser_actions
    else:
        # 无效的Action类型
        raise ValueError(f'Invalid action type: {action.action}')

    try:
        # 执行Action并获取观察结果
        # BrowserGym提供的观察结果格式参考: https://github.com/ServiceNow/BrowserGym/blob/main/core/src/browsergym/core/env.py#L396
        obs = await call_sync_from_async(browser.step, action_str)

        # 保存截图文件（如果提供了workspace_dir且有截图数据）
        screenshot_path = None
        if workspace_dir is not None and obs.get('screenshot'):
            # 创建截图目录（如果不存在）
            screenshots_dir = Path(workspace_dir) / '.browser_screenshots'
            screenshots_dir.mkdir(exist_ok=True)

            # 基于时间戳生成截图文件名
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            screenshot_filename = f'screenshot_{timestamp}.png'
            screenshot_path = str(screenshots_dir / screenshot_filename)

            # 直接从base64数据保存图像，避免使用PIL的Image.open
            # 这种方法绕过了在不同图像表示之间转换时可能出现的编码问题，
            # 确保从浏览器获取的原始PNG数据直接保存到磁盘。

            # 提取base64数据
            base64_data = obs.get('screenshot', '')
            if ',' in base64_data:
                # 移除data URL前缀（如果存在）
                base64_data = base64_data.split(',')[1]

            try:
                # 直接将base64解码为二进制数据
                image_data = base64.b64decode(base64_data)

                # 将二进制数据直接写入文件
                with open(screenshot_path, 'wb') as f:
                    f.write(image_data)

                # 验证图像是否正确保存（验证步骤，生产环境中可以移除）
                Image.open(screenshot_path).verify()
            except Exception:
                # 如果直接保存失败，回退到原来的方法
                image = png_base64_url_to_image(obs.get('screenshot'))
                image.save(screenshot_path, format='PNG', optimize=True)

        # 创建包含所有数据的观察对象
        observation = BrowserOutputObservation(
            content=obs['text_content'],  # 页面的文本内容
            url=obs.get('url', ''),  # 页面URL
            screenshot=obs.get('screenshot', None),  # base64编码的截图（PNG格式）
            screenshot_path=screenshot_path,  # 保存的截图文件路径
            set_of_marks=obs.get(
                'set_of_marks', None
            ),  # base64编码的Set-of-Marks标注截图（PNG格式）
            goal_image_urls=obs.get('image_content', []),  # 目标图像URL列表
            open_pages_urls=obs.get('open_pages_urls', []),  # 打开页面的URL列表
            active_page_index=obs.get(
                'active_page_index', -1
            ),  # 当前活跃页面的索引
            axtree_object=obs.get('axtree_object', {}),  # Accessibility Tree对象
            extra_element_properties=obs.get('extra_element_properties', {}),  # 额外元素属性
            focused_element_bid=obs.get(
                'focused_element_bid', None
            ),  # 当前聚焦元素的bid
            last_browser_action=obs.get(
                'last_action', ''
            ),  # 最后执行的浏览器环境Action
            last_browser_action_error=obs.get('last_action_error', ''),  # 最后Action的错误信息
            error=True if obs.get('last_action_error', '') else False,  # 错误标志
            trigger_by_action=action.action,  # 触发此观察的Action类型
        )

        # 首先使用axtree_object处理内容
        observation.content = get_agent_obs_text(observation)

        # 如果return_axtree为False，移除axtree相关对象以节省空间
        if not action.return_axtree:
            observation.dom_object = {}
            observation.axtree_object = {}
            observation.extra_element_properties = {}

        return observation
    except Exception as e:
        # 异常处理：创建错误观察对象
        error_message = str(e)
        error_url = asked_url if action.action == ActionType.BROWSE else ''

        # 创建错误观察对象
        observation = BrowserOutputObservation(
            content=error_message,  # 错误消息作为内容
            screenshot='',  # 空截图
            screenshot_path=None,  # 无截图路径
            error=True,  # 错误标志为True
            last_browser_action_error=error_message,  # 记录错误信息
            url=error_url,  # 错误相关的URL
            trigger_by_action=action.action,  # 触发此观察的Action类型
        )

        # 尝试使用get_agent_obs_text处理内容，无论return_axtree值如何
        try:
            observation.content = get_agent_obs_text(observation)
        except Exception:
            # 如果get_agent_obs_text失败，保持原始错误消息
            pass

        return observation
