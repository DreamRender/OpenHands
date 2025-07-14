from dataclasses import dataclass
from typing import Callable


@dataclass
class CommandResult:
    """
    表示shell命令执行的结果。

    Attributes:
        content (str): 命令的输出内容
        exit_code (int): 命令执行的退出代码
    """

    content: str
    exit_code: int


class GitHandler:
    """
    通过shell命令执行Git相关操作的处理器。
    
    提供了一系列方法来执行Git操作，包括检查Repository状态、获取文件差异等。
    """

    def __init__(
        self,
        execute_shell_fn: Callable[[str, str | None], CommandResult],
    ):
        """
        初始化GitHandler。
        
        Args:
            execute_shell_fn (Callable[[str, str | None], CommandResult]): 
                执行shell命令的函数，接受命令和工作目录，返回CommandResult
        """
        self.execute = execute_shell_fn
        self.cwd: str | None = None  # 当前工作目录

    def set_cwd(self, cwd: str) -> None:
        """
        设置Git操作的当前工作目录。

        Args:
            cwd (str): 目录路径
        """
        self.cwd = cwd

    def _is_git_repo(self) -> bool:
        """
        检查当前目录是否为Git Repository。

        Returns:
            bool: 如果在Git Repository内则返回True，否则返回False
        """
        cmd = 'git --no-pager rev-parse --is-inside-work-tree'
        output = self.execute(cmd, self.cwd)
        return output.content.strip() == 'true'

    def _get_current_file_content(self, file_path: str) -> str:
        """
        检索给定文件的当前内容。

        Args:
            file_path (str): 文件路径

        Returns:
            str: 文件内容
        """
        output = self.execute(f'cat {file_path}', self.cwd)
        return output.content

    def _verify_ref_exists(self, ref: str) -> bool:
        """
        验证特定的Git引用是否存在。

        Args:
            ref (str): 要检查的Git引用

        Returns:
            bool: 如果引用存在则返回True，否则返回False
        """
        cmd = f'git --no-pager rev-parse --verify {ref}'
        output = self.execute(cmd, self.cwd)
        return output.exit_code == 0

    def _get_valid_ref(self) -> str | None:
        """
        确定用于比较的有效Git引用。

        Returns:
            str | None: 有效的Git引用，如果没有找到有效引用则返回None
        """
        # 获取当前分支和默认分支
        current_branch = self._get_current_branch()
        default_branch = self._get_default_branch()

        # 定义要尝试的引用列表
        ref_current_branch = f'origin/{current_branch}'
        ref_non_default_branch = f'$(git --no-pager merge-base HEAD "$(git --no-pager rev-parse --abbrev-ref origin/{default_branch})")'
        ref_default_branch = 'origin/' + default_branch
        ref_new_repo = '$(git --no-pager rev-parse --verify 4b825dc642cb6eb9a060e54bf8d69288fbee4904)'  # 与空树比较

        refs = [
            ref_current_branch,
            ref_non_default_branch,
            ref_default_branch,
            ref_new_repo,
        ]
        
        # 检查每个引用是否存在
        for ref in refs:
            if self._verify_ref_exists(ref):
                return ref

        return None

    def _get_ref_content(self, file_path: str) -> str:
        """
        从有效的Git引用中检索文件内容。

        Args:
            file_path (str): Repository中的文件路径

        Returns:
            str: 引用中文件的内容，如果不可用则返回空字符串
        """
        ref = self._get_valid_ref()
        if not ref:
            return ''

        cmd = f'git --no-pager show {ref}:{file_path}'
        output = self.execute(cmd, self.cwd)
        return output.content if output.exit_code == 0 else ''

    def _get_default_branch(self) -> str:
        """
        检索Repository的主要Git分支名称。

        Returns:
            str: 主要分支的名称
        """
        cmd = 'git --no-pager remote show origin | grep "HEAD branch"'
        output = self.execute(cmd, self.cwd)
        return output.content.split()[-1].strip()

    def _get_current_branch(self) -> str:
        """
        检索当前选择的Git分支。

        Returns:
            str: 当前分支的名称
        """
        cmd = 'git --no-pager rev-parse --abbrev-ref HEAD'
        output = self.execute(cmd, self.cwd)
        return output.content.strip()

    def _get_changed_files(self) -> list[str]:
        """
        检索与有效Git引用相比的更改文件列表。

        Returns:
            list[str]: 更改文件路径的列表
        """
        ref = self._get_valid_ref()
        if not ref:
            return []

        diff_cmd = f'git --no-pager diff --name-status {ref}'
        output = self.execute(diff_cmd, self.cwd)
        if output.exit_code != 0:
            raise RuntimeError(
                f'Failed to get diff for ref {ref} in {self.cwd}. Command output: {output.content}'
            )
            # 翻译：获取{self.cwd}中引用{ref}的差异失败。命令输出：{output.content}
        return output.content.splitlines()

    def _get_untracked_files(self) -> list[dict[str, str]]:
        """
        检索Repository中未跟踪文件的列表。这对于检测新文件很有用。

        Returns:
            list[dict[str, str]]: 包含文件路径和状态的字典列表
        """
        cmd = 'git --no-pager ls-files --others --exclude-standard'
        output = self.execute(cmd, self.cwd)
        obs_list = output.content.splitlines()
        return (
            [{'status': 'A', 'path': path} for path in obs_list]
            if output.exit_code == 0
            else []
        )

    def get_git_changes(self) -> list[dict[str, str]] | None:
        """
        检索Git Repository中更改文件的列表。

        Returns:
            list[dict[str, str]] | None: 包含文件路径和状态的字典列表。
                如果不是git Repository则返回None
        """
        # 检查是否为Git Repository
        if not self._is_git_repo():
            return None

        # 获取更改文件列表
        changes_list = self._get_changed_files()
        result = parse_git_changes(changes_list)

        # 合并任何未跟踪的文件
        result += self._get_untracked_files()
        return result

    def get_git_diff(self, file_path: str) -> dict[str, str]:
        """
        检索Repository中文件的原始和修改内容。

        Args:
            file_path (str): 文件路径

        Returns:
            dict[str, str]: 包含原始和修改内容的字典
        """
        modified = self._get_current_file_content(file_path)
        original = self._get_ref_content(file_path)

        return {
            'modified': modified,
            'original': original,
        }


def parse_git_changes(changes_list: list[str]) -> list[dict[str, str]]:
    """
    解析更改文件列表并提取其状态和路径。

    Args:
        changes_list (list[str]): 更改文件条目列表

    Returns:
        list[dict[str, str]]: 解析后的文件更改列表及其状态
    """
    result = []
    for line in changes_list:
        # 提取状态和路径
        status = line[:2].strip()
        path = line[2:].strip()

        # 获取第一个非空格字符作为主要状态
        primary_status = status.replace(' ', '')[0]
        result.append(
            {
                'status': primary_status,
                'path': path,
            }
        )
    return result
