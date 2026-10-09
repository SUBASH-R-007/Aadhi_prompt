"""Migration 0006 (``visual_reviews``): after the library migration, matches the model (no drift), cascades
with its version, keeps rows when the reviewer is deleted, is idempotent after ``create_all`` and downgrades."""

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


def test_0006_follows_the_library_migration():
    script = ScriptDirectory.from_config(_config(Path("unused.db")))
    rev = script.get_revision("0006_visual_reviews")
    assert rev.down_revision == "0005_library_items"
    assert script.get_revision("0005_library_items") is not None


def test_0006_creates_the_table_and_matches_the_model(app_env, tmp_path):
    db = tmp_path / "review.db"
    cfg = _config(db)
    command.upgrade(cfg, "0006_visual_reviews")
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        insp = inspect(engine)
        columns = {c["name"]: c for c in insp.get_columns("visual_reviews")}
        assert set(columns) == {"id", "version_id", "scene_id", "state", "fingerprint", "note", "updated_by",
                                "updated_at"}
        assert columns["note"]["nullable"] and not columns["fingerprint"]["nullable"]
        fks = {fk["constrained_columns"][0]: fk for fk in insp.get_foreign_keys("visual_reviews")}
        assert fks["version_id"]["referred_table"] == "project_versions"
        assert fks["version_id"]["options"].get("ondelete") == "CASCADE"
        assert fks["updated_by"]["options"].get("ondelete") == "SET NULL"
        uniques = {u["name"]: u["column_names"] for u in insp.get_unique_constraints("visual_reviews")}
        assert uniques["uq_visual_reviews_version_scene"] == ["version_id", "scene_id"]
    finally:
        engine.dispose()
    command.check(cfg)  # no drift between the models and the migrated schema


def test_0006_cascades_and_downgrades(app_env, tmp_path):
    db = tmp_path / "cascade.db"
    cfg = _config(db)
    command.upgrade(cfg, "0006_visual_reviews")
    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("INSERT INTO users (id, username, password_hash, role, is_active, must_change_password, "
                    "token_version, created_at, updated_at) VALUES (1, 'u', 'x', 'editor', 1, 0, 0, '2026-10-08', "
                    "'2026-10-08')")
        con.execute("INSERT INTO projects (id, owner_id, title, subject_name, unit_name, session_number, "
                    "session_title, language, settings, next_version_number, created_at, updated_at) VALUES "
                    "(1, 1, 't', '', '', '', '', 'en-IN', '{}', 2, '2026-10-08', '2026-10-08')")
        con.execute("INSERT INTO project_versions (id, project_id, number, label, status, language, issues, "
                    "generation_meta, issue_counts, has_timeline, revision, created_at, updated_at) VALUES "
                    "(1, 1, 1, '', 'ready', 'en-IN', '[]', '{}', '{}', 0, 1, '2026-10-08', '2026-10-08')")
        con.execute("INSERT INTO users (id, username, password_hash, role, is_active, must_change_password, "
                    "token_version, created_at, updated_at) VALUES (2, 'reviewer', 'x', 'admin', 1, 0, 0, "
                    "'2026-10-08', '2026-10-08')")
        con.execute("INSERT INTO visual_reviews (version_id, scene_id, state, fingerprint, updated_by, updated_at) "
                    "VALUES (1, 's1', 'approved', 'f', 2, '2026-10-08')")
        con.execute("DELETE FROM users WHERE id = 2")  # the reviewer goes: the sign-off stays
        assert con.execute("SELECT state, updated_by FROM visual_reviews").fetchall() == [("approved", None)]
        con.execute("DELETE FROM project_versions WHERE id = 1")  # the version goes: its sign-offs too
        assert con.execute("SELECT count(*) FROM visual_reviews").fetchone() == (0,)
        con.commit()
    finally:
        con.close()
    command.downgrade(cfg, "0005_library_items")
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT name FROM sqlite_master WHERE name='visual_reviews'").fetchone() is None
        assert con.execute("SELECT name FROM sqlite_master WHERE name='library_items'").fetchone() is not None
    finally:
        con.close()
    command.upgrade(cfg, "head")


def test_0006_is_idempotent_after_create_all(app_env, tmp_path):
    """A development database at 0005 whose startup ``create_all`` already made the table."""
    from aadhi.db import Base

    db = tmp_path / "dev.db"
    cfg = _config(db)
    command.upgrade(cfg, "0005_library_items")
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    try:
        Base.metadata.tables["visual_reviews"].create(engine)
    finally:
        engine.dispose()
    command.upgrade(cfg, "0006_visual_reviews")
    command.check(cfg)
