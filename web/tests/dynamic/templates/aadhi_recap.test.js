// @ts-check
// templates/aadhi_recap.js (DYNAMIC_VIDEO_DESIGN §4.3 aadhi_recap, §3.3 "16:9 / 9:16 aadhi_recap", §3.4; B2 plan §1.4):
// the two fixture timelines (tests/compose/dynamic/make_aadhi_fixtures.py) through the harness's contract and order
// gates, the pure helpers (data, slots, TeX), and the painted behaviour: the prompt card on his gesture side with three
// empty slots and no countdown in 16:9, the prompt in the hook band in 9:16, Aadhi's card fading out at t_exit (the
// stage's presenter), the text entry preflight reads, and the fallback.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { templateSlots } from '../../../js/dynamic/core.js';
import { FADE_SECONDS, presenterPaint, presenterSpec } from '../../../js/dynamic/presenter.js';
import aadhiIntro from '../../../js/dynamic/templates/aadhi_intro.js';
import aadhiRecap, {
  CARD_AT, CARD_SECONDS, ID, LAYOUT, SLOTS, drawsScene, recapItems, slotRows, texOf,
} from '../../../js/dynamic/templates/aadhi_recap.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['aadhi_recap.landscape.json', 'aadhi_recap.portrait.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { aadhi_recap: aadhiRecap, aadhi_intro: aadhiIntro } };
// Whether stage.js draws the presenter through presenter.js (step 5's request; aadhi_intro.test.js explains)
const STAGE_PAINTS_PRESENTER = readFileSync(new URL('../../../js/dynamic/stage.js', import.meta.url), 'utf-8').includes("from './presenter.js'");

/** The recap appearance's index in a fixture. @param {any} tl */
const recapIndex = (tl) => tl.scenes.findIndex((/** @type {any} */ s) => s.look.template === ID);

/**
 * A render stage with both presenter templates through the seam, and the context each scene was built with.
 * @param {any} tl
 */
function spyStage(tl) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...aadhiRecap, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return aadhiRecap.build(ctx); } };
  const run = renderStage(tl, { templates: { aadhi_recap: /** @type {any} */ (wrapped), aadhi_intro: aadhiIntro }, renderTex: recordingTex().renderTex });
  /** @param {number} i @param {number} t */
  const seekTo = async (i, t) => {
    const scene = tl.scenes[i];
    await run.stage.seek(Math.round((Number(scene.start) + t) * tl.fps));
  };
  return { ...run, contexts, seekTo, ctx: (/** @type {number} */ i) => contexts.get(i) };
}

/** An opacity as a number ('' is 1). @param {string} v */
const op = (v) => (v === '' ? 1 : Number(v));

test('every fixture keeps the template contract (harness)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(aadhiRecap, load(name)), [], name);
});

test('every fixture paints the recap the same forward, in reverse and on a fresh page (until Aadhi\'s exit)', async () => {
  // the frames after t_exit are the stage presenter's (aadhi_intro.test.js explains): checked here up to t_exit, and
  // the template's own layer through the scene's end below
  for (const name of FIXTURES) {
    const tl = load(name);
    const i = recapIndex(tl);
    const s = tl.scenes[i];
    const f0 = Math.round(s.start * tl.fps);
    const exit = Math.round((s.start + /** @type {any} */ (presenterSpec(s)).t_exit) * tl.fps);
    const end = Math.round((s.start + s.duration) * tl.fps);
    const frames = [0, 2, 5, 9, 14, 20, 30, 45, 60, 90, 120].map((d) => f0 + d).filter((f) => f < (STAGE_PAINTS_PRESENTER ? end : exit));
    if (STAGE_PAINTS_PRESENTER) frames.push(exit, exit + 3, end - 1);
    assert.deepEqual(await orderProblems(tl, frames, SEAM), [], name);
  }
});

test('the template layer stays the same in any order through Aadhi\'s exit and the scene\'s end', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const i = recapIndex(tl);
    const s = tl.scenes[i];
    const end = Math.round((s.start + s.duration) * tl.fps);
    const frames = [end - 12, end - 8, end - 4, end - 1];
    /** @param {number[]} order */
    const run = async (order) => {
      const { stage, done } = renderStage(structuredClone(tl), { ...SEAM, renderTex: recordingTex().renderTex });
      /** @type {Map<number, string>} */
      const out = new Map();
      for (const f of order) {
        await stage.seek(f);
        out.set(f, String(stage.el.querySelector(`.dyn-scene[data-scene="${i}"] .dyn-content`)?.outerHTML));
      }
      done();
      return out;
    };
    const a = await run(frames);
    const b = await run([...frames].reverse());
    for (const f of frames) assert.equal(b.get(f), a.get(f), `${name} frame ${f}`);
  }
});

test('the fixtures: both formats, the synthesised prompt, Aadhi\'s card fading at t_exit, 5-8 s', () => {
  const formats = new Set();
  for (const name of FIXTURES) {
    const tl = load(name);
    const s = tl.scenes[recapIndex(tl)];
    formats.add(tl.presentation.format);
    assert.ok(drawsScene(s), name);
    assert.ok(s.duration >= 5 - 1e-9 && s.duration <= 8 + 1e-9, name);
    const spec = /** @type {any} */ (presenterSpec(s));
    assert.equal(spec.exit_kind, 'fade');
    assert.equal(spec.exit_s, FADE_SECONDS);
    assert.ok(Math.abs(spec.t_hide - s.duration) < 1e-6);
    assert.equal(spec.layout, tl.presentation.format === 'landscape' ? 'card_left' : 'card');
    assert.equal(tl.scenes[recapIndex(tl) + 1].look.template, 'recap_retrieval', 'the recap opens the retrieval recap');
    assert.equal(/** @type {any} */ (recapItems(s).prompt).text, 'Quick check: what are the 3 big ideas?');
  }
  assert.deepEqual([...formats].sort(), ['landscape', 'portrait']);
});

test('drawsScene: exactly one prompt, nothing else with text, no shown panel; texOf; slotRows', () => {
  const q = { id: 'p', kind: 'question', text: 'Quick check $x$?' };
  assert.equal(drawsScene(/** @type {any} */ ({ board: [q] })), true);
  assert.equal(drawsScene(/** @type {any} */ ({ board: [] })), false);
  assert.equal(drawsScene(/** @type {any} */ ({ board: [q, { id: 'h', kind: 'heading', text: 'x' }] })), false);
  assert.equal(drawsScene(/** @type {any} */ ({ board: [q], side_panel: { panel: {} } })), false);
  assert.deepEqual(texOf(/** @type {any} */ ({ board: [q] })), [{ tex: 'x', display: false }]);
  const rows = slotRows();
  assert.equal(rows.prompt.landscape.default.w, LAYOUT.landscape.card.x1 - LAYOUT.landscape.card.x0 - 2 * LAYOUT.landscape.pad);
  assert.deepEqual(rows.prompt.portrait.default.size, [LAYOUT.portrait.prompt.min, LAYOUT.portrait.prompt.max]);
  assert.equal(rows.prompt.portrait.default.w, 696);
  const cat = /** @type {any} */ (templateSlots(ID));
  if (cat && Object.keys(cat).length) {
    for (const fmt of ['landscape', 'portrait']) {
      for (const k of ['w', 'max_lines', 'size', 'face', 'weight', 'line_height']) {
        assert.deepEqual(cat.prompt[fmt].default[k], /** @type {any} */ (rows.prompt)[fmt].default[k], `${fmt}.${k}`);
      }
    }
  }
});

test('16:9: the prompt card on his gesture side rises in, three empty slots follow, no countdown; Aadhi fades at t_exit', async () => {
  const tl = load('aadhi_recap.landscape.json');
  const i = recapIndex(tl);
  const s = tl.scenes[i];
  const run = spyStage(tl);
  try {
    await run.seekTo(i, 0);
    const ctx = run.ctx(i);
    const v = ctx.view;
    assert.equal(v.slots.length, SLOTS);
    assert.equal(op(v.card.style.opacity), 0);
    assert.equal(ctx.root.querySelector('.dyn-ring'), null, 'no countdown (§3.1: he is gone before anyone thinks)');
    await run.seekTo(i, CARD_AT + CARD_SECONDS + 1.5);
    assert.equal(op(v.card.style.opacity), 1);
    for (const sl of v.slots) assert.equal(op(sl.style.opacity), 1);
    const entry = /** @type {any} */ ((aadhiRecap.texts ? aadhiRecap.texts(ctx) : [])[0]);
    assert.equal(entry.slot, 'prompt');
    assert.equal(entry.critical, true);
    assert.equal(entry.bg, ctx.roles.color('surface'));
    assert.equal(entry.ink, ctx.roles.color('ink'));
    assert.equal(entry.alpha, 1);
    const spec = /** @type {any} */ (presenterSpec(s));
    assert.ok(presenterPaint(spec, spec.t_exit + FADE_SECONDS / 2).opacity < 1);
    assert.equal(presenterPaint(spec, s.duration).visible, false);
  } finally {
    run.done();
  }
});

test('9:16: the prompt in the hook band, on the background; no card of slots', async () => {
  const tl = load('aadhi_recap.portrait.json');
  const i = recapIndex(tl);
  const run = spyStage(tl);
  try {
    await run.seekTo(i, 2);
    const ctx = run.ctx(i);
    const v = ctx.view;
    assert.equal(v.card, null);
    assert.equal(v.slots.length, 0);
    const entry = /** @type {any} */ ((aadhiRecap.texts ? aadhiRecap.texts(ctx) : [])[0]);
    assert.equal(entry.bg, ctx.onBackground.bg);
    assert.equal(entry.alpha, 1);
  } finally {
    run.done();
  }
});

test('a recap scene with another item is board_generic\'s whole', async () => {
  const tl = load('aadhi_recap.landscape.json');
  const i = recapIndex(tl);
  tl.scenes[i].board.push({ id: 'x', kind: 'bullet', text: 'more' });
  const run = spyStage(tl);
  try {
    await run.seekTo(i, 1);
    assert.equal(run.ctx(i).view.arFallback, true);
  } finally {
    run.done();
  }
});
