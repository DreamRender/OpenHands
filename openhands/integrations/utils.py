from pydantic import SecretStr

from openhands.core.logger import openhands_logger as logger
from openhands.integrations.bitbucket.bitbucket_service import BitBucketService
from openhands.integrations.github.github_service import GitHubService
from openhands.integrations.gitlab.gitlab_service import GitLabService
from openhands.integrations.provider import ProviderType


async def validate_provider_token(
    token: SecretStr, base_domain: str | None = None
) -> ProviderType | None:
    """
    通过尝试从各种Git服务获取用户信息来确定token是属于GitHub、GitLab还是Bitbucket。
    
    这个函数会依次尝试使用提供的token访问GitHub、GitLab和Bitbucket的API，
    通过验证API调用是否成功来判断token的类型。
    
    Args:
        token (SecretStr): 需要验证的token
        base_domain (str | None, optional): 可选的服务基础域名。
                                           对于企业版Git服务，可以指定自定义域名。
                                           默认为None。
    
    Returns:
        ProviderType | None: 如果token有效，返回对应的provider类型：
                            - 'github' 如果是GitHub token
                            - 'gitlab' 如果是GitLab token  
                            - 'bitbucket' 如果是Bitbucket token
                            如果token对所有服务都无效，则返回None
    """
    # 跳过空token的验证
    if token is None:
        return None  # type: ignore[unreachable]

    # 首先尝试GitHub
    github_error = None
    try:
        # 创建GitHub服务实例
        github_service = GitHubService(token=token, base_domain=base_domain)
        # 验证访问权限
        await github_service.verify_access()
        return ProviderType.GITHUB
    except Exception as e:
        # 保存错误信息以便后续日志记录
        github_error = e

    # 接下来尝试GitLab
    gitlab_error = None
    try:
        # 创建GitLab服务实例
        gitlab_service = GitLabService(token=token, base_domain=base_domain)
        # 尝试获取用户信息来验证token
        await gitlab_service.get_user()
        return ProviderType.GITLAB
    except Exception as e:
        # 保存错误信息以便后续日志记录
        gitlab_error = e

    # 最后尝试Bitbucket
    bitbucket_error = None
    try:
        # 创建Bitbucket服务实例
        bitbucket_service = BitBucketService(token=token, base_domain=base_domain)
        # 尝试获取用户信息来验证token
        await bitbucket_service.get_user()
        return ProviderType.BITBUCKET
    except Exception as e:
        # 保存错误信息以便后续日志记录
        bitbucket_error = e

    # 如果所有尝试都失败，记录详细的错误信息
    logger.debug(
        f'Failed to validate token: {github_error} \n {gitlab_error} \n {bitbucket_error}'
    )

    # 返回None表示token对所有服务都无效
    return None
