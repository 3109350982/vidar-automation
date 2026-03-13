"""
随机点赞服务 - 模拟人类刷视频并随机点赞（快捷键版本）
"""
import time
import random
import asyncio
from services.base_service import BaseService
from core.config_manager import config_manager

class RandomLikeService(BaseService):
    """随机点赞服务（快捷键版本）"""
    
    def __init__(self):
        super().__init__()  # 移除 config 参数
        self.like_count = 0
        self.video_count = 0
    
    async def execute(self, duration_minutes=10, **kwargs):
        """
        执行随机点赞功能
        
        Args:
            duration_minutes: 运行时间(分钟)
        """
        await self._emit_event("operation", f"🚀 开始随机点赞任务，时长: {duration_minutes}分钟")
        
        # 确保浏览器就绪
        if not await self._ensure_browser_ready():
            await self._emit_event("error", "❌ 浏览器未就绪")
            return
        
        browser_manager = await self._get_browser_manager()
        await self._go_to_recommend_page(browser_manager.page)
        end_time = time.time() + duration_minutes * 60
        total_videos = 0
        total_likes = 0
        
        # 获取行为参数
        behavior_config = config_manager.get('behavior', {})
        like_probability = kwargs.get('like_probability', behavior_config.get('like_probability', 0.6))
        like_probability = max(0.0, min(1.0, float(like_probability)))
        
        try:
            while time.time() < end_time and not await self._check_stop():
                try:
                    # 随机观看时间（5-25秒）
                    watch_time = random.uniform(
                        behavior_config.get('min_watch_time', 5),
                        behavior_config.get('max_watch_time', 25)
                    )
                    
                    await self._emit_event("operation", f"⏳ 观看视频 {watch_time:.1f}秒...")
                    
                    # 人类化等待观看时间
                    start_wait = time.time()
                    while time.time() - start_wait < watch_time and not await self._check_stop():
                        # 在等待期间随机执行微小操作
                        if random.random() < 0.1:
                            from utils.advanced_anti_detection import anti_detection
                            await anti_detection._random_micro_operation()
                        await self._interruptible_sleep(0.1)
                    
                    if await self._check_stop():
                        break
                    
                    # 根据概率点赞（快捷键版）
                    if random.random() < like_probability:
                        like_success = await self._smart_like_video(browser_manager)
                        if like_success:
                            total_likes += 1
                            self.like_count += 1
                    
                    # if await self._check_stop():
                    #     break
                    
                    # 切换到下一个视频
                    await self._emit_event("operation", "⬇️ 切换到下一个视频...")
                    
                    # 使用快捷键切换视频
                    douyin_config = config_manager.get('douyin', {})
                    shortcuts = douyin_config.get('shortcuts', {})
                    next_video_key = shortcuts.get('next_video', 'ArrowDown')
                    
                    if await browser_manager.press_key(next_video_key):
                        total_videos += 1
                        self.video_count += 1
                        await self._emit_event("operation", f"📺 已处理视频: {total_videos}")
                        
                        # 等待视频加载（使用统一的pause方法）
                        await self.pause(2, 4, 'video_switch')
                    else:
                        await self._emit_event("error", f"❌ 切换视频失败")
                    
                    # 每处理10个视频后智能休息
                    if total_videos % 10 == 0 and total_videos > 0:
                        from utils.advanced_anti_detection import anti_detection
                        await anti_detection.smart_rest(20, 40)
                        # 发送统计信息
                        stats = anti_detection.get_operation_statistics()
                        await self._emit_event("operation", 
                                            f"📊 运行统计: {total_videos}视频/{total_likes}点赞, 平均{stats['avg_operations_per_minute']:.1f}操作/分钟")
                
                except Exception as e:
                    error_msg = f"随机点赞过程中出错: {e}"
                    await self._emit_event("error", error_msg)
                    await self.pause(2, 4, 'like')
        
        except Exception as e:
            error_msg = f"随机点赞任务异常: {e}"
            await self._emit_event("error", error_msg)
        
        finally:
            # 任务完成
            final_msg = f"🏁 随机点赞完成。共处理 {total_videos} 个视频，点赞 {total_likes} 次。"
            await self._emit_event("finished", final_msg)
    
    async def _smart_like_video(self, browser_manager):
        """智能点赞视频（快捷键版）"""
        try:
            await self._emit_event("operation", "🔄 使用快捷键点赞...")
            
            # 使用快捷键点赞
            douyin_config = config_manager.get('douyin', {})
            shortcuts = douyin_config.get('shortcuts', {})
            like_key = shortcuts.get('like', 'z')
            
            if await browser_manager.press_key(like_key):
                await self._emit_event("operation", f"✅ 使用快捷键{like_key}点赞成功！")
                await self.pause(0.5, 1.5, 'like')
                return True
            else:
                await self._emit_event("error", f"❌ 快捷键{like_key}点赞失败")
                return False
            
        except Exception as e:
            await self._emit_event("error", f"❌ 点赞过程中出错: {str(e)}")
            return False
    # 放在类里（RandomLikeService）任意位置即可
    async def _go_to_recommend_page(self, page):
        """
        导航到抖音推荐页并确认首屏可互动。
        不改你的点赞流程，只保证“起点正确”。
        """
        try:
            # 1) 直接去首页（桌面端首页默认就是‘推荐’流）
            await page.goto("https://www.douyin.com/?recommend=1", wait_until="domcontentloaded", timeout=5000)

            try:
                await page.wait_for_selector(
                    'a[href*="/video/"], [data-e2e*="feed"] a[href*="/video/"], video',
                    timeout=5000
                )
            except:
                # 兜底：轻滚一下触发懒加载再等一会
                await page.evaluate("()=>window.scrollBy({top:200,left:0,behavior:'instant'})")
                await page.wait_for_timeout(150)
                await page.wait_for_selector('a[href*="/video/"], video', timeout=3000)

            # 5) 让页面聚焦到播放器区域，避免热键打空
        
                    # 点页面中上部区域也可以激活键盘
                vw, vh = await page.evaluate("()=>[window.innerWidth, window.innerHeight]")
                await page.mouse.click(vw//2, int(vh*0.4))

            return True
        except Exception as e:
            await self._emit_event("warning", f"⚠️ 导航推荐页失败，继续按原逻辑: {e}")
            return False
