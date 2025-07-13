import importlib.resources
import json
import os
import pathlib
import subprocess
import tempfile
import urllib.request
from urllib.error import URLError

from openhands.core.logger import openhands_logger as logger


def download_latest_vsix_from_github() -> str | None:
    """
    从GitHub releases下载最新的.vsix文件。
    
    通过GitHub API获取最新的OpenHands扩展发布版本，并下载对应的.vsix文件。
    
    Returns:
        str | None: 下载的.vsix文件路径，如果失败返回None
    """
    api_url = 'https://api.github.com/repos/All-Hands-AI/OpenHands/releases'
    try:
        # 请求GitHub API获取发布信息
        with urllib.request.urlopen(api_url, timeout=10) as response:
            if response.status != 200:
                logger.debug(
                    f'GitHub API request failed with status: {response.status}'
                )
                return None
            releases = json.loads(response.read().decode())
            # GitHub API按时间倒序返回发布版本（最新的在前面）
            # 我们遍历它们并使用第一个匹配扩展前缀的版本
            for release in releases:
                if release.get('tag_name', '').startswith('ext-v'):
                    for asset in release.get('assets', []):
                        if asset.get('name', '').endswith('.vsix'):
                            download_url = asset.get('browser_download_url')
                            if not download_url:
                                continue
                            # 下载.vsix文件
                            with urllib.request.urlopen(
                                download_url, timeout=30
                            ) as download_response:
                                if download_response.status != 200:
                                    logger.debug(
                                        f'Failed to download .vsix with status: {download_response.status}'
                                    )
                                    continue
                                # 创建临时文件保存下载内容
                                with tempfile.NamedTemporaryFile(
                                    delete=False, suffix='.vsix'
                                ) as tmp_file:
                                    tmp_file.write(download_response.read())
                                    return tmp_file.name
                    # 找到了最新的扩展发布版本但没有.vsix资源
                    return None
    except (URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.debug(f'Failed to download from GitHub releases: {e}')
        return None
    return None


def attempt_vscode_extension_install():
    """
    检查是否在支持的编辑器中运行并尝试安装OpenHands伴侣扩展。
    
    这是一个尽力而为的一次性尝试。检测VS Code或Windsurf环境，
    然后尝试通过多种方式安装OpenHands扩展。
    """
    # 1. 检查是否在支持的编辑器环境中
    is_vscode_like = os.environ.get('TERM_PROGRAM') == 'vscode'
    is_windsurf = (
        os.environ.get('__CFBundleIdentifier') == 'com.exafunction.windsurf'
        or 'windsurf' in os.environ.get('PATH', '').lower()
        or any(
            'windsurf' in val.lower()
            for val in os.environ.values()
            if isinstance(val, str)
        )
    )
    # 如果不在支持的编辑器中，直接返回
    if not (is_vscode_like or is_windsurf):
        return

    # 2. 确定编辑器特定的命令和标志
    if is_windsurf:
        editor_command, editor_name, flag_suffix = 'surf', 'Windsurf', 'windsurf'
    else:
        editor_command, editor_name, flag_suffix = 'code', 'VS Code', 'vscode'

    # 3. 检查是否已经成功安装过扩展
    flag_dir = pathlib.Path.home() / '.openhands'
    flag_file = flag_dir / f'.{flag_suffix}_extension_installed'
    extension_id = 'openhands.openhands-vscode'

    try:
        # 创建标志目录
        flag_dir.mkdir(parents=True, exist_ok=True)
        if flag_file.exists():
            return  # 已经成功安装，退出
    except OSError as e:
        logger.debug(
            f'Could not create or check {editor_name} extension flag directory: {e}'
        )
        return  # 如果无法管理标志文件，不继续进行

    # 4. 检查扩展是否已经安装（即使没有我们的标志）
    if _is_extension_installed(editor_command, extension_id):
        print(f'INFO: OpenHands {editor_name} extension is already installed.')
        # 创建标志以避免将来的检查
        _mark_installation_successful(flag_file, editor_name)
        return

    # 5. 扩展未安装，尝试安装
    print(
        f'INFO: First-time setup: attempting to install the OpenHands {editor_name} extension...'
    )

    # 尝试1: 从GitHub Releases下载（新的主要方法）
    if _attempt_github_install(editor_command, editor_name):
        _mark_installation_successful(flag_file, editor_name)
        return  # 成功！我们完成了

    # 尝试2: 从捆绑的.vsix安装
    if _attempt_bundled_install(editor_command, editor_name):
        _mark_installation_successful(flag_file, editor_name)
        return  # 成功！我们完成了

    # TODO: 尝试3: 从Marketplace安装（当扩展发布时）
    # if _attempt_marketplace_install(editor_command, editor_name, extension_id):
    #     _mark_installation_successful(flag_file, editor_name)
    #     return  # 成功！我们完成了

    # 如果所有尝试都失败，通知用户（但不创建标志 - 允许重试）
    print(
        'INFO: Automatic installation failed. Please check the OpenHands documentation for manual installation instructions.'
    )
    print(
        f'INFO: Will retry installation next time you run OpenHands in {editor_name}.'
    )


def _mark_installation_successful(flag_file: pathlib.Path, editor_name: str) -> None:
    """
    通过创建标志文件标记扩展安装成功。
    
    Args:
        flag_file: 要创建的标志文件路径
        editor_name: 编辑器的可读名称，用于日志记录
    """
    try:
        flag_file.touch()  # 创建空文件作为标志
        logger.debug(f'{editor_name} extension installation marked as successful.')
    except OSError as e:
        logger.debug(f'Could not create {editor_name} extension success flag file: {e}')


def _is_extension_installed(editor_command: str, extension_id: str) -> bool:
    """
    检查OpenHands扩展是否已经安装。
    
    通过运行编辑器的--list-extensions命令来检查扩展是否已安装。
    
    Args:
        editor_command: 运行编辑器的命令（如'code'、'surf'）
        extension_id: 要检查的扩展ID
        
    Returns:
        bool: 如果扩展已安装返回True，否则返回False
    """
    try:
        # 运行编辑器命令列出已安装的扩展
        process = subprocess.run(
            [editor_command, '--list-extensions'],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode == 0:
            # 解析输出的扩展列表
            installed_extensions = process.stdout.strip().split('\n')
            return extension_id in installed_extensions
    except Exception as e:
        logger.debug(f'Could not check installed extensions: {e}')

    return False


def _attempt_github_install(editor_command: str, editor_name: str) -> bool:
    """
    尝试从GitHub Releases安装扩展。
    
    从GitHub releases下载最新的VSIX文件并尝试安装。
    确保临时文件的正确清理。
    
    Args:
        editor_command: 运行编辑器的命令（如'code'、'surf'）
        editor_name: 编辑器的可读名称（如'VS Code'、'Windsurf'）
        
    Returns:
        bool: 如果安装成功返回True，否则返回False
    """
    # 下载最新的VSIX文件
    vsix_path_from_github = download_latest_vsix_from_github()
    if not vsix_path_from_github:
        return False

    github_success = False
    try:
        # 尝试安装下载的VSIX文件
        process = subprocess.run(
            [
                editor_command,
                '--install-extension',
                vsix_path_from_github,
                '--force',  # 强制安装，覆盖现有版本
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode == 0:
            print(
                f'INFO: OpenHands {editor_name} extension installed successfully from GitHub.'
            )
            github_success = True
        else:
            logger.debug(
                f'Failed to install .vsix from GitHub: {process.stderr.strip()}'
            )
    finally:
        # 清理下载的文件
        if os.path.exists(vsix_path_from_github):
            try:
                os.remove(vsix_path_from_github)
            except OSError as e:
                logger.debug(
                    f'Failed to delete temporary file {vsix_path_from_github}: {e}'
                )

    return github_success


def _attempt_bundled_install(editor_command: str, editor_name: str) -> bool:
    """
    尝试从捆绑的VSIX文件安装扩展。
    
    使用与OpenHands安装打包在一起的VSIX文件。
    
    Args:
        editor_command: 运行编辑器的命令（如'code'、'surf'）
        editor_name: 编辑器的可读名称（如'VS Code'、'Windsurf'）
        
    Returns:
        bool: 如果安装成功返回True，否则返回False
    """
    try:
        vsix_filename = 'openhands-vscode-0.0.1.vsix'
        # 使用importlib.resources访问打包的VSIX文件
        with importlib.resources.as_file(
            importlib.resources.files('openhands').joinpath(
                'integrations', 'vscode', vsix_filename
            )
        ) as vsix_path:
            if vsix_path.exists():
                # 尝试安装捆绑的VSIX文件
                process = subprocess.run(
                    [
                        editor_command,
                        '--install-extension',
                        str(vsix_path),
                        '--force',  # 强制安装
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if process.returncode == 0:
                    print(
                        f'INFO: Bundled {editor_name} extension installed successfully.'
                    )
                    return True
                else:
                    logger.debug(
                        f'Bundled .vsix installation failed: {process.stderr.strip()}'
                    )
    except Exception as e:
        logger.debug(f'Could not locate bundled .vsix: {e}.')

    return False


def _attempt_marketplace_install(
    editor_command: str, editor_name: str, extension_id: str
) -> bool:
    """
    尝试从marketplace安装扩展。
    
    此方法目前未使用，因为OpenHands扩展尚未发布到
    VS Code/Windsurf marketplace。保留此方法供将来使用。
    
    Args:
        editor_command: 要使用的命令（'code'或'surf'）
        editor_name: 编辑器的可读名称（'VS Code'或'Windsurf'）
        extension_id: 要安装的扩展ID
        
    Returns:
        bool: 如果安装成功返回True，否则返回False
    """
    try:
        # 尝试从marketplace安装扩展
        process = subprocess.run(
            [editor_command, '--install-extension', extension_id, '--force'],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode == 0:
            print(
                f'INFO: {editor_name} extension installed successfully from the Marketplace.'
            )
            return True
        else:
            logger.debug(f'Marketplace installation failed: {process.stderr.strip()}')
            return False
    except FileNotFoundError:
        # 编辑器命令不在PATH中
        print(
            f"INFO: To complete {editor_name} integration, please ensure the '{editor_command}' command-line tool is in your PATH."
        )
        return False
    except Exception as e:
        logger.debug(
            f'An unexpected error occurred trying to install from the Marketplace: {e}'
        )
        return False