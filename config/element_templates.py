"""
抖音元素视觉定位配置
包含模板图像路径和颜色范围配置
"""

import numpy as np

# 抖音元素视觉定位配置 - 只保留私信阶段必需项
DOUYIN_VISION_CONFIG = {
    # 关注按钮 - 红色矩形
    'follow_button': {
        'color_range': (
            np.array([0, 100, 100]),    # 红色下限 (HSV)
            np.array([10, 255, 255])    # 红色上限
        ),
        'template_images': [
            'templates/follow_button.png',
            'templates/follow_button_red.png'
        ],
        'min_size': 60,
        'states': {
            'followed': {
                'color_range': (
                    np.array([0, 0, 100]),  # 灰色
                    np.array([180, 50, 150])
                )
            }
        }
    },
    
    # 私信按钮
    'message_button': {
        'color_range': (
            np.array([100, 100, 100]),  # 蓝色下限
            np.array([130, 255, 255])   # 蓝色上限
        ),
        'template_images': [
            'templates/message_button.png'
        ],
        'min_size': 30
    },
    
    # 私信输入框
    'message_input': {
        'color_range': (
            np.array([0, 0, 200]),      # 白色区域
            np.array([180, 50, 255])
        ),
        'template_images': [
            'templates/message_input.png'
        ],
        'min_size': 150
    },
    
    # 发送按钮
    'send_button': {
        'color_range': (
            np.array([100, 100, 150]),  # 亮蓝色下限
            np.array([130, 200, 255])   # 亮蓝色上限
        ),
        'template_images': [
            'templates/send_button.png',
            'templates/send_button_active.png'
        ],
        'min_size': 25,
        'states': {
            'disabled': {
                'color_range': (
                    np.array([0, 0, 100]),  # 灰色
                    np.array([180, 50, 150])
                )
            }
        }
    }
}

def get_element_region(element_type):
    """获取元素的屏幕区域"""
    screen_regions = {
        'follow_button': (0.3, 0.1, 0.7, 0.3),    # 上方区域
        'message_button': (0.3, 0.1, 0.7, 0.3),   # 上方区域
        'message_input': (0.1, 0.7, 0.9, 0.9),    # 下方区域（聊天框）
        'send_button': (0.7, 0.8, 0.9, 0.9),      # 右下角区域
    }
    
    return screen_regions.get(element_type, None)

def convert_region_to_pixels(region_percent, screen_width, screen_height):
    """将百分比区域转换为像素坐标"""
    if not region_percent:
        return None
    
    left = int(region_percent[0] * screen_width)
    top = int(region_percent[1] * screen_height)
    right = int(region_percent[2] * screen_width)
    bottom = int(region_percent[3] * screen_height)
    
    return (left, top, right, bottom)