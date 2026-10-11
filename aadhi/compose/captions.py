"""Caption cues: splitting narration into readable cues, and SRT / WebVTT serialisation.

Rules (shared by the web player, SRT/VTT downloads and MP4 burn-in):

* a cue holds at most ``MAX_LINES`` (2) lines of at most ``MAX_LINE_CHARS`` (42) characters (``split_caption``'s
  ``width`` sets another line length, e.g. for a narrower portrait caption band; the classic default is unchanged);
* a word longer than a line is hard-split; with ``clusters`` (the narrow Dynamic 9:16 band, where it is frequent) the
  split never cuts a grapheme cluster or an Indic conjunct, prefers a hyphen, and the pieces keep their word's timing;
* cues are timed by word timings (``TimedWord``) when available, otherwise spread over the
  given window proportionally to the character count of each token;
* cues never overlap and always have a positive duration after millisecond rounding.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence

from ..schemas.timeline import CaptionCue, TimedWord

MAX_LINE_CHARS = 42
MAX_LINES = 2
EST_SECONDS_PER_CHAR = 0.065  # speech-rate estimate used when nothing better is known
MIN_CUE_SECONDS = 0.04  # shorter cues are dropped (they would only flicker)
_CLOSE_GAP_SECONDS = 0.5  # gaps between consecutive cues shorter than this are closed
_SENTENCE_END = re.compile(r"[.!?;:।॥。]['\")\]]*$")  # incl. Devanagari danda
_WS = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Line wrapping
# ---------------------------------------------------------------------------


def _hard_split(token: str, width: int) -> list[str]:
    return [token[i : i + width] for i in range(0, len(token), width)] or [token]


_JOINERS = frozenset("\u200c\u200d")  # ZWNJ, ZWJ


def _unsafe_cut(token: str, i: int) -> bool:
    """Cutting ``token`` before index ``i`` would split a grapheme cluster or an Indic conjunct."""
    if not 0 < i < len(token):
        return False
    nxt, prev = token[i], token[i - 1]
    return (unicodedata.category(nxt)[0] == "M" or nxt in _JOINERS or prev in _JOINERS
            or unicodedata.combining(prev) == 9)  # never right after a virama / halant


def _cluster_split(token: str, width: int) -> list[str]:
    """Pieces of at most ``width`` code points, cut between grapheme clusters (after a hyphen in the second half
    of a line when there is one); a cluster longer than a line stays whole."""
    out: list[str] = []
    i = 0
    while len(token) - i > width:
        cut = i + width
        hyphen = token.rfind("-", i + 1, cut)
        if hyphen >= i + width // 2:
            cut = hyphen + 1
        while cut > i + 1 and _unsafe_cut(token, cut):
            cut -= 1  # back off: lines stay <= width
        if _unsafe_cut(token, cut):  # no safe point in the line: keep the whole cluster
            cut = i + width
            while cut < len(token) and _unsafe_cut(token, cut):
                cut += 1
        out.append(token[i:cut])
        i = cut
    if i < len(token):
        out.append(token[i:])
    return out or [token]


def tokenize(text: str, width: int = MAX_LINE_CHARS) -> list[str]:
    """Whitespace tokens of ``text``; tokens longer than a line are hard-split."""
    out: list[str] = []
    for tok in _WS.split((text or "").strip()):
        if tok:
            out.extend(_hard_split(tok, width) if len(tok) > width else [tok])
    return out


def tokens_with_owner(text: str, width: int = MAX_LINE_CHARS, *,
                      clusters: bool = False) -> tuple[list[str], list[int]]:
    """Like :func:`tokenize`, plus the index of the whitespace word each token comes from (a word split over a line
    gives several tokens of one owner). ``clusters`` splits long words between grapheme clusters."""
    split = _cluster_split if clusters else _hard_split
    toks: list[str] = []
    owner: list[int] = []
    for k, tok in enumerate(t for t in _WS.split((text or "").strip()) if t):
        parts = split(tok, width) if len(tok) > width else [tok]
        toks += parts
        owner += [k] * len(parts)
    return toks, owner


def wrap_tokens(tokens: Sequence[str], width: int = MAX_LINE_CHARS) -> list[str]:
    """Greedy word wrap of ``tokens`` into lines of at most ``width`` characters."""
    lines: list[str] = []
    cur = ""
    for tok in tokens:
        if not cur:
            cur = tok
        elif len(cur) + 1 + len(tok) <= width:
            cur = f"{cur} {tok}"
        else:
            lines.append(cur)
            cur = tok
    if cur:
        lines.append(cur)
    return lines


def balance_lines(tokens: Sequence[str], width: int = MAX_LINE_CHARS) -> list[str]:
    """Wrap ``tokens`` into the fewest lines; two-line results are balanced for readability."""
    lines = wrap_tokens(tokens, width)
    if len(lines) != 2:
        return lines
    best: tuple[int, list[str]] | None = None
    for k in range(1, len(tokens)):
        a, b = " ".join(tokens[:k]), " ".join(tokens[k:])
        if len(a) <= width and len(b) <= width:
            score = max(len(a), len(b))
            if best is None or score < best[0]:
                best = (score, [a, b])
    return best[1] if best else lines


def _fits(tokens: Sequence[str], width: int = MAX_LINE_CHARS) -> bool:
    return len(wrap_tokens(tokens, width)) <= MAX_LINES


# ---------------------------------------------------------------------------
# Splitting narration into cues
# ---------------------------------------------------------------------------


def _group_tokens(tokens: Sequence[str], width: int = MAX_LINE_CHARS) -> list[tuple[int, int]]:
    """Split token indices into cue groups ``[(start, end_exclusive)]``.

    A cue is closed when the next token would not fit into two lines of ``width`` characters, or after a
    sentence end once the cue is reasonably full (keeps cue boundaries on natural pauses).
    """
    groups: list[tuple[int, int]] = []
    start = 0
    capacity = width * MAX_LINES
    i = 0
    while i < len(tokens):
        if i > start and not _fits(tokens[start : i + 1], width):
            groups.append((start, i))
            start = i
            continue
        chars = len(" ".join(tokens[start : i + 1]))
        if _SENTENCE_END.search(tokens[i]) and chars >= capacity * 0.35 and i + 1 < len(tokens):
            groups.append((start, i + 1))
            start = i + 1
        i += 1
    if start < len(tokens):
        groups.append((start, len(tokens)))
    return groups


def _proportional_times(tokens: Sequence[str], start: float, end: float) -> list[tuple[float, float]]:
    """Distribute ``[start, end]`` over tokens proportionally to their length (+1 for the space)."""
    weights = [len(t) + 1 for t in tokens]
    total = float(sum(weights)) or 1.0
    span = max(0.0, end - start)
    out: list[tuple[float, float]] = []
    t = start
    for w in weights:
        d = span * w / total
        out.append((t, t + d))
        t += d
    return out


def estimate_words(text: str, start: float, end: float | None = None) -> list[TimedWord]:
    """Estimated word timings for ``text`` spoken from ``start`` (to ``end`` or at 0.065 s/char)."""
    tokens = tokenize(text, width=10_000)
    if not tokens:
        return []
    if end is None:
        end = start + max(0.3, len(" ".join(tokens)) * EST_SECONDS_PER_CHAR)
    return [TimedWord(text=tok, start=round(a, 3), end=round(b, 3)) for tok, (a, b) in
            zip(tokens, _proportional_times(tokens, start, end), strict=True)]


def _token_times(tokens: Sequence[str], words: Sequence[TimedWord],
                 owner: Sequence[int] | None = None) -> list[tuple[float, float]]:
    """Timing per caption token: word timings when they map 1:1, else proportional. With ``owner`` (the word each
    token comes from, :func:`tokens_with_owner`) the pieces of a split word share their word's timing, so the other
    words keep their exact times."""
    ws = sorted((w for w in words if w.end >= w.start), key=lambda w: w.start)
    if not ws:
        return []
    if owner and len(owner) == len(tokens) and len(ws) == owner[-1] + 1:
        out: list[tuple[float, float]] = []
        for k, w in enumerate(ws):
            out += _proportional_times([t for t, o in zip(tokens, owner, strict=True) if o == k], w.start,
                                       max(w.end, w.start))
        return out
    if len(ws) == len(tokens):
        return [(w.start, max(w.end, w.start)) for w in ws]
    return _proportional_times(tokens, ws[0].start, max(w.end for w in ws))


def split_caption(
    text: str,
    words: Sequence[TimedWord] | None,
    *,
    start: float | None = None,
    end: float | None = None,
    width: int = MAX_LINE_CHARS,
    clusters: bool = False,
) -> list[CaptionCue]:
    """Split ``text`` into cues of <= 2 lines x ``width`` (default 42) chars, timed by ``words``.

    ``words`` are the (written-form) word timings of ``text``; when they do not map 1:1 onto the
    text tokens their overall span is distributed proportionally. Without words, the cues are
    spread over ``[start, end]`` (``start`` defaults to 0; ``end`` to an estimate of 0.065 s/char).
    Consecutive cues separated by less than 0.5 s are joined end-to-start to avoid flicker.
    ``width`` (at least 1) is the line length in characters; tokens longer than a line are hard-split (with
    ``clusters``: between grapheme clusters, the pieces keeping their word's timing; see :func:`tokens_with_owner`).
    """
    width = max(1, int(width))
    if clusters:
        tokens, owner = tokens_with_owner(text, width, clusters=True)
    else:
        tokens, owner = tokenize(text, width), None
    if not tokens:
        return []
    times = _token_times(tokens, words or [], owner)
    if not times:
        s = 0.0 if start is None else float(start)
        e = s + len(" ".join(tokens)) * EST_SECONDS_PER_CHAR if end is None else float(end)
        times = _proportional_times(tokens, s, max(e, s))
    cues: list[CaptionCue] = []
    for a, b in _group_tokens(tokens, width):
        cue_start = times[a][0]
        cue_end = max(times[b - 1][1], cue_start)
        cues.append(CaptionCue(start=round(cue_start, 3), end=round(cue_end, 3),
                               text="\n".join(balance_lines(tokens[a:b], width))))
    for i in range(len(cues) - 1):
        nxt = cues[i + 1].start
        if nxt - cues[i].end < _CLOSE_GAP_SECONDS:
            cues[i] = cues[i].model_copy(update={"end": max(cues[i].start, nxt)})
    return normalize_cues(cues)


def shift_cues(cues: Iterable[CaptionCue], offset: float) -> list[CaptionCue]:
    """Return copies of ``cues`` shifted by ``offset`` seconds."""
    return [c.model_copy(update={"start": round(c.start + offset, 3), "end": round(c.end + offset, 3)}) for c in cues]


def normalize_cues(cues: Iterable[CaptionCue]) -> list[CaptionCue]:
    """Sort cues, clamp negatives, clip overlaps and drop cues that become too short."""
    items = sorted((c for c in cues if c.text.strip()), key=lambda c: (c.start, c.end))
    out: list[CaptionCue] = []
    for i, c in enumerate(items):
        s = max(0.0, c.start)
        e = max(s, c.end)
        if i + 1 < len(items):
            e = min(e, max(0.0, items[i + 1].start))
        if e - s < MIN_CUE_SECONDS:
            continue
        out.append(CaptionCue(start=round(s, 3), end=round(e, 3), text=c.text))
    return out


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _ms_pairs(cues: Sequence[CaptionCue]) -> list[tuple[int, int, str]]:
    """Millisecond cue times with overlaps removed after rounding (end <= next start, end > start)."""
    items = sorted(cues, key=lambda c: (c.start, c.end))
    raw = [(max(0, round(c.start * 1000)), max(0, round(c.end * 1000)), c.text) for c in items]
    out: list[tuple[int, int, str]] = []
    for i, (s, e, text) in enumerate(raw):
        if i + 1 < len(raw):
            e = min(e, raw[i + 1][0])
        if e <= s:
            continue
        out.append((s, e, text))
    return out


def _clock(ms: int, sep: str) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _clean_lines(text: str) -> list[str]:
    """Cue text as non-empty single-spaced lines (blank lines would terminate a cue)."""
    lines = [_WS.sub(" ", ln).strip() for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return [ln for ln in lines if ln]


_TAG_LIKE = re.compile(r"<\s*/?\s*[A-Za-z][^<>]*>")
_ASS_OVERRIDE = re.compile(r"\{\\[^{}]*\}")


def escape_srt_text(text: str) -> str:
    """Neutralise SRT/ASS markup so narration is always shown literally.

    SRT has no escape syntax; players interpret ``<i>``-style tags and ``{\\an8}`` override
    blocks (libass for burn-in). Such sequences are rendered harmless with look-alike brackets;
    ``-->`` (the timing separator) becomes ``->``.
    """
    lines = []
    for ln in _clean_lines(text):
        ln = ln.replace("-->", "->")
        ln = _TAG_LIKE.sub(lambda m: "‹" + m.group(0)[1:-1] + "›", ln)
        ln = _ASS_OVERRIDE.sub(lambda m: "(" + m.group(0)[1:-1] + ")", ln)
        lines.append(ln)
    return "\n".join(lines)


_WORD_JOINER = chr(0x2060)  # U+2060 WORD JOINER (invisible)


def escape_burn_in_text(text: str) -> str:
    """Make SRT cue text literal for libass (ffmpeg's ``subtitles`` filter burn-in).

    ffmpeg's SRT->ASS conversion passes backslashes and braces through and libass interprets
    ``\\N``/``\\n``/``\\h`` anywhere and ``{...}`` as override blocks (hidden). Every backslash is
    followed by an invisible word joiner (U+2060) and braces are escaped as ``\\{``/``\\}``.
    Only for burn-in: downloadable SRT/VTT files stay literal.
    """
    out = escape_srt_text(text).replace("\\", "\\" + _WORD_JOINER)
    return out.replace("{", "\\{").replace("}", "\\}")


def escape_vtt_text(text: str) -> str:
    """WebVTT cue text escaping (``&``, ``<``, ``>``) with blank lines removed."""
    out = []
    for ln in _clean_lines(text):
        out.append(ln.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return "\n".join(out)


def to_srt(cues: Sequence[CaptionCue], *, burn_in: bool = False) -> str:
    """Serialise cues as SubRip (``HH:MM:SS,mmm``), numbered from 1, CRLF-free.

    ``burn_in=True`` additionally escapes libass sequences (see :func:`escape_burn_in_text`); use it
    only for the file handed to ffmpeg's ``subtitles`` filter.
    """
    escape = escape_burn_in_text if burn_in else escape_srt_text
    blocks = []
    n = 0
    for s, e, text in _ms_pairs(list(cues)):
        body = escape(text)
        if not body:
            continue
        n += 1
        blocks.append(f"{n}\n{_clock(s, ',')} --> {_clock(e, ',')}\n{body}\n")
    return "\n".join(blocks)


def to_vtt(cues: Sequence[CaptionCue]) -> str:
    """Serialise cues as WebVTT (``HH:MM:SS.mmm``) with the mandatory header."""
    blocks = ["WEBVTT\n"]
    for s, e, text in _ms_pairs(list(cues)):
        body = escape_vtt_text(text)
        if not body:
            continue
        blocks.append(f"{_clock(s, '.')} --> {_clock(e, '.')}\n{body}\n")
    return "\n".join(blocks)
