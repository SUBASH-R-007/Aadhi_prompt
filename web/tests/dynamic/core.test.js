import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_FPS,
  Hasher,
  Painter,
  ROLES,
  ZONES,
  desaturate,
  frameOf,
  fx,
  insideAny,
  makeRoles,
  sceneIndexOfFrame,
  stageInfo,
  timelineFps,
  toneColor,
  zonesFor,
} from '../../js/dynamic/core.js';
import { contrastRatio, toOklch } from '../../js/motion/color.js';
import { dynamicTimeline } from './_fixture.js';

test('frame numbers use the motion engine rounding (floor(t * fps + 0.5))', () => {
  assert.equal(frameOf(0, 30), 0);
  assert.equal(frameOf(10, 30), 300);
  assert.equal(frameOf(0.25, 30), 8); // 7.5 rounds up, like Python's math.floor(7.5 + 0.5)
  assert.equal(frameOf(61.15, 30), 1835);
  assert.equal(frameOf(1 / 60 - 1e-9, 30), 0);
});

test('stageInfo gives contiguous scene ranges that cover every frame', () => {
  const tl = dynamicTimeline();
  const info = stageInfo(tl);
  assert.deepEqual([info.width, info.height, info.fps, info.frames], [1920, 1080, 30, 1200]);
  assert.deepEqual(info.scenes.map((s) => [s.index, s.f0, s.f1]), [[0, 0, 300], [1, 300, 420], [2, 420, 780], [3, 780, 960], [4, 960, 1200]]);
  for (let i = 1; i < info.scenes.length; i++) assert.equal(info.scenes[i].f0, info.scenes[i - 1].f1);
  const portrait = stageInfo(dynamicTimeline('portrait'));
  assert.deepEqual([portrait.width, portrait.height], [1080, 1920]);
});

test('a scene shorter than a frame still owns one frame; the rate falls back to 30', () => {
  const tl = dynamicTimeline();
  tl.scenes = [{ ...tl.scenes[0], start: 0, duration: 0.01 }, { ...tl.scenes[1], start: 0.01, duration: 1 }];
  tl.total_duration = 1.01;
  const info = stageInfo(tl);
  assert.deepEqual(info.scenes.map((s) => [s.f0, s.f1]), [[0, 1], [1, 30]]);
  assert.equal(timelineFps({ fps: 0 }), DEFAULT_FPS);
  assert.equal(timelineFps({ fps: 24 }), 24);
  assert.equal(timelineFps({ fps: 29.97 }), DEFAULT_FPS);
});

test('sceneIndexOfFrame finds the owner of a frame by range', () => {
  const ranges = stageInfo(dynamicTimeline()).scenes;
  assert.equal(sceneIndexOfFrame(ranges, 0), 0);
  assert.equal(sceneIndexOfFrame(ranges, 299), 0);
  assert.equal(sceneIndexOfFrame(ranges, 300), 1);
  assert.equal(sceneIndexOfFrame(ranges, 1199), 4);
  assert.equal(sceneIndexOfFrame([], 3), -1);
});

test('Hasher is stable, order-sensitive and separates its parts', () => {
  const a = new Hasher().add('ab').add('c').digest();
  assert.equal(a, new Hasher().add('ab').add('c').digest());
  assert.match(a, /^[0-9a-f]{16}$/);
  assert.notEqual(a, new Hasher().add('a').add('bc').digest());
  assert.notEqual(a, new Hasher().add('c').add('ab').digest());
});

test('Painter hashes every write but skips unchanged DOM writes', () => {
  const writes = [];
  const el = {
    style: {
      setProperty: (k, v) => writes.push(['set', k, v]),
      removeProperty: (k) => writes.push(['remove', k]),
    },
  };
  const p = new Painter();
  p.begin();
  p.style(/** @type {any} */ (el), 'opacity', '0.5');
  const first = p.digest();
  p.begin();
  p.style(/** @type {any} */ (el), 'opacity', '0.5');
  assert.equal(p.digest(), first, 'the same frame gives the same key');
  assert.equal(writes.length, 1, 'an unchanged value is not written again');
  p.begin();
  p.style(/** @type {any} */ (el), 'opacity', '');
  assert.notEqual(p.digest(), first);
  assert.deepEqual(writes.at(-1), ['remove', 'opacity']);
  p.begin();
  p.note('x');
  const noted = p.digest();
  p.begin();
  p.note('y');
  assert.notEqual(p.digest(), noted);
});

test('fx rounds without negative zero', () => {
  assert.equal(fx(0.12345), '0.123');
  assert.equal(fx(-0.0001), '0.000');
  assert.equal(fx(2, 2), '2.00');
});

test('roles come from the presentation and the look; unknown roles throw', () => {
  const tl = dynamicTimeline();
  const roles = makeRoles(tl.presentation, tl.scenes[0].look);
  assert.equal(roles.mode, 'dark');
  assert.equal(roles.color('ink'), '#F3F5F9');
  assert.equal(roles.color('struct.accent'), '#FF936D');
  assert.equal(roles.color('struct.border'), '#FF936D');
  assert.equal(roles.color('surface'), '#252A3F');
  for (const r of ROLES) assert.match(roles.color(r), /^#[0-9A-Fa-f]{6}$/);
  assert.throws(() => roles.color('accent'), /unknown colour role/);
  assert.throws(() => roles.color('bg'), /unknown colour role/);
  // text on a saturated block: the more legible of the inks
  const on = roles.inkOn('#F2D935');
  assert.ok(contrastRatio(on, '#F2D935') >= 4.5);
  const light = makeRoles({ content: { ink: '#141822', ink2: '#484D58' } }, null);
  assert.equal(light.mode, 'light');
  assert.ok(contrastRatio(light.color('ink'), light.color('surface')) >= 7, 'a look without a surface gets a legible one');
  assert.ok(contrastRatio(roles.color('ink'), makeRoles(tl.presentation, { tokens: { bg: '#10132C' } }).color('surface')) >= 7);
  assert.equal(toneColor(roles, 'bg-alt'), '#181D37');
  assert.equal(toneColor(roles, 'nope'), '#10132C');
});

test('desaturate scales OKLCH chroma and keeps lightness and hue', () => {
  const c = '#FF936D';
  assert.equal(desaturate(c, 0), c);
  const d = toOklch(desaturate(c, 0.3));
  const o = toOklch(c);
  assert.ok(Math.abs(d.l - o.l) < 0.01);
  assert.ok(Math.abs(d.c - o.c * 0.7) < 0.01);
  assert.ok(Math.abs(d.h - o.h) < 2);
});

test('zones follow R-59 and the format', () => {
  assert.deepEqual(ZONES.landscape.critical, [{ x0: 96, y0: 54, x1: 1824, y1: 912 }]);
  assert.deepEqual(ZONES.landscape.caption, { x0: 96, y0: 924, x1: 1824, y1: 1032 });
  assert.deepEqual(ZONES.portrait.caption, { x0: 120, y0: 1040, x1: 780, y1: 1240 });
  assert.equal(zonesFor(dynamicTimeline('portrait')).format, 'portrait');
  assert.equal(zonesFor(dynamicTimeline('landscape')).format, 'landscape');
  // the portrait text column lies inside both critical boxes
  const col = ZONES.portrait.textColumn;
  assert.ok(insideAny({ x: col.x0, y: 300, w: col.x1 - col.x0, h: 500 }, ZONES.portrait.critical));
  assert.ok(insideAny({ x: col.x0, y: 900, w: col.x1 - col.x0, h: 100 }, ZONES.portrait.critical));
  assert.ok(!insideAny({ x: 800, y: 900, w: 80, h: 40 }, ZONES.portrait.critical), 'the TikTok rail is outside');
});
