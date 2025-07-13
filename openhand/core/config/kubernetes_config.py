from pydantic import BaseModel, ConfigDict, Field, ValidationError


class KubernetesConfig(BaseModel):
    """Kubernetes runtime的配置类。

    这个类定义了在Kubernetes环境中运行OpenHands所需的各种配置参数，
    包括命名空间、存储、资源限制、网络等设置。

    Attributes:
        namespace: 用于OpenHands资源的Kubernetes命名空间
        ingress_domain: ingress资源的域名
        pvc_storage_size: 持久卷声明的大小（例如 "2Gi"）
        pvc_storage_class: 持久卷声明的存储类
        resource_cpu_request: runtime pods的CPU请求量
        resource_memory_request: runtime pods的内存请求量
        resource_memory_limit: runtime pods的内存限制
        image_pull_secret: 私有镜像仓库的可选镜像拉取密钥名称
        ingress_tls_secret: ingress的可选TLS密钥名称
        node_selector_key: pod调度的可选节点选择器键
        node_selector_val: pod调度的可选节点选择器值
        tolerations_yaml: 定义pod容忍度的可选YAML字符串
        privileged: 是否在特权模式下运行runtime sandbox容器，用于docker-in-docker
    """

    namespace: str = Field(
        default='default',
        description='用于OpenHands资源的Kubernetes命名空间',
    )
    ingress_domain: str = Field(
        default='localhost', description='ingress资源的域名'
    )
    pvc_storage_size: str = Field(
        default='2Gi', description='持久卷声明的大小'
    )
    pvc_storage_class: str | None = Field(
        default=None, description='持久卷声明的存储类'
    )
    resource_cpu_request: str = Field(
        default='1', description='runtime pods的CPU请求量'
    )
    resource_memory_request: str = Field(
        default='1Gi', description='runtime pods的内存请求量'
    )
    resource_memory_limit: str = Field(
        default='2Gi', description='runtime pods的内存限制'
    )
    image_pull_secret: str | None = Field(
        default=None,
        description='私有镜像仓库的可选镜像拉取密钥名称',
    )
    ingress_tls_secret: str | None = Field(
        default=None, description='ingress的可选TLS密钥名称'
    )
    node_selector_key: str | None = Field(
        default=None, description='pod调度的可选节点选择器键'
    )
    node_selector_val: str | None = Field(
        default=None, description='pod调度的可选节点选择器值'
    )
    tolerations_yaml: str | None = Field(
        default=None, description='定义pod容忍度的可选YAML字符串'
    )
    privileged: bool = Field(
        default=False,
        description='是否在特权模式下运行runtime sandbox容器，用于docker-in-docker',
    )

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')

    @classmethod
    def from_toml_section(cls, data: dict) -> dict[str, 'KubernetesConfig']:
        """从表示[kubernetes]部分的toml字典创建KubernetesConfig实例的映射。

        配置是从data中的所有键构建的。

        Args:
            data: 包含kubernetes配置数据的字典
            
        Returns:
            dict[str, KubernetesConfig]: 一个映射，其中键"kubernetes"对应[kubernetes]配置
            
        Raises:
            ValueError: 当kubernetes配置无效时抛出
        """
        # 初始化结果映射
        kubernetes_mapping: dict[str, KubernetesConfig] = {}

        # 尝试创建配置实例
        try:
            # 使用模型验证创建kubernetes配置实例
            kubernetes_mapping['kubernetes'] = cls.model_validate(data)
        except ValidationError as e:
            # 如果验证失败，抛出更友好的错误信息
            raise ValueError(f'Invalid kubernetes configuration: {e}')

        return kubernetes_mapping