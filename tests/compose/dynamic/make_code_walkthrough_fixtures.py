"""The ``code_walkthrough`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says where they live and
what makes one valid), generated here so every value in them is the resolver's own (presentation tokens, fonts, the
personality's motion scale and text entrance, the scene tokens of a palette slot, the editor's surface) and the files
can be rebuilt and checked byte for byte::

    python -m tests.compose.dynamic.make_code_walkthrough_fixtures           # write the files
    python -m tests.compose.dynamic.make_code_walkthrough_fixtures --check   # exit 1 when a file is stale

Every scene is a ``code_walkthrough``: one ``code`` item, at most one ``heading``, and in 16:9 at most a terminal side
panel (design §4.3, deviation 13). Each look carries the editor's surface as the resolver stores it on this template's
scenes (``look.tokens["code.surface"] = themes.code_surface(family)``: the requested resolver change; drop
:data:`CODE_SURFACE_TOKEN` from :func:`_scene` if that request is declined). The background is frozen from the scene
start (R-55: a dense template, ``presentation.resolve_looks``).

* ``code_walkthrough.landscape.json`` (Deep Teal / Crisp, dark, ``baseline_reveal``, x0.85): a headed Python program
  whose ``V``, ``I`` and ``R`` take the lesson's entity colours (``presentation.entity_colors``), with a comment, a
  ``#`` inside a string, a bar on line 3 and the whole editor pointed at; then (crossfade) a short program beside a
  terminal panel that slides up on its ``output`` event (the ``output`` sync cue times its lines), a tab expanded to
  four columns, and a bar on a terminal row.
* ``code_walkthrough.landscape.light.json`` (Paper & Ink / Crisp, light, ta-IN, Poppins + Anek): a Tamil heading over
  a C program with Tamil comments (the Indic line box) on the dark editor (``content_alt``), a block comment and a bar
  on a line (``cueMode``: the outline takes the dark palette's focus).
* ``code_walkthrough.landscape.long.json`` (Midnight Indigo / Soft, dark, ``fade_rise``, x1.15): the 16:9 capacity
  edge - 17 JavaScript lines, one of exactly 60 characters, no heading - set at the 36 px floor, and a bar on the last
  line.
* ``code_walkthrough.portrait.json`` (Midnight Indigo / Soft, dark): a headed 8-line reel (the most a headed Latin
  reel holds), then the 10-line edge with a line of exactly 28 characters.
* ``code_walkthrough.portrait.hindi.json`` (Mint Studio / Soft, light, hi-IN): a Hindi heading over a 6-line program
  with a Hindi comment (the headed Indic edge) on the dark editor, its entities coloured from the dark palette.

Each file is at most about 10 s (the slow 1x / 3x / reverse gate samples its first 300 frames), so the gate sees every
scene and crossfade. The narration is placeholder text."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aadhi.compose import presentation as pr
from aadhi.compose.dynamic import faces, themes
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR

TEMPLATE = "code_walkthrough"
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
LANGS = {"en": "en-IN", "ta": "ta-IN", "hi": "hi-IN"}
# web/js/dynamic/templates/code_walkthrough.js CODE_SURFACE_TOKEN: the look token holding the editor's surface
CODE_SURFACE_TOKEN = "code.surface"
# The lesson-wide entity map (R-30): the symbols of Ohm's law, as a formula scene of the lesson would have mapped them
OHM_ENTITIES = {"V": 0, "I": 1, "R": 2}


def _presentation(fmt: str, family_id: str, personality: str, cue_style: str, display: str,
                  entities: dict[str, int] | None = None) -> dict[str, Any]:
    """The resolver's presentation (``timeline_presentation``'s fields) for a family and personality. ``display``
    other than ``outfit``: the B2 fonts form naming that display face (and ``anek`` Indic faces with ``poppins``)."""
    family = pr.family_for(family_id)
    fonts = faces.resolver_fonts(display)
    if display != fonts["display"]:
        fonts = {**fonts, "display": display, "indic": "anek"}
    out: dict[str, Any] = {
        "style": "dynamic", "format": fmt, "safe_zone": "universal", "content": pr.content_tokens(family.mode),
        "fonts": fonts, "personality": personality,
        "caption_style": "bold_outline" if fmt == "portrait" else "clean_pill",
        "cue_term_style": cue_style, "captions_burn": True, "sfx": False, "motion": "standard",
        "generated_seconds": 0.0, "engine_version": pr.ENGINE_VERSION,
        "content_alt": themes.content_alt_tokens(family.mode), "family": family.id, "mode": family.mode,
        "motion_scale": pr.motion_scale(personality, family), "text_entrance": pr.text_entrance(personality, family),
    }
    if entities:
        out["entity_colors"] = dict(entities)
    return out


def _code(item_id: str, language: str, lines: list[str]) -> dict[str, Any]:
    return {"id": item_id, "kind": "code", "text": "", "language": language, "code": "\n".join(lines)}


def _heading(item_id: str, text: str) -> dict[str, Any]:
    return {"id": item_id, "kind": "heading", "text": text}


def _beat(sid: str, k: int, start: float, length: float, text: str, item: str | None) -> dict[str, Any]:
    speech_end = round(start + length - 0.3, 3)
    return {
        "beat_id": f"{sid}-{k}", "index": k, "phase": "main", "start": round(start, 3), "speech_end": speech_end,
        "end": round(start + length, 3), "narration": text, "board_item_id": item,
        "captions": [{"start": round(start, 3), "end": speech_end, "text": text}],
    }


def _scene(spec: dict[str, Any], *, index: int, start: float, family_id: str, slot: int,
           transition: str) -> dict[str, Any]:
    """One timed scene: ``spec`` = {id, title, items, beats: [(item | None, narration, length)], hold, events,
    panel, sync_cues}."""
    sid = spec["id"]
    beats = []
    t = 0.4
    for k, (item, text, length) in enumerate(spec["beats"]):
        beats.append(_beat(sid, k, t, length, text, item))
        t += length
    duration = round(t + spec.get("hold", 1.0), 3)
    family = pr.family_for(family_id)
    seconds = pr.crossfade_seconds(duration) if transition == "crossfade" else 0.0
    tokens = {**themes.scene_tokens(family, slot), CODE_SURFACE_TOKEN: themes.code_surface(family)}
    out: dict[str, Any] = {
        "scene_id": sid, "index": index, "type": "content", "title": spec["title"],
        "start": round(start, 3), "duration": duration, "audio_offset": 0.4, "audio_duration": round(t - 0.4, 3),
        "board": spec["items"], "beats": beats, "events": spec.get("events", []),
        "look": {
            "template": TEMPLATE, "tokens": tokens,
            # a dense template: the background is frozen from the scene start (R-55, presentation.resolve_looks)
            "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": 9100 + index, "frozen_at": 0.0},
            "transition_in": {"kind": transition, "seconds": seconds},
            "hold_reason": "code",
        },
    }
    if spec.get("panel") is not None:
        out["side_panel"] = spec["panel"]
    if spec.get("sync_cues"):
        out["sync_cues"] = spec["sync_cues"]
    return out


def _timeline(fmt: str, lang: str, family_id: str, personality: str, cue_style: str, display: str,
              specs: list[dict[str, Any]], *, entities: dict[str, int] | None = None) -> Timeline:
    scenes = []
    start = 0.0
    for i, spec in enumerate(specs):
        scene = _scene(spec, index=i, start=start, family_id=family_id, slot=i,
                       transition="cut" if i == 0 else "crossfade")
        scenes.append(scene)
        start = round(start + scene["duration"], 3)
    pres = _presentation(fmt, family_id, personality, cue_style, display, entities)
    pres["generated_seconds"] = start
    width, height = SIZES[fmt]
    captions = [{"start": round(s["start"] + c["start"], 3), "end": round(s["start"] + c["end"], 3), "text": c["text"]}
                for s in scenes for b in s["beats"] for c in b["captions"]]
    data = {
        "schema_version": 3, "language": LANGS[lang], "board_language": LANGS[lang], "fps": 30, "width": width,
        "height": height, "meta": {"subject_name": "Programming for Engineers", "unit_name": "Unit 1: Basics",
                                   "session_number": "Session 2", "session_title": "First programs"},
        "total_duration": start, "chapters": [{"start": s["start"], "title": s["title"]} for s in scenes],
        "captions": captions, "presentation": pres, "scenes": scenes,
    }
    return Timeline.model_validate(data)


def _cue(t: float, kind: str, target: str, dur: float | None = None, *, anchored: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"t": t, "kind": kind, "target": target, "anchored": anchored}
    if dur is not None:
        out["dur"] = dur
    return out


def _reveal(beat_start: float, item: str) -> dict[str, Any]:
    """The ``reveal`` events.py writes for an item a beat reveals: 120 ms before the beat (R-17)."""
    return _cue(round(beat_start - 0.12, 3), "reveal", item)


# --- the scenes -----------------------------------------------------------------------------------------------------

OHM_PY = [
    "# Ohm's law: the current through a resistor",
    "V = 12.0   # volts",
    "R = 4.0    # ohms",
    "I = V / R",
    'print(f"I = {I} A  # not a comment")',
]


def _ohm() -> dict[str, Any]:
    """A headed Python program: entity identifiers, comments, a ``#`` inside a string, a bar on line 3, the editor."""
    items = [_heading("h1", "Ohm's law in Python"), _code("c1", "python", OHM_PY)]
    beats = [("h1", "Let us write Ohm's law as a program.", 1.0), ("c1", "Five short lines do it.", 2.4)]
    # beat 1 starts at 1.4: the editor enters at 1.28; Crisp's lines start 0.38 s later, one every 0.255 s, so the
    # last is in by 2.9 s; the bar and the editor's outline are 1.8 s apart (R-26: one new cue per 2 s is events.py's
    # budget for generated cues; a fixture shows both)
    events = [_reveal(1.4, "c1"), _cue(3.2, "look_here", "c1:line:3", 1.2), _cue(5.0, "look_here", "c1", 0.8)]
    return {"id": "cw-l-ohm", "title": "Ohm's law in Python", "items": items, "beats": beats, "hold": 2.2,
            "events": events}


def _terminal() -> dict[str, Any]:
    """A short program beside its terminal: the panel slides up on the ``output`` event, a tab in the code."""
    code = ["def current(v, r):", "\treturn v / r", "", "print(current(12, 4))"]
    items = [_code("c2", "python", code)]
    beats = [("c2", "Here is the same idea as a function.", 1.8), (None, "Running it prints three.", 1.6)]
    panel = {"panel": {"kind": "terminal", "title": "Run it", "rationale": "Shows what the program prints.",
                       "terminal": {"command": "python ohm.py", "output": "3.0"}},
             "media": None, "show_at": 0.0}
    sync = [{"kind": "output", "start": 2.6, "words": "prints"}]
    events = [_reveal(0.4, "c2"), _cue(2.6, "output", "panel"), _cue(3.3, "look_here", "panel:row:0", 0.8)]
    return {"id": "cw-l-term", "title": "As a function", "items": items, "beats": beats, "hold": 0.6,
            "events": events, "panel": panel, "sync_cues": sync}


SUM_C = [
    "#include <stdio.h>",
    "/* எண்களின் கூட்டுத்தொகை */",
    "int main(void) {",
    "    int sum = 0;",
    "    for (int i = 1; i <= 5; i++) {",
    "        sum += i;   // கூட்டு",
    "    }",
    '    printf("%d", sum);',
    "    return 0;",
    "}",
]


def _tamil() -> dict[str, Any]:
    """A Tamil heading over a C program with Tamil comments (the Indic line box), on the dark editor."""
    items = [_heading("h1", "சுழற்சி மூலம் கூட்டல்"), _code("c1", "c", SUM_C)]
    beats = [("h1", "ஒரு சுழற்சியைப் பார்ப்போம்.", 1.0), ("c1", "இந்த நிரல் ஐந்து எண்களைக் கூட்டுகிறது.", 4.2)]
    events = [_reveal(1.4, "c1"), _cue(4.6, "look_here", "c1:line:5", 1.2), _cue(6.6, "look_here", "c1:line:7", 0.8)]
    return {"id": "cw-ta-sum", "title": "சுழற்சி", "items": items, "beats": beats, "hold": 2.0, "events": events}


LONG_JS = [
    "// Seventeen lines: the most a 16:9 editor holds at 36 px",
    "const readings = [2.10, 2.40, 2.2, 2.6, 2.5, 2.3, 2.7, 2.8];",
    "",
    "function mean(values) {",
    "  let total = 0;",
    "  for (const v of values) {",
    "    total += v;",
    "  }",
    "  return total / values.length;",
    "}",
    "",
    "function spread(values) {",
    "  const m = mean(values);",
    "  return Math.max(...values.map((v) => Math.abs(v - m)));",
    "}",
    "",
    "console.log(mean(readings).toFixed(2), spread(readings));",
]


def _long() -> dict[str, Any]:
    items = [_code("c1", "javascript", LONG_JS)]
    beats = [("c1", "A longer program, all seventeen lines of it.", 3.0), (None, "It prints the mean and the spread.", 2.0)]
    events = [_reveal(0.4, "c1"), _cue(7.0, "look_here", "c1:line:16", 1.2)]
    return {"id": "cw-l-long", "title": "Mean and spread", "items": items, "beats": beats, "hold": 3.0,
            "events": events}


REEL_PY = [
    "nums = [3, 1, 4, 1, 5]",
    "total = 0",
    "for n in nums:",
    "    total += n",
    "",
    "# the sum of the list",
    "print(total)",
    "print(total / len(nums))",
]
TEN_PY = [
    "def is_even(n):",
    "    return n % 2 == 0",
    "",
    "evens = []",
    "for k in range(1, 11):",
    "    if is_even(k):",
    "        evens.append(k)",
    "",
    "print(evens)  # even numbers",
    "print(len(evens), 'numbers')",
]


def _reel() -> dict[str, Any]:
    items = [_heading("h1", "Sum a list"), _code("c1", "python", REEL_PY)]
    beats = [("h1", "Add up a list.", 0.9), ("c1", "Eight lines, one loop.", 2.6)]
    events = [_reveal(1.3, "c1"), _cue(4.4, "look_here", "c1:line:3", 0.8)]
    return {"id": "cw-p-sum", "title": "Sum a list", "items": items, "beats": beats, "hold": 1.4, "events": events}


def _reel_ten() -> dict[str, Any]:
    items = [_code("c1", "python", TEN_PY)]
    beats = [("c1", "Ten lines fill the reel's editor.", 3.4)]
    events = [_reveal(0.4, "c1"), _cue(4.3, "look_here", "c1:line:9", 0.6)]
    return {"id": "cw-p-ten", "title": "Even numbers", "items": items, "beats": beats, "hold": 1.2, "events": events}


HINDI_PY = [
    "# धारा की गणना",
    "V = 9",
    "R = 3",
    "I = V / R",
    "print(I)",
    "print('ओम का नियम')",
]


def _hindi() -> dict[str, Any]:
    items = [_heading("h1", "ओम का नियम"), _code("c1", "python", HINDI_PY)]
    beats = [("h1", "ओम का नियम कोड में।", 1.0), ("c1", "छह पंक्तियाँ काफ़ी हैं।", 3.0)]
    events = [_reveal(1.4, "c1"), _cue(4.2, "look_here", "c1:line:3", 1.2), _cue(6.2, "look_here", "c1", 0.8)]
    return {"id": "cw-hi-ohm", "title": "ओम का नियम", "items": items, "beats": beats, "hold": 2.8, "events": events}


def fixtures() -> dict[str, Timeline]:
    """Every code_walkthrough fixture by file name. Each fits the slow gate's 300 frames closely enough that the gate
    sees every scene, the crossfades included."""
    return {
        f"{TEMPLATE}.landscape.json": _timeline("landscape", "en", "deep_teal", "crisp", "underline", "outfit",
                                                [_ohm(), _terminal()], entities=OHM_ENTITIES),
        f"{TEMPLATE}.landscape.light.json": _timeline("landscape", "ta", "paper_ink", "crisp", "marker", "poppins",
                                                      [_tamil()]),
        f"{TEMPLATE}.landscape.long.json": _timeline("landscape", "en", "midnight_indigo", "soft", "marker", "outfit",
                                                     [_long()]),
        f"{TEMPLATE}.portrait.json": _timeline("portrait", "en", "midnight_indigo", "soft", "underline", "outfit",
                                               [_reel(), _reel_ten()]),
        f"{TEMPLATE}.portrait.hindi.json": _timeline("portrait", "hi", "mint_studio", "soft", "marker", "outfit",
                                                     [_hindi()], entities=OHM_ENTITIES),
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
            print(f"stale: {name} (run python -m tests.compose.dynamic.make_code_walkthrough_fixtures)", file=sys.stderr)
        return 1 if bad else 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, tl in fixtures().items():
        (FIXTURE_DIR / name).write_text(render(tl), "utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
