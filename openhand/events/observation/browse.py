from dataclasses import dataclass, field
from typing import Any

from openhands.core.schema import ObservationType
from openhands.events.observation.observation import Observation


@dataclass
class BrowserOutputObservation(Observation):
    """浏览器输出观察类
    
    这个数据类表示浏览器操作的输出结果。
    用于封装浏览器访问网页、截图等操作后返回的所有相关信息。
    
    Attributes:
        url (str): 当前访问的URL地址
        trigger_by_action (str): 触发此观察的Action类型
        screenshot (str): 截图的base64编码数据，在repr中隐藏显示
        screenshot_path (str | None): 保存的截图文件路径，可能为None
        set_of_marks (str): 页面标记集合，在repr中隐藏显示
        error (bool): 是否发生错误，默认为False
        observation (str): 观察类型，固定为BROWSE
        goal_image_urls (list[str]): 目标图片URL列表
        open_pages_urls (list[str]): 已打开页面的URL列表，不包含在内存中
        active_page_index (int): 当前活跃页面的索引，-1表示无活跃页面
        dom_object (dict[str, Any]): DOM对象数据，在repr中隐藏显示
        axtree_object (dict[str, Any]): 可访问性树对象数据，在repr中隐藏显示
        extra_element_properties (dict[str, Any]): 额外的元素属性，在repr中隐藏显示
        last_browser_action (str): 最后执行的浏览器操作
        last_browser_action_error (str): 最后浏览器操作的错误信息
        focused_element_bid (str): 当前获得焦点的元素的browser ID
        filter_visible_only (bool): 是否只过滤可见元素，默认为False
    """

    url: str  # 当前访问的网页URL
    trigger_by_action: str  # 触发这个观察的Action名称
    
    # 截图相关字段，不在对象表示中显示以避免输出过长
    screenshot: str = field(repr=False, default='')  # 截图的base64编码数据
    screenshot_path: str | None = field(default=None)  # 截图文件保存路径
    
    # 页面标记信息，不在对象表示中显示
    set_of_marks: str = field(default='', repr=False)  # 页面上的标记集合
    
    # 状态和类型信息
    error: bool = False  # 操作是否出错
    observation: str = ObservationType.BROWSE  # 观察类型标识
    
    # 目标和页面管理
    goal_image_urls: list[str] = field(default_factory=list)  # 目标图片URL列表
    open_pages_urls: list[str] = field(default_factory=list)  # 当前打开的所有页面URL
    active_page_index: int = -1  # 当前活跃页面在open_pages_urls中的索引
    
    # 页面结构数据，不在对象表示中显示以避免输出过长
    dom_object: dict[str, Any] = field(
        default_factory=dict, repr=False
    )  # 页面的DOM结构对象
    axtree_object: dict[str, Any] = field(
        default_factory=dict, repr=False
    )  # 页面的可访问性树结构对象
    extra_element_properties: dict[str, Any] = field(
        default_factory=dict, repr=False
    )  # 页面元素的额外属性信息
    
    # 操作历史和状态
    last_browser_action: str = ''  # 最后执行的浏览器操作命令
    last_browser_action_error: str = ''  # 最后操作的错误信息
    focused_element_bid: str = ''  # 当前聚焦元素的浏览器ID
    filter_visible_only: bool = False  # 是否只显示可见元素

    @property
    def message(self) -> str:
        """返回消息内容
        
        生成一个简洁的消息，说明访问了哪个URL。
        
        Returns:
            str: 格式化的访问消息
        """
        return 'Visited ' + self.url

    def __str__(self) -> str:
        """返回字符串表示
        
        返回BrowserOutputObservation的详细字符串表示，包含所有重要的状态信息。
        
        Returns:
            str: 详细的格式化字符串表示
        """
        # 构建基本信息
        ret = (
            '**BrowserOutputObservation**\n'
            f'URL: {self.url}\n'
            f'Error: {self.error}\n'
            f'Open pages: {self.open_pages_urls}\n'
            f'Active page index: {self.active_page_index}\n'
            f'Last browser action: {self.last_browser_action}\n'
            f'Last browser action error: {self.last_browser_action_error}\n'
            f'Focused element bid: {self.focused_element_bid}\n'
        )
        
        # 如果有截图路径，添加到输出中
        if self.screenshot_path:
            ret += f'Screenshot saved to: {self.screenshot_path}\n'
            
        # 添加Agent观察内容
        ret += '--- Agent Observation ---\n'
        ret += self.content
        return ret