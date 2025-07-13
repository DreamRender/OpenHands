import docker


def stop_all_containers(prefix: str) -> None:
    """
    停止所有以指定前缀开头的Docker容器。
    
    该函数用于批量停止容器，通常在清理或关闭操作中使用。
    函数会安全地处理各种可能的异常情况，确保即使某些容器
    停止失败也不会影响其他容器的处理。
    
    Args:
        prefix (str): 容器名称前缀，只有名称以此前缀开头的容器会被停止
    
    Note:
        - 该函数会列出所有容器（包括已停止的）
        - 对于每个匹配前缀的容器，都会尝试停止操作
        - 所有异常都会被静默处理，确保函数执行的健壮性
        - 无论是否发生异常，都会确保Docker客户端被正确关闭
    """
    # 从环境创建Docker客户端
    docker_client = docker.from_env()
    
    try:
        # 获取所有容器列表（包括已停止的容器）
        containers = docker_client.containers.list(all=True)
        
        # 遍历所有容器
        for container in containers:
            try:
                # 检查容器名称是否以指定前缀开头
                if container.name.startswith(prefix):
                    # 尝试停止容器
                    container.stop()
            except docker.errors.APIError:
                # 忽略Docker API错误
                # 这可能发生在容器已经停止或无法停止的情况下
                pass
            except docker.errors.NotFound:
                # 忽略容器未找到错误
                # 这可能发生在容器在处理过程中被删除的情况下
                pass
    except docker.errors.NotFound:  
        # 是的，这确实可能发生！
        # 这个异常可能在containers.list()调用时发生
        # 例如当Docker守护进程不可用或其他系统级问题时
        pass
    finally:
        # 无论是否发生异常，都要确保关闭Docker客户端
        # 这是重要的资源清理步骤，避免连接泄漏
        docker_client.close()