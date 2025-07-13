import os
import shutil

from openhands.core.logger import openhands_logger as logger
from openhands.storage.files import FileStore


class LocalFileStore(FileStore):
    """
    本地文件系统存储实现类
    
    基于本地文件系统的文件存储实现，继承自FileStore抽象基类。
    支持在本地文件系统中进行文件的读写、列表和删除操作。
    """
    
    root: str  # 根目录路径，所有文件操作都基于此目录

    def __init__(self, root: str):
        """
        初始化本地文件存储
        
        Args:
            root (str): 存储的根目录路径，支持波浪号(~)表示用户主目录
        """
        # 如果路径以~开头，展开为用户主目录的完整路径
        if root.startswith('~'):
            root = os.path.expanduser(root)
        self.root = root
        # 创建根目录（如果不存在），exist_ok=True表示目录已存在时不报错
        os.makedirs(self.root, exist_ok=True)

    def get_full_path(self, path: str) -> str:
        """
        将相对路径转换为基于根目录的完整路径
        
        Args:
            path (str): 相对路径
            
        Returns:
            str: 完整的本地文件系统路径
        """
        # 如果路径以'/'开头，移除开头的'/'（避免被当作绝对路径）
        if path.startswith('/'):
            path = path[1:]
        # 将相对路径与根目录结合得到完整路径
        return os.path.join(self.root, path)

    def write(self, path: str, contents: str | bytes) -> None:
        """
        将内容写入本地文件系统的指定路径
        
        Args:
            path (str): 要写入的文件相对路径
            contents (str | bytes): 要写入的内容，可以是字符串或字节数据
        """
        # 获取完整的本地路径
        full_path = self.get_full_path(path)
        # 确保目标目录存在，自动创建所需的父目录
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        # 根据内容类型确定写入模式：str用文本模式，bytes用二进制模式
        mode = 'w' if isinstance(contents, str) else 'wb'
        # 写入文件内容
        with open(full_path, mode) as f:
            f.write(contents)

    def read(self, path: str) -> str:
        """
        从本地文件系统读取指定路径的文件内容
        
        Args:
            path (str): 要读取的文件相对路径
            
        Returns:
            str: 文件的字符串内容
            
        Raises:
            FileNotFoundError: 当文件不存在时抛出此异常
        """
        # 获取完整的本地路径
        full_path = self.get_full_path(path)
        # 以文本模式读取文件内容
        with open(full_path, 'r') as f:
            return f.read()

    def list(self, path: str) -> list[str]:
        """
        列出本地文件系统中指定路径下的所有文件和目录
        
        Args:
            path (str): 要列出内容的目录相对路径
            
        Returns:
            list[str]: 包含该路径下所有文件和目录路径的列表，目录以'/'结尾
        """
        # 获取完整的本地路径
        full_path = self.get_full_path(path)
        # 列出目录中的所有文件和文件夹，并构建相对路径
        files = [os.path.join(path, f) for f in os.listdir(full_path)]
        # 为目录路径添加'/'后缀以区分文件和目录
        files = [f + '/' if os.path.isdir(self.get_full_path(f)) else f for f in files]
        return files

    def delete(self, path: str) -> None:
        """
        删除本地文件系统中指定路径的文件或目录
        
        如果是文件则直接删除，如果是目录则递归删除整个目录树。
        
        Args:
            path (str): 要删除的文件或目录相对路径
        """
        try:
            # 获取完整的本地路径
            full_path = self.get_full_path(path)
            # 检查路径是否存在
            if not os.path.exists(full_path):
                logger.debug(f'Local path does not exist: {full_path}')
                return
            
            # 根据路径类型选择删除方法
            if os.path.isfile(full_path):
                # 删除文件
                os.remove(full_path)
                logger.debug(f'Removed local file: {full_path}')
            elif os.path.isdir(full_path):
                # 递归删除目录
                shutil.rmtree(full_path)
                logger.debug(f'Removed local directory: {full_path}')
        except Exception as e:
            # 记录删除过程中的任何错误
            logger.error(f'Error clearing local file store: {str(e)}')