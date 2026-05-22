"""
AI Agent 核心服务
负责：用户输入解析、反问补全、关键词生成、笔记分析、评论洞察、候选词发现、报告生成
"""
import json
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

from models.ai_schemas import (
    AgentRoute,
    AgentSessionStatus,
    ProfileParseResult,
    ClarifyingQuestion,
    ClarifyingQuestionResult,
    KeywordGenerationResult,
    NoteAnalysisResult,
    CommentInsightResult,
    CandidateTermResult,
    PersonalAccountReport,
    CustomerAcquisitionReport,
)
from services.ai_provider import AIProvider
from services.ai_prompts import (
    check_text_compliance,
    build_intent_parse_messages,
    build_clarifying_question_messages,
    build_keyword_generation_messages,
    build_note_analysis_messages,
    build_comment_insight_messages,
    build_candidate_term_messages,
    build_report_messages,
)
from utils.data_storage import data_storage


class AIAgentService:
    """AI Agent 核心服务"""

    def __init__(self, ai_provider: Optional[AIProvider] = None, storage=None):
        self.ai_provider = ai_provider or AIProvider()
        self.storage = storage or data_storage

    async def start_session(self, route: str, user_input: str) -> Dict[str, Any]:
        """启动 Agent 会话：解析用户输入，必要时反问，信息完整时生成关键词"""
        route_value = self._normalize_route(route)
        user_input = (user_input or "").strip()

        if not user_input:
            return {
                "status": "error",
                "message": "用户输入不能为空"
            }

        compliance_result = check_text_compliance(user_input)
        if not compliance_result.get("passed"):
            return {
                "status": "error",
                "message": "用户输入命中合规限制",
                "compliance": compliance_result
            }

        ai_result = await self.ai_provider.chat_json(
            build_intent_parse_messages(route_value, user_input)
        )

        parse_result = self._validate_model(ProfileParseResult, ai_result)
        profile_dict = self._model_to_dict(parse_result.profile)
        profile_dict["route"] = route_value

        parse_result_dict = self._model_to_dict(parse_result)
        parse_result_dict["profile"] = profile_dict

        is_ready = bool(parse_result_dict.get("is_ready")) and not parse_result_dict.get("questions")
        session_status = (
            AgentSessionStatus.ready.value
            if is_ready
            else AgentSessionStatus.need_clarify.value
        )

        project_id = self.storage.save_project(profile_dict)

        session_id = self.storage.create_agent_session({
            "project_id": project_id,
            "route": route_value,
            "user_input": user_input,
            "parsed_profile": parse_result_dict,
            "status": session_status,
            "progress": 10,
        })

        response = {
            "status": "success",
            "session_id": session_id,
            "project_id": project_id,
            "session_status": session_status,
            "profile": profile_dict,
            "missing_fields": parse_result_dict.get("missing_fields", []),
            "questions": parse_result_dict.get("questions", []),
            "keywords": []
        }

        if session_status == AgentSessionStatus.ready.value:
            response["keywords"] = await self.prepare_keywords(session_id)

        return response

    async def answer_clarifying_questions(self, session_id: int, answers: Dict[str, str]) -> Dict[str, Any]:
        """保存用户反问答案，重新判断是否可进入执行"""
        session = self.storage.get_agent_session(session_id)
        if not session:
            return {
                "status": "error",
                "message": "Agent 会话不存在"
            }

        route_value = self._normalize_route(session.get("route", ""))
        parse_result_data = self._json_loads(session.get("parsed_profile_json", ""))

        profile = parse_result_data.get("profile", {})
        if not isinstance(profile, dict):
            profile = {}

        for field_name, value in (answers or {}).items():
            field_name = str(field_name or "").strip()
            if field_name in profile:
                profile[field_name] = str(value or "").strip()

        profile["route"] = route_value

        missing_fields = self._get_required_missing_fields(route_value, profile)

        if missing_fields:
            question_result = await self._build_next_questions(profile, missing_fields)
            session_status = AgentSessionStatus.need_clarify.value
            is_ready = False
        else:
            question_result = []
            session_status = AgentSessionStatus.ready.value
            is_ready = True

        parse_result_data = {
            "profile": profile,
            "missing_fields": missing_fields,
            "questions": question_result,
            "is_ready": is_ready
        }

        project_id = int(session.get("project_id") or 0)
        if project_id > 0:
            self._update_project_fields(project_id, profile)
        else:
            project_id = self.storage.save_project(profile)
            self._set_agent_session_project_id(session_id, project_id)

        self.storage.update_agent_session(
            session_id=session_id,
            status=session_status,
            progress=20 if is_ready else 15,
            parsed_profile=parse_result_data
        )

        response = {
            "status": "success",
            "session_id": session_id,
            "project_id": project_id,
            "session_status": session_status,
            "profile": profile,
            "missing_fields": missing_fields,
            "questions": question_result,
            "keywords": []
        }

        if is_ready:
            response["keywords"] = await self.prepare_keywords(session_id)

        return response

    async def prepare_keywords(self, session_id: int, force: bool = False) -> List[Dict[str, Any]]:
        """生成并保存关键词"""
        if not force:
            exists = self.storage.get_generated_keywords(session_id)
            if exists:
                return exists

        session = self.storage.get_agent_session(session_id)
        if not session:
            raise RuntimeError("Agent 会话不存在")

        profile = self._get_profile_from_session(session)
        if not profile:
            raise RuntimeError("Agent 会话缺少项目画像")

        project_id = int(session.get("project_id") or 0)
        profile_for_prompt = self._build_profile_with_review_context(
            profile=profile,
            project_id=project_id
        )

        ai_result = await self.ai_provider.chat_json(
            build_keyword_generation_messages(profile_for_prompt)
        )

        keyword_result = self._validate_model(KeywordGenerationResult, ai_result)
        keywords = [self._model_to_dict(item) for item in keyword_result.keywords]

        self.storage.save_generated_keywords(session_id, keywords)
        self.storage.update_agent_session(
            session_id=session_id,
            status=AgentSessionStatus.ready.value,
            progress=30
        )

        return self.storage.get_generated_keywords(session_id)

    async def analyze_notes(self, session_id: int, notes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """分析小红书笔记"""
        if not notes:
            return []

        session = self.storage.get_agent_session(session_id)
        if not session:
            raise RuntimeError("Agent 会话不存在")

        profile = self._get_profile_from_session(session)
        project_id = int(session.get("project_id") or 0)
        profile_for_prompt = self._build_profile_with_review_context(
            profile=profile,
            project_id=project_id
        )

        ai_result = await self.ai_provider.chat_json(
            build_note_analysis_messages(profile_for_prompt, notes)
        )

        note_result = self._validate_model(NoteAnalysisResult, ai_result)
        items = [self._model_to_dict(item) for item in note_result.items]
        items = self._ensure_note_analysis_items(notes, items)

        saved_count = self.storage.save_xhs_note_analysis(session_id, items)
        if saved_count <= 0 and items:
            raise RuntimeError("小红书笔记分析结果保存失败")

        self.storage.update_agent_session(
            session_id=session_id,
            status=AgentSessionStatus.running.value,
            progress=60
        )

        return items

    async def analyze_comments(self, session_id: int, comments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """分析小红书评论"""
        if not comments:
            return []

        session = self.storage.get_agent_session(session_id)
        if not session:
            raise RuntimeError("Agent 会话不存在")

        profile = self._get_profile_from_session(session)
        project_id = int(session.get("project_id") or 0)
        profile_for_prompt = self._build_profile_with_review_context(
            profile=profile,
            project_id=project_id
        )

        ai_result = await self.ai_provider.chat_json(
            build_comment_insight_messages(profile_for_prompt, comments)
        )

        comment_result = self._validate_model(CommentInsightResult, ai_result)
        items = [self._model_to_dict(item) for item in comment_result.items]

        self.storage.save_xhs_comment_insights(session_id, items)
        self.storage.update_agent_session(
            session_id=session_id,
            status=AgentSessionStatus.running.value,
            progress=60
        )

        return items

    async def discover_candidate_terms(
        self,
        session_id: int,
        notes: List[Dict[str, Any]],
        comments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """发现候选词，证据数量少于 2 的候选词不入库"""
        session = self.storage.get_agent_session(session_id)
        if not session:
            raise RuntimeError("Agent 会话不存在")

        profile = self._get_profile_from_session(session)
        project_id = int(session.get("project_id") or 0)
        profile_for_prompt = self._build_profile_with_review_context(
            profile=profile,
            project_id=project_id
        )

        ai_result = await self.ai_provider.chat_json(
            build_candidate_term_messages(profile_for_prompt, notes, comments)
        )

        candidate_result = self._validate_model(CandidateTermResult, ai_result)

        items = []
        for item in candidate_result.items:
            item_dict = self._model_to_dict(item)
            if int(item_dict.get("evidence_count") or 0) < 2:
                continue

            item_dict["session_id"] = session_id
            item_dict["project_id"] = project_id
            item_dict["industry"] = profile.get("industry", "")
            item_dict["review_status"] = "pending"
            items.append(item_dict)

        self.storage.save_candidate_terms(items)

        return items

    async def generate_report(
        self,
        session_id: int,
        note_analyses: Optional[List[Dict[str, Any]]] = None,
        comment_insights: Optional[List[Dict[str, Any]]] = None,
        candidate_terms: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """生成并保存 Agent 报告"""
        session = self.storage.get_agent_session(session_id)
        if not session:
            raise RuntimeError("Agent 会话不存在")

        route_value = self._normalize_route(session.get("route", ""))
        profile = self._get_profile_from_session(session)
        project_id = int(session.get("project_id") or 0)
        keywords = self.storage.get_generated_keywords(session_id)
        profile_for_prompt = self._build_profile_with_review_context(
            profile=profile,
            project_id=project_id
        )

        ai_result = await self.ai_provider.chat_json(
            build_report_messages(
                route=route_value,
                profile=profile_for_prompt,
                keywords=keywords,
                note_analyses=note_analyses or [],
                comment_insights=comment_insights or [],
                candidate_terms=candidate_terms or []
            )
        )

        ai_result = self._normalize_report_ai_result(
            route_value=route_value,
            profile=profile,
            keywords=keywords,
            ai_result=ai_result,
            note_analyses=note_analyses or [],
            comment_insights=comment_insights or [],
            candidate_terms=candidate_terms or []
        )

        if route_value == AgentRoute.customer_acquisition.value:
            report_model = self._validate_model(CustomerAcquisitionReport, ai_result)
        else:
            report_model = self._validate_model(PersonalAccountReport, ai_result)

        report_json = self._model_to_dict(report_model)
        report_markdown = report_model.to_markdown()

        report_id = self.storage.save_agent_report(
            session_id=session_id,
            project_id=project_id,
            route=route_value,
            report_json=report_json,
            report_markdown=report_markdown
        )

        self.storage.update_agent_session(
            session_id=session_id,
            status=AgentSessionStatus.finished.value,
            progress=100,
            completed=True
        )

        return {
            "status": "success",
            "session_id": session_id,
            "project_id": project_id,
            "report_id": report_id,
            "route": route_value,
            "report_json": report_json,
            "report_markdown": report_markdown
        }

    async def run(
        self,
        session_id: int,
        notes: Optional[List[Dict[str, Any]]] = None,
        comments: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """执行 Agent 核心流程，不直接启动浏览器采集"""
        session = self.storage.get_agent_session(session_id)
        if not session:
            return {
                "status": "error",
                "message": "Agent 会话不存在"
            }

        route_value = self._normalize_route(session.get("route", ""))

        self.storage.update_agent_session(
            session_id=session_id,
            status=AgentSessionStatus.running.value,
            progress=40
        )

        keywords = await self.prepare_keywords(session_id)

        if route_value == AgentRoute.personal_account.value:
            if not notes:
                self.storage.update_agent_session(
                    session_id=session_id,
                    status=AgentSessionStatus.ready.value,
                    progress=40
                )
                return {
                    "status": "need_data",
                    "message": "个人账号运营路线需要先提供小红书笔记采集数据",
                    "session_id": session_id,
                    "keywords": keywords
                }

            note_analyses = await self.analyze_notes(session_id, notes)
            return await self.generate_report(
                session_id=session_id,
                note_analyses=note_analyses,
                comment_insights=[],
                candidate_terms=[]
            )

        if route_value == AgentRoute.customer_acquisition.value:
            if not comments:
                self.storage.update_agent_session(
                    session_id=session_id,
                    status=AgentSessionStatus.ready.value,
                    progress=40
                )
                return {
                    "status": "need_data",
                    "message": "获客路线需要先提供小红书评论采集数据",
                    "session_id": session_id,
                    "keywords": keywords
                }

            comment_insights = await self.analyze_comments(session_id, comments)
            candidate_terms = await self.discover_candidate_terms(
                session_id=session_id,
                notes=notes or [],
                comments=comments
            )
            return await self.generate_report(
                session_id=session_id,
                note_analyses=[],
                comment_insights=comment_insights,
                candidate_terms=candidate_terms
            )

        return {
            "status": "error",
            "message": f"不支持的路线: {route_value}"
        }

    def get_session(self, session_id: int) -> Dict[str, Any]:
        """读取 Agent 会话"""
        session = self.storage.get_agent_session(session_id)
        if not session:
            return {}

        result = dict(session)
        result["parsed_profile"] = self._json_loads(result.get("parsed_profile_json", ""))
        result["keywords"] = self.storage.get_generated_keywords(session_id)
        result["report"] = self.storage.get_agent_report(session_id)
        return result

    def get_report(self, session_id: int) -> Dict[str, Any]:
        """读取 Agent 报告"""
        report = self.storage.get_agent_report(session_id)
        if not report:
            return {}

        result = dict(report)
        result["report_json_data"] = self._json_loads(result.get("report_json", ""))
        return result
    def _ensure_note_analysis_items(
        self,
        notes: List[Dict[str, Any]],
        items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """确保笔记分析结果不为空，避免报告生成成功但 xhs_notes_analysis 未入库"""
        if items:
            return items

        fallback_items = []

        for note in notes:
            if not isinstance(note, dict):
                continue

            note_url = note.get("note_url") or note.get("video_url") or ""
            title = note.get("title") or note.get("video_desc") or ""
            author_name = note.get("author_name") or ""
            content_text = note.get("content_text") or note.get("raw_text") or ""
            keyword = note.get("keyword") or ""
            like_count = int(note.get("like_count") or 0)
            comment_count = int(note.get("comment_count") or 0)
            collect_count = int(note.get("collect_count") or 0)
            image_count = int(note.get("image_count") or 0)

            if not note_url and not title:
                continue

            content_score = 50
            if title:
                content_score += 10
            if content_text:
                content_score += 15
            if like_count > 0:
                content_score += 10
            if comment_count > 0:
                content_score += 10
            if collect_count > 0:
                content_score += 5

            content_score = min(100, content_score)

            matched_keywords = []
            if keyword:
                matched_keywords.append(keyword)

            content_summary = str(content_text or "").strip()
            if len(content_summary) > 80:
                content_summary = content_summary[:80]

            fallback_items.append({
                "note_url": note_url,
                "title": title,
                "author_name": author_name,
                "content_score": content_score,
                "title_structure": "标题信息 + 正文内容",
                "topic_type": "本次采集笔记",
                "tag_summary": keyword,
                "copywriting_structure": "标题 + 正文说明 + 标签",
                "visual_summary": f"图片数量：{image_count}" if image_count else "未采集到图片数量",
                "evidence": {
                    "note_url": note_url,
                    "title": title,
                    "like_count": like_count,
                    "comment_count": comment_count,
                    "collect_count": collect_count,
                    "matched_keywords": matched_keywords,
                    "content_summary": content_summary,
                    "reason": "AI 笔记分析返回空 items，系统已基于本次采集笔记生成兜底分析记录，确保分析结果可追溯。",
                    "industry_common_experience": "",
                    "current_collection_findings": "该记录来自当前 Agent session 绑定的小红书采集数据。",
                    "limitation": "该条为兜底分析记录，后续可继续优化 Prompt 提升模型返回稳定性。"
                }
            })

        return fallback_items
    def _normalize_report_ai_result(
        self,
        route_value: str,
        profile: Dict[str, Any],
        keywords: List[Dict[str, Any]],
        ai_result: Any,
        note_analyses: List[Dict[str, Any]],
        comment_insights: List[Dict[str, Any]],
        candidate_terms: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """标准化报告阶段 AI 返回结果，避免字段名不稳定导致 Pydantic 校验失败"""
        if not isinstance(ai_result, dict):
            ai_result = {}

        result = dict(ai_result)
        result["route"] = route_value
        result["project_profile"] = result.get("project_profile") or profile
        result["keywords"] = self._normalize_keyword_items(result.get("keywords") or keywords)

        if route_value == AgentRoute.customer_acquisition.value:
            result["comment_insights"] = self._normalize_comment_insight_items(
                result.get("comment_insights") or comment_insights
            )
            result["candidate_terms"] = self._normalize_candidate_term_items(
                result.get("candidate_terms") or candidate_terms
            )
            result["pain_points"] = self._normalize_string_list(result.get("pain_points"))
            result["demand_types"] = self._normalize_string_list(result.get("demand_types"))
            result["selling_points"] = self._normalize_string_list(result.get("selling_points"))
            result["follow_up_suggestions"] = self._normalize_string_list(result.get("follow_up_suggestions"))
            result["evidence_summary"] = self._normalize_string_list(result.get("evidence_summary"))
            result["risk_tips"] = self._normalize_string_list(result.get("risk_tips"))
            result["next_actions"] = self._normalize_string_list(result.get("next_actions"))

            if not isinstance(result.get("high_intent_user_profile"), str):
                result["high_intent_user_profile"] = self._format_report_item(result.get("high_intent_user_profile"))

            return result

        result["note_analyses"] = self._normalize_note_analysis_items(
            result.get("note_analyses") or note_analyses
        )
        result["content_topics"] = self._normalize_string_list(result.get("content_topics"))
        result["title_templates"] = self._normalize_string_list(result.get("title_templates"))
        result["visual_suggestions"] = self._normalize_string_list(result.get("visual_suggestions"))
        result["publish_plan"] = self._normalize_string_list(result.get("publish_plan"))
        result["evidence_summary"] = self._normalize_string_list(result.get("evidence_summary"))
        result["risk_tips"] = self._normalize_string_list(result.get("risk_tips"))
        result["next_actions"] = self._normalize_string_list(result.get("next_actions"))

        if not isinstance(result.get("account_positioning"), str):
            result["account_positioning"] = self._format_report_item(result.get("account_positioning"))

        result = self._ensure_personal_report_fields(
            result=result,
            profile=profile,
            keywords=result["keywords"],
            note_analyses=result["note_analyses"]
        )

        return result

    def _ensure_personal_report_fields(
        self,
        result: Dict[str, Any],
        profile: Dict[str, Any],
        keywords: List[Dict[str, Any]],
        note_analyses: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """补齐个人账号运营报告主体字段，避免 Markdown 报告出现大段暂无"""
        if not isinstance(result, dict):
            result = {}

        top_notes = self._get_top_note_analyses(note_analyses, limit=6)
        top_keywords = self._get_top_keywords(keywords, limit=8)

        if not str(result.get("account_positioning") or "").strip():
            result["account_positioning"] = self._build_personal_account_positioning(profile)

        if not result.get("content_topics"):
            result["content_topics"] = self._build_personal_content_topics(
                profile=profile,
                keywords=top_keywords,
                note_analyses=top_notes
            )

        if not result.get("title_templates"):
            result["title_templates"] = self._build_personal_title_templates(
                profile=profile,
                keywords=top_keywords,
                note_analyses=top_notes
            )

        if not result.get("visual_suggestions"):
            result["visual_suggestions"] = self._build_personal_visual_suggestions(
                profile=profile,
                note_analyses=top_notes
            )

        if not result.get("publish_plan"):
            result["publish_plan"] = self._build_personal_publish_plan(profile)

        if not result.get("evidence_summary"):
            result["evidence_summary"] = self._build_personal_evidence_summary(
                keywords=top_keywords,
                note_analyses=top_notes
            )

        risk_tips = self._normalize_string_list(result.get("risk_tips"))
        required_risk_tips = [
            "本报告仅基于当前 Agent session 绑定的小红书公开笔记、笔记正文摘要、互动数据和用户输入生成。",
            "所有内容草稿、标题和发布动作都需要人工审核后再执行。",
            "禁止自动私信、自动评论、批量骚扰、绕风控、账号矩阵、自动加好友等违规操作。",
            "涉及价格、效果、承诺、资质和案例时，需要以真实信息为准，不得夸大宣传。"
        ]
        for item in required_risk_tips:
            if item not in risk_tips:
                risk_tips.append(item)
        result["risk_tips"] = risk_tips

        if not result.get("next_actions"):
            result["next_actions"] = [
                "先从内容选题中选择 3 个方向，写成小红书笔记草稿。",
                "继续围绕高分关键词采集同类笔记，扩充本次报告的数据依据。",
                "人工复核标题模板、视觉建议和合规提示，再进入发布准备。",
                "发布后记录互动数据，下一轮 Agent 任务继续复盘标题、正文和评论反馈。"
            ]

        return result

    def _build_personal_account_positioning(self, profile: Dict[str, Any]) -> str:
        """构造个人账号运营路线的账号定位"""
        city = str(profile.get("city") or "").strip()
        industry = str(profile.get("industry") or "").strip()
        service = str(profile.get("service") or "").strip()
        target_customer = str(profile.get("target_customer") or "").strip()
        account_style = str(profile.get("account_style") or "").strip()

        parts = []
        if city:
            parts.append(city)
        if service:
            parts.append(service)
        elif industry:
            parts.append(industry)

        base = "".join(parts) if parts else "小红书业务"
        target_text = target_customer or "目标客户"
        style_text = account_style or "专业可信"

        return (
            f"{base}{style_text}账号，围绕{target_text}的真实需求，持续输出避坑、案例、流程、价格、"
            f"信任背书和常见问题解答内容，用公开笔记数据验证选题方向。"
        )

    def _build_personal_content_topics(
        self,
        profile: Dict[str, Any],
        keywords: List[Dict[str, Any]],
        note_analyses: List[Dict[str, Any]]
    ) -> List[str]:
        """根据关键词和笔记分析构造内容选题"""
        service = str(profile.get("service") or profile.get("industry") or "业务").strip()
        topics = []

        for item in note_analyses[:4]:
            title = str(item.get("title") or "").strip()
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            summary = str(evidence.get("content_summary") or "").strip()
            keyword_text = self._first_matched_keyword(evidence) or str(item.get("tag_summary") or "").strip()

            if title:
                if summary:
                    topics.append(f"复盘《{title}》：围绕“{keyword_text or service}”拆解用户真实需求和内容表达方式。")
                else:
                    topics.append(f"复盘《{title}》：拆解标题切入点、主题方向和可复用的内容结构。")

        for item in keywords:
            keyword = str(item.get("keyword") or "").strip()
            keyword_type = str(item.get("keyword_type") or "").strip()
            if not keyword:
                continue

            if keyword_type == "pain_point":
                topics.append(f"痛点解答：用户搜索“{keyword}”时最担心什么，以及你能如何解决。")
            elif keyword_type == "scene":
                topics.append(f"场景攻略：“{keyword}”场景下的准备清单、注意事项和避坑建议。")
            elif keyword_type == "customer":
                topics.append(f"人群内容：面向“{keyword}”制作一篇需求判断和服务选择指南。")
            else:
                topics.append(f"关键词选题：围绕“{keyword}”输出一篇专业科普或案例复盘。")

            if len(topics) >= 8:
                break

        return list(dict.fromkeys([item for item in topics if item]))[:8]

    def _build_personal_title_templates(
        self,
        profile: Dict[str, Any],
        keywords: List[Dict[str, Any]],
        note_analyses: List[Dict[str, Any]]
    ) -> List[str]:
        """根据关键词和高分笔记构造标题模板"""
        service = str(profile.get("service") or profile.get("industry") or "这件事").strip()
        title_templates = []

        for item in note_analyses[:3]:
            title = str(item.get("title") or "").strip()
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            keyword_text = self._first_matched_keyword(evidence) or str(item.get("tag_summary") or "").strip()
            if title:
                title_templates.append(f"《{title}》同类结构：用“真实问题 + 结果/疑问”切入，再承接“{keyword_text or service}”的解决方案。")

        for item in keywords:
            keyword = str(item.get("keyword") or "").strip()
            keyword_type = str(item.get("keyword_type") or "").strip()
            if not keyword:
                continue

            if keyword_type == "pain_point":
                title_templates.append(f"《{keyword}怎么办？先看这 3 个判断标准》")
            elif keyword_type == "scene":
                title_templates.append(f"《{keyword}前必须确认的 5 件事》")
            elif keyword_type == "conversion":
                title_templates.append(f"《想了解{service}？这篇把流程、价格和注意事项讲清楚》")
            else:
                title_templates.append(f"《{keyword}怎么选？新手先看这份避坑清单》")

            if len(title_templates) >= 8:
                break

        return list(dict.fromkeys([item for item in title_templates if item]))[:8]

    def _build_personal_visual_suggestions(
        self,
        profile: Dict[str, Any],
        note_analyses: List[Dict[str, Any]]
    ) -> List[str]:
        """根据笔记内容构造视觉建议"""
        service = str(profile.get("service") or profile.get("industry") or "服务").strip()
        suggestions = [
            f"封面优先突出“{service} + 用户痛点/场景”，用大字标题降低用户理解成本。",
            "正文配图优先展示真实流程、服务环境、操作步骤、案例结果、用户反馈或资质证明，增强信任感。",
            "每篇笔记至少准备 1 张封面、2 到 4 张过程图或证据图，避免只有口头描述。"
        ]

        for item in note_analyses[:3]:
            title = str(item.get("title") or "").strip()
            visual_summary = str(item.get("visual_summary") or "").strip()
            if title and visual_summary:
                suggestions.append(f"参考《{title}》的视觉信息：{visual_summary}，后续可补充更清晰的服务过程和证据画面。")

        return list(dict.fromkeys([item for item in suggestions if item]))[:6]

    def _build_personal_publish_plan(self, profile: Dict[str, Any]) -> List[str]:
        """构造个人账号运营发布计划"""
        service = str(profile.get("service") or profile.get("industry") or "业务").strip()

        return [
            f"第 1 周：发布 3 篇基础信任内容，分别讲清楚“{service}怎么选”“常见误区”“真实流程”。",
            "第 2 周：发布 3 篇场景内容，围绕用户搜索词拆解具体场景、准备清单和避坑建议。",
            "第 3 周：发布 2 篇案例复盘内容，展示真实问题、解决过程、结果反馈和注意事项。",
            "第 4 周：根据笔记互动数据复盘标题、封面和正文结构，保留高互动方向继续加采数据。"
        ]

    def _build_personal_evidence_summary(
        self,
        keywords: List[Dict[str, Any]],
        note_analyses: List[Dict[str, Any]]
    ) -> List[str]:
        """根据关键词和笔记分析构造数据依据"""
        evidence_summary = []

        for item in note_analyses[:8]:
            title = str(item.get("title") or "").strip()
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            like_count = int(evidence.get("like_count") or 0)
            comment_count = int(evidence.get("comment_count") or 0)
            collect_count = int(evidence.get("collect_count") or 0)
            matched_keywords = evidence.get("matched_keywords") if isinstance(evidence.get("matched_keywords"), list) else []
            content_summary = str(evidence.get("content_summary") or "").strip()

            if not title:
                continue

            keyword_text = "、".join([str(k) for k in matched_keywords if str(k).strip()]) or str(item.get("tag_summary") or "")
            summary_text = f"《{title}》｜赞 {like_count}｜评 {comment_count}｜藏 {collect_count}"
            if keyword_text:
                summary_text += f"｜命中关键词：{keyword_text}"
            if content_summary:
                summary_text += f"｜正文摘要：{content_summary}"
            evidence_summary.append(summary_text)

        if keywords:
            keyword_parts = []
            for item in keywords[:5]:
                keyword = str(item.get("keyword") or "").strip()
                score = self._to_float(item.get("score") or 0)
                if keyword:
                    keyword_parts.append(f"{keyword}({score:g})")
            if keyword_parts:
                evidence_summary.insert(0, f"本次关键词方向包括：{'、'.join(keyword_parts)}，用于指导采集和选题判断。")

        return evidence_summary[:10]

    def _get_top_note_analyses(self, items: Any, limit: int = 6) -> List[Dict[str, Any]]:
        """按内容分和互动数据选出重点笔记分析"""
        if not isinstance(items, list):
            return []

        normalized_items = self._normalize_note_analysis_items(items)

        def sort_key(item: Dict[str, Any]):
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            return (
                self._to_float(item.get("content_score") or 0),
                int(evidence.get("like_count") or 0),
                int(evidence.get("comment_count") or 0),
                int(evidence.get("collect_count") or 0)
            )

        normalized_items.sort(key=sort_key, reverse=True)
        return normalized_items[:limit]

    def _get_top_keywords(self, items: Any, limit: int = 8) -> List[Dict[str, Any]]:
        """按分数选出重点关键词"""
        normalized_items = self._normalize_keyword_items(items)

        normalized_items.sort(
            key=lambda item: self._to_float(item.get("score") or 0),
            reverse=True
        )

        return normalized_items[:limit]

    def _first_matched_keyword(self, evidence: Dict[str, Any]) -> str:
        """读取第一条命中关键词"""
        if not isinstance(evidence, dict):
            return ""

        matched_keywords = evidence.get("matched_keywords")
        if isinstance(matched_keywords, list):
            for item in matched_keywords:
                text = str(item or "").strip()
                if text:
                    return text

        return ""

    def _normalize_keyword_items(self, items: Any) -> List[Dict[str, Any]]:
        """标准化关键词字段"""
        if not isinstance(items, list):
            return []

        result = []
        for item in items:
            if isinstance(item, str):
                keyword = item.strip()
                if keyword:
                    result.append({
                        "keyword": keyword,
                        "keyword_type": "unknown",
                        "reason": "AI 返回为字符串，已按关键词兼容处理",
                        "score": 0
                    })
                continue

            if not isinstance(item, dict):
                continue

            keyword = str(
                item.get("keyword")
                or item.get("word")
                or item.get("term")
                or item.get("name")
                or ""
            ).strip()

            if not keyword:
                continue

            keyword_type = str(
                item.get("keyword_type")
                or item.get("type")
                or item.get("category")
                or "unknown"
            ).strip()

            reason = str(
                item.get("reason")
                or item.get("why")
                or item.get("description")
                or item.get("rationale")
                or "AI 未返回原因，已保留关键词用于报告展示"
            ).strip()

            result.append({
                "keyword": keyword,
                "keyword_type": keyword_type,
                "reason": reason,
                "score": self._to_float(item.get("score") or item.get("weight") or 0)
            })

        return result

    def _normalize_note_analysis_items(self, items: Any) -> List[Dict[str, Any]]:
        """标准化笔记分析字段"""
        if not isinstance(items, list):
            return []

        result = []
        for item in items:
            if not isinstance(item, dict):
                continue

            result.append({
                "note_url": item.get("note_url") or item.get("video_url") or "",
                "title": item.get("title") or item.get("video_desc") or "",
                "author_name": item.get("author_name") or "",
                "content_score": self._to_float(item.get("content_score") or 0),
                "title_structure": item.get("title_structure") or "",
                "topic_type": item.get("topic_type") or "",
                "tag_summary": item.get("tag_summary") or "",
                "copywriting_structure": item.get("copywriting_structure") or "",
                "visual_summary": item.get("visual_summary") or "",
                "evidence": item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            })

        return result

    def _normalize_comment_insight_items(self, items: Any) -> List[Dict[str, Any]]:
        """标准化评论洞察字段"""
        if not isinstance(items, list):
            return []

        result = []
        for item in items:
            if not isinstance(item, dict):
                continue

            result.append({
                "video_url": item.get("video_url") or "",
                "user_url": item.get("user_url") or "",
                "username": item.get("username") or "",
                "comment_text": item.get("comment_text") or item.get("raw_comment") or "",
                "intent_type": item.get("intent_type") or "",
                "pain_point": item.get("pain_point") or "",
                "demand_type": item.get("demand_type") or "",
                "intent_level": item.get("intent_level") or "low",
                "selling_point": item.get("selling_point") or "",
                "evidence": item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            })

        return result

    def _normalize_candidate_term_items(self, items: Any) -> List[Dict[str, Any]]:
        """标准化候选词字段"""
        if not isinstance(items, list):
            return []

        result = []
        for item in items:
            if isinstance(item, str):
                term = item.strip()
                if term:
                    result.append({
                        "term": term,
                        "term_type": "unknown",
                        "reason": "AI 返回为字符串，已按候选词兼容处理",
                        "evidence_count": 0,
                        "evidence_samples": [],
                        "review_status": "pending"
                    })
                continue

            if not isinstance(item, dict):
                continue

            term = str(item.get("term") or item.get("keyword") or item.get("word") or "").strip()
            if not term:
                continue

            result.append({
                "term": term,
                "term_type": item.get("term_type") or item.get("type") or "unknown",
                "reason": item.get("reason") or "AI 未返回原因，已保留候选词用于人工审核",
                "evidence_count": int(item.get("evidence_count") or 0),
                "evidence_samples": item.get("evidence_samples") if isinstance(item.get("evidence_samples"), list) else [],
                "review_status": item.get("review_status") or "pending"
            })

        return result

    def _normalize_string_list(self, value: Any) -> List[str]:
        """把 AI 返回的字符串、对象列表、混合列表统一成 List[str]"""
        if value is None:
            return []

        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []

        if isinstance(value, list):
            result = []
            for item in value:
                text = self._format_report_item(item)
                if text:
                    result.append(text)
            return result

        text = self._format_report_item(value)
        return [text] if text else []

    def _format_report_item(self, item: Any) -> str:
        """把报告中的对象项转成可展示字符串"""
        if item is None:
            return ""

        if isinstance(item, str):
            return item.strip()

        if isinstance(item, dict):
            parts = []

            if item.get("week"):
                parts.append(f"第{item.get('week')}周")

            if item.get("date"):
                parts.append(str(item.get("date")))

            if item.get("topic"):
                parts.append(str(item.get("topic")))

            if item.get("title"):
                parts.append(str(item.get("title")))

            if item.get("content"):
                parts.append(str(item.get("content")))

            if item.get("reason"):
                parts.append(f"原因：{item.get('reason')}")

            if item.get("action"):
                parts.append(str(item.get("action")))

            if parts:
                return "｜".join([p for p in parts if p])

            return json.dumps(item, ensure_ascii=False)

        return str(item).strip()

    def _to_float(self, value: Any) -> float:
        """安全转换浮点数"""
        try:
            return float(value)
        except Exception:
            return 0.0

    async def _build_next_questions(self, profile: Dict[str, Any], missing_fields: List[str]) -> List[Dict[str, Any]]:
        """生成下一轮反问问题"""
        try:
            ai_result = await self.ai_provider.chat_json(
                build_clarifying_question_messages(profile, missing_fields)
            )
            question_result = self._validate_model(ClarifyingQuestionResult, ai_result)
            return [self._model_to_dict(item) for item in question_result.questions]
        except Exception:
            return [self._model_to_dict(item) for item in self._fallback_questions(missing_fields)]

    def _fallback_questions(self, missing_fields: List[str]) -> List[ClarifyingQuestion]:
        """反问兜底问题"""
        question_map = {
            "industry": {
                "question": "你属于哪个行业？",
                "reason": "行业会影响关键词、竞品笔记和报告分析方向"
            },
            "service": {
                "question": "你具体提供什么产品或服务？",
                "reason": "服务内容会决定核心搜索关键词"
            },
            "city": {
                "question": "你主要服务哪个城市或地区？",
                "reason": "城市会影响本地获客关键词和用户筛选"
            },
            "target_customer": {
                "question": "你的目标客户是谁？",
                "reason": "目标客户会影响选题、关键词和评论意图判断"
            },
            "account_style": {
                "question": "你希望账号呈现什么风格？",
                "reason": "账号风格会影响标题、视觉和内容表达"
            },
            "customer_type": {
                "question": "你想获取哪类客户？",
                "reason": "客户类型会影响评论洞察和人工跟进建议"
            }
        }

        questions = []
        for field_name in missing_fields[:3]:
            item = question_map.get(field_name, {
                "question": f"请补充 {field_name} 信息",
                "reason": "该字段会影响 Agent 分析结果"
            })
            questions.append(ClarifyingQuestion(
                question=item["question"],
                field_name=field_name,
                reason=item["reason"]
            ))

        return questions

    def _build_profile_with_review_context(self, profile: Dict[str, Any], project_id: int) -> Dict[str, Any]:
        """把正式词库和项目记忆加入 AI 输入参考，不直接改变项目画像字段"""
        result = dict(profile or {})
        project_id = int(project_id or result.get("project_id") or 0)

        result["approved_terms_reference"] = self._load_approved_terms_reference(project_id)
        result["project_memory_reference"] = self._load_project_memory_reference(project_id)
        result["agent_reference_rules"] = [
            "正式库 approved_terms_reference 只能作为下一次 Agent 任务的参考，不能替代本次采集数据。",
            "项目记忆 project_memory_reference 用于延续同一项目的用户偏好、拒绝方向、账号风格和已接受卖点。",
            "rejected_content_direction 类型的项目记忆表示用户明确拒绝的内容方向，后续报告和选题需要避开。",
            "最终结论必须优先引用本次真实采集的笔记、评论、互动数据或原始证据。",
            "如果正式库、项目记忆和本次采集证据冲突，以本次真实采集证据和用户最新输入为准。"
        ]

        return result

    def _load_approved_terms_reference(self, project_id: int) -> List[Dict[str, Any]]:
        """读取人工审核通过的正式词库，供 AI 下一次任务参考"""
        project_id = int(project_id or 0)
        if project_id <= 0:
            return []

        try:
            rows = self.storage.get_approved_terms(project_id=project_id, limit=80)
        except Exception:
            return []

        result = []
        for row in rows:
            if not isinstance(row, dict):
                row = dict(row)

            mapping_data = self._json_loads(row.get("mapping_json") or "")

            result.append({
                "id": row.get("id"),
                "candidate_id": row.get("candidate_id"),
                "project_id": row.get("project_id"),
                "industry": row.get("industry") or "",
                "term": row.get("term") or "",
                "term_type": row.get("term_type") or "",
                "mapping": mapping_data,
                "created_time": row.get("created_time") or ""
            })

        return result

    def _load_project_memory_reference(self, project_id: int) -> List[Dict[str, Any]]:
        """读取项目记忆，供 AI 下一次任务参考"""
        project_id = int(project_id or 0)
        if project_id <= 0:
            return []

        try:
            rows = self.storage.get_project_memory(project_id=project_id, limit=100)
        except Exception:
            return []

        result = []
        for row in rows:
            if not isinstance(row, dict):
                row = dict(row)

            result.append({
                "id": row.get("id"),
                "project_id": row.get("project_id"),
                "memory_type": row.get("memory_type") or "",
                "content": row.get("content") or "",
                "source_session_id": row.get("source_session_id"),
                "created_time": row.get("created_time") or ""
            })

        return result

    def _get_required_missing_fields(self, route: str, profile: Dict[str, Any]) -> List[str]:
        """根据路线检查必填字段"""
        if route == AgentRoute.customer_acquisition.value:
            required_fields = [
                "industry",
                "service",
                "city",
                "target_customer",
                "customer_type"
            ]
        else:
            required_fields = [
                "industry",
                "service",
                "target_customer",
                "account_style"
            ]

        missing_fields = []
        for field_name in required_fields:
            if not str(profile.get(field_name) or "").strip():
                missing_fields.append(field_name)

        return missing_fields

    def _get_profile_from_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        """从会话中读取项目画像"""
        parsed_profile = self._json_loads(session.get("parsed_profile_json", ""))

        profile = parsed_profile.get("profile", {})
        if isinstance(profile, dict):
            return profile

        return {}

    def _set_agent_session_project_id(self, session_id: int, project_id: int) -> bool:
        """更新会话 project_id"""
        try:
            conn = self.storage.get_connection()
            cur = conn.cursor()
            cur.execute(
                "UPDATE agent_sessions SET project_id = ? WHERE id = ?",
                (int(project_id), int(session_id))
            )
            conn.commit()
            updated = cur.rowcount
            conn.close()
            return updated > 0
        except Exception as e:
            print(f"更新 Agent 会话 project_id 失败: {e}")
            return False

    def _update_project_fields(self, project_id: int, profile: Dict[str, Any]) -> bool:
        """更新项目画像字段"""
        try:
            conn = self.storage.get_connection()
            cur = conn.cursor()
            cur.execute("""
                UPDATE projects
                SET project_name = ?,
                    route = ?,
                    industry = ?,
                    service = ?,
                    city = ?,
                    target_customer = ?,
                    account_style = ?,
                    price_range = ?,
                    customer_type = ?
                WHERE id = ?
            """, (
                profile.get("project_name") or "",
                profile.get("route") or "",
                profile.get("industry") or "",
                profile.get("service") or "",
                profile.get("city") or "",
                profile.get("target_customer") or "",
                profile.get("account_style") or "",
                profile.get("price_range") or "",
                profile.get("customer_type") or "",
                int(project_id)
            ))
            conn.commit()
            updated = cur.rowcount
            conn.close()
            return updated > 0
        except Exception as e:
            print(f"更新 Agent 项目画像失败: {e}")
            return False

    def _normalize_route(self, route: str) -> str:
        """校验并标准化路线"""
        route = str(route or "").strip()

        if route == AgentRoute.personal_account.value:
            return AgentRoute.personal_account.value

        if route == AgentRoute.customer_acquisition.value:
            return AgentRoute.customer_acquisition.value

        raise ValueError(f"不支持的 Agent 路线: {route}")

    def _validate_model(self, model_class: Type[BaseModel], data: Any) -> BaseModel:
        """兼容 Pydantic v1/v2 的模型校验"""
        if hasattr(model_class, "model_validate"):
            return model_class.model_validate(data)
        return model_class.parse_obj(data)

    def _model_to_dict(self, model: Any) -> Dict[str, Any]:
        """兼容 Pydantic v1/v2 的模型转字典"""
        if model is None:
            return {}

        if isinstance(model, dict):
            return model

        if hasattr(model, "model_dump"):
            return model.model_dump()

        if hasattr(model, "dict"):
            return model.dict()

        return {}

    def _json_loads(self, text: Any) -> Dict[str, Any]:
        """安全读取 JSON 字符串"""
        if isinstance(text, dict):
            return text

        if not text:
            return {}

        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


_ai_agent_service_instance = None


def get_ai_agent_service() -> AIAgentService:
    """获取 AI Agent 服务单例"""
    global _ai_agent_service_instance
    if _ai_agent_service_instance is None:
        _ai_agent_service_instance = AIAgentService()
    return _ai_agent_service_instance