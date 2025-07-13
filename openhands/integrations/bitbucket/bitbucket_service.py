import base64
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
    User,
)
from openhands.server.types import AppMode
from openhands.utils.import_utils import get_impl


class BitBucketService(BaseGitService, GitService):
    """
    Bitbucket集成的GitService默认实现。

    这是OpenHands中的一个扩展点，允许应用程序自定义Bitbucket
    集成行为。应用程序可以通过以下方式替换自己的实现：
    1. 创建一个继承自GitService的类
    2. 实现所有必需的方法
    3. 设置server_config.bitbucket_service_class为该类的完全限定名

    该类通过openhands.server.shared.py中的get_impl()实例化。
    """

    BASE_URL = 'https://api.bitbucket.org/2.0'   # Bitbucket API基础URL
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
        初始化BitBucketService实例。
        
        Args:
            user_id (str | None): 用户ID
            external_auth_id (str | None): 外部认证ID
            external_auth_token (SecretStr | None): 外部认证token
            token (SecretStr | None): Bitbucket API token
            external_token_manager (bool): 是否使用外部token管理器
            base_domain (str | None): 自定义Bitbucket实例的域名
        """
        self.user_id = user_id
        self.external_token_manager = external_token_manager
        self.external_auth_id = external_auth_id
        self.external_auth_token = external_auth_token
        self.base_domain = base_domain or 'bitbucket.org'

        if token:
            self.token = token
        if base_domain:
            # 如果提供了自定义域名，更新API URL
            self.BASE_URL = f'https://api.{base_domain}/2.0'

    @property
    def provider(self) -> str:
        """
        返回服务提供商标识符。
        
        Returns:
            str: 'bitbucket'
        """
        return ProviderType.BITBUCKET.value

    async def get_latest_token(self) -> SecretStr | None:
        """
        获取用户的最新有效token。
        
        Returns:
            SecretStr | None: 当前token
        """
        return self.token

    def _has_token_expired(self, status_code: int) -> bool:
        """
        检查token是否已过期。
        
        Args:
            status_code (int): HTTP状态码
            
        Returns:
            bool: 如果状态码为401则返回True，表示token已过期
        """
        return status_code == 401

    async def _get_bitbucket_headers(self) -> dict[str, str]:
        """
        获取Bitbucket API请求的头信息。
        
        支持两种认证方式：
        1. Basic认证（username:password格式的token）
        2. Bearer认证（纯token）
        
        Returns:
            dict[str, str]: 包含Authorization和Accept头的字典
        """
        token_value = self.token.get_secret_value()

        # 检查token是否包含冒号，这表示它是username:password格式
        if ':' in token_value:
            # 使用Basic认证
            auth_str = base64.b64encode(token_value.encode()).decode()
            return {
                'Authorization': f'Basic {auth_str}',
                'Accept': 'application/json',
            }
        else:
            # 使用Bearer认证
            return {
                'Authorization': f'Bearer {token_value}',
                'Accept': 'application/json',
            }

    async def _make_request(
        self,
        url: str,
        params: dict | None = None,
        method: RequestMethod = RequestMethod.GET,
    ) -> tuple[Any, dict]:
        """
        向Bitbucket API发起请求。

        Args:
            url (str): 请求的URL
            params (dict | None): 请求的可选参数
            method (RequestMethod): 使用的HTTP方法

        Returns:
            tuple[Any, dict]: 包含响应数据和响应头的元组
        """
        try:
            async with httpx.AsyncClient() as client:
                bitbucket_headers = await self._get_bitbucket_headers()
                response = await self.execute_request(
                    client, url, bitbucket_headers, params, method
                )
                # 如果需要刷新且token已过期，则处理token刷新
                if self.refresh and self._has_token_expired(response.status_code):
                    await self.get_latest_token()
                    bitbucket_headers = await self._get_bitbucket_headers()
                    response = await self.execute_request(
                        client=client,
                        url=url,
                        headers=bitbucket_headers,
                        params=params,
                        method=method,
                    )
                response.raise_for_status()
                return response.json(), dict(response.headers)
        except httpx.HTTPStatusError as e:
            raise self.handle_http_status_error(e)
        except httpx.HTTPError as e:
            raise self.handle_http_error(e)

    async def get_user(self) -> User:
        """
        获取认证用户的信息。
        
        Returns:
            User: 用户信息对象
        """
        url = f'{self.BASE_URL}/user'
        data, _ = await self._make_request(url)

        account_id = data.get('account_id', '')

        return User(
            id=account_id,
            login=data.get('username', ''),
            avatar_url=data.get('links', {}).get('avatar', {}).get('href', ''),
            name=data.get('display_name'),
            email=None,  # Bitbucket API在此端点中不返回邮箱
        )

    async def search_repositories(
        self,
        query: str,
        per_page: int,
        sort: str,
        order: str,
    ) -> list[Repository]:
        """
        搜索Repository。
        
        Args:
            query (str): 搜索查询字符串
            per_page (int): 每页返回的结果数量
            sort (str): 排序字段
            order (str): 排序顺序
            
        Returns:
            list[Repository]: 搜索结果Repository列表
            
        Note:
            Bitbucket没有像GitHub那样的专用搜索端点，所以返回空列表。
        """
        # Bitbucket没有像GitHub那样的专用搜索端点
        return []

    async def _fetch_paginated_data(
        self, url: str, params: dict, max_items: int
    ) -> list[dict]:
        """
        为Bitbucket API获取带分页支持的数据。

        Args:
            url (str): API端点URL
            params (dict): 请求的查询参数
            max_items (int): 要获取的最大项目数量

        Returns:
            list[dict]: 来自所有页面的数据项列表
        """
        all_items: list[dict] = []
        current_url = url

        while current_url and len(all_items) < max_items:
            response, _ = await self._make_request(current_url, params)

            # 从响应中提取项目
            page_items = response.get('values', [])
            if not page_items:  # 没有更多项目
                break

            all_items.extend(page_items)

            # 从响应中获取下一页URL
            current_url = response.get('next')

            # 清除后续请求的参数，因为下一页URL已经包含所有参数
            params = {}

        return all_items[:max_items]  # 如果需要，截取到max_items

    async def get_repositories(self, sort: str, app_mode: AppMode) -> list[Repository]:
        """
        使用workspace端点获取认证用户的Repository。

        此方法通过遍历用户的workspace并从每个workspace获取Repository
        来获取用户有权访问的所有Repository（公开和私有）。
        这种方法比之前分别调用公开和私有Repository的实现更全面、更高效。
        
        Args:
            sort (str): 排序方式
            app_mode (AppMode): 应用模式
            
        Returns:
            list[Repository]: 用户的Repository列表
        """
        MAX_REPOS = 1000        # 最大Repository数量
        PER_PAGE = 100          # Bitbucket API允许的每页最大数量
        repositories: list[Repository] = []

        # 使用分页获取用户的workspace
        workspaces_url = f'{self.BASE_URL}/workspaces'
        workspaces = await self._fetch_paginated_data(workspaces_url, {}, MAX_REPOS)

        for workspace in workspaces:
            workspace_slug = workspace.get('slug')
            if not workspace_slug:
                continue

            # 使用分页获取此workspace的Repository
            workspace_repos_url = f'{self.BASE_URL}/repositories/{workspace_slug}'

            # 将排序参数映射到Bitbucket API兼容的值，并确保降序排列
            # 以便在顶部显示最近更改的Repository
            bitbucket_sort = sort
            if sort == 'pushed':
                # Bitbucket不支持'pushed'，使用'updated_on'代替
                bitbucket_sort = (
                    '-updated_on'  # 使用负号前缀表示降序
                )
            elif sort == 'updated':
                bitbucket_sort = '-updated_on'
            elif sort == 'created':
                bitbucket_sort = '-created_on'
            elif sort == 'full_name':
                bitbucket_sort = 'name'  # Bitbucket使用'name'而不是'full_name'
            else:
                # 默认按最近更新排序
                bitbucket_sort = '-updated_on'

            params = {
                'pagelen': PER_PAGE,
                'sort': bitbucket_sort,
            }

            # 使用分页获取此workspace的所有Repository
            workspace_repos = await self._fetch_paginated_data(
                workspace_repos_url, params, MAX_REPOS - len(repositories)
            )

            for repo in workspace_repos:
                uuid = repo.get('uuid', '')
                repositories.append(
                    Repository(
                        id=uuid,
                        full_name=f'{repo.get("workspace", {}).get("slug", "")}/{repo.get("slug", "")}',
                        git_provider=ProviderType.BITBUCKET,
                        is_public=repo.get('is_private', True) is False,
                        stargazers_count=None,  # Bitbucket没有星标功能
                        pushed_at=repo.get('updated_on'),
                    )
                )

                # 如果已达到最大Repository数量，停止
                if len(repositories) >= MAX_REPOS:
                    break

            # 如果已达到最大Repository数量，停止
            if len(repositories) >= MAX_REPOS:
                break

        return repositories

    async def get_suggested_tasks(self) -> list[SuggestedTask]:
        """
        获取认证用户在所有Repository中的建议任务。
        
        Returns:
            list[SuggestedTask]: 建议任务列表
            
        Note:
            TODO: 实现建议任务功能
        """
        # TODO: 实现建议任务
        return []

    async def get_repository_details_from_repo_name(
        self, repository: str
    ) -> Repository:
        """
        根据Repository名称获取所有Repository详细信息。
        
        Args:
            repository (str): Repository名称（格式：owner/repo）
            
        Returns:
            Repository: Repository详细信息
            
        Raises:
            ValueError: 当Repository名称格式无效时抛出
        """
        # 从Repository字符串中提取owner和repo（例如："owner/repo"）
        parts = repository.split('/')
        if len(parts) < 2:
            raise ValueError(f'Invalid repository name: {repository}')

        owner = parts[-2]
        repo = parts[-1]

        url = f'{self.BASE_URL}/repositories/{owner}/{repo}'
        data, _ = await self._make_request(url)

        uuid = data.get('uuid', '')
        return Repository(
            id=uuid,
            full_name=f'{data.get("workspace", {}).get("slug", "")}/{data.get("slug", "")}',
            git_provider=ProviderType.BITBUCKET,
            is_public=data.get('is_private', True) is False,
            stargazers_count=None,  # Bitbucket没有星标功能
            pushed_at=data.get('updated_on'),
        )

    async def get_branches(self, repository: str) -> list[Branch]:
        """
        获取Repository的分支。
        
        Args:
            repository (str): Repository名称（格式：owner/repo）
            
        Returns:
            list[Branch]: Repository的分支列表
            
        Raises:
            ValueError: 当Repository名称格式无效时抛出
        """
        # 从Repository字符串中提取owner和repo（例如："owner/repo"）
        parts = repository.split('/')
        if len(parts) < 2:
            raise ValueError(f'Invalid repository name: {repository}')

        owner = parts[-2]
        repo = parts[-1]

        url = f'{self.BASE_URL}/repositories/{owner}/{repo}/refs/branches'

        # 设置最大分支数量（类似于GitHub/GitLab实现）
        MAX_BRANCHES = 1000
        PER_PAGE = 100

        params = {
            'pagelen': PER_PAGE,
            'sort': '-target.date',  # 按最近提交日期排序，降序
        }

        # 使用分页获取所有分支
        branch_data = await self._fetch_paginated_data(url, params, MAX_BRANCHES)

        branches = []
        for branch in branch_data:
            branches.append(
                Branch(
                    name=branch.get('name', ''),
                    commit_sha=branch.get('target', {}).get('hash', ''),
                    protected=False,  # Bitbucket在API中不暴露此信息
                    last_push_date=branch.get('target', {}).get('date', None),
                )
            )

        return branches

    async def create_pr(
        self,
        repo_name: str,
        source_branch: str,
        target_branch: str,
        title: str,
        body: str | None = None,
        draft: bool = False,
    ) -> str:
        """
        在Bitbucket中创建Pull Request。

        Args:
            repo_name (str): Repository名称，格式为"workspace/repo"
            source_branch (str): 源分支名称
            target_branch (str): 目标分支名称
            title (str): Pull Request的标题
            body (str | None): Pull Request的描述
            draft (bool): 是否创建草稿Pull Request

        Returns:
            str: 创建的Pull Request的URL
            
        Raises:
            ValueError: 当Repository名称格式无效时抛出
        """
        # 从Repository字符串中提取owner和repo（例如："owner/repo"）
        parts = repo_name.split('/')
        if len(parts) < 2:
            raise ValueError(f'Invalid repository name: {repo_name}')

        owner = parts[-2]
        repo = parts[-1]

        url = f'{self.BASE_URL}/repositories/{owner}/{repo}/pullrequests'

        payload = {
            'title': title,
            'description': body or '',
            'source': {'branch': {'name': source_branch}},
            'destination': {'branch': {'name': target_branch}},
            'close_source_branch': False,
            'draft': draft,
        }

        data, _ = await self._make_request(
            url=url, params=payload, method=RequestMethod.POST
        )

        # 返回Pull Request的URL
        return data.get('links', {}).get('html', {}).get('href', '')


# 从环境变量获取Bitbucket服务类配置
bitbucket_service_cls = os.environ.get(
    'OPENHANDS_BITBUCKET_SERVICE_CLS',
    'openhands.integrations.bitbucket.bitbucket_service.BitBucketService',
)
# 使用get_impl获取实际的Bitbucket服务实现类
BitBucketServiceImpl = get_impl(BitBucketService, bitbucket_service_cls)
