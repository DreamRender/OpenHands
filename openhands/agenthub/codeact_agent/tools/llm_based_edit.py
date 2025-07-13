# 导入 LLM 工具相关类型
from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# 基于 LLM 的文件编辑工具的详细描述
_FILE_EDIT_DESCRIPTION = """以纯文本格式编辑文件。
* 助手可以通过指定文件路径并提供新文件内容的草稿来编辑文件。
* 草稿内容不需要与现有文件完全相同；助手可以使用如 `# ... existing code ...` 这样的注释来跳过未更改的行，以表示未更改的部分。
* 重要：对于大文件（例如 > 300 行），使用 `start` 和 `end`（从 1 开始索引，包含边界）指定要编辑的行范围。范围应小于 300 行。
* -1 表示文件的最后一行，当用作 `start` 或 `end` 值时。
* 尽可能在更改部分之前和之后至少保留一行未更改的内容。
* 确保设置 `start` 和 `end` 以包含新文件内容草稿中引用的原始文件中的所有行。否则将导致错误的编辑。
* 要向文件追加内容，将 `start` 和 `end` 都设置为 `-1`。
* 如果文件不存在，将使用提供的内容创建新文件。
* 重要：确保在草稿中包含每行代码所需的所有缩进，否则编辑后的代码将缩进不正确。
* 重要：确保草稿的第一行也正确缩进并具有所需的空白字符。
* 重要：永远不要在草稿中包含或引用 `start` 和 `end` 范围之外的行。
* 重要：以格式为 #EDIT: 编辑原因 的注释开始内容
* 重要：如果您不是在向文件追加内容，避免将 `start` 和 `end` 设置为相同的值。

**示例 1：短文件的常规编辑**
例如，给定一个现有文件 `/path/to/file.py`，内容如下：
(这是文件的开始)
1|class MyClass:
2|    def __init__(self):
3|        self.x = 1
4|        self.y = 2
5|        self.z = 3
6|
7|print(MyClass().z)
8|print(MyClass().x)
(这是文件的结束)

助手想要编辑文件使其看起来像这样：
(这是文件的开始)
1|class MyClass:
2|    def __init__(self):
3|        self.x = 1
4|        self.y = 2
5|
6|print(MyClass().y)
(这是文件的结束)

助手可以产生如下编辑动作：
path="/path/to/file.txt" start=1 end=-1
content=```
#EDIT: I want to change the value of y to 2
class MyClass:
    def __init__(self):
        # ... existing code ...
        self.y = 2

print(MyClass().y)
```

**示例 2：短文件的追加内容**
例如，给定一个现有文件 `/path/to/file.py`，内容如下：
(这是文件的开始)
1|class MyClass:
2|    def __init__(self):
3|        self.x = 1
4|        self.y = 2
5|        self.z = 3
6|
7|print(MyClass().z)
8|print(MyClass().x)
(这是文件的结束)

要向文件追加以下行：
```python
#EDIT: I want to print the value of y
print(MyClass().y)
```

助手可以产生如下编辑动作：
path="/path/to/file.txt" start=-1 end=-1
content=```
print(MyClass().y)
```

**示例 3：长文件的编辑**

给定一个现有文件 `/path/to/file.py`，内容如下：
(上面还有 1000 行)
1001|class MyClass:
1002|    def __init__(self):
1003|        self.x = 1
1004|        self.y = 2
1005|        self.z = 3
1006|
1007|print(MyClass().z)
1008|print(MyClass().x)
(下面还有 2000 行)

助手想要编辑文件使其看起来像这样：

(上面还有 1000 行)
1001|class MyClass:
1002|    def __init__(self):
1003|        self.x = 1
1004|        self.y = 2
1005|
1006|print(MyClass().y)
(下面还有 2000 行)

助手可以产生如下编辑动作：
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

# 创建基于 LLM 的文件编辑工具配置
LLMBasedFileEditTool = ChatCompletionToolParam(
    type='function',
    function=ChatCompletionToolParamFunctionChunk(
        name='edit_file',  # 工具名称
        description=_FILE_EDIT_DESCRIPTION,  # 工具描述
        parameters={
            'type': 'object',
            'properties': {
                'path': {
                    'type': 'string',
                    'description': '要编辑的文件的绝对路径。',
                },
                'content': {
                    'type': 'string',
                    'description': '要编辑的文件的新内容草稿。注意助手可以跳过未更改的行。',
                },
                'start': {
                    'type': 'integer',
                    'description': '编辑的起始行号（从 1 开始索引，包含边界）。默认为 1。',
                },
                'end': {
                    'type': 'integer',
                    'description': '编辑的结束行号（从 1 开始索引，包含边界）。默认为 -1（文件末尾）。',
                },
            },
            'required': ['path', 'content'],  # 必需参数
        },
    ),
)