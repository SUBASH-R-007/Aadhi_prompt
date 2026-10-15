"""The ``chapter_bumper`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says where they live and
what makes one valid), generated here so every value in them is the resolver's own (presentation tokens, fonts, the
personality's motion scale and text entrance, the scene tokens of a palette slot, the colour-block background and the
crossfade a block always gets, ``presentation.resolve_looks``) and the files can be rebuilt and checked byte for
byte::

    python -m tests.compose.dynamic.make_chapter_bumper_fixtures           # write the files
    python -m tests.compose.dynamic.make_chapter_bumper_fixtures --check   # exit 1 when a file is stale

Four timelines cover what the B2 plan §G gallery asks of a template, in both formats, in a dark and a light family
each, with Poppins as the display face in one dark and one light timeline (and Anek for the Indic text there). A
chapter card is silent (``ChapterCardScene.beats`` may be empty), so these timelines carry no narration, no caption
and no board item: a bumper draws its chapter number, its label's words and its title only.

* ``chapter_bumper.landscape.json`` (Teal / Crisp: ``baseline_reveal``, x0.85): three bumpers of a four-chapter
  lesson, each with the mini-roadmap of all four (``look.refs.roadmap``: past ticked, the current one lit) and a
  "Part n" label, so the roadmap walks done -> current -> upcoming across the file.
* ``chapter_bumper.landscape.light.json`` (Paper / Crisp, light, ta-IN, Poppins + Anek): a Tamil chapter title with a
  Tamil label ("அத்தியாயம் 2"), then a bumper whose label holds no digits and whose look has no roadmap (no numeral:
  the label is drawn whole as the kicker), then the longest title a lint allows in wide capitals (the fit's stress).
* ``chapter_bumper.portrait.json`` (Indigo / Soft: ``fade_rise``, x1.15, Poppins): the 9:16 bumper (numeral 240 px,
  everything centred); its look carries a roadmap, which 9:16 does not draw but still numbers the chapter from.
* ``chapter_bumper.portrait.hindi.json`` (Mint / Soft, light, hi-IN): a Hindi bumper and one with a two-line title.

Every file stays under 10 s, so the slow 1x / 3x / reverse gate (300 frames) sees every scene and every crossfade;
the scene lengths are §4.3's (2-3 s in 16:9, 1.2-1.8 s in 9:16).
"""

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

TEMPLATE = "chapter_bumper"
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
LANGS = {"en": "en-IN", "ta": "ta-IN", "hi": "hi-IN"}

# The four chapters of the showcase lesson: the mini-roadmap of §4.3 (a bumper lists every chapter).
CHAPTERS = ("What a circuit needs", "Ohm's law", "Power in a resistor", "Measuring in the lab")
# The longest title a lint allows (R-25: <= 8 words) in wide capitals: the title fit's stress case.
WIDE_TITLE = "MAXIMUM WATTAGE WHEN WARM MOTORS MEET HEAVY LOADS"


def _presentation(fmt: str, family_id: str, personality: str, display: str) -> dict[str, Any]:
    """The resolver's presentation (``timeline_presentation``'s fields) for a family and personality. ``display``
    other than ``outfit``: the B2 fonts form naming that display face (and ``anek`` Indic faces), as the resolver
    writes it from B2e (plan §G shows Poppins in a dark and in a light family)."""
    family = pr.family_for(family_id)
    fonts = faces.resolver_fonts(display)
    if display != fonts["display"]:
        fonts = {**fonts, "display": display, "indic": "anek"}
    return {
        "style": "dynamic", "format": fmt, "safe_zone": "universal", "content": pr.content_tokens(family.mode),
        "fonts": fonts, "personality": personality, "caption_style": "clean_pill",
        "cue_term_style": "marker", "captions_burn": True, "sfx": False, "motion": "standard",
        "generated_seconds": 0.0, "engine_version": pr.ENGINE_VERSION,
        "content_alt": themes.content_alt_tokens(family.mode), "family": family.id, "mode": family.mode,
        "motion_scale": pr.motion_scale(personality, family), "text_entrance": pr.text_entrance(personality, family),
    }


def _roadmap(current: int) -> list[dict[str, str]]:
    """``look.refs.roadmap`` with chapter ``current`` (1-based) lit, the ones before it ticked (§4.3)."""
    out = []
    for i, title in enumerate(CHAPTERS, start=1):
        out.append({"title": title, "state": "done" if i < current else "current" if i == current else "upcoming"})
    return out


def _scene(spec: dict[str, Any], *, index: int, start: float, family_id: str, slot: int, transition: str) -> dict[str, Any]:
    """One timed chapter card: ``spec`` = {id, title, label, duration, roadmap}. The look is what
    ``presentation.resolve_looks`` writes for a chapter card (a colour block, a crossfade in; R-54), plus the
    cross-scene ``refs`` the B2e resolver denormalises."""
    family = pr.family_for(family_id)
    duration = float(spec["duration"])
    look: dict[str, Any] = {
        "template": TEMPLATE, "tokens": themes.scene_tokens(family, slot),
        "background": {"preset": "colour_block", "params": {"tone": "bg"}, "seed": 5000 + index, "frozen_at": None},
        "transition_in": {"kind": transition,
                          "seconds": pr.crossfade_seconds(duration) if transition == "crossfade" else 0.0},
    }
    if spec.get("roadmap"):
        look["refs"] = {"roadmap": spec["roadmap"]}
    return {
        "scene_id": spec["id"], "index": index, "type": "chapter_card", "title": spec["title"],
        "chapter_label": spec["label"], "start": round(start, 3), "duration": round(duration, 3),
        "audio_offset": 0.0, "audio_duration": 0.0, "board": [], "beats": [], "events": [], "look": look,
    }


def _timeline(fmt: str, lang: str, family_id: str, personality: str, display: str,
              specs: list[dict[str, Any]]) -> Timeline:
    scenes = []
    start = 0.0
    for i, spec in enumerate(specs):
        scene = _scene(spec, index=i, start=start, family_id=family_id, slot=i + 1,
                       transition="cut" if i == 0 else "crossfade")
        scenes.append(scene)
        start = round(start + scene["duration"], 3)
    pres = _presentation(fmt, family_id, personality, display)
    pres["generated_seconds"] = start
    width, height = SIZES[fmt]
    data = {
        "schema_version": 3, "language": LANGS[lang], "board_language": LANGS[lang], "fps": 30, "width": width,
        "height": height, "meta": {"subject_name": "Basic Electrical Engineering", "unit_name": "Unit 2: DC Circuits",
                                   "session_number": "Session 3", "session_title": "Circuits"},
        "total_duration": start, "chapters": [{"start": s["start"], "title": s["title"]} for s in scenes],
        "captions": [], "presentation": pres, "scenes": scenes,
    }
    return Timeline.model_validate(data)


def _bumper(sid: str, title: str, label: str, duration: float, *, current: int | None = None) -> dict[str, Any]:
    return {"id": sid, "title": title, "label": label, "duration": duration,
            "roadmap": _roadmap(current) if current is not None else None}


def fixtures() -> dict[str, Timeline]:
    """Every chapter_bumper fixture by file name (each under 10 s: the slow gate's 300 frames)."""
    land = [_bumper("cb-l-1", CHAPTERS[0], "Part 1", 2.4, current=1),
            _bumper("cb-l-2", CHAPTERS[1], "Part 2", 2.4, current=2),
            _bumper("cb-l-4", CHAPTERS[3], "Part 4", 3.0, current=4)]
    light = [_bumper("cb-ta-1", "மின்சுற்றின் பகுதிகள்", "அத்தியாயம் 2", 2.4, current=2),
             # no digits in the label and no roadmap: the label is the kicker and the bumper has no numeral
             _bumper("cb-ta-2", "ஓம் விதி: Ohm's law", "தொடர்ச்சி", 2.4),
             _bumper("cb-l-wide", WIDE_TITLE, "Part 3", 3.0, current=3)]
    port = [_bumper("cb-p-1", CHAPTERS[1], "Part 2", 1.5, current=2),
            _bumper("cb-p-2", CHAPTERS[2], "Part 3", 1.8, current=3)]
    hindi = [_bumper("cb-hi-1", "विद्युत परिपथ के भाग", "भाग 1", 1.5, current=1),
             _bumper("cb-hi-2", "प्रतिरोध में शक्ति और ऊर्जा का मापन", "भाग 3", 1.8, current=3)]
    return {
        f"{TEMPLATE}.landscape.json": _timeline("landscape", "en", "deep_teal", "crisp", "outfit", land),
        f"{TEMPLATE}.landscape.light.json": _timeline("landscape", "ta", "paper_ink", "crisp", "poppins", light),
        f"{TEMPLATE}.portrait.json": _timeline("portrait", "en", "midnight_indigo", "soft", "poppins", port),
        f"{TEMPLATE}.portrait.hindi.json": _timeline("portrait", "hi", "mint_studio", "soft", "outfit", hindi),
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
            print(f"stale: {name} (run python -m tests.compose.dynamic.make_chapter_bumper_fixtures)", file=sys.stderr)
        return 1 if bad else 0
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, tl in fixtures().items():
        (FIXTURE_DIR / name).write_text(render(tl), "utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
