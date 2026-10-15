"""Safe zones, grid, platform zones, type floors and layout tokens (aadhi.compose.dynamic.zones; DYNAMIC_VIDEO_DESIGN
§2.5, R-29, R-59, §4.6; B2 plan §1.1).

* The zones the B1 stage draws with (web/js/dynamic/core.js ``ZONES``) come out of zones.py value for value, and
  preflight's bands and floors are the same numbers (B2a: no visible change).
* Every §2.5 region, the 12-column grid, the platform zones and their intersection, R-29's floors and §4.6's scale.
* The layout tokens that name dynamic.css values are the stylesheet's.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aadhi.compose.dynamic import preflight as pf
from aadhi.compose.dynamic import themes as th
from aadhi.compose.dynamic import zones as zn
from aadhi.compose.dynamic.zones import Box, Grid, PlatformZone, Rail

ROOT = Path(__file__).resolve().parents[3]

# web/js/dynamic/core.js ZONES at B1 (3226e20), copied literally: zones.py must reproduce it (step B makes core.js
# read the generated catalogue; a JS test then pins core.js against it).
CORE_JS_ZONES = {
    "landscape": {
        "format": "landscape", "width": 1920, "height": 1080,
        "critical": [{"x0": 96, "y0": 54, "x1": 1824, "y1": 912}],
        "caption": {"x0": 96, "y0": 924, "x1": 1824, "y1": 1032},
        "header": {"x0": 96, "y0": 54, "x1": 1824, "y1": 102},
        "title": {"x0": 96, "y0": 112, "x1": 1824, "y1": 352},
        "body": {"x0": 96, "y0": 300, "x1": 1824, "y1": 900},
        "textColumn": {"x0": 96, "y0": 300, "x1": 1824, "y1": 900},
    },
    "portrait": {
        "format": "portrait", "width": 1080, "height": 1920,
        "critical": [{"x0": 192, "y0": 288, "x1": 888, "y1": 840}, {"x0": 120, "y0": 840, "x1": 780, "y1": 1248}],
        "caption": {"x0": 120, "y0": 1040, "x1": 780, "y1": 1240},
        "header": {"x0": 192, "y0": 296, "x1": 888, "y1": 336},
        "title": {"x0": 192, "y0": 340, "x1": 888, "y1": 460},
        "body": {"x0": 60, "y0": 470, "x1": 1020, "y1": 1020},
        "textColumn": {"x0": 192, "y0": 470, "x1": 780, "y1": 1020},
    },
}


def covered(box: Box, boxes: tuple[Box, ...]) -> bool:
    """True when ``box`` lies inside the union of ``boxes`` (sliced at every box edge, each slice inside one box)."""
    ys = sorted({box.y0, box.y1, *(y for b in boxes for y in (b.y0, b.y1) if box.y0 < y < box.y1)})
    for y0, y1 in zip(ys, ys[1:], strict=False):
        part = Box(box.x0, y0, box.x1, y1)
        if not any(b.contains(part) for b in boxes):
            return False
    return True


# --- the stage zones -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", zn.FORMATS)
def test_zones_are_core_js_zones_value_for_value(fmt: str) -> None:
    assert zn.ZONES[fmt].as_stage_dict() == CORE_JS_ZONES[fmt]
    assert list(zn.ZONES[fmt].as_stage_dict()) == list(CORE_JS_ZONES[fmt])  # the JS key order too


def test_r59_safe_zones() -> None:
    """R-59: 16:9 critical content in x 96-1824, y 54-912, caption band y 924-1032; 9:16 critical text x 192-888 for
    y 288-840 and x 120-780 for y 840-1248, caption band x 120-780, y 1040-1240."""
    land, port = zn.ZONES["landscape"], zn.ZONES["portrait"]
    assert land.critical == (Box(96, 54, 1824, 912),) and (land.caption.y0, land.caption.y1) == (924, 1032)
    assert port.critical == (Box(192, 288, 888, 840), Box(120, 840, 780, 1248))
    assert port.caption == Box(120, 1040, 780, 1240)
    assert land.critical[0].y1 < land.caption.y0, "16:9 captions never cover critical content"
    assert port.critical[1].contains(port.caption), "9:16 captions sit in the lower critical band (x 120-780)"
    for z in zn.ZONES.values():
        frame = Box(0, 0, z.width, z.height)
        assert all(frame.contains(b) for b in (*z.critical, z.caption, z.header, z.title, z.body, z.text_column))
    assert zn.zones_for("portrait") is port and zn.zones_for("landscape") is land and zn.zones_for("?") is land


def test_the_preflight_bands_and_floors_are_the_zones() -> None:
    """preflight's names stay importable with B1's values (B2a: no visible change), now derived from zones.py."""
    assert pf.CRITICAL_BANDS == {"landscape": ((54, 912, 96, 1824),),
                                 "portrait": ((288, 840, 192, 888), (840, 1248, 120, 780))}
    assert pf.CAPTION_BANDS == {"landscape": ((924, 1032, 96, 1824),), "portrait": ((1040, 1240, 120, 780),)}
    assert pf.MIN_TEXT_PX == {"landscape": 36, "portrait": 40}
    assert pf.MIN_LABEL_PX == {"landscape": 40.0, "portrait": 44.0}
    assert all(type(v) is int for v in pf.MIN_TEXT_PX.values())
    assert all(type(v) is float for v in pf.MIN_LABEL_PX.values())
    assert (pf.PORTRAIT_HEAVY_BELOW_PX, pf.PORTRAIT_MIN_WEIGHT) == (64.0, 600.0)
    for fmt in zn.FORMATS:
        assert pf.CRITICAL_BANDS[fmt] == tuple(b.band() for b in zn.ZONES[fmt].critical)
        assert pf.MIN_TEXT_PX[fmt] == zn.min_text_px(fmt) and pf.MIN_LABEL_PX[fmt] == zn.min_label_px(fmt)


def test_box() -> None:
    b = Box(10, 20, 110, 70)
    assert (b.w, b.h, b.band(), b.as_dict()) == (100, 50, (20, 70, 10, 110), {"x0": 10, "y0": 20, "x1": 110, "y1": 70})
    assert b.contains(Box(10, 20, 110, 70)) and b.contains(Box(20, 30, 40, 50)) and not b.contains(Box(0, 20, 50, 60))
    assert b.intersect(Box(100, 60, 200, 200)) == Box(100, 60, 110, 70) and b.intersect(Box(110, 20, 120, 70)) is None
    for empty in ((0, 0, 0, 10), (0, 0, 10, 0), (5, 0, 1, 10)):
        with pytest.raises(ValueError):
            Box(*empty)


# --- regions and grid (§2.5) ---------------------------------------------------------------------------------------


def test_landscape_grid_and_named_regions() -> None:
    grid = zn.GRID
    assert (grid.columns, grid.gutter, grid.column_width, grid.box) == (12, 24, 122, Box(96, 54, 1824, 912))
    assert grid.column(0) == Box(96, 54, 218, 912) and grid.column(11) == Box(1702, 54, 1824, 912)
    assert grid.span(0, 11) == grid.box and grid.span(2, 3) == Box(388, 54, 656, 912)
    assert all(grid.column(i + 1).x0 - grid.column(i).x1 == 24 for i in range(11))
    for bad in (-1, 12):
        with pytest.raises(IndexError):
            grid.column(bad)
    with pytest.raises(ValueError):
        Grid(Box(0, 0, 1000, 10), 12, 24)  # (1000 - 264) / 12 is not whole
    r = zn.REGIONS["landscape"]
    assert set(r) == {"full", "left-55", "right-45", "centre-60", "top-band", "stage"}
    assert r["full"] == zn.ZONES["landscape"].critical[0]
    assert all(r["full"].contains(b) for b in r.values())
    # left-55 + right-45: split at 55% of the content width (x 1046, B1 board_generic's split line), one gutter between
    assert r["left-55"] == Box(96, 54, 1034, 912) and r["right-45"] == Box(1058, 54, 1824, 912)
    assert r["right-45"].x0 - r["left-55"].x1 == grid.gutter
    assert (r["left-55"].x1 + r["right-45"].x0) / 2 == round(96 + 0.55 * 1728)
    # centre-60: the middle 60%, symmetric
    c = r["centre-60"]
    assert c == Box(442, 54, 1478, 912) and c.x0 - 96 == 1824 - c.x1 and abs(c.w - 0.6 * 1728) <= 1
    # top-band (B1's header + title rows) + stage, one gutter apart, filling the content box
    assert r["top-band"] == Box(96, 54, 1824, 352) and r["stage"] == Box(96, 376, 1824, 912)
    assert r["top-band"].y1 == zn.ZONES["landscape"].title.y1 and r["stage"].y0 - r["top-band"].y1 == grid.gutter
    assert zn.END_SCREEN.box == Box(960, 0, 1920, 1080) and zn.END_SCREEN.seconds == 15.0  # the last 15 s, right half


def test_portrait_regions() -> None:
    """§2.5 9:16: hook band y 340-760 (text x 192-888), 1-line headline y 340-440 + stage y 460-1040 (580 px), the tall
    stage y 340-1040 (700 px), shapes x 60-1020, text x 192-888 above y 840 and x 120-780 below, clips y 360-968."""
    r = zn.REGIONS["portrait"]
    assert r["hook"] == Box(192, 340, 888, 760)
    assert r["headline"] == Box(192, 340, 888, 440)
    assert r["stage"] == Box(60, 460, 1020, 1040) and r["stage"].h == 580
    assert r["tall"] == Box(60, 340, 1020, 1040) and r["tall"].h == 700
    assert (r["stage"].x0, r["stage"].x1) == (r["tall"].x0, r["tall"].x1) == zn.SHAPES_X == (60, 1020)
    assert r["text-above"] == Box(192, 288, 888, 840) and r["text-below"] == Box(120, 840, 780, 1248)
    assert (r["text-above"], r["text-below"]) == zn.ZONES["portrait"].critical
    assert r["clip"] == Box(0, 360, 1080, 968) and (r["clip"].w, r["clip"].h) == (1080, 608)  # 16:9 at full width
    assert r["stage"].y1 == zn.ZONES["portrait"].caption.y0, "the stage ends where the caption band starts"
    for name in ("hook", "headline"):  # text regions keep to the critical text column
        assert covered(r[name], zn.ZONES["portrait"].critical), name


# --- platform zones (§2.5) ------------------------------------------------------------------------------------------


def test_platform_zones_and_their_intersection() -> None:
    assert zn.YT_SHORTS == (288, 672, 48, 192, None)
    assert zn.META_REELS == (269, 672, 65, 65, None)
    assert zn.TIKTOK == (240, 660, 120, 120, (300, 840))
    assert zn.UNIVERSAL == PlatformZone(288, 672, 120, 192, Rail(300, 840))
    # "Below y 840 the intersection is x 120-780, which is why the caption band uses it"
    assert zn.UNIVERSAL.safe_boxes() == (Box(120, 288, 888, 840), Box(120, 840, 780, 1248))
    assert set(zn.SAFE_ZONES) == {"yt_shorts", "meta_reels", "tiktok", "universal"}
    for name, zone in zn.PLATFORM_ZONES.items():
        for box in zn.UNIVERSAL.safe_boxes():
            assert covered(box, zone.safe_boxes()), (name, box)
    # R-59's critical bands and the caption band are inside the universal zone
    port = zn.ZONES["portrait"]
    for box in (*port.critical, port.caption):
        assert covered(box, zn.UNIVERSAL.safe_boxes()), box
    # R-59's upper column is the universal one made symmetric about the centre (x 192-888)
    assert port.critical[0].x0 == 1080 - port.critical[0].x1 == zn.UNIVERSAL.right


def test_platform_zone_edge_cases() -> None:
    assert zn.YT_SHORTS.safe_boxes() == (Box(48, 288, 888, 1248),)
    assert PlatformZone(100, 100, 10, 10, Rail(200, 1900)).safe_boxes() == (Box(10, 100, 1070, 1820),)  # below it all
    assert PlatformZone(100, 100, 10, 10, Rail(200, 0)).safe_boxes() == (Box(10, 100, 880, 1820),)  # the whole height
    assert zn.intersect_zones([zn.YT_SHORTS]) == zn.YT_SHORTS
    assert zn.intersect_zones([zn.TIKTOK, PlatformZone(0, 0, 0, 0, Rail(320, 900))]).rail == Rail(320, 840)
    with pytest.raises(ValueError):
        zn.intersect_zones([])


# --- type (R-29, §4.6) ------------------------------------------------------------------------------------------------


def test_r29_type_floors() -> None:
    land, port = zn.TYPE_FLOORS["landscape"], zn.TYPE_FLOORS["portrait"]
    # 16:9: body 52 (48-56); labels >= 40; never < 36; headings 84-120; display 140-200
    assert (land.body_default, land.body, land.label_min, land.text_min) == (52, (48, 56), 40, 36)
    assert (land.heading, land.display, land.hook) == ((84, 120), (140, 200), None)
    assert (land.heavy_max_px, land.heavy_min_weight) == (None, None)
    # 9:16: body 56-64; labels >= 44; never < 40; hook headline 88-104 auto-fit; weight >= 600 at <= 64 px
    assert (port.body, port.label_min, port.text_min, port.hook) == ((56, 64), 44, 40, (88, 104))
    assert (port.heavy_max_px, port.heavy_min_weight) == (64, 600)
    assert (port.heading, port.display) == (None, None)
    # captions (§2.6, plan §2): 48-54 (16:9) and 56-64 with a 52 px floor for one long word (9:16)
    assert (land.caption, land.caption_min, port.caption, port.caption_min) == ((48, 54), 48, (56, 64), 52)
    for fmt, floors in zn.TYPE_FLOORS.items():
        assert floors.format == fmt and floors.text_min < floors.label_min <= floors.body[0] <= floors.body_default
        assert floors.caption[0] >= floors.caption_min >= floors.text_min
        for style in th.CAPTION_STYLES.values():  # the released caption styles draw inside the range
            if style.metrics is not None:
                assert floors.caption[0] <= style.metrics.size[fmt] <= floors.caption[1]
    assert (zn.min_text_px("portrait"), zn.min_text_px("x"), zn.min_label_px("portrait"), zn.min_label_px("x")) == (
        40, 36, 44, 40)


def test_type_scale() -> None:
    """§4.6: display 1.0-1.05 with -1 to -2% tracking, headings 1.15, body 1.35 (<= 45 chars a line in 16:9); plan §2:
    Indic display >= 1.25, body >= 1.45 (captions >= 1.35 live with the caption styles)."""
    s = zn.TYPE_SCALE
    assert s["display"].line_height == {"latin": (1.0, 1.05), "indic": (1.25, None)}
    assert s["display"].tracking_em == (-0.02, -0.01)
    assert s["heading"].line_height["latin"] == (1.15, 1.15) and s["heading"].line_height["indic"][0] >= 1.25
    assert s["body"].line_height == {"latin": (1.35, 1.35), "indic": (1.45, None)}
    assert s["body"].max_line_chars == {"landscape": 45}
    assert (zn.CAPS_LABEL_MAX_WORDS, zn.CAPS_TRACKING_EM) == (4, (0.04, 0.06))
    for style in th.CAPTION_STYLES.values():
        if style.metrics is not None:
            assert all(v["indic"] >= 1.35 for v in style.metrics.line_height.values())


# --- layout tokens ------------------------------------------------------------------------------------------------


def _css() -> str:
    return re.sub(r"/\*.*?\*/", "", (ROOT / "web/css/dynamic.css").read_text(encoding="utf-8"), flags=re.S)


def _block(css: str, selector: str) -> str:
    m = re.search(r"(?:^|\})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


def _prop(block: str, name: str) -> str:
    m = re.search(rf"(?:^|;|\s){re.escape(name)}\s*:\s*([^;]+);", block)
    assert m, name
    return m.group(1).strip()


def test_layout_tokens() -> None:
    lt = zn.LAYOUT
    assert lt.spacing_unit == 8 and all(v % 8 == 0 for v in lt.spacing) and list(lt.spacing) == sorted(lt.spacing)
    assert zn.GRID.gutter in lt.spacing
    assert dict(lt.radius) == {"card": 24, "media": 24, "chip": 999, "presenter_card": 40}
    assert dict(lt.border) == {"structure_min": 4, "structure_max": 6, "card": 6}  # §4.7: 4-6 px borders and pips
    assert dict(lt.outline) == {"focus_px": 4, "focus_outset_px": 8, "underline_px": 4, "state_border_px": 2}
    assert set(lt.shadow) == {"1", "2", "3"} and lt.icon["stroke_width"] == 2


def test_layout_tokens_name_the_stylesheet_values() -> None:
    """The tokens B1 already draws are dynamic.css's: chip / card / media-frame shadows, the card radius and border,
    the chip pill, the inline icon."""
    css, lt = _css(), zn.LAYOUT
    chip, card, frame = _block(css, ".dyn-chip"), _block(css, ".dyn-card"), _block(css, ".dyn-frame")
    assert _prop(chip, "box-shadow") == lt.shadow["1"] and _prop(chip, "border-radius") == f"{lt.radius['chip']}px"
    assert _prop(card, "box-shadow") == lt.shadow["2"] and _prop(card, "border-radius") == f"{lt.radius['card']}px"
    assert _prop(card, "border-left").startswith(f"{lt.border['card']}px solid")
    assert _prop(frame, "box-shadow") == lt.shadow["3"]
    icon = _block(css, ".dyn-icon")
    assert _prop(icon, "width") == _prop(icon, "height") == f"{lt.icon['inline_em']}em"
