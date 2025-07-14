"""
系统工具模块。

该模块提供了系统相关的实用工具函数，包括端口可用性检查、
端口查找和数字矩阵显示等功能。
"""

import random
import socket
import time


def check_port_available(port: int) -> bool:
    """
    检查指定端口是否可用。
    
    Args:
        port (int): 要检查的端口号
    
    Returns:
        bool: 如果端口可用返回True，否则返回False
    """
    # 创建TCP socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # 尝试绑定到指定端口
        sock.bind(('0.0.0.0', port))
        return True
    except OSError:
        # 如果绑定失败，端口不可用
        time.sleep(0.1)  # 短暂延迟以进一步减少冲突的可能性
        return False
    finally:
        # 确保关闭socket
        sock.close()


def find_available_tcp_port(
    min_port: int = 30000, max_port: int = 39999, max_attempts: int = 10
) -> int:
    """
    在指定范围内查找可用的TCP端口。

    Args:
        min_port (int): 端口范围的下界（默认：30000）
        max_port (int): 端口范围的上界（默认：39999）
        max_attempts (int): 查找可用端口的最大尝试次数（默认：10）

    Returns:
        int: 可用的端口号，如果在max_attempts次尝试后没有找到则返回-1
    """
    # 使用系统随机数生成器
    rng = random.SystemRandom()
    # 生成指定范围内的端口列表
    ports = list(range(min_port, max_port + 1))
    # 随机打乱端口顺序
    rng.shuffle(ports)

    # 在打乱后的端口列表中查找可用端口
    for port in ports[:max_attempts]:
        if check_port_available(port):
            return port
    
    # 如果没有找到可用端口，返回-1
    return -1


def display_number_matrix(number: int) -> str | None:
    """
    将数字显示为矩阵形式。
    
    Args:
        number (int): 要显示的数字（0-999）
    
    Returns:
        str | None: 数字的矩阵表示形式，如果数字超出范围则返回None
    """
    # 检查数字是否在有效范围内
    if not 0 <= number <= 999:
        return None

    # 定义每个数字的矩阵表示
    digits = {
        '0': ['###', '# #', '# #', '# #', '###'],
        '1': ['  #', '  #', '  #', '  #', '  #'],
        '2': ['###', '  #', '###', '#  ', '###'],
        '3': ['###', '  #', '###', '  #', '###'],
        '4': ['# #', '# #', '###', '  #', '  #'],
        '5': ['###', '#  ', '###', '  #', '###'],
        '6': ['###', '#  ', '###', '# #', '###'],
        '7': ['###', '  #', '  #', '  #', '  #'],
        '8': ['###', '# #', '###', '# #', '###'],
        '9': ['###', '# #', '###', '  #', '###'],
    }

    # 另一种方法，使用前导零：num_str = f"{number:03d}"
    num_str = str(number)  # 转换为字符串，不填充零

    result = []
    # 遍历每一行（5行）
    for row in range(5):
        # 将每个数字在当前行的表示连接起来
        line = ' '.join(digits[digit][row] for digit in num_str)
        result.append(line)

    # 将所有行连接成最终的矩阵显示
    matrix_display = '\n'.join(result)
    return f'\n{matrix_display}\n'
