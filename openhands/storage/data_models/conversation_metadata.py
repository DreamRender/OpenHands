from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from openhands.integrations.service_types import ProviderType


class ConversationTrigger(Enum):
    """对话触发方式的枚举。
    
    定义了启动对话的不同方式，用于跟踪对话的来源。
    """
    RESOLVER = 'resolver'          # 通过解析器触发的对话
    GUI = 'gui'                    # 通过图形界面触发的对话
    SUGGESTED_TASK = 'suggested_task'  # 通过建议任务触发的对话
    REMOTE_API_KEY = 'openhands_api'   # 通过远程API密钥触发的对话
    SLACK = 'slack'                # 通过Slack集成触发的对话


@dataclass
class ConversationMetadata:
    """对话metadata的数据模型。
    
    包含对话的所有元信息，如用户信息、Repository设置、计费信息等。
    用于跟踪和管理对话的状态和配置。
    """
    
    conversation_id: str                            # 对话的唯一标识符
    selected_repository: str | None                 # 选中的Repository名称，可能为None
    user_id: str | None = None                     # 用户的唯一标识符，可能为None
    selected_branch: str | None = None             # 选中的Git分支名称，可能为None
    git_provider: ProviderType | None = None       # Git服务提供商类型（如GitHub、GitLab等）
    title: str | None = None                       # 对话的标题，可能为None
    last_updated_at: datetime | None = None        # 最后更新时间，可能为None
    trigger: ConversationTrigger | None = None     # 对话的触发方式，可能为None
    pr_number: list[int] = field(default_factory=list)  # 关联的Pull Request编号列表
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))  # 创建时间，默认为当前UTC时间
    llm_model: str | None = None                   # 使用的LLM模型名称，可能为None
    
    # 成本和token指标相关字段
    accumulated_cost: float = 0.0                  # 累计费用，默认为0.0
    prompt_tokens: int = 0                         # 提示词token数量，默认为0
    completion_tokens: int = 0                     # 完成词token数量，默认为0
    total_tokens: int = 0                          # 总token数量，默认为0