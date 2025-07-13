from openhands.core.logger import openhands_logger as logger
from openhands.events.action.action import Action
from openhands.events.action.empty import NullAction
from openhands.events.event import Event
from openhands.events.observation import (
    CmdOutputObservation,
    NullObservation,
    Observation,
)


def get_pairs_from_events(events: list[Event]) -> list[tuple[Action, Observation]]:
    """
    从Event列表中返回历史记录作为(Action, Observation)元组列表
    
    此函数是一个兼容性函数，用于评估读取和可视化工作，处理旧的历史记录格式。
    它将Event列表转换为Action-Observation对的格式，这是许多分析工具期望的格式。
    
    处理规则：
    - 可运行的Action被设置为Observation的原因
    - (MessageAction, NullObservation) 用于 source=USER
    - (MessageAction, NullObservation) 用于 source=AGENT  
    - (other_action?, NullObservation)
    - (NullAction, CmdOutputObservation) 用于背景CmdOutputObservation
    
    Args:
        events (list[Event]): 要处理的Event列表
        
    Returns:
        list[tuple[Action, Observation]]: Action-Observation对的列表
    """
    tuples: list[tuple[Action, Observation]] = []  # 结果元组列表
    action_map: dict[int, Action] = {}  # Action映射表，key为Event ID
    observation_map: dict[int, Observation] = {}  # Observation映射表，key为cause Event ID

    # 遍历所有Event，构建Action和Observation映射
    for event in events:
        # 检查Event是否有有效ID
        if event.id is None or event.id == -1:
            logger.debug(f'Event {event} has no ID')

        # 如果是Action类型的Event，添加到Action映射
        if isinstance(event, Action):
            action_map[event.id] = event

        # 如果是Observation类型的Event，处理其cause关系
        if isinstance(event, Observation):
            # 检查Observation是否有有效的cause
            if event.cause is None or event.cause == -1:
                logger.debug(f'Observation {event} has no cause')

            # 如果没有cause，跳过处理
            # 可运行的Action被设置为Observation的cause
            # NullObservation没有cause
            if event.cause is None:
                continue

            # 将Observation添加到映射，key为其cause的Event ID
            observation_map[event.cause] = event

    # 为每个Action寻找对应的Observation并组成对
    for action_id, action in action_map.items():
        observation = observation_map.get(action_id)
        if observation:
            # 找到了对应的Observation，组成一对
            tuples.append((action, observation))
        else:
            # 没有找到对应的Observation，使用NullObservation
            tuples.append((action, NullObservation('')))

    # 处理没有对应Action的Observation
    for cause_id, observation in observation_map.items():
        if cause_id not in action_map:
            # 如果是NullObservation且没有对应Action，跳过
            if isinstance(observation, NullObservation):
                continue
            # 如果不是CmdOutputObservation但没有cause，记录调试信息
            if not isinstance(observation, CmdOutputObservation):
                logger.debug(f'Observation {observation} has no cause')
            # 为没有对应Action的Observation创建NullAction对
            tuples.append((NullAction(), observation))

    # 返回元组列表的副本
    return tuples.copy()