"""
小红书评论服务：对勾选的视频/笔记页面自动发表评论
"""
import asyncio
import random
import hashlib
from urllib.parse import urlparse
from services.base_service import BaseService
from utils.data_storage import data_storage
from utils.advanced_anti_detection import anti_detection

class XhsCommentService(BaseService):
    """小红书评论服务"""

    async def execute(self, **kwargs):
        urls = kwargs.get("urls") or []
        text = kwargs.get("text") or ""
        templates = kwargs.get("templates") or []
        per_note = int(kwargs.get("per_note") or 1)
        interval_min_ms = int(kwargs.get("interval_min_ms") or 3)
        interval_max_ms = int(kwargs.get("interval_max_ms") or 4)
        skip_if_commented = bool(kwargs.get("skip_if_commented") or False)
        variant = bool(kwargs.get("variant") or False)

        if not urls:
            await self._emit_event("warning", "⚠️ 没有可评论的URL")
            return

        ok_count = 0
        skip_count = 0
        fail_count = 0
        total_commented = 0  # 全局计数，用于智能休息

        # 确保浏览器与页面就绪（必须在任何 page 操作之前）
        await self._ensure_browser_ready()
        mgr = await self._get_browser_manager()
        page = getattr(mgr, "page", None)
        if page is None:
            await self._emit_event("error", "❌ 浏览器页面未就绪（page=None）")
            return

        for url in urls:
            if await self._check_stop():
                break

            if "xiaohongshu.com" not in urlparse(url).netloc:
                await self._emit_event("warning", f"⏭ 跳过非小红书链接：{url}")
                continue

            # 选择本次文本
            base_text = text.strip()
            if not base_text and templates:
                base_text = random.choice([t.strip() for t in templates if str(t).strip()])
            if not base_text:
                await self._emit_event("error", "❌ 评论文本为空，已中止")
                return

            final_text = self._make_variant(base_text) if variant else base_text
            text_hash = hashlib.sha1(final_text.encode("utf-8")).hexdigest()

            # 去重：读库检查
            if skip_if_commented:
                try:
                    conn = data_storage.get_connection()
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT last_commented_hash FROM videos WHERE video_url=? ORDER BY id DESC LIMIT 1",
                        (url,),
                    )
                    row = cur.fetchone()
                    conn.close()
                    if row and row[0] and str(row[0]) == text_hash:
                        skip_count += 1
                        await self._emit_event("operation", f"⏭ 已发过相同文本，跳过：{url}")
                        continue
                except Exception as e:
                    await self._emit_event("warning", f"⚠️ 去重检查失败：{e}")

            try:
                await self._emit_event("operation", f"➡️ 打开：{url}")
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)

                # 打开笔记后先模拟阅读 3~8 秒，避免一打开就立刻评论
                await anti_detection.smart_read(3.0, 8.0)

                # 轻微滚动唤醒评论交互条（使用随机滚动距离，而不是固定 400）
                for _ in range(3):
                    try:
                        await anti_detection.random_scroll(page, 260, 780)
                    except Exception:
                        await page.mouse.wheel(0, random.randint(320, 860))
                    await asyncio.sleep(0.2)

                # 若存在“说点什么…”占位，先点它以激活编辑器（用 bounding box 点中心，避免误点点赞）
                try:
                    placeholder = page.locator('div.interactions.engage-bar >> text=说点什么').first
                    if await placeholder.count() > 0:
                        try:
                            await placeholder.scroll_into_view_if_needed()
                        except Exception:
                            pass
                        box = await placeholder.bounding_box()
                        if box:
                        # 在点击前将鼠标移到评论框位置悬停片刻
                            hover_x = box["x"] + box["width"] * 0.5 + random.uniform(-10, 10)
                            hover_y = box["y"] + box["height"] * 0.5 + random.uniform(-4, 4)
                            await anti_detection.human_like_move(hover_x, hover_y)
                            await asyncio.sleep(random.uniform(0.15, 0.30))

                            # 使用人类化 bbox 点击，增加随机偏移
                            await anti_detection.human_like_click_bbox(box, element_type="xhs_placeholder")
                            await asyncio.sleep(0.2)
                        else:
                            await placeholder.click()
                            await asyncio.sleep(0.2)
                except Exception:
                    pass

                # 聚焦输入并输入文本
                # 聚焦输入并输入文本（可见 + 点击 + 聚焦校验）
                input_locator = page.locator(
                    '#content-textarea[contenteditable="true"], .interactions.engage-bar .content-edit [contenteditable="true"], .interactions.engage-bar [contenteditable="true"]'
                ).first
                await input_locator.wait_for(state="visible", timeout=8000)
                try:
                    await input_locator.scroll_into_view_if_needed()
                except Exception:
                    pass

                # 先用 bounding box 点击到真正的可编辑节点中心（带随机偏移）
                box = await input_locator.bounding_box()
                if box:
                    await anti_detection.human_like_click_bbox(box, element_type="xhs_comment_input")
                else:
                    await input_locator.click()

                # 校验是否已聚焦；未聚焦则 JS 设置光标到末尾再聚焦
                el = await input_locator.element_handle()
                is_focused = False
                if el:
                    is_focused = await page.evaluate(
                        """(node) => !!node && (node === document.activeElement || node.contains(document.activeElement))""",
                        el
                    )
                    if not is_focused:
                        await page.evaluate("""(node)=>{
                            node.focus();
                            const sel = window.getSelection();
                            const range = document.createRange();
                            range.selectNodeContents(node);
                            range.collapse(false);
                            sel.removeAllRanges();
                            sel.addRange(range);
                        }""", el)

                await anti_detection.human_like_delay(0.15, 0.30, 'focus_input')
                type_delay = random.randint(18, 35)
                try:
                    await input_locator.type(final_text, delay=type_delay)   # 触发完整键盘事件链
                except Exception:
                    # 极端兜底：用键盘直打
                    await page.keyboard.type(final_text, delay=type_delay)
                await anti_detection.human_like_delay(0.12, 0.25, 'after_input')

                # 先用回车提交
                await page.keyboard.press("Enter")

                # 短等 5 秒确认是否已出现本次评论（兼容新版 comments-el 容器）
                ok = False
                try:
                    ok = await page.wait_for_function(
                        """(t) => {
                            const root =
                            document.querySelector('div.comments-el .comments-container [name="list"].list-container') ||
                            document.querySelector('div.comments-container [name="list"].list-container') ||
                            document.querySelector('.comment-list, .comment-container, [class*="comment-list"]');
                            if (!root) return false;
                            const items = Array.from(root.querySelectorAll(
                            '.comment-item, .list-item, [class*="comment-item"], li[class*="comment"]'
                            ));
                            return items.some(n => ((n.innerText || '')).includes(t));
                        }""",
                        arg=final_text,
                        timeout=5000
                    )
                except Exception:
                    ok = False

                if not ok:
                    # 回车未成功则点击发送按钮兜底
                    send_btn = page.locator(
                        '.interactions.engage-bar .interact-container button, .interactions.engage-bar .bottom button'
                    ).first
                    await send_btn.wait_for(state="visible", timeout=2000)
                    await send_btn.click()

                    # 再次等待成功（8 秒）
                    ok = await page.wait_for_function(
                        """(t) => {
                            const list = document.querySelector('.comment-list, .comment-container, [class*="comment-list"]');
                            if (!list) return false;
                            const items = Array.from(list.querySelectorAll('.comment-item, .list-item, [class*="comment-item"]'));
                            return items.some(n => (n.innerText || '').trim().includes(t));
                        }""",
                        arg=final_text,
                        timeout=3000
                    )
                if ok:
                    ok_count += 1
                    total_commented += 1
                    try:
                        data_storage.mark_video_commented(url, final_text)
                    except Exception as e:
                        await self._emit_event("warning", f"⚠️ 标记评论状态失败：{e}")
                else:
                    fail_count += 1

                # 每评论 5 条，强制智能休息一次，降低风控风险
                if total_commented > 0 and total_commented % 5 == 0:
                    await anti_detection.smart_rest(20, 40)

                # 同一笔记多条评论之间的间隔（单位仍为“分钟”，后面乘以 60）
                await self.pause(interval_min_ms*60, interval_max_ms*60, 'comment_interval')

                # per_note-1：同一笔记连发多条
                for _ in range(1, max(1, per_note)):
                    if await self._check_stop():
                        break
                    el = await input_locator.element_handle()
                    if el:
                        await page.evaluate("""(node)=>{
                            node.focus();
                            const sel = window.getSelection();
                            const range = document.createRange();
                            range.selectNodeContents(node);
                            range.collapse(false);
                            sel.removeAllRanges();
                            sel.addRange(range);
                        }""", el)

                    type_delay = random.randint(18, 35)
                    try:
                        await input_locator.type(final_text, delay=type_delay)
                    except Exception:
                        el = await input_locator.element_handle()
                        if el:
                            await page.evaluate("""(el)=>{
                                el.focus();
                                const sel = window.getSelection();
                                const range = document.createRange();
                                range.selectNodeContents(el);
                                range.collapse(false);
                                sel.removeAllRanges();
                                sel.addRange(range);
                            }""", el)
                            await page.keyboard.type(final_text, delay=type_delay)
                        else:
                            raise
                    await page.keyboard.press("Enter")

                    total_commented += 1
                    if total_commented > 0 and total_commented % 5 == 0:
                        await anti_detection.smart_rest(20, 40)

                    await self.pause(interval_min_ms*60, interval_max_ms*60, 'comment_interval')

                # 笔记之间也插入一个轻微阅读型停顿，让跳转节奏更自然
                await anti_detection.smart_read(1.5, 4.0)

            except Exception as e:
                fail_count += 1
                await self._emit_event("error", f"❌ 评论失败：{e}")

        await self._emit_event("success", f"✅ 小红书评论完成：成功 {ok_count}，跳过 {skip_count}，失败 {fail_count}")

    def _make_variant(self, text: str) -> str:
        """对文本做轻量变体：
        1) 随机在任意位置分散插入 1~6 个 '.'
        2) 随机在头或尾追加 1 个表情
        """
        base = (text or "").strip()
        if not base:
            return base

        # ---------- 分散插入点号 ----------
        dot_count = random.randint(1, 6)

        # 强制至少 1 个点插在“中间”（避免全部落在头/尾）
        if len(base) >= 2:
            mid_pos = random.randint(1, len(base) - 1)
            base = base[:mid_pos] + "." + base[mid_pos:]
            dot_count -= 1

        for _ in range(dot_count):
            insert_pos = random.randint(0, len(base))
            base = base[:insert_pos] + "." + base[insert_pos:]

        # ---------- 随机头/尾表情 ----------
        emojis = ["😊", "🙂", "😉", "😄", "✨", "🙏", "🌟", "👍", "❤️", "🤝"]
        if random.random() < 0.6:
            emo = random.choice(emojis)
            if random.random() < 0.5:
                base = emo + base
            else:
                base = base + emo

        return base
