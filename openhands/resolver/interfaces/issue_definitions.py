import json
import os
import re
from typing import Any, ClassVar

import jinja2

from openhands.core.config import LLMConfig
from openhands.events.event import Event
from openhands.llm.llm import LLM
from openhands.resolver.interfaces.issue import (
    Issue,
    IssueHandlerInterface,
    ReviewThread,
)
from openhands.resolver.utils import extract_image_urls


class ServiceContext:
    """
    服务上下文基类
    
    提供Issue处理服务的基础功能，采用策略模式设计，可以动态切换不同的
    Issue处理策略（GitHub、GitLab、Bitbucket等）。
    
    Attributes:
        issue_type (ClassVar[str]): 类级别的Issue类型标识符
        default_git_patch (ClassVar[str]): 默认的Git补丁内容，当没有变更时显示
        _strategy (IssueHandlerInterface): 当前使用的Issue处理策略
        llm (LLM): 大语言模型实例，用于处理需要AI分析的任务
    """
    issue_type: ClassVar[str]
    default_git_patch: ClassVar[str] = 'No changes made yet'

    def __init__(self, strategy: IssueHandlerInterface, llm_config: LLMConfig | None):
        """
        初始化服务上下文
        
        Args:
            strategy: Issue处理策略实例
            llm_config: LLM配置，如果不为None则初始化LLM实例
        """
        self._strategy = strategy
        if llm_config is not None:
            self.llm = LLM(llm_config)

    def set_strategy(self, strategy: IssueHandlerInterface) -> None:
        """
        设置新的Issue处理策略
        
        Args:
            strategy: 新的Issue处理策略实例
        """
        self._strategy = strategy


class ServiceContextPR(ServiceContext):
    """
    Pull Request服务上下文
    
    专门用于处理Pull Request相关操作的服务上下文，继承自ServiceContext。
    提供Pull Request特有的功能，如成功判断、指令生成等。
    
    Attributes:
        issue_type (ClassVar[str]): 设置为'pr'，表示这是Pull Request类型的服务
    """
    issue_type: ClassVar[str] = 'pr'

    def __init__(self, strategy: IssueHandlerInterface, llm_config: LLMConfig):
        """
        初始化Pull Request服务上下文
        
        Args:
            strategy: Issue处理策略实例
            llm_config: LLM配置，Pull Request服务必须提供此配置
        """
        super().__init__(strategy, llm_config)

    def get_clone_url(self) -> str:
        """
        获取Repository的克隆URL
        
        Returns:
            包含认证信息的Git克隆URL
        """
        return self._strategy.get_clone_url()

    def download_issues(self) -> list[Any]:
        """
        下载所有Issues/Pull Requests
        
        Returns:
            Issues列表
        """
        return self._strategy.download_issues()

    def guess_success(
        self,
        issue: Issue,
        history: list[Event],
        git_patch: str | None = None,
    ) -> tuple[bool, None | list[bool], str]:
        """
        基于历史记录、Issue描述和Git补丁推测Issue是否已修复
        
        使用LLM分析不同类型的反馈（审查线程、线程评论、审查评论）来判断
        Pull Request是否成功解决了相关问题。
        
        Args:
            issue: 要检查的Issue对象
            history: Agent的操作历史记录
            git_patch: 可选的Git补丁，显示所做的更改
            
        Returns:
            包含三个元素的元组：
            - bool: 总体成功状态（所有反馈都必须为True）
            - None | list[bool]: 各个反馈项的成功状态列表，如果没有处理任何反馈则为None
            - str: 解释信息的JSON字符串或错误消息
        """
        # 获取历史记录中的最后一条消息
        last_message = history[-1].message

        # 将相关Issues转换为JSON格式的上下文
        issues_context = json.dumps(issue.closing_issues, indent=4)
        success_list = []
        explanation_list = []

        # 处理包含文件特定审查评论的Pull Requests
        if issue.review_threads:
            for review_thread in issue.review_threads:
                if issues_context and last_message:
                    success, explanation = self._check_review_thread(
                        review_thread, issues_context, last_message, git_patch
                    )
                else:
                    success, explanation = False, 'Missing context or message'
                success_list.append(success)
                explanation_list.append(explanation)
        # 处理只有线程评论的Pull Requests（没有文件特定的审查评论）
        elif issue.thread_comments:
            if issue.thread_comments and issues_context and last_message:
                success, explanation = self._check_thread_comments(
                    issue.thread_comments, issues_context, last_message, git_patch
                )
            else:
                success, explanation = (
                    False,
                    'Missing thread comments, context or message',
                )
            success_list.append(success)
            explanation_list.append(explanation)
        elif issue.review_comments:
            # 处理只有审查评论的Pull Requests（没有文件特定审查评论或线程评论）
            if issue.review_comments and issues_context and last_message:
                success, explanation = self._check_review_comments(
                    issue.review_comments, issues_context, last_message, git_patch
                )
            else:
                success, explanation = (
                    False,
                    'Missing review comments, context or message',
                )
            success_list.append(success)
            explanation_list.append(explanation)
        else:
            # 没有找到审查评论、线程评论或文件级审查评论
            return False, None, 'No feedback was found to process'

        # 返回总体成功状态（所有项都必须为True）和解释列表
        if not success_list:
            return False, None, 'No feedback was processed'
        return all(success_list), success_list, json.dumps(explanation_list)

    def get_converted_issues(
        self, issue_numbers: list[int] | None = None, comment_id: int | None = None
    ) -> list[Issue]:
        """
        获取转换后的Issues列表
        
        Args:
            issue_numbers: 要获取的Issue编号列表
            comment_id: 可选的评论ID
            
        Returns:
            转换后的Issue对象列表
        """
        return self._strategy.get_converted_issues(issue_numbers, comment_id)

    def get_instruction(
        self,
        issue: Issue,
        user_instructions_prompt_template: str,
        conversation_instructions_prompt_template: str,
        repo_instruction: str | None = None,
    ) -> tuple[str, str, list[str]]:
        """
        为Agent生成指令
        
        基于Issue信息和模板生成用户指令和对话指令，同时提取图片URL。
        
        Args:
            issue: Issue对象
            user_instructions_prompt_template: 用户指令的Jinja2模板
            conversation_instructions_prompt_template: 对话指令的Jinja2模板
            repo_instruction: 可选的Repository特定指令
            
        Returns:
            包含三个元素的元组：
            - str: 生成的用户指令
            - str: 生成的对话指令
            - list[str]: 提取的图片URL列表
        """
        # 创建Jinja2模板实例
        user_instruction_template = jinja2.Template(user_instructions_prompt_template)
        conversation_instructions_template = jinja2.Template(
            conversation_instructions_prompt_template
        )
        images = []

        # 处理相关Issues信息
        issues_str = None
        if issue.closing_issues:
            issues_str = json.dumps(issue.closing_issues, indent=4)
            images.extend(extract_image_urls(issues_str))

        # 处理审查评论
        review_comments_str = None
        if issue.review_comments:
            review_comments_str = json.dumps(issue.review_comments, indent=4)
            images.extend(extract_image_urls(review_comments_str))

        # 处理文件特定的审查评论
        review_thread_str = None
        review_thread_file_str = None
        if issue.review_threads:
            # 提取审查线程的评论内容
            review_threads = [
                review_thread.comment for review_thread in issue.review_threads
            ]
            # 提取审查线程涉及的文件
            review_thread_files = []
            for review_thread in issue.review_threads:
                review_thread_files.extend(review_thread.files)
            review_thread_str = json.dumps(review_threads, indent=4)
            review_thread_file_str = json.dumps(review_thread_files, indent=4)
            images.extend(extract_image_urls(review_thread_str))

        # 格式化线程评论（如果存在）
        thread_context = ''
        if issue.thread_comments:
            thread_context = '\n---\n'.join(issue.thread_comments)
            images.extend(extract_image_urls(thread_context))

        # 渲染用户指令模板
        user_instruction = user_instruction_template.render(
            review_comments=review_comments_str,
            review_threads=review_thread_str,
            files=review_thread_file_str,
            thread_context=thread_context,
        )

        # 渲染对话指令模板
        conversation_instructions = conversation_instructions_template.render(
            issues=issues_str, repo_instruction=repo_instruction
        )

        return user_instruction, conversation_instructions, images

    def _check_feedback_with_llm(self, prompt: str) -> tuple[bool, str]:
        """
        使用LLM检查反馈的辅助函数，并解析响应
        
        Args:
            prompt: 发送给LLM的提示词
            
        Returns:
            包含两个元素的元组：
            - bool: 是否成功
            - str: 解释信息
        """
        # 发送提示词给LLM并获取响应
        response = self.llm.completion(messages=[{'role': 'user', 'content': prompt}])

        # 提取响应内容并解析
        answer = response.choices[0].message.content.strip()
        # 使用正则表达式解析响应格式：--- success\n(true|false)\n--- explanation\n(内容)
        pattern = r'--- success\n*(true|false)\n*--- explanation*\n((?:.|\n)*)'
        match = re.search(pattern, answer)
        if match:
            return match.group(1).lower() == 'true', match.group(2).strip()
        return False, f'Failed to decode answer from LLM response: {answer}'

    def _check_review_thread(
        self,
        review_thread: ReviewThread,
        issues_context: str,
        last_message: str,
        git_patch: str | None = None,
    ) -> tuple[bool, str]:
        """
        检查审查线程的反馈是否已被解决
        
        Args:
            review_thread: 要检查的审查线程
            issues_context: Issues上下文信息
            last_message: Agent的最后一条消息
            git_patch: 可选的Git补丁内容
            
        Returns:
            包含两个元素的元组：
            - bool: 反馈是否已被解决
            - str: 解释信息
        """
        # 格式化相关文件信息
        files_context = json.dumps(review_thread.files, indent=4)

        # 读取并渲染提示词模板
        with open(
            os.path.join(
                os.path.dirname(__file__),
                '../prompts/guess_success/pr-feedback-check.jinja',
            ),
            'r',
        ) as f:
            template = jinja2.Template(f.read())

        # 渲染提示词，包含所有相关上下文信息
        prompt = template.render(
            issue_context=issues_context,
            feedback=review_thread.comment,
            files_context=files_context,
            last_message=last_message,
            git_patch=git_patch or self.default_git_patch,
        )

        return self._check_feedback_with_llm(prompt)

    def _check_thread_comments(
        self,
        thread_comments: list[str],
        issues_context: str,
        last_message: str,
        git_patch: str | None = None,
    ) -> tuple[bool, str]:
        """
        检查线程评论反馈是否已被解决
        
        Args:
            thread_comments: 线程评论列表
            issues_context: Issues上下文信息
            last_message: Agent的最后一条消息
            git_patch: 可选的Git补丁内容
            
        Returns:
            包含两个元素的元组：
            - bool: 反馈是否已被解决
            - str: 解释信息
        """
        # 将线程评论连接成一个字符串，使用"---"分隔
        thread_context = '\n---\n'.join(thread_comments)

        # 读取并渲染提示词模板
        with open(
            os.path.join(
                os.path.dirname(__file__),
                '../prompts/guess_success/pr-thread-check.jinja',
            ),
            'r',
        ) as f:
            template = jinja2.Template(f.read())

        # 渲染提示词
        prompt = template.render(
            issue_context=issues_context,
            thread_context=thread_context,
            last_message=last_message,
            git_patch=git_patch or self.default_git_patch,
        )

        return self._check_feedback_with_llm(prompt)

    def _check_review_comments(
        self,
        review_comments: list[str],
        issues_context: str,
        last_message: str,
        git_patch: str | None = None,
    ) -> tuple[bool, str]:
        """
        检查审查评论反馈是否已被解决
        
        Args:
            review_comments: 审查评论列表
            issues_context: Issues上下文信息
            last_message: Agent的最后一条消息
            git_patch: 可选的Git补丁内容
            
        Returns:
            包含两个元素的元组：
            - bool: 反馈是否已被解决
            - str: 解释信息
        """
        # 将审查评论连接成一个字符串，使用"---"分隔
        review_context = '\n---\n'.join(review_comments)

        # 读取并渲染提示词模板
        with open(
            os.path.join(
                os.path.dirname(__file__),
                '../prompts/guess_success/pr-review-check.jinja',
            ),
            'r',
        ) as f:
            template = jinja2.Template(f.read())

        # 渲染提示词
        prompt = template.render(
            issue_context=issues_context,
            review_context=review_context,
            last_message=last_message,
            git_patch=git_patch or self.default_git_patch,
        )

        return self._check_feedback_with_llm(prompt)


class ServiceContextIssue(ServiceContext):
    """
    Issue服务上下文
    
    专门用于处理常规Issue相关操作的服务上下文，继承自ServiceContext。
    提供Issue特有的功能，如创建Pull Request、分支管理等。
    
    Attributes:
        issue_type (ClassVar[str]): 设置为'issue'，表示这是Issue类型的服务
    """
    issue_type: ClassVar[str] = 'issue'

    def __init__(self, strategy: IssueHandlerInterface, llm_config: LLMConfig | None):
        """
        初始化Issue服务上下文
        
        Args:
            strategy: Issue处理策略实例
            llm_config: LLM配置，可选参数
        """
        super().__init__(strategy, llm_config)

    def get_base_url(self) -> str:
        """
        获取API基础URL
        
        Returns:
            API基础URL字符串
        """
        return self._strategy.get_base_url()

    def get_branch_url(self, branch_name: str) -> str:
        """
        获取分支URL
        
        Args:
            branch_name: 分支名称
            
        Returns:
            分支URL字符串
        """
        return self._strategy.get_branch_url(branch_name)

    def get_download_url(self) -> str:
        """
        获取下载URL
        
        Returns:
            下载URL字符串
        """
        return self._strategy.get_download_url()

    def get_clone_url(self) -> str:
        """
        获取克隆URL
        
        Returns:
            包含认证信息的Git克隆URL
        """
        return self._strategy.get_clone_url()

    def get_graphql_url(self) -> str:
        """
        获取GraphQL API URL
        
        Returns:
            GraphQL API URL字符串
        """
        return self._strategy.get_graphql_url()

    def get_headers(self) -> dict[str, str]:
        """
        获取HTTP请求头
        
        Returns:
            包含认证信息的HTTP请求头字典
        """
        return self._strategy.get_headers()

    def get_authorize_url(self) -> str:
        """
        获取授权URL
        
        Returns:
            包含认证信息的授权URL
        """
        return self._strategy.get_authorize_url()

    def get_pull_url(self, pr_number: int) -> str:
        """
        获取Pull Request URL
        
        Args:
            pr_number: Pull Request编号
            
        Returns:
            Pull Request的Web页面URL
        """
        return self._strategy.get_pull_url(pr_number)

    def get_compare_url(self, branch_name: str) -> str:
        """
        获取分支比较URL
        
        Args:
            branch_name: 要比较的分支名称
            
        Returns:
            分支比较页面URL
        """
        return self._strategy.get_compare_url(branch_name)

    def download_issues(self) -> list[Any]:
        """
        下载所有Issues
        
        Returns:
            Issues列表
        """
        return self._strategy.download_issues()

    def get_branch_name(
        self,
        base_branch_name: str,
    ) -> str:
        """
        生成唯一的分支名称
        
        Args:
            base_branch_name: 基础分支名称
            
        Returns:
            可用的唯一分支名称
        """
        return self._strategy.get_branch_name(base_branch_name)

    def branch_exists(self, branch_name: str) -> bool:
        """
        检查分支是否存在
        
        Args:
            branch_name: 要检查的分支名称
            
        Returns:
            如果分支存在返回True，否则返回False
        """
        return self._strategy.branch_exists(branch_name)

    def get_default_branch_name(self) -> str:
        """
        获取默认分支名称
        
        Returns:
            默认分支名称字符串
        """
        return self._strategy.get_default_branch_name()

    def create_pull_request(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        创建Pull Request
        
        Args:
            data: Pull Request数据字典，如果为None则使用空字典
            
        Returns:
            创建的Pull Request信息字典
        """
        if data is None:
            data = {}
        return self._strategy.create_pull_request(data)

    def request_reviewers(self, reviewer: str, pr_number: int) -> None:
        """
        请求审查者
        
        Args:
            reviewer: 审查者用户名
            pr_number: Pull Request编号
        """
        return self._strategy.request_reviewers(reviewer, pr_number)

    def reply_to_comment(self, pr_number: int, comment_id: str, reply: str) -> None:
        """
        回复评论
        
        Args:
            pr_number: Pull Request编号
            comment_id: 要回复的评论ID
            reply: 回复内容
        """
        return self._strategy.reply_to_comment(pr_number, comment_id, reply)

    def send_comment_msg(self, issue_number: int, msg: str) -> None:
        """
        发送评论消息
        
        Args:
            issue_number: Issue编号
            msg: 评论消息内容
        """
        return self._strategy.send_comment_msg(issue_number, msg)

    def get_issue_comments(
        self, issue_number: int, comment_id: int | None = None
    ) -> list[str] | None:
        """
        获取Issue评论
        
        Args:
            issue_number: Issue编号
            comment_id: 可选的特定评论ID
            
        Returns:
            评论内容列表，如果没有评论则返回None
        """
        return self._strategy.get_issue_comments(issue_number, comment_id)

    def get_instruction(
        self,
        issue: Issue,
        user_instructions_prompt_template: str,
        conversation_instructions_prompt_template: str,
        repo_instruction: str | None = None,
    ) -> tuple[str, str, list[str]]:
        """
        为Agent生成指令
        
        基于Issue信息生成用户指令和对话指令，同时提取图片URL。
        
        Args:
            issue: Issue对象
            user_instructions_prompt_template: 用户指令的Jinja2模板
            conversation_instructions_prompt_template: 对话指令的Jinja2模板
            repo_instruction: 可选的Repository特定指令
            
        Returns:
            包含三个元素的元组：
            - str: 生成的用户指令
            - str: 生成的对话指令
            - list[str]: 提取的图片URL列表
        """
        # 格式化线程评论（如果存在）
        thread_context = ''
        if issue.thread_comments:
            thread_context = '\n\nIssue Thread Comments:\n' + '\n---\n'.join(
                issue.thread_comments
            )

        # 提取图片URL
        images = []
        images.extend(extract_image_urls(issue.body))
        images.extend(extract_image_urls(thread_context))

        # 渲染用户指令模板
        user_instructions_template = jinja2.Template(user_instructions_prompt_template)
        user_instructions = user_instructions_template.render(
            body=issue.title + '\n\n' + issue.body + thread_context
        )  # Issue主体和评论

        # 渲染对话指令模板
        conversation_instructions_template = jinja2.Template(
            conversation_instructions_prompt_template
        )
        conversation_instructions = conversation_instructions_template.render(
            repo_instruction=repo_instruction,
        )

        return user_instructions, conversation_instructions, images

    def guess_success(
        self, issue: Issue, history: list[Event], git_patch: str | None = None
    ) -> tuple[bool, None | list[bool], str]:
        """
        基于历史记录和Issue描述推测Issue是否已修复
        
        使用LLM分析Agent的操作历史和Issue描述来判断问题是否已经解决。
        
        Args:
            issue: 要检查的Issue对象
            history: Agent的操作历史记录
            git_patch: 可选的Git补丁，显示所做的更改
            
        Returns:
            包含三个元素的元组：
            - bool: 是否成功修复
            - None | list[bool]: 对于Issue总是为None（与PR不同）
            - str: 解释信息
        """
        # 获取历史记录中的最后一条消息
        last_message = history[-1].message
        
        # 构建Issue上下文，包含线程评论（如果存在）
        issue_context = issue.body
        if issue.thread_comments:
            issue_context += '\n\nIssue Thread Comments:\n' + '\n---\n'.join(
                issue.thread_comments
            )

        # 读取并渲染提示词模板
        with open(
            os.path.join(
                os.path.dirname(__file__),
                '../prompts/guess_success/issue-success-check.jinja',
            ),
            'r',
        ) as f:
            template = jinja2.Template(f.read())
            
        # 渲染提示词
        prompt = template.render(
            issue_context=issue_context,
            last_message=last_message,
            git_patch=git_patch or self.default_git_patch,
        )

        # 调用LLM进行分析
        response = self.llm.completion(messages=[{'role': 'user', 'content': prompt}])

        # 解析LLM响应
        answer = response.choices[0].message.content.strip()
        # 使用正则表达式解析响应格式：--- success\n(true|false)\n--- explanation\n(内容)
        pattern = r'--- success\n*(true|false)\n*--- explanation*\n((?:.|\n)*)'
        match = re.search(pattern, answer)
        if match:
            return match.group(1).lower() == 'true', None, match.group(2)

        # 如果无法解析响应，返回失败状态
        return False, None, f'Failed to decode answer from LLM response: {answer}'

    def get_converted_issues(
        self, issue_numbers: list[int] | None = None, comment_id: int | None = None
    ) -> list[Issue]:
        """
        获取转换后的Issues列表
        
        Args:
            issue_numbers: 要获取的Issue编号列表
            comment_id: 可选的评论ID
            
        Returns:
            转换后的Issue对象列表
        """
        return self._strategy.get_converted_issues(issue_numbers, comment_id)