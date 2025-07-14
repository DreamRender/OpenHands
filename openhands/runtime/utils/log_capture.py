import io
import logging
from contextlib import asynccontextmanager


@asynccontextmanager
async def capture_logs(logger_name, level=logging.ERROR):
    """
    异步上下文管理器，用于捕获特定logger的日志输出。
    
    这个函数创建一个临时的日志捕获环境，将指定logger的输出重定向到StringIO对象中，
    以便在测试或调试期间捕获日志消息。
    
    Args:
        logger_name (str): 要捕获日志的logger名称
        level (int): 日志级别，默认为logging.ERROR
        
    Yields:
        io.StringIO: 捕获日志内容的StringIO对象
        
    Example:
        async with capture_logs('my_logger') as log_capture:
            # 在这里执行会产生日志的代码
            logger.error("This will be captured")
            captured_content = log_capture.getvalue()
    """
    # 获取指定名称的logger
    logger = logging.getLogger(logger_name)

    # 存储原始的处理器和日志级别
    original_handlers = logger.handlers[:]
    original_level = logger.level

    # 设置日志捕获
    log_capture = io.StringIO()  # 创建内存中的字符串流
    handler = logging.StreamHandler(log_capture)  # 创建流处理器
    handler.setLevel(level)  # 设置处理器的日志级别

    # 替换logger的处理器和级别
    logger.handlers = [handler]
    logger.setLevel(level)

    try:
        # 返回日志捕获对象供使用
        yield log_capture
    finally:
        # 在退出时恢复原始配置
        logger.handlers = original_handlers
        logger.setLevel(original_level)
