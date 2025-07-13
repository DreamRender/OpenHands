"""对话摘要生成工具模块。

提供用于生成对话摘要和标题的实用函数，
帮助用户更好地管理和识别不同的对话。
"""

from typing import Optional

from openhands.core.config import LLMConfig
from openhands.core.logger import openhands_logger as logger
from openhands.events.action.message import MessageAction
from openhands.events.event import EventSource
from openhands.events.event_store import EventStore
from openhands.llm.llm import LLM
from openhands.storage.data_models.settings import Settings
from openhands.storage.files import FileStore


async def generate_conversation_title(
    message: str, llm_config: LLMConfig, max_length: int = 50
) -> Optional[str]:
    """基于第一条用户消息生成简洁的对话标题。

    使用大语言模型分析用户的第一条消息，生成一个描述性的对话标题。
    这有助于用户在对话列表中快速识别不同的对话内容。

    Args:
        message (str): 对话中的第一条用户消息
        llm_config (LLMConfig): 用于生成标题的LLM配置
        max_length (int, optional): 生成标题的最大长度。默认为50个字符

    Returns:
        Optional[str]: 生成的简洁对话标题，如果生成失败则返回None

    Note:
        - 对于超过1000字符的消息会进行截断以避免过度的token使用
        - 使用专门的系统提示词指导LLM生成合适的标题
        - 如果生成的标题超过最大长度会自动截断并添加省略号
    """
    if not message or message.strip() == '':
        return None

    # 截断过长的消息以避免过度的token使用
    if len(message) > 1000:
        truncated_message = message[:1000] + '...(truncated)'
    else:
        truncated_message = message

    try:
        llm = LLM(llm_config)

        # 为LLM创建生成标题的提示词
        messages = [
            {
                'role': 'system',
                'content': 'You are a helpful assistant that generates concise, descriptive titles for conversations with OpenHands. OpenHands is a helpful AI agent that can interact with a computer to solve tasks using bash terminal, file editor, and browser. Given a user message (which may be truncated), generate a concise, descriptive title for the conversation. Return only the title, with no additional text, quotes, or explanations.',
            },
            {
                'role': 'user',
                'content': f'Generate a title (maximum {max_length} characters) for a conversation that starts with this message:\n\n{truncated_message}',
            },
        ]

        # 调用LLM生成标题
        response = llm.completion(messages=messages)
        title = response.choices[0].message.content.strip()

        # 确保标题不超过最大长度
        if len(title) > max_length:
            title = title[: max_length - 3] + '...'

        return title
    except Exception as e:
        # 记录错误但不抛出异常，允许调用者使用备用方案
        logger.error(f'Error generating conversation title: {e}')
        return None


def get_default_conversation_title(conversation_id: str) -> str:
    """基于对话ID生成默认标题。

    当无法使用LLM生成标题时的备用方案，
    使用对话ID的前5个字符创建一个简单的默认标题。

    Args:
        conversation_id (str): 对话的唯一标识符

    Returns:
        str: 默认标题字符串，格式为"Conversation {前5个字符}"
    """
    return f'Conversation {conversation_id[:5]}'


async def auto_generate_title(
    conversation_id: str, user_id: str | None, file_store: FileStore, settings: Settings
) -> str:
    """自动为对话生成标题。

    基于对话中的第一条用户消息自动生成标题。
    如果可用，使用基于LLM的标题生成；否则回退到简单的截断方式。

    Args:
        conversation_id (str): 对话的唯一标识符
        user_id (str | None): 用户的唯一标识符
        file_store (FileStore): 文件存储对象，用于访问对话数据
        settings (Settings): 用户设置对象，包含LLM配置信息

    Returns:
        str: 生成的标题字符串

    Note:
        - 优先尝试使用LLM生成智能标题
        - 如果LLM不可用或失败，使用消息截断作为标题
        - 如果找不到用户消息，返回空字符串
        - 所有错误都会被捕获并记录，确保函数总是返回一个有效的字符串
    """
    try:
        # 为对话创建事件存储
        event_store = EventStore(conversation_id, file_store, user_id)

        # 查找第一条用户消息
        first_user_message = None
        for event in event_store.search_events():
            if (
                event.source == EventSource.USER  # 来自用户的事件
                and isinstance(event, MessageAction)  # 是消息Action
                and event.content  # 有内容
                and event.content.strip()  # 内容不为空
            ):
                first_user_message = event.content
                break

        if first_user_message:
            # 从用户设置获取LLM配置
            try:
                if settings and settings.llm_model:
                    # 从设置创建LLM配置
                    llm_config = LLMConfig(
                        model=settings.llm_model,
                        api_key=settings.llm_api_key,
                        base_url=settings.llm_base_url,
                    )

                    # 尝试使用LLM生成标题
                    llm_title = await generate_conversation_title(
                        first_user_message, llm_config
                    )
                    if llm_title:
                        logger.info(f'Generated title using LLM: {llm_title}')
                        return llm_title
            except Exception as e:
                # 记录LLM标题生成错误
                logger.error(f'Error using LLM for title generation: {e}')

            # 如果LLM生成失败或不可用，回退到简单截断
            first_user_message = first_user_message.strip()
            title = first_user_message[:30]  # 取前30个字符
            if len(first_user_message) > 30:
                title += '...'  # 如果被截断则添加省略号
            logger.info(f'Generated title using truncation: {title}')
            return title
    except Exception as e:
        # 记录任何生成标题时的错误
        logger.error(f'Error generating title: {str(e)}')
    
    # 如果所有方法都失败，返回空字符串
    return ''
