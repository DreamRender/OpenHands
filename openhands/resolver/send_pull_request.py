import argparse
import json
import os
import shutil
import subprocess

import jinja2
from pydantic import SecretStr

from openhands.core.config import LLMConfig
from openhands.core.logger import openhands_logger as logger
from openhands.integrations.service_types import ProviderType
from openhands.llm.llm import LLM
from openhands.resolver.interfaces.bitbucket import BitbucketIssueHandler
from openhands.resolver.interfaces.github import GithubIssueHandler
from openhands.resolver.interfaces.gitlab import GitlabIssueHandler
from openhands.resolver.interfaces.issue import Issue
from openhands.resolver.interfaces.issue_definitions import ServiceContextIssue
from openhands.resolver.io_utils import (
    load_single_resolver_output,
)
from openhands.resolver.patching import apply_diff, parse_patch
from openhands.resolver.resolver_output import ResolverOutput
from openhands.resolver.utils import identify_token
from openhands.utils.async_utils import GENERAL_TIMEOUT, call_async_from_sync


def apply_patch(repo_dir: str, patch: str) -> None:
    """将补丁应用到Repository
    
    解析git补丁并将其应用到指定的Repository目录。
    支持文件的创建、删除、修改和重命名操作。
    
    Args:
        repo_dir (str): 包含Repository的目录路径
        patch (str): 要应用的补丁内容
        
    Note:
        这个函数会处理各种复杂的git操作，包括：
        - 文件创建和删除
        - 文件重命名和移动
        - 保持原始文件的行结束符格式
    """
    # 解析补丁内容
    diffs = parse_patch(patch)
    for diff in diffs:
        if not diff.header.new_path:
            logger.warning('Could not determine file to patch')
            continue

        # 从路径中移除"a/"和"b/"前缀
        old_path = (
            os.path.join(
                repo_dir, diff.header.old_path.removeprefix('a/').removeprefix('b/')
            )
            if diff.header.old_path and diff.header.old_path != '/dev/null'
            else None
        )
        new_path = os.path.join(
            repo_dir, diff.header.new_path.removeprefix('a/').removeprefix('b/')
        )

        # 检查文件是否被删除
        if diff.header.new_path == '/dev/null':
            assert old_path is not None
            if os.path.exists(old_path):
                os.remove(old_path)
                logger.info(f'Deleted file: {old_path}')
            continue

        # 处理文件重命名
        if old_path and new_path and 'rename from' in patch:
            # 创建新路径的父目录
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            try:
                # 尝试直接移动文件
                shutil.move(old_path, new_path)
            except shutil.SameFileError:
                # 如果是同一个文件（可能发生在目录重命名时），先复制再删除
                shutil.copy2(old_path, new_path)
                os.remove(old_path)

            # 尝试删除空的父目录
            old_dir = os.path.dirname(old_path)
            while old_dir and old_dir.startswith(repo_dir):
                try:
                    os.rmdir(old_dir)
                    old_dir = os.path.dirname(old_dir)
                except OSError:
                    # 目录不为空或其他错误，停止尝试删除父目录
                    break
            continue

        # 处理文件内容修改
        if old_path:
            # 以二进制模式打开文件以检测行结束符
            with open(old_path, 'rb') as f:
                original_content = f.read()

            # 检测行结束符类型
            if b'\r\n' in original_content:
                newline = '\r\n'
            elif b'\n' in original_content:
                newline = '\n'
            else:
                newline = None  # 让Python决定

            try:
                with open(old_path, 'r', newline=newline) as f:
                    split_content = [x.strip(newline) for x in f.readlines()]
            except UnicodeDecodeError as e:
                logger.error(f'Error reading file {old_path}: {e}')
                split_content = []
        else:
            # 新文件，使用默认的行结束符
            newline = '\n'
            split_content = []

        if diff.changes is None:
            logger.warning(f'No changes to apply for {old_path}')
            continue

        # 应用差异到文件内容
        new_content = apply_diff(diff, split_content)

        # 确保目录存在再写入文件
        os.makedirs(os.path.dirname(new_path), exist_ok=True)

        # 使用检测到的行结束符写入新内容
        with open(new_path, 'w', newline=newline) as f:
            for line in new_content:
                print(line, file=f)

    logger.info('Patch applied successfully')


def initialize_repo(
    output_dir: str, issue_number: int, issue_type: str, base_commit: str | None = None
) -> str:
    """初始化Repository
    
    从输出目录复制Repository到补丁目录，并可选择地检出到指定提交。
    
    Args:
        output_dir (str): 将Repository写入的输出目录
        issue_number (int): 要修复的Issue编号
        issue_type (str): Issue的类型
        base_commit (str | None, optional): 要检出的基础提交（如果issue_type是pr）
        
    Returns:
        str: 初始化后的Repository目录路径
        
    Raises:
        ValueError: 当源目录不存在时抛出异常
        RuntimeError: 当检出提交失败时抛出异常
    """
    src_dir = os.path.join(output_dir, 'repo')
    dest_dir = os.path.join(output_dir, 'patches', f'{issue_type}_{issue_number}')

    if not os.path.exists(src_dir):
        raise ValueError(f'Source directory {src_dir} does not exist.')

    # 如果目标目录存在，先删除
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)

    # 复制Repository
    shutil.copytree(src_dir, dest_dir)
    logger.info(f'Copied repository to {dest_dir}')

    # 如果提供了基础提交，则检出到该提交
    if base_commit:
        result = subprocess.run(
            f'git -C {dest_dir} checkout {base_commit}',
            shell=True,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.info(f'Error checking out commit: {result.stderr}')
            raise RuntimeError('Failed to check out commit')

    return dest_dir


def make_commit(repo_dir: str, issue: Issue, issue_type: str) -> None:
    """使用更改内容向Repository提交
    
    配置git用户信息（如果需要），添加所有更改并创建提交。
    
    Args:
        repo_dir (str): 包含Repository的目录
        issue (Issue): 要修复的Issue
        issue_type (str): Issue的类型
        
    Raises:
        RuntimeError: 当git操作失败或没有更改可提交时抛出异常
    """
    # 检查git用户名是否已设置
    result = subprocess.run(
        f'git -C {repo_dir} config user.name',
        shell=True,
        capture_output=True,
        text=True,
    )

    if not result.stdout.strip():
        # 如果用户名未设置，配置git
        subprocess.run(
            f'git -C {repo_dir} config user.name "openhands" && '
            f'git -C {repo_dir} config user.email "openhands@all-hands.dev" && '
            f'git -C {repo_dir} config alias.git "git --no-pager"',
            shell=True,
            check=True,
        )
        logger.info('Git user configured as openhands')

    # 将所有更改添加到git索引
    result = subprocess.run(
        f'git -C {repo_dir} add .', shell=True, capture_output=True, text=True
    )
    if result.returncode != 0:
        logger.error(f'Error adding files: {result.stderr}')
        raise RuntimeError('Failed to add files to git')

    # 检查git索引的状态
    status_result = subprocess.run(
        f'git -C {repo_dir} status --porcelain',
        shell=True,
        capture_output=True,
        text=True,
    )

    # 如果没有更改，抛出错误
    if not status_result.stdout.strip():
        logger.error(
            f'No changes to commit for issue #{issue.number}. Skipping commit.'
        )
        raise RuntimeError('ERROR: Openhands failed to make code changes.')

    # 准备提交消息
    commit_message = f'Fix {issue_type} #{issue.number}: {issue.title}'

    # 提交更改
    result = subprocess.run(
        ['git', '-C', repo_dir, 'commit', '-m', commit_message],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f'Failed to commit changes: {result}')


def send_pull_request(
    issue: Issue,
    token: str,
    username: str | None,
    platform: ProviderType,
    patch_dir: str,
    pr_type: str,
    fork_owner: str | None = None,
    additional_message: str | None = None,
    target_branch: str | None = None,
    reviewer: str | None = None,
    pr_title: str | None = None,
    base_domain: str | None = None,
) -> str:
    """向GitHub、GitLab或Bitbucket Repository发送Pull Request
    
    创建新分支，推送更改，并创建Pull Request。
    支持多种PR类型和各种可选配置。
    
    Args:
        issue (Issue): 要发送Pull Request的Issue
        token (str): 用于认证的token
        username (str | None): 用户名（如果提供）
        platform (ProviderType): Repository的平台类型
        patch_dir (str): 包含要应用的补丁的目录
        pr_type (str): 类型：branch（不创建PR）、draft或ready（创建常规PR）
        fork_owner (str | None, optional): 推送更改的fork所有者（如果与原始Repository所有者不同）
        additional_message (str | None, optional): 作为PR评论发布的附加消息（json列表格式）
        target_branch (str | None, optional): 创建Pull Request的目标分支（默认为Repository默认分支）
        reviewer (str | None, optional): 要分配的审查者用户名
        pr_title (str | None, optional): Pull Request的自定义标题
        base_domain (str | None, optional): git服务器的基础域名
        
    Returns:
        str: 创建的Pull Request或分支的URL
        
    Raises:
        ValueError: 当PR类型无效或平台不支持时抛出异常
        RuntimeError: 当git操作失败时抛出异常
    """
    if pr_type not in ['branch', 'draft', 'ready']:
        raise ValueError(f'Invalid pr_type: {pr_type}')

    # 根据平台确定默认base_domain
    if base_domain is None:
        if platform == ProviderType.GITHUB:
            base_domain = 'github.com'
        elif platform == ProviderType.GITLAB:
            base_domain = 'gitlab.com'
        else:  # platform == ProviderType.BITBUCKET
            base_domain = 'bitbucket.org'

    # 根据平台创建相应的处理器
    handler = None
    if platform == ProviderType.GITHUB:
        handler = ServiceContextIssue(
            GithubIssueHandler(issue.owner, issue.repo, token, username, base_domain),
            None,
        )
    elif platform == ProviderType.GITLAB:
        handler = ServiceContextIssue(
            GitlabIssueHandler(issue.owner, issue.repo, token, username, base_domain),
            None,
        )
    elif platform == ProviderType.BITBUCKET:
        handler = ServiceContextIssue(
            BitbucketIssueHandler(
                issue.owner, issue.repo, token, username, base_domain
            ),
            None,
        )
    else:
        raise ValueError(f'Unsupported platform: {platform}')

    # 创建具有唯一名称的新分支
    base_branch_name = f'openhands-fix-issue-{issue.number}'
    branch_name = handler.get_branch_name(
        base_branch_name=base_branch_name,
    )

    # 获取默认分支或使用指定的目标分支
    logger.info('Getting base branch...')
    if target_branch:
        base_branch = target_branch
        exists = handler.branch_exists(branch_name=target_branch)
        if not exists:
            raise ValueError(f'Target branch {target_branch} does not exist')
    else:
        base_branch = handler.get_default_branch_name()
    logger.info(f'Base branch: {base_branch}')

    # 创建并检出新分支
    logger.info('Creating new branch...')
    result = subprocess.run(
        ['git', '-C', patch_dir, 'checkout', '-b', branch_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(f'Error creating new branch: {result.stderr}')
        raise RuntimeError(
            f'Failed to create a new branch {branch_name} in {patch_dir}:'
        )

    # 确定要推送到的Repository（原始或fork）
    push_owner = fork_owner if fork_owner else issue.owner

    handler._strategy.set_owner(push_owner)

    # 推送更改
    logger.info('Pushing changes...')
    push_url = handler.get_clone_url()
    result = subprocess.run(
        ['git', '-C', patch_dir, 'push', push_url, branch_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error(f'Error pushing changes: {result.stderr}')
        raise RuntimeError('Failed to push changes to the remote repository')

    # 准备PR数据：标题和内容
    final_pr_title = (
        pr_title if pr_title else f'Fix issue #{issue.number}: {issue.title}'
    )
    pr_body = f'This pull request fixes #{issue.number}.'
    if additional_message:
        pr_body += f'\n\n{additional_message}'
    pr_body += '\n\nAutomatic fix generated by [OpenHands](https://github.com/All-Hands-AI/OpenHands/) 🙌'

    # 对于跨Repository的Pull Request，我们需要发送head参数，格式为fork_owner:branch
    # 根据git文档：https://docs.github.com/en/rest/pulls/pulls?apiVersion=2022-11-28#create-a-pull-request
    # head参数用法：实现更改的分支名称。对于同一网络中的跨Repository Pull Request，
    # 使用用户名命名空间头部，如：username:branch。
    if fork_owner and platform == ProviderType.GITHUB:
        head_branch = f'{fork_owner}:{branch_name}'
    else:
        head_branch = branch_name
        
    # 如果我们不发送PR，可以提前结束并返回URL供用户手动打开PR
    if pr_type == 'branch':
        url = handler.get_compare_url(branch_name)
    else:
        # 为GitHub API准备PR数据
        data = {
            'title': final_pr_title,
            ('body' if platform == ProviderType.GITHUB else 'description'): pr_body,
            (
                'head' if platform == ProviderType.GITHUB else 'source_branch'
            ): head_branch,
            (
                'base' if platform == ProviderType.GITHUB else 'target_branch'
            ): base_branch,
            'draft': pr_type == 'draft',
        }

        # 创建Pull Request
        pr_data = handler.create_pull_request(data)
        url = pr_data['html_url']

        # 如果指定了审查者且不是分支类型，请求审查
        if reviewer and pr_type != 'branch':
            number = pr_data['number']
            handler.request_reviewers(reviewer, number)

    logger.info(
        f'{pr_type} created: {url}\n\n--- Title: {final_pr_title}\n\n--- Body:\n{pr_body}'
    )

    return url


def update_existing_pull_request(
    issue: Issue,
    token: str,
    username: str | None,
    platform: ProviderType,
    patch_dir: str,
    llm_config: LLMConfig,
    comment_message: str | None = None,
    additional_message: str | None = None,
    base_domain: str | None = None,
) -> str:
    """使用新补丁更新现有的Pull Request
    
    推送更改到现有的PR分支，并可选择地添加评论和回复评论线程。
    
    Args:
        issue (Issue): 要更新的Issue
        token (str): 用于认证的token
        username (str | None): 用于认证的用户名
        platform (ProviderType): Repository的平台类型
        patch_dir (str): 包含要应用的补丁的目录
        llm_config (LLMConfig): 用于总结更改的LLM配置
        comment_message (str | None, optional): 作为PR评论发布的主要消息
        additional_message (str | None, optional): 作为PR评论发布的附加消息（json列表格式）
        base_domain (str | None, optional): git服务器的基础域名
        
    Returns:
        str: 更新的Pull Request的URL
        
    Raises:
        RuntimeError: 当推送更改失败时抛出异常
    """
    # 根据平台确定默认base_domain
    if base_domain is None:
        base_domain = 'github.com' if platform == ProviderType.GITHUB else 'gitlab.com'

    # 创建处理器
    handler = None
    if platform == ProviderType.GITHUB:
        handler = ServiceContextIssue(
            GithubIssueHandler(issue.owner, issue.repo, token, username, base_domain),
            llm_config,
        )
    else:  # platform == Platform.GITLAB
        handler = ServiceContextIssue(
            GitlabIssueHandler(issue.owner, issue.repo, token, username, base_domain),
            llm_config,
        )

    branch_name = issue.head_branch

    # 准备推送命令
    push_command = (
        f'git -C {patch_dir} push '
        f'{handler.get_authorize_url()}'
        f'{issue.owner}/{issue.repo}.git {branch_name}'
    )

    # 将更改推送到现有分支
    result = subprocess.run(push_command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f'Error pushing changes: {result.stderr}')
        raise RuntimeError('Failed to push changes to the remote repository')

    pr_url = handler.get_pull_url(issue.number)
    logger.info(f'Updated pull request {pr_url} with new patches.')

    # 为PR消息生成所有评论成功指示器的摘要
    if not comment_message and additional_message:
        try:
            explanations = json.loads(additional_message)
            if explanations:
                comment_message = (
                    'OpenHands made the following changes to resolve the issues:\n\n'
                )
                for explanation in explanations:
                    comment_message += f'- {explanation}\n'

                # 如果提供了LLM，使用LLM进行总结
                if llm_config is not None:
                    llm = LLM(llm_config)
                    with open(
                        os.path.join(
                            os.path.dirname(__file__),
                            'prompts/resolve/pr-changes-summary.jinja',
                        ),
                        'r',
                    ) as f:
                        template = jinja2.Template(f.read())
                    prompt = template.render(comment_message=comment_message)
                    response = llm.completion(
                        messages=[{'role': 'user', 'content': prompt}],
                    )
                    comment_message = response.choices[0].message.content.strip()

        except (json.JSONDecodeError, TypeError):
            comment_message = f'A new OpenHands update is available, but failed to parse or summarize the changes:\n{additional_message}'

    # 在PR上发布评论
    if comment_message:
        handler.send_comment_msg(issue.number, comment_message)

    # 回复每个未解决的评论线程
    if additional_message and issue.thread_ids:
        try:
            explanations = json.loads(additional_message)
            for count, reply_comment in enumerate(explanations):
                comment_id = issue.thread_ids[count]
                handler.reply_to_comment(issue.number, comment_id, reply_comment)
        except (json.JSONDecodeError, TypeError):
            msg = f'Error occurred when replying to threads; success explanations {additional_message}'
            handler.send_comment_msg(issue.number, msg)

    return pr_url


def process_single_issue(
    output_dir: str,
    resolver_output: ResolverOutput,
    token: str,
    username: str,
    platform: ProviderType,
    pr_type: str,
    llm_config: LLMConfig,
    fork_owner: str | None,
    send_on_failure: bool,
    target_branch: str | None = None,
    reviewer: str | None = None,
    pr_title: str | None = None,
    base_domain: str | None = None,
) -> None:
    """处理单个Issue并发送Pull Request
    
    根据Issue类型（issue或pr）和解决状态，决定是创建新的PR还是更新现有的PR。
    
    Args:
        output_dir (str): 输出目录
        resolver_output (ResolverOutput): Resolver的输出结果
        token (str): 认证token
        username (str): 用户名
        platform (ProviderType): 平台类型
        pr_type (str): PR类型
        llm_config (LLMConfig): LLM配置
        fork_owner (str | None): Fork所有者
        send_on_failure (bool): 是否在失败时也发送PR
        target_branch (str | None, optional): 目标分支
        reviewer (str | None, optional): 审查者
        pr_title (str | None, optional): PR标题
        base_domain (str | None, optional): 基础域名
        
    Raises:
        ValueError: 当Issue类型无效时抛出异常
    """
    # 根据平台确定默认base_domain
    if base_domain is None:
        base_domain = 'github.com' if platform == ProviderType.GITHUB else 'gitlab.com'
        
    # 如果Issue没有成功解决且不发送失败的结果，则跳过PR创建
    if not resolver_output.success and not send_on_failure:
        logger.info(
            f'Issue {resolver_output.issue.number} was not successfully resolved. Skipping PR creation.'
        )
        return

    issue_type = resolver_output.issue_type

    # 根据Issue类型初始化Repository
    if issue_type == 'issue':
        patched_repo_dir = initialize_repo(
            output_dir,
            resolver_output.issue.number,
            issue_type,
            resolver_output.base_commit,
        )
    elif issue_type == 'pr':
        patched_repo_dir = initialize_repo(
            output_dir,
            resolver_output.issue.number,
            issue_type,
            resolver_output.issue.head_branch,
        )
    else:
        raise ValueError(f'Invalid issue type: {issue_type}')

    # 应用补丁
    apply_patch(patched_repo_dir, resolver_output.git_patch)

    # 创建提交
    make_commit(patched_repo_dir, resolver_output.issue, issue_type)

    # 根据Issue类型决定是更新现有PR还是创建新PR
    if issue_type == 'pr':
        update_existing_pull_request(
            issue=resolver_output.issue,
            token=token,
            username=username,
            platform=platform,
            patch_dir=patched_repo_dir,
            additional_message=resolver_output.result_explanation,
            llm_config=llm_config,
            base_domain=base_domain,
        )
    else:
        send_pull_request(
            issue=resolver_output.issue,
            token=token,
            username=username,
            platform=platform,
            patch_dir=patched_repo_dir,
            pr_type=pr_type,
            fork_owner=fork_owner,
            additional_message=resolver_output.result_explanation,
            target_branch=target_branch,
            reviewer=reviewer,
            pr_title=pr_title,
            base_domain=base_domain,
        )


def main() -> None:
    """主函数：解析命令行参数并处理Pull Request发送流程
    
    这个函数是程序的入口点，负责：
    1. 解析所有命令行参数
    2. 验证认证信息
    3. 加载Resolver输出结果
    4. 处理单个Issue的PR发送
    """
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(
        description='Send a pull request to Github or Gitlab.'
    )
    
    # Repository相关参数
    parser.add_argument(
        '--selected-repo',
        type=str,
        default=None,
        help='repository to send pull request in form of `owner/repo`.',
    )
    
    # 认证相关参数
    parser.add_argument(
        '--token',
        type=str,
        default=None,
        help='token to access the repository.',
    )
    parser.add_argument(
        '--username',
        type=str,
        default=None,
        help='username to access the repository.',
    )
    
    # 输出和Issue参数
    parser.add_argument(
        '--output-dir',
        type=str,
        default='output',
        help='Output directory to write the results.',
    )
    parser.add_argument(
        '--pr-type',
        type=str,
        default='draft',
        choices=['branch', 'draft', 'ready'],
        help='Type of the pull request to send [branch, draft, ready]',
    )
    parser.add_argument(
        '--issue-number',
        type=str,
        required=True,
        help="Issue number to send the pull request for, or 'all_successful' to process all successful issues.",
    )
    
    # Fork和发送选项
    parser.add_argument(
        '--fork-owner',
        type=str,
        default=None,
        help='Owner of the fork to push changes to (if different from the original repo owner).',
    )
    parser.add_argument(
        '--send-on-failure',
        action='store_true',
        help='Send a pull request even if the issue was not successfully resolved.',
    )
    
    # LLM相关参数
    parser.add_argument(
        '--llm-model',
        type=str,
        default=None,
        help='LLM model to use for summarizing changes.',
    )
    parser.add_argument(
        '--llm-api-key',
        type=str,
        default=None,
        help='API key for the LLM model.',
    )
    parser.add_argument(
        '--llm-base-url',
        type=str,
        default=None,
        help='Base URL for the LLM model.',
    )
    
    # PR配置参数
    parser.add_argument(
        '--target-branch',
        type=str,
        default=None,
        help='Target branch to create the pull request against (defaults to repository default branch)',
    )
    parser.add_argument(
        '--reviewer',
        type=str,
        help='GitHub or GitLab username of the person to request review from',
        default=None,
    )
    parser.add_argument(
        '--pr-title',
        type=str,
        help='Custom title for the pull request',
        default=None,
    )
    parser.add_argument(
        '--base-domain',
        type=str,
        default=None,
        help='Base domain for the git server (defaults to "github.com" for GitHub and "gitlab.com" for GitLab)',
    )
    
    # 解析命令行参数
    my_args = parser.parse_args()

    # 获取认证token，优先级：命令行参数 > 环境变量
    token = my_args.token or os.getenv('GITHUB_TOKEN') or os.getenv('GITLAB_TOKEN')
    if not token:
        raise ValueError(
            'token is not set, set via --token or GITHUB_TOKEN or GITLAB_TOKEN environment variable.'
        )
    # 获取用户名
    username = my_args.username if my_args.username else os.getenv('GIT_USERNAME')

    # 识别平台类型
    platform = call_async_from_sync(
        identify_token,
        GENERAL_TIMEOUT,
        token,
        my_args.base_domain,
    )

    # 配置LLM
    api_key = my_args.llm_api_key or os.environ['LLM_API_KEY']
    llm_config = LLMConfig(
        model=my_args.llm_model or os.environ['LLM_MODEL'],
        api_key=SecretStr(api_key) if api_key else None,
        base_url=my_args.llm_base_url or os.environ.get('LLM_BASE_URL', None),
    )

    # 验证输出目录存在
    if not os.path.exists(my_args.output_dir):
        raise ValueError(f'Output directory {my_args.output_dir} does not exist.')

    # 验证并解析Issue编号
    if not my_args.issue_number.isdigit():
        raise ValueError(f'Issue number {my_args.issue_number} is not a number.')
    issue_number = int(my_args.issue_number)
    
    # 加载Resolver输出结果
    output_path = os.path.join(my_args.output_dir, 'output.jsonl')
    resolver_output = load_single_resolver_output(output_path, issue_number)
    
    # 验证用户名
    if not username:
        raise ValueError('username is required.')
        
    # 处理单个Issue
    process_single_issue(
        my_args.output_dir,
        resolver_output,
        token,
        username,
        platform,
        my_args.pr_type,
        llm_config,
        my_args.fork_owner,
        my_args.send_on_failure,
        my_args.target_branch,
        my_args.reviewer,
        my_args.pr_title,
        my_args.base_domain,
    )


if __name__ == '__main__':
    # 当作为主程序运行时，调用main函数
    main()
