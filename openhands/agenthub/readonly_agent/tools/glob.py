"""
glob.py - Glob工具定义模块

该模块定义了Glob工具的参数和描述，用于文件模式匹配和文件查找。
Glob工具支持通配符模式匹配，可以根据文件名模式快速定位文件。
"""

from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# Glob工具的描述信息，定义了工具的功能特性和使用限制
_GLOB_DESCRIPTION = """快速文件模式匹配工具。
* 支持glob模式，如"**/*.js"或"src/**/*.ts"
* 当您需要按名称模式查找文件时使用此工具
* 返回按修改时间排序的匹配文件路径
* 仅返回前100个结果。如果需要更多结果，请考虑使用更严格的glob模式缩小搜索范围或提供path参数。
* 当您进行可能需要多轮globbing和grepping的开放式搜索时，请改用Agent工具
"""

# Glob工具的完整配置，包括工具类型、函数定义和参数规范
GlobTool = ChatCompletionToolParam(
    type='function',  # 工具类型：函数调用
    function=ChatCompletionToolParamFunctionChunk(
        name='glob',  # 工具名称
        description=_GLOB_DESCRIPTION,  # 工具描述
        parameters={  # 工具参数定义
            'type': 'object',  # 参数类型为对象
            'properties': {  # 参数属性定义
                'pattern': {
                    'type': 'string',  # 字符串类型
                    'description': '用于匹配文件的glob模式（例如："**/*.js"、"src/**/*.ts"）',
                },
                'path': {
                    'type': 'string',  # 字符串类型
                    'description': '要搜索的目录（绝对路径）。默认为当前工作目录。',
                },
            },
            'required': ['pattern'],  # 必需参数：pattern
        },
    ),
)