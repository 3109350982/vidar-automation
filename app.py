"""
抖音自动化系统 Web 后端 - 增强版（添加数据清除功能）
"""
from typing import List, Optional
from utils.license_client import init_from_cache, start_recheck, status as lic_status, activate as lic_activate
import asyncio
import json
import multiprocessing
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os,time
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Any
from services.customer_acquisition import CustomerAcquisitionService
from fastapi import Body
import json
from pathlib import Path
import re
# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.browser_manager import browser_manager
from core.task_scheduler import TaskScheduler
from utils.data_storage import data_storage
from main import get_system
from utils.strings import split_list  # 新增导入
from utils.license_client import clear_cache as lic_clear_cache
import logging
from utils.license_client import clear_cache as lic_clear_cache
from models.ai_schemas import AgentStartRequest, AgentAnswerRequest, AgentRunRequest
from services.ai_agent_service import get_ai_agent_service


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("douyin_web")
# 创建FastAPI应用
app = FastAPI(title="抖音自动化系统")

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
def json_response(status: str, message: str = "", data=None):
    return {
        "status": status,
        "message": message,
        "data": data
    }

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# WebSocket连接管理
class ConnectionManager:
    def __init__(self):
        self.active_connections = []
    
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
    
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
    
    async def send_personal_message(self, message: dict, websocket: WebSocket):
        try:
            await websocket.send_json(message)
        except:
            self.disconnect(websocket)
    
    async def broadcast(self, message: dict):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                disconnected.append(connection)
        
        for connection in disconnected:
            self.disconnect(connection)

manager = ConnectionManager()

# 全局系统实例
system = None

# 新增辅助函数
async def ok(msg):
    """成功响应"""
    await manager.broadcast({"type": "operation", "msg": msg})
    return {"status": "success", "message": msg}

async def fail(msg):
    """失败响应"""
    await manager.broadcast({"type": "error", "msg": msg})
    return {"status": "error", "message": msg}

@app.on_event("startup")
async def startup_event():
    """应用启动时初始化系统"""
    init_from_cache()
    _s = lic_status()
    now = int(time.time())
    if int(_s.get("lic_exp", 0) or 0) and int(_s.get("lic_exp", 0)) <= now:
        print("⚠️ 本地缓存显示许可证已到期，将等待前端重新验证。")
    elif _s.get("token") and int(_s.get("token_exp", 0) or _s.get("exp", 0) or 0) <= now:
        print("⚠️ 本地缓存的 token 已过期，将等待前端重新验证。")
    global system
    system = await get_system()
    
    # 检查模板文件是否存在
    template_files = [
        'templates/message_button.png',
        'templates/follow_button.png', 
        'templates/send_button.png',
        'templates/message_input.png'
    ]
    
    missing_templates = []
    for template in template_files:
        if not os.path.exists(template):
            missing_templates.append(template)
    
    if missing_templates:
        print(f"⚠️ 警告：缺少以下模板文件: {missing_templates}")
        print("💡 请确保在 templates 目录下放置相应的PNG文件")
    else:
        print("✅ 所有视觉模板文件就绪")
    
    print("🚀 抖音自动化系统 Web 后端已启动") 
    s = lic_status()
    if s.get("valid"):
        start_recheck(3600, 0)

@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时清理资源"""
    if system:
        await system.close()
    print("🔴 系统已关闭")

# API路由
@app.get("/")
async def read_index():
    """返回前端页面"""
    return FileResponse('static/index.html')

@app.get("/api/health")
async def health_check():
    """健康检查"""
    # 检查模板文件状态
    template_files = [
        'templates/message_button.png',
        'templates/follow_button.png', 
        'templates/send_button.png',
        'templates/message_input.png'
    ]
    
    template_status = {}
    for template in template_files:
        template_status[os.path.basename(template)] = os.path.exists(template)
    
    return {
        "status": "healthy", 
        "message": "系统运行正常",
        "templates": template_status
    }
@app.post("/api/app/quit")
async def api_app_quit():
    # 延迟 200ms 让响应先发回前端，再退出进程
    asyncio.get_event_loop().call_later(0.2, lambda: os._exit(0))
    return {"status": "success", "message": "应用已退出"}
@app.get("/api/license/status")
async def api_license_status():
    d = lic_status()
    return {"status": "success", "data": d}
from fastapi import Request
@app.post("/api/license/clear")
async def api_license_clear():
    """清除本地 license 缓存（用于处理过期/脏缓存卡死的场景）"""
    try:
        lic_clear_cache()
        return {"status": "success", "message": "缓存已清除"}
    except Exception as e:
        return {"status": "error", "message": f"清除失败: {e}"}
@app.post("/api/license/activate")
async def api_license_activate(request: Request, key: str = ""):
    # 既支持 ?key=xxx，也支持 JSON {"key":"xxx"}
    if not key:
        try:
            payload = await request.json()
            key = (payload or {}).get("key", "")
        except Exception:
            key = ""
    key = (key or "").strip()
    if not key:
        return {"status": "error", "message": "缺少密钥 key"}

    try:
        data = lic_activate(key)
        # —— 调试：打印服务端/本地判定的关键值 —— 
        print(f"[LIC-DEBUG] activate-> lic_exp={data.get('lic_exp')} token_exp={data.get('token_exp')} now={int(time.time())}", flush=True)
        now = int(time.time())
        #-------------------------------------------
        data = lic_activate(key)
        now = int(time.time())
        if int(data.get("lic_exp", 0)) and int(data["lic_exp"]) <= now:
            return {"status": "error", "message": "激活失败: 密钥已过期"}

        if not data.get("valid"):
            return {"status": "error", "message": "激活失败: 密钥已过期或无效"}
        start_recheck(3600, 0)
        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": f"激活失败: {e}"}
@app.post("/api/browser/start")
async def api_start_browser():
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}
    """启动浏览器 - 并发安全修复版"""
    try:
        # 幂等短路：已在运行就直接返回
        if browser_manager.is_running and browser_manager.page is not None:
            try:
                await browser_manager.page.title()
                return {"status": "success", "message": "✅ 浏览器已在运行"}
            except Exception:
                pass  # 交给 ensure_running 去修复
        
        success = await browser_manager.ensure_running()
        if success:
            return {"status": "success", "message": "✅ 浏览器启动成功"}
        else:
            return {"status": "error", "message": "❌ 浏览器启动失败"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 浏览器启动异常: {str(e)}"}

# 服务启动的统一浏览器检查函数
async def _ensure_browser_for_service():
    s = lic_status()
    if not s.get("valid"):
        await manager.broadcast({"type":"error","msg":"❌ 未授权或已过期，请先在页面输入许可证密钥"})
        return False
    success = await browser_manager.ensure_running()
    if not success:
        await manager.broadcast({"type": "error", "msg": "❌ 浏览器启动失败，无法启动服务"})
    return success

# ... 其他API路由保持不变 ...

@app.post("/api/random_like/start")
async def start_random_like(duration: int = 10, like_frequency: int = 60):
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}
    """开始随机点赞（支持前端配置点赞频率）"""
    try:
        # 统一浏览器检查
        if not await _ensure_browser_for_service():
            return {"status": "error", "message": "❌ 浏览器未就绪"}

        # 百分比 → 概率（0~1），并做边界夹紧
        like_frequency = max(0, min(100, like_frequency))
        like_probability = like_frequency / 100.0

        success = await system.start_service(
            "RandomLikeService",
            duration_minutes=duration,
            like_probability=like_probability
        )
        if success:
            await manager.broadcast({"type": "operation", "msg": f"🚀 开始随机点赞任务，时长: {duration} 分钟，点赞频率: {like_frequency}%"})
            return {"status": "success", "message": "随机点赞服务已启动"}
        else:
            return {"status": "error", "message": "随机点赞服务启动失败"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 随机点赞服务启动异常: {str(e)}"}

@app.post("/api/random_like/stop")
async def stop_random_like():
    """停止随机点赞"""
    await system.stop_service("RandomLikeService")
    return {"status": "success", "message": "🛑 随机点赞已停止"}

# 阶段一：视频采集
@app.post("/api/stage1_collect_videos/start")
async def start_stage1_collect_videos(
    keywords: str = Body(default=""),
    sort_type: str = Body(default="视频"),
    videos_per_keyword: int = Body(default=5),
    duration: int = Body(default=10),
):
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}
    """开始阶段一：视频采集"""
    try:
        keyword_list = split_list(keywords)  # 使用统一的字符串分割函数
        
        if not keyword_list:
            return {"status": "error", "message": "❌ 请输入关键词"}
        
        # 统一浏览器检查
        if not await _ensure_browser_for_service():
            return {"status": "error", "message": "❌ 浏览器未就绪"}
        source = sort_type
        success = await system.start_service(
            "CustomerAcquisitionService",
            keywords=keyword_list,
            sort_type=source,
            videos_per_keyword=videos_per_keyword,
            duration_minutes=duration,
            mode = "stage1"
        )
        
        if success:
            await manager.broadcast({"type": "operation", "msg": f"🚀 开始阶段一：视频采集"})
            await manager.broadcast({"type": "operation", "msg": f"📦 采集来源: {source}"})
            await manager.broadcast({"type": "operation", "msg": f"🔍 关键词: {', '.join(keyword_list)}"})
            return {"status": "success", "message": "视频采集服务已启动"}
        else:
            return {"status": "error", "message": "视频采集服务启动失败"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 视频采集服务启动异常: {str(e)}"}

@app.post("/api/stage1_collect_videos/stop")
async def stop_stage1_collect_videos():
    """停止阶段一视频采集"""
    await system.stop_service("CustomerAcquisitionService")
    return {"status": "success", "message": "🛑 阶段一视频采集已停止"}

# 阶段二：用户采集
@app.post("/api/stage2_collect_users/start")
async def start_stage2_collect_users(
    video_urls: str = Body(default=""),
    user_comment_keywords: str = Body(default=""),
    ip_keywords: str = Body(default=""),
    duration: int = Body(default=10),
):
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}
    """开始阶段二：用户采集"""
    try:
        print(f"🔍 收到阶段二请求: video_urls={video_urls}, user_comment_keywords={user_comment_keywords}")
        
        if not video_urls.strip():
            return {"status": "error", "message": "❌ 请选择视频"}
        if not user_comment_keywords.strip():
            return {"status": "error", "message": "❌ 请输入用户评论关键词"}
        
        user_comment_kw_list = split_list(user_comment_keywords)
        ip_list = split_list(ip_keywords)
        video_list = split_list(video_urls)
        
        print(f"📹 解析后的视频列表: {video_list}")
        print(f"💬 用户评论关键词: {user_comment_kw_list}")
        
        # 统一浏览器检查
        if not await _ensure_browser_for_service():
            return {"status": "error", "message": "❌ 浏览器未就绪"}
            
        success = await system.start_service(
            "CustomerAcquisitionService",
            videos=video_list,
            user_comment_keywords=user_comment_kw_list,
            ip_keywords=ip_list,
            duration_minutes=duration,
            mode="stage2"
        )
        if success:
            await manager.broadcast({"type": "operation", "msg": "🚀 开始阶段二：用户采集"})
            await manager.broadcast({"type": "operation", "msg": f"💬 用户评论关键词: {user_comment_keywords}"})
            await manager.broadcast({"type": "operation", "msg": f"📹 处理视频数量: {len(video_list)}"})
            return {"status": "success", "message": "用户采集服务已启动"}
        else:
            return {"status": "error", "message": "用户采集服务启动失败"}
    except Exception as e:
        print(f"❌ 用户采集服务启动异常: {str(e)}")
        return {"status": "error", "message": f"❌ 用户采集服务启动异常: {str(e)}"}

@app.post("/api/stage2_collect_users/stop")
async def stop_stage2_collect_users():
    """停止阶段二用户采集"""
    await system.stop_service("CustomerAcquisitionService")
    return {"status": "success", "message": "🛑 阶段二用户采集已停止"}
# ---------------- 小红书相关接口 ----------------

@app.post("/api/xhs/open_home")
async def open_xhs_home():
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}
    try:
        # 统一浏览器检查
        if not await _ensure_browser_for_service():
            return {"status": "error", "message": "❌ 浏览器未就绪"}

        from core.browser_manager import browser_manager
        url = "https://www.xiaohongshu.com/explore"
        await manager.broadcast({"type":"operation","msg": f"🌐 打开小红书首页: {url}"})
        await browser_manager.page.goto(url, wait_until='domcontentloaded', timeout=60000)
        return {"status": "success", "message": "✅ 已打开小红书首页"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 打开小红书首页异常: {str(e)}"}


@app.post("/api/xhs/collect/start")
async def start_xhs_collect(
    keywords: str = Body(default=""),
    sort_type: str = Body(default="综合"),            # 综合 / 笔记 / 视频
    videos_per_keyword: int = Body(default=5),
    duration: int = Body(default=10),
    agent_session_id: int = Body(default=0),
    project_id: int = Body(default=0),
):
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}
    """开始小红书采集"""
    try:
        keyword_list = split_list(keywords)
        if not keyword_list:
            return {"status": "error", "message": "❌ 请输入关键词"}

        # 统一浏览器检查
        if not await _ensure_browser_for_service():
            return {"status": "error", "message": "❌ 浏览器未就绪"}

        agent_session_id = int(agent_session_id or 0)
        project_id = int(project_id or 0)
        collection_batch_id = ""
        enable_note_content = False

        if agent_session_id > 0:
            service = get_ai_agent_service()
            session = service.get_session(agent_session_id)
            if not session:
                return {"status": "error", "message": "Agent 会话不存在，无法绑定本次采集"}

            project_id = project_id or int(session.get("project_id") or 0)
            collection_batch_id = f"agent_{agent_session_id}_{int(time.time())}"
            enable_note_content = True

        system = await get_system()
        success = await system.start_service(
            "XhsAcquisitionService",
            keywords=keyword_list,
            sort_type=sort_type,
            videos_per_keyword=int(videos_per_keyword),
            duration_minutes=int(duration),
            mode="stage1",
            agent_session_id=agent_session_id,
            project_id=project_id,
            collection_batch_id=collection_batch_id,
            enable_note_content=enable_note_content,
        )
        if success:
            await manager.broadcast({"type": "operation", "msg": f"🚀 小红书采集已启动"})
            await manager.broadcast({"type": "operation", "msg": f"🔍 关键词: {', '.join(keyword_list)}"})
            await manager.broadcast({"type": "operation", "msg": f"📦 每词采集: {videos_per_keyword} 条，模式: {sort_type}"})
            if agent_session_id > 0:
                await manager.broadcast({"type": "operation", "msg": f"🤖 已绑定 Agent session_id={agent_session_id}，将进入详情页采集正文"})
            return {"status": "success", "message": "小红书采集服务已启动"}
        else:
            return {"status": "error", "message": "小红书采集服务启动失败"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 小红书采集服务启动异常: {str(e)}"}


@app.post("/api/xhs/collect/stop")
async def stop_xhs_collect():
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}
    """停止小红书采集"""
    try:
        system = await get_system()
        await system.stop_service("XhsAcquisitionService")
        await manager.broadcast({"type": "operation", "msg": "🛑 小红书采集已停止"})
        return {"status": "success", "message": "小红书采集服务已停止"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 小红书采集服务停止异常: {str(e)}"}

from fastapi import Body
# 私信功能
@app.post("/api/send_messages/start")
async def start_send_messages(
    message_template: str = Body(default="您好，看到您的评论，很高兴认识您！"),
    duration: int = Body(default=10),
    user_urls: str = Body(default=""),
    interval_minutes: int = Body(default=4),
    like_frequency: int = Body(default=60),
    rotate_accounts: int = Body(default=0),      # 新增：是否启用多账号轮询（0/1）
    account_dirs: str = Body(default="")         # 新增：账号目录，多行或空格分隔
):

    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}
    """开始私信发送（支持每人间隔分钟数 & 等待期刷视频的点赞频率）"""
    try:
        selected = split_list(user_urls)

        if not await _ensure_browser_for_service():
            return {"status": "error", "message": "❌ 浏览器未就绪"}

        like_frequency = max(0, min(100, like_frequency))
        like_probability = like_frequency / 100.0

        success = await system.start_service(
            "PrivateMessageService",
            message_template=message_template,
            duration_minutes=duration,
            user_urls=selected,
            interval_minutes=interval_minutes,
            like_probability=like_probability,
            rotate_accounts=bool(int(rotate_accounts or 0)),  # 新增
            account_dirs=account_dirs                           # 新增（原样传入，服务里做清洗）
        )
        if success:
            await manager.broadcast({"type": "operation", "msg": f"🚀 开始私信发送任务（间隔 {interval_minutes} 分钟/人，等待期点赞频率 {like_frequency}%）"})
            if rotate_accounts:
                await manager.broadcast({"type":"operation","msg": "🔁 多账号轮询已启用"})

            await manager.broadcast({"type": "operation", "msg": f"💌 私信模板: {message_template}"})
            return {"status": "success", "message": "私信服务已启动"}
        else:
            return {"status": "error", "message": "私信服务启动失败"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 私信服务启动异常: {str(e)}"}
@app.post("/api/xhs/comment/start")
async def api_xhs_comment_start(payload: dict = Body(default=None)):
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}
    try:
        urls = (payload or {}).get("urls") or []
        text = (payload or {}).get("text") or ""
        templates = (payload or {}).get("templates") or []
        per_note = int((payload or {}).get("per_note") or 1)
        interval_min_ms = int((payload or {}).get("interval_min_ms") or 1200)
        interval_max_ms = int((payload or {}).get("interval_max_ms") or 2500)
        skip_if_commented = bool((payload or {}).get("skip_if_commented") or False)
        variant = bool((payload or {}).get("variant") or False)

        ok = await system.start_service(
            "XhsCommentService",
            urls=urls, text=text, templates=templates,
            per_note=per_note,
            interval_min_ms=interval_min_ms,
            interval_max_ms=interval_max_ms,
            skip_if_commented=skip_if_commented,
            variant=variant
        )
        if ok:
            return {"status":"success","message":"🚀 小红书评论任务已启动"}
        return {"status":"error","message":"小红书评论任务启动失败"}
    except Exception as e:
        return {"status":"error","message":f"❌ 小红书评论任务启动异常: {e}"}

@app.post("/api/xhs/comment/stop")
async def api_xhs_comment_stop():
    await system.stop_service("XhsCommentService")
    return {"status":"success","message":"🛑 小红书评论任务已停止"}
@app.post("/api/xhs/reply_watch/start")
async def api_xhs_reply_watch_start(payload: dict = Body(default=None)):
    """启动小红书评论监听并自动回复"""
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}
    data = payload or {}
    urls        = data.get("urls") or []
    keywords    = data.get("keywords") or data.get("watch_keywords") or []
    ip_filters  = data.get("ip_filters") or data.get("ip_keywords") or []
    text        = (data.get("text") or "").strip()
    templates   = data.get("templates") or []
    per_note    = int(data.get("per_note") or 3)
    interval_min_ms = int(data.get("interval_min_ms") or data.get("interval_ms") or 1200)
    interval_max_ms = int(data.get("interval_max_ms") or data.get("interval_ms") or 2500)
    dedup_hours = int(data.get("dedup_hours") or 0)
    after_expr  = (data.get("after_expr") or "").strip()
    variant    = bool((payload or {}).get("variant") or False)  # 回复文本变体（随机点号/表情）
 
    ok = await system.start_service(
        "XhsReplyWatchService",
        urls=urls, keywords=keywords, ip_filters=ip_filters,
        text=text, templates=templates,
        per_note=per_note,
        interval_min_ms=interval_min_ms,
        interval_max_ms=interval_max_ms,
        dedup_hours=dedup_hours,
        variant=variant,
        after_expr=after_expr
    )
    if ok:
        return {"status":"success","message":"🚀 小红书评论监听已启动"}
    return {"status":"error","message":"小红书评论监听启动失败"}

@app.post("/api/xhs/reply_watch/stop")
async def api_xhs_reply_watch_stop():
    """停止小红书评论监听"""
    await system.stop_service("XhsReplyWatchService")
    return {"status":"success","message":"🛑 小红书评论监听已停止"}



@app.post("/api/send_messages/stop")
async def stop_send_messages():
    """停止私信发送"""
    await system.stop_service("PrivateMessageService")
    return {"status": "success", "message": "🛑 私信发送已停止"}

@app.post("/api/stop_all")
async def stop_all_services():
    """停止所有服务"""
    await system.stop_all_services()
    return {"status": "success", "message": "🛑 所有服务已停止"}

@app.get("/api/status")
async def get_system_status():
    """获取系统状态"""
    try:
        services_status = await system.task_scheduler.get_all_services_status()
        browser_status = "运行中" if browser_manager.is_running else "未运行"
        user_stats = data_storage.get_user_stats()
        
        return {
            "status": "success",
            "data": {
                "browser": browser_status,
                "services": services_status,
                "user_stats": user_stats
            }
        }
    except Exception as e:
        return {"status": "error", "message": f"获取系统状态失败: {str(e)}"}

@app.post("/api/users/mark_pending")
async def mark_users_pending(user_urls: str = ""):
    """将选中用户标记为 pending，便于按选择发送"""
    try:
        from utils.data_storage import data_storage
        urls = split_list(user_urls)  # 使用统一的字符串分割函数
        n = data_storage.mark_users_pending(urls)
        return {"status": "success", "message": f"已标记 {n} 个用户为待发送"}
    except Exception as e:
        return {"status": "error", "message": f"标记失败: {str(e)}"}

@app.get("/api/users")
async def get_users(limit: int = 0, sort_by: str = "time", dedup: int = 0):
    """获取用户列表（支持去重与全量）"""
    try:
        if dedup == 1:
            users = data_storage.get_users_dedup(limit=limit, sort_by=sort_by)
        else:
            users = data_storage.get_recent_users(limit, sort_by=sort_by)

        # 维持原有排序约定
        if sort_by == "ip":
            users.sort(key=lambda x: x.get('ip_location', ''))
        elif sort_by == "publish":
            users.sort(key=lambda x: x.get('comment_ts', 0), reverse=True)
        else:
            users.sort(key=lambda x: x.get('collected_time', ''), reverse=True)


        return {"status": "success", "data": users}
    except Exception as e:
        return {"status": "error", "message": f"获取用户列表失败: {str(e)}"}

@app.get("/api/videos")
async def get_videos(limit: int = 0, dedup: int = 0,sort_by: str = "time"):
	"""获取视频列表（支持去重与全量）"""
	try:
		if dedup == 1:
			videos = data_storage.get_videos_dedup_by_desc(limit=limit, sort_by=sort_by)
		else:
			videos = data_storage.get_recent_videos(limit, sort_by=sort_by)

		# 新增：按点赞数排序
		if sort_by == "like":
			videos.sort(key=lambda x: x.get("like_count", 0), reverse=True)

		return {"status": "success", "data": videos}
	except Exception as e:
		return {"status": "error", "message": f"获取视频列表失败: {str(e)}"}


@app.post("/api/videos/add_manual")
async def add_manual_video(
    video_url: str = Body(default=""),
    video_desc: str = Body(default=""),
    keyword: str = Body(default="手动添加"),
    author_name: str = Body(default=""),
    like_count: int = Body(default=0),
    publish_time: str = Body(default="")
):
    """手动添加视频到视频数据库"""
    try:
        raw_url = (video_url or "").strip()
        if not raw_url:
            return {"status": "error", "message": "缺少 video_url"}

        # 统一标准化链接，避免变成 http://127.0.0.1/... 这样的相对路径
        # 只要不是 http 开头，就自动补成 https://... 的绝对地址
        if not raw_url.lower().startswith("http"):
            # 去掉前导斜杠，防止出现 /www.douyin.com/... 这种形式
            raw_url = raw_url.lstrip("/")

            # 常见输入形式：
            # - www.douyin.com/video/xxxx
            # - douyin.com/video/xxxx
            # - v.douyin.com/xxxx
            # - video/xxxx（只填了路径）
            if raw_url.startswith(("www.douyin.com", "douyin.com", "v.douyin.com")):
                video_url_norm = "https://" + raw_url
            elif raw_url.startswith("video/"):
                video_url_norm = "https://www.douyin.com/" + raw_url
            else:
                # 其他情况统一补 https://，保证一定是绝对链接
                video_url_norm = "https://" + raw_url
        else:
            video_url_norm = raw_url

        # 标准化 video 字典（与采集逻辑保持一致）
        video = {
            "video_url": video_url_norm,
            "video_desc": (video_desc or "").strip() or "手动添加视频",
            "keyword": (keyword or "").strip() or "手动添加",
            "author_name": (author_name or "").strip() or "未知作者",
            "author_url": "",  # 手动添加无法确定主页
            "like_count": int(like_count) if like_count else 0,
            "publish_time": (publish_time or "").strip() or "",
            "publish_ts": 0,
            "collected_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # 写入数据库
        data_storage.save_video(video)

        return {"status": "success", "message": "视频已添加", "data": video}

    except Exception as e:
        return {"status": "error", "message": f"添加失败：{e}"}

# app.py  第 525 行开始
@app.post("/api/videos/enrich_details")
async def api_enrich_video_details(payload: Dict[str, Any] = Body(...)):
    video_urls = payload.get("video_urls") or []
    if not isinstance(video_urls, list):
        return json_response("error", "video_urls 必须是数组")

    clean_urls = [str(u).strip() for u in video_urls if str(u).strip()]
    if not clean_urls:
        return json_response("error", "没有有效的视频链接")

    logger.info("🔍 触发视频详情采集任务, 数量=%d", len(clean_urls))

    # 确保浏览器运行
    if not await browser_manager.ensure_running():
        return json_response("error", "浏览器未启动")

    #system = system.task_scheduler.services.get
    svc = system.task_scheduler.services.get("CustomerAcquisitionService")


    updated = []

    for url in clean_urls:
        try:
            detail = await svc.enrich_video_detail(browser_manager, url)
            if detail:
                # 写入数据库
                data_storage.update_video(detail)
                updated.append(detail)
        except Exception as e:
            logger.info(f"❌ 采集失败 {url}: {e}")

    return json_response("success", f"已更新 {len(updated)} 条视频", updated)





@app.delete("/api/videos")
async def clear_videos(payload: dict = Body(...)):
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}
    """清除视频数据"""
    try:
        # 检查是否有服务在运行
        services_status = await system.task_scheduler.get_all_services_status()
        any_service_running = any(service.get('running', False) for service in services_status.values())
        if any_service_running:
            return {"status": "error", "message": "❌ 有服务正在运行，无法清除数据"}

        # 从 JSON Body 取参数（而不是使用默认查询参数）
        scope = payload.get("scope", "all")
        ids = payload.get("ids") or []
        days = int(payload.get("days", 7))

        result = data_storage.clear_videos(scope, ids, days)
        return {"status": "success", "message": f"✅ 已清除 {result} 条视频数据"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 清除视频数据失败: {str(e)}"}



@app.delete("/api/users")
async def clear_users(payload: dict = Body(...)):
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}
    """清除用户数据"""
    try:
        services_status = await system.task_scheduler.get_all_services_status()
        any_service_running = any(service.get('running', False) for service in services_status.values())
        if any_service_running:
            return {"status": "error", "message": "❌ 有服务正在运行，无法清除数据"}

        scope = payload.get("scope", "all")
        ids = payload.get("ids") or []
        days = int(payload.get("days", 7))
        allowed = {"all", "selected", "days", "sent", "unsent"}
        if scope not in allowed:
            return {"status": "error", "message": f"非法 scope: {scope}"}

        result = data_storage.clear_users(scope, ids, days)
        return {"status": "success", "message": f"✅ 已清除 {result} 条用户数据"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 清除用户数据失败: {str(e)}"}



@app.delete("/api/task_logs")
async def clear_task_logs(request: Request):
    if not lic_status().get("valid"):
        return {"status": "error", "message": "❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        services_status = await system.task_scheduler.get_all_services_status()
        if any(s.get("running", False) for s in services_status.values()):
            return {"status": "error", "message": "❌ 有服务正在运行，无法清除数据"}

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        scope = (payload.get("scope") or "all").strip()
        days  = int(payload.get("days") or 7)

        cnt = data_storage.clear_task_logs(scope=scope, days=days)
        return {"status": "success", "message": f"✅ 已清除 {cnt} 条任务日志"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 清除任务日志失败: {e}"}
# ---------------- Agent API 接口 ----------------

def _agent_route_value(route):
    """兼容 Enum 和字符串路线"""
    return route.value if hasattr(route, "value") else str(route or "")


def _load_agent_notes_for_run(session_id: int, limit: int = 80):
    """读取当前 Agent 会话绑定的小红书笔记，供个人账号运营路线分析"""
    rows = data_storage.get_agent_notes_for_analysis(session_id=session_id, limit=limit)
    notes = []

    for row in rows:
        video_url = str(row.get("video_url") or "")
        if video_url and "xiaohongshu.com" not in video_url:
            continue

        notes.append({
            "note_url": video_url,
            "video_url": video_url,
            "title": row.get("video_desc") or "",
            "video_desc": row.get("video_desc") or "",
            "author_name": row.get("author_name") or "",
            "author_url": row.get("author_url") or "",
            "like_count": int(row.get("like_count") or 0),
            "comment_count": int(row.get("comment_count") or 0),
            "collect_count": int(row.get("collect_count") or 0),
            "keyword": row.get("keyword") or "",
            "publish_time": row.get("publish_time") or "",
            "publish_ts": int(row.get("publish_ts") or 0),
            "collected_time": row.get("collected_time") or "",
            "content_text": row.get("content_text") or "",
            "raw_text": row.get("raw_text") or "",
            "tags_json": row.get("tags_json") or "",
            "image_count": int(row.get("image_count") or 0),
            "agent_session_id": int(row.get("agent_session_id") or 0),
            "project_id": int(row.get("project_id") or 0),
            "collection_batch_id": row.get("collection_batch_id") or "",
        })

    return notes


def _load_agent_comments_for_run(limit: int = 200):
    """读取小红书评论用户数据，供 Agent 获客路线分析"""
    rows = data_storage.get_recent_users(limit=limit, sort_by="time")
    comments = []

    for row in rows:
        video_url = str(row.get("video_url") or "")
        user_url = str(row.get("user_url") or "")

        if video_url and "xiaohongshu.com" not in video_url and "xiaohongshu.com" not in user_url:
            continue

        comments.append({
            "video_url": video_url,
            "user_url": user_url,
            "username": row.get("username") or "",
            "comment_text": row.get("comment_text") or "",
            "ip_location": row.get("ip_location") or "",
            "video_desc": row.get("video_desc") or "",
            "matched_keyword": row.get("matched_keyword") or "",
            "comment_time": row.get("comment_time") or "",
            "comment_ts": int(row.get("comment_ts") or 0),
            "collected_time": row.get("collected_time") or "",
        })

    return comments


@app.post("/api/agent/start")
async def api_agent_start(request: AgentStartRequest):
    """启动 Agent 会话：用户选择路线并输入一句业务描述"""
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        service = get_ai_agent_service()
        result = await service.start_session(
            route=_agent_route_value(request.route),
            user_input=request.user_input
        )
        await manager.broadcast({"type": "operation", "msg": "🤖 Agent 会话已创建"})
        return result
    except Exception as e:
        return {"status": "error", "message": f"❌ Agent 启动失败: {e}"}


@app.post("/api/agent/answer")
async def api_agent_answer(request: AgentAnswerRequest):
    """提交 Agent 反问答案"""
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        service = get_ai_agent_service()
        result = await service.answer_clarifying_questions(
            session_id=request.session_id,
            answers=request.answers
        )
        await manager.broadcast({"type": "operation", "msg": f"🤖 Agent 已接收反问答案，session_id={request.session_id}"})
        return result
    except Exception as e:
        return {"status": "error", "message": f"❌ Agent 反问答案提交失败: {e}"}


@app.post("/api/agent/run")
async def api_agent_run(request: AgentRunRequest):
    """执行 Agent 分析：读取已采集的小红书数据并生成报告"""
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        service = get_ai_agent_service()
        session = service.get_session(request.session_id)

        if not session:
            return {"status": "error", "message": "Agent 会话不存在"}

        route = session.get("route") or ""

        if route == "customer_acquisition":
            notes = _load_agent_notes_for_run(session_id=request.session_id, limit=80)
            comments = _load_agent_comments_for_run(limit=200)
        else:
            notes = _load_agent_notes_for_run(session_id=request.session_id, limit=80)
            comments = []

        result = await service.run(
            session_id=request.session_id,
            notes=notes,
            comments=comments
        )

        await manager.broadcast({"type": "operation", "msg": f"🤖 Agent 执行完成，session_id={request.session_id}"})
        return result
    except Exception as e:
        return {"status": "error", "message": f"❌ Agent 执行失败: {e}"}


@app.get("/api/agent/session/{session_id}")
async def api_agent_session(session_id: int):
    """读取 Agent 会话状态"""
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        service = get_ai_agent_service()
        data = service.get_session(session_id)

        if not data:
            return {"status": "error", "message": "Agent 会话不存在"}

        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": f"❌ 读取 Agent 会话失败: {e}"}


@app.get("/api/agent/report/{session_id}")
async def api_agent_report(session_id: int):
    """读取 Agent 报告"""
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        service = get_ai_agent_service()
        data = service.get_report(session_id)

        if not data:
            return {"status": "error", "message": "Agent 报告不存在"}

        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": f"❌ 读取 Agent 报告失败: {e}"}


@app.get("/api/agent/candidate_terms")
async def api_agent_candidate_terms(
    review_status: str = "pending",
    project_id: int = 0,
    limit: int = 100,
):
    """读取 Agent 候选库列表"""
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        review_status = str(review_status or "pending").strip()
        if review_status not in {"pending", "approved", "rejected"}:
            return {"status": "error", "message": "review_status 只能是 pending、approved、rejected"}

        project_filter = int(project_id or 0)
        data = data_storage.get_candidate_terms(
            review_status=review_status,
            project_id=project_filter if project_filter > 0 else None,
            limit=int(limit or 100)
        )

        for item in data:
            item["evidence_samples"] = _agent_json_loads(item.get("evidence_samples_json"))

        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": f"❌ 读取候选库失败: {e}"}


@app.post("/api/agent/candidate_terms/approve")
async def api_agent_candidate_term_approve(payload: dict = Body(default=None)):
    """人工通过候选词，写入正式库"""
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        payload = payload or {}
        candidate_id = int(payload.get("candidate_id") or 0)
        mapping = payload.get("mapping")

        if candidate_id <= 0:
            return {"status": "error", "message": "缺少 candidate_id"}

        candidate = _get_agent_candidate_term_by_id(candidate_id)
        if not candidate:
            return {"status": "error", "message": "候选项不存在"}

        if candidate.get("review_status") != "pending":
            return {"status": "error", "message": "只有 pending 状态的候选项可以通过"}

        if int(candidate.get("evidence_count") or 0) < 2:
            return {"status": "error", "message": "候选项证据数量少于 2，不能进入正式库"}

        success = data_storage.approve_candidate_term(candidate_id, mapping=mapping)
        if not success:
            return {"status": "error", "message": "候选项通过失败"}

        approved_terms = data_storage.get_approved_terms(
            project_id=int(candidate.get("project_id") or 0) if int(candidate.get("project_id") or 0) > 0 else None,
            limit=50
        )
        await manager.broadcast({"type": "operation", "msg": f"🧠 候选项已通过：{candidate.get('term') or ''}"})
        return {"status": "success", "message": "候选项已通过并写入正式库", "data": approved_terms}
    except Exception as e:
        return {"status": "error", "message": f"❌ 通过候选项失败: {e}"}


@app.post("/api/agent/candidate_terms/reject")
async def api_agent_candidate_term_reject(payload: dict = Body(default=None)):
    """人工拒绝候选词"""
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        payload = payload or {}
        candidate_id = int(payload.get("candidate_id") or 0)

        if candidate_id <= 0:
            return {"status": "error", "message": "缺少 candidate_id"}

        candidate = _get_agent_candidate_term_by_id(candidate_id)
        if not candidate:
            return {"status": "error", "message": "候选项不存在"}

        if candidate.get("review_status") != "pending":
            return {"status": "error", "message": "只有 pending 状态的候选项可以拒绝"}

        success = data_storage.reject_candidate_term(candidate_id)
        if not success:
            return {"status": "error", "message": "候选项拒绝失败"}

        await manager.broadcast({"type": "operation", "msg": f"🧠 候选项已拒绝：{candidate.get('term') or ''}"})
        return {"status": "success", "message": "候选项已拒绝"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 拒绝候选项失败: {e}"}


@app.get("/api/agent/approved_terms")
async def api_agent_approved_terms(project_id: int = 0, limit: int = 200):
    """读取人工审核通过后的正式库"""
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        project_filter = int(project_id or 0)
        data = data_storage.get_approved_terms(
            project_id=project_filter if project_filter > 0 else None,
            limit=int(limit or 200)
        )

        for item in data:
            item["mapping"] = _agent_json_loads(item.get("mapping_json"))

        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": f"❌ 读取正式库失败: {e}"}


@app.get("/api/agent/project_memory")
async def api_agent_project_memory(project_id: int, limit: int = 100):
    """读取项目记忆"""
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        project_id = int(project_id or 0)
        if project_id <= 0:
            return {"status": "error", "message": "缺少 project_id"}

        data = data_storage.get_project_memory(project_id=project_id, limit=int(limit or 100))
        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": f"❌ 读取项目记忆失败: {e}"}


@app.post("/api/agent/project_memory")
async def api_agent_project_memory_save(payload: dict = Body(default=None)):
    """保存项目记忆"""
    if not lic_status().get("valid"):
        return {"status":"error","message":"❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        payload = payload or {}
        project_id = int(payload.get("project_id") or 0)
        memory_type = str(payload.get("memory_type") or "").strip()
        content = str(payload.get("content") or "").strip()
        source_session_id = payload.get("source_session_id")

        allowed_memory_types = {
            "preferred_title_style",
            "rejected_content_direction",
            "account_style",
            "accepted_selling_point",
            "manual_note",
        }

        if project_id <= 0:
            return {"status": "error", "message": "缺少 project_id"}

        if memory_type not in allowed_memory_types:
            return {"status": "error", "message": "memory_type 不合法"}

        if not content:
            return {"status": "error", "message": "项目记忆内容不能为空"}

        if source_session_id is not None and str(source_session_id).strip():
            source_session_id = int(source_session_id)
        else:
            source_session_id = None

        memory_id = data_storage.save_project_memory(
            project_id=project_id,
            memory_type=memory_type,
            content=content,
            source_session_id=source_session_id
        )

        if memory_id <= 0:
            return {"status": "error", "message": "项目记忆保存失败"}

        data = data_storage.get_project_memory(project_id=project_id, limit=100)
        await manager.broadcast({"type": "operation", "msg": f"🧠 项目记忆已保存：{memory_type}"})
        return {"status": "success", "message": "项目记忆已保存", "memory_id": memory_id, "data": data}
    except Exception as e:
        return {"status": "error", "message": f"❌ 保存项目记忆失败: {e}"}


def _get_agent_candidate_term_by_id(candidate_id: int) -> Dict[str, Any]:
    """按 ID 读取候选项"""
    try:
        conn = data_storage.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM candidate_terms WHERE id = ? LIMIT 1", (int(candidate_id),))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception as e:
        print(f"读取候选项失败: {e}")
        return {}


def _agent_json_loads(text):
    """安全解析 Agent JSON 字段"""
    if isinstance(text, (dict, list)):
        return text

    if not text:
        return []

    try:
        return json.loads(text)
    except Exception:
        return []

# WebSocket路由
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket连接"""
    await manager.connect(websocket)
    try:
        # 发送连接成功消息
        await manager.send_personal_message({
            "type": "connected",
            "msg": "✅ 已连接到系统"
        }, websocket)
        
        # 保持连接
        while True:
            data = await websocket.receive_text()
            # 可以处理客户端发送的消息
            try:
                message = json.loads(data)
                if message.get("type") == "ping":
                    await manager.send_personal_message({"type": "pong"}, websocket)
            except:
                pass
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket错误: {e}")
        manager.disconnect(websocket)
# —— 账号列表本地缓存文件（与 exe 同层）——
ACCOUNTS_FILE = Path("profiles.json")

def _read_saved_profiles() -> list[str]:
    try:
        if ACCOUNTS_FILE.exists():
            data = json.loads(ACCOUNTS_FILE.read_text("utf-8"))
            return [str(x).strip() for x in (data or []) if str(x).strip()]
    except Exception:
        pass
    return []

def _write_saved_profiles(items: list[str]) -> None:
    ACCOUNTS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), "utf-8")
# —— 账号根目录默认位置（与 exe 同层的 DouyinProfiles）——
DEFAULT_PROFILES_ROOT = Path.cwd() / "DouyinProfiles"

@app.get("/api/accounts/default_root")
async def api_accounts_default_root():
    """
    返回并确保创建默认账号根目录（exe 同层 DouyinProfiles）
    """
    try:
        DEFAULT_PROFILES_ROOT.mkdir(parents=True, exist_ok=True)
        return {"status": "success", "data": str(DEFAULT_PROFILES_ROOT.resolve())}
    except Exception as e:
        return {"status": "error", "message": f"创建默认根目录失败: {e}"}


@app.get("/api/accounts/saved")
async def api_accounts_saved():
    """读取已保存账号列表（每行一个绝对目录）"""
    return {"status": "success", "data": _read_saved_profiles()}

@app.post("/api/accounts/save")
async def api_accounts_save(payload: dict = Body(default=None)):
    """保存账号列表（前端传来多行文本拆分）"""
    try:
        items = payload.get("profiles", []) if payload else []
        items = [str(x).strip() for x in items if str(x).strip()]
        _write_saved_profiles(items)
        return {"status": "success", "message": f"已保存 {len(items)} 个账号目录"}
    except Exception as e:
        return {"status": "error", "message": f"保存失败: {e}"}

@app.get("/api/accounts/list")
async def api_accounts_list(root: str = ""):
    """
    扫描指定根目录下的一级子目录，作为可用账号目录返回。
    仅扫描到第一层，不做深层/文件校验，保持简单可控。
    """
    try:
        base = Path(root.strip()) if root.strip() else Path.cwd()
        if not base.exists() or not base.is_dir():
            return {"status": "error", "message": f"目录不存在: {base}"}
        # 仅返回一级子目录（绝对路径）
        dirs = [str(p.resolve()) for p in sorted(base.iterdir()) if p.is_dir()]
        return {"status": "success", "data": dirs}
    except Exception as e:
        return {"status": "error", "message": f"扫描失败: {e}"}
@app.post("/api/accounts/open")
async def api_accounts_open(payload: dict = Body(default=None)):
    """
    用指定 user_data_dir 打开浏览器（若目录未登录，将出现扫码页）
    要求：已授权；dir 为绝对路径
    """
    if not lic_status().get("valid"):
        return {"status": "error", "message": "❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        dir_path = (payload or {}).get("dir", "").strip()
        if not dir_path:
            return {"status": "error", "message": "缺少目录参数 dir"}

        p = Path(dir_path)
        if not p.exists() or not p.is_dir():
            return {"status": "error", "message": f"目录不存在或不可用: {dir_path}"}

        ok = await browser_manager.switch_profile(str(p.resolve()))
        if ok:
            return {"status": "success", "message": f"🔑 已用该目录启动浏览器，请在新窗口扫码：{p.resolve()}"}
        else:
            return {"status": "error", "message": f"❌ 启动失败（目录无效或浏览器异常）：{p.resolve()}"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 执行异常：{e}"}
@app.post("/api/accounts/create")
async def api_accounts_create(payload: dict = Body(default=None)):
    """
    在给定根目录下自动创建下一个账号目录（acc001/acc002/...），
    若未提供 root，则使用默认根目录（exe 同层 DouyinProfiles）；
    创建后写入 profiles.json，并立即用该目录启动浏览器到扫码页。
    """
    if not lic_status().get("valid"):
        return {"status": "error", "message": "❌ 未授权或已过期，请先在页面输入许可证密钥"}

    try:
        root = (payload or {}).get("root", "").strip()
        prefix = (payload or {}).get("prefix", "acc").strip() or "acc"

        # 未提供 root 时使用默认根目录
        base = Path(root) if root else DEFAULT_PROFILES_ROOT

        # 确保根目录存在
        base.mkdir(parents=True, exist_ok=True)
        if not base.is_dir():
            return {"status": "error", "message": f"root 非目录: {base}"}

        # 计算下一个 accXXX
        maxn = 0
        for p in base.iterdir():
            if p.is_dir():
                m = re.match(rf"^{re.escape(prefix)}(\d+)$", p.name, flags=re.IGNORECASE)
                if m:
                    maxn = max(maxn, int(m.group(1)))

        new_name = f"{prefix}{maxn+1:03d}"
        new_dir = base / new_name
        # 防御：极小概率重名，再递增一次
        while new_dir.exists():
            maxn += 1
            new_name = f"{prefix}{maxn:03d}"
            new_dir = base / new_name
        new_dir.mkdir(parents=True, exist_ok=True)

        # 更新保存列表
        items = _read_saved_profiles()
        new_path = str(new_dir.resolve())
        if new_path not in items:
            items.append(new_path)
            _write_saved_profiles(items)

        # 立即启动到扫码页
        ok = await browser_manager.switch_profile(new_path)
        if ok:
            return {"status": "success", "dir": new_path, "message": f"✅ 已创建并打开：{new_path}，请在新窗口扫码"}
        else:
            return {"status": "error", "message": f"目录已创建，但打开浏览器失败：{new_path}"}
    except Exception as e:
        return {"status": "error", "message": f"❌ 创建失败：{e}"}

# 启动函数
def start_web_server():
    """启动Web服务器"""
    print("🚀 启动小红书自动化系统 Web 服务器...")
    print("📊 访问地址: http://127.0.0.1:17866")
    uvicorn.run("app:app", host="0.0.0.0", port=17866, reload=False)

if __name__ == "__main__":
    start_web_server()