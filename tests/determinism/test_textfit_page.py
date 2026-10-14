"""Measured text fit against the real render page (real Chromium, the vendored faces; DYNAMIC_VIDEO_DESIGN §4.6, R-29;
B2 plan §1.11, §2 "Fonts"): what Python's measurement promises, checked where the page decides the pixels.

* **Every word.** In every vendored Latin face at every weight and letter-spacing a slot uses, the page draws each word
  of a corpus (kerning-prone pairs, digits, punctuation, lesson words; each face's largest measured positive pairs,
  alone and repeated, and words of several such pairs: B2b review F1) no wider than ``textfit``'s conservative width
  (Pillow's advance + the 4% slack + the kerning floor + the measured bounds of its pairs and contexts): the per-word
  bound everything else rests on. A word holding a context the table does not bound (Inter's chained ``*``, a
  context's exception such as ``10x10``: F1, round 2) is not certified, and is left out.
* **board_generic's slots** (title: stack, quiz and chapter-card layouts; the kicker; the chapter card's subtitle), both
  formats, the B1 faces and three B2-form sets (Poppins / Atkinson / Anek, Lexend / Inter / Noto Sans, Inter / Inter /
  Noto Sans), for typical
  titles, wide-glyph titles at and past the line limit (a sweep of growing prefixes, so both answers and the edge
  between them are met), one very long word and a hyphenated compound wider than the column:
  - Python fits => the page reports overflow 0 (the kicker: is not cut with an ellipsis), at a size >= Python's
    (unless the page took the slot's preferred line count at a smaller size);
  - Python overflow => the page overflows, or fits only within Python's margins: the page's own measured words, made
    as much wider as Python can make a word (the 4% slack, the kerning floor, and its pairs' and contexts' bounds both
    ways, as Python's width leaves out the kerning that draws a word narrower), no longer fit (the plan's "within 4% /
    8% slack");
  - the subtitle (no line limit): Python's line count is never below the page's.
* **Kerned words the page keeps whole** (``AVAV...``, B2b review F2): drawn up to 12% narrower than Pillow's advance,
  so whether the page breaks them is not decided from the slack alone; and **words wider than the column at every
  size** in Outfit, Inter and Lexend, which the page breaks at every size (F2, round 2: read as words the page might
  keep whole, they were fitted at the minimum size): Python's size is the page's (a broken word's: at most one size
  step smaller, as Python's pieces are conservative).
* **Indic text is not certified** (Tamil, Hindi, mixed Tamil + English): ``fits`` is None. The basic layout cannot
  bound a shaped Indic cluster (measured here: syllables drawn far wider and far narrower than their characters'
  advances, more than the design's 8% either way), so the 8% gives an estimate only; the estimate is still a size
  within the slot's range with its lines, and the Indic titles render without a page error.
"""

from __future__ import annotations

import math
import unicodedata
from typing import Any

import pytest

from aadhi.compose.dynamic import faces as fc
from aadhi.compose.dynamic import kerning as kn
from aadhi.compose.dynamic import textfit as tf
from aadhi.compose.dynamic.stage_page import open_stage
from aadhi.compose.dynamic.templates import TEMPLATES

pytestmark = pytest.mark.slow
TOKEN = "textfit.page"
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
# word's natural width (an inline-block probe in the title, so its font, size and letter-spacing) and the space's;
# the kicker's box and natural width; the subtitle's line count.
_SCENE_JS = """(i) => {
  const root = document.querySelector(`.dyn-scene[data-scene="${i}"]`);
  const probe = (host, text) => {
    const e = document.createElement('span');
    e.style.cssText = 'display:inline-block;white-space:pre;position:absolute;visibility:hidden';
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
    words: words.map((w) => probe(t, w)), space: probe(t, 'x x') - probe(t, 'xx'),
    // the kicker's room: the block's width (the header's max-width) less the pip and the gap
    kicker: k && k.textContent ? { cut: k.scrollWidth > k.clientWidth, natural: probe(header, k.textContent),
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


def _page_fits_with_margins(got: dict[str, Any], spec: Any, slack: float, m: tf.Measured) -> bool:
    """Whether the page's own text still fits the slot at its minimum size when every word is made as much wider as
    Python's margins can make it (from ``m``): ``slack`` and the kerning floor, the most its kerning pairs and contexts
    can add, and the most they can take off (Python's conservative width starts from the advances, without the
    kerning that draws the word narrower on the page: B2b review F2, round 2); a space as wide as Python makes it (a
    negative letter-spacing is not taken off it). The page's rules (whole words, greedy lines; a word wider than the
    column even at the minimum breaks, into at least as many lines as its width needs)."""
    lo = spec.size[0]
    scale = lo / got["size"]
    width = got["width"]
    space = got["space"] * scale + max(0.0, -m.tracking_em) * lo
    lines, cur = 0, None
    for w, word in zip(got["words"], m.words, strict=True):
        ww = w * scale * (1 + slack) + (tf.KERN_FLOOR_EM + word.kern_pos + max(word.kern_neg, 0.0) * (1 + slack)) * lo
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
    titles = [TYPICAL, LONG_WORD, f"{LONG_WORD} is a word", COMPOUND, *_prefixes(WIDE, 4, 2), *_prefixes(PLAIN, 6, 3)]
    kickers = [*_prefixes(KICKERS[0], 2), *_prefixes(KICKERS[1], 3)]
    return [(variant, title, kickers[(i + j) % len(kickers)], SUBTITLE[: 40 + 9 * i])
            for j, variant in enumerate(("default", "quiz", "card")) for i, title in enumerate(titles)]


SLOT_VARIANT = {"default": "default", "quiz": "quiz", "card": "card"}


@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
@pytest.mark.parametrize("font_set", list(FONT_SETS))
async def test_python_fits_implies_the_page_fits_on_board_generic_slots(real_app, fmt: str, font_set: str) -> None:
    fonts = FONT_SETS[font_set]
    cases = _cases_en()
    pages = await _page(real_app, _timeline(fmt, fonts, "en-IN", cases))
    seen = {"fits": 0, "overflow": 0, "band": 0, "kicker_fits": 0, "kicker_cut": 0, "uncertified": 0}
    for (variant, title, kicker, subtitle), got in zip(cases, pages, strict=True):
        spec = SLOTS["title"][fmt][SLOT_VARIANT[variant]]
        where = (fmt, font_set, variant, title)
        assert got["width"] == spec.w, ("the slot's box is the title's", where, got["width"])
        m = tf.measure(title, fonts=fonts, role=spec.face, weight=spec.weight, language="en-IN",
                       tracking_em=spec.tracking_em)
        # every word: drawn no wider than Python's conservative width (at the page's size)
        for word, page_w in zip(m.words, got["words"], strict=True):
            if word.kern_bounded:
                assert page_w <= m.width(word, got["size"]) + 0.01, (where, word.text, page_w,
                                                                      m.width(word, got["size"]))
        r = tf.fit(m, width=spec.w, size=spec.size, max_lines=spec.max_lines, prefer_lines=spec.prefer_lines)
        if not m.reliable:  # Inter's "-by-": two chain characters among four (never bounded: kerning)
            assert font_set == "inter" and title == COMPOUND and r.fits is None, where
            assert [w.text for w in m.words if not w.kern_bounded] == [COMPOUND], where
            seen["uncertified"] += 1
        elif r.fits:
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
                assert not _page_fits_with_margins(got, spec, tf.SLACK_LATIN, m), ("beyond the slack", where, r, got)
        # the kicker (one line, cut with an ellipsis on the page)
        if got["kicker"] is not None:
            kspec = SLOTS["kicker"][fmt][SLOT_VARIANT[variant]]
            room = kspec.w - KICKER_ROOM
            assert abs(got["kicker"]["room"] - room) <= 0.5, ("the kicker's box", where, got["kicker"])
            k = tf.fit_slot(kicker, kspec, fonts=fonts, language="en-IN", width=room)
            assert k.reliable and k.fits is not None
            if k.fits:
                seen["kicker_fits"] += 1
                assert not got["kicker"]["cut"], ("kicker", where, kicker, k, got["kicker"])
            else:
                seen["kicker_cut"] += 1
                km = tf.measure(kicker, fonts=fonts, role=kspec.face, weight=kspec.weight, language="en-IN",
                                tracking_em=kspec.tracking_em)  # Python's floor and pair bounds, per word
                margins = sum(tf.KERN_FLOOR_EM + w.kern_pos + max(w.kern_neg, 0.0) * (1 + tf.SLACK_LATIN)
                              for w in km.words) * kspec.size[0]
                assert got["kicker"]["cut"] or got["kicker"]["natural"] * (1 + tf.SLACK_LATIN) + margins > room, (
                    "kicker beyond the slack", where, kicker, k, got["kicker"])
        # the chapter card's subtitle: Python never counts fewer lines than the page draws
        if got["subtitle"] is not None:
            sspec = SLOTS["subtitle"][fmt]["card"]
            assert got["subtitle"]["width"] == sspec.w
            s = tf.fit_slot(subtitle, sspec, fonts=fonts, language="en-IN")
            assert s.fits and len(s.lines) >= got["subtitle"]["lines"], (where, subtitle, s, got["subtitle"])
    # both answers and the edge between them were met (the sweep crosses it), and the kicker both ways
    assert seen["fits"] >= 10 and seen["overflow"] + seen["band"] >= 3, seen
    assert seen["kicker_fits"] >= 5 and seen["kicker_cut"] >= 3, seen


# Words the page draws up to 12% narrower than Pillow's advance (B2b review F2): wider than the portrait title's column
# by Pillow's advance at the minimum size, yet drawn whole on one line there by the page
_KERNED_TITLES = ["AV" * 10, "TY" * 10, "To" * 11, "Yo" * 11]
# Words wider than the column at every size, which the page breaks at every size (B2b review F2, round 2: in Inter and
# Lexend a face-wide lower bound per pair read them as words the page might keep whole, so Python laid them out broken
# only at the minimum size and stored it, where the page takes a larger one)
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
        assert kinds == (("either",) if title in _KERNED_TITLES else ("break",)), (font_set, title, kinds)
        # no exemption for the preferred line count; Python's line count is never fewer
        assert len(r.lines) >= got["lines"], (font_set, title, r, got)
        if title in _KERNED_TITLES:  # kept whole on one line: Python's size is the page's
            assert got["size"] == r.size, (font_set, title, r, got)
        else:  # broken at every size, not fitted at the minimum where the page is not (the F2 symptom); Python's
            # pieces are conservative (the 4% slack, the floor and its kerning bounds wider), so its size may be one
            # step smaller
            assert r.size <= got["size"] <= r.size + tf.SIZE_STEP, (font_set, title, r, got)


_INDIC_CASES = {
    "ta-IN": [TAMIL, MIXED, *_prefixes(TAMIL_LONG, 3, 2)],
    "hi-IN": [HINDI, *_prefixes(HINDI_LONG, 4, 3)],
}


@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
@pytest.mark.parametrize("language", list(_INDIC_CASES))
async def test_indic_text_is_estimated_never_certified(real_app, fmt: str, language: str) -> None:
    titles = _INDIC_CASES[language]
    for font_set in ("b1", "poppins"):
        fonts = FONT_SETS[font_set]
        cases = [(v, t, "Part 1", "") for v in ("default", "card") for t in titles]
        pages = await _page(real_app, _timeline(fmt, fonts, language, cases))
        for (variant, title, _, _), got in zip(cases, pages, strict=True):
            spec = SLOTS["title"][fmt][variant]
            r = tf.fit_slot(title, spec, fonts=fonts, language=language)
            assert r.reliable is False and r.fits is None, (title, r)
            assert spec.size[0] <= r.size <= spec.size[1] and r.lines, r  # an estimate is still given
            assert got["overflow"] >= 0


# A word corpus for the per-word bound: lesson words, digits and units, and every pair of kerning-prone characters
_WORDS = ("Ohm's law: voltage, current and resistance (series) [parallel] “quoted” ‘single’ 7.1 7, 9.1 P. T. W. "
          "Ty To Yo LT AV WA AVATAR WAVE Typography fi fl ff ffi f) f' f? f] f” (f “f 1e-4; -crf A/V 24 V 12 Ω 3.14 "
          "photosynthesis mitochondria Pythagoras' theorem's hypotenuse velocity acceleration (m/s²) 100% 1,000 "
          "x² √2 Pneumonoultramicroscopicsilicovolcanoconiosis").split(" ")
_KERN = "AFLPTVWYfrvwy.,'\")]?7"
_NEXT = "AVWYTacdeoqsvwy.,')]?"
# words whose positive pairs add up (B2b review F1: a per-word allowance certified them narrower than the page)
_MULTI = ("f'f'", "f'f''", "f'f'f'f'", "f)f)f)", "f'(x)g'(x)", "f''(x)", "(j(j(j", "qjqjqj", "ïïïï",
          "<–<–", "[j]f?",
          # contexts no pair shows (Inter: x between digits drawn as a wider multiplication sign; * before
          # punctuation and capitals), alone and adding up
          "3x3", "2x3x4x5", "1x1x1x1", "10x10", "**A**", "A**B**C", "*(A)*", "a*b", "->", "-->", "<->", "=>")
CORPUS = tuple(dict.fromkeys([*_WORDS, *_MULTI, *(a + b for a in _KERN for b in _NEXT)]))
# (family, weight, tracking) of every slot's text: titles (display 700, -0.015 em), kickers (body 600, +0.02 em),
# body text (400 in 16:9, 600 in 9:16), chips and subtitles
STYLES = [(f, 700, -0.015) for f in ("outfit", "poppins", "lexend", "inter", "atkinson_hyperlegible_next")] + [
    (f, w, t) for f in ("inter", "atkinson_hyperlegible_next") for w, t in ((600, 0.02), (400, 0.0), (600, 0.0),
                                                                              (700, 0.0))]

_WIDTHS_JS = """async ([css, words, weight, tracking]) => {
  const host = document.createElement('div');
  Object.assign(host.style, { position: 'absolute', left: '0', top: '0', fontFamily: css, fontWeight: String(weight),
    fontSize: '100px', letterSpacing: `${tracking}em`, visibility: 'hidden' });
  document.querySelector('.dyn-stage').appendChild(host);
  await Promise.all(words.map((w) => document.fonts.load(`${weight} 100px ${css}`, w)));
  const spans = words.map((w) => { const s = document.createElement('span'); s.style.display = 'inline-block';
    s.style.whiteSpace = 'pre'; s.textContent = w; host.appendChild(s); return s; });
  const out = spans.map((s) => s.getBoundingClientRect().width);
  host.remove();
  return out;
}"""


def _top_pairs(family: str, weight: int, n: int = 24) -> list[str]:
    """The face's ``n`` largest measured positive pairs and contexts (its Latin face at ``weight``), alone and three
    times over (a context repeated may be one of the face's open runs: not certified, then)."""
    face = fc.face_for(family, weight, ord("a"))
    assert face is not None
    k = kn.load()[kn.face_name(face)]
    out = []
    for table in (k.positive, k.up):
        # a combining mark makes a cluster textfit never certifies (an estimate only): not a per-word bound case
        plain = {seq: v for seq, v in table.items() if not any(unicodedata.combining(ch) for ch in seq)}
        top = sorted(plain.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
        out += [w for seq, _ in top for w in (seq, seq * 3)]
    return out


async def test_every_latin_face_draws_every_word_within_its_conservative_width(real_app) -> None:
    served = _timeline("landscape", FONT_SETS["b1"], "en-IN", [("default", TYPICAL, "Basics", "")])
    worst: dict[str, float] = {}
    unbounded: set[str] = set()
    async with open_stage(real_app.settings, size=(1920, 1080), token=TOKEN, base_url=real_app.base,
                          served_timeline=served) as page:
        await page.seek(0)
        for family, weight, tracking in STYLES:
            fonts = {"display": family, "body": "inter", "mono": "jetbrains_mono", "indic": "noto_sans"}
            css = fc.css_stack(fonts, "display", "en-IN")
            corpus = list(dict.fromkeys([*CORPUS, *_top_pairs(family, weight)]))
            got = await page.page.evaluate(_WIDTHS_JS, [css, corpus, weight, tracking])
            m = tf.measure(" ".join(corpus), fonts=fonts, role="display", weight=weight, language="en-IN",
                           tracking_em=tracking)
            assert [w.text for w in m.words] == corpus
            key = f"{family} {weight} {tracking:+}"
            for word, page_w in zip(m.words, got, strict=True):
                if not word.kern_bounded:  # a context that reaches four or more characters: never certified
                    assert not word.reliable and all(c.reliable for c in word.clusters), word.text
                    unbounded.add(word.text)
                    continue
                if not word.reliable:  # drawn by a system font (Greek and maths are not in the vendored subsets)
                    assert word.text in ("Ω", "√2") and m.uncovered == ("Ω", "√"), word.text
                    continue
                bound = m.width(word, 100)
                assert page_w <= bound + 0.01, (key, word.text, page_w, bound)
                worst[key] = max(worst.get(key, 0.0), page_w / bound)
        assert page.errors == [] and page.guard_trips == []
    assert len(worst) == len(STYLES), worst
    # Inter's chains and a context's exception are never certified; its x between digits is
    assert {"**A**", "A**B**C", "*(A)*", "10x10"} <= unbounded and "2x3x4x5" not in unbounded, unbounded


# Syllables Pillow's basic layout cannot bound (the shaped glyphs are not the characters' own): each with the
# direction the page differs from Pillow's advance by more than the 8% the design allows Indic text
_INDIC_SYLLABLES = [("रू", "wider"), ("ಕೋ", "wider"), ("டீ", "wider"), ("क्ष", "narrower"), ("ತ್ಝ", "narrower"),
                    ("ஸ்ரீ", "narrower")]


async def test_the_basic_layout_cannot_bound_indic_clusters(real_app) -> None:
    served = _timeline("landscape", FONT_SETS["b1"], "en-IN", [("default", TYPICAL, "Basics", "")])
    async with open_stage(real_app.settings, size=(1920, 1080), token=TOKEN, base_url=real_app.base,
                          served_timeline=served) as page:
        await page.seek(0)
        for text, direction in _INDIC_SYLLABLES:
            fonts = FONT_SETS["b1"]
            css = fc.css_stack(fonts, "display", "en-IN")
            (page_w,) = await page.page.evaluate(_WIDTHS_JS, [css, [text], 700, 0.0])
            m = tf.measure(text, fonts=fonts, role="display", weight=700, language="en-IN")
            (word,) = m.words
            assert not word.reliable and word.indic and not m.reliable, text
            raw = m.width(word, 100, "raw")
            if direction == "wider":
                assert page_w > raw * (1 + tf.SLACK_INDIC), (text, page_w, raw)
            else:
                assert page_w < raw * (1 - tf.SLACK_INDIC), (text, page_w, raw)
        assert page.errors == [] and page.guard_trips == []
