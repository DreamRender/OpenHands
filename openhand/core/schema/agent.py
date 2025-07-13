from enum import Enum


class AgentState(str, Enum):
    """Agent状态枚举类
    
    定义了Agent在执行任务过程中所有可能的状态。这些状态反映了Agent的当前工作状态，
    用于状态管理、流程控制和用户界面展示。
    
    继承自str和Enum，使得枚举值可以直接作为字符串使用，便于序列化和状态传递。
    """

    LOADING = 'loading'
    """Agent正在加载中的状态
    
    表示Agent正在初始化、加载配置或准备执行任务。
    这是Agent启动过程中的临时状态。
    """

    RUNNING = 'running'
    """Agent正在运行中的状态
    
    表示Agent正在活跃地执行任务，处理Action并产生Observation。
    这是Agent的主要工作状态。
    """

    AWAITING_USER_INPUT = 'awaiting_user_input'
    """Agent等待用户输入的状态
    
    表示Agent需要用户提供额外信息或指令才能继续执行任务。
    在此状态下，Agent会暂停执行直到收到用户响应。
    """

    PAUSED = 'paused'
    """Agent已暂停的状态
    
    表示Agent的执行已被暂停，但可以随时恢复。
    通常由用户主动触发或系统调度决定。
    """

    STOPPED = 'stopped'
    """Agent已停止的状态
    
    表示Agent已完全停止执行当前任务。
    与PAUSED不同，STOPPED状态需要重新启动才能继续工作。
    """

    FINISHED = 'finished'
    """Agent已完成当前任务的状态
    
    表示Agent成功完成了分配的任务。
    这是任务成功完成的终止状态。
    """

    REJECTED = 'rejected'
    """Agent拒绝任务的状态
    
    表示Agent因为某些原因（如任务不可行、资源不足等）拒绝执行任务。
    这是任务被拒绝的终止状态。
    """

    ERROR = 'error'
    """任务执行过程中发生错误的状态
    
    表示Agent在执行任务时遇到了无法处理的错误。
    这通常是系统异常或严重错误导致的状态。
    """

    AWAITING_USER_CONFIRMATION = 'awaiting_user_confirmation'
    """Agent等待用户确认的状态
    
    表示Agent需要用户确认某个操作或决策才能继续执行。
    这通常发生在执行重要或潜在风险操作之前。
    """

    USER_CONFIRMED = 'user_confirmed'
    """用户已确认Agent操作的状态
    
    表示用户已经确认了Agent的提议或操作。
    Agent可以基于用户的确认继续执行后续步骤。
    """

    USER_REJECTED = 'user_rejected'
    """用户拒绝了Agent操作的状态
    
    表示用户拒绝了Agent提议的操作或决策。
    Agent需要根据用户的拒绝调整执行策略。
    """

    RATE_LIMITED = 'rate_limited'
    """Agent因速率限制而受限的状态
    
    表示Agent因为API调用频率过高或资源使用超限而被暂时限制。
    在此状态下，Agent需要等待限制解除后才能继续执行。
    """