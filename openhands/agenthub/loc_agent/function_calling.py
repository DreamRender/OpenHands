"""此文件包含不同Action的函数调用实现。

这类似于 `CodeActResponseParser` 的功能。
该模块负责将Model的响应转换为OpenHands框架中的Action对象，
支持工具调用和消息处理的统一接口。
"""

import json

from litellm import (
    ChatCompletionToolParam,
    ModelResponse,
)

from openhands.agenthub.codeact_agent.function_calling import combine_thought
from openhands.agenthub.codeact_agent.tools import FinishTool
from openhands.agenthub.loc_agent.tools import (
    SearchEntityTool,
    SearchRepoTool,
    create_explore_tree_structure_tool,
)
from openhands.core.exceptions import (
    FunctionCallNotExistsError,
)
from openhands.core.logger import openhands_logger as logger
from openhands.events.action import (
    Action,
    AgentFinishAction,
    IPythonRunCellAction,
    MessageAction,
)
from openhands.events.tool import ToolCallMetadata


def response_to_actions(
    response: ModelResponse,
    mcp_tool_names: list[str] | None = None,
) -> list[Action]:
    """将Model响应转换为Action列表。
    
    此函数处理LLM的响应并将其转换为OpenHands框架中的Action对象。
    支持工具调用和纯文本消息两种类型的响应处理。
    
    Args:
        response (ModelResponse): LLM的响应对象，包含选择列表和消息内容
        mcp_tool_names (list[str] | None, optional): MCP工具名称列表。
            当前未使用，预留用于未来的MCP工具集成。默认值为 None。
    
    Returns:
        list[Action]: 转换后的Action对象列表，可能包含：
            - IPythonRunCellAction: 执行Python代码的Action
            - AgentFinishAction: Agent完成任务的Action
            - MessageAction: 纯消息Action
    
    Raises:
        AssertionError: 当响应中的选择数量不为1时抛出
        RuntimeError: 当工具调用参数解析失败时抛出
        FunctionCallNotExistsError: 当调用的工具不存在时抛出
    
    Note:
        - 目前只支持单选择响应（len(response.choices) == 1）
        - 工具调用会被转换为IPythonRunCellAction在Jupyter中执行
        - 只有第一个Action会包含思考内容（thought）
        - 所有Action都会添加工具调用Metadata和响应ID
    """
    actions: list[Action] = []  # 初始化Action列表
    
    # 验证响应格式：目前只支持单选择响应
    assert len(response.choices) == 1, 'Only one choice is supported for now'
    choice = response.choices[0]  # 获取响应选择
    assistant_msg = choice.message  # 获取助手消息

    # 处理包含工具调用的响应
    if hasattr(assistant_msg, 'tool_calls') and assistant_msg.tool_calls:
        # 提取思考内容：检查是否有assistant_msg.content，如果有则添加到思考中
        thought = ''
        if isinstance(assistant_msg.content, str):
            # 如果内容是字符串，直接使用
            thought = assistant_msg.content
        elif isinstance(assistant_msg.content, list):
            # 如果内容是列表，提取所有文本类型的内容
            for msg in assistant_msg.content:
                if msg['type'] == 'text':
                    thought += msg['text']

        # 处理每个工具调用并转换为OpenHands Action
        for i, tool_call in enumerate(assistant_msg.tool_calls):
            action: Action  # 声明Action变量
            logger.debug(f'Tool call in function_calling.py: {tool_call}')
            
            # 解析工具调用参数
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.decoder.JSONDecodeError as e:
                raise RuntimeError(
                    f'Failed to parse tool call arguments: {tool_call.function.arguments}'
                ) from e

            # ================================================
            # LocAgent的工具处理
            # ================================================
            # 定义LocAgent支持的所有函数名称
            ALL_FUNCTIONS = [
                'explore_tree_structure',    # 探索树结构
                'search_code_snippets',      # 搜索代码片段
                'get_entity_contents',       # 获取实体内容
            ]
            
            if tool_call.function.name in ALL_FUNCTIONS:
                # 在agent_skills中实现这些功能，可以通过Jupyter使用
                func_name = tool_call.function.name
                # 生成Python代码字符串，在Jupyter中执行
                code = f'print({func_name}(**{arguments}))'
                logger.debug(f'TOOL CALL: {func_name} with code: {code}')
                # 创建IPython运行单元格Action
                action = IPythonRunCellAction(code=code)

            # ================================================
            # AgentFinishAction处理
            # ================================================
            elif tool_call.function.name == FinishTool['function']['name']:
                # 创建Agent完成Action
                action = AgentFinishAction(
                    final_thought=arguments.get('message', ''),  # 最终思考内容
                    task_completed=arguments.get('task_completed', None),  # 任务是否完成
                )
            else:
                # 抛出工具不存在异常
                raise FunctionCallNotExistsError(
                    f'Tool {tool_call.function.name} is not registered. (arguments: {arguments}). Please check the tool name and retry with an existing tool.'
                )

            # 只为第一个Action添加思考内容
            if i == 0:
                action = combine_thought(action, thought)
                
            # 为Action添加工具调用Metadata
            action.tool_call_metadata = ToolCallMetadata(
                tool_call_id=tool_call.id,  # 工具调用ID
                function_name=tool_call.function.name,  # 函数名称
                model_response=response,  # Model响应
                total_calls_in_response=len(assistant_msg.tool_calls),  # 响应中的总调用数
            )
            actions.append(action)  # 添加到Action列表
    else:
        # 处理不包含工具调用的纯消息响应
        actions.append(
            MessageAction(
                content=str(assistant_msg.content) if assistant_msg.content else '',  # 消息内容
                wait_for_response=True,  # 等待响应
            )
        )

    # 为所有Action添加响应ID
    # 这确保我们可以将没有工具调用的Action（如MessageAction）
    # 和有工具调用的Action（如CmdRunAction、IPythonRunCellAction等）
    # 与Token使用数据进行匹配
    for action in actions:
        action.response_id = response.id

    # 确保至少有一个Action
    assert len(actions) >= 1
    return actions


def get_tools() -> list[ChatCompletionToolParam]:
    """获取LocAgent支持的工具列表。
    
    此函数返回LocAgent支持的所有聊天完成工具参数配置。
    这些工具用于与LLM进行函数调用交互，实现代码分析和搜索功能。
    
    Returns:
        list[ChatCompletionToolParam]: 工具参数配置列表，包含：
            - FinishTool: 完成任务的工具
            - SearchRepoTool: 搜索Repository代码片段的工具
            - SearchEntityTool: 搜索实体内容的工具
            - explore_tree_structure工具: 探索代码树结构的工具（简化版描述）
    
    Note:
        - 所有工具都支持函数调用格式
        - explore_tree_structure工具使用简化版描述以提高可用性
        - 工具的具体实现在agent_skills模块中
    """
    tools = [FinishTool]  # 初始化工具列表，包含完成工具
    
    # 添加搜索Repository工具
    tools.append(SearchRepoTool)
    
    # 添加搜索实体工具
    tools.append(SearchEntityTool)
    
    # 添加探索树结构工具（使用简化版描述）
    tools.append(create_explore_tree_structure_tool(use_simplified_description=True))
    
    return tools
