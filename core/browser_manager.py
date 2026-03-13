"""
浏览器管理器 - 单例模式（集成视觉助手版本 + 资源屏蔽优化 + 并发安全修复）
"""
import time
import asyncio
import contextlib
from playwright.async_api import async_playwright
from core.config_manager import config_manager
from utils.element_locator import ElementLocator, PageStateDetector
import random

class BrowserManager:
    """浏览器管理器 - 单例模式（集成视觉助手 + 资源屏蔽优化 + 并发安全修复）"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.playwright = None
            self.browser = None
            self.page = None
            self.is_running = False
            self.initialized = True
            self.locator = None
            self.state_detector = None
            self._resource_route_handler = None
            self.vision_helper = None  # 新增：视觉助手实例
            
            # 新增：并发安全控制
            self._op_lock = asyncio.Lock()   # 起/停/自检全局互斥
            self.user_data_dir = "./browser_data"  # 提取配置，便于访问
    
    async def _unsafe_close_locked(self):
        """仅在持有 _op_lock 时调用；不再在异常里"顺手关别人"的实例。"""
        self.is_running = False
        with contextlib.suppress(Exception):
            if self.browser:
                await self.browser.close()
        with contextlib.suppress(Exception):
            if self.playwright:
                await self.playwright.stop()
        self.browser = None
        self.page = None
        self.playwright = None

    async def start_browser(self, headless=False,user_data_dir=None):
        """启动浏览器（集成视觉助手 + 并发安全修复）"""
        async with self._op_lock:
            # 运行中直接短路返回；顺带做一次轻探活
            if self.is_running and self.page is not None:
                try:
                    await self.page.title()
                    print("✅ 浏览器已在运行，直接返回")
                    return True
                except Exception:
                    # 当前实例坏了，先把自己已知对象收干净
                    print("⚠️ 浏览器实例异常，清理后重启")
                    await self._unsafe_close_locked()

            # 退避重试：处理 profile 锁/偶发快速退出
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                try:
                    print(f"🔄 启动浏览器尝试 {attempt}/{max_attempts}...")
                    self.playwright = await async_playwright().start()
                    
                    browser_config = config_manager.get('browser', {})
                    browser_config['headless'] = headless
                    
                    self.browser = await self.playwright.chromium.launch_persistent_context(
                        user_data_dir=(user_data_dir or browser_config.get('user_data_dir', './browser_data')),
                        channel="msedge",
                        headless=browser_config.get('headless', False),
                        viewport=browser_config.get('viewport', {'width': 1366, 'height': 768}),
                        user_agent=browser_config.get('user_agent'),
                        args=browser_config.get('args', [])
                    )
                    
                    self.page = self.browser.pages[0] if self.browser.pages else await self.browser.new_page()
                    self.user_data_dir = (user_data_dir or browser_config.get('user_data_dir', './browser_data'))

                    await self.page.set_viewport_size(browser_config.get('viewport', {'width': 1366, 'height': 768}))
                             
                    # 隐藏自动化特征
                    await self.page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined,
                        });
                        window.chrome = { runtime: {} };
                        
                        // 覆盖 permissions 属性
                        const originalQuery = window.navigator.permissions.query;
                        window.navigator.permissions.query = (parameters) => (
                            parameters.name === 'notifications' ?
                                Promise.resolve({ state: Notification.permission }) :
                                originalQuery(parameters)
                        );
                        
                        // 覆盖 languages 属性
                        Object.defineProperty(navigator, 'languages', {
                            get: () => ['zh-CN', 'zh', 'en'],
                        });
                        // 覆盖 platform、硬件参数等指纹属性
                        Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
                        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
                        Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
                        
                        # // Canvas 指纹伪装：修改 toDataURL 返回固定值
                        # const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
                        # HTMLCanvasElement.prototype.toDataURL = function(...args) {
                        #     // 返回一个固定的1x1透明PNG，避免通过 Canvas 绘图数据指纹识别
                        #     return "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB...";
                        # };
                        
                        // WebGL 指纹伪装：固定 GPU厂商 和 渲染器 字符串
                        const getParameter = WebGLRenderingContext.prototype.getParameter;
                        WebGLRenderingContext.prototype.getParameter = function(param) {
                            // 拦截 UNMASKED_VENDOR_WEBGL 和 UNMASKED_RENDERER_WEBGL 查询
                            if (param === 37445) return "Intel Inc.";
                            if (param === 37446) return "Intel(R) Iris(R) Graphics";
                            return getParameter.call(this, param);
                        };
                        if (window.WebGL2RenderingContext) {
                            window.WebGL2RenderingContext.prototype.getParameter = WebGLRenderingContext.prototype.getParameter;
                        }
                        
                        // 模拟常见插件列表和 MIME 类型
                        Object.defineProperty(navigator, 'plugins', {
                            get: () => [ { name: "Chrome PDF Plugin", filename: "internal-pdf-viewer", description: "Portable Document Format" } ]
                        });
                        Object.defineProperty(navigator, 'mimeTypes', {
                            get: () => [ { type: "application/pdf", suffixes: "pdf", description: "", enabledPlugin: navigator.plugins[0] } ]
                        });
                    """)
                    
                    # 拦截统计请求
                    await self.page.route("**/hybridaction/**", lambda route: route.fulfill(
                        status=200,
                        body="{}"
                    ))
                    
                    # 初始化工具
                    self.locator = ElementLocator(self.page)
                    self.state_detector = PageStateDetector(self.page)
                    
                    # 新增：初始化视觉助手
                    from core.vision_helper import get_vision_helper
                    self.vision_helper = get_vision_helper(self.page)
                    print("✅ 视觉助手初始化完成")
                    
                    # 导航到抖音推荐页
                    douyin_config = config_manager.get('douyin', {})
                    recommend_url = douyin_config.get('recommend_url', 'https://www.xiaohongshu.com/explore')
                    
                    print("正在导航到小红书推荐页...")
                    await self.page.goto(recommend_url, wait_until='domcontentloaded', timeout=60000)
                    
                    # 等待页面加载
                    await self.page.wait_for_load_state('domcontentloaded')
                    from utils.advanced_anti_detection import anti_detection
                    await anti_detection.human_like_delay(3, 5, 'browser_start')
                    
                    # 设置焦点到视频区域（DOM方式）
                    print("🖱️ 设置焦点到视频区域...")
                    await self._ensure_page_focus_dom()
                    
                    # 轻探活，确认 page 可用再置 True
                    await self.page.title()
                    self.is_running = True
                    
                    print(f"✅ 浏览器启动完成（第{attempt}次尝试成功）")
                    return True
                    
                except Exception as e:
                    # 不要在这里再 close()！只清本地引用并决定是否重试
                    self.browser = None
                    self.page = None
                    # playwright 可能已起，再停一次确保干净
                    with contextlib.suppress(Exception):
                        if self.playwright:
                            await self.playwright.stop()
                    self.playwright = None

                    if attempt < max_attempts:
                        # 1.5s 递增退避
                        wait_time = 1.5 + 0.5 * (attempt - 1)
                        print(f"⚠️ 启动失败，{wait_time}秒后重试: {e}")
                        await asyncio.sleep(wait_time)
                        continue
                    # 最终失败
                    self.is_running = False
                    print(f"❌ 启动浏览器失败（{max_attempts}次尝试均失败）: {e}")
                    return False

    async def ensure_running(self):
        """确保浏览器在运行状态 - 并发安全修复版"""
        # 先在锁内校验当前实例；如果坏了只关闭"自己知道的"
        async with self._op_lock:
            if self.is_running and self.page is not None:
                try:
                    await self.page.title()
                    return True
                except Exception:
                    print("⚠️ 浏览器实例异常，清理后重启")
                    await self._unsafe_close_locked()

        # 锁外启动（内部会再拿锁），避免长时间占锁
        return await self.start_browser(headless=False)

    async def close(self):
        """关闭浏览器 - 并发安全修复版"""
        async with self._op_lock:
            await self._unsafe_close_locked()
            print("✅ 浏览器安全关闭完成")

    async def _ensure_page_focus_dom(self):
        """确保页面获得焦点（DOM方式）"""
        try:
            # 使用DOM方式设置焦点
            selectors = [
                '[data-e2e="video-player"]',
                '.xgplayer-container',
                'video',
                'body'
            ]
            
            for selector in selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        await element.click()
                        print(f"✅ 成功点击: {selector}")
                        break
                except:
                    continue
            
            from utils.advanced_anti_detection import anti_detection
            await anti_detection.human_like_delay(2, 3, 'browser_start')
            
            # 额外确保页面激活
            await self.page.evaluate("() => { window.focus(); }")
            
        except Exception as e:
            print(f"⚠️ 设置焦点时出错: {e}")

    async def press_key(self, key: str, delay: float = None):
        """按键盘按键（集成反检测）"""
        if delay:
            from utils.advanced_anti_detection import anti_detection
            await anti_detection.human_like_delay(delay * 0.5, delay * 1.5, 'key_press')
        
        try:
            # 添加随机微小延迟，模拟人类按键时机
            await asyncio.sleep(random.uniform(0.05, 0.2))
            
            await self.page.keyboard.press(key)
            print(f"⌨️ 按下按键: {key}")
            return True
        except Exception as e:
            print(f"❌ 按键失败 {key}: {e}")
            return False
    
    async def ensure_video_page(self):
        """确保在视频页面"""
        if not await self.state_detector.is_video_page():
            from utils.advanced_anti_detection import anti_detection
            print("🔄 重新导航到视频页面...")
            douyin_config = config_manager.get('douyin', {})
            recommend_url = douyin_config.get('recommend_url', 'https://www.xiaohongshu.com/explore')
            await self.page.goto(recommend_url, wait_until='domcontentloaded')
            await anti_detection.human_like_delay(3, 5, 'browser_start')
            await self._ensure_page_focus_dom()
        
        return await self.state_detector.is_video_page()
    
    async def get_page_info(self):
        """获取页面信息"""
        try:
            url = self.page.url
            title = await self.page.title()
            
            return {
                'url': url,
                'title': title,
                'is_video_page': await self.state_detector.is_video_page(),
                'is_comments_open': await self.state_detector.is_comments_open()
            }
        except Exception as e:
            print(f"获取页面信息失败: {e}")
            return {}

    async def switch_profile(self, user_data_dir: str) -> bool:
        """
        切换到新的用户数据目录（多账号轮询用）。
        仅关闭当前实例并以新的 user_data_dir 重新启动，保持其他配置不变。
        """
        user_data_dir = (user_data_dir or "").strip()
        if not user_data_dir:
            return False

        # 1. 在锁内安全关闭当前实例
        async with self._op_lock:
            try:
                await self._unsafe_close_locked()
            except Exception:
                pass

        # 2. 锁外重启浏览器（start_browser 内部会自己再加锁）
        #    为了扫码，一律用带界面的模式（headless=False）
        ok = await self.start_browser(headless=False, user_data_dir=user_data_dir)
        return bool(ok)



# 全局浏览器管理器实例
browser_manager = BrowserManager()