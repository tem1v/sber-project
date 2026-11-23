"""Юнит-тесты: статусы, экстрактор-заглушка, дубли, сроки, аналитика."""

from datetime import UTC, datetime

import pytest

from app.ai.rule_based import extract, extract_address
from app.core.enums import RequestStatus as S
from app.core.errors import InvalidTransitionError
from app.services.deadlines import calculate_deadline
from app.services.duplicates import normalize_address, similarity
from app.services.statuses import TRANSITIONS, can_transition, ensure_transition
from tests.conftest import NORMAL, URGENT


def test_every_status_has_transitions_entry():
    assert set(TRANSITIONS) == set(S)


@pytest.mark.parametrize(
    ("src", "dst", "ok"),
    [
        (S.NEW, S.ANALYZING, True),
        (S.ANALYZING, S.REGISTERED, False),  # прямо запрещено ТЗ
        (S.NEEDS_REVIEW, S.REGISTERED, False),
        (S.READY_FOR_CONFIRMATION, S.REGISTERED, True),
        (S.REGISTERED, S.ANALYZING, False),
        (S.REJECTED, S.ANALYZING, False),
        (S.FAILED, S.ANALYZING, True),
        (S.URGENT_ESCALATION, S.READY_FOR_CONFIRMATION, True),
    ],
)
def test_transitions(src, dst, ok):
    assert can_transition(src, dst) is ok


def test_only_ready_can_be_registered():
    sources = [s for s, targets in TRANSITIONS.items() if S.REGISTERED in targets]
    assert sources == [S.READY_FOR_CONFIRMATION]


def test_ensure_transition_raises():
    with pytest.raises(InvalidTransitionError):
        ensure_transition(S.ANALYZING, S.REGISTERED)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Яма на улице Ленина, д. 5", "улице Ленина, д. 5"),
        ("Во дворе дома 5 по улице Ленина яма", "улице Ленина, д. 5"),
        ("Проспект Мира, дом 31 — нет света", "Проспект Мира, дом 31"),
        ("ул. Гагарина, д. 7, корп. 2", "ул. Гагарина, д. 7, корп. 2"),
        ("Нет адреса вообще", None),
    ],
)
def test_extract_address(text, expected):
    assert extract_address(text) == expected


def test_extractor_does_not_invent_data():
    res = extract("Прошу разъяснить порядок получения пособия.")
    assert res.address.raw is None and res.address.normalized is None
    assert res.topics[0].name == "Социальная защита"
    assert "адрес" not in res.missing_information  # для соцзащиты адрес не обязателен


def test_extractor_urgency_and_evidence():
    res = extract(URGENT)
    assert res.urgency.level == "critical"
    assert res.urgency.requires_human_review
    assert any("газом" in e for e in res.evidence)
    assert extract(NORMAL).urgency.level == "normal"


def test_extractor_streetlight_is_not_fire():
    res = extract("На улице Ленина у дома 9 не горит фонарь уже неделю.")
    assert res.urgency.level == "normal"


def test_multitopic():
    res = extract("Во дворе дома 20 на бульваре Космонавтов сломан фонарь, а ещё не вывозят мусор.")
    assert len({t.name for t in res.topics}) == 2
    assert res.urgency.requires_human_review


def test_similarity():
    a = "Во дворе дома 5 по улице Ленина огромная яма на дороге"
    b = "На улице Ленина, д. 5 яма в асфальте"
    assert normalize_address("улице Ленина, д. 5") == normalize_address("ул. Ленина, дом 5") == "ленина 5"
    assert similarity(a, b, "улице Ленина, д. 5", "ул. Ленина, д. 5", True) >= 0.5
    assert similarity(a, b, "улице Ленина, д. 5", "ул. Ленина, д. 5", False) < 0.5


def test_deadline(db):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    assert calculate_deadline(db, rule_id=None, urgency_level="critical", received_at=t0).due_at.day == 2
    assert calculate_deadline(db, rule_id=None, urgency_level="normal", received_at=t0).due_at.day == 31
    assert calculate_deadline(db, rule_id=1, urgency_level="normal", received_at=t0.replace(tzinfo=None)).rule_id == 1


def test_analytics(client, analyzed):
    rid = analyzed(NORMAL)["request_id"]
    analyzed(URGENT)
    client.post(f"/api/v1/requests/{rid}/review", json={"decision": "correct", "corrected_fields": {"summary": "x"}})
    client.post(f"/api/v1/requests/{rid}/confirm-registration")
    s = client.get("/api/v1/analytics/summary").json()
    assert s["total"] == 2
    assert s["by_status"] == {"registered": 1, "urgent_escalation": 1}
    assert s["by_topic"] == {"ЖКХ": 1, "Аварийная ситуация": 1}
    assert s["urgent"] == 1 and s["registered"] == 1
    assert s["correction_rate"] == 1.0
    assert s["technical_error_rate"] == 0.0
