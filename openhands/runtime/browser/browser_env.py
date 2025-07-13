"""
浏览器环境模块

该模块提供了BrowserEnv类，用于管理和控制浏览器环境的初始化、操作和清理。
支持评估模式和常规浏览模式，通过多进程方式运行浏览器环境以确保隔离性和稳定性。
"""

import atexit
import json
import multiprocessing
import time
import uuid

import browsergym.core  # noqa F401 (注册openended任务作为gym环境)
import gymnasium as gym
import html2text
import tenacity
from browsergym.utils.obs import flatten_dom_to_str, overlay_som

from openhands.core.exceptions import BrowserInitException
from openhands.core.logger import openhands_logger as logger
from openhands.runtime.browser.base64 import image_to_png_base64_url
from openhands.utils.shutdown_listener import should_continue, should_exit
from openhands.utils.tenacity_stop import stop_if_should_exit

# 浏览器评估模式的特殊Action常量
BROWSER_EVAL_GET_GOAL_ACTION = 'GET_EVAL_GOAL'  # 获取评估目标的Action类型
BROWSER_EVAL_GET_REWARDS_ACTION = 'GET_EVAL_REWARDS'  # 获取评估奖励的Action类型


class BrowserEnv:
    """
    浏览器环境管理类
    
    该类负责管理整个浏览器环境的生命周期，包括初始化、与浏览器进程的通信、
    Action执行和环境清理。支持两种模式：评估模式和常规浏览模式。
    
    Attributes:
        html_text_converter (html2text.HTML2Text): HTML到文本的转换器
        eval_mode (bool): 是否为评估模式
        eval_dir (str): 评估目录路径
        browsergym_eval_env (str): BrowserGym评估环境名称
        browser_side (multiprocessing.Connection): 浏览器进程端的通信管道
        agent_side (multiprocessing.Connection): Agent端的通信管道
        process (multiprocessing.Process): 浏览器进程对象
        eval_goal (str): 评估目标文本
        goal_image_urls (list): 目标图像URL列表
        eval_rewards (list): 评估奖励列表
    """
    
    def __init__(self, browsergym_eval_env: str | None = None):
        """
        初始化浏览器环境
        
        Args:
            browsergym_eval_env (str | None): BrowserGym评估环境名称，如果提供则启用评估模式
        """
        # 初始化HTML到文本的转换器
        self.html_text_converter = self.get_html_text_converter()
        
        # 初始化评估相关属性
        self.eval_mode = False
        self.eval_dir = ''

        # EVAL专用：如果提供了browsergym_eval_env，则启用评估模式
        self.browsergym_eval_env = browsergym_eval_env
        self.eval_mode = bool(browsergym_eval_env)

        # 初始化浏览器环境进程
        # 强制使用spawn方法启动进程，确保进程间的完全隔离
        multiprocessing.set_start_method('spawn', force=True)
        # 创建双向通信管道：browser_side用于浏览器进程，agent_side用于Agent
        self.browser_side, self.agent_side = multiprocessing.Pipe()

        # 初始化浏览器并注册退出时的清理函数
        self.init_browser()
        atexit.register(self.close)

    def get_html_text_converter(self) -> html2text.HTML2Text:
        """
        获取HTML到文本的转换器
        
        配置html2text转换器的参数，用于将HTML内容转换为纯文本格式。
        
        Returns:
            html2text.HTML2Text: 配置好的HTML转换器实例
        """
        html_text_converter = html2text.HTML2Text()
        # 保留链接信息（设置为False表示不忽略链接）
        html_text_converter.ignore_links = False
        # 忽略图像标签
        html_text_converter.ignore_images = True
        # 对图像使用alt文本替代
        html_text_converter.images_to_alt = True
        # 禁用自动文本换行（设置为0表示不限制行宽）
        html_text_converter.body_width = 0
        return html_text_converter

    @tenacity.retry(
        wait=tenacity.wait_fixed(1),  # 每次重试前等待1秒
        stop=tenacity.stop_after_attempt(5) | stop_if_should_exit(),  # 最多重试5次或接收到退出信号时停止
        retry=tenacity.retry_if_exception_type(BrowserInitException),  # 只有当抛出BrowserInitException时才重试
    )
    def init_browser(self) -> None:
        """
        初始化浏览器进程
        
        启动独立的浏览器进程，并检查其是否成功启动。
        使用tenacity装饰器提供重试机制，增强启动过程的鲁棒性。
        
        Raises:
            BrowserInitException: 当浏览器环境启动失败时抛出
        """
        logger.debug('Starting browser env...')
        try:
            # 创建新的浏览器进程，目标函数为browser_process
            self.process = multiprocessing.Process(target=self.browser_process)
            self.process.start()
        except Exception as e:
            logger.error(f'Failed to start browser process: {e}')
            raise

        # 检查浏览器进程是否在200秒内成功启动
        if not self.check_alive(timeout=200):
            self.close()
            raise BrowserInitException('Failed to start browser environment.')

    def browser_process(self) -> None:
        """
        浏览器进程的主要执行函数
        
        该函数在独立的进程中运行，负责：
        1. 根据模式初始化相应的gym环境（评估模式或常规模式）
        2. 处理来自Agent的Action请求
        3. 执行Action并返回Observation
        4. 管理评估相关的数据收集
        """
        # 根据是否为评估模式初始化不同的环境
        if self.eval_mode:
            assert self.browsergym_eval_env is not None
            logger.info('Initializing browser env for web browsing evaluation.')
            
            # 确保环境名称以'browsergym/'开头
            if not self.browsergym_eval_env.startswith('browsergym/'):
                self.browsergym_eval_env = 'browsergym/' + self.browsergym_eval_env
            
            # 根据不同的评估环境类型导入相应的模块
            if 'visualwebarena' in self.browsergym_eval_env:
                import browsergym.visualwebarena  # noqa F401 注册visualwebarena任务为gym环境
                import nltk
                # 下载NLTK所需的punkt_tab数据
                nltk.download('punkt_tab')
            elif 'webarena' in self.browsergym_eval_env:
                import browsergym.webarena  # noqa F401 注册webarena任务为gym环境
            elif 'miniwob' in self.browsergym_eval_env:
                import browsergym.miniwob  # noqa F401 注册miniwob任务为gym环境
            else:
                raise ValueError(
                    f'Unsupported browsergym eval env: {self.browsergym_eval_env}'
                )
            
            # 创建评估环境，标记所有元素，设置超时时间
            env = gym.make(self.browsergym_eval_env, tags_to_mark='all', timeout=100000)
        else:
            # 创建常规的开放式浏览环境
            env = gym.make(
                'browsergym/openended',
                task_kwargs={'start_url': 'about:blank', 'goal': 'PLACEHOLDER_GOAL'},
                wait_for_user_message=False,  # 不等待用户消息
                headless=True,  # 无头模式运行
                disable_env_checker=True,  # 禁用环境检查器
                tags_to_mark='all',  # 标记所有元素
                timeout=100000,  # 设置超时时间
                pw_context_kwargs={'accept_downloads': True},  # 允许下载
                pw_chromium_kwargs={'downloads_path': '/workspace/.downloads/'},  # 设置下载路径
            )
        
        # 重置环境并获取初始Observation
        obs, info = env.reset()

        logger.info('Successfully called env.reset')
        
        # EVAL专用：初始化评估相关的变量
        self.eval_goal = None
        self.goal_image_urls = []
        self.eval_rewards: list[float] = []
        
        if self.eval_mode:
            # 提取评估目标信息
            self.eval_goal = obs['goal']
            if 'goal_object' in obs:
                # 将goal_object转换为列表格式
                obs['goal_object'] = list(obs['goal_object'])
                if len(obs['goal_object']) > 0:
                    self.eval_goal = obs['goal_object'][0]['text']
                
                # 提取目标中的图像URL
                for message in obs['goal_object']:
                    if message['type'] == 'image_url':
                        image_src = message['image_url']
                        # 处理不同格式的图像源
                        if isinstance(image_src, dict):
                            image_src = image_src['url']
                        self.goal_image_urls.append(image_src)
            logger.debug(f'Browsing goal: {self.eval_goal}')
        
        logger.info('Browser env started.')

        # 主事件循环：持续监听并处理来自Agent的请求
        while should_continue():
            try:
                # 检查是否有来自Agent的请求（超时时间0.01秒）
                if self.browser_side.poll(timeout=0.01):
                    # 接收请求：(唯一请求ID, Action数据)
                    unique_request_id, action_data = self.browser_side.recv()

                    # 处理关闭请求
                    if unique_request_id == 'SHUTDOWN':
                        logger.debug('SHUTDOWN recv, shutting down browser env...')
                        env.close()
                        return
                    # 处理存活检查请求
                    elif unique_request_id == 'IS_ALIVE':
                        self.browser_side.send(('ALIVE', None))
                        continue

                    # EVAL专用：处理评估信息获取请求
                    if action_data['action'] == BROWSER_EVAL_GET_GOAL_ACTION:
                        # 返回评估目标信息
                        self.browser_side.send(
                            (
                                unique_request_id,
                                {
                                    'text_content': self.eval_goal,
                                    'image_content': self.goal_image_urls,
                                },
                            )
                        )
                        continue
                    elif action_data['action'] == BROWSER_EVAL_GET_REWARDS_ACTION:
                        # 返回评估奖励信息
                        self.browser_side.send(
                            (
                                unique_request_id,
                                {'text_content': json.dumps(self.eval_rewards)},
                            )
                        )
                        continue

                    # 执行常规的浏览器Action
                    action = action_data['action']
                    obs, reward, terminated, truncated, info = env.step(action)

                    # EVAL专用：保存奖励信息用于评估
                    if self.eval_mode:
                        self.eval_rewards.append(reward)

                    # 添加页面的文本内容
                    html_str = flatten_dom_to_str(obs['dom_object'])
                    obs['text_content'] = self.html_text_converter.handle(html_str)
                    
                    # 使Observation可序列化：将图像转换为base64编码
                    # 生成带有Set-of-Marks标记的截图
                    obs['set_of_marks'] = image_to_png_base64_url(
                        overlay_som(
                            obs['screenshot'], obs.get('extra_element_properties', {})
                        ),
                        add_data_prefix=True,
                    )
                    # 转换原始截图
                    obs['screenshot'] = image_to_png_base64_url(
                        obs['screenshot'], add_data_prefix=True
                    )
                    # 转换numpy标量为Python原生类型
                    obs['active_page_index'] = obs['active_page_index'].item()
                    obs['elapsed_time'] = obs['elapsed_time'].item()
                    
                    # 发送处理后的Observation回Agent
                    self.browser_side.send((unique_request_id, obs))
            except KeyboardInterrupt:
                logger.debug('Browser env process interrupted by user.')
                try:
                    env.close()
                except Exception:
                    pass
                return

    def step(self, action_str: str, timeout: float = 120) -> dict:
        """
        在浏览器环境中执行Action并返回Observation
        
        该方法是Agent与浏览器环境交互的主要接口，通过进程间通信
        将Action发送给浏览器进程执行，并等待返回结果。
        
        Args:
            action_str (str): 要执行的Action字符串
            timeout (float): 超时时间，默认120秒
            
        Returns:
            dict: 包含Observation数据的字典
            
        Raises:
            TimeoutError: 当浏览器环境响应超时时抛出
        """
        # 生成唯一的请求ID用于匹配请求和响应
        unique_request_id = str(uuid.uuid4())
        
        # 向浏览器进程发送Action请求
        self.agent_side.send((unique_request_id, {'action': action_str}))
        
        # 记录开始时间用于超时检查
        start_time = time.time()
        
        # 等待浏览器进程的响应
        while True:
            # 检查是否需要退出或是否超时
            if should_exit() or time.time() - start_time > timeout:
                raise TimeoutError('Browser environment took too long to respond.')
            
            # 检查是否有响应（超时时间0.01秒）
            if self.agent_side.poll(timeout=0.01):
                response_id, obs = self.agent_side.recv()
                # 确保响应ID匹配请求ID
                if response_id == unique_request_id:
                    return dict(obs)

    def check_alive(self, timeout: float = 60) -> bool:
        """
        检查浏览器环境是否仍然存活
        
        向浏览器进程发送存活检查请求，用于确认进程状态。
        
        Args:
            timeout (float): 超时时间，默认60秒
            
        Returns:
            bool: 如果浏览器环境存活则返回True，否则返回False
        """
        # 发送存活检查请求
        self.agent_side.send(('IS_ALIVE', None))
        
        # 等待响应
        if self.agent_side.poll(timeout=timeout):
            response_id, _ = self.agent_side.recv()
            if response_id == 'ALIVE':
                return True
            logger.debug(f'Browser env is not alive. Response ID: {response_id}')
        return False

    def close(self) -> None:
        """
        关闭浏览器环境并清理资源
        
        该方法负责优雅地关闭浏览器进程和相关资源，包括：
        1. 发送关闭信号给浏览器进程
        2. 等待进程正常终止
        3. 强制终止无响应的进程
        4. 关闭通信管道
        """
        # 如果进程已经不存活，直接返回
        if not self.process.is_alive():
            return
        
        try:
            # 发送关闭信号给浏览器进程
            self.agent_side.send(('SHUTDOWN', None))
            
            # 等待进程正常终止（最多5秒）
            self.process.join(5)
            
            # 如果进程仍然存活，尝试强制终止
            if self.process.is_alive():
                logger.error(
                    'Browser process did not terminate, forcefully terminating...'
                )
                self.process.terminate()
                self.process.join(5)  # 等待进程终止
                
                # 如果terminate仍然无效，使用kill强制终止
                if self.process.is_alive():
                    self.process.kill()
                    self.process.join(5)  # 等待进程终止
            
            # 关闭通信管道
            self.agent_side.close()
            self.browser_side.close()
        except Exception as e:
            logger.error(f'Encountered an error when closing browser env: {e}')
