"""The chapter_bumper fixtures are what ``tests/compose/dynamic/make_chapter_bumper_fixtures.py`` builds (byte for
byte), cover a dark and a light family in each format, keep §4.3's bumper lengths and fit the slow 1x / 3x / reverse
gate's 300 frames, so the gate sees every scene and every crossfade
(``tests/determinism/test_template_fixtures.py``). Their validity as template fixtures is the shared validator's
(``test_template_fixtures.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aadhi.compose.dynamic import templates as tp
from aadhi.compose.motion.chunks import scene_frame_bounds
from tests.compose.dynamic import make_chapter_bumper_fixtures as cbf
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, fixture_name

GATE_FRAMES = 300  # tests/determinism/test_template_fixtures.py FRAMES


def test_the_committed_fixtures_are_current() -> None:
    assert cbf.stale() == [], "run: python -m tests.compose.dynamic.make_chapter_bumper_fixtures"


def test_stale_finds_a_changed_or_missing_file(tmp_path: Path) -> None:
    for name, tl in cbf.fixtures().items():
        (tmp_path / name).write_text(cbf.render(tl), "utf-8", newline="\n")
    assert cbf.stale(tmp_path) == []
    first = sorted(cbf.fixtures())[0]
    data = json.loads((tmp_path / first).read_text("utf-8"))
    data["scenes"][0]["title"] = "edited"
    (tmp_path / first).write_text(json.dumps(data), "utf-8")
    (tmp_path / sorted(cbf.fixtures())[1]).unlink()
    assert cbf.stale(tmp_path) == sorted(cbf.fixtures())[:2]


@pytest.mark.parametrize("name", sorted(cbf.fixtures()))
def test_each_fixture_is_seen_by_the_gate(name: str) -> None:
    tl = cbf.fixtures()[name]
    bounds = scene_frame_bounds(tl)
    assert fixture_name(FIXTURE_DIR / name)[0] == "chapter_bumper"
    assert all(f0 < GATE_FRAMES for f0, _ in bounds), bounds
    assert bounds[-1][1] <= GATE_FRAMES + 15, bounds
    assert all(s.look is not None and s.look.template == "chapter_bumper" for s in tl.scenes)


def test_both_families_in_both_formats() -> None:
    modes: dict[str, set[str]] = {}
    for tl in cbf.fixtures().values():
        assert tl.presentation is not None
        modes.setdefault(tl.presentation.format, set()).add(str(tl.presentation.mode))
    assert modes == {"landscape": {"dark", "light"}, "portrait": {"dark", "light"}}


def test_every_scene_keeps_the_template_s_timing_and_its_colour_block() -> None:
    """§4.3: 2-3 s in 16:9, 1.2-1.8 s in 9:16; a chapter card is drawn on the colour block and entered by a
    crossfade (R-54; the first scene of a file has nothing to cross-fade from)."""
    spec = tp.TEMPLATES["chapter_bumper"]
    for name, tl in cbf.fixtures().items():
        assert tl.presentation is not None
        fmt = tl.presentation.format
        duration = spec.duration[fmt]
        for i, scene in enumerate(tl.scenes):
            assert scene.look is not None
            assert duration.min - 1e-9 <= scene.duration <= (duration.max or 1e9) + 1e-9, (name, scene.scene_id)
            assert scene.look.background["preset"] == "colour_block", (name, scene.scene_id)
            assert scene.look.transition_in["kind"] == ("cut" if i == 0 else "crossfade"), (name, scene.scene_id)
            assert scene.type == "chapter_card" and not scene.board and not scene.beats


def test_the_roadmaps_walk_the_lesson_and_one_scene_has_neither_roadmap_nor_number() -> None:
    """The cases the template's own tests draw: a mini-roadmap whose current chapter moves, and a label with no
    digits on a look with no roadmap (no numeral at all)."""
    seen_states: set[str] = set()
    without = 0
    for tl in cbf.fixtures().values():
        for scene in tl.scenes:
            assert scene.look is not None
            refs = scene.look.refs
            road = list(refs.roadmap) if refs is not None else []
            if not road:
                without += 1
                assert scene.chapter_label is not None and not any(c.isdigit() for c in scene.chapter_label)
                continue
            seen_states |= {e.state for e in road}
            current = [i for i, e in enumerate(road) if e.state == "current"]
            assert len(current) == 1, scene.scene_id
            assert all(e.state == "done" for e in road[: current[0]])
            assert all(e.state == "upcoming" for e in road[current[0] + 1 :])
    assert seen_states == {"done", "current", "upcoming"}
    assert without == 1
