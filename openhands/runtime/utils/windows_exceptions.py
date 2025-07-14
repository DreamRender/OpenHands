"""Windows特定运行时问题的自定义异常模块。

此模块定义了Windows特定的异常类，用于处理.NET SDK、CoreCLR等Windows特定
组件的加载和运行时问题。这些异常为用户提供了更清晰的错误信息，而不是完整的
堆栈跟踪。
"""


class DotNetMissingError(Exception):
    """当.NET SDK或CoreCLR缺失或无法加载时引发的异常。
    
    此异常用于为用户提供更清晰的错误消息，而不是完整的堆栈跟踪。
    它通常在以下情况下被引发：
    - .NET CoreCLR运行时无法加载
    - .NET SDK组件缺失
    - PowerShell SDK (System.Management.Automation.dll) 无法找到或加载
    - 相关的.NET程序集无法引用
    
    Attributes:
        message (str): 主要错误消息
        details (str | None): 详细错误信息，包含具体的异常信息或路径信息
    """

    def __init__(self, message: str, details: str | None = None):
        """初始化DotNetMissingError异常。

        Args:
            message (str): 主要错误消息，描述问题的概要
            details (str | None): 可选的详细错误信息，包含具体的异常信息、
                路径信息或其他有助于调试的详细信息
        """
        self.message = message
        self.details = details
        # 调用父类Exception的初始化方法，将消息传递给基类
        super().__init__(message)
