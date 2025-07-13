from __future__ import annotations

from types import MappingProxyType
from typing import Annotated, Any, Coroutine, Literal, overload

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    WithJsonSchema,
)

from openhands.core.logger import openhands_logger as logger
from openhands.events.action.action import Action
from openhands.events.action.commands import CmdRunAction
from openhands.events.stream import EventStream
from openhands.integrations.bitbucket.bitbucket_service import BitBucketServiceImpl
from openhands.integrations.github.github_service import GithubServiceImpl
from openhands.integrations.gitlab.gitlab_service import GitLabServiceImpl
from openhands.integrations.service_types import (
    AuthenticationError,
    Branch,
    GitService,
    ProviderType,
    Repository,
    SuggestedTask,
    User,
)
from openhands.server.types import AppMode


class ProviderToken(BaseModel):
    """
    服务提供商token模型。
    
    用于存储和管理Git服务提供商的认证token信息。
    """
    token: SecretStr | None = Field(default=None)      # 认证token
    user_id: str | None = Field(default=None)          # 用户ID
    host: str | None = Field(default=None)             # 服务主机地址

    model_config = ConfigDict(
        frozen=True,            # 使整个模型不可变
        validate_assignment=True, # 启用赋值验证
    )

    @classmethod
    def from_value(cls, token_value: ProviderToken | dict[str, str]) -> ProviderToken:
        """
        工厂方法：从各种输入类型创建ProviderToken实例。
        
        Args:
            token_value (ProviderToken | dict[str, str]): 
                可以是ProviderToken实例或包含token信息的字典
                
        Returns:
            ProviderToken: 创建的ProviderToken实例
            
        Raises:
            ValueError: 当输入类型不支持时抛出
        """
        if isinstance(token_value, cls):
            # 如果已经是ProviderToken实例，直接返回
            return token_value
        elif isinstance(token_value, dict):
            # 从字典创建实例
            token_str = token_value.get('token', '')
            # 如果token被设置为None，则使用空字符串覆盖
            # 因为不能向SecretStr传递None
            if token_str is None:
                token_str = ''  # type: ignore[unreachable]
            user_id = token_value.get('user_id')
            host = token_value.get('host')
            return cls(token=SecretStr(token_str), user_id=user_id, host=host)

        else:
            raise ValueError('Unsupported Provider token type')


class CustomSecret(BaseModel):
    """
    自定义密钥模型。
    
    用于存储用户自定义的密钥信息，包含密钥值和描述。
    """
    secret: SecretStr = Field(default_factory=lambda: SecretStr(''))  # 密钥值
    description: str = Field(default='')                              # 密钥描述

    model_config = ConfigDict(
        frozen=True,            # 使整个模型不可变
        validate_assignment=True, # 启用赋值验证
    )

    @classmethod
    def from_value(cls, secret_value: CustomSecret | dict[str, str]) -> CustomSecret:
        """
        工厂方法：从各种输入类型创建CustomSecret实例。
        
        Args:
            secret_value (CustomSecret | dict[str, str]): 
                可以是CustomSecret实例或包含密钥信息的字典
                
        Returns:
            CustomSecret: 创建的CustomSecret实例
            
        Raises:
            ValueError: 当输入类型不支持时抛出
        """
        if isinstance(secret_value, CustomSecret):
            # 如果已经是CustomSecret实例，直接返回
            return secret_value
        elif isinstance(secret_value, dict):
            # 从字典创建实例
            secret = secret_value.get('secret', '')
            description = secret_value.get('description', '')
            return cls(secret=SecretStr(secret), description=description)

        else:
            raise ValueError('Unsupport Provider token type')


# 定义provider token的类型映射
PROVIDER_TOKEN_TYPE = MappingProxyType[ProviderType, ProviderToken]
# 定义自定义密钥的类型映射
CUSTOM_SECRETS_TYPE = MappingProxyType[str, CustomSecret]
# 带JSON Schema注解的provider token类型
PROVIDER_TOKEN_TYPE_WITH_JSON_SCHEMA = Annotated[
    PROVIDER_TOKEN_TYPE,
    WithJsonSchema({'type': 'object', 'additionalProperties': {'type': 'string'}}),
]
# 带JSON Schema注解的自定义密钥类型
CUSTOM_SECRETS_TYPE_WITH_JSON_SCHEMA = Annotated[
    CUSTOM_SECRETS_TYPE,
    WithJsonSchema({'type': 'object', 'additionalProperties': {'type': 'string'}}),
]


class ProviderHandler:
    """
    服务提供商处理器。
    
    负责管理和协调多个Git服务提供商的集成，提供统一的接口
    来处理不同服务提供商的API调用和认证。
    """
    
    def __init__(
        self,
        provider_tokens: PROVIDER_TOKEN_TYPE,
        external_auth_id: str | None = None,
        external_auth_token: SecretStr | None = None,
        external_token_manager: bool = False,
    ):
        """
        初始化ProviderHandler。
        
        Args:
            provider_tokens (PROVIDER_TOKEN_TYPE): provider token映射
            external_auth_id (str | None): 外部认证ID
            external_auth_token (SecretStr | None): 外部认证token
            external_token_manager (bool): 是否使用外部token管理器
            
        Raises:
            TypeError: 当provider_tokens不是MappingProxyType时抛出
        """
        if not isinstance(provider_tokens, MappingProxyType):
            raise TypeError(
                f'provider_tokens must be a MappingProxyType, got {type(provider_tokens).__name__}'
            )

        # 服务类映射：将ProviderType映射到对应的服务实现类
        self.service_class_map: dict[ProviderType, type[GitService]] = {
            ProviderType.GITHUB: GithubServiceImpl,
            ProviderType.GITLAB: GitLabServiceImpl,
            ProviderType.BITBUCKET: BitBucketServiceImpl,
        }

        self.external_auth_id = external_auth_id
        self.external_auth_token = external_auth_token
        self.external_token_manager = external_token_manager
        self._provider_tokens = provider_tokens

    @property
    def provider_tokens(self) -> PROVIDER_TOKEN_TYPE:
        """
        只读访问provider tokens。
        
        Returns:
            PROVIDER_TOKEN_TYPE: provider token映射
        """
        return self._provider_tokens

    def _get_service(self, provider: ProviderType) -> GitService:
        """
        为指定的provider实例化服务的辅助方法。
        
        Args:
            provider (ProviderType): 服务提供商类型
            
        Returns:
            GitService: 对应的服务实例
        """
        token = self.provider_tokens[provider]
        service_class = self.service_class_map[provider]
        return service_class(
            user_id=token.user_id,
            external_auth_id=self.external_auth_id,
            external_auth_token=self.external_auth_token,
            token=token.token,
            external_token_manager=self.external_token_manager,
            base_domain=token.host,
        )

    async def get_user(self) -> User:
        """
        从第一个可用的provider获取用户信息。
        
        尝试使用所有配置的provider获取用户信息，
        返回第一个成功获取的用户信息。
        
        Returns:
            User: 用户信息
            
        Raises:
            AuthenticationError: 当没有有效的provider token时抛出
        """
        for provider in self.provider_tokens:
            try:
                service = self._get_service(provider)
                return await service.get_user()
            except Exception:
                # 如果当前provider失败，继续尝试下一个
                continue
        raise AuthenticationError('Need valid provider token')

    async def _get_latest_provider_token(
        self, provider: ProviderType
    ) -> SecretStr | None:
        """
        从服务获取最新的token。
        
        Args:
            provider (ProviderType): 服务提供商类型
            
        Returns:
            SecretStr | None: 最新的token，如果获取失败则返回None
        """
        service = self._get_service(provider)
        return await service.get_latest_token()

    async def get_repositories(self, sort: str, app_mode: AppMode) -> list[Repository]:
        """
        从所有provider获取Repository列表。
        
        Args:
            sort (str): 排序方式
            app_mode (AppMode): 应用模式
            
        Returns:
            list[Repository]: 所有provider的Repository列表合集
        """
        all_repos: list[Repository] = []
        for provider in self.provider_tokens:
            try:
                service = self._get_service(provider)
                service_repos = await service.get_repositories(sort, app_mode)
                all_repos.extend(service_repos)
            except Exception as e:
                # 记录警告但继续处理其他provider
                logger.warning(f'Error fetching repos from {provider}: {e}')

        return all_repos

    async def get_suggested_tasks(self) -> list[SuggestedTask]:
        """
        从所有provider获取建议任务。
        
        Returns:
            list[SuggestedTask]: 所有provider的建议任务列表合集
        """
        tasks: list[SuggestedTask] = []
        for provider in self.provider_tokens:
            try:
                service = self._get_service(provider)
                service_repos = await service.get_suggested_tasks()
                tasks.extend(service_repos)
            except Exception as e:
                # 记录警告但继续处理其他provider
                logger.warning(f'Error fetching repos from {provider}: {e}')

        return tasks

    async def search_repositories(
        self,
        query: str,
        per_page: int,
        sort: str,
        order: str,
    ) -> list[Repository]:
        """
        在所有provider中搜索Repository。
        
        Args:
            query (str): 搜索查询字符串
            per_page (int): 每页结果数量
            sort (str): 排序字段
            order (str): 排序顺序
            
        Returns:
            list[Repository]: 搜索结果Repository列表
        """
        all_repos: list[Repository] = []
        for provider in self.provider_tokens:
            try:
                service = self._get_service(provider)
                service_repos = await service.search_repositories(
                    query, per_page, sort, order
                )
                all_repos.extend(service_repos)
            except Exception as e:
                # 记录警告但继续处理其他provider
                logger.warning(f'Error searching repos from {provider}: {e}')
                continue

        return all_repos

    async def set_event_stream_secrets(
        self,
        event_stream: EventStream,
        env_vars: dict[ProviderType, SecretStr] | None = None,
    ) -> None:
        """
        确保最新的provider token从event stream中被屏蔽。
        
        当provider token在runtime中首次初始化或使用最新的有效token重新导出时调用。
        
        Args:
            event_stream (EventStream): Agent Session的event stream
            env_vars (dict[ProviderType, SecretStr] | None): 
                需要更新的provider和其token的字典
        """
        if env_vars:
            # 如果提供了特定的环境变量，则暴露它们
            exposed_env_vars = self.expose_env_vars(env_vars)
        else:
            # 否则获取所有环境变量
            exposed_env_vars = await self.get_env_vars(expose_secrets=True)
        # 在event stream中设置密钥
        event_stream.set_secrets(exposed_env_vars)

    def expose_env_vars(
        self, env_secrets: dict[ProviderType, SecretStr]
    ) -> dict[str, str]:
        """
        返回环境密钥的字符串值而不是类型化值。
        
        在将密钥导出到runtime或在event stream中设置密钥之前调用。
        
        Args:
            env_secrets (dict[ProviderType, SecretStr]): provider密钥映射
            
        Returns:
            dict[str, str]: 暴露的环境变量映射
        """
        exposed_envs = {}
        for provider, token in env_secrets.items():
            env_key = ProviderHandler.get_provider_env_key(provider)
            exposed_envs[env_key] = token.get_secret_value()

        return exposed_envs

    @overload
    def get_env_vars(
        self,
        expose_secrets: Literal[True],
        providers: list[ProviderType] | None = ...,
        get_latest: bool = False,
    ) -> Coroutine[Any, Any, dict[str, str]]: ...

    @overload
    def get_env_vars(
        self,
        expose_secrets: Literal[False],
        providers: list[ProviderType] | None = ...,
        get_latest: bool = False,
    ) -> Coroutine[Any, Any, dict[ProviderType, SecretStr]]: ...

    async def get_env_vars(
        self,
        expose_secrets: bool = False,
        providers: list[ProviderType] | None = None,
        get_latest: bool = False,
    ) -> dict[ProviderType, SecretStr] | dict[str, str]:
        """
        从ProviderHandler对象检索provider token。
        
        在runtime中初始化/导出新的provider token时使用。
        
        Args:
            expose_secrets (bool): 标志，返回字符串而不是密钥类型
            providers (list[ProviderType] | None): 
                返回传入列表的provider token，否则返回所有可用的provider
            get_latest (bool): 
                如果为True则获取provider的最新有效token，否则获取现有的
                
        Returns:
            dict[ProviderType, SecretStr] | dict[str, str]: 
                环境变量映射，类型取决于expose_secrets参数
        """
        if not self.provider_tokens:
            return {}

        env_vars: dict[ProviderType, SecretStr] = {}
        all_providers = [provider for provider in ProviderType]
        provider_list = providers if providers else all_providers

        for provider in provider_list:
            if provider in self.provider_tokens:
                token = (
                    self.provider_tokens[provider].token
                    if self.provider_tokens
                    else SecretStr('')
                )

                if get_latest:
                    # 获取最新token
                    token = await self._get_latest_provider_token(provider)

                if token:
                    env_vars[provider] = token

        if not expose_secrets:
            return env_vars

        return self.expose_env_vars(env_vars)

    @classmethod
    def check_cmd_action_for_provider_token_ref(
        cls, event: Action
    ) -> list[ProviderType]:
        """
        检测Agent运行Action是否使用了provider token（例如$GITHUB_TOKEN）。
        
        返回被Agent调用的provider列表。
        
        Args:
            event (Action): 要检查的Action事件
            
        Returns:
            list[ProviderType]: 被调用的provider列表
        """
        if not isinstance(event, CmdRunAction):
            return []

        called_providers = []
        for provider in ProviderType:
            # 检查命令中是否包含provider的环境变量名
            if ProviderHandler.get_provider_env_key(provider) in event.command.lower():
                called_providers.append(provider)

        return called_providers

    @classmethod
    def get_provider_env_key(cls, provider: ProviderType) -> str:
        """
        将ProviderType值映射到runtime中的环境变量名。
        
        Args:
            provider (ProviderType): 服务提供商类型
            
        Returns:
            str: 对应的环境变量名
        """
        return f'{provider.value}_token'.lower()

    async def verify_repo_provider(
        self, repository: str, specified_provider: ProviderType | None = None
    ) -> Repository:
        """
        验证Repository的provider。
        
        尝试从指定的或所有可用的provider中访问Repository。
        
        Args:
            repository (str): Repository名称
            specified_provider (ProviderType | None): 指定的服务提供商
            
        Returns:
            Repository: Repository详细信息
            
        Raises:
            AuthenticationError: 当无法访问Repository时抛出
        """
        errors = []

        if specified_provider:
            # 如果指定了provider，先尝试使用指定的provider
            try:
                service = self._get_service(specified_provider)
                return await service.get_repository_details_from_repo_name(repository)
            except Exception as e:
                errors.append(f'{specified_provider.value}: {str(e)}')

        # 尝试所有可用的provider
        for provider in self.provider_tokens:
            try:
                service = self._get_service(provider)
                return await service.get_repository_details_from_repo_name(repository)
            except Exception as e:
                errors.append(f'{provider.value}: {str(e)}')

        # 在抛出AuthenticationError之前记录所有累积的错误
        logger.error(
            f'Failed to access repository {repository} with all available providers. Errors: {"; ".join(errors)}'
        )
        raise AuthenticationError(f'Unable to access repo {repository}')

    async def get_branches(
        self, repository: str, specified_provider: ProviderType | None = None
    ) -> list[Branch]:
        """
        获取Repository的分支列表。
        
        Args:
            repository (str): Repository名称
            specified_provider (ProviderType | None): 可选的指定provider类型
            
        Returns:
            list[Branch]: Repository的分支列表
        """
        all_branches: list[Branch] = []

        if specified_provider:
            # 如果指定了provider，尝试使用指定的provider
            try:
                service = self._get_service(specified_provider)
                branches = await service.get_branches(repository)
                return branches
            except Exception as e:
                logger.warning(
                    f'Error fetching branches from {specified_provider}: {e}'
                )

        # 尝试所有可用的provider
        for provider in self.provider_tokens:
            try:
                service = self._get_service(provider)
                branches = await service.get_branches(repository)
                all_branches.extend(branches)
                # 如果找到了分支，不需要检查其他provider
                if all_branches:
                    break
            except Exception as e:
                logger.warning(f'Error fetching branches from {provider}: {e}')

        # 按最后推送日期排序（最新的在前）
        all_branches.sort(
            key=lambda b: b.last_push_date if b.last_push_date else '', reverse=True
        )

        # 将main/master分支移到顶部（如果存在）
        main_branches = []
        other_branches = []

        for branch in all_branches:
            if branch.name.lower() in ['main', 'master']:
                main_branches.append(branch)
            else:
                other_branches.append(branch)

        return main_branches + other_branches
