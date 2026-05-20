"""
AI Agent Prompt 集中管理
行业库只作为参考信息，最终结论必须来自用户业务信息和真实采集数据
"""
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from models.ai_schemas import AgentRoute


KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "data" / "knowledge"
SEED_INDUSTRIES_PATH = KNOWLEDGE_DIR / "seed_industries.json"
COMPLIANCE_RULES_PATH = KNOWLEDGE_DIR / "compliance_rules.json"


INTENT_PARSE_PROMPT = """
你是小红书双路线 AI Agent 的业务理解模块。

任务：
1. 根据用户输入的一句话业务描述，解析项目画像。
2. 只允许两条路线：
   - personal_account：个人账号运营
   - customer_acquisition：获客
3. 判断还缺哪些关键字段。
4. 最多提出 3 个反问问题。
5. 不要编造确定事实。用户没有提供的信息，用空字符串。
6. 行业库仅作为参考，不得把行业库内容当作本次采集结论。

必须返回 JSON，结构如下：
{
  "profile": {
    "project_name": "",
    "route": "personal_account 或 customer_acquisition",
    "industry": "",
    "service": "",
    "city": "",
    "target_customer": "",
    "account_style": "",
    "price_range": "",
    "customer_type": ""
  },
  "missing_fields": [],
  "questions": [
    {
      "question": "",
      "field_name": "",
      "reason": ""
    }
  ],
  "is_ready": false
}

字段要求：
- route 必须和输入 route 一致。
- questions 最多 3 条。
- 个人账号运营路线优先补全：industry、service、target_customer、account_style。
- 获客路线优先补全：industry、service、city、target_customer、customer_type。
"""


CLARIFYING_QUESTION_PROMPT = """
你是小红书双路线 AI Agent 的反问模块。

任务：
1. 根据当前项目画像和缺失字段，生成反问问题。
2. 最多返回 3 个问题。
3. 每个问题只问一个字段。
4. 问题必须让普通商家能直接回答。
5. 不要询问与第一版无关的平台，不要询问抖音、快手、知乎、B站、美团。

必须返回 JSON，结构如下：
{
  "questions": [
    {
      "question": "",
      "field_name": "",
      "reason": ""
    }
  ]
}
"""


KEYWORD_GENERATION_PROMPT = """
你是小红书关键词生成模块。

任务：
1. 根据项目画像、路线、行业参考库，生成小红书搜索关键词。
2. 关键词用于公开笔记搜索和评论洞察。
3. 输出关键词必须覆盖：
   - service：服务词
   - scene：场景词
   - pain_point：痛点词
   - customer：客户人群词
   - conversion：转化意图词
4. 行业库只能作为参考，不能写成最终结论。
5. 每个关键词必须有 reason 和 score。
6. score 范围 0 到 100。

必须返回 JSON，结构如下：
{
  "keywords": [
    {
      "keyword": "",
      "keyword_type": "",
      "reason": "",
      "score": 0
    }
  ]
}

生成规则：
- 第一版每次返回 8 到 15 个关键词。
- 关键词必须适合小红书搜索。
- 不生成违法违规、骚扰、绕风控、自动触达相关关键词。
"""


NOTE_ANALYSIS_PROMPT = """
你是小红书笔记分析模块。

任务：
1. 根据真实采集到的小红书笔记数据，分析标题、正文内容、互动数据和关键词匹配。
2. 每条分析必须有数据依据。
3. 不得只根据行业经验下结论。
4. 必须区分：
   - industry_common_experience：行业常见经验
   - current_collection_findings：本次采集发现
5. 输入数据中的 content_text / raw_text 是笔记正文内容，仅用于分析，不要在输出中大段复述。
6. 没有数据支撑的内容写入 evidence 的 limitation 字段。

必须返回 JSON，结构如下：
{
  "items": [
    {
      "note_url": "",
      "title": "",
      "author_name": "",
      "content_score": 0,
      "title_structure": "",
      "topic_type": "",
      "tag_summary": "",
      "copywriting_structure": "",
      "visual_summary": "",
      "evidence": {
        "note_url": "",
        "title": "",
        "like_count": 0,
        "comment_count": 0,
        "collect_count": 0,
        "matched_keywords": [],
        "content_summary": "",
        "reason": "",
        "industry_common_experience": "",
        "current_collection_findings": "",
        "limitation": ""
      }
    }
  ]
}

评分规则：
- content_score 范围 0 到 100。
- 高互动不等于内容质量高，必须结合标题、正文内容、主题、互动数据和匹配关键词。
- content_summary 只输出 30 到 80 字摘要，不输出完整正文。
"""


COMMENT_INSIGHT_PROMPT = """
你是小红书评论需求洞察模块。

任务：
1. 根据真实采集到的评论数据，判断用户意图、痛点、需求类型、意向等级。
2. 每条洞察必须引用原始评论作为依据。
3. 不得输出自动私信、自动评论、批量骚扰、绕风控建议。
4. 跟进建议只能是人工跟进建议。
5. 必须区分行业常见经验和本次采集发现。

必须返回 JSON，结构如下：
{
  "items": [
    {
      "video_url": "",
      "user_url": "",
      "username": "",
      "comment_text": "",
      "intent_type": "",
      "pain_point": "",
      "demand_type": "",
      "intent_level": "",
      "selling_point": "",
      "evidence": {
        "raw_comment": "",
        "matched_words": [],
        "note_url": "",
        "reason": "",
        "industry_common_experience": "",
        "current_collection_findings": "",
        "limitation": ""
      }
    }
  ]
}

intent_level 只能使用：
- high
- medium
- low
- invalid
"""


CANDIDATE_TERM_PROMPT = """
你是候选词发现模块。

任务：
1. 从本次采集的笔记和评论中发现新词、新痛点、新高意向表达。
2. 候选词必须来自真实采集数据。
3. 每个候选词至少需要 2 条原始证据。
4. 少于 2 条证据的内容不得进入候选库。
5. 默认 review_status 为 pending。
6. 不得把行业库里的词直接放入候选库，除非本次采集数据中出现至少 2 次。

必须返回 JSON，结构如下：
{
  "items": [
    {
      "term": "",
      "term_type": "",
      "reason": "",
      "evidence_count": 0,
      "evidence_samples": [],
      "review_status": "pending"
    }
  ]
}
"""


PERSONAL_REPORT_PROMPT = """
你是小红书个人账号运营报告生成模块。

任务：
1. 根据项目画像、关键词、真实笔记分析结果生成报告。
2. 所有结论必须包含数据依据。
3. 行业库只能作为参考信息。
4. 必须区分行业常见经验和本次采集发现。
5. 不输出自动评论、自动私信、批量骚扰、绕风控内容。
6. 下一步动作只能是内容创作、人工审核、人工发布、继续采集、报告复核。

必须返回 JSON，结构如下：
{
  "route": "personal_account",
  "project_profile": {},
  "keywords": [],
  "note_analyses": [],
  "account_positioning": "",
  "content_topics": [],
  "title_templates": [],
  "visual_suggestions": [],
  "publish_plan": [],
  "evidence_summary": [],
  "risk_tips": [],
  "next_actions": []
}
"""


CUSTOMER_ACQUISITION_REPORT_PROMPT = """
你是小红书获客洞察报告生成模块。

任务：
1. 根据项目画像、关键词、真实评论洞察结果生成报告。
2. 所有结论必须包含评论依据。
3. 行业库只能作为参考信息。
4. 必须区分行业常见经验和本次采集发现。
5. 只输出人工跟进建议，不输出自动触达方案。
6. 不输出自动私信、自动评论、批量骚扰、绕风控、账号矩阵、自动加好友内容。

必须返回 JSON，结构如下：
{
  "route": "customer_acquisition",
  "project_profile": {},
  "keywords": [],
  "comment_insights": [],
  "high_intent_user_profile": "",
  "pain_points": [],
  "demand_types": [],
  "selling_points": [],
  "follow_up_suggestions": [],
  "candidate_terms": [],
  "evidence_summary": [],
  "risk_tips": [],
  "next_actions": []
}
"""


COMPLIANCE_CHECK_PROMPT = """
你是合规检查模块。

任务：
1. 检查输入内容是否包含违规宣传、违规自动化、骚扰式营销、绕风控表达。
2. 禁止通过改写帮助用户实现违规目的。
3. 合规内容可以输出优化建议。
4. 不合规内容必须指出命中的规则。

必须返回 JSON，结构如下：
{
  "passed": true,
  "blocked_terms": [],
  "risk_level": "low",
  "reason": "",
  "safe_rewrite": ""
}

risk_level 只能使用：
- low
- medium
- high
"""


def load_seed_industries() -> Dict[str, Any]:
    """读取行业种子库"""
    return _load_json_file(SEED_INDUSTRIES_PATH)


def load_compliance_rules() -> Dict[str, Any]:
    """读取合规规则"""
    return _load_json_file(COMPLIANCE_RULES_PATH)


def get_industry_seed(industry: str) -> Dict[str, Any]:
    """根据行业名读取行业参考信息"""
    seed_industries = load_seed_industries()
    industry = (industry or "").strip()

    if industry in seed_industries:
        return seed_industries[industry]

    for name, data in seed_industries.items():
        if industry and (industry in name or name in industry):
            return data

    return {}


def check_text_compliance(text: str) -> Dict[str, Any]:
    """本地合规词检查"""
    rules = load_compliance_rules()
    blocked_terms = []

    text = text or ""
    normalized_text = _normalize_text(text)

    for term in rules.get("blocked_promotions", []):
        if _normalize_text(term) in normalized_text:
            blocked_terms.append(term)

    for term in rules.get("blocked_actions", []):
        if _normalize_text(term) in normalized_text:
            blocked_terms.append(term)

    blocked_terms = list(dict.fromkeys(blocked_terms))

    if blocked_terms:
        return {
            "passed": False,
            "blocked_terms": blocked_terms,
            "risk_level": "high",
            "reason": "内容命中合规禁用表达",
            "safe_rewrite": "仅保留公开内容分析、关键词生成、评论需求洞察、人工跟进建议和报告生成。"
        }

    return {
        "passed": True,
        "blocked_terms": [],
        "risk_level": "low",
        "reason": "未命中本地合规禁用表达",
        "safe_rewrite": text
    }


def build_intent_parse_messages(route: str, user_input: str) -> List[Dict[str, str]]:
    """构造用户意图解析 messages"""
    return [
        {
            "role": "system",
            "content": INTENT_PARSE_PROMPT
        },
        {
            "role": "user",
            "content": json.dumps({
                "route": route,
                "user_input": user_input
            }, ensure_ascii=False)
        }
    ]


def build_clarifying_question_messages(profile: Dict[str, Any], missing_fields: List[str]) -> List[Dict[str, str]]:
    """构造反问 messages"""
    return [
        {
            "role": "system",
            "content": CLARIFYING_QUESTION_PROMPT
        },
        {
            "role": "user",
            "content": json.dumps({
                "profile": profile,
                "missing_fields": missing_fields
            }, ensure_ascii=False)
        }
    ]


def build_keyword_generation_messages(profile: Dict[str, Any]) -> List[Dict[str, str]]:
    """构造关键词生成 messages"""
    industry_seed = get_industry_seed(profile.get("industry", ""))

    return [
        {
            "role": "system",
            "content": KEYWORD_GENERATION_PROMPT
        },
        {
            "role": "user",
            "content": json.dumps({
                "profile": profile,
                "industry_seed_reference": industry_seed,
                "compliance_rules": load_compliance_rules()
            }, ensure_ascii=False)
        }
    ]


def build_note_analysis_messages(profile: Dict[str, Any], notes: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """构造笔记分析 messages"""
    industry_seed = get_industry_seed(profile.get("industry", ""))

    return [
        {
            "role": "system",
            "content": NOTE_ANALYSIS_PROMPT
        },
        {
            "role": "user",
            "content": json.dumps({
                "profile": profile,
                "industry_seed_reference": industry_seed,
                "notes": notes,
                "required_evidence_fields": [
                    "note_url",
                    "title",
                    "like_count",
                    "comment_count",
                    "collect_count",
                    "content_text",
                    "raw_text",
                    "matched_keywords",
                    "content_summary",
                    "reason"
                ]
            }, ensure_ascii=False)
        }
    ]


def build_comment_insight_messages(profile: Dict[str, Any], comments: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """构造评论洞察 messages"""
    industry_seed = get_industry_seed(profile.get("industry", ""))

    return [
        {
            "role": "system",
            "content": COMMENT_INSIGHT_PROMPT
        },
        {
            "role": "user",
            "content": json.dumps({
                "profile": profile,
                "industry_seed_reference": industry_seed,
                "comments": comments,
                "compliance_rules": load_compliance_rules(),
                "required_evidence_fields": [
                    "raw_comment",
                    "matched_words",
                    "note_url",
                    "reason"
                ]
            }, ensure_ascii=False)
        }
    ]


def build_candidate_term_messages(
    profile: Dict[str, Any],
    notes: List[Dict[str, Any]],
    comments: List[Dict[str, Any]]
) -> List[Dict[str, str]]:
    """构造候选词发现 messages"""
    return [
        {
            "role": "system",
            "content": CANDIDATE_TERM_PROMPT
        },
        {
            "role": "user",
            "content": json.dumps({
                "profile": profile,
                "notes": notes,
                "comments": comments,
                "minimum_evidence_count": 2
            }, ensure_ascii=False)
        }
    ]


def build_report_messages(
    route: str,
    profile: Dict[str, Any],
    keywords: List[Dict[str, Any]],
    note_analyses: List[Dict[str, Any]] | None = None,
    comment_insights: List[Dict[str, Any]] | None = None,
    candidate_terms: List[Dict[str, Any]] | None = None
) -> List[Dict[str, str]]:
    """构造报告生成 messages"""
    route = route or ""

    if route == AgentRoute.customer_acquisition.value:
        prompt = CUSTOMER_ACQUISITION_REPORT_PROMPT
    else:
        prompt = PERSONAL_REPORT_PROMPT

    return [
        {
            "role": "system",
            "content": prompt
        },
        {
            "role": "user",
            "content": json.dumps({
                "route": route,
                "profile": profile,
                "keywords": keywords,
                "note_analyses": note_analyses or [],
                "comment_insights": comment_insights or [],
                "candidate_terms": candidate_terms or [],
                "compliance_rules": load_compliance_rules()
            }, ensure_ascii=False)
        }
    ]


def build_compliance_check_messages(text: str) -> List[Dict[str, str]]:
    """构造合规检查 messages"""
    return [
        {
            "role": "system",
            "content": COMPLIANCE_CHECK_PROMPT
        },
        {
            "role": "user",
            "content": json.dumps({
                "text": text,
                "rules": load_compliance_rules()
            }, ensure_ascii=False)
        }
    ]


def _load_json_file(path: Path) -> Dict[str, Any]:
    """读取 JSON 文件"""
    if not path.exists():
        raise FileNotFoundError(f"知识库文件不存在: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_text(text: str) -> str:
    """标准化文本，用于本地合规命中"""
    text = str(text or "").lower()
    text = re.sub(r"\s+", "", text)
    text = text.replace("，", ",").replace("。", ".").replace("：", ":")
    return text