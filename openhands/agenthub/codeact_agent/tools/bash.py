import sys

# 导入 LLM 工具相关类型
from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# 导入工具名称常量
from openhands.llm.tool_names import EXECUTE_BASH_TOOL_NAME

# 详细的 Bash 工具描述 - 用于大部分模型
_DETAILED_BASH_DESCRIPTION = """在持久 shell 会话中的终端内执行 bash 命令。


### 命令执行
* 一次一个命令：一次只能执行一个 bash 命令。如果需要按顺序运行多个命令，请使用 `&&` 或 `;` 将它们链接在一起。
* 持久会话：命令在持久 shell 会话中执行，其中环境变量、虚拟环境和工作目录在命令之间保持不变。
* 软超时：命令有 10 秒的软超时，一旦达到，您可以选择继续或中断命令（详细信息请参见下面的部分）

### 长时间运行的命令
* 对于可能无限期运行的命令，请在后台运行并将输出重定向到文件，例如 `python3 app.py > server.log 2>&1 &`。
* 对于可能运行很长时间的命令（例如安装或测试命令），或运行固定时间的命令（例如 sleep），您应该将函数调用的 "timeout" 参数设置为适当的值。
* 如果 bash 命令返回退出代码 `-1`，这意味着进程达到了软超时且尚未完成。通过将 `is_input` 设置为 `true`，您可以：
  - 发送空 `command` 以检索其他日志
  - 发送文本（将 `command` 设置为文本）到正在运行的进程的 STDIN
  - 发送控制命令如 `C-c`（Ctrl+C）、`C-d`（Ctrl+D）或 `C-z`（Ctrl+Z）来中断进程
  - 如果您执行 C-c，您可以使用更长的 "timeout" 参数重新启动进程以让其运行完成

### 最佳实践
* 目录验证：在创建新目录或文件之前，首先验证父目录是否存在且位置正确。
* 目录管理：尝试通过使用绝对路径并避免过度使用 `cd` 来维护工作目录。

### 输出处理
* 输出截断：如果输出超过最大长度，将在返回前被截断。
"""

# 简短的 Bash 工具描述 - 用于有 token 限制的模型
_SHORT_BASH_DESCRIPTION = """在终端中执行 bash 命令。
* 长时间运行的命令：对于可能无限期运行的命令，应该在后台运行并将输出重定向到文件，例如 command = `python3 app.py > server.log 2>&1 &`。对于需要运行特定持续时间的命令，您可以设置 "timeout" 参数来指定以秒为单位的硬超时。
* 与运行进程交互：如果 bash 命令返回退出代码 `-1`，这意味着进程尚未完成。通过将 `is_input` 设置为 `true`，助手可以与正在运行的进程交互并发送空 `command` 以检索任何其他日志，或发送其他文本（将 `command` 设置为文本）到正在运行的进程的 STDIN，或发送如 `C-c`（Ctrl+C）、`C-d`（Ctrl+D）、`C-z`（Ctrl+Z）等命令来中断进程。
* 一次一个命令：一次只能执行一个 bash 命令。如果需要按顺序运行多个命令，可以使用 `&&` 或 `;` 将它们链接在一起。"""


def refine_prompt(prompt: str):
    """根据平台优化提示文本。
    
    在 Windows 平台上，将 'bash' 替换为 'powershell'，
    以适配不同操作系统的命令行环境。
    
    Args:
        prompt (str): 原始提示文本
        
    Returns:
        str: 优化后的提示文本
    """
    if sys.platform == 'win32':
        # Windows 系统使用 PowerShell
        return prompt.replace('bash', 'powershell')
    return prompt


def create_cmd_run_tool(
    use_short_description: bool = False,
) -> ChatCompletionToolParam:
    """创建命令运行工具的配置。
    
    根据是否需要简短描述来创建 bash/powershell 命令执行工具。
    对于 token 限制严格的模型使用简短描述。
    
    Args:
        use_short_description (bool): 是否使用简短描述，默认为 False
        
    Returns:
        ChatCompletionToolParam: 配置好的命令运行工具参数
    """
    # 根据参数选择描述类型
    description = (
        _SHORT_BASH_DESCRIPTION if use_short_description else _DETAILED_BASH_DESCRIPTION
    )
    
    return ChatCompletionToolParam(
        type='function',
        function=ChatCompletionToolParamFunctionChunk(
            name=EXECUTE_BASH_TOOL_NAME,  # 工具名称常量
            description=refine_prompt(description),  # 根据平台优化的描述
            parameters={
                'type': 'object',
                'properties': {
                    'command': {
                        'type': 'string',
                        'description': refine_prompt(
                            '要执行的 bash 命令。当前一个退出代码为 `-1` 时，可以是空字符串以查看其他日志。可以是 `C-c`（Ctrl+C）以中断当前正在运行的进程。注意：一次只能执行一个 bash 命令。如果需要按顺序运行多个命令，可以使用 `&&` 或 `;` 将它们链接在一起。'
                        ),
                    },
                    'is_input': {
                        'type': 'string',
                        'description': refine_prompt(
                            '如果为 True，该命令是对正在运行进程的输入。如果为 False，该命令是要在终端中执行的 bash 命令。默认为 False。'
                        ),
                        'enum': ['true', 'false'],  # 限制为字符串布尔值
                    },
                    'timeout': {
                        'type': 'number',
                        'description': '可选。为命令执行设置以秒为单位的硬超时。如果未提供，命令将使用默认的软超时行为。',
                    },
                },
                'required': ['command'],  # 必需参数列表
            },
        ),
    )