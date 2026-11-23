"""Решения сотрудника, черновики и подтверждение (идемпотентность, запрет без черновика)."""

from tests.conftest import NO_ADDRESS, NORMAL, URGENT


def _review(client, rid, **body):
    return client.post(f"/api/v1/requests/{rid}/review", json=body)


def test_confirm_impossible_before_draft(client, create, analyzed):
    new = create(NORMAL)
    r = client.post(f"/api/v1/requests/{new['id']}/confirm-registration")
    assert r.status_code == 409 and r.json()["code"] == "DRAFT_NOT_READY"

    res = analyzed("Прошу разъяснить порядок получения пособия.")  # проанализировано, решения ещё нет
    r = client.post(f"/api/v1/requests/{res['request_id']}/confirm-registration")
    assert r.status_code == 409
    detail = client.get(f"/api/v1/requests/{res['request_id']}").json()
    assert detail["request"]["status"] == "needs_review"


def test_accept_then_confirm_registration(client, analyzed):
    rid = analyzed(NORMAL)["request_id"]
    r = _review(client, rid, decision="accept", reviewer="operator-7", comment="ок")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready_for_confirmation"
    assert body["review"]["reviewer"] == "operator-7" and body["review"]["created_at"]
    draft = body["draft"]
    assert draft["action_type"] == "registration" and draft["status"] == "pending"
    assert draft["payload"]["department_name"] == "Управление ЖКХ района"
    assert draft["payload"]["due_at"]

    detail = client.get(f"/api/v1/requests/{rid}").json()
    assert "confirm_registration" in detail["allowed_actions"]
    assert detail["request"]["registration_number"] is None  # ещё не зарегистрировано

    c1 = client.post(f"/api/v1/requests/{rid}/confirm-registration", json={"confirmed_by": "operator-7"})
    assert c1.status_code == 200, c1.text
    c1 = c1.json()
    assert c1["status"] == "registered" and not c1["already_confirmed"]
    assert c1["registration_number"].startswith("CF-")
    assert c1["draft"]["confirmed_by"] == "operator-7"


def test_repeat_confirm_is_idempotent(client, analyzed, db):
    from sqlalchemy import func, select

    from app.db.models import ActionDraft

    rid = analyzed(NORMAL)["request_id"]
    _review(client, rid, decision="accept")
    first = client.post(f"/api/v1/requests/{rid}/confirm-registration").json()
    second = client.post(f"/api/v1/requests/{rid}/confirm-registration")
    assert second.status_code == 200
    second = second.json()
    assert second["already_confirmed"] is True
    assert second["registration_number"] == first["registration_number"]
    assert second["draft"]["id"] == first["draft"]["id"]
    confirmed = db.scalar(
        select(func.count(ActionDraft.id)).where(ActionDraft.request_id == rid, ActionDraft.status == "confirmed")
    )
    assert confirmed == 1


def test_correct_keeps_model_output(client, analyzed):
    rid = analyzed(NORMAL)["request_id"]
    depts = {d["code"]: d["id"] for d in client.get("/api/v1/departments").json()}
    r = _review(
        client, rid, decision="correct",
        corrected_fields={"address": "ул. Гагарина, д. 7, корп. 2", "department_id": depts["GKH"], "priority": "high"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["draft"]["payload"]["address"] == "ул. Гагарина, д. 7, корп. 2"
    assert r.json()["review"]["corrected_fields"]["priority"] == "high"

    detail = client.get(f"/api/v1/requests/{rid}").json()
    assert detail["request"]["address"] == "ул. Гагарина, д. 7, корп. 2"
    assert detail["request"]["priority"] == "high"
    # исходный вывод модели не перезаписан
    assert detail["analysis"]["extracted"]["address"]["raw"] == "ул. Гагарина, д. 7"
    assert detail["analysis"]["urgency_level"] == "normal"


def test_correct_validation(client, analyzed):
    rid = analyzed(NORMAL)["request_id"]
    assert _review(client, rid, decision="correct").json()["code"] == "NO_CORRECTIONS"
    archive = next(
        d["id"] for d in client.get("/api/v1/departments", params={"include_inactive": True}).json()
        if d["code"] == "ARCHIVE"
    )
    r = _review(client, rid, decision="correct", corrected_fields={"department_id": archive})
    assert r.status_code == 422 and r.json()["code"] == "INVALID_DEPARTMENT"
    r = _review(client, rid, decision="correct", corrected_fields={"department_id": 9999})
    assert r.status_code == 422
    r = _review(client, rid, decision="correct", corrected_fields={"hacker_field": 1})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"
    r = _review(client, rid, decision="accept", corrected_fields={"address": "x"})
    assert r.json()["code"] == "UNEXPECTED_CORRECTIONS"
    # после неудачных попыток статус не изменился, черновиков нет
    detail = client.get(f"/api/v1/requests/{rid}").json()
    assert detail["request"]["status"] == "needs_review" and detail["drafts"] == []


def test_accept_without_department_requires_correction(client, analyzed):
    rid = analyzed("Помогите")["request_id"]
    r = _review(client, rid, decision="accept")
    assert r.status_code == 422 and r.json()["code"] == "DEPARTMENT_REQUIRED"


def test_reject(client, analyzed):
    rid = analyzed(NORMAL)["request_id"]
    _review(client, rid, decision="accept")  # был черновик — должен отмениться
    r = _review(client, rid, decision="reject", comment="спам")
    assert r.json()["status"] == "rejected"
    detail = client.get(f"/api/v1/requests/{rid}").json()
    assert all(d["status"] == "cancelled" for d in detail["drafts"])
    assert client.post(f"/api/v1/requests/{rid}/confirm-registration").status_code == 409
    assert _review(client, rid, decision="accept").status_code == 409


def test_clarification_flow(client, analyzed):
    rid = analyzed(NO_ADDRESS)["request_id"]
    # отправить уточнение без черновика нельзя
    assert client.post(f"/api/v1/requests/{rid}/clarification").status_code == 409

    r = _review(client, rid, decision="request_clarification")
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    assert draft["action_type"] == "clarification"
    assert any("адрес" in q for q in draft["payload"]["questions"])
    assert r.json()["status"] == "needs_clarification"

    c1 = client.post(f"/api/v1/requests/{rid}/clarification", json={"confirmed_by": "op"}).json()
    assert c1["already_confirmed"] is False and c1["draft"]["status"] == "confirmed"
    c2 = client.post(f"/api/v1/requests/{rid}/clarification").json()
    assert c2["already_confirmed"] is True and c2["draft"]["id"] == c1["draft"]["id"]


def test_clarification_needs_questions(client, analyzed):
    rid = analyzed(NORMAL)["request_id"]  # в анализе вопросов нет
    r = _review(client, rid, decision="request_clarification")
    assert r.status_code == 422 and r.json()["code"] == "NO_QUESTIONS"
    r = _review(client, rid, decision="request_clarification", questions=["Какой подъезд?"])
    assert r.status_code == 200 and r.json()["draft"]["payload"]["questions"] == ["Какой подъезд?"]


def test_urgent_can_be_accepted_after_manual_check(client, analyzed):
    rid = analyzed(URGENT)["request_id"]
    r = _review(client, rid, decision="accept", comment="Передано в аварийную службу, проверено вручную")
    assert r.json()["status"] == "ready_for_confirmation"


def test_escalate(client, analyzed):
    rid = analyzed(NORMAL)["request_id"]
    _review(client, rid, decision="accept")
    r = _review(client, rid, decision="escalate", comment="жалоба повторная")
    assert r.json()["status"] == "urgent_escalation"
    assert client.post(f"/api/v1/requests/{rid}/confirm-registration").status_code == 409


def test_review_not_allowed_for_new(client, create):
    rid = create(NORMAL)["id"]
    r = _review(client, rid, decision="accept")
    assert r.status_code == 409 and r.json()["code"] == "INVALID_STATUS_TRANSITION"


def test_reaccept_replaces_pending_draft(client, analyzed):
    rid = analyzed(NORMAL)["request_id"]
    _review(client, rid, decision="accept")
    _review(client, rid, decision="correct", corrected_fields={"summary": "Не работает лифт"})
    drafts = client.get(f"/api/v1/requests/{rid}").json()["drafts"]
    assert [d["status"] for d in drafts] == ["cancelled", "pending"]
