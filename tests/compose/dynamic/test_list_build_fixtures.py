"""The list_build fixtures are what ``tests/compose/dynamic/list_build_fixtures.py`` builds (byte for byte), cover a
dark and a light family in each format, and fit the slow 1x / 3x / reverse gate's 300 frames closely enough that the
gate sees every scene (``tests/determinism/test_template_fixtures.py``). Their validity as template fixtures is the
shared validator's (``test_template_fixtures.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aadhi.compose.motion.chunks import scene_frame_bounds
from tests.compose.dynamic import list_build_fixtures as lbf
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, fixture_name

GATE_FRAMES = 300  # tests/determinism/test_template_fixtures.py FRAMES


def test_the_committed_fixtures_are_current() -> None:
    assert lbf.stale() == [], "run: python -m tests.compose.dynamic.list_build_fixtures"


def test_stale_finds_a_changed_or_missing_file(tmp_path: Path) -> None:
    for name, tl in lbf.fixtures().items():
        (tmp_path / name).write_text(lbf.render(tl), "utf-8", newline="\n")
    assert lbf.stale(tmp_path) == []
    first = sorted(lbf.fixtures())[0]
    data = json.loads((tmp_path / first).read_text("utf-8"))
    data["scenes"][0]["title"] = "edited"
    (tmp_path / first).write_text(json.dumps(data), "utf-8")
    (tmp_path / sorted(lbf.fixtures())[1]).unlink()
    assert lbf.stale(tmp_path) == sorted(lbf.fixtures())[:2]


@pytest.mark.parametrize("name", sorted(lbf.fixtures()))
def test_each_fixture_is_seen_by_the_gate(name: str) -> None:
    tl = lbf.fixtures()[name]
    bounds = scene_frame_bounds(tl)
    assert fixture_name(FIXTURE_DIR / name)[0] == "list_build"
    # every scene starts inside the gate's frames, and at most a few frames of the last one fall outside them
    assert all(f0 < GATE_FRAMES for f0, _ in bounds), bounds
    assert bounds[-1][1] <= GATE_FRAMES + 15, bounds
    assert all(s.look is not None and s.look.template == "list_build" for s in tl.scenes)


def test_both_families_in_both_formats() -> None:
    modes: dict[str, set[str]] = {}
    for tl in lbf.fixtures().values():
        assert tl.presentation is not None
        modes.setdefault(tl.presentation.format, set()).add(str(tl.presentation.mode))
    assert modes == {"landscape": {"dark", "light"}, "portrait": {"dark", "light"}}
