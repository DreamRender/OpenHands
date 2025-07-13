"""
OpenHands 异常定义模块

此模块定义了 OpenHands 系统中使用的所有自定义异常类。
异常按功能模块分组，包括 Agent、控制器、LLM、运行时、浏览器和 MicroAgent 相关异常。
"""

# ============================================
# Agent 异常
# ============================================


class AgentError(Exception):
    """所有 Agent 异常的基类。"""

    pass


class AgentNoInstructionError(AgentError):
    """当 Agent 没有收到指令时抛出的异常。"""
    
    def __init__(self, message: str = '必须提供指令') -> None:
        """
        初始化异常。
        
        Args:
            message: 异常消息，默认为 '必须提供指令'
        """
        super().__init__(message)


class AgentEventTypeError(AgentError):
    """当事件类型不正确时抛出的异常。"""
    
    def __init__(self, message: str = '事件必须是字典类型') -> None:
        """
        初始化异常。
        
        Args:
            message: 异常消息，默认为 '事件必须是字典类型'
        """
        super().__init__(message)


class AgentAlreadyRegisteredError(AgentError):
    """当 Agent 类已经注册时抛出的异常。"""
    
    def __init__(self, name: str | None = None) -> None:
        """
        初始化异常。
        
        Args:
            name: Agent 名称，如果提供则包含在错误消息中
        """
        if name is not None:
            message = f"Agent 类已在 '{name}' 下注册"
        else:
            message = 'Agent 类已注册'
        super().__init__(message)


class AgentNotRegisteredError(AgentError):
    """当 Agent 类未注册时抛出的异常。"""
    
    def __init__(self, name: str | None = None) -> None:
        """
        初始化异常。
        
        Args:
            name: Agent 名称，如果提供则包含在错误消息中
        """
        if name is not None:
            message = f"没有在 '{name}' 下注册的 Agent 类"
        else:
            message = '没有注册的 Agent 类'
        super().__init__(message)


class AgentStuckInLoopError(AgentError):
    """当 Agent 陷入循环时抛出的异常。"""
    
    def __init__(self, message: str = 'Agent 陷入了循环') -> None:
        """
        初始化异常。
        
        Args:
            message: 异常消息，默认为 'Agent 陷入了循环'
        """
        super().__init__(message)


# ============================================
# Agent Controller 异常
# ============================================


class TaskInvalidStateError(Exception):
    """当任务处于无效状态时抛出的异常。"""
    
    def __init__(self, state: str | None = None) -> None:
        """
        初始化异常。
        
        Args:
            state: 无效的状态值，如果提供则包含在错误消息中
        """
        if state is not None:
            message = f'无效状态 {state}'
        else:
            message = '无效状态'
        super().__init__(message)


# ============================================
# LLM 异常
# ============================================


# 此异常会发送回 LLM
# 可能是格式错误的 JSON
class LLMMalformedActionError(Exception):
    """当 LLM 返回格式错误的操作时抛出的异常。"""
    
    def __init__(self, message: str = '响应格式错误') -> None:
        """
        初始化异常。
        
        Args:
            message: 异常消息，默认为 '响应格式错误'
        """
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        """
        返回异常的字符串表示。
        
        Returns:
            异常消息字符串
        """
        return self.message


# 此异常会发送回 LLM
# 由于某种原因，Agent 没有返回操作
class LLMNoActionError(Exception):
    """当 Agent 没有返回操作时抛出的异常。"""
    
    def __init__(self, message: str = 'Agent 必须返回一个操作') -> None:
        """
        初始化异常。
        
        Args:
            message: 异常消息，默认为 'Agent 必须返回一个操作'
        """
        super().__init__(message)


# 此异常会发送回 LLM
# LLM 输出不包含操作，或操作不是预期的类型
class LLMResponseError(Exception):
    """当无法从 LLM 响应中检索操作时抛出的异常。"""
    
    def __init__(
        self, message: str = '无法从 LLM 响应中检索操作'
    ) -> None:
        """
        初始化异常。
        
        Args:
            message: 异常消息，默认为 '无法从 LLM 响应中检索操作'
        """
        super().__init__(message)


# 此异常应该被重试
# 通常，使用非零温度重试后，LLM 会返回响应
class LLMNoResponseError(Exception):
    """当 LLM 没有返回响应时抛出的异常。"""
    
    def __init__(
        self,
        message: str = 'LLM 没有返回响应。目前仅在 Gemini 模型中出现此问题。',
    ) -> None:
        """
        初始化异常。
        
        Args:
            message: 异常消息，默认提到 Gemini 模型的特定问题
        """
        super().__init__(message)


class UserCancelledError(Exception):
    """当用户取消请求时抛出的异常。"""
    
    def __init__(self, message: str = '用户取消了请求') -> None:
        """
        初始化异常。
        
        Args:
            message: 异常消息，默认为 '用户取消了请求'
        """
        super().__init__(message)


class OperationCancelled(Exception):
    """当操作被取消时抛出的异常（例如通过键盘中断）。"""

    def __init__(self, message: str = '操作已被取消') -> None:
        """
        初始化异常。
        
        Args:
            message: 异常消息，默认为 '操作已被取消'
        """
        super().__init__(message)


class LLMContextWindowExceedError(RuntimeError):
    """当 LLM 上下文窗口超限时抛出的异常。"""
    
    def __init__(
        self,
        message: str = '对话历史超过了 LLM 上下文窗口限制。考虑开启 enable_history_truncation 配置以避免此错误',
    ) -> None:
        """
        初始化异常。
        
        Args:
            message: 异常消息，默认建议启用历史截断配置
        """
        super().__init__(message)


# ============================================
# LLM 函数调用异常
# ============================================


class FunctionCallConversionError(Exception):
    """当 FunctionCallingConverter 无法将非函数调用消息转换为函数调用消息时抛出的异常。

    这通常发生在消息格式错误时（例如，缺少 <function=...> 标签）。但不是由于 LLM 输出导致的。
    """

    def __init__(self, message: str) -> None:
        """
        初始化异常。
        
        Args:
            message: 描述转换错误的具体消息
        """
        super().__init__(message)


class FunctionCallValidationError(Exception):
    """当 FunctionCallingConverter 无法验证函数调用消息时抛出的异常。

    这通常发生在 LLM 输出无法识别的函数调用/参数名称/值时。
    """

    def __init__(self, message: str) -> None:
        """
        初始化异常。
        
        Args:
            message: 描述验证错误的具体消息
        """
        super().__init__(message)


class FunctionCallNotExistsError(Exception):
    """当 LLM 调用未注册的工具时抛出的异常。"""

    def __init__(self, message: str) -> None:
        """
        初始化异常。
        
        Args:
            message: 描述未找到函数的具体消息
        """
        super().__init__(message)


# ============================================
# Agent Runtime 异常
# ============================================


class AgentRuntimeError(Exception):
    """所有 Agent 运行时异常的基类。"""

    pass


class AgentRuntimeBuildError(AgentRuntimeError):
    """当 Agent 运行时构建操作失败时抛出的异常。"""

    pass


class AgentRuntimeTimeoutError(AgentRuntimeError):
    """当 Agent 运行时操作超时时抛出的异常。"""

    pass


class AgentRuntimeUnavailableError(AgentRuntimeError):
    """当 Agent 运行时不可用时抛出的异常。"""

    pass


class AgentRuntimeNotReadyError(AgentRuntimeUnavailableError):
    """当 Agent 运行时未准备就绪时抛出的异常。"""

    pass


class AgentRuntimeDisconnectedError(AgentRuntimeUnavailableError):
    """当 Agent 运行时连接断开时抛出的异常。"""

    pass


class AgentRuntimeNotFoundError(AgentRuntimeUnavailableError):
    """当 Agent 运行时未找到时抛出的异常。"""

    pass


# ============================================
# 浏览器异常
# ============================================


class BrowserInitException(Exception):
    """当初始化浏览器环境失败时抛出的异常。"""
    
    def __init__(
        self, message: str = '初始化浏览器环境失败'
    ) -> None:
        """
        初始化异常。
        
        Args:
            message: 异常消息，默认为 '初始化浏览器环境失败'
        """
        super().__init__(message)


class BrowserUnavailableException(Exception):
    """当浏览器环境不可用时抛出的异常。"""
    
    def __init__(
        self,
        message: str = '浏览器环境不可用，请检查是否已经初始化',
    ) -> None:
        """
        初始化异常。
        
        Args:
            message: 异常消息，默认建议检查初始化状态
        """
        super().__init__(message)


# ============================================
# MicroAgent 异常
# ============================================


class MicroagentError(Exception):
    """所有 MicroAgent 错误的基类异常。"""

    pass


class MicroagentValidationError(MicroagentError):
    """当 MicroAgent Metadata 中存在验证错误时抛出的异常。"""

    def __init__(self, message: str = 'MicroAgent 验证失败') -> None:
        """
        初始化异常。
        
        Args:
            message: 异常消息，默认为 'MicroAgent 验证失败'
        """
        super().__init__(message)