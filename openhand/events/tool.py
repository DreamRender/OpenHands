from litellm import ModelResponse
from pydantic import BaseModel


class ToolCallMetadata(BaseModel):
    """
    工具调用元数据类
    
    存储工具调用相关的元数据信息，包括函数名称、调用ID、Model响应等。
    这个类用于跟踪和记录工具调用的详细信息。
    
    参考链接：https://docs.litellm.ai/docs/completion/function_call#step-3---second-litellmcompletion-call
    
    Attributes:
        function_name (str): 被调用的函数名称
        tool_call_id (str): 工具调用的唯一标识符
        model_response (ModelResponse): Model的完整响应对象
        total_calls_in_response (int): 此响应中的总调用次数
    """
    
    function_name: str  # 被调用的函数名称
    tool_call_id: str   # 工具调用ID
    
    model_response: ModelResponse  # Model响应对象
    total_calls_in_response: int   # 响应中的总调用次数