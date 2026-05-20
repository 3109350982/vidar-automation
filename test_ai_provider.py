import json
from utils.data_storage import data_storage


sid = 8

conn = data_storage.get_connection()
cur = conn.cursor()

cur.execute("""
    SELECT status, progress
    FROM agent_sessions
    WHERE id = ?
""", (sid,))
session_row = cur.fetchone()

cur.execute("""
    SELECT *
    FROM agent_reports
    WHERE session_id = ?
    ORDER BY id DESC
    LIMIT 1
""", (sid,))
report_row = cur.fetchone()

cur.execute("""
    SELECT COUNT(*)
    FROM xhs_notes_analysis
    WHERE session_id = ?
""", (sid,))
analysis_count = cur.fetchone()[0]

conn.close()

assert session_row is not None, "Agent 会话不存在"
assert session_row["status"] == "finished", f"会话状态错误: {session_row['status']}"
assert int(session_row["progress"] or 0) == 100, f"会话进度错误: {session_row['progress']}"
assert report_row is not None, "Agent 报告不存在"
assert analysis_count > 0, "笔记分析结果未入库"

report_json = json.loads(report_row["report_json"] or "{}")
report_markdown = report_row["report_markdown"] or ""

required_json_fields = [
    "account_positioning",
    "content_topics",
    "title_templates",
    "visual_suggestions",
    "publish_plan",
    "evidence_summary",
    "risk_tips",
    "next_actions",
]

for field in required_json_fields:
    assert field in report_json, f"报告缺少字段: {field}"

required_markdown_text = [
    "小红书个人账号运营报告",
    "账号定位",
    "内容选题",
    "标题模板",
    "视觉建议",
    "发布计划",
    "数据依据",
    "合规提示",
    "下一步动作",
]

for text in required_markdown_text:
    assert text in report_markdown, f"Markdown 缺少内容: {text}"

assert "自动私信" not in report_markdown or "禁止自动私信" in report_markdown, "报告存在不合规自动触达表达"
assert "绕风控" not in report_markdown or "禁止" in report_markdown, "报告存在不合规绕风控表达"

print("第八部分个人账号运营报告检查通过")