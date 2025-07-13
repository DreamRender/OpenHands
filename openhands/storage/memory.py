import os

from openhands.core.logger import openhands_logger as logger
from openhands.storage.files import FileStore


class InMemoryFileStore(FileStore):
    """
    内存文件存储实现类
    
    基于Python字典的内存文件存储实现，继承自FileStore抽象基类。
    所有文件数据都存储在内存中，进程结束后数据会丢失。
    适用于测试、临时存储或不需要持久化的场景。
    """
    
    files: dict[str, str]  # 存储文件内容的字典，键为文件路径，值为文件内容

    def __init__(self, files: dict[str, str] | None = None) -> None:
        """
        初始化内存文件存储
        
        Args:
            files (dict[str, str] | None): 可选的初始文件数据字典
                                         键为文件路径，值为文件内容
        """
        # 初始化空的文件存储字典
        self.files = {}
        # 如果提供了初始文件数据，则使用它
        if files is not None:
            self.files = files

    def write(self, path: str, contents: str | bytes) -> None:
        """
        将内容写入内存中的指定路径
        
        Args:
            path (str): 文件路径
            contents (str | bytes): 要写入的内容，如果是bytes会自动转换为UTF-8字符串
        """
        # 如果内容是字节类型，转换为UTF-8字符串
        if isinstance(contents, bytes):
            contents = contents.decode('utf-8')
        # 将内容存储到内存字典中
        self.files[path] = contents

    def read(self, path: str) -> str:
        """
        从内存中读取指定路径的文件内容
        
        Args:
            path (str): 要读取的文件路径
            
        Returns:
            str: 文件的字符串内容
            
        Raises:
            FileNotFoundError: 当文件不存在时抛出此异常
        """
        # 检查文件是否存在于内存中
        if path not in self.files:
            raise FileNotFoundError(path)
        # 返回文件内容
        return self.files[path]

    def list(self, path: str) -> list[str]:
        """
        列出内存中指定路径下的所有文件和目录
        
        模拟文件系统的目录结构，通过分析文件路径来确定目录内容。
        
        Args:
            path (str): 要列出内容的目录路径
            
        Returns:
            list[str]: 包含该路径下所有文件和目录路径的列表，目录以'/'结尾
        """
        files = []
        
        # 遍历内存中的所有文件路径
        for file in self.files:
            # 只处理以指定路径开头的文件
            if not file.startswith(path):
                continue
            
            # 移除路径前缀，获取相对路径部分
            suffix = file.removeprefix(path)
            # 按'/'分割路径
            parts = suffix.split('/')
            
            # 如果第一个部分是空字符串，移除它（处理路径末尾有'/'的情况）
            if parts[0] == '':
                parts.pop(0)
            
            # 根据路径部分数量判断是文件还是目录
            if len(parts) == 1:
                # 只有一个部分，说明是直接子文件
                files.append(file)
            else:
                # 有多个部分，说明存在子目录
                dir_path = os.path.join(path, parts[0])
                # 确保目录路径以'/'结尾
                if not dir_path.endswith('/'):
                    dir_path += '/'
                # 避免重复添加同一个目录
                if dir_path not in files:
                    files.append(dir_path)
        
        return files

    def delete(self, path: str) -> None:
        """
        删除内存中指定路径的文件或目录
        
        如果路径是目录（其他文件的前缀），会删除该目录下的所有文件。
        
        Args:
            path (str): 要删除的文件或目录路径
        """
        try:
            # 找到所有以指定路径开头的文件（包括文件本身和子目录中的文件）
            keys_to_delete = [key for key in self.files.keys() if key.startswith(path)]
            # 删除所有匹配的文件
            for key in keys_to_delete:
                del self.files[key]
            logger.debug(f'Cleared in-memory file store: {path}')
        except Exception as e:
            # 记录删除过程中的任何错误
            logger.error(f'Error clearing in-memory file store: {str(e)}')