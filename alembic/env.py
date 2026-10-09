"""Alembic environment for Aadhi EduEngine v2.

* URL: ``-x url=...`` > ``sqlalchemy.url`` set on the Config (tests/CLI) > ``Settings.resolved_database_url``.
* Target metadata: ``aadhi.db.Base.metadata`` (all models imported), naming convention included.
* SQLite: ``render_as_batch`` (ALTER via table copy), ``PRAGMA foreign_keys=OFF`` during the
  migration and ``PRAGMA foreign_key_check`` afterwards (fails the migration on violations).
* ``compare_type=True`` so ``alembic check`` also detects column type drift.
* A caller may pass an open connection via ``config.attributes["connection"]``.
"""

from __future__ import annotations

import logging
from logging.config import fileConfig

from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

import aadhi.models  # noqa: F401  (registers every table on Base.metadata)
from aadhi.config import get_settings
from aadhi.db import Base
from alembic import context

config = context.config
log = logging.getLogger("alembic.env")

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("url") or config.get_main_option("sqlalchemy.url") or get_settings().resolved_database_url


def _include_object(obj, name, type_, reflected, compare_to):
    # SQLite's internal tables are never part of the model.
    return not (type_ == "table" and name and name.startswith("sqlite_"))


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
        compare_server_default=False,
        include_object=_include_object,
    )


def _run(connection: Connection) -> None:
    is_sqlite = connection.dialect.name == "sqlite"
    if is_sqlite:
        # Must run outside a transaction; batch table copies would otherwise trip FK checks.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        if is_sqlite:
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"foreign key violations after migration: {violations[:10]}")
    finally:
        if is_sqlite:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")


def run_migrations_offline() -> None:
    """Emit SQL to stdout (``alembic upgrade head --sql``)."""
    url = _url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_engine(_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as conn:
            _run(conn)
            conn.commit()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
