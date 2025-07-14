"""
用于生成文件查看器HTML内容的实用模块。
"""

import base64
import mimetypes
import os


def generate_file_viewer_html(file_path: str) -> str:
    """
    为查看不同文件类型生成HTML内容。

    Args:
        file_path (str): 文件的绝对路径

    Returns:
        str: 用于查看文件的HTML内容

    Raises:
        ValueError: 如果文件扩展名不受支持或文件不存在
    """
    # 获取文件扩展名和文件名
    file_extension = os.path.splitext(file_path)[1].lower()
    file_name = os.path.basename(file_path)

    # 定义支持的文件扩展名
    supported_extensions = [
        '.pdf',
        '.png',
        '.jpg',
        '.jpeg',
        '.gif',
    ]

    # 检查文件扩展名是否受支持
    if file_extension not in supported_extensions:
        raise ValueError(
            f'Unsupported file extension: {file_extension}. '
            f'Supported extensions are: {", ".join(supported_extensions)}'
        )
        # 翻译：不支持的文件扩展名：{file_extension}。支持的扩展名有：{", ".join(supported_extensions)}

    # 检查文件是否存在
    if not os.path.exists(file_path):
        raise ValueError(
            f'File not found locally: {file_path}. Please download the file to the local machine and try again.'
        )
        # 翻译：本地未找到文件：{file_path}。请将文件下载到本地机器并重试。

    # 直接读取文件内容
    file_content = None
    mime_type = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'

    # 对于二进制文件（图像、PDF），编码为base64
    if file_extension in ['.pdf', '.png', '.jpg', '.jpeg', '.gif', '.bmp']:
        with open(file_path, 'rb') as file:
            file_content = base64.b64encode(file.read()).decode('utf-8')
    # 对于文本文件，读取为文本
    else:
        with open(file_path, 'r', encoding='utf-8') as file:
            file_content = file.read()

    # 返回生成的HTML内容
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>File Viewer - {file_name}</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
    <style>
        body, html {{ margin: 0; padding: 0; height: 100%; overflow: hidden; font-family: Arial, sans-serif; }}
        #viewer-container {{ width: 100%; height: 100vh; overflow: auto; }}
        .page {{ margin: 10px auto; box-shadow: 0 0 10px rgba(0,0,0,0.3); }}
        .text-content {{ margin: 20px; white-space: pre-wrap; font-family: monospace; line-height: 1.5; }}
        .error {{ color: red; margin: 20px; }}
        img {{ max-width: 100%; margin: 20px auto; display: block; }}
    </style>
</head>
<body>
    <div id="viewer-container"></div>
    <script>
    const filePath = "{file_path}";
    const fileExtension = "{file_extension}";
    const fileContent = `{file_content if file_extension not in ['.pdf', '.png', '.jpg', '.jpeg', '.gif', '.bmp'] else ''}`;
    const fileBase64 = "{file_content if file_extension in ['.pdf', '.png', '.jpg', '.jpeg', '.gif', '.bmp'] else ''}";
    const mimeType = "{mime_type}";
    const container = document.getElementById('viewer-container');

    async function loadContent() {{
        try {{
            if (fileExtension === '.pdf') {{
                // 设置PDF.js worker
                pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
                
                // 将base64转换为二进制数据
                const binaryString = atob(fileBase64);
                const bytes = new Uint8Array(binaryString.length);
                for (let i = 0; i < binaryString.length; i++) {{
                    bytes[i] = binaryString.charCodeAt(i);
                }}

                // 加载PDF文档
                const loadingTask = pdfjsLib.getDocument({{data: bytes.buffer}});
                const pdf = await loadingTask.promise;

                // 获取总页数
                const numPages = pdf.numPages;

                // 渲染每一页
                for (let pageNum = 1; pageNum <= numPages; pageNum++) {{
                    const page = await pdf.getPage(pageNum);

                    // 设置渲染比例
                    const viewport = page.getViewport({{ scale: 1.5 }});

                    // 创建渲染用的canvas
                    const canvas = document.createElement('canvas');
                    canvas.className = 'page';
                    canvas.width = viewport.width;
                    canvas.height = viewport.height;
                    container.appendChild(canvas);

                    // 将PDF页面渲染到canvas上下文中
                    const context = canvas.getContext('2d');
                    const renderContext = {{
                        canvasContext: context,
                        viewport: viewport
                    }};

                    await page.render(renderContext).promise;
                }}
            }} else if (['.png', '.jpg', '.jpeg', '.gif', '.bmp'].includes(fileExtension)) {{
                // 创建图像元素
                const img = document.createElement('img');
                img.src = `data:${{mimeType}};base64,${{fileBase64}}`;
                img.alt = filePath.split('/').pop();
                container.appendChild(img);
            }} else {{
                // 创建文本内容元素
                const pre = document.createElement('pre');
                pre.className = 'text-content';
                pre.textContent = fileContent;
                container.appendChild(pre);
            }}
        }} catch (error) {{
            console.error('Error:', error);
            container.innerHTML = `<div class="error"><h2>Error loading file</h2><p>${{error.message}}</p></div>`;
        }}
    }}

    // 页面加载时执行内容加载
    window.onload = loadContent;
    </script>
</body>
</html>"""
