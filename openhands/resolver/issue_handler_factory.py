from openhands.core.config import LLMConfig
from openhands.integrations.provider import ProviderType
from openhands.resolver.interfaces.bitbucket import (
    BitbucketIssueHandler,
    BitbucketPRHandler,
)
from openhands.resolver.interfaces.github import GithubIssueHandler, GithubPRHandler
from openhands.resolver.interfaces.gitlab import GitlabIssueHandler, GitlabPRHandler
from openhands.resolver.interfaces.issue_definitions import (
    ServiceContextIssue,
    ServiceContextPR,
)


class IssueHandlerFactory:
    """Issue处理器工厂类
    
    这个工厂类负责根据不同的平台类型（GitHub、GitLab、Bitbucket）
    和issue类型（issue或PR）创建相应的处理器实例。
    采用工厂模式，简化了不同平台处理器的创建逻辑。
    """
    
    def __init__(
        self,
        owner: str,
        repo: str,
        token: str,
        username: str,
        platform: ProviderType,
        base_domain: str,
        issue_type: str,
        llm_config: LLMConfig,
    ) -> None:
        """初始化Issue处理器工厂
        
        Args:
            owner (str): Repository的所有者名称
            repo (str): Repository的名称
            token (str): 访问Repository的认证token
            username (str): 用户名
            platform (ProviderType): 平台类型（GitHub、GitLab或Bitbucket）
            base_domain (str): 平台的基础域名
            issue_type (str): Issue类型，可以是'issue'或'pr'
            llm_config (LLMConfig): 大语言模型的配置信息
        """
        self.owner = owner  # Repository所有者
        self.repo = repo  # Repository名称
        self.token = token  # 认证token
        self.username = username  # 用户名
        self.platform = platform  # 平台类型
        self.base_domain = base_domain  # 基础域名
        self.issue_type = issue_type  # Issue类型
        self.llm_config = llm_config  # LLM配置

    def create(self) -> ServiceContextIssue | ServiceContextPR:
        """创建相应的Issue或PR处理器
        
        根据issue_type和platform的组合，创建对应的处理器实例。
        支持GitHub、GitLab和Bitbucket三个平台，
        以及issue和PR两种类型。
        
        Returns:
            ServiceContextIssue | ServiceContextPR: 创建的处理器实例
            
        Raises:
            ValueError: 当平台类型不支持或issue类型无效时抛出异常
        """
        # 处理issue类型的请求
        if self.issue_type == 'issue':
            # 根据不同平台创建对应的Issue处理器
            if self.platform == ProviderType.GITHUB:
                return ServiceContextIssue(
                    GithubIssueHandler(
                        self.owner,
                        self.repo,
                        self.token,
                        self.username,
                        self.base_domain,
                    ),
                    self.llm_config,
                )
            elif self.platform == ProviderType.GITLAB:
                return ServiceContextIssue(
                    GitlabIssueHandler(
                        self.owner,
                        self.repo,
                        self.token,
                        self.username,
                        self.base_domain,
                    ),
                    self.llm_config,
                )
            elif self.platform == ProviderType.BITBUCKET:
                return ServiceContextIssue(
                    BitbucketIssueHandler(
                        self.owner,
                        self.repo,
                        self.token,
                        self.username,
                        self.base_domain,
                    ),
                    self.llm_config,
                )
            else:
                # 不支持的平台类型
                raise ValueError(f'Unsupported platform: {self.platform}')
                
        # 处理PR类型的请求
        elif self.issue_type == 'pr':
            # 根据不同平台创建对应的PR处理器
            if self.platform == ProviderType.GITHUB:
                return ServiceContextPR(
                    GithubPRHandler(
                        self.owner,
                        self.repo,
                        self.token,
                        self.username,
                        self.base_domain,
                    ),
                    self.llm_config,
                )
            elif self.platform == ProviderType.GITLAB:
                return ServiceContextPR(
                    GitlabPRHandler(
                        self.owner,
                        self.repo,
                        self.token,
                        self.username,
                        self.base_domain,
                    ),
                    self.llm_config,
                )
            elif self.platform == ProviderType.BITBUCKET:
                return ServiceContextPR(
                    BitbucketPRHandler(
                        self.owner,
                        self.repo,
                        self.token,
                        self.username,
                        self.base_domain,
                    ),
                    self.llm_config,
                )
            else:
                # 不支持的平台类型
                raise ValueError(f'Unsupported platform: {self.platform}')
        else:
            # 无效的issue类型
            raise ValueError(f'Invalid issue type: {self.issue_type}')
