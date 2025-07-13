import json
import os
from datetime import datetime
from typing import Any

import httpx
from pydantic import SecretStr

from openhands.core.logger import openhands_logger as logger
from openhands.integrations.github.queries import (
    suggested_task_issue_graphql_query,
    suggested_task_pr_graphql_query,
)
from openhands.integrations.service_types import (
    BaseGitService,
    Branch,
    GitService,
    ProviderType,
    Repository,
    RequestMethod,
    SuggestedTask,
    TaskType,
    UnknownException,
    User,
)
from openhands.server.types import AppMode
from openhands.utils.import_utils import get_impl


class GitHubService(BaseGitService, GitService):
    """
    GitHub集成的GitService默认实现。

    TODO: 这似乎不是get_impl()模式的好候选。我们应该实际分离和实现哪些抽象方法？
    这是OpenHands中的一个扩展点，允许应用程序自定义GitHub
    集成行为。应用程序可以通过以下方式替换自己的实现：
    1. 创建一个继承自GitService的类
    2. 实现所有必需的方法
    3. 设置server_config.github_service_class为该类的完全限定名

    该类通过openhands.server.shared.py中的get_impl()实例化。
    """

    BASE_URL = 'https://api.github.com'          # GitHub API基础URL
    token: SecretStr = SecretStr('')             # 认证token
    refresh = False                              # 是否刷新token标志

    def __init__(
        self,
        user_id: str | None = None,
        external_auth_id: str | None = None,
        external_auth_token: SecretStr | None = None,
        token: SecretStr | None = None,
        external_token_manager: bool = False,
        base_domain: str | None = None,
    ):
        """
        初始化GitHubService实例。
        
        Args:
            user_id (str | None): 用户ID
            external_auth_id (str | None): 外部认证ID
            external_auth_token (SecretStr | None): 外部认证token
            token (SecretStr | None): GitHub API token
            external_token_manager (bool): 是否使用外部token管理器
            base_domain (str | None): 自定义GitHub Enterprise实例的域名
        """
        self.user_id = user_id
        self.external_token_manager = external_token_manager

        if token:
            self.token = token

        if base_domain and base_domain != 'github.com':
            # 如果提供了非标准域名，更新API URL（用于GitHub Enterprise）
            self.BASE_URL = f'https://{base_domain}/api/v3'

        self.external_auth_id = external_auth_id
        self.external_auth_token = external_auth_token

    @property
    def provider(self) -> str:
        """
        返回服务提供商标识符。
        
        Returns:
            str: 'github'
        """
        return ProviderType.GITHUB.value

    async def _get_github_headers(self) -> dict:
        """
        从设置存储中检索GitHub Token来构造请求头。
        
        Returns:
            dict: 包含Authorization和Accept头的字典
        """
        if not self.token:
            # 如果没有token，尝试获取最新token
            latest_token = await self.get_latest_token()
            if latest_token:
                self.token = latest_token

        return {
            'Authorization': f'Bearer {self.token.get_secret_value() if self.token else ""}',
            'Accept': 'application/vnd.github.v3+json',
        }

    def _has_token_expired(self, status_code: int) -> bool:
        """
        检查token是否已过期。
        
        Args:
            status_code (int): HTTP状态码
            
        Returns:
            bool: 如果状态码为401则返回True，表示token已过期
        """
        return status_code == 401

    async def get_latest_token(self) -> SecretStr | None:
        """
        获取最新的token。
        
        Returns:
            SecretStr | None: 当前token
        """
        return self.token

    async def _make_request(
        self,
        url: str,
        params: dict | None = None,
        method: RequestMethod = RequestMethod.GET,
    ) -> tuple[Any, dict]:
        """
        向GitHub API发起请求。
        
        Args:
            url (str): 请求URL
            params (dict | None): 请求参数
            method (RequestMethod): HTTP请求方法
            
        Returns:
            tuple[Any, dict]: 包含响应数据和头信息的元组
            
        Raises:
            HTTPStatusError: HTTP状态错误
            HTTPError: HTTP通信错误
        """
        try:
            async with httpx.AsyncClient() as client:
                github_headers = await self._get_github_headers()

                # 发起初始请求
                response = await self.execute_request(
                    client=client,
                    url=url,
                    headers=github_headers,
                    params=params,
                    method=method,
                )

                # 如果需要刷新且token已过期，则处理token刷新
                if self.refresh and self._has_token_expired(response.status_code):
                    await self.get_latest_token()
                    github_headers = await self._get_github_headers()
                    response = await self.execute_request(
                        client=client,
                        url=url,
                        headers=github_headers,
                        params=params,
                        method=method,
                    )

                response.raise_for_status()
                headers = {}
                # 保存分页链接信息
                if 'Link' in response.headers:
                    headers['Link'] = response.headers['Link']

                return response.json(), headers

        except httpx.HTTPStatusError as e:
            raise self.handle_http_status_error(e)
        except httpx.HTTPError as e:
            raise self.handle_http_error(e)

    async def get_user(self) -> User:
        """
        获取当前认证用户的信息。
        
        Returns:
            User: 用户信息对象
        """
        url = f'{self.BASE_URL}/user'
        response, _ = await self._make_request(url)

        return User(
            id=str(response.get('id', '')),
            login=response.get('login'),
            avatar_url=response.get('avatar_url'),
            company=response.get('company'),
            name=response.get('name'),
            email=response.get('email'),
        )

    async def verify_access(self) -> bool:
        """
        通过发起简单请求验证token是否有效。
        
        Returns:
            bool: 验证成功返回True
        """
        url = f'{self.BASE_URL}'
        await self._make_request(url)
        return True

    async def _fetch_paginated_repos(
        self, url: str, params: dict, max_repos: int, extract_key: str | None = None
    ) -> list[dict]:
        """
        使用分页支持获取Repository。
        
        Args:
            url (str): API端点URL
            params (dict): 请求的查询参数
            max_repos (int): 要获取的最大Repository数量
            extract_key (str | None): 如果提供，从响应中的此键提取Repository
            
        Returns:
            list[dict]: Repository字典列表
        """
        repos: list[dict] = []
        page = 1

        while len(repos) < max_repos:
            page_params = {**params, 'page': str(page)}
            response, headers = await self._make_request(url, page_params)

            # 从响应中提取Repository
            page_repos = response.get(extract_key, []) if extract_key else response

            if not page_repos:  # 没有更多Repository
                break

            repos.extend(page_repos)
            page += 1

            # 检查是否已到达最后一页
            link_header = headers.get('Link', '')
            if 'rel="next"' not in link_header:
                break

        return repos[:max_repos]  # 如果需要，截取到max_repos

    def parse_pushed_at_date(self, repo):
        """
        解析Repository的推送日期。
        
        Args:
            repo: Repository字典对象
            
        Returns:
            datetime: 解析后的日期时间对象，如果解析失败则返回最小日期
        """
        ts = repo.get('pushed_at')
        return datetime.strptime(ts, '%Y-%m-%dT%H:%M:%SZ') if ts else datetime.min

    async def get_repositories(self, sort: str, app_mode: AppMode) -> list[Repository]:
        """
        获取用户的Repository列表。
        
        Args:
            sort (str): 排序方式
            app_mode (AppMode): 应用模式
            
        Returns:
            list[Repository]: 用户的Repository列表
        """
        MAX_REPOS = 1000        # 最大Repository数量
        PER_PAGE = 100          # GitHub API允许的每页最大数量
        all_repos: list[dict] = []

        if app_mode == AppMode.SAAS:
            # 获取所有安装ID并为每个安装获取Repository
            installation_ids = await self.get_installation_ids()

            # 遍历每个安装ID
            for installation_id in installation_ids:
                params = {'per_page': str(PER_PAGE)}
                url = (
                    f'{self.BASE_URL}/user/installations/{installation_id}/repositories'
                )

                # 获取此安装的Repository
                installation_repos = await self._fetch_paginated_repos(
                    url, params, MAX_REPOS - len(all_repos), extract_key='repositories'
                )

                all_repos.extend(installation_repos)

                # 如果已经达到MAX_REPOS，不需要检查其他安装
                if len(all_repos) >= MAX_REPOS:
                    break

            if sort == 'pushed':
                # 对Repository按推送日期排序
                all_repos.sort(key=self.parse_pushed_at_date, reverse=True)
        else:
            # 非SaaS模式的原始行为
            params = {'per_page': str(PER_PAGE), 'sort': sort}
            url = f'{self.BASE_URL}/user/repos'

            # 获取用户Repository
            all_repos = await self._fetch_paginated_repos(url, params, MAX_REPOS)

        # 转换为Repository对象
        return [
            Repository(
                id=str(repo.get('id')),  # type: ignore[arg-type]
                full_name=repo.get('full_name'),  # type: ignore[arg-type]
                stargazers_count=repo.get('stargazers_count'),
                git_provider=ProviderType.GITHUB,
                is_public=not repo.get('private', True),
            )
            for repo in all_repos
        ]

    async def get_installation_ids(self) -> list[int]:
        """
        获取用户的GitHub App安装ID列表。
        
        Returns:
            list[int]: 安装ID列表
        """
        url = f'{self.BASE_URL}/user/installations'
        response, _ = await self._make_request(url)
        installations = response.get('installations', [])
        return [i['id'] for i in installations]

    async def search_repositories(
        self, query: str, per_page: int, sort: str, order: str
    ) -> list[Repository]:
        """
        搜索公开的Repository。
        
        Args:
            query (str): 搜索查询字符串
            per_page (int): 每页返回的结果数量
            sort (str): 排序字段
            order (str): 排序顺序
            
        Returns:
            list[Repository]: 搜索结果Repository列表
        """
        url = f'{self.BASE_URL}/search/repositories'
        # 向查询添加is:public以确保只搜索公开Repository
        query_with_visibility = f'{query} is:public'
        params = {
            'q': query_with_visibility,
            'per_page': per_page,
            'sort': sort,
            'order': order,
        }

        response, _ = await self._make_request(url, params)
        repo_items = response.get('items', [])

        repos = [
            Repository(
                id=str(repo.get('id')),
                full_name=repo.get('full_name'),
                stargazers_count=repo.get('stargazers_count'),
                git_provider=ProviderType.GITHUB,
                is_public=True,
            )
            for repo in repo_items
        ]

        return repos

    async def execute_graphql_query(
        self, query: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        """
        对GitHub API执行GraphQL查询。
        
        Args:
            query (str): GraphQL查询字符串
            variables (dict[str, Any]): GraphQL查询变量
            
        Returns:
            dict[str, Any]: GraphQL响应数据
            
        Raises:
            UnknownException: GraphQL查询错误或HTTP错误
        """
        try:
            async with httpx.AsyncClient() as client:
                github_headers = await self._get_github_headers()
                response = await client.post(
                    f'{self.BASE_URL}/graphql',
                    headers=github_headers,
                    json={'query': query, 'variables': variables},
                )
                response.raise_for_status()

                result = response.json()
                if 'errors' in result:
                    raise UnknownException(
                        f'GraphQL query error: {json.dumps(result["errors"])}'
                    )

                return dict(result)

        except httpx.HTTPStatusError as e:
            raise self.handle_http_status_error(e)
        except httpx.HTTPError as e:
            raise self.handle_http_error(e)

    async def get_suggested_tasks(self) -> list[SuggestedTask]:
        """
        获取认证用户在所有Repository中的建议任务。

        Returns:
            list[SuggestedTask]: 建议任务列表，包括：
            - 用户创建的PR
            - 分配给用户的Issue

        注意：查询被分割以避免超时问题。
        """
        # 获取用户信息用于查询
        user = await self.get_user()
        login = user.login
        tasks: list[SuggestedTask] = []
        variables = {'login': login}

        try:
            # 执行PR查询
            pr_response = await self.execute_graphql_query(
                suggested_task_pr_graphql_query, variables
            )
            pr_data = pr_response['data']['user']

            # 处理Pull Request
            for pr in pr_data['pullRequests']['nodes']:
                repo_name = pr['repository']['nameWithOwner']

                # 从默认任务类型开始
                task_type = TaskType.OPEN_PR

                # 检查特定状态
                if pr['mergeable'] == 'CONFLICTING':
                    # 有合并冲突
                    task_type = TaskType.MERGE_CONFLICTS
                elif (
                    pr['commits']['nodes']
                    and pr['commits']['nodes'][0]['commit']['statusCheckRollup']
                    and pr['commits']['nodes'][0]['commit']['statusCheckRollup'][
                        'state'
                    ]
                    == 'FAILURE'
                ):
                    # 状态检查失败
                    task_type = TaskType.FAILING_CHECKS
                elif any(
                    review['state'] in ['CHANGES_REQUESTED', 'COMMENTED']
                    for review in pr['reviews']['nodes']
                ):
                    # 有未解决的评论
                    task_type = TaskType.UNRESOLVED_COMMENTS

                # 只有当任务类型不是OPEN_PR时才添加任务
                if task_type != TaskType.OPEN_PR:
                    tasks.append(
                        SuggestedTask(
                            git_provider=ProviderType.GITHUB,
                            task_type=task_type,
                            repo=repo_name,
                            issue_number=pr['number'],
                            title=pr['title'],
                        )
                    )

        except Exception as e:
            logger.info(
                f'Error fetching suggested task for PRs: {e}',
                extra={
                    'signal': 'github_suggested_tasks',
                    'user_id': self.external_auth_id,
                },
            )

        try:
            # 执行Issue查询
            issue_response = await self.execute_graphql_query(
                suggested_task_issue_graphql_query, variables
            )
            issue_data = issue_response['data']['user']

            # 处理Issue
            for issue in issue_data['issues']['nodes']:
                repo_name = issue['repository']['nameWithOwner']
                tasks.append(
                    SuggestedTask(
                        git_provider=ProviderType.GITHUB,
                        task_type=TaskType.OPEN_ISSUE,
                        repo=repo_name,
                        issue_number=issue['number'],
                        title=issue['title'],
                    )
                )

            return tasks

        except Exception as e:
            logger.info(
                f'Error fetching suggested task for issues: {e}',
                extra={
                    'signal': 'github_suggested_tasks',
                    'user_id': self.external_auth_id,
                },
            )

        return tasks

    async def get_repository_details_from_repo_name(
        self, repository: str
    ) -> Repository:
        """
        根据Repository名称获取Repository详细信息。
        
        Args:
            repository (str): Repository名称（格式：owner/repo）
            
        Returns:
            Repository: Repository详细信息
        """
        url = f'{self.BASE_URL}/repos/{repository}'
        repo, _ = await self._make_request(url)

        return Repository(
            id=str(repo.get('id')),
            full_name=repo.get('full_name'),
            stargazers_count=repo.get('stargazers_count'),
            git_provider=ProviderType.GITHUB,
            is_public=not repo.get('private', True),
        )

    async def get_branches(self, repository: str) -> list[Branch]:
        """
        获取Repository的分支列表。
        
        Args:
            repository (str): Repository名称
            
        Returns:
            list[Branch]: 分支列表
        """
        url = f'{self.BASE_URL}/repos/{repository}/branches'

        # 设置最大分支数量（10页，每页100个）
        MAX_BRANCHES = 1000
        PER_PAGE = 100

        all_branches: list[Branch] = []
        page = 1

        # 获取最多10页的分支
        while page <= 10 and len(all_branches) < MAX_BRANCHES:
            params = {'per_page': str(PER_PAGE), 'page': str(page)}
            response, headers = await self._make_request(url, params)

            if not response:  # 没有更多分支
                break

            for branch_data in response:
                # 如果可用，提取最后提交日期
                last_push_date = None
                if branch_data.get('commit') and branch_data['commit'].get('commit'):
                    commit_info = branch_data['commit']['commit']
                    if commit_info.get('committer') and commit_info['committer'].get(
                        'date'
                    ):
                        last_push_date = commit_info['committer']['date']

                branch = Branch(
                    name=branch_data.get('name'),
                    commit_sha=branch_data.get('commit', {}).get('sha', ''),
                    protected=branch_data.get('protected', False),
                    last_push_date=last_push_date,
                )
                all_branches.append(branch)

            page += 1

            # 检查是否已到达最后一页
            link_header = headers.get('Link', '')
            if 'rel="next"' not in link_header:
                break

        return all_branches

    async def create_pr(
        self,
        repo_name: str,
        source_branch: str,
        target_branch: str,
        title: str,
        body: str | None = None,
        draft: bool = True,
        labels: list[str] | None = None,
    ) -> str:
        """
        使用用户凭据创建PR。
        
        Args:
            repo_name (str): Repository的完整名称（owner/repo）
            source_branch (str): 实现更改的分支名称
            target_branch (str): 希望将更改拉入的分支名称
            title (str): Pull Request的标题
            body (str | None): Pull Request的正文/描述（可选）
            draft (bool): 是否创建草稿PR（可选，默认为True）
            labels (list[str] | None): 应用到Pull Request的标签列表（可选）
            
        Returns:
            str: 成功时返回PR URL，失败时返回错误消息
        """
        url = f'{self.BASE_URL}/repos/{repo_name}/pulls'

        # 如果未提供正文，设置默认正文
        if not body:
            body = f'Merging changes from {source_branch} into {target_branch}'

        # 准备请求负载
        payload = {
            'title': title,
            'head': source_branch,
            'base': target_branch,
            'body': body,
            'draft': draft,
        }

        # 发起POST请求创建PR
        response, _ = await self._make_request(
            url=url, params=payload, method=RequestMethod.POST
        )

        # 如果提供了标签，添加标签（PR在GitHub API中是Issue的一种类型）
        if labels and len(labels) > 0:
            pr_number = response['number']
            labels_url = f'{self.BASE_URL}/repos/{repo_name}/issues/{pr_number}/labels'
            labels_payload = {'labels': labels}
            await self._make_request(
                url=labels_url, params=labels_payload, method=RequestMethod.POST
            )

        # 返回创建的PR的HTML URL
        return response['html_url']


# 从环境变量获取GitHub服务类配置
github_service_cls = os.environ.get(
    'OPENHANDS_GITHUB_SERVICE_CLS',
    'openhands.integrations.github.github_service.GitHubService',
)
# 使用get_impl获取实际的GitHub服务实现类
GithubServiceImpl = get_impl(GitHubService, github_service_cls)
