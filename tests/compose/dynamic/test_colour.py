"""CSS colour parsing and WCAG contrast for the Dynamic preflight (aadhi.compose.dynamic.colour)."""

from __future__ import annotations

import pytest

from aadhi.compose.dynamic.colour import (
    composite,
    contrast_ratio,
    parse_colour,
    relative_luminance,
    text_contrast,
    to_hex,
)


def _approx(colour, expected, tol=1 / 255):
    assert colour is not None
    assert all(abs(a - b) <= tol for a, b in zip(colour, expected)), (colour, expected)


@pytest.mark.parametrize(("text", "expected"), [
    ("#fff", (1, 1, 1, 1)),
    ("#0008", (0, 0, 0, 0x88 / 255)),
    ("#1B1F3B", (0x1B / 255, 0x1F / 255, 0x3B / 255, 1)),
    ("#ff000080", (1, 0, 0, 0x80 / 255)),
    ("rgb(255, 128, 0)", (1, 128 / 255, 0, 1)),
    ("rgba(0, 0, 0, 0.5)", (0, 0, 0, 0.5)),
    ("rgb(0 0 255 / 25%)", (0, 0, 1, 0.25)),
    ("rgb(100% 50% 0%)", (1, 0.5, 0, 1)),
    ("rgb(12.5, 34.25, 56)", (12.5 / 255, 34.25 / 255, 56 / 255, 1)),
    ("hsl(120deg 100% 50%)", (0, 1, 0, 1)),
    ("hsla(0, 100%, 50%, 0.5)", (1, 0, 0, 0.5)),
    ("color(srgb 1 0.5 0 / 0.5)", (1, 0.5, 0, 0.5)),
    ("transparent", (0, 0, 0, 0)),
    ("WHITE", (1, 1, 1, 1)),
])
def test_parse_colour(text, expected) -> None:
    _approx(parse_colour(text), expected)


@pytest.mark.parametrize("text", ["", "nope", "#12", "#12345", "rgb(1, 2)", "rgb(1, 2, 3, 4, 5)", "oklch(a b c)",
                                  "color(rec2020 1 0 0)", "rgb(1px, 2, 3)", None, 12, "x" * 300])
def test_unreadable_colours_are_none(text) -> None:
    assert parse_colour(text) is None


def test_oklch_and_oklab_match_srgb() -> None:
    _approx(parse_colour("oklch(1 0 0)"), (1, 1, 1, 1), tol=2 / 255)
    _approx(parse_colour("oklch(0 0 0)"), (0, 0, 0, 1), tol=1 / 255)
    _approx(parse_colour("oklab(100% 0 0)"), (1, 1, 1, 1), tol=2 / 255)
    # sRGB red is oklch(0.628 0.2577 29.23deg); blue oklch(0.452 0.313 264.05)
    _approx(parse_colour("oklch(0.62796 0.25768 29.2339)"), (1, 0, 0, 1), tol=2 / 255)
    _approx(parse_colour("oklch(45.2% 0.3132 264.05deg / 50%)"), (0, 0, 1, 0.5), tol=2 / 255)
    _approx(parse_colour("oklch(0.62796 64.42% 0.0812turn)"), (1, 0, 0, 1), tol=3 / 255)
    # out of gamut: clipped per channel, never outside 0..1
    clipped = parse_colour("oklch(0.7 0.4 150)")
    assert clipped is not None and all(0.0 <= c <= 1.0 for c in clipped)
    _approx(parse_colour("color(display-p3 1 1 1)"), (1, 1, 1, 1), tol=2 / 255)


def test_wcag_contrast_reference_values() -> None:
    white, black = (1.0, 1.0, 1.0), (0.0, 0.0, 0.0)
    assert contrast_ratio(white, black) == pytest.approx(21.0)
    assert contrast_ratio(black, white) == pytest.approx(21.0)
    assert contrast_ratio(white, white) == pytest.approx(1.0)
    grey = parse_colour("#767676")  # the classic "just passes 4.5:1 on white" grey
    assert contrast_ratio(grey, white) == pytest.approx(4.54, abs=0.01)
    assert relative_luminance((1.0, 1.0, 1.0)) == pytest.approx(1.0)
    # the design's content colours on their dark worst case (§4.7 table: entity:0 #0FE3FE >= 8:1 on dark bg)
    assert text_contrast("#0FE3FE", "#1B1F3B") > 8.0


def test_text_contrast_composites_ink_and_refuses_translucent_backgrounds() -> None:
    assert text_contrast("#ffffff", "#000000") == pytest.approx(21.0)
    half = text_contrast("rgba(255, 255, 255, 0.5)", "#000000")  # 50% white over black = #808080
    assert half == pytest.approx(contrast_ratio(parse_colour("#808080"), (0, 0, 0)), abs=0.05)
    assert text_contrast("#ffffff", "rgba(0, 0, 0, 0.5)") is None  # what shows through is not declared
    assert text_contrast("transparent", "#000000") is None
    assert text_contrast("bogus", "#000000") is None


def test_composite_and_hex() -> None:
    assert composite((1, 1, 1, 0.0), (0, 0, 0, 1)) == (0, 0, 0, 1)
    assert composite((1, 0, 0, 1), (0, 0, 1, 1)) == (1, 0, 0, 1)
    assert composite((0, 0, 0, 0), (0, 0, 0, 0)) == (0, 0, 0, 0)
    assert to_hex(parse_colour("rgb(255, 128, 0)")) == "#ff8000"
