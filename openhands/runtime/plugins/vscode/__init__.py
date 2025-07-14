import asyncio
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from openhands.core.logger import openhands_logger as logger
from openhands.events.action import Action
from openhands.events.observation import Observation
from openhands.runtime.plugins.requirement import Plugin, PluginRequirement
from openhands.runtime.utils.system import check_port_available
from openhands.utils.shutdown_listener import should_continue


@dataclass
class VSCodeRequirement(PluginRequirement):
    """
    VSCode插件的依赖需求类

    继承自PluginRequirement，用于定义VSCode插件的依赖需求
    """
    name: str = 'vscode'
    # 插件名称，默认为'vscode'


class VSCodePlugin(Plugin):
    """
    VSCode插件核心类

    负责启动和管理VSCode服务器，提供Web版本的VSCode编辑器
    """
    name: str = 'vscode'
    # 插件名称标识符

    vscode_port: Optional[int] = None
    # VSCode服务器的端口号，如果插件未启用则为None

    vscode_connection_token: Optional[str] = None
    # VSCode服务器的连接令牌，用于身份验证，如果插件未启用则为None

    gateway_process: asyncio.subprocess.Process

    # VSCode服务器进程对象

    async def initialize(self, username: str) -> None:
        """
        初始化VSCode插件

        启动VSCode服务器，配置设置文件和端口

        Args:
            username: 用户名，用于切换到对应用户执行命令
        """
        # 检查是否为Windows平台 - VSCode插件不支持Windows
        if os.name == 'nt' or sys.platform == 'win32':
            self.vscode_port = None
            self.vscode_connection_token = None
            logger.warning(
                'VSCode plugin is not supported on Windows. Plugin will be disabled.'
            )
            # VSCode插件不支持Windows。插件将被禁用。
            return

        # 检查用户名是否支持
        if username not in ['root', 'openhands']:
            self.vscode_port = None
            self.vscode_connection_token = None
            logger.warning(
                'VSCodePlugin is only supported for root or openhands user. '
                'It is not yet supported for other users (i.e., when running LocalRuntime).'
            )
            # VSCodePlugin仅支持root或openhands用户。还不支持其他用户（即运行LocalRuntime时）。
            return

        # 设置VSCode的settings.json文件
        self._setup_vscode_settings()

        try:
            # 从环境变量中获取VSCode端口
            self.vscode_port = int(os.environ['VSCODE_PORT'])
        except (KeyError, ValueError):
            logger.warning(
                'VSCODE_PORT environment variable not set or invalid. VSCode plugin will be disabled.'
            )
            # VSCODE_PORT环境变量未设置或无效。VSCode插件将被禁用。
            return

        # 生成连接令牌
        self.vscode_connection_token = str(uuid.uuid4())

        # 检查端口是否可用
        if not check_port_available(self.vscode_port):
            logger.warning(
                f'Port {self.vscode_port} is not available. VSCode plugin will be disabled.'
            )
            # 端口{port}不可用。VSCode插件将被禁用。
            return

        # 构建启动VSCode服务器的命令
        cmd = (
            f"su - {username} -s /bin/bash << 'EOF'\n"  # 切换到指定用户
            f'sudo chown -R {username}:{username} /openhands/.openvscode-server\n'  # 更改文件所有者
            'cd /workspace\n'  # 切换到工作目录
            f'exec /openhands/.openvscode-server/bin/openvscode-server --host 0.0.0.0 --connection-token {self.vscode_connection_token} --port {self.vscode_port} --disable-workspace-trust\n'  # 启动VSCode服务器
            'EOF'
        )

        # 使用asyncio.create_subprocess_shell而不是subprocess.Popen
        # 避免ASYNC101 linting错误
        self.gateway_process = await asyncio.create_subprocess_shell(
            cmd,
            stderr=asyncio.subprocess.STDOUT,
            stdout=asyncio.subprocess.PIPE,
        )
        # 读取stdout直到kernel gateway准备就绪
        output = ''
        while should_continue() and self.gateway_process.stdout is not None:
            line_bytes = await self.gateway_process.stdout.readline()
            line = line_bytes.decode('utf-8')
            print(line)  # 打印输出行用于调试
            output += line
            # 检查是否包含'at'字符串，表示服务已启动
            if 'at' in line:
                break
            await asyncio.sleep(1)
            logger.debug('Waiting for VSCode server to start...')
            # 等待VSCode服务器启动...

        logger.debug(
            f'VSCode server started at port {self.vscode_port}. Output: {output}'
        )
        # VSCode服务器已在端口{port}启动

    def _setup_vscode_settings(self) -> None:
        """
        设置VSCode设置文件

        在workspace中创建.vscode目录，并将settings.json文件复制到该目录中
        """
        # 获取插件目录中settings.json文件的路径
        current_dir = Path(__file__).parent
        settings_path = current_dir / 'settings.json'

        # 如果workspace中不存在.vscode目录，则创建它
        workspace_dir = Path(os.getenv('WORKSPACE_BASE', '/workspace'))
        vscode_dir = workspace_dir / '.vscode'
        vscode_dir.mkdir(parents=True, exist_ok=True)

        # 将settings.json文件复制到.vscode目录中
        target_path = vscode_dir / 'settings.json'
        shutil.copy(settings_path, target_path)

        # 确保设置文件对所有用户可读可写
        os.chmod(target_path, 0o666)

        logger.debug(f'VSCode settings copied to {target_path}')
        # VSCode设置已复制到{target_path}

    async def run(self, action: Action) -> Observation:
        """
        为给定的action运行插件

        Args:
            action: 要执行的Action对象

        Returns:
            Observation: 包含执行结果的观察对象

        Raises:
            NotImplementedError: VSCodePlugin不支持run方法
        """
        raise NotImplementedError('VSCodePlugin does not support run method')
        # VSCodePlugin不支持run方法
