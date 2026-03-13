# services/customer_acquisition.py
"""
获客服务（修复IP提取和评论采集问题 + 整页丝滑滑动版本）
"""
import asyncio
import json
import re
from urllib.parse import quote, urljoin
from services.base_service import BaseService
from core.config_manager import config_manager
from utils.data_storage import data_storage
import random
import time
class CustomerAcquisitionService(BaseService):
    """获客服务（修复版 + 整页丝滑滑动）"""

    def __init__(self):
        super().__init__()
        self.api_comments_cache = {}

    async def execute(
        self,
        keywords=None,
        ip_keywords=None,
        sort_type="视频",
        videos_per_keyword=5,
        duration_minutes=10,
        videos=None,
        user_comment_keywords=None,
        **kwargs,
    ):
        """
        Args:
            keywords: 阶段一-内容关键词列表
            user_comment_keywords: 阶段二-用户评论关键词列表
            ip_keywords: 阶段二-IP关键词列表
        """
        # 关键词规范化处理
        self.current_stage = kwargs.get("mode")
        user_comment_keys = self._normalize_keywords(user_comment_keywords)
        ip_keys = self._normalize_keywords(ip_keywords)
        kw_list = self._normalize_keywords(keywords)

        if not await self._ensure_browser_ready():
            await self._emit_event("error", "❌ 浏览器未就绪")
            return

        browser_manager = await self._get_browser_manager()
        storage = await self._get_data_storage()


        try:
            # 阶段二：基于视频列表采集评论用户
            if videos:
                if not user_comment_keys:
                    msg = "❌ 阶段二需要填写用户评论关键词"
                    await self._emit_event("error", msg)
                    raise ValueError(msg)

                await self._emit_event("operation", f"🚀 阶段二开始：共 {len(videos)} 个视频")
                await self._emit_event(
                    "operation",
                    f"💬 用户评论关键词: {' '.join(user_comment_keys)} | 📍 IP关键词: {'任意' if not ip_keys else ' '.join(ip_keys)}",
                )
                self.current_stage = "stage2"
                total_users = 0
                for i, url in enumerate(videos):
                    if await self._check_stop():
                        break
                    
                    self.api_comments_cache = {}
                    
                    await self._emit_event("operation", f"📹 处理视频 {i+1}/{len(videos)}: {url}")
                    try:
                        await browser_manager.page.goto(url, wait_until="domcontentloaded")
                        await self._quick_wait_for_page_load(browser_manager)
                        
                        video_desc = await self._read_video_desc_enhanced(browser_manager)

                        users = await self._collect_comments_smooth_scroll(
                            browser_manager=browser_manager,
                            storage=storage,
                            user_comment_keywords=user_comment_keys,
                            ip_keywords=ip_keys,
                            video_url=url,
                            video_desc=video_desc,
                        )
                        total_users += len(users)
                        await self._emit_event(
                            "operation", f"✅ 本视频采集 {len(users)} 个用户"
                        )
                    except Exception as e:
                        await self._emit_event("error", f"❌ 处理视频失败: {e}")
                        continue

                await self._emit_event("finished", f"🏁 阶段二完成：共采集 {total_users} 个用户")
                self.current_stage = None
                return

            # 阶段一：关键词搜索采集视频

            if not kw_list:
                msg = "❌ 请输入内容关键词"
                await self._emit_event("error", msg)
                raise ValueError(msg)
            self.current_stage = "stage1"
            await self._emit_event(
                "operation",
                f"🚀 阶段一开始：每关键词采集前 {videos_per_keyword} 条视频",
            )

            total_videos = 0
            for idx, kw in enumerate(kw_list):
                if await self._check_stop():
                    break

                await self._emit_event(
                    "operation", f"🔍 搜索关键词: {kw} ({idx+1}/{len(kw_list)})"
                )
                try:
                    if sort_type == "综合":
                        # 综合tab：进入综合页 + 监听接口 + 滚动触发加载
                        search_url = f"https://www.douyin.com/search/{quote(kw)}?type=general"
                        await self._emit_event("operation", f"🌐 导航到: {search_url}")
                        await browser_manager.page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
                        await self._quick_wait_for_page_load(browser_manager)

                        videos_collected = await self._collect_from_general_search(browser_manager, kw, int(videos_per_keyword))
                    else:
                        # 默认“视频”tab：保持你原有逻辑
                        search_url = f"https://www.douyin.com/search/{quote(kw)}?type=video"
                        await self._emit_event("operation", f"🌐 导航到: {search_url}")
                        await browser_manager.page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
                        await self._quick_wait_for_page_load(browser_manager)

                        videos_collected = await self._collect_videos_with_smooth_scroll(
                            browser_manager, kw, int(videos_per_keyword)
                        )
                    total_videos += len(videos_collected)
                    
                    if len(videos_collected) == 0:
                        await self._emit_event("warning", f"⚠️ 关键词 '{kw}' 未采集到任何视频")
                    else:
                        await self._emit_event(
                            "operation", f"✅ 关键词 '{kw}' 完成，采集 {len(videos_collected)} 条视频"
                        )

                except Exception as e:
                    await self._emit_event("error", f"❌ 搜索失败: {e}")
                    continue

            await self._emit_event("finished", f"🏁 阶段一完成：共采集 {total_videos} 条视频")

        except Exception as e:
            await self._emit_event("error", f"❌ 获客服务执行异常: {e}")
            raise
        self.current_stage = None
    async def enrich_videos_details(self, video_urls):
        """
        根据视频详情页补全:
        - 点赞数
        - 评论数
        - 收藏数
        - 作者昵称
        - 作者主页
        - 视频文案

        由 /api/videos/enrich_details 调用。
        """
        if not video_urls:
            return

        # 确认浏览器正常
        if not await self._ensure_browser_ready():
            await self._emit_event("error", "❌ 浏览器未就绪，无法获取视频详情")
            return

        browser_manager = await self._get_browser_manager()
        storage = await self._get_data_storage()

        total = len(video_urls)
        success = 0

        for idx, url in enumerate(video_urls):
            if await self._check_stop():
                break

            await self._emit_event("operation", f"🎯 获取视频详情 {idx+1}/{total}: {url}")
            try:
                await browser_manager.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await self._quick_wait_for_page_load(browser_manager)

                detail = await self._extract_video_detail_from_page(browser_manager)
                if not detail:
                    await self._emit_event("warning", "⚠️ 详情页未解析出任何数据")
                    continue

                # 补上 video_url 字段，方便 save_video / 更新
                detail["video_url"] = url

                # 这里直接复用你现有的 save_video 逻辑，让它按 video_url 做 upsert
                try:
                    storage.save_video(detail)
                except Exception:
                    # 为了兼容你之前在其他地方 from utils.data_storage import data_storage 的写法
                    from utils.data_storage import data_storage as _ds
                    _ds.save_video(detail)

                success += 1
                await self._emit_event(
                    "debug",
                    f"✅ 更新详情成功: 赞={detail.get('like_count', 0)}, 评={detail.get('comment_count', 0)}, 收藏={detail.get('collect_count', 0)}",
                )
            except Exception as e:
                await self._emit_event("error", f"❌ 获取视频详情失败: {e}")

        await self._emit_event("operation", f"🏁 视频详情补全结束，成功 {success}/{total} 个")

    def _normalize_keywords(self, keywords):
        """规范化关键词输入"""
        if not keywords:
            return []
        if isinstance(keywords, str):
            return [k.strip() for k in keywords.split() if k.strip()]
        elif isinstance(keywords, list):
            return [k.strip() for k in keywords if k.strip()]
        return []
    def _format_time_ago_from_epoch(self, ts: int) -> str:
        try:
            import time
            delta = max(0, int(time.time()) - int(ts))
            if delta < 60:
                return "刚刚"
            mins = delta // 60
            if mins < 60:
                return f"{mins}分钟前"
            hours = mins // 60
            if hours < 24:
                return f"{hours}小时前"
            days = hours // 24
            if days < 7:
                return f"{days}天前"
            weeks = days // 7
            if days < 30:
                return f"{weeks}周前"
            months = days // 30
            if days < 365:
                return f"{months}月前"
            years = days // 365
            return f"{years}年前"
        except Exception:
            return ""
    def _parse_count_text(self, text: str) -> int:
        """
        解析抖音计数文本，例如:
        753
        3.9万
        1.2亿
        """
        import re
        if not text:
            return 0

        text = text.strip().replace(" ", "")
        m = re.fullmatch(r"(\d+(?:\.\d+)?)([万亿]?)", text)
        if not m:
            # 非标准格式时，尽量取里面的数字
            digits = re.findall(r"\d+", text)
            return int(digits[0]) if digits else 0

        num = float(m.group(1))
        unit = m.group(2)
        if unit == "万":
            num *= 10000
        elif unit == "亿":
            num *= 100000000
        return int(num)

    def _split_comment_fields(self, raw_text, username_hint="", ip_hint=""):
        """
        修复版字段拆分 - 加强IP归属地提取
        """
        if not raw_text:
            return {"text": "", "ip_location": ip_hint,"comment_ago":""}
        
        text = raw_text.strip()
        ip_location = ip_hint
        time_ago = ""
        import re
        m = re.search(r'(刚刚)|(\d+\s*个?(分钟|小時|小时|天|周|月|年)前)', text)
        if m:
            time_ago = m.group(0)
        # 如果DOM中没有提取到IP，尝试从文本中提取IP归属地
        if not ip_location:
            ip_patterns = [
                r'(刚刚)|(\d+\s*(分钟|小時|小时|天|周|月|年)前)[，,·]\s*([\u4e00-\u9fa5·\s]{2,15})',
                r'(刚刚)|(\d+\s*(分钟|小時|小时|天|周|月|年)前)[·,，]\s*([\u4e00-\u9fa5·\s]{2,15})',
            ]

            for pattern in ip_patterns:
                match = re.search(pattern, text)
                if match:
                    groups = match.groups()
                    ip_candidate = None
                    # 在所有分组里挑出“不是时间、而是中文地名”的那个
                    for g in groups:
                        if not g:
                            continue
                        if re.search(r'(前|分钟|小時|小时|天|周|月|年)', g):
                            continue
                        if re.search(r'[\u4e00-\u9fa5]', g):
                            ip_candidate = g.strip()
                            break
                    if not ip_candidate and groups:
                        ip_candidate = groups[-1].strip()

                    if ip_candidate:
                        ip_location = ip_candidate
                        text = re.sub(pattern, '', text).strip()
                        break
        
        # 只清理固定噪声，避免删除正文数字
        noise_patterns = [
            r'回复\s*\d*',
            r'分享\s*\d*', 
            r'举报',
            r'展开\d*条回复',
            r'查看\d*条回复',
        ]
        for pattern in noise_patterns:
            text = re.sub(pattern, '', text).strip()
        
        # 移除时间信息（保留格式匹配）
        time_patterns = [
            r'\d{1,2}月\d{1,2}日',
            r'\d{4}-\d{1,2}-\d{1,2}',
            r'\d{1,2}:\d{2}',
            r'\d+\s*个?[分钟小時小时天周月年前]?',   # 保留“前/年前”变体
            r'\d+\s*个?[分钟小時小时天周月年]前',
        ]
        for pattern in time_patterns:
            text = re.sub(pattern, '', text).strip()

        # 如果DOM中提供了用户名提示，尝试从文本开头移除用户名
        if username_hint and text.startswith(username_hint):
            text = text[len(username_hint):].strip()
            text = re.sub(r'^[：:]\s*', '', text)
        
        # 清理多余的空白字符
        text = re.sub(r'\s+', ' ', text).strip()
        
        return {
            "text": text,
            "ip_location": ip_location,
            "comment_ago": time_ago,
        }

    async def _collect_comments_smooth_scroll(self, browser_manager, storage, user_comment_keywords, ip_keywords, video_url, video_desc):
        """整页丝滑滑动版评论采集 - 添加时间控制"""
        collected = []
        start_time = time.time()
        max_video_time = 600  # 每个视频最多5分钟
        try:
            # 启动接口监听
            await self._start_comment_api_listener(browser_manager)
            
            # 🎯 整页丝滑滑动采集评论
            await self._smooth_scroll_entire_page(browser_manager, max_scroll=500)
            # await self._click_all_reply_expand_buttons(browser_manager.page)
            # await self._smooth_scroll_entire_page(browser_manager, max_scroll=500)
            # 新增：滚动评论区域，触发更多评论加载
            #await self._scroll_comment_container(browser_manager.page)
            #await self._loop_scroll_comment_container(browser_manager.page, max_times=50)
            # 点击所有回复展开按钮
            #await self._click_all_reply_expand_buttons(browser_manager.page)
            # 滚动所有楼中楼容器
            #await self._scroll_all_reply_containers(browser_manager.page, max_times=30)

            # 检查是否超时
            # if time.time() - start_time > max_video_time:
            #     await self._emit_event("warning", f"⏰ 视频采集超时，跳过剩余处理")
            #     return collected
            # 使用增强版评论提取
            comments = await self._extract_comments_enhanced(browser_manager)
            
            # 数据合并 - 传入video_url
            merged_comments = await self._merge_comment_data(comments, video_url)
            
            # 关键词过滤
            filtered_comments = await self._filter_comments_by_keywords(
                merged_comments, user_comment_keywords, ip_keywords
            )
            
            # 保存用户数据
            for comment in filtered_comments:
                cleaned_data = self._split_comment_fields(
                    comment['content'], 
                    comment['username'], 
                    comment.get('ip_location', '')
                )
                
                user_data = {
                    "username": comment['username'],
                    "user_url": comment['user_url'],
                    "comment_text": cleaned_data['text'],
                    "ip_location": cleaned_data['ip_location'],
                    "video_url": video_url,
                    "video_desc": video_desc,
                    "matched_keyword": " ".join(user_comment_keywords),
                    "comment_time": cleaned_data.get("comment_ago", ""),
                    "comment_ts": data_storage._parse_time_ago_to_epoch(cleaned_data.get("comment_ago", "")),
                }
                comment_time = comment.get('comment_time') or cleaned_data.get('comment_ago', '')
                comment_ts = comment.get('comment_ts') or data_storage._parse_time_ago_to_epoch(comment_time)

                user_data.update({
                    "comment_time": comment_time,
                    "comment_ts": int(comment_ts or 0),
                })

                if storage.save_user(user_data):
                    collected.append(user_data)
                    await self._emit_event(
                        "debug", f"👤 采集用户: {comment['username']} | IP: {cleaned_data['ip_location']} | 评论: {cleaned_data['text'][:30]}..."
                    )
            
        except Exception as e:
            await self._emit_event("error", f"❌ 整页滑动评论采集失败: {e}")
            collected = await self._collect_comments_fallback(
                browser_manager, storage, user_comment_keywords, ip_keywords, video_url, video_desc
            )
        finally:
            await self._stop_comment_api_listener(browser_manager)
        
        return collected

    async def _smooth_scroll_entire_page(self, browser_manager, max_scroll=1000):  # 从500减少到100
        """在整个页面进行丝滑滑动 - 优化退出条件"""
        page = browser_manager.page
        scroll_attempts = 0
        no_new_comments_count = 0
        last_comment_count = 0
        
        try:
            # 获取初始评论数量
            last_comment_count = await self._get_comment_count(page)
            await self._emit_event("debug", f"📊 初始评论数: {last_comment_count}")
            
            while scroll_attempts < max_scroll and no_new_comments_count < 10:  # 从10减少到5
                if await self._check_stop():
                    break
                
                # 执行丝滑整页滚动
                scroll_success = await self._execute_smooth_scroll(page, scroll_attempts)
                
                if not scroll_success:
                    break
                
                # 等待新内容加载
                # 检查是否有新评论
                
                current_comment_count = await self._get_comment_count(page)
                
                if current_comment_count > last_comment_count:
                    new_comments = current_comment_count - last_comment_count
                    await self._emit_event("debug", f"🔄 滚动 {scroll_attempts+1}: +{new_comments} 条新评论")
                    last_comment_count = current_comment_count
                    no_new_comments_count = 0
                else:
                    no_new_comments_count += 1
                    await self._emit_event("debug", f"⏭️ 滚动 {scroll_attempts+1}: 无新评论（连续 {no_new_comments_count} 次）")
                
                scroll_attempts += 1
                
                # 每滚动5次随机休息一下
                if scroll_attempts % 5 == 0:
                    await self.pause(0.1, 0.5, 'wheel_break')
            
            await self._emit_event("debug", f"📜 整页滑动完成：共{scroll_attempts}次，评论={last_comment_count}")
            
        except Exception as e:
            await self._emit_event("error", f"❌ 整页滑动失败: {e}")
    async def _scroll_comment_container(self, page):
        """滚动评论容器，触发动态加载评论"""
        try:
            await page.evaluate("""
                () => {
                    const cList = document.querySelector('[class*="comment-list"]');
                    if (cList) {
                        cList.scrollTop = cList.scrollHeight;
                    }
                }
            """)
        except Exception:
            pass

    async def _loop_scroll_comment_container(self, page, max_times=30):
        """循环滚动评论容器，持续触发评论分页加载"""
        try:
            for _ in range(max_times):
                # 终止条件：外部强制停止
                if await self._check_stop():
                    break

                loaded_before = await self._get_comment_count(page)
                
                
                # 滚动评论容器到底部
                await page.evaluate("""
                    () => {
                        const cList = document.querySelector('[class*="comment-list"]');
                        if (cList) {
                            cList.scrollTop = cList.scrollHeight;
                        }
                    }
                """)

                # 等待加载响应
                await asyncio.sleep(0.4)
                
                loaded_after = await self._get_comment_count(page)

                # 如果评论数量没有增加，则停止循环
                if loaded_after <= loaded_before:
                    break

        except Exception:
            pass
    async def _click_all_reply_expand_buttons(self, page):
        """点击所有“查看回复”“展开更多回复”按钮"""
        try:
            buttons = await page.query_selector_all('button')
            if not buttons:
                return

            for btn in buttons:
                try:
                    txt = await btn.inner_text()
                    if txt and ("查看" in txt or "回复" in txt or "展开" in txt):
                        await btn.click()
                        #await asyncio.sleep(0.2)
                except:
                    continue
        except:
            pass
    async def _scroll_all_reply_containers(self, page, max_times=20):
        """滚动所有楼中楼容器，触发子评论分页"""
        try:
            for _ in range(max_times):
                containers = await page.query_selector_all('[class*="reply"]')
                if not containers:
                    break

                for c in containers:
                    try:
                        #await self._click_all_reply_expand_buttons(page)
                        await page.evaluate("""
                            el => { el.scrollTop = el.scrollHeight; }
                        """, c)
                    except:
                        continue

                await asyncio.sleep(0.2)
        except:
            pass

    async def _execute_smooth_scroll(self, page, attempt_number):
        """鼠标滚轮滚动方案"""
        try:
            # 滚轮距离配置 - 更激进的范围
            wheel_configs = [
                {"distance": 2200, "description": "中等滚轮"},
                {"distance": 2500, "description": "大幅滚轮"}, 
                {"distance": 2800, "description": "激进滚轮"},
                {"distance": 2000, "description": "保守滚轮"},  # 偶尔保守，增加随机性
            ]
            
            # 基于尝试次数选择策略
            if attempt_number % 5 == 0:
                # 每5次使用最激进的滚动
                config = wheel_configs[2]
            elif attempt_number % 3 == 0:
                # 每3次使用大幅滚动
                config = wheel_configs[1]
            elif random.random() < 0.2:
                # 20%概率使用保守滚动
                config = wheel_configs[3]
            else:
                # 默认中等滚动
                config = wheel_configs[0]
            
            distance = config['distance']
            
            # 添加随机微小偏移，模拟人类不精确性
            variance = random.randint(-100, 100)
            actual_distance = distance + variance
            
            # 执行鼠标滚轮滚动
            await page.evaluate(f"""
                () => {{
                    // 获取当前滚动位置
                    const currentScroll = window.pageYOffset || document.documentElement.scrollTop;
                    const targetScroll = currentScroll + {actual_distance};
                    
                    console.log('🔄 强制滚动: 从', currentScroll, '到', targetScroll);
                    
                    // 策略1: 直接设置滚动位置（最强制）
                    window.scrollTo(0, targetScroll);
                    
                    // 策略2: 设置文档元素的scrollTop
                    document.documentElement.scrollTop = targetScroll;
                    document.body.scrollTop = targetScroll;
                    
                    // 策略3: 使用scrollBy作为备份
                    window.scrollBy(0, {actual_distance});
                    
                    // 强制触发滚动事件（绕过事件阻止）
                    const scrollEvent = new Event('scroll', {{ bubbles: true, cancelable: false }});
                    window.dispatchEvent(scrollEvent);
                    document.dispatchEvent(scrollEvent);
                    
                    // 触发resize事件（有时能触发懒加载）
                    const resizeEvent = new Event('resize', {{ bubbles: true }});
                    window.dispatchEvent(resizeEvent);
                    
                    // 触发wheel事件（模拟滚轮）
                    const wheelEvent = new WheelEvent('wheel', {{ 
                        bubbles: true, 
                        cancelable: true,
                        deltaY: {actual_distance}
                    }});
                    document.body.dispatchEvent(wheelEvent);
                }}
            """)
            # 人类化等待 - 滚轮需要时间产生效果
            await asyncio.sleep(random.uniform(0.2, 0.4))
            
            # 偶尔添加微小回滚，模拟真实用户调整
            if random.random() < 0.15:
                await page.mouse.wheel(0, -random.randint(50, 150))
                await asyncio.sleep(0.3)
            
            return True
            
        except Exception as e:
            print(f"鼠标滚轮滚动失败: {e}")
            # 失败时降级到JS滚动
            try:
                await page.evaluate(f"window.scrollBy(0, 1000)")
                return True
            except:
                return False

    async def _get_comment_count(self, page):
        """获取当前评论数量"""
        try:
            # 多种选择器获取评论数量
            selectors = [
                'div[data-e2e="comment-item"]',
                '[class*="comment-item"]',
                '.comment-item'
            ]
            
            for selector in selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    if elements:
                        return len(elements)
                except:
                    continue
            return 0
        except:
            return 0
    async def _random_human_operation(self, page):
        """随机人类化操作"""
        operations = [
            lambda: page.evaluate("window.scrollBy({top: -100, left: 0, behavior: 'smooth'})"),  # 偶尔回滚
            lambda: page.mouse.move(random.randint(100, 500), random.randint(100, 300)),  # 随机移动鼠标
            lambda: asyncio.sleep(random.uniform(0.2, 0.4)),  # 随机暂停
        ]
        
        if random.random() < 0.3:  # 30%概率执行
            op = random.choice(operations)
            try:
                if asyncio.iscoroutinefunction(op):
                    await op()
                else:
                    op()
            except:
                pass
    async def _collect_from_general_search(self, browser_manager, keyword: str, target_count: int) -> list[dict]:
        """综合tab采集：优先监听接口，其次 DOM 兜底，再次 RENDER_DATA 兜底"""
        collected: list[dict] = []
        collected_ids: set[str] = set()
        stagnation = 0  # 连续无新增计数

        await self._start_general_search_listener(browser_manager, keyword, collected, collected_ids)
        try:
            await self._emit_event("operation", f"🎬 综合方式开始采集，目标: {target_count} 个")
            while len(collected) < target_count and not await self._check_stop():
                before = len(collected)

                # 丝滑滚动，触发接口返回
                await browser_manager.page.evaluate(
                    "window.scrollBy({top: Math.floor(window.innerHeight*2.5), left: 0, behavior: 'smooth'})"
                )
                await self.pause(0.5, 0.8, 'scroll_load')

                # 如果本轮没有新增，触发 DOM 兜底
                if len(collected) == before:
                    dom_new = await self._dom_fallback_collect_general(browser_manager, keyword, collected_ids, collected)
                    if dom_new:
                        await self._emit_event("debug", f"🧩 DOM兜底新增 {dom_new} 条")

                # 仍无新增，触发 RENDER_DATA 兜底
                if len(collected) == before:
                    render_new = await self._render_data_fallback_collect(browser_manager, keyword, collected_ids, collected)
                    if render_new:
                        await self._emit_event("debug", f"📦 RENDER_DATA兜底新增 {render_new} 条")

                # 无新增计数
                if len(collected) == before:
                    stagnation += 1
                else:
                    stagnation = 0

                # 若连续多轮无新增，则认为已经采空
                if stagnation >= 15:
                    await self._emit_event("debug", "📜 综合方式：多轮无新增，停止滚动")
                    break
        finally:
            await self._stop_general_search_listener(browser_manager)

        return collected[:target_count]
    async def _start_general_search_listener(self, browser_manager, keyword: str, collected: list, collected_ids: set):
        async def handle_search_resp(response):
            try:
                url = (response.url or "").lower()
                if ("/aweme/v1/web/general/search/single" in url or
                    "/aweme/v1/web/general/search/patch" in url):
                    if response.status == 200:
                        data = await response.json()
                        items = self._extract_awemes_from_general_search(data)
                        new_count = 0
                        for it in items:
                            aweme_id = it.get("aweme_id")
                            if not aweme_id or aweme_id in collected_ids:
                                continue
                            video = {
                                "video_url": f"https://www.douyin.com/video/{aweme_id}",
                                "video_desc": it.get("desc", "") or "",
                                "keyword": keyword,
                                "publish_ts": int(it.get("create_time") or 0),
                                "publish_time": self._format_time_ago_from_epoch(int(it.get("create_time") or 0)) if it.get("create_time") else "",
                                # 新增：直接用接口解析得到的作者/点赞
                                "author_name": it.get("author_name") or "",
                                "author_url": "",  # 接口里通常没有，留空，缺了再走 DOM 兜底
                                "like_count": int(it.get("like_count") or 0),
                            }
                            # —— 补齐作者与点赞（只在缺字段时执行 DOM 兜底）——
                            if not video.get("author_name") or not video.get("like_count"):
                                try:
                                    extra = await browser_manager.page.evaluate("""
                                        (vid) => {
                                            const res = { author_name: '', author_url: '', like_count: 0 };

                                            // 1) 先按 aweme_id 找到对应视频卡片的 <a href="/video/<vid>">
                                            const anchors = Array.from(document.querySelectorAll('a[href*="/video/"]'));
                                            let target = null;
                                            for (const a of anchors) {
                                                const href = a.getAttribute('href') || '';
                                                if (href.includes('/video/') && href.includes(vid)) { target = a; break; }
                                            }
                                            if (!target) return res;

                                            // 2) 取大容器作为搜索范围
                                            let card = target.closest('[class]') || target.parentElement;
                                            for (let i = 0; i < 5 && card && !card.querySelector; i++) {
                                                card = card.parentElement;
                                            }
                                            const root = card || document;

                                            // 3) 作者名、主页
                                            const authorSpan = root.querySelector('span[class*="WUZSchd"]');
                                            if (authorSpan && authorSpan.textContent) res.author_name = authorSpan.textContent.trim();
                                            const authorLink = root.querySelector('a[href*="/user/"]');
                                            if (authorLink) res.author_url = authorLink.href;

                                            // 4) 点赞数（综合卡片的数值在一个 span 里）
                                            const likeSpan = root.querySelector('div[class*="pMq5SQ1M"] span');
                                            if (likeSpan) {
                                                const t = (likeSpan.textContent || '').trim();
                                                const m = /([\\d\\.]+)\\s*[万wW]?/.exec(t);
                                                if (m) {
                                                    const n = parseFloat(m[1]);
                                                    res.like_count = /万|w|W/.test(t) ? Math.round(n * 10000) : Math.round(n);
                                                }
                                            }
                                            return res;
                                        }
                                    """, aweme_id) or {}
                                except Exception:
                                    extra = {}

                                # 仅在缺值时写回
                                if not video.get("author_name") and extra.get("author_name"):
                                    video["author_name"] = extra["author_name"]
                                if not video.get("author_url") and extra.get("author_url"):
                                    video["author_url"] = extra["author_url"]
                                if not video.get("like_count") and extra.get("like_count"):
                                    video["like_count"] = int(extra["like_count"]) or 0


                            if data_storage.save_video(video):
                                collected.append(video)
                                collected_ids.add(aweme_id)
                                new_count += 1
                        if new_count:
                            await self._emit_event("debug", f"📡 综合接口新增 {new_count} 条")
            except Exception as e:
                await self._emit_event("debug", f"❌ 解析综合搜索接口失败: {e}")

        self._general_search_handler = handle_search_resp
        # 你项目其他地方对监听是用 page.on，这里保持一致
        browser_manager.page.on("response", self._general_search_handler)

    async def _stop_general_search_listener(self, browser_manager):
        try:
            if hasattr(self, "_general_search_handler") and self._general_search_handler:
                # 你项目里已使用 remove_listener，这里用同样方式
                browser_manager.page.remove_listener("response", self._general_search_handler)
        except Exception:
            pass
        finally:
            self._general_search_handler = None
    def _extract_awemes_from_general_search(self, data: dict) -> list[dict]:
        """从综合搜索 JSON 提取 aweme_id/desc/create_time（多路径兜底）"""
        items: list[dict] = []
        try:
            candidates = []
            for path in (["data","data"], ["data","aweme_list"], ["data","mix_list"], ["list"], ["data"]):
                cur = data
                ok = True
                for k in path:
                    if isinstance(cur, dict) and k in cur:
                        cur = cur[k]
                    else:
                        ok = False; break
                if ok and isinstance(cur, list):
                    candidates.extend(cur)

            for c in candidates:
                aweme = (c.get("aweme") if isinstance(c, dict) else None) or \
                        (c.get("aweme_info") if isinstance(c, dict) else None) or \
                        (c.get("aweme_raw") if isinstance(c, dict) else None) or c

                if not isinstance(aweme, dict):
                    continue
                aweme_id = str(aweme.get("aweme_id") or aweme.get("awemeId") or "").strip()
                if not aweme_id:
                    continue

                desc = (aweme.get("desc") or aweme.get("description") or
                        (aweme.get("share_info") or {}).get("share_desc") or "")
                ct = aweme.get("create_time") or aweme.get("createTime") or 0

                # 取作者名（多路径兼容）
                author_obj = aweme.get("author") or aweme.get("author_info") or aweme.get("authorInfo") or {}
                author_name = (author_obj.get("nickname") or author_obj.get("nickName") or "") or ""

                # 取点赞数（多路径兼容）
                stats = aweme.get("statistics") or aweme.get("statisticsInfo") or {}
                digg = stats.get("digg_count") or stats.get("diggCount") or 0
                try:
                    like_count = int(digg)
                except Exception:
                    like_count = 0

                items.append({
                    "aweme_id": aweme_id,
                    "desc": desc or "",
                    "create_time": int(ct) if str(ct).isdigit() else 0,
                    "author_name": author_name,
                    "like_count": like_count
                })
        except Exception:
            pass
        return items
    async def _dom_fallback_collect_general(self, browser_manager, keyword: str, collected_ids: set, collected: list) -> int:
        """DOM 兜底：在综合页抓取可见卡片中的 video 链接或 aweme_id"""
        new_added = 0
        try:
            # 1) 直接抓 a[href*="/video/"]
            hrefs = await browser_manager.page.eval_on_selector_all(
                'a[href*="/video/"]',
                "els => els.map(e => e.getAttribute('href'))"
            ) or []

            # 2) 常见卡片容器上可能含有 data-* 里埋的 id，或 dataset.awemeId
            #    取所有包含 'aweme' / 'video' / 'id' 的 dataset/属性字符串，再用正则抠 19位数字
            data_texts = await browser_manager.page.evaluate("""
                () => {
                    const nodes = Array.from(document.querySelectorAll('.search-result-card,[data-e2e*="video"],[data-e2e*="card"]'));
                    const out = [];
                    for (const n of nodes) {
                        const ds = n.dataset || {};
                        const line = JSON.stringify(ds) + ' ' + (n.getAttribute('data-aweme-id')||'') + ' ' + (n.getAttribute('data-id')||'');
                        out.push(line);
                    }
                    return out;
                }
            """) or []

            import re
            id_from_attrs = []
            for s in data_texts:
                for m in re.finditer(r'(\d{16,21})', s):
                    id_from_attrs.append(m.group(1))

            # 3) 统一转成标准 URL
            urls = []
            from urllib.parse import urljoin
            for h in hrefs:
                if not h: 
                    continue
                urls.append(urljoin("https://www.douyin.com", h))
            for vid in id_from_attrs:
                urls.append(f"https://www.douyin.com/video/{vid}")

            # 4) 去重 & 入库
            for url in dict.fromkeys(urls).keys():
                # 从 URL 提取 aweme_id 做二次去重
                m = re.search(r'/video/(\d{16,21})', url)
                if not m:
                    continue
                aweme_id = m.group(1)
                if aweme_id in collected_ids:
                    continue
                video = {
                    "video_url": url,
                    "video_desc": "",
                    "keyword": keyword,
                    "publish_ts": 0,
                    "publish_time": ""
                }
                # —— 补齐作者与点赞（从综合页卡片 DOM 上就近解析）——
                try:
                    extra = await browser_manager.page.evaluate("""
                        (vid) => {
                            const res = { author_name: '', author_url: '', like_count: 0 };
                            const anchors = Array.from(document.querySelectorAll('a[href*="/video/"]'));
                            let target = null;
                            for (const a of anchors) {
                                const href = a.getAttribute('href') || '';
                                if (href.includes('/video/') && href.includes(vid)) { target = a; break; }
                            }
                            if (!target) return res;
                            let card = target.closest('[class]') || target.parentElement;

                            const authorEl = Array.from(card.querySelectorAll('span, a')).find(el => {
                                const t = (el.textContent || '').trim();
                                return t.startsWith('@') && t.length <= 40;
                            });
                            if (authorEl) {
                                res.author_name = (authorEl.textContent || '').trim().replace(/^@/, '');
                                const au = authorEl.closest('a');
                                if (au && au.href) res.author_url = au.href;
                            }

                            function parseLike(t) {
                                t = (t || '').trim();
                                if (!t || t.includes(':')) return null;
                                const m = t.match(/^(\\d+(?:\\.\\d+)?)([万亿]?)$/);
                                if (!m) return null;
                                let num = parseFloat(m[1]);
                                if (m[2] === '万') num *= 10000;
                                if (m[2] === '亿') num *= 100000000;
                                return Math.round(num);
                            }
                            let cands = Array.from(card.querySelectorAll('svg + span, div svg + span, div svg ~ span'));
                            cands = cands.concat(Array.from(card.querySelectorAll('span')));
                            for (const el of cands) {
                                const v = parseLike(el.textContent || '');
                                if (v) { res.like_count = v; break; }
                            }
                            return res;
                        }
                    """, aweme_id)
                    if isinstance(extra, dict):
                        video["author_name"] = extra.get("author_name") or ""
                        video["author_url"]  = extra.get("author_url")  or ""
                        video["like_count"]  = int(extra.get("like_count") or 0)
                except Exception:
                    pass

                if data_storage.save_video(video):
                    collected.append(video)
                    collected_ids.add(aweme_id)
                    new_added += 1
        except Exception as e:
            await self._emit_event("debug", f"❌ DOM兜底异常: {e}")
        return new_added
    async def _render_data_fallback_collect(self, browser_manager, keyword: str, collected_ids: set, collected: list) -> int:
        """RENDER_DATA 兜底：解析页面内嵌 JSON 中的 aweme_id"""
        new_added = 0
        try:
            raw = await browser_manager.page.evaluate("""
                () => {
                    const el = document.querySelector('#RENDER_DATA');
                    return el ? el.textContent || el.innerText || '' : '';
                }
            """) or ""
            if not raw:
                return 0

            # RENDER_DATA 通常是 URL 编码过的
            from urllib.parse import unquote
            txt = unquote(raw)

            # 抠 aweme_id（19~21位数字），并顺便取 desc / create_time（可选）
            import re, json
            ids = set(re.findall(r'"aweme_id"\s*:\s*"?(\\d{16,21})"?', txt))
            # 尝试把 JSON 解析出来以取更多字段（失败也无妨）
            desc_map = {}
            ct_map = {}
            try:
                data = json.loads(txt)
                def deep_walk(o):
                    if isinstance(o, dict):
                        # 兼容多命名
                        aid = str(o.get("aweme_id") or o.get("awemeId") or "") if ("aweme_id" in o or "awemeId" in o) else ""
                        if aid:
                            if "desc" in o: desc_map[aid] = o.get("desc") or ""
                            if "create_time" in o: ct_map[aid] = int(o.get("create_time") or 0)
                            if "createTime" in o: ct_map[aid] = int(o.get("createTime") or 0)
                        for v in o.values(): deep_walk(v)
                    elif isinstance(o, list):
                        for v in o: deep_walk(v)
                deep_walk(data)
            except Exception:
                pass

            for aid in ids:
                if aid in collected_ids:
                    continue
                video = {
                    "video_url": f"https://www.douyin.com/video/{aid}",
                    "video_desc": desc_map.get(aid, ""),
                    "keyword": keyword,
                    "publish_ts": ct_map.get(aid, 0),
                    "publish_time": self._format_time_ago_from_epoch(ct_map[aid]) if aid in ct_map else ""
                }
                # —— 补齐作者与点赞（从综合页卡片 DOM 上就近解析）——
                try:
                    extra = await browser_manager.page.evaluate("""
                        (vid) => {
                            const res = { author_name: '', author_url: '', like_count: 0 };
                            const anchors = Array.from(document.querySelectorAll('a[href*="/video/"]'));
                            let target = null;
                            for (const a of anchors) {
                                const href = a.getAttribute('href') || '';
                                if (href.includes('/video/') && href.includes(vid)) { target = a; break; }
                            }
                            if (!target) return res;
                            let card = target.closest('[class]') || target.parentElement;

                            const authorEl = Array.from(card.querySelectorAll('span, a')).find(el => {
                                const t = (el.textContent || '').trim();
                                return t.startsWith('@') && t.length <= 40;
                            });
                            if (authorEl) {
                                res.author_name = (authorEl.textContent || '').trim().replace(/^@/, '');
                                const au = authorEl.closest('a');
                                if (au && au.href) res.author_url = au.href;
                            }

                            function parseLike(t) {
                                t = (t || '').trim();
                                if (!t || t.includes(':')) return null;
                                const m = t.match(/^(\\d+(?:\\.\\d+)?)([万亿]?)$/);
                                if (!m) return null;
                                let num = parseFloat(m[1]);
                                if (m[2] === '万') num *= 10000;
                                if (m[2] === '亿') num *= 100000000;
                                return Math.round(num);
                            }
                            let cands = Array.from(card.querySelectorAll('svg + span, div svg + span, div svg ~ span'));
                            cands = cands.concat(Array.from(card.querySelectorAll('span')));
                            for (const el of cands) {
                                const v = parseLike(el.textContent || '');
                                if (v) { res.like_count = v; break; }
                            }
                            return res;
                        }
                    """, aid)
                    if isinstance(extra, dict):
                        video["author_name"] = extra.get("author_name") or ""
                        video["author_url"]  = extra.get("author_url")  or ""
                        video["like_count"]  = int(extra.get("like_count") or 0)
                except Exception:
                    pass

                if data_storage.save_video(video):
                    collected.append(video)
                    collected_ids.add(aid)
                    new_added += 1
        except Exception as e:
            await self._emit_event("debug", f"❌ RENDER_DATA兜底异常: {e}")
        return new_added

    async def _collect_videos_with_smooth_scroll(self, browser_manager, keyword, max_videos):
        """整页丝滑滑动版视频采集 - 集成智能底部检测"""
        videos = []
        collected_urls = set()
        scroll_attempts = 0
        max_scroll_attempts = 30
        no_new_count = 0  # 连续无新视频计数
        
        await self._emit_event("operation", f"🎬 开始采集视频，目标: {max_videos} 个")

        while len(videos) < max_videos and scroll_attempts < max_scroll_attempts and no_new_count < 5:
            # 采集当前可见区域的视频
            current_videos = await self._collect_visible_videos(browser_manager, keyword)
            
            new_videos = []
            for video in current_videos:
                if video['video_url'] not in collected_urls:
                    collected_urls.add(video['video_url'])
                    new_videos.append(video)
                    # 立即保存视频到数据库
                    try:
                        from utils.data_storage import data_storage
                        success = data_storage.save_video(video)
                        if success:
                            await self._emit_event("debug", f"✅ 保存视频: {video['video_desc'][:50]}...")
                        else:
                            await self._emit_event("error", f"❌ 保存视频失败: {video['video_url']}")
                    except Exception as e:
                        await self._emit_event("error", f"❌ 保存视频异常: {e}")
            
            if new_videos:
                videos.extend(new_videos)
                no_new_count = 0  # 重置无新视频计数
                await self._emit_event("debug", f"📹 本轮采集到 {len(new_videos)} 个新视频，总计: {len(videos)}")
            else:
                no_new_count += 1
                await self._emit_event("debug", f"⚠️ 连续 {no_new_count} 次未发现新视频")
            
            # 只有当达到目标数量时才停止
            if len(videos) >= max_videos:
                break
            
            # 🎯 使用智能底部检测
            can_scroll = await self._check_can_scroll_videos(browser_manager)
            if not can_scroll:
                await self._emit_event("debug", "📜 已滑动到底部，停止滚动")
                break
            
            # 执行丝滑整页滚动
            try:
                await browser_manager.page.evaluate("""
                    () => {
                        const scrollAmount = Math.floor(window.innerHeight * 2.5);
                        window.scrollBy({
                            top: scrollAmount,
                            left: 0,
                            behavior: 'smooth'
                        });
                    }
                """)
                
                # 智能等待加载（使用统一的pause方法）
                await self.pause(0.3, 0.5, 'scroll_load')
                
            except Exception as e:
                await self._emit_event("debug", f"⚠️ 滚动操作失败: {e}")
                no_new_count += 1
            
            scroll_attempts += 1
            
            if await self._check_stop():
                break

        await self._emit_event("operation", f"✅ 视频采集完成: {len(videos)}/{max_videos}")
        return videos[:max_videos]
    async def _check_can_scroll_videos(self, browser_manager):
        """视频采集专用底部检测 - 增加等待机制"""
        try:
            result = await browser_manager.page.evaluate("""
                () => {
                    const windowHeight = window.innerHeight;
                    const docHeight = Math.max(
                        document.body.scrollHeight,
                        document.documentElement.scrollHeight
                    );
                    const currentScroll = window.pageYOffset || document.documentElement.scrollTop;
                    const buffer = 100;  // 底部缓冲区
                    
                    return {
                        canScroll: currentScroll + windowHeight < docHeight - buffer,
                        currentScroll: currentScroll,
                        totalHeight: docHeight,
                        windowHeight: windowHeight
                    };
                }
            """)
            
            can_scroll = result.get('canScroll', False)
            
            # 如果检测到底部，先等待一段时间看看是否有新内容加载
            if not can_scroll:
                await self._emit_event("debug", "📜 检测到可能到底部，等待8秒确认是否有新视频...")
                
                # 记录当前的页面高度和视频数量
                initial_height = result.get('totalHeight', 0)
                initial_video_count = await self._get_video_count(browser_manager.page)
                
                # 等待8秒，期间每2秒检查一次是否有新内容
                waited_time = 0
                while waited_time < 8 and not await self._check_stop():
                    await self.pause(1, 1, 'bottom_wait')  # 等待2秒
                    waited_time += 1
                    
                    # 检查页面高度是否有变化
                    current_result = await browser_manager.page.evaluate("""
                        () => {
                            return Math.max(
                                document.body.scrollHeight,
                                document.documentElement.scrollHeight
                            );
                        }
                    """)
                    
                    # 检查视频数量是否有变化
                    current_video_count = await self._get_video_count(browser_manager.page)
                    
                    # 如果页面高度或视频数量有变化，说明有新内容加载
                    if current_result > initial_height or current_video_count > initial_video_count:
                        await self._emit_event("debug", f"🔄 等待期间发现新视频！页面高度: {initial_height} → {current_result}, 视频: {initial_video_count} → {current_video_count}")
                        return True  # 可以继续滚动
                
                # 等待8秒后仍无新内容，确认到底部
                await self._emit_event("debug", "📜 等待8秒后仍无新视频，确认已滑动到底部")
                return False
            
            return True
            
        except Exception as e:
            await self._emit_event("debug", f"⚠️ 检查滚动状态失败: {e}")
            return True  # 出错时默认可以滚动，避免误停

    async def _get_video_count(self, page):
        """获取当前视频数量"""
        try:
            # 多种选择器获取视频数量
            selectors = [
                'li .search-result-card',
                '.search-result-card',
                '[data-e2e*="video-item"]',
                '.video-card',
                'a[href*="/video/"]'
            ]
            
            for selector in selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    if elements:
                        return len(elements)
                except:
                    continue
            return 0
        except:
            return 0
    async def _check_can_scroll(self, browser_manager):
        """检查是否可以继续滚动 - 增加等待机制"""
        try:
            result = await browser_manager.page.evaluate("""
                () => {
                    const windowHeight = window.innerHeight;
                    const docHeight = Math.max(
                        document.body.scrollHeight,
                        document.documentElement.scrollHeight
                    );
                    const currentScroll = window.pageYOffset || document.documentElement.scrollTop;
                    const buffer = 100;  // 底部缓冲区
                    
                    return {
                        canScroll: currentScroll + windowHeight < docHeight - buffer,
                        currentScroll: currentScroll,
                        totalHeight: docHeight,
                        windowHeight: windowHeight
                    };
                }
            """)
            
            can_scroll = result.get('canScroll', False)
            
            # 如果检测到底部，先等待一段时间看看是否有新内容加载
            if not can_scroll:
                await self._emit_event("debug", "📜 检测到可能到底部，等待6秒确认...")
                
                # 记录当前的页面高度和评论数量
                initial_height = result.get('totalHeight', 0)
                initial_comment_count = await self._get_comment_count(browser_manager.page)
                
                # 等待8秒，期间每2秒检查一次是否有新内容
                waited_time = 0
                while waited_time < 6 and not await self._check_stop():
                    await self.pause(1, 1, 'bottom_wait')  # 等待200秒
                    waited_time += 1
                    
                    # 检查页面高度是否有变化
                    current_result = await browser_manager.page.evaluate("""
                        () => {
                            return Math.max(
                                document.body.scrollHeight,
                                document.documentElement.scrollHeight
                            );
                        }
                    """)
                    
                    # 检查评论数量是否有变化
                    current_comment_count = await self._get_comment_count(browser_manager.page)
                    
                    # 如果页面高度或评论数量有变化，说明有新内容加载
                    if current_result > initial_height or current_comment_count > initial_comment_count:
                        await self._emit_event("debug", f"🔄 等待期间发现新内容！页面高度: {initial_height} → {current_result}, 评论: {initial_comment_count} → {current_comment_count}")
                        return True  # 可以继续滚动
                
                # 等待8秒后仍无新内容，确认到底部
                await self._emit_event("debug", "📜 等待8秒后仍无新内容，确认已滑动到底部")
                return False
            
            return True
            
        except Exception as e:
            await self._emit_event("debug", f"⚠️ 检查滚动状态失败: {e}")
            return True  # 出错时默认可以滚动，避免误停

    async def _start_comment_api_listener(self, browser_manager):
        """启动评论接口监听（修复await问题）"""
        async def handle_comment_response(response):
            try:
                url = response.url
                # 同时允许fetch和xhr类型的请求
                if "comment" in url.lower() and response.request.resource_type in ["xhr", "fetch"]:
                    if response.status == 200:
                        data = await response.json()
                        # 修复：添加await
                        comments = await self._extract_comments_from_api(data)
                        if comments:
                            video_url = browser_manager.page.url
                            if video_url not in self.api_comments_cache:
                                self.api_comments_cache[video_url] = []
                            self.api_comments_cache[video_url].extend(comments)
                            await self._emit_event("debug", f"📡 从接口获取 {len(comments)} 条评论")
            except Exception as e:
                await self._emit_event("debug", f"❌ 解析评论接口失败: {e}")
        
        self._comment_response_handler = handle_comment_response
        browser_manager.page.on("response", self._comment_response_handler)

    async def _extract_comments_from_api(self, api_data):
        """从API响应数据中提取评论（修复局部变量引用与清洗顺序）"""
        comments = []
        try:
            # 兼容不同返回结构
            comment_paths = [
                ['comments'],
                ['data', 'comments'],
                ['data'],
                ['list'],
            ]

            comment_list = None
            for path in comment_paths:
                temp = api_data
                for key in path:
                    if isinstance(temp, dict) and key in temp:
                        temp = temp[key]
                    else:
                        temp = None
                        break
                if isinstance(temp, list) and temp:
                    comment_list = temp
                    break

            if not comment_list:
                return comments

            for comment in comment_list:
                try:
                    # 内容
                    content = self._get_nested_value(comment, ['text', 'content', 'comment'])
                    if not content:
                        continue

                    # 用户信息
                    user_info = self._get_nested_value(comment, ['user', 'author']) or {}
                    username = self._get_nested_value(user_info, ['nickname', 'name']) or ''
                    user_id = self._get_nested_value(user_info, ['uid', 'id']) or ''
                    user_url = f"https://www.douyin.com/user/{user_id}" if user_id else ""

                    # IP 属地（接口里就有时优先用接口）
                    ip_location = self._get_nested_value(comment, ['ip_label', 'location', 'ip_location']) or ''

                    # 发表时间戳（有就用，没有再回退到“xx分钟前”等）
                    ct_ts = (
                        self._get_nested_value(comment, ['create_time', 'createTime', 'ctime', 'createTs']) or 0
                    )
                    if isinstance(ct_ts, str) and ct_ts.isdigit():
                        ct_ts = int(ct_ts)  # 纯数字字符串 -> int
                    elif not isinstance(ct_ts, int):
                        ct_ts = 0

                    # 先清洗出干净文本与可能的“xx分钟前”
                    cleaned = self._split_comment_fields(content, username, ip_location)
                    # 优先用时间戳→人类可读；否则用清洗出来的“xx分钟前”
                    ct_str = self._format_time_ago_from_epoch(ct_ts) if ct_ts else cleaned.get('comment_ago', '')

                    comments.append({
                        'username': username,
                        'user_url': user_url,
                        'content': cleaned['text'],
                        'ip_location': cleaned['ip_location'],
                        'source': 'api',
                        'comment_time': ct_str,
                        'comment_ts': int(ct_ts) if ct_ts else data_storage._parse_time_ago_to_epoch(ct_str),
                        'cleaned': True,
                    })
                except Exception:
                    # 单条异常跳过，继续解析后续
                    continue

        except Exception as e:
            await self._emit_event("debug", f"❌ 提取API评论数据失败: {e}")

        return comments


    async def _filter_comments_by_keywords(self, comments, user_comment_keywords, ip_keywords):
        """根据关键词过滤评论 - 优化版"""
        filtered = []
        
        # 优化：预转换IP关键词为小写
        ip_keywords_lower = [ipk.lower().strip() for ipk in ip_keywords] if ip_keywords else []
        
        for comment in comments:
            # 检查是否已经清洗过
            if not comment.get('cleaned'):
                cleaned_data = self._split_comment_fields(
                    comment['content'], 
                    comment['username'], 
                    comment.get('ip_location', '')
                )
            else:
                cleaned_data = {
                    'text': comment['content'],
                    'ip_location': comment.get('ip_location', '')
                }
            
            # 使用清洗后的纯评论文本进行关键词匹配
            comment_lower = cleaned_data['text'].lower()
            keyword_in_comment = any(kw.lower() in comment_lower for kw in user_comment_keywords)
            
            if not keyword_in_comment:
            # 子评论自动保留（方案 B）
                # if comment.get('source') == 'dom_enhanced' and '回复' in comment.get('content', ''):
                #     filtered.append(comment)
                #     continue

                continue
            
            # IP匹配
            if ip_keywords_lower:
                ip_location = cleaned_data['ip_location']
                ip_location_lower = ip_location.lower() if ip_location else ""
                if not any(ipk in ip_location_lower for ipk in ip_keywords_lower):
                    continue
            
            # 更新评论数据
            comment['content'] = cleaned_data['text']
            comment['ip_location'] = cleaned_data['ip_location']
            comment['cleaned'] = True
            
            filtered.append(comment)
        
        await self._emit_event("debug", f"🔍 关键词过滤: {len(comments)} -> {len(filtered)}")
        return filtered

    async def _extract_comments_enhanced(self, browser_manager):
        """增强版评论提取"""
        comments = []
        try:
            comment_selectors = [
                'div[data-e2e="comment-item"]',
                '[class*="comment-item"]',
                '.comment-item'
            ]
            
            for selector in comment_selectors:
                try:
                    comment_elements = await browser_manager.page.query_selector_all(selector)
                    if comment_elements:
                        await self._emit_event("debug", f"✅ 找到 {len(comment_elements)} 个评论项")
                        
                        for element in comment_elements:
                            #await self.pause(0.05, 0.15, 'scroll_comment')  # 使用统一的pause方法
                            
                            comment_data = await self._extract_comment_enhanced(element)
                            if comment_data:
                                comments.append(comment_data)
                        break
                except:
                    continue
                    
        except Exception as e:
            await self._emit_event("error", f"❌ 增强版评论提取失败: {e}")
            
        return comments

    async def _extract_comment_enhanced(self, comment_element):
        """修复版评论数据提取"""
        try:
            username = ""
            user_url = ""
            
            user_link_selectors = [
                'a[href^="//www.douyin.com/user/"]',
                'a[href*="/user/"]',
                '[data-e2e="comment-nickname"]',
                '.nickname',
                '.username'
            ]
            
            for selector in user_link_selectors:
                try:
                    user_link = await comment_element.query_selector(selector)
                    if user_link:
                        username = await user_link.inner_text()
                        # ⭐ 增强版用户名清洗，允许非常规符号、emoji、日韩、特殊字等
                        try:
                            if username:
                                # 去掉不可见字符
                                username = re.sub(r'[\u200b\u200c\u200d]', '', username)
                                
                                # 去掉前后无意义符号，但保留中间的
                                username = username.strip().strip('|').strip('·').strip('•').strip('-').strip('_').strip('/').strip('\\')

                                # 允许保留绝大多数 Unicode 字符（emoji、日韩、特殊字）
                                # 只剔除明显的 HTML 噪声
                                #username = username.encode('utf-8', 'ignore').decode('utf-8')
                        except:
                            pass

                        user_url = await user_link.get_attribute('href')
                        if user_url:
                            if user_url.startswith("//"):
                                user_url = "https:" + user_url
                            elif user_url.startswith("/"):
                                user_url = urljoin("https://www.douyin.com", user_url)
                        if username and username.strip():
                            break
                except:
                    continue
            
            if not username.strip():
                try:
                    full_text = await comment_element.inner_text()
                    lines = full_text.split('\n')
                    if lines:
                        raw_name = lines[0].strip()

                        # 允许 Emoji + 中文 + 字母 + 数字 + 特殊符号
                        raw_name = re.sub(r'[\u200b\u200c\u200d]', '', raw_name)
                        raw_name = raw_name.encode('utf-8','ignore').decode('utf-8')
                        raw_name = raw_name.strip('|').strip('·').strip('•').strip('-').strip('_').strip('/')
                        username = raw_name.strip() or "未知用户"
                except:
                    username = "未知用户"

            import re
            time_text = ""
            try:
                raw = await comment_element.inner_text()
                if raw:
                    m = re.search(r'(刚刚)|(\d{1,2}\s?(分钟前|小时前|天前|周前|月前|年前))', raw)
                    if m:
                        time_text = m.group(0)
            except Exception:
                pass
            raw_comment_text = await self._extract_flattened_text(comment_element)
            if not raw_comment_text:
                return None

            ip_location = await self._extract_ip_location(comment_element)

            return {
                'username': username.strip() if username else "未知用户",
                'user_url': user_url,
                'content': raw_comment_text.strip(),
                'ip_location': ip_location,
                "comment_time": time_text,
                'source': 'dom_enhanced'
            }
            
        except Exception as e:
            await self._emit_event("debug", f"❌ 增强版评论提取失败: {e}")
            return None

    async def _extract_flattened_text(self, element):
        """扁平化文本提取（增强版：支持背景图表情）"""
        try:
            text = await element.evaluate("""
                (element) => {
                    // 1. 普通文本节点
                    const walker = document.createTreeWalker(
                        element,
                        NodeFilter.SHOW_TEXT,
                        null,
                        false
                    );

                    let fullText = '';
                    let node;
                    while ((node = walker.nextNode())) {
                        fullText += node.textContent;
                    }

                    // 2. <img> 表情：
                    //    - 有 alt：直接用 alt（一般是正常 emoji 或 [暖羊羊] 这种）
                    //    - 没有 alt：当成一个 [表情]
                    const imgs = element.querySelectorAll('img');
                    imgs.forEach(img => {
                        const alt = (img.getAttribute('alt') || '').trim();
                        if (alt) {
                            fullText += alt;
                        } else {
                            fullText += '[表情]';
                        }
                    });

                    // 3. Douyin 自定义表情：
                    //    用 sprite 背景图 + 小尺寸 div/span/i 画出来的。
                    //    我们检测：有 backgroundImage 且尺寸很小，就当成一个 [表情]。
                    const emojiNodes = element.querySelectorAll('span,div,i');
                    emojiNodes.forEach(node => {
                        const style = window.getComputedStyle(node);
                        const hasBg = style.backgroundImage && style.backgroundImage !== 'none';
                        if (!hasBg) return;

                        const rect = node.getBoundingClientRect();
                        const isSmall =
                            rect.width > 0 && rect.height > 0 &&
                            rect.width <= 32 && rect.height <= 32;  // 小图标

                        const cls = node.className || '';
                        const isEmojiClass = /emoji|表情/i.test(cls);

                        if (isSmall || isEmojiClass) {
                            fullText += '[表情]';
                        }
                    });

                    // 4. 收尾清理多余空白
                    return fullText.replace(/\\s+/g, ' ').trim();
                }
            """)
            return text or ""
        except Exception as e:
            await self._emit_event("debug", f"❌ 扁平化文本提取失败: {e}")
            try:
                return (await element.inner_text()) or ""
            except:
                return ""


    async def _extract_ip_location(self, comment_element):
        """提取IP属地（增强版）"""
        try:
            ip_selectors = [
                'span:has(img[src*="loc"])',
                '[class*="ip-label"]',
                '[class*="location"]',
                'span:has(svg)',
            ]
            
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

    async def _merge_comment_data(self, dom_comments, video_url):
        """修复版数据合并 - 使用传入的video_url作为缓存键"""
        merged = []
        
        # 使用传入的video_url作为缓存键
        api_comments = self.api_comments_cache.get(video_url, [])
        
        for api_comment in api_comments:
            matched_dom = None
            for dom_comment in dom_comments:
                if (dom_comment['username'] == api_comment['username'] or
                    self._is_similar_content(dom_comment['content'], api_comment['content'])):
                    matched_dom = dom_comment
                    break
            
            merged_comment = {
                'username': api_comment['username'] or (matched_dom['username'] if matched_dom else ''),
                'user_url': matched_dom['user_url'] if matched_dom else api_comment['user_url'],
                'content': api_comment['content'],
                'ip_location': api_comment['ip_location'] or (matched_dom['ip_location'] if matched_dom else '')
            }
            
            if merged_comment['username'] and merged_comment['content']:
                merged.append(merged_comment)
        
        # 补充DOM中独有的评论
        for dom_comment in dom_comments:
            already_merged = any(
                self._is_similar_content(dom_comment['content'], merged_comment['content'])
                for merged_comment in merged
            )
            
            if not already_merged and dom_comment['username'] and dom_comment['content']:
                merged.append(dom_comment)
        
        await self._emit_event("debug", f"🔄 数据合并完成: API={len(api_comments)}, DOM={len(dom_comments)}, 合并={len(merged)}")
        return merged

    def _is_similar_content(self, content1, content2):
        """判断两个评论内容是否相似"""
        if not content1 or not content2:
            return False
        
        len_diff = abs(len(content1) - len(content2)) / max(len(content1), len(content2))
        words1 = set(content1.lower().split())
        words2 = set(content2.lower().split())
        common_words = words1.intersection(words2)
        
        return len_diff < 0.5 and len(common_words) >= 1

    async def _collect_comments_fallback(self, browser_manager, storage, user_comment_keywords, ip_keywords, video_url, video_desc):
        """降级方案：整页滑动采集评论 - 集成智能底部检测"""
        collected = []
        processed = set()
        no_new_count = 0

        ip_keywords_lower = [ipk.lower().strip() for ipk in ip_keywords] if ip_keywords else []

        try:
            # 等待评论区域加载
            await browser_manager.page.wait_for_selector('div[data-e2e="comment-item"]', timeout=5000)
        except:
            await self._emit_event("warning", "❌ 评论区域加载超时")
            return collected

        scroll_attempts = 0
        max_scroll_attempts = 30

        while no_new_count < 3 and scroll_attempts < max_scroll_attempts and not await self._check_stop():
            try:
                # 🎯 检查是否可以继续滚动（带等待机制）
                can_scroll = await self._check_can_scroll(browser_manager)
                if not can_scroll:
                    await self._emit_event("debug", "📜 已滑动到底部，停止滚动")
                    break
                
                # 采集当前可见评论
                items = await browser_manager.page.query_selector_all('div[data-e2e="comment-item"]')
                new_found = 0

                for item in items:
                    try:
                        comment_data = await self._extract_comment_enhanced(item)
                        if not comment_data:
                            continue

                        cid = f"{comment_data['username']}_{comment_data['content']}"
                        if cid in processed:
                            continue
                        processed.add(cid)
                        new_found += 1

                        cleaned_data = self._split_comment_fields(
                            comment_data['content'], 
                            comment_data['username'], 
                            comment_data.get('ip_location', '')
                        )

                        comment_lower = cleaned_data['text'].lower()
                        if not any(kw.lower() in comment_lower for kw in user_comment_keywords):
                            continue

                        if ip_keywords_lower:
                            ip_location = cleaned_data['ip_location']
                            ip_location_lower = ip_location.lower() if ip_location else ""
                            if not any(ipk in ip_location_lower for ipk in ip_keywords_lower):
                                continue

                        user_data = {
                            "username": comment_data['username'],
                            "user_url": comment_data['user_url'],
                            "comment_text": cleaned_data['text'],
                            "ip_location": cleaned_data['ip_location'],

                            "video_url": video_url,
                            "video_desc": video_desc,
                            "matched_keyword": " ".join(user_comment_keywords),
                            "comment_time":cleaned_data.get('comment_ago',''),
                            "comment_ts": data_storage._parse_time_ago_to_epoch(cleaned_data.get('comment_ago','')),
                        }
                        storage.save_user(user_data)
                        collected.append(user_data)
                        await self._emit_event(
                            "debug", f"👤 降级采集用户: {comment_data['username']} | 评论: {cleaned_data['text'][:30]}..."
                        )

                    except Exception as e:
                        continue

                # 使用整页丝滑滑动
                await self._execute_smooth_scroll(browser_manager.page, scroll_attempts)
                    
                await self.pause(0.5, 1, 'scroll_wait')

                no_new_count = 0 if new_found > 0 else no_new_count + 1
                scroll_attempts += 1

            except Exception as e:
                await self._emit_event("warning", f"⚠️ 降级采集失败: {e}")
                no_new_count += 1
                await self.pause(0.5, 1, 'error_wait')

        return collected

    async def _collect_visible_videos(self, browser_manager, keyword):
        """采集当前可见区域的视频"""
        videos = []
        try:
            card_selectors = [
                'li .search-result-card',
                '.search-result-card',
                '[data-e2e*="video-item"]',
                '.video-card',
                'a[href*="/video/"]'
            ]
            
            for selector in card_selectors:
                try:
                    elements = await browser_manager.page.query_selector_all(selector)
                    if elements:
                        for element in elements:
                            try:
                                video_data = await self._extract_video_from_element(element, keyword)
                                if video_data and video_data.get('video_url'):
                                    videos.append(video_data)
                            except Exception as e:
                                continue
                                
                        if videos:
                            break
                except:
                    continue
                    
        except Exception as e:
            await self._emit_event("debug", f"⚠️ 采集可见视频失败: {e}")
            
        return videos

    async def _extract_video_from_element(self, element, keyword):
        """从视频元素中提取数据（补充作者主页 + 作者昵称 + 点赞数）"""
        try:
            # 视频链接
            link_el = await element.query_selector('a[href*="/video/"]')
            if not link_el:
                return None

            href = await link_el.get_attribute('href')
            if not href:
                return None

            video_url = urljoin("https://www.douyin.com", href)

            # 作者昵称 + 作者主页
            # 作者昵称 + 作者主页（标题智能推断：取“时间节点”前的最近名字）
            # 作者昵称 & 作者主页
            author_name = ""
            author_url = ""
            try:
                # 先尝试从 DOM 里拿真实作者节点（如果有）
                author_link_selectors = [
                    'a[href*="/user/"]',
                    '[data-e2e*="search-user-name"]',
                    '[data-e2e*="video-author-name"]',
                ]
                for sel in author_link_selectors:
                    try:
                        a_el = await element.query_selector(sel)
                        if not a_el:
                            continue
                        txt = await a_el.inner_text()
                        if txt:
                            author_name = txt.strip()
                        href_user = await a_el.get_attribute("href")
                        if href_user:
                            author_url = urljoin("https://www.douyin.com", href_user)
                        break
                    except:
                        continue
            except:
                pass

            # 如果 DOM 里拿不到作者，则按“时间节点前最近的人名”规则从文本里推断
            if not author_name:
                try:
                    full_text = await element.inner_text() or ""
                    full_text = full_text.strip()

                    # 提取发布时间文本，例如 “3月前”
                    publish_time = await self._extract_video_publish_time(element)
                    name_text = ""

                    if publish_time and publish_time in full_text:
                        idx = full_text.rfind(publish_time)
                        # 在时间节点前面截取一段窗口文本
                        window_text = full_text[max(0, idx - 40): idx]

                        # ① 优先找 @名字
                        at_matches = [m.group(1) for m in re.finditer(
                            r'@((?:[\w\u4e00-\u9fa5]|[^\s\w]){1,20})',
                            window_text
                        )]
                        if at_matches:
                            name_text = at_matches[-1]

                        # ② 没有 @，再找连续的中文/字母数字名
                        if not name_text:
                            cn_matches = [m.group(1) for m in re.finditer(
                                r'([\u4e00-\u9fa5A-Za-z0-9·]{2,12})',
                                window_text
                            )]
                            if cn_matches:
                                name_text = cn_matches[-1]

                    if name_text:
                        author_name = name_text.strip()

                    if not author_name:
                        author_name = "未知作者"

                    # 这里没有真实主页链接，只能留空，让前端只显示名字不跳转
                    if not author_url:
                        author_url = ""
                except:
                    if not author_name:
                        author_name = "未知作者"
                    if not author_url:
                        author_url = ""



            # 点赞数（解析数字 / 万 / 亿）
            like_count = 0
            try:
                span_texts = await element.eval_on_selector_all(
                    "span",
                    "els => els.map(e => e.textContent.trim())"
                )
                import re as _re
                like_text = ""
                for t in span_texts:
                    if _re.fullmatch(r"[0-9]+(?:\.[0-9]+)?[万亿]?", t):
                        like_text = t
                        break

                if like_text:
                    if like_text.endswith("万"):
                        like_count = int(float(like_text[:-1]) * 10000)
                    elif like_text.endswith("亿"):
                        like_count = int(float(like_text[:-1]) * 100000000)
                    else:
                        like_count = int(like_text)
            except:
                like_count = 0

            # 标题与发布时间
            title = await self._extract_video_title(element)
            publish_time = await self._extract_video_publish_time(element)
            publish_ts = data_storage._parse_time_ago_to_epoch(publish_time) if publish_time else 0

            return {
                "video_url": video_url,
                "video_desc": title or "无标题",
                "keyword": keyword,
                "publish_time": publish_time,
                "publish_ts": publish_ts,
                "author_name": author_name,
                "author_url": author_url,
                "like_count": like_count,
            }
        except Exception:
            return None

    async def _extract_video_detail_from_page(self, browser_manager):
        """
        进入视频详情页后提取：
        - like_count / comment_count / collect_count
        - author_name / author_url
        - video_desc
        先 JSON，后 DOM 兜底；不依赖随机类名
        """
        page = browser_manager.page

        # -------- 1) 详情页 URL 规范化并跳转 --------
        try:
            cur_url = page.url or ""
        except Exception:
            cur_url = ""
        clean_url = self._normalize_video_url(cur_url)
        if clean_url and clean_url != cur_url:
            await page.goto(clean_url, timeout=60000)

        # 等待详情主体出现；8s 内没出来直接返回默认（避免卡死）
        try:
            await page.wait_for_selector('script#RENDER_DATA, div[data-e2e="user-info"]', timeout=8000)
        except Exception:
            pass

        # 预置默认
        author_name = "未知作者"
        author_url = ""
        like_count = 0
        comment_count = 0
        collect_count = 0

        # -------- 2) 优先从 RENDER_DATA JSON 解析（最稳）--------
        try:
            script_el = await page.query_selector('script#RENDER_DATA')
            if script_el:
                raw = await script_el.inner_text()
                if raw:
                    import json, urllib.parse
                    # RENDER_DATA 是 URL 编码的 JSON，需要先解码
                    decoded = urllib.parse.unquote(raw)
                    data = json.loads(decoded)

                    # 兼容多版本结构：在所有节点里广度搜索需要字段
                    def _walk(obj):
                        if isinstance(obj, dict):
                            for k, v in obj.items():
                                yield k, v
                                if isinstance(v, (dict, list)):
                                    yield from _walk(v)
                        elif isinstance(obj, list):
                            for it in obj:
                                yield from _walk(it)

                    # 逐项提取
                    for k, v in _walk(data):
                        # 作者
                        if author_name == "未知作者" and isinstance(v, dict):
                            # 常见字段：author/authorInfo/nickname/name
                            for kk in ("author", "authorInfo", "user", "userInfo"):
                                if kk in v and isinstance(v[kk], dict):
                                    nn = v[kk].get("nickname") or v[kk].get("name")
                                    hu = v[kk].get("secUid") or v[kk].get("sec_uid") or v[kk].get("uid") or v[kk].get("id")
                                    if nn:
                                        author_name = str(nn).strip()
                                    if hu:
                                        # 优先用 secUid 生成稳定主页
                                        author_url = f"https://www.douyin.com/user/{hu}"
                                    if author_name != "未知作者" and author_url:
                                        break

                        # 点赞/评论/收藏
                        if isinstance(v, dict):
                            lc = v.get("diggCount") or v.get("likeCount") or v.get("statistics", {}).get("diggCount")
                            cc = v.get("commentCount") or v.get("statistics", {}).get("commentCount")
                            sc = v.get("collectCount") or v.get("favoriteCount") or v.get("statistics", {}).get("collectCount")
                            if isinstance(lc, (int, float)) and lc > like_count:
                                like_count = int(lc)
                            if isinstance(cc, (int, float)) and cc > comment_count:
                                comment_count = int(cc)
                            if isinstance(sc, (int, float)) and sc > collect_count:
                                collect_count = int(sc)

        except Exception:
            # JSON 解析失败时，进入 DOM 兜底
            pass

        # -------- 3) DOM 兜底（不依赖随机 class；从按钮附近取数字）--------
        async def _dom_pick_by_icon(icon_keywords):
            """
            在包含点赞/评论/收藏图标的按钮附近取数字；icon_keywords 如 ["赞","like"] / ["评","comment"] / ["藏","collect"]
            """
            try:
                # 找到所有按钮/容器
                nodes = await page.query_selector_all("button, a, div, span")
                import re
                for n in nodes:
                    try:
                        html = (await n.inner_html()) or ""
                        text = (await n.inner_text()) or ""
                        if not html:
                            continue
                        if any(kw in html for kw in icon_keywords):
                            m = re.search(r"([\d\.]+万|[\d\.]+w|[\d,]+)", text.replace(",", ""))
                            if m:
                                return self._parse_count_text(m.group(1))
                    except Exception:
                        continue
            except Exception:
                pass
            return 0

        if like_count == 0:
            like_count = await _dom_pick_by_icon(["赞", "like", "dianzan", "svg"])
        if comment_count == 0:
            comment_count = await _dom_pick_by_icon(["评", "comment", "pinglun", "svg"])
        if collect_count == 0:
            collect_count = await _dom_pick_by_icon(["藏", "collect", "shoucang", "favorite", "svg"])

        # -------- 4) 作者信息 DOM 兜底（多级选择器；统一绝对 URL）--------
        if author_name == "未知作者" or not author_url:
            author_selectors = [
                'div[data-e2e="user-info"] a[href*="/user/"]',
                'div.OMAnIChG a[href*="/user/"]',
                'div.OwMAhChG a[href*="/user/"]',
                'div.ChsTMt34 a[href*="/user/"]',
                'a[href^="//www.douyin.com/user/"]',
                'a[href^="/user/"]',
            ]
            for sel in author_selectors:
                try:
                    a = await page.query_selector(sel)
                    if not a:
                        continue
                    name = (await a.inner_text()) or ""
                    href = await a.get_attribute("href") or ""
                    if name.strip():
                        author_name = name.strip()
                    if href:
                        if href.startswith("//"):
                            author_url = "https:" + href
                        elif href.startswith("/"):
                            author_url = "https://www.douyin.com" + href
                        else:
                            author_url = href
                    if author_name != "未知作者" and author_url:
                        break
                except Exception:
                    continue

        # -------- 5) 视频文案（复用你现有的增强版）--------
        try:
            video_desc = await self._read_video_desc_enhanced(browser_manager)
        except Exception:
            video_desc = ""

        return {
            "video_desc": video_desc or "",
            "author_name": author_name or "未知作者",
            "author_url": author_url or "",
            "like_count": int(like_count) if like_count else 0,
            "comment_count": int(comment_count) if comment_count else 0,
            "collect_count": int(collect_count) if collect_count else 0,
        }


    async def _extract_video_publish_time(self, element) -> str:
        """
        从卡片内文中以文本方式抓 “X分钟前/小时前/天前/周前/月前/年前/刚刚”
        抓不到返回空串。
"""
        try:
            text = await element.inner_text()
            if not text:
                return ""
            import re
            m=re.search(r'(\d{1,2}\s?(分钟前|小时前|天前|周前|月前|年前|刚刚))', text)
            return m.group(0) if m else ""
        except Exception:
            return ""
    async def _extract_video_title(self, element):
        """提取视频标题"""
        try:
            title_text = await element.inner_text() or ""
            # 修复：只移除不可见控制字符，不移除 emoji/特殊符号
            title_text = re.sub(r'[\u200b\u200c\u200d]', '', title_text)

            if title_text and len(title_text.strip()) > 10:
                return title_text.strip()[:100]
            
            title_selectors = [
                '[data-e2e*="video-desc"]',
                '.video-desc',
                '.title',
                'div[class*="desc"]',
                'span[class*="desc"]'
            ]
            
            for selector in title_selectors:
                try:
                    title_el = await element.query_selector(selector)
                    if title_el:
                        text = await title_el.inner_text()
                        if text and text.strip():
                            return text.strip()[:100]
                except:
                    continue
                    
            return ""
            
        except:
            return ""
    def _normalize_video_url(self, url: str) -> str:
        """
        清洗 Douyin 视频 URL：去掉 modeFrom、share_token 等影响渲染的尾参数，
        统一为 https://www.douyin.com/video/{id}
        """
        import re
        m = re.search(r"/video/(\d+)", url or "")
        if not m:
            return url
        vid = m.group(1)
        return f"https://www.douyin.com/video/{vid}"

    async def _read_video_desc_enhanced(self, browser_manager) -> str:
        """增强版视频文案读取"""
        data_selectors = [
            '[data-e2e="video-desc"]',
            '[data-e2e*="desc"]',
            '[data-e2e*="title"]'
        ]
        
        for selector in data_selectors:
            try:
                element = await browser_manager.page.query_selector(selector)
                if element:
                    text = await element.inner_text()
                    if text and text.strip():
                        await self._emit_event("debug", f"✅ 通过data属性获取文案: {text[:50]}...")
                        return text.strip()
            except:
                continue
        
        try:
            meta_element = await browser_manager.page.query_selector('meta[property="og:description"]')
            if meta_element:
                content = await meta_element.get_attribute('content')
                if content and content.strip():
                    await self._emit_event("debug", f"✅ 通过OG meta获取文案: {content[:50]}...")
                    return content.strip()
        except:
            pass
        
        try:
            json_data = await self._extract_desc_from_json(browser_manager)
            if json_data:
                await self._emit_event("debug", f"✅ 通过JSON获取文案: {json_data[:50]}...")
                return json_data
        except:
            pass
        
        try:
            title = await browser_manager.page.title()
            if title and "抖音" not in title:
                return title
        except:
            pass
            
        return "无描述"

    async def _extract_desc_from_json(self, browser_manager):
        """从页面JSON数据中提取视频描述"""
        try:
            script_selectors = [
                'script#RENDER_DATA',
                'script[type="application/json"]',
                'script[data-react-helmet]'
            ]
            
            for selector in script_selectors:
                try:
                    script_element = await browser_manager.page.query_selector(selector)
                    if script_element:
                        script_content = await script_element.inner_text()
                        if script_content:
                            import urllib.parse
                            decoded_content = urllib.parse.unquote(script_content)
                            
                            # 查找常见的描述字段
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

    async def _quick_wait_for_page_load(self, browser_manager, timeout=10):
        """快速等待页面加载完成"""
        try:
            await self._emit_event("operation", "⏳ 快速等待页面加载...")
            
            start_time = asyncio.get_event_loop().time()
            
            try:
                await browser_manager.page.wait_for_load_state('domcontentloaded', timeout=5000)
                await self._emit_event("debug", "✅ DOM内容加载完成")
            except Exception as e:
                await self._emit_event("debug", f"⚠️ DOM加载等待超时: {e}")
            
            search_indicators = [
                '[data-e2e="search-input"]',
                '.search-container',
                '.search-result',
                'body'
            ]
            
            element_found = False
            for selector in search_indicators:
                try:
                    await browser_manager.page.wait_for_selector(selector, timeout=3000)
                    element_found = True
                    await self._emit_event("debug", f"✅ 关键元素加载: {selector}")
                    break
                except:
                    continue
            
            await self.pause(0.5, 1, 'page_load')  # 使用统一的pause方法
            
            content_ready = await self._quick_check_page_content(browser_manager)
            if not content_ready:
                await self._emit_event("debug", "⚠️ 页面内容可能未完全加载，但继续执行")
            
            elapsed = asyncio.get_event_loop().time() - start_time
            await self._emit_event("debug", f"⏱️ 页面加载耗时: {elapsed:.1f}秒")
            
        except Exception as e:
            await self._emit_event("debug", f"⚠️ 快速等待过程中出错: {e}")

    async def _quick_check_page_content(self, browser_manager):
        """快速检查页面内容"""
        try:
            content_check = await browser_manager.page.evaluate("""
                () => {
                    return {
                        hasBody: !!document.body,
                        bodyChildren: document.body ? document.body.children.length : 0,
                        readyState: document.readyState
                    };
                }
            """)
            
            is_ready = content_check['hasBody'] and content_check['bodyChildren'] > 0
            
            await self._emit_event("debug", 
                f"📊 快速检查: Body存在={content_check['hasBody']}, "
                f"子元素={content_check['bodyChildren']}, "
                f"状态={content_check['readyState']}")
            
            return is_ready
            
        except Exception as e:
            await self._emit_event("debug", f"⚠️ 快速内容检查失败: {e}")
            return True

    async def _stop_comment_api_listener(self, browser_manager):
        """停止评论接口监听"""
        if hasattr(self, '_comment_response_handler'):
            browser_manager.page.remove_listener("response", self._comment_response_handler)

    def _get_nested_value(self, data, keys):
        """安全获取嵌套字典的值"""
        try:
            current = data
            for key in keys:
                if isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    return None
            return current
        except:
            return None

    async def enrich_video_detail(self, browser_manager, video_url: str) -> dict:
        """
        打开视频详情页并采集：
        - 作者昵称
        - 作者主页
        - 点赞数
        - 评论数
        - 收藏数
        - 视频标题
        """
        page = browser_manager.page

        try:
            await page.goto(video_url, wait_until="domcontentloaded")
            await asyncio.sleep(1.2)

            # 视频标题
            title = ""
            try:
                title_el = await page.query_selector('[data-e2e="video-desc"]')
                if title_el:
                    title = (await title_el.inner_text()).strip()
            except:
                pass

            # 作者主页
            author_name, author_url = "", ""
            try:
                user_block = await page.query_selector('a[href*="/user/"]')
                if user_block:
                    author_name = (await user_block.inner_text()).strip()
                    href = await user_block.get_attribute("href")
                    if href:
                        author_url = "https:" + href if href.startswith("//") else href
            except:
                pass

            # 点赞/评论/收藏 数
            def parse_count(s):
                import re
                if not s:
                    return 0
                s = s.replace(" ", "")
                if s.endswith("万"):
                    return int(float(s[:-1]) * 10000)
                if s.endswith("亿"):
                    return int(float(s[:-1]) * 100000000)
                m = re.findall(r"\d+", s)
                return int(m[0]) if m else 0

            like = comment = collect = 0
            try:
                spans = await page.eval_on_selector_all(
                    "span",
                    "els => els.map(e => e.textContent.trim())"
                )
                for t in spans:
                    if "点赞" in t:
                        like = parse_count(t.replace("点赞", ""))
                    elif "评论" in t:
                        comment = parse_count(t.replace("评论", ""))
                    elif "收藏" in t:
                        collect = parse_count(t.replace("收藏", ""))
            except:
                pass

            return {
                "video_url": video_url,
                "video_desc": title or "",
                "author_name": author_name or "未知作者",
                "author_url": author_url or "",
                "like_count": like,
                "comment_count": comment,
                "collect_count": collect
            }

        except Exception as e:
            print(f"采集视频详情失败: {e}")
            return {}


