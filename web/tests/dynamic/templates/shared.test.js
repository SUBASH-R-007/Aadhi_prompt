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
  ICON_PATHS_MAX, ICON_PATH_MAX_CHARS, ICON_PATH_RE, ICON_STROKE_MAX, RADIUS, SPACE, STATE_BORDER, TEXT_RISE, TINT_BASE,
  boxRect, containRect, enterAt, entityColour, fitLines, gridSpan, group, insetBox, itemStateParts, layoutStrike,
  mediaAspect, paintItemState, paintRise, paintTextEntrance, paintTint, place, pushHeadroom, region, revealMask,
  richWords, sanitiseIcon, seconds, slotRange, slotSpec, storedIcon, texTokens, tintTargets, tintTex,
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
