"""Visual QA (frame sampling + vision review), prompts and LLM helpers."""

from __future__ import annotations

import pytest

from aadhi.jobs.base import BudgetExceeded
from aadhi.manim import qa
from aadhi.manim.base import ManimRenderRequest
from aadhi.manim.llm import FrameReview, ManimCodeFix, strip_code_fences
from aadhi.manim.prompts import freeform_rules, load_prompt, prompt_version
from aadhi.providers.base import ProviderError
from aadhi.schemas.screenplay import ManimSpec


def request(beats: list[float], total: float, **kw) -> ManimRenderRequest:
    return ManimRenderRequest(spec=ManimSpec(code="x"), beat_times=beats, total_duration=total, **kw)


# --- frame sampling ---------------------------------------------------------------------------------


@pytest.mark.parametrize("fps", [15.0, 30.0, 60.0])
def test_frame_times_use_settled_beat_states_and_a_safe_last_frame(fps: float) -> None:
    times = qa.frame_times(request([0.5, 2.5, 4.5, 6.5], 8.5), 8.5, fps=fps)
    assert times == [2.35, 4.35, 6.35, round(8.5 - 1.5 / fps, 3)]
    assert times[-1] < 8.5 - 1 / fps, "the last sample must precede the last frame's timestamp"


def test_frame_times_always_return_four_when_possible() -> None:
    assert qa.frame_times(request([0.5, 2.5, 4.5], 6.5), 6.5, fps=15) == [1.175, 2.35, 4.35, 6.4]
    assert qa.frame_times(request([], 8.0), 8.0, fps=30) == [1.988, 3.975, 5.963, 7.95]
    assert len(qa.frame_times(request([0.2], 0.3), 0.3, fps=15)) == 4


def test_frame_times_spread_many_beats() -> None:
    beats = [0.4 + 1.4 * i for i in range(9)]
    times = qa.frame_times(request(beats, 13.6), 13.6, fps=30)
    assert len(times) == 4 and times == sorted(times)
    assert times[0] == pytest.approx(1.65) and times[-1] == pytest.approx(13.55)


def test_frame_times_edge_cases() -> None:
    assert qa.frame_times(request([0.0], 0.05), 0.05, count=1) == [pytest.approx(0.0)]
    assert qa.frame_times(request([1.0, 2.0, 30.0], 3.0), 3.0, count=2, fps=30) == [1.85, 2.95]
    times = qa.frame_times(request([0.5, 0.5, 0.5], 2.0), 2.0, fps=30)
    assert len(times) == len(set(times)) and all(0 <= t < 2.0 for t in times)


# --- review_frames ----------------------------------------------------------------------------------


async def test_review_frames_formats_issues(job_ctx, fast_media, scripted_llm) -> None:
    scripted_llm.queue("FrameReview", {"issues": [
        {"frame": 1, "kind": "overlap", "description": "  title overlaps the formula  "},
        {"frame": 4, "kind": "unreadable", "description": "axis numbers too small"},
    ]})
    fast_media["conform"].append(("src", "dst", 6.5))  # probe reports 6.5 s
    req = request([0.5, 2.5, 4.5], 6.5, title="Ohm", target="panel", beat_cues=["battery", "", "current flows"])
    problems = await qa.review_frames(job_ctx, "video.mp4", req)
    assert problems == [
        "frame 1 (t=1.2s) overlap: title overlaps the formula",
        "frame 4 (t=6.4s) unreadable: axis numbers too small",
    ]
    (call,) = scripted_llm.calls
    assert call["images"] == 4 and call["model"] == job_ctx.settings.llm_model_fast
    assert "portrait side panel" in call["prompt"] and "Title: Ohm" in call["prompt"]
    assert "battery; current flows" in call["prompt"] and "frame 4 at t = 6.4 s" in call["prompt"]
    assert "overlap" in call["system"] and "cut_off" in call["system"]
    assert job_ctx.usages[0].operation == "vision"
    assert fast_media["frames"] == [[1.175, 2.35, 4.35, 6.4]]


async def test_review_frames_clamps_frame_numbers(job_ctx, fast_media, scripted_llm) -> None:
    scripted_llm.queue("FrameReview", {"issues": [{"frame": 4, "kind": "empty", "description": "blank"}]})
    fast_media["conform"].append(("src", "dst", 0.06))
    problems = await qa.review_frames(job_ctx, "video.mp4", request([0.0], 0.06))
    assert len(problems) == 1 and problems[0].startswith("frame 4 (t=")


async def test_review_frames_without_issues(job_ctx, fast_media, scripted_llm) -> None:
    scripted_llm.queue("FrameReview", {"issues": []})
    assert await qa.review_frames(job_ctx, "video.mp4", request([0.5], 3.0)) == []


async def test_review_frames_is_advisory(job_ctx, fast_media, scripted_llm) -> None:
    scripted_llm.queue("FrameReview", ProviderError("quota", provider="fake"))
    assert await qa.review_frames(job_ctx, "video.mp4", request([0.5], 3.0)) == []
    assert any(e["level"] == "warning" and "visual QA skipped: quota" in e["message"] for e in job_ctx.events)


async def test_review_frames_ffmpeg_failure_is_advisory(job_ctx, scripted_llm, monkeypatch) -> None:
    from aadhi.manim import media

    async def probe(path, settings):
        raise media.MediaError("ffprobe failed")

    monkeypatch.setattr(media, "probe", probe)
    assert await qa.review_frames(job_ctx, "missing.mp4", request([0.5], 3.0)) == []
    assert scripted_llm.calls == []


async def test_review_frames_propagates_budget_errors(job_ctx, fast_media, scripted_llm) -> None:
    scripted_llm.queue("FrameReview", BudgetExceeded("daily budget"))
    with pytest.raises(BudgetExceeded):
        await qa.review_frames(job_ctx, "video.mp4", request([0.5], 3.0))


# --- prompts and LLM helpers ------------------------------------------------------------------------


def test_prompts_are_versioned_and_expanded() -> None:
    assert prompt_version("fix.md") == "manim-fix-2"
    assert prompt_version("qa.md") == "manim-qa-1"
    assert prompt_version("freeform_rules.md") == "manim-freeform-rules-2"
    fix = load_prompt("fix.md")
    assert "PROMPT_VERSION" not in fix and "{{freeform_rules}}" not in fix
    assert freeform_rules() in fix
    rules = freeform_rules()
    for needle in ("AadhiScene", "wait_until_beat", "fit_to_safe_area", "self.label", 'r"', "SVGMobject"):
        assert needle in rules
    with pytest.raises(ValueError):
        load_prompt("../secrets.md")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("```python\nx = 1\n```", "x = 1\n"),
        ("```\nx = 1\n```\n", "x = 1\n"),
        ("x = 1", "x = 1\n"),
        ("\n\nx = 1\n\n", "x = 1\n"),
        ("```py\nprint('```')\n```", "print('```')\n"),
    ],
)
def test_strip_code_fences(raw: str, expected: str) -> None:
    assert strip_code_fences(raw) == expected


def test_llm_schemas_are_provider_compatible() -> None:
    schema_mod = pytest.importorskip("aadhi.providers.llm.schema")
    for model in (ManimCodeFix, FrameReview):
        schema_mod.assert_llm_compatible(model)
    with pytest.raises(ValueError):
        FrameReview.model_validate({"issues": [{"frame": 9, "kind": "overlap", "description": "x"}]})
