"""B3's own fixtures (``tests/fixtures/b3``, B3 plan §A B3-0 task 5): what each one must be, and the fixed-manifest
timeline gate (§3.2): today's code builds, from each fixture's stored screenplay and manifest, the stored timeline,
every field equal except the two B3 adds (``music``, ``sfx``). ``python -m tests.fixtures.b3.build --check`` is the same
check from the command line. The ``b2_era`` fixture is the legacy gate's (``tests/invariance/test_b3_legacy.py``).

The builder and its check depend on no database: an administrator's runtime switch stored in the database the shell
points at changes neither (and no database file is created)."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from aadhi import runtime_flags
from aadhi.compose.dynamic import templates as catalogue
from aadhi.compose.dynamic.templates import TEMPLATES
from aadhi.config import ROOT_DIR
from aadhi.schemas.timeline import Timeline
from tests.fixtures.b3 import build as b3


def templates(tl: Timeline) -> list[str]:
    return [s.look.template for s in tl.scenes if s.look is not None]


def undrawn(tl: Timeline) -> list[str]:
    """The implemented templates (the catalogue's, read when called) that ``tl`` does not draw."""
    return sorted(set(catalogue.IMPLEMENTED) - set(templates(tl)))


def dense_share(tl: Timeline) -> float:
    dense = sum(s.duration for s in tl.scenes if s.look is not None and TEMPLATES[s.look.template].dense)
    return dense / tl.total_duration


@pytest.mark.parametrize("name", b3.NAMES)
def test_the_stored_timeline_is_what_the_fixed_manifest_builds(name: str) -> None:
    assert b3.stale(name) == [], f"regenerate with python -m tests.fixtures.b3.build {name} (and say why)"


@pytest.mark.parametrize("name", b3.NAMES)
def test_every_fixture_is_a_dynamic_lecture_with_the_b1_mix(name: str) -> None:
    from aadhi.compose.presentation import b3_music, b3_voiced

    tl = b3.load_timeline(name)
    sp = b3.load_screenplay(name)
    assert tl.presentation is not None and tl.fps == 30
    assert tl.music is None and tl.sfx == []  # B2's placeholder music: no MusicPlan until B3 casts these lectures
    assert sp.presentation is not None and not b3_voiced(sp.presentation) and not b3_music(sp.presentation)
    manifest = b3.load_manifest(name)
    shown = {s.id for s in sp.scenes if not s.hidden}
    assert set(manifest.scene_hashes) == shown
    assert all(a.asset_key for sid, a in manifest.audio.items() if sid in shown)


def test_lesson_16x9_is_a_kitchen_sink_of_todays_templates() -> None:
    """Every template the catalogue implements is drawn: when B2 implements another one, this fails until a scene that
    draws it joins ``lesson_screenplay()`` (and the fixture is regenerated)."""
    tl = b3.load_timeline("lesson_16x9")
    assert (tl.width, tl.height) == (1920, 1080) and tl.presentation is not None
    assert tl.presentation.format == "landscape"
    assert set(catalogue.IMPLEMENTED) == {k for k, v in TEMPLATES.items() if v.implemented}
    assert undrawn(tl) == [], "add a scene that draws each of these to lesson_screenplay() and regenerate"
    assert templates(tl).count("chapter_bumper") >= 2
    quiz = [s for s in tl.scenes if s.quiz is not None]
    assert len(quiz) == 1
    q = quiz[0].quiz
    assert q is not None and q.countdown_seconds == 5 and q.reveal_start > q.countdown_start
    assert [b.phase for b in quiz[0].beats] == ["main", "reveal"]
    assert len(tl.chapters) >= 2


def test_a_newly_implemented_template_is_missed_by_the_kitchen_sink(monkeypatch) -> None:
    """The kitchen-sink check follows the catalogue, not a list kept beside it: a template B2 implements later is
    reported until the lesson draws it."""
    tl = b3.load_timeline("lesson_16x9")
    later = next(k for k, v in TEMPLATES.items() if not v.implemented)
    monkeypatch.setattr(catalogue, "IMPLEMENTED", (*catalogue.IMPLEMENTED, later))
    assert undrawn(tl) == [later]


def test_reel_9x16_is_a_learning_reel() -> None:
    tl = b3.load_timeline("reel_9x16")
    sp = b3.load_screenplay("reel_9x16")
    assert (tl.width, tl.height) == (1080, 1920)
    assert sp.presentation is not None and sp.presentation.format == "portrait"
    assert sp.presentation.reel_profile == "learning"
    assert any(s.quiz is not None for s in tl.scenes) and "check" in templates(tl)


def test_dense_16x9_is_at_least_30_percent_dense() -> None:
    tl = b3.load_timeline("dense_16x9")
    assert tl.presentation is not None and tl.presentation.format == "landscape"
    assert dense_share(tl) >= b3.DENSE_MIN_SHARE
    assert {"formula_focus", "worked_steps", "code_walkthrough"} <= set(templates(tl))
    assert dense_share(b3.load_timeline("lesson_16x9")) < b3.DENSE_MIN_SHARE  # the lesson is the bed case


def test_long_16x9_lasts_an_hour() -> None:
    tl = b3.load_timeline("long_16x9")  # rebuilt and checked against its stored digest
    assert tl.total_duration >= b3.LONG_MIN_SECONDS
    assert tl.presentation is not None and tl.presentation.format == "landscape"
    assert len(tl.scenes) <= 201 and sum(1 for s in tl.scenes if s.audio_asset_key) > 150
    assert not (b3.FIXTURE_DIR / "long_16x9" / "timeline.json").exists()  # metadata only: no hour-long file


def test_the_fixtures_are_written_as_the_builder_writes_them() -> None:
    """The stored screenplays are what the builder makes (so a regeneration changes only what B2 changed)."""
    for name in ("lesson_16x9", "reel_9x16", "dense_16x9"):
        docs = b3.documents(name)
        assert docs["screenplay"] == b3.load(name, "screenplay"), name
        assert docs["manifest"] == b3.load(name, "manifest"), name


def test_the_administrators_switch_changes_neither_the_check_nor_the_builder(app_env, db_session) -> None:
    """Dynamic switched off in the database (``app_settings``): the fixtures are still Dynamic lectures, the check
    still passes and the builder still writes the stored timelines (not classic ones)."""
    runtime_flags.invalidate()
    try:
        runtime_flags.set_flag(db_session, "dynamic_enabled", False)
        db_session.commit()
        assert runtime_flags.get("dynamic_enabled") is False  # the switch is stored, and read outside the builder
        for name in ("reel_9x16", b3.LEGACY):
            assert b3.stale(name) == [], name
        assert b3.main(["--check", "reel_9x16"]) == 0
        docs = b3.documents("reel_9x16")
        assert docs["timeline"] == b3.load("reel_9x16", "timeline") and docs["timeline"]["presentation"] is not None
        assert runtime_flags.get("dynamic_enabled") is False  # the builder put the reader back
    finally:
        runtime_flags.invalidate()


def test_the_check_opens_no_database(tmp_path) -> None:
    """``python -m tests.fixtures.b3.build --check`` in a shell whose ``DATABASE_URL`` names a database that does not
    exist: it passes, and the database is never created (nor anything under ``DATA_DIR``)."""
    db_file = tmp_path / "ambient.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file.as_posix()}", "DATA_DIR": str(tmp_path / "data"),
           "DYNAMIC_ENABLED": "false"}
    out = subprocess.run([sys.executable, "-m", "tests.fixtures.b3.build", "--check", "reel_9x16"], cwd=ROOT_DIR,
                         env=env, capture_output=True, text=True, timeout=300, check=False)
    assert out.returncode == 0, out.stderr[-4000:]
    assert not db_file.exists() and not (tmp_path / "data").exists()
