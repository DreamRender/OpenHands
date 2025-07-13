# GitHub GraphQL查询：获取用户的Pull Request

suggested_task_pr_graphql_query = """
    query GetUserPRs($login: String!) {
        user(login: $login) {
        pullRequests(first: 50, states: [OPEN], orderBy: {field: UPDATED_AT, direction: DESC}) {
            nodes {
            number
            title
            repository {
                nameWithOwner
            }
            mergeable
            commits(last: 1) {
                nodes {
                commit {
                    statusCheckRollup {
                        state
                    }
                }
                }
            }
            reviews(first: 50, states: [CHANGES_REQUESTED, COMMENTED]) {
                nodes {
                state
                }
            }
            }
        }
        }
    }
"""
"""
GitHub GraphQL查询：获取用户建议任务中的Pull Request信息。

此查询用于获取指定用户的开放状态Pull Request，包含：
- number: PR编号
- title: PR标题
- repository.nameWithOwner: Repository的完整名称（owner/repo格式）
- mergeable: 是否可合并（用于检测合并冲突）
- commits: 最新提交信息，包含状态检查汇总（用于检测CI失败）
- reviews: 评审信息（用于检测未解决的评论）

查询参数：
- $login: 用户登录名

返回最多50个按更新时间降序排列的开放PR。
"""


# GitHub GraphQL查询：获取用户的Issue

suggested_task_issue_graphql_query = """
    query GetUserIssues($login: String!) {
        user(login: $login) {
        issues(first: 50, states: [OPEN], filterBy: {assignee: $login}, orderBy: {field: UPDATED_AT, direction: DESC}) {
            nodes {
            number
            title
            repository {
                nameWithOwner
                }
            }
        }
        }
    }
"""
"""
GitHub GraphQL查询：获取用户建议任务中的Issue信息。

此查询用于获取分配给指定用户的开放状态Issue，包含：
- number: Issue编号
- title: Issue标题
- repository.nameWithOwner: Repository的完整名称（owner/repo格式）

查询参数：
- $login: 用户登录名

查询条件：
- 只获取开放状态的Issue
- 只获取分配给指定用户的Issue
- 按更新时间降序排列
- 返回最多50个Issue
"""
