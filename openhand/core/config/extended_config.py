from typing import Any

from pydantic import RootModel


class ExtendedConfig(RootModel[dict[str, Any]]):
    """扩展功能的配置类。
    
    这个类实现为根模型，使得整个输入都存储为根值。
    这允许存储任意的键值对，之后可以通过属性或字典风格的访问方式获取。
    
    Args:
        root: 字典类型的配置数据，键为字符串，值为任意类型
        
    Note:
        使用RootModel可以让用户在配置中添加任意的自定义字段，
        这些字段会被保存并可以通过多种方式访问
    """

    def __str__(self) -> str:
        """返回ExtendedConfig对象的字符串表示。
        
        使用根字典构建字符串表示形式。
        
        Returns:
            str: 格式化的字符串，形如 'ExtendedConfig(key1=value1, key2=value2)'
        """
        # 获取根字典数据
        root_dict: dict[str, Any] = self.model_dump()
        # 构建属性字符串列表，每个属性格式为 'key=repr(value)'
        attr_str = [f'{k}={repr(v)}' for k, v in root_dict.items()]
        # 返回格式化的字符串表示
        return f'ExtendedConfig({", ".join(attr_str)})'

    def __repr__(self) -> str:
        """返回ExtendedConfig对象的正式字符串表示。
        
        Returns:
            str: 与__str__相同的字符串表示
        """
        return self.__str__()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'ExtendedConfig':
        """从字典创建ExtendedConfig实例。
        
        直接通过包装输入字典来创建实例。
        
        Args:
            data: 要转换为ExtendedConfig的字典数据
            
        Returns:
            ExtendedConfig: 新创建的ExtendedConfig实例
        """
        # 直接使用字典数据创建实例
        return cls(data)

    def __getitem__(self, key: str) -> Any:
        """提供字典风格的访问方式。
        
        通过根字典提供类似字典的访问接口。
        
        Args:
            key: 要获取的配置项的键名
            
        Returns:
            Any: 对应键的值
            
        Raises:
            KeyError: 当键不存在时抛出
        """
        # 获取根字典并返回对应键的值
        root_dict: dict[str, Any] = self.model_dump()
        return root_dict[key]

    def __getattr__(self, key: str) -> Any:
        """提供属性访问的回退机制。
        
        当通过点号访问属性时，如果属性不存在，则尝试从根字典中获取。
        
        Args:
            key: 要获取的属性名
            
        Returns:
            Any: 对应属性的值
            
        Raises:
            AttributeError: 当属性不存在时抛出，提供清晰的错误信息
        """
        try:
            # 尝试从根字典中获取属性值
            root_dict: dict[str, Any] = self.model_dump()
            return root_dict[key]
        except KeyError as e:
            # 将KeyError转换为AttributeError，提供更符合Python规范的错误信息
            raise AttributeError(
                f"'ExtendedConfig' object has no attribute '{key}'"
            ) from e