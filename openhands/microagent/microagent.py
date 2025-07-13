import io
import re
from itertools import chain
from pathlib import Path
from typing import Union

import frontmatter
from pydantic import BaseModel

from openhands.core.exceptions import (
    MicroagentValidationError,
)
from openhands.core.logger import openhands_logger as logger
from openhands.microagent.types import InputMetadata, MicroagentMetadata, MicroagentType


class BaseMicroagent(BaseModel):
    """所有Microagent的基础类。
    
    这是一个抽象基类，定义了所有Microagent类型的通用属性和行为。
    Microagent是一种轻量级的智能体，用于提供特定领域的知识和任务处理能力。
    """

    name: str
    """Microagent的名称标识符"""
    
    content: str
    """Microagent的主要内容，通常包含指令、知识或任务描述"""
    
    metadata: MicroagentMetadata
    """Microagent的元数据，包含配置信息和类型定义"""
    
    source: str  # path to the file
    """源文件的路径，用于追踪Microagent的来源"""
    
    type: MicroagentType
    """Microagent的类型，决定其行为模式和激活条件"""

    @classmethod
    def load(
        cls,
        path: Union[str, Path],
        microagent_dir: Path | None = None,
        file_content: str | None = None,
    ) -> 'BaseMicroagent':
        """从带有frontmatter的markdown文件加载一个Microagent。

        Agent的名称从相对于microagent_dir的路径中派生。
        
        Args:
            path: Microagent文件的路径
            microagent_dir: Microagent目录的根路径，用于计算相对名称
            file_content: 可选的文件内容，如果提供则不从文件读取
            
        Returns:
            根据类型创建的具体Microagent实例
            
        Raises:
            MicroagentValidationError: 当Microagent验证失败时
            ValueError: 当无法确定Microagent类型时
        """
        # 确保path是Path对象
        path = Path(path) if isinstance(path, str) else path

        # 如果提供了microagent_dir，则从相对路径计算派生名称
        # 否则，我们将依赖后续从metadata中获取的名称
        derived_name = None
        if microagent_dir is not None:
            # 对.cursorrules文件进行特殊处理，这些文件不在microagent_dir中
            if path.name == '.cursorrules':
                derived_name = 'cursorrules'
            else:
                # 获取相对路径并去除文件扩展名作为名称
                derived_name = str(path.relative_to(microagent_dir).with_suffix(''))

        # 只有在没有提供file_content时才直接从路径加载
        if file_content is None:
            with open(path) as f:
                file_content = f.read()

        # 传统的Repository指令存储在.openhands_instructions文件中
        if path.name == '.openhands_instructions':
            return RepoMicroagent(
                name='repo_legacy',
                content=file_content,
                metadata=MicroagentMetadata(name='repo_legacy'),
                source=str(path),
                type=MicroagentType.REPO_KNOWLEDGE,
            )

        # 处理.cursorrules文件（cursor编辑器的配置文件）
        if path.name == '.cursorrules':
            return RepoMicroagent(
                name='cursorrules',
                content=file_content,
                metadata=MicroagentMetadata(name='cursorrules'),
                source=str(path),
                type=MicroagentType.REPO_KNOWLEDGE,
            )

        # 使用frontmatter库解析markdown文件的元数据和内容
        file_io = io.StringIO(file_content)
        loaded = frontmatter.load(file_io)
        content = loaded.content

        # 处理没有frontmatter或frontmatter为空的情况
        metadata_dict = loaded.metadata or {}

        # 确保version始终是字符串类型（YAML可能将数字版本解析为整数）
        if 'version' in metadata_dict and not isinstance(metadata_dict['version'], str):
            metadata_dict['version'] = str(metadata_dict['version'])

        try:
            # 创建元数据对象并进行验证
            metadata = MicroagentMetadata(**metadata_dict)

            # 如果存在MCP工具配置，则进行验证
            if metadata.mcp_tools:
                # 检查是否配置了SSE服务器（目前不支持）
                if metadata.mcp_tools.sse_servers:
                    logger.warning(
                        f'Microagent {metadata.name} has SSE servers. Only stdio servers are currently supported.'
                    )

                # 确保配置了stdio服务器
                if not metadata.mcp_tools.stdio_servers:
                    raise MicroagentValidationError(
                        f'Microagent {metadata.name} has MCP tools configuration but no stdio servers. '
                        'Only stdio servers are currently supported.'
                    )
        except Exception as e:
            # 为验证错误提供更详细的错误消息
            error_msg = f'Error validating microagent metadata in {path.name}: {str(e)}'
            # 检查是否是无效的type值
            if 'type' in metadata_dict and metadata_dict['type'] not in [
                t.value for t in MicroagentType
            ]:
                valid_types = ', '.join([f'"{t.value}"' for t in MicroagentType])
                error_msg += f'. Invalid "type" value: "{metadata_dict["type"]}". Valid types are: {valid_types}'
            raise MicroagentValidationError(error_msg) from e

        # 根据类型创建相应的子类映射
        subclass_map = {
            MicroagentType.KNOWLEDGE: KnowledgeMicroagent,
            MicroagentType.REPO_KNOWLEDGE: RepoMicroagent,
            MicroagentType.TASK: TaskMicroagent,
        }

        # 推断Agent类型的逻辑：
        # 1. 如果存在inputs -> TASK类型
        # 2. 如果存在triggers -> KNOWLEDGE类型
        # 3. 否则（没有triggers）-> REPO类型（始终活动）
        inferred_type: MicroagentType
        if metadata.inputs:
            inferred_type = MicroagentType.TASK
            # 如果尚未存在，为Agent名称添加触发器
            trigger = f'/{metadata.name}'
            if not metadata.triggers or trigger not in metadata.triggers:
                if not metadata.triggers:
                    metadata.triggers = [trigger]
                else:
                    metadata.triggers.append(trigger)
        elif metadata.triggers:
            inferred_type = MicroagentType.KNOWLEDGE
        else:
            # 没有触发器，默认为REPO类型
            # 这处理'type'可能缺失或被Pydantic默认设置的情况
            inferred_type = MicroagentType.REPO_KNOWLEDGE

        # 检查推断的类型是否在支持的映射中
        if inferred_type not in subclass_map:
            # 理论上在上述逻辑下不应该发生这种情况
            raise ValueError(f'Could not determine microagent type for: {path}')

        # 使用派生名称（如果可用），否则回退到metadata.name
        agent_name = derived_name if derived_name is not None else metadata.name

        # 创建并返回相应类型的Agent实例
        agent_class = subclass_map[inferred_type]
        return agent_class(
            name=agent_name,
            content=content,
            metadata=metadata,
            source=str(path),
            type=inferred_type,
        )


class KnowledgeMicroagent(BaseMicroagent):
    """Knowledge Microagent提供通过对话中的关键词触发的专业知识。

    它们帮助处理：
    - 编程语言最佳实践
    - 框架使用指南
    - 常见模式和范例
    - 工具使用方法
    
    Knowledge Microagent通过特定的触发词激活，为用户提供相关领域的专业知识。
    """

    def __init__(self, **data):
        """初始化Knowledge Microagent。
        
        Args:
            **data: 传递给父类的初始化数据
            
        Raises:
            ValueError: 当类型不是KNOWLEDGE或TASK时
        """
        super().__init__(**data)
        # 验证类型必须是KNOWLEDGE或TASK
        if self.type not in [MicroagentType.KNOWLEDGE, MicroagentType.TASK]:
            raise ValueError('KnowledgeMicroagent must have type KNOWLEDGE or TASK')

    def match_trigger(self, message: str) -> str | None:
        """在消息中匹配触发器。

        返回第一个匹配消息的触发器。
        
        Args:
            message: 要检查的消息内容
            
        Returns:
            匹配的触发器字符串，如果没有匹配则返回None
        """
        # 转换为小写进行不区分大小写的匹配
        message = message.lower()
        for trigger in self.triggers:
            if trigger.lower() in message:
                return trigger

        return None

    @property
    def triggers(self) -> list[str]:
        """获取此Microagent的触发器列表。
        
        Returns:
            触发器字符串列表
        """
        return self.metadata.triggers


class RepoMicroagent(BaseMicroagent):
    """专门用于Repository特定知识和指南的Microagent。

    RepoMicroagent从Repository中的`.openhands/microagents/repo.md`文件加载，
    包含私有的、Repository特定的指令，在使用该Repository时会自动加载。
    它们非常适合：
        - Repository特定的指南
        - 团队实践和约定
        - 项目特定的工作流程
        - 自定义文档引用
    """

    def __init__(self, **data):
        """初始化Repository Microagent。
        
        Args:
            **data: 传递给父类的初始化数据
            
        Raises:
            ValueError: 当类型不是REPO_KNOWLEDGE时
        """
        super().__init__(**data)
        # 验证类型必须是REPO_KNOWLEDGE
        if self.type != MicroagentType.REPO_KNOWLEDGE:
            raise ValueError(
                f'RepoMicroagent initialized with incorrect type: {self.type}'
            )


class TaskMicroagent(KnowledgeMicroagent):
    """TaskMicroagent是需要用户输入的特殊类型KnowledgeMicroagent。

    这些Microagent通过特殊格式触发："/{agent_name}"
    并且在继续之前会提示用户提供任何必需的输入。
    """

    def __init__(self, **data):
        """初始化Task Microagent。
        
        Args:
            **data: 传递给父类的初始化数据
            
        Raises:
            ValueError: 当类型不是TASK时
        """
        super().__init__(**data)
        # 验证类型必须是TASK
        if self.type != MicroagentType.TASK:
            raise ValueError(
                f'TaskMicroagent initialized with incorrect type: {self.type}'
            )

        # 追加提示以询问缺失的变量
        self._append_missing_variables_prompt()

    def _append_missing_variables_prompt(self) -> None:
        """追加提示以询问缺失的变量。
        
        如果Microagent需要用户输入或定义了输入字段，
        则在内容末尾添加提示文本，引导用户提供必要信息。
        """
        # 检查内容是否包含任何变量或定义了输入
        if not self.requires_user_input() and not self.metadata.inputs:
            return

        # 添加提示文本
        prompt = "\n\nIf the user didn't provide any of these variables, ask the user to provide them first before the agent can proceed with the task."
        self.content += prompt

    def extract_variables(self, content: str) -> list[str]:
        """从内容中提取变量。

        变量格式为${variable_name}。
        
        Args:
            content: 要分析的内容字符串
            
        Returns:
            找到的变量名列表
        """
        # 使用正则表达式匹配${variable_name}格式的变量
        pattern = r'\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}'
        matches = re.findall(pattern, content)
        return matches

    def requires_user_input(self) -> bool:
        """检查此Microagent是否需要用户输入。

        如果内容包含格式为${variable_name}的变量，则返回True。
        
        Returns:
            如果需要用户输入则返回True，否则返回False
        """
        # 检查内容是否包含任何变量
        variables = self.extract_variables(self.content)
        logger.debug(f'This microagent requires user input: {variables}')
        return len(variables) > 0

    @property
    def inputs(self) -> list[InputMetadata]:
        """获取此Microagent的输入配置。
        
        Returns:
            输入元数据列表
        """
        return self.metadata.inputs


def load_microagents_from_dir(
    microagent_dir: Union[str, Path],
) -> tuple[dict[str, RepoMicroagent], dict[str, KnowledgeMicroagent]]:
    """从指定目录加载所有Microagent。

    注意：传统的Repository指令不会在这里加载。

    Args:
        microagent_dir: Microagent目录的路径（例如 .openhands/microagents）

    Returns:
        包含(repo_agents, knowledge_agents)字典的元组
        - repo_agents: Repository类型的Microagent字典
        - knowledge_agents: Knowledge和Task类型的Microagent字典
        
    Raises:
        MicroagentValidationError: 当Microagent验证失败时
        ValueError: 当加载Microagent时发生其他错误
    """
    # 确保microagent_dir是Path对象
    if isinstance(microagent_dir, str):
        microagent_dir = Path(microagent_dir)

    # 初始化结果字典
    repo_agents = {}
    knowledge_agents = {}

    # 从Microagent目录加载所有Agent
    logger.debug(f'Loading agents from {microagent_dir}')
    if microagent_dir.exists():
        # 从Repository根目录收集.cursorrules文件和从Microagent目录收集.md文件
        cursorrules_files = []
        # 检查Repository根目录中是否存在.cursorrules文件
        if (microagent_dir.parent.parent / '.cursorrules').exists():
            cursorrules_files = [microagent_dir.parent.parent / '.cursorrules']

        # 递归查找所有.md文件，但排除README.md
        md_files = [f for f in microagent_dir.rglob('*.md') if f.name != 'README.md']

        # 在一个循环中处理所有文件
        for file in chain(cursorrules_files, md_files):
            try:
                # 加载Microagent
                agent = BaseMicroagent.load(file, microagent_dir)
                
                # 根据类型分类存储
                if isinstance(agent, RepoMicroagent):
                    repo_agents[agent.name] = agent
                elif isinstance(agent, KnowledgeMicroagent):
                    # KnowledgeMicroagent和TaskMicroagent都放入knowledge_agents
                    knowledge_agents[agent.name] = agent
            except MicroagentValidationError as e:
                # 对于验证错误，包含原始异常
                error_msg = f'Error loading microagent from {file}: {str(e)}'
                raise MicroagentValidationError(error_msg) from e
            except Exception as e:
                # 对于其他错误，用详细消息包装在ValueError中
                error_msg = f'Error loading microagent from {file}: {str(e)}'
                raise ValueError(error_msg) from e

    # 记录加载结果
    logger.debug(
        f'Loaded {len(repo_agents) + len(knowledge_agents)} microagents: '
        f'{[*repo_agents.keys(), *knowledge_agents.keys()]}'
    )
    return repo_agents, knowledge_agents