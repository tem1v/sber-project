from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def make_engine(url: str) -> Engine:
    is_sqlite = url.startswith("sqlite")
    engine = create_engine(
        url,
        pool_pre_ping=not is_sqlite,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )
    if is_sqlite:
        # В SQLite внешние ключи по умолчанию выключены.
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - инфраструктура
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI-зависимость: одна сессия на запрос."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
