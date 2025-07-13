import time
from typing import Any

import httpx


class InvariantClient:
    """Invariant服务的客户端类
    
    该类提供了与Invariant安全分析服务器通信的接口，包括Session管理、
    Policy管理和Monitor管理功能。通过HTTP API与远程Invariant服务器交互。
    """
    
    timeout: int = 120  # 连接超时时间（秒）

    def __init__(self, server_url: str, session_id: str | None = None) -> None:
        """初始化InvariantClient实例
        
        Args:
            server_url: Invariant服务器的URL地址
            session_id: 可选的Session ID，如果不提供会创建新的Session
            
        Raises:
            RuntimeError: 当Session创建失败时抛出异常
        """
        self.server = server_url  # 存储服务器URL
        
        # 创建或获取Session
        self.session_id, err = self._create_session(session_id)
        if err:
            raise RuntimeError(f'Failed to create session: {err}')
            
        # 初始化Policy和Monitor管理器
        self.Policy = self._Policy(self)
        self.Monitor = self._Monitor(self)

    def _create_session(
        self, session_id: str | None = None
    ) -> tuple[str | None, Exception | None]:
        """创建或获取Session
        
        Args:
            session_id: 可选的Session ID，如果提供则尝试获取现有Session
            
        Returns:
            tuple[str | None, Exception | None]: Session ID和可能的错误
            
        该方法会重试连接直到超时，用于处理服务器启动延迟的情况
        """
        elapsed = 0
        
        # 在超时时间内重试连接
        while elapsed < self.timeout:
            try:
                # 根据是否提供session_id选择不同的API端点
                if session_id:
                    response = httpx.get(
                        f'{self.server}/session/new?session_id={session_id}', timeout=60
                    )
                else:
                    response = httpx.get(f'{self.server}/session/new', timeout=60)
                    
                # 检查HTTP响应状态
                response.raise_for_status()
                
                # 返回Session ID
                return response.json().get('id'), None
                
            except (httpx.NetworkError, httpx.TimeoutException):
                # 网络错误或超时，等待1秒后重试
                elapsed += 1
                time.sleep(1)
            except httpx.HTTPError as http_err:
                # HTTP错误，直接返回错误
                return None, http_err
            except Exception as err:
                # 其他异常，返回错误
                return None, err
                
        # 超时返回连接错误
        return None, ConnectionError('Connection timed out')

    def close_session(self) -> Exception | None:
        """关闭当前Session
        
        Returns:
            Exception | None: 如果关闭失败返回异常，成功则返回None
            
        该方法向服务器发送DELETE请求来关闭Session并释放资源
        """
        try:
            # 向服务器发送删除Session的请求
            response = httpx.delete(
                f'{self.server}/session/?session_id={self.session_id}', timeout=60
            )
            response.raise_for_status()
        except (ConnectionError, httpx.TimeoutException, httpx.HTTPError) as err:
            return err
        return None

    class _Policy:
        """Policy管理内部类
        
        该类提供了安全策略的管理功能，包括创建策略、获取模板、
        分析trace等操作
        """
        
        def __init__(self, invariant: 'InvariantClient') -> None:
            """初始化Policy管理器
            
            Args:
                invariant: 父级InvariantClient实例的引用
            """
            self.server = invariant.server  # 服务器URL
            self.session_id = invariant.session_id  # Session ID
            self.policy_id: str | None = None  # Policy ID，创建策略后会被设置

        def _create_policy(self, rule: str) -> tuple[str | None, Exception | None]:
            """创建新的策略
            
            Args:
                rule: 策略规则字符串
                
            Returns:
                tuple[str | None, Exception | None]: Policy ID和可能的错误
                
            向服务器发送POST请求创建新的安全策略
            """
            try:
                response = httpx.post(
                    f'{self.server}/policy/new?session_id={self.session_id}',
                    json={'rule': rule},
                    timeout=60,
                )
                response.raise_for_status()
                # 返回服务器分配的Policy ID
                return response.json().get('policy_id'), None
            except (ConnectionError, httpx.TimeoutException, httpx.HTTPError) as err:
                return None, err

        def get_template(self) -> tuple[str | None, Exception | None]:
            """获取策略模板
            
            Returns:
                tuple[str | None, Exception | None]: 策略模板内容和可能的错误
                
            从服务器获取默认的策略模板
            """
            try:
                response = httpx.get(
                    f'{self.server}/policy/template',
                    timeout=60,
                )
                response.raise_for_status()
                # 返回策略模板的JSON内容
                return response.json(), None
            except (ConnectionError, httpx.TimeoutException, httpx.HTTPError) as err:
                return None, err

        def from_string(self, rule: str) -> 'InvariantClient._Policy':
            """从规则字符串创建策略
            
            Args:
                rule: 策略规则字符串
                
            Returns:
                InvariantClient._Policy: 当前Policy实例（支持链式调用）
                
            Raises:
                Exception: 当策略创建失败时抛出异常
                
            该方法创建新的策略并设置policy_id
            """
            policy_id, err = self._create_policy(rule)
            if err:
                raise err
            self.policy_id = policy_id
            return self

        def analyze(self, trace: list[dict[str, Any]]) -> tuple[Any, Exception | None]:
            """分析trace数据
            
            Args:
                trace: 需要分析的trace数据列表
                
            Returns:
                tuple[Any, Exception | None]: 分析结果和可能的错误
                
            使用当前策略分析提供的trace数据，检查是否存在安全问题
            """
            try:
                response = httpx.post(
                    f'{self.server}/policy/{self.policy_id}/analyze?session_id={self.session_id}',
                    json={'trace': trace},
                    timeout=60,
                )
                response.raise_for_status()
                # 返回分析结果的JSON数据
                return response.json(), None
            except (ConnectionError, httpx.TimeoutException, httpx.HTTPError) as err:
                return None, err

    class _Monitor:
        """Monitor管理内部类
        
        该类提供了实时监控功能，可以检查待执行的Action
        是否违反安全策略
        """
        
        def __init__(self, invariant: 'InvariantClient') -> None:
            """初始化Monitor管理器
            
            Args:
                invariant: 父级InvariantClient实例的引用
            """
            self.server = invariant.server  # 服务器URL
            self.session_id = invariant.session_id  # Session ID
            self.policy = ''  # 当前使用的策略字符串
            self.monitor_id: str | None = None  # Monitor ID，创建监控器后会被设置

        def _create_monitor(self, rule: str) -> tuple[str | None, Exception | None]:
            """创建新的监控器
            
            Args:
                rule: 监控规则字符串
                
            Returns:
                tuple[str | None, Exception | None]: Monitor ID和可能的错误
                
            向服务器发送POST请求创建新的监控器
            """
            try:
                response = httpx.post(
                    f'{self.server}/monitor/new?session_id={self.session_id}',
                    json={'rule': rule},
                    timeout=60,
                )
                response.raise_for_status()
                # 返回服务器分配的Monitor ID
                return response.json().get('monitor_id'), None
            except (ConnectionError, httpx.TimeoutException, httpx.HTTPError) as err:
                return None, err

        def from_string(self, rule: str) -> 'InvariantClient._Monitor':
            """从规则字符串创建监控器
            
            Args:
                rule: 监控规则字符串
                
            Returns:
                InvariantClient._Monitor: 当前Monitor实例（支持链式调用）
                
            Raises:
                Exception: 当监控器创建失败时抛出异常
                
            该方法创建新的监控器并设置monitor_id和policy
            """
            monitor_id, err = self._create_monitor(rule)
            if err:
                raise err
            self.monitor_id = monitor_id
            self.policy = rule  # 保存策略字符串
            return self

        def check(
            self,
            past_events: list[dict[str, Any]],
            pending_events: list[dict[str, Any]],
        ) -> tuple[Any, Exception | None]:
            """检查事件是否违反安全策略
            
            Args:
                past_events: 已发生的事件列表
                pending_events: 待处理的事件列表
                
            Returns:
                tuple[Any, Exception | None]: 检查结果和可能的错误
                
            该方法向服务器发送检查请求，用于实时监控Agent的Action
            是否符合安全策略
            """
            try:
                response = httpx.post(
                    f'{self.server}/monitor/{self.monitor_id}/check?session_id={self.session_id}',
                    json={'past_events': past_events, 'pending_events': pending_events},
                    timeout=60,
                )
                response.raise_for_status()
                # 返回检查结果的JSON数据
                return response.json(), None
            except (ConnectionError, httpx.TimeoutException, httpx.HTTPError) as err:
                return None, err