"""Write the ``formula_focus`` template fixtures (B2c; ``tests/compose/dynamic/template_fixtures.py`` says what a
template fixture is): ``tests/compose/dynamic/fixtures/templates/formula_focus.<format>[.<case>].json``.

Each fixture is a real Dynamic timeline: the screenplay below goes through the timeline builder (``build_timeline``,
the synthetic narration of ``tests/compose/factories.py``), with the resolver allowed to draw ``formula_focus`` (it
draws only ``board_generic`` until B2e's direct.py), so the looks, tokens, the lesson-wide entity map, the dense
template's frozen background (R-55) and B1's word-anchored ``var:<n>`` sync cues are the pipeline's own. On top, the
visual events events.py (B2d) will write: the formula's ``reveal``, a ``var`` event per symbol on the word that names
it (R-17: 120 ms before it), the box's ``draw`` and the R-16 hold's ``look_here`` walk over the symbols (R-26: at most
one new cue per 2 s).

Cases, each in both formats (B2 plan §G text cases):

* ``formula_focus.<format>.json``: typical, a dark family with the Soft personality (midnight_indigo): v = v_0 + a t,
  four variables (three entity colours, the fourth stays ink), a heading, a sentence with TeX and a [[term]], every
  event kind, a formula hold;
* ``.light``: a light family with Crisp (paper_ink; baseline reveal): mixed Tamil + English + TeX, a fraction, the
  formula revealed by its beat and its legend rows by B1's sync cues only (no var events: the fallback path);
* ``.stress``: a dark family with Crisp (deep_teal), four short scenes: the lint-limit case (R-25's 24 prose words on
  screen, in wide glyphs: a six-word headline, a seven-word sentence, five variables of two and three words: two legend
  columns in 16:9, the formula broken at "=" and the legend a compact stack in 9:16), one very long word, Tamil,
  Hindi.

Run ``python -m tests.compose.dynamic.make_formula_focus_fixtures`` to write them, ``--check`` to compare (exit 1 on a
difference). Byte-deterministic: sorted keys, two-space indent, LF, a final newline.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from aadhi.compose import presentation as pres
from aadhi.compose.timeline import build_timeline
from aadhi.config import Settings
from aadhi.schemas.screenplay import Screenplay
from aadhi.schemas.timeline import Timeline, VisualEvent
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR
from tests.compose.factories import beat, make_manifest, screenplay_dict
from tests.schemas.v3_fixtures import PRESENTATION

TEMPLATE = "formula_focus"
LEAD = 0.12  # R-17: a reveal starts 120 ms before its anchor word
CUE_GAP = 2.0  # R-26: at most one new cue per 2 s

Scene = dict[str, Any]


def _variables(*rows: tuple[str, str, str]) -> list[dict[str, str]]:
    return [{"symbol_latex": s, "meaning": m, "unit": u} for s, m, u in rows]


def _formula_scene(sid: str, title: str, heading: str | None, latex: str, text: str | None,
                   variables: list[dict[str, str]], narration: list[str], *, hold: float = 0.0,
                   hold_reason: str | None = None) -> Scene:
    board: list[dict[str, Any]] = []
    if heading is not None:
        board.append({"id": "h", "kind": "heading", "text": heading})
    formula: dict[str, Any] = {"id": "f", "kind": "formula", "latex": latex, "variables": variables}
    if text is not None:
        formula["text"] = text
    board.append(formula)
    beats = [beat(f"{sid}-b{k + 1}", line, **({"board_item_id": "f"} if k == 0 else {}))
             for k, line in enumerate(narration)]
    if hold:
        beats[-1]["pause_after"] = hold
    look: dict[str, Any] = {"template": TEMPLATE}
    if hold_reason:
        look["hold_reason"] = hold_reason
    return {"id": sid, "type": "content", "title": title, "concept_id": "ohm", "board": board, "beats": beats,
            "look": look}


TYPICAL = [_formula_scene(
    "ff-velocity", "Uniform acceleration", "Final velocity", "v = v_0 + a t",
    "Velocity grows steadily from $v_0$: the [[final velocity]]",
    _variables(("v", "final velocity", "m/s"), ("v_0", "initial velocity", "m/s"), ("a", "acceleration", "m/s²"),
               ("t", "time", "s")),
    ["This equation gives the final velocity of a steadily pushed body.", "Here v is the final velocity,",
     "v nought is the initial velocity,", "a is the acceleration,", "and t is the time."],
    hold=4.5, hold_reason="formula")]

LIGHT = [_formula_scene(
    "ff-ohm", "Ohm's law", "ஓம் விதி: Ohm's law $V = I R$", r"I = \frac{V}{R}",
    "மின்னோட்டம் [[current]] equals $V$ over $R$",
    _variables(("I", "current மின்னோட்டம்", "A"), ("V", "voltage மின்னழுத்தம்", "V"),
               ("R", "resistance மின்தடை", "ohm")),
    ["Ohm's law in its second form.", "I is the current,", "V is the voltage,", "and R is the resistance."],
    hold=3.0)]

STRESS = [
    _formula_scene(
        # R-25's limits, filled: <= 8 words an item, 24 prose words on screen (6 + 7 + 11), wide glyphs (W, M)
        "ff-lint", "Why work makes moving wagons whizz",
        # the formula: five symbols (two legend columns in 16:9), wider than a reel's 696 px at its smallest size whole
        # and narrower broken at "=" (9:16: the broken form, §4.3)
        "Why Work Makes Moving Wagons Whizz", r"E_{\mathrm{total}} = m g h + \frac{1}{2} m v^2",
        "Energy is height energy plus motion energy",
        _variables((r"E_{\mathrm{total}}", "Mechanical energy", "J"), ("m", "Wagon mass", "kg"), ("g", "Gravity's pull", "m/s²"),
                   ("h", "Height above ground", "m"), ("v", "Maximum velocity", "m/s")),
        ["Energy is height energy plus motion energy."], hold=1.4),
    _formula_scene(
        "ff-longword", "Pneumonoultramicroscopicsilicovolcanoconiosis",
        "Pneumonoultramicroscopicsilicovolcanoconiosis", "P V = n R T",
        "Supercalifragilisticexpialidociousthermodynamics in one line",
        _variables(("P", "pressure", "Pa"), ("V", "volume", "m³"), ("n", "amount", "mol"),
                   ("R", "Thermodynamicallyuniversalgasconstant", "J/(mol K)"), ("T", "temperature", "K")),
        ["The ideal gas law."], hold=1.4),
    _formula_scene(
        "ff-tamil", "சீரான முடுக்கம்", "சீரான முடுக்கம்", r"s = u t + \frac{1}{2} a t^2",
        "இடப்பெயர்ச்சி நேரத்தின் வர்க்கத்துடன் வளர்கிறது",
        _variables(("s", "இடப்பெயர்ச்சி", "m"), ("u", "தொடக்க வேகம்", "m/s"), ("a", "முடுக்கம்", "m/s²"),
                   ("t", "நேரம்", "s")),
        ["இது இடப்பெயர்ச்சிக்கான சமன்பாடு."], hold=1.4),
    _formula_scene(
        "ff-hindi", "न्यूटन का दूसरा नियम", "न्यूटन का दूसरा नियम", "F = m a", "बल द्रव्यमान और त्वरण का गुणनफल है",
        _variables(("F", "बल", "N"), ("m", "द्रव्यमान", "kg"), ("a", "त्वरण", "m/s²")),
        ["यह न्यूटन का दूसरा नियम है।"], hold=1.4),
]

# (scenes, family, personality, board language, the lesson-wide entity map (R-30, §4.7: formula symbols by first
# appearance, at most three; a fourth quantity stays ink), write var events, write the reveal / draw events)
CASES: dict[str, tuple[list[Scene], str, str, str, dict[str, int], bool, bool]] = {
    "": (TYPICAL, "midnight_indigo", "soft", "en-IN", {"v": 0, "v_0": 1, "a": 2}, True, True),
    "light": (LIGHT, "paper_ink", "crisp", "ta-IN", {"I": 0, "V": 1, "R": 2}, False, False),
    "stress": (STRESS, "deep_teal", "crisp", "en-IN", {r"E_{\mathrm{total}}": 0, "m": 1, "g": 2}, True, True),
}


@contextmanager
def _formula_focus_resolved() -> Iterator[None]:
    """Let the resolver draw formula_focus (presentation.TEMPLATES; B2e's direct.py will choose it)."""
    saved = pres.TEMPLATES
    pres.TEMPLATES = ("board_generic", TEMPLATE)
    try:
        yield
    finally:
        pres.TEMPLATES = saved


def _events(scene: Any, *, var_events: bool, reveal_draw: bool, compact: bool) -> list[VisualEvent]:
    """The events events.py (B2d) writes for a formula_focus scene (module docstring)."""
    formula = next(i for i in scene.board if i.kind == "formula")
    symbols = [v.symbol_latex for v in formula.variables]
    out: list[VisualEvent] = []
    beat0 = scene.beats[0]
    if reveal_draw:
        out.append(VisualEvent(t=round(max(0.0, beat0.start - LEAD), 3), kind="reveal", target="f", anchored=True))
    var_at: dict[int, float] = {}
    for cue in scene.sync_cues:
        if cue.kind == "var" and cue.item_id == "f" and cue.part and cue.part.startswith("var:"):
            var_at.setdefault(int(cue.part[4:]), round(max(0.0, cue.start - LEAD), 3))
    if compact:  # the short stress scenes: one symbol every 0.3 s from the formula's entrance
        var_at = {k: round(beat0.start + 0.3 * (k + 1), 3) for k in range(len(symbols))}
    if var_events:
        for k, at in sorted(var_at.items(), key=lambda kv: (kv[1], kv[0])):
            out.append(VisualEvent(t=at, kind="var", target=f"f:var:{symbols[k]}", anchored=True))
    last = max(var_at.values(), default=beat0.start)
    if reveal_draw:
        out.append(VisualEvent(t=round(last + 0.4, 3), kind="draw", target="f"))
    # the R-16 hold's look_here walk over the symbols, one new cue per CUE_GAP, inside the scene
    walk_from = last + 0.9
    end = scene.duration - 0.3
    k = 0
    while k < len(symbols) and walk_from + k * CUE_GAP + 0.6 <= end:
        t = round(walk_from + k * CUE_GAP, 3)
        dur = round(min(1.5, end - t), 3)
        out.append(VisualEvent(t=t, kind="look_here", target=f"f:var:{symbols[k]}", dur=dur))
        k += 1
    if not k and walk_from + 0.6 <= end:
        out.append(VisualEvent(t=round(walk_from, 3), kind="look_here", target=f"f:var:{symbols[0]}",
                               dur=round(min(1.5, end - walk_from), 3)))
    return sorted(out, key=lambda e: e.t)


def build(case: str, fmt: str) -> dict[str, Any]:
    """The fixture timeline of ``case`` in ``fmt`` (JSON-ready)."""
    scenes, family, personality, language, entities, var_events, reveal_draw = CASES[case]
    data = screenplay_dict()
    data["language"] = language
    data["learning_objectives"] = []
    data["presentation"] = {**PRESENTATION, "format": fmt, "hook": None, "theme_family": family,
                            "personality": personality, "cue_term_style": "marker" if personality == "soft" else "underline",
                            "asset_style": {"family": family, "personality": personality}, "captions_burn": True,
                            "music": {"source": "procedural", "provider": "procedural"}, "entity_colors": entities}
    data["scenes"] = json.loads(json.dumps(scenes))
    sp = Screenplay.model_validate(data)
    with _formula_focus_resolved():
        tl = build_timeline(sp, make_manifest(sp), settings=Settings(_env_file=None, dynamic_enabled=True))
    for s in tl.scenes:
        s.events = _events(s, var_events=var_events, reveal_draw=reveal_draw, compact=case == "stress")
    out = Timeline.model_validate(tl.model_dump(mode="json")).model_dump(mode="json")
    for s in out["scenes"]:  # every scene is drawn by the template, frozen from its start (R-55)
        assert s["look"]["template"] == TEMPLATE and s["look"]["background"]["frozen_at"] == 0.0, s["scene_id"]
    return out


def fixtures() -> dict[Path, str]:
    """Every fixture file and its text."""
    out: dict[Path, str] = {}
    for case in CASES:
        for fmt in ("landscape", "portrait"):
            name = f"{TEMPLATE}.{fmt}{'.' + case if case else ''}.json"
            out[FIXTURE_DIR / name] = json.dumps(build(case, fmt), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="compare instead of writing; exit 1 on a difference")
    args = ap.parse_args(argv)
    stale = []
    for path, text in fixtures().items():
        if args.check:
            if not path.is_file() or path.read_text("utf-8") != text:
                stale.append(path.name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
    if stale:
        print(f"stale formula_focus fixtures: {', '.join(stale)} (run python -m tests.compose.dynamic."
              "make_formula_focus_fixtures)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
