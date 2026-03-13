"""
字符串处理工具
"""
import re

def split_list(s: str) -> list[str]:
    """
    统一字符串分割函数
    支持空格、逗号、分号、中文逗号、中文分号等多种分隔符
    """
    if not s or not s.strip():
        return []
    
    # 使用正则表达式分割，支持多种分隔符
    parts = re.split(r"[\s,，;；]+", s.strip())
    
    # 过滤空字符串并去除前后空格
    return [p.strip() for p in parts if p.strip()]