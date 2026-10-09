"""The optional AI terminology assistant (QUALITY_AI_TERMINOLOGY), the critic hook and rewrite filtering."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aadhi.jobs.base import BudgetExceeded
from aadhi.pipeline import prompting
from aadhi.pipeline.base import Issue
from aadhi.pipeline.quality import AUTHOR_CODES
from aadhi.pipeline.quality.assist import GenTermAnswers, ambiguous_pairs, could_abbreviate, terminology_assist
from aadhi.providers.base import ProviderError
from aadhi.schemas.screenplay import Screenplay
from tests.pipeline.fakes import ScriptedLLM, install_providers


def lesson(board_language: str | None = None) -> Screenplay:
    def scene(sid: str, title: str, text: str, concept: str | None = None) -> dict[str, Any]:
        return {"id": sid, "type": "content", "title": title, "concept_id": concept,
                "board": [{"id": f"{sid}-i1", "kind": "bullet", "text": text}],
                "beats": [{"id": f"{sid}-b1", "narration": "A short clean beat of narration.", "board_item_id": f"{sid}-i1"}]}

    return Screenplay.model_validate({
        "board_language": board_language,
        "concept_map": [{"id": "ml", "title": "Machine Learning"}],
        "scenes": [
            scene("s1", "Learning systems", "Models keep data in a [[database]] first.", "ml"),
            scene("s2", "Sorting photos", "Our DB holds the photos."),
            scene("s3", "Predictions", "Models make [[predictions]].", "ml"),
        ],
    })


def answers(*pairs: tuple[int, bool]) -> Any:
    return lambda _prompt, _schema: {"answers": [{"pair": n, "same": same} for n, same in pairs]}


@pytest.fixture()
def assist_on(job_ctx):
    job_ctx.settings.quality_ai_terminology = True
    return job_ctx


def test_the_setting_is_off_by_default_and_then_nothing_is_asked(job_ctx, monkeypatch):
    assert job_ctx.settings.quality_ai_terminology is False
    llm = ScriptedLLM(use_fake_content=False)
    install_providers(monkeypatch, llm)
    assert asyncio.run(terminology_assist(job_ctx, lesson())) == []
    assert llm.calls == []


def test_the_ambiguous_pairs_are_bounded_words_with_scene_indexes():
    pairs = ambiguous_pairs(lesson())
    assert pairs and len(pairs) <= 8
    assert pairs[0] == {"kind": "abbreviation", "a": "DB", "b": "database", "scenes": [1], "n": 1}
    concept = [p for p in pairs if p["kind"] == "concept"]
    assert concept and concept[0]["a"] == "Machine Learning"
    assert ambiguous_pairs(lesson("ta-IN")) == []  # English boards only
    assert could_abbreviate("DB", "database") and not could_abbreviate("DB", "machine")


def test_a_yes_becomes_a_note_suggested_by_the_ai_reviewer(assist_on, monkeypatch):
    llm = ScriptedLLM(use_fake_content=False)
    llm.on("GenTermAnswers", answers((1, True), (2, False)))
    install_providers(monkeypatch, llm)
    issues = asyncio.run(terminology_assist(assist_on, lesson()))
    assert [(i.code, i.severity, i.source, i.fixable, i.scene_id) for i in issues] == [
        ("terminology.ambiguous", "info", "critic", False, "s2")]
    assert issues[0].message.startswith("Suggested by the AI reviewer (not checked by the rules)")
    call = llm.calls[0]
    assert call["schema"] == "GenTermAnswers" and call["system"] == prompting.system_prompt("quality_terms")
    assert '"pair": 1' in call["prompt"] and '"database"' in call["prompt"]
    assert assist_on.usages, "usage is recorded through the job context"
    assert "terminology.ambiguous" in AUTHOR_CODES


def test_unknown_pair_numbers_are_asked_again_then_dropped(assist_on, monkeypatch):
    llm = ScriptedLLM(use_fake_content=False)
    llm.on("GenTermAnswers", lambda p, s: {"answers": [{"pair": 99, "same": True}]})
    install_providers(monkeypatch, llm)
    assert asyncio.run(terminology_assist(assist_on, lesson())) == []
    assert len(llm.calls) == 2  # one validation retry, then the assistant gives up quietly
    assert any(e["level"] == "warning" and "terminology check" in e["message"] for e in assist_on.events)


def test_a_provider_failure_never_breaks_the_lecture(assist_on, monkeypatch):
    llm = ScriptedLLM(use_fake_content=False)
    llm.on("GenTermAnswers", lambda p, s: ProviderError("down", provider="fake"))
    install_providers(monkeypatch, llm)
    assert asyncio.run(terminology_assist(assist_on, lesson())) == []


def test_a_budget_stop_is_not_swallowed(assist_on, monkeypatch):
    llm = ScriptedLLM(use_fake_content=False)
    llm.on("GenTermAnswers", answers((1, True)))
    install_providers(monkeypatch, llm)
    assist_on.budget_usd = 0.0  # any priced usage exceeds it

    def record(_usage):
        raise BudgetExceeded("job budget exceeded")

    monkeypatch.setattr(assist_on, "record_usage", record)
    with pytest.raises(BudgetExceeded):
        asyncio.run(terminology_assist(assist_on, lesson()))


def test_the_response_model_is_llm_compatible():
    from aadhi.providers.llm.schema import assert_llm_compatible

    assert_llm_compatible(GenTermAnswers)


def test_the_critic_asks_once_for_the_whole_lecture_only(assist_on, monkeypatch, sample_ingest):
    from aadhi.pipeline.critic import critique

    llm = ScriptedLLM(use_fake_content=False)
    llm.on("GenTermAnswers", answers((1, True)))
    llm.on("GenCritique", lambda p, s: {"findings": [], "overall": "fine"})
    install_providers(monkeypatch, llm)
    issues = asyncio.run(critique(assist_on, lesson(), sample_ingest))
    assert [i.code for i in issues if i.code == "terminology.ambiguous"] == ["terminology.ambiguous"]
    assert len(llm.calls_for("GenTermAnswers")) == 1
    asyncio.run(critique(assist_on, lesson(), sample_ingest, scene_ids={"s2"}))
    assert len(llm.calls_for("GenTermAnswers")) == 1  # a single-scene review never asks


def test_rewrites_never_receive_the_authors_codes(job_ctx, monkeypatch, sample_ingest, options):
    from aadhi.pipeline import repair

    seen: list[list[str]] = []

    async def fake_write_scene(ctx, lc, pos, *, llm, extra_sections, task, files, prompt_names):
        seen.append([s for s in extra_sections if s.startswith("## Issues to fix")])
        raise RuntimeError("stop here")

    monkeypatch.setattr(repair, "write_scene", fake_write_scene)
    install_providers(monkeypatch, ScriptedLLM(use_fake_content=False))
    issues = [Issue(code="terminology.casing", severity="info", scene_id="s2", message="casing"),
              Issue(code="code.language_unhighlighted", severity="warning", scene_id="s2", message="rust"),
              Issue(code="code.mixed_indentation", severity="warning", scene_id="s2", message="tabs and spaces")]
    with pytest.raises(RuntimeError):
        asyncio.run(repair.rewrite_scene(job_ctx, lesson(), "s2", issues, sample_ingest, options, files=[]))
    assert len(seen) == 1 and len(seen[0]) == 1
    assert "tabs and spaces" in seen[0][0] and "casing" not in seen[0][0] and "rust" not in seen[0][0]
