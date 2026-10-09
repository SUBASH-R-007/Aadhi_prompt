"""Manim code repair and visual QA use the lecture's AI engine (``ManimRenderRequest.llm_provider``)."""

from __future__ import annotations

from typing import Any

import pytest

from aadhi.manim import llm, qa, render
from aadhi.schemas.screenplay import ManimSpec
from tests.manim.test_render import BROKEN, FIXED, fails_on_typo, settings_with


@pytest.fixture()
def engines(monkeypatch, scripted_llm) -> list[str | None]:
    """Engines the Manim code asked ``aadhi.manim.llm.get_llm`` for (answered by the scripted LLM)."""
    asked: list[str | None] = []

    def get_llm(settings: Any, engine: str | None = None) -> Any:
        asked.append(engine)
        return scripted_llm

    monkeypatch.setattr(llm, "get_llm", get_llm)
    return asked


def test_fast_model_per_engine(app_env) -> None:
    s = app_env.model_copy(update={"anthropic_model_fast": "claude-fast-test", "llm_model_fast": "gpt-5-mini"})
    assert llm.fast_model(s) == "gpt-5-mini"  # the default engine (fake in tests) reports LLM_MODEL_FAST
    assert llm.fast_model(s, "anthropic") == "claude-fast-test"
    assert llm.fast_model(s, "openai") == "gpt-5-mini"  # LLM_MODEL_FAST names an OpenAI model
    assert llm.fast_model(s, "gemini") == "gemini-2.5-flash"


def test_engine_is_not_part_of_the_render_cache_key(make_request) -> None:
    req = make_request(code=FIXED)
    claude = req.model_copy(update={"llm_provider": "anthropic"})
    assert render.cache_key(req, has_latex=True, sandbox="subprocess") == render.cache_key(
        claude, has_latex=True, sandbox="subprocess")
    assert make_request(code=FIXED).llm_provider is None  # older callers keep the server default


async def test_repair_uses_the_requests_engine(job_ctx, fake_runner, fast_media, scripted_llm, engines,
                                               make_request) -> None:
    settings_with(job_ctx, anthropic_model_fast="claude-fast-test")
    fake_runner.fail_when = fails_on_typo
    scripted_llm.queue("ManimCodeFix", {"diagnosis": "typo", "code": FIXED})
    result = await render.render_manim(job_ctx, make_request(code=BROKEN, llm_provider="anthropic"))
    assert result.healed and result.final_spec == ManimSpec(code=FIXED)
    assert engines == ["anthropic"]
    assert [c["model"] for c in scripted_llm.calls_for("ManimCodeFix")] == ["claude-fast-test"]


async def test_visual_qa_uses_the_requests_engine(job_ctx, fast_media, scripted_llm, engines, make_request) -> None:
    settings_with(job_ctx, anthropic_model_fast="claude-fast-test")
    scripted_llm.queue("FrameReview", {"issues": []})
    fast_media["conform"].append(("src", "dst", 6.5))
    assert await qa.review_frames(job_ctx, "video.mp4", make_request(code=FIXED, llm_provider="anthropic")) == []
    assert await qa.review_frames(job_ctx, "video.mp4", make_request(code=FIXED)) == []
    assert engines == ["anthropic", None]
    assert [c["model"] for c in scripted_llm.calls] == ["claude-fast-test", job_ctx.settings.llm_model_fast]
