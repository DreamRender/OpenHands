from typing import TypedDict

# 导入OpenHands核心模块
from openhands.controller.agent import Agent  # Agent基础类
from openhands.controller.state.state import State  # 系统状态管理
from openhands.core.config import AgentConfig  # Agent配置类
from openhands.core.schema import AgentState  # Agent状态枚举

# 导入各种Action类型
from openhands.events.action import (
    Action,  # 基础Action类
    AgentFinishAction,  # Agent完成Action
    AgentRejectAction,  # Agent拒绝Action
    BrowseInteractiveAction,  # 浏览器交互Action
    BrowseURLAction,  # 浏览器URL访问Action
    CmdRunAction,  # 命令执行Action
    FileReadAction,  # 文件读取Action
    FileWriteAction,  # 文件写入Action
    MessageAction,  # 消息Action
)

# 导入各种Observation类型
from openhands.events.observation import (
    AgentStateChangedObservation,  # Agent状态变更Observation
    BrowserOutputObservation,  # 浏览器输出Observation
    CmdOutputMetadata,  # 命令输出Metadata
    CmdOutputObservation,  # 命令输出Observation
    FileReadObservation,  # 文件读取Observation
    FileWriteObservation,  # 文件写入Observation
    Observation,  # 基础Observation类
)

# 导入事件序列化工具和LLM模块
from openhands.events.serialization.event import event_to_dict  # 事件转字典工具
from openhands.llm.llm import LLM  # 大语言Model接口

"""
FIXME: 此处存在一些已知问题需要修复
* FileWrites似乎会在文件末尾添加意外的换行符
* Browser功能当前无法正常工作
"""

# 全局类型定义：Action和Observation的组合结构
ActionObs = TypedDict(
    'ActionObs', {'action': Action, 'observations': list[Observation]}
)
"""ActionObs: 定义Action和对应Observation列表的组合类型，用于存储预定义的测试步骤"""


class DummyAgent(Agent):
    """
    DummyAgent类：用于端到端测试的虚拟Agent
    
    这个Agent不会进行任何LLM调用，而是按照预定义的步骤顺序执行一系列固定的Action，
    主要用于测试系统的基础功能和端到端流程验证。
    
    Attributes:
        VERSION (str): Agent版本号
        steps (list[ActionObs]): 预定义的测试步骤列表，包含Action和期望的Observation
    """
    
    VERSION = '1.0'  # Agent版本标识
    
    def __init__(self, llm: LLM, config: AgentConfig) -> None:
        """
        初始化DummyAgent
        
        Args:
            llm (LLM): 大语言Model实例（虽然DummyAgent不会实际使用）
            config (AgentConfig): Agent配置对象
        """
        super().__init__(llm, config)
        
        # 预定义的测试步骤序列，每个步骤包含一个Action和期望的Observation列表
        self.steps: list[ActionObs] = [
            # 步骤1: 发送开始消息
            {
                'action': MessageAction('Time to get started!'),
                'observations': [],
            },
            # 步骤2: 执行简单的echo命令
            {
                'action': CmdRunAction(command='echo "foo"'),
                'observations': [CmdOutputObservation('foo', command='echo "foo"')],
            },
            # 步骤3: 创建shell脚本文件
            {
                'action': FileWriteAction(
                    content='echo "Hello, World!"', path='hello.sh'
                ),
                'observations': [
                    FileWriteObservation(
                        content='echo "Hello, World!"', path='hello.sh'
                    )
                ],
            },
            # 步骤4: 读取刚创建的文件
            {
                'action': FileReadAction(path='hello.sh'),
                'observations': [
                    FileReadObservation('echo "Hello, World!"\n', path='hello.sh')
                ],
            },
            # 步骤5: 尝试执行shell脚本（预期会失败，用于测试错误处理）
            {
                'action': CmdRunAction(command='bash hello.sh'),
                'observations': [
                    CmdOutputObservation(
                        'bash: hello.sh: No such file or directory',
                        command='bash workspace/hello.sh',
                        metadata=CmdOutputMetadata(exit_code=127),
                    )
                ],
            },
            # 步骤6: 测试浏览器URL访问功能
            {
                'action': BrowseURLAction(url='https://google.com'),
                'observations': [
                    BrowserOutputObservation(
                        '<html><body>Simulated Google page</body></html>',
                        url='https://google.com',
                        screenshot='',
                        trigger_by_action='',
                    ),
                ],
            },
            # 步骤7: 测试浏览器交互功能
            {
                'action': BrowseInteractiveAction(
                    browser_actions='goto("https://google.com")'
                ),
                'observations': [
                    BrowserOutputObservation(
                        '<html><body>Simulated Google page after interaction</body></html>',
                        url='https://google.com',
                        screenshot='',
                        trigger_by_action='',
                    ),
                ],
            },
            # 步骤8: 测试Agent拒绝功能
            {
                'action': AgentRejectAction(),
                'observations': [AgentStateChangedObservation('', AgentState.REJECTED)],
            },
            # 步骤9: 测试Agent完成功能
            {
                'action': AgentFinishAction(
                    outputs={}, thought='Task completed', action='finish'
                ),
                'observations': [AgentStateChangedObservation('', AgentState.FINISHED)],
            },
        ]

    def step(self, state: State) -> Action:
        """
        执行单个测试步骤
        
        根据当前的迭代状态，返回预定义序列中对应的Action。
        同时验证上一步的Observation是否与期望一致。
        
        Args:
            state (State): 当前系统状态，包含迭代标志和历史事件
            
        Returns:
            Action: 当前步骤应该执行的Action，如果超出预定义步骤则返回AgentFinishAction
        """
        # 检查是否已经执行完所有预定义步骤
        if state.iteration_flag.current_value >= len(self.steps):
            return AgentFinishAction()

        # 获取当前步骤的Action
        current_step = self.steps[state.iteration_flag.current_value]
        action = current_step['action']

        # 如果不是第一步，验证上一步的Observation是否符合预期
        if state.iteration_flag.current_value > 0:
            # 获取上一步的预期结果
            prev_step = self.steps[state.iteration_flag.current_value - 1]

            # 如果上一步有期望的Observation，进行验证
            if 'observations' in prev_step and prev_step['observations']:
                expected_observations = prev_step['observations']
                # 从状态历史中获取最近的事件，数量与期望的Observation数量相等
                hist_events = state.view[-len(expected_observations) :]

                # 检查实际获得的事件数量是否符合预期
                if len(hist_events) < len(expected_observations):
                    print(
                        f'Warning: Expected {len(expected_observations)} observations, but got {len(hist_events)}'
                    )

                # 逐一比较每个Observation
                for i in range(min(len(expected_observations), len(hist_events))):
                    # 将事件转换为字典格式以便比较
                    hist_obs = event_to_dict(hist_events[i])
                    expected_obs = event_to_dict(expected_observations[i])

                    # 移除动态字段，这些字段在每次运行时可能不同
                    for obs in [hist_obs, expected_obs]:
                        obs.pop('id', None)  # 移除事件ID
                        obs.pop('timestamp', None)  # 移除时间戳
                        obs.pop('cause', None)  # 移除原因字段
                        obs.pop('source', None)  # 移除来源字段

                    # 比较处理后的Observation，如果不匹配则输出警告
                    if hist_obs != expected_obs:
                        print(
                            f'Warning: Observation mismatch. Expected {expected_obs}, got {hist_obs}'
                        )

        return action