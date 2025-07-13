from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

# 定义泛型类型变量T，用于表示控制标志的数值类型
# 可以是int类型（用于迭代次数）或float类型（用于预算金额）
T = TypeVar(
    'T', int, float
)  # Type for the value (int for iterations, float for budget)


@dataclass
class ControlFlag(Generic[T]):
    """控制标志的基类，用于管理限制和状态转换。
    
    这是一个泛型基类，用于实现各种类型的控制标志，如迭代次数控制、预算控制等。
    提供了统一的接口来管理限制值、当前值、最大值等状态信息。
    
    Attributes:
        limit_increase_amount (T): 每次增加限制时的增量值
        current_value (T): 当前使用的值
        max_value (T): 允许的最大值
        headless_mode (bool): 是否处于无头模式（默认False）
        _hit_limit (bool): 内部标志，表示是否已达到限制（默认False）
    """

    limit_increase_amount: T  # 限制增加的数量
    current_value: T          # 当前数值
    max_value: T             # 最大允许值
    headless_mode: bool = False    # 是否为无头模式
    _hit_limit: bool = False       # 是否已达到限制的内部标志

    def reached_limit(self) -> bool:
        """检查是否已达到限制。
        
        这是一个抽象方法，需要在子类中实现具体的限制检查逻辑。
        
        Returns:
            bool: 如果已达到限制返回True，否则返回False
        """
        raise NotImplementedError

    def increase_limit(self, headless_mode: bool) -> None:
        """在需要时扩展限制值。
        
        这是一个抽象方法，需要在子类中实现具体的限制扩展逻辑。
        通常在达到当前限制时调用，用于动态增加允许的最大值。
        
        Args:
            headless_mode (bool): 是否处于无头模式
        """
        raise NotImplementedError

    def step(self):
        """根据当前状态和模式确定下一个状态。
        
        这是一个抽象方法，需要在子类中实现具体的状态转换逻辑。
        通常在每次迭代或操作时调用，用于更新状态和检查限制。
        
        Returns:
            ControlFlagState: 下一个状态
        """
        raise NotImplementedError


@dataclass
class IterationControlFlag(ControlFlag[int]):
    """用于管理迭代次数限制的控制标志。
    
    继承自ControlFlag，专门用于控制Agent的迭代次数。
    当迭代次数达到最大值时，可以根据模式决定是否允许继续执行。
    """

    def reached_limit(self) -> bool:
        """检查是否已达到迭代次数限制。
        
        比较当前迭代次数与最大允许迭代次数，并更新内部限制标志。
        
        Returns:
            bool: 如果已达到迭代限制返回True，否则返回False
        """
        # 检查当前值是否大于等于最大值
        self._hit_limit = self.current_value >= self.max_value
        return self._hit_limit

    def increase_limit(self, headless_mode: bool) -> None:
        """通过添加初始值来扩展迭代限制。
        
        当不处于无头模式且已达到限制时，增加最大迭代次数。
        这允许用户在需要时动态扩展Agent的执行次数。
        
        Args:
            headless_mode (bool): 是否处于无头模式
        """
        # 只有在非无头模式且已达到限制时才增加限制
        if not headless_mode and self._hit_limit:
            # 将最大值增加指定的增量
            self.max_value += self.limit_increase_amount
            # 重置限制标志
            self._hit_limit = False

    def step(self):
        """执行一步迭代控制。
        
        首先检查是否已达到限制，如果是则抛出运行时错误。
        否则将当前迭代次数加1。
        
        Raises:
            RuntimeError: 当达到最大迭代次数时抛出异常
        """
        # 检查是否达到限制
        if self.reached_limit():
            # 如果达到限制，抛出运行时错误
            raise RuntimeError(
                f'Agent reached maximum iteration. '
                f'Current iteration: {self.current_value}, max iteration: {self.max_value}'
            )

        # 将当前值递增1
        self.current_value += 1


@dataclass
class BudgetControlFlag(ControlFlag[float]):
    """用于管理预算限制的控制标志。
    
    继承自ControlFlag，专门用于控制Agent的预算消耗。
    当预算达到最大值时，可以根据需要动态增加预算限制。
    """

    def reached_limit(self) -> bool:
        """检查是否已达到预算限制。
        
        比较当前预算消耗与最大允许预算，并更新内部限制标志。
        
        Returns:
            bool: 如果已达到预算限制返回True，否则返回False
        """
        # 检查当前预算是否大于等于最大预算
        self._hit_limit = self.current_value >= self.max_value
        return self._hit_limit

    def increase_limit(self, headless_mode) -> None:
        """通过将初始值添加到当前值来扩展预算限制。
        
        当已达到预算限制时，将最大预算设置为当前消耗加上增量。
        这允许在预算不足时动态扩展可用预算。
        
        Args:
            headless_mode: 无头模式标志（此实现中未使用）
        """
        # 如果已达到限制
        if self._hit_limit:
            # 将最大值设置为当前值加上增量
            self.max_value = self.current_value + self.limit_increase_amount
            # 重置限制标志
            self._hit_limit = False

    def step(self):
        """检查是否已达到限制并相应更新状态。
        
        注意：与IterationControlFlag不同，此方法不会递增值，
        因为预算是通过外部更新的（通常由LLM使用情况更新）。
        
        Raises:
            RuntimeError: 当达到最大预算时抛出异常
        """
        # 检查是否达到预算限制
        if self.reached_limit():
            # 格式化当前预算和最大预算为两位小数
            current_str = f'{self.current_value:.2f}'
            max_str = f'{self.max_value:.2f}'
            # 抛出预算超限异常
            raise RuntimeError(
                f'Agent reached maximum budget for conversation.'
                f'Current budget: {current_str}, max budget: {max_str}'
            )
