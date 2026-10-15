// @ts-check
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { track, interpolate, progress, stagger } from '../../js/motion/track.js';
import { ease } from '../../js/motion/easing.js';
import { mixOklch } from '../../js/motion/color.js';

const close = (a, b, eps = 1e-12) => assert.ok(Math.abs(a - b) <= eps, `${a} != ${b}`);

describe('track: numbers', () => {
  const opacity = track([
    { t: 0.5, v: 0 },
    { t: 1.0, v: 1, ease: 'ease.enter' },
    { t: 3.0, v: 1 },
    { t: 3.4, v: 0, ease: 'ease.exit' },
  ]);

  test('holds the first value before and the last value after', () => {
    assert.equal(opacity(-10), 0);
    assert.equal(opacity(0.5), 0);
    assert.equal(opacity(3.4), 0);
    assert.equal(opacity(99), 0);
  });

  test("a keyframe's ease shapes the segment that arrives at it", () => {
    close(opacity(0.75), ease('ease.enter', 0.5));
    close(opacity(2), 1);
    close(opacity(3.2), 1 - ease('ease.exit', 0.5));
  });

  test('keyframes are sorted by time (stable for equal times)', () => {
    const tr = track([
      { t: 2, v: 20 },
      { t: 0, v: 0 },
      { t: 1, v: 10 },
    ]);
    assert.equal(tr(0.5), 5);
    assert.equal(tr(1.5), 15);
  });

  test('two keyframes at the same time make a jump', () => {
    const tr = track([
      { t: 0, v: 0 },
      { t: 1, v: 10 },
      { t: 1, v: 50 },
      { t: 2, v: 60 },
    ]);
    close(tr(0.999999), 9.99999, 1e-9);
    assert.equal(tr(1), 50);
    assert.equal(tr(1.5), 55);
  });

  test('numbers keep an easing overshoot (pop); NaN time holds the first value', () => {
    const scale = track([
      { t: 0, v: 0 },
      { t: 1, v: 1, ease: 'pop' },
    ]);
    close(scale(0.4), 1.08);
    assert.equal(scale(Number.NaN), 0);
  });

  test('a single keyframe is a constant', () => {
    const tr = track([{ t: 3, v: 7 }]);
    assert.equal(tr(0), 7);
    assert.equal(tr(3), 7);
    assert.equal(tr(10), 7);
  });

  test('pure: evaluation order does not matter (frames may be requested in any order)', () => {
    const tr = track([
      { t: 0, v: [0, '#102030'] },
      { t: 1, v: [100, '#f0c040'], ease: 'ease.move' },
      { t: 2, v: [50, '#2050f0'], ease: 'ease.hero' },
    ]);
    const frames = Array.from({ length: 61 }, (_, i) => i / 30);
    const forward = frames.map((t) => JSON.stringify(tr(t)));
    const reverse = frames
      .slice()
      .reverse()
      .map((t) => JSON.stringify(tr(t)))
      .reverse();
    const shuffled = frames.map((_, i) => (i * 17) % frames.length);
    const byIndex = new Map(shuffled.map((i) => [i, JSON.stringify(tr(frames[i]))]));
    assert.deepEqual(reverse, forward);
    assert.deepEqual(frames.map((_, i) => byIndex.get(i)), forward);
  });
});

describe('track: arrays, objects and colours', () => {
  test('arrays interpolate element-wise and come back as fresh arrays', () => {
    const tr = track([
      { t: 0, v: [0, 100] },
      { t: 1, v: [10, 0] },
    ]);
    assert.deepEqual(tr(0.5), [5, 50]);
    const a = tr(0);
    a[0] = 999;
    assert.deepEqual(tr(0), [0, 100], 'callers cannot mutate the keyframes');
  });

  test('objects interpolate key-wise', () => {
    const tr = track([
      { t: 0, v: { x: 0, y: 10, fill: '#000000' } },
      { t: 2, v: { y: 20, x: 4, fill: '#ffffff' } },
    ]);
    assert.deepEqual(tr(1), { fill: mixOklch('#000000', '#ffffff', 0.5), x: 2, y: 15 });
  });

  test('colours interpolate in OKLCH and return hex; endpoints are the normalised inputs', () => {
    const tr = track([
      { t: 0, v: '#FF0000' },
      { t: 1, v: 'rgb(0 0 255)' },
    ]);
    assert.equal(tr(0), '#ff0000');
    assert.equal(tr(1), '#0000ff');
    assert.equal(tr(0.5), mixOklch('#ff0000', '#0000ff', 0.5));
    assert.match(tr(0.25), /^#[0-9a-f]{6}$/);
  });

  test('colour progress is clamped even with an overshooting ease', () => {
    const tr = track([
      { t: 0, v: '#000000' },
      { t: 1, v: '#ffffff', ease: 'pop' },
    ]);
    assert.equal(tr(0.4), '#ffffff');
  });

  test('shape changes and bad values are rejected when the track is built', () => {
    assert.throws(() => track([]), TypeError);
    assert.throws(() => track([{ t: 0, v: 1 }, { t: 1, v: '#fff' }]), /changes type/);
    assert.throws(() => track([{ t: 0, v: [1, 2] }, { t: 1, v: [1] }]), /changes length/);
    assert.throws(() => track([{ t: 0, v: { a: 1 } }, { t: 1, v: { b: 1 } }]), /changes keys/);
    assert.throws(() => track([{ t: 0, v: '12px' }]), /neither a number nor a colour/);
    assert.throws(() => track([{ t: Number.NaN, v: 1 }]), /finite t/);
    assert.throws(() => track([{ t: 0, v: Number.POSITIVE_INFINITY }]), /finite number/);
    assert.throws(() => track([{ t: 0, v: 0 }, { t: 1, v: 1, ease: 'bouncy' }]), RangeError);
  });
});

describe('interpolate, progress and stagger', () => {
  test('interpolate mixes two values once', () => {
    assert.equal(interpolate(0, 10, 0.25), 2.5);
    close(interpolate(0, 10, 0.5, 'ease.enter'), 10 * ease('ease.enter', 0.5));
    assert.deepEqual(interpolate([0, 0], [2, 4], 0.5), [1, 2]);
  });

  test('progress is clamped and a zero duration is a step', () => {
    assert.equal(progress(0, 1, 2), 0);
    assert.equal(progress(2, 1, 2), 0.5);
    assert.equal(progress(5, 1, 2), 1);
    assert.equal(progress(1, 1, 0), 0);
    assert.equal(progress(1.0001, 1, 0), 1);
    assert.equal(progress(Number.NaN, 1, 2), 0);
  });

  test('stagger delays from the start, the end or the centre', () => {
    assert.deepEqual([0, 1, 2, 3].map((i) => stagger(i, 4, 0.08)).map((x) => +x.toFixed(6)), [0, 0.08, 0.16, 0.24]);
    assert.deepEqual([0, 1, 2, 3].map((i) => stagger(i, 4, 0.1, { from: 'end' })).map((x) => +x.toFixed(6)), [0.3, 0.2, 0.1, 0]);
    assert.deepEqual([0, 1, 2].map((i) => stagger(i, 3, 0.1, { from: 'center' })), [0.1, 0, 0.1]);
    assert.equal(stagger(9, 3, 0.1), 0.2, 'index clamped to the last item');
  });
});
