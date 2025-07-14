from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# 对话历史压缩请求工具的描述
# 用于在上下文过长或需要聚焦最相关信息时请求压缩对话历史
_CONDENSATION_REQUEST_DESCRIPTION = 'Request a condensation of the conversation history when the context becomes too long or when you need to focus on the most relevant information.'
# 翻译：当上下文变得过长或需要聚焦于最相关信息时，请求对话历史的压缩

# 对话历史压缩请求工具配置
# 定义了一个无参数的工具，用于触发对话历史的压缩处理
CondensationRequestTool = ChatCompletionToolParam(
    type='function',  # 工具类型为函数
    function=ChatCompletionToolParamFunctionChunk(
        name='request_condensation',  # 工具名称：请求压缩
        description=_CONDENSATION_REQUEST_DESCRIPTION,  # 工具描述
        parameters={
            'type': 'object',  # 参数类型为对象
            'properties': {},  # 无属性参数，因为这是一个简单的触发工具
            'required': [],  # 无必需参数
        },
    ),
)