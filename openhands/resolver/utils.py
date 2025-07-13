import logging
import multiprocessing as mp
import os
import re
from typing import Callable

from pydantic import SecretStr

from openhands.controller.state.state import State
from openhands.core.logger import get_console_handler
from openhands.core.logger import openhands_logger as logger
from openhands.events.action import Action
from openhands.events.action.message import MessageAction
from openhands.integrations.service_types import ProviderType
from openhands.integrations.utils import validate_provider_token


async def identify_token(token: str, base_domain: str | None) -> ProviderType:
    """识别token属于GitHub、GitLab还是Bitbucket
    
    通过验证token来确定它所属的平台类型。
    
    Args:
        token (str): 要检查的个人访问token
        base_domain (str | None): 提供商的自定义基础域名（例如GitHub Enterprise）
        
    Returns:
        ProviderType: 识别出的平台类型
        
    Raises:
        ValueError: 当token无效时抛出异常
    """
    # 验证提供商token
    provider = await validate_provider_token(SecretStr(token), base_domain)
    if not provider:
        raise ValueError('Token is invalid.')

    return provider


def codeact_user_response(
    state: State,
    encapsulate_solution: bool = False,
    try_parse: Callable[[Action | None], str] | None = None,
) -> str:
    """生成CodeAct Agent的用户响应
    
    根据当前状态生成适当的用户响应，用于指导Agent继续工作。
    包含不同情况下的指令和退出条件。
    
    Args:
        state (State): 当前的Agent状态
        encapsulate_solution (bool, optional): 是否要求将最终答案封装在标签中. Defaults to False.
        try_parse (Callable[[Action | None], str] | None, optional): 尝试解析答案的函数. Defaults to None.
        
    Returns:
        str: 生成的用户响应字符串
    """
    # 如果需要封装解决方案，添加相应指令
    encaps_str = (
        (
            'Please encapsulate your final answer (answer ONLY) within <solution> and </solution>.\n'
            'For example: The answer to the question is <solution> 42 </solution>.\n'
        )
        if encapsulate_solution
        else ''
    )
    
    # 基础指令消息
    msg = (
        'Please continue working on the task on whatever approach you think is suitable.\n'
        'If you think you have solved the task, please first send your answer to user through message and then finish the interaction.\n'
        f'{encaps_str}'
        'IMPORTANT: YOU SHOULD NEVER ASK FOR HUMAN HELP.\n'
    )

    if state.history:
        # 检查最后一个Action是否有答案，如果有则提前退出
        if try_parse is not None:
            last_action = next(
                (
                    event
                    for event in reversed(state.history)
                    if isinstance(event, Action)
                ),
                None,
            )
            ans = try_parse(last_action)
            if ans is not None:
                return '/exit'

        # 检查Agent是否已经尝试与用户对话3次，如果是，让Agent知道它可以放弃
        user_msgs = [
            event
            for event in state.history
            if isinstance(event, MessageAction) and event.source == 'user'
        ]
        if len(user_msgs) >= 2:
            # 当Agent尝试了3次后，让它知道可以放弃
            return (
                msg
                + 'If you want to give up, run: <execute_bash> exit </execute_bash>.\n'
            )
    return msg


def cleanup() -> None:
    """清理子进程
    
    终止并等待所有活动的子进程结束，用于程序退出时的清理工作。
    """
    logger.info('Cleaning up child processes...')
    # 遍历所有活动的子进程
    for process in mp.active_children():
        logger.info(f'Terminating child process: {process.name}')
        process.terminate()  # 终止进程
        process.join()  # 等待进程结束


def reset_logger_for_multiprocessing(
    logger: logging.Logger, instance_id: str, log_dir: str
) -> None:
    """为多进程重置日志记录器
    
    将日志保存到每个进程的单独文件中，而不是尝试从多个进程写入同一个文件/控制台。
    
    Args:
        logger (logging.Logger): 要重置的日志记录器
        instance_id (str): 实例ID，用于区分不同的进程
        log_dir (str): 日志文件目录
    """
    # 设置日志文件路径
    log_file = os.path.join(
        log_dir,
        f'instance_{instance_id}.log',
    )
    
    # 删除日志记录器的所有现有处理器
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        
    # 添加控制台处理器以打印一行信息
    logger.addHandler(get_console_handler())
    logger.info(
        f'Starting resolver for instance {instance_id}.\n'
        f'Hint: run "tail -f {log_file}" to see live logs in a separate shell'
    )
    
    # 再次删除所有现有处理器
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        
    # 确保日志目录存在
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    # 创建文件处理器
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    )
    logger.addHandler(file_handler)


def extract_image_urls(issue_body: str) -> list[str]:
    """从Issue内容中提取图片URL
    
    使用正则表达式匹配Markdown图片语法，提取所有图片URL。
    
    Args:
        issue_body (str): Issue的内容文本
        
    Returns:
        list[str]: 提取出的图片URL列表
    """
    # 匹配Markdown图片语法的正则表达式 ![alt text](image_url)
    image_pattern = r'!\[.*?\]\((https?://[^\s)]+)\)'
    return re.findall(image_pattern, issue_body)


def extract_issue_references(body: str) -> list[int]:
    """从文本中提取Issue引用编号
    
    解析文本内容，提取所有#number格式的Issue引用，
    过滤掉代码块和URL中的假阳性结果。
    
    Args:
        body (str): 要解析的文本内容
        
    Returns:
        list[int]: 提取出的Issue编号列表
    """
    # 首先移除代码块，因为它们可能包含假阳性结果
    body = re.sub(r'```.*?```', '', body, flags=re.DOTALL)

    # 移除内联代码
    body = re.sub(r'`[^`]*`', '', body)

    # 移除包含井号符号的URL
    body = re.sub(r'https?://[^\s)]*#\d+[^\s)]*', '', body)

    # 现在提取Issue编号，确保它们不是其他文本的一部分
    # 该模式匹配满足以下条件的#number：
    # 1. 在文本开头或在空白/标点符号后
    # 2. 后面跟着空白、标点符号或文本结尾
    # 3. 不是URL的一部分
    pattern = r'(?:^|[\s\[({]|[^\w#])#(\d+)(?=[\s,.\])}]|$)'
    return [int(match) for match in re.findall(pattern, body)]


def get_unique_uid(start_uid: int = 1000) -> int:
    """获取一个唯一的用户ID
    
    从指定的起始UID开始，查找一个在系统中不存在的用户ID。
    主要用于容器环境中避免UID冲突。
    
    Args:
        start_uid (int, optional): 起始UID值. Defaults to 1000.
        
    Returns:
        int: 可用的唯一UID
    """
    # 收集系统中已存在的UID
    existing_uids = set()
    with open('/etc/passwd', 'r') as passwd_file:
        for line in passwd_file:
            parts = line.split(':')
            if len(parts) > 2:
                try:
                    existing_uids.add(int(parts[2]))
                except ValueError:
                    # 忽略无法转换为整数的UID
                    continue

    # 从起始UID开始查找可用的UID
    while start_uid in existing_uids:
        start_uid += 1

    return start_uid
