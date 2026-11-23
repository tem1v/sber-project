"""Создание, просмотр, фильтрация обращений и единый формат ошибок."""

from tests.conftest import NORMAL, URGENT


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}


def test_create_request(client, create):
    body = create("  " + NORMAL + "  ", source_channel="email", metadata={"source": "demo"})
    assert body["status"] == "new"
    assert body["raw_text"] == NORMAL  # пробелы по краям срезаны
    assert body["source_channel"] == "email"
    assert body["metadata"] == {"source": "demo"}
    assert body["received_at"]


def test_create_validation_error_format(client):
    r = client.post("/api/v1/requests", json={"raw_text": "   "}, headers={"X-Request-ID": "test-rid-1"})
    assert r.status_code == 422
    err = r.json()
    assert set(err) == {"code", "message", "details", "request_id"}
    assert err["code"] == "VALIDATION_ERROR"
    assert err["request_id"] == "test-rid-1"
    assert r.headers["X-Request-ID"] == "test-rid-1"


def test_create_rejects_unknown_channel(client):
    r = client.post("/api/v1/requests", json={"raw_text": NORMAL, "source_channel": "pigeon"})
    assert r.status_code == 422


def test_unknown_id_returns_404(client):
    for method, path in [
        ("get", "/api/v1/requests/9999"),
        ("post", "/api/v1/requests/9999/analyze"),
        ("post", "/api/v1/requests/9999/confirm-registration"),
        ("get", "/api/v1/requests/9999/trace"),
    ]:
        r = getattr(client, method)(path)
        assert r.status_code == 404, path
        assert r.json()["code"] == "NOT_FOUND"
        assert r.json()["request_id"]


def test_invalid_id_is_validation_error(client):
    assert client.get("/api/v1/requests/0").status_code == 422
    assert client.get("/api/v1/requests/abc").status_code == 422


def test_get_detail_of_new_request(client, create):
    req = create(NORMAL)
    detail = client.get(f"/api/v1/requests/{req['id']}").json()
    assert detail["request"]["id"] == req["id"]
    assert detail["analysis"] is None
    assert detail["allowed_actions"] == ["analyze"]


def test_list_and_filters(client, create, analyzed):
    analyzed(NORMAL)
    analyzed(URGENT)
    create("Прошу разъяснить порядок получения пособия.")

    all_ = client.get("/api/v1/requests").json()
    assert all_["total"] == 3 and len(all_["items"]) == 3
    assert "raw_text" not in all_["items"][0]

    urgent = client.get("/api/v1/requests", params={"status": "urgent_escalation"}).json()
    assert urgent["total"] == 1 and urgent["items"][0]["priority"] == "critical"

    by_two = client.get("/api/v1/requests", params=[("status", "new"), ("status", "needs_review")]).json()
    assert by_two["total"] == 2

    lift = client.get("/api/v1/requests", params={"topic": "ЖКХ", "priority": "normal"}).json()
    assert lift["total"] == 1 and lift["items"][0]["department_name"] == "Управление ЖКХ района"

    dept_id = lift["items"][0]["department_id"]
    assert client.get("/api/v1/requests", params={"department_id": dept_id}).json()["total"] == 1

    assert client.get("/api/v1/requests", params={"q": "пособия"}).json()["total"] == 1
    page = client.get("/api/v1/requests", params={"limit": 1, "offset": 1}).json()
    assert len(page["items"]) == 1 and page["total"] == 3

    assert client.get("/api/v1/requests", params={"status": "bogus"}).status_code == 422


def test_date_filters(client, create):
    create(NORMAL, received_at="2026-01-10T10:00:00+00:00")
    create(NORMAL, received_at="2026-03-10T10:00:00+00:00")
    r = client.get("/api/v1/requests", params={"date_from": "2026-02-01T00:00:00+00:00"}).json()
    assert r["total"] == 1
    r = client.get("/api/v1/requests", params={"date_to": "2026-02-01T00:00:00+00:00"}).json()
    assert r["total"] == 1


def test_departments(client):
    depts = client.get("/api/v1/departments").json()
    codes = {d["code"] for d in depts}
    assert {"GKH", "ROADS", "EDDS"} <= codes
    assert "ARCHIVE" not in codes
    all_codes = {d["code"] for d in client.get("/api/v1/departments", params={"include_inactive": True}).json()}
    assert "ARCHIVE" in all_codes


def test_openapi_available(client):
    spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    for p in [
        "/api/v1/requests",
        "/api/v1/requests/{request_id}",
        "/api/v1/requests/{request_id}/analyze",
        "/api/v1/requests/{request_id}/review",
        "/api/v1/requests/{request_id}/confirm-registration",
        "/api/v1/requests/{request_id}/clarification",
        "/api/v1/requests/{request_id}/trace",
        "/api/v1/departments",
        "/api/v1/analytics/summary",
    ]:
        assert p in paths, p
