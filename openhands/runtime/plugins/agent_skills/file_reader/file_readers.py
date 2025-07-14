"""OpenHands Agent的文件读取器技能模块

该模块为OpenHands Agent提供了解析和提取不同文件类型内容的功能，
包括PDF、DOCX、LaTeX、音频、图像、视频和PowerPoint文件的处理。
模块利用不同的库和API来处理这些文件并输出其内容或描述信息。

功能函数:
    parse_pdf(file_path: str) -> None: 解析并打印PDF文件内容
    parse_docx(file_path: str) -> None: 解析并打印DOCX文件内容
    parse_latex(file_path: str) -> None: 解析并打印LaTeX文件内容
    parse_audio(file_path: str, model: str = 'whisper-1') -> None: 转录并打印音频文件内容
    parse_image(file_path: str, task: str = 'Describe this image as detail as possible.') -> None: 分析并打印图像文件描述
    parse_video(file_path: str, task: str = 'Describe this image as detail as possible.', frame_interval: int = 30) -> None: 分析并打印视频帧描述
    parse_pptx(file_path: str) -> None: 解析并打印PowerPoint文件内容

注意:
    某些函数（parse_audio、parse_video、parse_image）需要OpenAI API凭证，
    只有在设置了必要的环境变量时才可用。
"""

import base64
from typing import Any

import docx
import PyPDF2
from pptx import Presentation
from pylatexenc.latex2text import LatexNodes2Text

# 导入配置工具函数，用于获取OpenAI相关配置
from openhands.runtime.plugins.agent_skills.utils.config import (
    _get_max_token,        # 获取最大token数限制
    _get_openai_api_key,   # 获取OpenAI API密钥
    _get_openai_base_url,  # 获取OpenAI API基础URL
    _get_openai_client,    # 获取OpenAI客户端实例
    _get_openai_model,     # 获取OpenAI Model名称
)


def parse_pdf(file_path: str) -> None:
    """解析PDF文件内容并打印
    
    该函数使用PyPDF2库读取PDF文件，逐页提取文本内容，
    并按页码格式化输出所有文本内容。
    
    Args:
        file_path: PDF文件的路径
        
    Returns:
        None
    """
    print(f'[Reading PDF file from {file_path}]')
    # 创建PDF阅读器对象
    content = PyPDF2.PdfReader(file_path)
    text = ''
    
    # 遍历PDF的每一页
    for page_idx in range(len(content.pages)):
        # 为每页添加页码标记并提取文本
        text += (
            f'@@ Page {page_idx + 1} @@\n'
            + content.pages[page_idx].extract_text()
            + '\n\n'
        )
    # 输出处理后的文本内容，去除末尾空白
    print(text.strip())


def parse_docx(file_path: str) -> None:
    """解析DOCX文件内容并打印
    
    该函数使用python-docx库读取Word文档，逐段提取文本内容，
    并按段落编号格式化输出所有文本内容。
    
    Args:
        file_path: DOCX文件的路径
        
    Returns:
        None
    """
    print(f'[Reading DOCX file from {file_path}]')
    # 创建Word文档对象
    content = docx.Document(file_path)
    text = ''
    
    # 遍历文档中的每个段落
    for i, para in enumerate(content.paragraphs):
        # 为每段添加页码标记（实际是段落编号）并提取文本
        text += f'@@ Page {i + 1} @@\n' + para.text + '\n\n'
    print(text)


def parse_latex(file_path: str) -> None:
    """解析LaTeX文件内容并打印
    
    该函数读取LaTeX源文件，使用pylatexenc库将LaTeX格式转换为纯文本，
    并输出转换后的文本内容。
    
    Args:
        file_path: LaTeX文件的路径
        
    Returns:
        None
    """
    print(f'[Reading LaTex file from {file_path}]')
    # 读取LaTeX文件内容
    with open(file_path) as f:
        data = f.read()
    
    # 使用LaTeX转文本工具进行格式转换
    text = LatexNodes2Text().latex_to_text(data)
    # 输出转换后的文本，去除末尾空白
    print(text.strip())


def _base64_img(file_path: str) -> str:
    """将图像文件转换为base64编码字符串
    
    该辅助函数读取图像文件的二进制数据，并将其编码为base64字符串，
    用于在API调用中传输图像数据。
    
    Args:
        file_path: 图像文件的路径
        
    Returns:
        str: base64编码的图像数据字符串
    """
    # 以二进制模式读取图像文件
    with open(file_path, 'rb') as image_file:
        # 将二进制数据编码为base64字符串
        encoded_image = base64.b64encode(image_file.read()).decode('utf-8')
    return encoded_image


def _base64_video(file_path: str, frame_interval: int = 10) -> list[str]:
    """将视频文件按指定间隔提取帧并转换为base64编码列表
    
    该辅助函数使用OpenCV读取视频文件，按指定的帧间隔提取视频帧，
    并将每帧转换为JPEG格式的base64编码字符串。
    
    Args:
        file_path: 视频文件的路径
        frame_interval: 帧提取间隔，默认为10（即每10帧提取一帧）
        
    Returns:
        list[str]: base64编码的视频帧数据字符串列表
    """
    import cv2

    # 创建视频捕获对象
    video = cv2.VideoCapture(file_path)
    base64_frames = []
    frame_count = 0
    
    # 逐帧读取视频
    while video.isOpened():
        success, frame = video.read()
        if not success:
            break
        
        # 按指定间隔提取帧
        if frame_count % frame_interval == 0:
            # 将帧编码为JPEG格式
            _, buffer = cv2.imencode('.jpg', frame)
            # 转换为base64字符串并添加到列表
            base64_frames.append(base64.b64encode(buffer).decode('utf-8'))
        frame_count += 1
    
    # 释放视频捕获资源
    video.release()
    return base64_frames


def _prepare_image_messages(task: str, base64_image: str) -> list[dict[str, Any]]:
    """准备图像分析的消息格式
    
    该辅助函数构造符合OpenAI API要求的消息格式，
    用于发送图像分析请求。
    
    Args:
        task: 图像分析任务描述
        base64_image: base64编码的图像数据
        
    Returns:
        list[dict[str, Any]]: 格式化的API消息列表
    """
    return [
        {
            'role': 'user',
            'content': [
                {'type': 'text', 'text': task},  # 文本任务描述
                {
                    'type': 'image_url',
                    'image_url': {'url': f'data:image/jpeg;base64,{base64_image}'},  # base64图像数据
                },
            ],
        }
    ]


def parse_audio(file_path: str, model: str = 'whisper-1') -> None:
    """解析音频文件内容并打印转录文本
    
    该函数使用OpenAI的Whisper模型对音频文件进行转录，
    将音频内容转换为文本并输出。
    
    Args:
        file_path: 音频文件的路径
        model: 用于转录的音频Model，默认为'whisper-1'
        
    Returns:
        None
    """
    print(f'[Transcribing audio file from {file_path}]')
    try:
        # TODO: 记录API调用的成本
        # 以二进制模式打开音频文件
        with open(file_path, 'rb') as audio_file:
            # 调用OpenAI音频转录API
            transcript = _get_openai_client().audio.translations.create(
                model=model, file=audio_file
            )
        # 输出转录结果
        print(transcript.text)

    except Exception as e:
        # 捕获并输出转录过程中的错误
        print(f'Error transcribing audio file: {e}')


def parse_image(
    file_path: str, task: str = 'Describe this image as detail as possible.'
) -> None:
    """解析图像文件内容并打印描述
    
    该函数使用OpenAI的视觉Model对图像进行分析，
    根据指定的任务描述生成图像的详细描述。
    
    Args:
        file_path: 图像文件的路径
        task: API调用的任务描述，默认为'Describe this image as detail as possible.'
        
    Returns:
        None
    """
    print(f'[Reading image file from {file_path}]')
    # TODO: 记录API调用的成本
    try:
        # 将图像转换为base64编码
        base64_image = _base64_img(file_path)
        
        # 调用OpenAI聊天API进行图像分析
        response = _get_openai_client().chat.completions.create(
            model=_get_openai_model(),
            messages=_prepare_image_messages(task, base64_image),
            max_tokens=_get_max_token(),
        )
        
        # 提取并输出API响应内容
        content = response.choices[0].message.content
        print(content)

    except Exception as error:
        # 捕获并输出请求过程中的错误
        print(f'Error with the request: {error}')


def parse_video(
    file_path: str,
    task: str = 'Describe this image as detail as possible.',
    frame_interval: int = 30,
) -> None:
    """解析视频文件内容并打印帧描述
    
    该函数提取视频的关键帧，使用OpenAI的视觉Model分析每一帧，
    并生成对应的描述内容。
    
    Args:
        file_path: 视频文件的路径
        task: API调用的任务描述，默认为'Describe this image as detail as possible.'
        frame_interval: 帧分析间隔，默认为30
        
    Returns:
        None
    """
    print(
        f'[Processing video file from {file_path} with frame interval {frame_interval}]'
    )

    # 如果没有指定任务，使用默认的视频帧描述任务
    task = task or 'This is one frame from a video, please summarize this frame.'
    
    # 提取视频帧的base64编码数据
    base64_frames = _base64_video(file_path)
    # 按指定间隔选择帧进行分析
    selected_frames = base64_frames[::frame_interval]

    # 如果选中的帧数超过30，重新计算间隔以限制分析数量
    if len(selected_frames) > 30:
        new_interval = len(base64_frames) // 30
        selected_frames = base64_frames[::new_interval]

    print(f'Totally {len(selected_frames)} would be analyze...\n')

    idx = 0
    # 逐帧进行分析
    for base64_frame in selected_frames:
        idx += 1
        print(f'Process the {file_path}, current No. {idx * frame_interval} frame...')
        # TODO: 记录API调用的成本
        try:
            # 调用OpenAI API分析当前帧
            response = _get_openai_client().chat.completions.create(
                model=_get_openai_model(),
                messages=_prepare_image_messages(task, base64_frame),
                max_tokens=_get_max_token(),
            )

            # 提取API响应内容并格式化输出
            content = response.choices[0].message.content
            current_frame_content = f"Frame {idx}'s content: {content}\n"
            print(current_frame_content)

        except Exception as error:
            # 捕获并输出请求过程中的错误
            print(f'Error with the request: {error}')


def parse_pptx(file_path: str) -> None:
    """解析PowerPoint文件内容并打印
    
    该函数使用python-pptx库读取PowerPoint演示文稿，
    提取每张幻灯片中的文本内容并按幻灯片编号格式化输出。
    
    Args:
        file_path: PowerPoint文件的路径
        
    Returns:
        None
    """
    print(f'[Reading PowerPoint file from {file_path}]')
    try:
        # 创建演示文稿对象
        pres = Presentation(str(file_path))
        text = []
        
        # 遍历每张幻灯片
        for slide_idx, slide in enumerate(pres.slides):
            # 添加幻灯片标记
            text.append(f'@@ Slide {slide_idx + 1} @@')
            
            # 遍历幻灯片中的所有形状对象
            for shape in slide.shapes:
                # 如果形状包含文本，则提取文本内容
                if hasattr(shape, 'text'):
                    text.append(shape.text)
        
        # 输出所有提取的文本内容
        print('\n'.join(text))

    except Exception as e:
        # 捕获并输出读取PowerPoint文件时的错误
        print(f'Error reading PowerPoint file: {e}')


# 定义模块的基础导出函数列表
__all__ = [
    'parse_pdf',    # PDF文件解析功能
    'parse_docx',   # DOCX文件解析功能
    'parse_latex',  # LaTeX文件解析功能
    'parse_pptx',   # PowerPoint文件解析功能
]

# 这部分代码从OpenHands端调用
# 如果设置了SANDBOX_ENV_OPENAI_API_KEY环境变量，
# 我们将能够在沙箱环境中使用这些需要OpenAI API的工具
if _get_openai_api_key() and _get_openai_base_url():
    # 添加需要OpenAI API的高级功能到导出列表
    __all__ += ['parse_audio', 'parse_video', 'parse_image']
