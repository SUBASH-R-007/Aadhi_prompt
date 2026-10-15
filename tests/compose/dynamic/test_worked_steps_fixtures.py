"""The worked_steps fixtures are what ``tests/compose/dynamic/worked_steps_fixtures.py`` builds (byte for byte), cover
a dark and a light family in each format, and fit the slow 1x / 3x / reverse gate's 300 frames closely enough that the
gate sees every scene (``tests/determinism/test_template_fixtures.py``). Their validity as template fixtures is the
shared validator's (``test_template_fixtures.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aadhi.compose.motion.chunks import scene_frame_bounds
from tests.compose.dynamic import worked_steps_fixtures as wsf
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, fixture_name

GATE_FRAMES = 300  # tests/determinism/test_template_fixtures.py FRAMES


def test_the_committed_fixtures_are_current() -> None:
    assert wsf.stale() == [], "run: python -m tests.compose.dynamic.worked_steps_fixtures"


def test_stale_finds_a_changed_or_missing_file(tmp_path: Path) -> None:
    for name, tl in wsf.fixtures().items():
        (tmp_path / name).write_text(wsf.render(tl), "utf-8", newline="\n")
    assert wsf.stale(tmp_path) == []
    first = sorted(wsf.fixtures())[0]
    data = json.loads((tmp_path / first).read_text("utf-8"))
    data["scenes"][0]["title"] = "edited"
    (tmp_path / first).write_text(json.dumps(data), "utf-8")
    (tmp_path / sorted(wsf.fixtures())[1]).unlink()
    assert wsf.stale(tmp_path) == sorted(wsf.fixtures())[:2]


@pytest.mark.parametrize("name", sorted(wsf.fixtures()))
def test_each_fixture_is_seen_by_the_gate(name: str) -> None:
    tl = wsf.fixtures()[name]
    bounds = scene_frame_bounds(tl)
    assert fixture_name(FIXTURE_DIR / name)[0] == "worked_steps"
    # every scene starts inside the gate's frames, and at most a few frames of the last one fall outside them
    assert all(f0 < GATE_FRAMES for f0, _ in bounds), bounds
    assert bounds[-1][1] <= GATE_FRAMES + 15, bounds
    assert all(s.look is not None and s.look.template == "worked_steps" for s in tl.scenes)


def test_both_families_in_both_formats() -> None:
    modes: dict[str, set[str]] = {}
    for tl in wsf.fixtures().values():
        assert tl.presentation is not None
        modes.setdefault(tl.presentation.format, set()).add(str(tl.presentation.mode))
    assert modes == {"landscape": {"dark", "light"}, "portrait": {"dark", "light"}}


def test_every_scene_is_dense_and_carries_the_events_the_template_draws() -> None:
    """A dense template's background is frozen from the scene's start (R-55), and the fixtures exercise the three
    events worked_steps draws itself: the think ring, the fill of a blank step and a look_here."""
    kinds: set[str] = set()
    blanks = 0
    for tl in wsf.fixtures().values():
        for scene in tl.scenes:
            assert scene.look is not None
            assert scene.look.background.get("frozen_at") == 0.0, scene.scene_id
            kinds |= {e.kind for e in scene.events}
            blanks += sum(1 for item in scene.board if item.blank)
            for e in scene.events:
                assert 0.0 <= e.t <= scene.duration, (scene.scene_id, e.kind, e.t)
    assert kinds == {"countdown", "fill", "look_here"}
    assert blanks >= 3, "§4.3: the fade rule's blank steps are covered in both formats"


def test_a_blank_step_is_filled_after_it_enters() -> None:
    """Every blank step has a fill moment (a ``fill`` event or its beat's ``fill_item_id``) after its entrance, so no
    answer is withheld for the whole scene."""
    for name, tl in wsf.fixtures().items():
        for scene in tl.scenes:
            for item in scene.board:
                if not item.blank:
                    continue
                reveal = min([b.start for b in scene.beats if b.board_item_id == item.id], default=0.0)
                fills = [e.t for e in scene.events if e.kind == "fill" and e.target == item.id]
                fills += [b.start for b in scene.beats if b.fill_item_id == item.id]
                assert fills, f"{name}: {item.id} is never filled"
                assert min(fills) > reveal, f"{name}: {item.id} is filled before it enters"
