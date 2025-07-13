from openhands.security.analyzer import SecurityAnalyzer
from openhands.security.invariant.analyzer import InvariantAnalyzer

# 全局变量：可用的安全分析器映射表
SecurityAnalyzers: dict[str, type[SecurityAnalyzer]] = {
    'invariant': InvariantAnalyzer,
}

"""
SecurityAnalyzers全局变量说明：

这是一个字典类型的全局变量，用于注册和管理系统中可用的安全分析器实现。

字典结构：
- 键（str）：安全分析器的名称标识符，用于在配置中引用
- 值（type[SecurityAnalyzer]）：安全分析器类的类型，必须继承自SecurityAnalyzer基类

当前注册的分析器：
- 'invariant': InvariantAnalyzer类，基于Invariant服务的安全分析器

使用场景：
1. 配置系统：用户可以通过字符串标识符选择要使用的安全分析器
2. 动态创建：系统可以根据配置动态实例化相应的安全分析器
3. 扩展性：新的安全分析器实现可以通过添加到此字典来注册到系统中

扩展方式：
如需添加新的安全分析器，只需：
1. 创建继承自SecurityAnalyzer的新类
2. 在此字典中添加相应的映射条目

例如：
SecurityAnalyzers['custom_analyzer'] = CustomSecurityAnalyzer
"""