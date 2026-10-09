"""Classified render failures: ``ManimError.category`` / ``.friendly`` and how render.py sets them."""

from __future__ import annotations

import pytest

from aadhi.manim.base import FAILURE_CATEGORIES, FAILURE_MESSAGES, ManimError
from aadhi.manim.sandbox import SandboxResult
from aadhi.manim.templates import registry


def _example(name: str) -> dict:
    import copy

    return copy.deepcopy(registry[name].example_params)


def test_manim_error_category_and_friendly() -> None:
    err = ManimError("boom", category="timeout")
    assert err.category == "timeout" and err.friendly == FAILURE_MESSAGES["timeout"]
    assert ManimError("x").category == "render_failed"  # default
    assert ManimError("x", category="not-a-real-category").category == "render_failed"  # unknown -> default
    for cat in FAILURE_CATEGORIES:
        assert ManimError("x", category=cat).friendly  # every category has a message


def _fail_log(log: str, rc: int = 1):
    return SandboxResult(ok=False, returncode=rc, video_path=None, log=log)


async def test_render_sets_category_from_the_sandbox_result(job_ctx, fake_runner, fast_media, make_request) -> None:
    # a timeout kill -> timeout category
    fake_runner.killed_reason = "timeout"
    req = make_request("equation_steps", _example("equation_steps"), beats=4)
    with pytest.raises(ManimError) as info:
        await _import_render().render_manim(job_ctx, req)
    assert info.value.category == "timeout" and info.value.friendly == FAILURE_MESSAGES["timeout"]


async def test_render_classifies_a_blocked_template(job_ctx, fake_runner, fast_media, make_request) -> None:
    fake_runner.fail_when = lambda script: "noise\n[AADHI_BLOCKED] open for writing\nPermissionError: blocked"
    req = make_request("equation_steps", _example("equation_steps"), beats=4)
    with pytest.raises(ManimError) as info:
        await _import_render().render_manim(job_ctx, req)
    assert info.value.category == "blocked"


async def test_render_classifies_freeform_unsafe_code(job_ctx, fake_runner, fast_media, make_request) -> None:
    job_ctx.settings = job_ctx.settings.model_copy(update={"manim_max_repair_attempts": 0})
    req = make_request(code="import os\n\n\nclass Demo(AadhiScene):\n    def construct(self):\n        os.system('x')\n",
                       beats=1)
    with pytest.raises(ManimError) as info:
        await _import_render().render_manim(job_ctx, req)
    assert info.value.category == "unsafe_code"


async def test_render_classifies_invalid_request(job_ctx, fake_runner, fast_media, make_request) -> None:
    req = make_request("equation_steps", _example("equation_steps"), beats=4)
    req.total_duration = -1.0
    with pytest.raises(ManimError) as info:
        await _import_render().render_manim(job_ctx, req)
    assert info.value.category == "invalid_request"


def test_sandbox_result_category_matches_the_known_set() -> None:
    for log, expected in (
        ("[AADHI_LIMIT] frames\nRuntimeError", "frame_limit"),
        ("[AADHI_LIMIT] profile\nRuntimeError", "profile_limit"),
        ("[AADHI_BLOCKED] open\nPermissionError", "blocked"),
        ("! LaTeX Error: File `x.sty' not found.", "latex"),
    ):
        assert _fail_log(log).category == expected
        assert _fail_log(log).category in FAILURE_CATEGORIES


def _import_render():
    from aadhi.manim import render

    return render


async def test_a_video_of_the_wrong_size_is_invalid_output(job_ctx, fake_runner, make_request, monkeypatch) -> None:
    from tests.manim.fakes import install_fast_media

    install_fast_media(monkeypatch, width=640, height=360)  # not the profile's 854x480
    req = make_request("equation_steps", _example("equation_steps"), beats=4)
    with pytest.raises(ManimError) as info:
        await _import_render().render_manim(job_ctx, req)
    assert info.value.category == "invalid_output" and "640x360" in str(info.value)


async def test_render_metrics_are_stored_with_the_asset(job_ctx, fake_runner, fast_media, make_request,
                                                        monkeypatch) -> None:
    import dataclasses

    real_run = fake_runner.run

    async def run(*args, **kwargs):
        result = await real_run(*args, **kwargs)
        return dataclasses.replace(result, peak_memory_mb=123.44, frames=90)

    monkeypatch.setattr(fake_runner, "run", run)
    req = make_request("equation_steps", _example("equation_steps"), beats=4)
    res = await _import_render().render_manim(job_ctx, req)
    meta = job_ctx.assets.get(res.asset_key).meta
    assert meta["peak_memory_mb"] == 123.4 and meta["frames"] == 90 and meta["render_seconds"] >= 0
