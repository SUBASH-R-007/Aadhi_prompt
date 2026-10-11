// @ts-check
/**
 * Colour maths for the Dynamic stage (design §4.7): CSS colour parsing, sRGB <-> OKLab/OKLCH, the CSS
 * Color 4 gamut mapping (chroma reduction, so lightness and hue survive), interpolation in OKLCH with
 * premultiplied alpha and the shorter hue arc, and the WCAG 2 contrast ratio. Pure functions only.
 *
 * Matrices: Björn Ottosson's OKLab (linear sRGB <-> LMS); the Python theme validator must use the same
 * ones so the page and the catalogue agree.
 */

/**
 * sRGB colour, gamma-encoded, channels 0..255 (floats, not rounded), alpha 0..1.
 * @typedef {{r: number, g: number, b: number, a: number}} Rgba
 */
/**
 * OKLCH colour: l 0..1, c >= 0, h in degrees [0, 360), alpha 0..1.
 * @typedef {{l: number, c: number, h: number, a: number}} Oklch
 */
/** @typedef {string | Rgba} ColorLike */

/** Below this chroma a colour counts as grey and its hue is "powerless" (CSS Color 4). */
export const ACHROMATIC_CHROMA = 1e-4;

/** @param {number} v  @param {number} lo  @param {number} hi */
const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

/** sRGB transfer, decode (0..1, sign-preserving as CSS Color 4 does for extended values). */
function toLinear(v) {
  const s = Math.abs(v);
  const out = s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  return v < 0 ? -out : out;
}

/** sRGB transfer, encode (0..1). */
function toGamma(v) {
  const s = Math.abs(v);
  const out = s <= 0.0031308 ? s * 12.92 : 1.055 * s ** (1 / 2.4) - 0.055;
  return v < 0 ? -out : out;
}

/**
 * Linear sRGB (0..1) to OKLab.
 * @param {number} r @param {number} g @param {number} b
 * @returns {[number, number, number]}
 */
export function linearSrgbToOklab(r, g, b) {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

/**
 * OKLab to linear sRGB (0..1, unclamped).
 * @param {number} L @param {number} a @param {number} b
 * @returns {[number, number, number]}
 */
export function oklabToLinearSrgb(L, a, b) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

/** OKLCH (l, c, h) -> gamma-encoded sRGB 0..1, unclamped. */
function oklchToSrgb01(l, c, h) {
  const rad = (h * Math.PI) / 180;
  const lin = oklabToLinearSrgb(l, c * Math.cos(rad), c * Math.sin(rad));
  return [toGamma(lin[0]), toGamma(lin[1]), toGamma(lin[2])];
}

const GAMUT_EPS = 1e-6;
/** @param {number[]} rgb01 */
const inGamut = (rgb01) => rgb01.every((v) => v >= -GAMUT_EPS && v <= 1 + GAMUT_EPS);

/** Euclidean distance in OKLab between two gamma-encoded sRGB triples (0..1). */
function deltaEOK(rgbA, rgbB) {
  const a = linearSrgbToOklab(toLinear(rgbA[0]), toLinear(rgbA[1]), toLinear(rgbA[2]));
  const b = linearSrgbToOklab(toLinear(rgbB[0]), toLinear(rgbB[1]), toLinear(rgbB[2]));
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

/**
 * Map an OKLCH colour into the sRGB gamut with the CSS Color 4 algorithm (binary search on chroma with
 * a local MINDE of 0.02), keeping lightness and hue. Returns gamma-encoded sRGB 0..1, clipped.
 * @param {number} l @param {number} c @param {number} h
 * @returns {[number, number, number]}
 */
export function gamutMapOklch(l, c, h) {
  if (!(l > 0)) return [0, 0, 0];
  if (l >= 1) return [1, 1, 1];
  /** @param {number[]} v @returns {[number, number, number]} */
  const clip = (v) => [clamp(v[0], 0, 1), clamp(v[1], 0, 1), clamp(v[2], 0, 1)];
  const origin = oklchToSrgb01(l, c, h);
  if (inGamut(origin)) return clip(origin);
  const JND = 0.02;
  const EPSILON = 0.0001;
  let clipped = clip(origin);
  if (deltaEOK(clipped, origin) < JND) return clipped;
  let min = 0;
  let max = c;
  let minInGamut = true;
  while (max - min > EPSILON) {
    const chroma = (min + max) / 2;
    const current = oklchToSrgb01(l, chroma, h);
    if (minInGamut && inGamut(current)) {
      min = chroma;
      continue;
    }
    clipped = clip(current);
    const e = deltaEOK(clipped, current);
    if (e < JND) {
      if (JND - e < EPSILON) return clipped;
      minInGamut = false;
      min = chroma;
    } else {
      max = chroma;
    }
  }
  return clipped;
}

const HEX_RE = /^#([0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const FUNC_RE = /^([a-z]+)\(\s*([^)]*)\)$/i;

/**
 * A CSS number or percentage; `pct` is what 100% means. `none` is 0 (CSS Color 4 missing component).
 * @param {string} token @param {number} pct
 */
function cssNumber(token, pct) {
  if (token === 'none') return 0;
  if (token.endsWith('%')) return (parseFloat(token) / 100) * pct;
  return Number(token);
}

/** A CSS hue in degrees (`deg`, `rad`, `grad`, `turn` or a bare number). */
function cssHue(token) {
  if (token === 'none') return 0;
  const m = /^(-?[\d.]+(?:e[-+]?\d+)?)(deg|rad|grad|turn)?$/i.exec(token);
  if (!m) return NaN;
  const v = parseFloat(m[1]);
  const unit = (m[2] || 'deg').toLowerCase();
  if (unit === 'rad') return (v * 180) / Math.PI;
  if (unit === 'grad') return v * 0.9;
  if (unit === 'turn') return v * 360;
  return v;
}

/** Split `a b c / d` or `a, b, c, d` into [channels, alpha token]. */
function splitArgs(body) {
  const [main, slashAlpha] = body.split('/');
  const parts = main.split(/[\s,]+/).filter(Boolean);
  let alpha = slashAlpha?.trim();
  if (alpha === undefined && parts.length === 4) alpha = parts.pop();
  return { parts, alpha };
}

/** @param {number} h */
const normHue = (h) => ((h % 360) + 360) % 360;

/** HSL (h degrees, s and l 0..1) to sRGB 0..1. */
function hslToSrgb01(h, s, l) {
  /** @param {number} n */
  const f = (n) => {
    const k = (n + h / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  };
  return [f(0), f(8), f(4)];
}

/**
 * Parse a CSS colour: `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`, `rgb()`/`rgba()`, `hsl()`/`hsla()`,
 * `oklch()`, `oklab()`, `transparent`, `black` and `white`. Out-of-gamut OKLCH/OKLab values are gamut-mapped.
 * Returns null for anything else (other named colours are not supported on purpose: themes are hex).
 * @param {ColorLike | null | undefined} input
 * @returns {Rgba | null}
 */
export function parseColor(input) {
  if (input && typeof input === 'object') {
    const { r, g, b, a } = input;
    return [r, g, b].every(Number.isFinite) ? { r, g, b, a: Number.isFinite(a) ? clamp(a, 0, 1) : 1 } : null;
  }
  if (typeof input !== 'string') return null;
  const v = input.trim().toLowerCase();
  if (v === 'transparent') return { r: 0, g: 0, b: 0, a: 0 };
  if (v === 'black') return { r: 0, g: 0, b: 0, a: 1 };
  if (v === 'white') return { r: 255, g: 255, b: 255, a: 1 };
  const hex = HEX_RE.exec(v);
  if (hex) {
    let h = hex[1];
    if (h.length <= 4) h = [...h].map((ch) => ch + ch).join('');
    const n = (i) => parseInt(h.slice(i, i + 2), 16);
    return { r: n(0), g: n(2), b: n(4), a: h.length === 8 ? n(6) / 255 : 1 };
  }
  const fn = FUNC_RE.exec(v);
  if (!fn) return null;
  const name = fn[1];
  const { parts, alpha } = splitArgs(fn[2]);
  if (parts.length !== 3) return null;
  const a = alpha === undefined ? 1 : clamp(cssNumber(alpha, 1), 0, 1);
  /** @type {number[]} */
  let rgb01;
  if (name === 'rgb' || name === 'rgba') {
    rgb01 = parts.map((p) => cssNumber(p, 255) / 255);
  } else if (name === 'hsl' || name === 'hsla') {
    rgb01 = hslToSrgb01(normHue(cssHue(parts[0])), clamp(cssNumber(parts[1], 1), 0, 1), clamp(cssNumber(parts[2], 1), 0, 1));
  } else if (name === 'oklch') {
    rgb01 = gamutMapOklch(cssNumber(parts[0], 1), Math.max(0, cssNumber(parts[1], 0.4)), normHue(cssHue(parts[2])));
  } else if (name === 'oklab') {
    const L = cssNumber(parts[0], 1);
    const A = cssNumber(parts[1], 0.4);
    const B = cssNumber(parts[2], 0.4);
    rgb01 = gamutMapOklch(L, Math.hypot(A, B), normHue((Math.atan2(B, A) * 180) / Math.PI));
  } else {
    return null;
  }
  if (!rgb01.every(Number.isFinite) || !Number.isFinite(a)) return null;
  return { r: clamp(rgb01[0], 0, 1) * 255, g: clamp(rgb01[1], 0, 1) * 255, b: clamp(rgb01[2], 0, 1) * 255, a };
}

/** @param {ColorLike} color @param {string} fn */
function mustParse(color, fn) {
  const c = parseColor(color);
  if (!c) throw new TypeError(`${fn}: not a colour: ${String(color)}`);
  return c;
}

/**
 * Convert a colour to OKLCH. Greys get hue 0 (check `c < ACHROMATIC_CHROMA` for "no hue").
 * @param {ColorLike} color
 * @returns {Oklch}
 */
export function toOklch(color) {
  const { r, g, b, a } = mustParse(color, 'toOklch');
  const [L, A, B] = linearSrgbToOklab(toLinear(r / 255), toLinear(g / 255), toLinear(b / 255));
  const c = Math.hypot(A, B);
  const h = c < ACHROMATIC_CHROMA ? 0 : normHue((Math.atan2(B, A) * 180) / Math.PI);
  return { l: L, c, h, a };
}

/** @param {number} v  0..1 */
const hex2 = (v) => Math.round(clamp(v, 0, 1) * 255).toString(16).padStart(2, '0');

/**
 * `#rrggbb`, or `#rrggbbaa` when translucent (channels rounded to 8 bits).
 * @param {ColorLike} color
 * @returns {string}
 */
export function toHex(color) {
  const { r, g, b, a } = mustParse(color, 'toHex');
  const rgb = `#${hex2(r / 255)}${hex2(g / 255)}${hex2(b / 255)}`;
  return a >= 1 ? rgb : `${rgb}${hex2(a)}`;
}

/**
 * An OKLCH colour as CSS hex (gamut-mapped by chroma reduction, then rounded to 8 bits).
 * @param {{l: number, c: number, h: number, a?: number}} lch
 * @returns {string}
 */
export function fromOklch(lch) {
  const a = typeof lch.a === 'number' && Number.isFinite(lch.a) ? clamp(lch.a, 0, 1) : 1;
  const rgb = gamutMapOklch(lch.l, Math.max(0, lch.c), normHue(Number.isFinite(lch.h) ? lch.h : 0));
  const out = `#${hex2(rgb[0])}${hex2(rgb[1])}${hex2(rgb[2])}`;
  return a >= 1 ? out : `${out}${hex2(a)}`;
}

/**
 * Interpolate two OKLCH colours (already converted): CSS Color 4 rules, i.e. the shorter hue arc, a grey
 * end borrows the other end's hue, and L and C are premultiplied by alpha. `p` is clamped to [0, 1].
 * @param {Oklch} A @param {Oklch} B @param {number} p
 * @returns {Oklch}
 */
export function mixOklchValues(A, B, p) {
  const u = Number.isNaN(p) ? 0 : clamp(p, 0, 1);
  const greyA = A.c < ACHROMATIC_CHROMA;
  const greyB = B.c < ACHROMATIC_CHROMA;
  const hA = greyA && !greyB ? B.h : A.h;
  const hB = greyB && !greyA ? A.h : B.h;
  let d = hB - hA;
  if (d > 180) d -= 360;
  else if (d < -180) d += 360;
  const h = greyA && greyB ? 0 : normHue(hA + d * u);
  const a = A.a + (B.a - A.a) * u;
  if (a <= 0) return { l: A.l + (B.l - A.l) * u, c: A.c + (B.c - A.c) * u, h, a: 0 };
  const l = (A.l * A.a + (B.l * B.a - A.l * A.a) * u) / a;
  const c = (A.c * A.a + (B.c * B.a - A.c * A.a) * u) / a;
  return { l, c, h, a };
}

/**
 * Mix two colours in OKLCH: `mixOklch(a, b, 0)` is a, `mixOklch(a, b, 1)` is b. Returns CSS hex.
 * @param {ColorLike} a @param {ColorLike} b @param {number} p  0..1 (clamped)
 * @returns {string}
 */
export function mixOklch(a, b, p) {
  return fromOklch(mixOklchValues(toOklch(a), toOklch(b), p));
}

/**
 * WCAG 2 relative luminance of an opaque colour (alpha ignored).
 * @param {ColorLike} color
 * @returns {number}
 */
export function relativeLuminance(color) {
  const { r, g, b } = mustParse(color, 'relativeLuminance');
  return 0.2126 * toLinear(r / 255) + 0.7152 * toLinear(g / 255) + 0.0722 * toLinear(b / 255);
}

/**
 * WCAG 2 contrast ratio (1..21). A translucent first colour (text) is composited over the second
 * (background), whose own alpha is ignored.
 * @param {ColorLike} a  foreground
 * @param {ColorLike} b  background
 * @returns {number}
 */
export function contrastRatio(a, b) {
  const fg = mustParse(a, 'contrastRatio');
  const bg = mustParse(b, 'contrastRatio');
  const k = fg.a;
  const solid = k >= 1 ? fg : { r: fg.r * k + bg.r * (1 - k), g: fg.g * k + bg.g * (1 - k), b: fg.b * k + bg.b * (1 - k), a: 1 };
  const l1 = relativeLuminance(solid);
  const l2 = relativeLuminance({ ...bg, a: 1 });
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}
