// @ts-check
/**
 * Pure maths for the graph panel: expression allow-list (mirror of
 * aadhi/schemas/screenplay.py `_check_expr`), a hardened math.js instance (per the math.js
 * security guidance), adaptive sampling, automatic y-range and nice ticks. No DOM access.
 */

/** Identifiers allowed in graph expressions (keep in sync with screenplay.py `_EXPR_FUNCS`). */
export const EXPR_FUNCS = new Set([
  'sin', 'cos', 'tan', 'asin', 'acos', 'atan', 'sinh', 'cosh', 'tanh', 'exp', 'log', 'log10', 'log2',
  'sqrt', 'cbrt', 'abs', 'sign', 'floor', 'ceil', 'round', 'min', 'max', 'pow', 'mod', 'pi', 'e', 'x',
]);

/** math.js functions replaced by throwing stubs (anything that parses strings or extends the instance). */
export const DISABLED_FUNCTIONS = Object.freeze([
  'import', 'createUnit', 'evaluate', 'parse', 'simplify', 'simplifyConstant', 'simplifyCore', 'derivative',
  'resolve', 'rationalize', 'symbolicEqual', 'leafCount', 'parser', 'compile', 'reviver', 'help',
]);

export const MAX_EXPR_LENGTH = 200;

/** ASCII-only grammar (same tokens as screenplay.py `_EXPR_TOKEN`), applied after normalizeExpr. */
const TOKEN = /[ ]*(?:([0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?|\.[0-9]+)|([A-Za-z_][A-Za-z0-9_]*)|(\*\*|[-+*/^(),.]))/y;

/**
 * Whitespace accepted by either validator: JS `\s` plus what Python's `\s` adds (`str.isspace`:
 * U+001C-U+001F, U+0085). math.js itself only understands ' ' and '\t'.
 */
const ANY_WHITESPACE = /[\s\u001c-\u001f\u0085]/g;
/** Unicode decimal digits (category Nd): Python's `\d` (screenplay.py) accepts them. */
const UNICODE_DIGIT = /\p{Nd}/gu;
const IS_DIGIT = /^\p{Nd}$/u;

/**
 * Numeric value of a Unicode decimal digit. Nd characters are always encoded in contiguous runs
 * of ten (0-9, a Unicode stability guarantee), so the value is the offset from the start of the
 * stretch of consecutive Nd code points, modulo 10.
 * @param {string} ch  one code point of category Nd
 */
export function digitValue(ch) {
  const cp = /** @type {number} */ (ch.codePointAt(0));
  let start = cp;
  while (start > 0 && IS_DIGIT.test(String.fromCodePoint(start - 1))) start--;
  return (cp - start) % 10;
}

/**
 * Map what the backend validator accepts onto the ASCII grammar math.js parses: every whitespace
 * character becomes ' ', Unicode digits become ASCII digits, surrounding spaces are trimmed
 * (pydantic strips them too). Anything else is left for checkExpr to reject.
 * @param {string} expr
 */
export function normalizeExpr(expr) {
  return expr
    .replace(ANY_WHITESPACE, ' ')
    .replace(UNICODE_DIGIT, (ch) => (ch >= '0' && ch <= '9' ? ch : String(digitValue(ch))))
    .trim();
}

export class ExprError extends Error {
  /** @param {string} message */
  constructor(message) {
    super(message);
    this.name = 'ExprError';
  }
}

/**
 * Validate an expression against the allow-list. Returns the normalised expression (see
 * normalizeExpr) with `**` rewritten to `^`: exactly the string that was checked is compiled.
 * Accepts everything screenplay.py `_check_expr` accepts (a superset only for exotic whitespace).
 * @param {unknown} input
 * @returns {string}
 * @throws {ExprError}
 */
export function checkExpr(input) {
  if (typeof input !== 'string') throw new ExprError('expression must be a string');
  const expr = normalizeExpr(input);
  if (!expr) throw new ExprError('expression is empty');
  if (Array.from(expr).length > MAX_EXPR_LENGTH) throw new ExprError('expression is too long');
  let pos = 0;
  while (pos < expr.length) {
    TOKEN.lastIndex = pos;
    const m = TOKEN.exec(expr);
    if (!m || TOKEN.lastIndex === pos) {
      if (expr.slice(pos).trim() === '') break;
      throw new ExprError(`unsupported character in expression at ${pos}`);
    }
    if (m[2] && !EXPR_FUNCS.has(m[2])) throw new ExprError(`unknown identifier "${m[2]}" (use x and math functions)`);
    pos = TOKEN.lastIndex;
  }
  return expr.replace(/\*\*/g, '^');
}

/** @typedef {{ compileExpr: (expr: string) => (x: number) => number, math: any }} SafeMath */

/** @type {WeakMap<object, SafeMath>} */
const safeCache = new WeakMap();

/** Marks our throwing stubs (detects an instance that was already hardened elsewhere). */
const DISABLED_MARK = '__aadhiDisabled';

/**
 * Hardened math.js instance (math.js security guidance): a `predictable` instance, the original
 * `compile` captured, then every string-parsing / extending function overridden to throw.
 * Expressions are re-checked against the allow-list before compiling. Cached per library object.
 *
 * - npm/ESM build (has `create` + `all` factories): a private `create(all, {predictable: true})`.
 * - browser UMD build (web/vendor/mathjs/math.js exposes only the default instance, no `all`):
 *   the default `window.math` instance itself is configured and hardened in place. Nothing else
 *   in the app uses math.js; any later user of `window.math` gets the hardened instance.
 * @param {any} mathLib  the math.js namespace (window.math or the npm module)
 * @returns {SafeMath}
 */
export function createSafeMath(mathLib) {
  if (!mathLib || (typeof mathLib !== 'object' && typeof mathLib !== 'function')) throw new Error('math.js is not available');
  const cached = safeCache.get(mathLib);
  if (cached) return cached;
  /** @type {any} */
  let math;
  if (typeof mathLib.create === 'function' && mathLib.all) {
    math = mathLib.create(mathLib.all, { predictable: true });
  } else if (typeof mathLib.compile === 'function' && typeof mathLib.import === 'function' && typeof mathLib.config === 'function') {
    math = mathLib;
    math.config({ predictable: true });
  } else {
    throw new Error('math.js is not available');
  }
  const compile = math.compile;
  if (compile && compile[DISABLED_MARK]) throw new Error('math.js instance was already hardened by another copy');
  /** @type {Record<string, () => never>} */
  const stubs = {};
  for (const name of DISABLED_FUNCTIONS) {
    const stub = function disabled() {
      throw new Error(`Function ${name} is disabled`);
    };
    Object.defineProperty(stub, DISABLED_MARK, { value: true });
    stubs[name] = stub;
  }
  math.import(stubs, { override: true });
  const safe = {
    math,
    /** @param {string} expr */
    compileExpr(expr) {
      const code = compile(checkExpr(expr));
      return (/** @type {number} */ x) => toNumber(code.evaluate({ x }));
    },
  };
  safeCache.set(mathLib, safe);
  return safe;
}

/**
 * math.js result -> finite number or NaN.
 * @param {unknown} v
 */
export function toNumber(v) {
  if (typeof v === 'number') return Number.isFinite(v) ? v : NaN;
  if (v && typeof v === 'object') {
    const c = /** @type {{ re?: unknown, im?: unknown }} */ (v);
    if (typeof c.re === 'number' && typeof c.im === 'number' && Math.abs(c.im) < 1e-9) return toNumber(c.re);
  }
  return NaN;
}

/**
 * @param {number[]} sorted
 * @param {number} q
 */
export function quantile(sorted, q) {
  if (!sorted.length) return NaN;
  const i = (sorted.length - 1) * q;
  const lo = Math.floor(i);
  const hi = Math.ceil(i);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
}

/** @typedef {{ x: number, y: number }} Pt */

/**
 * Adaptive sampling: a uniform pass, then recursive bisection where the midpoint departs from
 * the chord (curvature) or where the function becomes undefined. y is NaN where undefined.
 * @param {(x: number) => number} f
 * @param {number} x0
 * @param {number} x1
 * @param {{ initial?: number, maxDepth?: number, maxPoints?: number, tolerance?: number }} [opts]
 * @returns {{ points: Pt[], uniform: number[], minDx: number }}
 */
export function sampleFunction(f, x0, x1, opts = {}) {
  const n = Math.max(2, opts.initial ?? 160);
  const maxDepth = Math.max(0, opts.maxDepth ?? 7);
  let budget = Math.max(n + 1, opts.maxPoints ?? 3000) - (n + 1);
  /** @param {number} x */
  const at = (x) => {
    let y;
    try {
      y = f(x);
    } catch {
      y = NaN;
    }
    return Number.isFinite(y) ? y : NaN;
  };
  /** @type {Pt[]} */
  const base = [];
  for (let i = 0; i <= n; i++) {
    const x = i === n ? x1 : x0 + ((x1 - x0) * i) / n;
    base.push({ x, y: at(x) });
  }
  const finite = base.map((p) => p.y).filter(Number.isFinite).sort((a, b) => a - b);
  const span = finite.length ? quantile(finite, 0.95) - quantile(finite, 0.05) : 0;
  const tol = (opts.tolerance ?? 0.002) * (span > 0 ? span : 1);
  /** @type {Pt[]} */
  const out = [base[0]];
  /**
   * @param {Pt} a
   * @param {Pt} b
   * @param {number} depth
   */
  const refine = (a, b, depth) => {
    if (depth >= maxDepth || budget <= 0) {
      out.push(b);
      return;
    }
    const m = { x: (a.x + b.x) / 2, y: at((a.x + b.x) / 2) };
    budget--;
    const fa = Number.isFinite(a.y);
    const fb = Number.isFinite(b.y);
    const fm = Number.isFinite(m.y);
    const split = fa !== fb || fa !== fm || (fa && fb && fm && Math.abs(m.y - (a.y + b.y) / 2) > tol);
    if (split) {
      refine(a, m, depth + 1);
      refine(m, b, depth + 1);
    } else {
      out.push(m, b);
    }
  };
  for (let i = 0; i < n; i++) refine(base[i], base[i + 1], 0);
  return { points: out, uniform: base.map((p) => p.y), minDx: (x1 - x0) / n / 2 ** maxDepth };
}

/**
 * Split samples into drawable polylines: breaks at undefined values, at asymptotes (consecutive
 * samples off-range on opposite sides) and at jumps that bisection could not resolve.
 * @param {Pt[]} points
 * @param {[number, number]} yRange
 * @param {number} minDx
 * @returns {Pt[][]}
 */
export function splitSegments(points, yRange, minDx) {
  const [lo, hi] = yRange;
  const span = hi - lo;
  /** @type {Pt[][]} */
  const segs = [];
  /** @type {Pt[]} */
  let cur = [];
  for (let i = 0; i < points.length; i++) {
    const p = points[i];
    if (!Number.isFinite(p.y)) {
      if (cur.length) segs.push(cur);
      cur = [];
      continue;
    }
    const prev = cur[cur.length - 1];
    if (prev) {
      const opposite = (prev.y > hi && p.y < lo) || (prev.y < lo && p.y > hi);
      const unresolvedJump = p.x - prev.x <= minDx * 1.01 && Math.abs(p.y - prev.y) > span * 0.1;
      if (opposite || unresolvedJump) {
        segs.push(cur);
        cur = [];
      }
    }
    cur.push(p);
  }
  if (cur.length) segs.push(cur);
  return segs.filter((s) => s.length > 0);
}

/**
 * Automatic y-range: robust to asymptote spikes (5-95 % quantiles when the extremes dominate),
 * includes the marked points, snaps to 0 when close, pads 8 %.
 * @param {number[]} values  uniformly sampled function values (NaN allowed)
 * @param {number[]} [extra]  y values that must be visible (points)
 * @returns {[number, number]}
 */
export function autoYRange(values, extra = []) {
  const ys = values.filter(Number.isFinite).sort((a, b) => a - b);
  const pts = extra.filter(Number.isFinite);
  let lo = Infinity;
  let hi = -Infinity;
  if (ys.length) {
    lo = ys[0];
    hi = ys[ys.length - 1];
    const q1 = quantile(ys, 0.05);
    const q2 = quantile(ys, 0.95);
    if (q2 - q1 > 0 && hi - lo > (q2 - q1) * 4) {
      lo = q1;
      hi = q2;
    }
  }
  for (const p of pts) {
    lo = Math.min(lo, p);
    hi = Math.max(hi, p);
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [-1, 1];
  let span = hi - lo;
  if (span < 1e-9) {
    const d = Math.max(1, Math.abs(lo) * 0.5);
    lo -= d;
    hi += d;
    span = hi - lo;
  }
  if (lo > 0 && lo < span * 0.25) lo = 0;
  if (hi < 0 && -hi < span * 0.25) hi = 0;
  span = hi - lo;
  return [lo - span * 0.08, hi + span * 0.08];
}

/**
 * "Nice" tick values covering [lo, hi] (steps of 1, 2, 2.5, 5 x 10^k).
 * @param {number} lo
 * @param {number} hi
 * @param {number} [target] approximate number of ticks
 * @returns {{ ticks: number[], step: number }}
 */
export function niceTicks(lo, hi, target = 6) {
  if (!(hi > lo) || !Number.isFinite(lo) || !Number.isFinite(hi)) return { ticks: [], step: 0 };
  const raw = (hi - lo) / Math.max(1, target - 1);
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
  /** @type {number[]} */
  const ticks = [];
  const first = Math.ceil(lo / step - 1e-9);
  const last = Math.floor(hi / step + 1e-9);
  for (let k = first; k <= last && ticks.length < 50; k++) ticks.push(Math.abs(k * step) < step * 1e-9 ? 0 : k * step);
  return { ticks, step };
}

/**
 * Decimal places needed to print multiples of `step` exactly (0.25 -> 2, 2.5 -> 1, 5 -> 0).
 * @param {number} step
 */
export function decimalsOf(step) {
  if (!(step > 0) || !Number.isFinite(step)) return 0;
  const s = String(Number(step.toPrecision(6)));
  if (s.includes('e-')) return Math.min(10, Number(s.split('e-')[1]) + (s.split('e')[0].split('.')[1] || '').length);
  const dot = s.indexOf('.');
  return dot < 0 ? 0 : Math.min(10, s.length - dot - 1);
}

/**
 * Tick label with just enough decimals for the step; typographic minus sign.
 * @param {number} v
 * @param {number} step
 */
export function formatTick(v, step) {
  const a = Math.abs(v);
  let s;
  if ((a >= 1e5 || (a > 0 && a < 1e-4)) && a !== 0) s = v.toExponential(1);
  else {
    s = v.toFixed(decimalsOf(step));
    if (s.includes('.')) s = s.replace(/0+$/, '').replace(/\.$/, '');
  }
  if (/^-0(\.0*)?$/.test(s)) s = '0';
  return s.replace(/^-/, '−');
}
