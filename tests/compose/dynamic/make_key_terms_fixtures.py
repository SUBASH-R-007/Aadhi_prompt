"""The ``key_terms`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says where they live and what
makes one valid), generated here so every value in them is the resolver's own (presentation tokens, fonts, the
personality's motion scale and text entrance, the scene tokens of a palette slot, the stored icons) and the files can
be rebuilt and checked byte for byte::

    python -m tests.compose.dynamic.make_key_terms_fixtures           # write the files
    python -m tests.compose.dynamic.make_key_terms_fixtures --check   # exit 1 when a file is stale

Four timelines cover the cases the B2 plan §G gallery names for this template, in both formats, in a dark and a light
family each, with Poppins as the display face in one dark and one light timeline (and Anek for the Indic text there):

* ``key_terms.landscape.json`` (Deep Teal / Crisp: ``baseline_reveal``, x0.85, marker cue): the typical row of three
  term cards, each with a stored icon, a ``cue_term`` on a term and on a word of a meaning and a ``draw`` event that
  moves one icon's stroke draw off its card's entrance; then, entered by a crossfade, the stress row of four cards
  (the lint-limit meaning in wide capitals, one word longer than a card is wide, TeX inside a term and beside text,
  and two cards without an icon).
* ``key_terms.landscape.tamil.json`` (Paper & Ink / Crisp, light, ta-IN, Poppins + Anek): a Tamil row of three terms
  (``cue_term``, ``draw``), then (crossfade) a mixed Tamil + English + TeX row of two.
* ``key_terms.portrait.json`` (Midnight Indigo / Soft: ``fade_rise``, x1.15, underline cue, Poppins): the 9:16 stack
  of three terms with inline icons, a ``cue_term`` and a ``draw``.
* ``key_terms.portrait.hindi.json`` (Mint Studio / Soft, light, hi-IN): a Hindi stack of three terms, then
  (crossfade) the minimal case: two terms and no heading.

Each file is at most about 10.4 s, so the slow 1x / 3x / reverse gate's first 300 frames see every scene and every
crossfade; term beats are therefore shorter than §4.3's 3-4 s per term. The 9:16 meanings are short (<= 6 words):
four terms with full 8-word meanings do not fit a reel's text column at R-29's floors (key_terms.js's header), and
§4.3 calls the template optional in 9:16. The narration is placeholder text, never the terms verbatim (R-25)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aadhi.compose import presentation as pr
from aadhi.compose.dynamic import faces, icons, themes
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR

TEMPLATE = "key_terms"
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
LANGS = {"en": "en-IN", "ta": "ta-IN", "hi": "hi-IN"}

# The stress texts: the lint-limit meaning (R-25: <= 8 words per item) in wide capitals, a word longer than a 414 px
# four-term card is wide even at the R-29 floor, and TeX inside a [[term]] and beside text.
WIDE = "MAXIMUM WATTAGE WHEN WARM MOTORS MEET HEAVY LOADS"
LONG_WORD = "Pneumonoultramicroscopicsilicovolcanoconiosis is one word"


def _presentation(fmt: str, family_id: str, personality: str, cue_style: str, display: str) -> dict[str, Any]:
    """The resolver's presentation (``timeline_presentation``'s fields) for a family and personality. ``display``
    other than the family's own: the B2 fonts form naming that display face (and ``anek`` Indic faces with
    ``poppins``), which the resolver writes from B2e (the gallery shows Poppins in a dark and in a light family,
    plan §G)."""
    family = pr.family_for(family_id)
    fonts = faces.resolver_fonts(display)
    if display != fonts["display"]:
        fonts = {**fonts, "display": display, "indic": "anek"}
    return {
        "style": "dynamic", "format": fmt, "safe_zone": "universal", "content": pr.content_tokens(family.mode),
        "fonts": fonts, "personality": personality, "caption_style": "clean_pill",
        "cue_term_style": cue_style, "captions_burn": True, "sfx": False, "motion": "standard",
        "generated_seconds": 0.0, "engine_version": pr.ENGINE_VERSION,
        "content_alt": themes.content_alt_tokens(family.mode), "family": family.id, "mode": family.mode,
        "motion_scale": pr.motion_scale(personality, family), "text_entrance": pr.text_entrance(personality, family),
    }


def _heading(item_id: str, text: str) -> dict[str, Any]:
    return {"id": item_id, "kind": "heading", "text": text}


def _term(item_id: str, term: str, meaning: str, hint: str | None = None) -> dict[str, Any]:
    """One ``definition`` board item: the term, its meaning and the icon hint the resolver matched (§4.3 key_terms)."""
    out: dict[str, Any] = {"id": item_id, "kind": "definition", "term": term, "text": meaning}
    if hint is not None:
        out["icon_hint"] = hint
    return out


def _beat(sid: str, k: int, start: float, length: float, text: str, item: str | None) -> dict[str, Any]:
    speech_end = round(start + length - 0.4, 3)
    return {
        "beat_id": f"{sid}-{k}", "index": k, "phase": "main", "start": round(start, 3), "speech_end": speech_end,
        "end": round(start + length, 3), "narration": text, "board_item_id": item,
        "captions": [{"start": round(start, 3), "end": speech_end, "text": text}],
    }


def _scene(spec: dict[str, Any], *, index: int, start: float, family_id: str, slot: int,
           transition: str) -> dict[str, Any]:
    """One timed scene: ``spec`` = {id, title, items, beats: [(item | None, narration)], beat_len, events}."""
    sid = spec["id"]
    beats = []
    t = 0.5
    for k, (item, text) in enumerate(spec["beats"]):
        beats.append(_beat(sid, k, t, spec["beat_len"], text, item))
        t += spec["beat_len"]
    duration = round(t + 0.8, 3)
    family = pr.family_for(family_id)
    look_icons = icons.scene_icons(spec["items"])
    seconds = pr.crossfade_seconds(duration) if transition == "crossfade" else 0.0
    return {
        "scene_id": sid, "index": index, "type": "content", "title": spec["title"], "start": round(start, 3),
        "duration": duration, "audio_offset": 0.5, "audio_duration": round(t - 0.5, 3),
        "board": spec["items"], "beats": beats, "events": spec.get("events", []),
        "look": {
            "template": TEMPLATE, "tokens": themes.scene_tokens(family, slot),
            "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": 7000 + index,
                           "frozen_at": None},
            "transition_in": {"kind": transition, "seconds": seconds},
            "icons": {k: v.model_dump(mode="json") for k, v in look_icons.items()},
        },
    }


def _timeline(fmt: str, lang: str, family_id: str, personality: str, cue_style: str, display: str,
              specs: list[dict[str, Any]]) -> Timeline:
    scenes = []
    start = 0.0
    for i, spec in enumerate(specs):
        scene = _scene(spec, index=i, start=start, family_id=family_id, slot=i,
                       transition="cut" if i == 0 else "crossfade")
        scenes.append(scene)
        start = round(start + scene["duration"], 3)
    pres = _presentation(fmt, family_id, personality, cue_style, display)
    pres["generated_seconds"] = start
    width, height = SIZES[fmt]
    captions = [{"start": round(s["start"] + c["start"], 3), "end": round(s["start"] + c["end"], 3), "text": c["text"]}
                for s in scenes for b in s["beats"] for c in b["captions"]]
    data = {
        "schema_version": 3, "language": LANGS[lang], "board_language": LANGS[lang], "fps": 30, "width": width,
        "height": height, "meta": {"subject_name": "Basic Electrical Engineering", "unit_name": "Unit 2: DC Circuits",
                                   "session_number": "Session 3", "session_title": "Circuits"},
        "total_duration": start, "chapters": [{"start": s["start"], "title": s["title"]} for s in scenes],
        "captions": captions, "presentation": pres, "scenes": scenes,
    }
    return Timeline.model_validate(data)


def _cue(t: float, kind: str, target: str, dur: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"t": t, "kind": kind, "target": target, "anchored": True}
    if dur is not None:
        out["dur"] = dur
    return out


def _english(sid: str, beat_len: float) -> dict[str, Any]:
    return {
        "id": sid, "title": "Three words to know first", "beat_len": beat_len,
        "items": [
            _heading("h1", "Three words to know first"),
            _term("d1", "[[Voltage]]", "The push that moves charge", "zap"),
            _term("d2", "Current", "The [[flow]] of charge each second", "activity"),
            _term("d3", "Resistance", "How much a path resists flow", "shield"),
        ],
        "beats": [("d1", "First, the push itself."), ("d2", "Then what the push sets moving."),
                  ("d3", "And what holds it back.")],
    }


def _stress(sid: str, beat_len: float) -> dict[str, Any]:
    """Four cards: the lint-limit meaning in wide capitals, one over-long word, TeX in a term and beside text."""
    return {
        "id": sid, "title": "Stress terms", "beat_len": beat_len,
        "items": [
            _term("s1", "WIDE WATTAGE WORDS", WIDE, "ruler"),
            _term("s2", "Long word", LONG_WORD),
            _term("s3", "[[Ohm's law]]", "It says $V = I R$ holds"),
            _term("s4", "Unit", "Resistance is measured in ohm $\\Omega$"),
        ],
        "beats": [("s1", "The longest meaning a lint allows."), ("s2", "One word longer than a card."),
                  ("s3", "A formula inside the text."), ("s4", "And a unit symbol.")],
    }


def _tamil(sid: str, beat_len: float) -> dict[str, Any]:
    return {
        "id": sid, "title": "முதலில் தெரிய வேண்டியவை", "beat_len": beat_len,
        "items": [
            _heading("h1", "முதலில் தெரிய வேண்டியவை"),
            _term("t1", "[[மின்னழுத்தம்]]", "மின்னூட்டத்தைத் தள்ளும் விசை", "zap"),
            _term("t2", "மின்னோட்டம்", "ஒரு வினாடியில் [[ஓட்டம்]]"),
            _term("t3", "மின்தடை", "பாதை எதிர்க்கும் அளவு", "shield"),
        ],
        "beats": [("t1", "முதலில் தள்ளுதல்."), ("t2", "பிறகு ஓட்டம்."), ("t3", "இறுதியாக எதிர்ப்பு.")],
    }


def _mixed(sid: str, beat_len: float) -> dict[str, Any]:
    """Tamil, English terms and TeX in one row (plan §G "mixed Tamil + English term + $TeX$")."""
    return {
        "id": sid, "title": "ஓம் விதி: Ohm's law", "beat_len": beat_len,
        "items": [
            _heading("h1", "ஓம் விதி: Ohm's law"),
            _term("m1", "Voltage", "மின்னழுத்தம் என்பது $V$", "zap"),
            _term("m2", "[[Current]]", "மின்னோட்டம் $I = V/R$"),
        ],
        "beats": [("m1", "மின்னழுத்தம் என்றால் என்ன?"), ("m2", "மின்னோட்டம் இதைப் பொறுத்தது.")],
    }


def _portrait(sid: str, beat_len: float) -> dict[str, Any]:
    """The 9:16 stack: short meanings (<= 6 words), as a reel's text column holds them."""
    return {
        "id": sid, "title": "Three words first", "beat_len": beat_len,
        "items": [
            _heading("h1", "Three words first"),
            _term("p1", "[[Voltage]]", "The push on charge", "zap"),
            _term("p2", "Current", "The [[flow]] of charge", "activity"),
            _term("p3", "Resistance", "What slows the flow", "shield"),
        ],
        "beats": [("p1", "First, the push."), ("p2", "Then the flow."), ("p3", "And what slows it.")],
    }


def _hindi(sid: str, beat_len: float) -> dict[str, Any]:
    return {
        "id": sid, "title": "पहले ये शब्द", "beat_len": beat_len,
        "items": [
            _heading("h1", "पहले ये शब्द"),
            _term("k1", "[[वोल्टेज]]", "आवेश को धकेलने वाला बल", "zap"),
            _term("k2", "धारा", "आवेश का [[बहाव]]"),
            _term("k3", "प्रतिरोध", "बहाव का विरोध", "shield"),
        ],
        "beats": [("k1", "पहले धक्का।"), ("k2", "फिर बहाव।"), ("k3", "और विरोध।")],
    }


def _minimal(sid: str, beat_len: float) -> dict[str, Any]:
    """The minimal case: two terms and no heading (§4.3: 2-4 terms)."""
    return {
        "id": sid, "title": "Two more words", "beat_len": beat_len,
        "items": [_term("n1", "Energy", "Work the loop does", "battery"),
                  _term("n2", "[[Power]]", "Energy each second")],
        "beats": [("n1", "Work gets done."), ("n2", "And how fast.")],
    }


def fixtures() -> dict[str, Timeline]:
    """Every key_terms fixture by file name. Each fits the slow gate's 300 frames (10 s; the longest, 10.4 s, barely
    passes them), so the gate sees every scene and every crossfade."""
    land = _english("kt-l-typical", 1.6)  # 6.3 s
    # the term cue on a term field, then on a word of a meaning; the draw event moves d3's icon off its entrance
    land["events"] = [_cue(1.0, "cue_term", "d1", 1.2), _cue(2.6, "cue_term", "d2", 1.2), _cue(5.0, "draw", "d3")]
    land_stress = _stress("kt-l-stress", 0.7)  # 4.1 s, entered by a crossfade
    land_stress["events"] = [_cue(2.3, "cue_term", "s3", 1.0)]

    tamil = _tamil("kt-ta-row", 1.4)  # 5.5 s
    tamil["events"] = [_cue(0.9, "cue_term", "t1", 1.2), _cue(3.4, "draw", "t3")]
    mixed = _mixed("kt-ta-mixed", 1.5)  # 4.3 s
    mixed["events"] = [_cue(2.3, "cue_term", "m2", 1.0)]

    port = _portrait("kt-p-stack", 2.0)  # 7.3 s
    port["events"] = [_cue(1.0, "cue_term", "p1", 1.3), _cue(5.0, "draw", "p3")]

    hindi = _hindi("kt-hi-stack", 1.6)  # 6.3 s
    hindi["events"] = [_cue(0.9, "cue_term", "k1", 1.2)]
    minimal = _minimal("kt-hi-minimal", 1.4)  # 4.1 s
    minimal["events"] = [_cue(2.0, "cue_term", "n2", 1.0)]
    return {
        f"{TEMPLATE}.landscape.json": _timeline("landscape", "en", "deep_teal", "crisp", "marker", "outfit",
                                                [land, land_stress]),
        f"{TEMPLATE}.landscape.tamil.json": _timeline("landscape", "ta", "paper_ink", "crisp", "marker", "poppins",
                                                      [tamil, mixed]),
        f"{TEMPLATE}.portrait.json": _timeline("portrait", "en", "midnight_indigo", "soft", "underline", "poppins",
                                               [port]),
        f"{TEMPLATE}.portrait.hindi.json": _timeline("portrait", "hi", "mint_studio", "soft", "marker", "outfit",
                                                     [hindi, minimal]),
    }


def render(tl: Timeline) -> str:
    """The file text of a fixture (``Timeline.model_dump(mode="json")``, sorted keys, 1-space indent, final newline)."""
    return json.dumps(tl.model_dump(mode="json"), ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def files() -> dict[Path, str]:
    """Every fixture's path and its exact text."""
    return {FIXTURE_DIR / name: render(tl) for name, tl in fixtures().items()}


def stale(directory: Path = FIXTURE_DIR) -> list[str]:
    """The fixture files that are missing or differ from what :func:`fixtures` builds."""
    out = []
    for name, tl in fixtures().items():
        path = directory / name
        if not path.is_file() or path.read_text("utf-8") != render(tl):
            out.append(name)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when a fixture file is stale")
    args = parser.parse_args(argv)
    if args.check:
        bad = stale()
        for name in bad:
            print(f"stale: {name} (run python -m tests.compose.dynamic.make_key_terms_fixtures)", file=sys.stderr)
        return 1 if bad else 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, tl in fixtures().items():
        (FIXTURE_DIR / name).write_text(render(tl), "utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
