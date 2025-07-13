from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openhands.core.config.condenser_config import CondenserConfig, NoOpCondenserConfig
from openhands.core.config.extended_config import ExtendedConfig
from openhands.core.logger import openhands_logger as logger
from openhands.utils.import_utils import get_impl


class AgentConfig(BaseModel):
    """Agent的配置类。
    
    定义了Agent运行时的各种配置选项，包括使用的LLM、工具启用状态、
    系统提示模板等设置。这些配置控制Agent的行为和能力。
    """
    
    llm_config: str | None = Field(default=None)
    """要使用的LLM配置名称。如果指定，这将覆盖全局LLM配置"""
    
    classpath: str | None = Field(default=None)
    """要使用的Agent的类路径。用于不在openhands.agenthub包中定义的自定义Agent"""
    
    system_prompt_filename: str = Field(default='system_prompt.j2')
    """Agent提示目录中系统提示模板文件的文件名。默认为'system_prompt.j2'"""
    
    enable_browsing: bool = Field(default=True)
    """是否启用浏览器工具。
    注意：如果使用CLIRuntime，浏览器功能未实现，应该禁用"""
    
    enable_llm_editor: bool = Field(default=False)
    """是否启用LLM编辑器工具"""
    
    enable_editor: bool = Field(default=True)
    """是否启用标准编辑器工具（str_replace_editor），仅在enable_llm_editor为False时生效"""
    
    enable_jupyter: bool = Field(default=True)
    """是否启用Jupyter工具。
    注意：如果使用CLIRuntime，Jupyter使用未实现，应该禁用"""
    
    enable_cmd: bool = Field(default=True)
    """是否启用bash命令工具"""
    
    enable_think: bool = Field(default=True)
    """是否启用思考工具，用于Agent内部推理"""
    
    enable_finish: bool = Field(default=True)
    """是否启用完成工具，用于标记任务完成"""
    
    enable_condensation_request: bool = Field(default=False)
    """是否启用压缩请求工具，用于历史压缩"""
    
    enable_prompt_extensions: bool = Field(default=True)
    """是否启用提示扩展功能"""
    
    enable_mcp: bool = Field(default=True)
    """是否启用MCP工具"""
    
    disabled_microagents: list[str] = Field(default_factory=list)
    """要禁用的MicroAgent列表（按名称，不含.py扩展名，例如["github", "lint"]）。默认为None"""
    
    enable_history_truncation: bool = Field(default=True)
    """当达到LLM上下文长度限制时，是否应该截断历史以继续Session"""
    
    enable_som_visual_browsing: bool = Field(default=True)
    """是否启用SoM（Set of Marks）视觉浏览"""
    
    condenser: CondenserConfig = Field(
        default_factory=lambda: NoOpCondenserConfig(type='noop')
    )
    """历史压缩器配置，默认使用空操作压缩器"""
    
    extended: ExtendedConfig = Field(default_factory=lambda: ExtendedConfig({}))
    """Agent的扩展配置，允许自定义配置项"""

    # 配置模型不允许额外字段
    model_config = ConfigDict(extra='forbid')

    @classmethod
    def from_toml_section(cls, data: dict) -> dict[str, AgentConfig]:
        """从表示[agent]部分的toml字典创建AgentConfig实例的映射。

        默认配置是从data中的所有非字典键构建的。
        然后，每个具有字典值的键被视为自定义Agent配置，其值覆盖默认配置。

        Example:
            应用通用Agent配置与自定义Agent覆盖，例如：
            [agent]
            enable_prompt_extensions = false
            [agent.BrowsingAgent]
            enable_prompt_extensions = true
            结果是BrowsingAgent的prompt_extensions为true，而其他Agent为false。

        Args:
            data: 包含Agent配置的字典
            
        Returns:
            dict[str, AgentConfig]: 一个映射，其中键"agent"对应默认配置，
            其他键表示自定义配置
        """
        # 初始化结果映射
        agent_mapping: dict[str, AgentConfig] = {}

        # 提取基础配置数据（非字典值）
        base_data = {}
        custom_sections: dict[str, dict] = {}
        for key, value in data.items():
            if isinstance(value, dict):
                # 如果值是字典，说明是自定义Agent配置部分
                custom_sections[key] = value
            else:
                # 如果值不是字典，说明是基础配置项
                base_data[key] = value

        # 尝试创建基础配置
        try:
            base_config = cls.model_validate(base_data)
            agent_mapping['agent'] = base_config
        except ValidationError as e:
            logger.warning(f'Invalid base agent configuration: {e}. Using defaults.')
            # 如果基础配置失败，创建默认配置
            base_config = cls()
            # 仍然添加到映射中
            agent_mapping['agent'] = base_config

        # 独立处理每个自定义部分
        for name, overrides in custom_sections.items():
            try:
                # 将基础配置与覆盖配置合并
                merged = {**base_config.model_dump(), **overrides}
                if merged.get('classpath'):
                    # 如果给出了显式的classpath，尝试加载它并查找其配置模型类
                    from openhands.controller.agent import Agent

                    try:
                        agent_cls = get_impl(Agent, merged.get('classpath'))
                        custom_config = agent_cls.config_model.model_validate(merged)
                    except Exception as e:
                        logger.warning(
                            f'Failed to load custom agent class [{merged.get("classpath")}]: {e}. Using default config model.'
                        )
                        custom_config = cls.model_validate(merged)
                else:
                    # 否则，尝试按名称查找Agent类（即如果它是内置的）
                    # 如果失败，就使用默认的AgentConfig类
                    try:
                        agent_cls = Agent.get_cls(name)
                        custom_config = agent_cls.config_model.model_validate(merged)
                    except Exception:
                        # 否则，回退到默认配置模型
                        custom_config = cls.model_validate(merged)
                agent_mapping[name] = custom_config
            except ValidationError as e:
                logger.warning(
                    f'Invalid agent configuration for [{name}]: {e}. This section will be skipped.'
                )
                # 跳过此自定义部分但继续处理其他部分
                continue

        return agent_mapping