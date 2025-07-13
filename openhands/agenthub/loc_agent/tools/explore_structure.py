from litellm import (
    ChatCompletionToolParam,
    ChatCompletionToolParamFunctionChunk,
)

# 简化版结构探索器工具描述
# 这是一个统一的工具，用于遍历预构建的代码图谱，检索指定实体周围的依赖结构
_SIMPLIFIED_STRUCTURE_EXPLORER_DESCRIPTION = """
A unified tool that traverses a pre-built code graph to retrieve dependency structure around specified entities,
with options to explore upstream or downstream, and control traversal depth and filters for entity and dependency types.
"""
"""统一的工具，遍历预构建的代码图谱以检索指定实体周围的依赖结构，
支持探索上游或下游依赖，并可控制遍历深度和实体及依赖类型的过滤器。"""


# 简化版树结构探索示例
# 提供了三种常见的使用场景：下游依赖探索、Repository结构探索、类图生成
_SIMPLIFIED_TREE_EXAMPLE = """
Example Usage:
1. Exploring Downstream Dependencies:
    ```
    explore_tree_structure(
        start_entities=['src/module_a.py:ClassA'],
        direction='downstream',
        traversal_depth=2,
        dependency_type_filter=['invokes', 'imports']
    )
    ```
2. Exploring the repository structure from the root directory (/) up to two levels deep:
    ```
    explore_tree_structure(
      start_entities=['/'],
      traversal_depth=2,
      dependency_type_filter=['contains']
    )
    ```
3. Generate Class Diagrams:
    ```
    explore_tree_structure(
        start_entities=selected_entity_ids,
        direction='both',
        traverse_depth=-1,
        dependency_type_filter=['inherits']
    )
    ```
"""
"""使用示例：
1. 探索下游依赖：从指定类开始，向下探索2层深度的调用和导入关系
2. 探索Repository结构：从根目录开始，向下2层深度探索包含关系
3. 生成类图：双向无限深度探索继承关系"""


# 详细版结构探索器工具描述
# 包含了完整的代码图谱定义、实体类型、依赖类型、层次结构和交互关系的说明
_DETAILED_STRUCTURE_EXPLORER_DESCRIPTION = """
Unified repository exploring tool that traverses a pre-built code graph to retrieve dependency structure around specified entities.
The search can be controlled to traverse upstream (exploring dependencies that entities rely on) or downstream (exploring how entities impact others), with optional limits on traversal depth and filters for entity and dependency types.

Code Graph Definition:
* Entity Types: 'directory', 'file', 'class', 'function'.
* Dependency Types: 'contains', 'imports', 'invokes', 'inherits'.
* Hierarchy:
    - Directories contain files and subdirectories.
    - Files contain classes and functions.
    - Classes contain inner classes and methods.
    - Functions can contain inner functions.
* Interactions:
    - Files/classes/functions can import classes and functions.
    - Classes can inherit from other classes.
    - Classes and functions can invoke others (invocations in a class's `__init__` are attributed to the class).
Entity ID:
* Unique identifier including file path and module path.
* Here's an example of an Entity ID: `"interface/C.py:C.method_a.inner_func"` identifies function `inner_func` within `method_a` of class `C` in `"interface/C.py"`.

Notes:
* Traversal Control: The `traversal_depth` parameter specifies how deep the function should explore the graph starting from the input entities.
* Filtering: Use `entity_type_filter` and `dependency_type_filter` to narrow down the scope of the search, focusing on specific entity types and relationships.

"""
"""统一的Repository探索工具，遍历预构建的代码图谱以检索指定实体周围的依赖结构。
搜索可以控制为遍历上游（探索实体依赖的依赖项）或下游（探索实体如何影响其他实体），
并可选择限制遍历深度以及实体和依赖类型的过滤器。

代码图谱定义：
* 实体类型：'directory'（目录）、'file'（文件）、'class'（类）、'function'（函数）
* 依赖类型：'contains'（包含）、'imports'（导入）、'invokes'（调用）、'inherits'（继承）
* 层次结构：
    - 目录包含文件和子目录
    - 文件包含类和函数
    - 类包含内部类和方法
    - 函数可以包含内部函数
* 交互关系：
    - 文件/类/函数可以导入类和函数
    - 类可以继承其他类
    - 类和函数可以调用其他实体（类的__init__中的调用归属于该类）

实体ID：
* 包含文件路径和模块路径的唯一标识符
* 示例："interface/C.py:C.method_a.inner_func"标识文件"interface/C.py"中类C的method_a方法内的inner_func函数

注意事项：
* 遍历控制：traversal_depth参数指定函数从输入实体开始探索图谱的深度
* 过滤：使用entity_type_filter和dependency_type_filter缩小搜索范围，专注于特定的实体类型和关系"""


# 详细版树结构探索示例
# 提供了四种详细的使用场景，包含更详细的参数说明和用途解释
_DETAILED_TREE_EXAMPLE = """
Example Usage:
1. Exploring Outward Dependencies:
    ```
    explore_tree_structure(
        start_entities=['src/module_a.py:ClassA'],
        direction='downstream',
        traversal_depth=2,
        dependency_type_filter=['invokes', 'imports']
    )
    ```
    This retrieves the dependencies of `ClassA` up to 2 levels deep, focusing only on classes and functions with 'invokes' and 'imports' relationships.

2. Exploring Inward Dependencies:
    ```
    explore_tree_structure(
        start_entities=['src/module_b.py:FunctionY'],
        direction='upstream',
        traversal_depth=-1
    )
    ```
    This finds all entities that depend on `FunctionY` without restricting the traversal depth.
3. Exploring Repository Structure:
    ```
    explore_tree_structure(
      start_entities=['/'],
      traversal_depth=2,
      dependency_type_filter=['contains']
    )
    ```
    This retrieves the tree repository structure from the root directory (/), traversing up to two levels deep and focusing only on 'contains' relationship.
4. Generate Class Diagrams:
    ```
    explore_tree_structure(
        start_entities=selected_entity_ids,
        direction='both',
        traverse_depth=-1,
        dependency_type_filter=['inherits']
    )
    ```
"""
"""详细使用示例：
1. 探索对外依赖：
   检索ClassA最多2层深度的依赖，仅关注具有'invokes'和'imports'关系的类和函数

2. 探索对内依赖：
   查找所有依赖于FunctionY的实体，不限制遍历深度

3. 探索Repository结构：
   从根目录检索树形Repository结构，向下遍历最多2层深度，仅关注'contains'关系

4. 生成类图：
   双向无限深度探索选定实体的继承关系"""


# 结构探索器参数配置
# 定义了explore_tree_structure函数的完整参数规范，包括类型、描述和默认值
_STRUCTURE_EXPLORER_PARAMETERS = {
    'type': 'object',
    'properties': {
        # 起始实体列表：搜索的起点
        'start_entities': {
            'description': (
                'List of entities (e.g., class, function, file, or directory paths) to begin the search from.\n'
                'Entities representing classes or functions must be formatted as "file_path:QualifiedName" (e.g., `interface/C.py:C.method_a.inner_func`).\n'
                'For files or directories, provide only the file or directory path (e.g., `src/module_a.py` or `src/`).'
            ),
            'type': 'array',
            'items': {'type': 'string'},
        },
        # 遍历方向：上游、下游或双向
        'direction': {
            'description': (
                'Direction of traversal in the code graph; allowed options are: `upstream`, `downstream`, `both`.\n'
                "- 'upstream': Traversal to explore dependencies that the specified entities rely on (how they depend on others).\n"
                "- 'downstream': Traversal to explore the effects or interactions of the specified entities on others (how others depend on them).\n"
                "- 'both': Traversal on both direction."
            ),
            'type': 'string',
            'enum': ['upstream', 'downstream', 'both'],
            'default': 'downstream',
        },
        # 遍历深度：控制搜索的深度范围
        'traversal_depth': {
            'description': (
                'Maximum depth of traversal. A value of -1 indicates unlimited depth (subject to a maximum limit).'
                'Must be either `-1` or a non-negative integer (≥ 0).'
            ),
            'type': 'integer',
            'default': 2,
        },
        # 实体类型过滤器：限制搜索的实体类型
        'entity_type_filter': {
            'description': (
                "List of entity types (e.g., 'class', 'function', 'file', 'directory') to include in the traversal. If None, all entity types are included."
            ),
            'type': ['array', 'null'],
            'items': {'type': 'string'},
            'default': None,
        },
        # 依赖类型过滤器：限制搜索的依赖关系类型
        'dependency_type_filter': {
            'description': (
                "List of dependency types (e.g., 'contains', 'imports', 'invokes', 'inherits') to include in the traversal. If None, all dependency types are included."
            ),
            'type': ['array', 'null'],
            'items': {'type': 'string'},
            'default': None,
        },
    },
    'required': ['start_entities'],  # 必需参数：起始实体列表
}


def create_explore_tree_structure_tool(
    use_simplified_description: bool = False,
) -> ChatCompletionToolParam:
    """创建探索树结构的工具参数配置。
    
    此函数根据指定的描述类型（简化版或详细版）创建用于代码结构探索的工具配置。
    工具支持在预构建的代码图谱中进行依赖关系的探索和分析。
    
    Args:
        use_simplified_description (bool, optional): 是否使用简化版描述。
            - True: 使用简化版描述和示例，适合快速上手
            - False: 使用详细版描述和示例，包含完整的技术细节
            默认值为 False。
    
    Returns:
        ChatCompletionToolParam: 配置完成的聊天工具参数对象，包含：
            - type: 工具类型（'function'）
            - function: 包含工具名称、描述、示例和参数规范的函数配置
    
    Note:
        该工具主要用于：
        1. 探索代码实体间的依赖关系
        2. 分析代码结构和层次关系
        3. 生成类图和依赖图
        4. Repository结构分析
    """
    # 根据参数选择描述类型（简化版或详细版）
    description = (
        _SIMPLIFIED_STRUCTURE_EXPLORER_DESCRIPTION
        if use_simplified_description
        else _DETAILED_STRUCTURE_EXPLORER_DESCRIPTION
    )
    # 根据参数选择示例类型（简化版或详细版）
    example = (
        _SIMPLIFIED_TREE_EXAMPLE
        if use_simplified_description
        else _DETAILED_TREE_EXAMPLE
    )
    # 创建并返回工具参数配置
    return ChatCompletionToolParam(
        type='function',
        function=ChatCompletionToolParamFunctionChunk(
            name='explore_tree_structure',  # 工具函数名称
            description=description + example,  # 工具描述和使用示例
            parameters=_STRUCTURE_EXPLORER_PARAMETERS,  # 工具参数规范
        ),
    )
