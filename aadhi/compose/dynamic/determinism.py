"""Frame hashes of the stage rendered in different orders (DYNAMIC_VIDEO_DESIGN §7.4, test (a)).

Every visual property must be a pure function of ``(timeline, frame)``, so the same frame captured by any worker, in
any chunk and in any order gives the same bytes. :func:`standard_plans` gives the three orders the design names for
300 frames: one page rendering ``1x300`` in order, three pages rendering ``3x100`` concurrently (as three render
workers would) and one page rendering the frames in ``reverse``. :func:`run_plans` captures each plan (CDP JPEG q92,
the production capture) on fresh pages and :func:`DeterminismReport.mismatches` lists every frame whose SHA-256
differs between the plans; page errors and timer-guard trips are collected too (a trip fails the run, §7.4 rule 3).

:func:`spot_frames` and :func:`spot_check` are the optional post-render spot check of §7.12 (a warning, never an
error): the engine keeps the hashes of a few frames it captured and a fresh page captures them again.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field

from ..aio import gather_or_cancel
from .stage_page import StagePage


@dataclass(frozen=True)
class Plan:
    """A render order: each chunk is a frame sequence captured on its own fresh page (chunks run concurrently)."""

    name: str
    chunks: tuple[tuple[int, ...], ...]

    @property
    def frames(self) -> set[int]:
        return {f for chunk in self.chunks for f in chunk}


def standard_plans(start: int = 0, count: int = 300, parts: int = 3) -> list[Plan]:
    """``1x<count>`` in order, ``<parts>x<count/parts>`` (contiguous chunks) and the reverse order."""
    if count < 1 or parts < 1:
        raise ValueError("count and parts must be positive")
    frames = list(range(start, start + count))
    size = math.ceil(count / parts)
    return [
        Plan(f"1x{count}", (tuple(frames),)),
        Plan(f"{parts}x{size}", tuple(tuple(frames[i:i + size]) for i in range(0, count, size))),
        Plan("reverse", (tuple(reversed(frames)),)),
    ]


async def hash_frames(page: StagePage, frames: Iterable[int]) -> dict[int, str]:
    """SHA-256 of the captured JPEG of each frame, seeking in the given order."""
    out: dict[int, str] = {}
    for frame in frames:
        await page.seek(frame)
        out[frame] = hashlib.sha256(await page.capture_jpeg()).hexdigest()
    return out


@dataclass
class DeterminismReport:
    """Hashes per plan, plus the page errors and timer-guard trips seen while capturing."""

    hashes: dict[str, dict[int, str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    guard_trips: list[str] = field(default_factory=list)

    def mismatches(self) -> list[dict[str, object]]:
        """``[{frame, hashes: {plan: sha}}]`` for every frame the plans disagree on (or one plan lacks)."""
        frames = sorted({f for h in self.hashes.values() for f in h})
        out = []
        for f in frames:
            seen = {name: h.get(f) for name, h in self.hashes.items()}
            if len(set(seen.values())) > 1:
                out.append({"frame": f, "hashes": seen})
        return out

    @property
    def ok(self) -> bool:
        return not self.mismatches() and not self.guard_trips and not self.errors

    def distinct_frames(self) -> int:
        """How many different images the first plan produced (a stage that never changes proves nothing)."""
        first = next(iter(self.hashes.values()), {})
        return len(set(first.values()))


async def run_plans(open_page: Callable[[], AbstractAsyncContextManager[StagePage]],
                    plans: Sequence[Plan]) -> DeterminismReport:
    """Capture every plan; each chunk on a fresh page from ``open_page`` (an async context manager factory), the
    chunks of one plan concurrently, the plans one after another."""
    report = DeterminismReport()

    async def chunk(frames: Sequence[int]) -> dict[int, str]:
        async with open_page() as page:
            try:
                return await hash_frames(page, frames)
            finally:
                report.errors.extend(page.errors)
                report.guard_trips.extend(page.guard_trips)

    for plan in plans:
        parts = await gather_or_cancel([chunk(c) for c in plan.chunks])
        merged: dict[int, str] = {}
        for part in parts:
            merged.update(part)
        report.hashes[plan.name] = merged
    return report


def spot_frames(frames: int, count: int = 3) -> list[int]:
    """``count`` frames spread over ``[0, frames)`` (the centres of equal parts) for the post-render spot check."""
    if frames < 1 or count < 1:
        return []
    return sorted({min(frames - 1, (2 * k + 1) * frames // (2 * count)) for k in range(count)})


async def spot_check(page: StagePage, expected: Mapping[int, str]) -> list[int]:
    """Capture the frames of ``expected`` (``{frame: sha256 of the JPEG captured for the MP4}``) again on ``page``;
    returns the frames whose bytes differ (empty = deterministic)."""
    again = await hash_frames(page, sorted(expected))
    return [f for f in sorted(expected) if again.get(f) != expected[f]]
