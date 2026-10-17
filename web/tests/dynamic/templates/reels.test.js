// @ts-check
// The reel (9:16) adaptations of owner decision 4 (2026-10-16; docs/plans/b2/HANDOFF.md "Owner decisions"):
// (a) a reel quiz with more than two options is a two-option `check` - the correct answer and the strongest
//     distractor (check.js `strongestDistractor` / `shownOptions`);
// (b) a reel side panel is its own short full-stage beat BEFORE the steps / terms, never above them (`_shared.js`
//     `reelWindow` / `reelEntrances` / `reelPanelBox`, drawn by worked_steps.js and key_terms.js).
// The Python side (aadhi/compose/dynamic/reels.py) mirrors every number and rule: tests/compose/dynamic/test_reels.py
// pins it against _shared.js, and this file pins _shared.js against it. `drawsScene` for the reel cases is pinned
// here against REEL_FITS, the table templates.py's fits must answer once the integrator applies the step-5 requests
// (the CG-2 rule: fit > 0 must imply drawsScene; tests/compose/dynamic/test_templates.py gains the same table).
// The 16:9 output of the three templates is unchanged: a real-Chromium frame-hash comparison of their 12 fixtures
// before / after found 0 differences (recorded in the step-5 report), and their own test files still pass.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { ZONES } from '../../../js/dynamic/core.js';
import { itemTimings } from '../../../js/dynamic/state.js';
import {
  PANEL_DELAY, PANEL_KINDS, PANEL_TERM, PANEL_TITLED_KINDS, REEL_BEAT_EXIT, REEL_BEAT_MAX, REEL_BEAT_MIN, REEL_STAGGER,
  reelBeatEvent, reelEntrances, reelPanelBox, reelTimings, reelWindow,
} from '../../../js/dynamic/templates/_shared.js';
import check, {
  MAX_OPTIONS, drawnKind, drawsScene as checkDraws, shownOptions, showsLetters, strongestDistractor, texOf, timetable,
  variantOf,
} from '../../../js/dynamic/templates/check.js';
import keyTerms, { drawsScene as ktDraws, reelBeat as ktBeat } from '../../../js/dynamic/templates/key_terms.js';
import workedSteps, { drawsScene as wsDraws, reelBeat as wsBeat } from '../../../js/dynamic/templates/worked_steps.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const REELS_PY = readFileSync(new URL('../../../../aadhi/compose/dynamic/reels.py', import.meta.url), 'utf-8');
/** An opacity as a number ('' is 1). @param {string} v */
const op = (v) => (v === '' ? 1 : Number(v));

/**
 * A render stage with `template` through the seam, and the context each scene was built with.
 * @param {any} tl @param {string} id @param {any} template
 */
function spyStage(tl, id, template) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...template, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return template.build(ctx); } };
  const run = renderStage(tl, { templates: { [id]: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex });
  /** @param {number} t */
  const seekTo = async (t) => run.stage.seek(Math.round((Number(tl.scenes[0].start) + t) * tl.fps));
  return { ...run, seekTo, ctx: () => contexts.get(0) };
}

// --- the Python mirror -----------------------------------------------------------------------------------------------

test('every reel number is the one reels.py uses', () => {
  /** @param {string} name */
  const py = (name) => {
    const m = new RegExp(`^${name}: Final = ([0-9.]+)$`, 'm').exec(REELS_PY);
    assert.ok(m, `${name} in reels.py`);
    return Number(/** @type {RegExpExecArray} */ (m)[1]);
  };
  assert.equal(py('REEL_BEAT_MIN'), REEL_BEAT_MIN);
  assert.equal(py('REEL_BEAT_MAX'), REEL_BEAT_MAX);
  assert.equal(py('REEL_BEAT_EXIT'), REEL_BEAT_EXIT);
  assert.equal(py('REEL_STAGGER'), REEL_STAGGER);
  assert.equal(py('REEL_BEAT_START'), PANEL_DELAY);
  assert.equal(MAX_OPTIONS.portrait, 2, 'a reel check draws two options');
});

// --- (a) the reel check -------------------------------------------------------------------------------------------

/** @param {string[]} options @param {number} correct @param {string[]} [feedback] @param {(string | null)[]} [ids] */
const quiz = (options, correct, feedback, ids) => /** @type {any} */ ({
  quiz: {
    question: 'What happens to the current?', options, correct_index: correct, explanation: 'It doubles.',
    feedback_wrong: feedback || options.map(() => ''), countdown_start: 1, countdown_seconds: 4, reveal_start: 5,
    ...(ids ? { option_misconception_ids: ids } : {}),
  },
  duration: 8, board: [], beats: [], events: [],
});

test('strongestDistractor: a tagged misconception, else a written rationale, else the first wrong option (stored order)', () => {
  const opts = ['It stays the same', 'It doubles', 'It halves', 'It is used up'];
  assert.equal(strongestDistractor(quiz(opts, 1)), 0, 'no signal: the first wrong option');
  assert.equal(strongestDistractor(quiz(opts, 0)), 1);
  assert.equal(strongestDistractor(quiz(opts, 1, ['', '', 'The inverse trap.', 'The classic error.'])), 2, 'a rationale');
  assert.equal(strongestDistractor(quiz(opts, 1, ['  ', '', '', 'x'])), 3, 'whitespace is no rationale');
  assert.equal(strongestDistractor(quiz(opts, 1, ['a', '', 'b', 'c'], [null, null, null, 'current-used-up'])), 3,
    'a tagged misconception wins over a rationale');
  assert.equal(strongestDistractor(quiz(opts, 1, ['', 'why', '', ''], [null, 'm', null, null])), 0,
    'the correct option\'s own tag or rationale never counts');
  assert.equal(strongestDistractor(/** @type {any} */ ({ board: [] })), -1, 'not a quiz');
});

test('shownOptions: 16:9 draws every option; a 9:16 quiz of 3-5 draws the answer and the strongest distractor', () => {
  const opts = ['It stays the same', 'It doubles', 'It halves', 'It is used up'];
  const four = quiz(opts, 2, ['', 'tempting', '', '']);
  assert.deepEqual(shownOptions(four, 'landscape').map((o) => o.i), [0, 1, 2, 3], '16:9 is unchanged');
  assert.deepEqual(shownOptions(four, 'portrait'), [{ i: 1, text: 'It doubles' }, { i: 2, text: 'It halves' }],
    'stored order, stored indices (the answer is not always first)');
  assert.deepEqual(shownOptions(quiz(['a', 'b'], 1), 'portrait').map((o) => o.i), [0, 1], 'two are drawn as they are');
  assert.deepEqual(shownOptions(quiz(['a', 'b', 'c', 'd', 'e'], 4), 'portrait').map((o) => o.i), [0, 4]);
  assert.deepEqual(shownOptions(/** @type {any} */ ({ board: [{ id: 'q', kind: 'question', text: 'Q' }] }), 'portrait'), []);
  // the drawn kind, the stored variant and the letters follow what is drawn
  assert.equal(drawnKind(four, 'landscape'), 'mcq');
  assert.equal(drawnKind(four, 'portrait'), 'true_false');
  assert.equal(variantOf(four, 'portrait'), 'true_false', 'direct.variant_for must store this (step-5 request)');
  assert.ok(showsLetters(four, 'landscape') && !showsLetters(four, 'portrait'), 'two options need no A / B');
  // the timetable and the TeX are over the drawn options
  const plan = timetable(four, 'portrait');
  assert.equal(plan.options.length, 2);
  assert.ok(plan.options[1] - plan.options[0] > 0, 'staggered by drawn position');
  const texed = quiz(['$a$', '$b$', '$c$', '$d$'], 3);
  assert.deepEqual(texOf(texed, 'portrait').map((x) => x.tex), ['a', 'd']);
  assert.deepEqual(texOf(texed, 'landscape').map((x) => x.tex), ['a', 'b', 'c', 'd']);
});

test('a reel quiz of four options is drawn by check as two tiles: the answer turns done, the distractor dims', async () => {
  const tl = load('check.portrait.json');
  const s = tl.scenes[0];
  s.quiz.options = ['It stays the same', 'It doubles', 'It halves', 'It is used up faster'];
  s.quiz.correct_index = 1;
  s.quiz.feedback_wrong = ['', '', '', 'Current is never used up: it always comes home.'];
  assert.ok(checkDraws(s, 'portrait'));
  const run = spyStage(tl, 'check', check);
  await run.seekTo(Number(s.quiz.reveal_start) + 1.2);
  const ctx = run.ctx();
  const v = ctx.view;
  assert.equal(run.stage.el.querySelector('.dyn-board'), null, 'not board_generic');
  assert.deepEqual(v.options.map((/** @type {any} */ o) => [o.i, o.text]), [[1, 'It doubles'], [3, 'It is used up faster']]);
  assert.equal(v.letters, false);
  const [answer, other] = v.options;
  assert.equal(answer.correct, true);
  assert.equal(other.correct, false);
  assert.ok(op(other.card.style.opacity) < 0.5, 'the distractor dims to 40 %');
  assert.equal(answer.parts.check.style.visibility, 'visible', 'the answer takes the done check');
  // an `option:<i>` still names its stored option; a dropped option names nothing on the page
  assert.ok(check.focusRect?.(ctx, 'option:3'));
  assert.equal(check.focusRect?.(ctx, 'option:2'), null);
  const entries = /** @type {any[]} */ (check.texts?.(ctx));
  assert.deepEqual(entries.filter((e) => e.slot === 'option').map((e) => e.text), ['It doubles', 'It is used up faster']);
  run.done();
  assert.deepEqual(await contractProblems(check, tl), []);
  const frames = [];
  for (let f = 0; f < Math.round(tl.total_duration * tl.fps); f += 9) frames.push(f);
  assert.deepEqual(await orderProblems(tl, frames, { templates: { check } }), []);
});

// --- (b) the reel panel beat ----------------------------------------------------------------------------------------

/**
 * A scene for the window helpers: beats revealing items at `beats` (scene seconds), the rest unanchored.
 * @param {[string, number | null][]} items @param {number} [duration] @param {any[]} [events]
 */
const timed = (items, duration = 20, events = []) => /** @type {any} */ ({
  duration, events,
  board: items.map(([id]) => ({ id, kind: 'example_step', text: id })),
  beats: items.filter(([, at]) => at !== null).map(([id, at], k) => ({
    beat_id: `b${k}`, index: k, phase: 'main', start: at, speech_end: Number(at) + 1, end: Number(at) + 1, narration: 'x',
    board_item_id: id,
  })),
});

test('reelWindow: from PANEL_DELAY to the first anchored entrance, within REEL_BEAT_MIN / MAX (reels.py reel_window)', () => {
  const start = PANEL_DELAY;
  assert.deepEqual(reelWindow(timed([['a', 3.22], ['b', 5]])), { start, end: 3.1 }, 'the narration reveals a at 3.1');
  assert.deepEqual(reelWindow(timed([['a', 0.5]])), { start, end: start + REEL_BEAT_MIN }, 'never shorter');
  assert.deepEqual(reelWindow(timed([['h', null], ['a', 9.12]])), { start, end: start + REEL_BEAT_MAX },
    'an unanchored item waits: never longer');
  assert.deepEqual(reelWindow(timed([['a', 9.12]])), { start, end: 9 }, 'nothing waits: until the first anchored item');
  assert.deepEqual(reelWindow(timed([['h', null]])), { start, end: start + REEL_BEAT_MAX });
  assert.deepEqual(reelWindow(timed([['a', 9.12]], 5)), { start, end: 5 - REEL_BEAT_MIN }, 'the content keeps REEL_BEAT_MIN');
  // Python's planned beat wins (Python decides WHEN)
  const planned = timed([['a', 0.5]], 20, [{ t: 0.2, kind: 'reveal', target: 'panel', dur: 2.5 }]);
  assert.deepEqual(reelBeatEvent(planned), { start: 0.2, end: 2.7 });
  assert.deepEqual(reelWindow(planned), { start: 0.2, end: 2.7 });
  assert.equal(reelBeatEvent(timed([['a', 1]], 20, [{ t: 0.2, kind: 'reveal', target: 'panel' }])), null, 'no dur, no beat');
});

test('reelEntrances: what the beat holds back enters as it ends, REEL_STAGGER apart in entrance order', () => {
  const scene = timed([['h', null], ['s1', 0.5], ['s2', 4.12], ['s3', 2.32]]);
  const at = reelEntrances(scene, 2.2);
  assert.equal(at.get('h'), 2.2, 'the unanchored heading first (it enters at 0.35)');
  assert.equal(at.get('s1'), 2.2 + REEL_STAGGER, 's1 (0.38) is held back too');
  assert.ok(Math.abs(Number(at.get('s3')) - 2.2) < 1e-9 && Math.abs(Number(at.get('s2')) - 4.0) < 1e-9, 'the rest keep theirs');
  const order = reelTimings(scene, 2.2).map((x) => x.id);
  // s3's reveal (2.32 - 0.12) is the beat's end up to float noise: the same instant, so it is not held back and
  // enters with the heading (state.js keeps ties in board order only for equal times)
  assert.deepEqual(order, ['s3', 'h', 's1', 's2']);
  assert.equal(itemTimings(scene).find((x) => x.id === 'h')?.enter, 0.35, 'itemTimings itself is untouched');
});

test('reelPanelBox: a medium or a title card in the tall region, a terminal card in the text column (R-59)', () => {
  const z = ZONES.portrait;
  assert.deepEqual(reelPanelBox(null, z), { x0: 60, y0: 340, x1: 1020, y1: 1040 });
  assert.deepEqual(reelPanelBox(/** @type {any} */ ({ draw: 'terminal' }), z), { x0: 192, y0: 340, x1: 780, y1: 1020 });
  assert.ok(reelPanelBox(null, z).y1 <= z.caption.y0, 'above the caption band');
});

// The fits templates.py must answer for the reel cases once the integrator applies the step-5 requests
// (`_fit_worked_steps`, `_fit_key_terms`, `_fit_check`); drawsScene must draw every scene a fit accepts (CG-2).
const REEL_FITS = Object.freeze({
  // worked_steps: any panel kind _shared.js draws, with or without a formula; nothing else
  worked_steps: { drawn: true, formula: true, undrawn: false },
  // key_terms: any panel kind _shared.js draws, with any number (2-4) of terms
  key_terms: { drawn: true, many: true, undrawn: false },
});

test('drawsScene in 9:16: a drawable panel is its own beat (the fits of REEL_FITS accept exactly these)', () => {
  const step = { id: 's1', kind: 'example_step', text: 'one' };
  const formula = { id: 'f', kind: 'formula', latex: 'a' };
  const defs = ['a', 'b', 'c', 'd'].map((id) => ({ id, kind: 'definition', term: id, text: 'means' }));
  /** @param {any} panel */
  const sp = (panel) => ({ side_panel: { panel, media: null, show_at: 0 } });
  for (const kind of PANEL_KINDS) {
    const panel = PANEL_TITLED_KINDS.includes(kind) ? { kind, title: 'A plot' } : { kind, title: 'P', terminal: { command: 'ls' } };
    assert.equal(wsDraws(/** @type {any} */ ({ board: [step], ...sp(panel) }), 'portrait'), REEL_FITS.worked_steps.drawn, kind);
    assert.equal(wsDraws(/** @type {any} */ ({ board: [formula, step], ...sp(panel) }), 'portrait'), REEL_FITS.worked_steps.formula, kind);
    assert.equal(ktDraws(/** @type {any} */ ({ board: defs.slice(0, 2), ...sp(panel) }), 'portrait'), REEL_FITS.key_terms.drawn, kind);
    assert.equal(ktDraws(/** @type {any} */ ({ board: defs, ...sp(panel) }), 'portrait'), REEL_FITS.key_terms.many, kind);
  }
  for (const panel of [{ kind: 'skill_tree', title: 'T' }, { kind: 'quiz' }, { kind: 'graph' }]) {
    assert.equal(wsDraws(/** @type {any} */ ({ board: [step], ...sp(panel) }), 'portrait'), REEL_FITS.worked_steps.undrawn, panel.kind);
    assert.equal(ktDraws(/** @type {any} */ ({ board: defs.slice(0, 2), ...sp(panel) }), 'portrait'), REEL_FITS.key_terms.undrawn, panel.kind);
  }
  // 16:9 is unchanged: no panel beside a formula, at most two terms beside a panel
  assert.equal(wsDraws(/** @type {any} */ ({ board: [formula, step], ...sp({ kind: 'image', title: 'P' }) }), 'landscape'), false);
  assert.equal(ktDraws(/** @type {any} */ ({ board: defs.slice(0, 3), ...sp({ kind: 'image', title: 'P' }) }), 'landscape'), false);
  // the reel beat exists only in 9:16 and only with a panel
  const withPanel = /** @type {any} */ ({ board: [step], beats: [], events: [], duration: 10, ...sp({ kind: 'image', title: 'P' }) });
  assert.ok(wsBeat(withPanel, 'portrait') && !wsBeat(withPanel, 'landscape'));
  assert.equal(wsBeat(/** @type {any} */ ({ board: [step], beats: [], events: [], duration: 10 }), 'portrait'), null);
  assert.ok(ktBeat(withPanel, 'portrait') && !ktBeat(withPanel, 'landscape'));
});

/**
 * A 9:16 worked example with a side panel, timed as compose/timeline.py times it with the reel lead (the first
 * narrated step at 2.32 s: its reveal at 2.2 s, the beat's minimum end).
 * @param {any} panel @param {any} [media] @param {{formula?: boolean, planned?: boolean}} [opts]
 */
function wsReel(panel, media = null, opts = {}) {
  const tl = load('worked_steps.portrait.json');
  const scene = tl.scenes[0];
  scene.board = [
    { id: 'h1', kind: 'heading', text: 'A fan on 230 V' },
    ...(opts.formula ? [{ id: 'f1', kind: 'formula', latex: 'I = V / R' }] : []),
    { id: 's1', kind: 'example_step', text: 'Step 1: I = V / R', justification: 'we want the flow' },
    { id: 's2', kind: 'example_step', text: 'Step 2: I = 230 / 115' },
    { id: 's3', kind: 'example_step', text: 'Step 3: I = 2 A' },
  ];
  scene.beats = ['s1', 's2', 's3'].map((id, k) => ({
    beat_id: `w${k}`, index: k, phase: 'main', start: 2.32 + 2 * k, speech_end: 3.8 + 2 * k, end: 4.2 + 2 * k,
    narration: 'x', board_item_id: id, captions: [],
  }));
  scene.events = opts.planned ? [{ t: 0.2, kind: 'reveal', target: 'panel', dur: 2.0 }] : [];
  scene.side_panel = { panel, media, show_at: 0 };
  scene.layout = { ...(scene.layout || {}), show_side_panel: true };
  return tl;
}

const IMAGE = { kind: 'image', url: '/media/fan.png', width: 1200, height: 900 };

test('worked_steps 9:16: the panel holds the stage alone, leaves, then the steps enter (never above them)', async () => {
  const tl = wsReel({ kind: 'figure', title: 'The fan' }, IMAGE, { formula: true });
  const scene = tl.scenes[0];
  assert.ok(wsDraws(scene, 'portrait'));
  const beat = /** @type {{start: number, end: number}} */ (wsBeat(scene, 'portrait'));
  assert.ok(Math.abs(beat.end - 2.2) < 1e-9, 'the first narrated step\'s reveal');
  const run = spyStage(tl, 'worked_steps', workedSteps);
  await run.seekTo(1.2);
  const v = run.ctx().view;
  assert.ok(v.panel && v.panel.view, 'the figure is drawn as a medium');
  assert.equal(v.panel.frame.style.visibility, 'visible');
  assert.equal(op(v.panel.frame.style.opacity), 1);
  for (const s of v.steps) assert.equal(s.card.style.visibility, 'hidden', `${s.item.id} waits for the beat`);
  assert.equal(op(v.card.box.style.opacity), 0, 'the formula card waits too');
  assert.equal(v.head.alpha, 0, 'the heading too');
  // the panel's box: the whole tall stage, contained at its aspect
  assert.deepEqual(v.panel.box, { x0: 73, y0: 340, x1: 1006, y1: 1040 }, '933 x 700 at 4:3, centred in x 60-1020');
  await run.seekTo(beat.end + 0.05);
  assert.equal(v.panel.frame.style.visibility, 'hidden', 'gone as the content enters');
  await run.seekTo(beat.end + 0.6);
  assert.equal(v.steps[0].card.style.visibility, 'visible');
  assert.ok(v.head.alpha > 0 && op(v.card.box.style.opacity) > 0);
  // mediaBox: the reel box, the largest the picture is shown in (it only ever downscales)
  assert.deepEqual(workedSteps.mediaBox?.(scene, ZONES.portrait, 'render', 'panel', { safe: false, slots: {} }), { w: 960, h: 700 });
  run.done();
});

test('worked_steps 9:16 terminal: every output line is shown in the beat; the card text is reported in `terminal`', async () => {
  const tl = wsReel({ kind: 'terminal', terminal: { command: 'python ohm.py', output: 'I = 2.0 A\ndone' } });
  const run = spyStage(tl, 'worked_steps', workedSteps);
  await run.seekTo(1.5);
  const ctx = run.ctx();
  const v = ctx.view;
  assert.ok(v.panel && v.panel.term);
  assert.equal(v.panel.term.lines.length, 2, 'both output lines');
  for (const line of v.panel.term.lines) assert.equal(line.style.visibility, 'visible', 'before any output cue');
  const entry = /** @type {any[]} */ (workedSteps.texts?.(ctx)).find((e) => e.slot === 'terminal');
  assert.ok(entry && entry.critical && entry.weight === PANEL_TERM.portrait.weight);
  const r = /** @type {any} */ (v.panel.box);
  assert.ok(r.x0 >= 192 && r.x1 <= 780, 'the critical text column');
  run.done();
});

test('the reel beat keeps the template contract and paints every frame the same in any order', async () => {
  const cases = [
    wsReel({ kind: 'figure', title: 'The fan' }, IMAGE),
    wsReel({ kind: 'terminal', terminal: { command: 'python ohm.py', output: 'I = 2.0 A' } }, null, { formula: true }),
    wsReel({ kind: 'graph', title: 'I = V / R (a straight line)' }, null, { planned: true }),
  ];
  for (const tl of cases) {
    assert.deepEqual(await contractProblems(workedSteps, tl), []);
    const frames = [];
    for (let f = 0; f < Math.round(tl.total_duration * tl.fps); f += 7) frames.push(f);
    assert.deepEqual(await orderProblems(tl, frames, { templates: { worked_steps: workedSteps } }), []);
  }
});

/** A 9:16 pretraining scene of three terms with a side panel, timed with the reel lead. @param {any} panel @param {any} [media] */
function ktReel(panel, media = null) {
  const tl = load('key_terms.portrait.json');
  const scene = tl.scenes[0];
  scene.beats = (scene.beats || []).map((/** @type {any} */ b, k) => ({ ...b, start: 2.32 + 2 * k, speech_end: 3.8 + 2 * k, end: 4.2 + 2 * k }));
  scene.events = [];
  scene.side_panel = { panel, media, show_at: 0 };
  scene.layout = { ...(scene.layout || {}), show_side_panel: true };
  return tl;
}

test('key_terms 9:16: the panel beat comes before the heading and every row, which keep the whole column', async () => {
  const tl = ktReel({ kind: 'image', title: 'A circuit' }, IMAGE);
  const scene = tl.scenes[0];
  assert.ok(ktDraws(scene, 'portrait'));
  const run = spyStage(tl, 'key_terms', keyTerms);
  await run.seekTo(1.0);
  const v = run.ctx().view;
  assert.ok(v.panel && v.beat);
  assert.equal(v.panel.frame.style.visibility, 'visible');
  for (const c of v.cards) assert.equal(c.card.style.visibility, 'hidden', `${c.item.id} waits`);
  assert.equal(v.head.alpha, 0, 'the heading (unanchored) waits for the beat');
  assert.ok(Math.abs(Number(v.enter.get('h1')) - v.beat.end) < 1e-9, 'and enters as it ends');
  await run.seekTo(v.beat.end + 1);
  assert.equal(v.panel.frame.style.visibility, 'hidden');
  assert.equal(v.cards[0].card.style.visibility, 'visible');
  assert.ok(v.cards.every((/** @type {any} */ c) => c.el.style.left === '192px'), 'the rows take the text column, as without a panel');
  run.done();
  assert.deepEqual(await contractProblems(keyTerms, tl), []);
  const frames = [];
  for (let f = 0; f < Math.round(tl.total_duration * tl.fps); f += 7) frames.push(f);
  assert.deepEqual(await orderProblems(tl, frames, { templates: { key_terms: keyTerms } }), []);
});
