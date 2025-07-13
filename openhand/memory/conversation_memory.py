from typing import Generator

from litellm import ModelResponse

from openhands.core.config.agent_config import AgentConfig
from openhands.core.logger import openhands_logger as logger
from openhands.core.message import ImageContent, Message, TextContent
from openhands.core.schema import ActionType
from openhands.events.action import (
    Action,
    AgentDelegateAction,
    AgentFinishAction,
    AgentThinkAction,
    BrowseInteractiveAction,
    BrowseURLAction,
    CmdRunAction,
    FileEditAction,
    FileReadAction,
    IPythonRunCellAction,
    MessageAction,
)
from openhands.events.action.mcp import MCPAction
from openhands.events.action.message import SystemMessageAction
from openhands.events.event import Event, RecallType
from openhands.events.observation import (
    AgentCondensationObservation,
    AgentDelegateObservation,
    AgentThinkObservation,
    BrowserOutputObservation,
    CmdOutputObservation,
    FileDownloadObservation,
    FileEditObservation,
    FileReadObservation,
    IPythonRunCellObservation,
    UserRejectObservation,
)
from openhands.events.observation.agent import (
    MicroagentKnowledge,
    RecallObservation,
)
from openhands.events.observation.error import ErrorObservation
from openhands.events.observation.mcp import MCPObservation
from openhands.events.observation.observation import Observation
from openhands.events.serialization.event import truncate_content
from openhands.utils.prompt import (
    ConversationInstructions,
    PromptManager,
    RepositoryInfo,
    RuntimeInfo,
)


class ConversationMemory:
    """对话记忆管理器，负责将事件历史处理成Agent可理解的对话格式。

    该类的主要功能是将各种类型的事件（Action和Observation）转换为标准的消息格式，
    以便LLM能够理解和处理。它支持function calling模式、视觉功能、缓存优化等特性。
    """

    def __init__(self, config: AgentConfig, prompt_manager: PromptManager):
        """初始化对话记忆管理器。

        Args:
            config: Agent配置对象，包含各种行为配置参数
            prompt_manager: 提示管理器，用于生成和格式化各种提示文本
        """
        self.agent_config = config  # Agent配置对象，包含启用的功能和行为参数
        self.prompt_manager = prompt_manager  # 提示管理器，用于构建和格式化提示内容

    @staticmethod
    def _is_valid_image_url(url: str | None) -> bool:
        """检查图片URL是否有效且非空。

        Args:
            url: 待验证的图片URL

        Returns:
            bool: 如果URL有效返回True，否则返回False
        """
        # 检查URL是否存在且去除空白字符后不为空
        return bool(url and url.strip())

    def process_events(
            self,
            condensed_history: list[Event],
            initial_user_action: MessageAction,
            max_message_chars: int | None = None,
            vision_is_active: bool = False,
    ) -> list[Message]:
        """将状态历史处理为LLM可理解的消息列表。

        确保在function calling模式下正确处理tool call action。
        这是整个类的核心方法，负责将事件流转换为标准的对话格式。

        Args:
            condensed_history: 待转换的压缩事件历史列表
            initial_user_action: 初始用户消息action，用于确保对话正确开始
            max_message_chars: 事件内容包含在LLM提示中的最大字符数，较大的观察结果会被截断
            vision_is_active: LLM中是否激活了视觉功能。如果为True，将包含图片URL

        Returns:
            list[Message]: 处理后的消息列表，可直接发送给LLM
        """

        events = condensed_history

        # 确保事件列表以SystemMessageAction开始，然后是MessageAction(source='user')
        self._ensure_system_message(events)
        self._ensure_initial_user_message(events, initial_user_action)

        # 记录视觉浏览状态
        logger.debug(f'Visual browsing: {self.agent_config.enable_som_visual_browsing}')

        # 初始化空消息列表
        messages = []

        # 处理常规事件
        # 存储等待tool call结果的消息，key为response_id，value为消息对象
        pending_tool_call_action_messages: dict[str, Message] = {}
        # 存储tool call ID到消息的映射，用于function calling模式
        tool_call_id_to_message: dict[str, Message] = {}

        # 遍历所有事件进行处理
        for i, event in enumerate(events):
            # 根据事件类型创建相应的消息
            if isinstance(event, Action):
                # 处理Action事件（Agent的行为）
                messages_to_add = self._process_action(
                    action=event,
                    pending_tool_call_action_messages=pending_tool_call_action_messages,
                    vision_is_active=vision_is_active,
                )
            elif isinstance(event, Observation):
                # 处理Observation事件（行为的结果）
                messages_to_add = self._process_observation(
                    obs=event,
                    tool_call_id_to_message=tool_call_id_to_message,
                    max_message_chars=max_message_chars,
                    vision_is_active=vision_is_active,
                    enable_som_visual_browsing=self.agent_config.enable_som_visual_browsing,
                    current_index=i,
                    events=events,
                )
            else:
                raise ValueError(f'Unknown event type: {type(event)}')

            # 检查等待中的tool call action消息，查看是否已完成
            _response_ids_to_remove = []
            for (
                    response_id,
                    pending_message,
            ) in pending_tool_call_action_messages.items():
                assert pending_message.tool_calls is not None, (
                    'Tool calls should NOT be None when function calling is enabled & the message is considered pending tool call. '
                    f'Pending message: {pending_message}'
                )
                # 检查所有tool call是否都有对应的结果
                if all(
                        tool_call.id in tool_call_id_to_message
                        for tool_call in pending_message.tool_calls
                ):
                    # 如果完成：
                    # -- 1. 添加发起tool calls的消息
                    messages_to_add.append(pending_message)
                    # -- 2. 添加tool calls的结果
                    for tool_call in pending_message.tool_calls:
                        messages_to_add.append(tool_call_id_to_message[tool_call.id])
                        tool_call_id_to_message.pop(tool_call.id)
                    _response_ids_to_remove.append(response_id)
            # 清理已处理的等待消息
            for response_id in _response_ids_to_remove:
                pending_tool_call_action_messages.pop(response_id)

            # 将当前处理的消息添加到总列表中
            messages += messages_to_add

        # 应用最终过滤，确保上下文中的消息没有不匹配的tool calls和tool responses
        messages = list(ConversationMemory._filter_unmatched_tool_calls(messages))

        # 应用最终格式化
        messages = self._apply_user_message_formatting(messages)

        return messages

    def _apply_user_message_formatting(self, messages: list[Message]) -> list[Message]:
        """应用格式化规则，例如在连续的用户消息之间添加换行符。

        Args:
            messages: 待格式化的消息列表

        Returns:
            list[Message]: 格式化后的消息列表
        """
        formatted_messages = []
        prev_role = None  # 记录前一条消息的角色

        for msg in messages:
            # 在连续的用户消息之间添加双换行符，以提供视觉分隔
            if msg.role == 'user' and prev_role == 'user' and len(msg.content) > 0:
                # 找到消息中的第一个TextContent来添加换行符
                for content_item in msg.content:
                    if isinstance(content_item, TextContent):
                        # 前置两个换行符以确保视觉分隔
                        content_item.text = '\n\n' + content_item.text
                        break
            formatted_messages.append(msg)
            prev_role = msg.role  # 处理每条消息后更新prev_role
        return formatted_messages

    def _process_action(
            self,
            action: Action,
            pending_tool_call_action_messages: dict[str, Message],
            vision_is_active: bool = False,
    ) -> list[Message]:
        """将Action转换为可发送给LLM的消息格式。

        该方法处理不同类型的Action并进行适当的格式化：
        1. 对于基于工具的Action（AgentDelegate、CmdRun、IPythonRunCell、FileEdit）和Agent来源的AgentFinish：
            - 在function calling模式下：将LLM的响应存储在pending_tool_call_action_messages中
            - 在非function calling模式下：创建包含action字符串的消息
        2. 对于MessageAction：创建包含文本内容和可选图片内容的消息

        Args:
            action: 待转换的Action对象，可以是以下类型之一：
                - CmdRunAction: 执行bash命令
                - IPythonRunCellAction: 运行IPython代码
                - FileEditAction: 编辑文件
                - FileReadAction: 使用openhands-aci命令读取文件
                - BrowseInteractiveAction: 浏览网页
                - AgentFinishAction: 结束交互
                - MessageAction: 发送消息
                - MCPAction: 与MCP服务器交互
            pending_tool_call_action_messages: 将response ID映射到相应消息的字典。
                在function calling模式下用于跟踪等待结果的tool calls。
            vision_is_active: LLM中是否激活了视觉功能。如果为True，将包含图片URL

        Returns:
            list[Message]: 包含该Action格式化消息的列表。
                在function calling模式下，如果Action被处理为tool call，可能为空。

        Note:
            在function calling模式下，基于工具的Action被存储在pending_tool_call_action_messages中
            而不是立即返回。它们将在所有对应的tool call结果可用时稍后处理。
        """
        # 从事件创建常规消息
        if isinstance(
                action,
                (
                        AgentDelegateAction,
                        AgentThinkAction,
                        IPythonRunCellAction,
                        FileEditAction,
                        FileReadAction,
                        BrowseInteractiveAction,
                        BrowseURLAction,
                        MCPAction,
                ),
        ) or (isinstance(action, CmdRunAction) and action.source == 'agent'):
            # 处理需要tool call metadata的Action类型
            tool_metadata = action.tool_call_metadata
            assert tool_metadata is not None, (
                    'Tool call metadata should NOT be None when function calling is enabled. Action: '
                    + str(action)
            )

            # 获取LLM的响应数据
            llm_response: ModelResponse = tool_metadata.model_response
            assistant_msg = getattr(llm_response.choices[0], 'message')

            # 添加发起tool calls的LLM消息（assistant）
            # （覆盖任何具有相同response_id的先前消息）
            logger.debug(
                f'Tool calls type: {type(assistant_msg.tool_calls)}, value: {assistant_msg.tool_calls}'
            )
            # 将消息存储到等待处理的字典中
            pending_tool_call_action_messages[llm_response.id] = Message(
                role=getattr(assistant_msg, 'role', 'assistant'),
                # tool call内容应该是字符串
                content=[TextContent(text=assistant_msg.content)]
                if assistant_msg.content and assistant_msg.content.strip()
                else [],
                tool_calls=assistant_msg.tool_calls,
            )
            return []  # function calling模式下不立即返回消息
        elif isinstance(action, AgentFinishAction):
            # 处理Agent完成Action
            role = 'user' if action.source == 'user' else 'assistant'

            # 当Agent完成时，它有tool_metadata已经被执行，但没有响应
            # 当用户完成时（/exit），我们没有tool_metadata
            tool_metadata = action.tool_call_metadata
            if tool_metadata is not None:
                # 从tool call获取响应消息
                assistant_msg = getattr(
                    tool_metadata.model_response.choices[0], 'message'
                )
                content = assistant_msg.content or ''

                # 如果有内容，保存到thought中
                if action.thought:
                    if action.thought != content:
                        action.thought += '\n' + content
                else:
                    action.thought = content

                # 移除tool call metadata
                action.tool_call_metadata = None
            # 验证角色的有效性
            if role not in ('user', 'system', 'assistant', 'tool'):
                raise ValueError(f'Invalid role: {role}')
            return [
                Message(
                    role=role,  # type: ignore[arg-type]
                    content=[TextContent(text=action.thought)],
                )
            ]
        elif isinstance(action, MessageAction):
            # 处理消息Action
            role = 'user' if action.source == 'user' else 'assistant'
            content = [TextContent(text=action.content or '')]

            # 如果启用视觉功能且有图片URL，添加图片内容
            if vision_is_active and action.image_urls:
                if role == 'user':
                    # 用户消息：为每个图片添加标题
                    for idx, url in enumerate(action.image_urls):
                        content.append(TextContent(text=f'Image {idx + 1}:'))
                        content.append(ImageContent(image_urls=[url]))
                else:
                    # Assistant消息：直接添加图片
                    content.append(ImageContent(image_urls=action.image_urls))
            # 验证角色的有效性
            if role not in ('user', 'system', 'assistant', 'tool'):
                raise ValueError(f'Invalid role: {role}')
            return [
                Message(
                    role=role,  # type: ignore[arg-type]
                    content=content,
                )
            ]
        elif isinstance(action, CmdRunAction) and action.source == 'user':
            # 处理用户执行的命令Action
            content = [
                TextContent(text=f'User executed the command:\n{action.command}')
            ]
            return [
                Message(
                    role='user',  # CmdRunAction始终是用户角色
                    content=content,
                )
            ]
        elif isinstance(action, SystemMessageAction):
            # 将SystemMessageAction转换为系统消息
            return [
                Message(
                    role='system',
                    content=[TextContent(text=action.content)],
                    # 如果启用function calling，包含工具
                    tool_calls=None,
                )
            ]
        return []  # 其他情况返回空列表

    def _process_observation(
            self,
            obs: Observation,
            tool_call_id_to_message: dict[str, Message],
            max_message_chars: int | None = None,
            vision_is_active: bool = False,
            enable_som_visual_browsing: bool = False,
            current_index: int = 0,
            events: list[Event] | None = None,
    ) -> list[Message]:
        """将Observation转换为可发送给LLM的消息格式。

        该方法处理不同类型的Observation并进行适当格式化：
        - CmdOutputObservation: 格式化命令执行结果和退出码
        - IPythonRunCellObservation: 格式化IPython单元执行结果，替换base64图片
        - FileEditObservation: 格式化文件编辑结果
        - FileReadObservation: 格式化来自openhands-aci的文件读取结果
        - AgentDelegateObservation: 格式化来自委托Agent任务的结果
        - ErrorObservation: 格式化失败Action的错误消息
        - UserRejectObservation: 格式化用户拒绝消息
        - FileDownloadObservation: 格式化浏览Action中打开/下载文件的结果

        在function calling模式下，带有tool_call_metadata的Observation被存储在
        tool_call_id_to_message中供后续处理，而不是立即返回。

        Args:
            obs: 待转换的Observation对象
            tool_call_id_to_message: 将tool call ID映射到相应消息的字典（用于function calling模式）
            max_message_chars: Observation内容包含在LLM提示中的最大字符数
            vision_is_active: LLM中是否激活了视觉功能。如果为True，将包含图片URL
            enable_som_visual_browsing: 是否为SOM模型启用视觉浏览
            current_index: 当前事件在事件列表中的索引（用于去重）
            events: 所有事件的列表（用于去重）

        Returns:
            list[Message]: 包含该Observation格式化消息的列表。
                在function calling模式下，如果Observation被处理为tool response，可能为空。

        Raises:
            ValueError: 如果Observation类型未知
        """
        message: Message

        if isinstance(obs, CmdOutputObservation):
            # 处理命令输出Observation
            # 如果没有tool call metadata，说明是由用户Action触发的
            if obs.tool_call_metadata is None:
                text = truncate_content(
                    f'\nObserved result of command executed by user:\n{obs.to_agent_observation()}',
                    max_message_chars,
                )
            else:
                text = truncate_content(obs.to_agent_observation(), max_message_chars)
            message = Message(role='user', content=[TextContent(text=text)])
        elif isinstance(obs, MCPObservation):
            # 处理MCP Observation
            # logger.warning(f'MCPObservation: {obs}')
            text = truncate_content(obs.content, max_message_chars)
            message = Message(role='user', content=[TextContent(text=text)])
        elif isinstance(obs, IPythonRunCellObservation):
            # 处理IPython运行单元Observation
            text = obs.content
            # 清理文本内容中剩余的base64图片
            splitted = text.split('\n')
            for i, line in enumerate(splitted):
                if '![image](data:image/png;base64,' in line:
                    splitted[i] = (
                        '![image](data:image/png;base64, ...) already displayed to user'
                    )
            text = '\n'.join(splitted)
            text = truncate_content(text, max_message_chars)

            # 创建包含文本的消息内容
            content: list[TextContent | ImageContent] = [TextContent(text=text)]

            # 如果可用且启用视觉功能，添加图片URL
            if vision_is_active and obs.image_urls:
                # 过滤掉空的或无效的图片URL
                valid_image_urls = [
                    url for url in obs.image_urls if self._is_valid_image_url(url)
                ]
                invalid_count = len(obs.image_urls) - len(valid_image_urls)

                if valid_image_urls:
                    content.append(ImageContent(image_urls=valid_image_urls))
                    if invalid_count > 0:
                        # 添加文本指示某些图片被过滤
                        content[
                            0
                        ].text += f'\n\nNote: {invalid_count} invalid or empty image(s) were filtered from this output. The agent may need to use alternative methods to access visual information.'  # type: ignore[union-attr]
                else:
                    logger.debug(
                        'IPython observation has image URLs but none are valid'
                    )
                    # 添加文本指示所有图片都被过滤
                    content[
                        0
                    ].text += f'\n\nNote: All {len(obs.image_urls)} image(s) in this output were invalid or empty and have been filtered. The agent should use alternative methods to access visual information.'  # type: ignore[union-attr]

            message = Message(role='user', content=content)
        elif isinstance(obs, FileEditObservation):
            # 处理文件编辑Observation
            text = truncate_content(str(obs), max_message_chars)
            message = Message(role='user', content=[TextContent(text=text)])
        elif isinstance(obs, FileReadObservation):
            # 处理文件读取Observation
            message = Message(
                role='user', content=[TextContent(text=obs.content)]
            )  # 内容已经由openhands-aci截断
        elif isinstance(obs, BrowserOutputObservation):
            # 处理浏览器输出Observation
            text = obs.content
            # 如果是交互式浏览且启用了SOM视觉浏览和视觉功能
            if (
                    obs.trigger_by_action == ActionType.BROWSE_INTERACTIVE
                    and enable_som_visual_browsing
                    and vision_is_active
            ):
                text += 'Image: Current webpage screenshot (Note that only visible portion of webpage is present in the screenshot. However, the Accessibility tree contains information from the entire webpage.)\n'

                # 确定使用哪个图片并验证它
                image_url = None
                if obs.set_of_marks is not None and len(obs.set_of_marks) > 0:
                    image_url = obs.set_of_marks
                    image_type = 'set of marks'
                elif obs.screenshot is not None and len(obs.screenshot) > 0:
                    image_url = obs.screenshot
                    image_type = 'screenshot'

                # 创建包含文本的消息内容
                content = [TextContent(text=text)]

                # 仅在有有效图片URL时添加ImageContent
                if self._is_valid_image_url(image_url):
                    content.append(ImageContent(image_urls=[image_url]))  # type: ignore[list-item]
                    logger.debug(f'Vision enabled for browsing, showing {image_type}')
                else:
                    if image_url:
                        logger.warning(
                            f'Invalid image URL format for {image_type}: {image_url[:50]}...'
                        )
                        # 添加文本指示图片被过滤
                        content[
                            0
                        ].text += f'\n\nNote: The {image_type} for this webpage was invalid or empty and has been filtered. The agent should use alternative methods to access visual information about the webpage.'  # type: ignore[union-attr]
                    else:
                        logger.debug(
                            'Vision enabled for browsing, but no valid image available'
                        )
                        # 添加文本指示没有可用图片
                        content[
                            0
                        ].text += '\n\nNote: No visual information (screenshot or set of marks) is available for this webpage. The agent should rely on the text content above.'  # type: ignore[union-attr]

                message = Message(role='user', content=content)
            else:
                # 不使用视觉功能的情况
                message = Message(
                    role='user',
                    content=[TextContent(text=text)],
                )
                logger.debug('Vision disabled for browsing, showing text')
        elif isinstance(obs, AgentDelegateObservation):
            # 处理Agent委托Observation
            text = truncate_content(
                obs.outputs.get('content', obs.content),
                max_message_chars,
            )
            message = Message(role='user', content=[TextContent(text=text)])
        elif isinstance(obs, AgentThinkObservation):
            # 处理Agent思考Observation
            text = truncate_content(obs.content, max_message_chars)
            message = Message(role='user', content=[TextContent(text=text)])
        elif isinstance(obs, ErrorObservation):
            # 处理错误Observation
            text = truncate_content(obs.content, max_message_chars)
            text += '\n[Error occurred in processing last action]'
            message = Message(role='user', content=[TextContent(text=text)])
        elif isinstance(obs, UserRejectObservation):
            # 处理用户拒绝Observation
            text = 'OBSERVATION:\n' + truncate_content(obs.content, max_message_chars)
            text += '\n[Last action has been rejected by the user]'
            message = Message(role='user', content=[TextContent(text=text)])
        elif isinstance(obs, AgentCondensationObservation):
            # 处理Agent压缩Observation
            text = truncate_content(obs.content, max_message_chars)
            message = Message(role='user', content=[TextContent(text=text)])
        elif isinstance(obs, FileDownloadObservation):
            # 处理文件下载Observation
            text = truncate_content(obs.content, max_message_chars)
            message = Message(role='user', content=[TextContent(text=text)])
        elif (
                isinstance(obs, RecallObservation)
                and self.agent_config.enable_prompt_extensions
        ):
            # 处理回忆Observation（当启用提示扩展时）
            if obs.recall_type == RecallType.WORKSPACE_CONTEXT:
                # 所有内容都是可选的，检查是否存在
                if obs.repo_name or obs.repo_directory:
                    repo_info = RepositoryInfo(
                        repo_name=obs.repo_name or '',
                        repo_directory=obs.repo_directory or '',
                    )
                else:
                    repo_info = None

                date = obs.date

                if obs.runtime_hosts or obs.additional_agent_instructions:
                    runtime_info = RuntimeInfo(
                        available_hosts=obs.runtime_hosts,
                        additional_agent_instructions=obs.additional_agent_instructions,
                        date=date,
                        custom_secrets_descriptions=obs.custom_secrets_descriptions,
                    )
                else:
                    runtime_info = RuntimeInfo(
                        date=date,
                        custom_secrets_descriptions=obs.custom_secrets_descriptions,
                    )

                conversation_instructions = None

                if obs.conversation_instructions:
                    conversation_instructions = ConversationInstructions(
                        content=obs.conversation_instructions
                    )

                repo_instructions = (
                    obs.repo_instructions if obs.repo_instructions else ''
                )

                # 在调用模板之前确保有一些有意义的内容
                has_repo_info = repo_info is not None and (
                        repo_info.repo_name or repo_info.repo_directory
                )
                has_runtime_info = runtime_info is not None and (
                        runtime_info.date or runtime_info.custom_secrets_descriptions
                )
                has_repo_instructions = bool(repo_instructions.strip())
                has_conversation_instructions = conversation_instructions is not None

                # 过滤和处理MicroAgent知识
                filtered_agents = []
                if obs.microagent_knowledge:
                    # 排除已禁用的MicroAgent
                    filtered_agents = [
                        agent
                        for agent in obs.microagent_knowledge
                        if agent.name not in self.agent_config.disabled_microagents
                    ]

                has_microagent_knowledge = bool(filtered_agents)

                # 根据存在的内容生成适当的内容
                message_content: list[TextContent | ImageContent] = []

                # 构建Workspace上下文信息
                if (
                        has_repo_info
                        or has_runtime_info
                        or has_repo_instructions
                        or has_conversation_instructions
                ):
                    formatted_workspace_text = (
                        self.prompt_manager.build_workspace_context(
                            repository_info=repo_info,
                            runtime_info=runtime_info,
                            conversation_instructions=conversation_instructions,
                            repo_instructions=repo_instructions,
                        )
                    )
                    message_content.append(TextContent(text=formatted_workspace_text))

                # 如果存在，添加MicroAgent知识
                if has_microagent_knowledge:
                    formatted_microagent_text = (
                        self.prompt_manager.build_microagent_info(
                            triggered_agents=filtered_agents,
                        )
                    )
                    message_content.append(TextContent(text=formatted_microagent_text))

                # 如果有任何内容，返回组合消息
                if message_content:
                    message = Message(role='user', content=message_content)
                else:
                    return []
            elif obs.recall_type == RecallType.KNOWLEDGE:
                # 使用prompt manager构建MicroAgent信息
                # 首先，过滤出现在较早RecallObservation中的Agent
                filtered_agents = self._filter_agents_in_microagent_obs(
                    obs, current_index, events or []
                )

                # 如果有要包含的MicroAgent知识，创建并返回消息
                if filtered_agents:
                    # 排除已禁用的MicroAgent
                    filtered_agents = [
                        agent
                        for agent in filtered_agents
                        if agent.name not in self.agent_config.disabled_microagents
                    ]

                    # 仅在过滤掉禁用Agent后仍有Agent时才继续
                    if filtered_agents:
                        formatted_text = self.prompt_manager.build_microagent_info(
                            triggered_agents=filtered_agents,
                        )

                        return [
                            Message(
                                role='user', content=[TextContent(text=formatted_text)]
                            )
                        ]

                # 如果没有要包含的MicroAgent或全部被禁用，返回空列表
                return []
        elif (
                isinstance(obs, RecallObservation)
                and not self.agent_config.enable_prompt_extensions
        ):
            # 如果禁用了提示扩展，我们不添加任何额外信息
            # TODO: 测试这个
            return []
        else:
            # 如果不返回Observation消息，当LLM尝试返回下一条消息时会导致错误
            raise ValueError(f'Unknown observation type: {type(obs)}')

        # 在function calling模式下正确更新消息作为tool response
        if (tool_call_metadata := getattr(obs, 'tool_call_metadata', None)) is not None:
            tool_call_id_to_message[tool_call_metadata.tool_call_id] = Message(
                role='tool',
                content=message.content,
                tool_call_id=tool_call_metadata.tool_call_id,
                name=tool_call_metadata.function_name,
            )
            # 不需要返回Observation消息
            # 因为当同一请求中的所有对应tool calls被处理时，
            # 它将被get_action_message添加
            return []

        return [message]

    def apply_prompt_caching(self, messages: list[Message]) -> None:
        """为消息应用缓存断点。

        对于新的Anthropic API，我们只需要将最后一条用户或工具消息标记为可缓存。
        这可以显著提高性能并减少API调用成本。

        Args:
            messages: 待应用缓存的消息列表
        """
        # 如果有系统消息，标记其为可缓存
        if len(messages) > 0 and messages[0].role == 'system':
            messages[0].content[-1].cache_prompt = True
        # 注意：这仅对anthropic需要
        # 从后往前找到最后一条用户或工具消息并标记为可缓存
        for message in reversed(messages):
            if message.role in ('user', 'tool'):
                message.content[
                    -1
                ].cache_prompt = True  # 消息内容中的最后一项
                break

    def _filter_agents_in_microagent_obs(
            self, obs: RecallObservation, current_index: int, events: list[Event]
    ) -> list[MicroagentKnowledge]:
        """过滤出现在较早RecallObservation中的Agent。

        Args:
            obs: 待过滤的当前RecallObservation
            current_index: 当前事件在事件列表中的索引
            events: 所有事件的列表

        Returns:
            list[MicroagentKnowledge]: 过滤后的MicroAgent知识列表
        """
        if obs.recall_type != RecallType.KNOWLEDGE:
            return obs.microagent_knowledge

        # 对于当前MicroAgent Observation中的每个Agent，
        # 检查它是否出现在任何较早的MicroAgent Observation中
        filtered_agents = []
        for agent in obs.microagent_knowledge:
            # 如果此Agent不出现在任何较早的Observation中，则保留它
            # 即，如果这是第一个包含此MicroAgent的MicroAgent Observation
            if not self._has_agent_in_earlier_events(agent.name, current_index, events):
                filtered_agents.append(agent)

        return filtered_agents

    def _has_agent_in_earlier_events(
            self, agent_name: str, current_index: int, events: list[Event]
    ) -> bool:
        """检查Agent是否出现在事件列表中任何较早的RecallObservation中。

        Args:
            agent_name: 要查找的Agent名称
            current_index: 当前事件在事件列表中的索引
            events: 所有事件的列表

        Returns:
            bool: 如果Agent出现在较早的RecallObservation中返回True，否则返回False
        """
        # 遍历当前索引之前的所有事件
        for event in events[:current_index]:
            # 注意：这个检查包括WORKSPACE_CONTEXT
            if isinstance(event, RecallObservation):
                if any(
                        agent.name == agent_name for agent in event.microagent_knowledge
                ):
                    return True
        return False

    @staticmethod
    def _filter_unmatched_tool_calls(
            messages: list[Message],
    ) -> Generator[Message, None, None]:
        """过滤掉没有匹配tool response的tool call，反之亦然。

        这确保tool消息中的每个tool_call_id在assistant消息中都有对应的tool_calls[].id，
        反之亦然。原始列表未修改，当更新tool_calls时消息被复制。

        此方法不会移除id设置为None的项目。

        Args:
            messages: 待过滤的消息列表

        Yields:
            Message: 过滤后的有效消息
        """
        # 收集所有assistant消息中的tool call ID
        tool_call_ids = {
            tool_call.id
            for message in messages
            if message.tool_calls
            for tool_call in message.tool_calls
            if message.role == 'assistant' and tool_call.id
        }
        # 收集所有tool消息中的tool response ID
        tool_response_ids = {
            message.tool_call_id
            for message in messages
            if message.role == 'tool' and message.tool_call_id
        }

        for message in messages:
            # 移除没有匹配assistant tool call的tool消息
            if message.role == 'tool' and message.tool_call_id:
                if message.tool_call_id in tool_call_ids:
                    yield message

            # 移除没有匹配tool response的assistant tool call
            elif message.role == 'assistant' and message.tool_calls:
                all_tool_calls_match = all(
                    tool_call.id in tool_response_ids
                    for tool_call in message.tool_calls
                )
                if all_tool_calls_match:
                    yield message
                else:
                    # 只保留有匹配响应的tool call
                    matched_tool_calls = [
                        tool_call
                        for tool_call in message.tool_calls
                        if tool_call.id in tool_response_ids
                    ]

                    if matched_tool_calls:
                        # 如果还有tool call剩余，保留更新后的消息
                        yield message.model_copy(
                            update={'tool_calls': matched_tool_calls}
                        )
            else:
                # 任何其他情况都保留
                yield message

    def _ensure_system_message(self, events: list[Event]) -> None:
        """检查是否存在SystemMessageAction，如果不存在则添加一个（用于向后兼容）。

        Args:
            events: 事件列表，将在必要时被修改
        """
        # 检查事件中是否有SystemMessageAction
        has_system_message = any(
            isinstance(event, SystemMessageAction) for event in events
        )

        # 向后兼容行为：如果未找到SystemMessageAction，添加一个
        if not has_system_message:
            logger.debug(
                '[ConversationMemory] No SystemMessageAction found in events. '
                'Adding one for backward compatibility. '
            )
            system_prompt = self.prompt_manager.get_system_message()
            if system_prompt:
                system_message = SystemMessageAction(content=system_prompt)
                # 直接在事件列表的开头插入系统消息
                events.insert(0, system_message)
                logger.info(
                    '[ConversationMemory] Added SystemMessageAction for backward compatibility'
                )

    def _ensure_initial_user_message(
            self, events: list[Event], initial_user_action: MessageAction
    ) -> None:
        """检查第二个事件是否为用户MessageAction，如果需要则插入提供的Action。

        Args:
            events: 事件列表，将在必要时被修改
            initial_user_action: 初始用户消息Action，用于确保对话正确开始
        """
        if (
                not events
        ):  # 应该在前一步有系统消息，但进行安全检查
            logger.error('Cannot ensure initial user message: event list is empty.')
            # 或者抛出异常？现在先记录日志，_ensure_system_message应该处理这个问题
            return

        # 我们期望events[0]在_ensure_system_message之后是SystemMessageAction
        if len(events) == 1:
            # 只存在系统消息
            logger.info(
                'Initial user message action was missing. Inserting the initial user message.'
            )
            events.insert(1, initial_user_action)
        elif not isinstance(events[1], MessageAction) or events[1].source != 'user':
            # 第二个事件存在但不是正确的初始用户消息Action
            # 我们将插入提供的正确Action
            logger.info(
                'Second event was not the initial user message action. Inserting correct one at index 1.'
            )

            # 在索引1处插入用户消息事件。这将是LLM API期望的第二条消息
            # 但历史记录有问题，所以记录我们能记录的所有内容
            events.insert(1, initial_user_action)

        # 否则：events[1]已经是用户MessageAction
        # 检查它是否与提供的匹配（如果有任何差异，记录警告但继续）
        elif events[1] != initial_user_action:
            logger.debug(
                'The user MessageAction at index 1 does not match the provided initial_user_action. '
                'Proceeding with the one found in condensed history.'
            )
