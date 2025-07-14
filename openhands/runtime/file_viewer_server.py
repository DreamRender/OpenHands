"""
一个小型、隔离的服务器，仅提供来自action执行服务器的/view端点。
此服务器没有身份验证，只监听本地主机流量。

A tiny, isolated server that provides only the /view endpoint from the action execution server.
This server has no authentication and only listens to localhost traffic.
"""

# 标准库导入
import os  # 操作系统接口模块，用于文件路径操作
import threading  # 线程模块，用于多线程操作

# 第三方库导入
from fastapi import FastAPI, Request  # FastAPI框架和请求对象
from fastapi.responses import HTMLResponse  # HTML响应类
from uvicorn import Config, Server  # ASGI服务器配置和服务器实例

# 项目内部导入
from openhands.core.logger import openhands_logger as logger  # 日志记录器
from openhands.runtime.utils.file_viewer import generate_file_viewer_html  # 文件查看器HTML生成函数


def create_app() -> FastAPI:
    """
    创建FastAPI应用程序实例。

    该函数初始化一个FastAPI应用程序，配置了基本的路由端点，
    包括根路径和文件查看端点。应用程序禁用了OpenAPI文档。

    Returns:
        FastAPI: 配置好的FastAPI应用程序实例
    """
    # 创建FastAPI应用实例，禁用OpenAPI相关的文档端点
    app = FastAPI(
        title='File Viewer Server',  # 应用程序标题
        openapi_url=None,  # 禁用OpenAPI JSON端点
        docs_url=None,  # 禁用Swagger UI文档
        redoc_url=None  # 禁用ReDoc文档
    )

    @app.get('/')
    async def root() -> dict[str, str]:
        """
        根端点，用于检查服务器是否正在运行。

        Returns:
            dict[str, str]: 包含服务器状态的字典
        """
        return {'status': 'File viewer server is running'}
        # 返回状态信息: "File viewer server is running" 表示文件查看器服务器正在运行

    @app.get('/view')
    async def view_file(path: str, request: Request) -> HTMLResponse:
        """
        使用嵌入式查看器查看文件。

        该端点接收文件路径参数，执行安全检查后生成相应的HTML查看器。
        只允许来自本地主机的请求访问。

        Args:
            path (str): 要查看的文件的绝对路径
            request (Request): FastAPI请求对象，包含客户端信息

        Returns:
            HTMLResponse: 包含适当文件查看器的HTML页面
        """
        # 安全检查：仅允许来自localhost的请求
        client_host = request.client.host if request.client else None
        if client_host not in ['127.0.0.1', 'localhost', '::1']:
            # 拒绝非本地主机访问
            return HTMLResponse(
                content='<h1>Access Denied</h1><p>This endpoint is only accessible from localhost</p>',
                # 内容含义：访问被拒绝，此端点仅可从localhost访问
                status_code=403,  # HTTP 403 Forbidden
            )

        # 检查路径是否为绝对路径
        if not os.path.isabs(path):
            return HTMLResponse(
                content=f'<h1>Error: Path must be absolute</h1><p>{path}</p>',
                # 内容含义：错误，路径必须是绝对路径
                status_code=400,  # HTTP 400 Bad Request
            )

        # 检查文件是否存在
        if not os.path.exists(path):
            return HTMLResponse(
                content=f'<h1>Error: File not found</h1><p>{path}</p>',
                # 内容含义：错误，文件未找到
                status_code=404  # HTTP 404 Not Found
            )

        # 检查路径是否为目录
        if os.path.isdir(path):
            return HTMLResponse(
                content=f'<h1>Error: Path is a directory</h1><p>{path}</p>',
                # 内容含义：错误，路径是一个目录
                status_code=400,  # HTTP 400 Bad Request
            )

        try:
            # 生成文件查看器HTML内容
            html_content = generate_file_viewer_html(path)
            return HTMLResponse(content=html_content)

        except Exception as e:
            # 处理文件查看过程中的异常
            return HTMLResponse(
                content=f'<h1>Error viewing file</h1><p>{path}</p><p>{str(e)}</p>',
                # 内容含义：查看文件时出错
                status_code=500,  # HTTP 500 Internal Server Error
            )

    return app


def start_file_viewer_server(port: int) -> tuple[str, threading.Thread]:
    """
    在指定端口上启动文件查看器服务器。

    该函数创建并启动一个文件查看器服务器，将服务器URL保存到临时文件中，
    并在新线程中运行服务器以避免阻塞主线程。

    Args:
        port (int): 要绑定的端口号

    Returns:
        tuple[str, threading.Thread]: 包含服务器URL和线程对象的元组
    """
    # 构建服务器URL
    server_url = f'http://localhost:{port}'

    # 将服务器URL保存到临时文件
    port_path = '/tmp/oh-server-url'  # 临时文件路径
    os.makedirs(os.path.dirname(port_path), exist_ok=True)  # 确保目录存在
    with open(port_path, 'w') as f:
        f.write(server_url)  # 写入服务器URL

    # 记录服务器信息
    logger.info(f'File viewer server URL saved to /tmp/oh-server-url: {server_url}')
    # 日志信息：文件查看器服务器URL已保存到/tmp/oh-server-url
    logger.info(f'Starting file viewer server on port {port}')
    # 日志信息：在端口{port}上启动文件查看器服务器

    # 创建FastAPI应用实例
    app = create_app()

    # 配置服务器参数
    config = Config(
        app=app,  # FastAPI应用实例
        host='127.0.0.1',  # 监听地址（仅本地）
        port=port,  # 监听端口
        log_level='error'  # 日志级别设置为错误
    )
    server = Server(config=config)  # 创建服务器实例

    # 在新线程中运行服务器
    thread = threading.Thread(
        target=server.run,  # 目标函数
        daemon=True  # 设置为守护线程
    )
    thread.start()  # 启动线程

    return server_url, thread


# 主程序入口
if __name__ == '__main__':
    # 启动文件查看器服务器
    url, thread = start_file_viewer_server(port=8000)

    # 保持主线程运行
    try:
        thread.join()  # 等待服务器线程结束
    except KeyboardInterrupt:
        # 捕获键盘中断信号（Ctrl+C）
        logger.info('Server stopped')
        # 日志信息：服务器已停止
