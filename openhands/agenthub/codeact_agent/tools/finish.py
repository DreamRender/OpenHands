from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

from openhands.llm.tool_names import FINISH_TOOL_NAME

# 任务完成工具的详细描述
# 说明了何时使用该工具、消息应包含的内容以及任务完成状态的设置要求
_FINISH_DESCRIPTION = """Signals the completion of the current task or conversation.

Use this tool when:
- You have successfully completed the user's requested task
- You cannot proceed further due to technical limitations or missing information

The message should include:
- A clear summary of actions taken and their results
- Any next steps for the user
- Explanation if you're unable to complete the task
- Any follow-up questions if more information is needed

The task_completed field should be set to True if you believed you have completed the task, and False otherwise.
"""
# 翻译：表示当前任务或对话的完成。
# 使用场景：成功完成用户请求的任务，或由于技术限制或信息缺失无法继续进行。
# 消息应包含：已采取行动的清晰总结及其结果、用户的后续步骤、无法完成任务的解释、需要更多信息时的后续问题。
# task_completed字段应在认为已完成任务时设为True，否则设为False。

# 任务完成工具配置
# 定义了完成任务时需要提供的消息和完成状态参数
FinishTool = ChatCompletionToolParam(
    type='function',  # 工具类型为函数
    function=ChatCompletionToolParamFunctionChunk(
        name=FINISH_TOOL_NAME,  # 工具名称，从常量导入
        description=_FINISH_DESCRIPTION,  # 工具的详细描述
        parameters={
            'type': 'object',  # 参数类型为对象
            'required': ['message', 'task_completed'],  # 必需参数：消息和任务完成状态
            'properties': {
                # 发送给用户的最终消息
                'message': {
                    'type': 'string',
                    'description': 'Final message to send to the user',
                    # 翻译：发送给用户的最终消息
                },
                # 任务完成状态
                'task_completed': {
                    'type': 'string',
                    'enum': ['true', 'false', 'partial'],  # 枚举值：完全完成、未完成、部分完成
                    'description': 'Whether you have completed the task.',
                    # 翻译：是否已完成任务
                },
            },
        },
    ),
)