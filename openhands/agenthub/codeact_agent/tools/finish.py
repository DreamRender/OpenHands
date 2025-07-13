# 导入 LLM 工具相关类型
from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# 导入工具名称常量
from openhands.llm.tool_names import FINISH_TOOL_NAME

# 完成工具的描述
_FINISH_DESCRIPTION = """标记当前任务或对话的完成。

在以下情况下使用此工具：
- 您已成功完成用户请求的任务
- 由于技术限制或缺少信息无法进一步进行

消息应包括：
- 所采取行动及其结果的清晰总结
- 用户的任何后续步骤
- 如果无法完成任务的解释
- 如果需要更多信息的任何后续问题

如果您认为已完成任务，task_completed 字段应设置为 True，否则设置为 False。
"""

# 创建完成工具配置
FinishTool = ChatCompletionToolParam(
    type='function',
    function=ChatCompletionToolParamFunctionChunk(
        name=FINISH_TOOL_NAME,  # 工具名称常量
        description=_FINISH_DESCRIPTION,  # 工具描述
        parameters={
            'type': 'object',
            'required': ['message', 'task_completed'],  # 必需参数列表
            'properties': {
                'message': {
                    'type': 'string',
                    'description': '发送给用户的最终消息',
                },
                'task_completed': {
                    'type': 'string',
                    'enum': ['true', 'false', 'partial'],  # 限制可选值
                    'description': '您是否已完成任务。',
                },
            },
        },
    ),
)