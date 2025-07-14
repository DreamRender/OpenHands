from browsergym.core.action.highlevel import HighLevelActionSet
from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

from openhands.llm.tool_names import BROWSER_TOOL_NAME

# 浏览器动作空间配置
# 使用BrowserGym的高级动作集合，配置包含bid和nav子集，支持宽松解析和多动作执行
_browser_action_space = HighLevelActionSet(
    subsets=['bid', 'nav'],  # 动作子集：bid(browser element id)和nav(navigation)
    strict=False,  # 对动作解析采用较宽松的模式，提高容错性
    multiaction=True,  # 启用Agent一次执行多个动作的能力
)


# 浏览器工具的主要描述
# 说明了工具的基本用途、使用限制和特殊功能
_BROWSER_DESCRIPTION = """Interact with the browser using Python code. Use it ONLY when you need to interact with a webpage.

See the description of "code" parameter for more details.

Multiple actions can be provided at once, but will be executed sequentially without any feedback from the page.
More than 2-3 actions usually leads to failure or unexpected behavior. Example:
fill('a12', 'example with "quotes"')
click('a51')
click('48', button='middle', modifiers=['Shift'])

You can also use the browser to view pdf, png, jpg files.
You should first check the content of /tmp/oh-server-url to get the server url, and then use it to view the file by `goto("{server_url}/view?path={absolute_file_path}")`.
For example: `goto("http://localhost:8000/view?path=/workspace/test_document.pdf")`
Note: The file should be downloaded to the local machine first before using the browser to view it.
"""
# 翻译：使用Python代码与浏览器交互。仅在需要与网页交互时使用。
# 可以同时提供多个动作，但会按顺序执行且无页面反馈。超过2-3个动作通常会导致失败或意外行为。
# 也可以使用浏览器查看pdf、png、jpg文件。需要先检查服务器URL，然后通过goto方法查看文件。
# 注意：在使用浏览器查看之前，文件应该先下载到本地机器。

# 浏览器工具详细功能描述
# 包含15个可用函数的完整说明和使用示例
_BROWSER_TOOL_DESCRIPTION = """
The following 15 functions are available. Nothing else is supported.

goto(url: str)
    Description: Navigate to a url.
    Examples:
        goto('http://www.example.com')

go_back()
    Description: Navigate to the previous page in history.
    Examples:
        go_back()

go_forward()
    Description: Navigate to the next page in history.
    Examples:
        go_forward()

noop(wait_ms: float = 1000)
    Description: Do nothing, and optionally wait for the given time (in milliseconds).
    You can use this to get the current page content and/or wait for the page to load.
    Examples:
        noop()

        noop(500)

scroll(delta_x: float, delta_y: float)
    Description: Scroll horizontally and vertically. Amounts in pixels, positive for right or down scrolling, negative for left or up scrolling. Dispatches a wheel event.
    Examples:
        scroll(0, 200)

        scroll(-50.2, -100.5)

fill(bid: str, value: str)
    Description: Fill out a form field. It focuses the element and triggers an input event with the entered text. It works for <input>, <textarea> and [contenteditable] elements.
    Examples:
        fill('237', 'example value')

        fill('45', 'multi-line\nexample')

        fill('a12', 'example with "quotes"')

select_option(bid: str, options: str | list[str])
    Description: Select one or multiple options in a <select> element. You can specify option value or label to select. Multiple options can be selected.
    Examples:
        select_option('a48', 'blue')

        select_option('c48', ['red', 'green', 'blue'])

click(bid: str, button: Literal['left', 'middle', 'right'] = 'left', modifiers: list[typing.Literal['Alt', 'Control', 'ControlOrMeta', 'Meta', 'Shift']] = [])
    Description: Click an element.
    Examples:
        click('a51')

        click('b22', button='right')

        click('48', button='middle', modifiers=['Shift'])

dblclick(bid: str, button: Literal['left', 'middle', 'right'] = 'left', modifiers: list[typing.Literal['Alt', 'Control', 'ControlOrMeta', 'Meta', 'Shift']] = [])
    Description: Double click an element.
    Examples:
        dblclick('12')

        dblclick('ca42', button='right')

        dblclick('178', button='middle', modifiers=['Shift'])

hover(bid: str)
    Description: Hover over an element.
    Examples:
        hover('b8')

press(bid: str, key_comb: str)
    Description: Focus the matching element and press a combination of keys. It accepts the logical key names that are emitted in the keyboardEvent.key property of the keyboard events: Backquote, Minus, Equal, Backslash, Backspace, Tab, Delete, Escape, ArrowDown, End, Enter, Home, Insert, PageDown, PageUp, ArrowRight, ArrowUp, F1 - F12, Digit0 - Digit9, KeyA - KeyZ, etc. You can alternatively specify a single character you'd like to produce such as "a" or "#". Following modification shortcuts are also supported: Shift, Control, Alt, Meta, ShiftLeft, ControlOrMeta. ControlOrMeta resolves to Control on Windows and Linux and to Meta on macOS.
    Examples:
        press('88', 'Backspace')

        press('a26', 'ControlOrMeta+a')

        press('a61', 'Meta+Shift+t')

focus(bid: str)
    Description: Focus the matching element.
    Examples:
        focus('b455')

clear(bid: str)
    Description: Clear the input field.
    Examples:
        clear('996')

drag_and_drop(from_bid: str, to_bid: str)
    Description: Perform a drag & drop. Hover the element that will be dragged. Press left mouse button. Move mouse to the element that will receive the drop. Release left mouse button.
    Examples:
        drag_and_drop('56', '498')

upload_file(bid: str, file: str | list[str])
    Description: Click an element and wait for a "filechooser" event, then select one or multiple input files for upload. Relative file paths are resolved relative to the current working directory. An empty list clears the selected files.
    Examples:
        upload_file('572', '/home/user/my_receipt.pdf')

        upload_file('63', ['/home/bob/Documents/image.jpg', '/home/bob/Documents/file.zip'])
"""
# 翻译：以下15个函数可用，不支持其他功能。
# 包含导航(goto/go_back/go_forward)、等待(noop)、滚动(scroll)、表单填写(fill)、选项选择(select_option)、
# 点击操作(click/dblclick)、悬停(hover)、按键(press)、焦点(focus)、清除(clear)、拖放(drag_and_drop)、文件上传(upload_file)等功能

# 验证BrowserGym动作空间的一致性
# 确保工具描述中包含所有动作的签名和描述，防止版本不匹配
for _, action in _browser_action_space.action_set.items():
    # 检查动作签名是否在工具描述中
    assert action.signature in _BROWSER_TOOL_DESCRIPTION, (
        f'Browser description mismatch. Please double check if the BrowserGym updated their action space.\n\nAction: {action.signature}'
    )
    # 检查动作描述是否在工具描述中
    assert action.description in _BROWSER_TOOL_DESCRIPTION, (
        f'Browser description mismatch. Please double check if the BrowserGym updated their action space.\n\nAction: {action.description}'
    )

# 浏览器工具的完整配置
# 定义了工具的类型、名称、描述和参数结构
BrowserTool = ChatCompletionToolParam(
    type='function',  # 工具类型为函数
    function=ChatCompletionToolParamFunctionChunk(
        name=BROWSER_TOOL_NAME,  # 工具名称，从常量导入
        description=_BROWSER_DESCRIPTION,  # 工具的主要描述
        parameters={
            'type': 'object',  # 参数类型为对象
            'properties': {
                # Python代码参数，用于浏览器交互
                'code': {
                    'type': 'string',
                    'description': (
                        'The Python code that interacts with the browser.\n'
                        + _BROWSER_TOOL_DESCRIPTION
                    ),
                    # 翻译：与浏览器交互的Python代码，包含所有可用函数的详细说明
                }
            },
            'required': ['code'],  # 必需参数列表，code参数是必需的
        },
    ),
)