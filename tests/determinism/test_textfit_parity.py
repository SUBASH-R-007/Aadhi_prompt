"""HarfBuzz parity probe: the widths ``textfit`` shapes are the widths the real render page draws (real Chromium, the
vendored faces; DYNAMIC_VIDEO_DESIGN §4.6; owner decision 2026-10-14/15: shape every script, provided this probe
confirms the shaped widths match the page, and only then retire the B2b kerning table).

Every vendored face and weight the stage draws is measured, with the letter-spacing its slots use, at the smallest and
the largest size of that style (board_generic's slots, ``templates.py``; the display face also at the body weights,
where the stage draws a quote's text and a stat's unit with it, and Anek at the wght 800 a 900 bold run in an Indic
title is clamped to): the page draws each text as an inline-block (as a title word, ``.dyn-word``) in the stage's own
stack, ``lang`` and CSS (``font-kerning``, ``font-variant-ligatures``, ``text-rendering``, and ``white-space`` as the
stage has it: ``normal`` in a title, ``nowrap`` in a header, measured here with ``nowrap``; Chromium shapes text under
``white-space: pre`` otherwise, breaking the kerning and ligatures across a ZWNJ, which the stage never draws), and
Python measures it with :func:`textfit.measure`.

* **Latin** (every display and body family at the weights the stage draws, JetBrains Mono): the B2b review's runs
  (the ones its kerning table found wider or narrower than their pairs: Inter's chained ``*``, ``10x10``, ``2x3x4x5``,
  arrows, fractions, Outfit's ``ffiw``), test_textfit_page's word corpus, combining sequences, words of two faces of one
  family, the B2b-H review's script runs (digits and U+202F NARROW NO-BREAK SPACE, a Mongolian run on the page: no
  letter-spacing; characters whose Script_Extensions split a run or resolve it; paired brackets; ZWNJ and ZWJ between
  Latin letters; a left-to-right mark), and random runs of each face's characters (``latin`` and ``latin-ext``).
* **Tabular numerals** (board_generic's countdown ring, ``font-variant-numeric: tabular-nums``): digit strings in the
  body faces at the ring's weight and sizes, shaped with ``tnum``.
* **Indic** (Noto Sans and Anek, the five scripts, Marathi as well as Hindi for Devanagari): every consonant x vowel
  sign and every consonant + virama + consonant (9,487 syllables, every one drawn by its vendored face), and
  test_textfit_page's Tamil, Hindi and mixed Tamil + English strings.
* **Lines of flow text** (the kicker, a subtitle: one text node, shaped across its spaces): test_textfit_page's strings,
  English, Tamil, Hindi and mixed, and lines whose scripts resolve across their words (``about 1 000 km`` with U+202F:
  Latin there, Mongolian alone), drawn on one line, are never wider than Python's conservative line, nor than its
  reading of the words inside the whole text (``Word.readings``: the page only kerns a space narrower).
* **Words joined by only zero-width spaces** (flow text, one text node; the B2b-H review, round 3): the page shapes
  them as one (HarfBuzz skips U+200B), and draws them as HarfBuzz shapes the text as one, never wider than Python's
  conservative line when certified (``Word.join_em``; a chain a lookup reaches across is not certified).

Asserted per word Python certifies (``Word.reliable``): the page's width is within ``textfit``'s conservative and
optimistic widths, and within :data:`TOLERANCE_PX` plus :data:`TOLERANCE_REL` of the shaped width (parity, not just a
bound: a drift of Chromium's or HarfBuzz's shaping fails here, on the platform the test runs on). The residuals
(page - shaped, px) are printed per style and size (``pytest -s``): max and median of their absolute values.

What runs in the slow suite (CI's slow job, the container): every style at its smallest and largest slot size, 1,500
random runs per Latin style and every Indic syllable (on the development machine: Latin 35 s, Indic 50 s, the file
under 2 minutes). The extended probe (6,000 random runs per Latin style, every size of each slot's range in 4 px
steps: 3.87 million word widths, 5.5 to 7.5 minutes on the development machine: 328 s and 456 s measured) runs only
on demand: ``AADHI_TEXTFIT_PARITY_FULL=1`` (owner's rule: no many-minute measurement in CI; docs/OPERATIONS.md).
"""

from __future__ import annotations

import os
import random
import statistics
import unicodedata
from collections.abc import Sequence
from typing import Any

import pytest

from aadhi.compose.dynamic import faces as fc
from aadhi.compose.dynamic import shaping as sh
from aadhi.compose.dynamic import textfit as tf
from aadhi.compose.dynamic.stage_page import open_stage

from . import test_textfit_page as tp

pytestmark = pytest.mark.slow
TOKEN = "textfit.parity"
FULL_ENV = "AADHI_TEXTFIT_PARITY_FULL"
FULL = os.environ.get(FULL_ENV) == "1"
RANDOM_RUNS = 6000 if FULL else 1500  # random runs per Latin style
SEED = 20261015
# parity: |page - shaped| <= TOLERANCE_PX + TOLERANCE_REL * shaped. Measured on the development machine (Windows,
# Playwright's Chromium; the extended probe): at most 0.0531 px, a layout unit of 1/64 px and float rounding of at most
# 0.053% of the width beyond it; the tolerance is above that (a drift of the shaping, not of the rounding, fails), and
# a tenth of textfit's own relative margin (SLACK, 0.5%)
TOLERANCE_PX = 1 / 16
TOLERANCE_REL = 5e-4

# (role, family, weight, letter-spacing em, slot sizes px): every style a board_generic slot draws (templates.py: the
# title display 700 at -0.015 em, 50-148 px; the kicker body 600 at +0.02 em, 40-44 px; body text 400 (16:9) and 600
# (9:16), subtitles, chips, the ring at 700) and display 900 (a bold run in a title); JetBrains Mono for code
_DISPLAY = ("outfit", "poppins", "lexend", "inter", "atkinson_hyperlegible_next")
_BODY = ("inter", "atkinson_hyperlegible_next")
LATIN_STYLES: list[tuple[str, str, int, float, tuple[float, float]]] = [
    *[("display", f, 700, -0.015, (50.0, 148.0)) for f in _DISPLAY],
    *[("display", f, 900, -0.015, (50.0, 148.0)) for f in _DISPLAY],
    *[("body", f, w, t, s) for f in _BODY for w, t, s in ((400, 0.0, (39.0, 52.0)), (600, 0.0, (40.0, 56.0)),
                                                          (700, 0.0, (40.0, 44.0)), (600, 0.02, (40.0, 44.0)))],
    *[("mono", "jetbrains_mono", w, 0.0, (40.0, 56.0)) for w in (400, 700)],
    # the display face at the body weights (Inter and Atkinson are measured there as body faces): a quote's text and a
    # stat's unit (dynamic.css .dyn-i-quote .dyn-i-text, 1.08 em of the body size; .dyn-stat-unit, 600)
    *[("display", f, w, 0.0, (42.0, 62.0)) for f in ("outfit", "poppins", "lexend") for w in (400, 600)],
]
INDIC_STYLES: list[tuple[str, int, float, tuple[float, float]]] = [
    ("display", 700, -0.015, (50.0, 148.0)), ("body", 400, 0.0, (39.0, 52.0)), ("body", 600, 0.0, (40.0, 56.0)),
    ("body", 700, 0.0, (40.0, 44.0)), ("body", 600, 0.02, (40.0, 44.0)),
    ("display", 900, -0.015, (50.0, 148.0))]  # a bold run in a title: Anek's wght clamped to 800
LANGUAGES = {"tamil": ("ta-IN",), "devanagari": ("hi-IN", "mr-IN"), "telugu": ("te-IN",), "kannada": ("kn-IN",),
             "malayalam": ("ml-IN",)}
BLOCKS = {"tamil": 0x0B80, "devanagari": 0x0900, "telugu": 0x0C00, "kannada": 0x0C80, "malayalam": 0x0D00}

# The B2b review's runs (Inter 400 and 600 drew them wider than its kerning table's pairs and triples of the first fix
# round added up to; tests/determinism/test_kerning_table.py, retired with the table) and words that stayed bounded.
REVIEW_RUNS = ("A*****", "*****A", "A********", "!********", "2********3", "4|~***", "**:<H0?", "8]*-*}*]",
               "*:<H", "10x10", "20x9", "2x3x4x5", "**A**", "A**B**C", "*(A)*", "<==>", "<=>", "==>", "<->", "12⁄34",
               "1⁄2", "Supercalifragilisticexpialidocious", "AVAVAVAVAVAVAVAVAVAV", "efficient", "office", "ffiw",
               "fffi", "X-ray", "COVID-19", "12:30", "(DNA)", "example", "Maxwell", "step-by-step", "f(x)", "[1-3]",
               "(a+b)", "ffjj", "tttt")
# combining sequences (composed by HarfBuzz where the face has the precomposed letter, as on the page), and words of
# two faces of one family (latin + latin-ext: Atkinson kerns Ŷ against the A its latin-ext face has no glyph for)
COMBINING = ("cafe\u0301", "nai\u0308ve", "x\u0304", "n\u0303o", "c\u0327a", "o\u0302\u0301", "e\u0304", "Ra\u0304ma")
TWO_FACES = ("ŶA", "AŶ", "Rāma", "Erdős", "Łódź", "Dvořák", "ĀĀA", "aħ", "ħħ", "Ŵẃ", "Tœ", "ŁA")
# The B2b-H review (round 1): words whose script runs Script_Extensions, paired brackets or Inherited marks decide
# (U+202F with digits only: a Mongolian run, drawn with no letter-spacing), ZWNJ / ZWJ / other default-ignorables
# between Latin letters, a left-to-right mark; those with Indic letters are not measured in the mono stack (it holds no
# Indic family). ``₹`` (only the Indic faces have it) and U+02D7 (Inter's) are not drawn by every stack: uncertified
# there, as every character no face of the stack draws.
SCRIPT_RUNS = ("1\u202f000", "5\u202f%", "100\u202f%", "(1\u202f000)", "₹\u202f100", "1\u202f000\u202f000",
               "10\u202fkm", "1\u202f000km", "\u202f1", "1\u202f", "-\u202f-", "1\u202f000\u00a0000", "x\u202fx",
               "1\u0308", "1\u0304", "1ʼ", "x˗", "1˗", "1·", "·", "a(b)c", "(1)", "[A]V",
               "A\u200eV", "a\u2060b", "a\u00adb", "A\u200cV", "f\u200cfi", "A\u200dV", "\u200dA", "A\u200d",
               "a\u034fb", "AV\u200cAV")
SCRIPT_RUNS_INDIC = ("a।", "1।", "क।", "(க)a", "a(க)", "क\u202f१",
                     "१\u202f०००", "1\u1cf5")  # U+1CF5: Common, a preferred script (Bengali)
NOT_EVERY_STACK = frozenset({"₹\u202f100", "x˗", "1˗"})
# The B2b-H review (round 2): words of one grapheme cluster HarfBuzz shapes as several, one of them drawn by a system
# font (a Latin letter and a Devanagari vowel sign in two script runs, the sign's a broken Indic cluster whose dotted
# circle no face's range holds; a ZWNJ before a mark; a ZWJ or a virama before a character no vendored face draws):
# drawn up to 43 px wider than B2b-H's certified width, or 99 px narrower than its optimistic one; never certified,
# in any stack (test_textfit's GRAPHEME_PARTS)
GRAPHEME_PARTS = ("T\u093f\u0301", "a\u093f\u0301", "A\u200c\u0301", "e\u200c\u0300", "x\u200d\u4e2d",
                  "\u0915\u094d\u4e2d", "T\u093f\u0301x", "T\u093f\u093f\u0301\u0967")
LINES = (tp.TYPICAL, tp.WIDE, tp.PLAIN, tp.LONG_WORD, tp.COMPOUND, *tp.KICKERS, tp.SUBTITLE,
         # scripts resolved across words: 1 000 is Latin after "about", 23 Mongolian after 1 000 (U+202F)
         "about 1\u202f000 km", "1\u202f000 23", "Chapter 1\u202f000: the 5\u202f% rule", "(1\u202f000) 300 000",
         "1\u202f000\u2002000 7", "100\u202f%\u2003x", "zero\u200bwidth space")
INDIC_LINES = {"ta-IN": (tp.TAMIL, tp.TAMIL_LONG, tp.MIXED), "hi-IN": (tp.HINDI, tp.HINDI_LONG), "mr-IN": (tp.HINDI,)}

# each text an inline-block in a host with the stage's white-space (nowrap: as normal for shaping, and a word wider
# than the stage is not wrapped); ``numeric``: font-variant-numeric (the ring's tabular-nums)
_WIDTHS_JS = """async ([css, weight, size, spacing, lang, texts, numeric]) => {
  const host = document.createElement('div');
  Object.assign(host.style, { position: 'absolute', left: '0', top: '0', fontFamily: css, fontWeight: String(weight),
    fontSize: `${size}px`, letterSpacing: `${spacing}em`, visibility: 'hidden', whiteSpace: 'nowrap',
    fontVariantNumeric: numeric || 'normal' });
  host.lang = lang;
  document.querySelector('.dyn-stage').appendChild(host);
  await Promise.all(texts.map((t) => document.fonts.load(`${weight} ${size}px ${css}`, t)));
  const spans = texts.map((t) => { const s = document.createElement('span'); s.style.display = 'inline-block';
    s.textContent = t; host.appendChild(s); return s; });
  const out = spans.map((s) => s.getBoundingClientRect().width);
  host.remove();
  return out;
}"""


def _sizes(slot_sizes: tuple[float, float]) -> tuple[float, ...]:
    """The sizes measured: the style's smallest and largest slot size (every size of the range in the extended probe)."""
    return tf.sizes(*slot_sizes) if FULL else slot_sizes


def _fonts(role: str, family: str, indic: str = "noto_sans") -> dict[str, str]:
    return {"display": family if role == "display" else "outfit", "body": family if role == "body" else "inter",
            "mono": "jetbrains_mono", "indic": indic}


def _face_characters(family: str, weight: int) -> list[str]:
    """Every character the family's faces at ``weight`` declare and map (letters, digits, punctuation, symbols)."""
    out: set[str] = set()
    rows = fc.faces_of(family)
    matched = fc.match_weight(tuple(r.weight for r in rows), weight)
    for r in rows:
        if r.weight != matched:
            continue
        key = tf.face_key(r, weight)
        for lo, hi in r.ranges:
            for cp in range(max(lo, 0x21), hi + 1):
                ch = chr(cp)
                if unicodedata.category(ch)[0] not in "MZC" and not tf.ignorable(ch) and tf.has_glyph(key, ch):
                    out.add(ch)
    return sorted(out)


def _latin_corpus(role: str, family: str, weight: int, rng: random.Random) -> list[str]:
    chars = _face_characters(family, weight)
    runs: set[str] = set()
    while len(runs) < RANDOM_RUNS:
        runs.add("".join(rng.choice(chars) for _ in range(rng.randint(2, 12))))
    indic = SCRIPT_RUNS_INDIC if role != "mono" else ()
    return list(dict.fromkeys([*REVIEW_RUNS, *tp.CORPUS, *COMBINING, *TWO_FACES, *SCRIPT_RUNS, *indic, *GRAPHEME_PARTS,
                               *sorted(runs)]))


def _syllables(script: str) -> list[str]:
    """Every consonant x dependent vowel sign and consonant + virama + consonant of the script (its ISCII-ordered block:
    consonants at +0x15..+0x3A, Devanagari's nukta letters at +0x58..+0x5F)."""
    base = BLOCKS[script]
    consonants, signs, virama = [], [], ""
    for cp in range(base, base + 0x80):
        ch = chr(cp)
        name = unicodedata.name(ch, "")
        if "VOWEL SIGN" in name:
            signs.append(ch)
        elif name.endswith("SIGN VIRAMA"):
            virama = ch
        elif ((base + 0x15 <= cp <= base + 0x3A or (script == "devanagari" and base + 0x58 <= cp <= base + 0x5F))
              and " LETTER " in name and unicodedata.category(ch) == "Lo"):
            consonants.append(ch)
    assert consonants and signs and virama, script
    return [c + s for c in consonants for s in signs] + [a + virama + b for a in consonants for b in consonants]


async def _widths(real_app, jobs: Sequence[tuple[Any, ...]]) -> list[list[float]]:
    """The page's widths of each job ``(css, weight, size, letter-spacing, lang, texts[, font-variant-numeric])``."""
    served = tp._timeline("landscape", {"display": "outfit", "body": "inter", "mono": "jetbrains_mono"}, "en-IN",
                          [("default", tp.TYPICAL, "Basics", "")])
    out = []
    async with open_stage(real_app.settings, size=(1920, 1080), token=TOKEN, base_url=real_app.base,
                          served_timeline=served) as page:
        await page.seek(0)
        for css, weight, size, spacing, lang, texts, *numeric in jobs:
            got: list[float] = []
            for i in range(0, len(texts), 1500):
                got += await page.page.evaluate(_WIDTHS_JS, [css, weight, size, spacing, lang, texts[i:i + 1500],
                                                             numeric[0] if numeric else ""])
            out.append(got)
        assert page.errors == [] and page.guard_trips == [], (page.errors, page.guard_trips)
    return out


class Residuals:
    """Per style and size: the page's widths against the shaped ones (and the bounds textfit certifies)."""

    def __init__(self) -> None:
        self.rows: dict[str, list[float]] = {}
        self.relative: list[float] = []  # (|page - shaped| - a layout unit) / shaped: what the relative slack covers
        self.problems: list[Any] = []
        self.uncertified: dict[str, list[str]] = {}

    def word(self, label: str, m: tf.Measured, word: tf.Word, size: float, page: float) -> None:
        if not word.reliable:
            self.uncertified.setdefault(label, []).append(word.text)
            return
        raw = m.width(word, size, "raw")
        self.rows.setdefault(label, []).append(page - raw)
        self.relative.append(max(0.0, abs(page - raw) - tf.BOX_PX) / raw if raw > 0 else 0.0)
        if not m.width(word, size, "optimistic") <= page <= m.width(word, size):
            self.problems.append(("bounds", label, word.text, page, m.width(word, size, "optimistic"),
                                  m.width(word, size)))
        if abs(page - raw) > TOLERANCE_PX + TOLERANCE_REL * raw:
            self.problems.append(("parity", label, word.text, round(page, 4), round(raw, 4)))

    def report(self, title: str) -> str:
        lines = [f"{title}: residual page - shaped (px), |max| / median |.|, n"]
        for label, rs in self.rows.items():
            a = [abs(r) for r in rs]
            lines.append(f"  {label:58s} max {max(a):.4f}  median {statistics.median(a):.4f}  "
                         f"[{min(rs):+.4f}, {max(rs):+.4f}]  n={len(rs)}")
        every = [abs(r) for rs in self.rows.values() for r in rs]
        lines.append(f"  all: max {max(every):.4f} px, median {statistics.median(every):.4f} px, n={len(every)}; "
                     f"beyond a layout unit at most {max(self.relative):.2e} of the shaped width")
        return "\n".join(lines)


async def test_latin_shaped_widths_are_the_pages(real_app) -> None:
    rng = random.Random(SEED)
    jobs, meta = [], []
    for role, family, weight, tracking, slot_sizes in LATIN_STYLES:
        fonts = _fonts(role, family)
        corpus = _latin_corpus(role, family, weight, rng)
        css = fc.css_stack(fonts, role, "en-IN")  # type: ignore[arg-type]
        m = tf.measure(" ".join(corpus), fonts=fonts, role=role, weight=weight, language="en-IN",
                       tracking_em=tracking)
        assert [w.text for w in m.words] == corpus
        for size in _sizes(slot_sizes):
            jobs.append((css, weight, size, tracking, "en-IN", corpus))
            meta.append((f"{family} {weight} {tracking:+} @{size:g}px", m, size))
    res = Residuals()
    for (label, m, size), page in zip(meta, await _widths(real_app, jobs), strict=True):
        for word, w in zip(m.words, page, strict=True):
            res.word(label, m, word, size, w)
    print("\n" + res.report("Latin"))
    assert res.problems == [], res.problems[:20]
    # what is not certified is only what no vendored face of the stack draws (Ω and √ are in no vendored subset, nor
    # U+0302 / U+0301 after an ô: the system font differs by machine; NOT_EVERY_STACK, Indic in the mono stack), and
    # the GRAPHEME_PARTS (each character has a glyph in a face of the stack, but a part of the cluster is drawn by a
    # system font)
    for (label, m, _), _page in zip(meta, jobs, strict=True):
        faces = tf.fallback_faces(m.stack, m.weight)
        for text in res.uncertified.get(label, []):
            drawn_nowhere = any(not any(tf.has_glyph(k, ch) for k in faces) for ch in text)
            assert text in GRAPHEME_PARTS or drawn_nowhere, (label, text)
        assert set(GRAPHEME_PARTS) <= set(res.uncertified.get(label, [])), label
    allowed = {"Ω", "√2", "o\u0302\u0301", *NOT_EVERY_STACK, *SCRIPT_RUNS_INDIC, *GRAPHEME_PARTS}
    assert {t for ts in res.uncertified.values() for t in ts} <= allowed, res.uncertified
    # this round's script runs are certified, and so compared, in every stack that draws them
    for label, _m, _ in meta:
        assert not set(res.uncertified.get(label, [])) & (set(SCRIPT_RUNS) - NOT_EVERY_STACK), label
        assert "mono" in label or not set(res.uncertified.get(label, [])) & set(SCRIPT_RUNS_INDIC), label
    assert len(res.rows) == sum(len(_sizes(s[4])) for s in LATIN_STYLES)


async def test_indic_shaped_widths_are_the_pages(real_app) -> None:
    jobs, meta, counts = [], [], []
    for script, languages in LANGUAGES.items():
        syllables = _syllables(script)
        for language in languages:
            extra = [w for line in INDIC_LINES.get(language, ()) for w, _ in tf.words(line)]
            for group in ("noto_sans", "anek"):
                for role, weight, tracking, slot_sizes in INDIC_STYLES:
                    fonts = _fonts(role, "outfit" if role == "display" else "inter", group)
                    corpus = list(dict.fromkeys([*syllables, *extra]))
                    css = fc.css_stack(fonts, role, language)  # type: ignore[arg-type]
                    m = tf.measure(" ".join(corpus), fonts=fonts, role=role, weight=weight, language=language,
                                   tracking_em=tracking)
                    # every syllable is drawn by a vendored face (none uncovered), so every one is certified
                    assert [w.text for w in m.words] == corpus and m.reliable, (script, group, m.uncovered)
                    for size in _sizes(slot_sizes):
                        jobs.append((css, weight, size, tracking, language, corpus))
                        meta.append((f"{group} {script} {language} {weight} {tracking:+} @{size:g}px", m, size))
                        counts.append(len(corpus))
    res = Residuals()
    for (label, m, size), page in zip(meta, await _widths(real_app, jobs), strict=True):
        for word, w in zip(m.words, page, strict=True):
            res.word(label, m, word, size, w)
    print("\n" + res.report("Indic"))
    assert res.problems == [], res.problems[:20]
    assert res.uncertified == {}
    assert [len(rs) for rs in res.rows.values()] == counts  # every word of every corpus was compared


async def test_lines_of_flow_text_are_never_wider_than_pythons(real_app) -> None:
    """A kicker or subtitle is one text node: the page itemizes its scripts over the whole text and shapes it across its
    spaces (a face may kern a space against its neighbours: measured, only narrower), so Python's line (its words and
    spaces apart, conservative) is never narrower than the page's, and neither is its reading of the words inside the
    whole text (``Word.readings``: the scripts the page resolves there; within the parity tolerance)."""
    jobs, meta = [], []
    cases = [("en-IN", line) for line in LINES] + [(lang, line) for lang, ls in INDIC_LINES.items() for line in ls]
    for role, family, weight, tracking, slot_sizes in LATIN_STYLES:
        fonts = _fonts(role, family, "anek" if family in ("poppins", "atkinson_hyperlegible_next") else "noto_sans")
        for language, line in cases:
            if role == "mono" and language != "en-IN":
                continue  # the mono stack holds no Indic family
            m = tf.measure(line, fonts=fonts, role=role, weight=weight, language=language, tracking_em=tracking)
            css = fc.css_stack(fonts, role, language)  # type: ignore[arg-type]
            size = slot_sizes[1]
            jobs.append((css, weight, size, tracking, language, [line]))
            meta.append((f"{family} {weight} {tracking:+} {language}", m, size, line))
    narrower: list[float] = []
    for (label, m, size, line), (page,) in zip(meta, await _widths(real_app, jobs), strict=True):
        python = sum(m.width(w, size) + m.sep(w, size) for w in m.words)
        # certified, but for an en or em space between words (drawn as measured: its width is compared here; not
        # certified because the page does not hang it at a line's end: textfit.plain_separator)
        assert m.reliable or (m.uncovered == () and not all(w.sep_plain for w in m.words)), (label, line, m.uncovered)
        assert page <= python, (label, line, page, python)
        t = m.tracking_em
        flow = sum(size * (w.readings()[-1][0] + t * w.readings()[-1][1]) for w in m.words)
        flow += sum(size * (w.sep_em + t * (w.sep_units if w.sep_flow_units is None else w.sep_flow_units)
                            + w.join_em + t * w.join_spacings) for w in m.words)
        assert page <= flow + TOLERANCE_PX + TOLERANCE_REL * flow, (label, line, page, flow)
        narrower.append(python - page)
    print(f"\nflow lines: Python's conservative line - the page's (px): min {min(narrower):.3f}, "
          f"max {max(narrower):.3f}, median {statistics.median(narrower):.3f}, n={len(narrower)}")


# Whitespace between words (the B2b-H review, round 2). Widths: ``H`` + whitespace + ``H`` as one text node, in every
# Latin style at its largest slot size, is as wide as Python's words and whitespace, raw (no face kerns ``H`` against a
# space or across a zero-width space), whether certified or not: a maximal run of collapsible whitespace is one space,
# a space on each side of a zero-width or other space is two (B2b-H counted one: 17-28 px narrow at 100 px), a line
# feed next to a zero-width space draws nothing. Line breaks: in a box 1 px or 20 px wider than ``xxxx``, before a word
# that fits a line and one that does not, in flow text and between a title's inline-blocks, the whitespace
# ``textfit.plain_separator`` accepts gives the two lines the line model gives, and every other whitespace gives the
# page another line, or overflows the box sideways, in at least one of them (so it is never certified).
SEPARATOR_WIDTHS = (" ", "  ", " \u200b ", "\u200b", " \u200b", "\u200b ", " \u2009 ", "\u2009", " \u2002 ", "\u2002 ",
                    "\u2003 \u2003", " \u2003\u2003 ", "\u200b \u200b", " \u200b\n ", "\n\u200b", " \n\u200b ",
                    "\u200b\n", "\u200b \n", "\n \u200b", "\u200b\r\n", "\u200b\t\n", "\u200b\r", "\r", " \r\n ",
                    "\n\n", " \u2002\n ", "\t\u200b\t", " \ufeff ", "\ufeff\n", " \u205f ", "\u2002\r\u2002",
                    "\t\n\u200b\n ")
PLAIN_SEPARATORS = (" ", "  ", "\t", "\n", "\u200b", "\u200b ", "\u200b\u200b", "\u200b\u200b ", "\n\u200b",
                    "\u200b\n", "\u200b \n", "\u200b\t", "\t\n\u200b\n ")
OTHER_SEPARATORS = (" \u200b", " \u200b ", " \u2002 ", "\u2002", " \u2002", "\u2002 ", "\u2009", "\ufeff", " \ufeff ",
                    " \u3000 ")
# (whitespace after the last word or before the first, whether the line model's reading of it holds)
TRAILING = ((" ", True), ("\u200b", True), ("\u200b ", True), (" \u200b", False), ("\u2002", False))
LEADING = ((" ", True), ("\u200b", True), (" \u200b", True), ("\u200b\n", True), ("\u2002", False), ("\u200b ", False))

# lines and overflow of words in a box (white-space: normal, the stage's CSS): ``blocks`` draws each word as an
# inline-block with the whitespace as text nodes between them (board_generic wordSpans), else one text node
_BOX_JS = """async ([css, weight, size, spacing, width, cases]) => {
  const out = [];
  for (const [lead, parts, blocks] of cases) {
    const host = document.createElement('div');
    Object.assign(host.style, { position: 'absolute', left: '0', top: '0', fontFamily: css,
      fontWeight: String(weight), fontSize: `${size}px`, letterSpacing: `${spacing}em`, whiteSpace: 'normal',
      width: `${width}px`, lineHeight: '1', visibility: 'hidden' });
    document.querySelector('.dyn-stage').appendChild(host);
    await document.fonts.load(`${weight} ${size}px ${css}`, parts.flat().join(''));
    if (!blocks) host.appendChild(document.createTextNode(lead + parts.flat().join('')));
    else for (const [w, ws] of parts) { const e = document.createElement('span'); e.style.display = 'inline-block';
      e.textContent = w; host.appendChild(e); if (ws) host.appendChild(document.createTextNode(ws)); }
    out.push([Math.round(host.getBoundingClientRect().height / size), host.scrollWidth > host.clientWidth]);
    host.remove();
  }
  return out;
}"""


async def test_whitespace_is_as_wide_and_breaks_as_the_page_lays_it_out(real_app) -> None:
    jobs, meta = [], []
    for role, family, weight, tracking, slot_sizes in LATIN_STYLES:
        fonts = _fonts(role, family)
        texts = [f"H{sep}H" for sep in SEPARATOR_WIDTHS]
        css = fc.css_stack(fonts, role, "en-IN")  # type: ignore[arg-type]
        jobs.append((css, weight, slot_sizes[1], tracking, "en-IN", texts))
        meta.append((f"{family} {weight} {tracking:+}", fonts, role, weight, tracking, slot_sizes[1], texts))
    problems = []
    for (label, fonts, role, weight, tracking, size, texts), page in zip(meta, await _widths(real_app, jobs),
                                                                          strict=True):
        for text, w in zip(texts, page, strict=True):
            m = tf.measure(text, fonts=fonts, role=role, weight=weight, language="en-IN", tracking_em=tracking)
            assert [x.text for x in m.words] == ["H", "H"] and m.uncovered == (), (label, text)
            raw = sum(m.width(x, size, "raw") + m.sep(x, size, "raw") for x in m.words)
            if abs(w - raw) > TOLERANCE_PX + TOLERANCE_REL * raw:
                problems.append(("width", label, text, round(w, 3), round(raw, 3)))
            if m.reliable and w > sum(m.width(x, size) + m.sep(x, size) for x in m.words):
                problems.append(("conservative", label, text, w))
    assert problems == [], problems[:20]

    served = tp._timeline("landscape", {"display": "outfit", "body": "inter", "mono": "jetbrains_mono"}, "en-IN",
                          [("default", tp.TYPICAL, "Basics", "")])
    seen: dict[tuple[str, str, str, bool], list[bool]] = {}  # (style, where, whitespace, plain): laid out as modelled
    async with open_stage(real_app.settings, size=(1920, 1080), token=TOKEN, base_url=real_app.base,
                          served_timeline=served) as page:
        await page.seek(0)
        for role, family, weight, tracking in (("body", "inter", 400, 0.0), ("display", "outfit", 700, -0.015),
                                               ("body", "atkinson_hyperlegible_next", 600, 0.02)):
            fonts = _fonts(role, family)
            css = fc.css_stack(fonts, role, "en-IN")  # type: ignore[arg-type]
            m = tf.measure("xxxx yy yyyyyy", fonts=fonts, role=role, weight=weight, tracking_em=tracking)
            assert m.width(m.words[1], 100, "raw") + 25 < m.width(m.words[0], 100, "raw")  # yy fits the box
            assert m.width(m.words[2], 100, "raw") > m.width(m.words[0], 100, "raw") + 25  # yyyyyy does not
            for extra in (1.0, 20.0):
                width = m.width(m.words[0], 100, "raw") + extra
                cases: list[tuple[Any, ...]] = []
                for sep in (*PLAIN_SEPARATORS, *OTHER_SEPARATORS):
                    cases += [("", [["xxxx", sep], [nxt, ""]], blocks) for blocks in (False, True)
                              for nxt in ("yy", "yyyyyy")]
                cases += [("", [["xxxx", trail]], blocks) for trail, _ in TRAILING for blocks in (False, True)]
                cases += [(lead, [["xxxx", ""]], False) for lead, _ in LEADING]  # a title drops it (splitWords)
                got = await page.page.evaluate(_BOX_JS, [css, weight, 100, tracking, width, cases])
                k = 0
                for sep in (*PLAIN_SEPARATORS, *OTHER_SEPARATORS):
                    # (lines, overflow) for (flow yy, flow yyyyyy, title yy, title yyyyyy): the model's is two lines,
                    # overflowing only beside the over-wide word
                    as_model = got[k:k + 4] == [[2, False], [2, True], [2, False], [2, True]]
                    seen.setdefault((family, "between", sep, sep in PLAIN_SEPARATORS), []).append(as_model)
                    k += 4
                for (trail, plain), outcome in zip([t for t in TRAILING for _ in (0, 1)], got[k:k + 2 * len(TRAILING)],
                                                   strict=True):
                    seen.setdefault((family, "after", trail, plain), []).append(outcome == [1, False])
                k += 2 * len(TRAILING)
                for (lead, plain), outcome in zip(LEADING, got[k:], strict=True):
                    seen.setdefault((family, "before", lead, plain), []).append(outcome == [1, False])
        assert page.errors == [] and page.guard_trips == [], (page.errors, page.guard_trips)
    # the whitespace plain_separator accepts is laid out as the model has it in every case; any other whitespace is
    # not, in at least one case of each style
    wrong = []
    for (family, where, sep, plain), as_model in seen.items():
        assert tf.plain_separator(sep, leading=where == "before") is plain, (where, repr(sep))
        if all(as_model) is not plain:
            wrong.append((family, where, repr(sep), plain, as_model))
    assert wrong == [], wrong


# Words joined by only zero-width spaces (the B2b-H review, round 3): the page's text node holds them with nothing
# between them but U+200B, which HarfBuzz skips when it matches the font's lookups, so it kerns, ligates and takes
# contextual forms across it (B2b-H measured the words apart and certified ``q`` U+200B ``j`` 12.6 px narrower than
# Atkinson 400 draws it at 100 px). The review's pairs, chains Inter's case forms reach across, and random pairs and
# chains of ASCII words (a line feed next to the zero-width space is removed), in every Latin style at its largest
# slot size: the page draws each as HarfBuzz shapes the text as one (within the parity tolerance), and never wider than
# Python's conservative line when it is certified
JOINED = ("q\u200bj", "q\u200b\nj", "q\u200b \nj", "(\u200bj", "f\u200bT", "r\u200bt", "x\u200bt", "f\u200b'",
          "\u1e7e\u200b\u012b", "Y\u200bo", "A\u200bV", "A\u200b-\u200b*", "V\u200b-\u200b*", "*\u200b*\u200bA",
          "A\u200bb\u200bc", "f\u200bf\u200bi", "q\u200b\u200bj")
JOINED_RANDOM = 300  # random pairs and chains per style


def _joined_corpus(rng: random.Random) -> list[str]:
    chars = [chr(c) for c in range(0x21, 0x7F)]
    out = list(JOINED)
    while len(out) < len(JOINED) + JOINED_RANDOM:
        words = ["".join(rng.choice(chars) for _ in range(rng.randint(1, 3))) for _ in range(rng.choice((2, 2, 3)))]
        seps = [rng.choice(("\u200b", "\u200b", "\u200b\n", "\n\u200b", "\u200b\u200b")) for _ in words[1:]]
        out.append(words[0] + "".join(sep + w for sep, w in zip(seps, words[1:], strict=True)))
    return list(dict.fromkeys(out))


async def test_words_joined_by_zero_width_spaces_are_drawn_shaped_as_one(real_app) -> None:
    rng = random.Random(SEED)
    jobs, meta = [], []
    for role, family, weight, tracking, slot_sizes in LATIN_STYLES:
        fonts = _fonts(role, family)
        texts = _joined_corpus(rng)
        css = fc.css_stack(fonts, role, "en-IN")  # type: ignore[arg-type]
        jobs.append((css, weight, slot_sizes[1], tracking, "en-IN", texts))
        meta.append((f"{family} {weight} {tracking:+}", fonts, role, weight, tracking, slot_sizes[1], texts))
    problems, wider_than_apart, certified, refused = [], 0, 0, 0
    for (label, fonts, role, weight, tracking, size, texts), page in zip(meta, await _widths(real_app, jobs),
                                                                          strict=True):
        for text, w in zip(texts, page, strict=True):
            m = tf.measure(text, fonts=fonts, role=role, weight=weight, language="en-IN", tracking_em=tracking)
            assert all(tf.joins(text[a.at + len(a.text):b.at]) for a, b in zip(m.words, m.words[1:])), (label, text)
            if m.uncovered:  # drawn by a system font (Atkinson has no glyph for U+1E7E): neither compared nor certified
                assert not m.reliable and set(m.uncovered) <= set(JOINED[8]), (label, text)
                continue
            collapsed = text.replace("\n", "").replace(" ", "")
            segments = tf.shape_text(collapsed, m.stack, weight, m.language, tracking != 0, None,
                                     sh.script_runs(collapsed))
            shaped = size * (tf._em(segments) + tracking * sum(1 for seg in segments if seg.spaced))
            if abs(w - shaped) > TOLERANCE_PX + TOLERANCE_REL * shaped:
                problems.append(("parity", label, text, round(w, 3), round(shaped, 3)))
            apart = sum(m.width(x, size) for x in m.words)
            wider_than_apart += w > apart
            if not m.reliable:
                refused += 1
                continue
            certified += 1
            line = apart + sum(m.sep(x, size) for x in m.words)
            if w > line:
                problems.append(("conservative", label, text, round(w, 3), round(line, 3)))
    print(f"\njoined by zero-width spaces: {certified} certified, {refused} not; {wider_than_apart} drawn wider than "
          f"their words apart")
    assert problems == [], problems[:20]
    assert wider_than_apart > 0 and certified > 9 * refused > 0


# board_generic's countdown ring: body 700 at the label size (40 px in 16:9, 44 px in 9:16), tabular numerals
TABULAR = [str(n) for n in (*range(0, 21), 30, 45, 59, 60, 99, 100, 111, 120, 999, 1000, 1111, 2026, 9999, 12345)]


async def test_tabular_numerals_are_the_pages(real_app) -> None:
    """The ring's number (``.dyn-ring-num``, ``font-variant-numeric: tabular-nums``; ``SlotSpec.tabular_nums``) is
    shaped with ``tnum``: within the parity tolerance of the page, in each body face, where proportional figures miss
    by up to 50% (Inter's ``1``)."""
    jobs, meta = [], []
    for family in _BODY:
        fonts = _fonts("body", family)
        m = tf.measure(" ".join(TABULAR), fonts=fonts, role="body", weight=700, language="en-IN", tabular=True)
        plain = tf.measure(" ".join(TABULAR), fonts=fonts, role="body", weight=700, language="en-IN")
        for size in (40.0, 44.0, 100.0):
            jobs.append((fc.css_stack(fonts, "body", "en-IN"), 700, size, 0.0, "en-IN", TABULAR, "tabular-nums"))
            meta.append((f"{family} 700 tabular-nums @{size:g}px", m, plain, size))
    res = Residuals()
    missed = 0.0
    for (label, m, plain, size), page in zip(meta, await _widths(real_app, jobs), strict=True):
        for word, other, w in zip(m.words, plain.words, page, strict=True):
            res.word(label, m, word, size, w)
            missed = max(missed, abs(w - plain.width(other, size, "raw")) / w)
    print("\n" + res.report("Tabular numerals") + f"\n  proportional figures would miss by up to {missed:.1%}")
    assert res.problems == [] and res.uncertified == {}, (res.problems[:20], res.uncertified)
    assert missed > 0.2  # the probe sees the feature: without tnum the widths are not the page's
