"""Migration ``0003_asset_claims``: upgrade / downgrade on a temporary SQLite database, idempotent after
a development ``create_all``, the model matches the migrated schema, and the Postgres DDL (offline)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, create_engine, inspect

from aadhi.config import ROOT_DIR
from aadhi.db import Base
from aadhi.models import AssetClaim
from alembic import command

REV = "0003_asset_claims"


def _config(db_path: Path) -> Config:
    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    cfg.attributes["configure_logger"] = False
    return cfg


def _tables(db: Path) -> set[str]:
    con = sqlite3.connect(db)
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def _model_drift(db: Path) -> list[object]:
    """Autogenerate diff between the ``asset_claims`` model and the migrated table (nothing else)."""
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={
                "compare_type": True,
                "include_object": lambda obj, name, type_, reflected, compare_to: (
                    (type_ == "table" and name == "asset_claims")
                    or getattr(getattr(obj, "table", None), "name", None) == "asset_claims"
                ),
            })
            only = MetaData(naming_convention=Base.metadata.naming_convention)
            AssetClaim.__table__.to_metadata(only)
            return list(compare_metadata(ctx, only))
    finally:
        engine.dispose()


def test_revision_follows_0002():
    script = ScriptDirectory.from_config(_config(Path("unused.db")))
    rev = script.get_revision(REV)
    assert rev is not None and rev.down_revision == "0002"


def test_upgrade_downgrade_roundtrip(app_env, tmp_path):
    db = tmp_path / "claims.db"
    cfg = _config(db)
    command.upgrade(cfg, "0002")
    assert "asset_claims" not in _tables(db)
    command.upgrade(cfg, REV)
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        insp = inspect(engine)
        cols = {c["name"]: c for c in insp.get_columns("asset_claims")}
        assert set(cols) == {"key", "holder", "job_id", "worker_id", "attempt", "created_at", "expires_at"}
        assert not cols["key"]["nullable"] and not cols["holder"]["nullable"] and not cols["expires_at"]["nullable"]
        assert cols["job_id"]["nullable"] and cols["worker_id"]["nullable"] and cols["attempt"]["nullable"]
        pk = insp.get_pk_constraint("asset_claims")
        assert pk["constrained_columns"] == ["key"] and pk["name"] == "pk_asset_claims"
        assert {ix["name"]: ix["column_names"] for ix in insp.get_indexes("asset_claims")} == {
            "ix_asset_claims_expires_at": ["expires_at"]}
    finally:
        engine.dispose()
    # the model and the migration agree, apart from the column migration 0004 adds later
    assert [(d[0], d[3].name) for d in _model_drift(db)] == [("add_column", "operation")]

    command.downgrade(cfg, "0002")
    assert "asset_claims" not in _tables(db) and "jobs" in _tables(db)
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT version_num FROM alembic_version").fetchall() == [("0002",)]
    finally:
        con.close()
    command.upgrade(cfg, REV)  # and up again
    assert "asset_claims" in _tables(db)


def test_upgrade_is_idempotent_after_create_all(app_env, tmp_path):
    """A development database whose startup ``create_all`` already made the table."""
    db = tmp_path / "dev.db"
    cfg = _config(db)
    command.upgrade(cfg, "0002")
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        AssetClaim.__table__.create(engine)
    finally:
        engine.dispose()
    command.upgrade(cfg, REV)
    assert _model_drift(db) == []


def test_offline_postgres_sql(app_env, capsys):
    cfg = _config(Path("unused.db"))
    cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://user:pw@localhost/aadhi")
    command.upgrade(cfg, f"0002:{REV}", sql=True)
    sql = capsys.readouterr().out
    assert "CREATE TABLE asset_claims" in sql
    assert "expires_at TIMESTAMP WITH TIME ZONE NOT NULL" in sql
    assert "CONSTRAINT pk_asset_claims PRIMARY KEY (key)" in sql
    assert "CREATE INDEX ix_asset_claims_expires_at ON asset_claims (expires_at)" in sql
