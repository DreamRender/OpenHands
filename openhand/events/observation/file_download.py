from dataclasses import dataclass

from openhands.core.schema import ObservationType
from openhands.events.observation.observation import Observation


@dataclass
class FileDownloadObservation(Observation):
    """文件下载观察类
    
    这个数据类表示文件下载操作的结果。
    当Agent执行文件下载Action后，会生成此观察来记录下载的文件信息。
    
    Attributes:
        file_path (str): 下载文件的本地存储路径
        observation (str): 观察类型，固定为DOWNLOAD
    """
    
    file_path: str  # 文件下载到本地的完整路径
    observation: str = ObservationType.DOWNLOAD  # 观察类型标识

    @property
    def message(self) -> str:
        """返回消息内容
        
        Returns:
            str: 包含下载文件位置的格式化消息
        """
        return f'Downloaded the file at location: {self.file_path}'

    def __str__(self) -> str:
        """返回字符串表示
        
        Returns:
            str: 包含下载文件位置信息的格式化字符串
        """
        ret = (
            '**FileDownloadObservation**\n'
            f'Location of downloaded file: {self.file_path}\n'
        )
        return ret