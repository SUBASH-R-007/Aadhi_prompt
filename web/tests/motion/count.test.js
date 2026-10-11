// @ts-check
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { countUp, countUpValue } from '../../js/motion/count.js';
import { ease } from '../../js/motion/easing.js';

describe('countUp', () => {
  test('Indian grouping by default (lakh and crore)', () => {
    assert.equal(countUp(0, 300000, 1), '3,00,000');
    assert.equal(countUp(0, 12345678, 1), '1,23,45,678');
    assert.equal(countUp(0, 300000, 1, { locale: 'en-US' }), '300,000');
  });

  test('the ends are exact and progress is clamped', () => {
    assert.equal(countUp(15, 250, 0), '15');
    assert.equal(countUp(15, 250, 1), '250');
    assert.equal(countUp(15, 250, -3), '15');
    assert.equal(countUp(15, 250, 7), '250');
    assert.equal(countUp(15, 250, Number.NaN), '15');
  });

  test('decimals are fixed (no jitter in width)', () => {
    assert.equal(countUp(0, 9.81, 0.5, { decimals: 2 }), '4.91');
    assert.equal(countUp(0, 9.81, 1, { decimals: 2 }), '9.81');
    assert.equal(countUp(0, 9.81, 0, { decimals: 2 }), '0.00');
    assert.equal(countUp(0, 1, 0.5, { decimals: 1, locale: 'de-DE' }), '0,5');
  });

  test('never shows "-0"', () => {
    assert.equal(countUp(-1, 1, 0.5), '0');
    assert.equal(countUp(-1, 1, 0.4999, { decimals: 1 }), '0.0');
    assert.equal(countUpValue(-0.001, 0, 0), 0);
    assert.ok(!Object.is(countUpValue(-0.001, 0, 0), -0));
  });

  test('counts down too, monotonically', () => {
    let prev = Infinity;
    for (let i = 0; i <= 30; i++) {
      const v = countUpValue(1200, 0, i / 30);
      assert.ok(v <= prev);
      prev = v;
    }
    assert.equal(prev, 0);
  });

  test('every frame of a 1-second count at 30 fps is monotonic and integral', () => {
    let prev = -Infinity;
    for (let f = 0; f <= 30; f++) {
      const v = countUpValue(0, 1000, f / 30);
      assert.ok(Number.isInteger(v) && v >= prev);
      prev = v;
    }
  });

  test('an easing is applied to progress', () => {
    assert.equal(countUpValue(0, 1000, 0.5, { ease: 'ease.enter' }), Math.round(1000 * ease('ease.enter', 0.5)));
  });

  test('locale digits on request; Latin digits by default for Tamil, Hindi and Telugu', () => {
    assert.equal(countUp(0, 1234, 1, { locale: 'ta-IN' }), '1,234');
    assert.equal(countUp(0, 1234, 1, { locale: 'hi-IN' }), '1,234');
    assert.equal(countUp(0, 1234, 1, { locale: 'te-IN' }), '1,234');
    assert.equal(countUp(0, 1234, 1, { locale: 'hi-IN', numberingSystem: 'deva' }), '१,२३४');
    assert.equal(countUp(0, 1234, 1, { locale: 'ta-IN', numberingSystem: 'tamldec' }), '௧,௨௩௪');
  });

  test('grouping can be switched off; a bad locale falls back to en-IN', () => {
    assert.equal(countUp(0, 2025, 1, { useGrouping: false }), '2025');
    assert.equal(countUp(0, 100000, 1, { locale: 'not a locale!' }), '1,00,000');
  });
});
