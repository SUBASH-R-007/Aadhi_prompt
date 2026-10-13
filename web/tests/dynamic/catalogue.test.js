// @ts-check
// The generated catalogue as the stage consumes it (B2 plan §1.1; web/js/dynamic/catalogue.gen.js, written by
// `python -m aadhi.compose.dynamic.catalogue`, whose own tests pin it against zones.py / templates.py / themes.py):
// its shape, frozen all the way down; the geometry core.js reads from it equal to B1's literals (zones in B1's key
// order, the role names, the R-29 floors board_generic draws with); board_generic's LAYOUT and what it reports to
// preflight equal to its slots in the registry (no drift between the template and what Python fits text against); the
// registry's `implemented` flags equal to what the stage registers; the stage's look-engine generation the resolver's.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../player/_dom.js';
import { CATALOGUE } from '../../js/dynamic/catalogue.gen.js';
import { BLOCK_ROLES, Painter, ROLES, SCRIM_ALPHA_HEX, TYPE_FLOORS, ZONES, makeRoles, templateSlots } from '../../js/dynamic/core.js';
import { ENGINE_VERSION } from '../../js/dynamic/engine.js';
import { MediaView } from '../../js/dynamic/media.js';
import { chapterTitleAt } from '../../js/dynamic/state.js';
import { BACKGROUNDS, TEMPLATES } from '../../js/dynamic/stage.js';
import { TRANSITIONS } from '../../js/dynamic/transitions.js';
import boardGeneric, {
  BODY_WEIGHT, CARD_RADIUS, FRAME_RADIUS, LAYOUT, MIN_FIT, MIN_LABEL, MIN_TEXT, RING_GAP, RING_SIZE, regions,
} from '../../js/dynamic/templates/board_generic.js';
import { dynamicTimeline } from './_fixture.js';

before(() => installDom());
after(() => uninstallDom());

const FORMATS = /** @type {const} */ (['landscape', 'portrait']);

/** web/js/dynamic/core.js ZONES at B1 (3226e20), copied literally: what the stage drew with before the catalogue. */
const B1_ZONES = {
  landscape: {
    format: 'landscape',
    width: 1920,
    height: 1080,
    critical: [{ x0: 96, y0: 54, x1: 1824, y1: 912 }],
    caption: { x0: 96, y0: 924, x1: 1824, y1: 1032 },
    header: { x0: 96, y0: 54, x1: 1824, y1: 102 },
    title: { x0: 96, y0: 112, x1: 1824, y1: 352 },
    body: { x0: 96, y0: 300, x1: 1824, y1: 900 },
    textColumn: { x0: 96, y0: 300, x1: 1824, y1: 900 },
  },
  portrait: {
    format: 'portrait',
    width: 1080,
    height: 1920,
    critical: [
      { x0: 192, y0: 288, x1: 888, y1: 840 },
      { x0: 120, y0: 840, x1: 780, y1: 1248 },
    ],
    caption: { x0: 120, y0: 1040, x1: 780, y1: 1240 },
    header: { x0: 192, y0: 296, x1: 888, y1: 336 },
    title: { x0: 192, y0: 340, x1: 888, y1: 460 },
    body: { x0: 60, y0: 470, x1: 1020, y1: 1020 },
    textColumn: { x0: 192, y0: 470, x1: 780, y1: 1020 },
  },
};
/** core.js ROLES at B1. */
const B1_ROLES = ['ink', 'ink2', 'surface', 'entity:0', 'entity:1', 'entity:2', 'warn', 'ok', 'mark', 'focus',
  'struct.accent', 'struct.tint', 'struct.border', 'struct.pip'];

/**
 * Every object and array inside `value` (itself included), for the freeze check.
 * @param {any} value
 * @returns {any[]}
 */
function nested(value) {
  if (value === null || typeof value !== 'object') return [];
  return [value, ...Object.values(value).flatMap(nested)];
}

test('the catalogue: its top-level keys and versions, frozen all the way down', () => {
  assert.deepEqual(Object.keys(CATALOGUE).sort(), [
    'backgrounds', 'captions', 'content', 'cues', 'end_screen', 'families', 'grid', 'layout', 'personalities', 'platforms',
    'regions', 'roles', 'scrims', 'semantic_classes', 'studio_surface', 'targets', 'templates', 'transitions', 'type',
    'version', 'zones', 'zones_version',
  ]);
  assert.equal(CATALOGUE.version, 1);
  assert.equal(CATALOGUE.zones_version, 1);
  const all = nested(CATALOGUE);
  assert.ok(all.length > 900, `${all.length} objects`);
  assert.deepEqual(all.filter((o) => !Object.isFrozen(o)), [], 'every object and array is frozen');
  assert.throws(() => {
    /** @type {any} */ (CATALOGUE.zones.landscape).width = 1;
  }, TypeError);
});

test('core.js ZONES are B1\'s literals, value for value and key for key, and the catalogue\'s values', () => {
  assert.equal(JSON.stringify(ZONES), JSON.stringify(B1_ZONES), 'the same text: same values, same key order');
  assert.deepEqual(ZONES, B1_ZONES);
  for (const f of FORMATS) {
    assert.equal(CATALOGUE.zones[f].format, f);
    assert.deepEqual({ ...ZONES[f] }, { ...CATALOGUE.zones[f] }, `${f}: read from the catalogue`);
    assert.ok(Object.isFrozen(ZONES[f]) && Object.isFrozen(ZONES[f].critical) && Object.isFrozen(ZONES[f].caption));
  }
});

test('the role lists: B1\'s closed list, then the block roles (plan §1.9), both the catalogue\'s', () => {
  assert.deepEqual([...ROLES], B1_ROLES);
  assert.equal(ROLES, CATALOGUE.roles.colour);
  assert.deepEqual([...BLOCK_ROLES], ['block.base', 'block.highlight', 'block.ink', 'block.ink2', 'scrim']);
  assert.equal(BLOCK_ROLES, CATALOGUE.roles.block);
});

test('board_generic draws with B1\'s floors and weights, read from the catalogue (R-29)', () => {
  assert.deepEqual(MIN_TEXT, { landscape: 36, portrait: 40 });
  assert.deepEqual(MIN_LABEL, { landscape: 40, portrait: 44 });
  assert.deepEqual(BODY_WEIGHT, { landscape: 400, portrait: 600 });
  for (const f of FORMATS) {
    assert.equal(MIN_TEXT[f], TYPE_FLOORS[f].text_min);
    assert.equal(MIN_LABEL[f], TYPE_FLOORS[f].label_min);
    assert.equal(BODY_WEIGHT[f], CATALOGUE.templates.board_generic.slots.body[f].default.weight);
  }
  assert.ok(Object.isFrozen(MIN_TEXT) && Object.isFrozen(MIN_LABEL) && Object.isFrozen(BODY_WEIGHT));
  assert.equal(TYPE_FLOORS, CATALOGUE.type.floors);
});

test('board_generic\'s LAYOUT, ring and floors are its slots in the catalogue (no drift)', () => {
  const slots = /** @type {any} */ (templateSlots('board_generic'));
  for (const f of FORMATS) {
    const L = LAYOUT[f];
    const z = ZONES[f];
    const titleW = z.title.x1 - z.title.x0;
    const ringRoom = f === 'landscape' ? RING_SIZE + RING_GAP : 0;
    for (const [variant, spec] of /** @type {const} */ ([['default', L.title], ['quiz', L.titleQuiz], ['card', L.titleCard]])) {
      const s = slots.title[f][variant];
      assert.deepEqual([s.size, s.max_lines, s.line_height, s.prefer_lines, s.weight, s.role],
        [[spec.min, spec.max], spec.lines, spec.lh, /** @type {any} */ (spec).prefer ?? null, 700, 'title'], `${f} title ${variant}`);
      assert.deepEqual(slots.kicker[f][variant].size, [L.kicker, L.kicker], `${f} kicker ${variant}`);
    }
    assert.equal(slots.title[f].default.w, titleW);
    assert.equal(slots.title[f].quiz.w, titleW - ringRoom, `${f}: the quiz title keeps clear of the ring`);
    assert.equal(slots.kicker[f].quiz.w, titleW - ringRoom);
    const body = (/** @type {'stack' | 'split'} */ variant) => {
      const r = regions(variant, z, z.title.y0 + L.title.min * L.title.lh).cards;
      return /** @type {any} */ (r).x1 - /** @type {any} */ (r).x0;
    };
    const bodyMin = Math.max(MIN_TEXT[f], L.body * MIN_FIT[f]);
    for (const [variant, layout] of /** @type {const} */ ([['default', 'stack'], ['split', 'split']])) {
      const s = slots.body[f][variant];
      assert.deepEqual([s.size, s.weight, s.w], [[bodyMin, L.body], BODY_WEIGHT[f], body(layout)], `${f} body ${variant}`);
    }
    assert.deepEqual([slots.ring[f].default.w, slots.ring[f].default.size], [RING_SIZE, [MIN_LABEL[f], MIN_LABEL[f]]]);
    assert.deepEqual(slots.chip[f].default.size, [MIN_LABEL[f], MIN_LABEL[f]]);
  }
});

/**
 * The template's own text entries for scene `index` of the fixture at scene time `t` (direct context, as stage.test.js).
 * @param {'landscape' | 'portrait'} format @param {number} index @param {number} t
 */
function entries(format, index, t) {
  const tl = dynamicTimeline(format);
  const scene = tl.scenes[index];
  const root = document.createElement('div');
  document.body.appendChild(root);
  const ctx = /** @type {any} */ ({
    scene, index, look: scene.look, chapter: chapterTitleAt(tl, scene.start), roles: makeRoles(tl.presentation, scene.look),
    zones: ZONES[format], format, mode: 'render', reducedMotion: false, root, paint: new Painter(), pending: [],
    media: (/** @type {any} */ ref) => new MediaView(ref, { mode: 'render', box: { w: 640, h: 360 }, sceneDuration: 8, fps: 30 }),
    onBackground: { ink: '#F3F5F9', ink2: '#B9BEC7', bg: '#10132C', block: false }, rectOf: () => null, locale: 'en',
    rng: () => 0.5,
  });
  ctx.view = boardGeneric.build(ctx);
  /** @type {any} */ (boardGeneric).layout(ctx);
  boardGeneric.update(ctx, t, /** @type {any} */ ({ terminalLines: [], countdownRemaining: 3 }));
  const out = /** @type {any} */ (boardGeneric).texts(ctx);
  root.remove();
  return { out, variant: ctx.view.variant };
}

test('what board_generic reports to preflight is within its catalogue slots: sizes, minimums, weights', () => {
  const slots = /** @type {any} */ (templateSlots('board_generic'));
  // the board (cards, chip), the chapter card (kicker, title, subtitle), the quiz (ring) and the split picture scene
  for (const f of FORMATS) {
    for (const [index, t] of [[0, 9.5], [1, 3], [2, 5], [4, 4]]) {
      const { out, variant } = entries(f, index, t);
      assert.ok(out.length >= 2, `${f} scene ${index}: entries`);
      for (const e of out) {
        const titleVariant = variant === 'title' ? 'card' : index === 2 ? 'quiz' : 'default';
        const [slot, v] = e.role === 'subtitle' ? ['subtitle', 'card']
          : e.slot === 'body' ? ['body', variant === 'split' ? 'split' : 'default']
            : e.slot === 'chip' || e.slot === 'ring' ? [e.slot, 'default'] : [e.slot, titleVariant];
        const spec = slots[slot][f][v];
        const where = `${f} scene ${index}: ${e.role} in ${slot}/${v}`;
        assert.ok(spec, where);
        assert.equal(e.weight, spec.weight, `${where}: weight`);
        assert.ok(e.size >= spec.size[0] - 1e-9 && e.size <= spec.size[1] + 1e-9, `${where}: size ${e.size} in ${spec.size}`);
        if (e.min !== undefined) assert.equal(e.min, spec.size[0], `${where}: its minimum`);
        assert.equal(e.critical, spec.critical, `${where}: critical`);
      }
    }
  }
});

test('the registry\'s implemented templates, backgrounds and transitions are exactly what the stage registers', () => {
  const implemented = (/** @type {Record<string, {implemented: boolean}>} */ table) => Object.entries(table)
    .filter(([, spec]) => spec.implemented).map(([id]) => id).sort();
  assert.deepEqual(implemented(CATALOGUE.templates), Object.keys(TEMPLATES).sort());
  assert.deepEqual(implemented(CATALOGUE.backgrounds), Object.keys(BACKGROUNDS).sort());
  assert.deepEqual(implemented(CATALOGUE.transitions), [...TRANSITIONS].sort());
  for (const id of Object.keys(TEMPLATES)) assert.equal(TEMPLATES[id].id, id);
});

test('the scrim alpha and the card radius are the catalogue\'s layout tokens; the radii are dynamic.css\'s', () => {
  assert.equal(SCRIM_ALPHA_HEX, CATALOGUE.layout.alpha.scrim_hex);
  assert.equal(CARD_RADIUS, CATALOGUE.layout.radius.card);
  const css = readFileSync(new URL('../../css/dynamic.css', import.meta.url), 'utf8');
  assert.match(css, new RegExp(`\\n\\.dyn-card \\{[^}]*border-radius: ${CARD_RADIUS}px;`));
  assert.match(css, new RegExp(`\\n\\.dyn-frame \\{[^}]*border-radius: ${FRAME_RADIUS}px;`));
});

test('the stage draws the look-engine generation the resolver writes (aadhi/compose/presentation.py)', () => {
  const py = readFileSync(new URL('../../../aadhi/compose/presentation.py', import.meta.url), 'utf8');
  const m = /^ENGINE_VERSION = (\d+)$/m.exec(py);
  assert.ok(m, 'presentation.py declares ENGINE_VERSION');
  assert.equal(ENGINE_VERSION, Number(m[1]));
});

test('templateSlots: a template\'s slots, or null for an id the registry does not list (never an inherited key)', () => {
  assert.equal(templateSlots('board_generic'), CATALOGUE.templates.board_generic.slots);
  assert.deepEqual(templateSlots('formula_focus'), {}, 'not implemented yet: no slots');
  for (const id of ['nope', 'constructor', '__proto__', 'toString']) assert.equal(templateSlots(id), null, id);
});
