// @ts-check
// templates/_shared.js (B2c): the helpers the B2 templates share, and the test harness (_harness.js) the templates'
// own tests use, each check of it seen to fail on a template that breaks it.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { Painter, REGIONS, TYPE_FLOORS, ZONES, makeRoles } from '../../../js/dynamic/core.js';
import { PUSH_SCALE_MAX } from '../../../js/dynamic/cues.js';
import { svg } from '../../../js/shared/dom.js';
import {
  ICON_PATHS_MAX, ICON_PATH_MAX_CHARS, ICON_PATH_RE, ICON_STROKE_MAX, PANEL_DELAY, PANEL_ENTER, PANEL_KINDS,
  PANEL_LABEL_PX, PANEL_MEDIA_KINDS, PANEL_TERM, PANEL_TITLED_KINDS, RADIUS, SPACE, STATE_BORDER, TEXT_RISE, TINT_BASE,
  boxRect, buildPanel, containRect, drawsPanel, enterAt, entityColour, fitLines, gridSpan, group, insetBox,
  itemStateParts, layoutPanel, layoutStrike, mediaAspect, paintItemState, paintPanel, paintRise, paintTextEntrance,
  paintTint, panelFocusRect, panelMediaBox, panelOf, panelTexts, place, pushHeadroom, region, revealMask, richWords,
  sanitiseIcon, seconds, shownPanel, slotRange, slotSpec, storedIcon, texTokens, tintTargets, tintTex,
} from '../../../js/dynamic/templates/_shared.js';
import { contractProblems, orderProblems, recordingTex } from './_harness.js';
import { dynamicTimeline } from '../_fixture.js';

before(() => installDom());
after(() => uninstallDom());

/**
 * A template context for the helpers (no stage).
 * @param {Record<string, any>} [extra]
 */
function ctxFor(extra = {}) {
  const tl = dynamicTimeline();
  const root = document.createElement('div');
  document.body.appendChild(root);
  return /** @type {any} */ ({
    scene: tl.scenes[0], index: 0, look: tl.scenes[0].look, format: 'landscape', zones: ZONES.landscape,
    roles: makeRoles(tl.presentation, tl.scenes[0].look), paint: new Painter(), pending: [], root, locale: 'en',
    reducedMotion: false, motion: { scale: 1, entrance: 'fade_rise' }, rectOf: () => null, ...extra,
  });
}

// --- geometry -------------------------------------------------------------------------------------------------------

test('regions, grid spans and boxes from the catalogue geometry', () => {
  assert.deepEqual(region({ format: 'landscape' }, 'left-55'), REGIONS.landscape['left-55']);
  assert.deepEqual(region({ format: 'portrait' }, 'clip'), { x0: 0, y0: 360, x1: 1080, y1: 968 });
  assert.throws(() => region({ format: 'portrait' }, 'left-55'), RangeError);
  assert.throws(() => region({ format: 'landscape' }, 'toString'), RangeError);
  assert.deepEqual(gridSpan(0, 11), { x0: 96, y0: 54, x1: 1824, y1: 912 }, 'the 12 columns are the 16:9 critical box');
  assert.deepEqual(gridSpan(0, 0), { x0: 96, y0: 54, x1: 218, y1: 912 });
  assert.deepEqual(gridSpan(7, 7).x0, 96 + 7 * 146);
  assert.deepEqual(gridSpan(11, 6), gridSpan(6, 11), 'either order');
  assert.deepEqual(boxRect({ x0: 1, y0: 2, x1: 11, y1: 22 }), { x: 1, y: 2, w: 10, h: 20 });
  assert.deepEqual(insetBox({ x0: 0, y0: 0, x1: 100, y1: 50 }, 8), { x0: 8, y0: 8, x1: 92, y1: 42 });
  const el = document.createElement('div');
  place(el, { x0: 10.4, y0: 20.6, x1: 110.4, y1: 70.6 });
  assert.deepEqual([el.style.left, el.style.top, el.style.width, el.style.height], ['10px', '21px', '100px', '50px']);
  assert.deepEqual(SPACE.slice(0, 3), [8, 16, 24]);
  assert.equal(RADIUS.card, 24);
  assert.equal(STATE_BORDER, 2);
});

test('slotRange: the R-29 floor, preflight\'s size cap (fixes channel or legacy text_fit) and the safe layout', () => {
  const ctx = ctxFor();
  const floor = TYPE_FLOORS.landscape.text_min;
  assert.deepEqual(slotRange(ctx, 'body', { min: 40, max: 64 }), { min: Math.max(floor, 40), max: 64 });
  assert.deepEqual(slotRange(ctx, 'body', { min: 20, max: 30 }), { min: floor, max: floor }, 'never below the floor');
  assert.deepEqual(slotRange(ctx, 'label', { min: 20, max: 60, role: 'label' }).min, TYPE_FLOORS.landscape.label_min);
  ctx.look = { ...ctx.look, fixes: { safe: false, slots: { body: { size: 48 } } } };
  assert.deepEqual(slotRange(ctx, 'body', { min: 40, max: 64 }), { min: 40, max: 48 });
  ctx.look = { ...ctx.look, fixes: { safe: false, slots: { body: { size: 10 } } } };
  assert.deepEqual(slotRange(ctx, 'body', { min: 40, max: 64 }), { min: 40, max: 40 }, 'a cap below the minimum holds the minimum');
  ctx.look = { ...ctx.look, fixes: undefined, text_fit: { body: { size: 52, lines: 2 } } };
  assert.deepEqual(slotRange(ctx, 'body', { min: 40, max: 64 }), { min: 40, max: 52 }, 'text_fit.size is an upper bound (§1.11)');
  ctx.look = { ...ctx.look, text_fit: {}, fixes: { safe: true, slots: {} } };
  assert.deepEqual(slotRange(ctx, 'body', { min: 40, max: 64 }), { min: 40, max: 40 });
  assert.equal(slotSpec('board_generic', 'title', 'landscape')?.role, 'title');
  assert.equal(slotSpec('board_generic', 'nope', 'landscape'), null);
  assert.equal(slotSpec('nope', 'title', 'landscape'), null);
});

/**
 * An element laid out like text of `chars` characters at `charW` em per character in a box `boxW` px wide: its
 * offsetHeight follows the font size (jsdom has no layout).
 * @param {number} chars @param {number} boxW @param {number} lh
 */
function fakeText(chars, boxW, lh) {
  const el = document.createElement('div');
  Object.defineProperty(el, 'offsetHeight', {
    get() {
      const size = parseFloat(el.style.fontSize);
      return Math.ceil((chars * 0.5 * size) / boxW) * size * lh;
    },
  });
  Object.defineProperty(el, 'clientWidth', { get: () => boxW });
  return el;
}

test('fitLines: the largest size in 4 px steps whose line count fits; prefer first; overflow at the minimum', () => {
  // 40 characters at 0.5 em in a 1000 px box: 96 px -> 2 lines, 52 px -> 2 lines (1040 px), 48 px -> 1 line
  const el = fakeText(40, 1000, 1.1);
  assert.deepEqual(fitLines(el, { max: 96, min: 40, lines: 2, lh: 1.1 }), { size: 96, fits: true, overflow: 0 });
  assert.equal(el.style.fontSize, '96px', 'left at the chosen size');
  assert.deepEqual(fitLines(el, { max: 96, min: 40, lines: 2, lh: 1.1, prefer: 1 }), { size: 48, fits: true, overflow: 0 });
  assert.deepEqual(fitLines(el, { max: 96, min: 60, lines: 2, lh: 1.1, prefer: 1 }), { size: 96, fits: true, overflow: 0 }, 'prefer only while it fits at >= min');
  const long = fakeText(400, 1000, 1.1);
  const r = fitLines(long, { max: 96, min: 40, lines: 2, lh: 1.1 });
  assert.equal(r.size, 40);
  assert.equal(r.fits, false);
  assert.equal(r.overflow, Math.ceil(8000 / 1000) * 40 * 1.1 - 2 * 40 * 1.1);
  // a TeX word wider than the box at the minimum holds the text there and reports its excess
  const host = fakeText(10, 1000, 1.1);
  const word = document.createElement('span');
  Object.defineProperty(word, 'offsetWidth', { get: () => 1200 });
  Object.defineProperty(word, 'offsetHeight', { get: () => 10 });
  const t = fitLines(host, { max: 96, min: 40, lines: 2, lh: 1.1 }, [word], [true]);
  assert.deepEqual(t, { size: 40, fits: true, overflow: 200 });
});

test('richWords: word spans that keep their markup, [[terms]] as .rt-k (cue.term\'s targets), TeX through ctx.renderTex', async () => {
  const tex = recordingTex();
  const ctx = ctxFor({ renderTex: tex.renderTex });
  const host = document.createElement('div');
  const { words, tex: flags } = richWords(ctx, host, 'The [[electric current]] is **fast**, $I = V/R$ holds');
  assert.deepEqual(words.map((w) => w.textContent), ['The', 'electric', 'current', 'is', 'fast,', 'I = V/R', 'holds']);
  const pieces = [...host.querySelectorAll('.rt-k')];
  assert.deepEqual(pieces.map((k) => k.textContent), ['electric', ' ', 'current'], 'a term split into word pieces (and its space)');
  assert.deepEqual(pieces.map((k) => k.getAttribute('data-dyn-term')), ['0', '0', '0'], 'one term: its pieces share its ordinal');
  const two = document.createElement('div');
  richWords(ctx, two, '[[a]] and [[b c]]');
  assert.deepEqual([...two.querySelectorAll('.rt-k')].map((k) => `${k.textContent}:${k.getAttribute('data-dyn-term')}`), ['a:0', 'b:1', ' :1', 'c:1']);
  assert.equal(host.querySelector('.rt-b')?.textContent, 'fast');
  assert.deepEqual(flags, [false, false, false, false, false, true, false]);
  await Promise.all(ctx.pending);
  assert.deepEqual(tex.seen, [{ tex: 'I = V/R', display: false }]);
  assert.equal(host.textContent, 'The electric current is fast, I = V/R holds');
});

// --- motion ---------------------------------------------------------------------------------------------------------

test('motion: the personality\'s duration scale, fade-and-rise on whole px, the text entrances, reduced motion', () => {
  const ctx = ctxFor({ motion: { scale: 1.15, entrance: 'fade_rise' } });
  assert.ok(Math.abs(seconds(ctx, 0.4) - 0.46) < 1e-12);
  assert.equal(seconds(ctxFor({ motion: undefined }), 0.4), 0.4);
  assert.equal(enterAt(ctx, 1, 1, 0.4), 0);
  assert.equal(enterAt(ctx, 1.46, 1, 0.4), 1, 'scaled: done at 1 + 0.46');
  assert.ok(enterAt(ctx, 1.4, 1, 0.4) < 1);
  const el = document.createElement('div');
  paintRise(ctx, el, 0.5, 24);
  assert.equal(el.style.transform, 'translate(0, 12px)', '2D (§P: translate3d moved TeX order-dependently)');
  paintRise(ctx, el, 0.33, 24);
  assert.match(el.style.transform, /^translate\(0, 16px\)$/, 'whole px');
  paintRise(ctx, el, 1, 24);
  assert.equal(el.style.transform, 'none');
  paintRise(ctxFor({ reducedMotion: true }), el, 0.5, 24);
  assert.equal(el.style.transform, 'none');
  // the text entrances: Soft fades and rises 16 px; Crisp slides up inside its mask from the baseline
  const text = document.createElement('span');
  paintTextEntrance(ctx, text, null, 0.5);
  assert.equal(text.style.transform, `translate(0, ${TEXT_RISE / 2}px)`);
  assert.equal(text.style.opacity, '0.5');
  const crisp = ctxFor({ motion: { scale: 0.85, entrance: 'baseline_reveal' } });
  const mask = revealMask(text);
  Object.defineProperty(mask, 'offsetHeight', { get: () => 60 });
  assert.equal(mask.className, 'dyn-reveal-mask');
  paintTextEntrance(crisp, text, mask, 0.25);
  assert.equal(text.style.transform, 'translate(0, 45px)');
  assert.equal(text.style.opacity, '1');
  paintTextEntrance({ ...crisp, reducedMotion: true }, text, mask, 0.25);
  assert.equal(text.style.transform, 'none', 'reduced motion: a fade in place');
  const g = group('x-group', text, null);
  assert.equal(g.className, 'dyn-group x-group');
});

// --- item states ----------------------------------------------------------------------------------------------------

test('item states: weight and ink, a 2 px inset border (never an outline), the done check, the struck line', () => {
  const ctx = ctxFor();
  const host = document.createElement('div');
  const el = document.createElement('div');
  host.appendChild(el);
  const parts = itemStateParts(host);
  assert.ok(host.classList.contains('dyn-stateful'));
  paintItemState(ctx, host, parts, 'current', 1);
  assert.equal(host.style.fontWeight, '700');
  assert.equal(host.style.getPropertyValue('--dyn-state-border').toUpperCase(), `${ctx.roles.color('ink2')}FF`.toUpperCase());
  assert.equal(host.style.getPropertyValue('outline'), '');
  assert.equal(parts.check.style.visibility, 'hidden');
  paintItemState(ctx, host, parts, 'done', 0.5);
  assert.equal(host.style.fontWeight, '400', 'the base weight: never cleared');
  assert.equal(parts.check.style.visibility, 'visible');
  assert.equal(parts.check.style.opacity, '0.5');
  paintItemState(ctx, host, parts, null, 1);
  assert.equal(host.style.getPropertyValue('--dyn-state-border').toUpperCase(), `${ctx.roles.color('ink')}00`.toUpperCase(),
    'no state: a transparent border, never cleared');
  assert.notEqual(host.style.color, '', 'no state: the base ink, never cleared');
  paintItemState(ctx, host, parts, null, 1, { weight: 600, ink: ctx.roles.color('ink2') });
  assert.equal(host.style.fontWeight, '600', "the caller's base");
  // order independence of the style attribute (§P condition 3): past -> current serialises as current painted fresh
  const fresh = document.createElement('div');
  paintItemState(ctx, fresh, null, 'current', 1);
  const walked = document.createElement('div');
  paintItemState(ctx, walked, null, 'past', 1);
  paintItemState(ctx, walked, null, null, 1);
  paintItemState(ctx, walked, null, 'current', 1);
  assert.equal(walked.getAttribute('style'), fresh.getAttribute('style'));
  // struck: lines through the text's line boxes (jsdom: none, as it has no layout), drawn with k
  layoutStrike(ctx, parts, el);
  assert.equal(parts.lines.length, 0);
  const line = svg('line', { x1: '10', y1: '20', x2: '110', y2: '20' });
  parts.strike.appendChild(line);
  parts.lines.push(line);
  paintItemState(ctx, host, parts, 'struck', 0.25);
  assert.equal(line.getAttribute('stroke'), ctx.roles.color('warn'));
  assert.equal(line.getAttribute('stroke-dashoffset'), '75.00');
  assert.equal(parts.strike.style.visibility, 'visible');
  assert.throws(() => paintItemState(ctx, host, parts, 'glow', 1), RangeError);
});

// --- icons ----------------------------------------------------------------------------------------------------------

const SCHEMA = JSON.parse(readFileSync(new URL('../../../schemas/timeline.schema.json', import.meta.url), 'utf8'));

test('the icon checks are the schema\'s: path pattern, characters per path, paths per icon, stroke width', () => {
  const icon = SCHEMA.$defs.LookIcon.properties;
  assert.equal(ICON_PATH_RE.source, icon.paths.items.pattern);
  assert.equal(ICON_PATH_MAX_CHARS, icon.paths.items.maxLength);
  assert.equal(ICON_PATHS_MAX, icon.paths.maxItems);
  assert.equal(ICON_STROKE_MAX, icon.stroke_width.maximum);
});

const GOOD = { name: 'zap', set: 'lucide', version: '1.48.0', viewBox: [0, 0, 24, 24], stroke_width: 2, filled: false, paths: ['M4 14a1 1 0 0 1-.78-1.63l9.9-10.2', 'M12 2v4'] };

test('sanitiseIcon: a stored icon is checked again; anything outside the schema is not drawn', () => {
  assert.deepEqual(sanitiseIcon(GOOD), { viewBox: [0, 0, 24, 24], strokeWidth: 2, filled: false, paths: GOOD.paths });
  assert.equal(sanitiseIcon({ ...GOOD, stroke_width: undefined })?.strokeWidth, 2, 'the schema default');
  for (const bad of [
    null, 'x', { ...GOOD, viewBox: [0, 0, 24] }, { ...GOOD, viewBox: [0, 0, 0, 24] }, { ...GOOD, viewBox: [0, 0, '24', 24] },
    { ...GOOD, viewBox: [0, 0, Infinity, 24] }, { ...GOOD, stroke_width: 9 }, { ...GOOD, stroke_width: -1 },
    { ...GOOD, filled: 'yes' }, { ...GOOD, paths: [] }, { ...GOOD, paths: Array(33).fill('M0 0') },
    { ...GOOD, paths: ['M0 0" onload="x'] }, { ...GOOD, paths: ['<path d="M0 0"/>'] }, { ...GOOD, paths: ['url(#a)'] },
    { ...GOOD, paths: ['L0 0'] }, { ...GOOD, paths: [`M${'0 '.repeat(2100)}`] }, { ...GOOD, paths: [3] },
  ]) {
    assert.equal(sanitiseIcon(bad), null, JSON.stringify(bad).slice(0, 80));
  }
});

test('storedIcon: the look\'s icon as an SVG in currentColor, path data set with setAttribute, the stored stroke', () => {
  const ctx = ctxFor();
  ctx.look = { ...ctx.look, icons: { b2: GOOD, evil: { ...GOOD, paths: ['M0 0"><script>'] }, star: { ...GOOD, filled: true } } };
  const el = /** @type {SVGElement} */ (storedIcon(ctx, 'b2', { size: 96 }));
  assert.equal(el.getAttribute('viewBox'), '0 0 24 24');
  assert.equal(el.getAttribute('fill'), 'none');
  assert.equal(el.getAttribute('stroke'), 'currentColor');
  assert.equal(el.getAttribute('stroke-width'), '2');
  assert.equal(el.getAttribute('stroke-linecap'), 'round');
  assert.equal(el.getAttribute('stroke-linejoin'), 'round');
  assert.equal(el.getAttribute('class'), 'dyn-sicon');
  assert.equal(el.style.width, '96px');
  assert.deepEqual([...el.querySelectorAll('path')].map((p) => p.getAttribute('d')), GOOD.paths);
  assert.equal(el.querySelectorAll('*').length, 2, 'paths only');
  const filled = /** @type {SVGElement} */ (storedIcon(ctx, 'star', { size: '1.05em', block: true }));
  assert.equal(filled.getAttribute('fill'), 'currentColor');
  assert.equal(filled.getAttribute('stroke'), 'none');
  assert.equal(filled.getAttribute('class'), 'dyn-sicon dyn-sicon--block');
  assert.equal(filled.style.width, '1.05em');
  assert.equal(storedIcon(ctx, 'evil', { size: 96 }), null);
  assert.equal(storedIcon(ctx, 'none', { size: 96 }), null);
  assert.equal(storedIcon(ctx, 'toString', { size: 96 }), null);
  assert.equal(storedIcon(ctxFor(), 'b2', { size: 96 }), null, 'a look without icons');
  const css = readFileSync(new URL('../../../css/dynamic.css', import.meta.url), 'utf8');
  assert.match(css, /\.dyn-sicon \{\s*display: inline-block;\s*vertical-align: middle;/, 'explicit display and vertical-align');
});

// --- media ----------------------------------------------------------------------------------------------------------

test('media: aspect, a contained rect on whole px, the push headroom of a push_ok template\'s picture', () => {
  assert.equal(mediaAspect(/** @type {any} */ ({ width: 1280, height: 720 })), 16 / 9);
  assert.equal(mediaAspect(/** @type {any} */ ({ width: 0, height: 720 })), 16 / 9);
  assert.equal(mediaAspect(null), 16 / 9);
  assert.deepEqual(containRect(16 / 9, { x0: 1058, y0: 54, x1: 1824, y1: 912 }), { x: 1058, y: 268, w: 766, h: 431 });
  assert.deepEqual(containRect(1, { x0: 0, y0: 0, x1: 400, y1: 200 }, { alignX: 0 }), { x: 0, y: 0, w: 200, h: 200 });
  assert.deepEqual(containRect(1, { x0: 0, y0: 0, x1: 400, y1: 200 }, { alignX: 1 }), { x: 200, y: 0, w: 200, h: 200 });
  assert.deepEqual(pushHeadroom({ w: 1000, h: 500 }), { w: Math.ceil(1000 * (1 + PUSH_SCALE_MAX)), h: Math.ceil(500 * (1 + PUSH_SCALE_MAX)) });
});

// --- formula tints (§P (g)) -------------------------------------------------------------------------------------------

test('tintTex: each symbol in a \\color placeholder of its own, whole atoms only, longer symbols first', () => {
  const ph = (/** @type {number} */ k) => `${TINT_BASE}${k.toString(16).toUpperCase().padStart(2, '0')}`;
  const a = tintTex('v = v_0 + a t', ['v', 'v_0', 'a', 't']);
  assert.equal(a.tex, `{\\color{${ph(0)}}v} = {\\color{${ph(1)}}v_0} + {\\color{${ph(2)}}a} {\\color{${ph(3)}}t}`);
  assert.deepEqual(a.tints, [{ symbol: 'v', fill: ph(0) }, { symbol: 'v_0', fill: ph(1) }, { symbol: 'a', fill: ph(2) }, { symbol: 't', fill: ph(3) }]);
  assert.equal(tintTex('v_0 + v', ['v']).tex, `v_0 + {\\color{${ph(0)}}v}`, 'v never matches the v of v_0');
  // a power keeps its base; a subscript, a script group and upright text are not the symbol
  assert.equal(tintTex('v^2 + x_v + y_{v} + 3\\,\\mathrm{m}', ['v', 'm']).tex, `{\\color{${ph(0)}}v}^2 + x_v + y_{v} + 3\\,\\mathrm{m}`);
  // control words: \Delta V as one symbol, V alone elsewhere; \omega never matches inside \omegax
  assert.equal(tintTex('\\Delta V = I R', ['V', '\\Delta V', 'R']).tex, `{\\color{${ph(1)}}\\Delta V} = I {\\color{${ph(2)}}R}`);
  assert.equal(tintTex('\\frac{V}{R}', ['V', 'R']).tex, `\\frac{{\\color{${ph(0)}}V}}{{\\color{${ph(1)}}R}}`);
  assert.equal(tintTex('\\omegax + \\omega', ['\\omega']).tex, `\\omegax + {\\color{${ph(0)}}\\omega}`);
  assert.equal(tintTex('F = m a', []).tex, 'F = m a');
  assert.equal(tintTex('x', ['x', 'x', ' ']).tints.length, 1, 'symbols once each, blanks skipped');
  assert.equal(tintTex('a{b', ['{b']).tex, 'a{b', 'an unbalanced symbol never matches');
  // deterministic: the static tex() hook and build give the same string
  assert.equal(tintTex('v = v_0 + a t', ['v', 'v_0', 'a', 't']).tex, a.tex);
  assert.deepEqual(texTokens('\\alpha  x_{1}').map((t) => t.v), ['\\alpha', 'x', '_', '{', '1', '}']);
});

test('tintTargets / paintTint: the placeholder groups MathJax drew, found again after they were recoloured', () => {
  const ctx = ctxFor();
  const host = document.createElement('div');
  const mk = (/** @type {string} */ fill) => svg('g', { fill, stroke: fill }, svg('path', { d: 'M0 0' }));
  const g0 = mk('#0a0b00');
  const g1 = mk('#0A0B01');
  host.append(svg('svg', null, g0, g1, mk('#123456')));
  const tints = [{ symbol: 'v', fill: '#0A0B00' }, { symbol: 'a', fill: '#0A0B01' }, { symbol: 't', fill: '#0A0B02' }];
  const found = tintTargets(host, tints);
  assert.deepEqual([...found.keys()], ['v', 'a', 't']);
  assert.deepEqual(found.get('v'), [g0]);
  assert.deepEqual(found.get('a'), [g1]);
  assert.deepEqual(found.get('t'), []);
  paintTint(ctx, /** @type {Element[]} */ (found.get('v')), '#0FE3FE');
  assert.equal(g0.getAttribute('fill'), '#0FE3FE');
  assert.equal(g0.getAttribute('stroke'), '#0FE3FE');
  paintTint(ctx, /** @type {Element[]} */ (found.get('a')), null);
  assert.equal(g1.getAttribute('fill'), 'currentColor', 'untinted: the surrounding ink');
  assert.deepEqual(tintTargets(host, tints).get('v'), [g0], 'found again after recolouring (layout runs again)');
  const withMap = ctxFor({ entityRole: (/** @type {string} */ s) => (s === 'v' ? 'entity:1' : null) });
  assert.equal(entityColour(withMap, 'v'), withMap.roles.color('entity:1'));
  assert.equal(entityColour(withMap, 'a'), null);
  assert.equal(entityColour(ctxFor(), 'v'), null);
});

// --- the harness itself: each check fails on a template that breaks it -----------------------------------------------

/**
 * A minimal well-behaved template with list_build's id; `faults` breaks one rule at a time.
 * @param {Set<string>} [faults]
 */
function minimal(faults = new Set()) {
  return /** @type {any} */ ({
    id: 'list_build',
    build(/** @type {any} */ ctx) {
      const groups = (ctx.scene.board || []).map((/** @type {any} */ item) => {
        const text = document.createElement('div');
        text.appendChild(document.createTextNode(String(item.text || '')));
        const k = document.createElement('span');
        if (!faults.has('no-term')) k.className = 'rt-k';
        k.textContent = 'term';
        text.appendChild(k);
        if (faults.has('undeclared-tex')) {
          const m = document.createElement('span');
          text.appendChild(m);
          // a converter call the static tex() leaves out
          ctx.renderTex('x^2', false).then((/** @type {Element} */ n) => m.replaceChildren(n));
        }
        const g = document.createElement('div');
        g.appendChild(text);
        ctx.root.appendChild(g);
        return { id: item.id, g, text };
      });
      return { groups };
    },
    update(/** @type {any} */ ctx, /** @type {number} */ t) {
      for (const it of ctx.view.groups) {
        ctx.paint.style(faults.has('group-opacity') ? it.g : it.text, 'opacity', t > 1 ? '1' : '0');
        if (faults.has('outline')) ctx.paint.style(it.text, 'outline', '2px solid red');
      }
    },
    focusRect(/** @type {any} */ ctx, /** @type {string} */ target) {
      const it = ctx.view.groups.find((/** @type {any} */ x) => x.id === target);
      return it ? { rect: { x: faults.has('fractional') ? 10.5 : 10, y: 20, w: 100, h: 40 }, shape: 'rect', radius: 24, el: it.g } : null;
    },
    itemEl(/** @type {any} */ ctx, /** @type {string} */ id) {
      const it = ctx.view.groups.find((/** @type {any} */ x) => x.id === id);
      return it ? it.text : null;
    },
    dimGroups(/** @type {any} */ ctx) {
      const all = ctx.view.groups.map((/** @type {any} */ it) => it.g);
      return faults.has('changing-groups') ? all.slice(0, 1 + (ctx.paint.writes % 2)) : all;
    },
    texts(/** @type {any} */ ctx) {
      return ctx.view.groups.map((/** @type {any} */ it) => ({ el: it.text, role: 'body', critical: true, ink: faults.has('bad-ink') ? 'red' : ctx.roles.color('ink'), bg: ctx.roles.color('surface'), alpha: 1 }));
    },
    tex() {
      return [];
    },
  });
}

function minimalTimeline() {
  const tl = dynamicTimeline();
  const scene = tl.scenes[0];
  scene.look.template = 'list_build';
  scene.board = [{ id: 'b1', kind: 'bullet', text: 'One' }, { id: 'b2', kind: 'bullet', text: 'Two' }];
  scene.events = [{ t: 1, kind: 'cue_term', target: 'b1' }, { t: 3, kind: 'look_here', target: 'b2' }];
  tl.scenes = [scene];
  tl.total_duration = scene.duration;
  return tl;
}

test('the harness passes a template that keeps the contract, and catches each way of breaking it', async () => {
  const tl = minimalTimeline();
  assert.deepEqual(await contractProblems(minimal(), tl), []);
  assert.deepEqual(await orderProblems(tl, [0, 30, 45, 100, 150, 299], { templates: { list_build: minimal() } }), []);
  /** @type {[string, RegExp][]} */
  const cases = [
    ['group-opacity', /opacity of a dim group/],
    ['outline', /writes outline/],
    ['fractional', /not on whole px/],
    ['no-term', /no \[\[term\]\] span/],
    ['bad-ink', /hex ink/],
    ['undeclared-tex', /TeX not declared/],
    ['changing-groups', /dimGroups changed/],
  ];
  for (const [fault, want] of cases) {
    const problems = await contractProblems(minimal(new Set([fault])), tl);
    assert.ok(problems.some((p) => want.test(p)), `${fault}: ${JSON.stringify(problems)}`);
  }
});

// --- side panels (the B2-demo side-panel slice) ---------------------------------------------------------------------

/** A timed scene with a side panel. @param {any} panel @param {any} [media] @param {any} [extra] */
const panelScene = (panel, media = null, extra = {}) => /** @type {any} */ ({
  title: 'The scene', board: [], side_panel: { panel, media, show_at: 0 }, ...extra,
});

test('PANEL_KINDS: the picture kinds, the terminal and the three the engine draws nowhere yet', () => {
  assert.deepEqual([...PANEL_MEDIA_KINDS], ['figure', 'image', 'manim', 'gif']);
  assert.deepEqual([...PANEL_TITLED_KINDS], ['chart', 'graph', 'model_3d']);
  assert.deepEqual([...PANEL_KINDS], ['figure', 'image', 'manim', 'gif', 'terminal', 'chart', 'graph', 'model_3d']);
  // skill_tree and quiz panels are nobody's here: a template must hand such a scene to board_generic whole
  for (const kind of ['skill_tree', 'quiz']) assert.ok(!PANEL_KINDS.includes(kind), kind);
});

test('shownPanel: the panel only when the layout shows it', () => {
  assert.ok(shownPanel(panelScene({ kind: 'image' })));
  assert.equal(shownPanel(panelScene({ kind: 'image' }, null, { layout: { show_side_panel: false } })), null);
  assert.equal(shownPanel(/** @type {any} */ ({ board: [] })), null);
  assert.equal(shownPanel(/** @type {any} */ ({ board: [], side_panel: { show_at: 0 } })), null, 'no panel payload');
});

test('panelOf: a medium when the mode can show it, else the panel\'s title; a terminal its command and lines', () => {
  const ref = { kind: 'image', url: '/p.png', width: 800, height: 600 };
  const picture = panelOf(panelScene({ kind: 'image', title: 'Pumps' }, ref), 'render');
  assert.deepEqual({ kind: picture?.kind, draw: picture?.draw, shown: picture?.shown, title: picture?.title },
    { kind: 'image', draw: 'media', shown: true, title: 'Pumps' });
  assert.equal(picture?.ref, ref);
  // a GIF is live-only: the MP4 shows its title card, and the panel is laid out at the medium's aspect either way
  const gifRef = { kind: 'gif', url: 'https://x/g.gif', width: 400, height: 300 };
  assert.equal(panelOf(panelScene({ kind: 'gif', title: 'G' }, gifRef), 'live')?.draw, 'media');
  const inMp4 = panelOf(panelScene({ kind: 'gif', title: 'G' }, gifRef), 'render');
  assert.equal(inMp4?.draw, 'label');
  assert.equal(inMp4?.ref, gifRef, 'the medium is kept, so both modes lay the panel out the same');
  // a picture panel whose medium was never resolved: its title (else the scene's)
  assert.equal(panelOf(panelScene({ kind: 'figure' }), 'render')?.title, 'The scene');
  assert.equal(panelOf(panelScene({ kind: 'figure', title: 'Fig 2' }), 'render')?.draw, 'label');
  // a terminal: its command and the lines the classic schedule reveals
  const term = panelOf(panelScene({ kind: 'terminal', terminal: { command: 'ls -l', output: 'a\nb' } }), 'render');
  assert.equal(term?.draw, 'terminal');
  assert.equal(term?.terminal?.command, 'ls -l');
  assert.ok(/** @type {string[]} */ (term?.terminal?.lines).length >= 1);
  assert.equal(panelOf(panelScene({ kind: 'terminal' }), 'render'), null, 'no payload, nothing to draw');
  // the kinds the engine draws nowhere yet: their authored title, and only when they have one
  assert.equal(panelOf(panelScene({ kind: 'graph', title: 'I = V / R' }), 'render')?.draw, 'label');
  assert.equal(panelOf(panelScene({ kind: 'graph', title: 'I = V / R' }), 'render')?.title, 'I = V / R');
  assert.equal(panelOf(panelScene({ kind: 'graph' }), 'render'), null, 'no title: nothing this slice can draw');
  assert.equal(panelOf(panelScene({ kind: 'chart' }), 'render'), null);
  // and the kinds nobody draws here
  for (const kind of ['skill_tree', 'quiz']) assert.equal(panelOf(panelScene({ kind }), 'render'), null, kind);
  assert.equal(panelOf(/** @type {any} */ ({ board: [] }), 'render'), null, 'no panel at all');
});

test('drawsPanel: the one question a template\'s drawsScene and its Python fit must both ask (CG-2)', () => {
  assert.equal(drawsPanel(/** @type {any} */ ({ board: [] })), true, 'no panel: nothing to refuse');
  assert.equal(drawsPanel(panelScene({ kind: 'image' })), true);
  assert.equal(drawsPanel(panelScene({ kind: 'skill_tree', title: 'T' })), false);
  assert.equal(drawsPanel(panelScene({ kind: 'graph' })), false);
  assert.equal(drawsPanel(panelScene({ kind: 'graph', title: 'A plot' })), true);
  assert.equal(drawsPanel(panelScene({ kind: 'skill_tree' }, null, { layout: { show_side_panel: false } })), true,
    'a hidden panel is not drawn at all, so it never sends the scene away');
  // it never depends on the mode: a GIF is claimed in the MP4 too (its title card)
  assert.equal(drawsPanel(panelScene({ kind: 'gif', title: 'G' }, { kind: 'gif', url: 'https://x/g.gif' })), true);
});

test('buildPanel / layoutPanel: a medium contained at its aspect, a terminal card fitted and centred', () => {
  const ctx = ctxFor({ media: (/** @type {any} */ ref, /** @type {any} */ opts) => ({
    el: document.createElement('div'), missing: false, ref, opts, update: () => {},
  }) });
  const box = { x0: 1058, y0: 300, x1: 1824, y1: 900 };
  // a picture: contained at 4:3, centred, on whole px, and asked for in slot `panel`
  const pic = /** @type {any} */ (panelOf(panelScene({ kind: 'image', title: 'P' },
    { kind: 'image', url: '/p.png', width: 800, height: 600 }), 'render'));
  const parts = buildPanel(ctx, pic);
  assert.equal(parts.view?.opts.slot, 'panel');
  assert.equal(parts.label, null, 'a shown medium needs no stand-in label');
  const laid = layoutPanel(ctx, parts, box);
  assert.deepEqual(laid, { x0: 1058, y0: 313, x1: 1824, y1: 888 });
  assert.equal(Math.round(((laid.x1 - laid.x0) / (laid.y1 - laid.y0)) * 100), Math.round((4 / 3) * 100));
  for (const v of [parts.frame.style.left, parts.frame.style.top, parts.frame.style.width, parts.frame.style.height]) {
    assert.ok(/^-?\d+px$/.test(v), `${v} is whole px`);
  }
  // a terminal: the box's width, the fitted card's height, centred; its size never below the slot's minimum
  const term = /** @type {any} */ (panelOf(panelScene({ kind: 'terminal', terminal: { command: 'ls', output: 'a\nb' } }), 'render'));
  const tParts = buildPanel(ctx, term);
  assert.ok(tParts.term);
  const tBox = layoutPanel(ctx, tParts, box);
  assert.equal(tBox.x1 - tBox.x0, 766);
  assert.ok(/** @type {any} */ (tParts.term).size >= PANEL_TERM.landscape.min);
  assert.ok(/** @type {any} */ (tParts.term).size <= PANEL_TERM.landscape.max);
  // a stand-in title card, on the card surface
  const titled = /** @type {any} */ (panelOf(panelScene({ kind: 'graph', title: 'I = V / R' }), 'render'));
  const tc = buildPanel(ctx, titled);
  assert.equal(tc.label?.textContent, 'I = V / R');
  assert.equal(tc.view, null);
  ctx.root.remove();
});

test('panelMediaBox: the panel\'s box for slot `panel` when it has a medium, else nothing', () => {
  const box = { x0: 1058, y0: 54, x1: 1824, y1: 912 };
  const scene = panelScene({ kind: 'image', title: 'P' }, { kind: 'image', url: '/p.png', width: 800, height: 600 });
  assert.deepEqual(panelMediaBox(scene, box, 'panel'), { w: 766, h: 858 });
  assert.equal(panelMediaBox(scene, box, 'main'), null);
  assert.equal(panelMediaBox(scene, box, undefined), null);
  assert.equal(panelMediaBox(panelScene({ kind: 'terminal', terminal: { command: 'ls' } }), box, 'panel'), null);
  assert.equal(panelMediaBox(panelScene({ kind: 'graph', title: 'G' }), box, 'panel'), null);
  assert.equal(panelMediaBox(/** @type {any} */ ({ board: [] }), box, 'panel'), null);
});

test('paintPanel: the entrance, every property on every frame, and a clip that is never moved', () => {
  const ctx = ctxFor({ media: (/** @type {any} */ ref) => ({ el: document.createElement('div'), missing: false, ref, update: () => {} }) });
  const pic = /** @type {any} */ (panelOf(panelScene({ kind: 'image', title: 'P' },
    { kind: 'image', url: '/p.png', width: 800, height: 600 }), 'render'));
  const parts = buildPanel(ctx, pic);
  paintPanel(ctx, parts, 0, /** @type {any} */ ({ terminalLines: [] }));
  assert.equal(parts.frame.style.visibility, 'hidden');
  assert.equal(parts.frame.style.opacity, '0');
  paintPanel(ctx, parts, PANEL_DELAY + PANEL_ENTER + 0.2, /** @type {any} */ ({ terminalLines: [] }));
  assert.equal(parts.frame.style.visibility, 'visible');
  assert.equal(parts.frame.style.opacity, '1');
  assert.equal(parts.frame.style.transform, 'none');
  // mid-entrance the rise is a whole-pixel 2D translate (§P), never a scale
  paintPanel(ctx, parts, PANEL_DELAY + PANEL_ENTER / 2, /** @type {any} */ ({ terminalLines: [] }));
  assert.match(parts.frame.style.transform, /^translate\(0, \d+px\)$/);
  // a clip is never moved at all (a scaled or translated clip was measured order-dependent, B2c)
  const clip = /** @type {any} */ (panelOf(panelScene({ kind: 'manim', title: 'M' },
    { kind: 'video', asset_key: 'k', url: '/v.mp4', width: 800, height: 600 }), 'render'));
  const cParts = buildPanel(ctx, clip);
  paintPanel(ctx, cParts, PANEL_DELAY + PANEL_ENTER / 2, /** @type {any} */ ({ terminalLines: [] }));
  assert.equal(cParts.frame.style.transform, 'none');
  // a terminal's lines follow the scene state, and every one is written on every frame (never '')
  const term = /** @type {any} */ (panelOf(panelScene({ kind: 'terminal', terminal: { command: 'ls', output: 'a\nb' } }), 'render'));
  const tParts = buildPanel(ctx, term);
  const lines = /** @type {any} */ (tParts.term).lines;
  paintPanel(ctx, tParts, 1, /** @type {any} */ ({ terminalLines: [] }));
  assert.deepEqual(lines.map((/** @type {any} */ l) => l.style.visibility), lines.map(() => 'hidden'));
  paintPanel(ctx, tParts, 1, /** @type {any} */ ({ terminalLines: ['a'] }));
  assert.equal(lines[0].style.visibility, 'visible');
  assert.equal(lines[1].style.visibility, 'hidden');
  ctx.root.remove();
});

test('panelTexts: a terminal card is critical in slot `terminal`; a stand-in title card is not', () => {
  const ctx = ctxFor();
  const term = /** @type {any} */ (panelOf(panelScene({ kind: 'terminal', terminal: { command: 'ls', output: 'a' } }), 'render'));
  const tParts = buildPanel(ctx, term);
  tParts.alpha = 1;
  const [entry] = panelTexts(ctx, tParts);
  assert.equal(entry.slot, 'terminal');
  assert.equal(entry.critical, true);
  assert.equal(entry.role, 'body');
  assert.equal(entry.bg, ctx.roles.color('surface'));
  assert.ok(/** @type {string} */ (entry.text).startsWith('$ ls'));
  const titled = /** @type {any} */ (panelOf(panelScene({ kind: 'graph', title: 'A plot' }), 'render'));
  const lParts = buildPanel(ctx, titled);
  lParts.alpha = 0.5;
  const [label] = panelTexts(ctx, lParts);
  assert.equal(label.critical, false);
  assert.equal(label.role, 'label');
  assert.equal(label.size, PANEL_LABEL_PX.landscape);
  assert.equal(label.alpha, 0.5);
  // a panel showing its medium reports no text of its own
  const ctx2 = ctxFor({ media: (/** @type {any} */ ref) => ({ el: document.createElement('div'), missing: false, ref, update: () => {} }) });
  const pic = /** @type {any} */ (panelOf(panelScene({ kind: 'image', title: 'P' },
    { kind: 'image', url: '/p.png', width: 800, height: 600 }), 'render'));
  assert.deepEqual(panelTexts(ctx2, buildPanel(ctx2, pic)), []);
  ctx.root.remove();
  ctx2.root.remove();
});

test('panelFocusRect: `panel` the frame as a shape, `panel:row:<n>` a terminal line as a bar; nothing else', () => {
  const ctx = ctxFor();
  const term = /** @type {any} */ (panelOf(panelScene({ kind: 'terminal', terminal: { command: 'ls', output: 'a\nb' } }), 'render'));
  const parts = buildPanel(ctx, term);
  parts.alpha = 1;
  const lines = /** @type {any} */ (parts.term).lines;
  /** @param {Element} el */
  const rects = (el) => (el === parts.frame ? { x: 1058.4, y: 300.2, w: 766.3, h: 400.9 }
    : el === lines[1] ? { x: 1090, y: 420, w: 640, h: 56 } : null);
  const c = /** @type {any} */ ({ ...ctx, rectOf: rects });
  assert.deepEqual(panelFocusRect(c, parts, 'panel'),
    { rect: { x: 1058, y: 300, w: 767, h: 401 }, shape: 'rect', radius: RADIUS.media, el: parts.el, kind: 'shape' });
  assert.deepEqual(panelFocusRect(c, parts, 'panel:row:1'),
    { rect: { x: 1090, y: 420, w: 640, h: 56 }, shape: 'bar', radius: 0, el: parts.el, kind: 'shape' });
  assert.equal(panelFocusRect(c, parts, 'panel:row:5'), null, 'a row the panel has not got');
  assert.equal(panelFocusRect(c, parts, 'panel:column:0'), null, 'no column form here');
  assert.equal(panelFocusRect(c, parts, 'd1'), null, 'an item is the template\'s own');
  assert.equal(panelFocusRect(c, parts, 'not a target'), null);
  // nothing while the panel is off screen
  parts.alpha = 0;
  assert.equal(panelFocusRect(c, parts, 'panel'), null);
  ctx.root.remove();
});
