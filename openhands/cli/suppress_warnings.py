"""
在CLI模式下抑制常见警告的模块。

此模块用于减少CLI使用过程中出现的不影响功能但会干扰用户体验的警告信息。
"""

import warnings


def suppress_cli_warnings():
    """
    抑制CLI使用期间出现的常见警告。
    
    过滤掉来自依赖库的各种警告信息，包括：
    - pydub的ffmpeg/avconv警告
    - Pydantic序列化警告
    - 废弃方法调用警告
    - 其他不影响功能的依赖警告
    """

    # 抑制pydub关于ffmpeg/avconv的警告
    # 当系统没有安装ffmpeg或avconv时，pydub会发出此警告
    warnings.filterwarnings(
        'ignore',
        message="Couldn't find ffmpeg or avconv - defaulting to ffmpeg, but may not work",
        category=RuntimeWarning,
    )

    # 抑制Pydantic序列化警告
    # 过滤通用的Pydantic序列化相关警告信息
    warnings.filterwarnings(
        'ignore',
        message='.*Pydantic serializer warnings.*',
        category=UserWarning,
    )

    # 抑制特定的Pydantic序列化意外值警告
    # 处理PydanticSerializationUnexpectedValue类型的警告
    warnings.filterwarnings(
        'ignore',
        message='.*PydanticSerializationUnexpectedValue.*',
        category=UserWarning,
    )

    # 抑制CLI使用期间来自依赖项的一般废弃警告
    # 这会捕获"调用废弃方法get_events"之类的警告
    warnings.filterwarnings(
        'ignore',
        message='.*Call to deprecated method.*',
        category=DeprecationWarning,
    )

    # 抑制其他不影响功能的常见依赖警告
    # 处理字段数量不匹配等警告
    warnings.filterwarnings(
        'ignore',
        message='.*Expected .* fields but got .*',
        category=UserWarning,
    )


# 模块导入时自动应用警告抑制设置
suppress_cli_warnings()