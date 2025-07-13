import datetime
import os
import subprocess
import time

import docker

from openhands import __version__ as oh_version
from openhands.core.exceptions import AgentRuntimeBuildError
from openhands.core.logger import RollingLogger
from openhands.core.logger import openhands_logger as logger
from openhands.runtime.builder.base import RuntimeBuilder
from openhands.utils.term_color import TermColor, colorize


class DockerRuntimeBuilder(RuntimeBuilder):
    """Docker Runtime构建器实现类
    
    这个类实现了基于Docker的Runtime构建器，继承自RuntimeBuilder抽象基类。
    主要功能包括：
    1. 使用Docker BuildKit构建容器镜像
    2. 支持多平台构建
    3. 提供构建缓存管理
    4. 处理构建日志输出
    5. 检查镜像存在性并支持自动拉取
    
    该构建器支持Docker和Podman两种容器引擎，并会自动检测版本兼容性。
    """
    
    def __init__(self, docker_client: docker.DockerClient):
        """初始化Docker Runtime构建器
        
        Args:
            docker_client (docker.DockerClient): Docker客户端实例，用于与Docker守护进程通信
        
        Raises:
            AgentRuntimeBuildError: 当Docker/Podman版本不兼容时抛出异常
        """
        # Docker客户端实例，用于所有Docker操作
        self.docker_client = docker_client

        # 获取Docker服务器版本信息
        version_info = self.docker_client.version()
        # 清理版本号，移除连字符并用点分隔
        server_version = version_info.get('Version', '').replace('-', '.')
        
        # 检测是否使用Podman而不是Docker
        self.is_podman = (
            version_info.get('Components')[0].get('Name').startswith('Podman')
        )
        
        # 检查Docker版本兼容性（需要18.09+才能使用BuildKit）
        if (
            tuple(map(int, server_version.split('.')[:2])) < (18, 9)
            and not self.is_podman
        ):
            raise AgentRuntimeBuildError(
                'Docker server version must be >= 18.09 to use BuildKit'
            )

        # 检查Podman版本兼容性（需要4.9.0+）
        if self.is_podman and tuple(map(int, server_version.split('.')[:2])) < (4, 9):
            raise AgentRuntimeBuildError('Podman server version must be >= 4.9.0')

        # 初始化滚动日志记录器，用于构建过程的日志输出（最多保留10行）
        self.rolling_logger = RollingLogger(max_lines=10)

    @staticmethod
    def check_buildx(is_podman: bool = False) -> bool:
        """检查Docker Buildx是否可用的静态方法
        
        Docker Buildx是Docker的扩展构建功能，提供更强大的构建能力。
        
        Args:
            is_podman (bool): 是否使用Podman而不是Docker，默认为False
        
        Returns:
            bool: True表示Buildx可用，False表示不可用
        """
        try:
            # 尝试执行buildx version命令来检查可用性
            result = subprocess.run(
                ['docker' if not is_podman else 'podman', 'buildx', 'version'],
                capture_output=True,  # 捕获输出
                text=True,           # 以文本形式返回输出
            )
            # 返回码为0表示命令执行成功
            return result.returncode == 0
        except FileNotFoundError:
            # 如果找不到docker/podman命令，返回False
            return False

    def build(
        self,
        path: str,
        tags: list[str],
        platform: str | None = None,
        extra_build_args: list[str] | None = None,
        use_local_cache: bool = False,
    ) -> str:
        """使用BuildKit构建Docker镜像并适当处理构建日志
        
        这个方法实现了RuntimeBuilder抽象基类的build方法，使用Docker BuildKit
        进行镜像构建，支持多平台构建、缓存管理和详细的错误处理。
        
        Args:
            path (str): Docker构建上下文的路径，包含Dockerfile和相关文件
            tags (list[str]): 应用到构建镜像的标签列表
                             第一个标签是基于哈希的名称，第二个（如果存在）是通用标签
            platform (str, optional): 构建的目标平台，例如"linux/amd64"。默认为None
            use_local_cache (bool, optional): 是否使用和更新本地构建缓存。默认为False
            extra_build_args (list[str], optional): 传递给Docker构建命令的额外参数。默认为None
        
        Returns:
            str: 构建完成的Docker镜像名称，格式为"仓库:标签"
        
        Raises:
            AgentRuntimeBuildError: 当Docker服务器版本不兼容或构建过程失败时抛出
        
        Note:
            此方法使用Docker BuildKit以获得更好的构建性能和缓存能力。
            如果`use_local_cache`为True，将尝试使用和更新本地目录中的构建缓存。
            `extra_build_args`参数允许根据需要传递额外的Docker构建参数。
        """
        # 重新获取Docker客户端以确保连接有效
        self.docker_client = docker.from_env()
        version_info = self.docker_client.version()
        # 处理版本号，移除构建号和连字符
        server_version = version_info.get('Version', '').split('+')[0].replace('-', '.')
        # 重新检测是否为Podman
        self.is_podman = (
            version_info.get('Components')[0].get('Name').startswith('Podman')
        )
        
        # 再次检查版本兼容性
        if tuple(map(int, server_version.split('.'))) < (18, 9) and not self.is_podman:
            raise AgentRuntimeBuildError(
                'Docker server version must be >= 18.09 to use BuildKit'
            )

        if self.is_podman and tuple(map(int, server_version.split('.'))) < (4, 9):
            raise AgentRuntimeBuildError('Podman server version must be >= 4.9.0')

        # 检查Buildx是否可用，如果不可用则尝试安装Docker
        if not DockerRuntimeBuilder.check_buildx(self.is_podman):
            # 当在容器中运行openhands时，可能没有"docker"可执行文件
            # 在这种情况下，我们需要下载docker二进制文件
            # 由于官方openhands应用镜像基于debian构建，我们使用debian方式安装docker二进制文件
            logger.info(
                'No docker binary available inside openhands-app container, trying to download online...'
            )
            
            # Debian系统安装Docker的命令序列
            commands = [
                'apt-get update',  # 更新包索引
                'apt-get install -y ca-certificates curl gnupg',  # 安装必要工具
                'install -m 0755 -d /etc/apt/keyrings',  # 创建密钥环目录
                # 下载Docker GPG密钥
                'curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc',
                'chmod a+r /etc/apt/keyrings/docker.asc',  # 设置密钥权限
                # 添加Docker仓库到apt源
                'echo \
                  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
                  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
                  tee /etc/apt/sources.list.d/docker.list > /dev/null',
                'apt-get update',  # 再次更新包索引
                # 安装Docker相关包
                'apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin',
            ]
            
            # 执行每个安装命令
            for cmd in commands:
                try:
                    subprocess.run(
                        cmd, shell=True, check=True, stdout=subprocess.DEVNULL
                    )
                except subprocess.CalledProcessError as e:
                    logger.error(f'Image build failed:\n{e}')
                    logger.error(f'Command output:\n{e.output}')
                    raise
            logger.info('Downloaded and installed docker binary')

        # 解析镜像标签信息
        target_image_hash_name = tags[0]  # 第一个标签（基于哈希的名称）
        target_image_repo, target_image_source_tag = target_image_hash_name.split(':')
        # 如果有第二个标签，提取其标签部分，否则为None
        target_image_tag = tags[1].split(':')[1] if len(tags) > 1 else None

        # 构建Docker buildx命令
        buildx_cmd = [
            'docker' if not self.is_podman else 'podman',  # 选择命令
            'buildx',
            'build',
            '--progress=plain',  # 使用纯文本进度输出
            # 设置构建参数：OpenHands版本
            f'--build-arg=OPENHANDS_RUNTIME_VERSION={oh_version}',
            # 设置构建参数：构建时间
            f'--build-arg=OPENHANDS_RUNTIME_BUILD_TIME={datetime.datetime.now().isoformat()}',
            f'--tag={target_image_hash_name}',  # 设置镜像标签
            '--load',  # 构建完成后加载到本地镜像存储
        ]

        # 如果指定了平台，添加平台参数
        if platform:
            buildx_cmd.append(f'--platform={platform}')

        # 处理本地缓存配置
        cache_dir = '/tmp/.buildx-cache'  # 缓存目录路径
        if use_local_cache and self._is_cache_usable(cache_dir):
            buildx_cmd.extend(
                [
                    f'--cache-from=type=local,src={cache_dir}',  # 从本地缓存读取
                    f'--cache-to=type=local,dest={cache_dir},mode=max',  # 写入本地缓存
                ]
            )

        # 添加额外的构建参数
        if extra_build_args:
            buildx_cmd.extend(extra_build_args)

        buildx_cmd.append(path)  # 构建上下文路径必须是最后一个参数

        # 开始记录构建日志
        self.rolling_logger.start(
            f'================ {buildx_cmd[0].upper()} BUILD STARTED ================'
        )

        # 设置默认构建器
        builder_cmd = ['docker', 'buildx', 'use', 'default']
        subprocess.Popen(
            builder_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )

        try:
            # 启动构建进程
            process = subprocess.Popen(
                buildx_cmd,
                stdout=subprocess.PIPE,      # 捕获标准输出
                stderr=subprocess.STDOUT,    # 将错误输出重定向到标准输出
                universal_newlines=True,     # 以文本模式处理输出
                bufsize=1,                   # 行缓冲
            )

            # 实时读取并输出构建日志
            if process.stdout:
                for line in iter(process.stdout.readline, ''):
                    line = line.strip()
                    if line:
                        self._output_logs(line)

            # 等待进程完成并获取返回码
            return_code = process.wait()

            # 检查构建是否成功
            if return_code != 0:
                raise subprocess.CalledProcessError(
                    return_code,
                    process.args,
                    output=process.stdout.read() if process.stdout else None,
                    stderr=process.stderr.read() if process.stderr else None,
                )

        except subprocess.CalledProcessError as e:
            logger.error(f'Image build failed:\n{e}')  # TODO: {e} is empty
            logger.error(f'Command output:\n{e.output}')
            if self.rolling_logger.is_enabled():
                logger.error(
                    'Docker build output:\n' + self.rolling_logger.all_lines
                )  # 显示错误信息
            raise

        except subprocess.TimeoutExpired:
            logger.error('Image build timed out')
            raise

        except FileNotFoundError as e:
            logger.error(f'Python executable not found: {e}')
            raise

        except PermissionError as e:
            logger.error(
                f'Permission denied when trying to execute the build command:\n{e}'
            )
            raise

        except Exception as e:
            logger.error(f'An unexpected error occurred during the build process: {e}')
            raise

        logger.info(f'Image [{target_image_hash_name}] build finished.')

        # 如果有第二个标签，为镜像添加额外标签
        if target_image_tag:
            image = self.docker_client.images.get(target_image_hash_name)
            image.tag(target_image_repo, target_image_tag)
            logger.info(
                f'Re-tagged image [{target_image_hash_name}] with more generic tag [{target_image_tag}]'
            )

        # 验证镜像是否构建成功
        image = self.docker_client.images.get(target_image_hash_name)
        if image is None:
            raise AgentRuntimeBuildError(
                f'Build failed: Image {target_image_hash_name} not found'
            )

        # 生成标签字符串用于日志输出
        tags_str = (
            f'{target_image_source_tag}, {target_image_tag}'
            if target_image_tag
            else target_image_source_tag
        )
        logger.info(
            f'Image {target_image_repo} with tags [{tags_str}] built successfully'
        )
        return target_image_hash_name

    def image_exists(self, image_name: str, pull_from_repo: bool = True) -> bool:
        """检查镜像是否存在于注册表（首先尝试拉取）或本地存储中
        
        这个方法实现了RuntimeBuilder抽象基类的image_exists方法。
        首先检查本地是否存在指定镜像，如果不存在且允许拉取，则尝试从远程仓库拉取。
        
        Args:
            image_name (str): 要检查的Docker镜像名称，格式为"<镜像仓库>:<镜像标签>"
            pull_from_repo (bool): 当镜像在本地不存在时是否从远程仓库拉取，默认为True
            
        Returns:
            bool: 镜像是否存在于注册表或本地存储中
        """
        # 验证镜像名称有效性
        if not image_name:
            logger.error(f'Invalid image name: `{image_name}`')
            return False

        try:
            # 首先检查镜像是否在本地存在
            logger.debug(f'Checking, if image exists locally:\n{image_name}')
            self.docker_client.images.get(image_name)
            logger.debug('Image found locally.')
            return True
        except docker.errors.ImageNotFound:
            # 本地不存在镜像
            if not pull_from_repo:
                logger.debug(
                    f'Image {image_name} {colorize("not found", TermColor.WARNING)} locally'
                )
                return False
            try:
                logger.debug(
                    'Image not found locally. Trying to pull it, please wait...'
                )

                # 用于跟踪拉取进度的变量
                layers: dict[str, dict[str, str]] = {}  # 存储各层的下载状态
                previous_layer_count = 0  # 上一次的层数量

                # 解析镜像名称和标签
                if ':' in image_name:
                    image_repo, image_tag = image_name.split(':', 1)
                else:
                    image_repo = image_name
                    image_tag = None

                # 开始拉取镜像，使用流式输出显示进度
                for line in self.docker_client.api.pull(
                    image_repo, tag=image_tag, stream=True, decode=True
                ):
                    self._output_build_progress(line, layers, previous_layer_count)
                    previous_layer_count = len(layers)
                logger.debug('Image pulled')
                return True
            except docker.errors.ImageNotFound:
                logger.debug('Could not find image locally or in registry.')
                return False
            except Exception as e:
                # 处理其他拉取异常
                msg = f'Image {colorize("could not be pulled", TermColor.ERROR)}: '
                ex_msg = str(e)
                if 'Not Found' in ex_msg:
                    msg += 'image not found in registry.'
                else:
                    msg += f'{ex_msg}'
                logger.debug(msg)
                return False

    def _output_logs(self, new_line: str) -> None:
        """输出构建日志的私有方法
        
        根据滚动日志记录器的状态，选择合适的日志输出方式。
        
        Args:
            new_line (str): 要输出的日志行内容
        """
        if not self.rolling_logger.is_enabled():
            # 如果滚动日志未启用，使用标准调试日志
            logger.debug(new_line)
        else:
            # 如果滚动日志已启用，添加到滚动日志中
            self.rolling_logger.add_line(new_line)

    def _output_build_progress(
        self, current_line: dict, layers: dict, previous_layer_count: int
    ) -> None:
        """输出构建进度的私有方法
        
        处理Docker镜像拉取过程中的进度信息，实现实时进度显示。
        
        Args:
            current_line (dict): 当前处理的进度行数据
            layers (dict): 存储各层下载状态的字典
            previous_layer_count (int): 上一次统计的层数量
        """
        # 处理有进度详情的层信息
        if 'id' in current_line and 'progressDetail' in current_line:
            layer_id = current_line['id']  # 获取层ID
            
            # 如果是新层，初始化其状态信息
            if layer_id not in layers:
                layers[layer_id] = {'status': '', 'progress': '', 'last_logged': 0}

            # 更新层状态
            if 'status' in current_line:
                layers[layer_id]['status'] = current_line['status']

            # 更新进度信息
            if 'progress' in current_line:
                layers[layer_id]['progress'] = current_line['progress']

            # 计算下载百分比
            if 'progressDetail' in current_line:
                progress_detail = current_line['progressDetail']
                if 'total' in progress_detail and 'current' in progress_detail:
                    total = progress_detail['total']
                    current = progress_detail['current']
                    # 确保百分比不超过100%
                    percentage = min(
                        (current / total) * 100, 100
                    )
                else:
                    # 如果没有具体进度数据，根据状态判断
                    percentage = (
                        100 if layers[layer_id]['status'] == 'Download complete' else 0
                    )

            # 使用滚动日志显示进度
            if self.rolling_logger.is_enabled():
                self.rolling_logger.move_back(previous_layer_count)  # 回到之前的位置
                # 遍历所有层并显示其状态
                for lid, layer_data in sorted(layers.items()):
                    self.rolling_logger.replace_current_line()
                    status = layer_data['status']
                    progress = layer_data['progress']
                    if status == 'Download complete':
                        self.rolling_logger.write_immediately(
                            f'Layer {lid}: Download complete'
                        )
                    elif status == 'Already exists':
                        self.rolling_logger.write_immediately(
                            f'Layer {lid}: Already exists'
                        )
                    else:
                        self.rolling_logger.write_immediately(
                            f'Layer {lid}: {progress} {status}'
                        )
            # 如果不使用滚动日志，但进度有显著变化时输出日志
            elif percentage != 0 and (
                percentage - layers[layer_id]['last_logged'] >= 10 or percentage == 100
            ):
                logger.debug(
                    f'Layer {layer_id}: {layers[layer_id]["progress"]} {layers[layer_id]["status"]}'
                )

            # 更新上次记录的进度
            layers[layer_id]['last_logged'] = percentage
        elif 'status' in current_line:
            # 处理没有层ID的状态信息
            logger.debug(current_line['status'])

    def _prune_old_cache_files(self, cache_dir: str, max_age_days: int = 7) -> None:
        """清理超过指定天数的旧缓存文件
        
        定期清理构建缓存目录中的旧文件，以防止缓存占用过多磁盘空间。
        
        Args:
            cache_dir (str): 缓存目录的路径
            max_age_days (int): 缓存文件的最大保存天数，默认为7天
        """
        try:
            current_time = time.time()  # 当前时间戳
            max_age_seconds = max_age_days * 24 * 60 * 60  # 转换为秒数

            # 遍历缓存目录中的所有文件
            for root, _, files in os.walk(cache_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        # 计算文件年龄
                        file_age = current_time - os.path.getmtime(file_path)
                        if file_age > max_age_seconds:
                            # 删除过期文件
                            os.remove(file_path)
                            logger.debug(f'Removed old cache file: {file_path}')
                    except Exception as e:
                        logger.warning(f'Error processing cache file {file_path}: {e}')
        except Exception as e:
            logger.warning(f'Error during build cache pruning: {e}')

    def _is_cache_usable(self, cache_dir: str) -> bool:
        """检查缓存目录是否可用（存在且可写）
        
        验证指定的缓存目录是否存在、可写，如果不存在则尝试创建。
        同时清理过期的缓存文件。
        
        Args:
            cache_dir (str): 缓存目录的路径
        
        Returns:
            bool: True表示缓存目录可用，False表示不可用
        """
        # 检查缓存目录是否存在，不存在则创建
        if not os.path.exists(cache_dir):
            try:
                os.makedirs(cache_dir, exist_ok=True)
                logger.debug(f'Created cache directory: {cache_dir}')
            except OSError as e:
                logger.debug(f'Failed to create cache directory {cache_dir}: {e}')
                return False

        # 检查缓存目录是否可写
        if not os.access(cache_dir, os.W_OK):
            logger.warning(
                f'Cache directory {cache_dir} is not writable. Caches will not be used for Docker builds.'
            )
            return False

        # 清理旧的缓存文件
        self._prune_old_cache_files(cache_dir)

        logger.debug(f'Cache directory {cache_dir} is usable')
        return True