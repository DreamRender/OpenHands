"""
工具名称常量定义模块

该模块定义了在函数调用中使用的工具名称常量。
这些常量确保了整个系统中工具名称的一致性，避免了硬编码字符串的使用。
"""

# Bash命令执行工具名称
# 用于执行shell命令和脚本的工具
EXECUTE_BASH_TOOL_NAME = 'execute_bash'

# 字符串替换编辑器工具名称  
# 用于文件内容编辑和字符串替换操作的工具
STR_REPLACE_EDITOR_TOOL_NAME = 'str_replace_editor'

# 浏览器工具名称
# 用于网页浏览和web交互操作的工具
BROWSER_TOOL_NAME = 'browser'

# 完成工具名称
# 用于标记任务完成的工具
FINISH_TOOL_NAME = 'finish'

# 基于LLM的文件编辑工具名称
# 使用大语言Model进行智能文件编辑的工具
LLM_BASED_EDIT_TOOL_NAME = 'edit_file'