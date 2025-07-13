from pydantic import BaseModel, Field


class CLIConfig(BaseModel):
    """CLI特定设置的配置类。
    
    这个类包含与命令行界面相关的配置选项，
    用于控制CLI的行为和用户体验。
    """

    vi_mode: bool = Field(default=False)
    """是否启用vi模式。
    
    当设置为True时，CLI将使用vi风格的键绑定，
    允许用户使用vi编辑器的快捷键来编辑命令行。
    默认为False，使用标准的命令行编辑模式。
    """

    # 配置模型设置：不允许额外的字段
    model_config = {'extra': 'forbid'}