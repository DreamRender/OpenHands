import json
import logging
import os
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
import tenacity
from tenacity import RetryCallState

from openhands.core.config import OpenHandsConfig
from openhands.core.exceptions import (
    AgentRuntimeDisconnectedError,
    AgentRuntimeError,
    AgentRuntimeNotFoundError,
    AgentRuntimeNotReadyError,
    AgentRuntimeUnavailableError,
)
from openhands.core.logger import openhands_logger as logger
from openhands.events import EventStream
from openhands.integrations.provider import PROVIDER_TOKEN_TYPE
from openhands.runtime.builder.remote import RemoteRuntimeBuilder
from openhands.runtime.impl.action_execution.action_execution_client import (
    ActionExecutionClient,
)
from openhands.runtime.plugins import PluginRequirement
from openhands.runtime.runtime_status import RuntimeStatus
from openhands.runtime.utils.command import (
    DEFAULT_MAIN_MODULE,
    get_action_execution_server_startup_command,
)
from openhands.runtime.utils.request import send_request
from openhands.runtime.utils.runtime_build import build_runtime_image
from openhands.utils.async_utils import call_sync_from_async
from openhands.utils.tenacity_stop import stop_if_should_exit


class RemoteRuntime(ActionExecutionClient):
    """
    远程运行时实现，连接到远程的 oh-runtime-client。
    
    该运行时不在本地创建容器，而是连接到远程的运行时服务，
    通过 API 调用来管理远程容器的生命周期和执行操作。
    """

    # 远程运行时客户端的默认端口
    port: int = 60000
    # 运行时 ID，由远程服务分配
    runtime_id: str | None = None
    # 运行时 URL，用于连接到远程运行时
    runtime_url: str | None = None
    # 运行时是否已初始化的标志
    _runtime_initialized: bool = False
    # 远程运行时构建器
    runtime_builder: RemoteRuntimeBuilder
    # 容器镜像名称
    container_image: str
    # 可用主机字典
    available_hosts: dict[str, int]
    # 主模块名称
    main_module: str

    def __init__(
        self,
        config: OpenHandsConfig,
        event_stream: EventStream,
        sid: str = 'default',
        plugins: list[PluginRequirement] | None = None,
        env_vars: dict[str, str] | None = None,
        status_callback: Callable[..., None] | None = None,
        attach_to_existing: bool = False,
        headless_mode: bool = True,
        user_id: str | None = None,
        git_provider_tokens: PROVIDER_TOKEN_TYPE | None = None,
        main_module: str = DEFAULT_MAIN_MODULE,
    ) -> None:
        # 调用父类构造函数
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
        
        # 检查 API 密钥是否已配置
        if self.config.sandbox.api_key is None:
            raise ValueError(
                'API key is required to use the remote runtime. '
                'Please set the API key in the config (config.toml) or as an environment variable (SANDBOX_API_KEY).'
            )
        # 在 session 头部设置 API 密钥
        self.session.headers.update({'X-API-Key': self.config.sandbox.api_key})

        # 检查工作空间配置
        if self.config.workspace_base is not None:
            self.log(
                'debug',
                'Setting workspace_base is not supported in the remote runtime.',
            )
        
        # 检查远程运行时 API URL 是否已配置
        if self.config.sandbox.remote_runtime_api_url is None:
            raise ValueError(
                'remote_runtime_api_url is required in the remote runtime.'
            )

        # 验证远程运行时类型
        assert self.config.sandbox.remote_runtime_class in (None, 'sysbox', 'gvisor')
        self.main_module = main_module

        # 初始化远程运行时构建器
        self.runtime_builder = RemoteRuntimeBuilder(
            self.config.sandbox.remote_runtime_api_url,
            self.config.sandbox.api_key,
            self.session,
        )
        # 初始化可用主机字典
        self.available_hosts: dict[str, int] = {}
        # Session API 密钥
        self._session_api_key: str | None = None

    def log(self, level: str, message: str, exc_info: bool | None = None) -> None:
        """
        记录日志，包含 Session ID 和 Runtime ID 信息。
        
        Args:
            level (str): 日志级别
            message (str): 日志消息
            exc_info (bool | None, optional): 是否包含异常信息
        """
        getattr(logger, level)(
            message,
            stacklevel=2,
            exc_info=exc_info,
            extra={
                'session_id': self.sid,
                'runtime_id': self.runtime_id,
            },
        )

    @property
    def action_execution_server_url(self) -> str:
        """
        获取 Action 执行服务器的 URL。
        
        Returns:
            str: 运行时 URL
            
        Raises:
            NotImplementedError: 如果运行时 URL 未初始化
        """
        if self.runtime_url is None:
            raise NotImplementedError('Runtime URL is not initialized')
        return self.runtime_url

    async def connect(self) -> None:
        """
        连接到运行时。
        
        该方法会尝试启动或附加到远程运行时，如果失败会进行清理。
        """
        try:
            # 启动或附加到运行时
            await call_sync_from_async(self._start_or_attach_to_runtime)
        except Exception:
            # 如果失败，关闭运行时
            self.close()
            self.log('error', 'Runtime failed to start', exc_info=True)
            raise
        
        # 设置初始环境
        await call_sync_from_async(self.setup_initial_env)
        self._runtime_initialized = True

    def _start_or_attach_to_runtime(self) -> None:
        """
        启动或附加到运行时的内部方法。
        
        该方法会检查是否存在现有运行时，如果不存在且不是附加模式，
        则会构建运行时镜像并启动新的运行时。
        """
        self.log('info', 'Starting or attaching to runtime')
        
        # 检查是否存在现有运行时
        existing_runtime = self._check_existing_runtime()
        if existing_runtime:
            self.log('info', f'Using existing runtime with ID: {self.runtime_id}')
        elif self.attach_to_existing:
            self.log('info', f'Failed to find existing runtime for SID: {self.sid}')
            raise AgentRuntimeNotFoundError(
                f'Could not find existing runtime for SID: {self.sid}'
            )
        else:
            self.log('info', 'No existing runtime found, starting a new one')
            # 如果没有指定运行时容器镜像，构建运行时
            if self.config.sandbox.runtime_container_image is None:
                self.log(
                    'info',
                    f'Building remote runtime with base image: {self.config.sandbox.base_container_image}',
                )
                self._build_runtime()
            else:
                self.log(
                    'info',
                    f'Starting remote runtime with image: {self.config.sandbox.runtime_container_image}',
                )
                self.container_image = self.config.sandbox.runtime_container_image
            # 启动运行时
            self._start_runtime()
        
        # 确保运行时 ID 和 URL 已设置
        assert self.runtime_id is not None, (
            'Runtime ID is not set. This should never happen.'
        )
        assert self.runtime_url is not None, (
            'Runtime URL is not set. This should never happen.'
        )
        
        if not self.attach_to_existing:
            self.log('info', 'Waiting for runtime to be alive...')
        
        # 等待运行时就绪
        self._wait_until_alive()
        
        if not self.attach_to_existing:
            self.log('info', 'Runtime is ready.')
        
        self.set_runtime_status(RuntimeStatus.READY)

    def _check_existing_runtime(self) -> bool:
        """
        检查是否存在现有的运行时。
        
        该方法会向远程 API 查询指定 Session ID 的运行时状态。
        
        Returns:
            bool: 如果找到可用的现有运行时返回 True，否则返回 False
        """
        self.log('info', f'Checking for existing runtime with session ID: {self.sid}')
        try:
            # 查询现有运行时
            response = self._send_runtime_api_request(
                'GET',
                f'{self.config.sandbox.remote_runtime_api_url}/sessions/{self.sid}',
            )
            data = response.json()
            status = data.get('status')
            self.log('info', f'Found runtime with status: {status}')
            
            # 如果运行时处于运行或暂停状态，解析响应
            if status == 'running' or status == 'paused':
                self._parse_runtime_response(response)
        except httpx.HTTPError as e:
            if e.response.status_code == 404:
                self.log(
                    'info', f'No existing runtime found for session ID: {self.sid}'
                )
                return False
            self.log('error', f'Error while looking for remote runtime: {e}')
            raise
        except json.decoder.JSONDecodeError as e:
            self.log(
                'error',
                f'Invalid JSON response from runtime API: {e}. URL: {self.config.sandbox.remote_runtime_api_url}/sessions/{self.sid}. Response: {response}',
            )
            raise

        # 根据状态决定是否可以使用现有运行时
        if status == 'running':
            self.log('info', 'Found existing runtime in running state')
            return True
        elif status == 'stopped':
            self.log('info', 'Found existing runtime, but it is stopped')
            return False
        elif status == 'paused':
            self.log(
                'info', 'Found existing runtime in paused state, attempting to resume'
            )
            try:
                # 尝试恢复暂停的运行时
                self._resume_runtime()
                self.log('info', 'Successfully resumed paused runtime')
                return True
            except Exception as e:
                self.log(
                    'error', f'Failed to resume paused runtime: {e}', exc_info=True
                )
                # 返回 False 表示无法使用现有运行时
                return False
        else:
            self.log('error', f'Invalid response from runtime API: {data}')
            return False

    def _build_runtime(self) -> None:
        """
        构建运行时镜像。
        
        该方法会与远程 API 通信，获取镜像仓库前缀，
        然后构建自定义的运行时镜像。
        """
        self.log('debug', f'Building RemoteRuntime config:\n{self.config}')
        self.set_runtime_status(RuntimeStatus.BUILDING_RUNTIME)
        
        # 获取镜像仓库前缀
        response = self._send_runtime_api_request(
            'GET',
            f'{self.config.sandbox.remote_runtime_api_url}/registry_prefix',
        )
        response_json = response.json()
        registry_prefix = response_json['registry_prefix']
        
        # 设置运行时镜像仓库环境变量
        os.environ['OH_RUNTIME_RUNTIME_IMAGE_REPO'] = (
            registry_prefix.rstrip('/') + '/runtime'
        )
        self.log(
            'debug',
            f'Runtime image repo: {os.environ["OH_RUNTIME_RUNTIME_IMAGE_REPO"]}',
        )
        
        # 检查基础容器镜像是否已配置
        if self.config.sandbox.base_container_image is None:
            raise ValueError(
                'base_container_image is required to build the runtime image. '
            )
        
        # 如果配置了额外依赖，记录日志
        if self.config.sandbox.runtime_extra_deps:
            self.log(
                'debug',
                f'Installing extra user-provided dependencies in the runtime image: {self.config.sandbox.runtime_extra_deps}',
            )

        # 构建容器镜像
        self.container_image = build_runtime_image(
            self.config.sandbox.base_container_image,
            self.runtime_builder,
            platform=self.config.sandbox.platform,
            extra_deps=self.config.sandbox.runtime_extra_deps,
            force_rebuild=self.config.sandbox.force_rebuild_runtime,
        )

        # 验证镜像是否存在
        response = self._send_runtime_api_request(
            'GET',
            f'{self.config.sandbox.remote_runtime_api_url}/image_exists',
            params={'image': self.container_image},
        )
        if not response.json()['exists']:
            raise AgentRuntimeError(
                f'Container image {self.container_image} does not exist'
            )

    def _start_runtime(self) -> None:
        """
        启动新的运行时。
        
        该方法会准备启动请求并向远程 API 发送启动命令。
        """
        # 准备 /start 端点的请求体
        self.set_runtime_status(RuntimeStatus.STARTING_RUNTIME)
        
        # 获取启动命令
        command = self.get_action_execution_server_startup_command()
        
        # 准备环境变量
        environment: dict[str, str] = {}
        if self.config.debug or os.environ.get('DEBUG', 'false').lower() == 'true':
            environment['DEBUG'] = 'true'
        environment.update(self.config.sandbox.runtime_startup_env_vars)
        
        # 构建启动请求
        start_request: dict[str, Any] = {
            'image': self.container_image,                                           # 容器镜像
            'command': command,                                                      # 启动命令
            'working_dir': '/openhands/code/',                                      # 工作目录
            'environment': environment,                                             # 环境变量
            'session_id': self.sid,                                                # Session ID
            'resource_factor': self.config.sandbox.remote_runtime_resource_factor, # 资源因子
        }
        
        # 如果配置了 sysbox 运行时类，设置对应的运行时类
        if self.config.sandbox.remote_runtime_class == 'sysbox':
            start_request['runtime_class'] = 'sysbox-runc'
        # 我们暂时忽略其他运行时类，因为 None 和 'gvisor' 都映射到 'gvisor'

        # 使用 /start 端点启动沙箱
        try:
            response = self._send_runtime_api_request(
                'POST',
                f'{self.config.sandbox.remote_runtime_api_url}/start',
                json=start_request,
            )
            self._parse_runtime_response(response)
            self.log(
                'debug',
                f'Runtime started. URL: {self.runtime_url}',
            )
        except httpx.HTTPError as e:
            self.log('error', f'Unable to start runtime: {str(e)}')
            raise AgentRuntimeUnavailableError() from e

    def _resume_runtime(self) -> None:
        """
        恢复已停止的运行时。

        步骤：
        1. 显示运行时正在启动的状态更新
        2. 向运行时 API 发送 /resume 请求
        3. 轮询等待运行时就绪
        4. 更新环境变量
        """
        self.log('info', f'Attempting to resume runtime with ID: {self.runtime_id}')
        self.set_runtime_status(RuntimeStatus.STARTING_RUNTIME)
        
        try:
            # 发送恢复请求
            response = self._send_runtime_api_request(
                'POST',
                f'{self.config.sandbox.remote_runtime_api_url}/resume',
                json={'runtime_id': self.runtime_id},
            )
            self.log(
                'info',
                f'Resume API call successful with status code: {response.status_code}',
            )
        except Exception as e:
            self.log('error', f'Failed to call /resume API: {e}', exc_info=True)
            raise

        self.log(
            'info', 'Runtime resume API call completed, waiting for it to be alive...'
        )
        try:
            # 等待运行时就绪
            self._wait_until_alive()
            self.log('info', 'Runtime is now alive after resume')
        except Exception as e:
            self.log(
                'error',
                f'Runtime failed to become alive after resume: {e}',
                exc_info=True,
            )
            raise

        try:
            # 设置初始环境
            self.setup_initial_env()
            self.log('info', 'Successfully set up initial environment after resume')
        except Exception as e:
            self.log(
                'error',
                f'Failed to set up initial environment after resume: {e}',
                exc_info=True,
            )
            raise

        self.log('info', 'Runtime successfully resumed and alive.')

    def _parse_runtime_response(self, response: httpx.Response) -> None:
        """
        解析运行时响应。
        
        从启动或查询响应中提取运行时信息。
        
        Args:
            response (httpx.Response): HTTP 响应对象
        """
        start_response = response.json()
        self.runtime_id = start_response['runtime_id']      # 运行时 ID
        self.runtime_url = start_response['url']           # 运行时 URL
        self.available_hosts = start_response.get('work_hosts', {})  # 可用主机

        # 如果响应中包含 session API 密钥，设置到 session 头部
        if 'session_api_key' in start_response:
            self.session.headers.update(
                {'X-Session-API-Key': start_response['session_api_key']}
            )
            self._session_api_key = start_response['session_api_key']
            self.log(
                'debug',
                'Session API key set',
            )

    @property
    def session_api_key(self) -> str | None:
        """
        获取 Session API 密钥。
        
        Returns:
            str | None: Session API 密钥，如果未设置则返回 None
        """
        return self._session_api_key

    @property
    def vscode_url(self) -> str | None:
        """
        获取 VSCode 服务器的 URL。
        
        Returns:
            str | None: VSCode URL，如果没有 token 则返回 None
        """
        token = super().get_vscode_token()
        if not token:
            return None
        
        # 解析运行时 URL
        _parsed_url = urlparse(self.runtime_url)
        assert isinstance(_parsed_url.scheme, str) and isinstance(
            _parsed_url.netloc, str
        )
        
        # 构建 VSCode URL
        vscode_url = f'{_parsed_url.scheme}://vscode-{_parsed_url.netloc}/?tkn={token}&folder={self.config.workspace_mount_path_in_sandbox}'
        self.log(
            'debug',
            f'VSCode URL: {vscode_url}',
        )
        return vscode_url

    @property
    def web_hosts(self) -> dict[str, int]:
        """
        获取 web hosts 字典。
        
        Returns:
            dict[str, int]: 可用主机到端口的映射
        """
        return self.available_hosts

    def _wait_until_alive(self) -> None:
        """
        等待运行时变为活跃状态。
        
        该方法会持续检查运行时状态，直到其变为就绪状态。
        """
        # 创建重试装饰器
        retry_decorator = tenacity.retry(
            stop=tenacity.stop_after_delay(
                self.config.sandbox.remote_runtime_init_timeout
            )
            | stop_if_should_exit()
            | self._stop_if_closed,
            reraise=True,
            retry=tenacity.retry_if_exception_type(AgentRuntimeNotReadyError),
            wait=tenacity.wait_fixed(2),
        )
        # 执行等待逻辑
        retry_decorator(self._wait_until_alive_impl)()

    def _wait_until_alive_impl(self) -> None:
        """
        等待运行时活跃的实现方法。
        
        Raises:
            AgentRuntimeNotReadyError: 如果运行时未就绪
            AgentRuntimeUnavailableError: 如果运行时不可用
        """
        self.log('debug', f'Waiting for runtime to be alive at url: {self.runtime_url}')
        
        # 获取运行时信息
        runtime_info_response = self._send_runtime_api_request(
            'GET',
            f'{self.config.sandbox.remote_runtime_api_url}/runtime/{self.runtime_id}',
        )
        runtime_data = runtime_info_response.json()
        
        # 验证响应数据
        assert 'runtime_id' in runtime_data
        assert runtime_data['runtime_id'] == self.runtime_id
        assert 'pod_status' in runtime_data
        
        pod_status = runtime_data['pod_status'].lower()
        self.log('debug', f'Pod status: {pod_status}')
        
        # 检查重启次数
        restart_count = runtime_data.get('restart_count', 0)
        if restart_count != 0:
            restart_reasons = runtime_data.get('restart_reasons')
            self.log(
                'debug', f'Pod restarts: {restart_count}, reasons: {restart_reasons}'
            )

        # FIXME: 我们应该在 /start 端点的后端修复这个问题，
        # 确保在返回响应之前创建 Pod。
        # 重试一段时间给集群时间启动 Pod
        if pod_status == 'ready':
            try:
                # 检查运行时是否活跃
                self.check_if_alive()
            except httpx.HTTPError as e:
                self.log(
                    'warning',
                    f"Runtime /alive failed, but pod says it's ready: {str(e)}",
                )
                raise AgentRuntimeNotReadyError(
                    f'Runtime /alive failed to respond with 200: {str(e)}'
                )
            return
        elif (
            pod_status == 'not found'
            or pod_status == 'pending'
            or pod_status == 'running'
        ):  # 注意：Running 还不是 Ready
            raise AgentRuntimeNotReadyError(
                f'Runtime (ID={self.runtime_id}) is not yet ready. Status: {pod_status}'
            )
        elif pod_status in ('failed', 'unknown', 'crashloopbackoff'):
            if pod_status == 'crashloopbackoff':
                raise AgentRuntimeUnavailableError(
                    'Runtime crashed and is being restarted, potentially due to memory usage. Please try again.'
                )
            else:
                raise AgentRuntimeUnavailableError(
                    f'Runtime is unavailable (status: {pod_status}). Please try again.'
                )
        else:
            # 也许这应该是硬故障，但为了防止 API 变化，我们通过
            self.log('warning', f'Unknown pod status: {pod_status}')

        self.log(
            'debug',
            f'Waiting for runtime pod to be active. Current status: {pod_status}',
        )
        raise AgentRuntimeNotReadyError()

    def close(self) -> None:
        """
        关闭远程运行时。
        
        根据配置决定是暂停、停止还是保持运行时活跃。
        """
        # 如果是附加到现有运行时，只调用父类关闭方法
        if self.attach_to_existing:
            super().close()
            return
        
        # 如果配置了保持运行时活跃
        if self.config.sandbox.keep_runtime_alive:
            # 如果配置了暂停关闭的运行时
            if self.config.sandbox.pause_closed_runtimes:
                try:
                    if not self._runtime_closed:
                        # 发送暂停请求
                        self._send_runtime_api_request(
                            'POST',
                            f'{self.config.sandbox.remote_runtime_api_url}/pause',
                            json={'runtime_id': self.runtime_id},
                        )
                        self.log('info', 'Runtime paused.')
                except Exception as e:
                    self.log('error', f'Unable to pause runtime: {str(e)}')
                    raise e
            super().close()
            return
        
        # 停止运行时
        try:
            if not self._runtime_closed:
                self._send_runtime_api_request(
                    'POST',
                    f'{self.config.sandbox.remote_runtime_api_url}/stop',
                    json={'runtime_id': self.runtime_id},
                )
                self.log('info', 'Runtime stopped.')
        except Exception as e:
            self.log('error', f'Unable to stop runtime: {str(e)}')
            raise e
        finally:
            super().close()

    def _send_runtime_api_request(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        """
        发送运行时 API 请求。
        
        Args:
            method (str): HTTP 方法
            url (str): 请求 URL
            **kwargs: 其他请求参数
            
        Returns:
            httpx.Response: HTTP 响应
            
        Raises:
            httpx.TimeoutException: 如果请求超时
        """
        try:
            # 设置超时时间
            kwargs['timeout'] = self.config.sandbox.remote_runtime_api_timeout
            return send_request(self.session, method, url, **kwargs)
        except httpx.TimeoutException:
            self.log(
                'error',
                f'No response received within the timeout period for url: {url}',
            )
            raise

    def _send_action_server_request(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        """
        发送 Action 服务器请求。
        
        如果启用了重试，会使用 tenacity 进行重试逻辑。
        
        Args:
            method (str): HTTP 方法
            url (str): 请求 URL
            **kwargs: 其他请求参数
            
        Returns:
            httpx.Response: HTTP 响应
        """
        if not self.config.sandbox.remote_runtime_enable_retries:
            return self._send_action_server_request_impl(method, url, **kwargs)

        # 创建重试装饰器
        retry_decorator = tenacity.retry(
            retry=tenacity.retry_if_exception_type(httpx.NetworkError),
            stop=tenacity.stop_after_attempt(3)
            | stop_if_should_exit()
            | self._stop_if_closed,
            before_sleep=tenacity.before_sleep_log(logger, logging.WARNING),
            wait=tenacity.wait_exponential(multiplier=1, min=4, max=60),
        )
        return retry_decorator(self._send_action_server_request_impl)(
            method, url, **kwargs
        )

    def _send_action_server_request_impl(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response:
        """
        发送 Action 服务器请求的实现方法。
        
        处理各种 HTTP 错误情况，包括运行时暂停、断开连接等。
        
        Args:
            method (str): HTTP 方法
            url (str): 请求 URL
            **kwargs: 其他请求参数
            
        Returns:
            httpx.Response: HTTP 响应
            
        Raises:
            AgentRuntimeDisconnectedError: 如果运行时断开连接
            httpx.TimeoutException: 如果请求超时
        """
        try:
            return super()._send_action_server_request(method, url, **kwargs)
        except httpx.TimeoutException:
            self.log(
                'error',
                f'No response received within the timeout period for url: {url}',
            )
            raise

        except httpx.HTTPError as e:
            # 处理 404, 502, 504 错误
            if hasattr(e, 'response') and e.response.status_code in (404, 502, 504):
                if e.response.status_code == 404:
                    raise AgentRuntimeDisconnectedError(
                        f'Runtime is not responding. This may be temporary, please try again. Original error: {e}'
                    ) from e
                else:  # 502, 504
                    raise AgentRuntimeDisconnectedError(
                        f'Runtime is temporarily unavailable. This may be due to a restart or network issue, please try again. Original error: {e}'
                    ) from e
            # 处理 503 错误（运行时暂停）
            elif hasattr(e, 'response') and e.response.status_code == 503:
                if self.config.sandbox.keep_runtime_alive:
                    self.log(
                        'info',
                        f'Runtime appears to be paused (503 response). Runtime ID: {self.runtime_id}, URL: {url}',
                    )
                    try:
                        # 尝试恢复运行时
                        self._resume_runtime()
                        self.log(
                            'info', 'Successfully resumed runtime after 503 response'
                        )
                        # 重新发送请求
                        return super()._send_action_server_request(
                            method, url, **kwargs
                        )
                    except Exception as resume_error:
                        self.log(
                            'error',
                            f'Failed to resume runtime after 503 response: {resume_error}',
                            exc_info=True,
                        )
                        raise AgentRuntimeDisconnectedError(
                            f'Runtime is paused and could not be resumed. Original error: {e}, Resume error: {resume_error}'
                        ) from resume_error
                else:
                    self.log(
                        'info',
                        'Runtime appears to be paused (503 response) but keep_runtime_alive is False',
                    )
                    raise AgentRuntimeDisconnectedError(
                        f'Runtime is temporarily unavailable. This may be due to a restart or network issue, please try again. Original error: {e}'
                    ) from e
            else:
                raise e

    def _stop_if_closed(self, retry_state: RetryCallState) -> bool:
        """
        检查运行时是否已关闭的停止条件。
        
        Args:
            retry_state (RetryCallState): 重试状态
            
        Returns:
            bool: 如果运行时已关闭返回 True
        """
        return self._runtime_closed

    def get_action_execution_server_startup_command(self):
        """
        获取 Action 执行服务器的启动命令。
        
        Returns:
            启动命令列表
        """
        return get_action_execution_server_startup_command(
            server_port=self.port,
            plugins=self.plugins,
            app_config=self.config,
            main_module=self.main_module,
        )