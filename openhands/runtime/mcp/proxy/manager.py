"""
OpenHands的MCP Proxy Manager模块。

该模块提供了一个管理类，用于处理FastMCP proxy实例，
包括初始化、配置以及挂载到FastAPI应用程序的功能。

MCP (Model Context Protocol) 是一个用于AI Agent与外部工具和资源交互的协议。
FastMCP是MCP协议的一个快速实现，支持通过HTTP和SSE (Server-Sent Events) 进行通信。
"""

import logging
from typing import Any, Optional

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger as fastmcp_get_logger

from openhands.core.config.mcp_config import MCPStdioServerConfig

# 全局变量：当前模块的日志记录器
logger = logging.getLogger(__name__)

# 全局变量：FastMCP框架专用的日志记录器
fastmcp_logger = fastmcp_get_logger('fastmcp')


class MCPProxyManager:
    """
    FastMCP proxy实例的管理器。

    该类封装了与创建、配置和管理FastMCP proxy实例相关的所有功能，
    包括将它们挂载到FastAPI应用程序的能力。
    
    FastMCP proxy作为OpenHands Agent与外部MCP服务器之间的中间层，
    负责路由请求、处理认证以及管理连接状态。
    
    Attributes:
        auth_enabled (bool): 是否启用认证功能
        api_key (Optional[str]): 用于认证的API密钥
        proxy (Optional[FastMCP]): FastMCP proxy实例
        config (dict[str, Any]): MCP服务器的配置字典
    """

    def __init__(
        self,
        auth_enabled: bool = False,
        api_key: Optional[str] = None,
        logger_level: Optional[int] = None,
    ):
        """
        初始化MCP Proxy Manager。

        Args:
            auth_enabled (bool, optional): 是否启用认证功能。默认为False。
                当设置为True时，需要提供有效的api_key进行身份验证。
            api_key (Optional[str], optional): 用于认证的API密钥。
                如果auth_enabled为True，则此参数为必需。默认为None。
            logger_level (Optional[int], optional): FastMCP日志记录器的日志级别。
                可以使用logging模块中的标准级别常量（如logging.DEBUG, logging.INFO等）。
                默认为None，表示使用FastMCP的默认日志级别。
        
        Note:
            如果启用了认证但未提供api_key，在后续的proxy初始化过程中可能会出错。
        """
        # 存储认证相关配置
        self.auth_enabled = auth_enabled
        self.api_key = api_key
        
        # FastMCP proxy实例，初始化时为None，需要调用initialize()方法创建
        self.proxy: Optional[FastMCP] = None
        
        # 初始化为FastMCP所需的有效配置格式
        # mcpServers字段用于存储所有配置的MCP服务器信息
        self.config: dict[str, Any] = {
            'mcpServers': {},  # 存储MCP服务器配置的字典，键为服务器名称，值为配置信息
        }

        # 配置FastMCP框架的日志记录器级别
        if logger_level is not None:
            fastmcp_logger.setLevel(logger_level)

    def initialize(self) -> None:
        """
        使用当前配置初始化FastMCP proxy。
        
        该方法会检查是否有配置的MCP服务器，如果没有则跳过初始化。
        如果有配置的服务器，则创建新的FastMCP proxy实例。
        
        Returns:
            None
            
        Note:
            - 如果没有配置任何MCP服务器，该方法会记录信息并直接返回None
            - 成功初始化后，self.proxy将包含可用的FastMCP实例
            - 该方法可以被多次调用以重新初始化proxy
        """
        # 检查是否配置了任何MCP服务器
        if len(self.config['mcpServers']) == 0:
            logger.info(
                '没有为FastMCP Proxy配置MCP服务器，跳过初始化。'
            )
            return None

        # 使用当前配置创建新的proxy实例
        # as_proxy方法创建一个代理模式的FastMCP实例，
        # 该实例可以转发请求到配置的MCP服务器
        self.proxy = FastMCP.as_proxy(
            self.config,  # 包含mcpServers配置的字典
            auth_enabled=self.auth_enabled,  # 是否启用认证
            api_key=self.api_key,  # 认证使用的API密钥
        )

        logger.info('FastMCP Proxy初始化成功')

    async def mount_to_app(
        self, app: FastAPI, allow_origins: Optional[list[str]] = None
    ) -> None:
        """
        将SSE服务器应用挂载到FastAPI应用程序。

        该方法将FastMCP proxy的HTTP应用挂载到提供的FastAPI应用实例上，
        使得客户端可以通过HTTP和SSE (Server-Sent Events) 与MCP服务器通信。

        Args:
            app (FastAPI): 要挂载到的FastAPI应用程序实例
            allow_origins (Optional[list[str]], optional): CORS允许的源列表。
                用于配置跨域资源共享策略。默认为None。

        Raises:
            ValueError: 如果FastMCP Proxy未初始化（self.proxy为None）
            
        Note:
            - 如果没有配置MCP服务器，方法会记录信息并直接返回
            - 应用会被挂载在'/mcp'路径和根路径'/'上
            - 使用SSE传输方式，路径为'/sse'
        """
        # 检查是否有配置的MCP服务器
        if len(self.config['mcpServers']) == 0:
            logger.info('没有为FastMCP Proxy配置MCP服务器，跳过挂载。')
            return

        # 确保proxy已经初始化
        if not self.proxy:
            raise ValueError('FastMCP Proxy未初始化')

        # 获取SSE应用实例
        # 注释掉的行是HTTP传输方式的配置
        # mcp_app = self.proxy.http_app(path='/shttp')
        
        # 创建基于SSE (Server-Sent Events) 传输的HTTP应用
        # SSE允许服务器向客户端推送实时数据，适合MCP协议的双向通信需求
        mcp_app = self.proxy.http_app(path='/sse', transport='sse')
        
        # 将MCP应用挂载到'/mcp'路径
        app.mount('/mcp', mcp_app)

        # 移除根路径上可能存在的'/mcp'路由冲突
        # 这是一个防护措施，确保路由不会重复
        if '/mcp' in app.routes:
            app.routes.remove('/mcp')

        # 将MCP应用同时挂载到根路径，提供更直接的访问方式
        app.mount('/', mcp_app)
        logger.info('在/mcp路径挂载FastMCP Proxy应用成功')

    async def update_and_remount(
        self,
        app: FastAPI,
        stdio_servers: list[MCPStdioServerConfig],
        allow_origins: Optional[list[str]] = None,
    ) -> None:
        """
        更新工具配置并重新挂载proxy到应用程序。

        这是一个便捷方法，结合了更新工具配置、关闭现有proxy、
        初始化新proxy以及将其挂载到应用程序的操作。
        
        该方法通常在运行时动态更新MCP服务器配置时使用，
        允许在不重启整个应用的情况下重新配置proxy。

        Args:
            app (FastAPI): 要挂载到的FastAPI应用程序实例
            stdio_servers (list[MCPStdioServerConfig]): MCP标准输入输出服务器配置列表。
                每个配置包含服务器的名称、命令、参数等信息。
            allow_origins (Optional[list[str]], optional): CORS允许的源列表。
                默认为None。

        Note:
            - 该方法会完全替换现有的MCP服务器配置
            - 旧的proxy实例会被销毁并创建新的实例
            - 操作过程中可能会有短暂的服务中断
        """
        # 将MCPStdioServerConfig对象列表转换为配置字典
        # 使用服务器名称作为键，配置数据作为值
        tools = {t.name: t.model_dump() for t in stdio_servers}
        
        # 更新配置中的MCP服务器信息
        self.config['mcpServers'] = tools

        # 清理现有的proxy实例
        # 先删除引用，然后将其设置为None以确保垃圾回收
        del self.proxy
        self.proxy = None

        # 使用新配置初始化新的proxy实例
        self.initialize()

        # 将新的proxy挂载到应用程序
        await self.mount_to_app(app, allow_origins)