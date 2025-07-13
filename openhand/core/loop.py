"""
Agent 运行循环模块

此模块提供了运行 Agent 直到完成的核心循环功能。
负责协调控制器、运行时和内存组件，并处理状态回调。
"""

import asyncio

from openhands.controller import AgentController
from openhands.core.logger import openhands_logger as logger
from openhands.core.schema import AgentState
from openhands.memory.memory import Memory
from openhands.runtime.base import Runtime


async def run_agent_until_done(
    controller: AgentController,
    runtime: Runtime,
    memory: Memory,
    end_states: list[AgentState],
) -> None:
    """
    运行 Agent 直到达到终端状态。
    
    此函数接受一个控制器和一个运行时，并将运行 Agent
    直到它达到终端状态。
    注意：在传入此函数之前，运行时必须已经连接。

    Args:
        controller: Agent 控制器实例
        runtime: 运行时环境实例（必须已连接）
        memory: Memory 实例
        end_states: 终端状态列表，当 Agent 达到这些状态之一时停止运行
        
    Raises:
        ValueError: 当运行时或控制器已经设置了状态回调时
    """

    def status_callback(msg_type: str, msg_id: str, msg: str) -> None:
        """
        状态回调函数，用于处理运行时和控制器的状态消息。
        
        Args:
            msg_type: 消息类型（'error' 或其他）
            msg_id: 消息 ID
            msg: 消息内容
        """
        if msg_type == 'error':
            # 处理错误消息
            logger.error(msg)
            if controller:
                # 设置控制器的最后错误并将状态设为错误
                controller.state.last_error = msg
                asyncio.create_task(controller.set_agent_state_to(AgentState.ERROR))
        else:
            # 处理信息消息
            logger.info(msg)

    # 检查运行时是否已设置状态回调
    if hasattr(runtime, 'status_callback') and runtime.status_callback:
        raise ValueError(
            'Runtime status_callback 已设置，但 run_agent_until_done 将覆盖它'
        )
    
    # 检查控制器是否已设置状态回调
    if hasattr(controller, 'status_callback') and controller.status_callback:
        raise ValueError(
            'Controller status_callback 已设置，但 run_agent_until_done 将覆盖它'
        )

    # 为各个组件设置状态回调
    runtime.status_callback = status_callback
    controller.status_callback = status_callback
    memory.status_callback = status_callback

    # 主运行循环：持续运行直到 Agent 达到终端状态
    while controller.state.agent_state not in end_states:
        # 短暂休眠以避免忙等待，让其他异步任务有机会执行
        await asyncio.sleep(1)