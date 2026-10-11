// @ts-check
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { strokeDash, strokeSegment, staggerProgress } from '../../js/motion/draw.js';

/**
 * Visible stretches [a, b] of a path of length `len` under a dash pattern (dash, gap) and offset, as an
 * SVG renderer lays them out: the pattern starts at -offset and repeats.
 */
function visibleStretches(len, dasharray, dashoffset) {
  const [dash, gap] = dasharray.split(' ').map(Number);
  const period = dash + gap;
  const out = [];
  let start = -Number(dashoffset);
  while (start > 0) start -= period;
  for (; start < len; start += period) {
    const a = Math.max(0, start);
    const b = Math.min(len, start + dash);
    if (b > a) out.push([a, b]);
  }
  return out;
}

describe('strokeDash', () => {
  test('draws the first p of the path and nothing else', () => {
    for (const p of [0.1, 0.25, 0.5, 0.9]) {
      const d = strokeDash(200, p);
      const vis = visibleStretches(200, d.dasharray, d.dashoffset);
      assert.equal(vis.length, 1, `p=${p}`);
      assert.equal(vis[0][0], 0);
      assert.ok(Math.abs(vis[0][1] - 200 * p) <= 200 * 0.01 * p + 1e-3, `p=${p}: ${vis[0][1]}`);
      assert.equal(d.visible, true);
    }
  });

  test('p = 1 covers the whole path even if the measured length is a hair short', () => {
    const d = strokeDash(200, 1);
    assert.deepEqual(visibleStretches(200.8, d.dasharray, d.dashoffset), [[0, 200.8]]);
  });

  test('p = 0 hides the path (no round-cap dot) and p is clamped', () => {
    assert.equal(strokeDash(200, 0).visible, false);
    assert.equal(strokeDash(200, -1).visible, false);
    assert.deepEqual(strokeDash(200, 2), strokeDash(200, 1));
    assert.equal(strokeDash(200, Number.NaN).visible, false);
  });

  test('reverse draws from the end', () => {
    const d = strokeDash(100, 0.3, { reverse: true });
    const vis = visibleStretches(100, d.dasharray, d.dashoffset);
    assert.equal(vis.length, 1);
    assert.ok(Math.abs(vis[0][0] - 70) <= 1, String(vis[0][0]));
    assert.equal(vis[0][1], 100);
  });

  test('works with pathLength="1" and with a degenerate path', () => {
    const d = strokeDash(1, 0.5);
    const vis = visibleStretches(1, d.dasharray, d.dashoffset);
    assert.ok(Math.abs(vis[0][1] - 0.5) <= 0.01);
    assert.deepEqual(strokeDash(0, 0.5), { dasharray: 'none', dashoffset: '0', visible: true });
    assert.deepEqual(strokeDash(Number.NaN, 0), { dasharray: 'none', dashoffset: '0', visible: false });
  });

  test('numbers are rounded strings without exponents or negative zero', () => {
    const d = strokeDash(123.456789, 1 / 3);
    assert.match(d.dasharray, /^\d+(\.\d{1,3})? \d+(\.\d{1,3})?$/);
    assert.match(d.dashoffset, /^-?\d+(\.\d{1,3})?$/);
    assert.equal(strokeDash(1e-9, 0.5).dashoffset, '0');
    assert.equal(strokeDash(100, 1, { reverse: true }).dashoffset, '0');
  });
});

describe('strokeSegment', () => {
  test('shows only [from, to]', () => {
    const d = strokeSegment(100, 0.2, 0.4);
    const vis = visibleStretches(100, d.dasharray, d.dashoffset);
    assert.equal(vis.length, 1);
    assert.ok(Math.abs(vis[0][0] - 20) <= 0.5 && Math.abs(vis[0][1] - 40) <= 0.5, JSON.stringify(vis));
    assert.deepEqual(strokeSegment(100, 0.4, 0.2), d, 'order of the ends does not matter');
    assert.equal(strokeSegment(100, 0.5, 0.5).visible, false);
  });
});

describe('staggerProgress', () => {
  test('items draw in turn and all finish at p = 1', () => {
    assert.deepEqual(staggerProgress(0, 3), [0, 0, 0]);
    assert.deepEqual(staggerProgress(1, 3), [1, 1, 1]);
    const strict = staggerProgress(0.5, 2, 0);
    assert.deepEqual(strict, [1, 0]);
    const mid = staggerProgress(0.5, 3, 0.3);
    assert.ok(mid[0] > mid[1] && mid[1] > mid[2], JSON.stringify(mid));
  });

  test('overlap 1 draws everything together; edge counts', () => {
    assert.deepEqual(staggerProgress(0.4, 3, 1), [0.4, 0.4, 0.4]);
    assert.deepEqual(staggerProgress(0.4, 0), []);
    assert.deepEqual(staggerProgress(0.4, 1), [0.4]);
  });
});
