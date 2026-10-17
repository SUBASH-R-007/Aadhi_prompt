// @ts-check
// templates/myth_fact.js (DYNAMIC_VIDEO_DESIGN §4.3 myth_fact, R-10, R-63; owner decision 3 of 2026-10-16; B2 plan
// §1.4 template contract, §1.5 cues and item states, §1.11 slots, §P): the four fixture timelines
// (tests/compose/dynamic/make_myth_fact_fixtures.py) through the harness's contract and order gates, the pure helpers
// (the scene's parts, drawsScene - the page side of the CG-2 rule, which templates.py `_fit_myth_fact` mirrors - the
// refutation's timing, the shrink held at R-29's floors, geometry, slots, TeX), the painted behaviour in both formats
// (the myth on its beat, the strike, the myth giving way, the fact sliding in as the final frame, the supporting lines,
// the panel, reduced motion), the cues the stage draws from the template's geometry, preflight's text entries and
// remedies, the board_generic fallback, and the fit, on a fake text layout (jsdom has none).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { GRID, Painter, REGIONS, TYPE_FLOORS, ZONES, fx, parseTarget, templateSlots } from '../../../js/dynamic/core.js';
import { TEMPLATES } from '../../../js/dynamic/stage.js';
import { termPieces } from '../../../js/dynamic/cues.js';
import { itemTimings } from '../../../js/dynamic/state.js';
import {
  PANEL_KINDS, PANEL_TERM, PANEL_TITLED_KINDS, STRIKE_DRAW,
} from '../../../js/dynamic/templates/_shared.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import mythFact, {
  BORDER_PX, CARD_ENTER, CARD_RISE, DIM, FACT_DELAY, FACT_ENTER, FACT_LAG, FACT_TAIL, FACT_TRAVEL, ID, LABELS, LAYOUT,
  LINE_HEIGHT, MYTH_READ, SHRINK, SHRINK_SECONDS, SIZE_STEP, SUPPORT_MAX, SUPPORT_SEPARATOR, boxes, cardTextWidth,
  drawsScene, eventAt, lineHeight, panelFor, refuteTimes, sceneParts, shrinkScale, slotRows, texOf,
} from '../../../js/dynamic/templates/myth_fact.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['myth_fact.landscape.json', 'myth_fact.landscape.tamil.json', 'myth_fact.portrait.json',
  'myth_fact.portrait.hindi.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { myth_fact: mythFact } };

/**
 * A render stage of `tl` with myth_fact through the stage's test seam, and the context each scene was built with.
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...mythFact, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return mythFact.build(ctx); } };
  const run = renderStage(tl, { templates: { myth_fact: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
  /** Seek scene `i` to the frame nearest scene time `t`; returns that frame's exact scene time. @param {number} i @param {number} t */
  const seekTo = async (i, t) => {
    const scene = tl.scenes[i];
    const f = Math.min(Math.round((Number(scene.start) + Number(scene.duration)) * tl.fps) - 1,
      Math.round((Number(scene.start) + t) * tl.fps));
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
/** The misconception item of a scene. @param {any} scene */
const mythOf = (scene) => scene.board.find((/** @type {any} */ b) => b.kind === 'misconception');
/** The personality's duration scale of a fixture. @param {any} tl */
const scaleOf = (tl) => Number(tl.presentation.motion_scale) || 1;
/** A scene with these board items (and optionally a panel / medium), as the stage receives it. */
const sceneWith = (/** @type {any[]} */ board, /** @type {any} */ extra = {}) => /** @type {any} */ ({ board, duration: 10, ...extra });
const MYTH = { id: 'm', kind: 'misconception', text: 'Current is used up', justification: 'The same current returns' };
const DEF = (/** @type {string} */ id) => ({ id, kind: 'definition', term: 'Voltage', text: 'the push' });

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, whole-px focus, terms, TeX, entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(mythFact, load(name)), [], name);
});

test('every fixture paints the same frame forward, in reverse and on a fresh page (a frame is a pure function of t)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const total = Math.round(tl.total_duration * tl.fps);
    // every 9th frame plus the frames around each event, each refutation and each crossfade's start
    const frames = new Set();
    for (let f = 0; f < total; f += 9) frames.add(f);
    for (const s of tl.scenes) {
      const at = (/** @type {number} */ t) => frames.add(Math.min(total - 1, Math.max(0, Math.round((s.start + t) * tl.fps))));
      for (const e of s.events || []) for (const d of [0, 0.2, 0.5]) at(e.t + d);
      const m = mythOf(s);
      const times = refuteTimes(s, m.id, enterOf(s, m.id), scaleOf(tl));
      for (const t of [times.strike, times.strike + 0.15, times.shrink + 0.1, times.fact, times.fact + 0.25]) at(t);
      at(3 / tl.fps);
    }
    assert.deepEqual(await orderProblems(tl, [...frames].sort((a, b) => a - b), SEAM), [], name);
  }
});

test('the fixtures cover what the B2 plan §G asks: both formats, dark and light, both entrances, every case', () => {
  const seen = {
    formats: new Set(), modes: new Set(), entrances: new Set(), events: new Set(), scripts: new Set(), panels: new Set(),
    supports: new Set(), headings: 0,
  };
  for (const name of FIXTURES) {
    const tl = load(name);
    seen.formats.add(tl.presentation.format);
    seen.modes.add(tl.presentation.mode);
    seen.entrances.add(tl.presentation.text_entrance);
    for (const s of tl.scenes) {
      assert.equal(s.look.template, ID);
      assert.ok(drawsScene(s, tl.presentation.format), `${name} ${s.scene_id}: drawn by myth_fact itself`);
      const parts = sceneParts(s);
      seen.supports.add(parts.supports.length);
      if (parts.heading) seen.headings += 1;
      if (s.side_panel) seen.panels.add(s.side_panel.panel.kind);
      for (const e of s.events || []) seen.events.add(`${e.kind}:${parseTarget(e.target)?.form}`);
      const text = (s.board || []).map((/** @type {any} */ b) => `${b.term || ''} ${b.text || ''} ${b.justification || ''}`).join(' ');
      if (/\p{Script=Tamil}/u.test(text)) seen.scripts.add('tamil');
      if (/\p{Script=Devanagari}/u.test(text)) seen.scripts.add('devanagari');
      if (/\$[^$]+\$/.test(text)) seen.scripts.add('tex');
      if (/[A-Za-z]{40,}/.test(text)) seen.scripts.add('long-word');
      if (/(?:\b[A-Z]{4,}\b.*){5}/.test(text)) seen.scripts.add('wide-caps');
    }
  }
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.entrances].sort(), ['baseline_reveal', 'fade_rise']);
  assert.deepEqual([...seen.events].sort(), ['answer:item', 'cue_term:item', 'cue_term:item:term', 'draw:item', 'look_here:item', 'look_here:panel']);
  assert.deepEqual([...seen.scripts].sort(), ['devanagari', 'long-word', 'tamil', 'tex', 'wide-caps']);
  assert.deepEqual([...seen.panels].sort(), ['image', 'terminal']);
  assert.deepEqual([...seen.supports].sort(), [0, 1, 2], 'owner decision 3: none, one and two supporting definitions');
  assert.ok(seen.headings > 0, 'a heading');
});

test('the showcase fixture is the showcase lecture\'s s5: two definitions, then the misconception, beside a picture', () => {
  const s = load('myth_fact.landscape.json').scenes[0];
  assert.equal(s.title, 'VOLTAGE AND CURRENT');
  assert.deepEqual(s.board.map((/** @type {any} */ b) => b.kind), ['definition', 'definition', 'misconception']);
  assert.equal(s.side_panel.panel.kind, 'image');
  const parts = sceneParts(s);
  assert.deepEqual(parts.supports.map((b) => b.id), ['d1', 'd2']);
  assert.equal(parts.myth?.id, 'm1');
});

// --- pure helpers ---------------------------------------------------------------------------------------------------

test('sceneParts: the one misconception with its correction, the definitions, the first heading and everything else', () => {
  const parts = sceneParts(sceneWith([
    { id: 'h', kind: 'heading', text: 'Title' }, DEF('a'), MYTH, { id: 'h2', kind: 'heading', text: 'Again' },
    { id: 'm2', kind: 'misconception', text: 'Second', justification: 'Yes' }, { id: 'b', kind: 'bullet', text: 'x' },
  ]));
  assert.equal(parts.myth?.id, 'm');
  assert.equal(parts.heading?.id, 'h');
  assert.deepEqual(parts.supports.map((b) => b.id), ['a']);
  assert.deepEqual(parts.other.map((b) => b.id), ['h2', 'm2', 'b'], 'a second heading or misconception, any other kind');
  // a misconception without its correction is not the refutation's data (§4.3: text + justification)
  for (const justification of [null, '', '   ']) {
    const p = sceneParts(sceneWith([{ ...MYTH, justification }]));
    assert.equal(p.myth, null, String(justification));
    assert.deepEqual(p.other.map((b) => b.id), ['m']);
  }
  assert.deepEqual(sceneParts(sceneWith([])), { myth: null, supports: [], heading: null, other: [] });
});

test('drawsScene: the page\'s side of CG-2 (templates.py _fit_myth_fact refuses at least these): shape, format, panel, medium', () => {
  const panelled = (/** @type {any} */ scene, /** @type {any} */ panel) => ({ ...scene, side_panel: { panel, media: null, show_at: 0 } });
  // the misconception alone, in both formats
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) assert.equal(drawsScene(sceneWith([MYTH]), fmt), true, fmt);
  // nothing to refute
  assert.equal(drawsScene(sceneWith([]), 'landscape'), false);
  assert.equal(drawsScene(sceneWith([{ ...MYTH, justification: '' }]), 'landscape'), false, 'no correction');
  assert.equal(drawsScene(sceneWith([MYTH, { ...MYTH, id: 'm2' }]), 'landscape'), false, 'two misconceptions');
  // owner decision 3: definitions as supporting lines, at most SUPPORT_MAX (16:9 two, 9:16 none)
  assert.deepEqual({ ...SUPPORT_MAX }, { landscape: 2, portrait: 0 });
  assert.equal(drawsScene(sceneWith([DEF('a'), DEF('b'), MYTH]), 'landscape'), true, 's5');
  assert.equal(drawsScene(sceneWith([DEF('a'), DEF('b'), DEF('c'), MYTH]), 'landscape'), false, 'three definitions');
  assert.equal(drawsScene(sceneWith([DEF('a'), MYTH]), 'portrait'), false, 'a reel holds the two cards alone');
  // one heading, 16:9 only; any other kind is board_generic's
  const heading = { id: 'h', kind: 'heading', text: 'T' };
  assert.equal(drawsScene(sceneWith([heading, MYTH]), 'landscape'), true);
  assert.equal(drawsScene(sceneWith([heading, MYTH]), 'portrait'), false);
  assert.equal(drawsScene(sceneWith([heading, { ...heading, id: 'h2' }, MYTH]), 'landscape'), false);
  for (const kind of ['bullet', 'paragraph', 'takeaway', 'callout_warning', 'question', 'figure', 'formula']) {
    assert.equal(drawsScene(sceneWith([MYTH, { id: 'x', kind, text: 'x', latex: 'x', figure_id: 'f' }]), 'landscape'), false, kind);
  }
  // a scene medium has no place here
  assert.equal(drawsScene(sceneWith([MYTH], { media: { kind: 'image', url: '/x.png' } }), 'landscape'), false);
  // 16:9 draws the panel kinds _shared.js draws; 9:16 none
  for (const kind of PANEL_KINDS) {
    const panel = PANEL_TITLED_KINDS.includes(kind) ? { kind, title: 'A plot' } : { kind, title: 'P', terminal: { command: 'ls' } };
    assert.equal(drawsScene(panelled(sceneWith([DEF('a'), DEF('b'), MYTH]), panel), 'landscape'), true, kind);
    assert.equal(drawsScene(panelled(sceneWith([MYTH]), panel), 'portrait'), false, `${kind} in 9:16`);
  }
  for (const kind of ['skill_tree', 'quiz']) assert.equal(drawsScene(panelled(sceneWith([MYTH]), { kind }), 'landscape'), false, kind);
  for (const kind of PANEL_TITLED_KINDS) assert.equal(drawsScene(panelled(sceneWith([MYTH]), { kind }), 'landscape'), false, `${kind} untitled`);
  // a hidden panel is not drawn, so it never sends the scene away
  assert.equal(drawsScene({ ...panelled(sceneWith([MYTH]), { kind: 'skill_tree' }), layout: { show_side_panel: false } }, 'portrait'), true);
});

test('a scene this template does not draw is drawn by board_generic whole, every hook handed on', async () => {
  for (const name of ['myth_fact.landscape.json', 'myth_fact.portrait.json']) {
    const tl = load(name);
    const scene = tl.scenes[0];
    scene.board = [...scene.board, { id: 'x1', kind: 'bullet', text: 'A bullet this template has no place for' }];
    const run = spyStage(tl);
    await run.seekTo(0, 1);
    assert.ok(run.stage.el.querySelector('.dyn-board'), `${name}: board_generic drew it`);
    assert.equal(run.stage.el.querySelector('.dyn-mf'), null);
    const ctx = run.ctx(0);
    const fmt = tl.presentation.format;
    assert.deepEqual(mythFact.tex?.(scene, scene.look, fmt), boardGeneric.tex?.(scene, scene.look, fmt));
    const zones = ZONES[fmt];
    const fixes = { safe: false, slots: {} };
    assert.deepEqual(mythFact.mediaBox?.(scene, zones, 'render', 'panel', fixes), boardGeneric.mediaBox?.(scene, zones, 'render', 'panel', fixes));
    assert.deepEqual(mythFact.dimGroups?.(ctx), boardGeneric.dimGroups?.(ctx));
    assert.deepEqual(mythFact.itemEl?.(ctx, 'x1'), boardGeneric.itemEl?.(ctx, 'x1'));
    assert.ok(Array.isArray(mythFact.texts?.(ctx)));
    run.done();
  }
});

test('panelFor: the scene\'s panel in 16:9 (as _shared.js draws it), never in 9:16', () => {
  const scene = sceneWith([MYTH], { side_panel: { panel: { kind: 'image', title: 'P' }, media: { kind: 'image', url: '/p.png', width: 800, height: 600 }, show_at: 0 } });
  assert.equal(panelFor(scene, 'landscape', 'render')?.draw, 'media');
  assert.equal(panelFor(scene, 'portrait', 'render'), null);
  assert.equal(panelFor(sceneWith([MYTH]), 'landscape'), null);
});

test('eventAt: the earliest event of a kind on the item or one of its parts', () => {
  const scene = /** @type {any} */ ({ events: [
    { t: 3, kind: 'draw', target: 'm' }, { t: 2, kind: 'draw', target: 'm:term:0' }, { t: 1, kind: 'draw', target: 'mm' },
    { t: 0.5, kind: 'answer', target: 'x' }, { t: Number.NaN, kind: 'draw', target: 'm' }, { t: 4, kind: 'draw', target: null },
  ] });
  assert.equal(eventAt(scene, 'draw', 'm'), 2);
  assert.equal(eventAt(scene, 'answer', 'm'), null);
  assert.equal(eventAt(/** @type {any} */ ({}), 'draw', 'm'), null);
});

test('refuteTimes: a draw event, an answer event, the revealing beat\'s narration, FACT_DELAY; held between MYTH_READ and FACT_TAIL', () => {
  const beat = { beat_id: 'b', index: 0, start: 2, speech_end: 6, end: 6.5, narration: 'x', board_item_id: 'm' };
  const base = /** @type {any} */ ({ duration: 12, beats: [beat], events: [] });
  const k = 0.85;
  // the middle of the beat's speech: the belief is said first and its correction second
  let r = refuteTimes(base, 'm', 1.88, k);
  assert.equal(r.strike, 4);
  assert.ok(Math.abs(r.shrink - (4 + STRIKE_DRAW * k)) < 1e-9, 'the myth gives way as the strike completes');
  assert.ok(Math.abs(r.fact - (r.shrink + FACT_LAG * k)) < 1e-9, 'the fact FACT_LAG later');
  // never before MYTH_READ after the entrance; never later than FACT_TAIL before the end
  r = refuteTimes({ ...base, beats: [{ ...beat, speech_end: 2.4 }] }, 'm', 1.88, k);
  assert.ok(Math.abs(r.strike - (1.88 + MYTH_READ)) < 1e-9, 'a short beat: read first');
  r = refuteTimes({ ...base, duration: 4.5 }, 'm', 1.88, k);
  assert.ok(Math.abs(r.strike - (4.5 - FACT_TAIL)) < 1e-9, 'a short scene: the fact in before the end');
  // no beat reveals it: FACT_DELAY after the entrance
  r = refuteTimes({ ...base, beats: [] }, 'm', 0.35, k);
  assert.ok(Math.abs(r.strike - (0.35 + FACT_DELAY)) < 1e-9);
  // a draw event times the strike exactly (Python decides WHEN), but never before the card is in
  r = refuteTimes({ ...base, events: [{ t: 2.5, kind: 'draw', target: 'm' }] }, 'm', 1.88, k);
  assert.equal(r.strike, 2.5);
  r = refuteTimes({ ...base, events: [{ t: 1.9, kind: 'draw', target: 'm' }] }, 'm', 1.88, k);
  assert.ok(Math.abs(r.strike - (1.88 + CARD_ENTER * k)) < 1e-9, 'not before the card is in');
  // an answer event times the fact, and the strike before it
  r = refuteTimes({ ...base, events: [{ t: 5, kind: 'answer', target: 'm' }] }, 'm', 1.88, k);
  assert.equal(r.fact, 5);
  assert.ok(Math.abs(r.strike - (5 - (STRIKE_DRAW + FACT_LAG) * k)) < 1e-9);
  // both: the strike on its draw, the fact on its answer (never before the strike has drawn)
  r = refuteTimes({ ...base, events: [{ t: 3, kind: 'draw', target: 'm' }, { t: 6, kind: 'answer', target: 'm' }] }, 'm', 1.88, k);
  assert.deepEqual([r.strike, r.fact], [3, 6]);
  assert.ok(Math.abs(r.shrink - (6 - FACT_LAG * k)) < 1e-9, 'the myth gives way just before the fact comes');
  r = refuteTimes({ ...base, events: [{ t: 3, kind: 'draw', target: 'm' }, { t: 3.1, kind: 'answer', target: 'm' }] }, 'm', 1.88, k);
  assert.ok(Math.abs(r.fact - (3 + (STRIKE_DRAW + FACT_LAG) * k)) < 1e-9);
  // the personality's scale: a Soft refutation takes longer (the reading time does not scale)
  const soft = refuteTimes(base, 'm', 1.88, 1.15);
  assert.equal(soft.strike, 4, 'the beat decides the strike in every personality');
  assert.ok(Math.abs(soft.fact - soft.strike - (STRIKE_DRAW + FACT_LAG) * 1.15) < 1e-9);
});

test('shrinkScale: §4.3\'s 70%, held at R-29\'s floors so a struck card is set in legal type', () => {
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    const floors = TYPE_FLOORS[fmt];
    for (let size = L.myth.min; size <= L.myth.max; size += SIZE_STEP) {
      const s = shrinkScale(fmt, size);
      assert.ok(s >= SHRINK && s <= 1, `${fmt} ${size}: ${s}`);
      assert.ok(L.label * s >= floors.label_min, `${fmt}: the label at ${L.label * s} px`);
      assert.ok(size * s >= floors.text_min, `${fmt}: the belief at ${size * s} px`);
      assert.equal(Math.round(s * 10000), s * 10000, '4 decimals');
    }
  }
  assert.equal(shrinkScale('landscape', 52), 0.8334, 'the label floor holds it');
  assert.equal(shrinkScale('portrait', 44), 0.9091, 'the text floor holds it');
  assert.equal(shrinkScale('portrait', 52), 0.8462);
});

test('lineHeight: the type scale\'s values, the Indic minimums for Tamil and Devanagari (plan §2)', () => {
  assert.equal(lineHeight('body', 'Current is used up'), LINE_HEIGHT.body.latin);
  assert.equal(lineHeight('body', 'மின்னோட்டம் $I$'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('heading', 'आम गलतफहमी'), LINE_HEIGHT.heading.indic);
  const scale = CATALOGUE.type.scale;
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.body.indic, scale.body.line_height.indic[0], 'plan §2: Indic body >= 1.45');
  assert.equal(LINE_HEIGHT.heading.latin, scale.heading.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.heading.indic, scale.heading.line_height.indic[0]);
});

test('sizes keep R-29: nothing below the floors, the fact larger than the belief, 9:16 text at <= 64 px at least 600', () => {
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    const floors = TYPE_FLOORS[fmt];
    for (const r of [L.myth, L.fact, L.support, L.heading].filter(Boolean)) {
      assert.ok(/** @type {any} */ (r).min >= floors.text_min, `${fmt} ${JSON.stringify(r)}`);
      assert.equal((/** @type {any} */ (r).max - /** @type {any} */ (r).min) % SIZE_STEP, 0, 'on the 4 px steps');
    }
    assert.ok(L.label >= floors.label_min);
    assert.ok(L.fact.min > L.myth.min && L.fact.max > L.myth.max, '§4.3: the fact card is larger');
  }
  assert.ok(LAYOUT.portrait.weight >= /** @type {number} */ (TYPE_FLOORS.portrait.heavy_min_weight));
  assert.ok(LAYOUT.portrait.fact.max <= /** @type {number} */ (TYPE_FLOORS.portrait.heavy_max_px));
  assert.equal(LAYOUT.landscape.heading?.min, 84, 'R-29 headings 84-120');
});

/**
 * preflight.py inside_bands: a box lies inside the union of the critical boxes, each box it overlaps holding its whole
 * width.
 * @param {{x: number, y: number, w: number, h: number}} r @param {{x0: number, y0: number, x1: number, y1: number}[]} bands
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

test('geometry: the regions and grid spans, the text inside the critical boxes, the cards inside the shapes area', () => {
  const land = boxes('landscape', ZONES.landscape, true);
  assert.deepEqual([land.band.x0, land.band.y0, land.band.x1, land.band.y1], [96, 112, 1824, 900], 'title top to body bottom');
  assert.deepEqual([land.heading.x0, land.heading.x1], [REGIONS.landscape['top-band'].x0, REGIONS.landscape['top-band'].x1]);
  assert.deepEqual([land.column.x0, land.column.x1], [96, 1034], 'left-55');
  assert.deepEqual([land.myth.x0, land.myth.x1], [96, 802], 'grid columns 1-5');
  assert.deepEqual([land.fact.x0, land.fact.x1], [826, 1824], 'grid columns 6-12');
  assert.equal(land.fact.x0 - land.myth.x1, GRID.landscape.gutter);
  assert.deepEqual([land.panel?.x0, land.panel?.x1], [1058, 1824], 'right-45 beside the lines');
  const alone = boxes('landscape', ZONES.landscape, false);
  assert.deepEqual([alone.panel?.x0, alone.panel?.x1], [96, 1824], 'across the grid box with no lines');
  const port = boxes('portrait', ZONES.portrait);
  assert.deepEqual([port.band.y0, port.band.y1], [340, 1020]);
  assert.equal(port.panel, null);
  assert.deepEqual([port.myth.x0, port.myth.x1], [192 - LAYOUT.portrait.pad - BORDER_PX, 780 + LAYOUT.portrait.pad]);
  assert.equal(cardTextWidth('portrait', port.myth), 588, 'the card\'s text is exactly the text column');
  assert.equal(cardTextWidth('landscape', land.myth), 706 - 2 * LAYOUT.landscape.pad - BORDER_PX);
  // text inside the critical boxes (R-59) in both formats; the 9:16 cards in the shapes area (x 60-1020)
  for (const y of [land.band.y0, land.band.y1 - 120]) {
    for (const b of [land.heading, land.column, land.myth, land.fact]) {
      assert.ok(insideBands({ x: b.x0, y, w: b.x1 - b.x0, h: 120 }, ZONES.landscape.critical), `16:9 x ${b.x0}-${b.x1} y ${y}`);
    }
  }
  const text = { x: port.myth.x0 + LAYOUT.portrait.pad + BORDER_PX, w: 588 };
  for (const y of [340, 600, 900]) assert.ok(insideBands({ ...text, y, h: 120 }, ZONES.portrait.critical), `9:16 y ${y}`);
  assert.ok(port.myth.x0 >= 60 && port.myth.x1 <= 1020, 'the 9:16 cards stay in the shapes area');
});

test('slotRows: the slot table templates.py mirrors', () => {
  const rows = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  assert.deepEqual(Object.keys(rows).sort(), ['fact', 'heading', 'label', 'myth', 'support', 'terminal']);
  assert.deepEqual(Object.keys(rows.heading), ['landscape'], '9:16 draws no heading');
  assert.deepEqual(Object.keys(rows.support), ['landscape'], '9:16 draws no supporting line');
  assert.deepEqual(Object.keys(rows.terminal), ['landscape'], '9:16 draws no panel');
  assert.equal(rows.heading.landscape.default.w, 1728);
  assert.equal(rows.support.landscape.default.w, 938);
  assert.equal(rows.support.landscape.default.weight, 700, 'measured at the bold term\'s weight: an upper bound');
  assert.equal(rows.myth.landscape.default.w, 652);
  assert.equal(rows.fact.landscape.default.w, (1824 - 826) - 2 * LAYOUT.landscape.pad - BORDER_PX, '944');
  assert.equal(rows.myth.portrait.default.w, 588);
  assert.equal(rows.fact.portrait.default.w, 588);
  assert.deepEqual(rows.myth.portrait.default.size, [LAYOUT.portrait.myth.min, LAYOUT.portrait.myth.max]);
  assert.deepEqual(rows.fact.landscape.default.size, [LAYOUT.landscape.fact.min, LAYOUT.landscape.fact.max]);
  assert.equal(rows.myth.portrait.default.weight, LAYOUT.portrait.weight);
  assert.deepEqual(rows.label.landscape.default.size, [LAYOUT.landscape.label, LAYOUT.landscape.label]);
  assert.equal(rows.label.portrait.default.max_lines, 1);
  assert.equal(rows.heading.landscape.default.prefer_lines, 1);
  assert.equal(rows.terminal.landscape.default.w, (1824 - 1058) - 2 * PANEL_TERM.landscape.pad);
  for (const slot of Object.keys(rows)) {
    for (const fmt of Object.keys(rows[slot])) assert.deepEqual(Object.keys(rows[slot][fmt]), ['default'], `${slot} ${fmt}: no variant`);
  }
});

test('the catalogue\'s myth_fact slots are slotRows exactly, and its consumes names the forms it draws, once registered', () => {
  const registered = Object.prototype.hasOwnProperty.call(TEMPLATES, ID);
  const spec = /** @type {any} */ (CATALOGUE.templates)[ID];
  const cat = /** @type {any} */ (templateSlots(ID));
  if (!registered) {
    // not yet in stage.js TEMPLATES: templates.py must not claim it either (test_implemented_matches_the_stage)
    assert.equal(spec.implemented, false, 'templates.py says implemented but stage.js does not register it');
    assert.deepEqual(cat, {}, 'slots belong with the registration');
    return;
  }
  assert.equal(spec.implemented, true);
  const want = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  assert.deepEqual(Object.keys(cat).sort(), Object.keys(want).sort());
  for (const slot of Object.keys(want)) {
    assert.deepEqual(Object.keys(cat[slot]).sort(), Object.keys(want[slot]).sort(), slot);
    for (const fmt of Object.keys(want[slot])) {
      const got = cat[slot][fmt].default;
      assert.deepEqual({ w: got.w, max_lines: got.max_lines, size: got.size, role: got.role, face: got.face, weight: got.weight, line_height: got.line_height, prefer_lines: got.prefer_lines },
        want[slot][fmt].default, `${slot} ${fmt}`);
    }
  }
  const forms = /** @type {any} */ (spec.consumes.forms);
  assert.deepEqual(forms, {
    reveal: ['item'], draw: ['item'], answer: ['item'], cue_term: ['item', 'item:term'],
    look_here: ['item', 'panel', 'panel:row'], output: ['panel'],
  });
  assert.equal(spec.push_ok, false, 'never a camera push (the outline form)');
});

test('texOf: the $...$ spans of the heading, each supporting term and meaning, the belief and the correction, inline', () => {
  const scene = sceneWith([{ id: 'h', kind: 'heading', text: 'Law $V$' }, { id: 'a', kind: 'definition', term: '$R$', text: 'Use $I = V/R$' },
    { id: 'm', kind: 'misconception', text: 'Bigger $R$ brighter', justification: 'At fixed $V$ it dims, a \\$5 lamp' }]);
  assert.deepEqual(texOf(scene).map((x) => x.tex), ['V', 'R', 'I = V/R', 'R', 'V']);
  assert.ok(texOf(scene).every((x) => x.display === false));
  assert.deepEqual(mythFact.tex?.(scene, null, 'landscape'), texOf(scene));
});

test('mediaBox: the panel\'s largest box for slot `panel` in 16:9 (right-45, or the grid box), none otherwise', () => {
  const fixes = { safe: false, slots: {} };
  const media = { kind: 'image', url: '/p.png', width: 800, height: 600 };
  const withLines = sceneWith([DEF('a'), MYTH], { side_panel: { panel: { kind: 'image', title: 'P' }, media, show_at: 0 } });
  assert.deepEqual(mythFact.mediaBox?.(withLines, ZONES.landscape, 'render', 'panel', fixes), { w: 1824 - 1058, h: 900 - 112 });
  const alone = sceneWith([MYTH], { side_panel: { panel: { kind: 'image', title: 'P' }, media, show_at: 0 } });
  assert.deepEqual(mythFact.mediaBox?.(alone, ZONES.landscape, 'render', 'panel', fixes), { w: 1728, h: 900 - 112 });
  assert.equal(mythFact.mediaBox?.(withLines, ZONES.landscape, 'render', 'main', fixes), null, 'only the panel slot');
  assert.equal(mythFact.mediaBox?.(sceneWith([MYTH]), ZONES.landscape, 'render', 'panel', fixes), null);
  const terminal = sceneWith([MYTH], { side_panel: { panel: { kind: 'terminal', terminal: { command: 'ls' } }, media: null, show_at: 0 } });
  assert.equal(mythFact.mediaBox?.(terminal, ZONES.landscape, 'render', 'panel', fixes), null, 'a terminal asks for no medium');
  // 9:16 hands a panelled scene to board_generic, whose box it then reports (mediaPlan and ctx.media agree)
  assert.deepEqual(mythFact.mediaBox?.(alone, ZONES.portrait, 'render', 'panel', fixes),
    boardGeneric.mediaBox?.(alone, ZONES.portrait, 'render', 'panel', fixes));
});

// --- painted behaviour ----------------------------------------------------------------------------------------------

test('16:9: the myth enters on its beat, centred on the grid; struck; pushed aside, shrunk and dimmed; the fact slides in', async () => {
  const tl = load('myth_fact.landscape.json'); // Crisp x0.85
  const k = scaleOf(tl);
  const scene = tl.scenes[0];
  const s = spyStage(tl);
  const enter = enterOf(scene, 'm1');
  assert.ok(Math.abs(enter - (scene.beats[2].start - 0.12)) < 1e-9, 'R-17: 120 ms before its beat');
  const times = refuteTimes(scene, 'm1', enter, k);
  await s.seekTo(0, enter - 0.1);
  const v = s.ctx(0).view;
  assert.equal(v.myth.card.style.visibility, 'hidden', 'not yet in');
  // in, unstruck, centred on the grid (translated right of its column)
  await s.seekTo(0, enter + CARD_ENTER * k + 0.1);
  assert.equal(op(v.myth.card.style.opacity), 1);
  assert.equal(v.myth.dx, 511, 'the card\'s centre (x 449) on the grid\'s (x 960)');
  assert.equal(v.myth.card.style.transform, `translate(${v.myth.dx}px, 0px)`);
  assert.equal(v.myth.text.style.color, rgb(v.ink), 'not struck yet');
  assert.equal(v.myth.parts.strike.style.visibility, 'hidden');
  assert.equal(v.fact.card.style.visibility, 'hidden', 'no fact before the refutation');
  // the strike draws (the struck state: ink2 and the warn line), the card still where it was
  await s.seekTo(0, times.strike + 0.1);
  assert.equal(v.myth.text.style.color, rgb(v.ink2));
  assert.equal(v.myth.parts.strike.style.visibility, 'visible');
  assert.equal(v.myth.card.style.transform, `translate(${v.myth.dx}px, 0px)`);
  // the end: the myth at its column, shrunk and dimmed; the fact in, unmoved, at full opacity (R-63: the final frame)
  await s.seekTo(0, scene.duration - 0.05);
  assert.equal(v.myth.card.style.transform, `translate(0px, 0px) scale(${fx(v.scale, 4)})`);
  assert.equal(v.scale, shrinkScale('landscape', v.myth.size));
  assert.equal(op(v.myth.card.style.opacity), DIM);
  assert.equal(op(v.fact.card.style.opacity), 1);
  assert.equal(v.fact.card.style.transform, 'none');
  assert.equal(v.fact.card.style.visibility, 'visible');
  // in between: the fact arrives from the right, FACT_LAG after the myth starts to give way
  await s.seekTo(0, times.fact + 0.08);
  const m = /translate\((\d+)px, 0px\)/.exec(v.fact.card.style.transform);
  assert.ok(m && Number(m[1]) > 0 && Number(m[1]) < FACT_TRAVEL.landscape.x, v.fact.card.style.transform);
  s.done();
});

test('9:16: the myth stands centred in the band, then is pushed up and shrunk about its top centre; the fact rises in', async () => {
  const tl = load('myth_fact.portrait.json'); // Soft x1.15, the fact on its answer event
  const k = scaleOf(tl);
  const scene = tl.scenes[0];
  const s = spyStage(tl);
  const enter = enterOf(scene, 'r1');
  const times = refuteTimes(scene, 'r1', enter, k);
  assert.equal(times.fact, eventAt(scene, 'answer', 'r1'), 'the answer event times the fact');
  await s.seekTo(0, times.strike - 0.1);
  const v = s.ctx(0).view;
  assert.equal(v.myth.card.style.transformOrigin, '50% 0%');
  assert.equal(v.myth.card.style.transform, `translate(0px, ${v.myth.dy}px)`, 'centred in the band');
  await s.seekTo(0, scene.duration - 0.05);
  assert.equal(v.myth.card.style.transform, `translate(0px, 0px) scale(${fx(v.scale, 4)})`, 'pushed up to the band\'s top');
  assert.equal(v.fact.card.style.transform, 'none');
  await s.seekTo(0, times.fact + 0.08);
  const m = /translate\(0px, (\d+)px\)/.exec(v.fact.card.style.transform);
  assert.ok(m && Number(m[1]) > 0 && Number(m[1]) < FACT_TRAVEL.portrait.y, `rising from below: ${v.fact.card.style.transform}`);
  s.done();
});

test('the supporting lines enter on their own beats with the personality\'s entrance; the panel beside them', async () => {
  const tl = load('myth_fact.landscape.json');
  const scene = tl.scenes[0];
  const s = spyStage(tl);
  await s.seekTo(0, enterOf(scene, 'd2') - 0.05);
  const v = s.ctx(0).view;
  assert.equal(v.lines.length, 2);
  assert.equal(v.lines[0].text.style.opacity, '1', 'd1 is in');
  assert.equal(v.lines[1].box.style.visibility, 'hidden', 'd2 not yet');
  assert.ok(v.lines[0].mask, 'Crisp: revealed from the baseline inside a mask');
  // "term — meaning", the term bold, its own field
  assert.equal(v.lines[0].fields.length, 2);
  assert.equal(v.lines[0].fields[0].style.fontWeight, '700');
  assert.ok(v.lines[0].text.textContent.includes(SUPPORT_SEPARATOR.trim()));
  assert.ok(v.panel && v.panel.view, 'the picture panel');
  s.done();
});

test('reduced motion (live player): nothing travels - the myth at its final place from the start, the fact fades in place', async () => {
  for (const name of ['myth_fact.landscape.json', 'myth_fact.portrait.json']) {
    const tl = load(name);
    const scene = tl.scenes[0];
    const s = spyStage(tl, { reducedMotion: true, mode: 'live' });
    const m = mythOf(scene);
    const times = refuteTimes(scene, m.id, enterOf(scene, m.id), scaleOf(tl));
    await s.seekTo(0, enterOf(scene, m.id) + 0.1);
    const v = s.ctx(0).view;
    assert.equal(v.myth.card.style.transform, 'none', `${name}: no rise, no offset`);
    await s.seekTo(0, times.fact + 0.1);
    assert.match(v.fact.card.style.transform, /^none$/, `${name}: the fact fades in place`);
    assert.ok(op(v.fact.card.style.opacity) > 0 && op(v.fact.card.style.opacity) < 1);
    assert.match(v.myth.card.style.transform, /^translate\(0px, 0px\) scale\(0\.\d+\)$/, `${name}: shrinks in place`);
    s.done();
  }
});

test('cue.term: itemEl hands the stage each item\'s fields in order (the belief, then the correction), [[term]] spans kept', async () => {
  const tl = load('myth_fact.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(0, 6);
  const ctx = s.ctx(0);
  const v = ctx.view;
  assert.deepEqual(mythFact.itemEl?.(ctx, 'm1'), [v.myth.text, v.fact.text]);
  assert.deepEqual(mythFact.itemEl?.(ctx, 'd1'), v.lines[0].fields);
  assert.equal(mythFact.itemEl?.(ctx, 'nope'), null);
  /** @param {string} target */
  const cue = (target) => ({ target, parsed: parseTarget(target) });
  const correction = termPieces(mythFact, ctx, /** @type {any} */ (cue('m1:term:0')));
  assert.ok(correction.length > 0 && correction.every((p) => v.fact.text.contains(p.span)), 'the correction\'s term (index 0: the belief has none)');
  assert.equal(correction.map((p) => p.span.textContent).join(''), 'charge is conserved');
  const meaning = termPieces(mythFact, ctx, /** @type {any} */ (cue('d1')));
  assert.equal(meaning.map((p) => p.span.textContent).join(''), 'pressure');
  s.done();
  // a heading's field
  const ta = load('myth_fact.landscape.tamil.json');
  const t = spyStage(ta);
  await t.seekTo(0, 2);
  assert.equal(mythFact.itemEl?.(t.ctx(0), 'h1'), t.ctx(0).view.head.text);
  t.done();
});

test('focusRect: the myth card before the refutation and the fact card after it (shapes), a line\'s text, the panel; whole px', async () => {
  const tl = load('myth_fact.landscape.json');
  const scene = tl.scenes[0];
  const s = spyStage(tl);
  const times = refuteTimes(scene, 'm1', enterOf(scene, 'm1'), scaleOf(tl));
  await s.seekTo(0, times.strike - 0.2);
  const ctx = s.ctx(0);
  const v = ctx.view;
  const rect = { x: 100.4, y: 500.6, w: 600.2, h: 200.4 };
  const stub = { ...ctx, rectOf: (/** @type {Element} */ el) => (el ? rect : null) };
  let r = mythFact.focusRect?.(stub, 'm1');
  assert.deepEqual(r, { rect: { x: 100, y: 501, w: 601, h: 200 }, shape: 'rect', radius: 24, el: v.myth.el, kind: 'shape' });
  assert.equal(mythFact.focusRect?.(stub, 'm1:term:0')?.el, v.myth.el);
  await s.seekTo(0, scene.duration - 0.05);
  r = mythFact.focusRect?.(stub, 'm1');
  assert.equal(r?.el, v.fact.el, 'the fact card once it is in');
  r = mythFact.focusRect?.(stub, 'd2');
  assert.equal(r?.kind, 'text');
  assert.equal(r?.el, v.lines[1].el);
  r = mythFact.focusRect?.(stub, 'panel');
  assert.equal(r?.el, v.panel.el);
  assert.equal(r?.kind, 'shape');
  assert.equal(mythFact.focusRect?.(stub, 'panel:row:0'), null, 'a picture panel has no rows');
  assert.equal(mythFact.focusRect?.(stub, 'nope'), null);
  assert.equal(mythFact.focusRect?.(stub, 'm1:var:x'), null);
  s.done();
});

test('dimGroups: the heading, every supporting line, both cards and the panel; the same elements at every t', async () => {
  const tl = load('myth_fact.landscape.tamil.json');
  const s = spyStage(tl);
  await s.seekTo(0, 1);
  const ctx = s.ctx(0);
  const v = ctx.view;
  const groups = /** @type {Element[]} */ (mythFact.dimGroups?.(ctx));
  assert.deepEqual(groups, [v.head.el, v.lines[0].el, v.myth.el, v.fact.el]);
  await s.seekTo(0, 5);
  assert.deepEqual(mythFact.dimGroups?.(ctx), groups);
  for (const g of groups) assert.equal(/** @type {HTMLElement} */ (g).style.opacity, '', 'the stage owns a group\'s opacity');
  await s.seekTo(1, 2);
  const panelled = s.ctx(1);
  assert.equal(mythFact.dimGroups?.(panelled)?.at(-1), panelled.view.panel.el);
  s.done();
});

test('texts(): every text with its slot, ink and background; the struck myth dimmed (not critical) at its shrunk size', async () => {
  const tl = load('myth_fact.landscape.json');
  const scene = tl.scenes[0];
  const s = spyStage(tl);
  await s.seekTo(0, 3.3);
  const ctx = s.ctx(0);
  const v = ctx.view;
  let entries = /** @type {any[]} */ (mythFact.texts?.(ctx));
  assert.deepEqual(entries.map((e) => e.slot), ['support', 'support', 'label', 'myth', 'label', 'fact']);
  const support = entries[0];
  assert.equal(support.bg, ctx.onBackground.bg, 'a supporting line is on the background');
  assert.equal(support.ink, ctx.onBackground.ink);
  const label = entries[2];
  assert.equal(label.ink, ctx.roles.color('warn'));
  assert.equal(label.bg, ctx.roles.color('surface'));
  assert.equal(label.text, LABELS.myth);
  assert.equal(entries[4].ink, ctx.roles.color('ok'));
  assert.equal(entries[5].alpha, 0, 'no fact yet');
  // the end: the myth struck (ink2), dimmed (alpha DIM: the stage reports it as not critical), its sizes the shrunk ones
  await s.seekTo(0, scene.duration - 0.05);
  entries = /** @type {any[]} */ (mythFact.texts?.(ctx));
  const myth = entries.find((e) => e.slot === 'myth');
  assert.equal(myth.ink, ctx.roles.color('ink2'));
  assert.ok(Math.abs(myth.alpha - DIM) < 1e-9);
  assert.ok(Math.abs(myth.size - v.myth.size * v.scale) < 1e-9);
  const mythLabel = entries.filter((e) => e.slot === 'label')[0];
  assert.ok(mythLabel.size >= TYPE_FLOORS.landscape.label_min, `the shrunk label at ${mythLabel.size} px keeps R-29`);
  const fact = entries.find((e) => e.slot === 'fact');
  assert.equal(fact.alpha, 1);
  assert.equal(fact.ink, ctx.roles.color('ink'));
  assert.equal(fact.size, v.fact.size);
  s.done();
  // a terminal panel's text in the `terminal` slot
  const ta = load('myth_fact.landscape.tamil.json');
  const t = spyStage(ta);
  await t.seekTo(1, 3);
  const term = /** @type {any[]} */ (mythFact.texts?.(t.ctx(1))).find((e) => e.slot === 'terminal');
  assert.ok(term && term.critical && term.weight === PANEL_TERM.landscape.weight);
  t.done();
});

test('preflight\'s remedies: a slot\'s size cap and the safe layout hold the text small; the scrim goes behind background text', async () => {
  const tl = load('myth_fact.landscape.tamil.json');
  tl.scenes[0].look.fixes = { safe: false, slots: { fact: { size: 52 }, heading: { scrim: true }, support: { scrim: true } } };
  let s = spyStage(tl);
  await s.seekTo(0, 2);
  let v = s.ctx(0).view;
  assert.equal(v.fact.size, 52, 'the cap (jsdom fits at the largest size allowed)');
  assert.ok(v.head.scrim && v.lines[0].scrim);
  assert.notEqual(v.head.text.style.background, '', 'the scrim behind the heading');
  assert.notEqual(v.lines[0].text.style.background, '', 'and behind the supporting line');
  const entries = /** @type {any[]} */ (mythFact.texts?.(s.ctx(0)));
  const head = entries.find((e) => e.slot === 'heading');
  assert.notEqual(head.bg, s.ctx(0).onBackground.bg, 'reported on the scrim, composited');
  s.done();
  const safe = load('myth_fact.portrait.json');
  safe.scenes[0].look.fixes = { safe: true, slots: {} };
  s = spyStage(safe);
  await s.seekTo(0, 2);
  v = s.ctx(0).view;
  assert.deepEqual([v.myth.size, v.fact.size], [LAYOUT.portrait.myth.min, LAYOUT.portrait.fact.min], 'safe: every slot at its minimum');
  s.done();
});

test('update writes every property on every frame with a value (no \'\': a cleared property would make the DOM order-dependent)', async () => {
  const proto = /** @type {any} */ (Painter.prototype);
  const real = proto.style;
  /** @type {string[]} */
  const empty = [];
  let inside = false;
  const wrapped = { ...mythFact, update(/** @type {any} */ ctx, /** @type {number} */ t, /** @type {any} */ st) { inside = true; try { mythFact.update(ctx, t, st); } finally { inside = false; } } };
  proto.style = function style(/** @type {any} */ el, /** @type {string} */ prop, /** @type {any} */ value) {
    if (inside && String(value) === '') empty.push(`${el.className} ${prop}`);
    return real.call(this, el, prop, value);
  };
  try {
    for (const name of FIXTURES) {
      const tl = load(name);
      const { stage, done } = renderStage(tl, { templates: { myth_fact: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex });
      const total = Math.round(tl.total_duration * tl.fps);
      for (let f = 0; f < total; f += 7) await stage.seek(f);
      done();
    }
  } finally {
    proto.style = real;
  }
  assert.deepEqual([...new Set(empty)], []);
});

test('layout lifts the myth card\'s painted transform while it measures the strike, and puts the same value back', async () => {
  const tl = load('myth_fact.landscape.json');
  const s = spyStage(tl);
  await s.seekTo(0, tl.scenes[0].duration - 0.05);
  const ctx = s.ctx(0);
  const card = ctx.view.myth.card;
  const before = card.getAttribute('style');
  /** @type {string[]} */
  const seen = [];
  assert.notEqual(card.style.transform, 'none', 'painted: shrunk');
  // what layoutStrike sees: the card untransformed
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const real = proto.getBoundingClientRect;
  proto.getBoundingClientRect = function spy() {
    if (this === ctx.view.myth.inline) seen.push(card.style.transform);
    return real.call(this);
  };
  try {
    mythFact.layout?.(ctx);
  } finally {
    proto.getBoundingClientRect = real;
  }
  assert.ok(seen.length > 0 && seen.every((x) => x === 'none'), JSON.stringify(seen));
  assert.equal(card.getAttribute('style'), before, 'the same style, in the same order');
  s.done();
});

// --- the fit, on a fake text layout ---------------------------------------------------------------------------------

/**
 * Install a fake text layout on HTMLElement (jsdom has none): a glyph is 0.55 em wide, words wrap greedily, a word
 * wider than its line breaks across lines. Enough for this template's fit: the height of each text element, of the
 * mask that holds it and of a card (its padding, its label row, the gap and its statement), a word's size, the text
 * box's width. Returns the restore function.
 */
function fakeLayout() {
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const keys = ['offsetHeight', 'offsetWidth', 'clientWidth', 'offsetTop'];
  const saved = keys.map((k) => [k, Object.getOwnPropertyDescriptor(proto, k)]);
  const CW = 0.55;
  const TEXT = '.dyn-mf-heading, .dyn-mf-support, .dyn-mf-statement';
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
  /** @param {Element} el */
  const formatOf = (el) => (el.closest('.dyn-mf--portrait') ? 'portrait' : 'landscape');
  /** The width a text element wraps in. @param {Element} el */
  const boxOf = (el) => {
    const card = /** @type {HTMLElement | null} */ (el.closest('.dyn-mf-card'));
    if (card) return (Number.parseFloat(card.style.width) || 0) - 2 * LAYOUT[formatOf(el)].pad - BORDER_PX;
    const g = /** @type {HTMLElement | null} */ (el.closest('.dyn-group'));
    return g ? Number.parseFloat(g.style.width) || 0 : 0;
  };
  /** @param {HTMLElement} textEl @param {number} boxW */
  const lines = (textEl, boxW) => {
    const size = fontSize(textEl);
    let n = 1;
    let x = 0;
    for (const w of textEl.querySelectorAll('.dyn-word')) {
      const ww = (w.textContent || '').length * CW * size;
      if (boxW <= 0) continue;
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
  const textHeight = (el) => lines(el, boxOf(el)) * lineH(el);
  Object.defineProperty(proto, 'offsetHeight', {
    configurable: true,
    get() {
      const el = /** @type {HTMLElement} */ (this);
      if (el.classList.contains('dyn-word')) {
        const host = /** @type {HTMLElement | null} */ (el.closest(TEXT));
        if (!host) return 0;
        const ww = (el.textContent || '').length * CW * fontSize(el);
        const box = boxOf(el);
        return (box > 0 && ww > box ? Math.ceil(ww / box) : 1) * lineH(host);
      }
      if (el.matches(TEXT)) return textHeight(el);
      if (el.classList.contains('dyn-reveal-mask')) {
        const inner = /** @type {HTMLElement | null} */ (el.querySelector(TEXT));
        return inner ? textHeight(inner) : 0;
      }
      if (el.classList.contains('dyn-mf-card')) {
        const L = LAYOUT[formatOf(el)];
        const text = /** @type {HTMLElement} */ (el.querySelector('.dyn-mf-statement'));
        return 2 * L.pad + L.label * LINE_HEIGHT.label + L.labelGap + textHeight(text);
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

/** The top of an element as `layout` placed it. @param {HTMLElement} el */
const topOf = (el) => Number.parseFloat(el.style.top) || 0;

test('fit (fake layout): every text on a 4 px step in its range; the composition inside the band; nothing overflows', async () => {
  const restore = fakeLayout();
  try {
    for (const name of FIXTURES) {
      const tl = load(name);
      const fmt = /** @type {'landscape' | 'portrait'} */ (tl.presentation.format);
      const b = boxes(fmt, ZONES[fmt], true);
      for (let i = 0; i < tl.scenes.length; i++) {
        const s = spyStage(tl);
        await s.seekTo(i, tl.scenes[i].duration - 0.05);
        const ctx = s.ctx(i);
        const v = ctx.view;
        for (const part of [v.myth, v.fact, ...v.lines, ...(v.head ? [v.head] : [])]) {
          assert.ok(part.size >= part.range.min && part.size <= part.range.max, `${name} ${i}: ${part.size} in [${part.range.min}, ${part.range.max}]`);
        }
        const over = /** @type {any[]} */ (mythFact.texts?.(ctx)).filter((e) => Number(e.overflow) > 0);
        assert.deepEqual(over.map((e) => e.slot), [], `${name} ${i}: nothing overflows`);
        const bottom = Math.max(topOf(v.myth.card) + v.myth.height, topOf(v.fact.card) + v.fact.height);
        assert.ok(bottom <= b.band.y1, `${name} ${i}: the cards end inside the band (${bottom})`);
        if (fmt === 'portrait') {
          // the pair (the shrunk myth over the fact) centred in the band; before the refutation the myth alone,
          // centred, the fact then pushing it up to the pair's top (§4.3)
          const bandH = b.band.y1 - b.band.y0;
          const shrunk = Math.ceil(v.scale * v.myth.height);
          const pairTop = b.band.y0 + Math.max(0, Math.round((bandH - (shrunk + LAYOUT.portrait.gap + v.fact.height)) / 2));
          assert.equal(topOf(v.myth.card), pairTop);
          assert.equal(topOf(v.fact.card), pairTop + shrunk + LAYOUT.portrait.gap);
          assert.equal(pairTop + v.myth.dy, b.band.y0 + Math.max(0, Math.round((bandH - v.myth.height) / 2)));
          assert.ok(v.myth.dy > 0, `${name} ${i}: pushed UP by the fact`);
        }
        s.done();
      }
    }
  } finally {
    restore();
  }
});

test('fit (fake layout, 16:9): the lines under the heading, the panel beside them down to the row, the row on the band\'s bottom', async () => {
  const restore = fakeLayout();
  try {
    const tl = load('myth_fact.landscape.json');
    const s = spyStage(tl);
    await s.seekTo(0, 3);
    const v = s.ctx(0).view;
    const L = LAYOUT.landscape;
    assert.equal(topOf(v.lines[0].el), 112, 'no heading: the lines from the band\'s top');
    assert.equal(topOf(v.lines[1].el), 112 + v.lines[0].box.offsetHeight + L.supportGap);
    assert.equal(v.lines[0].size, v.lines[1].size, 'one size for every supporting line');
    const rowH = Math.max(v.myth.height, v.fact.height);
    assert.equal(v.rowTop, Math.max(topOf(v.lines[1].el) + v.lines[1].box.offsetHeight + L.rowGap, 900 - rowH));
    assert.equal(topOf(v.myth.card), v.rowTop);
    assert.equal(topOf(v.fact.card), v.rowTop);
    const frame = v.panel.box;
    assert.ok(frame.x0 >= 1058 && frame.x1 <= 1824, 'the panel in right-45');
    assert.ok(frame.y0 >= 112 && frame.y1 <= v.rowTop - L.rowGap, 'from the band\'s top down to the row');
    s.done();
    // a heading: across the top band on the band's top; with no panel, the lines and the row together centred under
    // it; a terminal panel with no lines spans the grid box
    const ta = load('myth_fact.landscape.tamil.json');
    const t = spyStage(ta);
    await t.seekTo(0, 3);
    const w = t.ctx(0).view;
    assert.equal(w.head.el.style.width, '1728px');
    assert.equal(topOf(w.head.el), 112);
    const under = 112 + w.head.box.offsetHeight + L.headingGap;
    const blockH = w.lines[0].box.offsetHeight + L.rowGap + Math.max(w.myth.height, w.fact.height);
    assert.equal(topOf(w.lines[0].el), Math.round(under + Math.max(0, Math.round((900 - under - blockH) / 2))));
    assert.equal(w.rowTop, Math.round(topOf(w.lines[0].el) + w.lines[0].box.offsetHeight + L.rowGap));
    assert.ok(topOf(w.lines[0].el) > under, 'centred: below the heading with room to spare');
    await t.seekTo(1, 3);
    const p = t.ctx(1).view;
    assert.ok(p.panel.box.x0 >= 96 && p.panel.box.x1 <= 1824 && p.panel.box.x1 - p.panel.box.x0 > 766, 'across the grid box');
    t.done();
  } finally {
    restore();
  }
});

test('fit (fake layout): too long - the supporting lines give way first, then the belief, the correction; the rest is reported', async () => {
  const restore = fakeLayout();
  try {
    const tl = load('myth_fact.landscape.json');
    const scene = tl.scenes[0];
    const long = 'a long supporting definition that keeps going and going for many words over many lines here';
    scene.board[0].text = long;
    scene.board[1].text = long;
    let s = spyStage(tl);
    await s.seekTo(0, 3);
    let v = s.ctx(0).view;
    assert.equal(v.lines[0].size, LAYOUT.landscape.support.min, 'the supporting lines gave way first');
    s.done();
    // far too long: everything at its minimum, the excess reported (preflight blocks with "Shorten text"), never clipped
    const huge = load('myth_fact.portrait.json');
    huge.scenes[0].board[0].justification = 'The same current flows back to the cell every single time. '.repeat(6);
    s = spyStage(huge);
    await s.seekTo(0, 4.9);
    v = s.ctx(0).view;
    assert.deepEqual([v.myth.size, v.fact.size], [LAYOUT.portrait.myth.min, LAYOUT.portrait.fact.min]);
    const over = /** @type {any[]} */ (mythFact.texts?.(s.ctx(0))).filter((e) => Number(e.overflow) > 0);
    assert.deepEqual(over.map((e) => e.slot), ['fact']);
    assert.ok(v.fact.text.textContent.length > 300, 'every word still there');
    s.done();
  } finally {
    restore();
  }
});
