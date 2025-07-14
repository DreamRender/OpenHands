from openhands.core.config import OpenHandsConfig
from openhands.runtime.plugins import PluginRequirement

# 默认Python前缀命令列表，用于通过micromamba和poetry运行Python
DEFAULT_PYTHON_PREFIX = [
    '/openhands/micromamba/bin/micromamba',
    'run',
    '-n',
    'openhands',
    'poetry',
    'run',
]

# 默认主模块路径
DEFAULT_MAIN_MODULE = 'openhands.runtime.action_execution_server'


def get_action_execution_server_startup_command(
    server_port: int,
    plugins: list[PluginRequirement],
    app_config: OpenHandsConfig,
    python_prefix: list[str] = DEFAULT_PYTHON_PREFIX,
    override_user_id: int | None = None,
    override_username: str | None = None,
    main_module: str = DEFAULT_MAIN_MODULE,
    python_executable: str = 'python',
) -> list[str]:
    """
    获取Action执行服务器启动命令。
    
    构建用于启动Action执行服务器的完整命令列表，包括所有必要的参数和配置。
    
    Args:
        server_port (int): 服务器端口号
        plugins (list[PluginRequirement]): 插件要求列表
        app_config (OpenHandsConfig): 应用配置对象
        python_prefix (list[str]): Python前缀命令列表，默认为DEFAULT_PYTHON_PREFIX
        override_user_id (int | None): 覆盖用户ID，默认为None
        override_username (str | None): 覆盖用户名，默认为None
        main_module (str): 主模块路径，默认为DEFAULT_MAIN_MODULE
        python_executable (str): Python可执行文件名，默认为'python'
        
    Returns:
        list[str]: 完整的启动命令列表
    """
    sandbox_config = app_config.sandbox

    # 插件参数
    plugin_args = []
    if plugins is not None and len(plugins) > 0:
        plugin_args = ['--plugins'] + [plugin.name for plugin in plugins]

    # Browsergym相关参数
    browsergym_args = []
    if sandbox_config.browsergym_eval_env is not None:
        browsergym_args = [
            '--browsergym-eval-env'
        ] + sandbox_config.browsergym_eval_env.split(' ')

    # 确定用户名和用户ID
    username = override_username or (
        'openhands' if app_config.run_as_openhands else 'root'
    )
    user_id = override_user_id or (
        sandbox_config.user_id if app_config.run_as_openhands else 0
    )

    # 构建基础命令
    base_cmd = [
        *python_prefix,
        python_executable,
        '-u',  # 无缓冲输出
        '-m',  # 作为模块运行
        main_module,
        str(server_port),
        '--working-dir',
        app_config.workspace_mount_path_in_sandbox,
        *plugin_args,
        '--username',
        username,
        '--user-id',
        str(user_id),
        *browsergym_args,
    ]

    # 如果未启用浏览器，添加相应参数
    if not app_config.enable_browser:
        base_cmd.append('--no-enable-browser')

    return base_cmd
