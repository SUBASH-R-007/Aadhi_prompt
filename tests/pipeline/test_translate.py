"""translate: masking, field collection, path setting, scripted translations."""

from __future__ import annotations

import asyncio

import pytest

from aadhi.pipeline.richlite import markup_balance_problems, mask, placeholder_problems, unmask
from aadhi.pipeline.translate import scene_fields, screenplay_fields, set_path, translate_screenplay
from aadhi.providers.base import ProviderError
from aadhi.schemas.screenplay import Screenplay


def make_sp() -> Screenplay:
    return Screenplay.model_validate({
        "language": "en-IN",
        "lexicon": [{"written": "Ohm's law", "spoken": "ohms law", "keep_in_english": True}],
        "learning_objectives": [{"id": "o1", "text": "Apply Ohm's law"}],
        "concept_map": [{"id": "ohm", "title": "Ohm's law", "summary": "V and I"}],
        "chapters": [{"id": "c1", "title": "Basics", "scene_ids": ["s1", "q1"]}],
        "scenes": [
            {"id": "s1", "type": "content", "title": "Ohm's law", "chapter_id": "c1",
             "board": [
                 {"id": "s1-i1", "kind": "bullet", "text": "Use $V = IR$ with `code` and **care**"},
                 {"id": "s1-i2", "kind": "formula", "latex": "V = IR", "text": "meaning"},
                 {"id": "s1-i3", "kind": "table", "headers": ["Q", "Unit"], "rows": [["Voltage", "volt"]]},
             ],
             "beats": [{"id": "s1-b1", "narration": "Ohm's law links voltage and current.", "board_item_id": "s1-i1"},
                       {"id": "s1-b2", "narration": "Remember it well.", "spoken": "Remember it well!"}],
             "side_panel": {"kind": "chart", "rationale": "compare", "title": "Values",
                            "chart": {"labels": ["a", "b"], "datasets": [{"label": "set", "data": [1, 2]}]}}},
            {"id": "q1", "type": "quiz_checkpoint", "title": "Check", "chapter_id": "c1", "question": "Which?",
             "options": ["one", "two"], "correct_index": 0, "feedback_wrong": ["", "no"],
             "beats": [{"id": "q1-b1", "narration": "Which one is right?"}],
             "reveal_beats": [{"id": "q1-b2", "narration": "The first one."}]},
        ],
    })


def test_mask_and_unmask():
    masked, orig = mask("Use $V = IR$ and `x` with Ohm's law today", ["Ohm's law"])
    assert masked == "Use ⟦0⟧ and ⟦1⟧ with ⟦2⟧ today" and orig == ["$V = IR$", "`x`", "Ohm's law"]
    assert unmask("⟦2⟧ இன்று ⟦0⟧ ⟦1⟧", orig) == "Ohm's law இன்று $V = IR$ `x`"
    assert placeholder_problems("⟦0⟧ ⟦0⟧ ⟦5⟧", 2) == ["missing placeholders ⟦1⟧", "unknown placeholders ⟦5⟧",
                                                      "repeated placeholders ⟦0⟧"]
    assert markup_balance_problems("**a* [[b") == ["unbalanced ** markers", "unbalanced [[ ]] markers"]


def test_field_collection_and_paths():
    sp = make_sp()
    data = sp.model_dump(mode="json")
    narr = [f.path for f in scene_fields(data["scenes"][0], False, [])]
    assert narr == ["beats[0].narration", "beats[1].narration", "beats[1].spoken"]
    full = {f.path for f in scene_fields(data["scenes"][0], True, ["Ohm's law"])}
    assert {"board[0].text", "board[1].text", "board[2].headers[0]", "board[2].rows[0][0]", "side_panel.title",
            "side_panel.chart.labels[0]", "side_panel.chart.datasets[0].label"} <= full
    assert not any("latex" in p for p in full)
    assert "title" not in full  # the title is only a kept-in-English term: nothing to translate
    assert "title" in {f.path for f in scene_fields(data["scenes"][0], True, [])}
    quiz = {f.path for f in scene_fields(data["scenes"][1], True, [])}
    assert {"question", "options[0]", "feedback_wrong[1]", "reveal_beats[0].narration"} <= quiz
    lecture = {f.path for f in screenplay_fields(data, [])}
    assert {"learning_objectives[0].text", "concept_map[0].title", "chapters[0].title"} <= lecture
    doc = {"board": [{"rows": [["a", "b"]]}]}
    set_path(doc, "board[0].rows[0][1]", "x")
    assert doc["board"][0]["rows"][0][1] == "x"
    with pytest.raises((KeyError, IndexError)):
        set_path(doc, "board[3].text", "x")


def test_translate_narration_only(job_ctx, providers):
    sp = make_sp()
    res = asyncio.run(translate_screenplay(job_ctx, sp, "ta-IN"))
    out = res.screenplay
    assert not res.issues
    assert out.language == "ta-IN" and out.board_language == "en-IN"
    s1 = out.scene_by_id("s1")
    assert s1.beats[0].narration == "தமிழ்: Ohm's law links voltage and current."  # kept-in-English term preserved
    assert s1.title == "Ohm's law" and s1.board[0].text == sp.scene_by_id("s1").board[0].text
    assert [b.id for b in s1.beats] == ["s1-b1", "s1-b2"]
    assert out.scene_by_id("q1").options == ["one", "two"]


def test_translate_board_too(job_ctx, providers):
    sp = make_sp()
    out = asyncio.run(translate_screenplay(job_ctx, sp, "hi-IN", translate_board=True)).screenplay
    assert out.board_language is None and out.language == "hi-IN"
    s1 = out.scene_by_id("s1")
    assert s1.board[0].text == "हिंदी: Use $V = IR$ with `code` and **care**"
    assert s1.board[1].latex == "V = IR"
    assert s1.board[2].rows == [["हिंदी: Voltage", "हिंदी: volt"]]
    assert out.scene_by_id("q1").options == ["हिंदी: one", "हिंदी: two"]
    assert out.learning_objectives[0].text == "हिंदी: Apply Ohm's law"
    assert out.chapters[0].title == "हिंदी: Basics"


def test_translate_reask_on_broken_placeholders(job_ctx, providers):
    from aadhi.pipeline import fake_content

    attempts = {"n": 0}

    def sloppy(prompt, schema):
        attempts["n"] += 1
        good = fake_content.translation_responder(prompt, schema)
        if "## Problems with your previous answer" not in prompt:
            good["items"] = [{"path": i["path"], "text": i["text"].replace("⟦0⟧", "")} for i in good["items"]]
        return good

    providers.llm.on("GenTranslation", sloppy)
    out = asyncio.run(translate_screenplay(job_ctx, make_sp(), "ta-IN", translate_board=True)).screenplay
    assert out.scene_by_id("s1").board[0].text.endswith("Use $V = IR$ with `code` and **care**")
    assert attempts["n"] > 2


def test_translate_failure_keeps_source_and_reports(job_ctx, providers):
    providers.llm.on("GenTranslation", lambda p, s: ProviderError("quota", provider="fake"))
    res = asyncio.run(translate_screenplay(job_ctx, make_sp(), "ta-IN"))
    assert {i.code for i in res.issues} == {"translate.scene_failed"}
    assert res.screenplay.scene_by_id("s1").beats[0].narration == "Ohm's law links voltage and current."
    with pytest.raises(ValueError):
        asyncio.run(translate_screenplay(job_ctx, make_sp(), "xx-XX"))


def test_untranslated_answer_is_reasked_once(job_ctx, providers):
    seen: list[str] = []

    def lazy_then_real(prompt, schema):
        from aadhi.pipeline.prompting import extract_json

        fields = extract_json(prompt, "Fields") or []
        seen.append(prompt)
        if "are not in Tamil" not in prompt:  # first answer: still English
            return {"items": [{"path": f["path"], "text": f["text"]} for f in fields]}
        return {"items": [{"path": f["path"], "text": "இது ஒரு மொழிபெயர்ப்பு, " + f["text"]} for f in fields]}

    providers.llm.on("GenTranslation", lazy_then_real)
    sp = Screenplay.model_validate({"scenes": [{"id": "s1", "type": "content", "beats": [
        {"id": "s1-b1", "narration": "Resistance is the opposition a material offers to the flow of current."}]}]})
    out = asyncio.run(translate_screenplay(job_ctx, sp, "ta-IN")).screenplay
    assert len(seen) == 2 and "are not in Tamil" in seen[1]
    assert out.scenes[0].beats[0].narration.startswith("இது ஒரு மொழிபெயர்ப்பு")
