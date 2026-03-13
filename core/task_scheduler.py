"""
任务调度器 - 管理所有服务的执行
"""
import asyncio
import time
from typing import Dict, List, Callable, Any
from services.random_like import RandomLikeService
from services.customer_acquisition import CustomerAcquisitionService
from services.private_message import PrivateMessageService
from services.xhs_acquisition import XhsAcquisitionService
from services.xhs_comment import XhsCommentService
from services.xhs_reply_watch import XhsReplyWatchService

class TaskScheduler:
    """任务调度器 - 管理所有服务的执行"""
    
    def __init__(self):
        self.services: Dict[str, Any] = {}
        self.active_services: List[str] = []
        self.event_callbacks: List[Callable] = []
        self.initialized = False
    
    async def initialize(self):
        """初始化调度器"""
        if self.initialized:
            return
        
        # 注册所有服务
        await self._register_services()
        self.initialized = True
    
    async def _register_services(self):
        """注册所有服务"""
        # 随机点赞服务
        random_like_service = RandomLikeService()  # 移除 config 参数
        self.services['RandomLikeService'] = random_like_service
        
        # 获客服务
        customer_service = CustomerAcquisitionService()  # 移除 config 参数
        self.services['CustomerAcquisitionService'] = customer_service
        # 小红书采集服务
        xhs_service = XhsAcquisitionService()
        self.services['XhsAcquisitionService'] = xhs_service
        # 小红书评论服务
        xhs_comment_service = XhsCommentService()
        self.services['XhsCommentService'] = xhs_comment_service
        # 小红书评论监听服务
        xhs_reply_watch = XhsReplyWatchService()
        self.services['XhsReplyWatchService'] = xhs_reply_watch

        # 私信服务
        message_service = PrivateMessageService()  # 移除 config 参数
        self.services['PrivateMessageService'] = message_service
        
        # 为所有服务添加事件回调
        for service_name, service in self.services.items():
            service.add_event_callback(self._handle_service_event)
    
    async def start_service(self, service_name: str, **kwargs) -> bool:
        """启动指定服务"""
        if service_name not in self.services:
            await self._emit_event({"type": "error", "msg": f"服务未注册: {service_name}"})
            return False
        
        # 停止其他服务（互斥执行）
        await self.stop_all_services()
        
        service = self.services[service_name]
        if await service.start(**kwargs):
            self.active_services.append(service_name)
            return True
        
        return False
    
    async def stop_service(self, service_name: str):
        """停止指定服务"""
        if service_name in self.services:
            await self.services[service_name].stop()
            if service_name in self.active_services:
                self.active_services.remove(service_name)
    
    async def stop_all_services(self):
        """停止所有服务"""
        for service_name in self.active_services[:]:
            await self.stop_service(service_name)
    
    async def get_service_status(self, service_name: str) -> Dict[str, Any]:
        """获取服务状态"""
        if service_name in self.services:
            service = self.services[service_name]
            return {
                "running": service.is_running,
                "name": service.service_name,
                "stop_requested": service.stop_requested
            }
        return {}
    
    async def get_all_services_status(self) -> Dict[str, Dict]:
        """获取所有服务状态"""
        status = {}

        for name, service in self.services.items():
            status[name] = {
                "running": service.is_running,
                "name": service.service_name,
                "stop_requested": service.stop_requested,
                "current_stage": getattr(service, "current_stage", None)  # ★ 新增
            }
        return status
    
    def add_event_callback(self, callback: Callable):
        """添加事件回调"""
        self.event_callbacks.append(callback)
    
    async def _handle_service_event(self, event):
        """处理服务事件"""
        # 添加调度器信息
        event['scheduler'] = 'TaskScheduler'
        event['timestamp'] = time.time()
        
        await self._emit_event(event)
    
    async def _emit_event(self, event):
        """触发事件"""
        for callback in self.event_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                print(f"事件回调错误: {e}")
    
    async def get_available_services(self) -> List[str]:
        """获取可用的服务列表"""
        return list(self.services.keys())
    
    async def is_any_service_running(self) -> bool:
        """检查是否有服务在运行"""
        return len(self.active_services) > 0