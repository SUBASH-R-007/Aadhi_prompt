"""v1 narration strings -> v2 beat drafts.

v1 narration used inline markers: ``[SYNC]`` revealed the next sync-able board element and
``[PAUSE]`` / ``[PAUSE:n]`` inserted silence (bare pause = 1.5 s). Conversion:

* split on ``[SYNC]``: chunk 0 is the intro (reveals nothing); chunk ``i`` reveals sync slot ``i-1``
  (if it exists); surplus chunks reveal nothing;
* split every chunk on pause markers: each spoken segment becomes a beat whose ``pause_after`` is the
  following silence; the first beat of a chunk carries the chunk's reveal;
* beats longer than ~350 characters are split at sentence boundaries;
* markdown asterisks and stray HTML tags are stripped (narration is plain spoken text);
* a chunk with no speech still reveals its item through a beat that reads the item aloud when that
  item has readable text (otherwise the item simply stays visible from the scene start).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["BeatDraft", "build_beats", "cap_beats", "clean_narration", "split_long"]

DEFAULT_PAUSE = 1.5
MAX_PAUSE = 8.0
SOFT_BEAT_CHARS = 350
MAX_BEAT_CHARS = 1500
_SYNC_RE = re.compile(r"\[\s*SYNC\s*\]", re.IGNORECASE)
_PAUSE_RE = re.compile(r"\[\s*PAUSE(?:\s*:\s*(\d+(?:\.\d+)?)\s*s?)?\s*\]", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"'”’)\]]*\s+")


@dataclass
class BeatDraft:
    """A beat before ids are assigned. ``slot`` indexes the v1 sync slots (None = no reveal)."""

    narration: str
    pause_after: float = 0.0
    slot: int | None = None


def clean_narration(text: str) -> str:
    """Plain spoken text: no HTML tags, no markdown emphasis/asterisks, collapsed whitespace."""
    t = text or ""
    t = re.sub(r"<[^<>]{0,300}>", " ", t)
    t = re.sub(r"(\*{1,3})(?=\S)(.+?)(?<=\S)\1", r"\2", t)
    t = t.replace("*", "")
    t = _SYNC_RE.sub(" ", t)
    t = _PAUSE_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _hard_split(sentence: str, limit: int) -> list[str]:
    out: list[str] = []
    rest = sentence
    while len(rest) > limit:
        cut = max(rest.rfind(", ", 0, limit), rest.rfind("; ", 0, limit), rest.rfind(" - ", 0, limit))
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        out.append(rest[: cut + 1].strip())
        rest = rest[cut + 1 :].strip()
    if rest:
        out.append(rest)
    return out


def split_long(text: str, soft: int = SOFT_BEAT_CHARS, hard: int = 1400) -> list[str]:
    """Split ``text`` into pieces of <= ``soft`` chars at sentence boundaries (<= ``hard`` always)."""
    text = text.strip()
    if len(text) <= soft:
        return [text] if text else []
    sentences: list[str] = []
    for s in _SENTENCE_END.split(text):
        s = s.strip()
        if s:
            sentences.extend(_hard_split(s, hard) if len(s) > hard else [s])
    pieces: list[str] = []
    current = ""
    for s in sentences:
        candidate = f"{current} {s}".strip()
        if current and len(candidate) > soft:
            pieces.append(current)
            current = s
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _segments(chunk: str) -> list[tuple[str, float]]:
    """``[(text, pause_after)]`` for one [SYNC] chunk (text may be empty for pause-only runs)."""
    out: list[tuple[str, float]] = []
    pos = 0
    for m in _PAUSE_RE.finditer(chunk):
        pause = float(m.group(1)) if m.group(1) else DEFAULT_PAUSE
        out.append((chunk[pos : m.start()], pause))
        pos = m.end()
    out.append((chunk[pos:], 0.0))
    return out


def build_beats(
    narration: str,
    *,
    n_slots: int = 0,
    readable: Callable[[int], str] | None = None,
    warnings: list[str] | None = None,
    where: str = "",
) -> list[BeatDraft]:
    """Convert a v1 narration string into beat drafts (see module docstring)."""
    drafts: list[BeatDraft] = []
    chunks = _SYNC_RE.split(narration or "")
    if len(chunks) - 1 > n_slots > 0 and warnings is not None:
        warnings.append(f"{where}: narration has more [SYNC] markers than board elements; extras ignored")
    for ci, chunk in enumerate(chunks):
        slot = ci - 1 if 1 <= ci <= n_slots else None
        pending = slot
        for text, pause in _segments(chunk):
            spoken = clean_narration(text)
            if not spoken:
                if drafts:
                    drafts[-1].pause_after += pause
                continue
            for piece in split_long(spoken):
                drafts.append(BeatDraft(piece, 0.0, pending))
                pending = None
            drafts[-1].pause_after += pause
        if pending is not None:  # a [SYNC] chunk without speech
            text = readable(pending) if readable is not None else ""
            if text:
                drafts.append(BeatDraft(split_long(text, soft=MAX_BEAT_CHARS - 100)[0], 0.0, pending))
            elif warnings is not None:
                warnings.append(f"{where}: a [SYNC] marker had no narration; its board item is shown from the start")
    for d in drafts:
        d.pause_after = round(min(max(d.pause_after, 0.0), MAX_PAUSE), 2)
    return drafts


def cap_beats(
    drafts: list[BeatDraft], max_beats: int, warnings: list[str] | None = None, where: str = ""
) -> list[BeatDraft]:
    """Merge adjacent beats until at most ``max_beats`` remain (reveals preserved where possible)."""
    drafts = [BeatDraft(d.narration, d.pause_after, d.slot) for d in drafts]
    dropped_reveal = False
    while len(drafts) > max_beats:
        best: tuple[int, int] | None = None  # (combined length, index)
        for allow_reveal_loss in (False, True):
            for i in range(len(drafts) - 1):
                nxt = drafts[i + 1]
                if nxt.slot is not None and not allow_reveal_loss:
                    continue
                combined = len(drafts[i].narration) + 1 + len(nxt.narration)
                if combined <= MAX_BEAT_CHARS and (best is None or combined < best[0]):
                    best = (combined, i)
            if best is not None:
                break
        if best is None:  # pragma: no cover - needs > max_beats beats of ~1500 chars each
            drafts = drafts[:max_beats]
            if warnings is not None:
                warnings.append(f"{where}: narration too long; trailing beats dropped")
            break
        i = best[1]
        nxt = drafts.pop(i + 1)
        if nxt.slot is not None:
            dropped_reveal = True
        drafts[i].narration = f"{drafts[i].narration} {nxt.narration}"
        drafts[i].pause_after = nxt.pause_after
    if dropped_reveal and warnings is not None:
        warnings.append(f"{where}: too many beats; some board items are shown from the start")
    return drafts
