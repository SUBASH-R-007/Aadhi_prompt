// @ts-check
/**
 * Small pure helpers for numbers, time and text formatting (no DOM access).
 */

/**
 * Clamp `value` into [lo, hi]. NaN becomes `lo`.
 * @param {number} value
 * @param {number} lo
 * @param {number} hi
 * @returns {number}
 */
export function clamp(value, lo, hi) {
  if (Number.isNaN(value)) return lo;
  return value < lo ? lo : value > hi ? hi : value;
}

/**
 * Linear interpolation between a and b.
 * @param {number} a
 * @param {number} b
 * @param {number} u  0..1 (not clamped)
 */
export function lerp(a, b, u) {
  return a + (b - a) * u;
}

/**
 * Finite-number guard with a fallback.
 * @param {unknown} value
 * @param {number} fallback
 * @returns {number}
 */
export function num(value, fallback = 0) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

/** @param {number} n */
function pad2(n) {
  return n < 10 ? `0${n}` : String(n);
}

/**
 * Format seconds as `m:ss` or `h:mm:ss` (floor; negatives and NaN count as 0).
 * @param {number} seconds
 * @returns {string}
 */
export function formatTime(seconds) {
  const s = Math.max(0, Math.floor(num(seconds, 0) + 1e-6));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0 ? `${h}:${pad2(m)}:${pad2(sec)}` : `${m}:${pad2(sec)}`;
}

/**
 * Human-readable duration for screen readers, e.g. "1 minute 5 seconds".
 * @param {number} seconds
 * @returns {string}
 */
export function formatDurationLong(seconds) {
  const s = Math.max(0, Math.round(num(seconds, 0)));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  /** @type {string[]} */
  const parts = [];
  if (h) parts.push(`${h} hour${h === 1 ? '' : 's'}`);
  if (m) parts.push(`${m} minute${m === 1 ? '' : 's'}`);
  if (sec || parts.length === 0) parts.push(`${sec} second${sec === 1 ? '' : 's'}`);
  return parts.join(' ');
}

/**
 * Playback-rate label: 1 -> "1x", 1.25 -> "1.25x".
 * @param {number} rate
 */
export function formatRate(rate) {
  return `${Number(num(rate, 1).toFixed(2))}x`;
}

/**
 * Round to a number of decimals (stable keys for float times).
 * @param {number} value
 * @param {number} [decimals]
 */
export function roundTo(value, decimals = 3) {
  const f = 10 ** decimals;
  return Math.round(value * f) / f;
}

/**
 * 32-bit FNV-1a hash as 8 hex chars (deterministic, non-cryptographic; used for state keys).
 * @param {string} text
 * @returns {string}
 */
export function fnv1a(text) {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, '0');
}

/**
 * Truncate text to `max` characters with an ellipsis.
 * @param {string} text
 * @param {number} max
 */
export function truncate(text, max) {
  const s = String(text ?? '');
  return s.length <= max ? s : `${s.slice(0, Math.max(0, max - 1))}…`;
}

/**
 * Letter label for an option index: 0 -> "A".
 * @param {number} index
 */
export function optionLetter(index) {
  return String.fromCharCode(65 + (index % 26));
}
