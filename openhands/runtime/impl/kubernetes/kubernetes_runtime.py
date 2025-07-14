from functools import lru_cache
from typing import Callable
from uuid import UUID

import tenacity
import yaml
from kubernetes import client, config
from kubernetes.client.models import (
    V1Container,
    V1ContainerPort,
    V1EnvVar,
    V1HTTPIngressPath,
    V1HTTPIngressRuleValue,
    V1Ingress,
    V1IngressBackend,
    V1IngressRule,
    V1IngressServiceBackend,
    V1IngressSpec,
    V1IngressTLS,
    V1ObjectMeta,
    V1PersistentVolumeClaim,
    V1PersistentVolumeClaimSpec,
    V1PersistentVolumeClaimVolumeSource,
    V1Pod,
    V1PodSpec,
    V1ResourceRequirements,
    V1SecurityContext,
    V1Service,
    V1ServiceBackendPort,
    V1ServicePort,
    V1ServiceSpec,
    V1Toleration,
    V1Volume,
    V1VolumeMount,
)

from openhands.core.config import OpenHandsConfig
from openhands.core.exceptions import (
    AgentRuntimeDisconnectedError,
    AgentRuntimeNotFoundError,
)
from openhands.core.logger import DEBUG
from openhands.core.logger import openhands_logger as logger
from openhands.events import EventStream
from openhands.integrations.provider import PROVIDER_TOKEN_TYPE
from openhands.runtime.impl.action_execution.action_execution_client import (
    ActionExecutionClient,
)
from openhands.runtime.plugins import PluginRequirement
from openhands.runtime.runtime_status import RuntimeStatus
from openhands.runtime.utils.command import get_action_execution_server_startup_command
from openhands.utils.async_utils import call_sync_from_async
from openhands.utils.shutdown_listener import add_shutdown_listener
from openhands.utils.tenacity_stop import stop_if_should_exit

# Pod 名称前缀，用于标识 OpenHands runtime 的 Pod
POD_NAME_PREFIX = 'openhands-runtime-'
# Pod 标签，用于 Kubernetes 资源的筛选和管理
POD_LABEL = 'openhands-runtime'


class KubernetesRuntime(ActionExecutionClient):
    """
    OpenHands 的 Kubernetes 运行时实现，与 Kind 集群配合工作。

    该运行时在 Kubernetes 集群中创建 Pod 来运行 Agent 代码。
    使用 Kubernetes Python 客户端来创建和管理 Pod。

    Args:
        config (OpenHandsConfig): 应用配置对象
        event_stream (EventStream): 用于订阅的事件流
        sid (str, optional): Session ID，会话标识符。默认为 'default'
        plugins (list[PluginRequirement] | None, optional): 插件需求列表。默认为 None
        env_vars (dict[str, str] | None, optional): 要设置的环境变量。默认为 None
        status_callback (Callable | None, optional): 状态更新回调函数。默认为 None
        attach_to_existing (bool, optional): 是否附加到现有的 Pod。默认为 False
        headless_mode (bool, optional): 是否在无头模式下运行。默认为 True
    """

    # 关闭监听器的 UUID，用于清理资源
    _shutdown_listener_id: UUID | None = None
    # Kubernetes 命名空间
    _namespace: str = ''

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
    ):
        # 如果还没有设置关闭监听器，添加一个
        # 当用户按 Ctrl+C 时会触发清理操作
        if not KubernetesRuntime._shutdown_listener_id:
            KubernetesRuntime._shutdown_listener_id = add_shutdown_listener(
                lambda: KubernetesRuntime._cleanup_k8s_resources(
                    namespace=self._k8s_namespace,
                    remove_pvc=True,
                    conversation_id=self.sid,
                )  # 这在用户按 Ctrl+C 时触发
            )
        
        # 保存配置对象
        self.config = config
        # 运行时是否已初始化的标志
        self._runtime_initialized: bool = False
        # 状态回调函数
        self.status_callback = status_callback

        # 加载并验证 Kubernetes 配置
        if self.config.kubernetes is None:
            raise ValueError(
                'Kubernetes configuration is required when using KubernetesRuntime. '
                'Please add a [kubernetes] section to your configuration.'
            )

        # 获取 Kubernetes 配置
        self._k8s_config = self.config.kubernetes
        # 获取 Kubernetes 命名空间
        self._k8s_namespace = self._k8s_config.namespace
        # 设置类级别的命名空间变量
        KubernetesRuntime._namespace = self._k8s_namespace

        # 初始化端口配置，设置为有效范围内的默认值
        self._container_port = 8080  # 默认内部容器端口
        self._vscode_port = 8081  # 默认 VSCode 端口
        self._app_ports: list[int] = [
            30082,
            30083,
        ]  # 有效范围内的默认应用端口，Agent 在暴露应用时优先使用这些端口

        # 初始化 Kubernetes 客户端
        self.k8s_client, self.k8s_networking_client = self._init_kubernetes_client()

        # 获取 Pod 镜像
        self.pod_image = self.config.sandbox.runtime_container_image
        if not self.pod_image:
            # 如果没有设置 runtime_container_image，使用 base_container_image 作为后备
            self.pod_image = self.config.sandbox.base_container_image

        # 生成 Pod 名称
        self.pod_name = POD_NAME_PREFIX + sid

        # 使用初始端口值初始化 API URL
        self.k8s_local_url = f'http://{self._get_svc_name(self.pod_name)}.{self._k8s_namespace}.svc.cluster.local'
        self.api_url = f'{self.k8s_local_url}:{self._container_port}'

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

    @staticmethod
    def _get_svc_name(pod_name: str) -> str:
        """
        获取 Pod 对应的 Service 名称。
        
        Args:
            pod_name (str): Pod 名称
            
        Returns:
            str: Service 名称
        """
        return f'{pod_name}-svc'

    @staticmethod
    def _get_vscode_svc_name(pod_name: str) -> str:
        """
        获取 Pod 对应的 VSCode Service 名称。
        
        Args:
            pod_name (str): Pod 名称
            
        Returns:
            str: VSCode Service 名称
        """
        return f'{pod_name}-svc-code'

    @staticmethod
    def _get_vscode_ingress_name(pod_name: str) -> str:
        """
        获取 Pod 对应的 VSCode Ingress 名称。
        
        Args:
            pod_name (str): Pod 名称
            
        Returns:
            str: VSCode Ingress 名称
        """
        return f'{pod_name}-ingress-code'

    @staticmethod
    def _get_vscode_tls_secret_name(pod_name: str) -> str:
        """
        获取 VSCode Ingress 对应的 TLS Secret 名称。
        
        Args:
            pod_name (str): Pod 名称
            
        Returns:
            str: TLS Secret 名称
        """
        return f'{pod_name}-tls-secret'

    @staticmethod
    def _get_pvc_name(pod_name: str) -> str:
        """
        获取 Pod 对应的 PVC (PersistentVolumeClaim) 名称。
        
        Args:
            pod_name (str): Pod 名称
            
        Returns:
            str: PVC 名称
        """
        return f'{pod_name}-pvc'

    @staticmethod
    def _get_pod_name(sid: str) -> str:
        """
        根据 Session ID 获取 Pod 名称。
        
        Args:
            sid (str): Session ID
            
        Returns:
            str: Pod 名称
        """
        return POD_NAME_PREFIX + sid

    @property
    def action_execution_server_url(self):
        """
        获取 Action 执行服务器的 URL。
        
        Returns:
            str: API URL
        """
        return self.api_url

    @property
    def node_selector(self) -> dict[str, str] | None:
        """
        获取节点选择器配置。
        
        根据配置中的 node_selector_key 和 node_selector_val 生成节点选择器字典。
        用于指定 Pod 运行在特定标签的节点上。
        
        Returns:
            dict[str, str] | None: 节点选择器字典，如果未配置则返回 None
        """
        if (
            not self._k8s_config.node_selector_key
            or not self._k8s_config.node_selector_val
        ):
            return None
        return {self._k8s_config.node_selector_key: self._k8s_config.node_selector_val}

    @property
    def tolerations(self) -> list[V1Toleration] | None:
        """
        获取容忍度 (Toleration) 配置。
        
        解析配置中的 YAML 格式容忍度设置，用于允许 Pod 调度到有污点的节点上。
        
        Returns:
            list[V1Toleration] | None: 容忍度列表，如果解析失败或未配置则返回 None
        """
        if not self._k8s_config.tolerations_yaml:
            return None
        tolerations_yaml_str = self._k8s_config.tolerations_yaml
        tolerations = []
        try:
            # 解析 YAML 配置
            tolerations_data = yaml.safe_load(tolerations_yaml_str)
            if isinstance(tolerations_data, list):
                # 将每个容忍度配置转换为 V1Toleration 对象
                for toleration in tolerations_data:
                    tolerations.append(V1Toleration(**toleration))
            else:
                logger.error(
                    f'Invalid tolerations format. Should be type list: {tolerations_yaml_str}. Expected a list.'
                )
                return None
        except yaml.YAMLError as e:
            logger.error(
                f'Error parsing tolerations YAML: {tolerations_yaml_str}. Error: {e}'
            )
            return None
        return tolerations

    async def connect(self):
        """
        连接到运行时，通过创建或附加到 Pod 来实现。
        
        该方法会：
        1. 尝试附加到现有 Pod（如果配置了 attach_to_existing）
        2. 或者创建新的 Kubernetes 资源
        3. 等待 Pod 就绪
        4. 设置初始环境
        """
        self.log('info', f'Connecting to runtime with conversation ID: {self.sid}')
        self.log('info', f'self._attach_to_existing: {self.attach_to_existing}')
        # 设置运行时状态为启动中
        self.set_runtime_status(RuntimeStatus.STARTING_RUNTIME)
        self.log('info', f'Using API URL {self.api_url}')

        try:
            # 尝试附加到现有 Pod
            await call_sync_from_async(self._attach_to_pod)
        except client.rest.ApiException as e:
            # 如果不是设置为附加到现有 Pod，忽略错误并初始化 K8s 资源
            if self.attach_to_existing:
                self.log(
                    'error',
                    f'Pod {self.pod_name} not found or cannot connect to it.',
                )
                raise AgentRuntimeDisconnectedError from e

            self.log('info', f'Starting runtime with image: {self.pod_image}')
            try:
                # 初始化 Kubernetes 资源
                await call_sync_from_async(self._init_k8s_resources)
                self.log(
                    'info',
                    f'Pod started: {self.pod_name}. VSCode URL: {self.vscode_url}',
                )
            except Exception as init_error:
                self.log('error', f'Failed to initialize k8s resources: {init_error}')
                raise AgentRuntimeNotFoundError(
                    f'Failed to initialize kubernetes resources: {init_error}'
                ) from init_error

        if not self.attach_to_existing:
            self.log('info', 'Waiting for pod to become ready ...')
            self.set_runtime_status(RuntimeStatus.STARTING_RUNTIME)
        try:
            # 等待 Pod 就绪
            await call_sync_from_async(self._wait_until_ready)
        except Exception as alive_error:
            self.log('error', f'Failed to connect to runtime: {alive_error}')
            self.send_error_message(
                'ERROR$RUNTIME_CONNECTION',
                f'Failed to connect to runtime: {alive_error}',
            )
            raise AgentRuntimeDisconnectedError(
                f'Failed to connect to runtime: {alive_error}'
            ) from alive_error

        if not self.attach_to_existing:
            self.log('info', 'Runtime is ready.')

        if not self.attach_to_existing:
            # 设置初始环境
            await call_sync_from_async(self.setup_initial_env)

        self.log(
            'info',
            f'Pod initialized with plugins: {[plugin.name for plugin in self.plugins]}. VSCode URL: {self.vscode_url}',
        )
        if not self.attach_to_existing:
            # 设置运行时状态为就绪
            self.set_runtime_status(RuntimeStatus.READY)
        self._runtime_initialized = True

    def _attach_to_pod(self):
        """
        附加到现有的 Pod。
        
        检查指定名称的 Pod 是否存在且处于运行状态。
        如果 Pod 存在但未就绪，会等待其变为就绪状态。
        
        Returns:
            bool: 如果成功附加则返回 True
            
        Raises:
            AgentRuntimeDisconnectedError: 如果 Pod 不存在或无法连接
        """
        try:
            # 读取指定命名空间中的 Pod
            pod = self.k8s_client.read_namespaced_pod(
                name=self.pod_name, namespace=self._k8s_namespace
            )

            # 检查 Pod 是否处于运行状态
            if pod.status.phase != 'Running':
                try:
                    # 等待 Pod 就绪
                    self._wait_until_ready()
                except TimeoutError:
                    raise AgentRuntimeDisconnectedError(
                        f'Pod {self.pod_name} exists but failed to become ready.'
                    )

            self.log('info', f'Successfully attached to pod {self.pod_name}')
            return True

        except client.rest.ApiException as e:
            self.log('error', f'Failed to attach to pod: {e}')
            raise

    @tenacity.retry(
        stop=tenacity.stop_after_delay(300) | stop_if_should_exit(),
        retry=tenacity.retry_if_exception_type(TimeoutError),
        reraise=True,
        wait=tenacity.wait_fixed(2),
    )
    def _wait_until_ready(self):
        """
        通过检查 Kubernetes 中的 Pod 状态等待运行时服务器就绪。
        
        该方法会持续检查 Pod 的状态，直到 Pod 处于 Running 状态且 Ready 条件为 True。
        使用 tenacity 装饰器进行重试，最多等待 300 秒，每 2 秒重试一次。
        
        Returns:
            bool: 如果 Pod 就绪则返回 True
            
        Raises:
            TimeoutError: 如果 Pod 仍未处于运行状态
        """
        self.log('info', f'Checking if pod {self.pod_name} is ready in Kubernetes')
        # 读取 Pod 状态
        pod = self.k8s_client.read_namespaced_pod(
            name=self.pod_name, namespace=self._k8s_namespace
        )
        
        # 检查 Pod 是否处于 Running 状态且有状态条件
        if pod.status.phase == 'Running' and pod.status.conditions:
            # 检查每个状态条件
            for condition in pod.status.conditions:
                # 查找 Ready 条件且状态为 True
                if condition.type == 'Ready' and condition.status == 'True':
                    self.log('info', f'Pod {self.pod_name} is ready!')
                    return True  # Pod 就绪时退出函数

        self.log(
            'info',
            f'Pod {self.pod_name} is not ready yet. Current phase: {pod.status.phase}',
        )
        # 抛出超时错误以触发重试
        raise TimeoutError(f'Pod {self.pod_name} is not in Running state yet.')

    @staticmethod
    @lru_cache(maxsize=1)
    def _init_kubernetes_client() -> tuple[client.CoreV1Api, client.NetworkingV1Api]:
        """
        初始化 Kubernetes 客户端。
        
        使用 LRU 缓存确保客户端只初始化一次。
        首先尝试加载集群内配置，即使是使用 mirrord 的本地使用也会技术上使用集群内配置。
        
        Returns:
            tuple[client.CoreV1Api, client.NetworkingV1Api]: Core API 和 Networking API 客户端
            
        Raises:
            Exception: 如果无法初始化 Kubernetes 客户端
        """
        try:
            # 加载集群内配置
            config.load_incluster_config()  # 即使是使用 mirrord 的本地使用也会技术上使用集群内配置
            return client.CoreV1Api(), client.NetworkingV1Api()
        except Exception as ex:
            logger.error(
                'Failed to initialize Kubernetes client. Make sure you have kubectl configured correctly or are running in a Kubernetes cluster.',
            )
            raise ex

    @staticmethod
    def _cleanup_k8s_resources(
        namespace: str, remove_pvc: bool = False, conversation_id: str = ''
    ):
        """
        清理命名空间中带有我们前缀的 Kubernetes 资源。

        Args:
            namespace (str): 要清理资源的命名空间
            remove_pvc (bool, optional): 如果为 True，也会删除持久卷声明。默认为 False
            conversation_id (str, optional): 对话 ID，用于确定要删除的具体资源
        """
        try:
            # 获取 Kubernetes 客户端
            k8s_api, k8s_networking_api = KubernetesRuntime._init_kubernetes_client()

            # 生成资源名称
            pod_name = KubernetesRuntime._get_pod_name(conversation_id)
            service_name = KubernetesRuntime._get_svc_name(pod_name)
            vscode_service_name = KubernetesRuntime._get_vscode_svc_name(pod_name)
            ingress_name = KubernetesRuntime._get_vscode_ingress_name(pod_name)
            pvc_name = KubernetesRuntime._get_pvc_name(pod_name)

            try:
                # 如果请求删除 PVC
                if remove_pvc:
                    k8s_api.delete_namespaced_persistent_volume_claim(
                        name=pvc_name,
                        namespace=namespace,
                        body=client.V1DeleteOptions(),
                    )
                    logger.info(f'Deleted PVC {pvc_name}')

                # 删除 Pod
                k8s_api.delete_namespaced_pod(
                    name=pod_name,
                    namespace=namespace,
                    body=client.V1DeleteOptions(),
                )
                logger.info(f'Deleted pod {pod_name}')

                # 删除 Service
                k8s_api.delete_namespaced_service(
                    name=service_name,
                    namespace=namespace,
                )
                logger.info(f'Deleted service {service_name}')
                
                # 删除 VSCode Service
                k8s_api.delete_namespaced_service(
                    name=vscode_service_name, namespace=namespace
                )
                logger.info(f'Deleted service {vscode_service_name}')

                # 删除 Ingress
                k8s_networking_api.delete_namespaced_ingress(
                    name=ingress_name, namespace=namespace
                )
                logger.info(f'Deleted ingress {ingress_name}')
            except client.rest.ApiException:
                # Service 可能不存在，忽略错误
                pass
            logger.info('Cleaned up Kubernetes resources')
        except Exception as e:
            logger.error(f'Error cleaning up k8s resources: {e}')

    def _get_pvc_manifest(self):
        """
        为运行时 Pod 创建 PVC (PersistentVolumeClaim) 清单。
        
        PVC 用于为 Pod 提供持久化存储，确保数据在 Pod 重启后仍然保留。
        
        Returns:
            V1PersistentVolumeClaim: PVC 清单对象
        """
        # 创建 PVC 对象
        pvc = V1PersistentVolumeClaim(
            api_version='v1',
            kind='PersistentVolumeClaim',
            metadata=V1ObjectMeta(
                name=self._get_pvc_name(self.pod_name), 
                namespace=self._k8s_namespace
            ),
            spec=V1PersistentVolumeClaimSpec(
                access_modes=['ReadWriteOnce'],  # 单节点读写访问模式
                resources=client.V1ResourceRequirements(
                    requests={'storage': self._k8s_config.pvc_storage_size}  # 存储大小请求
                ),
                storage_class_name=self._k8s_config.pvc_storage_class,  # 存储类名称
            ),
        )

        return pvc

    def _get_vscode_service_manifest(self):
        """
        为 VSCode 服务器创建 Service 清单。
        
        该 Service 用于暴露 Pod 中的 VSCode 服务，使其可以被其他组件访问。
        
        Returns:
            V1Service: VSCode Service 清单对象
        """
        # 创建 Service 规格
        vscode_service_spec = V1ServiceSpec(
            selector={'app': POD_LABEL, 'session': self.sid},  # 选择器，用于匹配 Pod
            type='ClusterIP',  # 集群内部 IP 类型
            ports=[
                V1ServicePort(
                    port=self._vscode_port,  # 服务端口
                    target_port='vscode',   # 目标端口名称
                    name='code',           # 端口名称
                )
            ],
        )

        # 创建 Service 对象
        vscode_service = V1Service(
            metadata=V1ObjectMeta(name=self._get_vscode_svc_name(self.pod_name)),
            spec=vscode_service_spec,
        )
        return vscode_service

    def _get_runtime_service_manifest(self):
        """
        为运行时 Pod 的 execution-server 创建 Service 清单。
        
        该 Service 用于暴露 Pod 中的动作执行服务器，使其可以接收和处理请求。
        
        Returns:
            V1Service: Runtime Service 清单对象
        """
        # 创建 Service 规格
        service_spec = V1ServiceSpec(
            selector={'app': POD_LABEL, 'session': self.sid},  # 选择器，用于匹配 Pod
            type='ClusterIP',  # 集群内部 IP 类型
            ports=[
                V1ServicePort(
                    port=self._container_port,     # 服务端口
                    target_port='http',           # 目标端口名称
                    name='execution-server',      # 端口名称
                )
            ],
        )

        # 创建 Service 对象
        service = V1Service(
            metadata=V1ObjectMeta(name=self._get_svc_name(self.pod_name)),
            spec=service_spec,
        )
        return service

    def _get_runtime_pod_manifest(self):
        """
        为运行时沙箱创建 Pod 清单。
        
        该方法构建完整的 Pod 配置，包括容器、环境变量、卷挂载、资源限制等。
        
        Returns:
            V1Pod: Pod 清单对象
        """
        # 准备环境变量
        environment = [
            V1EnvVar(name='port', value=str(self._container_port)),        # 端口号
            V1EnvVar(name='PYTHONUNBUFFERED', value='1'),                 # Python 无缓冲输出
            V1EnvVar(name='VSCODE_PORT', value=str(self._vscode_port)),   # VSCode 端口
        ]

        # 如果启用了调试模式，添加 DEBUG 环境变量
        if self.config.debug or DEBUG:
            environment.append(V1EnvVar(name='DEBUG', value='true'))

        # 添加运行时启动环境变量
        for key, value in self.config.sandbox.runtime_startup_env_vars.items():
            environment.append(V1EnvVar(name=key, value=value))

        # 准备卷挂载（如果配置了 workspace）
        volume_mounts = [
            V1VolumeMount(
                name='workspace-volume',  # 卷名称
                mount_path=self.config.workspace_mount_path_in_sandbox,  # 挂载路径
            ),
        ]
        # 准备卷定义
        volumes = [
            V1Volume(
                name='workspace-volume',  # 卷名称
                persistent_volume_claim=V1PersistentVolumeClaimVolumeSource(
                    claim_name=self._get_pvc_name(self.pod_name)  # PVC 名称
                ),
            )
        ]

        # 准备容器端口
        container_ports = [
            V1ContainerPort(container_port=self._container_port, name='http'),
        ]

        # 如果启用了 VSCode，添加 VSCode 端口
        if self.vscode_enabled:
            container_ports.append(
                V1ContainerPort(container_port=self._vscode_port, name='vscode')
            )

        # 添加应用端口
        for port in self._app_ports:
            container_ports.append(V1ContainerPort(container_port=port))

        # 定义就绪探针，用于检查容器是否准备好接收流量
        health_check = client.V1Probe(
            http_get=client.V1HTTPGetAction(
                path='/alive',                    # 健康检查路径
                port=self._container_port,       # 健康检查端口
            ),
            initial_delay_seconds=5,              # 初始延迟时间（秒）
            period_seconds=10,                    # 检查间隔（秒）
            timeout_seconds=5,                    # 超时时间（秒）
            success_threshold=1,                  # 成功阈值
            failure_threshold=3,                  # 失败阈值
        )
        
        # 准备启动命令
        # 生成沙箱运行时 Pod 的入口点命令
        command = get_action_execution_server_startup_command(
            server_port=self._container_port,
            plugins=self.plugins,
            app_config=self.config,
            override_user_id=0,       # 如果使用默认的 app_config.run_as_openhands，由于文件权限问题无法在 VSCode 中编辑文件
            override_username='root',
        )

        # 根据配置准备资源需求
        resources = V1ResourceRequirements(
            limits={'memory': self._k8s_config.resource_memory_limit},       # 内存限制
            requests={
                'cpu': self._k8s_config.resource_cpu_request,               # CPU 请求
                'memory': self._k8s_config.resource_memory_request,         # 内存请求
            },
        )

        # 为容器设置安全上下文
        security_context = V1SecurityContext(privileged=self._k8s_config.privileged)

        # 创建容器定义
        container = V1Container(
            name='runtime',                           # 容器名称
            image=self.pod_image,                    # 容器镜像
            command=command,                         # 启动命令
            env=environment,                         # 环境变量
            ports=container_ports,                   # 端口配置
            volume_mounts=volume_mounts,             # 卷挂载
            working_dir='/openhands/code/',          # 工作目录
            resources=resources,                     # 资源需求
            readiness_probe=health_check,            # 就绪探针
            security_context=security_context,       # 安全上下文
        )

        # 创建 Pod 定义
        image_pull_secrets = None
        # 如果配置了镜像拉取密钥，添加到 Pod 规格中
        if self._k8s_config.image_pull_secret:
            image_pull_secrets = [
                client.V1LocalObjectReference(name=self._k8s_config.image_pull_secret)
            ]
        
        pod = V1Pod(
            metadata=V1ObjectMeta(
                name=self.pod_name, 
                labels={'app': POD_LABEL, 'session': self.sid}  # Pod 标签
            ),
            spec=V1PodSpec(
                containers=[container],                # 容器列表
                volumes=volumes,                       # 卷列表
                restart_policy='Never',                # 重启策略
                image_pull_secrets=image_pull_secrets, # 镜像拉取密钥
                node_selector=self.node_selector,      # 节点选择器
                tolerations=self.tolerations,          # 容忍度
            ),
        )

        return pod

    def _get_vscode_ingress_manifest(self):
        """
        为 VSCode 服务器创建 Ingress 清单。
        
        Ingress 用于管理从集群外部到 VSCode 服务的 HTTP 访问。
        
        Returns:
            V1Ingress: VSCode Ingress 清单对象
        """
        tls = []
        # 如果配置了 TLS 密钥，添加 TLS 配置
        if self._k8s_config.ingress_tls_secret:
            runtime_tls = V1IngressTLS(
                hosts=[self.ingress_domain],                        # TLS 主机列表
                secret_name=self._k8s_config.ingress_tls_secret,   # TLS 密钥名称
            )
            tls = [runtime_tls]

        # 定义路由规则
        rules = [
            V1IngressRule(
                host=self.ingress_domain,  # 主机名
                http=V1HTTPIngressRuleValue(
                    paths=[
                        V1HTTPIngressPath(
                            path='/',              # 路径
                            path_type='Prefix',    # 路径类型
                            backend=V1IngressBackend(
                                service=V1IngressServiceBackend(
                                    port=V1ServiceBackendPort(
                                        number=self._vscode_port,  # 后端服务端口
                                    ),
                                    name=self._get_vscode_svc_name(self.pod_name),  # 后端服务名称
                                )
                            ),
                        )
                    ]
                ),
            )
        ]
        
        # 创建 Ingress 规格
        ingress_spec = V1IngressSpec(rules=rules, tls=tls)

        # 创建 Ingress 对象
        ingress = V1Ingress(
            api_version='networking.k8s.io/v1',
            metadata=V1ObjectMeta(
                name=self._get_vscode_ingress_name(self.pod_name),
                annotations={
                    # 外部 DNS 注解，用于自动管理 DNS 记录
                    'external-dns.alpha.kubernetes.io/hostname': self.ingress_domain
                },
            ),
            spec=ingress_spec,
        )

        return ingress

    def _pvc_exists(self):
        """
        检查 PVC 是否已经存在。
        
        Returns:
            bool: 如果 PVC 存在返回 True，否则返回 False
        """
        try:
            # 尝试读取 PVC
            pvc = self.k8s_client.read_namespaced_persistent_volume_claim(
                name=self._get_pvc_name(self.pod_name), 
                namespace=self._k8s_namespace
            )
            return pvc is not None
        except client.rest.ApiException as e:
            # 如果是 404 错误，说明 PVC 不存在
            if e.status == 404:
                return False
            self.log('error', f'Error checking PVC existence: {e}')

    def _init_k8s_resources(self):
        """
        初始化 Kubernetes 资源。
        
        该方法会创建运行 OpenHands runtime 所需的所有 Kubernetes 资源，
        包括 PVC、Pod、Service 和 Ingress。
        """
        self.log('info', 'Preparing to start pod...')
        self.set_runtime_status(RuntimeStatus.STARTING_RUNTIME)

        self.log('info', f'Runtime will be accessible at {self.api_url}')

        # 获取各种资源的清单
        pod = self._get_runtime_pod_manifest()
        service = self._get_runtime_service_manifest()
        vscode_service = self._get_vscode_service_manifest()
        pvc_manifest = self._get_pvc_manifest()
        ingress = self._get_vscode_ingress_manifest()

        # 在 Kubernetes 中创建 Pod
        try:
            # 如果 PVC 不存在，创建 PVC
            if not self._pvc_exists():
                self.k8s_client.create_namespaced_persistent_volume_claim(
                    namespace=self._k8s_namespace, body=pvc_manifest
                )
                self.log('info', f'Created PVC {self._get_pvc_name(self.pod_name)}')
            
            # 创建 Pod
            self.k8s_client.create_namespaced_pod(
                namespace=self._k8s_namespace, body=pod
            )
            self.log('info', f'Created pod {self.pod_name}.')
            
            # 创建用于外部访问的 Service
            self.k8s_client.create_namespaced_service(
                namespace=self._k8s_namespace, body=service
            )
            self.log('info', f'Created service {self._get_svc_name(self.pod_name)}')

            # 为 VSCode 服务器创建第二个 Service
            self.k8s_client.create_namespaced_service(
                namespace=self._k8s_namespace, body=vscode_service
            )
            self.log(
                'info', f'Created service {self._get_vscode_svc_name(self.pod_name)}'
            )

            # 创建 VSCode Ingress
            self.k8s_networking_client.create_namespaced_ingress(
                namespace=self._k8s_namespace, body=ingress
            )
            self.log(
                'info',
                f'Created ingress {self._get_vscode_ingress_name(self.pod_name)}',
            )

            # 等待 Pod 运行
            self._wait_until_ready()

        except client.rest.ApiException as e:
            self.log('error', f'Failed to create pod and services: {e}')
            raise
        except RuntimeError as e:
            self.log('error', f'Port forwarding failed: {e}')
            raise

    def close(self):
        """
        关闭运行时并清理资源。
        
        该方法在单个对话问题得到答案或标签页关闭时调用。
        根据配置决定是否保持运行时活跃或清理资源。
        """
        self.log(
            'info',
            f'Closing runtime and cleaning up resources for conersation ID: {self.sid}',
        )
        # 首先调用父类的 close 方法
        super().close()

        # 如果应该保持运行时活跃或者是附加到现有的，则提前返回
        if self.config.sandbox.keep_runtime_alive or self.attach_to_existing:
            self.log(
                'info', 'Keeping runtime alive due to configuration or attach mode'
            )
            return

        try:
            # 清理 Kubernetes 资源，但不删除 PVC
            self._cleanup_k8s_resources(
                namespace=self._k8s_namespace,
                remove_pvc=False,
                conversation_id=self.sid,
            )
        except Exception as e:
            self.log('error', f'Error closing runtime: {e}')

    @property
    def ingress_domain(self) -> str:
        """
        获取运行时的 Ingress 域名。
        
        Returns:
            str: Ingress 域名，格式为 {session_id}.{base_domain}
        """
        return f'{self.sid}.{self._k8s_config.ingress_domain}'

    @property
    def vscode_url(self) -> str | None:
        """
        获取 VSCode 服务器的 URL（如果启用）。
        
        Returns:
            str | None: VSCode URL，如果未启用或无 token 则返回 None
        """
        if not self.vscode_enabled:
            return None
        token = super().get_vscode_token()
        if not token:
            return None

        # 根据是否配置了 TLS 密钥决定协议
        protocol = 'https' if self._k8s_config.ingress_tls_secret else 'http'
        vscode_url = f'{protocol}://{self.ingress_domain}/?tkn={token}&folder={self.config.workspace_mount_path_in_sandbox}'
        self.log('info', f'VSCode URL: {vscode_url}')
        return vscode_url

    @property
    def web_hosts(self) -> dict[str, int]:
        """
        获取用于浏览器访问的 web hosts 字典映射。
        
        Returns:
            dict[str, int]: 主机名到端口的映射字典
        """
        hosts = {}
        # 为每个应用端口创建主机映射
        for idx, port in enumerate(self._app_ports):
            hosts[f'{self.k8s_local_url}:{port}'] = port
        return hosts

    @classmethod
    async def delete(cls, conversation_id: str):
        """
        删除与对话相关的资源。
        
        该方法在 UI 中实际删除对话时触发。
        
        Args:
            conversation_id (str): 要删除资源的对话 ID
        """
        try:
            # 清理 Kubernetes 资源，包括 PVC
            cls._cleanup_k8s_resources(
                namespace=cls._namespace,
                remove_pvc=True,
                conversation_id=conversation_id,
            )

        except Exception as e:
            logger.error(
                f'Error deleting resources for conversation {conversation_id}: {e}'
            )