from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from openhands.events.event import Event


class ActionConfirmationStatus(str, Enum):
    """Action确认状态枚举类
    
    用于表示Action的确认状态，继承自str和Enum，
    可以直接作为字符串使用，同时具有枚举的类型安全性。
    """
    
    CONFIRMED = 'confirmed'  # 已确认状态
    REJECTED = 'rejected'    # 已拒绝状态
    AWAITING_CONFIRMATION = 'awaiting_confirmation'  # 等待确认状态


class ActionSecurityRisk(int, Enum):
    """Action安全风险等级枚举类
    
    用于表示Action的安全风险等级，继承自int和Enum，
    数值越大表示风险等级越高。
    """
    
    UNKNOWN = -1  # 未知风险等级
    LOW = 0       # 低风险等级
    MEDIUM = 1    # 中等风险等级
    HIGH = 2      # 高风险等级


@dataclass
class Action(Event):
    """Action基类
    
    所有Action的基类，继承自Event。Action表示Agent可以执行的操作，
    是整个系统中行为的基本单元。
    
    Attributes:
        runnable (ClassVar[bool]): 类变量，表示该Action是否可以被执行。
                                   默认为False，子类可以重写此属性。
    """
    
    runnable: ClassVar[bool] = False  # 标识Action是否可执行，默认不可执行