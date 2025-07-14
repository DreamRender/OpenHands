import asyncio
import os
import subprocess
import sys
import time
from dataclasses import dataclass

from openhands.core.logger import openhands_logger as logger
from openhands.events.action import Action, IPythonRunCellAction
from openhands.events.observation import IPythonRunCellObservation
from openhands.runtime.plugins.jupyter.execute_server import JupyterKernel
from openhands.runtime.plugins.requirement import Plugin, PluginRequirement
from openhands.runtime.utils import find_available_tcp_port
from openhands.utils.shutdown_listener import should_continue


@dataclass
class JupyterRequirement(PluginRequirement):
    """
    Jupyter插件的依赖需求类

    继承自PluginRequirement，用于定义Jupyter插件的依赖需求
    """
    name: str = 'jupyter'
    # 插件名称，默认为'jupyter'


class JupyterPlugin(Plugin):
    """
    Jupyter插件核心类

    负责启动和管理Jupyter kernel gateway服务，提供代码执行环境
    """
    name: str = 'jupyter'
    # 插件名称标识符

    kernel_gateway_port: int
    # Jupyter kernel gateway服务的端口号

    kernel_id: str
    # Jupyter kernel的唯一标识符

    gateway_process: asyncio.subprocess.Process | subprocess.Popen
    # Jupyter kernel gateway进程对象，根据平台使用不同的进程类型

    python_interpreter_path: str

    # Python解释器的完整路径

    async def initialize(
            self, username: str, kernel_id: str = 'openhands-default'
    ) -> None:
        """
        初始化Jupyter插件

        启动Jupyter kernel gateway服务，配置环境变量和路径

        Args:
            username: 用户名，用于切换到对应用户执行命令
            kernel_id: kernel的唯一标识符，默认为'openhands-default'
        """
        # 查找可用的TCP端口，端口范围40000-49999
        self.kernel_gateway_port = find_available_tcp_port(40000, 49999)
        self.kernel_id = kernel_id

        # 检查是否为本地运行时模式
        is_local_runtime = os.environ.get('LOCAL_RUNTIME_MODE') == '1'
        # 检查是否为Windows平台
        is_windows = sys.platform == 'win32'

        if not is_local_runtime:
            # 非本地运行时模式的配置
            # 使用su命令切换用户
            prefix = f'su - {username} -s '
            # 设置环境变量和路径，包括poetry虚拟环境、Python路径、micromamba环境
            poetry_prefix = (
                'cd /openhands/code\n'  # 切换到代码仓库目录
                'export POETRY_VIRTUALENVS_PATH=/openhands/poetry;\n'  # 设置poetry虚拟环境路径
                'export PYTHONPATH=/openhands/code:$PYTHONPATH;\n'  # 添加代码路径到Python路径
                'export MAMBA_ROOT_PREFIX=/openhands/micromamba;\n'  # 设置micromamba根目录
                '/openhands/micromamba/bin/micromamba run -n openhands '  # 在openhands环境中运行
            )
        else:
            # 本地运行时模式的配置
            prefix = ''
            # 获取代码仓库路径
            code_repo_path = os.environ.get('OPENHANDS_REPO_PATH')
            if not code_repo_path:
                raise ValueError(
                    'OPENHANDS_REPO_PATH environment variable is not set. '
                    'This is required for the jupyter plugin to work with LocalRuntime.'
                )
                # 错误信息：OPENHANDS_REPO_PATH环境变量未设置。这是jupyter插件在LocalRuntime中工作所必需的。

            # 本地运行时中，正确的环境由PATH确保
            poetry_prefix = f'cd {code_repo_path}\n'

        if is_windows:
            # Windows平台特定的命令格式
            jupyter_launch_command = (
                f'cd /d "{code_repo_path}" && '  # 切换到代码仓库目录
                f'"{sys.executable}" -m jupyter kernelgateway '  # 启动jupyter kernelgateway
                '--KernelGatewayApp.ip=0.0.0.0 '  # 设置监听所有IP地址
                f'--KernelGatewayApp.port={self.kernel_gateway_port}'  # 设置端口号
            )
            logger.debug(f'Jupyter launch command (Windows): {jupyter_launch_command}')

            # 在Windows上使用同步的subprocess.Popen，因为asyncio.create_subprocess_shell在Windows平台有限制
            self.gateway_process = subprocess.Popen(  # type: ignore[ASYNC101] # noqa: ASYNC101
                jupyter_launch_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=True,
                text=True,
            )

            # Windows特定的stdout处理，使用同步的time.sleep
            # 因为asyncio在Windows上对子进程操作有限制
            output = ''
            while should_continue():
                if self.gateway_process.stdout is None:
                    time.sleep(1)  # type: ignore[ASYNC101] # noqa: ASYNC101
                    continue

                # 读取一行输出
                line = self.gateway_process.stdout.readline()
                if not line:
                    time.sleep(1)  # type: ignore[ASYNC101] # noqa: ASYNC101
                    continue

                output += line
                # 检查是否包含'at'字符串，表示服务已启动
                if 'at' in line:
                    break

                time.sleep(1)  # type: ignore[ASYNC101] # noqa: ASYNC101
                logger.debug('Waiting for jupyter kernel gateway to start...')
                # 等待jupyter kernel gateway启动...

            logger.debug(
                f'Jupyter kernel gateway started at port {self.kernel_gateway_port}. Output: {output}'
            )
            # Jupyter kernel gateway已在端口{port}启动
        else:
            # Unix系统（Linux/macOS）
            jupyter_launch_command = (
                f"{prefix}/bin/bash << 'EOF'\n"  # 使用bash heredoc语法
                f'{poetry_prefix}'  # 环境配置前缀
                f'"{sys.executable}" -m jupyter kernelgateway '  # 启动jupyter kernelgateway
                '--KernelGatewayApp.ip=0.0.0.0 '  # 设置监听所有IP地址
                f'--KernelGatewayApp.port={self.kernel_gateway_port}\n'  # 设置端口号
                'EOF'
            )
            logger.debug(f'Jupyter launch command: {jupyter_launch_command}')

            # 使用asyncio.create_subprocess_shell而不是subprocess.Popen
            # 避免ASYNC101 linting错误
            self.gateway_process = await asyncio.create_subprocess_shell(
                jupyter_launch_command,
                stderr=asyncio.subprocess.STDOUT,
                stdout=asyncio.subprocess.PIPE,
            )
            # 读取stdout直到kernel gateway准备就绪
            output = ''
            while should_continue() and self.gateway_process.stdout is not None:
                line_bytes = await self.gateway_process.stdout.readline()
                line = line_bytes.decode('utf-8')
                output += line
                # 检查是否包含'at'字符串，表示服务已启动
                if 'at' in line:
                    break
                await asyncio.sleep(1)
                logger.debug('Waiting for jupyter kernel gateway to start...')
                # 等待jupyter kernel gateway启动...

            logger.debug(
                f'Jupyter kernel gateway started at port {self.kernel_gateway_port}. Output: {output}'
            )
            # Jupyter kernel gateway已在端口{port}启动

        # 运行一个简单的Python命令来获取解释器路径
        _obs = await self.run(
            IPythonRunCellAction(code='import sys; print(sys.executable)')
        )
        # 提取并保存Python解释器路径
        self.python_interpreter_path = _obs.content.strip()

    async def _run(self, action: Action) -> IPythonRunCellObservation:
        """
        在jupyter kernel中运行代码单元的内部方法

        Args:
            action: 要执行的Action对象，必须是IPythonRunCellAction类型

        Returns:
            IPythonRunCellObservation: 包含执行结果的观察对象

        Raises:
            ValueError: 如果action不是IPythonRunCellAction类型
        """
        if not isinstance(action, IPythonRunCellAction):
            raise ValueError(
                f'Jupyter plugin only supports IPythonRunCellAction, but got {action}'
            )
            # Jupyter插件仅支持IPythonRunCellAction，但得到了{action}

        # 如果kernel还未初始化，则创建新的kernel实例
        if not hasattr(self, 'kernel'):
            self.kernel = JupyterKernel(
                f'localhost:{self.kernel_gateway_port}', self.kernel_id
            )

        # 如果kernel尚未初始化，则进行初始化
        if not self.kernel.initialized:
            await self.kernel.initialize()

        # 执行代码并获取结构化输出
        output = await self.kernel.execute(action.code, timeout=action.timeout)

        # 从结构化输出中提取文本内容和图像URL
        text_content = output.get('text', '')
        image_urls = output.get('images', [])

        # 返回包含执行结果的观察对象
        return IPythonRunCellObservation(
            content=text_content,
            code=action.code,
            image_urls=image_urls if image_urls else None,
        )

    async def run(self, action: Action) -> IPythonRunCellObservation:
        """
        运行插件的公共方法

        Args:
            action: 要执行的Action对象

        Returns:
            IPythonRunCellObservation: 包含执行结果的观察对象
        """
        # 调用内部_run方法执行代码
        obs = await self._run(action)
        return obs
