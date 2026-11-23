"""Статусы обращения и разрешённые переходы (ТЗ, п. 8.2)."""

from app.core.enums import RequestStatus as S
from app.core.errors import InvalidTransitionError

_REVIEW_TARGETS = {S.READY_FOR_CONFIRMATION, S.NEEDS_CLARIFICATION, S.URGENT_ESCALATION, S.REJECTED}

TRANSITIONS: dict[S, frozenset[S]] = {
    S.NEW: frozenset({S.ANALYZING, S.REJECTED}),
    S.ANALYZING: frozenset({S.NEEDS_REVIEW, S.NEEDS_CLARIFICATION, S.URGENT_ESCALATION, S.DUPLICATE_CANDIDATE, S.FAILED}),
    S.NEEDS_REVIEW: frozenset({S.ANALYZING, *_REVIEW_TARGETS}),
    S.NEEDS_CLARIFICATION: frozenset({S.ANALYZING, S.NEEDS_REVIEW, *_REVIEW_TARGETS}),
    S.URGENT_ESCALATION: frozenset({S.ANALYZING, *_REVIEW_TARGETS}),
    S.DUPLICATE_CANDIDATE: frozenset({S.ANALYZING, S.NEEDS_REVIEW, *_REVIEW_TARGETS}),
    S.READY_FOR_CONFIRMATION: frozenset({S.REGISTERED, *_REVIEW_TARGETS}),
    S.FAILED: frozenset({S.ANALYZING, S.REJECTED}),
    S.REGISTERED: frozenset(),
    S.REJECTED: frozenset(),
}

# Статусы, в которых сотрудник может принять решение по карточке.
REVIEWABLE = frozenset(
    {S.NEEDS_REVIEW, S.NEEDS_CLARIFICATION, S.URGENT_ESCALATION, S.DUPLICATE_CANDIDATE, S.READY_FOR_CONFIRMATION}
)


def can_transition(current: str, target: str) -> bool:
    return S(target) in TRANSITIONS[S(current)]


def ensure_transition(current: str, target: str) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError(
            f"Переход статуса {current} → {target} запрещён",
            details={"current": current, "target": target, "allowed": sorted(TRANSITIONS[S(current)])},
        )
