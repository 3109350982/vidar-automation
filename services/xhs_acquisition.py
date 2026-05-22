# services/xhs_acquisition.py
"""
小红书采集服务（阶段一：关键词 -> 搜索结果 -> 列表页滚动采集）
—— 滚动停止判断、字段抓取方法 完全对齐 services/customer_acquisition.py 的 Douyin 阶段一实现；
—— 仅替换为小红书的 DOM 选择器，与您截图的 6 个红框一一对应。
"""
import asyncio
from urllib.parse import quote
import re
import time
from services.base_service import BaseService
from core.browser_manager import browser_manager
from utils.data_storage import data_storage
import random

class XhsAcquisitionService(BaseService):
    """小红书采集服务（对齐抖音阶段一的滚动/采集方法）"""

    def __init__(self):
        super().__init__()
        self._untitled_counter = {}  # 每个关键词的“无标题”序号

        self.current_stage = "stage1"

    async def execute(
        self,
        keywords,
        videos_per_keyword: int = 5,
        sort_type: str = "综合",   # 综合 / 笔记 / 视频（仅影响搜索URL的type）
        duration_minutes: int = 10,
        **kwargs,
    ):
        self.current_stage = "stage1"

        agent_session_id = int(kwargs.get("agent_session_id") or 0)
        project_id = int(kwargs.get("project_id") or 0)
        collection_batch_id = str(kwargs.get("collection_batch_id") or "").strip()
        enable_note_content = bool(kwargs.get("enable_note_content") or False)
        agent_route = str(kwargs.get("agent_route") or "").strip()
        enable_comment_collection = bool(kwargs.get("enable_comment_collection") or False)
        max_comments_per_note = int(kwargs.get("max_comments_per_note") or 30)

        if agent_route != "customer_acquisition" or agent_session_id <= 0:
            enable_comment_collection = False

        if agent_session_id > 0 and not collection_batch_id:
            collection_batch_id = f"agent_{agent_session_id}_{int(time.time())}"

        if not await self._ensure_browser_ready():
            await self._emit_event("error", "❌ 浏览器未就绪，无法开始小红书采集")
            return

        await self._emit_event("operation", f"🚀 小红书采集开始（模式：{sort_type}）")
        if agent_session_id > 0:
            await self._emit_event(
                "operation",
                f"🤖 Agent 采集绑定：session_id={agent_session_id}, batch={collection_batch_id}"
            )
            if enable_comment_collection:
                await self._emit_event(
                    "operation",
                    f"💬 获客路线已启用公开评论采集：每篇最多 {max_comments_per_note} 条"
                )

        # 抖音阶段一用的是“每个关键词采集上限 + 滚动停止判断 + 无新增条数计数”的那套节奏
        # 这里严格复刻：逐关键词 → 搜索页 → while 滚动采集 → 详情字段在列表卡片上尽可能拿
        typ = "general"
        if sort_type == "笔记":
            typ = "note"
        elif sort_type == "视频":
            typ = "video"

        total_saved = 0

        try:
            for idx, kw in enumerate(keywords):
                if await self._check_stop():
                    break

                await self._emit_event("operation", f"🔍 关键词[{idx+1}/{len(keywords)}]：{kw}")
                try:
                    await browser_manager.page.evaluate("""() => { window.__NO_MICRO_OPS__ = true; }""")
                except Exception:
                    pass
                self._untitled_counter.setdefault(kw, 0)

                # 与你截图一致的 URL（右上角参数：source=51&type=51 也可工作；这里默认 web_search）
                search_url = (
                    f"https://www.xiaohongshu.com/search_result?keyword={quote(kw)}"
                    f"&source=web_search&type={typ}"
                )
                await browser_manager.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
                await self._wait_page_ready()

                # 等待首屏卡片渲染（小红书前端渲染，domcontentloaded 不代表卡片出现）
                try:
                    await browser_manager.page.wait_for_selector(
                        'section.note-item, .note-item, a.cover.mask[href*="/explore/"], a[href*="/explore/"]',
                        timeout=15000
                    )
                except Exception:
                    pass

                # —— 完全复刻抖音阶段一主循环的结构与节奏 ——
                collected_urls = set()
                videos         = []
                max_videos     = int(videos_per_keyword)
                max_scroll_attempts = 80     # 与 Douyin 阶段一一致的上限
                scroll_attempts = 0
                no_new_count    = 0

                while len(videos) < max_videos and scroll_attempts < max_scroll_attempts and no_new_count < 5:
                    # 采集当前可见区域的卡片
                    current_videos = await self._collect_visible_videos(browser_manager, kw)

                    new_videos = []
                    remaining = max_videos - len(videos)


                    for video in current_videos:
                        if len(new_videos) >= remaining:
                            break
                        if video['video_url'] not in collected_urls:
                            collected_urls.add(video['video_url'])
                            new_videos.append(video)
                            # 与抖音一致：发现即落库
                            try:
                                if agent_session_id > 0:
                                    video["agent_session_id"] = agent_session_id
                                    video["project_id"] = project_id
                                    video["collection_batch_id"] = collection_batch_id
                                    video["agent_collection"] = 1

                                success = data_storage.save_video(video)
                                if success:
                                    await self._emit_event("debug", f"✅ 保存笔记: {video['video_desc'][:50]}...")
                                    if enable_note_content and agent_session_id > 0:
                                        detail_data = await self._collect_note_detail_content(
                                            browser_manager=browser_manager,
                                            video=video,
                                            agent_session_id=agent_session_id,
                                            project_id=project_id,
                                            collection_batch_id=collection_batch_id,
                                            enable_comment_collection=enable_comment_collection,
                                            max_comments_per_note=max_comments_per_note,
                                            matched_keyword=kw
                                        )
                                        if detail_data:
                                            saved_content = data_storage.save_xhs_note_content(detail_data)
                                            if saved_content:
                                                await self._emit_event("debug", f"📝 已保存笔记正文: {video['video_desc'][:30]}...")

                                            comments = detail_data.get("comments") or []
                                            if comments:
                                                saved_comments = data_storage.save_xhs_note_comments(comments)
                                                await self._emit_event(
                                                    "debug",
                                                    f"💬 已保存公开评论: {saved_comments}/{len(comments)} 条"
                                                )
                                else:
                                    await self._emit_event("warn",  f"⚠️ 保存失败: {video.get('video_url','')}")
                            except Exception as e:
                                await self._emit_event("error", f"❌ 落库异常: {e}")

                    if new_videos:
                        videos.extend(new_videos)
                        no_new_count = 0
                    else:
                        no_new_count += 1

                    # 到达目标数量则退出
                    if len(videos) >= max_videos:
                        break

                    # —— 滚动前判是否可滚（完全按抖音的等待→确认逻辑）——
                    try:
                        can_scroll = await self._check_can_scroll_videos(browser_manager)
                        if not can_scroll:
                            # 确认到底后退出循环
                            break

                        # 执行滚动（与抖音一致：整屏 + 轻微随机）
                        # 新：平滑滚动 + 轻微人类节奏
                        await self._smooth_scroll_once(browser_manager.page, step_ratio=1.80, duration_ms=random.randint(100, 200))
                        await asyncio.sleep(random.uniform(0.10, 0.18))


                    except Exception as e:
                        await self._emit_event("debug", f"⚠️ 滚动操作失败: {e}")
                        no_new_count += 1

                    scroll_attempts += 1
                    if await self._check_stop():
                        break

                await self._emit_event("operation", f"✅ 关键词 '{kw}' 完成：采集 {len(videos[:max_videos])}/{max_videos} 条")
                total_saved += len(videos[:max_videos])

            await self._emit_event("operation", f"🏁 小红书采集结束：累计保存 {total_saved} 条")

        except Exception as e:
            await self._emit_event("error", f"❌ 小红书采集异常：{e}")

    # ---------------- 与抖音阶段一对齐的工具方法 ----------------

    async def _check_can_scroll_videos(self, browser_manager):
        """视频/笔记采集专用底部检测 - 等待 → 比较页面高度与元素数量是否增长（完全复刻 Douyin 阶段一）"""
        try:
            result = await browser_manager.page.evaluate("""
                () => {
                    const windowHeight = window.innerHeight;
                    const docHeight = Math.max(
                        document.body.scrollHeight,
                        document.documentElement.scrollHeight
                    );
                    const currentScroll = window.pageYOffset || document.documentElement.scrollTop;

                    const atBottom = currentScroll + windowHeight >= docHeight - 4;
                    return {
                        atBottom,
                        totalHeight: docHeight,
                        canScroll: !atBottom
                    };
                }
            """)

            can_scroll = result.get('canScroll', False)

            if not can_scroll:
                await self._emit_event("debug", "📜 检测到可能到底部，等待6~8秒确认是否有新增…")

                initial_height = result.get('totalHeight', 0)
                initial_card_count = await self._get_video_count(browser_manager.page)

                waited = 0
                while waited < 8 and not await self._check_stop():
                    await self.pause(1, 1, 'bottom_wait')
                    waited += 1

                    current_height = await browser_manager.page.evaluate("""
                        () => Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)
                    """)
                    current_card_count = await self._get_video_count(browser_manager.page)

                    if current_height > initial_height or current_card_count > initial_card_count:
                        await self._emit_event(
                            "debug",
                            f"🔄 检测到新增，继续滚动（H: {initial_height}→{current_height}, N: {initial_card_count}→{current_card_count}）"
                        )
                        return True

                await self._emit_event("debug", "📜 等待后仍无新增，确认已滑到底部")
                return False

            return True

        except Exception as e:
            await self._emit_event("debug", f"⚠️ 检查滚动状态失败: {e}")
            return True  # 出错时默认可滚，避免误停
    # === 新增：单步平滑下滑，与“监听回复”脚本保持一致风格 ===
    async def _smooth_scroll_once(self, page, step_ratio: float = 0.92, duration_ms: int = 520):
        # 使用 requestAnimationFrame 做缓动，避免“回弹”和“抖动”
        await page.evaluate(
            """
            (args) => new Promise(resolve => {
                const stepRatio = args.stepRatio;
                const duration  = args.duration;

                const height = Math.max(240, Math.floor(window.innerHeight * stepRatio));
                const startY = window.scrollY;
                const endY   = startY + height;
                const startTs = performance.now();

                function easeInOutQuad(t){
                    return t < 0.5 ? 2*t*t : 1 - Math.pow(-2*t+1, 2)/2;
                }
                function step(ts){
                    const p = Math.min(1, (ts - startTs) / duration);
                    const y = startY + (endY - startY) * easeInOutQuad(p);
                    window.scrollTo(0, y);
                    if (p < 1) requestAnimationFrame(step);
                    else resolve();
                }
                requestAnimationFrame(step);
            })
            """,
            {"stepRatio": step_ratio, "duration": duration_ms}
        )


    async def _get_video_count(self, page):
        """当前屏可见卡片数量（小红书：section.note-item）"""
        try:
            return await page.evaluate("""
                () => Array.from(
                    document.querySelectorAll('section.note-item, .note-item, [data-e2e*="note-item"]')
                ).length
            """)

        except Exception:
            return 0

    async def _collect_visible_videos(self, browser_manager, keyword):
        """批量采集当前屏的卡片（优先带 token 的 explore 链接；一次 evaluate 提升速度）"""
        videos = []
        try:
            # 确保卡片已渲染
            try:
                await browser_manager.page.wait_for_selector(
                    'section.note-item a[href*="/explore/"], a[href*="xsec_token"]',
                    timeout=5000
                )
            except Exception:
                pass

            data_list = await browser_manager.page.evaluate(
                """
                () => {
                    const cards = Array.from(document.querySelectorAll(
                        'section.note-item, .note-item, [data-e2e*="note-item"]'
                    ));

                    const toAbs = (raw) => {
                        if (!raw) return '';
                        // 保留 DOM 原始 href；相对路径只做字符串拼接，不用 new URL() 以免重排参数
                        return raw.startsWith('http') ? raw : (location.origin + raw);
                    };
                    const txt = (root, sel) => {
                        const el = root.querySelector(sel);
                        return el ? (el.innerText || el.textContent || '').trim() : '';
                    };
                    const rawHref = (root, sel) => {
                        const el = root.querySelector(sel);
                        if (!el) return '';
                        const raw = el.getAttribute('href') || '';
                        return raw ? toAbs(raw) : '';
                    };
                    const toInt = (t) => {
                        if (!t) return 0;
                        t = String(t).replace(/,/g,'').toLowerCase().trim();
                        if (t.endsWith('亿'))  return Math.floor(parseFloat(t) * 1e8);
                        if (t.endsWith('万') || t.endsWith('w')) return Math.floor(parseFloat(t) * 1e4);
                        const m = t.match(/[0-9]+(?:\\.[0-9]+)?/);
                        return m ? Math.floor(parseFloat(m[0])) : 0;
                    };

                    // ① 全页预扫描：构建 noteId -> 【带 token 的原始 href】映射
                    const tokenMap = new Map();
                    document.querySelectorAll('a[href*="xsec_token"]').forEach(a => {
                        const raw = a.getAttribute('href') || '';
                        const m = raw.match(/\\/(explore|search_result)\\/([0-9a-z]+)/i);
                        if (m && m[2]) tokenMap.set(m[2], toAbs(raw));
                    });

                    // 提取卡片 noteId（先 /explore/，再 /search_result/）
                    const extractId = (root) => {
                        let raw = '';
                        let el  = root.querySelector('a[href*="/explore/"]');
                        if (el) raw = el.getAttribute('href') || '';
                        if (!raw) {
                            el = root.querySelector('a[href*="/search_result/"]');
                            raw = el ? (el.getAttribute('href') || '') : '';
                        }
                        let m = raw.match(/\\/explore\\/([0-9a-z]+)/i);
                        if (!m) m = raw.match(/\\/search_result\\/([0-9a-z]+)/i);
                        return m ? m[1] : '';
                    };

                    return cards.map(card => {
                        // ② 详情链接：优先用【tokenMap】里的原始 href；其次用卡片内任何带 token 的 a；最后兜底 /explore/
                        const id = extractId(card);
                        let url = '';
                        if (id && tokenMap.has(id)) {
                            url = tokenMap.get(id);
                        }
                        if (!url) {
                            url = rawHref(card, 'a[href*="xsec_token"]');
                        }
                        if (!url) {
                            url = rawHref(card, 'a.cover.mask[href*="/explore/"], a[href^="/explore/"], a[href*="/explore/"]');
                        }
                        if (!url) return null;

                        // ③ 其它字段（与现有选择器保持一致）
                        let title = txt(card, '.title span, .title, [class*="title"] span, [class*="title"]');
                        if (!title) title = card.querySelector('img[alt]')?.getAttribute('alt') || '';

                        const author_url  = rawHref(card, '.author a[href*="/user/profile"], a[href*="/user/profile"]');
                        let author_name   = txt(card, '.author .name, .name-time-wrapper .name, .name, [class*="author"] .name') || '未知作者';
                        const publish_time = txt(card, '.name-time-wrapper .time span, .name-time-wrapper .time, .time > span, .time');

                        const like_count    = toInt(txt(card, '.like-wrapper .count, .like .count'));
                        const comment_count = toInt(txt(card, '.comment .count'));
                        const collect_count = toInt(txt(card, '.collect .count'));

                        return { url, title, author_url, author_name, publish_time, like_count, comment_count, collect_count };
                    }).filter(Boolean);
                }
                """
            )


            for d in data_list:
                title = d.get("title", "") or ""
                if (not title) or re.match(r"^[\s.·…•]+$", title):
                    self._untitled_counter.setdefault(keyword, 0)
                    self._untitled_counter[keyword] += 1
                    title = f"无标题（{self._untitled_counter[keyword]}）"

                video = {
                    "video_desc": title,
                    "video_url": d["url"],                     # 原始 href（含 xsec_token）已保留
                    "keyword": keyword,
                    "publish_time": d.get("publish_time", "") or "",
                    "author_name": d.get("author_name", "") or "",
                    "author_url": d.get("author_url", "") or "",
                    "like_count": int(d.get("like_count", 0) or 0),
                    "comment_count": int(d.get("comment_count", 0) or 0),
                    "collect_count": int(d.get("collect_count", 0) or 0),
                    "platform": "xhs",
                }
                videos.append(video)

        except Exception as e:
            await self._emit_event("error", f"❌ 采集当前区域异常: {e}")

        return videos


    async def _collect_note_detail_content(
        self,
        browser_manager,
        video,
        agent_session_id: int,
        project_id: int,
        collection_batch_id: str,
        enable_comment_collection: bool = False,
        max_comments_per_note: int = 30,
        matched_keyword: str = ""
    ):
        """进入笔记详情页采集正文，仅在 Agent 采集时启用"""
        detail_page = None
        try:
            video_url = video.get("video_url") or ""
            if not video_url:
                return {}

            context = browser_manager.page.context
            detail_page = await context.new_page()
            await detail_page.goto(video_url, wait_until="domcontentloaded", timeout=60000)

            try:
                await detail_page.wait_for_selector(
                    'div.note-content, div#detail-desc, span.note-text, .note-text',
                    timeout=15000
                )
            except Exception:
                pass

            if enable_comment_collection:
                try:
                    await detail_page.wait_for_selector(
                        '.comment-item, [class*="comment-item"], [class*="commentItem"], [class*="parent-comment"], [class*="comments"]',
                        timeout=8000
                    )
                except Exception:
                    pass
                try:
                    await detail_page.evaluate("""() => window.scrollBy(0, Math.floor(window.innerHeight * 0.35))""")
                    await asyncio.sleep(0.6)
                except Exception:
                    pass

            detail = await detail_page.evaluate(
                """
                (args) => {
                    const enableComments = !!args.enable_comment_collection;
                    const commentLimit = Number(args.max_comments_per_note || 30);

                    const pickText = (selectors, root=document) => {
                        for (const sel of selectors) {
                            const el = root.querySelector(sel);
                            if (el) {
                                const text = (el.innerText || el.textContent || '').trim();
                                if (text) return text;
                            }
                        }
                        return '';
                    };

                    const pickHref = (selectors, root=document) => {
                        for (const sel of selectors) {
                            const el = root.querySelector(sel);
                            if (el) {
                                const href = el.getAttribute('href') || '';
                                if (href) return href.startsWith('http') ? href : (location.origin + href);
                            }
                        }
                        return '';
                    };

                    const toInt = (text) => {
                        if (!text) return 0;
                        let t = String(text).replace(/,/g, '').toLowerCase().trim();
                        if (t.endsWith('亿')) return Math.floor(parseFloat(t) * 100000000);
                        if (t.endsWith('万') || t.endsWith('w')) return Math.floor(parseFloat(t) * 10000);
                        const m = t.match(/[0-9]+(?:\\.[0-9]+)?/);
                        return m ? Math.floor(parseFloat(m[0])) : 0;
                    };

                    const title = pickText([
                        'div.detail-title.title',
                        '.detail-title.title',
                        '.detail-title',
                        '[class*="detail-title"]',
                        'title'
                    ]);

                    const contentText = pickText([
                        'div#detail-desc span.note-text',
                        '#detail-desc .note-text',
                        'span.note-text',
                        '.note-text',
                        'div.desc',
                        '[class*="note-text"]'
                    ]);

                    const rawText = pickText([
                        'div.note-content',
                        '.note-content',
                        'div#detail-desc',
                        '#detail-desc',
                        '.interaction-container'
                    ]);

                    const authorName = pickText([
                        '.author-container .name',
                        '.author .name',
                        '.username',
                        '[class*="author"] [class*="name"]'
                    ]);

                    const tags = Array.from(document.querySelectorAll(
                        'a.tag, .tag, a[href*="search_result"], a[href*="keyword"]'
                    ))
                        .map(el => (el.innerText || el.textContent || '').trim())
                        .filter(Boolean)
                        .map(text => text.replace(/^#/, '').trim())
                        .filter(Boolean);

                    const imageCount = Array.from(document.querySelectorAll(
                        '.swiper-slide img, .media-container img, .note-slider img, img'
                    )).filter(img => {
                        const src = img.getAttribute('src') || '';
                        return src && !src.includes('avatar') && !src.includes('icon');
                    }).length;

                    let comments = [];
                    if (enableComments) {
                        const commentNodes = Array.from(document.querySelectorAll(
                            '.comment-item, [class*="comment-item"], [class*="commentItem"], [class*="parent-comment"], [class*="comment-item-container"]'
                        ));

                        const seen = new Set();
                        for (const node of commentNodes) {
                            if (comments.length >= commentLimit) break;

                            const raw = (node.innerText || node.textContent || '').trim();
                            if (!raw || raw.length < 2) continue;

                            let commentText = pickText([
                                '.content',
                                '.comment-content',
                                '[class*="comment-content"]',
                                '[class*="content"] span',
                                '[class*="content"]'
                            ], node);

                            if (!commentText) {
                                const lines = raw.split(/\\r?\\n/).map(x => x.trim()).filter(Boolean);
                                commentText = lines.length > 1 ? lines[1] : lines[0];
                            }

                            commentText = String(commentText || '').trim();
                            if (!commentText || commentText.length < 2) continue;
                            if (commentText.length > 500) commentText = commentText.slice(0, 500);

                            const username = pickText([
                                '.author .name',
                                '.user-name',
                                '.username',
                                '.name',
                                '[class*="author"] [class*="name"]',
                                '[class*="user"] [class*="name"]'
                            ], node);

                            const userUrl = pickHref([
                                'a[href*="/user/profile"]',
                                'a[href*="user/profile"]'
                            ], node);

                            const commentTime = pickText([
                                '.date',
                                '.time',
                                '[class*="date"]',
                                '[class*="time"]'
                            ], node);

                            const ipLocation = pickText([
                                '.location',
                                '[class*="location"]',
                                '[class*="ip"]'
                            ], node);

                            const likeText = pickText([
                                '.like .count',
                                '[class*="like"] [class*="count"]',
                                '[class*="like"]'
                            ], node);

                            const key = `${userUrl}|${username}|${commentText}|${commentTime}`;
                            if (seen.has(key)) continue;
                            seen.add(key);

                            comments.push({
                                username,
                                user_url: userUrl,
                                comment_text: commentText,
                                comment_time: commentTime,
                                ip_location: ipLocation,
                                like_count: toInt(likeText),
                                reply_count: 0,
                                raw_text: raw.slice(0, 1000)
                            });
                        }
                    }

                    return {
                        title,
                        content_text: contentText,
                        raw_text: rawText,
                        author_name: authorName,
                        tags,
                        image_count: imageCount,
                        comments
                    };
                }
                """,
                {
                    "enable_comment_collection": enable_comment_collection,
                    "max_comments_per_note": max_comments_per_note
                }
            )

            content_text = (detail.get("content_text") or "").strip()
            raw_text = (detail.get("raw_text") or "").strip()

            comments = []
            for item in detail.get("comments") or []:
                comment_text = (item.get("comment_text") or "").strip()
                if not comment_text:
                    continue
                comments.append({
                    "agent_session_id": agent_session_id,
                    "project_id": project_id,
                    "collection_batch_id": collection_batch_id,
                    "note_url": video_url,
                    "note_title": detail.get("title") or video.get("video_desc") or "",
                    "username": item.get("username") or "",
                    "user_url": item.get("user_url") or "",
                    "comment_text": comment_text,
                    "ip_location": item.get("ip_location") or "",
                    "comment_time": item.get("comment_time") or "",
                    "like_count": int(item.get("like_count") or 0),
                    "reply_count": int(item.get("reply_count") or 0),
                    "matched_keyword": matched_keyword or video.get("keyword") or "",
                    "raw_text": item.get("raw_text") or "",
                })

            if not content_text and not raw_text and not comments:
                return {}

            return {
                "video_url": video_url,
                "agent_session_id": agent_session_id,
                "project_id": project_id,
                "collection_batch_id": collection_batch_id,
                "title": detail.get("title") or video.get("video_desc") or "",
                "content_text": content_text,
                "raw_text": raw_text,
                "tags": detail.get("tags") or [],
                "image_count": int(detail.get("image_count") or 0),
                "source": "detail_page",
                "comments": comments
            }

        except Exception as e:
            await self._emit_event("debug", f"⚠️ 笔记正文采集失败: {e}")
            return {}
        finally:
            if detail_page:
                try:
                    await detail_page.close()
                except Exception:
                    pass


    # —— 与抖音一致的等待与辅助 —— #
    async def _wait_page_ready(self, timeout_ms: int = 10000):
        try:
            await browser_manager.page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            ok = await browser_manager.page.evaluate(
                "() => !!(document && document.body && document.body.children && document.body.children.length > 0)"
            )
            return bool(ok)
        except Exception:
            return True

    def _parse_count_text(self, text: str) -> int:
        """
        解析 “数字 / 数字万 / 数字亿 / 带逗号” —— 与 Douyin 阶段一一致
        """
        if not text:
            return 0
        t = str(text).replace(",", "").strip().lower()
        try:
            if t.endswith("万") or t.endswith("w"):
                return int(float(t[:-1]) * 10000)
            if t.endswith("亿"):
                return int(float(t[:-1]) * 100000000)
            return int(float(t))
        except Exception:
            return 0
    async def _smooth_scroll_once(self, page, step_ratio: float = 1.20, duration_ms: int = 300):
        """在 window.scrollingElement 上做缓动滚动，避免回弹；step_ratio 为每次移动的视窗倍数。"""
        try:
            await page.evaluate(
                """
                ({ stepRatio, duration }) => new Promise(resolve => {
                    const el = document.scrollingElement || document.documentElement || document.body;
                    const height = Math.max(240, Math.floor(window.innerHeight * (stepRatio || 1.0)));
                    const startY = el.scrollTop;
                    const maxY = el.scrollHeight - window.innerHeight - 2;
                    const endY  = Math.min(startY + height, maxY < 0 ? startY + height : maxY);
                    const t0 = performance.now();
                    const ease = (t) => t < 0.5 ? 2*t*t : 1 - Math.pow(-2*t+1, 2)/2;
                    function frame(now){
                        const p = Math.min(1, (now - t0) / (duration || 300));
                        el.scrollTop = startY + (endY - startY) * ease(p);
                        if (p < 1) requestAnimationFrame(frame);
                        else resolve(el.scrollTop);
                    }
                    requestAnimationFrame(frame);
                })
                """, { "stepRatio": step_ratio, "duration": duration_ms }
            )
        except Exception:
            # 兜底：失败就用一次性 scrollBy
            try:
                await page.evaluate("() => window.scrollBy(0, Math.max(300, Math.floor(window.innerHeight*0.9)))")
            except Exception:
                pass
