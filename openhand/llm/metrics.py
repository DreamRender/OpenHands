"""
指标统计模块

该模块定义了用于跟踪和记录LLM运行过程中各种指标的类，包括成本、响应延迟、
Token使用量等。这些指标对于监控系统性能和控制预算非常重要。
"""

import copy  # 导入copy模块，用于深拷贝操作
import time  # 导入time模块，用于时间戳生成

from pydantic import BaseModel, Field  # 导入Pydantic的基础Model和字段定义


class Cost(BaseModel):
    """
    成本记录Model
    
    用于记录单次LLM调用的成本信息，包括Model名称、成本金额和时间戳。
    """
    
    model: str  # Model名称
    cost: float  # 成本金额
    timestamp: float = Field(default_factory=time.time)  # 时间戳，默认为当前时间


class ResponseLatency(BaseModel):
    """
    响应延迟指标Model
    
    用于跟踪每次completion调用的往返时间（round-trip time）。
    这个指标对于监控系统响应性能非常重要。
    """
    
    model: str  # Model名称
    latency: float  # 延迟时间（秒）
    response_id: str  # 响应ID，用于关联特定的请求


class TokenUsage(BaseModel):
    """
    Token使用量指标Model
    
    用于跟踪每次completion调用的详细Token使用情况，包括输入Token、
    输出Token、缓存读写Token等。
    """
    
    model: str = Field(default='')  # Model名称
    prompt_tokens: int = Field(default=0)  # 提示词Token数量
    completion_tokens: int = Field(default=0)  # 完成Token数量
    cache_read_tokens: int = Field(default=0)  # 缓存读取Token数量
    cache_write_tokens: int = Field(default=0)  # 缓存写入Token数量
    context_window: int = Field(default=0)  # 上下文窗口大小
    per_turn_token: int = Field(default=0)  # 每轮Token数量
    response_id: str = Field(default='')  # 响应ID

    def __add__(self, other: 'TokenUsage') -> 'TokenUsage':
        """
        将两个TokenUsage实例相加
        
        Args:
            other (TokenUsage): 另一个TokenUsage实例
            
        Returns:
            TokenUsage: 相加后的新TokenUsage实例
            
        Note:
            - context_window取两者中的最大值
            - per_turn_token使用other的值
            - response_id使用当前实例的值
        """
        return TokenUsage(
            model=self.model,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            context_window=max(self.context_window, other.context_window),  # 取最大值
            per_turn_token=other.per_turn_token,  # 使用other的值
            response_id=self.response_id,  # 保持当前实例的response_id
        )


class Metrics:
    """
    指标收集类
    
    该类可以在运行和评估期间记录各种指标。主要跟踪以下内容：
    - 累计成本和成本列表
    - 每个任务的最大预算限制
    - 响应延迟列表
    - Token使用量列表（每次调用一个记录）
    """

    def __init__(self, model_name: str = 'default') -> None:
        """
        初始化Metrics实例
        
        Args:
            model_name (str): Model名称，默认为'default'
        """
        self._accumulated_cost: float = 0.0  # 累计成本
        self._max_budget_per_task: float | None = None  # 每个任务的最大预算
        self._costs: list[Cost] = []  # 成本记录列表
        self._response_latencies: list[ResponseLatency] = []  # 响应延迟记录列表
        self.model_name = model_name  # Model名称
        self._token_usages: list[TokenUsage] = []  # Token使用量记录列表
        
        # 初始化累计Token使用量
        self._accumulated_token_usage: TokenUsage = TokenUsage(
            model=model_name,
            prompt_tokens=0,
            completion_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            context_window=0,
            response_id='',
        )

    @property
    def accumulated_cost(self) -> float:
        """
        获取累计成本
        
        Returns:
            float: 累计成本金额
        """
        return self._accumulated_cost

    @accumulated_cost.setter
    def accumulated_cost(self, value: float) -> None:
        """
        设置累计成本
        
        Args:
            value (float): 新的累计成本值
            
        Raises:
            ValueError: 当成本为负数时抛出异常
        """
        if value < 0:
            raise ValueError('总成本不能为负数。')
        self._accumulated_cost = value

    @property
    def max_budget_per_task(self) -> float | None:
        """
        获取每个任务的最大预算
        
        Returns:
            float | None: 预算限制，如果未设置则为None
        """
        return self._max_budget_per_task

    @max_budget_per_task.setter
    def max_budget_per_task(self, value: float | None) -> None:
        """
        设置每个任务的最大预算
        
        Args:
            value (float | None): 预算限制值
        """
        self._max_budget_per_task = value

    @property
    def costs(self) -> list[Cost]:
        """
        获取成本记录列表
        
        Returns:
            list[Cost]: 所有成本记录的列表
        """
        return self._costs

    @property
    def response_latencies(self) -> list[ResponseLatency]:
        """
        获取响应延迟记录列表
        
        Returns:
            list[ResponseLatency]: 所有响应延迟记录的列表
            
        Note:
            如果属性不存在（向后兼容性），会初始化为空列表
        """
        if not hasattr(self, '_response_latencies'):
            self._response_latencies = []
        return self._response_latencies

    @response_latencies.setter
    def response_latencies(self, value: list[ResponseLatency]) -> None:
        """
        设置响应延迟记录列表
        
        Args:
            value (list[ResponseLatency]): 新的响应延迟记录列表
        """
        self._response_latencies = value

    @property
    def token_usages(self) -> list[TokenUsage]:
        """
        获取Token使用量记录列表
        
        Returns:
            list[TokenUsage]: 所有Token使用量记录的列表
            
        Note:
            如果属性不存在（向后兼容性），会初始化为空列表
        """
        if not hasattr(self, '_token_usages'):
            self._token_usages = []
        return self._token_usages

    @token_usages.setter
    def token_usages(self, value: list[TokenUsage]) -> None:
        """
        设置Token使用量记录列表
        
        Args:
            value (list[TokenUsage]): 新的Token使用量记录列表
        """
        self._token_usages = value

    @property
    def accumulated_token_usage(self) -> TokenUsage:
        """
        获取累计Token使用量，如果不存在则初始化
        
        Returns:
            TokenUsage: 累计的Token使用量
        """
        if not hasattr(self, '_accumulated_token_usage'):
            self._accumulated_token_usage = TokenUsage(
                model=self.model_name,
                prompt_tokens=0,
                completion_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
                context_window=0,
                response_id='',
            )
        return self._accumulated_token_usage

    def add_cost(self, value: float) -> None:
        """
        添加成本记录
        
        Args:
            value (float): 要添加的成本值
            
        Raises:
            ValueError: 当添加的成本为负数时抛出异常
        """
        if value < 0:
            raise ValueError('添加的成本不能为负数。')
        
        # 更新累计成本
        self._accumulated_cost += value
        # 创建成本记录并添加到列表
        self._costs.append(Cost(cost=value, model=self.model_name))

    def add_response_latency(self, value: float, response_id: str) -> None:
        """
        添加响应延迟记录
        
        Args:
            value (float): 延迟时间（秒）
            response_id (str): 响应ID
            
        Note:
            延迟时间会被限制为非负数（最小值为0.0）
        """
        self._response_latencies.append(
            ResponseLatency(
                latency=max(0.0, value),  # 确保延迟时间非负
                model=self.model_name, 
                response_id=response_id
            )
        )

    def add_token_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        context_window: int,
        response_id: str,
    ) -> None:
        """
        添加单个Token使用量记录
        
        Args:
            prompt_tokens (int): 提示词Token数量
            completion_tokens (int): 完成Token数量
            cache_read_tokens (int): 缓存读取Token数量
            cache_write_tokens (int): 缓存写入Token数量
            context_window (int): 上下文窗口大小
            response_id (str): 响应ID
        """
        # 计算每轮Token数量，用于计算上下文使用量
        per_turn_token = prompt_tokens + completion_tokens

        # 创建Token使用量记录
        usage = TokenUsage(
            model=self.model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            context_window=context_window,
            per_turn_token=per_turn_token,
            response_id=response_id,
        )
        # 添加到记录列表
        self._token_usages.append(usage)

        # 使用__add__操作符更新累计Token使用量
        self._accumulated_token_usage = self.accumulated_token_usage + TokenUsage(
            model=self.model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            context_window=context_window,
            per_turn_token=per_turn_token,
            response_id='',  # 累计记录不需要特定的response_id
        )

    def merge(self, other: 'Metrics') -> None:
        """
        将其他Metrics实例合并到当前实例中
        
        Args:
            other (Metrics): 要合并的另一个Metrics实例
            
        Note:
            - 累计成本直接相加
            - 如果当前实例没有设置预算而other有，则使用other的预算
            - 所有记录列表都会合并
        """
        # 合并累计成本
        self._accumulated_cost += other.accumulated_cost

        # 如果当前实例没有设置预算限制而other有，则使用other的预算
        if self._max_budget_per_task is None and other.max_budget_per_task is not None:
            self._max_budget_per_task = other.max_budget_per_task

        # 合并各种记录列表
        self._costs += other._costs
        # 使用属性访问以确保旧的pickle对象不会崩溃
        self.token_usages += other.token_usages
        self.response_latencies += other.response_latencies

        # 使用__add__操作符合并累计Token使用量
        self._accumulated_token_usage = (
            self.accumulated_token_usage + other.accumulated_token_usage
        )

    def get(self) -> dict:
        """
        将指标以字典形式返回
        
        Returns:
            dict: 包含所有指标数据的字典
        """
        return {
            'accumulated_cost': self._accumulated_cost,
            'max_budget_per_task': self._max_budget_per_task,
            'accumulated_token_usage': self.accumulated_token_usage.model_dump(),
            'costs': [cost.model_dump() for cost in self._costs],
            'response_latencies': [
                latency.model_dump() for latency in self._response_latencies
            ],
            'token_usages': [usage.model_dump() for usage in self._token_usages],
        }

    def log(self) -> str:
        """
        记录指标信息
        
        Returns:
            str: 格式化的指标信息字符串
        """
        metrics = self.get()  # 获取指标字典
        logs = ''
        # 遍历每个指标项，格式化为字符串
        for key, value in metrics.items():
            logs += f'{key}: {value}\n'
        return logs

    def copy(self) -> 'Metrics':
        """
        创建Metrics对象的深拷贝
        
        Returns:
            Metrics: 当前Metrics对象的深拷贝
        """
        return copy.deepcopy(self)

    def diff(self, baseline: 'Metrics') -> 'Metrics':
        """
        计算当前指标与基线指标的差值
        
        这对于跟踪特定操作（如delegate）的指标非常有用。
        
        Args:
            baseline (Metrics): 表示基线状态的Metrics对象
            
        Returns:
            Metrics: 包含自基线以来差值的新Metrics对象
        """
        result = Metrics(self.model_name)

        # 计算成本差值
        result._accumulated_cost = self._accumulated_cost - baseline._accumulated_cost

        # 只包含基线之后添加的成本记录
        if baseline._costs:
            last_baseline_timestamp = baseline._costs[-1].timestamp  # 获取基线最后一个成本的时间戳
            result._costs = [
                cost for cost in self._costs if cost.timestamp > last_baseline_timestamp
            ]
        else:
            result._costs = self._costs.copy()  # 如果基线没有成本记录，复制所有成本

        # 只包含基线之后添加的响应延迟记录
        result._response_latencies = self._response_latencies[
            len(baseline._response_latencies) :
        ]

        # 只包含基线之后添加的Token使用量记录
        result._token_usages = self._token_usages[len(baseline._token_usages) :]

        # 计算累计Token使用量差值
        base_usage = baseline.accumulated_token_usage
        current_usage = self.accumulated_token_usage

        result._accumulated_token_usage = TokenUsage(
            model=self.model_name,
            prompt_tokens=current_usage.prompt_tokens - base_usage.prompt_tokens,
            completion_tokens=current_usage.completion_tokens
            - base_usage.completion_tokens,
            cache_read_tokens=current_usage.cache_read_tokens
            - base_usage.cache_read_tokens,
            cache_write_tokens=current_usage.cache_write_tokens
            - base_usage.cache_write_tokens,
            context_window=current_usage.context_window,  # 保持当前值
            per_turn_token=0,  # 差值计算中设为0
            response_id='',  # 差值记录不需要response_id
        )

        return result

    def __repr__(self) -> str:
        """
        返回Metrics对象的字符串表示
        
        Returns:
            str: 对象的字符串表示
        """
        return f'Metrics({self.get()}'