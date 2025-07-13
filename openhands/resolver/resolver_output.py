from typing import Any

from litellm import BaseModel

from openhands.resolver.interfaces.issue import Issue


class ResolverOutput(BaseModel):
    """Resolver输出结果模型
    
    这个类定义了Issue解决器的输出结果结构，包含了
    Issue解决过程中的所有重要信息和最终结果。
    
    继承自litellm的BaseModel，提供了数据验证和序列化功能。
    
    注意：用户指定的字段
    NOTE: User-specified
    """
    
    # Issue相关信息
    issue: Issue
    """要解决的Issue对象，包含Issue的详细信息"""
    
    issue_type: str
    """Issue类型，可以是'issue'或'pr'"""
    
    # 指令和配置信息
    instruction: str
    """传递给Agent的指令内容"""
    
    base_commit: str
    """基础提交的哈希值，用于生成差异比较"""
    
    # 输出结果
    git_patch: str
    """生成的git补丁内容，包含所有代码更改"""
    
    history: list[dict[str, Any]]
    """Agent执行过程中的历史记录，包含所有Action和Observation"""
    
    metrics: dict[str, Any] | None
    """性能指标数据，如果有的话"""
    
    # 成功状态
    success: bool
    """整体成功状态，表示Issue是否被成功解决"""
    
    comment_success: list[bool] | None
    """评论级别的成功状态列表，主要用于PR评论的逐项解决状态"""
    
    result_explanation: str
    """结果解释说明，描述解决的具体内容或失败原因"""
    
    error: str | None
    """错误信息，如果解决过程中出现错误的话"""
