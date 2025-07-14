"""Repository操作工具模块

该模块作为Repository操作相关工具的统一导入入口，提供了代码索引和搜索功能。
主要功能包括探索代码树结构、获取实体内容和搜索代码片段等操作。

模块导出的工具函数:
    - explore_tree_structure: 探索代码树结构
    - get_entity_contents: 获取实体内容
    - search_code_snippets: 搜索代码片段
"""

# 从locagent工具模块中导入Repository操作相关的核心功能
from openhands_aci.indexing.locagent.tools import (
    explore_tree_structure,  # 探索并分析代码Repository的树形结构
    get_entity_contents,     # 获取代码实体（如类、函数等）的具体内容
    search_code_snippets,    # 在Repository中搜索匹配的代码片段
)

# 定义模块对外暴露的公共接口
# 这些是可以从该模块直接导入使用的函数
__all__ = [
    'get_entity_contents',     # 获取实体内容功能
    'search_code_snippets',    # 代码片段搜索功能
    'explore_tree_structure',  # 代码树结构探索功能
]
