"""The ``animation_stage`` fixtures are what ``tests/compose/dynamic/make_animation_stage_fixtures.py`` builds (byte
for byte), cover a dark and a light family in each format and both shapes of §4.3's data (a ``simulation`` whose own
medium is the animation, and a ``manim`` side panel beside board text items), carry only the events the template
draws, and fit the slow 1x / 3x / reverse gate's 300 frames (``tests/determinism/test_template_fixtures.py``). Their
validity as template fixtures is the shared validator's (``test_template_fixtures.py``), which also checks the event
kinds against the registry's ``consumes``.
"""

from __future__ import annotations

import json

import pytest

from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic import make_animation_stage_fixtures as asf
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, fixture_name

GATE_FRAMES = 300  # tests/determinism/test_template_fixtures.py FRAMES

# web/js/dynamic/templates/animation_stage.js: the kinds and target forms the template draws, and its board data
KINDS = {"reveal", "cue_term", "look_here"}
TEXT_KINDS = {"heading", "bullet", "paragraph", "definition", "takeaway", "callout_info", "callout_tip",
              "callout_warning"}
MAX_ITEMS = 3


def test_the_committed_fixtures_are_current() -> None:
    stale = [p.name for p, text in asf.files().items()
             if not p.is_file() or p.read_text("utf-8") != text]
    assert stale == [], "run: python -m tests.compose.dynamic.make_animation_stage_fixtures"
    assert all(p.parent == FIXTURE_DIR for p in asf.files())


def test_check_passes_on_the_committed_files_and_a_fixture_is_deterministic() -> None:
    """``--check`` is what CI runs, and two builds of a fixture are byte for byte the same (the stills are flat
    colours, the keys sorted), so a stale file is a real change and never run-to-run noise."""
    assert asf.main(["--check"]) == 0
    assert {p.name: text for p, text in asf.files().items()} == {p.name: text for p, text in asf.files().items()}
    first = sorted(asf.files())[0]
    edited = json.loads(first.read_text("utf-8"))
    edited["scenes"][0]["title"] = "edited"
    assert json.dumps(edited, indent=2, sort_keys=True, ensure_ascii=False) + "\n" != asf.files()[first]


def test_both_families_in_both_formats_and_both_shapes_of_the_data() -> None:
    modes: dict[str, set[str]] = {}
    shapes: set[str] = set()
    for data in asf.fixtures().values():
        tl = Timeline.model_validate(data)
        assert tl.presentation is not None
        modes.setdefault(tl.presentation.format, set()).add(str(tl.presentation.mode))
        scene = tl.scenes[0]
        shapes.add("main" if scene.media is not None else "panel")
    assert modes == {"landscape": {"dark", "light"}, "portrait": {"dark", "light"}}
    assert shapes == {"main", "panel"}


@pytest.mark.parametrize("case", sorted(asf.fixtures()))
def test_each_fixture_is_an_animation_stage_scene_the_gate_sees_whole(case: str) -> None:
    tl = Timeline.model_validate(asf.fixtures()[case])
    name = f"{asf.TEMPLATE}.{case}.json"
    assert fixture_name(FIXTURE_DIR / name)[0] == asf.TEMPLATE
    for scene in tl.scenes:
        assert scene.look is not None and scene.look.template == asf.TEMPLATE
        assert round((scene.start + scene.duration) * tl.fps) <= GATE_FRAMES, "the gate sees every frame"
        # the template draws the animation, so every fixture scene has one (else board_generic would draw it)
        panel = scene.side_panel
        assert scene.media is not None or (panel is not None and panel.media is not None)
        if panel is not None:
            assert str(getattr(panel.panel.kind, "value", panel.panel.kind)) == "manim"


def test_the_board_is_what_the_template_draws() -> None:
    """At most MAX_ITEMS text items and nothing else (``drawsScene``); a simulation carries no board and takes its
    step labels from the beats' ``visual_cue`` instead (§4.3: one animation step per beat)."""
    for case, data in asf.fixtures().items():
        tl = Timeline.model_validate(data)
        for scene in tl.scenes:
            kinds = [str(getattr(i.kind, "value", i.kind)) for i in scene.board]
            assert all(k in TEXT_KINDS for k in kinds), f"{case}: {kinds}"
            assert len(kinds) <= MAX_ITEMS, f"{case}: {len(kinds)} items"
            if scene.board:
                assert all(b.visual_cue is None for b in scene.beats), f"{case}: items and visual cues both"
                revealed = {b.board_item_id for b in scene.beats}
                assert {i.id for i in scene.board} <= revealed, f"{case}: an item no beat reveals"
            else:
                assert scene.beats and all(b.visual_cue for b in scene.beats), f"{case}: no step label"


def test_the_events_are_ones_animation_stage_draws() -> None:
    kinds: set[str] = set()
    for case, data in asf.fixtures().items():
        tl = Timeline.model_validate(data)
        for scene in tl.scenes:
            ids = {i.id for i in scene.board}
            for e in scene.events:
                kind = str(e.kind)
                kinds.add(kind)
                assert kind in KINDS, f"{case}: {scene.scene_id}: {kind} is not drawn"
                assert e.target is not None
                head = str(e.target).split(":")[0]
                assert head == "panel" or head in ids, f"{case}: {e.target!r}"
                assert 0 <= e.t < scene.duration, f"{case}: {scene.scene_id}: event at {e.t}"
                if kind == "look_here" and head != "panel":
                    assert e.t + (e.dur or 0) <= scene.duration + 1e-6
    assert kinds == KINDS, "the fixtures exercise a reveal, a term cue and a look_here on the animation and on a label"


def test_a_term_cue_has_a_term_to_mark() -> None:
    """Each ``cue_term`` target carries ``[[...]]``, so cues.js finds an ``.rt-k`` span through ``itemEl``."""
    for case, data in asf.fixtures().items():
        tl = Timeline.model_validate(data)
        for scene in tl.scenes:
            items = {i.id: i for i in scene.board}
            for e in scene.events:
                if str(e.kind) != "cue_term":
                    continue
                item = items[str(e.target).split(":")[0]]
                assert "[[" in f"{item.term or ''} {item.text or ''}", f"{case}: {item.id} has no [[term]]"


def test_the_stills_are_larger_than_the_boxes_they_are_shown_in() -> None:
    """§P condition 1: a picture only ever downscales. The 16:9 stills are at least the 16:9 stage's width and the
    9:16 one at least the portrait stage's, so no fixture frame is drawn larger than its source."""
    for case, data in asf.fixtures().items():
        tl = Timeline.model_validate(data)
        for scene in tl.scenes:
            ref = scene.media or (scene.side_panel.media if scene.side_panel else None)
            assert ref is not None and ref.width and ref.height, case
            if tl.presentation is not None and tl.presentation.format == "portrait":
                assert ref.width >= 960, case  # the 9:16 stage is x 60-1020
            else:
                assert ref.width >= 1180, case  # the widest 16:9 frame under a one-line title and a label
