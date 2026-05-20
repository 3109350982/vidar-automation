"""
AI Agent 结构化模型
统一 AI 输出、API 输入输出、数据库字段
"""
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator


class AgentRoute(str, Enum):
    """Agent 固定路线"""
    personal_account = "personal_account"
    customer_acquisition = "customer_acquisition"


class AgentSessionStatus(str, Enum):
    """Agent 会话状态"""
    created = "created"
    need_clarify = "need_clarify"
    ready = "ready"
    running = "running"
    finished = "finished"
    failed = "failed"


class ProjectProfile(BaseModel):
    """项目画像"""
    project_name: str = Field(default="", description="项目名称")
    route: AgentRoute = Field(..., description="Agent 路线")
    industry: str = Field(default="", description="行业")
    service: str = Field(default="", description="服务或产品")
    city: str = Field(default="", description="城市")
    target_customer: str = Field(default="", description="目标客户")
    account_style: str = Field(default="", description="账号风格")
    price_range: str = Field(default="", description="价格区间")
    customer_type: str = Field(default="", description="客户类型")

    class Config:
        use_enum_values = True


class ClarifyingQuestion(BaseModel):
    """反问问题"""
    question: str = Field(..., description="反问给用户的问题")
    field_name: str = Field(..., description="该问题要补全的字段名")
    reason: str = Field(..., description="为什么需要追问这个问题")


class ClarifyingQuestionResult(BaseModel):
    """反问问题结果"""
    questions: List[ClarifyingQuestion] = Field(default_factory=list, description="反问问题列表")

    @validator("questions")
    def validate_question_count(cls, value):
        if len(value) > 3:
            raise ValueError("反问问题最多 3 条")
        return value


class AgentStartRequest(BaseModel):
    """Agent 启动请求"""
    route: AgentRoute = Field(..., description="Agent 路线")
    user_input: str = Field(..., min_length=1, max_length=500, description="用户的一句话业务描述")

    class Config:
        use_enum_values = True


class AgentAnswerRequest(BaseModel):
    """Agent 反问回答请求"""
    session_id: int = Field(..., ge=1, description="Agent 会话 ID")
    answers: Dict[str, str] = Field(default_factory=dict, description="用户对反问字段的回答")


class AgentRunRequest(BaseModel):
    """Agent 执行请求"""
    session_id: int = Field(..., ge=1, description="Agent 会话 ID")
    project_id: Optional[int] = Field(default=None, description="项目 ID")


class ProfileParseResult(BaseModel):
    """用户输入解析结果"""
    profile: ProjectProfile = Field(..., description="项目画像")
    missing_fields: List[str] = Field(default_factory=list, description="缺失字段")
    questions: List[ClarifyingQuestion] = Field(default_factory=list, description="反问问题")
    is_ready: bool = Field(default=False, description="是否可以进入采集规划")

    @validator("questions")
    def validate_question_count(cls, value):
        if len(value) > 3:
            raise ValueError("反问问题最多 3 条")
        return value


class KeywordItem(BaseModel):
    """关键词项"""
    keyword: str = Field(..., min_length=1, description="关键词")
    keyword_type: str = Field(..., description="关键词类型")
    reason: str = Field(..., description="生成该关键词的原因")
    score: float = Field(default=0, ge=0, le=100, description="关键词分数")


class KeywordGenerationResult(BaseModel):
    """关键词生成结果"""
    keywords: List[KeywordItem] = Field(default_factory=list, description="关键词列表")


class NoteAnalysisItem(BaseModel):
    """小红书笔记分析项"""
    note_url: str = Field(default="", description="笔记链接")
    title: str = Field(default="", description="标题")
    author_name: str = Field(default="", description="作者昵称")
    content_score: float = Field(default=0, ge=0, le=100, description="内容分")
    title_structure: str = Field(default="", description="标题结构")
    topic_type: str = Field(default="", description="主题类型")
    tag_summary: str = Field(default="", description="标签总结")
    copywriting_structure: str = Field(default="", description="文案结构")
    visual_summary: str = Field(default="", description="视觉总结")
    evidence: Dict[str, Any] = Field(default_factory=dict, description="分析依据")


class NoteAnalysisResult(BaseModel):
    """小红书笔记分析结果"""
    items: List[NoteAnalysisItem] = Field(default_factory=list, description="笔记分析列表")


class CommentInsightItem(BaseModel):
    """小红书评论洞察项"""
    video_url: str = Field(default="", description="笔记链接")
    user_url: str = Field(default="", description="用户主页链接")
    username: str = Field(default="", description="用户名")
    comment_text: str = Field(default="", description="评论内容")
    intent_type: str = Field(default="", description="意图类型")
    pain_point: str = Field(default="", description="痛点")
    demand_type: str = Field(default="", description="需求类型")
    intent_level: str = Field(default="", description="意向等级")
    selling_point: str = Field(default="", description="可匹配卖点")
    evidence: Dict[str, Any] = Field(default_factory=dict, description="分析依据")


class CommentInsightResult(BaseModel):
    """小红书评论洞察结果"""
    items: List[CommentInsightItem] = Field(default_factory=list, description="评论洞察列表")


class CandidateTermItem(BaseModel):
    """候选词项"""
    term: str = Field(..., min_length=1, description="候选词")
    term_type: str = Field(..., description="候选词类型")
    reason: str = Field(default="", description="进入候选库的原因")
    evidence_count: int = Field(default=0, ge=0, description="证据数量")
    evidence_samples: List[str] = Field(default_factory=list, description="证据样例")
    review_status: str = Field(default="pending", description="审核状态")


class CandidateTermResult(BaseModel):
    """候选词结果"""
    items: List[CandidateTermItem] = Field(default_factory=list, description="候选词列表")


class PersonalAccountReport(BaseModel):
    """个人账号运营路线报告"""
    route: AgentRoute = Field(default=AgentRoute.personal_account, description="路线")
    project_profile: ProjectProfile = Field(..., description="项目画像")
    keywords: List[KeywordItem] = Field(default_factory=list, description="关键词")
    note_analyses: List[NoteAnalysisItem] = Field(default_factory=list, description="笔记分析")
    account_positioning: str = Field(default="", description="账号定位")
    content_topics: List[str] = Field(default_factory=list, description="内容选题")
    title_templates: List[str] = Field(default_factory=list, description="标题模板")
    visual_suggestions: List[str] = Field(default_factory=list, description="视觉建议")
    publish_plan: List[str] = Field(default_factory=list, description="发布计划")
    evidence_summary: List[str] = Field(default_factory=list, description="数据依据总结")
    risk_tips: List[str] = Field(default_factory=list, description="合规风险提示")
    next_actions: List[str] = Field(default_factory=list, description="下一步动作")

    class Config:
        use_enum_values = True

    def to_markdown(self) -> str:
        """转换为 Markdown 报告"""
        lines = [
            "# 小红书个人账号运营报告",
            "",
            "## 1. 项目画像",
            f"- 行业：{self.project_profile.industry}",
            f"- 服务：{self.project_profile.service}",
            f"- 城市：{self.project_profile.city}",
            f"- 目标客户：{self.project_profile.target_customer}",
            f"- 账号风格：{self.project_profile.account_style}",
            "",
            "## 2. 账号定位",
            self.account_positioning or "暂无",
            "",
            "## 3. 关键词",
        ]

        for item in self.keywords:
            lines.append(f"- {item.keyword}｜{item.keyword_type}｜分数：{item.score}｜原因：{item.reason}")

        lines.extend([
            "",
            "## 4. 内容选题",
            self._format_list(self.content_topics),
            "",
            "## 5. 标题模板",
            self._format_list(self.title_templates),
            "",
            "## 6. 视觉建议",
            self._format_list(self.visual_suggestions),
            "",
            "## 7. 发布计划",
            self._format_list(self.publish_plan),
            "",
            "## 8. 数据依据",
            self._format_list(self.evidence_summary),
            "",
            "## 9. 合规提示",
            self._format_list(self.risk_tips),
            "",
            "## 10. 下一步动作",
            self._format_list(self.next_actions),
        ])

        return "\n".join(lines)

    def _format_list(self, items: List[str]) -> str:
        if not items:
            return "暂无"
        return "\n".join([f"- {item}" for item in items])


class CustomerAcquisitionReport(BaseModel):
    """获客路线报告"""
    route: AgentRoute = Field(default=AgentRoute.customer_acquisition, description="路线")
    project_profile: ProjectProfile = Field(..., description="项目画像")
    keywords: List[KeywordItem] = Field(default_factory=list, description="关键词")
    comment_insights: List[CommentInsightItem] = Field(default_factory=list, description="评论洞察")
    high_intent_user_profile: str = Field(default="", description="高意向用户画像")
    pain_points: List[str] = Field(default_factory=list, description="客户痛点")
    demand_types: List[str] = Field(default_factory=list, description="需求类型")
    selling_points: List[str] = Field(default_factory=list, description="卖点")
    follow_up_suggestions: List[str] = Field(default_factory=list, description="人工跟进建议")
    candidate_terms: List[CandidateTermItem] = Field(default_factory=list, description="候选词")
    evidence_summary: List[str] = Field(default_factory=list, description="数据依据总结")
    risk_tips: List[str] = Field(default_factory=list, description="合规风险提示")
    next_actions: List[str] = Field(default_factory=list, description="下一步动作")

    class Config:
        use_enum_values = True

    def to_markdown(self) -> str:
        """转换为 Markdown 报告"""
        lines = [
            "# 小红书获客洞察报告",
            "",
            "## 1. 项目画像",
            f"- 行业：{self.project_profile.industry}",
            f"- 服务：{self.project_profile.service}",
            f"- 城市：{self.project_profile.city}",
            f"- 目标客户：{self.project_profile.target_customer}",
            f"- 客户类型：{self.project_profile.customer_type}",
            "",
            "## 2. 高意向用户画像",
            self.high_intent_user_profile or "暂无",
            "",
            "## 3. 客户痛点",
            self._format_list(self.pain_points),
            "",
            "## 4. 需求类型",
            self._format_list(self.demand_types),
            "",
            "## 5. 可匹配卖点",
            self._format_list(self.selling_points),
            "",
            "## 6. 人工跟进建议",
            self._format_list(self.follow_up_suggestions),
            "",
            "## 7. 关键词",
        ]

        for item in self.keywords:
            lines.append(f"- {item.keyword}｜{item.keyword_type}｜分数：{item.score}｜原因：{item.reason}")

        lines.extend([
            "",
            "## 8. 评论洞察",
        ])

        for item in self.comment_insights:
            lines.append(f"- {item.username}：{item.comment_text}｜意向：{item.intent_level}｜痛点：{item.pain_point}")

        lines.extend([
            "",
            "## 9. 候选词",
        ])

        for item in self.candidate_terms:
            lines.append(f"- {item.term}｜{item.term_type}｜证据数：{item.evidence_count}｜原因：{item.reason}")

        lines.extend([
            "",
            "## 10. 数据依据",
            self._format_list(self.evidence_summary),
            "",
            "## 11. 合规提示",
            self._format_list(self.risk_tips),
            "",
            "## 12. 下一步动作",
            self._format_list(self.next_actions),
        ])

        return "\n".join(lines)

    def _format_list(self, items: List[str]) -> str:
        if not items:
            return "暂无"
        return "\n".join([f"- {item}" for item in items])