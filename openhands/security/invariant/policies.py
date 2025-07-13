# 默认的Invariant安全策略定义
DEFAULT_INVARIANT_POLICY = """from invariant.detectors import semgrep, secrets, CodeIssue

raise "Disallow secrets in bash commands [risk=medium]" if:
    (call: ToolCall)
    call is tool:cmd_run
    any(secrets(call.function.arguments.command))

raise "Vulnerability in python code [risk=medium]" if:
    (call: ToolCall)
    call is tool:ipython_run_cell
    semgrep_res := semgrep(call.function.arguments.code, lang="python")
    any(semgrep_res)

raise "Vulnerability in bash command [risk=medium]" if:
    (call: ToolCall)
    call is tool:cmd_run
    semgrep_res := semgrep(call.function.arguments.command, lang="bash")
    any(semgrep_res)
"""

"""
DEFAULT_INVARIANT_POLICY详细说明：

这是OpenHands系统的默认安全策略配置，使用Invariant的策略语言编写。
策略包含三个主要的安全检查规则：

1. 禁止在bash命令中包含秘密信息 [风险等级: medium]
   - 检查目标：cmd_run工具调用
   - 检查内容：命令参数中是否包含敏感信息（如密码、API密钥等）
   - 触发条件：secrets检测器在命令中发现任何秘密信息

2. 检查Python代码中的漏洞 [风险等级: medium]  
   - 检查目标：ipython_run_cell工具调用
   - 检查内容：Python代码是否存在安全漏洞
   - 触发条件：semgrep静态分析工具在Python代码中发现安全问题

3. 检查bash命令中的漏洞 [风险等级: medium]
   - 检查目标：cmd_run工具调用  
   - 检查内容：bash命令是否存在安全漏洞
   - 触发条件：semgrep静态分析工具在bash命令中发现安全问题

策略语法说明：
- (call: ToolCall)：定义变量call为ToolCall类型
- call is tool:工具名：检查调用的工具是否匹配
- secrets()/semgrep()：调用相应的安全检测器
- any()：检查是否有任何检测结果
- [risk=level]：定义触发时的风险等级
"""