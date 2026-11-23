"""Базовые guardrails (зона Участника 3 — расширять здесь, сохраняя контракт ValidationResult).

Порядок приоритета решений: escalate > clarify > needs_review > ok.
"""

import re

from sqlalchemy.orm import Session

from app.agent.state import SimilarCandidate, ValidationResult
from app.ai.schemas import AnalysisResult
from app.core.enums import URGENT_LEVELS
from app.core.taxonomy import ADDRESS_REQUIRED_TOPICS
from app.db.models import Department
from app.services.routing import rule_links_topic_to_department

_PRIORITY = {"ok": 0, "needs_review": 1, "clarify": 2, "escalate": 3}


def _address_in_text(address: str, text: str) -> bool:
    """Защита от выдуманного адреса: все слова/номера адреса должны встречаться в тексте."""
    low = text.lower().replace("ё", "е")
    tokens = re.findall(r"[а-яa-z]{3,}|\d+", address.lower().replace("ё", "е"))
    return all(t in low for t in tokens) if tokens else False


def validate_route(
    db: Session,
    *,
    text: str,
    analysis: AnalysisResult | None,
    department_id: int | None,
    topic: str | None,
    confidence_threshold: float,
    similar: list[SimilarCandidate] | None = None,
) -> ValidationResult:
    decision = "ok"
    warnings: list[str] = []
    reasons: list[str] = []

    def raise_to(level: str, reason: str) -> None:
        nonlocal decision
        reasons.append(reason)
        if _PRIORITY[level] > _PRIORITY[decision]:
            decision = level

    if analysis is None:
        raise_to("needs_review", "Нет результата анализа текста")
        return ValidationResult(decision=decision, ok=False, warnings=warnings, reasons=reasons)

    if analysis.urgency.level in URGENT_LEVELS:
        signals = ", ".join(analysis.urgency.risk_signals) or "по оценке модели"
        raise_to("escalate", f"Признаки срочности ({analysis.urgency.level}): {signals}")

    if analysis.missing_information:
        raise_to("clarify", "Не хватает сведений: " + ", ".join(analysis.missing_information))
    elif not analysis.address.raw and topic in ADDRESS_REQUIRED_TOPICS:
        raise_to("clarify", f"Для темы «{topic}» нужен адрес или ориентир")

    if analysis.address.raw and not _address_in_text(analysis.address.raw, text):
        raise_to("needs_review", "Извлечённый адрес не найден в исходном тексте")

    if analysis.overall_confidence < confidence_threshold:
        raise_to("needs_review", f"Низкая уверенность: {analysis.overall_confidence:.2f} < {confidence_threshold:.2f}")

    if len({t.name for t in analysis.topics}) > 1:
        warnings.append("Многотемное обращение: проверьте, не нужно ли несколько ведомств")
        raise_to("needs_review", "Обращение содержит несколько тем")

    if department_id is None:
        raise_to("needs_review", "Ведомство не определено")
    else:
        dept = db.get(Department, department_id)
        if dept is None:
            raise_to("needs_review", f"Ведомство #{department_id} отсутствует в справочнике")
        elif not dept.active:
            raise_to("needs_review", f"Ведомство «{dept.name}» неактивно")
        elif not rule_links_topic_to_department(db, topic, department_id):
            raise_to("needs_review", f"Тема «{topic}» не закреплена за ведомством «{dept.name}»")

    if similar:
        ids = ", ".join(f"#{c.request_id}" for c in similar)
        warnings.append(f"Возможные дубликаты: {ids}")

    return ValidationResult(decision=decision, ok=decision == "ok", warnings=warnings, reasons=reasons)
