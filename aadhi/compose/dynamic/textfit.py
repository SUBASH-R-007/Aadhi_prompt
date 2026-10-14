"""Measured text fit (DYNAMIC_VIDEO_DESIGN §4.6 "Measured text fit" [RV T-4], R-29, R-59; B2 plan §1.2 ``text_fit``,
§1.6 slots, §1.11, §2 "Fonts"; b2crit_engineering "Fonts: classic invariance, Pillow measurement fidelity").

Every text slot is fitted by **measured advance widths**, never by character counts: the font size within the slot's
``(min, max)`` and the line breaks, so direct.py's lints (``text.overflow``, ``portrait.hook_overflow``) and the caption
chunker can decide before TTS. **The page decides the pixels** (plan §1.11): it lays the text out with CSS and reports
its own overflow (``textBoxes()``); Python's size is a start and an upper bound, biased **conservative**, so a text
Python certifies as fitting fits on the page.

How a text is measured (the page's own rules, mirrored):

* **Faces.** The stack of the slot's type role is the stage's (``faces.stack``: display = display face, body face, the
  lecture's Indic family first, then the others; body = body face, Indic families; mono), from ``presentation.fonts``
  in its B1 or B2 form and the lecture's ``board_language`` (``stage.js`` passes it to ``fontStacks``). Each grapheme
  cluster is drawn by the first family of the stack that has a face for it: CSS font matching per family (the weight by
  CSS Fonts 4 §5.2, ``faces.match_weight``; among that weight's faces whose ``unicode-range`` holds the cluster, the
  LAST declared first, §4.5) and the face's cmap really holding every character of the cluster (a range can promise
  more than the file has; a missing glyph would be .notdef, so the page would fall back to the next family). Runs of
  clusters drawn by one face are measured together.
* **Pillow FreeType, basic layout.** ``ImageFont.truetype(..., layout_engine=ImageFont.Layout.BASIC)`` on the vendored
  WOFF2 file itself (FreeType reads WOFF2: Pillow 12.3 / FreeType 2.14.3 on the dev machine; tests/compose/dynamic/
  test_textfit.py loads every face and pins its advances wherever the fast suite runs), never raqm: raqm needs FriBiDi at run time, which some machines have and some lack, so the same
  timeline would measure differently in the Docker image and on Windows (b2crit_engineering). A variable face's axes
  are ALWAYS set (Anek's file default is ``wght`` 500, not the 400 a page asks for): the requested weight clamped to
  the face's range, as CSS clamps it to the ``font-weight`` descriptor. Faces are measured at :data:`REF_PX` and
  scaled linearly: the page draws with ``--font-render-hinting=none`` and ``text-rendering: geometricPrecision``
  (unhinted, linear advances), and Pillow's hinted advances are whole pixels, which at 2048 px is < 0.0005 em.
* **Fail closed.** A face that cannot be read, or whose variable axes are not the table's (``faces.Family.axes``), is
  a :class:`FaceError`: never a silent fallback to another face or a guess.
* **Words.** The page's word units (``web/js/motion/text.js`` ``splitWords``): runs between whitespace (JS ``\\s`` and
  the zero-width space), no-break spaces glued inside a word. A run of collapsible whitespace (space, tab, line breaks)
  is one space wide (CSS ``white-space: normal``); a zero-width space is a break of no width. The title draws each word
  as an inline-block (board_generic ``wordSpans``): it never breaks inside unless it is wider than the column even at
  the slot's minimum size (``overflow-wrap: anywhere``; board_generic's "unavoidable" break), and then it takes whole
  lines of its own, broken after a hyphen where one fits, else between grapheme clusters (never inside one). Text in
  normal flow (the kicker, a subtitle, card text) may also break after a hyphen inside a word, which only gives the
  page fewer lines than Python counts.
* **Letter-spacing** (``SlotSpec.tracking_em``) is added after every cluster, as Chromium does (measured: a
  Devanagari consonant + vowel sign takes it once). It is counted conservatively: per code point when positive, per
  (coarse) cluster when negative.
* **Lines** are filled greedily (the line count CSS ``text-wrap: balance`` keeps), at sizes from ``max`` down in
  :data:`SIZE_STEP` px steps with ``min`` last, the slot's ``prefer_lines`` tried first: board_generic's ``fitLines``.

**Conservative bias** (plan §1.11, design §4.6). Each word's width is Pillow's advance plus :data:`SLACK_LATIN` (4%,
the design's kerning allowance) or :data:`SLACK_INDIC` (8%) of it, plus :data:`KERN_FLOOR_EM`, plus the most the page's
kerning can add **per pair** of neighbouring characters one face draws (:func:`kern_bounds`). Pillow's basic layout
applies no GPOS kerning, and Chromium's is mostly negative but positive for many pairs (``kerning``, measured in
Chromium for every pair of every vendored Latin face: Outfit ``(j`` +0.098 em, Atkinson ``qj`` +0.154 em, Inter
``ïï`` +0.166 em), which add up per pair (``f'f'f'f'``): a relative slack or a per-word floor alone is not conservative
(the B2b review F1 found ``f'f'f'f'`` certified to fit and overflowing on the page). The per-pair bound is the pair's
measured adjustment (:data:`kerning.EPSILON_MILLI` for a pair not listed); the floor is kept from the first
measurement (the plan's 4% alone was not conservative on short words: a deviation, recorded) for what no pair
measurement covers. A space is its own advance plus the letter-spacing the page adds after it when that is positive
(no vendored face kerns the space: ``kerning`` checks it).

**What is certified** (``FitResult.fits``):

* ``True``: the conservative layout fits; the page fits too (tests/determinism/test_textfit_page.py: every reliable
  word's page width is within its conservative width, and Python fits => page overflow 0 on board_generic's slots).
* ``False``: Python's conservative layout overflows at the minimum size; the page overflows too, or fits only within
  Python's margins (its own words, made as much wider as the slack and the floor make a word, would not fit: the
  plan's "within 4% slack"; the same test).
* ``None``: the text holds a cluster the measurement cannot bound (``FitResult.reliable`` is False): a cluster drawn
  by an Indic face, a combining sequence, or a character no vendored face of the stack draws (the page draws it with
  a system font). **The design's premise that 8% covers Indic shaping does not hold** (measured in Chromium against
  Pillow's basic layout, every consonant x vowel sign and consonant + virama + consonant of the five scripts, Noto Sans
  and Anek: a conjunct is drawn up to 75% narrower than its letters' advances, ``ಕೋ`` 3.1x, ``रू`` 1.9x, ``டீ`` 1.3x
  wider, because the shaped glyphs are not the characters' own), so no slack makes it an upper or a lower
  bound. ``size`` / ``lines`` / ``estimate`` are still given (the plan's 8%) as an estimate for a start size; a lint
  must not block on it. Shaping (HarfBuzz) would make Indic measurable: an owner decision, recorded for B2e.

Not wired into direct.py or preflight here (B2d/B2e). Imported only inside the Dynamic gate (it loads
``PIL.ImageFont``, which a classic build never does: tests/compose/test_dynamic_isolation.py).
"""

from __future__ import annotations

import threading
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

from PIL import Image, ImageDraw, ImageFont

from . import faces as fc
from . import kerning as kn

if TYPE_CHECKING:  # pragma: no cover
    from .templates import SlotSpec

StackRole = Literal["display", "body", "mono"]
# a width reading: Pillow's advance (+ letter-spacing), with the safety margins added, or with the slack taken off
Bias = Literal["conservative", "raw", "optimistic"]

REF_PX: Final = 2048  # measuring size (px): Pillow's whole-pixel advances are < 0.0005 em off the linear ones here
PROBE_PX: Final = 64  # the glyph-presence check's raster size (only when advance and box equal .notdef's)
SLACK_LATIN: Final = 0.04  # §4.6: "a 4% width slack covers kerning differences"
SLACK_INDIC: Final = 0.08  # §4.6: Indic 8% (an estimate only: see the module docstring)
KERN_FLOOR_EM: Final = 0.06  # per word, beyond the measured pairs (module docstring: the B2b margin, kept)
SIZE_STEP: Final = 4.0  # board_generic fitLines: 4 px steps from max down, the minimum itself last
UNCOVERED_EM: Final = 1.0  # estimate for a cluster no vendored face draws (system font; never certified)
NOTDEF_PROBE: Final = "\U0010ffff"  # a noncharacter: no cmap maps it, so it draws glyph 0 (.notdef)
JOINERS: Final = frozenset("\u200c\u200d")  # ZWNJ, ZWJ
ZWJ: Final = "\u200d"
# web/js/motion/text.js: SPACE_RE = /^[\s\u200b]+$/u (JS \s) minus GLUE_RE (no-break spaces stay inside a word)
_GLUE: Final = frozenset("\u00a0\u2007\u202f")
_JS_SPACE: Final = frozenset("\t\n\v\f\r \u00a0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff"
                             + "".join(chr(c) for c in range(0x2000, 0x200B)))
SEPARATORS: Final = frozenset((_JS_SPACE - _GLUE) | {"\u200b"})
_COLLAPSIBLE: Final = frozenset(" \t\n\r\f\v")  # CSS white-space: normal collapses a run of them into one space
# a break opportunity after these inside an over-wide word (UAX #14 HY / BA), preferred to a break between clusters
_HYPHENS: Final = frozenset("-\u2010\u2012\u2013")


# Pillow's FreeType faces are shared by every caller of this process (the API answers previews from a thread pool) and
# a FreeType face is not safe to use from two threads at once: every Pillow call on a face holds this lock.
_FT_LOCK: Final = threading.RLock()


class TextFitError(RuntimeError):
    """Text fit cannot measure (fail closed)."""


class FaceError(TextFitError):
    """A vendored face cannot be read, or is not what the face table says (its variable axes)."""


# --- characters and clusters ---------------------------------------------------------------------------------------


def ignorable(ch: str) -> bool:
    """A default-ignorable character the page draws with no width (format characters such as ZWJ, ZWNJ, the soft
    hyphen and the BOM; variation selectors; Mongolian free variation selectors)."""
    cp = ord(ch)
    return (unicodedata.category(ch) == "Cf" or 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF
            or 0x180B <= cp <= 0x180F or ch == "\u034f")


def _extends(cluster: str, ch: str) -> bool:
    last = cluster[-1]
    cp = ord(ch)
    if unicodedata.category(ch)[0] == "M" or ch in JOINERS or last == ZWJ:
        return True  # Extend / SpacingMark (UAX #29 GB9, GB9a), ZWJ sequences (GB11)
    if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF or 0x1F3FB <= cp <= 0x1F3FF or 0xE0020 <= cp <= 0xE007F:
        return True  # variation selectors, emoji modifiers, tag characters
    if last == "\r" and ch == "\n":
        return True  # GB3
    # an Indic conjunct: a letter after a virama (GB9c, applied to every script with a virama: coarser than ICU's for
    # Tamil and Kannada, which only makes breaking and negative letter-spacing more conservative)
    return unicodedata.combining(last) == 9 and unicodedata.category(ch) == "Lo"


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


def words(text: str) -> tuple[tuple[str, str], ...]:
    """``(word, separator after it)`` pairs: the page's word units (``splitWords``: whitespace-separated, no-break
    spaces inside a word, leading whitespace dropped); the last separator is ``""`` or trailing whitespace."""
    out: list[tuple[str, str]] = []
    word, sep = "", ""
    for ch in text:
        if ch in SEPARATORS:
            if word:
                sep += ch
            continue
        if sep:
            out.append((word, sep))
            word, sep = "", ""
        word += ch
    if word:
        out.append((word, sep))
    return tuple(out)


def tracking_count(clusters: Sequence[str], tracking_em: float) -> int:
    """How many times the page adds letter-spacing to these clusters, counted so the width is never under-estimated:
    once per drawn cluster (Chromium's rule) when the spacing is negative, once per drawn code point (never fewer)
    when it is positive."""
    if tracking_em > 0:
        return sum(1 for c in clusters for ch in c if not ignorable(ch))
    return sum(1 for c in clusters if any(not ignorable(ch) for ch in c))


# --- faces (Pillow, basic layout; fail closed) -----------------------------------------------------------------------


@dataclass(frozen=True)
class FaceKey:
    """One measurable face: its file and, for a variable face, the ``wght`` it is set to."""

    path: str
    wght: int | None
    axes: tuple[fc.Axis, ...] = ()


@lru_cache(maxsize=256)
def load_face(key: FaceKey, size: int = REF_PX) -> ImageFont.FreeTypeFont:
    """The face at ``size`` px with Pillow's basic layout, its variable axes checked against the table and set.
    :class:`FaceError` when the file cannot be read or its axes are not the table's (fail closed)."""
    with _FT_LOCK:
        return _load(key, size)


def _load(key: FaceKey, size: int) -> ImageFont.FreeTypeFont:
    try:
        font = ImageFont.truetype(key.path, size, layout_engine=ImageFont.Layout.BASIC)
    except (OSError, ValueError) as exc:
        raise FaceError(f"cannot read the face {key.path}: {exc}") from exc
    if font.layout_engine != ImageFont.Layout.BASIC:  # pragma: no cover - Pillow honours the argument
        raise FaceError(f"{key.path}: Pillow did not take the basic layout")
    try:
        found = font.get_variation_axes()
    except OSError:
        found = []
    want = [{"minimum": a.minimum, "default": a.default, "maximum": a.maximum} for a in key.axes]
    have = [{k: int(a[k]) for k in ("minimum", "default", "maximum")} for a in found]
    if have != want:
        raise FaceError(f"{key.path}: variable axes {have} are not the face table's {want}")
    if key.axes:
        if key.wght is None:
            raise FaceError(f"{key.path}: a variable face is measured at a set weight, never its file default")
        try:
            font.set_variation_by_axes([key.wght])
        except OSError as exc:  # pragma: no cover - the axes were just read
            raise FaceError(f"{key.path}: cannot set wght {key.wght}: {exc}") from exc
    return font


def face_key(face: fc.Face, weight: int, web_dir: Path | str | None = None) -> FaceKey:
    """The :class:`FaceKey` the page draws ``face`` with at ``weight`` (a variable face's ``wght`` clamped to its range,
    as CSS clamps it to the face's ``font-weight`` descriptor)."""
    family = fc.FAMILIES[face.family]
    path = str(fc.web_path(face, web_dir))
    if not family.variable:
        return FaceKey(path, None)
    lo, hi = face.weight
    return FaceKey(path, min(max(int(weight), lo), hi), family.axes)


@lru_cache(maxsize=256)
def _notdef(key: FaceKey) -> tuple[float, tuple[float, float, float, float]]:
    with _FT_LOCK:
        font = load_face(key)
        return font.getlength(NOTDEF_PROBE), font.getbbox(NOTDEF_PROBE)


@lru_cache(maxsize=65536)
def has_glyph(key: FaceKey, ch: str) -> bool:
    """Whether the face's cmap maps ``ch``: Pillow draws an unmapped character as glyph 0 (.notdef), so the character's
    advance and box equal .notdef's and, at :data:`PROBE_PX`, its raster too. A mapped glyph drawn exactly like .notdef
    would read as missing (the page then takes the next family): none of the vendored faces has one."""
    with _FT_LOCK:
        font = load_face(key)
        if (font.getlength(ch), font.getbbox(ch)) != _notdef(key):
            return True
        probe = load_face(key, PROBE_PX)
        return _raster(probe, ch) != _raster(probe, NOTDEF_PROBE)


def _raster(font: ImageFont.FreeTypeFont, ch: str) -> bytes:
    """``ch`` drawn alone on a fixed canvas (room for any glyph's overhang at :data:`PROBE_PX`)."""
    canvas = Image.new("L", (4 * PROBE_PX, 3 * PROBE_PX))
    ImageDraw.Draw(canvas).text((PROBE_PX, PROBE_PX), ch, font=font, fill=255)
    return canvas.tobytes()


@lru_cache(maxsize=65536)
def advance_em(key: FaceKey, text: str) -> float:
    """The advance of ``text`` in one face, in em (Pillow's basic layout: the characters' own glyphs, the face's legacy
    ``kern`` table if it has one, no shaping)."""
    with _FT_LOCK:
        return load_face(key).getlength(text) / REF_PX


# --- measuring a text -------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cluster:
    """A grapheme cluster and how it is measured: ``key`` the face drawing it (None: zero width when ``text`` is only
    ignorable characters, else no vendored face draws it), ``indic`` drawn by an Indic family."""

    text: str
    key: FaceKey | None
    indic: bool = False
    uncovered: bool = False

    @property
    def drawn(self) -> str:
        """The characters that take width (default-ignorable ones are dropped, as the page draws them)."""
        return "".join(ch for ch in self.text if not ignorable(ch))

    @property
    def em(self) -> float:
        if self.uncovered:
            return UNCOVERED_EM
        return advance_em(self.key, self.drawn) if self.key is not None and self.drawn else 0.0

    @property
    def reliable(self) -> bool:
        """Whether Pillow's basic layout bounds this cluster's width on the page: one drawn character of a non-Indic
        face (a combining sequence, an Indic cluster or an uncovered character is shaped or drawn by a system font)."""
        return not self.uncovered and not self.indic and len(self.drawn) <= 1


@dataclass(frozen=True)
class Word:
    """One word unit: its clusters, ``em`` the advance of its face runs (em, Pillow), ``sep_em`` the width of the
    whitespace after it (0 for the last word) and ``sep_units`` how many characters the page draws for it (a collapsed
    run of spaces is one; each takes the letter-spacing)."""

    text: str
    clusters: tuple[Cluster, ...]
    em: float
    sep_em: float
    sep_units: int = 0
    kern_pos: float = 0.0  # em: the most its pairs can draw it wider (:func:`kern_bounds`)
    kern_neg: float = 0.0  # em: the most its pairs can draw it narrower

    @property
    def reliable(self) -> bool:
        return all(c.reliable for c in self.clusters)

    @property
    def indic(self) -> bool:
        return any(c.indic for c in self.clusters)

    @property
    def slack(self) -> float:
        return SLACK_INDIC if self.indic else SLACK_LATIN


@dataclass(frozen=True)
class Measured:
    """A text measured for one slot role: its words, the role's stack (family ids), weight and letter-spacing."""

    text: str
    words: tuple[Word, ...]
    stack: tuple[str, ...]
    weight: int
    tracking_em: float

    @property
    def reliable(self) -> bool:
        return all(w.reliable for w in self.words)

    @property
    def uncovered(self) -> tuple[str, ...]:
        """The clusters no vendored face of the stack draws, in order, each once."""
        return tuple(dict.fromkeys(c.text for w in self.words for c in w.clusters if c.uncovered))

    def track_em(self, clusters: Sequence[Cluster]) -> float:
        return self.tracking_em * tracking_count([c.text for c in clusters], self.tracking_em)

    def width(self, word: Word, size: float, bias: Bias = "conservative",
              clusters: Sequence[Cluster] | None = None) -> float:
        """A word's (or a piece of it: ``clusters``) width in px at ``size``: ``raw`` Pillow's advance plus the
        letter-spacing; ``conservative`` + the slack, :data:`KERN_FLOOR_EM` and the most its kerning pairs add
        (:func:`kern_bounds`, per pair); ``optimistic`` - the slack and the most its pairs take off."""
        part = word.clusters if clusters is None else tuple(clusters)
        em = word.em if clusters is None else sum(c.em for c in part)
        pos, neg = (word.kern_pos, word.kern_neg) if clusters is None else kern_bounds(part)
        if bias == "conservative":
            em = em * (1 + word.slack) + KERN_FLOOR_EM + pos
        elif bias == "optimistic":
            em = max(0.0, em * (1 - word.slack) - neg)
        return size * (em + self.track_em(part))

    def sep(self, word: Word, size: float) -> float:
        """The whitespace after ``word`` in px at ``size``: its advance, and the letter-spacing the page adds after each
        of its characters when the spacing is positive (left out when it is negative: never under-estimated)."""
        return size * (word.sep_em + (self.tracking_em * word.sep_units if self.tracking_em > 0 else 0.0))


def _cluster(text: str, stack: Sequence[str], weight: int, web_dir: Path | str | None) -> Cluster:
    drawn = [ch for ch in text if not ignorable(ch)]
    if not drawn:
        return Cluster(text, None)
    for fid in stack:
        rows = fc.faces_of(fid)
        matched = fc.match_weight(tuple(r.weight for r in rows), weight)
        for row in reversed(rows):  # CSS Fonts 4 §4.5: the last declared face of a range is tried first
            if row.weight != matched or not all(row.covers(ord(ch)) for ch in drawn):
                continue
            key = face_key(row, weight, web_dir)
            if all(has_glyph(key, ch) for ch in drawn):
                return Cluster(text, key, indic=fc.FAMILIES[fid].role == "indic")
    return Cluster(text, None, uncovered=True)


def kern_bounds(clusters: Sequence[Cluster]) -> tuple[float, float]:
    """The most the page's kerning can draw these clusters wider, and narrower, than their advances (em): per pair of
    neighbouring clusters one Latin face draws (a face kerns only its own glyphs; a default-ignorable cluster between
    them is skipped, a cluster no vendored face draws ends the run), the measured bound of the pair (``kerning``:
    its positive adjustment, else :data:`kerning.EPSILON_MILLI`) and the face's most negative adjustment. Pairs of an
    Indic face add nothing: such text is never certified (an estimate only)."""
    table = kn.load()
    pos = neg = 0.0
    prev: Cluster | None = None
    for c in clusters:
        if c.uncovered:
            prev = None
            continue
        if c.key is None:  # only default-ignorable characters: no glyph, no pair
            continue
        if not c.indic:
            # the pair with the previous cluster, and the pairs inside a cluster of several characters
            chars = ([prev.drawn[-1]] if prev is not None and prev.key == c.key else []) + list(c.drawn)
            if len(chars) > 1:
                face = table.get(kn.face_name(c.key.path))
                if face is None:
                    raise TextFitError(f"no measured kerning for the face {c.key.path}: run {kn.GENERATOR}")
                for a, b in zip(chars, chars[1:], strict=False):
                    pos += face.bound(a, b)
                    neg -= face.most_negative
        prev = c
    return pos, neg


def _runs_em(clusters: Sequence[Cluster]) -> float:
    """The advance of consecutive clusters, measured as runs of one face each, as the page shapes a run (Pillow would
    apply a legacy ``kern`` table inside a run; none of the vendored faces has one: their kerning is GPOS, pinned by a
    test, so the slack and the floor carry all of it)."""
    total = 0.0
    run: list[Cluster] = []

    def flush() -> float:
        if not run:
            return 0.0
        key = run[0].key
        if key is None:
            return sum(c.em for c in run)
        return advance_em(key, "".join(c.drawn for c in run))

    for c in clusters:
        if run and (c.key != run[0].key or c.uncovered or run[0].uncovered):
            total += flush()
            run = []
        run.append(c)
    return total + flush()


def _separator(sep: str, stack: Sequence[str], weight: int, web_dir: Path | str | None) -> tuple[float, int]:
    """The width (em) of the whitespace between two words and how many characters the page draws for it: a run of
    collapsible whitespace is one space (CSS ``white-space: normal``); other spaces (en space, ideographic space, ...)
    keep their own width; the zero-width space and the BOM have none (counted, so a positive letter-spacing is never
    left out)."""
    total, units = 0.0, 0
    if any(ch in _COLLAPSIBLE for ch in sep):
        total += _cluster(" ", stack, weight, web_dir).em
        units += 1
    for ch in sep:
        if ch not in _COLLAPSIBLE:  # the zero-width space and the BOM are default-ignorable: no advance
            units += 1
            total += _cluster(ch, stack, weight, web_dir).em
    return total, units


def stack_for(fonts: fc.Fonts, role: str, language: str | None) -> tuple[str, ...]:
    """The family ids a slot of type ``role`` is drawn with (``faces.stack``); ``ValueError`` for a role no stack has."""
    if role not in ("display", "body", "mono"):
        raise ValueError(f"no font stack for the type role {role!r} (display, body or mono)")
    return fc.stack(fonts, role, language)  # type: ignore[arg-type]


def measure(text: str, *, fonts: fc.Fonts, role: str, weight: int, language: str | None = "",
            tracking_em: float = 0.0, web_dir: Path | str | None = None) -> Measured:
    """Measure ``text`` for a slot of type ``role`` at ``weight`` (``presentation.fonts`` in either form; ``language``
    the lecture's board language, which orders the Indic families as the stage does)."""
    stack = stack_for(fonts, role, language)
    out: list[Word] = []
    pairs = words(str(text))
    for i, (word, sep) in enumerate(pairs):
        clusters = tuple(_cluster(g, stack, int(weight), web_dir) for g in graphemes(word))
        sep_em, units = _separator(sep, stack, int(weight), web_dir) if i + 1 < len(pairs) else (0.0, 0)
        out.append(Word(word, clusters, _runs_em(clusters), sep_em, units, *kern_bounds(clusters)))
    return Measured(str(text), tuple(out), stack, int(weight), float(tracking_em))


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


def _pieces(m: Measured, word: Word, size: float, width: float, bias: str) -> list[tuple[str, float]]:
    """An over-wide word broken into lines of at most ``width`` (``overflow-wrap: anywhere``): greedily, after the last
    hyphen that fits (a normal break opportunity, which CSS takes first), else between clusters; never inside a
    cluster (a cluster wider than the line stands alone and overflows sideways)."""
    out: list[tuple[str, float]] = []
    cl = word.clusters
    start = 0
    while start < len(cl):
        end = start + 1
        hyphen = None
        while end < len(cl) and m.width(word, size, bias, cl[start:end + 1]) <= width:  # type: ignore[arg-type]
            end += 1
            prev, nxt = cl[end - 1].text, cl[end].text if end < len(cl) else ""
            if prev[-1:] in _HYPHENS and nxt[:1] and not nxt[:1].isdigit():
                hyphen = end
        if end < len(cl) and hyphen is not None and hyphen > start:
            end = hyphen
        piece = cl[start:end]
        out.append(("".join(c.text for c in piece), m.width(word, size, bias, piece)))  # type: ignore[arg-type]
        start = end
    return out


def _other(bias: Bias) -> Bias:
    return "optimistic" if bias == "conservative" else "conservative" if bias == "optimistic" else "raw"


def word_kinds(m: Measured, width: float, min_size: float, bias: Bias = "conservative") -> tuple[str, ...]:
    """How the page treats each word wider than the box (board_generic ``layout``: a word is "unavoidable", and may
    break, when the page measures it wider than the box at the slot's MINIMUM size; any other word must stay whole):
    ``"break"`` when even the most favourable reading (the slack the other way and every kerning pair at its face's
    most negative measured adjustment: ``kerning``) is wider than the box at the minimum, so the page certainly breaks
    it (the slack alone was not that: the page draws ``AVAVAV...`` up to 12% narrower than Pillow; B2b review F2); ``"whole"`` when even ``bias``'s reading fits at the minimum, so the page certainly
    keeps it whole; ``"either"`` when the page may do either."""
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
        sep_w = m.sep(w, size)
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
        total = sum(m.width(w, size, bias) + (m.sep(w, size) if i + 1 < len(m.words) else 0.0)
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
             prefer_lines: int | None = None, step: float = SIZE_STEP, web_dir: Path | str | None = None) -> FitResult:
    """Measure and fit ``text`` (:func:`measure`, then :func:`fit`)."""
    m = measure(text, fonts=fonts, role=role, weight=weight, language=language, tracking_em=tracking_em,
                web_dir=web_dir)
    return fit(m, width=width, size=size, max_lines=max_lines, prefer_lines=prefer_lines, step=step)


def fit_slot(text: str, spec: SlotSpec, *, fonts: fc.Fonts, language: str | None = "",
             width: float | None = None, step: float = SIZE_STEP, web_dir: Path | str | None = None) -> FitResult:
    """Fit ``text`` to a template slot (``templates.SlotSpec``, plan §1.6): its box ``w`` (``width`` overrides it where
    the template draws the text in a narrower box than the slot's: board_generic's kicker sits right of its 12 px pip
    and 18 px gap), size range, line limit and preference, type role, weight and letter-spacing."""
    return fit_text(text, width=spec.w if width is None else width, size=spec.size, max_lines=spec.max_lines,
                    role=spec.face, weight=spec.weight, fonts=fonts, language=language, tracking_em=spec.tracking_em,
                    prefer_lines=spec.prefer_lines, step=step, web_dir=web_dir)


def clear_caches() -> None:
    """Drop the loaded faces and measured advances (tests; a process normally keeps them)."""
    for f in (load_face, _notdef, has_glyph, advance_em, kn.load):
        f.cache_clear()
