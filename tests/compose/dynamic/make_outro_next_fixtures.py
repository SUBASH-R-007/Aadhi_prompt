"""Write the ``outro_next`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says where they live and
what makes one valid): ``tests/compose/dynamic/fixtures/templates/outro_next.<format>[.<case>].json``.

Every value in them is the resolver's own (presentation tokens, fonts, the personality's motion scale and text
entrance, the scene tokens of a palette slot, the crossfade length) and every scene's visual events are what
``events.scene_events`` writes for the template's ``consumes`` (the ``reveal`` of the statement's item, 120 ms before
its beat, R-17), so the files can be rebuilt and checked byte for byte::

    python -m tests.compose.dynamic.make_outro_next_fixtures           # write the files
    python -m tests.compose.dynamic.make_outro_next_fixtures --check   # exit 1 when a file is stale

An outro (§4.3, R-11) is a board scene with at most one takeaway, paragraph or heading - the statement - and the next
step in ``look.refs.next`` (plan §1.2, denormalised by the resolver; the next part's title in a series). Four timelines
cover what the B2 plan §G gallery asks of a template: both formats, a dark and a light family in each, both text
entrances (Crisp ``baseline_reveal``, Soft ``fade_rise``), Poppins in a dark and a light timeline (with Anek for the
Indic text), Latin, Tamil and Devanagari, inline TeX, and every layout the template draws:

* ``outro_next.landscape.json`` (Deep Teal / Crisp, en-IN): the ``next_up`` split - a takeaway with inline TeX under
  the scene title (the kicker), the "Next up" card on the right (``refs.next``); then an outro with no item and no
  next step (the scene title is the statement, set alone in ``centre-60``).
* ``outro_next.landscape.endscreen.json`` (Paper & Ink / Soft, light, ta-IN, Poppins + Anek): the ``end_screen``
  variant, exactly ``Duration.end_screen`` (15 s) long: a Tamil takeaway and the next card stacked in the left half,
  the right half clean (§2.5).
* ``outro_next.portrait.json`` (Midnight Indigo / Soft, Poppins): the reel outro - the call to action (the next card,
  <= 3 s, R-11) below the loop line in the hook band; then a 3 s loop line with no title and no next step.
* ``outro_next.portrait.hindi.json`` (Mint Studio / Crisp, light, hi-IN): a Hindi call to action and loop line, then a
  3 s outro whose call to action leaves at 2.0 s (``ctaEnd``: LOOP_HOLD before the end).

The 1x / 3x / reverse gate renders a fixture's first 300 frames: every scene starts inside them and every movement of
the template (entrances, the call to action's exit) ends inside them (the end-screen file's 15 s scene is still from
about 2 s on). Scene lengths are §4.3's: 6-10 s or exactly 15 s in 16:9, 3-5 s in 9:16.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aadhi.compose import presentation as pr
from aadhi.compose.dynamic import events as ev
from aadhi.compose.dynamic import faces, themes
from aadhi.compose.dynamic import templates as tp
from aadhi.schemas.timeline import TimedScene, Timeline
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR

TEMPLATE = "outro_next"
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}

Obj = dict[str, Any]


def presentation(fmt: str, family_id: str, personality: str, display: str, total: float) -> Obj:
    """The resolver's presentation for a family and personality (``timeline_presentation``'s fields). ``display``
    other than ``outfit``: the B2 fonts form naming that display face (with ``anek`` Indic faces), which the resolver
    writes from B2e (plan §G wants Poppins in a dark and in a light family)."""
    family = pr.family_for(family_id)
    fonts = faces.resolver_fonts(display)
    if display != fonts["display"]:
        fonts = {**fonts, "display": display, "indic": "anek"}
    return {
        "style": "dynamic", "format": fmt, "safe_zone": "universal", "content": pr.content_tokens(family.mode),
        "fonts": fonts, "personality": personality,
        "caption_style": "bold_outline" if fmt == "portrait" else "clean_pill",
        "cue_term_style": "marker", "captions_burn": True, "sfx": False, "motion": "standard",
        "generated_seconds": total, "engine_version": pr.ENGINE_VERSION,
        "content_alt": themes.content_alt_tokens(family.mode), "family": family.id, "mode": family.mode,
        "motion_scale": pr.motion_scale(personality, family), "text_entrance": pr.text_entrance(personality, family),
    }


def beat(bid: str, index: int, start: float, end: float, text: str, item: str | None) -> Obj:
    """A narrated beat (its caption is the narration), revealing ``item`` when given."""
    speech_end = round(end - 0.3, 3)
    return {"beat_id": bid, "index": index, "phase": "main", "start": start, "speech_end": speech_end, "end": end,
            "narration": text, "board_item_id": item, "captions": [{"start": start, "end": speech_end, "text": text}]}


def scene(spec: Obj, *, index: int, start: float, family_id: str, seed: int) -> Obj:
    """One timed outro: ``spec`` = {id, title, duration, item (or None), beats [(start, end, text, reveals item)],
    next (or None), variant (or None)}. The look is what ``presentation.resolve_looks`` writes (``mesh_drift``, the
    tone step, a cut into the first scene and a crossfade at a chapter boundary) plus the ``refs.next`` the resolver
    denormalises for an outro."""
    family = pr.family_for(family_id)
    duration = float(spec["duration"])
    item = spec.get("item")
    beats = [beat(f"{spec['id']}-b{k + 1}", k, a, b, text, item["id"] if reveals and item else None)
             for k, (a, b, text, reveals) in enumerate(spec["beats"])]
    transition = "cut" if index == 0 else "crossfade"
    look: Obj = {
        "template": TEMPLATE, "tokens": themes.scene_tokens(family, index),
        "background": {"preset": "mesh_drift", "params": {"tone": "bg"}, "seed": seed, "frozen_at": None},
        "transition_in": {"kind": transition,
                          "seconds": pr.crossfade_seconds(duration) if transition == "crossfade" else 0.0},
    }
    if spec.get("variant"):
        look["variant"] = spec["variant"]
    if spec.get("next"):
        look["refs"] = {"next": {"title": spec["next"]}}
    return {
        "scene_id": spec["id"], "index": index, "type": "summary", "title": spec["title"], "start": round(start, 3),
        "duration": round(duration, 3), "audio_offset": 0.5, "audio_duration": round(beats[-1]["speech_end"] - 0.5, 3),
        "board": [item] if item else [], "beats": beats, "events": [], "look": look,
    }


def timeline(fmt: str, lang: str, family_id: str, personality: str, display: str, specs: list[Obj], *,
             seed: int) -> Timeline:
    """The fixture timeline: one chapter per scene (so a later scene is entered by a crossfade, the resolver's
    chapter-boundary rule), the captions of every beat, and each scene's events from ``events.scene_events``."""
    scenes: list[Obj] = []
    start = 0.0
    for i, spec in enumerate(specs):
        scenes.append(scene(spec, index=i, start=start, family_id=family_id, seed=seed + i))
        start = round(start + float(spec["duration"]), 3)
    width, height = SIZES[fmt]
    data = {
        "schema_version": 3, "language": lang, "board_language": lang, "fps": 30, "width": width, "height": height,
        "meta": {"subject_name": "Basic Electrical Engineering", "unit_name": "Unit 2: DC Circuits",
                 "session_number": "Session 3", "session_title": "Ohm's law"},
        "total_duration": start, "chapters": [{"start": s["start"], "title": s["title"] or s["scene_id"]} for s in scenes],
        "captions": [{**c, "start": round(s["start"] + c["start"], 3), "end": round(s["start"] + c["end"], 3)}
                     for s in scenes for b in s["beats"] for c in b["captions"]],
        "transition_seconds": 0.4, "scenes": scenes,
        "presentation": presentation(fmt, family_id, personality, display, start),
    }
    tl = Timeline.model_validate(data)
    accepts = tp.TEMPLATES[TEMPLATE].consumes.accepts
    for s in tl.scenes:
        s.events = ev.scene_events(TimedScene.model_validate(s.model_dump()), accepts=accepts)
    return Timeline.model_validate(tl.model_dump(mode="json"))


def _item(iid: str, kind: str, text: str) -> Obj:
    return {"id": iid, "kind": kind, "text": text}


def fixtures() -> dict[str, Timeline]:
    """Every outro_next fixture by file name."""
    landscape = [
        {"id": "on-l-split", "title": "What we learned", "duration": 6.0, "variant": "next_up",
         "item": _item("on-l-i1", "takeaway",
                       "Voltage pushes, current flows, resistance opposes: $V = IR$ ties all three together."),
         "beats": [(0.5, 3.4, "Voltage pushes, current flows, resistance opposes, and V equals I R ties them.", True),
                   (3.4, 5.6, "Next time: series and parallel circuits.", False)],
         "next": "Part 2: Series and parallel circuits"},
        # no item and no next step: the scene title is the statement, set alone in the centre
        {"id": "on-l-title", "title": "Keep the current flowing", "duration": 6.0, "variant": "next_up",
         "item": None, "beats": [(0.5, 5.4, "Until then, keep the current flowing.", False)], "next": None},
    ]
    endscreen = [
        {"id": "on-ta-end", "title": "நாம் கற்றது", "duration": float(tp.TEMPLATES[TEMPLATE].duration["landscape"].end_screen or 0),
         "variant": "end_screen",
         "item": _item("on-ta-i1", "takeaway",
                       "மின்னழுத்தம் தள்ளுகிறது, மின்னோட்டம் பாய்கிறது, மின்தடை எதிர்க்கிறது."),
         "beats": [(0.5, 6.0, "மின்னழுத்தம் தள்ளுகிறது, மின்னோட்டம் பாய்கிறது, மின்தடை எதிர்க்கிறது.", True),
                   (6.0, 14.0, "அடுத்த பகுதியில் தொடர் மற்றும் இணைச் சுற்றுகள்.", False)],
         "next": "பகுதி 2: தொடர் மற்றும் இணைச் சுற்றுகள்"},
    ]
    portrait = [
        {"id": "on-p-cta", "title": "That's a wrap", "duration": 4.0, "variant": None,
         "item": _item("on-p-i1", "paragraph", "So: can a thin wire really stop a current?"),
         "beats": [(0.5, 1.6, "Next up, series circuits.", False),
                   (1.6, 3.6, "So: can a thin wire really stop a current?", True)],
         "next": "Part 2: Series circuits"},
        # the shortest reel outro: no title (no kicker) and no next step (no call to action), the loop line alone
        {"id": "on-p-loop", "title": "", "duration": 3.0, "variant": None,
         "item": _item("on-p-i2", "takeaway", "Watch again: can a wire stop current?"),
         "beats": [(0.5, 2.6, "Watch again: can a wire stop current?", True)], "next": None},
    ]
    hindi = [
        {"id": "on-hi-cta", "title": "आज बस इतना", "duration": 5.0, "variant": None,
         "item": _item("on-hi-i1", "paragraph", "तो, क्या एक पतला तार धारा को रोक सकता है?"),
         "beats": [(0.5, 1.8, "अगला भाग: श्रेणी परिपथ।", False),
                   (1.8, 4.6, "तो, क्या एक पतला तार धारा को रोक सकता है?", True)],
         "next": "भाग 2: श्रेणी परिपथ"},
        # a 3 s outro: the call to action leaves at 2.0 s (LOOP_HOLD before the end), the loop line stays
        {"id": "on-hi-short", "title": "", "duration": 3.0, "variant": None,
         "item": _item("on-hi-i2", "takeaway", "फिर से देखिए: धारा क्यों बहती है?"),
         "beats": [(0.5, 2.6, "फिर से देखिए: धारा क्यों बहती है?", True)], "next": "भाग 3: समानांतर परिपथ"},
    ]
    return {
        f"{TEMPLATE}.landscape.json": timeline("landscape", "en-IN", "deep_teal", "crisp", "outfit", landscape,
                                               seed=8101),
        f"{TEMPLATE}.landscape.endscreen.json": timeline("landscape", "ta-IN", "paper_ink", "soft", "poppins",
                                                         endscreen, seed=8201),
        f"{TEMPLATE}.portrait.json": timeline("portrait", "en-IN", "midnight_indigo", "soft", "poppins", portrait,
                                              seed=8301),
        f"{TEMPLATE}.portrait.hindi.json": timeline("portrait", "hi-IN", "mint_studio", "crisp", "outfit", hindi,
                                                    seed=8401),
    }


def render(tl: Timeline) -> str:
    """The file text of a fixture (``Timeline.model_dump(mode="json")``, sorted keys, 1-space indent, final
    newline), as the other template generators write theirs."""
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
            print(f"stale: {name} (run python -m tests.compose.dynamic.make_outro_next_fixtures)", file=sys.stderr)
        return 1 if bad else 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, tl in fixtures().items():
        (FIXTURE_DIR / name).write_text(render(tl), "utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
