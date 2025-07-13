from dataclasses import dataclass

from openhands.events.event import Event


@dataclass
class Observation(Event):
    """观察基类
    
    所有观察类型的基类，继承自Event。
    观察(Observation)表示Agent执行Action后环境返回的反馈信息。
    这是Agent感知环境状态变化的主要方式。
    
    Attributes:
        content (str): 观察的具体内容，描述环境状态或操作结果
    """
    
    content: str  # 观察的具体内容信息