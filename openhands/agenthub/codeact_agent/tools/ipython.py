# 导入 LLM 工具相关类型
from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# IPython 工具的描述
_IPYTHON_DESCRIPTION = """在 IPython 环境中运行 Python 代码单元。
* 助手应在使用变量和包之前定义变量并导入包。
* 在 IPython 环境中定义的变量在 IPython 环境外部（例如在终端中）将不可用。
"""

# 创建 IPython 工具配置
IPythonTool = ChatCompletionToolParam(
    type='function',
    function=ChatCompletionToolParamFunctionChunk(
        name='execute_ipython_cell',  # 工具名称
        description=_IPYTHON_DESCRIPTION,  # 工具描述
        parameters={
            'type': 'object',
            'properties': {
                'code': {
                    'type': 'string',
                    'description': '要执行的 Python 代码。支持魔法命令如 %pip。',
                },
            },
            'required': ['code'],  # 必需参数
        },
    ),
)