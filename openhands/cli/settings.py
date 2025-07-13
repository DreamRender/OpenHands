from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.completion import FuzzyWordCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.shortcuts import print_container
from prompt_toolkit.widgets import Frame, TextArea
from pydantic import SecretStr

from openhands.cli.tui import (
    COLOR_GREY,
    UserCancelledError,
    cli_confirm,
    kb_cancel,
)
from openhands.cli.utils import (
    VERIFIED_ANTHROPIC_MODELS,
    VERIFIED_MISTRAL_MODELS,
    VERIFIED_OPENAI_MODELS,
    VERIFIED_PROVIDERS,
    organize_models_and_providers,
)
from openhands.controller.agent import Agent
from openhands.core.config import OpenHandsConfig
from openhands.core.config.condenser_config import NoOpCondenserConfig
from openhands.core.config.utils import OH_DEFAULT_AGENT
from openhands.memory.condenser.impl.llm_summarizing_condenser import (
    LLMSummarizingCondenserConfig,
)
from openhands.storage.data_models.settings import Settings
from openhands.storage.settings.file_settings_store import FileSettingsStore
from openhands.utils.llm import get_supported_llm_models


def display_settings(config: OpenHandsConfig) -> None:
    """
    显示当前设置信息。
    
    以表格形式展示LLM配置、Agent设置、确认模式和内存压缩等信息。
    
    Args:
        config: OpenHands配置对象
    """
    llm_config = config.get_llm_config()
    # 判断是否使用高级LLM设置（有自定义base_url的为高级设置）
    advanced_llm_settings = True if llm_config.base_url else False

    # 根据设置类型准备标签和值
    labels_and_values = []
    if not advanced_llm_settings:
        # 基础设置：显示提供商、模型和API密钥
        # 尝试确定提供商，如果不能直接获取则回退处理
        provider = getattr(
            llm_config,
            'provider',
            llm_config.model.split('/')[0] if '/' in llm_config.model else 'Unknown',
        )
        labels_and_values.extend(
            [
                ('   LLM Provider', str(provider)),
                ('   LLM Model', str(llm_config.model)),
                ('   API Key', '********' if llm_config.api_key else 'Not Set'),
            ]
        )
    else:
        # 高级设置：显示自定义模型、Base URL和API密钥
        labels_and_values.extend(
            [
                ('   Custom Model', str(llm_config.model)),
                ('   Base URL', str(llm_config.base_url)),
                ('   API Key', '********' if llm_config.api_key else 'Not Set'),
            ]
        )

    # 通用设置
    labels_and_values.extend(
        [
            ('   Agent', str(config.default_agent)),
            (
                '   Confirmation Mode',
                'Enabled' if config.security.confirmation_mode else 'Disabled',
            ),
            (
                '   Memory Condensation',
                'Enabled' if config.enable_default_condenser else 'Disabled',
            ),
        ]
    )

    # 计算对齐的最大宽度
    # 确保值是字符串类型以便len()计算
    str_labels_and_values = [(label, str(value)) for label, value in labels_and_values]
    max_label_width = (
        max(len(label) for label, _ in str_labels_and_values)
        if str_labels_and_values
        else 0
    )

    # 构建带对齐列的摘要文本
    settings_lines = [
        f'{label + ":":<{max_label_width + 1}} {value:<}'  # 改为左对齐值 (<)
        for label, value in str_labels_and_values
    ]
    settings_text = '\n'.join(settings_lines)

    # 创建设置显示容器
    container = Frame(
        TextArea(
            text=settings_text,
            read_only=True,
            style=COLOR_GREY,
            wrap_lines=True,
        ),
        title='Settings',
        style=f'fg:{COLOR_GREY}',
    )

    print_container(container)


async def get_validated_input(
    session: PromptSession,
    prompt_text: str,
    completer=None,
    validator=None,
    error_message: str = 'Input cannot be empty',
) -> str:
    """
    获取经过验证的用户输入。
    
    持续提示用户输入直到输入通过验证。支持自动补全和自定义验证器。
    
    Args:
        session: PromptSession对象，用于处理用户交互
        prompt_text: 提示文本
        completer: 可选的自动补全器
        validator: 可选的验证函数，接受输入值并返回布尔值
        error_message: 验证失败时显示的错误消息
        
    Returns:
        str: 验证通过的用户输入
    """
    session.completer = completer
    value = None

    while True:
        # 异步获取用户输入
        value = await session.prompt_async(prompt_text)

        if validator:
            # 如果有自定义验证器，使用它验证输入
            is_valid = validator(value)
            if not is_valid:
                print_formatted_text('')
                print_formatted_text(HTML(f'<grey>{error_message}: {value}</grey>'))
                print_formatted_text('')
                continue
        elif not value:
            # 默认验证：输入不能为空
            print_formatted_text('')
            print_formatted_text(HTML(f'<grey>{error_message}</grey>'))
            print_formatted_text('')
            continue

        break  # 验证通过，退出循环

    return value


def save_settings_confirmation(config: OpenHandsConfig) -> bool:
    """
    显示保存设置确认对话框。
    
    Args:
        config: OpenHands配置对象
        
    Returns:
        bool: 用户确认保存返回True，否则返回False
    """
    return (
        cli_confirm(
            config,
            '\nSave new settings? (They will take effect after restart)',
            ['Yes, save', 'No, discard'],
        )
        == 0  # 0表示选择了第一个选项（Yes, save）
    )


async def modify_llm_settings_basic(
    config: OpenHandsConfig, settings_store: FileSettingsStore
) -> None:
    """
    修改基础LLM设置。
    
    引导用户选择LLM提供商、模型和API密钥。支持已验证的提供商
    优先级排序和模糊搜索补全。
    
    Args:
        config: OpenHands配置对象
        settings_store: 设置存储对象
    """
    # 获取支持的模型列表并按提供商组织
    model_list = get_supported_llm_models(config)
    organized_models = organize_models_and_providers(model_list)

    # 获取提供商列表，将已验证的提供商排在前面
    provider_list = list(organized_models.keys())
    verified_providers = [p for p in VERIFIED_PROVIDERS if p in provider_list]
    provider_list = [p for p in provider_list if p not in verified_providers]
    provider_list = verified_providers + provider_list

    # 创建提供商自动补全器和提示会话
    provider_completer = FuzzyWordCompleter(provider_list)
    session = PromptSession(key_bindings=kb_cancel())

    # 设置默认提供商 - 优先选择'anthropic'，否则使用第一个
    provider = 'anthropic' if 'anthropic' in provider_list else provider_list[0]
    model = None  # 选择的模型
    api_key = None  # API密钥

    try:
        # 显示默认提供商但允许更改
        print_formatted_text(
            HTML(f'\n<grey>Default provider: </grey><green>{provider}</green>')
        )

        # 显示已验证的提供商加上"选择其他提供商"选项
        provider_choices = verified_providers + ['Select another provider']
        provider_choice = cli_confirm(
            config,
            '(Step 1/3) Select LLM Provider:',
            provider_choices,
        )

        # 确保provider_choice是整数（用于测试兼容性）
        try:
            choice_index = int(provider_choice)
        except (TypeError, ValueError):
            # 如果转换失败（例如在测试中使用mock），默认为0
            choice_index = 0

        if choice_index < len(verified_providers):
            # 用户选择了已验证的提供商之一
            provider = verified_providers[choice_index]
        else:
            # 用户选择了"选择其他提供商" - 使用手动选择
            # 定义打印错误消息的验证函数
            def provider_validator(x):
                is_valid = x in organized_models
                if not is_valid:
                    print_formatted_text(
                        HTML('<grey>Invalid provider selected: {}</grey>'.format(x))
                    )
                return is_valid

            provider = await get_validated_input(
                session,
                '(Step 1/3) Select LLM Provider (TAB for options, CTRL-c to cancel): ',
                completer=provider_completer,
                validator=provider_validator,
                error_message='Invalid provider selected',
            )

        # 确保提供商存在于organized_models中
        if provider not in organized_models:
            # 如果提供商不存在，优先选择'anthropic'（如果可用），
            # 否则使用第一个提供商
            provider = (
                'anthropic'
                if 'anthropic' in organized_models
                else next(iter(organized_models.keys()))
            )

        # 获取提供商的模型列表，并将已验证的模型排在前面
        provider_models = organized_models[provider]['models']
        if provider == 'openai':
            provider_models = [
                m for m in provider_models if m not in VERIFIED_OPENAI_MODELS
            ]
            provider_models = VERIFIED_OPENAI_MODELS + provider_models
        if provider == 'anthropic':
            provider_models = [
                m for m in provider_models if m not in VERIFIED_ANTHROPIC_MODELS
            ]
            provider_models = VERIFIED_ANTHROPIC_MODELS + provider_models
        if provider == 'mistral':
            provider_models = [
                m for m in provider_models if m not in VERIFIED_MISTRAL_MODELS
            ]
            provider_models = VERIFIED_MISTRAL_MODELS + provider_models

        # 为提供商设置默认模型（最佳已验证模型）
        if provider == 'anthropic' and VERIFIED_ANTHROPIC_MODELS:
            # 使用VERIFIED_ANTHROPIC_MODELS列表中的第一个模型，因为它是最佳/最新的
            default_model = VERIFIED_ANTHROPIC_MODELS[0]
        elif provider == 'openai' and VERIFIED_OPENAI_MODELS:
            # 使用VERIFIED_OPENAI_MODELS列表中的第一个模型，因为它是最佳/最新的
            default_model = VERIFIED_OPENAI_MODELS[0]
        elif provider == 'mistral' and VERIFIED_MISTRAL_MODELS:
            # 使用VERIFIED_MISTRAL_MODELS列表中的第一个模型，因为它是最佳/最新的
            default_model = VERIFIED_MISTRAL_MODELS[0]
        else:
            # 对于其他提供商，使用列表中的第一个模型
            default_model = (
                provider_models[0] if provider_models else 'claude-sonnet-4-20250514'
            )

        # 显示默认模型但允许更改
        print_formatted_text(
            HTML(f'\n<grey>Default model: </grey><green>{default_model}</green>')
        )
        change_model = (
            cli_confirm(
                config,
                'Do you want to use a different model?',
                [f'Use {default_model}', 'Select another model'],
            )
            == 1  # 1表示选择了第二个选项（Select another model）
        )

        if change_model:
            # 用户选择更改模型
            model_completer = FuzzyWordCompleter(provider_models)

            # 定义允许自定义模型但显示警告的验证函数
            def model_validator(x):
                # 允许任何非空模型名称
                if not x.strip():
                    return False

                # 对不在预定义列表中的模型显示警告，但仍然允许它们
                if x not in provider_models:
                    print_formatted_text(
                        HTML(
                            f'<yellow>Warning: {x} is not in the predefined list for provider {provider}. '
                            f'Make sure this model name is correct.</yellow>'
                        )
                    )
                return True

            model = await get_validated_input(
                session,
                '(Step 2/3) Select LLM Model (TAB for options, CTRL-c to cancel): ',
                completer=model_completer,
                validator=model_validator,
                error_message='Model name cannot be empty',
            )
        else:
            # 使用默认模型
            model = default_model

        # 获取API密钥
        api_key = await get_validated_input(
            session,
            '(Step 3/3) Enter API Key (CTRL-c to cancel): ',
            error_message='API Key cannot be empty',
        )

    except (
        UserCancelledError,
        KeyboardInterrupt,
        EOFError,
    ):
        return  # 发生异常时返回

    # 上面的try-except块确保我们要么有有效输入，要么已经返回了
    # 这里不需要检查None值

    # 确认是否保存设置
    save_settings = save_settings_confirmation(config)

    if not save_settings:
        return

    # 更新LLM配置
    llm_config = config.get_llm_config()
    llm_config.model = f'{provider}{organized_models[provider]["separator"]}{model}'
    llm_config.api_key = SecretStr(api_key)
    llm_config.base_url = None  # 基础设置不使用自定义base_url
    config.set_llm_config(llm_config)

    # 设置默认Agent和启用内存压缩
    config.default_agent = OH_DEFAULT_AGENT
    config.enable_default_condenser = True

    # 配置Agent的Condenser
    agent_config = config.get_agent_config(config.default_agent)
    agent_config.condenser = LLMSummarizingCondenserConfig(
        llm_config=llm_config,
        type='llm',
    )
    config.set_agent_config(agent_config, config.default_agent)

    # 保存设置到存储
    settings = await settings_store.load()
    if not settings:
        settings = Settings()

    settings.llm_model = f'{provider}{organized_models[provider]["separator"]}{model}'
    settings.llm_api_key = SecretStr(api_key)
    settings.llm_base_url = None
    settings.agent = OH_DEFAULT_AGENT
    settings.enable_default_condenser = True

    await settings_store.store(settings)


async def modify_llm_settings_advanced(
    config: OpenHandsConfig, settings_store: FileSettingsStore
) -> None:
    """
    修改高级LLM设置。
    
    允许用户配置自定义模型、Base URL、API密钥、Agent类型、
    确认模式和内存压缩等高级选项。
    
    Args:
        config: OpenHands配置对象
        settings_store: 设置存储对象
    """
    session = PromptSession(key_bindings=kb_cancel())

    # 初始化设置变量
    custom_model = None  # 自定义模型名称
    base_url = None  # 自定义Base URL
    api_key = None  # API密钥
    agent = None  # Agent类型

    try:
        # 步骤1: 获取自定义模型名称
        custom_model = await get_validated_input(
            session,
            '(Step 1/6) Custom Model (CTRL-c to cancel): ',
            error_message='Custom Model cannot be empty',
        )

        # 步骤2: 获取Base URL
        base_url = await get_validated_input(
            session,
            '(Step 2/6) Base URL (CTRL-c to cancel): ',
            error_message='Base URL cannot be empty',
        )

        # 步骤3: 获取API密钥
        api_key = await get_validated_input(
            session,
            '(Step 3/6) API Key (CTRL-c to cancel): ',
            error_message='API Key cannot be empty',
        )

        # 步骤4: 选择Agent类型
        agent_list = Agent.list_agents()
        agent_completer = FuzzyWordCompleter(agent_list)
        agent = await get_validated_input(
            session,
            '(Step 4/6) Agent (TAB for options, CTRL-c to cancel): ',
            completer=agent_completer,
            validator=lambda x: x in agent_list,
            error_message='Invalid agent selected',
        )

        # 步骤5: 确认模式设置
        enable_confirmation_mode = (
            cli_confirm(
                config,
                question='(Step 5/6) Confirmation Mode (CTRL-c to cancel):',
                choices=['Enable', 'Disable'],
            )
            == 0  # 0表示选择了Enable
        )

        # 步骤6: 内存压缩设置
        enable_memory_condensation = (
            cli_confirm(
                config,
                question='(Step 6/6) Memory Condensation (CTRL-c to cancel):',
                choices=['Enable', 'Disable'],
            )
            == 0  # 0表示选择了Enable
        )

    except (
        UserCancelledError,
        KeyboardInterrupt,
        EOFError,
    ):
        return  # 发生异常时返回

    # 上面的try-except块确保我们要么有有效输入，要么已经返回了
    # 这里不需要检查None值

    # 确认是否保存设置
    save_settings = save_settings_confirmation(config)

    if not save_settings:
        return

    # 更新LLM配置
    llm_config = config.get_llm_config()
    llm_config.model = custom_model
    llm_config.base_url = base_url
    llm_config.api_key = SecretStr(api_key)
    config.set_llm_config(llm_config)

    # 设置默认Agent
    config.default_agent = agent

    # 设置确认模式
    config.security.confirmation_mode = enable_confirmation_mode

    # 配置内存压缩
    agent_config = config.get_agent_config(config.default_agent)
    if enable_memory_condensation:
        agent_config.condenser = LLMSummarizingCondenserConfig(
            llm_config=llm_config,
            type='llm',
        )
    else:
        agent_config.condenser = NoOpCondenserConfig(type='noop')
    config.set_agent_config(agent_config)

    # 保存设置到存储
    settings = await settings_store.load()
    if not settings:
        settings = Settings()

    settings.llm_model = custom_model
    settings.llm_api_key = SecretStr(api_key)
    settings.llm_base_url = base_url
    settings.agent = agent
    settings.confirmation_mode = enable_confirmation_mode
    settings.enable_default_condenser = enable_memory_condensation

    await settings_store.store(settings)