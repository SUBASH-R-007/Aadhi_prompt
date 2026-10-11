// @ts-check
/**
 * Stroke drawing (design §4.8: 600–1200 ms per group): the SVG dash pattern that shows the first `p` of a
 * path, as a pure function of progress. The page measures the path once (`getTotalLength()`), sets
 * `stroke-dasharray` once and updates `stroke-dashoffset` per frame. Numbers are rounded to 1/1000 so the
 * stage's state key stays stable.
 */

/**
 * @typedef {object} StrokeDash
 * @property {string} dasharray   SVG `stroke-dasharray`
 * @property {string} dashoffset  SVG `stroke-dashoffset`
 * @property {boolean} visible    false when nothing should show (hide the path: a zero-length dash with
 *                                round caps would still paint a dot)
 */

/** @param {number} v  rounded to 3 decimals, never `-0` or exponent notation */
function fmt(v) {
  const r = Math.round(v * 1000) / 1000;
  if (r === 0) return '0';
  return Math.abs(r) >= 1e21 ? r.toFixed(0) : String(r);
}

/** @param {number} p */
const unit = (p) => (p > 0 ? (p >= 1 ? 1 : p) : 0);

/**
 * A little slack added to the measured length, so the dash at p = 1 covers the whole path even when the
 * browser's measurement is a hair short (1% of the length, at most 1 unit; works with `pathLength="1"`).
 * @param {number} length
 */
function padded(length) {
  return length + Math.min(1, length * 0.01);
}

/**
 * Dash pattern that draws the first `p` (0..1, clamped) of a path of `pathLength`.
 * @param {number} pathLength  from `getTotalLength()` (or the `pathLength` attribute)
 * @param {number} p           progress
 * @param {{reverse?: boolean}} [options]  `reverse` draws from the end of the path towards its start
 * @returns {StrokeDash}
 */
export function strokeDash(pathLength, p, { reverse = false } = {}) {
  const u = unit(p);
  const length = Number.isFinite(pathLength) && pathLength > 0 ? pathLength : 0;
  if (length === 0) return { dasharray: 'none', dashoffset: '0', visible: u > 0 };
  const L = padded(length);
  const offset = reverse ? -(1 - u) * L : (1 - u) * L;
  return { dasharray: `${fmt(L)} ${fmt(L)}`, dashoffset: fmt(offset), visible: u > 0 };
}

/**
 * Dash pattern that shows only the stretch [from, to] of a path (fractions 0..1), e.g. a pulse
 * travelling along a wire.
 * @param {number} pathLength
 * @param {number} from
 * @param {number} to
 * @returns {StrokeDash}
 */
export function strokeSegment(pathLength, from, to) {
  const a = unit(Math.min(from, to));
  const b = unit(Math.max(from, to));
  const length = Number.isFinite(pathLength) && pathLength > 0 ? pathLength : 0;
  if (length === 0 || b <= a) return { dasharray: 'none', dashoffset: '0', visible: false };
  const L = padded(length);
  const dash = (b - a) * L;
  return { dasharray: `${fmt(dash)} ${fmt(L + 1)}`, dashoffset: fmt(-a * L), visible: true };
}

/**
 * Per-item progress when `count` strokes draw one after another within one overall progress `p`.
 * `overlap` 0 draws them strictly in turn; 1 draws them all together.
 * @param {number} p
 * @param {number} count
 * @param {number} [overlap]  0..1 (default 0.3)
 * @returns {number[]}
 */
export function staggerProgress(p, count, overlap = 0.3) {
  const n = Math.max(0, Math.floor(count));
  if (n === 0) return [];
  const o = unit(overlap);
  const u = unit(p);
  if (n === 1 || o >= 1 || u >= 1) return new Array(n).fill(u);
  // Each item lasts `span`; item i starts at i * step, with the last one ending at 1 (rounding snapped).
  const span = 1 / (n - (n - 1) * o);
  const step = span * (1 - o);
  return Array.from({ length: n }, (_, i) => {
    const v = (u - i * step) / span;
    return v >= 1 - 1e-9 ? 1 : unit(v);
  });
}
