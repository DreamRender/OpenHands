import abc


class RuntimeBuilder(abc.ABC):
    """Runtime构建器的抽象基类
    
    这个抽象基类定义了Runtime镜像构建器必须实现的核心接口。
    所有具体的Runtime构建器（如Docker构建器、远程构建器等）都应该继承这个类
    并实现其抽象方法。
    
    Runtime构建器负责：
    1. 构建Runtime环境的容器镜像
    2. 检查镜像是否存在
    3. 处理构建过程中的错误和异常
    """
    
    @abc.abstractmethod
    def build(
        self,
        path: str,
        tags: list[str],
        platform: str | None = None,
        extra_build_args: list[str] | None = None,
    ) -> str:
        """构建Runtime镜像的抽象方法
        
        这是所有Runtime构建器必须实现的核心方法，用于构建容器镜像。
        
        Args:
            path (str): Runtime镜像构建目录的路径，包含Dockerfile和相关构建文件
            tags (list[str]): 应用到Runtime镜像的标签列表，例如：["repo:my-repo", "sha:my-sha"]
                              第一个标签通常是基于哈希的唯一标识，第二个标签可能是更通用的版本标签
            platform (str, optional): 构建的目标平台架构，例如"linux/amd64"。默认为None
            extra_build_args (list[str], optional): 传递给构建器的额外构建参数列表。默认为None
        
        Returns:
            str: 构建完成后Runtime镜像的名称:标签格式，例如"repo:sha"
                 这个返回值可能与输入的tags不同，因为构建器可能会修改标签
                 （例如添加注册表前缀）。返回值应该用于后续操作（如`docker run`）
        
        Raises:
            AgentRuntimeBuildError: 当构建失败时抛出此异常
        
        Note:
            这是一个抽象方法，子类必须提供具体实现
        """
        pass

    @abc.abstractmethod
    def image_exists(self, image_name: str, pull_from_repo: bool = True) -> bool:
        """检查Runtime镜像是否存在的抽象方法
        
        检查指定的Runtime镜像是否在本地或远程仓库中存在。
        
        Args:
            image_name (str): Runtime镜像的名称，格式为"仓库:标签"，例如"repo:sha"
            pull_from_repo (bool): 当镜像在本地不存在时，是否尝试从远程仓库拉取
                                  True表示会尝试拉取，False表示只检查本地
        
        Returns:
            bool: Runtime镜像是否存在
                 True表示镜像存在（本地或远程），False表示镜像不存在
        
        Note:
            这是一个抽象方法，子类必须提供具体实现
            具体的检查逻辑（本地优先还是远程优先）由子类决定
        """
        pass