"""Расчёт срока по демонстрационному регламенту (календарные дни)."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.agent.state import DeadlineResult
from app.core.enums import URGENT_LEVELS, UrgencyLevel
from app.core.taxonomy import DEFAULT_DEADLINE_DAYS, URGENT_DEADLINE_DAYS, ensure_aware
from app.db.models import RoutingRule


def calculate_deadline(
    db: Session, *, rule_id: int | None, urgency_level: UrgencyLevel | str, received_at: datetime
) -> DeadlineResult:
    received_at = ensure_aware(received_at)
    rule = db.get(RoutingRule, rule_id) if rule_id else None
    if UrgencyLevel(urgency_level) in URGENT_LEVELS:
        days, note = URGENT_DEADLINE_DAYS, f"Срочное обращение: {URGENT_DEADLINE_DAYS} календ. день (демо-регламент)"
    elif rule is not None:
        days = rule.deadline_days
        note = f"Правило #{rule.id} ({rule.topic}): {days} календ. дн. (демо-регламент)"
    else:
        days, note = DEFAULT_DEADLINE_DAYS, f"Общий срок: {DEFAULT_DEADLINE_DAYS} календ. дн. (демо-регламент)"
    return DeadlineResult(rule_id=rule.id if rule else None, due_at=received_at + timedelta(days=days), note=note)
