"""БД: миграции, ограничения целостности, сохранность после перезапуска."""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.taxonomy import utcnow
from app.db.models import ActionDraft, Request, RequestLink, RoutingRule
from app.db.seed import seed_demo_requests, seed_reference_data
from app.db.session import make_engine

ROOT = Path(__file__).resolve().parents[1]


def _alembic(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "app/db/migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.mark.skipif(bool(os.getenv("TEST_DATABASE_URL")), reason="миграции на PG проверяются отдельным шагом CI")
def test_migrations_up_down_and_match_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    cfg = _alembic(url)
    command.upgrade(cfg, "head")
    command.check(cfg)  # модели и миграции не расходятся
    tables = set(inspect(make_engine(url)).get_table_names())
    assert {
        "requests", "request_analysis", "departments", "routing_rules", "request_links",
        "deadlines", "reviews", "agent_runs", "agent_trace", "action_drafts",
    } <= tables
    command.downgrade(cfg, "base")
    assert set(inspect(make_engine(url)).get_table_names()) <= {"alembic_version"}


def _req(**kw) -> Request:
    return Request(source_channel=kw.pop("source_channel", "web"), raw_text=kw.pop("raw_text", "текст"),
                   received_at=utcnow(), **kw)


def test_status_check_constraint(db):
    db.add(_req(status="whatever"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_foreign_keys_enforced(db):
    db.add(RoutingRule(topic="X", department_id=9999, deadline_days=5, conditions={}))
    with pytest.raises(IntegrityError):
        db.commit()


def test_link_to_self_forbidden(db):
    r = _req()
    db.add(r)
    db.commit()
    db.add(RequestLink(request_id=r.id, related_request_id=r.id, similarity_score=0.9))
    with pytest.raises(IntegrityError):
        db.commit()


def test_single_confirmed_registration(db):
    r = _req()
    db.add(r)
    db.commit()
    for _ in range(2):
        db.add(ActionDraft(request_id=r.id, action_type="registration", payload_json={}, status="confirmed", created_by="u"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_single_pending_draft_per_type(db):
    r = _req()
    db.add(r)
    db.commit()
    for _ in range(2):
        db.add(ActionDraft(request_id=r.id, action_type="clarification", payload_json={}, status="pending", created_by="u"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_data_survives_restart(client, analyzed, db_url, engine):
    rid = analyzed("Не работает лифт по адресу ул. Гагарина, д. 7.")["request_id"]
    client.post(f"/api/v1/requests/{rid}/review", json={"decision": "accept", "reviewer": "op"})

    # «перезапуск»: новый engine и новая сессия к той же БД
    engine.dispose()
    fresh = make_engine(db_url)
    with sessionmaker(bind=fresh)() as s:
        req = s.get(Request, rid)
        assert req.status == "ready_for_confirmation"
        assert len(req.analyses) == 1 and len(req.reviews) == 1
        run = req.runs[0]
        assert run.state_json["analysis"]["topics"][0]["name"] == "ЖКХ"
        assert len(run.steps) >= 5
    fresh.dispose()


def test_seed_is_idempotent(db):
    seed_reference_data(db)  # справочники уже засеяны фикстурой — повтор ничего не меняет
    ids = seed_demo_requests(db)
    db.commit()
    assert len(ids) == 12
    assert seed_demo_requests(db) == []
