import argparse
import os
import pathlib
import platform
import sys
from ast import literal_eval
from types import UnionType
from typing import MutableMapping, get_args, get_origin
from uuid import uuid4

import toml
from dotenv import load_dotenv
from pydantic import BaseModel, SecretStr, ValidationError

from openhands import __version__
from openhands.core import logger
from openhands.core.config.agent_config import AgentConfig
from openhands.core.config.condenser_config import (
    CondenserConfig,
    condenser_config_from_toml_section,
    create_condenser_config,
)
from openhands.core.config.config_utils import (
    OH_DEFAULT_AGENT,
    OH_MAX_ITERATIONS,
)
from openhands.core.config.extended_config import ExtendedConfig
from openhands.core.config.kubernetes_config import KubernetesConfig
from openhands.core.config.llm_config import LLMConfig
from openhands.core.config.mcp_config import MCPConfig
from openhands.core.config.openhands_config import OpenHandsConfig
from openhands.core.config.sandbox_config import SandboxConfig
from openhands.core.config.security_config import SecurityConfig
from openhands.storage import get_file_store
from openhands.storage.files import FileStore
from openhands.utils.import_utils import get_impl

# JWT密钥文件名常量
JWT_SECRET = '.jwt_secret'
# 加载环境变量
load_dotenv()


def load_from_env(
    cfg: OpenHandsConfig, env_or_toml_dict: dict | MutableMapping[str, str]
) -> None:
    """从环境变量或TOML字典设置配置属性。

    读取环境风格的变量并相应地更新配置属性。
    支持LLM设置配置（例如LLM_BASE_URL）、Agent设置配置
    （例如AGENT_MEMORY_ENABLED）、Sandbox设置配置（例如SANDBOX_TIMEOUT）等。

    Args:
        cfg: 要设置属性的OpenHandsConfig对象
        env_or_toml_dict: 环境变量或config.toml字典
    """

    def get_optional_type(union_type: UnionType | type | None) -> type | None:
        """从Union类型中返回非None类型。
        
        Args:
            union_type: 要分析的联合类型
            
        Returns:
            type | None: 非None的类型，如果没有找到则返回None
        """
        if union_type is None:
            return None
        if get_origin(union_type) is UnionType:
            types = get_args(union_type)
            return next((t for t in types if t is not type(None)), None)
        if isinstance(union_type, type):
            return union_type
        return None

    # 基于环境变量设置属性的辅助函数
    def set_attr_from_env(sub_config: BaseModel, prefix: str = '') -> None:
        """基于环境变量设置配置模型的属性。
        
        Args:
            sub_config: 要配置的模型实例
            prefix: 环境变量名的前缀
        """
        for field_name, field_info in sub_config.__class__.model_fields.items():
            field_value = getattr(sub_config, field_name)
            field_type = field_info.annotation

            # 从前缀和字段名计算预期的环境变量名
            # 例如 LLM_BASE_URL
            env_var_name = (prefix + field_name).upper()

            if isinstance(field_value, BaseModel):
                # 如果字段值是另一个BaseModel，递归处理
                set_attr_from_env(field_value, prefix=field_name + '_')

            elif env_var_name in env_or_toml_dict:
                # 将环境变量转换为正确的类型并设置
                value = env_or_toml_dict[env_var_name]

                # 跳过空配置值（回退到默认值）
                if not value:
                    continue

                try:
                    # 如果是可选类型，获取非None类型
                    if get_origin(field_type) is UnionType:
                        field_type = get_optional_type(field_type)

                    # 尝试将环境变量转换为数据类中提示的类型
                    if field_type is bool:
                        cast_value = str(value).lower() in ['true', '1']
                    # 解析字典和列表，如SANDBOX_RUNTIME_STARTUP_ENV_VARS和SANDBOX_RUNTIME_EXTRA_BUILD_ARGS
                    elif (
                        get_origin(field_type) is dict
                        or get_origin(field_type) is list
                        or field_type is dict
                        or field_type is list
                    ):
                        cast_value = literal_eval(value)
                    else:
                        if field_type is not None:
                            cast_value = field_type(value)
                    setattr(sub_config, field_name, cast_value)
                except (ValueError, TypeError):
                    logger.openhands_logger.error(
                        f'Error setting env var {env_var_name}={value}: check that the value is of the right type'
                    )

    # 从配置对象的根开始处理
    set_attr_from_env(cfg)

    # 从环境变量加载默认LLM配置
    default_llm_config = cfg.get_llm_config()
    set_attr_from_env(default_llm_config, 'LLM_')
    # 从环境变量加载默认Agent配置
    default_agent_config = cfg.get_agent_config()
    set_attr_from_env(default_agent_config, 'AGENT_')


def load_from_toml(cfg: OpenHandsConfig, toml_file: str = 'config.toml') -> None:
    """从toml文件加载配置。支持两种配置变量风格。

    Args:
        cfg: 要更新属性的OpenHandsConfig对象
        toml_file: toml文件的路径。默认为'config.toml'

    See Also:
        - config.template.toml包含完整的配置选项列表
    """
    # 尝试将config.toml文件读取到配置对象中
    try:
        with open(toml_file, 'r', encoding='utf-8') as toml_contents:
            toml_config = toml.load(toml_contents)
    except FileNotFoundError:
        # 如果文件不存在，直接返回
        return
    except toml.TomlDecodeError as e:
        logger.openhands_logger.warning(
            f'Cannot parse config from toml, toml values have not been applied.\nError: {e}',
        )
        return

    # 检查[core]部分
    if 'core' not in toml_config:
        logger.openhands_logger.warning(
            f'No [core] section found in {toml_file}. Core settings will use defaults.'
        )
        core_config = {}
    else:
        core_config = toml_config['core']

    # 如果存在，处理core部分
    for key, value in core_config.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
        else:
            logger.openhands_logger.warning(
                f'Unknown config key "{key}" in [core] section'
            )

    # 如果存在，处理agent部分
    if 'agent' in toml_config:
        try:
            agent_mapping = AgentConfig.from_toml_section(toml_config['agent'])
            for agent_key, agent_conf in agent_mapping.items():
                cfg.set_agent_config(agent_conf, agent_key)
        except (TypeError, KeyError, ValidationError) as e:
            logger.openhands_logger.warning(
                f'Cannot parse [agent] config from toml, values have not been applied.\nError: {e}'
            )

    # 如果存在，处理llm部分
    if 'llm' in toml_config:
        try:
            llm_mapping = LLMConfig.from_toml_section(toml_config['llm'])
            for llm_key, llm_conf in llm_mapping.items():
                cfg.set_llm_config(llm_conf, llm_key)
        except (TypeError, KeyError, ValidationError) as e:
            logger.openhands_logger.warning(
                f'Cannot parse [llm] config from toml, values have not been applied.\nError: {e}'
            )

    # 如果存在，处理security部分
    if 'security' in toml_config:
        try:
            security_mapping = SecurityConfig.from_toml_section(toml_config['security'])
            # 目前我们只使用基础安全配置
            if 'security' in security_mapping:
                cfg.security = security_mapping['security']
        except (TypeError, KeyError, ValidationError) as e:
            logger.openhands_logger.warning(
                f'Cannot parse [security] config from toml, values have not been applied.\nError: {e}'
            )
        except ValueError:
            # 重新抛出来自SecurityConfig.from_toml_section的ValueError
            raise ValueError('Error in [security] section in config.toml')

    # 如果存在，处理sandbox部分
    if 'sandbox' in toml_config:
        try:
            sandbox_mapping = SandboxConfig.from_toml_section(toml_config['sandbox'])
            # 目前我们只使用基础sandbox配置
            if 'sandbox' in sandbox_mapping:
                cfg.sandbox = sandbox_mapping['sandbox']
        except (TypeError, KeyError, ValidationError) as e:
            logger.openhands_logger.warning(
                f'Cannot parse [sandbox] config from toml, values have not been applied.\nError: {e}'
            )
        except ValueError:
            # 重新抛出来自SandboxConfig.from_toml_section的ValueError
            raise ValueError('Error in [sandbox] section in config.toml')

    # 如果存在，处理MCP部分
    if 'mcp' in toml_config:
        try:
            mcp_mapping = MCPConfig.from_toml_section(toml_config['mcp'])
            # 目前我们只使用基础mcp配置
            if 'mcp' in mcp_mapping:
                cfg.mcp = mcp_mapping['mcp']
        except (TypeError, KeyError, ValidationError) as e:
            logger.openhands_logger.warning(
                f'Cannot parse MCP config from toml, values have not been applied.\nError: {e}'
            )
        except ValueError:
            # 重新抛出来自MCPConfig.from_toml_section的ValueError
            raise ValueError('Error in MCP sections in config.toml')

    # 如果存在，处理kubernetes部分
    if 'kubernetes' in toml_config:
        try:
            kubernetes_mapping = KubernetesConfig.from_toml_section(
                toml_config['kubernetes']
            )
            if 'kubernetes' in kubernetes_mapping:
                cfg.kubernetes = kubernetes_mapping['kubernetes']
        except (TypeError, KeyError, ValidationError) as e:
            logger.openhands_logger.warning(
                f'Cannot parse [kubernetes] config from toml, values have not been applied.\nError: {e}'
            )

    # 如果存在，处理condenser部分
    if 'condenser' in toml_config:
        try:
            # 将LLM配置传递给condenser配置解析器
            condenser_mapping = condenser_config_from_toml_section(
                toml_config['condenser'], cfg.llms
            )
            # 将默认的condenser配置分配给默认的Agent配置
            if 'condenser' in condenser_mapping:
                # 获取默认Agent配置并将condenser配置分配给它
                default_agent_config = cfg.get_agent_config()
                default_agent_config.condenser = condenser_mapping['condenser']
                logger.openhands_logger.debug(
                    'Default condenser configuration loaded from config toml and assigned to default agent'
                )
        except (TypeError, KeyError, ValidationError) as e:
            logger.openhands_logger.warning(
                f'Cannot parse [condenser] config from toml, values have not been applied.\nError: {e}'
            )
    # 如果toml中没有condenser部分但enable_default_condenser为True，
    # 设置LLMSummarizingCondenserConfig作为默认值
    elif cfg.enable_default_condenser:
        from openhands.core.config.condenser_config import LLMSummarizingCondenserConfig

        # 获取默认Agent配置
        default_agent_config = cfg.get_agent_config()

        # 创建默认LLM总结Condenser配置
        default_condenser = LLMSummarizingCondenserConfig(
            llm_config=cfg.get_llm_config(),  # 使用默认LLM配置
            type='llm',
        )

        # 设置为默认Condenser
        default_agent_config.condenser = default_condenser
        logger.openhands_logger.debug(
            'Default LLM summarizing condenser assigned to default agent (no condenser in config)'
        )

    # 如果存在，处理extended部分
    if 'extended' in toml_config:
        try:
            cfg.extended = ExtendedConfig(toml_config['extended'])
        except (TypeError, KeyError, ValidationError) as e:
            logger.openhands_logger.warning(
                f'Cannot parse [extended] config from toml, values have not been applied.\nError: {e}'
            )

    # 检查未知部分
    known_sections = {
        'core',
        'extended',
        'agent',
        'llm',
        'security',
        'sandbox',
        'condenser',
        'mcp',
        'kubernetes',
    }
    for key in toml_config:
        if key.lower() not in known_sections:
            logger.openhands_logger.warning(f'Unknown section [{key}] in {toml_file}')


def get_or_create_jwt_secret(file_store: FileStore) -> str:
    """获取或创建JWT密钥。
    
    Args:
        file_store: 文件存储实例
        
    Returns:
        str: JWT密钥字符串
    """
    try:
        # 尝试读取现有的JWT密钥
        jwt_secret = file_store.read(JWT_SECRET)
        return jwt_secret
    except FileNotFoundError:
        # 如果文件不存在，生成新的密钥
        new_secret = uuid4().hex
        file_store.write(JWT_SECRET, new_secret)
        return new_secret


def finalize_config(cfg: OpenHandsConfig) -> None:
    """配置加载后的最终调整。
    
    Args:
        cfg: 要最终调整的OpenHandsConfig对象
    """
    # 处理sandbox.volumes参数
    if cfg.workspace_base is not None or cfg.workspace_mount_path is not None:
        logger.openhands_logger.warning(
            'DEPRECATED: The WORKSPACE_BASE and WORKSPACE_MOUNT_PATH environment variables are deprecated. '
            "Please use RUNTIME_MOUNT instead, e.g. 'RUNTIME_MOUNT=/my/host/dir:/workspace:rw'"
        )
    if cfg.sandbox.volumes is not None:
        # 按逗号分割以处理多个挂载
        mounts = cfg.sandbox.volumes.split(',')

        # 检查是否有挂载明确指向/workspace
        workspace_mount_found = False
        for mount in mounts:
            parts = mount.split(':')
            if len(parts) >= 2 and parts[1] == '/workspace':
                workspace_mount_found = True
                host_path = os.path.abspath(parts[0])

                # 设置workspace_mount_path和workspace_mount_path_in_sandbox
                cfg.workspace_mount_path = host_path
                cfg.workspace_mount_path_in_sandbox = '/workspace'

                # 同时设置workspace_base
                cfg.workspace_base = host_path
                break

        # 如果没有找到明确的/workspace挂载，不设置任何workspace挂载
        # 这允许用户挂载卷而不影响workspace
        if not workspace_mount_found:
            logger.openhands_logger.debug(
                'No explicit /workspace mount found in SANDBOX_VOLUMES. '
                'Using default workspace path in sandbox.'
            )
            # 确保workspace_mount_path和workspace_base为None，避免
            # 意外的挂载行为
            cfg.workspace_mount_path = None
            cfg.workspace_base = None

        # 验证所有挂载
        for mount in mounts:
            parts = mount.split(':')
            if len(parts) < 2 or len(parts) > 3:
                raise ValueError(
                    f'Invalid mount format in sandbox.volumes: {mount}. '
                    f"Expected format: 'host_path:container_path[:mode]', e.g. '/my/host/dir:/workspace:rw'"
                )

    # 处理已弃用的workspace_*参数
    elif cfg.workspace_base is not None or cfg.workspace_mount_path is not None:
        if cfg.workspace_base is not None:
            cfg.workspace_base = os.path.abspath(cfg.workspace_base)
            if cfg.workspace_mount_path is None:
                cfg.workspace_mount_path = cfg.workspace_base

        if cfg.workspace_mount_rewrite:
            base = cfg.workspace_base or os.getcwd()
            parts = cfg.workspace_mount_rewrite.split(':')
            cfg.workspace_mount_path = base.replace(parts[0], parts[1])

    # 确保log_completions_folder是绝对路径
    for llm in cfg.llms.values():
        llm.log_completions_folder = os.path.abspath(llm.log_completions_folder)

    if cfg.sandbox.use_host_network and platform.system() == 'Darwin':
        logger.openhands_logger.warning(
            'Please upgrade to Docker Desktop 4.29.0 or later to use host network mode on macOS. '
            'See https://github.com/docker/roadmap/issues/238#issuecomment-2044688144 for more information.'
        )

    # 确保缓存目录存在
    if cfg.cache_dir:
        pathlib.Path(cfg.cache_dir).mkdir(parents=True, exist_ok=True)

    if not cfg.jwt_secret:
        cfg.jwt_secret = SecretStr(
            get_or_create_jwt_secret(
                get_file_store(cfg.file_store, cfg.file_store_path)
            )
        )

    # 如果选择了CLIRuntime，为所有Agent禁用Jupyter
    # 假设'cli'是CLIRuntime的标识符
    if cfg.runtime and cfg.runtime.lower() == 'cli':
        for agent_name, agent_config in cfg.agents.items():
            if agent_config.enable_jupyter:
                agent_config.enable_jupyter = False
            if agent_config.enable_browsing:
                agent_config.enable_browsing = False
        logger.openhands_logger.debug(
            'Automatically disabled Jupyter plugin and browsing for all agents '
            'because CLIRuntime is selected and does not support IPython execution.'
        )


def get_agent_config_arg(
    agent_config_arg: str, toml_file: str = 'config.toml'
) -> AgentConfig | None:
    """从配置文件获取一组Agent设置。

    config.toml中的组可以如下所示：

    ```
    [agent.default]
    enable_prompt_extensions = false
    ```

    用户定义的组名，如"default"，是此函数的参数。该函数将从配置文件中加载
    该组设置的AgentConfig对象，并将其设置为应用程序的AgentConfig对象。

    注意，组必须在"agent"组下，或者说，组名必须以"agent."开头。

    Args:
        agent_config_arg: 要从config.toml文件获取的Agent设置组
        toml_file: 要读取的配置文件路径。默认为'config.toml'

    Returns:
        AgentConfig: 具有配置文件设置的AgentConfig对象，如果失败则返回None
    """
    # 只保留名称，以防万一
    agent_config_arg = agent_config_arg.strip('[]')

    # 截断前缀，以防万一
    if agent_config_arg.startswith('agent.'):
        agent_config_arg = agent_config_arg[6:]

    logger.openhands_logger.debug(f'Loading agent config from {agent_config_arg}')

    # 加载toml文件
    try:
        with open(toml_file, 'r', encoding='utf-8') as toml_contents:
            toml_config = toml.load(toml_contents)
    except FileNotFoundError as e:
        logger.openhands_logger.error(f'Config file not found: {e}')
        return None
    except toml.TomlDecodeError as e:
        logger.openhands_logger.error(
            f'Cannot parse agent group from {agent_config_arg}. Exception: {e}'
        )
        return None

    # 使用指定部分更新Agent配置
    if 'agent' in toml_config and agent_config_arg in toml_config['agent']:
        return AgentConfig(**toml_config['agent'][agent_config_arg])
    logger.openhands_logger.debug(f'Loading from toml failed for {agent_config_arg}')
    return None


def get_llm_config_arg(
    llm_config_arg: str, toml_file: str = 'config.toml'
) -> LLMConfig | None:
    """从配置文件获取一组LLM设置。

    config.toml中的组可以如下所示：

    ```
    [llm.gpt-3.5-for-eval]
    model = 'gpt-3.5-turbo'
    api_key = '...'
    temperature = 0.5
    num_retries = 8
    ...
    ```

    用户定义的组名，如"gpt-3.5-for-eval"，是此函数的参数。该函数将从配置文件中加载
    该组设置的LLMConfig对象，并将其设置为应用程序的LLMConfig对象。

    注意，组必须在"llm"组下，或者说，组名必须以"llm."开头。

    Args:
        llm_config_arg: 要从config.toml文件获取的LLM设置组
        toml_file: 要读取的配置文件路径。默认为'config.toml'

    Returns:
        LLMConfig: 具有配置文件设置的LLMConfig对象，如果失败则返回None
    """
    # 只保留名称，以防万一
    llm_config_arg = llm_config_arg.strip('[]')

    # 截断前缀，以防万一
    if llm_config_arg.startswith('llm.'):
        llm_config_arg = llm_config_arg[4:]

    logger.openhands_logger.debug(f'Loading llm config from {llm_config_arg}')

    # 加载toml文件
    try:
        with open(toml_file, 'r', encoding='utf-8') as toml_contents:
            toml_config = toml.load(toml_contents)
    except FileNotFoundError as e:
        logger.openhands_logger.error(f'Config file not found: {e}')
        return None
    except toml.TomlDecodeError as e:
        logger.openhands_logger.error(
            f'Cannot parse llm group from {llm_config_arg}. Exception: {e}'
        )
        return None

    # 使用指定部分更新LLM配置
    if 'llm' in toml_config and llm_config_arg in toml_config['llm']:
        return LLMConfig(**toml_config['llm'][llm_config_arg])
    logger.openhands_logger.debug(f'Loading from toml failed for {llm_config_arg}')
    return None


def get_condenser_config_arg(
    condenser_config_arg: str, toml_file: str = 'config.toml'
) -> CondenserConfig | None:
    """按名称从配置文件获取一组Condenser设置。

    config.toml中的组可以如下所示：

    ```
    [condenser.my_summarizer]
    type = 'llm'
    llm_config = 'gpt-4o' # 引用[llm.gpt-4o]
    max_size = 50
    ...
    ```

    用户定义的组名，如"my_summarizer"，是此函数的参数。
    该函数将从配置文件中加载该组设置的CondenserConfig对象。

    注意，组必须在"condenser"组下，或者说，
    组名必须以"condenser."开头。

    Args:
        condenser_config_arg: 要从config.toml文件获取的Condenser设置组
        toml_file: 要读取的配置文件路径。默认为'config.toml'

    Returns:
        CondenserConfig: 具有配置文件设置的CondenserConfig对象，如果未找到/错误则返回None
    """
    # 只保留名称，以防万一
    condenser_config_arg = condenser_config_arg.strip('[]')

    # 截断前缀，以防万一
    if condenser_config_arg.startswith('condenser.'):
        condenser_config_arg = condenser_config_arg[10:]

    logger.openhands_logger.debug(
        f'Loading condenser config [{condenser_config_arg}] from {toml_file}'
    )

    # 加载toml文件
    try:
        with open(toml_file, 'r', encoding='utf-8') as toml_contents:
            toml_config = toml.load(toml_contents)
    except FileNotFoundError as e:
        logger.openhands_logger.error(f'Config file not found: {toml_file}. Error: {e}')
        return None
    except toml.TomlDecodeError as e:
        logger.openhands_logger.error(
            f'Cannot parse condenser group [{condenser_config_arg}] from {toml_file}. Exception: {e}'
        )
        return None

    # 检查condenser部分和特定配置是否存在
    if (
        'condenser' not in toml_config
        or condenser_config_arg not in toml_config['condenser']
    ):
        logger.openhands_logger.error(
            f'Condenser config section [condenser.{condenser_config_arg}] not found in {toml_file}'
        )
        return None

    # 使用副本进行修改
    condenser_data = toml_config['condenser'][condenser_config_arg].copy()

    # 确定类型并处理潜在的LLM依赖
    condenser_type = condenser_data.get('type')
    if not condenser_type:
        logger.openhands_logger.error(
            f'Missing "type" field in [condenser.{condenser_config_arg}] section of {toml_file}'
        )
        return None

    # 如果需要，处理LLM配置引用，使用get_llm_config_arg
    if (
        condenser_type in ('llm', 'llm_attention', 'structured')
        and 'llm_config' in condenser_data
        and isinstance(condenser_data['llm_config'], str)
    ):
        llm_config_name = condenser_data['llm_config']
        logger.openhands_logger.debug(
            f'Condenser [{condenser_config_arg}] requires LLM config [{llm_config_name}]. Loading it...'
        )
        # 使用现有函数加载特定的LLM配置
        referenced_llm_config = get_llm_config_arg(llm_config_name, toml_file=toml_file)

        if referenced_llm_config:
            # 用实际的LLMConfig对象替换字符串引用
            condenser_data['llm_config'] = referenced_llm_config
        else:
            # 如果未找到，get_llm_config_arg已经记录了错误
            logger.openhands_logger.error(
                f"Failed to load required LLM config '{llm_config_name}' for condenser '{condenser_config_arg}'."
            )
            return None

    # 创建condenser配置实例
    try:
        config = create_condenser_config(condenser_type, condenser_data)
        logger.openhands_logger.info(
            f'Successfully loaded condenser config [{condenser_config_arg}] from {toml_file}'
        )
        return config
    except (ValidationError, ValueError) as e:
        logger.openhands_logger.error(
            f'Invalid condenser configuration for [{condenser_config_arg}]: {e}.'
        )
        return None


# 命令行参数
def get_parser() -> argparse.ArgumentParser:
    """获取参数解析器。
    
    Returns:
        argparse.ArgumentParser: 配置好的参数解析器
    """
    parser = argparse.ArgumentParser(description='通过CLI运行Agent')

    # 添加版本参数
    parser.add_argument(
        '-v', '--version', action='store_true', help='显示版本信息'
    )

    parser.add_argument(
        '--config-file',
        type=str,
        default='config.toml',
        help='配置文件的路径（默认：当前目录中的config.toml）',
    )
    parser.add_argument(
        '-d',
        '--directory',
        type=str,
        help='Agent的工作目录',
    )
    parser.add_argument(
        '-t',
        '--task',
        type=str,
        default='',
        help='Agent要执行的任务',
    )
    parser.add_argument(
        '-f',
        '--file',
        type=str,
        help='包含任务的文件路径。如果同时提供了-t，此选项会覆盖-t',
    )
    parser.add_argument(
        '-c',
        '--agent-cls',
        default=OH_DEFAULT_AGENT,
        type=str,
        help='要使用的默认Agent名称',
    )
    parser.add_argument(
        '-i',
        '--max-iterations',
        default=OH_MAX_ITERATIONS,
        type=int,
        help='运行Agent的最大迭代次数',
    )
    parser.add_argument(
        '-b',
        '--max-budget-per-task',
        type=float,
        help='每个任务允许的最大预算，超过此预算Agent将停止',
    )
    # --eval配置仅用于评估
    parser.add_argument(
        '--eval-output-dir',
        default='evaluation/evaluation_outputs/outputs',
        type=str,
        help='保存评估输出的目录',
    )
    parser.add_argument(
        '--eval-n-limit',
        default=None,
        type=int,
        help='要评估的实例数量',
    )
    parser.add_argument(
        '--eval-num-workers',
        default=4,
        type=int,
        help='用于评估的工作器数量',
    )
    parser.add_argument(
        '--eval-note',
        default=None,
        type=str,
        help='要添加到评估目录的备注',
    )
    parser.add_argument(
        '-l',
        '--llm-config',
        default=None,
        type=str,
        help='用指定的LLM配置替换默认LLM（config.toml中的[llm]部分）配置，例如"llama3"对应config.toml中的[llm.llama3]部分',
    )
    parser.add_argument(
        '--agent-config',
        default=None,
        type=str,
        help='用指定的Agent配置替换默认Agent（config.toml中的[agent]部分）配置，例如"CodeAct"对应config.toml中的[agent.CodeAct]部分',
    )
    parser.add_argument(
        '-n',
        '--name',
        help='Session名称',
        type=str,
        default='',
    )
    parser.add_argument(
        '--eval-ids',
        default=None,
        type=str,
        help='要评估的实例ID的逗号分隔列表（用引号括起来）',
    )
    parser.add_argument(
        '--no-auto-continue',
        help='在无头模式下禁用自动继续响应（即无头模式将从stdin读取而不是自动继续）',
        action='store_true',
        default=False,
    )
    parser.add_argument(
        '--selected-repo',
        help='要克隆的GitHub仓库（格式：owner/repo）',
        type=str,
        default=None,
    )
    parser.add_argument(
        '--override-cli-mode',
        help='覆盖CLI模式的默认设置',
        type=bool,
        default=False,
    )
    return parser


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数。
    
    Returns:
        argparse.Namespace: 解析后的命令行参数
    """
    parser = get_parser()
    args = parser.parse_args()

    if args.version:
        print(f'OpenHands version: {__version__}')
        sys.exit(0)

    return args


def register_custom_agents(config: OpenHandsConfig) -> None:
    """从配置注册自定义Agent。

    此函数在配置加载后调用，以确保配置中指定的所有自定义Agent
    都正确导入和注册。
    
    Args:
        config: 包含Agent配置的OpenHandsConfig对象
    """
    # 在这里导入以避免循环依赖
    from openhands.controller.agent import Agent

    for agent_name, agent_config in config.agents.items():
        if agent_config.classpath:
            try:
                agent_cls = get_impl(Agent, agent_config.classpath)
                Agent.register(agent_name, agent_cls)
                logger.openhands_logger.info(
                    f"Registered custom agent '{agent_name}' from {agent_config.classpath}"
                )
            except Exception as e:
                logger.openhands_logger.error(
                    f"Failed to register agent '{agent_name}': {e}"
                )


def load_openhands_config(
    set_logging_levels: bool = True, config_file: str = 'config.toml'
) -> OpenHandsConfig:
    """从指定的配置文件和环境变量加载配置。

    Args:
        set_logging_levels: 是否设置日志级别的全局变量
        config_file: 配置文件的路径。默认为当前目录中的'config.toml'
        
    Returns:
        OpenHandsConfig: 加载的配置对象
    """
    config = OpenHandsConfig()
    load_from_toml(config, config_file)
    load_from_env(config, os.environ)
    finalize_config(config)
    register_custom_agents(config)
    if set_logging_levels:
        logger.DEBUG = config.debug
        logger.DISABLE_COLOR_PRINTING = config.disable_color
    return config


def setup_config_from_args(args: argparse.Namespace) -> OpenHandsConfig:
    """从toml加载配置并用命令行参数覆盖。

    CLI和main.py入口点使用的通用设置。
    
    Args:
        args: 解析后的命令行参数
        
    Returns:
        OpenHandsConfig: 配置好的OpenHandsConfig对象
    """
    # 从toml和环境变量加载基础配置
    config = load_openhands_config(config_file=args.config_file)

    # 如果提供了命令行参数，进行覆盖
    if args.llm_config:
        # 如果我们还没有加载它，从toml文件获取它
        if args.llm_config not in config.llms:
            llm_config = get_llm_config_arg(args.llm_config)
        else:
            llm_config = config.llms[args.llm_config]
        if llm_config is None:
            raise ValueError(f'Invalid toml file, cannot read {args.llm_config}')
        config.set_llm_config(llm_config)

    # 如果提供了默认Agent，进行覆盖
    if args.agent_cls:
        config.default_agent = args.agent_cls

    # 如果提供了最大迭代次数和每任务最大预算，进行设置，否则回退到配置值
    if args.max_iterations is not None:
        config.max_iterations = args.max_iterations
    if args.max_budget_per_task is not None:
        config.max_budget_per_task = args.max_budget_per_task

    # 在配置中读取选定的仓库供CLI和main.py使用
    if args.selected_repo is not None:
        config.sandbox.selected_repo = args.selected_repo

    return config