# utils/data_storage.py
"""
数据存储模块 - SQLite（修复UNIQUE约束和锁问题 + 添加数据清除功能）
"""
import sqlite3
from typing import List, Dict, Any
from datetime import datetime, timedelta
import time, hashlib


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

        conn.commit()
        conn.close()

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
            conn = self.get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO videos 
                (video_url, video_desc, keyword, publish_time, publish_ts, author_name, author_url, like_count, comment_count, collect_count, platform)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ))


            conn.commit()
            conn.close()

            return True
        except Exception as e:
            print(f"保存视频失败: {e}")
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
                    collect_count = COALESCE(?, collect_count)
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
