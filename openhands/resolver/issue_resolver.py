# flake8: noqa: E501

import asyncio
import dataclasses
import json
import os
import pathlib
import shutil
import subprocess
from argparse import Namespace
from typing import Any
from uuid import uuid4

from termcolor import colored

import openhands
from openhands.controller.state.state import State
from openhands.core.config import AgentConfig, OpenHandsConfig, SandboxConfig
from openhands.core.config.utils import load_openhands_config
from openhands.core.logger import openhands_logger as logger
from openhands.core.main import create_runtime, run_controller
from openhands.events.action import CmdRunAction, MessageAction
from openhands.events.event import Event
from openhands.events.observation import (
    CmdOutputObservation,
    ErrorObservation,
    Observation,
)
from openhands.events.stream import EventStreamSubscriber
from openhands.integrations.service_types import ProviderType
from openhands.resolver.interfaces.issue import Issue
from openhands.resolver.interfaces.issue_definitions import (
    ServiceContextIssue,
    ServiceContextPR,
)
from openhands.resolver.issue_handler_factory import IssueHandlerFactory
from openhands.resolver.resolver_output import ResolverOutput
from openhands.resolver.utils import (
    codeact_user_response,
    get_unique_uid,
    identify_token,
    reset_logger_for_multiprocessing,
)
from openhands.runtime.base import Runtime
from openhands.utils.async_utils import GENERAL_TIMEOUT, call_async_from_sync

# 目前不要将此配置化，除非我们有其他竞争性的Agent
# Don't make this configurable for now, unless we have other competitive agents
AGENT_CLASS = 'CodeActAgent'


class IssueResolver:
    """Issue解决器主类
    
    这个类是整个Issue解决流程的核心，负责：
    1. 初始化运行环境和配置
    2. 处理不同平台（GitHub、GitLab、Bitbucket）的Issue
    3. 管理Agent的运行流程
    4. 生成和处理git补丁
    5. 输出解决结果
    """
    
    # 检测是否在GitLab CI环境中运行
    GITLAB_CI = os.getenv('GITLAB_CI') == 'true'

    def __init__(self, args: Namespace) -> None:
        """使用给定参数初始化IssueResolver
        
        Args:
            args (Namespace): 命令行参数对象，包含所有必要的配置信息
            
        初始化的参数包括：
            owner: Repository的所有者
            repo: Repository名称
            token: 访问Repository的token
            username: 访问Repository的用户名
            platform: Repository的平台类型
            runtime_container_image: 使用的容器镜像
            max_iterations: 运行的最大迭代次数
            output_dir: 写入结果的输出目录
            llm_config: 大语言模型的配置
            prompt_template: 使用的提示模板
            issue_type: 要解决的Issue类型（issue或pr）
            repo_instruction: 使用的Repository指令
            issue_number: 要解决的Issue编号
            comment_id: 可选的特定评论ID
            base_domain: git服务器的基础域名
        """

        # 解析repository字符串，格式应为 owner/repo
        parts = args.selected_repo.rsplit('/', 1)
        if len(parts) < 2:
            raise ValueError('Invalid repository format. Expected owner/repo')
        owner, repo = parts

        # 获取认证token，优先级：命令行参数 > 环境变量
        token = (
            args.token
            or os.getenv('GITHUB_TOKEN')
            or os.getenv('GITLAB_TOKEN')
            or os.getenv('BITBUCKET_TOKEN')
        )
        
        # 获取用户名，优先级：命令行参数 > 环境变量
        username = args.username if args.username else os.getenv('GIT_USERNAME')
        if not username:
            raise ValueError('Username is required.')

        if not token:
            raise ValueError('Token is required.')

        # 通过token识别平台类型
        platform = call_async_from_sync(
            identify_token,
            GENERAL_TIMEOUT,
            token,
            args.base_domain,
        )

        # 读取Repository指令文件（如果提供）
        repo_instruction = None
        if args.repo_instruction_file:
            with open(args.repo_instruction_file, 'r') as f:
                repo_instruction = f.read()

        issue_type = args.issue_type

        # 读取提示模板文件
        prompt_file = args.prompt_file
        if prompt_file is None:
            # 根据issue类型选择默认的提示模板
            if issue_type == 'issue':
                prompt_file = os.path.join(
                    os.path.dirname(__file__), 'prompts/resolve/basic-with-tests.jinja'
                )
            else:
                prompt_file = os.path.join(
                    os.path.dirname(__file__), 'prompts/resolve/basic-followup.jinja'
                )
        
        # 读取用户指令提示模板
        with open(prompt_file, 'r') as f:
            user_instructions_prompt_template = f.read()

        # 读取对话指令提示模板
        with open(
            prompt_file.replace('.jinja', '-conversation-instructions.jinja')
        ) as f:
            conversation_instructions_prompt_template = f.read()

        # 设置基础域名，如果未提供则根据平台类型设置默认值
        base_domain = args.base_domain
        if base_domain is None:
            base_domain = (
                'github.com'
                if platform == ProviderType.GITHUB
                else 'gitlab.com'
                if platform == ProviderType.GITLAB
                else 'bitbucket.org'
            )

        # 设置实例变量
        self.output_dir = args.output_dir
        self.issue_type = issue_type
        self.issue_number = args.issue_number

        # 构建工作空间基础路径
        self.workspace_base = self.build_workspace_base(
            self.output_dir, self.issue_type, self.issue_number
        )

        self.max_iterations = args.max_iterations

        # 更新OpenHands配置
        self.app_config = self.update_openhands_config(
            load_openhands_config(),
            self.max_iterations,
            self.workspace_base,
            args.base_container_image,
            args.runtime_container_image,
            args.is_experimental,
        )

        # 设置Repository相关信息
        self.owner = owner
        self.repo = repo
        self.platform = platform
        self.user_instructions_prompt_template = user_instructions_prompt_template
        self.conversation_instructions_prompt_template = (
            conversation_instructions_prompt_template
        )
        self.repo_instruction = repo_instruction
        self.comment_id = args.comment_id

        # 创建Issue处理器工厂并生成处理器
        factory = IssueHandlerFactory(
            owner=self.owner,
            repo=self.repo,
            token=token,
            username=username,
            platform=self.platform,
            base_domain=base_domain,
            issue_type=self.issue_type,
            llm_config=self.app_config.get_llm_config(),
        )
        self.issue_handler = factory.create()

    @classmethod
    def update_openhands_config(
        cls,
        config: OpenHandsConfig,
        max_iterations: int,
        workspace_base: str,
        base_container_image: str | None,
        runtime_container_image: str | None,
        is_experimental: bool,
    ) -> OpenHandsConfig:
        """更新OpenHands配置
        
        Args:
            config (OpenHandsConfig): 要更新的配置对象
            max_iterations (int): 最大迭代次数
            workspace_base (str): 工作空间基础路径
            base_container_image (str | None): 基础容器镜像
            runtime_container_image (str | None): 运行时容器镜像
            is_experimental (bool): 是否为实验模式
            
        Returns:
            OpenHandsConfig: 更新后的配置对象
        """
        # 设置默认Agent类型
        config.default_agent = 'CodeActAgent'
        # 设置运行时类型为docker
        config.runtime = 'docker'
        # 设置每个任务的最大预算
        config.max_budget_per_task = 4
        # 设置最大迭代次数
        config.max_iterations = max_iterations

        # 不挂载工作空间，直接使用workspace_base
        config.workspace_base = workspace_base
        config.workspace_mount_path = workspace_base
        # 禁用github MicroAgent以避免冲突
        config.agents = {'CodeActAgent': AgentConfig(disabled_microagents=['github'])}

        # 更新沙盒配置
        cls.update_sandbox_config(
            config,
            base_container_image,
            runtime_container_image,
            is_experimental,
        )

        return config

    @classmethod
    def update_sandbox_config(
        cls,
        openhands_config: OpenHandsConfig,
        base_container_image: str | None,
        runtime_container_image: str | None,
        is_experimental: bool,
    ) -> None:
        """更新沙盒配置
        
        Args:
            openhands_config (OpenHandsConfig): OpenHands配置对象
            base_container_image (str | None): 基础容器镜像
            runtime_container_image (str | None): 运行时容器镜像
            is_experimental (bool): 是否为实验模式
            
        Raises:
            ValueError: 当同时提供运行时和基础容器镜像时抛出异常
        """
        # 检查是否同时提供了两种容器镜像
        if runtime_container_image is not None and base_container_image is not None:
            raise ValueError('Cannot provide both runtime and base container images.')

        # 如果都没有提供且不是实验模式，则使用默认的运行时镜像
        if (
            runtime_container_image is None
            and base_container_image is None
            and not is_experimental
        ):
            runtime_container_image = (
                f'ghcr.io/all-hands-ai/runtime:{openhands.__version__}-nikolaik'
            )

        # 将容器镜像值转换为字符串或None
        container_base = (
            str(base_container_image) if base_container_image is not None else None
        )
        container_runtime = (
            str(runtime_container_image)
            if runtime_container_image is not None
            else None
        )

        # 创建沙盒配置
        sandbox_config = SandboxConfig(
            base_container_image=container_base,
            runtime_container_image=container_runtime,
            enable_auto_lint=False,  # 禁用自动代码检查
            use_host_network=False,  # 不使用主机网络
            timeout=300,  # 设置超时时间为5分钟
        )

        # 为GitLab CI环境配置沙盒
        if cls.GITLAB_CI:
            sandbox_config.local_runtime_url = os.getenv(
                'LOCAL_RUNTIME_URL', 'http://localhost'
            )
            # 获取用户ID，如果是root用户则生成唯一ID
            user_id = os.getuid() if hasattr(os, 'getuid') else 1000
            if user_id == 0:
                sandbox_config.user_id = get_unique_uid()

        # 将沙盒配置应用到OpenHands配置
        openhands_config.sandbox.base_container_image = (
            sandbox_config.base_container_image
        )
        openhands_config.sandbox.runtime_container_image = (
            sandbox_config.runtime_container_image
        )
        openhands_config.sandbox.enable_auto_lint = sandbox_config.enable_auto_lint
        openhands_config.sandbox.use_host_network = sandbox_config.use_host_network
        openhands_config.sandbox.timeout = sandbox_config.timeout
        openhands_config.sandbox.local_runtime_url = sandbox_config.local_runtime_url
        openhands_config.sandbox.user_id = sandbox_config.user_id

    def initialize_runtime(
        self,
        runtime: Runtime,
    ) -> None:
        """为Agent初始化运行时环境
        
        这个函数在运行时被用来运行Agent之前调用。
        它设置git配置并运行设置脚本（如果存在）。
        
        Args:
            runtime (Runtime): 要初始化的运行时对象
            
        Raises:
            RuntimeError: 当初始化步骤失败时抛出异常
        """
        logger.info('-' * 30)
        logger.info('BEGIN Runtime Completion Fn')
        logger.info('-' * 30)
        obs: Observation

        # 切换到工作目录
        action = CmdRunAction(command='cd /workspace')
        logger.info(action, extra={'msg_type': 'ACTION'})
        obs = runtime.run_action(action)
        logger.info(obs, extra={'msg_type': 'OBSERVATION'})
        if not isinstance(obs, CmdOutputObservation) or obs.exit_code != 0:
            raise RuntimeError(f'Failed to change directory to /workspace.\n{obs}')

        # 在GitLab CI环境中修改文件权限
        if self.platform == ProviderType.GITLAB and self.GITLAB_CI:
            action = CmdRunAction(command='sudo chown -R 1001:0 /workspace/*')
            logger.info(action, extra={'msg_type': 'ACTION'})
            obs = runtime.run_action(action)
            logger.info(obs, extra={'msg_type': 'OBSERVATION'})

        # 配置git以禁用分页器
        action = CmdRunAction(command='git config --global core.pager ""')
        logger.info(action, extra={'msg_type': 'ACTION'})
        obs = runtime.run_action(action)
        logger.info(obs, extra={'msg_type': 'OBSERVATION'})
        if not isinstance(obs, CmdOutputObservation) or obs.exit_code != 0:
            raise RuntimeError(f'Failed to set git config.\n{obs}')

        # 运行设置脚本（如果存在）
        logger.info('Checking for .openhands/setup.sh script...')
        runtime.maybe_run_setup_script()

        # 设置git钩子（如果存在）
        logger.info('Checking for .openhands/pre-commit.sh script...')
        runtime.maybe_setup_git_hooks()

    async def complete_runtime(
        self,
        runtime: Runtime,
        base_commit: str,
    ) -> dict[str, Any]:
        """完成运行时处理
        
        这个函数在Agent运行完成后调用。
        如果需要在Agent运行后在沙盒中做一些事情来获得正确性度量，
        请修改这个函数。
        
        Args:
            runtime (Runtime): 运行时对象
            base_commit (str): 基础提交的哈希值
            
        Returns:
            dict[str, Any]: 包含git补丁等结果的字典
            
        Raises:
            RuntimeError: 当运行时操作失败时抛出异常
        """
        logger.info('-' * 30)
        logger.info('BEGIN Runtime Completion Fn')
        logger.info('-' * 30)
        obs: Observation

        # 切换到工作目录
        action = CmdRunAction(command='cd /workspace')
        logger.info(action, extra={'msg_type': 'ACTION'})
        obs = runtime.run_action(action)
        logger.info(obs, extra={'msg_type': 'OBSERVATION'})
        if not isinstance(obs, CmdOutputObservation) or obs.exit_code != 0:
            raise RuntimeError(
                f'Failed to change directory to /workspace. Observation: {obs}'
            )

        # 配置git分页器
        action = CmdRunAction(command='git config --global core.pager ""')
        logger.info(action, extra={'msg_type': 'ACTION'})
        obs = runtime.run_action(action)
        logger.info(obs, extra={'msg_type': 'OBSERVATION'})
        if not isinstance(obs, CmdOutputObservation) or obs.exit_code != 0:
            raise RuntimeError(f'Failed to set git config. Observation: {obs}')

        # 添加工作空间为安全目录
        action = CmdRunAction(
            command='git config --global --add safe.directory /workspace'
        )
        logger.info(action, extra={'msg_type': 'ACTION'})
        obs = runtime.run_action(action)
        logger.info(obs, extra={'msg_type': 'OBSERVATION'})
        if not isinstance(obs, CmdOutputObservation) or obs.exit_code != 0:
            raise RuntimeError(f'Failed to set git config. Observation: {obs}')

        # 添加所有更改到git索引
        if self.platform == ProviderType.GITLAB and self.GITLAB_CI:
            action = CmdRunAction(command='sudo git add -A')
        else:
            action = CmdRunAction(command='git add -A')

        logger.info(action, extra={'msg_type': 'ACTION'})
        obs = runtime.run_action(action)
        logger.info(obs, extra={'msg_type': 'OBSERVATION'})
        if not isinstance(obs, CmdOutputObservation) or obs.exit_code != 0:
            raise RuntimeError(f'Failed to git add. Observation: {obs}')

        # 生成git补丁，带重试机制
        n_retries = 0
        git_patch = None
        while n_retries < 5:
            action = CmdRunAction(command=f'git diff --no-color --cached {base_commit}')
            # 设置超时时间，每次重试增加100秒
            action.set_hard_timeout(600 + 100 * n_retries)
            logger.info(action, extra={'msg_type': 'ACTION'})
            obs = runtime.run_action(action)
            logger.info(obs, extra={'msg_type': 'OBSERVATION'})
            n_retries += 1
            
            if isinstance(obs, CmdOutputObservation):
                if obs.exit_code == 0:
                    git_patch = obs.content.strip()
                    break
                else:
                    logger.info('Failed to get git diff, retrying...')
                    await asyncio.sleep(10)
            elif isinstance(obs, ErrorObservation):
                logger.error(f'Error occurred: {obs.content}. Retrying...')
                await asyncio.sleep(10)
            else:
                raise ValueError(f'Unexpected observation type: {type(obs)}')

        logger.info('-' * 30)
        logger.info('END Runtime Completion Fn')
        logger.info('-' * 30)
        return {'git_patch': git_patch}

    @staticmethod
    def build_workspace_base(
        output_dir: str, issue_type: str, issue_number: int
    ) -> str:
        """构建工作空间基础路径
        
        Args:
            output_dir (str): 输出目录
            issue_type (str): Issue类型
            issue_number (int): Issue编号
            
        Returns:
            str: 工作空间的绝对路径
        """
        workspace_base = os.path.join(
            output_dir, 'workspace', f'{issue_type}_{issue_number}'
        )
        return os.path.abspath(workspace_base)

    async def process_issue(
        self,
        issue: Issue,
        base_commit: str,
        issue_handler: ServiceContextIssue | ServiceContextPR,
        reset_logger: bool = False,
    ) -> ResolverOutput:
        """处理单个Issue
        
        这是核心的Issue处理函数，负责整个解决流程：
        1. 设置日志
        2. 复制Repository到工作空间
        3. 初始化运行时
        4. 运行Agent
        5. 生成结果
        
        Args:
            issue (Issue): 要处理的Issue对象
            base_commit (str): 基础提交哈希
            issue_handler (ServiceContextIssue | ServiceContextPR): Issue处理器
            reset_logger (bool, optional): 是否重置日志记录器. Defaults to False.
            
        Returns:
            ResolverOutput: 处理结果
        """
        # 正确设置日志记录器，以便可以运行多进程来并行处理
        if reset_logger:
            log_dir = os.path.join(self.output_dir, 'infer_logs')
            reset_logger_for_multiprocessing(logger, str(issue.number), log_dir)
        else:
            logger.info(f'Starting fixing issue {issue.number}.')

        # 将Repository复制到工作空间
        if os.path.exists(self.workspace_base):
            shutil.rmtree(self.workspace_base)
        shutil.copytree(os.path.join(self.output_dir, 'repo'), self.workspace_base)

        # 创建并连接运行时
        runtime = create_runtime(self.app_config)
        await runtime.connect()

        # 设置事件订阅器
        def on_event(evt: Event) -> None:
            logger.info(evt)

        runtime.event_stream.subscribe(
            EventStreamSubscriber.MAIN, on_event, str(uuid4())
        )

        # 初始化运行时
        self.initialize_runtime(runtime)

        # 获取指令和对话指令
        instruction, conversation_instructions, images_urls = (
            issue_handler.get_instruction(
                issue,
                self.user_instructions_prompt_template,
                self.conversation_instructions_prompt_template,
                self.repo_instruction,
            )
        )
        
        # 运行Agent（类似于main函数）并获取最终任务状态
        action = MessageAction(content=instruction, image_urls=images_urls)
        try:
            state: State | None = await run_controller(
                config=self.app_config,
                initial_user_action=action,
                runtime=runtime,
                fake_user_response_fn=codeact_user_response,
                conversation_instructions=conversation_instructions,
            )
            if state is None:
                raise RuntimeError('Failed to run the agent.')
        except (ValueError, RuntimeError) as e:
            error_msg = f'Agent failed with error: {str(e)}'
            logger.error(error_msg)
            state = None
            last_error: str | None = error_msg

        # 获取git补丁
        return_val = await self.complete_runtime(runtime, base_commit)
        git_patch = return_val['git_patch']
        logger.info(
            f'Got git diff for instance {issue.number}:\n--------\n{git_patch}\n--------'
        )

        # 序列化历史记录并为失败状态设置默认值
        if state is None:
            histories = []
            metrics = None
            success = False
            comment_success = None
            result_explanation = 'Agent failed to run'
            last_error = 'Agent failed to run or crashed'
        else:
            # 将事件历史转换为字典格式
            histories = [dataclasses.asdict(event) for event in state.history]
            metrics = state.metrics.get() if state.metrics else None
            # 根据历史记录、Issue描述和git补丁确定成功状态
            success, comment_success, result_explanation = issue_handler.guess_success(
                issue, state.history, git_patch
            )

            # 对于PR类型且评论成功的情况，生成详细的成功日志
            if issue_handler.issue_type == 'pr' and comment_success:
                success_log = 'I have updated the PR and resolved some of the issues that were cited in the pull request review. Specifically, I identified the following revision requests, and all the ones that I think I successfully resolved are checked off. All the unchecked ones I was not able to resolve, so manual intervention may be required:\n'
                try:
                    explanations = json.loads(result_explanation)
                except json.JSONDecodeError:
                    logger.error(
                        f'Failed to parse result_explanation as JSON: {result_explanation}'
                    )
                    explanations = [
                        str(result_explanation)
                    ]  # 使用原始字符串作为后备

                # 为每个成功指示器和解释生成格式化的日志
                for success_indicator, explanation in zip(
                    comment_success, explanations
                ):
                    status = (
                        colored('[X]', 'red')
                        if success_indicator
                        else colored('[ ]', 'red')
                    )
                    bullet_point = colored('-', 'yellow')
                    success_log += f'\n{bullet_point} {status}: {explanation}'
                logger.info(success_log)
            last_error = state.last_error if state.last_error else None

        # 保存输出结果
        output = ResolverOutput(
            issue=issue,
            issue_type=issue_handler.issue_type,
            instruction=instruction,
            base_commit=base_commit,
            git_patch=git_patch,
            history=histories,
            metrics=metrics,
            success=success,
            comment_success=comment_success,
            result_explanation=result_explanation,
            error=last_error,
        )
        return output

    def extract_issue(self) -> Issue:
        """提取要处理的Issue
        
        从Issue处理器获取指定编号的Issue。
        
        Returns:
            Issue: 提取的Issue对象
            
        Raises:
            ValueError: 当找不到指定编号的Issue时抛出异常
        """
        # 加载数据集
        issues: list[Issue] = self.issue_handler.get_converted_issues(
            issue_numbers=[self.issue_number], comment_id=self.comment_id
        )

        if not issues:
            raise ValueError(
                f'No issues found for issue number {self.issue_number}. Please verify that:\n'
                f'1. The issue/PR #{self.issue_number} exists in the repository {self.owner}/{self.repo}\n'
                f'2. You have the correct permissions to access it\n'
                f'3. The repository name is spelled correctly'
            )

        return issues[0]

    async def resolve_issue(
        self,
        reset_logger: bool = False,
    ) -> None:
        """解决单个Issue
        
        这是主要的入口函数，完成整个Issue解决流程：
        1. 提取Issue信息
        2. 验证评论ID（如果提供）
        3. 检出Repository
        4. 处理Issue
        5. 保存结果
        
        Args:
            reset_logger (bool, optional): 是否为多进程重置日志记录器. Defaults to False.
            
        Raises:
            ValueError: 当评论ID无效或其他验证失败时抛出异常
            RuntimeError: 当Repository克隆失败时抛出异常
        """

        # 提取Issue信息
        issue = self.extract_issue()

        # 验证评论ID
        if self.comment_id is not None:
            if (
                self.issue_type == 'pr'
                and not issue.review_comments
                and not issue.review_threads
                and not issue.thread_comments
            ):
                raise ValueError(
                    f'Comment ID {self.comment_id} did not have a match for issue {issue.number}'
                )

            if self.issue_type == 'issue' and not issue.thread_comments:
                raise ValueError(
                    f'Comment ID {self.comment_id} did not have a match for issue {issue.number}'
                )

        # 测试元数据
        model_name = self.app_config.get_llm_config().model.split('/')[-1]

        # 创建输出目录
        pathlib.Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        pathlib.Path(os.path.join(self.output_dir, 'infer_logs')).mkdir(
            parents=True, exist_ok=True
        )
        logger.info(f'Using output directory: {self.output_dir}')

        # 检出Repository
        repo_dir = os.path.join(self.output_dir, 'repo')
        if not os.path.exists(repo_dir):
            checkout_output = subprocess.check_output(  # noqa: ASYNC101
                [
                    'git',
                    'clone',
                    self.issue_handler.get_clone_url(),
                    f'{self.output_dir}/repo',
                ]
            ).decode('utf-8')
            if 'fatal' in checkout_output:
                raise RuntimeError(f'Failed to clone repository: {checkout_output}')

        # 获取当前Repository的提交ID以确保可重现性
        base_commit = (
            subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_dir)  # noqa: ASYNC101
            .decode('utf-8')
            .strip()
        )
        logger.info(f'Base commit: {base_commit}')

        # 检查Repository指令文件
        if self.repo_instruction is None:
            # 在工作空间目录中检查.openhands_instructions文件
            openhands_instructions_path = os.path.join(
                repo_dir, '.openhands_instructions'
            )
            if os.path.exists(openhands_instructions_path):
                with open(openhands_instructions_path, 'r') as f:  # noqa: ASYNC101
                    self.repo_instruction = f.read()

        # 输出文件
        output_file = os.path.join(self.output_dir, 'output.jsonl')
        logger.info(f'Writing output to {output_file}')

        # 检查这个Issue是否已经被处理过
        if os.path.exists(output_file):
            with open(output_file, 'r') as f:  # noqa: ASYNC101
                for line in f:
                    data = ResolverOutput.model_validate_json(line)
                    if data.issue.number == self.issue_number:
                        logger.warning(
                            f'Issue {self.issue_number} was already processed. Skipping.'
                        )
                        return

        # 打开输出文件进行追加写入
        output_fp = open(output_file, 'a')  # noqa: ASYNC101

        logger.info(
            f'Resolving issue {self.issue_number} with Agent {AGENT_CLASS}, model {model_name}, max iterations {self.max_iterations}.'
        )

        try:
            # 如果需要，检出到PR分支
            if self.issue_type == 'pr':
                branch_to_use = issue.head_branch
                logger.info(
                    f'Checking out to PR branch {branch_to_use} for issue {issue.number}'
                )

                if not branch_to_use:
                    raise ValueError('Branch name cannot be None')

                # 首先获取分支以确保它在本地存在
                fetch_cmd = ['git', 'fetch', 'origin', branch_to_use]
                subprocess.check_output(  # noqa: ASYNC101
                    fetch_cmd,
                    cwd=repo_dir,
                )

                # 检出分支
                checkout_cmd = ['git', 'checkout', branch_to_use]
                subprocess.check_output(  # noqa: ASYNC101
                    checkout_cmd,
                    cwd=repo_dir,
                )

                # 更新基础提交
                base_commit = (
                    subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo_dir)  # noqa: ASYNC101
                    .decode('utf-8')
                    .strip()
                )

            # 处理Issue
            output = await self.process_issue(
                issue,
                base_commit,
                self.issue_handler,
                reset_logger,
            )
            # 将结果写入文件
            output_fp.write(output.model_dump_json() + '\n')
            output_fp.flush()

        finally:
            output_fp.close()
            logger.info('Finished.')
