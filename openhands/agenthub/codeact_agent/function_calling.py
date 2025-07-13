"""此文件包含不同动作的函数调用实现。

这类似于 `CodeActResponseParser` 的功能。
主要负责将 LLM 的函数调用响应转换为对应的 OpenHands 动作对象。
"""

import json

# LLM 响应相关导入
from litellm import (
    ModelResponse,
)

# 导入各种工具定义
from openhands.agenthub.codeact_agent.tools import (
    BrowserTool,
    CondensationRequestTool,
    FinishTool,
    IPythonTool,
    LLMBasedFileEditTool,
    ThinkTool,
    create_cmd_run_tool,
    create_str_replace_editor_tool,
)
# 导入异常类
from openhands.core.exceptions import (
    FunctionCallNotExistsError,
    FunctionCallValidationError,
)
from openhands.core.logger import openhands_logger as logger
# 导入各种动作类
from openhands.events.action import (
    Action,
    AgentDelegateAction,
    AgentFinishAction,
    AgentThinkAction,
    BrowseInteractiveAction,
    CmdRunAction,
    FileEditAction,
    FileReadAction,
    IPythonRunCellAction,
    MessageAction,
)
from openhands.events.action.agent import CondensationRequestAction
from openhands.events.action.mcp import MCPAction
# 导入事件相关枚举
from openhands.events.event import FileEditSource, FileReadSource
from openhands.events.tool import ToolCallMetadata


def combine_thought(action: Action, thought: str) -> Action:
    """将思考内容与动作合并。
    
    如果动作支持思考属性，将思考内容添加到动作中。
    如果动作已有思考内容，则将新思考内容前置。
    
    Args:
        action (Action): 要添加思考内容的动作
        thought (str): 要添加的思考内容
        
    Returns:
        Action: 合并了思考内容的动作
    """
    # 检查动作是否支持思考属性
    if not hasattr(action, 'thought'):
        return action
    
    # 合并思考内容
    if thought and action.thought:
        # 如果都有内容，将新思考内容放在前面
        action.thought = f'{thought}\n{action.thought}'
    elif thought:
        # 如果只有新思考内容，直接设置
        action.thought = thought
    
    return action


def response_to_actions(
    response: ModelResponse, mcp_tool_names: list[str] | None = None
) -> list[Action]:
    """将 LLM 响应转换为动作列表。
    
    解析 LLM 响应中的工具调用，并转换为相应的 OpenHands 动作对象。
    支持多种工具类型，包括命令执行、文件编辑、浏览器操作等。
    
    Args:
        response (ModelResponse): LLM 的响应对象
        mcp_tool_names (list[str] | None): MCP 工具名称列表，可选
        
    Returns:
        list[Action]: 转换后的动作列表
        
    Raises:
        FunctionCallValidationError: 当工具调用参数无效时
        FunctionCallNotExistsError: 当工具不存在时
    """
    actions: list[Action] = []
    
    # 目前只支持单个选择
    assert len(response.choices) == 1, 'Only one choice is supported for now'
    choice = response.choices[0]
    assistant_msg = choice.message
    
    # 检查是否有工具调用
    if hasattr(assistant_msg, 'tool_calls') and assistant_msg.tool_calls:
        # 检查是否有助手消息内容，如果有，将其添加到思考中
        thought = ''
        if isinstance(assistant_msg.content, str):
            thought = assistant_msg.content
        elif isinstance(assistant_msg.content, list):
            # 处理多部分消息内容
            for msg in assistant_msg.content:
                if msg['type'] == 'text':
                    thought += msg['text']

        # 处理每个工具调用，转换为 OpenHands 动作
        for i, tool_call in enumerate(assistant_msg.tool_calls):
            action: Action
            logger.debug(f'Tool call in function_calling.py: {tool_call}')
            
            # 解析工具调用参数
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.decoder.JSONDecodeError as e:
                raise FunctionCallValidationError(
                    f'Failed to parse tool call arguments: {tool_call.function.arguments}'
                ) from e

            # ================================================
            # CmdRunTool (Bash) - 命令执行工具
            # ================================================
            if tool_call.function.name == create_cmd_run_tool()['function']['name']:
                # 验证必需参数
                if 'command' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "command" in tool call {tool_call.function.name}'
                    )
                
                # 转换 is_input 为布尔值
                is_input = arguments.get('is_input', 'false') == 'true'
                action = CmdRunAction(command=arguments['command'], is_input=is_input)

                # 如果提供了超时时间，设置硬超时
                if 'timeout' in arguments:
                    try:
                        action.set_hard_timeout(float(arguments['timeout']))
                    except ValueError as e:
                        raise FunctionCallValidationError(
                            f"Invalid float passed to 'timeout' argument: {arguments['timeout']}"
                        ) from e

            # ================================================
            # IPythonTool (Jupyter) - IPython 代码执行工具
            # ================================================
            elif tool_call.function.name == IPythonTool['function']['name']:
                # 验证必需参数
                if 'code' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "code" in tool call {tool_call.function.name}'
                    )
                action = IPythonRunCellAction(code=arguments['code'])
                
            # ================================================
            # 浏览器 Agent 委托 - 委托给专门的浏览器 Agent
            # ================================================
            elif tool_call.function.name == 'delegate_to_browsing_agent':
                action = AgentDelegateAction(
                    agent='BrowsingAgent',
                    inputs=arguments,
                )

            # ================================================
            # AgentFinishAction - 任务完成动作
            # ================================================
            elif tool_call.function.name == FinishTool['function']['name']:
                action = AgentFinishAction(
                    final_thought=arguments.get('message', ''),
                    task_completed=arguments.get('task_completed', None),
                )

            # ================================================
            # LLMBasedFileEditTool - 基于 LLM 的文件编辑工具（已弃用）
            # ================================================
            elif tool_call.function.name == LLMBasedFileEditTool['function']['name']:
                # 验证必需参数
                if 'path' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "path" in tool call {tool_call.function.name}'
                    )
                if 'content' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "content" in tool call {tool_call.function.name}'
                    )
                
                action = FileEditAction(
                    path=arguments['path'],
                    content=arguments['content'],
                    start=arguments.get('start', 1),
                    end=arguments.get('end', -1),
                    impl_source=arguments.get(
                        'impl_source', FileEditSource.LLM_BASED_EDIT
                    ),
                )
                
            # ================================================
            # 字符串替换编辑器工具 - 精确的文本替换编辑
            # ================================================
            elif (
                tool_call.function.name
                == create_str_replace_editor_tool()['function']['name']
            ):
                # 验证必需参数
                if 'command' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "command" in tool call {tool_call.function.name}'
                    )
                if 'path' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "path" in tool call {tool_call.function.name}'
                    )
                
                path = arguments['path']
                command = arguments['command']
                # 获取除 command 和 path 外的其他参数
                other_kwargs = {
                    k: v for k, v in arguments.items() if k not in ['command', 'path']
                }

                # 根据命令类型创建不同的动作
                if command == 'view':
                    # 查看文件内容
                    action = FileReadAction(
                        path=path,
                        impl_source=FileReadSource.OH_ACI,
                        view_range=other_kwargs.get('view_range', None),
                    )
                else:
                    # 编辑文件
                    if 'view_range' in other_kwargs:
                        # 移除 view_range，因为 FileEditAction 不需要此参数
                        other_kwargs.pop('view_range')

                    # 过滤掉意外的参数
                    valid_kwargs = {}
                    # 从字符串替换编辑器工具定义中获取有效参数
                    str_replace_editor_tool = create_str_replace_editor_tool()
                    valid_params = set(
                        str_replace_editor_tool['function']['parameters'][
                            'properties'
                        ].keys()
                    )
                    
                    for key, value in other_kwargs.items():
                        if key in valid_params:
                            valid_kwargs[key] = value
                        else:
                            raise FunctionCallValidationError(
                                f'Unexpected argument {key} in tool call {tool_call.function.name}. Allowed arguments are: {valid_params}'
                            )

                    action = FileEditAction(
                        path=path,
                        command=command,
                        impl_source=FileEditSource.OH_ACI,
                        **valid_kwargs,
                    )
                    
            # ================================================
            # AgentThinkAction - Agent 思考动作
            # ================================================
            elif tool_call.function.name == ThinkTool['function']['name']:
                action = AgentThinkAction(thought=arguments.get('thought', ''))

            # ================================================
            # CondensationRequestAction - 历史压缩请求动作
            # ================================================
            elif tool_call.function.name == CondensationRequestTool['function']['name']:
                action = CondensationRequestAction()

            # ================================================
            # BrowserTool - 浏览器交互工具
            # ================================================
            elif tool_call.function.name == BrowserTool['function']['name']:
                # 验证必需参数
                if 'code' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "code" in tool call {tool_call.function.name}'
                    )
                action = BrowseInteractiveAction(browser_actions=arguments['code'])

            # ================================================
            # MCPAction (MCP) - MCP 协议动作
            # ================================================
            elif mcp_tool_names and tool_call.function.name in mcp_tool_names:
                action = MCPAction(
                    name=tool_call.function.name,
                    arguments=arguments,
                )
            else:
                # 未知工具调用
                raise FunctionCallNotExistsError(
                    f'Tool {tool_call.function.name} is not registered. (arguments: {arguments}). Please check the tool name and retry with an existing tool.'
                )

            # 只在第一个动作中添加思考内容
            if i == 0:
                action = combine_thought(action, thought)
                
            # 为工具调用添加元数据
            action.tool_call_metadata = ToolCallMetadata(
                tool_call_id=tool_call.id,
                function_name=tool_call.function.name,
                model_response=response,
                total_calls_in_response=len(assistant_msg.tool_calls),
            )
            actions.append(action)
    else:
        # 没有工具调用，创建普通消息动作
        actions.append(
            MessageAction(
                content=str(assistant_msg.content) if assistant_msg.content else '',
                wait_for_response=True,
            )
        )

    # 为动作添加响应 ID
    # 这确保我们可以将没有工具调用的动作（如 MessageAction）
    # 和有工具调用的动作（如 CmdRunAction、IPythonRunCellAction 等）
    # 与 token 使用数据匹配
    for action in actions:
        action.response_id = response.id

    # 确保至少有一个动作
    assert len(actions) >= 1
    return actions