import os
from typing import Any

import httpx
from pydantic import SecretStr

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


class GitLabService(BaseGitService, GitService):
    """
    GitLab集成的GitService默认实现。

    TODO: 这似乎不是get_impl()模式的好候选。我们应该实际分离和实现哪些抽象方法？
    这是OpenHands中的一个扩展点，允许应用程序自定义GitLab
    集成行为。应用程序可以通过以下方式替换自己的实现：
    1. 创建一个继承自GitService的类
    2. 实现所有必需的方法
    3. 设置server_config.gitlab_service_class为该类的完全限定名

    该类通过openhands.server.shared.py中的get_impl()实例化。
    """

    BASE_URL = 'https://gitlab.com/api/v4'        # GitLab API基础URL
    GRAPHQL_URL = 'https://gitlab.com/api/graphql'  # GitLab GraphQL API URL
    token: SecretStr = SecretStr('')              # 认证token
    refresh = False                               # 是否刷新token标志

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
        初始化GitLabService实例。
        
        Args:
            user_id (str | None): 用户ID
            external_auth_id (str | None): 外部认证ID
            external_auth_token (SecretStr | None): 外部认证token
            token (SecretStr | None): GitLab API token
            external_token_manager (bool): 是否使用外部token管理器
            base_domain (str | None): 自定义GitLab实例的域名
        """
        self.user_id = user_id
        self.external_token_manager = external_token_manager

        if token:
            self.token = token

        if base_domain:
            # 如果提供了自定义域名，更新API URL
            self.BASE_URL = f'https://{base_domain}/api/v4'
            self.GRAPHQL_URL = f'https://{base_domain}/api/graphql'

    @property
    def provider(self) -> str:
        """
        返回服务提供商标识符。
        
        Returns:
            str: 'gitlab'
        """
        return ProviderType.GITLAB.value

    async def _get_gitlab_headers(self) -> dict[str, Any]:
        """
        获取GitLab API请求头。
        
        检索GitLab Token来构造请求头。
        
        Returns:
            dict[str, Any]: 包含Authorization头的字典
        """
        if not self.token:
            # 如果没有token，尝试获取最新token
            latest_token = await self.get_latest_token()
            if latest_token:
                self.token = latest_token

        return {
            'Authorization': f'Bearer {self.token.get_secret_value()}',
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
        向GitLab API发起请求。
        
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
                gitlab_headers = await self._get_gitlab_headers()

                # 发起初始请求
                response = await self.execute_request(
                    client=client,
                    url=url,
                    headers=gitlab_headers,
                    params=params,
                    method=method,
                )

                # 如果需要刷新且token已过期，则处理token刷新
                if self.refresh and self._has_token_expired(response.status_code):
                    await self.get_latest_token()
                    gitlab_headers = await self._get_gitlab_headers()
                    response = await self.execute_request(
                        client=client,
                        url=url,
                        headers=gitlab_headers,
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

    async def execute_graphql_query(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> Any:
        """
        对GitLab GraphQL API执行GraphQL查询。
        
        Args:
            query (str): GraphQL查询字符串
            variables (dict[str, Any] | None): GraphQL查询的可选变量
            
        Returns:
            Any: GraphQL响应的数据部分
            
        Raises:
            UnknownException: GraphQL错误或HTTP错误
        """
        if variables is None:
            variables = {}
        try:
            async with httpx.AsyncClient() as client:
                gitlab_headers = await self._get_gitlab_headers()
                # 为GraphQL添加Content-Type头
                gitlab_headers['Content-Type'] = 'application/json'

                payload = {
                    'query': query,
                    'variables': variables if variables is not None else {},
                }

                response = await client.post(
                    self.GRAPHQL_URL, headers=gitlab_headers, json=payload
                )

                # 处理token刷新
                if self.refresh and self._has_token_expired(response.status_code):
                    await self.get_latest_token()
                    gitlab_headers = await self._get_gitlab_headers()
                    gitlab_headers['Content-Type'] = 'application/json'
                    response = await client.post(
                        self.GRAPHQL_URL, headers=gitlab_headers, json=payload
                    )

                response.raise_for_status()
                result = response.json()

                # 检查GraphQL错误
                if 'errors' in result:
                    error_message = result['errors'][0].get(
                        'message', 'Unknown GraphQL error'
                    )
                    raise UnknownException(f'GraphQL error: {error_message}')

                return result.get('data')
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

        # 如果未提供头像URL，使用默认值
        # 在一些自托管的GitLab实例中，avatar_url字段可能返回None
        avatar_url = response.get('avatar_url') or ''

        return User(
            id=str(response.get('id', '')),
            login=response.get('username'),  # type: ignore[call-arg]
            avatar_url=avatar_url,
            name=response.get('name'),
            email=response.get('email'),
            company=response.get('organization'),
        )

    async def search_repositories(
        self, query: str, per_page: int = 30, sort: str = 'updated', order: str = 'desc'
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
        url = f'{self.BASE_URL}/projects'
        params = {
            'search': query,
            'per_page': per_page,
            'order_by': 'last_activity_at',
            'sort': order,
            'visibility': 'public',  # 只搜索公开的项目
        }

        response, _ = await self._make_request(url, params)
        repos = [
            Repository(
                id=str(repo.get('id')),
                full_name=repo.get('path_with_namespace'),
                stargazers_count=repo.get('star_count'),
                git_provider=ProviderType.GITLAB,
                is_public=True,
            )
            for repo in response
        ]

        return repos

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
        PER_PAGE = 100          # GitLab API允许的每页最大数量
        all_repos: list[dict] = []
        page = 1

        url = f'{self.BASE_URL}/projects'
        # 将GitHub的排序值映射到GitLab的order_by值
        order_by = {
            'pushed': 'last_activity_at',
            'updated': 'last_activity_at',
            'created': 'created_at',
            'full_name': 'name',
        }.get(sort, 'last_activity_at')

        while len(all_repos) < MAX_REPOS:
            params = {
                'page': str(page),
                'per_page': str(PER_PAGE),
                'order_by': order_by,
                'sort': 'desc',    # GitLab使用sort表示方向（asc/desc）
                'membership': 1,   # 使用1而不是True
            }
            response, headers = await self._make_request(url, params)

            if not response:  # 没有更多Repository
                break

            all_repos.extend(response)
            page += 1

            # 检查是否已到达最后一页
            link_header = headers.get('Link', '')
            if 'rel="next"' not in link_header:
                break

        # 如果需要，截取到MAX_REPOS数量并转换为Repository对象
        all_repos = all_repos[:MAX_REPOS]
        return [
            Repository(
                id=str(repo.get('id')),  # type: ignore[arg-type]
                full_name=repo.get('path_with_namespace'),  # type: ignore[arg-type]
                stargazers_count=repo.get('star_count'),
                git_provider=ProviderType.GITLAB,
                is_public=repo.get('visibility') == 'public',
            )
            for repo in all_repos
        ]

    async def get_suggested_tasks(self) -> list[SuggestedTask]:
        """
        获取认证用户在所有Repository中的建议任务。

        Returns:
            list[SuggestedTask]: 建议任务列表，包括：
            - 用户创建的Merge Request
            - 分配给用户的Issue
        """
        # 获取用户信息用于查询
        user = await self.get_user()
        username = user.login

        # 获取Merge Request的GraphQL查询
        query = """
        query GetUserTasks {
          currentUser {
            authoredMergeRequests(state: opened, sort: UPDATED_DESC, first: 100) {
              nodes {
                id
                iid
                title
                project {
                  fullPath
                }
                conflicts
                mergeStatus
                pipelines(first: 1) {
                  nodes {
                    status
                  }
                }
                discussions(first: 100) {
                  nodes {
                    notes {
                      nodes {
                        resolvable
                        resolved
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """

        try:
            tasks: list[SuggestedTask] = []

            # 使用GraphQL获取Merge Request
            response = await self.execute_graphql_query(query)
            data = response.get('currentUser', {})

            # 处理Merge Request
            merge_requests = data.get('authoredMergeRequests', {}).get('nodes', [])
            for mr in merge_requests:
                repo_name = mr.get('project', {}).get('fullPath', '')
                mr_number = mr.get('iid')
                title = mr.get('title', '')

                # 从默认任务类型开始
                task_type = TaskType.OPEN_PR

                # 检查特定状态
                if mr.get('conflicts'):
                    # 有合并冲突
                    task_type = TaskType.MERGE_CONFLICTS
                elif (
                    mr.get('pipelines', {}).get('nodes', [])
                    and mr.get('pipelines', {}).get('nodes', [])[0].get('status')
                    == 'FAILED'
                ):
                    # CI管道失败
                    task_type = TaskType.FAILING_CHECKS
                else:
                    # 检查未解决的评论
                    has_unresolved_comments = False
                    for discussion in mr.get('discussions', {}).get('nodes', []):
                        for note in discussion.get('notes', {}).get('nodes', []):
                            if note.get('resolvable') and not note.get('resolved'):
                                has_unresolved_comments = True
                                break
                        if has_unresolved_comments:
                            break

                    if has_unresolved_comments:
                        task_type = TaskType.UNRESOLVED_COMMENTS

                # 只有当任务类型不是OPEN_PR时才添加任务
                if task_type != TaskType.OPEN_PR:
                    tasks.append(
                        SuggestedTask(
                            git_provider=ProviderType.GITLAB,
                            task_type=task_type,
                            repo=repo_name,
                            issue_number=mr_number,
                            title=title,
                        )
                    )

            # 使用REST API获取分配的Issue
            url = f'{self.BASE_URL}/issues'
            params = {
                'assignee_username': username,
                'state': 'opened',
                'scope': 'assigned_to_me',
            }

            issues_response, _ = await self._make_request(
                method=RequestMethod.GET, url=url, params=params
            )

            # 处理Issue
            for issue in issues_response:
                # 从references中提取Repository名称
                repo_name = (
                    issue.get('references', {}).get('full', '').split('#')[0].strip()
                )
                issue_number = issue.get('iid')
                title = issue.get('title', '')

                tasks.append(
                    SuggestedTask(
                        git_provider=ProviderType.GITLAB,
                        task_type=TaskType.OPEN_ISSUE,
                        repo=repo_name,
                        issue_number=issue_number,
                        title=title,
                    )
                )

            return tasks
        except Exception:
            # 如果出现任何错误，返回空列表
            return []

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
        # URL编码Repository名称（将/替换为%2F）
        encoded_name = repository.replace('/', '%2F')

        url = f'{self.BASE_URL}/projects/{encoded_name}'
        repo, _ = await self._make_request(url)

        return Repository(
            id=str(repo.get('id')),
            full_name=repo.get('path_with_namespace'),
            stargazers_count=repo.get('star_count'),
            git_provider=ProviderType.GITLAB,
            is_public=repo.get('visibility') == 'public',
        )

    async def get_branches(self, repository: str) -> list[Branch]:
        """
        获取Repository的分支列表。
        
        Args:
            repository (str): Repository名称
            
        Returns:
            list[Branch]: 分支列表
        """
        # URL编码Repository名称
        encoded_name = repository.replace('/', '%2F')
        url = f'{self.BASE_URL}/projects/{encoded_name}/repository/branches'

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
                branch = Branch(
                    name=branch_data.get('name'),
                    commit_sha=branch_data.get('commit', {}).get('id', ''),
                    protected=branch_data.get('protected', False),
                    last_push_date=branch_data.get('commit', {}).get('committed_date'),
                )
                all_branches.append(branch)

            page += 1

            # 检查是否已到达最后一页
            link_header = headers.get('Link', '')
            if 'rel="next"' not in link_header:
                break

        return all_branches

    async def create_mr(
        self,
        id: int | str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str | None = None,
        labels: list[str] | None = None,
    ) -> str:
        """
        在GitLab中创建Merge Request。
        
        Args:
            id (int | str): 项目的ID或URL编码路径
            source_branch (str): 实现更改的分支名称
            target_branch (str): 希望将更改合并到的分支名称
            title (str): Merge Request的标题
            description (str | None): Merge Request的描述（可选）
            labels (list[str] | None): 应用到Merge Request的标签列表（可选）
            
        Returns:
            str: 成功时返回MR URL，失败时返回错误消息
        """
        # 如果需要，将字符串ID转换为URL编码路径
        project_id = str(id).replace('/', '%2F') if isinstance(id, str) else id
        url = f'{self.BASE_URL}/projects/{project_id}/merge_requests'

        # 如果未提供描述，设置默认描述
        if not description:
            description = f'Merging changes from {source_branch} into {target_branch}'

        # 准备请求负载
        payload = {
            'source_branch': source_branch,
            'target_branch': target_branch,
            'title': title,
            'description': description,
        }

        # 如果提供了标签，添加标签
        if labels and len(labels) > 0:
            payload['labels'] = ','.join(labels)

        # 发起POST请求创建MR
        response, _ = await self._make_request(
            url=url, params=payload, method=RequestMethod.POST
        )

        return response['web_url']


# 从环境变量获取GitLab服务类配置
gitlab_service_cls = os.environ.get(
    'OPENHANDS_GITLAB_SERVICE_CLS',
    'openhands.integrations.gitlab.gitlab_service.GitLabService',
)
# 使用get_impl获取实际的GitLab服务实现类
GitLabServiceImpl = get_impl(GitLabService, gitlab_service_cls)
