# flake8: noqa: E501

import asyncio

from openhands.resolver.issue_resolver import IssueResolver


def main() -> None:
    """主函数：解析命令行参数并执行Issue解决流程
    
    这个函数是程序的入口点，负责：
    1. 解析所有命令行参数
    2. 创建IssueResolver实例
    3. 启动异步Issue解决流程
    """
    import argparse

    def int_or_none(value: str) -> int | None:
        """将字符串转换为整数或None
        
        Args:
            value (str): 输入字符串，如果是'none'则返回None，否则转换为整数
            
        Returns:
            int | None: 转换后的整数或None
        """
        if value.lower() == 'none':
            return None
        else:
            return int(value)

    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description='Resolve a single issue.')
    
    # Repository相关参数
    parser.add_argument(
        '--selected-repo',
        type=str,
        required=True,
        help='repository to resolve issues in form of `owner/repo`.',
    )
    
    # 认证相关参数
    parser.add_argument(
        '--token',
        type=str,
        default=None,
        help='token to access the repository.',
    )
    parser.add_argument(
        '--username',
        type=str,
        default=None,
        help='username to access the repository.',
    )
    
    # 容器镜像相关参数
    parser.add_argument(
        '--base-container-image',
        type=str,
        default=None,
        help='base container image to use.',
    )
    parser.add_argument(
        '--runtime-container-image',
        type=str,
        default=None,
        help='Container image to use.',
    )
    
    # 运行配置参数
    parser.add_argument(
        '--max-iterations',
        type=int,
        default=50,
        help='Maximum number of iterations to run.',
    )
    
    # Issue相关参数
    parser.add_argument(
        '--issue-number',
        type=int,
        required=True,
        help='Issue number to resolve.',
    )
    parser.add_argument(
        '--comment-id',
        type=int_or_none,
        required=False,
        default=None,
        help='Resolve a specific comment',
    )
    
    # 输出相关参数
    parser.add_argument(
        '--output-dir',
        type=str,
        default='output',
        help='Output directory to write the results.',
    )
    
    # LLM相关参数
    parser.add_argument(
        '--llm-model',
        type=str,
        default=None,
        help='LLM model to use.',
    )
    parser.add_argument(
        '--llm-api-key',
        type=str,
        default=None,
        help='LLM API key to use.',
    )
    parser.add_argument(
        '--llm-base-url',
        type=str,
        default=None,
        help='LLM base URL to use.',
    )
    
    # 提示模板相关参数
    parser.add_argument(
        '--prompt-file',
        type=str,
        default=None,
        help='Path to the prompt template file in Jinja format.',
    )
    parser.add_argument(
        '--repo-instruction-file',
        type=str,
        default=None,
        help='Path to the repository instruction file in text format.',
    )
    
    # Issue类型参数
    parser.add_argument(
        '--issue-type',
        type=str,
        default='issue',
        choices=['issue', 'pr'],
        help='Type of issue to resolve, either open issue or pr comments.',
    )
    
    # 实验模式参数
    parser.add_argument(
        '--is-experimental',
        type=lambda x: x.lower() == 'true',
        help='Whether to run in experimental mode.',
    )
    
    # 基础域名参数
    parser.add_argument(
        '--base-domain',
        type=str,
        default=None,
        help='Base domain for the git server (defaults to "github.com" for GitHub, "gitlab.com" for GitLab, and "bitbucket.org" for Bitbucket)',
    )

    # 解析命令行参数
    my_args = parser.parse_args()

    # 创建IssueResolver实例并执行Issue解决流程
    issue_resolver = IssueResolver(my_args)
    # 使用asyncio运行异步的Issue解决方法
    asyncio.run(issue_resolver.resolve_issue())


if __name__ == '__main__':
    # 当作为主程序运行时，调用main函数
    main()
