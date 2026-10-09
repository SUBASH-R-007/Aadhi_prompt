"""aadhi.pipeline.quality: the deterministic lesson quality and consistency checks (lint family)."""

from __future__ import annotations

import copy
import re
import time
from pathlib import Path
from typing import Any

import pytest

from aadhi.pipeline.base import GenerationOptions
from aadhi.pipeline.quality import AUTHOR_CODES, QUALITY_CODES, lint_quality, quality_report
from aadhi.pipeline.quality import code as quality_code
from aadhi.pipeline.quality.abbreviations import words_before
from aadhi.pipeline.quality.formulas import base_letters
from aadhi.pipeline.quality.text import group_key, loose, sep_sig
from aadhi.pipeline.quality.titles import title_casing
from aadhi.pipeline.validate import lint, scenes_needing_repair
from aadhi.schemas.screenplay import Screenplay

ROOT = Path(__file__).resolve().parents[2]


def item(iid: str, kind: str = "bullet", **kw: Any) -> dict[str, Any]:
    return {"id": iid, "kind": kind, **kw}


def scene(sid: str, title: str = "", board: list[dict[str, Any]] | None = None, narration: str = "This is a short clean beat.",
          **kw: Any) -> dict[str, Any]:
    board = board if board is not None else [item(f"{sid}-i1", text="one point")]
    beats = [{"id": f"{sid}-b1", "narration": narration, **({"board_item_id": board[0]["id"]} if board else {})}]
    return {"id": sid, "type": "content", "title": title, "board": board, "beats": beats, **kw}


def bullets(sid: str, *texts: str) -> list[dict[str, Any]]:
    return [item(f"{sid}-i{n}", text=t) for n, t in enumerate(texts, 1)]


def sp(scenes: list[dict[str, Any]], **kw: Any) -> Screenplay:
    return Screenplay.model_validate({"scenes": scenes, **kw})


def found(screenplay: Screenplay, code: str | None = None) -> list[Any]:
    issues = lint_quality(screenplay)
    return [i for i in issues if code is None or i.code == code]


# ---------------------------------------------------------------------------
# Normalisation helpers (ported behaviour)
# ---------------------------------------------------------------------------

def test_group_key_separators_and_loose_casing():
    assert group_key("Machine-Learning") == group_key("machine learning") == group_key("machine_learnings")
    assert sep_sig("machine-learning") != sep_sig("machine learning")
    assert loose("Machine learning", False) == loose("machine learning", False)  # the first letter never counts
    assert loose("Speed of Light", True) == loose("speed of light", True)  # Title Case in a heading
    assert loose("SPEED OF LIGHT", True) == "*"  # a heading in capitals matches any spelling
    assert loose("Machine Learning", False) != loose("machine learning", False)


# ---------------------------------------------------------------------------
# Terminology
# ---------------------------------------------------------------------------

def test_casing_of_a_keyword_is_a_note_naming_both_scenes_with_a_repair():
    lesson = sp([
        scene("s1", "Learning from data", bullets("s1", "Today we meet [[Machine Learning]] and its uses.")),
        scene("s2", "Models", bullets("s2", "In practice, [[machine learning]] builds models.")),
        scene("s3", "Data", bullets("s3", "Again **machine learning** finds patterns.")),
    ])
    hits = found(lesson, "terminology.casing")
    assert len(hits) == 1
    issue = hits[0]
    assert (issue.severity, issue.fixable, issue.source, issue.scene_id) == ("info", False, "lint", "s1")
    assert "scene 1 (“Learning from data”)" in issue.message and "scenes 2 and 3" in issue.message
    repair = next(r for r in quality_report(lesson)["repairs"] if r["code"] == "terminology.casing")
    assert repair["message"] == issue.message and repair["scene_id"] == "s1"
    assert repair["edits"] == [{"scene_id": "s1", "path": ["board", 0, "text"],
                                "before": "Today we meet [[Machine Learning]] and its uses.",
                                "after": "Today we meet [[Machine learning]] and its uses."}]


def test_sentence_start_title_case_headers_and_headings_are_not_casing_problems():
    lesson = sp([
        scene("s1", "Plants", bullets("s1", "**Photosynthesis** makes sugar.")),
        scene("s2", "Leaves", bullets("s2", "Leaves use **photosynthesis** every day.")),
        scene("s3", "Two ways", [item("s3-i1", "table", headers=["Machine Learning", "Fixed Rules"], rows=[["learns", "fixed"]])]),
        scene("s4", "Models", bullets("s4", "Here [[machine learning]] adapts.")),
        scene("s5", "Light", [item("s5-i1", "heading", text="Speed of Light"),
                              item("s5-i2", text="Nothing beats the **speed of light**.")]),
        scene("s6", "Caps", [item("s6-i1", "heading", text="MACHINE LEARNING TODAY")]),
    ])
    assert found(lesson, "terminology.casing") == []
    assert found(lesson, "terminology.variant") == []


def test_hyphen_and_space_variants_are_one_finding_and_the_repair_fixes_both_spellings():
    lesson = sp([
        scene("s1", "Intro", bullets("s1", "We study [[Machine-Learning]] today.")),
        scene("s2", "More", bullets("s2", "Then [[machine learning]] again.")),
        scene("s3", "Even more", bullets("s3", "Again [[machine learning]] here.")),
    ])
    assert found(lesson, "terminology.casing") == []  # one cause, one finding
    hits = found(lesson, "terminology.variant")
    assert len(hits) == 1 and hits[0].scene_id == "s1" and hits[0].severity == "info"
    edits = next(r for r in quality_report(lesson)["repairs"] if r["code"] == "terminology.variant")["edits"]
    assert [e["after"] for e in edits] == ["We study [[Machine learning]] today."]


def test_plurals_are_one_term():
    lesson = sp([
        scene("s1", "Cells", bullets("s1", "The [[chloroplasts]] are green.")),
        scene("s2", "Cell", bullets("s2", "One [[chloroplast]] is small.")),
    ])
    assert found(lesson) == []
    terms = quality_report(lesson)["registry"]["terms"]
    group = next(t for t in terms if t["key"] == "chloroplast")
    assert {f["form"]: f["scene_ids"] for f in group["forms"]} == {"chloroplasts": ["s1"], "chloroplast": ["s2"]}


def test_repairs_never_touch_code_or_math_spans():
    lesson = sp([
        scene("s1", "One", bullets("s1", "Use [[Ohm's Law]] and `Ohm's law` with $Ohm's law$ here.")),
        scene("s2", "Two", bullets("s2", "Then [[Ohm's law]] again.")),
        scene("s3", "Three", bullets("s3", "And [[Ohm's law]] once more.")),
    ])
    repair = next(r for r in quality_report(lesson)["repairs"] if r["code"] == "terminology.casing")
    assert repair["edits"][0]["after"] == "Use [[Ohm's law]] and `Ohm's law` with $Ohm's law$ here."


def test_definition_terms_count_as_emphasised_places():
    lesson = sp([
        scene("s1", "Definition", [item("s1-i1", "definition", term="Ohm's Law", text="V equals I times R.")]),
        scene("s2", "Use", bullets("s2", "By [[Ohm's law]] we find the current.")),
        scene("s3", "Again", bullets("s3", "So [[Ohm's law]] gives the voltage.")),
    ])
    hits = found(lesson, "terminology.casing")
    assert len(hits) == 1 and hits[0].scene_id == "s1"
    edit = next(r for r in quality_report(lesson)["repairs"] if r["code"] == "terminology.casing")["edits"][0]
    assert edit["path"] == ["board", 0, "term"] and edit["after"] == "Ohm's law"


# ---------------------------------------------------------------------------
# Abbreviations
# ---------------------------------------------------------------------------

def test_an_abbreviation_never_spelled_out_is_a_note():
    lesson = sp([scene("s1", "Photos", bullets("s1", "Computers sort photos.")),
                 scene("s2", "Sorting", bullets("s2", "We use ML to sort photos."))])
    hits = found(lesson, "terminology.abbreviation_undefined")
    assert [(i.scene_id, i.severity, i.fixable) for i in hits] == [("s2", "info", False)]
    assert "“ML”" in hits[0].message and "scene 2 (“Sorting”)" in hits[0].message
    entry = next(a for a in quality_report(lesson)["registry"]["abbreviations"] if a["abbreviation"] == "ML")
    assert entry == {"abbreviation": "ML", "expansion": None, "how": None, "ways": 0, "scene_ids": ["s2"]}


@pytest.mark.parametrize("scenes", [
    [scene("s1", "Intro", bullets("s1", "Machine Learning (ML) finds patterns.")), scene("s2", "Use", bullets("s2", "ML sorts photos."))],
    [scene("s1", "Intro", bullets("s1", "ML (machine learning) finds patterns.")), scene("s2", "Use", bullets("s2", "ML sorts photos."))],
    [scene("s1", "Intro", bullets("s1", "ML sorts photos."), narration="ML stands for machine learning, which finds patterns."),
     scene("s2", "Use", bullets("s2", "ML sorts photos."))],
    [scene("s1", "Intro", bullets("s1", "We meet [[machine learning]].")), scene("s2", "Use", bullets("s2", "ML sorts photos."))],
    [scene("s1", "Intro", [item("s1-i1", "definition", term="ML", text="Machine learning: programs that learn from data.")]),
     scene("s2", "Use", bullets("s2", "ML sorts photos."))],
    [scene("s1", "Note", bullets("s1", "NOTE: chapter II says CO2 is OK. KEY TAKEAWAYS FOR TODAY. AC and DC."))],
    [scene("s1", "Code", bullets("s1", "Call `HTTP_GET` or `SQL` and $AB$ here."))],
    # words in capitals for emphasis, and titles set in capitals, are style, not abbreviations
    [scene("s1", "VOLTAGE AND CURRENT", bullets("s1", "The SAME current, more ENERGY."), narration="Energy is the same."),
     scene("s2", "RESISTANCE - THE NARROW PIPE", bullets("s2", "HIGH resistance means LESS current."))],
])
def test_spelled_out_in_every_accepted_way_is_silent(scenes):
    lesson = sp(scenes)
    codes = {i.code for i in found(lesson)}
    assert not codes & {"terminology.abbreviation_undefined", "terminology.abbreviation_conflict",
                        "terminology.abbreviation_late"}


def test_an_abbreviation_spelled_out_two_ways_is_a_warning():
    lesson = sp([
        scene("s1", "Intro", bullets("s1", "Machine Learning (ML) finds patterns.")),
        scene("s2", "Use", bullets("s2", "ML sorts photos.")),
        scene("s3", "Statistics", bullets("s3", "Maximum Likelihood (ML) estimates a value.")),
    ])
    hits = found(lesson, "terminology.abbreviation_conflict")
    assert [(i.severity, i.scene_id, i.fixable) for i in hits] == [("warning", "s3", False)]
    assert "Machine Learning" in hits[0].message and "Maximum Likelihood" in hits[0].message
    assert scenes_needing_repair(lint(lesson)) == {}  # a lint warning never pays for a rewrite


def test_spelled_out_late_is_a_note_with_a_first_use_repair():
    lesson = sp([
        scene("s1", "Models", bullets("s1", "Our models use **ML** and `ML` in code.")),
        scene("s2", "Definition", bullets("s2", "Machine Learning (ML) finds patterns.")),
    ])
    hits = found(lesson, "terminology.abbreviation_late")
    assert [(i.scene_id, i.severity) for i in hits] == [("s1", "info")]
    repair = next(r for r in quality_report(lesson)["repairs"] if r["code"] == "terminology.abbreviation_late")
    assert repair["edits"] == [{"scene_id": "s1", "path": ["board", 0, "text"],
                                "before": "Our models use **ML** and `ML` in code.",
                                "after": "Our models use **Machine Learning (ML)** and `ML` in code."}]
    assert repair["label"].startswith("Spell it out here")


def test_the_first_use_repair_skips_a_heading_set_in_capitals():
    """Capital runs are style, not uses (detection skips them): the repair spells it out in the bullet."""
    lesson = sp([
        scene("s1", "Why it matters", [item("s1-h", "heading", text="WHY ML MATTERS"), item("s1-i", text="ML helps.")]),
        scene("s2", "Definition", bullets("s2", "Machine learning (ML) is everywhere.")),
    ])
    repair = next(r for r in quality_report(lesson)["repairs"] if r["code"] == "terminology.abbreviation_late")
    assert repair["edits"] == [{"scene_id": "s1", "path": ["board", 1, "text"], "before": "ML helps.",
                                "after": "Machine learning (ML) helps."}]
    lesson = sp([
        scene("s1", "Why it matters", bullets("s1", "WHY ML MATTERS: ML helps.")),
        scene("s2", "Definition", bullets("s2", "Machine learning (ML) is everywhere.")),
    ])
    repair = next(r for r in quality_report(lesson)["repairs"] if r["code"] == "terminology.abbreviation_late")
    assert [e["after"] for e in repair["edits"]] == ["WHY ML MATTERS: Machine learning (ML) helps."]


def test_words_before_reads_a_bounded_window():
    text = "We use the Bipolar Junction Transistor (BJT) here"
    assert words_before(text, text.index("(")) == ["We", "use", "the", "Bipolar", "Junction", "Transistor"][-8:]
    assert words_before("x" * 20000 + " (AB)", 20001) == []  # a 20,000-character word is not words


# ---------------------------------------------------------------------------
# Concepts and figures
# ---------------------------------------------------------------------------

def test_one_concept_titled_two_ways_is_a_note():
    lesson = sp([
        scene("s1", "Machine Learning", concept_id="ml"),
        scene("s2", "Machine-learning (contd.)", concept_id="ml"),
    ], concept_map=[{"id": "ml", "title": "Machine Learning"}])
    hits = found(lesson, "concept.naming_variant")
    assert [(i.scene_id, i.severity) for i in hits] == [("s2", "info")]
    assert "the concept map calls it" in hits[0].message


def test_the_same_title_without_a_concept_id_and_sub_titles():
    assert len(found(sp([scene("s1", "Machine Learning"), scene("s2", "Machine-learning (contd.)")]),
                     "concept.naming_variant")) == 1
    assert found(sp([scene("s1", "Machine Learning"), scene("s2", "Machine learning (contd.)")]), "concept.naming_variant") == []
    lesson = sp([scene("s1", "Ohm's law", concept_id="ohm"), scene("s2", "Ohm's law in practice", concept_id="ohm")],
                concept_map=[{"id": "ohm", "title": "Ohm's law"}])
    assert found(lesson, "concept.naming_variant") == []  # a different name is never compared


def test_one_figure_with_different_captions_is_a_note():
    lesson = sp([
        scene("s1", "Symbol", [item("s1-i1", "figure", figure_id="fig-1", caption="The BJT symbol")]),
        scene("s2", "Again", [item("s2-i1", "figure", figure_id="fig-1", caption="Current flow in a resistor")]),
        scene("s3", "Same", [item("s3-i1", "figure", figure_id="fig-1", caption="The BJT symbol")]),
    ], figures=[{"id": "fig-1", "caption": "c"}])
    hits = found(lesson, "figure.caption_variant")
    assert [(i.scene_id, i.severity) for i in hits] == [("s2", "info")]
    captions = quality_report(lesson)["registry"]["figures"][0]["captions"]
    assert captions == [{"caption": "The BJT symbol", "scene_ids": ["s1", "s3"]},
                        {"caption": "Current flow in a resistor", "scene_ids": ["s2"]}]


# ---------------------------------------------------------------------------
# Formulas
# ---------------------------------------------------------------------------

def formula(iid: str, latex: str, *variables: tuple[str, str], unit: str = "", text: str = "") -> dict[str, Any]:
    return item(iid, "formula", latex=latex, text=text,
                variables=[{"symbol_latex": s, "meaning": m, "unit": unit} for s, m in variables])


def test_one_symbol_with_two_meanings_is_a_warning_and_contained_meanings_are_one():
    lesson = sp([
        scene("s1", "Speed", [formula("s1-f", "v = s/t", ("v", "velocity"), ("s", "distance"), ("t", "time"))]),
        scene("s2", "Density", [formula("s2-f", "k = m/v", ("k", "density"), ("m", "mass"), ("v", "volume"))]),
        scene("s3", "Ball", [formula("s3-f", "p = mv", ("p", "momentum"), ("m", "mass of the ball"), ("v", "velocity"))]),
    ])
    hits = found(lesson, "formula.symbol_conflict")
    assert [(i.scene_id, i.severity, i.fixable) for i in hits] == [("s2", "warning", False)]
    assert "“velocity”" in hits[0].message and "“volume”" in hits[0].message
    symbols = {s["symbol"]: s for s in quality_report(lesson)["registry"]["symbols"]}
    assert [m["meaning"] for m in symbols["m"]["meanings"]] == ["mass", "mass of the ball"]


def test_two_symbols_for_one_quantity_and_units_never_compared():
    lesson = sp([
        scene("s1", "Speed", [formula("s1-f", "v = s/t", ("v", "velocity"), ("s", "distance"), ("t", "time"))]),
        scene("s2", "Again", [formula("s2-f", "V = s/t", ("V", "velocity"), ("s", "distance"), ("t", "time"))]),
        scene("s3", "Volt", [formula("s3-f", "P = VI", ("P", "power"), ("V", "voltage"), ("I", "current"), unit="V")]),
    ])
    mismatch = found(lesson, "formula.notation_mismatch")
    assert [(i.scene_id, i.severity) for i in mismatch] == [("s2", "info")]
    conflicts = found(lesson, "formula.symbol_conflict")
    assert [i.scene_id for i in conflicts] == ["s3"]  # "V" as velocity and as voltage (units ignored)


def test_formula_legend_missing_unless_explained():
    unexplained = sp([scene("s1", "Energy", [formula("s1-f", "E = mc^2")], narration="Energy depends on mass.")])
    hits = found(unexplained, "formula.legend_missing")
    assert [(i.scene_id, i.severity) for i in hits] == [("s1", "info")]
    explained = sp([scene("s1", "Energy", [formula("s1-f", "E = mc^2")],
                          narration="Here E is energy, m is mass and c is the speed of light.")])
    assert found(explained, "formula.legend_missing") == []
    by_legend = sp([scene("s1", "Legend", [formula("s1-f", "E = mc^2", ("E", "energy"), ("m", "mass"), ("c", "speed of light"))]),
                    scene("s2", "Again", [formula("s2-f", "E = mc^2")])])
    assert found(by_legend, "formula.legend_missing") == []
    one_symbol = sp([scene("s1", "Value", [formula("s1-f", "R = 10\\,\\Omega")])])
    assert found(one_symbol, "formula.legend_missing") == []


def test_base_letters_ignore_commands_text_and_scripts():
    assert base_letters(r"V_{in} = \frac{R_2}{R_1 + R_2} V_{s} + \sin(\omega t) \text{ volts}") == ["\\omega", "V", "R", "t"]
    assert base_letters(r"\frac{dV}{dt}") == ["V", "t"]


# ---------------------------------------------------------------------------
# Code
# ---------------------------------------------------------------------------

def code_item(iid: str, code: str, language: str | None = "python") -> dict[str, Any]:
    return item(iid, "code", code=code, language=language)


def test_a_language_the_player_cannot_colour_is_a_warning_never_handed_to_a_rewrite():
    lesson = sp([scene("s1", "Rust", [code_item("s1-c", "fn main() {\n    let x = 1;\n}", "rust")])])
    hits = found(lesson, "code.language_unhighlighted")
    assert [(i.scene_id, i.severity, i.fixable) for i in hits] == [("s1", "warning", False)]
    assert "Rust" in hits[0].message and "without colours" in hits[0].message
    assert "code.language_unhighlighted" in AUTHOR_CODES


def test_coloured_and_plain_code_is_silent():
    lesson = sp([scene("s1", "Py", [code_item("s1-c", "for i in range(3):\n    print(i)")]),
                 scene("s2", "Out", [code_item("s2-c", "0\n1\n2", "text")]),
                 scene("s3", "None", [code_item("s3-c", "x = 1", None)])])
    assert found(lesson) == []


def test_tabs_mixed_with_spaces_is_a_fixable_warning_and_mixed_languages_info():
    lesson = sp([scene("s1", "Tabs", [code_item("s1-c", "if True:\n\tprint(1)\nif True:\n    print(2)")]),
                 scene("s2", "JS", [code_item("s2-c", "console.log(1);", "js")])])
    tabs = found(lesson, "code.mixed_indentation")
    assert [(i.scene_id, i.severity, i.fixable) for i in tabs] == [("s1", "warning", True)]
    mixed = found(lesson, "code.mixed_languages")
    assert [(i.scene_id, i.severity) for i in mixed] == [("s2", "info")]
    assert quality_report(lesson)["registry"]["code_languages"] == [
        {"language": "python", "name": "Python", "coloured": True, "scene_ids": ["s1"]},
        {"language": "javascript", "name": "JavaScript", "coloured": True, "scene_ids": ["s2"]},
    ]


def test_coloured_languages_mirror_the_player():
    source = (ROOT / "web/js/player/board.js").read_text(encoding="utf-8")
    block = re.search(r"const PRISM_LANGS = [^{]*\{(.*?)\}\);", source, re.S)
    assert block, "PRISM_LANGS not found in board.js"
    pairs = dict(re.findall(r"'?([\w+#-]+)'?\s*:\s*'([\w-]+)'", block.group(1)))
    assert pairs == quality_code.HIGHLIGHTED


# ---------------------------------------------------------------------------
# Titles and pacing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Newton's second law", "sentence"), ("Plants vs Animals", None), ("Worked example: a falling ball", "sentence"),
    ("Inside the Leaf", None), ("The Water Cycle In Nature", "title"), ("DNA", None), ("", None),
    ("Using the iPhone camera", "sentence"), (None, None), (42, None), ("WHY LEAVES ARE GREEN", "upper"),
])
def test_title_casing_styles(title, expected):
    assert title_casing(title) == expected


def test_title_casing_mixed_across_the_lesson():
    titles = ["Inside The Leaf", "How Plants Make Food", "Light And Water Together", "The sun gives energy",
              "WHY LEAVES ARE GREEN", "Photosynthesis", "DNA and RNA"]
    lesson = sp([scene(f"s{n}", t) for n, t in enumerate(titles, 1)])
    hits = found(lesson, "title.casing_mixed")
    assert [(i.scene_id, i.severity) for i in hits] == [("s4", "info"), ("s5", "info")]
    assert "capitalises only its first word" in hits[0].message


def test_title_cards_and_chapter_titles_are_other_levels():
    lesson = sp([
        {**scene("t", "Ohm's Law And Circuits"), "type": "title"},
        scene("s1", "What resistance does"), scene("s2", "Current in a loop"),
    ], chapters=[{"id": "c1", "title": "Basic Ideas Of Circuits", "scene_ids": ["s1"]},
                 {"id": "c2", "title": "Laws And Practice", "scene_ids": ["s2"]}])
    assert found(lesson, "title.casing_mixed") == []


def test_dense_boards_in_a_row_and_reading_time():
    dense = [item(f"x-i{n}", text=f"point {n}") for n in range(5)]
    lesson = sp([
        scene("s1", "One", copy.deepcopy([{**d, "id": d["id"].replace("x", "s1")} for d in dense])),
        scene("s2", "Two", [code_item("s2-c", "x = 1\ny = 2"), item("s2-i2", text="a"), item("s2-i3", text="b")]),
        {"id": "q", "type": "quiz_checkpoint", "title": "Check", "question": "Which?", "options": ["a", "b"],
         "correct_index": 0, "beats": [{"id": "q-b1", "narration": "Pick one now."}],
         "reveal_beats": [{"id": "q-b2", "narration": "It is a."}]},
        scene("s3", "Three", [{**d, "id": d["id"].replace("x", "s3")} for d in dense]),
    ])
    runs = found(lesson, "pacing.dense_run")
    assert [(i.scene_id, i.severity) for i in runs] == [("s2", "info")]
    assert runs[0].message.startswith("Scenes 1 and 2 are dense boards")
    wordy = sp([scene("s1", "Wordy", bullets("s1", *[" ".join(["word"] * 20)] * 5), narration="Short.")])
    reading = found(wordy, "board.reading_time")
    assert [(i.scene_id, i.severity) for i in reading] == [("s1", "info")]


def test_a_minimum_duration_gives_the_board_its_reading_time():
    words = bullets("s1", *[" ".join(["word"] * 20)] * 5)  # about 30 s to read
    held = sp([scene("s1", "Wordy", copy.deepcopy(words), narration="Short.", min_seconds=60)])
    assert found(held, "board.reading_time") == []  # the hold keeps the board on screen long enough
    short = sp([scene("s1", "Wordy", copy.deepcopy(words), narration="Short.", min_seconds=10)])
    reading = found(short, "board.reading_time")
    assert len(reading) == 1 and "plays for about 10 s" in reading[0].message  # 30 s > 1.5 x 10 s
    assert reading[0].message.endswith("give the scene a longer minimum duration.")
    plain = found(sp([scene("s1", "Wordy", copy.deepcopy(words), narration="Short.")]), "board.reading_time")
    assert plain[0].message.endswith("(a pause after a beat gives reading time).")  # no hold: wording as before


def test_hidden_scenes_neither_extend_nor_break_a_dense_run():
    def dense(sid: str, **kw: Any) -> dict[str, Any]:
        return scene(sid, sid.upper(), [item(f"{sid}-i{n}", text=f"point {n}") for n in range(5)], **kw)

    light = scene("l", "Light")
    assert found(sp([dense("h", hidden=True), dense("s"), light]), "pacing.dense_run") == []
    runs = found(sp([dense("d1"), scene("lh", "Light", hidden=True), dense("d2")]), "pacing.dense_run")
    assert [i.scene_id for i in runs] == ["d2"]  # d1 and d2 play back to back
    assert runs[0].message.startswith("Scenes 1 and 3 are dense boards")  # labels keep the editor's numbering
    assert found(sp([dense("d1"), scene("lh", "Light"), dense("d2")]), "pacing.dense_run") == []  # nothing hidden


def test_an_abbreviation_spelled_out_only_in_a_hidden_scene_is_not_spelled_out():
    spelled = scene("h", "Basics", bullets("h", "Machine Learning (ML) finds patterns in data."), hidden=True)
    used = scene("s", "Models", bullets("s", "ML models improve with more examples."))
    undefined = found(sp([spelled, used]), "terminology.abbreviation_undefined")
    assert [(i.scene_id, i.severity) for i in undefined] == [("s", "info")]
    shown = {**spelled, "hidden": False}
    assert found(sp([shown, used]), "terminology.abbreviation_undefined") == []  # nothing hidden: spelled out
    only_hidden = scene("h2", "More", bullets("h2", "GPU work is fast."), hidden=True)
    assert found(sp([used, only_hidden]), "terminology.abbreviation_undefined")[0].scene_id == "s"
    assert not any("GPU" in i.message for i in found(sp([used, only_hidden])))  # used only where nothing plays


# ---------------------------------------------------------------------------
# Contract, languages, bounds
# ---------------------------------------------------------------------------

def rich_lesson() -> Screenplay:
    return sp([
        scene("s1", "Learning from data", bullets("s1", "Today [[Machine Learning]] and ML.")),
        scene("s2", "Models", bullets("s2", "In practice, [[machine learning]] builds models."),
              narration="Machine learning, or ML, means programs that learn. ML stands for machine learning."),
        scene("s3", "Volume", [formula("s3-f", "V = lwh", ("V", "volume")), code_item("s3-c", "a\n\tb\n  c", "rust")]),
        scene("s4", "Voltage Basics", [formula("s4-f", "V = IR", ("V", "voltage"), ("I", "current"), ("R", "resistance"))]),
    ])


def test_every_finding_follows_the_issue_contract():
    issues = lint_quality(rich_lesson())
    assert issues
    for i in issues:
        assert i.code in QUALITY_CODES and i.source == "lint" and i.severity in ("info", "warning")
        assert i.fixable == (i.code == "code.mixed_indentation")
        assert i.scene_id is None or i.scene_id.startswith("s")
        assert len(i.message) <= 1500 and (i.message[0].isupper() or i.message[0] == "“")
    assert scenes_needing_repair(issues) == {}


def test_deterministic_and_the_input_is_never_changed():
    lesson = rich_lesson()
    before = lesson.model_dump(mode="json")
    first = [i.model_dump() for i in lint_quality(lesson)]
    assert first == [i.model_dump() for i in lint_quality(lesson)]
    assert quality_report(lesson) == quality_report(lesson)
    assert lesson.model_dump(mode="json") == before


def test_non_english_boards_skip_the_language_rules_but_keep_formulas_and_code():
    lesson = rich_lesson().model_copy(update={"board_language": "ta-IN"})
    codes = {i.code for i in lint_quality(lesson)}
    assert not {c for c in codes if c.startswith(("terminology.", "title.", "concept."))}
    assert {"formula.symbol_conflict", "code.language_unhighlighted"} <= codes


def test_clean_lessons_stay_silent():
    lesson = sp([
        scene("s1", "What resistance does", [item("s1-i1", "heading", text="Resistance"),
                                             item("s1-i2", text="A [[resistor]] limits current.")]),
        scene("s2", "Ohm's law", [formula("s2-f", "V = IR", ("V", "voltage"), ("I", "current"), ("R", "resistance"))]),
        scene("s3", "Using the law", bullets("s3", "Each [[resistor]] drops a voltage.")),
    ], concept_map=[{"id": "ohm", "title": "Ohm's law"}])
    assert lint_quality(lesson) == []


def test_validate_lint_includes_the_family_and_its_issues_never_block_or_repair():
    issues = lint(rich_lesson(), GenerationOptions(target_minutes=3))
    quality = [i for i in issues if i.code in QUALITY_CODES]
    assert {i.code for i in quality} >= {"terminology.casing", "formula.symbol_conflict", "code.mixed_indentation"}
    assert all(i.severity != "error" for i in quality)


def test_a_failing_check_stays_silent_and_the_rest_still_reports(monkeypatch):
    from aadhi.pipeline.quality import code as code_module

    def boom(_facts):
        raise RuntimeError("bug in a check")

    monkeypatch.setattr(code_module, "code_findings", boom)
    codes = {i.code for i in lint_quality(rich_lesson())}
    assert "formula.symbol_conflict" in codes and not {c for c in codes if c.startswith("code.")}


ADVERSARIAL = {
    "long word": "ML " + "a" * 1100, "spaces": "ML" + " " * 1100 + "(x)", "marks": "ML [A:" * 190,
    "brackets": "ML " + "(" * 1100, "capitals": "ML " + "A" * 1100, "capital words": "ML " + "AB " * 350,
    "dollars": "$a" * 550, "keyword brackets": "[[" * 550, "stars": "**a" * 390, "parentheses": "(AB) " * 220,
    "means": "ML" + " " * 400 + "means " + "a " * 300, "blank lines": "x:" + "\n" * 1100,
}


@pytest.mark.parametrize("name", sorted(ADVERSARIAL))
def test_adversarial_text_stays_fast(name):
    text = ADVERSARIAL[name]

    def big(n: int) -> dict[str, Any]:
        return {"id": f"s{n}", "type": "content", "title": text[:200],
                "board": [item(f"s{n}-i{k}", text=text) for k in range(4)]
                + [item(f"s{n}-t", "table", headers=[text[:200], "B"], rows=[[text, "x"]] * 3),
                   formula(f"s{n}-f", "E = mc^2 + x"), code_item(f"s{n}-c", text[:3000], "rust")],
                "beats": [{"id": f"s{n}-b{k}", "narration": text[:1500]} for k in range(4)]}

    lesson = sp([big(n) for n in range(200)])
    started = time.perf_counter()
    lint_quality(lesson)
    quality_report(lesson)
    assert time.perf_counter() - started < 6.0  # both passes over ~1 MB of crafted text; typical lessons take ms


def _spellings(word: str, hyphen: bool):
    """Distinct spellings of ``word`` (every casing; with ``hyphen`` also a moving hyphen)."""
    for k in range(1, 1 << 15):
        w = "".join(ch.upper() if (k >> i) & 1 else ch for i, ch in enumerate(word))
        if hyphen:
            pos = (k % 13) + 1
            w = w[:pos] + "-" + w[pos:]
        yield w


@pytest.mark.parametrize(("hyphen", "code"), [(False, "terminology.casing"), (True, "terminology.variant")])
def test_thousands_of_spellings_of_one_term_stay_fast(hyphen, code):
    """24,000 distinct written forms of one term (40 keywords x 3 bullets x 200 scenes): the pairwise
    clustering is bounded (text.MAX_GROUP_FORMS) and the repair stops at the edits it offers."""
    forms = _spellings("machinelearning", hyphen)
    lesson = sp([{"id": f"s{n}", "type": "content", "title": f"Scene {n}",
                  "board": [item(f"s{n}-i{k}", text=" ".join(f"[[{next(forms)}]]" for _ in range(40))[:1200])
                            for k in range(3)],
                  "beats": [{"id": f"s{n}-b", "narration": "A short clean beat."}]} for n in range(200)])
    started = time.perf_counter()
    codes = [i.code for i in lint_quality(lesson)]
    report = quality_report(lesson)
    assert time.perf_counter() - started < 6.0  # was over an hour (quadratic in the number of spellings)
    assert codes.count(code) == 1
    repair = next(r for r in report["repairs"] if r["code"] == code)
    assert 0 < len(repair["edits"]) <= 40


def test_thousands_of_distinct_capital_tokens_stay_fast():
    """Every scene full of distinct 4-letter capital tokens: only the first MAX_ABBREVIATIONS are collected,
    so the definition scan is bounded (it was quadratic: ~14 s per pass at 200 scenes)."""
    import itertools
    import string

    from aadhi.pipeline.quality.abbreviations import MAX_ABBREVIATIONS

    tokens = ("".join(t) for t in itertools.product(string.ascii_uppercase, repeat=4))
    order: list[str] = []

    def text() -> str:
        out = []
        while sum(len(t) + 3 for t in out) < 1190:
            out.append(next(tokens))
        order.extend(out)
        return " y ".join(out)

    lesson = sp([{"id": f"s{n}", "type": "content", "title": f"Scene {n}",
                  "board": [item(f"s{n}-i{k}", text=text()) for k in range(4)],
                  "beats": [{"id": f"s{n}-b", "narration": "A short clean beat."}]} for n in range(200)])
    started = time.perf_counter()
    lint_quality(lesson)
    report = quality_report(lesson)
    assert time.perf_counter() - started < 3.0
    reported = [a["abbreviation"] for a in report["registry"]["abbreviations"]]
    assert reported == order[:MAX_ABBREVIATIONS]  # the first ones in first-use order, as before


def test_many_term_groups_and_undefined_abbreviations_stay_fast():
    """24,000 multi-word term groups and 30 undefined abbreviations: the initials of the groups are indexed
    once instead of walked per abbreviation."""
    import itertools
    import string

    names = ("".join(p) for p in itertools.product(string.ascii_lowercase, repeat=4))
    caps = ["".join(p) for p in itertools.product("QJZK", repeat=3)][:30]

    def bullet(n: int, k: int) -> str:
        terms = " ".join(f"[[Ab{next(names)} Cd{next(names)}]]" for _ in range(12))
        return (terms + (f" {caps[n % 30]}" if k == 0 else ""))[:1200]

    lesson = sp([{"id": f"s{n}", "type": "content", "title": f"Scene {n}",
                  "board": [item(f"s{n}-i{k}", text=bullet(n, k)) for k in range(12)],
                  "beats": [{"id": f"s{n}-b", "narration": "A short clean beat."}]} for n in range(200)])
    started = time.perf_counter()
    codes = [i.code for i in lint_quality(lesson)]
    quality_report(lesson)
    assert time.perf_counter() - started < 3.0
    assert codes.count("terminology.abbreviation_undefined") == 30


def test_many_meanings_for_one_symbol_stay_fast():
    """19,200 legend meanings on one symbol: at most three distinct meanings are kept (the message names
    three), so the conflict check is linear (it was ~3 minutes)."""
    n = iter(range(10**6))
    lesson = sp([{"id": f"s{s}", "type": "content", "title": f"Scene {s}",
                  "board": [formula(f"s{s}-f{k}", "V = IR", *[("V", f"q{next(n)}") for _ in range(8)]) for k in range(12)],
                  "beats": [{"id": f"s{s}-b", "narration": "A short clean beat."}]} for s in range(200)])
    started = time.perf_counter()
    issues = [i for i in lint_quality(lesson) if i.code == "formula.symbol_conflict"]
    quality_report(lesson)
    assert time.perf_counter() - started < 3.0
    assert len(issues) == 1
    assert "“q0” in scene 1" in issues[0].message and "“q2”" in issues[0].message and "“q3”" not in issues[0].message


def test_a_typical_lesson_is_checked_quickly():
    lesson = sp([scene(f"s{n}", f"Scene {n} topic", bullets(f"s{n}", "A [[resistor]] limits current.", "Use **Ohm's law**."),
                       narration="Here V is voltage and I is current, so we use Ohm's law to find the value.")
                 for n in range(150)])
    started = time.perf_counter()
    lint_quality(lesson)
    assert time.perf_counter() - started < 0.5
