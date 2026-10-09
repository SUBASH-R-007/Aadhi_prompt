"""companion: derivation from the board, practice problems, Markdown/HTML rendering (escaping)."""

from __future__ import annotations

import asyncio

from aadhi.pipeline.base import GenerationOptions
from aadhi.pipeline.companion import (
    build_sheet,
    derive_definitions,
    derive_formulas,
    derive_misconceptions,
    render_html,
    render_markdown,
)
from aadhi.providers.base import ProviderError
from aadhi.schemas.screenplay import CompanionSheet, Screenplay


def make_sp(**kw) -> Screenplay:
    data = {
        "session_title": "Ohm's <Law>",
        "subject_name": "BEE",
        "learning_objectives": [{"id": "o1", "text": "Apply **Ohm's law**", "bloom": "apply"},
                                {"id": "o2", "text": "Recall units", "bloom": "remember"}],
        "misconceptions": [{"id": "m1", "statement": "Current is used up", "correction": "It is conserved"}],
        "scenes": [{
            "id": "s1", "type": "content", "title": "Ohm's law",
            "board": [
                {"id": "s1-i1", "kind": "formula", "latex": "V = I \\times R", "text": "Ohm's law: voltage equals current times resistance",
                 "variables": [{"symbol_latex": "V", "meaning": "voltage", "unit": "V"}]},
                {"id": "s1-i2", "kind": "formula", "latex": "V=I \\times R"},
                {"id": "s1-i3", "kind": "definition", "term": "Resistance", "text": "Opposition to <current> flow"},
                {"id": "s1-i4", "kind": "misconception", "text": "Thick wires resist more", "justification": "Thinner wires resist more"},
                {"id": "s1-i5", "kind": "formula", "latex": "a < b", "text": "inequality"},
            ],
            "beats": [{"id": "s1-b1", "narration": "Here it is.", "board_item_id": "s1-i1"}],
        }],
    }
    data.update(kw)
    return Screenplay.model_validate(data)


def test_derivations():
    sp = make_sp()
    formulas = derive_formulas(sp)
    assert [f.latex for f in formulas] == ["V = I \\times R", "a < b"]  # duplicate (spacing) removed
    assert formulas[0].name == "Ohm's law" and formulas[0].source_item_id == "s1/s1-i1"
    assert formulas[0].variables[0].meaning == "voltage"
    defs = derive_definitions(sp)
    assert defs[0].term == "Resistance" and defs[0].source_item_id == "s1/s1-i3"
    miscs = derive_misconceptions(sp)
    assert [m.misconception for m in miscs] == ["Current is used up", "Thick wires resist more"]


def test_build_sheet_with_practice(job_ctx, providers, sample_ingest):
    sp = make_sp()
    sheet = asyncio.run(build_sheet(job_ctx, sp, sample_ingest, GenerationOptions()))
    assert isinstance(sheet, CompanionSheet)
    assert sheet.key_formulas and sheet.definitions and sheet.misconceptions
    probs = sheet.practice_problems
    assert 3 <= len(probs) <= 8 and [p.id for p in probs] == [f"p{n}" for n in range(1, len(probs) + 1)]
    assert {o for p in probs for o in p.objective_ids} <= {"o1", "o2"}
    call = providers.llm.calls_for("GenPractice")[0]
    assert "## Objectives" in call["prompt"] and "practice section" in call["system"]


def test_practice_reask_and_failure(job_ctx, providers, sample_ingest):
    sp = make_sp()
    seen = {"n": 0}

    def responder(prompt, schema):
        seen["n"] += 1
        bad = {"problems": [{"question": "Q", "final_answer": "A", "steps": ["s"], "objective_keys": ["ghost"]}]}
        if seen["n"] == 1:
            return bad
        return {"problems": [{"question": f"Q{i}", "final_answer": "A", "steps": ["s"], "objective_keys": ["o1", "ghost"],
                              "difficulty": d} for i, d in enumerate(["easy", "medium", "hard"])]}

    providers.llm.on("GenPractice", responder)
    sheet = asyncio.run(build_sheet(job_ctx, sp, sample_ingest, GenerationOptions()))
    assert seen["n"] == 2 and len(sheet.practice_problems) == 3
    assert all(p.objective_ids == ["o1"] for p in sheet.practice_problems)  # unknown keys dropped
    providers.llm.on("GenPractice", lambda p, s: ProviderError("down", provider="fake"))
    sheet = asyncio.run(build_sheet(job_ctx, sp, sample_ingest, GenerationOptions()))
    assert sheet.practice_problems == [] and sheet.key_formulas
    assert any(e.get("level") == "warning" for e in job_ctx.events)


def _with_sheet() -> Screenplay:
    sp = make_sp()
    sheet = CompanionSheet(
        key_formulas=derive_formulas(sp), definitions=derive_definitions(sp), misconceptions=derive_misconceptions(sp),
        practice_problems=[{"id": "p1", "question": "Find $I$ when <script>alert(1)</script>", "steps": ["Use $I=V/R$"],
                            "final_answer": "2 A", "hints": ["Divide"], "difficulty": "easy"}],
    )
    return sp.model_copy(update={"companion_sheet": sheet})


def test_render_markdown():
    md = render_markdown(_with_sheet())
    assert md.startswith("# Ohm's &lt;Law>")
    for heading in ("## Learning objectives", "## Key formulas", "## Definitions", "## Common misconceptions",
                    "## Practice problems", "## Answer key"):
        assert heading in md
    assert "$$\nV = I \\times R\n$$" in md and "| $V$ | voltage | V |" in md
    assert "<script>" not in md and "&lt;script>" in md
    assert "a < b" in md  # maths comparisons are not mangled


def test_render_html_is_escaped_and_scriptless():
    page = render_html(_with_sheet())
    assert page.startswith("<!doctype html>") and '<meta charset="utf-8">' in page
    assert "<script" not in page.lower()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert '<code class="tex formula">a &lt; b</code>' in page
    assert "<strong>Ohm&#x27;s law</strong>" in page  # rich-lite bold rendered safely
    assert "Opposition to &lt;current&gt; flow" in page
    assert "Ohm&#x27;s &lt;Law&gt; — Companion sheet" in page
    assert 'class="answers"' in page


def test_render_legacy_markdown():
    sp = make_sp(scenes=[]).model_copy(update={"companion_sheet": CompanionSheet(legacy_markdown="# Old <b>sheet</b>")})
    assert "&lt;b>sheet" in render_markdown(sp)
    assert "# Old &lt;b&gt;sheet&lt;/b&gt;" in render_html(sp)


def test_render_markdown_neutralises_html_inside_maths():
    """Math spans, $$ blocks and symbol cells are not Markdown constructs: tag-like '<' is broken there too."""
    import re

    from aadhi.pipeline.richlite import escape_tex_html, to_markdown

    sp = make_sp(learning_objectives=[{"id": "o1", "text": "Use $<img src=x onerror=alert(1)>$ and $a<b$",
                                       "bloom": "apply"}])
    sheet = CompanionSheet(key_formulas=[{
        "name": "Ohm", "latex": "V=IR</p><script>alert(2)</script>\n$$\n<iframe src=//evil>",
        "variables": [{"symbol_latex": "<b>V</b>", "meaning": "volt|age", "unit": "V"}],
    }])
    md = render_markdown(sp.model_copy(update={"companion_sheet": sheet}))
    assert not re.search(r"<[A-Za-z/!?]", md), md  # no raw tag can start anywhere in the file
    assert "$< img src=x onerror=alert(1)>$" in md and "$a< b$" in md  # TeX renders the same (spaces ignored)
    assert "| $< b>V< /b>$ | volt\\|age | V |" in md
    assert escape_tex_html("a < b") == "a < b" and escape_tex_html("x<y") == "x< y"
    assert to_markdown("`<b>` stays literal") == "`<b>` stays literal"  # code spans never render HTML


def test_unexpected_practice_error_keeps_the_sheet(job_ctx, providers, sample_ingest):
    providers.llm.on("GenPractice", lambda p, s: TypeError("practice bug"))
    sheet = asyncio.run(build_sheet(job_ctx, make_sp(), sample_ingest, GenerationOptions()))
    assert sheet.practice_problems == [] and sheet.key_formulas and sheet.definitions
