from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# 基于LLM的文件编辑工具详细描述
# 包含完整的文件编辑指导、限制条件和多个示例
_FILE_EDIT_DESCRIPTION = """Edit a file in plain-text format.
* The assistant can edit files by specifying the file path and providing a draft of the new file content.
* The draft content doesn't need to be exactly the same as the existing file; the assistant may skip unchanged lines using comments like `# ... existing code ...` to indicate unchanged sections.
* IMPORTANT: For large files (e.g., > 300 lines), specify the range of lines to edit using `start` and `end` (1-indexed, inclusive). The range should be smaller than 300 lines.
* -1 indicates the last line of the file when used as the `start` or `end` value.
* Keep at least one unchanged line before the changed section and after the changed section wherever possible.
* Make sure to set the `start` and `end` to include all the lines in the original file referred to in the draft of the new file content. Failure to do so will result in bad edits.
* To append to a file, set both `start` and `end` to `-1`.
* If the file doesn't exist, a new file will be created with the provided content.
* IMPORTANT: Make sure you include all the required indentations for each line of code in the draft, otherwise the edited code will be incorrectly indented.
* IMPORTANT: Make sure that the first line of the draft is also properly indented and has the required whitespaces.
* IMPORTANT: NEVER include or make references to lines from outside the `start` and `end` range in the draft.
* IMPORTANT: Start the content with a comment in the format: #EDIT: Reason for edit
* IMPORTANT: If you are not appending to the file, avoid setting `start` and `end` to the same value.

**Example 1: general edit for short files**
For example, given an existing file `/path/to/file.py` that looks like this:
(this is the beginning of the file)
1|class MyClass:
2|    def __init__(self):
3|        self.x = 1
4|        self.y = 2
5|        self.z = 3
6|
7|print(MyClass().z)
8|print(MyClass().x)
(this is the end of the file)

The assistant wants to edit the file to look like this:
(this is the beginning of the file)
1|class MyClass:
2|    def __init__(self):
3|        self.x = 1
4|        self.y = 2
5|
6|print(MyClass().y)
(this is the end of the file)

The assistant may produce an edit action like this:
path="/path/to/file.txt" start=1 end=-1
content=```
#EDIT: I want to change the value of y to 2
class MyClass:
    def __init__(self):
        # ... existing code ...
        self.y = 2

print(MyClass().y)
```

**Example 2: append to file for short files**
For example, given an existing file `/path/to/file.py` that looks like this:
(this is the beginning of the file)
1|class MyClass:
2|    def __init__(self):
3|        self.x = 1
4|        self.y = 2
5|        self.z = 3
6|
7|print(MyClass().z)
8|print(MyClass().x)
(this is the end of the file)

To append the following lines to the file:
```python
#EDIT: I want to print the value of y
print(MyClass().y)
```

The assistant may produce an edit action like this:
path="/path/to/file.txt" start=-1 end=-1
content=```
print(MyClass().y)
```

**Example 3: edit for long files**

Given an existing file `/path/to/file.py` that looks like this:
(1000 more lines above)
1001|class MyClass:
1002|    def __init__(self):
1003|        self.x = 1
1004|        self.y = 2
1005|        self.z = 3
1006|
1007|print(MyClass().z)
1008|print(MyClass().x)
(2000 more lines below)

The assistant wants to edit the file to look like this:

(1000 more lines above)
1001|class MyClass:
1002|    def __init__(self):
1003|        self.x = 1
1004|        self.y = 2
1005|
1006|print(MyClass().y)
(2000 more lines below)

The assistant may produce an edit action like this:
path="/path/to/file.txt" start=1002 end=1008
content=```
#EDIT: I want to change the value of y to 2
    def __init__(self):
        # no changes before
        self.y = 2
        # self.z is removed

# MyClass().z is removed
print(MyClass().y)
```
"""
# 翻译：以纯文本格式编辑文件。
# assistant可以通过指定文件路径和提供新文件内容草稿来编辑文件。
# 草稿内容不需要与现有文件完全相同，可以使用注释跳过未更改的行。
# 重要：对于大文件(>300行)，使用start和end指定编辑的行范围(1索引，包含)，范围应小于300行。
# -1表示文件的最后一行。尽可能在更改部分前后保持至少一行不变。
# 确保start和end包含草稿中引用的原文件的所有行。要追加到文件，将start和end都设为-1。
# 如果文件不存在，将创建新文件。重要：确保草稿中每行代码都有正确的缩进。
# 重要：草稿的第一行也要正确缩进。不要在草稿中包含或引用start和end范围外的行。
# 重要：内容以格式为#EDIT: 编辑原因的注释开始。不要将start和end设为相同值(除非追加)。

# 基于LLM的文件编辑工具配置
# 定义了文件编辑工具的参数，包括路径、内容、起始和结束行号
LLMBasedFileEditTool = ChatCompletionToolParam(
    type='function',  # 工具类型为函数
    function=ChatCompletionToolParamFunctionChunk(
        name='edit_file',  # 工具名称：编辑文件
        description=_FILE_EDIT_DESCRIPTION,  # 工具的详细描述
        parameters={
            'type': 'object',  # 参数类型为对象
            'properties': {
                # 要编辑的文件的绝对路径
                'path': {
                    'type': 'string',
                    'description': 'The absolute path to the file to be edited.',
                    # 翻译：要编辑的文件的绝对路径
                },
                # 文件的新内容草稿
                'content': {
                    'type': 'string',
                    'description': 'A draft of the new content for the file being edited. Note that the assistant may skip unchanged lines.',
                    # 翻译：被编辑文件的新内容草稿。注意assistant可以跳过未更改的行
                },
                # 编辑的起始行号
                'start': {
                    'type': 'integer',
                    'description': 'The starting line number for the edit (1-indexed, inclusive). Default is 1.',
                    # 翻译：编辑的起始行号(1索引，包含)。默认为1
                },
                # 编辑的结束行号
                'end': {
                    'type': 'integer',
                    'description': 'The ending line number for the edit (1-indexed, inclusive). Default is -1 (end of file).',
                    # 翻译：编辑的结束行号(1索引，包含)。默认为-1(文件末尾)
                },
            },
            'required': ['path', 'content'],  # 必需参数：路径和内容
        },
    ),
)