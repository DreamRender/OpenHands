import base64
from typing import Any

import httpx

from openhands.core.logger import openhands_logger as logger
from openhands.resolver.interfaces.issue import (
    Issue,
    IssueHandlerInterface,
    ReviewThread,
)
from openhands.resolver.utils import extract_issue_references


class BitbucketIssueHandler(IssueHandlerInterface):
    """
    Bitbucket Issue处理器类
    
    该类实现了IssueHandlerInterface接口，用于处理Bitbucket平台的Issue相关操作，
    包括获取Issue信息、创建Pull Request、发送评论等功能。
    
    Attributes:
        owner (str): Repository的拥有者（workspace）
        repo (str): Repository名称
        token (str): Bitbucket API访问token
        username (str | None): 可选的Bitbucket用户名
        base_domain (str): Bitbucket服务器域名，默认为'bitbucket.org'
        base_url (str): API基础URL
        download_url (str): 下载URL
        clone_url (str): 克隆URL
        headers (dict[str, str]): HTTP请求头
    """
    
    def __init__(
        self,
        owner: str,
        repo: str,
        token: str,
        username: str | None = None,
        base_domain: str = 'bitbucket.org',
    ):
        """
        初始化Bitbucket Issue处理器
        
        Args:
            owner: Repository的workspace
            repo: Repository名称
            token: Bitbucket API token
            username: 可选的Bitbucket用户名
            base_domain: Bitbucket服务器域名（默认值: "bitbucket.org"）
        """
        self.owner = owner
        self.repo = repo
        self.token = token
        self.username = username
        self.base_domain = base_domain
        # 初始化各种URL和请求头
        self.base_url = self.get_base_url()
        self.download_url = self.get_download_url()
        self.clone_url = self.get_clone_url()
        self.headers = self.get_headers()

    def set_owner(self, owner: str) -> None:
        """
        设置Repository的拥有者
        
        Args:
            owner: 新的拥有者名称
        """
        self.owner = owner

    def get_headers(self) -> dict[str, str]:
        """
        获取HTTP请求头
        
        根据token格式自动选择认证方式：
        - 如果token包含冒号，则使用Basic认证（用户名:密码格式）
        - 否则使用Bearer token认证
        
        Returns:
            包含认证信息的HTTP请求头字典
        """
        # 检查token是否包含冒号，这表示它是username:password格式
        if ':' in self.token:
            # 将token进行base64编码用于Basic认证
            auth_str = base64.b64encode(self.token.encode()).decode()
            return {
                'Authorization': f'Basic {auth_str}',
                'Accept': 'application/json',
            }
        else:
            # 使用Bearer token认证
            return {
                'Authorization': f'Bearer {self.token}',
                'Accept': 'application/json',
            }

    def get_base_url(self) -> str:
        """
        获取Bitbucket API的基础URL
        
        Returns:
            API基础URL字符串
        """
        return f'https://api.{self.base_domain}/2.0'

    def get_download_url(self) -> str:
        """
        获取Repository的下载URL
        
        Returns:
            Repository下载URL（主分支的zip文件）
        """
        return f'https://{self.base_domain}/{self.owner}/{self.repo}/get/master.zip'

    def get_clone_url(self) -> str:
        """
        获取Repository的克隆URL
        
        Returns:
            Git克隆URL字符串
        """
        return f'https://{self.base_domain}/{self.owner}/{self.repo}.git'

    def get_repo_url(self) -> str:
        """
        获取Repository的Web URL
        
        Returns:
            Repository的Web页面URL
        """
        return f'https://{self.base_domain}/{self.owner}/{self.repo}'

    def get_issue_url(self, issue_number: int) -> str:
        """
        获取指定Issue的URL
        
        Args:
            issue_number: Issue编号
            
        Returns:
            Issue的Web页面URL
        """
        return f'{self.get_repo_url()}/issues/{issue_number}'

    def get_pr_url(self, pr_number: int) -> str:
        """
        获取指定Pull Request的URL
        
        Args:
            pr_number: Pull Request编号
            
        Returns:
            Pull Request的Web页面URL
        """
        return f'{self.get_repo_url()}/pull-requests/{pr_number}'

    async def get_issue(self, issue_number: int) -> Issue:
        """
        从Bitbucket获取指定的Issue
        
        Args:
            issue_number: 要获取的Issue编号
        
        Returns:
            包含Issue信息的Issue对象
        """
        # 构建API请求URL
        url = f'{self.base_url}/repositories/{self.owner}/{self.repo}/issues/{issue_number}'
        
        # 使用异步HTTP客户端发送请求
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()  # 如果请求失败则抛出异常
            data = response.json()

        # 创建基础的Issue对象，包含必需字段
        issue = Issue(
            owner=self.owner,
            repo=self.repo,
            number=data.get('id'),
            title=data.get('title', ''),
            body=data.get('content', {}).get('raw', ''),  # 获取原始内容
        )

        return issue

    def create_pr(
        self,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> str:
        """
        创建Pull Request
        
        Args:
            title: Pull Request标题
            body: Pull Request描述
            head: 源分支名称
            base: 目标分支名称
        
        Returns:
            创建的Pull Request的URL
        """
        # 构建API请求URL
        url = f'{self.base_url}/repositories/{self.owner}/{self.repo}/pullrequests'

        # 构建请求payload
        payload = {
            'title': title,
            'description': body,
            'source': {'branch': {'name': head}},       # 源分支配置
            'destination': {'branch': {'name': base}},  # 目标分支配置
            'close_source_branch': False,               # 不自动关闭源分支
        }

        # 发送POST请求创建Pull Request
        response = httpx.post(url, headers=self.headers, json=payload)
        response.raise_for_status()
        data = response.json()

        # 返回Pull Request的HTML URL
        return data.get('links', {}).get('html', {}).get('href', '')

    def download_issues(self) -> list[Any]:
        """
        下载Repository中的所有Issue
        
        注意：此方法尚未实现
        
        Returns:
            Issue列表（目前返回空列表）
        """
        logger.warning('BitbucketIssueHandler.download_issues not implemented')
        return []

    def get_issue_comments(
        self, issue_number: int, comment_id: int | None = None
    ) -> list[str] | None:
        """
        获取Issue的评论
        
        Args:
            issue_number: Issue编号
            comment_id: 可选的评论ID
        
        Returns:
            评论列表（目前返回空列表，此方法尚未实现）
        """
        logger.warning('BitbucketIssueHandler.get_issue_comments not implemented')
        return []

    def get_branch_url(self, branch_name: str) -> str:
        """
        获取分支的URL
        
        Args:
            branch_name: 分支名称
        
        Returns:
            分支的Web页面URL
        """
        return (
            f'https://{self.base_domain}/{self.owner}/{self.repo}/branch/{branch_name}'
        )

    def get_compare_url(self, branch_name: str) -> str:
        """
        获取分支比较的URL
        
        Args:
            branch_name: 要比较的分支名称
        
        Returns:
            分支比较页面的URL（与master分支比较）
        """
        return f'https://{self.base_domain}/{self.owner}/{self.repo}/compare/master...{branch_name}'

    def get_authorize_url(self) -> str:
        """
        获取授权URL
        
        Returns:
            包含OAuth token的授权URL
        """
        return f'https://oauth2:{self.token}@{self.base_domain}/'

    def get_pull_url(self, pr_number: int) -> str:
        """
        获取Pull Request的URL
        
        Args:
            pr_number: Pull Request编号
        
        Returns:
            Pull Request的Web页面URL
        """
        return f'https://{self.base_domain}/{self.owner}/{self.repo}/pull-requests/{pr_number}'

    def get_branch_name(self, base_branch_name: str) -> str:
        """
        生成唯一的分支名称
        
        Args:
            base_branch_name: 基础分支名称
        
        Returns:
            添加了拥有者后缀的唯一分支名称
        """
        return f'{base_branch_name}-{self.owner}'

    def branch_exists(self, branch_name: str) -> bool:
        """
        检查分支是否存在
        
        Args:
            branch_name: 要检查的分支名称
        
        Returns:
            如果分支存在返回True，否则返回False
            注意：此方法尚未实现，总是返回False
        """
        logger.warning('BitbucketIssueHandler.branch_exists not implemented')
        return False

    def get_default_branch_name(self) -> str:
        """
        获取默认分支名称
        
        Returns:
            默认分支名称（'master'）
        """
        return 'master'

    def create_pull_request(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        创建Pull Request
        
        Args:
            data: Pull Request数据字典，包含title、description、source_branch、target_branch等
        
        Returns:
            包含创建的Pull Request信息的字典，包括html_url和number字段
        """
        if data is None:
            data = {}

        # 从数据中提取各个字段
        title = data.get('title', '')
        description = data.get('description', '')
        source_branch = data.get('source_branch', '')
        target_branch = data.get('target_branch', '')

        # 构建API请求URL
        url = f'{self.base_url}/repositories/{self.owner}/{self.repo}/pullrequests'

        # 构建请求payload
        payload = {
            'title': title,
            'description': description,
            'source': {'branch': {'name': source_branch}},
            'destination': {'branch': {'name': target_branch}},
            'close_source_branch': False,
        }

        # 发送POST请求
        response = httpx.post(url, headers=self.headers, json=payload)
        response.raise_for_status()
        data = response.json()

        # 确保data不为None后访问其内容
        if data is None:
            data = {}

        # 返回标准化的响应格式
        return {
            'html_url': data.get('links', {}).get('html', {}).get('href', ''),
            'number': data.get('id', 0),
        }

    def request_reviewers(self, reviewer: str, pr_number: int) -> None:
        """
        为Pull Request请求审查者
        
        Args:
            reviewer: 审查者用户名
            pr_number: Pull Request编号
            
        注意：此方法尚未实现
        """
        logger.warning('BitbucketIssueHandler.request_reviewers not implemented')

    def send_comment_msg(self, issue_number: int, msg: str) -> None:
        """
        向Issue发送评论消息
        
        Args:
            issue_number: Issue编号
            msg: 评论消息内容
        """
        # 构建评论API的URL
        url = f'{self.base_url}/repositories/{self.owner}/{self.repo}/pullrequests/{issue_number}/comments'

        # 构建评论payload
        payload = {'content': {'raw': msg}}

        # 发送POST请求创建评论
        response = httpx.post(url, headers=self.headers, json=payload)
        response.raise_for_status()

    def get_issue_thread_comments(self, issue_number: int) -> list[str]:
        """
        获取Issue的线程评论
        
        Args:
            issue_number: Issue编号
        
        Returns:
            线程评论列表（目前返回空列表，此方法尚未实现）
        """
        logger.warning(
            'BitbucketIssueHandler.get_issue_thread_comments not implemented'
        )
        return []

    def get_issue_review_comments(self, issue_number: int) -> list[str]:
        """
        获取Issue的审查评论
        
        Args:
            issue_number: Issue编号
        
        Returns:
            审查评论列表（目前返回空列表，此方法尚未实现）
        """
        logger.warning(
            'BitbucketIssueHandler.get_issue_review_comments not implemented'
        )
        return []

    def get_issue_review_threads(self, issue_number: int) -> list[ReviewThread]:
        """
        获取Issue的审查线程
        
        Args:
            issue_number: Issue编号
        
        Returns:
            审查线程列表（目前返回空列表，此方法尚未实现）
        """
        logger.warning('BitbucketIssueHandler.get_issue_review_threads not implemented')
        return []

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
        
        该方法解析各种文本内容中的Issue引用，获取相关Issue的详细信息，
        并将其添加到closing_issues列表中，用于提供更完整的上下文。
        
        Args:
            closing_issues: 关闭的Issue列表（将被修改）
            closing_issue_numbers: 关闭的Issue编号列表
            issue_body: Issue主体内容
            review_comments: 审查评论列表
            review_threads: 审查线程列表
            thread_comments: 线程评论列表
        
        Returns:
            更新后的closing_issues列表
        """
        new_issue_references = []

        # 从Issue主体中提取Issue引用
        if issue_body:
            new_issue_references.extend(extract_issue_references(issue_body))

        # 从审查评论中提取Issue引用
        if review_comments:
            for comment in review_comments:
                new_issue_references.extend(extract_issue_references(comment))

        # 从审查线程中提取Issue引用
        if review_threads:
            for review_thread in review_threads:
                new_issue_references.extend(
                    extract_issue_references(review_thread.comment)
                )

        # 从线程评论中提取Issue引用
        if thread_comments:
            for thread_comment in thread_comments:
                new_issue_references.extend(extract_issue_references(thread_comment))

        # 去重并排除已存在的Issue编号
        non_duplicate_references = set(new_issue_references)
        unique_issue_references = non_duplicate_references.difference(
            closing_issue_numbers
        )

        # 获取每个新引用的Issue详细信息
        for issue_number in unique_issue_references:
            try:
                url = f'{self.base_url}/repositories/{self.owner}/{self.repo}/issues/{issue_number}'
                response = httpx.get(url, headers=self.headers)
                response.raise_for_status()
                issue_data = response.json()
                # 提取Issue的原始内容
                issue_body = issue_data.get('content', {}).get('raw', '')
                if issue_body:
                    closing_issues.append(issue_body)
            except httpx.HTTPError as e:
                logger.warning(f'Failed to fetch issue {issue_number}: {str(e)}')

        return closing_issues

    def get_converted_issues(
        self, issue_numbers: list[int] | None = None, comment_id: int | None = None
    ) -> list[Issue]:
        """
        获取转换后的Issue列表
        
        Args:
            issue_numbers: 要获取的Issue编号列表
            comment_id: 可选的评论ID
        
        Returns:
            转换后的Issue对象列表
        
        Raises:
            ValueError: 当未指定issue_numbers时抛出异常
        """
        if not issue_numbers:
            raise ValueError('Unspecified issue numbers')

        # 下载所有Issues
        all_issues = self.download_issues()
        logger.info(f'Limiting resolving to issues {issue_numbers}.')
        # 过滤出指定编号的Issues
        all_issues = [issue for issue in all_issues if issue.get('id') in issue_numbers]

        converted_issues = []
        for issue in all_issues:
            # 检查Issue是否包含必需的字段（id和title）
            if any([issue.get(key) is None for key in ['id', 'title']]):
                logger.warning(f'Skipping #{issue} as it is missing id or title.')
                continue

            # 处理Pull Request可能为None的body字段
            body = (
                issue.get('content', {}).get('raw', '')
                if issue.get('content') is not None
                else ''
            )

            # 为Pull Request Metadata设置占位符
            closing_issues: list[str] = []
            review_comments: list[str] = []
            review_threads: list[ReviewThread] = []
            thread_ids: list[str] = []
            # 获取head分支名称
            head_branch = issue.get('source', {}).get('branch', {}).get('name', '')
            thread_comments: list[str] = []

            # 创建Issue详情对象
            issue_details = Issue(
                owner=self.owner,
                repo=self.repo,
                number=issue['id'],
                title=issue['title'],
                body=body,
                closing_issues=closing_issues,
                review_comments=review_comments,
                review_threads=review_threads,
                thread_ids=thread_ids,
                head_branch=head_branch,
                thread_comments=thread_comments,
            )

            converted_issues.append(issue_details)

        return converted_issues

    def get_graphql_url(self) -> str:
        """
        获取GraphQL API的URL
        
        Returns:
            GraphQL API的URL字符串
        """
        return f'https://api.{self.base_domain}/graphql'

    def reply_to_comment(self, pr_number: int, comment_id: str, reply: str) -> None:
        """
        回复评论
        
        Args:
            pr_number: Pull Request编号
            comment_id: 要回复的评论ID
            reply: 回复内容
        """
        # 构建回复评论的API URL
        url = f'{self.base_url}/repositories/{self.owner}/{self.repo}/pullrequests/{pr_number}/comments/{comment_id}'

        # 构建回复payload
        payload = {'content': {'raw': reply}}

        # 发送POST请求创建回复
        response = httpx.post(url, headers=self.headers, json=payload)
        response.raise_for_status()

    def get_issue_references(self, body: str) -> list[int]:
        """
        从字符串中提取Issue引用
        
        Args:
            body: 要提取Issue引用的字符串
        
        Returns:
            Issue编号列表
        """
        return extract_issue_references(body)


class BitbucketPRHandler(BitbucketIssueHandler):
    """
    Bitbucket Pull Request处理器，继承自Issue处理器
    
    该类专门用于处理Bitbucket平台的Pull Request相关操作，
    复用了父类的大部分功能。
    """

    def __init__(
        self,
        owner: str,
        repo: str,
        token: str,
        username: str | None = None,
        base_domain: str = 'bitbucket.org',
    ):
        """
        初始化Bitbucket Pull Request处理器
        
        Args:
            owner: Repository的workspace
            repo: Repository名称
            token: Bitbucket API token
            username: 可选的Bitbucket用户名
            base_domain: Bitbucket服务器域名（默认值: "bitbucket.org"）
        """
        super().__init__(owner, repo, token, username, base_domain)