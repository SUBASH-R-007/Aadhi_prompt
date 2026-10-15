"""Measured text fit (DYNAMIC_VIDEO_DESIGN §4.6 "Measured text fit" [RV T-4], R-29, R-59; B2 plan §1.2 ``text_fit``,
§1.6 slots, §1.11, §2 "Fonts"; owner decision 2026-10-14/15: HarfBuzz shaping for every script).

Every text slot is fitted by **measured widths**, never by character counts: the font size within the slot's
``(min, max)`` and the line breaks, so direct.py's lints (``text.overflow``, ``portrait.hook_overflow``) and the caption
chunker can decide before TTS. **The page decides the pixels** (plan §1.11): it lays the text out with CSS and reports
its own overflow (``textBoxes()``); Python's size is a start and an upper bound, biased **conservative**, so a text
Python certifies as fitting fits on the page.

How a text is measured (the page's own rules, mirrored; tests/determinism/test_textfit_parity.py measures every vendored
face and weight the stage uses against the real render page):

* **Shaping** (``shaping``): HarfBuzz on the vendored WOFF2 faces (decoded once per process with fontTools), with the
  features, ``unicode-range`` filter, variable axes, script and language the page shapes with. Measured against
  Chromium at every slot size (39-148 px), every face, weight and script draws a word within 0.054 px of its shaped
  width (the extended probe, 2,964,267 word widths: max 0.0531 px Latin, 0.0322 px Indic; median 0.009 px): Latin text
  with its kerning, ligatures
  and contextual alternates (Inter's ``2x3`` drawn with ``×``, ``A*****``), and the Indic conjuncts and vowel signs B2b
  could not bound (``ಕೋ`` drawn 3.1x its letters' Pillow advances, ``ತ್ಝ`` 75% narrower).
* **Faces: Chromium's font fallback** (:func:`shape_text`). The stack of the slot's type role is the stage's
  (``faces.stack``: display = display face, body face, the lecture's Indic family first, then the others; body = body
  face, Indic families; mono), from ``presentation.fonts`` in its B1 or B2 form and the lecture's ``board_language``.
  A text is split into Chromium's script runs (``shaping.script_runs``: its ``ScriptRunIterator``, Script_Extensions
  and paired brackets included), and each run is shaped as Chromium's ``HarfBuzzShaper`` does:
  with the faces of the stack in order (per family the weight by CSS Fonts 4 §5.2, ``faces.match_weight``, its faces
  the LAST declared first, §4.5; a face whose ``unicode-range`` holds none of the characters still to draw is skipped),
  each face shapes what is still to draw; the HarfBuzz clusters it draws with no missing glyph are kept as shaped, and
  the clusters holding a missing glyph are left for the next face. What no face draws is drawn by a system font
  (``uncovered``). Kept as shaped means with what the face's GPOS did beside a missing glyph: Atkinson draws ``ŶA``
  3 px narrower than ``Ŷ`` and ``A`` apart (100 px), because its ``latin-ext`` face kerns ``Ŷ`` against the ``A`` it
  has no glyph for before its ``latin`` face draws the ``A`` (measured in Chromium; the parity probe).
* **Fail closed.** A face that cannot be read, or whose variable axes are not the table's (``faces.Family.axes``), is
  a :class:`FaceError`: never a silent fallback to another face or a guess.
* **Words.** The page's word units (``web/js/motion/text.js`` ``splitWords``): runs between whitespace (JS ``\\s`` and
  the zero-width space), no-break spaces glued inside a word. A maximal run of collapsible whitespace (space, tab,
  line breaks) is one space wide (CSS ``white-space: normal``; :func:`collapse`: a zero-width or other space between
  two spaces keeps both, a line feed next to a zero-width space draws nothing); a zero-width space is a break of no
  width. The title draws each word
  as an inline-block (board_generic ``wordSpans``), shaped on its own: it never breaks inside unless it is wider than
  the column even at the slot's minimum size (``overflow-wrap: anywhere``; board_generic's "unavoidable" break), and
  then it takes whole lines of its own, broken after a hyphen where one fits, else between grapheme clusters (never
  inside one); each piece is shaped on its own with the script runs of its word, as the page shapes a line's part of
  a word. Text in normal flow (the kicker, a subtitle, card text) is shaped as one text on the page: across a space a
  face may kern (Atkinson draws ``y W`` 3.8 px narrower than its words and the space apart at 100 px; measured, never
  wider), so measuring its words apart over-estimates its lines, which only makes Python's line count conservative; it
  may also break after a hyphen inside a word, which only gives the page fewer lines than Python counts. Between two
  words joined by only zero-width spaces (:func:`joins`) HarfBuzz skips the default-ignorable U+200B, so the page
  shapes the two as one word, wider or narrower (Atkinson 400 draws ``q`` U+200B ``j`` 13 px wider than ``q`` and
  ``j`` apart at 100 px): what the pair draws across the zero-width spaces is added to the conservative whitespace
  between them (``Word.join_em``), and a chain of three or more is certified only when no part of it draws wider
  than its pairs add up to (:func:`measure`; Inter's ``A`` U+200B ``-`` U+200B ``*`` does: its asterisk takes its
  case form across two zero-width spaces). Its script
  runs are the whole text's, so a word's can resolve otherwise than on its own: every word is measured both ways
  (``Word.readings``), the conservative width is the wider, the optimistic the narrower, and ``raw`` is the word on its
  own (a title word, which the parity probe measures).
* **Letter-spacing** (``SlotSpec.tracking_em``) is added after every HarfBuzz cluster that draws something, as
  Chromium does (``ShapeResultSpacing``; measured: a Tamil consonant + virama + consonant takes it twice, a Telugu one
  in Noto Sans once, a cluster that is only a ZWNJ not at all), except inside a run of a cursive script, where only a
  space takes it (``shaping.takes_letter_spacing``; CSS Text 3 §8.2.1): a word of digits and U+202F NARROW NO-BREAK
  SPACE (``1 000``, ``5 %``, ``₹ 100`` so written) is a Mongolian run on its own and is drawn without it (measured:
  at -0.015 em, ``1 000`` is 7.5 px wider at 100 px than with it). The spaces between words take it when it is
  positive (left out when it is negative: never under-estimated). With a letter-spacing other than 0 the page drops the
  optional ligatures and the contextual alternates (``shaping.features``); ``tabular_nums`` (the countdown ring) shapes
  with ``tnum``.
* **Lines** are filled greedily (the line count CSS ``text-wrap: balance`` keeps), at sizes from ``max`` down in
  :data:`SIZE_STEP` px steps with ``min`` last, the slot's ``prefer_lines`` tried first: board_generic's ``fitLines``.

**Conservative bias** (plan §1.11, design §4.6), re-derived from the parity probe (B2b's 4% / 8% slack, its 0.06 em
per-word floor and its Chromium-measured kerning table are retired: they bounded what Pillow did not shape). The page
draws a word within 0.054 px of its shaped width: a layout unit (Chromium rounds an inline box's width up to 1/64 px)
and float rounding, which beyond the layout unit is at most 0.053% of the width. A word's **conservative** width is
its shaped advance plus :data:`SLACK` (0.5%) of it, its letter-spacing, and :data:`BOX_PX` (1/64 px, the layout
unit); a space's likewise. The 0.5% is 9 times what the probe's machine needs; it is kept for what that machine cannot
show: another platform's glyph advances (were they rounded to 1/64 px per glyph, as FreeType's hinted advances are, a
glyph 1.6 px wide or wider would still be covered) and a Chromium or HarfBuzz release whose shaping moved (the slow
parity probe re-measures both, and runs in the container). The **optimistic** width (from which :func:`word_kinds`
decides that the page certainly breaks an over-wide word) takes the slack off and adds no layout unit.

**What is certified** (``FitResult.fits``):

* ``True``: the conservative layout fits; the page fits too (tests/determinism/test_textfit_page.py: every word's page
  width is within its conservative width, and Python fits => page overflow 0 on board_generic's slots).
* ``False``: Python's conservative layout overflows at the minimum size; the page overflows too, or fits only within
  Python's margins (its own words, made as much wider as the slack and the layout unit make a word, would not fit: the
  plan's "within 4% slack", now within 0.5%; the same test).
* ``None``: the text holds a cluster (or a part of one: a grapheme cluster HarfBuzz shapes as several) or a separator
  between words that no vendored face of the stack draws (the page draws it with a system font, which differs between
  machines: Greek and maths symbols, U+3000 IDEOGRAPHIC SPACE, U+2028 LINE SEPARATOR, U+0600 ARABIC NUMBER SIGN, the
  form feed are in no vendored subset), whitespace the page does not break a line at as the line model does (an en,
  em, thin or other space, a space before a zero-width space, around the words or between them:
  :func:`plain_separator`), a chain of words joined by zero-width spaces that a part of draws wider than its pairs add
  up to (or longer than :data:`JOIN_WORDS` words: ``Measured.joins_bounded``), a word that starts with a combining mark (HarfBuzz draws a dotted circle before it where
  the mark starts the page's text, and none where it follows a space of the same text: the width depends on the
  slot's markup), or a word holding a character that can change the bidi embedding level (U+200F RIGHT-TO-LEFT MARK,
  an embedding, override or isolate: the page shapes each level on its own, which text fit does not model;
  :func:`splits_bidi`). ``size`` / ``lines`` / ``estimate`` are still given (:data:`UNCOVERED_EM` per uncovered
  cluster) as an estimate for a start size; a lint must not block on it.

Latin, Indic and mixed text, combining sequences a vendored face draws, and the Latin runs B2b left uncertified
(Inter's ``step-by-step``, ``f(x)``, ``10x10``, Outfit's ``ffiw``) are certified.

Not wired into direct.py or preflight here (B2d/B2e). Imported only inside the Dynamic gate (it loads ``uharfbuzz`` and
``fontTools``, which a classic build never does: tests/compose/test_dynamic_isolation.py).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

from . import faces as fc
from . import shaping as sh
from .shaping import FaceError, FaceKey, TextFitError, has_glyph, load_font

if TYPE_CHECKING:  # pragma: no cover
    from .templates import SlotSpec

__all__ = ["FaceError", "FaceKey", "TextFitError", "has_glyph", "load_font"]  # re-exported from ``shaping``

StackRole = Literal["display", "body", "mono"]
# a width reading: the shaped advance (+ letter-spacing), with the safety margins added, or with the slack taken off
Bias = Literal["conservative", "raw", "optimistic"]

SLACK: Final = 0.005  # relative, on a shaped advance (module docstring: re-derived from the parity probe)
BOX_PX: Final = 1 / 64  # Chromium's layout unit: an inline box's width is rounded up to it
SIZE_STEP: Final = 4.0  # board_generic fitLines: 4 px steps from max down, the minimum itself last
UNCOVERED_EM: Final = 1.0  # estimate for a cluster no vendored face draws (system font; never certified)
JOIN_WORDS: Final = 24  # the longest chain of words joined by zero-width spaces text fit bounds (:func:`measure`)
JOINERS: Final = frozenset("\u200c\u200d")  # ZWNJ, ZWJ
ZWJ: Final = "\u200d"
# web/js/motion/text.js: SPACE_RE = /^[\s\u200b]+$/u (JS \s) minus GLUE_RE (no-break spaces stay inside a word)
_GLUE: Final = frozenset("\u00a0\u2007\u202f")
_JS_SPACE: Final = frozenset("\t\n\v\f\r \u00a0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff"
                             + "".join(chr(c) for c in range(0x2000, 0x200B)))
SEPARATORS: Final = frozenset((_JS_SPACE - _GLUE) | {"\u200b"})
# CSS white-space: normal collapses a run of these into one space (CSS Text 3 §4.1.1: space, tab, segment break; a
# carriage return is treated as a space). Measured on the page: the form feed and the vertical tab are not collapsed
# (Chromium draws them, about 0.9 em wide at 100 px; no vendored face does, so they are uncovered)
_COLLAPSIBLE: Final = frozenset(" \t\n\r")
ZWSP: Final = "\u200b"
# a break opportunity after these inside an over-wide word (UAX #14 HY / BA), preferred to a break between clusters
_HYPHENS: Final = frozenset("-\u2010\u2012\u2013")
# bidi classes that can raise text above the page's left-to-right embedding level (:func:`splits_bidi`)
_BIDI_SPLITTING: Final = frozenset({"R", "AL", "RLE", "RLO", "LRE", "LRO", "RLI", "LRI", "FSI", "PDF", "PDI"})


# --- characters and clusters ---------------------------------------------------------------------------------------


def ignorable(ch: str) -> bool:
    """A default-ignorable character HarfBuzz hides, drawing no glyph and no width (``shaping.ignorable``: ZWJ, ZWNJ,
    the soft hyphen, the BOM, the bidi controls, variation selectors, Mongolian free variation selectors; not every
    ``Cf``: U+0600 ARABIC NUMBER SIGN is drawn)."""
    return sh.ignorable(ch)


def is_mark(ch: str) -> bool:
    """A combining mark (Unicode category M*; ``shaping.category``)."""
    return sh.category(ch).startswith("M")


def splits_bidi(ch: str) -> bool:
    """A character whose bidi class can give text of a left-to-right page another embedding level (a strong
    right-to-left character such as U+200F RIGHT-TO-LEFT MARK or U+061C ARABIC LETTER MARK, an embedding, override or
    isolate, or their terminators): Chromium shapes each level's run on its own, losing the kerning across the
    boundary (``A`` U+200F ``V`` is drawn 9 px wider than ``AV`` at 100 px), which text fit does not model: a word
    holding one is not certified."""
    return sh.bidirectional(ch) in _BIDI_SPLITTING


def _extends(cluster: str, ch: str) -> bool:
    last = cluster[-1]
    cp = ord(ch)
    if sh.category(ch)[0] == "M" or ch in JOINERS or last == ZWJ:
        return True  # Extend / SpacingMark (UAX #29 GB9, GB9a), ZWJ sequences (GB11)
    if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F:
        return True  # variation selectors, emoji modifiers, tag characters
    if last == "\r" and ch == "\n":
        return True  # GB3
    # an Indic conjunct: a letter after a virama (GB9c, applied to every script with a virama: coarser than ICU's for
    # Tamil and Kannada, which only keeps a broken word's pieces from ending inside a conjunct)
    return sh.combining(last) == 9 and sh.category(ch) == "Lo"


def graphemes(text: str) -> tuple[str, ...]:
    """The text's grapheme clusters, Indic conjuncts kept whole (``web/js/motion/text.js`` ``segmentGraphemes``,
    coarser where it differs). ``"".join(graphemes(t)) == t``."""
    out: list[str] = []
    for ch in text:
        if out and _extends(out[-1], ch):
            out[-1] += ch
        else:
            out.append(ch)
    return tuple(out)


def _spans(text: str) -> tuple[tuple[int, int, int], ...]:
    """``(word start, word end = separator start, separator end)`` offsets of :func:`words`' pairs in ``text``."""
    out: list[tuple[int, int, int]] = []
    start = end = None
    for i, ch in enumerate(text):
        if ch in SEPARATORS:
            if start is not None and end is None:
                end = i
            continue
        if end is not None:
            assert start is not None
            out.append((start, end, i))
            start, end = None, None
        if start is None:
            start = i
    if start is not None:
        out.append((start, len(text) if end is None else end, len(text)))
    return tuple(out)


def words(text: str) -> tuple[tuple[str, str], ...]:
    """``(word, separator after it)`` pairs: the page's word units (``splitWords``: whitespace-separated, no-break
    spaces inside a word, leading whitespace dropped); the last separator is ``""`` or trailing whitespace."""
    return tuple((text[a:b], text[b:c]) for a, b, c in _spans(text))


# --- faces ------------------------------------------------------------------------------------------------------------


def face_key(face: fc.Face, weight: int, web_dir: Path | str | None = None) -> FaceKey:
    """The :class:`FaceKey` the page draws ``face`` with at ``weight``: its file, its ``unicode-range`` and, for a
    variable face, ``wght`` clamped to its range (as CSS clamps it to the face's ``font-weight`` descriptor)."""
    family = fc.FAMILIES[face.family]
    path = sh.web_file(face, web_dir)
    if not family.variable:
        return FaceKey(path, None, (), face.ranges)
    lo, hi = face.weight
    return FaceKey(path, min(max(int(weight), lo), hi), family.axes, face.ranges)


@lru_cache(maxsize=1024)
def fallback_faces(stack: tuple[str, ...], weight: int, web_dir: str | None = None) -> tuple[FaceKey, ...]:
    """The faces the page tries for a text, in its order: per family of the stack, the faces of the weight CSS matches
    (Fonts 4 §5.2), the last declared first (§4.5)."""
    out: list[FaceKey] = []
    for fid in stack:
        rows = fc.faces_of(fid)
        matched = fc.match_weight(tuple(r.weight for r in rows), weight)
        out += [face_key(r, weight, web_dir) for r in reversed(rows) if r.weight == matched]
    return tuple(out)


# face file name -> whether its family is an Indic one (``Cluster.indic``)
_INDIC_FILES: Final = frozenset(Path(f.file).name for f in fc.FACES if fc.FAMILIES[f.family].role == "indic")


def _indic(key: FaceKey | None) -> bool:
    return key is not None and Path(key.path).name in _INDIC_FILES


# --- shaping a text the page's way -----------------------------------------------------------------------------------

Runs = tuple[tuple[int, int, str], ...]  # (start, end, script): a text's script runs (``shaping.script_runs``)


@dataclass(frozen=True)
class Segment:
    """A HarfBuzz cluster of a shaped text (``text[start:end]``) as the page draws it: ``key`` the face (None: a system
    font, ``uncovered``, or nothing at all for default-ignorable characters), ``units`` its advance (1/
    ``shaping.UNITS_PER_EM`` em) and ``spaced`` whether the page adds the letter-spacing after it
    (``shaping.takes_letter_spacing``: not after a zero-width character, nor inside a cursive script's run)."""

    start: int
    end: int
    key: FaceKey | None
    units: int
    spaced: bool
    uncovered: bool = False


def runs_of(text: str, runs: Runs | None = None) -> Runs:
    """``text``'s script runs: ``runs`` when given (a piece of a word, or a word of a text node, takes the runs the
    page itemized the whole with: :func:`slice_runs`), else its own (``shaping.script_runs``: the text is its own text
    node, as a title word's inline-block is)."""
    return sh.script_runs(text) if runs is None else runs


def slice_runs(runs: Runs, start: int, end: int) -> Runs:
    """The part of ``runs`` (of a whole text) over ``[start, end)``, its offsets relative to ``start``."""
    return tuple((max(a, start) - start, min(b, end) - start, s) for a, b, s in runs if a < end and b > start)


def _clusters(key: FaceKey, text: str, start: int, end: int, script: str, language: str,
              spaced: bool, tabular: bool = False) -> list[tuple[int, int, int, bool, int]]:
    """``text[start:end]`` shaped by one face: ``(start, end, advance, missing, first)`` per HarfBuzz cluster
    (absolute offsets; ``missing``: a glyph of it is the face's .notdef; ``first``: the cluster's own index, which
    Chromium reads its letter-spacing from)."""
    buf = sh.shape(key, text[start:end], script, language, spaced, tabular)
    starts: list[int] = []
    adv: dict[int, int] = {}
    bad: dict[int, bool] = {}
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions, strict=True):
        c = info.cluster
        if c not in adv:
            starts.append(c)
            adv[c], bad[c] = 0, False
        adv[c] += pos.x_advance
        bad[c] = bad[c] or info.codepoint == 0
    if not starts:  # HarfBuzz removed every glyph (only default-ignorable characters, the face has no space glyph)
        return [(start, end, 0, False, start)]
    starts.sort()  # left to right, clusters monotone: already sorted
    out = []
    for n, c in enumerate(starts):
        s = 0 if n == 0 else c  # characters before the first glyph's cluster (removed ignorables) belong to it
        e = starts[n + 1] if n + 1 < len(starts) else end - start
        out.append((start + s, start + e, adv[c], bad[c], start + c))
    return out


@lru_cache(maxsize=65536)
def shape_text(text: str, stack: tuple[str, ...], weight: int, language: str, spaced: bool = False,
               web_dir: str | None = None, runs: Runs | None = None, tabular: bool = False) -> tuple[Segment, ...]:
    """``text`` (one text node of the page: a word, or a piece of one) shaped as Chromium shapes it (the module
    docstring): per script run (:func:`runs_of`), the stack's faces in order (:func:`fallback_faces`), each drawing the
    HarfBuzz clusters it has every glyph for as it shaped them, the rest left to the next face; what is left at the end
    is drawn by a system font (uncovered), or not at all when it is only default-ignorable characters. ``language`` is
    the page's ``lang`` (``shaping.language_tag``), ``spaced`` a letter-spacing other than 0, ``tabular``
    ``font-variant-numeric: tabular-nums``."""
    faces = fallback_faces(stack, int(weight), web_dir)
    out: list[Segment] = []
    for a, b, script in runs_of(text, runs):
        holes = [(a, b)]
        for key in faces:
            if not holes:
                break
            hint = {ord(text[i]) for s, e in holes for i in range(s, e)}
            if not any(key.in_range(cp) for cp in hint):
                continue  # Chromium's fallback iterator skips a face whose range holds none of the characters left
            left: list[tuple[int, int]] = []
            for s, e in holes:
                for cs, ce, adv, missing, first in _clusters(key, text, s, e, script, language, spaced, tabular):
                    if missing:
                        if left and left[-1][1] == cs:
                            left[-1] = (left[-1][0], ce)
                        else:
                            left.append((cs, ce))
                    else:
                        drawn = any(not ignorable(ch) for ch in text[cs:ce])
                        out.append(Segment(cs, ce, key, adv, drawn and sh.takes_letter_spacing(text[first], script)))
            holes = left
        for s, e in holes:  # no vendored face draws it
            at = s
            for g in graphemes(text[s:e]):
                if all(ignorable(ch) for ch in g):
                    out.append(Segment(at, at + len(g), None, 0, False))
                else:
                    out.append(Segment(at, at + len(g), None, round(UNCOVERED_EM * sh.UNITS_PER_EM),
                                       sh.takes_letter_spacing(g[0], script), True))
                at += len(g)
    return tuple(sorted(out, key=lambda seg: seg.start))


# --- measuring a text -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cluster:
    """A grapheme cluster as the page draws it: ``key`` the face (None: no vendored face, or only default-ignorable
    characters), ``em`` the advance of the HarfBuzz clusters that start in it (a ligature's whole advance is its first
    grapheme's), ``indic`` drawn by an Indic family, ``uncovered`` drawn by a system font, ``at`` its offset in its
    word."""

    text: str
    key: FaceKey | None
    em: float = 0.0
    indic: bool = False
    uncovered: bool = False
    at: int = 0

    @property
    def reliable(self) -> bool:
        """Whether the shaped width is the page's: a vendored face draws it (a system font differs between machines)."""
        return not self.uncovered


@dataclass(frozen=True)
class Word:
    """One word unit: its clusters, ``em`` its shaped advance (em) and ``spacings`` how many times the page adds the
    letter-spacing to it, as its own text node (a title word's inline-block); ``flow_em`` / ``flow_spacings`` the same
    inside the whole text as one text node (a kicker, subtitle or body text: the page itemizes the scripts of the whole
    text, so a word's runs can resolve otherwise: ``1`` U+202F ``000`` is a Mongolian run on its own, unspaced, and
    Latin after ``about``); ``sep_em`` the width of the whitespace after it (0 for the last word), ``sep_units`` /
    ``sep_flow_units`` how many characters the page letter-spaces in it (a collapsed run of spaces is one; a
    zero-width space none), ``sep_uncovered`` the characters of it no vendored face draws; ``at`` its offset in the
    text; ``sep_plain`` whether the page breaks a line at that whitespace as the line model does
    (:func:`plain_separator`; the whitespace after the last word too, which the page draws at the end of the text);
    ``flow_uncovered`` the clusters no vendored face draws when the word is shaped inside the whole text;
    ``join_em`` / ``join_spacings`` what the whole text draws across the whitespace after it when that is only
    zero-width spaces (:func:`joins`): the advance (em) and letter-spacing count of the word, those zero-width spaces
    and the next word shaped as one, less the two words' readings inside the whole text (0 otherwise)."""

    text: str
    clusters: tuple[Cluster, ...]
    em: float
    sep_em: float
    sep_units: int = 0
    spacings: int = 0
    flow_em: float | None = None
    flow_spacings: int | None = None
    sep_flow_units: int | None = None
    sep_uncovered: tuple[str, ...] = ()
    at: int = 0
    sep_plain: bool = True
    flow_uncovered: tuple[str, ...] = ()
    join_em: float = 0.0
    join_spacings: int = 0

    @property
    def reliable(self) -> bool:
        """Certifiable: every cluster is drawn by a vendored face (as its own text node and inside the whole text), and
        so is the whitespace after it, at which the line model breaks a line as the page does (``sep_plain``); the
        word does not start with a combining mark that is not default-ignorable (a variation selector is one; the
        module docstring: its width would depend on the slot's markup); and it holds no character that can change the
        bidi embedding level (:func:`splits_bidi`)."""
        lead = self.text[:1]
        return (all(c.reliable for c in self.clusters) and not self.flow_uncovered and not self.sep_uncovered
                and self.sep_plain
                and not (lead and is_mark(lead) and not ignorable(lead))
                and not any(splits_bidi(ch) for ch in self.text))

    @property
    def indic(self) -> bool:
        return any(c.indic for c in self.clusters)

    def readings(self) -> tuple[tuple[float, int], ...]:
        """``(em, spacings)`` as its own text node, then inside the whole text (when that differs)."""
        flow = (self.em if self.flow_em is None else self.flow_em,
                self.spacings if self.flow_spacings is None else self.flow_spacings)
        return ((self.em, self.spacings),) if flow == (self.em, self.spacings) else ((self.em, self.spacings), flow)


def _measure_word(text: str, segments: Sequence[Segment]) -> tuple[tuple[Cluster, ...], float, int]:
    """The word's clusters (grapheme by grapheme, each with the face of the HarfBuzz cluster it starts in), its shaped
    advance (em) and its letter-spacing count. A grapheme is uncovered when any HarfBuzz cluster over it is: a grapheme
    can span several (``T`` U+093F U+0301, a Latin letter and a Devanagari vowel sign in two script runs; a ZWNJ
    before a mark; ``x`` ZWJ ``中``), and the part a system font draws makes its width the page's machine's."""
    out: list[Cluster] = []
    at = 0
    seg = list(segments)
    for g in graphemes(text):
        end = at + len(g)
        inside = [s for s in seg if at <= s.start < end]
        holder = next((s for s in seg if s.start <= at < s.end), None)
        key = holder.key if holder is not None else None
        uncovered = any(s.uncovered for s in seg if s.start < end and s.end > at)
        em = sum(s.units for s in inside) / sh.UNITS_PER_EM
        out.append(Cluster(g, key, em, _indic(key), uncovered, at))
        at = end
    return tuple(out), _em(segments), _spacings(segments)


def _em(segments: Sequence[Segment]) -> float:
    return sum(s.units for s in segments) / sh.UNITS_PER_EM


def _spacings(segments: Sequence[Segment]) -> int:
    return sum(1 for s in segments if s.spaced)


def _units(segments: Sequence[Segment]) -> int:
    return sum(s.units for s in segments)


@dataclass(frozen=True)
class Measured:
    """A text measured for one slot role: its words, the role's stack (family ids), weight, letter-spacing, the page's
    ``lang``, the faces' folder, ``tabular`` (``font-variant-numeric: tabular-nums``) and the text's script runs as one
    text node (a piece of a broken word is shaped again with them); ``joins_bounded`` whether the words joined by
    zero-width spaces draw no wider on a line, however many of them the line holds, than their readings and what each
    zero-width space adds (``Word.join_em``) add up to (:func:`measure`)."""

    text: str
    words: tuple[Word, ...]
    stack: tuple[str, ...]
    weight: int
    tracking_em: float
    language: str = sh.DEFAULT_LANGUAGE
    web_dir: str | None = None
    tabular: bool = False
    flow_runs: Runs | None = None
    lead_plain: bool = True
    joins_bounded: bool = True

    @property
    def reliable(self) -> bool:
        """Every word is (:attr:`Word.reliable`), the whitespace before the first word draws nothing on the page
        (``lead_plain``: :func:`plain_separator`), and the words joined by zero-width spaces are bounded
        (``joins_bounded``)."""
        return self.lead_plain and self.joins_bounded and all(w.reliable for w in self.words)

    @property
    def uncovered(self) -> tuple[str, ...]:
        """The clusters (and the separator characters) no vendored face of the stack draws, in order, each once."""
        return tuple(dict.fromkeys(t for w in self.words for t in (*(c.text for c in w.clusters if c.uncovered),
                                                                   *w.flow_uncovered, *w.sep_uncovered)))

    def shaped(self, text: str, runs: Runs | None = None) -> tuple[float, int]:
        """``text`` shaped as one text node (a word or a piece of one; ``runs``: the script runs it takes from the
        text it is part of, else its own): its advance (em) and letter-spacing count."""
        segments = shape_text(text, self.stack, self.weight, self.language, self.tracking_em != 0, self.web_dir, runs,
                              self.tabular)
        return _em(segments), _spacings(segments)

    def _piece(self, word: Word, clusters: Sequence[Cluster]) -> tuple[tuple[float, int], ...]:
        """A piece of ``word`` (a line's part of a broken word) shaped on its own with the runs the page itemized the
        word with, as its own text node and inside the whole text."""
        lo = clusters[0].at
        hi = clusters[-1].at + len(clusters[-1].text)
        piece = word.text[lo:hi]
        alone = self.shaped(piece, slice_runs(sh.script_runs(word.text), lo, hi))
        if self.flow_runs is None:
            return (alone,)
        flow = self.shaped(piece, slice_runs(self.flow_runs, word.at + lo, word.at + hi))
        return (alone,) if flow == alone else (alone, flow)

    def width(self, word: Word, size: float, bias: Bias = "conservative",
              clusters: Sequence[Cluster] | None = None) -> float:
        """A word's (or a piece of it: ``clusters``, shaped on its own as the page shapes a line's part of a word)
        width in px at ``size``: ``raw`` the shaped advance plus the letter-spacing, as its own text node (a title
        word); ``conservative`` the widest reading (as its own text node or inside the whole text: :meth:`Word.readings`)
        + :data:`SLACK` of the advance and :data:`BOX_PX`; ``optimistic`` the narrowest - :data:`SLACK` of the
        advance."""
        readings = word.readings() if clusters is None else self._piece(word, clusters)
        t = self.tracking_em
        if bias == "conservative":
            return max(size * (em * (1 + SLACK) + t * n) for em, n in readings) + BOX_PX
        if bias == "optimistic":
            return max(0.0, min(size * (em * (1 - SLACK) + t * n) for em, n in readings))
        em, n = readings[0]
        return size * (em + t * n)

    def sep(self, word: Word, size: float, bias: Bias = "conservative") -> float:
        """The whitespace after ``word`` in px at ``size``: ``raw`` its advance and the letter-spacing the page adds
        after its characters (as a title draws it, between inline-blocks); ``conservative`` + :data:`SLACK` of the
        advance and :data:`BOX_PX`, the largest letter-spacing either reading adds, a negative one left out (never
        under-estimated), and when the whitespace is only zero-width spaces (:func:`joins`) what the whole text draws
        wider across it (``Word.join_em``, ``join_spacings``: a positive kerning, a ligature or contextual form across
        it; with the slack, and the layout unit when it adds anything; never less than nothing); ``optimistic`` -
        :data:`SLACK` of the advance, the smallest letter-spacing (across zero-width spaces the page may draw a pair
        narrower still: flow text drawn narrower than its words apart only makes Python's line count conservative)."""
        t = self.tracking_em
        across = 0.0
        if bias == "conservative" and (word.join_em > 0 or t * word.join_spacings > 0):
            across = size * (max(0.0, word.join_em) * (1 + SLACK) + max(0.0, t * word.join_spacings)) + BOX_PX
        if not word.sep_em and not word.sep_units and not word.sep_flow_units:
            return across
        units = (word.sep_units, word.sep_units if word.sep_flow_units is None else word.sep_flow_units)
        if bias == "conservative":
            return size * (word.sep_em * (1 + SLACK) + max(0.0, *(t * u for u in units))) + BOX_PX + across
        if bias == "optimistic":
            return max(0.0, size * (word.sep_em * (1 - SLACK) + min(t * u for u in units)))
        return size * (word.sep_em + t * word.sep_units)


def collapse(sep: str) -> tuple[tuple[int, str], ...]:
    """Whitespace between (or around) words as the page lays it out (CSS Text 3 §4.1.1, ``white-space: normal``, as
    Chromium does it; measured on the render page): ``(offset in sep, character)`` per character drawn, a run of
    collapsible whitespace (:data:`_COLLAPSIBLE`) given as one space at its first offset. A run is a maximal one:
    another character between two (a zero-width space, an en space, the BOM) keeps both, and the page draws both
    (``x`` space U+200B space ``y`` is 28.1 px wider than ``x y`` in Inter 400 at 100 px). A run holding a line feed
    next to a zero-width space draws nothing (Chromium removes the segment break there, and the spaces around it with
    it: ``x`` U+200B LF ``y`` is as wide as ``xy``; a carriage return is a space even there: ``x`` U+200B CR ``y`` is as
    wide as ``x y``). Chromium also removes a segment break between two East Asian wide (or fullwidth) characters, which
    no vendored face draws (the text is not certified then: such a character is uncovered; test_textfit checks that no
    vendored face draws one); text fit keeps its space there (an over-estimate)."""
    out: list[tuple[int, str]] = []
    i, n = 0, len(sep)
    while i < n:
        if sep[i] not in _COLLAPSIBLE:
            out.append((i, sep[i]))
            i += 1
            continue
        j = i
        while j < n and sep[j] in _COLLAPSIBLE:
            j += 1
        if not ("\n" in sep[i:j] and ((i > 0 and sep[i - 1] == ZWSP) or (j < n and sep[j] == ZWSP))):
            out.append((i, " "))
        i = j
    return tuple(out)


def plain_separator(sep: str, leading: bool = False) -> bool:
    """Whether the page breaks a line at ``sep`` (whitespace between words, or after the last word) as the line model
    does: the whole of it vanishes at a break and nothing of it shows at a line's end or start. True when it lays out
    (:func:`collapse`) as zero-width spaces, then at most one space; ``leading`` (the whitespace before the first word,
    which a title drops and flow text draws): a space, then only zero-width spaces (a space at a line's start is
    removed). Anything else is not modelled (measured on the render page, in flow text and between a title's
    inline-blocks): an en, em, thin or other space is not hung at a line's end (``xxxx`` U+2002 overflows its line by
    the en space's 50 px; between a title's words it takes a line of its own), and a space before a zero-width space
    can take a line of its own (``xxxx`` space U+200B before an over-wide word: three lines, not two), as can a
    space after one at a line's start."""
    chars = [ch for _, ch in collapse(sep)]
    if leading and chars[:1] == [" "]:
        chars = chars[1:]
    if not leading and chars[-1:] == [" "]:
        chars = chars[:-1]
    return all(ch == ZWSP for ch in chars)


def joins(sep: str) -> bool:
    """Whether the whitespace between two words lays out (:func:`collapse`) as only zero-width spaces: the page's text
    node then holds the two words with nothing between them but default-ignorable characters, which HarfBuzz skips when
    it matches the font's lookups, so the words kern, ligate and take contextual forms across it as one word would
    (measured, 100 px: Atkinson 400 draws ``q`` U+200B ``j`` 13 px wider than ``q`` and ``j`` apart, Lexend 700
    ``f`` U+200B ``T`` 5.5 px; HarfBuzz shaping the two as one text reproduces the page). A title draws each word as an
    inline-block, so nothing is shaped across its whitespace."""
    chars = [ch for _, ch in collapse(sep)]
    return bool(chars) and all(ch == ZWSP for ch in chars)


def _joined(text: str, scripts: Sequence[str], spans: Sequence[tuple[int, int, int]], p: int,
            q: int) -> tuple[str, Runs]:
    """Words ``p`` to ``q`` of ``text`` (:func:`_spans`) with the zero-width spaces between them, as the page's text
    node holds them once its whitespace is collapsed (:func:`collapse`: a line feed next to a zero-width space is
    removed), and their script runs there (``scripts``: the script of each character of ``text`` in the whole text's
    runs; adjacent equal scripts are one run, as the page itemizes the text)."""
    at: list[int] = []
    for r in range(p, q + 1):
        a, b, c = spans[r]
        at += range(a, b)
        if r < q:
            at += [b + i for i, _ in collapse(text[b:c])]
    runs: list[tuple[int, int, str]] = []
    for k, i in enumerate(at):
        if runs and runs[-1][2] == scripts[i]:
            runs[-1] = (runs[-1][0], k + 1, scripts[i])
        else:
            runs.append((k, k + 1, scripts[i]))
    return "".join(text[i] for i in at), tuple(runs)


def _separator(sep: str, flow: Runs, stack: tuple[str, ...], weight: int, language: str, spaced: bool,
               web_dir: str | None) -> tuple[float, int, int, tuple[str, ...]]:
    """The whitespace between two words: its width (em), how many of its characters the page letter-spaces as a
    title draws it (its own text node) and inside the whole text (``flow``: its script runs there), and the characters
    of it no vendored face draws. It is laid out as the page does (:func:`collapse`): a run of collapsible whitespace
    is one space (Chromium letter-spaces a space even in a cursive run); other spaces (en space, ideographic space,
    ...) keep their own width; the zero-width space and the BOM have none and take no letter-spacing
    (``shaping.zero_width``)."""
    total, units, flow_units = 0.0, 0, 0
    uncovered: list[str] = []
    scripts = [s for a, b, s in flow for _ in range(a, b)]
    for i, ch in collapse(sep):
        segments = shape_text(ch, stack, weight, language, spaced, web_dir)
        total += _em(segments)
        if sep[i] in _COLLAPSIBLE:  # a collapsed run: one space
            units += 1
            flow_units += 1
        else:
            units += _spacings(segments)
            flow_units += 1 if sh.takes_letter_spacing(ch, scripts[i]) else 0
        uncovered += [ch] if any(s.uncovered for s in segments) else []
    return total, units, flow_units, tuple(dict.fromkeys(uncovered))


def stack_for(fonts: fc.Fonts, role: str, language: str | None) -> tuple[str, ...]:
    """The family ids a slot of type ``role`` is drawn with (``faces.stack``); ``ValueError`` for a role no stack has."""
    if role not in ("display", "body", "mono"):
        raise ValueError(f"no font stack for the type role {role!r} (display, body or mono)")
    return fc.stack(fonts, role, language)  # type: ignore[arg-type]


def measure(text: str, *, fonts: fc.Fonts, role: str, weight: int, language: str | None = "",
            tracking_em: float = 0.0, web_dir: Path | str | None = None, tabular: bool = False) -> Measured:
    """Measure ``text`` for a slot of type ``role`` at ``weight`` (``presentation.fonts`` in either form; ``language``
    the lecture's board language, which orders the Indic families as the stage does and is the page's ``lang``;
    ``tabular``: the slot is drawn with ``font-variant-numeric: tabular-nums``). Each word is measured as its own text
    node (a title word's inline-block) and inside the whole text as one text node (flow text: :class:`Word`).

    Words joined by only zero-width spaces (:func:`joins`) are shaped together too, as the whole text shapes them:
    what a pair draws across its zero-width spaces is the first word's ``join_em`` / ``join_spacings``, which the
    conservative whitespace adds when the two share a line (:meth:`Measured.sep`). On a line holding three or more of
    one chain, a lookup could reach across two zero-width spaces (a ligature of three one-letter words): every such
    part of a chain (of at most :data:`JOIN_WORDS` words) is shaped as one, and the text is certified only when none
    draws wider than its words' readings and the pairs' positive ``join_em`` / letter-spacing add up to
    (``Measured.joins_bounded``; a longer chain is not certified)."""
    text = str(text)
    stack = stack_for(fonts, role, language)
    lang = sh.language_tag(language)
    where = None if web_dir is None else str(web_dir)
    spaced = float(tracking_em) != 0
    weight = int(weight)
    flow = sh.script_runs(text)
    out: list[Word] = []
    flow_readings: list[tuple[int, int]] = []  # each word's (units, spacings) inside the whole text
    spans = _spans(text)
    for i, (a, b, c) in enumerate(spans):
        word = text[a:b]
        own = shape_text(word, stack, weight, lang, spaced, where, None, tabular)
        clusters, em, spacings = _measure_word(word, own)
        runs = slice_runs(flow, a, b)
        flow_segments = own
        flow_uncovered: tuple[str, ...] = ()
        if runs != sh.script_runs(word):
            flow_segments = shape_text(word, stack, weight, lang, spaced, where, runs, tabular)
            flow_uncovered = tuple(cl.text for cl in _measure_word(word, flow_segments)[0] if cl.uncovered)
        flow_readings.append((_units(flow_segments), _spacings(flow_segments)))
        if i + 1 < len(spans):
            sep_em, units, flow_units, sep_uncovered = _separator(text[b:c], slice_runs(flow, b, c), stack, weight,
                                                                  lang, spaced, where)
        else:  # the whitespace after the last word ends the text: drawn at its end (sep_plain), never between words
            sep_em, units, flow_units, sep_uncovered = 0.0, 0, 0, ()
        out.append(Word(word, clusters, em, sep_em, units, spacings, _em(flow_segments), _spacings(flow_segments),
                        flow_units, sep_uncovered, a, plain_separator(text[b:c]), flow_uncovered))
    joined = [i + 1 < len(spans) and joins(text[spans[i][1]:spans[i][2]]) for i in range(len(spans))]
    scripts = [s for a, b, s in flow for _ in range(a, b)]
    t = float(tracking_em)

    def across(p: int, q: int) -> tuple[int, int, bool]:  # words p..q shaped as one, with the zero-width spaces
        joint, runs = _joined(text, scripts, spans, p, q)
        segments = shape_text(joint, stack, weight, lang, spaced, where, runs, tabular)
        return _units(segments), _spacings(segments), any(seg.uncovered for seg in segments)

    bounded = True
    gain: dict[int, tuple[int, int]] = {}  # word -> (units, spacings) drawn across the zero-width spaces after it
    for i, join in enumerate(joined):
        if join:
            units, n, uncovered = across(i, i + 1)
            gain[i] = (units - flow_readings[i][0] - flow_readings[i + 1][0],
                       n - flow_readings[i][1] - flow_readings[i + 1][1])
            bounded = bounded and not uncovered
    i = 0
    while i < len(spans):  # each chain of words i..j joined by zero-width spaces
        j = i
        while joined[j]:
            j += 1
        if j - i + 1 > JOIN_WORDS:
            bounded = False
        elif j - i >= 2:
            for p in range(i, j - 1):
                for q in range(p + 2, j + 1):
                    units, n, uncovered = across(p, q)
                    words_units = sum(flow_readings[r][0] for r in range(p, q + 1))
                    words_n = sum(flow_readings[r][1] for r in range(p, q + 1))
                    gains = range(p, q)
                    bounded = (bounded and not uncovered
                               and units <= words_units + sum(max(0, gain[r][0]) for r in gains)
                               and t * n <= t * words_n + sum(max(0.0, t * gain[r][1]) for r in gains))
        i = j + 1
    out = [replace(w, join_em=gain[k][0] / sh.UNITS_PER_EM, join_spacings=gain[k][1]) if k in gain else w
           for k, w in enumerate(out)]
    lead = text[:spans[0][0]] if spans else text
    return Measured(text, tuple(out), stack, weight, float(tracking_em), lang, where, bool(tabular), flow,
                    plain_separator(lead, leading=True), bounded)


# --- fitting ----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    """One size's conservative layout: the lines (text), their widths (px), whether every word that must stay whole
    does (``whole``) and whether the lines fit the slot's line limit (``fits``)."""

    size: float
    lines: tuple[str, ...]
    widths: tuple[float, ...]
    whole: bool
    fits: bool


def _pieces(m: Measured, word: Word, size: float, width: float, bias: Bias) -> list[tuple[str, float]]:
    """An over-wide word broken into lines of at most ``width`` (``overflow-wrap: anywhere``): greedily, after the last
    hyphen that fits (a normal break opportunity, which CSS takes first), else between clusters; never inside a
    cluster (a cluster wider than the line stands alone and overflows sideways). Each piece is shaped on its own."""
    out: list[tuple[str, float]] = []
    cl = word.clusters
    start = 0
    while start < len(cl):
        end = start + 1
        hyphen = None
        while end < len(cl) and m.width(word, size, bias, cl[start:end + 1]) <= width:
            end += 1
            prev, nxt = cl[end - 1].text, cl[end].text if end < len(cl) else ""
            if prev[-1:] in _HYPHENS and nxt[:1] and not nxt[:1].isdigit():
                hyphen = end
        if end < len(cl) and hyphen is not None and hyphen > start:
            end = hyphen
        piece = cl[start:end]
        out.append(("".join(c.text for c in piece), m.width(word, size, bias, piece)))
        start = end
    return out


def _other(bias: Bias) -> Bias:
    return "optimistic" if bias == "conservative" else "conservative" if bias == "optimistic" else "raw"


def word_kinds(m: Measured, width: float, min_size: float, bias: Bias = "conservative") -> tuple[str, ...]:
    """How the page treats each word wider than the box (board_generic ``layout``: a word is "unavoidable", and may
    break, when the page measures it wider than the box at the slot's MINIMUM size; any other word must stay whole):
    ``"break"`` when even the most favourable reading (the slack the other way) is wider than the box at the minimum,
    so the page certainly breaks it; ``"whole"`` when even ``bias``'s reading fits at the minimum, so the page
    certainly keeps it whole; ``"either"`` when the page may do either (within the margins)."""
    out = []
    for w in m.words:
        if m.width(w, min_size, _other(bias)) > width:
            out.append("break")
        elif m.width(w, min_size, bias) <= width:
            out.append("whole")
        else:
            out.append("either")
    return tuple(out)


def _lines(m: Measured, size: float, width: float, breaks: Sequence[bool], bias: Bias) -> list[tuple[str, float]]:
    """Greedy lines; a word wider than the box that ``breaks`` allows to break is broken into lines of its own (its
    inline-block takes the column's width, so the words around it go on other lines)."""
    lines: list[tuple[str, float]] = []
    cur: list[str] = []
    cur_w = sep_w = 0.0
    for w, may_break in zip(m.words, breaks, strict=True):
        ww = m.width(w, size, bias)
        if may_break and ww > width:
            if cur:
                lines.append((" ".join(cur), cur_w))
            cur, cur_w, sep_w = [], 0.0, 0.0
            lines.extend(_pieces(m, w, size, width, bias))
            continue
        if cur and cur_w + sep_w + ww > width:
            lines.append((" ".join(cur), cur_w))
            cur, cur_w = [w.text], ww
        else:
            cur_w += (sep_w if cur else 0.0) + ww
            cur.append(w.text)
        sep_w = m.sep(w, size, bias)
    if cur:
        lines.append((" ".join(cur), cur_w))
    return lines


def layout(m: Measured, size: float, width: float | None, max_lines: int | None, min_size: float,
           bias: Bias = "conservative") -> Layout:
    """The greedy layout of ``m`` at ``size`` in a box ``width`` px wide (None: unbounded, one line), with the page's
    rules for a word wider than the box (:func:`word_kinds`). Above the minimum size a word the page may keep whole
    must fit whole (``whole`` False otherwise: the page could refuse the size). At the minimum size a word the page may
    either break or keep whole is laid out broken: kept whole it would take at most one line (the page measured it no
    wider than the box), broken it takes at least two (Python's pieces are never narrower than the page's), so the
    broken layout holds the most lines either way."""
    if width is None:
        total = sum(m.width(w, size, bias) + (m.sep(w, size, bias) if i + 1 < len(m.words) else 0.0)
                    for i, w in enumerate(m.words))
        text = " ".join(w.text for w in m.words)
        return Layout(size, (text,) if text else (), (total,) if text else (), True, True)
    kinds = word_kinds(m, width, min_size, bias)
    breaks = [k == "break" or (k == "either" and size <= min_size) for k in kinds]
    whole = all(may_break or m.width(w, size, bias) <= width for w, may_break in zip(m.words, breaks, strict=True))
    lines = _lines(m, size, width, breaks, bias)
    fits = whole and (max_lines is None or len(lines) <= max_lines)
    return Layout(size, tuple(t for t, _ in lines), tuple(round(x, 3) for _, x in lines), whole, fits)


def sizes(lo: float, hi: float, step: float = SIZE_STEP) -> tuple[float, ...]:
    """The sizes the page tries (board_generic ``fitLines``): ``hi``, then ``step`` px smaller each time, ``lo``
    itself last."""
    if not 0 < lo <= hi or step <= 0:
        raise ValueError(f"bad size range ({lo}, {hi}) or step {step}")
    out = [float(hi)]
    while out[-1] > lo:
        out.append(max(float(lo), out[-1] - step))
    return tuple(out)


@dataclass(frozen=True)
class FitResult:
    """The fitted slot. ``size`` the largest size (of :func:`sizes`) at which the conservative layout fits, else the
    minimum; ``lines`` / ``widths`` its lines and their conservative widths (px); ``estimate`` whether it fits;
    ``fits`` the certified answer (``estimate`` when ``reliable``, else None: see the module docstring);
    ``uncovered`` the clusters no vendored face draws; ``width`` the box (px, None: unbounded)."""

    size: float
    lines: tuple[str, ...]
    widths: tuple[float, ...]
    estimate: bool
    fits: bool | None
    reliable: bool
    uncovered: tuple[str, ...]
    width: float | None

    def stored(self) -> dict[str, Any]:
        """The ``ResolvedLook.text_fit[slot]`` entry of §6.7 (``{size, lines}``: the size and the line count), for
        direct.py (B2e) when the answer is certified."""
        return {"size": self.size, "lines": len(self.lines)}


def fit(m: Measured, *, width: float | None, size: tuple[float, float], max_lines: int | None,
        prefer_lines: int | None = None, step: float = SIZE_STEP, bias: Bias = "conservative") -> FitResult:
    """Fit a measured text to a box: the page's ``fitLines`` (``prefer_lines`` first, then ``max_lines``) on the
    ``bias`` layout (``conservative`` for every real use; the others are for the tests that bracket the page)."""
    lo, hi = float(size[0]), float(size[1])
    candidates = sizes(lo, hi, step)

    def fit_lines(limit: int | None) -> Layout:
        last = None
        for s in candidates:
            last = layout(m, s, width, limit, lo, bias)
            if last.fits:
                return last
        assert last is not None
        return last

    best = fit_lines(prefer_lines) if prefer_lines else None
    if best is None or not best.fits:
        best = fit_lines(max_lines)
    return FitResult(best.size, best.lines, best.widths, best.fits, best.fits if m.reliable else None, m.reliable,
                     m.uncovered, None if width is None else float(width))


def fit_text(text: str, *, width: float | None, size: tuple[float, float], max_lines: int | None, role: str,
             weight: int, fonts: fc.Fonts, language: str | None = "", tracking_em: float = 0.0,
             prefer_lines: int | None = None, step: float = SIZE_STEP, web_dir: Path | str | None = None,
             tabular: bool = False) -> FitResult:
    """Measure and fit ``text`` (:func:`measure`, then :func:`fit`)."""
    m = measure(text, fonts=fonts, role=role, weight=weight, language=language, tracking_em=tracking_em,
                web_dir=web_dir, tabular=tabular)
    return fit(m, width=width, size=size, max_lines=max_lines, prefer_lines=prefer_lines, step=step)


def fit_slot(text: str, spec: SlotSpec, *, fonts: fc.Fonts, language: str | None = "",
             width: float | None = None, step: float = SIZE_STEP, web_dir: Path | str | None = None) -> FitResult:
    """Fit ``text`` to a template slot (``templates.SlotSpec``, plan §1.6): its box ``w`` (``width`` overrides it where
    the template draws the text in a narrower box than the slot's: board_generic's kicker sits right of its 12 px pip
    and 18 px gap), size range, line limit and preference, type role, weight, letter-spacing and numerals
    (``tabular_nums``: board_generic's countdown ring)."""
    return fit_text(text, width=spec.w if width is None else width, size=spec.size, max_lines=spec.max_lines,
                    role=spec.face, weight=spec.weight, fonts=fonts, language=language, tracking_em=spec.tracking_em,
                    prefer_lines=spec.prefer_lines, step=step, web_dir=web_dir, tabular=spec.tabular_nums)


def clear_caches() -> None:
    """Drop the decoded faces, shaped texts and face lists (tests; a process normally keeps them)."""
    sh.clear_caches()
    for f in (shape_text, fallback_faces):
        f.cache_clear()
