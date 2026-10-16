// @ts-check
// templates/worked_steps.js (B2; DYNAMIC_VIDEO_DESIGN §4.3 worked_steps, B2 plan §1.4 template contract, §1.5 cues
// and item states, §1.7 target grammar, §1.11 slots, §P): the four fixture timelines
// (tests/compose/dynamic/worked_steps_fixtures.py) through the harness's contract and order gates, the pure helpers
// (data, variants, geometry, slots, TeX, typing, states), the painted behaviour in both formats (a step per beat,
// number then text, current / past / done, the blank typing in, the think ring, the 9:16 compact stack, the formula's
// card and its entity tints, the cues the stage draws from the template's geometry, preflight's entries) and the fit,
// on a fake text layout (jsdom has none).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { TYPE_FLOORS, ZONES, templateSlots } from '../../../js/dynamic/core.js';
import { FOCUS_OUTSET, FOCUS_PX } from '../../../js/dynamic/cues.js';
import { itemTimings } from '../../../js/dynamic/state.js';
import {
  PANEL_DELAY, PANEL_ENTER, PANEL_KINDS, PANEL_LABEL_PX, PANEL_TERM, PANEL_TITLED_KINDS, STATE_WEIGHT, TEXT_RISE,
} from '../../../js/dynamic/templates/_shared.js';
import workedSteps, {
  COUNTDOWN_FADE, LAYOUT, LINE_HEIGHT, PAD, RISE, STATE_EASE, STEP_ENTER, TEXT_DELAY, TOP_KINDS, TYPE_CPS, anchored,
  boxes, countdownOf, drawsScene, lineHeight, panelFor, slotRows, stateAt, stateTrack, statesOn, stepItems, texOf,
  typeSeconds, typedAt, variantOf, wordChars,
} from '../../../js/dynamic/templates/worked_steps.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['worked_steps.landscape.json', 'worked_steps.landscape.light.json', 'worked_steps.portrait.json',
  'worked_steps.portrait.light.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { worked_steps: workedSteps } };

/**
 * A render stage of `tl` with worked_steps through the stage's test seam, and the context each scene was built with.
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...workedSteps, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return workedSteps.build(ctx); } };
  const run = renderStage(tl, { templates: { worked_steps: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
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
const stepOf = (/** @type {any} */ ctx, /** @type {string} */ id) => ctx.view.steps.find((/** @type {any} */ s) => s.item.id === id);
/** A hex colour as the DOM reports a painted one. @param {string} hex */
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;
/** An opacity as a number ('' is 1: none painted). @param {string} v */
const op = (v) => (v === '' ? 1 : Number(v));

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, whole-px focus, TeX, entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(workedSteps, load(name)), [], name);
});

test('every fixture paints the same frame forward, in reverse and on a fresh page (a frame is a pure function of t)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const total = Math.round(tl.total_duration * tl.fps);
    const frames = new Set();
    for (let f = 0; f < total; f += 12) frames.add(f);
    for (const s of tl.scenes) {
      for (const e of s.events || []) for (const d of [0, 0.2, 0.5]) frames.add(Math.min(total - 1, Math.round((s.start + e.t + d) * tl.fps)));
      frames.add(Math.min(total - 1, Math.round(s.start * tl.fps) + 3));
    }
    assert.deepEqual(await orderProblems(tl, [...frames].sort((a, b) => a - b), SEAM), [], name);
  }
});

test('the fixtures cover both formats, dark and light, every variant, both entrances and the events it draws', () => {
  const seen = { formats: new Set(), modes: new Set(), variants: new Set(), entrances: new Set(), kinds: new Set(), text: new Set() };
  for (const name of FIXTURES) {
    const tl = load(name);
    const fmt = tl.presentation.format;
    seen.formats.add(fmt);
    seen.modes.add(tl.presentation.mode);
    seen.entrances.add(tl.presentation.text_entrance);
    for (const s of tl.scenes) {
      assert.equal(s.look.template, 'worked_steps', name);
      assert.equal(s.look.background.frozen_at, 0, 'dense: the background is frozen from the scene start (R-55)');
      seen.variants.add(variantOf(s, fmt));
      for (const e of s.events || []) seen.kinds.add(e.kind);
      const text = (s.board || []).map((/** @type {any} */ b) => `${b.text || ''} ${b.justification || ''}`).join(' ');
      if (/\$[^$]+\$/.test(text)) seen.text.add('tex');
      if (/\p{Script=Devanagari}/u.test(text)) seen.text.add('devanagari');
      if (/[A-Za-z]{40,}/.test(text)) seen.text.add('long-word');
      if ((s.board || []).some((/** @type {any} */ b) => b.blank)) seen.text.add('blank');
    }
  }
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.variants].sort(), ['centred', 'list', 'split', 'stack']);
  assert.deepEqual([...seen.entrances].sort(), ['baseline_reveal', 'fade_rise']);
  assert.deepEqual([...seen.kinds].sort(), ['countdown', 'fill', 'look_here']);
  assert.deepEqual([...seen.text].sort(), ['blank', 'devanagari', 'long-word', 'tex']);
});

// --- pure helpers ---------------------------------------------------------------------------------------------------

test('stepItems: the example steps in board order, the first top line and the first formula with LaTeX', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'c', kind: 'callout_info', text: 'Example' }, { id: 'h', kind: 'heading', text: 'Later heading' },
    { id: 'f', kind: 'formula', latex: 'a = b' }, { id: 'f2', kind: 'formula', latex: 'c = d' },
    { id: 's1', kind: 'example_step', text: 'one' }, { id: 'e', kind: 'example_step', text: '  ' },
    { id: 's2', kind: 'example_step', text: 'two' },
  ] });
  const { top, steps, formula } = stepItems(scene);
  assert.equal(top?.id, 'c', 'the first item of TOP_KINDS with text');
  assert.deepEqual(steps.map((s) => s.id), ['s1', 's2'], 'a step with no text is not drawn');
  assert.equal(formula?.id, 'f');
  assert.deepEqual([...TOP_KINDS], ['heading', 'callout_info', 'paragraph']);
  assert.equal(stepItems(/** @type {any} */ ({})).steps.length, 0);
});

test('drawsScene: steps plus at most one top line and one formula; anything else is board_generic\'s', () => {
  const scene = (/** @type {any} */ board, /** @type {any} */ extra = {}) => /** @type {any} */ ({ board, ...extra });
  const step = { id: 's1', kind: 'example_step', text: 'one' };
  assert.equal(drawsScene(scene([step])), true);
  assert.equal(drawsScene(scene([step, { id: 'h', kind: 'heading', text: 'T' }, { id: 'f', kind: 'formula', latex: 'a' }])), true);
  assert.equal(drawsScene(scene([])), false, 'no step');
  assert.equal(drawsScene(scene([step, { id: 'b', kind: 'bullet', text: 'x' }])), false, 'a bullet it does not draw');
  assert.equal(drawsScene(scene([step, { id: 'h', kind: 'heading', text: 'a' }, { id: 'p', kind: 'paragraph', text: 'b' }])),
    false, 'two top lines');
  assert.equal(drawsScene(scene([step], { quiz: {} })), false);
  assert.equal(drawsScene(scene([step], { media: {} })), false);
  // a side panel: 16:9 only, never beside a formula card (the right 45% holds one visual), and only a kind
  // _shared.js draws - the one question `_fit_worked_steps` must ask too (B2c review CG-2)
  const sp = (/** @type {any} */ panel, /** @type {any} */ media = null) => ({ side_panel: { panel, media, show_at: 0 } });
  for (const kind of PANEL_KINDS) {
    const panel = PANEL_TITLED_KINDS.includes(kind) ? { kind, title: 'A plot' } : { kind, title: 'P', terminal: { command: 'ls' } };
    assert.equal(drawsScene(scene([step], sp(panel)), 'landscape'), true, kind);
    assert.equal(drawsScene(scene([step], sp(panel)), 'portrait'), false, `${kind} in 9:16`);
    assert.equal(drawsScene(scene([step, { id: 'f', kind: 'formula', latex: 'a' }], sp(panel)), 'landscape'), false,
      `${kind} beside a formula`);
  }
  for (const kind of ['skill_tree', 'quiz']) assert.equal(drawsScene(scene([step], sp({ kind })), 'landscape'), false, kind);
  for (const kind of PANEL_TITLED_KINDS) {
    assert.equal(drawsScene(scene([step], sp({ kind })), 'landscape'), false, `${kind} with no title to draw`);
  }
  assert.equal(drawsScene(scene([step], { side_panel: { panel: { kind: 'figure' }, show_at: 0 }, layout: { show_side_panel: false } })), true);
  assert.equal(drawsScene(scene([step]), 'portrait'), true, 'without a panel 9:16 draws the steps');
});

test('variantOf: 16:9 split with a formula, else centred; 9:16 stack when a step is beat-revealed, else list', () => {
  const base = [{ id: 's1', kind: 'example_step', text: 'one' }, { id: 's2', kind: 'example_step', text: 'two' }];
  const noBeats = /** @type {any} */ ({ board: base, beats: [] });
  const withBeats = /** @type {any} */ ({ board: base, beats: [{ beat_id: 'b', index: 0, phase: 'main', start: 1, speech_end: 2, end: 2, narration: 'x', board_item_id: 's2' }] });
  assert.equal(variantOf(noBeats, 'landscape'), 'centred');
  assert.equal(variantOf(/** @type {any} */ ({ ...noBeats, board: [...base, { id: 'f', kind: 'formula', latex: 'a' }] }), 'landscape'), 'split');
  // a panel takes the same visual box, so it splits the layout too (16:9 only)
  const panelled = /** @type {any} */ ({ ...noBeats, side_panel: { panel: { kind: 'image' }, media: null, show_at: 0 } });
  assert.equal(variantOf(panelled, 'landscape'), 'split');
  assert.equal(variantOf(panelled, 'portrait'), 'list', '9:16 draws no panel');
  assert.ok(panelFor(panelled, 'landscape'));
  assert.equal(panelFor(panelled, 'portrait'), null);
  assert.equal(variantOf(noBeats, 'portrait'), 'list');
  assert.equal(variantOf(withBeats, 'portrait'), 'stack');
  assert.equal(anchored(withBeats, ['s2']), true);
  assert.equal(anchored(withBeats, ['s1']), false);
  assert.equal(statesOn(noBeats, ['s1', 's2']), false, 'no walk and no fill: no states');
});

test('boxes: the named regions of §2.5 (16:9 left-55 beside right-45, else centre-60; 9:16 the text column)', () => {
  const l = boxes('landscape', 'split', ZONES.landscape, true);
  assert.deepEqual(l.steps, { x0: 96, y0: 112, x1: 1034, y1: 900 });
  assert.deepEqual(l.visual, { x0: 1058, y0: 112, x1: 1824, y1: 900 });
  assert.equal(l.bottom, 900);
  const c = boxes('landscape', 'centred', ZONES.landscape, false);
  assert.deepEqual(c.steps, { x0: 442, y0: 112, x1: 1478, y1: 900 });
  assert.equal(c.visual, null);
  const p = boxes('portrait', 'stack', ZONES.portrait, true);
  assert.deepEqual(p.steps, { x0: 192, y0: 340, x1: 780, y1: 1020 }, 'the text column both critical boxes hold (R-59)');
  assert.deepEqual(p.visual, { ...p.steps });
  assert.deepEqual(boxes('portrait', 'list', ZONES.portrait, false).visual, null);
});

test('slotRows: the step and justification boxes, R-29 sizes, the formula as a picture', () => {
  const rows = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  const textW = (/** @type {number} */ w, /** @type {'landscape' | 'portrait'} */ f) => w - 2 * PAD - LAYOUT[f].num - LAYOUT[f].numGap;
  assert.equal(rows.step.landscape.default.w, textW(1034 - 96, 'landscape'));
  assert.equal(rows.step.landscape.centred.w, textW(1478 - 442, 'landscape'));
  assert.equal(rows.step.portrait.default.w, textW(780 - 192, 'portrait'));
  assert.equal(rows.step.landscape.default.weight, STATE_WEIGHT, 'laid out at the current weight');
  assert.deepEqual(rows.row.portrait.default.size, [40, 40], 'R-29\'s 9:16 text floor');
  assert.equal(rows.why.landscape.default.size[0], TYPE_FLOORS.landscape.label_min);
  assert.equal(rows.why.portrait.default.size[0], TYPE_FLOORS.portrait.label_min);
  assert.equal(rows.formula.landscape.default.max_lines, null);
  assert.equal(rows.formula.landscape.default.line_height, 1, 'a TeX picture sets its own height');
  assert.equal(rows.formula.portrait.default.w, (780 - 192) - 2 * LAYOUT.portrait.card.padX);
  assert.equal(rows.heading.portrait.default.w, 696);
  // the panel's terminal card: 16:9 only (9:16 draws no panel), mono, the right 45% less its padding
  assert.deepEqual(Object.keys(rows.terminal), ['landscape']);
  assert.equal(rows.terminal.landscape.default.face, 'mono');
  assert.equal(rows.terminal.landscape.default.w, (1824 - 1058) - 2 * PANEL_TERM.landscape.pad);
  assert.deepEqual(rows.terminal.landscape.default.size, [PANEL_TERM.landscape.min, PANEL_TERM.landscape.max]);
  // R-29 (tests/compose/dynamic/test_templates.py pins the same rule on the catalogue's rows): no slot starts below
  // its role's floor, and 9:16 text at <= 64 px is at least weight 600
  for (const slot of Object.keys(rows)) {
    for (const f of /** @type {('landscape' | 'portrait')[]} */ (Object.keys(rows[slot]))) {
      for (const variant of Object.keys(rows[slot][f])) {
        const spec = rows[slot][f][variant];
        const floor = spec.role === 'label' || spec.role === 'chip' ? TYPE_FLOORS[f].label_min : TYPE_FLOORS[f].text_min;
        assert.ok(spec.size[0] >= floor, `${slot} ${f} ${variant}: ${spec.size[0]} >= ${floor}`);
        if (f === 'portrait' && spec.size[0] <= Number(TYPE_FLOORS.portrait.heavy_max_px)) {
          assert.ok(spec.weight >= Number(TYPE_FLOORS.portrait.heavy_min_weight), `${slot} ${variant}: weight`);
        }
      }
    }
  }
  // the line heights are the type scale's (the catalogue's `type.scale`: [min, max] per script)
  const scale = /** @type {any} */ (CATALOGUE.type).scale;
  assert.equal(LINE_HEIGHT.body.latin, Number(scale.body.line_height.latin[0]));
  assert.equal(LINE_HEIGHT.body.indic, Number(scale.body.line_height.indic[0]));
  assert.equal(LINE_HEIGHT.heading.latin, Number(scale.heading.line_height.latin[0]));
  assert.equal(LINE_HEIGHT.heading.indic, Number(scale.heading.line_height.indic[0]));
  assert.equal(lineHeight('body', 'धारा'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('heading', 'Current'), LINE_HEIGHT.heading.latin);
});

test('the catalogue\'s worked_steps slots (templates.py) are slotRows exactly, once it is registered', () => {
  const spec = /** @type {any} */ (CATALOGUE.templates).worked_steps;
  const cat = /** @type {any} */ (templateSlots('worked_steps'));
  if (!spec.implemented) {
    // before the integrator registers it (templates.py `implemented=True`, `slots=_worked_steps_slots()`), the
    // catalogue has no slot table for it; this test turns strict the moment it does
    assert.deepEqual(cat || {}, {}, 'an unregistered template has no slots in the catalogue');
    return;
  }
  const want = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  // the side-panel slice added the `terminal` row; until the integrator applies its templates.py change and
  // regenerates the catalogue, that one row is not there yet (the same handshake as `implemented` above)
  const pending = Object.keys(want).filter((slot) => !Object.prototype.hasOwnProperty.call(cat, slot));
  assert.deepEqual(pending.filter((slot) => slot !== 'terminal'), [], 'only the new `terminal` row may be pending');
  for (const slot of Object.keys(want)) {
    if (pending.includes(slot)) continue;
    for (const fmt of Object.keys(want[slot])) {
      for (const variant of Object.keys(want[slot][fmt])) {
        const got = cat[slot][fmt][variant];
        assert.deepEqual({ w: got.w, max_lines: got.max_lines, size: got.size, role: got.role, face: got.face, weight: got.weight, line_height: got.line_height, prefer_lines: got.prefer_lines },
          want[slot][fmt][variant], `${slot} ${fmt} ${variant}`);
      }
    }
  }
  assert.deepEqual(Object.keys(cat).sort(), Object.keys(want).filter((s) => !pending.includes(s)).sort());
  // the panel's own forms (`panel`, `panel:row`) and its `output` kind reach `consumes` with the integrator's
  // templates.py change; until then the B2c forms stand
  const forms = /** @type {any} */ (spec.consumes.forms);
  assert.deepEqual({ reveal: forms.reveal, fill: forms.fill, countdown: forms.countdown },
    { reveal: ['item'], fill: ['item'], countdown: ['item'] });
  assert.ok(forms.look_here.includes('item') && forms.look_here.includes('item:var') && forms.look_here.includes('item:step'));
  assert.equal(spec.dense, true, 'R-55 / R-49: the background freezes and SFX are off');
});

test('texOf: the formula (display, tinted), its 9:16 broken form, and every $TeX$ span of the texts', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'c', kind: 'callout_info', text: 'Example $x$' },
    { id: 'f', kind: 'formula', latex: 'I = V / R', text: 'the law $L$', variables: [{ symbol_latex: 'V', meaning: 'volt' }] },
    { id: 's1', kind: 'example_step', text: '$I = 3$', justification: 'why $w$' },
  ] });
  const land = texOf(scene, 'landscape');
  assert.equal(land.filter((x) => x.display).length, 1, 'one display formula in 16:9');
  assert.ok(land[0].tex.includes('\\color{'), 'the formula is tinted per symbol');
  assert.deepEqual(land.filter((x) => !x.display).map((x) => x.tex), ['L', 'x', '$I = 3$'.slice(1, -1), 'w']);
  const port = texOf(scene, 'portrait');
  assert.equal(port.filter((x) => x.display).length, 2, '9:16 declares the broken form too');
  assert.deepEqual(texOf(/** @type {any} */ ({ board: [] }), 'landscape'), []);
});

test('typing: a word is typed over its own characters at 30 chars/s, TeX counting its LaTeX source', () => {
  const span = (/** @type {string} */ text) => {
    const el = document.createElement('span');
    el.textContent = text;
    return el;
  };
  const words = [span('abc'), span('')];
  const chars = wordChars(words, [false, true], 'abc $x + y$');
  assert.deepEqual(chars, [3, 5], 'the TeX word counts "x + y"');
  assert.equal(typeSeconds(chars), 8 / TYPE_CPS);
  /** @param {number[]} got @param {number[]} want */
  const near = (got, want) => {
    assert.equal(got.length, want.length);
    got.forEach((x, i) => assert.ok(Math.abs(x - want[i]) < 1e-9, `${x} ~ ${want[i]}`));
  };
  near(typedAt(chars, null, 0), [1, 1]); // a blank with no fill moment is drawn filled
  near(typedAt(chars, 1, 0.9), [0, 0]);
  near(typedAt(chars, 1, 1 + 1.5 / TYPE_CPS), [0.5, 0]);
  near(typedAt(chars, 1, 1 + 4 / TYPE_CPS), [1, 0.2]);
  near(typedAt(chars, 1, 99), [1, 1]);
});

test('the state track: current at the entrance, done when the answer is typed, past when the next step enters', () => {
  assert.deepEqual(stateTrack(1, null, null), [{ at: 1, state: 'current' }]);
  assert.deepEqual(stateTrack(1, 4, null), [{ at: 1, state: 'current' }, { at: 4, state: 'past' }]);
  assert.deepEqual(stateTrack(1, null, 3), [{ at: 1, state: 'current' }, { at: 3, state: 'done' }]);
  assert.deepEqual(stateTrack(1, 4, 3), [{ at: 1, state: 'current' }, { at: 3, state: 'done' }, { at: 4, state: 'past' }]);
  assert.deepEqual(stateTrack(1, 4, 5), [{ at: 1, state: 'current' }, { at: 4, state: 'past' }],
    'past wins over a done that would come later: only the final answer keeps its check');
  const track = stateTrack(1, 4, 3);
  assert.equal(stateAt(track, 0.5), null, 'before its entrance');
  assert.deepEqual(stateAt(track, 1), { state: 'current', since: 1, from: null });
  assert.deepEqual(stateAt(track, 3.5), { state: 'done', since: 3, from: 'current' });
  assert.deepEqual(stateAt(track, 9), { state: 'past', since: 4, from: 'done' });
});

test('countdownOf: the earliest countdown on the item, its dur or 4 s; null for another item', () => {
  const scene = /** @type {any} */ ({ events: [
    { t: 5, kind: 'countdown', target: 's3' }, { t: 3, kind: 'countdown', target: 's3', dur: 1.5 },
    { t: 1, kind: 'countdown', target: 's1:step:1' }, { t: 2, kind: 'fill', target: 's3' },
  ] });
  assert.deepEqual(countdownOf(scene, 's3'), { t: 3, dur: 1.5 });
  assert.deepEqual(countdownOf(scene, 's1'), { t: 1, dur: 4 });
  assert.equal(countdownOf(scene, 'nope'), null);
});

test('PAD is the look_here outline\'s room, so text, state border and outline are evenly spaced', () => {
  assert.equal(PAD, FOCUS_OUTSET + FOCUS_PX);
});

// --- painted behaviour ----------------------------------------------------------------------------------------------

test('a step enters on its beat (120 ms before it), number first, text 80 ms later, a 24 px rise', async () => {
  // the 9:16 fixture's personality is Soft (`fade_rise`), so both the number and the text fade in and the delay
  // between them is visible in their opacities; the 16:9 fixture's Crisp entrance is the baseline reveal below
  const tl = load('worked_steps.portrait.json');
  const s = spyStage(tl);
  try {
    const scene = tl.scenes[0];
    const at = enterOf(scene, 's2');
    const scale = tl.presentation.motion_scale;
    await s.seekTo(0, at - 0.1);
    const step = stepOf(s.ctx(0), 's2');
    assert.equal(step.card.style.visibility, 'hidden', 'not on screen before its beat');
    await s.seekTo(0, at + STEP_ENTER * scale * 0.5);
    assert.equal(step.card.style.visibility, 'visible');
    const eNum = op(step.num.style.opacity);
    const eText = op(step.text.style.opacity);
    assert.ok(eNum > 0 && eNum < 1, `the number is entering (${eNum})`);
    assert.ok(eNum > eText, `the text follows the number (${eNum} > ${eText})`);
    assert.ok(/^translate\(0, \d+px\)$/.test(step.num.style.transform), `a whole-pixel rise (${step.num.style.transform})`);
    await s.seekTo(0, at + (STEP_ENTER + TEXT_DELAY) * scale + 0.1);
    assert.equal(op(step.num.style.opacity), 1);
    assert.equal(step.num.style.transform, 'none');
    assert.equal(op(step.text.style.opacity), 1);
    assert.ok(RISE === 24);
  } finally {
    s.done();
  }
});

test('16:9 (Crisp): the text is revealed from its baseline inside its mask, after the number', async () => {
  const tl = load('worked_steps.landscape.json');
  assert.equal(tl.presentation.text_entrance, 'baseline_reveal');
  const s = spyStage(tl);
  try {
    const at = enterOf(tl.scenes[0], 's2');
    await s.seekTo(0, at + 0.03);
    const step = stepOf(s.ctx(0), 's2');
    assert.ok(step.mask, 'the text sits in a reveal mask');
    assert.ok(op(step.num.style.opacity) > 0, 'the number has started');
    assert.equal(op(step.text.style.opacity), 0, 'the text has not (80 ms later)');
    await s.seekTo(0, at + (STEP_ENTER + TEXT_DELAY) * tl.presentation.motion_scale + 0.1);
    assert.equal(op(step.text.style.opacity), 1);
    assert.equal(step.text.style.transform, 'none');
  } finally {
    s.done();
  }
});

test('16:9: the step entered last is current (700, full ink, the 2 px border), the one before it past (ink2)', async () => {
  const tl = load('worked_steps.landscape.json');
  const s = spyStage(tl);
  try {
    const scene = tl.scenes[0];
    const ctx = () => s.ctx(0);
    await s.seekTo(0, enterOf(scene, 's2') + STATE_EASE * 2);
    const two = stepOf(ctx(), 's2');
    const one = stepOf(ctx(), 's1');
    const roles = ctx().onBackground;
    assert.equal(two.card.style.fontWeight, String(STATE_WEIGHT));
    assert.equal(two.card.style.color, rgb(roles.ink));
    assert.match(two.card.style.getPropertyValue('--dyn-state-border'), /ff$/, 'the current step\'s border is fully on');
    assert.equal(one.card.style.fontWeight, String(LAYOUT.landscape.weight));
    assert.equal(one.card.style.color, rgb(roles.ink2), 'the earlier step is ink2');
    assert.match(one.card.style.getPropertyValue('--dyn-state-border'), /00$/, 'and keeps no border');
  } finally {
    s.done();
  }
});

test('the blank answer: the rule until its fill event, then the words type in, and the step turns done', async () => {
  const tl = load('worked_steps.landscape.json');
  const fill = /** @type {any} */ (tl.scenes[0].events.find((/** @type {any} */ e) => e.kind === 'fill'));
  const s = spyStage(tl);
  try {
    await s.seekTo(0, fill.t - 0.3);
    const step = stepOf(s.ctx(0), 's3');
    assert.equal(step.blank.style.visibility, 'visible', 'the rule stands in for the answer');
    assert.equal(op(step.words[0].style.opacity), 0, 'nothing of the answer is shown');
    assert.equal(/** @type {any[]} */ (workedSteps.texts?.(s.ctx(0))).find((e) => e.el === step.body).alpha, 0);
    await s.seekTo(0, fill.t + step.typeFor / 2);
    assert.ok(op(step.words[0].style.opacity) > 0, 'the answer is typing in');
    assert.ok(op(step.blank.style.opacity) < 1, 'the rule is fading out');
    await s.seekTo(0, fill.t + step.typeFor + STATE_EASE * 2);
    assert.equal(op(step.words[0].style.opacity), 1);
    assert.equal(step.blank.style.visibility, 'hidden');
    assert.equal(step.parts.check.style.visibility, 'visible', '§4.3: the final answer gets the ok check');
    assert.equal(step.parts.check.style.color, rgb(s.ctx(0).roles.color('ok')));
    assert.equal(step.card.style.getPropertyValue('--dyn-state-border').slice(0, 7), s.ctx(0).roles.color('ok'));
  } finally {
    s.done();
  }
});

test('the think ring: it empties over the countdown and fades out, and never draws outline or a cue role', async () => {
  const tl = load('worked_steps.landscape.json');
  const count = /** @type {any} */ (tl.scenes[0].events.find((/** @type {any} */ e) => e.kind === 'countdown'));
  const s = spyStage(tl);
  try {
    await s.seekTo(0, count.t + COUNTDOWN_FADE);
    const step = stepOf(s.ctx(0), 's3');
    assert.ok(step.ring, 'the countdown gave the step a ring');
    assert.equal(step.ring.el.style.visibility, 'visible');
    const early = Number(step.ring.circle.getAttribute('stroke-dashoffset'));
    assert.equal(step.ring.circle.getAttribute('stroke'), s.ctx(0).onBackground.ink2, 'a content ink, never mark / focus');
    await s.seekTo(0, count.t + count.dur * 0.9);
    assert.ok(Number(step.ring.circle.getAttribute('stroke-dashoffset')) > early, 'the ring empties');
    await s.seekTo(0, count.t + count.dur + COUNTDOWN_FADE + 0.2);
    assert.equal(step.ring.el.style.visibility, 'hidden');
  } finally {
    s.done();
  }
});

test('the formula card: it fades in, its symbols take their entity colours, and it is the morph anchor', async () => {
  const tl = load('worked_steps.landscape.json');
  const s = spyStage(tl);
  try {
    await s.seekTo(0, 1.5);
    const ctx = s.ctx(0);
    const card = ctx.view.card;
    assert.equal(card.box.style.visibility, 'visible');
    assert.equal(op(card.box.style.opacity), 1);
    assert.deepEqual(card.tints.map((/** @type {any} */ t) => t.symbol), ['V', 'R', 'I']);
    for (const tint of card.tints) {
      const colour = ctx.entityRole(tint.symbol);
      assert.ok(colour && colour.startsWith('entity:'), `${tint.symbol} has a lesson-wide entity colour`);
    }
    // the card is the shared-element morph's anchor (§4.9 formula -> worked_steps), on whole px
    const rect = { x: 1098.4, y: 340.2, w: 686, h: 120 };
    const anchor = workedSteps.anchor?.({ ...ctx, rectOf: (/** @type {Element} */ el) => (el === card.texEl ? rect : null) });
    assert.equal(anchor?.el, card.texEl);
    assert.deepEqual(anchor?.rect, { x: 1098, y: 340, w: 686, h: 120 });
    assert.equal(workedSteps.anchor?.(ctx), null, 'nothing before the formula has a box');
  } finally {
    s.done();
  }
});

test('9:16: the stack keeps every earlier step compact and only the current one large', async () => {
  const tl = load('worked_steps.portrait.json');
  const s = spyStage(tl);
  try {
    const scene = tl.scenes[0];
    await s.seekTo(0, enterOf(scene, 's2') + 0.6);
    const ctx = s.ctx(0);
    const one = stepOf(ctx, 's1');
    const two = stepOf(ctx, 's2');
    assert.equal(two.compactNow, false, 'the current step is large');
    assert.equal(one.compactNow, true, 'the one before it is compact');
    assert.equal(one.card.style.fontSize, `${ctx.view.compactSize}px`);
    assert.equal(two.card.style.fontSize, `${ctx.view.largeSize}px`);
    assert.ok(ctx.view.compactSize <= 40 + 0.001, 'R-29: a compact row is at the 9:16 text floor');
    assert.equal(one.card.style.padding, `${LAYOUT.portrait.padCompact}px ${PAD}px`);
    assert.equal(one.why ? one.why.style.visibility : 'hidden', 'hidden', 'a compact row hides its justification');
    // every step that entered is still on screen (never a carousel, RV L-13)
    for (const id of ['s1', 's2']) assert.equal(stepOf(ctx, id).card.style.visibility, 'visible');
    assert.ok(one.top < two.top, 'the stack keeps board order');
  } finally {
    s.done();
  }
});

test('the cues the stage draws from the geometry: a step, the n-th step, a symbol and the top line', async () => {
  const tl = load('worked_steps.landscape.json');
  const s = spyStage(tl);
  try {
    await s.seekTo(0, 4.4);
    const real = s.ctx(0);
    // jsdom lays nothing out: hand the template the boxes a browser would report (list_build.test.js's way)
    /** @type {Map<Element, {x: number, y: number, w: number, h: number}>} */
    const rects = new Map();
    real.view.steps.forEach((/** @type {any} */ step, i) => rects.set(step.card, { x: 96, y: 300 + i * 100, w: 938, h: 94 }));
    rects.set(real.view.head.text, { x: 96, y: 112, w: 1728, h: 110 });
    rects.set(real.view.card.box, { x: 1058, y: 300, w: 766, h: 300 });
    rects.set(real.view.card.texEl, { x: 1098, y: 340, w: 686, h: 120 });
    const ctx = { ...real, rectOf: (/** @type {Element} */ el) => rects.get(el) || null };
    const second = workedSteps.focusRect?.(ctx, 'x1:step:2');
    const byId = workedSteps.focusRect?.(ctx, 's2');
    assert.ok(second, 'the §1.7 <item>:step:<n> form resolves to the scene\'s n-th step');
    assert.deepEqual(second?.rect, byId?.rect, 'the 2nd step is s2');
    assert.equal(second?.kind, 'shape', 'a card: the outline runs outside its state border (V4)');
    assert.equal(second?.radius, 24);
    assert.equal(workedSteps.focusRect?.(ctx, 'x1:step:9'), null, 'a step the scene does not have');
    const head = workedSteps.focusRect?.(ctx, 'x1');
    assert.equal(head?.kind, 'text');
    const sym = workedSteps.focusRect?.(ctx, 'f1:var:V');
    assert.ok(sym, 'a symbol of the formula (its card when MathJax drew no glyph)');
    assert.ok(['shape'].includes(String(sym?.kind)));
    // dimGroups: the top line, every step and the formula; the same elements at every t
    const groups = /** @type {Element[]} */ (workedSteps.dimGroups?.(ctx));
    assert.equal(groups.length, 1 + ctx.view.steps.length + 1);
    assert.ok(groups.every((g) => g.classList.contains('dyn-group')));
    // itemEl: a step's text and then its justification (the item's field order)
    const els = /** @type {Element[]} */ (workedSteps.itemEl?.(ctx, 's1'));
    assert.deepEqual(els, [stepOf(ctx, 's1').text, stepOf(ctx, 's1').why]);
    assert.equal(workedSteps.itemEl?.(ctx, 'nope'), null);
  } finally {
    s.done();
  }
});

test('texts(): the top line, each step, each justification, the formula and its meaning, with their slots', async () => {
  const tl = load('worked_steps.landscape.json');
  const s = spyStage(tl);
  try {
    await s.seekTo(0, 8.0);
    const entries = /** @type {any[]} */ (workedSteps.texts?.(s.ctx(0)));
    const slots = entries.map((e) => e.slot);
    assert.deepEqual([...new Set(slots)].sort(), ['formula', 'heading', 'meaning', 'step', 'why']);
    assert.ok(entries.every((e) => e.critical === true));
    assert.ok(entries.every((e) => /^#[0-9a-f]{6}$/i.test(e.ink) && /^#[0-9a-f]{6}$/i.test(e.bg)));
    assert.ok(entries.every((e) => e.alpha >= 0 && e.alpha <= 1 && e.size >= e.min));
    const step = entries.find((e) => e.slot === 'step');
    assert.match(step.text, /^1 /, 'the step number is part of what preflight reads');
  } finally {
    s.done();
  }
});

test('reduced motion (live only): entrances fade without moving', async () => {
  const tl = load('worked_steps.landscape.json');
  const s = spyStage(tl, { mode: 'live', reducedMotion: true });
  try {
    const at = enterOf(tl.scenes[0], 's2');
    await s.seekTo(0, at + 0.1);
    const step = stepOf(s.ctx(0), 's2');
    assert.equal(step.num.style.transform, 'none');
    assert.ok(op(step.num.style.opacity) < 1);
    assert.ok(RISE > 0 && TEXT_RISE > 0);
  } finally {
    s.done();
  }
});

test('a scene it does not draw whole is handed to board_generic, every hook with it', async () => {
  const tl = load('worked_steps.landscape.json');
  tl.scenes[0].board.push({ id: 'extra', kind: 'bullet', text: 'not part of a worked example' });
  tl.scenes[0].events = [];
  const s = spyStage(tl);
  try {
    await s.seekTo(0, 1.0);
    const ctx = s.ctx(0);
    assert.equal(ctx.view.wsFallback, true, 'board_generic built it');
    assert.ok(ctx.root.querySelector('.dyn-board'), 'board_generic\'s DOM is on the page');
    assert.ok(Array.isArray(workedSteps.dimGroups?.(ctx)));
    assert.ok(Array.isArray(workedSteps.texts?.(ctx)));
    assert.equal(workedSteps.anchor?.(ctx), null);
    assert.ok(Array.isArray(workedSteps.tex?.(tl.scenes[0], tl.scenes[0].look, 'landscape')));
  } finally {
    s.done();
  }
});

// --- the fit, on a fake text layout ---------------------------------------------------------------------------------

/**
 * Install a fake text layout on HTMLElement (jsdom has none): a glyph is 0.55 em wide, words wrap greedily, a word
 * wider than its line breaks across lines. Enough for the fit: a step's (or its clone's) height from its font size,
 * padding, justification and width, a word's height, the text box's width, the top line's height and the formula
 * card's. Returns the restore function.
 */
function fakeLayout() {
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const keys = ['offsetHeight', 'offsetWidth', 'clientWidth', 'offsetTop'];
  const saved = keys.map((k) => [k, Object.getOwnPropertyDescriptor(proto, k)]);
  const CW = 0.55;
  /** @param {Element} el */
  const fmt = (el) => (el.closest('.dyn-ws--portrait') ? 'portrait' : 'landscape');
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
    return (v > 0 ? v : 1.35) * fontSize(el);
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
    const card = /** @type {HTMLElement | null} */ (el.closest('.dyn-ws-item'));
    if (card) return cardBox(card).text;
    const head = /** @type {HTMLElement | null} */ (el.closest('.dyn-ws-heading-group'));
    if (head) return Number.parseFloat(head.style.width) || 0;
    const box = /** @type {HTMLElement | null} */ (el.closest('.dyn-ws-card'));
    if (box) {
      const L = LAYOUT[fmt(el)];
      return (Number.parseFloat(box.style.width) || 0) - 2 * L.card.padX;
    }
    return 0;
  };
  Object.defineProperty(proto, 'offsetHeight', {
    configurable: true,
    get() {
      const el = /** @type {HTMLElement} */ (this);
      if (el.classList.contains('dyn-word')) {
        const ww = (el.textContent || '').length * CW * fontSize(el);
        const box = textWidth(el);
        const host = /** @type {HTMLElement | null} */ (el.closest('.dyn-ws-text, .dyn-ws-why, .dyn-ws-heading, .dyn-ws-meaning'));
        return (box > 0 && ww > box ? Math.ceil(ww / box) : 1) * lineH(host || el);
      }
      if (el.classList.contains('dyn-ws-item')) {
        const { py, text } = cardBox(el);
        const t = /** @type {HTMLElement} */ (el.querySelector('.dyn-ws-text'));
        const why = /** @type {HTMLElement | null} */ (el.querySelector('.dyn-ws-why'));
        const whyH = why && why.style.display !== 'none'
          ? Number.parseFloat(why.style.marginTop || '0') + lines(why, text) * lineH(why) : 0;
        return 2 * py + lines(t, text) * lineH(t) + whyH;
      }
      if (el.classList.contains('dyn-ws-heading')) return lines(el, textWidth(el)) * lineH(el);
      if (el.classList.contains('dyn-ws-heading-group')) {
        const t = /** @type {HTMLElement} */ (el.querySelector('.dyn-ws-heading'));
        return lines(t, textWidth(t)) * lineH(t);
      }
      if (el.classList.contains('dyn-ws-meaning')) return lines(el, textWidth(el)) * lineH(el);
      if (el.classList.contains('dyn-ws-card')) {
        const L = LAYOUT[fmt(el)];
        const host = /** @type {HTMLElement} */ (el.querySelector('.dyn-ws-formula'));
        const meaning = /** @type {HTMLElement | null} */ (el.querySelector('.dyn-ws-meaning'));
        const tex = 1.2 * fontSize(host);
        return 2 * L.card.padY + tex + (meaning ? L.card.textGap + lines(meaning, textWidth(meaning)) * lineH(meaning) : 0);
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
 * A one-scene timeline of `fmt` whose steps are `texts` (each with a justification), beat-revealed unless
 * `beats: false`. `panel` / `panelMedia`: the scene's side panel (the demo's s11 and s12 shapes).
 * @param {'landscape' | 'portrait'} fmt @param {string[]} texts
 * @param {{beats?: boolean, heading?: string | null, formula?: boolean, panel?: any, panelMedia?: any}} [opts]
 */
function stepsTimeline(fmt, texts, opts = {}) {
  const tl = load(fmt === 'portrait' ? 'worked_steps.portrait.json' : 'worked_steps.landscape.json');
  const scene = tl.scenes[0];
  if (opts.panel) {
    scene.side_panel = { panel: opts.panel, media: opts.panelMedia || null, show_at: 0 };
    scene.layout = { ...(scene.layout || {}), show_side_panel: true };
  }
  const heading = opts.heading === undefined ? 'Find the current' : opts.heading;
  scene.board = [
    ...(heading ? [{ id: 'h1', kind: 'heading', text: heading }] : []),
    ...(opts.formula ? [{ id: 'f1', kind: 'formula', latex: 'I = V / R' }] : []),
    ...texts.map((text, i) => ({ id: `q${i}`, kind: 'example_step', text, justification: 'because' })),
  ];
  scene.beats = texts.map((_, i) => ({ beat_id: `k${i}`, index: i, phase: 'main', start: 0.5 + i * 1.5, speech_end: 1.8 + i * 1.5, end: 2 + i * 1.5, narration: 'x', board_item_id: opts.beats === false ? null : `q${i}`, captions: [] }));
  scene.events = [];
  return tl;
}

test('fit (16:9): the largest 4 px size at which every step fits under the top line; the floor reports overflow', async () => {
  const restore = fakeLayout();
  try {
    const short = spyStage(stepsTimeline('landscape', ['$I = 12 / 4$', '$I = 3$']));
    await short.seekTo(0, 0.2);
    assert.equal(short.ctx(0).view.largeSize, LAYOUT.landscape.step.max, 'two short steps fit at the top of the range');
    assert.ok(short.ctx(0).view.stepsTop > ZONES.landscape.title.y0, 'the steps start under the top line');
    short.done();
    const many = spyStage(stepsTimeline('landscape', Array(12).fill('Substitute every given number into the law')));
    await many.seekTo(0, 0.2);
    const v = many.ctx(0).view;
    assert.equal(v.largeSize, LAYOUT.landscape.step.min, 'a long derivation goes to the floor');
    assert.ok(v.steps.at(-1).overflowLarge > 0, 'what still does not fit is reported (preflight blocks: "Shorten text")');
    assert.equal(v.steps[0].overflowLarge, 0);
    const entries = /** @type {any[]} */ (workedSteps.texts?.(many.ctx(0)));
    assert.ok(entries.filter((e) => e.slot === 'step').at(-1).overflow > 0);
    many.done();
  } finally {
    restore();
  }
});

test('fit: a word wider than the step even at the floor does not cost the derivation its size', async () => {
  const restore = fakeLayout();
  try {
    const s = spyStage(stepsTimeline('landscape', ['Pneumonoultramicroscopicsilicovolcanoconiosis', '$I = 3$']));
    await s.seekTo(0, 0.2);
    assert.equal(s.ctx(0).view.largeSize, LAYOUT.landscape.step.max);
    s.done();
  } finally {
    restore();
  }
});

test('fit (9:16): the stack sizes the current step under the compact steps above it; a plain list may go compact', async () => {
  const restore = fakeLayout();
  try {
    const stack = spyStage(stepsTimeline('portrait', ['$I = 12/4$', '$I = 3$', '$P = 36$'], { formula: true }));
    await stack.seekTo(0, 0.2);
    const v = stack.ctx(0).view;
    assert.equal(v.variant, 'stack');
    assert.ok(v.largeSize >= LAYOUT.portrait.step.min && v.largeSize <= LAYOUT.portrait.step.max);
    assert.equal(v.compactSize, LAYOUT.portrait.row.max);
    assert.ok(v.steps[0].top === 0 && v.steps[1].top > 0, 'each step sits under the compact rows before it');
    assert.ok(v.steps.every((/** @type {any} */ s) => s.overflowLarge === 0 && s.overflowCompact === 0), 'it fits');
    stack.done();
    const list = spyStage(stepsTimeline('portrait', Array(7).fill('Check the unit of the answer'), { beats: false }));
    await list.seekTo(0, 0.2);
    assert.equal(list.ctx(0).view.variant, 'list');
    assert.equal(list.ctx(0).view.allCompact, true, 'a list too long for large steps goes compact, never fewer steps');
    list.done();
  } finally {
    restore();
  }
});

// --- the scene's side panel (the B2-demo side-panel slice; _shared.js) ----------------------------------------------

test('16:9 panel: the steps keep the left 55%, the panel takes the right 45% from the steps\' top down', async () => {
  const restore = fakeLayout();
  try {
    // the demo's s11 shape: a worked example whose panel illustrates it (here a figure the mode can show)
    const s = spyStage(stepsTimeline('landscape', ['Step 1: I = V / R', 'Step 2: I = 230 / 115', 'Step 3: I = 2 A'],
      { panel: { kind: 'figure', title: 'I = V / R (a straight line)' },
        panelMedia: { kind: 'image', url: '/media/plot.png', width: 1200, height: 900 } }));
    await s.seekTo(0, 1.2);
    const ctx = s.ctx(0);
    assert.equal(s.stage.el.querySelector('.dyn-board'), null, 'worked_steps drew it, not board_generic');
    assert.equal(ctx.view.variant, 'split');
    assert.ok(ctx.view.panel && ctx.view.panel.view, 'the panel shows its medium');
    const px = (/** @type {string} */ v) => Number.parseFloat(v) || 0;
    const frame = ctx.view.panel.frame.style;
    const box = { x: px(frame.left), y: px(frame.top), w: px(frame.width), h: px(frame.height) };
    assert.ok(box.x >= 1058 - 1 && box.x + box.w <= 1824 + 1, 'the panel is in the right 45%');
    assert.ok(box.y >= ctx.view.stepsTop - 1, 'it starts under the top line');
    assert.equal(Math.round((box.w / box.h) * 100), Math.round((1200 / 900) * 100), 'contained at its aspect');
    for (const step of ctx.view.steps) {
      assert.ok(px(step.el.style.left) + px(step.el.style.width) <= 1034 + 1, 'a step stays in the left 55%');
    }
    // its own dim group, whose opacity the template never writes (the stage owns it)
    assert.ok(workedSteps.dimGroups?.(ctx)?.includes(ctx.view.panel.el));
    assert.equal(ctx.view.panel.el.style.opacity, '');
    // look_here on `panel` (jsdom has no layout: rectOf is stubbed)
    const stub = { ...ctx, rectOf: (/** @type {Element} */ el) => (el === ctx.view.panel.frame ? { x: 1100.4, y: 300.6, w: 680.2, h: 510.4 } : null) };
    assert.deepEqual(workedSteps.focusRect?.(stub, 'panel'),
      { rect: { x: 1100, y: 301, w: 681, h: 510 }, shape: 'rect', radius: 24, el: ctx.view.panel.el, kind: 'shape' });
    assert.equal(workedSteps.focusRect?.(stub, 'panel:row:0'), null, 'a picture panel has no rows');
    // mediaBox for slot `panel`: the panel's whole box, so a clip is extracted at the largest size it is shown at
    const fixes = { safe: false, slots: {} };
    const want = /** @type {any} */ (boxes('landscape', 'split', ZONES.landscape, true)).visual;
    assert.deepEqual(workedSteps.mediaBox?.(ctx.scene, ZONES.landscape, 'render', 'panel', fixes),
      { w: want.x1 - want.x0, h: want.y1 - want.y0 });
    assert.equal(workedSteps.mediaBox?.(ctx.scene, ZONES.landscape, 'render', 'main', fixes), null);
    s.done();
  } finally {
    restore();
  }
});

test('16:9 terminal panel: the command and its output lines on the card surface, in the `terminal` slot', async () => {
  const restore = fakeLayout();
  try {
    // the demo's s12 shape: a "your turn" example with a terminal session beside it
    const s = spyStage(stepsTimeline('landscape', ['Step 1: I = V / R', 'Step 2: I = ______ / ______'],
      { panel: { kind: 'terminal', title: 'Check', terminal: { command: 'circuit_check --v 6 --r 3', output: 'I = 6 / 3\nI = 2.0 A' } } }));
    await s.seekTo(0, 1.2);
    const ctx = s.ctx(0);
    const term = ctx.view.panel.term;
    assert.ok(term, 'a terminal card');
    assert.equal(term.cmd.textContent, '$ circuit_check --v 6 --r 3');
    assert.equal(term.lines.length, 2);
    for (const line of term.lines) assert.ok(['visible', 'hidden'].includes(line.style.visibility), 'written every frame');
    const entry = /** @type {any} */ (workedSteps.texts?.(ctx)?.find((e) => e.slot === 'terminal'));
    assert.ok(entry && entry.critical === true);
    assert.equal(entry.bg, ctx.roles.color('surface'));
    assert.equal(entry.weight, PANEL_TERM.landscape.weight);
    assert.ok(entry.size >= PANEL_TERM.landscape.min && entry.size <= PANEL_TERM.landscape.max);
    assert.ok(entry.text.startsWith('$ circuit_check'), 'preflight gets the text it would ask to shorten');
    // a terminal asks for no medium at all
    assert.equal(workedSteps.mediaBox?.(ctx.scene, ZONES.landscape, 'render', 'panel', { safe: false, slots: {} }), null);
    // look_here on its n-th output line is a bar on that line
    const stub = { ...ctx, rectOf: (/** @type {Element} */ el) => (el === term.lines[1] ? { x: 1090, y: 420, w: 640, h: 56 } : null) };
    assert.deepEqual(workedSteps.focusRect?.(stub, 'panel:row:1'),
      { rect: { x: 1090, y: 420, w: 640, h: 56 }, shape: 'bar', radius: 0, el: ctx.view.panel.el, kind: 'shape' });
    assert.equal(workedSteps.focusRect?.(stub, 'panel:row:7'), null, 'a row the panel has not got');
    s.done();
  } finally {
    restore();
  }
});

test('16:9 panel with nothing to show: the authored title on the card surface, reported but not critical', async () => {
  const restore = fakeLayout();
  try {
    // a graph panel: the Dynamic engine draws no plot anywhere yet (function_plot is unimplemented, and
    // board_generic draws none either), so the panel's own title stands in for it
    const s = spyStage(stepsTimeline('landscape', ['Step 1: I = V / R', 'Step 2: I = 2 A'],
      { panel: { kind: 'graph', title: 'I = V / R (a straight line)', graph: { functions: [{ expr: 'x/2' }] } } }));
    await s.seekTo(0, 1.2);
    const ctx = s.ctx(0);
    assert.ok(ctx.view.panel && ctx.view.panel.label, 'a title card');
    assert.equal(ctx.view.panel.label.textContent, 'I = V / R (a straight line)');
    assert.equal(ctx.view.panel.term, null);
    const entry = /** @type {any} */ (workedSteps.texts?.(ctx)?.find((e) => e.el === ctx.view.panel.label));
    assert.ok(entry && entry.critical === false, 'a stand-in label is not critical (board_generic\'s reading)');
    assert.equal(entry.size, PANEL_LABEL_PX.landscape);
    assert.equal(entry.bg, ctx.roles.color('surface'));
    s.done();
  } finally {
    restore();
  }
});

test('a panel in 9:16, beside a formula, or of a kind nobody draws sends the whole scene to board_generic (CG-2)', async () => {
  const restore = fakeLayout();
  try {
    /** @type {[string, 'landscape' | 'portrait', any][]} */
    const cases = [
      ['a panel in 9:16', 'portrait', { panel: { kind: 'figure', title: 'P' } }],
      ['a panel beside a formula', 'landscape', { panel: { kind: 'figure', title: 'P' }, formula: true }],
      ['a skill-tree panel', 'landscape', { panel: { kind: 'skill_tree', title: 'P' } }],
      ['a graph with no title', 'landscape', { panel: { kind: 'graph', graph: { functions: [] } } }],
    ];
    for (const [name, fmt, opts] of cases) {
      const tl = stepsTimeline(fmt, ['Step 1: I = V / R', 'Step 2: I = 2 A'], opts);
      const s = spyStage(tl);
      await s.seekTo(0, 1.2);
      assert.ok(s.stage.el.querySelector('.dyn-board'), `${name}: board_generic drew it`);
      assert.equal(s.stage.el.querySelector('.dyn-ws'), null, name);
      const scene = tl.scenes[0];
      const fixes = { safe: false, slots: {} };
      // every hook is handed on, so `mediaPlan` and `ctx.media` agree with what the page draws
      assert.deepEqual(workedSteps.tex?.(scene, scene.look, fmt), boardGeneric.tex?.(scene, scene.look, fmt), name);
      assert.deepEqual(workedSteps.mediaBox?.(scene, ZONES[fmt], 'render', 'panel', fixes),
        boardGeneric.mediaBox?.(scene, ZONES[fmt], 'render', 'panel', fixes), name);
      s.done();
    }
  } finally {
    restore();
  }
});

test('the panel enters at its own show_at over PANEL_ENTER, and PANEL_DELAY after a scene that has it from the start', async () => {
  const restore = fakeLayout();
  try {
    const tl = stepsTimeline('landscape', ['Step 1: I = V / R', 'Step 2: I = 2 A'],
      { panel: { kind: 'figure', title: 'P' }, panelMedia: { kind: 'image', url: '/media/p.png', width: 800, height: 600 } });
    tl.scenes[0].side_panel.show_at = 2;
    const s = spyStage(tl);
    await s.seekTo(0, 1.9);
    const ctx = s.ctx(0);
    assert.equal(ctx.view.panel.frame.style.visibility, 'hidden', 'nothing before its show_at');
    const scale = Number(tl.presentation.motion_scale ?? 1) || 1;
    await s.seekTo(0, 2 + PANEL_ENTER * scale + 0.3);
    assert.equal(op(ctx.view.panel.frame.style.opacity), 1);
    assert.equal(ctx.view.panel.frame.style.transform, 'none', 'the rise ends at none, on whole px');
    s.done();
    // from the scene's start: PANEL_DELAY, board_generic's media frame
    const own = stepsTimeline('landscape', ['Step 1: I = V / R'],
      { panel: { kind: 'figure', title: 'P' }, panelMedia: { kind: 'image', url: '/media/p.png', width: 800, height: 600 } });
    const t0 = spyStage(own);
    await t0.seekTo(0, PANEL_DELAY / 2);
    assert.equal(t0.ctx(0).view.panel.frame.style.visibility, 'hidden');
    t0.done();
  } finally {
    restore();
  }
});

test('a panelled scene keeps the contract and paints the same frame forward, in reverse and on a fresh page', async () => {
  /** @type {any[]} */
  const panels = [
    { panel: { kind: 'figure', title: 'I = V / R (a straight line)' },
      panelMedia: { kind: 'image', url: '/media/plot.png', width: 1200, height: 900 } },
    { panel: { kind: 'terminal', title: 'Check', terminal: { command: 'circuit_check --v 6 --r 3', output: 'I = 6 / 3\nI = 2.0 A   [VERIFIED]' } } },
    { panel: { kind: 'graph', title: 'I = V / R (a straight line)', graph: { functions: [{ expr: 'x/2' }] } } },
  ];
  for (const opts of panels) {
    const tl = stepsTimeline('landscape', ['Step 1: I = V / R', 'Step 2: I = 230 / 115', 'Step 3: I = 2 A'], opts);
    // a look_here on the panel and on one of its rows, so the cue layer asks focusRect for both forms
    tl.scenes[0].events = [{ t: 1.4, kind: 'look_here', target: 'panel', dur: 1.2 },
      { t: 3.2, kind: 'look_here', target: 'panel:row:0', dur: 1 }];
    const name = String(opts.panel.kind);
    assert.deepEqual(await contractProblems(workedSteps, tl), [], name);
    const total = Math.round(tl.total_duration * tl.fps);
    const frames = new Set();
    for (let f = 0; f < total; f += 9) frames.add(f);
    for (const e of tl.scenes[0].events) {
      for (const d of [0, 0.2, 0.5]) frames.add(Math.min(total - 1, Math.round((tl.scenes[0].start + e.t + d) * tl.fps)));
    }
    assert.deepEqual(await orderProblems(tl, [...frames].sort((a, b) => a - b), SEAM), [], name);
  }
});
