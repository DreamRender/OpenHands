# 导入 LLM 工具相关类型
from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# 历史压缩请求工具的描述
_CONDENSATION_REQUEST_DESCRIPTION = '当上下文变得过长或当您需要专注于最相关的信息时，请求压缩对话历史。'

# 创建历史压缩请求工具配置
CondensationRequestTool = ChatCompletionToolParam(
    type='function',
    function=ChatCompletionToolParamFunctionChunk(
        name='request_condensation',  # 工具名称
        description=_CONDENSATION_REQUEST_DESCRIPTION,  # 工具描述
        parameters={
            'type': 'object',
            'properties': {},   # 无参数属性
            'required': [],     # 无必需参数
        },
    ),
)