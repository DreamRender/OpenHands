import sys

from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

from openhands.llm.tool_names import EXECUTE_BASH_TOOL_NAME

# 详细的bash命令执行工具描述
# 包含命令执行、长时间运行命令处理、最佳实践和输出处理等详细说明
_DETAILED_BASH_DESCRIPTION = """Execute a bash command in the terminal within a persistent shell session.


### Command Execution
* One command at a time: You can only execute one bash command at a time. If you need to run multiple commands sequentially, use `&&` or `;` to chain them together.
* Persistent session: Commands execute in a persistent shell session where environment variables, virtual environments, and working directory persist between commands.
* Soft timeout: Commands have a soft timeout of 10 seconds, once that's reached, you have the option to continue or interrupt the command (see section below for details)

### Long-running Commands
* For commands that may run indefinitely, run them in the background and redirect output to a file, e.g. `python3 app.py > server.log 2>&1 &`.
* For commands that may run for a long time (e.g. installation or testing commands), or commands that run for a fixed amount of time (e.g. sleep), you should set the "timeout" parameter of your function call to an appropriate value.
* If a bash command returns exit code `-1`, this means the process hit the soft timeout and is not yet finished. By setting `is_input` to `true`, you can:
  - Send empty `command` to retrieve additional logs
  - Send text (set `command` to the text) to STDIN of the running process
  - Send control commands like `C-c` (Ctrl+C), `C-d` (Ctrl+D), or `C-z` (Ctrl+Z) to interrupt the process
  - If you do C-c, you can re-start the process with a longer "timeout" parameter to let it run to completion

### Best Practices
* Directory verification: Before creating new directories or files, first verify the parent directory exists and is the correct location.
* Directory management: Try to maintain working directory by using absolute paths and avoiding excessive use of `cd`.

### Output Handling
* Output truncation: If the output exceeds a maximum length, it will be truncated before being returned.
"""
# 翻译：在持久的shell会话中在终端执行bash命令
# 包含命令执行规则、长时间运行命令处理、最佳实践和输出处理等内容

# 简短的bash命令执行工具描述
# 提供核心功能的简洁说明，适用于需要紧凑描述的场景
_SHORT_BASH_DESCRIPTION = """Execute a bash command in the terminal.
* Long running commands: For commands that may run indefinitely, it should be run in the background and the output should be redirected to a file, e.g. command = `python3 app.py > server.log 2>&1 &`. For commands that need to run for a specific duration, you can set the "timeout" argument to specify a hard timeout in seconds.
* Interact with running process: If a bash command returns exit code `-1`, this means the process is not yet finished. By setting `is_input` to `true`, the assistant can interact with the running process and send empty `command` to retrieve any additional logs, or send additional text (set `command` to the text) to STDIN of the running process, or send command like `C-c` (Ctrl+C), `C-d` (Ctrl+D), `C-z` (Ctrl+Z) to interrupt the process.
* One command at a time: You can only execute one bash command at a time. If you need to run multiple commands sequentially, you can use `&&` or `;` to chain them together."""
# 翻译：在终端执行bash命令，包含长时间运行命令处理、进程交互和命令链接等功能


def refine_prompt(prompt: str):
    """
    根据操作系统平台优化提示文本
    
    Args:
        prompt (str): 原始提示文本
        
    Returns:
        str: 优化后的提示文本，在Windows平台将'bash'替换为'powershell'
    """
    # 如果是Windows平台，将bash替换为powershell以适配Windows环境
    if sys.platform == 'win32':
        return prompt.replace('bash', 'powershell')
    return prompt


def create_cmd_run_tool(
    use_short_description: bool = False,
) -> ChatCompletionToolParam:
    """
    创建命令执行工具的配置参数
    
    Args:
        use_short_description (bool): 是否使用简短描述，默认为False使用详细描述
        
    Returns:
        ChatCompletionToolParam: 配置好的命令执行工具参数，包含工具名称、描述和参数定义
    """
    # 根据参数选择使用详细描述还是简短描述
    description = (
        _SHORT_BASH_DESCRIPTION if use_short_description else _DETAILED_BASH_DESCRIPTION
    )
    
    # 创建并返回聊天完成工具参数配置
    return ChatCompletionToolParam(
        type='function',  # 工具类型为函数
        function=ChatCompletionToolParamFunctionChunk(
            name=EXECUTE_BASH_TOOL_NAME,  # 工具名称，从常量导入
            description=refine_prompt(description),  # 根据平台优化的描述
            parameters={
                'type': 'object',  # 参数类型为对象
                'properties': {
                    # 要执行的bash命令参数
                    'command': {
                        'type': 'string',
                        'description': refine_prompt(
                            'The bash command to execute. Can be empty string to view additional logs when previous exit code is `-1`. Can be `C-c` (Ctrl+C) to interrupt the currently running process. Note: You can only execute one bash command at a time. If you need to run multiple commands sequentially, you can use `&&` or `;` to chain them together.'
                        ),
                        # 翻译：要执行的bash命令。当前一个退出码为-1时可以为空字符串来查看额外日志。可以是C-c来中断当前运行的进程。注意：一次只能执行一个bash命令，如需顺序执行多个命令可使用&&或;连接
                    },
                    # 是否为输入模式的参数
                    'is_input': {
                        'type': 'string',
                        'description': refine_prompt(
                            'If True, the command is an input to the running process. If False, the command is a bash command to be executed in the terminal. Default is False.'
                        ),
                        # 翻译：如果为True，命令是对运行进程的输入。如果为False，命令是要在终端执行的bash命令。默认为False
                        'enum': ['true', 'false'],  # 枚举值限制
                    },
                    # 超时时间参数
                    'timeout': {
                        'type': 'number',
                        'description': 'Optional. Sets a hard timeout in seconds for the command execution. If not provided, the command will use the default soft timeout behavior.',
                        # 翻译：可选参数。为命令执行设置硬超时时间（秒）。如果未提供，命令将使用默认的软超时行为
                    },
                },
                'required': ['command'],  # 必需参数列表，只有command是必需的
            },
        ),
    )