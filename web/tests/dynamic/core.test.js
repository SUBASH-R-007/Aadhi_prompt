import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  BLOCK_ROLES,
  DEFAULT_FPS,
  Hasher,
  Painter,
  ROLES,
  SCRIM_ALPHA_HEX,
  ZONES,
  blockInk2,
  desaturate,
  fixesOf,
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
import { ENGINE_VERSION, engineProblem, engineVersion } from '../../js/dynamic/engine.js';
import { contrastRatio, mixOklch, toOklch } from '../../js/motion/color.js';
import { OWNER_DECISION, R1_PALETTES, STAGE_VECTORS, dynamicTimeline } from './_fixture.js';

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

// --- B2a: block roles from the look's stored tokens (plan §1.2, §1.9), content_alt, fixes, the engine generation -----

const BASE_KEYS = ['struct.accent', 'bg', 'bg-alt', 'bg-tint', 'surface'];

test('block roles: a look\'s stored tokens; without them, exactly what the B1 stage derived (colour vectors)', () => {
  assert.equal(R1_PALETTES.length, 12, 'four R1 families, three palette slots each');
  const changed = new Map();
  for (const { name, stored, b1: v1 } of R1_PALETTES) {
    assert.ok(v1, `${name}: a B1 vector`);
    const presentation = /** @type {any} */ ({ content: v1.content });
    for (const k of BASE_KEYS) assert.equal(stored[k], v1.tokens[k].toUpperCase(), `${name} ${k}: B1's token`);
    // engine_version 2: the ten stored tokens are drawn as they are
    const v2 = makeRoles(presentation, /** @type {any} */ ({ tokens: stored }));
    for (const role of BLOCK_ROLES) assert.equal(v2.color(role), stored[role], `${name} ${role}: stored`);
    assert.equal(v2.color('surface'), stored.surface);
    // a B1 look (the five tokens): the B1 stage's run-time derivation, string for string
    const b1 = makeRoles(presentation, /** @type {any} */ ({ tokens: v1.tokens }));
    /** @type {Record<string, string>} */
    const derived = {
      'block.base': v1.block.base, 'block.highlight': v1.block.highlight, 'block.ink': v1.block.ink,
      'block.ink2': v1.block.ink2, scrim: v1.scrim,
    };
    for (const role of BLOCK_ROLES) assert.equal(b1.color(role), derived[role], `${name} ${role}: B1's derivation`);
    assert.deepEqual(v1.block.colors, [v1.block.base, v1.block.highlight]);
    const differ = BLOCK_ROLES.filter((role) => b1.color(role).toUpperCase() !== v2.color(role).toUpperCase());
    if (differ.length) changed.set(name, differ);
  }
  assert.deepEqual(Object.fromEntries(changed), OWNER_DECISION, 'the stored tokens differ from B1 exactly by the owner decision');
});

test('block roles are feature-detected per field: a missing or malformed one is derived from those present', () => {
  const v1 = STAGE_VECTORS.get('midnight_indigo/1');
  const presentation = /** @type {any} */ ({ content: v1.content });
  // only a stored base: the highlight is mixed from it (as B1 mixed it from its own base), the inks picked over both
  const base = '#3A2F12';
  const r = makeRoles(presentation, /** @type {any} */ ({ tokens: { ...v1.tokens, 'block.base': base } }));
  const highlight = mixOklch(base, v1.tokens['struct.accent'], 0.35);
  assert.equal(r.color('block.base'), base);
  assert.equal(r.color('block.highlight'), highlight);
  assert.equal(r.color('block.ink'), r.inkOn([base, highlight]));
  assert.equal(r.color('block.ink2'), blockInk2(r.color('block.ink'), [base, highlight]));
  // malformed stored values are ignored: the derivation, as for a B1 look
  const bad = makeRoles(presentation, /** @type {any} */ ({
    tokens: { ...v1.tokens, 'block.base': 'red', 'block.ink': '#FFF', 'block.ink2': 7, scrim: '#10132C' },
  }));
  assert.equal(bad.color('block.base'), v1.block.base);
  assert.equal(bad.color('block.ink'), v1.block.ink);
  assert.equal(bad.color('block.ink2'), v1.block.ink2);
  assert.equal(bad.color('scrim'), `${v1.tokens.bg}${SCRIM_ALPHA_HEX}`, 'a scrim without its alpha is not a scrim');
  assert.equal(makeRoles(presentation, /** @type {any} */ ({ tokens: { ...v1.tokens, scrim: '#01020380' } })).color('scrim'), '#01020380');
  // a look without tokens: the fallbacks' block, still derived as B1 did
  const none = makeRoles(presentation, null);
  assert.equal(none.color('block.base'), mixOklch('#FF936D', '#10132C', 0.38));
  assert.throws(() => r.color('block'), /unknown colour role/);
  assert.throws(() => r.color('block.surface'), /unknown colour role/);
  assert.throws(() => r.color('scrim.alpha'), /unknown colour role/);
});

test('roles.on(mode): the scene\'s own roles for its mode; the other mode\'s content palette (content_alt) otherwise', () => {
  const light = STAGE_VECTORS.get('paper_ink/0');
  const dark = STAGE_VECTORS.get('midnight_indigo/0');
  const presentation = /** @type {any} */ ({ content: light.content, content_alt: dark.content });
  const roles = makeRoles(presentation, /** @type {any} */ ({ tokens: light.tokens }));
  assert.equal(roles.mode, 'light');
  assert.equal(roles.on('light'), roles);
  const onDark = roles.on('dark');
  assert.equal(onDark.mode, 'dark');
  assert.equal(roles.on('dark'), onDark, 'made once');
  for (const role of ['ink', 'ink2', 'entity:0', 'entity:1', 'entity:2', 'warn', 'ok', 'mark', 'focus']) {
    assert.equal(onDark.color(role), dark.content[role], role);
    assert.equal(roles.color(role), light.content[role], `${role}: the scene's own palette is unchanged`);
  }
  for (const role of ['surface', 'struct.accent', 'struct.tint', 'struct.border', 'struct.pip', ...BLOCK_ROLES]) {
    assert.equal(onDark.color(role), roles.color(role), `${role}: the scene's structure`);
  }
  assert.equal(onDark.tokens, roles.tokens);
  // inkOn offers that palette's ink (with white and near-black): a black ink is the best on white there, not here
  const custom = makeRoles(/** @type {any} */ ({ content: dark.content, content_alt: { ...light.content, ink: '#000000' } }), null);
  assert.equal(custom.on('light').inkOn('#FFFFFF'), '#000000');
  assert.equal(custom.inkOn('#FFFFFF'), '#14161C');
  assert.equal(onDark.on('light'), roles);
  assert.equal(onDark.on('dark'), onDark);
  assert.throws(() => onDark.color('accent'), /unknown colour role/);
  // a timeline without that palette (B1), an incomplete one or one of the wrong mode: refused, never guessed
  for (const alt of [undefined, {}, { ...dark.content, ok: 'green' }, light.content]) {
    const r = makeRoles(/** @type {any} */ ({ content: light.content, content_alt: alt }), null);
    assert.throws(() => r.on('dark'), /no dark content palette/, JSON.stringify(alt));
    assert.equal(r.on('light'), r);
  }
  assert.throws(() => roles.on(/** @type {any} */ ('sepia')), /unknown colour mode/);
});

test('fixesOf: preflight\'s fixes channel first, B1\'s legacy fields second (§7.12, plan §1.2)', () => {
  assert.deepEqual(fixesOf(null), { safe: false, slots: {} });
  assert.deepEqual(fixesOf(/** @type {any} */ ({ variant: 'safe', text_fit: { title: { size: 60, scrim: true, lines: 2 } } })),
    { safe: true, slots: { title: { size: 60, scrim: true } } }, 'a B1 look remedied before B2a');
  assert.deepEqual(fixesOf(/** @type {any} */ ({ fixes: { safe: true, slots: { body: { size: 44 }, kicker: { scrim: true } } } })),
    { safe: true, slots: { body: { size: 44, scrim: false }, kicker: { size: null, scrim: true } } });
  // both channels: the fixes size wins; a scrim or the safe layout from either holds
  assert.deepEqual(fixesOf(/** @type {any} */ ({
    variant: 'media_left', fixes: { safe: false, slots: { title: { size: 50 }, body: { scrim: true } } },
    text_fit: { title: { size: 70, scrim: true }, body: { size: 48 } },
  })), { safe: false, slots: { title: { size: 50, scrim: true }, body: { size: 48, scrim: true } } });
  assert.equal(fixesOf(/** @type {any} */ ({ variant: 'safe', fixes: { safe: false, slots: {} } })).safe, true);
  // a fixes size that is no size falls back to the legacy one; malformed channels are empty
  assert.deepEqual(fixesOf(/** @type {any} */ ({ fixes: { slots: { title: { size: 0 } } }, text_fit: { title: { size: '44' } } })).slots.title,
    { size: 44, scrim: false });
  assert.deepEqual(fixesOf(/** @type {any} */ ({ fixes: 5, text_fit: 'x' })), { safe: false, slots: {} });
  assert.deepEqual(fixesOf(/** @type {any} */ ({ fixes: { safe: 'yes', slots: [] } })), { safe: false, slots: {} });
  assert.equal(fixesOf(/** @type {any} */ ({ fixes: { slots: { title: { scrim: 'true' } } } })).slots.title.scrim, false, 'only true');
});

test('the look-engine generation: an integer >= 1, else B1\'s 1 (as the API reads it); above the stage\'s: refused', () => {
  const tl = (/** @type {any} */ v) => ({ presentation: { style: 'dynamic', engine_version: v } });
  assert.equal(ENGINE_VERSION, 5);
  assert.deepEqual([1, 2, 3, 7].map((v) => engineVersion(tl(v))), [1, 2, 3, 7]);
  assert.equal(engineProblem(tl(4)), null);
  assert.equal(engineProblem(tl(5)), null);
  for (const v of [undefined, null, 0, -2, 2.5, '3', true, Number.NaN]) assert.equal(engineVersion(tl(v)), 1, String(v));
  assert.equal(engineVersion({}), 1);
  assert.equal(engineVersion(null), 1);
  assert.equal(engineProblem(tl(1)), null);
  assert.equal(engineProblem(tl(2)), null);
  assert.equal(engineProblem(tl(undefined)), null);
  assert.equal(engineProblem(tl(3)), null);
  assert.match(String(engineProblem(tl(6))), /look engine 6; this page draws up to 5/);
});
