from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Protocol

from httpx import AsyncClient, HTTPError, HTTPStatusError
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel, SecretStr

from openhands.core.logger import openhands_logger as logger
from openhands.server.types import AppMode


class ProviderType(Enum):
    """
    Git服务提供商类型枚举。
    
    定义了OpenHands支持的Git服务提供商类型。
    """
    GITHUB = 'github'       # GitHub服务
    GITLAB = 'gitlab'       # GitLab服务
    BITBUCKET = 'bitbucket' # Bitbucket服务


class TaskType(str, Enum):
    """
    任务类型枚举。
    
    定义了系统能够识别和处理的各种任务类型，主要用于建议任务功能。
    """
    MERGE_CONFLICTS = 'MERGE_CONFLICTS'           # 合并冲突任务
    FAILING_CHECKS = 'FAILING_CHECKS'             # 失败的检查任务（CI/CD失败）
    UNRESOLVED_COMMENTS = 'UNRESOLVED_COMMENTS'   # 未解决的评论任务
    OPEN_ISSUE = 'OPEN_ISSUE'                     # 开放的Issue任务
    OPEN_PR = 'OPEN_PR'                           # 开放的Pull Request任务


class SuggestedTask(BaseModel):
    """
    建议任务模型。
    
    表示系统为用户建议的待处理任务，包含任务的基本信息和相关metadata。
    """
    git_provider: ProviderType  # Git服务提供商类型
    task_type: TaskType         # 任务类型
    repo: str                   # Repository名称
    issue_number: int           # Issue或PR的编号
    title: str                  # 任务标题

    def get_provider_terms(self) -> dict:
        """
        获取不同Git服务提供商的术语映射。
        
        根据当前的git_provider返回对应服务商使用的术语，
        用于在UI和提示中显示正确的术语。
        
        Returns:
            dict: 包含提供商特定术语的字典，包括：
                - requestType: 请求类型的完整名称
                - requestTypeShort: 请求类型的缩写
                - apiName: API名称
                - tokenEnvVar: 环境变量名称
                - ciSystem: CI系统名称
                - ciProvider: CI提供商名称
                - requestVerb: 请求动词
        """
        if self.git_provider == ProviderType.GITLAB:
            return {
                'requestType': 'Merge Request',
                'requestTypeShort': 'MR',
                'apiName': 'GitLab API',
                'tokenEnvVar': 'GITLAB_TOKEN',
                'ciSystem': 'CI pipelines',
                'ciProvider': 'GitLab',
                'requestVerb': 'merge request',
            }
        elif self.git_provider == ProviderType.GITHUB:
            return {
                'requestType': 'Pull Request',
                'requestTypeShort': 'PR',
                'apiName': 'GitHub API',
                'tokenEnvVar': 'GITHUB_TOKEN',
                'ciSystem': 'GitHub Actions',
                'ciProvider': 'GitHub',
                'requestVerb': 'pull request',
            }
        elif self.git_provider == ProviderType.BITBUCKET:
            return {
                'requestType': 'Pull Request',
                'requestTypeShort': 'PR',
                'apiName': 'Bitbucket API',
                'tokenEnvVar': 'BITBUCKET_TOKEN',
                'ciSystem': 'Bitbucket Pipelines',
                'ciProvider': 'Bitbucket',
                'requestVerb': 'pull request',
            }

        raise ValueError(f'Provider {self.git_provider} for suggested task prompts')

    def get_prompt_for_task(self) -> str:
        """
        根据任务类型生成对应的提示文本。
        
        使用Jinja2模板引擎根据任务类型和Git服务提供商生成
        相应的任务处理提示。
        
        Returns:
            str: 渲染后的提示文本
            
        Raises:
            ValueError: 当遇到不支持的任务类型时抛出
        """
        task_type = self.task_type
        issue_number = self.issue_number
        repo = self.repo

        # 创建Jinja2环境，用于模板渲染
        env = Environment(
            loader=FileSystemLoader('openhands/integrations/templates/suggested_task')
        )

        template = None
        # 根据任务类型选择对应的模板
        if task_type == TaskType.MERGE_CONFLICTS:
            template = env.get_template('merge_conflict_prompt.j2')
        elif task_type == TaskType.FAILING_CHECKS:
            template = env.get_template('failing_checks_prompt.j2')
        elif task_type == TaskType.UNRESOLVED_COMMENTS:
            template = env.get_template('unresolved_comments_prompt.j2')
        elif task_type == TaskType.OPEN_ISSUE:
            template = env.get_template('open_issue_prompt.j2')
        else:
            raise ValueError(f'Unsupported task type: {task_type}')

        # 获取提供商特定的术语
        terms = self.get_provider_terms()

        # 渲染模板并返回结果
        return template.render(issue_number=issue_number, repo=repo, **terms)


class User(BaseModel):
    """
    用户信息模型。
    
    表示从Git服务提供商API获取的用户信息。
    """
    id: str                      # 用户ID
    login: str                   # 用户登录名/用户名
    avatar_url: str              # 头像URL
    company: str | None = None   # 公司信息（可选）
    name: str | None = None      # 用户真实姓名（可选）
    email: str | None = None     # 邮箱地址（可选）


class Branch(BaseModel):
    """
    分支信息模型。
    
    表示Git Repository中的分支信息。
    """
    name: str                           # 分支名称
    commit_sha: str                     # 最新commit的SHA值
    protected: bool                     # 是否为受保护分支
    last_push_date: str | None = None   # 最后推送日期（ISO 8601格式）


class Repository(BaseModel):
    """
    Repository信息模型。
    
    表示Git Repository的基本信息和metadata。
    """
    id: str                             # Repository ID
    full_name: str                      # Repository全名（owner/repo格式）
    git_provider: ProviderType          # Git服务提供商类型
    is_public: bool                     # 是否为公开Repository
    stargazers_count: int | None = None # 星标数量（可选）
    link_header: str | None = None      # 链接头信息（用于分页）
    pushed_at: str | None = None        # 最后推送时间（ISO 8601格式）


class AuthenticationError(ValueError):
    """
    认证错误异常。
    
    当GitHub认证出现问题时抛出的异常。
    """
    pass


class UnknownException(ValueError):
    """
    未知异常。
    
    当与GitHub通信出现未知问题时抛出的异常。
    """
    pass


class RateLimitError(ValueError):
    """
    速率限制错误异常。
    
    当Git服务提供商的API速率限制被超过时抛出的异常。
    """
    pass


class RequestMethod(Enum):
    """
    HTTP请求方法枚举。
    
    定义支持的HTTP请求方法类型。
    """
    POST = 'post'  # POST请求
    GET = 'get'    # GET请求


class BaseGitService(ABC):
    """
    Git服务的抽象基类。
    
    定义了所有Git服务实现都必须遵循的基本接口和通用方法。
    """
    
    @property
    def provider(self) -> str:
        """
        服务提供商标识符属性。
        
        Returns:
            str: 服务提供商的字符串标识符
            
        Raises:
            NotImplementedError: 子类必须实现此属性
        """
        raise NotImplementedError('Subclasses must implement the provider property')

    # 用于满足mypy对抽象类定义的要求的方法
    @abstractmethod
    async def _make_request(
        self,
        url: str,
        params: dict | None = None,
        method: RequestMethod = RequestMethod.GET,
    ) -> tuple[Any, dict]:
        """
        执行HTTP请求的抽象方法。
        
        子类必须实现此方法来处理具体的HTTP请求逻辑。
        
        Args:
            url (str): 请求的URL
            params (dict | None): 请求参数
            method (RequestMethod): HTTP请求方法
            
        Returns:
            tuple[Any, dict]: 包含响应数据和头信息的元组
        """
        ...

    async def execute_request(
        self,
        client: AsyncClient,
        url: str,
        headers: dict,
        params: dict | None,
        method: RequestMethod = RequestMethod.GET,
    ):
        """
        执行HTTP请求的通用方法。
        
        根据指定的请求方法执行实际的HTTP请求。
        
        Args:
            client (AsyncClient): HTTP客户端实例
            url (str): 请求URL
            headers (dict): 请求头
            params (dict | None): 请求参数
            method (RequestMethod): HTTP请求方法
            
        Returns:
            HTTP响应对象
        """
        if method == RequestMethod.POST:
            # 对于POST请求，参数作为JSON body发送
            return await client.post(url, headers=headers, json=params)
        # 对于GET请求，参数作为查询参数发送
        return await client.get(url, headers=headers, params=params)

    def handle_http_status_error(
        self, e: HTTPStatusError
    ) -> AuthenticationError | RateLimitError | UnknownException:
        """
        处理HTTP状态错误。
        
        根据HTTP状态码将HTTPStatusError转换为相应的业务异常。
        
        Args:
            e (HTTPStatusError): HTTP状态错误
            
        Returns:
            AuthenticationError | RateLimitError | UnknownException: 
            对应的业务异常
        """
        if e.response.status_code == 401:
            # 401状态码表示认证失败
            return AuthenticationError(f'Invalid {self.provider} token')
        elif e.response.status_code == 429:
            # 429状态码表示速率限制被超过
            logger.warning(f'Rate limit exceeded on {self.provider} API: {e}')
            return RateLimitError('GitHub API rate limit exceeded')

        # 其他状态码归类为未知错误
        logger.warning(f'Status error on {self.provider} API: {e}')
        return UnknownException(f'Unknown error: {e}')

    def handle_http_error(self, e: HTTPError) -> UnknownException:
        """
        处理HTTP通信错误。
        
        将HTTPError转换为UnknownException业务异常。
        
        Args:
            e (HTTPError): HTTP通信错误
            
        Returns:
            UnknownException: 未知异常
        """
        logger.warning(f'HTTP error on {self.provider} API: {type(e).__name__} : {e}')
        return UnknownException(f'HTTP error {type(e).__name__} : {e}')


class GitService(Protocol):
    """
    Git服务提供商接口协议。
    
    定义了Git服务提供商必须实现的接口方法。
    使用Protocol定义接口，支持结构化类型检查。
    """

    def __init__(
        self,
        user_id: str | None = None,
        token: SecretStr | None = None,
        external_auth_id: str | None = None,
        external_auth_token: SecretStr | None = None,
        external_token_manager: bool = False,
        base_domain: str | None = None,
    ) -> None:
        """
        初始化服务实例。
        
        Args:
            user_id (str | None): 用户ID
            token (SecretStr | None): 认证token
            external_auth_id (str | None): 外部认证ID
            external_auth_token (SecretStr | None): 外部认证token
            external_token_manager (bool): 是否使用外部token管理器
            base_domain (str | None): 服务基础域名
        """
        ...

    async def get_latest_token(self) -> SecretStr | None:
        """
        获取用户的最新有效token。
        
        Returns:
            SecretStr | None: 最新的有效token，如果没有则返回None
        """
        ...

    async def get_user(self) -> User:
        """
        获取认证用户的信息。
        
        Returns:
            User: 用户信息对象
        """
        ...

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
            per_page (int): 每页结果数量
            sort (str): 排序字段
            order (str): 排序顺序
            
        Returns:
            list[Repository]: Repository列表
        """
        ...

    async def get_repositories(self, sort: str, app_mode: AppMode) -> list[Repository]:
        """
        获取认证用户的Repository列表。
        
        Args:
            sort (str): 排序方式
            app_mode (AppMode): 应用模式
            
        Returns:
            list[Repository]: Repository列表
        """
        ...

    async def get_suggested_tasks(self) -> list[SuggestedTask]:
        """
        获取认证用户在所有Repository中的建议任务。
        
        Returns:
            list[SuggestedTask]: 建议任务列表
        """
        ...

    async def get_repository_details_from_repo_name(
        self, repository: str
    ) -> Repository:
        """
        根据Repository名称获取Repository详细信息。
        
        Args:
            repository (str): Repository名称
            
        Returns:
            Repository: Repository详细信息
        """

    async def get_branches(self, repository: str) -> list[Branch]:
        """
        获取Repository的分支列表。
        
        Args:
            repository (str): Repository名称
            
        Returns:
            list[Branch]: 分支列表
        """
