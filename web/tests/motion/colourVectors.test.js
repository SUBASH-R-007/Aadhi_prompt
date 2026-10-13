// @ts-check
/**
 * Python/JS colour parity (DYNAMIC_VIDEO_DESIGN §4.7, B2 plan §1.2): tests/fixtures/colour_vectors.json must be exactly
 * what scripts/gen_colour_vectors.mjs computes from the stage's colour code today (web/js/motion/color.js and the
 * colour helpers of web/js/dynamic). Its Python twin, tests/compose/dynamic/test_colour_math.py, checks that
 * aadhi/compose/dynamic/colour_math.py and themes.py reproduce every vector; so a change on either side fails one of
 * the two until the file is regenerated (`node scripts/gen_colour_vectors.mjs`) and both agree.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { OUT, buildVectors, serialise } from '../../../scripts/gen_colour_vectors.mjs';
import { fromOklch, mixOklch } from '../../js/motion/color.js';

test('the committed colour vectors are what the stage code produces', () => {
  const committed = readFileSync(OUT, 'utf8');
  const fresh = serialise(buildVectors());
  if (committed !== fresh) {
    const a = committed.split('\n');
    const b = fresh.split('\n');
    const i = a.findIndex((line, k) => line !== b[k]);
    assert.fail(`tests/fixtures/colour_vectors.json is stale (first difference at line ${i + 1}):\n  committed: ${a[i]}\n  fresh:     ${b[i]}\nrun: node scripts/gen_colour_vectors.mjs`);
  }
});

test('the vectors cover what the parity is for', () => {
  const v = JSON.parse(readFileSync(OUT, 'utf8'));
  for (const key of ['hex_oklch', 'from_oklch', 'mix', 'contrast', 'composite', 'desaturate', 'stage']) {
    assert.ok(Array.isArray(v[key]) && v[key].length > 0, key);
  }
  // out-of-gamut accents are in (the gamut mapping's search branch runs), and so are translucent mixes
  assert.ok(v.from_oklch.some((r) => r.rgb.some((c) => c === 0 || c === 1) && r.l > 0 && r.l < 1));
  assert.ok(v.mix.some((r) => r.hex.length === 9));
  assert.equal(v.stage.length, 12, 'the four R1 families x palette slots 0-2');
});

test('the serialisation is canonical and the checker is sensitive', () => {
  const vectors = buildVectors();
  const text = serialise(vectors);
  assert.equal(serialise(JSON.parse(text)), text, 'parsing and re-serialising is the identity');
  // a one-unit change in a single colour is a different file (the test above can fail)
  const changed = structuredClone(vectors);
  changed.stage[0].block.base = changed.stage[0].block.base === '#000000' ? '#000001' : '#000000';
  assert.notEqual(serialise(changed), text);
  // spot values: color.js itself (the Python side pins the same ones)
  assert.equal(fromOklch({ l: 0.62796, c: 0.25768, h: 29.234 }), '#ff0000');
  assert.equal(mixOklch('#000000', '#FFFFFF', 0.5), '#636363');
});
