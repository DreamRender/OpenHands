from typing import Any

import httpx

from openhands.core.logger import openhands_logger as logger
from openhands.resolver.interfaces.issue import (
    Issue,
    IssueHandlerInterface,
    ReviewThread,
)
from openhands.resolver.utils import extract_issue_references


class GithubIssueHandler(IssueHandlerInterface):
    """
    GitHub Issue处理器类
    
    该类实现了IssueHandlerInterface接口，用于处理GitHub平台的Issue相关操作，
    包括获取Issue信息、创建Pull Request、发送评论等功能。支持GitHub.com和GitHub Enterprise。
    
    Attributes:
        owner (str): Repository的拥有者
        repo (str): Repository名称
        token (str): GitHub个人访问token
        username (str | None): 可选的GitHub用户名
        base_domain (str): GitHub域名，默认为'github.com'，支持GitHub Enterprise
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
        base_domain: str = 'github.com',
    ):
        """
        初始化GitHub Issue处理器
        
        Args:
            owner: Repository的拥有者
            repo: Repository名称
            token: GitHub个人访问token
            username: 可选的GitHub用户名
            base_domain: GitHub域名，用于支持GitHub Enterprise（默认值: "github.com"）
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
        获取GitHub API的HTTP请求头
        
        使用token认证方式，包含Accept头用于指定API版本。
        
        Returns:
            包含认证信息的HTTP请求头字典
        """
        return {
            'Authorization': f'token {self.token}',
            'Accept': 'application/vnd.github.v3+json',  # 指定GitHub API v3版本
        }

    def get_base_url(self) -> str:
        """
        获取GitHub API的基础URL
        
        根据域名自动判断是GitHub.com还是GitHub Enterprise，
        并返回相应的API URL格式。
        
        Returns:
            API基础URL字符串
        """
        if self.base_domain == 'github.com':
            return f'https://api.github.com/repos/{self.owner}/{self.repo}'
        else:
            # GitHub Enterprise的API URL格式
            return f'https://{self.base_domain}/api/v3/repos/{self.owner}/{self.repo}'

    def get_authorize_url(self) -> str:
        """
        获取带认证信息的授权URL
        
        Returns:
            包含用户名和token的授权URL
        """
        return f'https://{self.username}:{self.token}@{self.base_domain}/'

    def get_branch_url(self, branch_name: str) -> str:
        """
        获取分支的API URL
        
        Args:
            branch_name: 分支名称
        
        Returns:
            分支的API URL
        """
        return self.get_base_url() + f'/branches/{branch_name}'

    def get_download_url(self) -> str:
        """
        获取Issues的下载URL
        
        Returns:
            Issues API的URL
        """
        return f'{self.base_url}/issues'

    def get_clone_url(self) -> str:
        """
        获取带认证信息的Repository克隆URL
        
        根据是否提供了username选择不同的认证格式。
        
        Returns:
            包含认证信息的Git克隆URL
        """
        # 如果提供了用户名，使用用户名:token格式，否则使用x-auth-token:token格式
        username_and_token = (
            f'{self.username}:{self.token}'
            if self.username
            else f'x-auth-token:{self.token}'
        )
        return f'https://{username_and_token}@{self.base_domain}/{self.owner}/{self.repo}.git'

    def get_graphql_url(self) -> str:
        """
        获取GraphQL API的URL
        
        Returns:
            GraphQL API的URL字符串
        """
        if self.base_domain == 'github.com':
            return 'https://api.github.com/graphql'
        else:
            # GitHub Enterprise的GraphQL URL
            return f'https://{self.base_domain}/api/graphql'

    def get_compare_url(self, branch_name: str) -> str:
        """
        获取分支比较的Web URL
        
        Args:
            branch_name: 要比较的分支名称
        
        Returns:
            分支比较页面的URL，带有expand=1参数用于展开diff
        """
        return f'https://{self.base_domain}/{self.owner}/{self.repo}/compare/{branch_name}?expand=1'

    def get_converted_issues(
        self, issue_numbers: list[int] | None = None, comment_id: int | None = None
    ) -> list[Issue]:
        """
        从GitHub下载并转换Issues
        
        Args:
            issue_numbers: 要下载的Issue编号列表
            comment_id: 单个评论的ID，如果提供则只获取该评论，否则获取所有评论
        
        Returns:
            GitHub Issues列表
        
        Raises:
            ValueError: 当未指定issue_numbers时抛出异常
        """
        if not issue_numbers:
            raise ValueError('Unspecified issue number')

        # 下载所有Issues
        all_issues = self.download_issues()
        logger.info(f'Limiting resolving to issues {issue_numbers}.')
        # 过滤出指定编号的Issues，排除Pull Requests（包含'pull_request'字段的Issue）
        all_issues = [
            issue
            for issue in all_issues
            if issue['number'] in issue_numbers and 'pull_request' not in issue
        ]

        # 如果只查询一个Issue但没找到，抛出异常
        if len(issue_numbers) == 1 and not all_issues:
            raise ValueError(f'Issue {issue_numbers[0]} not found')

        converted_issues = []
        for issue in all_issues:
            # 检查必需字段（number和title）
            if any([issue.get(key) is None for key in ['number', 'title']]):
                logger.warning(
                    f'Skipping issue {issue} as it is missing number or title.'
                )
                continue

            # 处理空的body字段，使用空字符串替代None
            if issue.get('body') is None:
                issue['body'] = ''

            # 获取Issue的线程评论
            thread_comments = self.get_issue_comments(
                issue['number'], comment_id=comment_id
            )
            
            # 为常规Issues初始化可选字段，review_comments对于常规Issue为None
            issue_details = Issue(
                owner=self.owner,
                repo=self.repo,
                number=issue['number'],
                title=issue['title'],
                body=issue['body'],
                thread_comments=thread_comments,
                review_comments=None,  # 常规Issues的review_comments初始化为None
            )

            converted_issues.append(issue_details)

        return converted_issues

    def download_issues(self) -> list[Any]:
        """
        下载Repository中的所有开放状态的Issues
        
        使用分页方式获取所有Issues，每页最多100个。
        
        Returns:
            包含所有Issues的列表
        
        Raises:
            ValueError: 当API返回的数据格式不正确时抛出异常
        """
        # 设置分页参数：只获取开放状态的Issues，每页100个，从第1页开始
        params: dict[str, int | str] = {'state': 'open', 'per_page': 100, 'page': 1}
        all_issues = []

        # 循环获取所有页面的Issues
        while True:
            response = httpx.get(self.download_url, headers=self.headers, params=params)
            response.raise_for_status()
            issues = response.json()

            # 如果当前页没有Issues，结束循环
            if not issues:
                break

            # 验证返回数据的格式
            if not isinstance(issues, list) or any(
                [not isinstance(issue, dict) for issue in issues]
            ):
                raise ValueError(
                    'Expected list of dictionaries from Service Github API.'
                )

            # 将当前页的Issues添加到总列表中
            all_issues.extend(issues)
            # 准备获取下一页
            assert isinstance(params['page'], int)
            params['page'] += 1

        return all_issues

    def get_issue_comments(
        self, issue_number: int, comment_id: int | None = None
    ) -> list[str] | None:
        """
        从GitHub下载指定Issue的评论
        
        Args:
            issue_number: Issue编号
            comment_id: 可选的特定评论ID，如果提供则只返回该评论
        
        Returns:
            评论内容列表，如果没有评论则返回None
        """
        # 构建评论API的URL
        url = f'{self.download_url}/{issue_number}/comments'
        params = {'per_page': 100, 'page': 1}
        all_comments = []

        # 分页获取所有评论
        while True:
            response = httpx.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            comments = response.json()

            # 如果当前页没有评论，结束循环
            if not comments:
                break

            # 如果指定了comment_id，只查找匹配的评论
            if comment_id:
                matching_comment = next(
                    (
                        comment['body']
                        for comment in comments
                        if comment['id'] == comment_id
                    ),
                    None,
                )
                if matching_comment:
                    return [matching_comment]
            else:
                # 收集所有评论的内容
                all_comments.extend([comment['body'] for comment in comments])

            # 获取下一页
            params['page'] += 1

        # 如果有评论则返回列表，否则返回None
        return all_comments if all_comments else None

    def branch_exists(self, branch_name: str) -> bool:
        """
        检查指定分支是否存在
        
        通过发送HTTP请求到分支API来检查分支是否存在。
        
        Args:
            branch_name: 要检查的分支名称
        
        Returns:
            如果分支存在返回True，否则返回False
        """
        logger.info(f'Checking if branch {branch_name} exists...')
        response = httpx.get(
            f'{self.base_url}/branches/{branch_name}', headers=self.headers
        )
        # 状态码200表示分支存在
        exists = response.status_code == 200
        logger.info(f'Branch {branch_name} exists: {exists}')
        return exists

    def get_branch_name(self, base_branch_name: str) -> str:
        """
        生成一个不冲突的分支名称
        
        如果指定的分支名已存在，会在后面添加数字后缀直到找到可用的名称。
        
        Args:
            base_branch_name: 基础分支名称
        
        Returns:
            可用的唯一分支名称
        """
        branch_name = base_branch_name
        attempt = 1
        # 循环检查分支名，如果存在则添加尝试次数后缀
        while self.branch_exists(branch_name):
            attempt += 1
            branch_name = f'{base_branch_name}-try{attempt}'
        return branch_name

    def reply_to_comment(self, pr_number: int, comment_id: str, reply: str) -> None:
        """
        回复Pull Request中的评论
        
        使用GraphQL API来回复评论，因为REST API不支持在评论线程中回复回复。
        
        Args:
            pr_number: Pull Request编号
            comment_id: 要回复的评论线程ID
            reply: 回复内容
        """
        # 定义GraphQL mutation查询
        query = """
            mutation($body: String!, $pullRequestReviewThreadId: ID!) {
                addPullRequestReviewThreadReply(input: { body: $body, pullRequestReviewThreadId: $pullRequestReviewThreadId }) {
                    comment {
                        id
                        body
                        createdAt
                    }
                }
            }
            """

        # 为回复内容添加Openhands标识
        comment_reply = f'Openhands fix success summary\n\n\n{reply}'
        variables = {'body': comment_reply, 'pullRequestReviewThreadId': comment_id}
        url = self.get_graphql_url()
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }

        # 发送GraphQL请求
        response = httpx.post(
            url, json={'query': query, 'variables': variables}, headers=headers
        )
        response.raise_for_status()

    def get_pull_url(self, pr_number: int) -> str:
        """
        获取Pull Request的Web URL
        
        Args:
            pr_number: Pull Request编号
        
        Returns:
            Pull Request的Web页面URL
        """
        return f'https://{self.base_domain}/{self.owner}/{self.repo}/pull/{pr_number}'

    def get_default_branch_name(self) -> str:
        """
        获取Repository的默认分支名称
        
        通过API查询Repository信息来获取默认分支名称。
        
        Returns:
            默认分支名称字符串
        """
        response = httpx.get(f'{self.base_url}', headers=self.headers)
        response.raise_for_status()
        data = response.json()
        return str(data['default_branch'])

    def create_pull_request(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        创建Pull Request
        
        Args:
            data: 包含Pull Request信息的字典
        
        Returns:
            创建的Pull Request信息字典
        
        Raises:
            RuntimeError: 当由于权限不足导致创建失败时抛出异常
        """
        if data is None:
            data = {}
            
        # 发送POST请求创建Pull Request
        response = httpx.post(f'{self.base_url}/pulls', headers=self.headers, json=data)
        
        # 特殊处理权限不足的情况
        if response.status_code == 403:
            raise RuntimeError(
                'Failed to create pull request due to missing permissions. '
                'Make sure that the provided token has push permissions for the repository.'
            )
        response.raise_for_status()
        pr_data = response.json()
        return dict(pr_data)

    def request_reviewers(self, reviewer: str, pr_number: int) -> None:
        """
        为Pull Request请求审查者
        
        Args:
            reviewer: 审查者的用户名
            pr_number: Pull Request编号
        """
        # 构建审查者请求数据
        review_data = {'reviewers': [reviewer]}
        review_response = httpx.post(
            f'{self.base_url}/pulls/{pr_number}/requested_reviewers',
            headers=self.headers,
            json=review_data,
        )
        # 如果请求失败，记录警告日志
        if review_response.status_code != 201:
            logger.warning(
                f'Failed to request review from {reviewer}: {review_response.text}'
            )

    def send_comment_msg(self, issue_number: int, msg: str) -> None:
        """
        向GitHub Issue或Pull Request发送评论消息
        
        Args:
            issue_number: Issue或Pull Request编号
            msg: 要发布为评论的消息内容
        """
        # 构建评论API URL
        comment_url = f'{self.base_url}/issues/{issue_number}/comments'
        comment_data = {'body': msg}
        
        # 发送POST请求创建评论
        comment_response = httpx.post(
            comment_url, headers=self.headers, json=comment_data
        )
        
        # 检查响应状态并记录相应日志
        if comment_response.status_code != 201:
            logger.error(
                f'Failed to post comment: {comment_response.status_code} {comment_response.text}'
            )
        else:
            logger.info(f'Comment added to the PR: {msg}')

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
        
        注意：GitHub实现中此方法为空实现，返回空列表。
        
        Args:
            closing_issues: 关闭的Issue列表
            closing_issue_numbers: 关闭的Issue编号列表
            issue_body: Issue主体内容
            review_comments: 审查评论列表
            review_threads: 审查线程列表
            thread_comments: 线程评论列表
        
        Returns:
            空列表（GitHub实现中未处理外部引用）
        """
        return []


class GithubPRHandler(GithubIssueHandler):
    """
    GitHub Pull Request处理器
    
    该类继承自GithubIssueHandler，专门用于处理GitHub Pull Request相关操作，
    包括下载PR Metadata、获取审查评论和线程等。
    
    Attributes:
        继承父类的所有属性，并重写download_url为Pull Request专用URL
    """
    
    def __init__(
        self,
        owner: str,
        repo: str,
        token: str,
        username: str | None = None,
        base_domain: str = 'github.com',
    ):
        """
        初始化GitHub Pull Request处理器
        
        Args:
            owner: Repository的拥有者
            repo: Repository名称
            token: GitHub个人访问token
            username: 可选的GitHub用户名
            base_domain: GitHub域名，用于支持GitHub Enterprise（默认值: "github.com"）
        """
        super().__init__(owner, repo, token, username, base_domain)
        
        # 根据域名设置Pull Request专用的下载URL
        if self.base_domain == 'github.com':
            self.download_url = (
                f'https://api.github.com/repos/{self.owner}/{self.repo}/pulls'
            )
        else:
            # GitHub Enterprise的Pull Request URL
            self.download_url = f'https://{self.base_domain}/api/v3/repos/{self.owner}/{self.repo}/pulls'

    def download_pr_metadata(
        self, pull_number: int, comment_id: int | None = None
    ) -> tuple[list[str], list[int], list[str], list[ReviewThread], list[str]]:
        """
        对GitHub API运行GraphQL查询以获取信息
        
        获取以下信息：
        1. 未解决的审查评论
        2. Pull Request将关闭的被引用Issues
        
        Args:
            pull_number: 要查询的Pull Request编号
            comment_id: 可选的特定评论ID，用于聚焦特定评论
        
        Returns:
            包含以下元素的元组：
            - closing_issues_bodies: 关闭的Issues内容列表
            - closing_issue_numbers: 关闭的Issue编号列表
            - review_bodies: 审查评论内容列表
            - review_threads: 未解决的审查线程列表
            - thread_ids: 线程ID列表
        """
        # 使用GraphQL查询因为REST API不能指示审查评论的解决状态
        # TODO: 目前获取前10个issues、100个审查线程和100个评论；需要添加分页以获取全部
        query = """
                query($owner: String!, $repo: String!, $pr: Int!) {
                    repository(owner: $owner, name: $repo) {
                        pullRequest(number: $pr) {
                            closingIssuesReferences(first: 10) {
                                edges {
                                    node {
                                        body
                                        number
                                    }
                                }
                            }
                            url
                            reviews(first: 100) {
                                nodes {
                                    body
                                    state
                                    fullDatabaseId
                                }
                            }
                            reviewThreads(first: 100) {
                                edges{
                                    node{
                                        id
                                        isResolved
                                        comments(first: 100) {
                                            totalCount
                                            nodes {
                                                body
                                                path
                                                fullDatabaseId
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            """

        variables = {'owner': self.owner, 'repo': self.repo, 'pr': pull_number}

        url = self.get_graphql_url()
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }

        # 发送GraphQL请求
        response = httpx.post(
            url, json={'query': query, 'variables': variables}, headers=headers
        )
        response.raise_for_status()
        response_json = response.json()

        # 解析响应以获取关闭的Issue引用和未解决的审查评论
        pr_data = (
            response_json.get('data', {}).get('repository', {}).get('pullRequest', {})
        )

        # 获取关闭的Issues
        closing_issues = pr_data.get('closingIssuesReferences', {}).get('edges', [])
        closing_issues_bodies = [issue['node']['body'] for issue in closing_issues]
        closing_issue_numbers = [
            issue['node']['number'] for issue in closing_issues
        ]  # 提取Issue编号

        # 获取审查评论
        reviews = pr_data.get('reviews', {}).get('nodes', [])
        # 如果指定了comment_id，过滤只包含该评论的审查
        if comment_id is not None:
            reviews = [
                review
                for review in reviews
                if int(review['fullDatabaseId']) == comment_id
            ]
        review_bodies = [review['body'] for review in reviews]

        # 获取未解决的审查线程
        review_threads = []
        thread_ids = []  # 存储线程ID；Agent回复该线程
        raw_review_threads = pr_data.get('reviewThreads', {}).get('edges', [])
        
        for thread in raw_review_threads:
            node = thread.get('node', {})
            # 检查审查线程是否未解决
            if not node.get('isResolved', True):
                id = node.get('id')
                thread_contains_comment_id = False
                my_review_threads = node.get('comments', {}).get('nodes', [])
                message = ''
                files = []
                
                for i, review_thread in enumerate(my_review_threads):
                    # 检查是否包含指定的comment_id
                    if (
                        comment_id is not None
                        and int(review_thread['fullDatabaseId']) == comment_id
                    ):
                        thread_contains_comment_id = True

                    # 如果是线程中的最后一个评论
                    if i == len(my_review_threads) - 1:
                        if len(my_review_threads) > 1:
                            message += '---\n'  # 如果有多个线程，在最后一个消息前添加"---"
                        message += 'latest feedback:\n' + review_thread['body'] + '\n'
                    else:
                        message += review_thread['body'] + '\n'  # 每个线程添加到新行

                    # 收集相关文件路径
                    file = review_thread.get('path')
                    if file and file not in files:
                        files.append(file)

                # 如果没有指定comment_id或线程包含指定的comment_id，则添加到结果中
                if comment_id is None or thread_contains_comment_id:
                    unresolved_thread = ReviewThread(comment=message, files=files)
                    review_threads.append(unresolved_thread)
                    thread_ids.append(id)

        return (
            closing_issues_bodies,
            closing_issue_numbers,
            review_bodies,
            review_threads,
            thread_ids,
        )

    def get_pr_comments(
        self, pr_number: int, comment_id: int | None = None
    ) -> list[str] | None:
        """
        从GitHub下载指定Pull Request的评论
        
        Args:
            pr_number: Pull Request编号
            comment_id: 可选的特定评论ID
        
        Returns:
            评论内容列表，如果没有评论则返回None
        """
        # 根据域名构建评论API URL
        if self.base_domain == 'github.com':
            url = f'https://api.github.com/repos/{self.owner}/{self.repo}/issues/{pr_number}/comments'
        else:
            url = f'https://{self.base_domain}/api/v3/repos/{self.owner}/{self.repo}/issues/{pr_number}/comments'
            
        headers = {
            'Authorization': f'token {self.token}',
            'Accept': 'application/vnd.github.v3+json',
        }
        params = {'per_page': 100, 'page': 1}
        all_comments = []

        # 分页获取所有评论
        while True:
            response = httpx.get(url, headers=headers, params=params)
            response.raise_for_status()
            comments = response.json()

            if not comments:
                break

            # 如果指定了comment_id，查找匹配的评论
            if comment_id is not None:
                matching_comment = next(
                    (
                        comment['body']
                        for comment in comments
                        if comment['id'] == comment_id
                    ),
                    None,
                )
                if matching_comment:
                    return [matching_comment]
            else:
                # 收集所有评论内容
                all_comments.extend([comment['body'] for comment in comments])

            params['page'] += 1

        return all_comments if all_comments else None

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
        并将其添加到closing_issues列表中。
        
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

        # 获取每个新引用Issue的详细信息
        for issue_number in unique_issue_references:
            try:
                # 根据域名构建Issue API URL
                if self.base_domain == 'github.com':
                    url = f'https://api.github.com/repos/{self.owner}/{self.repo}/issues/{issue_number}'
                else:
                    url = f'https://{self.base_domain}/api/v3/repos/{self.owner}/{self.repo}/issues/{issue_number}'
                    
                headers = {
                    'Authorization': f'Bearer {self.token}',
                    'Accept': 'application/vnd.github.v3+json',
                }
                response = httpx.get(url, headers=headers)
                response.raise_for_status()
                issue_data = response.json()
                issue_body = issue_data.get('body', '')
                if issue_body:
                    closing_issues.append(issue_body)
            except httpx.HTTPError as e:
                logger.warning(f'Failed to fetch issue {issue_number}: {str(e)}')

        return closing_issues

    def get_converted_issues(
        self, issue_numbers: list[int] | None = None, comment_id: int | None = None
    ) -> list[Issue]:
        """
        获取转换后的Pull Request列表
        
        Args:
            issue_numbers: 要获取的Pull Request编号列表
            comment_id: 可选的评论ID
        
        Returns:
            转换后的Issue对象列表（代表Pull Requests）
        
        Raises:
            ValueError: 当未指定issue_numbers时抛出异常
        """
        if not issue_numbers:
            raise ValueError('Unspecified issue numbers')

        # 下载所有Pull Requests
        all_issues = self.download_issues()
        logger.info(f'Limiting resolving to issues {issue_numbers}.')
        # 过滤出指定编号的Pull Requests
        all_issues = [issue for issue in all_issues if issue['number'] in issue_numbers]

        converted_issues = []
        for issue in all_issues:
            # 对于Pull Requests，body可能为None
            if any([issue.get(key) is None for key in ['number', 'title']]):
                logger.warning(f'Skipping #{issue} as it is missing number or title.')
                continue

            # 处理Pull Request可能为None的body字段
            body = issue.get('body') if issue.get('body') is not None else ''
            
            # 下载Pull Request的Metadata
            (
                closing_issues,
                closing_issues_numbers,
                review_comments,
                review_threads,
                thread_ids,
            ) = self.download_pr_metadata(issue['number'], comment_id=comment_id)
            
            # 获取head分支
            head_branch = issue['head']['ref']

            # 获取Pull Request的线程评论
            thread_comments = self.get_pr_comments(
                issue['number'], comment_id=comment_id
            )

            # 获取外部Issue引用的上下文
            closing_issues = self.get_context_from_external_issues_references(
                closing_issues,
                closing_issues_numbers,
                body,
                review_comments,
                review_threads,
                thread_comments,
            )

            # 创建Issue详情对象（代表Pull Request）
            issue_details = Issue(
                owner=self.owner,
                repo=self.repo,
                number=issue['number'],
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