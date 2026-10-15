// @ts-check
// templates/recap_retrieval.js (DYNAMIC_VIDEO_DESIGN §4.3 recap_retrieval, B2 plan §1.4 template contract, §1.5 cues
// and item states, §1.6/§1.11 slots, §P): the four fixture timelines
// (tests/compose/dynamic/make_recap_retrieval_fixtures.py) through the harness's contract and order gates, the pure
// helpers (data, timetable, geometry, slots, TeX), the painted behaviour in both formats (the prompt sequence, the
// think ring, the answer and its `done` state, the tile flip, icons, the personality's entrance and scale, reduced
// motion, the cues the stage draws from the template's geometry, preflight's text entries and remedies) and the fit,
// on a fake text layout (jsdom has none).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { Painter, TYPE_FLOORS, ZONES, fx, parseTarget, templateSlots } from '../../../js/dynamic/core.js';
import { FOCUS_OUTSET, FOCUS_PX, clampOf, outlinePath, shapeArea, termPieces } from '../../../js/dynamic/cues.js';
import { itemTimings } from '../../../js/dynamic/state.js';
import { scrimmedBg } from '../../../js/dynamic/templates/_shared.js';
import recapRetrieval, {
  ANSWER_RISE, CARD_ENTER, CARD_EXIT, CARD_RISE, DRAWN_KINDS, ICON_SIZE, LAYOUT, LINE_HEIGHT, RING_DELAY, RING_MIN,
  TEXT_DELAY, THINK_DEFAULT, TILE_FLIP, boxes, drawsScene, eventFor, lineHeight, recapItems, slotRows, texOf,
  tileRows, tileTextWidth, tileWidth, timetable,
} from '../../../js/dynamic/templates/recap_retrieval.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['recap_retrieval.landscape.json', 'recap_retrieval.landscape.tamil.json',
  'recap_retrieval.portrait.json', 'recap_retrieval.portrait.hindi.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { recap_retrieval: recapRetrieval } };

/**
 * A render stage of `tl` with recap_retrieval through the stage's test seam, and the context each scene was built with.
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...recapRetrieval, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return recapRetrieval.build(ctx); } };
  const run = renderStage(tl, { templates: { recap_retrieval: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
  /** Seek scene `i` to the frame nearest scene time `t`. @param {number} i @param {number} t */
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
const promptOf = (/** @type {any} */ ctx, /** @type {string} */ id) => ctx.view.prompts.find((/** @type {any} */ x) => x.p.item.id === id);
const tileOf = (/** @type {any} */ ctx, /** @type {string} */ id) => ctx.view.tiles.find((/** @type {any} */ x) => x.item.id === id);
/** A hex colour as the DOM reports a painted one (`rgb(r, g, b)`). @param {string} hex */
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;
/** An opacity as a number ('' is 1: none painted). @param {string} v */
const op = (v) => (v === '' ? 1 : Number(v));

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, whole-px focus, terms, TeX, entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(recapRetrieval, load(name)), [], name);
});

test('every fixture paints the same frame forward, in reverse and on a fresh page (a frame is a pure function of t)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const total = Math.round(tl.total_duration * tl.fps);
    // every 11th frame plus the frames around each event, each beat and each crossfade's start
    const frames = new Set();
    for (let f = 0; f < total; f += 11) frames.add(f);
    for (const s of tl.scenes) {
      for (const e of s.events || []) for (const d of [0, 0.2, 0.5]) frames.add(Math.min(total - 1, Math.round((s.start + e.t + d) * tl.fps)));
      for (const b of s.beats || []) frames.add(Math.min(total - 1, Math.round((s.start + b.start) * tl.fps)));
      frames.add(Math.min(total - 1, Math.round(s.start * tl.fps) + 3));
    }
    assert.deepEqual(await orderProblems(tl, [...frames].sort((a, b) => a - b), SEAM), [], name);
  }
});

test('the fixtures cover both formats, dark and light families, every text case and every event kind the template reads', () => {
  const seen = { formats: new Set(), modes: new Set(), entrances: new Set(), kinds: new Set(), scripts: new Set(), rings: new Set(), icons: new Set() };
  for (const name of FIXTURES) {
    const tl = load(name);
    seen.formats.add(tl.presentation.format);
    seen.modes.add(tl.presentation.mode);
    seen.entrances.add(tl.presentation.text_entrance);
    for (const s of tl.scenes) {
      for (const e of s.events || []) seen.kinds.add(e.kind);
      const { prompts, tiles } = recapItems(s);
      const plan = timetable(s, prompts, tiles, recapItems(s).label);
      for (const p of prompts) seen.rings.add(plan.prompts.get(p.item.id)?.ring ? 'ring' : 'no-ring');
      seen.icons.add(tiles.every((i) => s.look.icons && s.look.icons[i.id]) ? 'icons' : 'none');
      const text = [...(s.board || [])].map((/** @type {any} */ b) => `${b.text} ${b.justification || ''}`).join(' ');
      if (/\p{Script=Tamil}/u.test(text)) seen.scripts.add('tamil');
      if (/\p{Script=Devanagari}/u.test(text)) seen.scripts.add('devanagari');
      if (/\$[^$]+\$/.test(text)) seen.scripts.add('tex');
      if (/[A-Za-z]{40,}/.test(text)) seen.scripts.add('long-word');
      if (/(?:\b[A-Z]{3,}\b.*){4}/.test(text)) seen.scripts.add('wide-caps');
    }
  }
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.entrances].sort(), ['baseline_reveal', 'fade_rise']);
  assert.deepEqual([...seen.kinds].sort(), ['answer', 'countdown', 'cue_term']);
  assert.deepEqual([...seen.scripts].sort(), ['devanagari', 'long-word', 'tamil', 'tex', 'wide-caps']);
  assert.deepEqual([...seen.rings].sort(), ['no-ring', 'ring'], 'a think time too short for a ring, and one long enough');
  assert.deepEqual([...seen.icons].sort(), ['icons', 'none'], 'tiles with a stored icon each, and tiles with none');
});

// --- pure helpers ---------------------------------------------------------------------------------------------------

test('recapItems: the first heading is the label, questions and paragraphs are prompts (a justification is the answer), takeaways are tiles', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'h', kind: 'heading', text: 'Recap' },
    { id: 'q1', kind: 'question', text: 'Why?', justification: 'Because.' },
    { id: 'q2', kind: 'question', text: 'Name them.' },
    { id: 'q3', kind: 'question', text: 'Blank?', justification: '   ' },
    { id: 'p1', kind: 'paragraph', text: 'Say the big ideas.' },
    { id: 'e', kind: 'takeaway', text: '  ' },
    { id: 'h2', kind: 'heading', text: 'Second' },
    { id: 't1', kind: 'takeaway', text: 'One' },
  ] });
  const { label, prompts, tiles } = recapItems(scene);
  assert.equal(label?.id, 'h');
  assert.deepEqual(prompts.map((p) => [p.item.id, p.answer]), [['q1', 'Because.'], ['q2', null], ['q3', null], ['p1', null]]);
  assert.deepEqual(tiles.map((t) => t.id), ['t1'], 'an empty takeaway is not a tile');
  assert.deepEqual(recapItems(/** @type {any} */ ({ board: [] })), { label: null, prompts: [], tiles: [] });
});

test('drawsScene: a question with text, at most one heading, and only the four kinds §4.3 names', () => {
  const q = { id: 'q1', kind: 'question', text: 'Why?', justification: 'Because.' };
  assert.ok(drawsScene(/** @type {any} */ ({ board: [q] })));
  assert.ok(drawsScene(/** @type {any} */ ({ board: [{ id: 'h', kind: 'heading', text: 'R' }, q, { id: 't', kind: 'takeaway', text: 'One' }] })));
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [] })), 'no question');
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [{ id: 'q', kind: 'question', text: ' ' }] })), 'no question with text');
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [q, { id: 'h1', kind: 'heading', text: 'A' }, { id: 'h2', kind: 'heading', text: 'B' }] })), 'two headings');
  for (const kind of ['bullet', 'definition', 'formula', 'code', 'table', 'figure', 'stat', 'misconception', 'quote']) {
    assert.ok(!drawsScene(/** @type {any} */ ({ board: [q, { id: 'x', kind, text: 'x' }] })), kind);
  }
  assert.deepEqual([...DRAWN_KINDS].sort(), ['heading', 'paragraph', 'question', 'takeaway']);
  // review D5: this template plans no media box, so a scene with a medium beside the recap is board_generic's whole
  // (the Python `_fit_recap_retrieval` carries the same guard, or the panel's picture is fetched by no one)
  const panel = { kind: 'image', title: 'A lamp' };
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [q], side_panel: { panel } })), 'a shown side panel');
  assert.ok(drawsScene(/** @type {any} */ ({ board: [q], side_panel: { panel }, layout: { show_side_panel: false } })),
    'a hidden one is not shown, so it is still this template\'s');
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [q], media: { asset_key: 'a' } })), 'a scene medium');
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [q], poster: { asset_key: 'a' } })), 'a poster');
});

test('a scene this template does not draw is drawn by board_generic whole, every hook handed on', async () => {
  for (const name of ['recap_retrieval.landscape.json', 'recap_retrieval.portrait.json']) {
    const tl = load(name);
    tl.scenes[0].board.push({ id: 'x1', kind: 'bullet', text: 'A bullet no recap draws' });
    const fmt = tl.presentation.format;
    assert.ok(!drawsScene(tl.scenes[0]));
    const run = renderStage(tl, { templates: { recap_retrieval: recapRetrieval }, renderTex: recordingTex().renderTex });
    await run.stage.seek(30);
    assert.ok(run.stage.el.querySelector('.dyn-board'), `${name}: board_generic drew it`);
    assert.equal(run.stage.el.querySelector('.dyn-rr'), null);
    assert.deepEqual(recapRetrieval.tex?.(tl.scenes[0], tl.scenes[0].look, fmt), boardGeneric.tex?.(tl.scenes[0], tl.scenes[0].look, fmt));
    const zones = ZONES[fmt];
    assert.deepEqual(recapRetrieval.mediaBox?.(tl.scenes[0], zones, 'render', 'panel', { safe: false, slots: {} }),
      boardGeneric.mediaBox?.(tl.scenes[0], zones, 'render', 'panel', { safe: false, slots: {} }));
    run.done();
  }
});

test('timetable: a prompt leaves when the next prompt or the first tile enters; its answer comes from an event, else the fill, else the think default', () => {
  const tl = load('recap_retrieval.landscape.json');
  const scene = tl.scenes[0];
  const { label, prompts, tiles } = recapItems(scene);
  const plan = timetable(scene, prompts, tiles, label);
  assert.deepEqual(plan.order, ['q1', 'q2', 'r1'], 'prompts in entrance order');
  const q1 = /** @type {any} */ (plan.prompts.get('q1'));
  const q2 = /** @type {any} */ (plan.prompts.get('q2'));
  const r1 = /** @type {any} */ (plan.prompts.get('r1'));
  assert.equal(q1.enter, enterOf(scene, 'q1'));
  assert.equal(q1.until, enterOf(scene, 'q2'), 'until the next prompt enters');
  assert.equal(q1.answerAt, itemTimings(scene).find((x) => x.id === 'q1')?.fill, 'the beat that fills it');
  // q2 is answered by an `answer` event, not by a beat
  const ev = scene.events.find((/** @type {any} */ e) => e.kind === 'answer');
  assert.equal(itemTimings(scene).find((/** @type {any} */ x) => x.id === 'q2')?.fill, null);
  assert.equal(q2.answerAt, ev.t);
  assert.deepEqual(eventFor(scene, 'answer', 'q2'), { t: ev.t, dur: null });
  assert.equal(eventFor(scene, 'answer', 'q1'), null);
  // the free-recall prompt has no answer; its ring runs until the first tile enters
  assert.equal(r1.answerAt, null);
  assert.equal(r1.until, Math.min(...['t1', 't2', 't3'].map((id) => enterOf(scene, id))));
  assert.equal(r1.ring.start, r1.enter + RING_DELAY);
  assert.ok(Math.abs(r1.ring.seconds - (r1.until - r1.ring.start)) < 1e-9);
  assert.equal(plan.label, enterOf(scene, 'h1'));
  assert.equal(plan.tiles.get('t2'), enterOf(scene, 't2'));
  // the ring of a question runs from its entrance to its answer
  assert.ok(Math.abs(q1.ring.seconds - (q1.answerAt - q1.ring.start)) < 1e-9);
  assert.ok(q1.exits && q2.exits && r1.exits, 'each has something after it');
  // a question no beat and no event answers waits the think default
  const bare = { ...scene, beats: [], events: [], duration: 30 };
  const bq = /** @type {any} */ (timetable(bare, prompts, tiles, label).prompts.get('q1'));
  assert.equal(bq.answerAt, bq.enter + THINK_DEFAULT);
  // the last prompt of a scene with no tile after it never fades out
  const alone = { ...scene, board: scene.board.filter((/** @type {any} */ i) => i.kind !== 'takeaway') };
  const only = recapItems(alone);
  const last = /** @type {any} */ (timetable(alone, only.prompts, only.tiles, only.label).prompts.get('r1'));
  assert.equal(last.exits, false);
  assert.equal(last.until, alone.duration);
});

test('timetable: a countdown event times the ring (its own start and length), and a think time under the minimum draws none', () => {
  const tl = load('recap_retrieval.portrait.json');
  const scene = tl.scenes[0];
  const { label, prompts, tiles } = recapItems(scene);
  const cd = scene.events.find((/** @type {any} */ e) => e.kind === 'countdown');
  const q1 = /** @type {any} */ (timetable(scene, prompts, tiles, label).prompts.get('q1'));
  assert.deepEqual(q1.ring, { start: cd.t, seconds: cd.dur });
  assert.notEqual(cd.t, q1.enter + RING_DELAY, 'the event, not the entrance');
  const mixed = load('recap_retrieval.landscape.tamil.json').scenes[1];
  const m = recapItems(mixed);
  const mq = /** @type {any} */ (timetable(mixed, m.prompts, m.tiles, m.label).prompts.get('q1'));
  assert.equal(mq.ring, null, `a think time of ${mq.answerAt - mq.enter} s is under RING_MIN ${RING_MIN}`);
});

test('texOf: the $...$ spans of the label, each prompt and its answer, and each tile, inline and in build order', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'h', kind: 'heading', text: 'Law $V$' },
    { id: 'q', kind: 'question', text: 'What is $I$?', justification: 'It is $V/R$.' },
    { id: 't', kind: 'takeaway', text: 'Power is $P = VI$' },
    { id: 'b', kind: 'takeaway', text: 'no maths, a \\$5 note' },
  ] });
  assert.deepEqual(texOf(scene), [{ tex: 'V', display: false }, { tex: 'I', display: false },
    { tex: 'V/R', display: false }, { tex: 'P = VI', display: false }]);
  assert.deepEqual(recapRetrieval.tex?.(scene, null, 'portrait'), texOf(scene));
});

test('lineHeight: the type scale\'s Latin values and the Indic minimums (plan §2)', () => {
  assert.equal(lineHeight('body', 'A closed loop'), LINE_HEIGHT.body.latin);
  assert.equal(lineHeight('body', 'மின்கலம் pushes $V$'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('heading', 'विद्युत परिपथ'), LINE_HEIGHT.heading.indic);
  assert.equal(lineHeight('label', 'Retrieval check'), LINE_HEIGHT.label.latin);
  const scale = CATALOGUE.type.scale;
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.body.indic, scale.body.line_height.indic[0], 'plan §2: Indic body >= 1.45');
  assert.equal(LINE_HEIGHT.heading.latin, scale.heading.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.heading.indic, scale.heading.line_height.indic[0]);
});

test('sizes keep R-29: labels at the label floor, text above the text floor, 9:16 body text at least weight 600', () => {
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    const floors = TYPE_FLOORS[fmt];
    assert.equal(L.label.min, floors.label_min, `${fmt} label floor`);
    assert.equal(L.ringNum, floors.label_min, `${fmt} ring number is a label`);
    for (const [slot, s] of /** @type {const} */ ([['prompt', L.prompt], ['answer', L.answer], ['tile', L.tile]])) {
      assert.ok(s.min >= floors.text_min, `${fmt} ${slot} >= the text floor`);
      if (floors.heavy_max_px && s.max <= floors.heavy_max_px) {
        assert.ok(s.weight >= /** @type {number} */ (floors.heavy_min_weight), `${fmt} ${slot}: R-29 weight`);
      }
    }
  }
  assert.ok(LAYOUT.landscape.prompt.max <= TYPE_FLOORS.landscape.heading[1], 'R-29 16:9 headings <= 120');
  assert.ok(LAYOUT.landscape.prompt.min >= TYPE_FLOORS.landscape.heading[0], 'R-29 16:9 headings >= 84');
  assert.deepEqual(tileRows(7, 3), [[0, 1, 2], [3, 4, 5], [6]]);
  assert.deepEqual(tileRows(3, 3), [[0, 1, 2]]);
  assert.deepEqual(tileRows(3, 1), [[0], [1], [2]], '9:16 stacks them');
  assert.deepEqual(tileRows(0, 3), []);
});

/**
 * preflight.py inside_bands: a box lies inside the union of the critical boxes, each box it overlaps holding its whole
 * width (a 9:16 card may cross y 840, where the upper box ends and the lower begins).
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

test('geometry: 16:9 the centre 60% for the card and the whole grid for three 560 px tiles; 9:16 the text column, all inside the critical zones', () => {
  const l = boxes('landscape', ZONES.landscape);
  assert.deepEqual([l.prompt.x0, l.prompt.x1, l.prompt.y0, l.prompt.y1], [442, 1478, 112, 900], 'the centre 60%, title top to body bottom');
  assert.deepEqual([l.tiles.x0, l.tiles.x1], [96, 1824], 'the whole content width');
  assert.equal(tileWidth('landscape', ZONES.landscape), 560);
  assert.equal(3 * 560 + 2 * LAYOUT.landscape.tileGap, l.tiles.x1 - l.tiles.x0, 'three tiles fill the grid with its own 24 px gutters');
  const p = boxes('portrait', ZONES.portrait);
  assert.deepEqual([p.prompt.x0, p.prompt.x1, p.prompt.y0, p.prompt.y1], [192, 780, 340, 1020], '9:16: the column both critical boxes hold');
  assert.equal(tileWidth('portrait', ZONES.portrait), 588);
  assert.equal(tileTextWidth('landscape', ZONES.landscape), 560 - 2 * LAYOUT.landscape.pad);
  assert.equal(tileTextWidth('portrait', ZONES.portrait), 588 - 2 * LAYOUT.portrait.pad - ICON_SIZE - LAYOUT.portrait.gap);
  for (const [fmt, b] of /** @type {const} */ ([['landscape', l], ['portrait', p]])) {
    const zones = ZONES[fmt];
    for (const box of [b.label, b.prompt, b.tiles]) {
      assert.ok(insideBands({ x: box.x0, y: box.y0, w: box.x1 - box.x0, h: box.y1 - box.y0 }, zones.critical), `${fmt}: inside the critical area (R-59)`);
    }
  }
});

test('slotRows: the slot table templates.py mirrors (widths from the boxes and the tile)', () => {
  const rows = slotRows(ZONES.landscape, ZONES.portrait);
  assert.deepEqual(Object.keys(rows).sort(), ['answer', 'label', 'prompt', 'ring', 'tile']);
  assert.equal(rows.prompt.landscape.default.w, 1036 - 2 * LAYOUT.landscape.pad);
  assert.equal(rows.answer.landscape.default.w, rows.prompt.landscape.default.w, 'the answer shares the card\'s text box');
  assert.equal(rows.prompt.portrait.default.w, 588 - 2 * LAYOUT.portrait.pad);
  assert.equal(rows.label.landscape.default.w, 1036);
  assert.equal(rows.label.portrait.default.w, 588);
  assert.equal(rows.tile.landscape.default.w, 496);
  assert.equal(rows.tile.portrait.default.w, 420);
  assert.deepEqual(rows.ring.landscape.default.size, [40, 40]);
  assert.equal(rows.ring.landscape.default.w, LAYOUT.landscape.ring);
  assert.equal(rows.ring.portrait.default.w, LAYOUT.portrait.ring);
  assert.equal(rows.ring.landscape.default.critical, false, 'R-07: the think time is the ring itself');
  assert.equal(rows.ring.landscape.default.tabular_nums, true);
  assert.equal(rows.prompt.landscape.default.face, 'display');
  assert.equal(rows.prompt.landscape.default.prefer_lines, LAYOUT.landscape.prompt.prefer);
  assert.equal(rows.answer.landscape.default.prefer_lines, null, 'an answer has no preferred line count');
});

test('the catalogue\'s recap_retrieval slots (templates.py) are slotRows exactly, and its consumes names the forms it draws', () => {
  // registered (B2c review CG-4: never skipped, so a missing slot table or consumes change fails here)
  assert.equal(/** @type {any} */ (CATALOGUE.templates).recap_retrieval.implemented, true,
    'templates.py: recap_retrieval needs implemented=True and slots=_recap_retrieval_slots()');
  const cat = /** @type {any} */ (templateSlots('recap_retrieval'));
  const want = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  assert.ok(cat, 'templates.py: recap_retrieval has no slot table');
  for (const slot of Object.keys(want)) {
    for (const fmt of Object.keys(want[slot])) {
      for (const variant of Object.keys(want[slot][fmt])) {
        const got = cat[slot][fmt][variant];
        assert.deepEqual({
          w: got.w, max_lines: got.max_lines, size: got.size, role: got.role, face: got.face, weight: got.weight,
          line_height: got.line_height, prefer_lines: got.prefer_lines, critical: got.critical,
          tabular_nums: got.tabular_nums,
        }, want[slot][fmt][variant], `${slot} ${fmt} ${variant}`);
      }
    }
  }
  assert.deepEqual(Object.keys(cat).sort(), Object.keys(want).sort());
  // the forms per kind (B2c review CG-5): a whole item for what Python times, a term or the item for the term cue
  assert.deepEqual(/** @type {any} */ (CATALOGUE.templates).recap_retrieval.consumes.forms, {
    reveal: ['item'], fill: ['item'], countdown: ['item'], answer: ['item'], cue_term: ['item', 'item:term'],
  });
});

test('mediaBox: this template shows no picture or clip', () => {
  const scene = /** @type {any} */ ({ board: [{ id: 'q', kind: 'question', text: 'Why?' }] });
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    for (const slot of ['main', 'panel', undefined]) {
      assert.equal(recapRetrieval.mediaBox?.(scene, ZONES[fmt], 'render', slot, { safe: false, slots: {} }), null);
    }
  }
});

// --- painted behaviour ----------------------------------------------------------------------------------------------

test('a prompt card rises 24 px as it fades in, its question 80 ms later, in the personality\'s time', async () => {
  const tl = load('recap_retrieval.portrait.json'); // Soft: fade_rise, x1.15
  const scale = tl.presentation.motion_scale;
  const scene = tl.scenes[0];
  const s = spyStage(tl);
  const at = enterOf(scene, 'q1');
  await s.seekTo(0, at - 0.1);
  let pr = promptOf(s.ctx(0), 'q1');
  assert.equal(pr.card.style.visibility, 'hidden', 'not yet in');
  await s.seekTo(0, at + 0.1);
  pr = promptOf(s.ctx(0), 'q1');
  const card = Number(pr.card.style.opacity);
  const text = Number(pr.question.style.opacity);
  assert.ok(card > 0 && text < card, `the card first (${card}), then its question (${text})`);
  assert.match(pr.card.style.transform, /^translate\(0, \d+px\)$/, 'a whole-px rise (2D, §P)');
  const dy = Number(/translate\(0, (\d+)px/.exec(pr.card.style.transform)?.[1]);
  assert.ok(dy > 0 && dy < CARD_RISE);
  await s.seekTo(0, at + (TEXT_DELAY + CARD_ENTER) * scale + 0.05);
  pr = promptOf(s.ctx(0), 'q1');
  assert.deepEqual([op(pr.card.style.opacity), op(pr.question.style.opacity), pr.card.style.transform], [1, 1, 'none']);
  // the card (no delay) is still entering after the unscaled 500 ms: Soft's x1.15 stretches it
  await s.seekTo(0, at + CARD_ENTER + 0.02);
  assert.ok(op(promptOf(s.ctx(0), 'q1').card.style.opacity) < 1);
  s.done();
});

test('§4.3\'s sequence: a prompt card leaves as the next prompt enters, and the last one as the first tile enters', async () => {
  const tl = load('recap_retrieval.landscape.json');
  const scene = tl.scenes[0];
  const scale = tl.presentation.motion_scale;
  const s = spyStage(tl);
  const q2 = enterOf(scene, 'q2');
  await s.seekTo(0, q2 - CARD_EXIT * scale - 0.1);
  assert.equal(op(promptOf(s.ctx(0), 'q1').card.style.opacity), 1, 'still up');
  await s.seekTo(0, q2 - 0.02);
  const going = Number(promptOf(s.ctx(0), 'q1').card.style.opacity);
  assert.ok(going > 0 && going < 1, `leaving (${going})`);
  await s.seekTo(0, q2 + 0.2);
  let ctx = s.ctx(0);
  assert.equal(promptOf(ctx, 'q1').card.style.visibility, 'hidden', 'gone once the next prompt is in');
  assert.ok(Number(promptOf(ctx, 'q2').card.style.opacity) > 0);
  // the free-recall prompt leaves as the first tile enters
  const t1 = enterOf(scene, 't1');
  await s.seekTo(0, t1 + 0.2);
  ctx = s.ctx(0);
  assert.equal(promptOf(ctx, 'r1').card.style.visibility, 'hidden');
  assert.ok(Number(tileOf(ctx, 't1').card.style.opacity) > 0);
  s.done();
});

test('the think ring runs linearly with no sound: the arc empties and the number counts whole seconds down (R-07)', async () => {
  const tl = load('recap_retrieval.portrait.json');
  const scene = tl.scenes[0];
  const cd = scene.events.find((/** @type {any} */ e) => e.kind === 'countdown');
  const s = spyStage(tl);
  await s.seekTo(0, cd.t - 0.1);
  let ring = promptOf(s.ctx(0), 'q1').ring;
  assert.ok(ring, 'the prompt has a ring');
  assert.equal(ring.box.style.visibility, 'hidden', 'not before its countdown');
  await s.seekTo(0, cd.t + 0.35);
  ring = promptOf(s.ctx(0), 'q1').ring;
  assert.equal(ring.box.style.visibility, 'visible');
  const full = Number(ring.arc.getAttribute('stroke-dasharray').split(' ')[0]);
  const offAt = (/** @type {any} */ r) => Number(r.arc.getAttribute('stroke-dashoffset'));
  const quarter = offAt(ring);
  const nAt = (/** @type {any} */ r) => r.num.textContent;
  assert.equal(nAt(ring), String(Math.ceil(cd.dur - 0.35)), 'whole seconds left');
  await s.seekTo(0, cd.t + cd.dur / 2);
  const half = offAt(promptOf(s.ctx(0), 'q1').ring);
  await s.seekTo(0, cd.t + cd.dur - 0.02);
  const end = offAt(promptOf(s.ctx(0), 'q1').ring);
  assert.ok(quarter < half && half < end, `the arc empties (${quarter} < ${half} < ${end})`);
  assert.ok(Math.abs(half - full / 2) < full * 0.08, 'linear: half the arc is gone at half the time');
  // after the ring's time it leaves; the number is cleared with it
  await s.seekTo(0, cd.t + cd.dur + 0.4);
  ring = promptOf(s.ctx(0), 'q1').ring;
  assert.equal(ring.box.style.visibility, 'hidden');
  assert.equal(ring.num.textContent, '');
  s.done();
});

test('the answer: at the fill, it rises 16 px and takes the `done` state (an ok check pops, an ok border draws)', async () => {
  const tl = load('recap_retrieval.landscape.json');
  const scene = tl.scenes[0];
  const ok = tl.presentation.content.ok;
  const ink = tl.presentation.content.ink;
  const s = spyStage(tl);
  const at = /** @type {number} */ (itemTimings(scene).find((x) => x.id === 'q1')?.fill);
  await s.seekTo(0, at - 0.1);
  let a = promptOf(s.ctx(0), 'q1').answer;
  assert.equal(a.el.style.visibility, 'hidden', 'withheld until the beat fills it');
  assert.match(a.el.style.getPropertyValue('--dyn-state-border'), /00$/, 'no border yet');
  assert.equal(a.parts.check.style.visibility, 'hidden');
  await s.seekTo(0, at + 0.08);
  a = promptOf(s.ctx(0), 'q1').answer;
  assert.equal(a.el.style.visibility, 'visible');
  const dy = Number(/translate\(0, (\d+)px/.exec(a.el.style.transform)?.[1]);
  assert.ok(dy > 0 && dy <= ANSWER_RISE, `a whole-px 16 px rise (${a.el.style.transform})`);
  assert.ok(Number(a.el.style.opacity) < 1);
  await s.seekTo(0, at + 0.6);
  a = promptOf(s.ctx(0), 'q1').answer;
  assert.deepEqual([op(a.el.style.opacity), a.el.style.transform], [1, 'none']);
  assert.equal(a.el.style.getPropertyValue('--dyn-state-border').toLowerCase(), `${ok.toLowerCase()}ff`, 'the ok border, fully in');
  assert.equal(a.parts.check.style.visibility, 'visible');
  assert.equal(a.parts.check.style.color, rgb(ok), 'the check in ok');
  assert.equal(a.el.style.color, rgb(ink));
  assert.equal(a.el.style.fontWeight, String(LAYOUT.landscape.answer.weight));
  // an `answer` event answers q2 (no beat fills it)
  const ev = scene.events.find((/** @type {any} */ e) => e.kind === 'answer');
  await s.seekTo(0, ev.t - 0.1);
  assert.equal(promptOf(s.ctx(0), 'q2').answer.el.style.visibility, 'hidden');
  await s.seekTo(0, ev.t + 0.15);
  assert.equal(promptOf(s.ctx(0), 'q2').answer.el.style.visibility, 'visible');
  // the free-recall prompt has no answer at all
  assert.equal(promptOf(s.ctx(0), 'r1').answer, null);
  s.done();
});

test('tiles flip in as each is named (§4.3 rotateX 90° -> 0 without perspective: the 2D scaleY it is), icons all or nothing', async () => {
  const tl = load('recap_retrieval.landscape.json');
  const scene = tl.scenes[0];
  const scale = tl.presentation.motion_scale;
  const s = spyStage(tl);
  const at = enterOf(scene, 't2');
  await s.seekTo(0, at - 0.1);
  let ctx = s.ctx(0);
  assert.equal(tileOf(ctx, 't2').card.style.visibility, 'hidden');
  assert.ok(ctx.view.icons && ctx.view.tiles.every((/** @type {any} */ x) => x.glyph), 'an icon for every tile');
  assert.equal(tileOf(ctx, 't1').glyph.querySelector('svg')?.getAttribute('stroke'), 'currentColor');
  assert.equal(tileOf(ctx, 't1').glyph.style.width, `${ICON_SIZE}px`);
  assert.equal(tileOf(ctx, 't1').card.style.transformOrigin, 'center center');
  await s.seekTo(0, at + 0.1);
  const mid = tileOf(s.ctx(0), 't2');
  const k = Number(/scaleY\(([\d.]+)\)/.exec(mid.card.style.transform)?.[1]);
  assert.ok(k > 0 && k < 1, `mid-flip (${mid.card.style.transform})`);
  assert.equal(Number(mid.card.style.opacity) > 0, true);
  await s.seekTo(0, at + TILE_FLIP * scale + 0.05);
  const done = tileOf(s.ctx(0), 't2');
  assert.deepEqual([op(done.card.style.opacity), done.card.style.transform], [1, 'none']);
  // the three tiles sit side by side across the grid, the row centred in the content box
  ctx = s.ctx(0);
  const xs = ctx.view.tiles.map((/** @type {any} */ x) => Number.parseFloat(x.el.style.left));
  assert.deepEqual(xs, [96, 96 + 560 + 24, 96 + 2 * (560 + 24)]);
  const widths = new Set(ctx.view.tiles.map((/** @type {any} */ x) => x.el.style.width));
  assert.deepEqual([...widths], ['560px']);
  s.done();
  // a scene whose tiles have no stored icon draws none at all (B2c review V12)
  const none = load('recap_retrieval.landscape.tamil.json');
  const s2 = spyStage(none);
  await s2.seekTo(1, 2.5);
  const c2 = s2.ctx(1);
  assert.equal(c2.view.icons, false);
  assert.ok(c2.view.tiles.length > 0 && c2.view.tiles.every((/** @type {any} */ x) => x.glyph === null));
  s2.done();
});

test('9:16: the card and the tiles fill the text column, the tiles stacked with their icons beside them', async () => {
  const tl = load('recap_retrieval.portrait.json');
  const s = spyStage(tl);
  await s.seekTo(0, 6.5);
  const ctx = s.ctx(0);
  const col = ZONES.portrait.textColumn;
  for (const pr of ctx.view.prompts) {
    assert.equal(Number.parseFloat(pr.el.style.left), col.x0);
    assert.equal(Number.parseFloat(pr.el.style.width), col.x1 - col.x0);
  }
  const tops = ctx.view.tiles.map((/** @type {any} */ x) => Number.parseFloat(x.el.style.top));
  assert.deepEqual(tops, [...tops].sort((a, b) => a - b), 'stacked in order');
  assert.equal(new Set(ctx.view.tiles.map((/** @type {any} */ x) => x.el.style.left)).size, 1, 'one column');
  for (const tile of ctx.view.tiles) {
    assert.ok(tile.glyph);
    assert.equal(Number.parseFloat(tile.glyph.style.left), LAYOUT.portrait.pad, 'the icon beside the text');
    assert.equal(Number.parseFloat(tile.text.style.left), LAYOUT.portrait.pad + ICON_SIZE + LAYOUT.portrait.gap);
    assert.equal(Number.parseFloat(tile.text.style.width), tileTextWidth('portrait', ZONES.portrait));
  }
  s.done();
});

test('the personality\'s text entrance: Crisp slides the question up inside a mask, Soft fades it', async () => {
  const crisp = load('recap_retrieval.landscape.json');
  assert.equal(crisp.presentation.text_entrance, 'baseline_reveal');
  let s = spyStage(crisp);
  await s.seekTo(0, enterOf(crisp.scenes[0], 'q1') + 0.2);
  let pr = promptOf(s.ctx(0), 'q1');
  assert.ok(pr.qmask && pr.question.parentElement === pr.qmask && pr.qmask.classList.contains('dyn-reveal-mask'));
  assert.equal(pr.question.style.opacity, '1', 'a baseline reveal moves, it does not fade');
  assert.ok(s.ctx(0).view.head.mask, 'the label too');
  s.done();
  const soft = load('recap_retrieval.portrait.json');
  s = spyStage(soft);
  await s.seekTo(0, enterOf(soft.scenes[0], 'q1') + 0.15);
  pr = promptOf(s.ctx(0), 'q1');
  assert.equal(pr.qmask, null);
  assert.ok(Number(pr.question.style.opacity) < 1);
  s.done();
});

test('reduced motion (live player): cards, answers and tiles fade in place', async () => {
  const tl = load('recap_retrieval.landscape.json');
  tl.presentation.text_entrance = 'fade_rise';
  const scene = tl.scenes[0];
  const s = spyStage(tl, { mode: 'live', reducedMotion: true });
  await s.seekTo(0, enterOf(scene, 'q1') + 0.1);
  let pr = promptOf(s.ctx(0), 'q1');
  assert.equal(pr.card.style.transform, 'none');
  assert.equal(pr.question.style.transform, 'none');
  assert.ok(Number(pr.card.style.opacity) > 0 && Number(pr.card.style.opacity) < 1);
  const at = /** @type {number} */ (itemTimings(scene).find((x) => x.id === 'q1')?.fill);
  await s.seekTo(0, at + 0.08);
  pr = promptOf(s.ctx(0), 'q1');
  assert.equal(pr.answer.el.style.transform, 'none');
  await s.seekTo(0, enterOf(scene, 't1') + 0.1);
  assert.equal(tileOf(s.ctx(0), 't1').card.style.transform, 'none');
  s.done();
});

test('cue.term: the stage finds a prompt\'s [[term]] through itemEl, over the question and then the answer', async () => {
  const tl = load('recap_retrieval.landscape.json');
  tl.scenes[0].board[1].justification = 'More [[voltage]] pushes more current.';
  const s = spyStage(tl);
  await s.seekTo(0, 6);
  const ctx = s.ctx(0);
  const pr = promptOf(ctx, 'q1');
  assert.deepEqual(recapRetrieval.itemEl?.(ctx, 'q1'), [pr.question, pr.answer.el], 'the question, then the answer');
  assert.equal(recapRetrieval.itemEl?.(ctx, 't1'), tileOf(ctx, 't1').text);
  assert.equal(recapRetrieval.itemEl?.(ctx, 'h1'), ctx.view.head.text);
  assert.equal(recapRetrieval.itemEl?.(ctx, 'nope'), null);
  // <item>:term:<n> counts over both fields, in the item's field order (text, justification)
  /** @param {string} target */
  const pieces = (target) => termPieces(recapRetrieval, ctx,
    /** @type {any} */ ({ kind: 'cue_term', target, parsed: parseTarget(target), start: 0, hold: 1, order: 0 }));
  assert.deepEqual(pieces('q1:term:0').map((p) => p.span.textContent), ['higher', ' ', 'voltage'], 'the question\'s term');
  assert.deepEqual(pieces('q1:term:1').map((p) => p.span.textContent), ['voltage'], 'the answer\'s term');
  assert.ok(pieces('q1').length >= 4, '<item> sweeps every term of both fields');
  assert.deepEqual(pieces('t1:term:0').map((p) => p.span.textContent), ['Voltage']);
  s.done();
});

test('focusRect: a card (a shape, so its outline is drawn outside it) and the label\'s text, on whole px, with its dim group', async () => {
  const tl = load('recap_retrieval.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(0, 2);
  const real = s.ctx(0);
  const pr = promptOf(real, 'q1');
  const tile = tileOf(real, 't1');
  const rects = new Map([[pr.card, { x: 442, y: 300.4, w: 1036, h: 420.6 }], [tile.card, { x: 96, y: 400, w: 560, h: 300 }],
    [real.view.head.text, { x: 442, y: 112, w: 1036, h: 52.4 }]]);
  const ctx = { ...real, rectOf: (/** @type {Element} */ el) => rects.get(el) || null };
  assert.deepEqual(recapRetrieval.focusRect?.(ctx, 'q1'), { rect: { x: 442, y: 300, w: 1036, h: 421 }, shape: 'rect', radius: 24, el: pr.el, kind: 'shape' });
  assert.deepEqual(recapRetrieval.focusRect?.(ctx, 'q1:term:0'), recapRetrieval.focusRect?.(ctx, 'q1'), 'a term target points at its card');
  assert.deepEqual(recapRetrieval.focusRect?.(ctx, 't1'), { rect: { x: 96, y: 400, w: 560, h: 300 }, shape: 'rect', radius: 24, el: tile.el, kind: 'shape' });
  assert.deepEqual(recapRetrieval.focusRect?.(ctx, 'h1'), { rect: { x: 442, y: 112, w: 1036, h: 52 }, shape: 'rect', radius: 0, el: real.view.head.el, kind: 'text' });
  assert.equal(recapRetrieval.focusRect?.(ctx, 'zz'), null);
  // a card's outline is drawn whole just outside it (cues.js shapeArea), never clamped across its text
  const focus = /** @type {any} */ (recapRetrieval.focusRect?.(ctx, 't1'));
  const zones = ZONES.landscape;
  const outline = /** @type {any} */ (outlinePath(focus, shapeArea(zones)));
  const inner = { x0: outline.box.x0 + FOCUS_PX / 2, y0: outline.box.y0 + FOCUS_PX / 2, x1: outline.box.x1 - FOCUS_PX / 2, y1: outline.box.y1 - FOCUS_PX / 2 };
  const r = focus.rect;
  assert.deepEqual([r.x - inner.x0, r.y - inner.y0, inner.x1 - r.x - r.w, inner.y1 - r.y - r.h], [FOCUS_OUTSET, FOCUS_OUTSET, FOCUS_OUTSET, FOCUS_OUTSET]);
  // the label's text is text: its outline is clamped to the critical box holding it (R-59)
  const head = /** @type {any} */ (recapRetrieval.focusRect?.(ctx, 'h1'));
  assert.deepEqual(clampOf(zones, head.rect), zones.critical[0]);
  s.done();
});

test('dimGroups: the label, every prompt and every tile; the stage dims all but the target\'s, and the template never writes their opacity', async () => {
  const tl = load('recap_retrieval.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(0, 2);
  const ctx = s.ctx(0);
  const groups = /** @type {HTMLElement[]} */ (recapRetrieval.dimGroups?.(ctx));
  assert.deepEqual(groups, [ctx.view.head.el, ...ctx.view.prompts.map((/** @type {any} */ x) => x.el),
    ...ctx.view.tiles.map((/** @type {any} */ x) => x.el)]);
  for (const g of groups) assert.equal(g.style.opacity, '', 'the stage owns it; nothing dims without a look_here');
  assert.equal(recapRetrieval.anchor?.(ctx), null);
  s.done();
});

test('texts(): the label, every question, ring number, answer and tile, in the ink, size and weight painted now', async () => {
  const tl = load('recap_retrieval.landscape.json');
  const scene = tl.scenes[0];
  const s = spyStage(tl);
  const at = /** @type {number} */ (itemTimings(scene).find((x) => x.id === 'q1')?.fill);
  await s.seekTo(0, at + 0.5);
  const ctx = s.ctx(0);
  const entries = /** @type {any[]} */ (recapRetrieval.texts?.(ctx));
  const bySlot = (/** @type {string} */ slot) => entries.filter((e) => e.slot === slot);
  const head = bySlot('label')[0];
  assert.deepEqual([head.role, head.critical, head.weight, head.size, head.text], ['label', true, 600, 40, 'Retrieval check']);
  assert.equal(head.ink.toLowerCase(), ctx.onBackground.ink2.toLowerCase());
  assert.equal(head.bg, ctx.onBackground.bg, 'the label sits on the background');
  const prompts = bySlot('prompt');
  assert.equal(prompts.length, 3, 'every prompt is reported, up or not');
  assert.deepEqual([prompts[0].role, prompts[0].critical, prompts[0].weight], ['title', true, 700]);
  assert.equal(prompts[0].bg, ctx.roles.color('surface'), 'the card\'s surface');
  assert.equal(prompts[0].ink, ctx.roles.color('ink'));
  assert.ok(prompts[0].alpha > 0 && prompts[2].alpha === 0, 'only the prompt that is up has an alpha');
  const answer = bySlot('answer')[0];
  assert.deepEqual([answer.role, answer.critical, answer.weight, answer.size], ['body', true, LAYOUT.landscape.answer.weight, ctx.view.answerSize]);
  assert.ok(answer.alpha > 0);
  assert.equal(answer.text, 'More voltage pushes more current through the same resistance.');
  const tiles = bySlot('tile');
  assert.equal(tiles.length, 3);
  assert.deepEqual([tiles[0].role, tiles[0].critical, tiles[0].weight, tiles[0].alpha], ['body', true, 600, 0]);
  assert.equal(tiles[0].text, 'Voltage pushes charge round a closed loop', 'plain text');
  for (const e of entries) {
    assert.match(String(e.ink), /^#[0-9a-f]{6}$/i);
    assert.match(String(e.bg), /^#[0-9a-f]{6}$/i);
    assert.ok(e.alpha >= 0 && e.alpha <= 1);
    assert.ok(e.size >= e.min);
  }
  // the ring's number while it runs: a label, never critical (R-07)
  const r1 = enterOf(scene, 'r1');
  await s.seekTo(0, r1 + 0.6);
  const ring = /** @type {any[]} */ (recapRetrieval.texts?.(s.ctx(0))).filter((e) => e.slot === 'ring');
  assert.equal(ring.length, 1, 'only the ring that is running');
  assert.deepEqual([ring[0].role, ring[0].critical, ring[0].weight, ring[0].size], ['label', false, 700, LAYOUT.landscape.ringNum]);
  assert.match(ring[0].text, /^\d+$/);
  s.done();
});

test('preflight\'s remedies: a slot size cap and safe hold the text small; a scrim goes behind the label', async () => {
  const tl = load('recap_retrieval.landscape.json');
  tl.scenes[0].look.fixes = { safe: false, slots: { prompt: { size: 88 }, answer: { size: 44 }, tile: { size: 44 }, label: { scrim: true } } };
  let s = spyStage(tl);
  await s.seekTo(0, 3);
  let ctx = s.ctx(0);
  assert.equal(ctx.view.promptSize, 88, 'the cap (jsdom fits at the largest size allowed)');
  assert.equal(ctx.view.answerSize, 44);
  assert.equal(ctx.view.tileSize, 44);
  const scrim = ctx.roles.color('scrim');
  assert.equal(ctx.view.head.scrim, scrim);
  assert.ok(ctx.view.head.el.style.background !== '' && ctx.view.head.el.style.padding !== '');
  const head = /** @type {any[]} */ (recapRetrieval.texts?.(ctx)).find((e) => e.slot === 'label');
  assert.equal(head.bg, scrimmedBg(scrim, ctx.onBackground, head.ink), 'measured on the scrim over the background');
  assert.notEqual(head.bg, ctx.onBackground.bg);
  s.done();
  const safe = load('recap_retrieval.portrait.json');
  safe.scenes[0].look.fixes = { safe: true, slots: {} };
  s = spyStage(safe);
  await s.seekTo(0, 3);
  ctx = s.ctx(0);
  assert.equal(ctx.view.promptSize, LAYOUT.portrait.prompt.min, 'safe: every slot at its minimum');
  assert.equal(ctx.view.answerSize, LAYOUT.portrait.answer.min);
  assert.equal(ctx.view.tileSize, LAYOUT.portrait.tile.min);
  assert.equal(ctx.view.head.size, LAYOUT.portrait.label.min);
  s.done();
});

test('update writes every property on every frame with a value (no \'\': a cleared property would make the DOM order-dependent)', async () => {
  const proto = /** @type {any} */ (Painter.prototype);
  const real = proto.style;
  /** @type {string[]} */
  const empty = [];
  let inside = false;
  const wrapped = { ...recapRetrieval, update(/** @type {any} */ ctx, /** @type {number} */ t, /** @type {any} */ st) { inside = true; try { recapRetrieval.update(ctx, t, st); } finally { inside = false; } } };
  proto.style = function style(/** @type {any} */ el, /** @type {string} */ prop, /** @type {any} */ value) {
    if (inside && String(value) === '') empty.push(`${el.className} ${prop}`);
    return real.call(this, el, prop, value);
  };
  try {
    for (const name of FIXTURES) {
      const tl = load(name);
      const { stage, done } = renderStage(tl, { templates: { recap_retrieval: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex });
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
 * wider than its line breaks across lines. Enough for this template's fit: the height of a question, an answer, a tile
 * and the label from the font size, the line height and the box's width. Returns the restore function.
 */
function fakeLayout() {
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const keys = ['offsetHeight', 'offsetWidth', 'clientWidth', 'offsetTop'];
  const saved = keys.map((k) => [k, Object.getOwnPropertyDescriptor(proto, k)]);
  const CW = 0.55;
  const TEXTS = '.dyn-rr-q, .dyn-rr-a, .dyn-rr-t, .dyn-rr-label';
  /** @param {Element | null} el @returns {number} */
  const fontSize = (el) => {
    for (let e = el; e; e = e.parentElement) {
      const v = Number.parseFloat(/** @type {HTMLElement} */ (e).style?.fontSize || '');
      if (v > 0) return v;
    }
    return 16;
  };
  /** The first width an element or an ancestor declares. @param {Element | null} el */
  const boxWidth = (el) => {
    for (let e = el; e; e = e.parentElement) {
      const v = Number.parseFloat(/** @type {HTMLElement} */ (e).style?.width || '');
      if (v > 0) return v;
    }
    return 0;
  };
  /** @param {HTMLElement} el */
  const lineH = (el) => {
    const v = Number.parseFloat(el.style.lineHeight || '');
    return (v > 0 ? v : 1.3) * fontSize(el);
  };
  /** @param {HTMLElement} textEl @param {number} boxW */
  const lines = (textEl, boxW) => {
    const size = fontSize(textEl);
    let n = 1;
    let x = 0;
    for (const w of textEl.querySelectorAll('.dyn-word')) {
      const ww = (w.textContent || '').length * CW * size;
      if (boxW > 0 && ww > boxW) {
        n += (x > 0 ? 1 : 0) + Math.ceil(ww / boxW) - 1;
        x = ww % boxW;
        continue;
      }
      const need = x > 0 ? x + CW * size + ww : ww;
      if (boxW > 0 && need > boxW) {
        n += 1;
        x = ww;
      } else {
        x = need;
      }
    }
    return n;
  };
  Object.defineProperty(proto, 'offsetHeight', {
    configurable: true,
    get() {
      const el = /** @type {HTMLElement} */ (this);
      if (el.classList.contains('dyn-word')) {
        const ww = (el.textContent || '').length * CW * fontSize(el);
        const host = /** @type {HTMLElement | null} */ (el.closest(TEXTS));
        const box = boxWidth(host);
        return (box > 0 && ww > box ? Math.ceil(ww / box) : 1) * lineH(/** @type {HTMLElement} */ (host || el));
      }
      if (el.matches(TEXTS)) return lines(el, boxWidth(el)) * lineH(el);
      const inner = /** @type {HTMLElement | null} */ (el.querySelector(TEXTS));
      return inner ? lines(inner, boxWidth(inner)) * lineH(inner) : 0;
    },
  });
  Object.defineProperty(proto, 'offsetTop', { configurable: true, get() { return Number.parseFloat(/** @type {HTMLElement} */ (this).style.top) || 0; } });
  Object.defineProperty(proto, 'offsetWidth', { configurable: true, get() { return (/** @type {HTMLElement} */ (this).textContent || '').length * CW * fontSize(this); } });
  Object.defineProperty(proto, 'clientWidth', { configurable: true, get() { return boxWidth(/** @type {HTMLElement} */ (this)); } });
  return () => {
    for (const [k, d] of saved) {
      if (d) Object.defineProperty(proto, k, /** @type {any} */ (d));
      else delete proto[/** @type {string} */ (k)];
    }
  };
}

test('the fit (fake layout): the largest common size in 4 px steps whose cards and tiles fit, never below the slot minimum', async () => {
  const restore = fakeLayout();
  try {
    for (const name of FIXTURES) {
      const tl = load(name);
      const fmt = /** @type {'landscape' | 'portrait'} */ (tl.presentation.format);
      const L = LAYOUT[fmt];
      const s = spyStage(tl);
      await s.seekTo(0, 2);
      const v = s.ctx(0).view;
      for (const [slot, size, range] of /** @type {const} */ ([['prompt', v.promptSize, L.prompt], ['answer', v.answerSize, L.answer], ['tile', v.tileSize, L.tile]])) {
        assert.ok(size >= range.min && size <= range.max, `${name} ${slot}: ${size} in [${range.min}, ${range.max}]`);
        assert.equal((range.max - size) % 4, 0, `${name} ${slot}: a 4 px step from the maximum`);
      }
      // every card is centred in the prompt box and every tile row is the same height
      const heights = new Set(v.tiles.map((/** @type {any} */ x) => x.el.style.height));
      assert.ok(heights.size <= 1, `${name}: every tile the same height`);
      for (const pr of v.prompts) assert.ok(Number.parseFloat(pr.card.style.height) > 0, `${name}: the card has a height`);
      s.done();
    }
  } finally {
    restore();
  }
});

test('the fit (fake layout): a card that cannot fit at its minimum is reported as overflow, never clipped', async () => {
  const restore = fakeLayout();
  try {
    const tl = load('recap_retrieval.portrait.json');
    // an answer far past what the reel's card can hold at 40 px
    tl.scenes[0].board[1].justification = `${'A long sentence that keeps going and going. '.repeat(12)}`;
    const s = spyStage(tl);
    const at = /** @type {number} */ (itemTimings(tl.scenes[0]).find((x) => x.id === 'q1')?.fill);
    await s.seekTo(0, at + 0.5);
    const ctx = s.ctx(0);
    assert.equal(ctx.view.answerSize, LAYOUT.portrait.answer.min, 'held at the minimum');
    const entries = /** @type {any[]} */ (recapRetrieval.texts?.(ctx));
    const over = entries.filter((e) => Number(e.overflow) > 0);
    assert.ok(over.length > 0, 'preflight is told (it blocks with "Shorten text")');
    assert.ok(over.some((e) => e.slot === 'answer' || e.slot === 'prompt'), over.map((e) => e.slot).join(','));
    s.done();
  } finally {
    restore();
  }
});
