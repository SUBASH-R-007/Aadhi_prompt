"""render_manim: caching, templates, LaTeX fallback, free-form self-healing, visual QA.

The sandbox, ffmpeg and the LLM are replaced by fakes (``tests/manim/fakes.py``); real renders are
covered by ``test_render_slow.py``.
"""

from __future__ import annotations

import asyncio
import copy
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from aadhi.jobs.base import BudgetExceeded, JobCancelled
from aadhi.manim import render
from aadhi.manim.base import TEMPLATE_API_VERSION, ManimError
from aadhi.manim.templates import registry
from aadhi.providers.base import ProviderError
from aadhi.schemas.screenplay import ManimSpec
from tests.manim.fakes import error_log

BROKEN = """from manim import *


class Demo(AadhiScene):
    def construct(self):
        self.wait_until_beat(0)
        self.play(Create(Circl()))
        self.play_step(1, FadeIn(Square()))
        self.play_step(2, FadeOut(Square()))
""".strip()  # ManimSpec strips surrounding whitespace
FIXED = BROKEN.replace("Circl()", "Circle()")
POLISHED = FIXED.replace("Square()", "Square().scale(0.8)")
UNSYNCED = """from manim import *


class Demo(AadhiScene):
    def construct(self):
        self.play(Create(Circle()))
        self.play(FadeIn(Square()))
""".strip()
NAME_ERROR = "NameError: name 'Circl' is not defined"


def fails_on_typo(script: str) -> str | None:
    return error_log(NAME_ERROR) if "Circl()" in script else None


def example(name: str) -> dict[str, Any]:
    return copy.deepcopy(registry[name].example_params)


def settings_with(ctx, **update):
    ctx.settings = ctx.settings.model_copy(update=update)
    return ctx.settings


def warnings(ctx) -> list[str]:
    return [e["message"] for e in ctx.events if e["type"] == "log" and e["level"] == "warning"]


# --- templates ------------------------------------------------------------------------------------


async def test_template_render_creates_asset_and_is_cached(job_ctx, fake_runner, fast_media, make_request) -> None:
    req = make_request("equation_steps", example("equation_steps"), beats=4)
    first = await render.render_manim(job_ctx, req)
    assert not first.cached and not first.healed
    assert (first.width, first.height) == (854, 480)
    assert first.duration == pytest.approx(req.total_duration)
    assert first.asset_key.startswith("manim-") and first.storage_key.startswith("assets/manim/")
    assert first.final_spec == req.spec and first.qa_issues == []

    asset = job_ctx.assets.get(first.asset_key)
    assert asset is not None and asset.kind == "manim" and asset.mime == "video/mp4"
    assert asset.meta["template"] == "equation_steps" and asset.meta["latex"] is True
    assert asset.meta["spec_key"] == first.asset_key

    assert len(fake_runner.scripts) == 1
    script = fake_runner.scripts[0]
    assert "_AADHI_CFG = {" in script and "class EquationStepsScene(AadhiScene)" in script
    assert "'beat_times': [0.5, 2.5, 4.5, 6.5]" in script and "class AadhiScene(MovingCameraScene)" in script
    call = fake_runner.calls[0]
    assert call["scene"] == "EquationStepsScene" and call["quality"] == "l"
    assert call["timeout"] == float(job_ctx.settings.manim_timeout_seconds)
    assert not call["workdir"].parent.exists(), "the private work dir is removed after the render"
    assert fast_media["conform"][0][2] == pytest.approx(req.total_duration)

    second = await render.render_manim(job_ctx, req)
    assert second.cached and second.asset_key == first.asset_key and second.storage_key == first.storage_key
    assert len(fake_runner.scripts) == 1, "a cache hit never renders"


def test_cache_key_covers_every_input(make_request) -> None:
    base = make_request("wave", example("wave"), beats=4)

    def key(req, **kw) -> str:
        return render.cache_key(req, has_latex=kw.get("latex", True), sandbox=kw.get("sandbox", "subprocess"))

    k0 = key(base)
    assert k0 == key(base.model_copy(deep=True))
    jitter = base.model_copy(update={"beat_times": [t + 0.0002 for t in base.beat_times]})
    assert key(jitter) == k0, "beat times are rounded to milliseconds"
    changed_params = example("wave")
    changed_params["steps"][0]["amplitude"] = 1.5
    variants = [
        base.model_copy(update={"beat_times": [t + 0.01 for t in base.beat_times]}),
        base.model_copy(update={"total_duration": base.total_duration + 1}),
        base.model_copy(update={"target": "panel"}),
        base.model_copy(update={"quality": "m"}),
        base.model_copy(update={"background": "#000000"}),
        base.model_copy(update={"language": "ta-IN"}),
        base.model_copy(update={"spec": ManimSpec(template="wave", params=changed_params)}),
    ]
    keys = {key(v) for v in variants} | {key(base, latex=False), key(base, sandbox="docker")}
    assert k0 not in keys and len(keys) == len(variants) + 2
    inputs = render.cache_inputs(base, has_latex=True, sandbox="subprocess")
    assert inputs["template_api"] == TEMPLATE_API_VERSION and inputs["manim"] == render.manim_version()
    assert inputs["beat_times"] == [0.5, 2.5, 4.5, 6.5]
    assert render.manim_version().startswith("0.")
    assert render.source_revision("wave") != render.source_revision("geometry") != render.source_revision(None)


async def test_template_step_mismatch_is_logged_not_fatal(job_ctx, fake_runner, fast_media, make_request) -> None:
    req = make_request("equation_steps", example("equation_steps"), beats=2)
    result = await render.render_manim(job_ctx, req)
    assert not result.cached
    assert any("4 steps" in w and "2 beats" in w for w in warnings(job_ctx))


@pytest.mark.parametrize(
    ("template", "params", "total", "needle"),
    [
        ("wave", {"steps": []}, 5.0, "invalid template params"),
        ("nope", {}, 5.0, "unknown manim template"),
        ("wave", None, 0.0, "total_duration must be positive"),
    ],
)
async def test_invalid_requests_raise(job_ctx, fake_runner, fast_media, make_request, template, params, total,
                                      needle) -> None:
    req = make_request(template, params if params is not None else example(template), total_duration=total)
    with pytest.raises(ManimError, match=needle):
        await render.render_manim(job_ctx, req)
    assert fake_runner.scripts == []


NAN, INF = float("nan"), float("inf")


@pytest.mark.parametrize(
    ("beat_times", "total", "needle"),
    [
        ([0.5, 2.5, 4.5, 6.5], NAN, "total_duration"),
        ([0.5, 2.5, 4.5, 6.5], INF, "total_duration"),
        ([0.5, 2.5, 4.5, 6.5], -1.0, "total_duration"),
        ([0.0, NAN, 4.5, 6.5], 9.0, "beat_times"),
        ([0.0, 2.5, INF, 6.5], 9.0, "beat_times"),
        ([-1.0, 2.5, 4.5, 6.5], 9.0, "beat_times"),
    ],
)
async def test_non_finite_times_are_rejected_before_rendering(job_ctx, fake_runner, fast_media, scripted_llm,
                                                              make_request, beat_times, total, needle) -> None:
    for req in (make_request("wave", example("wave"), beat_times=beat_times, total_duration=total),
                make_request(code=FIXED, beat_times=beat_times, total_duration=total)):
        with pytest.raises(ManimError, match=needle):
            await render.render_manim(job_ctx, req)
    assert fake_runner.scripts == [] and scripted_llm.calls == [], "no render and no paid repair of correct code"


async def test_tiny_negative_float_noise_in_beat_times_is_accepted(job_ctx, fake_runner, fast_media,
                                                                   make_request) -> None:
    req = make_request("wave", example("wave"), beat_times=[-0.0001, 2.5, 4.5, 6.5], total_duration=9.0)
    assert not (await render.render_manim(job_ctx, req)).cached


LATEX_LOG = "! Undefined control sequence.\nl.7 \\foo\n! LaTeX Error: File `siunitx.sty' not found."


async def test_template_latex_failure_falls_back_to_plain_text(job_ctx, fake_runner, fast_media, make_request) -> None:
    fake_runner.fail_when = lambda script: LATEX_LOG if "'has_latex': True" in script else None
    req = make_request("equation_steps", example("equation_steps"), beats=4)
    result = await render.render_manim(job_ctx, req)
    assert len(fake_runner.scripts) == 2 and "'has_latex': False" in fake_runner.scripts[1]
    latex_key = render.cache_key(req, has_latex=True, sandbox="subprocess")
    plain_key = render.cache_key(req, has_latex=False, sandbox="subprocess")
    assert result.asset_key == plain_key and not result.cached
    assert job_ctx.assets.get(plain_key).meta["latex"] is False
    assert job_ctx.assets.get(latex_key) is None, "the fallback is never stored under the LaTeX key"
    assert any("plain-text maths" in w for w in warnings(job_ctx))
    assert not any("LaTeX is not available" in w for w in warnings(job_ctx))


async def test_latex_fallback_is_not_served_once_latex_works(job_ctx, fake_runner, fast_media, make_request) -> None:
    """Reviewer probe: LaTeX broken (missing package), then fixed -> the next render uses LaTeX."""
    req = make_request("equation_steps", example("equation_steps"), beats=4)
    fake_runner.fail_when = lambda script: LATEX_LOG if "'has_latex': True" in script else None
    degraded = await render.render_manim(job_ctx, req)
    assert job_ctx.assets.get(degraded.asset_key).meta["latex"] is False

    again = await render.render_manim(job_ctx, req)  # still broken: LaTeX is tried, then the cached fallback is used
    assert again.cached and again.asset_key == degraded.asset_key and len(fake_runner.scripts) == 3

    fake_runner.fail_when = lambda script: None  # the admin installed the package
    fixed = await render.render_manim(job_ctx, req)
    assert not fixed.cached and fixed.asset_key == render.cache_key(req, has_latex=True, sandbox="subprocess")
    assert job_ctx.assets.get(fixed.asset_key).meta["latex"] is True
    assert "'has_latex': True" in fake_runner.scripts[-1]
    cached = await render.render_manim(job_ctx, req)
    assert cached.cached and cached.asset_key == fixed.asset_key and len(fake_runner.scripts) == 4


async def test_concurrent_latex_fallbacks_share_the_plain_render(job_ctx, fake_runner, fast_media, make_request) -> None:
    fake_runner.fail_when = lambda script: LATEX_LOG if "'has_latex': True" in script else None
    req = make_request("equation_steps", example("equation_steps"), beats=4)
    a, b = await asyncio.gather(render.render_manim(job_ctx, req), render.render_manim(job_ctx, req))
    assert a.asset_key == b.asset_key == render.cache_key(req, has_latex=False, sandbox="subprocess")
    assert sum("'has_latex': False" in s for s in fake_runner.scripts) == 1


async def test_template_failure_raises_with_log_tail(job_ctx, fake_runner, fast_media, make_request) -> None:
    fake_runner.fail_when = lambda script: error_log("ValueError: boom")
    with pytest.raises(ManimError, match="boom") as info:
        await render.render_manim(job_ctx, make_request("wave", example("wave"), beats=4))
    assert "ValueError: boom" in info.value.log_tail
    assert len(fake_runner.scripts) == 1, "non-LaTeX failures are not retried for templates"


async def test_timeout_is_reported(job_ctx, fake_runner, fast_media, make_request) -> None:
    fake_runner.killed_reason = "timeout"
    with pytest.raises(ManimError, match="timed out"):
        await render.render_manim(job_ctx, make_request("wave", example("wave"), beats=4))


async def test_cancelled_job_never_renders(job_ctx, fake_runner, fast_media, make_request) -> None:
    job_ctx.cancelled = True
    with pytest.raises(JobCancelled):
        await render.render_manim(job_ctx, make_request("wave", example("wave"), beats=4))
    assert fake_runner.scripts == []


async def test_concurrent_identical_requests_render_once(job_ctx, fake_runner, fast_media, make_request) -> None:
    req = make_request("bar_compare", example("bar_compare"), beats=5)
    a, b = await asyncio.gather(render.render_manim(job_ctx, req), render.render_manim(job_ctx, req))
    assert a.asset_key == b.asset_key and sorted([a.cached, b.cached]) == [False, True]
    assert len(fake_runner.scripts) == 1


async def test_uses_process_wide_manim_limit(job_ctx, fake_runner, fast_media, make_request, monkeypatch) -> None:
    import aadhi.jobs.limits as limits

    seen: list[str] = []

    @asynccontextmanager
    async def acquire(name: str) -> AsyncIterator[None]:
        seen.append(name)
        yield

    monkeypatch.setattr(limits, "acquire", acquire)
    await render.render_manim(job_ctx, make_request("wave", example("wave"), beats=4))
    assert seen == ["manim"]


async def test_manim_slot_falls_back_without_limits_module(app_env, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "aadhi.jobs.limits", None)  # import now raises ImportError
    active = 0
    peak = 0

    async def worker() -> None:
        nonlocal active, peak
        async with render.manim_slot(app_env):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1

    await asyncio.gather(*(worker() for _ in range(5)))
    assert 1 <= peak <= app_env.manim_max_concurrent


# --- free-form code -------------------------------------------------------------------------------


async def test_freeform_is_repaired_and_healed_once(job_ctx, fake_runner, fast_media, scripted_llm,
                                                     make_request) -> None:
    fake_runner.fail_when = fails_on_typo
    scripted_llm.queue("ManimCodeFix", {"diagnosis": "typo in Circle", "code": "```python\n" + FIXED + "```"})
    req = make_request(code=BROKEN, beats=3, title="Circles", beat_cues=["draw a circle", "", "fade"])
    result = await render.render_manim(job_ctx, req)
    assert result.healed and not result.cached
    assert result.final_spec.code == FIXED and result.final_spec.template is None
    assert len(fake_runner.scripts) == 2

    (call,) = scripted_llm.calls_for("ManimCodeFix")
    assert call["model"] == job_ctx.settings.llm_model_fast
    assert NAME_ERROR in call["prompt"] and "Circl()" in call["prompt"]
    assert "beat 0 starts at 0.50 s: draw a circle" in call["prompt"] and "Scene title: Circles" in call["prompt"]
    assert "wait_until_beat" in call["system"] and "{{freeform_rules}}" not in call["system"]
    assert len(job_ctx.usages) == 1 and job_ctx.usages[0].model == job_ctx.settings.llm_model_fast

    original = job_ctx.assets.get(result.asset_key)
    assert original.meta["healed"] is True and original.meta["final_spec"]["code"] == FIXED
    assert original.meta["attempts"] == 2 and original.meta["prompt_version"] == "manim-fix-2"
    healed_key = render.cache_key(req, has_latex=True, sandbox="subprocess", spec=ManimSpec(code=FIXED))
    healed = job_ctx.assets.get(healed_key)
    assert healed is not None and healed.meta["healed_from"] == result.asset_key

    again = await render.render_manim(job_ctx, req)  # the failing spec is healed once
    assert again.cached and again.healed and again.final_spec.code == FIXED and again.asset_key == result.asset_key
    direct = await render.render_manim(job_ctx, req.model_copy(update={"spec": ManimSpec(code=FIXED)}))
    assert direct.cached and direct.asset_key == healed_key
    assert len(fake_runner.scripts) == 2 and len(scripted_llm.calls_for("ManimCodeFix")) == 1


async def test_guard_rejection_is_repaired_before_rendering(job_ctx, fake_runner, fast_media, scripted_llm,
                                                           make_request) -> None:
    scripted_llm.queue("ManimCodeFix", {"diagnosis": "removed os", "code": FIXED})
    evil = "import os\n" + FIXED
    result = await render.render_manim(job_ctx, make_request(code=evil, beats=3))
    assert result.healed and result.final_spec.code == FIXED
    assert len(fake_runner.scripts) == 1, "rejected code never reaches the sandbox"
    assert "rejected by the safety checker" in scripted_llm.calls_for("ManimCodeFix")[0]["prompt"]


async def test_repair_output_is_guarded_inside_the_reask_loop(job_ctx, fake_runner, fast_media, scripted_llm,
                                                              make_request) -> None:
    fake_runner.fail_when = fails_on_typo
    scripted_llm.queue("ManimCodeFix", {"code": FIXED + "\nopen('x')\n"}, {"code": FIXED})
    result = await render.render_manim(job_ctx, make_request(code=BROKEN, beats=3))
    assert result.final_spec.code == FIXED
    calls = scripted_llm.calls_for("ManimCodeFix")
    assert [c["attempt"] for c in calls] == [0, 1] and "'open' is not allowed" in calls[1]["prompt"]


async def test_unsynchronised_code_is_repaired_for_timing(job_ctx, fake_runner, fast_media, scripted_llm,
                                                          make_request) -> None:
    scripted_llm.queue("ManimCodeFix", {"code": FIXED})
    result = await render.render_manim(job_ctx, make_request(code=UNSYNCED, beats=3))
    assert result.healed and result.final_spec.code == FIXED and len(fake_runner.scripts) == 1
    assert "wait_until_beat" in scripted_llm.calls_for("ManimCodeFix")[0]["prompt"]


async def test_unsynchronised_code_renders_when_repairs_are_disabled(job_ctx, fake_runner, fast_media,
                                                                     make_request) -> None:
    settings_with(job_ctx, manim_max_repair_attempts=0)
    result = await render.render_manim(job_ctx, make_request(code=UNSYNCED, beats=3))
    assert not result.healed and result.final_spec.code == UNSYNCED


async def test_repairs_are_bounded(job_ctx, fake_runner, fast_media, scripted_llm, make_request) -> None:
    fake_runner.fail_when = fails_on_typo
    scripted_llm.queue("ManimCodeFix", {"code": BROKEN})  # the model keeps returning broken code
    req = make_request(code=BROKEN, beats=3)
    with pytest.raises(ManimError, match="after 2 repair attempt") as info:
        await render.render_manim(job_ctx, req)
    assert NAME_ERROR in info.value.log_tail
    assert len(fake_runner.scripts) == 3 and len(scripted_llm.calls_for("ManimCodeFix")) == 2
    assert job_ctx.assets.get(render.cache_key(req, has_latex=True, sandbox="subprocess")) is None


async def test_repair_provider_error_raises_manim_error(job_ctx, fake_runner, fast_media, scripted_llm,
                                                        make_request) -> None:
    fake_runner.fail_when = fails_on_typo
    scripted_llm.queue("ManimCodeFix", ProviderError("model unavailable", provider="fake"))
    with pytest.raises(ManimError, match="repair failed: model unavailable"):
        await render.render_manim(job_ctx, make_request(code=BROKEN, beats=3))


async def test_budget_errors_propagate(job_ctx, fake_runner, fast_media, scripted_llm, make_request) -> None:
    fake_runner.fail_when = fails_on_typo
    scripted_llm.queue("ManimCodeFix", BudgetExceeded("job budget exceeded"))
    with pytest.raises(BudgetExceeded):
        await render.render_manim(job_ctx, make_request(code=BROKEN, beats=3))


async def test_freeform_policy(job_ctx, fake_runner, fast_media, make_request) -> None:
    req = make_request(code=FIXED, beats=3)
    settings_with(job_ctx, manim_allow_freeform=False)
    with pytest.raises(ManimError, match="disabled"):
        await render.render_manim(job_ctx, req)
    settings_with(job_ctx, manim_allow_freeform=True, app_env="production")
    with pytest.raises(ManimError, match="requires MANIM_SANDBOX=docker"):
        await render.render_manim(job_ctx, req)
    templated = await render.render_manim(job_ctx, make_request("wave", example("wave"), beats=4))
    assert not templated.cached, "templates are allowed without docker"
    fake_runner.name = "docker"
    assert not (await render.render_manim(job_ctx, req)).healed, "production + docker allows free-form code"
    assert len(fake_runner.scripts) == 2


async def test_errors_and_prompts_are_redacted(job_ctx, fake_runner, fast_media, scripted_llm, make_request) -> None:
    secret = "sk-test-secret-value-123456"
    settings_with(job_ctx, openai_api_key=SecretStr(secret), manim_max_repair_attempts=1)
    fake_runner.fail_when = lambda script: error_log(f"RuntimeError: leaked {secret}")
    scripted_llm.queue("ManimCodeFix", {"code": BROKEN})
    with pytest.raises(ManimError) as info:
        await render.render_manim(job_ctx, make_request(code=BROKEN, beats=3))
    assert secret not in str(info.value) and secret not in info.value.log_tail
    assert "[REDACTED]" in info.value.log_tail
    prompt = scripted_llm.calls_for("ManimCodeFix")[0]["prompt"]
    assert secret not in prompt and "[REDACTED]" in prompt
    assert all(secret not in e["message"] and secret not in str(e["data"]) for e in job_ctx.events)


async def test_repair_with_the_real_fake_provider(job_ctx, fake_runner, fast_media, make_request) -> None:
    """End to end through aadhi.providers.factory -> FakeLLM (structured re-ask loop + usage)."""
    fake_mod = pytest.importorskip("aadhi.providers.llm.fake")
    fake_runner.fail_when = fails_on_typo
    fake_mod.FakeLLM.register("ManimCodeFix", lambda prompt, schema: {"diagnosis": "typo", "code": FIXED})
    try:
        result = await render.render_manim(job_ctx, make_request(code=BROKEN, beats=3))
    finally:
        fake_mod.FakeLLM.unregister("ManimCodeFix")
    assert result.healed and result.final_spec.code == FIXED
    assert job_ctx.usages and job_ctx.usages[0].provider == "fake"


# --- visual QA ------------------------------------------------------------------------------------


QA_ISSUE = {"issues": [{"frame": 2, "kind": "cut_off", "description": "label cut at the right edge"}]}


async def test_visual_qa_records_issues_for_templates(job_ctx, fake_runner, fast_media, scripted_llm,
                                                      make_request) -> None:
    settings_with(job_ctx, manim_visual_qa=True)
    scripted_llm.queue("FrameReview", QA_ISSUE)
    result = await render.render_manim(job_ctx, make_request("wave", example("wave"), beats=4))
    assert len(result.qa_issues) == 1 and "cut off: label cut at the right edge" in result.qa_issues[0]
    assert result.qa_issues[0].startswith("frame 2 (t=")
    assert len(fake_runner.scripts) == 1, "template output is not code-repaired"
    (review,) = scripted_llm.calls_for("FrameReview")
    assert review["images"] == 4 and "16:9" in review["prompt"]
    assert job_ctx.assets.get(result.asset_key).meta["qa_issues"] == result.qa_issues


async def test_visual_qa_gives_freeform_code_one_repair_round(job_ctx, fake_runner, fast_media, scripted_llm,
                                                              make_request) -> None:
    settings_with(job_ctx, manim_visual_qa=True)
    scripted_llm.queue("FrameReview", QA_ISSUE, {"issues": []})
    scripted_llm.queue("ManimCodeFix", {"diagnosis": "smaller square", "code": POLISHED})
    result = await render.render_manim(job_ctx, make_request(code=FIXED, beats=3))
    assert result.healed and result.final_spec.code == POLISHED and result.qa_issues == []
    assert len(fake_runner.scripts) == 2 and len(fast_media["conform"]) == 2
    (fix,) = scripted_llm.calls_for("ManimCodeFix")
    assert fix["images"] == 4 and "label cut at the right edge" in fix["prompt"]
    assert len(scripted_llm.calls_for("FrameReview")) == 2


async def test_visual_qa_repair_that_fails_keeps_the_original(job_ctx, fake_runner, fast_media, scripted_llm,
                                                              make_request) -> None:
    settings_with(job_ctx, manim_visual_qa=True)
    fake_runner.fail_when = fails_on_typo
    scripted_llm.queue("FrameReview", QA_ISSUE)
    scripted_llm.queue("ManimCodeFix", {"code": BROKEN})
    result = await render.render_manim(job_ctx, make_request(code=FIXED, beats=3))
    assert not result.healed and result.final_spec.code == FIXED and len(result.qa_issues) == 1
    assert any("did not render" in w for w in warnings(job_ctx))


async def test_visual_qa_failures_are_advisory(job_ctx, fake_runner, fast_media, scripted_llm, make_request) -> None:
    settings_with(job_ctx, manim_visual_qa=True)
    scripted_llm.queue("FrameReview", ProviderError("vision down", provider="fake"))
    result = await render.render_manim(job_ctx, make_request("wave", example("wave"), beats=4))
    assert result.qa_issues == [] and any("visual QA skipped" in w for w in warnings(job_ctx))


async def test_conform_failure_is_a_manim_error(job_ctx, fake_runner, fast_media, make_request, monkeypatch) -> None:
    from aadhi.manim import media

    async def broken(src: Path, dst: Path, total: float, settings) -> media.VideoInfo:
        raise media.MediaError("ffmpeg failed: corrupt input")

    monkeypatch.setattr(media, "conform", broken)
    with pytest.raises(ManimError, match="post-processing"):
        await render.render_manim(job_ctx, make_request("wave", example("wave"), beats=4))


async def test_missing_latex_is_reported_and_uses_plain_text(job_ctx, fake_runner, fast_media, make_request) -> None:
    fake_runner.latex = False
    result = await render.render_manim(job_ctx, make_request("equation_steps", example("equation_steps"), beats=4))
    assert "'has_latex': False" in fake_runner.scripts[0] and len(fake_runner.scripts) == 1
    assert job_ctx.assets.get(result.asset_key).meta["latex"] is False
    assert any("LaTeX is not available" in w for w in warnings(job_ctx))


async def test_cancelling_the_job_mid_render_stops_the_sandbox(job_ctx, fast_media, make_request, monkeypatch) -> None:
    import time

    observed: dict[str, bool] = {}

    class SlowRunner:
        name = "subprocess"

        def has_latex(self) -> bool:
            return True

        async def run(self, script, scene, *, workdir, quality, timeout):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                observed["cancelled"] = True  # the real runners kill the process tree here
                raise

    async def cancel_soon() -> None:
        await asyncio.sleep(0.3)
        job_ctx.cancelled = True

    monkeypatch.setattr(render, "get_runner", lambda settings: SlowRunner())
    started = time.monotonic()
    with pytest.raises(JobCancelled):
        await asyncio.gather(render.render_manim(job_ctx, make_request("wave", example("wave"), beats=4)),
                             cancel_soon())
    assert observed == {"cancelled": True} and time.monotonic() - started < 5


# --- template params are data (reviewer finding: labels with paths/URLs/dunders) -------------------


async def test_template_labels_with_paths_urls_and_dunders_render(job_ctx, fake_runner, fast_media,
                                                                  make_request) -> None:
    from aadhi.manim import validate_spec

    params = {
        "title": "Linux file system",
        "nodes": [
            {"id": "root", "label": "/", "step": 0},
            {"id": "home", "label": "/home", "step": 1},
            {"id": "etc", "label": "/etc", "step": 1},
            {"id": "init", "label": "__init__()", "step": 2},
            {"id": "api", "label": "https://api.example.com", "step": 2},
        ],
        "edges": [{"source": "root", "target": "home", "step": 1}, {"source": "root", "target": "etc", "step": 1},
                  {"source": "home", "target": "init", "step": 2}, {"source": "etc", "target": "api", "step": 2}],
        "notes": ["Everything starts at /", "C:\\Users is the Windows equivalent", "../ goes up one level"],
    }
    req = make_request("block_diagram", params, beats=3)
    assert validate_spec(req.spec, 3) == []
    result = await render.render_manim(job_ctx, req)
    assert not result.cached and len(fake_runner.scripts) == 1 and "'/home'" in fake_runner.scripts[0]

    timeline = {"title": "Python __main__ module",
                "events": [{"marker": "1", "title": "python -m pkg", "detail": "runs pkg/__main__.py"},
                           {"marker": "2", "title": "if __name__ == '__main__':", "detail": "see /usr/lib/python3"}]}
    req = make_request("timeline_steps", timeline, beats=2)
    assert validate_spec(req.spec, 2) == []
    assert not (await render.render_manim(job_ctx, req)).cached


# --- scene class names ----------------------------------------------------------------------------


async def test_underscore_scene_names_render(job_ctx, fake_runner, fast_media, make_request) -> None:
    code = FIXED.replace("class Demo(", "class Ohms_Law(")
    result = await render.render_manim(job_ctx, make_request(code=code, beats=3))
    assert not result.healed and fake_runner.calls[0]["scene"] == "Ohms_Law"


async def test_runner_argument_errors_go_through_the_repair_loop(job_ctx, fake_runner, fast_media, scripted_llm,
                                                                 make_request) -> None:
    """Reviewer probe: a runner ValueError must end as ManimError (after repairs), never escape as ValueError."""

    async def refuse(script, scene, *, workdir, quality, timeout):
        raise ValueError(f"invalid scene class name {scene!r}")

    fake_runner.run = refuse  # type: ignore[method-assign]
    scripted_llm.queue("ManimCodeFix", {"code": FIXED})
    with pytest.raises(ManimError, match="invalid scene class name") as info:
        await render.render_manim(job_ctx, make_request(code=FIXED, beats=3))
    assert len(scripted_llm.calls_for("ManimCodeFix")) == job_ctx.settings.manim_max_repair_attempts
    assert "invalid scene class name" in scripted_llm.calls_for("ManimCodeFix")[0]["prompt"]
    assert "invalid scene class name" in info.value.log_tail


async def test_run_rejects_unusable_scene_names_without_rendering(job_ctx, fake_runner, make_request,
                                                                  tmp_path: Path) -> None:
    req = make_request(code=FIXED, beats=3)
    for source in ("from manim import *\nclass Ohm\u00e9(AadhiScene):\n    pass\n", "x = 1\n"):
        result = await render._run(job_ctx, fake_runner, req, source, has_latex=True, workdir=tmp_path)
        assert not result.ok and "[AADHI_ERROR]" in result.log
    assert fake_runner.scripts == []


async def test_template_runner_argument_error_is_a_manim_error(job_ctx, fake_runner, fast_media,
                                                                make_request) -> None:
    async def refuse(script, scene, *, workdir, quality, timeout):
        raise ValueError("invalid manim quality")

    fake_runner.run = refuse  # type: ignore[method-assign]
    with pytest.raises(ManimError, match="invalid manim quality"):
        await render.render_manim(job_ctx, make_request("wave", example("wave"), beats=4))


# --- visual QA is advisory --------------------------------------------------------------------------


async def test_qa_repair_conform_failure_keeps_the_original(job_ctx, fake_runner, fast_media, scripted_llm,
                                                            make_request, monkeypatch) -> None:
    """Reviewer probe: conform of the QA-repaired video fails -> the original video is kept and stored."""
    from aadhi.manim import media

    settings_with(job_ctx, manim_visual_qa=True)
    scripted_llm.queue("FrameReview", QA_ISSUE)
    scripted_llm.queue("ManimCodeFix", {"code": POLISHED})
    real_conform = media.conform
    calls = {"n": 0}

    async def flaky_conform(src: Path, dst: Path, total: float, settings) -> media.VideoInfo:
        calls["n"] += 1
        if calls["n"] == 2:
            raise media.MediaError("ffprobe failed: moov atom not found")
        return await real_conform(src, dst, total, settings)

    monkeypatch.setattr(media, "conform", flaky_conform)
    req = make_request(code=FIXED, beats=3)
    result = await render.render_manim(job_ctx, req)
    assert not result.healed and result.final_spec.code == FIXED and len(result.qa_issues) == 1
    assert job_ctx.assets.get(result.asset_key) is not None and len(fake_runner.scripts) == 2
    assert any("QA repair failed; keeping the original video" in w and "moov atom" in w for w in warnings(job_ctx))


async def test_qa_repair_runner_exception_keeps_the_original(job_ctx, fake_runner, fast_media, scripted_llm,
                                                             make_request) -> None:
    settings_with(job_ctx, manim_visual_qa=True)
    scripted_llm.queue("FrameReview", QA_ISSUE)
    scripted_llm.queue("ManimCodeFix", {"code": POLISHED})
    real_run = fake_runner.run

    async def crash_on_second(script, scene, *, workdir, quality, timeout):
        if fake_runner.scripts:
            raise RuntimeError("docker daemon went away")
        return await real_run(script, scene, workdir=workdir, quality=quality, timeout=timeout)

    fake_runner.run = crash_on_second  # type: ignore[method-assign]
    result = await render.render_manim(job_ctx, make_request(code=FIXED, beats=3))
    assert not result.healed and result.final_spec.code == FIXED
    assert any("docker daemon went away" in w for w in warnings(job_ctx))


async def test_qa_repair_cancellation_propagates(job_ctx, fake_runner, fast_media, scripted_llm,
                                                make_request) -> None:
    settings_with(job_ctx, manim_visual_qa=True)
    scripted_llm.queue("FrameReview", QA_ISSUE)

    def fix_then_cancel(prompt: str) -> dict:
        job_ctx.cancelled = True
        return {"code": POLISHED}

    scripted_llm.queue("ManimCodeFix", fix_then_cancel)
    with pytest.raises(JobCancelled):
        await render.render_manim(job_ctx, make_request(code=FIXED, beats=3))


# --- host paths never reach the model or the user ------------------------------------------------


async def test_host_paths_are_scrubbed_from_prompts_and_errors(job_ctx, fast_media, scripted_llm, make_request,
                                                               monkeypatch) -> None:
    import tempfile

    home = str(Path.home())

    class LeakyRunner:
        name = "subprocess"

        def has_latex(self) -> bool:
            return True

        async def run(self, script, scene, *, workdir, quality, timeout):
            from aadhi.manim.sandbox import SandboxResult

            log = "\n".join([
                "Traceback (most recent call last):",
                f'  File "{Path(workdir) / "scene.py"}", line 400, in <module>',
                f'  File "{Path(sys.prefix) / "Lib" / "site-packages" / "manim" / "scene" / "scene.py"}", line 9',
                f"  media at {Path(tempfile.gettempdir()) / 'aadhi-m-zz' / 'videos'}",
                f"  config read from {home}\\.config\\manim\\manim.cfg",
                "ImportError: cannot import name 'Circl'",
            ])
            return SandboxResult(ok=False, returncode=1, video_path=None, log=log)

    monkeypatch.setattr(render, "get_runner", lambda settings: LeakyRunner())
    settings_with(job_ctx, manim_max_repair_attempts=1)
    scripted_llm.queue("ManimCodeFix", {"code": FIXED})
    with pytest.raises(ManimError) as info:
        await render.render_manim(job_ctx, make_request(code=FIXED, beats=3))
    prompt = scripted_llm.calls_for("ManimCodeFix")[0]["prompt"]
    leaks = (tempfile.gettempdir(), sys.prefix, home, Path.home().name)
    for text in (prompt, info.value.log_tail, str(info.value), *(str(e["data"]) for e in job_ctx.events)):
        for leak in leaks:
            assert leak.lower() not in text.lower(), (leak, text[:300])
    assert "<work>" in prompt and "<python>" in prompt and "<home>" in prompt and "cannot import name" in prompt


def test_logs_hide_a_custom_scratch_root(app_env, tmp_path: Path) -> None:
    # SCRATCH_DIR may live outside the temp dir and the home dir (e.g. a host-shared mount): still hidden.
    scratch = tmp_path / "srv" / "aadhi-scratch"
    settings = app_env.model_copy(update={"scratch_dir": str(scratch)})
    work = scratch / "aadhi-manim-abc"
    log = f"media at {scratch / 'aadhi-m-x1' / 'videos'}\nscript {work / 'scene.py'}"
    clean = render._clean(settings, log, work)
    assert str(scratch).lower() not in clean.lower()
    assert "<scratch>" in clean and "<work>" in clean


# --- no file I/O on the event loop --------------------------------------------------------------------


def test_file_backed_caches_are_warm_after_import() -> None:
    from aadhi.manim import aadhi_scene, prompts
    from aadhi.manim.templates import _base

    assert aadhi_scene.runtime_source.cache_info().currsize == 1
    assert _base.scene_file_source.cache_info().currsize >= len(registry)
    assert prompts._raw.cache_info().currsize >= 3
    assert render.source_revision.cache_info().currsize >= len(registry) + 1
    assert render.manim_version.cache_info().currsize == 1


async def test_work_dir_is_created_off_the_event_loop(job_ctx, fake_runner, fast_media, make_request,
                                                      monkeypatch) -> None:
    import tempfile
    import threading
    from types import SimpleNamespace

    threads: list[bool] = []

    def mkdtemp(*args: Any, **kwargs: Any) -> str:
        threads.append(threading.current_thread() is threading.main_thread())
        return tempfile.mkdtemp(*args, **kwargs)

    monkeypatch.setattr(render, "tempfile", SimpleNamespace(mkdtemp=mkdtemp))
    await render.render_manim(job_ctx, make_request("wave", example("wave"), beats=4))
    assert threads == [False]
