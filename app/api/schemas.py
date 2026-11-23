"""Pydantic-схемы API (контракт frontend ↔ backend)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import (
    ActionType,
    Channel,
    DraftStatus,
    LinkStatus,
    LinkType,
    RequestStatus,
    ReviewDecision,
    RunStatus,
    UrgencyLevel,
)

ReviewerId = Field("demo-user", min_length=1, max_length=64, description="ID сотрудника / демо-пользователя")


class ErrorOut(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str | None = None


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- обращения ---------------------------------------------------------------


class RequestCreate(BaseModel):
    raw_text: str = Field(min_length=1, max_length=10_000)
    source_channel: Channel = Channel.WEB
    received_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("raw_text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Текст обращения не может быть пустым")
        return v


class RequestListItem(ORM):
    id: int
    source_channel: Channel
    received_at: datetime
    status: RequestStatus
    summary: str | None
    address: str | None
    topic: str | None
    subtopic: str | None
    priority: UrgencyLevel | None
    department_id: int | None
    department_name: str | None = None
    registration_number: str | None


class RequestOut(RequestListItem):
    raw_text: str
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_json")
    created_at: datetime
    updated_at: datetime


class RequestList(BaseModel):
    items: list[RequestListItem]
    total: int
    limit: int
    offset: int


class AnalysisOut(ORM):
    id: int
    version: int
    agent_run_id: int | None
    summary: str | None
    extracted: dict[str, Any] = Field(validation_alias="extracted_json")
    topics: list[Any] = Field(validation_alias="topics_json")
    urgency_level: UrgencyLevel
    requires_human_review: bool
    confidence: float
    model_version: str
    prompt_version: str | None
    created_at: datetime


class LinkOut(BaseModel):
    id: int
    related_request_id: int
    related_summary: str | None
    related_status: RequestStatus
    similarity_score: float
    link_type: LinkType
    status: LinkStatus
    reviewed_by: str | None
    reviewed_at: datetime | None


class DeadlineOut(ORM):
    id: int
    rule_id: int | None
    due_at: datetime
    calculation_note: str
    created_at: datetime


class DraftOut(ORM):
    id: int
    action_type: ActionType
    payload: dict[str, Any] = Field(validation_alias="payload_json")
    status: DraftStatus
    created_by: str
    confirmed_by: str | None
    confirmed_at: datetime | None
    created_at: datetime


class ReviewOut(ORM):
    id: int
    reviewer: str
    decision: ReviewDecision
    corrected_fields: dict[str, Any] = Field(validation_alias="corrected_fields_json")
    comment: str | None
    created_at: datetime


class RouteCheck(BaseModel):
    decision: str
    ok: bool
    warnings: list[str] = []
    reasons: list[str] = []


class RequestDetail(BaseModel):
    request: RequestOut
    analysis: AnalysisOut | None
    analysis_versions: int
    department_candidates: list[dict[str, Any]] = []
    route_check: RouteCheck | None
    similar: list[LinkOut]
    deadline: DeadlineOut | None
    drafts: list[DraftOut]
    reviews: list[ReviewOut]
    allowed_actions: list[str]


class AnalyzeResponse(BaseModel):
    request_id: int
    run_id: int
    run_status: RunStatus
    status: RequestStatus
    analysis: AnalysisOut | None
    route_check: RouteCheck | None
    similar_count: int


# --- решения сотрудника -------------------------------------------------------


class CorrectedFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str | None = Field(None, max_length=2000)
    address: str | None = Field(None, max_length=500)
    topic: str | None = Field(None, max_length=128)
    subtopic: str | None = Field(None, max_length=128)
    department_id: int | None = Field(None, gt=0)
    priority: UrgencyLevel | None = None


class ReviewIn(BaseModel):
    decision: ReviewDecision
    reviewer: str = ReviewerId
    corrected_fields: CorrectedFields | None = None
    comment: str | None = Field(None, max_length=2000)
    questions: list[str] | None = Field(None, description="Вопросы уточнения (по умолчанию — из анализа)")


class ReviewResponse(BaseModel):
    review: ReviewOut
    status: RequestStatus
    draft: DraftOut | None


class ConfirmIn(BaseModel):
    confirmed_by: str = ReviewerId


class ConfirmResponse(BaseModel):
    request_id: int
    status: RequestStatus
    draft: DraftOut
    registration_number: str | None = None
    already_confirmed: bool


class LinkReviewIn(BaseModel):
    decision: Literal["confirm", "reject"]
    reviewer: str = ReviewerId


# --- справочники, трассировка, аналитика -------------------------------------


class DepartmentOut(ORM):
    id: int
    code: str
    name: str
    description: str | None
    contact: str | None
    queue: str | None
    active: bool


class TraceStepOut(ORM):
    step_no: int
    tool_name: str
    arguments: dict[str, Any] = Field(validation_alias="arguments_json")
    result: Any = Field(validation_alias="result_json")
    decision: str | None
    error: str | None
    duration_ms: int
    token_usage: dict[str, Any] | None
    created_at: datetime


class AgentRunOut(ORM):
    id: int
    status: RunStatus
    agent_mode: str
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    steps_count: int
    steps: list[TraceStepOut]


class TraceOut(BaseModel):
    request_id: int
    runs: list[AgentRunOut]


class AnalyticsOut(BaseModel):
    total: int
    by_status: dict[str, int]
    by_topic: dict[str, int]
    by_department: dict[str, int]
    by_priority: dict[str, int]
    urgent: int
    duplicate_candidates: int
    registered: int
    reviews_total: int
    correction_rate: float | None
    failed_runs: int
    technical_error_rate: float | None
