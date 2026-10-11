// @ts-check
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { ease, easingFn, cubicBezier, cssEasing, EASINGS, BEZIER_CURVES, POP_OVERSHOOT } from '../../js/motion/easing.js';

/**
 * Reference value of a cubic-bezier at x by plain bisection on t to machine precision (independent of
 * the solver under test, which uses Newton's method first).
 */
function referenceBezier(x1, y1, x2, y2, x) {
  // The ends are exact by definition (bisection cannot reach t = 1 where x(t) is flat in double precision).
  if (x <= 0) return 0;
  if (x >= 1) return 1;
  const bez = (p1, p2, t) => 3 * p1 * t * (1 - t) ** 2 + 3 * p2 * t * t * (1 - t) + t ** 3;
  let lo = 0;
  let hi = 1;
  for (let i = 0; i < 200; i++) {
    const mid = (lo + hi) / 2;
    if (bez(x1, x2, mid) < x) lo = mid;
    else hi = mid;
  }
  return bez(y1, y2, (lo + hi) / 2);
}

/** Curves for the accuracy gate: the tokens, the CSS keywords and deliberately hard ones. */
const CURVES = [
  ...Object.values(BEZIER_CURVES),
  [0, 1, 0, 1], // vertical start: dx/dt = 0 where dy/dt = 3
  [1, 0, 1, 0], // flat start, vertical end
  [0, 0, 1, 1], // linear
  [1, 1, 0, 0], // S-shaped through the middle with a vertical tangent at x = 0.5
  [0.5, -0.5, 0.5, 1.5], // overshoot both ways
  [0.1, 0.7, 1, 0.1],
  [0.68, -0.6, 0.32, 1.6], // "back" in-out
  [0.999, 0, 0.001, 1],
];

describe('cubicBezier: accuracy (B1 gate: <= 1e-4)', () => {
  test('matches a bisection reference within 1e-4 at 2,001 points on every curve', () => {
    let worst = 0;
    for (const [x1, y1, x2, y2] of CURVES) {
      const f = cubicBezier(x1, y1, x2, y2);
      for (let i = 0; i <= 2000; i++) {
        const x = i / 2000;
        const err = Math.abs(f(x) - referenceBezier(x1, y1, x2, y2, x));
        if (err > worst) worst = err;
      }
    }
    assert.ok(worst <= 1e-4, `worst error ${worst}`);
    // The solver is far inside the gate; a regression to a loose solver shows up here first.
    assert.ok(worst <= 1e-7, `worst error ${worst}`);
  });

  test('tiny inputs on a vertical start are still accurate (no absolute-x tolerance trap)', () => {
    const f = cubicBezier(0, 1, 0, 1);
    for (const x of [1e-15, 1e-12, 1e-9, 1e-6]) {
      assert.ok(Math.abs(f(x) - referenceBezier(0, 1, 0, 1, x)) <= 1e-6, `x=${x}`);
    }
  });

  test('CSS reference value: ease(0.5) = 0.8024033877', () => {
    assert.ok(Math.abs(ease('ease', 0.5) - 0.8024033877399112) < 1e-8);
  });

  test('endpoints are exact and token curves are monotonic on [0, 1]', () => {
    for (const name of ['ease.enter', 'ease.exit', 'ease.move', 'ease.hero', 'ease', 'ease-in', 'ease-out', 'ease-in-out', 'linear']) {
      const f = EASINGS[name];
      assert.equal(f(0), 0, name);
      assert.equal(f(1), 1, name);
      let prev = -Infinity;
      for (let i = 0; i <= 1000; i++) {
        const y = f(i / 1000);
        assert.ok(y >= prev - 1e-12, `${name} not monotonic at ${i / 1000}`);
        prev = y;
      }
    }
  });

  test('outside [0, 1] the curve extends along its end tangents, like Chromium', () => {
    // ease.enter = (0, 0, 0.3, 1): x1 = 0 and y1 = 0, so the start slope is y2 / x2.
    assert.ok(Math.abs(ease('ease.enter', -0.1) - -0.1 / 0.3) < 1e-12);
    // ease = (0.25, 0.1, 0.25, 1): end slope (y2 - 1) / (x2 - 1) = 0.
    assert.equal(ease('ease', 1.5), 1);
    // ease-in = (0.42, 0, 1, 1): x2 = 1 and y2 = 1, so the end slope is (y1 - 1) / (x1 - 1).
    assert.ok(Math.abs(ease('ease-in', 1.2) - (1 + (0.2 * (0 - 1)) / (0.42 - 1))) < 1e-12);
    assert.equal(cubicBezier(0.3, 0.3, 0.6, 0.6)(-2), -2); // linear shortcut
  });

  test('a curve with x(t) in [0, 1] is required, as in CSS', () => {
    assert.throws(() => cubicBezier(-0.1, 0, 0.5, 1), RangeError);
    assert.throws(() => cubicBezier(0.1, 0, 1.5, 1), RangeError);
    assert.throws(() => cubicBezier(0.1, Number.NaN, 0.5, 1), TypeError);
  });

  test('NaN progress maps to 0 (no NaN reaches a style)', () => {
    for (const name of Object.keys(EASINGS)) assert.equal(EASINGS[name](Number.NaN), 0, name);
  });
});

describe('motion tokens (§4.8)', () => {
  test('the token curves are the documented control points', () => {
    assert.deepEqual(BEZIER_CURVES['ease.enter'], [0, 0, 0.3, 1]);
    assert.deepEqual(BEZIER_CURVES['ease.exit'], [0.3, 0, 0.8, 0.15]);
    assert.deepEqual(BEZIER_CURVES['ease.move'], [0.4, 0.14, 0.3, 1]);
    assert.deepEqual(BEZIER_CURVES['ease.hero'], [0.05, 0.7, 0.1, 1]);
    assert.equal(cssEasing('ease.hero'), 'cubic-bezier(0.05, 0.7, 0.1, 1)');
    assert.equal(cssEasing('linear'), 'linear');
    assert.throws(() => cssEasing('pop'), RangeError);
  });

  test('pop: exactly one overshoot of 1.08, then a monotonic settle to exactly 1', () => {
    const pop = EASINGS.pop;
    assert.equal(POP_OVERSHOOT, 1.08);
    assert.equal(pop(0), 0);
    assert.equal(pop(1), 1);
    assert.equal(pop(-1), 0);
    assert.equal(pop(2), 1);
    let max = 0;
    let peaks = 0;
    let prev = pop(0);
    let rising = true;
    for (let i = 1; i <= 10000; i++) {
      const y = pop(i / 10000);
      assert.ok(y >= 0, 'never negative');
      if (rising && y < prev) {
        peaks++;
        rising = false;
      } else if (!rising && y > prev + 1e-15) {
        assert.fail(`second rise at ${i / 10000}`);
      }
      max = Math.max(max, y);
      prev = y;
    }
    assert.equal(peaks, 1);
    assert.ok(Math.abs(max - 1.08) < 1e-9, `peak ${max}`);
    // C1 at the peak: the slopes on both sides vanish.
    const h = 1e-6;
    assert.ok(Math.abs((pop(0.4) - pop(0.4 - h)) / h) < 1e-3);
    assert.ok(Math.abs((pop(0.4 + h) - pop(0.4)) / h) < 1e-3);
  });

  test('steps hold or jump', () => {
    assert.equal(ease('step-end', 0.999), 0);
    assert.equal(ease('step-end', 1), 1);
    assert.equal(ease('step-start', 0), 0);
    assert.equal(ease('step-start', 0.001), 1);
  });
});

describe('ease() and easingFn()', () => {
  test('names, functions, null and cubic-bezier strings', () => {
    assert.equal(ease(null, 0.25), 0.25);
    assert.equal(ease(undefined, 0.25), 0.25);
    assert.equal(ease((x) => x * x, 0.5), 0.25);
    const s = ease('cubic-bezier(0.05, 0.7, 0.1, 1)', 0.3);
    assert.equal(s, ease('ease.hero', 0.3));
    assert.equal(easingFn('cubic-bezier(0.05,0.7,0.1,1)'), easingFn('cubic-bezier(0.05,0.7,0.1,1)'), 'parsed curves are cached');
  });

  test('unknown names throw instead of easing linearly', () => {
    assert.throws(() => ease('ease.entr', 0.5), RangeError);
    assert.throws(() => ease('cubic-bezier(2, 0, 0.5, 1)', 0.5), RangeError);
  });

  test('pure: the same input always gives the same output, in any order', () => {
    const xs = Array.from({ length: 200 }, (_, i) => ((i * 37) % 200) / 199);
    const first = xs.map((x) => ease('ease.move', x));
    const again = xs
      .slice()
      .reverse()
      .map((x) => ease('ease.move', x))
      .reverse();
    assert.deepEqual(again, first);
  });
});
