import os
from datetime import datetime, timezone

from openhands.core.config.utils import load_openhands_config
from openhands.core.logger import openhands_logger as logger
from openhands.server.config.server_config import ServerConfig
from openhands.storage.conversation.conversation_store import ConversationStore
from openhands.storage.data_models.conversation_metadata import ConversationMetadata
from openhands.utils.conversation_summary import get_default_conversation_title
from openhands.utils.import_utils import get_impl


class ConversationValidator:
    """用于验证对话访问权限的抽象基类。

    这是OpenHands中的一个扩展点，允许应用程序自定义对话访问验证的方式。
    应用程序可以通过以下方式替换自己的实现：
    1. 创建一个继承自ConversationValidator的类
    2. 实现validate方法
    3. 将OPENHANDS_CONVERSATION_VALIDATOR_CLS环境变量设置为该类的完全限定名称

    该类通过create_conversation_validator()中的get_impl()进行实例化。

    默认实现不执行任何验证，返回None, None。
    """

    async def validate(
        self,
        conversation_id: str,
        cookies_str: str,
        authorization_header: str | None = None,
    ) -> str | None:
        """验证对话访问权限并返回用户ID。
        
        Args:
            conversation_id: 对话的唯一标识符
            cookies_str: 请求中的cookies字符串
            authorization_header: 可选的授权头信息
            
        Returns:
            验证通过的用户ID，如果验证失败则返回None
        """
        # 默认实现中user_id为None，表示不进行用户验证
        user_id = None
        # 确保对话metadata存在，如果不存在则创建
        metadata = await self._ensure_metadata_exists(conversation_id, user_id)
        return metadata.user_id

    async def _ensure_metadata_exists(
        self,
        conversation_id: str,
        user_id: str | None,
    ) -> ConversationMetadata:
        """确保对话metadata存在，如果不存在则创建。
        
        Args:
            conversation_id: 对话的唯一标识符
            user_id: 用户的唯一标识符，可能为None
            
        Returns:
            对话的metadata对象
        """
        # 加载OpenHands配置
        config = load_openhands_config()
        # 创建服务器配置实例
        server_config = ServerConfig()

        # 通过反射获取配置的ConversationStore实现类
        conversation_store_class: type[ConversationStore] = get_impl(
            ConversationStore,
            server_config.conversation_store_class,
        )
        # 获取ConversationStore的实例
        conversation_store = await conversation_store_class.get_instance(
            config, user_id
        )

        try:
            # 尝试获取现有的对话metadata
            metadata = await conversation_store.get_metadata(conversation_id)
        except FileNotFoundError:
            # 如果metadata不存在，则创建新的metadata
            logger.info(
                f'Creating new conversation metadata for {conversation_id}',
                extra={'session_id': conversation_id},
            )
            # 保存新创建的metadata到存储中
            await conversation_store.save_metadata(
                ConversationMetadata(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    title=get_default_conversation_title(conversation_id),  # 获取默认的对话标题
                    last_updated_at=datetime.now(timezone.utc),  # 设置最后更新时间为当前UTC时间
                    selected_repository=None,  # 初始化时没有选择的Repository
                )
            )
            # 重新获取刚创建的metadata
            metadata = await conversation_store.get_metadata(conversation_id)
        return metadata


def create_conversation_validator() -> ConversationValidator:
    """创建对话验证器实例。
    
    通过环境变量OPENHANDS_CONVERSATION_VALIDATOR_CLS获取验证器类名，
    如果未设置则使用默认的ConversationValidator。
    
    Returns:
        ConversationValidator的实例
    """
    # 从环境变量获取验证器类的完全限定名，如果未设置则使用默认值
    conversation_validator_cls = os.environ.get(
        'OPENHANDS_CONVERSATION_VALIDATOR_CLS',
        'openhands.storage.conversation.conversation_validator.ConversationValidator',
    )
    # 通过反射获取验证器类并实例化
    ConversationValidatorImpl = get_impl(
        ConversationValidator, conversation_validator_cls
    )
    return ConversationValidatorImpl()