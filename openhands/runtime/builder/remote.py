import base64
import io
import tarfile
import time

import httpx

from openhands.core.exceptions import AgentRuntimeBuildError
from openhands.core.logger import openhands_logger as logger
from openhands.runtime.builder import RuntimeBuilder
from openhands.runtime.utils.request import send_request
from openhands.utils.http_session import HttpSession
from openhands.utils.shutdown_listener import (
    should_continue,
    sleep_if_should_continue,
)


class RemoteRuntimeBuilder(RuntimeBuilder):
    """远程Runtime构建器实现类
    
    这个类通过与远程Runtime API交互来构建和管理容器镜像，继承自RuntimeBuilder抽象基类。
    
    主要功能包括：
    1. 将本地构建上下文打包并上传到远程服务
    2. 通过远程API发起镜像构建任务
    3. 轮询构建状态直到完成
    4. 检查远程镜像仓库中的镜像存在性
    5. 处理构建过程中的错误和重试逻辑
    
    适用于以下场景：
    - 本地环境无法直接进行Docker构建
    - 需要在远程强大的构建环境中进行构建
    - 集中化的镜像构建管理
    """

    def __init__(self, api_url: str, api_key: str, session: HttpSession | None = None):
        """初始化远程Runtime构建器
        
        Args:
            api_url (str): 远程Runtime API的基础URL地址
            api_key (str): 用于API认证的密钥
            session (HttpSession | None): HTTP会话对象，如果为None则创建新的会话
        """
        # 远程API的基础URL
        self.api_url = api_url
        # API认证密钥
        self.api_key = api_key
        # HTTP会话对象，用于与远程API通信
        self.session = session or HttpSession()
        # 在请求头中添加API密钥用于认证
        self.session.headers.update({'X-API-Key': self.api_key})

    def build(
        self,
        path: str,
        tags: list[str],
        platform: str | None = None,
        extra_build_args: list[str] | None = None,
    ) -> str:
        """使用Runtime API的/build端点构建Docker镜像
        
        这个方法实现了RuntimeBuilder抽象基类的build方法，通过远程API进行镜像构建。
        整个过程包括：
        1. 将构建上下文打包为tar.gz文件
        2. 将文件编码为base64格式上传
        3. 发起构建请求
        4. 轮询构建状态直到完成
        
        Args:
            path (str): 构建上下文的本地路径，包含Dockerfile和相关文件
            tags (list[str]): 应用到构建镜像的标签列表
            platform (str | None): 目标平台架构（当前未使用，保留用于未来扩展）
            extra_build_args (list[str] | None): 额外构建参数（当前未使用，保留用于未来扩展）
        
        Returns:
            str: 构建完成的镜像名称
        
        Raises:
            AgentRuntimeBuildError: 当构建失败、超时或发生其他错误时抛出
        """
        # 创建tar压缩包，包含整个构建上下文
        tar_buffer = io.BytesIO()  # 在内存中创建二进制缓冲区
        with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tar:
            # 将整个路径添加到tar包中，使用'.'作为根目录名
            tar.add(path, arcname='.')
        tar_buffer.seek(0)  # 重置缓冲区位置到开始

        # 将tar文件编码为base64字符串，用于HTTP传输
        base64_encoded_tar = base64.b64encode(tar_buffer.getvalue()).decode('utf-8')

        # 准备multipart表单数据，用于文件上传
        files = [
            ('context', ('context.tar.gz', base64_encoded_tar)),  # 构建上下文文件
            ('target_image', (None, tags[0])),  # 目标镜像名称（主要标签）
        ]

        # 添加额外的标签（如果存在）
        for tag in tags[1:]:
            files.append(('tags', (None, tag)))

        # 发送POST请求到/build端点（开始构建过程）
        try:
            response = send_request(
                self.session,
                'POST',
                f'{self.api_url}/build',
                files=files,
                timeout=30,  # 30秒超时
            )
        except httpx.HTTPError as e:
            # 处理HTTP错误
            if e.response.status_code == 429:
                # 429表示请求过于频繁，等待后重试
                logger.warning('Build was rate limited. Retrying in 30 seconds.')
                time.sleep(30)
                return self.build(path, tags, platform)
            else:
                raise e

        # 解析构建响应，获取构建ID
        build_data = response.json()
        build_id = build_data['build_id']
        logger.info(f'Build initiated with ID: {build_id}')

        # 轮询/build_status端点直到构建完成
        start_time = time.time()  # 记录开始时间
        timeout = 30 * 60  # 30分钟超时（以秒为单位）
        
        while should_continue():  # 检查是否应该继续（用于优雅关闭）
            # 检查是否超时
            if time.time() - start_time > timeout:
                logger.error('Build timed out after 30 minutes')
                raise AgentRuntimeBuildError('Build timed out after 30 minutes')

            # 查询构建状态
            status_response = send_request(
                self.session,
                'GET',
                f'{self.api_url}/build_status',
                params={'build_id': build_id},
            )

            # 检查状态查询请求是否成功
            if status_response.status_code != 200:
                logger.error(f'Failed to get build status: {status_response.text}')
                raise AgentRuntimeBuildError(
                    f'Failed to get build status: {status_response.text}'
                )

            # 解析状态响应
            status_data = status_response.json()
            status = status_data['status']
            logger.info(f'Build status: {status}')

            # 检查构建是否成功完成
            if status == 'SUCCESS':
                logger.debug(f'Successfully built {status_data["image"]}')
                return str(status_data['image'])
            elif status in [
                'FAILURE',        # 构建失败
                'INTERNAL_ERROR', # 内部错误
                'TIMEOUT',        # 超时
                'CANCELLED',      # 被取消
                'EXPIRED',        # 过期
            ]:
                # 构建失败的各种状态
                error_message = status_data.get(
                    'error', f'Build failed with status: {status}. Build ID: {build_id}'
                )
                logger.error(error_message)
                raise AgentRuntimeBuildError(error_message)

            # 等待30秒后再次查询状态
            sleep_if_should_continue(30)

        # 如果循环被中断（should_continue返回False），抛出异常
        raise AgentRuntimeBuildError('Build interrupted')

    def image_exists(self, image_name: str, pull_from_repo: bool = True) -> bool:
        """使用/image_exists端点检查镜像是否存在于远程注册表中
        
        这个方法实现了RuntimeBuilder抽象基类的image_exists方法，
        通过远程API查询指定镜像是否存在于远程镜像仓库中。
        
        Args:
            image_name (str): 要检查的镜像名称，格式为"仓库:标签"
            pull_from_repo (bool): 此参数在远程构建器中未使用，保留用于接口兼容性
        
        Returns:
            bool: 镜像是否存在于远程注册表中
        
        Raises:
            AgentRuntimeBuildError: 当API请求失败时抛出
        """
        # 准备查询参数
        params = {'image': image_name}
        
        # 发送GET请求到/image_exists端点
        response = send_request(
            self.session,
            'GET',
            f'{self.api_url}/image_exists',
            params=params,
        )

        # 检查请求是否成功
        if response.status_code != 200:
            logger.error(f'Failed to check image existence: {response.text}')
            raise AgentRuntimeBuildError(
                f'Failed to check image existence: {response.text}'
            )

        # 解析响应数据
        result = response.json()

        # 根据镜像是否存在记录不同的日志信息
        if result['exists']:
            logger.debug(
                f'Image {image_name} exists. '
                f'Uploaded at: {result["image"]["upload_time"]}, '
                f'Size: {result["image"]["image_size_bytes"] / 1024 / 1024:.2f} MB'
            )
        else:
            logger.debug(f'Image {image_name} does not exist.')

        # 返回布尔值表示镜像是否存在
        return bool(result['exists'])