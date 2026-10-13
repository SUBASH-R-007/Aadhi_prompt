"""The one colour module of the Dynamic video mode (design §4.7, §7.12, R-53, R-54, R-55): sRGB transfer functions,
OKLab and OKLCH, gamut mapping, hex, WCAG luminance and contrast, alpha compositing, OKLCH mixing, desaturation,
colour-vision simulation and the red-flash measure.

Two duties, kept apart on purpose:

* **Parity with the stage.** The page derives colours at run time with ``web/js/motion/color.js`` (block colours,
  on-block ink, scrims, the hold desaturation, mixes). Everything here with a ``color.js`` counterpart is a line-by-line
  port of it: the same Ottosson matrices and constants, the same order of floating-point operations, ``math.cbrt``
  for ``Math.cbrt``, the CSS Color 4 gamut mapping (:func:`gamut_map_oklch`), ``Math.round`` (ties toward +inf, never
  Python's ties-to-even ``round``; :func:`js_round`), JavaScript's truncated ``%`` (``math.fmod``), lower-case hex,
  and arithmetic on color.js's own 0..255 channel scale wherever color.js computes on it (contrast blending,
  compositing). So a colour the resolver stores (``ResolvedLook.tokens``, plan §1.2) is computed with the stage's
  own arithmetic (the B1 stage's derivation exactly, except where ``themes.py`` deliberately applies a rule of its own
  on top: the R-54 red lowering, the hold-safe block text and the accent nudge), and a contrast the validator
  (``themes.py``) measures is the one the page and preflight measure.
  ``tests/fixtures/colour_vectors.json`` is written by ``scripts/gen_colour_vectors.mjs`` from ``color.js`` (and the
  stage's colour helpers); ``tests/compose/dynamic/test_colour_math.py`` asserts this module reproduces every vector
  (hex equal, floats within 1e-9) and ``web/tests/motion/colourVectors.test.js`` that the JS still writes the
  committed file, so a drift on either side fails CI. Platform maths (``pow``, ``cbrt``, ``atan2``, ``hypot``) may
  differ from V8's by an ulp, far below both tolerances.
* **The palette generator.** A palette's anchor colours (``bg``, ``struct.accent``, ``ink``, ... in ``themes.py``) are
  defined by an OKLCH lightness and hue, which carry the contrast bands of §4.7. :func:`palette_hex` keeps both exactly
  and lowers only the chroma until the colour is inside sRGB: B1's ``presentation.oklch_hex``, moved here unchanged in
  result (every B1 token is pinned by ``tests/compose/dynamic/test_themes.py``, and a 160k-point grid matched B1 bit
  for bit when this module was written). It never clips, unlike CSS Color 4's mapping, which accepts a clip within a
  just-noticeable difference and so shifts hue and lightness a little: right for the stage's run-time mixes, wrong for
  a palette's anchors. The page never recomputes an anchor from OKLCH (anchors reach it as hex), so the two never meet.

Colours are CSS hex strings (``#rgb``, ``#rgba``, ``#rrggbb``, ``#rrggbbaa``, any case) or the keywords color.js reads
(``transparent``, ``black``, ``white``), or gamma-encoded sRGB sequences on 0..1 (``(r, g, b)`` or ``(r, g, b, a)``).
CSS functional notations (``rgb()``, ``oklch()``, ...) are parsed by preflight's ``aadhi.compose.dynamic.colour``,
which measures what Chrome paints and builds on this module's maths. Pure functions: no I/O, no state.

Imported only inside the Dynamic gate (``aadhi.compose.presentation`` imports it lazily): the classic renderer never
loads ``aadhi.compose.dynamic``.
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
ColourLike = str | Sequence[float]
CvdKind = Literal["protan", "deutan", "tritan"]

# color.js constants (the JS names in the comments).
ACHROMATIC_CHROMA: Final = 1e-4  # ACHROMATIC_CHROMA: below it a colour is grey and its hue "powerless" (CSS Color 4)
GAMUT_EPS: Final = 1e-6  # GAMUT_EPS: inGamut() tolerance on gamma-encoded channels
MAP_JND: Final = 0.02  # gamutMapOklch JND: a clip within this OKLab distance is accepted
MAP_EPSILON: Final = 0.0001  # gamutMapOklch EPSILON: chroma resolution of the binary search
# palette_hex (B1 presentation.oklch_hex): bisection steps and the in-gamut tolerance on *linear* channels.
PALETTE_BISECT_STEPS: Final = 24
PALETTE_GAMUT_EPS: Final = 1e-6
# WCAG 2.2 "general flash and red flash thresholds": a state is a saturated red when R / (R + G + B) >= 0.8, R, G, B
# being the linear channels of the relative-luminance definition (R-54).
SATURATED_RED_RATIO: Final = 0.8

_HEX = re.compile(r"#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})")  # used with fullmatch on the lower-cased text
_KEYWORDS: Final[dict[str, RGBA]] = {  # the only keywords color.js parseColor reads (themes are hex); 0..255 scale
    "transparent": (0.0, 0.0, 0.0, 0.0),
    "black": (0.0, 0.0, 0.0, 1.0),
    "white": (255.0, 255.0, 255.0, 1.0),
}

# Machado, Oliveira & Fernandes 2009, "A physiologically-based model for simulation of color vision deficiency"
# (IEEE TVCG 15(6)): the severity-1.0 (dichromacy) matrices, applied to linear sRGB (design §4.7 "Colour vision").
MACHADO_2009: Final[dict[str, tuple[RGB, RGB, RGB]]] = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
}
CVD_KINDS: Final[tuple[CvdKind, ...]] = ("protan", "deutan", "tritan")
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
    """``Math.round``: the nearest integer, ties toward +inf (Python's ``round`` ties to even: 0.5 -> 0, 2.5 -> 2).
    Computed as ``floor`` plus the fraction test, never ``floor(x + 0.5)``, which is wrong for 0.49999999999999994."""
    if not math.isfinite(x):
        raise ValueError(f"not a finite number: {x!r}")
    f = math.floor(x)
    return int(f) + 1 if x - f >= 0.5 else int(f)


def norm_hue(h: float) -> float:
    """color.js ``normHue``: ``((h % 360) + 360) % 360`` with JavaScript's truncated ``%`` (``math.fmod``), so a hue a
    hair below 0 lands where the page puts it (Python's floored ``%`` could give 360.0). NaN/inf give NaN, as in JS."""
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
    """OKLCH -> OKLab, hue in degrees converted as color.js does: ``(h * Math.PI) / 180``."""
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
    ``gamutMapOklch``: lightness <= 0 (or NaN) is black and >= 1 white; an in-gamut colour is returned clipped; else a
    binary search on chroma (lightness and hue kept) that accepts a clipped colour once it lies within the 0.02 OKLab
    "JND" of the unclipped one. Gamma-encoded sRGB 0..1, clipped."""
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
    """``#RRGGBB`` (upper case, as every stored token since B1) of a palette anchor colour: the largest chroma <=
    ``chroma`` whose *linear* sRGB lies in the gamut (+-1e-6), found by a 24-step bisection, at exactly this lightness
    and hue; never clipped. B1's ``presentation.oklch_hex`` moved here: the hue is converted with ``math.radians`` as
    there, so every B1 token is reproduced bit for bit (the only other difference, ``js_round`` for Python's ``round``,
    matters at an exact .5 only)."""

    def linear(c: float) -> RGB:
        rad = math.radians(hue)
        return oklab_to_linear_srgb(lightness, c * math.cos(rad), c * math.sin(rad))

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


# --- hex and colour values ----------------------------------------------------------------------------------------


def _hex2(v01: float) -> str:
    """color.js ``hex2``: ``Math.round(clamp(v, 0, 1) * 255)`` as two lower-case hex digits."""
    return f"{js_round(clamp(v01, 0.0, 1.0) * 255):02x}"


def _js_rgba(colour: str) -> RGBA:
    """color.js ``parseColor`` of a hex string or one of its three keywords, on its own scale: channels 0..255
    (integers for hex), alpha 0..1. ``ValueError`` where color.js ``mustParse`` throws."""
    text = colour.strip().lower() if isinstance(colour, str) else ""
    if text in _KEYWORDS:
        return _KEYWORDS[text]
    m = _HEX.fullmatch(text)
    if not m:
        raise ValueError(f"not a colour: {colour!r}")
    digits = m.group(1)
    if len(digits) <= 4:
        digits = "".join(ch + ch for ch in digits)
    n = [int(digits[i:i + 2], 16) for i in range(0, len(digits), 2)]
    return float(n[0]), float(n[1]), float(n[2]), n[3] / 255 if len(n) == 4 else 1.0


def _channels(colour: ColourLike) -> tuple[RGBA, float]:
    """``((r, g, b, a), scale)``: a string on color.js's 0..255 scale (``scale`` 255), a sequence as given on 0..1
    (``scale`` 1). Dividing a channel by ``scale`` gives exactly what color.js computes as ``r / 255``."""
    if isinstance(colour, str):
        return _js_rgba(colour), 255.0
    if len(colour) not in (3, 4):
        raise ValueError(f"not a colour: {colour!r}")
    a = float(colour[3]) if len(colour) == 4 else 1.0
    return (float(colour[0]), float(colour[1]), float(colour[2]), a), 1.0


def _rgba01(colour: ColourLike) -> RGBA:
    (r, g, b, a), scale = _channels(colour)
    return r / scale, g / scale, b / scale, a


def parse_hex(colour: str) -> RGBA | None:
    """``(r, g, b, a)`` 0..1 of a ``#rgb``/``#rgba``/``#rrggbb``/``#rrggbbaa`` string (any case), else None."""
    if not isinstance(colour, str) or not _HEX.fullmatch(colour.strip().lower()):
        return None
    return _rgba01(colour)


def is_hex6(colour: object) -> bool:
    """True for ``#rrggbb`` (any case): what core.js ``makeRoles`` accepts as a token (its ``HEX`` test)."""
    return isinstance(colour, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", colour) is not None


def to_hex(colour: ColourLike) -> str:
    """color.js ``toHex``: ``#rrggbb``, or ``#rrggbbaa`` when the alpha is below 1 (``Math.round``, lower case). A
    sequence is on 0..1, a string is parsed first (so ``to_hex('#ABC')`` is ``'#aabbcc'``)."""
    r, g, b, a = _rgba01(colour)
    out = f"#{_hex2(r)}{_hex2(g)}{_hex2(b)}"
    return out if a >= 1 else f"{out}{_hex2(clamp(a, 0.0, 1.0))}"


def with_alpha(colour: str, alpha_hex: str) -> str:
    """An opaque hex colour followed by two alpha digits, exactly as the stage writes a scrim (board_generic
    ``${bg}${SCRIM_ALPHA_HEX}``): the colour's own spelling is kept."""
    if not is_hex6(colour) or re.fullmatch(r"[0-9a-fA-F]{2}", alpha_hex) is None:
        raise ValueError(f"not an opaque hex colour and alpha: {colour!r}, {alpha_hex!r}")
    return f"{colour}{alpha_hex}"


def hsl_to_srgb(hue: float, saturation: float, lightness: float) -> RGB:
    """HSL (hue degrees, s and l 0..1) -> sRGB 0..1 (CSS Color 4; color.js ``hslToSrgb01``)."""

    def f(n: float) -> float:
        k = math.fmod(n + hue / 30, 12)
        a = saturation * min(lightness, 1 - lightness)
        return lightness - a * max(-1.0, min(k - 3, 9 - k, 1.0))

    return f(0), f(8), f(4)


def display_p3_to_linear_srgb(r_lin: float, g_lin: float, b_lin: float) -> RGB:
    """Linear Display P3 -> linear sRGB (D65)."""
    (m00, m01, m02), (m10, m11, m12), (m20, m21, m22) = P3_TO_SRGB
    return (m00 * r_lin + m01 * g_lin + m02 * b_lin, m10 * r_lin + m11 * g_lin + m12 * b_lin,
            m20 * r_lin + m21 * g_lin + m22 * b_lin)


# --- OKLCH of colours, mixing, desaturation --------------------------------------------------------------------------


def to_oklch(colour: ColourLike) -> Oklch:
    """OKLCH of a colour (color.js ``toOklch``): greys (chroma < 1e-4) get hue 0."""
    r, g, b, a = _rgba01(colour)
    lightness, a_, b_ = linear_srgb_to_oklab(srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b))
    c = math.hypot(a_, b_)
    h = 0.0 if c < ACHROMATIC_CHROMA else norm_hue((math.atan2(b_, a_) * 180) / math.pi)
    return Oklch(lightness, c, h, a)


def from_oklch(lightness: float, chroma: float, hue: float, alpha: float = 1.0) -> str:
    """CSS hex of an OKLCH colour (color.js ``fromOklch``): CSS Color 4 gamut mapping, ``Math.round`` to 8 bits,
    ``#rrggbb`` (lower case) or ``#rrggbbaa`` when translucent. A non-finite hue counts as 0 and a non-finite alpha
    as 1, as there; a NaN chroma (which color.js would print as ``#NaNNaNNaN``) raises ``ValueError``."""
    a = clamp(alpha, 0.0, 1.0) if math.isfinite(alpha) else 1.0
    c = math.nan if math.isnan(chroma) else max(0.0, chroma)
    rgb = gamut_map_oklch(lightness, c, norm_hue(hue if math.isfinite(hue) else 0.0))
    out = f"#{_hex2(rgb[0])}{_hex2(rgb[1])}{_hex2(rgb[2])}"
    return out if a >= 1 else f"{out}{_hex2(a)}"


def mix_oklch_values(a: Oklch, b: Oklch, p: float) -> Oklch:
    """Interpolate two OKLCH colours (color.js ``mixOklchValues``, CSS Color 4): the shorter hue arc, a grey end
    borrows the other end's hue (both grey: hue 0), L and C premultiplied by alpha; ``p`` clamped to 0..1 (NaN -> 0).
    A fully transparent result keeps the plain interpolation of L and C."""
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


def mix_oklch(a: ColourLike, b: ColourLike, p: float) -> str:
    """``a`` mixed toward ``b`` in OKLCH by ``p`` (color.js ``mixOklch``: 0 is ``a``, 1 is ``b``): CSS hex, lower
    case (``#rrggbbaa`` when the mix is translucent)."""
    m = mix_oklch_values(to_oklch(a), to_oklch(b), p)
    return from_oklch(m.l, m.c, m.h, m.a)


def desaturate(colour: str, amount: float) -> str:
    """``colour`` with its OKLCH chroma scaled by ``1 - min(1, amount)``, lightness and hue kept: the R-55 hold freeze
    (web/js/dynamic/core.js ``desaturate``). ``amount <= 0`` (or NaN) returns the string unchanged, as the page does;
    otherwise the result is opaque lower-case hex (the alpha is dropped, as there)."""
    if not (amount > 0):
        return colour
    c = to_oklch(colour)
    return from_oklch(c.l, c.c * (1 - min(1.0, amount)), c.h)


# --- compositing, luminance, contrast --------------------------------------------------------------------------------


def source_over(top: Sequence[float], bottom: Sequence[float]) -> RGBA:
    """``top`` painted over ``bottom`` (source-over in gamma-encoded sRGB, the way browsers blend). Channels on any
    one scale (0..1, or color.js's 0..255), alpha 0..1, in color.js ``composite``'s order of operations. A fully
    transparent result is ``(0, 0, 0, 0)``."""
    ta, ba = top[3], bottom[3]
    out_a = ta + ba * (1.0 - ta)  # 1.0: a float result even for int alphas
    if out_a <= 0:
        return 0.0, 0.0, 0.0, 0.0
    return (
        (top[0] * ta + bottom[0] * ba * (1.0 - ta)) / out_a,
        (top[1] * ta + bottom[1] * ba * (1.0 - ta)) / out_a,
        (top[2] * ta + bottom[2] * ba * (1.0 - ta)) / out_a,
        out_a,
    )


def composite(top: str, bottom: str) -> str:
    """``top`` over ``bottom`` as CSS hex (color.js ``composite``): ``#rrggbb`` when opaque, else ``#rrggbbaa``; a fully
    transparent result is ``#00000000``. Computed on color.js's 0..255 channels, so the rounding is the page's."""
    out = source_over(_js_rgba(top), _js_rgba(bottom))
    if out[3] <= 0:
        return "#00000000"
    return to_hex((out[0] / 255, out[1] / 255, out[2] / 255, clamp(out[3], 0.0, 1.0)))


def relative_luminance(colour: ColourLike) -> float:
    """WCAG 2 relative luminance, alpha ignored (color.js ``relativeLuminance``)."""
    (r, g, b, _), scale = _channels(colour)
    return 0.2126 * srgb_to_linear(r / scale) + 0.7152 * srgb_to_linear(g / scale) + 0.0722 * srgb_to_linear(b / scale)


def contrast_ratio(fg: ColourLike, bg: ColourLike) -> float:
    """WCAG 2 contrast ratio 1..21 (color.js ``contrastRatio``): a translucent ``fg`` (text) is blended over ``bg``,
    whose own alpha is ignored. The blend runs on color.js's 0..255 scale when either colour is a string."""
    (fr, fg_, fb, k), fs = _channels(fg)
    (br, bg_, bb, _), bs = _channels(bg)
    if fs != bs:  # one string, one 0..1 sequence: both on color.js's scale
        fr, fg_, fb = (fr * 255, fg_ * 255, fb * 255) if fs == 1.0 else (fr, fg_, fb)
        br, bg_, bb = (br * 255, bg_ * 255, bb * 255) if bs == 1.0 else (br, bg_, bb)
        fs = bs = 255.0
    solid = (fr, fg_, fb) if k >= 1 else (fr * k + br * (1 - k), fg_ * k + bg_ * (1 - k), fb * k + bb * (1 - k))
    l1 = relative_luminance((solid[0] / fs, solid[1] / fs, solid[2] / fs))
    l2 = relative_luminance((br / bs, bg_ / bs, bb / bs))
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


def worst_contrast(fg: ColourLike, bgs: Sequence[ColourLike]) -> float:
    """The lowest contrast of ``fg`` over any of ``bgs`` (R-53's worst case); ``ValueError`` for no backgrounds."""
    if not bgs:
        raise ValueError("worst_contrast needs at least one background")
    return min(contrast_ratio(fg, b) for b in bgs)


# --- colour vision and flash safety ----------------------------------------------------------------------------------


def simulate_cvd(colour: ColourLike, kind: CvdKind) -> RGB:
    """``colour`` as seen with a dichromacy (Machado 2009, severity 1.0): the matrix applied to linear sRGB, the result
    clipped to 0..1 and gamma-encoded again (alpha ignored). The design's palette search (§4.7) used the same model."""
    r, g, b, _ = _rgba01(colour)
    lin = (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b))
    out = [clamp(row[0] * lin[0] + row[1] * lin[1] + row[2] * lin[2], 0.0, 1.0) for row in MACHADO_2009[kind]]
    return linear_to_srgb(out[0]), linear_to_srgb(out[1]), linear_to_srgb(out[2])


def oklab_distance(a: ColourLike, b: ColourLike) -> float:
    """Euclidean OKLab distance of two colours (alpha ignored)."""
    return delta_e_ok(_rgba01(a)[:3], _rgba01(b)[:3])


def red_ratio(colour: ColourLike) -> float:
    """WCAG 2.3.1's saturated-red measure ``R / (R + G + B)`` on linear channels (0 for black); a state with a ratio
    >= :data:`SATURATED_RED_RATIO` is a saturated red (R-54)."""
    r, g, b, _ = _rgba01(colour)
    lr, lg, lb = srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b)
    total = lr + lg + lb
    return lr / total if total > 0 else 0.0
