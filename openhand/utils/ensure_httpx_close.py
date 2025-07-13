"""
LiteLLM目前存在一个问题：HttpHandlers被创建但没有被正确关闭。
我们已向他们提交了PR（https://github.com/BerriAI/litellm/pull/8711），
他们的开发团队表示正在进行重构以修复此问题，但在此期间，
我们需要手动管理httpx.Client的生命周期。

我们不能简单地传入自己的客户端对象，因为不同的实现使用不同类型的客户端对象。

所以我们对httpx.Client类进行猴子补丁，跟踪新创建的实例，
并在操作完成时关闭这些实例。（由于某些路径创建单个共享客户端并重用它们，
我们实际上需要创建一个代理对象来允许这些客户端可重用。）

希望这个问题很快会被修复，这样我们就可以移除这个可憎的代码了。
"""

import contextlib
from typing import Callable

import httpx


@contextlib.contextmanager
def ensure_httpx_close():
    """确保httpx客户端正确关闭的上下文管理器。

    通过猴子补丁替换httpx.Client类为代理类，自动管理客户端的生命周期，
    防止LiteLLM导致的资源泄漏问题。

    Yields:
        None: 在上下文中可以正常使用httpx.Client

    Note:
        - 在上下文开始时替换httpx.Client为ClientProxy
        - 跟踪所有创建的代理实例
        - 在上下文结束时恢复原始类并关闭所有代理
    """
    # 保存原始的httpx.Client类
    wrapped_class = httpx.Client
    # 用于跟踪所有创建的代理实例
    proxys = []

    class ClientProxy:
        """httpx.Client代理类。

        有时LiteLLM为每个连接打开一个新的httpx客户端，而不关闭它们。
        有时它确实关闭了它们。有时，它在连接之间重用客户端。
        对于重用客户端的情况，我们需要能够在关闭后仍然重用客户端。

        Attributes:
            client_constructor (Callable): 客户端构造函数（未使用）
            args (tuple): 构造客户端时的位置参数
            kwargs (dict): 构造客户端时的关键字参数
            client (httpx.Client): 实际的httpx客户端实例
        """

        client_constructor: Callable
        """客户端构造函数引用（当前未使用）。"""
        
        args: tuple
        """创建客户端时使用的位置参数。"""
        
        kwargs: dict
        """创建客户端时使用的关键字参数。"""
        
        client: httpx.Client
        """实际的httpx.Client实例，可能为None表示已关闭。"""

        def __init__(self, *args, **kwargs):
            """初始化客户端代理。

            Args:
                *args: 传递给httpx.Client的位置参数
                **kwargs: 传递给httpx.Client的关键字参数

            Note:
                保存参数以便在客户端关闭后能够重新创建，
                同时将代理实例添加到跟踪列表中。
            """
            self.args = args
            self.kwargs = kwargs
            # 创建实际的httpx.Client实例
            self.client = wrapped_class(*self.args, **self.kwargs)
            # 将此代理添加到跟踪列表中
            proxys.append(self)

        def __getattr__(self, name):
            """属性访问代理。

            当访问代理对象的属性时，将调用转发给实际的客户端。
            如果客户端已关闭（为None），会重新创建一个新的客户端。

            Args:
                name (str): 要访问的属性名称

            Returns:
                实际客户端对象上对应属性的值

            Note:
                这个方法使代理对象表现得像真正的httpx.Client，
                同时提供重新创建已关闭客户端的能力。
            """
            # 如果客户端已关闭，重新创建一个
            if self.client is None:
                self.client = wrapped_class(*self.args, **self.kwargs)
            # 返回实际客户端对象的属性
            return getattr(self.client, name)

        def close(self):
            """关闭客户端。

            关闭实际的httpx.Client并将引用设为None，
            表示客户端已关闭。

            Note:
                关闭后，通过__getattr__访问属性时会自动重新创建客户端。
            """
            if self.client:
                self.client.close()
                self.client = None

        def __iter__(self, *args, **kwargs):
            """迭代器方法代理。

            我们需要重写此方法，因为调试器会调用它导致客户端重新打开。

            Args:
                *args: 迭代器的位置参数
                **kwargs: 迭代器的关键字参数

            Returns:
                迭代器对象

            Note:
                如果客户端存在，调用其iter方法；否则调用对象的默认iter方法。
                这种特殊处理是为了避免调试器意外重新打开已关闭的客户端。
            """
            if self.client:
                return self.client.iter(*args, **kwargs)
            return object.__getattribute__(self, 'iter')(*args, **kwargs)

        @property
        def is_closed(self):
            """检查客户端是否已关闭。

            Returns:
                bool: 如果客户端为None或已关闭返回True，否则返回False
            """
            if self.client is None:
                return True
            return self.client.is_closed

    # 用代理类替换原始的httpx.Client
    httpx.Client = ClientProxy
    try:
        # 执行上下文代码块
        yield
    finally:
        # 恢复原始的httpx.Client类
        httpx.Client = wrapped_class
        # 关闭所有创建的代理客户端
        while proxys:
            proxy = proxys.pop()
            proxy.close()
