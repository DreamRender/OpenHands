import json
from datetime import datetime

from json_repair import repair_json
from litellm.types.utils import ModelResponse

from openhands.core.exceptions import LLMResponseError
from openhands.events.event import Event
from openhands.events.observation import CmdOutputMetadata
from openhands.events.serialization import event_to_dict
from openhands.llm.metrics import Metrics


class OpenHandsJSONEncoder(json.JSONEncoder):
    """
    自定义JSON编码器，用于处理datetime和Event对象的序列化。
    
    该编码器继承自json.JSONEncoder，能够序列化标准JSON不支持的类型，
    包括datetime对象、Event对象、Metrics对象、ModelResponse对象和CmdOutputMetadata对象。
    """

    def default(self, obj):
        """
        重写default方法以处理自定义对象类型的序列化。
        
        Args:
            obj: 需要序列化的对象
            
        Returns:
            序列化后的对象，可以是基本数据类型或字典
            
        Notes:
            处理以下类型的对象：
            - datetime: 转换为ISO格式字符串
            - Event: 转换为字典格式
            - Metrics: 调用get()方法获取数据
            - ModelResponse: 调用model_dump()方法转换为字典
            - CmdOutputMetadata: 调用model_dump()方法转换为字典
        """
        if isinstance(obj, datetime):
            # 将datetime对象转换为ISO格式字符串
            return obj.isoformat()
        if isinstance(obj, Event):
            # 将Event对象转换为字典格式
            return event_to_dict(obj)
        if isinstance(obj, Metrics):
            # 获取Metrics对象的数据
            return obj.get()
        if isinstance(obj, ModelResponse):
            # 将ModelResponse对象转换为字典
            return obj.model_dump()
        if isinstance(obj, CmdOutputMetadata):
            # 将CmdOutputMetadata对象转换为字典
            return obj.model_dump()
        # 对于其他类型，调用父类的default方法
        return super().default(obj)


# 创建一个可重复使用的编码器实例，避免重复创建对象
_json_encoder = OpenHandsJSONEncoder()


def dumps(obj, **kwargs):
    """
    将对象序列化为JSON字符串格式。
    
    Args:
        obj: 需要序列化的对象
        **kwargs: 传递给json.dumps的额外参数
        
    Returns:
        str: JSON格式的字符串
        
    Notes:
        如果没有额外参数，使用预创建的编码器实例以提高性能。
        如果有额外参数，创建新的编码器实例。
    """
    if not kwargs:
        # 没有额外参数时，使用预创建的编码器实例
        return _json_encoder.encode(obj)

    # 创建kwargs的副本，避免修改原始参数
    encoder_kwargs = kwargs.copy()

    # 如果没有指定cls参数，使用我们的自定义编码器
    if 'cls' not in encoder_kwargs:
        encoder_kwargs['cls'] = OpenHandsJSONEncoder

    return json.dumps(obj, **encoder_kwargs)


def loads(json_str, **kwargs):
    """
    从JSON字符串创建Python对象，包含错误修复功能。
    
    Args:
        json_str (str): 要解析的JSON字符串
        **kwargs: 传递给json.loads的额外参数
        
    Returns:
        解析后的Python对象
        
    Raises:
        LLMResponseError: 当JSON无效且无法修复时抛出异常
        
    Notes:
        该函数具有JSON修复功能：
        1. 首先尝试标准JSON解析
        2. 如果失败，尝试从字符串中提取第一个完整的JSON对象
        3. 使用json_repair库修复可能的JSON格式错误
        4. 如果仍然失败，抛出LLMResponseError异常
    """
    try:
        # 首先尝试标准的JSON解析
        return json.loads(json_str, **kwargs)
    except json.JSONDecodeError:
        # 如果标准解析失败，进入修复模式
        pass
    
    # JSON修复逻辑：寻找第一个完整的JSON对象
    depth = 0  # 跟踪大括号的嵌套深度
    start = -1  # 记录JSON对象开始的位置
    
    for i, char in enumerate(json_str):
        if char == '{':
            if depth == 0:
                # 找到最外层JSON对象的开始位置
                start = i
            depth += 1  # 进入更深的嵌套层
        elif char == '}':
            depth -= 1  # 退出一层嵌套
            if depth == 0 and start != -1:
                # 找到完整的JSON对象（depth回到0且有开始位置）
                response = json_str[start : i + 1]
                try:
                    # 使用json_repair库修复可能的JSON格式错误
                    json_str = repair_json(response)
                    return json.loads(json_str, **kwargs)
                except (json.JSONDecodeError, ValueError, TypeError) as e:
                    # 修复失败，抛出自定义异常
                    raise LLMResponseError(
                        'Invalid JSON in response. Please make sure the response is a valid JSON object.'
                    ) from e
    
    # 如果没有找到有效的JSON对象，抛出异常
    raise LLMResponseError('No valid JSON object found in response.')