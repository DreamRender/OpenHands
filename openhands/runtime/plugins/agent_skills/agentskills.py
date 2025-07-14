"""OpenHands Agent技能集成模块

该模块作为OpenHands Agent所有技能的统一集成入口，负责从各个子模块中
导入技能函数，并生成统一的函数文档。主要功能包括文件操作、文件读取、
Repository操作等Agent核心技能的集成和管理。

模块特点:
    - 动态导入各子模块的技能函数
    - 自动生成统一的API文档
    - 支持可选模块的条件导入
    - 提供统一的对外接口

导出的技能类别:
    - 文件操作技能 (file_ops)
    - 文件读取技能 (file_reader)
    - Repository操作技能 (repo_ops, 可选)
    - 文件编辑技能 (file_editor)
"""

from inspect import signature

# 导入核心技能模块
from openhands.runtime.plugins.agent_skills import file_ops, file_reader
# 导入动态函数导入工具
from openhands.runtime.plugins.agent_skills.utils.dependency import import_functions

# 从文件操作模块中导入所有公开函数到当前全局命名空间
# 这使得file_ops模块中的所有函数可以直接在本模块中使用
import_functions(
    module=file_ops, function_names=file_ops.__all__, target_globals=globals()
)

# 从文件读取模块中导入所有公开函数到当前全局命名空间
# 这使得file_reader模块中的所有函数可以直接在本模块中使用
import_functions(
    module=file_reader, function_names=file_reader.__all__, target_globals=globals()
)

# 构建模块的公开接口列表，合并文件操作和文件读取模块的所有函数
__all__ = file_ops.__all__ + file_reader.__all__

# 尝试导入Repository操作模块（可选模块）
try:
    from openhands.runtime.plugins.agent_skills import repo_ops

    # 如果成功导入repo_ops，则将其函数也添加到全局命名空间
    import_functions(
        module=repo_ops, function_names=repo_ops.__all__, target_globals=globals()
    )

    # 将repo_ops的函数添加到公开接口列表
    __all__ += repo_ops.__all__
except ImportError:
    # 如果repo_ops模块不可用，我们跳过导入
    # 这种设计允许在某些环境下可选地禁用Repository操作功能
    pass

# 全局变量：存储所有技能函数的格式化文档字符串
# 该变量包含了所有导入函数的签名和文档，供外部查询使用
DOCUMENTATION = ''

# 遍历所有公开的函数，生成统一格式的API文档
for func_name in __all__:
    # 从全局命名空间获取函数对象
    func = globals()[func_name]

    # 获取函数的原始文档字符串
    cur_doc = func.__doc__

    # 清理文档字符串：移除缩进并过滤空行
    # 这一步确保文档格式的一致性，移除原有的不规则缩进
    cur_doc = '\n'.join(filter(None, map(lambda x: x.strip(), cur_doc.split('\n'))))

    # 为文档字符串添加一致的4空格缩进
    # 这确保所有函数文档在最终输出中具有统一的格式
    cur_doc = '\n'.join(map(lambda x: ' ' * 4 + x, cur_doc.split('\n')))

    # 生成函数签名字符串，包含函数名和完整的参数列表
    fn_signature = f'{func.__name__}' + str(signature(func))

    # 将函数签名和文档合并到总文档字符串中
    # 格式：函数签名 + 冒号 + 换行 + 缩进的文档 + 双换行分隔
    DOCUMENTATION += f'{fn_signature}:\n{cur_doc}\n\n'

# 添加文件编辑器功能（单独的函数导入）
# 注意：这里使用了 noqa: E402 来忽略导入位置的代码风格检查
# 因为该导入需要在动态函数导入之后进行
from openhands.runtime.plugins.agent_skills.file_editor import file_editor  # noqa: E402

# 将文件编辑器函数添加到公开接口列表
# 这是一个特殊的技能函数，需要单独导入和注册
__all__ += ['file_editor']
