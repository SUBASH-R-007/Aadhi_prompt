"""The ``worked_steps`` template fixtures (B2; ``tests/compose/dynamic/template_fixtures.py`` says where they live and
what makes one valid), generated here - as ``list_build_fixtures.py`` does - so every value in them is the resolver's
own (presentation tokens, fonts, the personality's motion scale and text entrance, the scene tokens of a palette slot,
the lesson-wide entity map) and the files can be rebuilt and checked byte for byte::

    python -m tests.compose.dynamic.worked_steps_fixtures           # write the files
    python -m tests.compose.dynamic.worked_steps_fixtures --check   # exit 1 when a file is stale

The four timelines cover both formats in a dark and a light family each, every layout variant, both text entrances and
the three event kinds the template draws itself (``countdown``, ``fill``, ``look_here``):

* ``worked_steps.landscape.json`` (Teal / Crisp: ``baseline_reveal``, x0.85, marker): the typical 16:9 ``split``
  example - the setup line, the formula's card on the right with three entity-coloured symbols and its meaning, three
  steps with justifications, the last one ``blank`` with a think ring (``countdown``) and a ``fill`` event, and a
  ``look_here`` on ``<item>:step:<n>``.
* ``worked_steps.landscape.light.json`` (Paper / Crisp, light, Poppins): the ``centred`` variant (no formula, four
  steps, one holding a long word), then - entered by a crossfade - a two-step ``split`` scene whose blank is filled by
  a ``fill`` event without a countdown, with a ``look_here`` on the formula's symbol (``<item>:var:<symbol>``).
* ``worked_steps.portrait.json`` (Indigo / Soft: ``fade_rise``, x1.15, underline, Poppins): the 9:16 compact
  ``stack`` - a heading, the formula's card across the text column, three short steps, the last ``blank`` filled by
  its beat (``Beat.fill_item_id``) after a think ring, and a ``look_here`` on a step.
* ``worked_steps.portrait.light.json`` (Mint / Soft, light, hi-IN): a Hindi stack (Devanagari + TeX, a
  ``look_here``), then (crossfade) the 9:16 plain ``list``: two steps no beat reveals.

Each file is at most about 10 s (the slow 1x / 3x / reverse gate samples its first 300 frames), so the gate sees every
scene and crossfade; the step beats are therefore shorter than §4.3's pace and the think rings shorter than its
4-6 s. The narration is placeholder text, never the steps verbatim (R-25)."""

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

TEMPLATE = "worked_steps"
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
LANGS = {"en": "en-IN", "hi": "hi-IN"}
# The lesson-wide entity map (R-30, §4.7): the formula's symbols in the three entity slots.
ENTITIES = {"V": 0, "R": 1, "I": 2}


def _presentation(fmt: str, family_id: str, personality: str, cue_style: str, display: str) -> dict[str, Any]:
    """The resolver's presentation for a family and personality (list_build_fixtures' form, plus the entity map)."""
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
        "entity_colors": dict(ENTITIES),
    }


def _step(item_id: str, text: str, why: str | None = None, *, blank: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"id": item_id, "kind": "example_step", "text": text}
    if why is not None:
        out["justification"] = why
    if blank:
        out["blank"] = True
    return out


def _formula(item_id: str, latex: str, *, text: str = "", variables: list[tuple[str, str, str]] | None = None,
             ) -> dict[str, Any]:
    out: dict[str, Any] = {"id": item_id, "kind": "formula", "latex": latex}
    if text:
        out["text"] = text
    if variables:
        out["variables"] = [{"symbol_latex": s, "meaning": m, "unit": u} for s, m, u in variables]
    return out


def _beat(sid: str, k: int, start: float, length: float, text: str, item: str | None,
          fill: str | None = None) -> dict[str, Any]:
    speech_end = round(start + length - 0.4, 3)
    out: dict[str, Any] = {
        "beat_id": f"{sid}-{k}", "index": k, "phase": "main", "start": round(start, 3), "speech_end": speech_end,
        "end": round(start + length, 3), "narration": text, "board_item_id": item,
        "captions": [{"start": round(start, 3), "end": speech_end, "text": text}],
    }
    if fill is not None:
        out["fill_item_id"] = fill
    return out


def _cue(t: float, kind: str, target: str | None = None, dur: float | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"t": t, "kind": kind, "anchored": True}
    if target is not None:
        out["target"] = target
    if dur is not None:
        out["dur"] = dur
    return out


def _scene(spec: dict[str, Any], *, index: int, start: float, family_id: str, slot: int,
           transition: str) -> dict[str, Any]:
    """One timed scene; ``spec`` = {id, title, items, beats: [(item | None, narration, fill | None)], beat_len,
    events}. The background is frozen from the scene's start, as the resolver writes it for a dense template (R-55)."""
    sid = spec["id"]
    beats = []
    t = 0.5
    for k, entry in enumerate(spec["beats"]):
        item, text = entry[0], entry[1]
        fill = entry[2] if len(entry) > 2 else None
        beats.append(_beat(sid, k, t, spec["beat_len"], text, item, fill))
        t += spec["beat_len"]
    duration = round(t + 0.8, 3)
    family = pr.family_for(family_id)
    seconds = pr.crossfade_seconds(duration) if transition == "crossfade" else 0.0
    return {
        "scene_id": sid, "index": index, "type": "content", "title": spec["title"], "start": round(start, 3),
        "duration": duration, "audio_offset": 0.5, "audio_duration": round(t - 0.5, 3),
        "board": spec["items"], "beats": beats, "events": spec.get("events", []),
        "look": {
            "template": TEMPLATE, "tokens": themes.scene_tokens(family, slot),
            "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": 7000 + index, "frozen_at": 0.0},
            "transition_in": {"kind": transition, "seconds": seconds},
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


def _enter(beat_len: float, k: int) -> float:
    """When the item revealed by beat ``k`` enters (state.js itemTimings: 120 ms before the beat)."""
    return round(0.5 + k * beat_len - 0.12, 3)


# --- the scenes -----------------------------------------------------------------------------------------------------

OHM = "I = \\frac{V}{R}"


def _split(sid: str, beat_len: float) -> dict[str, Any]:
    """16:9 split: the setup line, the formula's card, three steps, the last one blank."""
    return {
        "id": sid, "title": "Current through a resistor", "beat_len": beat_len,
        "items": [
            {"id": "x1", "kind": "callout_info", "text": "Example: a 12 V source across a 4 ohm resistor"},
            _formula("f1", OHM, text="Ohm's law gives the current.",
                     variables=[("V", "voltage across the resistor", "V"), ("R", "resistance", "ohm"),
                                ("I", "current through it", "A")]),
            _step("s1", "$I = 12 / 4$", "from Ohm's law"),
            _step("s2", "$I = 3\\ \\mathrm{A}$", "divide the voltage by the resistance"),
            _step("s3", "$P = V I = 36\\ \\mathrm{W}$", "power is voltage times current", blank=True),
        ],
        "beats": [(None, "We know the voltage and the resistance."),
                  ("s1", "So we put both numbers into the law."),
                  ("s2", "That division gives the current."),
                  ("s3", "Now work out the power yourself. Pause here."),
                  (None, "The answer appears once you have tried it.")],
    }


def _plain(sid: str, beat_len: float) -> dict[str, Any]:
    """16:9 centred: no formula, three steps, one holding a word longer than any line (the step's own text, where the
    fit may not count it against the size: board_generic's rule)."""
    return {
        "id": sid, "title": "Three steps to the answer", "beat_len": beat_len,
        "items": [
            {"id": "h1", "kind": "heading", "text": "Three steps to the answer"},
            _step("p1", "Write what you are given", "start from the data"),
            # the long word alone: with a lead-in it wraps to a second line and the stack no longer leaves the last
            # row the height it needs (measured on the real page)
            _step("p2", "Pneumonoultramicroscopicsilicovolcanoconiosis", "one long word"),
            # short: the long word above pushes the stack down, and what is left for the last row is measured
            _step("p3", "Put the numbers in", "keep the unit"),
        ],
        "beats": [("p1", "First, write down what you are given."),
                  ("p2", "Then choose the rule that connects them."),
                  ("p3", "Substitute, and keep the unit."),
                  (None, "Finally, ask whether the answer is sensible.")],
    }


def _blank_pair(sid: str, beat_len: float) -> dict[str, Any]:
    """16:9 split entered by a crossfade: two steps, the second blank and filled by a `fill` event."""
    return {
        "id": sid, "title": "Your turn", "beat_len": beat_len,
        "items": [
            _formula("f2", "P = V I", variables=[("V", "voltage", "V"), ("I", "current", "A")]),
            _step("b1", "$P = 12 \\times 3$", "substitute both numbers"),
            _step("b2", "$P = 36\\ \\mathrm{W}$", "and the unit is the watt", blank=True),
        ],
        "beats": [("b1", "Here are the numbers again."),
                  ("b2", "What is the power? Say it out loud."),
                  (None, "Here it is.")],
    }


def _stack(sid: str, beat_len: float) -> dict[str, Any]:
    """9:16 stack: a heading, the formula's card and three short steps, the last one blank."""
    return {
        "id": sid, "title": "Find the current", "beat_len": beat_len,
        "items": [
            # no heading item: in 9:16 a heading above the formula card leaves the three steps too little room and
            # the last one overflows (measured on the real page; the scene title is not drawn by this template)
            _formula("f1", OHM, variables=[("V", "voltage", "V"), ("R", "resistance", "ohm"), ("I", "current", "A")]),
            # the 9:16 stack is tight under a heading and the formula card: short steps, short justifications
            _step("s1", "$I = 12 / 4$", "Ohm's law"),
            _step("s2", "$I = 3\\ \\mathrm{A}$", "then the unit"),
            _step("s3", "$P = 36\\ \\mathrm{W}$", "power", blank=True),
        ],
        "beats": [("s1", "Put both numbers into the law."),
                  ("s2", "That gives the current."),
                  ("s3", "Now the power. Pause and try."),
                  (None, "Here it is.", "s3")],
    }


def _hindi(sid: str, beat_len: float) -> dict[str, Any]:
    """9:16 stack in Hindi (Devanagari with TeX), no formula card."""
    return {
        "id": sid, "title": "धारा निकालिए", "beat_len": beat_len,
        "items": [
            {"id": "h1", "kind": "heading", "text": "धारा निकालिए"},
            _step("d1", "$V = 12$ वोल्ट", "दिया गया है"),
            _step("d2", "$R = 4$ ओम", "दिया गया है"),
            _step("d3", "$I = 3$ ऐम्पियर", "भाग देने पर"),
        ],
        "beats": [("d1", "पहले वोल्टता देखिए।"), ("d2", "फिर प्रतिरोध।"), ("d3", "अब भाग दीजिए।")],
    }


def _list(sid: str, beat_len: float) -> dict[str, Any]:
    """9:16 plain list: two steps no beat reveals (every step enters with the scene)."""
    return {
        "id": sid, "title": "Two checks", "beat_len": beat_len,
        "items": [
            {"id": "h2", "kind": "heading", "text": "Two checks"},
            _step("c1", "Units on both sides", "always"),
            _step("c2", "Size of the answer", "is it sensible?"),
        ],
        "beats": [(None, "Check the units."), (None, "Then the size.")],
    }


def fixtures() -> dict[str, Timeline]:
    """Every worked_steps fixture by file name (each inside the slow gate's 300 frames)."""
    land = _split("ws-l-split", 1.7)  # 9.8 s
    e3 = _enter(1.7, 3)  # the blank step's entrance
    land["events"] = [
        _cue(round(e3 + 0.2, 3), "countdown", "s3", 1.4),
        _cue(round(e3 + 1.7, 3), "fill", "s3"),
        _cue(4.3, "look_here", "x1:step:2", 1.0),
    ]
    plain = _plain("ws-l-plain", 1.2)  # 6.1 s
    pair = _blank_pair("ws-l-pair", 1.0)  # 4.3 s
    pair["events"] = [_cue(2.6, "fill", "b2"), _cue(0.9, "look_here", "f2:var:V", 1.0)]

    port = _stack("ws-p-stack", 2.0)  # 9.3 s
    p2 = _enter(2.0, 2)
    port["events"] = [_cue(round(p2 + 0.2, 3), "countdown", "s3", 1.4), _cue(1.0, "look_here", "s1", 1.2)]
    hindi = _hindi("ws-p-hindi", 1.3)  # 5.2 s
    hindi["events"] = [_cue(3.0, "look_here", "d2", 1.2)]
    plist = _list("ws-p-list", 0.9)  # 3.1 s
    return {
        f"{TEMPLATE}.landscape.json": _timeline("landscape", "en", "deep_teal", "crisp", "marker", "outfit", [land]),
        f"{TEMPLATE}.landscape.light.json": _timeline("landscape", "en", "paper_ink", "crisp", "marker", "poppins",
                                                      [plain, pair]),
        f"{TEMPLATE}.portrait.json": _timeline("portrait", "en", "midnight_indigo", "soft", "underline", "poppins",
                                               [port]),
        f"{TEMPLATE}.portrait.light.json": _timeline("portrait", "hi", "mint_studio", "soft", "marker", "outfit",
                                                     [hindi, plist]),
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
            print(f"stale: {name} (run python -m tests.compose.dynamic.worked_steps_fixtures)", file=sys.stderr)
        return 1 if bad else 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, tl in fixtures().items():
        (FIXTURE_DIR / name).write_text(render(tl), "utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
