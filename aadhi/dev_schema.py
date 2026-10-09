"""Bring a development database created by ``create_all`` up to the models.

``create_all`` creates missing tables but never alters existing ones, so a development SQLite database
created before a column was added (e.g. ``usage_events.billed_to``) would fail at the first query that
uses it. ``add_missing_columns`` adds such columns in place (``ALTER TABLE ... ADD COLUMN``) when that
is safe: nullable columns and columns with a server default. Anything else still needs a real
migration (``python -m aadhi.cli migrate``). Production and non-SQLite databases never come here
(``aadhi.main.prepare_database`` requires them to be at the Alembic head).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from sqlalchemy import Column, Table, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.schema import CreateColumn

from . import models  # noqa: F401  (registers every table on Base.metadata)
from .db import Base

log = logging.getLogger(__name__)

__all__ = ["add_missing_columns", "addable", "missing_columns", "only_additive"]


def addable(column: Column[Any]) -> bool:
    """Whether ``column`` can be added to a table that already holds rows."""
    return not column.primary_key and (bool(column.nullable) or column.server_default is not None)


def missing_columns(engine: Engine) -> list[tuple[Table, Column[Any]]]:
    """Model columns that existing tables of the database lack."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    out: list[tuple[Table, Column[Any]]] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        out.extend((table, column) for column in table.columns if column.name not in existing)
    return out


def add_missing_columns(engine: Engine) -> list[str]:
    """Add every missing column that ``addable`` allows; returns ``["table.column", ...]`` added."""
    pending = missing_columns(engine)
    if not pending:
        return []
    quote = engine.dialect.identifier_preparer.quote
    added: list[str] = []
    with engine.begin() as conn:
        for table, column in pending:
            if not addable(column):
                log.warning(
                    "development database: column %s.%s is missing and cannot be added in place; run "
                    "`python -m aadhi.cli migrate`", table.name, column.name,
                )
                continue
            ddl = CreateColumn(column).compile(dialect=engine.dialect)
            conn.exec_driver_sql(f"ALTER TABLE {quote(table.name)} ADD COLUMN {ddl}")
            added.append(f"{table.name}.{column.name}")
    if added:
        log.warning("development database: added missing columns %s", ", ".join(added))
    return added


def only_additive(diffs: Iterable[Any]) -> bool:
    """Whether every Alembic autogenerate difference is a missing table (with its indexes) or a missing
    column that ``addable`` allows, i.e. ``create_all`` + ``add_missing_columns`` make the database
    match the models."""
    items = list(diffs)
    new_tables = {d[1].name for d in items if isinstance(d, tuple) and d and d[0] == "add_table"}
    for diff in items:
        if not isinstance(diff, tuple) or not diff:  # modify_* changes come grouped in lists
            return False
        kind = diff[0]
        if kind == "add_table":
            continue
        if kind == "add_index" and diff[1].table is not None and diff[1].table.name in new_tables:
            continue
        if kind == "add_column" and addable(diff[3]):
            continue
        return False
    return True
