from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class ReviewThread(BaseModel):
    """
    审查线程数据模型
    
    用于表示代码审查过程中的讨论线程，包含评论内容和相关文件信息。
    
    Attributes:
        comment (str): 线程中的评论内容
        files (list[str]): 与此线程相关的文件路径列表
    """
    comment: str
    files: list[str]


class Issue(BaseModel):
    """
    Issue数据模型
    
    统一的Issue/Pull Request数据结构，用于表示来自不同Git平台
    （GitHub、GitLab、Bitbucket）的Issue或Pull Request信息。
    
    Attributes:
        owner (str): Repository的拥有者
        repo (str): Repository名称
        number (int): Issue或Pull Request的编号
        title (str): 标题
        body (str): 主体内容描述
        thread_comments (list[str] | None): Issue线程评论列表，可选字段
        closing_issues (list[str] | None): 相关的将被关闭的Issue列表，可选字段
        review_comments (list[str] | None): 审查评论列表，可选字段
        review_threads (list[ReviewThread] | None): 审查线程列表，可选字段  
        thread_ids (list[str] | None): 线程ID列表，可选字段
        head_branch (str | None): 头分支名称（用于Pull Request），可选字段
        base_branch (str | None): 基础分支名称（用于Pull Request），可选字段
    """
    owner: str
    repo: str
    number: int
    title: str
    body: str
    thread_comments: list[str] | None = None  # Issue线程评论字段
    closing_issues: list[str] | None = None
    review_comments: list[str] | None = None
    review_threads: list[ReviewThread] | None = None
    thread_ids: list[str] | None = None
    head_branch: str | None = None
    base_branch: str | None = None


class IssueHandlerInterface(ABC):
    """
    Issue处理器接口
    
    定义了处理不同Git平台（GitHub、GitLab、Bitbucket）Issue和Pull Request
    操作的统一接口。所有具体的平台处理器都必须实现此接口。
    
    该接口采用抽象基类模式，确保所有实现类都提供一致的API。
    """

    @abstractmethod
    def set_owner(self, owner: str) -> None:
        """
        设置Repository的拥有者
        
        Args:
            owner: 新的拥有者名称
        """
        pass

    @abstractmethod
    def download_issues(self) -> list[Any]:
        """
        下载Repository中的所有Issues
        
        Returns:
            Issues列表，具体格式取决于平台实现
        """
        pass

    @abstractmethod
    def get_issue_comments(
        self, issue_number: int, comment_id: int | None = None
    ) -> list[str] | None:
        """
        获取Issue的评论
        
        Args:
            issue_number: Issue编号
            comment_id: 可选的特定评论ID
            
        Returns:
            评论内容列表，如果没有评论则返回None
        """
        pass

    @abstractmethod
    def get_base_url(self) -> str:
        """
        获取API的基础URL
        
        Returns:
            API基础URL字符串
        """
        pass

    @abstractmethod
    def get_branch_url(self, branch_name: str) -> str:
        """
        获取分支的URL
        
        Args:
            branch_name: 分支名称
            
        Returns:
            分支的URL字符串
        """
        pass

    @abstractmethod
    def get_download_url(self) -> str:
        """
        获取下载URL
        
        Returns:
            下载URL字符串
        """
        pass

    @abstractmethod
    def get_clone_url(self) -> str:
        """
        获取Repository的克隆URL
        
        Returns:
            包含认证信息的Git克隆URL
        """
        pass

    @abstractmethod
    def get_pull_url(self, pr_number: int) -> str:
        """
        获取Pull Request的URL
        
        Args:
            pr_number: Pull Request编号
            
        Returns:
            Pull Request的Web页面URL
        """
        pass

    @abstractmethod
    def get_graphql_url(self) -> str:
        """
        获取GraphQL API的URL
        
        Returns:
            GraphQL API的URL字符串
        """
        pass

    @abstractmethod
    def get_headers(self) -> dict[str, str]:
        """
        获取HTTP请求头
        
        Returns:
            包含认证信息的HTTP请求头字典
        """
        pass

    @abstractmethod
    def get_compare_url(self, branch_name: str) -> str:
        """
        获取分支比较的URL
        
        Args:
            branch_name: 要比较的分支名称
            
        Returns:
            分支比较页面的URL
        """
        pass

    @abstractmethod
    def get_branch_name(self, base_branch_name: str) -> str:
        """
        生成唯一的分支名称
        
        Args:
            base_branch_name: 基础分支名称
            
        Returns:
            可用的唯一分支名称
        """
        pass

    @abstractmethod
    def get_default_branch_name(self) -> str:
        """
        获取Repository的默认分支名称
        
        Returns:
            默认分支名称字符串
        """
        pass

    @abstractmethod
    def branch_exists(self, branch_name: str) -> bool:
        """
        检查分支是否存在
        
        Args:
            branch_name: 要检查的分支名称
            
        Returns:
            如果分支存在返回True，否则返回False
        """
        pass

    @abstractmethod
    def reply_to_comment(self, pr_number: int, comment_id: str, reply: str) -> None:
        """
        回复Pull Request中的评论
        
        Args:
            pr_number: Pull Request编号
            comment_id: 要回复的评论ID
            reply: 回复内容
        """
        pass

    @abstractmethod
    def send_comment_msg(self, issue_number: int, msg: str) -> None:
        """
        向Issue或Pull Request发送评论消息
        
        Args:
            issue_number: Issue或Pull Request编号
            msg: 评论消息内容
        """
        pass

    @abstractmethod
    def get_authorize_url(self) -> str:
        """
        获取授权URL
        
        Returns:
            包含认证信息的授权URL
        """
        pass

    @abstractmethod
    def create_pull_request(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        创建Pull Request
        
        Args:
            data: Pull Request数据字典，如果为None则使用空字典
            
        Returns:
            创建的Pull Request信息字典
            
        Raises:
            NotImplementedError: 如果子类未实现此方法
        """
        if data is None:
            data = {}
        raise NotImplementedError

    @abstractmethod
    def request_reviewers(self, reviewer: str, pr_number: int) -> None:
        """
        为Pull Request请求审查者
        
        Args:
            reviewer: 审查者用户名
            pr_number: Pull Request编号
        """
        pass

    @abstractmethod
    def get_context_from_external_issues_references(
        self,
        closing_issues: list[str],
        closing_issue_numbers: list[int],
        issue_body: str,
        review_comments: list[str] | None,
        review_threads: list[ReviewThread],
        thread_comments: list[str] | None,
    ) -> list[str]:
        """
        从外部Issue引用中获取上下文信息
        
        解析各种文本内容中的Issue引用，获取相关Issue的详细信息，
        并将其添加到closing_issues列表中，用于提供更完整的上下文。
        
        Args:
            closing_issues: 关闭的Issue列表（将被修改）
            closing_issue_numbers: 关闭的Issue编号列表
            issue_body: Issue主体内容
            review_comments: 审查评论列表，可选
            review_threads: 审查线程列表
            thread_comments: 线程评论列表，可选
            
        Returns:
            更新后的closing_issues列表
        """
        pass

    @abstractmethod
    def get_converted_issues(
        self, issue_numbers: list[int] | None = None, comment_id: int | None = None
    ) -> list[Issue]:
        """
        从Git Provider（GitHub、GitLab或Bitbucket）下载Issues
        
        将平台特定的Issue数据转换为统一的Issue对象格式。
        
        Args:
            issue_numbers: 要下载的Issue编号列表，可选
            comment_id: 特定评论ID，可选
            
        Returns:
            转换后的Issue对象列表
        """
        pass