from abc import abstractmethod


class FileStore:
    """
    文件存储抽象基类
    
    定义了文件存储操作的标准接口，包括读取、写入、列出和删除文件的方法。
    所有具体的文件存储实现（如本地存储、云存储等）都必须继承这个类并实现其抽象方法。
    """
    
    @abstractmethod
    def write(self, path: str, contents: str | bytes) -> None:
        """
        写入文件内容到指定路径
        
        Args:
            path (str): 要写入的文件路径
            contents (str | bytes): 要写入的文件内容，可以是字符串或字节数据
            
        Returns:
            None
        """
        pass

    @abstractmethod
    def read(self, path: str) -> str:
        """
        从指定路径读取文件内容
        
        Args:
            path (str): 要读取的文件路径
            
        Returns:
            str: 文件的字符串内容
            
        Raises:
            FileNotFoundError: 当文件不存在时抛出此异常
        """
        pass

    @abstractmethod
    def list(self, path: str) -> list[str]:
        """
        列出指定路径下的所有文件和目录
        
        Args:
            path (str): 要列出内容的目录路径
            
        Returns:
            list[str]: 包含该路径下所有文件和目录名称的列表
        """
        pass

    @abstractmethod
    def delete(self, path: str) -> None:
        """
        删除指定路径的文件或目录
        
        Args:
            path (str): 要删除的文件或目录路径
            
        Returns:
            None
        """
        pass