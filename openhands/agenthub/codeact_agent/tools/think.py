from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# 思考工具的详细描述
# 说明了工具的用途、常见使用场景和功能限制
_THINK_DESCRIPTION = """Use the tool to think about something. It will not obtain new information or make any changes to the repository, but just log the thought. Use it when complex reasoning or brainstorming is needed.

Common use cases:
1. When exploring a repository and discovering the source of a bug, call this tool to brainstorm several unique ways of fixing the bug, and assess which change(s) are likely to be simplest and most effective.
2. After receiving test results, use this tool to brainstorm ways to fix failing tests.
3. When planning a complex refactoring, use this tool to outline different approaches and their tradeoffs.
4. When designing a new feature, use this tool to think through architecture decisions and implementation details.
5. When debugging a complex issue, use this tool to organize your thoughts and hypotheses.

The tool simply logs your thought process for better transparency and does not execute any code or make changes."""
# 翻译：使用该工具进行思考。它不会获取新信息或对Repository进行任何更改，只是记录思考过程。在需要复杂推理或头脑风暴时使用。
# 常见使用场景：
# 1. 探索Repository并发现bug源头时，调用此工具头脑风暴多种修复bug的独特方法，评估哪种更改可能最简单最有效
# 2. 收到测试结果后，使用此工具头脑风暴修复失败测试的方法
# 3. 规划复杂重构时，使用此工具概述不同方法及其权衡
# 4. 设计新功能时，使用此工具思考架构决策和实现细节
# 5. 调试复杂问题时，使用此工具整理思路和假设
# 该工具只记录思考过程以提高透明度，不执行任何代码或进行更改

# 思考工具配置
# 定义了一个简单的工具，用于记录思考过程，提高推理透明度
ThinkTool = ChatCompletionToolParam(
    type='function',  # 工具类型为函数
    function=ChatCompletionToolParamFunctionChunk(
        name='think',  # 工具名称：思考
        description=_THINK_DESCRIPTION,  # 工具的详细描述
        parameters={
            'type': 'object',  # 参数类型为对象
            'properties': {
                # 要记录的思考内容
                'thought': {
                    'type': 'string', 
                    'description': 'The thought to log.',
                    # 翻译：要记录的思考内容
                },
            },
            'required': ['thought'],  # 必需参数：思考内容
        },
    ),
)