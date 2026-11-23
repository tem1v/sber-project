import os
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_agent
from app.db.base import Base
from app.db.seed import seed_reference_data
from app.db.session import get_db, make_engine
from app.main import create_app

# Тесты по умолчанию идут на SQLite; в CI дополнительно на PostgreSQL через TEST_DATABASE_URL.


@pytest.fixture
def db_url(tmp_path) -> str:
    return os.getenv("TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def engine(db_url):
    eng = make_engine(db_url)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session_factory(engine) -> sessionmaker[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        seed_reference_data(s)
        s.commit()
    return factory


@pytest.fixture
def db(session_factory) -> Iterator[Session]:
    with session_factory() as s:
        yield s


@pytest.fixture
def app(session_factory):
    application = create_app()

    def _get_db():
        with session_factory() as s:
            yield s

    application.dependency_overrides[get_db] = _get_db
    return application


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def override_agent(app) -> Callable[[object], None]:
    def _set(agent) -> None:
        app.dependency_overrides[get_agent] = lambda: agent

    return _set


@pytest.fixture
def create(client) -> Callable[..., dict]:
    def _create(text: str, **extra) -> dict:
        r = client.post("/api/v1/requests", json={"raw_text": text, **extra})
        assert r.status_code == 201, r.text
        return r.json()

    return _create


@pytest.fixture
def analyzed(client, create) -> Callable[..., dict]:
    """Создать обращение и прогнать анализ; вернуть ответ analyze."""

    def _run(text: str, **extra) -> dict:
        req = create(text, **extra)
        r = client.post(f"/api/v1/requests/{req['id']}/analyze")
        assert r.status_code == 200, r.text
        return r.json()

    return _run


NORMAL = "Не работает лифт по адресу ул. Гагарина, д. 7, жильцы не могут подняться."
URGENT = "В подъезде на ул. Садовая, д. 12 сильно пахнет газом!"
NO_ADDRESS = "Во дворе не вывозят мусор, контейнеры переполнены уже неделю."
