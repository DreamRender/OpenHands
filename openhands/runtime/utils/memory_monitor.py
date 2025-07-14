"""
运行时的内存监控实用工具。

该模块提供了内存监控功能，用于跟踪和记录运行时的内存使用情况。
"""

import threading

from memory_profiler import memory_usage

from openhands.core.logger import openhands_logger as logger


class LogStream:
    """
    类似流的对象，将写入操作重定向到日志记录器。
    
    这个类实现了一个简单的流接口，用于将内存监控的输出重定向到日志系统。
    """

    def write(self, message: str) -> None:
        """
        将消息写入到日志记录器。
        
        Args:
            message (str): 要写入的消息内容
        """
        # 只有当消息不为空且不是空白字符时才记录日志
        if message and not message.isspace():
            logger.info(f'[Memory usage] {message.strip()}')

    def flush(self) -> None:
        """
        刷新流缓冲区。
        
        这里是空实现，因为日志记录器会自动处理缓冲区刷新。
        """
        pass


class MemoryMonitor:
    """
    运行时的内存监控器。
    
    该类提供了启动和停止内存监控的功能，可以在后台线程中持续监控内存使用情况。
    """

    def __init__(self, enable: bool = False):
        """
        初始化内存监控器。
        
        Args:
            enable (bool): 是否启用内存监控功能，默认为False
        """
        self._monitoring_thread: threading.Thread | None = None
        # 用于停止监控的事件对象
        self._stop_monitoring = threading.Event()
        # 日志流对象，用于重定向内存监控输出
        self.log_stream = LogStream()
        # 是否启用监控的标志
        self.enable = enable

    def start_monitoring(self) -> None:
        """
        开始监控内存使用情况。
        
        如果启用了监控功能，将在后台线程中启动内存监控进程。
        """
        # 如果未启用监控，直接返回
        if not self.enable:
            return

        # 如果监控线程已经存在，不重复启动
        if self._monitoring_thread is not None:
            return

        def monitor_process() -> None:
            """
            监控进程的内部函数。
            
            该函数在后台线程中运行，使用memory_usage函数持续监控内存使用情况。
            """
            try:
                # 使用memory_usage的内置监控循环
                mem_usage = memory_usage(
                    -1,  # 监控当前进程
                    interval=0.1,  # 每0.1秒检查一次
                    timeout=3600,  # 运行1小时（实际上会无限期运行）
                    max_usage=False,  # 获取连续读数而非最大值
                    include_children=True,  # 包括子进程
                    multiprocess=True,  # 监控所有进程
                    stream=self.log_stream,  # 将输出重定向到日志记录器
                    backend='psutil_pss',  # 使用psutil_pss后端
                )
                logger.info(f'Memory usage across time: {mem_usage}')
                # 中文说明：随时间变化的内存使用情况
            except Exception as e:
                logger.error(f'Memory monitoring failed: {e}')
                # 中文说明：内存监控失败

        # 创建守护线程来执行监控任务
        self._monitoring_thread = threading.Thread(target=monitor_process, daemon=True)
        self._monitoring_thread.start()
        logger.info('Memory monitoring started')
        # 中文说明：内存监控已启动

    def stop_monitoring(self) -> None:
        """
        停止监控内存使用情况。
        
        如果启用了监控功能，将停止后台监控线程。
        """
        # 如果未启用监控，直接返回
        if not self.enable:
            return

        # 如果监控线程存在，则停止监控
        if self._monitoring_thread is not None:
            # 设置停止监控的事件
            self._stop_monitoring.set()
            # 清除监控线程引用
            self._monitoring_thread = None
            logger.info('Memory monitoring stopped')
            # 中文说明：内存监控已停止
