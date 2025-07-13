# 导入 BrowserGym 的高级动作集
from browsergym.core.action.highlevel import HighLevelActionSet
# 导入 LLM 工具相关类型
from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

# 导入工具名称常量
from openhands.llm.tool_names import BROWSER_TOOL_NAME

# 从 browsergym/core/action/highlevel.py 配置浏览器动作空间
# 创建浏览器高级动作集合，包含 bid（通过 ID）和 nav（导航）子集
_browser_action_space = HighLevelActionSet(
    subsets=['bid', 'nav'],  # 启用的动作子集
    strict=False,            # 对动作解析不严格，提高容错性
    multiaction=True,        # 启用 Agent 一次执行多个动作
)

# 浏览器工具的主要描述
_BROWSER_DESCRIPTION = """使用 Python 代码与浏览器交互。仅在需要与网页交互时使用。

有关更多详细信息，请参阅 "code" 参数的描述。

可以一次提供多个动作，但将按顺序执行，不会收到页面的反馈。
超过 2-3 个动作通常会导致失败或意外行为。示例：
fill('a12', 'example with "quotes"')
click('a51')
click('48', button='middle', modifiers=['Shift'])

您也可以使用浏览器查看 pdf、png、jpg 文件。
您应该首先检查 /tmp/oh-server-url 的内容以获取服务器 URL，然后使用它通过 `goto("{server_url}/view?path={absolute_file_path}")` 查看文件。
例如：`goto("http://localhost:8000/view?path=/workspace/test_document.pdf")`
注意：在使用浏览器查看文件之前，应先将文件下载到本地机器。
"""

# 详细的浏览器工具功能描述
_BROWSER_TOOL_DESCRIPTION = """
以下 15 个函数可用。不支持其他任何功能。

goto(url: str)
    描述：导航到一个 URL。
    示例：
        goto('http://www.example.com')

go_back()
    描述：导航到历史记录中的上一页。
    示例：
        go_back()

go_forward()
    描述：导航到历史记录中的下一页。
    示例：
        go_forward()

noop(wait_ms: float = 1000)
    描述：什么都不做，可选择等待给定时间（以毫秒为单位）。
    您可以使用此功能获取当前页面内容和/或等待页面加载。
    示例：
        noop()

        noop(500)

scroll(delta_x: float, delta_y: float)
    描述：水平和垂直滚动。数量以像素为单位，正值表示向右或向下滚动，负值表示向左或向上滚动。分发滚轮事件。
    示例：
        scroll(0, 200)

        scroll(-50.2, -100.5)

fill(bid: str, value: str)
    描述：填写表单字段。它聚焦元素并使用输入的文本触发输入事件。适用于 <input>、<textarea> 和 [contenteditable] 元素。
    示例：
        fill('237', 'example value')

        fill('45', 'multi-line\nexample')

        fill('a12', 'example with "quotes"')

select_option(bid: str, options: str | list[str])
    描述：在 <select> 元素中选择一个或多个选项。您可以指定选项值或标签进行选择。可以选择多个选项。
    示例：
        select_option('a48', 'blue')

        select_option('c48', ['red', 'green', 'blue'])

click(bid: str, button: Literal['left', 'middle', 'right'] = 'left', modifiers: list[typing.Literal['Alt', 'Control', 'ControlOrMeta', 'Meta', 'Shift']] = [])
    描述：点击元素。
    示例：
        click('a51')

        click('b22', button='right')

        click('48', button='middle', modifiers=['Shift'])

dblclick(bid: str, button: Literal['left', 'middle', 'right'] = 'left', modifiers: list[typing.Literal['Alt', 'Control', 'ControlOrMeta', 'Meta', 'Shift']] = [])
    描述：双击元素。
    示例：
        dblclick('12')

        dblclick('ca42', button='right')

        dblclick('178', button='middle', modifiers=['Shift'])

hover(bid: str)
    描述：悬停在元素上。
    示例：
        hover('b8')

press(bid: str, key_comb: str)
    描述：聚焦匹配元素并按下键组合。它接受键盘事件的 keyboardEvent.key 属性中发出的逻辑键名：Backquote、Minus、Equal、Backslash、Backspace、Tab、Delete、Escape、ArrowDown、End、Enter、Home、Insert、PageDown、PageUp、ArrowRight、ArrowUp、F1 - F12、Digit0 - Digit9、KeyA - KeyZ 等。您也可以指定要产生的单个字符，如 "a" 或 "#"。还支持以下修饰键快捷键：Shift、Control、Alt、Meta、ShiftLeft、ControlOrMeta。ControlOrMeta 在 Windows 和 Linux 上解析为 Control，在 macOS 上解析为 Meta。
    示例：
        press('88', 'Backspace')

        press('a26', 'ControlOrMeta+a')

        press('a61', 'Meta+Shift+t')

focus(bid: str)
    描述：聚焦匹配元素。
    示例：
        focus('b455')

clear(bid: str)
    描述：清除输入字段。
    示例：
        clear('996')

drag_and_drop(from_bid: str, to_bid: str)
    描述：执行拖放操作。悬停将被拖动的元素。按下鼠标左键。将鼠标移动到将接收拖放的元素。释放鼠标左键。
    示例：
        drag_and_drop('56', '498')

upload_file(bid: str, file: str | list[str])
    描述：点击元素并等待 "filechooser" 事件，然后选择一个或多个输入文件进行上传。相对文件路径相对于当前工作目录解析。空列表清除所选文件。
    示例：
        upload_file('572', '/home/user/my_receipt.pdf')

        upload_file('63', ['/home/bob/Documents/image.jpg', '/home/bob/Documents/file.zip'])
"""

# 验证 BrowserGym 动作空间的一致性
# 确保所有动作的签名和描述都包含在工具描述中
for _, action in _browser_action_space.action_set.items():
    # 检查动作签名是否在描述中
    assert action.signature in _BROWSER_TOOL_DESCRIPTION, (
        f'Browser description mismatch. Please double check if the BrowserGym updated their action space.\n\nAction: {action.signature}'
    )
    # 检查动作描述是否在描述中
    assert action.description in _BROWSER_TOOL_DESCRIPTION, (
        f'Browser description mismatch. Please double check if the BrowserGym updated their action space.\n\nAction: {action.description}'
    )

# 创建浏览器工具配置
BrowserTool = ChatCompletionToolParam(
    type='function',
    function=ChatCompletionToolParamFunctionChunk(
        name=BROWSER_TOOL_NAME,  # 工具名称常量
        description=_BROWSER_DESCRIPTION,  # 工具主要描述
        parameters={
            'type': 'object',
            'properties': {
                'code': {
                    'type': 'string',
                    'description': (
                        '与浏览器交互的 Python 代码。\n'
                        + _BROWSER_TOOL_DESCRIPTION  # 详细的功能描述
                    ),
                }
            },
            'required': ['code'],  # 必需参数
        },
    ),
)