"""The Dynamic mode's colour module (aadhi.compose.dynamic.colour_math) against the stage's own colour code.

``tests/fixtures/colour_vectors.json`` is written by ``scripts/gen_colour_vectors.mjs`` from web/js/motion/color.js and
the colour helpers of web/js/dynamic (``web/tests/motion/colourVectors.test.js`` checks the JS still produces it).
Here every vector must be reproduced by the Python port: hex strings equal, floats within 1e-9 (design §4.7, B2 plan
§1.2: a colour the resolver stores is the colour the stage would derive). Plus the properties the port relies on
(``Math.round`` ties, JavaScript's ``%``) and B1's palette generator.
"""

from __future__ import annotations

import functools
import json
import math
from pathlib import Path
from typing import Any

import pytest

from aadhi.compose.dynamic import colour_math as cm
from aadhi.compose.dynamic import themes as th

VECTORS = Path(__file__).resolve().parents[2] / "fixtures" / "colour_vectors.json"
TOL = 1e-9


@functools.cache
def vectors() -> dict[str, Any]:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def close(a: float, b: float, what: object) -> None:
    assert abs(a - b) <= TOL, (what, a, b)


def close_hue(a: float, b: float, what: object) -> None:
    assert cm.hue_distance(a, b) <= TOL, (what, a, b)


def test_the_vectors_file_is_the_generators() -> None:
    v = vectors()
    assert v["version"] == 1 and v["generator"] == "scripts/gen_colour_vectors.mjs"
    for key in ("hex_oklch", "from_oklch", "mix", "mix_values", "contrast", "composite", "desaturate", "stage"):
        assert len(v[key]) > 10 or key == "stage", key


# --- color.js primitives ----------------------------------------------------------------------------------------------


def test_hex_to_oklch_and_back() -> None:
    for r in vectors()["hex_oklch"]:
        got = cm.to_oklch(r["hex"])
        close(got.l, r["l"], r)
        close(got.c, r["c"], r)
        close_hue(got.h, r["h"], r)
        assert got.a == pytest.approx(r["a"], abs=TOL)
        assert cm.from_oklch(r["l"], r["c"], r["h"], r["a"]) == r["back"], r
        assert cm.from_oklch(got.l, got.c, got.h, got.a) == r["back"], r
        assert cm.to_hex(r["hex"]) == r["to_hex"], r
        close(cm.relative_luminance(r["hex"]), r["luminance"], r)


def test_oklch_to_hex_with_the_css_gamut_mapping() -> None:
    rows = vectors()["from_oklch"]
    searched = 0
    for r in rows:
        assert cm.from_oklch(r["l"], r["c"], r["h"], r.get("a", 1.0)) == r["hex"], r
        rgb = cm.gamut_map_oklch(r["l"], max(0.0, r["c"]), cm.norm_hue(r["h"]))
        for got, want in zip(rgb, r["rgb"]):
            close(got, want, r)
        if 0 < r["l"] < 1 and not cm.in_gamut(cm.oklch_to_srgb(r["l"], max(0.0, r["c"]), r["h"])):
            searched += 1
    assert searched > 500, "the vectors exercise the out-of-gamut branch"


def test_the_accents_of_every_family_window_are_covered() -> None:
    v = vectors()
    windows = {fid: [list(w) for w in f.windows] for fid, f in th.FAMILIES.items()}
    assert v["families"] == windows, "regenerate the vectors: FAMILY_WINDOWS must be the catalogue's windows"
    assert v["accent_lc"] == {m: list(lc) for m, lc in th.ACCENT_LC.items()}
    keys = {(r["l"], r["c"], r["h"]) for r in v["from_oklch"]}
    for f in th.FAMILIES.values():
        for lo, hi in f.windows:
            for h in range(int(lo), int(hi) + 1, 5):
                for lc in th.ACCENT_LC.values():
                    assert (lc[0], lc[1], h % 360) in keys, (f.id, h)


def test_oklch_mixes() -> None:
    for r in vectors()["mix"]:
        assert cm.mix_oklch(r["a"], r["b"], r["p"]) == r["hex"], r
        got = cm.mix_oklch_values(cm.to_oklch(r["a"]), cm.to_oklch(r["b"]), r["p"])
        want = r["value"]
        close(got.l, want["l"], r)
        close(got.c, want["c"], r)
        close_hue(got.h, want["h"], r)
        close(got.a, want["a"], r)


def test_oklch_mixes_across_the_hue_boundary() -> None:
    """mixOklchValues on OKLCH inputs whose hue difference is just below, at and just above 180° (both signs, and
    across 0°): the shorter arc, with JavaScript's choice at exactly +-180° (no turn)."""
    rows = vectors()["mix_values"]
    assert {(r["a"]["h"], r["b"]["h"]) for r in rows} >= {(10, 189.5), (10, 190), (10, 190.5), (190, 10)}
    for r in rows:
        a, b = (cm.Oklch(v["l"], v["c"], v["h"], v["a"]) for v in (r["a"], r["b"]))
        got = cm.mix_oklch_values(a, b, r["p"])
        want = r["value"]
        close(got.l, want["l"], r)
        close(got.c, want["c"], r)
        close_hue(got.h, want["h"], r)
        close(got.a, want["a"], r)


def test_mix_spot_values() -> None:
    """The same spot values the JS test pins, plus the grey and hue-arc rules stated directly."""
    assert cm.mix_oklch("#000000", "#FFFFFF", 0.5) == "#636363"
    assert cm.mix_oklch_values(cm.to_oklch("#000000"), cm.to_oklch("#808080"), 0.5).h == 0.0  # two greys: hue 0
    mid = cm.mix_oklch_values(cm.to_oklch("#808080"), cm.Oklch(0.6, 0.12, 200.0), 0.5)
    assert mid.h == pytest.approx(200.0)  # a grey end borrows the other end's hue
    at = functools.partial(cm.mix_oklch_values, cm.Oklch(0.6, 0.12, 10.0), p=0.5)
    assert at(cm.Oklch(0.7, 0.08, 189.5)).h == pytest.approx(99.75)  # d = 179.5: forward
    assert at(cm.Oklch(0.7, 0.08, 190.0)).h == pytest.approx(100.0)  # d = 180: no turn (as JS)
    assert at(cm.Oklch(0.7, 0.08, 190.5)).h == pytest.approx(280.25)  # d = 180.5: the shorter arc backward


def test_contrast_composite_and_desaturate() -> None:
    v = vectors()
    for r in v["contrast"]:
        close(cm.contrast_ratio(r["fg"], r["bg"]), r["ratio"], r)
    for r in v["composite"]:
        assert cm.composite(r["top"], r["bottom"]) == r["hex"], r
    for r in v["desaturate"]:
        assert cm.desaturate(r["hex"], r["amount"]) == r["out"], r


# --- the stage's derived colours (themes.py mirrors) -----------------------------------------------------------------


def test_stage_colour_helpers() -> None:
    for r in vectors()["stage"]:
        mode, tokens, content = r["mode"], r["tokens"], r["content"]
        assert content == th.content_tokens(mode), "the generator's content inks are the catalogue's"
        ink = content["ink"]
        base, highlight = th.block_colours(mode, tokens)
        assert (base, highlight) == (r["block"]["base"], r["block"]["highlight"]), r["name"]
        assert r["block"]["colors"] == [base, highlight]
        assert th.ink_on(ink, [base, highlight]) == r["block"]["ink"], r["name"]
        assert th.block_ink2(r["block"]["ink"], [base, highlight]) == r["block"]["ink2"], r["name"]
        assert th.ink_on(ink, tokens["bg"]) == r["ink_on"]["ink"]
        assert th.ink_on(ink, [tokens["bg"], tokens["surface"]]) == r["ink_on"]["ink_surface"]
        assert th.derived_surface(mode, tokens["bg"], ink) == r["surface_fallback"], r["name"]
        assert th.scrim_colour(tokens["bg"]) == r["scrim"]
        for tone, m in r["mesh"].items():
            colours = th.mesh_drift_colours(tokens, tone, ink)
            assert colours == m["colors"], (r["name"], tone)
            assert [cm.desaturate(c, th.HOLD_DESATURATION) for c in colours] == m["desaturated"], (r["name"], tone)
            for role, want in m["scrimmed"].items():
                assert th.scrimmed_bg(r["scrim"], colours, content[role]) == want, (r["name"], tone, role)


def test_the_stage_vectors_cover_every_released_palette_slot() -> None:
    by_name = {r["name"]: r for r in vectors()["stage"]}
    for fid, family in th.RELEASED_FAMILIES.items():
        for slot in range(family.accent_period):
            r = by_name.get(f"{fid}/{slot}")
            assert r is not None, f"regenerate the vectors with {fid}/{slot}"
            assert r["tokens"] == th.structure_tokens(family, th.accent_hue(family, slot))
            assert r["mode"] == family.mode


# --- properties of the port -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("x", "expected"), [
    (0.5, 1), (1.5, 2), (2.5, 3), (-0.5, 0), (-1.5, -1), (-2.5, -2), (0.49999999999999994, 0), (127.5, 128),
    (128.5, 129), (254.49999, 254), (3.0, 3), (-0.0, 0),
])
def test_js_round_is_math_round(x: float, expected: int) -> None:
    assert cm.js_round(x) == expected
    assert isinstance(cm.js_round(x), int)


def test_js_round_rejects_non_finite() -> None:
    for x in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            cm.js_round(x)


def test_hue_normalisation_is_javascripts() -> None:
    assert cm.norm_hue(-30.0) == 330.0
    assert cm.norm_hue(725.0) == 5.0
    assert cm.norm_hue(360.0) == 0.0
    assert cm.norm_hue(-1e-18) == 0.0  # Python's floored % would give 360.0
    assert math.isnan(cm.norm_hue(math.inf))
    assert cm.hue_distance(350.0, 10.0) == 20.0
    assert cm.hue_distance(0.0, 180.0) == 180.0


def test_hex_parsing_and_formatting() -> None:
    assert cm.parse_hex("#FFF") == (1.0, 1.0, 1.0, 1.0)
    assert cm.parse_hex(" #ff000080 ") == (1.0, 0.0, 0.0, 128 / 255)
    for bad in ("#12", "#12345", "fff", "#fff\n#000", "rgb(0,0,0)", None, 12):
        assert cm.parse_hex(bad) is None  # type: ignore[arg-type]
    assert cm.to_hex("#ABC") == "#aabbcc"
    assert cm.to_hex((1.0, 0.5, 0.0)) == "#ff8000"  # 127.5 rounds up, as Math.round
    assert cm.to_hex((0.0, 0.0, 0.0, 0.5)) == "#00000080"
    assert cm.with_alpha("#10132C", "c7") == "#10132Cc7"
    with pytest.raises(ValueError):
        cm.with_alpha("#10132Cc7", "c7")
    with pytest.raises(ValueError):
        cm.to_oklch("oklch(0.5 0.1 30)")  # functional notations are preflight's parser's (colour.py)
    assert cm.is_hex6("#AbCdEf") and not cm.is_hex6("#abc") and not cm.is_hex6("#abcdef00")


def test_composite_and_contrast_edges() -> None:
    assert cm.composite("transparent", "transparent") == "#00000000"
    assert cm.composite("#ffffff80", "#000000") == "#808080"
    assert cm.composite("#ff000080", "#0000ff00") == "#ff000080"
    assert cm.contrast_ratio("#FFFFFF", "#000000") == pytest.approx(21.0)
    assert cm.contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    # a translucent foreground is blended over the background (whose alpha is ignored), as color.js does
    half = cm.contrast_ratio("#ffffff80", "#000000")
    assert half == pytest.approx(cm.contrast_ratio("#808080", "#000000"), abs=0.01)
    assert cm.contrast_ratio((1.0, 1.0, 1.0, 128 / 255), "#000000") == pytest.approx(half, abs=1e-12)
    assert cm.worst_contrast("#FFFFFF", ["#000000", "#767676"]) == pytest.approx(4.54, abs=0.01)
    with pytest.raises(ValueError):
        cm.worst_contrast("#FFFFFF", [])


def test_from_oklch_edges() -> None:
    assert cm.from_oklch(0.0, 0.2, 30.0) == "#000000"
    assert cm.from_oklch(1.0, 0.2, 30.0) == "#ffffff"
    assert cm.from_oklch(0.62796, 0.25768, 29.234) == "#ff0000"
    assert cm.from_oklch(0.5, 0.1, math.nan) == cm.from_oklch(0.5, 0.1, 0.0)  # color.js: a non-finite hue is 0
    assert cm.from_oklch(0.5, 0.1, 30.0, math.nan) == cm.from_oklch(0.5, 0.1, 30.0)  # ... and alpha 1
    with pytest.raises(ValueError):
        cm.from_oklch(0.5, math.nan, 30.0)  # color.js would print "#NaNNaNNaN"
    assert cm.desaturate("#FF0000", 0) == "#FF0000"  # unchanged, spelling kept, as core.js
    assert cm.desaturate("#FF0000", math.nan) == "#FF0000"
    grey = cm.to_oklch(cm.desaturate("#FF0000", 1.0))
    assert grey.c < 1e-3


# --- the palette generator (B1's oklch_hex) --------------------------------------------------------------------------


def test_palette_hex_keeps_lightness_and_hue_by_lowering_chroma() -> None:
    assert cm.palette_hex(1.0, 0.0, 0.0) == "#FFFFFF"
    assert cm.palette_hex(0.0, 0.0, 0.0) == "#000000"
    assert cm.palette_hex(0.62796, 0.25768, 29.234) == "#FF0000"
    out = cm.palette_hex(0.9, 0.4, 140.0)  # far out of gamut
    assert out == "#89FF6D"  # B1's value (color.js clips within its JND instead: #62ff31)
    v = cm.to_oklch(out)
    assert v.l == pytest.approx(0.9, abs=0.004) and cm.hue_distance(v.h, 140.0) < 1.0
    assert cm.from_oklch(0.9, 0.4, 140.0) == "#62ff31"


def test_cvd_simulation_and_red_ratio() -> None:
    # Machado 2009 severity 1: a grey is a grey for every dichromacy; red and green collapse under protanopia
    for kind in cm.CVD_KINDS:
        r, g, b = cm.simulate_cvd("#808080", kind)
        assert max(r, g, b) - min(r, g, b) < 0.02, kind
    normal = cm.oklab_distance("#FF0000", "#00A000")
    assert cm.oklab_distance(cm.simulate_cvd("#FF0000", "protan"), cm.simulate_cvd("#00A000", "protan")) < normal / 2
    assert cm.red_ratio("#FF0000") == pytest.approx(1.0)
    assert cm.red_ratio("#000000") == 0.0
    assert cm.red_ratio("#FFFFFF") == pytest.approx(0.2126 / 1.0, abs=0.2)  # equal channels: 1/3
    assert cm.red_ratio("#C44323") >= cm.SATURATED_RED_RATIO
