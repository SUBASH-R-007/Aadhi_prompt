// @ts-check
// The motion toolkit's public surface (the B1 module contract) and its purity: it imports in plain
// Node, where there is no window or document, and it never touches the DOM at import time.
import { test } from 'node:test';
import assert from 'node:assert/strict';

test('imports without any DOM global', async () => {
  assert.equal(typeof globalThis.window, 'undefined');
  assert.equal(typeof globalThis.document, 'undefined');
  const motion = await import('../../js/motion/index.js');
  assert.ok(motion);
});

test('exports the module contract', async () => {
  const motion = await import('../../js/motion/index.js');
  const contract = {
    'easing.js': ['ease', 'cubicBezier', 'EASINGS'],
    'track.js': ['track'],
    'color.js': ['parseColor', 'toOklch', 'fromOklch', 'mixOklch', 'contrastRatio'],
    'text.js': ['segmentGraphemes', 'splitWords', 'splitLines'],
    'draw.js': ['strokeDash'],
    'random.js': ['mulberry32', 'hashSeed'],
    'count.js': ['countUp'],
  };
  for (const [file, names] of Object.entries(contract)) {
    const mod = await import(`../../js/motion/${file}`);
    for (const name of names) {
      assert.ok(name in mod, `${file} exports ${name}`);
      assert.equal(motion[name], mod[name], `index.js re-exports ${name} from ${file}`);
    }
  }
  assert.equal(typeof motion.EASINGS, 'object');
  for (const [name, value] of Object.entries(motion)) {
    if (name === 'EASINGS' || name === 'BEZIER_CURVES') assert.ok(Object.isFrozen(value), `${name} is frozen`);
  }
});
