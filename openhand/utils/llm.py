import warnings

import httpx

# 忽略litellm库的警告信息，避免在导入时产生不必要的警告输出
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    import litellm

from openhands.core.config import LLMConfig, OpenHandsConfig
from openhands.core.logger import openhands_logger as logger
from openhands.llm import bedrock


def get_supported_llm_models(config: OpenHandsConfig) -> list[str]:
    """获取所有LiteLLM支持的模型列表。

    此函数整合来自LiteLLM和Bedrock的模型，并移除有问题的Bedrock模型。
    同时支持动态发现Ollama本地模型（如果配置了Ollama服务）。

    Args:
        config (OpenHandsConfig): OpenHands配置对象，包含LLM相关的配置信息

    Returns:
        list[str]: 排序后的唯一模型名称列表

    Note:
        - 优先使用LiteLLM的模型列表和成本模型键
        - 如果配置了AWS凭证，会添加Bedrock模型
        - 如果配置了Ollama，会动态获取可用的本地模型
        - 最终返回去重且排序的模型列表
    """
    # 获取LiteLLM支持的所有模型
    # model_list包含预定义模型，model_cost.keys()包含有成本信息的模型
    litellm_model_list = litellm.model_list + list(litellm.model_cost.keys())
    
    # 移除有问题的Bedrock模型ID
    litellm_model_list_without_bedrock = bedrock.remove_error_modelId(
        litellm_model_list
    )
    
    # TODO: 对于bedrock，这里使用的是默认配置
    llm_config: LLMConfig = config.get_llm_config()
    bedrock_model_list = []
    
    # 如果配置了AWS凭证，获取Bedrock模型列表
    if (
        llm_config.aws_region_name
        and llm_config.aws_access_key_id
        and llm_config.aws_secret_access_key
    ):
        bedrock_model_list = bedrock.list_foundation_models(
            llm_config.aws_region_name,
            llm_config.aws_access_key_id.get_secret_value(),
            llm_config.aws_secret_access_key.get_secret_value(),
        )
    
    # 合并LiteLLM和Bedrock模型列表
    model_list = litellm_model_list_without_bedrock + bedrock_model_list
    
    # 检查所有配置的LLM以发现Ollama模型
    for llm_config in config.llms.values():
        ollama_base_url = llm_config.ollama_base_url
        
        # 如果模型名称以'ollama'开头但没有专门的ollama_base_url，
        # 则尝试使用通用的base_url
        if llm_config.model.startswith('ollama'):
            if not ollama_base_url:
                ollama_base_url = llm_config.base_url
                
        # 如果配置了Ollama URL，尝试获取可用模型
        if ollama_base_url:
            # 构建Ollama API的tags端点URL
            ollama_url = ollama_base_url.strip('/') + '/api/tags'
            try:
                # 发送HTTP请求获取Ollama模型列表（3秒超时）
                ollama_models_list = httpx.get(ollama_url, timeout=3).json()['models']  # noqa: ASYNC100
                
                # 将Ollama模型添加到模型列表中，添加'ollama/'前缀
                for model in ollama_models_list:
                    model_list.append('ollama/' + model['name'])
                    
                # 找到一个可用的Ollama实例后就退出循环
                break
            except httpx.HTTPError as e:
                # 记录获取Ollama模型时的错误，但不中断程序执行
                logger.error(f'Error getting OLLAMA models: {e}')

    # 返回去重且排序的模型列表
    return list(sorted(set(model_list)))
