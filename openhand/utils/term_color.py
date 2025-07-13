from enum import Enum

from termcolor import colored


class TermColor(Enum):
    """终端颜色代码枚举类。
    
    定义了在终端中显示不同类型信息时使用的颜色代码。
    这些颜色代码用于提升终端输出的可读性，通过不同颜色区分不同类型的信息。
    """

    WARNING = 'yellow'  # 警告信息使用黄色
    SUCCESS = 'green'   # 成功信息使用绿色
    ERROR = 'red'       # 错误信息使用红色
    INFO = 'blue'       # 一般信息使用蓝色


def colorize(text: str, color: TermColor = TermColor.WARNING) -> str:
    """为文本添加指定的颜色。

    使用termcolor库为终端输出的文本添加颜色，提升用户体验。
    默认使用警告色（黄色）来突出显示重要信息。

    Args:
        text (str): 需要着色的文本内容
        color (TermColor, optional): 要使用的颜色枚举值。默认为TermColor.WARNING（黄色）

    Returns:
        str: 经过颜色处理的文本，可直接在终端中显示

    Example:
        >>> colorize("警告信息", TermColor.WARNING)
        >>> colorize("成功信息", TermColor.SUCCESS)
    """
    return colored(text, color.value)
