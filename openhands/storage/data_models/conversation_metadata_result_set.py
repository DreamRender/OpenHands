from dataclasses import dataclass, field

from openhands.storage.data_models.conversation_metadata import ConversationMetadata


@dataclass
class ConversationMetadataResultSet:
    """对话metadata搜索结果集的数据模型。
    
    用于封装分页搜索的结果，包含当前页的结果列表和下一页的标识符。
    支持分页查询，便于处理大量对话数据。
    """
    
    results: list[ConversationMetadata] = field(default_factory=list)  # 当前页的对话metadata列表，默认为空列表
    next_page_id: str | None = None                                     # 下一页的标识符，如果没有下一页则为None