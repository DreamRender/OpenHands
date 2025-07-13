from litellm import (
    ChatCompletionToolParam,
    ChatCompletionToolParamFunctionChunk,
)

# 搜索实体描述
# 定义了根据实体名称搜索代码库中完整实现的工具功能说明
_SEARCH_ENTITY_DESCRIPTION = """
Searches the codebase to retrieve the complete implementations of specified entities based on the provided entity names.
The tool can handle specific entity queries such as function names, class names, or file paths.

**Usage Example:**
# Search for a specific function implementation
get_entity_contents(['src/my_file.py:MyClass.func_name'])

# Search for a file's complete content
get_entity_contents(['src/my_file.py'])

**Entity Name Format:**
- To specify a function or class, use the format: `file_path:QualifiedName`
  (e.g., 'src/helpers/math_helpers.py:MathUtils.calculate_sum').
- To search for a file's content, use only the file path (e.g., 'src/my_file.py').
"""
"""搜索代码库以根据提供的实体名称检索指定实体的完整实现。
该工具可以处理特定的实体查询，如函数名、类名或文件路径。

**使用示例：**
# 搜索特定函数实现
get_entity_contents(['src/my_file.py:MyClass.func_name'])

# 搜索文件的完整内容
get_entity_contents(['src/my_file.py'])

**实体名称格式：**
- 指定函数或类时，使用格式：`file_path:QualifiedName`
  (例如：'src/helpers/math_helpers.py:MathUtils.calculate_sum')
- 搜索文件内容时，只使用文件路径 (例如：'src/my_file.py')"""

# 搜索实体工具配置
# 创建用于搜索和获取代码实体内容的聊天工具参数配置
SearchEntityTool = ChatCompletionToolParam(
    type='function',  # 工具类型：函数
    function=ChatCompletionToolParamFunctionChunk(
        name='get_entity_contents',  # 工具函数名称
        description=_SEARCH_ENTITY_DESCRIPTION,  # 工具描述
        parameters={
            'type': 'object',
            'properties': {
                # 实体名称列表参数：要查询的实体名称数组
                'entity_names': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': (
                        'A list of entity names to query. Each entity name can represent a function, class, or file. '
                        "For functions or classes, the format should be 'file_path:QualifiedName' "
                        "(e.g., 'src/helpers/math_helpers.py:MathUtils.calculate_sum'). "
                        "For files, use just the file path (e.g., 'src/my_file.py')."
                    ),
                    # 要查询的实体名称列表。每个实体名称可以表示函数、类或文件。
                    # 对于函数或类，格式应为'file_path:QualifiedName'
                    # 对于文件，只使用文件路径
                }
            },
            'required': ['entity_names'],  # 必需参数：实体名称列表
        },
    ),
)


# 搜索Repository描述
# 定义了基于查询条件（关键词或行号）搜索代码库相关代码片段的工具功能说明
_SEARCH_REPO_DESCRIPTION = """Searches the codebase to retrieve relevant code snippets based on given queries(terms or line numbers).
** Note:
- Either `search_terms` or `line_nums` must be provided to perform a search.
- If `search_terms` are provided, it searches for code snippets based on each term:
- If `line_nums` is provided, it searches for code snippets around the specified lines within the file defined by `file_path_or_pattern`.

** Example Usage:
# Search for code content contain keyword `order`, `bill`
search_code_snippets(search_terms=["order", "bill"])

# Search for a class
search_code_snippets(search_terms=["MyClass"])

# Search for context around specific lines (10 and 15) within a file
search_code_snippets(line_nums=[10, 15], file_path_or_pattern='src/example.py')
"""
"""搜索代码库以根据给定的查询条件（关键词或行号）检索相关的代码片段。

** 注意事项：
- 必须提供 `search_terms` 或 `line_nums` 中的一个来执行搜索
- 如果提供了 `search_terms`，将基于每个关键词搜索代码片段
- 如果提供了 `line_nums`，将在 `file_path_or_pattern` 定义的文件中搜索指定行周围的代码片段

** 使用示例：
# 搜索包含关键词 `order`、`bill` 的代码内容
search_code_snippets(search_terms=["order", "bill"])

# 搜索类
search_code_snippets(search_terms=["MyClass"])

# 搜索文件中特定行（第10行和第15行）周围的上下文
search_code_snippets(line_nums=[10, 15], file_path_or_pattern='src/example.py')"""

# 搜索Repository工具配置
# 创建用于搜索代码片段的聊天工具参数配置
SearchRepoTool = ChatCompletionToolParam(
    type='function',  # 工具类型：函数
    function=ChatCompletionToolParamFunctionChunk(
        name='search_code_snippets',  # 工具函数名称
        description=_SEARCH_REPO_DESCRIPTION,  # 工具描述
        parameters={
            'type': 'object',
            'properties': {
                # 搜索关键词参数：要在代码库中搜索的名称、关键词或代码片段列表
                'search_terms': {
                    'type': 'array',
                    'items': {'type': 'string'},
                    'description': 'A list of names, keywords, or code snippets to search for within the codebase. '
                    'This can include potential function names, class names, or general code fragments. '
                    'Either `search_terms` or `line_nums` must be provided to perform a search.',
                    # 要在代码库中搜索的名称、关键词或代码片段列表
                    # 可以包括潜在的函数名、类名或一般的代码片段
                    # 必须提供 `search_terms` 或 `line_nums` 中的一个来执行搜索
                },
                # 行号参数：用于在指定文件中定位代码片段的特定行号
                'line_nums': {
                    'type': 'array',
                    'items': {'type': 'integer'},
                    'description': 'Specific line numbers to locate code snippets within a specified file. '
                    'Must be used alongside a valid `file_path_or_pattern`. '
                    'Either `line_nums` or `search_terms` must be provided to perform a search.',
                    # 用于在指定文件中定位代码片段的特定行号
                    # 必须与有效的 `file_path_or_pattern` 一起使用
                    # 必须提供 `line_nums` 或 `search_terms` 中的一个来执行搜索
                },
                # 文件路径或模式参数：用于过滤搜索结果到特定文件或目录的glob模式或具体文件路径
                'file_path_or_pattern': {
                    'type': 'string',
                    'description': 'A glob pattern or specific file path used to filter search results '
                    'to particular files or directories. Defaults to "**/*.py", meaning all Python files are searched by default. '
                    'If `line_nums` are provided, this must specify a specific file path.',
                    'default': '**/*.py',
                    # 用于将搜索结果过滤到特定文件或目录的glob模式或具体文件路径
                    # 默认为"**/*.py"，表示默认搜索所有Python文件
                    # 如果提供了 `line_nums`，此参数必须指定具体的文件路径
                },
            },
            'required': [],  # 无必需参数，但search_terms和line_nums必须提供其中一个
        },
    ),
)
