"""Write the ``animation_stage`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says what a
template fixture is): ``tests/compose/dynamic/fixtures/templates/animation_stage.<format>[.<case>].json``.

Each fixture is one 10 s scene (so the slow 1x / 3x / reverse gate covers every frame of it) drawn by
``animation_stage``, with the presentation and look tokens of a released family as the resolver computes them
(``themes.content_tokens`` / ``scene_tokens``, ``presentation.motion_scale`` / ``text_entrance`` of the family's
default personality), and the two shapes of data §4.3 gives the template:

* a ``simulation`` scene whose rendered animation is the scene's own medium, its step labels coming from the beats'
  ``visual_cue`` ("one animation step per beat"), with a ``look_here`` on the animation's frame;
* a ``content`` scene carrying a ``manim`` side panel beside at most three board text items, whose step labels are
  those items, with a ``cue_term`` on a ``[[term]]`` and a ``look_here`` on an item.

The animation is a **still** here (a flat-colour Pillow drawing of the Ohm's-law plot, embedded as a ``data:`` PNG
URL): the slow real-Chromium gate serves no pre-extracted frames, so a video clip is covered by the jsdom tests
(``web/tests/dynamic/templates/animation_stage.test.js``) instead, exactly as ``media_split``'s fixtures do. Each
still is larger than the box it is shown in (§P: pictures only ever downscale). The landscape stills are 16:9 (what
the Manim renderer produces today) and the portrait one is 1080x1350 (§4.3's 9:16 panel profile).

Cases (B2 plan §G text cases, the four R1 families with their default personalities):

* ``landscape``: Midnight Indigo / Soft (fade + rise, marker), a simulation with three step labels and a look_here on
  the panel;
* ``landscape.light``: Paper & Ink / Crisp (baseline reveal, underline), a manim panel beside the lint-limit case
  (R-25: eight words per item) in wide glyphs, one very long word and a takeaway with a ``[[term]]``;
* ``portrait``: Deep Teal / Crisp, a simulation with three short step labels (the 9:16 label column is x 192-780) and
  a look_here on the panel;
* ``portrait.indic``: Mint Studio / Soft (marker), Tamil, Hindi and Tamil + English + TeX labels on a manim panel.

Run ``python -m tests.compose.dynamic.make_animation_stage_fixtures`` to write them, ``--check`` to compare (exit 1 on
a difference). Byte-deterministic: sorted keys, two-space indent, LF, a final newline.
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

TEMPLATE = "animation_stage"
SCENE_SECONDS = 10.0  # every frame inside the slow gate's first 300 (30 fps)

Obj = dict[str, Any]

# The lint-limit text (R-25: <= 8 words per item) in wide capitals, and one word longer than any label column.
WIDE = "MAXIMUM WATTAGE WARMS WIDE METAL WIRES WHEN WARM"
LONG_WORD = "Pneumonoultramicroscopicsilicovolcanoconiosis plots badly"


def still(w: int, h: int, tint: int) -> str:
    """A frame of the Ohm's-law animation (axes, a straight line through the origin, two marked points) as a ``data:``
    PNG URL: flat colours, no antialiasing, so the file is small and byte-stable."""
    im = Image.new("RGB", (w, h), (12 + tint * 6, 14, 26))
    d = ImageDraw.Draw(im)
    m = min(w, h)
    lw = max(4, m // 90)
    x0, y0, x1, y1 = w * 0.18, h * 0.18, w * 0.88, h * 0.80
    d.line((x0, y0, x0, y1), fill=(230, 236, 244), width=lw)  # current axis
    d.line((x0, y1, x1, y1), fill=(230, 236, 244), width=lw)  # voltage axis
    d.line((x0, y1, x1, y0 + (y1 - y0) * 0.1), fill=(250, 220, 70), width=lw * 2)
    for k in (0.4, 0.8):
        px = x0 + (x1 - x0) * k
        py = y1 - (y1 - y0) * 0.9 * k
        r = m * 0.022
        d.ellipse((px - r, py - r, px + r, py + r), fill=(240, 110, 90))
    d.rectangle((0, h - m * 0.05, w, h), fill=(8, 10, 18))
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
        "captions_burn": True, "sfx": False, "motion": "standard", "generated_seconds": total,
        "engine_version": pres.ENGINE_VERSION, "content_alt": themes.content_alt_tokens(fam.mode), "family": fam.id,
        "mode": fam.mode, "motion_scale": pres.motion_scale(personality, fam),
        "text_entrance": pres.text_entrance(personality, fam),
    }


def look(family_id: str, seed: int) -> Obj:
    fam = themes.RELEASED_FAMILIES[family_id]
    return {"template": TEMPLATE, "variant": None, "tokens": themes.scene_tokens(fam, 0),
            "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": seed, "frozen_at": None},
            "transition_in": {"kind": "cut", "seconds": 0.0}}


def beat(bid: str, index: int, start: float, end: float, text: str, item: str | None, cue: str | None) -> Obj:
    """One beat: its narration, the board item it reveals (a manim-panel scene) and what the animation does then
    (``visual_cue``: §4.3's one animation step per beat, which a simulation's step label shows)."""
    speech_end = round(end - 0.2, 3)
    out: Obj = {"beat_id": bid, "index": index, "start": start, "speech_end": speech_end, "end": end,
                "narration": text, "board_item_id": item,
                "captions": [{"start": start, "end": speech_end, "text": text}]}
    if cue is not None:
        out["visual_cue"] = cue
    return out


TIMES = ((0.6, 3.4), (3.4, 6.4), (6.4, 9.6))


def beats(prefix: str, lines: list[str], items: list[str | None], cues: list[str | None]) -> list[Obj]:
    """Three beats over 0.6-9.6 s, one per step."""
    return [beat(f"{prefix}{k + 1}", k, a, b, line, item, cue)
            for k, ((a, b), line, item, cue) in enumerate(zip(TIMES, lines, items, cues, strict=True))]


def manim_panel(title: str, url: str, w: int, h: int) -> Obj:
    """A ``manim`` side panel with its rendered clip resolved to a still (§4.3 data: a ``manim`` panel)."""
    return {"panel": {"kind": "manim", "title": title, "rationale": "Shows the law as a straight line.",
                      "manim": {"template": "proportional_line",
                                "params": {"slope": 0.5, "x_label": "Voltage (V)", "y_label": "Current (A)"}}},
            "media": {"kind": "image", "asset_key": "manim-fixture", "url": url, "width": w, "height": h,
                      "fit": "contain"},
            "show_at": 0.0}


def cue(t: float, kind: str, target: str, dur: float = 1.6) -> Obj:
    return {"t": t, "kind": kind, "target": target, "dur": dur, "anchored": True}


def timeline(fmt: str, family_id: str, sid: str, stype: str, title: str, items: list[Obj], scene_beats: list[Obj],
             events: list[Obj], *, media: Obj | None = None, panel: Obj | None = None, seed: int,
             language: str = "en-IN") -> Obj:
    scene: Obj = {"scene_id": sid, "index": 0, "type": stype, "title": title, "start": 0.0,
                  "duration": SCENE_SECONDS, "audio_offset": 0.4, "board": items, "beats": scene_beats,
                  "events": events, "look": look(family_id, seed)}
    if media is not None:
        scene["media"] = media
        scene["layout"] = {"mascot_position": "hidden", "show_side_panel": False, "fullscreen_media": True}
    if panel is not None:
        scene["side_panel"] = panel
    portrait = fmt == "portrait"
    captions = [c for b in scene_beats for c in b["captions"]]
    data = {"schema_version": 3, "language": language, "board_language": language, "fps": 30,
            "width": 1080 if portrait else 1920, "height": 1920 if portrait else 1080,
            "total_duration": SCENE_SECONDS, "chapters": [{"start": 0.0, "title": "Ohm's law at work"}],
            "captions": captions, "transition_seconds": 0.4, "scenes": [scene],
            "presentation": presentation(fmt, family_id, SCENE_SECONDS)}
    return Timeline.model_validate(data).model_dump(mode="json")


def clip_media(url: str, w: int, h: int) -> Obj:
    """The scene's own animation (a simulation's rendered Manim video, here a still: see the module docstring)."""
    return {"kind": "image", "asset_key": "manim-scene-fixture", "url": url, "width": w, "height": h,
            "fit": "contain"}


def fixtures() -> dict[str, Obj]:
    wide = still(1600, 900, 0)
    tallish = still(1080, 1350, 2)
    out: dict[str, Obj] = {}

    # a simulation: no board, one step label per beat, the look_here on the animation's frame after the last label
    out["landscape"] = timeline(
        "landscape", "midnight_indigo", "s-sim", "simulation", "Visualising Ohm's law", [],
        beats("a", ["Voltage equals current times resistance.", "Now watch it rearrange.",
                    "A perfect straight line through the origin."],
              [None, None, None],
              ["The law is written and boxed", "It rearranges to current equals voltage over resistance",
               "Current is plotted against voltage"]),
        [cue(7.2, "look_here", "panel", 2.0)], media=clip_media(wide, 1600, 900), seed=7101)

    # a manim panel beside three text items: the stress text, one very long word, a [[term]] in a takeaway
    items = [{"id": "w1", "kind": "bullet", "text": WIDE},
             {"id": "w2", "kind": "paragraph", "text": LONG_WORD},
             {"id": "w3", "kind": "takeaway", "text": "Double the [[voltage]] and the current doubles"}]
    out["landscape.light"] = timeline(
        "landscape", "paper_ink", "s-panel", "content", "Maximum Wattage Within Wide Wires", items,
        beats("b", ["Wide words first.", "A very long word.", "Double the voltage and the current doubles."],
              ["w1", "w2", "w3"], [None, None, None]),
        [cue(0.5, "reveal", "w1"), cue(3.3, "reveal", "w2"), cue(6.3, "reveal", "w3"),
         cue(7.4, "cue_term", "w3", 1.6)],
        panel=manim_panel("Current against voltage", wide, 1600, 900), seed=7202)

    # 9:16: short step labels (the label column is x 192-780, at most two lines at its 48 px floor)
    out["portrait"] = timeline(
        "portrait", "deep_teal", "s-reel", "simulation", "Ohm's law, seen", [],
        beats("c", ["Voltage times resistance.", "Watch it rearrange.", "One straight line."],
              [None, None, None],
              ["The law appears", "It rearranges", "The line is drawn"]),
        [cue(7.2, "look_here", "panel", 2.0)], media=clip_media(tallish, 1080, 1350), seed=7303)

    items = [{"id": "i1", "kind": "bullet", "text": "[[மின்னோட்டம்]] நேர் விகிதம்"},
             {"id": "i2", "kind": "bullet", "text": "वोल्टेज दोगुना, [[धारा]] दोगुनी"},
             {"id": "i3", "kind": "takeaway", "text": "Ohm விதி: $I = V/R$"}]
    out["portrait.indic"] = timeline(
        "portrait", "mint_studio", "s-indic", "content", "ஓம் விதியை பார்ப்போம்", items,
        beats("d", ["மின்னோட்டம் நேர் விகிதம்.", "वोल्टेज दोगुना.", "Ohm விதி."], ["i1", "i2", "i3"],
              [None, None, None]),
        [cue(1.4, "cue_term", "i1", 1.6), cue(4.2, "cue_term", "i2", 1.6), cue(7.4, "look_here", "i3", 1.8)],
        panel=manim_panel("மின்னோட்டம்", tallish, 1080, 1350), seed=7404, language="ta-IN")
    return out


def files() -> dict[Path, str]:
    """Every fixture's path and its exact text."""
    return {FIXTURE_DIR / f"{TEMPLATE}.{case}.json":
            json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
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
