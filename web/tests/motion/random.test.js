// @ts-check
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { mulberry32, hashSeed, seededRandom, randomRange, randomInt, pick, shuffle } from '../../js/motion/random.js';

/** The canonical mulberry32 snippet (Tommy Ettinger / bryc), verbatim apart from formatting. */
function canonicalMulberry32(a) {
  return function () {
    let t = (a += 0x6d2b79f5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

describe('mulberry32', () => {
  test('matches the canonical snippet for 10,000 draws on several seeds', () => {
    for (const seed of [0, 1, 42, 0x7fffffff, 3126866879]) {
      const ours = mulberry32(seed);
      const ref = canonicalMulberry32(seed >>> 0);
      for (let i = 0; i < 10000; i++) assert.equal(ours(), ref(), `seed ${seed}, draw ${i}`);
    }
  });

  test('pinned vectors (also produced by an independent Python port)', () => {
    const r = mulberry32(1);
    assert.deepEqual(
      [r(), r(), r(), r(), r()],
      [0.6270739405881613, 0.002735721180215478, 0.5274470399599522, 0.9810509674716741, 0.9683778982143849],
    );
    const z = mulberry32(0);
    assert.deepEqual([z(), z(), z()], [0.26642920868471265, 0.0003297457005828619, 0.2232720274478197]);
  });

  test('values lie in [0, 1) and look uniform', () => {
    const r = mulberry32(7);
    let sum = 0;
    const buckets = new Array(10).fill(0);
    for (let i = 0; i < 100000; i++) {
      const v = r();
      assert.ok(v >= 0 && v < 1);
      sum += v;
      buckets[Math.floor(v * 10)]++;
    }
    assert.ok(Math.abs(sum / 100000 - 0.5) < 0.01);
    for (const b of buckets) assert.ok(Math.abs(b - 10000) < 500, String(buckets));
  });

  test('only the low 32 bits of the seed count', () => {
    assert.equal(mulberry32(2 ** 32 + 5)(), mulberry32(5)());
    assert.equal(mulberry32(-1)(), mulberry32(0xffffffff)());
  });

  test('independent generators do not share state', () => {
    const a = mulberry32(9);
    const b = mulberry32(9);
    a();
    a();
    const c = mulberry32(9);
    assert.equal(b(), c());
  });
});

describe('hashSeed', () => {
  test('pinned vectors (cross-checked with an independent Python port of the documented algorithm)', () => {
    assert.equal(hashSeed(1234567, 's-ohm', 'background'), 3126866879);
    assert.equal(hashSeed('தமிழ்'), 1948086186);
    assert.equal(hashSeed('a', 'bc'), 3321838905);
    assert.equal(hashSeed('ab', 'c'), 719984881);
    assert.equal(hashSeed(null), 2872998923);
    assert.equal(hashSeed(0), 1693486945);
  });

  test('unsigned 32-bit, order-sensitive, separator-safe', () => {
    const h = hashSeed('look', 's1', 'bg');
    assert.ok(Number.isInteger(h) && h >= 0 && h <= 0xffffffff);
    assert.notEqual(hashSeed('s1', 'look', 'bg'), h);
    assert.notEqual(hashSeed('a', 'bc'), hashSeed('ab', 'c'));
    assert.equal(hashSeed(12, 'x'), hashSeed('12', 'x'), 'numbers hash as their decimal text (Python str(int) agrees)');
    assert.equal(hashSeed(null), hashSeed(undefined));
  });

  test('few collisions over 50,000 scene ids', () => {
    const seen = new Set();
    for (let i = 0; i < 50000; i++) seen.add(hashSeed(987654321, `scene-${i}`, 'particles'));
    assert.ok(seen.size >= 49990, String(seen.size));
  });

  test('lone surrogates hash like U+FFFD (as a UTF-8 encoder writes them)', () => {
    assert.equal(hashSeed('\ud800'), hashSeed('�'));
  });
});

describe('helpers', () => {
  test('seededRandom = mulberry32(hashSeed(...))', () => {
    const a = seededRandom(5, 'scene', 'blobs');
    const b = mulberry32(hashSeed(5, 'scene', 'blobs'));
    for (let i = 0; i < 20; i++) assert.equal(a(), b());
  });

  test('range, int, pick and shuffle are reproducible and in bounds', () => {
    const r = seededRandom('helpers');
    for (let i = 0; i < 1000; i++) {
      const f = randomRange(r, -2, 3);
      assert.ok(f >= -2 && f < 3);
      const n = randomInt(r, 1, 6);
      assert.ok(Number.isInteger(n) && n >= 1 && n <= 6);
    }
    const items = ['a', 'b', 'c', 'd', 'e'];
    const s1 = shuffle(seededRandom('s'), items);
    const s2 = shuffle(seededRandom('s'), items);
    assert.deepEqual(s1, s2);
    assert.deepEqual([...s1].sort(), items);
    assert.deepEqual(items, ['a', 'b', 'c', 'd', 'e'], 'input untouched');
    assert.ok(items.includes(pick(seededRandom('p'), items)));
    assert.throws(() => pick(r, []), RangeError);
  });
});
