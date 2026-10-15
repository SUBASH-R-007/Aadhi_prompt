// @ts-check
// templates/formula_focus.js (DYNAMIC_VIDEO_DESIGN §4.3 B, R-30, R-55, R-59; B2 plan §A step 3, the B2c pilot): its
// fixtures through the template contract and the order-independence check (both formats; dark and light families;
// typical, lint-limit, a very long word, Tamil, Hindi, mixed Tamil + English + TeX), the scene data it takes (else
// board_generic draws the scene), the TeX it declares (the 9:16 "=" break included), the entity tints, braces, legend
// rows and box on their events, the look_here geometry of the R-16 walk, the hold freeze, the slots it reports to
// preflight and their parity with the catalogue. jsdom has no layout: geometry is stubbed where a test needs it; the
// real-Chromium checks are tests/determinism/test_template_fixtures.py and test_formula_focus_page.py.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { TYPE_FLOORS, ZONES, templateBehaviour, templateSlots } from '../../../js/dynamic/core.js';
import { cueEvents } from '../../../js/dynamic/cues.js';
import { svg } from '../../../js/shared/dom.js';
import { composite, contrastRatio } from '../../../js/motion/color.js';
import { REVEAL_LEAD } from '../../../js/dynamic/state.js';
import { TINT_BASE } from '../../../js/dynamic/templates/_shared.js';
import template, {
  BOX_PX, BRACE_DRAW, BRACE_EXIT, BRACE_GAP, BRACE_HOLD, FORMULA_FADE, FORMULA_SLOT, LAYOUT, LINE_HEIGHT, ROW_SECONDS,
  TINT_SECONDS, TITLE_TRACKING, boxDrawAt, boxPath, braceBands, braceEnds, bracePath, bracePlace, breakAtEquals,
  brokenTex, fitsScene, formulaTex, geometry, slotWidths, texOf, variableIndex, variableTimes,
} from '../../../js/dynamic/templates/formula_focus.js';
import { contractProblems, orderProblems, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const FIXTURES = ['landscape', 'portrait', 'landscape.light', 'portrait.light', 'landscape.stress', 'portrait.stress']
  .map((c) => `formula_focus.${c}.json`);
const FPS = 30;
/** @param {number} t */
const frame = (t) => Math.round(t * FPS);
const ID = 'formula_focus';

/**
 * A MathJax stand-in that draws what the tint check needs: an `mjx-container` holding an SVG with one `<g>` per
 * `\color{#hex}` group of the TeX (fill and stroke the placeholder, as MathJax's mstyle has them, plan §P (g)).
 */
function fakeMathJax() {
  /** @type {{tex: string, display: boolean}[]} */
  const seen = [];
  return {
    seen,
    /** @param {string} tex @param {boolean} display */
    renderTex: (tex, display) => {
      seen.push({ tex, display });
      const box = document.createElement('mjx-container');
      box.setAttribute('display', display ? 'true' : 'false');
      const pic = svg('svg', { fill: 'currentColor', stroke: 'currentColor' });
      for (const m of tex.matchAll(/\\color\{(#[0-9A-Fa-f]{6})\}/g)) {
        pic.appendChild(svg('g', { fill: m[1], stroke: m[1] }, svg('path', { d: 'M0 0' })));
      }
      box.appendChild(pic);
      return Promise.resolve(/** @type {Element} */ (box));
    },
  };
}

/**
 * A render-mode stage of `timeline` drawing formula_focus through the stage's test seam; `ctx(i)` is scene i's context.
 * @param {any} timeline @param {{renderTex?: any, mode?: 'live' | 'render', reducedMotion?: boolean}} [opts]
 */
function staged(timeline, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = /** @type {any} */ ({ ...template, build(/** @type {any} */ ctx) {
    contexts.set(ctx.index, ctx);
    return template.build(ctx);
  } });
  const run = renderStage(timeline, { templates: { [ID]: wrapped }, renderTex: (opts.renderTex || fakeMathJax()).renderTex, mode: opts.mode, reducedMotion: opts.reducedMotion });
  return { ...run, ctx: (i = 0) => contexts.get(i), seekT: (/** @type {number} */ t) => run.stage.seek(frame(t)) };
}

/** Stub an element's box (stage px; jsdom has no layout). @param {Element} el @param {{x: number, y: number, w: number, h: number}} r */
function stubRect(el, r) {
  /** @type {any} */ (el).getBoundingClientRect = () => ({ left: r.x, top: r.y, width: r.w, height: r.h, right: r.x + r.w, bottom: r.y + r.h, x: r.x, y: r.y });
}

/** The fixture's frames worth comparing: a spread over the first 300 and around every event. @param {any} tl */
function framesOf(tl) {
  const total = Math.min(300, Math.round(Number(tl.total_duration) * FPS));
  const out = new Set([0, total - 1]);
  for (let f = 0; f < total; f += 25) out.add(f);
  for (const s of tl.scenes) {
    for (const e of s.events || []) {
      for (const dt of [0, 0.15, 0.5]) {
        const f = frame(Number(s.start) + e.t + dt);
        if (f < total) out.add(f);
      }
    }
  }
  return [...out].sort((a, b) => a - b);
}

// --- the fixtures through the contract and the order check ----------------------------------------------------------

for (const name of FIXTURES) {
  test(`${name}: the template contract and order independence (forward, reverse, fresh)`, async () => {
    const tl = load(name);
    assert.ok(tl.scenes.every((/** @type {any} */ s) => s.look.template === ID && s.look.background.frozen_at === 0),
      'every scene is drawn by formula_focus, its background frozen from the scene start (R-55)');
    assert.deepEqual(await contractProblems(template, tl), []);
    assert.deepEqual(await orderProblems(tl, framesOf(tl), { templates: { [ID]: template } }), []);
  });
}

test('the fixtures cover both formats, a dark and a light family, both R1 personalities and every text case', () => {
  const all = FIXTURES.map(load);
  assert.deepEqual(new Set(all.map((t) => t.presentation.format)), new Set(['landscape', 'portrait']));
  assert.deepEqual(new Set(all.map((t) => t.presentation.mode)), new Set(['dark', 'light']));
  assert.deepEqual(new Set(all.map((t) => t.presentation.text_entrance)), new Set(['fade_rise', 'baseline_reveal']));
  const text = JSON.stringify(all);
  assert.match(text, /[஀-௿]/u, 'Tamil');
  assert.match(text, /[ऀ-ॿ]/u, 'Devanagari');
  assert.ok(all.some((t) => t.scenes.some((/** @type {any} */ s) => s.board.some((/** @type {any} */ i) => i.kind === 'heading' && /[஀-௿]/u.test(i.text) && /[A-Za-z]/.test(i.text) && i.text.includes('$')))), 'mixed Tamil + English + TeX');
  assert.ok(all.some((t) => t.scenes.some((/** @type {any} */ s) => /\S{40,}/.test(s.title))), 'one very long word');
  assert.ok(all.some((t) => t.scenes.some((/** @type {any} */ s) => s.board.some((/** @type {any} */ i) => (i.variables || []).length >= LAYOUT.landscape.twoColumns))), 'a two-column legend');
});

// --- the scene data (§4.3; templates.py _fit_formula_focus) ---------------------------------------------------------

const FORMULA = { id: 'f', kind: 'formula', latex: 'v = v_0 + a t', variables: [{ symbol_latex: 'v', meaning: 'final velocity', unit: 'm/s' }] };

test('fitsScene: one formula with LaTeX, at most one heading, nothing else, no shown side panel', () => {
  const scene = (/** @type {any} */ extra) => /** @type {any} */ ({ board: [FORMULA], ...extra });
  assert.ok(fitsScene(scene({})));
  assert.ok(fitsScene(scene({ board: [{ id: 'h', kind: 'heading', text: 'H' }, FORMULA] })));
  assert.ok(!fitsScene(scene({ board: [{ id: 'h', kind: 'heading', text: 'H' }, { id: 'h2', kind: 'heading', text: 'H' }, FORMULA] })));
  assert.ok(!fitsScene(scene({ board: [FORMULA, { id: 'b', kind: 'bullet', text: 'x' }] })));
  assert.ok(!fitsScene(scene({ board: [FORMULA, { ...FORMULA, id: 'g' }] })));
  assert.ok(!fitsScene(scene({ board: [{ ...FORMULA, latex: '  ' }] })), 'no LaTeX');
  assert.ok(!fitsScene(scene({ board: [] })));
  assert.ok(!fitsScene(scene({ side_panel: { kind: 'figure' } })), 'a side panel would be dropped');
  assert.ok(fitsScene(scene({ side_panel: { kind: 'figure' }, layout: { show_side_panel: false } })), 'a hidden one is not drawn anyway');
  assert.ok(!fitsScene(scene({ quiz: {} })));
  assert.ok(!fitsScene(scene({ media: { kind: 'video' } })));
  assert.ok(!fitsScene(scene({ poster: { kind: 'image' } })));
});

test('a scene it cannot draw whole is drawn by board_generic, every hook handed on', async () => {
  const tl = load('formula_focus.landscape.json');
  tl.scenes[0].board.push({ id: 'b9', kind: 'bullet', text: 'An extra bullet' });
  const run = staged(tl);
  await run.seekT(2);
  assert.ok(run.stage.el.querySelector('.dyn-board'), 'board_generic drew it');
  assert.equal(run.stage.el.querySelector('.ff'), null);
  const ctx = run.ctx();
  assert.ok(template.texts && template.texts(ctx).some((e) => e.slot === 'body'), 'board_generic texts');
  assert.deepEqual(template.tex && template.tex(tl.scenes[0], tl.scenes[0].look, 'landscape').map((x) => x.tex), ['v = v_0 + a t', 'v', 'v_0', 'a', 't', 'v_0'], 'board_generic tex()');
  assert.deepEqual(template.dimGroups && template.dimGroups(ctx).length, 4, 'the title block and three cards');
  assert.equal(template.mediaBox && JSON.stringify(template.mediaBox(tl.scenes[0], ZONES.landscape, 'render', 'main', { safe: false, slots: {} })), JSON.stringify({ w: 1728, h: 600 }));
  run.done();
});

// --- TeX (plan §1.4, §2: tex() declares what build converts; §P (g) tints; §4.3 the 9:16 break) -----------------------

test('breakAtEquals: the top-level "=" signs only, never inside a group, a \\left pair or an environment', () => {
  assert.deepEqual(breakAtEquals('W = \\Delta K = \\frac{1}{2} m v^2'), ['W', '\\Delta K', '\\frac{1}{2} m v^2']);
  assert.deepEqual(breakAtEquals('\\frac{a=b}{c} = d'), ['\\frac{a=b}{c}', 'd']);
  assert.deepEqual(breakAtEquals('\\left( x = y \\right) = z'), ['\\left( x = y \\right)', 'z']);
  assert.deepEqual(breakAtEquals('\\begin{cases} x = 1 \\end{cases} = y'), ['\\begin{cases} x = 1 \\end{cases}', 'y']);
  assert.deepEqual(breakAtEquals('x^= y'), null, "a script's =");
  assert.equal(breakAtEquals('F + m a'), null, 'no "="');
  assert.equal(breakAtEquals('a &= b \\\\ &= c'), null, 'already aligned');
  assert.equal(breakAtEquals('= 5'), null, 'an empty side');
  assert.equal(breakAtEquals('a = = b'), null);
  assert.deepEqual(breakAtEquals('\\text{a = b} = c'), ['\\text{a = b}', 'c']);
});

test('brokenTex: aligned on "=", each part tinted on its own (no placeholder in the environment\'s markup)', () => {
  const item = /** @type {any} */ ({ id: 'f', kind: 'formula', latex: 'a = b = c', variables: [{ symbol_latex: 'a' }, { symbol_latex: 'c' }] });
  const ph = (/** @type {number} */ k) => `${TINT_BASE}${k.toString(16).toUpperCase().padStart(2, '0')}`;
  // every line starts at the alignment point: the left side alone, then "= ..." under it (no staircase, which would
  // keep the broken form as wide as the whole one)
  assert.equal(brokenTex(item), `\\begin{aligned}&{\\color{${ph(0)}}a} \\\\ &= b \\\\ &= {\\color{${ph(1)}}c}\\end{aligned}`);
  assert.equal(brokenTex({ ...item, latex: 'a + c' }), null);
  // the same placeholders as the whole formula's (one colour per symbol whatever form is shown)
  assert.deepEqual(formulaTex(item).tex, `{\\color{${ph(0)}}a} = b = {\\color{${ph(1)}}c}`);
});

test('texOf: the formula (display, tinted), 9:16 its broken form, the symbols, the TeX of the heading and sentence', () => {
  const scene = /** @type {any} */ ({
    title: 'A $title$ is plain', board: [{ id: 'h', kind: 'heading', text: 'Heading $x$' },
      { id: 'f', kind: 'formula', latex: 'v = a t', text: 'Grows by $a t$', variables: [{ symbol_latex: 'v' }, { symbol_latex: 'a' }] }],
  });
  const tinted = formulaTex(scene.board[1]).tex;
  const land = texOf(scene, 'landscape');
  assert.deepEqual(land, [{ tex: 'x', display: false }, { tex: tinted, display: true }, { tex: 'a t', display: false }, { tex: 'v', display: false }, { tex: 'a', display: false }]);
  const port = texOf(scene, 'portrait');
  assert.deepEqual(port.filter((x) => x.display).map((x) => x.tex), [tinted, /** @type {string} */ (brokenTex(scene.board[1]))]);
  assert.equal(template.tex && template.tex(scene, null, 'landscape').length, land.length);
  assert.deepEqual(texOf(/** @type {any} */ ({ board: [] }), 'landscape'), []);
});

test('every TeX string build converts is one tex() declares, in both formats (render.js converts them before ready)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const tex = fakeMathJax();
    const run = staged(tl, { renderTex: tex });
    for (const s of run.stage.info().scenes) await run.stage.seek(s.f0);
    const fmt = tl.presentation.format;
    const declared = new Set(tl.scenes.flatMap((/** @type {any} */ s) => texOf(s, fmt)).map((x) => `${x.display}:${x.tex}`));
    assert.ok(tex.seen.length > 0);
    for (const x of tex.seen) assert.ok(declared.has(`${x.display}:${x.tex}`), `${name}: ${x.tex}`);
    run.done();
  }
});

// --- when things happen (events.py's var / draw events; B1's sync cues until then) ----------------------------------

test('variableTimes: the earliest var event per symbol (as written, else without spaces), else B1\'s sync cue, never before the formula', () => {
  const item = /** @type {any} */ ({ id: 'f', variables: [{ symbol_latex: '\\Delta V' }, { symbol_latex: 'I' }, { symbol_latex: 'R' }, { symbol_latex: 'q' }] });
  const scene = /** @type {any} */ ({
    events: [
      { t: 3, kind: 'var', target: 'f:var:\\DeltaV' }, { t: 2.5, kind: 'var', target: 'f:var:\\Delta V' },
      { t: 1, kind: 'var', target: 'f:var:I' }, { t: 0.5, kind: 'var', target: 'g:var:R' }, { t: 0.2, kind: 'var', target: 'f:var:Z' },
      { t: 0.1, kind: 'look_here', target: 'f:var:R' }, { t: 'x', kind: 'var', target: 'f:var:R' }, { t: 0.3, kind: 'var', target: 'f:var: R' },
    ],
    sync_cues: [{ kind: 'var', start: 4, item_id: 'f', part: 'var:2' }, { kind: 'var', start: 5, item_id: 'f', part: 'var:1' }],
    beats: [],
  });
  assert.deepEqual(variableTimes(scene, item, 0.8), [{ at: 2.5 }, { at: 1 }, { at: 4 - REVEAL_LEAD }, { at: null }]);
  assert.deepEqual(variableTimes(scene, item, 1.5).map((x) => x.at), [2.5, 1.5, 4 - REVEAL_LEAD, null], 'clamped to the formula entrance');
  assert.equal(variableIndex(item, '\\DeltaV'), 0);
  assert.equal(variableIndex(item, 'Z'), -1);
});

test('boxDrawAt: the earliest draw event on the formula item, never before its entrance; none: null', () => {
  const item = /** @type {any} */ ({ id: 'f' });
  const scene = (/** @type {any[]} */ events) => /** @type {any} */ ({ events });
  assert.equal(boxDrawAt(scene([{ t: 5, kind: 'draw', target: 'f' }, { t: 4, kind: 'draw', target: 'f' }]), item, 1), 4);
  assert.equal(boxDrawAt(scene([{ t: 0.2, kind: 'draw', target: 'f' }]), item, 1), 1);
  assert.equal(boxDrawAt(scene([{ t: 2, kind: 'draw', target: 'f:var:v' }, { t: 2, kind: 'draw', target: 'g' }, { t: 2, kind: 'reveal', target: 'f' }]), item, 1), null);
});

// --- painting (the typical fixture: Soft, x1.15) --------------------------------------------------------------------

/** The typical 16:9 fixture staged with the MathJax stand-in, laid out with stubbed boxes for the formula and symbols. */
async function typical(/** @type {string} */ name = 'formula_focus.landscape.json') {
  const tl = load(name);
  const run = staged(tl);
  await run.seekT(0);
  const ctx = run.ctx();
  const v = ctx.view;
  stubRect(v.texEl, { x: 700, y: 400, w: 520, h: 110 });
  [...v.texEl.querySelectorAll('g[fill], g[data-dyn-tint]')].forEach((g, i) => stubRect(g, { x: 700.4 + 100 * i, y: 410, w: 60.2, h: 90 }));
  stubRect(v.card, { x: 600, y: 360, w: 720, h: 300 });
  /** @type {any} */ (template).layout(ctx);
  return { tl, run, ctx, v, scene: tl.scenes[0] };
}

test('the card fades in on the formula\'s entrance (its reveal event; §4.3: 400 ms, the personality\'s scale)', async () => {
  const { run, v, scene, ctx } = await typical();
  const reveal = scene.events.find((/** @type {any} */ e) => e.kind === 'reveal').t;
  assert.equal(v.enter, reveal);
  await run.seekT(reveal - 0.1);
  assert.equal(Number(v.card.style.opacity), 0);
  assert.equal(v.card.style.visibility, 'hidden');
  await run.seekT(reveal + 0.5 * FORMULA_FADE * ctx.motion.scale);
  assert.ok(Number(v.card.style.opacity) > 0 && Number(v.card.style.opacity) < 1);
  await run.seekT(reveal + FORMULA_FADE * ctx.motion.scale + 0.05);
  assert.equal(Number(v.card.style.opacity), 1);
  run.done();
});

test('tints (§4.7, R-30): each symbol from ink to its lesson-wide entity colour on its var cue; a symbol without a slot stays ink', async () => {
  const { run, v, scene, ctx } = await typical();
  const roles = ctx.roles;
  const at = (/** @type {string} */ sym) => scene.events.find((/** @type {any} */ e) => e.kind === 'var' && e.target === `f:var:${sym}`).t;
  const fills = (/** @type {string} */ sym) => (v.targets.get(sym) || []).map((/** @type {Element} */ g) => g.getAttribute('fill'));
  assert.deepEqual([...v.targets.keys()], ['v', 'v_0', 'a', 't']);
  assert.ok(v.targets.get('v').length >= 1);
  const scale = ctx.motion.scale;
  await run.seekT(at('v_0') - 0.05);
  assert.deepEqual(fills('v_0'), ['currentColor'], 'before its cue: the ink around it');
  assert.deepEqual(fills('v'), [roles.color('entity:0')], 'v is tinted by now');
  await run.seekT(at('v_0') + 0.5 * TINT_SECONDS * scale);
  const mid = fills('v_0')[0];
  assert.match(String(mid), /^#[0-9A-F]{6}$/i);
  assert.notEqual(mid, roles.color('entity:1'));
  assert.notEqual(mid, roles.color('ink'));
  await run.seekT(at('v_0') + TINT_SECONDS * scale + 0.05);
  assert.deepEqual(fills('v_0'), [roles.color('entity:1')]);
  await run.seekT(9.5);
  assert.deepEqual([fills('v'), fills('v_0'), fills('a'), fills('t')], [[roles.color('entity:0')], [roles.color('entity:1')], [roles.color('entity:2')], ['currentColor']],
    'the fourth quantity has no entity slot: it stays ink');
  // the stroke follows the fill (MathJax strokes its glyph outlines in the same colour)
  assert.equal(v.targets.get('a')[0].getAttribute('stroke'), roles.color('entity:2'));
  run.done();
});

test('braces (§4.3): drawn under the symbol on its cue (300 ms), gone at the next symbol\'s cue; the last at the walk\'s first look_here', async () => {
  const { run, v, scene, ctx } = await typical();
  const scale = ctx.motion.scale;
  const brace = (/** @type {number} */ k) => v.braces.find((/** @type {any} */ b) => b.index === k);
  const b0 = brace(0);
  assert.equal(v.braces.length, 4);
  assert.ok(b0.drawn);
  // under the first symbol's own glyphs (x 700.4 + 60.2 rounded, bottom 410 + 90), BRACE_GAP below them
  assert.equal(b0.path.getAttribute('d'), bracePath(700, 761, 500 + BRACE_GAP, Math.round(0.24 * v.formulaSize)));
  assert.equal(b0.path.getAttribute('stroke'), ctx.roles.color('entity:0'), 'in the symbol\'s colour');
  assert.equal(brace(3).path.getAttribute('stroke'), ctx.roles.color('ink2'), 'a symbol without a slot: ink2');
  const atV = scene.events.find((/** @type {any} */ e) => e.target === 'f:var:v').t;
  const atV0 = scene.events.find((/** @type {any} */ e) => e.target === 'f:var:v_0').t;
  await run.seekT(atV - 0.05);
  assert.equal(b0.path.style.visibility, 'hidden');
  await run.seekT(atV + 0.5 * BRACE_DRAW * scale);
  assert.equal(b0.path.style.visibility, 'visible');
  const half = Number(b0.path.getAttribute('stroke-dashoffset'));
  assert.ok(half > 0 && half < 1, `drawing: ${half}`);
  await run.seekT(atV + BRACE_DRAW * scale + 0.05);
  assert.equal(b0.path.getAttribute('stroke-dashoffset'), '0.0000');
  assert.equal(b0.path.getAttribute('pathLength'), '1');
  await run.seekT(atV0 + BRACE_EXIT * scale + 0.05);
  assert.equal(b0.path.style.visibility, 'hidden', 'the next symbol\'s cue ends it');
  assert.equal(brace(1).path.style.visibility, 'visible');
  // the last symbol's brace (t) leaves at the walk's first look_here (B2c review V3: never a second pointer beside the
  // walk's outline, never a lone brace through the hold)
  const atT = scene.events.find((/** @type {any} */ e) => e.target === 'f:var:t').t;
  const walk = scene.events.find((/** @type {any} */ e) => e.kind === 'look_here').t;
  assert.ok(walk > atT && walk < atT + BRACE_HOLD);
  assert.equal(brace(3).until, walk);
  await run.seekT(walk - 0.05);
  assert.equal(brace(3).path.style.visibility, 'visible');
  await run.seekT(walk + BRACE_EXIT * scale + 0.05);
  assert.equal(brace(3).path.style.visibility, 'hidden', 'gone once the walk starts');
  await run.seekT(scene.duration - 0.05);
  assert.ok(v.braces.every((/** @type {any} */ b) => b.path.style.visibility === 'hidden'), 'no brace through the hold');
  run.done();
});

test('braceEnds: the next symbol\'s cue, else the first look_here after the cue, the last at most BRACE_HOLD', () => {
  const cued = [{ at: 1 }, { at: 3 }, { at: 3 }, { at: 6 }];
  const look = (/** @type {number} */ t) => ({ kind: 'look_here', t });
  assert.deepEqual(braceEnds(cued, []), [3, 6, 6, 6 + BRACE_HOLD]);
  assert.deepEqual(braceEnds(cued, [look(2), look(7)]), [2, 6, 6, 7], 'a look_here before the next cue ends it first');
  assert.deepEqual(braceEnds(cued, [look(1), { kind: 'cue_term', t: 2 }, { kind: 'look_here', t: Number.NaN }]), [3, 6, 6, 6 + BRACE_HOLD],
    'only look_here cues strictly after the brace\'s own cue');
  assert.deepEqual(braceEnds([], [look(1)]), []);
});

test('bracePlace (B2c review V1): at the symbol\'s own glyphs: under them, over a numerator, none when boxed in', () => {
  const bh = 20;
  const reach = 2 * BRACE_GAP + bh;
  // I = V / R: I at the axis, V over the bar, R under it (stage px)
  const I = { x: 700, y: 450, w: 40, h: 60 };
  const V = { x: 820, y: 400, w: 50, h: 60 };
  const bar = { x: 810, y: 470, w: 70, h: 6 };
  const R = { x: 820, y: 490, w: 50, h: 60 };
  const eq = { x: 760, y: 470, w: 40, h: 20 };
  assert.deepEqual(bracePlace(I, [V, bar, R, eq], bh), { dir: 1, x0: 700, x1: 740, y: 510 + BRACE_GAP }, 'I: under its own glyph, not the formula\'s bottom');
  assert.deepEqual(bracePlace(V, [I, bar, R, eq], bh), { dir: -1, x0: 820, x1: 870, y: 400 - BRACE_GAP }, 'V: over it (the bar is under it)');
  assert.deepEqual(bracePlace(R, [I, V, bar, eq], bh), { dir: 1, x0: 820, x1: 870, y: 550 + BRACE_GAP });
  // a numerator whose bar is further below than a brace's reach still takes the open side, over it (a brace between
  // it and the bar would read as part of the fraction)
  assert.equal(bracePlace(V, [{ ...bar, y: 460 + reach + 6 }], bh)?.dir, -1);
  // the middle of a stack takes the side with room, under first; boxed in above and below: none
  const mid = { x: 820, y: 400 + 60 + reach - 10, w: 50, h: 40 };
  assert.equal(bracePlace(mid, [V, { ...R, y: mid.y + 40 + 10 }], bh), null);
  assert.equal(bracePlace(mid, [V, { ...R, y: mid.y + 40 + reach + 2 }], bh)?.dir, 1);
  assert.equal(bracePlace(mid, [{ ...V, y: V.y - 20 }, { ...R, y: mid.y + 40 + 10 }], bh)?.dir, -1);
  // a glyph merely touching the span's edge (<= 1 px of overlap) does not block it
  assert.equal(bracePlace(V, [{ x: 869, y: 470, w: 30, h: 30 }], bh)?.dir, 1);
  // the bands the card keeps for them past the formula's box (I = V / R: 400-550)
  const f = { x: 700, y: 400, w: 180, h: 150 };
  const places = [bracePlace(I, [V, bar, R, eq], bh), bracePlace(V, [I, bar, R, eq], bh), bracePlace(R, [I, V, bar, eq], bh)];
  assert.deepEqual(braceBands(f, places, bh), { above: reach, below: reach });
  assert.deepEqual(braceBands(f, [places[0]], bh), { above: 0, below: 0 }, 'I\'s brace stays inside the formula\'s box');
  assert.deepEqual(braceBands(f, [null], bh), { above: 0, below: 0 });
  // the over-brace's path: its ends at y, its tip bh above
  assert.match(bracePath(820, 870, 392, bh, -1), /Q845\.0 382\.0 845\.0 372\.0/);
});

test('a stacked formula: each brace points at its own symbol (V over the fraction, I right under I), the card keeps bands', async () => {
  const tl = load('formula_focus.landscape.light.json');
  const run = staged(tl);
  await run.seekT(0);
  const ctx = run.ctx();
  const v = ctx.view;
  assert.equal(v.item.latex.replace(/\s+/g, ''), 'I=\\frac{V}{R}');
  const order = v.tints.map((/** @type {any} */ t) => t.symbol);
  const groups = [...v.texEl.querySelectorAll('g[fill], g[data-dyn-tint]')];
  const boxes = /** @type {Record<string, {x: number, y: number, w: number, h: number}>} */ ({ I: { x: 700, y: 450, w: 40, h: 60 }, V: { x: 820, y: 400, w: 50, h: 60 }, R: { x: 820, y: 490, w: 50, h: 60 } });
  groups.forEach((g, i) => {
    stubRect(g, boxes[order[i]]);
    for (const leaf of g.querySelectorAll('path')) stubRect(leaf, boxes[order[i]]);
  });
  // the fraction bar: a rule of the picture outside every symbol
  const bar = svg('rect', { width: '10', height: '1' });
  v.texEl.querySelector('svg')?.appendChild(bar);
  stubRect(bar, { x: 810, y: 470, w: 70, h: 6 });
  stubRect(v.texEl, { x: 700, y: 400, w: 180, h: 150 });
  /** @type {any} */ (template).layout(ctx);
  const bh = Math.max(12, Math.min(28, Math.round(0.24 * v.formulaSize)));
  const d = (/** @type {string} */ sym) => v.braces.find((/** @type {any} */ b) => v.item.variables[b.index].symbol_latex === sym).path.getAttribute('d');
  assert.equal(d('V'), bracePath(820, 870, 400 - BRACE_GAP, bh, -1), 'V: an over-brace above V, never under R');
  assert.equal(d('I'), bracePath(700, 740, 510 + BRACE_GAP, bh, 1), 'I: right under I');
  assert.equal(d('R'), bracePath(820, 870, 550 + BRACE_GAP, bh, 1));
  // above: the reach less what the card's top padding gives while keeping BRACE_GAP clear of the box's stroke
  const above = 2 * BRACE_GAP + bh + BOX_PX - LAYOUT.landscape.card.padY;
  assert.ok(above > 0);
  assert.deepEqual(v.bands, { above, below: 2 * BRACE_GAP + bh });
  assert.equal(v.bandAbove.style.height, `${above}px`, 'room in the card above the formula');
  assert.equal(v.band.style.height, `${2 * BRACE_GAP + bh}px`, 'and below it, before the sentence');
  run.done();
});

test('legend rows enter on their cues; the box draws on the draw event; rows with no cue come with the formula', async () => {
  const { run, v, scene, ctx } = await typical();
  const scale = ctx.motion.scale;
  const ev = (/** @type {string} */ kind, /** @type {string} */ target) => scene.events.find((/** @type {any} */ e) => e.kind === kind && e.target === target).t;
  await run.seekT(ev('var', 'f:var:a') - 0.05);
  assert.deepEqual(v.rows.map((/** @type {any} */ r) => Number(r.row.style.opacity)), [1, 1, 0, 0]);
  await run.seekT(ev('var', 'f:var:a') + ROW_SECONDS * scale + 0.05);
  assert.equal(Number(v.rows[2].row.style.opacity), 1);
  // the box
  await run.seekT(ev('draw', 'f') - 0.05);
  assert.equal(v.boxPathEl.style.visibility, 'hidden');
  await run.seekT(ev('draw', 'f') + 0.7 * scale);
  assert.equal(v.boxPathEl.style.visibility, 'visible');
  assert.equal(v.boxPathEl.getAttribute('stroke-dashoffset'), '0.0000');
  assert.equal(v.boxPathEl.getAttribute('stroke'), ctx.roles.color('struct.border'), 'a structure border (§4.7)');
  assert.equal(v.boxPathEl.getAttribute('stroke-width'), String(BOX_PX));
  run.done();
  // without var events or a draw event (the light fixture: B1's sync cues place the rows, the box comes with the card)
  const light = load('formula_focus.landscape.light.json');
  const r2 = staged(light);
  await r2.seekT(0);
  const v2 = r2.ctx().view;
  assert.equal(v2.boxAt, null);
  const sync = light.scenes[0].sync_cues.map((/** @type {any} */ c) => c.start - REVEAL_LEAD);
  assert.deepEqual(v2.rows.map((/** @type {any} */ r) => r.enter), sync);
  await r2.seekT(1.5);
  assert.equal(v2.boxPathEl.style.visibility, 'visible');
  assert.equal(v2.boxPathEl.getAttribute('stroke-dashoffset'), '0.0000');
  r2.done();
  // a variable no cue names enters with the formula, after its sentence
  const plain = load('formula_focus.landscape.json');
  plain.scenes[0].events = plain.scenes[0].events.filter((/** @type {any} */ e) => e.kind !== 'var');
  plain.scenes[0].sync_cues = [];
  const r3 = staged(plain);
  await r3.seekT(0);
  const v3 = r3.ctx().view;
  assert.ok(v3.rows.every((/** @type {any} */ r, /** @type {number} */ k) => r.at === null && r.enter > v3.enter && (k === 0 || r.enter > v3.rows[k - 1].enter)));
  assert.equal(v3.braces.length, 0, 'no cue, no brace');
  r3.done();
});

test('the headline takes the personality\'s text entrance: Crisp from the baseline in its mask, Soft a fade and rise', async () => {
  const soft = staged(load('formula_focus.landscape.json'));
  await soft.seekT(0.2);
  const vs = soft.ctx().view;
  assert.equal(vs.titleMask, null, 'Soft: no clip');
  assert.match(vs.title.style.transform, /^translate\(0(px)?, \d+px\)$/, 'a 2D translate (see paintEntrance)');
  soft.done();
  const crisp = staged(load('formula_focus.landscape.light.json'));
  await crisp.seekT(0.2);
  const vc = crisp.ctx().view;
  assert.ok(vc.titleMask && vc.titleMask.classList.contains('dyn-reveal-mask'));
  assert.ok(vc.rows.every((/** @type {any} */ r) => r.mask && r.mask.classList.contains('dyn-reveal-mask')));
  crisp.done();
  const calm = staged(load('formula_focus.landscape.json'), { mode: 'live', reducedMotion: true });
  await calm.seekT(0.2);
  calm.stage.update(0.2);
  assert.equal(calm.ctx().view.title.style.transform, 'none', 'reduced motion (live): a fade in place');
  calm.done();
});

// --- the R-16 walk: focusRect; dim groups --------------------------------------------------------------------------

test('focusRect: a symbol\'s first place in the formula (whole px, the card holds it), the card, the headline; none else', async () => {
  const { run, v, ctx } = await typical();
  const fr = /** @type {any} */ (template).focusRect;
  const sym = fr(ctx, 'f:var:v_0');
  assert.deepEqual(sym.rect, { x: 800, y: 410, w: 61, h: 90 });
  assert.equal(sym.shape, 'rect');
  assert.equal(sym.el, v.card);
  assert.equal(sym.kind, 'shape', 'a glyph in the card: its outline goes round it (cues.js shapeArea)');
  assert.deepEqual(sym.keep, [v.rows[1].slot], 'the symbol\'s own legend row stays bright');
  assert.equal(fr(ctx, 'f').kind, 'shape', 'the card');
  assert.equal(fr(ctx, 'f:var:v_0 '), null, 'outside the grammar (a symbol is never padded)');
  assert.deepEqual(fr(ctx, 'f').rect, { x: 600, y: 360, w: 720, h: 300 });
  assert.equal(fr(ctx, 'f').el, v.card);
  stubRect(v.title, { x: 96, y: 54, w: 900, h: 101 });
  assert.equal(fr(ctx, 'h').el, v.title);
  assert.equal(fr(ctx, 'h').kind, 'text');
  for (const t of ['f:var:zz', 'g:var:v', 'option:0', 'panel', 'f:line:0', 'nope', '']) assert.equal(fr(ctx, t), null, t);
  // a symbol the formula does not show: its legend row
  v.targets.set('t', []);
  stubRect(v.rows[3].row, { x: 400, y: 700, w: 300.6, h: 60 });
  assert.deepEqual(fr(ctx, 'f:var:t').rect, { x: 400, y: 700, w: 301, h: 60 });
  assert.equal(fr(ctx, 'f:var:t').el, v.rows[3].row);
  // the dim groups: the headline, the card (with its braces), each legend row; the same elements at every t
  const groups = /** @type {any} */ (template).dimGroups(ctx);
  assert.deepEqual(groups, [v.titleGroup, v.cardGroup, ...v.rows.map((/** @type {any} */ r) => r.slot)]);
  await run.seekT(7);
  assert.equal(/** @type {any} */ (template).dimGroups(ctx)[1], groups[1]);
  assert.ok(groups[1].contains(v.card) && groups[1].contains(v.bracesSvg));
  run.done();
});

test('the R-16 walk is drawn by cues.js (the catalogue consumes look_here on <item>:var): the symbol\'s own row stays bright', async () => {
  const { run, v, scene, ctx } = await typical();
  const walk = cueEvents(scene, ID).filter((e) => e.kind === 'look_here');
  assert.deepEqual(walk.map((e) => e.target), ['f:var:v', 'f:var:v_0']);
  await run.seekT(walk[0].start + 0.5);
  const outline = run.stage.el.querySelector('.dyn-cue-outline');
  assert.ok(outline && outline.getAttribute('d'));
  const dimmed = (/** @type {HTMLElement} */ el) => el.style.opacity !== '' && Number(el.style.opacity) < 1;
  assert.deepEqual(v.rows.map((/** @type {any} */ r) => dimmed(r.slot)), [false, true, true, true], 'v\'s row stays, the others dim');
  assert.equal(dimmed(v.titleGroup), true);
  assert.equal(v.cardGroup.style.opacity, '', 'the card holds the target');
  assert.equal(ctx.view, v);
  run.done();
});

// --- hold freeze (R-55): the background is pinned to the scene start ----------------------------------------------

test('the dense template\'s frozen background (R-55): the background paints the same at every t once the 600 ms ease is done', async () => {
  const bgHtml = async (/** @type {any} */ tl, /** @type {number} */ t) => {
    const run = staged(tl);
    await run.seekT(t);
    const html = /** @type {Element} */ (run.stage.el.querySelector('.dyn-scene')?.firstElementChild).outerHTML;
    run.done();
    return html;
  };
  const tl = load('formula_focus.landscape.json');
  assert.equal(await bgHtml(tl, 2), await bgHtml(tl, 8));
  const drifting = load('formula_focus.landscape.json');
  drifting.scenes[0].look.background.frozen_at = null;
  assert.notEqual(await bgHtml(drifting, 2), await bgHtml(drifting, 8), 'the check can fail: an unfrozen one drifts');
});

// --- what preflight is told (§7.12) --------------------------------------------------------------------------------

test('texts(): headline, formula, sentence and legend rows in their slots, critical, in the colours they are drawn in', async () => {
  const { run, v, ctx } = await typical();
  await run.seekT(9.5);
  const entries = /** @type {any} */ (template).texts(ctx);
  assert.deepEqual(entries.map((/** @type {any} */ e) => e.slot), ['title', 'formula', 'text', 'legend', 'legend', 'legend', 'legend']);
  assert.ok(entries.every((/** @type {any} */ e) => e.critical && /^#[0-9A-F]{6}$/i.test(e.ink) && /^#[0-9A-F]{6}$/i.test(e.bg)));
  const [title, formula, text, row] = entries;
  assert.deepEqual([title.role, title.ink, title.bg, title.weight, title.alpha], ['title', ctx.onBackground.ink, ctx.onBackground.bg, 700, 1]);
  assert.equal(title.text, 'Final velocity', 'the heading item is the headline (the scene title is "Uniform acceleration")');
  assert.deepEqual([formula.role, formula.ink, formula.bg, formula.el, formula.alpha], [FORMULA_SLOT.role, ctx.roles.color('ink'), ctx.roles.color('surface'), v.texEl, 1]);
  assert.equal(formula.text, 'v = v_0 + a t');
  assert.deepEqual([text.ink, text.bg, text.alpha, text.weight], [ctx.roles.color('ink'), ctx.roles.color('surface'), 1, LAYOUT.landscape.text.weight]);
  assert.deepEqual([row.role, row.ink, row.bg, row.alpha, row.text], ['label', ctx.onBackground.ink, ctx.onBackground.bg, 1, 'v final velocity (m/s)']);
  assert.equal(row.el.querySelector('.ff-meaning').textContent, 'final velocity (m/s)', 'the unit runs on after the meaning');
  // each legend symbol in its entity colour (R-30), a symbol without a slot in the legend's ink
  const rgb = (/** @type {string} */ hex) => `rgb(${[1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;
  assert.deepEqual(v.rows.map((/** @type {any} */ r) => r.row.querySelector('.ff-sym').style.color),
    [rgb(ctx.roles.color('entity:0')), rgb(ctx.roles.color('entity:1')), rgb(ctx.roles.color('entity:2')), rgb(ctx.onBackground.ink)]);
  const L = LAYOUT.landscape;
  assert.deepEqual([title.size, title.min, formula.size, formula.min, text.size, text.min, row.size, row.min],
    [L.title.max, L.title.min, L.formula.max, L.formula.min, L.text.max, L.text.min, L.legend.max, L.legend.min], 'jsdom: everything fits at its largest');
  assert.ok(entries.every((/** @type {any} */ e) => e.overflow === 0));
  // the sentence is inside the card: both opacities count
  await run.seekT(0.5);
  const early = /** @type {any} */ (template).texts(ctx);
  assert.ok(early[2].alpha <= early[1].alpha);
  run.done();
});

test('preflight\'s remedies: a slot\'s size cap, the safe layout (9:16: the narrow card) and a scrim behind text on the background', async () => {
  const tl = load('formula_focus.portrait.json');
  tl.scenes[0].look.fixes = { safe: false, slots: { formula: { size: 76 }, legend: { size: 46, scrim: true }, title: { scrim: true } } };
  const run = staged(tl);
  await run.seekT(9.5);
  const ctx = run.ctx();
  const v = ctx.view;
  const entries = /** @type {any} */ (template).texts(ctx);
  const by = (/** @type {string} */ slot) => entries.find((/** @type {any} */ e) => e.slot === slot);
  assert.equal(by('formula').size, 76, 'capped');
  assert.equal(by('legend').size, 46);
  assert.equal(v.narrow, false);
  const scrim = ctx.roles.color('scrim');
  assert.notEqual(v.titleBox.style.background, '');
  assert.notEqual(v.legend.style.background, '');
  // the scrim over the background colour that leaves the ink the least contrast (R-53's worst case)
  const under = (/** @type {string} */ ink) => ctx.onBackground.bgs.map((/** @type {string} */ b) => composite(scrim, b))
    .reduce((/** @type {string} */ a, /** @type {string} */ b) => (contrastRatio(ink, b) < contrastRatio(ink, a) ? b : a));
  assert.equal(by('title').bg, under(ctx.onBackground.ink));
  assert.equal(by('legend').bg, under(ctx.onBackground.ink));
  assert.notEqual(by('title').bg, ctx.onBackground.bg, 'the scrim over the background is what the text sits on');
  assert.notEqual(by('legend').bg, ctx.onBackground.bg);
  assert.equal(by('formula').bg, ctx.roles.color('surface'), 'the card is not scrimmed');
  run.done();
  const safe = load('formula_focus.portrait.json');
  safe.scenes[0].look.fixes = { safe: true, slots: {} };
  const r2 = staged(safe);
  await r2.seekT(9.5);
  const v2 = r2.ctx().view;
  assert.equal(v2.narrow, true, 'the safe layout keeps the card in the text column (R-59)');
  const L = LAYOUT.portrait;
  const e2 = /** @type {any} */ (template).texts(r2.ctx());
  assert.deepEqual(e2.map((/** @type {any} */ e) => e.size), [L.title.min, L.formula.min, L.text.min, ...v2.rows.map(() => L.legend.min)], 'every slot at its minimum');
  assert.equal(v2.card.style.left, `${geometry(ZONES.portrait).narrow?.x0}px`);
  r2.done();
});

// --- layout decisions (jsdom: widths stubbed) ----------------------------------------------------------------------

/**
 * Make an element as wide as `em` em of its formula host's font size (MathJax scales with the em).
 * @param {HTMLElement} el @param {HTMLElement} host @param {number} em
 */
function widthInEm(el, host, em) {
  Object.defineProperty(el, 'offsetWidth', { configurable: true, get: () => (el.style.display === 'none' ? 0 : Math.round(em * parseFloat(host.style.fontSize || '0'))) });
}

test('9:16: a formula wider than the card at its smallest size is set broken at "=" (§4.3); still wider: overflow reported', async () => {
  const tl = load('formula_focus.portrait.stress.json');
  const run = staged(tl);
  await run.seekT(0);
  const ctx = run.ctx();
  const v = ctx.view;
  const inner = slotWidths(ZONES.portrait).formula;
  assert.ok(v.brokenEl, 'the lint-limit formula has two "=": a broken form exists');
  widthInEm(v.wideTex, v.formulaHost, 16); // 16 em: 1088 px at its smallest (68 px), wider than 696
  widthInEm(v.brokenEl, v.formulaHost, 9); // 9 em: fits at 72 px (648 px)
  /** @type {any} */ (template).layout(ctx);
  assert.equal(v.broken, true);
  assert.equal(v.texEl, v.brokenEl);
  assert.equal(v.wideTex.style.display, 'none');
  assert.equal(v.formulaSize, 76, 'the largest 4 px step whose width fits: 76 x 9 = 684 <= 696');
  assert.ok(76 * 9 <= inner && 80 * 9 > inner);
  assert.equal(v.formulaOverflow, 0);
  widthInEm(v.brokenEl, v.formulaHost, 14);
  /** @type {any} */ (template).layout(ctx);
  assert.equal(v.formulaSize, LAYOUT.portrait.formula.min);
  assert.equal(v.formulaOverflow, 14 * LAYOUT.portrait.formula.min - inner, 'the broken form overflows less: kept, its excess reported');
  // a formula that fits at a smaller size is never broken
  widthInEm(v.wideTex, v.formulaHost, 9.5);
  /** @type {any} */ (template).layout(ctx);
  assert.equal(v.broken, false);
  assert.equal(v.formulaSize, 72, '72 x 9.5 = 684 <= 696 < 76 x 9.5');
  run.done();
  // 16:9 has no broken form: it shrinks, then reports
  const land = staged(load('formula_focus.landscape.stress.json'));
  await land.seekT(0);
  const vl = land.ctx().view;
  assert.equal(vl.brokenEl, null);
  widthInEm(vl.wideTex, vl.formulaHost, 30);
  /** @type {any} */ (template).layout(land.ctx());
  assert.equal(vl.formulaSize, LAYOUT.landscape.formula.min);
  assert.equal(vl.formulaOverflow, 30 * 64 - slotWidths(ZONES.landscape).formula);
  land.done();
});

test('9:16: a formula crossing the rail is first set smaller in the wide card (§4.3\'s 768 px); the narrow card only when its minimum crosses too', async () => {
  const tl = load('formula_focus.portrait.json');
  const run = staged(tl);
  await run.seekT(0);
  const ctx = run.ctx();
  const v = ctx.view;
  const g = geometry(ZONES.portrait);
  const L = LAYOUT.portrait;
  const top = g.title.y0 + L.titleGap; // jsdom: the headline has no height
  /** A formula `wEm` x `hEm` em, at the size the page sets. @param {number} wEm @param {number} hEm */
  const em = (wEm, hEm) => {
    const fs = () => parseFloat(v.formulaHost.style.fontSize) || L.formula.max;
    Object.defineProperty(v.wideTex, 'offsetWidth', { configurable: true, get: () => (v.wideTex.style.display === 'none' ? 0 : Math.round(wEm * fs())) });
    Object.defineProperty(v.wideTex, 'offsetHeight', { configurable: true, get: () => Math.round(hEm * fs()) });
  };
  // 8 em wide (fitted to the wide card's 696 px at 84; its right end past x 780 at any size), 5.5 em tall: ends below
  // the rail at 84 px, above it at 80
  em(8, 5.5);
  assert.ok(8 * 84 <= 696 && 8 * 88 > 696 && 540 + 8 * L.formula.min / 2 > 780);
  assert.ok(top + L.card.padY + 5.5 * 84 > g.rail && top + L.card.padY + 5.5 * 80 <= g.rail);
  /** @type {any} */ (template).layout(ctx);
  assert.deepEqual([v.narrow, v.formulaSize], [false, 80], 'the wide card, the formula at the largest size that ends above the rail');
  // 7 em tall: below the rail even at its minimum (68 x 7 = 476 + 388 > 840): the narrow card
  em(8, 7);
  assert.ok(top + L.card.padY + 7 * L.formula.min > g.rail);
  /** @type {any} */ (template).layout(ctx);
  assert.equal(v.narrow, true);
  run.done();
});

test('9:16: the brace band above the formula counts toward where it ends (a numerator\'s over-brace can push it below y 840)', async () => {
  const tl = load('formula_focus.portrait.light.json');
  const run = staged(tl);
  await run.seekT(0);
  const ctx = run.ctx();
  const v = ctx.view;
  const g = geometry(ZONES.portrait);
  const L = LAYOUT.portrait;
  const top = g.title.y0 + L.titleGap; // jsdom: the headline has no height
  const fs = L.formula.max;
  const bh = Math.max(12, Math.min(28, Math.round(0.24 * fs)));
  const above = 2 * BRACE_GAP + bh + BOX_PX - L.card.padY;
  // a formula 600 px wide (its right end past x 780) ending 5 px above the rail without the band, below it with it
  const texH = g.rail - 5 - top - L.card.padY;
  Object.defineProperty(v.wideTex, 'offsetWidth', { configurable: true, get: () => (v.wideTex.style.display === 'none' ? 0 : 600) });
  Object.defineProperty(v.wideTex, 'offsetHeight', { configurable: true, get: () => texH });
  // I = V / R: V over the bar, so its brace goes over it (the band above)
  stubRect(v.wideTex, { x: 240, y: 500, w: 600, h: texH });
  const order = v.tints.map((/** @type {any} */ t) => t.symbol);
  /** @param {number} rx R's x: under V (V's brace over it, the band above) or beside it (V's under it, no band) */
  const lay = (rx) => {
    const boxes = /** @type {Record<string, any>} */ ({ I: { x: 300, y: 600, w: 40, h: 60 }, V: { x: 500, y: 500, w: 50, h: 60 }, R: { x: rx, y: 700, w: 50, h: 60 } });
    [...v.wideTex.querySelectorAll('g[fill], g[data-dyn-tint]')].forEach((el, i) => {
      stubRect(el, boxes[order[i]]);
      for (const leaf of el.querySelectorAll('path')) stubRect(leaf, boxes[order[i]]);
    });
    /** @type {any} */ (template).layout(ctx);
  };
  assert.ok(above > 0 && top + L.card.padY + texH <= g.rail && top + L.card.padY + above + texH > g.rail);
  lay(700);
  assert.equal(v.narrow, false, 'no glyph under V: its brace under it, no band above, the formula ends above the rail');
  lay(500);
  assert.equal(v.narrow, true, 'the band above moves the formula below the rail, wider than the column there: the narrow card');
  run.done();
});

test('9:16: a formula that would reach below y 840 outside the column there (R-59) gets the narrow card; one inside it keeps the wide one', async () => {
  const tl = load('formula_focus.portrait.json');
  const run = staged(tl);
  await run.seekT(0);
  const ctx = run.ctx();
  const v = ctx.view;
  const g = geometry(ZONES.portrait);
  const stubFormula = (/** @type {number} */ w, /** @type {number} */ hh) => {
    Object.defineProperty(v.wideTex, 'offsetWidth', { configurable: true, get: () => (v.wideTex.style.display === 'none' ? 0 : w) });
    Object.defineProperty(v.wideTex, 'offsetHeight', { configurable: true, get: () => hh });
  };
  stubFormula(600, 500); // from y 340 + 24 + 24: its bottom passes y 840, its right end x 540 + 300 > 780
  /** @type {any} */ (template).layout(ctx);
  assert.equal(v.narrow, true);
  assert.equal(v.card.style.left, `${g.narrow?.x0}px`);
  assert.equal(v.card.style.width, `${(g.narrow?.x1 ?? 0) - (g.narrow?.x0 ?? 0)}px`);
  stubFormula(480, 500); // ends at x 780: inside the column below the rail
  /** @type {any} */ (template).layout(ctx);
  assert.equal(v.narrow, false);
  assert.equal(v.card.style.width, '768px', 'the 768 px card of §4.3');
  stubFormula(600, 300); // ends above the rail
  /** @type {any} */ (template).layout(ctx);
  assert.equal(v.narrow, false);
  run.done();
});

/**
 * The typical 9:16 fixture with stubbed heights: the card is `withText` px tall with its sentence and `bare` px
 * without, each legend row `rowEm` x its font size (the legend the rows and their gaps).
 * @param {{withText: number, bare: number, rowEm: number}} h
 */
async function tallPortrait(h) {
  const tl = load('formula_focus.portrait.json');
  const run = staged(tl);
  await run.seekT(0);
  const ctx = run.ctx();
  const v = ctx.view;
  const textBox = v.textMask || v.textEl;
  Object.defineProperty(v.card, 'offsetHeight', { configurable: true, get: () => (textBox.style.display === 'none' ? h.bare : h.withText) });
  for (const r of v.rows) Object.defineProperty(r.row, 'offsetHeight', { configurable: true, get: () => Math.round(h.rowEm * parseFloat(v.legend.style.fontSize)) });
  Object.defineProperty(v.legend, 'offsetHeight', { configurable: true, get: () => v.rows.reduce((/** @type {number} */ a, /** @type {any} */ r) => a + r.row.offsetHeight, 0) + (v.rows.length - 1) * LAYOUT.portrait.rowGap });
  /** @type {any} */ (template).layout(ctx);
  return { tl, run, ctx, v };
}

test('the fit: legend, sentence, headline, formula shrink in turn; then the sentence is left out; then overflow, never a dropped row', async () => {
  const L = LAYOUT.portrait;
  const g = geometry(ZONES.portrait);
  const top = g.title.y0 + L.titleGap; // jsdom: the headline has no height
  const avail = g.bottom - top;
  // everything fits with the sentence: all at their largest, the sentence drawn
  {
    const { run, v, ctx } = await tallPortrait({ withText: 300, bare: 150, rowEm: 1 });
    assert.deepEqual([v.textShown, v.legendSize, v.formulaSize, v.titleSize, v.legendOverflow], [true, L.legend.max, L.formula.max, L.title.max, 0]);
    assert.ok(/** @type {any} */ (template).texts(ctx).some((/** @type {any} */ e) => e.slot === 'text'));
    run.done();
  }
  // the legend shrinks first (4 rows at 2 em: 52 -> 416 + 12 too tall beside a 250 px card; 44 -> 352 + 12 fits)
  {
    const { run, v } = await tallPortrait({ withText: 250, bare: 150, rowEm: 2 });
    assert.ok(250 + L.legendGap + 4 * 2 * 52 + 12 > avail && 250 + L.legendGap + 4 * 2 * 44 + 12 <= avail);
    assert.equal(v.textShown, true);
    assert.ok(v.legendSize < L.legend.max && v.legendSize >= L.legend.min);
    assert.equal(v.formulaSize, L.formula.max, 'the formula keeps its size while the legend can shrink');
    run.done();
  }
  // nothing fits with the sentence even at the minimums: it is left out, the sizes fitted again from their largest
  {
    const { run, v, ctx } = await tallPortrait({ withText: 600, bare: 150, rowEm: 2 });
    assert.equal(v.textShown, false);
    assert.equal(/** @type {HTMLElement} */ (v.textMask || v.textEl).style.display, 'none');
    assert.ok(!/** @type {any} */ (template).texts(ctx).some((/** @type {any} */ e) => e.slot === 'text'), 'not reported: not drawn');
    assert.equal(v.formulaSize, L.formula.max);
    assert.equal(v.legendOverflow, 0);
    run.done();
  }
  // still too tall without it: everything at its minimum, every row kept, the excess reported as the legend's overflow
  // (preflight blocks with "Shorten text"; RV L-13: no row is dropped or faded out to make room, B2c review V2)
  {
    const { run, v, ctx, tl } = await tallPortrait({ withText: 600, bare: 150, rowEm: 3 });
    const legendH = 4 * 3 * L.legend.min + 3 * L.rowGap;
    assert.deepEqual([v.legendSize, v.formulaSize, v.titleSize], [L.legend.min, L.formula.min, L.title.min]);
    assert.equal(v.legendOverflow, 150 + L.legendGap + legendH - avail, 'the spill past the bottom');
    assert.deepEqual([...v.legend.children], v.rows.map((/** @type {any} */ r) => r.slot), 'every row, in legend order');
    assert.equal(v.legend.style.height, '', 'the legend is its rows\' height');
    // every row that entered stays on screen to the scene's end, at full opacity (never a one-item carousel)
    await run.seekT(tl.scenes[0].duration - 0.05);
    assert.deepEqual(v.rows.map((/** @type {any} */ r) => Number(r.row.style.opacity)), [1, 1, 1, 1]);
    assert.ok(v.rows.every((/** @type {any} */ r) => r.slot.style.visibility !== 'hidden' && r.slot.style.transform === ''));
    const entries = /** @type {any} */ (template).texts(ctx).filter((/** @type {any} */ e) => e.slot === 'legend');
    assert.deepEqual(entries.map((/** @type {any} */ e) => [e.alpha, e.overflow > 0]), v.rows.map(() => [1, true]));
    run.done();
  }
});

// --- geometry, slots and the catalogue -----------------------------------------------------------------------------

test('geometry from the zones: the 9:16 card is 768 px (§4.3) round the 696 px critical column (R-59); the narrow one round the text column', () => {
  const p = geometry(ZONES.portrait);
  assert.deepEqual([p.wide?.x0, p.wide?.x1], [156, 924]);
  assert.equal((p.wide?.x1 ?? 0) - (p.wide?.x0 ?? 0), 768);
  assert.deepEqual([p.narrow?.x0, p.narrow?.x1], [156, 816]);
  assert.deepEqual([p.title.x0, p.title.x1, p.title.y0, p.bottom, p.rail], [192, 888, 340, 1020, 840]);
  assert.deepEqual(p.legend, { x0: 192, x1: 780 });
  const l = geometry(ZONES.landscape);
  assert.deepEqual([l.title.x0, l.title.x1, l.title.y0, l.bottom, l.centre, l.maxWidth, l.rail], [96, 1824, 54, 912, 960, 1728, Infinity]);
  assert.deepEqual(slotWidths(ZONES.landscape), { title: 1728, formula: 1616, text: 1616, legend: 840 });
  assert.deepEqual(slotWidths(ZONES.portrait), { title: 696, formula: 696, text: 588, legend: 588 });
  // every text box inside the critical zones (R-59): 9:16 above the rail x 192-888, the text column x 192-780
  assert.ok(p.title.x0 >= ZONES.portrait.critical[0].x0 && p.title.x1 <= ZONES.portrait.critical[0].x1);
  assert.ok((p.legend?.x0 ?? 0) >= ZONES.portrait.critical[1].x0 && (p.legend?.x1 ?? 0) <= ZONES.portrait.critical[1].x1);
  assert.ok((p.narrow?.x1 ?? 0) - LAYOUT.portrait.card.padX <= ZONES.portrait.critical[1].x1);
});

test('sizes keep R-29\'s floors; line heights are the catalogue type scale\'s (Indic at its floors)', () => {
  for (const f of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[f];
    const floors = TYPE_FLOORS[f];
    for (const s of [L.title, L.formula, L.text]) assert.ok(s.min >= floors.text_min && s.min <= s.max);
    assert.ok(L.legend.min >= floors.label_min, 'legend rows are labels');
    if (floors.heavy_max_px) {
      for (const s of [L.text, L.legend]) assert.ok(s.weight >= /** @type {number} */ (floors.heavy_min_weight), 'R-29 9:16: >= 600 at <= 64 px');
    }
  }
  const scale = /** @type {any} */ (CATALOGUE).type.scale;
  assert.ok(LINE_HEIGHT.display.latin >= scale.display.line_height.latin[0] && LINE_HEIGHT.display.latin <= scale.display.line_height.latin[1]);
  assert.equal(LINE_HEIGHT.display.indic, scale.display.line_height.indic[0]);
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.body.indic, scale.body.line_height.indic[0]);
});

test('the catalogue\'s slots are this template\'s (templates.py mirrors LAYOUT; no drift)', () => {
  // registered (B2c review CG-4: never skipped, so a lost slot table or consumes entry fails here)
  assert.equal(/** @type {any} */ (CATALOGUE).templates[ID].implemented, true);
  const slots = /** @type {any} */ (templateSlots(ID));
  assert.deepEqual(Object.keys(slots).sort(), ['formula', 'legend', 'text', 'title']);
  for (const f of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[f];
    const w = slotWidths(ZONES[f]);
    const spec = (/** @type {string} */ slot) => slots[slot][f].default;
    assert.deepEqual(Object.keys(slots.title[f]), ['default']);
    assert.deepEqual({ ...spec('title') }, {
      w: w.title, max_lines: L.title.lines, size: [L.title.min, L.title.max], role: 'title', face: 'display', weight: L.title.weight,
      line_height: LINE_HEIGHT.display.latin, tracking_em: TITLE_TRACKING, max_words: null, max_chars: null,
      prefer_lines: L.title.prefer || null, critical: true,
    });
    assert.deepEqual({ ...spec('formula') }, {
      w: w.formula, max_lines: null, size: [L.formula.min, L.formula.max], role: FORMULA_SLOT.role, face: FORMULA_SLOT.face,
      weight: FORMULA_SLOT.weight, line_height: FORMULA_SLOT.lineHeight, tracking_em: 0, max_words: null, max_chars: null,
      prefer_lines: null, critical: true,
    });
    assert.deepEqual({ ...spec('text') }, {
      w: w.text, max_lines: L.text.lines, size: [L.text.min, L.text.max], role: 'body', face: 'body', weight: L.text.weight,
      line_height: LINE_HEIGHT.body.latin, tracking_em: 0, max_words: null, max_chars: null, prefer_lines: null, critical: true,
    });
    assert.deepEqual({ ...spec('legend') }, {
      w: w.legend, max_lines: null, size: [L.legend.min, L.legend.max], role: 'label', face: 'body', weight: L.legend.weight,
      line_height: LINE_HEIGHT.body.latin, tracking_em: 0, max_words: null, max_chars: null, prefer_lines: null, critical: true,
    });
  }
  const behaviour = templateBehaviour(ID);
  assert.deepEqual(behaviour?.consumes.kinds, ['reveal', 'var', 'draw', 'look_here']);
  assert.deepEqual(behaviour?.consumes.targets, ['item', 'item:var']);
  assert.deepEqual(behaviour?.consumes.forms, { reveal: ['item'], var: ['item:var'], draw: ['item'], look_here: ['item', 'item:var'] });
  assert.equal(behaviour?.pushOk, false, 'R-30: no camera motion on a formula');
});

test('bracePath / boxPath: whole and half px, the brace\'s tip at its middle, the box inside the card', () => {
  const d = bracePath(100, 161, 518, 25);
  assert.equal(d, 'M100.0 518.0Q100.0 530.5 112.5 530.5L118.0 530.5Q130.5 530.5 130.5 543.0Q130.5 530.5 143.0 530.5L148.5 530.5Q161.0 530.5 161.0 518.0');
  const narrow = bracePath(10, 14, 0, 20);
  assert.match(narrow, /^M10\.0 0\.0Q10\.0 10\.0 11\.0 10\.0L11\.0 10\.0/, 'a narrow symbol: the curls shrink to fit');
  assert.equal(boxPath(200, 100, 24, 4), 'M24.0 2.0H176.0A22.0 22.0 0 0 1 198.0 24.0V76.0A22.0 22.0 0 0 1 176.0 98.0H24.0A22.0 22.0 0 0 1 2.0 76.0V24.0A22.0 22.0 0 0 1 24.0 2.0Z');
});

test('line heights follow the script (§4.6): Latin display 1.05 and body 1.35, Indic 1.25 and 1.45', async () => {
  const tl = load('formula_focus.landscape.stress.json');
  const run = staged(tl);
  const info = run.stage.info();
  /** @type {Record<string, string[]>} */
  const seen = {};
  for (const s of info.scenes) {
    await run.stage.seek(s.f0);
    const v = run.ctx(s.index).view;
    seen[tl.scenes[s.index].scene_id] = [v.title.style.lineHeight, v.textEl ? v.textEl.style.lineHeight : '', v.legend.style.lineHeight];
  }
  const latin = [String(LINE_HEIGHT.display.latin), String(LINE_HEIGHT.body.latin), String(LINE_HEIGHT.body.latin)];
  const indic = [String(LINE_HEIGHT.display.indic), String(LINE_HEIGHT.body.indic), String(LINE_HEIGHT.body.indic)];
  assert.deepEqual(seen, { 'ff-lint': latin, 'ff-longword': latin, 'ff-tamil': indic, 'ff-hindi': indic });
  run.done();
});
