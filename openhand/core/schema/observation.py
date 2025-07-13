from enum import Enum


class ObservationType(str, Enum):
    """Observation类型枚举类
    
    定义了Agent执行Action后产生的所有可能的Observation类型。Observation代表Agent对环境的感知结果，
    是Action-Observation循环中的重要组成部分。每个Action通常会产生对应的Observation。
    
    继承自str和Enum，使得枚举值可以直接作为字符串使用，便于序列化和网络传输。
    """

    read = 'read'
    """文件读取操作的Observation
    
    对应READ Action的执行结果。包含从文件系统中读取的文件内容。
    通常包含文件的完整内容或错误信息。
    """

    WRITE = 'write'
    """文件写入操作的Observation
    
    对应WRITE Action的执行结果。表示文件写入操作的成功或失败状态。
    通常包含写入是否成功的确认信息。
    """

    EDIT = 'edit'
    """文件编辑操作的Observation
    
    对应EDIT Action的执行结果。表示文件编辑操作的结果和状态。
    可能包含编辑后的文件内容或编辑操作的确认信息。
    """

    BROWSE = 'browse'
    """网页浏览操作的Observation
    
    对应BROWSE Action的执行结果。包含从指定URL获取的HTML内容。
    通常包含网页的完整HTML源代码或加载错误信息。
    """

    RUN = 'run'
    """命令执行操作的Observation
    
    对应RUN Action的执行结果。包含命令执行后的输出信息。
    通常包含标准输出、错误输出和退出码。
    """

    RUN_IPYTHON = 'run_ipython'
    """IPython单元格执行的Observation
    
    对应RUN_IPYTHON Action的执行结果。包含IPython代码执行后的输出。
    通常包含执行结果、输出图表或异常信息。
    """

    CHAT = 'chat'
    """来自用户的消息Observation
    
    表示从用户接收到的聊天消息。这是用户与Agent交互的主要方式。
    包含用户发送的文本内容和相关Metadata。
    """

    DELEGATE = 'delegate'
    """任务委托的结果Observation
    
    对应DELEGATE Action的执行结果。包含委托给其他Agent的任务的执行结果。
    通常包含子任务的完成状态和返回的数据。
    """

    MESSAGE = 'message'
    """消息类型的Observation
    
    表示系统内部或Agent之间传递的消息。
    用于记录重要的状态变化或通知信息。
    """

    ERROR = 'error'
    """错误类型的Observation
    
    表示操作执行过程中发生的错误。包含错误详情、错误类型和可能的解决建议。
    这是系统异常处理的重要组成部分。
    """

    SUCCESS = 'success'
    """成功类型的Observation
    
    表示操作成功完成的确认信息。通常用于标识重要操作的成功状态。
    可能包含成功的详细信息和相关数据。
    """

    NULL = 'null'
    """空Observation类型
    
    表示无内容或占位符Observation。通常用于系统内部处理或测试场景。
    """

    THINK = 'think'
    """思考过程的Observation
    
    对应THINK Action的结果。记录Agent的思考过程、推理步骤或决策逻辑。
    有助于理解Agent的行为模式和调试系统问题。
    """

    AGENT_STATE_CHANGED = 'agent_state_changed'
    """Agent状态变化的Observation
    
    表示Agent状态发生了变化。记录状态变化的详细信息，包括：
    - 之前的状态
    - 新的状态  
    - 状态变化的原因
    - 状态变化的时间戳
    """

    USER_REJECTED = 'user_rejected'
    """用户拒绝操作的Observation
    
    表示用户拒绝了Agent提议的某个操作或决策。
    包含用户拒绝的具体内容和可能的拒绝原因。
    """

    CONDENSE = 'condense'
    """压缩操作的结果Observation
    
    对应Condenser功能的执行结果。包含事件列表压缩后的摘要信息。
    有助于减少信息冗余，提高系统处理效率。
    """

    RECALL = 'recall'
    """回忆操作的结果Observation
    
    对应RECALL Action的执行结果。可以是Workspace上下文、MicroAgent信息或其他类型的历史信息。
    支持从多种来源检索的信息类型，包括：
    - Workspace的历史数据
    - MicroAgent的执行记录
    - 其他外部数据源的信息
    """

    MCP = 'mcp'
    """MCP服务器操作的结果Observation
    
    对应MCP Action的执行结果。包含与MCP服务器交互后获得的数据和状态信息。
    MCP (Model Context Protocol) 用于与外部工具和服务的集成。
    """

    DOWNLOAD = 'download'
    """通过浏览器下载/打开文件的结果Observation
    
    表示通过浏览器成功下载或打开文件的结果。包含文件的相关信息，如：
    - 文件名称
    - 文件大小
    - 文件类型
    - 下载状态
    这通常与浏览器的文件操作功能相关。
    """