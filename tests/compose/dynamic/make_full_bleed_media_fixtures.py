"""Write the ``full_bleed_media`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says what a
template fixture is): ``tests/compose/dynamic/fixtures/templates/full_bleed_media.<format>[.<case>].json``.

Each fixture is one scene drawn by ``full_bleed_media`` (16:9 10 s, 9:16 8 s: inside the template's §4.3 timing of
3-12 s and inside the slow 1x / 3x / reverse gate's first 300 frames), with the resolver's own values (presentation
tokens, fonts, the personality's motion scale and text entrance, the scene tokens of a palette slot). The medium is a
flat-colour Pillow drawing served as a ``data:`` PNG, so no media server is needed; a clip is covered by the
template's jsdom test, which swaps the still for a video ref (``web/tests/dynamic/templates/full_bleed_media.test.js``),
and by ``tests/determinism/test_template_clips.py``.

The cases cover what the B2 plan §G gallery asks - both formats, a dark and a light family each, Poppins as the
display face in one dark and one light timeline (with Anek for the Indic text), both text entrances and both cue
styles - and both data shapes §4.3 gives this template (a forced ``ai_video`` scene and a board with an application
image), each with the medium's two aspect cases (one that matches the frame, one that does not):

* ``full_bleed_media.landscape.json`` (Midnight Indigo / Soft: ``fade_rise``, x1.15, marker; Poppins): the showcase
  shape - an ``ai_video`` scene whose 16:9 still (Ken Burns 1.00 -> 1.06, as ``compose/timeline.py`` attaches it to an
  AI-video fallback) fills the frame, with the scene's title as the keyword label and its subtitle beneath.
* ``full_bleed_media.landscape.light.json`` (Paper & Ink / Crisp: ``baseline_reveal``, x0.85, underline): the board
  shape in the light family - an application image (a 4:3 picture, so 16:9 covers the frame and crops) under a label
  of wide capitals, with one text item carrying a ``[[term]]`` and inline TeX, revealed by a ``reveal`` event.
* ``full_bleed_media.portrait.json`` (Deep Teal / Crisp: ``baseline_reveal``, x0.85, underline): an ``ai_video`` scene
  in 9:16, where the 16:9 still is shown ``contain_blur`` (§4.3, §2.5) over a blurred copy of its own frame.
* ``full_bleed_media.portrait.indic.json`` (Mint Studio / Soft, light, ta-IN, Poppins + Anek): a Tamil label over a
  9:16 picture that fills the frame exactly, with a Devanagari sub-line holding an English ``[[term]]`` and inline TeX.

Run ``python -m tests.compose.dynamic.make_full_bleed_media_fixtures`` to write them, ``--check`` to compare (exit 1
on a difference).
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
from aadhi.compose.dynamic import faces, themes
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR

TEMPLATE = "full_bleed_media"
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
SECONDS = {"landscape": 10.0, "portrait": 8.0}
# §4.3: "Stills get Ken Burns 1.00 -> 1.06" (a slow push toward the centre right).
KEN_BURNS = {"start": {"cx": 0.48, "cy": 0.52, "scale": 1.0}, "end": {"cx": 0.54, "cy": 0.48, "scale": 1.06}}

Obj = dict[str, Any]


def picture(w: int, h: int) -> str:
    """A flat-colour night-city sketch (no antialiasing, so small and byte-stable) as a ``data:`` PNG URL: a dark sky,
    a horizon, three towers with lit windows and a glowing socket. Flat bands make the blurred ``contain_blur``
    backdrop and a Ken-Burns zoom easy to read in the reference frames."""
    im = Image.new("RGB", (w, h), (12, 16, 36))
    d = ImageDraw.Draw(im)
    m = min(w, h)
    horizon = h * 0.72
    d.rectangle((0, horizon, w, h), fill=(20, 26, 48))
    for k, (x0, width, top) in enumerate(((0.10, 0.16, 0.34), (0.32, 0.22, 0.20), (0.60, 0.18, 0.42))):
        left = w * x0
        right = left + w * width
        d.rectangle((left, h * top, right, horizon), fill=(28, 36, 62))
        for row in range(5):
            for col in range(3):
                x = left + w * width * (0.18 + 0.3 * col)
                y = h * top + (horizon - h * top) * (0.12 + 0.17 * row)
                if (row + col + k) % 3:
                    d.rectangle((x, y, x + m * 0.018, y + m * 0.026), fill=(240, 196, 96))
    d.ellipse((w * 0.78 - m * 0.09, horizon - m * 0.09, w * 0.78 + m * 0.09, horizon + m * 0.09), fill=(248, 214, 120))
    d.rectangle((0, h - m * 0.05, w, h), fill=(8, 11, 26))
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


def look(family_id: str, seed: int) -> Obj:
    """The scene's look: this template, no variant (§4.3 gives it none), the family's scene tokens."""
    family = pr.family_for(family_id)
    return {
        "template": TEMPLATE, "variant": None, "tokens": themes.scene_tokens(family, 0),
        "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": seed, "frozen_at": None},
        "transition_in": {"kind": "cut", "seconds": 0.0},
    }


def beat(bid: str, index: int, start: float, end: float, text: str, item: str | None) -> Obj:
    speech_end = round(end - 0.3, 3)
    out: Obj = {"beat_id": bid, "index": index, "phase": "main", "start": start, "speech_end": speech_end, "end": end,
                "narration": text, "captions": [{"start": start, "end": speech_end, "text": text}]}
    if item is not None:
        out["board_item_id"] = item
    return out


def still(url: str, w: int, h: int, *, ken_burns: bool = True) -> Obj:
    """An AI-video still as ``compose/timeline.py`` stores it (its own size; the template decides the fit)."""
    out: Obj = {"kind": "image", "asset_key": f"fbm-{w}x{h}", "url": url, "width": w, "height": h, "fit": "contain"}
    if ken_burns:
        out["ken_burns"] = KEN_BURNS
    return out


def image_panel(title: str, url: str, w: int, h: int) -> Obj:
    """An application image side panel (``_fit_full_bleed_media``'s board shape).

    The picture carries its stored Ken Burns like the AI stills do: this template shows a side panel's medium full
    bleed, so a still with no move of its own leaves the whole frame unchanged once the lower third has entered -
    which the 1x/3x/reverse gate's own liveness check refuses ("the frames hardly change": the background that would
    otherwise drift is covered by the picture)."""
    return {"panel": {"kind": "image", "title": title, "rationale": "Shows the law at work.",
                      "image_prompt": "A city skyline lit district by district, drawn flat"},
            "media": still(url, w, h), "show_at": 0.0}


def timeline(fmt: str, family_id: str, personality: str, cue_style: str, display: str, *, sid: str, stype: str,
             title: str, subtitle: str | None, lines: list[str], seed: int, media: Obj | None = None,
             panel: Obj | None = None, items: list[Obj] | None = None, events: list[Obj] | None = None,
             language: str = "en-IN") -> Obj:
    """One scene: the medium (its own, or a side panel's), a beat per line, the events as given."""
    total = SECONDS[fmt]
    board = items or []
    step = round((total - 1.0) / max(1, len(lines)), 3)
    scene_beats = [beat(f"{sid}-{k + 1}", k, round(0.6 + k * step, 3), round(0.6 + (k + 1) * step, 3), line,
                        board[k]["id"] if k < len(board) else None)
                   for k, line in enumerate(lines)]
    scene: Obj = {"scene_id": sid, "index": 0, "type": stype, "title": title, "start": 0.0, "duration": total,
                  "audio_offset": 0.4, "audio_duration": round(total - 0.8, 3), "board": board, "beats": scene_beats,
                  "events": events or [], "look": look(family_id, seed)}
    if subtitle is not None:
        scene["subtitle"] = subtitle
    if media is not None:
        scene["media"] = media
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


def fixtures() -> dict[str, Obj]:
    out: dict[str, Obj] = {}

    # the showcase shape: an ai_video scene, a 16:9 still with Ken Burns, title + subtitle as the lower third
    out["landscape"] = timeline(
        "landscape", "midnight_indigo", "soft", "marker", "poppins", sid="fbm-l-video", stype="ai_video",
        title="THE INVISIBLE RIVER", subtitle="Something is flowing inside your walls",
        lines=["Tonight, millions of phones will quietly drink power from a simple wall socket.",
               "But what is actually flowing behind that wall?"],
        media=still(picture(1600, 900), 1600, 900), seed=8101)

    # the board shape in the light family: a 4:3 application image (16:9 covers and crops) under wide capitals, with
    # one text item holding a [[term]] and inline TeX
    items = [{"id": "b1", "kind": "paragraph",
              "text": "Every lit window obeys the same [[law]], $I = V/R$, from Chennai to Coimbatore"}]
    out["landscape.light"] = timeline(
        "landscape", "paper_ink", "crisp", "underline", "outfit", sid="fbm-l-board", stype="content",
        title="ONE LAW, ONE GLOWING CITY", subtitle=None,
        lines=["From one small formula to an entire glowing city.",
               "Every light you see obeys the law you just mastered."],
        items=items, events=[{"t": 4.2, "kind": "reveal", "target": "b1", "anchored": True}],
        panel=image_panel("A city skyline at dusk", picture(1600, 1200), 1600, 1200), seed=8202)

    # 9:16: the 16:9 still is shown contain_blur over a blurred copy of its own frame (§4.3, §2.5)
    out["portrait"] = timeline(
        "portrait", "deep_teal", "crisp", "underline", "outfit", sid="fbm-p-video", stype="ai_video",
        title="ENGINEERS OF TOMORROW", subtitle="One formula, one glowing city",
        lines=["From one small formula to an entire glowing city.", "That is thinking like an engineer."],
        media=still(picture(1600, 900), 1600, 900), seed=8303)

    # 9:16 Indic: a Tamil label over a 9:16 picture that fills the frame exactly, a Devanagari sub-line with an
    # English [[term]] and TeX
    items = [{"id": "b1", "kind": "paragraph", "text": "[[resistance]]: $R = V/I$ का नियम"}]
    out["portrait.indic"] = timeline(
        "portrait", "mint_studio", "soft", "marker", "poppins", sid="fbm-p-indic", stype="content",
        title="மின்சாரத்தின் நதி", subtitle=None,
        lines=["மின்னோட்டம் ஒரு நதி போல ஓடுகிறது.", "यही नियम हर शहर में चलता है."],
        items=items, events=[{"t": 4.0, "kind": "reveal", "target": "b1", "anchored": True}],
        media=still(picture(1080, 1920), 1080, 1920), language="ta-IN", seed=8404)
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
