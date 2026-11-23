"""Состояние агента — общий контракт между агентом, инструментами и backend."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.ai.schemas import AnalysisResult


class SimilarCandidate(BaseModel):
    request_id: int
    similarity: float = Field(ge=0, le=1)
    summary: str | None = None
    status: str
    same_address: bool = False
    same_topic: bool = False


class DepartmentCandidate(BaseModel):
    department_id: int
    department_code: str
    department_name: str
    rule_id: int
    topic: str
    subtopic: str | None = None
    score: float = Field(ge=0, le=1)
    deadline_days: int
    explanation: str


class ValidationResult(BaseModel):
    """Контракт инструмента проверки (guardrails, зона Участника 3)."""

    decision: Literal["ok", "needs_review", "clarify", "escalate"]
    ok: bool
    warnings: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class DeadlineResult(BaseModel):
    rule_id: int | None = None
    due_at: datetime
    note: str


class AgentState(BaseModel):
    request_id: int
    run_id: int | None = None
    text: str
    channel: str
    received_at: datetime

    analysis: AnalysisResult | None = None
    similar: list[SimilarCandidate] = Field(default_factory=list)
    department_candidates: list[DepartmentCandidate] = Field(default_factory=list)
    chosen_department: DepartmentCandidate | None = None
    validation: ValidationResult | None = None
    deadline: DeadlineResult | None = None

    history: list[str] = Field(default_factory=list)
    finished: bool = False
    finish_reason: str | None = None
    # Агент никогда не выполняет действие сам — всегда ждёт подтверждения сотрудника.
    awaiting_confirmation: bool = True
