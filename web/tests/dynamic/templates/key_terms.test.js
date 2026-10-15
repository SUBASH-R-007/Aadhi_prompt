// @ts-check
// templates/key_terms.js (DYNAMIC_VIDEO_DESIGN §4.3 key_terms, B2 plan §1.4 template contract, §1.5 cues, §1.11
// slots, §P): the four fixture timelines (tests/compose/dynamic/make_key_terms_fixtures.py) through the harness's
// contract and order gates, the pure helpers (data, geometry, card widths, slots, sizes, TeX), the painted behaviour
// in both formats (a card per beat, the icon's stroke draw, term then meaning 80 ms apart, the personality's entrance
// and scale, reduced motion, the cues the stage draws from the template's geometry, preflight's text entries and
// remedies, the board_generic fallback) and the fit, on a fake text layout (jsdom has none).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { Painter, TYPE_FLOORS, ZONES, fx, parseTarget, templateSlots } from '../../../js/dynamic/core.js';
import { TEMPLATES } from '../../../js/dynamic/stage.js';
import { termPieces } from '../../../js/dynamic/cues.js';
import { itemTimings } from '../../../js/dynamic/state.js';
import { TEXT_RISE } from '../../../js/dynamic/templates/_shared.js';
import { scrimmedBg } from '../../../js/dynamic/templates/_shared.js';
import keyTerms, {
  CARD_GUTTER, HEADING_ENTER, ICON_DRAW, ICON_SIZE, ITEM_ENTER, LAYOUT, LINE_HEIGHT, RISE, TERM_WEIGHT, TEXT_DELAY,
  boxes, cardWidth, drawAt, drawsScene, inlineIcon, lineHeight, sizePairs, sizeSteps, slotRows, termItems, texOf,
} from '../../../js/dynamic/templates/key_terms.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['key_terms.landscape.json', 'key_terms.landscape.tamil.json', 'key_terms.portrait.json',
  'key_terms.portrait.hindi.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { key_terms: keyTerms } };

/**
 * A render stage of `tl` with key_terms through the stage's test seam, and the context each scene was built with.
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...keyTerms, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return keyTerms.build(ctx); } };
  const run = renderStage(tl, { templates: { key_terms: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
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
const cardOf = (/** @type {any} */ ctx, /** @type {string} */ id) => ctx.view.cards.find((/** @type {any} */ c) => c.item.id === id);
/** A hex colour as the DOM reports a painted one (`rgb(r, g, b)`). @param {string} hex */
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;
/** An opacity as a number ('' is 1: none painted). @param {string} v */
const op = (v) => (v === '' ? 1 : Number(v));

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, whole-px focus, terms, TeX, entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(keyTerms, load(name)), [], name);
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

test('the fixtures cover what the B2 plan §G asks: both formats, dark and light, every text case, 2-4 terms', () => {
  const seen = { formats: new Set(), modes: new Set(), entrances: new Set(), cues: new Set(), scripts: new Set(), counts: new Set() };
  for (const name of FIXTURES) {
    const tl = load(name);
    seen.formats.add(tl.presentation.format);
    seen.modes.add(tl.presentation.mode);
    seen.entrances.add(tl.presentation.text_entrance);
    for (const s of tl.scenes) {
      const { terms } = termItems(s);
      seen.counts.add(terms.length);
      for (const e of s.events || []) seen.cues.add(e.kind);
      const text = (s.board || []).map((/** @type {any} */ b) => `${b.term || ''} ${b.text || ''}`).join(' ');
      if (/\p{Script=Tamil}/u.test(text)) seen.scripts.add('tamil');
      if (/\p{Script=Devanagari}/u.test(text)) seen.scripts.add('devanagari');
      if (/\$[^$]+\$/.test(text)) seen.scripts.add('tex');
      if (/[A-Za-z]{40,}/.test(text)) seen.scripts.add('long-word');
      if (/(?:\b[A-Z]{3,}\b.*){8}/.test(text)) seen.scripts.add('wide-caps');
    }
  }
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.entrances].sort(), ['baseline_reveal', 'fade_rise']);
  assert.deepEqual([...seen.cues].sort(), ['cue_term', 'draw']);
  assert.deepEqual([...seen.scripts].sort(), ['devanagari', 'long-word', 'tamil', 'tex', 'wide-caps']);
  assert.deepEqual([...seen.counts].sort(), [2, 3, 4], '§4.3: 2-4 terms');
});

// --- pure helpers ---------------------------------------------------------------------------------------------------

test('termItems: the first heading with text is the heading; every other item with a term or text is a card', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'a', kind: 'definition', term: 'One', text: 'first' }, { id: 'h', kind: 'heading', text: 'Title' },
    { id: 'e', kind: 'definition', term: '  ', text: '  ' }, { id: 'h2', kind: 'heading', text: 'Second' },
    { id: 'b', kind: 'definition', term: 'Two', text: '' }, { id: 'p', kind: 'bullet', text: 'a stray bullet' },
  ] });
  const { heading, terms } = termItems(scene);
  assert.equal(heading?.id, 'h');
  assert.deepEqual(terms.map((t) => t.id), ['a', 'b', 'p'], 'an empty item and a second heading are not cards');
  assert.deepEqual(termItems(/** @type {any} */ ({ board: [] })), { heading: null, terms: [] });
});

test('drawsScene: a scene with a shown side panel or a scene medium is board_generic\'s (nothing is dropped)', () => {
  const plain = /** @type {any} */ ({ board: [] });
  assert.equal(drawsScene(plain), true);
  assert.equal(drawsScene({ ...plain, side_panel: { panel: { kind: 'image' } } }), false);
  assert.equal(drawsScene({ ...plain, side_panel: { panel: { kind: 'image' } }, layout: { show_side_panel: false } }), true);
  assert.equal(drawsScene({ ...plain, media: { kind: 'image', url: '/x.png' } }), false);
});

test('a scene with a panel this template does not draw is drawn by board_generic whole, every hook handed on', async () => {
  for (const name of ['key_terms.landscape.json', 'key_terms.portrait.json']) {
    const tl = load(name);
    tl.scenes[0].side_panel = { panel: { kind: 'image', title: 'P' }, media: { kind: 'image', url: '/media/p.png', width: 800, height: 600 }, show_at: 0 };
    tl.scenes[0].layout = { ...(tl.scenes[0].layout || {}), show_side_panel: true };
    const run = renderStage(tl, { templates: { key_terms: keyTerms } });
    await run.stage.seek(30);
    assert.ok(run.stage.el.querySelector('.dyn-board'), `${name}: board_generic drew it`);
    assert.equal(run.stage.el.querySelector('.dyn-kt'), null);
    const fmt = tl.presentation.format;
    assert.deepEqual(keyTerms.tex?.(tl.scenes[0], tl.scenes[0].look, fmt), boardGeneric.tex?.(tl.scenes[0], tl.scenes[0].look, fmt));
    const zones = ZONES[fmt];
    assert.deepEqual(keyTerms.mediaBox?.(tl.scenes[0], zones, 'render', 'panel', { safe: false, slots: {} }),
      boardGeneric.mediaBox?.(tl.scenes[0], zones, 'render', 'panel', { safe: false, slots: {} }));
    run.done();
  }
});

test('texOf: the $...$ spans of the heading and of each term and meaning, in field order, inline', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'h', kind: 'heading', text: 'Law $V$' }, { id: 'a', kind: 'definition', term: '$R$ symbol', text: 'Use $I = V/R$' },
    { id: 'b', kind: 'definition', term: 'Plain', text: 'no maths, a \\$5 note' },
  ] });
  assert.deepEqual(texOf(scene), [{ tex: 'V', display: false }, { tex: 'R', display: false }, { tex: 'I = V/R', display: false }]);
  assert.deepEqual(keyTerms.tex?.(scene, null, 'portrait'), texOf(scene));
});

test('lineHeight: the type scale\'s Latin values, the Indic minimums for Tamil, Devanagari and mixed text (plan §2)', () => {
  assert.equal(lineHeight('body', 'The push on charge'), LINE_HEIGHT.body.latin);
  assert.equal(lineHeight('body', 'மின்கலம் pushes $V$'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('heading', 'विद्युत परिपथ'), LINE_HEIGHT.heading.indic);
  assert.equal(lineHeight('heading', '[[Voltage]]'), LINE_HEIGHT.heading.latin);
  const scale = CATALOGUE.type.scale;
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.body.indic, scale.body.line_height.indic[0], 'plan §2: Indic body >= 1.45');
  assert.equal(LINE_HEIGHT.heading.latin, scale.heading.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.heading.indic, scale.heading.line_height.indic[0]);
});

test('sizes keep R-29: no size below the format\'s text floor, 9:16 text at <= 64 px at least weight 600', () => {
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    const floors = TYPE_FLOORS[fmt];
    assert.ok(L.term.min >= floors.text_min, `${fmt} term`);
    assert.ok(L.meaning.min >= floors.text_min, `${fmt} meaning`);
    assert.ok(L.heading.min >= floors.text_min, `${fmt} heading`);
  }
  const heavy = /** @type {number} */ (TYPE_FLOORS.portrait.heavy_min_weight);
  assert.ok(LAYOUT.portrait.meaningWeight >= heavy, 'R-29 (9:16): text at <= 64 px is at least 600');
  assert.ok(TERM_WEIGHT >= heavy);
  assert.ok(LAYOUT.portrait.term.max <= /** @type {number} */ (TYPE_FLOORS.portrait.heavy_max_px));
  assert.deepEqual(sizeSteps(64, 48), [64, 60, 56, 52, 48]);
  assert.deepEqual(sizeSteps(52, 41), [52, 48, 44, 41], 'the minimum itself last');
  assert.deepEqual(sizeSteps(40, 40), [40]);
  // the two sizes step down together, each holding at its own floor
  assert.deepEqual(sizePairs(LAYOUT.landscape), [
    { term: 64, meaning: 48 }, { term: 60, meaning: 44 }, { term: 56, meaning: 40 }, { term: 52, meaning: 40 },
    { term: 48, meaning: 40 },
  ]);
  assert.equal(sizePairs(LAYOUT.portrait).at(-1)?.term, LAYOUT.portrait.term.min);
  assert.equal(sizePairs(LAYOUT.portrait).at(-1)?.meaning, LAYOUT.portrait.meaning.min);
});

test('cardWidth: §4.3\'s (1728 - gutters) / n on the 12-column grid, with the grid\'s own gutter', () => {
  assert.equal(CARD_GUTTER, CATALOGUE.grid.landscape.gutter);
  assert.deepEqual([2, 3, 4].map(cardWidth), [852, 560, 414]);
  for (const n of [2, 3, 4]) {
    assert.ok(cardWidth(n) * n + CARD_GUTTER * (n - 1) <= 1728, `${n} cards fit the grid box`);
  }
  assert.equal(cardWidth(1), 1728);
});

/**
 * preflight.py inside_bands: a box lies inside the union of the critical boxes, each box it overlaps holding its
 * whole width (a 9:16 row may cross y 840, where the upper box ends and the lower begins).
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

test('geometry: the cards sit on the grid box (16:9) and in the text column (9:16), inside the critical zones', () => {
  const land = boxes('landscape', ZONES.landscape);
  assert.deepEqual([land.cards.x0, land.cards.x1, land.bottom], [96, 1824, 900]);
  assert.deepEqual([land.heading.x0, land.heading.x1], [96, 1824]);
  const port = boxes('portrait', ZONES.portrait);
  assert.deepEqual([port.cards.x0, port.cards.x1, port.bottom], [192, 780, 1020], '9:16: the column both critical boxes hold');
  assert.deepEqual([port.heading.x0, port.heading.x1], [192, 888]);
  for (const [fmt, b] of /** @type {const} */ ([['landscape', land], ['portrait', port]])) {
    const zones = ZONES[fmt];
    const pad = LAYOUT[fmt].pad;
    const n = fmt === 'landscape' ? 4 : 1;
    const width = fmt === 'landscape' ? cardWidth(n) : b.cards.x1 - b.cards.x0;
    for (const y of [b.cards.y0 + 120, b.bottom - 200]) {
      for (let k = 0; k < n; k++) {
        const text = { x: b.cards.x0 + k * (width + LAYOUT[fmt].gap) + pad, y, w: width - 2 * pad, h: 120 };
        assert.ok(insideBands(text, zones.critical), `${fmt} card ${k} at y ${y} inside the critical area (R-59)`);
      }
    }
  }
});

test('slotRows: the slot table templates.py mirrors (the narrowest card\'s width in 16:9)', () => {
  const rows = slotRows(ZONES.landscape, ZONES.portrait);
  assert.equal(rows.term.landscape.default.w, cardWidth(4) - 2 * LAYOUT.landscape.pad, '350: the four-term card');
  assert.equal(rows.meaning.landscape.default.w, rows.term.landscape.default.w);
  assert.equal(rows.term.portrait.default.w, 780 - 192 - 2 * LAYOUT.portrait.pad);
  assert.equal(rows.heading.landscape.default.w, 1728);
  assert.equal(rows.heading.portrait.default.w, 696);
  assert.deepEqual(rows.term.landscape.default.size, [LAYOUT.landscape.term.min, LAYOUT.landscape.term.max]);
  assert.deepEqual(rows.meaning.portrait.default.size, [LAYOUT.portrait.meaning.min, LAYOUT.portrait.meaning.max]);
  assert.equal(rows.meaning.portrait.default.weight, LAYOUT.portrait.meaningWeight);
  assert.equal(rows.term.portrait.default.weight, TERM_WEIGHT);
  assert.equal(rows.meaning.landscape.default.max_lines, null, 'the meaning is fitted by the card\'s height');
  assert.equal(rows.heading.portrait.default.prefer_lines, 1);
  assert.deepEqual(Object.keys(rows).sort(), ['heading', 'meaning', 'term']);
  for (const slot of Object.keys(rows)) {
    for (const fmt of Object.keys(/** @type {any} */ (rows)[slot])) {
      assert.deepEqual(Object.keys(/** @type {any} */ (rows)[slot][fmt]), ['default'], `${slot} ${fmt}: no look variant`);
    }
  }
});

test('the catalogue\'s key_terms slots are slotRows exactly once the template is registered (templates.py)', () => {
  const registered = Object.prototype.hasOwnProperty.call(TEMPLATES, 'key_terms');
  const spec = /** @type {any} */ (CATALOGUE.templates).key_terms;
  const cat = /** @type {any} */ (templateSlots('key_terms'));
  const want = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  if (!registered) {
    // not yet in stage.js TEMPLATES: templates.py must not claim it either (test_implemented_matches_the_stage)
    assert.equal(spec.implemented, false, 'templates.py says implemented but stage.js does not register it');
    assert.deepEqual(cat, {}, 'slots belong with the registration');
    return;
  }
  assert.equal(spec.implemented, true);
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
  // the forms per kind (B2c review CG-5): a card for a reveal and for the icon's draw, a term or the card for the cue
  assert.deepEqual(spec.consumes.forms, { reveal: ['item'], draw: ['item'], cue_term: ['item', 'item:term'] });
});

test('mediaBox: none (this template shows no medium)', () => {
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    for (const slot of ['main', 'panel', undefined]) {
      assert.equal(keyTerms.mediaBox?.(/** @type {any} */ ({ board: [] }), ZONES[fmt], 'render', slot, { safe: false, slots: {} }), null);
    }
  }
});

test('drawAt: a draw event on the item moves its icon\'s stroke draw; without one it draws with the card', () => {
  const scene = /** @type {any} */ ({ events: [{ t: 3.5, kind: 'draw', target: 'd3' }, { t: 2.0, kind: 'reveal', target: 'd3' }, { t: 5.0, kind: 'draw', target: 'd3:term:0' }] });
  assert.equal(drawAt(scene, 'd3', 1.0), 3.5, 'the earliest draw event on the item');
  assert.equal(drawAt(scene, 'd1', 1.25), 1.25, 'no event: the card\'s entrance');
  assert.equal(drawAt(/** @type {any} */ ({}), 'd1', 0.5), 0.5);
});

// --- painted behaviour ----------------------------------------------------------------------------------------------

test('16:9: a card per beat (120 ms before it), rising 32 px, its term first and its meaning 80 ms later, in the personality\'s time', async () => {
  const tl = load('key_terms.landscape.json'); // Crisp: baseline_reveal, x0.85
  const scale = tl.presentation.motion_scale;
  const scene = tl.scenes[0];
  const s = spyStage(tl);
  const d1 = enterOf(scene, 'd1');
  assert.ok(Math.abs(d1 - (scene.beats[0].start - 0.12)) < 1e-9, 'R-17: 120 ms before its beat');
  const d2 = enterOf(scene, 'd2');
  await s.seekTo(0, d2 - 0.1);
  let c = cardOf(s.ctx(0), 'd2');
  assert.equal(c.card.style.visibility, 'hidden', 'not yet in');
  assert.equal(op(c.card.style.opacity), 0);
  await s.seekTo(0, d2 + 0.1);
  c = cardOf(s.ctx(0), 'd2');
  assert.ok(Number(c.card.style.opacity) > 0 && Number(c.card.style.opacity) < 1, 'the card is entering');
  assert.match(c.card.style.transform, /^translate\(0, \d+px\)$/, 'a whole-px rise (2D, §P)');
  const dy = Number(/translate\(0, (\d+)px/.exec(c.card.style.transform)?.[1]);
  assert.ok(dy > 0 && dy < RISE, `${dy} px of the 32 px rise left`);
  await s.seekTo(0, d2 + (TEXT_DELAY + ITEM_ENTER) * scale + 0.02);
  c = cardOf(s.ctx(0), 'd2');
  assert.deepEqual([c.card.style.transform, c.termEl.style.transform, c.meaningEl.style.transform], ['none', 'none', 'none']);
  assert.equal(op(c.card.style.opacity), 1);
  assert.ok(c.termMask && c.meaningMask, 'Crisp: each text reveals inside a mask of its own');
  s.done();
  // the 80 ms gap is visible in a fading entrance (Soft, x1.15): the term is in, the meaning is still arriving
  const soft = load('key_terms.portrait.json');
  const softScale = soft.presentation.motion_scale;
  const p = spyStage(soft);
  const p1 = enterOf(soft.scenes[0], 'p1');
  await p.seekTo(0, p1 + ITEM_ENTER * softScale + 0.01);
  const row = cardOf(p.ctx(0), 'p1');
  assert.equal(op(row.termEl.style.opacity), 1, 'the term is in');
  assert.ok(Number(row.meaningEl.style.opacity) < 1, `the meaning is still arriving (${row.meaningEl.style.opacity})`);
  await p.seekTo(0, p1 + (TEXT_DELAY + ITEM_ENTER) * softScale + 0.02);
  assert.equal(op(cardOf(p.ctx(0), 'p1').meaningEl.style.opacity), 1);
  // the term's own entrance is still running after the unscaled 450 ms (Soft's x1.15 stretches it to 518 ms)
  await p.seekTo(0, p1 + ITEM_ENTER + 0.02);
  assert.ok(Number(cardOf(p.ctx(0), 'p1').termEl.style.opacity) < 1);
  p.done();
});

test('the icon\'s strokes draw over 400 ms: pathLength 1 and a dash offset from 1 to 0; a draw event moves it', async () => {
  const tl = load('key_terms.landscape.json');
  const scene = tl.scenes[0];
  const scale = tl.presentation.motion_scale;
  const s = spyStage(tl);
  const d1 = enterOf(scene, 'd1');
  await s.seekTo(0, d1 - 0.05);
  let c = cardOf(s.ctx(0), 'd1');
  assert.ok(c.paths.length > 0, 'the stored icon\'s paths');
  for (const p of c.paths) {
    assert.equal(p.getAttribute('pathLength'), '1');
    assert.equal(p.getAttribute('stroke-dasharray'), '1 1');
    assert.equal(p.getAttribute('stroke-dashoffset'), fx(1, 4), 'nothing drawn before the card enters');
  }
  assert.equal(c.icon.getAttribute('stroke'), 'currentColor');
  assert.equal(c.iconHost.style.width, `${ICON_SIZE}px`, '16:9: the catalogue\'s card icon size, a block above the term');
  await s.seekTo(0, d1 + ICON_DRAW * scale * 0.5);
  c = cardOf(s.ctx(0), 'd1');
  const mid = Number(c.paths[0].getAttribute('stroke-dashoffset'));
  assert.ok(mid > 0 && mid < 1, `drawing (${mid})`);
  await s.seekTo(0, d1 + ICON_DRAW * scale + 0.02);
  c = cardOf(s.ctx(0), 'd1');
  for (const p of c.paths) assert.equal(p.getAttribute('stroke-dashoffset'), fx(0, 4), 'drawn');
  // d3's icon draws on its own `draw` event (5.0 s), not with its card
  const draw = scene.events.find((/** @type {any} */ e) => e.kind === 'draw');
  const d3 = enterOf(scene, 'd3');
  assert.ok(draw.t > d3 + ICON_DRAW);
  await s.seekTo(0, d3 + 0.3);
  assert.equal(cardOf(s.ctx(0), 'd3').paths[0].getAttribute('stroke-dashoffset'), fx(1, 4), 'waits for its draw event');
  await s.seekTo(0, draw.t + ICON_DRAW * scale + 0.02);
  assert.equal(cardOf(s.ctx(0), 'd3').paths[0].getAttribute('stroke-dashoffset'), fx(0, 4));
  s.done();
});

test('9:16: the terms stack in the text column, each with its icon inline before the term', async () => {
  const tl = load('key_terms.portrait.json'); // Soft: fade_rise, x1.15
  const s = spyStage(tl);
  await s.seekTo(0, 6.5);
  const v = s.ctx(0).view;
  assert.equal(v.cards.length, 3);
  const tops = v.cards.map((/** @type {any} */ c) => Number.parseFloat(c.el.style.top));
  assert.deepEqual(tops, [...tops].sort((a, b) => a - b), 'in board order, one under the other');
  assert.ok(tops[0] >= v.cardsTop);
  for (const c of v.cards) {
    assert.equal(Number.parseFloat(c.el.style.left), 192);
    assert.equal(Number.parseFloat(c.el.style.width), 780 - 192);
    assert.equal(c.card.style.padding, '0px', '9:16: rows, not cards (no padding, no surface)');
    assert.equal(c.card.style.background, 'transparent');
    assert.ok(c.iconHost.classList.contains('dyn-kt-icon--inline'));
    assert.equal(c.iconHost.parentElement?.className, 'dyn-kt-termline');
    assert.equal(c.meaningEl.style.fontWeight, String(LAYOUT.portrait.meaningWeight));
    assert.equal(c.termEl.style.fontWeight, String(TERM_WEIGHT));
    // review D2: the icon is 1.05x the term's FITTED size (not 1.05 em of the inherited base), and it shares the
    // term's line box - a flex row carrying the term's own size, so the term is not pushed to the next line
    const size = v.sizes.term;
    const { icon, gap } = inlineIcon(size);
    assert.equal(c.termEl.style.fontSize, `${size}px`);
    assert.ok(icon >= 1.05 * size - 1 && icon <= 1.05 * size + 1, `the icon is ~1.05x the term (${icon} vs ${size})`);
    assert.deepEqual([c.iconHost.style.width, c.iconHost.style.height], [`${icon}px`, `${icon}px`]);
    assert.deepEqual([c.icon.style.width, c.icon.style.height], [`${icon}px`, `${icon}px`], 'the SVG itself, not an em');
    assert.equal(c.iconHost.style.marginRight, `${gap}px`);
    assert.equal(c.termLine.style.display, 'flex', 'icon and term on one line box');
    assert.equal(c.termLine.style.alignItems, 'baseline');
    assert.equal(c.termLine.style.fontSize, `${size}px`);
    assert.deepEqual([...c.termLine.children], [c.iconHost, c.termMask || c.termEl]);
  }
  assert.equal(v.cards[0].termMask, null, 'Soft fades, it does not reveal from the baseline');
  s.done();
});

test('icons are all or nothing: a scene whose terms do not all have one draws none (V12, review D6)', async () => {
  const tl = load('key_terms.landscape.json');
  const i = tl.scenes.findIndex((/** @type {any} */ s) => s.scene_id === 'kt-l-stress');
  const s = spyStage(tl);
  await s.seekTo(i, 3.8);
  const ctx = s.ctx(i);
  const stored = ctx.look.icons || {};
  const v = ctx.view;
  assert.ok(v.cards.length > 1 && v.cards.some((/** @type {any} */ c) => stored[c.item.id])
    && v.cards.some((/** @type {any} */ c) => !stored[c.item.id]), 'the fixture stores an icon for some terms only');
  for (const c of v.cards) {
    assert.equal(c.iconHost, null, `${c.item.id}: no card takes an icon when one term lacks it`);
    assert.deepEqual(c.paths, []);
  }
  const tops = new Set(v.cards.map((/** @type {any} */ c) => c.termEl.getBoundingClientRect().top));
  assert.equal(tops.size, 1, 'so every term in the row starts at the same height');
  s.done();
});

test('16:9 cards: one surface card per term, equal width and height, the row centred under the heading', async () => {
  const tl = load('key_terms.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(0, 5.5);
  const ctx = s.ctx(0);
  const v = ctx.view;
  const width = cardWidth(3);
  v.cards.forEach((/** @type {any} */ c, k) => {
    assert.equal(Number.parseFloat(c.el.style.left), 96 + k * (width + CARD_GUTTER));
    assert.equal(Number.parseFloat(c.el.style.width), width);
    assert.equal(c.card.style.background, rgb(ctx.roles.color('surface')));
    assert.equal(c.card.style.padding, `${LAYOUT.landscape.pad}px`);
    assert.equal(c.card.style.borderRadius, '24px');
  });
  const heights = new Set(v.cards.map((/** @type {any} */ c) => c.card.style.height));
  assert.equal(heights.size, 1, 'every card the tallest card\'s height');
  const tops = new Set(v.cards.map((/** @type {any} */ c) => c.el.style.top));
  assert.equal(tops.size, 1, 'the row shares one top');
  assert.ok(Number.parseFloat([...tops][0]) >= v.cardsTop);
  s.done();
});

test('a reveal event wins over the card\'s beat (state.js itemTimings)', async () => {
  const tl = load('key_terms.portrait.json');
  const scene = tl.scenes[0];
  scene.events = [...scene.events, { t: 5.5, kind: 'reveal', target: 'p2', anchored: true }];
  assert.equal(enterOf(scene, 'p2'), 5.5);
  const s = spyStage(tl);
  await s.seekTo(0, scene.beats[1].start + 0.05);
  assert.equal(cardOf(s.ctx(0), 'p2').card.style.visibility, 'hidden', 'not on its beat');
  await s.seekTo(0, 5.8);
  assert.equal(cardOf(s.ctx(0), 'p2').card.style.visibility, 'visible');
  s.done();
});

test('the personality\'s text entrance and scale: Crisp reveals from a mask at x0.85, Soft fades with the 16 px rise', async () => {
  const crisp = load('key_terms.landscape.json');
  assert.equal(crisp.presentation.text_entrance, 'baseline_reveal');
  let s = spyStage(crisp);
  const d1 = enterOf(crisp.scenes[0], 'd1');
  await s.seekTo(0, d1 + 0.15);
  let c = cardOf(s.ctx(0), 'd1');
  assert.ok(c.termMask && c.termEl.parentElement === c.termMask && c.termMask.classList.contains('dyn-reveal-mask'));
  assert.equal(c.termEl.style.opacity, '1', 'a baseline reveal moves, it does not fade');
  assert.ok(s.ctx(0).view.head.mask, 'the heading too');
  s.done();
  const soft = load('key_terms.portrait.json');
  s = spyStage(soft);
  const p1 = enterOf(soft.scenes[0], 'p1');
  await s.seekTo(0, p1 + 0.15);
  c = cardOf(s.ctx(0), 'p1');
  assert.equal(c.termMask, null);
  assert.ok(Number(c.termEl.style.opacity) < 1);
  s.done();
  assert.ok(RISE > TEXT_RISE, 'the card\'s rise (§4.3: 32 px) is the template\'s own, not the 16 px text rise');
  assert.equal(HEADING_ENTER, ITEM_ENTER);
});

test('reduced motion (live player): cards and text fade in place', async () => {
  const tl = load('key_terms.landscape.json');
  tl.presentation.text_entrance = 'fade_rise';
  const s = spyStage(tl, { mode: 'live', reducedMotion: true });
  await s.seekTo(0, enterOf(tl.scenes[0], 'd1') + 0.1);
  const c = cardOf(s.ctx(0), 'd1');
  assert.equal(c.card.style.transform, 'none');
  assert.equal(c.termEl.style.transform, 'none');
  assert.equal(c.meaningEl.style.transform, 'none');
  assert.ok(Number(c.card.style.opacity) > 0 && Number(c.card.style.opacity) < 1);
  s.done();
});

test('cue.term: the stage marks a card\'s [[term]] through itemEl, in the term and in the meaning; <item>:term:<n> picks one', async () => {
  const tl = load('key_terms.landscape.json');
  const cue = tl.scenes[0].events.find((/** @type {any} */ e) => e.kind === 'cue_term');
  const s = spyStage(tl);
  await s.seekTo(0, cue.t + 0.5);
  const ctx = s.ctx(0);
  const els = /** @type {Element[]} */ (keyTerms.itemEl?.(ctx, 'd1'));
  assert.deepEqual(els, [cardOf(ctx, 'd1').termEl, cardOf(ctx, 'd1').meaningEl], 'field order: term, then meaning');
  const span = /** @type {HTMLElement} */ (els[0].querySelector('.rt-k'));
  assert.equal(span.textContent, 'Voltage');
  assert.match(span.style.backgroundImage, /linear-gradient/, 'the stage drew the marker on the term');
  assert.equal(keyTerms.itemEl?.(ctx, 'h1'), ctx.view.head.text, 'the heading is an item too');
  assert.equal(keyTerms.itemEl?.(ctx, 'nope'), null);
  // a card whose meaning holds the [[term]] (d2): the cue reaches it through the second element
  const second = tl.scenes[0].events.filter((/** @type {any} */ e) => e.kind === 'cue_term')[1];
  await s.seekTo(0, second.t + 0.5);
  const ctx2 = s.ctx(0);
  const flow = /** @type {HTMLElement} */ (cardOf(ctx2, 'd2').meaningEl.querySelector('.rt-k'));
  assert.equal(flow.textContent, 'flow');
  assert.match(flow.style.backgroundImage, /linear-gradient/);
  // <item>:term:<n> counts the terms of both fields in field order (cues.js termPieces over itemEl)
  const two = load('key_terms.landscape.json');
  two.scenes[0].board[1].term = '[[Voltage]]';
  two.scenes[0].board[1].text = 'The [[electric push]] on charge';
  const s2 = spyStage(two);
  await s2.seekTo(0, 2);
  const target = 'd1:term:1';
  const pieces = termPieces(keyTerms, s2.ctx(0), /** @type {any} */ ({ kind: 'cue_term', target, parsed: parseTarget(target), start: 0, hold: 1, order: 0 }));
  assert.deepEqual(pieces.map((p) => p.span.textContent), ['electric', ' ', 'push'], 'the second term, in word pieces');
  assert.deepEqual(pieces.map((p) => fx(p.to, 2)), ['0.62', '0.69', '1.00'], 'swept left to right across its pieces');
  s.done();
  s2.done();
});

test('focusRect and dimGroups: the card (16:9 a shape) or the 9:16 row, on whole px, with its dim group', async () => {
  const tl = load('key_terms.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(0, 5.5);
  const real = s.ctx(0);
  const card = cardOf(real, 'd2');
  const rects = new Map([[card.card, { x: 680.4, y: 262.6, w: 560.2, h: 396.4 }], [real.view.head.text, { x: 96, y: 112, w: 1728, h: 110.4 }]]);
  const ctx = { ...real, rectOf: (/** @type {Element} */ el) => rects.get(el) || null };
  assert.deepEqual(keyTerms.focusRect?.(ctx, 'd2'), { rect: { x: 680, y: 263, w: 561, h: 396 }, shape: 'rect', radius: 24, el: card.el, kind: 'shape' });
  assert.deepEqual(keyTerms.focusRect?.(ctx, 'd2:term:0'), keyTerms.focusRect?.(ctx, 'd2'), 'a term target points at its card');
  assert.deepEqual(keyTerms.focusRect?.(ctx, 'h1'), { rect: { x: 96, y: 112, w: 1728, h: 110 }, shape: 'rect', radius: 0, el: real.view.head.el, kind: 'text' });
  assert.equal(keyTerms.focusRect?.(ctx, 'zz'), null);
  const groups = /** @type {HTMLElement[]} */ (keyTerms.dimGroups?.(real));
  assert.deepEqual(groups, [real.view.head.el, ...real.view.cards.map((/** @type {any} */ c) => c.el)]);
  for (const g of groups) assert.equal(g.style.opacity, '', 'no cue dims anything here (key_terms draws no look_here)');
  s.done();
  // 9:16: a row is text, so its outline would be clamped to the critical text boxes, not the shapes area
  const port = spyStage(load('key_terms.portrait.json'));
  await port.seekTo(0, 6.5);
  const pctx = port.ctx(0);
  const row = cardOf(pctx, 'p1');
  const one = { ...pctx, rectOf: (/** @type {Element} */ el) => (el === row.card ? { x: 192, y: 500, w: 588, h: 140 } : null) };
  assert.deepEqual(keyTerms.focusRect?.(one, 'p1'), { rect: { x: 192, y: 500, w: 588, h: 140 }, shape: 'rect', radius: 0, el: row.el, kind: 'text' });
  port.done();
});

test('texts(): the heading, every term and every meaning, critical, at the size and weight painted now', async () => {
  const tl = load('key_terms.landscape.json');
  const scene = tl.scenes[0];
  const s = spyStage(tl);
  await s.seekTo(0, enterOf(scene, 'd3') + 1.0);
  const ctx = s.ctx(0);
  const v = ctx.view;
  const entries = /** @type {any[]} */ (keyTerms.texts?.(ctx));
  const head = entries[0];
  assert.deepEqual([head.role, head.slot, head.critical, head.weight], ['title', 'heading', true, TERM_WEIGHT]);
  assert.equal(head.text, 'Three words to know first');
  assert.equal(head.bg, ctx.onBackground.bg, 'the heading sits on the background');
  const term = entries.find((e) => e.el === cardOf(ctx, 'd1').termEl);
  const meaning = entries.find((e) => e.el === cardOf(ctx, 'd1').meaningEl);
  assert.deepEqual([term.role, term.slot, term.size, term.weight, term.alpha], ['title', 'term', v.sizes.term, TERM_WEIGHT, 1]);
  assert.equal(term.text, 'Voltage', 'plain text, the [[term]] markup resolved');
  assert.deepEqual([meaning.role, meaning.slot, meaning.size, meaning.weight], ['body', 'meaning', v.sizes.meaning, LAYOUT.landscape.meaningWeight]);
  assert.equal(meaning.text, 'The push that moves charge');
  for (const e of [term, meaning]) {
    assert.equal(e.bg, ctx.roles.color('surface'), '16:9: the card\'s surface');
    assert.equal(e.min, e.slot === 'term' ? LAYOUT.landscape.term.min : LAYOUT.landscape.meaning.min);
  }
  for (const e of entries) {
    assert.equal(e.critical, true);
    assert.match(String(e.ink), /^#[0-9a-f]{6}$/i);
    assert.ok(e.alpha >= 0 && e.alpha <= 1);
  }
  assert.equal(entries.length, 1 + 2 * v.cards.length);
  s.done();
  // 9:16 reports the background (no card surface), and a card with no meaning reports no meaning entry
  const port = load('key_terms.portrait.json');
  port.scenes[0].board[3].text = '';
  const p = spyStage(port);
  await p.seekTo(0, 6.5);
  const pe = /** @type {any[]} */ (keyTerms.texts?.(p.ctx(0)));
  assert.equal(pe.every((e) => e.bg === p.ctx(0).onBackground.bg), true);
  assert.equal(pe.filter((e) => e.slot === 'meaning').length, 2, 'the emptied meaning reports nothing');
  p.done();
});

test('preflight\'s remedies: a slot size cap and safe hold the text small; a 9:16 row takes the scrim behind it', async () => {
  const tl = load('key_terms.landscape.json');
  tl.scenes[0].look.fixes = { safe: false, slots: { term: { size: 52 }, meaning: { size: 44 }, heading: { scrim: true } } };
  let s = spyStage(tl);
  await s.seekTo(0, 3);
  let ctx = s.ctx(0);
  assert.deepEqual(ctx.view.sizes, { term: 52, meaning: 44 }, 'the caps (jsdom fits at the largest size allowed)');
  const scrim = ctx.roles.color('scrim');
  assert.equal(ctx.view.head.scrim, scrim);
  assert.equal(ctx.view.rowScrim, null, '16:9 cards are opaque: no scrim behind them');
  const entries = /** @type {any[]} */ (keyTerms.texts?.(ctx));
  const head = entries[0];
  assert.equal(head.bg, scrimmedBg(scrim, ctx.onBackground, head.ink), 'measured on the scrim over the background');
  assert.notEqual(head.bg, ctx.onBackground.bg);
  s.done();
  const port = load('key_terms.portrait.json');
  port.scenes[0].look.fixes = { safe: false, slots: { meaning: { scrim: true } } };
  s = spyStage(port);
  await s.seekTo(0, 6.5);
  ctx = s.ctx(0);
  assert.equal(ctx.view.rowScrim, ctx.roles.color('scrim'));
  for (const c of ctx.view.cards) assert.ok(!['', 'transparent'].includes(c.card.style.background), 'the scrim behind each row');
  const row = /** @type {any[]} */ (keyTerms.texts?.(ctx)).find((e) => e.slot === 'term');
  assert.equal(row.bg, scrimmedBg(ctx.roles.color('scrim'), ctx.onBackground, row.ink));
  s.done();
  const safe = load('key_terms.portrait.json');
  safe.scenes[0].look.fixes = { safe: true, slots: {} };
  s = spyStage(safe);
  await s.seekTo(0, 6.5);
  ctx = s.ctx(0);
  assert.deepEqual(ctx.view.sizes, { term: LAYOUT.portrait.term.min, meaning: LAYOUT.portrait.meaning.min }, 'safe: the minimums');
  assert.equal(ctx.view.head.size, LAYOUT.portrait.heading.min);
  s.done();
});

test('update writes every property on every frame with a value (no \'\': a cleared property would make the DOM order-dependent)', async () => {
  const proto = /** @type {any} */ (Painter.prototype);
  const real = proto.style;
  const realAttr = proto.attr;
  /** @type {string[]} */
  const empty = [];
  let inside = false;
  const wrapped = { ...keyTerms, update(/** @type {any} */ ctx, /** @type {number} */ t, /** @type {any} */ st) { inside = true; try { keyTerms.update(ctx, t, st); } finally { inside = false; } } };
  proto.style = function style(/** @type {any} */ el, /** @type {string} */ prop, /** @type {any} */ value) {
    if (inside && String(value) === '') empty.push(`${el.className} ${prop}`);
    return real.call(this, el, prop, value);
  };
  proto.attr = function attr(/** @type {any} */ el, /** @type {string} */ name, /** @type {any} */ value) {
    if (inside && (value === null || String(value) === '')) empty.push(`@${name}`);
    return realAttr.call(this, el, name, value);
  };
  try {
    for (const name of FIXTURES) {
      const tl = load(name);
      const { stage, done } = renderStage(tl, { templates: { key_terms: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex });
      const total = Math.round(tl.total_duration * tl.fps);
      for (let f = 0; f < total; f += 7) await stage.seek(f);
      done();
    }
  } finally {
    proto.style = real;
    proto.attr = realAttr;
  }
  assert.deepEqual([...new Set(empty)], []);
});

// --- the fit, on a fake text layout ---------------------------------------------------------------------------------

/**
 * Install a fake text layout on HTMLElement (jsdom has none): a glyph is 0.55 em wide, words wrap greedily, a word
 * wider than its box breaks across lines. Enough for the cards' fit: a card's (or its clone's) height from its icon,
 * its two text sizes, its padding and its width, a word's height, the text box's width and the heading's height.
 * Returns the restore function.
 */
function fakeLayout() {
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const keys = ['offsetHeight', 'offsetWidth', 'clientWidth', 'offsetTop'];
  const saved = keys.map((k) => [k, Object.getOwnPropertyDescriptor(proto, k)]);
  const CW = 0.55;
  // the stage's own class: a clone in the measuring host hangs outside the template's `.dyn-kt` root
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
  /** A px width only: the real card is `width: 100%` and takes its box from its group (a clone has px). @param {HTMLElement | null} el */
  const pxWidth = (el) => (el && /px$/.test(el.style.width) ? Number.parseFloat(el.style.width) : 0);
  /** @param {HTMLElement} card */
  const cardInner = (card) => (pxWidth(card) || pxWidth(card.parentElement)) - 2 * LAYOUT[fmt(card)].pad;
  /** @param {HTMLElement} el @param {number} boxW */
  const lines = (el, boxW) => {
    const size = fontSize(el);
    let n = 1;
    let x = 0;
    for (const w of el.querySelectorAll('.dyn-word')) {
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
  const boxOf = (el) => {
    const card = /** @type {HTMLElement | null} */ (el.closest('.dyn-kt-card'));
    if (card) return cardInner(card);
    const head = /** @type {HTMLElement | null} */ (el.closest('.dyn-kt-heading-group'));
    return head ? Number.parseFloat(head.style.width) || 0 : 0;
  };
  Object.defineProperty(proto, 'offsetHeight', {
    configurable: true,
    get() {
      const el = /** @type {HTMLElement} */ (this);
      if (el.classList.contains('dyn-word')) {
        const ww = (el.textContent || '').length * CW * fontSize(el);
        const box = boxOf(el);
        const host = /** @type {HTMLElement} */ (el.closest('.dyn-kt-term, .dyn-kt-meaning, .dyn-kt-heading'));
        return (box > 0 && ww > box ? Math.ceil(ww / box) : 1) * lineH(host);
      }
      if (el.classList.contains('dyn-kt-card')) {
        const L = LAYOUT[fmt(el)];
        const box = cardInner(el);
        const term = /** @type {HTMLElement} */ (el.querySelector('.dyn-kt-term'));
        const meaning = /** @type {HTMLElement} */ (el.querySelector('.dyn-kt-meaning'));
        const icon = el.querySelector('.dyn-kt-icon--block') ? ICON_SIZE + L.iconGap : 0;
        return 2 * L.pad + icon + lines(term, box) * lineH(term) + L.textGap + lines(meaning, box) * lineH(meaning);
      }
      if (el.classList.contains('dyn-kt-heading')) return lines(el, boxOf(el)) * lineH(el);
      if (el.classList.contains('dyn-kt-heading-group')) {
        const t = /** @type {HTMLElement} */ (el.querySelector('.dyn-kt-heading'));
        return lines(t, boxOf(t)) * lineH(t);
      }
      return 0;
    },
  });
  Object.defineProperty(proto, 'offsetTop', { configurable: true, get() { return Number.parseFloat(/** @type {HTMLElement} */ (this).style.top) || 0; } });
  Object.defineProperty(proto, 'offsetWidth', { configurable: true, get() { return (/** @type {HTMLElement} */ (this).textContent || '').length * CW * fontSize(this); } });
  Object.defineProperty(proto, 'clientWidth', { configurable: true, get() { return boxOf(this); } });
  return () => {
    for (const [k, d] of saved) {
      if (d) Object.defineProperty(proto, /** @type {string} */ (k), /** @type {PropertyDescriptor} */ (d));
      else delete proto[/** @type {string} */ (k)];
    }
  };
}

/**
 * A one-scene timeline of `fmt` whose cards are `terms` ([term, meaning] pairs), each revealed by a beat.
 * @param {'landscape' | 'portrait'} fmt @param {[string, string][]} terms
 * @param {{heading?: string | null}} [opts]
 */
function ktTimeline(fmt, terms, opts = {}) {
  const tl = load(fmt === 'portrait' ? 'key_terms.portrait.json' : 'key_terms.landscape.json');
  tl.scenes.length = 1;
  const scene = tl.scenes[0];
  const heading = opts.heading === undefined ? 'Three words to know first' : opts.heading;
  scene.board = [...(heading ? [{ id: 'h1', kind: 'heading', text: heading }] : []),
    ...terms.map(([term, meaning], i) => ({ id: `t${i}`, kind: 'definition', term, text: meaning }))];
  scene.beats = terms.map((_, i) => ({ beat_id: `k${i}`, index: i, phase: 'main', start: 0.5 + i * 1.5, speech_end: 1.8 + i * 1.5, end: 2 + i * 1.5, narration: 'x', board_item_id: `t${i}`, captions: [] }));
  scene.events = [];
  scene.duration = 1.5 * terms.length + 1.3;
  scene.look.icons = {};
  tl.total_duration = scene.duration;
  return tl;
}

test('fit (16:9): the largest size pair at which the tallest card fits under the heading; smaller for fuller cards', async () => {
  const restore = fakeLayout();
  try {
    const short = spyStage(ktTimeline('landscape', [['Voltage', 'The push on charge'], ['Current', 'The flow of charge']]));
    await short.seekTo(0, 4);
    const v = short.ctx(0).view;
    assert.deepEqual(v.sizes, { term: LAYOUT.landscape.term.max, meaning: LAYOUT.landscape.meaning.max });
    // "Three words to know first" (25 glyphs) on one line of the 1728 px band at 96 px, then the 40 px gap
    assert.equal(v.head.size, LAYOUT.landscape.heading.max);
    assert.equal(v.cardsTop, Math.round(112 + 96 * LINE_HEIGHT.heading.latin + LAYOUT.landscape.headingGap));
    for (const c of v.cards) assert.equal(c.overflow, 0);
    short.done();
    // four cards of 350 px text: a long meaning takes several lines, so the pair steps down
    const one = 'Maximum wattage when warm motors meet heavy loads';
    const full = `${one} ${one}`;
    const many = spyStage(ktTimeline('landscape', /** @type {[string, string][]} */ (
      [['Wide wattage words', full], ['Second term here', full], ['Third term here', full], ['Fourth term here', full]])));
    await many.seekTo(0, 5);
    const w = many.ctx(0).view;
    assert.ok(w.sizes.term < LAYOUT.landscape.term.max || w.sizes.meaning < LAYOUT.landscape.meaning.max,
      `the pair stepped down (${JSON.stringify(w.sizes)})`);
    assert.ok(w.cards.every((/** @type {any} */ c) => c.overflow === 0), 'it fits');
    many.done();
    // a meaning nothing can fit: the minimum pair, and the cards report their overflow (preflight: "Shorten text")
    const huge = `${full} ${full} ${full} ${full} ${full} ${full}`;
    const over = spyStage(ktTimeline('landscape', [['One', huge], ['Two', huge]]));
    await over.seekTo(0, 4);
    const o = over.ctx(0).view;
    assert.deepEqual(o.sizes, { term: LAYOUT.landscape.term.min, meaning: LAYOUT.landscape.meaning.min }, 'at the floors');
    assert.ok(o.cards[0].overflow > 0, 'reported, never clipped silently');
    const entries = /** @type {any[]} */ (keyTerms.texts?.(over.ctx(0)));
    assert.ok(entries.filter((e) => e.slot === 'meaning').every((e) => e.overflow > 0));
    over.done();
  } finally {
    restore();
  }
});

test('fit: a word wider than the card even at the floor does not cost the cards their size (board_generic\'s rule)', async () => {
  const restore = fakeLayout();
  try {
    const s = spyStage(ktTimeline('landscape', [['Long word', 'Pneumonoultramicroscopicsilicovolcanoconiosis'], ['Short', 'Fine']]));
    await s.seekTo(0, 4);
    assert.deepEqual(s.ctx(0).view.sizes, { term: LAYOUT.landscape.term.max, meaning: LAYOUT.landscape.meaning.max });
    s.done();
  } finally {
    restore();
  }
});

test('fit (9:16): the rows stack from the heading down at the largest pair that fits the column; overflow is reported', async () => {
  const restore = fakeLayout();
  try {
    const s = spyStage(ktTimeline('portrait', [['Voltage', 'The push on charge'], ['Current', 'The flow of charge'],
      ['Resistance', 'What slows the flow']]));
    await s.seekTo(0, 5.5);
    const v = s.ctx(0).view;
    const avail = 1020 - v.cardsTop;
    assert.ok(v.sizes.term >= LAYOUT.portrait.term.min && v.sizes.meaning >= LAYOUT.portrait.meaning.min);
    for (const c of v.cards) assert.equal(c.overflow, 0, c.item.id);
    // each row's top is the rows before it plus the gap
    assert.equal(v.cards[1].top - v.cards[0].top, v.cards[0].height + LAYOUT.portrait.gap);
    assert.equal(Number.parseFloat(v.cards[1].el.style.top), v.cardsTop + v.cards[1].top);
    assert.ok(v.cards.at(-1).top + v.cards.at(-1).height <= avail);
    s.done();
    // four terms with full 8-word meanings do not fit a reel at the floors (key_terms.js's header): reported, blocked
    const full = 'Maximum wattage when warm motors meet heavy loads';
    const over = spyStage(ktTimeline('portrait', /** @type {[string, string][]} */ (
      [['One term', full], ['Two terms', full], ['Three terms', full], ['Four terms', full]])));
    await over.seekTo(0, 6);
    const o = over.ctx(0).view;
    assert.deepEqual(o.sizes, { term: LAYOUT.portrait.term.min, meaning: LAYOUT.portrait.meaning.min });
    assert.ok(o.cards.at(-1).overflow > 0, 'the last rows report their overflow');
    assert.equal(o.cards[0].overflow, 0);
    over.done();
  } finally {
    restore();
  }
});

