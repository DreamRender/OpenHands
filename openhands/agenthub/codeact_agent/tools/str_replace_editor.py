# 导入 LLM 工具相关类型
from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# 导入工具名称常量
from openhands.llm.tool_names import STR_REPLACE_EDITOR_TOOL_NAME

# 详细的字符串替换编辑器描述 - 用于大部分模型
_DETAILED_STR_REPLACE_EDITOR_DESCRIPTION = """用于查看、创建和编辑纯文本格式文件的自定义编辑工具
* 状态在命令调用和与用户的讨论之间保持持久
* 如果 `path` 是文本文件，`view` 显示应用 `cat -n` 的结果。如果 `path` 是目录，`view` 列出最多 2 级深度的非隐藏文件和目录
* 以下二进制文件扩展名可以以 Markdown 格式查看：[".xlsx", ".pptx", ".wav", ".mp3", ".m4a", ".flac", ".pdf", ".docx"]。它不处理图像。
* 如果指定的 `path` 已经作为文件存在，则不能使用 `create` 命令
* 如果 `command` 生成长输出，它将被截断并标记为 `<response clipped>`
* `undo_edit` 命令将恢复对 `path` 处文件的最后一次编辑
* 此工具可用于创建和编辑纯文本格式的文件。


使用此工具之前：
1. 使用 view 工具了解文件的内容和上下文
2. 验证目录路径是否正确（仅在创建新文件时适用）：
   - 使用 view 工具验证父目录是否存在且位置正确

进行编辑时：
   - 确保编辑结果是惯用的、正确的代码
   - 不要让代码处于损坏状态
   - 始终使用绝对文件路径（以 / 开头）

使用此工具的关键要求：

1. 精确匹配：`old_str` 参数必须与文件中的一行或多行连续行精确匹配，包括所有空格和缩进。如果 `old_str` 匹配多个位置或与文件内容不精确匹配，工具将失败。

2. 唯一性：`old_str` 必须唯一标识文件中的单个实例：
   - 在更改点之前和之后包含足够的上下文（建议 3-5 行）
   - 如果不唯一，将不会执行替换

3. 替换：`new_str` 参数应包含替换 `old_str` 的编辑行。两个字符串必须不同。

记住：当连续对同一文件进行多次文件编辑时，您应该优先在单个消息中发送所有编辑，并多次调用此工具，而不是每次调用一个工具的多个消息。
"""

# 简短的字符串替换编辑器描述 - 用于有 token 限制的模型
_SHORT_STR_REPLACE_EDITOR_DESCRIPTION = """用于查看、创建和编辑纯文本格式文件的自定义编辑工具
* 状态在命令调用和与用户的讨论之间保持持久
* 如果 `path` 是文件，`view` 显示应用 `cat -n` 的结果。如果 `path` 是目录，`view` 列出最多 2 级深度的非隐藏文件和目录
* 如果指定的 `path` 已经作为文件存在，则不能使用 `create` 命令
* 如果 `command` 生成长输出，它将被截断并标记为 `<response clipped>`
* `undo_edit` 命令将恢复对 `path` 处文件的最后一次编辑
使用 `str_replace` 命令的注意事项：
* `old_str` 参数应该与原始文件中的一行或多行连续行精确匹配。注意空格！
* 如果 `old_str` 参数在文件中不唯一，将不会执行替换。确保在 `old_str` 中包含足够的上下文以使其唯一
* `new_str` 参数应包含应该替换 `old_str` 的编辑行
"""


def create_str_replace_editor_tool(
    use_short_description: bool = False,
) -> ChatCompletionToolParam:
    """创建字符串替换编辑器工具的配置。
    
    根据是否需要简短描述来创建字符串替换编辑器工具。
    对于 token 限制严格的模型使用简短描述。
    
    Args:
        use_short_description (bool): 是否使用简短描述，默认为 False
        
    Returns:
        ChatCompletionToolParam: 配置好的字符串替换编辑器工具参数
    """
    # 根据参数选择描述类型
    description = (
        _SHORT_STR_REPLACE_EDITOR_DESCRIPTION
        if use_short_description
        else _DETAILED_STR_REPLACE_EDITOR_DESCRIPTION
    )
    
    return ChatCompletionToolParam(
        type='function',
        function=ChatCompletionToolParamFunctionChunk(
            name=STR_REPLACE_EDITOR_TOOL_NAME,  # 工具名称常量
            description=description,  # 根据需要选择的描述
            parameters={
                'type': 'object',
                'properties': {
                    'command': {
                        'description': '要运行的命令。允许的选项有：`view`、`create`、`str_replace`、`insert`、`undo_edit`。',
                        'enum': [
                            'view',         # 查看文件或目录
                            'create',       # 创建新文件
                            'str_replace',  # 字符串替换编辑
                            'insert',       # 插入内容
                            'undo_edit',    # 撤销编辑
                        ],
                        'type': 'string',
                    },
                    'path': {
                        'description': '文件或目录的绝对路径，例如 `/workspace/file.py` 或 `/workspace`。',
                        'type': 'string',
                    },
                    'file_text': {
                        'description': '`create` 命令的必需参数，包含要创建的文件内容。',
                        'type': 'string',
                    },
                    'old_str': {
                        'description': '`str_replace` 命令的必需参数，包含要在 `path` 中替换的字符串。',
                        'type': 'string',
                    },
                    'new_str': {
                        'description': '`str_replace` 命令的可选参数，包含新字符串（如果未给出，不会添加字符串）。`insert` 命令的必需参数，包含要插入的字符串。',
                        'type': 'string',
                    },
                    'insert_line': {
                        'description': '`insert` 命令的必需参数。`new_str` 将在 `path` 的第 `insert_line` 行之后插入。',
                        'type': 'integer',
                    },
                    'view_range': {
                        'description': '当 `path` 指向文件时，`view` 命令的可选参数。如果未给出，将显示完整文件。如果提供，文件将在指定的行号范围内显示，例如 [11, 12] 将显示第 11 和 12 行。索引从 1 开始。设置 `[start_line, -1]` 显示从 `start_line` 到文件末尾的所有行。',
                        'items': {'type': 'integer'},
                        'type': 'array',
                    },
                },
                'required': ['command', 'path'],  # 必需参数
            },
        ),
    )