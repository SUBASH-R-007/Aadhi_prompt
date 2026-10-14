"""The Dynamic theme catalogue and its validator (aadhi.compose.dynamic.themes; design §4.5-§4.8, §4.11, R-52..R-56,
§7.5; B2 plan §1.1-§1.3).

* B1 compatibility: every colour token B1's ``presentation.py`` emitted for the four R1 families is pinned here (values
  captured from the B1 code before B2a moved the maths), through both ``themes`` and ``presentation``.
* The catalogue's rows (families, personalities, caption styles, faces, cue colours) and the caption metrics against
  ``web/css/dynamic.css``.
* The derived structure colours the resolver stores (block, block inks, scrim), with R-54's red lowering.
* The validator: the released catalogue has no findings; the measured worst values reproduce the design's numbers; it
  does fail on a bad palette or family (so the pinned "no findings" means something).
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from aadhi.compose import presentation as pres
from aadhi.compose.dynamic import colour_math as cm
from aadhi.compose.dynamic import themes as th

ROOT = Path(__file__).resolve().parents[3]

# --- B1 values (captured from aadhi/compose/presentation.py at 54c72c5, before B2a) --------------------------------

B1_CONTENT = {
    "dark": {"ink": "#F3F5F9", "ink2": "#B9BEC7", "entity:0": "#0FE3FE", "entity:1": "#C4A4FE",
             "entity:2": "#FEACE3", "warn": "#F8A87F", "ok": "#5DD8AA", "mark": "#F2D935", "focus": "#F2D935"},
    "light": {"ink": "#141822", "ink2": "#484D58", "entity:0": "#0267C7", "entity:1": "#80369E",
              "entity:2": "#870C62", "warn": "#9F4503", "ok": "#016F50", "mark": "#F2D935", "focus": "#141822"},
}
# family -> slot -> (struct.accent, bg, bg-alt, bg-tint, surface)
B1_STRUCTURE = {
    "midnight_indigo": [("#FF936D", "#10132C", "#181D37", "#26120C", "#252A3F"),
                        ("#BBBC22", "#10132C", "#181D37", "#191A06", "#252A3F"),
                        ("#EFA30F", "#10132C", "#181D37", "#221605", "#252A3F")],
    "deep_teal": [("#FF89B0", "#001B1C", "#002527", "#251118", "#0E3133"),
                  ("#F49F1E", "#001B1C", "#002527", "#221505", "#0E3133"),
                  ("#FF9277", "#001B1C", "#002527", "#26120D", "#0E3133")],
    "paper_ink": [("#BE3D70", "#FDF2DD", "#F3E8D3", "#FFEAEF", "#FFFDFA"),
                  ("#A16600", "#FDF2DD", "#F3E8D3", "#FDEDDC", "#FFFDFA"),
                  ("#C44323", "#FDF2DD", "#F3E8D3", "#FFEBE6", "#FFFDFA")],
    "mint_studio": [("#4F69D7", "#E4F9F1", "#DAEFE7", "#EAF0FF", "#FBFEFD"),
                    ("#B24393", "#E4F9F1", "#DAEFE7", "#FEE9F6", "#FBFEFD"),
                    ("#8C54C3", "#E4F9F1", "#DAEFE7", "#F4ECFF", "#FBFEFD")],
}
B1_ACCENT_HUES = {"midnight_indigo": (40.0, 110.0, 75.0), "deep_teal": (0.0, 70.0, 35.0),
                  "paper_ink": (0.0, 70.0, 35.0), "mint_studio": (270.0, 340.0, 305.0)}
B1_FAMILIES = {"midnight_indigo": ("dark", 275.0, (40.0, 110.0), 0.05), "deep_teal": ("dark", 200.0, (0.0, 70.0), 0.045),
               "paper_ink": ("light", 85.0, (0.0, 70.0), 0.03), "mint_studio": ("light", 170.0, (270.0, 340.0), 0.025)}

# Derived tokens of the R1 families (new in B2a; pinned so any change is deliberate): block.base, block.highlight,
# block.ink, block.ink2, scrim. Versus what the B1 stage derives at run time: paper_ink/2's block is the R-54
# red-lowered one (B1 #C44323 -> #B05641), and block.ink2 is one mix step nearer the ink where B1's falls below 3:1
# during a hold (midnight_indigo/0 #F2E5EA -> #F6EDF1, /2 #F4E4E5 -> #F8EDEE, deep_teal/0 #EAE7F2 -> #F1EFF7;
# owner decision 2026-10-13, A1 review finding 1).
DERIVED = {
    "midnight_indigo": [("#A15778", "#C46979", "#FFFFFF", "#F6EDF1", "#10132CC7"),
                        ("#00886C", "#489F5F", "#14161C", "#1C2D3D", "#10132CC7"),
                        ("#AC525D", "#CC684E", "#FFFFFF", "#F8EDEE", "#10132CC7")],
    "deep_teal": [("#7B64A8", "#A970B6", "#FFFFFF", "#F1EFF7", "#001B1CC7"),
                  ("#6C7B23", "#998A0A", "#14161C", "#162E3B", "#001B1CC7"),
                  ("#837224", "#B07E2F", "#14161C", "#182933", "#001B1CC7")],
    "paper_ink": [("#BE3D70", "#C7537D", "#FFFFFF", "#F6D9E1", "#FDF2DDC7"),
                  ("#A16600", "#AB752E", "#FFFFFF", "#EDE0D1", "#FDF2DDC7"),
                  ("#B05641", "#BA6853", "#FFFFFF", "#F2DDD7", "#FDF2DDC7")],
    "mint_studio": [("#4F69D7", "#5E79DC", "#FFFFFF", "#D9E1FA", "#E4F9F1C7"),
                    ("#B24393", "#BB589E", "#FFFFFF", "#F3DAE9", "#E4F9F1C7"),
                    ("#8C54C3", "#9766CA", "#FFFFFF", "#E7DDF5", "#E4F9F1C7")],
}


@pytest.fixture(autouse=True)
def _fresh_cache():
    th._scene_colours.cache_clear()
    yield
    th._scene_colours.cache_clear()


def test_b1_content_tokens_are_unchanged_in_value_and_key_order() -> None:
    for mode, expected in B1_CONTENT.items():
        for got in (th.content_tokens(mode), pres.content_tokens(mode)):
            assert list(got.items()) == list(expected.items())  # the stored timeline's JSON keeps the order
    assert pres.content_tokens("anything else") == B1_CONTENT["light"]  # B1: any mode but "dark" is light


def test_b1_structure_tokens_and_accent_hues_are_unchanged() -> None:
    assert set(pres.FAMILIES) == set(B1_STRUCTURE) == set(th.RELEASED_FAMILIES)
    for fid, slots in B1_STRUCTURE.items():
        family = pres.FAMILIES[fid]
        assert (family.mode, family.hue, family.accent, family.chroma) == B1_FAMILIES[fid]
        assert tuple(pres.accent_hue(family, k) for k in range(3)) == B1_ACCENT_HUES[fid]
        assert [pres.accent_hue(family, k) for k in range(3, 9)] == list(B1_ACCENT_HUES[fid]) * 2
        for slot, values in enumerate(slots):
            expected = dict(zip(th.STRUCTURE_KEYS, values))
            hue = pres.accent_hue(family, slot)
            assert list(pres.structure_tokens(family, hue).items()) == list(expected.items())
            assert th.structure_tokens(family, hue) == expected
            scene = th.scene_colours(family, slot)
            assert {k: scene.tokens[k] for k in th.STRUCTURE_KEYS} == expected, "no accent nudge on R1 (B1 kept)"
            assert scene.accent_shift == 0.0


def test_presentation_keeps_its_b1_names() -> None:
    assert pres.Family is th.Family
    assert pres.FAMILIES == th.RELEASED_FAMILIES and pres.FAMILIES is not th.RELEASED_FAMILIES  # a copy
    assert pres.family_for("deep_teal") is th.FAMILIES["deep_teal"]
    assert pres.family_for("teal_coral") == pres.family_for("teal_coral") in th.RELEASED_FAMILIES.values()
    assert pres.family_for("aubergine") in th.RELEASED_FAMILIES.values(), "W2 families are not resolved by B1 code"
    assert pres.oklch_hex(0.62796, 0.25768, 29.234) == cm.palette_hex(0.62796, 0.25768, 29.234) == "#FF0000"
    with pytest.raises(AttributeError):
        _ = pres.NOT_A_NAME  # type: ignore[attr-defined]


def test_classic_imports_load_no_dynamic_module() -> None:
    """compose.timeline imports presentation for every build: the Dynamic modules must stay unloaded (plan §2)."""
    code = ("import sys, aadhi.compose.timeline, aadhi.compose.presentation as p; p.dynamic_enabled(object()); "
            "print(sorted(m for m in sys.modules if m.startswith('aadhi.compose.dynamic')))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                         check=True)
    assert out.stdout.strip() == "[]", out.stdout
    code_dyn = "import sys, aadhi.compose.presentation as p; p.content_tokens('dark'); print('aadhi.compose.dynamic.themes' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code_dyn], cwd=ROOT, capture_output=True, text=True, timeout=120,
                         check=True)
    assert out.stdout.strip() == "True", "the check above can fail"


# --- the catalogue -------------------------------------------------------------------------------------------------

# §4.7 table: id -> (mode, bg H, accent window, alternate window, default personality in R1 terms, design default, R1)
FAMILY_TABLE = {
    "midnight_indigo": ("dark", 275.0, (40.0, 110.0), None, "soft", "cinematic", True),
    "deep_teal": ("dark", 200.0, (0.0, 70.0), None, "crisp", "crisp", True),
    "aubergine": ("dark", 320.0, (140.0, 210.0), None, "cinematic", "cinematic", False),
    "charcoal_math": ("dark", 260.0, (200.0, 280.0), None, "chalk", "chalk", False),
    "forest_night": ("dark", 155.0, (20.0, 90.0), None, "soft", "soft", False),
    "cosmic": ("dark", 285.0, (30.0, 70.0), (170.0, 210.0), "cinematic", "cinematic", False),
    "paper_ink": ("light", 85.0, (0.0, 70.0), None, "crisp", "editorial", True),
    "blueprint": ("light", 230.0, (230.0, 290.0), (30.0, 70.0), "crisp", "crisp", False),
    "mint_studio": ("light", 170.0, (270.0, 340.0), None, "soft", "soft", True),
    "sunrise": ("light", 60.0, (260.0, 330.0), None, "bouncy_soft", "bouncy_soft", False),
    "chalkboard": ("dark", 150.0, (60.0, 130.0), None, "chalk", "chalk", False),
    "bold_blocks": ("dark", 265.0, (0.0, 300.0), None, "crisp", "crisp", False),
}


def test_the_twelve_families() -> None:
    assert list(th.FAMILIES) == list(FAMILY_TABLE), "§4.7 table order"
    for fid, (mode, hue, accent, alt, personality, target, released) in FAMILY_TABLE.items():
        f = th.FAMILIES[fid]
        assert (f.id, f.mode, f.hue, f.accent, f.accent_alt) == (fid, mode, hue, accent, alt)
        assert (f.personality, f.personality_target, f.release == "R1") == (personality, target, released)
        assert f.name
        if released:
            assert th.PERSONALITIES[f.personality].release == "R1", "an R1 family defaults to an R1 personality"
    assert th.FAMILIES["charcoal_math"].chroma == 0.01 and th.FAMILIES["cosmic"].chroma == 0.07
    assert th.FAMILIES["chalkboard"].tones is not None and th.FAMILIES["chalkboard"].tones[0] == 0.28
    assert "PROVISIONAL" in th.FAMILIES["bold_blocks"].note


@pytest.mark.parametrize("fid", list(FAMILY_TABLE))
def test_accent_rotation_alternates_and_keeps_neighbours_apart(fid: str) -> None:
    f = th.FAMILIES[fid]
    hues = [th.accent_hue(f, k) for k in range(4 * f.accent_period)]
    assert hues[: f.accent_period] == hues[f.accent_period: 2 * f.accent_period], "periodic"
    for a, b in zip(hues, hues[1:]):
        assert cm.hue_distance(a, b) >= th.HUE_SEPARATION_MIN, (fid, a, b)
    for k, h in enumerate(hues):
        lo, hi = f.windows[k % len(f.windows)]
        assert lo <= h <= hi or lo <= h + 360 <= hi, (fid, k, h)
    if f.accent_alt is not None:  # two windows alternate first: k even in the first, odd in the second
        assert th.accent_hue(f, 0) == f.accent[0] and th.accent_hue(f, 1) == f.accent_alt[0]
        assert th.accent_hue(f, 2) == f.accent[1] and th.accent_hue(f, 4) == (f.accent[0] + f.accent[1]) / 2


def test_content_palettes_and_alt() -> None:
    assert th.CONTENT["dark"].entities == ("#0FE3FE", "#C4A4FE", "#FEACE3")
    assert th.CONTENT["light"].entities == ("#0267C7", "#80369E", "#870C62")
    assert (th.CONTENT["dark"].warn, th.CONTENT["dark"].ok) == ("#F8A87F", "#5DD8AA")
    assert (th.CONTENT["light"].warn, th.CONTENT["light"].ok) == ("#9F4503", "#016F50")
    assert th.CONTENT["dark"].mark == th.CONTENT["light"].mark == "#F2D935"
    assert th.content_alt_tokens("light") == th.content_tokens("dark")
    assert th.content_alt_tokens("dark") == th.content_tokens("light")
    t = th.content_tokens("dark")
    t["ink"] = "#000000"
    assert th.content_tokens("dark")["ink"] == "#F3F5F9", "a new dict each call"
    assert tuple(th.content_tokens("light")) == th.CONTENT_ROLES


def test_cue_colours_dim_and_scrim() -> None:
    dark, light = th.CUE_COLOURS["dark"], th.CUE_COLOURS["light"]
    assert (dark.marker_ink, dark.marker_alpha, dark.underline) == ("#14161C", 1.0, "#F2D935")
    assert (light.marker_ink, light.marker_alpha, light.underline) == ("#141822", 0.35, "#141822")
    assert dark.marker_ink == th.ON_BLOCK[1]
    assert th.DIM_ALPHA == 0.7
    assert th.SCRIM_ALPHA >= th.SCRIM_ALPHA_MIN == 0.55
    assert th.SCRIM_ALPHA_HEX == "c7"


def test_personalities() -> None:
    assert list(th.PERSONALITIES) == ["crisp", "soft", "cinematic", "editorial", "chalk", "bouncy_soft"]
    released = {k for k, p in th.PERSONALITIES.items() if p.release == "R1"}
    assert released == {"crisp", "soft"}
    crisp, soft = th.PERSONALITIES["crisp"], th.PERSONALITIES["soft"]
    assert (crisp.duration_scale, crisp.cue_term_style, dict(crisp.caption_style)) == (
        0.85, "underline", {"landscape": "clean_pill", "portrait": "bold_outline"})
    assert (soft.duration_scale, soft.cue_term_style, dict(soft.caption_style)) == (
        1.15, "marker", {"landscape": "clean_pill", "portrait": "clean_pill"})
    assert soft.transitions == ("crossfade", "iris") and soft.transitions_r1 == ("cut", "crossfade")  # iris is W2
    assert crisp.transitions_r1 == ("cut", "push", "colour_wipe")
    assert (crisp.music_mood, soft.music_mood) == ("bright-curious", "calm-focus")
    assert (crisp.text_entrances, soft.text_entrances) == (("baseline_reveal",), ("fade_rise",))
    # §4.8: Editorial has both entrances; Bouncy-soft's pop is the `pop` easing, icons and "?" only, never text
    assert th.PERSONALITIES["editorial"].text_entrances == ("highlighter_swipe", "typewriter")
    assert [k for k, p in th.PERSONALITIES.items() if p.icons_only] == ["bouncy_soft"]
    for p in th.PERSONALITIES.values():
        assert p.sfx_pack and p.music_mood and p.text_entrances and p.transitions
        assert p.icons_only == any("pop" in e for e in p.text_entrances), p.id
        for fmt in th.FORMATS:
            assert p.caption_style[fmt] in th.CAPTION_STYLES
        if p.release == "R1":
            assert all(th.CAPTION_STYLES[p.caption_style[f]].release == "R1" for f in th.FORMATS)
            assert p.transitions_r1


def test_faces() -> None:
    """The faces are faces.py's (B2b: the one face table; tests/compose/dynamic/test_faces.py), re-exported here."""
    assert th.DISPLAY_FACES_R1 == ("outfit", "poppins", "lexend")
    roles = {f.role for f in th.FACES.values()}
    assert roles == {"display", "body", "mono", "indic"}
    vendored = {f.id for f in th.FACES.values() if f.vendored}
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["dependencies"]
    for fid in vendored:  # vendored: its @fontsource package pinned in package.json
        assert th.FACES[fid].package in package, fid
    assert all(f.licence.startswith("OFL-1.1") for f in th.FACES.values())


def _css_block(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def _prop(block: str, name: str) -> str:
    m = re.search(rf"(?:^|;|\s){re.escape(name)}\s*:\s*([^;]+);", block)
    assert m, name
    return m.group(1).strip()


def test_caption_metrics_match_the_stylesheet() -> None:
    css = re.sub(r"/\*.*?\*/", "", (ROOT / "web/css/dynamic.css").read_text(encoding="utf-8"), flags=re.S)
    pill = th.CAPTION_STYLES["clean_pill"].metrics
    outline = th.CAPTION_STYLES["bold_outline"].metrics
    assert pill is not None and outline is not None
    for fmt in th.FORMATS:
        block = _css_block(css, f".dyn-captions--{fmt} .dyn-caption-text")
        assert _prop(block, "font-size") == f"{pill.size[fmt]}px" == f"{outline.size[fmt]}px"
        assert int(_prop(block, "font-weight")) == pill.weight[fmt]
        assert float(_prop(block, "line-height")) == pill.line_height[fmt]["latin"] == outline.line_height[fmt]["latin"]
        assert pill.line_height[fmt]["indic"] >= 1.35 and outline.line_height[fmt]["indic"] >= 1.35  # plan §2
    box = _css_block(css, ".dyn-captions--clean_pill .dyn-caption")
    assert _prop(box, "padding") == f"{pill.padding_y}px {pill.padding_x}px"
    assert _prop(box, "border-radius") == f"{pill.radius_px}px"
    r, g, b, a = (float(x) for x in re.findall(r"[\d.]+", _prop(box, "background")))
    assert cm.to_hex((r / 255, g / 255, b / 255)).upper() == pill.backdrop and a == pill.backdrop_alpha
    assert _prop(_css_block(css, ".dyn-captions--clean_pill .dyn-caption-text"), "color") == "#fff"
    text = _css_block(css, ".dyn-captions--bold_outline .dyn-caption-text")
    width, colour = _prop(text, "-webkit-text-stroke").split()
    assert int(width.removesuffix("px")) == 2 * outline.outline_px and colour.upper() == outline.outline
    assert int(_prop(text, "font-weight")) == outline.weight["landscape"] == outline.weight["portrait"] == 700
    assert _prop(text, "word-spacing") == f"{outline.word_spacing_em}em"
    assert _prop(_css_block(css, ".dyn-caption-key"), "color").upper() == pill.key_ink == outline.key_ink
    js = (ROOT / "web/js/dynamic/captions.js").read_text(encoding="utf-8")
    assert f"CAPTION_INK = '{th.CAPTION_INK}'" in js and f"CAPTION_PILL = '{th.CAPTION_PILL}'" in js
    assert f"CAPTION_MARK = '{th.CAPTION_MARK}'" in js
    assert {k for k, s in th.CAPTION_STYLES.items() if s.release == "R1"} == {"clean_pill", "bold_outline"}
    assert all(s.metrics is None for s in th.CAPTION_STYLES.values() if s.release == "W2")


def test_stage_constants_mirror_the_js() -> None:
    """The constants the mirrors use are the JS ones (the vectors prove the behaviour; this names the source)."""
    # B2a step B: the run-time derivations a look without stored tokens gets (blockColors, blockInk2, the scrim) are
    # core.js makeRoles' block roles; colour_block.js, stage.js and board_generic.js re-export / read them from there
    core = (ROOT / "web/js/dynamic/core.js").read_text(encoding="utf-8")
    assert "const ON_BLOCK = Object.freeze(['#FFFFFF', '#14161C']);" in core
    assert "mixOklch(tokens.bg, '#FFFFFF', 0.85) : mixOklch(tokens.bg, content.ink, 0.1)" in core
    assert "mixOklch(accent, tokens.bg || '#000000', 0.38)" in core
    assert "mixOklch(base, accent, 0.35) : mixOklch(base, '#FFFFFF', 0.1)" in core
    mesh = (ROOT / "web/js/dynamic/backgrounds/mesh_drift.js").read_text(encoding="utf-8")
    assert "export const FREEZE_DESATURATION = 0.3;" in mesh
    assert "mixOklch(base, accent, 0.2)" in mesh and "mixOklch(base, ink, 0.07)" in mesh
    assert "export const BLOCK_INK2_CONTRAST = 3;" in core and "[0.25, 0.2, 0.15, 0.1, 0.05]" in core
    assert "export const SCRIM_ALPHA_HEX = 'c7';" in core
    stage = (ROOT / "web/js/dynamic/stage.js").read_text(encoding="utf-8")
    assert "export { BLOCK_INK2_CONTRAST, blockInk2 } from './core.js';" in stage
    board = (ROOT / "web/js/dynamic/templates/board_generic.js").read_text(encoding="utf-8")
    assert "export { SCRIM_ALPHA_HEX };" in board


# --- derived scene colours -------------------------------------------------------------------------------------------


def test_derived_tokens_of_the_released_families() -> None:
    for fid, slots in DERIVED.items():
        family = th.FAMILIES[fid]
        for slot, values in enumerate(slots):
            sc = th.scene_colours(family, slot)
            assert list(sc.tokens) == list(th.SCENE_TOKEN_KEYS)
            assert tuple(sc.tokens[k] for k in th.DERIVED_KEYS) == values, (fid, slot)
            assert sc.block_ok and sc.accent_hue == th.accent_hue(family, slot)
            assert all(cm.is_hex6(v) for k, v in sc.tokens.items() if k != "scrim")
            assert sc.tokens["scrim"] == (sc.tokens["bg"] + th.SCRIM_ALPHA_HEX).upper()
            base, highlight = th.block_colours(family.mode, sc.tokens)
            if sc.red_factor == 1.0:  # the stored block is the stage's own blockColors()
                assert (sc.tokens["block.base"], sc.tokens["block.highlight"]) == (base.upper(), highlight.upper())
    sc = th.scene_colours(th.FAMILIES["paper_ink"], 0)
    sc.tokens["bg"] = "#000000"
    assert th.scene_tokens(th.FAMILIES["paper_ink"], 0)["bg"] == "#FDF2DD", "callers get a copy"


def test_block_text_holds_through_the_hold_desaturation() -> None:
    for family in th.RELEASED_FAMILIES.values():
        for slot in range(family.accent_period):
            t = th.scene_tokens(family, slot)
            block = [t["block.base"], t["block.highlight"]]
            shown = block + [cm.desaturate(c, th.HOLD_DESATURATION) for c in block]
            for key in ("block.ink", "block.ink2"):
                assert cm.worst_contrast(t[key], shown) >= th.CONTRAST_LARGE, (family.id, slot, key)


# block.ink2 differs from the B1 stage's run-time blockInk2 exactly in these palette slots (every family, one accent
# period each; B1's is below 3:1 while held there)
HOLD_SAFE_INK2 = {("midnight_indigo", 0), ("midnight_indigo", 2), ("deep_teal", 0), ("forest_night", 1),
                  ("cosmic", 2), ("cosmic", 4), ("bold_blocks", 0)}


def test_block_ink_is_the_stages_choice_and_ink2_is_hold_safe() -> None:
    """Owner decision (A1 review finding 1): block.ink is stage.js's own choice over the shown block (no white /
    near-black flip on a hundredth of a contrast point); block.ink2 is blockInk2's mix, required to hold 3:1 while
    held too, so it is one mix step nearer the ink than the B1 stage's in the slots of HOLD_SAFE_INK2 and the stage's
    own elsewhere."""
    changed = set()
    for family in th.FAMILIES.values():
        ink = th.CONTENT[family.mode].ink
        for slot in range(family.accent_period):
            t = th.scene_tokens(family, slot)
            shown = [t["block.base"], t["block.highlight"]]
            stage_ink = th.ink_on(ink, shown)
            assert t["block.ink"] == stage_ink.upper(), (family.id, slot)
            stage_ink2 = th.block_ink2(stage_ink, shown)
            if t["block.ink2"] != stage_ink2.upper():
                changed.add((family.id, slot))
                held = shown + [cm.desaturate(c, th.HOLD_DESATURATION) for c in shown]
                assert cm.worst_contrast(stage_ink2, held) < th.CONTRAST_LARGE, (family.id, slot)
                assert t["block.ink2"] == th.block_ink2(stage_ink, held).upper()
    assert changed == HOLD_SAFE_INK2


def test_block_ink_falls_back_to_the_held_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only when the stage's choice misses the threshold while held is block.ink chosen over the shown and held
    colours: on this light block white wins as shown (3.91 vs near-black's 3.84) but drops to 3.82 held."""
    tokens = {"struct.accent": "#B65277", "bg": "#FDF2DD"}
    ink = th.CONTENT["light"].ink
    assert th._block_set("light", tokens, ink)[2] == "#FFFFFF"
    monkeypatch.setattr(th, "CONTRAST_LARGE", 3.83)  # between white's held worst and near-black's
    assert th._block_set("light", tokens, ink)[2] == "#14161C"


def test_a_saturated_red_block_gets_its_chroma_lowered() -> None:
    family = th.FAMILIES["paper_ink"]
    raw = th.block_colours("light", th.structure_tokens(family, th.accent_hue(family, 2)))
    assert cm.red_ratio(raw[0]) >= cm.SATURATED_RED_RATIO, "B1's slot-2 block is a saturated red"
    sc = th.scene_colours(family, 2)
    assert 0.5 < sc.red_factor < 1.0
    for key, before in zip(("block.base", "block.highlight"), raw):
        after = sc.tokens[key]
        assert cm.red_ratio(after) <= th.RED_SAFE_RATIO
        a, b = cm.to_oklch(before), cm.to_oklch(after)
        assert abs(a.l - b.l) < 0.01 and cm.hue_distance(a.h, b.h) < 3.0 and b.c < a.c  # only chroma lowered
    assert sc.tokens["struct.accent"] == "#C44323", "the accent itself (pips, borders) is not a full-frame block"
    assert th.lower_red(["#4F69D7", "#5e79dc"]) == (("#4F69D7", "#5e79dc"), 1.0)


def test_the_accent_nudge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a failing block text moves the accent (by <= 0.06 L); real families never need it (inkOn has white and
    near-black), so the failure is forced here: white is the only block ink and the light accent is lifted to L 0.7,
    where white misses 3:1 on some hues by little (a nudge fixes it) and on others by much (none does)."""
    monkeypatch.setattr(th, "ON_BLOCK", ("#FFFFFF",))
    monkeypatch.setitem(th.CONTENT, "light", dataclasses.replace(th.CONTENT["light"], ink="#FFFFFF"))
    monkeypatch.setitem(th.ACCENT_LC, "light", (0.7, 0.17))
    nudged, failed = [], []
    for hue in range(0, 360, 10):
        f = th.Family("light", 85.0, (float(hue), float(hue) + 70.0), 0.03, id=f"probe{hue}")
        sc = th.scene_colours(f, 0)
        if sc.accent_shift:
            nudged.append((f, sc))
        if not sc.block_ok:
            failed.append((f, sc))
    assert nudged and failed
    f, sc = nudged[0]
    assert 0 < abs(sc.accent_shift) <= th.ACCENT_NUDGE_MAX + 1e-9 and sc.block_ok
    assert sc.tokens["struct.accent"] != th.structure_tokens(f, sc.accent_hue)["struct.accent"]
    assert sc.tokens["struct.accent"] == th.structure_tokens(f, sc.accent_hue, accent_shift=sc.accent_shift)[
        "struct.accent"]
    f, sc = failed[0]
    assert sc.accent_shift == 0.0, "no nudge within +-0.06 holds: the un-nudged colours are kept"
    report = th.validate([f])
    assert any(x.code == "block_text" and x.family == f.id for x in report.findings)
    assert report.scrims["colour_block"][f.id] is True


def test_preset_colours() -> None:
    family = th.FAMILIES["midnight_indigo"]
    t = th.scene_tokens(family, 1)
    for tone in th.TONES:
        assert th.preset_colours("mesh_drift", family, 1, tone) == th.mesh_drift_colours(t, tone, "#F3F5F9")
    assert th.preset_colours("colour_block", family, 1) == [t["block.base"], t["block.highlight"]]
    held = th.preset_colours("mesh_drift", family, 1, "bg", hold=True)
    assert held == [cm.desaturate(c, 0.3) for c in th.preset_colours("mesh_drift", family, 1, "bg")]
    with pytest.raises(ValueError):
        th.preset_colours("dot_field", family, 1)
    assert th.tone_colour(t, "bg-tint") == t["bg-tint"] and th.tone_colour(t, "nonsense") == t["bg"]


def test_code_surface() -> None:
    assert th.code_surface(th.FAMILIES["deep_teal"]) == "#0E3133"
    paper = th.code_surface(th.FAMILIES["paper_ink"])
    assert cm.relative_luminance(paper) < 0.05, "a dark editor in a light family"
    assert paper == cm.palette_hex(0.29, 0.03, 85.0)


# --- the validator ---------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report() -> th.Report:
    th._scene_colours.cache_clear()
    return th.validate()


def test_the_released_catalogue_has_no_findings(report: th.Report) -> None:
    assert report.released_findings() == (), report.released_findings()[:5]
    assert report.nudges == {}, "no R1 accent moved (B1 tokens kept)"


def test_w2_families_have_no_findings_either(report: th.Report) -> None:
    assert report.findings == ()


def test_advisories_are_the_known_mesh_luminance_spread(report: th.Report) -> None:
    """R-55's behind-text luminance ratio of mesh_drift's pure colours exceeds 1.5 in dark families (owner: B2f)."""
    assert {a.code for a in report.advisories} == {"bg_luminance_ratio"}
    released = {a.family for a in report.advisories if a.release == "R1"}
    assert released == {"midnight_indigo", "deep_teal"}
    assert max(a.measured for a in report.advisories if a.release == "R1") < 1.61
    assert report.worst("bg_luminance_ratio", "paper_ink").value <= th.BG_LUMINANCE_RATIO_MAX


def test_the_design_numbers_are_reproduced(report: th.Report) -> None:
    # §4.7 "Colour vision": worst entity-to-entity distance 0.051 (dark) and 0.068 (light)
    assert round(report.worst("cvd_entities", "dark").value, 3) == 0.051
    assert round(report.worst("cvd_entities", "light").value, 3) == 0.068
    assert report.worst("cvd_entity_fixed", "dark").value >= th.CVD_DISTANCE_MIN
    assert report.worst("hue_separation", "light").value >= 30.0  # entity:1 vs entity:2, 30.2°
    assert report.worst("marker", "dark").value >= 12.0
    for fid in th.RELEASED_FAMILIES:
        assert report.worst("content_contrast", fid).value >= 4.5
        assert report.worst("caption", fid).value >= 7.0


def test_forced_scrims_and_the_chroma_rule(report: th.Report) -> None:
    released = {p: {f: v for f, v in t.items() if f in th.RELEASED_FAMILIES} for p, t in report.scrims.items()}
    assert released == {
        "mesh_drift": {"midnight_indigo": False, "deep_teal": False, "paper_ink": True, "mint_studio": True},
        "colour_block": {"midnight_indigo": False, "deep_teal": False, "paper_ink": False, "mint_studio": False},
    }
    assert report.scrim_roles["mesh_drift"]["paper_ink"] == ("entity:0", "warn", "ok")
    assert th.forced_scrims() == released
    assert report.chroma_restricted == {"dark": (), "light": ("entity:0", "entity:1", "entity:2", "warn")}
    assert report.red_lowered["paper_ink"] == {2: pytest.approx(0.7192, abs=1e-4)}


def test_the_studio_hook() -> None:
    assert th.STUDIO_WALL is None, "B2f measures the wall; no guessed value"
    # a mid-grey wall still takes R-53 body text (the best of ink, white and near-black: 4.58:1) ...
    passing = th.validate(th.RELEASED_FAMILIES, studio="#808080")
    assert passing.worst("studio", "paper_ink").value == pytest.approx(4.579, abs=1e-3)
    assert not [f for f in passing.findings if f.code == "studio"]
    # ... a slightly darker one cannot (4.29:1): a finding on every released family
    failing = th.validate(th.RELEASED_FAMILIES, studio="#7A7A7A")
    assert {f.family for f in failing.findings if f.code == "studio"} == set(th.RELEASED_FAMILIES)
    assert not [m for m in th.validate(th.RELEASED_FAMILIES).measures if m.code == "studio"]


def test_the_validator_fails_a_bad_palette(monkeypatch: pytest.MonkeyPatch) -> None:
    """A content colour too close in hue, too weak in contrast: findings on R1 (a build failure)."""
    bad = dataclasses.replace(th.CONTENT["light"], entities=("#0267C7", "#2A5BC0", "#870C62"), ok="#7FBF9F")
    monkeypatch.setitem(th.CONTENT, "light", bad)
    r = th.validate(th.RELEASED_FAMILIES)
    codes = {f.code for f in r.released_findings()}
    assert {"hue_separation", "cvd_entities", "content_contrast"} <= codes, codes


def test_the_validator_fails_a_bad_family() -> None:
    """A W2 family whose background defeats the content palette: findings tagged W2 (reported, not blocking)."""
    murky = th.Family("dark", 200.0, (0.0, 20.0), 0.05, id="murky", release="W2", tones=(0.45, 0.5, 0.46, 0.55))
    r = th.validate([murky])
    mine = [f for f in r.findings if f.family == "murky"]
    assert mine and all(f.release == "W2" for f in mine)
    assert {"content_contrast", "accent_rotation"} <= {f.code for f in mine}
    assert r.released_findings() == ()
