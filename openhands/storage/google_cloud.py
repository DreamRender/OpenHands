import os

from google.api_core.exceptions import NotFound
from google.cloud import storage
from google.cloud.storage.blob import Blob
from google.cloud.storage.bucket import Bucket
from google.cloud.storage.client import Client

from openhands.storage.files import FileStore


class GoogleCloudFileStore(FileStore):
    """
    Google Cloud Storage文件存储实现类
    
    基于Google Cloud Storage服务的文件存储实现，继承自FileStore抽象基类。
    支持在Google Cloud Storage bucket中进行文件的读写、列表和删除操作。
    """
    
    def __init__(self, bucket_name: str | None = None) -> None:
        """
        创建一个新的GoogleCloudFileStore实例
        
        如果环境变量中定义了GOOGLE_APPLICATION_CREDENTIALS，将使用它进行身份验证。
        否则将使用匿名访问模式。
        
        Args:
            bucket_name (str | None): Google Cloud Storage的bucket名称。
                                    如果为None，则从环境变量GOOGLE_CLOUD_BUCKET_NAME中获取
        """
        # 如果没有提供bucket名称，从环境变量中获取
        if bucket_name is None:
            bucket_name = os.environ['GOOGLE_CLOUD_BUCKET_NAME']
        
        # 创建Google Cloud Storage客户端
        self.storage_client: Client = storage.Client()
        # 获取指定的bucket实例
        self.bucket: Bucket = self.storage_client.bucket(bucket_name)

    def write(self, path: str, contents: str | bytes) -> None:
        """
        将内容写入Google Cloud Storage中的指定路径
        
        Args:
            path (str): 文件在bucket中的路径
            contents (str | bytes): 要写入的内容，可以是字符串或字节数据
        """
        # 创建blob对象，代表bucket中的一个文件
        blob: Blob = self.bucket.blob(path)
        # 根据内容类型确定写入模式：bytes用二进制模式，str用文本模式
        mode = 'wb' if isinstance(contents, bytes) else 'w'
        # 使用blob的open方法写入内容
        with blob.open(mode) as f:
            f.write(contents)

    def read(self, path: str) -> str:
        """
        从Google Cloud Storage中读取指定路径的文件内容
        
        Args:
            path (str): 要读取的文件在bucket中的路径
            
        Returns:
            str: 文件的字符串内容
            
        Raises:
            FileNotFoundError: 当文件不存在时抛出此异常
        """
        # 创建blob对象
        blob: Blob = self.bucket.blob(path)
        try:
            # 以只读文本模式打开文件并读取内容
            with blob.open('r') as f:
                return str(f.read())
        except NotFound as err:
            # 捕获Google Cloud的NotFound异常并转换为标准的FileNotFoundError
            raise FileNotFoundError(err)

    def list(self, path: str) -> list[str]:
        """
        列出Google Cloud Storage中指定路径下的所有文件和目录
        
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
        
        blobs: set[str] = set()  # 使用set避免重复
        prefix_len = len(path)
        
        # 遍历所有匹配前缀的blob
        for blob in self.bucket.list_blobs(prefix=path):
            name: str = blob.name
            # 跳过与路径完全相同的项（即目录本身）
            if name == path:
                continue
            try:
                # 查找路径前缀之后的第一个'/'位置
                index = name.index('/', prefix_len + 1)
                if index != prefix_len:
                    # 如果找到了子目录，添加到结果集中（以'/'结尾表示目录）
                    blobs.add(name[: index + 1])
            except ValueError:
                # 如果没有找到'/'，说明这是一个文件，直接添加
                blobs.add(name)
        
        return list(blobs)

    def delete(self, path: str) -> None:
        """
        删除Google Cloud Storage中指定路径的文件或目录
        
        如果路径是目录，会递归删除其中的所有文件。
        如果路径是文件，直接删除该文件。
        
        Args:
            path (str): 要删除的文件或目录路径
        """
        # 路径清理
        if not path or path == '/':
            path = ''
        if path.endswith('/'):
            path = path[:-1]

        # 尝试删除所有子资源（假设路径是一个目录）
        for blob in self.bucket.list_blobs(prefix=f'{path}/'):
            blob.delete()

        # 然后尝试将项目作为文件删除
        try:
            file_blob: Blob = self.bucket.blob(path)
            file_blob.delete()
        except NotFound:
            # 如果文件不存在，忽略错误（可能已经是目录并且在上面被删除了）
            pass