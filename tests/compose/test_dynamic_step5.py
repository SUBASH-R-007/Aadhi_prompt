"""Step 5's wiring of the new templates into a built Dynamic timeline (``compose/timeline.py`` -> ``dynamic/direct.py``
-> ``compose/presentation.py``), end to end through ``build_timeline``:

* ``outro_next`` closes a lecture whose last scene is R-11's closure shape (§4.4's positional row), its variant follows
  the destination, an ``end_screen`` outro is held to exactly 15 s (§2.5), and ``refs.next`` carries the caller's next
  title on that scene only (nothing is invented without one);
* a 9:16 scene whose side panel is its own beat (owner decision 4) starts its narration later (the reel lead), and its
  events carry the beat's ``reveal`` on ``panel``;
* ``code_walkthrough``'s scene stores the editor's surface (``code.surface``) and no other scene does;
* a reel quiz carries its options' misconception ids in a Dynamic build only;
* a chart drawn as a plot is no longer reported as an undrawn panel.

Classic output is unchanged by all of it (tests/compose/test_dynamic_presentation.py, tests/invariance).
"""

from __future__ import annotations

from typing import Any

import pytest

from aadhi.compose.dynamic import themes as th
from aadhi.compose.motion import job as job_mod
from aadhi.compose.presentation import family_for
from aadhi.compose.timeline import SCENE_LEAD_SECONDS, build_timeline
from aadhi.config import Settings
from aadhi.schemas.screenplay import Screenplay
from aadhi.schemas.timeline import Timeline

from .factories import beat, make_manifest, screenplay_dict


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, render_fps=25, dynamic_enabled=True, **kw)


def _screenplay(scenes: list[dict[str, Any]], fmt: str = "landscape", *,
                misconceptions: list[dict[str, Any]] | None = None, **presentation: Any) -> Screenplay:
    from tests.schemas.v3_fixtures import PRESENTATION

    data = screenplay_dict()
    data["scenes"] = scenes
    if misconceptions:
        data["misconceptions"] = misconceptions
    data["presentation"] = {**PRESENTATION, "format": fmt, "hook": None, "theme_family": "midnight_indigo",
                            "captions_burn": True, "music": {"source": "procedural", "provider": "procedural"},
                            **presentation}
    return Screenplay.model_validate(data)


def _build(sp: Screenplay, **kw: Any) -> Timeline:
    settings = kw.pop("settings", None) or _settings()
    return build_timeline(sp, make_manifest(sp, skip_audio=()), settings=settings, **kw)


def _content(sid: str) -> dict[str, Any]:
    return {"id": sid, "type": "content", "title": "Ohm's law", "concept_id": "ohm",
            "board": [{"id": f"{sid}-b1", "kind": "bullet", "text": "Voltage pushes current"},
                      {"id": f"{sid}-b2", "kind": "bullet", "text": "Resistance holds it back"}],
            "beats": [beat(f"{sid}-n1", "Voltage pushes current.", board_item_id=f"{sid}-b1"),
                      beat(f"{sid}-n2", "Resistance holds it back.", board_item_id=f"{sid}-b2")]}


def _closing(sid: str = "s-end") -> dict[str, Any]:
    return {"id": sid, "type": "summary", "title": "The big idea", "concept_id": "ohm",
            "board": [{"id": f"{sid}-t", "kind": "takeaway", "text": "Current follows voltage over resistance"}],
            "beats": [beat(f"{sid}-n1", "Current follows voltage over resistance.", board_item_id=f"{sid}-t")]}


# --- outro_next -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("destination", "variant"), [("youtube", "end_screen"), ("lms", "next_up")])
def test_a_closing_takeaway_ends_the_video_on_its_outro(destination: str, variant: str) -> None:
    tl = _build(_screenplay([_content("s1"), _closing()], destination=destination))
    last = tl.scenes[-1]
    assert last.look is not None and last.look.template == "outro_next" and last.look.variant == variant
    assert tl.scenes[0].look is not None and tl.scenes[0].look.template == "aadhi_intro"  # Aadhi opens it
    if variant == "end_screen":  # §2.5: exactly 15 s, the difference a hold after the content
        assert last.duration == 15.0 and last.hold_seconds > 0
    else:
        assert last.hold_seconds == 0
    assert tl.total_duration == pytest.approx(last.start + last.duration)
    # the same scene in the middle of a lecture is the statement row's
    mid = _build(_screenplay([_closing("s-mid"), _content("s2")], destination=destination))
    assert [s.look.template for s in mid.scenes if s.look is not None][1] == "kinetic_statement"


def test_a_9_16_outro_has_no_variant_and_keeps_its_length() -> None:
    tl = _build(_screenplay([_content("s1"), _closing()], fmt="portrait", destination="youtube"))
    last = tl.scenes[-1]
    assert last.look is not None and last.look.template == "outro_next" and last.look.variant is None
    assert last.hold_seconds == 0


def test_refs_next_carries_the_callers_next_title_on_the_outro_only() -> None:
    sp = _screenplay([_content("s1"), _closing()], destination="lms")
    tl = _build(sp, next_title="  Part 2: Power  ")
    refs = [s.look.refs for s in tl.scenes if s.look is not None]
    assert refs[-1] is not None and refs[-1].next is not None and refs[-1].next.title == "Part 2: Power"
    assert all(r is None or r.next is None for r in refs[:-1])
    for empty in (None, "", "   "):  # nothing is invented
        tl = _build(sp, next_title=empty)
        assert all(s.look.refs is None or s.look.refs.next is None for s in tl.scenes if s.look is not None)


# --- the reel's panel beat (owner decision 4) -----------------------------------------------------------------------


def _worked_with_terminal(sid: str = "s-ex") -> dict[str, Any]:
    return {"id": sid, "type": "example", "title": "Worked example", "concept_id": "ohm",
            "board": [{"id": f"{sid}-h", "kind": "heading", "text": "Find the current"},
                      {"id": f"{sid}-1", "kind": "example_step", "text": "I = V / R"},
                      {"id": f"{sid}-2", "kind": "example_step", "text": "I = 6 / 3 = 2 A", "blank": True}],
            "beats": [beat(f"{sid}-n1", "Find the current.", board_item_id=f"{sid}-h"),
                      beat(f"{sid}-n2", "Current is voltage over resistance.", board_item_id=f"{sid}-1"),
                      beat(f"{sid}-n3", "So it is two amperes.", board_item_id=f"{sid}-2")],
            "side_panel": {"kind": "terminal", "terminal": {"command": "python ohm.py", "output": "I = 2.0 A"},
                           "rationale": "the program prints it"}}


def test_a_reel_panel_beat_starts_the_narration_later_and_plans_the_beat() -> None:
    reel = _build(_screenplay([_content("s1"), _worked_with_terminal(), _content("s3")], fmt="portrait"))
    scene = next(s for s in reel.scenes if s.scene_id == "s-ex")
    assert scene.look is not None and scene.look.template == "worked_steps"
    assert scene.audio_offset > SCENE_LEAD_SECONDS  # the reel lead (reels.reel_lead_for)
    beat_events = [e for e in scene.events if e.kind == "reveal" and e.target == "panel"]
    assert len(beat_events) == 1 and beat_events[0].dur is not None and beat_events[0].dur > 0
    # 16:9: the panel stands beside the steps, so no lead and no beat
    wide = _build(_screenplay([_content("s1"), _worked_with_terminal(), _content("s3")]))
    scene = next(s for s in wide.scenes if s.scene_id == "s-ex")
    assert scene.audio_offset == SCENE_LEAD_SECONDS
    assert not [e for e in scene.events if e.kind == "reveal" and e.target == "panel"]


# --- the code surface and the quiz's misconception ids --------------------------------------------------------------


def test_the_code_editor_s_surface_is_stored_on_its_scene_only() -> None:
    code = {"id": "s-code", "type": "content", "title": "The program", "concept_id": "ohm",
            "board": [{"id": "c", "kind": "code", "language": "python", "code": "v = 6\nr = 3\nprint(v / r)"}],
            "beats": [beat("s-code-n1", "Here is the program.", board_item_id="c")]}
    tl = _build(_screenplay([_content("s1"), code, _content("s3")]))
    looks = {s.scene_id: s.look for s in tl.scenes if s.look is not None}
    assert looks["s-code"].template == "code_walkthrough"
    assert looks["s-code"].tokens["code.surface"] == th.code_surface(family_for("midnight_indigo"))
    assert all("code.surface" not in lk.tokens for sid, lk in looks.items() if sid != "s-code")


def test_a_dynamic_quiz_carries_its_options_misconception_ids() -> None:
    quiz = {"id": "s-quiz", "type": "quiz_checkpoint", "title": "Check", "concept_id": "ohm",
            "question": "If R doubles at fixed V, what happens to I?",
            "options": ["It halves", "It doubles", "It stays the same"], "correct_index": 0,
            "option_misconception_ids": [None, "m-more", None], "feedback_wrong": ["", "", ""],
            "explanation": "I = V / R.", "countdown_seconds": 5,
            "beats": [beat("s-quiz-b1", "Here is a quick question.")],
            "reveal_beats": [beat("s-quiz-r1", "The current halves.")]}
    sp = _screenplay([_content("s1"), quiz, _content("s3")], fmt="portrait",
                     misconceptions=[{"id": "m-more", "concept_id": "ohm", "statement": "More R means more current",
                                      "correction": "Current falls as resistance rises"}])
    tl = _build(sp)
    timed = next(s for s in tl.scenes if s.scene_id == "s-quiz")
    assert timed.quiz is not None and timed.quiz.option_misconception_ids == [None, "m-more", None]
    assert timed.look is not None and timed.look.template == "check" and timed.look.variant == "true_false"
    classic = build_timeline(sp.model_copy(update={"presentation": None}), make_manifest(sp, skip_audio=()),
                             settings=_settings())
    quiz_json = next(s for s in classic.model_dump(mode="json")["scenes"] if s["scene_id"] == "s-quiz")["quiz"]
    assert "option_misconception_ids" not in quiz_json  # a classic timeline never carries it


# --- the render warnings --------------------------------------------------------------------------------------------


def test_a_chart_drawn_as_a_plot_is_not_reported_as_undrawn() -> None:
    chart = {"id": "s-chart", "type": "content", "title": "Power", "concept_id": "ohm",
             "board": [{"id": "h", "kind": "heading", "text": "Power of three appliances"}],
             "beats": [beat("s-chart-n1", "A fan, an AC and a geyser.")],
             "side_panel": {"kind": "chart", "title": "Power", "rationale": "compare",
                            "chart": {"chart_type": "bar", "labels": ["Fan", "AC", "Geyser"],
                                      "datasets": [{"label": "Watts", "data": [60, 1500, 2000]}]}}}
    tl = _build(_screenplay([_content("s1"), chart, _content("s3")]))
    scene = next(s for s in tl.scenes if s.scene_id == "s-chart")
    assert scene.look is not None and scene.look.template == "chart_build"
    assert not [w for w in job_mod.undrawn_panel_warnings(tl) if w["scene_id"] == "s-chart"]
    # the same chart on a board_generic scene is still its title card, and warned
    data = tl.model_dump(mode="json")
    for s in data["scenes"]:
        if s["scene_id"] == "s-chart":
            s["look"]["template"] = "board_generic"
    warned = job_mod.undrawn_panel_warnings(Timeline.model_validate(data))
    assert [w["scene_id"] for w in warned] == ["s-chart"]
