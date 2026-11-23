"""Демонстрационная таксономия тем. Справочник ведомств и правила — в БД (seed)."""

from datetime import UTC, datetime

# Для этих тем без адреса/ориентира маршрутизация не может быть уверенной.
ADDRESS_REQUIRED_TOPICS = frozenset({"ЖКХ", "Дороги", "Благоустройство", "Транспорт", "Аварийная ситуация"})

DEFAULT_DEADLINE_DAYS = 30
URGENT_DEADLINE_DAYS = 1


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_aware(dt: datetime) -> datetime:
    """SQLite возвращает naive datetime — считаем его UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
