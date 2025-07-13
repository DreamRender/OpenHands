import os
from typing import Any, TypedDict

import boto3
import botocore

from openhands.storage.files import FileStore


class S3ObjectDict(TypedDict):
    """
    S3对象字典类型定义
    
    定义了S3对象在API响应中的结构。
    """
    Key: str  # S3对象的键（路径）


class GetObjectOutputDict(TypedDict):
    """
    获取S3对象输出字典类型定义
    
    定义了S3 get_object API响应的结构。
    """
    Body: Any  # 对象的内容流


class ListObjectsV2OutputDict(TypedDict):
    """
    列出S3对象V2 API输出字典类型定义
    
    定义了S3 list_objects_v2 API响应的结构。
    """
    Contents: list[S3ObjectDict] | None  # 对象列表，可能为None


class S3FileStore(FileStore):
    """
    AWS S3文件存储实现类
    
    基于Amazon S3服务的文件存储实现，继承自FileStore抽象基类。
    支持在S3 bucket中进行文件的读写、列表和删除操作。
    """
    
    def __init__(self, bucket_name: str | None) -> None:
        """
        初始化S3文件存储
        
        从环境变量中读取AWS认证信息和配置参数。
        
        Args:
            bucket_name (str | None): S3 bucket名称。如果为None，从环境变量AWS_S3_BUCKET中获取
        """
        # 从环境变量获取AWS访问密钥
        access_key = os.getenv('AWS_ACCESS_KEY_ID')
        secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')
        
        # 从环境变量获取SSL配置，默认为true（安全连接）
        secure = os.getenv('AWS_S3_SECURE', 'true').lower() == 'true'
        
        # 获取S3服务端点URL并确保正确的URL协议
        endpoint = self._ensure_url_scheme(secure, os.getenv('AWS_S3_ENDPOINT'))
        
        # 如果没有提供bucket名称，从环境变量中获取
        if bucket_name is None:
            bucket_name = os.environ['AWS_S3_BUCKET']
        
        self.bucket: str = bucket_name
        
        # 创建boto3 S3客户端
        self.client: Any = boto3.client(
            's3',
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            endpoint_url=endpoint,
            use_ssl=secure,
        )

    def write(self, path: str, contents: str | bytes) -> None:
        """
        将内容写入S3 bucket中的指定路径
        
        Args:
            path (str): 文件在bucket中的键（路径）
            contents (str | bytes): 要写入的内容
            
        Raises:
            FileNotFoundError: 当访问被拒绝、bucket不存在或其他写入错误时抛出
        """
        try:
            # 将字符串内容转换为字节数组
            as_bytes = (
                contents.encode('utf-8') if isinstance(contents, str) else contents
            )
            # 使用S3客户端上传对象
            self.client.put_object(Bucket=self.bucket, Key=path, Body=as_bytes)
        except botocore.exceptions.ClientError as e:
            # 处理各种S3客户端错误
            if e.response['Error']['Code'] == 'AccessDenied':
                raise FileNotFoundError(
                    f"Error: Access denied to bucket '{self.bucket}'."
                )
            elif e.response['Error']['Code'] == 'NoSuchBucket':
                raise FileNotFoundError(
                    f"Error: The bucket '{self.bucket}' does not exist."
                )
            raise FileNotFoundError(
                f"Error: Failed to write to bucket '{self.bucket}' at path {path}: {e}"
            )

    def read(self, path: str) -> str:
        """
        从S3 bucket中读取指定路径的文件内容
        
        Args:
            path (str): 要读取的文件在bucket中的键（路径）
            
        Returns:
            str: 文件的字符串内容
            
        Raises:
            FileNotFoundError: 当文件不存在、bucket不存在或读取失败时抛出
        """
        try:
            # 从S3获取对象
            response: GetObjectOutputDict = self.client.get_object(
                Bucket=self.bucket, Key=path
            )
            # 读取响应体中的内容并解码为UTF-8字符串
            with response['Body'] as stream:
                return str(stream.read().decode('utf-8'))
        except botocore.exceptions.ClientError as e:
            # 捕获所有S3相关错误并转换为FileNotFoundError
            if e.response['Error']['Code'] == 'NoSuchBucket':
                raise FileNotFoundError(
                    f"Error: The bucket '{self.bucket}' does not exist."
                )
            elif e.response['Error']['Code'] == 'NoSuchKey':
                raise FileNotFoundError(
                    f"Error: The object key '{path}' does not exist in bucket '{self.bucket}'."
                )
            else:
                raise FileNotFoundError(
                    f"Error: Failed to read from bucket '{self.bucket}' at path {path}: {e}"
                )
        except Exception as e:
            # 捕获其他所有异常
            raise FileNotFoundError(
                f"Error: Failed to read from bucket '{self.bucket}' at path {path}: {e}"
            )

    def list(self, path: str) -> list[str]:
        """
        列出S3 bucket中指定路径下的所有文件和目录
        
        Args:
            path (str): 要列出内容的目录路径
            
        Returns:
            list[str]: 包含该路径下所有文件和目录路径的列表
        """
        # 路径规范化处理
        if not path or path == '/':
            path = ''
        elif not path.endswith('/'):
            path += '/'
        
        # 注释说明：分隔符逻辑会过滤掉目录，所以我们不能使用它
        # 例如，给定以下结构：
        #   foo/bar/zap.txt
        #   foo/bar/bang.txt
        #   ping.txt
        # prefix=None, delimiter="/"   产生  ["ping.txt"]  # 不理想
        # prefix="foo", delimiter="/"  产生  []  # 不理想
        
        results: set[str] = set()  # 使用set避免重复
        prefix_len = len(path)
        
        # 列出所有匹配前缀的对象
        response: ListObjectsV2OutputDict = self.client.list_objects_v2(
            Bucket=self.bucket, Prefix=path
        )
        contents = response.get('Contents')
        
        # 如果没有内容，返回空列表
        if not contents:
            return []
        
        # 提取所有对象的键
        paths = [obj['Key'] for obj in contents]
        
        # 处理每个路径，识别文件和目录
        for sub_path in paths:
            # 跳过与路径完全相同的项
            if sub_path == path:
                continue
            try:
                # 查找路径前缀之后的第一个'/'位置
                index = sub_path.index('/', prefix_len + 1)
                if index != prefix_len:
                    # 如果找到了子目录，添加到结果集中
                    results.add(sub_path[: index + 1])
            except ValueError:
                # 如果没有找到'/'，说明这是一个文件，直接添加
                results.add(sub_path)
        
        return list(results)

    def delete(self, path: str) -> None:
        """
        删除S3 bucket中指定路径的文件或目录
        
        如果路径是目录，会递归删除其中的所有文件。
        如果路径是文件，直接删除该文件。
        
        Args:
            path (str): 要删除的文件或目录路径
            
        Raises:
            FileNotFoundError: 当bucket不存在、访问被拒绝或删除失败时抛出
        """
        try:
            # 路径清理
            if not path or path == '/':
                path = ''
            if path.endswith('/'):
                path = path[:-1]

            # 尝试删除所有子资源（假设路径是一个目录）
            response = self.client.list_objects_v2(
                Bucket=self.bucket, Prefix=f'{path}/'
            )
            # 删除所有找到的子对象
            for content in response.get('Contents') or []:
                self.client.delete_object(Bucket=self.bucket, Key=content['Key'])

            # 然后尝试将项目作为文件删除
            self.client.delete_object(Bucket=self.bucket, Key=path)

        except botocore.exceptions.ClientError as e:
            # 处理各种S3客户端错误
            if e.response['Error']['Code'] == 'NoSuchBucket':
                raise FileNotFoundError(
                    f"Error: The bucket '{self.bucket}' does not exist."
                )
            elif e.response['Error']['Code'] == 'AccessDenied':
                raise FileNotFoundError(
                    f"Error: Access denied to bucket '{self.bucket}'."
                )
            elif e.response['Error']['Code'] == 'NoSuchKey':
                raise FileNotFoundError(
                    f"Error: The object key '{path}' does not exist in bucket '{self.bucket}'."
                )
            else:
                raise FileNotFoundError(
                    f"Error: Failed to delete key '{path}' from bucket '{self.bucket}': {e}"
                )
        except Exception as e:
            # 捕获其他所有异常
            raise FileNotFoundError(
                f"Error: Failed to delete key '{path}' from bucket '{self.bucket}: {e}"
            )

    def _ensure_url_scheme(self, secure: bool, url: str | None) -> str | None:
        """
        确保URL具有正确的协议方案（http或https）
        
        根据secure参数和现有URL确定最终的URL格式。
        
        Args:
            secure (bool): 是否使用安全连接（HTTPS）
            url (str | None): 原始URL，可能为None
            
        Returns:
            str | None: 处理后的URL，如果输入为None则返回None
        """
        # 如果URL为空，直接返回None
        if not url:
            return None
        
        if secure:
            # 安全模式：确保使用https协议
            if not url.startswith('https://'):
                # 移除可能存在的http://前缀，然后添加https://
                url = 'https://' + url.removeprefix('http://')
        else:
            # 非安全模式：确保使用http协议
            if not url.startswith('http://'):
                # 移除可能存在的https://前缀，然后添加http://
                url = 'http://' + url.removeprefix('https://')
        
        return url