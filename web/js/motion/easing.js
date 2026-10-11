// @ts-check
/**
 * Easing curves for the Dynamic stage (design §4.8): a cubic-bezier solver that matches CSS
 * `cubic-bezier()` (including its linear extrapolation outside [0, 1]), the motion tokens, the CSS
 * keywords and the `pop` curve. Every curve is a pure function of its input; nothing here reads a
 * clock or the DOM.
 */

/** @typedef {(x: number) => number} EasingFn */
/** @typedef {string | EasingFn} EasingLike  a name from EASINGS, a `cubic-bezier(...)` string or a function */

/** Newton steps tried before falling back to bisection. */
const NEWTON_ITERATIONS = 8;
/** Newton has converged when its step in t is below this. */
const NEWTON_STEP = 1e-10;
/** The bisection fallback stops when the bracket in t is below this. */
const BISECTION_WIDTH = 1e-10;

/**
 * A CSS `cubic-bezier(x1, y1, x2, y2)` timing function: the curve from (0, 0) to (1, 1) with the two
 * control points. x1 and x2 must lie in [0, 1] (as in CSS), which keeps x(t) monotonic; y1 and y2 may
 * overshoot. The solver finds t for x by Newton's method with a bisection fallback, accurate to about
 * 1e-9 in y (the B1 gate is 1e-4). Outside [0, 1] the curve extends along its end tangents, as Chromium does.
 * @param {number} x1
 * @param {number} y1
 * @param {number} x2
 * @param {number} y2
 * @returns {EasingFn}
 */
export function cubicBezier(x1, y1, x2, y2) {
  for (const v of [x1, y1, x2, y2]) {
    if (typeof v !== 'number' || !Number.isFinite(v)) throw new TypeError('cubicBezier: control points must be finite numbers');
  }
  if (x1 < 0 || x1 > 1 || x2 < 0 || x2 > 1) throw new RangeError('cubicBezier: x1 and x2 must lie in [0, 1]');
  if (x1 === y1 && x2 === y2) return linear;

  // Polynomial coefficients: B(t) = ((a t + b) t + c) t.
  const cx = 3 * x1;
  const bx = 3 * (x2 - x1) - cx;
  const ax = 1 - cx - bx;
  const cy = 3 * y1;
  const by = 3 * (y2 - y1) - cy;
  const ay = 1 - cy - by;
  /** @param {number} t */
  const sampleX = (t) => ((ax * t + bx) * t + cx) * t;
  /** @param {number} t */
  const sampleY = (t) => ((ay * t + by) * t + cy) * t;
  /** @param {number} t */
  const slopeX = (t) => (3 * ax * t + 2 * bx) * t + cx;

  /** @param {number} x  in (0, 1) */
  function solveT(x) {
    let t = x;
    for (let i = 0; i < NEWTON_ITERATIONS; i++) {
      const d = slopeX(t);
      if (Math.abs(d) < 1e-12) break;
      const next = t - (sampleX(t) - x) / d;
      if (!(next >= 0 && next <= 1)) break;
      if (Math.abs(next - t) < NEWTON_STEP) return next;
      t = next;
    }
    let lo = 0;
    let hi = 1;
    t = x;
    while (hi - lo > BISECTION_WIDTH) {
      if (sampleX(t) < x) lo = t;
      else hi = t;
      t = (lo + hi) / 2;
    }
    return t;
  }

  // End tangents for extrapolation (Chromium's gfx::CubicBezier).
  let startGradient = 0;
  if (x1 > 0) startGradient = y1 / x1;
  else if (y1 === 0 && x2 > 0) startGradient = y2 / x2;
  else if (y1 === 0 && y2 === 0) startGradient = 1;
  let endGradient = 0;
  if (x2 < 1) endGradient = (y2 - 1) / (x2 - 1);
  else if (y2 === 1 && x1 < 1) endGradient = (y1 - 1) / (x1 - 1);
  else if (y2 === 1 && y1 === 1) endGradient = 1;

  return function bezier(x) {
    if (Number.isNaN(x)) return 0;
    if (x <= 0) return x === 0 ? 0 : startGradient * x;
    if (x >= 1) return x === 1 ? 1 : 1 + endGradient * (x - 1);
    return sampleY(solveT(x));
  };
}

/** @type {EasingFn} */
function linear(x) {
  return Number.isNaN(x) ? 0 : x;
}

/** Where `pop` reaches its peak (fraction of the duration). */
const POP_PEAK_AT = 0.4;
/** The single overshoot of `pop` (§4.8: icons and "?" only, never text). */
export const POP_OVERSHOOT = 1.08;

/**
 * `pop`: a spring-like curve with exactly one overshoot. It rises to 1.08 at 40% of the duration with
 * a quarter-sine (fast start, zero slope at the peak), then settles to 1 along a half-cosine with zero
 * slope at the end. C1-continuous, no second oscillation, exactly 0 at 0 and 1 at 1; clamped outside.
 * @type {EasingFn}
 */
function pop(x) {
  if (!(x > 0)) return 0;
  if (x >= 1) return 1;
  if (x <= POP_PEAK_AT) return POP_OVERSHOOT * Math.sin((Math.PI / 2) * (x / POP_PEAK_AT));
  const u = (x - POP_PEAK_AT) / (1 - POP_PEAK_AT);
  return 1 + ((POP_OVERSHOOT - 1) * (1 + Math.cos(Math.PI * u))) / 2;
}

/** @type {EasingFn} CSS `step-start`: jumps at the beginning. */
function stepStart(x) {
  return x > 0 ? 1 : 0;
}

/** @type {EasingFn} CSS `step-end`: holds the start value until the end (a "hold" keyframe). */
function stepEnd(x) {
  return x >= 1 ? 1 : 0;
}

/**
 * Control points of the named cubic-bezier curves: the motion tokens of §4.8 and the CSS keywords.
 * The CSS twins of the live player use `cssEasing(name)` so both sides read the same numbers.
 * @type {Readonly<Record<string, readonly [number, number, number, number]>>}
 */
export const BEZIER_CURVES = Object.freeze({
  'ease.enter': [0, 0, 0.3, 1], // IBM Carbon expressive entrance
  'ease.exit': [0.3, 0, 0.8, 0.15], // Material 3 emphasized-accelerate
  'ease.move': [0.4, 0.14, 0.3, 1], // Carbon standard expressive
  'ease.hero': [0.05, 0.7, 0.1, 1], // Material 3 emphasized-decelerate
  ease: [0.25, 0.1, 0.25, 1],
  'ease-in': [0.42, 0, 1, 1],
  'ease-out': [0, 0, 0.58, 1],
  'ease-in-out': [0.42, 0, 0.58, 1],
});

/** @type {Record<string, EasingFn>} */
const named = { linear, pop, 'step-start': stepStart, 'step-end': stepEnd };
for (const [name, pts] of Object.entries(BEZIER_CURVES)) named[name] = cubicBezier(...pts);

/**
 * Every named easing: `linear`, `ease.enter`, `ease.exit`, `ease.move`, `ease.hero`, `pop` (the §4.8
 * tokens), the CSS keywords `ease`, `ease-in`, `ease-out`, `ease-in-out`, `step-start` and `step-end`.
 * @type {Readonly<Record<string, EasingFn>>}
 */
export const EASINGS = Object.freeze(named);

/** Parsed `cubic-bezier(...)` strings (a pure cache: the same string always gives the same curve). */
const parsedCache = new Map();
const BEZIER_RE = /^cubic-bezier\(\s*([^,\s)]+)\s*,\s*([^,\s)]+)\s*,\s*([^,\s)]+)\s*,\s*([^,\s)]+)\s*\)$/;

/**
 * The easing function for a name, a `cubic-bezier(x1, y1, x2, y2)` string or a function (returned as is).
 * Unknown names throw, so a typo fails in tests rather than silently easing linearly.
 * @param {EasingLike | null | undefined} name  null or undefined means `linear`
 * @returns {EasingFn}
 */
export function easingFn(name) {
  if (name == null) return linear;
  if (typeof name === 'function') return name;
  const fn = EASINGS[name];
  if (fn) return fn;
  const cached = parsedCache.get(name);
  if (cached) return cached;
  const m = typeof name === 'string' ? BEZIER_RE.exec(name.trim()) : null;
  if (m) {
    const pts = m.slice(1, 5).map(Number);
    const curve = cubicBezier(pts[0], pts[1], pts[2], pts[3]);
    parsedCache.set(name, curve);
    return curve;
  }
  throw new RangeError(`unknown easing: ${String(name)}`);
}

/**
 * Ease a progress value: `ease('ease.enter', 0.5)`.
 * @param {EasingLike | null | undefined} name
 * @param {number} x  progress, normally 0..1
 * @returns {number}
 */
export function ease(name, x) {
  return easingFn(name)(x);
}

/**
 * The CSS timing-function text for a named curve (for the live player's CSS twins).
 * @param {string} name
 * @returns {string}
 */
export function cssEasing(name) {
  if (name === 'linear' || name === 'step-start' || name === 'step-end') return name;
  const pts = BEZIER_CURVES[name];
  if (!pts) throw new RangeError(`no CSS form for easing: ${name}`);
  return `cubic-bezier(${pts.join(', ')})`;
}
