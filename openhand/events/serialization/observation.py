"""
observation.py - Observation序列化模块

该模块负责处理各种Observation的序列化和反序列化操作。
包含了从字典创建Observation对象的核心功能，以及处理已废弃字段的兼容性逻辑。
"""

import copy
from typing import Any

from openhands.events.event import RecallType
from openhands.events.observation.agent import (
    AgentCondensationObservation,
    AgentStateChangedObservation,
    AgentThinkObservation,
    MicroagentKnowledge,
    RecallObservation,
)
from openhands.events.observation.browse import BrowserOutputObservation
from openhands.events.observation.commands import (
    CmdOutputMetadata,
    CmdOutputObservation,
    IPythonRunCellObservation,
)
from openhands.events.observation.delegate import AgentDelegateObservation
from openhands.events.observation.empty import (
    NullObservation,
)
from openhands.events.observation.error import ErrorObservation
from openhands.events.observation.file_download import FileDownloadObservation
from openhands.events.observation.files import (
    FileEditObservation,
    FileReadObservation,
    FileWriteObservation,
)
from openhands.events.observation.mcp import MCPObservation
from openhands.events.observation.observation import Observation
from openhands.events.observation.reject import UserRejectObservation
from openhands.events.observation.success import SuccessObservation

# 所有可用的Observation类型元组
# 包含系统支持的所有Observation类，用于类型映射和验证
observations = (
    NullObservation,                # 空Observation，表示无观察结果
    CmdOutputObservation,           # 命令输出Observation
    IPythonRunCellObservation,      # IPython单元格执行结果Observation
    BrowserOutputObservation,       # 浏览器输出Observation
    FileReadObservation,            # 文件读取结果Observation
    FileWriteObservation,           # 文件写入结果Observation
    FileEditObservation,            # 文件编辑结果Observation
    AgentDelegateObservation,       # Agent委托结果Observation
    SuccessObservation,             # 成功执行Observation
    ErrorObservation,               # 错误Observation
    AgentStateChangedObservation,   # Agent状态变更Observation
    UserRejectObservation,          # 用户拒绝Observation
    AgentCondensationObservation,   # Agent Condenser压缩Observation
    AgentThinkObservation,          # Agent思考过程Observation
    RecallObservation,              # 回忆Observation
    MCPObservation,                 # MCP协议Observation
    FileDownloadObservation,        # 文件下载Observation
)

# Observation类型字符串到Observation类的映射字典
# 键为observation.observation属性值，值为对应的Observation类
# 用于根据字符串类型创建对应的Observation实例
OBSERVATION_TYPE_TO_CLASS = {
    observation_class.observation: observation_class  # type: ignore[attr-defined]
    for observation_class in observations
}


def _update_cmd_output_metadata(
    metadata: dict[str, Any] | CmdOutputMetadata | None, **kwargs: Any
) -> dict[str, Any] | CmdOutputMetadata:
    """
    更新CmdOutputObservation的Metadata。
    
    该函数用于更新命令输出观察的Metadata信息，支持多种输入格式，
    确保Metadata能够正确更新或创建。
    
    Args:
        metadata (dict[str, Any] | CmdOutputMetadata | None): 
            现有的Metadata，可以是字典、CmdOutputMetadata实例或None
        **kwargs (Any): 要更新的键值对
        
    Returns:
        dict[str, Any] | CmdOutputMetadata: 更新后的Metadata
        
    Note:
        - 如果metadata为None，创建新的CmdOutputMetadata实例
        - 如果metadata为字典，直接更新字典内容
        - 如果metadata为CmdOutputMetadata实例，更新实例属性
    """
    if metadata is None:
        # 创建新的CmdOutputMetadata实例
        return CmdOutputMetadata(**kwargs)

    if isinstance(metadata, dict):
        # 直接更新字典内容
        metadata.update(**kwargs)
    elif isinstance(metadata, CmdOutputMetadata):
        # 更新CmdOutputMetadata实例的属性
        for key, value in kwargs.items():
            setattr(metadata, key, value)
    
    return metadata


def handle_observation_deprecated_extras(extras: dict) -> dict:
    """
    处理Observation中已废弃的额外字段。
    
    该函数用于维护向后兼容性，处理在系统演进过程中被废弃的字段，
    确保旧版本的Observation数据仍能正确处理。
    
    Args:
        extras (dict): 原始的extras字典
        
    Returns:
        dict: 处理后的extras字典，移除或转换已废弃的字段
    """
    # 这些字段在PR #4881中被废弃
    
    # exit_code字段已废弃，转换为Metadata中的exit_code
    if 'exit_code' in extras:
        extras['metadata'] = _update_cmd_output_metadata(
            extras.get('metadata', None), exit_code=extras.pop('exit_code')
        )
    
    # command_id字段已废弃，转换为Metadata中的pid
    if 'command_id' in extras:
        extras['metadata'] = _update_cmd_output_metadata(
            extras.get('metadata', None), pid=extras.pop('command_id')
        )

    # formatted_output_and_error字段在PR #6671中被废弃，直接移除
    if 'formatted_output_and_error' in extras:
        extras.pop('formatted_output_and_error')
    
    return extras


def observation_from_dict(observation: dict) -> Observation:
    """
    从字典创建Observation对象。
    
    该函数是Observation反序列化的核心方法，负责将字典格式的Observation数据
    转换为对应的Observation对象实例，同时处理各种特殊字段和兼容性问题。
    
    Args:
        observation (dict): 包含Observation信息的字典，必须包含'observation'键
        
    Returns:
        Observation: 创建的Observation对象实例
        
    Raises:
        KeyError: 当'observation'键不存在或Observation类型未定义时抛出
    """
    # 创建字典副本，避免修改原始数据
    observation = observation.copy()
    
    # 验证必需的'observation'键是否存在
    if 'observation' not in observation:
        raise KeyError(f"'observation' key is not found in {observation=}")
    
    # 根据observation类型获取对应的Observation类
    observation_class = OBSERVATION_TYPE_TO_CLASS.get(observation['observation'])
    if observation_class is None:
        raise KeyError(
            f"'{observation['observation']=}' is not defined. Available observations: {OBSERVATION_TYPE_TO_CLASS.keys()}"
        )
    
    # 移除已处理的字段
    observation.pop('observation')  # 移除类型标识符
    observation.pop('message', None)  # 移除消息字段（如果存在）
    
    # 提取内容和额外字段
    content = observation.pop('content', '')
    extras = copy.deepcopy(observation.pop('extras', {}))

    # 处理已废弃的额外字段
    extras = handle_observation_deprecated_extras(extras)

    # 特殊处理CmdOutputObservation的Metadata
    if observation_class is CmdOutputObservation:
        if 'metadata' in extras and isinstance(extras['metadata'], dict):
            # 将字典转换为CmdOutputMetadata对象
            extras['metadata'] = CmdOutputMetadata(**extras['metadata'])
        elif 'metadata' in extras and isinstance(extras['metadata'], CmdOutputMetadata):
            # 已经是CmdOutputMetadata对象，无需转换
            pass
        else:
            # 创建默认的CmdOutputMetadata对象
            extras['metadata'] = CmdOutputMetadata()

    # 特殊处理RecallObservation
    if observation_class is RecallObservation:
        # 处理枚举转换
        if 'recall_type' in extras:
            extras['recall_type'] = RecallType(extras['recall_type'])

        # 将microagent_knowledge中的字典转换为MicroagentKnowledge对象
        if 'microagent_knowledge' in extras and isinstance(
            extras['microagent_knowledge'], list
        ):
            extras['microagent_knowledge'] = [
                MicroagentKnowledge(**item) if isinstance(item, dict) else item
                for item in extras['microagent_knowledge']
            ]

    # 创建Observation实例
    obs = observation_class(content=content, **extras)
    
    # 确保返回的对象是Observation的实例
    assert isinstance(obs, Observation)
    return obs