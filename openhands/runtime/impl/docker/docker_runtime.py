import os
import typing
from functools import lru_cache
from typing import Callable
from uuid import UUID

import docker
import httpx
import tenacity
from docker.models.containers import Container

from openhands.core.config import OpenHandsConfig
from openhands.core.exceptions import (
    AgentRuntimeDisconnectedError,
    AgentRuntimeNotFoundError,
)
from openhands.core.logger import DEBUG, DEBUG_RUNTIME
from openhands.core.logger import openhands_logger as logger
from openhands.events import EventStream
from openhands.integrations.provider import PROVIDER_TOKEN_TYPE
from openhands.runtime.builder import DockerRuntimeBuilder
from openhands.runtime.impl.action_execution.action_execution_client import (
    ActionExecutionClient,
)
from openhands.runtime.impl.docker.containers import stop_all_containers
from openhands.runtime.plugins import PluginRequirement
from openhands.runtime.runtime_status import RuntimeStatus
from openhands.runtime.utils import find_available_tcp_port
from openhands.runtime.utils.command import (
    DEFAULT_MAIN_MODULE,
    get_action_execution_server_startup_command,
)
from openhands.runtime.utils.log_streamer import LogStreamer
from openhands.runtime.utils.runtime_build import build_runtime_image
from openhands.utils.async_utils import call_sync_from_async
from openhands.utils.shutdown_listener import add_shutdown_listener
from openhands.utils.tenacity_stop import stop_if_should_exit

# 容器名称前缀，用于标识OpenHands runtime容器
CONTAINER_NAME_PREFIX = 'openhands-runtime-'

# 各种服务的端口范围定义
EXECUTION_SERVER_PORT_RANGE = (30000, 39999)  # Action执行服务器端口范围
VSCODE_PORT_RANGE = (40000, 49999)            # VSCode服务端口范围
APP_PORT_RANGE_1 = (50000, 54999)             # 应用端口范围1
APP_PORT_RANGE_2 = (55000, 59999)             # 应用端口范围2


def _is_retryablewait_until_alive_error(exception: Exception) -> bool:
    """
    检查异常是否为可重试的wait_until_alive错误。
    
    该函数用于判断在等待容器存活状态时遇到的异常是否应该重试。
    主要处理网络连接相关的异常，这些异常通常是暂时性的。
    
    Args:
        exception (Exception): 需要检查的异常对象
        
    Returns:
        bool: 如果异常可重试返回True，否则返回False
    """
    # 如果是tenacity的重试错误，递归检查原始异常
    if isinstance(exception, tenacity.RetryError):
        cause = exception.last_attempt.exception()
        return _is_retryablewait_until_alive_error(cause)

    # 检查是否为可重试的网络相关异常
    return isinstance(
        exception,
        (
            ConnectionError,         # 连接错误
            httpx.ConnectTimeout,   # 连接超时
            httpx.NetworkError,     # 网络错误
            httpx.RemoteProtocolError,  # 远程协议错误
            httpx.HTTPStatusError,  # HTTP状态错误
            httpx.ReadTimeout,      # 读取超时
        ),
    )


class DockerRuntime(ActionExecutionClient):
    """
    Docker运行时实现类。
    
    该Runtime会订阅event stream。当接收到事件时，它会将事件发送到运行在
    Docker环境内的runtime-client。
    
    Args:
        config (OpenHandsConfig): 应用程序配置对象
        event_stream (EventStream): 要订阅的事件流
        sid (str, optional): Session ID，默认为'default'
        plugins (list[PluginRequirement] | None, optional): 插件需求列表，默认为None
        env_vars (dict[str, str] | None, optional): 要设置的环境变量，默认为None
        status_callback (Callable | None, optional): 状态回调函数，默认为None
        attach_to_existing (bool, optional): 是否附加到现有容器，默认为False
        headless_mode (bool, optional): 是否启用无头模式，默认为True
        user_id (str | None, optional): 用户ID，默认为None
        git_provider_tokens (PROVIDER_TOKEN_TYPE | None, optional): Git提供商令牌，默认为None
        main_module (str, optional): 主模块名称，默认为DEFAULT_MAIN_MODULE
    """

    # 关闭监听器ID，用于确保应用关闭时清理容器
    _shutdown_listener_id: UUID | None = None

    def __init__(
        self,
        config: OpenHandsConfig,
        event_stream: EventStream,
        sid: str = 'default',
        plugins: list[PluginRequirement] | None = None,
        env_vars: dict[str, str] | None = None,
        status_callback: Callable | None = None,
        attach_to_existing: bool = False,
        headless_mode: bool = True,
        user_id: str | None = None,
        git_provider_tokens: PROVIDER_TOKEN_TYPE | None = None,
        main_module: str = DEFAULT_MAIN_MODULE,
    ):
        """
        初始化DockerRuntime实例。
        
        设置Docker客户端、端口分配、容器配置等关键组件。
        """
        # 确保只注册一次关闭监听器，避免重复注册
        if not DockerRuntime._shutdown_listener_id:
            DockerRuntime._shutdown_listener_id = add_shutdown_listener(
                lambda: stop_all_containers(CONTAINER_NAME_PREFIX)
            )

        # 存储配置和状态回调
        self.config = config
        self.status_callback = status_callback

        # 初始化端口相关变量
        self._host_port = -1          # 主机端口，用于与容器通信
        self._container_port = -1     # 容器内部端口
        self._vscode_port = -1        # VSCode服务端口
        self._app_ports: list[int] = []  # 应用程序端口列表

        # 如果设置了DOCKER_HOST_ADDR环境变量，使用它作为本地runtime URL
        if os.environ.get('DOCKER_HOST_ADDR'):
            logger.info(
                f'Using DOCKER_HOST_IP: {os.environ["DOCKER_HOST_ADDR"]} for local_runtime_url'
            )
            self.config.sandbox.local_runtime_url = (
                f'http://{os.environ["DOCKER_HOST_ADDR"]}'
            )

        # 初始化Docker客户端和API URL
        self.docker_client: docker.DockerClient = self._init_docker_client()
        self.api_url = f'{self.config.sandbox.local_runtime_url}:{self._container_port}'

        # 设置容器镜像相关属性
        self.base_container_image = self.config.sandbox.base_container_image
        self.runtime_container_image = self.config.sandbox.runtime_container_image
        self.container_name = CONTAINER_NAME_PREFIX + sid
        self.container: Container | None = None
        self.main_module = main_module

        # 初始化runtime构建器
        self.runtime_builder = DockerRuntimeBuilder(self.docker_client)

        # 容器日志缓冲区
        self.log_streamer: LogStreamer | None = None

        # 调用父类初始化方法
        super().__init__(
            config,
            event_stream,
            sid,
            plugins,
            env_vars,
            status_callback,
            attach_to_existing,
            headless_mode,
            user_id,
            git_provider_tokens,
        )

        # 在基类初始化后记录runtime_extra_deps，确保self.sid可用
        if self.config.sandbox.runtime_extra_deps:
            self.log(
                'debug',
                f'Installing extra user-provided dependencies in the runtime image: {self.config.sandbox.runtime_extra_deps}',
            )

    @property
    def action_execution_server_url(self) -> str:
        """
        获取Action执行服务器的URL。
        
        Returns:
            str: Action执行服务器的完整URL
        """
        return self.api_url

    async def connect(self) -> None:
        """
        连接到Docker容器并初始化runtime环境。
        
        该方法执行以下步骤：
        1. 尝试附加到现有容器
        2. 如果容器不存在且不是附加模式，则构建并启动新容器
        3. 等待容器就绪
        4. 设置初始环境
        """
        self.set_runtime_status(RuntimeStatus.STARTING_RUNTIME)
        try:
            # 尝试附加到现有容器
            await call_sync_from_async(self._attach_to_container)
        except docker.errors.NotFound as e:
            # 如果是附加模式但容器不存在，抛出异常
            if self.attach_to_existing:
                self.log(
                    'warning',
                    f'Container {self.container_name} not found.',
                )
                raise AgentRuntimeDisconnectedError from e
            
            # 构建runtime容器镜像（如果需要）
            self.maybe_build_runtime_container_image()
            self.log(
                'info', f'Starting runtime with image: {self.runtime_container_image}'
            )
            
            # 初始化新容器
            await call_sync_from_async(self.init_container)
            self.log(
                'info',
                f'Container started: {self.container_name}. VSCode URL: {self.vscode_url}',
            )

        # 如果启用了runtime调试且容器存在，启动日志流
        if DEBUG_RUNTIME and self.container:
            self.log_streamer = LogStreamer(self.container, self.log)
        else:
            self.log_streamer = None

        # 等待客户端准备就绪
        if not self.attach_to_existing:
            self.log('info', f'Waiting for client to become ready at {self.api_url}...')
            self.set_runtime_status(RuntimeStatus.STARTING_RUNTIME)

        # 等待容器存活
        await call_sync_from_async(self.wait_until_alive)

        if not self.attach_to_existing:
            self.log('info', 'Runtime is ready.')

        # 设置初始环境
        if not self.attach_to_existing:
            await call_sync_from_async(self.setup_initial_env)

        # 记录初始化完成信息
        self.log(
            'debug',
            f'Container initialized with plugins: {[plugin.name for plugin in self.plugins]}. VSCode URL: {self.vscode_url}',
        )
        
        # 设置runtime状态为就绪
        if not self.attach_to_existing:
            self.set_runtime_status(RuntimeStatus.READY)
        self._runtime_initialized = True

    def maybe_build_runtime_container_image(self):
        """
        如果需要，构建runtime容器镜像。
        
        检查是否已有runtime容器镜像，如果没有则基于base镜像构建一个。
        """
        if self.runtime_container_image is None:
            if self.base_container_image is None:
                raise ValueError(
                    'Neither runtime container image nor base container image is set'
                )
            
            # 设置状态为构建runtime
            self.set_runtime_status(RuntimeStatus.BUILDING_RUNTIME)
            
            # 构建runtime镜像
            self.runtime_container_image = build_runtime_image(
                self.base_container_image,
                self.runtime_builder,
                platform=self.config.sandbox.platform,
                extra_deps=self.config.sandbox.runtime_extra_deps,
                force_rebuild=self.config.sandbox.force_rebuild_runtime,
                extra_build_args=self.config.sandbox.runtime_extra_build_args,
            )

    @staticmethod
    @lru_cache(maxsize=1)
    def _init_docker_client() -> docker.DockerClient:
        """
        初始化Docker客户端。
        
        使用LRU缓存确保只创建一次Docker客户端实例。
        
        Returns:
            docker.DockerClient: Docker客户端实例
            
        Raises:
            Exception: 如果Docker客户端初始化失败
        """
        try:
            return docker.from_env()
        except Exception as ex:
            logger.error(
                'Launch docker client failed. Please make sure you have installed docker and started docker desktop/daemon.',
            )
            raise ex

    def _process_volumes(self) -> dict[str, dict[str, str]]:
        """
        根据配置处理卷挂载。
        
        处理两种挂载方式：
        1. 新的volumes配置（支持逗号分隔的多个挂载点）
        2. 传统的workspace_mount_path配置
        
        Returns:
            dict[str, dict[str, str]]: 映射主机路径到容器绑定挂载及其模式的字典
        """
        # 初始化volumes字典
        volumes: dict[str, dict[str, str]] = {}

        # 处理volumes配置（逗号分隔）
        if self.config.sandbox.volumes is not None:
            # 使用逗号分隔符处理多个挂载点
            mounts = self.config.sandbox.volumes.split(',')

            for mount in mounts:
                parts = mount.split(':')
                if len(parts) >= 2:
                    host_path = os.path.abspath(parts[0])    # 主机路径
                    container_path = parts[1]                # 容器路径
                    # 如果未指定模式，默认为'rw'（读写）
                    mount_mode = parts[2] if len(parts) > 2 else 'rw'

                    volumes[host_path] = {
                        'bind': container_path,
                        'mode': mount_mode,
                    }
                    logger.debug(
                        f'Mount dir (sandbox.volumes): {host_path} to {container_path} with mode: {mount_mode}'
                    )

        # 使用workspace_*参数的传统挂载方式
        elif (
            self.config.workspace_mount_path is not None
            and self.config.workspace_mount_path_in_sandbox is not None
        ):
            mount_mode = 'rw'  # 默认模式

            # 例如结果为: {"/home/user/openhands/workspace": {'bind': "/workspace", 'mode': 'rw'}}
            volumes[self.config.workspace_mount_path] = {
                'bind': self.config.workspace_mount_path_in_sandbox,
                'mode': mount_mode,
            }
            logger.debug(
                f'Mount dir (legacy): {self.config.workspace_mount_path} with mode: {mount_mode}'
            )

        return volumes

    def init_container(self) -> None:
        """
        初始化并启动Docker容器。
        
        该方法执行以下步骤：
        1. 分配可用端口
        2. 配置网络模式和端口映射
        3. 设置环境变量
        4. 处理卷挂载
        5. 启动容器
        """
        self.log('debug', 'Preparing to start container...')
        self.set_runtime_status(RuntimeStatus.STARTING_RUNTIME)
        
        # 分配可用端口
        self._host_port = self._find_available_port(EXECUTION_SERVER_PORT_RANGE)
        self._container_port = self._host_port
        
        # 使用配置的vscode_port或查找可用端口
        self._vscode_port = (
            self.config.sandbox.vscode_port
            or self._find_available_port(VSCODE_PORT_RANGE)
        )
        
        # 分配应用程序端口
        self._app_ports = [
            self._find_available_port(APP_PORT_RANGE_1),
            self._find_available_port(APP_PORT_RANGE_2),
        ]
        
        # 更新API URL
        self.api_url = f'{self.config.sandbox.local_runtime_url}:{self._container_port}'

        # 配置网络模式
        use_host_network = self.config.sandbox.use_host_network
        network_mode: typing.Literal['host'] | None = (
            'host' if use_host_network else None
        )

        # 初始化端口映射
        port_mapping: dict[str, list[dict[str, str]]] | None = None
        if not use_host_network:
            # 设置主要服务端口映射
            port_mapping = {
                f'{self._container_port}/tcp': [
                    {
                        'HostPort': str(self._host_port),
                        'HostIp': self.config.sandbox.runtime_binding_address,
                    }
                ],
            }

            # 如果启用VSCode，添加VSCode端口映射
            if self.vscode_enabled:
                port_mapping[f'{self._vscode_port}/tcp'] = [
                    {
                        'HostPort': str(self._vscode_port),
                        'HostIp': self.config.sandbox.runtime_binding_address,
                    }
                ]

            # 添加应用程序端口映射
            for port in self._app_ports:
                port_mapping[f'{port}/tcp'] = [
                    {
                        'HostPort': str(port),
                        'HostIp': self.config.sandbox.runtime_binding_address,
                    }
                ]
        else:
            # 使用主机网络模式的警告信息
            self.log(
                'warn',
                'Using host network mode. If you are using MacOS, please make sure you have the latest version of Docker Desktop and enabled host network feature: https://docs.docker.com/network/drivers/host/#docker-desktop',
            )

        # 合并环境变量
        environment = dict(**self.initial_env_vars)
        environment.update(
            {
                'port': str(self._container_port),
                'PYTHONUNBUFFERED': '1',  # 确保Python输出不被缓冲
                # 传递端口信息意味着嵌套runtime不会使用自己的端口！
                'VSCODE_PORT': str(self._vscode_port),
                'APP_PORT_1': str(self._app_ports[0]),
                'APP_PORT_2': str(self._app_ports[1]),
                'PIP_BREAK_SYSTEM_PACKAGES': '1',  # 允许pip在系统环境中安装包
            }
        )
        
        # 如果启用调试模式，设置DEBUG环境变量
        if self.config.debug or DEBUG:
            environment['DEBUG'] = 'true'
            
        # 更新runtime启动环境变量
        environment.update(self.config.sandbox.runtime_startup_env_vars)

        self.log('debug', f'Workspace Base: {self.config.workspace_base}')

        # 处理卷挂载
        volumes = self._process_volumes()

        # 如果没有配置卷，设置为空字典
        if not volumes:
            logger.debug(
                'Mount dir is not set, will not mount the workspace directory to the container'
            )
            volumes = {}  # 使用空字典而不是None以满足mypy类型检查
            
        self.log(
            'debug',
            f'Sandbox workspace: {self.config.workspace_mount_path_in_sandbox}',
        )

        # 获取启动命令
        command = self.get_action_execution_server_startup_command()
        self.log('info', f'Starting server with command: {command}')

        # 配置GPU支持
        if self.config.sandbox.enable_gpu:
            gpu_ids = self.config.sandbox.cuda_visible_devices
            if gpu_ids is None:
                # 使用所有可用GPU
                device_requests = [
                    docker.types.DeviceRequest(capabilities=[['gpu']], count=-1)
                ]
            else:
                # 使用指定的GPU
                device_requests = [
                    docker.types.DeviceRequest(
                        capabilities=[['gpu']],
                        device_ids=[str(i) for i in gpu_ids.split(',')],
                    )
                ]
        else:
            device_requests = None
            
        try:
            # 检查runtime容器镜像是否已设置
            if self.runtime_container_image is None:
                raise ValueError('Runtime container image is not set')
                
            # 启动容器
            self.container = self.docker_client.containers.run(
                self.runtime_container_image,
                command=command,
                # 覆盖默认的'bash'入口点，因为命令是一个二进制文件
                entrypoint=[],
                network_mode=network_mode,
                ports=port_mapping,
                working_dir='/openhands/code/',  # 不要更改这个路径！
                name=self.container_name,
                detach=True,  # 后台运行
                environment=environment,
                volumes=volumes,  # type: ignore
                device_requests=device_requests,
                **(self.config.sandbox.docker_runtime_kwargs or {}),
            )
            self.log('debug', f'Container started. Server url: {self.api_url}')
            self.set_runtime_status(RuntimeStatus.RUNTIME_STARTED)
        except Exception as e:
            self.log(
                'error',
                f'Error: Instance {self.container_name} FAILED to start container!\n',
            )
            self.close()
            raise e

    def _attach_to_container(self) -> None:
        """
        附加到现有的Docker容器。
        
        从容器的环境变量中恢复端口配置，并启动已停止的容器。
        """
        # 获取现有容器
        self.container = self.docker_client.containers.get(self.container_name)
        
        # 如果容器已退出，重新启动它
        if self.container.status == 'exited':
            self.container.start()

        # 从容器配置中恢复端口信息
        config = self.container.attrs['Config']
        for env_var in config['Env']:
            if env_var.startswith('port='):
                self._host_port = int(env_var.split('port=')[1])
                self._container_port = self._host_port
            elif env_var.startswith('VSCODE_PORT='):
                self._vscode_port = int(env_var.split('VSCODE_PORT=')[1])

        # 恢复应用程序端口
        self._app_ports = []
        exposed_ports = config.get('ExposedPorts')
        if exposed_ports:
            for exposed_port in exposed_ports.keys():
                exposed_port = int(exposed_port.split('/tcp')[0])
                # 排除主要服务端口和VSCode端口
                if (
                    exposed_port != self._host_port
                    and exposed_port != self._vscode_port
                ):
                    self._app_ports.append(exposed_port)

        # 更新API URL
        self.api_url = f'{self.config.sandbox.local_runtime_url}:{self._container_port}'
        self.log(
            'debug',
            f'attached to container: {self.container_name} {self._container_port} {self.api_url}',
        )

    @tenacity.retry(
        stop=tenacity.stop_after_delay(120) | stop_if_should_exit(),  # 最多等待120秒或收到退出信号
        retry=tenacity.retry_if_exception(_is_retryablewait_until_alive_error),  # 仅对特定异常重试
        reraise=True,  # 重新抛出异常
        wait=tenacity.wait_fixed(2),  # 每次重试间隔2秒
    )
    def wait_until_alive(self) -> None:
        """
        等待容器变为存活状态。
        
        使用tenacity重试机制，最多等待120秒，每2秒检查一次容器状态。
        
        Raises:
            AgentRuntimeDisconnectedError: 如果容器已退出
            AgentRuntimeNotFoundError: 如果容器未找到
        """
        try:
            # 检查容器状态
            container = self.docker_client.containers.get(self.container_name)
            if container.status == 'exited':
                raise AgentRuntimeDisconnectedError(
                    f'Container {self.container_name} has exited.'
                )
        except docker.errors.NotFound:
            raise AgentRuntimeNotFoundError(
                f'Container {self.container_name} not found.'
            )

        # 检查服务是否存活
        self.check_if_alive()

    def close(self, rm_all_containers: bool | None = None) -> None:
        """
        关闭DockerRuntime和相关对象。
        
        Args:
            rm_all_containers (bool | None): 是否移除所有带有'openhands-sandbox-'前缀的容器
        """
        # 调用父类的关闭方法
        super().close()
        
        # 关闭日志流
        if self.log_streamer:
            self.log_streamer.close()

        # 如果未指定，使用配置中的设置
        if rm_all_containers is None:
            rm_all_containers = self.config.sandbox.rm_all_containers

        # 如果配置为保持runtime存活或附加到现有容器，则不清理
        if self.config.sandbox.keep_runtime_alive or self.attach_to_existing:
            return
            
        # 确定要停止的容器前缀
        close_prefix = (
            CONTAINER_NAME_PREFIX if rm_all_containers else self.container_name
        )
        stop_all_containers(close_prefix)

    def _is_port_in_use_docker(self, port: int) -> bool:
        """
        检查端口是否已被Docker容器使用。
        
        Args:
            port (int): 要检查的端口号
            
        Returns:
            bool: 如果端口被使用返回True，否则返回False
        """
        containers = self.docker_client.containers.list()
        for container in containers:
            container_ports = container.ports
            if str(port) in str(container_ports):
                return True
        return False

    def _find_available_port(
        self, port_range: tuple[int, int], max_attempts: int = 5
    ) -> int:
        """
        在指定范围内查找可用端口。
        
        Args:
            port_range (tuple[int, int]): 端口范围（最小值，最大值）
            max_attempts (int): 最大尝试次数，默认为5
            
        Returns:
            int: 可用的端口号
        """
        port = port_range[1]
        for _ in range(max_attempts):
            # 查找系统可用端口
            port = find_available_tcp_port(port_range[0], port_range[1])
            # 检查Docker容器是否使用了该端口
            if not self._is_port_in_use_docker(port):
                return port
        # 如果在max_attempts次尝试后没有找到端口，返回最后尝试的端口
        return port

    @property
    def vscode_url(self) -> str | None:
        """
        获取VSCode服务的URL。
        
        Returns:
            str | None: VSCode服务的完整URL，如果没有token则返回None
        """
        token = super().get_vscode_token()
        if not token:
            return None

        vscode_url = f'http://localhost:{self._vscode_port}/?tkn={token}&folder={self.config.workspace_mount_path_in_sandbox}'
        return vscode_url

    @property
    def web_hosts(self) -> dict[str, int]:
        """
        获取Web主机映射。
        
        Returns:
            dict[str, int]: 映射URL到端口号的字典
        """
        hosts: dict[str, int] = {}

        # 获取Docker主机地址，默认为localhost
        host_addr = os.environ.get('DOCKER_HOST_ADDR', 'localhost')
        for port in self._app_ports:
            hosts[f'http://{host_addr}:{port}'] = port

        return hosts

    def pause(self) -> None:
        """
        通过停止容器来暂停runtime。
        
        这与container.stop()不同，因为它确保环境变量被正确保留。
        
        Raises:
            RuntimeError: 如果容器未初始化
        """
        if not self.container:
            raise RuntimeError('Container not initialized')

        # 首先，确保所有环境变量都正确持久化到.bashrc中
        # 这已经在base.py的add_env_vars中处理了

        # 停止容器
        self.container.stop()
        self.log('debug', f'Container {self.container_name} paused')

    def resume(self) -> None:
        """
        通过启动容器来恢复runtime。
        
        这与container.start()不同，因为它确保环境变量被正确恢复。
        
        Raises:
            RuntimeError: 如果容器未初始化
        """
        if not self.container:
            raise RuntimeError('Container not initialized')

        # 启动容器
        self.container.start()
        self.log('debug', f'Container {self.container_name} resumed')

        # 等待容器就绪
        self.wait_until_alive()

    @classmethod
    async def delete(cls, conversation_id: str) -> None:
        """
        删除指定的Docker容器。
        
        这是一个类方法，用于清理特定会话的容器。
        
        Args:
            conversation_id (str): 会话ID，用于构造容器名称
        """
        docker_client = cls._init_docker_client()
        try:
            container_name = CONTAINER_NAME_PREFIX + conversation_id
            container = docker_client.containers.get(container_name)
            container.remove(force=True)  # 强制删除容器
        except docker.errors.APIError:
            # 忽略API错误（可能容器已被删除）
            pass
        except docker.errors.NotFound:
            # 忽略未找到错误（容器不存在）
            pass
        finally:
            # 确保关闭Docker客户端
            docker_client.close()

    def get_action_execution_server_startup_command(self) -> list[str]:
        """
        获取Action执行服务器的启动命令。
        
        Returns:
            list[str]: 启动命令的字符串列表
        """
        return get_action_execution_server_startup_command(
            server_port=self._container_port,
            plugins=self.plugins,
            app_config=self.config,
            main_module=self.main_module,
        )