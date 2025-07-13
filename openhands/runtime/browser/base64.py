"""
Base64图像编码转换工具模块

该模块提供了图像与base64编码之间相互转换的工具函数，
主要用于在浏览器环境中处理截图和图像数据的编码传输。
支持numpy数组和PIL图像对象的转换。
"""

import base64
import io

import numpy as np
from PIL import Image


def image_to_png_base64_url(
    image: np.ndarray | Image.Image, add_data_prefix: bool = False
) -> str:
    """
    将图像转换为base64编码的PNG图像URL
    
    该函数接受numpy数组或PIL图像对象，将其转换为PNG格式的base64编码字符串。
    这种格式常用于在网络传输中嵌入图像数据，特别是在浏览器环境中显示截图。
    
    Args:
        image (np.ndarray | Image.Image): 输入图像，可以是numpy数组或PIL图像对象
        add_data_prefix (bool): 是否添加data URL前缀，默认为False
            - True: 返回完整的data URL格式 'data:image/png;base64,{base64_data}'
            - False: 只返回base64编码字符串
    
    Returns:
        str: base64编码的图像字符串，根据add_data_prefix参数决定是否包含data URL前缀
    
    Note:
        - 如果输入是numpy数组，会先转换为PIL图像
        - RGBA和LA模式的图像会被转换为RGB模式，以确保兼容性
        - 输出始终为PNG格式，保证图像质量
    """
    # 如果输入是numpy数组，转换为PIL图像对象
    if isinstance(image, np.ndarray):
        image = Image.fromarray(image)
    
    # 处理带透明度的图像模式，转换为RGB以确保兼容性
    # RGBA: 红绿蓝透明度模式, LA: 亮度透明度模式
    if image.mode in ('RGBA', 'LA'):
        image = image.convert('RGB')
    
    # 创建内存缓冲区用于存储PNG数据
    buffered = io.BytesIO()
    # 将图像保存为PNG格式到缓冲区
    image.save(buffered, format='PNG')

    # 将PNG二进制数据编码为base64字符串
    image_base64 = base64.b64encode(buffered.getvalue()).decode()
    
    # 根据参数决定返回格式
    return (
        f'data:image/png;base64,{image_base64}'  # 完整的data URL格式
        if add_data_prefix
        else f'{image_base64}'  # 只返回base64编码
    )


def png_base64_url_to_image(png_base64_url: str) -> Image.Image:
    """
    将base64编码的PNG图像URL转换为PIL图像对象
    
    该函数是image_to_png_base64_url的逆操作，将base64编码的图像数据
    解码并转换回PIL图像对象，用于后续的图像处理操作。
    
    Args:
        png_base64_url (str): base64编码的PNG图像字符串
            支持两种格式：
            1. 完整的data URL: 'data:image/png;base64,{base64_data}'
            2. 纯base64字符串: '{base64_data}'
    
    Returns:
        Image.Image: 解码后的PIL图像对象
    
    Note:
        - 自动处理data URL前缀，提取纯base64数据
        - 如果输入不包含逗号分隔符，则认为是纯base64数据
        - 返回的图像对象可用于进一步的图像处理操作
    """
    # 分割字符串，处理可能存在的data URL前缀
    splited = png_base64_url.split(',')
    
    if len(splited) == 2:
        # 如果包含逗号，取逗号后的部分作为base64数据
        # 例如: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA...' -> 'iVBORw0KGgoAAAANSUhEUgAA...'
        base64_data = splited[1]
    else:
        # 如果没有逗号，整个字符串就是base64数据
        base64_data = png_base64_url
    
    # 解码base64数据并创建PIL图像对象
    # 1. base64.b64decode(): 将base64字符串解码为二进制数据
    # 2. io.BytesIO(): 将二进制数据包装为字节流对象
    # 3. Image.open(): 从字节流创建PIL图像对象
    return Image.open(io.BytesIO(base64.b64decode(base64_data)))
