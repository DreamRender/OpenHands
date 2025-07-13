"""Convert function calling messages to non-function calling messages and vice versa.

将函数调用消息转换为非函数调用消息，反之亦然。

This will inject prompts so that models that doesn't support function calling
can still be used with function calling agents.

这将注入提示，使得不支持函数调用的模型仍然可以与函数调用Agent一起使用。

We follow format from: https://docs.litellm.ai/docs/completion/function_call
我们遵循的格式来自: https://docs.litellm.ai/docs/completion/function_call
"""

import copy
import json
import re
import sys
from typing import Iterable

from litellm import ChatCompletionToolParam

from openhands.core.exceptions import (
    FunctionCallConversionError,
    FunctionCallValidationError,
)
from openhands.llm.tool_names import (
    BROWSER_TOOL_NAME,
    EXECUTE_BASH_TOOL_NAME,
    FINISH_TOOL_NAME,
    LLM_BASED_EDIT_TOOL_NAME,
    STR_REPLACE_EDITOR_TOOL_NAME,
)

# Inspired by: https://docs.together.ai/docs/llama-3-function-calling#function-calling-w-llama-31-70b
# 灵感来自: https://docs.together.ai/docs/llama-3-function-calling#function-calling-w-llama-31-70b
SYSTEM_PROMPT_SUFFIX_TEMPLATE: str = """
You have access to the following functions:

{description}

If you choose to call a function ONLY reply in the following format with NO suffix:

<function=example_function_name>
<parameter=example_parameter_1>value_1</parameter>
<parameter=example_parameter_2>
This is the value for the second parameter
that can span
multiple lines
</parameter>
</function>

<IMPORTANT>
Reminder:
- Function calls MUST follow the specified format, start with <function= and end with </function>
- Required parameters MUST be specified
- Only call one function at a time
- You may provide optional reasoning for your function call in natural language BEFORE the function call, but NOT after.
- If there is no function call available, answer the question like normal with your current knowledge and do not tell the user about function calls
</IMPORTANT>
"""
"""
系统提示模板后缀。

用于告知模型如何正确格式化函数调用。包含函数描述占位符{description}，
以及详细的函数调用格式说明和重要注意事项。
"""

STOP_WORDS: list[str] = ['</function']
"""
停止词列表。

包含用于识别函数调用结束的停止词。当模型生成到这些词时，
表示函数调用可能已经结束。
"""


def refine_prompt(prompt: str) -> str:
    """
    根据操作系统平台优化提示内容。
    
    在Windows平台上，将'bash'替换为'powershell'，
    以适应不同操作系统的命令行环境。
    
    Args:
        prompt (str): 原始提示文本
        
    Returns:
        str: 优化后的提示文本
    """
    # 检查是否为Windows平台
    if sys.platform == 'win32':
        # 将bash替换为powershell以适应Windows环境
        return prompt.replace('bash', 'powershell')
    # 非Windows平台直接返回原始提示
    return prompt


# NOTE: we need to make sure these examples are always in-sync with the tool interface designed in openhands/agenthub/codeact_agent/function_calling.py
# 注意：我们需要确保这些示例始终与openhands/agenthub/codeact_agent/function_calling.py中设计的工具接口保持同步

# Example snippets for each tool
# 每个工具的示例代码片段
TOOL_EXAMPLES: dict[str, dict[str, str]] = {
    'execute_bash': {
        'check_dir': """
ASSISTANT: Sure! Let me first check the current directory:
<function=execute_bash>
<parameter=command>
pwd && ls
</parameter>
</function>

USER: EXECUTION RESULT of [execute_bash]:
/workspace
openhands@runtime:~/workspace$
""",
        'run_server': """
ASSISTANT:
Let me run the Python file for you:
<function=execute_bash>
<parameter=command>
python3 app.py > server.log 2>&1 &
</parameter>
</function>

USER: EXECUTION RESULT of [execute_bash]:
[1] 121
[1]+  Exit 1                  python3 app.py > server.log 2>&1

A:
Looks like the server was running with PID 121 then crashed. Let me check the server log:
<function=execute_bash>
<parameter=command>
cat server.log
</parameter>
</function>

USER: EXECUTION RESULT of [execute_bash]:
Traceback (most recent call last):
  File "/workspace/app.py", line 2, in <module>
    from flask import Flask
ModuleNotFoundError: No module named 'flask'

A:
Looks like the server crashed because the `flask` module is not installed. Let me install the `flask` module for you:
<function=execute_bash>
<parameter=command>
pip3 install flask
</parameter>
</function>

USER: EXECUTION RESULT of [execute_bash]:
Defaulting to user installation because normal site-packages is not writeable
Collecting flask
  Using cached flask-3.0.3-py3-none-any.whl (101 kB)
Collecting blinker>=1.6.2
  Using cached blinker-1.7.0-py3-none-any.whl (13 kB)
Collecting Werkzeug>=3.0.0
  Using cached werkzeug-3.0.2-py3-none-any.whl (226 kB)
Collecting click>=8.1.3
  Using cached click-8.1.7-py3-none-any.whl (97 kB)
Collecting itsdangerous>=2.1.2
  Using cached itsdangerous-2.2.0-py3-none-any.whl (16 kB)
Requirement already satisfied: Jinja2>=3.1.2 in /home/openhands/.local/lib/python3.10/site-packages (from flask) (3.1.3)
Requirement already satisfied: MarkupSafe>=2.0 in /home/openhands/.local/lib/python3.10/site-packages (from Jinja2>=3.1.2->flask) (2.1.5)
Installing collected packages: Werkzeug, itsdangerous, click, blinker, flask
Successfully installed Werkzeug-3.0.2 blinker-1.7.0 click-8.1.7 flask-3.0.3 itsdangerous-2.2.0

A:
The `flask` module is installed successfully. Let me run the Python file again:
<function=execute_bash>
<parameter=command>
python3 app.py > server.log 2>&1 &
</parameter>
</function>

USER: EXECUTION RESULT of [execute_bash]:
[1] 124

A:
Let me check the server log again:
<function=execute_bash>
<parameter=command>
cat server.log
</parameter>
</function>

USER: EXECUTION RESULT of [execute_bash]:
* Serving Flask app 'app'
 * Debug mode: off
WARNING: This is a development server. Do not use it in a production deployment. Use a production WSGI server instead.
 * Running on http://127.0.0.1:5000
Press CTRL+C to quit

A:
The server is running on port 5000 with PID 124. You can access the list of numbers by visiting http://127.0.0.1:5000. If you have any further questions, feel free to ask!
""",
        'kill_server': """
USER: Now kill the server, make it display the numbers in a table format.

A:
Sure! Let me stop the server first:
<function=execute_bash>
<parameter=command>
kill 124
</parameter>
</function>

USER: EXECUTION RESULT of [execute_bash]:
[1]+  Terminated              python3 app.py > server.log 2>&1
""",
        'run_server_again': """
ASSISTANT:
Running the updated file:
<function=execute_bash>
<parameter=command>
python3 app.py > server.log 2>&1 &
</parameter>
</function>

USER: EXECUTION RESULT of [execute_bash]:
[1] 126

A:
The server is running on port 5000 with PID 126. You can access the list of numbers in a table format by visiting http://127.0.0.1:5000.
""",
    },
    'str_replace_editor': {
        'create_file': """
ASSISTANT:
There is no `app.py` file in the current directory. Let me create a Python file `app.py`:
<function=str_replace_editor>
<parameter=command>create</parameter>
<parameter=path>/workspace/app.py</parameter>
<parameter=file_text>
from flask import Flask
app = Flask(__name__)

@app.route('/')
def index():
    numbers = list(range(1, 11))
    return str(numbers)

if __name__ == '__main__':
    app.run(port=5000)
</parameter>
</function>

USER: EXECUTION RESULT of [str_replace_editor]:
File created successfully at: /workspace/app.py
""",
        'edit_file': """
ASSISTANT:
Now let me display the numbers in a table format:
<function=str_replace_editor>
<parameter=command>str_replace</parameter>
<parameter=path>/workspace/app.py</parameter>
<parameter=old_str>return str(numbers)</parameter>
<parameter=new_str>return '<table>' + ''.join([f'<tr><td>{i}</td></tr>' for i in numbers]) + '</table>'</parameter>
</function>

USER: EXECUTION RESULT of [str_replace_editor]:
The file /workspace/app.py has been edited. Here's the result of running `cat -n` on a snippet of /workspace/app.py:
     3
     4  @app.route('/')
     5  def index():
     6      numbers = list(range(1, 11))
     7      return '<table>' + ''.join([f'<tr><td>{i}</td></tr>' for i in numbers]) + '</table>'
     8
     9  if __name__ == '__main__':
    10      app.run(port=5000)
Review the changes and make sure they are as expected. Edit the file again if necessary.

""",
    },
    'browser': {
        'view_page': """
ASSISTANT:
Let me check how the page looks in the browser:
<function=browser>
<parameter=code>
goto('http://127.0.0.1:5000')
noop(1000)  # Wait for page to load
</parameter>
</function>

USER: EXECUTION RESULT of [browser]:
[Browser shows the numbers in a table format]
"""
    },
    'edit_file': {
        'create_file': """
ASSISTANT: There is no `app.py` file in the current directory. Let me create a Python file `app.py`:
<function=edit_file>
<parameter=path>/workspace/app.py</parameter>
<parameter=start>1</parameter>
<parameter=end>-1</parameter>
<parameter=content>
from flask import Flask
app = Flask(__name__)

@app.route('/')
def index():
    numbers = list(range(1, 11))
    return str(numbers)

if __name__ == '__main__':
    app.run(port=5000)
</parameter>
</function>

USER: EXECUTION RESULT of [edit_file]:
File created successfully at: /workspace/app.py
""",
        'edit_file': """
ASSISTANT:
Now let me display the numbers in a table format:
<function=edit_file>
<parameter=path>/workspace/app.py</parameter>
<parameter=start>6</parameter>
<parameter=end>9</parameter>
<parameter=content>
    numbers = list(range(1, 11))
    return '<table>' + ''.join([f'<tr><td>{i}</td></tr>' for i in numbers]) + '</table>'
    # ... existing code ...
if __name__ == '__main__':
</parameter>
</function>

USER: EXECUTION RESULT of [edit_file]:
The file /workspace/app.py has been edited. Here's the result of running `cat -n` on a snippet of /workspace/app.py:
     3
     4  @app.route('/')
     5  def index():
     6      numbers = list(range(1, 11))
     7      return '<table>' + ''.join([f'<tr><td>{i}</td></tr>' for i in numbers]) + '</table>'
     8
     9  if __name__ == '__main__':
    10      app.run(port=5000)
Review the changes and make sure they are as expected. Edit the file again if necessary.
""",
    },
    'finish': {
        'task_completed': """
ASSISTANT:
The server is running on port 5000 with PID 126. You can access the list of numbers in a table format by visiting http://127.0.0.1:5000. Let me know if you have any further requests!
<function=finish>
<parameter=message>The task has been completed. The web server is running and displaying numbers 1-10 in a table format at http://127.0.0.1:5000.</parameter>
<parameter=task_completed>true</parameter>
</function>
"""
    },
}
"""
工具示例字典。

包含不同工具类型的使用示例，用于生成上下文学习示例。
每个工具都有多个使用场景的示例代码片段，展示了完整的交互流程。

结构：
- execute_bash: bash命令执行示例
- str_replace_editor: 字符串替换编辑器示例  
- browser: 浏览器操作示例
- edit_file: 文件编辑示例
- finish: 任务完成示例
"""


def get_example_for_tools(tools: list[dict]) -> str:
    """
    根据可用工具生成上下文学习示例。
    
    分析传入的工具列表，确定哪些工具可用，然后构建一个完整的
    任务示例，展示如何使用这些工具来完成一个具体任务。
    
    Args:
        tools (list[dict]): 可用工具列表，每个工具都有type和function字段
        
    Returns:
        str: 生成的示例字符串，如果没有可用工具则返回空字符串
    """
    # 收集可用的工具类型
    available_tools = set()
    for tool in tools:
        # 只处理函数类型的工具
        if tool['type'] == 'function':
            name = tool['function']['name']
            # 根据工具名称映射到内部工具类型
            if name == EXECUTE_BASH_TOOL_NAME:
                available_tools.add('execute_bash')
            elif name == STR_REPLACE_EDITOR_TOOL_NAME:
                available_tools.add('str_replace_editor')
            elif name == BROWSER_TOOL_NAME:
                available_tools.add('browser')
            elif name == FINISH_TOOL_NAME:
                available_tools.add('finish')
            elif name == LLM_BASED_EDIT_TOOL_NAME:
                available_tools.add('edit_file')

    # 如果没有可用工具，返回空字符串
    if not available_tools:
        return ''

    # 构建示例的开头部分
    example = """Here's a running example of how to perform a task with the provided tools.

--------------------- START OF EXAMPLE ---------------------

USER: Create a list of numbers from 1 to 10, and display them in a web page at port 5000.

"""

    # 根据可用工具按顺序构建示例
    # 首先检查当前目录（如果有bash工具）
    if 'execute_bash' in available_tools:
        example += TOOL_EXAMPLES['execute_bash']['check_dir']

    # 创建文件（优先使用str_replace_editor，其次使用edit_file）
    if 'str_replace_editor' in available_tools:
        example += TOOL_EXAMPLES['str_replace_editor']['create_file']
    elif 'edit_file' in available_tools:
        example += TOOL_EXAMPLES['edit_file']['create_file']

    # 运行服务器（如果有bash工具）
    if 'execute_bash' in available_tools:
        example += TOOL_EXAMPLES['execute_bash']['run_server']

    # 查看页面（如果有浏览器工具）
    if 'browser' in available_tools:
        example += TOOL_EXAMPLES['browser']['view_page']

    # 停止服务器（如果有bash工具）
    if 'execute_bash' in available_tools:
        example += TOOL_EXAMPLES['execute_bash']['kill_server']

    # 编辑文件（优先使用str_replace_editor，其次使用edit_file）
    if 'str_replace_editor' in available_tools:
        example += TOOL_EXAMPLES['str_replace_editor']['edit_file']
    elif 'edit_file' in available_tools:
        example += TOOL_EXAMPLES['edit_file']['edit_file']

    # 重新运行服务器（如果有bash工具）
    if 'execute_bash' in available_tools:
        example += TOOL_EXAMPLES['execute_bash']['run_server_again']

    # 完成任务（如果有finish工具）
    if 'finish' in available_tools:
        example += TOOL_EXAMPLES['finish']['task_completed']

    # 添加示例结尾部分
    example += """
--------------------- END OF EXAMPLE ---------------------

Do NOT assume the environment is the same as in the example above.

--------------------- NEW TASK DESCRIPTION ---------------------
"""
    # 去除开头的多余空白
    example = example.lstrip()

    return example


IN_CONTEXT_LEARNING_EXAMPLE_PREFIX = get_example_for_tools
"""
上下文学习示例前缀生成函数的别名。

指向get_example_for_tools函数，用于根据可用工具生成示例前缀。
"""

IN_CONTEXT_LEARNING_EXAMPLE_SUFFIX: str = """
--------------------- END OF NEW TASK DESCRIPTION ---------------------

PLEASE follow the format strictly! PLEASE EMIT ONE AND ONLY ONE FUNCTION CALL PER MESSAGE.
"""
"""
上下文学习示例后缀。

包含任务描述结束标记和重要提醒，强调必须严格遵循格式，
并且每条消息只能包含一个函数调用。
"""

# Regex patterns for function call parsing
# 用于函数调用解析的正则表达式模式
FN_REGEX_PATTERN: str = r'<function=([^>]+)>\n(.*?)</function>'
"""
函数调用的正则表达式模式。

用于匹配完整的函数调用块，包括函数名和函数体。
模式说明：
- <function=([^>]+)>: 匹配函数开始标签并捕获函数名
- \n(.*?)</function>: 匹配函数体内容直到结束标签
"""

FN_PARAM_REGEX_PATTERN: str = r'<parameter=([^>]+)>(.*?)</parameter>'
"""
函数参数的正则表达式模式。

用于匹配函数调用中的参数块，包括参数名和参数值。
模式说明：
- <parameter=([^>]+)>: 匹配参数开始标签并捕获参数名
- (.*?)</parameter>: 匹配参数值内容直到结束标签
"""

# Add new regex pattern for tool execution results
# 添加用于工具执行结果的新正则表达式模式
TOOL_RESULT_REGEX_PATTERN: str = r'EXECUTION RESULT of \[(.*?)\]:\n(.*)'
"""
工具执行结果的正则表达式模式。

用于匹配工具执行结果的格式化输出。
模式说明：
- EXECUTION RESULT of \[(.*?)\]: 匹配结果标题并捕获工具名
- \n(.*): 匹配结果内容
"""


def convert_tool_call_to_string(tool_call: dict) -> str:
    """
    将工具调用转换为字符串格式。
    
    将标准的工具调用字典格式转换为自定义的字符串格式，
    以便不支持原生函数调用的模型能够理解和处理。
    
    Args:
        tool_call (dict): 工具调用字典，必须包含id、type、function字段
        
    Returns:
        str: 格式化的工具调用字符串
        
    Raises:
        FunctionCallConversionError: 当工具调用格式不正确时抛出
    """
    # 验证工具调用必须包含function字段
    if 'function' not in tool_call:
        raise FunctionCallConversionError("Tool call must contain 'function' key.")
    # 验证工具调用必须包含id字段
    if 'id' not in tool_call:
        raise FunctionCallConversionError("Tool call must contain 'id' key.")
    # 验证工具调用必须包含type字段
    if 'type' not in tool_call:
        raise FunctionCallConversionError("Tool call must contain 'type' key.")
    # 验证工具调用类型必须是function
    if tool_call['type'] != 'function':
        raise FunctionCallConversionError("Tool call type must be 'function'.")

    # 构建函数调用字符串的开始部分
    ret = f'<function={tool_call["function"]["name"]}>\n'
    try:
        # 解析函数参数JSON字符串
        args = json.loads(tool_call['function']['arguments'])
    except json.JSONDecodeError as e:
        # 如果JSON解析失败，抛出转换错误
        raise FunctionCallConversionError(
            f'Failed to parse arguments as JSON. Arguments: {tool_call["function"]["arguments"]}'
        ) from e
    
    # 遍历每个参数，构建参数字符串
    for param_name, param_value in args.items():
        # 检查参数值是否包含多行内容
        is_multiline = isinstance(param_value, str) and '\n' in param_value
        ret += f'<parameter={param_name}>'
        # 如果是多行内容，在开始处添加换行符
        if is_multiline:
            ret += '\n'
        # 处理不同类型的参数值
        if isinstance(param_value, list) or isinstance(param_value, dict):
            # 列表和字典类型转换为JSON字符串
            ret += json.dumps(param_value)
        else:
            # 其他类型直接转换为字符串
            ret += f'{param_value}'
        # 如果是多行内容，在结束处添加换行符
        if is_multiline:
            ret += '\n'
        ret += '</parameter>\n'
    # 添加函数调用结束标签
    ret += '</function>'
    return ret


def convert_tools_to_description(tools: list[dict]) -> str:
    """
    将工具列表转换为描述文本。
    
    将工具的详细信息（包括名称、描述、参数等）格式化为
    易于理解的文本描述，供模型参考使用。
    
    Args:
        tools (list[dict]): 工具列表，每个工具包含函数定义
        
    Returns:
        str: 格式化的工具描述文本
    """
    ret = ''
    # 遍历每个工具
    for i, tool in enumerate(tools):
        # 确保工具类型为function
        assert tool['type'] == 'function'
        fn = tool['function']
        # 如果不是第一个工具，添加分隔符
        if i > 0:
            ret += '\n'
        # 添加函数标题
        ret += f'---- BEGIN FUNCTION #{i + 1}: {fn["name"]} ----\n'
        # 添加函数描述
        ret += f'Description: {fn["description"]}\n'

        # 处理函数参数
        if 'parameters' in fn:
            ret += 'Parameters:\n'
            # 获取参数属性和必需参数列表
            properties = fn['parameters'].get('properties', {})
            required_params = set(fn['parameters'].get('required', []))

            # 遍历每个参数
            for j, (param_name, param_info) in enumerate(properties.items()):
                # 确定参数是否为必需参数
                is_required = param_name in required_params
                param_status = 'required' if is_required else 'optional'
                param_type = param_info.get('type', 'string')

                # 获取参数描述
                desc = param_info.get('description', 'No description provided')

                # 处理枚举值（如果存在）
                if 'enum' in param_info:
                    enum_values = ', '.join(f'`{v}`' for v in param_info['enum'])
                    desc += f'\nAllowed values: [{enum_values}]'

                # 添加参数信息到描述中
                ret += (
                    f'  ({j + 1}) {param_name} ({param_type}, {param_status}): {desc}\n'
                )
        else:
            # 如果没有参数，添加说明
            ret += 'No parameters are required for this function.\n'

        # 添加函数结束标记
        ret += f'---- END FUNCTION #{i + 1} ----\n'
    return ret


def convert_fncall_messages_to_non_fncall_messages(
    messages: list[dict],
    tools: list[ChatCompletionToolParam],
    add_in_context_learning_example: bool = True,
) -> list[dict]:
    """
    将函数调用消息转换为非函数调用消息。
    
    这是核心转换函数，将标准的函数调用格式转换为文本格式，
    使不支持函数调用的模型也能理解和使用工具。
    
    Args:
        messages (list[dict]): 原始消息列表
        tools (list[ChatCompletionToolParam]): 可用工具列表
        add_in_context_learning_example (bool): 是否添加上下文学习示例
        
    Returns:
        list[dict]: 转换后的消息列表
        
    Raises:
        FunctionCallConversionError: 转换过程中遇到错误时抛出
    """
    # 创建消息的深拷贝，避免修改原始数据
    messages = copy.deepcopy(messages)

    # 将工具列表转换为描述文本
    formatted_tools = convert_tools_to_description(tools)
    # 生成系统提示后缀
    system_prompt_suffix = SYSTEM_PROMPT_SUFFIX_TEMPLATE.format(
        description=formatted_tools
    )

    converted_messages = []
    first_user_message_encountered = False  # 标记是否遇到第一个用户消息
    
    # 遍历每条消息
    for message in messages:
        role = message['role']
        content = message['content']

        # 1. 处理系统消息
        # 在系统消息内容后追加系统提示后缀
        if role == 'system':
            if isinstance(content, str):
                # 字符串类型直接追加
                content += system_prompt_suffix
            elif isinstance(content, list):
                # 列表类型需要处理最后一个文本元素
                if content and content[-1]['type'] == 'text':
                    content[-1]['text'] += system_prompt_suffix
                else:
                    # 如果没有文本元素，添加新的文本元素
                    content.append({'type': 'text', 'text': system_prompt_suffix})
            else:
                # 不支持的内容类型
                raise FunctionCallConversionError(
                    f'Unexpected content type {type(content)}. Expected str or list. Content: {content}'
                )
            converted_messages.append({'role': 'system', 'content': content})

        # 2. 处理用户消息（基本无变化，但可能添加示例）
        elif role == 'user':
            # 为第一个用户消息添加上下文学习示例
            if not first_user_message_encountered and add_in_context_learning_example:
                first_user_message_encountered = True

                # 根据可用工具生成示例
                example = IN_CONTEXT_LEARNING_EXAMPLE_PREFIX(tools)

                # 如果有示例，添加到消息中
                if example:
                    if isinstance(content, str):
                        # 字符串类型：示例 + 原内容 + 后缀
                        content = example + content + IN_CONTEXT_LEARNING_EXAMPLE_SUFFIX
                    elif isinstance(content, list):
                        # 列表类型：需要处理第一个文本元素
                        if content and content[0]['type'] == 'text':
                            content[0]['text'] = (
                                example
                                + content[0]['text']
                                + IN_CONTEXT_LEARNING_EXAMPLE_SUFFIX
                            )
                        else:
                            # 如果没有文本元素，构建新的内容列表
                            content = (
                                [
                                    {
                                        'type': 'text',
                                        'text': example,
                                    }
                                ]
                                + content
                                + [
                                    {
                                        'type': 'text',
                                        'text': IN_CONTEXT_LEARNING_EXAMPLE_SUFFIX,
                                    }
                                ]
                            )
                    else:
                        # 不支持的内容类型
                        raise FunctionCallConversionError(
                            f'Unexpected content type {type(content)}. Expected str or list. Content: {content}'
                        )
            converted_messages.append(
                {
                    'role': 'user',
                    'content': content,
                }
            )

        # 3. 处理助手消息
        # - 3.1 如果没有函数调用，保持不变
        # - 3.2 如果有函数调用，需要转换
        elif role == 'assistant':
            if 'tool_calls' in message and message['tool_calls'] is not None:
                # 验证只有一个工具调用（当前不支持多个）
                if len(message['tool_calls']) != 1:
                    raise FunctionCallConversionError(
                        f'Expected exactly one tool call in the message. More than one tool call is not supported. But got {len(message["tool_calls"])} tool calls. Content: {content}'
                    )
                try:
                    # 将工具调用转换为字符串格式
                    tool_content = convert_tool_call_to_string(message['tool_calls'][0])
                except FunctionCallConversionError as e:
                    # 转换失败时提供详细错误信息
                    raise FunctionCallConversionError(
                        f'Failed to convert tool call to string.\nCurrent tool call: {message["tool_calls"][0]}.\nRaw messages: {json.dumps(messages, indent=2)}'
                    ) from e
                
                # 将工具调用内容添加到原有内容中
                if isinstance(content, str):
                    content += '\n\n' + tool_content
                    content = content.lstrip()  # 去除开头空白
                elif isinstance(content, list):
                    # 列表类型：添加到最后一个文本元素或创建新元素
                    if content and content[-1]['type'] == 'text':
                        content[-1]['text'] += '\n\n' + tool_content
                        content[-1]['text'] = content[-1]['text'].lstrip()
                    else:
                        content.append({'type': 'text', 'text': tool_content})
                else:
                    # 不支持的内容类型
                    raise FunctionCallConversionError(
                        f'Unexpected content type {type(content)}. Expected str or list. Content: {content}'
                    )
            converted_messages.append({'role': 'assistant', 'content': content})

        # 4. 处理工具消息（工具输出）
        elif role == 'tool':
            # 将工具结果转换为用户消息格式
            tool_name = message.get('name', 'function')
            prefix = f'EXECUTION RESULT of [{tool_name}]:\n'
            # 省略"tool_call_id"和"name"字段
            if isinstance(content, str):
                content = prefix + content
            elif isinstance(content, list):
                # 找到第一个文本内容并添加前缀
                if content and (
                    first_text_content := next(
                        (c for c in content if c['type'] == 'text'), None
                    )
                ):
                    first_text_content['text'] = prefix + first_text_content['text']
                else:
                    # 如果没有文本内容，创建新的文本元素
                    content = [{'type': 'text', 'text': prefix}] + content
            else:
                # 不支持的内容类型
                raise FunctionCallConversionError(
                    f'Unexpected content type {type(content)}. Expected str or list. Content: {content}'
                )
            # 处理缓存控制（如果存在）
            if 'cache_control' in message:
                content[-1]['cache_control'] = {'type': 'ephemeral'}
            converted_messages.append({'role': 'user', 'content': content})
        else:
            # 不支持的消息角色
            raise FunctionCallConversionError(
                f'Unexpected role {role}. Expected system, user, assistant or tool.'
            )
    return converted_messages


def _extract_and_validate_params(
    matching_tool: dict, param_matches: Iterable[re.Match], fn_name: str
) -> dict:
    """
    提取并验证函数参数。
    
    从正则匹配结果中提取参数，并根据工具定义验证参数的合法性，
    包括类型检查、必需参数检查和枚举值检查。
    
    Args:
        matching_tool (dict): 匹配的工具定义
        param_matches (Iterable[re.Match]): 参数匹配结果迭代器
        fn_name (str): 函数名称，用于错误提示
        
    Returns:
        dict: 验证后的参数字典
        
    Raises:
        FunctionCallValidationError: 参数验证失败时抛出
    """
    params = {}
    # 获取必需参数集合
    required_params = set()
    if 'parameters' in matching_tool and 'required' in matching_tool['parameters']:
        required_params = set(matching_tool['parameters'].get('required', []))

    # 获取允许的参数集合
    allowed_params = set()
    if 'parameters' in matching_tool and 'properties' in matching_tool['parameters']:
        allowed_params = set(matching_tool['parameters']['properties'].keys())

    # 获取参数名称到类型的映射
    param_name_to_type = {}
    if 'parameters' in matching_tool and 'properties' in matching_tool['parameters']:
        param_name_to_type = {
            name: val.get('type', 'string')
            for name, val in matching_tool['parameters']['properties'].items()
        }

    # 收集找到的参数
    found_params = set()
    for param_match in param_matches:
        param_name = param_match.group(1)
        param_value = param_match.group(2)

        # 验证参数是否被允许
        if allowed_params and param_name not in allowed_params:
            raise FunctionCallValidationError(
                f"Parameter '{param_name}' is not allowed for function '{fn_name}'. "
                f'Allowed parameters: {allowed_params}'
            )

        # 验证和转换参数类型
        # 支持的类型：string、integer、array
        if param_name in param_name_to_type:
            if param_name_to_type[param_name] == 'integer':
                # 整数类型转换
                try:
                    param_value = int(param_value)
                except ValueError:
                    raise FunctionCallValidationError(
                        f"Parameter '{param_name}' is expected to be an integer."
                    )
            elif param_name_to_type[param_name] == 'array':
                # 数组类型转换（JSON解析）
                try:
                    param_value = json.loads(param_value)
                except json.JSONDecodeError:
                    raise FunctionCallValidationError(
                        f"Parameter '{param_name}' is expected to be an array."
                    )
            else:
                # 字符串类型，无需转换
                pass

        # 枚举值检查
        if 'enum' in matching_tool['parameters']['properties'][param_name]:
            if (
                param_value
                not in matching_tool['parameters']['properties'][param_name]['enum']
            ):
                raise FunctionCallValidationError(
                    f"Parameter '{param_name}' is expected to be one of {matching_tool['parameters']['properties'][param_name]['enum']}."
                )

        # 添加参数到结果字典
        params[param_name] = param_value
        found_params.add(param_name)

    # 检查所有必需参数是否都存在
    missing_params = required_params - found_params
    if missing_params:
        raise FunctionCallValidationError(
            f"Missing required parameters for function '{fn_name}': {missing_params}"
        )
    return params


def _fix_stopword(content: str) -> str:
    """
    修复停止词问题。
    
    某些LLM可能不会返回完整的停止词，这个函数用来修复
    不完整的函数调用结束标签。
    
    Args:
        content (str): 可能包含不完整停止词的内容
        
    Returns:
        str: 修复后的内容
    """
    # 检查是否包含函数调用且只有一个函数调用
    if '<function=' in content and content.count('<function=') == 1:
        # 如果以不完整的结束标签结尾，补全它
        if content.endswith('</'):
            content = content.rstrip() + 'function>'
        else:
            # 如果完全没有结束标签，添加完整的结束标签
            content = content + '\n</function>'
    return content


def convert_non_fncall_messages_to_fncall_messages(
    messages: list[dict],
    tools: list[ChatCompletionToolParam],
) -> list[dict]:
    """
    将非函数调用消息转换回函数调用消息。
    
    这是convert_fncall_messages_to_non_fncall_messages的逆向操作，
    将文本格式的函数调用转换回标准的函数调用格式。
    
    Args:
        messages (list[dict]): 非函数调用格式的消息列表
        tools (list[ChatCompletionToolParam]): 可用工具列表
        
    Returns:
        list[dict]: 转换后的函数调用格式消息列表
        
    Raises:
        FunctionCallConversionError: 转换过程中遇到错误时抛出
        FunctionCallValidationError: 函数调用验证失败时抛出
    """
    # 创建消息的深拷贝
    messages = copy.deepcopy(messages)
    # 生成工具描述和系统提示后缀
    formatted_tools = convert_tools_to_description(tools)
    system_prompt_suffix = SYSTEM_PROMPT_SUFFIX_TEMPLATE.format(
        description=formatted_tools
    )

    converted_messages = []
    tool_call_counter = 1  # 工具调用计数器，用于生成唯一ID

    first_user_message_encountered = False
    for message in messages:
        role, content = message['role'], message['content']
        content = content or ''  # 处理content为None的情况
        
        # 处理系统消息，移除添加的后缀
        if role == 'system':
            if isinstance(content, str):
                # 移除后缀（如果存在）
                content = content.split(system_prompt_suffix)[0]
            elif isinstance(content, list):
                if content and content[-1]['type'] == 'text':
                    # 从最后一个文本项移除后缀
                    content[-1]['text'] = content[-1]['text'].split(
                        system_prompt_suffix
                    )[0]
            converted_messages.append({'role': 'system', 'content': content})
            
        # 处理用户消息
        elif role == 'user':
            # 检查并替换上下文学习示例
            if not first_user_message_encountered:
                first_user_message_encountered = True
                if isinstance(content, str):
                    # 移除任何现有的示例
                    if content.startswith(IN_CONTEXT_LEARNING_EXAMPLE_PREFIX(tools)):
                        content = content.replace(
                            IN_CONTEXT_LEARNING_EXAMPLE_PREFIX(tools), '', 1
                        )
                    if content.endswith(IN_CONTEXT_LEARNING_EXAMPLE_SUFFIX):
                        content = content.replace(
                            IN_CONTEXT_LEARNING_EXAMPLE_SUFFIX, '', 1
                        )
                elif isinstance(content, list):
                    for item in content:
                        if item['type'] == 'text':
                            # 移除任何现有的示例
                            example = IN_CONTEXT_LEARNING_EXAMPLE_PREFIX(tools)
                            if item['text'].startswith(example):
                                item['text'] = item['text'].replace(example, '', 1)
                            if item['text'].endswith(
                                IN_CONTEXT_LEARNING_EXAMPLE_SUFFIX
                            ):
                                item['text'] = item['text'].replace(
                                    IN_CONTEXT_LEARNING_EXAMPLE_SUFFIX, '', 1
                                )
                else:
                    raise FunctionCallConversionError(
                        f'Unexpected content type {type(content)}. Expected str or list. Content: {content}'
                    )

            # 检查工具执行结果模式
            if isinstance(content, str):
                tool_result_match = re.search(
                    TOOL_RESULT_REGEX_PATTERN, content, re.DOTALL
                )
            elif isinstance(content, list):
                # 在列表中查找匹配的工具结果
                tool_result_match = next(
                    (
                        _match
                        for item in content
                        if item.get('type') == 'text'
                        and (
                            _match := re.search(
                                TOOL_RESULT_REGEX_PATTERN, item['text'], re.DOTALL
                            )
                        )
                    ),
                    None,
                )
            else:
                raise FunctionCallConversionError(
                    f'Unexpected content type {type(content)}. Expected str or list. Content: {content}'
                )

            # 如果找到工具结果，转换为工具消息格式
            if tool_result_match:
                if isinstance(content, list):
                    text_content_items = [
                        item for item in content if item.get('type') == 'text'
                    ]
                    if not text_content_items:
                        raise FunctionCallConversionError(
                            f'Could not find text content in message with tool result. Content: {content}'
                        )
                elif not isinstance(content, str):
                    raise FunctionCallConversionError(
                        f'Unexpected content type {type(content)}. Expected str or list. Content: {content}'
                    )

                # 提取工具名称和结果
                tool_name = tool_result_match.group(1)
                tool_result = tool_result_match.group(2).strip()

                # 转换为工具消息格式
                converted_messages.append(
                    {
                        'role': 'tool',
                        'name': tool_name,
                        'content': [{'type': 'text', 'text': tool_result}]
                        if isinstance(content, list)
                        else tool_result,
                        'tool_call_id': f'toolu_{tool_call_counter - 1:02d}',  # 使用最后生成的ID
                    }
                )
            else:
                # 普通用户消息，保持不变
                converted_messages.append({'role': 'user', 'content': content})

        # 处理助手消息
        elif role == 'assistant':
            if isinstance(content, str):
                # 修复停止词问题
                content = _fix_stopword(content)
                # 查找函数调用模式
                fn_match = re.search(FN_REGEX_PATTERN, content, re.DOTALL)
            elif isinstance(content, list):
                if content and content[-1]['type'] == 'text':
                    # 修复最后一个文本项的停止词问题
                    content[-1]['text'] = _fix_stopword(content[-1]['text'])
                    fn_match = re.search(
                        FN_REGEX_PATTERN, content[-1]['text'], re.DOTALL
                    )
                else:
                    fn_match = None
                # 检查是否存在函数调用但不在最后位置
                fn_match_exists = any(
                    item.get('type') == 'text'
                    and re.search(FN_REGEX_PATTERN, item['text'], re.DOTALL)
                    for item in content
                )
                if fn_match_exists and not fn_match:
                    raise FunctionCallConversionError(
                        f'Expecting function call in the LAST index of content list. But got content={content}'
                    )
            else:
                raise FunctionCallConversionError(
                    f'Unexpected content type {type(content)}. Expected str or list. Content: {content}'
                )

            # 如果找到函数调用，进行转换
            if fn_match:
                fn_name = fn_match.group(1)
                fn_body = fn_match.group(2)
                
                # 在可用工具中查找匹配的工具
                matching_tool = next(
                    (
                        tool['function']
                        for tool in tools
                        if tool['type'] == 'function'
                        and tool['function']['name'] == fn_name
                    ),
                    None,
                )
                # 验证函数是否存在于工具列表中
                if not matching_tool:
                    raise FunctionCallValidationError(
                        f"Function '{fn_name}' not found in available tools: {[tool['function']['name'] for tool in tools if tool['type'] == 'function']}"
                    )

                # 解析参数
                param_matches = re.finditer(FN_PARAM_REGEX_PATTERN, fn_body, re.DOTALL)
                params = _extract_and_validate_params(
                    matching_tool, param_matches, fn_name
                )

                # 创建带有唯一ID的工具调用
                tool_call_id = f'toolu_{tool_call_counter:02d}'
                tool_call = {
                    'index': 1,  # 始终为1，因为我们只支持每条消息一个工具调用
                    'id': tool_call_id,
                    'type': 'function',
                    'function': {'name': fn_name, 'arguments': json.dumps(params)},
                }
                tool_call_counter += 1  # 递增计数器

                # 从内容中移除函数调用部分
                if isinstance(content, list):
                    assert content and content[-1]['type'] == 'text'
                    content[-1]['text'] = (
                        content[-1]['text'].split('<function=')[0].strip()
                    )
                elif isinstance(content, str):
                    content = content.split('<function=')[0].strip()
                else:
                    raise FunctionCallConversionError(
                        f'Unexpected content type {type(content)}. Expected str or list. Content: {content}'
                    )

                # 添加包含工具调用的助手消息
                converted_messages.append(
                    {'role': 'assistant', 'content': content, 'tool_calls': [tool_call]}
                )
            else:
                # 没有函数调用，保持消息不变
                converted_messages.append(message)

        else:
            # 不支持的消息角色
            raise FunctionCallConversionError(
                f'Unexpected role {role}. Expected system, user, or assistant in non-function calling messages.'
            )
    return converted_messages


def convert_from_multiple_tool_calls_to_single_tool_call_messages(
    messages: list[dict],
    ignore_final_tool_result: bool = False,
) -> list[dict]:
    """
    将包含多个工具调用的消息拆分为多个单工具调用消息。
    
    将一条包含多个工具调用的消息分解为多条只包含单个工具调用的消息，
    以符合某些模型只支持单工具调用的限制。
    
    Args:
        messages (list[dict]): 原始消息列表
        ignore_final_tool_result (bool): 是否忽略最终的工具结果检查
        
    Returns:
        list[dict]: 转换后的消息列表
        
    Raises:
        FunctionCallConversionError: 转换过程中遇到错误时抛出
    """
    converted_messages = []

    # 跟踪待处理的工具调用
    pending_tool_calls: dict[str, dict] = {}
    
    for message in messages:
        role, content = message['role'], message['content']
        
        if role == 'assistant':
            # 检查是否有多个工具调用
            if message.get('tool_calls') and len(message['tool_calls']) > 1:
                # 通过将多个工具调用拆分为多个消息来处理
                for i, tool_call in enumerate(message['tool_calls']):
                    pending_tool_calls[tool_call['id']] = {
                        'role': 'assistant',
                        'content': content if i == 0 else '',  # 只有第一个消息包含内容
                        'tool_calls': [tool_call],
                    }
            else:
                # 单个或没有工具调用，直接添加
                converted_messages.append(message)
                
        elif role == 'tool':
            # 检查工具调用ID是否在待处理列表中
            if message['tool_call_id'] in pending_tool_calls:
                # 从待处理列表中移除工具调用
                _tool_call_message = pending_tool_calls.pop(message['tool_call_id'])
                converted_messages.append(_tool_call_message)
                # 添加工具结果
                converted_messages.append(message)
            else:
                # 确保没有未处理的待处理工具调用
                assert len(pending_tool_calls) == 0, (
                    f'Found pending tool calls but not found in pending list: {pending_tool_calls=}'
                )
                converted_messages.append(message)
        else:
            # 其他角色的消息，确保没有未处理的待处理工具调用
            assert len(pending_tool_calls) == 0, (
                f'Found pending tool calls but not expect to handle it with role {role}: {pending_tool_calls=}, {message=}'
            )
            converted_messages.append(message)

    # 检查是否还有未处理的工具调用（除非忽略最终工具结果）
    if not ignore_final_tool_result and len(pending_tool_calls) > 0:
        raise FunctionCallConversionError(
            f'Found pending tool calls but no tool result: {pending_tool_calls=}'
        )
    return converted_messages
