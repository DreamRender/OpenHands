from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# IPython代码执行工具的描述
# 说明了在IPython环境中运行Python代码的特点和限制
_IPYTHON_DESCRIPTION = """Run a cell of Python code in an IPython environment.
* The assistant should define variables and import packages before using them.
* The variable defined in the IPython environment will not be available outside the IPython environment (e.g., in terminal).
"""
# 翻译：在IPython环境中运行Python代码单元。
# assistant应该在使用变量和包之前先定义和导入它们。
# 在IPython环境中定义的变量在IPython环境外（如终端中）不可用。

# IPython代码执行工具配置
# 定义了在IPython环境中执行Python代码的工具，支持魔术命令
IPythonTool = ChatCompletionToolParam(
    type='function',  # 工具类型为函数
    function=ChatCompletionToolParamFunctionChunk(
        name='execute_ipython_cell',  # 工具名称：执行IPython单元
        description=_IPYTHON_DESCRIPTION,  # 工具描述
        parameters={
            'type': 'object',  # 参数类型为对象
            'properties': {
                # 要执行的Python代码参数
                'code': {
                    'type': 'string',
                    'description': 'The Python code to execute. Supports magic commands like %pip.',
                    # 翻译：要执行的Python代码。支持如%pip等魔术命令。
                },
            },
            'required': ['code'],  # 必需参数列表，code参数是必需的
        },
    ),
)