"""
所有服务的基类 - 增强版，集成视觉辅助功能
"""
from abc import ABC, abstractmethod
import threading
import time
import asyncio
import random
from core.browser_manager import browser_manager
from utils.data_storage import data_storage
from utils.advanced_anti_detection import anti_detection  # 新增导入

class BaseService(ABC):
    """所有服务的基类 - 增强版"""
    
    def __init__(self):
        self.is_running = False
        self.stop_requested = False
        self.current_thread = None
        self.event_callbacks = []
    
    @abstractmethod
    async def execute(self, **kwargs):
        """执行服务的主要逻辑"""
        pass
    
    async def start(self, **kwargs):
        """启动服务"""
        if self.is_running:
            await self._emit_event("warning", "服务已在运行中")
            return False
        
        self.is_running = True
        self.stop_requested = False
        self.current_stage = kwargs.get("mode", getattr(self, "current_stage", None))  # ★ 新增

        
        
        try:
            await self._emit_event("started", {"service": self.service_name})
            await self.execute(**kwargs)
        except Exception as e:
            await self._emit_event("error", str(e))
        finally:
            self.is_running = False
            await self._emit_event("finished", {"service": self.service_name})
        
        return True
    
    async def stop(self):
        """停止服务"""
        self.stop_requested = True
        self.is_running = False
    
    def add_event_callback(self, callback):
        """添加事件回调"""
        self.event_callbacks.append(callback)
    
    async def _emit_event(self, event_type: str, data=None):
        """触发事件"""
        event = {
            "type": event_type,
            "service": self.service_name,
            "data": data,
            "timestamp": time.time()
        }
        
        for callback in self.event_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                print(f"事件回调错误: {e}")
    
    @property
    def service_name(self) -> str:
        """服务名称"""
        return self.__class__.__name__
    
    async def _get_browser_manager(self):
        """获取浏览器管理器实例"""
        return browser_manager
    
    async def _get_data_storage(self):
        """获取数据存储实例"""
        return data_storage
    
    async def _get_vision_helper(self):
        """获取视觉助手实例"""
        browser_mgr = await self._get_browser_manager()
        return browser_mgr.vision_helper
    
    async def _hybrid_click(self, element_type, dom_selector=None, confidence=0.7, region=None):
        """混合点击 - 已废弃，视觉仅在私信阶段使用"""
        # 只使用DOM点击
        browser_manager = await self._get_browser_manager()
        
        if dom_selector and browser_manager.locator:
            try:
                if await browser_manager.locator.click_element(dom_selector):
                    await self._emit_event("operation", f"✅ DOM点击成功: {element_type}")
                    return True
            except Exception as e:
                print(f"DOM点击失败 {element_type}: {e}")
        
        await self._emit_event("error", f"❌ DOM点击失败: {element_type}")
        return False
    
    async def pause(self, min_seconds=0.5, max_seconds=1.5, operation_type="pause"):
        """统一的暂停方法 - 结合人类化延迟和中断检查"""
        if await self._check_stop():
            return False
        
        await anti_detection.human_like_delay(min_seconds, max_seconds, operation_type)
        return not self.stop_requested
    
    async def _random_delay(self, min_seconds=1, max_seconds=3):
        """随机延迟"""
        delay = random.uniform(min_seconds, max_seconds)
        start_time = time.time()
        while time.time() - start_time < delay and not self.stop_requested:
            await asyncio.sleep(0.1)
        return not self.stop_requested
    
    async def _interruptible_sleep(self, seconds):
        """可中断的睡眠"""
        start_time = time.time()
        while time.time() - start_time < seconds and not self.stop_requested:
            await asyncio.sleep(0.1)
    
    async def _check_stop(self):
        """检查是否应该停止"""
        return self.stop_requested
    
    async def _ensure_browser_ready(self):
        """确保浏览器就绪"""
        browser_manager = await self._get_browser_manager()
        if not await browser_manager.ensure_running():
            raise Exception("浏览器未就绪")
        return True