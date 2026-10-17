"""The ``myth_fact`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says where they live and what
makes one valid), generated here so every value in them is the resolver's own (presentation tokens, fonts, the
personality's motion scale and text entrance, the scene tokens of a palette slot) and the files can be rebuilt and
checked byte for byte::

    python -m tests.compose.dynamic.make_myth_fact_fixtures           # write the files
    python -m tests.compose.dynamic.make_myth_fact_fixtures --check   # exit 1 when a file is stale

Four timelines cover the cases the B2 plan §G gallery names for this template, in both formats, in a dark and a light
family each, with Poppins (and Anek for the Indic text) as the display face in one timeline per format:

* ``myth_fact.landscape.json`` (Deep Teal / Crisp: ``baseline_reveal``, x0.85, marker cue): the showcase lecture's s5
  shape (owner decision 3, 2026-10-16) - two definitions drawn as supporting lines, then the misconception, beside an
  image panel (a flat-colour PNG embedded as a ``data:`` URL, larger than its box: pictures only ever downscale, §P);
  a ``cue_term`` on a supporting term, a ``look_here`` on the panel and a ``cue_term`` on a term of the correction
  (``<item>:term:<n>`` counted over the belief, then the correction), the strike timed from the beat's narration;
  then, entered by a crossfade, the stress card: TeX in the belief and the correction, one over-long word, the
  refutation timed by a ``draw`` event.
* ``myth_fact.landscape.tamil.json`` (Paper & Ink / Soft: ``fade_rise``, x1.15, underline cue, ta-IN, Poppins +
  Anek): a Tamil heading, one supporting definition in Tamil + English + TeX and a Tamil misconception; then
  (crossfade) a misconception with a terminal panel and no supporting line, so the panel spans the grid box, and a
  ``look_here`` on an output row.
* ``myth_fact.portrait.json`` (Midnight Indigo / Soft, underline): the reel card pair, the fact timed by an ``answer``
  event, a ``cue_term`` on the correction's term and a ``look_here`` on the card.
* ``myth_fact.portrait.hindi.json`` (Mint Studio / Crisp, marker, hi-IN, Poppins): a Hindi misconception; then
  (crossfade) the Latin stress pair at the reel caps (templates.py ``MYTH_FACT_WORDS``) in wide capitals.

Each file is at most about 10 s, so the slow 1x / 3x / reverse gate's first 300 frames see every scene and every
crossfade; the refutations are therefore quicker than §4.3's 8-14 s. Every scene is one ``_fit_myth_fact`` takes
(the shape and the caps, ``test_myth_fact_fixtures.py``). The narration is placeholder text, never the board text
verbatim (R-25)."""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from aadhi.compose import presentation as pr
from aadhi.compose.dynamic import faces, themes
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR

TEMPLATE = "myth_fact"
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
LANGS = {"en": "en-IN", "ta": "ta-IN", "hi": "hi-IN"}

Obj = dict[str, Any]

# The stress texts: wide capitals, a word longer than the myth card is wide even at the R-29 floor, TeX in both fields.
LONG_WORD = "Pneumonoultramicroscopicsilicovolcanoconiosis"


def picture(w: int, h: int) -> str:
    """A flat-colour water pump pushing water round a loop of pipe (the s5 panel's subject) as a ``data:`` PNG URL:
    no antialiasing, so it is small and byte-stable."""
    im = Image.new("RGB", (w, h), (22, 44, 70))
    d = ImageDraw.Draw(im)
    m = min(w, h)
    lw = max(8, m // 40)
    x0, y0, x1, y1 = w * 0.16, h * 0.2, w * 0.84, h * 0.8
    d.rectangle((x0, y0, x1, y1), outline=(120, 200, 250), width=lw)
    cx, cy, r = w * 0.16, h * 0.5, m * 0.12
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(240, 160, 60))
    d.rectangle((cx - r * 0.25, cy - r * 0.9, cx + r * 0.25, cy + r * 0.9), fill=(250, 220, 120))
    for k in range(6):
        px = x0 + (x1 - x0) * (0.12 + 0.15 * k)
        d.ellipse((px - m * 0.025, y0 - m * 0.025, px + m * 0.025, y0 + m * 0.025), fill=(200, 240, 255))
    gx, gy, gr = w * 0.62, h * 0.8, m * 0.07
    d.ellipse((gx - gr, gy - gr, gx + gr, gy + gr), fill=(230, 236, 244))
    d.line((gx, gy, gx + gr * 0.7, gy - gr * 0.5), fill=(200, 60, 60), width=max(3, lw // 2))
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _presentation(fmt: str, family_id: str, personality: str, cue_style: str, display: str) -> Obj:
    """The resolver's presentation (``timeline_presentation``'s fields) for a family and personality. ``display``
    other than the family's own: the B2 fonts form naming that display face (and ``anek`` Indic faces with
    ``poppins``), as the resolver writes it from B2e (the gallery shows Poppins in a dark and a light family, §G)."""
    family = pr.family_for(family_id)
    fonts = faces.resolver_fonts(display)
    if display != fonts["display"]:
        fonts = {**fonts, "display": display, "indic": "anek"}
    return {
        "style": "dynamic", "format": fmt, "safe_zone": "universal", "content": pr.content_tokens(family.mode),
        "fonts": fonts, "personality": personality, "caption_style": "bold_outline" if fmt == "portrait" else "clean_pill",
        "cue_term_style": cue_style, "captions_burn": True, "sfx": False, "motion": "standard",
        "generated_seconds": 0.0, "engine_version": pr.ENGINE_VERSION,
        "content_alt": themes.content_alt_tokens(family.mode), "family": family.id, "mode": family.mode,
        "motion_scale": pr.motion_scale(personality, family), "text_entrance": pr.text_entrance(personality, family),
    }


def _heading(item_id: str, text: str) -> Obj:
    return {"id": item_id, "kind": "heading", "text": text}


def _definition(item_id: str, term: str, meaning: str) -> Obj:
    return {"id": item_id, "kind": "definition", "term": term, "text": meaning}


def _myth(item_id: str, belief: str, correction: str) -> Obj:
    """One ``misconception`` board item: ``text`` the belief, ``justification`` its correction (§4.3 myth_fact)."""
    return {"id": item_id, "kind": "misconception", "text": belief, "justification": correction,
            "misconception_id": f"mc-{item_id}"}


def image_panel(title: str, url: str, w: int, h: int) -> Obj:
    return {"panel": {"kind": "image", "title": title, "rationale": "Shows the push and the flow.",
                      "image_prompt": "A water pump pushing water round a loop of pipe"},
            "media": {"kind": "image", "asset_key": "img-fixture", "url": url, "width": w, "height": h,
                      "fit": "contain"},
            "show_at": 0.0}


def terminal_panel() -> Obj:
    return {"panel": {"kind": "terminal", "title": "Measure it", "rationale": "Shows the same current twice.",
                      "terminal": {"command": "meter --at A --at B",
                                   "output": "A: I = 0.50 A\nB: I = 0.50 A"}},
            "media": None, "show_at": 0.3}


def _beat(sid: str, k: int, start: float, length: float, text: str, item: str | None) -> Obj:
    speech_end = round(start + length - 0.4, 3)
    return {
        "beat_id": f"{sid}-{k}", "index": k, "phase": "main", "start": round(start, 3), "speech_end": speech_end,
        "end": round(start + length, 3), "narration": text, "board_item_id": item,
        "captions": [{"start": round(start, 3), "end": speech_end, "text": text}],
    }


def _scene(spec: Obj, *, index: int, start: float, family_id: str, slot: int, transition: str) -> Obj:
    """One timed scene: ``spec`` = {id, title, items, beats: [(item | None, narration, seconds)], events, panel?}."""
    sid = spec["id"]
    beats = []
    t = 0.5
    for k, (item, text, length) in enumerate(spec["beats"]):
        beats.append(_beat(sid, k, t, length, text, item))
        t += length
    duration = round(t + 0.8, 3)
    family = pr.family_for(family_id)
    seconds = pr.crossfade_seconds(duration) if transition == "crossfade" else 0.0
    out: Obj = {
        "scene_id": sid, "index": index, "type": "content", "title": spec["title"], "start": round(start, 3),
        "duration": duration, "audio_offset": 0.5, "audio_duration": round(t - 0.5, 3),
        "board": spec["items"], "beats": beats, "events": spec.get("events", []),
        "look": {
            "template": TEMPLATE, "tokens": themes.scene_tokens(family, slot),
            "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": 9100 + index,
                           "frozen_at": None},
            "transition_in": {"kind": transition, "seconds": seconds},
        },
    }
    if spec.get("panel") is not None:
        out["side_panel"] = spec["panel"]
    return out


def _timeline(fmt: str, lang: str, family_id: str, personality: str, cue_style: str, display: str,
              specs: list[Obj]) -> Timeline:
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


def _cue(t: float, kind: str, target: str, dur: float | None = None) -> Obj:
    out: Obj = {"t": t, "kind": kind, "target": target, "anchored": True}
    if dur is not None:
        out["dur"] = dur
    return out


def _showcase(sid: str, url: str) -> Obj:
    """The showcase lecture's s5 (tests/core/fixtures/showcase_ohms_law.json, its text as the screenplay holds it):
    two definitions, then the misconception, beside the pump picture."""
    return {
        "id": sid, "title": "VOLTAGE AND CURRENT",
        "items": [
            _definition("d1", "Voltage (V)",
                        "The electrical [[pressure]] that pushes charge through a circuit, measured in volts."),
            _definition("d2", "Current (I)", "The rate of flow of charge, measured in amperes."),
            _myth("m1", "Students often think current gets \"used up\" as it flows around a circuit",
                  "The SAME current returns to the source, because [[charge is conserved]]. It is ENERGY that gets "
                  "delivered."),
        ],
        "beats": [("d1", "First, what pushes.", 1.3), ("d2", "Then what flows.", 1.3),
                  ("m1", "And careful here. The same current comes home.", 2.9)],
        "panel": image_panel("A pump station pushing water", url, 1600, 900),
    }


def _stress(sid: str) -> Obj:
    """TeX in the belief and in the correction, one over-long word; the strike timed by a ``draw`` event."""
    return {
        "id": sid, "title": "Stress myth",
        "items": [_myth("m2", "A bigger $R$ always makes the bulb BRIGHTER",
                        f"{LONG_WORD} aside: at fixed $V$, $I = V/R$ falls, so the bulb dims")],
        "beats": [("m2", "A bigger resistor, a brighter bulb?", 2.2)],
    }


def _tamil(sid: str) -> Obj:
    return {
        "id": sid, "title": "தவறான நம்பிக்கை",
        "items": [
            _heading("h1", "தவறான நம்பிக்கை"),
            _definition("t1", "[[Voltage]]", "மின்னூட்டத்தைத் தள்ளும் விசை $V$"),
            _myth("t2", "மின்னோட்டம் சுற்றில் செலவாகிவிடும்",
                  "அதே மின்னோட்டம் திரும்புகிறது; செலவாவது [[ஆற்றல்]] மட்டுமே"),
        ],
        "beats": [("h1", "இதைப் பாருங்கள்.", 0.9), ("t1", "முதலில் தள்ளும் விசை.", 1.3),
                  ("t2", "பலர் இப்படி நினைக்கிறார்கள். ஆனால் உண்மை வேறு.", 2.9)],
    }


def _terminal(sid: str) -> Obj:
    return {
        "id": sid, "title": "Measure it twice",
        "items": [_myth("t3", "The bulb uses up some of the current", "The meter reads the [[same current]] on both sides")],
        "beats": [("t3", "Does the bulb use some up? Measure it.", 2.6)],
        "panel": terminal_panel(),
    }


def _reel(sid: str) -> Obj:
    """The 9:16 pair: the belief and the correction alone, short (the reel caps)."""
    return {
        "id": sid, "title": "Used up?",
        "items": [_myth("r1", "Current gets used up in the bulb", "The [[same current]] flows back to the cell")],
        "beats": [(None, "Here is a common idea.", 1.0), ("r1", "Does the bulb use current up?", 3.0)],
    }


def _hindi(sid: str) -> Obj:
    return {
        "id": sid, "title": "आम गलतफहमी",
        "items": [_myth("k1", "बल्ब में धारा खर्च हो जाती है", "वही [[धारा]] वापस सेल तक लौटती है")],
        "beats": [("k1", "क्या बल्ब धारा खर्च करता है?", 2.8)],
    }


def _reel_stress(sid: str) -> Obj:
    """The Latin pair at the reel caps (templates.py MYTH_FACT_WORDS, portrait) in wide capitals."""
    return {
        "id": sid, "title": "WIDE WORDS",
        "items": [_myth("w1", "MAXIMUM WATTS MEAN MAXIMUM AMPS",
                        "WATTS ARE VOLTS TIMES AMPS: [[VOLTAGE]] COUNTS")],
        "beats": [("w1", "Watts and amps, the same thing?", 2.6)],
    }


def fixtures() -> dict[str, Timeline]:
    """Every myth_fact fixture by file name. Each fits the slow gate's 300 frames (10 s), so the gate sees every
    scene and every crossfade."""
    url = picture(1600, 900)
    showcase = _showcase("mf-l-showcase", url)  # 6.8 s: the strike at the middle of m1's narration
    showcase["events"] = [_cue(1.0, "cue_term", "d1", 1.2), _cue(2.6, "look_here", "panel", 1.2),
                          _cue(5.7, "cue_term", "m1:term:0", 1.0)]
    stress = _stress("mf-l-stress")  # 3.5 s, entered by a crossfade; the strike on its draw event
    stress["events"] = [_cue(1.3, "draw", "m2")]

    tamil = _tamil("mf-ta-heading")  # 6.4 s
    tamil["events"] = [_cue(1.6, "cue_term", "t1", 1.0), _cue(4.8, "cue_term", "t2:term:0", 1.0)]
    terminal = _terminal("mf-ta-terminal")  # 3.9 s: a terminal panel across the grid box
    terminal["events"] = [_cue(1.1, "draw", "t3"), _cue(2.9, "look_here", "panel", 0.8)]

    reel = _reel("mf-p-pair")  # 5.3 s: the fact on its answer event
    reel["events"] = [_cue(3.3, "answer", "r1"), _cue(3.9, "cue_term", "r1:term:0", 1.0),
                      _cue(4.4, "look_here", "r1", 0.8)]

    hindi = _hindi("mf-hi-pair")  # 4.1 s
    hindi["events"] = [_cue(3.0, "cue_term", "k1", 0.9)]
    reel_stress = _reel_stress("mf-p-stress")  # 3.9 s
    reel_stress["events"] = [_cue(1.2, "draw", "w1")]
    return {
        f"{TEMPLATE}.landscape.json": _timeline("landscape", "en", "deep_teal", "crisp", "marker", "outfit",
                                                [showcase, stress]),
        f"{TEMPLATE}.landscape.tamil.json": _timeline("landscape", "ta", "paper_ink", "soft", "underline", "poppins",
                                                      [tamil, terminal]),
        f"{TEMPLATE}.portrait.json": _timeline("portrait", "en", "midnight_indigo", "soft", "underline", "outfit",
                                               [reel]),
        f"{TEMPLATE}.portrait.hindi.json": _timeline("portrait", "hi", "mint_studio", "crisp", "marker", "poppins",
                                                     [hindi, reel_stress]),
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
            print(f"stale: {name} (run python -m tests.compose.dynamic.make_myth_fact_fixtures)", file=sys.stderr)
        return 1 if bad else 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, tl in fixtures().items():
        (FIXTURE_DIR / name).write_text(render(tl), "utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
