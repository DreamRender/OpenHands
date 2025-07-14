import threading
from typing import Callable

import docker


class LogStreamer:
    """
    将Docker容器日志流式传输到stdout的类。

    这个类提供了一种通过提供的日志函数将Docker容器的日志直接流式传输到stdout的方法。
    使用独立线程来实现异步日志流式传输，避免阻塞主线程。
    """

    def __init__(
        self,
        container: docker.models.containers.Container,
        logFn: Callable[[str, str], None],
    ):
        """
        初始化LogStreamer。
        
        Args:
            container (docker.models.containers.Container): Docker容器对象
            logFn (Callable[[str, str], None]): 日志函数，接受日志级别和消息两个参数
        """
        self.log = logFn
        # 在此实例上启动线程之前初始化所有属性
        self.stdout_thread = None  # 日志流线程对象
        self.log_generator = None  # 日志生成器
        self._stop_event = threading.Event()  # 停止事件，用于线程间通信

        try:
            # 创建日志生成器，启用流式传输和跟随模式
            self.log_generator = container.logs(stream=True, follow=True)
            # 启动stdout流式传输线程
            self.stdout_thread = threading.Thread(target=self._stream_logs)
            self.stdout_thread.daemon = True  # 设置为守护线程
            self.stdout_thread.start()
        except Exception as e:
            self.log('error', f'Failed to initialize log streaming: {e}')
            # 翻译：初始化日志流式传输失败：{e}

    def _stream_logs(self) -> None:
        """
        从Docker容器流式传输日志到stdout的内部方法。
        
        这个方法在独立线程中运行，持续读取容器日志并通过日志函数输出。
        """
        if not self.log_generator:
            self.log('error', 'Log generator not initialized')
            # 翻译：日志生成器未初始化
            return

        try:
            # 遍历日志生成器的每一行
            for log_line in self.log_generator:
                # 检查是否收到停止信号
                if self._stop_event.is_set():
                    break
                if log_line:
                    # 解码日志行并去除尾部空白
                    decoded_line = log_line.decode('utf-8').rstrip()
                    self.log('debug', f'[inside container] {decoded_line}')
                    # 翻译：[容器内部] {decoded_line}
        except Exception as e:
            self.log('error', f'Error streaming docker logs to stdout: {e}')
            # 翻译：将docker日志流式传输到stdout时出错：{e}

    def __del__(self) -> None:
        """
        对象销毁时的清理方法。
        
        确保在对象被垃圾回收时正确关闭日志流。
        """
        if (
            hasattr(self, 'stdout_thread')
            and self.stdout_thread
            and self.stdout_thread.is_alive()
        ):
            self.close(timeout=5)

    def close(self, timeout: float = 5.0) -> None:
        """
        清理关闭日志流式传输。
        
        Args:
            timeout (float): 等待线程关闭的超时时间（秒），默认为5.0秒
        """
        # 设置停止事件，通知线程停止
        self._stop_event.set()
        
        # 等待线程结束
        if self.stdout_thread and self.stdout_thread.is_alive():
            self.stdout_thread.join(timeout)
            
        # 关闭日志生成器以释放文件描述符
        if self.log_generator is not None:
            self.log_generator.close()
