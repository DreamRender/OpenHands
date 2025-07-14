"""
运行时构建模块。

该模块负责构建OpenHands运行时的Docker镜像，包括从不同基础镜像构建、
依赖管理和镜像标签管理等功能。
"""

import argparse
import hashlib
import os
import shutil
import string
import tempfile
from enum import Enum
from pathlib import Path

import docker
from dirhash import dirhash
from jinja2 import Environment, FileSystemLoader

import openhands
from openhands import __version__ as oh_version
from openhands.core.exceptions import AgentRuntimeBuildError
from openhands.core.logger import openhands_logger as logger
from openhands.runtime.builder import DockerRuntimeBuilder, RuntimeBuilder


class BuildFromImageType(Enum):
    """
    构建镜像的类型枚举。
    
    该枚举定义了三种不同的构建策略，每种策略在构建速度和依赖重用方面有所不同。
    """
    # 最慢：从基础镜像构建（不重用任何依赖）
    SCRATCH = 'scratch'
    # 中等速度：重用具有相同基础镜像和OH版本的最新镜像（许多依赖已经安装）
    VERSIONED = 'versioned'  
    # 最快：重用具有完全相同依赖的最新镜像（锁定文件）
    LOCK = 'lock'


def get_runtime_image_repo() -> str:
    """
    获取运行时镜像仓库地址。
    
    Returns:
        str: 运行时镜像仓库的URL地址
    """
    return os.getenv('OH_RUNTIME_RUNTIME_IMAGE_REPO', 'ghcr.io/all-hands-ai/runtime')


def _generate_dockerfile(
    base_image: str,
    build_from: BuildFromImageType = BuildFromImageType.SCRATCH,
    extra_deps: str | None = None,
) -> str:
    """
    基于基础镜像生成运行时镜像的Dockerfile内容。

    Args:
        base_image (str): 为运行时镜像提供的基础镜像
        build_from (BuildFromImageType): 运行时镜像的构建方法
        extra_deps (str | None): 额外的依赖项

    Returns:
        str: 生成的Dockerfile内容
    """
    # 创建Jinja2环境，从runtime_templates目录加载模板
    env = Environment(
        loader=FileSystemLoader(
            searchpath=os.path.join(os.path.dirname(__file__), 'runtime_templates')
        )
    )
    # 获取Dockerfile模板
    template = env.get_template('Dockerfile.j2')

    # 渲染Dockerfile模板
    dockerfile_content = template.render(
        base_image=base_image,
        build_from_scratch=build_from == BuildFromImageType.SCRATCH,
        build_from_versioned=build_from == BuildFromImageType.VERSIONED,
        extra_deps=extra_deps if extra_deps is not None else '',
    )
    return dockerfile_content


def get_runtime_image_repo_and_tag(base_image: str) -> tuple[str, str]:
    """
    获取与Docker镜像关联的Docker仓库和标签。

    Args:
        base_image (str): 基础Docker镜像的名称

    Returns:
        tuple[str, str]: Docker镜像的仓库和标签
    """
    # 如果提供的镜像已经是有效的运行时镜像
    if get_runtime_image_repo() in base_image:
        logger.debug(
            f'The provided image [{base_image}] is already a valid runtime image.\n'
            f'Will try to reuse it as is.'
        )
        # 中文说明：提供的镜像已经是有效的运行时镜像。将尝试按原样重用它。

        # 如果没有标签，添加latest标签
        if ':' not in base_image:
            base_image = base_image + ':latest'
        repo, tag = base_image.split(':')
        return repo, tag
    else:
        # 如果没有标签，添加latest标签
        if ':' not in base_image:
            base_image = base_image + ':latest'
        [repo, tag] = base_image.split(':')

        # 如果仓库名太长，进行哈希处理
        if len(repo) > 32:
            repo_hash = hashlib.md5(repo[:-24].encode()).hexdigest()[:8]
            repo = f'{repo_hash}_{repo[-24:]}'  # 使用8个字符的哈希 + 最后24个字符
        else:
            repo = repo.replace('/', '_s_')

        # 生成新的标签
        new_tag = f'oh_v{oh_version}_image_{repo}_tag_{tag}'

        # 如果标签仍然太长，对整个镜像名进行哈希处理
        if len(new_tag) > 128:
            new_tag = f'oh_v{oh_version}_image_{hashlib.md5(new_tag.encode()).hexdigest()[:64]}'
            logger.warning(
                f'The new tag [{new_tag}] is still too long, so we use an hash of the entire image name: {new_tag}'
            )
            # 中文说明：新标签仍然太长，所以我们使用整个镜像名的哈希值

        return get_runtime_image_repo(), new_tag


def build_runtime_image(
    base_image: str,
    runtime_builder: RuntimeBuilder,
    platform: str | None = None,
    extra_deps: str | None = None,
    build_folder: str | None = None,
    dry_run: bool = False,
    force_rebuild: bool = False,
    extra_build_args: list[str] | None = None,
) -> str:
    """
    准备最终的docker构建文件夹。

    如果dry_run为False，它还会使用docker构建文件夹构建OpenHands运行时Docker镜像。

    Args:
        base_image (str): 要使用的基础Docker镜像名称
        runtime_builder (RuntimeBuilder): 要使用的运行时构建器
        platform (str | None): 构建的目标平台（例如linux/amd64, linux/arm64）
        extra_deps (str | None): 额外的依赖项
        build_folder (str | None): 用于构建的目录。如果未提供，将使用临时目录
        dry_run (bool): 如果为True，只准备构建文件夹。不会实际构建Docker镜像
        force_rebuild (bool): 如果为True，将创建使用base_image的Dockerfile
        extra_build_args (list[str] | None): 传递给构建器的额外构建参数

    Returns:
        str: <image_repo>:<MD5 hash>。其中MD5 hash是docker构建文件夹的哈希值

    有关更多详细信息，请参阅https://docs.all-hands.dev/usage/architecture/runtime。
    """
    # 如果没有提供构建文件夹，使用临时目录
    if build_folder is None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = build_runtime_image_in_folder(
                base_image=base_image,
                runtime_builder=runtime_builder,
                build_folder=Path(temp_dir),
                extra_deps=extra_deps,
                dry_run=dry_run,
                force_rebuild=force_rebuild,
                platform=platform,
                extra_build_args=extra_build_args,
            )
            return result

    # 使用提供的构建文件夹
    result = build_runtime_image_in_folder(
        base_image=base_image,
        runtime_builder=runtime_builder,
        build_folder=Path(build_folder),
        extra_deps=extra_deps,
        dry_run=dry_run,
        force_rebuild=force_rebuild,
        platform=platform,
        extra_build_args=extra_build_args,
    )
    return result


def build_runtime_image_in_folder(
    base_image: str,
    runtime_builder: RuntimeBuilder,
    build_folder: Path,
    extra_deps: str | None,
    dry_run: bool,
    force_rebuild: bool,
    platform: str | None = None,
    extra_build_args: list[str] | None = None,
) -> str:
    """
    在指定文件夹中构建运行时镜像。
    
    Args:
        base_image (str): 基础镜像名称
        runtime_builder (RuntimeBuilder): 运行时构建器
        build_folder (Path): 构建文件夹路径
        extra_deps (str | None): 额外依赖项
        dry_run (bool): 是否为试运行
        force_rebuild (bool): 是否强制重建
        platform (str | None): 目标平台
        extra_build_args (list[str] | None): 额外构建参数
        
    Returns:
        str: 构建的镜像名称
    """
    # 获取运行时镜像仓库和标签
    runtime_image_repo, _ = get_runtime_image_repo_and_tag(base_image)
    
    # 生成不同类型的标签
    lock_tag = f'oh_v{oh_version}_{get_hash_for_lock_files(base_image)}'
    versioned_tag = (
        # 截断基础镜像到96个字符以适应标签最大长度（128个字符）
        f'oh_v{oh_version}_{get_tag_for_versioned_image(base_image)}'
    )
    versioned_image_name = f'{runtime_image_repo}:{versioned_tag}'
    source_tag = f'{lock_tag}_{get_hash_for_source_files()}'
    hash_image_name = f'{runtime_image_repo}:{source_tag}'

    logger.info(f'Building image: {hash_image_name}')
    # 中文说明：正在构建镜像
    
    # 如果强制重建，从头开始构建
    if force_rebuild:
        logger.debug(
            f'Force rebuild: [{runtime_image_repo}:{source_tag}] from scratch.'
        )
        # 中文说明：强制重建：从头开始
        prep_build_folder(
            build_folder,
            base_image,
            build_from=BuildFromImageType.SCRATCH,
            extra_deps=extra_deps,
        )
        if not dry_run:
            _build_sandbox_image(
                build_folder,
                runtime_builder,
                runtime_image_repo,
                source_tag,
                lock_tag,
                versioned_tag,
                platform,
                extra_build_args=extra_build_args,
            )
        return hash_image_name

    # 确定构建策略
    lock_image_name = f'{runtime_image_repo}:{lock_tag}'
    build_from = BuildFromImageType.SCRATCH

    # 如果确切的镜像已经存在，我们不需要构建它
    if runtime_builder.image_exists(hash_image_name, False):
        logger.debug(f'Reusing Image [{hash_image_name}]')
        # 中文说明：重用镜像
        return hash_image_name

    # 查找具有相同lock_tag的现有镜像。如果存在这样的镜像，我们可以将其用作
    # 构建的基础镜像，只需复制源文件。这使构建速度更快。
    if runtime_builder.image_exists(lock_image_name):
        logger.debug(f'Build [{hash_image_name}] from lock image [{lock_image_name}]')
        # 中文说明：从锁定镜像构建
        build_from = BuildFromImageType.LOCK
        base_image = lock_image_name
    elif runtime_builder.image_exists(versioned_image_name):
        logger.info(
            f'Build [{hash_image_name}] from versioned image [{versioned_image_name}]'
        )
        # 中文说明：从版本化镜像构建
        build_from = BuildFromImageType.VERSIONED
        base_image = versioned_image_name
    else:
        logger.debug(f'Build [{hash_image_name}] from scratch')
        # 中文说明：从头开始构建

    # 准备构建文件夹
    prep_build_folder(build_folder, base_image, build_from, extra_deps)
    if not dry_run:
        _build_sandbox_image(
            build_folder,
            runtime_builder,
            runtime_image_repo,
            source_tag=source_tag,
            lock_tag=lock_tag,
            # 只有在从头开始构建时才标记版本化镜像。
            # 这避免了多次堆叠镜像时产生过多的层
            versioned_tag=versioned_tag
            if build_from == BuildFromImageType.SCRATCH
            else None,
            platform=platform,
            extra_build_args=extra_build_args,
        )

    return hash_image_name


def prep_build_folder(
    build_folder: Path,
    base_image: str,
    build_from: BuildFromImageType,
    extra_deps: str | None,
) -> None:
    """
    准备构建文件夹。
    
    Args:
        build_folder (Path): 构建文件夹路径
        base_image (str): 基础镜像名称
        build_from (BuildFromImageType): 构建类型
        extra_deps (str | None): 额外依赖项
    """
    # 将源代码复制到目录。它将在build_folder/code中
    # 如果找不到包，从源代码构建
    openhands_source_dir = Path(openhands.__file__).parent
    project_root = openhands_source_dir.parent
    logger.debug(f'Building source distribution using project root: {project_root}')
    # 中文说明：使用项目根目录构建源分发

    # 复制'openhands'目录（源代码）
    shutil.copytree(
        openhands_source_dir,
        Path(build_folder, 'code', 'openhands'),
        ignore=shutil.ignore_patterns(
            '.*/',         # 忽略隐藏目录
            '__pycache__/', # 忽略Python缓存目录
            '*.pyc',       # 忽略Python字节码文件
            '*.md',        # 忽略Markdown文件
        ),
    )

    # 复制pyproject.toml和poetry.lock文件
    for file in ['pyproject.toml', 'poetry.lock']:
        src = Path(openhands_source_dir, file)
        if not src.exists():
            src = Path(project_root, file)
        shutil.copy2(src, Path(build_folder, 'code', file))

    # 创建Dockerfile并写入build_folder
    dockerfile_content = _generate_dockerfile(
        base_image,
        build_from=build_from,
        extra_deps=extra_deps,
    )
    dockerfile_path = Path(build_folder, 'Dockerfile')
    with open(str(dockerfile_path), 'w') as f:
        f.write(dockerfile_content)


# 用于哈希截断的字母表（数字+小写字母）
_ALPHABET = string.digits + string.ascii_lowercase


def truncate_hash(hash: str) -> str:
    """
    将base16哈希转换为base36并截断到16个字符。
    
    Args:
        hash (str): 要截断的哈希值
        
    Returns:
        str: 截断后的哈希值
    """
    value = int(hash, 16)
    result: list[str] = []
    # 转换为base36并限制长度
    while value > 0 and len(result) < 16:
        value, remainder = divmod(value, len(_ALPHABET))
        result.append(_ALPHABET[remainder])
    return ''.join(result)


def get_hash_for_lock_files(base_image: str) -> str:
    """
    获取锁定文件的哈希值。
    
    Args:
        base_image (str): 基础镜像名称
        
    Returns:
        str: 锁定文件的哈希值
    """
    openhands_source_dir = Path(openhands.__file__).parent
    md5 = hashlib.md5()
    # 将基础镜像名称添加到哈希中
    md5.update(base_image.encode())
    
    # 处理项目配置文件
    for file in ['pyproject.toml', 'poetry.lock']:
        src = Path(openhands_source_dir, file)
        if not src.exists():
            src = Path(openhands_source_dir.parent, file)
        with open(src, 'rb') as f:
            # 分块读取文件内容并更新哈希
            for chunk in iter(lambda: f.read(4096), b''):
                md5.update(chunk)
    
    # 我们使用截断是因为我们需要的是唯一性而不是密码学安全性
    result = truncate_hash(md5.hexdigest())
    return result


def get_tag_for_versioned_image(base_image: str) -> str:
    """
    获取版本化镜像的标签。
    
    Args:
        base_image (str): 基础镜像名称
        
    Returns:
        str: 版本化镜像的标签
    """
    # 替换特殊字符并转换为小写，取最后96个字符
    return base_image.replace('/', '_s_').replace(':', '_t_').lower()[-96:]


def get_hash_for_source_files() -> str:
    """
    获取源文件的哈希值。
    
    Returns:
        str: 源文件的哈希值
    """
    openhands_source_dir = Path(openhands.__file__).parent
    # 计算目录的哈希值
    dir_hash = dirhash(
        openhands_source_dir,
        'md5',
        ignore=[
            '.*/',          # 隐藏目录
            '__pycache__/', # Python缓存目录
            '*.pyc',        # Python字节码文件
        ],
    )
    # 我们使用截断是因为我们需要的是唯一性而不是密码学安全性
    result = truncate_hash(dir_hash)
    return result


def _build_sandbox_image(
    build_folder: Path,
    runtime_builder: RuntimeBuilder,
    runtime_image_repo: str,
    source_tag: str,
    lock_tag: str,
    versioned_tag: str | None,
    platform: str | None = None,
    extra_build_args: list[str] | None = None,
) -> str:
    """
    构建并标记沙盒镜像。镜像将使用所有尚不存在的标签进行标记。
    
    Args:
        build_folder (Path): 构建文件夹路径
        runtime_builder (RuntimeBuilder): 运行时构建器
        runtime_image_repo (str): 运行时镜像仓库
        source_tag (str): 源标签
        lock_tag (str): 锁定标签
        versioned_tag (str | None): 版本化标签
        platform (str | None): 目标平台
        extra_build_args (list[str] | None): 额外构建参数
        
    Returns:
        str: 构建的镜像名称
        
    Raises:
        AgentRuntimeBuildError: 当构建失败时抛出
    """
    # 准备所有可能的标签
    names = [
        f'{runtime_image_repo}:{source_tag}',
        f'{runtime_image_repo}:{lock_tag}',
    ]
    if versioned_tag is not None:
        names.append(f'{runtime_image_repo}:{versioned_tag}')
    
    # 过滤掉已经存在的标签
    names = [name for name in names if not runtime_builder.image_exists(name, False)]

    # 构建镜像
    image_name = runtime_builder.build(
        path=str(build_folder),
        tags=names,
        platform=platform,
        extra_build_args=extra_build_args,
    )
    
    # 检查构建是否成功
    if not image_name:
        raise AgentRuntimeBuildError(f'Build failed for image {names}')
        # 中文说明：构建失败

    return image_name


if __name__ == '__main__':
    # 命令行参数解析
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--base_image', type=str, default='nikolaik/python-nodejs:python3.12-nodejs22'
    )
    parser.add_argument('--build_folder', type=str, default=None)
    parser.add_argument('--force_rebuild', action='store_true', default=False)
    parser.add_argument('--platform', type=str, default=None)
    args = parser.parse_args()

    if args.build_folder is not None:
        # 如果提供了build_folder，我们不会实际构建Docker镜像。我们只复制必要的源代码
        # 并动态创建Dockerfile并将其放在build_folder中。这允许使用Dockerfile创建Docker镜像
        #（很可能使用containers/build.sh脚本）
        build_folder = args.build_folder
        assert os.path.exists(build_folder), (
            f'Build folder {build_folder} does not exist'
        )
        # 中文说明：构建文件夹不存在
        
        logger.debug(
            f'Copying the source code and generating the Dockerfile in the build folder: {build_folder}'
        )
        # 中文说明：复制源代码并在构建文件夹中生成Dockerfile

        # 获取运行时镜像仓库和标签
        runtime_image_repo, runtime_image_tag = get_runtime_image_repo_and_tag(
            args.base_image
        )
        logger.debug(
            f'Runtime image repo: {runtime_image_repo} and runtime image tag: {runtime_image_tag}'
        )
        # 中文说明：运行时镜像仓库和运行时镜像标签

        with tempfile.TemporaryDirectory() as temp_dir:
            # dry_run为true，所以我们只准备一个包含所需源代码和Dockerfile的temp_dir
            # 然后获取文件夹的MD5哈希并返回<image_repo>:<temp_dir_md5_hash>
            runtime_image_hash_name = build_runtime_image(
                args.base_image,
                runtime_builder=DockerRuntimeBuilder(docker.from_env()),
                build_folder=temp_dir,
                dry_run=True,
                force_rebuild=args.force_rebuild,
                platform=args.platform,
            )

            _runtime_image_repo, runtime_image_source_tag = (
                runtime_image_hash_name.split(':')
            )

            # 将temp_dir的内容移动到build_folder
            shutil.copytree(temp_dir, build_folder, dirs_exist_ok=True)
        
        logger.debug(
            f'Build folder [{build_folder}] is ready: {os.listdir(build_folder)}'
        )
        # 中文说明：构建文件夹已准备就绪

        # 现在我们更新build_folder中的config.sh以包含所需的值。
        # 这在containers/build.sh脚本中使用，该脚本被调用来实际构建Docker镜像
        with open(os.path.join(build_folder, 'config.sh'), 'a') as file:
            file.write(
                (
                    f'\n'
                    f'DOCKER_IMAGE_TAG={runtime_image_tag}\n'
                    f'DOCKER_IMAGE_SOURCE_TAG={runtime_image_source_tag}\n'
                )
            )

        logger.debug(
            f'`config.sh` is updated with the image repo[{runtime_image_repo}] and tags [{runtime_image_tag}, {runtime_image_source_tag}]'
        )
        # 中文说明：config.sh已更新，包含镜像仓库和标签
        
        logger.debug(
            f'Dockerfile, source code and config.sh are ready in {build_folder}'
        )
        # 中文说明：Dockerfile、源代码和config.sh在构建文件夹中已准备就绪
    else:
        # 如果没有提供build_folder，在复制所需源代码并动态创建Dockerfile后，
        # 我们实际构建Docker镜像
        logger.debug('Building image in a temporary folder')
        # 中文说明：在临时文件夹中构建镜像
        
        docker_builder = DockerRuntimeBuilder(docker.from_env())
        image_name = build_runtime_image(
            args.base_image, docker_builder, platform=args.platform
        )
        logger.debug(f'\nBuilt image: {image_name}\n')
        # 中文说明：已构建镜像
