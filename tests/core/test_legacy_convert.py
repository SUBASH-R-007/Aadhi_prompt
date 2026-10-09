"""v1 JSON -> v2 Screenplay conversion (fixtures + edge cases). Every result must validate."""

from __future__ import annotations

import copy

import pytest

from aadhi.legacy import convert_legacy, is_legacy
from aadhi.legacy.html_board import extract_board, parse_html, rich_text
from aadhi.legacy.narration import build_beats, cap_beats, clean_narration, split_long
from aadhi.legacy.panels import convert_side_panel, js_to_mathjs, model_3d_primitives
from aadhi.schemas.screenplay import (
    AIVideoScene,
    BoardScene,
    ChapterCardScene,
    InteractiveScene,
    QuizScene,
    Screenplay,
    SimulationScene,
)


def roundtrip(sp: Screenplay) -> Screenplay:
    """The converted screenplay survives JSON serialisation + re-validation (what the DB stores)."""
    again = Screenplay.model_validate(sp.model_dump(mode="json"))
    assert again.model_dump(mode="json") == sp.model_dump(mode="json")
    return again


def scene(sp: Screenplay, sid: str):
    return sp.scene_by_id(sid)


def convert_one(v1_scene: dict, **meta) -> tuple[Screenplay, list[str]]:
    sp, warnings = convert_legacy({"scenes": [v1_scene], **meta})
    roundtrip(sp)
    return sp, warnings


# --- fixtures ----------------------------------------------------------------------------


def test_showcase_converts(showcase_json):
    assert is_legacy(showcase_json)
    sp, warnings = convert_legacy(showcase_json)
    roundtrip(sp)
    assert sp.subject_name == "Basic Electrical Engineering" and sp.session_number == "Session 2"
    assert [c.id for c in sp.concept_map] == ["intro", "voltage_current", "resistance", "ohms_law", "applications"]
    assert sp.concept_map[3].depends_on == ["voltage_current", "resistance"]
    assert len(sp.scenes) == 16
    types = [s.type for s in sp.scenes]
    assert types.count("ai_video") == 3 and types.count("chapter_card") == 2 and types.count("quiz_checkpoint") == 1
    assert "key_takeaway" in types and "simulation" in types
    assert sp.companion_sheet.legacy_markdown.startswith("# Key Formulas")
    assert [c.id for c in sp.chapters] == ["ch0", "ch1", "ch2"]
    assert sp.chapters[1].title == "The River of Charge"
    assert all(s.chapter_id for s in sp.scenes)
    assert len(warnings) <= 5


def test_showcase_board_and_beats(showcase_json):
    sp, _ = convert_legacy(showcase_json)
    content = scene(sp, "s2")
    assert isinstance(content, BoardScene) and content.mascot_position == "right"
    assert content.side_panel.kind == "skill_tree"
    assert [i.kind.value for i in content.board] == ["paragraph", "paragraph", "callout_info"]
    beats = content.beats
    assert beats[0].board_item_id is None and beats[0].narration == "And here is the answer waiting for us."
    assert [b.board_item_id for b in beats[1:4]] == ["s2-i1", "s2-i2", "s2-i3"]
    assert beats[1].pause_after == 1.5  # [PAUSE] directly after the first sync chunk
    assert beats[-1].board_item_id is None


def test_showcase_definitions_and_misconception(showcase_json):
    sp, _ = convert_legacy(showcase_json)
    s5 = scene(sp, "s5")
    d = s5.board[0]
    assert d.kind.value == "definition" and d.term == "Voltage (V)"
    assert d.text.startswith("The electrical pressure")
    m = s5.board[2]
    assert m.kind.value == "misconception" and m.misconception_id == "m1"
    assert "used up" in m.text and m.justification.startswith("The SAME current")
    assert sp.misconceptions[0].statement.startswith("Students often think")
    assert s5.beats[-1].pause_after == 2.0  # [PAUSE:2]


def test_showcase_quiz(showcase_json):
    sp, _ = convert_legacy(showcase_json)
    quiz = scene(sp, "s9")
    assert isinstance(quiz, QuizScene)
    assert quiz.correct_index == 1 and quiz.options[1] == "It doubles"
    assert quiz.feedback_wrong[1] == "" and "inverse trap" in quiz.feedback_wrong[2]
    assert quiz.countdown_seconds == 8 and quiz.mascot_position == "hidden"
    assert quiz.reveal_beats[0].narration.startswith("It doubles!")
    assert quiz.beats[0].narration == "Checkpoint!"


def test_showcase_media_scenes(showcase_json):
    sp, _ = convert_legacy(showcase_json)
    video = scene(sp, "s1")
    assert isinstance(video, AIVideoScene) and video.video_prompt.startswith("A cinematic dolly shot")
    assert video.fallback_image_prompt and len(video.beats) == 4
    sim = scene(sp, "s8")
    assert isinstance(sim, SimulationScene)
    assert "class OhmsLawScene(AadhiScene):" in sim.manim.code and "Visual reasoning" in sim.notes


def test_showcase_side_panels(showcase_json):
    sp, _ = convert_legacy(showcase_json)
    assert scene(sp, "s3").side_panel.kind == "image"
    model = scene(sp, "s7").side_panel
    assert model.kind == "model_3d" and model.model_3d.primitives[0].label == "Nucleus"
    graph = scene(sp, "s11").side_panel
    assert graph.kind == "graph" and graph.graph.functions[0].expr == "x/2"
    assert graph.title == "I = V / R (straight line!)"
    term = scene(sp, "s12").side_panel
    assert term.kind == "terminal" and term.terminal.command.startswith("python circuit_check.py")
    chart = scene(sp, "s13").side_panel.chart
    assert chart.chart_type == "bar" and chart.labels[0] == "LED Bulb" and chart.datasets[0].data[-1] == 15
    gif = scene(sp, "s16").side_panel
    assert gif.kind == "gif" and gif.gif_query == "celebration high five excited"


def test_showcase_examples_and_table(showcase_json):
    sp, _ = convert_legacy(showcase_json)
    ex = scene(sp, "s11")
    assert ex.type == "example" and ex.board[0].kind.value == "heading"
    step1 = ex.board[1]
    assert step1.kind.value == "example_step" and step1.text == "Step 1: Choose the form I = V / R"
    assert step1.justification.startswith("Because we want the flow")
    fill = scene(sp, "s12")
    assert fill.beats[3].pause_after == 5.0 and fill.beats[3].board_item_id == "s12-i3"
    summary = scene(sp, "s16")
    table = summary.board[0]
    assert table.kind.value == "table" and table.headers == ["Quantity", "Symbol", "Unit", "Water Analogy"]
    assert table.rows[2][2] == "Ohm (Ω)"
    assert summary.beats[0].board_item_id is None and summary.beats[1].board_item_id == "s16-i2"
    takeaway = scene(sp, "s15")
    assert [i.kind.value for i in takeaway.board] == ["takeaway"] * 3
    assert takeaway.beats[0].pause_after == 3.0


def test_demo_slides_convert(demo_slides_json):
    assert is_legacy(demo_slides_json)
    sp, warnings = convert_legacy(demo_slides_json)
    roundtrip(sp)
    assert [s.type for s in sp.scenes] == ["title", "content", "content", "ai_video", "content"]
    definition, formula, *bullets = scene(sp, "s2").board
    assert definition.term == "Stress" and formula.latex == r"\sigma = \frac{F}{A}"
    assert bullets[0].text == "**F:** Applied Force (N)"
    assert scene(sp, "s3").board[1].text.startswith("[[Tensile (+ve):]] Tends to elongate")
    takeaway = scene(sp, "s5").board
    assert takeaway[0].kind.value == "paragraph" and [i.kind.value for i in takeaway[1:]] == ["takeaway"] * 3
    # No [SYNC] markers: items are visible from the start; long narration split at sentences.
    s2 = scene(sp, "s2")
    assert all(b.board_item_id is None for b in s2.beats) and len(s2.beats) == 2
    assert all(len(b.narration) <= 350 for s in sp.scenes for b in s.all_beats())
    assert sp.session_number == "Session 1" and sp.concept_map == []
    assert warnings == []


# --- detection -----------------------------------------------------------------------------


def test_is_legacy_detection(showcase_json):
    v2 = convert_legacy(showcase_json)[0].model_dump(mode="json")
    assert not is_legacy(v2)
    assert not is_legacy({"scenes": []})
    assert not is_legacy("nope") and not is_legacy(None) and not is_legacy([1, 2])
    assert is_legacy([{"type": "content", "narration": "hi"}])
    assert is_legacy({"slides": [{"type": "p5_simulation", "p5_code": "x"}]})
    assert not is_legacy({"scenes": [{"type": "content", "beats": []}]})


def test_not_a_lecture_raises():
    with pytest.raises(ValueError):
        convert_legacy({"title": "x"})


# --- narration -----------------------------------------------------------------------------


def test_clean_narration():
    assert (
        clean_narration("This is **really** *important*, <b>ok</b>?  [SYNC] yes")
        == "This is really important, ok ? yes"
    )
    assert clean_narration("2 * 3 star") == "2 3 star"


def test_split_long():
    text = " ".join(f"Sentence number {i} is here." for i in range(40))
    parts = split_long(text)
    assert len(parts) > 2 and all(len(p) <= 350 for p in parts)
    assert " ".join(parts) == text
    giant = "word " * 600
    assert all(len(p) <= 1400 for p in split_long(giant))


def test_build_beats_sync_and_pause():
    drafts = build_beats("Intro. [SYNC] One. [PAUSE] Still one. [SYNC] Two. [PAUSE:3] [SYNC] [SYNC] Extra.", n_slots=3)
    assert [(d.narration, d.slot, d.pause_after) for d in drafts] == [
        ("Intro.", None, 0.0),
        ("One.", 0, 1.5),
        ("Still one.", None, 0.0),
        ("Two.", 1, 3.0),
        ("Extra.", None, 0.0),  # chunk 4 has no slot; chunk 3 had no speech and nothing readable
    ]


def test_build_beats_reads_item_for_empty_chunk():
    drafts = build_beats("[SYNC][SYNC] Second.", n_slots=2, readable=lambda slot: f"Item {slot}")
    assert [(d.narration, d.slot) for d in drafts] == [("Item 0", 0), ("Second.", 1)]


def test_pause_clamped_and_leading_pause_dropped():
    drafts = build_beats("[PAUSE] Hello [PAUSE:30] [PAUSE] there")
    assert [(d.narration, d.pause_after) for d in drafts] == [("Hello", 8.0), ("there", 0.0)]


def test_cap_beats_preserves_reveals():
    drafts = build_beats(" ".join(f"[SYNC] Point {i}." for i in range(6)), n_slots=6)
    capped = cap_beats(drafts, 4)
    assert len(capped) == 4
    warnings: list[str] = []
    capped2 = cap_beats(drafts, 3, warnings)
    assert len(capped2) == 3 and warnings  # had to drop reveals


# --- board HTML ----------------------------------------------------------------------------


def test_rich_inline_conversion():
    node = parse_html(
        '<p>A <strong>bold</strong> and <em>soft</em> <span class="keyword">key</span> with '
        '<code>x*y</code>, <span class="inline-code">f()</span> and $a^*$ but 2*3.</p>'
    )
    text = rich_text(node.children[0])
    assert text == r"A **bold** and *soft* [[key]] with `x*y`, `f()` and $a^*$ but 2\*3."


def test_extract_board_kinds_and_sync_order():
    html = (
        "<h2>Head</h2><p>Para</p><ul><li>One</li><li>Two</li></ul>"
        '<div class="math-block">$$E = mc^2$$</div><div class="definition">Ohm: unit of resistance</div>'
        "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
        '<div class="formula-block">\\\\[x^2\\\\]</div><div class="tip-callout">Tip!</div>'
        '<div class="warning-callout">Watch out</div><div class="info-callout">Info</div>'
        '<pre><code class="language-python">print("hi")\n  x = 1</code></pre>'
        "<p></p><h4>Small heading</h4>loose text"
    )
    warnings: list[str] = []
    ext = extract_board(html, scene_type="content", warnings=warnings, where="t")
    kinds = [i["kind"] for i in ext.items]
    assert kinds == [
        "heading",
        "paragraph",
        "bullet",
        "bullet",
        "formula",
        "definition",
        "table",
        "formula",
        "callout_tip",
        "callout_warning",
        "callout_info",
        "code",
        "heading",
        "paragraph",
    ]
    assert ext.items[4]["latex"] == "E = mc^2" and ext.items[7]["latex"] == "x^2"
    assert ext.items[5] == {"kind": "definition", "text": "unit of resistance", "term": "Ohm"}
    assert ext.items[11]["language"] == "python" and ext.items[11]["code"] == 'print("hi")\n  x = 1'
    # Sync slots follow v1's selector order; table/h4/loose text are not sync-able; the empty <p>
    # still consumes a slot (None) so [SYNC] counting stays aligned with v1.
    assert ext.sync_slots == [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, None]


def test_nested_sync_elements_use_outermost():
    ext = extract_board("<li>Outer <p>inner</p></li>", scene_type="content", warnings=[], where="t")
    assert ext.sync_slots == [0] and ext.items[0]["text"] == "Outer inner"


def test_figures_and_gifs():
    html = (
        '<p>See below</p><div class="board-image-placeholder" data-img-id="Image_1_Static_motor">[x]</div>'
        '<img class="context-gif" data-query="spinning engine"><p>Inline <img src="x.png"> image</p>'
    )
    sp, warnings = convert_one(
        {
            "type": "content",
            "title": "T",
            "html": html,
            "narration": "[SYNC] a [SYNC] b",
            "uploaded_images": {"Image_1_Static_motor": "/static/x.png"},
        }
    )
    board = sp.scenes[0].board
    assert board[1].kind.value == "figure" and board[1].figure_id == "image_1_static_motor"
    assert sp.figures[0].id == "image_1_static_motor" and sp.figures[0].caption == "Image 1 Static motor"
    assert sp.figures[0].asset_key is None
    assert any("animated GIF dropped" in w for w in warnings)
    assert any("inline image dropped" in w for w in warnings)
    assert any("re-uploaded" in w for w in warnings)


def test_display_math_paragraph_and_forbidden_tex():
    sp, warnings = convert_one(
        {"type": "content", "html": r"<p>$$\frac{a}{b}$$</p><p>\[ \href{x}{y} \]</p>", "narration": "x"}
    )
    a, b = sp.scenes[0].board
    assert a.kind.value == "formula" and a.latex == r"\frac{a}{b}"
    assert b.kind.value == "paragraph" and b.text.startswith("`")
    assert any("forbidden TeX" in w for w in warnings)


def test_unbalanced_html_and_scripts():
    html = "<p>One<p>Two<ul><li>A<li>B</ul><script>alert(1)</script><style>p{}</style><div>Loose <b>bold"
    ext = extract_board(html, scene_type="content", warnings=[], where="t")
    texts = [i.get("text") for i in ext.items]
    assert texts == ["One", "Two", "A", "B", "Loose **bold**"]
    assert all("alert" not in (t or "") for t in texts)


# --- scene-level edge cases ----------------------------------------------------------------


def test_too_many_board_items_continue_in_new_scene():
    lis = "".join(f"<li>Point {i}</li>" for i in range(15))
    narration = "Intro. " + " ".join(f"[SYNC] Talking about point {i}." for i in range(15))
    sp, warnings = convert_one({"type": "content", "title": "Many", "html": f"<ul>{lis}</ul>", "narration": narration})
    first, second = sp.scenes
    assert len(first.board) == 12 and len(second.board) == 3
    assert second.id == "s1-2" and second.title == "Many (continued)"
    assert second.beats[0].board_item_id == "s1-2-i1"
    assert any("continued" in w for w in warnings)


def test_more_items_than_syncs_and_more_syncs_than_items():
    sp, _ = convert_one({"type": "content", "html": "<p>a</p><p>b</p><p>c</p>", "narration": "x [SYNC] y"})
    s = sp.scenes[0]
    assert [b.board_item_id for b in s.beats] == [None, "s1-i1"]  # b and c visible from the start
    sp2, warnings = convert_one({"type": "content", "html": "<p>a</p>", "narration": "x [SYNC] y [SYNC] z"})
    assert [b.board_item_id for b in sp2.scenes[0].beats] == [None, "s1-i1", None]
    assert any("more [SYNC] markers" in w for w in warnings)


def test_scene_without_narration_gets_one():
    sp, warnings = convert_one({"type": "content", "title": "Silent", "html": "<p>x</p>"})
    assert sp.scenes[0].beats[0].narration == "Silent"
    assert any("no narration" in w for w in warnings)
    sp2, _ = convert_one({"type": "chapter_card", "title": "Part", "chapter_label": "Part 9"})
    card = sp2.scenes[0]
    assert isinstance(card, ChapterCardScene) and card.beats == [] and card.chapter_label == "Part 9"


def test_chapter_card_beats_capped_to_four():
    narration = " ".join(f"Sentence {i}. [PAUSE]" for i in range(10))
    sp, _ = convert_one({"type": "chapter_card", "title": "C", "narration": narration})
    assert len(sp.scenes[0].beats) == 4


def test_v1_backup_format_and_mascot():
    data = {
        "subjectName": "Physics",
        "unitName": "U1",
        "sessionNumber": "Session 3",
        "sessionTitle": "Waves",
        "conceptMapData": [{"id": "Waves 101", "title": "Waves"}],
        "slides": [
            {
                "type": "content",
                "concept_id": "Waves 101",
                "aadhi_position": "upside_down",
                "html": "<p>x</p>",
                "narration": "hello",
            }
        ],
    }
    sp, warnings = convert_legacy(data)
    roundtrip(sp)
    assert (sp.subject_name, sp.unit_name, sp.session_number, sp.session_title) == (
        "Physics",
        "U1",
        "Session 3",
        "Waves",
    )
    assert sp.scenes[0].concept_id == "waves-101" and sp.scenes[0].mascot_position == "left"
    assert any("mascot position" in w for w in warnings)


def test_concept_map_problems():
    data = {
        "concept_map": [
            {"id": "a", "title": "A", "depends_on": ["b"]},
            {"id": "b", "title": "B", "depends_on": ["a", "ghost", "b"]},
            {"id": "a", "title": "dup"},
            "junk",
        ],
        "scenes": [{"type": "content", "concept_id": "unknown", "narration": "x"}],
    }
    sp, warnings = convert_legacy(data)
    roundtrip(sp)
    assert [c.id for c in sp.concept_map] == ["a", "b"]
    assert sp.concept_map[0].depends_on == [] and sp.concept_map[1].depends_on == ["a"]
    assert sp.scenes[0].concept_id is None
    assert any("cycle" in w for w in warnings) and any("duplicate concept" in w for w in warnings)


def test_quiz_edge_cases():
    base = {"type": "quiz_checkpoint", "question": "Q?", "narration": "Think.", "countdown_seconds": 99}
    sp, warnings = convert_one({**base, "options": ["A", "a", "B"], "correct_index": 7})
    q = sp.scenes[0]
    assert isinstance(q, QuizScene)
    assert q.options == ["A", "a (2)", "B"] and q.correct_index == 0 and q.countdown_seconds == 30
    assert q.reveal_beats[0].narration.startswith("The answer is: A.")
    assert any("out of range" in w for w in warnings) and any("distinct" in w for w in warnings)
    sp2, _ = convert_one(
        {**base, "options": [str(i) for i in range(7)], "correct_index": 6, "feedback_wrong": ["w"] * 7}
    )
    q2 = sp2.scenes[0]
    assert len(q2.options) == 5 and q2.options[q2.correct_index] == "6" and q2.feedback_wrong[q2.correct_index] == ""
    sp3, warnings3 = convert_one({**base, "options": ["only"], "correct_index": 0})
    assert isinstance(sp3.scenes[0], BoardScene) and sp3.scenes[0].board[0].text == "Q?"


def test_simulation_variants():
    sp, warnings = convert_one(
        {"type": "visual", "title": "V", "manim_code": "class X(MovingCameraScene):\n  pass", "narration": "Look."}
    )
    s = sp.scenes[0]
    assert isinstance(s, SimulationScene) and "MovingCameraScene" in s.manim.code
    assert any("not a plain Scene subclass" in w for w in warnings)
    sp2, warnings2 = convert_one({"type": "simulation", "title": "No code", "narration": "Hmm."})
    assert isinstance(sp2.scenes[0], BoardScene)
    assert any("without usable Manim code" in w for w in warnings2)


def test_ai_video_and_p5():
    sp, warnings = convert_one(
        {"type": "ai_video", "title": "T", "prompt": "p" * 1500, "narration": "n", "video_url": "/static/v.mp4"}
    )
    v = sp.scenes[0]
    assert len(v.video_prompt) == 1200 and len(v.fallback_image_prompt) == 800 and "/static/v.mp4" in v.notes
    assert any("video url" in w for w in warnings)
    sp2, warnings2 = convert_one(
        {"type": "p5_simulation", "title": "Play", "p5_code": "function setup(){}", "narration": "Try it"}
    )
    assert isinstance(sp2.scenes[0], InteractiveScene)
    assert any("web player" in w for w in warnings2)
    sp3, _ = convert_one({"type": "p5_simulation", "title": "Empty", "narration": "x"})
    assert isinstance(sp3.scenes[0], BoardScene)


def test_unknown_scene_type_and_non_dict_scene():
    sp, warnings = convert_legacy([{"type": "hologram", "html": "<p>x</p>", "narration": "y"}, "junk"])
    roundtrip(sp)
    assert len(sp.scenes) == 1 and sp.scenes[0].type == "content"
    assert any("unknown v1 scene type" in w for w in warnings) and any("not an object" in w for w in warnings)


def test_companion_formats():
    sp, _ = convert_legacy({"companion_sheet": None, "scenes": [{"type": "title", "narration": "x"}]})
    assert sp.companion_sheet.legacy_markdown is None
    sp2, w2 = convert_legacy({"companion_sheet": ["a", "b"], "scenes": [{"type": "title", "narration": "x"}]})
    assert '"a"' in sp2.companion_sheet.legacy_markdown and w2


def test_long_texts_truncated_with_warning():
    long_p = "word " * 400
    sp, warnings = convert_one({"type": "content", "title": "T" * 300, "html": f"<p>{long_p}</p>", "narration": "x"})
    s = sp.scenes[0]
    assert len(s.title) == 240 and len(s.board[0].text) <= 1200
    assert any("shortened" in w for w in warnings)


# --- side panels -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("x*x + 2*x", "x*x + 2*x"),
        ("Math.sin(x)", "sin(x)"),
        ("Math.pow(x, 2) + Math.PI", "pow(x, 2) + pi"),
        ("x ** 3", "x ^ 3"),
        ("y = Math.exp(-x/2)", "exp(-x/2)"),
        ("return Math.sqrt(x) * Math.E;", "sqrt(x) * e"),
        ("Math.random() * x", None),
        ("x > 0 ? x : -x", None),
        ("alert(1)", None),
        ("", None),
    ],
)
def test_js_to_mathjs(src, expected):
    assert js_to_mathjs(src) == expected


def test_panel_conversions():
    w: list[str] = []
    scatter = convert_side_panel(
        {
            "type": "chart",
            "chart_type": "scatter",
            "data": {"datasets": [{"label": "pts", "data": [{"x": 1, "y": 2}, {"x": 3, "y": "4"}]}]},
        },
        scene_title="t",
        warnings=w,
        where="s1",
    )
    assert scatter["chart"]["chart_type"] == "bar" and scatter["chart"]["labels"] == ["1", "3"]
    assert scatter["chart"]["datasets"][0]["data"] == [2.0, 4.0]
    ragged = convert_side_panel(
        {
            "type": "chart",
            "chart_type": "polarArea",
            "data": {"labels": ["a", "b", "c"], "datasets": [{"data": [1, "x"]}]},
        },
        scene_title="t",
        warnings=w,
        where="s1",
    )
    assert ragged["chart"]["chart_type"] == "pie" and ragged["chart"]["datasets"][0]["data"] == [1.0, 0.0, 0.0]
    assert convert_side_panel({"type": "chart", "data": {}}, scene_title="t", warnings=w, where="s1") is None
    quiz = convert_side_panel(
        {"type": "quiz", "question": "Q", "options": ["a", "b"], "correct_index": 1},
        scene_title="t",
        warnings=w,
        where="s1",
    )
    assert quiz["quiz"]["correct_index"] == 1
    assert (
        convert_side_panel(
            {"type": "quiz", "question": "Q", "options": ["a"], "correct_index": 0},
            scene_title="t",
            warnings=w,
            where="s1",
        )
        is None
    )
    image = convert_side_panel({"type": "image"}, scene_title="Fallback title", warnings=w, where="s1")
    assert image["image_prompt"] == "Fallback title"
    manim = convert_side_panel(
        {"type": "manim", "title": "Proof", "manim_code": "class P(Scene):\n pass"},
        scene_title="t",
        warnings=w,
        where="s1",
    )
    assert "AadhiScene" in manim["manim"]["code"]
    for bad in (
        {"type": "animation", "keyword": "math"},
        {"type": "3d_model", "model_name": "dragon"},
        {"type": "gif"},
        {"type": "graph", "function": "Math.random()"},
        "nope",
    ):
        assert convert_side_panel(bad, scene_title="t", warnings=w, where="s1") is None
    assert len(w) >= 5


def test_model_3d_primitives():
    assert len(model_3d_primitives("ATOM")) == 7
    assert model_3d_primitives("torus")[0]["shape"] == "torus"
    assert model_3d_primitives("box")[0]["size"] == [2.0, 2.0, 2.0]
    assert model_3d_primitives("dragon") is None


def test_input_not_mutated(showcase_json):
    before = copy.deepcopy(showcase_json)
    convert_legacy(showcase_json)
    assert showcase_json == before


# --- robustness ------------------------------------------------------------------------------

_JUNK = [
    None,
    0,
    -1,
    3.5,
    True,
    "",
    " ",
    "x" * 5000,
    [],
    [1, "a", None],
    {},
    {"type": "x"},
    "<p>unclosed <b>bold <div class='definition'><strong>T:</strong>",
    "[SYNC][PAUSE:999][SYNC]",
    "<table><tr><td>1</td></tr></table>",
    r"<p>\[\href{evil}{x}\]</p>",
    "$$",
    "<script>alert(1)</script>",
]


def test_fuzzed_scenes_never_crash(showcase_json, demo_slides_json):
    import random

    rng = random.Random(1234)
    base_scenes = showcase_json["scenes"] + demo_slides_json
    keys = sorted({k for s in base_scenes for k in s} | {"side_panel", "options", "correct_index", "p5_code"})
    for _ in range(150):
        scenes = []
        for _ in range(rng.randint(1, 6)):
            s = copy.deepcopy(rng.choice(base_scenes))
            for _ in range(rng.randint(1, 4)):
                s[rng.choice(keys)] = copy.deepcopy(rng.choice(_JUNK))
            if rng.random() < 0.3 and isinstance(s.get("side_panel"), dict):
                s["side_panel"]["type"] = rng.choice(["chart", "graph", "quiz", "3d_model", "image", "gif", "zzz"])
            scenes.append(s)
        data = {"concept_map": rng.choice([showcase_json["concept_map"], [], None, "junk"]), "scenes": scenes}
        sp, warnings = convert_legacy(data)
        roundtrip(sp)
        assert isinstance(warnings, list)


# --- review regressions ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("depends_on", "expected"),
    [
        ("concept1", ["concept1"]),  # a single id string is ONE dependency, not one per character
        (7, []),
        ({"id": "concept1"}, []),
        (None, []),
        ([["concept1"], True, "concept1"], ["concept1"]),
    ],
)
def test_concept_map_depends_on_shapes(depends_on, expected):
    data = {
        "concept_map": [{"id": "concept1", "title": "One"}, {"id": "two", "title": "Two", "depends_on": depends_on}],
        "scenes": [{"type": "content", "narration": "x"}],
    }
    sp, warnings = convert_legacy(data)
    roundtrip(sp)
    assert sp.concept_map[1].depends_on == expected
    if isinstance(depends_on, (int, dict)):
        assert any("unknown format" in w for w in warnings)


@pytest.mark.parametrize(
    "callout",
    [
        "Common Misconception: People believe this is true but [[]]",  # empty correction after stripping
        "Common Misconception: [[]][[]][[]] but the current is the same everywhere",  # empty statement
    ],
)
def test_misconception_with_empty_parts_stays_a_warning_callout(callout):
    html = f'<div class="warning-callout">{callout}</div>'
    sp, warnings = convert_one({"type": "content", "title": "T", "html": html, "narration": "x"})
    item = sp.scenes[0].board[0]
    assert item.kind.value == "callout_warning" and item.misconception_id is None and item.text
    assert sp.misconceptions == []
    assert any("kept as a warning callout" in w for w in warnings)


def test_valid_misconception_still_registered():
    html = '<div class="warning-callout">Common Misconception: Current is used up but it stays the same</div>'
    sp, _ = convert_one({"type": "content", "title": "T", "html": html, "narration": "x"})
    item = sp.scenes[0].board[0]
    assert item.kind.value == "misconception" and item.misconception_id == "m1"
    assert sp.misconceptions[0].correction.startswith("It stays")


def test_more_than_200_figures_degrade_to_captions():
    def placeholder(n: int) -> str:
        return f'<div class="board-image-placeholder" data-img-id="fig_{n}">[x]</div>'

    scenes = [
        {
            "type": "content",
            "title": f"S{s}",
            "html": "".join(placeholder(s * 10 + k) for k in range(10)),
            "narration": "x",
        }
        for s in range(21)
    ]
    sp, warnings = convert_legacy({"scenes": scenes})
    roundtrip(sp)
    assert len(sp.figures) == 200
    known = {f.id for f in sp.figures}
    items = [i for s in sp.scenes for i in getattr(s, "board", [])]
    assert all(i.figure_id in known for i in items if i.kind.value == "figure")
    dropped = [i for i in items if i.kind.value == "paragraph"]
    assert len(dropped) == 10 and dropped[0].text == "fig 200"
    assert any("more than 200 figures" in w for w in warnings)


@pytest.mark.parametrize("key", ["manim_code", "simulation_code", "code"])
def test_simulation_code_keys(key):
    sp, warnings = convert_one(
        {"type": "simulation", "title": "Sim", key: "class Demo(Scene):\n  pass", "narration": "Look."}
    )
    s = sp.scenes[0]
    assert isinstance(s, SimulationScene) and "class Demo(AadhiScene)" in s.manim.code
    assert not any("without usable Manim code" in w for w in warnings)


def test_simulation_code_key_precedence():
    sp, _ = convert_one(
        {
            "type": "visual",
            "manim_code": "  ",
            "simulation_code": "class A(Scene):\n  pass",
            "code": "class B(Scene):\n  pass",
            "narration": "x",
        }
    )
    assert "class A(AadhiScene)" in sp.scenes[0].manim.code


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999", float("nan"), float("inf"), 10**400])
def test_non_finite_chart_values_become_zero(value):
    panel = {"type": "chart", "data": {"labels": ["a", "b"], "datasets": [{"label": "d", "data": [value, 2]}]}}
    sp, warnings = convert_one(
        {"type": "content", "title": "T", "html": "<p>x</p>", "narration": "x", "side_panel": panel}
    )
    data = sp.scenes[0].side_panel.chart.datasets[0].data
    assert data == [0.0, 2.0]
    import json

    json.dumps(sp.model_dump(mode="json"), allow_nan=False)  # what Starlette's JSONResponse does


def test_cross_reference_failure_degrades_instead_of_raising(monkeypatch):
    import aadhi.legacy.convert as conv

    real = conv._chapters

    def broken_chapters(scenes):
        out = real(scenes)
        if out:
            out[0]["scene_ids"].append("ghost-scene")  # simulate an unexpected dangling reference
        return out

    monkeypatch.setattr(conv, "_chapters", broken_chapters)
    data = {
        "concept_map": [{"id": "a", "title": "A"}],
        "scenes": [
            {"type": "chapter_card", "title": "Part 1", "narration": "Hi", "concept_id": "a"},
            {"type": "content", "title": "T", "html": "<p>x</p>", "narration": "x"},
        ],
    }
    sp, warnings = convert_legacy(data)
    roundtrip(sp)
    assert sp.chapters == [] and sp.concept_map == [] and len(sp.scenes) == 2
    assert any("could not be validated" in w for w in warnings)
