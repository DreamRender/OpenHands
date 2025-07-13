"""
view.py - View工具定义模块

该模块定义了View工具的参数和描述，用于读取文件或列出目录内容。
View工具是一个只读工具，主要用于文件系统的查看操作。
"""

from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# View工具的描述信息，定义了工具的功能和使用规则
_VIEW_DESCRIPTION = """从本地文件系统读取文件或列出目录内容。
* path参数必须是绝对路径，不能是相对路径。
* 如果`path`是文件，`view`显示应用`cat -n`的结果；如果`path`是目录，`view`列出非隐藏文件和目录，最多深入2级。
* 您可以选择性地指定行范围来查看（对于长文件特别有用），但建议通过不提供此参数来读取整个文件。
* 对于图像文件，工具将为您显示图像。
* 对于超过显示限制的大文件：
  - 输出将被截断并标记为`<response clipped>`
  - 使用`view_range`参数在截断点后查看特定部分
"""

# View工具的完整配置，包括工具类型、函数定义和参数规范
ViewTool = ChatCompletionToolParam(
    type='function',  # 工具类型：函数调用
    function=ChatCompletionToolParamFunctionChunk(
        name='view',  # 工具名称
        description=_VIEW_DESCRIPTION,  # 工具描述
        parameters={  # 工具参数定义
            'type': 'object',  # 参数类型为对象
            'properties': {  # 参数属性定义
                'path': {
                    'type': 'string',  # 字符串类型
                    'description': '要读取的文件或要列出的目录的绝对路径',
                },
                'view_range': {
                    'description': '当`path`指向*文件*时`view`命令的可选参数。如果未提供，则显示完整文件。如果提供，文件将在指定的行号范围内显示，例如[11, 12]将显示第11行和第12行。索引从1开始。设置`[start_line, -1]`显示从`start_line`到文件末尾的所有行。',
                    'items': {'type': 'integer'},  # 数组元素类型为整数
                    'type': 'array',  # 数组类型
                },
            },
            'required': ['path'],  # 必需参数：path
        },
    ),
)