from enum import Enum


class ConversationStatus(Enum):
    """对话状态的枚举。
    
    定义了对话在其生命周期中可能的状态，用于跟踪对话的执行状态。
    """
    
    STARTING = 'STARTING'  # 对话正在启动中，初始化阶段
    RUNNING = 'RUNNING'    # 对话正在运行中，活跃状态
    STOPPED = 'STOPPED'    # 对话已停止，终止状态