import os
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

if TYPE_CHECKING:
    from openhands.core.config.openhands_config import OpenHandsConfig

from openhands.core.logger import openhands_logger as logger
from openhands.utils.import_utils import get_impl


class MCPSSEServerConfig(BaseModel):
    """单个MCP服务器的配置类。
    
    用于配置基于Server-Sent Events (SSE) 的MCP服务器连接。

    Attributes:
        url: 服务器URL地址
        api_key: 用于身份验证的可选API密钥
    """

    url: str
    """MCP服务器的URL地址"""
    
    api_key: str | None = None
    """可选的API密钥，用于服务器身份验证"""


class MCPStdioServerConfig(BaseModel):
    """使用stdio的MCP服务器配置类。
    
    用于配置通过标准输入输出进行通信的MCP服务器。

    Attributes:
        name: 服务器名称
        command: 运行服务器的命令
        args: 传递给服务器的参数
        env: 为服务器设置的环境变量
    """

    name: str
    """服务器的唯一名称标识符"""
    
    command: str
    """启动服务器的命令"""
    
    args: list[str] = Field(default_factory=list)
    """传递给服务器命令的参数列表"""
    
    env: dict[str, str] = Field(default_factory=dict)
    """为服务器进程设置的环境变量字典"""

    def __eq__(self, other):
        """重写相等运算符以比较服务器配置。

        两个服务器配置如果具有相同的名称、命令、参数和环境变量值则被认为是相等的。
        参数的顺序很重要，但环境变量的顺序不重要。
        
        Args:
            other: 要比较的另一个对象
            
        Returns:
            bool: 如果配置相等则返回True，否则返回False
        """
        if not isinstance(other, MCPStdioServerConfig):
            return False
        return (
            self.name == other.name
            and self.command == other.command
            and self.args == other.args
            and set(self.env.items()) == set(other.env.items())
        )


class MCPSHTTPServerConfig(BaseModel):
    """基于HTTP的MCP服务器配置类。
    
    用于配置通过HTTP协议通信的MCP服务器。
    """
    
    url: str
    """HTTP服务器的URL地址"""
    
    api_key: str | None = None
    """可选的API密钥，用于服务器身份验证"""


class MCPConfig(BaseModel):
    """MCP（消息控制协议）设置的配置类。
    
    这个类管理所有类型的MCP服务器配置，包括SSE、stdio和HTTP服务器。
    MCP用于扩展Agent的功能，允许连接外部工具和服务。

    Attributes:
        sse_servers: MCP SSE服务器配置列表
        stdio_servers: MCP stdio服务器配置列表。这些服务器将添加到runtime容器内运行的MCP Router中
        shttp_servers: MCP HTTP服务器配置列表
    """

    sse_servers: list[MCPSSEServerConfig] = Field(default_factory=list)
    """基于Server-Sent Events的MCP服务器配置列表"""
    
    stdio_servers: list[MCPStdioServerConfig] = Field(default_factory=list)
    """基于标准输入输出的MCP服务器配置列表"""
    
    shttp_servers: list[MCPSHTTPServerConfig] = Field(default_factory=list)
    """基于HTTP协议的MCP服务器配置列表"""

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')

    @staticmethod
    def _normalize_servers(servers_data: list[dict | str]) -> list[dict]:
        """规范化SSE服务器配置的辅助方法。
        
        将字符串URL转换为包含url字段的字典格式。
        
        Args:
            servers_data: 服务器配置数据列表，可以是字符串或字典
            
        Returns:
            list[dict]: 规范化后的服务器配置字典列表
        """
        normalized = []
        for server in servers_data:
            if isinstance(server, str):
                # 如果是字符串，转换为字典格式
                normalized.append({'url': server})
            else:
                # 如果已经是字典，直接添加
                normalized.append(server)
        return normalized

    @model_validator(mode='before')
    def convert_string_urls(cls, data):
        """将字符串URL转换为MCPSSEServerConfig对象。
        
        这个验证器在模型实例化之前运行，处理简化的配置格式。
        
        Args:
            data: 原始配置数据
            
        Returns:
            dict: 处理后的配置数据
        """
        if isinstance(data, dict):
            if 'sse_servers' in data:
                # 规范化SSE服务器配置
                data['sse_servers'] = cls._normalize_servers(data['sse_servers'])

            if 'shttp_servers' in data:
                # 规范化HTTP服务器配置
                data['shttp_servers'] = cls._normalize_servers(data['shttp_servers'])

        return data

    def validate_servers(self) -> None:
        """验证服务器URL是否有效且唯一。
        
        检查所有SSE服务器的URL格式和唯一性。
        
        Raises:
            ValueError: 当URL无效或重复时抛出
        """
        urls = [server.url for server in self.sse_servers]

        # 检查重复的服务器URL
        if len(set(urls)) != len(urls):
            raise ValueError('Duplicate MCP server URLs are not allowed')

        # 验证URL格式
        for url in urls:
            try:
                result = urlparse(url)
                if not all([result.scheme, result.netloc]):
                    raise ValueError(f'Invalid URL format: {url}')
            except Exception as e:
                raise ValueError(f'Invalid URL {url}: {str(e)}')

    @classmethod
    def from_toml_section(cls, data: dict) -> dict[str, 'MCPConfig']:
        """从表示[mcp]部分的toml字典创建MCPConfig实例的映射。

        配置是从data中的所有键构建的。

        Args:
            data: 包含MCP配置数据的字典
            
        Returns:
            dict[str, MCPConfig]: 一个映射，其中键"mcp"对应[mcp]配置
            
        Raises:
            ValueError: 当MCP配置无效时抛出
        """
        # 初始化结果映射
        mcp_mapping: dict[str, MCPConfig] = {}

        try:
            # 将sse_servers中的所有条目转换为MCPSSEServerConfig对象
            if 'sse_servers' in data:
                data['sse_servers'] = cls._normalize_servers(data['sse_servers'])
                servers: list[
                    MCPSSEServerConfig | MCPStdioServerConfig | MCPSHTTPServerConfig
                ] = []
                for server in data['sse_servers']:
                    servers.append(MCPSSEServerConfig(**server))
                data['sse_servers'] = servers

            # 将stdio_servers中的所有条目转换为MCPStdioServerConfig对象
            if 'stdio_servers' in data:
                servers = []
                for server in data['stdio_servers']:
                    servers.append(MCPStdioServerConfig(**server))
                data['stdio_servers'] = servers

            # 将shttp_servers中的所有条目转换为MCPSHTTPServerConfig对象
            if 'shttp_servers' in data:
                data['shttp_servers'] = cls._normalize_servers(data['shttp_servers'])
                servers = []
                for server in data['shttp_servers']:
                    servers.append(MCPSHTTPServerConfig(**server))
                data['shttp_servers'] = servers

            # 如果存在则创建SSE配置
            mcp_config = MCPConfig.model_validate(data)
            mcp_config.validate_servers()

            # 创建主要的MCP配置
            mcp_mapping['mcp'] = cls(
                sse_servers=mcp_config.sse_servers,
                stdio_servers=mcp_config.stdio_servers,
                shttp_servers=mcp_config.shttp_servers,
            )
        except ValidationError as e:
            raise ValueError(f'Invalid MCP configuration: {e}')
        return mcp_mapping


class OpenHandsMCPConfig:
    """OpenHands特定的MCP配置管理类。
    
    提供静态方法来创建和管理OpenHands环境中的MCP服务器配置。
    """
    
    @staticmethod
    def add_search_engine(app_config: 'OpenHandsConfig') -> MCPStdioServerConfig | None:
        """向MCP配置添加搜索引擎。
        
        如果配置了有效的Tavily API密钥，则添加搜索引擎服务器。
        
        Args:
            app_config: OpenHands应用配置对象
            
        Returns:
            MCPStdioServerConfig | None: 搜索引擎服务器配置，如果未配置则返回None
        """
        if (
            app_config.search_api_key
            and app_config.search_api_key.get_secret_value().startswith('tvly-')
        ):
            logger.info('Adding search engine to MCP config')
            return MCPStdioServerConfig(
                name='tavily',
                command='npx',
                args=['-y', 'tavily-mcp@0.2.1'],
                env={'TAVILY_API_KEY': app_config.search_api_key.get_secret_value()},
            )
        else:
            logger.warning('No search engine API key found, skipping search engine')
        # 在SaaS模式下不添加搜索引擎到MCP配置，因为它将由OpenHands服务器添加
        return None

    @staticmethod
    def create_default_mcp_server_config(
        host: str, config: 'OpenHandsConfig', user_id: str | None = None
    ) -> tuple[MCPSHTTPServerConfig, list[MCPStdioServerConfig]]:
        """创建默认的MCP服务器配置。

        Args:
            host: 主机字符串
            config: OpenHandsConfig配置对象
            user_id: 可选的用户ID
            
        Returns:
            tuple[MCPSHTTPServerConfig, list[MCPStdioServerConfig]]: 
            包含默认HTTP服务器配置和MCP stdio服务器配置列表的元组
        """
        stdio_servers = []
        search_engine_stdio_server = OpenHandsMCPConfig.add_search_engine(config)
        if search_engine_stdio_server:
            stdio_servers.append(search_engine_stdio_server)

        shttp_servers = MCPSHTTPServerConfig(url=f'http://{host}/mcp/mcp', api_key=None)
        return shttp_servers, stdio_servers


# 从环境变量获取MCP配置类的实现
openhands_mcp_config_cls = os.environ.get(
    'OPENHANDS_MCP_CONFIG_CLS',
    'openhands.core.config.mcp_config.OpenHandsMCPConfig',
)

# 动态获取MCP配置类的实现
OpenHandsMCPConfigImpl = get_impl(OpenHandsMCPConfig, openhands_mcp_config_cls)