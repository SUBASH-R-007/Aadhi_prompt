"""Write the ``definition_card`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says what a template
fixture is): ``tests/compose/dynamic/fixtures/templates/definition_card.<format>[.<case>].json``.

Each fixture is one scene drawn by ``definition_card`` (16:9 10 s, 9:16 8 s: inside the template's §4.3 timing and
inside the slow 1x / 3x / reverse gate's first 300 frames), with the resolver's own values (presentation tokens,
fonts, the personality's motion scale and text entrance, the scene tokens of a palette slot, the stored icons), one
``definition`` item and one ``bullet`` example (what ``templates.py _fit_definition_card`` accepts), a ``cue_term`` on
a [[term]] and a ``reveal`` event. The cases cover what the B2 plan §G gallery asks: both formats, a dark and a light
family each, Poppins as the display face in one dark and one light timeline (with Anek for the Indic text), the three
§4.3 variants, both text entrances and both cue styles, and the text cases typical / longest with wide capitals / one
very long word / inline TeX / Tamil / Devanagari:

* ``definition_card.landscape.json`` (Midnight Indigo / Soft: ``fade_rise``, x1.15, marker; Poppins): the typical
  card - a term, its meaning with a [[term]], an example bullet on its own beat, and the item's stored icon
  (``card_icon_right``: the feature icon beside the text).
* ``definition_card.landscape.light.json`` (Paper & Ink / Crisp: ``baseline_reveal``, x0.85, underline): the stress
  card in the light family - the lint-limit meaning in wide capitals, a word longer than any line, inline TeX - with a
  picture beside it (``card_image_left``; the picture is a flat-colour Pillow drawing as a ``data:`` PNG, so no media
  server is needed).
* ``definition_card.portrait.json`` (Deep Teal / Crisp: ``baseline_reveal``, x0.85, underline): the 9:16 card with the
  stored icon above the term (``card_icon_right``) and a short example.
* ``definition_card.portrait.indic.json`` (Mint Studio / Soft, light, ta-IN, Poppins + Anek): a Tamil term and meaning
  with an English [[term]] and inline TeX, and a Devanagari example (``centred``).

Run ``python -m tests.compose.dynamic.make_definition_card_fixtures`` to write them, ``--check`` to compare (exit 1 on
a difference).
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

from aadhi.compose import presentation as pr
from aadhi.compose.dynamic import faces, icons, themes
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR

TEMPLATE = "definition_card"
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
SECONDS = {"landscape": 10.0, "portrait": 8.0}

Obj = dict[str, Any]


def picture(w: int, h: int) -> str:
    """A flat-colour resistor sketch (no antialiasing, so small and byte-stable) as a ``data:`` PNG URL."""
    im = Image.new("RGB", (w, h), (26, 34, 58))
    d = ImageDraw.Draw(im)
    m = min(w, h)
    lw = max(6, m // 60)
    y = h * 0.5
    d.line((w * 0.08, y, w * 0.34, y), fill=(230, 236, 244), width=lw)
    d.line((w * 0.66, y, w * 0.92, y), fill=(230, 236, 244), width=lw)
    d.rectangle((w * 0.34, y - m * 0.11, w * 0.66, y + m * 0.11), fill=(240, 160, 60))
    for k in range(4):
        x = w * (0.40 + 0.06 * k)
        d.rectangle((x, y - m * 0.11, x + m * 0.022, y + m * 0.11), fill=(26, 34, 58))
    d.rectangle((0, h - m * 0.06, w, h), fill=(16, 22, 40))
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def presentation(fmt: str, family_id: str, personality: str, cue_style: str, display: str, total: float) -> Obj:
    """The resolver's presentation for a family and personality (``timeline_presentation``'s fields). ``display``
    other than ``outfit``: the B2 fonts form naming that display face (with ``anek`` Indic faces), which the resolver
    writes from B2e; the gallery wants Poppins in a dark and in a light family (plan §G)."""
    family = pr.family_for(family_id)
    fonts = faces.resolver_fonts(display)
    if display != fonts["display"]:
        fonts = {**fonts, "display": display, "indic": "anek"}
    return {
        "style": "dynamic", "format": fmt, "safe_zone": "universal", "content": pr.content_tokens(family.mode),
        "fonts": fonts, "personality": personality, "caption_style": "bold_outline" if fmt == "portrait" else "clean_pill",
        "cue_term_style": cue_style, "captions_burn": True, "sfx": False, "motion": "standard",
        "generated_seconds": total, "engine_version": pr.ENGINE_VERSION,
        "content_alt": themes.content_alt_tokens(family.mode), "family": family.id, "mode": family.mode,
        "motion_scale": pr.motion_scale(personality, family), "text_entrance": pr.text_entrance(personality, family),
    }


def look(family_id: str, seed: int, variant: str, items: list[Obj]) -> Obj:
    family = pr.family_for(family_id)
    stored = icons.scene_icons(items)
    return {
        "template": TEMPLATE, "variant": variant, "tokens": themes.scene_tokens(family, 0),
        "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": seed, "frozen_at": None},
        "transition_in": {"kind": "cut", "seconds": 0.0},
        "icons": {k: v.model_dump(mode="json") for k, v in stored.items()},
    }


def beat(bid: str, index: int, start: float, end: float, text: str, item: str) -> Obj:
    speech_end = round(end - 0.3, 3)
    return {"beat_id": bid, "index": index, "phase": "main", "start": start, "speech_end": speech_end, "end": end,
            "narration": text, "board_item_id": item,
            "captions": [{"start": start, "end": speech_end, "text": text}]}


def cue(t: float, kind: str, target: str, dur: float | None = None) -> Obj:
    out: Obj = {"t": t, "kind": kind, "target": target, "anchored": True}
    if dur is not None:
        out["dur"] = dur
    return out


def timeline(fmt: str, family_id: str, personality: str, cue_style: str, display: str, *, sid: str, title: str,
             items: list[Obj], lines: list[str], events: list[Obj], variant: str, seed: int,
             panel: Obj | None = None, language: str = "en-IN") -> Obj:
    """One scene: a beat per item (the definition first, then the example), the cues as given."""
    total = SECONDS[fmt]
    half = round(total / 2, 3)
    times = [(0.6, half), (half, round(total - 0.4, 3))]
    scene_beats = [beat(f"{sid}-{k + 1}", k, a, b, line, item["id"])
                   for k, ((a, b), line, item) in enumerate(zip(times, lines, items, strict=True))]
    scene = {"scene_id": sid, "index": 0, "type": "content", "title": title, "start": 0.0, "duration": total,
             "audio_offset": 0.4, "audio_duration": round(total - 0.8, 3), "board": items, "beats": scene_beats,
             "events": events, "look": look(family_id, seed, variant, items)}
    if panel is not None:
        scene["side_panel"] = panel
        scene["layout"] = {"show_side_panel": True}
    width, height = SIZES[fmt]
    data = {
        "schema_version": 3, "language": language, "board_language": language, "fps": 30, "width": width,
        "height": height, "total_duration": total, "chapters": [{"start": 0.0, "title": title}],
        "captions": [c for b in scene_beats for c in b["captions"]], "transition_seconds": 0.4, "scenes": [scene],
        "presentation": presentation(fmt, family_id, personality, cue_style, display, total),
    }
    return Timeline.model_validate(data).model_dump(mode="json")


def image_panel(title: str, url: str, w: int, h: int) -> Obj:
    return {"panel": {"kind": "image", "title": title, "rationale": "Shows the component.",
                      "image_prompt": "A resistor in a circuit, drawn flat"},
            "media": {"kind": "image", "asset_key": "img-definition", "url": url, "width": w, "height": h,
                      "fit": "contain"},
            "show_at": 0.0}


def fixtures() -> dict[str, Obj]:
    out: dict[str, Obj] = {}

    # the typical card: a term, its meaning with a [[term]], an example, and a stored icon (card_icon_right)
    items = [{"id": "d1", "kind": "definition", "term": "Resistance",
              "text": "how strongly a material [[opposes]] the flow of charge", "icon_hint": "filter"},
             {"id": "b1", "kind": "bullet", "text": "A thin wire resists more than a thick one"}]
    out["landscape"] = timeline(
        "landscape", "midnight_indigo", "soft", "marker", "poppins", sid="dc-l-typical",
        title="What resistance means", items=items,
        lines=["Resistance is how strongly a material opposes the flow of charge.",
               "A thin wire resists more than a thick one."],
        events=[cue(1.6, "cue_term", "d1", 1.6), cue(5.4, "reveal", "b1")], variant="card_icon_right", seed=7101)

    # the stress card in a light family, with a picture beside it (card_image_left)
    items = [{"id": "d1", "kind": "definition", "term": "MAXIMUM WATTAGE",
              "text": "WARM MOTORS MEET HEAVY LOADS at $P = VI$"},
             {"id": "b1", "kind": "bullet", "text": "Pneumonoultramicroscopicsilicovolcanoconiosis is [[unrelated]]"}]
    out["landscape.light"] = timeline(
        "landscape", "paper_ink", "crisp", "underline", "outfit", sid="dc-l-stress",
        title="Maximum wattage", items=items,
        lines=["Maximum wattage is reached when warm motors meet heavy loads.",
               "And here is one very long word."],
        events=[cue(5.6, "cue_term", "b1", 1.4)], variant="card_image_left", seed=7202,
        panel=image_panel("A resistor", picture(1600, 900), 1600, 900))

    # 9:16: the icon above the term, a short meaning and a short example. The column holds very little: with the
    # icon above the term, a two-line meaning plus an example row reaches past the critical box and the real page's
    # safe-zone check blocks (measured, tests/determinism/test_template_pages.py), so both stay to one line.
    items = [{"id": "d1", "kind": "definition", "term": "Resistance",
              "text": "what [[opposes]] charge", "icon_hint": "filter"},
             {"id": "b1", "kind": "bullet", "text": "Thin wires resist"}]
    out["portrait"] = timeline(
        "portrait", "deep_teal", "crisp", "underline", "outfit", sid="dc-p-typical",
        title="Resistance", items=items,
        lines=["Resistance is what opposes charge.", "Thin wires resist."],
        events=[cue(1.4, "cue_term", "d1", 1.4), cue(4.2, "reveal", "b1")], variant="card_icon_right", seed=7303)

    # 9:16 Indic: a Tamil term and meaning with an English [[term]] and TeX, a Devanagari example (centred)
    items = [{"id": "d1", "kind": "definition", "term": "மின்தடை",
              "text": "[[resistance]]: $R = V/I$"},
             {"id": "b1", "kind": "bullet", "text": "पतला तार"}]
    out["portrait.indic"] = timeline(
        "portrait", "mint_studio", "soft", "marker", "poppins", sid="dc-p-indic",
        title="மின்தடை", items=items,
        lines=["மின்தடை என்பது தடுக்கும் தன்மை.", "पतला तार."],
        events=[cue(1.4, "cue_term", "d1", 1.4)], variant="centred", seed=7404, language="ta-IN")
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
