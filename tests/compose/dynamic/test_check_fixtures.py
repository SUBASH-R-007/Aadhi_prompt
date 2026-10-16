"""The check fixtures are what ``tests/compose/dynamic/make_check_fixtures.py`` builds (byte for byte), cover a dark
and a light family in each format, hold the data §4.3 asks of the template, stay inside the option cap each format
allows (``templates.CHECK_MAX_OPTIONS``, which ``check.js MAX_OPTIONS`` mirrors), and fit the slow 1x / 3x / reverse
gate's 300 frames closely enough that the gate sees both scenes
(``tests/determinism/test_template_fixtures.py``). Their validity as template fixtures is the shared validator's
(``test_template_fixtures.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aadhi.compose.dynamic import templates as tp
from aadhi.compose.motion.chunks import scene_frame_bounds
from aadhi.schemas.screenplay import Beat, BoardItem, BoardScene, SceneIntent
from tests.compose.dynamic import make_check_fixtures as ckf
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, fixture_name

GATE_FRAMES = 300  # tests/determinism/test_template_fixtures.py FRAMES


def test_the_committed_fixtures_are_current() -> None:
    assert ckf.stale() == [], "run: python -m tests.compose.dynamic.make_check_fixtures"


def test_stale_finds_a_changed_or_missing_file(tmp_path: Path) -> None:
    for name, tl in ckf.fixtures().items():
        (tmp_path / name).write_text(ckf.render(tl), "utf-8", newline="\n")
    assert ckf.stale(tmp_path) == []
    first = sorted(ckf.fixtures())[0]
    data = json.loads((tmp_path / first).read_text("utf-8"))
    data["scenes"][0]["title"] = "edited"
    (tmp_path / first).write_text(json.dumps(data), "utf-8")
    (tmp_path / sorted(ckf.fixtures())[1]).unlink()
    assert ckf.stale(tmp_path) == sorted(ckf.fixtures())[:2]


@pytest.mark.parametrize("name", sorted(ckf.fixtures()))
def test_each_fixture_is_seen_by_the_gate(name: str) -> None:
    tl = ckf.fixtures()[name]
    bounds = scene_frame_bounds(tl)
    assert fixture_name(FIXTURE_DIR / name)[0] == "check"
    # every scene starts inside the gate's frames, and at most a few frames of the last one fall outside them
    assert all(f0 < GATE_FRAMES for f0, _ in bounds), bounds
    assert bounds[-1][1] <= GATE_FRAMES + 15, bounds
    assert all(s.look is not None and s.look.template == "check" for s in tl.scenes)


def test_both_families_in_both_formats() -> None:
    modes: dict[str, set[str]] = {}
    for tl in ckf.fixtures().values():
        assert tl.presentation is not None
        modes.setdefault(tl.presentation.format, set()).add(str(tl.presentation.mode))
    assert modes == {"landscape": {"dark", "light"}, "portrait": {"dark", "light"}}


def test_every_fixture_holds_both_forms_the_template_draws() -> None:
    """Each file has one quiz scene (``TimedScene.quiz``: §4.3's mcq or true / false) and one board scene with a
    single blank ``question`` item (free recall / apply, its answer in ``justification``) - the two forms
    ``check.js checkItems`` reads."""
    for name, tl in ckf.fixtures().items():
        quizzes = [s for s in tl.scenes if s.quiz is not None]
        boards = [s for s in tl.scenes if s.quiz is None]
        assert len(quizzes) == 1 and len(boards) == 1, name
        quiz = quizzes[0].quiz
        assert quiz is not None
        assert quiz.question.strip() and quiz.explanation.strip(), name
        assert 0 <= quiz.correct_index < len(quiz.options), name
        assert quiz.countdown_seconds >= 3, name  # the schema's shortest think time
        assert quiz.reveal_start >= quiz.countdown_start + quiz.countdown_seconds, name
        assert not quizzes[0].board, f"{name}: a quiz scene carries no board item (check.js drawsScene)"
        items = [i for i in boards[0].board if i.kind.value == "question"]
        assert len(items) == 1 and items[0].blank and (items[0].justification or "").strip(), name


def test_no_fixture_passes_the_option_cap_of_its_format() -> None:
    """§4.3: `mcq` is 16:9 only and 9:16 takes two options (``templates.CHECK_MAX_OPTIONS``, mirrored by
    ``check.js MAX_OPTIONS``). A quiz past the cap is ``board_generic``'s whole scene, so a fixture past it would
    never be drawn by this template."""
    assert dict(tp.CHECK_MAX_OPTIONS) == {"landscape": 4, "portrait": 2}, "check.js MAX_OPTIONS mirrors this"
    for name, tl in ckf.fixtures().items():
        assert tl.presentation is not None
        cap = tp.CHECK_MAX_OPTIONS[tl.presentation.format]
        for scene in tl.scenes:
            if scene.quiz is not None:
                assert 2 <= len(scene.quiz.options) <= cap, name


def test_every_board_scene_is_one_the_template_prefers() -> None:
    """A board check's shape is the one ``_fit_check`` scores FIT_PREFERRED - one blank ``question`` item on a scene
    whose narrative role is ``check`` (``signals``' prompt row) - in both formats: the fixtures exercise the data
    direct.py will send the template, not data only the page tolerates."""
    spec = tp.TEMPLATES["check"]
    for name, tl in ckf.fixtures().items():
        for scene in tl.scenes:
            if scene.quiz is not None:
                continue
            items = [BoardItem.model_validate(i.model_dump(mode="json")) for i in scene.board]
            board = BoardScene(id=scene.scene_id, type="content", title=scene.title, board=items,
                               intent=SceneIntent(narrative_role="check"),
                               beats=[Beat(id="b0", narration="x", board_item_id=items[0].id)])
            for fmt in ("landscape", "portrait"):
                assert spec.fit(board, fmt) == tp.FIT_PREFERRED, f"{name}: {scene.scene_id} in {fmt}"


def test_the_events_are_only_the_kinds_the_template_consumes() -> None:
    """The board form's ring and reveal are timed by ``countdown`` / ``answer`` events on the question item, and one
    file reveals it with a ``reveal`` event too; ``check`` consumes exactly these three kinds (templates.py), and
    the stage would skip anything else."""
    spec = tp.TEMPLATES["check"]
    seen: set[str] = set()
    for name, tl in ckf.fixtures().items():
        for scene in tl.scenes:
            for event in scene.events:
                assert event.kind in spec.consumes.kinds, f"{name}: {event.kind}"
                assert event.target is not None and spec.consumes.accepts(event.kind, event.target), \
                    f"{name}: {event.kind} on {event.target!r}"
                seen.add(event.kind)
    assert seen == {"reveal", "countdown", "answer"}
