"""CSS colours for the Dynamic QA: parse what the stage reports, composite alpha, measure WCAG contrast (R-53).

``textBoxes()`` (stage contract v3, §7.3) reports each text box's ink and declared background as CSS colour strings.
Chrome serialises computed colours as ``rgb()``/``rgba()``, but a colour written in ``oklch()``, ``oklab()`` or
``color()`` keeps that notation, and the theme tokens are hex (§4.7), so all of them are read here:

* ``#rgb``, ``#rgba``, ``#rrggbb``, ``#rrggbbaa``;
* ``rgb()``/``rgba()`` (comma or space syntax, numbers or percentages, ``/ alpha``);
* ``hsl()``/``hsla()``;
* ``oklch(L C H / a)`` and ``oklab(L a b / a)`` (L as 0..1 or a percentage; C/a/b percentages of 0.4);
* ``color(srgb | srgb-linear | display-p3 r g b / a)``;
* ``transparent`` and a few named colours.

Out-of-gamut OKLCH colours are clipped per channel, which is what Chrome paints today (``colour_math.from_oklch``
gamut-maps instead, as color.js does for the colours the stage derives itself). Colours are returned as
``(r, g, b, a)`` floats in 0..1 (sRGB, gamma encoded). Pure functions; no I/O.

The maths (transfer functions, OKLab, HSL, Display P3, compositing, luminance, contrast, hex) is
``colour_math``'s, the Dynamic mode's one colour module; this module adds the CSS grammar and the clipping. Its hex
output rounds as the page does (``Math.round``: ties up; B1 used Python's ties-to-even ``round``).
"""

from __future__ import annotations

import math
import re

from . import colour_math as cm

RGBA = tuple[float, float, float, float]

_NAMED: dict[str, RGBA] = {
    "transparent": (0.0, 0.0, 0.0, 0.0),
    "black": (0.0, 0.0, 0.0, 1.0),
    "white": (1.0, 1.0, 1.0, 1.0),
    "red": (1.0, 0.0, 0.0, 1.0),
    "lime": (0.0, 1.0, 0.0, 1.0),
    "green": (0.0, 128 / 255, 0.0, 1.0),
    "blue": (0.0, 0.0, 1.0, 1.0),
    "yellow": (1.0, 1.0, 0.0, 1.0),
    "gray": (128 / 255, 128 / 255, 128 / 255, 1.0),
    "grey": (128 / 255, 128 / 255, 128 / 255, 1.0),
}
_FUNC = re.compile(r"^([a-z-]+)\(\s*(.*?)\s*\)$", re.IGNORECASE | re.DOTALL)
_HEX = re.compile(r"^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$", re.IGNORECASE)
_NUMBER = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)(%|deg|rad|grad|turn)?$", re.IGNORECASE)


def _clip(x: float) -> float:
    return 0.0 if x < 0.0 or math.isnan(x) else 1.0 if x > 1.0 else x


def srgb_encode(linear: float) -> float:
    """Linear light -> sRGB gamma encoded (0..1; clipped first, NaN -> 0)."""
    return cm.linear_to_srgb(_clip(linear))


def srgb_decode(encoded: float) -> float:
    """sRGB gamma encoded -> linear light (0..1; clipped first, NaN -> 0)."""
    return cm.srgb_to_linear(_clip(encoded))


def oklab_to_srgb(lightness: float, a: float, b: float) -> tuple[float, float, float]:
    """OKLab -> gamma-encoded sRGB, clipped to the gamut per channel (Ottosson's matrices)."""
    r, g, bl = cm.oklab_to_linear_srgb(lightness, a, b)
    return srgb_encode(r), srgb_encode(g), srgb_encode(bl)


def _token(text: str) -> tuple[float, str] | None:
    if text.lower() == "none":
        return 0.0, ""
    m = _NUMBER.match(text)
    if not m:
        return None
    return float(m.group(1)), (m.group(2) or "").lower()


def _split(args: str) -> tuple[list[str], str | None] | None:
    """``"a b c / d"`` or ``"a, b, c, d"`` -> (components, alpha or None)."""
    alpha: str | None = None
    if "/" in args:
        args, _, alpha = args.partition("/")
        alpha = alpha.strip()
        if not alpha or "/" in alpha:
            return None
    parts = [p for p in re.split(r"[\s,]+", args.strip()) if p]
    if alpha is None and len(parts) == 4:
        parts, alpha = parts[:3], parts[3]
    if len(parts) != 3:
        return None
    return parts, alpha


def _alpha(text: str | None) -> float | None:
    if text is None:
        return 1.0
    tok = _token(text)
    if tok is None or tok[1] not in ("", "%"):
        return None
    value, unit = tok
    return _clip(value / 100.0 if unit == "%" else value)


def _hue(value: float, unit: str) -> float | None:
    factor = {"": 1.0, "deg": 1.0, "grad": 0.9, "rad": 180.0 / math.pi, "turn": 360.0}.get(unit)
    return None if factor is None else (value * factor) % 360.0


def _rgb(parts: list[str]) -> tuple[float, float, float] | None:
    out = []
    for p in parts:
        tok = _token(p)
        if tok is None or tok[1] not in ("", "%"):
            return None
        value, unit = tok
        out.append(_clip(value / 100.0 if unit == "%" else value / 255.0))
    return out[0], out[1], out[2]


def _hsl(parts: list[str]) -> tuple[float, float, float] | None:
    toks = [_token(p) for p in parts]
    if any(t is None for t in toks):
        return None
    (h, hu), (s, su), (lv, lu) = toks  # type: ignore[misc]
    hue = _hue(h, hu)
    if hue is None or su not in ("", "%") or lu not in ("", "%"):
        return None
    return cm.hsl_to_srgb(hue, _clip(s / 100.0), _clip(lv / 100.0))


def _ok(parts: list[str], polar: bool) -> tuple[float, float, float] | None:
    toks = [_token(p) for p in parts]
    if any(t is None for t in toks):
        return None
    (lv, lu), (x, xu), (y, yu) = toks  # type: ignore[misc]
    if lu not in ("", "%"):
        return None
    lightness = lv / 100.0 if lu == "%" else lv
    if polar:
        if xu not in ("", "%"):
            return None
        chroma = x * 0.004 if xu == "%" else x
        hue = _hue(y, yu)
        if hue is None:
            return None
        _, a, b = cm.oklch_to_oklab(lightness, chroma, hue)
    else:
        if xu not in ("", "%") or yu not in ("", "%"):
            return None
        a = x * 0.004 if xu == "%" else x
        b = y * 0.004 if yu == "%" else y
    return oklab_to_srgb(lightness, a, b)


def _color_fn(args: str) -> RGBA | None:
    space, _, rest = args.strip().partition(" ")
    split = _split(rest)
    if split is None:
        return None
    parts, alpha_text = split
    values = []
    for p in parts:
        tok = _token(p)
        if tok is None or tok[1] not in ("", "%"):
            return None
        values.append(tok[0] / 100.0 if tok[1] == "%" else tok[0])
    alpha = _alpha(alpha_text)
    if alpha is None:
        return None
    space = space.lower()
    if space == "srgb":
        r, g, b = (_clip(v) for v in values)
    elif space == "srgb-linear":
        r, g, b = (srgb_encode(v) for v in values)
    elif space == "display-p3":
        r, g, b = (srgb_encode(v) for v in cm.display_p3_to_linear_srgb(*(srgb_decode(v) for v in values)))
    else:
        return None
    return r, g, b, alpha


def parse_colour(value: object) -> RGBA | None:
    """``(r, g, b, a)`` (0..1, sRGB) of a CSS colour string, or None when it is not one this module reads."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if not text or len(text) > 200:
        return None
    if text in _NAMED:
        return _NAMED[text]
    m = _HEX.match(text)
    if m:
        digits = m.group(1)
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        vals = [int(digits[i:i + 2], 16) / 255.0 for i in range(0, len(digits), 2)]
        return vals[0], vals[1], vals[2], vals[3] if len(vals) == 4 else 1.0
    m = _FUNC.match(text)
    if not m:
        return None
    name, args = m.group(1), m.group(2)
    if name == "color":
        return _color_fn(args)
    split = _split(args)
    if split is None:
        return None
    parts, alpha_text = split
    alpha = _alpha(alpha_text)
    if alpha is None:
        return None
    if name in ("rgb", "rgba"):
        rgb = _rgb(parts)
    elif name in ("hsl", "hsla"):
        rgb = _hsl(parts)
    elif name == "oklch":
        rgb = _ok(parts, polar=True)
    elif name == "oklab":
        rgb = _ok(parts, polar=False)
    else:
        return None
    if rgb is None:
        return None
    return rgb[0], rgb[1], rgb[2], alpha


def composite(top: RGBA, bottom: RGBA) -> RGBA:
    """``top`` painted over ``bottom`` (source-over in gamma-encoded sRGB, as browsers blend)."""
    return cm.source_over(top, bottom)


def _opaque(colour: RGBA | tuple[float, float, float]) -> tuple[float, float, float]:
    return _clip(colour[0]), _clip(colour[1]), _clip(colour[2])


def relative_luminance(colour: RGBA | tuple[float, float, float]) -> float:
    """WCAG 2.x relative luminance of an opaque sRGB colour (alpha ignored, channels clipped to 0..1)."""
    return cm.relative_luminance(_opaque(colour))


def contrast_ratio(a: RGBA | tuple[float, float, float], b: RGBA | tuple[float, float, float]) -> float:
    """WCAG 2.x contrast ratio (1..21) of two opaque colours (alphas ignored)."""
    return cm.contrast_ratio(_opaque(a), _opaque(b))


def text_contrast(ink: object, background: object) -> float | None:
    """Contrast of text ``ink`` on its declared ``background`` (CSS strings); translucent ink is composited over the
    background first. None when either colour cannot be read or the background itself is translucent (what shows
    through it is not declared, so the analytic check cannot know)."""
    fg, bg = parse_colour(ink), parse_colour(background)
    if fg is None or bg is None or bg[3] < 0.999:
        return None
    if fg[3] <= 0.0:
        return None  # invisible ink: nothing to read
    return contrast_ratio(composite(fg, bg), bg)


def to_hex(colour: RGBA | tuple[float, float, float]) -> str:
    """``#rrggbb`` of a colour (alpha dropped; channels clipped, ``Math.round`` as the page)."""
    return cm.to_hex(_opaque(colour))
