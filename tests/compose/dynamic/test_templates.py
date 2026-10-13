"""The scene-template registry (aadhi.compose.dynamic.templates; DYNAMIC_VIDEO_DESIGN §4.2-§4.4, R-28; B2 plan
§1.6-§1.9).

* All 41 templates of §4.3 with the §4.2 class table (19 classes, frozen), releases, formats, forced types, timing,
  holds, dense / push flags and what they consume.
* board_generic's slots are its JS ``LAYOUT`` / ``MIN_*`` constants and dynamic.css's text metrics; ``implemented``
  and the backgrounds / transitions registries match what the B1 stage draws.
* The target grammar (plan §1.7): every form parses and formats back; malformed targets are refused.
* ``fit`` on one scene per §4.4 row: the preferred template, the named alternatives, the data limits, the fallback.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from aadhi.compose.dynamic import templates as tp
from aadhi.compose.dynamic import themes as th
from aadhi.compose.dynamic import zones as zn
from aadhi.compose.dynamic.templates import FIT_ALTERNATIVE, FIT_FALLBACK, FIT_PREFERRED, FIT_STRETCHED, Target
from aadhi.schemas.screenplay import (
    AIVideoScene,
    BoardScene,
    ChapterCardScene,
    ClipScene,
    InteractiveScene,
    QuizScene,
    SimulationScene,
)

ROOT = Path(__file__).resolve().parents[3]
JS = ROOT / "web" / "js" / "dynamic"

R1 = {"aadhi_intro", "aadhi_recap", "hook_question", "key_terms", "chapter_bumper", "recap_retrieval", "outro_next",
      "thumbnail", "definition_card", "kinetic_statement", "list_build", "media_split", "formula_focus", "worked_steps",
      "code_walkthrough", "chart_build", "myth_fact", "predict", "check", "full_bleed_media", "animation_stage",
      "interactive_poster", "board_generic"}  # §4.3: 8 bookend + 11 content and practice + 4 forced or fallback
W2 = {"roadmap", "spotlight_image", "diagram_build", "versus", "table_reveal", "analogy_bridge", "process_flow",
      "cycle_loop", "timeline_track", "cause_chain", "concept_tree", "function_plot", "model_spin", "takeaway_stamp",
      "quote_card", "checklist", "stat_punch"}


# --- the registry ---------------------------------------------------------------------------------------------------


def test_all_41_templates_with_their_release() -> None:
    assert len(tp.TEMPLATES) == 41 and len(R1) == 23 and len(W2) == 17
    assert {t for t, s in tp.TEMPLATES.items() if s.release == "R1"} == R1
    assert {t for t, s in tp.TEMPLATES.items() if s.release == "W2"} == W2
    assert {t for t, s in tp.TEMPLATES.items() if s.release == "B6"} == {"clip_scene"}
    assert tp.registry_problems() == []
    assert all(spec.id == tid for tid, spec in tp.TEMPLATES.items()) and tp.template("check") is tp.TEMPLATES["check"]
    with pytest.raises(KeyError):
        tp.template("nope")


def test_the_semantic_class_table_is_frozen() -> None:
    """§4.2's table, row for row: 19 classes, their claims and members; every template's class is its row."""
    table = {
        "structure": ("Orientation, not content",
                      {"aadhi_intro", "aadhi_recap", "chapter_bumper", "roadmap", "outro_next", "thumbnail"}),
        "prompt": ("Think about this", {"hook_question", "predict", "check", "recap_retrieval"}),
        "definition": ("X means Y", {"key_terms", "definition_card"}),
        "statement": ("One principle in words (typography only)",
                      {"kinetic_statement", "takeaway_stamp", "quote_card"}),
        "enumeration": ("Unordered members of a set", {"list_build"}),
        "contrast": ("A differs from B", {"versus"}),
        "correspondence": ("A maps to B (analogy)", {"analogy_bridge"}),
        "sequence": ("Ordered steps or times", {"process_flow", "cycle_loop", "timeline_track"}),
        "cause": ("A causes B", {"cause_chain"}),
        "completion": ("Items done or required", {"checklist"}),
        "hierarchy": ("Parts of a whole", {"concept_tree"}),
        "quantity": ("Numbers and functions", {"stat_punch", "chart_build", "function_plot"}),
        "formula": ("An equation and its symbols", {"formula_focus"}),
        "procedure": ("A worked example", {"worked_steps"}),
        "code": ("A program", {"code_walkthrough"}),
        "table": ("A grid of attributes", {"table_reveal"}),
        "refutation": ("A belief corrected", {"myth_fact"}),
        "media": ("A representational picture or motion",
                  {"media_split", "full_bleed_media", "spotlight_image", "diagram_build", "model_spin",
                   "animation_stage", "interactive_poster", "clip_scene"}),
        "fallback": ("Anything", {"board_generic"}),
    }
    assert list(tp.SEMANTIC_CLASSES) == list(table)  # §4.2's order, which is also the §1.4 definition's
    assert {c: (r.claim, set(r.templates)) for c, r in tp.SEMANTIC_CLASSES.items()} == table
    assert set(tp.SemanticClass.__args__) == set(table)  # type: ignore[attr-defined]
    for tid, spec in tp.TEMPLATES.items():
        assert tid in table[spec.semantic_class][1], tid


def test_the_registry_checker_finds_a_half_declared_template(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = dict(tp.TEMPLATES)
    odd = tp.Consumes(("x",))  # type: ignore[arg-type]
    bad["list_build"] = tp.TemplateSpec("list_build", "statement", False, "R1", tp._d((1.0, 2.0)), odd,
                                        tp._fit_list_build)
    bad["ghost"] = tp.TemplateSpec("ghost", "fallback", False, "W2", tp._d((1.0, 2.0)), tp.Consumes(), tp._never,
                                   formats=("landscape",))
    monkeypatch.setattr(tp, "TEMPLATES", bad)
    problems = tp.registry_problems()
    assert any("list_build: class statement" in p for p in problems)
    assert any("list_build: unknown event kind" in p for p in problems)
    assert any("ghost: variants / duration" in p for p in problems)


def test_implemented_matches_the_stage() -> None:
    """``implemented`` == the stage's template registry (web/js/dynamic/stage.js TEMPLATES); likewise backgrounds
    and transitions (step B adds the JS-side parity test against the catalogue)."""
    stage = (JS / "stage.js").read_text(encoding="utf-8")
    templates = re.search(r"export const TEMPLATES = Object\.freeze\(\{([^}]*)\}\);", stage)
    backgrounds = re.search(r"export const BACKGROUNDS = Object\.freeze\(\{([^}]*)\}\);", stage)
    assert templates and backgrounds
    assert set(re.findall(r"(\w+):", templates.group(1))) == set(tp.IMPLEMENTED) == {"board_generic"}
    assert set(re.findall(r"(\w+):", backgrounds.group(1))) == {b for b, s in tp.BACKGROUNDS.items() if s.implemented}
    transitions = re.search(r"export const TRANSITIONS = Object\.freeze\(\[([^\]]*)\]\);",
                            (JS / "transitions.js").read_text(encoding="utf-8"))
    assert transitions
    assert set(re.findall(r"'(\w+)'", transitions.group(1))) == {t for t, s in tp.TRANSITIONS.items() if s.implemented}
    assert {p for p, b in tp.BACKGROUNDS.items() if b.mirrored} == set(th.PRESETS_MIRRORED)
    assert all(set(b.personalities) <= set(th.PERSONALITIES) for b in tp.BACKGROUNDS.values())
    # §4.5: the six R1 presets; §4.9: cut, crossfade, push, colour wipe and the shared-element morph in R1
    assert {b for b, s in tp.BACKGROUNDS.items() if s.release == "R1"} == {
        "mesh_drift", "dot_field", "paper_grain", "soft_waves", "spotlight", "colour_block"}
    assert {t for t, s in tp.TRANSITIONS.items() if s.release == "R1"} == {"cut", "crossfade", "push", "colour_wipe",
                                                                          "morph"}
    assert tp.TRANSITIONS["crossfade"].seconds[0] >= 0.3  # R-54


def test_formats_forced_types_and_flags() -> None:
    assert tp.TEMPLATES["thumbnail"].formats == ("landscape",)
    assert all(s.formats == ("landscape", "portrait") for t, s in tp.TEMPLATES.items() if t != "thumbnail")
    forced = {t: s.forced_for for t, s in tp.TEMPLATES.items() if s.forced_for}
    assert forced == {"chapter_bumper": ("chapter_card",), "check": ("quiz_checkpoint",),
                      "full_bleed_media": ("ai_video",), "animation_stage": ("simulation",),
                      "interactive_poster": ("interactive",), "clip_scene": ("clip",)}
    # R-55 / R-49 / R-44: formula, code and worked-example scenes are dense
    assert {t for t, s in tp.TEMPLATES.items() if s.dense} == {"formula_focus", "worked_steps", "code_walkthrough"}
    # §4.3 calls for a camera push only in media_split (<= 3%) and spotlight_image; never on formulas (R-30) or code
    assert {t for t, s in tp.TEMPLATES.items() if s.push_ok} == {"media_split", "spotlight_image"}
    assert tp.TEMPLATES["formula_focus"].holds == ("formula",) and tp.TEMPLATES["code_walkthrough"].holds == ("code",)
    assert set(tp.TEMPLATES["worked_steps"].holds) == {"formula", "think"}
    assert all(tp.TEMPLATES[t].holds == ("think",) for t in ("predict", "check", "recap_retrieval"))
    assert all(set(s.holds) <= set(tp.HOLD_REASONS) for s in tp.TEMPLATES.values())
    statement = {t for t, s in tp.TEMPLATES.items() if s.semantic_class == "statement"}
    assert not any(tp.TEMPLATES[t].representational for t in statement), "statement is typography only (§4.2)"
    assert all(tp.TEMPLATES[t].representational for t in tp.SEMANTIC_CLASSES["media"].templates)


def test_variants() -> None:
    v = {t: dict(s.variants) for t, s in tp.TEMPLATES.items() if any(s.variants.values())}
    assert v == {
        "aadhi_intro": {"landscape": ("fullframe", "card_left", "pip", "split"), "portrait": ("card",)},  # §3.3
        "hook_question": {f: ("question", "true_false", "stakes", "mystery", "challenge") for f in zn.FORMATS},
        "definition_card": {f: ("card_icon_right", "card_image_left", "centred") for f in zn.FORMATS},
        "media_split": {"landscape": ("media_left", "media_right"), "portrait": ()},
        "check": {"landscape": ("mcq", "free_recall", "true_false", "apply"),
                  "portrait": ("free_recall", "true_false")},
    }
    assert all("safe" not in vs for s in tp.TEMPLATES.values() for vs in s.variants.values())  # plan §1.2


def test_durations() -> None:
    """§4.3 "Timing" (16:9, 9:16); per-item where §4.3 times each item; R-19 where it gives none."""
    expected = {
        "aadhi_intro": ((5, 8), (5, 8)), "aadhi_recap": ((5, 8), (5, 8)), "hook_question": ((4, 10), (3, 6)),
        "chapter_bumper": ((2, 3), (1.2, 1.8)), "recap_retrieval": ((25, 40), (14, 20)),
        "outro_next": ((6, 15), (3, 5)), "definition_card": ((6, 12), (4, 8)), "kinetic_statement": ((3, 7), (2, 5)),
        "media_split": ((8, 20), (5, 12)), "full_bleed_media": ((3, 12), (3, 12)), "formula_focus": ((10, 25), (8, 16)),
        "worked_steps": ((15, 45), (10, 30)), "code_walkthrough": ((10, 30), (8, 20)),
        "chart_build": ((8, 20), (8, 20)),
        "animation_stage": ((8, 40), (8, 40)), "myth_fact": ((8, 14), (6, 10)), "predict": ((6, 12), (5, 9)),
        "check": ((15, 30), (10, 18)), "key_terms": ((3, 4), (3, 4)), "list_build": ((3, 5), (3, 5)),
        "interactive_poster": ((6, 30), (4, 20)), "board_generic": ((6, 45), (4, 30)), "roadmap": ((6, 30), (4, 20)),
        "spotlight_image": ((6, 12), (6, 12)), "diagram_build": ((8, 30), (8, 30)), "versus": ((8, 16), (8, 16)),
        "table_reveal": ((10, 20), (10, 20)), "analogy_bridge": ((8, 15), (8, 15)),
        "process_flow": ((10, 25), (10, 25)), "cycle_loop": ((8, 25), (8, 25)), "timeline_track": ((8, 25), (8, 25)),
        "cause_chain": ((8, 25), (8, 25)), "concept_tree": ((8, 15), (8, 15)), "function_plot": ((6, 25), (6, 25)),
        "model_spin": ((6, 25), (6, 25)), "takeaway_stamp": ((4, 7), (4, 7)), "quote_card": ((5, 8), (5, 8)),
        "checklist": ((8, 20), (8, 20)), "stat_punch": ((4, 7), (4, 7)),
    }
    for tid, (land, port) in expected.items():
        d = tp.TEMPLATES[tid].duration
        assert ((d["landscape"].min, d["landscape"].max), (d["portrait"].min, d["portrait"].max)) == (land, port), tid
    assert {t for t, s in tp.TEMPLATES.items() if s.duration[s.formats[0]].per_item} == {"key_terms", "list_build"}
    assert tp.TEMPLATES["clip_scene"].duration["landscape"] == tp.Duration(0.5, None)  # the clip's own length
    assert dict(tp.TEMPLATES["thumbnail"].duration) == {"landscape": tp.Duration(0.0, 0.0)}  # one frame, no timeline
    with pytest.raises(ValueError):
        tp.Duration(3.0, 2.0)


def test_consumes() -> None:
    for tid, spec in tp.TEMPLATES.items():
        assert set(spec.consumes.kinds) <= set(tp.EVENT_KINDS), tid
        assert set(spec.consumes.targets) <= set(tp.TARGET_FORMS), tid
        assert bool(spec.consumes.kinds) == bool(spec.consumes.targets), tid
    bg = tp.TEMPLATES["board_generic"].consumes  # what web/js/dynamic/state.js reads today
    assert (bg.kinds, bg.targets) == (("reveal", "fill"), ("item",))
    assert bg.accepts("reveal", "i1") and bg.accepts("fill", "s2") and bg.accepts("reveal", None)
    assert not bg.accepts("look_here", "i1") and not bg.accepts("reveal", "i1:line:2")
    assert not bg.accepts("reveal", "x:")
    assert tp.TEMPLATES["formula_focus"].consumes.accepts("var", "f1:var:R")
    assert tp.TEMPLATES["check"].consumes.accepts("answer", "option:2")
    assert tp.TEMPLATES["chart_build"].consumes.accepts("look_here", "panel:bar:0:3")


def test_roles() -> None:
    """plan §1.9: core.js's closed role list in its order, and the block roles the resolver stores."""
    assert tp.ROLES == ("ink", "ink2", "surface", "entity:0", "entity:1", "entity:2", "warn", "ok", "mark", "focus",
                        "struct.accent", "struct.tint", "struct.border", "struct.pip")
    assert tp.BLOCK_ROLES == th.DERIVED_KEYS == ("block.base", "block.highlight", "block.ink", "block.ink2", "scrim")


def test_templates_and_events_do_not_import_each_other() -> None:
    source = (ROOT / "aadhi/compose/dynamic/templates.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from\s+\S*events\s+import|import\s+\S*events)", source, re.M)
    assert tp.TEMPLATES["list_build"].events(None, None, "landscape") == []  # type: ignore[arg-type]


# --- board_generic's slots ------------------------------------------------------------------------------------------


def _js_layout() -> dict[str, dict[str, Any]]:
    """board_generic.js LAYOUT[format]: kicker, the three title specs, body and split."""
    board = (JS / "templates" / "board_generic.js").read_text(encoding="utf-8")
    block = re.search(r"export const LAYOUT = Object\.freeze\(\{(.*?)\n\}\);", board, re.S)
    assert block
    out: dict[str, dict[str, Any]] = {}
    for fmt in zn.FORMATS:
        m = re.search(fmt + r": Object\.freeze\(\{(.*?)\n  \}\),", block.group(1), re.S)
        assert m, fmt
        text = m.group(1)
        spec: dict[str, Any] = {k: float(v) for k, v in re.findall(r"^\s*(kicker|body|split): ([\d.]+),", text, re.M)}
        for key in ("title", "titleQuiz", "titleCard"):
            t = re.search(rf"^\s*{key}: \{{([^}}]*)\}}", text, re.M)
            assert t, key
            spec[key] = {k: float(v) for k, v in re.findall(r"(\w+): ([\d.]+)", t.group(1))}
        out[fmt] = spec
    return out


def _js_const(name: str) -> str | None:
    board = (JS / "templates" / "board_generic.js").read_text(encoding="utf-8")
    m = re.search(rf"export const {name} = ([^;]*);", board)
    return m.group(1) if m else None


def test_board_generic_layout_is_the_js_layout() -> None:
    js = _js_layout()
    for fmt, layout in tp.BOARD_LAYOUT.items():
        spec = js[fmt]
        assert (layout.kicker, layout.body, layout.split) == (spec["kicker"], spec["body"], spec["split"])
        for key, title in (("title", layout.title), ("titleQuiz", layout.title_quiz), ("titleCard", layout.title_card)):
            want = {"max": title.max, "min": title.min, "lines": title.lines, "lh": title.lh}
            if title.prefer is not None:
                want["prefer"] = title.prefer
            assert spec[key] == want, (fmt, key)
    assert _js_const("MIN_FIT") == "Object.freeze({ landscape: 0.75, portrait: 0.72 })"
    assert dict(tp.BOARD_MIN_FIT) == {"landscape": 0.75, "portrait": 0.72}
    assert (_js_const("RING_SIZE"), _js_const("RING_GAP")) == tuple(str(v) for v in tp.BOARD_RING)
    board = (JS / "templates" / "board_generic.js").read_text(encoding="utf-8")
    for fmt, box in tp.BOARD_CENTRED_TITLE.items():  # centeredTitleBox
        literal = f"{{ x0: {box.x0}, y0: {box.y0}, x1: {box.x1}, y1: {box.y1} }}"
        assert literal in board, fmt
    # R-29 floors: literals in board_generic.js today; from step B they come from the catalogue (no literals left)
    floors = {"MIN_TEXT": "text_min", "MIN_LABEL": "label_min"}
    for name, attr in floors.items():
        literal = _js_const(name)
        if literal is not None and literal.startswith("Object.freeze({ landscape:"):
            values = dict(re.findall(r"(landscape|portrait): (\d+)", literal))
            assert {f: int(v) for f, v in values.items()} == {f: getattr(zn.TYPE_FLOORS[f], attr) for f in zn.FORMATS}
        else:
            assert "CATALOGUE" in board, f"{name} is neither the R-29 literal nor read from the catalogue"
    weight = _js_const("BODY_WEIGHT")
    if weight is not None and weight.startswith("Object.freeze({ landscape:"):
        assert {f: int(v) for f, v in re.findall(r"(landscape|portrait): (\d+)", weight)} == dict(tp.BOARD_BODY_WEIGHT)


def test_board_generic_slots() -> None:
    """The slot table: widths from zones.py and board_generic's layout (title zone; minus the ring in a 16:9 quiz;
    the centred title box; the 16:9 split at 55%; the 9:16 card column x 168-804), sizes from LAYOUT and R-29."""
    s = tp.TEMPLATES["board_generic"].slots
    assert set(s) == {"kicker", "title", "subtitle", "body", "chip", "ring"}

    def row(slot: str, fmt: str, variant: str) -> tuple[Any, ...]:
        x = s[slot][fmt][variant]
        return (x.w, x.max_lines, x.size, x.weight, x.line_height, x.prefer_lines, x.critical)

    assert row("title", "landscape", "default") == (1728, 2, (72, 96), 700, 1.06, None, True)
    assert row("title", "landscape", "quiz") == (1608, 3, (56, 76), 700, 1.12, None, True)
    assert row("title", "landscape", "card") == (1536, 3, (96, 148), 700, 1.02, None, True)
    assert row("title", "portrait", "default") == (696, 2, (56, 88), 700, 1.06, 1, True)
    assert row("title", "portrait", "quiz") == (696, 4, (50, 68), 700, 1.12, None, True)
    assert row("title", "portrait", "card") == (696, 4, (72, 112), 700, 1.04, None, True)
    assert row("kicker", "landscape", "default") == (1728, 1, (40, 40), 600, 1.2, None, False)
    assert row("kicker", "landscape", "card") == (1536, 1, (40, 40), 600, 1.2, None, True)
    assert row("kicker", "portrait", "quiz") == (696, 1, (44, 44), 600, 1.2, None, False)
    assert row("subtitle", "landscape", "card") == (1400, None, (52, 52), 400, 1.3, None, True)
    assert row("subtitle", "portrait", "card") == (696, None, (48, 48), 600, 1.3, None, True)
    assert row("body", "landscape", "default") == (1728, None, (39.0, 52), 400, 1.3, None, True)
    assert row("body", "landscape", "split") == (950, None, (39.0, 52), 400, 1.3, None, True)
    assert row("body", "portrait", "default") == row("body", "portrait", "split") == (
        636, None, (max(40, 56 * 0.72), 56), 600, 1.3, None, True)
    assert row("chip", "landscape", "default") == (None, 1, (40, 40), 600, 1.1, None, False)
    assert row("ring", "portrait", "default") == (96, 1, (44, 44), 700, 1.3, None, False)
    assert s["title"]["landscape"]["default"].face == "display" and s["body"]["portrait"]["default"].face == "body"
    assert s["title"]["portrait"]["card"].tracking_em == -0.015 and s["kicker"]["portrait"]["card"].tracking_em == 0.02
    for slot, per_format in s.items():  # R-29: no slot goes below the format's floor
        for fmt, per_variant in per_format.items():
            floors = zn.TYPE_FLOORS[fmt]
            for variant, spec in per_variant.items():
                floor = floors.label_min if spec.role in ("label", "chip") else floors.text_min
                assert spec.size[0] >= floor, (slot, fmt, variant)
                if fmt == "portrait" and spec.size[0] <= floors.heavy_max_px:  # type: ignore[operator]
                    assert spec.weight >= floors.heavy_min_weight, (slot, variant)  # type: ignore[operator]
    assert all(not spec.slots for t, spec in tp.TEMPLATES.items() if not spec.implemented)
    with pytest.raises(ValueError):
        tp.SlotSpec(w=10, max_lines=1, size=(50, 40), role="body", face="body", weight=400, line_height=1.3)


def test_board_generic_text_metrics_are_the_stylesheets() -> None:
    css = re.sub(r"/\*.*?\*/", "", (ROOT / "web/css/dynamic.css").read_text(encoding="utf-8"), flags=re.S)

    def prop(selector: str, name: str) -> str:
        m = re.search(r"\}\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert m, selector
        v = re.search(rf"(?:^|;|\s){re.escape(name)}\s*:\s*([^;]+);", m.group(1))
        assert v, (selector, name)
        return v.group(1).strip()

    s = tp.TEMPLATES["board_generic"].slots
    assert prop(".dyn-header", "line-height") == str(s["kicker"]["landscape"]["default"].line_height)
    assert prop(".dyn-header", "letter-spacing") == f"{s['kicker']['landscape']['default'].tracking_em}em"
    assert prop(".dyn-title", "letter-spacing") == f"{s['title']['landscape']['default'].tracking_em}em"
    assert prop(".dyn-card", "line-height") == str(s["body"]["landscape"]["default"].line_height)
    assert prop(".dyn-chip", "line-height") == str(s["chip"]["landscape"]["default"].line_height)
    size, weight, max_w = tp.BOARD_SUBTITLE["landscape"]
    assert (prop(".dyn-subtitle", "font-size"), prop(".dyn-subtitle", "font-weight")) == (f"{size}px", str(weight))
    assert prop(".dyn-subtitle", "max-width") == f"{max_w}px" and prop(".dyn-subtitle", "line-height") == "1.3"
    assert prop(".dyn-board--portrait .dyn-subtitle", "font-size") == f"{tp.BOARD_SUBTITLE['portrait'][0]}px"


# --- the target grammar (plan §1.7) ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "target"), [
    ("i1", Target("item", item="i1")),
    ("step-2", Target("item", item="step-2")),
    ("c1:line:0", Target("item:line", item="c1", index=0)),
    ("c1:line:12", Target("item:line", item="c1", index=12)),
    ("f1:var:R", Target("item:var", item="f1", symbol="R")),
    ("f1:var:\\Omega_{in}", Target("item:var", item="f1", symbol="\\Omega_{in}")),
    ("f1:var:\\Delta V", Target("item:var", item="f1", symbol="\\Delta V")),
    ("f1:var:a:b", Target("item:var", item="f1", symbol="a:b")),
    ("ex:step:3", Target("item:step", item="ex", index=3)),
    ("t1:row:0", Target("item:row", item="t1", index=0)),
    ("t1:column:2", Target("item:column", item="t1", index=2)),
    ("panel", Target("panel")),
    ("panel:bar:0:3", Target("panel:bar", series=0, index=3)),
    ("panel:point:1:0", Target("panel:point", series=1, index=0)),
    ("panel:row:4", Target("panel:row", index=4)),
    ("panel:column:1", Target("panel:column", index=1)),
    ("option:0", Target("option", index=0)),
    ("option:3", Target("option", index=3)),
])
def test_targets_parse_and_format_back(text: str, target: Target) -> None:
    assert tp.parse_target(text) == target
    assert tp.format_target(target) == str(target) == text
    assert tp.target_form(text) == target.form


def test_every_target_form_is_covered() -> None:
    assert set(tp.TARGET_FORMS) == {"item", "item:line", "item:var", "item:step", "item:row", "item:column", "panel",
                                    "panel:bar", "panel:point", "panel:row", "panel:column", "option"}
    assert tp.RESERVED_TARGET_WORDS == {"panel", "option"}


@pytest.mark.parametrize("text", [
    "", "I1", "-a", "a" * 65, "a1:", "a1:line", "a1:line:", "a1:line:01", "a1:line:-1", "a1:line:x", "a1:line:1.0",
    "a1:line:1:2", "a1:foo:1", "a1:var:", "a1:var: V", "a1:var:V ", "a1:var:V\n", "a1:var:" + "x" * 81, "panel:",
    "panel:bar", "panel:bar:0", "panel:bar:x:1", "panel:bar:0:1:2", "panel:pie:0:1", "panel:row", "panel:row:1:2",
    "option", "option:", "option:a", "option:1:2", "option:-1", "a1:row:1234567", " i1", "i1 ",
])
def test_malformed_targets_are_refused(text: str) -> None:
    with pytest.raises(ValueError):
        tp.parse_target(text)


def test_formatting_refuses_incomplete_targets() -> None:
    for bad in (Target("item", item="panel"), Target("item", item="option"), Target("item"),
                Target("item:line", item="a"), Target("panel:bar", index=1), Target("option", index=-1),
                Target("option", index=True), Target("item:var", item="a", symbol=" x"),
                Target("bogus", item="a")):  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            tp.format_target(bad)
    with pytest.raises(ValueError):
        tp.parse_target(None)  # type: ignore[arg-type]


# --- fit (§4.4) -----------------------------------------------------------------------------------------------------

PANELS: dict[str, dict[str, Any]] = {
    "image": {"kind": "image", "image_prompt": "a lamp"}, "figure": {"kind": "figure", "figure_id": "fig-1"},
    "terminal": {"kind": "terminal", "terminal": {"command": "ls"}},
    "chart": {"kind": "chart", "chart": {"labels": ["a"], "datasets": [{"data": [1]}]}},
    "graph": {"kind": "graph", "graph": {"functions": [{"expr": "x"}]}},
    "manim": {"kind": "manim", "manim": {"template": "x"}},
    "model_3d": {"kind": "model_3d", "model_3d": {"primitives": [{"shape": "sphere"}]}},
    "skill_tree": {"kind": "skill_tree"}, "gif": {"kind": "gif", "gif_query": "x"},
}
_ITEM_DATA: dict[str, dict[str, Any]] = {
    "definition": {"term": "Ohm", "text": "the unit of resistance"}, "formula": {"latex": "V = IR"},
    "code": {"code": "print(1)"}, "table": {"headers": ["A", "B"], "rows": [["1", "2"]]},
    "figure": {"figure_id": "fig-1"}, "misconception": {"text": "Current is used up", "justification": "It is not"},
    "quote": {"text": "Measure what is measurable", "caption": "Galileo"}, "pair": {"term": "water", "text": "current"},
    "stat": {"value": "3", "text": "lamps in the circuit"},
}
BEAT = [{"id": "b1", "narration": "Hello there."}]


def item(kind: str, n: int = 0, **kw: Any) -> dict[str, Any]:
    data = {"id": f"{kind.replace('_', '-')}{n}", "kind": kind, "text": "A short line of text"}
    data.update(_ITEM_DATA.get(kind, {}))
    data.update(kw)
    return data


def board(*items: dict[str, Any], type: str = "content", role: str | None = None, intent: str | None = None,
          panel: str | None = None, sequence: str | None = None, presenter: bool = False) -> BoardScene:
    data: dict[str, Any] = {"id": "s1", "type": type, "title": "T", "beats": BEAT, "board": list(items)}
    if role or intent:
        data["intent"] = {k: v for k, v in (("narrative_role", role), ("visual_intent", intent)) if v}
    if panel:
        data["side_panel"] = PANELS[panel]
    if sequence:
        data["sequence"] = sequence
    if presenter:
        data["look"] = {"presenter": {"clip": "left", "seconds": 6.0, "layout": "fullframe"}}
    return BoardScene.model_validate(data)


def fits(scene: Any, fmt: str = "landscape", **kw: Any) -> dict[str, float]:
    """The templates with fit > 0 for ``scene``."""
    out = {t: s.fit(scene, fmt, **kw) for t, s in tp.TEMPLATES.items()}  # type: ignore[arg-type]
    return {t: v for t, v in out.items() if v > 0}


def rows(scene: Any, **kw: Any) -> list[str]:
    return [r.id for r in tp.signals(scene, **kw)]


def words(n: int) -> str:
    return " ".join(["word"] * n)


def test_fit_presenter_hook_and_pretraining() -> None:
    intro = board(item("heading"), item("question"), role="hook", presenter=True)
    assert rows(intro) == ["presenter", "hook"] and tp.signals(intro)[0].forced
    assert fits(intro) == {"aadhi_intro": 1.0, "hook_question": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(board(item("heading"), item("question"), presenter=True))["hook_question"] == FIT_ALTERNATIVE
    hook = board(item("question", text=words(12)), role="hook")
    assert fits(hook) == {"hook_question": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(board(item("question", text=words(13)), role="hook"))["hook_question"] == FIT_STRETCHED
    assert fits(board(item("question", text=words(8)), role="hook"), "portrait")["hook_question"] == 1.0
    assert fits(board(item("question", text=words(9)), role="hook"), "portrait")["hook_question"] == FIT_STRETCHED
    stat_hook = board(item("stat"), role="hook")  # the stat hook kind: stat_punch (the hook row's alternative)
    assert rows(stat_hook) == ["hook", "stat"] and fits(stat_hook)["stat_punch"] == 1.0
    terms = [item("definition", n) for n in range(3)]
    assert fits(board(*terms, role="pretraining")) == {"key_terms": 1.0, "board_generic": FIT_FALLBACK}
    assert "key_terms" not in fits(board(*[item("definition", n) for n in range(5)], role="pretraining"))
    long_term = board(*terms[:2], item("definition", 9, term="one two three four"), role="pretraining")
    assert fits(long_term)["key_terms"] == FIT_STRETCHED


def test_fit_forced_scene_types() -> None:
    chapter = ChapterCardScene.model_validate({"id": "c", "type": "chapter_card", "title": "Part", "beats": []})
    assert rows(chapter) == ["chapter_card"] and fits(chapter) == {"chapter_bumper": 1.0, "board_generic": FIT_FALLBACK}
    quiz = QuizScene.model_validate({"id": "q", "type": "quiz_checkpoint", "question": "Q?", "options": ["a", "b", "c"],
                                     "correct_index": 0, "beats": BEAT,
                                     "reveal_beats": [{"id": "b2", "narration": "A."}]})
    assert fits(quiz) == {"check": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(quiz, "portrait")["check"] == FIT_STRETCHED  # reels: free recall or true / false (2 options)
    five = quiz.model_copy(update={"options": ["a", "b", "c", "d", "e"]})
    assert fits(five)["check"] == FIT_STRETCHED  # forced: never 0 on its scene type
    sim = SimulationScene.model_validate({"id": "m", "type": "simulation", "manim": {"template": "x"}, "beats": BEAT})
    ai = AIVideoScene.model_validate({"id": "v", "type": "ai_video", "video_prompt": "a lamp", "beats": BEAT})
    sketch = InteractiveScene.model_validate({"id": "p", "type": "interactive", "p5_code": "x", "beats": BEAT})
    clip = ClipScene.model_validate({"id": "k", "type": "clip", "clip": {"asset_key": "clip-a", "in_frame": 0,
                                                                         "out_frame": 60}})
    assert fits(sim) == {"animation_stage": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(ai) == {"full_bleed_media": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(sketch) == {"interactive_poster": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(clip) == {"clip_scene": 1.0}  # B1's board_generic never drew a clip scene
    for scene in (chapter, quiz, sim, ai, sketch, clip):
        assert len(tp.signals(scene)) == 1 and tp.signals(scene)[0].forced


def test_fit_statement_definition_and_list_rows() -> None:
    assert fits(board(item("definition"), item("bullet"))) == {"definition_card": 1.0, "board_generic": FIT_FALLBACK}
    takeaway = board(item("takeaway", text=words(12)))
    assert fits(takeaway) == {"kinetic_statement": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(takeaway, chapter_end=True)["takeaway_stamp"] == FIT_ALTERNATIVE  # §4.4: at a chapter end only
    assert fits(board(item("takeaway", text=words(13))))["kinetic_statement"] == FIT_STRETCHED
    assert fits(board(item("callout_tip")))["kinetic_statement"] == 1.0
    quote = board(item("quote"))
    assert fits(quote) == {"quote_card": 1.0, "kinetic_statement": FIT_ALTERNATIVE, "board_generic": FIT_FALLBACK}
    assert fits(board(item("quote", text=words(26))))["quote_card"] == FIT_STRETCHED
    bullets = board(item("heading"), item("bullet", 1), item("bullet", 2), item("bullet", 3))
    assert fits(bullets) == {"list_build": 1.0, "board_generic": FIT_FALLBACK}


def test_fit_sequences() -> None:
    steps = [item("step", n) for n in range(4)]
    dated = [item("step", n, date=f"{1900 + n}") for n in range(4)]
    assert fits(board(*steps, sequence="flow")) == {"process_flow": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(board(*dated, sequence="flow"))["timeline_track"] == FIT_ALTERNATIVE  # a flow with dates may be one
    assert fits(board(*dated, sequence="timeline")) == {"timeline_track": 1.0, "process_flow": FIT_ALTERNATIVE,
                                                       "board_generic": FIT_FALLBACK}
    assert fits(board(*steps, sequence="cycle")) == {"cycle_loop": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(board(*steps, sequence="chain")) == {"cause_chain": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(board(*steps, sequence="checklist")) == {"checklist": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(board(*steps[:2], sequence="flow")) == {"board_generic": FIT_FALLBACK}  # 3-6 steps


def test_fit_pairs_tables_and_compare() -> None:
    pairs = board(item("pair", 1), item("pair", 2))
    assert fits(pairs) == {"analogy_bridge": 1.0, "board_generic": FIT_FALLBACK}  # never versus (§4.3)
    two = board(item("table"))
    assert fits(two) == {"versus": 1.0, "board_generic": FIT_FALLBACK}
    five_rows = board(item("table", rows=[["1", "2"]] * 5))
    assert fits(five_rows) == {"board_generic": FIT_FALLBACK}  # versus: <= 4 rows; table_reveal: >= 3 columns
    three = board(item("table", headers=["", "A", "B"], rows=[["x", "1", "2"]]))
    assert fits(three) == {"table_reveal": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(board(item("table", headers=["", "A", "B"], rows=[["x", "1", "2"]]), intent="compare")) == {
        "versus": 1.0, "table_reveal": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(board(item("definition", 1), item("definition", 2), intent="compare")) == {
        "versus": 1.0, "board_generic": FIT_FALLBACK}


def test_fit_quantity_formula_procedure_code_refutation() -> None:
    assert fits(board(item("stat"))) == {"stat_punch": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(board(item("stat", text=words(11))))["stat_punch"] == FIT_STRETCHED
    assert fits(board(item("heading"), panel="chart"))["chart_build"] == 1.0
    assert fits(board(item("heading"), panel="graph"))["function_plot"] == 1.0
    beside = board(item("bullet", 1), item("bullet", 2), panel="chart")  # the chart beside bullets: media_split's
    assert rows(beside) == ["list", "chart", "media"] and "chart_build" not in fits(beside)
    assert fits(beside)["media_split"] == 1.0 and fits(beside)["list_build"] == 1.0
    assert fits(board(item("formula"))) == {"formula_focus": 1.0, "board_generic": FIT_FALLBACK}
    worked = board(item("formula"), item("example_step", 1), item("example_step", 2, blank=True))
    assert fits(worked) == {"worked_steps": 1.0, "board_generic": FIT_FALLBACK}
    code = board(item("code", code="\n".join(["x = 1"] * 10)))
    assert fits(code)["code_walkthrough"] == 1.0 and fits(code, "portrait")["code_walkthrough"] == 1.0
    long_lines = board(item("code", code="y = " + "1" * 30))  # > 28 chars: portrait.code_too_wide
    assert fits(long_lines)["code_walkthrough"] == 1.0
    assert fits(long_lines, "portrait")["code_walkthrough"] == FIT_STRETCHED
    assert fits(board(item("code", code="\n".join(["x"] * 18))))["code_walkthrough"] == FIT_STRETCHED  # <= 17 lines
    assert fits(board(item("misconception"))) == {"myth_fact": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(board(item("misconception", justification=None))) == {"board_generic": FIT_FALLBACK}  # no fact card


def test_fit_prompts_and_recap() -> None:
    blank = item("question", blank=True)
    assert fits(board(blank, role="predict")) == {"predict": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(board(blank, role="check")) == {"check": 1.0, "board_generic": FIT_FALLBACK}
    assert rows(board(item("question"), role="predict")) == ["fallback"]  # a predict question is blank
    recap = [item("question", 1, blank=True), item("question", 2, blank=True)] + [item("takeaway", n) for n in range(3)]
    assert fits(board(*recap, type="recap")) == {"recap_retrieval": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(board(*recap, type="recap", presenter=True)) == {"aadhi_recap": 1.0, "board_generic": FIT_FALLBACK}
    assert fits(board(*recap, item("takeaway", 7), type="recap"))["recap_retrieval"] == FIT_STRETCHED
    assert "recap_retrieval" not in fits(board(*recap, item("bullet"), type="recap"))


def test_fit_media_row() -> None:
    two = board(item("bullet", 1), item("bullet", 2), panel="image")
    assert rows(two) == ["list", "media"]
    assert fits(two) == {"list_build": 1.0, "media_split": 1.0, "spotlight_image": FIT_ALTERNATIVE,
                         "board_generic": FIT_FALLBACK}
    label = board(item("bullet"), panel="image")  # an application image under a one-line label
    assert fits(label)["full_bleed_media"] == FIT_ALTERNATIVE
    assert fits(board(item("bullet"), panel="manim")) == {"animation_stage": FIT_ALTERNATIVE,
                                                          "board_generic": FIT_FALLBACK}
    assert fits(board(item("bullet"), panel="model_3d")) == {"model_spin": FIT_ALTERNATIVE,
                                                             "board_generic": FIT_FALLBACK}
    assert fits(board(item("bullet"), item("figure"))) == {"spotlight_image": FIT_ALTERNATIVE,
                                                          "board_generic": FIT_FALLBACK}
    assert fits(board(item("bullet"), panel="terminal"))["media_split"] == 1.0
    four = board(*[item("bullet", n) for n in range(4)], panel="image")  # > 3 text items: no media row
    assert rows(four) == ["list"] and "media_split" not in fits(four)
    assert rows(board(item("bullet"), panel="gif")) == ["fallback"]  # live-only GIFs are no media panel
    assert fits(board(item("heading"), panel="skill_tree"))["concept_tree"] == 1.0
    assert all(tp.TEMPLATES["diagram_build"].fit(s, "landscape") == 0 for s in (two, label))  # needs a bound SVG (W2)


def test_fit_last_scene_fallback_and_formats() -> None:
    closing = board(item("takeaway"))
    assert fits(closing, last=True) == {"kinetic_statement": 1.0, "outro_next": 1.0, "board_generic": FIT_FALLBACK}
    assert "outro_next" not in fits(closing) and "outro_next" not in fits(board(item("bullet", 1), item("bullet", 2)),
                                                                         last=True)
    other = board(item("formula"), item("bullet"))  # no template accepts it: the fallback row
    assert rows(other) == ["fallback"] and fits(other) == {"board_generic": 1.0}
    assert fits(board()) == {"board_generic": 1.0}
    assert tp.TEMPLATES["thumbnail"].fit(closing, "portrait") == 0 and tp.TEMPLATES["thumbnail"].fit(
        closing, "landscape") == 0  # never a scene's template
    assert all(spec.fit(closing, "square") == 0 for spec in tp.TEMPLATES.values())  # type: ignore[arg-type]


def test_cross_class_alternatives_are_the_two_the_design_names() -> None:
    scenes = [board(item("heading"), item("question"), role="hook", presenter=True), board(item("stat"), role="hook"),
              board(item("quote")), board(*[item("step", n, date="1") for n in range(4)], sequence="timeline"),
              board(item("bullet"), panel="image")]
    seen: set[tuple[str, str]] = set()
    for scene in scenes:
        for row in tp.signals(scene, chapter_end=True):
            assert tp.TEMPLATES[row.preferred].semantic_class == row.semantic_class, row
            for alt in row.alternatives:
                if tp.TEMPLATES[alt].semantic_class != row.semantic_class:
                    seen.add((row.id, alt))
    assert seen == tp.CROSS_CLASS_ALTERNATIVES == {("presenter", "hook_question"), ("hook", "stat_punch")}


def test_fit_values_are_ordered_and_swap_threshold_holds() -> None:
    assert FIT_PREFERRED > FIT_ALTERNATIVE >= tp.SWAP_MIN_FIT > FIT_STRETCHED > FIT_FALLBACK > tp.FIT_NONE == 0
