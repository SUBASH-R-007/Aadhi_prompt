"""The one colour module of the Dynamic video mode (design §4.7, §7.12, R-53, R-54): transfer functions, OKLab and
OKLCH, gamut mapping, hex, WCAG luminance and contrast, alpha compositing, OKLCH mixing, colour-vision simulation.

Two callers, two duties:

* **Parity with the stage.** The page derives colours at run time with ``web/js/motion/color.js`` (block colours,
  on-block ink, scrims, hold desaturation, mixes). Everything here that has a ``color.js`` counterpart is a line-by-line
  port of it: the same Ottosson matrices and constants, the same order of floating-point operations, ``math.cbrt``
  for ``Math.cbrt``, the CSS Color 4 gamut mapping (:func:`gamut_map_oklch`), ``Math.round`` (ties toward +inf, never
  Python's ties-to-even ``round``), JavaScript's truncated ``%`` (``math.fmod``) and lower-case hex. So a colour the
  resolver stores (``ResolvedLook.tokens``, plan §1.2) is the colour the stage would have derived, and a contrast the
  validator measures is the one preflight measures. ``tests/fixtures/colour_vectors.json`` is written by
  ``scripts/gen_colour_vectors.mjs`` from ``color.js``; ``tests/compose/dynamic/test_colour_math.py`` checks that
  this module reproduces every vector (hex equal, floats within 1e-9) and ``web/tests/motion/colourVectors.test.js``
  that ``color.js`` still writes the committed file, so a drift on either side fails CI.
* **The palette generator.** The palette's own colours (``bg``, ``struct.accent``, ``ink``, ...) are defined by their
  OKLCH lightness and hue, which carry the contrast bands of §4.7. :func:`palette_hex` keeps both exactly and lowers
  only the chroma until the colour is inside sRGB (B1's ``presentation.oklch_hex``, unchanged, so every token a B1
  timeline resolved is still produced bit for bit). It never clips: CSS Color 4's mapping may clip a colour within a
  just-noticeable difference, which shifts its hue and lightness a little; that is right for the stage's mixes and
  wrong for a palette's anchor colours.

Colours are gamma-encoded sRGB floats 0..1 (``RGB``/``RGBA``) or CSS hex strings. Pure functions, no I/O, no state.
``aadhi.compose.dynamic.colour`` (preflight's CSS parser) and ``aadhi.compose.presentation`` build on this module.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

RGB = tuple[float, float, float]
RGBA = tuple[float, float, float, float]
Lab = tuple[float, float, float]
CvdKind = Literal["protan", "deutan", "tritan"]

# color.js constants (the names are the JS ones).
ACHROMATIC_CHROMA: Final = 1e-4  # below this chroma a colour is grey and its hue is "powerless" (CSS Color 4)
GAMUT_EPS: Final = 1e-6  # inGamut() tolerance on gamma-encoded channels
MAP_JND: Final = 0.02  # gamutMapOklch: a clip within this OKLab distance is accepted (CSS Color 4 "JND")
MAP_EPSILON: Final = 0.0001  # gamutMapOklch: chroma resolution of the binary search
# palette_hex (B1 presentation.oklch_hex): bisection steps and in-gamut tolerance on *linear* channels.
PALETTE_BISECT_STEPS: Final = 24
PALETTE_GAMUT_EPS: Final = 1e-6

_HEX = re.compile(r"^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$")
_KEYWORDS: Final[dict[str, RGBA]] = {  # the only keywords color.js parseColor reads (themes are hex)
    "transparent": (0.0, 0.0, 0.0, 0.0),
    "black": (0.0, 0.0, 0.0, 1.0),
    "white": (255.0, 255.0, 255.0, 1.0),
}

# Machado, Oliveira & Fernandes 2009, "A physiologically-based model for simulation of color vision deficiency"
# (IEEE TVCG 15(6)), the severity-1.0 matrices (dichromacy), applied to linear sRGB (design §4.7 "Colour vision").
MACHADO_2009: Final[dict[str, tuple[RGB, RGB, RGB]]] = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
}
# Linear Display P3 -> linear sRGB (D65 both), for CSS color(display-p3 ...) in preflight's parser.
P3_TO_SRGB: Final = ((1.2249401, -0.2249404, 0.0), (-0.0420569, 1.0420571, 0.0), (-0.0196376, -0.0786361, 1.0982735))


@dataclass(frozen=True)
class Oklch:
    """An OKLCH colour (color.js ``Oklch``): l 0..1, c >= 0, h degrees [0, 360), alpha 0..1."""

    l: float  # noqa: E741 - the CSS Color 4 component name
    c: float
    h: float
    a: float = 1.0


# --- scalars ------------------------------------------------------------------------------------------------------


def clamp(v: float, lo: float, hi: float) -> float:
    """color.js ``clamp``: NaN passes through, as in JavaScript."""
    return lo if v < lo else hi if v > hi else v


def js_round(x: float) -> int:
    """``Math.round``: the nearest integer, ties toward +inf (Python's ``round`` ties to even: 0.5 -> 0, 2.5 -> 2)."""
    if not math.isfinite(x):
        raise ValueError(f"not a finite number: {x!r}")
    f = math.floor(x)
    return int(f) + 1 if x - f >= 0.5 else int(f)


def norm_hue(h: float) -> float:
    """color.js ``normHue``: ``((h % 360) + 360) % 360`` with JavaScript's truncated ``%`` (``math.fmod``), so a hue a
    hair below 0 lands on 0 exactly as in the page, where Python's floored ``%`` could give 360.0."""
    if not math.isfinite(h):
        return math.nan
    return math.fmod(math.fmod(h, 360.0) + 360.0, 360.0)


def hue_distance(a: float, b: float) -> float:
    """Distance of two hues on the circle, 0..180 degrees."""
    d = abs(norm_hue(a) - norm_hue(b))
    return 360.0 - d if d > 180.0 else d


# --- transfer functions and OKLab ---------------------------------------------------------------------------------


def srgb_to_linear(v: float) -> float:
    """sRGB decode (color.js ``toLinear``), sign-preserving for extended values as CSS Color 4 does."""
    s = abs(v)
    out = s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4
    return -out if v < 0 else out


def linear_to_srgb(v: float) -> float:
    """sRGB encode (color.js ``toGamma``), sign-preserving."""
    s = abs(v)
    out = s * 12.92 if s <= 0.0031308 else 1.055 * s ** (1 / 2.4) - 0.055
    return -out if v < 0 else out


def linear_srgb_to_oklab(r: float, g: float, b: float) -> Lab:
    """Linear sRGB -> OKLab (Björn Ottosson's matrices; color.js ``linearSrgbToOklab``)."""
    l_ = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m_ = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s_ = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_,
    )


def oklab_to_linear_srgb(lightness: float, a: float, b: float) -> RGB:
    """OKLab -> linear sRGB, unclamped (color.js ``oklabToLinearSrgb``)."""
    l_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3
    return (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_,
    )


def oklch_to_oklab(lightness: float, chroma: float, hue: float) -> Lab:
    """OKLCH -> OKLab (hue in degrees; color.js converts with ``(h * Math.PI) / 180``)."""
    rad = (hue * math.pi) / 180
    return lightness, chroma * math.cos(rad), chroma * math.sin(rad)


def oklch_to_srgb(lightness: float, chroma: float, hue: float) -> RGB:
    """OKLCH -> gamma-encoded sRGB 0..1, unclamped (color.js ``oklchToSrgb01``)."""
    r, g, b = oklab_to_linear_srgb(*oklch_to_oklab(lightness, chroma, hue))
    return linear_to_srgb(r), linear_to_srgb(g), linear_to_srgb(b)


def srgb_to_oklab(rgb: Sequence[float]) -> Lab:
    """Gamma-encoded sRGB 0..1 -> OKLab."""
    return linear_srgb_to_oklab(srgb_to_linear(rgb[0]), srgb_to_linear(rgb[1]), srgb_to_linear(rgb[2]))


def in_gamut(rgb: Sequence[float]) -> bool:
    """color.js ``inGamut``: every gamma-encoded channel within 0..1 (+-1e-6); NaN is out."""
    return all(-GAMUT_EPS <= v <= 1 + GAMUT_EPS for v in rgb)


def delta_e_ok(rgb_a: Sequence[float], rgb_b: Sequence[float]) -> float:
    """Euclidean OKLab distance of two gamma-encoded sRGB triples (color.js ``deltaEOK``)."""
    a, b = srgb_to_oklab(rgb_a), srgb_to_oklab(rgb_b)
    return math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _clip3(v: Sequence[float]) -> RGB:
    return clamp(v[0], 0.0, 1.0), clamp(v[1], 0.0, 1.0), clamp(v[2], 0.0, 1.0)


def gamut_map_oklch(lightness: float, chroma: float, hue: float) -> RGB:
    """An OKLCH colour mapped into sRGB with the CSS Color 4 algorithm, ported line by line from color.js
    ``gamutMapOklch``: a binary search on chroma (lightness and hue kept) that accepts a clipped colour once it lies
    within the 0.02 OKLab "JND" of the unclipped one. Gamma-encoded sRGB 0..1, clipped."""
    if not (lightness > 0):
        return 0.0, 0.0, 0.0
    if lightness >= 1:
        return 1.0, 1.0, 1.0
    origin = oklch_to_srgb(lightness, chroma, hue)
    if in_gamut(origin):
        return _clip3(origin)
    clipped = _clip3(origin)
    if delta_e_ok(clipped, origin) < MAP_JND:
        return clipped
    lo, hi = 0.0, chroma
    lo_in_gamut = True
    while hi - lo > MAP_EPSILON:
        mid = (lo + hi) / 2
        current = oklch_to_srgb(lightness, mid, hue)
        if lo_in_gamut and in_gamut(current):
            lo = mid
            continue
        clipped = _clip3(current)
        e = delta_e_ok(clipped, current)
        if e < MAP_JND:
            if MAP_JND - e < MAP_EPSILON:
                return clipped
            lo_in_gamut = False
            lo = mid
        else:
            hi = mid
    return clipped


def palette_hex(lightness: float, chroma: float, hue: float) -> str:
    """``#RRGGBB`` of a palette colour: the largest chroma <= ``chroma`` whose linear sRGB lies in the gamut, at exactly
    this lightness and hue (24-step bisection), never clipped. This is B1's ``presentation.oklch_hex`` moved here
    unchanged in result (every B1 token is pinned by ``tests/compose/dynamic/test_themes.py``); upper case, as every
    stored token since B1."""

    def linear(c: float) -> RGB:
        return oklab_to_linear_srgb(*oklch_to_oklab(lightness, c, hue))

    def inside(rgb: RGB) -> bool:
        return all(-PALETTE_GAMUT_EPS <= v <= 1 + PALETTE_GAMUT_EPS for v in rgb)

    rgb = linear(chroma)
    if not inside(rgb):
        lo, hi = 0.0, chroma
        for _ in range(PALETTE_BISECT_STEPS):
            mid = (lo + hi) / 2
            if inside(linear(mid)):
                lo = mid
            else:
                hi = mid
        rgb = linear(lo)
    return "#" + "".join(f"{js_round(linear_to_srgb(clamp(v, 0.0, 1.0)) * 255):02X}" for v in rgb)


# --- hex and CSS colour strings ------------------------------------------------------------------------------------


def _hex2(v01: float) -> str:
    """color.js ``hex2``: ``Math.round(clamp(v, 0, 1) * 255)`` as two lower-case hex digits."""
    return f"{js_round(clamp(v01, 0.0, 1.0) * 255):02x}"


def _js_rgba(colour: str) -> RGBA:
    """color.js ``parseColor`` for the colours the stage derives from (hex and its three keywords), in its own scale:
    channels 0..255 (integers for hex), alpha 0..1. Raises ``ValueError`` where color.js ``mustParse`` throws."""
    text = colour.strip().lower() if isinstance(colour, str) else ""
    if text in _KEYWORDS:
        return _KEYWORDS[text]
    m = _HEX.match(text)
    if not m:
        raise ValueError(f"not a colour: {colour!r}")
    digits = m.group(1)
    if len(digits) <= 4:
        digits = "".join(ch + ch for ch in digits)
    n = [int(digits[i:i + 2], 16) for i in range(0, len(digits), 2)]
    return float(n[0]), float(n[1]), float(n[2]), n[3] / 255 if len(n) == 4 else 1.0


def parse_hex(colour: str) -> RGBA | None:
    """``(r, g, b, a)`` 0..1 of a ``#rgb``/``#rgba``/``#rrggbb``/``#rrggbbaa`` string (any case), else None."""
    if not isinstance(colour, str) or not _HEX.match(colour.strip().lower()):
        return None
    r, g, b, a = _js_rgba(colour)
    return r / 255, g / 255, b / 255, a


def to_hex(colour: Sequence[float]) -> str:
    """``#rrggbb``, or ``#rrggbbaa`` when a fourth (alpha) channel is below 1, of a 0..1 colour (color.js ``toHex``
    rounding: ``Math.round``, lower case)."""
    out = f"#{_hex2(colour[0])}{_hex2(colour[1])}{_hex2(colour[2])}"
    a = colour[3] if len(colour) > 3 else 1.0
    return out if a >= 1 else f"{out}{_hex2(a)}"


def with_alpha(colour: str, alpha: float) -> str:
    """``#rrggbbaa``: an opaque hex colour at ``alpha`` (the stage writes scrims as ``${bg}c7``)."""
    r, g, b, _ = _js_rgba(colour)
    return f"#{_hex2(r / 255)}{_hex2(g / 255)}{_hex2(b / 255)}{_hex2(alpha)}"


def _rgba01(colour: str | Sequence[float]) -> RGBA:
    if isinstance(colour, str):
        r, g, b, a = _js_rgba(colour)
        return r / 255, g / 255, b / 255, a  # exactly color.js's `r / 255` of an integer channel
    return colour[0], colour[1], colour[2], colour[3] if len(colour) > 3 else 1.0


def hsl_to_srgb(hue: float, saturation: float, lightness: float) -> RGB:
    """HSL (hue degrees, s and l 0..1) -> sRGB 0..1 (CSS Color 4; color.js ``hslToSrgb01``)."""

    def f(n: float) -> float:
        k = math.fmod(n + hue / 30, 12)
        a = saturation * min(lightness, 1 - lightness)
        return lightness - a * max(-1.0, min(k - 3, 9 - k, 1.0))

    return f(0), f(8), f(4)


def display_p3_to_linear_srgb(r_lin: float, g_lin: float, b_lin: float) -> RGB:
    """Linear Display P3 -> linear sRGB (D65)."""
    m = P3_TO_SRGB
    return tuple(row[0] * r_lin + row[1] * g_lin + row[2] * b_lin for row in m)  # type: ignore[return-value]


# --- OKLCH of CSS colours, mixing, desaturation --------------------------------------------------------------------


def to_oklch(colour: str | Sequence[float]) -> Oklch:
    """OKLCH of a colour (color.js ``toOklch``): greys (chroma < 1e-4) get hue 0."""
    r, g, b, a = _rgba01(colour)
    lightness, a_, b_ = linear_srgb_to_oklab(srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b))
    c = math.hypot(a_, b_)
    h = 0.0 if c < ACHROMATIC_CHROMA else norm_hue((math.atan2(b_, a_) * 180) / math.pi)
    return Oklch(lightness, c, h, a)


def from_oklch(lightness: float, chroma: float, hue: float, alpha: float = 1.0) -> str:
    """CSS hex of an OKLCH colour (color.js ``fromOklch``): CSS Color 4 gamut mapping, ``Math.round`` to 8 bits,
    ``#rrggbb`` (lower case) or ``#rrggbbaa`` when translucent."""
    a = clamp(alpha, 0.0, 1.0) if math.isfinite(alpha) else 1.0
    rgb = gamut_map_oklch(lightness, max(0.0, chroma), norm_hue(hue if math.isfinite(hue) else 0.0))
    out = f"#{_hex2(rgb[0])}{_hex2(rgb[1])}{_hex2(rgb[2])}"
    return out if a >= 1 else f"{out}{_hex2(a)}"


def mix_oklch_values(a: Oklch, b: Oklch, p: float) -> Oklch:
    """Interpolate two OKLCH colours (color.js ``mixOklchValues``, CSS Color 4): the shorter hue arc, a grey end
    borrows the other end's hue, L and C premultiplied by alpha; ``p`` clamped to 0..1 (NaN -> 0)."""
    u = 0.0 if math.isnan(p) else clamp(p, 0.0, 1.0)
    grey_a = a.c < ACHROMATIC_CHROMA
    grey_b = b.c < ACHROMATIC_CHROMA
    h_a = b.h if grey_a and not grey_b else a.h
    h_b = a.h if grey_b and not grey_a else b.h
    d = h_b - h_a
    if d > 180:
        d -= 360
    elif d < -180:
        d += 360
    h = 0.0 if grey_a and grey_b else norm_hue(h_a + d * u)
    alpha = a.a + (b.a - a.a) * u
    if alpha <= 0:
        return Oklch(a.l + (b.l - a.l) * u, a.c + (b.c - a.c) * u, h, 0.0)
    lightness = (a.l * a.a + (b.l * b.a - a.l * a.a) * u) / alpha
    chroma = (a.c * a.a + (b.c * b.a - a.c * a.a) * u) / alpha
    return Oklch(lightness, chroma, h, alpha)


def mix_oklch(a: str | Sequence[float], b: str | Sequence[float], p: float) -> str:
    """``a`` mixed toward ``b`` in OKLCH by ``p`` (color.js ``mixOklch``): CSS hex, lower case."""
    m = mix_oklch_values(to_oklch(a), to_oklch(b), p)
    return from_oklch(m.l, m.c, m.h, m.a)


def desaturate(colour: str, amount: float) -> str:
    """``colour`` with its OKLCH chroma scaled by ``1 - amount`` (lightness and hue kept): the R-55 hold freeze
    (web/js/dynamic/core.js ``desaturate``). ``amount <= 0`` returns the string unchanged, as the page does."""
    if not (amount > 0):
        return colour
    c = to_oklch(colour)
    return from_oklch(c.l, c.c * (1 - min(1.0, amount)), c.h)


# --- compositing, luminance, contrast --------------------------------------------------------------------------------


def source_over(top: Sequence[float], bottom: Sequence[float]) -> RGBA:
    """``top`` painted over ``bottom`` (source-over in gamma-encoded sRGB, the way browsers blend). Channels may be on
    any scale (0..1 here, 0..255 in color.js); alpha is 0..1. Fully transparent result: ``(0, 0, 0, 0)``."""
    ta, ba = top[3], bottom[3]
    out_a = ta + ba * (1 - ta)
    if out_a <= 0:
        return 0.0, 0.0, 0.0, 0.0
    return (
        (top[0] * ta + bottom[0] * ba * (1 - ta)) / out_a,
        (top[1] * ta + bottom[1] * ba * (1 - ta)) / out_a,
        (top[2] * ta + bottom[2] * ba * (1 - ta)) / out_a,
        out_a,
    )


def composite(top: str, bottom: str) -> str:
    """``top`` over ``bottom`` as CSS hex (color.js ``composite``): ``#rrggbb`` when opaque, else ``#rrggbbaa``; a fully
    transparent result is ``#00000000``. Computed on color.js's 0..255 channels, so the rounding is the page's."""
    out = source_over(_js_rgba(top), _js_rgba(bottom))
    if out[3] <= 0:
        return "#00000000"
    return to_hex((out[0] / 255, out[1] / 255, out[2] / 255, clamp(out[3], 0.0, 1.0)))


def relative_luminance(colour: str | Sequence[float]) -> float:
    """WCAG 2 relative luminance (alpha ignored; color.js ``relativeLuminance``)."""
    r, g, b, _ = _rgba01(colour)
    return 0.2126 * srgb_to_linear(r) + 0.7152 * srgb_to_linear(g) + 0.0722 * srgb_to_linear(b)


def contrast_ratio(fg: str | Sequence[float], bg: str | Sequence[float]) -> float:
    """WCAG 2 contrast ratio 1..21 (color.js ``contrastRatio``): a translucent ``fg`` (text) is blended over ``bg``,
    whose own alpha is ignored."""
    f, b = _rgba01(fg), _rgba01(bg)
    k = f[3]
    solid = f if k >= 1 else (f[0] * k + b[0] * (1 - k), f[1] * k + b[1] * (1 - k), f[2] * k + b[2] * (1 - k), 1.0)
    l1, l2 = relative_luminance(solid), relative_luminance(b)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def worst_contrast(fg: str | Sequence[float], bgs: Sequence[str | Sequence[float]]) -> float:
    """The lowest contrast of ``fg`` over any of ``bgs`` (R-53's worst case)."""
    return min(contrast_ratio(fg, b) for b in bgs)


# --- colour vision and flash safety ----------------------------------------------------------------------------------


def simulate_cvd(colour: str | Sequence[float], kind: CvdKind) -> RGB:
    """``colour`` as seen with a dichromacy (Machado 2009, severity 1.0): the matrix on linear sRGB, clipped to the
    gamut, gamma-encoded again (0..1)."""
    r, g, b, _ = _rgba01(colour)
    lin = (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b))
    out = tuple(clamp(row[0] * lin[0] + row[1] * lin[1] + row[2] * lin[2], 0.0, 1.0) for row in MACHADO_2009[kind])
    return linear_to_srgb(out[0]), linear_to_srgb(out[1]), linear_to_srgb(out[2])


def oklab_distance(a: str | Sequence[float], b: str | Sequence[float]) -> float:
    """Euclidean OKLab distance of two colours (alpha ignored)."""
    return delta_e_ok(_rgba01(a)[:3], _rgba01(b)[:3])


def red_ratio(colour: str | Sequence[float]) -> float:
    """WCAG 2.3.1's "saturated red" measure ``R / (R + G + B)`` on linear channels (0 for black). A state with a
    ratio >= 0.8 is a saturated red (WCAG 2.2, "general flash and red flash thresholds", note 2)."""
    r, g, b, _ = _rgba01(colour)
    lr, lg, lb = srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b)
    total = lr + lg + lb
    return lr / total if total > 0 else 0.0
