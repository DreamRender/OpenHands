# 导入 LLM 工具相关类型
from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# 思考工具的描述
_THINK_DESCRIPTION = """使用该工具来思考某事。它不会获取新信息或对 Repository 进行任何更改，只是记录思考过程。在需要复杂推理或头脑风暴时使用。

常见用例：
1. 在探索 Repository 并发现错误来源时，调用此工具头脑风暴几种独特的修复错误方法，并评估哪些更改可能最简单、最有效。
2. 收到测试结果后，使用此工具头脑风暴修复失败测试的方法。
3. 计划复杂重构时，使用此工具概述不同方法及其权衡。
4. 设计新功能时，使用此工具思考架构决策和实施细节。
5. 调试复杂问题时，使用此工具组织您的思路和假设。

该工具只是记录您的思考过程以提高透明度，不执行任何代码或进行更改。"""

# 创建思考工具配置
ThinkTool = ChatCompletionToolParam(
    type='function',
    function=ChatCompletionToolParamFunctionChunk(
        name='think',  # 工具名称
        description=_THINK_DESCRIPTION,  # 工具描述
        parameters={
            'type': 'object',
            'properties': {
                'thought': {
                    'type': 'string', 
                    'description': '要记录的思考内容。'
                },
            },
            'required': ['thought'],  # 必需参数
        },
    ),
)