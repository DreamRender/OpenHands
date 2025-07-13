"""
对话存储路径定义模块

该模块定义了对话相关文件在存储系统中的路径结构和命名规则。
包括对话目录、事件文件、元数据文件、初始化数据文件和Agent状态文件的路径生成函数。
"""

# 对话数据的基础目录名称
CONVERSATION_BASE_DIR = 'sessions'


def get_conversation_dir(sid: str, user_id: str | None = None) -> str:
    """
    获取指定对话的根目录路径
    
    根据是否提供用户ID来决定目录结构：
    - 有用户ID时：users/{user_id}/conversations/{sid}/
    - 无用户ID时：sessions/{sid}/
    
    Args:
        sid (str): Session ID，对话的唯一标识符
        user_id (str | None): 用户ID，可选参数
        
    Returns:
        str: 对话根目录的路径字符串
    """
    if user_id:
        # 多用户模式：在用户目录下创建对话目录
        return f'users/{user_id}/conversations/{sid}/'
    else:
        # 单用户模式：直接在基础目录下创建对话目录
        return f'{CONVERSATION_BASE_DIR}/{sid}/'


def get_conversation_events_dir(sid: str, user_id: str | None = None) -> str:
    """
    获取指定对话的事件存储目录路径
    
    事件目录用于存储对话过程中发生的各种事件文件。
    
    Args:
        sid (str): Session ID，对话的唯一标识符
        user_id (str | None): 用户ID，可选参数
        
    Returns:
        str: 对话事件目录的路径字符串
    """
    # 在对话根目录下创建events子目录
    return f'{get_conversation_dir(sid, user_id)}events/'


def get_conversation_event_filename(
    sid: str, id: int, user_id: str | None = None
) -> str:
    """
    获取指定对话中特定事件的文件路径
    
    每个事件都有一个唯一的ID，对应一个JSON文件。
    
    Args:
        sid (str): Session ID，对话的唯一标识符
        id (int): 事件的唯一标识符
        user_id (str | None): 用户ID，可选参数
        
    Returns:
        str: 事件文件的完整路径字符串
    """
    # 在事件目录下创建以事件ID命名的JSON文件
    return f'{get_conversation_events_dir(sid, user_id)}{id}.json'


def get_conversation_metadata_filename(sid: str, user_id: str | None = None) -> str:
    """
    获取指定对话的元数据文件路径
    
    元数据文件存储对话的基本信息和配置参数。
    
    Args:
        sid (str): Session ID，对话的唯一标识符
        user_id (str | None): 用户ID，可选参数
        
    Returns:
        str: 元数据文件的完整路径字符串
    """
    # 在对话根目录下创建metadata.json文件
    return f'{get_conversation_dir(sid, user_id)}metadata.json'


def get_conversation_init_data_filename(sid: str, user_id: str | None = None) -> str:
    """
    获取指定对话的初始化数据文件路径
    
    初始化数据文件存储对话开始时的初始配置和参数。
    
    Args:
        sid (str): Session ID，对话的唯一标识符
        user_id (str | None): 用户ID，可选参数
        
    Returns:
        str: 初始化数据文件的完整路径字符串
    """
    # 在对话根目录下创建init.json文件
    return f'{get_conversation_dir(sid, user_id)}init.json'


def get_conversation_agent_state_filename(sid: str, user_id: str | None = None) -> str:
    """
    获取指定对话的Agent状态文件路径
    
    Agent状态文件以pickle格式存储Agent的运行时状态信息。
    
    Args:
        sid (str): Session ID，对话的唯一标识符
        user_id (str | None): 用户ID，可选参数
        
    Returns:
        str: Agent状态文件的完整路径字符串
    """
    # 在对话根目录下创建agent_state.pkl文件
    return f'{get_conversation_dir(sid, user_id)}agent_state.pkl'