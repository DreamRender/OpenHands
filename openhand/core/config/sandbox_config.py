import os

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class SandboxConfig(BaseModel):
    """Sandbox的配置类。

    这个类定义了Sandbox运行环境的各种配置参数，包括远程和本地runtime设置、
    容器配置、资源限制、网络设置、安全选项等。Sandbox为Agent提供隔离的
    执行环境，确保代码执行的安全性和可控性。

    Attributes:
        remote_runtime_api_url: Remote Runtime API的主机名
        local_runtime_url: 本地runtime的默认主机名。在DIND环境中可能需要更改为http://host.docker.internal
        base_container_image: 构建runtime镜像的基础容器镜像
        runtime_container_image: 要使用的runtime容器镜像
        user_id: Sandbox的用户ID
        timeout: 默认Sandbox Action执行的超时时间
        remote_runtime_init_timeout: 远程runtime启动的超时时间
        remote_runtime_api_timeout: 远程runtime API请求的超时时间
        remote_runtime_enable_retries: 是否为远程runtime API请求启用重试（对于可恢复错误如requests.ConnectionError）
        enable_auto_lint: 是否启用自动lint
        use_host_network: 是否使用主机网络
        runtime_binding_address: runtime端口的绑定地址。它指定Docker应该将runtime端口绑定到主机上的哪个网络接口
        initialize_plugins: 是否初始化插件
        force_rebuild_runtime: 是否强制重建runtime镜像
        runtime_extra_deps: 在runtime镜像中安装的额外依赖（通常用于评估）。这将渲染到构建runtime镜像的Dockerfile的末尾。它可以包含任何有效的shell命令（例如pip install numpy）。解释器的路径可用作$OH_INTERPRETER_PATH，可用于为OH特定的Python解释器安装依赖
        runtime_startup_env_vars: 在runtime启动时设置的环境变量。这是一个键值对字典。这对于设置runtime需要的环境变量很有用。例如，为browsergym评估指定网站的基础URL
        browsergym_eval_env: 用于评估的BrowserGym环境。默认为None表示通用浏览。查看evaluation/miniwob和evaluation/webarena的示例
        platform: 应该构建镜像的平台。默认为None
        remote_runtime_resource_factor: 缩放远程runtime资源分配的因子。必须是[1, 2, 4, 8]之一。仅当runtime是远程时才会使用
        enable_gpu: 是否启用GPU
        docker_runtime_kwargs: 运行容器时传递给Docker runtime的额外关键字参数。这应该是一个Python字典字面量字符串，将被解析为字典
        trusted_dirs: 可以信任运行OpenHands CLI的目录列表
        vscode_port: 用于VSCode的端口。如果为None，将选择随机端口。当在远程机器上部署OpenHands时很有用，您需要暴露特定端口
    """

    remote_runtime_api_url: str | None = Field(default='http://localhost:8000')
    """远程Runtime API的URL地址"""
    
    local_runtime_url: str = Field(default='http://localhost')
    """本地runtime的URL地址"""
    
    keep_runtime_alive: bool = Field(default=False)
    """是否保持runtime运行状态"""
    
    pause_closed_runtimes: bool = Field(default=True)
    """是否暂停已关闭的runtime"""
    
    rm_all_containers: bool = Field(default=False)
    """是否删除所有容器"""
    
    api_key: str | None = Field(default=None)
    """API访问密钥"""
    
    base_container_image: str | None = Field(
        default='nikolaik/python-nodejs:python3.12-nodejs22'
    )
    """基础容器镜像，用于构建runtime环境"""
    
    runtime_container_image: str | None = Field(default=None)
    """指定的runtime容器镜像"""
    
    user_id: int = Field(default=os.getuid() if hasattr(os, 'getuid') else 1000)
    """运行容器的用户ID，默认使用当前用户ID"""
    
    timeout: int = Field(default=120)
    """默认操作执行超时时间（秒）"""
    
    remote_runtime_init_timeout: int = Field(default=180)
    """远程runtime初始化超时时间（秒）"""
    
    remote_runtime_api_timeout: int = Field(default=10)
    """远程runtime API请求超时时间（秒）"""
    
    remote_runtime_enable_retries: bool = Field(default=True)
    """是否启用远程runtime API请求重试"""
    
    remote_runtime_class: str | None = Field(
        default=None
    )  # 可以是"None"（默认为gvisor）或"sysbox"（支持runtime内的docker + 更稳定）
    """远程runtime类类型，None表示默认gvisor，sysbox支持容器内docker"""
    
    enable_auto_lint: bool = Field(
        default=False
    )  # 启用后，OpenHands会在编辑文件后进行lint
    """是否启用自动代码检查功能"""
    
    use_host_network: bool = Field(default=False)
    """是否使用主机网络模式"""
    
    runtime_binding_address: str = Field(default='0.0.0.0')
    """runtime端口绑定的网络地址"""
    
    runtime_extra_build_args: list[str] | None = Field(default=None)
    """构建runtime时的额外参数列表"""
    
    initialize_plugins: bool = Field(default=True)
    """是否初始化插件系统"""
    
    force_rebuild_runtime: bool = Field(default=False)
    """是否强制重建runtime镜像"""
    
    runtime_extra_deps: str | None = Field(default=None)
    """runtime环境的额外依赖安装命令"""
    
    runtime_startup_env_vars: dict[str, str] = Field(default_factory=dict)
    """runtime启动时设置的环境变量字典"""
    
    browsergym_eval_env: str | None = Field(default=None)
    """BrowserGym评估环境配置"""
    
    platform: str | None = Field(default=None)
    """构建镜像的目标平台"""
    
    close_delay: int = Field(
        default=3600,
        description='Agent完成后关闭Sandbox前的延迟时间（秒）',
    )
    """Sandbox关闭延迟时间，给用户时间查看结果"""
    
    remote_runtime_resource_factor: int = Field(default=1)
    """远程runtime资源分配缩放因子"""
    
    enable_gpu: bool = Field(default=False)
    """是否启用GPU支持"""
    
    docker_runtime_kwargs: dict | None = Field(default=None)
    """传递给Docker runtime的额外关键字参数"""
    
    selected_repo: str | None = Field(default=None)
    """选定的代码仓库"""
    
    trusted_dirs: list[str] = Field(default_factory=list)
    """可信任的目录列表"""
    
    vscode_port: int | None = Field(default=None)
    """VSCode服务端口号"""
    
    volumes: str | None = Field(
        default=None,
        description="卷挂载格式为'host_path:container_path[:mode]'，例如'/my/host/dir:/workspace:rw'。多个挂载可以用逗号指定，例如'/path1:/workspace/path1,/path2:/workspace/path2:ro'",
    )
    """容器卷挂载配置字符串"""

    cuda_visible_devices: str | None = Field(default=None)
    """CUDA可见设备配置"""
    
    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')

    @classmethod
    def from_toml_section(cls, data: dict) -> dict[str, 'SandboxConfig']:
        """从表示[sandbox]部分的toml字典创建SandboxConfig实例的映射。

        配置是从data中的所有键构建的。

        Args:
            data: 包含sandbox配置数据的字典
            
        Returns:
            dict[str, SandboxConfig]: 一个映射，其中键"sandbox"对应[sandbox]配置
            
        Raises:
            ValueError: 当sandbox配置无效时抛出
        """
        # 初始化结果映射
        sandbox_mapping: dict[str, SandboxConfig] = {}

        # 尝试创建配置实例
        try:
            sandbox_mapping['sandbox'] = cls.model_validate(data)
        except ValidationError as e:
            raise ValueError(f'Invalid sandbox configuration: {e}')

        return sandbox_mapping

    @model_validator(mode='after')
    def set_default_base_image(self) -> 'SandboxConfig':
        """模型验证后设置默认基础镜像。
        
        确保base_container_image字段有合适的默认值。
        
        Returns:
            SandboxConfig: 验证并可能修改后的配置实例
        """
        if self.base_container_image is None:
            self.base_container_image = 'nikolaik/python-nodejs:python3.12-nodejs22'
        return self