"""Visual QA for rendered Manim videos.

Four frames (the settled state at the end of beats, always including the final frame) are sent to
the vision model, which reports overlaps, text cut off by the frame edge, unreadable text and
empty frames. QA is advisory: provider/ffmpeg failures are logged and return no issues; budget
and cancellation errors propagate.
"""

from __future__ import annotations

from pathlib import Path

from ..jobs.base import BudgetExceeded, JobCancelled, JobContext
from ..providers.base import ImageInput
from . import llm as manim_llm
from . import media
from .base import ManimRenderRequest
from .prompts import load_prompt

QA_FRAMES = 4
SETTLE = 0.15  # seconds before the next beat: the step's animation has finished by then


def frame_times(request: ManimRenderRequest, duration: float, count: int = QA_FRAMES, fps: float = 30.0) -> list[float]:
    """Sorted times (seconds) of the ``count`` frames to review, last frame included.

    Preferred samples are the settled state of each beat (just before the next beat starts, when the
    step's animation has finished). With fewer beats than ``count`` the largest uncovered stretch is
    split in the middle until there are ``count`` samples. The last sample sits 1.5 frames before the
    end: the final frame's timestamp is ``duration - 1/fps`` and seeking past it yields no image.
    """
    count = max(int(count), 1)
    duration = max(float(duration), 1e-3)
    end = max(duration - 1.5 / max(float(fps or 30.0), 1.0), 0.0)
    beats = sorted(t for t in request.beat_times if 0.0 <= t < duration)
    settled = sorted({round(max(min(nxt - SETTLE, end), 0.0), 3) for nxt in beats[1:]} | {round(end, 3)})
    if len(settled) > count:
        if count == 1:
            return [settled[-1]]
        return [settled[round(k * (len(settled) - 1) / (count - 1))] for k in range(count)]
    picks = list(settled)
    min_gap = min(0.25, duration / (2 * count))
    while len(picks) < count:
        anchors = [0.0, *sorted(picks)]
        width, mid = max((b - a, (a + b) / 2) for a, b in zip(anchors, anchors[1:]))
        if width < 2 * min_gap:
            break
        picks.append(round(mid, 3))
    return sorted(picks)


def _prompt(request: ManimRenderRequest, times: list[float]) -> str:
    shape = "a portrait side panel (4:5)" if request.target == "panel" else "a full 16:9 frame"
    lines = [
        f"The animation fills {shape}. Title: {request.title or '(none)'}.",
        "Frames (in order):",
        *[f"- frame {i + 1} at t = {t:.1f} s" for i, t in enumerate(times)],
    ]
    cues = [c for c in request.beat_cues if c]
    if cues:
        lines.append("What the animation is meant to show: " + "; ".join(cues)[:800])
    lines.append("Report only clear visible defects.")
    return "\n".join(lines)


async def review_frames(ctx: JobContext, video_path: Path, request: ManimRenderRequest) -> list[str]:
    """Problems found in four frames of ``video_path`` ([] = looks fine or QA unavailable)."""
    settings = ctx.settings
    try:
        info = await media.probe(Path(video_path), settings)
        times = frame_times(request, info.duration, fps=info.fps)
        frames = await media.extract_frames(Path(video_path), times, settings)
        provider = manim_llm.get_llm(settings, request.llm_provider)
        review = await provider.generate_json(
            model=manim_llm.fast_model(settings, request.llm_provider),
            system=load_prompt("qa.md"),
            prompt=_prompt(request, times),
            schema=manim_llm.FrameReview,
            images=[ImageInput(data=f, mime="image/png") for f in frames],
            temperature=0.1,
            on_usage=ctx.record_usage,
            validation_retries=1,
        )
    except (BudgetExceeded, JobCancelled):
        raise
    except Exception as exc:  # QA is advisory: never fail the render because of it
        ctx.log(f"Manim visual QA skipped: {settings.redact(str(exc))[:300]}", "warning")
        return []
    problems = []
    for issue in review.issues:
        idx = min(max(issue.frame, 1), len(times)) - 1
        kind = issue.kind.replace("_", " ")
        problems.append(f"frame {issue.frame} (t={times[idx]:.1f}s) {kind}: {issue.description.strip()}")
    return problems
