"""The recap_retrieval fixtures are what ``tests/compose/dynamic/make_recap_retrieval_fixtures.py`` builds (byte for
byte), cover a dark and a light family in each format, hold the data §4.3 asks of the template, and fit the slow
1x / 3x / reverse gate's 300 frames closely enough that the gate sees every scene
(``tests/determinism/test_template_fixtures.py``). Their validity as template fixtures is the shared validator's
(``test_template_fixtures.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aadhi.compose.dynamic import templates as tp
from aadhi.compose.motion.chunks import scene_frame_bounds
from aadhi.schemas.screenplay import Beat, BoardItem, BoardScene
from tests.compose.dynamic import make_recap_retrieval_fixtures as rrf
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, fixture_name

GATE_FRAMES = 300  # tests/determinism/test_template_fixtures.py FRAMES


def test_the_committed_fixtures_are_current() -> None:
    assert rrf.stale() == [], "run: python -m tests.compose.dynamic.make_recap_retrieval_fixtures"


def test_stale_finds_a_changed_or_missing_file(tmp_path: Path) -> None:
    for name, tl in rrf.fixtures().items():
        (tmp_path / name).write_text(rrf.render(tl), "utf-8", newline="\n")
    assert rrf.stale(tmp_path) == []
    first = sorted(rrf.fixtures())[0]
    data = json.loads((tmp_path / first).read_text("utf-8"))
    data["scenes"][0]["title"] = "edited"
    (tmp_path / first).write_text(json.dumps(data), "utf-8")
    (tmp_path / sorted(rrf.fixtures())[1]).unlink()
    assert rrf.stale(tmp_path) == sorted(rrf.fixtures())[:2]


@pytest.mark.parametrize("name", sorted(rrf.fixtures()))
def test_each_fixture_is_seen_by_the_gate(name: str) -> None:
    tl = rrf.fixtures()[name]
    bounds = scene_frame_bounds(tl)
    assert fixture_name(FIXTURE_DIR / name)[0] == "recap_retrieval"
    # every scene starts inside the gate's frames, and at most a few frames of the last one fall outside them
    assert all(f0 < GATE_FRAMES for f0, _ in bounds), bounds
    assert bounds[-1][1] <= GATE_FRAMES + 15, bounds
    assert all(s.look is not None and s.look.template == "recap_retrieval" for s in tl.scenes)


def test_both_families_in_both_formats() -> None:
    modes: dict[str, set[str]] = {}
    for tl in rrf.fixtures().values():
        assert tl.presentation is not None
        modes.setdefault(tl.presentation.format, set()).add(str(tl.presentation.mode))
    assert modes == {"landscape": {"dark", "light"}, "portrait": {"dark", "light"}}


def test_every_scene_is_a_recap_the_template_prefers() -> None:
    """Every fixture scene is a ``recap`` scene whose board ``_fit_recap_retrieval`` scores FIT_PREFERRED: the fixtures
    exercise the data direct.py will send the template, not data only the page tolerates."""
    spec = tp.TEMPLATES["recap_retrieval"]
    for name, tl in rrf.fixtures().items():
        for scene in tl.scenes:
            assert scene.type == "recap", f"{name}: {scene.scene_id}"
            items = [BoardItem.model_validate(i.model_dump(mode="json")) for i in scene.board]
            board = BoardScene(id=scene.scene_id, scene_id=scene.scene_id, type="recap", title=scene.title,
                               board=items, beats=[Beat(id="b0", narration="x", board_item_id=items[0].id)])
            for fmt in ("landscape", "portrait"):
                assert spec.fit(board, fmt) == tp.FIT_PREFERRED, f"{name}: {scene.scene_id} in {fmt}"


def test_the_landscape_fixture_holds_what_the_design_asks_of_a_recap() -> None:
    """§4.3: two blank ``question`` items with their answer in ``justification`` (at least one of them apply or
    transfer), a free-recall prompt and three ``takeaway`` tiles, each with an icon the resolver stored."""
    tl = rrf.fixtures()["recap_retrieval.landscape.json"]
    scene = tl.scenes[0]
    questions = [i for i in scene.board if i.kind.value == "question"]
    blanks = [i for i in questions if i.blank]
    assert len(blanks) == 2 and all((i.justification or "").strip() for i in blanks)
    assert {i.level for i in blanks} & {"apply", "transfer"}
    assert [i.id for i in questions if not i.blank] == ["r1"], "the free-recall prompt"
    tiles = [i for i in scene.board if i.kind.value == "takeaway"]
    assert len(tiles) == 3
    assert scene.look is not None
    assert all(t.id in scene.look.icons for t in tiles)
    # each blank question is filled by a later beat or by an `answer` event (§4.3: the beat's pause is the think time)
    filled = {b.fill_item_id for b in scene.beats if b.fill_item_id}
    answered = {e.target for e in scene.events if e.kind == "answer"}
    assert all(i.id in filled | answered for i in blanks)
