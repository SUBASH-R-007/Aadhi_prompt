// @ts-check
// templates/check.js (DYNAMIC_VIDEO_DESIGN §4.3 check, R-06 / R-07, B2 plan §1.4 template contract, §1.5 item
// states, §1.6/§1.11 slots, §P): the four fixture timelines (tests/compose/dynamic/make_check_fixtures.py) through
// the harness's contract and order gates, the pure helpers (the two data forms, the kinds, the think time, the
// timetable, geometry, slots, TeX), the painted behaviour in both formats (the question's words, the 100 ms option
// stagger, the think ring, the reveal - the correct tile's `done` state and the others dimmed to 40% - the
// explanation line, the personality's entrance and scale, reduced motion), preflight's text entries and remedies,
// and the fit, on a fake text layout (jsdom has none).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { TYPE_FLOORS, ZONES, templateSlots } from '../../../js/dynamic/core.js';
import { itemTimings } from '../../../js/dynamic/state.js';
import { scrimmedBg } from '../../../js/dynamic/templates/_shared.js';
import check, {
  CARD_RISE, DRAWN_KINDS, EXPLAIN_DELAY, LAYOUT, LINE_HEIGHT, MAX_OPTIONS, OPTION_DELAY, OPTION_DIM, OPTION_STAGGER,
  RING_MIN, SIZE_STEP, TEXT_DELAY, THINK_CAP, THINK_MIN, THINK_WPM, boxes, checkItems, drawsScene, eventFor, kindOf,
  lineHeight, optionLetter, showsLetters, slotRows, texOf, thinkSeconds, tileRows, tileTextWidth, tileWidth,
  timetable, variantOf, wordCount,
} from '../../../js/dynamic/templates/check.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['check.landscape.json', 'check.landscape.light.json', 'check.portrait.json',
  'check.portrait.hindi.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { check } };

/**
 * A render stage of `tl` with check through the stage's test seam, and the context each scene was built with.
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...check, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return check.build(ctx); } };
  const run = renderStage(tl, { templates: { check: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
  /** Seek scene `i` to the frame nearest scene time `t`. @param {number} i @param {number} t */
  const seekTo = async (i, t) => {
    const scene = tl.scenes[i];
    const f = Math.round((Number(scene.start) + t) * tl.fps);
    await run.stage.seek(f);
    return f / tl.fps - Number(scene.start);
  };
  return { ...run, contexts, seekTo, ctx: (/** @type {number} */ i) => contexts.get(i) };
}

/** An opacity as a number ('' is 1: none painted). @param {string} v */
const op = (v) => (v === '' ? 1 : Number(v));
/** A hex colour as the DOM reports a painted one (`rgb(r, g, b)`). @param {string} hex */
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;
/** A fixture's quiz scene and its board scene. @param {any} tl */
const forms = (tl) => ({
  quiz: tl.scenes.findIndex((/** @type {any} */ s) => !!s.quiz),
  board: tl.scenes.findIndex((/** @type {any} */ s) => !s.quiz),
});

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, whole-px focus, TeX, entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(check, load(name)), [], name);
});

test('every fixture paints the same frame forward, in reverse and on a fresh page (a frame is a pure function of t)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const total = Math.round(tl.total_duration * tl.fps);
    // every 11th frame plus the frames around each event, each beat, each quiz mark and each crossfade's start
    const frames = new Set();
    for (let f = 0; f < total; f += 11) frames.add(f);
    /** @param {number} at */
    const mark = (at) => frames.add(Math.min(total - 1, Math.max(0, Math.round(at * tl.fps))));
    for (const s of tl.scenes) {
      for (const e of s.events || []) for (const d of [0, 0.2, 0.5]) mark(s.start + e.t + d);
      for (const b of s.beats || []) mark(s.start + b.start);
      if (s.quiz) {
        for (const at of [s.quiz.countdown_start, s.quiz.reveal_start, s.quiz.reveal_start + EXPLAIN_DELAY]) mark(s.start + at);
      }
      mark(s.start + 0.1);
    }
    assert.deepEqual(await orderProblems(tl, [...frames].sort((a, b) => a - b), SEAM), [], name);
  }
});

test('the fixtures cover both formats, dark and light families, every text case, both data forms and every event kind', () => {
  const seen = { formats: new Set(), modes: new Set(), entrances: new Set(), kinds: new Set(), scripts: new Set(), shapes: new Set(), letters: new Set() };
  for (const name of FIXTURES) {
    const tl = load(name);
    seen.formats.add(tl.presentation.format);
    seen.modes.add(tl.presentation.mode);
    seen.entrances.add(tl.presentation.text_entrance);
    for (const s of tl.scenes) {
      for (const e of s.events || []) seen.kinds.add(e.kind);
      seen.shapes.add(s.quiz ? 'quiz' : 'board');
      seen.letters.add(showsLetters(s) ? 'letters' : 'none');
      const data = checkItems(s);
      const text = [data.question, data.explanation, ...data.options, data.label ? data.label.text : ''].join(' ');
      if (/\p{Script=Tamil}/u.test(text)) seen.scripts.add('tamil');
      if (/\p{Script=Devanagari}/u.test(text)) seen.scripts.add('devanagari');
      if (/\$[^$]+\$/.test(text)) seen.scripts.add('tex');
      if (/[A-Za-z]{40,}/.test(text)) seen.scripts.add('long-word');
      if (/(?:\b[A-Z]{3,}\b.*){3}/.test(text)) seen.scripts.add('wide-caps');
    }
  }
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.entrances].sort(), ['baseline_reveal', 'fade_rise']);
  assert.deepEqual([...seen.kinds].sort(), ['answer', 'countdown', 'reveal']);
  assert.deepEqual([...seen.scripts].sort(), ['devanagari', 'long-word', 'tamil', 'tex', 'wide-caps']);
  assert.deepEqual([...seen.shapes].sort(), ['board', 'quiz'], 'a quiz scene and a board question');
  assert.deepEqual([...seen.letters].sort(), ['letters', 'none'], 'an mcq with letters and a true / false pair without');
});

// --- pure helpers ---------------------------------------------------------------------------------------------------

test('checkItems: a quiz gives the question, options, correct index and explanation; a board question gives its justification', () => {
  const quiz = /** @type {any} */ ({
    title: 'Quick check',
    quiz: { question: 'Why?', options: ['A', ' B ', ''], correct_index: 1, explanation: 'Because.' },
  });
  const q = checkItems(quiz);
  assert.deepEqual(q.options, ['A', 'B'], 'an empty option is not drawn');
  assert.equal(q.question, 'Why?');
  assert.equal(q.correct, 1);
  assert.equal(q.explanation, 'Because.');
  assert.equal(q.label?.text, 'Quick check', 'a quiz scene is labelled by its title');
  assert.equal(q.item, null);

  const board = /** @type {any} */ ({
    title: 'ignored',
    board: [{ id: 'h', kind: 'heading', text: 'Your turn' }, { id: 'q1', kind: 'question', text: 'Name it.', justification: 'The current.', blank: true }],
  });
  const b = checkItems(board);
  assert.equal(b.label?.text, 'Your turn', 'a heading item is the label, not the scene title');
  assert.equal(b.label?.item?.id, 'h');
  assert.equal(b.item?.id, 'q1');
  assert.equal(b.question, 'Name it.');
  assert.deepEqual(b.options, []);
  assert.equal(b.correct, -1);
  assert.equal(b.explanation, 'The current.', 'the answer is the explanation line');
  assert.equal(checkItems(/** @type {any} */ ({ board: [] })).label, null, 'no heading and no quiz: no label');
});

test('kindOf / variantOf / showsLetters: the kind comes from the data, and it is direct.variant_for\'s own rule', () => {
  /** @param {number} n */
  const withOptions = (n) => /** @type {any} */ ({ quiz: { question: 'Q', options: Array.from({ length: n }, (_, i) => `o${i}`), correct_index: 0, explanation: 'x' } });
  const recall = /** @type {any} */ ({ board: [{ id: 'q1', kind: 'question', text: 'Q', justification: 'A', blank: true }] });
  assert.equal(kindOf(recall), 'free_recall');
  assert.equal(kindOf(withOptions(2)), 'true_false');
  assert.equal(kindOf(withOptions(3)), 'mcq');
  assert.equal(kindOf(withOptions(4)), 'mcq');
  assert.equal(variantOf(withOptions(4), 'landscape'), 'mcq');
  // owner decision 4: a reel mcq is drawn as a two-option check (its correct answer and strongest distractor), whose
  // variant is the two-option `true_false` (9:16 has no mcq variant, §4.3; reels.test.js pins the reduction)
  assert.equal(variantOf(withOptions(4), 'portrait'), 'true_false');
  assert.equal(variantOf(withOptions(2), 'portrait'), 'true_false');
  assert.equal(variantOf(recall, 'landscape'), 'free_recall');
  assert.ok(showsLetters(withOptions(3)) && showsLetters(withOptions(4)));
  assert.ok(!showsLetters(withOptions(2)) && !showsLetters(recall), 'a True / False pair needs no A / B');
  assert.deepEqual([0, 1, 2, 3].map(optionLetter), ['A', 'B', 'C', 'D']);
  // the variant every fixture scene would be stored with is the one the page draws for (the slot table has one
  // `default` variant, so no stored variant can disagree with the box text is laid out in)
  for (const name of FIXTURES) {
    const tl = load(name);
    for (const s of tl.scenes) assert.equal(typeof variantOf(s, tl.presentation.format), 'string', name);
  }
});

test('drawsScene: a quiz within the format\'s option cap, or one blank question; anything else is board_generic\'s', () => {
  /** @param {number} n */
  const quiz = (n) => /** @type {any} */ ({ quiz: { question: 'Q', options: Array.from({ length: n }, (_, i) => `o${i}`), correct_index: 0, explanation: 'x' } });
  const question = { id: 'q1', kind: 'question', text: 'Name it.', justification: 'The current.', blank: true };
  assert.ok(drawsScene(quiz(4), 'landscape'));
  assert.ok(drawsScene(quiz(2), 'portrait'));
  // owner decision 4: 9:16 draws two options, and reduces more to the answer and the strongest distractor
  assert.ok(drawsScene(quiz(4), 'portrait') && drawsScene(quiz(5), 'portrait'), 'a reel mcq is a two-option check');
  assert.ok(!drawsScene(quiz(5), 'landscape'), 'the schema allows 5 options; §4.3 draws at most 4');
  assert.deepEqual({ ...MAX_OPTIONS }, { landscape: 4, portrait: 2 }, 'templates.py CHECK_MAX_OPTIONS');
  assert.ok(!drawsScene(/** @type {any} */ ({ quiz: { question: '', options: ['a', 'b'], correct_index: 0, explanation: '' } }), 'landscape'), 'no question');
  assert.ok(!drawsScene(/** @type {any} */ ({ quiz: { question: 'Q', options: ['a', 'b'], correct_index: 7, explanation: '' } }), 'landscape'), 'the answer is not among the options');
  assert.ok(drawsScene(/** @type {any} */ ({ board: [question] }), 'landscape'));
  assert.ok(drawsScene(/** @type {any} */ ({ board: [{ id: 'h', kind: 'heading', text: 'H' }, question] }), 'portrait'));
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [question, { id: 'q2', kind: 'question', text: 'And?' }] }), 'landscape'), 'two questions');
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [question, { id: 'h1', kind: 'heading', text: 'A' }, { id: 'h2', kind: 'heading', text: 'B' }] }), 'landscape'), 'two headings');
  assert.ok(!drawsScene(/** @type {any} */ ({ board: [] }), 'landscape'), 'nothing to ask');
  for (const kind of ['bullet', 'definition', 'formula', 'code', 'table', 'figure', 'stat', 'misconception', 'takeaway']) {
    assert.ok(!drawsScene(/** @type {any} */ ({ board: [question, { id: 'x', kind, text: 'x' }] }), 'landscape'), kind);
  }
  assert.deepEqual([...DRAWN_KINDS].sort(), ['heading', 'question']);
  // a quiz scene carrying a board item would lose it here, and §4.3 gives a check no picture (review D5)
  assert.ok(!drawsScene(/** @type {any} */ ({ ...quiz(2), board: [question] }), 'landscape'), 'a quiz with a board item');
  const panel = { kind: 'image', title: 'A lamp' };
  assert.ok(!drawsScene(/** @type {any} */ ({ ...quiz(2), side_panel: { panel } }), 'landscape'), 'a shown side panel');
  assert.ok(drawsScene(/** @type {any} */ ({ ...quiz(2), side_panel: { panel }, layout: { show_side_panel: false } }), 'landscape'),
    'a hidden one is not shown, so it is still this template\'s');
  assert.ok(!drawsScene(/** @type {any} */ ({ ...quiz(2), media: { asset_key: 'a' } }), 'landscape'), 'a scene medium');
  assert.ok(!drawsScene(/** @type {any} */ ({ ...quiz(2), poster: { asset_key: 'a' } }), 'landscape'), 'a poster');
});

test('a scene this template does not draw is drawn by board_generic whole, every hook handed on', async () => {
  // a five-option lesson quiz in 16:9 (the schema allows 5): more options than §4.3's mcq takes, so board_generic's
  // stack draws it (nothing is dropped, plan §1.4 invariant 2). (A 9:16 quiz of any size is the reel's two-option
  // check since owner decision 4: reels.test.js.)
  const tl = load('check.landscape.json');
  const i = forms(tl).quiz;
  tl.scenes[i].quiz.options = ['It stays the same', 'It doubles', 'It halves', 'It is used up faster', 'It reverses'];
  tl.scenes[i].quiz.feedback_wrong = ['', '', '', '', ''];
  assert.ok(!drawsScene(tl.scenes[i], 'landscape'));
  const run = renderStage(tl, { templates: { check }, renderTex: recordingTex().renderTex });
  await run.stage.seek(Math.round(tl.scenes[i].start * tl.fps) + 30);
  assert.ok(run.stage.el.querySelector('.dyn-board'), 'board_generic drew it');
  assert.equal(run.stage.el.querySelector('.dyn-ck'), null);
  assert.deepEqual(check.tex?.(tl.scenes[i], tl.scenes[i].look, 'landscape'),
    boardGeneric.tex?.(tl.scenes[i], tl.scenes[i].look, 'landscape'));
  assert.deepEqual(check.mediaBox?.(tl.scenes[i], ZONES.landscape, 'render', 'main', { safe: false, slots: {} }),
    boardGeneric.mediaBox?.(tl.scenes[i], ZONES.landscape, 'render', 'main', { safe: false, slots: {} }));
  run.done();
});

test('thinkSeconds: §1.4\'s formula, never below R-07\'s minimum, capped at 12 s', () => {
  assert.equal(THINK_WPM, 130);
  assert.deepEqual({ ...THINK_MIN }, { landscape: 5, portrait: 4 });
  assert.equal(thinkSeconds(0, 'landscape'), 5, 'R-07: >= 5 s in 16:9');
  assert.equal(thinkSeconds(0, 'portrait'), 4, 'R-07: >= 4 s in 9:16');
  assert.equal(thinkSeconds(26, 'landscape'), THINK_CAP, '26 words / 130 wpm = 12 s + 3 s, capped at 12');
  assert.equal(thinkSeconds(13, 'portrait'), 9, '13 words / 130 wpm = 6 s + 3 s');
  assert.equal(thinkSeconds(1000, 'landscape'), THINK_CAP);
  assert.equal(wordCount('**Two** words'), 2, 'markup is not a word');
  assert.equal(wordCount('[[A term]] and a word'), 5);
  assert.equal(wordCount('   '), 0);
});

test('eventFor: the first event of a kind on an item, with its duration', () => {
  const scene = /** @type {any} */ ({ events: [
    { t: 3, kind: 'countdown', target: 'q1', dur: 2 },
    { t: 1, kind: 'countdown', target: 'q1' },
    { t: 2, kind: 'answer', target: 'q1:term:0' },
    { t: 1, kind: 'countdown', target: 'other' },
  ] });
  assert.deepEqual(eventFor(scene, 'countdown', 'q1'), { t: 1, dur: null });
  assert.deepEqual(eventFor(scene, 'answer', 'q1'), { t: 2, dur: null });
  assert.equal(eventFor(scene, 'fill', 'q1'), null);
  assert.equal(eventFor(/** @type {any} */ ({}), 'answer', 'q1'), null);
});

test('timetable (quiz): the narration\'s own clock - the countdown, the reveal, and the options 100 ms apart', () => {
  const tl = load('check.landscape.json');
  const scene = tl.scenes[forms(tl).quiz];
  const plan = timetable(scene, 'landscape');
  assert.equal(plan.enter, 0, 'a quiz starts with its scene');
  assert.deepEqual(plan.options.map((/** @type {number} */ t) => Number(t.toFixed(3))),
    scene.quiz.options.map((/** @type {unknown} */ _o, /** @type {number} */ i) => Number((OPTION_DELAY + OPTION_STAGGER * i).toFixed(3))));
  assert.equal(plan.ring?.start, scene.quiz.countdown_start);
  assert.equal(plan.ring?.seconds, scene.quiz.countdown_seconds);
  assert.equal(plan.revealAt, scene.quiz.reveal_start);
  assert.equal(plan.explainAt, plan.revealAt);
  // the ring never runs past the reveal (R-07: its time is over when the answer appears)
  const early = { ...scene, quiz: { ...scene.quiz, reveal_start: scene.quiz.countdown_start + 1 } };
  assert.equal(timetable(/** @type {any} */ (early), 'landscape').ring?.seconds, 1);
  // nothing to think about: no ring at all
  const none = { ...scene, quiz: { ...scene.quiz, countdown_seconds: 0, reveal_start: scene.quiz.countdown_start } };
  assert.equal(timetable(/** @type {any} */ (none), 'landscape').ring, null);
  assert.ok(RING_MIN > 0);
});

test('timetable (quiz): an event overrides the quiz\'s own clock - the reveal, the ring and an option\'s entrance', () => {
  const tl = load('check.landscape.light.json'); // the one quiz fixture with events of its own
  const scene = tl.scenes[forms(tl).quiz];
  const plan = timetable(scene, 'landscape');
  const answer = /** @type {any} */ (scene.events.find((/** @type {any} */ e) => e.kind === 'answer'));
  const reveal = /** @type {any} */ (scene.events.find((/** @type {any} */ e) => e.kind === 'reveal'));
  assert.equal(answer.target, 'option:0', 'the correct option names the answer');
  assert.ok(answer.t < scene.quiz.reveal_start, 'and it comes before the quiz\'s own mark');
  assert.equal(plan.revealAt, answer.t, 'the event wins');
  assert.ok(Math.abs(/** @type {number} */ (plan.ring?.seconds) - (answer.t - scene.quiz.countdown_start)) < 1e-6,
    'so the ring is cut off there');
  assert.equal(plan.options[1], reveal.t, 'a reveal on option:1 moves it off the stagger');
  assert.equal(plan.options[0], plan.enter + OPTION_DELAY, 'the other keeps it');
  // a countdown event of any target times the ring (a quiz question has no item id to name)
  const timed = { ...scene, events: [{ t: 2.0, kind: 'countdown', target: 'option:0', dur: 1.0, anchored: true }] };
  const ring = timetable(/** @type {any} */ (timed), 'landscape').ring;
  assert.deepEqual([ring?.start, ring?.seconds], [2.0, 1.0]);
});

test('timetable (board): a countdown event times the ring, an answer event the reveal, else the fill and §1.4', () => {
  const tl = load('check.landscape.json');
  const scene = tl.scenes[forms(tl).board];
  const plan = timetable(scene, 'landscape');
  const enter = /** @type {number} */ (itemTimings(scene).find((/** @type {any} */ x) => x.id === 'q1')?.enter);
  assert.equal(plan.enter, enter);
  assert.deepEqual(plan.options, []);
  assert.equal(plan.ring?.start, 1.5, 'the countdown event');
  assert.equal(plan.ring?.seconds, 1.5, 'its own duration');
  assert.equal(plan.revealAt, 3.0, 'the answer event');
  // without events: §1.4's think time from the words on screen, and the beat that fills the item reveals it
  const bare = { ...scene, events: [] };
  const plain = timetable(/** @type {any} */ (bare), 'landscape');
  const fill = /** @type {number} */ (itemTimings(/** @type {any} */ (bare)).find((/** @type {any} */ x) => x.id === 'q1')?.fill);
  assert.equal(plain.revealAt, fill);
  assert.ok(plain.ring && plain.ring.seconds > 0 && plain.ring.start + plain.ring.seconds <= fill + 1e-9,
    'the ring stops at the reveal');
  // nothing to reveal (no justification): no reveal and no explanation
  const open = { ...scene, events: [], board: [{ ...scene.board[0], justification: '' }] };
  assert.equal(timetable(/** @type {any} */ (open), 'landscape').revealAt, null);
});

test('boxes: the band holds the label and the ring, and the block is what is left of the content box', () => {
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const z = ZONES[fmt];
    const L = LAYOUT[fmt];
    const b = boxes(fmt, z);
    assert.equal(b.content.y0, z.title.y0);
    assert.equal(b.content.y1, z.body.y1);
    assert.ok(b.content.y1 < z.caption.y0, `${fmt}: clear of the caption band`);
    const col = fmt === 'portrait' ? z.textColumn : z.body;
    assert.deepEqual([b.content.x0, b.content.x1], [col.x0, col.x1]);
    assert.equal(b.ring.x1, b.content.x1);
    assert.equal(b.ring.x1 - b.ring.x0, L.ring);
    assert.equal(b.ring.y1 - b.ring.y0, L.ring);
    assert.equal(b.label.x1, b.ring.x0 - L.gap, `${fmt}: the label never runs under the ring`);
    assert.ok(b.block.y0 > b.ring.y1 - 1 && b.block.y1 === b.content.y1);
    for (const v of [b.label.x0, b.label.y0, b.block.y0, b.ring.x0, b.ring.y0]) {
      assert.ok(Number.isInteger(v), `${fmt}: whole px`);
    }
  }
  // 9:16: x 192-780, the span every critical band holds whatever the row's y (R-59, preflight CRITICAL_BANDS)
  const p = boxes('portrait', ZONES.portrait);
  for (const band of ZONES.portrait.critical) {
    if (p.block.y1 <= band.y0 || p.block.y0 >= band.y1) continue;
    assert.ok(p.block.x0 >= band.x0 && p.block.x1 <= band.x1, 'the block stays inside every band it reaches');
  }
  assert.deepEqual(tileRows(4, 2), [[0, 1], [2, 3]]);
  assert.deepEqual(tileRows(3, 2), [[0, 1], [2]], 'the last row is centred by layout');
  assert.deepEqual(tileRows(2, 1), [[0], [1]], '9:16 stacks them');
  assert.deepEqual(tileRows(0, 2), []);
  assert.equal(tileWidth('landscape', ZONES.landscape), LAYOUT.landscape.tileW);
  assert.equal(tileWidth('portrait', ZONES.portrait), ZONES.portrait.textColumn.x1 - ZONES.portrait.textColumn.x0);
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    // the letter column and the check's column are kept whether or not either is drawn (plan §1.11)
    assert.equal(tileTextWidth(fmt, ZONES[fmt]), tileWidth(fmt, ZONES[fmt]) - 2 * L.pad - 2 * (L.badge + L.gap));
  }
});

test('lineHeight: the type scale\'s Latin values and the Indic minimums (plan §2)', () => {
  assert.equal(lineHeight('body', 'A closed loop'), LINE_HEIGHT.body.latin);
  assert.equal(lineHeight('body', 'மின்கலம் pushes $V$'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('heading', 'विद्युत परिपथ'), LINE_HEIGHT.heading.indic);
  assert.equal(lineHeight('label', 'Quick check'), LINE_HEIGHT.label.latin);
  const scale = CATALOGUE.type.scale;
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.body.indic, scale.body.line_height.indic[0], 'plan §2: Indic body >= 1.45');
  assert.equal(LINE_HEIGHT.heading.latin, scale.heading.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.heading.indic, scale.heading.line_height.indic[0]);
});

test('sizes keep R-29: labels at the label floor, text above the text floor, 9:16 text at least weight 600', () => {
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    const floors = TYPE_FLOORS[fmt];
    assert.equal(L.label.min, floors.label_min, `${fmt} label floor`);
    assert.equal(L.ringNum, floors.label_min, `${fmt} ring number is a label`);
    for (const [slot, s] of /** @type {const} */ ([['question', L.question], ['option', L.option], ['explain', L.explain]])) {
      assert.ok(s.min >= floors.text_min, `${fmt} ${slot} >= the text floor`);
      if (floors.heavy_max_px && s.max <= floors.heavy_max_px) {
        assert.ok(s.weight >= /** @type {number} */ (floors.heavy_min_weight), `${fmt} ${slot}: R-29's weight clause`);
      }
    }
    assert.ok(L.question.max > L.option.max, `${fmt}: the question is the biggest text`);
  }
  // deviation 31's family: a check question is a whole sentence, so 16:9 sits below R-29's nominal heading band
  // (84-120) and above its floor - a B2-demo proposal, pending the owner's review
  assert.ok(LAYOUT.landscape.question.max < TYPE_FLOORS.landscape.heading[0]);
  assert.ok(LAYOUT.landscape.question.min > TYPE_FLOORS.landscape.text_min);
});

test('slotRows mirrors LAYOUT and the boxes the page lays text out in, with one `default` variant per format', () => {
  const rows = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  assert.deepEqual(Object.keys(rows).sort(), ['explain', 'label', 'option', 'question', 'ring']);
  for (const slot of Object.keys(rows)) {
    assert.deepEqual(Object.keys(rows[slot]).sort(), ['landscape', 'portrait'], slot);
    for (const fmt of ['landscape', 'portrait']) {
      assert.deepEqual(Object.keys(rows[slot][fmt]), ['default'], `${slot} ${fmt}: no per-variant width (review D7)`);
    }
  }
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    const b = boxes(fmt, ZONES[fmt]);
    assert.equal(rows.question[fmt].default.w, b.block.x1 - b.block.x0);
    assert.equal(rows.explain[fmt].default.w, b.block.x1 - b.block.x0);
    assert.equal(rows.label[fmt].default.w, b.label.x1 - b.label.x0);
    assert.equal(rows.option[fmt].default.w, tileTextWidth(fmt, ZONES[fmt]));
    assert.equal(rows.ring[fmt].default.w, L.ring);
    assert.deepEqual(rows.question[fmt].default.size, [L.question.min, L.question.max]);
    assert.equal(rows.question[fmt].default.max_lines, L.question.lines);
    assert.equal(rows.question[fmt].default.prefer_lines, L.question.prefer);
    assert.equal(rows.question[fmt].default.weight, L.question.weight);
  }
  assert.equal(rows.question.landscape.default.face, 'display');
  assert.equal(rows.option.landscape.default.face, 'body');
  assert.equal(rows.ring.landscape.default.critical, false, 'R-07: the think time is the ring itself');
  assert.equal(rows.ring.landscape.default.tabular_nums, true);
});

test('the catalogue\'s check slots (templates.py) are slotRows exactly, and its consumes names the forms it draws', () => {
  // registered (B2c review CG-4: never skipped, so a missing slot table or consumes change fails here)
  assert.equal(/** @type {any} */ (CATALOGUE.templates).check.implemented, true,
    'templates.py: check needs implemented=True and slots=_check_slots()');
  const cat = /** @type {any} */ (templateSlots('check'));
  const want = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  assert.ok(cat, 'templates.py: check has no slot table');
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
  // the kinds and forms the page reads: a reveal moves an option off the stagger, a countdown times the ring and an
  // answer the reveal, each by item (the board form) or by `option:<i>` / by kind (a quiz, whose question has no item
  // id). §4.3 gives a check no term cue and no look_here: R-16's hold is answered by the ring itself
  assert.deepEqual(/** @type {any} */ (CATALOGUE.templates).check.consumes.forms,
    { reveal: ['item', 'option'], countdown: ['item', 'option'], answer: ['item', 'option'] });
  assert.deepEqual(/** @type {any} */ (CATALOGUE.templates).check.variants.landscape,
    ['mcq', 'free_recall', 'true_false', 'apply']);
  assert.deepEqual(/** @type {any} */ (CATALOGUE.templates).check.variants.portrait, ['free_recall', 'true_false']);
  assert.deepEqual(/** @type {any} */ (CATALOGUE.templates).check.forced_for, ['quiz_checkpoint']);
});

test('mediaBox: this template shows no picture or clip', () => {
  const scene = /** @type {any} */ ({ board: [{ id: 'q1', kind: 'question', text: 'Why?', justification: 'Because.', blank: true }] });
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    for (const slot of ['main', 'panel', undefined]) {
      assert.equal(check.mediaBox?.(scene, ZONES[fmt], 'render', slot, { safe: false, slots: {} }), null);
    }
  }
});

test('texOf declares every TeX the template mounts, inline, in build order', () => {
  const tl = load('check.landscape.json');
  const scene = tl.scenes[forms(tl).quiz];
  const declared = texOf(scene);
  assert.ok(declared.length > 0, 'the fixture question or explanation holds $TeX$');
  assert.ok(declared.every((/** @type {any} */ x) => x.display === false), 'a check mounts TeX inline only');
  assert.deepEqual(texOf(/** @type {any} */ ({ board: [] })), []);
});

// --- painted behaviour ----------------------------------------------------------------------------------------------

test('the question\'s words enter 80 ms after the scene, in the personality\'s time', async () => {
  const tl = load('check.portrait.json'); // Soft: fade_rise, x1.15
  const scale = tl.presentation.motion_scale;
  const i = forms(tl).quiz;
  const s = spyStage(tl);
  await s.seekTo(i, TEXT_DELAY * scale);
  const ctx = s.ctx(i);
  assert.ok(op(ctx.view.question.el.style.opacity) < 0.2, 'not yet in');
  await s.seekTo(i, (TEXT_DELAY + 0.45) * scale + 0.1);
  assert.equal(op(ctx.view.question.el.style.opacity), 1, 'in after its entrance');
  assert.equal(ctx.view.question.el.style.transform, 'none');
  s.done();
});

test('the options appear with a 100 ms stagger, each rising 24 px as it fades in', async () => {
  const tl = load('check.landscape.json'); // Crisp: baseline_reveal, x0.85
  const scale = tl.presentation.motion_scale;
  const i = forms(tl).quiz;
  const s = spyStage(tl);
  const ctx = () => s.ctx(i);
  // at the first option's entrance the later ones are still out
  await s.seekTo(i, OPTION_DELAY + 0.02);
  const first = ctx().view.options;
  assert.ok(op(first[0].card.style.opacity) > 0, 'the first option has started');
  assert.equal(op(first[3].card.style.opacity), 0, 'the fourth has not');
  await s.seekTo(i, OPTION_DELAY + OPTION_STAGGER * 3 + 0.02);
  assert.ok(op(ctx().view.options[3].card.style.opacity) > 0, 'the fourth starts 300 ms after the first');
  // the rise is whole pixels, and it is over once the entrance is
  await s.seekTo(i, OPTION_DELAY + 0.2 * scale);
  const mid = ctx().view.options[0].card.style.transform;
  const dy = /translate\(0, (\d+)px\)/.exec(mid);
  assert.ok(dy && Number(dy[1]) > 0 && Number(dy[1]) <= CARD_RISE, mid);
  await s.seekTo(i, OPTION_DELAY + 0.4 * scale + 0.1);
  assert.equal(ctx().view.options[0].card.style.transform, 'none');
  assert.equal(op(ctx().view.options[0].card.style.opacity), 1);
  s.done();
});

test('on reveal the correct tile takes the `done` state and the others dim to 40%, and the explanation follows', async () => {
  const tl = load('check.landscape.json');
  const i = forms(tl).quiz;
  const { reveal_start: at, correct_index: correct } = tl.scenes[i].quiz;
  const s = spyStage(tl);
  await s.seekTo(i, at - 0.2);
  let v = s.ctx(i).view;
  assert.equal(op(v.options[correct].card.style.opacity), 1);
  assert.equal(op(v.options[0].card.style.opacity), 1, 'nothing is dimmed before the reveal');
  assert.equal(op(v.explain.inner.style.opacity), 0, 'the explanation waits');
  await s.seekTo(i, at + 0.5);
  v = s.ctx(i).view;
  const ok = s.ctx(i).roles.color('ok');
  assert.equal(op(v.options[correct].card.style.opacity), 1, 'the answer stays at full contrast');
  assert.ok(Math.abs(op(v.options[(correct + 1) % v.options.length].card.style.opacity) - OPTION_DIM) < 1e-6,
    '§4.3: the others dim to 40% - and stay on screen');
  assert.ok(v.options[correct].card.style.getPropertyValue('--dyn-state-border').startsWith(ok),
    'the ok border draws round the correct tile');
  assert.equal(v.options[correct].parts.check.style.visibility, 'visible', 'the ok check pops');
  assert.equal(v.options[0].parts.check.style.visibility, 'hidden');
  assert.equal(v.options[correct].parts.check.style.color, rgb(ok));
  await s.seekTo(i, at + EXPLAIN_DELAY + 0.5);
  v = s.ctx(i).view;
  assert.equal(op(v.explain.inner.style.opacity), 1, 'then the explanation line');
  assert.equal(v.explain.inner.style.transform, 'none');
  s.done();
});

test('the explanation is never left unshown: a scene that ends soon after the reveal shows it at the reveal', async () => {
  const tl = load('check.landscape.json');
  const i = forms(tl).quiz;
  const scene = tl.scenes[i];
  // the scene ends 300 ms after the reveal: later than that and the line would never be up
  scene.duration = Number((scene.quiz.reveal_start + 0.3).toFixed(3));
  const s = spyStage(tl);
  await s.seekTo(i, scene.duration - 1 / 30);
  // its entrance ends with the scene, so the last captured frame has it all but in (never at 0, which is what an
  // unclamped delay would have left)
  assert.ok(op(s.ctx(i).view.explain.inner.style.opacity) > 0.9, s.ctx(i).view.explain.inner.style.opacity);
  s.done();
});

test('free recall: the answer is the explanation line rising into place, with no item state of its own', async () => {
  const tl = load('check.landscape.json');
  const i = forms(tl).board;
  const s = spyStage(tl);
  await s.seekTo(i, 3.0 + EXPLAIN_DELAY - 0.1);
  assert.equal(op(s.ctx(i).view.explain.inner.style.opacity), 0, 'it waits for the reveal');
  await s.seekTo(i, 3.0 + EXPLAIN_DELAY + 0.5);
  const v = s.ctx(i).view;
  assert.deepEqual(v.options, [], 'no options to mark');
  assert.equal(op(v.explain.inner.style.opacity), 1);
  assert.equal(v.explain.inner.style.transform, 'none', 'the rise is over');
  // §4.3 gives the `done` state to the correct tile; a line on the background takes none (the ok border read as a
  // text field and its check sat over the last word)
  assert.equal(v.explain.parts, undefined);
  assert.equal(v.explain.el.style.getPropertyValue('--dyn-state-border'), '');
  assert.equal(v.explain.el.querySelector('.dyn-state-check'), null);
  s.done();
});

test('the label is the quiz scene\'s title, or the heading item - which enters at its own beat', async () => {
  const tl = load('check.landscape.json');
  const { quiz, board } = forms(tl);
  const s = spyStage(tl);
  await s.seekTo(quiz, 1.0);
  assert.equal(s.ctx(quiz).view.head.text, tl.scenes[quiz].title, 'a quiz is labelled by its title');
  assert.equal(op(s.ctx(quiz).view.head.el.style.opacity), 1);
  const entry = /** @type {any[]} */ (check.texts?.(s.ctx(quiz))).find((e) => e.slot === 'label');
  assert.equal(entry.ink, s.ctx(quiz).onBackground.ink2, 'on the background, in the quieter ink');
  assert.equal(entry.role, 'label');
  // a board check with no heading has no label of its own (its scene title is structure, not content)
  await s.seekTo(board, 1.0);
  assert.equal(s.ctx(board).view.head, null);
  s.done();

  // the heading item's own entrance times the label (the Tamil fixture carries one)
  const ta = load('check.landscape.light.json');
  const i = forms(ta).board;
  const h = spyStage(ta);
  const at = /** @type {number} */ (itemTimings(ta.scenes[i]).find((/** @type {any} */ x) => x.id === 'h1')?.enter);
  assert.equal(timetable(ta.scenes[i], 'landscape').label, at);
  await h.seekTo(i, at - 0.2);
  assert.ok(op(h.ctx(i).view.head.el.style.opacity) < 0.2, 'not before its beat');
  await h.seekTo(i, at + 0.6);
  assert.equal(op(h.ctx(i).view.head.el.style.opacity), 1);
  assert.equal(h.ctx(i).view.head.text, 'MAXIMUM WATTAGE MEETS LOAD');
  h.done();
});

test('the think ring runs linearly from its mark, counting whole seconds down, and leaves at the reveal', async () => {
  const tl = load('check.landscape.json');
  const i = forms(tl).quiz;
  const { countdown_start: start, countdown_seconds: secs } = tl.scenes[i].quiz;
  const s = spyStage(tl);
  await s.seekTo(i, start - 0.3);
  assert.equal(s.ctx(i).view.ring.box.style.visibility, 'hidden', 'not before its mark');
  const t0 = await s.seekTo(i, start + 0.4);
  const v = s.ctx(i).view;
  assert.equal(v.ring.box.style.visibility, 'visible');
  assert.equal(v.ring.num.textContent, String(Math.max(0, Math.ceil(secs * (1 - (t0 - start) / secs) - 1e-6))));
  // the arc empties linearly: half the think time leaves half the dash
  const half = await s.seekTo(i, start + secs / 2);
  const dash = Number(v.ring.arc.getAttribute('stroke-dashoffset'));
  const left = 1 - (half - start) / secs;
  assert.ok(Math.abs(dash) > 0, 'the arc is partly drawn');
  assert.ok(Math.abs(left - 0.5) < 0.1, 'measured at the half-way mark');
  await s.seekTo(i, start + secs + 0.4);
  assert.equal(s.ctx(i).view.ring.box.style.visibility, 'hidden', 'gone once the time is up');
  assert.equal(s.ctx(i).view.ring.num.textContent, '', 'and its number with it');
  s.done();
});

test('the ring\'s arc is a structure mark and nothing else takes a structure role (§4.2)', async () => {
  const tl = load('check.portrait.json');
  const i = forms(tl).quiz;
  const s = spyStage(tl);
  await s.seekTo(i, tl.scenes[i].quiz.countdown_start + 0.5);
  const ctx = s.ctx(i);
  assert.equal(ctx.view.ring.arc.getAttribute('stroke'), ctx.roles.color('struct.accent'));
  const accent = ctx.roles.color('struct.accent');
  for (const o of ctx.view.options) {
    assert.notEqual(o.card.style.background, rgb(accent), 'a tile never takes the chapter accent');
  }
  s.done();
});

test('reduced motion (live only): entrances fade in place, nothing translates', async () => {
  const tl = load('check.landscape.json');
  const i = forms(tl).quiz;
  const s = spyStage(tl, { mode: 'live', reducedMotion: true });
  await s.seekTo(i, OPTION_DELAY + 0.1);
  for (const o of s.ctx(i).view.options) assert.equal(o.card.style.transform, 'none');
  s.done();
});

test('every dim group is returned at every t and the template never writes their opacity', async () => {
  const tl = load('check.portrait.json');
  const i = forms(tl).quiz;
  const s = spyStage(tl);
  await s.seekTo(i, 1.0);
  const ctx = s.ctx(i);
  const groups = /** @type {any[]} */ (check.dimGroups?.(ctx));
  assert.ok(groups.length >= 1 + 1 + ctx.view.options.length, 'the label, the question, the ring and every tile');
  for (const g of groups) {
    assert.ok(/** @type {HTMLElement} */ (g).classList.contains('dyn-group'), 'a group the stage owns');
    assert.equal(/** @type {HTMLElement} */ (g).style.opacity, '', 'the stage owns a group\'s opacity');
  }
  await s.seekTo(i, tl.scenes[i].quiz.reveal_start + 1.0);
  assert.deepEqual(check.dimGroups?.(s.ctx(i)), groups, 'the same elements at every t');
  s.done();
});

test('focusRect: an option tile is a shape on whole pixels, the question and the label are text', async () => {
  const tl = load('check.landscape.json');
  const { quiz, board } = forms(tl);
  const s = spyStage(tl);
  await s.seekTo(quiz, 1.5);
  const ctx = s.ctx(quiz);
  const tile = /** @type {any} */ (check.focusRect?.(ctx, 'option:1'));
  assert.ok(tile, 'option:1');
  assert.equal(tile.kind, 'shape', 'its outline is drawn outside the tile, never across its text');
  assert.equal(tile.radius, 24);
  for (const k of ['x', 'y', 'w', 'h']) assert.ok(Number.isInteger(tile.rect[k]), JSON.stringify(tile.rect));
  assert.ok(/** @type {any} */ (check.dimGroups?.(ctx)).includes(tile.el), 'with the group holding it');
  assert.equal(check.focusRect?.(ctx, 'option:9'), null, 'an option that is not there');
  assert.equal(check.focusRect?.(ctx, 'nope'), null);
  await s.seekTo(board, 1.0);
  const asked = /** @type {any} */ (check.focusRect?.(s.ctx(board), 'q1'));
  assert.ok(asked && asked.kind === 'text', 'the board form\'s question');
  s.done();
});

test('itemEl: the board form\'s question and then its answer, in the item\'s field order', async () => {
  const tl = load('check.landscape.json');
  const { quiz, board } = forms(tl);
  const s = spyStage(tl);
  await s.seekTo(board, 1.0);
  const ctx = s.ctx(board);
  const els = /** @type {any} */ (check.itemEl?.(ctx, 'q1'));
  assert.ok(Array.isArray(els) && els.length === 2, 'text, then justification');
  assert.equal(els[0], ctx.view.question.el);
  assert.equal(els[1], ctx.view.explain.el);
  assert.equal(check.itemEl?.(ctx, 'nope'), null);
  await s.seekTo(quiz, 1.0);
  assert.equal(check.itemEl?.(s.ctx(quiz), 'q1'), null, 'a quiz scene has no board item to name');
  s.done();
});

test('texts(): every slot preflight needs, on the colour it is drawn on, at the alpha painted now', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const fmt = tl.presentation.format;
    const L = LAYOUT[fmt];
    const { quiz } = forms(tl);
    const s = spyStage(tl);
    await s.seekTo(quiz, tl.scenes[quiz].quiz.countdown_start + 0.4);
    const ctx = s.ctx(quiz);
    const entries = /** @type {any[]} */ (check.texts?.(ctx));
    const slots = entries.map((e) => e.slot);
    assert.ok(slots.includes('question') && slots.includes('option') && slots.includes('ring'), `${name}: ${slots}`);
    const question = entries.find((e) => e.slot === 'question');
    assert.equal(question.critical, true);
    assert.equal(question.bg, ctx.onBackground.bg, `${name}: the question is on the background`);
    assert.equal(question.ink, ctx.onBackground.ink);
    assert.equal(question.weight, L.question.weight);
    const option = entries.find((e) => e.slot === 'option');
    assert.equal(option.bg, ctx.view.surface, `${name}: a tile's text is on the tile`);
    assert.equal(option.ink, ctx.view.ink);
    assert.equal(entries.find((e) => e.slot === 'ring').critical, false, 'R-07: the number is not critical text');
    for (const e of entries) {
      assert.ok(e.alpha >= 0 && e.alpha <= 1, `${name}: ${e.slot} alpha ${e.alpha}`);
      assert.ok(e.size >= e.min, `${name}: ${e.slot} ${e.size} >= ${e.min}`);
      assert.equal(typeof e.text, 'string');
    }
    s.done();
  }
});

test('texts(): a scrim preflight asked for is drawn behind the text and reported as the colour it sits on', async () => {
  const tl = load('check.landscape.json');
  const i = forms(tl).quiz;
  tl.scenes[i].look.fixes = { safe: false, slots: { question: { scrim: true } } };
  const s = spyStage(tl);
  await s.seekTo(i, 1.0);
  const ctx = s.ctx(i);
  const scrim = ctx.roles.color('scrim');
  assert.ok(ctx.view.question.inner.style.background, 'the scrim is drawn behind the question');
  const entry = /** @type {any[]} */ (check.texts?.(ctx)).find((e) => e.slot === 'question');
  assert.equal(entry.bg, scrimmedBg(scrim, ctx.onBackground, ctx.onBackground.ink), 'measured on the scrim over the background');
  s.done();
});

test('preflight\'s size cap holds a slot at or below it (the fixes channel, core.js fixesOf)', async () => {
  const tl = load('check.landscape.json');
  const i = forms(tl).quiz;
  tl.scenes[i].look.fixes = { safe: false, slots: { question: { size: LAYOUT.landscape.question.min } } };
  const s = spyStage(tl);
  await s.seekTo(i, 1.0);
  assert.equal(s.ctx(i).view.question.size, LAYOUT.landscape.question.min);
  s.done();
});

// --- the fit, on a fake text layout ---------------------------------------------------------------------------------

/**
 * Install a fake text layout on HTMLElement (jsdom has none): a glyph is 0.55 em wide, words wrap greedily, a word
 * wider than its line breaks across lines. Enough for this template's fit: the height of the question, an option, the
 * explanation and the label from the font size, the line height and the box's width. Returns the restore function.
 */
function fakeLayout() {
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const keys = ['offsetHeight', 'offsetWidth', 'clientWidth', 'offsetTop'];
  const saved = keys.map((k) => [k, Object.getOwnPropertyDescriptor(proto, k)]);
  const CW = 0.55;
  const TEXTS = '.dyn-ck-q, .dyn-ck-o, .dyn-ck-x, .dyn-ck-label';
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

test('the fit (fake layout): every slot sits in its range on a 4 px step, and the block fits its box', async () => {
  const restore = fakeLayout();
  try {
    for (const name of FIXTURES) {
      const tl = load(name);
      const fmt = /** @type {'landscape' | 'portrait'} */ (tl.presentation.format);
      const L = LAYOUT[fmt];
      const b = boxes(fmt, ZONES[fmt]);
      for (const i of [forms(tl).quiz, forms(tl).board]) {
        const s = spyStage(tl);
        await s.seekTo(i, 1.0);
        const v = s.ctx(i).view;
        for (const [slot, part, range] of /** @type {const} */ ([['question', v.question, L.question],
          ['explain', v.explain, L.explain]])) {
          if (!part) continue;
          assert.ok(part.size >= range.min && part.size <= range.max, `${name} ${slot}: ${part.size} in [${range.min}, ${range.max}]`);
          assert.equal((range.max - part.size) % SIZE_STEP, 0, `${name} ${slot}: a 4 px step from the maximum`);
        }
        const sizes = new Set(v.options.map((/** @type {any} */ o) => o.size));
        assert.ok(sizes.size <= 1, `${name}: one size for every option`);
        const heights = new Set(v.options.map((/** @type {any} */ o) => o.holder.style.height));
        assert.ok(heights.size <= 1, `${name}: every tile the same height`);
        // the block stays inside the content box, or says so (the next test)
        const bottom = v.explain
          ? Number.parseFloat(v.explain.holder.style.top) + v.explain.el.offsetHeight
          : Number.parseFloat(v.question.holder.style.top) + v.question.el.offsetHeight;
        const over = /** @type {any[]} */ (check.texts?.(s.ctx(i))).some((e) => Number(e.overflow) > 0);
        if (!over) assert.ok(bottom <= b.block.y1 + 1, `${name}: the block ends inside the box (${bottom} <= ${b.block.y1})`);
        s.done();
      }
    }
  } finally {
    restore();
  }
});

test('the fit (fake layout): a question that cannot fit at its minimum is reported as overflow, never clipped', async () => {
  const restore = fakeLayout();
  try {
    const tl = load('check.portrait.json');
    const i = forms(tl).quiz;
    tl.scenes[i].quiz.question = `${'A very long question that keeps going and going and going. '.repeat(8)}`;
    const s = spyStage(tl);
    await s.seekTo(i, 1.0);
    const ctx = s.ctx(i);
    assert.equal(ctx.view.question.size, LAYOUT.portrait.question.min, 'held at the minimum');
    const over = /** @type {any[]} */ (check.texts?.(ctx)).filter((e) => Number(e.overflow) > 0);
    assert.ok(over.length > 0, 'preflight is told (it blocks with "Shorten text")');
    assert.ok(over.some((e) => e.slot === 'question'), over.map((e) => e.slot).join(','));
    s.done();
  } finally {
    restore();
  }
});

test('the fit (fake layout): a long option keeps every line and the tile grows to hold it', async () => {
  const restore = fakeLayout();
  try {
    const tl = load('check.landscape.json');
    const i = forms(tl).quiz;
    const s = spyStage(tl);
    await s.seekTo(i, 1.0);
    const v = s.ctx(i).view;
    const tall = Number.parseFloat(v.options[0].holder.style.height);
    assert.ok(tall >= 2 * LAYOUT.landscape.pad + LAYOUT.landscape.badge, 'a tile is at least its padding and letter');
    const wrapped = v.options.find((/** @type {any} */ o) => o.body.offsetHeight > o.size * o.lh * 1.5);
    if (wrapped) assert.ok(tall >= wrapped.body.offsetHeight, 'a wrapped option keeps every line inside its tile');
    s.done();
  } finally {
    restore();
  }
});
