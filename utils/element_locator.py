"""
元素定位器 - 智能元素查找（混合策略优化版本 + 扁平化文本提取）
"""
import time
import random
import asyncio
import re
from typing import List, Optional
from core.config_manager import config_manager


class ElementLocator:
    """智能元素定位器 - 混合策略优化版本 + 扁平化文本提取"""
    
    def __init__(self, page):
        self.page = page
        self.selectors = config_manager.get('selectors', {})
        # 从统一配置中获取评论选择器
        self.comment_selectors = self.selectors.get('comments', {})
    
    async def find_element(self, element_type: str, timeout: int = 3000) -> Optional[any]:
        """查找元素 - 支持多种选择器（优化超时时间）"""
        selector_list = self.selectors.get(element_type, [])
        
        for selector in selector_list:
            try:
                # 使用更短的超时时间
                element = await self.page.query_selector(selector)
                if element and await element.is_visible():
                    return element
            except Exception:
                continue
        
        return None
    
    async def find_all_elements(self, element_type: str) -> List:
        """查找所有匹配元素"""
        selector_list = self.selectors.get(element_type, [])
        all_elements = []
        
        for selector in selector_list:
            try:
                elements = await self.page.query_selector_all(selector)
                if elements:
                    # 过滤可见元素
                    visible_elements = []
                    for element in elements:
                        if await element.is_visible():
                            visible_elements.append(element)
                    
                    if visible_elements:
                        all_elements.extend(visible_elements)
            except Exception:
                continue
        
        return all_elements
    
    async def click_element(self, element_type: str, index: int = 0, timeout: int = 3000) -> bool:
        """点击元素（优化超时时间）"""
        elements = await self.find_all_elements(element_type)
        
        if elements and len(elements) > index:
            try:
                await elements[index].click()
                return True
            except Exception:
                # 如果点击失败，尝试使用JavaScript点击
                try:
                    await elements[index].evaluate("element => element.click()")
                    return True
                except Exception:
                    pass
        
        return False
    
    async def type_text(self, element_type: str, text: str, clear_first: bool = True) -> bool:
        """在输入框中输入文本"""
        element = await self.find_element(element_type)
        
        if element:
            try:
                if clear_first:
                    await element.evaluate("element => element.value = ''")
                
                # 模拟人类输入，但减少延迟
                for char in text:
                    await element.type(char, delay=random.uniform(30, 100))  # 减少延迟范围
                
                return True
            except Exception:
                pass
        
        return False
    
    async def get_text(self, element_type: str, index: int = 0) -> str:
        """获取元素文本"""
        elements = await self.find_all_elements(element_type)
        
        if elements and len(elements) > index:
            try:
                text = await elements[index].inner_text()
                return text.strip()
            except Exception:
                pass
        
        return ""
    
    async def wait_for_element(self, element_type: str, timeout: int = 5000) -> bool:
        """等待元素出现（优化超时时间）"""
        start_time = time.time()
        
        while time.time() - start_time < timeout / 1000:
            element = await self.find_element(element_type)
            if element:
                return True
            await asyncio.sleep(0.3)  # 减少等待间隔
        
        return False
    
    async def wait_for_element_advanced(self, element_type: str, timeout: int = 8000, check_interval: int = 300) -> bool:
        """增强版元素等待 - 优化速度和检查间隔"""
        start_time = asyncio.get_event_loop().time()
        
        while (asyncio.get_event_loop().time() - start_time) < timeout / 1000:
            # 检查元素是否存在且可见
            element = await self.find_element(element_type)
            if element and await element.is_visible():
                return True
            
            # 简化的页面状态检查
            try:
                page_state = await self.page.evaluate("""
                    () => {
                        return document.readyState === 'complete';
                    }
                """)
                
                if page_state:
                    # 页面已就绪但元素未找到，提前退出
                    break
            except:
                pass
            
            await asyncio.sleep(check_interval / 1000)
        
        return False
    
    async def wait_for_element_with_retry(self, element_type: str, max_retries: int = 2, base_timeout: int = 3000) -> bool:
        """带重试机制的元素等待（减少重试次数）"""
        for attempt in range(max_retries):
            try:
                timeout = base_timeout * (attempt + 1)
                success = await self.wait_for_element_advanced(element_type, timeout)
                
                if success:
                    return True
                
                # 如果失败，尝试简单的恢复操作
                if attempt < max_retries - 1:
                    await self._quick_recovery_action(attempt)
                    
            except Exception as e:
                print(f"第 {attempt + 1} 次等待元素失败: {e}")
                
        return False
    
    async def _quick_recovery_action(self, attempt: int):
        """快速恢复操作"""
        recovery_actions = [
            lambda: asyncio.sleep(1),  # 简单等待
            lambda: self.page.evaluate("window.scrollBy(0, 200)")  # 小幅度滚动
        ]
        
        if attempt < len(recovery_actions):
            try:
                await recovery_actions[attempt]()
            except Exception as e:
                print(f"恢复操作失败: {e}")
    
    async def wait_for_multiple_elements(self, element_types: List[str], timeout: int = 6000) -> bool:
        """等待多个元素中的任意一个出现（优化超时时间）"""
        start_time = time.time()
        
        while time.time() - start_time < timeout / 1000:
            for element_type in element_types:
                if await self.find_element(element_type):
                    return True
            await asyncio.sleep(0.3)  # 减少等待间隔
        
        return False
    
    async def wait_for_element_count(self, element_type: str, min_count: int = 1, timeout: int = 6000) -> bool:
        """等待元素达到指定数量（优化超时时间）"""
        start_time = time.time()
        
        while time.time() - start_time < timeout / 1000:
            elements = await self.find_all_elements(element_type)
            if len(elements) >= min_count:
                return True
            await asyncio.sleep(0.3)  # 减少等待间隔
        
        return False
    
    async def is_element_visible(self, element_type: str) -> bool:
        """检查元素是否可见"""
        element = await self.find_element(element_type)
        return element is not None
    
    async def is_element_enabled(self, element_type: str, index: int = 0) -> bool:
        """检查元素是否可用"""
        elements = await self.find_all_elements(element_type)
        
        if elements and len(elements) > index:
            try:
                return await elements[index].is_enabled()
            except Exception:
                pass
        
        return False
    
    async def get_element_attribute(self, element_type: str, attribute: str, index: int = 0) -> str:
        """获取元素属性"""
        elements = await self.find_all_elements(element_type)
        
        if elements and len(elements) > index:
            try:
                value = await elements[index].get_attribute(attribute)
                return value or ""
            except Exception:
                pass
        
        return ""
    
    async def wait_for_element_state(self, element_type: str, state: str = "visible", timeout: int = 5000) -> bool:
        """等待元素达到特定状态（优化超时时间）"""
        selector_list = self.selectors.get(element_type, [])
        
        for selector in selector_list:
            try:
                if state == "visible":
                    await self.page.wait_for_selector(selector, state="visible", timeout=timeout)
                    return True
                elif state == "hidden":
                    await self.page.wait_for_selector(selector, state="hidden", timeout=timeout)
                    return True
                elif state == "attached":
                    await self.page.wait_for_selector(selector, state="attached", timeout=timeout)
                    return True
                elif state == "detached":
                    await self.page.wait_for_selector(selector, state="detached", timeout=timeout)
                    return True
            except Exception:
                continue
        
        return False
    
    async def wait_for_page_ready(self, timeout: int = 10000) -> bool:
        """快速等待页面就绪（大幅优化超时时间）"""
        try:
            # 只等待DOM内容加载，不等待网络空闲
            await self.page.wait_for_load_state('domcontentloaded', timeout=timeout)
            
            # 简化就绪检查
            ready = await self.page.evaluate("""
                () => {
                    return document.readyState === 'complete' && 
                           document.body && 
                           document.body.children.length > 5;
                }
            """)
            
            return ready
            
        except Exception as e:
            print(f"快速等待页面就绪失败: {e}")
            return True  # 即使失败也返回True，避免阻塞
    
    async def quick_wait(self, condition_type: str, **kwargs) -> bool:
        """快速等待 - 根据条件类型执行不同的等待策略"""
        wait_strategies = {
            'element': lambda: self.wait_for_element_advanced(kwargs.get('element_type'), kwargs.get('timeout', 5000)),
            'multiple_elements': lambda: self.wait_for_multiple_elements(kwargs.get('element_types', []), kwargs.get('timeout', 5000)),
            'element_count': lambda: self.wait_for_element_count(kwargs.get('element_type'), kwargs.get('min_count', 1), kwargs.get('timeout', 5000)),
            'page_ready': lambda: self.wait_for_page_ready(kwargs.get('timeout', 8000)),
            'element_state': lambda: self.wait_for_element_state(kwargs.get('element_type'), kwargs.get('state', 'visible'), kwargs.get('timeout', 5000))
        }
        
        strategy = wait_strategies.get(condition_type)
        if strategy:
            return await strategy()
        
        print(f"未知的等待条件类型: {condition_type}")
        return True  # 未知类型时默认返回True，避免阻塞
    
    async def take_screenshot_for_debug(self, filename: str = None):
        """截取调试截图（可选功能，默认不启用）"""
        # 为了性能，默认不截图
        return True
    
    # ------------------------- 新增扁平化文本提取方法 -------------------------
    
    async def extract_flattened_text(self, element) -> str:
        """扁平化文本提取 - 解决多层span和emoji问题"""
        try:
            # 在浏览器端执行文本提取，处理碎片化文本
            text = await element.evaluate("""
                (element) => {
                    // 创建TreeWalker遍历所有文本节点
                    const walker = document.createTreeWalker(
                        element, 
                        NodeFilter.SHOW_TEXT, 
                        null, 
                        false
                    );
                    
                    let fullText = '';
                    let node;
                    while (node = walker.nextNode()) {
                        fullText += node.textContent;
                    }
                    
                    // 处理emoji图片，提取alt文本
                    const emojiImages = element.querySelectorAll('img[alt]');
                    emojiImages.forEach(img => {
                        fullText += img.alt;
                    });
                    
                    // 清理文本：合并多余空格，去除首尾空白
                    return fullText.replace(/\\s+/g, ' ').trim();
                }
            """)
            
            return text if text else ""
            
        except Exception as e:
            print(f"扁平化文本提取失败: {e}")
            # 降级到普通inner_text
            try:
                return await element.inner_text() or ""
            except:
                return ""

    async def extract_comment_data_enhanced(self, comment_element) -> dict:
        """增强版评论数据提取"""
        try:
            # 确保评论元素在视口中
            await comment_element.scroll_into_view_if_needed()
            await asyncio.sleep(0.1)  # 等待虚拟化渲染

            # 提取用户名和主页URL
            username = ""
            user_url = ""
            user_selectors = self.comment_selectors.get('user_name', [])
            
            for selector in user_selectors:
                try:
                    user_element = await comment_element.query_selector(selector)
                    if user_element:
                        username = await user_element.inner_text()
                        user_url = await user_element.get_attribute('href')
                        if user_url and user_url.startswith("//"):
                            user_url = "https:" + user_url
                        elif user_url and user_url.startswith("/"):
                            from urllib.parse import urljoin
                            user_url = urljoin("https://www.douyin.com", user_url)
                        break
                except:
                    continue

            # 使用扁平化文本提取评论内容
            content = await self.extract_flattened_text(comment_element)
            if not content:
                return None

            # 提取IP属地
            ip_location = await self._extract_ip_location_enhanced(comment_element)

            return {
                'username': username.strip(),
                'user_url': user_url,
                'content': content.strip(),
                'ip_location': ip_location,
                'element': comment_element
            }
            
        except Exception as e:
            print(f"增强版评论数据提取失败: {e}")
            return None

    async def _extract_ip_location_enhanced(self, comment_element) -> str:
        """增强版IP属地提取"""
        try:
            # 优先使用包含位置图标的选择器
            ip_selectors = self.comment_selectors.get('ip_location', [])
            
            for selector in ip_selectors:
                try:
                    ip_element = await comment_element.query_selector(selector)
                    if ip_element:
                        ip_text = await ip_element.inner_text()
                        if ip_text and ip_text.strip():
                            return ip_text.strip()
                except:
                    continue
                    
            return ""
        except:
            return ""


    async def extract_video_desc_enhanced(self, page) -> str:
        """增强版视频文案提取 - 三段式兜底策略"""
        # 第一段：data属性选择器
        data_selectors = [
            '[data-e2e="video-desc"]',
            '[data-e2e*="desc"]',
            '[data-e2e*="title"]'
        ]
        
        for selector in data_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    text = await element.inner_text()
                    if text and text.strip():
                        return text.strip()
            except:
                continue
        
        # 第二段：OG meta标签
        try:
            meta_element = await page.query_selector('meta[property="og:description"]')
            if meta_element:
                content = await meta_element.get_attribute('content')
                if content and content.strip():
                    return content.strip()
        except:
            pass
        
        # 第三段：页面JSON数据
        try:
            json_data = await self._extract_desc_from_json(page)
            if json_data:
                return json_data
        except:
            pass
        
        # 最终兜底：页面标题
        try:
            title = await page.title()
            if title and "抖音" not in title:  # 简单的过滤
                return title
        except:
            pass
            
        return "无描述"

    async def _extract_desc_from_json(self, page):
        """从页面JSON数据中提取视频描述"""
        try:
            # 查找包含视频数据的script标签
            script_selectors = [
                'script#RENDER_DATA',
                'script[type="application/json"]',
                'script[data-react-helmet]'
            ]
            
            for selector in script_selectors:
                try:
                    script_element = await page.query_selector(selector)
                    if script_element:
                        script_content = await script_element.inner_text()
                        if script_content:
                            import urllib.parse
                            decoded_content = urllib.parse.unquote(script_content)
                            
                            # 尝试解析JSON并查找描述字段
                            desc_patterns = [
                                r'"desc":"([^"]+)"',
                                r'"aweme_desc":"([^"]+)"',
                                r'"video_desc":"([^"]+)"',
                                r'"title":"([^"]+)"'
                            ]
                            
                            for pattern in desc_patterns:
                                matches = re.findall(pattern, decoded_content)
                                if matches and matches[0]:
                                    return matches[0]
                except:
                    continue
                    
            return None
        except:
            return None

    # ------------------------- 新增评论相关方法 -------------------------
    

    
    async def find_all_comment_items(self) -> List:
        """查找所有评论项"""
        all_elements = []
        selector_list = self.comment_selectors.get('comment_item', [])
        
        for selector in selector_list:
            try:
                elements = await self.page.query_selector_all(selector)
                if elements:
                    # 过滤可见元素
                    visible_elements = []
                    for element in elements:
                        if await element.is_visible():
                            visible_elements.append(element)
                    
                    if visible_elements:
                        all_elements.extend(visible_elements)
            except Exception:
                continue
        
        return all_elements
    
    async def extract_comment_data(self, comment_element) -> dict:
        """从评论元素中提取数据"""
        try:
            # 提取用户名和主页URL
            username = ""
            user_url = ""
            user_selectors = self.comment_selectors.get('user_name', [])
            
            for selector in user_selectors:
                try:
                    user_element = await comment_element.query_selector(selector)
                    if user_element:
                        username = await user_element.inner_text()
                        user_url = await user_element.get_attribute('href')
                        if user_url and user_url.startswith("//"):
                            user_url = "https:" + user_url
                        elif user_url and user_url.startswith("/"):
                            from urllib.parse import urljoin
                            user_url = urljoin("https://www.douyin.com", user_url)
                        break
                except:
                    continue
            
            # 提取评论内容
            content = ""
            content_selectors = self.comment_selectors.get('comment_text', [])
            
            for selector in content_selectors:
                try:
                    content_element = await comment_element.query_selector(selector)
                    if content_element:
                        content_text = await content_element.inner_text()
                        if content_text and content_text.strip():
                            content = content_text.strip()
                            break
                except:
                    continue
            
            # 提取IP属地
            ip_location = ""
            ip_selectors = self.comment_selectors.get('ip_location', [])
            
            for selector in ip_selectors:
                try:
                    ip_element = await comment_element.query_selector(selector)
                    if ip_element:
                        ip_text = await ip_element.inner_text()
                        if ip_text and ip_text.strip():
                            ip_location = ip_text.strip()
                            break
                except:
                    continue
            
            return {
                'username': username,
                'user_url': user_url,
                'content': content,
                'ip_location': ip_location,
                'element': comment_element
            }
            
        except Exception as e:
            print(f"提取评论数据失败: {e}")
            return {}
    


class PageStateDetector:
    """页面状态检测器 - 优化速度版本 + 增强功能"""
    
    def __init__(self, page):
        self.page = page
        self.locator = ElementLocator(page)
    
    async def is_video_page(self) -> bool:
        """快速检测是否在视频页面"""
        current_url = self.page.url
        
        # 简化的URL模式检测
        video_patterns = [
            "douyin.com/?recommend=1",
            "douyin.com/video/",
            "v.douyin.com/"
        ]
        
        for pattern in video_patterns:
            if pattern in current_url:
                return True
        
        # 快速元素检测
        if await self.locator.is_element_visible('video_player'):
            return True
        
        return False
    
    async def is_comments_open(self) -> bool:
        """快速检测评论区是否打开"""
        # 快速检查评论区域是否存在
        comment_elements = await self.locator.find_all_comment_items()
        return len(comment_elements) > 0
    
    async def is_search_results_page(self) -> bool:
        """快速检测是否在搜索结果页"""
        current_url = self.page.url
        return "douyin.com/search" in current_url and "type=video" in current_url
    
    async def is_user_profile_page(self) -> bool:
        """快速检测是否在用户主页"""
        current_url = self.page.url
        return "douyin.com/user" in current_url
    
    async def is_message_page(self) -> bool:
        """快速检测是否在私信页面"""
        current_url = self.page.url
        return "message" in current_url.lower() or "chat" in current_url.lower()
    
    async def wait_for_video_load(self, timeout: int = 6000) -> bool:
        """快速等待视频加载完成"""
        start_time = time.time()
        
        while time.time() - start_time < timeout / 1000:
            if await self.is_video_page():
                # 快速等待视频播放器
                if await self.locator.wait_for_element('video_player', 2000):
                    return True
            await asyncio.sleep(0.3)  # 减少等待间隔
        
        return False
    
    async def wait_for_search_results(self, timeout: int = 8000) -> bool:
        """快速等待搜索结果加载完成"""
        return await self.locator.quick_wait('element_count', 
                                           element_type='search_results', 
                                           min_count=1, 
                                           timeout=timeout)
    
    async def wait_for_comments_section(self, timeout: int = 6000) -> bool:
        """快速等待评论区加载完成"""
        return await self.locator.quick_wait('element_count',
                                           element_type='comment_item',
                                           min_count=1,
                                           timeout=timeout)
    
    async def wait_for_comments_loaded(self, timeout: int = 10000) -> bool:
        """等待评论区加载完成（简化版）- 现在只检查评论项存在"""
        try:
            # 直接等待评论项出现，不再检查评论容器
            return await self.locator.wait_for_element_count('comment_item', min_count=1, timeout=5000)
            
        except Exception as e:
            print(f"等待评论区加载失败: {e}")
            return False

    async def get_page_load_status(self) -> dict:
        """快速获取页面加载状态"""
        try:
            status = await self.page.evaluate("""
                () => {
                    return {
                        readyState: document.readyState,
                        hasVideo: !!document.querySelector('video'),
                        hasContent: document.body && document.body.children.length > 0
                    };
                }
            """)
            return status
        except Exception as e:
            return {}
    
    async def is_page_stable(self, check_interval: float = 0.5, max_checks: int = 2) -> bool:
        """快速检测页面是否稳定（大幅减少检查次数）"""
        try:
            previous_state = await self._get_page_state_snapshot()
            
            for i in range(max_checks):
                await asyncio.sleep(check_interval)
                current_state = await self._get_page_state_snapshot()
                
                # 简化的稳定性检查
                if previous_state['domElementCount'] != current_state['domElementCount']:
                    return False
                
                previous_state = current_state
            
            return True
            
        except Exception as e:
            return True  # 出错时默认稳定
    
    async def _get_page_state_snapshot(self) -> dict:
        """快速获取页面状态快照"""
        try:
            return await self.page.evaluate("""
                () => {
                    return {
                        domElementCount: document.getElementsByTagName('*').length,
                        timestamp: Date.now()
                    };
                }
            """)
        except:
            return {'domElementCount': 0, 'timestamp': 0}
    
    async def quick_page_check(self) -> bool:
        """快速页面检查 - 最基本的页面就绪检查"""
        try:
            return await self.page.evaluate("""
                () => {
                    return document.readyState === 'complete' && 
                           document.body && 
                           document.body.children.length > 0;
                }
            """)
        except:
            return False

    # 新增：用户主页就绪检测方法
    async def wait_for_user_profile_ready(self, timeout: int = 12000) -> bool:
        """
        用户主页就绪判定：
        1) URL 包含 /user
        2) readyState ∈ {interactive, complete}
        3) 关键元素出现（关注 或 私信 按钮任意可见）
        4) DOM 稳定：domElementCount 在 3*200ms 采样内波动 < 2%
        """
        page = self.page
        start = time.time()

        async def _dom_ready():
            try:
                return await page.evaluate("() => document.readyState === 'interactive' || document.readyState === 'complete'")
            except:
                return False

        async def _url_ok():
            try:
                return "douyin.com/user" in page.url
            except:
                return False

        async def _key_elements_visible():
            # 复用已有定位器的增强等待
            return await self.locator.wait_for_element_advanced('message_button', 2000) \
                or await self.locator.wait_for_element_advanced('follow_button', 2000)

        async def _dom_stable(samples=3, interval=0.2, jitter=0.05, tolerance=0.02):
            try:
                counts = []
                for _ in range(samples):
                    snap = await self._get_page_state_snapshot()  # 已有：返回 {domElementCount, timestamp}
                    counts.append(snap.get('domElementCount', 0))
                    await asyncio.sleep(interval + random.uniform(0, jitter))
                mi, ma = min(counts), max(counts)
                return (mi > 0) and ((ma - mi) / max(ma, 1) <= tolerance)
            except:
                return False

        while time.time() - start < timeout/1000:
            if await _url_ok() and await _dom_ready() and await _key_elements_visible() and await _dom_stable():
                print(f"✅ 用户主页就绪检测通过: URL={await _url_ok()}, DOM={await _dom_ready()}, 元素={await _key_elements_visible()}, 稳定={await _dom_stable()}")
                return True
            await asyncio.sleep(0.2)

        print(f"❌ 用户主页就绪检测失败: URL={await _url_ok()}, DOM={await _dom_ready()}, 元素={await _key_elements_visible()}, 稳定={await _dom_stable()}")
        return False