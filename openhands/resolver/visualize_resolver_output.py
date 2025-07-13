import argparse
import os

from openhands.resolver.io_utils import load_single_resolver_output


def visualize_resolver_output(
    issue_number: int, output_dir: str, vis_method: str
) -> None:
    """可视化Resolver输出结果
    
    从输出目录中加载指定Issue编号的Resolver结果，
    并根据指定的可视化方法进行展示。
    
    Args:
        issue_number (int): 要可视化的Issue编号
        output_dir (str): 包含输出结果的目录路径
        vis_method (str): 可视化方法，目前支持'json'格式
        
    Raises:
        ValueError: 当可视化方法无效时抛出异常
        
    Note:
        目前只支持JSON格式的可视化，会以格式化的JSON形式
        输出ResolverOutput对象的完整内容。
    """
    # 构建输出JSONL文件路径
    output_jsonl = os.path.join(output_dir, 'output.jsonl')
    
    # 加载指定Issue编号的Resolver输出结果
    resolver_output = load_single_resolver_output(output_jsonl, issue_number)
    
    # 根据可视化方法进行展示
    if vis_method == 'json':
        # 以缩进格式打印JSON内容，便于阅读
        print(resolver_output.model_dump_json(indent=4))
    else:
        # 不支持的可视化方法
        raise ValueError(f'Invalid visualization method: {vis_method}')


if __name__ == '__main__':
    """主程序入口
    
    当作为独立脚本运行时，解析命令行参数并执行可视化操作。
    """
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description='Visualize a patch.')
    
    # Issue编号参数（必需）
    parser.add_argument(
        '--issue-number',
        type=int,
        required=True,
        help='Issue number to send the pull request for.',
    )
    
    # 输出目录参数
    parser.add_argument(
        '--output-dir',
        type=str,
        default='output',
        help='Output directory to write the results.',
    )
    
    # 可视化方法参数
    parser.add_argument(
        '--vis-method',
        type=str,
        default='json',
        choices=['json'],
        help='Method to visualize the patch [json].',
    )
    
    # 解析命令行参数
    my_args = parser.parse_args()

    # 调用可视化函数
    visualize_resolver_output(
        issue_number=my_args.issue_number,
        output_dir=my_args.output_dir,
        vis_method=my_args.vis_method,
    )
