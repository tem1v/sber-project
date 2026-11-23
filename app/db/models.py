"""Схема БД CivicFlow (раздел 9 ТЗ).

Принципы:
* исходный текст обращения хранится отдельно от извлечённых данных;
* ответ модели (request_analysis) не перезаписывается — исправления сотрудника лежат в reviews;
* дубликаты — только связи (request_links), без автоматического слияния;
* каждый запуск агента и каждый его шаг сохраняются (agent_runs / agent_trace).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    sql_in,
)
from app.db.base import Base


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(String(255))
    queue: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    rules: Mapped[list["RoutingRule"]] = relationship(back_populates="department")


class RoutingRule(Base):
    __tablename__ = "routing_rules"
    __table_args__ = (
        CheckConstraint("deadline_days > 0", name="deadline_positive"),
        Index(None, "topic", "subtopic"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    topic: Mapped[str] = mapped_column(String(128))
    subtopic: Mapped[str | None] = mapped_column(String(128))
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id", ondelete="RESTRICT"))
    conditions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    deadline_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))

    department: Mapped[Department] = relationship(back_populates="rules")


class Request(Base):
    __tablename__ = "requests"
    __table_args__ = (
        CheckConstraint(sql_in("status", RequestStatus), name="status_valid"),
        CheckConstraint(sql_in("source_channel", Channel), name="channel_valid"),
        CheckConstraint(f"priority IS NULL OR {sql_in('priority', UrgencyLevel)}", name="priority_valid"),
        CheckConstraint("length(raw_text) > 0", name="raw_text_not_empty"),
        Index(None, "received_at"),
        Index(None, "status"),
        Index(None, "department_id"),
        Index(None, "topic"),
        Index(None, "priority"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_channel: Mapped[str] = mapped_column(String(32))
    raw_text: Mapped[str] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default=RequestStatus.NEW.value, server_default="new")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Текущие (рабочие) значения для очереди и фильтров: результат последнего анализа
    # с учётом исправлений сотрудника. Исходный вывод модели — в request_analysis.
    summary: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(128))
    subtopic: Mapped[str | None] = mapped_column(String(128))
    priority: Mapped[str | None] = mapped_column(String(16))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"))
    registration_number: Mapped[str | None] = mapped_column(String(32), unique=True)

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    department: Mapped[Department | None] = relationship()
    analyses: Mapped[list["RequestAnalysis"]] = relationship(
        back_populates="request", order_by="RequestAnalysis.version", cascade="all, delete-orphan"
    )
    reviews: Mapped[list["Review"]] = relationship(order_by="Review.id", cascade="all, delete-orphan")
    drafts: Mapped[list["ActionDraft"]] = relationship(order_by="ActionDraft.id", cascade="all, delete-orphan")
    deadlines: Mapped[list["Deadline"]] = relationship(order_by="Deadline.id", cascade="all, delete-orphan")
    runs: Mapped[list["AgentRun"]] = relationship(order_by="AgentRun.id", cascade="all, delete-orphan")


class RequestAnalysis(Base):
    __tablename__ = "request_analysis"
    __table_args__ = (
        UniqueConstraint("request_id", "version"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint(sql_in("urgency_level", UrgencyLevel), name="urgency_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), index=True)
    agent_run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="SET NULL"))
    version: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    extracted_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    topics_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    urgency_level: Mapped[str] = mapped_column(String(16))
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created_at()

    request: Mapped[Request] = relationship(back_populates="analyses")


class RequestLink(Base):
    __tablename__ = "request_links"
    __table_args__ = (
        UniqueConstraint("request_id", "related_request_id"),
        CheckConstraint("request_id <> related_request_id", name="not_self"),
        CheckConstraint("similarity_score >= 0 AND similarity_score <= 1", name="score_range"),
        CheckConstraint(sql_in("link_type", LinkType), name="link_type_valid"),
        CheckConstraint(sql_in("status", LinkStatus), name="status_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), index=True)
    related_request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), index=True)
    similarity_score: Mapped[float] = mapped_column(Float)
    link_type: Mapped[str] = mapped_column(String(16), default=LinkType.DUPLICATE.value)
    status: Mapped[str] = mapped_column(String(16), default=LinkStatus.CANDIDATE.value)
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()

    related: Mapped[Request] = relationship(foreign_keys=[related_request_id])
    source: Mapped[Request] = relationship(foreign_keys=[request_id])


class Deadline(Base):
    __tablename__ = "deadlines"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("routing_rules.id", ondelete="SET NULL"))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    calculation_note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (CheckConstraint(sql_in("decision", ReviewDecision), name="decision_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), index=True)
    reviewer: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(32))
    corrected_fields_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (CheckConstraint(sql_in("status", RunStatus), name="status_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.RUNNING.value)
    agent_mode: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    steps_count: Mapped[int] = mapped_column(Integer, default=0)
    # Финальное состояние агента — чтобы его можно было восстановить после перезапуска.
    state_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    steps: Mapped[list["AgentTrace"]] = relationship(order_by="AgentTrace.step_no", cascade="all, delete-orphan")


class AgentTrace(Base):
    __tablename__ = "agent_trace"
    __table_args__ = (UniqueConstraint("agent_run_id", "step_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    step_no: Mapped[int] = mapped_column(Integer)
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    decision: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created_at()


class ActionDraft(Base):
    __tablename__ = "action_drafts"
    __table_args__ = (
        CheckConstraint(sql_in("action_type", ActionType), name="action_type_valid"),
        CheckConstraint(sql_in("status", DraftStatus), name="status_valid"),
        # Не более одного ожидающего черновика каждого типа на обращение.
        Index(
            "uq_action_drafts_one_pending",
            "request_id",
            "action_type",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
        # Регистрация подтверждается ровно один раз (идемпотентность на уровне БД).
        Index(
            "uq_action_drafts_one_registration",
            "request_id",
            unique=True,
            sqlite_where=text("status = 'confirmed' AND action_type = 'registration'"),
            postgresql_where=text("status = 'confirmed' AND action_type = 'registration'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id", ondelete="CASCADE"), index=True)
    action_type: Mapped[str] = mapped_column(String(32))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default=DraftStatus.PENDING.value)
    created_by: Mapped[str] = mapped_column(String(64))
    confirmed_by: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
