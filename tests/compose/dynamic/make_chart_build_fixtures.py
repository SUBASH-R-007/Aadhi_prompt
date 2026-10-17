"""Write the ``chart_build`` template fixtures (``tests/compose/dynamic/template_fixtures.py`` says what a template
fixture is): ``tests/compose/dynamic/fixtures/templates/chart_build.<format>[.<case>].json``.

Each fixture is one scene drawn by ``chart_build`` (16:9 10 s, 9:16 8 s: inside the template's §4.3 timing of 8-20 s
and inside the slow 1x / 3x / reverse gate's first 300 frames), with the resolver's own values (presentation tokens,
fonts, the personality's motion scale and text entrance, the scene tokens of a palette slot). Nothing is fetched: the
chart is drawn by the page (Chart.js marks, DOM labels), so no medium is needed.

The cases cover what the B2 plan §G gallery asks - both formats, a dark and a light family each, Poppins as the
display face in one dark and one light timeline (with Anek for the Indic text), both text entrances and both cue
styles, Latin and two Indic scripts - and every kind ``_charts.js`` draws in each format:

* ``chart_build.landscape.json`` (Midnight Indigo / Soft: ``fade_rise``, x1.15, marker; Poppins): the showcase's own
  chart (s13's current draw of five appliances) as a one-series bar chart under a heading, with the chart's title as
  the caption; the narration names the AC and the geyser, whose bars grow on those beats (``draw`` events), and a
  ``look_here`` points at the geyser's bar.
* ``chart_build.landscape.light.json`` (Paper & Ink / Crisp: ``baseline_reveal``, x0.85, underline): a two-series
  line chart (copper and nichrome resistance as they heat) with both axis titles and the scene title as the headline;
  the lesson's entity map gives Nichrome ``entity:1`` (R-30), so Copper takes ``entity:0``; the nichrome line starts on
  its own beat and a ``look_here`` rings its last point.
* ``chart_build.landscape.graph.json`` (Deep Teal / Crisp, dark): a graph (two lines of Ohm's law, ``x/2`` and
  ``x/4``, with two marked points) under a heading holding inline TeX; a ``draw`` on ``panel`` starts the build and a
  ``look_here`` rings a marked point.
* ``chart_build.portrait.json`` (Deep Teal / Crisp: ``baseline_reveal``, underline): a one-series bar chart drawn as
  the 9:16 bar list, both axis titles in the header row, two bars revealed on their beats.
* ``chart_build.portrait.light.json`` (Mint Studio / Soft, light, ta-IN, Poppins + Anek): a Tamil headline over a line
  chart of five years (the values counted on the points), with a Tamil value-axis title.
* ``chart_build.portrait.graph.json`` (Midnight Indigo / Soft, dark, hi-IN): a parabola with a marked point under a
  Devanagari headline.

Run ``python -m tests.compose.dynamic.make_chart_build_fixtures`` to write them, ``--check`` to compare (exit 1 on a
difference).
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

TEMPLATE = "chart_build"
SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}
SECONDS = {"landscape": 10.0, "portrait": 8.0}

Obj = dict[str, Any]


def presentation(fmt: str, family_id: str, personality: str, cue_style: str, display: str, total: float,
                 entity_colors: dict[str, int] | None = None) -> Obj:
    """The resolver's presentation for a family and personality (``timeline_presentation``'s fields). ``display``
    other than ``outfit``: the B2 fonts form naming that display face (with ``anek`` Indic faces), which the resolver
    writes from B2e; the gallery wants Poppins in a dark and in a light family (plan §G)."""
    family = pr.family_for(family_id)
    fonts = faces.resolver_fonts(display)
    if display != fonts["display"]:
        fonts = {**fonts, "display": display, "indic": "anek"}
    out: Obj = {
        "style": "dynamic", "format": fmt, "safe_zone": "universal", "content": pr.content_tokens(family.mode),
        "fonts": fonts, "personality": personality, "caption_style": "bold_outline" if fmt == "portrait" else "clean_pill",
        "cue_term_style": cue_style, "captions_burn": True, "sfx": False, "motion": "standard",
        "generated_seconds": total, "engine_version": pr.ENGINE_VERSION,
        "content_alt": themes.content_alt_tokens(family.mode), "family": family.id, "mode": family.mode,
        "motion_scale": pr.motion_scale(personality, family), "text_entrance": pr.text_entrance(personality, family),
    }
    if entity_colors:
        out["entity_colors"] = dict(entity_colors)
    return out


def look(family_id: str, seed: int) -> Obj:
    """The scene's look: this template, no variant (§4.3 gives it none), the family's scene tokens, a diagram hold."""
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


def chart_panel(title: str, chart_type: str, labels: list[str], datasets: list[tuple[str, list[float]]],
                x_label: str | None = None, y_label: str | None = None) -> Obj:
    spec: Obj = {"chart_type": chart_type, "labels": labels,
                 "datasets": [{"label": name, "data": data} for name, data in datasets]}
    if x_label is not None:
        spec["x_label"] = x_label
    if y_label is not None:
        spec["y_label"] = y_label
    return {"panel": {"kind": "chart", "title": title, "rationale": "Compares the quantities at a glance.",
                      "chart": spec}, "show_at": 0.0}


def graph_panel(title: str, functions: list[tuple[str, str | None]], points: list[tuple[float, float, str]],
                x_range: tuple[float, float], y_range: tuple[float, float] | None,
                x_label: str | None = None, y_label: str | None = None) -> Obj:
    spec: Obj = {"functions": [{"expr": e, **({"label": lb} if lb else {})} for e, lb in functions],
                 "points": [{"x": x, "y": y, "label": lb} for x, y, lb in points], "x_range": list(x_range)}
    if y_range is not None:
        spec["y_range"] = list(y_range)
    if x_label is not None:
        spec["x_label"] = x_label
    if y_label is not None:
        spec["y_label"] = y_label
    return {"panel": {"kind": "graph", "title": title, "rationale": "Shows the law as a line.", "graph": spec},
            "show_at": 0.0}


def timeline(fmt: str, family_id: str, personality: str, cue_style: str, display: str, *, sid: str, title: str,
             lines: list[str], panel: Obj, seed: int, heading: str | None = None, events: list[Obj] | None = None,
             language: str = "en-IN", entity_colors: dict[str, int] | None = None) -> Obj:
    """One scene: the chart panel, an optional heading revealed by the first beat, a beat per line, the events."""
    total = SECONDS[fmt]
    board = [{"id": "h1", "kind": "heading", "text": heading}] if heading else []
    step = round((total - 1.0) / max(1, len(lines)), 3)
    scene_beats = [beat(f"{sid}-{k + 1}", k, round(0.4 + k * step, 3), round(0.4 + (k + 1) * step, 3), line,
                        "h1" if heading and k == 0 else None)
                   for k, line in enumerate(lines)]
    scene: Obj = {"scene_id": sid, "index": 0, "type": "content", "title": title, "start": 0.0, "duration": total,
                  "audio_offset": 0.4, "audio_duration": round(total - 0.8, 3), "board": board, "beats": scene_beats,
                  "events": events or [], "look": look(family_id, seed), "side_panel": panel,
                  "layout": {"show_side_panel": True}}
    width, height = SIZES[fmt]
    data = {
        "schema_version": 3, "language": language, "board_language": language, "fps": 30, "width": width,
        "height": height, "total_duration": total, "chapters": [{"start": 0.0, "title": title}],
        "captions": [c for b in scene_beats for c in b["captions"]], "transition_seconds": 0.4, "scenes": [scene],
        "presentation": presentation(fmt, family_id, personality, cue_style, display, total, entity_colors),
    }
    return Timeline.model_validate(data).model_dump(mode="json")


def ev(t: float, kind: str, target: str, dur: float | None = None, anchored: bool = True) -> Obj:
    out: Obj = {"t": t, "kind": kind, "target": target, "anchored": anchored}
    if dur is not None:
        out["dur"] = dur
    return out


def fixtures() -> dict[str, Obj]:
    out: dict[str, Obj] = {}

    # the showcase's own chart: five appliances, one series, the AC and the geyser named on their beats
    out["landscape"] = timeline(
        "landscape", "midnight_indigo", "soft", "marker", "poppins", sid="cb-l-bar",
        title="THE LAW THAT POWERS TAMIL NADU", heading="Which appliance pulls the most current?",
        lines=["An LED bulb sips a tiny current, and a fan takes a little more.",
               "A fridge needs more again, but the AC pulls seven amperes.",
               "And the geyser? Fifteen amperes: the hungriest of them all."],
        panel=chart_panel("Typical current draw (amperes)", "bar", ["LED Bulb", "Fan", "Fridge", "AC", "Geyser"],
                          [("Current (A)", [0.04, 0.3, 1.5, 7.0, 15.0])]),
        events=[ev(4.3, "draw", "panel:bar:0:3"), ev(6.6, "draw", "panel:bar:0:4"),
                ev(8.2, "look_here", "panel:bar:0:4", dur=1.6)],
        seed=9101)

    # two series that heat up: an entity map entry for Nichrome (R-30), both axis titles, the scene title as headline
    out["landscape.light"] = timeline(
        "landscape", "paper_ink", "crisp", "underline", "outfit", sid="cb-l-line",
        title="WHY HOT WIRES RESIST MORE",
        lines=["Heat a copper wire and its resistance climbs slowly.",
               "Now nichrome: it starts far higher and keeps climbing.",
               "That is why heaters are made of nichrome."],
        panel=chart_panel("Resistance of two wires as they heat", "line", ["0°C", "25°C", "50°C", "75°C", "100°C"],
                          [("Copper", [1.0, 1.1, 1.2, 1.3, 1.4]), ("Nichrome", [6.0, 6.1, 6.2, 6.3, 6.5])],
                          x_label="Temperature", y_label="Resistance (ohms)"),
        events=[ev(3.4, "draw", "panel:point:1:0"), ev(7.6, "look_here", "panel:point:1:4", dur=1.8)],
        entity_colors={"Nichrome": 1}, seed=9202)

    # a graph: two straight lines of Ohm's law and two marked points, a heading with inline TeX
    out["landscape.graph"] = timeline(
        "landscape", "deep_teal", "crisp", "underline", "outfit", sid="cb-l-graph",
        title="OHM'S LAW AS A PICTURE", heading="For a fixed resistor, $I = V/R$ is a straight line",
        lines=["Double the voltage and the current doubles too.",
               "A bigger resistor gives a flatter line.",
               "At eight volts the two-ohm resistor carries four amperes."],
        panel=graph_panel("Current against voltage", [("x/2", "R = 2 ohms"), ("x/4", "R = 4 ohms")],
                          [(4.0, 2.0, "4 V, 2 A"), (8.0, 4.0, "8 V, 4 A")], (0.0, 10.0), (0.0, 5.0),
                          x_label="Voltage (V)", y_label="Current (A)"),
        events=[ev(0.6, "draw", "panel"), ev(7.4, "look_here", "panel:point:2:1", dur=1.8)],
        seed=9303)

    # 9:16: the bar list, both axis titles in the header row, two bars on their beats
    out["portrait"] = timeline(
        "portrait", "deep_teal", "crisp", "underline", "outfit", sid="cb-p-bar",
        title="CURRENT AT HOME", heading="Who pulls the most current?",
        lines=["A fan and a fridge take only a little.", "The AC pulls seven amperes.",
               "The geyser takes fifteen!"],
        panel=chart_panel("Typical current draw", "bar", ["Fan", "Fridge", "AC", "Geyser"],
                          [("", [0.3, 1.5, 7.0, 15.0])], x_label="Appliance", y_label="Current (A)"),
        events=[ev(2.8, "draw", "panel:bar:0:2"), ev(5.2, "draw", "panel:bar:0:3"),
                ev(6.6, "look_here", "panel:bar:0:3", dur=1.2)],
        seed=9404)

    # 9:16 Tamil: a line of five years with its values counted on the points
    out["portrait.light"] = timeline(
        "portrait", "mint_studio", "soft", "marker", "poppins", sid="cb-p-line",
        title="தமிழ்நாட்டின் சூரிய மின்சாரம்",
        lines=["ஒவ்வொரு ஆண்டும் சூரிய மின்சாரம் வளர்கிறது.", "ஐந்து ஆண்டுகளில் இரண்டு மடங்கு!"],
        panel=chart_panel("சூரிய மின் திறன்", "line", ["2019", "2020", "2021", "2022", "2023"],
                          [("", [3.9, 4.4, 5.1, 6.5, 7.4])], y_label="ஜிகாவாட்"),
        language="ta-IN", seed=9505)

    # 9:16 Devanagari: a parabola and one marked point
    out["portrait.graph"] = timeline(
        "portrait", "midnight_indigo", "soft", "marker", "outfit", sid="cb-p-graph",
        title="परवलय का आकार",
        lines=["x का वर्ग हमेशा धनात्मक होता है।", "x = 2 पर y = 4 होता है।"],
        panel=graph_panel("y = x^2", [("x^2", None)], [(2.0, 4.0, "(2, 4)")], (-3.0, 3.0), None,
                          x_label="x", y_label="y"),
        events=[ev(5.0, "look_here", "panel:point:1:0", dur=1.6)],
        language="hi-IN", seed=9606)
    return out


def render(data: Obj) -> str:
    """A fixture's exact text."""
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def files() -> dict[Path, str]:
    """Every fixture's path and its exact text."""
    return {FIXTURE_DIR / f"{TEMPLATE}.{case}.json": render(data) for case, data in fixtures().items()}


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
