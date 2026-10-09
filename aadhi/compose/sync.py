"""Word-anchored synchronisation: board and side-panel moments placed on the spoken word (pure).

Computed once per scene by the timeline builder from the beats' word timings (``TimedBeat.words``:
the TTS provider's word boundaries, or the proportional estimate captions use when the provider gave
none) and played by ``web/js/player/schedule.js`` in the live player and on the render page alike,
so the preview and the MP4 show the same moments. Each cue is a ``SyncCue``:

* ``var``: a formula legend row appears when the narration names the symbol's meaning (or a Greek
  symbol's name) while it explains the formula: its reveal beat and the narration-only beats that
  follow it, or the beat named by ``FormulaVariable.beat_id``. A row that is never named stays with
  the formula, as before.
* ``emphasis``: an authored highlight (``Beat.highlight_item_ids``) starts when the beat names the
  item (its ``[[keyword]]``/``**bold**`` span, definition term, table column header, else its first
  distinctive English word) instead of at the beat start; a named table column or definition term is
  emphasised on its own, one column at a time. It is re-timed only when it is named at least
  ``MIN_EMPHASIS_SECONDS`` before its beat ends and was not already lit by the previous beat; otherwise
  it stays lit from the beat start (a named column or term is still emphasised at its word). One-letter
  anchors (a header "A", a term "V") are never used: they match the article or the pronoun. With
  ``SYNC_AUTO_EMPHASIS`` a visible item whose keyword, term or column the narration names is
  highlighted too (at most two per beat, one at a time, never in a beat that has authored highlights).
* ``output``: a terminal panel's output starts when the narration says what the program printed: only
  result forms count (``prints``, ``outputs``, ``returns``, ``displays`` ...; the bare keywords "print",
  "return" name the code being explained). Without an authored show beat, the beats up to and including
  the code's reveal beat are skipped (the code is being introduced). No match keeps the even spread.
* ``focus``: the side visual pulses (``FOCUS_SECONDS``) when the narration points at it ("look at the
  graph", "this diagram"), at most twice per scene. A plural-looking form ("maps", "models", "plots")
  counts as the visual only after a plural determiner ("these graphs"), and a subject pronoun between
  the pointing word and the noun ("here we map") makes it a verb. English cue words only.

Matching is on case-folded tokens; ASCII tokens tolerate an English plural ending, other tokens
(accented Latin, Indic scripts ...) must match exactly. Cue times closer than one video frame to a beat boundary or another cue are merged
onto it, so the MP4 gains no sub-frame states. No match means no cue, and a scene without cues plays
and renders exactly as before.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..pipeline.richlite import to_plain
from ..pipeline.richlite import tokenize as rich_spans
from ..schemas.screenplay import BoardItem, BoardItemKind
from ..schemas.timeline import ResolvedSidePanel, SyncCue, TimedBeat

FOCUS_SECONDS = 1.6  # length of a side-panel focus pulse
FOCUS_MIN_GAP_SECONDS = 4.0  # between two pulses
MAX_FOCUS_PULSES = 2
MAX_AUTO_EMPHASIS_PER_BEAT = 2
MAX_CUES_PER_SCENE = 64  # bounds the extra render states of a scene
MAX_PHRASE_TOKENS = 6
LOOK_BACK_TOKENS = 3
# An authored highlight named later than this before its beat ends, or already lit by the previous beat,
# stays lit from the beat start (re-timing it would shrink it to a flash or make it blink between beats).
MIN_EMPHASIS_SECONDS = 1.2

VISUAL_PANEL_KINDS = frozenset({"figure", "image", "chart", "graph", "model_3d", "manim", "gif"})
VISUAL_NOUNS = frozenset({
    "diagram", "figure", "picture", "image", "illustration", "photo", "photograph", "graph", "chart", "plot",
    "curve", "animation", "model", "map", "drawing", "sketch", "schematic", "visual",
})
LOOK_TRIGGERS = frozenset({"look", "see", "observe", "notice", "this", "these", "here", "shown"})
PLURAL_DETERMINERS = frozenset({"these", "those", "the", "both", "two", "three", "our", "all"})  # before a plural noun
SUBJECT_PRONOUNS = frozenset({"it", "we", "you", "they", "he", "she", "i"})  # "here we map": a verb, not the visual
# Result forms only: the bare keywords ("print", "return", "output", "display") name the code being explained.
OUTPUT_WORDS = frozenset({
    "prints", "printed", "outputs", "outputted", "returns", "returned",
    "displays", "displayed", "produces", "produced",
})
STOPWORDS = frozenset("""
a an the of to in on at by for from with without into onto over under and or nor but not no is are was were
be been being am it its this that these those there here which what who whom whose when where why how as than
then so if else per via each every any all some such same other another also only just very more most less
least much many can could will would shall should may might must do does did done has have had having we you
they he she i me my our your their his her them us let lets
""".split())

GREEK = frozenset({
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta", "vartheta", "iota",
    "kappa", "lambda", "mu", "nu", "xi", "pi", "rho", "sigma", "tau", "upsilon", "phi", "varphi", "chi", "psi",
    "omega",
})
_TEX_COMMAND = re.compile(r"\\([A-Za-z]+)")
_SPLIT = re.compile(r"[\s\-/\u2010-\u2015]+")
_PUNCT = ".,;:!?\"'()[]{}\u201c\u201d\u2018\u2019"  # stripped from a spoken word shown in the Studio


@dataclass(frozen=True)
class SyncOptions:
    """What the timeline builder computes (from ``Settings``)."""

    fps: int = 30
    auto_emphasis: bool = False

    @classmethod
    def from_settings(cls, settings: Any) -> SyncOptions | None:
        """Options from ``Settings``; None when word anchors are switched off."""
        if not getattr(settings, "sync_word_anchors", True):
            return None
        return cls(fps=max(1, int(getattr(settings, "render_fps", 30) or 30)),
                   auto_emphasis=bool(getattr(settings, "sync_auto_emphasis", False)))

    @property
    def frame(self) -> float:
        return 1.0 / self.fps


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def norm_token(text: str) -> str:
    """Case-folded letters, digits and combining marks (Indic vowel signs are kept)."""
    return "".join(ch for ch in (text or "").casefold()
                   if ch.isalnum() or unicodedata.category(ch).startswith("M"))


def stem(token: str) -> str:
    """Plural-insensitive form of an ASCII token (others unchanged)."""
    if not token.isascii() or len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("es") and token[:-2].endswith(("s", "x", "z", "ch", "sh")):
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokens(text: str) -> list[str]:
    """Normalised tokens of plain text."""
    return [t for t in (norm_token(p) for p in _SPLIT.split(text or "")) if t]


def is_content(token: str) -> bool:
    """A word that can name something: not a stopword, not a bare number, long enough."""
    if not token or token.isdigit():
        return False
    if token.isascii():
        return len(token) >= 3 and token not in STOPWORDS
    return len(token) >= 2


def _first_unique(words: Sequence[str], others: set[str]) -> list[str] | None:
    for w in words:
        if is_content(w) and stem(w) not in others:
            return [w]
    return None


def _phrase(text: str) -> list[str]:
    return tokens(text)[:MAX_PHRASE_TOKENS]


@dataclass(frozen=True)
class _Tok:
    norm: str
    stem: str
    raw: str
    start: float
    beat: int  # index into the scene's beats


class Stream(list[_Tok]):
    """Spoken tokens in time order, with the index range of each beat's tokens."""

    def __init__(self, toks: Sequence[_Tok]) -> None:
        super().__init__(toks)
        self.ranges: dict[int, tuple[int, int]] = {}
        for i, tok in enumerate(self):
            lo, _ = self.ranges.get(tok.beat, (i, i))
            self.ranges[tok.beat] = (lo, i + 1)


def word_stream(beats: Sequence[TimedBeat]) -> Stream:
    """Every spoken token of the scene in time order (words split at hyphens and slashes)."""
    out: list[_Tok] = []
    for i, b in enumerate(beats):
        for w in b.words:
            for piece in _SPLIT.split(w.text or ""):
                n = norm_token(piece)
                if n:
                    raw = piece.strip().strip(_PUNCT)
                    out.append(_Tok(n, stem(n), raw or piece, float(w.start), i))
    out.sort(key=lambda t: t.start)  # stable: keeps the word order inside a beat
    return Stream(out)


def _same(tok: _Tok, key: str) -> bool:
    return tok.norm == key or (key.isascii() and tok.stem == stem(key))


def find_phrase(stream: Stream, phrase: Sequence[str], beats: set[int]) -> int | None:
    """Index of the earliest occurrence of ``phrase`` inside one of ``beats`` (tokens of one beat)."""
    n = len(phrase)
    if not n:
        return None
    for b in sorted(beats):
        lo, hi = stream.ranges.get(b, (0, 0))
        for i in range(lo, hi - n + 1):
            tok = stream[i]
            if tok.beat == b and all(stream[i + k].beat == b and _same(stream[i + k], phrase[k]) for k in range(n)):
                return i
    return None


def _spoken(stream: Stream, i: int, n: int) -> str:
    return " ".join(t.raw for t in stream[i:i + n])[:80]


# ---------------------------------------------------------------------------
# Anchors of board items
# ---------------------------------------------------------------------------


def _audible(phrase: Sequence[str]) -> bool:
    """A phrase a listener can hear as a name: one-letter words alone ("A", [[V]]) are too ambiguous."""
    return any(len(t) >= 2 for t in phrase)


def marked_phrases(text: str | None) -> list[list[str]]:
    """Author-marked spans of rich-lite text (``[[keyword]]``, ``**bold**``) as token phrases."""
    out: list[list[str]] = []
    for span in rich_spans(text or ""):
        if span.kind in ("keyword", "bold"):
            p = _phrase(span.text)
            if _audible(p):  # a one-letter keyword ([[V]]) is too ambiguous to hear
                out.append(p)
    return out


def column_phrases(headers: Sequence[str]) -> list[list[str]]:
    """One phrase per table header: its first word no other header uses, else the whole header; a header
    of one-letter words only ("A", "B") has none (``[]``: the list stays aligned with the columns)."""
    words = [tokens(to_plain(h)) for h in headers]
    out: list[list[str]] = []
    for n, ws in enumerate(words):
        others = {stem(w) for k, o in enumerate(words) if k != n for w in o}
        p = _first_unique(ws, others) or ws[:MAX_PHRASE_TOKENS]
        out.append(p if _audible(p) else [])
    return out


def item_anchors(item: BoardItem, *, explicit_only: bool) -> list[tuple[str | None, list[str]]]:
    """``(part, phrase)`` pairs that name a board item, most specific first.

    Explicit anchors are the author's own: marked spans, a definition's term, table column headers.
    Otherwise (authored highlights only) the first distinctive English word of the item's text names it
    (only ASCII words have a stopword list: an Indic "this" or "is" would anchor anywhere).
    """
    out: list[tuple[str | None, list[str]]] = []
    if item.kind == BoardItemKind.table and len(item.headers or []) >= 2:
        out += [(f"column:{n}", p) for n, p in enumerate(column_phrases(item.headers or [])) if p]
    if item.kind == BoardItemKind.definition and item.term:
        p = _phrase(to_plain(item.term))
        if p and _audible(p):
            out.append(("term", p))
    texts = [item.text, item.caption if item.kind == BoardItemKind.figure else None]
    for text in texts:
        out += [(None, p) for p in marked_phrases(text)]
    if not explicit_only and not out:
        plain = to_plain(item.caption or "") if item.kind == BoardItemKind.figure else to_plain(item.text)
        word = _first_unique([t for t in tokens(plain) if t.isascii()], set())
        if word:
            out.append((None, word))
    return out


def variable_phrases(item: BoardItem) -> list[list[list[str]]]:
    """Per legend row, the phrases that name it: each word of its meaning that no other row uses
    ("wave speed": "wave" or "speed"; else the whole meaning), plus the name of a Greek symbol
    (``\\lambda`` -> "lambda")."""
    meanings = [tokens(v.meaning) for v in item.variables]
    out: list[list[list[str]]] = []
    for n, (var, ws) in enumerate(zip(item.variables, meanings, strict=True)):
        others = {stem(w) for k, o in enumerate(meanings) if k != n for w in o if is_content(w)}
        keys: list[list[str]] = []
        for w in ws:
            if is_content(w) and stem(w) not in others and [w] not in keys:
                keys.append([w])
        if not keys and ws:
            keys.append(ws[:MAX_PHRASE_TOKENS])
        for name in _TEX_COMMAND.findall(var.symbol_latex):
            if name.lower() in GREEK and [name.lower()] not in keys:
                keys.append([name.lower()])
        out.append(keys)
    return out


# ---------------------------------------------------------------------------
# Cue builders
# ---------------------------------------------------------------------------


class _Snap:
    """Merges cue times closer than a frame onto a beat boundary or an earlier cue time."""

    def __init__(self, anchors: Sequence[float], frame: float) -> None:
        self.fixed = sorted({round(a, 3) for a in anchors})
        self.chosen: list[float] = []
        self.frame = frame

    def __call__(self, t: float) -> float:
        t = round(float(t), 3)
        best: float | None = None
        for a in (*self.fixed, *self.chosen):
            if abs(t - a) < self.frame - 1e-9 and (best is None or abs(t - a) < abs(t - best)):
                best = a
        if best is not None:
            return best
        self.chosen.append(t)
        return t


def _reveal_beats(beats: Sequence[TimedBeat]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, b in enumerate(beats):
        if b.board_item_id and b.board_item_id not in out:
            out[b.board_item_id] = i
    return out


def explain_window(beats: Sequence[TimedBeat], reveal: int | None) -> list[int]:
    """Beats that explain an item: its reveal beat and the following narration-only main beats (from
    the first beat when the item is on the board from the start)."""
    out: list[int] = []
    for i in range(reveal or 0, len(beats)):
        b = beats[i]
        if b.phase != "main" or (i != reveal and (b.board_item_id or b.fill_item_id)):
            break
        out.append(i)
    return out


def _var_cues(beats: Sequence[TimedBeat], board: Sequence[BoardItem], stream: Stream,
              snap: _Snap) -> list[SyncCue]:
    reveal = _reveal_beats(beats)
    index = {b.beat_id: i for i, b in enumerate(beats) if b.phase == "main"}
    out: list[SyncCue] = []
    for item in board:
        if item.kind != BoardItemKind.formula or not item.variables:
            continue
        r = reveal.get(item.id)
        shown = beats[r].start if r is not None else 0.0
        window = set(explain_window(beats, r))
        for n, (var, keys) in enumerate(zip(item.variables, variable_phrases(item), strict=True)):
            where, fallback = window, None
            authored = index.get(var.beat_id or "")
            if authored is not None and (r is None or authored >= r):
                where, fallback = {authored}, beats[authored]
            hits = [(pos, len(k)) for k in keys if (pos := find_phrase(stream, k, where)) is not None]
            if hits:
                pos, size = min(hits, key=lambda h: stream[h[0]].start)
                at, beat_id, words = stream[pos].start, beats[stream[pos].beat].beat_id, _spoken(stream, pos, size)
            elif fallback is not None:
                at, beat_id, words = fallback.start, fallback.beat_id, ""
            else:
                continue
            at = snap(at)
            if at > shown:
                out.append(SyncCue(kind="var", start=at, item_id=item.id, part=f"var:{n}", beat_id=beat_id,
                                   words=words))
    return out


@dataclass
class _Mention:
    part: str | None
    start: float
    words: str


def _mentions(item: BoardItem, beat: int, stream: Stream, *, explicit_only: bool) -> list[_Mention]:
    """First mention of each anchor of ``item`` in one beat, in time order (one per part)."""
    found: dict[str | None, _Mention] = {}
    for part, phrase in item_anchors(item, explicit_only=explicit_only):
        i = find_phrase(stream, phrase, {beat})
        if i is None:
            continue
        m = _Mention(part, stream[i].start, _spoken(stream, i, len(phrase)))
        if part not in found or m.start < found[part].start:
            found[part] = m
    return sorted(found.values(), key=lambda m: (m.start, m.part or ""))


def _item_cues(item: BoardItem, beat: TimedBeat, mentions: list[_Mention], end: float, snap: _Snap,
               *, authored: bool, lead_in: bool = False) -> list[SyncCue]:
    """Cues for one named item in one beat: the item from its first mention until ``end``, a named
    column until the next column is named, a named term until ``end``. An authored highlight named
    less than ``MIN_EMPHASIS_SECONDS`` before ``end``, or lit by the previous beat (``lead_in``), stays
    lit from the beat start."""
    mentions = [m for m in mentions if m.start < end]
    if not mentions:
        return []
    starts = [snap(m.start) for m in mentions]
    named = min(starts)
    first = named
    if authored and (lead_in or end - named < MIN_EMPHASIS_SECONDS):
        first = beat.start  # lit through from the previous beat, or named too late to re-time
    out: list[SyncCue] = []
    columns = [(s, m) for s, m in zip(starts, mentions, strict=True) if (m.part or "").startswith("column:")]
    for k, (s, m) in enumerate(columns):
        stop = columns[k + 1][0] if k + 1 < len(columns) else end
        if stop > s:
            out.append(SyncCue(kind="emphasis", start=s, end=stop, item_id=item.id, part=m.part,
                               beat_id=beat.beat_id, words=m.words))
    for s, m in zip(starts, mentions, strict=True):
        if m.part == "term" and end > s:
            out.append(SyncCue(kind="emphasis", start=s, end=end, item_id=item.id, part="term",
                               beat_id=beat.beat_id, words=m.words))
    covered = min((c.start for c in out), default=None)
    if covered is None or first < covered:
        # the whole item; an authored highlight that would start with its beat anyway needs no cue
        if not (authored and not out and first <= beat.start):
            m = mentions[starts.index(named)]
            out.append(SyncCue(kind="emphasis", start=first, end=end if covered is None else covered,
                               item_id=item.id, beat_id=beat.beat_id, words=m.words))
    return [c for c in out if c.end is not None and c.end > c.start]


def _emphasis_cues(beats: Sequence[TimedBeat], board: Sequence[BoardItem], stream: Stream, snap: _Snap,
                   *, auto: bool) -> list[SyncCue]:
    items = {i.id: i for i in board}
    reveal = _reveal_beats(beats)
    out: list[SyncCue] = []
    for bi, beat in enumerate(beats):
        if beat.phase != "main":
            continue
        visible = {i for i in items if reveal.get(i, -1) <= bi}
        authored = [i for i in dict.fromkeys(beat.highlight_item_ids) if i in items and i in visible]
        if beat.highlight_item_ids:
            prev = beats[bi - 1] if bi > 0 and beats[bi - 1].phase == "main" else None
            for item_id in authored:
                ms = _mentions(items[item_id], bi, stream, explicit_only=False)
                out += _item_cues(items[item_id], beat, ms, beat.end, snap, authored=True,
                                  lead_in=prev is not None and item_id in prev.highlight_item_ids)
            continue
        if not auto:
            continue
        named: list[tuple[BoardItem, list[_Mention]]] = []
        for item_id in items:
            if item_id not in visible or item_id in (beat.board_item_id, beat.fill_item_id):
                continue
            ms = _mentions(items[item_id], bi, stream, explicit_only=True)
            if ms:
                named.append((items[item_id], ms))
        named.sort(key=lambda x: x[1][0].start)
        named = named[:MAX_AUTO_EMPHASIS_PER_BEAT]
        for k, (item, ms) in enumerate(named):
            stop = snap(named[k + 1][1][0].start) if k + 1 < len(named) else beat.end
            out += _item_cues(item, beat, ms, stop, snap, authored=False)
    return out


def _panel_cues(beats: Sequence[TimedBeat], panel: ResolvedSidePanel | None, duration: float,
                stream: Stream, snap: _Snap, board: Sequence[BoardItem] = ()) -> list[SyncCue]:
    if panel is None:
        return []
    kind = panel.panel.kind
    show_at = float(panel.show_at or 0.0)
    out: list[SyncCue] = []
    if kind == "terminal" and panel.panel.terminal is not None and panel.panel.terminal.output.strip():
        first_beat = 0  # an authored show beat (show_at) is trusted as is
        if not panel.panel.show_from_beat_id:
            reveal = _reveal_beats(beats)
            code = [reveal[i.id] for i in board if i.kind == BoardItemKind.code and i.id in reveal]
            if code:
                first_beat = min(code) + 1  # not while the code is being introduced
        for tok in stream:
            if tok.start >= show_at and tok.beat >= first_beat and tok.norm in OUTPUT_WORDS:
                out.append(SyncCue(kind="output", start=snap(tok.start), beat_id=beats[tok.beat].beat_id,
                                   words=tok.raw))
                break
    if kind in VISUAL_PANEL_KINDS:
        pulses: list[float] = []
        for i, tok in enumerate(stream):
            if len(pulses) >= MAX_FOCUS_PULSES:
                break
            if tok.start < show_at or tok.stem not in VISUAL_NOUNS:
                continue
            # "this function maps", "this models": a plural-looking form is a noun only after a plural determiner
            if tok.norm != tok.stem and not (i > 0 and stream[i - 1].beat == tok.beat
                                             and stream[i - 1].norm in PLURAL_DETERMINERS):
                continue
            # the earliest pointing word just before the noun: "look at this graph" pulses at "look"
            trigger = next((k for k in range(max(0, i - LOOK_BACK_TOKENS), i)
                            if stream[k].beat == tok.beat and stream[k].norm in LOOK_TRIGGERS), None)
            if trigger is None or stream[trigger].start < show_at:
                continue
            if any(stream[k].norm in SUBJECT_PRONOUNS for k in range(trigger + 1, i)):
                continue  # "here it curves", "here we map"
            at = snap(stream[trigger].start)
            if pulses and at - pulses[-1] < FOCUS_MIN_GAP_SECONDS:
                continue
            end = min(duration, round(at + FOCUS_SECONDS, 3))
            if end > at:
                pulses.append(at)
                out.append(SyncCue(kind="focus", start=at, end=end, beat_id=beats[tok.beat].beat_id,
                                   words=_spoken(stream, trigger, i - trigger + 1)))
    return out


def scene_sync_cues(beats: Sequence[TimedBeat], board: Sequence[BoardItem], side_panel: ResolvedSidePanel | None,
                    duration: float, options: SyncOptions) -> list[SyncCue]:
    """The word-anchored cues of one timed scene, sorted by start (empty without any match)."""
    if not beats:
        return []
    stream = word_stream(beats)
    if not stream:
        return []
    anchors = [t for b in beats for t in (b.start, b.speech_end, b.end)]
    if side_panel is not None:
        anchors.append(float(side_panel.show_at or 0.0))
    snap = _Snap(anchors, options.frame)
    cues = _panel_cues(beats, side_panel, duration, stream, snap, board)
    if board:
        cues += _var_cues(beats, board, stream, snap)
        cues += _emphasis_cues(beats, board, stream, snap, auto=options.auto_emphasis)
    cues = [c for c in cues if 0.0 <= c.start < duration]
    cues.sort(key=lambda c: (c.start, c.kind, c.item_id or "", c.part or ""))
    return cues[:MAX_CUES_PER_SCENE]
