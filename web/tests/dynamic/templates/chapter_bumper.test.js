// @ts-check
// templates/chapter_bumper.js (DYNAMIC_VIDEO_DESIGN §4.3 chapter_bumper, B2 plan §1.4 template contract, §1.6
// TemplateSpec, §1.11 slots, §P, deviation 14): the four fixture timelines
// (tests/compose/dynamic/make_chapter_bumper_fixtures.py) through the harness's contract and order gates, the pure
// helpers (what the scene shows, the boxes, the slot table, the roadmap nodes, TeX), the painted choreography in both
// formats (the numeral's slide, the kicker with it, the title 120 ms later with the personality's entrance, the
// roadmap's fade and the current chapter's node lighting, reduced motion), preflight's text entries and remedies, and
// the title's fit on a fake text layout (jsdom has none).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { Painter, TYPE_FLOORS, ZONES, fx, templateSlots } from '../../../js/dynamic/core.js';
import { TEXT_RISE } from '../../../js/dynamic/templates/_shared.js';
import chapterBumper, {
  LAYOUT, LIGHT, LIGHT_AT, LINE_HEIGHT, NUMERAL_ENTER, NUMERAL_FILL_ALPHA, NUMERAL_RISE, PIP_ALPHA, ROADMAP_DELAY,
  ROADMAP_ENTER, SIZE_STEP, TICK_ALPHA, TITLE_DELAY, boxes, bumperText, contentBox, drawsScene, lineHeight, pipRects,
  rgbaOf, roadmapOf, slotRows, texOf,
} from '../../../js/dynamic/templates/chapter_bumper.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['chapter_bumper.landscape.json', 'chapter_bumper.landscape.light.json',
  'chapter_bumper.portrait.json', 'chapter_bumper.portrait.hindi.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { chapter_bumper: chapterBumper } };

/**
 * A render stage of `tl` with chapter_bumper through the stage's test seam, and the context each scene was built
 * with (the template is registered in stage.js, but the seam keeps this test independent of that).
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...chapterBumper, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return chapterBumper.build(ctx); } };
  const run = renderStage(tl, { templates: { chapter_bumper: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
  /** Seek scene `i` to the frame nearest scene time `t`; returns that frame's exact scene time. @param {number} i @param {number} t */
  const seekTo = async (i, t) => {
    const scene = tl.scenes[i];
    const f = Math.round((Number(scene.start) + t) * tl.fps);
    await run.stage.seek(f);
    return f / tl.fps - Number(scene.start);
  };
  return { ...run, contexts, seekTo, ctx: (/** @type {number} */ i) => contexts.get(i) };
}

/** A hex colour as the DOM reports a painted one (`rgb(r, g, b)`). @param {string} hex */
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;
/** An opacity as a number ('' is 1: none painted). @param {string} v */
const op = (v) => (v === '' ? 1 : Number(v));
/** The personality's scale of a timeline (the fixtures store it; _shared.js `seconds`). @param {any} tl */
const scaleOf = (tl) => Number(tl.presentation.motion_scale) || 1;

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, TeX declared, text entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(chapterBumper, load(name)), [], name);
});

test('every fixture paints the same frame forward, in reverse and on a fresh page (a frame is a pure function of t)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const total = Math.round(tl.total_duration * tl.fps);
    const frames = new Set();
    for (let f = 0; f < total; f += 5) frames.add(f);
    // the frames around each scene's start (its crossfade) and around the choreography's moments
    for (const s of tl.scenes) {
      for (const d of [0, 0.1, 0.3, 0.5, 0.8, 1.1]) frames.add(Math.min(total - 1, Math.round((s.start + d) * tl.fps)));
    }
    assert.deepEqual(await orderProblems(tl, [...frames].sort((a, b) => a - b), SEAM), [], name);
  }
});

test('the fixtures cover both formats, a dark and a light family, both text entrances, every case the template draws', () => {
  const seen = { formats: new Set(), modes: new Set(), entrances: new Set(), cases: new Set(), scripts: new Set() };
  for (const name of FIXTURES) {
    const tl = load(name);
    seen.formats.add(tl.presentation.format);
    seen.modes.add(tl.presentation.mode);
    seen.entrances.add(tl.presentation.text_entrance);
    for (const s of tl.scenes) {
      const road = roadmapOf(s.look);
      const text = bumperText(s, road);
      seen.cases.add(text.number ? 'numeral' : 'no-numeral');
      seen.cases.add(road.length ? 'roadmap' : 'no-roadmap');
      if (text.kicker) seen.cases.add('kicker');
      if (/\p{Script=Tamil}/u.test(s.title)) seen.scripts.add('tamil');
      if (/\p{Script=Devanagari}/u.test(s.title)) seen.scripts.add('devanagari');
      if (/(?:\b[A-Z]{3,}\b.*){6}/.test(s.title)) seen.scripts.add('wide-caps');
    }
  }
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.entrances].sort(), ['baseline_reveal', 'fade_rise']);
  assert.deepEqual([...seen.cases].sort(), ['kicker', 'no-numeral', 'no-roadmap', 'numeral', 'roadmap']);
  assert.deepEqual([...seen.scripts].sort(), ['devanagari', 'tamil', 'wide-caps']);
});

// --- pure helpers ---------------------------------------------------------------------------------------------------

test('bumperText: the label\'s digits are the numeral (zero-padded), its other words the kicker; the roadmap numbers a label without digits', () => {
  /** @param {string | null} label @param {string} title @param {{title: string, state: string}[]} [road] */
  const of = (label, title, road = []) => bumperText(/** @type {any} */ ({ chapter_label: label, title }), road);
  assert.deepEqual(of('Part 2', 'Ohm\'s law'), { number: '02', kicker: 'Part', title: 'Ohm\'s law' });
  assert.deepEqual(of('Part 12', 'x'), { number: '12', kicker: 'Part', title: 'x' });
  assert.deepEqual(of('அத்தியாயம் 2', 'x').number, '02', 'a Tamil label with an ASCII number');
  assert.deepEqual(of('அத்தியாயம் 2', 'x').kicker, 'அத்தியாயம்', 'its words are kept (nothing is dropped)');
  // no digits: the whole label is the kicker, and the roadmap gives the number (or there is none)
  assert.deepEqual(of('Intro', 'x'), { number: '', kicker: 'Intro', title: 'x' });
  assert.deepEqual(of('Intro', 'x', [{ title: 'a', state: 'done' }, { title: 'b', state: 'current' }]),
    { number: '02', kicker: 'Intro', title: 'x' });
  assert.deepEqual(of(null, 'x', [{ title: 'a', state: 'current' }]), { number: '01', kicker: '', title: 'x' });
  assert.deepEqual(of('3', 'x'), { number: '03', kicker: '' }.number ? { number: '03', kicker: '', title: 'x' } : {},
    'a label that is only the number leaves no kicker');
});

test('drawsScene: a chapter card with a title or a label; a scene with board text or neither goes to board_generic', () => {
  assert.equal(drawsScene(/** @type {any} */ ({ title: 'Ohm\'s law' })), true);
  assert.equal(drawsScene(/** @type {any} */ ({ title: '', chapter_label: 'Part 2' })), true);
  assert.equal(drawsScene(/** @type {any} */ ({ title: '', chapter_label: ' ' })), false);
  assert.equal(drawsScene(/** @type {any} */ ({ title: 'x', board: [{ id: 'b1', kind: 'bullet', text: 'a point' }] })), false);
  assert.equal(drawsScene(/** @type {any} */ ({ title: 'x', board: [{ id: 'b1', kind: 'bullet', text: '  ' }] })), true);
});

test('roadmapOf: look.refs.roadmap, [] without one or when an entry is malformed', () => {
  assert.deepEqual(roadmapOf(null), []);
  assert.deepEqual(roadmapOf({ refs: {} }), []);
  assert.deepEqual(roadmapOf({ refs: { roadmap: [{ title: 'a', state: 'done' }, { title: 'b' }, null] } }),
    [{ title: 'a', state: 'done' }]);
});

test('texOf: the $...$ spans of the kicker and the title, inline, in order (what richWords mounts)', () => {
  const scene = /** @type {any} */ ({ chapter_label: 'Part 2', title: 'The law $V = IR$ and $P$' });
  assert.deepEqual(texOf(scene, null), [{ tex: 'V = IR', display: false }, { tex: 'P', display: false }]);
  assert.deepEqual(texOf(/** @type {any} */ ({ title: 'plain' }), null), []);
});

test('lineHeight: the type scale\'s Latin values, the Indic minimums for Tamil and Devanagari (plan §2)', () => {
  const scale = /** @type {any} */ (CATALOGUE).type.scale;
  assert.equal(LINE_HEIGHT.numeral, scale.display.line_height.latin[0], 'the numeral is set solid (display 1.0)');
  assert.equal(lineHeight('title', 'Ohm\'s law'), LINE_HEIGHT.title.latin);
  assert.equal(lineHeight('title', 'மின்சுற்று'), LINE_HEIGHT.title.indic);
  assert.equal(lineHeight('kicker', 'भाग'), LINE_HEIGHT.kicker.indic);
  assert.ok(LINE_HEIGHT.title.indic >= scale.display.line_height.indic[0], 'Indic display >= 1.25');
  assert.ok(LINE_HEIGHT.kicker.indic >= scale.body.line_height.indic[0], 'Indic body >= 1.45');
});

test('pipRects: equal nodes with the gap between them, the first and last on the box\'s edges, whole px', () => {
  const box = { x0: 96, y0: 864, x1: 1824, y1: 912 };
  for (const n of [1, 2, 3, 4, 7, 12]) {
    const rects = pipRects(box, n, 8);
    assert.equal(rects.length, n);
    assert.equal(rects[0].x, 96);
    assert.equal(rects[n - 1].x + rects[n - 1].w, 1824, `${n} nodes end on the box's edge`);
    for (const r of rects) assert.ok(Number.isInteger(r.x) && Number.isInteger(r.w) && r.w > 0);
    for (let i = 1; i < n; i++) assert.equal(rects[i].x - (rects[i - 1].x + rects[i - 1].w), 8, 'the gap');
  }
});

/**
 * preflight.py inside_bands: a box lies inside the union of the critical boxes, and every box its vertical extent
 * overlaps holds its whole width.
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

test('geometry: every box is inside the critical area (R-59) and clear of the caption band, in both formats', () => {
  const caption = { landscape: ZONES.landscape.caption, portrait: ZONES.portrait.caption };
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const zones = ZONES[fmt];
    const content = contentBox(fmt);
    assert.ok(insideBands({ x: content.x0, y: content.y0, w: content.x1 - content.x0, h: content.y1 - content.y0 },
      zones.critical), `${fmt}: the content box is critical-safe`);
    for (const shows of [{ kicker: true, numeral: true, roadmap: 4 }, { kicker: false, numeral: true, roadmap: 0 },
      { kicker: true, numeral: false, roadmap: 12 }]) {
      const b = boxes(fmt, shows);
      for (const [part, box] of Object.entries(b)) {
        if (!box) continue;
        const r = { x: box.x0, y: box.y0, w: box.x1 - box.x0, h: box.y1 - box.y0 };
        assert.ok(insideBands(r, zones.critical), `${fmt} ${part}: inside the critical area`);
        assert.ok(box.y1 <= caption[fmt].y0, `${fmt} ${part}: above the caption band (burned captions cover it)`);
        assert.ok([box.x0, box.y0, box.x1, box.y1].every(Number.isInteger), `${fmt} ${part}: whole px`);
      }
      // the title's box holds the lines its fit allows at the largest size, Indic text (the taller line height,
      // plan §2) included: the fit caps lines, so a box too short would let the text run past the critical area
      const L = LAYOUT[fmt];
      const tallest = Math.max(LINE_HEIGHT.title.latin, LINE_HEIGHT.title.indic);
      assert.ok(b.title.y1 - b.title.y0 >= L.title.lines * L.title.max * tallest,
        `${fmt}: the title's box holds ${L.title.lines} Indic lines at ${L.title.max} px`);
      if (b.roadmap) assert.equal(b.roadmap.y1 - b.roadmap.y0, L.pip);
    }
    assert.equal(boxes(fmt, { kicker: true, numeral: true, roadmap: 4 }).roadmap !== null, fmt === 'landscape',
      '§4.3: the mini-roadmap is 16:9 only');
  }
  assert.deepEqual(contentBox('landscape'), { x0: 96, y0: 54, x1: 1824, y1: 912 }, 'the `full` region');
  assert.deepEqual(contentBox('portrait'), { x0: 192, y0: 288, x1: 888, y1: 840 }, 'the upper critical box');
});

test('sizes keep R-29 (the numeral excepted by deviation 14): the title above the text floor, labels above the label floor', () => {
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    const floors = TYPE_FLOORS[fmt];
    assert.ok(L.title.min >= floors.text_min, `${fmt}: the title above the text floor`);
    assert.ok(L.kicker.min >= floors.label_min, `${fmt}: the kicker above the label floor`);
    if (floors.heavy_max_px) {
      // R-29 (9:16): text at <= 64 px is at least weight 600 — every slot of this template is 600 or 700
      for (const slot of Object.values(slotRows(ZONES.landscape, ZONES.portrait))) {
        const spec = /** @type {any} */ (slot)[fmt];
        if (!spec) continue;
        if (spec.default.size[0] <= floors.heavy_max_px) {
          assert.ok(spec.default.weight >= /** @type {number} */ (floors.heavy_min_weight), `${fmt}: R-29 weight`);
        }
      }
    }
    assert.ok(L.numeral.size > floors.label_min, `${fmt}: the numeral is decoration, far above any floor`);
  }
  assert.deepEqual([LAYOUT.landscape.title.max, LAYOUT.landscape.numeral.size], [96, 320], '§4.3 16:9: 96 px title, 320 px numeral');
  assert.deepEqual([LAYOUT.portrait.numeral.size], [240], '§4.3 9:16: 240 px numeral');
  assert.equal(SIZE_STEP, 4, 'board_generic\'s and textfit.py\'s 4 px steps');
});

test('slotRows: the slot table templates.py mirrors (widths from the content boxes), and the catalogue\'s once it lists them', () => {
  const rows = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  assert.deepEqual(Object.keys(rows).sort(), ['kicker', 'numeral', 'title']);
  assert.equal(rows.title.landscape.default.w, 1728, 'the `full` region');
  assert.equal(rows.title.portrait.default.w, 696, 'the upper critical box');
  assert.deepEqual(rows.title.landscape.default.size, [LAYOUT.landscape.title.min, LAYOUT.landscape.title.max]);
  assert.deepEqual(rows.numeral.portrait.default.size, [240, 240]);
  assert.equal(rows.numeral.landscape.default.critical, false, 'deviation 14: the numeral is decoration');
  assert.equal(rows.kicker.landscape.default.critical, false, 'board_generic\'s kicker precedent');
  assert.equal(rows.title.landscape.default.critical, true, 'the chapter\'s name is critical');
  const cat = /** @type {any} */ (templateSlots('chapter_bumper'));
  const spec = /** @type {any} */ (CATALOGUE.templates).chapter_bumper;
  if (!cat || !Object.keys(cat).length) {
    // templates.py registers the template (implemented=True, slots=_chapter_bumper_slots()) in the integrator's
    // change; until then the catalogue must not claim it is implemented, and this check binds as soon as it does
    assert.equal(spec.implemented, false, 'not registered yet: the catalogue agrees');
    return;
  }
  assert.equal(spec.implemented, true);
  for (const slot of Object.keys(rows)) {
    for (const fmt of Object.keys(rows[slot])) {
      const got = cat[slot][fmt].default;
      const want = rows[slot][fmt].default;
      assert.deepEqual({ w: got.w, max_lines: got.max_lines, size: got.size, role: got.role, face: got.face,
        weight: got.weight, line_height: got.line_height, prefer_lines: got.prefer_lines, critical: got.critical },
      want, `${slot} ${fmt}`);
    }
  }
  assert.deepEqual(Object.keys(cat).sort(), Object.keys(rows).sort());
  // a bumper has no board item, so it consumes no cue at all (plan §1.5: the stage then never asks for geometry)
  assert.deepEqual(spec.consumes.kinds, []);
});

test('mediaBox and the static hooks: no medium, no cue geometry (a bumper draws neither)', () => {
  const scene = /** @type {any} */ ({ title: 'Ohm\'s law', chapter_label: 'Part 2' });
  assert.equal(chapterBumper.mediaBox?.(scene, ZONES.landscape, 'render', 'panel', { safe: false, slots: {} }), null);
  assert.equal(chapterBumper.mediaBox?.(scene, ZONES.portrait, 'render', undefined, { safe: false, slots: {} }), null);
  assert.equal(chapterBumper.anchor?.(/** @type {any} */ ({})), null);
});

// --- painted behaviour ----------------------------------------------------------------------------------------------

test('16:9: the numeral slides 40 px in 500 ms (the kicker with it at the text rise), in the personality\'s time', async () => {
  const tl = load('chapter_bumper.landscape.json');
  const k = scaleOf(tl);
  const run = spyStage(tl);
  try {
    const v = () => run.ctx(0).view;
    await run.seekTo(0, 0);
    assert.equal(op(v().numeral.el.style.opacity), 0);
    assert.equal(v().numeral.el.style.transform, `translate(0, ${NUMERAL_RISE}px)`);
    assert.equal(v().kicker.el.style.transform, `translate(0, ${TEXT_RISE}px)`, 'the kicker rises the text distance');
    assert.equal(v().numeral.el.style.visibility, 'hidden');
    const mid = await run.seekTo(0, (NUMERAL_ENTER * k) / 2);
    assert.ok(op(v().numeral.el.style.opacity) > 0 && op(v().numeral.el.style.opacity) < 1, `half way at ${mid}`);
    assert.equal(v().numeral.el.style.visibility, 'visible');
    const dy = Number.parseFloat(/[-\d.]+px\)$/.exec(v().numeral.el.style.transform)?.[0] || '0');
    assert.ok(dy > 0 && dy < NUMERAL_RISE && Number.isInteger(dy), `whole-pixel rise, got ${dy}`);
    await run.seekTo(0, NUMERAL_ENTER * k + 0.05);
    assert.equal(op(v().numeral.el.style.opacity), 1);
    assert.equal(v().numeral.el.style.transform, 'none', 'landed');
    assert.equal(v().kicker.el.style.transform, 'none');
    // the personality's scale really applies: at the unscaled duration the numeral is already in (Crisp x0.85)
    assert.ok(k < 1 && op(v().numeral.el.style.opacity) === 1);
  } finally {
    run.done();
  }
});

test('the title enters 120 ms after the numeral, with the personality\'s text entrance', async () => {
  // Crisp (landscape fixture): baseline_reveal inside a mask; Soft (portrait): a fade and a 16 px rise
  const crisp = spyStage(load('chapter_bumper.landscape.json'));
  try {
    const v = () => crisp.ctx(0).view;
    await crisp.seekTo(0, 0.03);
    assert.equal(v().title.alpha, 0, 'nothing of the title before its delay');
    assert.ok(v().title.mask, 'Crisp wraps the title in a reveal mask');
    await crisp.seekTo(0, 0.4);
    assert.ok(v().title.alpha > 0);
    assert.equal(v().title.el.style.opacity, '1', 'baseline_reveal: opacity on, the mask slides it up');
  } finally {
    crisp.done();
  }
  const soft = spyStage(load('chapter_bumper.portrait.json'));
  try {
    const v = () => soft.ctx(0).view;
    const tl = load('chapter_bumper.portrait.json');
    const k = scaleOf(tl);
    await soft.seekTo(0, TITLE_DELAY * k - 0.02);
    assert.equal(v().title.mask, null, 'Soft has no mask');
    assert.equal(op(v().title.el.style.opacity), 0);
    await soft.seekTo(0, TITLE_DELAY * k + 0.02);
    assert.ok(op(v().title.el.style.opacity) > 0, 'the title starts after the delay');
    assert.match(v().title.el.style.transform, /^translate\(0, \d+px\)$/);
  } finally {
    soft.done();
  }
});

test('reduced motion (live player): everything fades in place, no rise', async () => {
  const run = spyStage(load('chapter_bumper.landscape.json'), { mode: 'live', reducedMotion: true });
  try {
    await run.seekTo(0, 0.1);
    const v = run.ctx(0).view;
    assert.equal(v.numeral.el.style.transform, 'none');
    assert.equal(v.kicker.el.style.transform, 'none');
    assert.ok(op(v.numeral.el.style.opacity) > 0 && op(v.numeral.el.style.opacity) < 1, 'it still fades');
  } finally {
    run.done();
  }
});

test('the mini-roadmap: a node per chapter, past ones ticked, the current one lighting after the title', async () => {
  const tl = load('chapter_bumper.landscape.json');
  const k = scaleOf(tl);
  const run = spyStage(tl);
  try {
    // scene 2 of the fixture is chapter 2 of four: one done, one current, two upcoming
    const v = () => run.ctx(1).view;
    await run.seekTo(1, 0);
    const states = v().progress.pips.map((/** @type {any} */ p) => p.entry.state);
    assert.deepEqual(states, ['done', 'current', 'upcoming', 'upcoming']);
    assert.equal(v().progress.pips.filter((/** @type {any} */ p) => p.tick).length, 1, 'only the done node is ticked');
    assert.equal(op(v().progress.row.style.opacity), 0, 'the row enters after the numeral');
    const ink = run.ctx(1).onBackground.ink;
    await run.seekTo(1, (ROADMAP_DELAY + ROADMAP_ENTER) * k + 0.05);
    assert.equal(op(v().progress.row.style.opacity), 1, 'the row has faded in');
    await run.seekTo(1, LIGHT_AT * k - 0.03);
    const before = v().progress.pips.map((/** @type {any} */ p) => op(p.pip.style.opacity));
    assert.deepEqual(before, [PIP_ALPHA.done, PIP_ALPHA.upcoming, PIP_ALPHA.upcoming, PIP_ALPHA.upcoming],
      'before it lights, the current node is as faint as an upcoming one');
    for (const p of v().progress.pips) assert.equal(p.pip.style.backgroundColor, rgb(ink), 'the block ink, never a structure role');
    const tick = v().progress.pips.find((/** @type {any} */ p) => p.tick).tick;
    assert.equal(op(tick.style.opacity), TICK_ALPHA);
    assert.equal(tick.style.color, rgb(ink));
    await run.seekTo(1, (LIGHT_AT + LIGHT) * k + 0.05);
    const after = v().progress.pips.map((/** @type {any} */ p) => op(p.pip.style.opacity));
    assert.deepEqual(after, [PIP_ALPHA.done, PIP_ALPHA.current, PIP_ALPHA.upcoming, PIP_ALPHA.upcoming],
      'the current chapter\'s node is lit');
    // nothing but the row's own fade moves: the nodes keep the boxes pipRects gave them
    const rects = pipRects(v().boxes.roadmap, 4, LAYOUT.landscape.pipGap);
    assert.deepEqual(v().progress.pips.map((/** @type {any} */ p) => Number.parseFloat(p.pip.style.width)),
      rects.map((r) => r.w));
  } finally {
    run.done();
  }
});

test('9:16 draws no roadmap, but still numbers the chapter from one (§4.3: numeral 240 px, centred)', async () => {
  const run = spyStage(load('chapter_bumper.portrait.json'));
  try {
    await run.seekTo(0, 0.5);
    const v = run.ctx(0).view;
    assert.equal(v.progress, null, 'no node row in the narrow column');
    assert.equal(v.numeral.el.style.fontSize, '240px');
    assert.equal(v.title.holder.style.textAlign, 'center');
    assert.equal(v.numeral.holder.style.textAlign, 'center');
  } finally {
    run.done();
  }
});

test('a label with no digits and no roadmap: the label is the kicker, drawn whole, and there is no numeral', async () => {
  const run = spyStage(load('chapter_bumper.landscape.light.json'));
  try {
    await run.seekTo(1, 0.6);
    const v = run.ctx(1).view;
    assert.equal(v.numeral, null);
    assert.equal(v.text.kicker, 'தொடர்ச்சி');
    assert.equal(v.kicker.el.textContent, 'தொடர்ச்சி', 'no word of the label is dropped');
    // with no numeral the title starts where the numeral would have (the boxes move up, nothing overlaps)
    assert.equal(v.boxes.numeral, null);
    assert.ok(v.boxes.title.y0 < boxes('landscape', { kicker: true, numeral: true, roadmap: 0 }).title.y0);
  } finally {
    run.done();
  }
});

test('the numeral is a tint of the block ink (deviation 14, review D4), never a content role and never stroked', async () => {
  const run = spyStage(load('chapter_bumper.landscape.json'));
  try {
    await run.seekTo(0, 0.5);
    const ctx = run.ctx(0);
    const v = ctx.view;
    assert.equal(v.numeral.el.textContent, '01');
    // the tint: the block ink at NUMERAL_FILL_ALPHA (the DOM drops the trailing zero of the alpha `fx` writes, so
    // the parts are compared)
    const fill = /^rgba\((\d+), (\d+), (\d+), ([\d.]+)\)$/.exec(v.numeral.el.style.color);
    assert.ok(fill, `an rgba fill, got ${v.numeral.el.style.color}`);
    assert.equal(`rgb(${fill[1]}, ${fill[2]}, ${fill[3]})`, rgb(ctx.onBackground.ink));
    assert.equal(Number(fill[4]), NUMERAL_FILL_ALPHA);
    assert.equal(rgbaOf(ctx.onBackground.ink, NUMERAL_FILL_ALPHA),
      `rgba(${fill[1]}, ${fill[2]}, ${fill[3]}, ${fx(NUMERAL_FILL_ALPHA)})`, 'rgbaOf writes the alpha through fx');
    assert.equal(v.numeral.el.style.fontVariantNumeric, 'tabular-nums');
    assert.equal(v.title.el.style.color, rgb(ctx.onBackground.ink), 'the title takes the block ink (R-53)');
    assert.equal(v.kicker.el.style.color, rgb(ctx.onBackground.ink2));
    assert.equal(v.numeral.el.style.outline, '', 'an outline property is cues.js\'s, never a template\'s');
    // review D4: no text stroke - in HTML it is painted OVER the fill, so the internal joins of a glyph whose
    // outline self-overlaps (the "1" of "01" in the display face) were stroked and it read as a grid of boxes
    assert.equal(v.numeral.el.style.getPropertyValue('-webkit-text-stroke'), '');
    assert.ok(!/text-stroke/.test(v.numeral.el.getAttribute('style') || ''), 'no stroke in the written style');
  } finally {
    run.done();
  }
});

test('update writes every property on every frame with a value (no \'\': a cleared property would make the DOM order-dependent)', async () => {
  const proto = /** @type {any} */ (Painter.prototype);
  const real = proto.style;
  /** @type {string[]} */
  const empty = [];
  let inside = false;
  const wrapped = { ...chapterBumper, update(/** @type {any} */ ctx, /** @type {number} */ t, /** @type {any} */ st) { inside = true; try { chapterBumper.update(ctx, t, st); } finally { inside = false; } } };
  proto.style = function style(/** @type {any} */ el, /** @type {string} */ prop, /** @type {any} */ value) {
    if (inside && String(value) === '') empty.push(`${el.className} ${prop}`);
    return real.call(this, el, prop, value);
  };
  try {
    for (const name of FIXTURES) {
      const tl = load(name);
      const { stage, done } = renderStage(tl, { templates: { chapter_bumper: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex });
      const total = Math.round(tl.total_duration * tl.fps);
      for (let f = 0; f < total; f += 3) await stage.seek(f);
      done();
    }
  } finally {
    proto.style = real;
  }
  assert.deepEqual([...new Set(empty)], []);
});

test('dimGroups: the kicker, the numeral, the title and the roadmap row, the same elements at every t', async () => {
  const run = spyStage(load('chapter_bumper.landscape.json'));
  try {
    await run.seekTo(0, 0.2);
    const ctx = run.ctx(0);
    const groups = /** @type {Element[]} */ (chapterBumper.dimGroups?.(ctx));
    assert.deepEqual(groups.map((g) => g.className),
      ['dyn-group dyn-cb-kicker-group', 'dyn-group dyn-cb-numeral-group', 'dyn-group dyn-cb-title-group',
        'dyn-group dyn-cb-progress-group']);
    await run.seekTo(0, 1.2);
    assert.deepEqual(chapterBumper.dimGroups?.(ctx), groups);
    assert.equal(chapterBumper.focusRect?.(ctx, 'b1'), null, 'a bumper has no cue geometry');
    assert.equal(chapterBumper.itemEl?.(ctx, 'b1'), null);
  } finally {
    run.done();
  }
});

test('a scene with board text is drawn by board_generic whole, every hook handed on', async () => {
  const tl = load('chapter_bumper.landscape.json');
  tl.scenes[0].board = [{ id: 'b1', kind: 'bullet', text: 'A point the bumper does not draw' }];
  const run = spyStage(tl);
  try {
    await run.seekTo(0, 0.5);
    const ctx = run.ctx(0);
    assert.equal(ctx.view.cbFallback, true);
    assert.ok(ctx.root.querySelector('.dyn-board'), 'board_generic\'s DOM');
    const entries = /** @type {any[]} */ (chapterBumper.texts?.(ctx));
    assert.deepEqual(entries.map((e) => e.text), /** @type {any[]} */ (boardGeneric.texts?.(ctx)).map((e) => e.text));
    assert.deepEqual(chapterBumper.dimGroups?.(ctx), boardGeneric.dimGroups?.(ctx));
    assert.deepEqual(chapterBumper.tex?.(tl.scenes[0], tl.scenes[0].look, 'landscape'),
      boardGeneric.tex?.(tl.scenes[0], tl.scenes[0].look, 'landscape'));
  } finally {
    run.done();
  }
});

// --- preflight: text entries and remedies ---------------------------------------------------------------------------

test('texts(): the title critical in the block ink, the kicker and numeral decoration, at the sizes painted now', async () => {
  const run = spyStage(load('chapter_bumper.landscape.json'));
  try {
    await run.seekTo(0, 1.5);
    const ctx = run.ctx(0);
    const entries = /** @type {any[]} */ (chapterBumper.texts?.(ctx));
    assert.deepEqual(entries.map((e) => [e.slot, e.role, e.critical]),
      [['kicker', 'label', false], ['numeral', 'label', false], ['title', 'title', true]]);
    const title = entries[2];
    assert.equal(title.ink, ctx.onBackground.ink);
    assert.equal(title.bg, ctx.onBackground.bg, 'the background\'s worst case against the ink (R-53)');
    assert.equal(title.size, LAYOUT.landscape.title.max, 'jsdom has no layout: the title fits at its largest size');
    assert.equal(title.min, LAYOUT.landscape.title.min);
    assert.equal(title.weight, 700);
    assert.equal(title.text, 'What a circuit needs');
    assert.equal(title.alpha, 1, 'settled');
    assert.equal(entries[0].overflow, 0, 'an over-long kicker is cut with an ellipsis, never reported (board_generic\'s rule)');
    assert.equal(entries[0].size, LAYOUT.landscape.kicker.max);
    assert.equal(entries[1].size, LAYOUT.landscape.numeral.size);
    assert.equal(entries[1].text, '01');
    for (const e of entries) assert.match(String(e.ink), /^#[0-9a-f]{6}$/i);
  } finally {
    run.done();
  }
});

test('preflight\'s remedies: a size cap and `safe` hold the title small; a scrim goes behind it', async () => {
  const tl = load('chapter_bumper.landscape.json');
  tl.scenes[0].look.fixes = { safe: false, slots: { title: { size: 88, scrim: true } } };
  const run = spyStage(tl);
  try {
    await run.seekTo(0, 1.5);
    const ctx = run.ctx(0);
    const v = ctx.view;
    assert.equal(v.title.size, 88, 'the fitted size never passes the cap');
    assert.equal(v.title.holder.style.background, rgb(ctx.roles.color('scrim')).replace('rgb', 'rgba').replace(')', ', 0.78)'),
      'the scrim is painted behind the title (the catalogue\'s alpha)');
    const title = /** @type {any[]} */ (chapterBumper.texts?.(ctx))[2];
    assert.notEqual(title.bg, ctx.onBackground.bg, 'reported on the scrimmed background');
  } finally {
    run.done();
  }
  const safe = load('chapter_bumper.landscape.json');
  safe.scenes[0].look.fixes = { safe: true, slots: {} };
  const run2 = spyStage(safe);
  try {
    await run2.seekTo(0, 1.5);
    assert.equal(run2.ctx(0).view.title.size, LAYOUT.landscape.title.min, '`safe` holds the slot at its minimum');
  } finally {
    run2.done();
  }
});

// --- the title's fit, on a fake text layout -------------------------------------------------------------------------

/**
 * Install a fake text layout on HTMLElement (jsdom has none): a glyph is 0.55 em wide and words wrap greedily inside
 * the title's box. Enough for the title's fit (its height from its lines and line height) and for the numeral's
 * width. Returns the restore function.
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
  const boxWidth = (el) => {
    const holder = /** @type {HTMLElement | null} */ (el.closest('.dyn-group'));
    return holder ? Number.parseFloat(holder.style.width) || 0 : 0;
  };
  /** @param {HTMLElement} el */
  const lines = (el) => {
    const size = fontSize(el);
    const box = boxWidth(el);
    let n = 1;
    let x = 0;
    for (const w of el.querySelectorAll('.dyn-word')) {
      const ww = (w.textContent || '').length * CW * size;
      const need = x > 0 ? x + CW * size + ww : ww;
      if (need > box) {
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
      const lh = Number.parseFloat(el.style.lineHeight || '') || 1.3;
      if (el.classList.contains('dyn-word')) return lh * fontSize(el);
      if (el.classList.contains('dyn-cb-title')) return lines(el) * lh * fontSize(el);
      return 0;
    },
  });
  Object.defineProperty(proto, 'offsetWidth', { configurable: true, get() { return (/** @type {HTMLElement} */ (this).textContent || '').length * CW * fontSize(this); } });
  Object.defineProperty(proto, 'clientWidth', { configurable: true, get() { return boxWidth(this); } });
  return () => {
    for (const [k, d] of saved) {
      if (d) Object.defineProperty(proto, /** @type {string} */ (k), /** @type {PropertyDescriptor} */ (d));
      else delete proto[/** @type {string} */ (k)];
    }
  };
}

/** A one-scene timeline of `fmt` whose bumper shows `title`. @param {'landscape' | 'portrait'} fmt @param {string} title */
function bumperTimeline(fmt, title) {
  const tl = load(fmt === 'portrait' ? 'chapter_bumper.portrait.json' : 'chapter_bumper.landscape.json');
  tl.scenes = [tl.scenes[0]];
  tl.scenes[0].title = title;
  tl.total_duration = tl.scenes[0].duration;
  tl.chapters = [{ start: 0, title }];
  return tl;
}

test('fit: the title takes the largest 4 px size on one line, then two; it overflows only at the floor', async () => {
  const restore = fakeLayout();
  try {
    // "Ohm's law" (9 glyphs) fits one line of 1728 px at 96 px; a title of 44 glyphs needs 2 lines at 96
    const short = spyStage(bumperTimeline('landscape', 'Ohm\'s law'));
    await short.seekTo(0, 1.0);
    assert.equal(short.ctx(0).view.title.size, 96, 'the largest size, one line (prefer 1)');
    assert.equal(short.ctx(0).view.title.overflow, 0);
    short.done();
    const two = spyStage(bumperTimeline('landscape', 'MAXIMUM WATTAGE WHEN WARM MOTORS MEET HEAVY LOADS'));
    await two.seekTo(0, 1.0);
    const v = two.ctx(0).view;
    assert.ok(v.title.size <= 96 && v.title.size >= LAYOUT.landscape.title.min, `fitted to ${v.title.size}`);
    assert.equal(v.title.overflow, 0, 'two lines at a size that fits');
    two.done();
    // a title no size can fit in 2 lines: it stays at the minimum and reports the overflow (preflight blocks it)
    const long = spyStage(bumperTimeline('landscape', `${'LONGCHAPTERNAME '.repeat(12)}END`));
    await long.seekTo(0, 1.0);
    assert.equal(long.ctx(0).view.title.size, LAYOUT.landscape.title.min);
    assert.ok(long.ctx(0).view.title.overflow > 0, 'reported, never silently clipped');
    const entry = /** @type {any[]} */ (chapterBumper.texts?.(long.ctx(0)))[2];
    assert.ok(entry.overflow > 0 && entry.critical);
    long.done();
    // 9:16: the narrower column fits fewer glyphs per line, so the same title lands smaller
    const port = spyStage(bumperTimeline('portrait', 'Power in a resistor'));
    await port.seekTo(0, 1.0);
    assert.ok(port.ctx(0).view.title.size <= LAYOUT.portrait.title.max);
    assert.ok(port.ctx(0).view.title.size >= LAYOUT.portrait.title.min);
    assert.equal(port.ctx(0).view.title.overflow, 0);
    port.done();
  } finally {
    restore();
  }
});

test('fit: a chapter number wider than the frame is reported as a decorative overflow, never clipped in silence', async () => {
  const restore = fakeLayout();
  try {
    const tl = bumperTimeline('portrait', 'Ohm\'s law');
    tl.scenes[0].chapter_label = `Part ${'9'.repeat(12)}`;
    const run = spyStage(tl);
    await run.seekTo(0, 1.0);
    const v = run.ctx(0).view;
    assert.ok(v.numeral.overflow > 0, `12 digits at 240 px pass the 696 px column (${v.numeral.overflow} px)`);
    const entry = /** @type {any[]} */ (chapterBumper.texts?.(run.ctx(0))).find((e) => e.slot === 'numeral');
    assert.ok(entry.overflow > 0 && entry.critical === false, 'a decorative overflow: a warning, never a block');
    run.done();
  } finally {
    restore();
  }
});

test('every painted number goes through fx (stable strings between machines; the DOM normalises them back)', async () => {
  const run = spyStage(load('chapter_bumper.landscape.json'));
  try {
    await run.seekTo(1, 0.9);
    const v = run.ctx(1).view;
    for (const p of v.progress.pips) assert.equal(String(Number(p.pip.style.opacity)), p.pip.style.opacity, 'a plain number');
    assert.equal(Number(v.progress.pips[0].tick.style.opacity), Number(fx(TICK_ALPHA)));
    assert.equal(Number(v.numeral.el.style.opacity), Number(fx(1)));
    // every translate the template paints is on whole pixels (plan §P condition 2)
    for (const el of [v.numeral.el, v.kicker.el, v.title.el, v.progress.row]) {
      const dy = /translate\(0, (-?[\d.]+)px\)/.exec(el.style.transform);
      if (dy) assert.ok(Number.isInteger(Number(dy[1])), `${el.className}: whole-pixel translate`);
    }
  } finally {
    run.done();
  }
});
