"""
模型下载触发模块

这是一个简单的触发模块，用于启动模型下载过程。
运行此文件将触发模型下载，通过导入 agenthub 模块来注册所有 agents。
"""

# 运行此文件以触发模型下载
import openhands.agenthub  # noqa F401 (导入此模块以注册所有 agents)