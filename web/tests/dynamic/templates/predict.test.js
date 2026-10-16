// @ts-check
// templates/predict.js (DYNAMIC_VIDEO_DESIGN §4.3 predict, R-62, R-07; B2 plan §1.4 the template contract, §1.5 the
// cues and item states, §1.6/§1.11 the slots, §P): the four fixture timelines
// (tests/compose/dynamic/make_predict_fixtures.py) through the harness's contract and order gates, the pure helpers
// (data, the think time, the timetable, geometry, slots, TeX), the painted behaviour in both formats (the card, the
// question, "Pause and predict", the think ring, and that NO answer is ever drawn here), the "Your prediction?" chip
// the explaining scene carries, the cues the stage draws from the template's geometry, preflight's text entries and
// remedies, and the fit on a fake text layout (jsdom has none).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { Painter, TYPE_FLOORS, ZONES, parseTarget, templateSlots } from '../../../js/dynamic/core.js';
import { FOCUS_OUTSET, FOCUS_PX, clampOf, outlinePath, shapeArea, termPieces } from '../../../js/dynamic/cues.js';
import { itemTimings } from '../../../js/dynamic/state.js';
import { scrimmedBg } from '../../../js/dynamic/templates/_shared.js';
import predict, {
  CARD_ENTER, CARD_RISE, CHIP_ENTER, CHIP_HOLD, CHIP_POP, CHIP_TEXT, LAYOUT, LINE_HEIGHT, PROMPT_DELAY, PROMPT_ENTER,
  PROMPT_RISE, PROMPT_TEXT, RING_DELAY, RING_MIN, TEXT_DELAY, THINK_EXTRA, THINK_MAX, THINK_MIN, THINK_WPM, boxes,
  buildChip, chipBox, chipTexts, drawsScene, eventFor, layoutChip, lineHeight, paintChip, predictItems, predictionOf,
  slotRows, texOf, thinkSeconds, timetable, wordsToRead,
} from '../../../js/dynamic/templates/predict.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['predict.landscape.json', 'predict.landscape.tamil.json', 'predict.portrait.json',
  'predict.portrait.hindi.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { predict } };

/**
 * A render stage of `tl` with predict through the stage's test seam, and the context each scene was built with.
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...predict, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return predict.build(ctx); } };
  const run = renderStage(tl, { templates: { predict: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
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
/** A hex colour as the DOM reports a painted one (`rgb(r, g, b)`). @param {string} hex */
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;
/** An opacity as a number ('' is 1: none painted). @param {string} v */
const op = (v) => (v === '' ? 1 : Number(v));

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, whole-px focus, terms, TeX, entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(predict, load(name)), [], name);
});

test('every fixture paints the same frame forward, in reverse and on a fresh page (a frame is a pure function of t)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const total = Math.round(tl.total_duration * tl.fps);
    // every 7th frame plus the frames around each event, each beat and each crossfade's start
    const frames = new Set();
    for (let f = 0; f < total; f += 7) frames.add(f);
    for (const s of tl.scenes) {
      for (const e of s.events || []) for (const d of [0, 0.2, 0.5]) frames.add(Math.min(total - 1, Math.round((s.start + e.t + d) * tl.fps)));
      for (const b of s.beats || []) frames.add(Math.min(total - 1, Math.round((s.start + b.start) * tl.fps)));
      frames.add(Math.min(total - 1, Math.round(s.start * tl.fps) + 3));
    }
    assert.deepEqual(await orderProblems(tl, [...frames].sort((a, b) => a - b), SEAM), [], name);
  }
});

test('the fixtures cover both formats, dark and light families, every text case and every event kind the template reads', () => {
  const seen = { formats: new Set(), modes: new Set(), entrances: new Set(), kinds: new Set(), scripts: new Set(), rings: new Set(), labels: new Set(), chip: new Set() };
  for (const name of FIXTURES) {
    const tl = load(name);
    seen.formats.add(tl.presentation.format);
    seen.modes.add(tl.presentation.mode);
    seen.entrances.add(tl.presentation.text_entrance);
    const fmt = tl.presentation.format;
    for (const s of tl.scenes) {
      for (const e of s.events || []) seen.kinds.add(e.kind);
      const { question, label } = predictItems(s);
      seen.labels.add(label ? 'label' : 'no-label');
      seen.rings.add(timetable(s, question, label, fmt).ring ? 'ring' : 'no-ring');
      seen.chip.add(predictionOf(s.look) ? 'chip' : 'none');
      const text = String(question?.text || '');
      if (/\p{Script=Tamil}/u.test(text)) seen.scripts.add('tamil');
      if (/\p{Script=Devanagari}/u.test(text)) seen.scripts.add('devanagari');
      if (/\$[^$]+\$/.test(text)) seen.scripts.add('tex');
      if (/\b[A-Z]{3,}\b.*\b[A-Z]{3,}\b/.test(text)) seen.scripts.add('wide-caps');
      assert.equal(question?.blank, true, `${name} ${s.scene_id}: §4.3's question is blank`);
      assert.equal(s.beats.every((/** @type {any} */ b) => !b.fill_item_id), true, `${name}: R-62 — no answer here`);
    }
  }
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.entrances].sort(), ['baseline_reveal', 'fade_rise']);
  assert.deepEqual([...seen.kinds].sort(), ['countdown', 'cue_term']);
  assert.deepEqual([...seen.scripts].sort(), ['devanagari', 'tamil', 'tex', 'wide-caps']);
  assert.deepEqual([...seen.rings].sort(), ['ring'], 'every prediction has a think ring');
  assert.deepEqual([...seen.labels].sort(), ['label', 'no-label'], 'a heading above the card, and none');
  assert.deepEqual([...seen.chip].sort(), ['chip', 'none'], 'a look carrying refs.prediction, and one without');
});

// --- pure helpers ---------------------------------------------------------------------------------------------------

test('predictItems: the first question with text is the prediction, the first heading its label, anything else is "others"', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'h1', kind: 'heading', text: 'Before we explain' },
    { id: 'q1', kind: 'question', text: 'What happens?', blank: true },
    { id: 'q2', kind: 'question', text: 'And then?' },
    { id: 'e', kind: 'question', text: '  ' },
    { id: 'h2', kind: 'heading', text: 'Second' },
    { id: 'b1', kind: 'bullet', text: 'A bullet' },
  ] });
  const { question, label, others } = predictItems(scene);
  assert.equal(question?.id, 'q1');
  assert.equal(label?.id, 'h1');
  assert.deepEqual(others.map((i) => i.id), ['q2', 'h2', 'b1'], 'an empty item is not one of them');
  assert.deepEqual(predictItems(/** @type {any} */ ({ board: [] })), { question: null, label: null, others: [] });
});

test('drawsScene: one question with text, at most one heading, nothing else, and nothing to show beside it', () => {
  const q = { id: 'q1', kind: 'question', text: 'What happens?', blank: true };
  assert.ok(drawsScene(/** @type {any} */ ({ board: [q] })));
  assert.ok(drawsScene(/** @type {any} */ ({ board: [{ id: 'h', kind: 'heading', text: 'First' }, q] })));
  assert.ok(drawsScene(/** @type {any} */ ({ board: [{ ...q, blank: false }] })), 'the page draws a non-blank one too');
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [] })), 'no question');
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [{ id: 'q', kind: 'question', text: ' ' }] })), 'no text');
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [q, { id: 'q2', kind: 'question', text: 'Second?' }] })), 'two questions');
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [q, { id: 'h1', kind: 'heading', text: 'A' }, { id: 'h2', kind: 'heading', text: 'B' }] })), 'two headings');
  for (const kind of ['bullet', 'definition', 'formula', 'takeaway', 'misconception', 'paragraph']) {
    assert.ok(!drawsScene(/** @type {any} */ ({ board: [q, { id: 'x', kind, text: 'x' }] })), kind);
  }
  // review D5: this template plans no media box, so a scene with a medium beside the question is board_generic's whole
  const panel = { kind: 'image', title: 'A lamp' };
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [q], side_panel: { panel } })), 'a shown side panel');
  assert.ok(drawsScene(/** @type {any} */ ({ board: [q], side_panel: { panel }, layout: { show_side_panel: false } })),
    'a hidden one is not shown, so it is still this template\'s');
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [q], media: { asset_key: 'a' } })), 'a scene medium');
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [q], poster: { asset_key: 'a' } })), 'a poster');
});

test('a scene this template does not draw is drawn by board_generic whole, every hook handed on', async () => {
  for (const name of ['predict.landscape.json', 'predict.portrait.json']) {
    const tl = load(name);
    tl.scenes[0].board.push({ id: 'x1', kind: 'bullet', text: 'A bullet no prediction draws' });
    const fmt = tl.presentation.format;
    assert.ok(!drawsScene(tl.scenes[0]));
    const run = renderStage(tl, { templates: { predict }, renderTex: recordingTex().renderTex });
    await run.stage.seek(20);
    assert.ok(run.stage.el.querySelector('.dyn-board'), `${name}: board_generic drew it`);
    assert.equal(run.stage.el.querySelector('.dyn-pd'), null);
    assert.deepEqual(predict.tex?.(tl.scenes[0], tl.scenes[0].look, fmt), boardGeneric.tex?.(tl.scenes[0], tl.scenes[0].look, fmt));
    const zones = ZONES[fmt];
    assert.deepEqual(predict.mediaBox?.(tl.scenes[0], zones, 'render', 'panel', { safe: false, slots: {} }),
      boardGeneric.mediaBox?.(tl.scenes[0], zones, 'render', 'panel', { safe: false, slots: {} }));
    run.done();
  }
});

test('the think time is design §1.4\'s formula over the words to read, floored by format and capped at 12 s (R-07)', () => {
  assert.equal(wordsToRead('What happens to the [[current]] here?'), 6, 'the plain words of the rich text');
  assert.equal(wordsToRead('   '), 0);
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    assert.equal(thinkSeconds(0, fmt), THINK_MIN[fmt], 'the floor');
    assert.equal(thinkSeconds(1, fmt), THINK_MIN[fmt], 'still the floor');
    const words = 12;
    assert.equal(thinkSeconds(words, fmt), (words / THINK_WPM) * 60 + THINK_EXTRA);
    assert.equal(thinkSeconds(500, fmt), THINK_MAX, 'the cap');
  }
  assert.deepEqual([THINK_MIN.landscape, THINK_MIN.portrait], [5, 4], 'R-07: >= 5 s in 16:9, >= 4 s in 9:16');
  assert.equal(THINK_WPM, 130);
});

test('timetable: a countdown event times the ring; otherwise the §1.4 think time from the entrance, clamped to the scene', () => {
  const tl = load('predict.landscape.json');
  // scene 0 has no countdown: the ring starts RING_DELAY after the question and runs the think time, clamped
  const s0 = tl.scenes[0];
  const first = predictItems(s0);
  const p0 = timetable(s0, first.question, first.label, 'landscape');
  assert.equal(p0.question, enterOf(s0, 'q1'));
  assert.equal(p0.label, enterOf(s0, 'h1'));
  const start = enterOf(s0, 'q1') + RING_DELAY;
  assert.equal(p0.ring?.start, start);
  const want = thinkSeconds(wordsToRead(String(first.question?.text)), 'landscape');
  assert.equal(p0.ring?.seconds, Math.min(want, s0.duration - start));
  assert.ok(/** @type {number} */ (p0.ring?.seconds) < want, 'clamped to what is left of this short fixture scene');
  // scene 1 has one: its own start and length win
  const s1 = tl.scenes[1];
  const second = predictItems(s1);
  const cd = s1.events.find((/** @type {any} */ e) => e.kind === 'countdown');
  assert.deepEqual(timetable(s1, second.question, second.label, 'landscape').ring, { start: cd.t, seconds: cd.dur });
  assert.notEqual(cd.t, enterOf(s1, 'q1') + RING_DELAY, 'the event, not the entrance');
  assert.deepEqual(eventFor(s1, 'countdown', 'q1'), { t: cd.t, dur: cd.dur });
  assert.equal(eventFor(s1, 'countdown', 'nope'), null);
  assert.equal(eventFor(s1, 'answer', 'q1'), null);
  // a scene with nothing left to think in draws no ring at all
  const tiny = { ...s1, duration: enterOf(s1, 'q1') + RING_DELAY + RING_MIN / 2, events: [] };
  assert.equal(timetable(tiny, second.question, second.label, 'landscape').ring, null);
  // no question at all: the timetable still answers (board_generic draws such a scene)
  assert.deepEqual(timetable(/** @type {any} */ ({ board: [], duration: 5 }), null, null, 'portrait').question, 0);
});

test('texOf: the $...$ spans of the label and the question, inline and in build order', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'h', kind: 'heading', text: 'Law $V$' },
    { id: 'q', kind: 'question', text: 'What is $I$ when $R$ doubles?' },
  ] });
  assert.deepEqual(texOf(scene), [{ tex: 'V', display: false }, { tex: 'I', display: false },
    { tex: 'R', display: false }]);
  assert.deepEqual(predict.tex?.(scene, null, 'portrait'), texOf(scene));
});

test('lineHeight: the type scale\'s Latin values and the Indic minimums (plan §2)', () => {
  assert.equal(lineHeight('heading', 'What happens?'), LINE_HEIGHT.heading.latin);
  assert.equal(lineHeight('heading', 'विद्युत परिपथ'), LINE_HEIGHT.heading.indic);
  assert.equal(lineHeight('label', 'மின்தடை pushes $V$'), LINE_HEIGHT.label.indic);
  assert.equal(lineHeight('body', 'Pause and predict'), LINE_HEIGHT.body.latin);
  const scale = CATALOGUE.type.scale;
  assert.equal(LINE_HEIGHT.heading.latin, scale.heading.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.heading.indic, scale.heading.line_height.indic[0], 'plan §2: Indic heading >= 1.25');
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.body.indic, scale.body.line_height.indic[0], 'plan §2: Indic body >= 1.45');
});

test('sizes keep R-29: labels at the label floor, the question above the text floor, 9:16 body text at least weight 600', () => {
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    const floors = TYPE_FLOORS[fmt];
    assert.equal(L.label.min, floors.label_min, `${fmt} label floor`);
    assert.equal(L.prompt.min, floors.label_min, `${fmt} the instruction is a label`);
    assert.equal(L.ringNum, floors.label_min, `${fmt} the ring's number is a label`);
    assert.equal(L.chip, floors.label_min, `${fmt} the chip is a label`);
    assert.ok(L.question.min >= floors.text_min, `${fmt} question >= the text floor`);
    if (floors.heavy_max_px && L.question.max <= floors.heavy_max_px) {
      assert.ok(L.question.weight >= /** @type {number} */ (floors.heavy_min_weight), `${fmt} question: R-29 weight`);
    }
  }
  assert.ok(LAYOUT.landscape.question.max <= TYPE_FLOORS.landscape.heading[1], 'R-29 16:9 headings <= 120');
  // The question is SET in R-29's heading band (its maximum is 96) but may come down below it rather than block a
  // render: the card holds three lines, the instruction and the 120 px think ring, so a long Indic question has to
  // shrink to stay in three (deviation 31's family, as `check`'s 56-72 px question does). Never below R-29's text
  // floor, which the loop above asserts.
  assert.ok(LAYOUT.landscape.question.max >= TYPE_FLOORS.landscape.heading[0], 'R-29 16:9 headings >= 84');
  assert.ok(LAYOUT.landscape.question.min < TYPE_FLOORS.landscape.heading[0],
    'the floor is below the band on purpose: see the comment above');
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

test('geometry: 16:9 the centre 60% from the title top to the body bottom; 9:16 the text column, both inside the critical zones', () => {
  const l = boxes('landscape', ZONES.landscape);
  assert.deepEqual([l.card.x0, l.card.x1, l.card.y0, l.card.y1], [442, 1478, 112, 900], 'the centre 60%');
  assert.deepEqual([l.label.x0, l.label.x1, l.label.y0], [442, 1478, 112]);
  assert.equal(l.label.y1, 112 + Math.round(LAYOUT.landscape.label.max * LINE_HEIGHT.label.latin));
  const p = boxes('portrait', ZONES.portrait);
  assert.deepEqual([p.card.x0, p.card.x1, p.card.y0, p.card.y1], [192, 780, 340, 1020], '9:16: the column both critical boxes hold');
  for (const [fmt, b] of /** @type {const} */ ([['landscape', l], ['portrait', p]])) {
    const zones = ZONES[fmt];
    for (const box of [b.label, b.card]) {
      assert.ok(insideBands({ x: box.x0, y: box.y0, w: box.x1 - box.x0, h: box.y1 - box.y0 }, zones.critical), `${fmt}: inside the critical area (R-59)`);
    }
  }
  // the chip's default room: one line right-aligned in the header row, never wider than chipMax
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const zones = ZONES[fmt];
    const c = chipBox(fmt, zones);
    assert.equal(c.x1, zones.header.x1, `${fmt}: right-aligned in the header row`);
    assert.equal(c.y0, zones.header.y0);
    assert.equal(c.x1 - c.x0, Math.min(LAYOUT[fmt].chipMax, zones.header.x1 - zones.header.x0));
    assert.ok(c.y1 > c.y0 && c.x0 >= zones.header.x0);
  }
});

test('slotRows: the slot table templates.py mirrors (widths from the boxes, the ring and the chip)', () => {
  const rows = slotRows(ZONES.landscape, ZONES.portrait);
  assert.deepEqual(Object.keys(rows).sort(), ['chip', 'label', 'prompt', 'question', 'ring']);
  assert.equal(rows.question.landscape.default.w, 1036 - 2 * LAYOUT.landscape.pad);
  assert.equal(rows.prompt.landscape.default.w, rows.question.landscape.default.w, 'the instruction shares the card\'s text box');
  assert.equal(rows.question.portrait.default.w, 588 - 2 * LAYOUT.portrait.pad);
  assert.equal(rows.label.landscape.default.w, 1036);
  assert.equal(rows.label.portrait.default.w, 588);
  assert.equal(rows.question.landscape.default.face, 'display');
  assert.equal(rows.question.landscape.default.prefer_lines, LAYOUT.landscape.question.prefer);
  assert.deepEqual(rows.ring.landscape.default.size, [40, 40]);
  assert.equal(rows.ring.landscape.default.w, LAYOUT.landscape.ring);
  assert.equal(rows.ring.landscape.default.critical, false, 'R-07: the think time is the ring itself');
  assert.equal(rows.ring.landscape.default.tabular_nums, true);
  assert.equal(rows.chip.landscape.default.critical, false, 'a reminder of another scene\'s words, cut with an ellipsis');
  assert.equal(rows.chip.portrait.default.w,
    chipBox('portrait', ZONES.portrait).x1 - chipBox('portrait', ZONES.portrait).x0 - 2 * LAYOUT.portrait.chipPad[1]);
  assert.equal(rows.prompt.landscape.default.max_lines, 1);
});

test('the catalogue\'s predict slots (templates.py) are slotRows exactly, and its consumes names the forms it draws', () => {
  // registered (B2c review CG-4: never skipped, so a missing slot table or consumes change fails here)
  assert.equal(/** @type {any} */ (CATALOGUE.templates).predict.implemented, true,
    'templates.py: predict needs implemented=True and slots=_predict_slots()');
  const cat = /** @type {any} */ (templateSlots('predict'));
  const want = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  assert.ok(cat, 'templates.py: predict has no slot table');
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
  assert.deepEqual(/** @type {any} */ (CATALOGUE.templates).predict.consumes.forms, {
    reveal: ['item'], countdown: ['item'], cue_term: ['item', 'item:term'],
  });
  assert.deepEqual(/** @type {any} */ (CATALOGUE.templates).predict.holds, ['think'], 'R-16: a prediction holds to think');
  assert.equal(/** @type {any} */ (CATALOGUE.templates).predict.push_ok, false, 'R-07: the ring never moves');
});

test('mediaBox: this template shows no picture or clip', () => {
  const scene = /** @type {any} */ ({ board: [{ id: 'q', kind: 'question', text: 'What happens?' }] });
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    for (const slot of ['main', 'panel', undefined]) {
      assert.equal(predict.mediaBox?.(scene, ZONES[fmt], 'render', slot, { safe: false, slots: {} }), null);
    }
  }
});

// --- painted behaviour ----------------------------------------------------------------------------------------------

test('the card rises 24 px as it fades in, its question 80 ms later and the instruction after that, in the personality\'s time', async () => {
  const tl = load('predict.portrait.json'); // Soft: fade_rise, x1.15
  const scale = tl.presentation.motion_scale;
  const scene = tl.scenes[0];
  const s = spyStage(tl);
  const at = enterOf(scene, 'q1');
  await s.seekTo(0, at - 0.1);
  let v = s.ctx(0).view;
  assert.equal(v.card.style.visibility, 'hidden', 'not yet in');
  await s.seekTo(0, at + 0.1);
  v = s.ctx(0).view;
  const card = Number(v.card.style.opacity);
  assert.ok(card > 0 && Number(v.question.style.opacity) < card, 'the card first, then its question');
  assert.match(v.card.style.transform, /^translate\(0, \d+px\)$/, 'a whole-px rise (2D, §P)');
  const dy = Number(/translate\(0, (\d+)px/.exec(v.card.style.transform)?.[1]);
  assert.ok(dy > 0 && dy < CARD_RISE);
  assert.ok(Number(v.prompt.style.opacity) < Number(v.question.style.opacity) || Number(v.prompt.style.opacity) === 0,
    'the instruction follows the question');
  await s.seekTo(0, at + (TEXT_DELAY + PROMPT_DELAY + PROMPT_ENTER) * scale + 0.1);
  v = s.ctx(0).view;
  assert.deepEqual([op(v.card.style.opacity), op(v.question.style.opacity), op(v.prompt.style.opacity)], [1, 1, 1]);
  assert.deepEqual([v.card.style.transform, v.prompt.style.transform], ['none', 'none']);
  // the card (no delay) is still entering after the unscaled 500 ms: Soft's x1.15 stretches it
  await s.seekTo(0, at + CARD_ENTER + 0.02);
  assert.ok(op(s.ctx(0).view.card.style.opacity) < 1);
  s.done();
});

test('the instruction is §4.3\'s "Pause and predict", under the question and above the ring, and no answer is ever drawn', async () => {
  for (const name of ['predict.landscape.json', 'predict.portrait.json']) {
    const tl = load(name);
    const s = spyStage(tl);
    await s.seekTo(0, tl.scenes[0].duration - 0.3);
    const v = s.ctx(0).view;
    assert.equal(v.prompt.textContent, PROMPT_TEXT, name);
    const host = v.qmask || v.question;
    const top = (/** @type {HTMLElement} */ el) => Number.parseFloat(el.style.top);
    assert.ok(top(host) < top(v.prompt), `${name}: the question above the instruction`);
    assert.ok(top(v.prompt) < top(v.ring.box), `${name}: the ring below both`);
    // R-62: the prediction scene has no answer part at all
    assert.equal(v.card.querySelector('.dyn-pd-a'), null);
    assert.equal(Object.keys(v).includes('answer'), false, 'no answer in the view');
    const slots = /** @type {any[]} */ (predict.texts?.(s.ctx(0))).map((e) => e.slot);
    assert.ok(!slots.includes('answer'), `${name}: ${slots.join(',')}`);
    // the card holds to the end of the scene (it never fades out to make room)
    assert.equal(op(v.card.style.opacity), 1);
    s.done();
  }
});

test('the think ring runs linearly with no sound: the arc empties and the number counts whole seconds down (R-07)', async () => {
  const tl = load('predict.portrait.json');
  const scene = tl.scenes[0];
  const cd = scene.events.find((/** @type {any} */ e) => e.kind === 'countdown');
  const s = spyStage(tl);
  await s.seekTo(0, cd.t - 0.1);
  let v = s.ctx(0).view;
  assert.ok(v.ring, 'the prediction has a ring');
  assert.equal(v.ring.box.style.visibility, 'hidden', 'not before its countdown');
  await s.seekTo(0, cd.t + 0.35);
  v = s.ctx(0).view;
  assert.equal(v.ring.box.style.visibility, 'visible');
  const full = Number(v.ring.arc.getAttribute('stroke-dasharray').split(' ')[0]);
  const offAt = (/** @type {any} */ r) => Number(r.arc.getAttribute('stroke-dashoffset'));
  const quarter = offAt(v.ring);
  assert.equal(v.ring.num.textContent, String(Math.ceil(cd.dur - 0.35)), 'whole seconds left');
  assert.equal(v.ring.num.style.fontVariantNumeric, 'tabular-nums');
  await s.seekTo(0, cd.t + cd.dur / 2);
  const half = offAt(s.ctx(0).view.ring);
  await s.seekTo(0, cd.t + cd.dur - 0.02);
  const end = offAt(s.ctx(0).view.ring);
  assert.ok(quarter < half && half < end, `the arc empties (${quarter} < ${half} < ${end})`);
  assert.ok(Math.abs(half - full / 2) < full * 0.08, 'linear: half the arc is gone at half the time');
  // after the ring's time it leaves; the number is cleared with it
  await s.seekTo(0, cd.t + cd.dur + 0.4);
  v = s.ctx(0).view;
  assert.equal(v.ring.box.style.visibility, 'hidden');
  assert.equal(v.ring.num.textContent, '');
  s.done();
});

test('the ring\'s arc is the one structure mark here, and the ring never moves (R-07)', async () => {
  const tl = load('predict.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(1, 2);
  const ctx = s.ctx(1);
  const v = ctx.view;
  assert.equal(v.ring.arc.getAttribute('stroke'), ctx.roles.color('struct.accent'));
  assert.equal(v.ring.box.style.transform, '', 'the ring is never transformed');
  assert.equal(v.question.style.color, rgb(ctx.roles.color('ink')));
  assert.equal(v.prompt.style.color, rgb(ctx.roles.color('ink2')));
  s.done();
});

test('the personality\'s text entrance: Crisp slides the question up inside a mask, Soft fades it', async () => {
  const crisp = load('predict.landscape.json');
  assert.equal(crisp.presentation.text_entrance, 'baseline_reveal');
  let s = spyStage(crisp);
  await s.seekTo(0, enterOf(crisp.scenes[0], 'q1') + 0.2);
  let v = s.ctx(0).view;
  assert.ok(v.qmask && v.question.parentElement === v.qmask && v.qmask.classList.contains('dyn-reveal-mask'));
  assert.equal(v.question.style.opacity, '1', 'a baseline reveal moves, it does not fade');
  assert.ok(v.head.mask, 'the label too');
  s.done();
  const soft = load('predict.portrait.json');
  s = spyStage(soft);
  await s.seekTo(0, enterOf(soft.scenes[0], 'q1') + 0.15);
  v = s.ctx(0).view;
  assert.equal(v.qmask, null);
  assert.ok(Number(v.question.style.opacity) < 1);
  s.done();
});

test('reduced motion (live player): the card, the question and the instruction fade in place', async () => {
  const tl = load('predict.landscape.json');
  tl.presentation.text_entrance = 'fade_rise';
  const s = spyStage(tl, { mode: 'live', reducedMotion: true });
  await s.seekTo(0, enterOf(tl.scenes[0], 'q1') + 0.1);
  const v = s.ctx(0).view;
  assert.equal(v.card.style.transform, 'none');
  assert.equal(v.question.style.transform, 'none');
  assert.equal(v.prompt.style.transform, 'none');
  assert.ok(Number(v.card.style.opacity) > 0 && Number(v.card.style.opacity) < 1);
  assert.equal(PROMPT_RISE, 16);
  s.done();
});

test('cue.term: the stage finds the question\'s [[term]] through itemEl', async () => {
  const tl = load('predict.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(0, 2.2);
  const ctx = s.ctx(0);
  assert.equal(predict.itemEl?.(ctx, 'q1'), ctx.view.question);
  assert.equal(predict.itemEl?.(ctx, 'h1'), ctx.view.head.text);
  assert.equal(predict.itemEl?.(ctx, 'nope'), null);
  /** @param {string} target */
  const pieces = (target) => termPieces(predict, ctx,
    /** @type {any} */ ({ kind: 'cue_term', target, parsed: parseTarget(target), start: 0, hold: 1, order: 0 }));
  assert.deepEqual(pieces('q1:term:0').map((p) => p.span.textContent), ['resistance']);
  assert.ok(pieces('q1').length >= 1, '<item> sweeps every term of the question');
  s.done();
});

test('focusRect: the card (a shape, so its outline is drawn outside it) and the label\'s text, on whole px, with its dim group', async () => {
  const tl = load('predict.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(0, 2);
  const real = s.ctx(0);
  const v = real.view;
  const rects = new Map([[v.card, { x: 442, y: 300.4, w: 1036, h: 420.6 }],
    [v.head.text, { x: 442, y: 112, w: 1036, h: 52.4 }]]);
  const ctx = { ...real, rectOf: (/** @type {Element} */ el) => rects.get(el) || null };
  assert.deepEqual(predict.focusRect?.(ctx, 'q1'), { rect: { x: 442, y: 300, w: 1036, h: 421 }, shape: 'rect', radius: 24, el: v.cardGroup, kind: 'shape' });
  assert.deepEqual(predict.focusRect?.(ctx, 'q1:term:0'), predict.focusRect?.(ctx, 'q1'), 'a term target points at its card');
  assert.deepEqual(predict.focusRect?.(ctx, 'h1'), { rect: { x: 442, y: 112, w: 1036, h: 52 }, shape: 'rect', radius: 0, el: v.head.el, kind: 'text' });
  assert.equal(predict.focusRect?.(ctx, 'zz'), null);
  // a card's outline is drawn whole just outside it (cues.js shapeArea), never clamped across its question
  const focus = /** @type {any} */ (predict.focusRect?.(ctx, 'q1'));
  const zones = ZONES.landscape;
  const outline = /** @type {any} */ (outlinePath(focus, shapeArea(zones)));
  const inner = { x0: outline.box.x0 + FOCUS_PX / 2, y0: outline.box.y0 + FOCUS_PX / 2, x1: outline.box.x1 - FOCUS_PX / 2, y1: outline.box.y1 - FOCUS_PX / 2 };
  const r = focus.rect;
  assert.deepEqual([r.x - inner.x0, r.y - inner.y0, inner.x1 - r.x - r.w, inner.y1 - r.y - r.h], [FOCUS_OUTSET, FOCUS_OUTSET, FOCUS_OUTSET, FOCUS_OUTSET]);
  // the label's text is text: its outline is clamped to the critical box holding it (R-59)
  const head = /** @type {any} */ (predict.focusRect?.(ctx, 'h1'));
  assert.deepEqual(clampOf(zones, head.rect), zones.critical[0]);
  s.done();
});

test('dimGroups: the label and the card; the stage owns their opacity and they are the same at every t', async () => {
  const tl = load('predict.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(0, 2);
  let ctx = s.ctx(0);
  const groups = /** @type {HTMLElement[]} */ (predict.dimGroups?.(ctx));
  assert.deepEqual(groups, [ctx.view.head.el, ctx.view.cardGroup]);
  for (const g of groups) assert.equal(g.style.opacity, '', 'the stage owns it; nothing dims without a look_here');
  assert.equal(predict.anchor?.(ctx), null);
  // a scene with no heading: the card alone
  await s.seekTo(1, 1.5);
  ctx = s.ctx(1);
  assert.deepEqual(predict.dimGroups?.(ctx), [ctx.view.cardGroup]);
  s.done();
});

test('texts(): the label, the question, the instruction and the ring\'s number, in the ink, size and weight painted now', async () => {
  const tl = load('predict.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(0, tl.scenes[0].duration - 0.6);
  const ctx = s.ctx(0);
  const entries = /** @type {any[]} */ (predict.texts?.(ctx));
  const bySlot = (/** @type {string} */ slot) => entries.filter((e) => e.slot === slot);
  const head = bySlot('label')[0];
  assert.deepEqual([head.role, head.critical, head.weight, head.size, head.text], ['label', true, 600, 40, 'Before we explain']);
  assert.equal(head.ink.toLowerCase(), ctx.onBackground.ink2.toLowerCase());
  assert.equal(head.bg, ctx.onBackground.bg, 'the label sits on the background');
  const q = bySlot('question')[0];
  assert.deepEqual([q.role, q.critical, q.weight], ['title', true, 700]);
  assert.equal(q.bg, ctx.roles.color('surface'), 'the card\'s surface');
  assert.equal(q.ink, ctx.roles.color('ink'));
  assert.ok(q.alpha > 0);
  assert.equal(q.text, 'What happens to the current if the resistance doubles?', 'plain text');
  const prompt = bySlot('prompt')[0];
  assert.deepEqual([prompt.role, prompt.critical, prompt.weight, prompt.text], ['label', true, 600, PROMPT_TEXT]);
  assert.equal(prompt.ink, ctx.roles.color('ink2'));
  const ring = bySlot('ring');
  assert.equal(ring.length, 1, 'the ring is running');
  assert.deepEqual([ring[0].role, ring[0].critical, ring[0].weight, ring[0].size], ['label', false, 700, LAYOUT.landscape.ringNum]);
  assert.match(ring[0].text, /^\d+$/);
  for (const e of entries) {
    assert.match(String(e.ink), /^#[0-9a-f]{6}$/i);
    assert.match(String(e.bg), /^#[0-9a-f]{6}$/i);
    assert.ok(e.alpha >= 0 && e.alpha <= 1);
    assert.ok(e.size >= e.min);
  }
  // before the ring runs, no number is reported
  await s.seekTo(0, 0.05);
  assert.equal(/** @type {any[]} */ (predict.texts?.(s.ctx(0))).filter((e) => e.slot === 'ring').length, 0);
  s.done();
});

test('preflight\'s remedies: a slot size cap and safe hold the text small; a scrim goes behind the label', async () => {
  const tl = load('predict.landscape.json');
  tl.scenes[0].look.fixes = { safe: false, slots: { question: { size: 88 }, prompt: { size: 40 }, label: { scrim: true } } };
  let s = spyStage(tl);
  await s.seekTo(0, 2);
  let ctx = s.ctx(0);
  assert.equal(ctx.view.size, 88, 'the cap (jsdom fits at the largest size allowed)');
  const scrim = ctx.roles.color('scrim');
  assert.equal(ctx.view.head.scrim, scrim);
  assert.ok(ctx.view.head.el.style.background !== '' && ctx.view.head.el.style.padding !== '');
  const head = /** @type {any[]} */ (predict.texts?.(ctx)).find((e) => e.slot === 'label');
  assert.equal(head.bg, scrimmedBg(scrim, ctx.onBackground, head.ink), 'measured on the scrim over the background');
  assert.notEqual(head.bg, ctx.onBackground.bg);
  s.done();
  const safe = load('predict.portrait.json');
  safe.scenes[0].look.fixes = { safe: true, slots: {} };
  s = spyStage(safe);
  await s.seekTo(0, 2);
  ctx = s.ctx(0);
  assert.equal(ctx.view.size, LAYOUT.portrait.question.min, 'safe: every slot at its minimum');
  assert.equal(ctx.view.head.size, LAYOUT.portrait.label.min);
  assert.equal(ctx.view.promptSize, LAYOUT.portrait.prompt.min);
  s.done();
});

test('update writes every property on every frame with a value (no \'\': a cleared property would make the DOM order-dependent)', async () => {
  const proto = /** @type {any} */ (Painter.prototype);
  const real = proto.style;
  /** @type {string[]} */
  const empty = [];
  let inside = false;
  const wrapped = { ...predict, update(/** @type {any} */ ctx, /** @type {number} */ t, /** @type {any} */ st) { inside = true; try { predict.update(ctx, t, st); } finally { inside = false; } } };
  proto.style = function style(/** @type {any} */ el, /** @type {string} */ prop, /** @type {any} */ value) {
    if (inside && String(value) === '') empty.push(`${el.className} ${prop}`);
    return real.call(this, el, prop, value);
  };
  try {
    for (const name of FIXTURES) {
      const tl = load(name);
      const { stage, done } = renderStage(tl, { templates: { predict: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex });
      const total = Math.round(tl.total_duration * tl.fps);
      for (let f = 0; f < total; f += 5) await stage.seek(f);
      done();
    }
  } finally {
    proto.style = real;
  }
  assert.deepEqual([...new Set(empty)], []);
});

// --- the "Your prediction?" chip (§4.3: carried by the EXPLAINING scene, so a host calls this API) -------------------

test('predictionOf: look.refs.prediction as plain text with its resolve time, null without one', () => {
  assert.equal(predictionOf(null), null);
  assert.equal(predictionOf(/** @type {any} */ ({})), null);
  assert.equal(predictionOf(/** @type {any} */ ({ refs: {} })), null);
  assert.equal(predictionOf(/** @type {any} */ ({ refs: { prediction: { text: '  ' } } })), null);
  assert.deepEqual(predictionOf(/** @type {any} */ ({ refs: { prediction: { text: 'Does $I$ halve?', resolve_t: 2.5 } } })),
    { text: 'Does I halve?', resolveT: 2.5 }, 'plain text, so the chip converts no TeX');
  assert.deepEqual(predictionOf(/** @type {any} */ ({ refs: { prediction: { text: 'Will it glow?' } } })),
    { text: 'Will it glow?', resolveT: null }, 'no resolve_t: the chip stays to the end');
  assert.deepEqual(predictionOf(/** @type {any} */ ({ refs: { prediction: { text: 'x', resolve_t: -1 } } })),
    { text: 'x', resolveT: null }, 'a malformed time is no time');
});

test('the chip: "Your prediction?" plus the question on one line, cut with an ellipsis, drawn in no TeX at all', async () => {
  const tl = load('predict.landscape.json');
  const tex = recordingTex();
  const s = spyStage(tl);
  await s.seekTo(1, 1);   // the stage builds a scene when it is first seeked into
  const ctx = { ...s.ctx(1), renderTex: tex.renderTex };
  const host = document.createElement('div');
  document.body.appendChild(host);
  const chip = buildChip(ctx, host);
  assert.ok(chip, 'scene 1 of the fixture carries refs.prediction');
  layoutChip(ctx, chip);
  assert.equal(chip.label.textContent, CHIP_TEXT);
  assert.match(String(chip.text.textContent), /^If V stays the same/, 'plain text: no TeX is converted for the chip');
  assert.equal(tex.seen.length, 0);
  assert.equal(chip.text.style.textOverflow, 'ellipsis');
  assert.equal(chip.text.style.whiteSpace, 'nowrap');
  const box = chipBox('landscape', ZONES.landscape);
  assert.deepEqual([chip.el.style.left, chip.el.style.top, chip.el.style.width],
    [`${box.x0}px`, `${box.y0}px`, `${box.x1 - box.x0}px`]);
  // a host that reserves its own room passes its own box
  const own = buildChip(ctx, host, { x0: 10, y0: 20, x1: 410, y1: 80 });
  layoutChip(ctx, own);
  assert.deepEqual([own.el.style.left, own.el.style.width], ['10px', '400px']);
  // the chip is reported to preflight as one non-critical label on the card surface
  const entries = chipTexts(ctx, chip);
  assert.equal(entries.length, 1);
  assert.deepEqual([entries[0].slot, entries[0].role, entries[0].critical, entries[0].overflow],
    ['chip', 'label', false, 0]);
  assert.equal(entries[0].bg, ctx.roles.color('surface'));
  assert.deepEqual(chipTexts(ctx, null), []);
  host.remove();
  s.done();
});

test('the chip fades in with the scene, takes the `done` state at the answering beat and then leaves (§4.3)', async () => {
  const tl = load('predict.landscape.json');
  const ok = tl.presentation.content.ok;
  const s = spyStage(tl);
  await s.seekTo(1, 1);
  const ctx = s.ctx(1);
  const scale = tl.presentation.motion_scale;
  const at = /** @type {number} */ (predictionOf(ctx.look)?.resolveT);
  const host = document.createElement('div');
  document.body.appendChild(host);
  const chip = buildChip(ctx, host);
  layoutChip(ctx, chip);
  paintChip(ctx, chip, 0.02);
  assert.ok(chip.alpha > 0 && chip.alpha < 1, 'fading in');
  assert.match(chip.body.style.transform, /^translate\(0, \d+px\)$/, 'a whole-px rise (2D, §P)');
  assert.match(chip.body.style.getPropertyValue('--dyn-state-border'), /00$/, 'no border yet');
  paintChip(ctx, chip, CHIP_ENTER * scale + 0.05);
  assert.equal(chip.alpha, 1);
  assert.deepEqual([chip.body.style.transform, chip.parts.check.style.visibility], ['none', 'hidden']);
  // the answering beat: the ok check pops and the ok border draws (plan §1.5)
  paintChip(ctx, chip, at + CHIP_POP * scale + 0.02);
  assert.equal(chip.body.style.getPropertyValue('--dyn-state-border').toLowerCase(), `${ok.toLowerCase()}ff`);
  assert.equal(chip.parts.check.style.visibility, 'visible');
  assert.equal(chip.parts.check.style.color, rgb(ok));
  assert.equal(chip.alpha, 1, 'it is read before it goes');
  // then it leaves
  paintChip(ctx, chip, at + (CHIP_POP + CHIP_HOLD) * scale + 0.3);
  assert.ok(chip.alpha < 1, 'leaving');
  paintChip(ctx, chip, at + (CHIP_POP + CHIP_HOLD) * scale + 1.5);
  assert.equal(chip.alpha, 0);
  assert.equal(chip.el.style.visibility, 'hidden');
  // a chip with no resolve_t stays to the end
  const open = { ...ctx, look: { ...ctx.look, refs: { prediction: { text: 'Will it glow?' } } } };
  const other = buildChip(open, host);
  layoutChip(open, other);
  paintChip(open, other, 30);
  assert.equal(other.alpha, 1);
  assert.equal(other.parts.check.style.visibility, 'hidden', 'nothing resolves it');
  host.remove();
  s.done();
});

test('the chip paints the same values at the same t whatever order it was painted in (a pure function of t)', async () => {
  const tl = load('predict.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(1, 1);
  const ctx = s.ctx(1);
  const times = [0, 0.2, 1, 2.4, 2.6, 3.2, 4];
  /** @param {number[]} order */
  const run = (order) => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const chip = buildChip(ctx, host);
    layoutChip(ctx, chip);
    /** @type {Map<number, string>} */
    const seen = new Map();
    for (const t of order) {
      paintChip(ctx, chip, t);
      seen.set(t, chip.el.outerHTML);
    }
    host.remove();
    return seen;
  };
  const forward = run(times);
  const backward = run([...times].reverse());
  for (const t of times) assert.equal(backward.get(t), forward.get(t), `t ${t}`);
  s.done();
});

// --- the fit, on a fake text layout ---------------------------------------------------------------------------------

/**
 * Install a fake text layout on HTMLElement (jsdom has none): a glyph is 0.55 em wide, words wrap greedily, a word
 * wider than its line breaks across lines. Enough for this template's fit: the height of the question, the label and
 * the instruction from the font size, the line height and the box's width. Returns the restore function.
 */
function fakeLayout() {
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const keys = ['offsetHeight', 'offsetWidth', 'clientWidth', 'offsetTop'];
  const saved = keys.map((k) => [k, Object.getOwnPropertyDescriptor(proto, k)]);
  const CW = 0.55;
  const TEXTS = '.dyn-pd-q, .dyn-pd-label, .dyn-pd-prompt';
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
    const words = textEl.querySelectorAll('.dyn-word');
    if (!words.length) return Math.max(1, Math.ceil(((textEl.textContent || '').length * CW * size) / (boxW || 1e9)));
    for (const w of words) {
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

test('the fit (fake layout): the largest size in 4 px steps whose question fits the card, never below the slot minimum', async () => {
  const restore = fakeLayout();
  try {
    for (const name of FIXTURES) {
      const tl = load(name);
      const fmt = /** @type {'landscape' | 'portrait'} */ (tl.presentation.format);
      const L = LAYOUT[fmt];
      const s = spyStage(tl);
      await s.seekTo(0, 1.5);
      const v = s.ctx(0).view;
      assert.ok(v.size >= L.question.min && v.size <= L.question.max, `${name}: ${v.size} in [${L.question.min}, ${L.question.max}]`);
      assert.equal((L.question.max - v.size) % 4, 0, `${name}: a 4 px step from the maximum`);
      assert.ok(Number.parseFloat(v.card.style.height) > 0, `${name}: the card has a height`);
      // the card is centred in the room the label leaves it, and inside it
      const top = Number.parseFloat(v.card.style.top);
      assert.ok(top >= 0, `${name}: ${top}`);
      const entries = /** @type {any[]} */ (predict.texts?.(s.ctx(0)));
      assert.deepEqual(entries.filter((e) => Number(e.overflow) > 0), [], `${name}: nothing overflows`);
      s.done();
    }
  } finally {
    restore();
  }
});

test('the fit (fake layout): a question that cannot fit at its minimum is reported as overflow, never clipped', async () => {
  const restore = fakeLayout();
  try {
    const tl = load('predict.portrait.json');
    tl.scenes[0].board[1].text = `${'What happens to the current when everything about this circuit changes at once? '.repeat(6)}`;
    const s = spyStage(tl);
    await s.seekTo(0, 2);
    const ctx = s.ctx(0);
    assert.equal(ctx.view.size, LAYOUT.portrait.question.min, 'held at the minimum');
    const over = /** @type {any[]} */ (predict.texts?.(ctx)).filter((e) => Number(e.overflow) > 0);
    assert.ok(over.length > 0, 'preflight is told (it blocks with "Shorten text")');
    assert.ok(over.some((e) => e.slot === 'question'), over.map((e) => e.slot).join(','));
    s.done();
  } finally {
    restore();
  }
});

test('the chip inside a real scene: a host template that calls the four hooks keeps the frame pure in t and reports it', async () => {
  // exactly what the requested hook does, with board_generic standing in for whichever template explains the outcome
  const tl = load('predict.landscape.json');
  const explaining = tl.scenes[1];
  explaining.look.template = 'board_generic';
  /** @type {any} */
  let chip = null;
  /** @type {any} */
  let hostCtx = null;
  const host = /** @type {any} */ ({
    ...boardGeneric,
    build(/** @type {any} */ ctx) {
      const view = boardGeneric.build(ctx);
      view.chip = buildChip(ctx, ctx.root);
      return view;
    },
    layout(/** @type {any} */ ctx) {
      boardGeneric.layout?.(ctx);
      layoutChip(ctx, ctx.view.chip);
    },
    update(/** @type {any} */ ctx, /** @type {number} */ t, /** @type {any} */ st) {
      boardGeneric.update(ctx, t, st);
      paintChip(ctx, ctx.view.chip, t);
      chip = ctx.view.chip;
      hostCtx = ctx;
    },
    texts(/** @type {any} */ ctx) {
      return [...(boardGeneric.texts?.(ctx) || []), ...chipTexts(ctx, ctx.view.chip)];
    },
  });
  const seam = { templates: { board_generic: host, predict } };
  const run = renderStage(tl, { ...seam, renderTex: recordingTex().renderTex });
  const at = Math.round((explaining.start + 1.0) * tl.fps);
  await run.stage.seek(at);
  assert.ok(chip, 'the host built the chip from look.refs.prediction');
  assert.equal(chip.alpha, 1);
  assert.match(String(chip.text.textContent), /^If V stays the same/);
  // preflight sees it among the scene's text, because the host hands chipTexts on
  const slots = /** @type {any[]} */ (host.texts(hostCtx));
  assert.ok(slots.some((e) => e.slot === 'chip'), slots.map((e) => e.slot).join(','));
  run.done();
  // and the frame is still a pure function of t with the chip on it
  const frames = [];
  for (let f = 0; f < Math.round(tl.total_duration * tl.fps); f += 6) frames.push(f);
  assert.deepEqual(await orderProblems(tl, frames, seam), []);
});
