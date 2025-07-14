import os
import re
import tempfile
from abc import ABC, abstractmethod
from typing import Any

from openhands_aci.utils.diff import get_diff  # type: ignore

from openhands.core.config import OpenHandsConfig
from openhands.core.logger import openhands_logger as logger
from openhands.events.action import (
    FileEditAction,
    FileReadAction,
    FileWriteAction,
    IPythonRunCellAction,
)
from openhands.events.observation import (
    ErrorObservation,
    FileEditObservation,
    FileReadObservation,
    FileWriteObservation,
    Observation,
)
from openhands.linter import DefaultLinter
from openhands.llm.llm import LLM
from openhands.llm.metrics import Metrics
from openhands.utils.chunk_localizer import Chunk, get_top_k_chunk_matches

# 用户消息模板，用于指导LLM如何应用代码变更
USER_MSG = """
Code changes will be provided in the form of a draft. You will need to apply the draft to the original code.
The original code will be enclosed within `<original_code>` tags.
The draft will be enclosed within `<update_snippet>` tags.
You need to output the update code within `<updated_code>` tags.

Within the `<updated_code>` tag, include only the final code after updation. Do not include any explanations or other content within these tags.

<original_code>{old_contents}</original_code>

<update_snippet>{draft_changes}</update_snippet>
    """
# 翻译：代码更改将以草稿形式提供。您需要将草稿应用到原始代码中。
# 原始代码将包含在`<original_code>`标签内。
# 草稿将包含在`<update_snippet>`标签内。
# 您需要在`<updated_code>`标签内输出更新后的代码。
# 在`<updated_code>`标签内，只包含更新后的最终代码。不要在这些标签内包含任何解释或其他内容。

# 错误修正系统消息
CORRECT_SYS_MSG = """You are a code repair assistant. Now you have an original file content and error information from a static code checking tool (lint tool). Your task is to automatically modify and return the repaired complete code based on these error messages and refer to the current file content.

The following are the specific task steps you need to complete:

Carefully read the current file content to ensure that you fully understand its code structure.

According to the lint error prompt, accurately locate and analyze the cause of the problem.

Modify the original file content and fix all errors prompted by the lint tool.

Return complete, runnable, and error-fixed code, paying attention to maintaining the overall style and specifications of the original code.

Please note:

Please strictly follow the lint error prompts to make modifications and do not miss any problems.

The modified code must be complete and cannot introduce new errors or bugs.

The modified code must maintain the original code function and logic, and no changes unrelated to error repair should be made."""
# 翻译：您是一个代码修复助手。现在您有原始文件内容和来自静态代码检查工具（lint工具）的错误信息。您的任务是基于这些错误消息自动修改并返回修复完整的代码，参考当前文件内容。
# 您需要完成的具体任务步骤如下：
# 仔细阅读当前文件内容，确保完全理解其代码结构。
# 根据lint错误提示，准确定位和分析问题原因。
# 修改原始文件内容，修复lint工具提示的所有错误。
# 返回完整、可运行且错误修复的代码，注意保持原始代码的整体风格和规范。
# 请注意：
# 请严格按照lint错误提示进行修改，不要遗漏任何问题。
# 修改后的代码必须是完整的，不能引入新的错误或bug。
# 修改后的代码必须保持原始代码的功能和逻辑，不得进行与错误修复无关的更改。

# 错误修正用户消息模板
CORRECT_USER_MSG = """
THE FOLLOWING ARE THE ORIGINAL FILE CONTENTS AND THE ERROR INFORMATION REPORTED BY THE LINT TOOL

# CURRENT FILE CONTENT:
```
{file_content}
```

# ERROR MESSAGE FROM STATIC CODE CHECKING TOOL:
```
{lint_error}
```
""".strip()
# 翻译：以下是原始文件内容和lint工具报告的错误信息
# 当前文件内容：
# 静态代码检查工具的错误消息：


def _extract_code(string: str) -> str | None:
    """
    从字符串中提取<updated_code>标签内的代码。
    
    Args:
        string (str): 包含代码的字符串
        
    Returns:
        str | None: 提取的代码内容，如果未找到则返回None
    """
    pattern = r'<updated_code>(.*?)</updated_code>'
    matches = re.findall(pattern, string, re.DOTALL)
    if not matches:
        return None

    content = str(matches[0])
    # 如果内容以#EDIT:开头，移除第一行
    if content.startswith('#EDIT:'):
        content = content[content.find('\n') + 1 :]
    return content


def get_new_file_contents(
    llm: LLM, old_contents: str, draft_changes: str, num_retries: int = 3
) -> str | None:
    """
    使用LLM获取新的文件内容。
    
    Args:
        llm (LLM): 语言模型对象
        old_contents (str): 原始文件内容
        draft_changes (str): 草稿变更内容
        num_retries (int): 重试次数，默认为3
        
    Returns:
        str | None: 新的文件内容，如果失败则返回None
    """
    while num_retries > 0:
        # 构建消息
        messages = [
            {
                'role': 'user',
                'content': USER_MSG.format(
                    old_contents=old_contents, draft_changes=draft_changes
                ),
            },
        ]
        # 调用LLM
        resp = llm.completion(messages=messages)
        new_contents = _extract_code(resp['choices'][0]['message']['content'])
        if new_contents is not None:
            return new_contents
        num_retries -= 1
    return None


class FileEditRuntimeInterface(ABC):
    """
    文件编辑运行时接口抽象基类。
    
    定义了文件编辑运行时必须实现的基本接口。
    """
    
    config: OpenHandsConfig

    @abstractmethod
    def read(self, action: FileReadAction) -> Observation:
        """
        读取文件的抽象方法。
        
        Args:
            action (FileReadAction): 文件读取Action
            
        Returns:
            Observation: 观察对象
        """
        pass

    @abstractmethod
    def write(self, action: FileWriteAction) -> Observation:
        """
        写入文件的抽象方法。
        
        Args:
            action (FileWriteAction): 文件写入Action
            
        Returns:
            Observation: 观察对象
        """
        pass

    @abstractmethod
    def run_ipython(self, action: IPythonRunCellAction) -> Observation:
        """
        运行IPython代码的抽象方法。
        
        Args:
            action (IPythonRunCellAction): IPython运行Action
            
        Returns:
            Observation: 观察对象
        """
        pass


class FileEditRuntimeMixin(FileEditRuntimeInterface):
    """
    文件编辑运行时混合类。
    
    提供基于LLM的文件编辑功能实现。
    """
    
    # 大多数LLM的输出token限制为4k tokens。
    # 这限制了我们可以编辑的行数以避免超过token限制。
    MAX_LINES_TO_EDIT = 300

    def __init__(self, enable_llm_editor: bool, *args: Any, **kwargs: Any) -> None:
        """
        初始化文件编辑运行时混合类。
        
        Args:
            enable_llm_editor (bool): 是否启用LLM编辑器
            *args: 可变位置参数
            **kwargs: 可变关键字参数
        """
        super().__init__(*args, **kwargs)
        self.enable_llm_editor = enable_llm_editor

        if not self.enable_llm_editor:
            return

        # 获取草稿编辑器配置
        draft_editor_config = self.config.get_llm_config('draft_editor')

        # 手动设置草稿编辑器LLM的模型名称以区分token成本
        llm_metrics = Metrics(model_name='draft_editor:' + draft_editor_config.model)
        if draft_editor_config.caching_prompt:
            logger.debug(
                'It is not recommended to cache draft editor LLM prompts as it may incur high costs for the same prompt. '
                'Automatically setting caching_prompt=false.'
            )
            # 翻译：不建议缓存草稿编辑器LLM提示，因为相同提示可能产生高昂成本。自动设置caching_prompt=false。
            draft_editor_config.caching_prompt = False

        # 初始化草稿编辑器LLM
        self.draft_editor_llm = LLM(draft_editor_config, metrics=llm_metrics)
        logger.debug(
            f'[Draft edit functionality] enabled with LLM: {self.draft_editor_llm}'
        )
        # 翻译：[草稿编辑功能] 已启用LLM：{self.draft_editor_llm}

    def _validate_range(
        self, start: int, end: int, total_lines: int
    ) -> Observation | None:
        """
        验证编辑范围是否有效。
        
        Args:
            start (int): 开始行号（1-indexed）
            end (int): 结束行号（1-indexed）
            total_lines (int): 总行数
            
        Returns:
            Observation | None: 如果范围无效则返回ErrorObservation，否则返回None
        """
        # start和end是1-indexed且包含的
        if (
            (start < 1 and start != -1)
            or start > total_lines
            or (start > end and end != -1 and start != -1)
        ):
            return ErrorObservation(
                f'Invalid range for editing: start={start}, end={end}, total lines={total_lines}. start must be >= 1 and <={total_lines} (total lines of the edited file), start <= end, or start == -1 (append to the end of the file).'
            )
            # 翻译：编辑范围无效：start={start}, end={end}, total lines={total_lines}。start必须>=1且<={total_lines}（编辑文件的总行数），start <= end，或start == -1（追加到文件末尾）。
        if (
            (end < 1 and end != -1)
            or end > total_lines
            or (end < start and start != -1 and end != -1)
        ):
            return ErrorObservation(
                f'Invalid range for editing: start={start}, end={end}, total lines={total_lines}. end must be >= 1 and <= {total_lines} (total lines of the edited file), end >= start, or end == -1 (to edit till the end of the file).'
            )
            # 翻译：编辑范围无效：start={start}, end={end}, total lines={total_lines}。end必须>=1且<={total_lines}（编辑文件的总行数），end >= start，或end == -1（编辑到文件末尾）。
        return None

    def _get_lint_error(
        self,
        suffix: str,
        old_content: str,
        new_content: str,
        filepath: str,
        diff: str,
    ) -> ErrorObservation | None:
        """
        获取lint错误信息。
        
        Args:
            suffix (str): 文件后缀名
            old_content (str): 原始文件内容
            new_content (str): 新文件内容
            filepath (str): 文件路径
            diff (str): 差异字符串
            
        Returns:
            ErrorObservation | None: 如果有lint错误则返回ErrorObservation，否则返回None
        """
        linter = DefaultLinter()
        # 将原始文件复制到临时文件（具有相同扩展名）并进行lint检查
        with (
            tempfile.NamedTemporaryFile(
                suffix=suffix, mode='w+', encoding='utf-8'
            ) as original_file_copy,
            tempfile.NamedTemporaryFile(
                suffix=suffix, mode='w+', encoding='utf-8'
            ) as updated_file_copy,
        ):
            # 检查原始文件
            original_file_copy.write(old_content)
            original_file_copy.flush()

            # 检查更新后的文件
            updated_file_copy.write(new_content)
            updated_file_copy.flush()

            # 获取lint错误
            updated_lint_error = linter.lint_file_diff(
                original_file_copy.name, updated_file_copy.name
            )

            if len(updated_lint_error) > 0:
                # 构建观察对象
                _obs = FileEditObservation(
                    content=diff,
                    path=filepath,
                    prev_exist=True,
                    old_content=old_content,
                    new_content=new_content,
                )
                # 构建错误消息
                error_message = (
                    (
                        f'\n[Linting failed for edited file {filepath}. {len(updated_lint_error)} lint errors found.]\n'
                        '[begin attempted changes]\n'
                        f'{_obs.visualize_diff(change_applied=False)}\n'
                        '[end attempted changes]\n'
                    )
                    + '-' * 40
                    + '\n'
                )
                # 翻译：[编辑文件{filepath}的lint检查失败。发现{len(updated_lint_error)}个lint错误。]
                # [开始尝试的更改]
                # [结束尝试的更改]
                
                error_message += '-' * 20 + 'First 5 lint errors' + '-' * 20 + '\n'
                # 翻译：前5个lint错误
                
                for i, lint_error in enumerate(updated_lint_error[:5]):
                    error_message += f'[begin lint error {i}]\n'
                    error_message += lint_error.visualize().strip() + '\n'
                    error_message += f'[end lint error {i}]\n'
                    error_message += '-' * 40 + '\n'
                    # 翻译：[开始lint错误{i}] [结束lint错误{i}]
                return ErrorObservation(error_message)
        return None

    def llm_based_edit(self, action: FileEditAction, retry_num: int = 0) -> Observation:
        """
        基于LLM的文件编辑。
        
        Args:
            action (FileEditAction): 文件编辑Action
            retry_num (int): 重试次数，默认为0
            
        Returns:
            Observation: 观察对象
        """
        # 首先尝试读取文件
        obs = self.read(FileReadAction(path=action.path))
        if (
            isinstance(obs, ErrorObservation)
            and 'File not found'.lower() in obs.content.lower()
        ):
            logger.debug(
                f'Agent attempted to edit a file that does not exist. Creating the file. Error msg: {obs.content}'
            )
            # 翻译：Agent尝试编辑不存在的文件。创建文件。错误消息：{obs.content}
            
            # 直接写入新内容
            obs = self.write(
                FileWriteAction(path=action.path, content=action.content.strip())
            )
            if isinstance(obs, ErrorObservation):
                return obs
            if not isinstance(obs, FileWriteObservation):
                raise ValueError(
                    f'Expected FileWriteObservation, got {type(obs)}: {str(obs)}'
                )
            return FileEditObservation(
                content=get_diff('', action.content, action.path),
                path=action.path,
                prev_exist=False,
                old_content='',
                new_content=action.content,
            )
        if not isinstance(obs, FileReadObservation):
            raise ValueError(
                f'Expected FileReadObservation, got {type(obs)}: {str(obs)}'
            )

        # 获取原始文件内容
        original_file_content = obs.content
        old_file_lines = original_file_content.split('\n')
        
        # 注意：start和end是1-indexed
        start = action.start
        end = action.end
        
        # 验证范围
        error = self._validate_range(start, end, len(old_file_lines))
        if error is not None:
            return error

        # 追加到文件末尾
        if start == -1:
            updated_content = '\n'.join(old_file_lines + action.content.split('\n'))
            diff = get_diff(original_file_content, updated_content, action.path)
            
            # 检查更新后的内容
            if self.config.sandbox.enable_auto_lint:
                suffix = os.path.splitext(action.path)[1]

                error_obs = self._get_lint_error(
                    suffix,
                    original_file_content,
                    updated_content,
                    action.path,
                    diff,
                )
                if error_obs is not None:
                    self.write(
                        FileWriteAction(path=action.path, content=updated_content)
                    )
                    return self.correct_edit(
                        file_content=updated_content,
                        error_obs=error_obs,
                        retry_num=retry_num,
                    )

            obs = self.write(FileWriteAction(path=action.path, content=updated_content))
            return FileEditObservation(
                content=diff,
                path=action.path,
                prev_exist=True,
                old_content=original_file_content,
                new_content=updated_content,
            )

        # 获取0-indexed的start和end
        start_idx = start - 1
        if end != -1:
            # 减去1使其变为0-indexed
            # 然后加1因为`end`是包含的
            end_idx = end - 1 + 1
        else:
            # end == -1表示用户想要编辑到文件末尾
            end_idx = len(old_file_lines)

        # 获取要编辑的行范围 - 如果太长则拒绝
        length_of_range = end_idx - start_idx
        if length_of_range > self.MAX_LINES_TO_EDIT + 1:
            error_msg = (
                f'[Edit error: The range of lines to edit is too long.]\n'
                f'[The maximum number of lines allowed to edit at once is {self.MAX_LINES_TO_EDIT}. '
                f'Got (L{start_idx + 1}-L{end_idx}) {length_of_range} lines.]\n'
                # [start_idx, end_idx)，所以不需要+1
            )
            # 翻译：[编辑错误：要编辑的行范围太长。]
            # [一次允许编辑的最大行数是{self.MAX_LINES_TO_EDIT}。得到了(L{start_idx + 1}-L{end_idx}){length_of_range}行。]
            
            # 搜索相关范围以提示Agent
            topk_chunks: list[Chunk] = get_top_k_chunk_matches(
                text=original_file_content,
                query=action.content,  # 编辑草稿作为查询
                k=3,
                max_chunk_size=20,  # 行数
            )
            error_msg += (
                'Here are some snippets that maybe relevant to the provided edit.\n'
            )
            # 翻译：这里是一些可能与提供的编辑相关的片段。
            
            for i, chunk in enumerate(topk_chunks):
                error_msg += f'[begin relevant snippet {i + 1}. Line range: L{chunk.line_range[0]}-L{chunk.line_range[1]}. Similarity: {chunk.normalized_lcs}]\n'
                error_msg += f'[Browse around it via `open_file("{action.path}", {(chunk.line_range[0] + chunk.line_range[1]) // 2})`]\n'
                error_msg += chunk.visualize() + '\n'
                error_msg += f'[end relevant snippet {i + 1}]\n'
                error_msg += '-' * 40 + '\n'
                # 翻译：[开始相关片段{i + 1}。行范围：L{chunk.line_range[0]}-L{chunk.line_range[1]}。相似度：{chunk.normalized_lcs}]
                # [通过`open_file("{action.path}", {(chunk.line_range[0] + chunk.line_range[1]) // 2})`浏览它]
                # [结束相关片段{i + 1}]

            error_msg += 'Consider using `open_file` to explore around the relevant snippets if needed.\n'
            error_msg += f'**IMPORTANT**: Please REDUCE the range of edits to less than {self.MAX_LINES_TO_EDIT} lines by setting `start` and `end` in the edit action (e.g. `<file_edit path="{action.path}" start=[PUT LINE NUMBER HERE] end=[PUT LINE NUMBER HERE] />`). '
            # 翻译：如果需要，考虑使用`open_file`来浏览相关片段。
            # **重要**：请通过在编辑操作中设置`start`和`end`将编辑范围减少到少于{self.MAX_LINES_TO_EDIT}行

            return ErrorObservation(error_msg)

        # 获取要编辑的内容
        content_to_edit = '\n'.join(old_file_lines[start_idx:end_idx])
        _edited_content = get_new_file_contents(
            self.draft_editor_llm, content_to_edit, action.content
        )
        if _edited_content is None:
            ret_err = ErrorObservation(
                'Failed to get new file contents. '
                'Please try to reduce the number of edits and try again.'
            )
            # 翻译：获取新文件内容失败。请尝试减少编辑数量并重试。
            ret_err.llm_metrics = self.draft_editor_llm.metrics
            return ret_err

        # 将更新后的内容与未更改的内容拼接
        updated_lines = (
            old_file_lines[:start_idx]
            + _edited_content.split('\n')
            + old_file_lines[end_idx:]
        )
        updated_content = '\n'.join(updated_lines)
        diff = get_diff(original_file_content, updated_content, action.path)

        # 检查更新后的内容
        if self.config.sandbox.enable_auto_lint:
            suffix = os.path.splitext(action.path)[1]
            error_obs = self._get_lint_error(
                suffix, original_file_content, updated_content, action.path, diff
            )
            if error_obs is not None:
                error_obs.llm_metrics = self.draft_editor_llm.metrics
                self.write(FileWriteAction(path=action.path, content=updated_content))
                return self.correct_edit(
                    file_content=updated_content,
                    error_obs=error_obs,
                    retry_num=retry_num,
                )

        # 写入文件
        obs = self.write(FileWriteAction(path=action.path, content=updated_content))
        ret_obs = FileEditObservation(
            content=diff,
            path=action.path,
            prev_exist=True,
            old_content=original_file_content,
            new_content=updated_content,
        )
        ret_obs.llm_metrics = self.draft_editor_llm.metrics
        return ret_obs

    def check_retry_num(self, retry_num):
        """
        检查重试次数是否超过限制。
        
        Args:
            retry_num (int): 当前重试次数
            
        Returns:
            bool: 如果重试次数超过限制则返回True
        """
        correct_num = self.draft_editor_llm.config.correct_num  # type: ignore[attr-defined]
        return correct_num < retry_num

    def correct_edit(
        self, file_content: str, error_obs: ErrorObservation, retry_num: int = 0
    ) -> Observation:
        """
        修正编辑错误。
        
        Args:
            file_content (str): 文件内容
            error_obs (ErrorObservation): 错误观察对象
            retry_num (int): 重试次数，默认为0
            
        Returns:
            Observation: 观察对象
        """
        import openhands.agenthub.codeact_agent.function_calling as codeact_function_calling
        from openhands.agenthub.codeact_agent.tools import LLMBasedFileEditTool
        from openhands.llm.llm_utils import check_tools

        _retry_num = retry_num + 1
        if self.check_retry_num(_retry_num):
            return error_obs
        
        # 检查工具
        tools = check_tools([LLMBasedFileEditTool], self.draft_editor_llm.config)
        # 构建消息
        messages = [
            {'role': 'system', 'content': CORRECT_SYS_MSG},
            {
                'role': 'user',
                'content': CORRECT_USER_MSG.format(
                    file_content=file_content, lint_error=error_obs.content
                ),
            },
        ]
        params: dict = {'messages': messages, 'tools': tools}
        try:
            # 调用LLM
            response = self.draft_editor_llm.completion(**params)
            actions = codeact_function_calling.response_to_actions(response)
            if len(actions) != 1:
                return error_obs
            for action in actions:
                if isinstance(action, FileEditAction):
                    return self.llm_based_edit(action, _retry_num)
        except Exception as e:
            logger.error(f'correct lint error is failed: {e}')
            # 翻译：修正lint错误失败：{e}
        return error_obs
