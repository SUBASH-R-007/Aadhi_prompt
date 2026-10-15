"""The key_terms fixtures are what ``tests/compose/dynamic/make_key_terms_fixtures.py`` builds (byte for byte), cover
a dark and a light family in each format, carry the cue and draw events the template draws on 2-4 definition items,
and fit the slow 1x / 3x / reverse gate's 300 frames closely enough that the gate sees every scene
(``tests/determinism/test_template_fixtures.py``). Their validity as template fixtures is the shared validator's
(``test_template_fixtures.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aadhi.compose.dynamic import templates as tp
from aadhi.compose.motion.chunks import scene_frame_bounds
from tests.compose.dynamic import make_key_terms_fixtures as ktf
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, fixture_name

GATE_FRAMES = 300  # tests/determinism/test_template_fixtures.py FRAMES


def test_the_committed_fixtures_are_current() -> None:
    assert ktf.stale() == [], "run: python -m tests.compose.dynamic.make_key_terms_fixtures"


def test_files_and_fixtures_agree() -> None:
    assert {p.name: text for p, text in ktf.files().items()} == {
        name: ktf.render(tl) for name, tl in ktf.fixtures().items()}
    assert all(p.parent == FIXTURE_DIR for p in ktf.files())


def test_stale_finds_a_changed_or_missing_file(tmp_path: Path) -> None:
    for name, tl in ktf.fixtures().items():
        (tmp_path / name).write_text(ktf.render(tl), "utf-8", newline="\n")
    assert ktf.stale(tmp_path) == []
    first = sorted(ktf.fixtures())[0]
    data = json.loads((tmp_path / first).read_text("utf-8"))
    data["scenes"][0]["title"] = "edited"
    (tmp_path / first).write_text(json.dumps(data), "utf-8")
    (tmp_path / sorted(ktf.fixtures())[1]).unlink()
    assert ktf.stale(tmp_path) == sorted(ktf.fixtures())[:2]


@pytest.mark.parametrize("name", sorted(ktf.fixtures()))
def test_each_fixture_is_seen_by_the_gate(name: str) -> None:
    tl = ktf.fixtures()[name]
    bounds = scene_frame_bounds(tl)
    assert fixture_name(FIXTURE_DIR / name)[0] == "key_terms"
    # every scene starts inside the gate's frames, and at most a few frames of the last one fall outside them
    assert all(f0 < GATE_FRAMES for f0, _ in bounds), bounds
    assert bounds[-1][1] <= GATE_FRAMES + 15, bounds
    assert all(s.look is not None and s.look.template == "key_terms" for s in tl.scenes)


def test_both_families_in_both_formats() -> None:
    modes: dict[str, set[str]] = {}
    for tl in ktf.fixtures().values():
        assert tl.presentation is not None
        modes.setdefault(tl.presentation.format, set()).add(str(tl.presentation.mode))
    assert modes == {"landscape": {"dark", "light"}, "portrait": {"dark", "light"}}


def test_every_scene_is_a_key_terms_scene_the_registry_prefers() -> None:
    """2-4 definition items (and at most one heading), terms of <= 3 words and meanings of <= 8 words: what §4.3 gives
    the template, so ``_fit_key_terms`` scores every fixture scene 1.0 in both formats (the stress scene included)."""
    for name, tl in ktf.fixtures().items():
        for scene in tl.scenes:
            items = [i for i in scene.board if str(getattr(i.kind, "value", i.kind)) == "definition"]
            headings = [i for i in scene.board if str(getattr(i.kind, "value", i.kind)) == "heading"]
            assert 2 <= len(items) <= 4, f"{name}: {scene.scene_id} has {len(items)} definitions"
            assert len(headings) <= 1, f"{name}: {scene.scene_id} has {len(headings)} headings"
            assert len(items) + len(headings) == len(scene.board), f"{name}: {scene.scene_id} has other items"
            for item in items:
                assert item.term and item.text, f"{name}: {scene.scene_id}: {item.id} needs a term and a meaning"
                assert len((item.term or "").split()) <= 3, f"{name}: {item.id}: term over 3 words"
                assert len((item.text or "").split()) <= 8, f"{name}: {item.id}: meaning over 8 words"


def test_the_events_are_ones_key_terms_draws() -> None:
    """Every event names a term card and a kind the spec consumes (the shared validator checks the cue kinds against
    ``consumes``; this also pins the ``draw`` events, which the template draws itself, to real items)."""
    spec = tp.TEMPLATES["key_terms"]
    kinds: set[str] = set()
    for name, tl in ktf.fixtures().items():
        for scene in tl.scenes:
            ids = {i.id for i in scene.board}
            for e in scene.events:
                kinds.add(str(e.kind))
                assert str(e.kind) in spec.consumes.kinds, f"{name}: {scene.scene_id}: {e.kind} is not consumed"
                assert e.target is not None and e.target.split(":")[0] in ids, f"{name}: {e.target!r}"
                assert 0 <= e.t < scene.duration, f"{name}: {scene.scene_id}: event at {e.t}"
    assert {"cue_term", "draw"} <= kinds, "the fixtures exercise the term cue and the icon draw"


def test_a_term_cue_has_a_term_to_mark() -> None:
    """Each ``cue_term`` target carries ``[[...]]`` in its term or its meaning, so the stage finds an `.rt-k` span
    (cues.js termsOf over the template's ``itemEl``; the jsdom harness checks the drawn spans)."""
    for name, tl in ktf.fixtures().items():
        for scene in tl.scenes:
            items = {i.id: i for i in scene.board}
            for e in scene.events:
                if str(e.kind) != "cue_term":
                    continue
                item = items[str(e.target).split(":")[0]]
                assert "[[" in f"{item.term or ''} {item.text or ''}", f"{name}: {item.id} has no [[term]]"


def test_the_icons_the_resolver_stored_are_on_term_items() -> None:
    for name, tl in ktf.fixtures().items():
        for scene in tl.scenes:
            assert scene.look is not None
            hinted = {i.id for i in scene.board if i.icon_hint}
            stored = set(scene.look.icons)
            assert stored <= hinted, f"{name}: {scene.scene_id}: an icon for an item with no hint"
        assert any(scene.look is not None and scene.look.icons for scene in tl.scenes), f"{name}: no stored icon"
