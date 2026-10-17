"""The visual events of a Dynamic scene (aadhi.compose.dynamic.events; DYNAMIC_VIDEO_DESIGN §4.8, R-15 to R-17,
R-26, R-35; B2 plan §1.5-§1.7).

* ``reveal`` / ``fill`` times are exactly the ones the stage computes without an event
  (``web/js/dynamic/state.js`` ``itemTimings``: ``max(0, beat.start - REVEAL_LEAD)`` and ``plan.fillAt``), so writing
  them moves nothing on screen; an item no beat reveals gets none.
* ``var`` maps B1's ``var:<n>`` sync part onto the §1.7 symbol target; ``draw`` follows the last symbol.
* ``cue_term`` marks a ``[[term]]`` on its first two narrated mentions, lesson-wide (R-35), inside R-26's budget.
* ``look_here`` comes from the pointing sync cues and from the R-16 hold walk.
* Only what the template consumes is written, every target is in the §1.7 grammar, and every event lies inside the
  scene (``TimedScene.check_inside``).
"""

from __future__ import annotations

import pytest

from aadhi.compose.dynamic import events as ev
from aadhi.compose.dynamic import templates as tp
from aadhi.schemas.screenplay import SceneLook
from aadhi.schemas.timeline import SyncCue, TimedScene

from .plan_factories import board_scene, item, screenplay, timed

ALL = tp.TEMPLATES
FORMULA = ALL["formula_focus"].consumes.accepts
LIST = ALL["list_build"].consumes.accepts
MEDIA = ALL["media_split"].consumes.accepts
BOARD = ALL["board_generic"].consumes.accepts


def kinds(events, kind):  # type: ignore[no-untyped-def]
    return [e for e in events if e.kind == kind]


def at(events, kind, target):  # type: ignore[no-untyped-def]
    return [round(e.t, 3) for e in events if e.kind == kind and e.target == target]


# --- reveals and fills ----------------------------------------------------------------------------------------------


def test_a_reveal_lands_120_ms_before_its_beat_and_is_anchored() -> None:
    """R-17 and state.js ``itemTimings``: the event time is ``max(0, beat.start - REVEAL_LEAD)``, so the stage draws
    the same entrance with the event as it did from the beat alone."""
    scene = board_scene("s1", [item("a", "bullet"), item("b", "bullet")],
                        beats=[("b0", "The first one.", "a"), ("b1", "And the second one.", "b")])
    t = timed(scene, beat_times=[(0.0, 3.0), (5.0, 8.0)])
    out = ev.scene_events(t, accepts=BOARD)
    assert at(out, "reveal", "a") == [0.0]  # clamped at the scene start
    assert at(out, "reveal", "b") == [round(5.0 - ev.REVEAL_LEAD, 3)]
    assert all(e.anchored for e in kinds(out, "reveal"))


def test_an_item_no_beat_reveals_gets_no_event() -> None:
    """The stage's staggered scene entrance (``INITIAL_DELAY`` / ``INITIAL_STAGGER``) stays its own."""
    scene = board_scene("s1", [item("a", "bullet"), item("b", "bullet")],
                        beats=[("b0", "Only the first one.", "a")])
    out = ev.scene_events(timed(scene), accepts=BOARD)
    assert at(out, "reveal", "a") == [0.0] and at(out, "reveal", "b") == []


def test_a_fill_lands_on_its_beat_and_only_where_consumed() -> None:
    scene = board_scene("s1", [item("st", "example_step", blank=True)],
                        beats=[("b0", "Here is the step.", "st"), ("b1", "Now we fill it in.", None)])
    t = timed(scene, beat_times=[(0.0, 3.0), (4.0, 7.0)])
    t.beats[1].fill_item_id = "st"
    assert at(ev.scene_events(t, accepts=BOARD), "fill", "st") == [4.0]  # plan.fillAt: the beat start, no lead
    assert kinds(ev.scene_events(t, accepts=LIST), "fill") == []  # list_build consumes no fill


# --- formula symbols and the box ------------------------------------------------------------------------------------


def _formula_scene(duration: float = 24.0, *, hold: str | None = None) -> TimedScene:
    scene = board_scene("s1", [item("f", "formula", variables=[("\\lambda", "wavelength"), ("f", "frequency")])],
                        beats=[("b0", "The wavelength is here.", "f"), ("b1", "Then frequency follows.", None)],
                        look=SceneLook(hold_reason=hold) if hold else None)
    return timed(scene, duration=duration,
                 sync=[SyncCue(kind="var", start=1.2, item_id="f", part="var:0", words="wavelength"),
                       SyncCue(kind="var", start=5.0, item_id="f", part="var:1", words="frequency")])


def test_a_var_names_the_symbol_and_keeps_the_sync_cue_timing() -> None:
    """Plan §1.7: B1's ``var:<n>`` becomes ``<item>:var:<symbol_latex>``, 120 ms before its word - the time
    ``formula_focus.variableTimes`` gives the sync cue it replaces, so the legend row appears when it did."""
    out = ev.scene_events(_formula_scene(), accepts=FORMULA)
    assert at(out, "var", "f:var:\\lambda") == [round(1.2 - ev.REVEAL_LEAD, 3)]
    assert at(out, "var", "f:var:f") == [round(5.0 - ev.REVEAL_LEAD, 3)]
    assert all(e.anchored and e.words for e in kinds(out, "var"))


def test_the_box_draws_after_the_last_named_symbol() -> None:
    out = ev.scene_events(_formula_scene(), accepts=FORMULA)
    assert at(out, "draw", "f") == [round(5.0 - ev.REVEAL_LEAD + ev.DRAW_AFTER_VAR, 3)]
    assert not kinds(out, "draw")[0].anchored  # the box is not itself a narrated moment (R-15)


def test_a_formula_with_no_named_symbol_gets_no_box_of_its_own() -> None:
    scene = board_scene("s1", [item("f", "formula", variables=[("\\lambda", "wavelength")])],
                        beats=[("b0", "Nothing is named.", "f")])
    assert kinds(ev.scene_events(timed(scene), accepts=FORMULA), "draw") == []


def test_a_var_cue_for_a_symbol_the_item_has_not_got_is_dropped() -> None:
    scene = board_scene("s1", [item("f", "formula", variables=[("\\lambda", "wavelength")])],
                        beats=[("b0", "The wavelength.", "f")])
    t = timed(scene, sync=[SyncCue(kind="var", start=1.0, item_id="f", part="var:7")])
    assert kinds(ev.scene_events(t, accepts=FORMULA), "var") == []


# --- key terms (R-35, R-26) -----------------------------------------------------------------------------------------


def test_term_phrases_index_every_rt_k_span_in_the_grammars_field_order() -> None:
    """Plan §1.7's ``<item>:term:<n>``: the terms of ``term``, ``text``, ``justification``, ``caption`` in reading
    order. ``**bold**`` is not a key term (the stage's ``.rt-b``), and a one-letter term cannot be heard."""
    one = item("d", "definition", term="[[wave speed]]", text="how fast a **wave** carries its [[energy]]")
    assert ev.term_phrases(one) == [(0, ["wave", "speed"]), (1, ["energy"])]
    assert ev.term_phrases(item("b", "bullet", text="only [[V]] here")) == []  # dropped, but still takes its ordinal
    assert ev.term_phrases(item("b", "bullet", text="[[V]] then [[energy]]")) == [(1, ["energy"])]


def test_a_term_is_marked_on_its_first_two_mentions_only() -> None:
    """R-35: the first two narrated mentions. The third is not marked, and the cues keep R-26's 2 s spacing."""
    scene = board_scene("s1", [item("b1", "bullet", text="The [[wave speed]] is constant"), item("b2", "bullet")],
                        beats=[("b0", "The wave speed is constant in a medium.", "b1"),
                               ("b1n", "Now once again we meet the wave speed, and later the wave speed once more.",
                                "b2")])
    out = ev.scene_events(timed(scene, duration=30.0), accepts=LIST)
    marks = kinds(out, "cue_term")
    assert len(marks) == ev.TERM_MENTIONS and {e.target for e in marks} == {"b1"}
    assert marks[1].t - marks[0].t >= ev.CUE_MIN_GAP


def test_the_term_budget_runs_over_the_whole_lesson() -> None:
    """One :class:`TermBudget` per video: a term marked twice in scene 1 is not marked again in scene 2 (R-35)."""
    first = board_scene("s1", [item("b1", "bullet", text="The [[wave speed]] matters"), item("b2", "bullet")],
                        beats=[("b0", "Today the wave speed matters a great deal to us.", "b1"),
                               ("b1n", "Once again we meet the wave speed in this medium.", "b2")])
    second = board_scene("s2", [item("c1", "bullet", text="The [[wave speed]] again"), item("c2", "bullet")],
                         beats=[("b0", "Today the wave speed comes back one more time.", "c1"),
                                ("b1n", "And so on.", "c2")])
    budget = ev.TermBudget()
    out1 = ev.scene_events(timed(first, duration=30.0), accepts=LIST, terms=budget)
    out2 = ev.scene_events(timed(second, duration=30.0), accepts=LIST, terms=budget)
    assert len(kinds(out1, "cue_term")) == 2 and kinds(out2, "cue_term") == []


def test_an_item_with_several_terms_names_the_one_the_cue_marks() -> None:
    """Plan §1.7: ``<item>`` marks every term of the item, so a cue on an item with several names its index."""
    scene = board_scene("s1", [item("b1", "bullet", text="[[wave speed]] and [[frequency]] differ"),
                               item("b2", "bullet")],
                        beats=[("b0", "Wave speed and frequency differ a lot.", "b1"), ("b1n", "More.", "b2")])
    out = ev.scene_events(timed(scene, duration=24.0), accepts=LIST)
    assert {e.target for e in kinds(out, "cue_term")} <= {"b1:term:0", "b1:term:1"}


def test_a_mention_before_its_item_is_on_screen_is_skipped() -> None:
    scene = board_scene("s1", [item("a", "bullet"), item("b", "bullet", text="the [[wave speed]]")],
                        beats=[("b0", "Wave speed comes up early.", "a"), ("b1", "And here it is.", "b")])
    out = ev.scene_events(timed(scene, beat_times=[(0.0, 3.0), (6.0, 9.0)], duration=20.0), accepts=LIST)
    assert all(e.t >= 6.0 for e in kinds(out, "cue_term"))


def test_r26_keeps_at_most_two_cues_on_screen_and_one_new_cue_per_two_seconds() -> None:
    narration = "Wave speed then frequency then wave speed then frequency then wave speed again."
    scene = board_scene("s1", [item("b1", "bullet", text="[[wave speed]] and [[frequency]]"), item("b2", "bullet")],
                        beats=[("b0", narration, "b1"), ("b1n", "Nothing more.", "b2")])
    out = ev.scene_events(timed(scene, duration=30.0), accepts=LIST)
    cues = sorted(kinds(out, "cue_term") + kinds(out, "look_here"), key=lambda e: e.t)
    assert all(b.t - a.t >= ev.CUE_MIN_GAP - 1e-9 for a, b in zip(cues, cues[1:], strict=False))
    for cue in cues:
        on = [c for c in cues if c.t <= cue.t < c.t + (c.dur if c.dur is not None else ev.CUE_HOLD)]
        assert len(on) <= ev.MAX_CONCURRENT_CUES


def test_the_same_target_is_never_cued_twice_within_four_seconds() -> None:
    scene = board_scene("s1", [item("b1", "bullet", text="The [[wave speed]] is here"), item("b2", "bullet")],
                        beats=[("b0", "Wave speed and wave speed right away again.", "b1"), ("b1n", "More.", "b2")])
    out = ev.scene_events(timed(scene, duration=24.0), accepts=LIST)
    times = at(out, "cue_term", "b1")
    assert all(b - a >= ev.CUE_REPEAT_GAP - 1e-9 for a, b in zip(times, times[1:], strict=False))


# --- look_here: pointing cues and the R-16 hold walk ----------------------------------------------------------------


def test_a_panel_focus_pulse_becomes_a_look_here_on_the_panel() -> None:
    scene = board_scene("s1", [item("h", "heading"), item("p", "paragraph")], panel="figure")
    t = timed(scene, sync=[SyncCue(kind="focus", start=3.0, end=4.6, words="look at the figure")])
    out = ev.scene_events(t, accepts=MEDIA)
    pulse = kinds(out, "look_here")
    assert [(e.target, e.t, e.dur) for e in pulse] == [("panel", 3.0, 1.6)] and pulse[0].anchored


def test_an_authored_highlight_becomes_a_look_here_on_its_item() -> None:
    scene = board_scene("s1", [item("b1", "bullet"), item("b2", "bullet")],
                        beats=[("b0", "The first one.", "b1"), ("b1n", "The second one.", "b2")])
    t = timed(scene, sync=[SyncCue(kind="emphasis", start=5.0, end=7.0, item_id="b1", beat_id="b1n")])
    assert at(ev.scene_events(t, accepts=LIST), "look_here", "b1") == [5.0]


def test_a_hold_walks_a_cue_through_the_content_r16() -> None:
    """R-16: a hold may last up to 12 s only while a cue moves. The walk starts after the narration, cycles through
    the formula's symbols, and is unanchored (it is not a narrated moment, §1.4)."""
    out = ev.scene_events(_formula_scene(duration=24.0), accepts=FORMULA, hold_reason="formula")
    walk = [e for e in kinds(out, "look_here") if not e.anchored]
    assert walk and [e.target for e in walk[:2]] == ["f:var:\\lambda", "f:var:f"]
    assert all(e.t >= max(b.end for b in _formula_scene().beats) for e in walk)
    gaps = [round(b.t - a.t, 3) for a, b in zip(walk, walk[1:], strict=False)]
    assert gaps and all(g >= ev.CUE_MIN_GAP for g in gaps)


def test_a_scene_without_a_hold_gets_no_walk() -> None:
    out = ev.scene_events(_formula_scene(duration=24.0), accepts=FORMULA)  # no hold_reason
    assert [e for e in kinds(out, "look_here") if not e.anchored] == []


def test_walk_targets_fall_back_to_the_items_a_template_resolves() -> None:
    scene = board_scene("s1", [item("b1", "bullet"), item("b2", "bullet")],
                        beats=[("b0", "One.", "b1"), ("b1n", "Two.", "b2")])
    t = timed(scene)
    assert ev.walk_targets(t, accepts=LIST) == ["b1", "b2"]
    assert ev.walk_targets(t, accepts=BOARD) == []  # board_generic resolves no cue, so a hold walks nothing


# --- the consumes gate, the grammar and the scene bounds ------------------------------------------------------------


def test_only_what_the_template_consumes_is_written() -> None:
    """Plan §1.6 / B2c review CG-5: the kind and the target's form. board_generic declares no cue, so it is never
    handed one; media_split declares no ``var``."""
    formula = _formula_scene()
    assert {e.kind for e in ev.scene_events(formula, accepts=BOARD, hold_reason="formula")} <= {"reveal", "fill"}
    media = ev.scene_events(formula, accepts=MEDIA, hold_reason="formula")
    assert {e.kind for e in media} == {"reveal", "look_here"}  # its hold walks the item, not the symbols
    assert {e.target for e in media if e.kind == "look_here"} == {"f"}


def test_an_item_called_panel_or_option_is_never_a_target() -> None:
    """Plan §1.7: ``panel`` and ``option`` are reserved words, so a board item with that id is not addressable."""
    scene = board_scene("s1", [item("panel", "bullet", text="the [[wave speed]]"), item("option", "bullet")],
                        beats=[("b0", "Wave speed here.", "panel"), ("b1n", "And more.", "option")])
    out = ev.scene_events(timed(scene, duration=24.0), accepts=LIST)
    assert {e.target for e in out} == set()


@pytest.mark.parametrize("accepts", [FORMULA, LIST, MEDIA, BOARD])
def test_every_target_is_in_the_grammar_and_the_events_fit_the_scene(accepts) -> None:  # type: ignore[no-untyped-def]
    for scene in (_formula_scene(), _panel_scene(), _list_scene()):
        out = ev.scene_events(scene, accepts=accepts, hold_reason="formula")
        for e in out:
            assert e.target is None or tp.parse_target(e.target).form in tp.TARGET_FORMS
            assert accepts(e.kind, e.target)
        assert out == sorted(out, key=lambda e: e.t)
        scene.events = out
        scene.check_inside()  # every event, and every dur, lies inside the scene


def _panel_scene() -> TimedScene:
    scene = board_scene("s2", [item("h", "heading"), item("p", "paragraph", text="the [[wave speed]] shown")],
                        panel="terminal", beats=[("b0", "Wave speed on the terminal.", "h"), ("b1n", "It prints.",
                                                                                              "p")])
    return timed(scene, duration=20.0,
                 sync=[SyncCue(kind="output", start=6.0, words="prints"),
                       SyncCue(kind="focus", start=9.0, end=10.6, words="look at this")])


def _list_scene() -> TimedScene:
    scene = board_scene("s3", [item("b1", "bullet", text="[[wave speed]] first"), item("b2", "bullet")],
                        beats=[("b0", "Wave speed first.", "b1"), ("b1n", "Then the rest.", "b2")])
    return timed(scene, duration=18.0)


def test_an_event_that_cannot_be_seen_is_dropped_and_a_long_dur_is_cut() -> None:
    scene = board_scene("s1", [item("h", "heading"), item("p", "paragraph")], panel="figure")
    t = timed(scene, duration=4.0, sync=[SyncCue(kind="focus", start=3.9, end=9.0),
                                         SyncCue(kind="focus", start=1.0, end=9.0)])
    out = ev.scene_events(t, accepts=MEDIA)
    pulses = kinds(out, "look_here")
    assert [(e.t, e.dur) for e in pulses] == [(1.0, 3.0)]  # 3.9 s leaves less than CUE_MIN_VISIBLE


def test_a_zero_length_scene_has_no_events() -> None:
    scene = board_scene("s1", [item("b1", "bullet"), item("b2", "bullet")])
    t = timed(scene, duration=20.0)
    t.duration = 0.0
    assert ev.scene_events(t, accepts=LIST) == []


def test_anchored_gaps_measure_the_narrated_moments() -> None:
    """R-15 counts anchored events only (§1.4), which is what the pacing lint reads."""
    out = ev.scene_events(_formula_scene(duration=24.0), accepts=FORMULA, hold_reason="formula")
    gaps = ev.anchored_gaps(out, 24.0)
    assert gaps and sum(gaps) == pytest.approx(24.0)
    assert len(gaps) == len([e for e in out if e.anchored]) + 1


def test_the_same_scene_always_gives_the_same_events() -> None:
    scene = _formula_scene()
    assert (ev.scene_events(scene, accepts=FORMULA, hold_reason="formula")
            == ev.scene_events(scene, accepts=FORMULA, hold_reason="formula"))


def test_the_screenplay_and_timed_scene_agree_on_the_board() -> None:
    """A guard on the factories: the events are generated from the timed scene, which carries the same board."""
    scene = board_scene("s1", [item("b1", "bullet"), item("b2", "bullet")])
    assert [i.id for i in timed(scene).board] == ["b1", "b2"]
    assert screenplay([scene]).presentation is not None


# --- step 5: a chart's marks on their beats, a code walk line by line ------------------------------------------------

CHART = ALL["chart_build"].consumes.accepts
CODE = ALL["code_walkthrough"].consumes.accepts
_POWER = {"kind": "chart", "title": "Power",
          "chart": {"chart_type": "bar", "labels": ["Fan", "AC", "Geyser"],
                    "datasets": [{"label": "Watts", "data": [60, 1500, 2000]}]}}


def _chart_scene(show_at: float = 0.0) -> TimedScene:
    scene = board_scene("s1", [item("h", "heading")],
                        beats=[("b0", "A fan needs little power.", None), ("b1", "The AC needs a lot.", None),
                               ("b2", "The geyser needs the most.", None), ("b3", "Look at the AC again.", None)])
    return timed(scene, beat_times=[(0.0, 3.0), (4.0, 7.0), (8.0, 11.0), (12.0, 15.0)],
                 panel={"panel": _POWER, "media": None, "show_at": show_at})


def test_a_chart_s_bar_is_drawn_on_the_word_that_names_it() -> None:
    """chart_build (§4.3): each bar is revealed on its first narrated mention, 120 ms early (R-17) and never before
    the panel shows; a later mention of the same bar is a ``look_here`` on it (R-26's budget applies)."""
    out = ev.scene_events(_chart_scene(), accepts=CHART)
    fan, ac, geyser = (0.4, 4.4, 8.4)  # plan_factories.words: 0.4 s a word from the beat's start
    assert at(out, "draw", "panel:bar:0:0") == [round(fan - ev.REVEAL_LEAD, 3)]
    assert at(out, "draw", "panel:bar:0:1") == [round(ac - ev.REVEAL_LEAD, 3)]
    assert at(out, "draw", "panel:bar:0:2") == [round(geyser - ev.REVEAL_LEAD, 3)]
    assert all(e.anchored for e in kinds(out, "draw"))
    assert at(out, "look_here", "panel:bar:0:1") == [13.2]  # "AC" again, on its word
    late = ev.scene_events(_chart_scene(show_at=5.0), accepts=CHART)
    assert at(late, "draw", "panel:bar:0:0") == []  # named only before the panel shows
    assert at(late, "draw", "panel:bar:0:1") == [round(13.2 - ev.REVEAL_LEAD, 3)]  # its first mention once shown
    assert at(late, "draw", "panel:bar:0:2") == [round(geyser - ev.REVEAL_LEAD, 3)]
    # a template that consumes neither kind on a mark gets none
    assert not [e for e in ev.scene_events(_chart_scene(), accepts=MEDIA) if (e.target or "").startswith("panel:bar")]


def test_a_code_hold_walks_the_program_line_by_line() -> None:
    """R-16 on code_walkthrough: the hold's look_here walk reads through the non-blank lines (``<item>:line:<n>``,
    the page's line indices), not the whole editor."""
    scene = board_scene("s1", [item("c", "code", code="x = 1\n\ny = x + 1\nprint(y)")],
                        beats=[("b0", "Here is the program.", "c")])
    t = timed(scene)
    assert ev.walk_targets(t, accepts=CODE) == ["c:line:0", "c:line:2", "c:line:3"]
    walked = [e.target for e in ev.scene_events(t, accepts=CODE, hold_reason="code") if e.kind == "look_here"]
    assert walked and set(walked) <= {"c:line:0", "c:line:2", "c:line:3"}
