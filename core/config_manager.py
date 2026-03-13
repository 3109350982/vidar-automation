"""
配置管理器
"""
import os
import json
from typing import Dict, Any

class ConfigManager:
    """配置管理器 - 单例模式"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.config = {}
            self.load_config()
            self.initialized = True
    
    def load_config(self):
        """加载配置"""
        try:
            from config.settings import (
                BROWSER_CONFIG, DOUYIN_CONFIG, 
                SELECTORS, BEHAVIOR_CONFIG, SAFETY_CONFIG
            )
            
            self.config = {
                'browser': BROWSER_CONFIG,
                'douyin': DOUYIN_CONFIG,
                'selectors': SELECTORS,
                'behavior': BEHAVIOR_CONFIG,
                'safety': SAFETY_CONFIG
            }
        except ImportError as e:
            print(f"配置加载失败: {e}")
            self.config = {}
    
    def get(self, key: str, default=None):
        """获取配置值"""
        keys = key.split('.')
        value = self.config
        
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def update(self, key: str, value: Any):
        """更新配置值"""
        keys = key.split('.')
        config = self.config
        
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        config[keys[-1]] = value
    
    def save_to_file(self, filepath: str):
        """保存配置到文件"""
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存配置失败: {e}")

# 全局配置实例
config_manager = ConfigManager()