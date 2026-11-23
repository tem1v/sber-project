"""Анализ агентом: статусы, сохранение результатов, трассировка, отказ LLM."""

from app.agent.base import AgentUnavailableError
from app.api.deps import get_agent
from app.agent.state import AgentState
from tests.conftest import NO_ADDRESS, NORMAL, URGENT


def test_analyze_normal_request(client, analyzed):
    res = analyzed(NORMAL)
    assert res["status"] == "needs_review"
    assert res["run_status"] == "succeeded"
    a = res["analysis"]
    assert a["version"] == 1
    assert a["extracted"]["address"]["raw"].startswith("ул. Гагарина")
    assert a["topics"][0]["name"] == "ЖКХ"
    assert a["urgency_level"] == "normal"

    detail = client.get(f"/api/v1/requests/{res['request_id']}").json()
    req = detail["request"]
    assert req["department_name"] == "Управление ЖКХ района"
    assert req["topic"] == "ЖКХ" and req["subtopic"] == "Лифт"
    assert detail["deadline"]["calculation_note"].startswith("Правило")
    assert detail["route_check"]["decision"] in {"ok", "needs_review"}
    assert detail["drafts"] == []  # анализ не готовит и не выполняет действий
    assert req["registration_number"] is None
    assert set(detail["allowed_actions"]) == {"analyze", "review"}


def test_analyze_urgent_goes_to_escalation(client, analyzed):
    res = analyzed(URGENT)
    assert res["status"] == "urgent_escalation"
    assert res["route_check"]["decision"] == "escalate"
    assert any("газом" in r for r in res["route_check"]["reasons"])
    detail = client.get(f"/api/v1/requests/{res['request_id']}").json()
    assert detail["request"]["priority"] == "critical"
    assert "1 календ" in detail["deadline"]["calculation_note"]


def test_missing_address_needs_clarification(client, analyzed):
    res = analyzed(NO_ADDRESS)
    assert res["status"] == "needs_clarification"
    assert "адрес" in res["analysis"]["extracted"]["missing_information"]
    assert res["analysis"]["extracted"]["clarifying_questions"]


def test_empty_meaning_needs_clarification(analyzed):
    res = analyzed("Помогите")
    assert res["status"] == "needs_clarification"
    assert res["analysis"]["confidence"] < 0.2


def test_duplicates_are_links_not_merges(client, analyzed):
    first = analyzed("Во дворе дома 5 по улице Ленина огромная яма на дороге, машины ломаются.")
    second = analyzed("На улице Ленина, д. 5 яма в асфальте, невозможно проехать.")
    assert second["status"] == "duplicate_candidate"
    assert second["similar_count"] == 1

    d2 = client.get(f"/api/v1/requests/{second['request_id']}").json()
    assert d2["similar"][0]["related_request_id"] == first["request_id"]
    assert d2["similar"][0]["status"] == "candidate"
    # связь видна с обеих сторон, оба обращения остаются отдельными
    d1 = client.get(f"/api/v1/requests/{first['request_id']}").json()
    assert d1["similar"][0]["related_request_id"] == second["request_id"]
    assert client.get("/api/v1/requests").json()["total"] == 2


def test_different_topic_same_address_is_not_duplicate(analyzed):
    analyzed("Во дворе дома 5 по улице Ленина огромная яма на дороге.")
    res = analyzed("На улице Ленина, д. 5 не работает лифт.")
    assert res["similar_count"] == 0


def test_link_review(client, analyzed):
    analyzed("Во дворе дома 5 по улице Ленина огромная яма на дороге, машины ломаются.")
    second = analyzed("На улице Ленина, д. 5 яма в асфальте, невозможно проехать.")
    rid = second["request_id"]
    link_id = client.get(f"/api/v1/requests/{rid}").json()["similar"][0]["id"]

    r = client.patch(f"/api/v1/requests/{rid}/links/{link_id}", json={"decision": "confirm", "reviewer": "u1"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed" and r.json()["reviewed_by"] == "u1"
    # повтор того же решения идемпотентен, противоположное — конфликт
    assert client.patch(f"/api/v1/requests/{rid}/links/{link_id}", json={"decision": "confirm"}).status_code == 200
    assert client.patch(f"/api/v1/requests/{rid}/links/{link_id}", json={"decision": "reject"}).status_code == 409
    assert client.patch(f"/api/v1/requests/{rid}/links/99999", json={"decision": "reject"}).status_code == 404


def test_trace_is_saved(client, analyzed):
    res = analyzed(NORMAL)
    trace = client.get(f"/api/v1/requests/{res['request_id']}/trace").json()
    assert len(trace["runs"]) == 1
    run = trace["runs"][0]
    assert run["status"] == "succeeded" and run["agent_mode"] == "rule_based"
    tools = [s["tool_name"] for s in run["steps"]]
    assert tools[0] == "analyze_text"
    assert {"search_similar", "find_department", "validate_route", "calculate_deadline", "finish"} <= set(tools)
    assert tools[-1] == "validate_route"  # финальная проверка backend
    assert [s["step_no"] for s in run["steps"]] == list(range(1, len(tools) + 1))
    assert all(s["duration_ms"] >= 0 for s in run["steps"])
    assert run["steps_count"] == len(tools)


def test_reanalysis_creates_new_version(client, analyzed):
    res = analyzed(NORMAL)
    rid = res["request_id"]
    again = client.post(f"/api/v1/requests/{rid}/analyze").json()
    assert again["analysis"]["version"] == 2
    detail = client.get(f"/api/v1/requests/{rid}").json()
    assert detail["analysis_versions"] == 2
    assert len(client.get(f"/api/v1/requests/{rid}/trace").json()["runs"]) == 2


class _DownAgent:
    name = "down"

    def run(self, state: AgentState, tools):
        raise AgentUnavailableError("GigaChat: timeout")


class _BrokenAgent:
    name = "broken"

    def run(self, state: AgentState, tools):
        tools.call("analyze_text")
        raise RuntimeError("boom")


class _LoopingAgent:
    name = "looping"

    def run(self, state: AgentState, tools):
        while True:
            tools.call("search_similar")


class _BadToolAgent:
    """Ошибка инструмента возвращается агенту, а не роняет запуск."""

    name = "bad_tool"

    def run(self, state: AgentState, tools):
        r1 = tools.call("no_such_tool")
        r2 = tools.call("search_similar", {"limit": 0})
        assert not r1.ok and not r2.ok
        tools.call("analyze_text")
        return state


def test_llm_unavailable_marks_failed_and_allows_retry(client, create, override_agent):
    req = create(NORMAL)
    override_agent(_DownAgent())
    r = client.post(f"/api/v1/requests/{req['id']}/analyze")
    assert r.status_code == 503
    assert r.json()["code"] == "LLM_UNAVAILABLE"
    detail = client.get(f"/api/v1/requests/{req['id']}").json()
    assert detail["request"]["status"] == "failed"
    run = client.get(f"/api/v1/requests/{req['id']}/trace").json()["runs"][0]
    assert run["status"] == "failed" and "timeout" in run["error"]

    # «LLM снова доступна» — повторный анализ из статуса failed разрешён
    client.app.dependency_overrides.pop(get_agent)
    retry = client.post(f"/api/v1/requests/{req['id']}/analyze")
    assert retry.status_code == 200 and retry.json()["status"] == "needs_review"


def test_agent_crash_is_controlled(client, create, override_agent):
    req = create(NORMAL)
    override_agent(_BrokenAgent())
    r = client.post(f"/api/v1/requests/{req['id']}/analyze")
    assert r.status_code == 500 and r.json()["code"] == "AGENT_FAILED"
    run = client.get(f"/api/v1/requests/{req['id']}/trace").json()["runs"][0]
    assert run["status"] == "failed"
    assert run["steps"][0]["tool_name"] == "analyze_text"  # шаги до сбоя сохранены
    assert client.get(f"/api/v1/requests/{req['id']}").json()["request"]["status"] == "failed"


def test_step_limit_stops_loop(client, create, override_agent):
    req = create(NORMAL)
    override_agent(_LoopingAgent())
    r = client.post(f"/api/v1/requests/{req['id']}/analyze")
    assert r.status_code == 500
    run = client.get(f"/api/v1/requests/{req['id']}/trace").json()["runs"][0]
    assert "лимит шагов" in run["error"]
    assert len(run["steps"]) == 12


def test_tool_errors_are_returned_to_agent(client, create, override_agent):
    req = create(NORMAL)
    override_agent(_BadToolAgent())
    r = client.post(f"/api/v1/requests/{req['id']}/analyze")
    assert r.status_code == 200
    steps = client.get(f"/api/v1/requests/{req['id']}/trace").json()["runs"][0]["steps"]
    assert "Неизвестный инструмент" in steps[0]["error"]
    assert "Некорректные аргументы" in steps[1]["error"]


def test_cannot_analyze_while_registered(client, analyzed):
    res = analyzed(NORMAL)
    rid = res["request_id"]
    client.post(f"/api/v1/requests/{rid}/review", json={"decision": "accept"})
    client.post(f"/api/v1/requests/{rid}/confirm-registration")
    r = client.post(f"/api/v1/requests/{rid}/analyze")
    assert r.status_code == 409 and r.json()["code"] == "INVALID_STATUS_TRANSITION"
