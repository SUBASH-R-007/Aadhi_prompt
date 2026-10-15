"""Measured text fit against the real render page (real Chromium, the vendored faces; DYNAMIC_VIDEO_DESIGN §4.6, R-29;
B2 plan §1.11, §2 "Fonts"): what Python's measurement promises, checked where the page decides the pixels.

* **Every word.** In every vendored Latin face at every weight and letter-spacing a slot uses, the page draws each word
  of a corpus (kerning-prone pairs, digits, punctuation, lesson words, the words whose kerning, ligatures and
  contextual alternates the B2b review found adding up or chaining: ``f'f'f'f'``, ``2x3x4x5``, Inter's ``**A**``,
  ``10x10``) within ``textfit``'s conservative and optimistic widths (its HarfBuzz width with the 0.5% slack and the
  layout unit either way): the per-word bound everything else rests on. Only words with a character no vendored face
  draws (Greek, maths) are not certified.
* **board_generic's slots** (title: stack, quiz and chapter-card layouts; the kicker; the chapter card's subtitle), both
  formats, the B1 faces and three B2-form sets (Poppins / Atkinson / Anek, Lexend / Inter / Noto Sans, Inter / Inter /
  Noto Sans), for typical
  titles, wide-glyph titles at and past the line limit (a sweep of growing prefixes, so both answers and the edge
  between them are met), one very long word and a hyphenated compound wider than the column; and the same in Tamil,
  Hindi and mixed Tamil + English titles (certified since the HarfBuzz step):
  - Python fits => the page reports overflow 0 (the kicker: is not cut with an ellipsis), at a size >= Python's
    (unless the page took the slot's preferred line count at a smaller size);
  - Python overflow => the page overflows, or fits only within Python's margins: the page's own measured words, made
    as much wider as Python can make a word (the 0.5% slack and the layout unit), no longer fit (the plan's "within
    4% / 8% slack", now within 0.5%); for the kicker (one text node: the page kerns across its spaces, which Python's
    word-by-word line leaves out on purpose) its own measured words and spaces, so widened;
  - the subtitle (no line limit): Python's line count is never below the page's.
* **The stage's white-space.** Words are measured as the stage draws them (``white-space: normal`` in a title's
  ``.dyn-word``, ``nowrap`` in the header; the probes use ``nowrap``, which shapes as ``normal`` does): Chromium shapes
  text under ``white-space: pre`` otherwise (a ZWNJ between Latin letters breaks their kerning there), so only the
  space, which ``nowrap`` would trim from an inline-block of its own, is measured under ``pre``.
* **Titles of numbers grouped with U+202F NARROW NO-BREAK SPACE** (``300 000``; the B2b-H review, H-1: such a word is a
  Mongolian run on the page, drawn with no letter-spacing, and B2b-H certified two such titles that the page drew on 3
  lines), and a kicker with them, whose words are Latin inside its one text node.
* **Kerned words the page keeps whole** (``AVAV...``, B2b review F2) and **words wider than the column at every
  size** in Outfit, Inter and Lexend, which the page breaks at every size (F2, round 2): Python's size is the page's
  (a broken word's: at most one size step smaller, as Python's pieces are conservative).
* **The Indic syllables B2b's Pillow advances could not bound** (drawn up to 3.1x wider and 75% narrower than their
  characters' advances) are drawn at their shaped widths.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from aadhi.compose.dynamic import faces as fc
from aadhi.compose.dynamic import textfit as tf
from aadhi.compose.dynamic.stage_page import open_stage
from aadhi.compose.dynamic.templates import TEMPLATES

pytestmark = pytest.mark.slow
TOKEN = "textfit.page"
NN = "\u202f"  # NARROW NO-BREAK SPACE
FPS = 30
SLOTS = TEMPLATES["board_generic"].slots
TOKENS = {"struct.accent": "#FF936D", "bg": "#10132C", "bg-alt": "#181D37", "bg-tint": "#22121C", "surface": "#252A3F"}
CONTENT = {"ink": "#F3F5F9", "ink2": "#B9BEC7", "entity:0": "#0FE3FE", "entity:1": "#C4A4FE", "entity:2": "#FEACE3",
           "warn": "#F8A87F", "ok": "#5DD8AA", "mark": "#F2D935", "focus": "#F2D935"}
FONT_SETS: dict[str, dict[str, str]] = {
    "b1": {"display": "outfit", "body": "inter", "mono": "jetbrains_mono"},  # what B1 / B2a timelines store
    "poppins": {"display": "poppins", "body": "atkinson_hyperlegible_next", "mono": "jetbrains_mono", "indic": "anek"},
    "lexend": {"display": "lexend", "body": "inter", "mono": "jetbrains_mono", "indic": "noto_sans"},
    "inter": {"display": "inter", "body": "inter", "mono": "jetbrains_mono", "indic": "noto_sans"},
}
# board_generic's header: the kicker text sits right of the 12 px pip and the 18 px gap (dynamic.css .dyn-pip,
# .dyn-header); the slot's w is the whole header (templates.py), so the kicker's own box is 30 px narrower
KICKER_ROOM = 12 + 18

TYPICAL = "How does a battery push current through a circuit?"
WIDE = ("Why Warm Moist Air Masses Make Monsoon Weather Worse Mid-Way Through Wet Summer Weeks While "
        "Mumbai Workers Wait")
PLAIN = ("The total resistance of resistors joined in series is the sum of their separate resistances, so the "
         "current is the same everywhere")
LONG_WORD = "Pneumonoultramicroscopicsilicovolcanoconiosis"
COMPOUND = "Electro-magnetic-induction-and-self-inductance-explained-step-by-step"
KICKERS = ("Chapter 12: Electromagnetic induction, Faraday's law and Lenz's law in moving conductors and coils",
           "Quick check: what happens to the current in a series circuit when one more identical lamp is added?")
SUBTITLE = ("Why the current is the same everywhere in a series circuit, and how each lamp shares the battery's "
            "voltage")
TAMIL = "மின்னோட்டம் என்றால் என்ன?"
TAMIL_LONG = "ஒரு மின்சுற்றில் உள்ள மின்தடையங்கள் தொடராக இணைக்கப்படும்போது மொத்த மின்தடை எவ்வாறு மாறுகிறது?"
MIXED = "Ohm விதி: மின்னழுத்தம் V = மின்னோட்டம் I × மின்தடை R"
HINDI = "विद्युत धारा क्या है?"
HINDI_LONG = "श्रेणीक्रम में जुड़े प्रतिरोधों का कुल प्रतिरोध उनके अलग-अलग प्रतिरोधों के योग के बराबर क्यों होता है?"
# numbers grouped with U+202F (the B2b-H review's titles, H-1, which B2b-H certified and the page drew on 3 lines), and a
# kicker of them (one text node: Latin there)
SPACED_NUMBERS = (f"reach from 300{NN}000 1{NN}000{NN}000 to Earth 300{NN}000",
                  f"take 300{NN}000 eight from Earth light 149{NN}600{NN}000 1{NN}000{NN}000 6{NN}371 300{NN}000 "
                  f"across 6{NN}371 Why Earth about",
                  f"Light covers 300{NN}000 km each second", f"5{NN}% of 1{NN}000{NN}000 is 50{NN}000")
SPACED_KICKER = f"Chapter 3: light covers 300{NN}000 km per second and 1{NN}000{NN}000 in under 4 seconds flat"


def _prefixes(text: str, start: int, step: int = 1) -> list[str]:
    w = text.split(" ")
    return [" ".join(w[:n]) for n in range(start, len(w) + 1, step)]


def _beat(beat_id: str, start: float, end: float, text: str) -> dict[str, Any]:
    return {"beat_id": beat_id, "index": 0, "start": start, "speech_end": end, "end": end, "narration": text,
            "captions": [{"start": start, "end": end, "text": text}]}


def _look() -> dict[str, Any]:
    return {"template": "board_generic", "tokens": dict(TOKENS),
            "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": 7, "frozen_at": None},
            "transition_in": {"kind": "cut", "seconds": 0}}


def _scene(variant: str, title: str, kicker: str, subtitle: str) -> dict[str, Any]:
    if variant == "quiz":
        return {"type": "quiz_checkpoint", "title": kicker, "duration": 6.0,
                "beats": [_beat("q1", 0.5, 2.5, "What happens?")],
                "quiz": {"question": title, "options": ["Yes", "No", "Maybe", "Never"], "correct_index": 1,
                         "feedback_wrong": ["", "", "", ""], "explanation": "It doubles.", "countdown_start": 3,
                         "countdown_seconds": 2, "reveal_start": 5}}
    if variant == "card":
        return {"type": "chapter_card", "title": title, "chapter_label": kicker, "subtitle": subtitle, "duration": 4.0,
                "board": [], "beats": []}
    return {"type": "content", "title": title, "duration": 4.0, "board": [{"id": "b1", "kind": "bullet", "text": "Ohm"}],
            "beats": [_beat("k1", 0.5, 2.0, "Ohm.")]}


def _timeline(fmt: str, fonts: dict[str, str], language: str, cases: list[tuple[str, str, str, str]]) -> dict[str, Any]:
    """One scene per case ``(variant, title, kicker, subtitle)``; a stack scene's kicker is its chapter's title."""
    scenes: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []
    t = 0.0
    for i, (variant, title, kicker, subtitle) in enumerate(cases):
        scene = {"scene_id": f"s{i}", "index": i, "start": t, "audio_offset": 0.5, "look": _look(),
                 **_scene(variant, title, kicker, subtitle)}
        scenes.append(scene)
        chapters.append({"start": t, "title": kicker if variant == "default" else f"Part {i}"})
        t += scene["duration"]
    portrait = fmt == "portrait"
    return {
        "schema_version": 3, "language": language, "board_language": language, "fps": FPS,
        "width": 1080 if portrait else 1920, "height": 1920 if portrait else 1080, "total_duration": t,
        "chapters": chapters, "captions": [], "transition_seconds": 0.4, "scenes": scenes,
        "presentation": {
            "style": "dynamic", "format": fmt, "safe_zone": "universal", "content": dict(CONTENT), "fonts": dict(fonts),
            "personality": "crisp", "caption_style": "clean_pill", "cue_term_style": "underline", "captions_burn": False,
            "sfx": False, "motion": "standard", "generated_seconds": t, "engine_version": 1,
        },
    }


# The scene's title block as the page laid it out: the title's size, line height and line count, its box, every
# word's natural width (an inline-block probe in the title, so its font, size and letter-spacing) and the space's own
# (alone: what Python measures; a face may kern a space against its neighbours); the kicker's box, natural width,
# words and space; the subtitle's line count.
_SCENE_JS = """(i) => {
  const root = document.querySelector(`.dyn-scene[data-scene="${i}"]`);
  // white-space as the stage has it (nowrap shapes as normal does; pre would not: a ZWNJ breaks the kerning there),
  // pre only for a lone space, which nowrap trims from an inline-block of its own
  const probe = (host, text) => {
    const e = document.createElement('span');
    const ws = text.trim() ? 'nowrap' : 'pre';
    e.style.cssText = `display:inline-block;white-space:${ws};position:absolute;visibility:hidden`;
    e.textContent = text;
    host.appendChild(e);
    const w = e.getBoundingClientRect().width;
    e.remove();
    return w;
  };
  const block = root.querySelector('.dyn-titleblock');
  const t = root.querySelector('.dyn-title');
  const cs = getComputedStyle(t);
  const size = parseFloat(cs.fontSize);
  const lh = parseFloat(cs.lineHeight);
  const words = [...t.querySelectorAll('.dyn-word')].map((w) => w.textContent);
  const header = root.querySelector('.dyn-header');
  const k = root.querySelector('.dyn-kicker-text');
  const pip = root.querySelector('.dyn-pip');
  const sub = root.querySelector('.dyn-subtitle');
  return {
    size, lines: Math.round(t.offsetHeight / lh), width: block.clientWidth,  // the title's room (it shrinks to fit)
    words: words.map((w) => probe(t, w)), space: probe(t, ' '),
    // the kicker's room: the block's width (the header's max-width) less the pip and the gap
    kicker: k && k.textContent ? { cut: k.scrollWidth > k.clientWidth, natural: probe(header, k.textContent),
      words: k.textContent.split(' ').filter((w) => w).map((w) => probe(header, w)),
      space: probe(header, ' '),
      room: block.clientWidth - pip.getBoundingClientRect().width - parseFloat(getComputedStyle(header).columnGap) }
      : null,
    subtitle: sub ? { lines: Math.round(sub.offsetHeight / parseFloat(getComputedStyle(sub).lineHeight)),
      width: Math.min(block.clientWidth, parseFloat(getComputedStyle(sub).maxWidth)) } : null,  // its room
  };
}"""


async def _page(real_app, served: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    async with open_stage(real_app.settings, size=(served["width"], served["height"]), token=TOKEN,
                          base_url=real_app.base, served_timeline=served) as page:
        for i, scene in enumerate(served["scenes"]):
            await page.seek(round((scene["start"] + 2.5) * FPS))  # the title settled (its words in by ~1.3 s)
            boxes = [b for b in await page.text_boxes() if b.get("scene") == i]
            got = await page.page.evaluate(_SCENE_JS, i)
            got["overflow"] = next(b["overflow"] for b in boxes if b.get("role") == "title")
            out.append(got)
        assert page.errors == [] and page.guard_trips == [], (page.errors, page.guard_trips)
    return out


# what the page's own widths, scaled from its size to the minimum, may differ by from Python's raw widths there: the
# parity probe's residual (< 0.06 px) and the scaling of a layout unit (tests/determinism/test_textfit_parity.py)
MEASURED_PX = 3 * tf.BOX_PX


def _widened(page_px: float) -> float:
    """A width the page measured, made as much wider as Python makes a width (the slack and the layout unit) and the
    measurement's own allowance (:data:`MEASURED_PX`)."""
    return page_px * (1 + tf.SLACK) + tf.BOX_PX + MEASURED_PX


def _page_fits_with_margins(got: dict[str, Any], spec: Any, m: tf.Measured) -> bool:
    """Whether the page's own text still fits the slot at its minimum size when every word is made as much wider as
    Python's margins make it (:func:`_widened`); a space as wide as Python makes it (a negative letter-spacing is not
    taken off it). The page's rules (whole words, greedy lines; a word wider than the column even at the minimum
    breaks, into at least as many lines as its width needs)."""
    lo = spec.size[0]
    scale = lo / got["size"]
    width = got["width"]
    space = _widened(got["space"] * scale) + max(0.0, -m.tracking_em) * lo
    lines, cur = 0, None
    for w in got["words"]:
        ww = _widened(w * scale)
        if ww > width:  # unavoidable at the minimum: lines of its own
            lines += (1 if cur is not None else 0) + math.ceil(ww / width)
            cur = None
        elif cur is not None and cur + space + ww <= width:
            cur += space + ww
        else:
            lines += 1 if cur is not None else 0
            cur = ww
    lines += 1 if cur is not None else 0
    return lines <= spec.max_lines


def _cases_en() -> list[tuple[str, str, str, str]]:
    titles = [TYPICAL, LONG_WORD, f"{LONG_WORD} is a word", COMPOUND, *_prefixes(WIDE, 4, 2), *_prefixes(PLAIN, 6, 3),
              *SPACED_NUMBERS]
    kickers = [*_prefixes(KICKERS[0], 2), *_prefixes(KICKERS[1], 3), *_prefixes(SPACED_KICKER, 4, 2)]
    return [(variant, title, kickers[(i + j) % len(kickers)], SUBTITLE[: 40 + 9 * i])
            for j, variant in enumerate(("default", "quiz", "card")) for i, title in enumerate(titles)]


SLOT_VARIANT = {"default": "default", "quiz": "quiz", "card": "card"}


def _check_slots(fmt: str, font_set: str, language: str, cases: list[tuple[str, str, str, str]],
                 pages: list[dict[str, Any]], seen: dict[str, int]) -> None:
    """The board_generic slots of each case against what the page laid out (the module docstring)."""
    fonts = FONT_SETS[font_set]
    for (variant, title, kicker, subtitle), got in zip(cases, pages, strict=True):
        spec = SLOTS["title"][fmt][SLOT_VARIANT[variant]]
        where = (fmt, font_set, variant, title)
        assert got["width"] == spec.w, ("the slot's box is the title's", where, got["width"])
        m = tf.measure(title, fonts=fonts, role=spec.face, weight=spec.weight, language=language,
                       tracking_em=spec.tracking_em)
        assert m.reliable, (where, m.uncovered)
        # every word: drawn within Python's conservative and optimistic widths (at the page's size)
        for word, page_w in zip(m.words, got["words"], strict=True):
            assert m.width(word, got["size"], "optimistic") <= page_w <= m.width(word, got["size"]), (
                where, word.text, page_w, m.width(word, got["size"], "raw"))
        r = tf.fit(m, width=spec.w, size=spec.size, max_lines=spec.max_lines, prefer_lines=spec.prefer_lines)
        if r.fits:
            seen["fits"] += 1
            assert got["overflow"] == 0, (where, r, got)
            preferred = spec.prefer_lines is not None and got["lines"] <= spec.prefer_lines < len(r.lines)
            assert got["size"] >= r.size or preferred, (where, r.size, got["size"])
        else:
            assert r.fits is False and r.size == spec.size[0], (where, r)
            if got["overflow"] > 0:
                seen["overflow"] += 1
            else:
                seen["band"] += 1
                assert not _page_fits_with_margins(got, spec, m), ("beyond the margins", where, r, got)
        # the kicker (one line, cut with an ellipsis on the page)
        if got["kicker"] is not None:
            kspec = SLOTS["kicker"][fmt][SLOT_VARIANT[variant]]
            room = kspec.w - KICKER_ROOM
            assert abs(got["kicker"]["room"] - room) <= 0.5, ("the kicker's box", where, got["kicker"])
            k = tf.fit_slot(kicker, kspec, fonts=fonts, language=language, width=room)
            assert k.reliable and k.fits is not None
            if k.fits:
                seen["kicker_fits"] += 1
                assert not got["kicker"]["cut"], ("kicker", where, kicker, k, got["kicker"])
            else:
                seen["kicker_cut"] += 1
                # its own words and spaces, made as much wider as Python makes them: the page kerns across the spaces
                # of its one text node, which Python's word-by-word line leaves out on purpose (never wider: the
                # parity probe's flow lines)
                kw = got["kicker"]["words"]
                own = sum(_widened(w) for w in kw) + (len(kw) - 1) * (
                    _widened(got["kicker"]["space"]) + max(0.0, -kspec.tracking_em) * kspec.size[0])
                assert got["kicker"]["cut"] or own > room, ("kicker beyond the margins", where, kicker, k,
                                                            got["kicker"])
        # the chapter card's subtitle: Python never counts fewer lines than the page draws
        if got["subtitle"] is not None:
            sspec = SLOTS["subtitle"][fmt]["card"]
            assert got["subtitle"]["width"] == sspec.w
            s = tf.fit_slot(subtitle, sspec, fonts=fonts, language=language)
            assert s.fits and len(s.lines) >= got["subtitle"]["lines"], (where, subtitle, s, got["subtitle"])


@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
@pytest.mark.parametrize("font_set", list(FONT_SETS))
async def test_python_fits_implies_the_page_fits_on_board_generic_slots(real_app, fmt: str, font_set: str) -> None:
    cases = _cases_en()
    pages = await _page(real_app, _timeline(fmt, FONT_SETS[font_set], "en-IN", cases))
    seen = {"fits": 0, "overflow": 0, "band": 0, "kicker_fits": 0, "kicker_cut": 0}
    _check_slots(fmt, font_set, "en-IN", cases, pages, seen)
    # both answers and the edge between them were met (the sweep crosses it), and the kicker both ways
    assert seen["fits"] >= 10 and seen["overflow"] + seen["band"] >= 3, seen
    assert seen["kicker_fits"] >= 5 and seen["kicker_cut"] >= 3, seen


# Words the page draws narrower than their letters' advances (B2b review F2: up to 12% below Pillow's): wider than the
# portrait title's column by those advances at the minimum size, yet drawn whole on one line there by the page
_KERNED_TITLES = ["AV" * 10, "TY" * 10, "To" * 11, "Yo" * 11]
# Words wider than the column at every size, which the page breaks at every size (B2b review F2, round 2: Python must
# not lay them out broken only at the minimum size and store it where the page takes a larger one)
_OVER_WIDE = ["Supercalifragilisticexpialidocious", LONG_WORD, "Electrochemistry_and_batteries_overview"]
_WHOLE_OR_BROKEN = [("b1", t) for t in _KERNED_TITLES] + [
    (s, t) for s in ("b1", "inter", "lexend") for t in _OVER_WIDE]


@pytest.mark.parametrize("font_set", ["b1", "inter", "lexend"])
async def test_pythons_size_is_the_pages_for_kerned_and_over_wide_words(real_app, font_set: str) -> None:
    spec = SLOTS["title"]["portrait"]["default"]
    fonts = FONT_SETS[font_set]
    titles = [t for s, t in _WHOLE_OR_BROKEN if s == font_set]
    cases = [("default", t, "Basics", "") for t in titles]
    pages = await _page(real_app, _timeline("portrait", fonts, "en-IN", cases))
    for title, got in zip(titles, pages, strict=True):
        r = tf.fit_slot(title, spec, fonts=fonts, language="en-IN")
        m = tf.measure(title, fonts=fonts, role=spec.face, weight=spec.weight, tracking_em=spec.tracking_em)
        kinds = tf.word_kinds(m, spec.w, spec.size[0])
        assert r.reliable and (got["overflow"] == 0 or not r.fits), (font_set, title, r, got)
        # a kerned word is never read as certainly broken (whole, or either within the margins); an over-wide one is
        assert kinds in ((("either",), ("whole",)) if title in _KERNED_TITLES else (("break",),)), (font_set, title,
                                                                                                      kinds)
        # no exemption for the preferred line count; Python's line count is never fewer
        assert len(r.lines) >= got["lines"], (font_set, title, r, got)
        if title in _KERNED_TITLES:  # kept whole on one line: Python's size is the page's
            assert got["size"] == r.size, (font_set, title, r, got)
        else:  # broken at every size, not fitted at the minimum where the page is not (the F2 symptom); Python's
            # pieces are conservative (the slack and the layout unit wider), so its size may be one step smaller
            assert r.size <= got["size"] <= r.size + tf.SIZE_STEP, (font_set, title, r, got)


def _cases_indic(language: str) -> list[tuple[str, str, str, str]]:
    long = TAMIL_LONG if language == "ta-IN" else HINDI_LONG
    titles = ([TAMIL, MIXED, *_prefixes(TAMIL_LONG, 3, 2)] if language == "ta-IN"
              else [HINDI, *_prefixes(HINDI_LONG, 4, 3)])
    titles.append(f"{long} {long}")  # past the line limit in every layout
    kickers = _prefixes(f"{long} {long}", 2, 2)
    return [(variant, title, kickers[(i + j) % len(kickers)], long[: 20 + 7 * i])
            for j, variant in enumerate(("default", "quiz", "card")) for i, title in enumerate(titles)]


@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
@pytest.mark.parametrize("language", ["ta-IN", "hi-IN"])
async def test_indic_text_is_certified_and_python_fits_implies_the_page_fits(real_app, fmt: str,
                                                                            language: str) -> None:
    """Tamil, Hindi and mixed Tamil + English on board_generic's slots, with the Noto Sans group (the B1 faces) and
    Anek (the Poppins set): the same promises as Latin text (B2b gave them ``fits`` None: Pillow could not bound the
    shaping)."""
    cases = _cases_indic(language)
    seen = {"fits": 0, "overflow": 0, "band": 0, "kicker_fits": 0, "kicker_cut": 0}
    for font_set in ("b1", "poppins"):
        pages = await _page(real_app, _timeline(fmt, FONT_SETS[font_set], language, cases))
        _check_slots(fmt, font_set, language, cases, pages, seen)
    assert seen["fits"] >= 6 and seen["overflow"] + seen["band"] >= 1, seen
    assert seen["kicker_fits"] >= 2 and seen["kicker_cut"] >= 2, seen


# A word corpus for the per-word bound: lesson words, digits and units, and every pair of kerning-prone characters
_WORDS = ("Ohm's law: voltage, current and resistance (series) [parallel] “quoted” ‘single’ 7.1 7, 9.1 P. T. W. "
          "Ty To Yo LT AV WA AVATAR WAVE Typography fi fl ff ffi f) f' f? f] f” (f “f 1e-4; -crf A/V 24 V 12 Ω 3.14 "
          "photosynthesis mitochondria Pythagoras' theorem's hypotenuse velocity acceleration (m/s²) 100% 1,000 "
          "x² √2 Pneumonoultramicroscopicsilicovolcanoconiosis").split(" ")
_KERN = "AFLPTVWYfrvwy.,'\")]?7"
_NEXT = "AVWYTacdeoqsvwy.,')]?"
# words whose kerning adds up per pair (B2b review F1: a per-word allowance certified them narrower than the page),
# contexts no pair shows (Inter: x between digits drawn as a wider multiplication sign) and contexts that chain along
# a run (Inter's * beside capitals and punctuation, which B2b's kerning table could not bound)
_MULTI = ("f'f'", "f'f''", "f'f'f'f'", "f)f)f)", "f'(x)g'(x)", "f''(x)", "(j(j(j", "qjqjqj", "ïïïï",
          "<–<–", "[j]f?", "3x3", "2x3x4x5", "1x1x1x1", "10x10", "**A**", "A**B**C", "*(A)*", "a*b", "->", "-->",
          "<->", "=>", "A*****", "*:<H", "step-by-step", "f(x)", "(a+b)", "ffiw", "office",
          # the B2b-H review: ZWNJ / ZWJ between Latin letters (drawn kerned and ligated across them under the stage's
          # white-space), numbers grouped with U+202F (Mongolian runs: no letter-spacing)
          "A\u200cV", "f\u200cfi", "A\u200cVA\u200cVA", "A\u200dV", f"1{NN}000", f"5{NN}%", f"(1{NN}000)",
          f"300{NN}000", f"1{NN}000km")
CORPUS = tuple(dict.fromkeys([*_WORDS, *_MULTI, *(a + b for a in _KERN for b in _NEXT)]))
# (family, weight, tracking) of every slot's text: titles (display 700, -0.015 em), kickers (body 600, +0.02 em),
# body text (400 in 16:9, 600 in 9:16), chips and subtitles
STYLES = [(f, 700, -0.015) for f in ("outfit", "poppins", "lexend", "inter", "atkinson_hyperlegible_next")] + [
    (f, w, t) for f in ("inter", "atkinson_hyperlegible_next") for w, t in ((600, 0.02), (400, 0.0), (600, 0.0),
                                                                              (700, 0.0))]

_WIDTHS_JS = """async ([css, words, weight, tracking, lang]) => {
  const host = document.createElement('div');
  Object.assign(host.style, { position: 'absolute', left: '0', top: '0', fontFamily: css, fontWeight: String(weight),
    fontSize: '100px', letterSpacing: `${tracking}em`, visibility: 'hidden', whiteSpace: 'nowrap' });
  host.lang = lang;
  document.querySelector('.dyn-stage').appendChild(host);
  await Promise.all(words.map((w) => document.fonts.load(`${weight} 100px ${css}`, w)));
  const spans = words.map((w) => { const s = document.createElement('span'); s.style.display = 'inline-block';
    s.textContent = w; host.appendChild(s); return s; });
  const out = spans.map((s) => s.getBoundingClientRect().width);
  host.remove();
  return out;
}"""


async def test_every_latin_face_draws_every_word_within_its_conservative_width(real_app) -> None:
    served = _timeline("landscape", FONT_SETS["b1"], "en-IN", [("default", TYPICAL, "Basics", "")])
    worst: dict[str, float] = {}
    async with open_stage(real_app.settings, size=(1920, 1080), token=TOKEN, base_url=real_app.base,
                          served_timeline=served) as page:
        await page.seek(0)
        for family, weight, tracking in STYLES:
            fonts = {"display": family, "body": "inter", "mono": "jetbrains_mono", "indic": "noto_sans"}
            css = fc.css_stack(fonts, "display", "en-IN")
            got = await page.page.evaluate(_WIDTHS_JS, [css, list(CORPUS), weight, tracking, "en-IN"])
            m = tf.measure(" ".join(CORPUS), fonts=fonts, role="display", weight=weight, language="en-IN",
                           tracking_em=tracking)
            assert [w.text for w in m.words] == list(CORPUS)
            key = f"{family} {weight} {tracking:+}"
            for word, page_w in zip(m.words, got, strict=True):
                if not word.reliable:  # drawn by a system font (Greek and maths are not in the vendored subsets)
                    assert word.text in ("Ω", "√2") and m.uncovered == ("Ω", "√"), word.text
                    continue
                low, high = m.width(word, 100, "optimistic"), m.width(word, 100)
                assert low <= page_w <= high, (key, word.text, low, page_w, high)
                worst[key] = max(worst.get(key, 0.0), page_w / high)
        assert page.errors == [] and page.guard_trips == []
    assert len(worst) == len(STYLES), worst


# Syllables B2b's Pillow advances could not bound (the shaped glyphs are not the characters' own): each with the
# direction the page differs from Pillow's basic-layout advance (B2b's measurement) by more than the design's 8%
_INDIC_SYLLABLES = [("रू", "wider"), ("ಕೋ", "wider"), ("டீ", "wider"), ("क्ष", "narrower"), ("ತ್ಝ", "narrower"),
                    ("ஸ்ரீ", "narrower")]


async def test_indic_clusters_are_drawn_at_their_shaped_widths(real_app) -> None:
    from PIL import ImageFont

    served = _timeline("landscape", FONT_SETS["b1"], "en-IN", [("default", TYPICAL, "Basics", "")])
    async with open_stage(real_app.settings, size=(1920, 1080), token=TOKEN, base_url=real_app.base,
                          served_timeline=served) as page:
        await page.seek(0)
        for text, direction in _INDIC_SYLLABLES:
            fonts = FONT_SETS["b1"]
            css = fc.css_stack(fonts, "display", "en-IN")
            (page_w,) = await page.page.evaluate(_WIDTHS_JS, [css, [text], 700, 0.0, "en-IN"])
            m = tf.measure(text, fonts=fonts, role="display", weight=700, language="en-IN")
            (word,) = m.words
            assert word.reliable and word.indic and m.reliable, text
            assert m.width(word, 100, "optimistic") <= page_w <= m.width(word, 100), (text, page_w)
            assert abs(page_w - m.width(word, 100, "raw")) < 0.05, (text, page_w, m.width(word, 100, "raw"))
            # B2b's measurement: Pillow's basic layout (no shaping) on the same face
            key = word.clusters[0].key
            assert key is not None
            pillow = ImageFont.truetype(key.path, 100, layout_engine=ImageFont.Layout.BASIC).getlength(text)
            if direction == "wider":
                assert page_w > pillow * 1.08, (text, page_w, pillow)
            else:
                assert page_w < pillow * 0.92, (text, page_w, pillow)
        assert page.errors == [] and page.guard_trips == []
