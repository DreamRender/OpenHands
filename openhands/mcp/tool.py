from mcp.types import Tool
from pydantic import ConfigDict


class MCPClientTool(Tool):
    """
    MCP客户端工具代理类，表示可以从客户端调用的MCP服务器端工具。
    
    该类继承自MCP的Tool类型，作为服务器端工具在客户端的代理表示。
    与之前的版本不同，这个版本不存储Session引用，因为Session是由MCPClient
    为每个操作按需创建的。
    
    该类的主要作用：
    - 存储工具的元数据信息（名称、描述、输入Schema等）
    - 提供工具参数格式转换功能
    - 作为服务器端工具在客户端的代理对象
    
    继承关系:
        Tool: MCP协议定义的基础工具类型
        
    Note:
        该版本不存储Session引用，Session由MCPClient按需创建以确保连接的灵活性
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    """Pydantic模型配置，允许任意类型以支持复杂的工具参数类型"""

    def to_param(self) -> dict:
        """
        将工具转换为函数调用格式。
        
        该方法将MCP工具的元数据转换为标准的函数调用参数格式，
        通常用于与Agent或其他需要函数调用格式的组件集成。
        
        转换后的格式符合OpenAI函数调用规范，包含：
        - type: 固定为'function'，表示这是一个函数调用
        - function: 包含函数的详细信息
          - name: 函数名称（对应工具名称）
          - description: 函数描述（对应工具描述）
          - parameters: 函数参数Schema（对应工具输入Schema）
        
        Returns:
            dict: 包含工具信息的函数调用格式字典，格式如下:
                {
                    'type': 'function',
                    'function': {
                        'name': str,         # 工具名称
                        'description': str,  # 工具描述
                        'parameters': dict,  # 输入参数Schema
                    }
                }
                
        Note:
            返回的格式可以直接用于Agent的工具调用系统，
            特别是与基于函数调用的AI模型（如OpenAI GPT）集成
        """
        return {
            'type': 'function',              # 标记为函数类型
            'function': {
                'name': self.name,           # 工具名称作为函数名
                'description': self.description,  # 工具描述作为函数描述
                'parameters': self.inputSchema,   # 工具输入Schema作为函数参数
            },
        }