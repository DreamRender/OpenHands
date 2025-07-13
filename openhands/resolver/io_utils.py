import json
from typing import Iterable

from openhands.resolver.resolver_output import ResolverOutput


def load_all_resolver_outputs(output_jsonl: str) -> Iterable[ResolverOutput]:
    """从JSONL文件中加载所有的Resolver输出结果
    
    这个函数读取包含多个ResolverOutput对象的JSONL文件，
    每行一个JSON对象，并逐个解析返回。
    
    Args:
        output_jsonl (str): JSONL文件的路径，包含多个Resolver输出结果
        
    Yields:
        ResolverOutput: 解析后的ResolverOutput对象
        
    Note:
        使用生成器模式，可以节省内存，适合处理大文件
    """
    # 打开JSONL文件进行读取
    with open(output_jsonl, 'r') as f:
        # 逐行读取文件内容
        for line in f:
            # 解析JSON行并转换为ResolverOutput对象
            yield ResolverOutput.model_validate(json.loads(line))


def load_single_resolver_output(output_jsonl: str, issue_number: int) -> ResolverOutput:
    """从JSONL文件中加载指定issue编号的Resolver输出结果
    
    遍历JSONL文件中的所有ResolverOutput对象，找到匹配指定issue编号的结果。
    
    Args:
        output_jsonl (str): JSONL文件的路径，包含多个Resolver输出结果
        issue_number (int): 要查找的issue编号
        
    Returns:
        ResolverOutput: 匹配的ResolverOutput对象
        
    Raises:
        ValueError: 当在文件中找不到指定issue编号时抛出异常
    """
    # 遍历所有的resolver输出结果
    for resolver_output in load_all_resolver_outputs(output_jsonl):
        # 检查当前输出结果的issue编号是否匹配
        if resolver_output.issue.number == issue_number:
            return resolver_output
    
    # 如果没有找到匹配的issue编号，抛出异常
    raise ValueError(f'Issue number {issue_number} not found in {output_jsonl}')
