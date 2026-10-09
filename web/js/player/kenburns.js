// @ts-check
/**
 * Ken Burns pan/zoom as a pure function of time (MediaRef.ken_burns): linear interpolation from
 * `start` to `end` over [0, duration]. The visible window of the image is centred on (cx, cy) with
 * size 1/scale; the centre is clamped so the window never leaves the image. ffmpeg's zoompan in
 * the MP4 renderer reads the same spec.
 */

import { clamp, lerp, num } from '../shared/format.js';

/**
 * @typedef {object} KenBurnsFrame
 * @property {number} scale
 * @property {number} cx      clamped focus centre (0..1)
 * @property {number} cy
 * @property {number} tx      translate x in % of the element size (transform-origin 0 0)
 * @property {number} ty
 */

/**
 * @param {import('../shared/types.js').KenBurns | null | undefined} kb
 * @param {number} t          scene-relative seconds
 * @param {number} duration   scene duration
 * @returns {KenBurnsFrame}
 */
export function kenBurnsAt(kb, t, duration) {
  const s0 = kb?.start || {};
  const s1 = kb?.end || {};
  const u = duration > 0 ? clamp(t / duration, 0, 1) : 0;
  const scale = Math.max(1, lerp(num(s0.scale, 1), num(s1.scale, 1.15), u));
  const half = 0.5 / scale;
  const cx = clamp(lerp(num(s0.cx, 0.5), num(s1.cx, 0.5), u), half, 1 - half);
  const cy = clamp(lerp(num(s0.cy, 0.5), num(s1.cy, 0.5), u), half, 1 - half);
  return { scale, cx, cy, tx: (0.5 - scale * cx) * 100, ty: (0.5 - scale * cy) * 100 };
}

/**
 * CSS transform for a frame (apply with transform-origin: 0 0).
 * @param {KenBurnsFrame} f
 */
export function kenBurnsTransform(f) {
  return `translate(${f.tx.toFixed(4)}%, ${f.ty.toFixed(4)}%) scale(${f.scale.toFixed(5)})`;
}
