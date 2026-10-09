"""Chapter markers: YouTube description text and ffmpeg ``FFMETADATA1`` chapters."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence

from ..schemas.timeline import Chapter, Timeline

YOUTUBE_MIN_CHAPTER_SECONDS = 10.0
# Names of the synthetic chapter at 0:00, in order of preference (then "Opening 2", "Opening 3", ...).
OPENING_TITLES = ("Introduction", "Opening", "Lesson start")
_WS = re.compile(r"\s+")


def clean_title(title: str, *, max_len: int = 100) -> str:
    """Single-line, trimmed chapter title (YouTube titles are one line)."""
    t = _WS.sub(" ", title or "").strip()
    return t[:max_len].rstrip() or "Chapter"


def title_key(title: str) -> str:
    """Comparison key of a chapter title (whitespace collapsed, case folded)."""
    return clean_title(title).casefold()


def opening_title(taken: Iterable[str]) -> str:
    """Name for the synthetic chapter at 0:00 that no real chapter uses.

    ``taken`` are the real chapters' titles (compared with :func:`title_key`): "Introduction",
    else "Opening", else "Lesson start", else "Opening 2", "Opening 3", ...
    """
    keys = {title_key(t) for t in taken}
    for name in OPENING_TITLES:
        if name.casefold() not in keys:
            return name
    n = 2
    while f"opening {n}" in keys:
        n += 1
    return f"Opening {n}"


def format_timestamp(seconds: float, *, force_hours: bool = False) -> str:
    """``MM:SS`` (or ``H:MM:SS`` for long videos), floored to whole seconds."""
    total = max(0, math.floor(seconds + 1e-6))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h or force_hours:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def youtube_chapter_list(chapters: Sequence[Chapter], total_duration: float) -> list[Chapter]:
    """Chapters that satisfy YouTube's rules.

    The first chapter starts at 00:00 (an opening chapter is inserted if needed, named
    "Introduction" unless a real chapter already uses that name: see :func:`opening_title`),
    timestamps are unique and ascending, every chapter lasts at least 10 s (later chapters closer
    than 10 s to the previous kept one are skipped, as is a chapter starting less than 10 s before
    the end). An opening-named chapter at 00:00 that repeats the title of a later kept chapter
    (timelines built before the opening-name rule) is renamed the same way.
    """
    items = sorted(chapters, key=lambda c: c.start)
    out: list[Chapter] = []
    synthetic_intro = not items or math.floor(items[0].start + 1e-6) > 0
    if synthetic_intro:
        out.append(Chapter(start=0.0, title=opening_title(c.title for c in items)))
    for ch in items:
        start = float(math.floor(ch.start + 1e-6)) if out else 0.0
        if out and start - out[-1].start < YOUTUBE_MIN_CHAPTER_SECONDS:
            if synthetic_intro and len(out) == 1:  # a real chapter right after 0:00 names it
                out[0] = Chapter(start=0.0, title=clean_title(ch.title))
                synthetic_intro = False
            continue
        if out and total_duration > 0 and total_duration - start < YOUTUBE_MIN_CHAPTER_SECONDS:
            continue
        out.append(Chapter(start=start, title=clean_title(ch.title)))
    if len(out) > 1 and title_key(out[0].title) in {t.casefold() for t in OPENING_TITLES}:
        later = [c.title for c in out[1:]]
        if title_key(out[0].title) in {title_key(t) for t in later}:
            out[0] = Chapter(start=0.0, title=opening_title(later))
    return out


def youtube_chapters(timeline: Timeline) -> str:
    """YouTube description chapters (``00:00 Introduction`` ...), one per line."""
    chapters = youtube_chapter_list(timeline.chapters, timeline.total_duration)
    long_video = timeline.total_duration >= 3600
    return "\n".join(f"{format_timestamp(c.start, force_hours=long_video)} {c.title}" for c in chapters)


def _ffmeta_escape(value: str) -> str:
    """Escape ``=``, ``;``, ``#``, ``\\`` and newlines for the FFMETADATA1 format."""
    v = _WS.sub(" ", value or "").strip()
    return re.sub(r"([=;#\\])", r"\\\1", v)


def to_ffmetadata(chapters: Sequence[Chapter], total_duration: float, *, title: str | None = None,
                  extra: dict[str, str] | None = None) -> str:
    """FFMETADATA1 document with millisecond chapters covering ``[0, total_duration]``."""
    lines = [";FFMETADATA1"]
    if title:
        lines.append(f"title={_ffmeta_escape(title)}")
    for k, v in (extra or {}).items():
        if re.fullmatch(r"[a-z_]{1,32}", k) and v:
            lines.append(f"{k}={_ffmeta_escape(v)}")
    total_ms = max(0, round(total_duration * 1000))
    items = sorted(chapters, key=lambda c: c.start)
    starts = [min(max(0, round(c.start * 1000)), total_ms) for c in items]
    for i, ch in enumerate(items):
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(items) else total_ms
        if end <= start:
            continue
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={end}",
                  f"title={_ffmeta_escape(clean_title(ch.title, max_len=200))}"]
    return "\n".join(lines) + "\n"
