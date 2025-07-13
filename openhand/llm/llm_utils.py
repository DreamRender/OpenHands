"""
LLM工具函数模块

该模块提供了与LLM（大语言Model）工具相关的实用函数，主要用于处理不同Model之间的兼容性问题。
特别是针对Gemini Model的特殊处理，因为它们对工具格式有特定的限制。
"""

import copy  # 导入copy模块，用于深拷贝操作
from typing import TYPE_CHECKING  # 导入类型检查标志，避免循环导入

from openhands.core.config import LLMConfig  # 导入LLM配置类
from openhands.core.logger import openhands_logger as logger  # 导入日志记录器

# 类型检查时才导入，避免运行时循环导入问题
if TYPE_CHECKING:
    from litellm import ChatCompletionToolParam


def check_tools(
    tools: list['ChatCompletionToolParam'], llm_config: LLMConfig
) -> list['ChatCompletionToolParam']:
    """
    检查并修改工具以确保与当前LLM的兼容性
    
    该函数主要处理不同LLM Model对工具格式的特殊要求，特别是Gemini Model。
    Gemini Model不支持默认字段，并且对格式支持有限制。
    
    Args:
        tools (list[ChatCompletionToolParam]): 需要检查的工具列表
        llm_config (LLMConfig): LLM配置对象，包含Model信息
        
    Returns:
        list[ChatCompletionToolParam]: 经过兼容性处理后的工具列表
        
    Note:
        - 对于Gemini Model，会移除默认字段和不支持的格式
        - Gemini仅支持STRING类型的'enum'和'date-time'格式
    """
    # 检查是否为Gemini Model（通过Model名称判断）
    if 'gemini' in llm_config.model.lower():
        logger.info(
            f'正在为Gemini Model {llm_config.model} 移除默认字段和不支持的格式 '
            "因为Gemini Model对格式支持有限（STRING类型仅支持'enum'和'date-time'格式）。"
        )
        
        # 防止修改输入的工具列表，创建深拷贝
        checked_tools = copy.deepcopy(tools)
        
        # 遍历每个工具，移除会导致Gemini预览版错误的默认字段和不支持的格式
        for tool in checked_tools:
            # 检查工具是否包含function字段和parameters字段
            if 'function' in tool and 'parameters' in tool['function']:
                # 检查是否包含properties字段
                if 'properties' in tool['function']['parameters']:
                    # 遍历每个属性
                    for prop_name, prop in tool['function']['parameters'][
                        'properties'
                    ].items():
                        # 移除默认字段（Gemini不支持）
                        if 'default' in prop:
                            del prop['default']

                        # 对于STRING类型的参数，移除不支持的格式字段
                        # Gemini仅支持'enum'和'date-time'格式
                        if prop.get('type') == 'string' and 'format' in prop:
                            supported_formats = ['enum', 'date-time']  # Gemini支持的格式列表
                            if prop['format'] not in supported_formats:
                                logger.info(
                                    f'移除STRING参数"{prop_name}"的不支持格式"{prop["format"]}"'
                                )
                                del prop['format']
        return checked_tools
    
    # 非Gemini Model直接返回原工具列表
    return tools