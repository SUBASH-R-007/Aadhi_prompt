"""Word-anchored sync cues (aadhi.compose.sync) as built into the timeline."""

from __future__ import annotations

from typing import Any

import pytest

from aadhi.compose.sync import (
    FOCUS_SECONDS,
    MAX_AUTO_EMPHASIS_PER_BEAT,
    SyncOptions,
    explain_window,
    find_phrase,
    item_anchors,
    norm_token,
    stem,
    variable_phrases,
    word_stream,
)
from aadhi.compose.timeline import build_timeline, preview_timeline
from aadhi.config import Settings
from aadhi.pipeline.assets import scene_hash
from aadhi.schemas.screenplay import BoardItem, FormulaVariable, Screenplay
from aadhi.schemas.timeline import TimedBeat, TimedScene, TimedWord, Timeline

from .factories import beat, make_manifest, make_screenplay

LAW = "Newton's second law says force equals mass times acceleration."


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, render_fps=30, **kw)


def formula(**kw: Any) -> dict[str, Any]:
    return {"id": "f", "kind": "formula", "latex": "F = m a", "variables": [
        {"symbol_latex": "F", "meaning": "Force", "unit": "N"},
        {"symbol_latex": "m", "meaning": "Mass", "unit": "kg"},
        {"symbol_latex": "a", "meaning": "Acceleration", "unit": "m/s^2"}], **kw}


def build(scenes: list[dict[str, Any]], *, audio: bool = True, preview: bool = False, **kw: Any) -> Timeline:
    sp = Screenplay.model_validate({"subject_name": "Physics", "language": "en-IN", "scenes": scenes})
    manifest = make_manifest(sp, skip_audio=()) if audio else None
    fn = preview_timeline if preview else build_timeline
    return fn(sp, manifest, settings=settings(**kw), include_intro=False)


def board_scene(board: list[dict[str, Any]], beats: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    return {"id": "s1", "type": "content", "title": "Dynamics", "board": board, "beats": beats, **kw}


def word_at(scene: TimedScene, beat_id: str, text: str) -> float:
    b = next(b for b in scene.beats if b.beat_id == beat_id)
    return next(w.start for w in b.words if norm_token(w.text) == norm_token(text))


def cues(scene: TimedScene, kind: str) -> list[Any]:
    return [c for c in scene.sync_cues if c.kind == kind]


# --- formula legend rows ----------------------------------------------------------------------------


def test_legend_rows_appear_when_their_meanings_are_spoken_in_the_reveal_beat() -> None:
    s = build([board_scene([formula()], [beat("b1", LAW, board_item_id="f")])]).scenes[0]
    var = cues(s, "var")
    assert [(c.part, c.start) for c in var] == [
        ("var:0", word_at(s, "b1", "force")), ("var:1", word_at(s, "b1", "mass")),
        ("var:2", word_at(s, "b1", "acceleration"))]
    assert all(c.item_id == "f" and c.beat_id == "b1" for c in var)
    assert [c.words for c in var] == ["force", "mass", "acceleration"]
    assert all(c.start > s.beats[0].start for c in var)


def test_legend_rows_follow_the_narration_only_beats_that_explain_the_formula() -> None:
    s = build([board_scene([formula(), {"id": "p", "kind": "bullet", "text": "Mass resists change"}], [
        beat("b1", "Here is Newton's second law.", board_item_id="f"),
        beat("b2", "Force is mass times acceleration."),
        beat("b3", "Mass resists any change in motion.", board_item_id="p"),
    ])]).scenes[0]
    var = cues(s, "var")
    assert [(c.part, c.beat_id) for c in var] == [("var:0", "b2"), ("var:1", "b2"), ("var:2", "b2")]
    assert var[1].start == word_at(s, "b2", "mass")


def test_a_row_never_named_while_the_formula_is_explained_stays_with_the_formula() -> None:
    s = build([board_scene([formula(), {"id": "p", "kind": "bullet", "text": "Mass resists change"}], [
        beat("b1", "This law links force and acceleration.", board_item_id="f"),
        beat("b2", "Mass resists any change in motion.", board_item_id="p"),  # another reveal: window over
    ])]).scenes[0]
    assert [c.part for c in cues(s, "var")] == ["var:0", "var:2"]


def test_a_meaning_spoken_as_the_beat_starts_needs_no_cue() -> None:
    s = build([board_scene([formula()], [beat("b1", "Force is what we measure here.", board_item_id="f")])]).scenes[0]
    assert cues(s, "var") == []  # within a frame of the reveal: the row simply appears with the formula


def test_greek_symbol_names_and_plurals_name_a_row() -> None:
    item = {"id": "f", "kind": "formula", "latex": r"v = f \lambda", "variables": [
        {"symbol_latex": "v", "meaning": "Wave speed"}, {"symbol_latex": r"\lambda", "meaning": "Wavelength"},
        {"symbol_latex": "f", "meaning": "Frequency"}]}
    s = build([board_scene([item], [beat("b1", "The speed depends on lambda and on the frequencies we use.",
                                         board_item_id="f")])]).scenes[0]
    by_part = {c.part: c for c in cues(s, "var")}
    assert by_part["var:1"].words == "lambda"
    assert by_part["var:2"].words == "frequencies"
    assert by_part["var:0"].start == word_at(s, "b1", "speed")  # "wave" is not said; its first unique word is


def test_authored_beat_places_the_row_and_bad_anchors_are_ignored() -> None:
    board = [formula(variables=[
        {"symbol_latex": "F", "meaning": "Force", "beat_id": "b3"},
        {"symbol_latex": "m", "meaning": "Mass", "beat_id": "nope"},
        {"symbol_latex": "a", "meaning": "Acceleration", "beat_id": "b0"}]),
        {"id": "p", "kind": "bullet", "text": "Units"}]
    s = build([board_scene(board, [
        beat("b0", "First a word about units.", board_item_id="p"),
        beat("b1", "Here is the law with force mass and acceleration.", board_item_id="f"),
        beat("b2", "Let us pause on it."),
        beat("b3", "Notice how it scales."),
    ])]).scenes[0]
    by_part = {c.part: c for c in cues(s, "var")}
    assert by_part["var:0"].beat_id == "b3" and by_part["var:0"].start == s.beats[3].start  # not named in b3
    assert by_part["var:0"].words == ""
    assert by_part["var:1"].start == word_at(s, "b1", "mass")  # unknown beat: as if unset
    assert by_part["var:2"].start == word_at(s, "b1", "acceleration")  # beat before the reveal: ignored


def test_formula_on_the_board_from_the_start_is_explained_by_the_first_beats() -> None:
    s = build([board_scene([formula()], [beat("b1", "Read it as force equals mass times acceleration.")])]).scenes[0]
    assert [c.part for c in cues(s, "var")] == ["var:0", "var:1", "var:2"]


def test_estimated_words_place_the_rows_too() -> None:
    tl = build([board_scene([formula()], [beat("b1", LAW, board_item_id="f")])], audio=False, preview=True)
    s = tl.scenes[0]
    assert s.beats[0].estimated
    assert [c.part for c in cues(s, "var")] == ["var:0", "var:1", "var:2"]


# --- emphasis ------------------------------------------------------------------------------------


def test_authored_highlight_starts_when_the_item_is_named() -> None:
    board = [{"id": "p", "kind": "bullet", "text": "[[Voltage]] drives the current"}, formula()]
    s = build([board_scene(board, [
        beat("b1", "Voltage drives the current.", board_item_id="p"),
        # pause_after: the factory's 1 s beats would name it too late to re-time (MIN_EMPHASIS_SECONDS)
        beat("b2", "Here again, remember that voltage drives it.", board_item_id="f", highlight_item_ids=["p"],
             pause_after=1.0),
    ])]).scenes[0]
    (e,) = cues(s, "emphasis")
    assert (e.item_id, e.part, e.beat_id, e.words) == ("p", None, "b2", "voltage")
    assert e.start == word_at(s, "b2", "voltage") and e.end == s.beats[1].end


def test_highlight_not_named_keeps_its_beat_long_timing() -> None:
    board = [{"id": "p", "kind": "bullet", "text": "Voltage drives the current"}, formula()]
    s = build([board_scene(board, [
        beat("b1", "Voltage drives the current.", board_item_id="p"),
        beat("b2", "And now the second law of motion.", board_item_id="f", highlight_item_ids=["p"]),
    ])]).scenes[0]
    assert cues(s, "emphasis") == []


def test_named_table_columns_are_emphasised_one_at_a_time() -> None:
    table = {"id": "t", "kind": "table", "headers": ["Property", "Series", "Parallel"],
             "rows": [["Current", "Same", "Splits"]]}
    s = build([board_scene([table], [
        beat("b1", "Compare the two circuits.", board_item_id="t"),
        beat("b2", "In series the current is shared, in parallel it splits.", highlight_item_ids=["t"],
             pause_after=1.0),
    ])]).scenes[0]
    em = cues(s, "emphasis")
    assert [(c.part, c.words) for c in em] == [("column:1", "series"), ("column:2", "parallel")]
    assert em[0].end == em[1].start == word_at(s, "b2", "parallel")
    assert em[1].end == s.beats[1].end


def test_definition_term_is_emphasised_when_said() -> None:
    board = [{"id": "d", "kind": "definition", "term": "Inertia", "text": "Resistance to change"}, formula()]
    s = build([board_scene(board, [
        beat("b1", "This is a key idea.", board_item_id="d"),
        beat("b2", "Mass measures inertia, so the formula needs it.", board_item_id="f", highlight_item_ids=["d"],
             pause_after=1.0),
    ])]).scenes[0]
    (e,) = cues(s, "emphasis")
    assert (e.part, e.words) == ("term", "inertia")


def test_highlight_named_at_the_end_of_its_beat_stays_lit_from_the_beat_start() -> None:
    """Re-timing an item named in the last word of its beat would shrink the teacher's beat-long
    highlight to a flash: named less than MIN_EMPHASIS_SECONDS before the beat ends, it keeps its timing."""
    board = [{"id": "d", "kind": "definition", "term": "Inertia", "text": "Resistance to change"},
             {"id": "p", "kind": "bullet", "text": "[[Momentum]] is conserved"}, formula()]
    s = build([board_scene(board, [
        beat("b1", "Two ideas for today.", board_item_id="d"),
        beat("b2", "Next comes momentum.", board_item_id="p"),
        beat("b3", "Resisting change is what physicists call inertia.", board_item_id="f",
             highlight_item_ids=["d", "p"], pause_after=0.2),
        beat("b4", "Finally, this law also explains momentum.", highlight_item_ids=["p"], pause_after=0.2),
    ])]).scenes[0]
    b3, b4 = s.beats[2], s.beats[3]
    assert b3.end - word_at(s, "b3", "inertia") < 1.2 and b4.end - word_at(s, "b4", "momentum") < 1.2
    em = cues(s, "emphasis")
    # the term is still emphasised at its word, but the definition is lit from the beat start
    d = [c for c in em if c.item_id == "d"]
    assert [(c.part, c.start) for c in d] == [(None, b3.start), ("term", word_at(s, "b3", "inertia"))]
    assert d[0].end == d[1].start and d[1].end == b3.end
    assert not [c for c in em if c.item_id == "p"]  # plain items: no cue, the beat-long highlight stays


def test_highlight_lit_by_the_previous_beat_is_not_re_timed() -> None:
    """The same item highlighted in consecutive beats glows continuously: the second beat emits no cue
    that would switch it off until its word."""
    board = [{"id": "p", "kind": "bullet", "text": "[[Voltage]] drives the current"}]
    s = build([board_scene(board, [
        beat("b0", "Here is the first idea.", board_item_id="p"),
        beat("b1", "Remember that voltage pushes the charge.", highlight_item_ids=["p"], pause_after=1.0),
        beat("b2", "It keeps pushing, and that is what voltage does.", highlight_item_ids=["p"], pause_after=1.0),
    ])]).scenes[0]
    b1, b2 = s.beats[1], s.beats[2]
    em = cues(s, "emphasis")
    assert [(c.beat_id, c.start, c.end) for c in em] == [("b1", word_at(s, "b1", "voltage"), b1.end)]
    assert not [c for c in em if c.beat_id == "b2" and c.start > b2.start]


def test_auto_emphasis_is_off_by_default_and_never_overrides_authored_highlights() -> None:
    board = [{"id": "p", "kind": "bullet", "text": "[[Inertia]] resists change"},
             {"id": "q", "kind": "bullet", "text": "[[Momentum]] is conserved"},
             {"id": "r", "kind": "bullet", "text": "[[Energy]] too"}, formula()]
    beats = [beat("b1", "Inertia resists change.", board_item_id="p"),
             beat("b2", "Momentum is conserved.", board_item_id="q"),
             beat("b3", "Energy is conserved too.", board_item_id="r"),
             beat("b4", "Like inertia, then momentum and energy, the law fits.", board_item_id="f"),
             beat("b5", "Inertia again matters here.", highlight_item_ids=["q"])]
    assert cues(build([board_scene(board, beats)]).scenes[0], "emphasis") == []
    s = build([board_scene(board, beats)], sync_auto_emphasis=True).scenes[0]
    em = cues(s, "emphasis")
    b4 = [c for c in em if c.beat_id == "b4"]
    assert [c.item_id for c in b4] == ["p", "q"][:MAX_AUTO_EMPHASIS_PER_BEAT]  # at most two, one at a time
    assert b4[0].end == b4[1].start and b4[1].end == s.beats[3].end
    assert not [c for c in em if c.beat_id == "b5"]  # b5 has its own highlight: no automatic one


# --- side panel ----------------------------------------------------------------------------------


def terminal_scene(beats: list[dict[str, Any]], **panel: Any) -> dict[str, Any]:
    return board_scene([{"id": "c", "kind": "code", "language": "python", "code": "print(2 + 3)"}], beats,
                       side_panel={"kind": "terminal", "rationale": "run it",
                                   "terminal": {"command": "python add.py", "output": "5"}, **panel})


def test_terminal_output_starts_when_the_narration_says_what_it_prints() -> None:
    s = build([terminal_scene([
        beat("b1", "We print the sum here.", board_item_id="c"),
        beat("b2", "Run it and the program prints five."),
    ], show_from_beat_id="b2")]).scenes[0]
    (o,) = cues(s, "output")
    assert (o.start, o.beat_id, o.words) == (word_at(s, "b2", "prints"), "b2", "prints")  # b1 is before show_at


def test_code_describing_words_do_not_reveal_the_output_while_the_code_is_explained() -> None:
    """'return statement', 'print function' name the code, not its result: only result forms count, and
    without a show beat the code's reveal beat is skipped."""
    explain = beat("b1", "This function uses a return statement and the print function.", board_item_id="c")
    call = beat("b2", "Then we call it with two and three.")
    s = build([terminal_scene([explain, call, beat("b3", "Running it, the program prints five.")])]).scenes[0]
    (o,) = cues(s, "output")
    assert (o.beat_id, o.words) == ("b3", "prints")
    s = build([terminal_scene([explain, call])]).scenes[0]
    assert cues(s, "output") == []  # no result word: the lines keep their even spread
    early = beat("b1", "This function prints the sum of its inputs.", board_item_id="c")
    s = build([terminal_scene([early, call])]).scenes[0]
    assert cues(s, "output") == []  # said while the code is introduced (no show beat): not the result yet


def test_terminal_without_an_output_word_keeps_spreading_its_lines() -> None:
    s = build([terminal_scene([beat("b1", "Here is the code we wrote.", board_item_id="c")])]).scenes[0]
    assert s.sync_cues == []


def figure_scene(beats: list[dict[str, Any]], kind: str = "chart") -> dict[str, Any]:
    panel: dict[str, Any] = {"kind": kind, "rationale": "data"}
    if kind == "chart":
        panel["chart"] = {"labels": ["a", "b"], "datasets": [{"label": "x", "data": [1, 2]}]}
    else:
        panel["terminal"] = {"command": "ls", "output": "a"}
    return {"id": "s1", "type": "content", "title": "Data", "board": [], "beats": beats, "side_panel": panel}


def test_focus_pulses_when_the_narration_points_at_the_visual() -> None:
    s = build([figure_scene([
        beat("b1", "The graph rises steadily."),  # a noun without a pointing word: no pulse
        beat("b2", "Now look at this chart on the right."),
        beat("b3", "See the chart again, it peaks."),  # too soon after the first pulse
        beat("b4", "Take your time."),
        beat("b5", "Pause."),
        beat("b6", "Here the graph flattens out."),
        beat("b7", "Look at the graph once more."),  # third pulse: over the cap
    ])]).scenes[0]
    focus = cues(s, "focus")
    assert [(c.beat_id, c.words) for c in focus] == [("b2", "look at this chart"), ("b6", "Here the graph")]
    assert focus[0].start == word_at(s, "b2", "look")
    assert focus[0].end == pytest.approx(focus[0].start + FOCUS_SECONDS, abs=1e-3)


@pytest.mark.parametrize("text", [
    "This function maps every input to exactly one output.",
    "This models the population over time.",
    "Here we map each key to a value.",
])
def test_verbs_that_look_like_visual_nouns_do_not_pulse(text: str) -> None:
    s = build([figure_scene([beat("b1", text), beat("b2", "Pause."),
                             beat("b3", "Now look at this chart on the right.")])]).scenes[0]
    assert [(c.beat_id, c.words) for c in cues(s, "focus")] == [("b3", "look at this chart")]


def test_a_plural_visual_noun_after_a_plural_determiner_still_pulses() -> None:
    s = build([figure_scene([beat("b1", "Look at these graphs.")])]).scenes[0]
    assert [(c.beat_id, c.words) for c in cues(s, "focus")] == [("b1", "Look at these graphs")]


def test_terminal_panels_never_pulse() -> None:
    s = build([figure_scene([beat("b1", "Now look at this graph of the output.")], kind="terminal")]).scenes[0]
    assert cues(s, "focus") == []


# --- switches, serialisation, determinism -------------------------------------------------------


def test_switching_word_anchors_off_restores_beat_timing() -> None:
    s = build([board_scene([formula()], [beat("b1", LAW, board_item_id="f")])], sync_word_anchors=False).scenes[0]
    assert s.sync_cues == []
    assert SyncOptions.from_settings(settings(sync_word_anchors=False)) is None


def test_timelines_without_cues_serialise_exactly_as_before() -> None:
    sp = make_screenplay()
    tl = build_timeline(sp, make_manifest(sp), settings=settings())
    assert all(not s.sync_cues for s in tl.scenes)
    assert all("sync_cues" not in s for s in tl.model_dump(mode="json")["scenes"])


def test_cues_round_trip_and_build_deterministically() -> None:
    scenes = [board_scene([formula()], [beat("b1", LAW, board_item_id="f")])]
    a, b = build(scenes), build(scenes)
    assert a.model_dump(mode="json") == b.model_dump(mode="json")
    assert Timeline.model_validate(a.model_dump(mode="json")) == a
    assert "sync_cues" in a.model_dump(mode="json")["scenes"][0]


def test_cue_times_closer_than_a_frame_merge() -> None:
    words = [TimedWord(text=t, start=s, end=s + 0.01) for t, s in
             (("so", 1.0), ("force", 1.2), ("mass", 1.21), ("acceleration", 1.5))]
    tb = TimedBeat(beat_id="b1", index=0, start=1.0, speech_end=2.0, end=2.0, narration="x",
                   board_item_id="f", words=words)
    from aadhi.compose.sync import scene_sync_cues

    out = scene_sync_cues([tb], [BoardItem.model_validate(formula())], None, 3.0, SyncOptions(fps=30))
    assert [c.start for c in out] == [1.2, 1.2, 1.5]


def test_formula_variable_beat_is_left_out_of_dumps_so_scene_hashes_do_not_change() -> None:
    assert "beat_id" not in FormulaVariable(symbol_latex="F", meaning="Force").model_dump()
    assert FormulaVariable(symbol_latex="F", meaning="Force", beat_id="B 2").model_dump()["beat_id"] == "b-2"
    sp = Screenplay.model_validate({"scenes": [board_scene([formula()], [beat("b1", LAW, board_item_id="f")])]})
    dumped = sp.scenes[0].model_dump(mode="json")
    assert dumped["board"][0]["variables"][0] == {"symbol_latex": "F", "meaning": "Force", "unit": "N"}
    authored = Screenplay.model_validate({"scenes": [board_scene([formula(variables=[
        {"symbol_latex": "F", "meaning": "Force", "unit": "N", "beat_id": "b1"}])], [beat("b1", LAW)])]})
    assert scene_hash(authored.scenes[0]) != scene_hash(sp.scenes[0])  # a set anchor is content


# --- matching helpers ----------------------------------------------------------------------------


def test_tokens_keep_indic_marks_and_match_exactly() -> None:
    assert norm_token("விசை,") == "விசை"
    assert stem("forces") == "force" and stem("processes") == "process" and stem("batteries") == "battery"
    assert stem("gas") == "gas" and stem("விசைகள்") == "விசைகள்"
    words = [TimedWord(text=t, start=i * 0.2, end=i * 0.2 + 0.1) for i, t in enumerate(["இது", "விசை", "ஆகும்"])]
    stream = word_stream([TimedBeat(beat_id="b", index=0, start=0, speech_end=1, end=1, narration="x", words=words)])
    assert find_phrase(stream, ["விசை"], {0}) == 1
    assert find_phrase(stream, ["விசைகள்"], {0}) is None


def test_anchor_extraction() -> None:
    bullet = BoardItem(id="p", kind="bullet", text="The **net force** on a body")
    assert item_anchors(bullet, explicit_only=True) == [(None, ["net", "force"])]
    plain = BoardItem(id="q", kind="bullet", text="The resistance of a wire")
    assert item_anchors(plain, explicit_only=True) == []
    assert item_anchors(plain, explicit_only=False) == [(None, ["resistance"])]
    gravity = BoardItem.model_validate({"id": "g", "kind": "formula", "latex": "F = G m_1 m_2 / r^2", "variables": [
        {"symbol_latex": "m_1", "meaning": "mass of the first body"},
        {"symbol_latex": "m_2", "meaning": "mass of the second body"}]})
    assert variable_phrases(gravity) == [[["first"]], [["second"]]]
    beats = [TimedBeat(beat_id=f"b{i}", index=i, start=i, speech_end=i + 0.5, end=i + 0.5, narration="x",
                       board_item_id=r) for i, r in enumerate(["x", "f", None, None, "y", None])]
    assert explain_window(beats, 1) == [1, 2, 3]
    assert explain_window(beats, None) == []  # on the board from the start, but the first beat moves on
    assert explain_window(beats[2:], None) == [0, 1]


def test_one_letter_headers_and_indic_function_words_are_not_anchors() -> None:
    from aadhi.compose.sync import column_phrases

    assert column_phrases(["A", "B", "A AND B"]) == [[], [], ["a", "and", "b"]]
    truth = {"id": "t", "kind": "table", "headers": ["A", "B", "A AND B"], "rows": [["0", "0", "0"], ["1", "1", "1"]]}
    s = build([board_scene([truth], [
        beat("b1", "Here is the truth table.", board_item_id="t"),
        beat("b2", "The output is true only in a row where both inputs are true.", highlight_item_ids=["t"],
             pause_after=1.0),
    ])]).scenes[0]
    assert cues(s, "emphasis") == []  # the article "a" never lights column A
    s = build([board_scene([truth], [beat("b1", "Here is the truth table.", board_item_id="t"),
                                     beat("b2", "A row is a case, and a column is an input.")])],
              sync_auto_emphasis=True).scenes[0]
    assert cues(s, "emphasis") == []
    hindi = BoardItem(id="p", kind="bullet", text="यह ओम का नियम है")
    assert item_anchors(hindi, explicit_only=False) == []
    marked = BoardItem(id="q", kind="bullet", text="[[ओम का नियम]] यह है")
    assert item_anchors(marked, explicit_only=False) == [(None, ["ओम", "का", "नियम"])]  # explicit anchors still work
    one_letter_term = BoardItem.model_validate({"id": "d", "kind": "definition", "term": "V", "text": "Voltage"})
    assert ("term", ["v"]) not in item_anchors(one_letter_term, explicit_only=True)


def test_cue_count_per_scene_is_bounded() -> None:
    from aadhi.compose.sync import MAX_CUES_PER_SCENE, scene_sync_cues

    meanings = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]
    board = [BoardItem.model_validate({"id": f"f{k}", "kind": "formula", "latex": "x", "variables": [
        {"symbol_latex": "x", "meaning": f"{m}{k}"} for m in meanings]}) for k in range(12)]
    beats, t = [], 0.0
    for k in range(12):
        words = [TimedWord(text="so", start=t, end=t + 0.1)]
        words += [TimedWord(text=f"{m}{k}", start=t + 0.2 * (j + 1), end=t + 0.2 * (j + 1) + 0.1)
                  for j, m in enumerate(meanings)]
        beats.append(TimedBeat(beat_id=f"b{k}", index=k, start=t, speech_end=t + 2.0, end=t + 2.0, narration="x",
                               board_item_id=f"f{k}", words=words))
        t += 2.2
    out = scene_sync_cues(beats, board, None, t + 1, SyncOptions())
    assert len(out) == MAX_CUES_PER_SCENE
    assert [c.start for c in out] == sorted(c.start for c in out)
