"""
运行时初始化模块。

该模块负责初始化运行时环境，包括创建用户账户和工作目录。
"""

import os
import subprocess
import sys

from openhands.core.logger import openhands_logger as logger


def init_user_and_working_directory(
    username: str, user_id: int, initial_cwd: str
) -> int | None:
    """
    创建工作目录和用户（如果不存在）。
    
    该函数有效地执行以下步骤：
    * 创建工作目录：
        - 使用mkdir -p创建目录
        - 将所有权设置为username:root
        - 调整权限，使组和其他用户可读可写
    * 用户验证和创建：
        - 使用id -u检查用户是否存在
        - 如果用户存在且UID正确，则跳过创建
        - 如果UID不同，记录警告并返回更新的user_id
        - 如果用户不存在，则继续创建用户
    * Sudo配置：
        - 将%sudo ALL=(ALL) NOPASSWD:ALL追加到/etc/sudoers，
          为sudo组授予免密码sudo访问权限
        - 使用useradd命令将用户添加到sudo组，如果需要会处理UID冲突
    
    Args:
        username (str): 要创建的用户名
        user_id (int): 分配给用户的用户ID
        initial_cwd (str): 要创建的初始工作目录
    
    Returns:
        int | None: 如果用户ID被更新则返回用户ID，否则返回None
    """
    # 如果在Windows上运行，只创建目录并返回
    if sys.platform == 'win32':
        logger.debug('Running on Windows, skipping Unix-specific user setup')
        # 中文说明：在Windows上运行，跳过Unix特定的用户设置
        logger.debug(f'Client working directory: {initial_cwd}')
        # 中文说明：客户端工作目录

        # 如果工作目录不存在则创建
        os.makedirs(initial_cwd, exist_ok=True)
        logger.debug(f'Created working directory: {initial_cwd}')
        # 中文说明：已创建工作目录

        return None

    # 如果用户名是CURRENT_USER，则不需要做任何事情
    # 这是本地runtime的特殊情况
    if username == os.getenv('USER') and username not in ['root', 'openhands']:
        return None

    # 首先创建工作目录，独立于用户
    logger.debug(f'Client working directory: {initial_cwd}')
    # 中文说明：客户端工作目录
    
    # 设置umask并创建目录
    command = f'umask 002; mkdir -p {initial_cwd}'
    output = subprocess.run(command, shell=True, capture_output=True)
    out_str = output.stdout.decode()

    # 更改目录所有权为指定用户和root组
    command = f'chown -R {username}:root {initial_cwd}'
    output = subprocess.run(command, shell=True, capture_output=True)
    out_str += output.stdout.decode()

    # 设置目录权限，使组具有读写权限
    command = f'chmod g+rw {initial_cwd}'
    output = subprocess.run(command, shell=True, capture_output=True)
    out_str += output.stdout.decode()
    logger.debug(f'Created working directory. Output: [{out_str}]')
    # 中文说明：已创建工作目录。输出

    # 跳过root用户，因为它已经存在
    if username == 'root':
        return None

    # 检查用户名是否已经存在
    existing_user_id = -1
    try:
        # 使用id -u命令检查用户是否存在
        result = subprocess.run(
            f'id -u {username}', shell=True, check=True, capture_output=True
        )
        existing_user_id = int(result.stdout.decode().strip())

        # 如果用户ID已经存在，跳过设置
        if existing_user_id == user_id:
            logger.debug(
                f'User `{username}` already has the provided UID {user_id}. Skipping user setup.'
            )
            # 中文说明：用户已经具有提供的UID。跳过用户设置。
        else:
            logger.warning(
                f'User `{username}` already exists with UID {existing_user_id}. Skipping user setup.'
            )
            # 中文说明：用户已经存在，具有不同的UID。跳过用户设置。
            return existing_user_id
        return None
    except subprocess.CalledProcessError as e:
        # 返回码1表示用户尚不存在
        if e.returncode == 1:
            logger.debug(
                f'User `{username}` does not exist. Proceeding with user creation.'
            )
            # 中文说明：用户不存在。继续创建用户。
        else:
            logger.error(f'Error checking user `{username}`, skipping setup:\n{e}\n')
            # 中文说明：检查用户时出错，跳过设置
            raise

    # 添加sudoer配置
    sudoer_line = r"echo '%sudo ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers"
    output = subprocess.run(sudoer_line, shell=True, capture_output=True)
    if output.returncode != 0:
        raise RuntimeError(f'Failed to add sudoer: {output.stderr.decode()}')
        # 中文说明：添加sudoer失败
    logger.debug(f'Added sudoer successfully. Output: [{output.stdout.decode()}]')
    # 中文说明：成功添加sudoer。输出

    # 使用useradd命令创建用户
    command = (
        f'useradd -rm -d /home/{username} -s /bin/bash '
        f'-g root -G sudo -u {user_id} {username}'
    )
    output = subprocess.run(command, shell=True, capture_output=True)
    if output.returncode == 0:
        logger.debug(
            f'Added user `{username}` successfully with UID {user_id}. Output: [{output.stdout.decode()}]'
        )
        # 中文说明：成功添加用户，使用UID。输出
    else:
        raise RuntimeError(
            f'Failed to create user `{username}` with UID {user_id}. Output: [{output.stderr.decode()}]'
        )
        # 中文说明：创建用户失败，使用UID。输出
    return None
