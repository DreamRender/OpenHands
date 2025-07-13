from enum import Enum


class ActionType(str, Enum):
    """Action类型枚举类
    
    定义了系统中所有可能的Action类型。Action代表Agent可以执行的各种操作，
    包括文件操作、命令执行、任务管理、浏览器交互等。
    
    继承自str和Enum，使得枚举值可以直接作为字符串使用，便于序列化和传输。
    """

    MESSAGE = 'message'
    """表示一个消息Action
    
    用于Agent发送消息给用户或其他组件的Action类型。
    通常包含文本内容，用于交流和状态报告。
    """

    SYSTEM = 'system'
    """表示系统消息Action
    
    用于系统级别的消息传递，通常包含系统状态、错误信息或重要通知。
    与普通MESSAGE的区别在于这是系统内部产生的消息。
    """

    START = 'start'
    """启动新的开发任务或发送用户聊天消息的Action
    
    只能由客户端发送。用于：
    1. 启动一个新的开发任务
    2. 用户发送聊天消息给Agent
    这是任务生命周期的起始点。
    """

    READ = 'read'
    """读取文件内容的Action
    
    用于从文件系统中读取指定文件的内容。
    通常需要指定文件路径作为参数。
    """

    WRITE = 'write'
    """写入内容到文件的Action
    
    用于将指定内容写入到文件系统中的文件。
    通常需要指定文件路径和要写入的内容。
    """

    EDIT = 'edit'
    """通过提供草稿来编辑文件的Action
    
    用于修改现有文件的内容。与WRITE不同，EDIT通常是基于现有内容的修改，
    而不是完全重写文件。
    """

    RUN = 'run'
    """运行命令的Action
    
    用于在系统中执行shell命令或其他可执行程序。
    通常需要指定要执行的命令和参数。
    """

    RUN_IPYTHON = 'run_ipython'
    """运行IPython单元格的Action
    
    用于在IPython环境中执行Python代码。
    适用于数据分析、机器学习等需要交互式Python环境的场景。
    """

    BROWSE = 'browse'
    """打开网页的Action
    
    用于在浏览器中打开指定的网页URL。
    通常用于获取网页内容或进行网络搜索。
    """

    BROWSE_INTERACTIVE = 'browse_interactive'
    """与浏览器实例进行交互的Action
    
    用于在已打开的浏览器中进行交互操作，如点击、输入、滚动等。
    比普通的BROWSE更高级，支持动态交互。
    """

    MCP = 'call_tool_mcp'
    """与MCP服务器交互的Action
    
    MCP (Model Context Protocol) 是一种协议，用于与外部工具和服务集成。
    此Action用于调用MCP服务器提供的各种工具和功能。
    """

    DELEGATE = 'delegate'
    """将任务委托给另一个Agent的Action
    
    用于将当前任务或子任务分配给其他Agent执行。
    支持分布式任务处理和专业化Agent的协作。
    """

    THINK = 'think'
    """记录思考过程的Action
    
    用于Agent记录其思考过程、推理步骤或决策逻辑。
    有助于调试和理解Agent的行为模式。
    """

    FINISH = 'finish'
    """完成任务的Action
    
    当Agent确信已经完成了分配的任务并且已经测试了工作成果时，
    使用此Action来停止工作。这标志着任务的成功完成。
    """

    REJECT = 'reject'
    """拒绝任务的Action
    
    当Agent确信无法在给定的要求下完成任务时，
    使用此Action来停止工作。这表示任务无法完成。
    """

    NULL = 'null'
    """空Action类型
    
    表示无操作或占位符Action。通常用于系统内部处理或测试场景。
    """

    PAUSE = 'pause'
    """暂停任务的Action
    
    用于临时暂停当前正在执行的任务。
    任务可以通过RESUME Action重新恢复执行。
    """

    RESUME = 'resume'
    """恢复任务的Action
    
    用于恢复之前被暂停的任务。
    与PAUSE Action配对使用，支持任务的暂停和恢复功能。
    """

    STOP = 'stop'
    """停止任务的Action
    
    用于完全停止当前任务。与PAUSE不同，STOP后需要发送START Action才能重新开始新任务。
    这是任务的终止状态。
    """

    CHANGE_AGENT_STATE = 'change_agent_state'
    """改变Agent状态的Action
    
    用于修改Agent的当前状态。Agent的状态变化会影响其行为和可用操作。
    """

    PUSH = 'push'
    """推送分支到GitHub的Action
    
    用于将本地代码分支推送到GitHub仓库。
    通常在代码开发完成后使用，为后续的PR创建做准备。
    """

    SEND_PR = 'send_pr'
    """向GitHub发送Pull Request的Action
    
    用于在GitHub上创建Pull Request，将开发的功能合并到主分支。
    这是代码协作和版本控制流程中的重要步骤。
    """

    RECALL = 'recall'
    """从用户Workspace、MicroAgent或其他来源检索内容的Action
    
    用于获取历史信息、上下文数据或之前的工作成果。
    支持从多种来源检索信息，包括：
    - 用户Workspace的历史数据
    - MicroAgent的执行结果
    - 其他外部数据源
    """

    CONDENSATION = 'condensation'
    """将事件列表压缩为摘要的Action
    
    用于将一系列事件或操作压缩成简洁的摘要信息。
    有助于减少信息冗余，提高处理效率。
    """

    CONDENSATION_REQUEST = 'condensation_request'
    """请求对事件列表进行压缩的Action
    
    用于请求系统对指定的事件列表进行压缩处理。
    这是Condenser功能的触发Action。
    """