"""Lessons saved by the friend's fork of v1 (origin/andryan): converted like v1 lessons, hidden scenes kept hidden,
per-scene durations kept, everything else reported (``aadhi.legacy.convert``, ``legacy_db``)."""

from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import select

from aadhi.legacy import convert_legacy, is_legacy
from aadhi.legacy.legacy_db import import_legacy_db
from aadhi.models import Project, ProjectVersion

FIXTURES = Path(__file__).parent / "fixtures"
FORK_SCENE_KEYS = ("scene_id", "edit", "visual_direction", "source", "presenter_plan", "mascot_state", "cinematic_plan",
                   "visual_review", "video_asset_id", "uploaded_image_assets")


@pytest.fixture()
def fork_lesson() -> dict:
    return json.loads((FIXTURES / "andryan_lesson.json").read_text(encoding="utf-8"))


def strip_fork_keys(lesson: dict) -> dict:
    """The same lesson as plain v1 saved it (no fork additions)."""
    plain = copy.deepcopy(lesson)
    for key in ("source_document", "cinematic_style", "editor", "studio"):
        plain.pop(key, None)
    for scene in plain["scenes"]:
        for key in FORK_SCENE_KEYS:
            scene.pop(key, None)
        if isinstance(scene.get("side_panel"), dict):
            scene["side_panel"].pop("video_asset_id", None)
        if isinstance(scene.get("html"), str):
            scene["html"] = scene["html"].replace('<img src="asset:0123456789abcdef0123456789abcdef">', "")
    return plain


def test_fork_lessons_are_v1_lessons(fork_lesson):
    assert is_legacy(fork_lesson)
    assert is_legacy(json.loads((FIXTURES / "andryan_export_lesson.json").read_text(encoding="utf-8")))


def test_hidden_scenes_stay_hidden_and_durations_are_kept(fork_lesson):
    sp, warnings = convert_legacy(fork_lesson)
    assert [s.id for s in sp.scenes] == ["s1", "s2", "s3", "s4", "s5"]  # positional ids, as for v1
    assert [s.hidden for s in sp.scenes] == [False, False, True, False, False]
    assert sp.scene_by_id("s1").min_seconds == 6.0
    assert sp.scene_by_id("s4").min_seconds == 1.0  # 0.5 s in the source app: v2's minimum is 1 s
    assert sp.scene_by_id("s3").title == "WORKED STEP"  # kept in full, only skipped by the timeline
    text = "\n".join(warnings)
    assert "scene s3: hidden in the source app; imported as a hidden scene" in warnings
    assert "scene s4: minimum duration 0.5 s set to 1 s" in text
    assert "scene s4: narration was muted in the source app" in text
    assert "scene s4: captions were turned off for this scene in the source app" in text
    assert "scene s2 (content): media made in the source app was not imported; it will be made again" in warnings
    assert "scene s5 (content): media made in the source app was not imported; it will be made again" in warnings
    assert "scene s5 (content): 1 uploaded board image(s) must be re-uploaded as figures in v2" in warnings
    assert "scene s2 (content): inline image dropped (not supported on the v2 board)" in warnings
    assert ("the source app's Studio records, editor settings, video style, prepared-source link were not imported"
            in warnings)
    assert "4 scene(s) carried presenter, style or review data from the source app" in text
    assert "1 scene(s) kept the source app's record of the generated text" in text
    dumped = sp.model_dump(mode="json")
    assert "edit" not in json.dumps(dumped["scenes"][0]) and "scene_id" not in json.dumps(dumped)


def test_fork_additions_change_nothing_else(fork_lesson):
    """Apart from hidden / min_seconds, a fork lesson converts exactly as the same lesson saved by v1."""
    fork_sp, fork_warnings = convert_legacy(fork_lesson)
    plain_sp, plain_warnings = convert_legacy(strip_fork_keys(fork_lesson))
    fork_doc = fork_sp.model_dump(mode="json")
    for scene in fork_doc["scenes"]:
        scene.pop("hidden", None)
        scene.pop("min_seconds", None)
    assert fork_doc == plain_sp.model_dump(mode="json")
    assert not any("source app" in w for w in plain_warnings)
    assert set(plain_warnings) <= set(fork_warnings)


def test_plain_v1_lessons_get_no_fork_warnings():
    lesson = json.loads((FIXTURES / "andryan_export_lesson.json").read_text(encoding="utf-8"))
    sp, warnings = convert_legacy(lesson)
    assert len(sp.scenes) == 6 and not any(s.hidden or s.min_seconds for s in sp.scenes)
    assert not any("source app" in w for w in warnings)
    assert any("existing v1 video url was not imported" in w for w in warnings)


@pytest.mark.parametrize(("value", "expected"), [(6, 6.0), (600, 600.0), (601, 600.0), (0.2, 1.0), (12.345, 12.35),
                                               (10**400, 600.0), (1e300, 600.0)])
def test_minimum_durations_are_clamped(value, expected):
    lesson = {"scenes": [{"type": "content", "title": "T", "html": "<p>x</p>", "narration": "Hello there.",
                          "edit": {"min_seconds": value}}]}
    sp, _ = convert_legacy(lesson)
    assert sp.scenes[0].min_seconds == expected


@pytest.mark.parametrize("value", [0, -3, "6", True, None, float("nan")])
def test_unusable_minimum_durations_are_ignored(value):
    lesson = {"scenes": [{"type": "content", "title": "T", "html": "<p>x</p>", "narration": "Hello there.",
                          "edit": {"min_seconds": value}}]}
    sp, warnings = convert_legacy(lesson)
    assert sp.scenes[0].min_seconds is None
    if value is not None:
        assert any("minimum duration from the source app is not a usable number" in w for w in warnings)


def test_a_hidden_scene_split_in_parts_hides_every_part():
    items = "".join(f"<p>Point {i}</p>" for i in range(20))
    lesson = {"scenes": [{"type": "content", "title": "Long", "html": items, "narration": "Many points.",
                          "edit": {"hidden": True, "min_seconds": 30}},
                         {"type": "content", "title": "Next", "html": "<p>x</p>", "narration": "Next one."}]}
    sp, warnings = convert_legacy(lesson)
    assert [(s.id, s.hidden, s.min_seconds) for s in sp.scenes] == [
        ("s1", True, None), ("s1-2", True, 30.0), ("s2", False, None)]
    assert "scene s1: minimum duration applied to the last of its 2 parts" in warnings


def test_edit_data_of_unexpected_shapes_is_ignored():
    for edit in ("hidden", ["hidden"], {"hidden": "yes"}, {"hidden": 1}, {}):
        lesson = {"scenes": [{"type": "content", "title": "T", "html": "<p>x</p>", "narration": "Hello there.",
                              "edit": edit}]}
        sp, warnings = convert_legacy(lesson)
        assert sp.scenes[0].hidden is False and not any("hidden" in w for w in warnings), edit


def test_fork_projects_db_imports_with_hidden_scenes(app_env, db_session, tmp_path, fork_lesson):
    """Their database is v1's users/projects plus their own tables, which the importer ignores."""
    path = tmp_path / "fork" / "projects.db"
    path.parent.mkdir()
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(50) NOT NULL UNIQUE,
                            password_hash VARCHAR(255) NOT NULL);
        CREATE TABLE projects (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject_name VARCHAR(255) NOT NULL,
                               unit_name VARCHAR(255), session_number VARCHAR(50), session_title VARCHAR(255),
                               json_data TEXT NOT NULL, created_at DATETIME, updated_at DATETIME);
        CREATE TABLE assets (id VARCHAR(32) PRIMARY KEY, scope_key VARCHAR(64), storage_volume VARCHAR(16),
                             sha256 VARCHAR(64), details TEXT);
        CREATE TABLE ai_generation_runs (id VARCHAR(32) PRIMARY KEY, project_id INTEGER, status VARCHAR(16));
        CREATE TABLE video_exports (id VARCHAR(32) PRIMARY KEY, project_id INTEGER, status VARCHAR(16));
    """)
    con.execute("INSERT INTO users (id, username, password_hash) VALUES (2, 'teacher1', ?)",
                ("$2b$04$" + "a" * 53,))
    con.execute("INSERT INTO projects (id, user_id, subject_name, unit_name, session_number, session_title, json_data, "
                "created_at) VALUES (1, 2, 'Mascot Reliability Check', 'Unit 1', 'Session 1', 'Stress in One Minute', ?,"
                " '2026-09-30 10:00:00')", (json.dumps(fork_lesson),))
    con.execute("INSERT INTO assets (id, scope_key) VALUES ('0123456789abcdef0123456789abcdef', 'user:2')")
    con.commit()
    con.close()
    result = import_legacy_db(db_session, path, app_env)
    assert result["projects_imported"] == 1 and not result["errors"]
    project = db_session.execute(select(Project)).scalar_one()
    version = db_session.execute(select(ProjectVersion).where(ProjectVersion.project_id == project.id)).scalar_one()
    sp = version.get_screenplay()
    assert [s.hidden for s in sp.scenes] == [False, False, True, False, False]
    assert any(i["message"] == "scene s3: hidden in the source app; imported as a hidden scene" and i["scene_id"] == "s3"
               for i in version.issues)


def _fork_db(path: Path, lesson: dict, rows: list[tuple]) -> Path:
    """A fork projects.db whose ``projects`` rows are ``(id, user_id, subject, unit, session, title)``: every save of
    a lesson is a new row there."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(50) NOT NULL UNIQUE,
                            password_hash VARCHAR(255) NOT NULL);
        CREATE TABLE projects (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject_name VARCHAR(255) NOT NULL,
                               unit_name VARCHAR(255), session_number VARCHAR(50), session_title VARCHAR(255),
                               json_data TEXT NOT NULL, created_at DATETIME, updated_at DATETIME);
    """)
    con.executemany("INSERT INTO users (id, username, password_hash) VALUES (?, ?, ?)",
                    [(2, "teacher1", "$2b$04$" + "a" * 53), (3, "teacher2", "$2b$04$" + "b" * 53)])
    con.executemany("INSERT INTO projects (id, user_id, subject_name, unit_name, session_number, session_title, "
                    "json_data) VALUES (?, ?, ?, ?, ?, ?, ?)", [(*r, json.dumps(lesson)) for r in rows])
    con.commit()
    con.close()
    return path


def test_latest_only_imports_the_newest_save_of_each_lesson(app_env, db_session, tmp_path, fork_lesson):
    rows = [
        (1, 2, "Physics", "Unit 1", "Session 1", "Stress"),
        (2, 2, "Physics", "Unit 1", "Session 1", "Stress"),  # a later save of lesson 1
        (3, 3, "Physics", "Unit 1", "Session 1", "Stress"),  # same names, another teacher: its own lesson
        (4, 2, " physics", "UNIT 1 ", "session 1", "stress"),  # the newest save of lesson 1 (case and spaces)
        (5, 2, "", "", "", ""),  # no names at all: always its own lesson
        (6, 2, "", None, None, None),
    ]
    path = _fork_db(tmp_path / "fork" / "projects.db", fork_lesson, rows)
    result = import_legacy_db(db_session, path, app_env, latest_only=True)
    assert result["projects_superseded"] == [1, 2] and not result["errors"]
    imported = sorted(p.settings["legacy_project_id"] for p in db_session.execute(select(Project)).scalars())
    assert imported == [3, 4, 5, 6] and result["projects_imported"] == 4


def test_without_latest_only_every_row_is_imported_as_before(app_env, db_session, tmp_path, fork_lesson):
    rows = [(1, 2, "Physics", "Unit 1", "Session 1", "Stress"), (2, 2, "Physics", "Unit 1", "Session 1", "Stress")]
    path = _fork_db(tmp_path / "fork" / "projects.db", fork_lesson, rows)
    result = import_legacy_db(db_session, path, app_env)
    assert result["projects_imported"] == 2 and "projects_superseded" not in result


def test_import_legacy_cli_passes_latest_only(monkeypatch, tmp_path):
    import contextlib

    from aadhi import cli

    seen: list[bool] = []

    def fake_import(db, path, settings, *, owner_fallback, latest_only):
        seen.append(latest_only)
        return {"project_ids": [1], "errors": [], "action_required": []}

    monkeypatch.setattr("aadhi.legacy.legacy_db.import_legacy_db", fake_import)
    monkeypatch.setattr(cli, "_session", lambda: contextlib.nullcontext(None))  # no database is opened
    db_file = tmp_path / "projects.db"
    db_file.write_bytes(b"")
    for argv, expected in ((["--latest-only"], True), ([], False)):
        args = cli.build_parser().parse_args(["import-legacy", "--db", str(db_file), *argv])
        assert args.latest_only is expected
        assert cli.cmd_import_legacy(args, None) == 0
    assert seen == [True, False]
