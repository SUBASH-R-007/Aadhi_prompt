"""Migration ``0004_claim_operation`` (``asset_claims.operation``): upgrade keeps existing claims, downgrade
removes the column, idempotent after a development ``add_missing_columns``, and the Postgres DDL (offline)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from aadhi.config import ROOT_DIR
from alembic import command
from tests.storage.test_asset_claims_migration import _model_drift

REV = "0004_claim_operation"


def _config(db_path: Path) -> Config:
    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    cfg.attributes["configure_logger"] = False
    return cfg


def _columns(db: Path) -> dict[str, dict]:
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        return {c["name"]: c for c in inspect(engine).get_columns("asset_claims")}
    finally:
        engine.dispose()


def test_revision_follows_0003():
    rev = ScriptDirectory.from_config(_config(Path("unused.db"))).get_revision(REV)
    assert rev is not None and rev.down_revision == "0003_asset_claims"


def test_upgrade_keeps_claims_and_downgrade_removes_the_column(app_env, tmp_path):
    db = tmp_path / "claims.db"
    cfg = _config(db)
    command.upgrade(cfg, "0003_asset_claims")
    con = sqlite3.connect(db)
    con.execute("INSERT INTO asset_claims (key, holder, created_at, expires_at) VALUES "
                "('video-x', 'h1', '2026-10-07 00:00:00', '2026-10-07 00:02:00')")
    con.commit()
    con.close()

    command.upgrade(cfg, REV)
    cols = _columns(db)
    assert "operation" in cols and cols["operation"]["nullable"]
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT key, holder, operation FROM asset_claims").fetchall() == [("video-x", "h1", None)]
    finally:
        con.close()
    assert _model_drift(db) == []  # the AssetClaim model and the migrated table agree

    command.downgrade(cfg, "0003_asset_claims")
    assert "operation" not in _columns(db)
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT key FROM asset_claims").fetchall() == [("video-x",)]
    finally:
        con.close()
    command.upgrade(cfg, REV)
    assert "operation" in _columns(db)


def test_upgrade_is_idempotent_after_add_missing_columns(app_env, tmp_path):
    """A development database whose startup already added the column in place."""
    from aadhi.dev_schema import add_missing_columns

    db = tmp_path / "dev.db"
    cfg = _config(db)
    command.upgrade(cfg, "0003_asset_claims")
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        assert "asset_claims.operation" in add_missing_columns(engine)  # other models may add columns too
    finally:
        engine.dispose()
    command.upgrade(cfg, REV)
    assert _model_drift(db) == []


def test_offline_postgres_sql(app_env, capsys):
    cfg = _config(Path("unused.db"))
    cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://user:pw@localhost/aadhi")
    command.upgrade(cfg, f"0003_asset_claims:{REV}", sql=True)
    assert "ALTER TABLE asset_claims ADD COLUMN operation JSONB" in capsys.readouterr().out
