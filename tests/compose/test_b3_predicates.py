"""B3's shared Dynamic predicate and its two legacy markers (B3 plan §1.0, §A B3-0 task 2).

* ``is_dynamic(presentation, settings)`` is exactly the expression ``compose/timeline.py`` used before B3
  (``presentation is not None and dynamic_enabled(settings)``), including the short circuit: without a presentation
  the flag is not read at all (no database read on a classic build). ``timeline.py`` calls it.
* ``b3_voiced`` / ``b3_music`` tell a B3 lecture from a stored B2-era one: B2's ``default_presentation`` writes a cast
  with no calibration and the placeholder ``MusicChoice(source="off")`` (mode ``bed``), and both markers say "legacy"
  for exactly that shape.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import pytest

from aadhi import runtime_flags
from aadhi.compose import presentation as pr
from aadhi.compose import timeline as tl
from aadhi.config import Settings
from aadhi.schemas.screenplay import MusicChoice, Presentation, Screenplay, VoiceCalibration
from tests.compose.factories import make_manifest, screenplay_dict
from tests.schemas.v3_fixtures import PRESENTATION


def _presentation(**over: Any) -> Presentation:
    data = {**PRESENTATION, "format": "landscape", "hook": None, "theme_family": "midnight_indigo",
            "music": {"source": "procedural", "provider": "procedural"}}
    data.update(over)
    return Presentation.model_validate(data)


def _b2_default() -> Presentation:
    """What a B2-era generation stored: ``authoring.default_presentation`` (bare narrator, placeholder music)."""
    from aadhi.compose.dynamic.authoring import default_presentation

    return default_presentation(subject_name="Physics", unit_name="Waves", session_number="1",
                                session_title="Wave speed", tts_provider="edge", language="en-IN")


@pytest.mark.parametrize(("has_presentation", "env", "override"),
                         list(itertools.product((False, True), (False, True), (None, False, True))))
def test_is_dynamic_is_the_timeline_expression_exactly(monkeypatch, has_presentation: bool, env: bool,
                                                      override: bool | None) -> None:
    """Every combination of presentation, environment and the administrator's switch gives the pre-B3 answer."""
    reads: list[str] = []

    def fake_get(name: str) -> Any:
        reads.append(name)
        return override

    monkeypatch.setattr(runtime_flags, "get", fake_get)
    settings = Settings(_env_file=None, dynamic_enabled=env)
    presentation = _presentation() if has_presentation else None
    expected = presentation is not None and (env if override is None else override)
    assert pr.is_dynamic(presentation, settings) is expected
    # the short circuit of the old expression: no presentation, no flag read
    assert reads == (["dynamic_enabled"] if has_presentation else [])


def test_is_dynamic_reads_the_stored_runtime_flag(app_env, db_session) -> None:
    """The administrator's switch in the database wins over the environment, as ``dynamic_enabled`` merges them."""
    on = app_env.model_copy(update={"dynamic_enabled": True})
    presentation = _presentation()
    runtime_flags.invalidate()
    try:
        assert pr.is_dynamic(presentation, on) is True
        runtime_flags.set_flag(db_session, "dynamic_enabled", False)
        db_session.commit()
        assert pr.is_dynamic(presentation, on) is False
        assert pr.is_dynamic(None, on) is False
    finally:
        runtime_flags.invalidate()


def test_the_timeline_build_asks_is_dynamic(monkeypatch) -> None:
    """``compose/timeline.py`` decides Dynamic through the shared predicate (and nothing else names the old one)."""
    calls: list[tuple[Any, Any]] = []
    real = tl.is_dynamic

    def spy(presentation: Any, settings: Any) -> bool:
        calls.append((presentation, settings))
        return real(presentation, settings)

    monkeypatch.setattr(tl, "is_dynamic", spy)
    data = screenplay_dict()
    data["presentation"] = _presentation().model_dump(mode="json")
    sp = Screenplay.model_validate(data)
    settings = Settings(_env_file=None, dynamic_enabled=True)
    assert tl.build_timeline(sp, make_manifest(sp), settings=settings).presentation is not None
    assert calls and calls[0][0] is sp.presentation and calls[0][1] is settings
    source = Path(tl.__file__).read_text(encoding="utf-8")
    assert "dynamic_enabled(" not in source and "is_dynamic(screenplay.presentation, settings)" in source


def test_a_b2_era_presentation_is_legacy_for_both_markers() -> None:
    p = _b2_default()
    assert ":" not in p.voices.narrator and p.voices.calibration == {}  # the bare id B2 stored
    assert p.music == MusicChoice(source="off") and p.music.mode == "bed"
    assert pr.b3_voiced(p) is False
    assert pr.b3_music(p) is False


def test_b3_voiced_is_a_cast_with_calibration() -> None:
    p = _b2_default()
    cast = p.voices.model_copy(update={"calibration": {
        p.voices.narrator: VoiceCalibration(natural_wpm=160.0, control="+0%")}})
    assert pr.b3_voiced(p.model_copy(update={"voices": cast})) is True


@pytest.mark.parametrize(("music", "b3"), [
    ({"source": "off"}, False),  # B2's placeholder: mode defaults to "bed"
    ({"source": "off", "mode": "bed"}, False),
    ({"source": "off", "mode": "off"}, True),  # a deliberate off (what B3 writes)
    ({"source": "off", "mode": "gaps_bookends"}, True),
    ({"source": "procedural", "provider": "procedural"}, True),
    ({"source": "procedural", "provider": "procedural", "mode": "off"}, True),
    ({"source": "upload", "asset_key": "music-abc", "mode": "bed", "level": "low"}, True),
])
def test_b3_music_is_anything_but_the_placeholder(music: dict[str, Any], b3: bool) -> None:
    assert pr.b3_music(_presentation(music=music)) is b3


def test_the_markers_are_pure(monkeypatch) -> None:
    """They read the presentation only: never the Dynamic switch (a marker is not a predicate; ``is_dynamic`` is)."""

    def no_flag(name: str) -> Any:
        raise AssertionError(f"a marker read the runtime flag {name}")

    monkeypatch.setattr(runtime_flags, "get", no_flag)
    p = _presentation()  # the v3 maximal cast carries calibration
    assert p.voices.calibration and pr.b3_voiced(p) is True and pr.b3_music(p) is True
