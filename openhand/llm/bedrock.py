import boto3

from openhands.core.logger import openhands_logger as logger


def list_foundation_models(
    aws_region_name: str, aws_access_key_id: str, aws_secret_access_key: str
) -> list[str]:
    """
    列出AWS Bedrock服务中可用的基础模型。
    
    该函数连接到AWS Bedrock服务并获取支持文本输出且按需推理的基础模型列表。
    如果未配置AWS参数或发生错误，将不会查询AWS Bedrock模型ID。
    
    Args:
        aws_region_name: AWS区域名称，例如'us-east-1'
        aws_access_key_id: AWS访问密钥ID
        aws_secret_access_key: AWS秘密访问密钥
        
    Returns:
        list[str]: 基础模型ID列表，每个ID都以'bedrock/'为前缀
                  如果发生错误则返回空列表
    """
    try:
        # 使用提供的AWS凭证创建Bedrock服务客户端
        # 如果没有配置AWS参数，则不会查询AWS Bedrock模型ID
        client = boto3.client(
            service_name='bedrock',
            region_name=aws_region_name,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )
        
        # 获取基础模型列表
        # byOutputModality='TEXT': 只获取支持文本输出的模型
        # byInferenceType='ON_DEMAND': 只获取支持按需推理的模型
        foundation_models_list = client.list_foundation_models(
            byOutputModality='TEXT', byInferenceType='ON_DEMAND'
        )
        
        # 从响应中提取模型摘要信息
        model_summaries = foundation_models_list['modelSummaries']
        
        # 为每个模型ID添加'bedrock/'前缀并返回列表
        return ['bedrock/' + model['modelId'] for model in model_summaries]
        
    except Exception as err:
        # 如果发生任何异常，记录警告信息并提示用户配置AWS参数
        logger.warning(
            '%s. Please config AWS_REGION_NAME AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY'
            ' if you want use bedrock model.',
            err,
        )
        # 返回空列表表示没有可用的模型
        return []


def remove_error_modelId(model_list: list[str]) -> list[str]:
    """
    从模型列表中移除错误的Bedrock模型ID。
    
    该函数过滤掉所有以'bedrock'开头的模型ID，
    通常用于移除可能导致错误的Bedrock模型。
    
    Args:
        model_list: 包含模型ID的字符串列表
        
    Returns:
        list[str]: 过滤后的模型列表，不包含以'bedrock'开头的模型ID
    """
    # 使用filter函数和lambda表达式过滤掉以'bedrock'开头的模型
    # 返回不以'bedrock'开头的模型列表
    return list(filter(lambda m: not m.startswith('bedrock'), model_list))