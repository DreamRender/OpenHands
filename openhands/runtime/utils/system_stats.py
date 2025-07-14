"""获取系统资源统计信息的工具模块。

此模块提供了获取当前系统资源统计信息的功能，包括CPU使用率、内存使用情况、
磁盘使用情况和I/O统计信息。主要用于监控系统性能和资源消耗。
"""

import time

import psutil


def get_system_stats() -> dict[str, object]:
    """获取当前系统资源统计信息。

    该函数收集当前进程的系统资源使用情况，包括CPU使用率、内存使用情况、
    磁盘使用情况和I/O统计信息。通过psutil库获取系统信息，并直接读取
    /proc/[pid]/io文件来获取I/O统计信息以避免psutil的字段名假设。

    Returns:
        dict: 包含以下键值的字典：
            - cpu_percent: 当前进程的CPU使用率百分比
            - memory: 内存使用统计信息 (rss, vms, percent)
            - disk: 磁盘使用统计信息 (total, used, free, percent)
            - io: I/O统计信息 (read_bytes, write_bytes)
    """
    # 获取当前进程对象
    process = psutil.Process()
    
    # 获取初始CPU百分比（这将返回0.0）
    # 第一次调用cpu_percent()总是返回0.0，因为没有之前的数据用于比较
    process.cpu_percent()
    
    # 等待一段时间并获取实际的CPU百分比
    # 需要等待一小段时间才能获得准确的CPU使用率
    time.sleep(0.1)

    # 使用oneshot()上下文管理器来批量获取进程信息，提高效率
    with process.oneshot():
        # 获取CPU使用率百分比
        cpu_percent = process.cpu_percent()
        # 获取内存使用信息
        memory_info = process.memory_info()
        # 获取内存使用率百分比
        memory_percent = process.memory_percent()

    # 获取根目录的磁盘使用情况
    disk_usage = psutil.disk_usage('/')

    # 直接从/proc/[pid]/io获取I/O统计信息，避免psutil的字段名假设
    # 这样可以确保获取到准确的I/O数据，不受psutil版本差异影响
    try:
        # 以二进制模式打开进程的I/O统计文件
        with open(f'/proc/{process.pid}/io', 'rb') as f:
            io_stats = {}
            # 逐行读取I/O统计信息
            for line in f:
                if line:
                    try:
                        # 解析每行的键值对，格式为 "key: value"
                        name, value = line.strip().split(b': ')
                        # 将键从字节转换为ASCII字符串，值转换为整数
                        io_stats[name.decode('ascii')] = int(value)
                    except (ValueError, UnicodeDecodeError):
                        # 如果解析失败，跳过这一行
                        continue
    except (FileNotFoundError, PermissionError):
        # 如果无法读取/proc/[pid]/io文件，使用默认值
        # 这可能发生在非Linux系统或权限不足的情况下
        io_stats = {'read_bytes': 0, 'write_bytes': 0}

    # 构建并返回系统统计信息字典
    return {
        'cpu_percent': cpu_percent,  # CPU使用率百分比
        'memory': {
            'rss': memory_info.rss,      # 驻留集大小（物理内存使用量）
            'vms': memory_info.vms,      # 虚拟内存大小
            'percent': memory_percent,   # 内存使用率百分比
        },
        'disk': {
            'total': disk_usage.total,   # 磁盘总容量
            'used': disk_usage.used,     # 已使用磁盘空间
            'free': disk_usage.free,     # 可用磁盘空间
            'percent': disk_usage.percent, # 磁盘使用率百分比
        },
        'io': {
            'read_bytes': io_stats.get('read_bytes', 0),   # 读取字节数
            'write_bytes': io_stats.get('write_bytes', 0), # 写入字节数
        },
    }
