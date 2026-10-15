// @ts-check
// templates/list_build.js (B2c pilot; DYNAMIC_VIDEO_DESIGN §4.3 list_build, B2 plan §1.4 template contract, §1.5 cues
// and item states, §1.11 slots, §P): the six fixture timelines (tests/compose/dynamic/list_build_fixtures.py) through
// the harness's contract and order gates, the pure helpers (data, variants, geometry, slots, TeX), the painted
// behaviour in both formats (per-row reveal on its beat, number then text, current / past states, the 9:16 compact
// stack, icons, the personality's entrance and scale, reduced motion, the cues the stage draws from the template's
// geometry, preflight's text entries and remedies) and the fit, on a fake text layout (jsdom has none).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { Painter, TYPE_FLOORS, ZONES, fx, parseTarget, templateSlots } from '../../../js/dynamic/core.js';
import { FOCUS_OUTSET, FOCUS_PX, clampOf, outlinePath, termPieces, zoneOf } from '../../../js/dynamic/cues.js';
import { itemTimings } from '../../../js/dynamic/state.js';
import { STATE_WEIGHT, TEXT_RISE } from '../../../js/dynamic/templates/_shared.js';
import { scrimmedBg } from '../../../js/dynamic/templates/board_generic.js';
import listBuild, {
  DIP, ICON_EXIT, ICON_SIZE, ITEM_ENTER, LAYOUT, LINE_HEIGHT, PAD, RISE, STATE_EASE, TEXT_DELAY, boxes, lineHeight,
  listItems, panelOf, sizeSteps, slotRows, texOf, variantOf,
} from '../../../js/dynamic/templates/list_build.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['list_build.landscape.json', 'list_build.landscape.stress.json', 'list_build.landscape.tamil.json',
  'list_build.portrait.json', 'list_build.portrait.stress.json', 'list_build.portrait.hindi.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { list_build: listBuild } };

/**
 * A render stage of `tl` with list_build through the stage's test seam, and the context each scene was built with.
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...listBuild, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return listBuild.build(ctx); } };
  const run = renderStage(tl, { templates: { list_build: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
  /** Seek scene `i` to the frame nearest scene time `t`; returns that frame's exact scene time. @param {number} i @param {number} t */
  const seekTo = async (i, t) => {
    const scene = tl.scenes[i];
    const f = Math.round((Number(scene.start) + t) * tl.fps);
    await run.stage.seek(f);
    return f / tl.fps - Number(scene.start);
  };
  return { ...run, contexts, seekTo, ctx: (/** @type {number} */ i) => contexts.get(i) };
}

/** The scene-relative entrance of item `id` (state.js itemTimings: a reveal event, else its beat's start less 120 ms). */
const enterOf = (/** @type {any} */ scene, /** @type {string} */ id) => /** @type {number} */ (itemTimings(scene).find((x) => x.id === id)?.enter);
const rowOf = (/** @type {any} */ ctx, /** @type {string} */ id) => ctx.view.rows.find((/** @type {any} */ r) => r.item.id === id);
/** A hex colour as the DOM reports a painted one (`rgb(r, g, b)`). @param {string} hex */
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;
/** An opacity as a number ('' is 1: none painted). @param {string} v */
const op = (v) => (v === '' ? 1 : Number(v));

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, whole-px focus, terms, TeX, entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(listBuild, load(name)), [], name);
});

test('every fixture paints the same frame forward, in reverse and on a fresh page (a frame is a pure function of t)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const total = Math.round(tl.total_duration * tl.fps);
    // every 12th frame plus the frames around each event and each crossfade's start
    const frames = new Set();
    for (let f = 0; f < total; f += 12) frames.add(f);
    for (const s of tl.scenes) {
      for (const e of s.events || []) for (const d of [0, 0.2, 0.5]) frames.add(Math.min(total - 1, Math.round((s.start + e.t + d) * tl.fps)));
      frames.add(Math.min(total - 1, Math.round(s.start * tl.fps) + 3));
    }
    assert.deepEqual(await orderProblems(tl, [...frames].sort((a, b) => a - b), SEAM), [], name);
  }
});

test('the fixtures cover what the B2 plan §G asks of a pilot: both formats, dark and light, every text case and variant', () => {
  const seen = { formats: new Set(), modes: new Set(), variants: new Set(), entrances: new Set(), cues: new Set(), scripts: new Set() };
  for (const name of FIXTURES) {
    const tl = load(name);
    const fmt = tl.presentation.format;
    seen.formats.add(fmt);
    seen.modes.add(tl.presentation.mode);
    seen.entrances.add(tl.presentation.text_entrance);
    for (const s of tl.scenes) {
      seen.variants.add(variantOf(s, fmt, 'render', s.look));
      for (const e of s.events || []) seen.cues.add(e.kind);
      const text = (s.board || []).map((/** @type {any} */ b) => b.text).join(' ');
      if (/\p{Script=Tamil}/u.test(text)) seen.scripts.add('tamil');
      if (/\p{Script=Devanagari}/u.test(text)) seen.scripts.add('devanagari');
      if (/\$[^$]+\$/.test(text)) seen.scripts.add('tex');
      if (/[A-Za-z]{40,}/.test(text)) seen.scripts.add('long-word');
      if (/(?:\b[A-Z]{3,}\b.*){8}/.test(text)) seen.scripts.add('wide-caps');
    }
  }
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.variants].sort(), ['centred', 'list', 'split', 'stack']);
  assert.deepEqual([...seen.entrances].sort(), ['baseline_reveal', 'fade_rise']);
  assert.deepEqual([...seen.cues].sort(), ['cue_term', 'look_here', 'reveal']);
  assert.deepEqual([...seen.scripts].sort(), ['devanagari', 'long-word', 'tamil', 'tex', 'wide-caps']);
});

// --- pure helpers ---------------------------------------------------------------------------------------------------

test('listItems: the first heading with text is the heading; every other item with text is a row, in board order', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'a', kind: 'bullet', text: 'one' }, { id: 'h', kind: 'heading', text: 'Title' }, { id: 'e', kind: 'bullet', text: '  ' },
    { id: 'h2', kind: 'heading', text: 'Second heading' }, { id: 'b', kind: 'bullet', text: 'two' }, { id: 'p', kind: 'paragraph', text: 'three' },
  ] });
  const { heading, rows } = listItems(scene);
  assert.equal(heading?.id, 'h');
  assert.deepEqual(rows.map((r) => r.id), ['a', 'b', 'p'], 'empty text and a second heading are not rows');
  assert.deepEqual(listItems(/** @type {any} */ ({ board: [] })), { heading: null, rows: [] });
});

test('variantOf: 9:16 stack when a beat walks the rows, else a plain list; 16:9 split for a panel or a current icon, else centred', () => {
  const tl = load('list_build.landscape.json');
  const typical = tl.scenes[0];
  assert.equal(variantOf(typical, 'landscape', 'render', typical.look), 'split', 'beat-revealed rows with stored icons');
  assert.equal(variantOf(typical, 'landscape', 'render', { ...typical.look, icons: {} }), 'centred', 'no icon: nothing beside the list');
  assert.equal(variantOf(typical, 'portrait', 'render', typical.look), 'stack');
  const still = { ...typical, beats: typical.beats.map((/** @type {any} */ b) => ({ ...b, board_item_id: null })) };
  assert.equal(variantOf(still, 'landscape', 'render', typical.look), 'centred', 'icons need rows that take turns');
  assert.equal(variantOf(still, 'portrait', 'render', typical.look), 'list');
  const withPanel = { ...still, side_panel: { panel: { kind: 'image', title: 'P' }, media: { kind: 'image', url: '/media/p.png', width: 800, height: 600 }, show_at: 0 }, layout: { show_side_panel: true } };
  assert.equal(variantOf(withPanel, 'landscape', 'render', { ...typical.look, icons: {} }), 'split', 'a panel is shown beside any list');
  assert.equal(variantOf({ ...withPanel, layout: { show_side_panel: false } }, 'landscape', 'render', { icons: {} }), 'centred');
  assert.equal(panelOf(withPanel, 'render')?.ref.url, '/media/p.png');
  assert.equal(panelOf({ ...withPanel, side_panel: { ...withPanel.side_panel, media: { kind: 'gif', url: '/x.gif' } } }, 'render'), null, 'render mode shows no GIF');
});

test('texOf: the $...$ spans of the heading and the rows, inline, in order (what richWords mounts)', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'h', kind: 'heading', text: 'Law $V$' }, { id: 'a', kind: 'bullet', text: 'Use $I = V/R$ and $P = VI$' },
    { id: 'f', kind: 'formula', text: '', latex: 'E = mc^2' }, { id: 'b', kind: 'bullet', text: 'no maths, a \\$5 note' },
  ] });
  assert.deepEqual(texOf(scene), [{ tex: 'V', display: false }, { tex: 'I = V/R', display: false }, { tex: 'P = VI', display: false }]);
  assert.deepEqual(listBuild.tex?.(scene, null, 'portrait'), texOf(scene));
});

test('lineHeight: the type scale\'s Latin values, the Indic minimums for Tamil, Devanagari and mixed text (plan §2)', () => {
  assert.equal(lineHeight('body', 'A closed path'), LINE_HEIGHT.body.latin);
  assert.equal(lineHeight('body', 'மின்கலம் pushes $V$'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('heading', 'विद्युत परिपथ'), LINE_HEIGHT.heading.indic);
  assert.equal(lineHeight('heading', '**Ohm** [[law]]'), LINE_HEIGHT.heading.latin);
  const scale = CATALOGUE.type.scale;
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0], 'zones.py TYPE_SCALE body');
  assert.equal(LINE_HEIGHT.body.indic, scale.body.line_height.indic[0], 'plan §2: Indic body >= 1.45');
  assert.equal(LINE_HEIGHT.heading.latin, scale.heading.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.heading.indic, scale.heading.line_height.indic[0]);
});

test('sizes keep R-29: rows never below the text floor, 9:16 rows at least 600 below 64 px, 4 px steps down to the minimum', () => {
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    const floors = TYPE_FLOORS[fmt];
    assert.ok(L.item.min >= floors.text_min, `${fmt} item`);
    if (L.row) assert.ok(L.row.min >= floors.text_min && L.row.max >= 40, `${fmt} compact row (§4.3: >= 40 px)`);
    if (floors.heavy_max_px) assert.ok(L.weight >= /** @type {number} */ (floors.heavy_min_weight), `${fmt}: R-29 weight`);
  }
  assert.deepEqual(LAYOUT.portrait.item, { max: TYPE_FLOORS.portrait.body[1], min: TYPE_FLOORS.portrait.body[0] }, 'R-29 9:16 body 56-64');
  assert.deepEqual(sizeSteps(64, 56), [64, 60, 56]);
  assert.deepEqual(sizeSteps(52, 41), [52, 48, 44, 41], 'the minimum itself last');
  assert.deepEqual(sizeSteps(40, 40), [40]);
});

/**
 * preflight.py inside_bands: a box lies inside the union of the critical boxes, each box it overlaps holding its whole
 * width (a 9:16 row may cross y 840, where the upper box ends and the lower begins).
 * @param {{x: number, y: number, w: number, h: number}} r
 * @param {{x0: number, y0: number, x1: number, y1: number}[]} bands
 */
function insideBands(r, bands) {
  let covered = 0;
  for (const b of bands) {
    const overlap = Math.min(r.y + r.h, b.y1) - Math.max(r.y, b.y0);
    if (overlap <= 1) continue;
    if (r.x < b.x0 - 1 || r.x + r.w > b.x1 + 1) return false;
    covered += overlap;
  }
  return covered >= r.h - 1;
}

test('geometry: rows inside the critical zones, and a look_here outline around a row\'s text lies on the row\'s edge, never clamped', () => {
  assert.equal(PAD, FOCUS_OUTSET + FOCUS_PX);
  for (const [fmt, variants] of /** @type {const} */ ([['landscape', ['split', 'centred']], ['portrait', ['stack', 'list']]])) {
    const zones = ZONES[fmt];
    for (const variant of variants) {
      const b = boxes(fmt, variant, zones);
      // the text of a full-width row at the top and at the bottom of the list
      for (const y of [b.list.y0 + 40, b.bottom - 200]) {
        const text = { x: b.list.x0 + PAD, y, w: b.list.x1 - b.list.x0 - 2 * PAD, h: 120 };
        assert.ok(insideBands(text, zones.critical), `${fmt} ${variant}: row text inside the critical area (R-59)`);
        const path = outlinePath({ rect: text, shape: 'rect', radius: 12 }, null);
        const clamped = outlinePath({ rect: text, shape: 'rect', radius: 12 }, zoneOf(zones, text));
        const box = /** @type {any} */ (path).box;
        const got = /** @type {any} */ (clamped).box;
        assert.deepEqual([got.x0, got.x1], [box.x0, box.x1], `${fmt} ${variant} y ${y}: the outline is not clamped into the text`);
        // vertically too: cues.js clamps to the critical boxes the row overlaps (clampOf), so a 9:16 row across
        // y 840 keeps its whole outline
        const both = /** @type {any} */ (outlinePath({ rect: text, shape: 'rect', radius: 12 }, clampOf(zones, text)));
        assert.deepEqual(both.box, box, `${fmt} ${variant} y ${y}: the outline is never clamped into the text`);
        // the outline's centre line is FOCUS_OUTSET + FOCUS_PX / 2 outside the text: its outer edge is the row's edge
        assert.equal(/** @type {any} */ (path).box.x0 - FOCUS_PX / 2, b.list.x0);
      }
    }
  }
  const split = boxes('landscape', 'split', ZONES.landscape);
  assert.deepEqual([split.list.x0, split.list.x1, split.visual?.x0, split.visual?.x1], [96, 1034, 1058, 1824], 'left 55% / right 45%');
  const centred = boxes('landscape', 'centred', ZONES.landscape);
  assert.deepEqual([centred.list.x0, centred.list.x1, centred.visual], [442, 1478, null], 'the centre 60%');
  const stack = boxes('portrait', 'stack', ZONES.portrait);
  assert.deepEqual([stack.list.x0, stack.list.x1, stack.bottom], [192, 780, 1020], '9:16: the text column both critical boxes hold');
});

test('slotRows: the slot table templates.py mirrors (widths from the boxes), equal to the catalogue\'s once it lists them', () => {
  const rows = slotRows(ZONES.landscape, ZONES.portrait);
  assert.equal(rows.item.landscape.default.w, 1034 - 96 - 2 * PAD - LAYOUT.landscape.num - LAYOUT.landscape.numGap, 'default: the split layout');
  assert.equal(rows.item.landscape.centred.w, 1478 - 442 - 2 * PAD - LAYOUT.landscape.num - LAYOUT.landscape.numGap);
  assert.ok(rows.item.landscape.default.w < rows.item.landscape.centred.w, 'the default is the narrower (a stored size is an upper bound for both)');
  assert.equal(rows.item.portrait.default.w, 780 - 192 - 2 * PAD - LAYOUT.portrait.num - LAYOUT.portrait.numGap);
  assert.equal(rows.heading.landscape.default.w, 1728);
  assert.equal(rows.heading.landscape.centred.w, 1036);
  assert.equal(rows.heading.portrait.default.w, 696);
  assert.equal(rows.item.landscape.centred.weight, STATE_WEIGHT, 'laid out at the current weight');
  assert.deepEqual(rows.row.portrait.default.size, [40, 40]);
  assert.equal(rows.row.portrait.default.weight, LAYOUT.portrait.weight);
});

const CATALOGUED = Object.keys(templateSlots('list_build') || {}).length > 0;
test('the catalogue\'s list_build slots (templates.py) are slotRows exactly', { skip: !CATALOGUED && 'templates.py does not list list_build slots yet (the integrator adds them)' }, () => {
  const cat = /** @type {any} */ (templateSlots('list_build'));
  const want = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  for (const slot of Object.keys(want)) {
    for (const fmt of Object.keys(want[slot])) {
      for (const variant of Object.keys(want[slot][fmt])) {
        const got = cat[slot][fmt][variant];
        const w = want[slot][fmt][variant];
        assert.deepEqual({ w: got.w, max_lines: got.max_lines, size: got.size, role: got.role, face: got.face, weight: got.weight, line_height: got.line_height, prefer_lines: got.prefer_lines },
          w, `${slot} ${fmt} ${variant}`);
      }
    }
  }
  assert.deepEqual(Object.keys(cat).sort(), Object.keys(want).sort());
});

test('mediaBox: the right 45% for the 16:9 panel (the largest box it is shown in, from the title zone\'s top); none otherwise', () => {
  const box = listBuild.mediaBox?.(/** @type {any} */ ({}), ZONES.landscape, 'render', 'panel', { safe: false, slots: {} });
  assert.deepEqual(box, { w: 1824 - 1058, h: 900 - ZONES.landscape.title.y0 });
  assert.equal(listBuild.mediaBox?.(/** @type {any} */ ({}), ZONES.landscape, 'render', 'main', { safe: false, slots: {} }), null);
  assert.equal(listBuild.mediaBox?.(/** @type {any} */ ({}), ZONES.portrait, 'render', 'panel', { safe: false, slots: {} }), null);
});

// --- painted behaviour ----------------------------------------------------------------------------------------------

test('16:9: each row enters on its beat (120 ms before it), number first, text 80 ms later, a 24 px rise in the personality\'s time', async () => {
  const tl = load('list_build.portrait.json'); // Soft: fade_rise, x1.15
  tl.presentation.format = 'landscape';
  tl.width = 1920;
  tl.height = 1080;
  const scale = tl.presentation.motion_scale;
  const scene = tl.scenes[0];
  const s = spyStage(tl);
  const b1 = enterOf(scene, 'b1');
  assert.ok(Math.abs(b1 - (scene.beats[0].start - 0.12)) < 1e-9, 'R-17: 120 ms before its beat');
  const b2 = enterOf(scene, 'b2');
  await s.seekTo(0, b2 - 0.1);
  let r = rowOf(s.ctx(0), 'b2');
  assert.equal(r.card.style.visibility, 'hidden', 'not yet in');
  assert.equal(op(r.num.style.opacity), 0);
  await s.seekTo(0, b2 + 0.1);
  r = rowOf(s.ctx(0), 'b2');
  const num = Number(r.num.style.opacity);
  const text = Number(r.text.style.opacity);
  assert.ok(num > 0 && text < num, `number first (${num}), then the text (${text})`);
  assert.match(r.num.style.transform, /^translate\(0, \d+px\)$/, 'a whole-px rise (2D, §P)');
  const dy = Number(/translate\(0, (\d+)px/.exec(r.num.style.transform)?.[1]);
  assert.ok(dy > 0 && dy < RISE);
  await s.seekTo(0, b2 + TEXT_DELAY * scale + ITEM_ENTER * scale + 0.04);
  r = rowOf(s.ctx(0), 'b2');
  assert.deepEqual([op(r.num.style.opacity), op(r.text.style.opacity), r.num.style.transform, r.text.style.transform], [1, 1, 'none', 'none'], 'in after 450 ms x the scale');
  // the number (no delay) is still entering after the unscaled 450 ms: Soft's x1.15 stretches it to 518 ms
  await s.seekTo(0, b2 + ITEM_ENTER + 0.02);
  assert.ok(op(rowOf(s.ctx(0), 'b2').num.style.opacity) < 1);
  await s.seekTo(0, b2 + ITEM_ENTER * scale + 0.02);
  assert.equal(op(rowOf(s.ctx(0), 'b2').num.style.opacity), 1);
  s.done();
});

test('a reveal event wins over the row\'s beat (state.js itemTimings)', async () => {
  const tl = load('list_build.portrait.json');
  const scene = tl.scenes[0];
  const reveal = scene.events.find((/** @type {any} */ e) => e.kind === 'reveal');
  assert.equal(enterOf(scene, 'b2'), reveal.t);
  assert.ok(reveal.t > scene.beats[1].start, 'the event comes after the beat');
  const s = spyStage(tl);
  await s.seekTo(0, scene.beats[1].start + 0.05);
  assert.equal(rowOf(s.ctx(0), 'b2').card.style.visibility, 'hidden', 'not on its beat');
  await s.seekTo(0, reveal.t + 0.2);
  assert.equal(rowOf(s.ctx(0), 'b2').card.style.visibility, 'visible');
  s.done();
});

test('16:9 states: the row entered last is current (700, full ink, 2 px ink2 border); earlier rows past (ink2, no border)', async () => {
  const tl = load('list_build.landscape.json');
  const scene = tl.scenes[0];
  const ink = tl.presentation.content.ink;
  const ink2 = tl.presentation.content.ink2;
  const s = spyStage(tl);
  const b3 = enterOf(scene, 'b3');
  await s.seekTo(0, b3 + STATE_EASE + 0.2);
  const ctx = s.ctx(0);
  const [b1, b2, b3r, b4] = ['b1', 'b2', 'b3', 'b4'].map((id) => rowOf(ctx, id));
  assert.equal(b3r.card.style.fontWeight, '700');
  assert.equal(b3r.card.style.color, rgb(ink));
  assert.equal(b3r.card.style.getPropertyValue('--dyn-state-border').toLowerCase(), `${ink2.toLowerCase()}ff`, 'ink2 border, fully in');
  for (const past of [b1, b2]) {
    assert.equal(past.card.style.fontWeight, String(LAYOUT.landscape.weight));
    assert.equal(past.card.style.color, rgb(ink2));
    assert.equal(past.card.style.getPropertyValue('--dyn-state-border').toLowerCase(), `${ink2.toLowerCase()}00`);
  }
  assert.equal(b4.card.style.visibility, 'hidden');
  // mid-change: the past row's ink and border are on their way (its weight changed at once)
  await s.seekTo(0, b3 + 0.1);
  const mid = rowOf(s.ctx(0), 'b2');
  assert.equal(mid.card.style.fontWeight, String(LAYOUT.landscape.weight));
  const alpha = parseInt(mid.card.style.getPropertyValue('--dyn-state-border').slice(7), 16);
  assert.ok(alpha > 0 && alpha < 255, `border fading (${alpha})`);
  // the row's text is laid out at the current weight (16:9 sets the size once; it never changes with the state)
  assert.equal(b1.card.style.fontSize, `${s.ctx(0).view.largeSize}px`);
  s.done();
});

test('a list no beat walks through has no current row: every row at full ink, its own weight, no border', async () => {
  const tl = load('list_build.landscape.stress.json');
  const s = spyStage(tl);
  await s.seekTo(0, 5);
  const ctx = s.ctx(0);
  assert.equal(ctx.view.variant, 'centred');
  assert.equal(ctx.view.states, false);
  for (const r of ctx.view.rows) {
    assert.equal(r.card.style.fontWeight, String(LAYOUT.landscape.weight));
    assert.equal(r.card.style.color, rgb(tl.presentation.content.ink));
    assert.match(r.card.style.getPropertyValue('--dyn-state-border'), /00$/);
  }
  s.done();
});

test('9:16 stack: the current row large; at the next row\'s entrance it fades out, turns compact (40 px) in place and fades back', async () => {
  const tl = load('list_build.portrait.json');
  const scene = tl.scenes[0];
  const scale = tl.presentation.motion_scale;
  const s = spyStage(tl);
  const b1 = enterOf(scene, 'b1');
  const b2 = enterOf(scene, 'b2');
  await s.seekTo(0, b1 + 0.8);
  let ctx = s.ctx(0);
  const v = ctx.view;
  assert.equal(v.variant, 'stack');
  let r1 = rowOf(ctx, 'b1');
  assert.equal(r1.card.style.fontSize, `${v.largeSize}px`);
  assert.equal(r1.card.style.fontWeight, '700');
  assert.deepEqual([r1.card.style.paddingTop, r1.card.style.paddingLeft], [`${PAD}px`, `${PAD}px`]);
  assert.equal(op(r1.card.style.opacity), 1);
  // the swap: invisible at the next row's entrance, large just before it, compact just after it
  const at = await s.seekTo(0, b2);
  r1 = rowOf(s.ctx(0), 'b1');
  assert.ok(Math.abs(at - b2) < 1 / 60);
  assert.ok(Number(r1.card.style.opacity) < 0.35, `faded at the swap (${r1.card.style.opacity})`);
  await s.seekTo(0, b2 + (DIP * scale) / 2 + 0.05);
  r1 = rowOf(s.ctx(0), 'b1');
  assert.equal(op(r1.card.style.opacity), 1);
  assert.equal(r1.card.style.fontSize, `${v.compactSize}px`);
  assert.equal(v.compactSize, 40);
  assert.equal(r1.card.style.fontWeight, String(LAYOUT.portrait.weight));
  assert.deepEqual([r1.card.style.paddingTop, r1.card.style.paddingLeft], [`${LAYOUT.portrait.padCompact}px`, `${PAD}px`]);
  assert.equal(r1.card.style.color, rgb(tl.presentation.content.ink2));
  const r2 = rowOf(s.ctx(0), 'b2');
  assert.equal(r2.card.style.fontSize, `${v.largeSize}px`, 'the new row is large');
  // every row keeps its place: its top is the compact rows that entered before it
  assert.ok(Number.parseFloat(r2.el.style.top) > Number.parseFloat(r1.el.style.top));
  ctx = s.ctx(0);
  const tops = v.order.map((/** @type {string} */ id) => rowOf(ctx, id).top);
  assert.deepEqual(tops, [...tops].sort((a, b) => a - b), 'in entrance order');
  s.done();
});

test('9:16 plain list (no beat): every row the same size, no state, laid out by the list fit', async () => {
  const tl = load('list_build.portrait.stress.json');
  const s = spyStage(tl);
  await s.seekTo(1, 2.5);
  const ctx = s.ctx(1);
  assert.equal(ctx.view.variant, 'list');
  const sizes = new Set(ctx.view.rows.map((/** @type {any} */ r) => r.card.style.fontSize));
  assert.equal(sizes.size, 1);
  for (const r of ctx.view.rows) {
    assert.equal(r.card.style.fontWeight, String(LAYOUT.portrait.weight));
    assert.match(r.card.style.getPropertyValue('--dyn-state-border'), /00$/);
  }
  s.done();
});

test('16:9 icons: the current row\'s stored icon on the right, in its row\'s dim group; it leaves when the next row enters', async () => {
  const tl = load('list_build.landscape.json');
  const scene = tl.scenes[0];
  const scale = tl.presentation.motion_scale;
  const s = spyStage(tl);
  const b2 = enterOf(scene, 'b2');
  await s.seekTo(0, b2 - 0.3);
  let ctx = s.ctx(0);
  const r1 = rowOf(ctx, 'b1');
  const r2 = rowOf(ctx, 'b2');
  assert.ok(r1.icon && r2.icon && !rowOf(ctx, 'b4').icon, 'icons for the rows the look stores one for');
  assert.equal(r1.icon.parentElement, r1.el, 'in the row\'s group');
  assert.equal(r1.icon.style.visibility, 'visible');
  assert.equal(r2.icon.style.visibility, 'hidden');
  assert.equal(r1.icon.querySelector('svg')?.getAttribute('stroke'), 'currentColor');
  assert.equal(r1.icon.style.width, `${ICON_SIZE}px`);
  // on the right: x relative to the row, centred in the right 45%
  const left = Number.parseFloat(r1.el.style.left) + Number.parseFloat(r1.icon.style.left);
  assert.equal(left, Math.round(1058 + (1824 - 1058 - ICON_SIZE) / 2));
  await s.seekTo(0, b2 + ICON_EXIT * scale + 0.1);
  ctx = s.ctx(0);
  assert.equal(rowOf(ctx, 'b1').icon.style.visibility, 'hidden', 'the old icon is out');
  assert.equal(rowOf(ctx, 'b2').icon.style.visibility, 'visible');
  s.done();
});

test('the personality\'s text entrance: Crisp slides the text up inside a mask, Soft fades it with the row\'s 24 px rise', async () => {
  const crisp = load('list_build.landscape.json');
  assert.equal(crisp.presentation.text_entrance, 'baseline_reveal');
  let s = spyStage(crisp);
  const b1 = enterOf(crisp.scenes[0], 'b1');
  await s.seekTo(0, b1 + 0.15);
  let r = rowOf(s.ctx(0), 'b1');
  assert.ok(r.mask && r.text.parentElement === r.mask && r.mask.classList.contains('dyn-reveal-mask'));
  assert.equal(r.text.style.opacity, '1', 'a baseline reveal moves, it does not fade');
  assert.ok(s.ctx(0).view.head.mask, 'the heading too');
  s.done();
  const soft = load('list_build.portrait.json');
  s = spyStage(soft);
  await s.seekTo(0, enterOf(soft.scenes[0], 'b1') + 0.15);
  r = rowOf(s.ctx(0), 'b1');
  assert.equal(r.mask, null);
  assert.ok(Number(r.text.style.opacity) < 1);
  s.done();
  assert.ok(RISE > TEXT_RISE, 'the row\'s slide (§4.3: 24 px) is the list\'s own, not the 16 px text rise');
});

test('reduced motion (live player): rows and icons fade in place', async () => {
  const tl = load('list_build.landscape.json');
  tl.presentation.text_entrance = 'fade_rise';
  const s = spyStage(tl, { mode: 'live', reducedMotion: true });
  await s.seekTo(0, enterOf(tl.scenes[0], 'b1') + 0.1);
  const r = rowOf(s.ctx(0), 'b1');
  assert.equal(r.num.style.transform, 'none');
  assert.equal(r.text.style.transform, 'none');
  assert.equal(r.icon.style.transform, 'none');
  assert.ok(Number(r.num.style.opacity) > 0 && Number(r.num.style.opacity) < 1);
  s.done();
});

test('cue.term: the stage marks a row\'s [[term]] through itemEl (marker in a dark family); <item>:term:<n> finds the n-th term', async () => {
  const tl = load('list_build.landscape.json');
  const cue = tl.scenes[0].events.find((/** @type {any} */ e) => e.kind === 'cue_term');
  const s = spyStage(tl);
  await s.seekTo(0, cue.t + 0.5);
  const ctx = s.ctx(0);
  const text = /** @type {Element} */ (listBuild.itemEl?.(ctx, 'b1'));
  assert.equal(text, rowOf(ctx, 'b1').text);
  const span = /** @type {HTMLElement} */ (text.querySelector('.rt-k'));
  assert.equal(span.textContent, 'source');
  assert.match(span.style.backgroundImage, /linear-gradient/, 'the stage drew the marker on the term');
  assert.equal(listBuild.itemEl?.(ctx, 'h1'), ctx.view.head.text, 'the heading is an item too');
  assert.equal(listBuild.itemEl?.(ctx, 'nope'), null);
  // two terms in one row: <item>:term:1 is the second (cues.js termPieces over itemEl)
  const two = load('list_build.landscape.json');
  two.scenes[0].board[1].text = 'A [[source]] that pushes [[electric charge]]';
  const s2 = spyStage(two);
  await s2.seekTo(0, 1);
  const c2 = s2.ctx(0);
  const target = 'b1:term:1';
  const pieces = termPieces(listBuild, c2, /** @type {any} */ ({ kind: 'cue_term', target, parsed: parseTarget(target), start: 0, hold: 1, order: 0 }));
  assert.deepEqual(pieces.map((p) => p.span.textContent), ['electric', ' ', 'charge'], 'one term in word pieces');
  assert.deepEqual(pieces.map((p) => fx(p.to, 2)), ['0.53', '0.60', '1.00'], 'swept left to right across its pieces');
  s.done();
  s2.done();
});

test('cue.look_here: focusRect is the row\'s text box and its group; the stage dims every other group to 70%, never the target\'s', async () => {
  const tl = load('list_build.landscape.json');
  const cue = tl.scenes[0].events.find((/** @type {any} */ e) => e.kind === 'look_here');
  const s = spyStage(tl);
  await s.seekTo(0, cue.t + 0.5);
  const ctx = s.ctx(0);
  const r3 = rowOf(ctx, 'b3');
  const focus = /** @type {any} */ (listBuild.focusRect?.(ctx, 'b3'));
  assert.equal(focus.el, r3.el);
  assert.equal(focus.shape, 'rect');
  assert.equal(focus.radius, 24 - PAD, 'the outline\'s curve on the row\'s corners');
  assert.ok(['x', 'y', 'w', 'h'].every((k) => Number.isInteger(focus.rect[k])));
  assert.equal(/** @type {any} */ (listBuild.focusRect?.(ctx, 'b3:term:0')).el, r3.el, 'a term target points at its row');
  assert.equal(listBuild.focusRect?.(ctx, 'zz'), null);
  const groups = /** @type {HTMLElement[]} */ (listBuild.dimGroups?.(ctx));
  assert.deepEqual(groups, [ctx.view.head.el, ...ctx.view.rows.map((/** @type {any} */ r) => r.el)]);
  for (const g of groups) assert.equal(g.style.opacity, g === r3.el ? '' : '0.7', g.className);
  // the row's icon is in its group: undimmed with it
  assert.equal(r3.icon.parentElement, r3.el);
  s.done();
});

test('focusRect: the row\'s text box (not the row\'s edge) on whole px; the heading\'s text with its group', async () => {
  const tl = load('list_build.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(0, 6);
  const real = s.ctx(0);
  const r2 = rowOf(real, 'b2');
  const rects = new Map([[r2.body, { x: 108.4, y: 300.6, w: 913.2, h: 70.2 }], [r2.card, { x: 96, y: 288, w: 938, h: 94 }],
    [real.view.head.text, { x: 96, y: 112, w: 900, h: 110.4 }]]);
  const ctx = { ...real, rectOf: (/** @type {Element} */ el) => rects.get(el) || null };
  assert.deepEqual(listBuild.focusRect?.(ctx, 'b2'), { rect: { x: 108, y: 301, w: 914, h: 70 }, shape: 'rect', radius: 24 - PAD, el: r2.el });
  assert.deepEqual(listBuild.focusRect?.(ctx, 'h1'), { rect: { x: 96, y: 112, w: 900, h: 110 }, shape: 'rect', radius: 0, el: real.view.head.el });
  s.done();
});

test('texts(): the heading and every row, critical, in the ink, size and weight painted now; 9:16 compact rows in slot row', async () => {
  const tl = load('list_build.portrait.json');
  const scene = tl.scenes[0];
  const s = spyStage(tl);
  await s.seekTo(0, enterOf(scene, 'b3') + 0.8);
  const ctx = s.ctx(0);
  const entries = /** @type {any[]} */ (listBuild.texts?.(ctx));
  const head = entries[0];
  assert.deepEqual([head.role, head.slot, head.critical, head.weight], ['title', 'heading', true, 700]);
  assert.equal(head.text, 'What a circuit needs');
  const byText = (/** @type {string} */ id) => entries.find((e) => e.el === rowOf(ctx, id).body);
  const past = byText('b1');
  assert.deepEqual([past.role, past.slot, past.size, past.weight, past.alpha], ['body', 'row', 40, LAYOUT.portrait.weight, 1]);
  assert.equal(past.ink.toLowerCase(), tl.presentation.content.ink2.toLowerCase());
  assert.equal(past.min, LAYOUT.portrait.row?.min);
  const cur = byText('b3');
  assert.deepEqual([cur.slot, cur.size, cur.weight, cur.alpha], ['item', ctx.view.largeSize, 700, 1]);
  assert.equal(cur.min, LAYOUT.portrait.item.min);
  assert.equal(cur.text, '3 A load that turns energy into work', 'plain text with its number');
  const later = byText('b4');
  assert.equal(later.alpha, 0, 'not yet in');
  for (const e of entries) {
    assert.equal(e.critical, true);
    assert.match(String(e.ink), /^#[0-9a-f]{6}$/i);
    assert.equal(e.bg, ctx.onBackground.bg);
  }
  s.done();
});

test('preflight\'s remedies: a slot size cap and safe hold the rows small; a scrim goes behind the rows and the heading', async () => {
  const tl = load('list_build.landscape.json');
  tl.scenes[0].look.fixes = { safe: false, slots: { item: { size: 44, scrim: true }, heading: { scrim: true } } };
  let s = spyStage(tl);
  await s.seekTo(0, 3);
  let ctx = s.ctx(0);
  assert.equal(ctx.view.largeSize, 44, 'the cap (jsdom fits at the largest size allowed)');
  const scrim = ctx.roles.color('scrim');
  assert.equal(ctx.view.itemScrim, scrim);
  assert.equal(ctx.view.head.scrim, scrim);
  for (const r of ctx.view.rows) assert.ok(!['', 'transparent'].includes(r.card.style.background), 'the scrim behind each row');
  const entries = /** @type {any[]} */ (listBuild.texts?.(ctx));
  const row = entries.find((e) => e.slot === 'item' && e.alpha > 0);
  assert.equal(row.bg, scrimmedBg(scrim, ctx.onBackground, row.ink), 'measured on the scrim over the background');
  assert.notEqual(row.bg, ctx.onBackground.bg);
  assert.ok(ctx.view.head.el.style.background !== '' && ctx.view.head.el.style.padding !== '');
  s.done();
  const compact = load('list_build.portrait.json');
  compact.scenes[0].look.fixes = { safe: false, slots: { row: { scrim: true } } };
  s = spyStage(compact);
  await s.seekTo(0, 3);
  assert.equal(s.ctx(0).view.itemScrim, s.ctx(0).roles.color('scrim'), 'a compact row\'s scrim (slot row) too');
  assert.equal(s.ctx(0).view.head.scrim, null);
  s.done();
  const safe = load('list_build.portrait.json');
  safe.scenes[0].look.fixes = { safe: true, slots: {} };
  s = spyStage(safe);
  await s.seekTo(0, 3);
  ctx = s.ctx(0);
  assert.equal(ctx.view.largeSize, LAYOUT.portrait.item.min, 'safe: the slot\'s minimum');
  assert.equal(ctx.view.head.size, LAYOUT.portrait.heading.min);
  s.done();
});

test('a 16:9 picture panel beside the list: in its own dim group, contained in the right 45%, sized by mediaBox; no icons then', async () => {
  const tl = load('list_build.landscape.json');
  Object.assign(tl.scenes[0], {
    side_panel: { panel: { kind: 'image', title: 'Circuit' }, media: { kind: 'image', asset_key: 'img-1', url: '/media/circuit.png', width: 1024, height: 768, fit: 'contain' }, show_at: 1 },
    layout: { show_side_panel: true },
  });
  const s = spyStage(tl);
  await s.seekTo(0, 0.5);
  let ctx = s.ctx(0);
  const vis = ctx.view.visual;
  assert.ok(vis && vis.frame.contains(vis.view.el));
  assert.equal(vis.frame.style.visibility, 'hidden', 'enters at show_at');
  assert.ok(ctx.view.rows.every((/** @type {any} */ r) => r.icon === null), 'the panel, not the icons');
  assert.ok(listBuild.dimGroups?.(ctx).includes(vis.el));
  const left = Number.parseFloat(vis.frame.style.left);
  const width = Number.parseFloat(vis.frame.style.width);
  assert.ok(left >= 1058 && left + width <= 1824, 'inside the right 45%');
  assert.ok(Math.abs(width / Number.parseFloat(vis.frame.style.height) - 1024 / 768) < 0.005, 'contained at its aspect (whole px)');
  await s.seekTo(0, 2);
  ctx = s.ctx(0);
  assert.equal(ctx.view.visual.frame.style.visibility, 'visible');
  s.done();
  assert.deepEqual(await orderProblems(tl, [0, 20, 30, 45, 90], SEAM), []);
});

test('update writes every property on every frame with a value (no \'\': a cleared property would make the DOM order-dependent)', async () => {
  const proto = /** @type {any} */ (Painter.prototype);
  const real = proto.style;
  /** @type {string[]} */
  const empty = [];
  let inside = false;
  const wrapped = { ...listBuild, update(/** @type {any} */ ctx, /** @type {number} */ t, /** @type {any} */ st) { inside = true; try { listBuild.update(ctx, t, st); } finally { inside = false; } } };
  proto.style = function style(/** @type {any} */ el, /** @type {string} */ prop, /** @type {any} */ value) {
    if (inside && String(value) === '') empty.push(`${el.className} ${prop}`);
    return real.call(this, el, prop, value);
  };
  try {
    for (const name of FIXTURES) {
      const tl = load(name);
      const { stage, done } = renderStage(tl, { templates: { list_build: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex });
      const total = Math.round(tl.total_duration * tl.fps);
      for (let f = 0; f < total; f += 7) await stage.seek(f);
      done();
    }
  } finally {
    proto.style = real;
  }
  assert.deepEqual([...new Set(empty)], []);
});

// --- the fit, on a fake text layout ---------------------------------------------------------------------------------

/**
 * Install a fake text layout on HTMLElement (jsdom has none): a glyph is 0.55 em wide, words wrap greedily, a word
 * wider than its line breaks across lines. Enough for the list's fit: a row's (or its clone's) height from its font size,
 * padding and width, a word's height, the text box's width, the heading's height. Returns the restore function.
 */
function fakeLayout() {
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const keys = ['offsetHeight', 'offsetWidth', 'clientWidth', 'offsetTop'];
  const saved = keys.map((k) => [k, Object.getOwnPropertyDescriptor(proto, k)]);
  const CW = 0.55;
  /** @param {Element} el */
  const fmt = (el) => (el.closest('.dyn-stage--portrait') ? 'portrait' : 'landscape');
  /** @param {Element | null} el @returns {number} */
  const fontSize = (el) => {
    for (let e = el; e; e = e.parentElement) {
      const v = Number.parseFloat(/** @type {HTMLElement} */ (e).style?.fontSize || '');
      if (v > 0) return v;
    }
    return 16;
  };
  /** @param {HTMLElement} el */
  const lineH = (el) => {
    const v = Number.parseFloat(el.style.lineHeight || '');
    return (v > 0 ? v : 1.3) * fontSize(el);
  };
  /** @param {HTMLElement} card */
  const cardBox = (card) => {
    const pad = (card.style.padding || '0px').split(/\s+/).map((x) => Number.parseFloat(x));
    const py = pad[0] || 0;
    const px = pad.length > 1 ? pad[1] : py;
    const width = Number.parseFloat(card.style.width) || Number.parseFloat(/** @type {HTMLElement} */ (card.parentElement)?.style.width) || 0;
    const L = LAYOUT[fmt(card)];
    return { py, text: width - 2 * px - L.num - L.numGap };
  };
  /** @param {HTMLElement} textEl @param {number} boxW */
  const lines = (textEl, boxW) => {
    const size = fontSize(textEl);
    let n = 1;
    let x = 0;
    for (const w of textEl.querySelectorAll('.dyn-word')) {
      const ww = (w.textContent || '').length * CW * size;
      if (ww > boxW) {
        n += (x > 0 ? 1 : 0) + Math.ceil(ww / boxW) - 1;
        x = ww % boxW;
        continue;
      }
      const need = x > 0 ? x + CW * size + ww : ww;
      if (need > boxW) {
        n += 1;
        x = ww;
      } else {
        x = need;
      }
    }
    return n;
  };
  /** @param {HTMLElement} el */
  const textWidth = (el) => {
    const card = /** @type {HTMLElement | null} */ (el.closest('.dyn-lb-item'));
    if (card) return cardBox(card).text;
    const head = /** @type {HTMLElement | null} */ (el.closest('.dyn-lb-heading-group'));
    return head ? Number.parseFloat(head.style.width) || 0 : 0;
  };
  Object.defineProperty(proto, 'offsetHeight', {
    configurable: true,
    get() {
      const el = /** @type {HTMLElement} */ (this);
      if (el.classList.contains('dyn-word')) {
        const ww = (el.textContent || '').length * CW * fontSize(el);
        const box = textWidth(el);
        return (box > 0 && ww > box ? Math.ceil(ww / box) : 1) * lineH(/** @type {HTMLElement} */ (el.closest('.dyn-lb-text, .dyn-lb-heading')));
      }
      if (el.classList.contains('dyn-lb-item')) {
        const { py, text } = cardBox(el);
        const t = /** @type {HTMLElement} */ (el.querySelector('.dyn-lb-text'));
        return 2 * py + lines(t, text) * lineH(t);
      }
      if (el.classList.contains('dyn-lb-heading')) return lines(el, textWidth(el)) * lineH(el);
      if (el.classList.contains('dyn-lb-heading-group')) {
        const t = /** @type {HTMLElement} */ (el.querySelector('.dyn-lb-heading'));
        return lines(t, textWidth(t)) * lineH(t);
      }
      return 0;
    },
  });
  Object.defineProperty(proto, 'offsetTop', { configurable: true, get() { return Number.parseFloat(/** @type {HTMLElement} */ (this).style.top) || 0; } });
  Object.defineProperty(proto, 'offsetWidth', { configurable: true, get() { return (/** @type {HTMLElement} */ (this).textContent || '').length * CW * fontSize(this); } });
  Object.defineProperty(proto, 'clientWidth', { configurable: true, get() { return textWidth(this); } });
  return () => {
    for (const [k, d] of saved) {
      if (d) Object.defineProperty(proto, /** @type {string} */ (k), /** @type {PropertyDescriptor} */ (d));
      else delete proto[/** @type {string} */ (k)];
    }
  };
}

/**
 * A one-scene timeline of `fmt` with a heading and the rows `texts`, beat-revealed (`beats`) or not.
 * @param {'landscape' | 'portrait'} fmt @param {string[]} texts
 * @param {{beats?: boolean, heading?: string | null}} [opts]
 */
function listTimeline(fmt, texts, opts = {}) {
  const tl = load(fmt === 'portrait' ? 'list_build.portrait.json' : 'list_build.landscape.json');
  const scene = tl.scenes[0];
  const heading = opts.heading === undefined ? 'What a circuit needs' : opts.heading;
  scene.board = [...(heading ? [{ id: 'h1', kind: 'heading', text: heading }] : []), ...texts.map((text, i) => ({ id: `r${i}`, kind: 'bullet', text }))];
  scene.beats = texts.map((_, i) => ({ beat_id: `k${i}`, index: i, phase: 'main', start: 0.5 + i * 1.5, speech_end: 1.8 + i * 1.5, end: 2 + i * 1.5, narration: 'x', board_item_id: opts.beats === false ? null : `r${i}`, captions: [] }));
  scene.events = [];
  scene.look.icons = {};
  return tl;
}

test('fit (16:9): the largest 4 px size at which the whole list fits under the heading; smaller for a longer list; overflow at the floor', async () => {
  const restore = fakeLayout();
  try {
    const short = spyStage(listTimeline('landscape', ['One short row', 'Another short row']));
    await short.seekTo(0, 0.2);
    assert.equal(short.ctx(0).view.largeSize, LAYOUT.landscape.item.max);
    // "What a circuit needs" (20 glyphs) on one line of the centred 1036 px: 1056 px at 96 is two lines, 1012 at 92 one
    assert.equal(short.ctx(0).view.head.size, 92, 'the heading: the largest size on one line (prefer 1)');
    assert.equal(short.ctx(0).view.listTop, Math.round(112 + 92 * LINE_HEIGHT.heading.latin + LAYOUT.landscape.headingGap));
    short.done();
    // four 49-glyph rows in the centred 932 px text box, 638 px under the heading: two lines each; 52 and 48 px are too
    // tall (706, 662 px with the gaps), 44 px fits (619 px)
    const eight = 'Maximum wattage when warm motors meet heavy loads';
    const long = spyStage(listTimeline('landscape', Array(4).fill(eight)));
    await long.seekTo(0, 0.2);
    const v = long.ctx(0).view;
    assert.equal(v.largeSize, 44);
    assert.ok(v.rows.every((/** @type {any} */ r) => r.overflowLarge === 0), 'it fits');
    long.done();
    const tooLong = spyStage(listTimeline('landscape', Array(14).fill(eight)));
    await tooLong.seekTo(0, 0.2);
    const w = tooLong.ctx(0).view;
    assert.equal(w.largeSize, LAYOUT.landscape.item.min, 'at the floor');
    assert.ok(w.rows.at(-1).overflowLarge > 0, 'the rows past the bottom report it (preflight blocks: "Shorten text")');
    assert.equal(w.rows[0].overflowLarge, 0);
    const entries = /** @type {any[]} */ (listBuild.texts?.(tooLong.ctx(0)));
    assert.ok(entries.at(-1).overflow > 0);
    tooLong.done();
  } finally {
    restore();
  }
});

test('fit: a word wider than the row even at the floor does not cost the list its size (board_generic\'s rule)', async () => {
  const restore = fakeLayout();
  try {
    const s = spyStage(listTimeline('landscape', ['Pneumonoultramicroscopicsilicovolcanoconiosis is a famously long word', 'Short']));
    await s.seekTo(0, 0.2);
    assert.equal(s.ctx(0).view.largeSize, LAYOUT.landscape.item.max);
    s.done();
    // a word that breaks only at the larger sizes costs them: 15 glyphs in the 9:16 496 px text box are 528 px at
    // 64 (broken), 495 px at 60 (whole)
    const word = 'electrification';
    const textW = slotRows(ZONES.landscape, ZONES.portrait).item.portrait.default.w;
    assert.equal(textW, 496);
    const t = spyStage(listTimeline('portrait', [word, 'Short']));
    await t.seekTo(0, 0.2);
    assert.equal(t.ctx(0).view.largeSize, 60);
    t.done();
  } finally {
    restore();
  }
});

test('fit (9:16 stack): each row large below the compact rows before it; the largest size at which every row fits so', async () => {
  const restore = fakeLayout();
  try {
    const rows = ['A source that pushes charge', 'A closed path for the current', 'A load that turns energy into work', 'A switch to open the loop'];
    const s = spyStage(listTimeline('portrait', rows));
    await s.seekTo(0, 0.2);
    const v = s.ctx(0).view;
    const avail = 1020 - v.listTop;
    assert.ok([64, 60, 56].includes(v.largeSize));
    for (const r of v.rows) assert.equal(r.overflowLarge, 0, r.item.id);
    // each row's top is the compact rows before it: "A source that pushes charge" at 40 px is two lines of the 496 px
    // text box, 2 x 54 + 2 x padCompact = 116 px, then the gap
    assert.equal(v.rows[1].top - v.rows[0].top, 2 * 40 * LINE_HEIGHT.body.latin + 2 * LAYOUT.portrait.padCompact + LAYOUT.portrait.gap);
    assert.equal(Number.parseFloat(v.rows[1].el.style.top), v.listTop + v.rows[1].top);
    assert.ok(v.rows.at(-1).top < avail);
    s.done();
    // too many rows for the stack even at 56 px: the last ones report their overflow
    const many = spyStage(listTimeline('portrait', Array(9).fill('A load that turns energy into useful work')));
    await many.seekTo(0, 0.2);
    const m = many.ctx(0).view;
    assert.equal(m.largeSize, LAYOUT.portrait.item.min);
    assert.ok(m.rows.at(-1).overflowLarge > 0);
    assert.ok(m.rows.at(-1).overflowCompact > 0, 'past the bottom even compact');
    assert.equal(m.rows[0].overflowLarge, 0);
    many.done();
  } finally {
    restore();
  }
});

test('a 9:16 stack follows the entrance order (a later row in the board can enter first); a 16:9 list keeps the board order', async () => {
  const restore = fakeLayout();
  try {
    for (const fmt of /** @type {const} */ (['portrait', 'landscape'])) {
      const tl = listTimeline(fmt, ['First row', 'Second row', 'Third row']);
      // r2 is revealed before r1
      tl.scenes[0].events = [{ t: 1.0, kind: 'reveal', target: 'r2', anchored: true }, { t: 3.0, kind: 'reveal', target: 'r1', anchored: true }];
      const s = spyStage(tl);
      await s.seekTo(0, 4);
      const v = s.ctx(0).view;
      const [r0, r1, r2] = ['r0', 'r1', 'r2'].map((id) => rowOf(s.ctx(0), id));
      if (fmt === 'portrait') {
        assert.deepEqual(v.order, ['r0', 'r2', 'r1']);
        assert.ok(r0.top < r2.top && r2.top < r1.top, 'the stack grows in the order rows enter');
        assert.equal(r1.card.style.fontSize, `${v.largeSize}px`, 'the row entered last is the large one');
        assert.equal(r2.card.style.fontSize, '40px');
      } else {
        assert.ok(r0.top < r1.top && r1.top < r2.top, 'a numbered list keeps its order');
        assert.equal(r1.card.style.fontWeight, '700', 'current: entered last');
      }
      assert.deepEqual([r0, r1, r2].map((r) => r.num.textContent), ['1', '2', '3'], 'numbers follow the board');
      s.done();
    }
  } finally {
    restore();
  }
});

test('fit (9:16 plain list): large rows when the whole list fits at 56 px, else every row compact (never fewer rows)', async () => {
  const restore = fakeLayout();
  try {
    const few = spyStage(listTimeline('portrait', ['One', 'Two', 'Three'], { beats: false }));
    await few.seekTo(0, 2);
    assert.equal(few.ctx(0).view.variant, 'list');
    assert.equal(few.ctx(0).view.allCompact, false);
    assert.equal(few.ctx(0).view.largeSize, LAYOUT.portrait.item.max);
    few.done();
    const lots = spyStage(listTimeline('portrait', Array(6).fill('A load that turns energy into useful work'), { beats: false }));
    await lots.seekTo(0, 2);
    const v = lots.ctx(0).view;
    assert.equal(v.allCompact, true);
    assert.equal(v.largeSize, 40);
    for (const r of v.rows) assert.equal(r.card.style.fontSize, '40px');
    const entries = /** @type {any[]} */ (listBuild.texts?.(lots.ctx(0)));
    assert.ok(entries.filter((e) => e.slot === 'row').length === 6);
    lots.done();
  } finally {
    restore();
  }
});
