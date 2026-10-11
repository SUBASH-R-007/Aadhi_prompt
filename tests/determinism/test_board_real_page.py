"""``board_generic``'s quiz and title block on the real render page (real Chromium layout, real fonts, real MathJax,
the real preflight; DYNAMIC_VIDEO_DESIGN §4.3, §7.12, R-29, R-53, R-59): what jsdom cannot measure.

* a question in rich-lite (``$TeX$``, ``**bold**``, ``[[term]]``, `` `code` ``) is drawn as such, word by word, its
  TeX converted before the frame is captured, its bold in a heavier face than the 700 title (visible), and reported
  to preflight as its plain text;
* the countdown ring never overlaps the question or the kicker: in 16:9 the title block keeps clear of the ring at the
  top right; in 9:16 the ring sits below the question, right of the option column, above the action-rail band;
* the countdown number is reported as a (non-critical) label at the R-29 label size of its format;
* preflight's contrast remedy for a chapter card's kicker (a scrim planned for slot ``kicker``) is drawn, and the
  second pass measures the text on the scrim, so the finding ends ``fixed``, not ``warn``.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from aadhi.compose.dynamic.colour import text_contrast
from aadhi.compose.dynamic.preflight import run_preflight
from aadhi.compose.dynamic.stage_page import open_stage
from aadhi.schemas.timeline import Timeline

pytestmark = pytest.mark.slow
TOKEN = "board.real-page"
FPS = 30
TOKENS = {"struct.accent": "#FF936D", "bg": "#10132C", "bg-alt": "#181D37", "bg-tint": "#22121C", "surface": "#252A3F"}
CONTENT = {"ink": "#F3F5F9", "ink2": "#B9BEC7", "entity:0": "#0FE3FE", "entity:1": "#C4A4FE", "entity:2": "#FEACE3",
           "warn": "#F8A87F", "ok": "#5DD8AA", "mark": "#F2D935", "focus": "#F2D935"}
RICH = "If $V = IR$ and **R** doubles, what happens to the [[current]] `I`?"
RICH_PLAIN = "If V = IR and R doubles, what happens to the current I?"
LONG = ("A circuit runs on a 12 V battery and we swap it for a 24 V battery while the resistance stays exactly the same: "
        "what happens to the current that flows through the lamp?")
LONG_KICKER = "Checkpoint: voltage, current and resistance in a series circuit with one lamp"
OPTIONS = ["It stays the same", "It doubles", "It halves", "It gets used up faster"]
COUNTDOWN_AT = 3.0  # scene time; the countdown runs 3-8 s, the answer at 8 s


def _timeline(fmt: str, question: str, *, title: str = "Quick check") -> dict[str, Any]:
    portrait = fmt == "portrait"
    duration = 12.0
    beat = {"beat_id": "q1", "index": 0, "start": 0.5, "speech_end": 2.5, "end": 2.7,
            "narration": "What happens to the current?",
            "captions": [{"start": 0.5, "end": 2.5, "text": "What happens to the current?"}]}
    scene = {
        "scene_id": "q", "index": 0, "type": "quiz_checkpoint", "title": title, "start": 0, "duration": duration,
        "audio_offset": 0.5, "beats": [beat],
        "quiz": {"question": question, "options": OPTIONS, "correct_index": 1, "feedback_wrong": ["", "", "", ""],
                 "explanation": "$I = V / R$, so doubling the voltage doubles the current.",
                 "countdown_start": COUNTDOWN_AT, "countdown_seconds": 5, "reveal_start": 8},
        "look": {"template": "board_generic", "tokens": dict(TOKENS),
                 "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": 7, "frozen_at": None},
                 "transition_in": {"kind": "cut", "seconds": 0}},
    }
    return {
        "schema_version": 3, "language": "en-IN", "board_language": "en-IN", "fps": FPS,
        "width": 1080 if portrait else 1920, "height": 1920 if portrait else 1080, "total_duration": duration,
        "chapters": [{"start": 0, "title": "Basics"}], "captions": [], "transition_seconds": 0.4, "scenes": [scene],
        "presentation": {
            "style": "dynamic", "format": fmt, "safe_zone": "universal", "content": dict(CONTENT),
            "fonts": {"display": "outfit", "body": "inter", "mono": "jetbrains_mono"}, "personality": "crisp",
            "caption_style": "clean_pill", "cue_term_style": "underline", "captions_burn": False, "sfx": False,
            "motion": "standard", "generated_seconds": duration, "engine_version": 1,
        },
    }


_TITLE_JS = """() => {
  const rect = (e) => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; };
  const title = document.querySelector('.dyn-title');
  const words = [...title.querySelectorAll('.dyn-word')];
  return {
    text: title.textContent,
    words: words.map((w) => ({ text: w.textContent, opacity: Number(w.style.opacity || '0'), rect: rect(w) })),
    tex: [...title.querySelectorAll('.rt-math')].map((m) => ({ pending: m.classList.contains('is-pending'),
      error: m.classList.contains('is-error'), mjx: !!m.querySelector('mjx-container svg'), rect: rect(m) })),
    bold: [...title.querySelectorAll('.rt-b')].map((e) => [e.textContent, getComputedStyle(e).fontWeight]),
    term: [...title.querySelectorAll('.rt-k')].map((e) => [e.textContent, getComputedStyle(e).fontWeight]),
    code: [...title.querySelectorAll('code.rt-code')].map((e) => [e.textContent, getComputedStyle(e).fontFamily]),
    title: rect(title),
    titleWeight: getComputedStyle(title).fontWeight,
    // the same text in the title's face at the bold's weight and at the title's: a heavier face is drawn wider
    probe: (() => {
      const b = title.querySelector('.rt-b');
      const width = (weight) => {
        const s = document.createElement('span');
        Object.assign(s.style, { display: 'inline-block', whiteSpace: 'nowrap', fontWeight: weight });
        s.textContent = 'Rs doubles';
        title.appendChild(s);
        const w = s.getBoundingClientRect().width;
        s.remove();
        return w;
      };
      return b ? [width(getComputedStyle(b).fontWeight), width(getComputedStyle(title).fontWeight)] : null;
    })(),
  };
}"""

_RING_JS = """() => {
  const rect = (e) => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; };
  const ring = document.querySelector('.dyn-ring');
  const num = ring.querySelector('.dyn-ring-num');
  return {
    ring: rect(ring), visible: ring.style.visibility, number: num.textContent, numberSize: getComputedStyle(num).fontSize,
    track: getComputedStyle(ring.querySelector('.dyn-ring-track')).stroke,
    words: [...document.querySelectorAll('.dyn-title .dyn-word')].map(rect),
    kicker: rect(document.querySelector('.dyn-kicker-text')),
    block: rect(document.querySelector('.dyn-titleblock')),
    cards: [...document.querySelectorAll('.dyn-card')].filter((c) => Number(c.style.opacity || '0') > 0).map(rect),
  };
}"""


def _overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    return (min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]) > 0
            and min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]) > 0)


async def _at(real_app, served: dict[str, Any], seconds: float, js: str) -> tuple[list[dict], Any]:
    async with open_stage(real_app.settings, size=(served["width"], served["height"]), token=TOKEN,
                          base_url=real_app.base, served_timeline=served) as page:
        await page.seek(round(seconds * FPS))
        boxes = await page.text_boxes()
        extra = await page.page.evaluate(js)
        assert page.errors == [] and page.guard_trips == [], (page.errors, page.guard_trips)
    return boxes, extra


@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
async def test_a_rich_question_is_drawn_with_its_markup_and_tex(real_app, fmt: str) -> None:
    boxes, got = await _at(real_app, _timeline(fmt, RICH), 2.5, _TITLE_JS)
    for literal in ("$", "**", "[[", "]]", "`"):
        assert literal not in got["text"], (literal, got["text"])
    assert [t["pending"] or t["error"] for t in got["tex"]] == [False], got["tex"]
    assert got["tex"][0]["mjx"], "MathJax SVG in the title"
    assert got["tex"][0]["rect"]["w"] > 10
    # **bold** is heavier than the 700 title around it (dynamic.css: Outfit 900), and drawn so, not only declared
    assert int(got["titleWeight"]) == 700
    assert [b[0] for b in got["bold"]] == ["R"] and int(got["bold"][0][1]) > int(got["titleWeight"]), got["bold"]
    bold_width, title_width = got["probe"]
    assert bold_width > title_width + 4, got["probe"]
    # a [[term]] keeps the title's weight: its emphasis is B2's cue.term (cues.js), never a template style (§4.8)
    assert [k[0] for k in got["term"]] == ["current"] and got["term"][0][1] == got["titleWeight"], got["term"]
    assert [c[0] for c in got["code"]] == ["I"] and "JetBrains Mono" in got["code"][0][1]
    assert len(got["words"]) == 11 and all(w["opacity"] == 1 for w in got["words"])
    title = next(b for b in boxes if b.get("role") == "title")
    assert title["text"] == RICH_PLAIN and title["overflow"] == 0 and title["critical"] is True
    # every word inside the title box (no TeX running out of the column)
    right = got["title"]["x"] + got["title"]["w"]
    assert all(w["rect"]["x"] + w["rect"]["w"] <= right + 1 for w in got["words"])


@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
async def test_the_countdown_ring_never_overlaps_the_question_or_kicker(real_app, fmt: str) -> None:
    served = _timeline(fmt, LONG, title=LONG_KICKER)
    boxes, got = await _at(real_app, served, COUNTDOWN_AT + 1.0, _RING_JS)
    assert got["visible"] == "visible" and got["number"] == "4"
    ring = got["ring"]
    assert ring["w"] == 96 and ring["h"] == 96
    for word in got["words"]:
        assert not _overlap(ring, word), (ring, word)
    assert not _overlap(ring, got["kicker"]), (ring, got["kicker"])
    for card in got["cards"]:
        assert not _overlap(ring, card), (ring, card)
    if fmt == "landscape":
        assert got["block"]["x"] + got["block"]["w"] <= ring["x"] - 24 + 0.5, (got["block"], ring)
        assert ring["x"] + ring["w"] <= 1824 + 0.5, "inside the content box (R-59)"
    else:
        assert ring["y"] >= got["block"]["y"] + got["block"]["h"], "below the question"
        assert ring["x"] + ring["w"] / 2 < 888, "its number left of the 9:16 safe edge"
        assert ring["y"] + ring["h"] <= 840, "above the band beside the action rail (x > 780 below y 840)"
    label = next(b for b in boxes if b.get("slot") == "ring")
    size = 40 if fmt == "landscape" else 44
    assert (label["role"], label["critical"], label["text"], label["size"]) == ("label", False, "4", size)
    assert got["numberSize"] == f"{size}px", "drawn at the R-29 label size it reports"
    assert label["x"] == round(ring["x"]) and label["w"] == round(ring["w"])


def _chapter_card(bg_tint: str) -> dict[str, Any]:
    """A chapter card (its "Part 2" kicker is critical) on mesh_drift; ``bg_tint`` is one of the background's colours."""
    served = _timeline("landscape", RICH)
    scene = copy.deepcopy(served["scenes"][0])
    del scene["quiz"]
    scene.update(scene_id="ch", type="chapter_card", title="The law", chapter_label="Part 2", board=[], beats=[],
                 duration=4.0)
    scene["look"]["tokens"]["bg-tint"] = bg_tint
    served.update(scenes=[scene], total_duration=4.0)
    served["presentation"]["generated_seconds"] = 4.0
    return served


async def test_a_kicker_scrim_remedy_is_drawn_and_reported_fixed(real_app) -> None:
    # ink2 (#B9BEC7) on the lightest background colour is 2.5:1, under the 3:1 a 40 px label needs (R-53); the title's
    # ink holds 4.3:1 there, so only the kicker is remedied, and only its own slot asks for the scrim
    served = _chapter_card("#6E7385")
    res = await run_preflight(real_app.settings, Timeline.model_validate(served), token=TOKEN, served_timeline=served,
                              base_url=real_app.base)
    assert [(r.kind, r.scene_index, r.slot) for r in res.remedies] == [("scrim", 0, "kicker")]
    assert res.rounds == 2 and res.ok
    items = [(i["check"], i["slot"], i["status"]) for i in res.items()]
    assert items == [("contrast", "kicker", "fixed")], items
    assert res.timeline["scenes"][0]["look"]["text_fit"] == {"kicker": {"scrim": True}}
    # what the second pass saw: the kicker on the drawn scrim, measured from the colour the stage reports
    boxes, block = await _at(real_app, res.timeline, 2.0, """() => {
      const b = document.querySelector('.dyn-titleblock');
      const r = b.getBoundingClientRect();
      return { scrim: b.classList.contains('has-scrim'), background: getComputedStyle(b).backgroundColor,
               centre: r.left + r.width / 2 };
    }""")
    assert block["scrim"] and block["background"] != "rgba(0, 0, 0, 0)", block
    assert abs(block["centre"] - 960) <= 0.5, "the centred block's scrim keeps the frame's centre"
    kicker = next(b for b in boxes if b.get("slot") == "kicker")
    assert kicker["critical"] is True and text_contrast(kicker["ink"], kicker["bg"]) >= 3.0, kicker
