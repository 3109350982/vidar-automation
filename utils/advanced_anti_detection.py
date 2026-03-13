"""
高级反检测机制
提供更真实的人类行为模拟和操作随机化
"""
import time
import random
import asyncio


class AdvancedAntiDetection:
    """高级反检测机制"""

    def __init__(self):
        self.operation_history = []
        self.session_start_time = time.time()
        self.operation_count = 0
        
        # 行为参数
        self.behavior_params = {
            'min_delay': 0.5,
            'max_delay': 3.0,
            'mouse_speed_variance': 0.3,
            'click_duration_variance': 0.2,
            'micro_operation_probability': 0.3
        }
    
    async def human_like_delay(self, min_seconds=None, max_seconds=None, operation_type=None):
        """人类化延迟，考虑操作历史"""
        if min_seconds is None:
            min_seconds = self.behavior_params['min_delay']
        if max_seconds is None:
            max_seconds = self.behavior_params['max_delay']
        
        # 基于操作类型调整延迟
        if operation_type:
            delay_multiplier = self._get_operation_delay_multiplier(operation_type)
            min_seconds *= delay_multiplier
            max_seconds *= delay_multiplier
        
        # 基于操作频率动态调整延迟
        recent_ops = self._get_recent_operations(30)  # 最近30秒的操作
        if len(recent_ops) > 10:
            frequency_factor = len(recent_ops) / 10.0
            min_seconds *= min(frequency_factor, 2.0)
            max_seconds *= min(frequency_factor, 2.0)
        
        delay = random.uniform(min_seconds, max_seconds)
        self._record_operation(operation_type)
        
        start_time = time.time()
        while time.time() - start_time < delay:
            # 检查是否禁用微操作
            try:
                from core.browser_manager import browser_manager
                if browser_manager.page:
                    no_micro_ops = await browser_manager.page.evaluate('window.__NO_MICRO_OPS__ === true')
                    if no_micro_ops:
                        await asyncio.sleep(0.03)
                        continue
            except:
                pass
                
            # 发私信/页面就绪探针阶段，不做随机按键/微移动，避免滚动干扰
            if operation_type not in {'message_send', 'page_probe_retry', 'page_ready', 'message_input_focus', 'follow'}:
                if random.random() < self.behavior_params['micro_operation_probability']:
                    await self._random_micro_operation()
            await asyncio.sleep(0.03)
        
        return True
    
    async def human_like_click(self, x, y, button='left', element_type=None):
        """人类化点击"""
        try:
            from core.browser_manager import browser_manager
            page = browser_manager.page
            await page.mouse.click(x, y, button=button)
            print(f"🎯 Playwright 点击: ({x}, {y}) - {element_type}")
            self._record_operation(f"click_{element_type}")
        except Exception as e:
            print(f"点击失败: {e}")
    
    async def human_like_move(self, target_x, target_y, quick=False):
        """人类化移动"""
        try:
            from core.browser_manager import browser_manager
            page = browser_manager.page
            await page.mouse.move(target_x, target_y)
        except Exception as e:
            print(f"移动失败: {e}")
    
    async def _random_micro_operation(self):
        """随机微小操作"""
        ops = [self._key_press,  self._micro_move]
        op = random.choice(ops)
        await op()
    
    async def _micro_move(self):
        from core.browser_manager import browser_manager
        """微小移动"""
        page = browser_manager.page
        try:
            await page.mouse.move(0, 0)
            await asyncio.sleep(random.uniform(0.05, 0.15))
        except Exception as e:
            print(f"微移动失败: {e}")
    
    async def _key_press(self):
        """按键操作"""
        keys = [' ', 'ArrowLeft', 'ArrowRight', 'Shift']
        key = random.choice(keys)
        from core.browser_manager import browser_manager
        page = browser_manager.page
        try:
            await page.keyboard.press(key)
            await asyncio.sleep(random.uniform(0.05, 0.15))
        except Exception as e:
            print(f"按键失败: {e}")
    
    def _get_operation_delay_multiplier(self, operation_type):
        """根据操作类型获取延迟乘数"""
        delay_multipliers = {
            'like': 1.0,
            'comment_open': 1.2,
            'comment_close': 1.0,
            'video_switch': 1.5,
            'user_avatar_click': 2.0,
            'follow': 2.5,
            'message_send': 3.0,
            'search': 1.8,
            'page_probe_retry': 1.0,  # 页面探针重试 - 禁用微操作
            'page_ready': 1.0,        # 页面就绪 - 禁用微操作
            'message_input_focus': 1.0, # 消息输入聚焦 - 禁用微操作
        }
        return delay_multipliers.get(operation_type, 1.0)
    
    def _get_recent_operations(self, time_window=30):
        """获取最近时间窗口内的操作"""
        current_time = time.time()
        return [op for op in self.operation_history if current_time - op['timestamp'] <= time_window]
    
    def _record_operation(self, operation_type):
        """记录操作"""
        self.operation_count += 1
        self.operation_history.append({
            'type': operation_type,
            'timestamp': time.time(),
            'count': self.operation_count
        })
        if len(self.operation_history) > 1000:
            self.operation_history = self.operation_history[-500:]
    
    async def smart_rest(self, min_rest=30, max_rest=120):
        """智能休息"""
        recent_ops = self._get_recent_operations(300)  # 最近5分钟
        op_count = len(recent_ops)
        
        if op_count > 50:
            rest_time = random.uniform(max_rest * 0.8, max_rest)
        elif op_count > 20:
            rest_time = random.uniform(min_rest, max_rest)
        else:
            rest_time = random.uniform(min_rest * 0.5, min_rest)
        
        print(f"😴 智能休息 {rest_time:.1f} 秒，最近操作数: {op_count}")
        await asyncio.sleep(rest_time)

    async def smart_read(self, min_seconds=3.0, max_seconds=8.0):
        """
        模拟用户阅读/浏览内容的停顿时间，用于打开笔记后或回复前的“看一看”行为。
        """
        if min_seconds <= 0 or max_seconds <= 0:
            return
        if max_seconds < min_seconds:
            max_seconds = min_seconds
        await self.human_like_delay(min_seconds, max_seconds, operation_type='page_read')

    async def random_scroll(self, page, min_px=260, max_px=780):
        """
        人类化随机滚动：用于“唤醒评论条”“轻微浏览内容”等场景。
        在垂直方向拆成若干小步滚动，避免一次性固定值。
        """
        try:
            if max_px < min_px:
                max_px = min_px
            distance = random.randint(int(min_px), int(max_px))
            steps = random.randint(1, 3)
            dy_base = distance // steps if steps > 0 else distance
            for i in range(steps):
                dy = dy_base + random.randint(-40, 40)
                await page.mouse.wheel(0, dy)
                await asyncio.sleep(random.uniform(0.05, 0.18))
        except Exception as e:
            print(f"随机滚动失败: {e}")

    async def human_like_click_bbox(self, box, button='left', element_type=None):
        """
        基于元素 bounding box 的人类化点击：
        在 bbox 中间区域随机偏移点击点，避免所有点击都落在精确中心。
        """
        if not box:
            return
        try:
            x = box["x"] + random.uniform(box["width"] * 0.3, box["width"] * 0.7)
            y = box["y"] + random.uniform(box["height"] * 0.3, box["height"] * 0.7)
            await self.human_like_click(x, y, button=button, element_type=element_type)
        except Exception as e:
            print(f"human_like_click_bbox 失败: {e}")
    
    def get_operation_statistics(self):
        """获取操作统计"""
        recent_ops = self._get_recent_operations(300)  # 最近5分钟
        stats = {
            'total_operations': self.operation_count,
            'recent_operations': len(recent_ops),
            'session_duration': time.time() - self.session_start_time,
            'avg_operations_per_minute': len(recent_ops) / 5.0
        }
        return stats

# 全局反检测实例
anti_detection = AdvancedAntiDetection()
