# utils/data_storage.py
"""
数据存储模块 - SQLite（修复UNIQUE约束和锁问题 + 添加数据清除功能）
"""
import sqlite3
from typing import List, Dict, Any
from datetime import datetime, timedelta
import time, hashlib, json


class DataStorage:
    """数据存储管理器"""

    def __init__(self, db_path: str = "douyin_data.db"):
        self.db_path = db_path
        self.init_database()
    def _table_has_column(self, table: str, column: str) -> bool:
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cur.fetchall()]
        conn.close()
        return column in cols

    def _ensure_column(self, table: str, column: str, ddl: str):
        if not self._table_has_column(table, column):
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            conn.commit()
            conn.close()

    def _parse_time_ago_to_epoch(self, text: str) -> int:
        """
        把发布时间字符串转成 epoch 秒，用于排序/清理。
        支持：
        - 刚刚
        - N分钟前 / N小时前 / N天前 / N周前 / N月前 / N年前
        - 今天 / 昨天 / 前天（可带 “HH:MM”）
        - YYYY-MM-DD[ HH:MM]
        - MM-DD[ HH:MM]（年份按当前年份，若日期在未来则视为去年）
        无效返回 0。
        """
        if not text:
            return 0

        import re
        from datetime import datetime, timedelta

        s = str(text).strip().replace("：", ":")

        now = datetime.now()

        # 1) 刚刚
        if s == "刚刚":
            return int(now.timestamp())

        # 2) 相对时间：N 分钟/小时/天/周/月/年前
        m = re.search(r"(\d+)\s*个?(分钟|小時|小时|天|周|月|年)前", s)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            sec_map = {
                "分钟": 60,
                "小時": 3600,
                "小时": 3600,
                "天": 86400,
                "周": 604800,
                "月": 2592000,   # 30 天
                "年": 31536000,
            }
            seconds = n * sec_map.get(unit, 0)
            if seconds <= 0:
                return 0
            return int(now.timestamp() - seconds)

        # 3) 今天 / 昨天 / 前天 [+ HH:MM]
        m = re.match(r"^(今天|昨天|前天)(?:\s+(\d{1,2}):(\d{1,2}))?$", s)
        if m:
            day_word, hh, mm = m.group(1), m.group(2), m.group(3)
            base = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if day_word == "昨天":
                base = base - timedelta(days=1)
            elif day_word == "前天":
                base = base - timedelta(days=2)
            if hh is not None and mm is not None:
                base = base.replace(hour=int(hh), minute=int(mm))
            return int(base.timestamp())

        # 4) 绝对日期：YYYY-MM-DD[ HH:MM]
        m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{1,2}))?$", s)
        if m:
            year = int(m.group(1))
            month = int(m.group(2))
            day = int(m.group(3))
            hh = int(m.group(4) or 0)
            mm = int(m.group(5) or 0)
            try:
                dt = datetime(year, month, day, hh, mm)
                return int(dt.timestamp())
            except ValueError:
                return 0

        # 5) 绝对日期：MM-DD[ HH:MM]（小红书常见写法）
        m = re.match(r"^(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{1,2}))?$", s)
        if m:
            month = int(m.group(1))
            day = int(m.group(2))
            hh = int(m.group(3) or 0)
            mm = int(m.group(4) or 0)
            year = now.year
            try:
                dt = datetime(year, month, day, hh, mm)
                # 如果这个日期在未来（例如现在是 12 月，日期是 01-15），当成是去年的
                if dt > now:
                    dt = datetime(year - 1, month, day, hh, mm)
                return int(dt.timestamp())
            except ValueError:
                return 0

        # 其它格式暂时不解析
        return 0


    def get_connection(self):
        """获取数据库连接（字典行模式）"""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        
        # 启用WAL模式和设置busy_timeout解决锁问题
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")

        return conn

    def init_database(self):
        """
        初始化表结构；移除users.user_url的UNIQUE约束
        """
        conn = self.get_connection()
        cur = conn.cursor()

        # 用户表 - 移除user_url的唯一约束
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                user_url TEXT,
                comment_text TEXT,
                ip_location TEXT,
                video_url TEXT,
                video_desc TEXT,
                matched_keyword TEXT,
                collected_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_status TEXT DEFAULT 'pending',
                last_message_time TIMESTAMP,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 视频表（不对video_url做UNIQUE约束）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_url TEXT,
                video_desc TEXT,
                keyword TEXT,
                collected_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                view_count INTEGER DEFAULT 0,
                like_count INTEGER DEFAULT 0
            )
        """)

        # 任务日志表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS task_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT,
                task_status TEXT,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                details TEXT,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 永久已发送标记表（按 user_url 持久化）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sent_users (
                user_url TEXT PRIMARY KEY,
                sent_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 语境化已发送表（与抖音 sent_users 完全独立；按 user_url+context 去重）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sent_users_ctx (
                user_url TEXT NOT NULL,
                context  TEXT NOT NULL,
                ts       INTEGER DEFAULT (strftime('%s','now')),
                UNIQUE(user_url, context)
            )
        """)

        # 小红书评论监听的游标表（每个笔记一条）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS xhs_watch_state (
                video_url TEXT PRIMARY KEY,
                last_scan_ts INTEGER DEFAULT 0,
                last_seen_fpid TEXT,
                last_seen_count INTEGER DEFAULT 0
            )
        """)
        # Agent 项目表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT,
                route TEXT NOT NULL,
                industry TEXT,
                service TEXT,
                city TEXT,
                target_customer TEXT,
                account_style TEXT,
                price_range TEXT,
                customer_type TEXT,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Agent 会话表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                route TEXT NOT NULL,
                user_input TEXT,
                parsed_profile_json TEXT,
                status TEXT DEFAULT 'created',
                progress INTEGER DEFAULT 0,
                error_message TEXT,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_time TIMESTAMP,
                CHECK(status IN ('created', 'need_clarify', 'ready', 'running', 'finished', 'failed'))
            )
        """)

        # Agent 生成关键词表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS generated_keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                keyword_type TEXT,
                reason TEXT,
                score REAL DEFAULT 0,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 小红书笔记分析表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS xhs_notes_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                note_url TEXT,
                title TEXT,
                author_name TEXT,
                content_score REAL DEFAULT 0,
                title_structure TEXT,
                topic_type TEXT,
                tag_summary TEXT,
                copywriting_structure TEXT,
                visual_summary TEXT,
                evidence_json TEXT,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 小红书评论洞察表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS xhs_comment_insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                video_url TEXT,
                user_url TEXT,
                username TEXT,
                comment_text TEXT,
                intent_type TEXT,
                pain_point TEXT,
                demand_type TEXT,
                intent_level TEXT,
                selling_point TEXT,
                evidence_json TEXT,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 候选词表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS candidate_terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                project_id INTEGER,
                industry TEXT,
                term TEXT NOT NULL,
                term_type TEXT,
                reason TEXT,
                evidence_count INTEGER DEFAULT 0,
                evidence_samples_json TEXT,
                review_status TEXT DEFAULT 'pending',
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_time TIMESTAMP,
                CHECK(review_status IN ('pending', 'approved', 'rejected'))
            )
        """)

        # 人工审核通过词表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS approved_terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER,
                project_id INTEGER,
                industry TEXT,
                term TEXT NOT NULL,
                term_type TEXT,
                mapping_json TEXT,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Agent 报告表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                project_id INTEGER,
                route TEXT NOT NULL,
                report_json TEXT,
                report_markdown TEXT,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 项目记忆表
        cur.execute("""
            CREATE TABLE IF NOT EXISTS project_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                memory_type TEXT,
                content TEXT,
                source_session_id INTEGER,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 小红书笔记正文内容表（仅供 Agent 分析，不在前端列表展示）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS xhs_note_contents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER,
                video_url TEXT NOT NULL,
                agent_session_id INTEGER DEFAULT 0,
                project_id INTEGER DEFAULT 0,
                collection_batch_id TEXT,
                title TEXT,
                content_text TEXT,
                raw_text TEXT,
                tags_json TEXT,
                image_count INTEGER DEFAULT 0,
                source TEXT DEFAULT 'detail_page',
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 小红书笔记公开评论原始表（仅供 Agent 获客路线分析，不在前端列表展示）
        cur.execute("""
            CREATE TABLE IF NOT EXISTS xhs_note_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_session_id INTEGER DEFAULT 0,
                project_id INTEGER DEFAULT 0,
                collection_batch_id TEXT,
                note_url TEXT NOT NULL,
                note_title TEXT,
                comment_id TEXT,
                username TEXT,
                user_url TEXT,
                comment_text TEXT,
                ip_location TEXT,
                comment_time TEXT,
                comment_ts INTEGER DEFAULT 0,
                like_count INTEGER DEFAULT 0,
                reply_count INTEGER DEFAULT 0,
                matched_keyword TEXT,
                intent_level TEXT DEFAULT 'unknown',
                is_high_value INTEGER DEFAULT 0,
                raw_text TEXT,
                created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        		# —— 新增的列，向后兼容（若不存在则添加）——
        self._ensure_column("videos", "publish_time", "TEXT")
        self._ensure_column("videos", "publish_ts", "INTEGER DEFAULT 0")
        self._ensure_column("users", "comment_time", "TEXT")
        self._ensure_column("users", "comment_ts", "INTEGER DEFAULT 0")
        self._ensure_column("videos", "author_name", "TEXT")
        self._ensure_column("videos", "author_url", "TEXT")
        self._ensure_column("videos", "like_count", "INTEGER DEFAULT 0")
        # 添加评论数 / 收藏数字段（若未存在）
        self._ensure_column("videos", "comment_count", "INTEGER DEFAULT 0")
        self._ensure_column("videos", "collect_count", "INTEGER DEFAULT 0")
        self._ensure_column("videos", "last_commented_ts", "INTEGER DEFAULT 0")
        self._ensure_column("videos", "last_commented_hash", "TEXT")

        self._ensure_column("videos", "platform", "TEXT")
        self._ensure_column("users", "platform", "TEXT")
        self._ensure_column("videos", "agent_session_id", "INTEGER DEFAULT 0")
        self._ensure_column("videos", "project_id", "INTEGER DEFAULT 0")
        self._ensure_column("videos", "collection_batch_id", "TEXT")
        self._ensure_column("videos", "agent_collection", "INTEGER DEFAULT 0")



        # 检查是否需要迁移users表（移除UNIQUE约束）
        try:
            cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'")
            row = cur.fetchone()
            if row and row["sql"] and "UNIQUE" in row["sql"].upper():
                # 迁移users表
                cur.execute("ALTER TABLE users RENAME TO users_old")
                cur.execute("""
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL,
                        user_url TEXT,
                        comment_text TEXT,
                        ip_location TEXT,
                        video_url TEXT,
                        video_desc TEXT,
                        matched_keyword TEXT,
                        collected_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        message_status TEXT DEFAULT 'pending',
                        last_message_time TIMESTAMP,
                        created_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        comment_time TEXT,
                        comment_ts INTEGER DEFAULT 0
                    )
                """)
                cur.execute("""
                    INSERT INTO users (username, user_url, comment_text, ip_location, video_url, video_desc, matched_keyword, collected_time, message_status, last_message_time, created_time)
                    SELECT username, user_url, comment_text, ip_location, video_url, video_desc, matched_keyword, collected_time, message_status, last_message_time, created_time
                    FROM users_old
                """)
                cur.execute("DROP TABLE users_old")
                print("✅ 成功迁移users表，移除UNIQUE约束")
        except Exception as e:
            print(f"迁移users表失败或无须迁移: {e}")

        # 索引（注意不要创建唯一索引）
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_status ON users(message_status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_videos_keyword ON videos(keyword)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_task_logs_time ON task_logs(created_time)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_sessions_status ON agent_sessions(status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_sessions_project ON agent_sessions(project_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_generated_keywords_session ON generated_keywords(session_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_xhs_notes_analysis_session ON xhs_notes_analysis(session_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_xhs_comment_insights_session ON xhs_comment_insights(session_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_candidate_terms_status ON candidate_terms(review_status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_candidate_terms_project ON candidate_terms(project_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_approved_terms_project ON approved_terms(project_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_agent_reports_session ON agent_reports(session_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_project_memory_project ON project_memory(project_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_videos_agent_session ON videos(agent_session_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_videos_collection_batch ON videos(collection_batch_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_xhs_note_contents_session ON xhs_note_contents(agent_session_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_xhs_note_contents_video_url ON xhs_note_contents(video_url)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_xhs_note_comments_session ON xhs_note_comments(agent_session_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_xhs_note_comments_note_url ON xhs_note_comments(note_url)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_xhs_note_comments_high_value ON xhs_note_comments(is_high_value)")
        conn.commit()
        conn.close()
    def _json_dumps(self, data: Any) -> str:
        """转换为 JSON 字符串"""
        try:
            if data is None:
                return ""
            if isinstance(data, str):
                return data
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            return ""

    def save_project(self, project_data: Dict[str, Any]) -> int:
        """保存 Agent 项目画像"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO projects
                (project_name, route, industry, service, city, target_customer, account_style, price_range, customer_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                project_data.get("project_name") or project_data.get("name") or "",
                project_data.get("route") or "",
                project_data.get("industry") or "",
                project_data.get("service") or "",
                project_data.get("city") or "",
                project_data.get("target_customer") or "",
                project_data.get("account_style") or "",
                project_data.get("price_range") or "",
                project_data.get("customer_type") or "",
            ))
            project_id = cur.lastrowid
            conn.commit()
            conn.close()
            return int(project_id or 0)
        except Exception as e:
            print(f"保存 Agent 项目失败: {e}")
            return 0

    def get_project(self, project_id: int) -> Dict[str, Any]:
        """读取 Agent 项目画像"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM projects WHERE id = ? LIMIT 1", (int(project_id),))
            row = cur.fetchone()
            conn.close()
            return dict(row) if row else {}
        except Exception as e:
            print(f"读取 Agent 项目失败: {e}")
            return {}

    def create_agent_session(self, session_data: Dict[str, Any]) -> int:
        """创建 Agent 会话"""
        try:
            parsed_profile = session_data.get("parsed_profile_json")
            if parsed_profile is None:
                parsed_profile = session_data.get("parsed_profile")

            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO agent_sessions
                (project_id, route, user_input, parsed_profile_json, status, progress, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                session_data.get("project_id"),
                session_data.get("route") or "",
                session_data.get("user_input") or "",
                self._json_dumps(parsed_profile),
                session_data.get("status") or "created",
                int(session_data.get("progress") or 0),
                session_data.get("error_message") or "",
            ))
            session_id = cur.lastrowid
            conn.commit()
            conn.close()
            return int(session_id or 0)
        except Exception as e:
            print(f"创建 Agent 会话失败: {e}")
            return 0

    def update_agent_session(
        self,
        session_id: int,
        status: str | None = None,
        progress: int | None = None,
        error_message: str | None = None,
        parsed_profile: Any = None,
        completed: bool = False
    ) -> bool:
        """更新 Agent 会话状态"""
        try:
            fields = []
            values = []

            if status is not None:
                fields.append("status = ?")
                values.append(status)

            if progress is not None:
                fields.append("progress = ?")
                values.append(int(progress))

            if error_message is not None:
                fields.append("error_message = ?")
                values.append(error_message)

            if parsed_profile is not None:
                fields.append("parsed_profile_json = ?")
                values.append(self._json_dumps(parsed_profile))

            if completed:
                fields.append("completed_time = CURRENT_TIMESTAMP")

            if not fields:
                return False

            values.append(int(session_id))
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute(
                f"UPDATE agent_sessions SET {', '.join(fields)} WHERE id = ?",
                values
            )
            conn.commit()
            updated = cur.rowcount
            conn.close()
            return updated > 0
        except Exception as e:
            print(f"更新 Agent 会话失败: {e}")
            return False

    def get_agent_session(self, session_id: int) -> Dict[str, Any]:
        """读取 Agent 会话"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM agent_sessions WHERE id = ? LIMIT 1", (int(session_id),))
            row = cur.fetchone()
            conn.close()
            return dict(row) if row else {}
        except Exception as e:
            print(f"读取 Agent 会话失败: {e}")
            return {}

    def save_generated_keywords(self, session_id: int, keywords: List[Dict[str, Any]]) -> int:
        """保存 Agent 生成关键词"""
        try:
            if not keywords:
                return 0

            conn = self.get_connection()
            cur = conn.cursor()
            count = 0

            for item in keywords:
                cur.execute("""
                    INSERT INTO generated_keywords
                    (session_id, keyword, keyword_type, reason, score)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    int(session_id),
                    item.get("keyword") or "",
                    item.get("keyword_type") or "",
                    item.get("reason") or "",
                    float(item.get("score") or 0),
                ))
                count += 1

            conn.commit()
            conn.close()
            return count
        except Exception as e:
            print(f"保存 Agent 关键词失败: {e}")
            return 0

    def get_generated_keywords(self, session_id: int) -> List[Dict]:
        """读取 Agent 生成关键词"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT *
                FROM generated_keywords
                WHERE session_id = ?
                ORDER BY score DESC, id ASC
            """, (int(session_id),))
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"读取 Agent 关键词失败: {e}")
            return []

    def save_xhs_note_analysis(self, session_id: int, items: List[Dict[str, Any]]) -> int:
        """保存小红书笔记分析结果"""
        try:
            if not items:
                return 0

            conn = self.get_connection()
            cur = conn.cursor()
            count = 0

            for item in items:
                cur.execute("""
                    INSERT INTO xhs_notes_analysis
                    (session_id, note_url, title, author_name, content_score, title_structure, topic_type, tag_summary, copywriting_structure, visual_summary, evidence_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    int(session_id),
                    item.get("note_url") or item.get("video_url") or "",
                    item.get("title") or item.get("video_desc") or "",
                    item.get("author_name") or "",
                    float(item.get("content_score") or 0),
                    item.get("title_structure") or "",
                    item.get("topic_type") or "",
                    item.get("tag_summary") or "",
                    item.get("copywriting_structure") or "",
                    item.get("visual_summary") or "",
                    self._json_dumps(item.get("evidence") or item.get("evidence_json")),
                ))
                count += 1

            conn.commit()
            conn.close()
            return count
        except Exception as e:
            print(f"保存小红书笔记分析失败: {e}")
            return 0

    def save_xhs_comment_insights(self, session_id: int, items: List[Dict[str, Any]]) -> int:
        """保存小红书评论洞察结果"""
        try:
            if not items:
                return 0

            conn = self.get_connection()
            cur = conn.cursor()
            count = 0

            for item in items:
                cur.execute("""
                    INSERT INTO xhs_comment_insights
                    (session_id, video_url, user_url, username, comment_text, intent_type, pain_point, demand_type, intent_level, selling_point, evidence_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    int(session_id),
                    item.get("video_url") or "",
                    item.get("user_url") or "",
                    item.get("username") or "",
                    item.get("comment_text") or "",
                    item.get("intent_type") or "",
                    item.get("pain_point") or "",
                    item.get("demand_type") or "",
                    item.get("intent_level") or "",
                    item.get("selling_point") or "",
                    self._json_dumps(item.get("evidence") or item.get("evidence_json")),
                ))
                count += 1

            conn.commit()
            conn.close()
            return count
        except Exception as e:
            print(f"保存小红书评论洞察失败: {e}")
            return 0

    def save_candidate_terms(self, items: List[Dict[str, Any]]) -> int:
        """保存候选词，默认 pending"""
        try:
            if not items:
                return 0

            conn = self.get_connection()
            cur = conn.cursor()
            count = 0

            for item in items:
                cur.execute("""
                    INSERT INTO candidate_terms
                    (session_id, project_id, industry, term, term_type, reason, evidence_count, evidence_samples_json, review_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item.get("session_id"),
                    item.get("project_id"),
                    item.get("industry") or "",
                    item.get("term") or "",
                    item.get("term_type") or "",
                    item.get("reason") or "",
                    int(item.get("evidence_count") or 0),
                    self._json_dumps(item.get("evidence_samples") or item.get("evidence_samples_json")),
                    item.get("review_status") or "pending",
                ))
                count += 1

            conn.commit()
            conn.close()
            return count
        except Exception as e:
            print(f"保存候选词失败: {e}")
            return 0

    def get_candidate_terms(self, review_status: str = "pending", project_id: int | None = None, limit: int = 100) -> List[Dict]:
        """读取候选词"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()

            sql = "SELECT * FROM candidate_terms WHERE review_status = ?"
            params = [review_status]

            if project_id is not None:
                sql += " AND project_id = ?"
                params.append(int(project_id))

            sql += " ORDER BY created_time DESC LIMIT ?"
            params.append(int(limit))

            cur.execute(sql, params)
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"读取候选词失败: {e}")
            return []

    def approve_candidate_term(self, candidate_id: int, mapping: Any = None) -> bool:
        """人工通过候选词并写入正式词库"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()

            cur.execute("SELECT * FROM candidate_terms WHERE id = ? LIMIT 1", (int(candidate_id),))
            row = cur.fetchone()
            if not row:
                conn.close()
                return False

            item = dict(row)

            cur.execute("""
                UPDATE candidate_terms
                SET review_status = 'approved', reviewed_time = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (int(candidate_id),))

            cur.execute("""
                INSERT INTO approved_terms
                (candidate_id, project_id, industry, term, term_type, mapping_json)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                int(candidate_id),
                item.get("project_id"),
                item.get("industry") or "",
                item.get("term") or "",
                item.get("term_type") or "",
                self._json_dumps(mapping),
            ))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"通过候选词失败: {e}")
            return False

    def reject_candidate_term(self, candidate_id: int) -> bool:
        """人工拒绝候选词"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                UPDATE candidate_terms
                SET review_status = 'rejected', reviewed_time = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (int(candidate_id),))
            conn.commit()
            updated = cur.rowcount
            conn.close()
            return updated > 0
        except Exception as e:
            print(f"拒绝候选词失败: {e}")
            return False

    def get_approved_terms(self, project_id: int | None = None, limit: int = 200) -> List[Dict]:
        """读取正式词库"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()

            if project_id is not None:
                cur.execute("""
                    SELECT *
                    FROM approved_terms
                    WHERE project_id = ?
                    ORDER BY created_time DESC
                    LIMIT ?
                """, (int(project_id), int(limit)))
            else:
                cur.execute("""
                    SELECT *
                    FROM approved_terms
                    ORDER BY created_time DESC
                    LIMIT ?
                """, (int(limit),))

            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"读取正式词库失败: {e}")
            return []

    def save_agent_report(self, session_id: int, project_id: int, route: str, report_json: Any, report_markdown: str = "") -> int:
        """保存 Agent 报告"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO agent_reports
                (session_id, project_id, route, report_json, report_markdown)
                VALUES (?, ?, ?, ?, ?)
            """, (
                int(session_id),
                int(project_id or 0),
                route or "",
                self._json_dumps(report_json),
                report_markdown or "",
            ))
            report_id = cur.lastrowid
            conn.commit()
            conn.close()
            return int(report_id or 0)
        except Exception as e:
            print(f"保存 Agent 报告失败: {e}")
            return 0

    def get_agent_report(self, session_id: int) -> Dict[str, Any]:
        """读取 Agent 最新报告"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT *
                FROM agent_reports
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 1
            """, (int(session_id),))
            row = cur.fetchone()
            conn.close()
            return dict(row) if row else {}
        except Exception as e:
            print(f"读取 Agent 报告失败: {e}")
            return {}

    def save_project_memory(self, project_id: int, memory_type: str, content: str, source_session_id: int | None = None) -> int:
        """保存项目记忆"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO project_memory
                (project_id, memory_type, content, source_session_id)
                VALUES (?, ?, ?, ?)
            """, (
                int(project_id),
                memory_type or "",
                content or "",
                source_session_id,
            ))
            memory_id = cur.lastrowid
            conn.commit()
            conn.close()
            return int(memory_id or 0)
        except Exception as e:
            print(f"保存项目记忆失败: {e}")
            return 0

    def get_project_memory(self, project_id: int, limit: int = 100) -> List[Dict]:
        """读取项目记忆"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT *
                FROM project_memory
                WHERE project_id = ?
                ORDER BY created_time DESC
                LIMIT ?
            """, (int(project_id), int(limit)))
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"读取项目记忆失败: {e}")
            return []
    def is_user_sent(self, user_url: str) -> bool:
        """检查 user_url 是否在永久已发送表中"""
        if not user_url:
            return False
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM sent_users WHERE user_url = ? LIMIT 1", (user_url,))
            row = cur.fetchone()
            conn.close()
            return row is not None
        except Exception:
            return False

    def mark_user_sent(self, user_url: str) -> bool:
        """将 user_url 写入 sent_users（幂等）"""
        if not user_url:
            return False
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("INSERT OR IGNORE INTO sent_users (user_url) VALUES (?)", (user_url,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"写入 sent_users 失败: {e}")
            return False
    def is_user_sent_ctx(self, context: str, user_url: str, within_hours: int | None = None) -> bool:
        """查询某个 context 下 user_url 是否已发送；within_hours>0 时做时效内去重"""
        if not context or not user_url:
            return False
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            if within_hours and int(within_hours) > 0:
                cutoff = int(time.time()) - int(within_hours) * 3600
                cur.execute(
                    "SELECT 1 FROM sent_users_ctx WHERE user_url=? AND context=? AND ts>=? LIMIT 1",
                    (user_url, context, cutoff)
                )
            else:
                cur.execute(
                    "SELECT 1 FROM sent_users_ctx WHERE user_url=? AND context=? LIMIT 1",
                    (user_url, context)
                )
            row = cur.fetchone()
            conn.close()
            return row is not None
        except Exception:
            return False

    def mark_user_sent_ctx(self, context: str, user_url: str) -> bool:
        """写入 context 去重表（幂等）"""
        if not context or not user_url:
            return False
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT OR IGNORE INTO sent_users_ctx (user_url, context, ts) VALUES (?, ?, strftime('%s','now'))",
                (user_url, context)
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"写入 sent_users_ctx 失败: {e}")
            return False

    def save_user(self, user_data: Dict[str, Any]) -> bool:
        """保存用户信息（使用INSERT OR IGNORE避免重复；根据 sent_users 强制写入 message_status）"""
        try:
            comment_time = user_data.get("comment_time") or ""
            comment_ts = user_data.get("comment_ts") or self._parse_time_ago_to_epoch(comment_time)
            user_url = user_data.get("user_url") or ""
            status = 'sent' if self.is_user_sent(user_url) else 'pending'
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT OR IGNORE INTO users
                (username, user_url, comment_text, ip_location, video_url, video_desc, matched_keyword, comment_time, comment_ts, message_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_data.get("username"),
                user_data.get("user_url"),
                user_data.get("comment_text"),
                user_data.get("ip_location"),
                user_data.get("video_url"),
                user_data.get("video_desc"),
                user_data.get("matched_keyword"),
                comment_time,
                int(comment_ts or 0),
                status,
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"保存用户失败: {e}")
            return False
    def user_sent_in_users(self, user_url: str) -> bool:
        """在 users 表中检查 user_url 是否已被标记为 sent。"""
        try:
            if not user_url:
                return False
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT 1 FROM users
                WHERE user_url = ? AND COALESCE(message_status, 'pending') = 'sent'
                LIMIT 1
            """, (user_url,))
            row = cur.fetchone()
            conn.close()
            return bool(row)
        except Exception:
            return False

    def save_user_with_status(self, user_data: Dict[str, Any], status: str = 'sent') -> bool:
        """显式写入一条 users 记录，并把 message_status 强制为给定值（不触发 sent_users 持久表）。"""
        try:
            comment_time = user_data.get("comment_time") or ""
            comment_ts = user_data.get("comment_ts") or self._parse_time_ago_to_epoch(comment_time)
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO users
                (username, user_url, comment_text, ip_location, video_url, video_desc, matched_keyword, comment_time, comment_ts, message_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_data.get("username") or "",
                user_data.get("user_url") or "",
                user_data.get("comment_text") or "",
                user_data.get("ip_location") or "",
                user_data.get("video_url") or "",
                user_data.get("video_desc") or "",
                user_data.get("matched_keyword") or "",
                comment_time,
                int(comment_ts or 0),
                status or 'pending'
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"保存用户（显式状态）失败: {e}")
            return False

    def save_video(self, video_data: Dict[str, Any]) -> bool:
        """保存视频信息（允许重复，不去重）"""
        try:
            platform = video_data.get("platform") or ""

            publish_time = video_data.get("publish_time") or ""
            publish_ts = video_data.get("publish_ts") or self._parse_time_ago_to_epoch(publish_time)
            author_name = video_data.get("author_name") or ""
            author_url = video_data.get("author_url") or ""
            like_count = video_data.get("like_count") or 0
            comment_count = video_data.get("comment_count") or 0
            collect_count = video_data.get("collect_count") or 0
            agent_session_id = int(video_data.get("agent_session_id") or 0)
            project_id = int(video_data.get("project_id") or 0)
            collection_batch_id = video_data.get("collection_batch_id") or ""
            agent_collection = int(video_data.get("agent_collection") or 0)
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO videos 
                (video_url, video_desc, keyword, publish_time, publish_ts, author_name, author_url, like_count, comment_count, collect_count, platform, agent_session_id, project_id, collection_batch_id, agent_collection)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                video_data.get("video_url"),
                video_data.get("video_desc"),
                video_data.get("keyword"),
                publish_time,
                int(publish_ts or 0),
                author_name,
                author_url,
                int(like_count),
                int(comment_count),
                int(collect_count),
                platform or 'xhs',
                agent_session_id,
                project_id,
                collection_batch_id,
                agent_collection,
            ))


            conn.commit()
            conn.close()

            return True
        except Exception as e:
            print(f"保存视频失败: {e}")
            return False

    def save_xhs_note_content(self, content_data: Dict[str, Any]) -> bool:
        """保存小红书笔记正文内容（仅供 Agent 分析，不在前端列表展示）"""
        try:
            video_url = content_data.get("video_url") or ""
            if not video_url:
                return False

            conn = self.get_connection()
            cur = conn.cursor()

            video_id = content_data.get("video_id")
            if not video_id:
                cur.execute("""
                    SELECT id
                    FROM videos
                    WHERE video_url = ?
                    ORDER BY id DESC
                    LIMIT 1
                """, (video_url,))
                row = cur.fetchone()
                video_id = row["id"] if row else None

            cur.execute("""
                INSERT INTO xhs_note_contents
                (video_id, video_url, agent_session_id, project_id, collection_batch_id, title, content_text, raw_text, tags_json, image_count, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                video_id,
                video_url,
                int(content_data.get("agent_session_id") or 0),
                int(content_data.get("project_id") or 0),
                content_data.get("collection_batch_id") or "",
                content_data.get("title") or "",
                content_data.get("content_text") or "",
                content_data.get("raw_text") or "",
                self._json_dumps(content_data.get("tags") or content_data.get("tags_json")),
                int(content_data.get("image_count") or 0),
                content_data.get("source") or "detail_page",
            ))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"保存小红书笔记正文失败: {e}")
            return False

    def get_agent_notes_for_analysis(self, session_id: int, limit: int = 80) -> List[Dict]:
        """读取当前 Agent 会话绑定的小红书笔记，包含正文内容供 AI 分析"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    v.*,
                    c.content_text AS content_text,
                    c.raw_text AS raw_text,
                    c.tags_json AS tags_json,
                    c.image_count AS image_count
                FROM videos v
                LEFT JOIN (
                    SELECT c1.*
                    FROM xhs_note_contents c1
                    INNER JOIN (
                        SELECT video_url, MAX(id) AS max_id
                        FROM xhs_note_contents
                        WHERE agent_session_id = ?
                        GROUP BY video_url
                    ) c2 ON c1.video_url = c2.video_url AND c1.id = c2.max_id
                ) c ON v.video_url = c.video_url
                WHERE v.agent_session_id = ?
                  AND COALESCE(v.platform, '') = 'xhs'
                ORDER BY v.collected_time DESC, v.id DESC
                LIMIT ?
            """, (int(session_id), int(session_id), int(limit)))
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"读取 Agent 笔记分析数据失败: {e}")
            return []

    def save_xhs_note_comments(self, comments: List[Dict[str, Any]]) -> int:
        """保存小红书笔记公开评论原始数据（仅供 Agent 获客路线分析）"""
        try:
            if not comments:
                return 0

            conn = self.get_connection()
            cur = conn.cursor()
            count = 0

            for item in comments:
                note_url = item.get("note_url") or item.get("video_url") or ""
                comment_text = item.get("comment_text") or ""
                if not note_url or not comment_text:
                    continue

                comment_time = item.get("comment_time") or ""
                comment_ts = item.get("comment_ts") or self._parse_time_ago_to_epoch(comment_time)
                comment_id = item.get("comment_id") or self._build_xhs_comment_id(
                    note_url=note_url,
                    username=item.get("username") or "",
                    comment_text=comment_text,
                    comment_time=comment_time,
                )

                cur.execute("""
                    SELECT 1
                    FROM xhs_note_comments
                    WHERE agent_session_id = ?
                      AND note_url = ?
                      AND comment_id = ?
                    LIMIT 1
                """, (
                    int(item.get("agent_session_id") or 0),
                    note_url,
                    comment_id,
                ))
                if cur.fetchone():
                    continue

                is_high_value = self._is_high_value_xhs_comment(
                    comment_text=comment_text,
                    matched_keyword=item.get("matched_keyword") or ""
                )
                intent_level = item.get("intent_level") or ("medium" if is_high_value else "low")

                cur.execute("""
                    INSERT INTO xhs_note_comments
                    (agent_session_id, project_id, collection_batch_id, note_url, note_title, comment_id,
                     username, user_url, comment_text, ip_location, comment_time, comment_ts, like_count,
                     reply_count, matched_keyword, intent_level, is_high_value, raw_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    int(item.get("agent_session_id") or 0),
                    int(item.get("project_id") or 0),
                    item.get("collection_batch_id") or "",
                    note_url,
                    item.get("note_title") or item.get("video_desc") or "",
                    comment_id,
                    item.get("username") or "",
                    item.get("user_url") or "",
                    comment_text,
                    item.get("ip_location") or "",
                    comment_time,
                    int(comment_ts or 0),
                    int(item.get("like_count") or 0),
                    int(item.get("reply_count") or 0),
                    item.get("matched_keyword") or "",
                    intent_level,
                    1 if is_high_value else 0,
                    item.get("raw_text") or "",
                ))
                count += 1

            conn.commit()
            conn.close()
            return count
        except Exception as e:
            print(f"保存小红书笔记评论失败: {e}")
            return 0

    def get_agent_note_comments_for_analysis(self, session_id: int, limit: int = 120, high_value_only: bool = True) -> List[Dict]:
        """读取当前 Agent 会话绑定的小红书公开评论，供获客路线 AI 分析"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()

            if high_value_only:
                cur.execute("""
                    SELECT *
                    FROM xhs_note_comments
                    WHERE agent_session_id = ?
                      AND is_high_value = 1
                    ORDER BY comment_ts DESC, created_time DESC, id DESC
                    LIMIT ?
                """, (int(session_id), int(limit)))
            else:
                cur.execute("""
                    SELECT *
                    FROM xhs_note_comments
                    WHERE agent_session_id = ?
                    ORDER BY is_high_value DESC, comment_ts DESC, created_time DESC, id DESC
                    LIMIT ?
                """, (int(session_id), int(limit)))

            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"读取 Agent 笔记评论失败: {e}")
            return []

    def _build_xhs_comment_id(self, note_url: str, username: str, comment_text: str, comment_time: str) -> str:
        """生成评论去重 ID"""
        raw = f"{note_url}|{username}|{comment_text}|{comment_time}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _is_high_value_xhs_comment(self, comment_text: str, matched_keyword: str = "") -> bool:
        """本地判断公开评论是否具备获客分析价值"""
        text = str(comment_text or "").strip()
        if not text:
            return False

        low_value_texts = {
            "哈哈哈", "哈哈", "好看", "路过", "赞", "收藏了", "学到了", "蹲", "马克",
            "打卡", "来了", "不错", "真好", "哇", "666", "姐妹", "谢谢分享"
        }
        if text in low_value_texts:
            return False

        high_value_words = [
            "多少钱", "价格", "价位", "报价", "费用", "预算", "贵吗", "便宜吗",
            "求推荐", "推荐", "在哪", "地址", "位置", "能预约", "预约", "档期",
            "避坑", "靠谱吗", "靠谱", "踩雷", "案例", "客片", "效果", "套餐",
            "怎么选", "适合", "咨询", "联系方式", "私", "还有吗", "有位置吗"
        ]
        if any(word in text for word in high_value_words):
            return True

        matched_keyword = str(matched_keyword or "").strip()
        if matched_keyword and matched_keyword in text:
            return True

        question_markers = ["?", "？", "吗", "么", "怎么", "哪里", "哪家", "多少", "能不能", "有没有"]
        if any(marker in text for marker in question_markers) and len(text) >= 4:
            return True

        return False

    def mark_message_sent(self, user_url: str) -> bool:
        """标记用户为已发送私信（同时写入 sent_users）"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            # 1) 幂等写入永久表
            cur.execute("INSERT OR IGNORE INTO sent_users (user_url) VALUES (?)", (user_url,))
            # 2) 更新当前 users 表中的状态
            cur.execute("""
                UPDATE users
                SET message_status = 'sent', last_message_time = CURRENT_TIMESTAMP
                WHERE user_url = ?
            """, (user_url,))
            updated = cur.rowcount
            conn.commit()
            conn.close()
            return updated > 0
        except Exception as e:
            print(f"标记用户私信状态失败: {e}")
            return False

    def mark_users_pending(self, user_urls: List[str]) -> int:
        """将选中的用户批量标记为pending"""
        try:
            if not user_urls:
                return 0
            conn = self.get_connection()
            cur = conn.cursor()
            count = 0
            for u in user_urls:
                cur.execute('UPDATE users SET message_status="pending" WHERE user_url = ?', (u,))
                count += cur.rowcount
            conn.commit()
            conn.close()
            return count
        except Exception as e:
            print(f"标记选中用户为待发送失败: {e}")
            return 0

    def get_pending_users(self, limit: int = 100) -> List[Dict]:
        """获取待发送私信的用户"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT * FROM users
                WHERE message_status = 'pending'
                ORDER BY collected_time ASC
                LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"获取待发送用户失败: {e}")
            return []

    def get_recent_users(self, limit: int = 50, sort_by: str = "time") -> List[Dict]:
        """获取最近采集的用户，支持排序"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            
            if sort_by == "ip":
                query = """
                    SELECT * FROM users
                    ORDER BY ip_location ASC, collected_time DESC
                    LIMIT ?
                """
            elif sort_by == "publish":
                query = """
                    SELECT * FROM users
                    ORDER BY comment_ts DESC, collected_time DESC
                    LIMIT ?
                """
            else:
                query = """
                    SELECT * FROM users
                    ORDER BY collected_time DESC
                    LIMIT ?
                """
            cur.execute(query, (limit,))
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"获取最近用户失败: {e}")
            return []

    def get_recent_videos(self, limit: int = 0 ,sort_by:str ="time") -> List[Dict]:
        """获取最近采集的视频"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            if sort_by == "publish":
                cur.execute("""
                    SELECT * FROM videos
                    ORDER BY publish_ts DESC
                    LIMIT ?
                """, (limit,))
            else:
                cur.execute("""
                    SELECT * FROM videos
                    ORDER BY collected_time DESC
                    LIMIT ?
                """, (limit,))
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"获取最近视频失败: {e}")
            return []
    def get_users_dedup(self, limit: int = 0, sort_by: str = "time") -> List[Dict]:
        """按用户名去重（同昵称只保留最新采集的一条），支持全量"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()

            base_sql = """
                SELECT *
                FROM users
                WHERE id IN (
                    SELECT MAX(id)
                    FROM users
                    GROUP BY COALESCE(username, '')
                )
            """
            order_map = {
                "ip": " ORDER BY ip_location ASC, collected_time DESC",
                "publish": " ORDER BY comment_ts DESC, collected_time DESC"
            }
            limit_sql = "" if not limit or limit <= 0 else " LIMIT ?"
            order_sql = order_map.get(sort_by, " ORDER BY collected_time DESC")
            sql = base_sql + order_sql + limit_sql

            if limit_sql:
                cur.execute(sql, (limit,))
            else:
                cur.execute(sql)

            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"获取去重用户失败: {e}")
            return []

    def get_videos_dedup_by_desc(self, limit: int = 0,sort_by:str="time") -> List[Dict]:
        """按视频文案去重（同文案只保留最新采集的一条），支持全量"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            order_sql = " ORDER BY publish_ts DESC, collected_time DESC" if sort_by == "publish" else " ORDER BY collected_time DESC"
            base_sql = f"""
                SELECT *
                FROM videos
                WHERE id IN (
                    SELECT MAX(id)
                    FROM videos
                    GROUP BY COALESCE(video_desc, '')
                )
                {order_sql}
            """
            limit_sql = "" if not limit or limit <= 0 else " LIMIT ?"
            sql = base_sql + limit_sql

            if limit_sql:
                cur.execute(sql, (limit,))
            else:
                cur.execute(sql)

            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"获取去重视频失败: {e}")
            return []

    def get_user_stats(self) -> Dict[str, Any]:
            """统计数据（去重 + 本地日历日）"""
            try:
                conn = self.get_connection()
                cur = conn.cursor()

                # 以 user_url 为唯一标识；若 user_url 为空则回退到 username
                # SQLite: COUNT(DISTINCT x) 不统计 NULL，这里用 IFNULL 兜底
                cur.execute("SELECT COUNT(DISTINCT IFNULL(user_url, username)) AS total FROM users")
                total_row = cur.fetchone()
                total = total_row[0] if total_row else 0

                cur.execute("""
                    SELECT COUNT(DISTINCT IFNULL(user_url, username)) AS pending
                    FROM users
                    WHERE message_status = 'pending'
                """)
                pending_row = cur.fetchone()
                pending = pending_row[0] if pending_row else 0

                cur.execute("""
                    SELECT COUNT(DISTINCT IFNULL(user_url, username)) AS sent
                    FROM users
                    WHERE message_status = 'sent'
                """)
                sent_row = cur.fetchone()
                sent = sent_row[0] if sent_row else 0

                # 今日按本地日历日统计（而不是 UTC）
                cur.execute("""
                    SELECT COUNT(DISTINCT IFNULL(user_url, username)) AS today
                    FROM users
                    WHERE date(collected_time, 'localtime') = date('now', 'localtime')
                """)
                today_row = cur.fetchone()
                today = today_row[0] if today_row else 0

                conn.close()
                return {
                    "total_users": total,
                    "pending_users": pending,
                    "sent_users": sent,
                    "today_users": today,
                }
            except Exception:
                return {
                    "total_users": 0,
                    "pending_users": 0,
                    "sent_users": 0,
                    "today_users": 0,
                }

    # 新增：数据清除方法
    def clear_users(self, scope: str = "all", ids: list | None = None, days: int = 7) -> int:
        conn = self.get_connection()
        cur = conn.cursor()
        try:
            if scope == "selected" and ids:
                placeholders = ','.join(['?' for _ in ids])
                cur.execute(f"DELETE FROM users WHERE id IN ({placeholders})", [int(x) for x in ids])

            elif scope == "sent":
                cur.execute("DELETE FROM users WHERE message_status = 'sent'")
            elif scope == "unsent":
                cur.execute("DELETE FROM users WHERE COALESCE(message_status, 'pending') <> 'sent'")
            elif scope == "days":
                # 按评论时间的时间戳 comment_ts 清理：
                # 1）优先删除 comment_ts 对应的“评论时间早于 N 天”的数据
                # 2）对没有 comment_ts 的记录，退回按 created_time 来清理
                cutoff_ts = int((datetime.now() - timedelta(days=int(days))).timestamp())
                cur.execute("""
                    DELETE FROM users
                    WHERE 
                        (comment_ts IS NOT NULL AND comment_ts > 0 AND comment_ts < ?)
                        OR (
                            (comment_ts IS NULL OR comment_ts = 0)
                            AND created_time < datetime('now', ?)
                        )
                """, (cutoff_ts, f"-{int(days)} days"))

            elif scope == "all":
                cur.execute("DELETE FROM users")
            else:
                # 未知 scope，直接不动数据，返回 0
                return 0
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def clear_videos(self, scope: str = "all", ids: list | None = None, days: int = 7) -> int:
        conn = self.get_connection()
        cur = conn.cursor()
        try:
            if scope == "selected" and ids:
                placeholders = ','.join(['?' for _ in ids])
                cur.execute(f"DELETE FROM videos WHERE id IN ({placeholders})", [int(x) for x in ids])
            elif scope == "days":
                # 按视频发布时间的时间戳 publish_ts 清理：
                # 1）优先删除 publish_ts 对应的“发布时间早于 N 天”的数据
                # 2）对没有 publish_ts 的记录，退回按 collected_time 来清理
                cutoff_ts = int((datetime.now() - timedelta(days=int(days))).timestamp())
                cur.execute("""
                    DELETE FROM videos
                    WHERE 
                        (publish_ts IS NOT NULL AND publish_ts > 0 AND publish_ts < ?)
                        OR (
                            (publish_ts IS NULL OR publish_ts = 0)
                            AND collected_time < datetime('now', ?)
                        )
                """, (cutoff_ts, f"-{int(days)} days"))
            elif scope == "all":
                cur.execute("DELETE FROM videos")
            else:
                # 未知 scope，不做任何删除，直接返回 0
                return 0
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


    def clear_task_logs(self, scope: str = "all", days: int = 7) -> int:
        conn = self.get_connection()
        cur = conn.cursor()
        try:
            if scope == "days":
                cur.execute("DELETE FROM task_logs WHERE created_time < datetime('now', ?)", (f"-{int(days)} days",))
            else:
                cur.execute("DELETE FROM task_logs")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    def update_video(self, data: Dict[str, Any]) -> bool:
        """
        根据 video_url 更新视频详情字段：
        - video_desc
        - keyword
        - publish_time / publish_ts
        - author_name / author_url
        - like_count / comment_count / collect_count
        """
        url = data.get("video_url")
        if not url:
            return False

        try:
            conn = self.get_connection()
            cur = conn.cursor()

            cur.execute("""
                UPDATE videos
                SET 
                    video_desc = COALESCE(?, video_desc),
                    keyword = COALESCE(?, keyword),
                    publish_time = COALESCE(?, publish_time),
                    publish_ts = COALESCE(?, publish_ts),
                    author_name = COALESCE(?, author_name),
                    author_url = COALESCE(?, author_url),
                    like_count = COALESCE(?, like_count),
                    comment_count = COALESCE(?, comment_count),
                    collect_count = COALESCE(?, collect_count),
                    agent_session_id = COALESCE(?, agent_session_id),
                    project_id = COALESCE(?, project_id),
                    collection_batch_id = COALESCE(?, collection_batch_id),
                    agent_collection = COALESCE(?, agent_collection)
                WHERE video_url = ?
            """, (
                data.get("video_desc"),
                data.get("keyword"),
                data.get("publish_time"),
                data.get("publish_ts"),
                data.get("author_name"),
                data.get("author_url"),
                data.get("like_count"),
                data.get("comment_count"),
                data.get("collect_count"),
                data.get("agent_session_id"),
                data.get("project_id"),
                data.get("collection_batch_id"),
                data.get("agent_collection"),
                url
            ))

            conn.commit()
            conn.close()

            return cur.rowcount > 0

        except Exception as e:
            print(f"更新视频失败: {e}")
            return False
    def mark_video_commented(self, video_url: str, text: str) -> bool:
        """标记指定 video_url 的最近一次评论信息（幂等覆盖）"""
        if not video_url or not text:
            return False
        try:
            ts = int(time.time())
            h = hashlib.sha1(text.encode("utf-8")).hexdigest()
            conn = self.get_connection()
            cur = conn.cursor()
            # 允许重复 video_url，这里直接覆盖所有匹配行的标记
            cur.execute("""
                UPDATE videos
                SET last_commented_ts = ?, last_commented_hash = ?
                WHERE video_url = ?
            """, (ts, h, video_url))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"标记视频评论状态失败: {e}")
            return False
    def get_watch_cursor(self, video_url: str) -> dict:
        """读取某条笔记的监听游标（无则返回默认值）"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT last_scan_ts, last_seen_fpid, last_seen_count FROM xhs_watch_state WHERE video_url=? LIMIT 1",
                (video_url,)
            )
            row = cur.fetchone()
            conn.close()
            if not row:
                return {"last_scan_ts": 0, "last_seen_fpid": None, "last_seen_count": 0}
            return {"last_scan_ts": row[0] or 0, "last_seen_fpid": row[1], "last_seen_count": row[2] or 0}
        except Exception:
            return {"last_scan_ts": 0, "last_seen_fpid": None, "last_seen_count": 0}

    def set_watch_cursor(self, video_url: str, fpid: str | None, count: int) -> bool:
        """更新某条笔记的监听游标（upsert）"""
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            ts = int(time.time())
            cur.execute("""
                INSERT INTO xhs_watch_state (video_url, last_scan_ts, last_seen_fpid, last_seen_count)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(video_url) DO UPDATE SET
                    last_scan_ts=excluded.last_scan_ts,
                    last_seen_fpid=excluded.last_seen_fpid,
                    last_seen_count=excluded.last_seen_count
            """, (video_url, ts, fpid, int(count or 0)))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"更新 watch cursor 失败: {e}")
            return False

# 全局实例
data_storage = DataStorage()
