from typing import Optional

from fastmcp import Client
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from mcp import McpError
from mcp.types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field

from openhands.core.config.mcp_config import MCPSHTTPServerConfig, MCPSSEServerConfig
from openhands.core.logger import openhands_logger as logger
from openhands.mcp.tool import MCPClientTool


class MCPClient(BaseModel):
    """
    MCP客户端类，用于连接MCP服务器并通过Model Context Protocol管理可用工具的集合。
    
    该类负责：
    - 建立与MCP服务器的连接
    - 获取并管理服务器端可用的工具列表
    - 提供工具调用的接口
    
    Attributes:
        client: FastMCP客户端实例，用于与MCP服务器通信
        description: 客户端描述信息
        tools: MCP客户端工具列表
        tool_map: 工具名称到工具对象的映射字典，用于快速查找
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    client: Optional[Client] = None
    """FastMCP客户端实例，用于与MCP服务器进行实际通信"""
    
    description: str = 'MCP client tools for server interaction'
    """客户端描述信息，说明该客户端用于服务器交互的MCP工具集合"""
    
    tools: list[MCPClientTool] = Field(default_factory=list)
    """MCP客户端工具列表，存储从服务器获取的所有可用工具"""
    
    tool_map: dict[str, MCPClientTool] = Field(default_factory=dict)
    """工具名称到工具对象的映射字典，提供基于工具名称的快速查找功能"""

    async def _initialize_and_list_tools(self) -> None:
        """
        初始化Session并填充工具映射字典。
        
        该方法负责：
        1. 验证客户端连接状态
        2. 从MCP服务器获取可用工具列表
        3. 清空现有工具列表
        4. 为每个服务器工具创建MCPClientTool对象
        5. 更新工具映射字典和工具列表
        
        Raises:
            RuntimeError: 当Session未初始化时抛出
        """
        if not self.client:
            raise RuntimeError('Session not initialized.')

        # 使用异步上下文管理器确保连接正确建立和关闭
        async with self.client:
            # 从MCP服务器获取可用工具列表
            tools = await self.client.list_tools()

        # 清空现有的工具列表，准备重新填充
        self.tools = []

        # 为每个服务器工具创建对应的客户端工具对象
        for tool in tools:
            # 创建MCPClientTool实例，包装服务器端工具的元数据
            server_tool = MCPClientTool(
                name=tool.name,                    # 工具名称
                description=tool.description,      # 工具描述
                inputSchema=tool.inputSchema,      # 输入参数Schema
                session=self.client,               # 客户端Session引用
            )
            # 将工具添加到映射字典中，以便按名称快速查找
            self.tool_map[tool.name] = server_tool
            # 将工具添加到工具列表中
            self.tools.append(server_tool)

        # 记录成功连接的服务器工具信息
        logger.info(f'Connected to server with tools: {[tool.name for tool in tools]}')

    async def connect_http(
        self,
        server: MCPSSEServerConfig | MCPSHTTPServerConfig,
        conversation_id: str | None = None,
        timeout: float = 30.0,
    ):
        """
        使用SHTTP或SSE传输协议连接到MCP服务器。
        
        该方法支持两种传输协议：
        - SHTTP: 流式HTTP传输
        - SSE: Server-Sent Events传输
        
        Args:
            server: MCP服务器配置，可以是SSE或SHTTP类型
            conversation_id: 可选的会话ID，用于标识特定的对话上下文
            timeout: 连接超时时间，默认30秒
            
        Raises:
            ValueError: 当服务器URL为空时抛出
            McpError: 当MCP连接发生错误时抛出
            Exception: 当连接过程中发生其他错误时抛出
        """
        server_url = server.url
        api_key = server.api_key

        # 验证服务器URL是否提供
        if not server_url:
            raise ValueError('Server URL is required.')

        try:
            # 构建请求头，包含认证和会话信息
            headers = (
                {
                    'Authorization': f'Bearer {api_key}',           # 标准Bearer token认证
                    's': api_key,                                   # Action execution server的MCP Router所需
                    'X-Session-API-Key': api_key,                  # Remote Runtime所需
                }
                if api_key
                else {}
            )

            # 如果提供了会话ID，将其添加到请求头中
            if conversation_id:
                headers['X-OpenHands-ServerConversation-ID'] = conversation_id

            # 根据服务器类型实例化相应的传输层
            # 由于需要自定义请求头，因此需要手动创建传输实例
            if isinstance(server, MCPSHTTPServerConfig):
                # 创建流式HTTP传输实例
                transport = StreamableHttpTransport(
                    url=server_url,
                    headers=headers if headers else None,
                )
            else:
                # 创建SSE传输实例
                transport = SSETransport(
                    url=server_url,
                    headers=headers if headers else None,
                )

            # 使用指定的传输层和超时时间创建客户端
            self.client = Client(transport, timeout=timeout)

            # 初始化连接并获取可用工具列表
            await self._initialize_and_list_tools()
            
        except McpError as e:
            # 记录MCP特定错误并重新抛出
            logger.error(f'McpError connecting to {server_url}: {e}')
            raise  # 重新抛出错误以便上层处理

        except Exception as e:
            # 记录一般性连接错误并重新抛出
            logger.error(f'Error connecting to {server_url}: {e}')
            raise

    async def call_tool(self, tool_name: str, args: dict) -> CallToolResult:
        """
        在MCP服务器上调用指定的工具。
        
        该方法负责：
        1. 验证工具是否存在于工具映射中
        2. 检查客户端连接状态
        3. 通过客户端Session调用服务器端工具
        
        Args:
            tool_name: 要调用的工具名称
            args: 传递给工具的参数字典
            
        Returns:
            CallToolResult: 工具调用的结果
            
        Raises:
            ValueError: 当指定的工具未找到时抛出
            RuntimeError: 当客户端Session不可用时抛出
            
        Note:
            MCPClientTool主要用于存储元数据，实际的工具调用通过Session进行
        """
        # 检查工具是否存在于工具映射中
        if tool_name not in self.tool_map:
            raise ValueError(f'Tool {tool_name} not found.')
            
        # 检查客户端Session是否可用
        # MCPClientTool主要用于元数据存储，实际工具调用需要使用Session
        if not self.client:
            raise RuntimeError('Client session is not available.')

        # 使用异步上下文管理器确保连接状态，并调用服务器端工具
        async with self.client:
            return await self.client.call_tool_mcp(name=tool_name, arguments=args)