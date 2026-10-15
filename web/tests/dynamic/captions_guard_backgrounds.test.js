import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom } from '../player/_dom.js';
import { CAPTION_INK, CAPTION_PILL, CaptionLayer, captionStyle } from '../../js/dynamic/captions.js';
import { GUARD_MESSAGE, GuardError, installTimerGuard } from '../../js/dynamic/guard.js';
import meshDrift, { AMPLITUDE_MAX, DRIFT_STEP, PERIOD_MIN, blobAt, blobPaths } from '../../js/dynamic/backgrounds/mesh_drift.js';
import colourBlock, { blockColor, grainTile } from '../../js/dynamic/backgrounds/colour_block.js';
import { Painter, ZONES, makeRoles } from '../../js/dynamic/core.js';
import { toOklch } from '../../js/motion/color.js';
import { dynamicTimeline } from './_fixture.js';

before(() => installDom());
after(() => uninstallDom());

// --- captions ------------------------------------------------------------------------------------------------

test('caption style: the presentation choice, else Crisp reels get bold_outline', () => {
  assert.equal(captionStyle({ caption_style: 'bold_outline' }, 'landscape'), 'bold_outline');
  assert.equal(captionStyle({ caption_style: 'marker', personality: 'crisp' }, 'portrait'), 'bold_outline');
  assert.equal(captionStyle({ caption_style: 'marker', personality: 'soft' }, 'portrait'), 'clean_pill');
  assert.equal(captionStyle(null, 'landscape'), 'clean_pill');
});

test('CaptionLayer: one fixed band, lines as text, key words marked, hidden when blank or off', () => {
  const host = document.createElement('div');
  const layer = new CaptionLayer(host, { zones: ZONES.portrait, style: 'bold_outline', locale: 'en' });
  assert.equal(layer.el.style.left, '120px');
  assert.equal(layer.el.style.top, '1040px');
  assert.equal(layer.el.style.width, '660px');
  const p = new Painter();
  p.begin();
  layer.update(p, { lines: ['Watch the', 'current flow'], keys: new Set(['current']), id: 'k1' });
  assert.equal(layer.text.querySelectorAll('br').length, 1);
  assert.equal(layer.text.textContent, 'Watch thecurrent flow');
  assert.deepEqual([...layer.text.querySelectorAll('.dyn-caption-key')].map((e) => e.textContent), ['current']);
  assert.equal(layer.el.style.visibility, 'visible');
  const entry = layer.textEntry();
  assert.deepEqual({ ...entry, el: null }, {
    el: null, role: 'caption', slot: 'caption', critical: true, ink: CAPTION_INK, bg: CAPTION_PILL, overflow: 0, size: 56, min: 56,
    weight: 700, text: 'Watch thecurrent flow',
  });
  assert.equal(entry.el, layer.box);
  const key = p.digest();
  p.begin();
  layer.update(p, null);
  assert.equal(layer.el.style.visibility, 'hidden');
  assert.notEqual(p.digest(), key, 'the caption is part of the frame key');
  assert.equal(layer.textEntry(), null);
  layer.setEnabled(false);
  layer.update(p, { lines: ['x'], keys: new Set(), id: 'k2' });
  assert.equal(layer.el.style.visibility, 'hidden');
  layer.destroy();
});

test('caption text is always text, never markup', () => {
  const host = document.createElement('div');
  const layer = new CaptionLayer(host, { zones: ZONES.landscape, style: 'clean_pill' });
  layer.update(new Painter(), { lines: ['<img src=x onerror=alert(1)>'], keys: new Set(), id: 'x' });
  assert.equal(layer.text.querySelector('img'), null);
  assert.equal(layer.text.textContent, '<img src=x onerror=alert(1)>');
});

// --- timer guard ---------------------------------------------------------------------------------------------

test('the timer guard throws on setTimeout / setInterval, logs and records the trip', () => {
  const logged = [];
  const fake = { setTimeout: () => 1, setInterval: () => 2, console };
  const guard = installTimerGuard(fake, { log: { error: (m) => logged.push(m) } });
  assert.throws(() => fake.setTimeout(() => {}, 0), GuardError);
  assert.throws(() => fake.setInterval(() => {}, 10), /not allowed after the stage is ready/);
  assert.equal(guard.trips.length, 2);
  assert.deepEqual(guard.trips.map((t) => t.api), ['setTimeout', 'setInterval']);
  assert.ok(logged[0].startsWith(GUARD_MESSAGE));
  assert.match(logged[0], /timer guard/, 'the render worker matches "timer guard"');
  assert.equal(fake.aadhiStageGuard.trips, guard.trips);
  guard.restore();
  assert.equal(fake.setTimeout(), 1);
});

test('the timer guard lets allow-listed call sites through', () => {
  const fake = { setTimeout: () => 'real', setInterval: () => 'real', console };
  installTimerGuard(fake, { allow: ['captions_guard_backgrounds.test.js'] });
  assert.equal(fake.setTimeout(() => {}, 0), 'real');
});

// --- backgrounds ---------------------------------------------------------------------------------------------

test('mesh_drift paths are seeded, slow (>= 15 s periods) and drift below 2% of the width per second', () => {
  const a = blobPaths(101, 1920, 1080);
  assert.deepEqual(a, blobPaths(101, 1920, 1080));
  assert.notDeepEqual(a, blobPaths(102, 1920, 1080));
  for (const [w, h] of [[1920, 1080], [1080, 1920]]) {
    for (const b of blobPaths(7, w, h)) {
      assert.ok(b.px >= PERIOD_MIN && b.py >= PERIOD_MIN && PERIOD_MIN >= 15);
      assert.ok(b.ax <= AMPLITUDE_MAX * w && b.ay <= AMPLITUDE_MAX * w);
      const speed = 2 * Math.PI * Math.hypot(b.ax / b.px, b.ay / b.py);
      assert.ok(speed <= 0.02 * w, `blob speed ${speed.toFixed(1)} px/s`);
      assert.ok(b.cx >= 0 && b.cx <= w && b.cy >= 0 && b.cy <= h);
    }
  }
  const p = blobAt(a[0], 0);
  assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
});

test('mesh_drift (DOM fallback without canvas) moves in 0.2 s steps and desaturates during a hold', () => {
  const tl = dynamicTimeline();
  const roles = makeRoles(tl.presentation, tl.scenes[0].look);
  const view = meshDrift.build({ roles, zones: ZONES.landscape, params: { tone: 'bg' }, seed: 101, mode: 'render', pending: [] });
  assert.equal(view.el.querySelector('canvas'), null, 'jsdom has no 2D canvas: one element per blob');
  const blobs = view.el.querySelectorAll('.dyn-blob');
  assert.equal(blobs.length, 4);
  const p = new Painter();
  view.update(p, 10, 0);
  const at10 = blobs[0].style.transform;
  view.update(p, 10 + DRIFT_STEP / 2, 0);
  assert.equal(blobs[0].style.transform, at10, 'the drift holds within a step');
  view.update(p, 10 + DRIFT_STEP, 0);
  assert.notEqual(blobs[0].style.transform, at10);
  const base = view.el.style.backgroundColor;
  view.update(p, 10, 1);
  assert.notEqual(view.el.style.backgroundColor, base, 'frozen: 30% less chroma');
  for (const c of view.colors()) assert.match(c, /^#[0-9A-F]{6}$/i);
});

test('colour_block deepens the accent in dark families and keeps it in light ones', () => {
  const tl = dynamicTimeline();
  const roles = makeRoles(tl.presentation, tl.scenes[1].look);
  const deep = toOklch(blockColor(roles));
  assert.ok(deep.l < toOklch(roles.color('struct.accent')).l);
  const light = makeRoles({ content: { ink: '#141822', ink2: '#484D58' } }, { tokens: { 'struct.accent': '#BE3D70' } });
  assert.equal(blockColor(light), '#BE3D70');
  assert.equal(grainTile(3), '', 'no canvas under jsdom: no grain, no error');
  const pending = [];
  const view = colourBlock.build({ roles, zones: ZONES.portrait, params: {}, seed: 202, mode: 'render', pending });
  const p = new Painter();
  view.update(p, 0, 0);
  assert.match(view.el.style.backgroundImage, /radial-gradient/);
});
