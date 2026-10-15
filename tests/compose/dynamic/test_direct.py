"""The Director (aadhi.compose.dynamic.direct; DYNAMIC_VIDEO_DESIGN §4.4 template selection, §4.2 classes, R-20,
R-28; B2 plan §1.6, §A step 5).

* Only a template the stage can draw is ever chosen: ``implemented``, release ``R1``, and the lecture's format in its
  ``formats``. A preferred template that is not built yet is skipped, ``board_generic`` is the only fallback, and a
  template becomes available by flipping ``implemented`` (checked with a patched registry).
* One scene per implemented template's §4.4 row picks it, in both formats.
* Forced scene types, locked looks (``SceneLook.lock``), the score terms (intent, R-20's repeat, the share, the
  unused bonus, the seeded tie-break) and the fixed order behind them.
* ``media_split``'s media side is constant inside a chapter and flips at a boundary (§4.3 / §4.4).
* Determinism (the same inputs always give the same plan), and the §2 preview budget (40 scenes in a fraction of
  ``build_timeline``'s 300 ms).
"""

from __future__ import annotations

import dataclasses
import statistics
import time

import pytest

from aadhi.compose.dynamic import direct
from aadhi.compose.dynamic import templates as tp
from aadhi.schemas.screenplay import SceneLook

from .plan_factories import board_scene, chapter_card, chapters, item, screenplay, timed

FORMATS = ("landscape", "portrait")


def _plan(scenes, *, fmt="landscape", chaps=None, durations=None, **over):  # type: ignore[no-untyped-def]
    """``direct.plan`` over ``scenes`` (screenplay scenes), each timed back to back."""
    sp = screenplay(scenes, format=fmt, **over)
    timeds = []
    start = 0.0
    for i, scene in enumerate(scenes):
        duration = durations[i] if durations else 20.0
        timeds.append(timed(scene, index=i, start=start, duration=duration))
        start += duration
    return direct.plan(sp, timeds, fmt, chaps)


def _one(scene, **kw):  # type: ignore[no-untyped-def]
    return _plan([scene], **kw)[0]


# --- what may be chosen ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", FORMATS)
def test_only_implemented_r1_templates_of_the_format_are_candidates(fmt) -> None:  # type: ignore[no-untyped-def]
    ids = direct.candidates(fmt)
    assert set(ids) == {t for t, s in tp.TEMPLATES.items() if s.implemented and s.release == "R1" and fmt in s.formats}
    # B1's, B2c's three pilots and the demo phase's six
    assert set(ids) == {"board_generic", "chapter_bumper", "definition_card", "formula_focus", "hook_question",
                        "key_terms", "list_build", "media_split", "recap_retrieval", "worked_steps"}
    for tid in ids:
        assert direct.candidate(tid, fmt) is tp.TEMPLATES[tid]
    assert direct.candidate("kinetic_statement", fmt) is None  # R1, not built yet
    assert direct.candidate("versus", fmt) is None  # built later (W2)
    assert direct.candidate("nope", fmt) is None


def test_a_template_becomes_available_the_moment_implemented_flips(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The gate is ``TemplateSpec.implemented``: nothing here names a template, so a template arrives with its JS."""
    scene = board_scene("s1", [item("t", "takeaway")])
    assert _one(scene).template == "board_generic"  # kinetic_statement is not built yet
    built = {**tp.TEMPLATES,
             "kinetic_statement": dataclasses.replace(tp.TEMPLATES["kinetic_statement"], implemented=True)}
    monkeypatch.setattr(tp, "TEMPLATES", built)
    monkeypatch.setattr(direct, "TEMPLATES", built)
    assert _one(scene).template == "kinetic_statement"


@pytest.mark.parametrize("fmt", FORMATS)
def test_board_generic_is_only_ever_the_fallback(fmt) -> None:  # type: ignore[no-untyped-def]
    """§4.4's fallback row. A board scene no row claims, and a scene whose forced template is not built, fall back;
    a scene a built template claims never does."""
    plain = _one(board_scene("s1", [item("h", "heading"), item("p", "paragraph"), item("q", "paragraph")]), fmt=fmt)
    assert plain.template == "board_generic" and plain.fallback and plain.semantic_class == "fallback"
    statement = _one(board_scene("s2", [item("t", "takeaway")]), fmt=fmt)  # kinetic_statement is not built yet
    assert statement.template == "board_generic" and statement.fallback
    unbuilt = {**tp.TEMPLATES,
               "chapter_bumper": dataclasses.replace(tp.TEMPLATES["chapter_bumper"], implemented=False)}
    with pytest.MonkeyPatch.context() as mp:  # a scene whose forced template is not built falls back
        mp.setattr(direct, "TEMPLATES", unbuilt)
        card = _one(chapter_card("cc"), fmt=fmt)
    assert card.template == "board_generic" and card.variant is None
    claimed = _one(board_scene("s1", [item("b1", "bullet"), item("b2", "bullet")]), fmt=fmt)
    assert claimed.template == "list_build" and not claimed.fallback


def test_board_generic_keeps_its_variant_empty() -> None:
    """B1's look: ``ResolvedLook.variant`` stays None for board_generic, so nothing existing changes."""
    assert _one(board_scene("s1", [item("h", "heading")])).variant is None


# --- the signal rows of the built templates -------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_formula_scene_picks_formula_focus(fmt) -> None:  # type: ignore[no-untyped-def]
    plan = _one(board_scene("s1", [item("f", "formula", variables=[("\\lambda", "wavelength")])]), fmt=fmt)
    assert (plan.template, plan.semantic_class, plan.fit) == ("formula_focus", "formula", tp.FIT_PREFERRED)


@pytest.mark.parametrize("fmt", FORMATS)
def test_two_bullets_pick_list_build(fmt) -> None:  # type: ignore[no-untyped-def]
    plan = _one(board_scene("s1", [item("h", "heading"), item("b1", "bullet"), item("b2", "bullet")]), fmt=fmt)
    assert (plan.template, plan.semantic_class) == ("list_build", "enumeration")


@pytest.mark.parametrize("fmt", FORMATS)
def test_text_beside_a_media_panel_picks_media_split(fmt) -> None:  # type: ignore[no-untyped-def]
    plan = _one(board_scene("s1", [item("h", "heading"), item("p", "paragraph")], panel="figure"), fmt=fmt)
    assert (plan.template, plan.semantic_class) == ("media_split", "media")


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_list_beside_a_panel_it_cannot_draw_goes_to_the_media_row(fmt) -> None:  # type: ignore[no-untyped-def]
    """B2c review CG-2: ``list_build`` draws only a picture or clip panel, and none in 9:16, so a list beside a chart
    is not its scene and the media row's ``media_split`` takes it instead (``list_build.js drawsScene`` would have
    handed such a scene to board_generic)."""
    scene = board_scene("s1", [item("b1", "bullet"), item("b2", "bullet")], panel="chart")
    assert _one(scene, fmt=fmt).template == "media_split"
    assert tp.TEMPLATES["list_build"].fit(scene, fmt) == 0.0


def test_the_first_row_with_a_candidate_wins_which_keeps_r28() -> None:
    """``signals`` lists the rows in §4.4's table order; a scene matching several takes the first one a built
    template claims, so a representational row is never passed over for a typography-only one (R-28)."""
    scene = board_scene("s1", [item("t", "takeaway")], panel="figure")  # statement row, then media row
    rows = [r.id for r in tp.signals(scene)]
    assert rows == ["statement", "media"]
    assert _one(scene).template == "media_split"  # kinetic_statement is not built, media_split is


# --- forced rows and locks ------------------------------------------------------------------------------------------


def test_a_forced_row_is_taken_before_the_class_rows() -> None:
    """A quiz forces ``check``; until it is built the scene falls back, and the scene's own class is never used
    instead of the forced one."""
    quiz = chapter_card("cc")
    unbuilt = {**tp.TEMPLATES,
               "chapter_bumper": dataclasses.replace(tp.TEMPLATES["chapter_bumper"], implemented=False)}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(direct, "TEMPLATES", unbuilt)
        plan = _one(quiz)
    assert plan.template == "board_generic" and not plan.forced
    forced = _one(quiz)
    assert forced.template == "chapter_bumper" and forced.forced and forced.semantic_class == "structure"


def test_a_pinned_or_settled_look_keeps_its_template() -> None:
    scene = board_scene("s1", [item("h", "heading"), item("p", "paragraph")], panel="figure",
                        look=SceneLook(template="board_generic", lock="pinned"))
    plan = _one(scene)
    assert plan.template == "board_generic" and plan.locked
    loose = board_scene("s2", [item("h", "heading"), item("p", "paragraph")], panel="figure",
                        look=SceneLook(template="board_generic", lock="auto"))
    assert _one(loose).template == "media_split"  # "auto" may be re-directed on save (§4.4)


def test_a_lock_on_a_template_the_stage_has_not_got_is_ignored() -> None:
    scene = board_scene("s1", [item("f", "formula", variables=[("\\lambda", "wavelength")])],
                        look=SceneLook(template="worked_steps", lock="pinned"))
    plan = _one(scene)
    assert plan.template == "formula_focus" and not plan.locked


def test_a_lock_on_a_template_that_cannot_render_the_scene_is_ignored() -> None:
    scene = board_scene("s1", [item("b1", "bullet"), item("b2", "bullet")],
                        look=SceneLook(template="formula_focus", lock="settled"))  # no formula item: fit 0
    assert _one(scene).template == "list_build"


# --- the score ------------------------------------------------------------------------------------------------------


def _ctx(**over):  # type: ignore[no-untyped-def]
    return dataclasses.replace(direct._Context(fmt="landscape", total=12), **over)


def test_r20_penalises_a_template_that_repeats_unless_the_scene_continues_it() -> None:
    scene = board_scene("s1", [item("b1", "bullet"), item("b2", "bullet")])
    plain = direct.score("list_build", 1.0, scene, _ctx(), None, continues=False)
    repeat = direct.score("list_build", 1.0, scene, _ctx(previous="list_build"), None, continues=False)
    carried = direct.score("list_build", 1.0, scene, _ctx(previous="list_build"), None, continues=True)
    assert repeat == pytest.approx(plain - direct.REPEAT_PENALTY)
    assert carried == pytest.approx(plain)


def test_the_share_and_unused_terms() -> None:
    scene = board_scene("s1", [item("b1", "bullet"), item("b2", "bullet")])
    free = direct.score("list_build", 1.0, scene, _ctx(), None, continues=False)
    over = direct.score("list_build", 1.0, scene, _ctx(used={"list_build": 3}), None, continues=False)
    recent = direct.score("list_build", 1.0, scene, _ctx(recent=("list_build",)), None, continues=False)
    assert over == pytest.approx(free - direct.SHARE_PENALTY)  # 25% of 12 scenes is 3
    assert recent == pytest.approx(free - direct.UNUSED_BONUS)
    assert not direct._share_exceeded({}, "list_build", 3, "landscape")  # a share always has room for one scene


def test_every_intent_in_the_table_is_a_real_visual_intent() -> None:
    """The keys are ``schemas.screenplay.VisualIntent``, so a typo cannot silently stop giving the bonus."""
    from typing import get_args

    from aadhi.schemas.screenplay import VisualIntent

    assert set(direct.INTENT_TEMPLATES) <= set(get_args(VisualIntent))
    assert all(t in tp.TEMPLATES for ts in direct.INTENT_TEMPLATES.values() for t in ts)


def test_the_visual_intent_bonus() -> None:
    plain = board_scene("s1", [item("b1", "bullet"), item("b2", "bullet")])
    structured = board_scene("s2", [item("b1", "bullet"), item("b2", "bullet")], intent="explain")
    a = direct.score("list_build", 1.0, plain, _ctx(), None, continues=False)
    b = direct.score("list_build", 1.0, structured, _ctx(), None, continues=False)
    assert b - a == pytest.approx(direct.INTENT_BONUS + direct.TIE_BREAK * (
        direct.seed_for(None, "s2", "list_build") - direct.seed_for(None, "s1", "list_build")))


def test_the_tie_break_is_seeded_by_the_lecture_never_by_hash() -> None:
    """§4.4: SHA-256 of the stored ``look_seed`` / ``layout_epoch``, so a rebuild reproduces the look."""
    one = direct.seed_for(None, "s1", "list_build")
    assert 0.0 <= one < 1.0 and one == direct.seed_for(None, "s1", "list_build")
    assert direct.seed_for(None, "s1", "list_build") != direct.seed_for(None, "s1", "media_split")


def test_the_score_never_decides_alone_when_only_one_candidate_fits() -> None:
    """Four media scenes in a row: the repeat and share penalties cannot push ``media_split`` out, because no other
    built template claims the media row (the §4.4 post-pass that would add variety is not built: see the module)."""
    scenes = [board_scene(f"s{i}", [item("h", "heading"), item("p", "paragraph")], panel="figure") for i in range(4)]
    assert [p.template for p in _plan(scenes)] == ["media_split"] * 4


# --- variants -------------------------------------------------------------------------------------------------------


def test_the_media_side_is_constant_inside_a_chapter_and_flips_at_the_boundary() -> None:
    """§4.3 / §4.4: the media side changes only at chapter boundaries."""
    scenes = [board_scene(f"s{i}", [item("h", "heading"), item("p", "paragraph")], panel="figure") for i in range(4)]
    plans = _plan(scenes, chaps=chapters((0.0, "One"), (40.0, "Two")))
    sides = [p.variant for p in plans]
    assert set(sides) <= set(direct.MEDIA_SIDES) and sides[0] == sides[1] and sides[2] == sides[3]
    assert sides[1] != sides[2]


def test_without_chapters_every_scene_is_in_chapter_zero() -> None:
    scenes = [board_scene(f"s{i}", [item("h", "heading"), item("p", "paragraph")], panel="figure") for i in range(3)]
    assert len({p.variant for p in _plan(scenes)}) == 1


def test_a_portrait_media_split_has_no_variant() -> None:
    """9:16 stacks, so ``media_split`` has no side there (``spec.variants['portrait']`` is empty)."""
    scene = board_scene("s1", [item("h", "heading"), item("p", "paragraph")], panel="figure")
    assert _one(scene, fmt="portrait").variant is None


def test_variant_for_falls_back_to_the_first_of_the_list() -> None:
    """A template with variants and no rule of its own is deterministic until its rule lands with its JS."""
    assert direct.variant_for("hook_question", "landscape", None, chapter=0) == "question"
    assert direct.variant_for("board_generic", "landscape", None, chapter=0) is None
    assert direct.variant_for("nope", "landscape", None, chapter=0) is None


# --- the plan as a whole --------------------------------------------------------------------------------------------


def test_a_plan_carries_the_scenes_events_and_the_lesson_wide_term_budget() -> None:
    """R-35 over the lecture: the same key term is not marked again in a later scene."""
    first = board_scene("s1", [item("b1", "bullet", text="The [[wave speed]] matters"), item("b2", "bullet")],
                        beats=[("b0", "Today the wave speed matters a great deal to us.", "b1"),
                               ("b1n", "Once again we meet the wave speed in this medium.", "b2")])
    second = board_scene("s2", [item("c1", "bullet", text="The [[wave speed]] again"), item("c2", "bullet")],
                         beats=[("b0", "Today the wave speed comes back one more time.", "c1"),
                                ("b1n", "And so on.", "c2")])
    plans = _plan([first, second], durations=[30.0, 30.0])
    marks = [[e for e in p.events if e.kind == "cue_term"] for p in plans]
    assert len(marks[0]) == 2 and marks[1] == []
    assert all(e.kind in tp.EVENT_KINDS for p in plans for e in p.events)


def test_a_holds_walk_reaches_the_events_through_the_scenes_look() -> None:
    """``direct.plan`` reads ``SceneLook.hold_reason`` and hands it to events.py (R-16)."""
    items = [item("f", "formula", variables=[("\\lambda", "wavelength")])]
    held = board_scene("s1", items, look=SceneLook(hold_reason="formula"))
    free = board_scene("s1", items)
    assert any(e.kind == "look_here" for e in _one(held, durations=[24.0]).events)
    assert not any(e.kind == "look_here" for e in _one(free, durations=[24.0]).events)


def test_every_event_is_one_the_chosen_template_consumes() -> None:
    scenes = [
        board_scene("s1", [item("f", "formula", variables=[("\\lambda", "wavelength")])],
                    look=SceneLook(hold_reason="formula")),
        board_scene("s2", [item("b1", "bullet", text="the [[wave speed]]"), item("b2", "bullet")],
                    beats=[("b0", "Today the wave speed matters.", "b1"), ("b1n", "And more.", "b2")]),
        board_scene("s3", [item("h", "heading"), item("p", "paragraph")], panel="terminal"),
        board_scene("s4", [item("h", "heading"), item("p", "paragraph"), item("q", "paragraph")]),
    ]
    for plan in _plan(scenes):
        accepts = tp.TEMPLATES[plan.template].consumes.accepts
        for e in plan.events:
            assert accepts(e.kind, e.target), f"{plan.template} does not draw {e.kind} on {e.target}"
            assert e.target is None or tp.parse_target(e.target).form in tp.TARGET_FORMS


def test_counts_and_a_missing_screenplay_scene() -> None:
    scenes = [board_scene("s1", [item("b1", "bullet"), item("b2", "bullet")]),
              board_scene("s2", [item("b1", "bullet"), item("b2", "bullet")])]
    plans = _plan(scenes)
    assert direct.template_counts(plans) == {"list_build": 2}
    sp = screenplay(scenes)
    stray = timed(scenes[0], index=0, start=0.0, duration=20.0)
    stray.scene_id = "gone"
    orphan = direct.plan(sp, [stray])[0]
    assert orphan.template == "board_generic" and orphan.events == ()


def test_the_format_is_taken_from_the_presentation_and_checked() -> None:
    scenes = [board_scene("s1", [item("h", "heading"), item("p", "paragraph")], panel="figure")]
    sp = screenplay(scenes, format="portrait")
    timeds = [timed(scenes[0])]
    assert direct.plan(sp, timeds)[0].variant is None  # 9:16: no side
    with pytest.raises(ValueError, match="unknown format"):
        direct.plan(sp, timeds, "square")  # type: ignore[arg-type]


def test_the_same_lecture_always_gives_the_same_plan() -> None:
    scenes = [board_scene("s1", [item("h", "heading"), item("p", "paragraph")], panel="figure"),
              board_scene("s2", [item("f", "formula", variables=[("\\lambda", "wavelength")])]),
              board_scene("s3", [item("b1", "bullet"), item("b2", "bullet")])]
    assert _plan(scenes) == _plan(scenes)


def test_forty_scenes_plan_well_inside_the_preview_budget() -> None:
    """Plan §2 "Classic isolation": ``build_timeline`` must build a 40-scene Dynamic preview in <= 300 ms, so the
    Director's own share has to be a fraction of it (this machine: about 4 ms). The bound keeps 25x headroom, so
    scheduling noise on a busy runner cannot flake it."""
    scenes = []
    for i in range(40):
        if i % 3 == 0:
            scenes.append(board_scene(f"s{i}", [item("h", "heading"),
                                                item("p", "paragraph", text="a [[wave speed]] note")], panel="figure"))
        elif i % 3 == 1:
            scenes.append(board_scene(f"s{i}", [item("b1", "bullet", text="the [[wave speed]] here"),
                                                item("b2", "bullet")]))
        else:
            scenes.append(board_scene(f"s{i}", [item("f", "formula", variables=[("\\lambda", "wavelength")])]))
    sp = screenplay(scenes)
    timeds = [timed(s, index=i, start=20.0 * i, duration=20.0) for i, s in enumerate(scenes)]
    direct.plan(sp, timeds)  # warm-up
    runs = []
    for _ in range(3):
        at = time.process_time()
        direct.plan(sp, timeds)
        runs.append(time.process_time() - at)
    assert statistics.median(runs) < 0.1, runs


def test_fallback_events_can_be_switched_off_for_a_board_generic_scene() -> None:
    """The flag the integrator has if a stored-JSON check objects: the events of a fallback scene are redundant (the
    stage computes the same times from the beats), so leaving them out changes no frame."""
    scenes = [board_scene("s1", [item("h", "heading"), item("p", "paragraph"), item("q", "paragraph")],
                          beats=[("b0", "The first.", "h"), ("b1", "The second.", "p")])]
    sp = screenplay(scenes)
    timeds = [timed(scenes[0])]
    with_events = direct.plan(sp, timeds)[0]
    without = direct.plan(sp, timeds, fallback_events=False)[0]
    assert with_events.template == without.template == "board_generic"
    assert [e.kind for e in with_events.events] == ["reveal", "reveal"] and without.events == ()


def test_the_variant_rules_read_the_scene_and_the_presentation() -> None:
    """§4.3 / §4.4: the stored choice where there is one, the scene's data otherwise. These templates are not built
    yet, so the rules are checked through ``variant_for`` directly."""
    from aadhi.schemas.screenplay import QuizScene

    pres = screenplay([board_scene("s1", [item("h", "heading")])], hook_kind="mystery",
                      intro_layout="pip").presentation
    assert direct.variant_for("hook_question", "landscape", pres, chapter=0) == "mystery"
    assert direct.variant_for("aadhi_intro", "landscape", pres, chapter=0) == "pip"
    assert direct.variant_for("aadhi_intro", "portrait", pres, chapter=0) == "card"  # 9:16 has one layout
    quiz = QuizScene.model_validate({"id": "q1", "type": "quiz_checkpoint", "question": "Which one?",
                                     "options": ["a", "b"], "correct_index": 0,
                                     "beats": [{"id": "b0", "narration": "Which one?"}],
                                     "reveal_beats": [{"id": "r0", "narration": "The first one."}]})
    assert direct.variant_for("check", "landscape", pres, chapter=0, scene=quiz) == "true_false"
    # definition_card: exactly what definition_card.js `variantOf` draws (review D7) - a picture in 16:9 only, an
    # icon only when the resolved look has one for the definition item, else `centred` (its DEFAULT_VARIANT). The
    # resolver writes no `look.icons` yet, so a plain card is stored as `centred`, not as a `card_icon_right` the
    # page would then draw as `centred` (whose slot is 324 px narrower).
    with_picture = board_scene("s2", [item("d", "definition")], panel="image")
    assert direct.variant_for("definition_card", "landscape", pres, chapter=0, scene=with_picture) == "card_image_left"
    assert direct.variant_for("definition_card", "portrait", pres, chapter=0, scene=with_picture) == "centred"
    plain = board_scene("s3", [item("d", "definition")])
    assert direct.variant_for("definition_card", "landscape", pres, chapter=0, scene=plain) == "centred"
    for fmt in ("landscape", "portrait"):
        assert direct.variant_for("definition_card", fmt, pres, chapter=0, scene=plain,  # type: ignore[arg-type]
                                  icons={"d": {"paths": ["M0 0"]}}) == "card_icon_right"
