import json
from typing import TYPE_CHECKING

# 使用TYPE_CHECKING避免循环导入问题，只在类型检查时导入Agent
if TYPE_CHECKING:
    from openhands.controller.agent import Agent


from openhands.core.config.mcp_config import (
    MCPConfig,
    MCPSHTTPServerConfig,
    MCPSSEServerConfig,
)
from openhands.core.logger import openhands_logger as logger
from openhands.events.action.mcp import MCPAction
from openhands.events.observation.mcp import MCPObservation
from openhands.events.observation.observation import Observation
from openhands.mcp.client import MCPClient
from openhands.memory.memory import Memory
from openhands.runtime.base import Runtime


def convert_mcp_clients_to_tools(mcp_clients: list[MCPClient] | None) -> list[dict]:
    """
    将MCPClient实例列表转换为ChatCompletionToolParam格式，供CodeActAgent使用。
    
    该函数负责：
    1. 处理空客户端列表的情况
    2. 遍历所有MCP客户端
    3. 将每个客户端的工具转换为Agent可用的格式
    4. 统一收集所有工具并返回
    
    Args:
        mcp_clients: MCPClient实例列表，可能为None
        
    Returns:
        list[dict]: 转换后的工具字典列表，准备供CodeActAgent使用。
                   如果输入为None或转换过程中出错，返回空列表
                   
    Note:
        每个MCPClient都有一个tools属性，包含ToolCollection
        ToolCollection有to_params方法可将工具转换为ChatCompletionToolParam格式
    """
    # 处理空输入的情况
    if mcp_clients is None:
        logger.warning('mcp_clients is None, returning empty list')
        return []

    all_mcp_tools = []
    try:
        # 遍历所有MCP客户端
        for client in mcp_clients:
            # 每个MCPClient都有一个mcp_clients属性，它是一个ToolCollection
            # ToolCollection有一个to_params方法，可以将工具转换为ChatCompletionToolParam格式
            for tool in client.tools:
                # 将单个工具转换为参数格式
                mcp_tools = tool.to_param()
                # 将转换后的工具添加到总列表中
                all_mcp_tools.append(mcp_tools)
                
    except Exception as e:
        # 如果转换过程中出现任何错误，记录错误并返回空列表
        logger.error(f'Error in convert_mcp_clients_to_tools: {e}')
        return []
        
    return all_mcp_tools


async def create_mcp_clients(
    sse_servers: list[MCPSSEServerConfig],
    shttp_servers: list[MCPSHTTPServerConfig],
    conversation_id: str | None = None,
) -> list[MCPClient]:
    """
    创建MCP客户端列表，连接到指定的SSE和SHTTP服务器。
    
    该函数负责：
    1. 检查平台兼容性（Windows系统跳过）
    2. 合并SSE和SHTTP服务器配置
    3. 为每个服务器创建并初始化MCP客户端
    4. 处理连接错误并记录日志
    5. 返回成功连接的客户端列表
    
    Args:
        sse_servers: SSE服务器配置列表
        shttp_servers: SHTTP服务器配置列表  
        conversation_id: 可选的会话ID，用于关联MCP客户端
        
    Returns:
        list[MCPClient]: 成功连接的MCP客户端列表
        
    Note:
        - Windows平台上MCP功能被禁用，会返回空列表
        - 连接失败的服务器不会被添加到返回列表中
        - 每个服务器的连接状态都会被记录
    """
    import sys

    # 在Windows平台上跳过MCP客户端创建
    if sys.platform == 'win32':
        logger.info(
            'MCP functionality is disabled on Windows, skipping client creation'
        )
        return []

    # 合并SSE和SHTTP服务器配置列表
    servers: list[MCPSSEServerConfig | MCPSHTTPServerConfig] = [
        *sse_servers,
        *shttp_servers,
    ]

    # 如果没有配置任何服务器，直接返回空列表
    if not servers:
        return []

    mcp_clients = []

    # 遍历所有服务器配置，尝试建立连接
    for server in servers:
        # 判断服务器类型，用于日志记录
        is_shttp = isinstance(server, MCPSHTTPServerConfig)
        connection_type = 'SHTTP' if is_shttp else 'SSE'
        logger.info(
            f'Initializing MCP agent for {server} with {connection_type} connection...'
        )
        
        # 创建新的MCP客户端实例
        client = MCPClient()

        try:
            # 尝试连接到服务器
            await client.connect_http(server, conversation_id=conversation_id)

            # 只有连接成功后才将客户端添加到列表中
            mcp_clients.append(client)

        except Exception as e:
            # 记录连接失败的详细信息，包括异常堆栈
            logger.error(f'Failed to connect to {server}: {str(e)}', exc_info=True)

    return mcp_clients


async def fetch_mcp_tools_from_config(
    mcp_config: MCPConfig, conversation_id: str | None = None
) -> list[dict]:
    """
    从MCP配置中获取MCP工具列表。
    
    该函数负责完整的MCP工具获取流程：
    1. 检查平台兼容性
    2. 根据配置创建MCP客户端
    3. 将客户端工具转换为Agent可用格式
    4. 处理错误并提供容错机制
    
    Args:
        mcp_config: MCP配置对象，包含服务器连接信息
        conversation_id: 可选的会话ID，用于与MCP客户端关联
        
    Returns:
        list[dict]: 工具字典列表。如果无法建立连接则返回空列表
        
    Note:
        - 该函数会获取工具但不维持活跃连接
        - Windows平台上会跳过工具获取
        - 所有错误都会被捕获并记录，不会中断执行
    """
    import sys

    # 在Windows平台上跳过MCP工具获取
    if sys.platform == 'win32':
        logger.info('MCP functionality is disabled on Windows, skipping tool fetching')
        return []

    mcp_clients = []
    mcp_tools = []
    try:
        logger.debug(f'Creating MCP clients with config: {mcp_config}')
        
        # 创建客户端 - 这会获取工具但不维持活跃连接
        mcp_clients = await create_mcp_clients(
            mcp_config.sse_servers, mcp_config.shttp_servers, conversation_id
        )

        # 如果没有成功连接的客户端，返回空列表
        if not mcp_clients:
            logger.debug('No MCP clients were successfully connected')
            return []

        # 将工具转换为Agent期望的格式
        mcp_tools = convert_mcp_clients_to_tools(mcp_clients)

    except Exception as e:
        # 记录获取MCP工具时的错误
        logger.error(f'Error fetching MCP tools: {str(e)}')
        return []

    logger.debug(f'MCP tools: {mcp_tools}')
    return mcp_tools


async def call_tool_mcp(mcp_clients: list[MCPClient], action: MCPAction) -> Observation:
    """
    在MCP服务器上调用工具并返回Observation。
    
    该函数负责：
    1. 平台兼容性检查
    2. 验证MCP客户端可用性
    3. 根据工具名称查找匹配的客户端
    4. 执行工具调用并处理响应
    5. 将结果包装为MCPObservation返回
    
    Args:
        mcp_clients: MCP客户端列表，用于执行Action
        action: 要执行的MCP Action
        
    Returns:
        Observation: 来自MCP服务器的Observation
        
    Raises:
        ValueError: 当没有找到MCP客户端或匹配的工具时抛出
        
    Note:
        - Windows平台会返回错误Observation
        - 工具调用会内部创建新连接
        - 响应会被序列化为JSON格式
    """
    import sys

    from openhands.events.observation import ErrorObservation

    # 在Windows平台上跳过MCP工具调用
    if sys.platform == 'win32':
        logger.info('MCP functionality is disabled on Windows')
        return ErrorObservation('MCP functionality is not available on Windows')

    # 验证是否有可用的MCP客户端
    if not mcp_clients:
        raise ValueError('No MCP clients found')

    logger.debug(f'MCP action received: {action}')

    # 查找具有匹配工具名称的MCP客户端
    matching_client = None
    logger.debug(f'MCP clients: {mcp_clients}')
    logger.debug(f'MCP action name: {action.name}')

    # 遍历所有客户端，寻找包含指定工具的客户端
    for client in mcp_clients:
        logger.debug(f'MCP client tools: {client.tools}')
        # 检查客户端是否包含所需的工具
        if action.name in [tool.name for tool in client.tools]:
            matching_client = client
            break

    # 如果没有找到匹配的客户端，抛出错误
    if matching_client is None:
        raise ValueError(f'No matching MCP agent found for tool name: {action.name}')

    logger.debug(f'Matching client: {matching_client}')

    # 调用工具 - 这会在内部创建新的连接
    response = await matching_client.call_tool(action.name, action.arguments)
    logger.debug(f'MCP response: {response}')

    # 将响应包装为MCPObservation并返回
    return MCPObservation(
        content=json.dumps(response.model_dump(mode='json')),  # 将响应序列化为JSON字符串
        name=action.name,                                      # 工具名称
        arguments=action.arguments,                            # 调用参数
    )


async def add_mcp_tools_to_agent(agent: 'Agent', runtime: Runtime, memory: 'Memory'):
    """
    向Agent添加MCP工具。
    
    该函数负责完整的MCP工具集成流程：
    1. 平台兼容性检查
    2. 验证Runtime初始化状态
    3. 从Memory获取MicroAgent MCP配置
    4. 合并额外的stdio服务器
    5. 获取Runtime的MCP配置
    6. 获取并设置MCP工具到Agent
    
    Args:
        agent: 要添加工具的Agent实例
        runtime: Runtime实例，必须已初始化
        memory: Memory实例，用于获取MicroAgent配置
        
    Note:
        - Windows平台会跳过MCP工具添加
        - Runtime必须在调用前已初始化
        - 支持MicroAgent提供的额外MCP工具
        - SSE服务器在MicroAgent配置中暂不支持
    """
    import sys

    # 在Windows平台上跳过MCP工具添加
    if sys.platform == 'win32':
        logger.info('MCP functionality is disabled on Windows, skipping MCP tools')
        agent.set_mcp_tools([])
        return

    # 确保Runtime已经初始化后再添加MCP工具
    assert runtime.runtime_initialized, (
        'Runtime must be initialized before adding MCP tools'
    )

    extra_stdio_servers = []

    # 添加MicroAgent MCP工具（如果可用）
    microagent_mcp_configs = memory.get_microagent_mcp_tools()
    for mcp_config in microagent_mcp_configs:
        # 检查并警告不支持的SSE服务器配置
        if mcp_config.sse_servers:
            logger.warning(
                'Microagent MCP config contains SSE servers, it is not yet supported.'
            )

        # 处理stdio服务器配置
        if mcp_config.stdio_servers:
            for stdio_server in mcp_config.stdio_servers:
                # 检查该stdio服务器是否已经在配置中
                if stdio_server not in extra_stdio_servers:
                    extra_stdio_servers.append(stdio_server)
                    logger.info(f'Added microagent stdio server: {stdio_server.name}')

    # 将Runtime作为另一个MCP服务器添加
    updated_mcp_config = runtime.get_mcp_config(extra_stdio_servers)

    # 获取MCP工具
    mcp_tools = await fetch_mcp_tools_from_config(updated_mcp_config)

    # 记录加载的工具信息
    logger.info(
        f'Loaded {len(mcp_tools)} MCP tools: {[tool["function"]["name"] for tool in mcp_tools]}'
    )

    # 在Agent上设置MCP工具
    agent.set_mcp_tools(mcp_tools)