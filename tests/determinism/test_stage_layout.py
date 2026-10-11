"""``board_generic`` layout on the real render page (real Chromium layout, real fonts; DYNAMIC_VIDEO_DESIGN §4.6, R-29):
what jsdom cannot measure.

* a crowded 9:16 quiz: every option is kept until the reveal, so a long question can leave too little room; the
  stack first tightens (``is-compact``), and what still does not fit is reported as overflow (preflight then blocks
  with "Shorten text") instead of being drawn into the caption band with ``overflow: 0``;
* the title fitter never accepts a word broken inside (``overflow-wrap: anywhere``) when a smaller size keeps it whole;
* 9:16 card text is weight >= 600 and labels are >= 44 px (>= 40 px in 16:9).
"""

from __future__ import annotations

from typing import Any

import pytest

from aadhi.compose.dynamic.stage_page import open_stage

pytestmark = pytest.mark.slow
TOKEN = "layout.check"
TOKENS = {"struct.accent": "#FF936D", "bg": "#10132C", "bg-alt": "#181D37", "bg-tint": "#22121C", "surface": "#252A3F"}
QUESTION = ("A circuit runs on a 12 V battery. We swap it for a 24 V battery, keeping the same resistance. What happens "
            "to the current?")
OPTIONS = ["It stays the same", "It doubles", "It halves", "It gets used up faster"]


def _look(preset: str = "mesh_drift", seed: int = 7) -> dict[str, Any]:
    return {"template": "board_generic", "tokens": dict(TOKENS), "background": {"preset": preset, "params": {"tone": "bg"},
            "seed": seed, "frozen_at": None}, "transition_in": {"kind": "cut", "seconds": 0}}


def _beat(beat_id: str, start: float, end: float, text: str, **extra: Any) -> dict[str, Any]:
    return {"beat_id": beat_id, "index": 0, "start": start, "speech_end": end, "end": end, "narration": text,
            "captions": [{"start": start, "end": end, "text": text}], **extra}


def _timeline(fmt: str, scene: dict[str, Any], *, language: str = "en-IN") -> dict[str, Any]:
    portrait = fmt == "portrait"
    duration = float(scene["duration"])
    scene = {"index": 0, "start": 0, "audio_offset": 0.5, "look": _look(), **scene}
    return {
        "schema_version": 3, "language": language, "board_language": language, "fps": 30,
        "width": 1080 if portrait else 1920, "height": 1920 if portrait else 1080, "total_duration": duration,
        "chapters": [{"start": 0, "title": "Basics"}], "captions": [], "transition_seconds": 0.4, "scenes": [scene],
        "presentation": {
            "style": "dynamic", "format": fmt, "safe_zone": "universal",
            "content": {"ink": "#F3F5F9", "ink2": "#B9BEC7", "entity:0": "#0FE3FE", "entity:1": "#C4A4FE",
                        "entity:2": "#FEACE3", "warn": "#F8A87F", "ok": "#5DD8AA", "mark": "#F2D935", "focus": "#F2D935"},
            "fonts": {"display": "outfit", "body": "inter", "mono": "jetbrains_mono"}, "personality": "crisp",
            "caption_style": "clean_pill", "cue_term_style": "underline", "captions_burn": False, "sfx": False,
            "motion": "standard", "generated_seconds": duration, "engine_version": 1,
        },
    }


def _quiz(options: list[str]) -> dict[str, Any]:
    return {"scene_id": "q", "type": "quiz_checkpoint", "title": "Checkpoint", "duration": 12.0,
            "beats": [_beat("q1", 0.5, 2.5, "What happens to the current?")],
            "quiz": {"question": QUESTION, "options": options, "correct_index": 1, "feedback_wrong": ["", "", "", ""],
                     "explanation": "I = V / R, so doubling the voltage doubles the current.", "countdown_start": 3,
                     "countdown_seconds": 5, "reveal_start": 8}}


_CARDS_JS = """() => [...document.querySelectorAll('.dyn-card')].map((e) => {
  const r = e.getBoundingClientRect();
  return { id: e.dataset.item, top: r.top, bottom: r.bottom, opacity: Number(e.style.opacity || '0'),
           compact: e.parentElement.classList.contains('is-compact') };
})"""


async def _boxes(real_app, served: dict[str, Any], frame: int, js: str | None = None) -> tuple[list[dict], Any]:
    async with open_stage(real_app.settings, size=(served["width"], served["height"]), token=TOKEN,
                          base_url=real_app.base, served_timeline=served) as page:
        await page.seek(frame)
        boxes = await page.text_boxes()
        extra = await page.page.evaluate(js) if js else None
        assert page.errors == []
    return boxes, extra


async def test_a_crowded_portrait_quiz_tightens_to_fit_and_never_draws_into_the_caption_band(real_app) -> None:
    boxes, cards = await _boxes(real_app, _timeline("portrait", _quiz(OPTIONS)), 60, _CARDS_JS)
    options = [b for b in boxes if b["role"] == "body"]
    assert len(options) == 4 and all(b["overflow"] == 0 for b in options)
    shown = [c for c in cards if c["id"].startswith("option") and c["opacity"] > 0.99]
    assert len(shown) == 4 and shown[0]["compact"], "the long question leaves too little room: compact cards"
    assert max(c["bottom"] for c in shown) <= 1020 + 1, "every option above the 9:16 body bottom (y 1020)"


async def test_options_that_cannot_fit_report_overflow_for_preflight(real_app) -> None:
    long = [OPTIONS[0], "It doubles, because current follows voltage in every circuit we build", *OPTIONS[2:]]
    boxes, cards = await _boxes(real_app, _timeline("portrait", _quiz(long)), 60, _CARDS_JS)
    options = [b for b in boxes if b["role"] == "body"]
    assert len(options) == 4
    assert options[-1]["overflow"] > 0, "the last option runs past the column: overflow, not 0"
    assert options[0]["overflow"] == 0 and options[-1]["size"] == options[-1]["min"]  # at its minimum size
    # a short quiz still reports nothing
    short = {**_quiz(["Yes", "No", "Maybe", "Never"]), "quiz": {**_quiz(["Yes", "No", "Maybe", "Never"])["quiz"],
                                                               "question": "Does current double?"}}
    boxes, cards = await _boxes(real_app, _timeline("portrait", short), 60, _CARDS_JS)
    assert all(b["overflow"] == 0 for b in boxes if b["role"] == "body")
    assert not any(c["compact"] for c in cards), "room to spare: the regular cards"


_WORDS_JS = """() => {
  const t = document.querySelector('.dyn-title');
  const size = parseFloat(getComputedStyle(t).fontSize);
  const lh = parseFloat(getComputedStyle(t).lineHeight) / size;
  return { size, lh, words: [...t.querySelectorAll('.dyn-word')].map((w) => ({ text: w.textContent, h: w.offsetHeight })) };
}"""


def _chapter(title: str) -> dict[str, Any]:
    return {"scene_id": "c", "type": "chapter_card", "title": title, "chapter_label": "Part 2", "subtitle": "",
            "duration": 4.0, "board": [], "beats": [], "look": _look("colour_block", 202)}


@pytest.mark.parametrize("fmt", ["portrait", "landscape"])
async def test_the_title_fitter_keeps_words_whole(real_app, fmt: str) -> None:
    _, got = await _boxes(real_app, _timeline(fmt, _chapter("Electromagnetism Fundamentals")), 90, _WORDS_JS)
    assert all(w["h"] <= 1.5 * got["size"] * got["lh"] for w in got["words"]), got
    if fmt == "portrait":
        assert got["size"] <= 88, got  # 112 px would break "Electromagnetism" inside the word


async def test_a_word_wider_than_the_column_may_break_but_the_others_stay_whole(real_app) -> None:
    served = _timeline("portrait", _chapter("மின்னோட்டம் கண்டுபிடிக்கப்பட்டது"), language="ta-IN")
    boxes, got = await _boxes(real_app, served, 90, _WORDS_JS)
    first = got["words"][0]
    assert first["h"] <= 1.5 * got["size"] * got["lh"], got  # the first word is whole
    title = next(b for b in boxes if b["role"] == "title")
    assert title["overflow"] == 0  # an unavoidable break inside a word is not overflow (preflight would block it)


_STYLES_JS = """() => {
  const cs = (sel) => { const e = document.querySelector(sel); return e ? getComputedStyle(e) : null; };
  const text = cs('.dyn-card--bullet .dyn-i-text');
  const label = cs('.dyn-card--callout_tip .dyn-i-label');
  const chip = cs('.dyn-chip');
  return { textWeight: text && Number(text.fontWeight), labelSize: label && parseFloat(label.fontSize),
           chipSize: chip && parseFloat(chip.fontSize), chip: document.querySelector('.dyn-chip').textContent };
}"""


def _board() -> dict[str, Any]:
    long = "with enough words to wrap over several lines of the column at any size"
    return {"scene_id": "b", "type": "content", "title": "Voltage and current", "duration": 10.0,
            "board": [{"id": "b1", "kind": "heading", "text": "Charge flows like water"},
                      {"id": "b2", "kind": "bullet", "text": f"Voltage is the push {long}"},
                      {"id": "b3", "kind": "definition", "term": "Current", "text": f"The rate of flow {long}"},
                      {"id": "b4", "kind": "bullet", "text": f"Resistance resists {long}"},
                      {"id": "b5", "kind": "callout_tip", "text": f"Units matter {long}"}],
            "beats": [_beat("k1", 0.5, 2, "Voltage.", board_item_id="b2"), _beat("k2", 2.2, 4, "Current.", board_item_id="b3"),
                      _beat("k3", 4.2, 6, "Resistance.", board_item_id="b4"),
                      _beat("k4", 6.2, 8, "Units.", board_item_id="b5")]}


@pytest.mark.parametrize("fmt", ["portrait", "landscape"])
async def test_card_text_weight_and_label_sizes_follow_r29(real_app, fmt: str) -> None:
    boxes, got = await _boxes(real_app, _timeline(fmt, _board()), 285, _STYLES_JS)
    floor = 44 if fmt == "portrait" else 40
    assert got["labelSize"] is not None and got["labelSize"] >= floor - 0.01, got
    assert got["chip"] and got["chipSize"] >= floor - 0.01, got
    if fmt == "portrait":
        assert got["textWeight"] >= 600, got  # R-29: 9:16 text at <= 64 px is weight >= 600
    body = [b for b in boxes if b["role"] == "body"]
    assert body and all(b["weight"] == (600 if fmt == "portrait" else 400) for b in body)
    chip = next(b for b in boxes if b["role"] == "chip")
    assert chip["size"] == floor
