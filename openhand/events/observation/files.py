"""文件相关观察类模块

这个模块包含用于跟踪文件操作的观察类。
提供了文件读取、写入和编辑操作的观察结果封装。
"""

from dataclasses import dataclass
from difflib import SequenceMatcher

from openhands.core.schema import ObservationType
from openhands.events.event import FileEditSource, FileReadSource
from openhands.events.observation.observation import Observation


@dataclass
class FileReadObservation(Observation):
    """文件读取观察类
    
    这个数据类表示文件内容读取操作的结果。
    当Agent执行文件读取Action后，会生成此观察来记录读取的文件信息和内容。
    
    Attributes:
        path (str): 被读取文件的路径
        observation (str): 观察类型，固定为READ
        impl_source (FileReadSource): 文件读取的实现来源，默认为DEFAULT
    """

    path: str  # 被读取文件的完整路径
    observation: str = ObservationType.READ  # 观察类型标识
    impl_source: FileReadSource = FileReadSource.DEFAULT  # 读取操作的实现来源

    @property
    def message(self) -> str:
        """获取人类可读的文件读取操作描述消息
        
        Returns:
            str: 描述文件读取操作的格式化消息
        """
        return f'I read the file {self.path}.'

    def __str__(self) -> str:
        """获取文件读取观察的字符串表示
        
        Returns:
            str: 包含读取成功信息和文件内容的字符串
        """
        return f'[Read from {self.path} is successful.]\n{self.content}'


@dataclass
class FileWriteObservation(Observation):
    """文件写入观察类
    
    这个数据类表示文件写入操作的结果。
    当Agent执行文件写入Action后，会生成此观察来记录写入操作的结果。
    
    Attributes:
        path (str): 被写入文件的路径
        observation (str): 观察类型，固定为WRITE
    """

    path: str  # 被写入文件的完整路径
    observation: str = ObservationType.WRITE  # 观察类型标识

    @property
    def message(self) -> str:
        """获取人类可读的文件写入操作描述消息
        
        Returns:
            str: 描述文件写入操作的格式化消息
        """
        return f'I wrote to the file {self.path}.'

    def __str__(self) -> str:
        """获取文件写入观察的字符串表示
        
        Returns:
            str: 包含写入成功信息和内容的字符串
        """
        return f'[Write to {self.path} is successful.]\n{self.content}'


@dataclass
class FileEditObservation(Observation):
    """文件编辑观察类

    这个数据类表示文件编辑操作的结果。

    观察结果包含文件的新旧内容，并可以生成显示更改的差异可视化。
    差异是懒加载计算并缓存的以提高性能。

    .content属性可以是以下之一：
      - LLM模式下的Git diff格式
      - OH_ACI模式下发送给LLM的渲染消息（例如："文件 /path/to/file.txt 已使用提供的内容创建。"）
      
    Attributes:
        path (str): 被编辑文件的路径，默认为空字符串
        prev_exist (bool): 文件之前是否存在，False表示新建文件
        old_content (str | None): 文件的原始内容，新建文件时为None
        new_content (str | None): 文件的新内容，可能为None
        observation (str): 观察类型，固定为EDIT
        impl_source (FileEditSource): 文件编辑的实现来源，默认为LLM_BASED_EDIT
        diff (str | None): 新旧内容间的原始差异，在OH_ACI模式下使用
        _diff_cache (str | None): 差异可视化的缓存，在LLM模式下使用
    """

    path: str = ''  # 被编辑文件的完整路径
    prev_exist: bool = False  # 文件编辑前是否已存在
    old_content: str | None = None  # 编辑前的文件内容
    new_content: str | None = None  # 编辑后的文件内容
    observation: str = ObservationType.EDIT  # 观察类型标识
    impl_source: FileEditSource = FileEditSource.LLM_BASED_EDIT  # 编辑操作的实现来源
    diff: str | None = (
        None  # 新旧内容之间的原始差异，用于OH_ACI模式
    )
    _diff_cache: str | None = (
        None  # 差异可视化的缓存，用于LLM模式
    )

    @property
    def message(self) -> str:
        """获取人类可读的文件编辑操作描述消息
        
        Returns:
            str: 描述文件编辑操作的格式化消息
        """
        return f'I edited the file {self.path}.'

    def get_edit_groups(self, n_context_lines: int = 2) -> list[dict[str, list[str]]]:
        """获取显示新旧内容间更改的编辑组
        
        将文件的新旧内容进行比较，生成结构化的编辑组，每组包含一个连续的更改区域。
        
        Args:
            n_context_lines (int): 在每个更改周围显示的上下文行数，默认为2行
            
        Returns:
            list[dict[str, list[str]]]: 编辑组列表，每组包含before_edits和after_edits字段
        """
        # 如果没有新旧内容，返回空列表
        if self.old_content is None or self.new_content is None:
            return []
            
        # 将内容按行分割
        old_lines = self.old_content.split('\n')
        new_lines = self.new_content.split('\n')
        
        # 借用difflib.unified_diff的逻辑，直接解析为结构化格式
        edit_groups: list[dict] = []
        
        # 使用SequenceMatcher获取分组的操作码
        for group in SequenceMatcher(None, old_lines, new_lines).get_grouped_opcodes(
            n_context_lines
        ):
            # 获取组中最大行号，用于计算缩进填充大小
            _indent_pad_size = len(str(group[-1][3])) + 1  # +1 for "*" prefix
            
            # 初始化当前编辑组
            cur_group: dict[str, list[str]] = {
                'before_edits': [],  # 编辑前的内容行
                'after_edits': [],   # 编辑后的内容行
            }
            
            # 处理组中的每个操作
            for tag, i1, i2, j1, j2 in group:
                if tag == 'equal':
                    # 处理相同的行（上下文行）
                    for idx, line in enumerate(old_lines[i1:i2]):
                        line_num = i1 + idx + 1
                        cur_group['before_edits'].append(
                            f'{line_num:>{_indent_pad_size}}|{line}'
                        )
                    for idx, line in enumerate(new_lines[j1:j2]):
                        line_num = j1 + idx + 1
                        cur_group['after_edits'].append(
                            f'{line_num:>{_indent_pad_size}}|{line}'
                        )
                    continue
                    
                if tag in {'replace', 'delete'}:
                    # 处理被替换或删除的行
                    for idx, line in enumerate(old_lines[i1:i2]):
                        line_num = i1 + idx + 1
                        cur_group['before_edits'].append(
                            f'-{line_num:>{_indent_pad_size - 1}}|{line}'
                        )
                        
                if tag in {'replace', 'insert'}:
                    # 处理新增或替换后的行
                    for idx, line in enumerate(new_lines[j1:j2]):
                        line_num = j1 + idx + 1
                        cur_group['after_edits'].append(
                            f'+{line_num:>{_indent_pad_size - 1}}|{line}'
                        )
                        
            edit_groups.append(cur_group)
        return edit_groups

    def visualize_diff(
        self,
        n_context_lines: int = 2,
        change_applied: bool = True,
    ) -> str:
        """可视化文件编辑的差异，用于LLM模式
        
        不是逐行显示差异，而是将每个更改块作为单独的实体显示。
        
        Args:
            n_context_lines (int): 在更改前后显示的上下文行数，默认为2行
            change_applied (bool): 更改是否已应用。如果为False，显示为尝试的编辑
            
        Returns:
            str: 包含格式化差异可视化的字符串
        """
        # 如果有缓存的差异，直接返回
        if self._diff_cache is not None:
            return self._diff_cache

        # 检查是否有任何更改
        if change_applied and self.old_content == self.new_content:
            msg = '(no changes detected. Please make sure your edits change '
            msg += 'the content of the existing file.)\n'
            self._diff_cache = msg
            return self._diff_cache

        # 获取编辑组
        edit_groups = self.get_edit_groups(n_context_lines=n_context_lines)

        # 构建标题
        if change_applied:
            header = f'[Existing file {self.path} is edited with '
            header += f'{len(edit_groups)} changes.]'
        else:
            header = f"[Changes are NOT applied to {self.path} - Here's how "
            header += 'the file looks like if changes are applied.]'
        result = [header]

        # 构建差异内容
        op_type = 'edit' if change_applied else 'ATTEMPTED edit'
        for i, cur_edit_group in enumerate(edit_groups):
            if i != 0:
                result.append('-------------------------')  # 分隔线
            result.append(f'[begin of {op_type} {i + 1} / {len(edit_groups)}]')
            result.append(f'(content before {op_type})')
            result.extend(cur_edit_group['before_edits'])
            result.append(f'(content after {op_type})')
            result.extend(cur_edit_group['after_edits'])
            result.append(f'[end of {op_type} {i + 1} / {len(edit_groups)}]')

        # 缓存结果
        self._diff_cache = '\n'.join(result)
        return self._diff_cache

    def __str__(self) -> str:
        """获取文件编辑观察的字符串表示
        
        Returns:
            str: 根据实现来源和文件状态返回相应的字符串表示
        """
        # OH_ACI模式直接返回内容
        if self.impl_source == FileEditSource.OH_ACI:
            return self.content

        # 如果是新建文件
        if not self.prev_exist:
            assert self.old_content == '', (
                'old_content should be empty if the file is new (prev_exist=False).'
            )
            return f'[New file {self.path} is created with the provided content.]\n'

        # 使用缓存的差异或重新计算
        return self.visualize_diff().rstrip() + '\n'