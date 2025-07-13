import collections
import re
from warnings import warn

import yaml


def yaml_parser(message: str) -> tuple[dict, bool, str]:
    """
    解析YAML格式的消息
    
    用于重试函数的YAML消息解析器，能够处理一些常见的YAML格式错误
    
    Args:
        message (str): 待解析的YAML格式字符串
        
    Returns:
        tuple[dict, bool, str]: 包含以下三个元素的元组：
            - dict: 解析后的字典对象，解析失败时为空字典
            - bool: 解析是否成功的标志
            - str: 重试消息，解析失败时提供给用户的错误提示
    """
    # 修复gpt-3.5可能产生的一些YAML解析错误
    # 将": \n"后跟非空白字符或换行符的模式替换为": "
    message = re.sub(r':\s*\n(?=\S|\n)', ': ', message)

    try:
        # 尝试安全解析YAML内容
        value = yaml.safe_load(message)
        valid = True
        retry_message = ''
    except yaml.YAMLError as e:
        # YAML解析出错时发出警告
        warn(str(e), stacklevel=2)
        value = {}
        valid = False
        retry_message = "Your response is not a valid yaml. Please try again and be careful to the format. Don't add any apology or comment, just the answer."
    return value, valid, retry_message


def _compress_chunks(
    text: str, identifier: str, skip_list: list[str], split_regex: str = '\n\n+'
) -> tuple[dict[str, str], str]:
    """
    压缩文本块的内部函数
    
    通过将冗余的文本块替换为标识符来压缩字符串。文本块由split_regex定义
    
    Args:
        text (str): 待压缩的原始文本
        identifier (str): 用于替换重复块的标识符前缀
        skip_list (list[str]): 跳过压缩的文本块列表
        split_regex (str): 用于分割文本块的正则表达式，默认为连续换行符
        
    Returns:
        tuple[dict[str, str], str]: 包含以下两个元素的元组：
            - dict: 定义字典，映射标识符到原始文本块
            - str: 压缩后的文本
    """
    # 使用正则表达式分割文本
    text_list = re.split(split_regex, text)
    # 去除每个文本块的首尾空白字符
    text_list = [chunk.strip() for chunk in text_list]
    # 统计每个文本块出现的次数
    counter = collections.Counter(text_list)
    def_dict = {}
    id = 0

    # 将出现次数超过一次的项目存储在字典中
    for item, count in counter.items():
        # 只压缩出现多次、不在跳过列表中且长度超过10字符的文本块
        if count > 1 and item not in skip_list and len(item) > 10:
            def_dict[f'{identifier}-{id}'] = item
            id += 1

    # 在文本中用标识符替换冗余项目
    compressed_text = '\n'.join(text_list)
    for key, value in def_dict.items():
        compressed_text = compressed_text.replace(value, key)

    return def_dict, compressed_text


def compress_string(text: str) -> str:
    """
    压缩字符串
    
    通过将冗余的段落和行替换为标识符来压缩字符串，减少文本冗余
    
    Args:
        text (str): 待压缩的原始文本
        
    Returns:
        str: 压缩后的文本，包含定义部分和压缩后的内容
    """
    # 执行段落级压缩
    def_dict, compressed_text = _compress_chunks(
        text, identifier='§', skip_list=[], split_regex='\n\n+'
    )

    # 执行行级压缩，跳过任何段落标识符
    line_dict, compressed_text = _compress_chunks(
        compressed_text, '¶', list(def_dict.keys()), split_regex='\n+'
    )
    # 合并两个定义字典
    def_dict.update(line_dict)

    # 创建定义部分
    def_lines = ['<definitions>']
    for key, value in def_dict.items():
        def_lines.append(f'{key}:\n{value}')
    def_lines.append('</definitions>')
    definitions = '\n'.join(def_lines)

    return definitions + '\n' + compressed_text


def extract_html_tags(text: str, keys: list[str]) -> dict[str, list[str]]:
    """
    提取HTML标签内容
    
    从文本中提取指定HTML标签列表的内容
    
    Args:
        text (str): 包含HTML标签的输入字符串
        keys (list[str]): 要提取内容的HTML标签列表
        
    Returns:
        dict[str, list[str]]: 将每个键映射到文本中匹配该键的子集列表的字典
        
    Notes:
        所有文本和键在匹配前都会转换为小写
    """
    content_dict = {}
    # text = text.lower()  # 注释掉的代码：将文本转换为小写
    # keys = set([k.lower() for k in keys])  # 注释掉的代码：将键转换为小写
    
    # 遍历每个HTML标签键
    for key in keys:
        # 构建匹配模式：<key>内容</key>
        pattern = f'<{key}>(.*?)</{key}>'
        # 使用DOTALL标志匹配包含换行符的内容
        matches = re.findall(pattern, text, re.DOTALL)
        if matches:
            # 去除匹配内容的首尾空白字符
            content_dict[key] = [match.strip() for match in matches]
    return content_dict


class ParseError(Exception):
    """
    解析错误异常类
    
    当解析操作失败时抛出的自定义异常
    """
    pass


def parse_html_tags_raise(
    text: str,
    keys: list[str] | None = None,
    optional_keys: list[str] | None = None,
    merge_multiple: bool = False,
) -> dict[str, str]:
    """
    解析HTML标签并在失败时抛出异常
    
    parse_html_tags的变体版本，如果解析不成功则抛出异常
    
    Args:
        text (str): 包含HTML标签的输入字符串
        keys (list[str] | None): 必需的HTML标签列表，默认为None
        optional_keys (list[str] | None): 可选的HTML标签列表，默认为None
        merge_multiple (bool): 是否合并多个匹配，默认为False
        
    Returns:
        dict[str, str]: 解析成功的内容字典
        
    Raises:
        ParseError: 当解析失败时抛出此异常
    """
    content_dict, valid, retry_message = parse_html_tags(
        text, keys, optional_keys, merge_multiple=merge_multiple
    )
    if not valid:
        raise ParseError(retry_message)
    return content_dict


def parse_html_tags(
    text: str,
    keys: list[str] | None = None,
    optional_keys: list[str] | None = None,
    merge_multiple: bool = False,
) -> tuple[dict[str, str], bool, str]:
    """
    解析HTML标签
    
    满足解析API要求，每个键提取1个匹配并验证所有键都存在
    
    Args:
        text (str): 包含HTML标签的输入字符串
        keys (list[str] | None): 要提取内容的必需HTML标签列表，默认为None
        optional_keys (list[str] | None): 要提取内容的可选HTML标签列表，默认为None
        merge_multiple (bool): 当发现多个匹配时是否合并，默认为False
        
    Returns:
        tuple[dict[str, str], bool, str]: 包含以下三个元素的元组：
            - dict: 将每个键映射到匹配文本子集的字典
            - bool: 解析是否成功的标志
            - str: 解析失败时向Agent显示的消息
    """
    # 设置默认值
    keys = keys or []
    optional_keys = optional_keys or []
    
    # 合并所有键
    all_keys = list(keys) + list(optional_keys)
    
    # 提取HTML标签内容
    content_dict = extract_html_tags(text, all_keys)
    retry_messages = []  # 存储重试消息
    result_dict: dict[str, str] = {}  # 存储最终结果

    # 验证每个键的解析结果
    for key in all_keys:
        if key not in content_dict:
            # 如果键不在结果中且不是可选键，添加错误消息
            if key not in optional_keys:
                retry_messages.append(f'Missing the key <{key}> in the answer.')
        else:
            val = content_dict[key]
            if len(val) > 1:
                # 发现多个实例的处理
                if not merge_multiple:
                    retry_messages.append(
                        f'Found multiple instances of the key {key}. You should have only one of them.'
                    )
                else:
                    # 合并多个实例
                    result_dict[key] = '\n'.join(val)
            else:
                # 单个匹配，直接使用
                result_dict[key] = val[0]

    # 确定解析是否成功
    valid = len(retry_messages) == 0
    retry_message = '\n'.join(retry_messages)
    return result_dict, valid, retry_message