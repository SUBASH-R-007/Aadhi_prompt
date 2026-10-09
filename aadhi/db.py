"""SQLAlchemy engine/session factory (SQLite for dev/test, Postgres in production).

Concurrency rules (all modules):
* State transitions are single atomic SQL statements (``UPDATE ... WHERE <expected state>
  ... RETURNING``) — never ORM read-modify-write. Use ``compare_and_set`` / ``atomic_add``.
* JSON columns are *replaced*, never mutated in place (no mutation tracking).
* ``session.begin()`` blocks must not contain ``await``; async code calls DB helpers via
  ``asyncio.to_thread``.
* Streaming responses (SSE) never hold a request-scoped session: open short sessions per poll.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import MetaData, create_engine, event, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def make_engine(url: str, *, enforce_fk: bool = True) -> Engine:
    s = get_settings()
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" not in url and url not in ("sqlite://", "sqlite+pysqlite://"):  # file DB -> QueuePool
            kwargs.update(pool_size=s.db_pool_size, max_overflow=s.db_max_overflow, pool_timeout=s.db_pool_timeout)
    else:
        kwargs.update(
            pool_size=s.db_pool_size,
            max_overflow=s.db_max_overflow,
            pool_timeout=s.db_pool_timeout,
            connect_args={"options": "-c idle_in_transaction_session_timeout=60000 -c statement_timeout=60000"},
        )
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute(f"PRAGMA foreign_keys={'ON' if enforce_fk else 'OFF'}")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    return engine


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().resolved_database_url)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency (use ``DbSession`` so it closes before streaming bodies)."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


# Request-scoped session that is closed when the endpoint function returns (FastAPI
# ``Depends(scope="function")``), i.e. before a StreamingResponse body is iterated.
DbSession = Annotated[Session, Depends(get_db, scope="function")]


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for workers/CLI: commit on success, rollback on error."""
    db = get_sessionmaker()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def compare_and_set(db: Session, model: Any, ident: int, expected: dict[str, Any], values: dict[str, Any]) -> bool:
    """Atomically ``UPDATE model SET values WHERE id=ident AND <expected>``. Returns True if a row changed.

    Example: ``compare_and_set(db, ProjectVersion, vid, {"revision": 4}, {"revision": 5, "screenplay": sp})``.
    The caller commits.
    """
    stmt = update(model).where(model.id == ident)
    for col, val in expected.items():
        stmt = stmt.where(getattr(model, col) == val)
    result = db.execute(stmt.values(**values).execution_options(synchronize_session=False))
    return (result.rowcount or 0) > 0


def atomic_add(db: Session, model: Any, ident: int, column: str, delta: float) -> float | None:
    """Atomically add ``delta`` to ``column`` and return the new value (None if no row)."""
    col = getattr(model, column)
    row = db.execute(
        update(model).where(model.id == ident).values({column: col + delta}).returning(col)
    ).first()
    return None if row is None else row[0]


def reset_engine_cache() -> None:
    """Tests: dispose the engine and drop cached settings/engine/sessionmaker."""
    try:
        if get_engine.cache_info().currsize:
            get_engine().dispose()
    finally:
        get_sessionmaker.cache_clear()
        get_engine.cache_clear()
        get_settings.cache_clear()


def ping(db: Session) -> bool:
    db.execute(text("SELECT 1"))
    return True
