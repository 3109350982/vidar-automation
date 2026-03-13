import asyncio
import random
import time
import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from urllib.parse import urljoin
import hashlib
from services.base_service import BaseService
from core.browser_manager import browser_manager
from utils.advanced_anti_detection import anti_detection
from utils.data_storage import data_storage
from utils.data_storage import data_storage
class XhsReplyWatchService(BaseService):
    """小红书评论监听并自动回复：滚动评论容器 → 逐条判断 → 点击该条【回复】 → 输入 → 回车"""

    async def execute(self, **kwargs):
        urls: List[str] = kwargs.get("urls") or []
        if not urls:
            await self._emit_event("error", "❌ 没有勾选任何小红书链接")
            return

        def to_list(v):
            if v is None: return []
            if isinstance(v, list): return [str(x).strip() for x in v if str(x).strip()]
            s = str(v or "").strip()
            if not s: return []
            return [x for x in re.split(r"[，, \s]+", s) if x]

        keywords   = to_list(kwargs.get("keywords") or kwargs.get("watch_keywords"))
        ip_filters = to_list(kwargs.get("ip_filters") or kwargs.get("ip_keywords"))
        templates  = to_list(kwargs.get("templates") or kwargs.get("template"))
        text_single: str = (kwargs.get("text") or "").strip()
        if text_single and not templates:
            templates = [text_single]

        # 统一化关键词（小写/全角转半角/去掉空白和 emoji）
        norm_keywords = [self._norm(k) for k in keywords]

        # 注：目前不限制每条笔记的回复数
        per_note: int        = int(65536)
        int_min: int         = int(kwargs.get("interval_min_ms") or kwargs.get("interval_ms") or 2)
        int_max: int         = int(kwargs.get("interval_max_ms") or kwargs.get("interval_ms") or 4)
        if int_max < int_min: int_max = int_min + 500
        variant: bool     = bool(kwargs.get("variant") or False)

        dedup_hours: int     = int(kwargs.get("dedup_hours") or 0)      # 同一用户去重时长（小时，0=永久）
        variant: bool       = bool(kwargs.get("variant") or False)     # 回复文本变体（随机点号/表情）
 
        after_expr: str      = (kwargs.get("after_expr") or "").strip()
        cutoff_ts: int       = self._parse_after_expr(after_expr) if after_expr else 0
        if cutoff_ts:
            await self._emit_event("operation", f"⏰ 仅回复 {after_expr} 之后的评论")

        await self._ensure_browser_ready()
        page = (await self._get_browser_manager()).page

        total_sent = 0
        processed = set()

        for url in urls:
            if await self._check_stop(): break

            await self._emit_event("operation", f"🔧 打开：{url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(600)

            # 打开笔记后先模拟阅读一段时间，避免立刻开始评论操作
            await anti_detection.smart_read(3.0, 8.0)

            # 有“展开 X 条评论”就点一下，保证列表渲染
            try:
                await page.locator('text=/展开\\s*\\d+\\s*条?评论/').first.click(timeout=1200)
                await page.wait_for_timeout(300)
            except Exception:
                pass

            # 1) 找到评论外层容器
            container = await self._find_comment_container(page)
            if container is None:
                for _ in range(4):
                    await page.mouse.wheel(0, 700)
                    await asyncio.sleep(0.2)
                container = await self._find_comment_container(page)
            if container is None:
                await self._emit_event("warning", "⚠️ 未找到评论容器，跳过本链接")
                continue

            # 2) 先把评论容器滚到可见区域
            try:
                await container.scroll_into_view_if_needed()
                await page.wait_for_timeout(200)
            except Exception:
                pass

            # 3) 找到真正承载 item 的“列表节点”，并定位**可滚动目标**
            list_el = await self._find_comment_list(container)
            scroll_el = await self._find_scroll_target(list_el)

            per_round_sent = 0
            last_top = -1
            stagnation = 0  # 连续无推进计数

            for _ in range(600):
                if await self._check_stop(): break
                if per_round_sent >= per_note: break

                # 列出评论项（覆盖 parent-comment）
                items = await list_el.query_selector_all(
                    ':scope .comment-item, '
                    ':scope [class*="comment"][class*="item"], '
                    ':scope li[class*="comment"], '
                    ':scope div.parent-comment, '
                    ':scope div[class*="parent-comment"]'
                )

                if not items:
                    # 平滑小步推进，触发懒加载
                    await self._scroll_element(scroll_el, 0)
                    await page.wait_for_timeout(400)
                    items = await list_el.query_selector_all(
                        ':scope .comment-item, '
                        ':scope [class*="comment"][class*="item"], '
                        ':scope li[class*="comment"], '
                        ':scope div.parent-comment, '
                        ':scope div[class*="parent-comment"]'
                    )

                # 兜底：以“回复图标”反向定位评论项
                if not items:
                    reply_icons = await list_el.query_selector_all(
                        'svg use[href*="#reply"], svg use[xlink\\:href*="#reply"]'
                    )
                    tmp = []
                    for icon in (reply_icons or []):
                        try:
                            root = await icon.evaluate_handle("""
                                (node)=>{
                                    let n=node;
                                    while(n && !(n.closest && (n.closest('.comment-item')
                                        || n.closest('li[class*="comment"]')
                                        || n.closest('div[class*="comment-item"]')))){
                                        n = n.parentElement;
                                    }
                                    return (n && (n.closest('.comment-item')
                                        || n.closest('li[class*="comment"]')
                                        || n.closest('div[class*="comment-item"]')
                                        || n.closest('div.parent-comment')
                                        || n.closest('div[class*="parent-comment"]'))) || null;
                                }
                            """)
                            if root and root.as_element():
                                tmp.append(root.as_element())
                        except Exception:
                            pass
                    items = tmp
                    await self._emit_event("debug", f"[DIAG] items={len(items or [])}")

                round_seen = set()
                for it in items or []:

                    if await self._check_stop(): break
                    if per_round_sent >= per_note: break

                    # 指纹 + 等价集合（解决 h↔cid 形态切换）
                    fid = await self._fingerprint_comment(it)
                    aliases = await self._fingerprint_aliases(it)

                    # 当轮去重：fid 或任一别名出现过即跳过
                    if (fid in round_seen) or any(a in round_seen for a in aliases):
                        continue
                    round_seen.add(fid)
                    round_seen.update(aliases)

                    # 跨轮去重：fid 或任一别名已处理过即跳过
                    if (fid in processed) or any(a in processed for a in aliases):
                        continue

                    # ⚠️ 不再对每条评论强制滚动（避免频繁位移导致“回弹”）
                    # 直接抽取内容；若抽不到，再做最小滚动重试
                    text, date_txt, ip_txt, user_url, username = await self._extract_comment_fields(it)
                    # user_url 为空时做确定性兜底（避免去重/落库失效）
                    if not user_url:
                        raw = f"{username}|{ip_txt}|{url}"
                        user_url = "__xhs_fallback__" + hashlib.sha1(raw.encode('utf-8')).hexdigest()

                    if not text:
                        # 最小幅度对齐到容器视口后再试一次
                        try:
                            await self._ensure_item_visible(list_el, it)
                            await page.wait_for_timeout(80)
                        except Exception:
                            pass
                        text, date_txt, ip_txt, user_url = await self._extract_comment_fields(it)

                    await self._emit_event("debug", f"[DIAG] text_len={len(text)} date='{date_txt}' ip='{ip_txt}'")

                    if not text:
                        processed.add(fid); processed.update(aliases)
                        continue

                    # 时间过滤（解析失败也跳过，严格按 after_expr）
                    if cutoff_ts:
                        cts = self._parse_comment_time(date_txt)
                        if (not cts) or (cts < cutoff_ts):
                            processed.add(fid); processed.update(aliases)
                            continue
                    # 已回复用户跳过：先查永久表，再查语境去重表（按每条笔记 url 分 context）
                    if data_storage.is_user_sent(user_url):
                        processed.add(fid)
                        continue
                    ctx = f"xhs_reply_watch::{url}"
                    if data_storage.is_user_sent_ctx(ctx, user_url, within_hours=dedup_hours):
                        processed.add(fid)
                        continue

                    # 关键词/IP 过滤（标准化文本）
                    ntext = self._norm(text)
                    if norm_keywords and not any(k in ntext for k in norm_keywords):
                        processed.add(fid); processed.update(aliases)
                        continue
                    if ip_filters and not any(x in (ip_txt or "") for x in ip_filters):
                        processed.add(fid); processed.update(aliases)
                        continue

                    # 在点击前，最小幅度把该条对齐到容器视口（只在需要点击时才滚动）
                    try:
                        await self._ensure_item_visible(list_el, it)
                        await page.wait_for_timeout(50)
                    except Exception:
                        pass
                    # ——— 监听去重：若该用户在用户列表中已标记为已发送，则跳过 ———
                    try:
                        if user_url and data_storage.user_sent_in_users(user_url):
                            processed.add(fid)
                            continue
                    except Exception as e:
                        await self._emit_event("warning", f"⚠️ 用户去重检查失败：{e}")

                    # 回复前先短暂停留，模拟“看了一眼这条评论”的行为
                    await anti_detection.smart_read(0.8, 2.0)

                    # 点该条【回复】
                    clicked = await self._click_reply_button(it)
                    if not clicked:
                        try:
                            loc = it.locator('text=回复').first
                            box = await loc.bounding_box()
                            if box:
                                await anti_detection.human_like_click_bbox(box, element_type="xhs_reply_text")
                            else:
                                await loc.click(timeout=1200)
                            clicked = True
                        except Exception:
                            clicked = False
                    await self._emit_event("debug", f"[DIAG] click_reply clicked={clicked}")

                    if not clicked:
                        processed.add(fid); processed.update(aliases)
                        continue

                    # 聚焦输入 → 输入 → 回车
                    out = random.choice(templates) if templates else "感谢你的评论～"
                    if variant:
                        out = self._make_variant(out)
                    out = out.replace("{nick}", username or "")
                    out = self._make_variant(out) if variant else out
                    ok = await self._focus_and_submit(page, out)
                    await self._emit_event("debug", f"[DIAG] focus_and_submit ok={ok}")

                    if not ok:
                        processed.add(fid); processed.update(aliases)
                        continue
                    # ——— 回复成功：把该评论的用户写入 users 表，并标记为 sent ———
                    try:
                        matched_kw = ''
                        try:
                            matched_kw = next((k for k in (norm_keywords or []) if k in ntext), '')
                        except Exception:
                            matched_kw = ''
                        data_storage.save_user_with_status({
                            "username": "",
                            "user_url": user_url or "",
                            "comment_text": text or "",
                            "ip_location": ip_txt or "",
                            "video_url": url,
                            "video_desc": "",
                            "matched_keyword": matched_kw,
                            "comment_time": date_txt or "",
                            "comment_ts": self._parse_comment_time(date_txt or "") or 0
                        }, status='sent')
                    except Exception as e:
                        await self._emit_event("warning", f"⚠️ 写入用户列表失败：{e}")
                    data_storage.mark_video_commented(url, out)
                    # 落库：B 方案 - 直接写 users.message_status='sent' 并写入 sent_users 永久表
                    try:
                        data_storage.save_user_with_status({
                            "username": username or "",
                            "user_url": user_url or "",
                            "comment_text": text or "",
                            "ip_location": ip_txt or "",
                            "video_url": url or "",
                            "matched_keyword": "|".join([k for k in keywords if k]) if keywords else "",
                            "comment_time": date_txt or "",
                            "collected_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            "platform": "xhs"
                        }, status="sent")
                        data_storage.mark_message_sent(user_url)
                        data_storage.mark_user_sent_ctx(f"xhs_reply_watch::{url}", user_url)
                    except Exception:
                        pass
                    per_round_sent += 1
                    total_sent += 1
                    processed.add(fid); processed.update(aliases)

                    # 每成功回复 5 条，强制智能休息一次
                    if total_sent > 0 and total_sent % 5 == 0:
                        await anti_detection.smart_rest(15, 30)

                    # interval 仍按“分钟”*60 来控制整体节奏
                    await asyncio.sleep(random.uniform(int_min*60, int_max*60))

                if per_round_sent >= per_note:
                    break

                # —— 可继续滚动？（借鉴抖音：看是否还有空间或出现新内容） ——
                can_scroll = await self._check_can_scroll_list(list_el)
                if not can_scroll:
                    break

                # —— 惯性缓动推进一屏 ——（不回弹，不倒车）
                new_top = await self._scroll_element(scroll_el, 0)
                await page.wait_for_timeout(80)  # 更短的稳定等待，提高整体速度

                if new_top is not None and new_top == last_top:
                    stagnation += 1
                else:
                    stagnation = 0
                if stagnation >= 3:
                    break
                last_top = new_top

            await self._emit_event("operation", f"📄 本条笔记完成：回复 {per_round_sent} 条")

        await self._emit_event("success", f"✅ 监听完成，共回复 {total_sent} 条")
    def _make_variant(self, text: str) -> str:
        """对文本做轻量变体：随机追加点号，以及在头/尾随机追加 1 个表情"""
        """对文本做轻量变体：
        1) 随机在任意位置分散插入 1~6 个 '.'
        2) 随机在头或尾追加 1 个表情
        """
        base = (text or "").strip()
        if not base:
            return base

        # ---------- 分散插入点号 ----------
        if random.random() < 0.7:
            dot_count = random.randint(1, 6)
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
    # ----------------- 抽取器 / DOM / 滚动工具 -----------------

    async def _extract_comment_fields(self, item) -> Tuple[str, str, str, str, str]:
        """
        逐级兜底抽取：评论内容 text、时间 date_txt、IP ip_txt、作者链接 user_url
        1) 明确选择器
        2) 宽松选择器
        3) JS 兜底 innerText（从 .content 或当前节点）
        """
        text = ""
        date_txt = ""
        ip_txt = ""
        user_url = ""
        username = ""
        # 1) 文本（优先常见结构）
        for sel in [
            'div.note-text > span',
            'span.note-text',
            'div[class*="note-text"]',
            'div.content',
            'div[class*="content"]',
            'p',
        ]:
            try:
                el = await item.query_selector(sel)
                if el:
                    t = (await el.inner_text()) or ""
                    if t.strip():
                        text = t.strip()
                        break
            except Exception:
                pass
        if not text:
            # 2) JS 兜底：尽量取 .content 下的纯文本
            try:
                text = await item.evaluate("""(n)=>{
                    const c = n.querySelector('.content') || n;
                    return (c.innerText || '').trim();
                }""")
            except Exception:
                text = ""

        # 3) 时间
        for sel in ['div.date', 'div[class*="date"]', 'span.date', 'span[class*="date"]']:
            try:
                el = await item.query_selector(sel)
                if el:
                    s = (await el.inner_text()) or ""
                    if s.strip():
                        date_txt = s.strip()
                        break
            except Exception:
                pass

        # 4) IP 归属
        for sel in ['div.location', 'span.location', 'div[class*="location"]', 'span[class*="location"]']:
            try:
                el = await item.query_selector(sel)
                if el:
                    s = (await el.inner_text()) or ""
                    if s.strip():
                        ip_txt = s.strip()
                        break
            except Exception:
                pass

        # 4.1) 规范化：无论是否单独抽到 IP，都剥离 date_txt 末尾的中文地名
        if date_txt:
            try:
                if ip_txt and date_txt.endswith(ip_txt):
                    date_txt = re.sub(rf'{re.escape(ip_txt)}\s*$', '', date_txt).strip()
                else:
                    # 没有单独 IP，但日期尾部携带了地名（如 “昨天03:19天津”“2天前湖南”）
                    m = re.search(r'([\u4e00-\u9fa5]{2,})\s*$', date_txt)
                    if (not ip_txt) and m:
                        ip_txt = m.group(1)
                        date_txt = re.sub(r'([\u4e00-\u9fa5]{2,})\s*$', '', date_txt).strip()
            except Exception:
                pass

        # 5) 作者链接
        try:
            href = await self._safe_attr(item, 'a[href*="/user/profile/"]', 'href')
            if href:
                user_url = urljoin("https://www.xiaohongshu.com", href) if href.startswith("/") else href
        except Exception:
            user_url = ""

        # 6) 作者昵称（尽量从作者链接节点拿）
        try:
            a = await item.query_selector('a[href*="/user/profile/"]')
            if a:
                s = (await a.inner_text()) or ""
                if s.strip():
                    username = s.strip()
        except Exception:
            pass

        if not username:
            for sel in ['span.name', 'a.name', 'div.name', 'span[class*="name"]', 'a[class*="name"]']:
                try:
                    el = await item.query_selector(sel)
                    if el:
                        s = (await el.inner_text()) or ""
                        if s.strip():
                            username = s.strip()
                            break
                except Exception:
                    pass

        return text, date_txt, ip_txt, user_url, username

    async def _find_comment_container(self, page):
        """
        外层容器优先：comments-el / comments-container / 兼容旧类名；找不到返回 <body>
        """
        selectors = [
            '.comment-list', '.comment-container', '[class*="comment-list"]', '[class*="CommentList"]',
            'div.comments-el',
            'div.comments-el .comments-container',
            'div.comments-container',
            'div.right',
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el: return el
            except Exception:
                pass
        try:
            return await page.query_selector('body')
        except Exception:
            return None

    async def _find_comment_list(self, root):
        """在容器下进一步定位真正承载 item 的“列表节点”（name=list / .list-container）"""
        for sel in [
            ':scope [name="list"].list-container',
            ':scope [name="list"]',
            ':scope .list-container',
        ]:
            try:
                el = await root.query_selector(sel)
                if el: return el
            except Exception:
                pass
        return root

    async def _find_scroll_target(self, el):
        """
        从列表节点向上找“可滚动容器”（overflowY 为 auto/scroll 且 scrollHeight>clientHeight）
        找不到时返回 document.scrollingElement（页面滚动）
        """
        try:
            return await el.evaluate_handle("""
                (node)=>{
                  const isScrollable = (n)=>{
                    if(!n) return false;
                    const s = getComputedStyle(n);
                    const oy = s.overflowY;
                    return (oy==='auto' || oy==='scroll') && n.scrollHeight > n.clientHeight;
                  };
                  let cur = node;
                  let sc = node;
                  if(isScrollable(cur)) sc = cur;
                  while(cur && cur.parentElement){
                    cur = cur.parentElement;
                    if(isScrollable(cur)){ sc = cur; break; }
                  }
                  return sc || document.scrollingElement || document.documentElement || document.body;
                }
            """)
        except Exception:
            try:
                return await el.page.query_selector('body')
            except Exception:
                return el

    async def _check_can_scroll_list(self, list_el) -> bool:
        """
        借鉴抖音脚本：检测评论容器是否还可继续滚动。
        若看似到底，则等待最多 6 秒，若容器高度或评论条数增加，则继续滚动，否则确认到底。
        """
        try:
            # 先判断是否还能滚动
            result = await list_el.evaluate("""
                (node)=>{
                    const isScrollable = (n)=>{
                        if(!n) return false;
                        const s = getComputedStyle(n);
                        const oy = s.overflowY;
                        return (oy==='auto' || oy==='scroll') && n.scrollHeight > n.clientHeight;
                    };
                    let sc = isScrollable(node) ? node : node;
                    let cur = node;
                    while(cur && cur.parentElement){
                        cur = cur.parentElement;
                        if(isScrollable(cur)){ sc = cur; break; }
                    }
                    const top = sc.scrollTop || 0;
                    const h   = sc.scrollHeight || 0;
                    const ch  = sc.clientHeight || 0;
                    const can = (top + ch) < (h - 16);
                    return {canScroll: can, top, totalHeight: h, clientHeight: ch};
                }
            """)
            can_scroll = bool(result and result.get('canScroll', False))
            if can_scroll:
                return True

            # 看似到底：等待观察是否有新内容加载
            initial_h = int(result.get('totalHeight', 0)) if result else 0
            initial_cnt = await self._count_comment_items(list_el)

            waited = 0
            while waited < 6 and not await self._check_stop():
                await asyncio.sleep(1.0)
                cur_h = await list_el.evaluate("(n)=> n.scrollHeight || 0")
                cur_cnt = await self._count_comment_items(list_el)
                if (cur_h and cur_h > initial_h) or (cur_cnt > initial_cnt):
                    return True
                waited += 1

            return False
        except Exception:
            # 出错时默认允许滚动，避免误停
            return True

    async def _count_comment_items(self, list_el) -> int:
        """统计当前列表中的评论项数量（与 execute 中的选择器保持一致）"""
        try:
            items = await list_el.query_selector_all(
                ':scope .comment-item, '
                ':scope [class*="comment"][class*="item"], '
                ':scope li[class*="comment"], '
                ':scope div.parent-comment, '
                ':scope div[class*="parent-comment"]'
            )
            return len(items or [])
        except Exception:
            return 0

    async def _ensure_item_visible(self, list_el, item) -> Optional[int]:
        """
        只在“需要点击”时，把评论项最小幅度对齐到容器视口（顶部留 96px，底部留 72px）。
        不使用 scrollIntoView，避免把容器猛拉导致回弹。
        """
        try:
            return await list_el.evaluate("""(list, it)=>{
                const isScrollable = (n)=>{
                    if(!n) return false;
                    const s = getComputedStyle(n);
                    const oy = s.overflowY;
                    return (oy==='auto' || oy==='scroll') && n.scrollHeight > n.clientHeight;
                };
                let sc = isScrollable(list) ? list : list;
                let cur = list;
                while(cur && cur.parentElement){
                    cur = cur.parentElement;
                    if(isScrollable(cur)){ sc = cur; break; }
                }
                const lr = sc.getBoundingClientRect();
                const ir = it.getBoundingClientRect();
                const topM = 96, botM = 72;

                if (ir.top < lr.top + topM){
                    sc.scrollTop -= (lr.top + topM - ir.top);
                } else if (ir.bottom > lr.bottom - botM){
                    sc.scrollTop += (ir.bottom - (lr.bottom - botM));
                }
                return sc.scrollTop || 0;
            }""", item)
        except Exception:
            return None

    async def _scroll_element(self, el, delta: int) -> Optional[int]:
        """
        惯性缓动推进“下一屏”；返回当前 scrollTop。
        - 若 delta==0：自动按“可视高度的 1.6 倍”推进一屏（更快更长）；
        - 使用 rAF + ease-out（非 CSS smooth），落地后吸附到目标，避免抖动。
        """
        try:
            return await el.evaluate("""(node, dy)=>{
  return new Promise((resolve)=>{
    try{
      const isRoot = (node===document.scrollingElement || node===document.documentElement || node===document.body);
      const sc = isRoot ? (document.scrollingElement || document.documentElement || document.body) : node;

      const ch  = sc.clientHeight || window.innerHeight || 800;
      const max = Math.max(0, (sc.scrollHeight || 0) - ch);
      const startTop = sc.scrollTop || 0;

      // 更长一步：1.6 屏；若外部传入 dy>0 则使用 dy
      const autoStep = Math.floor(ch * 1.60);
      const step = (typeof dy==='number' && Math.abs(dy)>0) ? dy : autoStep;

      let target = startTop + step;
      if (target < 0) target = 0;
      if (target > max) target = max;

      const t0 = performance.now();
      const dist = Math.abs(target - startTop);
      const dur = Math.min(420, Math.max(220, dist * 0.35)); // 220–420ms，更快

      const ease = (k)=> 1 - Math.pow(1-k, 3); // cubic ease-out

      function frame(now){
        const k = Math.min(1, (now - t0) / dur);
        sc.scrollTop = startTop + (target - startTop) * ease(k);
        if (k >= 1){
          sc.scrollTop = target;             // 吸附到目标，避免偏差累积
          resolve(sc.scrollTop | 0);
        } else {
          requestAnimationFrame(frame);
        }
      }
      requestAnimationFrame(frame);
    }catch(e){ resolve(null); }
  });
}""", delta)
        except Exception:
            return None

    async def _safe_text(self, root, selector: str) -> str:
        try:
            el = await root.query_selector(selector)
            return (await el.inner_text()) if el else ""
        except Exception:
            return ""

    async def _safe_attr(self, root, selector: str, name: str) -> str:
        try:
            el = await root.query_selector(selector)
            return (await el.get_attribute(name)) if el else ""
        except Exception:
            return ""

    async def _fingerprint_comment(self, item) -> str:
        """为同一条评论生成稳定指纹：
        1) 先取最近的评论根节点上的稳定 comment-id；
        2) 否则使用“正文(规范化)+日期(去掉IP尾巴)”构造签名并哈希，避免相同评论在不同DOM包装下出现不同fid而被重复解析。"""
        # 1) 最近的评论根节点上的稳定 ID
        try:
            cid = await item.evaluate("""
                (n)=>{
                    const root = n.closest('.comment-item, li[class*="comment"], div.parent-comment, div[class*="parent-comment"]') || n;
                    const attrs = ['data-comment-id','data-id','data-e2e-id','id'];
                    for (const a of attrs){
                        const v = root.getAttribute(a);
                        if (v) return v;
                    }
                    return '';
                }
            """)
            if cid:
                return f"cid:{cid}"
        except Exception:
            pass

        # 2) 规范化文本 + 日期（剥离尾部中文地名），生成签名哈希
        try:
            sig = await item.evaluate("""
                (n)=>{
                    const root = n.closest('.comment-item, li[class*="comment"], div.parent-comment, div[class*="parent-comment"]') || n;
                    const textNode = root.querySelector('div.note-text > span, span.note-text, div[class*="note-text"], div.content, div[class*="content"], p') || root;
                    const dateNode = root.querySelector('div.date, span.date, div[class*="date"], span[class*="date"]');
                    let txt = (textNode && textNode.innerText ? textNode.innerText : '').trim();
                    let dt  = (dateNode && dateNode.innerText ? dateNode.innerText : '').trim();
                    dt = dt.replace(/：/g, ':').trim();
                    dt = dt.replace(/[\u4e00-\u9fa5]{2,}\\s*$/, '').trim(); // 去掉尾部IP地名

                    return txt + '|' + dt;
                }
            """)
        except Exception:
            sig = await self._safe_text(item, 'div.note-text > span, span.note-text, div[class*="note-text"], div[class*="content"]')
            sig = (sig or '').strip()

        return f"h:{hash(self._norm(sig))}"

    async def _fingerprint_aliases(self, item) -> set:
        """生成同一条评论的等价指纹集合：同时包含 cid:... 与 h:... 两种形式，
        防止首次扫描取不到ID走哈希、再次扫描出现ID导致换指纹的二次回复。"""
        aliases = set()
        try:
            cid = await item.evaluate("""
                (n)=>{
                    const root = n.closest('.comment-item, li[class*="comment"], div.parent-comment, div[class*="parent-comment"]') || n;
                    const attrs = ['data-comment-id','data-id','data-e2e-id','id'];
                    for (const a of attrs){
                        const v = root.getAttribute(a);
                        if (v) return v;
                    }
                    return '';
                }
            """)
        except Exception:
            cid = ''
        if cid:
            aliases.add(f"cid:{cid}")
        try:
            sig = await item.evaluate("""
                (n)=>{
                    const root = n.closest('.comment-item, li[class*="comment"], div.parent-comment, div[class*="parent-comment"]') || n;
                    const textNode = root.querySelector('div.note-text > span, span.note-text, div[class*="note-text"], div.content, div[class*="content"], p') || root;
                    const dateNode = root.querySelector('div.date, span.date, div[class*="date"], span[class*="date"]');
                    let txt = (textNode && textNode.innerText ? textNode.innerText : '').trim();
                    let dt  = (dateNode && dateNode.innerText ? dateNode.innerText : '').trim();
                    dt = dt.replace(/：/g, ':').trim();
                    dt = dt.replace(/[\u4e00-\u9fa5]{2,}\\s*$/, '').trim(); // 去掉尾部IP地名

                    return txt + '|' + dt;
                }
            """)
        except Exception:
            sig = await self._safe_text(item, 'div.note-text > span, span.note-text, div[class*="note-text"], div[class*="content"]')
            sig = (sig or '').strip()
        if sig:
            aliases.add(f"h:{hash(self._norm(sig))}")
        return aliases

    async def _click_reply_button(self, item) -> bool:
        """
        点击当前评论项的“回复”按钮：
        1) hover + 找 reply-icon 容器点击（带随机偏移）
        2) 兜底：svg use#reply 往上找可点击元素
        3) 最兜底：text=回复
        """
        # 先悬停，部分样式 hover 才显示“回复”图标
        try:
            await item.hover()
        # 增加随机悬停延迟
            await asyncio.sleep(random.uniform(0.15, 0.30))

            await asyncio.sleep(0.05)
        except Exception:
            pass

        # 1) 优先命中 reply-icon 容器
        try:
            cont = await item.query_selector(
                '.interactions .reply.icon-container, '
                '.reply-icon-container, '
                '[class*="reply-icon-container"], '
                '.interactions .reply [class*="icon-container"], '
                '[class*="reply-icon"]'
            )

            if cont:
                btn = await cont.query_selector('button, [role="button"], svg, use')
                target = btn if btn else cont
                try:
                    box = await target.bounding_box()
                    if box:
                        await anti_detection.human_like_click_bbox(box, element_type="xhs_reply_icon")
                    else:
                        await target.click(timeout=1200)
                    return True
                except Exception:
                    pass
        except Exception:
            pass

        # 2) svg use#reply → 向上找可点击按钮
        try:
            use = await item.query_selector('svg use[href*="#reply"], svg use[xlink\\:href*="#reply"]')
            if use:
                btn = await use.evaluate_handle("""(node)=>{
                    let n=node;
                    while(n && !(n.tagName==='BUTTON' || n.onclick || n.getAttribute('role')==='button')){
                        n=n.parentElement;
                    }
                    return n || node.closest('svg') || node.parentElement || null;
                }""")
                if btn and btn.as_element():
                    try:
                        elem = btn.as_element()
                        box = await elem.bounding_box()
                        if box:
                            await anti_detection.human_like_click_bbox(box, element_type="xhs_reply_svg")
                        else:
                            await elem.click(timeout=1200)
                        return True
                    except Exception:
                        pass
        except Exception:
            pass

        # 3) 明文“回复”
        try:
            loc = item.locator('text=回复').first
            box = await loc.bounding_box()
            if box:
                await anti_detection.human_like_click_bbox(box, element_type="xhs_reply_text")
            else:
                await loc.click(timeout=1200)
            return True
        except Exception:
            return False

    async def _focus_and_submit(self, page, text_out: str) -> bool:
        # 等编辑器可见（底部输入条）
        try:
            editor = page.locator(
                '#content-textarea[contenteditable="true"], '
                '.interactions.engage-bar .content-edit [contenteditable="true"], '
                '.reply-container [contenteditable="true"], '
                'div[class*="reply"] [contenteditable="true"]'
            ).first

            await editor.wait_for(state="visible", timeout=5000)
            await self._emit_event("debug", f"[DIAG] editor located")
        except Exception:
            return False

        # 点击编辑器中心，确保进入输入态
        try:
            box = await editor.bounding_box()
            if box:
                await anti_detection.human_like_click_bbox(box, element_type="xhs_reply_input")
            else:
                await editor.click(timeout=800)
        except Exception:
            try:
                await editor.click(timeout=800)
            except Exception:
                pass

        # 保险：先空格再回删，触发 focus/selection
        await page.keyboard.type(" ", delay=random.randint(15, 28))
        await page.keyboard.press("Backspace")
        # 输入回复内容前的停顿
        await anti_detection.human_like_delay(0.1, 0.3, 'focus_input')

        # 输入并回车
        await page.keyboard.type(text_out, delay=random.randint(18, 42))
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(800)
        # 关键：发送后取消焦点，防止输入框把容器强行拉回导致“回弹”
        try:
            await page.evaluate("document.activeElement && document.activeElement.blur && document.activeElement.blur()")
        except Exception:
            pass

        await self._emit_event("debug", f"[DIAG] submit done")
        return True

    # ----------------- 文本标准化 & 时间解析 -----------------

    def _norm(self, s: str) -> str:
        """统一比较用文本：小写，全角转半角，去空白/不可见字符/emoji"""
        s = (s or "").lower()

        # 全角转半角
        def to_halfwidth(u: str) -> str:
            out = []
            for ch in u:
                code = ord(ch)
                if code == 0x3000:
                    code = 0x20
                elif 0xFF01 <= code <= 0xFF5E:
                    code -= 0xFEE0
                out.append(chr(code))
            return "".join(out)

        s = to_halfwidth(s)
        # 去空白和不可见字符
        s = re.sub(r'[\s\u200b-\u200f\ufeff]+', '', s)
        # 去 emoji
        try:
            s = re.sub(r'[\U00010000-\U0010ffff]+', '', s)
        except re.error:
            pass
        return s

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

    def _parse_after_expr(self, expr: str) -> int:
        expr = expr.strip().replace("：", ":").replace(" ", "")
        now = datetime.now()

        m = re.match(r"(\d+)(分钟|小时|天)前$", expr)
        if m:
            n = int(m.group(1)); unit = m.group(2)
            delta = {"分钟": timedelta(minutes=n), "小时": timedelta(hours=n), "天": timedelta(days=n)}[unit]
            return int((now - delta).timestamp())

        m = re.match(r"^(今天|昨天|前天)(\d{1,2}):(\d{1,2})$", expr)
        if m:
            day, hh, mm = m.groups()
            base = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            offset = {"今天":0, "昨天":1, "前天":2}[day]
            return int((base - timedelta(days=offset)).timestamp())

        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T\\s](\d{1,2}):(\d{1,2}))?$", expr)
        if m:
            y, mo, d, hh, mm = [int(x or 0) for x in m.groups()]
            return int(datetime(y, mo, d, hh, mm or 0).timestamp())

        m = re.match(r"^(\d{1,2})-(\d{1,2})(?:[T\\s](\d{1,2}):(\d{1,2}))?$", expr)
        if m:
            mo, d, hh, mm = [int(x or 0) for x in m.groups()]
            y = now.year
            dt = datetime(y, mo, d, hh, mm or 0)
            if dt > now:
                dt = dt.replace(year=y-1)
            return int(dt.timestamp())

        if expr in ("今天","昨天","前天"):
            offset = {"今天":0,"昨天":1,"前天":2}[expr]
            dt = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=offset)
            return int(dt.timestamp())
        return 0

    def _parse_comment_time(self, txt: str) -> int:
        if not txt: return 0
        s = txt.strip().replace("：", ":")
        now = datetime.now()

        if s == "刚刚":
            return int(now.timestamp())

        # 相对时间：N 分钟/小时/天前
        m = re.match(r"(\d+)(分钟|小时|天)前$", s)
        if m:
            n = int(m.group(1)); unit = m.group(2)
            delta = {"分钟": timedelta(minutes=n), "小时": timedelta(hours=n), "天": timedelta(days=n)}[unit]
            return int((now - delta).timestamp())

        # 绝对-中文：今天/昨天/前天 HH:MM
        m = re.match(r"^(今天|昨天|前天)\s*(\d{1,2}):(\d{1,2})$", s)
        if m:
            day, hh, mm = m.groups()
            offset = {"今天":0, "昨天":1, "前天":2}[day]
            dt = (now - timedelta(days=offset)).replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            return int(dt.timestamp())

        # 绝对-中文：今天/昨天/前天（无时间，按当日 00:00）
        if s in ("今天", "昨天", "前天"):
            offset = {"今天":0, "昨天":1, "前天":2}[s]
            dt = (now - timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
            return int(dt.timestamp())

        # 绝对-数字：YYYY-MM-DD 或 YYYY-MM-DD HH:MM
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s*(\d{1,2}):(\d{1,2}))?$", s)
        if m:
            y, mo, d, hh, mm = [int(x or 0) for x in m.groups()]
            return int(datetime(y, mo, d, hh, mm or 0).timestamp())

        # 绝对-数字：MM-DD HH:MM（年份取今年，若未来则回拨一年）
        m = re.match(r"^(\d{1,2})-(\d{1,2})\s*(\d{1,2}):(\d{1,2})$", s)
        if m:
            mo, d, hh, mm = [int(x) for x in m.groups()]
            y = now.year
            dt = datetime(y, mo, d, hh, mm)
            if dt > now: dt = dt.replace(year=y-1)
            return int(dt.timestamp())

        # 绝对-数字：MM-DD（无时间，按 00:00；年份取今年，若未来则回拨一年）
        m = re.match(r"^(\d{1,2})-(\d{1,2})$", s)
        if m:
            mo, d = [int(x) for x in m.groups()]
            y = now.year
            dt = datetime(y, mo, d, 0, 0)
            if dt > now: dt = dt.replace(year=y-1)
            return int(dt.timestamp())

        return 0
