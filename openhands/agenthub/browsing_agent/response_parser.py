import ast
import re

from openhands.controller.action_parser import ActionParser, ResponseParser
from openhands.core.logger import openhands_logger as logger
from openhands.events.action import (
    Action,
    BrowseInteractiveAction,
)


class BrowsingResponseParser(ResponseParser):
    """
    浏览器响应解析器类
    
    专门用于解析浏览器Agent响应的解析器，继承自基础ResponseParser类。
    负责将LLM的原始响应转换为可执行的浏览器操作。
    
    Attributes:
        action_parsers (list): 特定的操作解析器列表
        default_parser: 默认的操作解析器
    """
    
    def __init__(self) -> None:
        """
        初始化BrowsingResponseParser实例
        
        设置操作解析器列表和默认解析器。
        注意：action_parsers中的项目顺序很重要，会按顺序检查条件。
        """
        super().__init__()
        # 需要注意self.action_parsers中项目的顺序，按顺序检查解析条件
        self.action_parsers = [BrowsingActionParserMessage()]
        # 默认解析器处理浏览器交互操作
        self.default_parser = BrowsingActionParserBrowseInteractive()

    def parse(
        self, response: str | dict[str, list[dict[str, dict[str, str | None]]]]
    ) -> Action:
        """
        解析响应为Action对象
        
        接受字符串或字典格式的响应，解析为对应的Action对象
        
        Args:
            response: LLM的原始响应，可以是字符串或字典格式
            
        Returns:
            Action: 解析后的操作对象
        """
        # 根据响应类型提取操作字符串
        if isinstance(response, str):
            action_str = response
        else:
            action_str = self.parse_response(response)
        # 解析操作字符串为Action对象
        return self.parse_action(action_str)

    def parse_response(
        self, response: dict[str, list[dict[str, dict[str, str | None]]]]
    ) -> str:
        """
        从字典格式响应中提取操作字符串
        
        处理LLM API返回的字典格式响应，提取其中的操作内容并进行格式化
        
        Args:
            response: LLM API返回的字典格式响应
            
        Returns:
            str: 提取并格式化后的操作字符串
        """
        # 从响应字典中提取消息内容
        action_str = response['choices'][0]['message']['content']
        if action_str is None:
            return ''
        action_str = action_str.strip()
        
        # 确保action_str以')```'结尾，这是期望的格式
        if action_str:
            if not action_str.endswith('```'):
                if action_str.endswith(')'):
                    # 防止重复的结束括号，例如send_msg_to_user('Done'))
                    action_str += '```'
                else:
                    # 添加期望的格式结尾
                    action_str += ')```'
        
        # 记录调试信息
        logger.debug(action_str)
        return action_str

    def parse_action(self, action_str: str) -> Action:
        """
        将操作字符串解析为Action对象
        
        按顺序检查各个解析器的条件，使用第一个匹配的解析器进行解析
        
        Args:
            action_str: 待解析的操作字符串
            
        Returns:
            Action: 解析后的操作对象
        """
        # 按顺序检查每个解析器的条件
        for action_parser in self.action_parsers:
            if action_parser.check_condition(action_str):
                return action_parser.parse(action_str)
        # 如果没有匹配的解析器，使用默认解析器
        return self.default_parser.parse(action_str)


class BrowsingActionParserMessage(ActionParser):
    """
    消息操作解析器类
    
    用于解析非预期响应格式的消息，当响应不包含代码块时，
    将整个响应作为消息发送给用户。
    
    解析的操作类型：
    - BrowseInteractiveAction(browser_actions) - 处理非预期响应格式，向用户发送消息
    """
    
    def __init__(self) -> None:
        """初始化消息操作解析器"""
        pass

    def check_condition(self, action_str: str) -> bool:
        """
        检查是否满足消息解析条件
        
        当操作字符串中不包含代码块标记```时，认为是普通消息
        
        Args:
            action_str: 待检查的操作字符串
            
        Returns:
            bool: 如果不包含代码块标记则返回True
        """
        return '```' not in action_str

    def parse(self, action_str: str) -> Action:
        """
        解析为消息操作
        
        将普通文本响应包装为send_msg_to_user操作，直接向用户发送消息
        
        Args:
            action_str: 要发送的消息内容
            
        Returns:
            BrowseInteractiveAction: 包含消息发送操作的浏览器交互动作
        """
        # 将消息包装为send_msg_to_user函数调用
        msg = f'send_msg_to_user("""{action_str}""")'
        return BrowseInteractiveAction(
            browser_actions=msg,
            thought=action_str,  # 将原始文本作为思考过程
            browsergym_send_msg_to_user=action_str,  # 设置要发送给用户的消息
        )


class BrowsingActionParserBrowseInteractive(ActionParser):
    """
    浏览器交互操作解析器类
    
    用于解析包含代码块的浏览器操作响应，处理各种浏览器交互命令。
    特别处理BrowserGym中的send_msg_to_user函数调用。
    
    解析的操作类型：
    - BrowseInteractiveAction(browser_actions) - 处理浏览器操作和用户消息发送
    """
    
    def __init__(self) -> None:
        """初始化浏览器交互操作解析器"""
        pass

    def check_condition(self, action_str: str) -> bool:
        """
        检查是否满足浏览器交互解析条件
        
        该解析器作为默认解析器，总是返回True
        
        Args:
            action_str: 待检查的操作字符串
            
        Returns:
            bool: 总是返回True，作为默认解析器
        """
        return True

    def parse(self, action_str: str) -> Action:
        """
        解析浏览器交互操作
        
        将操作字符串解析为browser_actions和thought两部分。
        LLM可能返回单一字符串或包含思考过程的完整响应。
        
        完整响应格式示例：
        ### 基于当前页面状态和查找美国总统的目标，下一步操作应该涉及搜索相关信息
        ### 为了实现这个目标，我们可以导航到可靠的信息源，如搜索引擎或提供美国总统信息的特定网站
        ### 以下是实现这个目标的有效操作示例：
        ### ```
        ### goto('https://www.whitehouse.gov/about-the-white-house/presidents/')
        # 实际上，BrowsingResponseParser.parse_response也会在字符串末尾添加)```
        
        单一字符串格式示例：
        ### goto('https://www.whitehouse.gov/about-the-white-house/presidents/')
        # parse_response会在字符串末尾添加)```
        
        Args:
            action_str: 包含浏览器操作的字符串
            
        Returns:
            BrowseInteractiveAction: 解析后的浏览器交互操作
        """
        # 按```分割字符串，区分思考过程和具体操作
        parts = action_str.split('```')
        
        # 如果有代码块，使用代码块内容作为browser_actions，否则使用整个字符串
        browser_actions = (
            parts[1].strip() if parts[1].strip() != '' else parts[0].strip()
        )
        
        # 如果有代码块，代码块前的内容作为思考过程，否则思考过程为空
        thought = parts[0].strip() if parts[1].strip() != '' else ''

        # 如果LLM想要与用户对话，提取消息内容
        msg_content = ''
        
        # 遍历browser_actions中的每一行，查找send_msg_to_user调用
        for sub_action in browser_actions.split('\n'):
            if 'send_msg_to_user(' in sub_action:
                try:
                    # 使用AST解析获取函数参数
                    tree = ast.parse(sub_action)
                    args = tree.body[0].value.args  # type: ignore
                    msg_content = args[0].value
                except SyntaxError:
                    # 语法错误时记录错误，但仍尝试提取消息
                    logger.error(f'Error parsing action: {sub_action}')
                    # 语法不正确，但仍可以尝试获取消息
                    # 例如：send_msg_to_user("Hello, world!") 或 send_msg_to_user('Hello, world!')
                    match = re.search(r'send_msg_to_user\((["\'])(.*?)\1\)', sub_action)
                    if match:
                        msg_content = match.group(2)
                    else:
                        msg_content = ''

        return BrowseInteractiveAction(
            browser_actions=browser_actions,  # 具体的浏览器操作命令
            thought=thought,  # 思考过程
            browsergym_send_msg_to_user=msg_content,  # 要发送给用户的消息内容
        )