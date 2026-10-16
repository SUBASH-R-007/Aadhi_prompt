// @ts-check
// animation_stage (DYNAMIC_VIDEO_DESIGN §4.3 B, R1, media; forced for a `simulation` scene): the template contract
// and order independence on every fixture (tests/compose/dynamic/fixtures/templates/animation_stage.*.json: both
// formats, a dark and a light family each, a simulation whose step labels come from the beats' visual cues, and a
// manim panel beside board items with the lint-limit text, one very long word, Tamil, Hindi and TeX), then its
// behaviour: the boxes of each format, the animation contained between the title and the step label, mediaBox against
// the laid-out frame under every fix, the fade-only entrance (a clip is never scaled), one step label at a time, the
// cues' hooks, the scene it hands to board_generic whole, and what it reports to preflight.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import animationStage, {
  EXIT_FACTOR, LABELS, LABEL_LEAD, LAYOUT, LINE_HEIGHT, MAX_ITEMS, MEDIA_DELAY, MIN_DWELL, TEXT_ENTER, TEXT_KINDS,
  boxes, clipShown, drawsScene, frameRect, labelSchedule, lineHeight, mediaOf, stepsOf, texOf, textItemsOf,
} from '../../../js/dynamic/templates/animation_stage.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import { cueEvents, termSpans } from '../../../js/dynamic/cues.js';
import { REGIONS, ZONES, insideAny, templateBehaviour, templateSlots } from '../../../js/dynamic/core.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
const FIXTURES = readdirSync(DIR).filter((n) => n.startsWith('animation_stage.') && n.endsWith('.json')).sort();
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const TEMPLATES = { animation_stage: animationStage };

test('the fixtures: both formats, a dark and a light family in each', () => {
  const seen = FIXTURES.map((n) => {
    const tl = load(n);
    return `${tl.presentation.format}:${tl.presentation.mode}`;
  });
  for (const want of ['landscape:dark', 'landscape:light', 'portrait:dark', 'portrait:light']) assert.ok(seen.includes(want), want);
  // both shapes of §4.3's data: a simulation's own medium, and a manim side panel
  const types = FIXTURES.map((n) => load(n).scenes[0]).map((s) => (s.media ? 'main' : 'panel'));
  assert.ok(types.includes('main') && types.includes('panel'));
});

for (const name of FIXTURES) {
  test(`${name}: the template contract (plan §1.4, §1.5) and order independence (§7.4)`, async () => {
    const tl = load(name);
    assert.deepEqual(await contractProblems(animationStage, tl), []);
    const last = Math.round(tl.total_duration * tl.fps) - 1;
    const frames = [0, 4, 10, 18, 30, 45, 60, 90, 120, 150, 180, 200, 215, 230, 260, last];
    assert.deepEqual(await orderProblems(tl, frames, { templates: TEMPLATES }), []);
  });
}

// ---------------------------------------------------------------------------------------------
// Helpers: a stage with the template (its build context kept), and a small layout model for jsdom (no layout
// engine): text takes ceil(characters x 0.55 em / width) lines of size x line-height; an element placed at layout
// time keeps its left / top / width / height; a word wider than its line breaks.
// ---------------------------------------------------------------------------------------------

/** @param {any} tl @param {{mode?: 'live' | 'render', reducedMotion?: boolean}} [opts] */
function mount(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const ctxs = new Map();
  const wrapped = { ...animationStage, build(/** @type {any} */ ctx) { ctxs.set(ctx.index, ctx); return animationStage.build(ctx); } };
  const tex = recordingTex();
  const { stage, done } = renderStage(tl, { templates: { animation_stage: wrapped }, renderTex: tex.renderTex, ...opts });
  return { stage, done, tex, ctx: (i = 0) => ctxs.get(i), view: (i = 0) => ctxs.get(i).view };
}

const CH = 0.55;
/** @param {unknown} v */
const px = (v) => {
  const m = /^(-?\d+(?:\.\d+)?)px$/.exec(String(v || ''));
  return m ? Number(m[1]) : null;
};
/** @param {any} el @param {string} prop */
const up = (el, prop) => {
  for (let e = el; e && e.style; e = e.parentElement) if (e.style[prop]) return e.style[prop];
  return '';
};
/** @param {any} el */
const widthOf = (el) => {
  for (let e = el; e && e.style; e = e.parentElement) {
    const w = px(e.style.width);
    if (w !== null) return w;
  }
  return 1920;
};
/** @param {any} el the element whose text sets a box's height */
const textHost = (el) => (el.matches('.dyn-as-text, h2, .dyn-word') ? el
  : el.querySelector('.dyn-as-text, h2') || ((el.textContent || '').trim() ? el : null));
/** @param {any} el */
function fakeHeight(el) {
  const own = px(el.style && el.style.height);
  if (own !== null) return own;
  const host = textHost(el);
  if (!host) return 0;
  const size = px(up(host, 'fontSize')) || 16;
  const lh = Number(up(host, 'lineHeight')) || 1.3;
  const w = widthOf(host);
  const len = (host.textContent || '').length;
  return (len ? Math.max(1, Math.ceil((len * CH * size) / w)) : 0) * size * lh;
}
/** Install the model; returns its uninstall. */
function layoutModel() {
  const proto = /** @type {any} */ (window.HTMLElement.prototype);
  const saved = ['offsetHeight', 'offsetWidth', 'clientWidth', 'scrollWidth'].map((k) => [k, Object.getOwnPropertyDescriptor(proto, k)]);
  const rect = /** @type {any} */ (window.Element.prototype).getBoundingClientRect;
  Object.defineProperty(proto, 'offsetHeight', { configurable: true, get() { return fakeHeight(this); } });
  Object.defineProperty(proto, 'offsetWidth', { configurable: true, get() {
    return this.classList.contains('dyn-word') ? (this.textContent || '').length * CH * (px(up(this, 'fontSize')) || 16) : widthOf(this);
  } });
  Object.defineProperty(proto, 'clientWidth', { configurable: true, get() { return widthOf(this); } });
  Object.defineProperty(proto, 'scrollWidth', { configurable: true, get() { return widthOf(this); } });
  /** @this {any} */
  window.Element.prototype.getBoundingClientRect = function box() {
    let x = 0;
    let y = 0;
    for (let e = this; e && e.style; e = e.parentElement) {
      x += px(e.style.left) || 0;
      y += px(e.style.top) || 0;
    }
    const w = this.classList && this.classList.contains('dyn-stage') ? px(this.style.width) || 0 : widthOf(this);
    const h = this.classList && this.classList.contains('dyn-stage') ? px(this.style.height) || 0 : fakeHeight(this);
    return /** @type {any} */ ({ left: x, top: y, width: w, height: h, right: x + w, bottom: y + h, x, y });
  };
  return () => {
    for (const [k, d] of saved) {
      if (d) Object.defineProperty(proto, /** @type {string} */ (k), d);
      else delete proto[/** @type {string} */ (k)];
    }
    window.Element.prototype.getBoundingClientRect = rect;
  };
}

/** @param {any} el */
const boxOf = (el) => ({
  x0: px(el.style.left), y0: px(el.style.top),
  x1: (px(el.style.left) || 0) + (px(el.style.width) || 0),
  y1: (px(el.style.top) || 0) + (px(el.style.height) || 0),
});
/** A fixture with its first scene edited. @param {string} name @param {(s: any, tl: any) => void} edit */
function edited(name, edit) {
  const tl = load(name);
  edit(tl.scenes[0], tl);
  return tl;
}
/** Frame of scene time t in the first scene. @param {any} tl @param {number} t */
const frameAt = (tl, t) => Math.round(t * tl.fps);
/** @param {{x0: number, y0: number, x1: number, y1: number}} b */
const rectOfBox = (b) => ({ x: b.x0, y: b.y0, w: b.x1 - b.x0, h: b.y1 - b.y0 });

// ---------------------------------------------------------------------------------------------
// Pure parts
// ---------------------------------------------------------------------------------------------

test('the catalogue slots of animation_stage are its LAYOUT (registered: never skipped, B2c review CG-4)', () => {
  const spec = /** @type {any} */ (CATALOGUE.templates).animation_stage;
  const slots = /** @type {any} */ (templateSlots('animation_stage'));
  assert.equal(spec.implemented, true, 'templates.py: implemented=True');
  assert.equal(spec.push_ok, false, 'a clip is never pushed (deviation 28)');
  assert.deepEqual(spec.forced_for, ['simulation'], '§4.3: forced for a simulation scene');
  const land = boxes('landscape', 0, 0);
  const port = boxes('portrait', 0, 0);
  const tl = slots.title.landscape.default;
  assert.deepEqual([tl.w, tl.max_lines, tl.prefer_lines, tl.size, tl.weight, tl.line_height],
    [land.title.x1 - land.title.x0, LAYOUT.landscape.title.lines, LAYOUT.landscape.title.prefer,
      [LAYOUT.landscape.title.min, LAYOUT.landscape.title.max], 700, LINE_HEIGHT.heading.latin]);
  const ll = slots.label.landscape.default;
  assert.deepEqual([ll.w, ll.max_lines, ll.size, ll.weight, ll.line_height],
    [land.label.x1 - land.label.x0, LAYOUT.landscape.label.lines,
      [LAYOUT.landscape.label.min, LAYOUT.landscape.label.max], LAYOUT.landscape.labelWeight, LINE_HEIGHT.body.latin]);
  const tp = slots.title.portrait.default;
  assert.deepEqual([tp.w, tp.max_lines, tp.prefer_lines, tp.size, tp.line_height],
    [port.title.x1 - port.title.x0, LAYOUT.portrait.title.lines, LAYOUT.portrait.title.prefer,
      [LAYOUT.portrait.title.min, LAYOUT.portrait.title.max], LINE_HEIGHT.heading.latin]);
  const lp = slots.label.portrait.default;
  assert.deepEqual([lp.w, lp.max_lines, lp.size, lp.weight, lp.line_height],
    [port.label.x1 - port.label.x0, LAYOUT.portrait.label.lines,
      [LAYOUT.portrait.label.min, LAYOUT.portrait.label.max], LAYOUT.portrait.labelWeight, LINE_HEIGHT.body.latin]);
  // the forms per kind (B2c review CG-5): the label's item for a term cue, the item or the animation for look_here
  assert.deepEqual(spec.consumes.forms,
    { reveal: ['item'], cue_term: ['item', 'item:term'], look_here: ['item', 'panel'] });
  assert.equal(templateBehaviour('animation_stage')?.pushOk, false);
});

test('line heights per script (§4.6, plan §2 R-29): the type scale\'s, Indic text at the Indic floors', () => {
  const scale = /** @type {any} */ (CATALOGUE).type.scale;
  assert.equal(LINE_HEIGHT.heading.latin, scale.heading.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.heading.indic, scale.heading.line_height.indic[0]);
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.body.indic, scale.body.line_height.indic[0]);
  assert.equal(lineHeight('body', 'Ohm விதி: $R = V/I$'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('body', 'वोल्टेज दोगुना'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('body', 'The line is drawn'), LINE_HEIGHT.body.latin);
  assert.equal(lineHeight('heading', 'ஓம் விதி'), LINE_HEIGHT.heading.indic);
  // a [[term]] or $TeX$ around Indic text does not hide it (plainText first)
  assert.equal(lineHeight('body', '[[மின்னோட்டம்]] நேர் விகிதம்'), LINE_HEIGHT.body.indic);
});

test('TEXT_KINDS is templates.py TEXT_KINDS (the text items the §4.4 media row counts)', () => {
  const py = readFileSync(new URL('../../../../aadhi/compose/dynamic/templates.py', import.meta.url), 'utf-8');
  const m = /TEXT_KINDS: Final = frozenset\(\{([^}]*)\}\)/.exec(py);
  assert.ok(m);
  const kinds = [...(/** @type {RegExpExecArray} */ (m))[1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]).sort();
  assert.deepEqual([...TEXT_KINDS].sort(), kinds);
  assert.equal(MAX_ITEMS, 3, '§4.4 media row: at most three text items beside the medium');
});

test('boxes: the title above, the animation between, the step label in the lower third; inside the critical boxes', () => {
  // 16:9: the critical box, the label at its bottom just above the caption band
  const full = REGIONS.landscape.full;
  const land = boxes('landscape', 100, 60);
  assert.deepEqual(land.title, { x0: full.x0, y0: full.y0, x1: full.x1, y1: full.y0 + 100 });
  assert.deepEqual(land.label, { x0: full.x0, y0: full.y1 - 60, x1: full.x1, y1: full.y1 });
  assert.deepEqual(land.media, { x0: full.x0, y0: full.y0 + 100 + LAYOUT.landscape.gap, x1: full.x1, y1: full.y1 - 60 - LAYOUT.landscape.gap });
  assert.ok(land.label.y1 < ZONES.landscape.caption.y0, 'the label ends above the caption band');
  const bare = boxes('landscape', 0, 0);
  assert.deepEqual(bare.media, full, 'no title and no label: the animation takes the whole critical box');
  assert.deepEqual(boxes('landscape', 100, 0).media, { ...full, y0: full.y0 + 100 + LAYOUT.landscape.gap });

  // 9:16: the headline's left edge to the TikTok rail, 8 px above the caption band (the catalogue's zones, CG-3)
  const port = boxes('portrait', 80, 70);
  const L = LAYOUT.portrait;
  assert.equal(L.labelBottom, ZONES.portrait.caption.y0 - 8);
  assert.deepEqual([port.label.x0, port.label.x1, port.label.y1], [192, 780, L.labelBottom]);
  assert.equal(port.label.y0, L.labelBottom - 70);
  assert.deepEqual([port.title.x0, port.title.x1, port.title.y0], [192, 888, REGIONS.portrait.headline.y0]);
  assert.deepEqual(port.media, { x0: 60, y0: REGIONS.portrait.headline.y0 + 80 + L.gap, x1: 1020, y1: port.label.y0 - L.gap });
  assert.deepEqual(boxes('portrait', 0, 0).media, REGIONS.portrait.tall, 'without either: the tall region');
  assert.deepEqual(boxes('portrait', 0, 70).media, { ...REGIONS.portrait.tall, y1: port.label.y0 - L.gap });

  // R-59: the title and the label stay inside the critical boxes at the tallest fit their slots allow
  for (const [fmt, zones] of /** @type {const} */ ([['landscape', ZONES.landscape], ['portrait', ZONES.portrait]])) {
    const S = LAYOUT[fmt];
    const titleH = Math.ceil(S.title.max * LINE_HEIGHT.heading.indic * S.title.lines);
    const labelH = Math.ceil(S.label.max * LINE_HEIGHT.body.indic * S.label.lines);
    const b = boxes(fmt, titleH, labelH);
    assert.ok(insideAny(rectOfBox(b.title), zones.critical), `${fmt}: the title`);
    assert.ok(insideAny(rectOfBox(b.label), zones.critical), `${fmt}: the label`);
    assert.ok(b.media.y1 > b.media.y0, `${fmt}: the animation keeps room even then`);
  }
});

test('mediaOf / clipShown / textItemsOf: what the scene shows', () => {
  const sim = load('animation_stage.landscape.json').scenes[0];
  const panel = load('animation_stage.landscape.light.json').scenes[0];
  assert.equal(mediaOf(sim, 'render')?.slot, 'main');
  assert.equal(mediaOf(sim, 'render')?.shown, true);
  assert.equal(mediaOf(panel, 'render')?.slot, 'panel');
  const video = { kind: 'video', asset_key: 'manim-1', url: '/m.mp4', width: 1280, height: 720 };
  assert.equal(clipShown(mediaOf({ ...sim, media: video }, 'render')), true);
  assert.equal(clipShown(mediaOf(sim, 'render')), false, 'a still stands in for the animation');
  assert.equal(mediaOf({ ...sim, media: null, poster: video }, 'render')?.slot, 'main', 'a poster stands in too');
  assert.equal(mediaOf({ ...panel, layout: { show_side_panel: false } }, 'render'), null, 'a panel the layout hides');
  assert.equal(mediaOf({ ...panel, side_panel: { ...panel.side_panel, panel: { ...panel.side_panel.panel, kind: 'chart' } } }, 'render'),
    null, 'a panel kind this template does not draw');
  const gif = { ...sim, media: { kind: 'gif', url: 'https://example.test/a.gif', render_in_mp4: false } };
  assert.equal(mediaOf(gif, 'render')?.shown, false, 'a GIF is live only');
  assert.equal(mediaOf(gif, 'live')?.shown, true);
  assert.deepEqual(textItemsOf(/** @type {any} */ ({ board: [{ id: 'a', kind: 'formula' }, { id: 'b', kind: 'bullet' }] })).map((i) => i.id), ['b']);
});

test('stepsOf: the board items when there are any, else one label per beat that has a visual cue (§4.3)', () => {
  const sim = load('animation_stage.landscape.json').scenes[0];
  const steps = stepsOf(sim);
  assert.deepEqual(steps.map((s) => s.text), sim.beats.map((/** @type {any} */ b) => b.visual_cue));
  assert.deepEqual(steps.map((s) => s.item), [null, null, null]);
  assert.deepEqual(steps.map((s) => Number(s.enter.toFixed(3))),
    sim.beats.map((/** @type {any} */ b) => Number(Math.max(0, b.start - LABEL_LEAD).toFixed(3))), 'R-17: the lead');
  const panel = load('animation_stage.landscape.light.json').scenes[0];
  const items = stepsOf(panel);
  assert.deepEqual(items.map((s) => s.id), ['w1', 'w2', 'w3']);
  assert.ok(items.every((s) => s.item && !s.initial), 'each item is revealed by a beat');
  assert.ok(items[0].enter < items[1].enter && items[1].enter < items[2].enter);
  // a definition reads as its term and meaning; a beat with no cue contributes no label
  assert.equal(stepsOf(/** @type {any} */ ({ board: [{ id: 'd', kind: 'definition', term: 'Ohm', text: 'the unit' }] }))[0].text,
    `Ohm${LABELS.definitionJoin}the unit`);
  assert.deepEqual(stepsOf(/** @type {any} */ ({ board: [], beats: [{ beat_id: 'b1', start: 1 }, { beat_id: 'b2', start: 2, visual_cue: '  ' }] })), []);
  assert.deepEqual(texOf(panel), [], 'the fixture label has no TeX');
  assert.deepEqual(texOf(load('animation_stage.portrait.indic.json').scenes[0]), [{ tex: 'I = V/R', display: false }]);
});

test('drawsScene: a scene with no animation, or a board this template does not draw, is board_generic\'s whole (CG-2)', () => {
  const sim = load('animation_stage.landscape.json').scenes[0];
  const panel = load('animation_stage.landscape.light.json').scenes[0];
  assert.equal(drawsScene(sim), true);
  assert.equal(drawsScene(panel), true);
  // a simulation whose Manim render failed: compose/timeline.py gives it a board of bullets and no medium
  const failed = { ...sim, media: null, board: [{ id: 'cue-1', kind: 'bullet', text: 'The law appears' }] };
  assert.equal(drawsScene(failed), false);
  assert.equal(drawsScene({ ...sim, media: null }), false, 'nothing to lay out at all');
  assert.equal(drawsScene({ ...panel, board: [...panel.board, { id: 'x', kind: 'bullet', text: 'a fourth' }] }), false,
    `more than ${MAX_ITEMS} text items`);
  assert.equal(drawsScene({ ...panel, board: [{ id: 'f', kind: 'figure', figure_id: 'fig-1' }] }), false,
    'a figure item carries its own medium: media_split / board_generic draw it');
  assert.deepEqual(texOf(failed), [], 'every hook is handed on, the static ones included');
});

test('drawsScene: a shown side panel that is not the animation is board_generic\'s whole (never drop content, CG-2)', () => {
  const sim = load('animation_stage.landscape.json').scenes[0];
  assert.equal(mediaOf(sim)?.slot, 'main', "the simulation's own Manim render");
  const terminal = { panel: { kind: 'terminal', terminal: { command: 'python ohm.py', output: 'I = 0.5 A' } }, show_at: 0 };
  assert.equal(drawsScene({ ...sim, side_panel: terminal, layout: { show_side_panel: true } }), false);
  assert.equal(drawsScene({ ...sim, side_panel: terminal, layout: {} }), false, 'shown unless the layout hides it');
  assert.equal(drawsScene({ ...sim, side_panel: terminal, layout: { show_side_panel: false } }), true,
    'a panel the layout hides is nothing to draw');
  for (const kind of ['chart', 'image', 'skill_tree']) {
    assert.equal(drawsScene({ ...sim, side_panel: { panel: { kind, title: 'A card' }, show_at: 0 }, layout: {} }), false, kind);
  }
  // the `manim` panel that *is* the animation is still drawn (the light fixture is exactly that scene)
  const panel = load('animation_stage.landscape.light.json').scenes[0];
  assert.equal(mediaOf(panel)?.slot, 'panel');
  assert.equal(drawsScene(panel), true);
});

test('mediaBox answers only the slot it lays out: another slot gets board_generic\'s box, never the animation box', () => {
  const fixes = /** @type {any} */ ({ safe: false, slots: {} });
  const sim = load('animation_stage.landscape.json').scenes[0];
  const own = animationStage.mediaBox?.(sim, ZONES.landscape, 'render', 'main', fixes);
  assert.ok(own && own.w > 0 && own.h > 0);
  const scene = { ...sim, side_panel: { panel: { kind: 'manim' }, media: { kind: 'video', url: '/p.mp4', asset_key: 'p' }, show_at: 0 }, layout: { show_side_panel: false } };
  assert.equal(mediaOf(scene, 'render')?.slot, 'main');
  assert.deepEqual(animationStage.mediaBox?.(scene, ZONES.landscape, 'render', 'main', fixes), own);
  assert.deepEqual(animationStage.mediaBox?.(scene, ZONES.landscape, 'render', 'panel', fixes),
    boardGeneric.mediaBox?.(scene, ZONES.landscape, 'render', 'panel', fixes));
});

test('frameRect: the animation at its own aspect, centred, and never larger than the asset (§P: only downscale)', () => {
  const box = { x0: 96, y0: 158, x1: 1824, y1: 912 };
  const big = frameRect(/** @type {any} */ ({ kind: 'video', width: 1920, height: 1080 }), box);
  assert.deepEqual(big, { x: 290, y: 158, w: 1340, h: 754 }, 'contained in the box, centred');
  // a 1280x720 Manim render: shown at its own size, never stretched over the 1728 px stage
  const small = frameRect(/** @type {any} */ ({ kind: 'video', width: 1280, height: 720 }), box);
  assert.deepEqual([small.w, small.h], [1280, 720]);
  assert.deepEqual([small.x + small.w / 2, small.y + small.h / 2], [(box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2]);
  // no stored size (or the card shown when the mode has no animation): the box, at 16:9
  const none = frameRect(null, box);
  assert.deepEqual([none.w, none.h], [1340, 754]);
  for (const r of [big, small, none]) {
    assert.ok(r.x >= box.x0 && r.y >= box.y0 && r.x + r.w <= box.x1 && r.y + r.h <= box.y1);
    assert.ok([r.x, r.y, r.w, r.h].every((n) => Number.isInteger(n)), 'whole px');
  }
});

test('labelSchedule: one step label at a time, the one before leaving 30% faster just before it', () => {
  const ex = TEXT_ENTER * EXIT_FACTOR;
  const on = labelSchedule([{ id: 'a', enter: 0.5, initial: false }, { id: 'b', enter: 3, initial: false },
    { id: 'c', enter: 6, initial: false }], 10);
  assert.deepEqual(on.map((s) => s.enter), [0.5, 3, 6], 'on beats: each enters on time');
  assert.ok(Math.abs(on[0].exit - (3 - ex)) < 1e-12 && Math.abs(on[1].exit - (6 - ex)) < 1e-12);
  assert.equal(on[2].exit, Infinity, 'the last one stays');
  const free = labelSchedule([{ id: 'a', enter: 0.35, initial: true }, { id: 'b', enter: 0.44, initial: true }], 20);
  assert.ok(Math.abs(free[1].enter - (0.35 + TEXT_ENTER + MIN_DWELL + ex)) < 1e-12, 'unanchored: waits for MIN_DWELL');
  const short = labelSchedule([{ id: 'a', enter: 0.35, initial: true }, { id: 'b', enter: 0.44, initial: true }], 2);
  assert.ok(Math.abs(short[1].enter - Math.max(2 - TEXT_ENTER - 1, 0.35 + ex)) < 1e-12, 'but a second before a short scene ends');
  const soft = labelSchedule([{ id: 'a', enter: 0, initial: false }, { id: 'b', enter: 3, initial: false }], 10, 1.15);
  assert.ok(Math.abs(soft[0].exit - (3 - TEXT_ENTER * 1.15 * EXIT_FACTOR)) < 1e-12, 'the personality scales the exit');
  const close = labelSchedule([{ id: 'a', enter: 1, initial: false }, { id: 'b', enter: 1.1, initial: false }], 10);
  assert.equal(close[0].exit, 1, 'beats too close: the earlier one leaves at once');
  for (const list of [on, free, short, soft, close]) {
    for (let k = 1; k < list.length; k++) assert.ok(list[k].enter >= list[k - 1].exit + ex - 1e-12, 'never two labels at once');
  }
});

// ---------------------------------------------------------------------------------------------
// On the stage
// ---------------------------------------------------------------------------------------------

test('16:9 layout: the animation contained at its aspect between the title and the step label, centred', async () => {
  const undo = layoutModel();
  try {
    const tl = load('animation_stage.landscape.json');
    const m = mount(tl);
    await m.stage.seek(frameAt(tl, 5));
    const v = m.view();
    const laid = boxes('landscape', Math.max(1, fakeHeight(v.title)), Math.max(1, v.labels.reduce((/** @type {number} */ a, /** @type {any} */ it) => Math.max(a, fakeHeight(it.group)), 0)));
    const frame = boxOf(v.frame);
    assert.deepEqual(v.mediaBox, frame, 'the frame is the medium itself (the clip\'s own dark card)');
    assert.ok(frame.x0 >= laid.media.x0 && frame.y0 >= laid.media.y0, 'inside its box');
    assert.ok((frame.x1 || 0) <= laid.media.x1 && (frame.y1 || 0) <= laid.media.y1);
    const w = (frame.x1 || 0) - (frame.x0 || 0);
    const h = (frame.y1 || 0) - (frame.y0 || 0);
    assert.ok(Math.abs(w / h - 1600 / 900) < 0.01, '16:9, its own aspect');
    const cx = (laid.media.x0 + laid.media.x1) / 2;
    assert.ok(Math.abs((frame.x0 || 0) + w / 2 - cx) <= 1, 'centred across its box');
    assert.equal(px(v.titleGroup.style.top), REGIONS.landscape.full.y0, 'the title at the top of the critical box');
    assert.equal(px(v.titleGroup.style.width), REGIONS.landscape.full.x1 - REGIONS.landscape.full.x0);
    for (const it of v.labels) assert.equal(px(it.group.style.top), laid.label.y0, 'every label in the lower third');
    assert.ok(v.labelSize <= LAYOUT.landscape.label.max && v.labelSize >= LAYOUT.landscape.label.min);
    m.done();
  } finally {
    undo();
  }
});

test('9:16 layout: the animation across the stage under the headline, the label in the lower critical box', async () => {
  const undo = layoutModel();
  try {
    const tl = load('animation_stage.portrait.json');
    const m = mount(tl);
    await m.stage.seek(frameAt(tl, 5));
    const v = m.view();
    const frame = boxOf(v.frame);
    assert.ok((frame.x0 || 0) >= REGIONS.portrait.stage.x0 && (frame.x1 || 0) <= REGIONS.portrait.stage.x1);
    assert.ok((frame.y0 || 0) >= REGIONS.portrait.headline.y0, 'never above the headline');
    assert.ok((frame.y1 || 0) <= v.labelBox.y0 - LAYOUT.portrait.gap + 1, 'and clear of the step label');
    const w = (frame.x1 || 0) - (frame.x0 || 0);
    const h = (frame.y1 || 0) - (frame.y0 || 0);
    assert.ok(Math.abs(w / h - 1080 / 1350) < 0.01, '§4.3: the 1080x1350 9:16 panel profile, at its own aspect');
    assert.deepEqual([v.labelBox.x0, v.labelBox.x1, v.labelBox.y1], [192, 780, LAYOUT.portrait.labelBottom]);
    assert.ok(insideAny(rectOfBox(v.labelBox), ZONES.portrait.critical), 'R-59: the label is in a critical box');
    assert.ok(insideAny({ x: v.titleBox.x0, y: v.titleBox.y0, w: v.titleBox.x1 - v.titleBox.x0, h: v.titleBox.y1 - v.titleBox.y0 },
      ZONES.portrait.critical), 'R-59: so is the headline');
    m.done();
  } finally {
    undo();
  }
});

test('mediaBox is never smaller than the laid-out frame, under every preflight fix (§P: pictures only downscale)', async () => {
  const undo = layoutModel();
  try {
    /** @type {any[]} */
    const fixes = [undefined, { safe: true }, { slots: { title: { size: LAYOUT.landscape.title.min } } },
      { slots: { label: { size: LAYOUT.landscape.label.min }, title: { scrim: true } } }];
    for (const name of ['animation_stage.landscape.json', 'animation_stage.landscape.light.json',
      'animation_stage.portrait.json', 'animation_stage.portrait.indic.json']) {
      for (const fix of fixes) {
        const tl = edited(name, (s) => {
          if (fix) s.look.fixes = fix;
        });
        const m = mount(tl);
        await m.stage.seek(frameAt(tl, 8));
        const v = m.view();
        const zones = tl.presentation.format === 'portrait' ? ZONES.portrait : ZONES.landscape;
        const box = /** @type {any} */ (animationStage.mediaBox?.(tl.scenes[0], zones, 'render', v.media.slot, { safe: false, slots: {} }));
        const frame = boxOf(v.frame);
        assert.ok(box.w >= (frame.x1 || 0) - (frame.x0 || 0) - 1 && box.h >= (frame.y1 || 0) - (frame.y0 || 0) - 1,
          `${name} ${JSON.stringify(fix)}: mediaBox ${JSON.stringify(box)} < the frame ${JSON.stringify(frame)}`);
        m.done();
      }
    }
  } finally {
    undo();
  }
});

test('the animation enters by a fade and is never scaled (deviation 28), a video clip included', async () => {
  const undo = layoutModel();
  try {
    const clip = { kind: 'video', asset_key: 'manim-clip', url: '/media/a.mp4', width: 1280, height: 720, duration: 6, fit: 'contain', end_behavior: 'freeze' };
    for (const media of [null, clip]) {
      const tl = edited('animation_stage.landscape.json', (s) => {
        if (media) s.media = media;
      });
      const m = mount(tl);
      /** @type {number[]} */
      const alphas = [];
      for (let f = 0; f < 40; f++) {
        await m.stage.seek(f);
        const v = m.view();
        assert.equal(v.frame.style.transform, 'none', `frame ${f}: never scaled`);
        alphas.push(Number(v.frame.style.opacity));
      }
      assert.equal(alphas[0], 0, 'hidden at t = 0');
      assert.ok(alphas[Math.round(MEDIA_DELAY * 30) + 20] === 1, 'fully in after its entrance');
      for (let k = 1; k < alphas.length; k++) assert.ok(alphas[k] >= alphas[k - 1] - 1e-9, 'the fade never goes back');
      m.done();
    }
  } finally {
    undo();
  }
});

test('one step label on screen at a time (the one before leaves as the next enters)', async () => {
  const undo = layoutModel();
  try {
    for (const name of ['animation_stage.landscape.json', 'animation_stage.portrait.indic.json']) {
      const tl = load(name);
      const m = mount(tl);
      for (let f = 0; f < Math.round(tl.total_duration * tl.fps); f += 2) {
        await m.stage.seek(f);
        const shown = m.view().labels.filter((/** @type {any} */ it) => it.alpha > 0.001);
        assert.ok(shown.length <= 1, `${name} frame ${f}: ${shown.length} labels at once`);
      }
      // every label is shown at some point: nothing is dropped
      /** @type {Set<string>} */
      const seen = new Set();
      for (let f = 0; f < Math.round(tl.total_duration * tl.fps); f++) {
        await m.stage.seek(f);
        for (const it of m.view().labels) if (it.alpha > 0.5) seen.add(it.id);
      }
      assert.equal(seen.size, m.view().labels.length, `${name}: every step label is shown`);
      m.done();
    }
  } finally {
    undo();
  }
});

test('the cues\' hooks: focusRect on the animation and on a label, itemEl\'s [[term]] spans, constant dimGroups', async () => {
  const undo = layoutModel();
  try {
    const tl = load('animation_stage.portrait.indic.json');
    const m = mount(tl);
    await m.stage.seek(frameAt(tl, 8));
    const ctx = m.ctx();
    const v = m.view();
    const item = /** @type {any} */ (animationStage.focusRect?.(ctx, 'i3'));
    assert.ok(item, 'the label on screen');
    assert.equal(item.kind, 'text');
    assert.deepEqual(item.el, v.byId.get('i3').group, 'its own dim group stays bright');
    assert.ok(['x', 'y', 'w', 'h'].every((k) => Number.isInteger(item.rect[k])), 'whole px');
    assert.deepEqual(animationStage.focusRect?.(ctx, 'i1'), null, 'a label that has left');
    assert.equal(animationStage.focusRect?.(ctx, 'nope'), null);
    const panelRect = /** @type {any} */ (animationStage.focusRect?.(ctx, 'panel'));
    assert.equal(panelRect.kind, 'shape', 'B2c review CG-1: the outline runs round the animation, never across it');
    assert.deepEqual(panelRect.el, v.mediaGroup);
    // a term of an item points at its item (the catalogue lets look_here name one: B2c review CG-5)
    assert.deepEqual(animationStage.focusRect?.(ctx, 'i3:term:0')?.rect, item.rect);
    const groups = /** @type {Element[]} */ (animationStage.dimGroups?.(ctx));
    assert.equal(groups.length, 2 + v.labels.length, 'the animation, the title, every label');
    await m.stage.seek(frameAt(tl, 2));
    assert.deepEqual(animationStage.dimGroups?.(m.ctx()), groups, 'the same elements at every t');
    for (const e of cueEvents(tl.scenes[0], 'animation_stage')) {
      if (e.kind !== 'cue_term') continue;
      // cueEvents gives a cue's own `start` (cues.js CueEvent), not the raw event's `t`
      await m.stage.seek(frameAt(tl, e.start + 0.3));
      assert.ok(termSpans(animationStage, m.ctx(), e).length > 0, `a .rt-k span for ${e.target}`);
    }
    m.done();
  } finally {
    undo();
  }
});

test('texts(): the title and every step label with their slot, size, minimum and weight; overflow is reported', async () => {
  const undo = layoutModel();
  try {
    const tl = load('animation_stage.landscape.light.json');
    const m = mount(tl);
    await m.stage.seek(frameAt(tl, 8));
    const entries = /** @type {any[]} */ (animationStage.texts?.(m.ctx()) || []);
    const title = entries.find((e) => e.slot === 'title');
    assert.deepEqual([title.role, title.critical, title.weight, title.min],
      ['title', true, 700, LAYOUT.landscape.title.min]);
    const labels = entries.filter((e) => e.slot === 'label');
    assert.equal(labels.length, 3);
    assert.ok(labels.every((e) => e.role === 'label' && e.critical && e.min === LAYOUT.landscape.label.min));
    assert.ok(labels.every((e) => e.bg === m.ctx().onBackground.bg), 'on the background, never over the clip');
    assert.ok(labels.every((e) => e.overflow === 0), 'the fixture fits');
    assert.equal(entries.find((e) => e.slot === 'label' && e.text.includes('voltage'))?.weight, 700, 'a takeaway is bold');
    m.done();
    // a step label no size can fit reports its overflow, so preflight blocks with "Shorten text" (§7.12)
    const long = edited('animation_stage.landscape.light.json', (s) => {
      s.board[0].text = 'word '.repeat(90).trim();
    });
    const o = mount(long);
    await o.stage.seek(frameAt(long, 2));
    const over = /** @type {any[]} */ (animationStage.texts?.(o.ctx()) || []);
    assert.equal(o.view().labelSize, LAYOUT.landscape.label.min, 'every label down to the slot minimum');
    assert.ok(over.filter((e) => e.slot === 'label').some((e) => e.overflow > 0));
    o.done();
  } finally {
    undo();
  }
});

test('a scene this template does not draw is drawn by board_generic, every hook handed on', async () => {
  const undo = layoutModel();
  try {
    // a simulation whose Manim render failed: no medium, a board of bullets from the visual cues
    const tl = edited('animation_stage.landscape.json', (s) => {
      s.media = null;
      s.layout = { mascot_position: 'hidden', show_side_panel: false, fullscreen_media: false };
      s.board = s.beats.map((/** @type {any} */ b, /** @type {number} */ i) => ({ id: `cue-${i + 1}`, kind: 'bullet', text: b.visual_cue }));
      s.beats = s.beats.map((/** @type {any} */ b, /** @type {number} */ i) => ({ ...b, board_item_id: `cue-${i + 1}` }));
      s.events = [];
    });
    const m = mount(tl);
    await m.stage.seek(frameAt(tl, 5));
    const ctx = m.ctx();
    assert.equal(ctx.view.asFallback, true);
    assert.ok(ctx.root.querySelector('.dyn-board'), 'board_generic drew the scene');
    const texts = /** @type {any[]} */ (animationStage.texts?.(ctx) || []);
    assert.ok(texts.length > 0, 'and reports its own text to preflight');
    for (const item of tl.scenes[0].board) {
      assert.ok(texts.some((e) => (e.text || '') === item.text), `nothing is dropped: ${item.text}`);
    }
    assert.deepEqual(animationStage.focusRect?.(ctx, 'cue-1'), boardGeneric.focusRect?.(ctx, 'cue-1'));
    assert.deepEqual(animationStage.itemEl?.(ctx, 'cue-1'), boardGeneric.itemEl?.(ctx, 'cue-1'));
    assert.deepEqual(animationStage.dimGroups?.(ctx), boardGeneric.dimGroups?.(ctx));
    assert.deepEqual(animationStage.tex?.(tl.scenes[0], tl.scenes[0].look, 'landscape'),
      boardGeneric.tex?.(tl.scenes[0], tl.scenes[0].look, 'landscape'));
    assert.deepEqual(animationStage.mediaBox?.(tl.scenes[0], ZONES.landscape, 'render', 'main', { safe: false, slots: {} }),
      boardGeneric.mediaBox?.(tl.scenes[0], ZONES.landscape, 'render', 'main', { safe: false, slots: {} }));
    m.done();
    assert.deepEqual(await orderProblems(tl, [0, 30, 90, 150, 240, 299], { templates: TEMPLATES }), []);
  } finally {
    undo();
  }
});
