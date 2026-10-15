// @ts-check
// templates/hook_question.js (DYNAMIC_VIDEO_DESIGN §4.3 hook_question, §2.5 / §3.3 the 9:16 hook band, B2 plan §1.4
// the template contract, §1.5 the cues, §1.11 the slots): the six fixture timelines
// (tests/compose/dynamic/make_hook_question_fixtures.py) through the harness's contract and order gates, the pure
// helpers (data, variants, geometry, slots, TeX, the "?" split), the painted behaviour in both formats (the per-word
// landing, the pill, the eyebrow, the "?" pop, the frame0_hook cover frame, the personality's scale, reduced motion,
// the cues the stage draws from the template's geometry, preflight's text entries and remedies) and the fit, on a fake
// text layout (jsdom has none).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { TYPE_FLOORS, ZONES, parseTarget, templateSlots } from '../../../js/dynamic/core.js';
import { itemTimings } from '../../../js/dynamic/state.js';
import hookQuestion, {
  DEFAULT_VARIANT, EYEBROW_ENTER, HOLD_AFTER, ID, LAYOUT, LINE_HEIGHT, PILL_TEXT, PILL_VARIANT, POP, POP_SCALE, RISE,
  SIZE_STEP, TEXT_DELAY, TRACKING_EM, VARIANTS, WORD_SECONDS, WORD_STAGGER, WORD_STAGGER_TOTAL, boxes, drawsScene,
  fitInBox, hasPill, hookItems, lineHeight, questionGlyph, setsAtFrame0, slotRows, texOf, variantOf,
} from '../../../js/dynamic/templates/hook_question.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['hook_question.landscape.json', 'hook_question.landscape.light.json',
  'hook_question.landscape.stress.json', 'hook_question.portrait.json', 'hook_question.portrait.light.json',
  'hook_question.portrait.stress.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { hook_question: hookQuestion } };

/**
 * A render stage of `tl` with hook_question through the stage's test seam, and the context each scene was built with.
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...hookQuestion, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return hookQuestion.build(ctx); } };
  const run = renderStage(tl, { templates: { hook_question: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
  /** Seek scene `i` to the frame nearest scene time `t`; returns that frame's exact scene time. @param {number} i @param {number} t */
  const seekTo = async (i, t) => {
    const scene = tl.scenes[i];
    const f = Math.round((Number(scene.start) + t) * tl.fps);
    await run.stage.seek(f);
    return f / tl.fps - Number(scene.start);
  };
  return { ...run, contexts, seekTo, ctx: (/** @type {number} */ i) => contexts.get(i) };
}

/** The scene-relative entrance of item `id` (state.js itemTimings). */
const enterOf = (/** @type {any} */ scene, /** @type {string} */ id) => /** @type {number} */ (itemTimings(scene).find((x) => x.id === id)?.enter);
/** An opacity as a number ('' is 1: none painted). @param {string} v */
const op = (v) => (v === '' ? 1 : Number(v));
/** A hex colour as the DOM reports a painted one. @param {string} hex */
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, whole-px focus, terms, TeX, entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(hookQuestion, load(name)), [], name);
});

test('every fixture paints the same frame forward, in reverse and on a fresh page (a frame is a pure function of t)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const total = Math.round(tl.total_duration * tl.fps);
    const frames = new Set();
    for (let f = 0; f < total; f += 6) frames.add(f);
    for (const s of tl.scenes) {
      for (const e of s.events || []) for (const d of [0, 0.2, 0.5]) frames.add(Math.min(total - 1, Math.round((s.start + e.t + d) * tl.fps)));
      frames.add(Math.min(total - 1, Math.round(s.start * tl.fps)));
      frames.add(Math.min(total - 1, Math.round(s.start * tl.fps) + 3));
    }
    assert.deepEqual(await orderProblems(tl, [...frames].sort((a, b) => a - b), SEAM), [], name);
  }
});

test('the fixtures cover what the B2 plan §G asks: both formats, dark and light, every variant, text case and cue', () => {
  const seen = { formats: new Set(), modes: new Set(), variants: new Set(), entrances: new Set(), cues: new Set(), scripts: new Set(), parts: new Set() };
  for (const name of FIXTURES) {
    const tl = load(name);
    seen.formats.add(tl.presentation.format);
    seen.modes.add(tl.presentation.mode);
    seen.entrances.add(tl.presentation.text_entrance);
    for (const s of tl.scenes) {
      seen.variants.add(variantOf(s.look));
      for (const e of s.events || []) seen.cues.add(`${e.kind}:${parseTarget(e.target).form}`);
      const { question, eyebrow } = hookItems(s);
      if (eyebrow) seen.parts.add('eyebrow');
      if (hasPill(variantOf(s.look))) seen.parts.add('pill');
      const text = String(question?.text || '');
      if (/[?？]\s*$/u.test(text)) seen.parts.add('question-mark');
      else seen.parts.add('no-question-mark');
      if (/\p{Script=Tamil}/u.test(text)) seen.scripts.add('tamil');
      if (/\p{Script=Devanagari}/u.test(text)) seen.scripts.add('devanagari');
      if (/\$[^$]+\$/.test(text)) seen.scripts.add('tex');
      if (/[A-Za-z]{30,}/.test(text)) seen.scripts.add('long-word');
      if (/(?:\b[A-Z]{3,}\b\s*){5}/.test(text)) seen.scripts.add('wide-caps');
      if (/\[\[[^\]]+\]\]/.test(text)) seen.scripts.add('term');
    }
  }
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.variants].sort(), [...VARIANTS].sort(), 'every §4.3 variant is drawn by a fixture');
  assert.deepEqual([...seen.entrances].sort(), ['baseline_reveal', 'fade_rise']);
  assert.deepEqual([...seen.cues].sort(), ['cue_term:item', 'cue_term:item:term', 'reveal:item']);
  assert.deepEqual([...seen.parts].sort(), ['eyebrow', 'no-question-mark', 'pill', 'question-mark']);
  assert.deepEqual([...seen.scripts].sort(), ['devanagari', 'long-word', 'tamil', 'term', 'tex', 'wide-caps']);
});

test('every fixture scene holds after the last word and lasts what §4.3 gives the template', () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const fmt = tl.presentation.format;
    const [min, max] = fmt === 'portrait' ? [3, 6] : [4, 10];
    const scale = Number(tl.presentation.motion_scale) || 1;
    for (const s of tl.scenes) {
      assert.ok(s.duration >= min - 1e-9 && s.duration <= max + 1e-9, `${name} ${s.scene_id}: ${s.duration}s`);
      const { question } = hookItems(s);
      const words = String(question?.text || '').replace(/\[\[|\]\]/g, '').split(/\s+/).length;
      const each = words > 1 ? Math.min(WORD_STAGGER, WORD_STAGGER_TOTAL / (words - 1)) : 0;
      const lands = enterOf(s, /** @type {string} */ (question?.id)) + scale * (each * (words - 1) + WORD_SECONDS);
      assert.ok(s.duration - lands >= HOLD_AFTER, `${name} ${s.scene_id}: holds ${(s.duration - lands).toFixed(2)}s`);
    }
  }
});

// --- pure helpers ---------------------------------------------------------------------------------------------------

test('hookItems: the first question with text is the hook, the first heading its eyebrow, anything else is others', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'h', kind: 'heading', text: 'Stakes' }, { id: 'e', kind: 'question', text: '  ' },
    { id: 'q', kind: 'question', text: 'Why?' }, { id: 'h2', kind: 'heading', text: 'Second' },
    { id: 'q2', kind: 'question', text: 'And why?' }, { id: 'b', kind: 'bullet', text: 'a point' },
  ] });
  const got = hookItems(scene);
  assert.equal(got.question?.id, 'q');
  assert.equal(got.eyebrow?.id, 'h');
  assert.deepEqual(got.others.map((i) => i.id), ['h2', 'q2', 'b'], 'a second heading or question, and any other item');
  assert.deepEqual(hookItems(/** @type {any} */ ({ board: [] })), { question: null, eyebrow: null, others: [] });
});

test('drawsScene: one question (and at most a heading), nothing else and no shown panel; else board_generic draws it', () => {
  const hook = /** @type {any} */ ({ board: [{ id: 'q', kind: 'question', text: 'Why?' }] });
  const withHeading = { board: [{ id: 'h', kind: 'heading', text: 'Stakes' }, ...hook.board] };
  assert.ok(drawsScene(hook) && drawsScene(/** @type {any} */ (withHeading)));
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [] })), 'no question: not this template\'s scene');
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [...hook.board, { id: 'b', kind: 'bullet', text: 'x' }] })));
  const panel = { ...hook, side_panel: { panel: { kind: 'image', title: 'P' }, show_at: 0 } };
  assert.ok(!drawsScene(/** @type {any} */ (panel)), 'a panel this template does not draw');
  assert.ok(drawsScene(/** @type {any} */ ({ ...panel, layout: { show_side_panel: false } })), 'a hidden panel is not drawn anyway');
});

test('a scene this template does not draw is drawn by board_generic whole, every hook handed on', async () => {
  for (const name of ['hook_question.landscape.json', 'hook_question.portrait.json']) {
    const tl = load(name);
    tl.scenes[0].board.push({ id: 'b1', kind: 'bullet', text: 'An extra point this template does not lay out' });
    const s = spyStage(tl);
    await s.seekTo(0, 1.0);
    assert.ok(s.stage.el.querySelector('.dyn-board'), `${name}: board_generic drew it`);
    assert.equal(s.stage.el.querySelector('.dyn-hq'), null);
    const fmt = tl.presentation.format;
    const scene = tl.scenes[0];
    assert.deepEqual(hookQuestion.tex?.(scene, scene.look, fmt), boardGeneric.tex?.(scene, scene.look, fmt));
    const zones = ZONES[fmt];
    const fixes = { safe: false, slots: {} };
    assert.deepEqual(hookQuestion.mediaBox?.(scene, zones, 'render', 'main', fixes),
      boardGeneric.mediaBox?.(scene, zones, 'render', 'main', fixes));
    // every context hook is board_generic's answer for the scene it drew
    const ctx = s.ctx(0);
    assert.ok(ctx.view.hqFallback);
    assert.deepEqual(hookQuestion.texts?.(ctx), boardGeneric.texts?.(ctx));
    assert.equal(hookQuestion.itemEl?.(ctx, 'q'), boardGeneric.itemEl?.(ctx, 'q'));
    assert.deepEqual(hookQuestion.dimGroups?.(ctx), boardGeneric.dimGroups?.(ctx));
    assert.deepEqual(hookQuestion.focusRect?.(ctx, 'q'), boardGeneric.focusRect?.(ctx, 'q'));
    s.done();
  }
});

test('variantOf / hasPill: §4.3\'s five hook kinds, the catalogue\'s list, and the pill for true_false only', () => {
  for (const v of VARIANTS) assert.equal(variantOf(/** @type {any} */ ({ variant: v })), v);
  for (const bad of [undefined, null, 'safe', 'nope', '']) assert.equal(variantOf(/** @type {any} */ ({ variant: bad })), DEFAULT_VARIANT);
  assert.equal(variantOf(null), DEFAULT_VARIANT);
  assert.ok(hasPill(PILL_VARIANT) && VARIANTS.includes(PILL_VARIANT));
  for (const v of VARIANTS) if (v !== PILL_VARIANT) assert.ok(!hasPill(v), `${v} draws no pill`);
  const cat = /** @type {any} */ (CATALOGUE.templates)[ID];
  for (const fmt of ['landscape', 'portrait']) {
    assert.deepEqual([...cat.variants[fmt]].sort(), [...VARIANTS].sort(), `${fmt}: templates.py variants`);
  }
  assert.equal(cat.class, 'prompt');
  assert.equal(cat.release, 'R1');
});

test('setsAtFrame0 (the frame0_hook rule): the hook is set whole on frame 0 of a reel\'s first scene only', () => {
  assert.ok(setsAtFrame0('portrait', 0));
  assert.ok(!setsAtFrame0('portrait', 1));
  assert.ok(!setsAtFrame0('landscape', 0), '16:9 has no cover frame: the words land');
});

test('texOf: the $...$ spans of the eyebrow and the question, inline, in order (what richWords mounts)', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'h', kind: 'heading', text: 'Law $V$' }, { id: 'q', kind: 'question', text: 'Does $I = V/R$ hold for a \\$5 wire?' },
  ] });
  assert.deepEqual(texOf(scene), [{ tex: 'V', display: false }, { tex: 'I = V/R', display: false }]);
  assert.deepEqual(hookQuestion.tex?.(scene, null, 'portrait'), texOf(scene));
  assert.deepEqual(texOf(/** @type {any} */ ({ board: [{ id: 'q', kind: 'question', text: 'No maths' }] })), []);
});

test('lineHeight: the type scale\'s Latin values and the Indic minimums (plan §2)', () => {
  assert.equal(lineHeight('display', 'Why does it fall?'), LINE_HEIGHT.display.latin);
  assert.equal(lineHeight('display', 'மின்சாரம் எப்படி?'), LINE_HEIGHT.display.indic);
  assert.equal(lineHeight('body', 'बिजली घर तक'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('body', '**Ohm** [[law]]'), LINE_HEIGHT.body.latin);
  const scale = CATALOGUE.type.scale;
  assert.equal(LINE_HEIGHT.display.latin, scale.display.line_height.latin[1], 'zones.py TYPE_SCALE display');
  assert.equal(LINE_HEIGHT.display.indic, scale.display.line_height.indic[0], 'plan §2: Indic display >= 1.25');
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.body.indic, scale.body.line_height.indic[0]);
  assert.ok(TRACKING_EM >= scale.display.tracking_em[0] && TRACKING_EM <= scale.display.tracking_em[1]);
});

test('questionGlyph: the trailing "?" becomes a span of its own, inside the word, and nothing else moves', () => {
  const host = document.createElement('div');
  host.innerHTML = '<span class="dyn-word">Why<span class="rt-k">now</span>?</span>';
  const span = questionGlyph(host);
  assert.ok(span, 'a question ends in one');
  assert.equal(span?.textContent, '?');
  assert.equal(host.textContent, 'Whynow?', 'the text is unchanged');
  assert.ok(host.querySelector('.dyn-word')?.contains(/** @type {Node} */ (span)), 'inside the word, so its entrance covers it');
  assert.ok(host.querySelector('.rt-k'), 'the term span survives');
  const plain = document.createElement('div');
  plain.innerHTML = '<span class="dyn-word">Statement</span>';
  assert.equal(questionGlyph(plain), null, 'a statement (true_false, stakes) has none');
  const full = document.createElement('div');
  full.textContent = 'なぜ？ ';
  assert.equal(questionGlyph(full)?.textContent, '？', 'the full-width mark, trailing space trimmed');
  assert.equal(questionGlyph(document.createElement('div')), null);
});

test('sizes keep R-29 and §4.3: 112-140 px over 3 lines in 16:9, the hook floors 88-104 over 4 lines in 9:16', () => {
  const land = LAYOUT.landscape;
  const port = LAYOUT.portrait;
  assert.deepEqual([land.question.min, land.question.max, land.question.lines], [112, 140, 3], '§4.3 16:9');
  assert.deepEqual([port.question.min, port.question.max, port.question.lines],
    [...TYPE_FLOORS.portrait.hook, 4], 'R-29 9:16 hook range, §2.5 four lines in the band');
  for (const [fmt, L] of /** @type {const} */ ([['landscape', land], ['portrait', port]])) {
    const floors = TYPE_FLOORS[fmt];
    assert.ok(L.question.min >= floors.text_min, `${fmt}: the text floor`);
    assert.ok(L.pill >= floors.label_min && L.eyebrow >= floors.label_min, `${fmt}: the label floor`);
    if (floors.heavy_max_px) {
      // R-29: text at or below heavy_max_px (9:16: 64) carries at least heavy_min_weight (600)
      const rows = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
      for (const slot of ['pill', 'eyebrow']) {
        assert.ok(rows[slot][fmt].default.size[1] > floors.heavy_max_px
          || rows[slot][fmt].default.weight >= /** @type {number} */ (floors.heavy_min_weight), `${fmt} ${slot}`);
      }
      assert.ok(L.question.min > floors.heavy_max_px, `${fmt}: the hook is never in the heavy range`);
    }
  }
  // the band holds four lines at the minimum and three at the maximum: the fit must see the height, not just the lines
  const band = boxes('portrait', ZONES.portrait).stack;
  const h = band.y1 - band.y0;
  assert.ok(4 * port.question.min * LINE_HEIGHT.display.latin <= h, 'four 88 px lines fit the band');
  assert.ok(4 * port.question.max * LINE_HEIGHT.display.latin > h, 'four 104 px lines do not');
  // the budget under the pill's row (the layout's own reading, pinned here): three Latin lines at the hook's floor,
  // two Indic ones (a 9:16 true_false hook in an Indic script is short; preflight blocks a longer one)
  const labelRow = Math.round(port.pill * LINE_HEIGHT.body.latin) + 2 * port.pillPad[0] + port.gap;
  const left = h - labelRow;
  assert.ok(3 * port.question.min * LINE_HEIGHT.display.latin <= left, 'three Latin lines under the pill');
  assert.ok(2 * port.question.min * LINE_HEIGHT.display.indic <= left, 'two Indic lines under the pill');
  assert.ok(3 * port.question.min * LINE_HEIGHT.display.indic > left, 'a third Indic line does not fit');
});

/**
 * preflight.py inside_bands: a box lies inside the union of the critical boxes, each box it overlaps holding its
 * whole width.
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

test('geometry: the 9:16 box is the hook band (y 340-760, frame0_hook), the 16:9 box the centre 60% below the header', () => {
  const port = boxes('portrait', ZONES.portrait).stack;
  assert.deepEqual(port, { x0: 192, y0: 340, x1: 888, y1: 760 }, '§2.5 / §3.3: the hook band');
  assert.deepEqual(port, CATALOGUE.regions.portrait.hook, 'the catalogue\'s region, never a literal');
  const land = boxes('landscape', ZONES.landscape).stack;
  assert.deepEqual([land.x0, land.x1], [442, 1478], 'centre-60');
  assert.ok(land.y0 >= ZONES.landscape.header.y1, 'clear of the kicker row');
  assert.equal(land.y1, 912, 'to the bottom of the critical box');
  for (const [fmt, b] of /** @type {const} */ ([['landscape', land], ['portrait', port]])) {
    const r = { x: b.x0, y: b.y0, w: b.x1 - b.x0, h: b.y1 - b.y0 };
    assert.ok(insideBands(r, ZONES[fmt].critical), `${fmt}: inside the critical area (R-59)`);
  }
});

test('slotRows: the slot table templates.py mirrors (the stack\'s width, the true_false line cost)', () => {
  const rows = slotRows(ZONES.landscape, ZONES.portrait);
  assert.deepEqual(Object.keys(rows).sort(), ['eyebrow', 'pill', 'question']);
  assert.equal(rows.question.landscape.default.w, 1036, 'the centre 60%');
  assert.equal(rows.question.portrait.default.w, 696, 'the hook band');
  assert.deepEqual(rows.question.landscape.default.size, [112, 140]);
  assert.deepEqual(rows.question.portrait.default.size, [88, 104]);
  assert.equal(rows.question.portrait.default.max_lines, 4);
  assert.equal(rows.question.portrait[PILL_VARIANT].max_lines, LAYOUT.portrait.pillLines, 'the pill costs a line');
  assert.ok(rows.question.portrait[PILL_VARIANT].max_lines < rows.question.portrait.default.max_lines);
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    assert.equal(rows.question[fmt].default.role, 'title');
    assert.equal(rows.question[fmt].default.weight, 700);
    assert.equal(rows.pill[fmt].default.weight, 700);
    assert.equal(rows.eyebrow[fmt].default.weight, 600);
    for (const slot of ['pill', 'eyebrow']) {
      assert.equal(rows[slot][fmt].default.max_lines, 1, `${slot} is a one-line label`);
      assert.equal(rows[slot][fmt].default.role, 'label');
      assert.deepEqual(rows[slot][fmt].default.size, [LAYOUT[fmt][slot === 'pill' ? 'pill' : 'eyebrow'],
        LAYOUT[fmt][slot === 'pill' ? 'pill' : 'eyebrow']]);
    }
  }
});

test('the catalogue\'s hook_question slots (templates.py) are slotRows exactly, and it is registered', () => {
  // registered (B2c review CG-4: never skipped, so a lost slot table fails here)
  assert.equal(/** @type {any} */ (CATALOGUE.templates)[ID].implemented, true);
  const cat = /** @type {any} */ (templateSlots(ID));
  const want = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  for (const slot of Object.keys(want)) {
    for (const fmt of Object.keys(want[slot])) {
      for (const variant of Object.keys(want[slot][fmt])) {
        const got = cat[slot][fmt][variant];
        const w = want[slot][fmt][variant];
        assert.deepEqual({ w: got.w, max_lines: got.max_lines, size: got.size, role: got.role, face: got.face, weight: got.weight, line_height: got.line_height, prefer_lines: got.prefer_lines },
          { w: w.w, max_lines: w.max_lines, size: w.size, role: w.role, face: w.face, weight: w.weight, line_height: w.line_height, prefer_lines: w.prefer_lines },
          `${slot} ${fmt} ${variant}`);
      }
    }
  }
  assert.deepEqual(Object.keys(cat).sort(), Object.keys(want).sort());
  // the forms per kind (B2c review CG-5): a term or the whole item for the cue, the whole item for a reveal
  assert.deepEqual(/** @type {any} */ (CATALOGUE.templates)[ID].consumes.forms,
    { reveal: ['item'], cue_term: ['item', 'item:term'] });
  assert.equal(/** @type {any} */ (CATALOGUE.templates)[ID].push_ok, false, 'a hook is text: no camera push');
});

test('mediaBox: none (mystery\'s picture is deferred to B4, deviation 16)', () => {
  const scene = /** @type {any} */ ({ board: [{ id: 'q', kind: 'question', text: 'Why?' }] });
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    for (const slot of ['main', 'panel', undefined]) {
      assert.equal(hookQuestion.mediaBox?.(scene, ZONES[fmt], 'render', slot, { safe: false, slots: {} }), null);
    }
  }
});

// --- painted behaviour ----------------------------------------------------------------------------------------------

test('16:9: the words land one by one from the question\'s beat, a 24 px whole-px rise in the personality\'s time', async () => {
  const tl = load('hook_question.landscape.json'); // Soft: fade_rise, x1.15
  const scale = tl.presentation.motion_scale;
  const scene = tl.scenes[0];
  const start = enterOf(scene, 'q');
  assert.ok(Math.abs(start - (scene.beats[0].start - 0.12)) < 1e-9, 'R-17: 120 ms before its beat');
  const s = spyStage(tl);
  await s.seekTo(0, Math.max(0, start - 0.1));
  let v = s.ctx(0).view;
  assert.deepEqual(v.words.map((/** @type {any} */ w) => op(w.style.opacity)).filter((/** @type {number} */ x) => x > 0), [], 'nothing before the beat');
  const each = Math.min(WORD_STAGGER, WORD_STAGGER_TOTAL / (v.words.length - 1));
  await s.seekTo(0, start + scale * each * 1.5);
  v = s.ctx(0).view;
  const alphas = v.words.map((/** @type {any} */ w) => op(w.style.opacity));
  assert.ok(alphas[0] > alphas[1] && alphas[1] > alphas[2], `one by one: ${alphas.slice(0, 3)}`);
  assert.ok(alphas[alphas.length - 1] === 0, 'the last word has not started');
  assert.match(v.words[0].style.transform, /^translate\(0, \d+px\)$/, 'a whole-px 2D rise (§P)');
  const dy = Number(/translate\(0, (\d+)px/.exec(v.words[0].style.transform)?.[1]);
  assert.ok(dy > 0 && dy < RISE);
  // every word is in after the last one's 450 ms (scaled)
  await s.seekTo(0, start + scale * (each * (v.words.length - 1) + WORD_SECONDS) + 0.05);
  v = s.ctx(0).view;
  assert.deepEqual([...new Set(v.words.map((/** @type {any} */ w) => `${op(w.style.opacity)}|${w.style.transform}`))], ['1|none']);
  assert.equal(v.alpha, 1, 'texts() reports the hook as settled');
  s.done();
});

test('9:16: the first scene of a reel is set whole on frame 0 (frame0_hook), a later scene still lands word by word', async () => {
  const tl = load('hook_question.portrait.stress.json');
  const s = spyStage(tl);
  await s.seekTo(0, 0);
  let v = s.ctx(0).view;
  assert.ok(v.cover, 'the reel\'s first scene');
  assert.deepEqual([...new Set(v.words.map((/** @type {any} */ w) => `${op(w.style.opacity)}|${w.style.transform}`))], ['1|none'],
    'frame 0 shows the hook fully set, in place');
  assert.equal(v.eye ? op(v.eye.el.style.opacity) : 1, 1, 'the eyebrow too');
  assert.equal(v.alpha, 1);
  // the second scene (not the cover) lands with the narration
  await s.seekTo(1, 0.02);
  v = s.ctx(1).view;
  assert.ok(!v.cover);
  assert.ok(v.words.every((/** @type {any} */ w) => op(w.style.opacity) < 1), 'still entering');
  s.done();
});

test('the "?" pops once after the last word lands, never under reduced motion, and the hook is one scale at every t', async () => {
  const tl = load('hook_question.landscape.stress.json');
  const s = spyStage(tl);
  await s.seekTo(0, 0);
  const v = s.ctx(0).view;
  assert.equal(v.glyph, null, 'the stakes hook is a statement: no "?"');
  s.done();
  const tl2 = load('hook_question.landscape.json');
  const s2 = spyStage(tl2);
  await s2.seekTo(0, 0.1);
  const v2 = s2.ctx(0).view;
  assert.ok(v2.glyph, 'the question mark has its own span');
  assert.equal(v2.glyph.style.transform, 'none', 'no scale before the pop');
  const popAt = v2.popAt;
  const half = await s2.seekTo(0, popAt + 0.15 * (tl2.presentation.motion_scale || 1));
  const scaled = /scale\(([\d.]+)\)/.exec(v2.glyph.style.transform);
  assert.ok(scaled && Number(scaled[1]) > 1 && Number(scaled[1]) <= 1 + POP_SCALE + 1e-9, `mid-pop at ${half}: ${v2.glyph.style.transform}`);
  await s2.seekTo(0, popAt + POP * (tl2.presentation.motion_scale || 1) + 0.1);
  assert.equal(v2.glyph.style.transform, 'none', 'back, once');
  s2.done();
  const rm = spyStage(load('hook_question.landscape.json'), { mode: 'live', reducedMotion: true });
  await rm.seekTo(0, 0.1);
  const vr = rm.ctx(0).view;
  await rm.seekTo(0, vr.popAt + 0.15);
  assert.equal(vr.glyph.style.transform, 'none', 'reduced motion: no pop (live only)');
  assert.deepEqual([...new Set(vr.words.map((/** @type {any} */ w) => w.style.transform))], ['none'], 'and no rise');
  rm.done();
});

test('true_false: the pill above the statement, in the content roles (never warn: R-63), with the words after it', async () => {
  const tl = load('hook_question.landscape.light.json'); // 16:9: the words land (a reel's first frame is set whole)
  const s = spyStage(tl);
  const scene = tl.scenes[0];
  const start = enterOf(scene, 'q');
  await s.seekTo(0, start + 0.02);
  const ctx = s.ctx(0);
  const v = ctx.view;
  assert.ok(v.badge, 'the pill');
  assert.equal(v.badge.el.textContent, PILL_TEXT);
  assert.equal(v.badge.el.style.background, rgb(ctx.roles.color('surface')));
  assert.equal(v.badge.el.style.color, rgb(ctx.onBackground.ink));
  assert.notEqual(v.badge.el.style.color, rgb(ctx.roles.color('warn')));
  assert.ok(op(v.badge.el.style.opacity) > op(v.words[0].style.opacity), 'the pill leads the words by TEXT_DELAY');
  assert.ok(TEXT_DELAY > 0);
  assert.equal(v.badge.g.nextElementSibling, v.textGroup, 'the pill sits straight above the statement');
  assert.equal(v.eye.g.nextElementSibling, v.badge.g, 'under the eyebrow');
  s.done();
  // every other variant draws no pill
  const plain = spyStage(load('hook_question.landscape.json'));
  await plain.seekTo(0, 0.5);
  assert.equal(plain.ctx(0).view.badge, null);
  plain.done();
});

test('the eyebrow (the scene\'s heading) is drawn above the hook in ink2 and fades in with the frame', async () => {
  const tl = load('hook_question.landscape.stress.json');
  const s = spyStage(tl);
  await s.seekTo(0, EYEBROW_ENTER * (tl.presentation.motion_scale || 1) + 0.05);
  const v = s.ctx(0).view;
  assert.ok(v.eye, 'the heading item is never dropped');
  assert.equal(v.eye.el.textContent, 'Stakes');
  assert.equal(v.eye.el.style.color, rgb(s.ctx(0).onBackground.ink2));
  assert.equal(op(v.eye.el.style.opacity), 1);
  assert.equal(v.eye.el.style.transform, 'none');
  assert.equal(v.stack.firstElementChild, v.eye.g, 'above the hook');
  s.done();
});

test('the cues the stage draws from this template\'s geometry: a term\'s spans, whole-px focus rects, stable dim groups', async () => {
  const tl = load('hook_question.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(0, 1.5);
  const ctx = s.ctx(0);
  const v = ctx.view;
  const terms = [...v.text.querySelectorAll('.rt-k')];
  assert.ok(terms.length > 0, 'richWords kept the [[term]] spans (cue.term finds them)');
  assert.equal(hookQuestion.itemEl?.(ctx, 'q'), v.text);
  assert.equal(hookQuestion.itemEl?.(ctx, 'nope'), null);
  const r = hookQuestion.focusRect?.(ctx, 'q:term:0');
  assert.ok(r, 'a focus rect for the hook');
  assert.ok(['x', 'y', 'w', 'h'].every((k) => Number.isInteger(/** @type {any} */ (r?.rect)[k])), 'whole px');
  assert.deepEqual([r?.shape, r?.kind, r?.el], ['rect', 'text', v.textGroup]);
  assert.equal(hookQuestion.focusRect?.(ctx, 'panel'), null);
  const groups = hookQuestion.dimGroups?.(ctx) || [];
  assert.ok(groups.includes(v.textGroup));
  await s.seekTo(0, 3.0);
  assert.deepEqual(hookQuestion.dimGroups?.(ctx), groups, 'the same elements at every t');
  assert.equal(hookQuestion.anchor?.(ctx), null, 'a hook is no morph anchor');
  s.done();
});

test('preflight\'s text entries (§7.12): the hook, the pill and the eyebrow, critical, with their ink, size and weight', async () => {
  const tl = load('hook_question.landscape.light.json');
  const s = spyStage(tl);
  await s.seekTo(0, 2.0);
  const ctx = s.ctx(0);
  const entries = hookQuestion.texts?.(ctx) || [];
  assert.deepEqual(entries.map((e) => e.slot), ['eyebrow', 'pill', 'question']);
  const q = /** @type {any} */ (entries.find((e) => e.slot === 'question'));
  assert.deepEqual([q.role, q.critical, q.weight], ['title', true, 700]);
  assert.equal(q.ink, ctx.onBackground.ink);
  assert.equal(q.bg, ctx.onBackground.bg, 'no scrim planned: the background itself');
  assert.ok(q.size >= LAYOUT.landscape.question.min && q.size <= LAYOUT.landscape.question.max);
  assert.equal(q.min, LAYOUT.landscape.question.min);
  assert.ok(q.text.includes('filament'), 'the plain text, for preflight\'s message');
  const pill = /** @type {any} */ (entries.find((e) => e.slot === 'pill'));
  assert.deepEqual([pill.role, pill.critical, pill.weight, pill.size, pill.text], ['label', true, 700, LAYOUT.landscape.pill, PILL_TEXT]);
  assert.equal(pill.bg, ctx.roles.color('surface'));
  const eye = /** @type {any} */ (entries.find((e) => e.slot === 'eyebrow'));
  assert.deepEqual([eye.role, eye.critical, eye.weight, eye.size], ['label', true, 600, LAYOUT.landscape.eyebrow]);
  assert.equal(eye.ink, ctx.onBackground.ink2);
  for (const e of entries) assert.ok(/^#[0-9a-f]{6}$/i.test(String(e.ink)) && /^#[0-9a-f]{6}$/i.test(String(e.bg)));
  s.done();
});

test('preflight\'s remedies (§7.12): a scrim fix puts the stack on a scrim and is reported as the text\'s background', async () => {
  const tl = load('hook_question.portrait.light.json');
  tl.scenes[0].look.fixes = { safe: false, slots: { question: { scrim: true } } };
  const s = spyStage(tl);
  await s.seekTo(0, 2.0);
  const ctx = s.ctx(0);
  assert.ok(ctx.view.scrim, 'the scrim colour');
  assert.ok(ctx.view.holder.style.background.length > 0, 'drawn behind the whole stack');
  const q = /** @type {any} */ ((hookQuestion.texts?.(ctx) || []).find((e) => e.slot === 'question'));
  assert.notEqual(q.bg, ctx.onBackground.bg, 'reported on the composited scrim');
  assert.match(q.bg, /^#[0-9a-f]{6}$/i);
  s.done();
  // a size cap in the fixes channel is an upper bound on the hook's size (core.js fixesOf, _shared.js slotRange)
  const tl2 = load('hook_question.portrait.light.json');
  tl2.scenes[0].look.fixes = { safe: false, slots: { question: { size: LAYOUT.portrait.question.min } } };
  const capped = spyStage(tl2);
  await capped.seekTo(0, 2.0);
  assert.equal(capped.ctx(0).view.size, LAYOUT.portrait.question.min);
  capped.done();
});

// --- the fit, on a fake text layout ---------------------------------------------------------------------------------

/**
 * Install a fake text layout on HTMLElement (jsdom has none): a glyph is 0.55 em wide, words wrap greedily, a word
 * wider than its line breaks across lines. Enough for this template's fit: the hook's height from its font size and
 * its box's width, a word's height and width, the box's width, the one-line eyebrow and pill. Returns the restore
 * function.
 */
function fakeLayout() {
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const keys = ['offsetHeight', 'offsetWidth', 'clientWidth', 'scrollWidth'];
  const saved = keys.map((k) => [k, Object.getOwnPropertyDescriptor(proto, k)]);
  const CW = 0.55;
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
  /** @param {Element} el the width of the box text in `el` is laid out in */
  const boxW = (el) => {
    const holder = /** @type {HTMLElement | null} */ (el.closest('.dyn-hq-holder'));
    return holder ? Number.parseFloat(holder.style.width) || 0 : 0;
  };
  /** @param {HTMLElement} textEl @param {number} width */
  const lines = (textEl, width) => {
    const size = fontSize(textEl);
    let n = 1;
    let x = 0;
    for (const w of textEl.querySelectorAll('.dyn-word')) {
      const ww = (w.textContent || '').length * CW * size;
      if (width > 0 && ww > width) {
        n += (x > 0 ? 1 : 0) + Math.ceil(ww / width) - 1;
        x = ww % width;
        continue;
      }
      const need = x > 0 ? x + CW * size + ww : ww;
      if (width > 0 && need > width) {
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
        const box = boxW(el);
        const host = /** @type {HTMLElement} */ (el.closest('.dyn-hq-text, .dyn-hq-eyebrow') || el);
        return (box > 0 && ww > box ? Math.ceil(ww / box) : 1) * lineH(host);
      }
      if (el.classList.contains('dyn-hq-text')) return lines(el, boxW(el)) * lineH(el);
      if (el.classList.contains('dyn-hq-eyebrow')) return lineH(el); // nowrap: always one line
      if (el.classList.contains('dyn-hq-pill')) {
        const pad = (el.style.padding || '0px').split(/\s+/).map((x) => Number.parseFloat(x));
        return Math.round(lineH(el) + 2 * (pad[0] || 0));
      }
      return 0;
    },
  });
  Object.defineProperty(proto, 'offsetWidth', { configurable: true, get() { return (/** @type {HTMLElement} */ (this).textContent || '').length * CW * fontSize(this); } });
  Object.defineProperty(proto, 'clientWidth', { configurable: true, get() { return boxW(/** @type {HTMLElement} */ (this)); } });
  Object.defineProperty(proto, 'scrollWidth', { configurable: true, get() { return (/** @type {HTMLElement} */ (this).textContent || '').length * CW * fontSize(this); } });
  return () => {
    for (const [k, d] of saved) {
      if (d) Object.defineProperty(proto, /** @type {string} */ (k), /** @type {PropertyDescriptor} */ (d));
      else delete proto[/** @type {string} */ (k)];
    }
  };
}

/**
 * A one-scene timeline of `fmt` whose hook is `question` (and `heading`), in `variant`.
 * @param {'landscape' | 'portrait'} fmt @param {string} question
 * @param {{heading?: string | null, variant?: string}} [opts]
 */
function hookTimeline(fmt, question, opts = {}) {
  const tl = load(fmt === 'portrait' ? 'hook_question.portrait.json' : 'hook_question.landscape.json');
  const scene = tl.scenes[0];
  scene.board = [...(opts.heading ? [{ id: 'h', kind: 'heading', text: opts.heading }] : []),
    { id: 'q', kind: 'question', text: question }];
  scene.events = [];
  scene.look.variant = opts.variant || 'question';
  return tl;
}

test('fitInBox: the largest 4 px size whose lines AND height fit, the minimum last, overflow only at the floor', () => {
  const restore = fakeLayout();
  try {
    const host = document.createElement('div');
    host.className = 'dyn-hq-holder';
    host.style.width = '696px';
    const el = document.createElement('div');
    el.className = 'dyn-hq-text';
    el.style.lineHeight = String(LINE_HEIGHT.display.latin);
    host.appendChild(el);
    document.body.appendChild(host);
    /** @param {string[]} texts */
    const words = (texts) => texts.map((t) => {
      const w = document.createElement('span');
      w.className = 'dyn-word';
      w.textContent = t;
      el.appendChild(w);
      return w;
    });
    const spec = { max: 104, min: 88, lines: 4, lh: LINE_HEIGHT.display.latin, step: SIZE_STEP };
    const short = words(['Why', 'now']);
    assert.deepEqual(fitInBox(el, spec, short, [], 420), { size: 104, fits: true, overflow: 0 }, 'one line: the maximum');
    assert.equal(el.style.fontSize, '104px', 'left at the chosen size');
    el.textContent = '';
    // four lines at 104 are 437 px: taller than the band, so the fit steps down although the line count allows it
    const long = words(['Why', 'doesn\'t', 'the', 'Moon', 'fall', 'down', 'to', 'Earth?']);
    const fit = fitInBox(el, spec, long, [], 420);
    assert.ok(fit.fits && fit.size < 104 && fit.size % SIZE_STEP === 0, `stepped down to ${fit.size}`);
    assert.ok(el.offsetHeight <= 420);
    // the same text with no height limit keeps a larger size (the height is what bound it)
    assert.ok(fitInBox(el, spec, long, [], Infinity).size >= fit.size);
    el.textContent = '';
    const huge = words(['Why', 'does', 'this', 'hook', 'run', 'past', 'every', 'line', 'the', 'band', 'has', 'room', 'for', 'today']);
    const over = fitInBox(el, spec, huge, [], 420);
    assert.ok(!over.fits && over.size === 88 && over.overflow > 0, `overflow at the floor: ${JSON.stringify(over)}`);
  } finally {
    restore();
    document.body.textContent = '';
  }
});

test('fit (9:16): the hook fits the band; the pill and the eyebrow take room off it; a long hook overflows and is reported', async () => {
  const restore = fakeLayout();
  try {
    const band = boxes('portrait', ZONES.portrait).stack;
    const height = band.y1 - band.y0;
    const plain = spyStage(hookTimeline('portrait', 'Why does it fall?'));
    await plain.seekTo(0, 1.0);
    let v = plain.ctx(0).view;
    assert.equal(v.size, LAYOUT.portrait.question.max, 'a short hook takes the largest size');
    assert.equal(v.overflow, 0);
    assert.ok(v.text.offsetHeight <= height);
    plain.done();
    // the pill: the same hook keeps the band, so its size is at most the plain one's
    const pill = spyStage(hookTimeline('portrait', 'Why does it fall and never stop?', { variant: PILL_VARIANT }));
    await pill.seekTo(0, 1.0);
    v = pill.ctx(0).view;
    const withPill = v.size;
    assert.ok(v.text.offsetHeight + v.badge.el.offsetHeight + LAYOUT.portrait.gap <= height, 'the stack fits the band');
    pill.done();
    const bare = spyStage(hookTimeline('portrait', 'Why does it fall and never stop?'));
    await bare.seekTo(0, 1.0);
    assert.ok(bare.ctx(0).view.size >= withPill, 'the pill never makes the hook larger');
    bare.done();
    // a hook past the band's room at the floor: reported as overflow (preflight blocks: "Shorten hook"), never clipped
    const long = spyStage(hookTimeline('portrait', 'Why does this very long hook run past every line the band has room for'));
    await long.seekTo(0, 1.0);
    v = long.ctx(0).view;
    assert.equal(v.size, LAYOUT.portrait.question.min);
    assert.ok(v.overflow > 0);
    const entry = /** @type {any} */ ((hookQuestion.texts?.(long.ctx(0)) || []).find((e) => e.slot === 'question'));
    assert.ok(Number(entry.overflow) > 0, 'preflight sees it');
    long.done();
  } finally {
    restore();
  }
});

test('fit (16:9): §4.3\'s 112-140 px over at most 3 lines in the centre 60%, and the eyebrow\'s overflow is reported', async () => {
  const restore = fakeLayout();
  try {
    const s = spyStage(hookTimeline('landscape', 'Why not?'));
    await s.seekTo(0, 1.0);
    assert.equal(s.ctx(0).view.size, LAYOUT.landscape.question.max);
    s.done();
    const long = spyStage(hookTimeline('landscape', 'Why does the Moon keep falling towards Earth?'));
    await long.seekTo(0, 1.0);
    const v = long.ctx(0).view;
    assert.ok(v.size < LAYOUT.landscape.question.max && v.size >= LAYOUT.landscape.question.min,
      `a longer hook is set smaller: ${v.size}px`);
    const rows = Math.round(v.text.offsetHeight / (v.size * LINE_HEIGHT.display.latin));
    // either it fits §4.3's three lines, or it is over them at the floor and preflight is told (never clipped)
    assert.ok(v.overflow > 0 ? rows > LAYOUT.landscape.question.lines : rows <= LAYOUT.landscape.question.lines,
      `${rows} lines at ${v.size}px, overflow ${v.overflow}`);
    long.done();
    const eyebrow = spyStage(hookTimeline('landscape', 'Why not?', { heading: 'A heading so long that it cannot sit on one line of the centre sixty per cent column' }));
    await eyebrow.seekTo(0, 1.0);
    const entry = /** @type {any} */ ((hookQuestion.texts?.(eyebrow.ctx(0)) || []).find((e) => e.slot === 'eyebrow'));
    assert.ok(Number(entry.overflow) > 0, 'a clipped eyebrow is reported, never silently cut');
    eyebrow.done();
  } finally {
    restore();
  }
});
