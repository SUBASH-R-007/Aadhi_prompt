"""Write the ``media_split`` template fixtures (B2c; ``tests/compose/dynamic/template_fixtures.py`` says what a
template fixture is): ``tests/compose/dynamic/fixtures/templates/media_split.<format>[.<case>].json``.

Each fixture is one 10 s scene (so the slow 1x / 3x / reverse gate covers every frame of it) drawn by ``media_split``,
with the presentation and look tokens of a released family as the resolver computes them (``themes.content_tokens``,
``themes.scene_tokens``, ``presentation.motion_scale`` / ``text_entrance`` of the family's default personality), a
picture panel or a terminal panel, beats revealing the text items, and the cue events events.py (B2d) will write:
``cue_term`` on an item with a [[term]], ``look_here`` on the panel, a terminal row or an item (a camera push: media_split
is push_ok), each push after the entrances around it (§4.8 grammar: nothing enters during a camera move; the jsdom
test checks every fixture). The pictures are flat-colour Pillow drawings embedded as ``data:`` PNG URLs (no media server needed by the
real-Chromium gate; each at least as large as it is shown, §P: pictures only ever downscale). Clips are covered by the
jsdom tests (web/tests/dynamic/templates/media_split.test.js): the slow gate serves no pre-extracted frames.

Cases (B2 plan §G text cases, the four R1 families with their default personalities):

* ``landscape``: Midnight Indigo / Soft (marker), media on the right, a 16:9 picture, a bullet with a [[term]], a
  definition, a bullet with TeX; a cue.term, then a push toward the picture;
* ``landscape.light``: Paper & Ink / Crisp (baseline reveal, underline), media on the left, a tall picture, the
  lint-limit case (R-25: eight words per item) in wide glyphs, one very long word in a callout, a takeaway; a push
  toward an item;
* ``landscape.terminal``: Deep Teal / Crisp, a terminal panel whose output lines appear over the scene; a push toward
  an output row;
* ``portrait``: Deep Teal / Crisp, a 16:9 picture, three key phrases (one at a time); a cue.term, a push toward the
  picture and one toward a phrase;
* ``portrait.indic``: Mint Studio / Soft (marker), Tamil, Hindi, and Tamil + English + TeX phrases, a square picture;
* ``portrait.long``: Paper & Ink / Crisp, the lint-limit phrases in wide glyphs and one very long word, a tall picture.

Run ``python -m tests.compose.dynamic.make_media_split_fixtures`` to write them, ``--check`` to compare (exit 1 on a
difference). Byte-deterministic: sorted keys, two-space indent, LF, a final newline.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from aadhi.compose import presentation as pres
from aadhi.compose.dynamic import faces, themes
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR

TEMPLATE = "media_split"
SCENE_SECONDS = 10.0  # every frame inside the slow gate's first 300 (30 fps)

Obj = dict[str, Any]


def picture(w: int, h: int, tint: int) -> str:
    """A flat-colour circuit (a loop of wire, a battery, a bulb, charges) as a ``data:`` PNG URL: no antialiasing, so
    small and byte-stable."""
    im = Image.new("RGB", (w, h), (24 + tint * 9, 38, 64))
    d = ImageDraw.Draw(im)
    m = min(w, h)
    lw = max(6, m // 60)
    x0, y0, x1, y1 = w * 0.18, h * 0.22, w * 0.82, h * 0.78
    d.rectangle((x0, y0, x1, y1), outline=(230, 236, 244), width=lw)
    bx = w * 0.18
    d.rectangle((bx - m * 0.05, h * 0.42, bx + m * 0.05, h * 0.58), fill=(240, 160, 60))
    d.rectangle((bx - m * 0.03, h * 0.38, bx + m * 0.03, h * 0.42), fill=(240, 160, 60))
    cx, cy, r = w * 0.82, h * 0.5, m * 0.09
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(250, 220, 70))
    for k in range(5):
        px = x0 + (x1 - x0) * (0.15 + 0.17 * k)
        d.ellipse((px - m * 0.02, y0 - m * 0.02, px + m * 0.02, y0 + m * 0.02), fill=(90, 200, 250))
    d.rectangle((0, h - m * 0.06, w, h), fill=(16, 22, 40))
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def presentation(fmt: str, family_id: str, total: float) -> Obj:
    """``Timeline.presentation`` of a released family with its default personality (as ``timeline_presentation``
    computes it, without a screenplay)."""
    fam = themes.RELEASED_FAMILIES[family_id]
    personality = fam.personality
    return {
        "style": "dynamic", "format": fmt, "safe_zone": "universal", "content": themes.content_tokens(fam.mode),
        "fonts": faces.resolver_fonts(None), "personality": personality,
        "caption_style": "bold_outline" if fmt == "portrait" else "clean_pill",
        "cue_term_style": "underline" if personality == "crisp" else "marker",
        "captions_burn": True, "sfx": False, "motion": "standard", "generated_seconds": total, "engine_version": 2,
        "content_alt": themes.content_alt_tokens(fam.mode), "family": fam.id, "mode": fam.mode,
        "motion_scale": pres.motion_scale(personality, fam), "text_entrance": pres.text_entrance(personality, fam),
    }


def look(family_id: str, seed: int, variant: str | None) -> Obj:
    fam = themes.RELEASED_FAMILIES[family_id]
    return {"template": TEMPLATE, "variant": variant, "tokens": themes.scene_tokens(fam, 0),
            "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": seed, "frozen_at": None},
            "transition_in": {"kind": "cut", "seconds": 0.0}}


def beat(bid: str, index: int, start: float, end: float, text: str, item: str) -> Obj:
    speech_end = round(end - 0.2, 3)
    return {"beat_id": bid, "index": index, "start": start, "speech_end": speech_end, "end": end, "narration": text,
            "board_item_id": item, "captions": [{"start": start, "end": speech_end, "text": text}]}


def beats(prefix: str, items: list[Obj], lines: list[str]) -> list[Obj]:
    """One beat per item: 0.6-3.4, 3.4-6.4, 6.4-9.6 s."""
    times = ((0.6, 3.4), (3.4, 6.4), (6.4, 9.6))
    return [beat(f"{prefix}{k + 1}", k, a, b, line, item["id"])
            for k, ((a, b), line, item) in enumerate(zip(times, lines, items, strict=True))]


def image_panel(title: str, url: str, w: int, h: int) -> Obj:
    return {"panel": {"kind": "image", "title": title, "rationale": "Shows the circuit.",
                      "image_prompt": "A simple circuit: a battery, wires and a bulb"},
            "media": {"kind": "image", "asset_key": "img-fixture", "url": url, "width": w, "height": h,
                      "fit": "contain"},
            "show_at": 0.0}


def cue(t: float, kind: str, target: str, dur: float = 1.6) -> Obj:
    return {"t": t, "kind": kind, "target": target, "dur": dur, "anchored": True}


def timeline(fmt: str, family_id: str, sid: str, title: str, items: list[Obj], scene_beats: list[Obj],
             events: list[Obj], variant: str | None, panel: Obj, *, seed: int, language: str = "en-IN") -> Obj:
    scene = {"scene_id": sid, "index": 0, "type": "content", "title": title, "start": 0.0, "duration": SCENE_SECONDS,
             "audio_offset": 0.4, "board": items, "beats": scene_beats, "side_panel": panel, "events": events,
             "look": look(family_id, seed, variant)}
    portrait = fmt == "portrait"
    captions = [c for b in scene_beats for c in b["captions"]]
    data = {"schema_version": 3, "language": language, "board_language": language, "fps": 30,
            "width": 1080 if portrait else 1920, "height": 1920 if portrait else 1080,
            "total_duration": SCENE_SECONDS, "chapters": [{"start": 0.0, "title": "Circuits"}], "captions": captions,
            "transition_seconds": 0.4, "scenes": [scene],
            "presentation": presentation(fmt, family_id, SCENE_SECONDS)}
    return Timeline.model_validate(data).model_dump(mode="json")


def fixtures() -> dict[str, Obj]:
    wide = picture(1600, 900, 0)
    tall = picture(900, 1200, 1)
    square = picture(1200, 1200, 2)
    out: dict[str, Obj] = {}

    items = [{"id": "b1", "kind": "bullet", "text": "[[Current]] is the flow of charge"},
             {"id": "b2", "kind": "definition", "term": "Voltage", "text": "the push that drives it"},
             {"id": "b3", "kind": "bullet", "text": "Ohm's law: $I = V/R$"}]
    out["landscape"] = timeline(
        "landscape", "midnight_indigo", "s-split", "How current flows in a circuit", items,
        beats("k", items, ["Current is the flow of charge.", "Voltage is the push that drives it.",
                           "And Ohm's law ties them together."]),
        # the push after b3's entrance (§4.8: nothing enters during a camera move; B2c review V6)
        [cue(1.4, "cue_term", "b1"), cue(7.0, "look_here", "panel", 2.0)], "media_right",
        image_panel("A simple circuit", wide, 1600, 900), seed=1101)

    items = [{"id": "w1", "kind": "bullet", "text": "WWWW MMMM WWWW MMMM WWWW MMMM WWWW MMMM"},
             {"id": "w2", "kind": "callout_warning", "text": "Pneumonoultramicroscopicsilicovolcanoconiosis harms lungs"},
             {"id": "w3", "kind": "takeaway", "text": "Wide words make [[maximum]] width demands on every line"}]
    out["landscape.light"] = timeline(
        "landscape", "paper_ink", "s-wide", "Maximum Wattage Warms Wide Metal Wires", items,
        beats("l", items, ["Wide words first.", "A very long word.", "Wide words make maximum demands."]),
        [cue(4.0, "look_here", "w2"), cue(6.8, "cue_term", "w3", 2.0)], "media_left",
        image_panel("A tall circuit", tall, 900, 1200), seed=2202)

    items = [{"id": "t1", "kind": "bullet", "text": "Run the [[simulation]]"},
             {"id": "t2", "kind": "paragraph", "text": "Each line is one time step"},
             {"id": "t3", "kind": "bullet", "text": "The current settles at 2 A"}]
    terminal = {"panel": {"kind": "terminal", "title": "Simulation", "rationale": "Shows the steps.",
                          "terminal": {"command": "python circuit.py --steps 4",
                                       "output": "t=0  I=0.00 A\nt=1  I=1.25 A\nt=2  I=2.00 A\nt=3  I=2.00 A"}},
                "media": None, "show_at": 0.5}
    out["landscape.terminal"] = timeline(
        "landscape", "deep_teal", "s-term", "Watching the current settle", items,
        beats("m", items, ["Run the simulation.", "Each line is one time step.", "The current settles at two amps."]),
        [cue(1.4, "cue_term", "t1"), cue(6.8, "look_here", "panel:row:1", 2.0)], "media_right", terminal, seed=3303)

    items = [{"id": "p1", "kind": "bullet", "text": "[[Current]] heats the thin filament"},
             {"id": "p2", "kind": "bullet", "text": "Hot metal gives off light"},
             {"id": "p3", "kind": "bullet", "text": "Power is $P = VI$"}]
    out["portrait"] = timeline(
        "portrait", "deep_teal", "s-reel", "Why the bulb glows", items,
        beats("n", items, ["Current heats the thin filament.", "Hot metal gives off light.",
                           "Power is voltage times current."]),
        [cue(1.2, "cue_term", "p1"), cue(4.0, "look_here", "panel"), cue(7.2, "look_here", "p3")], None,
        image_panel("The bulb", wide, 1600, 900), seed=4404)

    items = [{"id": "i1", "kind": "bullet", "text": "[[மின்னோட்டம்]] என்பது மின்னூட்டத்தின் ஓட்டம்"},
             {"id": "i2", "kind": "bullet", "text": "वोल्टेज [[धक्का]] देता है"},
             {"id": "i3", "kind": "bullet", "text": "Ohm விதி: [[மின்தடை]] $R = V/I$"}]
    out["portrait.indic"] = timeline(
        "portrait", "mint_studio", "s-indic", "மின்னோட்டம் எப்படி பாய்கிறது", items,
        beats("j", items, ["மின்னோட்டம் ஓடுகிறது.", "वोल्टेज धक्का देता है.", "Ohm விதி."]),
        [cue(1.2, "cue_term", "i1"), cue(4.0, "cue_term", "i2"), cue(7.2, "look_here", "panel")], None,
        image_panel("மின்சுற்று", square, 1200, 1200), seed=5505, language="ta-IN")

    items = [{"id": "q1", "kind": "bullet", "text": "WWW MMM WWW MMM WWW MMM WWW MMM"},
             {"id": "q2", "kind": "paragraph", "text": "Pneumonoultramicroscopicsilicovolcanoconiosis"},
             {"id": "q3", "kind": "takeaway", "text": "Wide words make [[maximum]] demands"}]
    out["portrait.long"] = timeline(
        "portrait", "paper_ink", "s-long", "Maximum Wattage Within Wide Wires", items,
        beats("r", items, ["Wide words.", "A very long word.", "Wide words make maximum demands."]),
        [cue(7.0, "cue_term", "q3")], None, image_panel("A tall circuit", tall, 900, 1200), seed=6606)
    return out


def files() -> dict[Path, str]:
    """Every fixture's path and its exact text."""
    return {FIXTURE_DIR / f"{TEMPLATE}.{case}.json": json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            for case, data in fixtures().items()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="compare instead of writing; exit 1 on a difference")
    args = ap.parse_args(argv)
    stale = []
    for path, text in files().items():
        current = path.read_text("utf-8") if path.is_file() else None
        if current == text:
            continue
        if args.check:
            stale.append(path.name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {path.name}")
    if stale:
        print("stale:", ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
