from pathlib import Path

import toml
from pydantic import BaseModel, Field

from openhands.cli.tui import (
    UsageMetrics,
)
from openhands.events.event import Event
from openhands.llm.metrics import Metrics

# 本地配置文件路径常量
_LOCAL_CONFIG_FILE_PATH = Path.home() / '.openhands' / 'config.toml'
# 默认配置结构常量
_DEFAULT_CONFIG: dict[str, dict[str, list[str]]] = {'sandbox': {'trusted_dirs': []}}


def get_local_config_trusted_dirs() -> list[str]:
    """
    获取本地配置文件中的受信任目录列表。
    
    从用户主目录下的.openhands/config.toml文件中读取受信任的目录列表。
    如果文件不存在或读取失败，返回空列表。
    
    Returns:
        list[str]: 受信任目录的路径列表
    """
    if _LOCAL_CONFIG_FILE_PATH.exists():
        with open(_LOCAL_CONFIG_FILE_PATH, 'r') as f:
            try:
                config = toml.load(f)
            except Exception:
                # 如果解析失败，使用默认配置
                config = _DEFAULT_CONFIG
        # 检查配置结构并返回受信任目录列表
        if 'sandbox' in config and 'trusted_dirs' in config['sandbox']:
            return config['sandbox']['trusted_dirs']
    return []


def add_local_config_trusted_dir(folder_path: str) -> None:
    """
    将目录添加到本地配置的受信任目录列表中。
    
    如果配置文件不存在，会创建新文件。如果目录已在列表中，不会重复添加。
    
    Args:
        folder_path: 要添加到受信任列表的目录路径
    """
    config = _DEFAULT_CONFIG
    
    # 如果配置文件存在，尝试读取现有配置
    if _LOCAL_CONFIG_FILE_PATH.exists():
        try:
            with open(_LOCAL_CONFIG_FILE_PATH, 'r') as f:
                config = toml.load(f)
        except Exception:
            # 读取失败时使用默认配置
            config = _DEFAULT_CONFIG
    else:
        # 配置文件不存在，创建目录
        _LOCAL_CONFIG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 确保配置结构存在
    if 'sandbox' not in config:
        config['sandbox'] = {}
    if 'trusted_dirs' not in config['sandbox']:
        config['sandbox']['trusted_dirs'] = []

    # 如果目录不在列表中，添加它
    if folder_path not in config['sandbox']['trusted_dirs']:
        config['sandbox']['trusted_dirs'].append(folder_path)

    # 将更新后的配置写回文件
    with open(_LOCAL_CONFIG_FILE_PATH, 'w') as f:
        toml.dump(config, f)


def update_usage_metrics(event: Event, usage_metrics: UsageMetrics) -> None:
    """
    根据事件更新使用指标统计。
    
    从事件中提取LLM使用指标并更新到UsageMetrics对象中。
    如果事件没有LLM指标或指标为None，则不进行任何更新。
    
    Args:
        event: 包含LLM指标的事件对象
        usage_metrics: 要更新的使用指标统计对象
    """
    # 检查事件是否有llm_metrics属性
    if not hasattr(event, 'llm_metrics'):
        return

    # 获取LLM指标
    llm_metrics: Metrics | None = event.llm_metrics
    if not llm_metrics:
        return

    # 更新使用指标
    usage_metrics.metrics = llm_metrics


class ModelInfo(BaseModel):
    """
    模型和提供商信息的数据模型。
    
    存储模型标识符的解析结果，包括提供商、模型名称和分隔符。
    
    Attributes:
        provider: 模型的提供商名称
        model: 模型标识符
        separator: 模型标识符中使用的分隔符
    """

    provider: str = Field(description='The provider of the model')
    model: str = Field(description='The model identifier')
    separator: str = Field(description='The separator used in the model identifier')

    def __getitem__(self, key: str) -> str:
        """
        允许字典式访问字段。
        
        提供向后兼容性，允许像字典一样访问模型信息。
        
        Args:
            key: 要访问的字段名称
            
        Returns:
            str: 对应字段的值
            
        Raises:
            KeyError: 如果字段不存在
        """
        if key == 'provider':
            return self.provider
        elif key == 'model':
            return self.model
        elif key == 'separator':
            return self.separator
        raise KeyError(f'ModelInfo has no key {key}')


def extract_model_and_provider(model: str) -> ModelInfo:
    """
    从模型标识符中提取提供商和模型信息。
    
    解析模型标识符字符串，识别提供商、模型名称和使用的分隔符。
    支持'/'和'.'作为分隔符，并对已知模型进行特殊处理。
    
    Args:
        model: 模型标识符字符串
        
    Returns:
        ModelInfo: 包含提供商、模型和分隔符信息的ModelInfo对象
    """
    separator = '/'  # 默认分隔符
    split = model.split(separator)

    if len(split) == 1:
        # 没有找到"/"分隔符，尝试使用"."
        separator = '.'
        split = model.split(separator)
        if split_is_actually_version(split):
            # 如果分割结果实际上是版本号，撤销分割
            split = [separator.join(split)]

    if len(split) == 1:
        # 没有找到"/"或"."分隔符，检查是否为已知模型
        if split[0] in VERIFIED_OPENAI_MODELS:
            return ModelInfo(provider='openai', model=split[0], separator='/')
        if split[0] in VERIFIED_ANTHROPIC_MODELS:
            return ModelInfo(provider='anthropic', model=split[0], separator='/')
        if split[0] in VERIFIED_MISTRAL_MODELS:
            return ModelInfo(provider='mistral', model=split[0], separator='/')
        # 作为纯模型名称返回
        return ModelInfo(provider='', model=model, separator='')

    # 有分隔符的情况
    provider = split[0]
    model_id = separator.join(split[1:])
    return ModelInfo(provider=provider, model=model_id, separator=separator)


def organize_models_and_providers(
    models: list[str],
) -> dict[str, 'ProviderInfo']:
    """
    按提供商组织模型标识符列表。
    
    将模型列表按提供商分组，并为每个提供商创建ProviderInfo对象。
    
    Args:
        models: 模型标识符列表
        
    Returns:
        dict[str, ProviderInfo]: 提供商到其信息和模型的映射
    """
    result_dict: dict[str, ProviderInfo] = {}

    for model in models:
        # 提取模型信息
        extracted = extract_model_and_provider(model)
        separator = extracted.separator
        provider = extracted.provider
        model_id = extracted.model

        # 忽略使用"."分隔符的"anthropic"提供商
        # 这些是过时且不兼容的提供商
        if provider == 'anthropic' and separator == '.':
            continue

        # 使用提供商名称作为键，如果为空则使用'other'
        key = provider or 'other'
        if key not in result_dict:
            result_dict[key] = ProviderInfo(separator=separator, models=[])

        result_dict[key].models.append(model_id)

    return result_dict


# 已验证的提供商列表
VERIFIED_PROVIDERS = ['anthropic', 'openai', 'mistral']

# 已验证的OpenAI模型列表（按优先级排序）
VERIFIED_OPENAI_MODELS = [
    'o4-mini',
    'gpt-4o',
    'gpt-4o-mini',
    'gpt-4-turbo',
    'gpt-4',
    'gpt-4-32k',
    'o1-mini',
    'o1',
    'o3-mini',
    'o3-mini-2025-01-31',
]

# 已验证的Anthropic模型列表（按优先级排序）
VERIFIED_ANTHROPIC_MODELS = [
    'claude-sonnet-4-20250514',
    'claude-opus-4-20250514',
    'claude-3-7-sonnet-20250219',
    'claude-3-sonnet-20240229',
    'claude-3-opus-20240229',
    'claude-3-haiku-20240307',
    'claude-3-5-haiku-20241022',
    'claude-3-5-sonnet-20241022',
    'claude-3-5-sonnet-20240620',
    'claude-2.1',
    'claude-2',
]

# 已验证的Mistral模型列表（按优先级排序）
VERIFIED_MISTRAL_MODELS = [
    'devstral-small-2505',
]


class ProviderInfo(BaseModel):
    """
    提供商及其模型信息的数据模型。
    
    存储特定提供商的分隔符和模型列表信息。
    
    Attributes:
        separator: 模型标识符中使用的分隔符
        models: 该提供商的模型标识符列表
    """

    separator: str = Field(description='The separator used in model identifiers')
    models: list[str] = Field(
        default_factory=list, description='List of model identifiers'
    )

    def __getitem__(self, key: str) -> str | list[str]:
        """
        允许字典式访问字段。
        
        提供向后兼容性，允许像字典一样访问提供商信息。
        
        Args:
            key: 要访问的字段名称
            
        Returns:
            str | list[str]: 对应字段的值
            
        Raises:
            KeyError: 如果字段不存在
        """
        if key == 'separator':
            return self.separator
        elif key == 'models':
            return self.models
        raise KeyError(f'ProviderInfo has no key {key}')

    def get(self, key: str, default: None = None) -> str | list[str] | None:
        """
        字典式get方法，支持默认值。
        
        Args:
            key: 要访问的字段名称
            default: 字段不存在时返回的默认值
            
        Returns:
            str | list[str] | None: 字段值或默认值
        """
        try:
            return self[key]
        except KeyError:
            return default


def is_number(char: str) -> bool:
    """
    检查字符是否为数字。
    
    Args:
        char: 要检查的字符
        
    Returns:
        bool: 如果字符是数字返回True，否则返回False
    """
    return char.isdigit()


def split_is_actually_version(split: list[str]) -> bool:
    """
    检查分割结果是否实际上是版本号。
    
    通过检查分割后的第二部分是否以数字开头来判断是否为版本号。
    
    Args:
        split: 字符串分割后的列表
        
    Returns:
        bool: 如果是版本号格式返回True，否则返回False
    """
    return (
        len(split) > 1
        and bool(split[1])
        and bool(split[1][0])
        and is_number(split[1][0])
    )


def read_file(file_path: str | Path) -> str:
    """
    读取文件内容。
    
    同步方式读取指定文件的全部内容。
    
    Args:
        file_path: 文件路径（字符串或Path对象）
        
    Returns:
        str: 文件的文本内容
        
    Raises:
        FileNotFoundError: 如果文件不存在
        IOError: 如果读取文件时发生错误
    """
    with open(file_path, 'r') as f:
        return f.read()


def write_to_file(file_path: str | Path, content: str) -> None:
    """
    将内容写入文件。
    
    同步方式将指定内容写入文件，会覆盖现有内容。
    
    Args:
        file_path: 文件路径（字符串或Path对象）
        content: 要写入的文本内容
        
    Raises:
        IOError: 如果写入文件时发生错误
    """
    with open(file_path, 'w') as f:
        f.write(content)