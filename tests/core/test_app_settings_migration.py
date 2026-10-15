"""Migration 0007 (``app_settings``): the administrator's runtime switches.

It follows the visual-review migration, creates the table the model describes, keeps a switch when the admin who
set it is deleted, is idempotent after a development ``create_all``, and downgrades. The switch's behaviour is
``tests/api/test_admin_settings.py``; this is the schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from aadhi.config import ROOT_DIR
from alembic import command


def _config(db_path: Path) -> Config:
    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
    cfg.attributes["configure_logger"] = False
    return cfg


def test_0007_follows_the_visual_review_migration():
    script = ScriptDirectory.from_config(_config(Path("unused.db")))
    assert script.get_revision("0007_app_settings").down_revision == "0006_visual_reviews"


def test_0007_creates_the_table_and_matches_the_model(app_env, tmp_path):
    db = tmp_path / "flags.db"
    cfg = _config(db)
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        insp = inspect(engine)
        columns = {c["name"]: c for c in insp.get_columns("app_settings")}
        assert set(columns) == {"key", "value", "updated_by", "updated_at"}
        assert not columns["value"]["nullable"] and columns["updated_by"]["nullable"]
        assert insp.get_pk_constraint("app_settings")["constrained_columns"] == ["key"]  # one row per key
        fks = {fk["constrained_columns"][0]: fk for fk in insp.get_foreign_keys("app_settings")}
        assert fks["updated_by"]["options"].get("ondelete") == "SET NULL"
    finally:
        engine.dispose()
    command.check(cfg)  # no drift between the models and the migrated schema


def test_0007_keeps_a_switch_when_the_admin_who_set_it_is_deleted(app_env, tmp_path):
    """A switch is server state, not the admin's: deleting them must not quietly change the video mode."""
    db = tmp_path / "cascade.db"
    cfg = _config(db)
    command.upgrade(cfg, "head")
    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("INSERT INTO users (id, username, password_hash, role, is_active, must_change_password, "
                    "token_version, created_at, updated_at) VALUES (1, 'root', 'x', 'admin', 1, 0, 0, "
                    "'2026-10-15', '2026-10-15')")
        con.execute("INSERT INTO app_settings (key, value, updated_by, updated_at) "
                    "VALUES ('dynamic_enabled', 'true', 1, '2026-10-15')")
        con.execute("DELETE FROM users WHERE id = 1")
        assert con.execute("SELECT value, updated_by FROM app_settings").fetchall() == [("true", None)]
        con.commit()
    finally:
        con.close()
    command.downgrade(cfg, "0006_visual_reviews")
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT name FROM sqlite_master WHERE name='app_settings'").fetchone() is None
        assert con.execute("SELECT name FROM sqlite_master WHERE name='visual_reviews'").fetchone() is not None
    finally:
        con.close()
    command.upgrade(cfg, "head")


def test_0007_is_idempotent_after_create_all(app_env, tmp_path):
    """A development database at 0006 whose startup ``create_all`` already made the table."""
    from aadhi.db import Base

    db = tmp_path / "dev.db"
    cfg = _config(db)
    command.upgrade(cfg, "0006_visual_reviews")
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        Base.metadata.tables["app_settings"].create(engine)
    finally:
        engine.dispose()
    command.upgrade(cfg, "head")
    command.check(cfg)
