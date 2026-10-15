// @ts-check
/**
 * Keyframe tracks: `track(keyframes)` returns a pure function of time. Values may be numbers, CSS colours
 * (interpolated in OKLCH, returned as hex), arrays of values and plain objects of values (element- and
 * key-wise). Everything is parsed and checked once, when the track is built, so evaluating it per frame
 * is cheap and allocation-light.
 *
 * Semantics:
 * - keyframes are ordered by `t` (a stable sort, so equal times keep their order);
 * - before the first keyframe the first value holds, after the last the last value holds;
 * - a keyframe's `ease` shapes the segment that ARRIVES at it (default `linear`);
 * - two keyframes at the same `t` make a jump: from that instant the later one applies;
 * - numbers follow the eased progress, overshoot included (`pop`); colours clamp it to [0, 1].
 */

import { easingFn } from './easing.js';
import { mixOklchValues, fromOklch, toOklch, parseColor } from './color.js';

/** @typedef {import('./easing.js').EasingLike} EasingLike */
/** @typedef {number | string | any[] | {[key: string]: any}} TrackValue  a number, a colour, or an array / object of those */
/**
 * @typedef {object} Keyframe
 * @property {number} t           time (any unit; usually scene-relative seconds)
 * @property {TrackValue} v       value at `t`
 * @property {EasingLike} [ease]  easing of the segment that ends at this keyframe
 */

/**
 * A compiled value: numbers stay numbers, colours become OKLCH, containers become node lists.
 * @typedef {{kind: 'num', v: number}
 *   | {kind: 'color', v: import('./color.js').Oklch, css: string}
 *   | {kind: 'arr', v: Node[]}
 *   | {kind: 'obj', keys: string[], v: Node[]}} Node
 */

/**
 * @param {TrackValue} value
 * @param {string} where  for error messages
 * @returns {Node}
 */
function compile(value, where) {
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new TypeError(`track: ${where} is not a finite number`);
    return { kind: 'num', v: value };
  }
  if (typeof value === 'string') {
    const rgba = parseColor(value);
    if (!rgba) throw new TypeError(`track: ${where} is neither a number nor a colour: ${value}`);
    const lch = toOklch(rgba);
    return { kind: 'color', v: lch, css: fromOklch(lch) };
  }
  if (Array.isArray(value)) return { kind: 'arr', v: value.map((x, i) => compile(x, `${where}[${i}]`)) };
  if (value && typeof value === 'object') {
    const keys = Object.keys(value).sort();
    return { kind: 'obj', keys, v: keys.map((k) => compile(value[k], `${where}.${k}`)) };
  }
  throw new TypeError(`track: ${where} has an unsupported type`);
}

/**
 * @param {Node} a @param {Node} b @param {string} where
 */
function checkShape(a, b, where) {
  if (a.kind !== b.kind) throw new TypeError(`track: ${where} changes type (${a.kind} -> ${b.kind})`);
  if (a.kind === 'arr' && b.kind === 'arr') {
    if (a.v.length !== b.v.length) throw new TypeError(`track: ${where} changes length`);
    a.v.forEach((x, i) => checkShape(x, b.v[i], `${where}[${i}]`));
  } else if (a.kind === 'obj' && b.kind === 'obj') {
    if (a.keys.join('\u0000') !== b.keys.join('\u0000')) throw new TypeError(`track: ${where} changes keys`);
    a.v.forEach((x, i) => checkShape(x, b.v[i], `${where}.${a.keys[i]}`));
  }
}

/**
 * The plain value of a compiled node (fresh arrays and objects every time).
 * @param {Node} n
 * @returns {TrackValue}
 */
function valueOf(n) {
  switch (n.kind) {
    case 'num':
      return n.v;
    case 'color':
      return n.css;
    case 'arr':
      return n.v.map(valueOf);
    default: {
      /** @type {{[key: string]: TrackValue}} */
      const out = {};
      n.keys.forEach((k, i) => (out[k] = valueOf(n.v[i])));
      return out;
    }
  }
}

/**
 * Interpolate two compiled nodes of the same shape.
 * @param {Node} a @param {Node} b @param {number} u  eased progress
 * @returns {TrackValue}
 */
function mixNodes(a, b, u) {
  if (a.kind === 'num' && b.kind === 'num') return a.v + (b.v - a.v) * u;
  if (a.kind === 'color' && b.kind === 'color') {
    if (u <= 0) return a.css;
    if (u >= 1) return b.css;
    return fromOklch(mixOklchValues(a.v, b.v, u));
  }
  if (a.kind === 'arr' && b.kind === 'arr') return a.v.map((x, i) => mixNodes(x, b.v[i], u));
  if (a.kind === 'obj' && b.kind === 'obj') {
    /** @type {{[key: string]: TrackValue}} */
    const out = {};
    a.keys.forEach((k, i) => (out[k] = mixNodes(a.v[i], b.v[i], u)));
    return out;
  }
  /* c8 ignore next */
  throw new TypeError('track: shape mismatch');
}

/**
 * Build a keyframe track.
 * @param {Keyframe[]} keyframes  at least one
 * @returns {(t: number) => any} value at time t (NaN counts as before the first keyframe)
 */
export function track(keyframes) {
  if (!Array.isArray(keyframes) || keyframes.length === 0) throw new TypeError('track: needs at least one keyframe');
  const frames = keyframes
    .map((k, i) => {
      if (!k || typeof k.t !== 'number' || !Number.isFinite(k.t)) throw new TypeError(`track: keyframe ${i} needs a finite t`);
      return { t: k.t, node: compile(k.v, `keyframe ${i} value`), ease: easingFn(k.ease), order: i };
    })
    .sort((x, y) => x.t - y.t || x.order - y.order);
  for (let i = 1; i < frames.length; i++) checkShape(frames[0].node, frames[i].node, `keyframe ${frames[i].order} value`);
  const times = frames.map((f) => f.t);
  const last = frames.length - 1;

  return function at(t) {
    if (!(t >= times[0])) return valueOf(frames[0].node);
    if (t >= times[last]) return valueOf(frames[last].node);
    // Largest i with times[i] <= t (binary search; equal times resolve to the later keyframe).
    let lo = 0;
    let hi = last;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (times[mid] <= t) lo = mid;
      else hi = mid;
    }
    const a = frames[lo];
    const b = frames[lo + 1];
    const u = b.ease((t - a.t) / (b.t - a.t));
    return mixNodes(a.node, b.node, u);
  };
}

/**
 * Interpolate two values of the same shape once (parses on every call: prefer `track` per frame).
 * @param {TrackValue} a @param {TrackValue} b @param {number} p  progress (not clamped for numbers)
 * @param {EasingLike} [easeName]
 * @returns {any}
 */
export function interpolate(a, b, p, easeName) {
  const na = compile(a, 'a');
  const nb = compile(b, 'b');
  checkShape(na, nb, 'b');
  return mixNodes(na, nb, easingFn(easeName)(p));
}

/**
 * Progress of `t` through the window [start, start + duration], clamped to [0, 1]. A zero or negative
 * duration is a step at `start`.
 * @param {number} t @param {number} start @param {number} duration
 * @returns {number}
 */
export function progress(t, start, duration) {
  if (!(t > start)) return 0;
  if (!(duration > 0)) return 1;
  const u = (t - start) / duration;
  return u >= 1 ? 1 : u;
}

/**
 * Start delay of item `index` of `count` in a stagger (§4.8: 60–120 ms per item or word).
 * @param {number} index
 * @param {number} count
 * @param {number} each  delay between neighbours
 * @param {{from?: 'start' | 'end' | 'center'}} [options]
 * @returns {number}
 */
export function stagger(index, count, each, { from = 'start' } = {}) {
  const n = Math.max(1, Math.floor(count));
  const i = Math.min(Math.max(0, Math.floor(index)), n - 1);
  if (from === 'end') return (n - 1 - i) * each;
  if (from === 'center') return Math.abs(i - (n - 1) / 2) * each;
  return i * each;
}
