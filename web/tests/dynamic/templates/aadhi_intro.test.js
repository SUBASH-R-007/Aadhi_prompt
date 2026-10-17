// @ts-check
// templates/aadhi_intro.js (DYNAMIC_VIDEO_DESIGN §4.3 aadhi_intro, §3.3 the layouts and choreography; B2 plan §1.4 the
// template contract, §1.12, §2 "Aadhi" and R-54): the six fixture timelines (tests/compose/dynamic/
// make_aadhi_fixtures.py) through the harness's contract and order gates, the pure helpers (data, variants, slots, TeX,
// the exit time), and the painted behaviour in both formats: the chip on frame 0, the title's per-word rise, the hook
// landing at its reveal, the 9:16 frame-0 cover, the inks of each layout (the studio wall, the split panel, the theme
// background), the brand wipe in the next scene's accent, the text entries preflight reads, and the fallback.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { parseTarget, templateSlots } from '../../../js/dynamic/core.js';
import { presenterSpec, wipeProgress } from '../../../js/dynamic/presenter.js';
import aadhiIntro, {
  CHIP, DEFAULT_VARIANT, HOOK_AT, ID, LAYOUT, PORTRAIT, RISE, SCENE_TITLE_ITEM, TITLE_AT, VARIANTS, WORD_SECONDS,
  chipText, drawsScene, plainRich,
  exitAt, introItems, setsAtFrame0, slotRows, texOf, variantOf,
} from '../../../js/dynamic/templates/aadhi_intro.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['aadhi_intro.landscape.json', 'aadhi_intro.landscape.light.json', 'aadhi_intro.landscape.hook.json',
  'aadhi_intro.landscape.card.json', 'aadhi_intro.portrait.json', 'aadhi_intro.portrait.hook.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { aadhi_intro: aadhiIntro } };

/**
 * A render stage of `tl` with aadhi_intro through the stage's test seam, and the context each scene was built with.
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...aadhiIntro, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return aadhiIntro.build(ctx); } };
  const run = renderStage(tl, { templates: { aadhi_intro: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
  /** @param {number} i @param {number} t */
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
/** A hex colour as the DOM reports a painted one. @param {string} hex */
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;
/** The intro scene (the first) of a fixture. @param {any} tl */
const intro = (tl) => tl.scenes[0];

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, whole-px focus, terms, TeX, entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(aadhiIntro, load(name)), [], name);
});

// Whether stage.js draws the presenter through presenter.js (step 5's request: the Slot paints the presenter's frame,
// visibility, settle, slide and fade as a pure function of t). Before it, the Slot leaves the last frame's `src` on the
// hidden presenter after t_exit, so the WHOLE stage's DOM after t_exit depends on the frames painted before (pixels do
// not: it is hidden). The template's own layer is checked over the exit in every case (`exitOrderProblems`); the whole
// stage over the exit once the request is in.
const STAGE_PAINTS_PRESENTER = readFileSync(new URL('../../../js/dynamic/stage.js', import.meta.url), 'utf-8').includes("from './presenter.js'");

/** The exit frames of a fixture's intro: the wipe (or the slide), its last frame and the next scene's first. @param {any} tl */
function exitFrames(tl) {
  const s = intro(tl);
  const end = Math.round((s.start + s.duration) * tl.fps);
  const exit = exitAt(s);
  const out = new Set([end - 1, end, end + 3]);
  for (const d of [-0.05, 0, 0.1, 0.2, 0.3, 0.45, 0.59]) out.add(Math.min(end - 1, Math.round((exit + d) * tl.fps)));
  return [...out].sort((a, b) => a - b);
}

/**
 * The frames whose TEMPLATE layer (`.dyn-content` of the intro) differs painted forward, in reverse or fresh.
 * @param {any} tl @param {number[]} frames
 */
async function exitOrderProblems(tl, frames) {
  /** @param {number[]} order */
  const run = async (order) => {
    const { stage, done } = renderStage(structuredClone(tl), { ...SEAM, renderTex: recordingTex().renderTex });
    /** @type {Map<number, string>} */
    const out = new Map();
    for (const f of order) {
      await stage.seek(f);
      const el = stage.el.querySelector('.dyn-scene[data-scene="0"] .dyn-content');
      out.set(f, el ? el.outerHTML.replace(/ style=""/g, '') : '');
    }
    done();
    return out;
  };
  const forward = await run(frames);
  const backward = await run([...frames].reverse());
  /** @type {string[]} */
  const problems = [];
  for (const f of frames) {
    if (backward.get(f) !== forward.get(f)) problems.push(`frame ${f}: the template layer differs painted in reverse`);
    if ((await run([f])).get(f) !== forward.get(f)) problems.push(`frame ${f}: the template layer differs on a fresh page`);
  }
  return problems;
}

test('every fixture paints the same frame forward, in reverse and on a fresh page (a frame is a pure function of t)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const s = intro(tl);
    const exit = Math.round((s.start + exitAt(s)) * tl.fps);
    const frames = new Set([0, 1, 3, 5, 8, 12, 20, 30, 45, 60, 90, 120].filter((f) => f < exit));
    for (const e of s.events || []) for (const d of [0, 0.2, 0.5]) frames.add(Math.min(exit - 1, Math.round((s.start + e.t + d) * tl.fps)));
    if (STAGE_PAINTS_PRESENTER) for (const f of exitFrames(tl)) frames.add(f);
    assert.deepEqual(await orderProblems(tl, [...frames].filter((f) => f >= 0).sort((a, b) => a - b), SEAM), [], name);
    assert.deepEqual(await exitOrderProblems(tl, exitFrames(tl)), [], `${name} (the exit)`);
  }
});

test('the fixtures cover every layout, both formats and modes, the hook and no hook, scripts and the cue forms', () => {
  const seen = { variants: new Set(), formats: new Set(), modes: new Set(), parts: new Set(), scripts: new Set(), cues: new Set() };
  for (const name of FIXTURES) {
    const tl = load(name);
    const s = intro(tl);
    assert.equal(s.look.template, ID, name);
    seen.formats.add(tl.presentation.format);
    seen.modes.add(tl.presentation.mode);
    seen.variants.add(variantOf(s.look, tl.presentation.format));
    const { title, question } = introItems(s);
    seen.parts.add(question ? 'hook' : 'no-hook');
    if (title) seen.parts.add('title');
    if (chipText(s)) seen.parts.add('chip');
    for (const e of s.events || []) seen.cues.add(`${e.kind}:${parseTarget(e.target).form}`);
    const text = `${title?.text || ''} ${question?.text || ''}`;
    if (/\p{Script=Tamil}/u.test(text)) seen.scripts.add('tamil');
    if (/\p{Script=Devanagari}/u.test(text)) seen.scripts.add('devanagari');
    // a long session title is set whole (never cut with an ellipsis: step-5 review R5-5)
    if (String(title?.text || '').length > 80) seen.scripts.add('long-title');
    assert.ok(!/…$/.test(String(title?.text || '')), `${name}: the title is never cut`);
  }
  assert.deepEqual([...seen.variants].sort(), [...VARIANTS.landscape, ...VARIANTS.portrait].sort());
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.parts].sort(), ['chip', 'hook', 'no-hook', 'title']);
  assert.deepEqual([...seen.scripts].sort(), ['devanagari', 'long-title', 'tamil']);
  assert.deepEqual([...seen.cues].sort(), ['cue_term:item:term', 'reveal:item']);
});

test('every fixture intro: 5-8 s, Aadhi placed by the look, the wipe in the next scene\'s accent, a crossfade after it', () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const s = intro(tl);
    assert.ok(s.duration >= 5 - 1e-9 && s.duration <= 8 + 1e-9, `${name}: ${s.duration}`);
    const spec = /** @type {any} */ (presenterSpec(s));
    assert.ok(spec, name);
    assert.equal(spec.layout, variantOf(s.look, tl.presentation.format), name);
    assert.ok(Math.abs(spec.t_hide - s.duration) < 1e-6, `${name}: Aadhi leaves with the scene's end`);
    const next = tl.scenes[1];
    assert.equal(next.look.transition_in.kind, 'crossfade', name);
    assert.ok(next.look.transition_in.seconds >= 0.3, name);
    if (tl.presentation.format === 'landscape') {
      assert.equal(s.look.refs.accent_next, next.look.tokens['block.base'], name);
      assert.equal(spec.exit_kind, 'wipe');
    } else {
      assert.equal(spec.exit_kind, 'slide_down');
      assert.equal(spec.enter_kind, 'rise');
    }
  }
});

// --- pure helpers ---------------------------------------------------------------------------------------------------

test('introItems / drawsScene: a title and a hook at most, at least one of them, nothing else, no shown panel', () => {
  /** @param {any[]} board @param {any} [extra] @returns {any} */
  const sc = (board, extra = {}) => ({ board, ...extra });
  const heading = { id: 't', kind: 'heading', text: 'Ohm' };
  const q = { id: 'q', kind: 'question', text: 'Why?' };
  assert.equal(drawsScene(sc([heading])), true);
  assert.equal(drawsScene(sc([q])), true);
  assert.equal(drawsScene(sc([heading, q])), true);
  assert.equal(drawsScene(sc([])), false);
  assert.equal(drawsScene(sc([{ id: 'e', kind: 'heading', text: '  ' }])), false);
  assert.equal(drawsScene(sc([heading, { id: 'b', kind: 'bullet', text: 'x' }])), false);
  assert.equal(drawsScene(sc([heading, { ...heading, id: 't2' }])), false, 'a second heading is not drawn here');
  assert.equal(drawsScene(sc([heading, { id: 'f', kind: 'figure', figure_id: 'fig-1' }])), false, 'a figure has no text and is still content');
  assert.equal(drawsScene(sc([heading, { id: 'q0', kind: 'question', text: '' }])), true, 'an empty question draws nothing');
  assert.equal(drawsScene(sc([heading], { side_panel: { panel: { kind: 'image' } } })), false);
  assert.equal(drawsScene(sc([heading], { side_panel: { panel: { kind: 'image' } }, layout: { show_side_panel: false } })), true);
  const items = introItems(/** @type {any} */ (sc([q, heading])));
  assert.equal(items.title?.id, 't');
  assert.equal(items.question?.id, 'q');
});

test('introItems / drawsScene: a board with no heading takes the scene\'s own title (step-5 review R5-2)', () => {
  // transform_survival_dynamic's s1: a screenplay presenter scene with its title and subtitle and an empty board was
  // handed to board_generic, so the video opened without Aadhi (templates.py _fit_aadhi_intro takes the same scenes)
  /** @param {any[]} board @param {any} [extra] @returns {any} */
  const sc = (board, extra = {}) => ({ title: "Ohm's Law", subtitle: 'Why current flows', board, ...extra });
  assert.equal(drawsScene(sc([])), true);
  const own = introItems(sc([]));
  assert.deepEqual(own.title, { id: SCENE_TITLE_ITEM, kind: 'heading', text: "Ohm's Law" });
  assert.equal(own.question, null);
  assert.equal(drawsScene(sc([], { title: '   ' })), false, 'no title anywhere: nothing to set');
  assert.equal(drawsScene(sc([{ id: 'e', kind: 'heading', text: ' ' }])), true, 'an empty heading draws nothing');
  const q = { id: 'q', kind: 'question', text: 'Why?' };
  const both = introItems(sc([q]));
  assert.equal(both.title?.id, SCENE_TITLE_ITEM, 'the title stack: the scene\'s title above the hook');
  assert.equal(both.question?.id, 'q');
  assert.equal(introItems(sc([{ id: 't', kind: 'heading', text: 'Board title' }])).title?.id, 't', 'a heading wins');
  assert.equal(drawsScene(sc([{ id: 'b', kind: 'bullet', text: 'x' }])), false, 'another item: board_generic');
});

test('a scene title is plain text: its rich-lite specials are escaped, never drawn as TeX or emphasis (audit A5-2)', async () => {
  // board_generic and formula_focus draw a scene title literally; presenter.py plain_rich escapes the synthesised
  // intro's title the same way. Unescaped, `$...$` became TeX, `*...*` italics and backticks code here only.
  assert.equal(plainRich('Graph of  $y = x^2$ *and* `code` [1] \\ end'),
    'Graph of \\$y = x^2\\$ \\*and\\* \\`code\\` \\[1\\] \\\\ end');
  /** @type {any} */
  const sc = { title: 'Speed = $d/t$: *the* `key` idea', board: [] };
  assert.equal(introItems(sc).title?.text, 'Speed = \\$d/t\\$: \\*the\\* \\`key\\` idea');
  const tl = load('aadhi_intro.landscape.json');
  const s = intro(tl);
  s.board = [];
  s.title = 'Speed = $d/t$: *the* `key` idea';
  const run = spyStage(tl);
  try {
    await run.seekTo(0, 2);
    const ctx = run.ctx(0);
    assert.notEqual(ctx.view.aiFallback, true, 'aadhi_intro draws it');
    const el = ctx.view.titleRow.el;
    assert.equal(el.querySelector('em, strong, code, mjx-container, svg'), null, 'no emphasis, code or TeX in the title');
    assert.equal(el.textContent.replace(/\s+/g, ' ').trim(), 'Speed = $d/t$: *the* `key` idea', 'drawn literally');
    const entry = /** @type {any} */ ((aadhiIntro.texts?.(ctx) || []).find((e) => e.slot === 'title'));
    assert.equal(entry?.text, 'Speed = $d/t$: *the* `key` idea', 'reported as drawn');
  } finally {
    run.done();
  }
});

test('the scene\'s own title is drawn and reported like a heading', async () => {
  const tl = load('aadhi_intro.landscape.json');
  const s = intro(tl);
  s.board = [];
  s.title = 'The title from the scene';
  const run = spyStage(tl);
  try {
    await run.seekTo(0, 2);
    const ctx = run.ctx(0);
    assert.notEqual(ctx.view.aiFallback, true, 'aadhi_intro draws it');
    assert.equal(ctx.view.titleRow.item.id, SCENE_TITLE_ITEM);
    assert.equal(ctx.view.titleRow.el.textContent.replace(/\s+/g, ' ').trim(), 'The title from the scene');
    const entry = /** @type {any} */ ((aadhiIntro.texts?.(ctx) || []).find((e) => e.slot === 'title'));
    assert.ok(entry && entry.critical && entry.text === 'The title from the scene');
  } finally {
    run.done();
  }
});

test('variantOf: the look\'s variant for the format, else the presenter\'s layout, else the default', () => {
  assert.equal(variantOf({ variant: 'pip' }, 'landscape'), 'pip');
  assert.equal(variantOf({ variant: 'card' }, 'landscape'), DEFAULT_VARIANT.landscape);
  assert.equal(variantOf({ presenter: { layout: 'split' } }, 'landscape'), 'split');
  assert.equal(variantOf({ variant: 'pip' }, 'portrait'), 'card');
  assert.equal(variantOf(null, 'landscape'), 'card_left');
  assert.deepEqual([...VARIANTS.landscape], ['fullframe', 'card_left', 'pip', 'split']);
  assert.equal(setsAtFrame0('portrait', 0), true);
  assert.equal(setsAtFrame0('portrait', 1), false);
  assert.equal(setsAtFrame0('landscape', 0), false);
});

test('texOf declares the TeX of the chip, the title and the hook; exitAt follows the presenter', () => {
  const scene = /** @type {any} */ ({ subtitle: 'Unit $2$', board: [{ id: 't', kind: 'heading', text: 'Law $V=IR$' }, { id: 'q', kind: 'question', text: 'Why $I$?' }], duration: 5 });
  assert.deepEqual(texOf(scene), [{ tex: '2', display: false }, { tex: 'V=IR', display: false }, { tex: 'I', display: false }]);
  assert.equal(exitAt(scene), 5 - 0.6);
  assert.equal(exitAt({ ...scene, look: { presenter: { clip_key: 'a.mp4', rect: [0, 0, 10, 10], t_exit: 4.1 } } }), 4.1);
});

test('slotRows: chip / title / question per 16:9 layout and chip / headline / eyebrow in 9:16, matching LAYOUT', () => {
  const rows = slotRows();
  for (const v of VARIANTS.landscape) {
    const L = /** @type {any} */ (LAYOUT)[v];
    const w = L.box.x1 - L.box.x0;
    assert.deepEqual(rows.title.landscape[v].size, [L.title.min, L.title.max]);
    assert.equal(rows.title.landscape[v].w, w);
    assert.equal(rows.question.landscape[v].max_lines, L.question.lines);
    assert.equal(rows.chip.landscape[v].w, w - 2 * CHIP.landscape.pad[1]);
    assert.equal(rows.chip.landscape[v].critical, false, 'the chip is decoration (board_generic kicker rule)');
  }
  assert.deepEqual(rows.headline.portrait.default.size, [PORTRAIT.headline.min, PORTRAIT.headline.max]);
  assert.equal(rows.headline.portrait.default.w, 696);
  assert.equal(rows.eyebrow.portrait.default.max_lines, 2);
  // the catalogue mirrors these rows once templates.py registers the template (step 5's integration)
  const cat = /** @type {any} */ (templateSlots(ID));
  if (cat && Object.keys(cat).length) {
    for (const [slot, perFormat] of Object.entries(rows)) {
      for (const [fmt, perVariant] of Object.entries(perFormat)) {
        for (const [variant, row] of Object.entries(/** @type {any} */ (perVariant))) {
          const got = cat[slot][fmt][variant];
          for (const k of ['w', 'max_lines', 'size', 'face', 'weight', 'line_height', 'critical']) {
            assert.deepEqual(got[k], row[k], `${slot}.${fmt}.${variant}.${k}`);
          }
        }
      }
    }
  }
});

// --- painted behaviour ----------------------------------------------------------------------------------------------

test('16:9: the chip is up on frame 0, the title\'s words rise from 0.15 s, the wipe sweeps in from t_exit', async () => {
  const tl = load('aadhi_intro.landscape.json');
  const run = spyStage(tl);
  try {
    await run.seekTo(0, 0);
    const v = run.ctx(0).view;
    assert.equal(v.variant, 'fullframe');
    assert.equal(op(v.chip.el.style.opacity), 1);
    for (const w of v.titleRow.words) assert.equal(op(w.style.opacity), 0);
    assert.equal(v.wipe.style.visibility, 'hidden');
    const t1 = await run.seekTo(0, TITLE_AT + WORD_SECONDS / 2);
    assert.ok(op(v.titleRow.words[0].style.opacity) > 0 && op(v.titleRow.words[0].style.opacity) < 1, `t ${t1}`);
    const dy = /translate\(0, (\d+)px\)/.exec(v.titleRow.words[0].style.transform);
    assert.ok(dy && Number(dy[1]) > 0 && Number(dy[1]) < RISE);
    await run.seekTo(0, 2.0);
    for (const w of v.titleRow.words) assert.equal(op(w.style.opacity), 1);
    const exit = exitAt(intro(tl));
    const tw = await run.seekTo(0, exit + 0.2);
    assert.equal(v.wipe.style.visibility, 'visible');
    const m = /translate\((-?\d+)px, 0px\)/.exec(v.wipe.style.transform);
    assert.ok(m, v.wipe.style.transform);
    assert.equal(Number(m[1]), Math.round((wipeProgress(tw, exit) - 1) * 1920));
    assert.equal(v.wipe.style.background, rgb(intro(tl).look.refs.accent_next));
    await run.seekTo(0, intro(tl).duration - 1 / 30);
    assert.equal(v.wipe.style.visibility, 'visible');
  } finally {
    run.done();
  }
});

test('16:9 inks: the studio wall\'s on fullframe, the block\'s on the split panel, the background\'s on a card', async () => {
  const cases = [['aadhi_intro.landscape.json', 'wall'], ['aadhi_intro.landscape.light.json', 'panel'], ['aadhi_intro.landscape.card.json', 'bg']];
  for (const [name, where] of cases) {
    const tl = load(name);
    const run = spyStage(tl);
    try {
      await run.seekTo(0, 2);
      const ctx = run.ctx(0);
      const v = ctx.view;
      const look = intro(tl).look;
      const entries = aadhiIntro.texts ? aadhiIntro.texts(ctx) : [];
      const title = /** @type {any} */ (entries.find((e) => e.slot === 'title'));
      if (where === 'wall') {
        assert.equal(v.titleRow.el.style.color, rgb(look.presenter.wall_ink));
        assert.equal(title.bg, look.presenter.wall);
      } else if (where === 'panel') {
        assert.ok(v.panel, 'the split layout draws its colour panel');
        assert.equal(v.panel.style.background, rgb(ctx.roles.color('block.base')));
        assert.equal(title.ink, ctx.roles.color('block.ink'));
        assert.equal(title.bg, ctx.roles.color('block.base'));
      } else {
        assert.equal(title.ink, ctx.onBackground.ink);
        assert.equal(title.bg, ctx.onBackground.bg);
      }
      const chip = /** @type {any} */ (entries.find((e) => e.slot === 'chip'));
      assert.equal(chip.critical, false);
      assert.equal(chip.role, 'chip');
      assert.equal(chip.bg, ctx.roles.color('surface'));
      assert.equal(title.critical, true);
      assert.equal(title.alpha, 1);
    } finally {
      run.done();
    }
  }
});

test('16:9 hook: the question lands at its reveal (Aadhi\'s speech start), large in pip; the text fades under the wipe', async () => {
  const tl = load('aadhi_intro.landscape.hook.json');
  const s = intro(tl);
  const reveal = s.events.find((/** @type {any} */ e) => e.kind === 'reveal').t;
  assert.ok(reveal < HOOK_AT);
  const run = spyStage(tl);
  try {
    await run.seekTo(0, reveal - 0.05);
    const ctx = run.ctx(0);
    const v = ctx.view;
    assert.equal(v.variant, 'pip');
    for (const w of v.questionRow.words) assert.equal(op(w.style.opacity), 0);
    await run.seekTo(0, reveal + 1.5);
    for (const w of v.questionRow.words) assert.equal(op(w.style.opacity), 1);
    const q = /** @type {any} */ ((aadhiIntro.texts ? aadhiIntro.texts(ctx) : []).find((e) => e.slot === 'question'));
    assert.equal(q.alpha, 1);
    assert.equal(aadhiIntro.itemEl && aadhiIntro.itemEl(ctx, 'hook'), v.questionRow.el);
    assert.ok(v.questionRow.el.querySelector('.rt-k'), 'the hook keyword keeps its [[term]] span for cue.term');
    const exit = exitAt(s);
    await run.seekTo(0, exit + 0.3);
    const under = /** @type {any} */ ((aadhiIntro.texts ? aadhiIntro.texts(ctx) : []).find((e) => e.slot === 'question'));
    assert.ok(under.alpha < 1 && under.alpha > 0, `alpha ${under.alpha}`);
  } finally {
    run.done();
  }
});

test('9:16: the chip and the headline are fully set on frame 0 (the cover); a hook takes the band with the title above', async () => {
  for (const name of ['aadhi_intro.portrait.json', 'aadhi_intro.portrait.hook.json']) {
    const tl = load(name);
    const run = spyStage(tl);
    try {
      await run.seekTo(0, 0);
      const ctx = run.ctx(0);
      const v = ctx.view;
      assert.equal(v.cover, true);
      assert.equal(v.wipe, null, '9:16 has no brand wipe (the card slides down; the next scene cross-fades)');
      const entries = aadhiIntro.texts ? aadhiIntro.texts(ctx) : [];
      const head = /** @type {any} */ (entries.find((e) => e.slot === 'headline'));
      assert.ok(head && head.alpha === 1 && head.critical, name);
      for (const r of [v.titleRow, v.questionRow].filter(Boolean)) for (const w of r.words) assert.equal(op(w.style.opacity), 1);
      if (name.includes('hook')) {
        const eyebrow = /** @type {any} */ (entries.find((e) => e.slot === 'eyebrow'));
        assert.ok(eyebrow, 'the title stays on screen above the hook (never dropped)');
        assert.equal(eyebrow.role, 'label');
        assert.equal(head.el, v.questionRow.el);
      } else {
        assert.equal(entries.find((e) => e.slot === 'eyebrow'), undefined);
        assert.equal(head.el, v.titleRow.el);
      }
      assert.equal(/** @type {any} */ (entries.find((e) => e.slot === 'chip')).critical, false);
    } finally {
      run.done();
    }
  }
});

test('a scene with another item is board_generic\'s whole (every hook handed on)', async () => {
  const tl = load('aadhi_intro.landscape.json');
  intro(tl).board.push({ id: 'extra', kind: 'bullet', text: 'Something else' });
  const run = spyStage(tl);
  try {
    await run.seekTo(0, 1);
    const ctx = run.ctx(0);
    assert.equal(ctx.view.aiFallback, true);
    assert.ok(ctx.root.querySelector('.dyn-board'), 'board_generic drew the scene');
  } finally {
    run.done();
  }
});

test('reduced motion (live): the words fade in place, no rise', async () => {
  const tl = load('aadhi_intro.landscape.card.json');
  const run = spyStage(tl, { reducedMotion: true, mode: 'live' });
  try {
    await run.seekTo(0, TITLE_AT + WORD_SECONDS / 2);
    const v = run.ctx(0).view;
    assert.equal(v.titleRow.words[0].style.transform, 'none');
  } finally {
    run.done();
  }
});

// --- the 9:16 headline's fit, on a fake text layout (step-5 review R5-5) ----------------------------------------------

/**
 * Install a fake text layout on HTMLElement (jsdom has none): a glyph is 0.55 em wide, words wrap greedily in the
 * holder's width (hook_question.test.js's model). Returns the restore function.
 */
function fakeLayout() {
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const keys = ['offsetHeight', 'offsetWidth', 'clientWidth'];
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
  const lineH = (el) => (Number.parseFloat(el.style.lineHeight || '') || 1.3) * fontSize(el);
  /** @param {Element} el */
  const boxW = (el) => {
    const holder = /** @type {HTMLElement | null} */ (el.closest('.dyn-ai-holder'));
    return holder ? Number.parseFloat(holder.style.width) || 0 : 0;
  };
  /** @param {HTMLElement} textEl @param {number} width */
  const lines = (textEl, width) => {
    const size = fontSize(textEl);
    let n = 1;
    let x = 0;
    for (const w of textEl.querySelectorAll('.dyn-word')) {
      const ww = (w.textContent || '').length * CW * size;
      const need = x > 0 ? x + CW * size + ww : ww;
      if (width > 0 && need > width && x > 0) {
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
      if (el.classList.contains('dyn-word')) return lineH(/** @type {HTMLElement} */ (el.closest('.dyn-ai-text, .dyn-ai-eyebrow') || el));
      if (el.classList.contains('dyn-ai-text') || el.classList.contains('dyn-ai-eyebrow')) return lines(el, boxW(el)) * lineH(el);
      return 0;
    },
  });
  Object.defineProperty(proto, 'offsetWidth', { configurable: true, get() { return (/** @type {HTMLElement} */ (this).textContent || '').length * CW * fontSize(this); } });
  Object.defineProperty(proto, 'clientWidth', { configurable: true, get() { return boxW(/** @type {HTMLElement} */ (this)); } });
  return () => {
    for (const [k, d] of saved) {
      if (d) Object.defineProperty(proto, /** @type {string} */ (k), /** @type {PropertyDescriptor} */ (d));
      else delete proto[/** @type {string} */ (k)];
    }
  };
}

test('9:16: a long session title is set whole in the headline, and one past its room is reported, never cut', async () => {
  const restore = fakeLayout();
  /** @param {string} title */
  const headline = async (title) => {
    const tl = load('aadhi_intro.portrait.json');
    const s = intro(tl);
    s.title = title;
    s.board = [{ id: 'title', kind: 'heading', text: title }];
    const run = spyStage(tl);
    try {
      await run.seekTo(0, 0);
      const ctx = run.ctx(0);
      const entry = /** @type {any} */ ((aadhiIntro.texts?.(ctx) || []).find((e) => e.slot === 'headline'));
      const words = ctx.view.titleRow.words.map((/** @type {HTMLElement} */ w) => w.textContent).join(' ');
      return { entry, words };
    } finally {
      run.done();
    }
  };
  try {
    // the showcase's title: four lines in the headline's range, every word on screen (the old 32-character cap set
    // "Ohm's Law - The Rule That Runs…")
    const showcase = "Ohm's Law - The Rule That Runs Every Circuit";
    const fit = await headline(showcase);
    assert.equal(fit.words, showcase);
    assert.equal(fit.entry.overflow, 0);
    assert.ok(fit.entry.size >= PORTRAIT.headline.min && fit.entry.size <= PORTRAIT.headline.max, String(fit.entry.size));
    // past the band's room at the minimum: every word still set, the overflow reported so preflight blocks the render
    const long = "Ohm's Law, Kirchhoff's Laws and the Rules That Quietly Run Every Circuit in Your Whole House";
    const over = await headline(long);
    assert.equal(over.words, long);
    assert.ok(over.entry.overflow > 0 && over.entry.critical, JSON.stringify({ overflow: over.entry.overflow }));
    assert.equal(over.entry.size, PORTRAIT.headline.min);
  } finally {
    restore();
  }
});
