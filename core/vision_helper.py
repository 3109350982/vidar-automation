"""
视觉辅助工具类 - 全新版本：DOM优先 + 锚点法 + 稳定ROI + 双引擎匹配
"""
import cv2
import numpy as np
import asyncio
import time
import random
import os
import math
from core.config_manager import config_manager

class VisionHelper:
    """视觉辅助工具类 - 全新版本：双引擎匹配 + 安全区域"""

    def __init__(self, page=None, screen_width=None, screen_height=None, debug_mode=True, debug_dir="./debug/vision"):
        # 获取浏览器视窗大小作为屏幕大小
        browser_cfg = config_manager.get('browser', {})
        viewport = browser_cfg.get('viewport', {'width': 1366, 'height': 768})
        
        self.screen_width = screen_width or viewport.get('width', 1366)
        self.screen_height = screen_height or viewport.get('height', 768)
        self.page = page
        self.debug_mode = debug_mode
        self.debug_dir = debug_dir
        self.templates_dir = "templates"
        
        # 确保目录存在
        os.makedirs(self.debug_dir, exist_ok=True)
        os.makedirs(self.templates_dir, exist_ok=True)
        
        print(f"🖥️ 视觉助手初始化完成，页面分辨率: {self.screen_width}x{self.screen_height}")

    # -------- 基础工具 --------
    def normalize_region(self, region):
        """把 (x,y,w,h) / (x1,y1,x2,y2) / 百分比 统一成 xywh。"""
        if not region:
            return None
            
        x, y, a, b = region
        W, H = self.screen_width, self.screen_height
        
        # 百分比
        if 0 <= x <= 1 and 0 <= y <= 1 and 0 <= a <= 1 and 0 <= b <= 1:
            x1, y1 = int(x * W), int(y * H)
            x2, y2 = int(a * W), int(b * H)
            return (x1, y1, max(1, x2 - x1), max(1, y2 - y1))
        
        # 两点矩形
        if a > x and b > y and a <= W and b <= H:
            return (int(x), int(y), int(a - x), int(b - y))
        
        # 默认 xywh
        return (int(x), int(y), int(a), int(b))

    def get_top_actionbar_roi(self):
        """抖音用户页顶部操作区（头像与'关注/私信'一行）——固定 ROI。"""
        W, H = self.screen_width, self.screen_height
        x = int(W * 0.34)
        y = int(H * 0.11)
        w = int(W * 0.44)
        h = int(H * 0.14)
        return (x, y, w, h)

    async def take_screenshot(self, region=None):
        """返回 BGR numpy 图像。"""
        try:
            if not self.page:
                from core.browser_manager import browser_manager
                self.page = browser_manager.page
                
            if region:
                x, y, w, h = self.normalize_region(region)
                # ROI 边界保护
                x = max(0, min(self.screen_width - 1, x))
                y = max(0, min(self.screen_height - 1, y))
                w = max(8, min(self.screen_width - x, w))
                h = max(8, min(self.screen_height - y, h))
                clip = {'x': x, 'y': y, 'width': w, 'height': h}
            else:
                clip = None
                
            buf = await self.page.screenshot(clip=clip, type="png")
            img = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
            return img
        except Exception as e:
            print(f"截图失败: {e}")
            return None

    def _load_template(self, element_type):
        """从 templates 目录加载模板 PNG。"""
        mapping = {
            'message_button': 'message_button.png',
            'follow_button': 'follow_button.png',
            'send_button': 'send_button.png',
            'message_input': 'message_input.png',
        }
        filename = mapping.get(element_type)
        if not filename:
            return None
            
        path = os.path.join(self.templates_dir, filename)
        if not os.path.exists(path):
            print(f"❌ 模板文件不存在: {path}")
            return None
            
        template = cv2.imread(path, cv2.IMREAD_COLOR)
        if template is None:
            print(f"❌ 无法加载模板: {path}")
            return None
            
        print(f"✅ 加载模板: {element_type} -> {template.shape}")
        return template

    def _save_debug(self, name, img):
        """保存调试图像"""
        if not self.debug_mode:
            return
        try:
            p = os.path.join(self.debug_dir, f"{name}_{int(time.time())}.png")
            cv2.imwrite(p, img)
            print(f"📸 保存调试图像: {p}")
        except Exception as e:
            print(f"保存调试图像失败: {e}")

    # -------- 引擎1：边缘 NCC 多尺度（安全）--------
    def _safe_multi_scale_match_edge(self, roi_bgr, tpl_bgr,
                                     min_scale=0.6, max_scale=1.6, step=0.08, thresh=0.74):
        """
        先 Canny 得到边缘，再 NCC；严格限制 scaled_tpl <= ROI，避免断言。
        返回 (score, (x,y), scale) 或 None
        """
        if roi_bgr is None or tpl_bgr is None:
            return None
            
        rh, rw = roi_bgr.shape[:2]
        th, tw = tpl_bgr.shape[:2]
        
        if min(rh, rw, th, tw) < 8:
            return None

        # 转换为灰度并提取边缘
        roi_gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
        tpl_gray = cv2.cvtColor(tpl_bgr, cv2.COLOR_BGR2GRAY)
        roi_ed = cv2.Canny(roi_gray, 60, 120)
        tpl_ed = cv2.Canny(tpl_gray, 60, 120)
        roi_ed = cv2.dilate(roi_ed, np.ones((3, 3), np.uint8), iterations=1)

        # 计算允许的最大缩放比例
        max_scale_allowed = min(rw / tw, rh / th)
        if max_scale_allowed <= 0:
            return None
            
        hi = min(max_scale, max_scale_allowed)
        lo = min_scale
        
        if hi < lo * 0.95:
            return None

        best = (-1, None, None)
        s = hi
        
        while s >= lo - 1e-6:
            ws, hs = int(tw * s), int(th * s)
            if ws < 8 or hs < 8 or ws > rw or hs > rh:
                s -= step
                continue
                
            tpl_s = cv2.resize(tpl_ed, (ws, hs), interpolation=cv2.INTER_AREA)
            try:
                res = cv2.matchTemplate(roi_ed, tpl_s, cv2.TM_CCOEFF_NORMED)
            except cv2.error as e:
                s -= step
                continue
                
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
            if max_val > best[0]:
                best = (max_val, max_loc, s)
                
            s -= step

        if best[0] >= thresh:
            print(f"🎯 边缘NCC匹配: score={best[0]:.3f}, scale={best[2]:.2f}")
            return best
            
        return None

    # -------- 引擎2：ORB 特征匹配 + 单应性 --------
    def _feature_match_orb(self, roi_bgr, tpl_bgr, score_thresh=0.15, min_inliers=8):
        """
        返回 (score, rect_points) 或 None
        score = inliers / keypoints_tpl
        """
        if roi_bgr is None or tpl_bgr is None:
            return None
            
        # 创建ORB检测器
        orb = cv2.ORB_create(nfeatures=800)
        
        # 检测关键点和描述符
        kp1, des1 = orb.detectAndCompute(cv2.cvtColor(tpl_bgr, cv2.COLOR_BGR2GRAY), None)
        kp2, des2 = orb.detectAndCompute(cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY), None)
        
        if des1 is None or des2 is None or len(kp1) < 6 or len(kp2) < 6:
            return None

        # 特征匹配
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        matches = bf.knnMatch(des1, des2, k=2)
        
        # 应用比值测试
        good = []
        for m, n in matches:
            if m.distance < 0.72 * n.distance:
                good.append(m)
                
        if len(good) < min_inliers:
            return None

        # 计算单应性矩阵
        src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        
        H, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 4.0)
        if H is None:
            return None
            
        inliers = int(mask.sum())
        if inliers < min_inliers:
            return None

        # 计算模板在ROI中的投影
        h, w = tpl_bgr.shape[:2]
        rect = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        proj = cv2.perspectiveTransform(rect, H).reshape(-1, 2)
        
        score = inliers / max(len(kp1), 1)
        if score < score_thresh:
            return None
            
        print(f"🎯 ORB特征匹配: inliers={inliers}/{len(kp1)}, score={score:.3f}")
        return (score, proj)

    # -------- 统一入口：在 ROI 内点击 element_type --------
    async def click_element_in_region(self, element_type, region, confidence=0.74, allow_scroll=False):
        """
        不滚动、不扩视图；在给定 ROI 内做双引擎匹配，任一命中即点击。
        """
        print(f"🎯 开始视觉定位: {element_type}, ROI={region}")
        
        # 归一化区域
        region_normalized = self.normalize_region(region)
        if not region_normalized:
            print(f"❌ 无效区域: {region}")
            return False
            
        x, y, w, h = region_normalized
        
        # ROI 边界保护
        x = max(0, min(self.screen_width - 1, x))
        y = max(0, min(self.screen_height - 1, y))
        w = max(8, min(self.screen_width - x, w))
        h = max(8, min(self.screen_height - y, h))
        region_final = (x, y, w, h)

        # 截取ROI
        roi = await self.take_screenshot(region_final)
        if roi is None:
            print(f"❌ 无法截取ROI: {region_final}")
            return False

        # 加载模板
        tpl = self._load_template(element_type)
        if tpl is None:
            print(f"❌ 无法加载模板: {element_type}")
            return False

        print(f"📊 ROI尺寸: {roi.shape}, 模板尺寸: {tpl.shape}")

        # 检查模板尺寸，必要时轻微扩展ROI
        rh, rw = roi.shape[:2]
        th, tw = tpl.shape[:2]
        
        if tw > rw or th > rh:
            print("🔄 模板大于ROI，尝试轻微扩展ROI...")
            padX = int(self.screen_width * 0.05)
            padY = int(self.screen_height * 0.04)
            region_expanded = (
                max(0, x - padX), 
                max(0, y - padY),
                min(self.screen_width - (x - padX), w + 2 * padX),
                min(self.screen_height - (y - padY), h + 2 * padY)
            )
            roi = await self.take_screenshot(region_expanded)
            if roi is None:
                print("❌ 扩展ROI后截图失败")
                return False
            region_final = region_expanded
            rh, rw = roi.shape[:2]
            print(f"📊 扩展后ROI尺寸: {roi.shape}")

        # 引擎1：边缘 NCC
        edge_result = self._safe_multi_scale_match_edge(roi, tpl, thresh=confidence)
        if edge_result:
            score, (ox, oy), scale = edge_result
            cx = region_final[0] + ox + int((tpl.shape[1] * scale) / 2)
            cy = region_final[1] + oy + int((tpl.shape[0] * scale) / 2)
            await self._click_at(cx, cy, element_type)
            return True

        # 引擎2：ORB 特征
        orb_result = self._feature_match_orb(roi, tpl)
        if orb_result:
            score, quad = orb_result
            cx = int(region_final[0] + np.mean(quad[:, 0]))
            cy = int(region_final[1] + np.mean(quad[:, 1]))
            await self._click_at(cx, cy, element_type)
            return True

        # 调试输出
        print(f"❌ 双引擎匹配失败: {element_type}")
        self._save_debug(f"fail_roi_{element_type}", roi)
        return False

    async def _click_at(self, x, y, element_type):
        """执行点击操作"""
        try:
            if not self.page:
                from core.browser_manager import browser_manager
                self.page = browser_manager.page
                
            # 确保坐标在页面范围内
            x = max(0, min(self.screen_width - 1, x))
            y = max(0, min(self.screen_height - 1, y))
            
            await self.page.mouse.move(x, y)
            await asyncio.sleep(0.1)  # 微小延迟，模拟人类
            await self.page.mouse.click(x, y)
            
            print(f"🖱️ 视觉点击成功: {element_type} 在 ({x}, {y})")
            return True
        except Exception as e:
            print(f"❌ 视觉点击失败: {element_type} - {e}")
            return False

    # -------- 向后兼容的旧方法 --------
    async def locate_element(self, element_type, confidence=0.7, region=None):
        """向后兼容的定位方法"""
        if not region:
            region = self.get_top_actionbar_roi()
        return await self.click_element_in_region(element_type, region, confidence)

    async def click_element(self, element_type, confidence=0.7, region=None, human_like=True):
        """向后兼容的点击方法"""
        if not region:
            region = self.get_top_actionbar_roi()
        return await self.click_element_in_region(element_type, region, confidence)

# 全局视觉助手实例
vision_helper = None

def get_vision_helper(page=None):
    """获取视觉助手实例"""
    global vision_helper
    if vision_helper is None:
        vision_helper = VisionHelper(page=page)
    elif page and not vision_helper.page:
        vision_helper.page = page
    return vision_helper