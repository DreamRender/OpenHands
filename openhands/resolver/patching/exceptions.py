"""
exceptions.py - patch处理异常定义模块

该模块定义了patch处理过程中可能遇到的各种异常类型，
包括基础异常、hunk处理异常、应用异常等。
"""


class PatchingException(Exception):
    """
    patch处理的基础异常类
    
    所有patch相关异常的基类，用于统一异常处理。
    """
    pass


class HunkException(PatchingException):
    """
    hunk处理异常类
    
    当处理diff中的某个hunk时发生错误时抛出此异常。
    
    Attributes:
        hunk (int | None): 发生错误的hunk编号，如果无法确定则为None
    """
    
    def __init__(self, msg: str, hunk: int | None = None) -> None:
        """
        初始化hunk异常
        
        Args:
            msg: 异常消息
            hunk: 发生错误的hunk编号，可选
        """
        self.hunk = hunk
        if hunk is not None:
            # 如果有hunk编号，则在错误信息中包含hunk编号
            super().__init__('{msg}, in hunk #{n}'.format(msg=msg, n=hunk))
        else:
            super().__init__(msg)


class ApplyException(PatchingException):
    """
    patch应用异常类
    
    当应用patch到文件时发生错误时抛出此异常。
    """
    pass


class SubprocessException(ApplyException):
    """
    子进程异常类
    
    当调用外部程序（如系统的patch命令）失败时抛出此异常。
    
    Attributes:
        code (int): 子进程的退出码
    """
    
    def __init__(self, msg: str, code: int) -> None:
        """
        初始化子进程异常
        
        Args:
            msg: 异常消息
            code: 子进程的退出码
        """
        super().__init__(msg)
        self.code = code


class HunkApplyException(HunkException, ApplyException, ValueError):
    """
    hunk应用异常类
    
    当特定的hunk无法应用到目标文件时抛出此异常。
    这是一个多重继承的异常类，同时继承了HunkException、ApplyException和ValueError。
    
    通常在以下情况下抛出：
    - hunk的上下文行与目标文件不匹配
    - hunk引用的行号超出文件范围
    - hunk的格式不正确
    """
    pass


class ParseException(HunkException, ValueError):
    """
    解析异常类
    
    当解析patch文件或diff内容时遇到格式错误时抛出此异常。
    继承自HunkException和ValueError。
    
    通常在以下情况下抛出：
    - patch文件格式不正确
    - diff格式无法识别
    - hunk的语法错误
    """
    pass