"""Единые перечисления (статусы, каналы, решения) — общий контракт для всей команды."""

from enum import StrEnum


class RequestStatus(StrEnum):
    NEW = "new"
    ANALYZING = "analyzing"
    NEEDS_REVIEW = "needs_review"
    NEEDS_CLARIFICATION = "needs_clarification"
    URGENT_ESCALATION = "urgent_escalation"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    READY_FOR_CONFIRMATION = "ready_for_confirmation"
    REGISTERED = "registered"
    REJECTED = "rejected"
    FAILED = "failed"


class Channel(StrEnum):
    WEB = "web"
    EMAIL = "email"
    PHONE = "phone"
    SOCIAL = "social"
    VISIT = "visit"
    OTHER = "other"


class UrgencyLevel(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


URGENT_LEVELS = frozenset({UrgencyLevel.HIGH, UrgencyLevel.CRITICAL})


class ReviewDecision(StrEnum):
    ACCEPT = "accept"
    CORRECT = "correct"
    REJECT = "reject"
    REQUEST_CLARIFICATION = "request_clarification"
    ESCALATE = "escalate"


class ActionType(StrEnum):
    REGISTRATION = "registration"
    CLARIFICATION = "clarification"


class DraftStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class LinkType(StrEnum):
    DUPLICATE = "duplicate"
    RELATED = "related"


class LinkStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


def sql_in(column: str, enum_cls: type[StrEnum]) -> str:
    """SQL-выражение для CHECK-ограничения «значение из перечисления»."""
    values = ", ".join(f"'{m.value}'" for m in enum_cls)
    return f"{column} IN ({values})"
