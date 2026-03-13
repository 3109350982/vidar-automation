"""
系统配置文件
"""

# 浏览器配置
BROWSER_CONFIG = {
    'user_data_dir': "./browser_data",
    'viewport': {'width': 1366, 'height': 768},
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    'headless': False,
    'timeout': 30000,
    'args': [
        '--disable-blink-features=AutomationControlled',
        '--no-sandbox',
        '--disable-dev-shm-usage',
        '--start-maximized',
        '--disable-extensions',
        '--disable-plugins',
        '--disable-translate',
    ]
}

# 抖音配置
DOUYIN_CONFIG = {
    'recommend_url': 'https://www.xiaohongshu.com/explore',
    'base_url': 'https://www.xiaohongshu.com',
    'shortcuts': {
        'next_video': 'ArrowDown',
        'like': 'z',
    }
}

# 元素选择器配置
SELECTORS = {
    # 搜索相关
    'search_input': [
        '[data-e2e="search-input"]',
        'input[placeholder*="搜索"]',
        'input[type="search"]'
    ],
    'search_results': [
        '[data-e2e="search-video-item"]',
        '.video-item',
        '[class*="video-card"]'
    ],
    
    # 视频相关
    'video_desc': ['[data-e2e="video-desc"]','.public-DraftStyleDefault-block','.title','.B0w9b'],
    'video_player': [
        '[data-e2e="video-player"]',
        '.xgplayer-container',
        'video'
    ],
    
    # 评论相关 - 统一配置
    'comments': {
        'comment_container': [
            'div[data-scroll="comment"]',
            '.comment-list',
            '.comments-container',
            '[class*="comment-list"]',
            '[class*="comments-container"]',
            'div[style*="overflow"]'
        ],
        'comment_item': [
            'div[data-e2e="comment-item"]',
            '[class*="comment-item"]',
            '.comment-item'
        ],
        'comment_text': [
            'span[class*="comment-text"]',
            'div[class*="comment-text"]',
            'span[class*="text"]',
            'div[class*="content"]',
            'p[class*="comment"]'
        ],
        'user_name': [
            'a[href^="//www.douyin.com/user/"]',
            '[class*="nickname"]',
            '[class*="username"]'
        ],
        'user_avatar': [
            'img[class*="avatar"]',
            '[class*="avatar"] img'
        ],
        'ip_location': [
            'span:has(img[src*="loc"])',
            '[class*="ip-label"]',
            '[class*="location"]'
        ]
    },
    
    # 私信相关
    'follow_button': [
        'button:has-text("关注")',
        '[data-e2e="follow-btn"]'
    ],
    'message_button': [
        'button:has-text("发消息")',
        '[data-e2e="message-btn"]'
    ],
    'message_input': [
        'textarea',
        'input[type="text"]',
        '[contenteditable="true"]'
    ],
    'send_button': [
        'button:has-text("发送")',
        '[data-e2e="send-btn"]'
    ]
}

# 行为配置
BEHAVIOR_CONFIG = {
    'min_watch_time': 5,
    'max_watch_time': 25,
    'like_probability': 0.7,
    'scroll_delay_min': 1,
    'scroll_delay_max': 3,
    'operation_delay_min': 0.5,
    'operation_delay_max': 2
}

# 安全配置
SAFETY_CONFIG = {
    'max_operations_per_minute': 30,
    'max_daily_runtime': 2 * 60 * 60,  # 2小时
    'rest_interval': 5 * 60  # 5分钟休息
}