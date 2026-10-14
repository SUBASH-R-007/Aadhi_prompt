"""Kerning and contexts of the vendored Latin faces, measured once in Chromium (B2b review F1, F2; DYNAMIC_VIDEO_DESIGN
§4.6, B2 plan §1.11): what Pillow's basic layout does not apply and the page does, bounded on both sides.

The page shapes text with HarfBuzz (``font-kerning: normal``, common ligatures, contextual alternates,
``text-rendering: geometricPrecision``: dynamic.css), so characters one face draws are drawn wider or narrower than
their advances. Pillow's basic layout applies none of it (no GPOS, no GSUB; none of the vendored faces has a legacy
``kern`` table, pinned by tests/compose/dynamic/test_textfit.py). The review measured what a per-word allowance misses:
positive pair adjustments are not rare (Outfit ``(j`` +0.098 em, Atkinson ``qj`` +0.154 em, Inter ``ïï`` +0.166 em)
and add up per pair (``f'f'f'f'``); a face's ligatures and contextual alternates change a glyph by its neighbours,
which no pair shows (Inter draws the ``x`` of ``2x3`` as a wider ``×``; Outfit's ``ffi`` is one glyph); and some of
them reach further, or chain along a run of any length (Inter's ``*`` beside capitals and punctuation: ``A*****`` is
drawn 0.24 em wider than its pairs and triples add up to, and ``*:<H`` 0.144 em, through a colon and a less-than sign
whose own alternates change no width; ``10x1``, whose ``x`` is a ``×`` where ``0x1``'s is not; Lexend's and Inter's
digits before a fraction slash; Inter's arrows ``<==>``; B2b review F1, rounds 1 and 2).

So the table describes a face's text as **pairs, triples and the contexts that reach further**. For a run of
characters one face draws, ``E(run)`` (its drawn width less its characters' advances) is

    E(a1..an) = sum of E(pair) over its n-1 pairs + sum of D(triple) over its n-2 triples,

where ``D(abc) = E(abc) - E(ab) - E(bc)`` is what a triple adds beyond its two pairs (zero unless the face takes the
three characters into one context: a ligature, a contextual alternate, contextual kerning). The identity is exact when
no four neighbouring characters interact beyond their triples (``E(abcd) = E(abc) + E(bcd) - E(bc)``); a face's runs
where four do, or may, are never bounded (``FaceKerning.bounded``), so ``textfit`` never certifies them. Per face:

* ``positive`` / ``negative``: every pair's adjustment, bounded above (rounded up, plus :data:`MARGIN_MILLI`) and below
  (rounded down, less it), in 1/1000 em; a pair not listed is bounded by +/- :data:`EPSILON_MILLI`.
* ``up`` / ``down``: every **context** triple's ``D`` (``|D| >`` :data:`CONTEXT_TOL_MILLI` in a measurement), bounded
  above and below likewise (a value of 0 is not listed); a triple not listed adds nothing beyond its pairs (its ``D``
  is float noise, < :data:`CONTEXT_TOL_MILLI`, which the pairs' margins absorb: each pair's bound is at least
  :data:`MARGIN_MILLI` beyond its measurement, and a run has one pair more than it has triples).
* ``chain``: the face's chain characters (:func:`chain_characters`: the middle characters of its context triples that
  are not letters or digits; Inter's case-sensitive punctuation and operators, Lexend's fraction slash). Their
  alternates pass along a run of them through alternates that change no width, so no measurement of a fixed length
  finds where such a chain ends: a run of four or more characters with two of them among any four neighbouring
  characters is never bounded.
* ``open`` / ``open4``: where four neighbouring characters interact beyond their triples. Every **near** triple
  (:func:`near_triples`: every context triple, and every triple that puts a context triple's middle character between
  characters its contexts have on its left and on its right, which finds the exceptions of a rule such as Inter's
  ``0x1``; not one holding two chain characters) is measured again with each character before and after it (``abc``
  -> ``xabc``, ``abcx``); a four-character run whose ``E(abcd) - E(abc) - E(bcd) + E(bc)`` exceeds
  :data:`OPEN_TOL_MILLI` is open. They are stored as a cover (:func:`cover`): a triple standing for every open
  four-character run that holds it when it stands for at least :data:`COVER_MIN` of them (Inter's ``***``, Lexend's
  ``1⁄2``), else the four-character runs themselves (Outfit's ``ffiw``, whose ``ffi`` ligature kerns with the ``w``). A
  run of four or more characters holding any of them is never bounded.

``textfit`` sums a run's pair and triple bounds above (its conservative width) and below (its optimistic width: the
reading from which it decides that the page certainly breaks an over-wide word, B2b review F2). What remains assumed:
that a character the face never takes into a context triple, or takes in only with neighbours its near triples do not
hold, interacts with nothing four or more characters long, and that no five characters interact where no four do
outside a chain. tests/determinism/test_kerning_table.py checks it on random runs of every ``latin`` face (mostly of
the characters its contexts hold) against their bounds, both sides; the HarfBuzz shaping step that follows B2b
measures shaped runs instead of bounding them.

How it is measured (:func:`measure`, needs Chromium and ``web/vendor``): each Latin face file (not the Indic faces,
whose text ``textfit`` never certifies) is loaded into a blank page as its own ``FontFace``; the characters are the
ones its ``unicode-range`` holds and its cmap maps (``textfit.has_glyph``, the same test ``textfit`` resolves a cluster
with), the space included; canvas widths at :data:`MEASURE_PX` px with the page's kerning, ligature and rendering
settings, once with no letter-spacing (ligatures and contextual alternates on) and once with letter-spacing (Chromium
then drops them, as on a slot with tracking); a bound covers both. Every ordered pair, every ordered triple (on
:data:`WORKERS` pages in parallel), then every near triple's four-character extensions. The launch flags are the
render worker's (``CHROMIUM_ARGS``, ``DETERMINISM_ARGS``). No vendored face kerns the space wider, takes it into a
context or an open run (checked by the generator): a word's kerning ends at its edges. Triples and runs holding a
combining mark are not stored (``textfit`` never certifies a combining sequence).

    python -m aadhi.compose.dynamic.kerning            # measure and write aadhi/compose/dynamic/kerning.json
    python -m aadhi.compose.dynamic.kerning --check    # exit 1 when the committed table differs from a measurement

A measurement takes about 90 minutes (the ``latin-ext`` triples, Inter's near triples). The committed table is read
at run time (:func:`load`, JSON only: no Chromium); the slow test tests/determinism/test_kerning_table.py measures
the faces again and checks the table still bounds them (on any platform: a stored bound is never inside a measured
value), and tests/determinism/test_textfit_page.py checks the bounds on the real render page. Regenerate after a
face changes (``faces.py``, ``npm run vendor``).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import heapq
import json
import math
import os
import sys
import unicodedata
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from . import faces as fc

VERSION: Final = 3  # 2: triples beyond the pairs' bounds; 3: both sides, context triples and open runs (B2b review)
TABLE_PATH: Final = Path(__file__).with_name("kerning.json")
TABLE_NAME: Final = "aadhi/compose/dynamic/kerning.json"
GENERATOR: Final = "python -m aadhi.compose.dynamic.kerning"
MEASURE_PX: Final = 1000  # 1 px = 1/1000 em
EPSILON_MILLI: Final = 1  # pairs adjusted by at most this (1/1000 em) either way are not listed; each is bounded by it
MARGIN_MILLI: Final = 0.1  # added before rounding up (subtracted before rounding down): float noise between machines
CONTEXT_TOL_MILLI: Final = 0.05  # a triple adding more than this beyond its pairs, either way, is a context
OPEN_TOL_MILLI: Final = 0.05  # four characters adding more than this beyond their triples, either way, are open
COVER_MIN: Final = 2  # a triple stands for the open four-character runs holding it when there are at least this many
LETTER_SPACING: Final = ("0px", "10px")  # without tracking (ligatures, contexts on), and with it (Chromium drops them)
WORKERS: Final = max(1, min(12, (os.cpu_count() or 2) - 2))  # pages measuring triples and runs at once


class KerningError(RuntimeError):
    """The kerning table is missing, damaged or not the face table's, or cannot be measured (fail closed)."""


def _frozen(d: Mapping[str, float] | None = None) -> Mapping[str, float]:
    return MappingProxyType(dict(d or {}))


@dataclass(frozen=True)
class FaceKerning:
    """One face's measured bounds, in em: ``positive`` / ``negative`` (pair -> its adjustment's bound above / below,
    listed beyond +/- :data:`EPSILON_MILLI`), ``up`` / ``down`` (context triple -> what it adds beyond its pairs,
    bounded above / below, listed when not 0), ``open`` / ``open4`` (the triples and four-character runs a run of
    four or more characters must not hold to be bounded: :func:`cover`) and ``chain`` (the characters two of which a
    run of four or more must not hold among any four neighbouring characters: :func:`chain_characters`)."""

    positive: Mapping[str, float] = field(default_factory=_frozen)
    negative: Mapping[str, float] = field(default_factory=_frozen)
    up: Mapping[str, float] = field(default_factory=_frozen)
    down: Mapping[str, float] = field(default_factory=_frozen)
    open: frozenset[str] = frozenset()
    open4: frozenset[str] = frozenset()
    chain: frozenset[str] = frozenset()

    def upper(self, a: str, b: str) -> float:
        """The most the pair ``ab`` can draw wider than its two advances, in em."""
        return self.positive.get(a + b, EPSILON_MILLI / 1000)

    def lower(self, a: str, b: str) -> float:
        """The most the pair ``ab`` can draw narrower than its two advances, in em (<= 0 unless it always widens)."""
        return self.negative.get(a + b, -EPSILON_MILLI / 1000)

    def context(self, a: str, b: str, c: str) -> tuple[float, float]:
        """What the triple ``abc`` can add beyond its pairs, in em: (above, below)."""
        t = a + b + c
        return self.up.get(t, 0.0), self.down.get(t, 0.0)

    def bounds(self, run: str) -> tuple[float, float]:
        """How much wider (first) and narrower-or-wider (second: the lower bound, usually negative) than its advances
        the face can draw ``run``, in em: its pairs' and triples' bounds summed (the module docstring)."""
        hi = sum(self.upper(a, b) for a, b in zip(run, run[1:], strict=False))
        lo = sum(self.lower(a, b) for a, b in zip(run, run[1:], strict=False))
        for i in range(len(run) - 2):
            up, down = self.context(*run[i:i + 3])
            hi += up
            lo += down
        return hi, lo

    def bounded(self, run: str) -> bool:
        """Whether :meth:`bounds` holds for ``run``: three characters or fewer (measured whole), or no open triple, no
        open four-character run and no four neighbouring characters holding two chain characters inside it."""
        if len(run) <= 3:
            return True
        return not (any(run[i:i + 3] in self.open for i in range(len(run) - 2))
                    or any(run[i:i + 4] in self.open4 for i in range(len(run) - 3))
                    or any(sum(ch in self.chain for ch in run[i:i + 4]) >= 2 for i in range(len(run) - 3)))


def is_mark(ch: str) -> bool:
    """A combining mark (Unicode category M*): a cluster holding one is never certified by ``textfit`` (its width is
    the shaper's mark positioning, not an advance), so the triples and runs holding one are not stored (their pairs
    are)."""
    return unicodedata.category(ch).startswith("M")


def table_faces() -> tuple[fc.Face, ...]:
    """The faces the table covers: every vendored face of a family that is not Indic (declaration order)."""
    return tuple(f for f in fc.FACES if fc.FAMILIES[f.family].vendored and fc.FAMILIES[f.family].role != "indic")


def face_name(face_or_path: fc.Face | str | Path) -> str:
    """The table's key for a face: its file name (``inter-latin-400-normal.woff2``)."""
    path = face_or_path.file if isinstance(face_or_path, fc.Face) else face_or_path
    return Path(str(path).replace("\\", "/")).name


def _split(seqs: str, n: int) -> list[str]:
    if not isinstance(seqs, str):
        raise TypeError(f"not a string: {seqs!r}")
    chars = list(seqs)
    if not chars or len(chars) % n:
        raise ValueError(f"not a whole number of {n}-character sequences: {seqs!r}")
    return ["".join(chars[i:i + n]) for i in range(0, len(chars), n)]


def _entries(groups: Mapping[str, str], n: int, valid: Any) -> dict[str, float]:
    """``{"<milli>": "<sequence><sequence>..."}`` (sequences of ``n`` characters) -> ``{sequence: em}``; every value
    ``valid`` and spelt canonically, every group a whole number of sequences, no sequence twice (``ValueError``
    otherwise)."""
    if not isinstance(groups, dict):
        raise TypeError(f"not a mapping: {groups!r}")
    out: dict[str, float] = {}
    for milli, seqs in groups.items():
        value = int(milli)
        if not valid(value) or str(value) != milli:
            raise ValueError(f"bad entry {milli!r}")
        for seq in _split(seqs, n):
            if seq in out:
                raise ValueError(f"{seq!r} listed twice")
            out[seq] = value / 1000
    return out


def _patterns(seqs: str, n: int) -> frozenset[str]:
    """``"<sequence><sequence>..."`` (sequences of ``n`` characters) -> the set (sorted, none twice, or
    ``ValueError``); ``""`` is the empty set."""
    if isinstance(seqs, str) and seqs == "":
        return frozenset()
    out = _split(seqs, n)
    if out != sorted(set(out)):
        raise ValueError(f"{n}-character sequences not sorted or listed twice")
    return frozenset(out)


ROW_KEYS: Final = ("chain", "down", "negative", "open", "open4", "positive", "up")


def parse_row(row: Any) -> FaceKerning:
    """A table row -> :class:`FaceKerning` (``ValueError`` / ``TypeError`` / ``KeyError`` when damaged)."""
    if not isinstance(row, dict) or sorted(row) != list(ROW_KEYS):
        raise ValueError(f"keys {sorted(row) if isinstance(row, dict) else row!r} are not {list(ROW_KEYS)}")
    return FaceKerning(
        positive=_frozen(_entries(row["positive"], 2, lambda v: v > EPSILON_MILLI)),
        negative=_frozen(_entries(row["negative"], 2, lambda v: v < -EPSILON_MILLI)),
        up=_frozen(_entries(row["up"], 3, lambda v: v != 0)),
        down=_frozen(_entries(row["down"], 3, lambda v: v != 0)),
        open=_patterns(row["open"], 3),
        open4=_patterns(row["open4"], 4),
        chain=_patterns(row["chain"], 1),
    )


@lru_cache(maxsize=4)
def load(path: Path = TABLE_PATH) -> Mapping[str, FaceKerning]:
    """The committed table: face file name -> :class:`FaceKerning`. Fails closed (:class:`KerningError`) when the
    file is missing or damaged, or its faces are not exactly :func:`table_faces`."""
    try:
        data = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KerningError(f"{TABLE_NAME} cannot be read ({exc}): run {GENERATOR}") from exc
    if not isinstance(data, dict) or data.get("version") != VERSION or data.get("epsilon") != EPSILON_MILLI:
        raise KerningError(f"{TABLE_NAME} is not a version {VERSION} table (epsilon {EPSILON_MILLI}): run {GENERATOR}")
    faces = data.get("faces")
    want = {face_name(f) for f in table_faces()}
    if not isinstance(faces, dict) or set(faces) != want:
        raise KerningError(f"{TABLE_NAME} does not cover exactly the face table's faces: run {GENERATOR}")
    out: dict[str, FaceKerning] = {}
    for name, row in faces.items():
        try:
            out[name] = parse_row(row)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise KerningError(f"{TABLE_NAME}: face {name} is damaged ({exc}): run {GENERATOR}") from exc
    return MappingProxyType(out)


# --- measurement (Chromium; the generator and the slow test) ---------------------------------------------------------

_SETUP_JS = """
  const face = new FontFace('KernProbe', `url(data:font/woff2;base64,${b64})`);
  await face.load();
  document.fonts.add(face);
  const c = document.createElement('canvas').getContext('2d');
  c.font = `${PX}px KernProbe`;
  c.fontKerning = 'normal';
  c.textRendering = 'geometricPrecision';
  c.letterSpacing = spacing;
  const W = new Map();
  // a character's and a pair's widths are kept (longer strings are measured each time: a page holds the pairs only)
  const w = (s) => {
    let v = W.get(s);
    if (v === undefined) { v = c.measureText(s).width; if (s.length <= 2) W.set(s, v); }
    return v;
  };
""".replace("${PX}", str(MEASURE_PX))

# Every pair whose adjustment exceeds ``floor`` either way: [pair, adjustment].
_PAIRS_JS = """async ([b64, chars, spacing, floor]) => {""" + _SETUP_JS + """
  try {
    const out = [];
    for (const a of chars) for (const b of chars) {
      const d = w(a + b) - w(a) - w(b);
      if (Math.abs(d) > floor) out.push([a + b, d]);
    }
    return out;
  } finally {
    document.fonts.delete(face);
  }
}"""

# Every triple whose first character is one of ``firsts`` and that adds more than ``tol`` beyond its pairs, either way:
# [triple, D] (D = w(abc) - w(ab) - w(bc) + w(b), the module docstring's E(abc) - E(ab) - E(bc)).
_TRIPLES_JS = """async ([b64, chars, firsts, spacing, tol]) => {""" + _SETUP_JS + """
  try {
    const out = [];
    for (const a of firsts) for (const b of chars) {
      const ab = a + b, wab = w(ab), wb = w(b);
      for (const d of chars) {
        const x = w(ab + d) - wab - w(b + d) + wb;
        if (Math.abs(x) > tol) out.push([ab + d, x]);
      }
    }
    return out;
  } finally {
    document.fonts.delete(face);
  }
}"""

# Each context triple's D: [triple, D].
_D3_JS = """async ([b64, chars, ctxs, spacing]) => {""" + _SETUP_JS + """
  try {
    return ctxs.map((t) => [t, w(t) - w(t.slice(0, 2)) - w(t.slice(1)) + w(t[1])]);
  } finally {
    document.fonts.delete(face);
  }
}"""

# Every four-character run one of the triples ``trips`` starts or ends (with each of ``chars``; not a run holding two
# of the ``chain`` characters, which is never bounded) whose characters add more than ``tol`` beyond their triples,
# either way: [run, E(abcd) - E(abc) - E(bcd) + E(bc)] (= w(abcd) - w(abc) - w(bcd) + w(bc)).
_RUNS_JS = """async ([b64, chars, trips, spacing, tol, chain]) => {""" + _SETUP_JS + """
  try {
    const out = [];
    for (const t of trips) {
      const ab = t.slice(0, 2), bc = t.slice(1), wt = w(t);
      const linked = [...t].some((ch) => chain.includes(ch));
      for (const x of chars) {
        if (linked && chain.includes(x)) continue;
        const l = w(x + t) - w(x + ab) - wt + w(ab);
        if (Math.abs(l) > tol) out.push([x + t, l]);
        const r = w(t + x) - wt - w(bc + x) + w(bc);
        if (Math.abs(r) > tol) out.push([t + x, r]);
      }
    }
    return out;
  } finally {
    document.fonts.delete(face);
  }
}"""


def characters(face: fc.Face, web_dir: Path | str | None = None) -> list[str]:
    """The characters a face is measured for: its ``unicode-range`` and cmap (``textfit.has_glyph``), from the space
    up, without default-ignorable characters (they take no width and form no pair: ``textfit``)."""
    from . import textfit as tf

    key = tf.face_key(face, face.weight[0], web_dir)
    out = []
    for lo, hi in face.ranges:
        for cp in range(max(lo, 0x20), hi + 1):
            ch = chr(cp)
            if not tf.ignorable(ch) and tf.has_glyph(key, ch):
                out.append(ch)
    return list(dict.fromkeys(out))


@dataclass
class Measurement:
    """One face measured in Chromium (1/1000 em, every spacing of :data:`LETTER_SPACING`): ``pairs`` (pair -> its
    highest and lowest adjustment, for the pairs beyond the reporting floor), ``contexts`` (context triple -> its
    highest and lowest D) and ``runs`` (the open four-character runs); ``contexts`` and ``runs`` are empty when the
    face's triples were not measured (``triples`` False)."""

    pairs: dict[str, tuple[float, float]] = field(default_factory=dict)
    contexts: dict[str, tuple[float, float]] = field(default_factory=dict)
    runs: set[str] = field(default_factory=set)
    triples: bool = True

    @property
    def chain(self) -> str:
        """The face's chain characters (:func:`chain_characters`), as a sorted string."""
        return "".join(sorted(chain_characters(self.contexts)))


WORD_CATEGORIES: Final = frozenset({"Lu", "Ll", "Lt", "Lo", "Nd"})  # letters (not modifier letters) and digits


def chain_characters(contexts: Collection[str]) -> set[str]:
    """The characters a face substitutes by their neighbours that are not letters or digits: the middle characters of
    its context triples outside :data:`WORD_CATEGORIES` (Inter's ``*``, brackets, operators, dashes, ``:``, ``@``:
    its case-sensitive alternates; Lexend's fraction slash). Their alternates chain along a run of them through
    alternates that change no width (Inter draws ``*:<H`` 0.144 em wider than its pairs and triples, and ``**:<H``
    0.288 em: B2b review F1, round 2), which no measurement of a fixed length finds; so a run of four or more
    characters with two of them among any four neighbouring characters is never bounded (``FaceKerning.bounded``)."""
    return {t[1] for t in contexts if unicodedata.category(t[1]) not in WORD_CATEGORIES}


def near_triples(contexts: Collection[str], chain: Collection[str], chars: Collection[str]) -> list[str]:
    """The triples whose four-character runs are measured (sorted): every context triple; every triple that puts a
    context triple's middle character between characters its context triples have on its left and on its right; and,
    for a middle character that is a letter or a digit (:data:`WORD_CATEGORIES`), every triple with one of those on
    either side and any of ``chars`` on the other: the exceptions of a context rule (Inter draws ``0x1`` as written, a
    hexadecimal prefix, so ``0`` is never on the left of a context ``x``, but ``10x1`` with a multiplication sign,
    0.116 em wider than its pairs and triples: B2b review F1, round 2). Not a triple holding two ``chain`` characters
    (a run holding it is never bounded), nor one holding a combining mark or the space."""
    left: dict[str, set[str]] = {}
    right: dict[str, set[str]] = {}
    for a, b, c in contexts:
        left.setdefault(b, set()).add(a)
        right.setdefault(b, set()).add(c)
    out = set(contexts)
    for b, lefts in left.items():
        out.update(a + b + c for a in lefts for c in right[b])
        if unicodedata.category(b) in WORD_CATEGORIES:
            out.update(a + b + c for a in lefts for c in chars)
            out.update(a + b + c for a in chars for c in right[b])
    return sorted(t for t in out
                  if sum(ch in chain for ch in t) < 2 and " " not in t and not any(is_mark(ch) for ch in t))


def _inputs(web_dir: Path | str | None) -> list[tuple[str, str, list[str]]]:
    """(face name, the file as base64, its characters) of every :func:`table_faces` face, read before Chromium
    starts."""
    out = []
    for face in table_faces():
        path = fc.web_path(face, web_dir)
        try:
            b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        except OSError as exc:
            raise KerningError(f"cannot read {path} (npm run vendor): {exc}") from exc
        chars = characters(face, web_dir)
        if face.covers(0x20) != (" " in chars):
            raise KerningError(f"{face_name(face)}: its range and its cmap disagree on the space")
        out.append((face_name(face), b64, chars))
    return out


def _widen(into: dict[str, tuple[float, float]], seq: str, value: float) -> None:
    hi, lo = into.get(seq, (value, value))
    into[seq] = (max(hi, value), min(lo, value))


async def _measure_async(inputs: list[tuple[str, str, list[str]]],
                         triples_of: Collection[str] | None) -> dict[str, Measurement]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - a dev dependency
        raise KerningError("playwright is not installed: the table is measured in Chromium") from exc
    from ..screenshot import CHROMIUM_ARGS
    from .stage_page import DETERMINISM_ARGS

    floor = EPSILON_MILLI - MARGIN_MILLI - 0.5  # a superset of what is kept (kept: beyond EPSILON_MILLI, rounded out)
    out: dict[str, Measurement] = {}
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True, args=[*CHROMIUM_ARGS, *DETERMINISM_ARGS])
        except Exception as exc:  # noqa: BLE001 - any launch failure: no measurement
            raise KerningError(f"Chromium cannot start: {str(exc).splitlines()[0][:200]}") from exc
        try:
            pages = []
            for _ in range(WORKERS):  # a context each: its own renderer process, so they measure in parallel
                page = await (await browser.new_context()).new_page()
                await page.set_content("<!doctype html><html><body></body></html>")
                pages.append(page)

            async def spread(js: str, b64: str, chars: list[str], items: list[str], *args: Any) -> list[Any]:
                chunks = [items[i::len(pages)] for i in range(len(pages))]
                return list(await asyncio.gather(*(
                    page.evaluate(js, [b64, chars, chunk, *args])
                    for page, chunk in zip(pages, chunks, strict=True) if chunk)))

            for name, b64, chars in inputs:
                m = Measurement(triples=triples_of is None or name in triples_of)
                for spacing in LETTER_SPACING:
                    for pair, d in await pages[0].evaluate(_PAIRS_JS, [b64, chars, spacing, floor]):
                        _widen(m.pairs, pair, float(d))
                if m.triples:
                    plain = [ch for ch in chars if not is_mark(ch)]
                    found: set[str] = set()
                    for spacing in LETTER_SPACING:
                        for got in await spread(_TRIPLES_JS, b64, chars, chars, spacing, CONTEXT_TOL_MILLI):
                            found.update(t for t, _ in got if not any(is_mark(ch) for ch in t))
                    ctxs = sorted(found)
                    for spacing in LETTER_SPACING:  # every context's D in every spacing
                        for got in await spread(_D3_JS, b64, chars, ctxs, spacing):
                            for t, d in got:
                                _widen(m.contexts, t, float(d))
                    chain = m.chain
                    trips = near_triples(ctxs, chain, plain)
                    for spacing in LETTER_SPACING:  # the open runs
                        for got in await spread(_RUNS_JS, b64, plain, trips, spacing, OPEN_TOL_MILLI, chain):
                            m.runs.update(q for q, _ in got)
                out[name] = m
        finally:
            await browser.close()
    return out


def measure(web_dir: Path | str | None = None, *, only: Collection[str] | None = None,
            triples_of: Collection[str] | None = None) -> dict[str, Measurement]:
    """Measure every :func:`table_faces` face (or the ones named in ``only``) in Chromium: face file name ->
    :class:`Measurement`. The triples and open runs of every face, or of the ones named in ``triples_of`` (the
    others' pairs only: the slow test measures the triples of the ``latin`` faces, as the ``latin-ext`` ones take
    most of an hour)."""
    inputs = [i for i in _inputs(web_dir) if only is None or i[0] in only]
    return asyncio.run(_measure_async(inputs, triples_of))


def cover(runs: Iterable[str], minimum: int = COVER_MIN) -> tuple[list[str], list[str]]:
    """The open four-character runs as ``(triples, runs)``: every run holds one of the triples or is one of the runs.
    Greedy and deterministic: the triple standing for the most runs not yet covered first (ties: the smaller triple),
    while it stands for at least ``minimum``; the runs left are listed themselves (so a triple that only one run
    makes open, such as Outfit's ``ffi`` in ``ffiw``, does not make every word holding it uncertified)."""
    todo = set(runs)
    holders: dict[str, set[str]] = {}
    for q in todo:
        for t in {q[:3], q[1:]}:
            holders.setdefault(t, set()).add(q)
    heap = [(-len(qs), t) for t, qs in holders.items()]
    heapq.heapify(heap)
    triples: list[str] = []
    while heap:
        count, t = heapq.heappop(heap)
        live = holders[t] & todo
        if len(live) != -count:  # stale: some of its runs were covered since
            if live:
                heapq.heappush(heap, (-len(live), t))
            continue
        if len(live) < minimum:
            break
        triples.append(t)
        todo -= live
    return sorted(triples), sorted(todo)


def _group(values: Mapping[str, int]) -> dict[str, str]:
    groups: dict[int, list[str]] = {}
    for seq, milli in values.items():
        groups.setdefault(milli, []).append(seq)
    return {str(k): "".join(sorted(v)) for k, v in sorted(groups.items())}


def _row(name: str, m: Measurement) -> dict[str, Any]:
    """One face's table row from its measurement (the module docstring): pair bounds beyond +/- EPSILON_MILLI, context
    bounds that are not 0, and the open runs' cover. Fails closed when the space is kerned wider, or taken into a
    context or an open run (``textfit`` measures a word on its own)."""
    positive: dict[str, int] = {}
    negative: dict[str, int] = {}
    for pair, (hi, lo) in m.pairs.items():
        up, down = math.ceil(hi + MARGIN_MILLI), math.floor(lo - MARGIN_MILLI)
        if up > EPSILON_MILLI:
            if " " in pair:
                raise KerningError(f"{name} kerns the space ({pair!r} +{hi:.1f}/1000 em): textfit assumes no face "
                                   "does; extend it")
            positive[pair] = up
        if down < -EPSILON_MILLI and " " not in pair:  # a narrower space only narrows the page's line: not stored
            negative[pair] = down
    ups: dict[str, int] = {}
    downs: dict[str, int] = {}
    for triple, (hi, lo) in m.contexts.items():
        if " " in triple:
            raise KerningError(f"{name} takes the space into a context ({triple!r} {hi:+.1f}/1000 em): textfit "
                               "assumes no face does; extend it")
        up, down = math.ceil(hi + MARGIN_MILLI), math.floor(lo - MARGIN_MILLI)
        if up:
            ups[triple] = up
        if down:
            downs[triple] = down
    spaced = sorted(q for q in m.runs if " " in q)
    if spaced:
        raise KerningError(f"{name} takes the space into an open run ({spaced[0]!r}): textfit assumes no face does; "
                           "extend it")
    open3, open4 = cover(m.runs)
    return {"chain": m.chain, "down": _group(downs), "negative": _group(negative), "open": "".join(open3),
            "open4": "".join(open4), "positive": _group(positive), "up": _group(ups)}


def render(faces: Mapping[str, Mapping[str, Any]]) -> str:
    """The table's text: one face per line, sorted keys, UTF-8, LF (byte-deterministic)."""
    head = {"version": VERSION, "generator": GENERATOR, "unit": "1/1000 em", "epsilon": EPSILON_MILLI}
    lines = ["{"] + [f"  {json.dumps(k)}: {json.dumps(v)}," for k, v in head.items()] + ['  "faces": {']
    names = sorted(faces)
    for i, name in enumerate(names):
        row = json.dumps(faces[name], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        lines.append(f"    {json.dumps(name)}: {row}{',' if i + 1 < len(names) else ''}")
    return "\n".join([*lines, "  }", "}"]) + "\n"


def covers(table: Mapping[str, FaceKerning], measured: Mapping[str, Measurement]) -> list[str]:
    """Where a fresh measurement is not bounded by the table (empty: the table still holds): a pair measured wider or
    narrower than its bounds, a triple adding more or less than the table bounds it by (a triple the table does not
    list: 0 either way, within :data:`CONTEXT_TOL_MILLI`), or an open four-character run the table's cover does not
    hold."""
    problems = []
    for name, m in measured.items():
        k = table.get(name)
        if k is None:
            problems.append(f"{name}: not in the table")
            continue
        for pair, (hi, lo) in m.pairs.items():
            if hi > k.upper(*pair) * 1000:
                problems.append(f"{name}: pair {pair!r} {hi:+.2f} > {k.upper(*pair) * 1000:+.0f}")
            if lo < k.lower(*pair) * 1000 and " " not in pair:
                problems.append(f"{name}: pair {pair!r} {lo:+.2f} < {k.lower(*pair) * 1000:+.0f}")
        for triple, (hi, lo) in m.contexts.items():
            up, down = k.context(*triple)
            if hi > up * 1000 or lo < down * 1000:
                problems.append(f"{name}: triple {triple!r} [{lo:+.2f}, {hi:+.2f}] not within "
                                f"[{down * 1000:+.0f}, {up * 1000:+.0f}]")
        for q in sorted(m.runs):
            if k.bounded(q):
                problems.append(f"{name}: the open run {q!r} is not in the table's cover")
        if m.triples and not set(m.chain) <= k.chain:
            problems.append(f"{name}: chain characters {sorted(set(m.chain) - k.chain)} are not the table's")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the committed table differs")
    parser.add_argument("--path", type=Path, default=TABLE_PATH, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        text = render({name: _row(name, m) for name, m in measure().items()})
    except KerningError as exc:
        print(f"{TABLE_NAME} cannot be measured: {exc}", file=sys.stderr)
        return 2
    try:
        current: str | None = args.path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        current = None
    if args.check:
        if current == text:
            return 0
        print(f"{TABLE_NAME} is out of date: run {GENERATOR}", file=sys.stderr)
        return 1
    if current != text:
        args.path.write_bytes(text.encode("utf-8"))
        print(f"wrote {TABLE_NAME}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
