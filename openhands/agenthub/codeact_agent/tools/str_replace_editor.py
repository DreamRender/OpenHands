from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

from openhands.llm.tool_names import STR_REPLACE_EDITOR_TOOL_NAME

# 字符串替换编辑器工具的详细描述
# 包含完整的工具功能说明、使用要求和关键限制条件
_DETAILED_STR_REPLACE_EDITOR_DESCRIPTION = """Custom editing tool for viewing, creating and editing files in plain-text format
* State is persistent across command calls and discussions with the user
* If `path` is a text file, `view` displays the result of applying `cat -n`. If `path` is a directory, `view` lists non-hidden files and directories up to 2 levels deep
* The following binary file extensions can be viewed in Markdown format: [".xlsx", ".pptx", ".wav", ".mp3", ".m4a", ".flac", ".pdf", ".docx"]. IT DOES NOT HANDLE IMAGES.
* The `create` command cannot be used if the specified `path` already exists as a file
* If a `command` generates a long output, it will be truncated and marked with `<response clipped>`
* The `undo_edit` command will revert the last edit made to the file at `path`
* This tool can be used for creating and editing files in plain-text format.


Before using this tool:
1. Use the view tool to understand the file's contents and context
2. Verify the directory path is correct (only applicable when creating new files):
   - Use the view tool to verify the parent directory exists and is the correct location

When making edits:
   - Ensure the edit results in idiomatic, correct code
   - Do not leave the code in a broken state
   - Always use absolute file paths (starting with /)

CRITICAL REQUIREMENTS FOR USING THIS TOOL:

1. EXACT MATCHING: The `old_str` parameter must match EXACTLY one or more consecutive lines from the file, including all whitespace and indentation. The tool will fail if `old_str` matches multiple locations or doesn't match exactly with the file content.

2. UNIQUENESS: The `old_str` must uniquely identify a single instance in the file:
   - Include sufficient context before and after the change point (3-5 lines recommended)
   - If not unique, the replacement will not be performed

3. REPLACEMENT: The `new_str` parameter should contain the edited lines that replace the `old_str`. Both strings must be different.

Remember: when making multiple file edits in a row to the same file, you should prefer to send all edits in a single message with multiple calls to this tool, rather than multiple messages with a single call each.
"""
# 翻译：用于查看、创建和编辑纯文本格式文件的自定义编辑工具。
# 状态在命令调用和与用户讨论之间持久保存。
# 如果path是文本文件，view显示cat -n的结果；如果是目录，view列出最多2级深度的非隐藏文件和目录。
# 以下二进制文件扩展名可以Markdown格式查看：[".xlsx", ".pptx", ".wav", ".mp3", ".m4a", ".flac", ".pdf", ".docx"]。不处理图像。
# 如果指定的path已存在为文件，则不能使用create命令。
# 如果命令生成长输出，将被截断并标记为<response clipped>。
# undo_edit命令将恢复对指定路径文件的最后一次编辑。
# 使用前：1.使用view工具了解文件内容和上下文 2.验证目录路径正确（仅适用于创建新文件时）
# 编辑时：确保编辑结果是符合习惯的正确代码，不要让代码处于损坏状态，始终使用绝对文件路径。
# 关键要求：1.精确匹配：old_str必须精确匹配文件中一行或多行连续行，包括所有空格和缩进
# 2.唯一性：old_str必须唯一标识文件中的单个实例 3.替换：new_str应包含替换old_str的编辑行

# 字符串替换编辑器工具的简短描述
# 提供核心功能的简洁说明，适用于需要紧凑描述的场景
_SHORT_STR_REPLACE_EDITOR_DESCRIPTION = """Custom editing tool for viewing, creating and editing files in plain-text format
* State is persistent across command calls and discussions with the user
* If `path` is a file, `view` displays the result of applying `cat -n`. If `path` is a directory, `view` lists non-hidden files and directories up to 2 levels deep
* The `create` command cannot be used if the specified `path` already exists as a file
* If a `command` generates a long output, it will be truncated and marked with `<response clipped>`
* The `undo_edit` command will revert the last edit made to the file at `path`
Notes for using the `str_replace` command:
* The `old_str` parameter should match EXACTLY one or more consecutive lines from the original file. Be mindful of whitespaces!
* If the `old_str` parameter is not unique in the file, the replacement will not be performed. Make sure to include enough context in `old_str` to make it unique
* The `new_str` parameter should contain the edited lines that should replace the `old_str`
"""
# 翻译：用于查看、创建和编辑纯文本格式文件的自定义编辑工具。
# str_replace命令使用注意事项：old_str参数应精确匹配原文件中的一行或多行连续行，注意空格！
# 如果old_str在文件中不唯一，将不执行替换。确保在old_str中包含足够的上下文使其唯一。
# new_str参数应包含应替换old_str的编辑行。


def create_str_replace_editor_tool(
    use_short_description: bool = False,
) -> ChatCompletionToolParam:
    """
    创建字符串替换编辑器工具的配置参数
    
    Args:
        use_short_description (bool): 是否使用简短描述，默认为False使用详细描述
        
    Returns:
        ChatCompletionToolParam: 配置好的字符串替换编辑器工具参数，包含工具名称、描述和参数定义
    """
    # 根据参数选择使用详细描述还是简短描述
    description = (
        _SHORT_STR_REPLACE_EDITOR_DESCRIPTION
        if use_short_description
        else _DETAILED_STR_REPLACE_EDITOR_DESCRIPTION
    )
    
    # 创建并返回聊天完成工具参数配置
    return ChatCompletionToolParam(
        type='function',  # 工具类型为函数
        function=ChatCompletionToolParamFunctionChunk(
            name=STR_REPLACE_EDITOR_TOOL_NAME,  # 工具名称，从常量导入
            description=description,  # 根据参数选择的描述
            parameters={
                'type': 'object',  # 参数类型为对象
                'properties': {
                    # 要执行的命令参数
                    'command': {
                        'description': 'The commands to run. Allowed options are: `view`, `create`, `str_replace`, `insert`, `undo_edit`.',
                        # 翻译：要运行的命令。允许的选项有：view(查看)、create(创建)、str_replace(字符串替换)、insert(插入)、undo_edit(撤销编辑)
                        'enum': [
                            'view',      # 查看文件或目录
                            'create',    # 创建文件
                            'str_replace',  # 字符串替换
                            'insert',    # 插入内容
                            'undo_edit', # 撤销编辑
                        ],
                        'type': 'string',
                    },
                    # 文件或目录的绝对路径
                    'path': {
                        'description': 'Absolute path to file or directory, e.g. `/workspace/file.py` or `/workspace`.',
                        # 翻译：文件或目录的绝对路径，例如 /workspace/file.py 或 /workspace
                        'type': 'string',
                    },
                    # create命令的必需参数，包含要创建文件的内容
                    'file_text': {
                        'description': 'Required parameter of `create` command, with the content of the file to be created.',
                        # 翻译：create命令的必需参数，包含要创建文件的内容
                        'type': 'string',
                    },
                    # str_replace命令的必需参数，包含要替换的字符串
                    'old_str': {
                        'description': 'Required parameter of `str_replace` command containing the string in `path` to replace.',
                        # 翻译：str_replace命令的必需参数，包含path中要替换的字符串
                        'type': 'string',
                    },
                    # str_replace和insert命令的参数，包含新字符串或要插入的字符串
                    'new_str': {
                        'description': 'Optional parameter of `str_replace` command containing the new string (if not given, no string will be added). Required parameter of `insert` command containing the string to insert.',
                        # 翻译：str_replace命令的可选参数，包含新字符串(如果未给出，不添加字符串)。insert命令的必需参数，包含要插入的字符串
                        'type': 'string',
                    },
                    # insert命令的必需参数，指定插入位置的行号
                    'insert_line': {
                        'description': 'Required parameter of `insert` command. The `new_str` will be inserted AFTER the line `insert_line` of `path`.',
                        # 翻译：insert命令的必需参数。new_str将在path的insert_line行之后插入
                        'type': 'integer',
                    },
                    # view命令的可选参数，指定查看的行号范围
                    'view_range': {
                        'description': 'Optional parameter of `view` command when `path` points to a file. If none is given, the full file is shown. If provided, the file will be shown in the indicated line number range, e.g. [11, 12] will show lines 11 and 12. Indexing at 1 to start. Setting `[start_line, -1]` shows all lines from `start_line` to the end of the file.',
                        # 翻译：当path指向文件时view命令的可选参数。如果未给出，显示整个文件。如果提供，文件将在指定的行号范围内显示，例如[11, 12]将显示第11和12行。从1开始索引。设置[start_line, -1]显示从start_line到文件末尾的所有行
                        'items': {'type': 'integer'},  # 数组项类型为整数
                        'type': 'array',  # 参数类型为数组
                    },
                },
                'required': ['command', 'path'],  # 必需参数：命令和路径
            },
        ),
    )