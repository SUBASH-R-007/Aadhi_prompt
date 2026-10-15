"""The ``list_build`` template fixtures (B2c; ``tests/compose/dynamic/template_fixtures.py`` says where they live and what
makes one valid), generated here so every value in them is the resolver's own (presentation tokens, fonts, the
personality's motion scale and text entrance, the scene tokens of a palette slot, the stored icons) and the files can
be rebuilt and checked byte for byte::

    python -m tests.compose.dynamic.list_build_fixtures           # write the files
    python -m tests.compose.dynamic.list_build_fixtures --check   # exit 1 when a file is stale

The six timelines cover the cases the B2 plan §G gallery names, in both formats, in a dark and a light family each,
with Poppins as the display face in one dark and one light timeline (and Anek for the Indic text there):

* ``list_build.landscape.json`` (Teal / Crisp: ``baseline_reveal``, x0.85, marker cue): the typical list: a heading,
  four beat-revealed rows, each with a stored icon (the split layout: the current row's icon on the right), a
  ``cue_term`` and a ``look_here``.
* ``list_build.landscape.stress.json`` (Teal / Crisp): the stress rows with no beat (the centred plain list): the
  lint-limit longest row in wide capitals (R-25: 8 words), one word longer than any line, TeX beside a [[term]]; then,
  entered by a crossfade, two beat-revealed rows, one with an icon (partial icons: the list is centred and draws none,
  B2c review V12), a ``cue_term``.
* ``list_build.landscape.tamil.json`` (Paper / Crisp, light, ta-IN, Poppins + Anek): a Tamil list, then (crossfade) a
  mixed Tamil + English + TeX list, ``cue_term`` on a Tamil and on an English term, a ``look_here``.
* ``list_build.portrait.json`` (Indigo / Soft: ``fade_rise``, x1.15, underline cue, Poppins): the 9:16 compact stack:
  a heading, four rows (one entered by a ``reveal`` event), a ``cue_term`` and a ``look_here``.
* ``list_build.portrait.stress.json`` (Indigo / Soft): the stress rows as a stack (a ``cue_term`` on the TeX row), then
  (crossfade) the 9:16 plain list of two rows no beat reveals.
* ``list_build.portrait.hindi.json`` (Mint / Soft, light, hi-IN): a Hindi stack (``cue_term``, ``look_here``), then
  (crossfade) the mixed Tamil + English + TeX list as a 9:16 plain list.

Each file is at most about 10 s (the slow 1x / 3x / reverse gate samples its first 300 frames), so the gate sees
every scene and crossfade; row beats are therefore shorter than §4.3's 3-5 s per item. The narration is placeholder
text, never the rows verbatim (R-25)."""

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

TEMPLATE = "list_build"
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
LANGS = {"en": "en-IN", "ta": "ta-IN", "hi": "hi-IN"}

# The lint-limit rows (R-25: <= 8 words per item) in wide capitals, a word longer than any row is wide even at the
# R-29 floor, and TeX inside a [[term]] and beside text.
WIDE = "MAXIMUM WATTAGE WHEN WARM MOTORS MEET HEAVY LOADS"
LONG_WORD = "Pneumonoultramicroscopicsilicovolcanoconiosis is a famously long word"
TEX_ROW = "[[Ohm's law]] $V = I R$ holds when $R$ stays fixed"
# 9:16: the stack's third row is large (56 px) under two compact rows, so the longest TeX row a reel holds is shorter
# (TEX_ROW there overflowed the stack by 10 px; preflight blocks it, "Shorten text": B2c review fix round)
TEX_ROW_REEL = "[[Ohm's law]] $V = I R$ for a fixed $R$"


def _presentation(fmt: str, family_id: str, personality: str, cue_style: str, display: str) -> dict[str, Any]:
    """The resolver's presentation (``timeline_presentation``'s fields) for a family and personality. ``display``
    other than ``outfit``: the B2 fonts form naming that display face (and ``anek`` Indic faces with ``poppins``),
    which the resolver writes from B2e (the gallery shows Poppins in a dark and in a light family, plan §G)."""
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


def _item(item_id: str, kind: str, text: str, hint: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": item_id, "kind": kind, "text": text}
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


def _scene(spec: dict[str, Any], *, index: int, start: float, family_id: str, slot: int, transition: str,
           frozen: bool = False) -> dict[str, Any]:
    """One timed scene: ``spec`` = {id, title, items, beats: [(item | None, narration)], beat_len, events, reveal}."""
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
            "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": 4000 + index,
                           "frozen_at": 0.0 if frozen else None},
            "transition_in": {"kind": transition, "seconds": seconds},
            "icons": {k: v.model_dump(mode="json") for k, v in look_icons.items()},
        },
    }


def _timeline(fmt: str, lang: str, family_id: str, personality: str, cue_style: str, display: str,
              specs: list[dict[str, Any]]) -> Timeline:
    scenes = []
    start = 0.0
    for i, spec in enumerate(specs):
        scene = _scene(spec, index=i, start=start, family_id=family_id, slot=i, transition="cut" if i == 0 else "crossfade")
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


def _english_typical(sid: str, beat_len: float) -> dict[str, Any]:
    return {
        "id": sid, "title": "What a circuit needs", "beat_len": beat_len,
        "items": [
            _item("h1", "heading", "What a circuit needs"),
            _item("b1", "bullet", "A [[source]] that pushes charge", "battery"),
            _item("b2", "bullet", "A closed path for the current", "plug"),
            _item("b3", "bullet", "A [[load]] that turns energy into work", "lightbulb"),
            _item("b4", "bullet", "A switch to open the loop", "toggle-left"),
        ],
        "beats": [("b1", "First, something has to push the charge around."),
                  ("b2", "Then the charge needs a complete loop to travel."),
                  ("b3", "Somewhere in that loop, energy is used."),
                  ("b4", "And we want a way to stop it.")],
    }


def _stress(sid: str, beat_len: float, *, beats: bool, tex_row: str = TEX_ROW) -> dict[str, Any]:
    items = [_item("w1", "bullet", WIDE), _item("w2", "bullet", LONG_WORD), _item("w3", "bullet", tex_row)]
    narration = ["The longest rows a lint allows.", "One word longer than any line.", "Formulas sit inside the text."]
    return {
        "id": sid, "title": "Stress rows", "beat_len": beat_len, "items": items,
        "beats": [(i["id"] if beats else None, text) for i, text in zip(items, narration, strict=True)],
    }


def _short(sid: str, beat_len: float, *, beats: bool) -> dict[str, Any]:
    """Two rows, entered by a crossfade from the scene before (the outgoing scene is painted at its final state)."""
    items = [_item("s1", "bullet", "Charge flows from [[high]] to low", "zap"), _item("s2", "bullet", "Energy is conserved")]
    return {
        "id": sid, "title": "Two rules", "beat_len": beat_len, "items": items,
        "beats": [("s1" if beats else None, "Charge flows downhill."), ("s2" if beats else None, "Nothing is lost.")],
    }


def _tamil(sid: str, beat_len: float) -> dict[str, Any]:
    return {
        "id": sid, "title": "மின்சுற்றின் பகுதிகள்", "beat_len": beat_len,
        "items": [_item("h1", "heading", "மின்சுற்றின் பகுதிகள்"),
                  _item("t1", "bullet", "[[மின்கலம்]] மின்னூட்டத்தைத் தள்ளுகிறது", "battery"),
                  _item("t2", "bullet", "மூடிய பாதை மின்னோட்டத்தை அனுமதிக்கிறது"),
                  _item("t3", "bullet", "விளக்கு ஆற்றலைப் பயன்படுத்துகிறது", "lightbulb")],
        "beats": [("t1", "முதலில் ஒரு மின்கலம் தேவை."), ("t2", "பிறகு ஒரு மூடிய பாதை."),
                  ("t3", "இறுதியாக ஒரு விளக்கு.")],
    }


def _mixed(sid: str, beat_len: float, *, beats: bool) -> dict[str, Any]:
    """Tamil, English terms and TeX in one list (plan §G "mixed Tamil + English term + $TeX$")."""
    items = [_item("h1", "heading", "ஓம் விதி: Ohm's law"),
             _item("m1", "bullet", "மின்னழுத்தம் [[voltage]] என்பது $V$"),
             _item("m2", "bullet", "$I = V / R$ மின்னோட்டம் current"),
             _item("m3", "bullet", "மின்தடையின் அலகு ohm $\\Omega$")]
    narration = ["மின்னழுத்தம் என்றால் என்ன?", "மின்னோட்டம் இதைப் பொறுத்தது.", "மின்தடை ஓமில் அளக்கப்படுகிறது."]
    return {
        "id": sid, "title": "ஓம் விதி", "beat_len": beat_len, "items": items,
        "beats": [(i if beats else None, text) for i, text in zip(("m1", "m2", "m3"), narration, strict=True)],
    }


def _hindi(sid: str, beat_len: float) -> dict[str, Any]:
    return {
        "id": sid, "title": "विद्युत परिपथ के भाग", "beat_len": beat_len,
        "items": [_item("h1", "heading", "विद्युत परिपथ के भाग"),
                  _item("d1", "bullet", "[[बैटरी]] आवेश को धकेलती है"),
                  _item("d2", "bullet", "बंद रास्ता धारा को बहने देता है"),
                  _item("d3", "bullet", "बल्ब ऊर्जा का उपयोग करता है"),
                  _item("d4", "bullet", "स्विच परिपथ को तोड़ता है")],
        "beats": [("d1", "पहले बैटरी चाहिए।"), ("d2", "फिर एक बंद रास्ता।"), ("d3", "फिर एक बल्ब।"),
                  ("d4", "और एक स्विच।")],
    }


def _cue(t: float, kind: str, target: str, dur: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"t": t, "kind": kind, "target": target, "anchored": True}
    if dur is not None:
        out["dur"] = dur
    return out


def fixtures() -> dict[str, Timeline]:
    """Every list_build fixture by file name. Each fits the slow gate's 300 frames (10 s; the last of 10.4 s barely
    passes them), so the gate sees every scene, the crossfades included."""
    land = _english_typical("lb-l-typical", 2.1)  # 9.7 s
    land["events"] = [_cue(1.2, "cue_term", "b1", 1.4), _cue(5.0, "look_here", "b3", 1.6)]
    land_stress = _stress("lb-l-stress", 1.6, beats=False)  # 6.1 s, the centred plain list
    land_short = _short("lb-l-short", 1.4, beats=True)  # 4.1 s, centred (one icon of two), entered by a crossfade
    land_short["events"] = [_cue(0.9, "cue_term", "s1", 1.0)]
    tamil = _tamil("lb-ta-list", 1.3)  # 5.2 s
    tamil["events"] = [_cue(0.9, "cue_term", "t1", 1.2)]
    mixed_ta = _mixed("lb-ta-mixed", 1.3, beats=True)  # 5.2 s
    mixed_ta["events"] = [_cue(0.8, "cue_term", "m1", 1.0), _cue(3.3, "look_here", "m3", 1.2)]

    port = _english_typical("lb-p-typical", 2.1)  # 9.7 s
    # b2 enters on a reveal event (it wins over its beat; state.js itemTimings), a little after its beat starts
    port["events"] = [_cue(1.1, "cue_term", "b1", 1.3), _cue(2.9, "reveal", "b2"), _cue(7.0, "look_here", "b2", 1.4)]
    port_stress = _stress("lb-p-stress", 1.8, beats=True, tex_row=TEX_ROW_REEL)  # 6.7 s
    port_stress["events"] = [_cue(4.4, "cue_term", "w3", 1.2)]
    port_short = _short("lb-p-short", 1.0, beats=False)  # 3.3 s, the 9:16 plain list, entered by a crossfade
    hindi = _hindi("lb-hi-stack", 1.3)  # 6.5 s
    hindi["events"] = [_cue(0.9, "cue_term", "d1", 1.0), _cue(3.6, "look_here", "d2", 1.2)]
    mixed_hi = _mixed("lb-hi-mixed", 0.75, beats=False)  # 3.55 s, the 9:16 plain list
    return {
        f"{TEMPLATE}.landscape.json": _timeline("landscape", "en", "deep_teal", "crisp", "marker", "outfit", [land]),
        f"{TEMPLATE}.landscape.stress.json": _timeline("landscape", "en", "deep_teal", "crisp", "marker", "outfit",
                                                       [land_stress, land_short]),
        f"{TEMPLATE}.landscape.tamil.json": _timeline("landscape", "ta", "paper_ink", "crisp", "marker", "poppins",
                                                      [tamil, mixed_ta]),
        f"{TEMPLATE}.portrait.json": _timeline("portrait", "en", "midnight_indigo", "soft", "underline", "poppins",
                                               [port]),
        f"{TEMPLATE}.portrait.stress.json": _timeline("portrait", "en", "midnight_indigo", "soft", "underline",
                                                      "outfit", [port_stress, port_short]),
        f"{TEMPLATE}.portrait.hindi.json": _timeline("portrait", "hi", "mint_studio", "soft", "marker", "outfit",
                                                     [hindi, mixed_hi]),
    }


def render(tl: Timeline) -> str:
    """The file text of a fixture (``Timeline.model_dump(mode="json")``, sorted keys, 1-space indent, final newline)."""
    return json.dumps(tl.model_dump(mode="json"), ensure_ascii=False, indent=1, sort_keys=True) + "\n"


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
            print(f"stale: {name} (run python -m tests.compose.dynamic.list_build_fixtures)", file=sys.stderr)
        return 1 if bad else 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, tl in fixtures().items():
        (FIXTURE_DIR / name).write_text(render(tl), "utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
