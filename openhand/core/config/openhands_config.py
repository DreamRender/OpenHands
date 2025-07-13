import os
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from openhands.core import logger
from openhands.core.config.agent_config import AgentConfig
from openhands.core.config.cli_config import CLIConfig
from openhands.core.config.config_utils import (
    OH_DEFAULT_AGENT,
    OH_MAX_ITERATIONS,
    model_defaults_to_dict,
)
from openhands.core.config.extended_config import ExtendedConfig
from openhands.core.config.kubernetes_config import KubernetesConfig
from openhands.core.config.llm_config import LLMConfig
from openhands.core.config.mcp_config import MCPConfig
from openhands.core.config.sandbox_config import SandboxConfig
from openhands.core.config.security_config import SecurityConfig


class OpenHandsConfig(BaseModel):
    """应用程序的配置类。

    这是OpenHands的主配置类，包含了系统运行所需的所有配置选项。
    配置可以通过TOML文件、环境变量或程序参数进行设置。

    Attributes:
        llms: 将LLM名称映射到其配置的字典。默认配置存储在'llm'键下
        agents: 将Agent名称映射到其配置的字典。默认配置存储在'agent'键下
        default_agent: 要使用的默认Agent名称
        sandbox: Sandbox配置设置
        runtime: Runtime环境标识符
        file_store: 要使用的文件存储类型
        file_store_path: 文件存储的路径
        file_store_web_hook_url: 文件存储web hook的可选URL
        file_store_web_hook_headers: 文件存储web hook的可选headers
        enable_browser: 是否启用浏览器环境
        save_trajectory_path: 保存轨迹的文件夹路径（自动生成文件名）或指定的轨迹文件路径
        save_screenshots_in_trajectory: 是否在轨迹中保存截图（编码图像格式）
        replay_trajectory_path: 加载轨迹并重播的路径。如果提供，轨迹将在用户指令之前先重播
        search_api_key: Tavily搜索引擎的API密钥 (https://tavily.com/)
        workspace_base (已弃用): workspace的基础路径。默认为`./workspace`的绝对路径
        workspace_mount_path (已弃用): 挂载workspace的路径。默认为`workspace_base`
        workspace_mount_path_in_sandbox (已弃用): 在sandbox中挂载workspace的路径。默认为`/workspace`
        workspace_mount_rewrite (已弃用): 重写workspace挂载路径的路径
        cache_dir: 缓存目录路径。默认为`/tmp/cache`
        run_as_openhands: 是否以openhands身份运行
        max_iterations: 允许的最大迭代次数
        max_budget_per_task: 每个任务的最大预算，如果超过则Agent停止
        disable_color: 是否禁用终端颜色。适用于不支持颜色的终端
        debug: 是否启用调试模式
        file_uploads_max_file_size_mb: 文件上传的最大大小（MB）。`0`表示无限制
        file_uploads_restrict_file_types: 是否限制上传文件类型
        file_uploads_allowed_extensions: 允许的文件扩展名。`['.*']`允许所有类型
        cli_multiline_input: 是否在CLI中启用多行输入。禁用时，逐行读取输入。启用时，输入持续到/exit命令
        mcp_host: OpenHands默认MCP服务器的主机
        mcp: MCP配置设置
    """

    llms: dict[str, LLMConfig] = Field(default_factory=dict)
    """LLM配置字典，键为LLM名称，值为对应的LLMConfig对象"""
    
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    """Agent配置字典，键为Agent名称，值为对应的AgentConfig对象"""
    
    default_agent: str = Field(default=OH_DEFAULT_AGENT)
    """默认使用的Agent名称"""
    
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    """Sandbox运行环境的配置"""
    
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    """安全相关的配置设置"""
    
    extended: ExtendedConfig = Field(default_factory=lambda: ExtendedConfig({}))
    """扩展配置，允许用户自定义任意配置项"""
    
    runtime: str = Field(default='docker')
    """使用的runtime类型，默认为docker"""
    
    file_store: str = Field(default='local')
    """文件存储类型，默认为本地存储"""
    
    file_store_path: str = Field(default='~/.openhands')
    """文件存储的路径"""
    
    file_store_web_hook_url: str | None = Field(default=None)
    """文件存储web hook的URL，可选"""
    
    file_store_web_hook_headers: dict | None = Field(default=None)
    """文件存储web hook的请求头，可选"""
    
    enable_browser: bool = Field(default=True)
    """是否启用浏览器功能"""
    
    save_trajectory_path: str | None = Field(default=None)
    """保存轨迹的路径，可以是文件夹或具体文件路径"""
    
    save_screenshots_in_trajectory: bool = Field(default=False)
    """是否在轨迹中保存截图"""
    
    replay_trajectory_path: str | None = Field(default=None)
    """重播轨迹的文件路径"""
    
    search_api_key: SecretStr | None = Field(
        default=None,
        description='Tavily搜索引擎的API密钥 (https://tavily.com/)。搜索功能必需',
    )
    """搜索引擎API密钥，用于启用搜索功能"""

    # 已弃用的参数 - 将在未来版本中移除
    workspace_base: str | None = Field(default=None, deprecated=True)
    """工作空间基础路径（已弃用）"""
    
    workspace_mount_path: str | None = Field(default=None, deprecated=True)
    """工作空间挂载路径（已弃用）"""
    
    workspace_mount_path_in_sandbox: str = Field(default='/workspace', deprecated=True)
    """sandbox中的工作空间挂载路径（已弃用）"""
    
    workspace_mount_rewrite: str | None = Field(default=None, deprecated=True)
    """工作空间挂载路径重写规则（已弃用）"""
    # 已弃用参数结束

    cache_dir: str = Field(default='/tmp/cache')
    """缓存目录路径"""
    
    run_as_openhands: bool = Field(default=True)
    """是否以openhands用户身份运行"""
    
    max_iterations: int = Field(default=OH_MAX_ITERATIONS)
    """Agent执行的最大迭代次数"""
    
    max_budget_per_task: float | None = Field(default=None)
    """每个任务的最大预算限制"""

    disable_color: bool = Field(default=False)
    """是否禁用终端颜色输出"""
    
    jwt_secret: SecretStr | None = Field(default=None)
    """JWT认证密钥"""
    
    debug: bool = Field(default=False)
    """是否启用调试模式"""
    
    file_uploads_max_file_size_mb: int = Field(default=0)
    """文件上传最大大小限制（MB），0表示无限制"""
    
    file_uploads_restrict_file_types: bool = Field(default=False)
    """是否限制文件上传类型"""
    
    file_uploads_allowed_extensions: list[str] = Field(default_factory=lambda: ['.*'])
    """允许上传的文件扩展名列表"""

    cli_multiline_input: bool = Field(default=False)
    """CLI是否启用多行输入模式"""
    
    conversation_max_age_seconds: int = Field(default=864000)  # 10天的秒数
    """对话的最大保存时间（秒）"""
    
    enable_default_condenser: bool = Field(default=True)
    """是否启用默认的历史压缩器"""
    
    max_concurrent_conversations: int = Field(
        default=3
    )  # 每个用户允许的最大并发Agent循环数
    """每个用户允许的最大并发对话数"""
    
    mcp_host: str = Field(default=f'localhost:{os.getenv("port", 3000)}')
    """MCP服务器主机地址"""
    
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    """MCP（消息控制协议）配置"""
    
    kubernetes: KubernetesConfig = Field(default_factory=KubernetesConfig)
    """Kubernetes运行时配置"""
    
    cli: CLIConfig = Field(default_factory=CLIConfig)
    """CLI相关配置"""

    # 类变量：存储默认值字典
    defaults_dict: ClassVar[dict] = {}

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')

    def get_llm_config(self, name: str = 'llm') -> LLMConfig:
        """获取指定名称的LLM配置。
        
        'llm'是默认配置的名称（为了与0.8版本之前的向后兼容性）。
        
        Args:
            name: LLM配置的名称，默认为'llm'
            
        Returns:
            LLMConfig: 对应的LLM配置对象
        """
        if name in self.llms:
            return self.llms[name]
        if name is not None and name != 'llm':
            logger.openhands_logger.warning(
                f'llm config group {name} not found, using default config'
            )
        # 如果默认配置不存在，创建一个
        if 'llm' not in self.llms:
            self.llms['llm'] = LLMConfig()
        return self.llms['llm']

    def set_llm_config(self, value: LLMConfig, name: str = 'llm') -> None:
        """设置指定名称的LLM配置。
        
        Args:
            value: 要设置的LLMConfig对象
            name: LLM配置的名称，默认为'llm'
        """
        self.llms[name] = value

    def get_agent_config(self, name: str = 'agent') -> AgentConfig:
        """获取指定名称的Agent配置。
        
        'agent'是默认配置的名称（为了与0.8版本之前的向后兼容性）。
        
        Args:
            name: Agent配置的名称，默认为'agent'
            
        Returns:
            AgentConfig: 对应的Agent配置对象
        """
        if name in self.agents:
            return self.agents[name]
        # 如果默认配置不存在，创建一个
        if 'agent' not in self.agents:
            self.agents['agent'] = AgentConfig()
        return self.agents['agent']

    def set_agent_config(self, value: AgentConfig, name: str = 'agent') -> None:
        """设置指定名称的Agent配置。
        
        Args:
            value: 要设置的AgentConfig对象
            name: Agent配置的名称，默认为'agent'
        """
        self.agents[name] = value

    def get_agent_to_llm_config_map(self) -> dict[str, LLMConfig]:
        """获取Agent名称到LLM配置的映射。
        
        Returns:
            dict[str, LLMConfig]: Agent名称到对应LLM配置的映射字典
        """
        return {name: self.get_llm_config_from_agent(name) for name in self.agents}

    def get_llm_config_from_agent(self, name: str = 'agent') -> LLMConfig:
        """从Agent配置获取对应的LLM配置。
        
        Args:
            name: Agent配置的名称，默认为'agent'
            
        Returns:
            LLMConfig: Agent使用的LLM配置对象
        """
        agent_config: AgentConfig = self.get_agent_config(name)
        # 如果Agent配置中指定了LLM配置名称，使用指定的，否则使用默认的'llm'
        llm_config_name = (
            agent_config.llm_config if agent_config.llm_config is not None else 'llm'
        )
        return self.get_llm_config(llm_config_name)

    def get_agent_configs(self) -> dict[str, AgentConfig]:
        """获取所有Agent配置。
        
        Returns:
            dict[str, AgentConfig]: 所有Agent配置的字典
        """
        return self.agents

    def model_post_init(self, __context: Any) -> None:
        """模型初始化后的钩子函数。
        
        当实例仅使用默认值创建时调用。
        
        Args:
            __context: Pydantic上下文对象
        """
        super().model_post_init(__context)

        # 只有当defaults_dict为空时才设置默认值字典
        if not OpenHandsConfig.defaults_dict:
            OpenHandsConfig.defaults_dict = model_defaults_to_dict(self)