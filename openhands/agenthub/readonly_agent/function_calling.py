"""
function_calling.py - 函数调用实现模块

该文件包含了不同Action的函数调用实现。
此功能类似于`CodeActResponseParser`的功能。

主要功能：
1. 将工具调用参数转换为shell命令
2. 将Model响应转换为OpenHands Action对象
3. 提供可用工具的列表
"""

import json
import shlex

from litellm import (
    ChatCompletionToolParam,
    ModelResponse,
)

from openhands.agenthub.codeact_agent.function_calling import (
    combine_thought,
)
from openhands.agenthub.codeact_agent.tools import (
    FinishTool,
    ThinkTool,
)
from openhands.agenthub.readonly_agent.tools import (
    GlobTool,
    GrepTool,
    ViewTool,
)
from openhands.core.exceptions import (
    FunctionCallNotExistsError,
    FunctionCallValidationError,
)
from openhands.core.logger import openhands_logger as logger
from openhands.events.action import (
    Action,
    AgentFinishAction,
    AgentThinkAction,
    CmdRunAction,
    FileReadAction,
    MCPAction,
    MessageAction,
)
from openhands.events.event import FileReadSource
from openhands.events.tool import ToolCallMetadata


def grep_to_cmdrun(
    pattern: str, path: str | None = None, include: str | None = None
) -> str:
    """
    将grep工具参数转换为shell命令字符串。
    
    注意：此函数目前依赖于`rg`（ripgrep）。
    在使用CLIRuntime或LocalRuntime时，`rg`可能未安装。
    TODO：如果`rg`不可用，实现回退到`grep`的功能。

    Args:
        pattern (str): 要在文件内容中搜索的正则表达式模式
        path (str | None): 要搜索的目录（可选）
        include (str | None): 用于过滤要搜索文件的可选文件模式（例如："*.js"）

    Returns:
        str: 用于ripgrep的正确转义的shell命令字符串
    """
    # 使用shlex.quote正确转义所有shell特殊字符
    quoted_pattern = shlex.quote(pattern)
    path_arg = shlex.quote(path) if path else '.'

    # 构建ripgrep命令
    # -li: 列出匹配的文件名（忽略大小写）
    # --sortr=modified: 按修改时间逆序排序
    rg_cmd = f'rg -li {quoted_pattern} --sortr=modified'

    # 如果指定了文件包含模式，添加glob过滤器
    if include:
        quoted_include = shlex.quote(include)
        rg_cmd += f' --glob {quoted_include}'

    # 构建完整命令，限制输出为前100行
    complete_cmd = f'{rg_cmd} {path_arg} | head -n 100'

    # 为输出添加标题头
    echo_cmd = f'echo "Below are the execution results of the search command: {complete_cmd}\n"; '
    return echo_cmd + complete_cmd


def glob_to_cmdrun(pattern: str, path: str = '.') -> str:
    """
    将glob工具参数转换为shell命令字符串。
    
    注意：此函数目前依赖于`rg`（ripgrep）。
    在使用CLIRuntime或LocalRuntime时，`rg`可能未安装。
    TODO：如果`rg`不可用，实现回退到`find`的功能。

    Args:
        pattern (str): 要匹配文件的glob模式（例如："**/*.js"）
        path (str): 要搜索的目录（默认为当前目录）

    Returns:
        str: 用于ripgrep实现glob功能的正确转义的shell命令字符串
    """
    # 使用shlex.quote正确转义所有shell特殊字符
    quoted_path = shlex.quote(path)
    quoted_pattern = shlex.quote(pattern)

    # 使用ripgrep的仅glob模式，通过-g标志和--files列出文件
    # 这最接近NodeJS glob实现的行为
    # --files: 仅列出文件，不搜索内容
    # -g: 使用glob模式过滤
    # --sortr=modified: 按修改时间逆序排序
    rg_cmd = f'rg --files {quoted_path} -g {quoted_pattern} --sortr=modified'

    # 排序结果并限制为100条（匹配Node.js实现）
    sort_and_limit_cmd = ' | head -n 100'

    complete_cmd = f'{rg_cmd}{sort_and_limit_cmd}'

    # 为输出添加标题头
    echo_cmd = f'echo "Below are the execution results of the glob command: {complete_cmd}\n"; '
    return echo_cmd + complete_cmd


def response_to_actions(
    response: ModelResponse, mcp_tool_names: list[str] | None = None
) -> list[Action]:
    """
    将Model响应转换为Action列表。
    
    该函数解析LLM的响应，将其中的工具调用转换为相应的OpenHands Action对象。
    支持多种工具类型：Finish、View、Think、Grep、Glob、MCP等。

    Args:
        response (ModelResponse): LLM的响应对象
        mcp_tool_names (list[str] | None): MCP工具名称列表（可选）

    Returns:
        list[Action]: 转换后的Action对象列表

    Raises:
        FunctionCallValidationError: 当工具调用参数无效时
        FunctionCallNotExistsError: 当工具不存在时
    """
    actions: list[Action] = []  # 存储转换后的Action列表
    
    # 目前仅支持单个选择
    assert len(response.choices) == 1, 'Only one choice is supported for now'
    choice = response.choices[0]
    assistant_msg = choice.message
    
    # 检查是否有工具调用
    if hasattr(assistant_msg, 'tool_calls') and assistant_msg.tool_calls:
        # 检查是否有assistant_msg.content，如果有，将其添加到thought中
        thought = ''
        if isinstance(assistant_msg.content, str):
            thought = assistant_msg.content
        elif isinstance(assistant_msg.content, list):
            # 处理列表格式的内容，提取文本部分
            for msg in assistant_msg.content:
                if msg['type'] == 'text':
                    thought += msg['text']

        # 处理每个工具调用，转换为OpenHands Action
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
            # AgentFinishAction - Agent完成任务的Action
            # ================================================
            if tool_call.function.name == FinishTool['function']['name']:
                action = AgentFinishAction(
                    final_thought=arguments.get('message', ''),  # 最终思考内容
                    task_completed=arguments.get('task_completed', None),  # 任务是否完成
                )

            # ================================================
            # ViewTool (基于ACI的文件查看器，只读)
            # ================================================
            elif tool_call.function.name == ViewTool['function']['name']:
                # 验证必需参数
                if 'path' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "path" in tool call {tool_call.function.name}'
                    )
                action = FileReadAction(
                    path=arguments['path'],  # 文件路径
                    impl_source=FileReadSource.OH_ACI,  # 实现源：OpenHands ACI
                    view_range=arguments.get('view_range', None),  # 查看范围（可选）
                )

            # ================================================
            # AgentThinkAction - Agent思考的Action
            # ================================================
            elif tool_call.function.name == ThinkTool['function']['name']:
                action = AgentThinkAction(thought=arguments.get('thought', ''))

            # ================================================
            # GrepTool (文件内容搜索)
            # ================================================
            elif tool_call.function.name == GrepTool['function']['name']:
                # 验证必需参数
                if 'pattern' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "pattern" in tool call {tool_call.function.name}'
                    )

                # 提取参数
                pattern = arguments['pattern']
                path = arguments.get('path')
                include = arguments.get('include')

                # 转换为命令行执行
                grep_cmd = grep_to_cmdrun(pattern, path, include)
                action = CmdRunAction(command=grep_cmd, is_input=False)

            # ================================================
            # GlobTool (文件模式匹配)
            # ================================================
            elif tool_call.function.name == GlobTool['function']['name']:
                # 验证必需参数
                if 'pattern' not in arguments:
                    raise FunctionCallValidationError(
                        f'Missing required argument "pattern" in tool call {tool_call.function.name}'
                    )

                # 提取参数
                pattern = arguments['pattern']
                path = arguments.get('path', '.')

                # 转换为命令行执行
                glob_cmd = glob_to_cmdrun(pattern, path)
                action = CmdRunAction(command=glob_cmd, is_input=False)

            # ================================================
            # MCPAction (MCP工具调用)
            # ================================================
            elif mcp_tool_names and tool_call.function.name in mcp_tool_names:
                action = MCPAction(
                    name=tool_call.function.name,  # MCP工具名称
                    arguments=arguments,  # MCP工具参数
                )

            else:
                # 未知工具错误
                raise FunctionCallNotExistsError(
                    f'Tool {tool_call.function.name} is not registered. (arguments: {arguments}). Please check the tool name and retry with an existing tool.'
                )

            # 只为第一个Action添加thought
            if i == 0:
                action = combine_thought(action, thought)
                
            # 为工具调用添加Metadata
            action.tool_call_metadata = ToolCallMetadata(
                tool_call_id=tool_call.id,  # 工具调用ID
                function_name=tool_call.function.name,  # 函数名称
                model_response=response,  # Model响应
                total_calls_in_response=len(assistant_msg.tool_calls),  # 响应中的总调用数
            )
            actions.append(action)
    else:
        # 没有工具调用时，创建消息Action
        actions.append(
            MessageAction(
                content=str(assistant_msg.content) if assistant_msg.content else '',
                wait_for_response=True,  # 等待用户响应
            )
        )

    # 为所有Action添加响应ID
    # 这确保我们可以匹配没有工具调用的Action（例如MessageAction）
    # 和有工具调用的Action（例如CmdRunAction、IPythonRunCellAction等）
    # 与token使用数据对应
    for action in actions:
        action.response_id = response.id

    # 确保至少有一个Action
    assert len(actions) >= 1
    return actions


def get_tools() -> list[ChatCompletionToolParam]:
    """
    获取ReadOnlyAgent可用的工具列表。
    
    Returns:
        list[ChatCompletionToolParam]: 工具配置列表，包括Think、Finish、Grep、Glob、View工具
    """
    return [
        ThinkTool,    # 思考工具
        FinishTool,   # 完成工具
        GrepTool,     # 内容搜索工具
        GlobTool,     # 文件模式匹配工具
        ViewTool,     # 文件查看工具
    ]