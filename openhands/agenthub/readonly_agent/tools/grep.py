"""
grep.py - Grep工具定义模块

该模块定义了Grep工具的参数和描述，用于在文件内容中进行快速搜索。
Grep工具支持正则表达式搜索，可以在指定目录中查找包含特定模式的文件。
"""

from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# Grep工具的描述信息，定义了工具的功能特性和使用场景
_GREP_DESCRIPTION = """快速内容搜索工具。
* 使用正则表达式搜索文件内容
* 支持完整的正则表达式语法（例如："log.*Error"、"function\\s+\\w+"等）
* 通过include参数按模式过滤文件（例如："*.js"、"*.{ts,tsx}"）
* 返回按修改时间排序的匹配文件路径。
* 仅返回前100个结果。如果需要更多结果，请考虑使用更严格的正则表达式模式缩小搜索范围或提供path参数。
* 当您需要查找包含特定模式的文件时使用此工具
* 当您进行可能需要多轮globbing和grepping的开放式搜索时，请改用Agent工具
"""

# Grep工具的完整配置，包括工具类型、函数定义和参数规范
GrepTool = ChatCompletionToolParam(
    type='function',  # 工具类型：函数调用
    function=ChatCompletionToolParamFunctionChunk(
        name='grep',  # 工具名称
        description=_GREP_DESCRIPTION,  # 工具描述
        parameters={  # 工具参数定义
            'type': 'object',  # 参数类型为对象
            'properties': {  # 参数属性定义
                'pattern': {
                    'type': 'string',  # 字符串类型
                    'description': '要在文件内容中搜索的正则表达式模式',
                },
                'path': {
                    'type': 'string',  # 字符串类型
                    'description': '要搜索的目录（绝对路径）。默认为当前工作目录。',
                },
                'include': {
                    'type': 'string',  # 字符串类型
                    'description': '用于过滤要搜索文件的可选文件模式（例如："*.js"、"*.{ts,tsx}"）',
                },
            },
            'required': ['pattern'],  # 必需参数：pattern
        },
    ),
)