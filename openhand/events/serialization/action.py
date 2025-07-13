"""
action.py - Action序列化模块

该模块负责处理各种Action的序列化和反序列化操作，包含了从字典创建Action对象的核心功能。
主要处理Action对象与字典格式之间的转换，以及向后兼容性相关的参数处理。
"""

from typing import Any

from openhands.core.exceptions import LLMMalformedActionError
from openhands.events.action.action import Action
from openhands.events.action.agent import (
    AgentDelegateAction,
    AgentFinishAction,
    AgentRejectAction,
    AgentThinkAction,
    ChangeAgentStateAction,
    CondensationAction,
    CondensationRequestAction,
    RecallAction,
)
from openhands.events.action.browse import BrowseInteractiveAction, BrowseURLAction
from openhands.events.action.commands import (
    CmdRunAction,
    IPythonRunCellAction,
)
from openhands.events.action.empty import NullAction
from openhands.events.action.files import (
    FileEditAction,
    FileReadAction,
    FileWriteAction,
)
from openhands.events.action.mcp import MCPAction
from openhands.events.action.message import MessageAction, SystemMessageAction

# 所有可用的Action类型元组
# 包含系统支持的所有Action类，用于类型映射和验证
actions = (
    NullAction,              # 空Action，表示无操作
    CmdRunAction,            # 命令行执行Action
    IPythonRunCellAction,    # IPython单元格执行Action
    BrowseURLAction,         # 浏览器URL访问Action
    BrowseInteractiveAction, # 浏览器交互Action
    FileReadAction,          # 文件读取Action
    FileWriteAction,         # 文件写入Action
    FileEditAction,          # 文件编辑Action
    AgentThinkAction,        # Agent思考Action
    AgentFinishAction,       # Agent完成Action
    AgentRejectAction,       # Agent拒绝Action
    AgentDelegateAction,     # Agent委托Action
    RecallAction,            # 回忆Action
    ChangeAgentStateAction,  # 改变Agent状态Action
    MessageAction,           # 消息Action
    SystemMessageAction,     # 系统消息Action
    CondensationAction,      # Condenser压缩Action
    CondensationRequestAction, # Condenser压缩请求Action
    MCPAction,               # MCP协议Action
)

# Action类型字符串到Action类的映射字典
# 键为action.action属性值，值为对应的Action类
# 用于根据字符串类型创建对应的Action实例
ACTION_TYPE_TO_CLASS = {action_class.action: action_class for action_class in actions}  # type: ignore[attr-defined]


def handle_action_deprecated_args(args: dict[str, Any]) -> dict[str, Any]:
    """
    处理Action中已废弃的参数。
    
    该函数用于维护向后兼容性，处理在系统演进过程中被废弃的参数，
    确保旧版本的Action数据仍能正确处理。
    
    Args:
        args (dict[str, Any]): 原始的Action参数字典
        
    Returns:
        dict[str, Any]: 处理后的参数字典，移除或转换已废弃的参数
    """
    # keep_prompt参数在PR #4881中被废弃，直接移除
    if 'keep_prompt' in args:
        args.pop('keep_prompt')

    # 处理translated_ipython_code参数的废弃
    # 该参数曾用于存储转换后的IPython代码
    if 'translated_ipython_code' in args:
        code = args.pop('translated_ipython_code')

        # 检查是否为file_editor调用，使用前缀检查提高效率
        file_editor_prefix = 'print(file_editor(**'
        if (
            code is not None
            and code.startswith(file_editor_prefix)
            and code.endswith('))')
        ):
            try:
                # 提取并解析字典字符串
                import ast

                # 从代码中提取字典字符串部分
                # 移除前缀和结尾的'))'
                dict_str = code[len(file_editor_prefix) : -2]
                file_args = ast.literal_eval(dict_str)

                # 将解析出的文件编辑器参数合并到args中
                args.update(file_args)
            except (ValueError, SyntaxError):
                # 如果解析失败，仅移除translated_ipython_code参数
                pass

        # 特殊处理'view'命令
        # 'view'命令会被转换为FileReadAction，而FileReadAction不包含command参数
        if args.get('command') == 'view':
            args.pop('command')

    return args


def action_from_dict(action: dict) -> Action:
    """
    从字典创建Action对象。
    
    该函数是Action反序列化的核心方法，负责将字典格式的Action数据
    转换为对应的Action对象实例，同时处理各种兼容性和验证问题。
    
    Args:
        action (dict): 包含Action信息的字典，必须包含'action'键
        
    Returns:
        Action: 创建的Action对象实例
        
    Raises:
        LLMMalformedActionError: 当输入格式错误或Action类型未定义时抛出
    """
    # 验证输入参数类型
    if not isinstance(action, dict):
        raise LLMMalformedActionError('action must be a dictionary')
    
    # 创建字典副本，避免修改原始数据
    action = action.copy()
    
    # 验证必需的'action'键是否存在
    if 'action' not in action:
        raise LLMMalformedActionError(f"'action' key is not found in {action=}")
    
    # 验证action类型是否为字符串
    if not isinstance(action['action'], str):
        raise LLMMalformedActionError(
            f"'{action['action']=}' is not defined. Available actions: {ACTION_TYPE_TO_CLASS.keys()}"
        )
    
    # 根据action类型获取对应的Action类
    action_class = ACTION_TYPE_TO_CLASS.get(action['action'])
    if action_class is None:
        raise LLMMalformedActionError(
            f"'{action['action']=}' is not defined. Available actions: {ACTION_TYPE_TO_CLASS.keys()}"
        )
    
    # 获取Action参数，默认为空字典
    args = action.get('args', {})
    
    # 移除时间戳参数（如果存在），稍后单独处理
    timestamp = args.pop('timestamp', None)

    # 处理旧事件流的兼容性
    # is_confirmed已重命名为confirmation_state
    is_confirmed = args.pop('is_confirmed', None)
    if is_confirmed is not None:
        args['confirmation_state'] = is_confirmed

    # images_urls已重命名为image_urls
    if 'images_urls' in args:
        args['image_urls'] = args.pop('images_urls')

    # 处理已废弃的参数
    args = handle_action_deprecated_args(args)

    try:
        # 使用处理后的参数创建Action实例
        decoded_action = action_class(**args)
        
        # 如果指定了超时时间，设置硬超时
        if 'timeout' in action:
            blocking = args.get('blocking', False)
            decoded_action.set_hard_timeout(action['timeout'], blocking=blocking)

        # 如果提供了时间戳，设置到Action对象上
        if timestamp:
            decoded_action._timestamp = timestamp

    except TypeError as e:
        # 参数错误时抛出格式化的错误信息
        raise LLMMalformedActionError(
            f'action={action} has the wrong arguments: {str(e)}'
        )
    
    # 确保返回的对象是Action的实例
    assert isinstance(decoded_action, Action)
    return decoded_action