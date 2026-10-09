"""Character-level alignment -> word timings."""

from __future__ import annotations

from collections.abc import Sequence

from ..base import WordTiming

_PUNCT = "\"'`.,;:!?()[]{}<>-–—…“”‘’«»/\\|*_~"


def words_from_characters(
    chars: Sequence[str], starts: Sequence[float], ends: Sequence[float]
) -> list[WordTiming]:
    """Group per-character timings into words (whitespace separated, punctuation trimmed).

    Tokens made only of punctuation are dropped. Mismatched sequence lengths are truncated to
    the shortest one.
    """
    n = min(len(chars), len(starts), len(ends))
    words: list[WordTiming] = []
    buf: list[tuple[str, float, float]] = []

    def flush() -> None:
        if not buf:
            return
        token = "".join(c for c, _, _ in buf)
        lead = len(token) - len(token.lstrip(_PUNCT))
        trail = len(token) - len(token.rstrip(_PUNCT))
        core = buf[lead : len(buf) - trail if trail else len(buf)]
        if core:
            words.append(
                WordTiming(text="".join(c for c, _, _ in core), start=float(core[0][1]), end=float(core[-1][2]))
            )
        buf.clear()

    for i in range(n):
        ch = chars[i]
        if not ch or ch.isspace():
            flush()
            continue
        buf.append((ch, float(starts[i]), float(ends[i])))
    flush()
    return words
