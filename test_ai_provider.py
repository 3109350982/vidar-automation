import time

import app as app_module
from fastapi.testclient import TestClient

from utils.data_storage import data_storage
from services.ai_agent_service import AIAgentService


app_module.lic_status = lambda: {"valid": True}

client = TestClient(app_module.app)

suffix = str(int(time.time()))

project_id = data_storage.save_project({
    "project_name": f"第十部分总体验收项目_{suffix}",
    "route": "personal_account",
    "industry": "宠物服务",
    "service": "宠物寄养",
    "city": "天津",
    "target_customer": "天津本地宠物主人",
    "account_style": "科普类",
    "price_range": "",
    "customer_type": "个人"
})

assert project_id > 0, "项目创建失败"

session_id = data_storage.create_agent_session({
    "project_id": project_id,
    "route": "personal_account",
    "user_input": "我是做宠物寄养的，主要服务天津本地宠物主人，账号风格是科普类",
    "parsed_profile": {
        "profile": {
            "project_name": f"第十部分总体验收项目_{suffix}",
            "route": "personal_account",
            "industry": "宠物服务",
            "service": "宠物寄养",
            "city": "天津",
            "target_customer": "天津本地宠物主人",
            "account_style": "科普类",
            "price_range": "",
            "customer_type": "个人"
        },
        "missing_fields": [],
        "questions": [],
        "is_ready": True
    },
    "status": "ready",
    "progress": 30
})

assert session_id > 0, "Agent 会话创建失败"

approved_term = f"安心寄养验收词_{suffix}"
rejected_term = f"拒绝方向验收词_{suffix}"
low_evidence_term = f"低证据验收词_{suffix}"

saved_count = data_storage.save_candidate_terms([
    {
        "session_id": session_id,
        "project_id": project_id,
        "industry": "宠物服务",
        "term": approved_term,
        "term_type": "selling_point",
        "reason": "两条证据均指向用户关注寄养安全感",
        "evidence_count": 2,
        "evidence_samples": [
            "证据1：评论提到害怕宠物寄养不适应",
            "证据2：评论提到希望每天看到宠物状态"
        ],
        "review_status": "pending"
    },
    {
        "session_id": session_id,
        "project_id": project_id,
        "industry": "宠物服务",
        "term": rejected_term,
        "term_type": "content_direction",
        "reason": "用于验证拒绝项不会进入正式库",
        "evidence_count": 2,
        "evidence_samples": [
            "证据1：测试拒绝项",
            "证据2：测试拒绝项"
        ],
        "review_status": "pending"
    },
    {
        "session_id": session_id,
        "project_id": project_id,
        "industry": "宠物服务",
        "term": low_evidence_term,
        "term_type": "pain_point",
        "reason": "用于验证证据数不足不能通过",
        "evidence_count": 1,
        "evidence_samples": [
            "证据1：只有一条证据"
        ],
        "review_status": "pending"
    }
])

assert saved_count == 3, f"候选词写入数量错误: {saved_count}"

pending_resp = client.get(
    "/api/agent/candidate_terms",
    params={
        "review_status": "pending",
        "project_id": project_id,
        "limit": 100
    }
)
pending_json = pending_resp.json()
assert pending_json["status"] == "success", pending_json

pending_items = pending_json["data"]
approved_candidate = next((item for item in pending_items if item["term"] == approved_term), None)
rejected_candidate = next((item for item in pending_items if item["term"] == rejected_term), None)
low_evidence_candidate = next((item for item in pending_items if item["term"] == low_evidence_term), None)

assert approved_candidate is not None, "未读取到待通过候选项"
assert rejected_candidate is not None, "未读取到待拒绝候选项"
assert low_evidence_candidate is not None, "未读取到低证据候选项"

approved_before_resp = client.get(
    "/api/agent/approved_terms",
    params={
        "project_id": project_id,
        "limit": 100
    }
)
approved_before_json = approved_before_resp.json()
assert approved_before_json["status"] == "success", approved_before_json

approved_before_terms = [item["term"] for item in approved_before_json["data"]]
assert approved_term not in approved_before_terms, "候选项在人工通过前已经进入正式库"
assert rejected_term not in approved_before_terms, "拒绝测试项在审核前已经进入正式库"
assert low_evidence_term not in approved_before_terms, "低证据项在审核前已经进入正式库"

low_evidence_resp = client.post(
    "/api/agent/candidate_terms/approve",
    json={
        "candidate_id": low_evidence_candidate["id"],
        "mapping": {
            "usage": "这条不能通过"
        }
    }
)
low_evidence_json = low_evidence_resp.json()
assert low_evidence_json["status"] == "error", low_evidence_json

approve_resp = client.post(
    "/api/agent/candidate_terms/approve",
    json={
        "candidate_id": approved_candidate["id"],
        "mapping": {
            "usage": "下一次 Agent 任务参考",
            "style": "强调安全感、每日反馈、透明寄养流程"
        }
    }
)
approve_json = approve_resp.json()
assert approve_json["status"] == "success", approve_json

reject_resp = client.post(
    "/api/agent/candidate_terms/reject",
    json={
        "candidate_id": rejected_candidate["id"]
    }
)
reject_json = reject_resp.json()
assert reject_json["status"] == "success", reject_json

approved_after_resp = client.get(
    "/api/agent/approved_terms",
    params={
        "project_id": project_id,
        "limit": 100
    }
)
approved_after_json = approved_after_resp.json()
assert approved_after_json["status"] == "success", approved_after_json

approved_after_items = approved_after_json["data"]
approved_after_terms = [item["term"] for item in approved_after_items]

assert approved_term in approved_after_terms, "通过项未进入正式库"
assert rejected_term not in approved_after_terms, "拒绝项错误进入正式库"
assert low_evidence_term not in approved_after_terms, "低证据项错误进入正式库"

memory_items = [
    {
        "memory_type": "preferred_title_style",
        "content": f"标题偏好：多用避坑、清单、真实流程，不要空泛口号_{suffix}"
    },
    {
        "memory_type": "rejected_content_direction",
        "content": f"拒绝方向：不要写夸张收益、不要承诺宠物一定不应激_{suffix}"
    },
    {
        "memory_type": "account_style",
        "content": f"账号风格：温和、专业、科普、真实案例_{suffix}"
    },
    {
        "memory_type": "accepted_selling_point",
        "content": f"接受卖点：每日视频反馈、无笼养、环境透明_{suffix}"
    }
]

for item in memory_items:
    memory_resp = client.post(
        "/api/agent/project_memory",
        json={
            "project_id": project_id,
            "memory_type": item["memory_type"],
            "content": item["content"],
            "source_session_id": session_id
        }
    )
    memory_json = memory_resp.json()
    assert memory_json["status"] == "success", memory_json

memory_list_resp = client.get(
    "/api/agent/project_memory",
    params={
        "project_id": project_id,
        "limit": 100
    }
)
memory_list_json = memory_list_resp.json()
assert memory_list_json["status"] == "success", memory_list_json

memory_contents = [item["content"] for item in memory_list_json["data"]]
for item in memory_items:
    assert item["content"] in memory_contents, f"项目记忆未写入: {item['content']}"

service = AIAgentService(storage=data_storage)

profile = {
    "project_name": f"第十部分总体验收项目_{suffix}",
    "route": "personal_account",
    "industry": "宠物服务",
    "service": "宠物寄养",
    "city": "天津",
    "target_customer": "天津本地宠物主人",
    "account_style": "科普类",
    "price_range": "",
    "customer_type": "个人"
}

profile_with_context = service._build_profile_with_review_context(
    profile=profile,
    project_id=project_id
)

assert "approved_terms_reference" in profile_with_context, "AI 输入缺少正式库参考"
assert "project_memory_reference" in profile_with_context, "AI 输入缺少项目记忆参考"
assert "agent_reference_rules" in profile_with_context, "AI 输入缺少参考规则"

approved_reference_terms = [
    item.get("term")
    for item in profile_with_context["approved_terms_reference"]
]

memory_reference_contents = [
    item.get("content")
    for item in profile_with_context["project_memory_reference"]
]

assert approved_term in approved_reference_terms, "正式库未进入下一次 Agent 输入参考"
assert rejected_term not in approved_reference_terms, "拒绝项进入了下一次 Agent 输入参考"
assert low_evidence_term not in approved_reference_terms, "低证据项进入了下一次 Agent 输入参考"

for item in memory_items:
    assert item["content"] in memory_reference_contents, f"项目记忆未进入 Agent 输入参考: {item['content']}"

rules_text = "\n".join(profile_with_context["agent_reference_rules"])
assert "正式库" in rules_text, "参考规则缺少正式库说明"
assert "项目记忆" in rules_text, "参考规则缺少项目记忆说明"
assert "不能替代本次采集数据" in rules_text, "参考规则缺少数据依据约束"

print("project_id =", project_id)
print("session_id =", session_id)
print("approved_term =", approved_term)
print("rejected_term =", rejected_term)
print("low_evidence_term =", low_evidence_term)
print("第十部分 10E 总体验收通过")