"""The predict fixtures are what ``tests/compose/dynamic/make_predict_fixtures.py`` builds (byte for byte), cover a
dark and a light family in each format, hold the data §4.3 asks of the template (R-62: a blank question whose answer is
NOT in this scene), and fit the slow 1x / 3x / reverse gate's 300 frames closely enough that the gate sees every scene
(``tests/determinism/test_template_fixtures.py``). Their validity as template fixtures is the shared validator's
(``test_template_fixtures.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aadhi.compose.dynamic import templates as tp
from aadhi.compose.motion.chunks import scene_frame_bounds
from aadhi.schemas.screenplay import Beat, BoardItem, BoardScene, SceneIntent, SidePanel
from tests.compose.dynamic import make_predict_fixtures as pdf
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, fixture_name

GATE_FRAMES = 300  # tests/determinism/test_template_fixtures.py FRAMES


def _board(scene: object, *, panel: SidePanel | None = None) -> BoardScene:
    """The ``BoardScene`` the director scores, rebuilt from a fixture's timed scene: its items, and the narrative role
    a ``predict`` scene carries in the plan (``signals`` reads it; a timed scene keeps no intent)."""
    items = [BoardItem.model_validate(i.model_dump(mode="json")) for i in scene.board]  # type: ignore[attr-defined]
    return BoardScene(id=scene.scene_id, scene_id=scene.scene_id, type="content",  # type: ignore[attr-defined]
                      title=scene.title, board=items, side_panel=panel,  # type: ignore[attr-defined]
                      intent=SceneIntent(narrative_role="predict", visual_intent="predict"),
                      beats=[Beat(id="b0", narration="x", board_item_id=items[0].id)])


def test_the_committed_fixtures_are_current() -> None:
    assert pdf.stale() == [], "run: python -m tests.compose.dynamic.make_predict_fixtures"


def test_stale_finds_a_changed_or_missing_file(tmp_path: Path) -> None:
    for name, tl in pdf.fixtures().items():
        (tmp_path / name).write_text(pdf.render(tl), "utf-8", newline="\n")
    assert pdf.stale(tmp_path) == []
    first = sorted(pdf.fixtures())[0]
    data = json.loads((tmp_path / first).read_text("utf-8"))
    data["scenes"][0]["title"] = "edited"
    (tmp_path / first).write_text(json.dumps(data), "utf-8")
    (tmp_path / sorted(pdf.fixtures())[1]).unlink()
    assert pdf.stale(tmp_path) == sorted(pdf.fixtures())[:2]


@pytest.mark.parametrize("name", sorted(pdf.fixtures()))
def test_each_fixture_is_seen_by_the_gate(name: str) -> None:
    tl = pdf.fixtures()[name]
    bounds = scene_frame_bounds(tl)
    assert fixture_name(FIXTURE_DIR / name)[0] == "predict"
    # every scene starts inside the gate's frames, and at most a few frames of the last one fall outside them
    assert all(f0 < GATE_FRAMES for f0, _ in bounds), bounds
    assert bounds[-1][1] <= GATE_FRAMES + 15, bounds
    assert all(s.look is not None and s.look.template == "predict" for s in tl.scenes)


def test_both_families_in_both_formats() -> None:
    modes: dict[str, set[str]] = {}
    for tl in pdf.fixtures().values():
        assert tl.presentation is not None
        modes.setdefault(tl.presentation.format, set()).add(str(tl.presentation.mode))
    assert modes == {"landscape": {"dark", "light"}, "portrait": {"dark", "light"}}


def test_every_scene_holds_what_the_design_asks_of_a_prediction() -> None:
    """§4.3 / R-62: exactly one ``question`` item, ``blank``, at most a ``heading`` above it, and **no answer in this
    scene** — no ``justification`` on the question, no beat filling it and no ``answer`` event. The scene holds to
    think (R-16 ``hold_reason`` "think")."""
    for name, tl in pdf.fixtures().items():
        for scene in tl.scenes:
            kinds = [i.kind.value for i in scene.board]
            assert kinds.count("question") == 1, f"{name}: {scene.scene_id}: {kinds}"
            assert kinds.count("heading") <= 1, f"{name}: {scene.scene_id}: {kinds}"
            assert set(kinds) <= {"question", "heading"}, f"{name}: {scene.scene_id}: {kinds}"
            question = next(i for i in scene.board if i.kind.value == "question")
            assert question.blank, f"{name}: {scene.scene_id}: §4.3's question is blank"
            assert not (question.justification or "").strip(), f"{name}: {scene.scene_id}: R-62 — no answer here"
            assert all(b.fill_item_id is None for b in scene.beats), f"{name}: {scene.scene_id}: R-62"
            assert not [e for e in scene.events if e.kind == "answer"], f"{name}: {scene.scene_id}: R-62"
            assert scene.look is not None and scene.look.hold_reason == "think", f"{name}: {scene.scene_id}"
            # R-55: the resolver freezes the background of a scene with a hold throughout (resolve_looks)
            assert scene.look.background.get("frozen_at") == 0.0, f"{name}: {scene.scene_id}"


def test_every_scene_is_a_board_the_template_prefers() -> None:
    """Every fixture scene's board scores ``_fit_predict`` FIT_PREFERRED in both formats: the fixtures exercise the
    data direct.py will send the template, not data only the page tolerates."""
    spec = tp.TEMPLATES["predict"]
    for name, tl in pdf.fixtures().items():
        for scene in tl.scenes:
            for fmt in ("landscape", "portrait"):
                assert spec.fit(_board(scene), fmt) == tp.FIT_PREFERRED, f"{name}: {scene.scene_id} in {fmt}"


def test_the_fit_refuses_what_the_page_hands_to_board_generic() -> None:
    """The CG-2 parity rule the demo phase's review added (``_fit_hook_question`` / ``_fit_key_terms``): a scene
    ``predict.js drawsScene`` refuses must score 0, or the director plans ``predict`` for a scene board_generic then
    draws whole. ``predict.js`` draws no medium, so a scene carrying a side panel is not this template's.

    REQUESTED CHANGE (``aadhi/compose/dynamic/templates.py _fit_predict``), until which this test fails::

        def _fit_predict(scene: SceneBase, fmt: Format) -> float:
            # predict.js draws no medium (drawsScene hands such a scene to board_generic whole), so a scene with a
            # side panel is not this template's (B2c review CG-2, B2-demo review D5)
            if _panel(scene) is not None:
                return FIT_NONE
            return FIT_PREFERRED if _blank_question(scene) else FIT_NONE
    """
    spec = tp.TEMPLATES["predict"]
    scene = next(iter(pdf.fixtures().values())).scenes[0]
    panel = SidePanel.model_validate({"kind": "image", "image_prompt": "a lamp on a desk"})
    with_panel = _board(scene, panel=panel)
    for fmt in ("landscape", "portrait"):
        assert spec.fit(with_panel, fmt) == tp.FIT_NONE, f"{fmt}: a scene with a side panel is board_generic's whole"
