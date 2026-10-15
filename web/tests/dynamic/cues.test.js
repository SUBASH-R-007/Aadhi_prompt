// @ts-check
// cues.js (DYNAMIC_VIDEO_DESIGN §4.8, R-26; B2 plan §1.5, §1.7): the target grammar the stage reads, the cue colours,
// which events a template is given, the two cues' timing and styles (with their contrast at every sweep step), the
// look_here outline geometry, the dim and the push, the reduced-motion mapping (live only), and the stage wiring:
// a scene whose template draws no cue keeps B2b's DOM and frame keys exactly.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';

import { installDom, uninstallDom } from '../player/_dom.js';
import { CATALOGUE } from '../../js/dynamic/catalogue.gen.js';
import {
  LAYOUT_TOKENS, LIGHT_MARKER_ALPHA, Painter, TARGET_FORMS, ZONES, cueColours, entityRole, makeRoles, parseTarget,
  templateBehaviour,
} from '../../js/dynamic/core.js';
import {
  CUE_HOLD, DIM_ALPHA, DIM_RAMP, EXIT_FACTOR, FOCUS_OUTSET, FOCUS_PX, OUTLINE_DRAW, PUSH_IN, PUSH_SCALE, TERM_DRAW,
  UNDERLINE_PX, asElements, bandGradient, clampOf, cueEvents, cueLevel, cueNeeds, outlinePath, pieceBand, termBand, termSpans,
  termStyle, termsOf, zoneOf,
} from '../../js/dynamic/cues.js';
import { createDynamicStage, motionOf, DEFAULT_TEXT_ENTRANCE } from '../../js/dynamic/stage.js';
import { composite, contrastRatio } from '../../js/motion/color.js';
import { renderRich } from '../../js/player/richtext.js';
import { R1_PALETTES, dynamicTimeline } from './_fixture.js';

before(() => installDom());
after(() => uninstallDom());

const fast = { texIdle: () => Promise.resolve(), frame: () => Promise.resolve() };

// --- the target grammar (plan §1.7; templates.py parse_target, the Python test's cases) -------------------------------

const VALID = /** @type {[string, any][]} */ ([
  ['i1', { form: 'item', item: 'i1' }],
  ['step-2', { form: 'item', item: 'step-2' }],
  ['c1:line:0', { form: 'item:line', item: 'c1', index: 0 }],
  ['c1:line:12', { form: 'item:line', item: 'c1', index: 12 }],
  ['f1:var:R', { form: 'item:var', item: 'f1', symbol: 'R' }],
  ['f1:var:\\Omega_{in}', { form: 'item:var', item: 'f1', symbol: '\\Omega_{in}' }],
  ['f1:var:\\Delta V', { form: 'item:var', item: 'f1', symbol: '\\Delta V' }],
  ['f1:var:a:b', { form: 'item:var', item: 'f1', symbol: 'a:b' }],
  ['f1:var:0', { form: 'item:var', item: 'f1', symbol: '0' }],
  ['ex:step:3', { form: 'item:step', item: 'ex', index: 3 }],
  ['t1:row:0', { form: 'item:row', item: 't1', index: 0 }],
  ['t1:column:2', { form: 'item:column', item: 't1', index: 2 }],
  ['b2:term:0', { form: 'item:term', item: 'b2', index: 0 }],
  ['b2:term:11', { form: 'item:term', item: 'b2', index: 11 }],
  ['panel', { form: 'panel' }],
  ['panel:bar:0:3', { form: 'panel:bar', series: 0, index: 3 }],
  ['panel:point:1:0', { form: 'panel:point', series: 1, index: 0 }],
  ['panel:row:4', { form: 'panel:row', index: 4 }],
  ['panel:column:1', { form: 'panel:column', index: 1 }],
  ['option:0', { form: 'option', index: 0 }],
  ['option:3', { form: 'option', index: 3 }],
]);
const MALFORMED = [
  '', 'I1', '-a', 'a'.repeat(65), 'a1:', 'a1:line', 'a1:line:', 'a1:line:01', 'a1:line:-1', 'a1:line:x', 'a1:line:1.0',
  'a1:line:1:2', 'a1:foo:1', 'a1:var:', 'a1:var: V', 'a1:var:V ', 'a1:var:V\n', `a1:var:${'x'.repeat(81)}`, 'panel:',
  'panel:bar', 'panel:bar:0', 'panel:bar:x:1', 'panel:bar:0:1:2', 'panel:pie:0:1', 'panel:row', 'panel:row:1:2',
  'option', 'option:', 'option:a', 'option:1:2', 'option:-1', 'a1:row:1234567', ' i1', 'i1 ', 'a1:term', 'a1:term:',
  'a1:term:01', 'a1:term:x', 'a1:term:1:2', 'panel:term:0',
];

test('parseTarget reads the §1.7 grammar as templates.py does (its test cases, B2c\'s <item>:term:<n> included)', () => {
  for (const [text, want] of VALID) assert.deepEqual(parseTarget(text), want, text);
  for (const text of MALFORMED) assert.equal(parseTarget(text), null, JSON.stringify(text));
  for (const bad of [null, undefined, 3, {}]) assert.equal(parseTarget(bad), null);
  assert.deepEqual([...TARGET_FORMS], [...CATALOGUE.targets.forms]);
  assert.ok(TARGET_FORMS.includes('item:term'));
  assert.deepEqual(new Set(VALID.map(([, t]) => t.form)), new Set(TARGET_FORMS), 'every form is covered');
});

// --- colours, behaviour and the entity map --------------------------------------------------------------------------

test('cueColours: dark-on-mark marker, mark underline (dark); mark at 35% under ink, ink underline (light): the catalogue\'s cue colours', () => {
  for (const mode of /** @type {const} */ (['dark', 'light'])) {
    const roles = makeRoles(/** @type {any} */ ({ content: CATALOGUE.content[mode] }), null);
    assert.equal(roles.mode, mode);
    const c = cueColours(roles);
    const want = CATALOGUE.cues[mode];
    assert.deepEqual({ markerInk: c.markerInk.toUpperCase(), markerAlpha: c.markerAlpha, underline: c.underline.toUpperCase() },
      { markerInk: want.marker_ink.toUpperCase(), markerAlpha: want.marker_alpha, underline: want.underline.toUpperCase() }, mode);
    assert.equal(c.mark, CATALOGUE.content[mode].mark);
  }
  assert.equal(LIGHT_MARKER_ALPHA, CATALOGUE.cues.light.marker_alpha);
});

test('templateBehaviour: what each template consumes and whether it may push (the catalogue registry)', () => {
  assert.deepEqual(templateBehaviour('board_generic'), { consumes: { kinds: ['reveal', 'fill'], targets: ['item'] }, pushOk: false });
  const list = templateBehaviour('list_build');
  assert.ok(list && list.consumes.kinds.includes('cue_term') && list.consumes.kinds.includes('look_here') && !list.pushOk);
  const media = templateBehaviour('media_split');
  assert.ok(media && media.pushOk && media.consumes.targets.includes('panel'));
  assert.equal(templateBehaviour('nope'), null);
  assert.equal(templateBehaviour('__proto__'), null);
});

test('entityRole: the lesson-wide map (R-30), by the symbol as written, else without its spaces', () => {
  const map = { R: 0, '\\Delta V': 1, 'v_0': 2, bad: 3 };
  assert.equal(entityRole(map, 'R'), 'entity:0');
  assert.equal(entityRole(map, '\\Delta V'), 'entity:1');
  assert.equal(entityRole(map, 'v_ 0'), 'entity:2');
  assert.equal(entityRole(map, 'bad'), null, 'only slots 0..2');
  assert.equal(entityRole(map, 'x'), null);
  assert.equal(entityRole(map, 'toString'), null, 'own keys only');
  assert.equal(entityRole(null, 'R'), null);
});

// --- events ---------------------------------------------------------------------------------------------------------

/** @param {any[]} events @param {Record<string, any>} [extra] */
function sceneWith(events, extra = {}) {
  return /** @type {any} */ ({ scene_id: 's', index: 0, type: 'content', start: 0, duration: 10, board: [], beats: [], events, ...extra });
}

test('cueEvents: only cue kinds the template consumes, on targets of forms it resolves, sorted, with their holds', () => {
  const events = [
    { t: 3, kind: 'look_here', target: 'b2' },
    { t: 1, kind: 'cue_term', target: 'b1', dur: 0.1 },
    { t: 2, kind: 'cue_term', target: 'b1', dur: 4 },
    { t: 1, kind: 'reveal', target: 'b1' },
    { t: 4, kind: 'cue_term', target: 'panel' }, // a form list_build does not resolve
    { t: 5, kind: 'cue_term', target: 'B1' }, // not in the grammar
    { t: Number.NaN, kind: 'cue_term', target: 'b1' },
    { t: 6, kind: 'cue_term', target: null },
    { t: 1, kind: 'look_here', target: 'b3' },
  ];
  const got = cueEvents(sceneWith(events), 'list_build');
  assert.deepEqual(got.map((e) => [e.kind, e.target, e.start, e.hold]), [
    ['cue_term', 'b1', 1, 1 + TERM_DRAW], // dur shorter than the sweep: held at least the sweep
    ['look_here', 'b3', 1, 1 + CUE_HOLD],
    ['cue_term', 'b1', 2, 6],
    ['look_here', 'b2', 3, 3 + CUE_HOLD],
  ]);
  assert.deepEqual(cueEvents(sceneWith(events), 'board_generic'), [], 'board_generic draws no cue');
  assert.deepEqual(cueEvents(sceneWith(events), 'unknown'), []);
  assert.deepEqual(cueEvents(sceneWith(undefined), 'list_build'), []);
  const needs = cueNeeds(sceneWith(events), 'list_build');
  assert.deepEqual([needs.camera, needs.layer, needs.events.length], [false, true, 4]);
  const push = cueNeeds(sceneWith([{ t: 1, kind: 'look_here', target: 'panel' }]), 'media_split');
  assert.deepEqual([push.camera, push.layer], [true, true]);
  const terms = cueNeeds(sceneWith([{ t: 1, kind: 'cue_term', target: 'b1' }]), 'media_split');
  assert.deepEqual([terms.camera, terms.layer, terms.events.length], [false, false, 1], 'no camera without a look_here');
});

test('cue timing: the sweep in, the hold, the sweep off from the left (exits 30% shorter)', () => {
  const [cue] = cueEvents(sceneWith([{ t: 2, kind: 'cue_term', target: 'b1', dur: 1 }]), 'list_build');
  assert.equal(termBand(cue, 1.99), null);
  assert.equal(termBand(cue, 2), null, 'nothing swept yet');
  const mid = /** @type {{a: number, b: number}} */ (termBand(cue, 2 + TERM_DRAW / 2));
  assert.ok(mid.a === 0 && mid.b > 0.3 && mid.b < 1, JSON.stringify(mid));
  assert.deepEqual(termBand(cue, 2.5), { a: 0, b: 1 });
  assert.deepEqual(termBand(cue, 3), { a: 0, b: 1 }, 'held to t + dur');
  const off = /** @type {{a: number, b: number}} */ (termBand(cue, 3 + (TERM_DRAW * EXIT_FACTOR) / 2));
  assert.ok(off.a > 0 && off.a < 1 && off.b === 1, JSON.stringify(off));
  assert.equal(termBand(cue, 3 + TERM_DRAW * EXIT_FACTOR), null);
  assert.equal(cueLevel(0, 1, 0.3, 2, 0.3), 0);
  assert.equal(cueLevel(1.5, 1, 0.3, 2, 0.3), 1);
  assert.equal(cueLevel(2.3, 1, 0.3, 2, 0.3), 0);
});

// --- cue.term styles and their contrast (plan §1.5; §4.7: ink on mark >= 12:1, underline >= 3:1) --------------------

/**
 * The colour a hard-stop band gradient gives at fraction x (0..1), parsed back from its CSS.
 * @param {string} css @param {number} x
 */
function gradientAt(css, x) {
  const m = /linear-gradient\(90deg, (\S+) ([\d.]+)%, (\S+) ([\d.]+)%, \S+ ([\d.]+)%, \S+ ([\d.]+)%\)/.exec(css);
  assert.ok(m, css);
  const [, outside, a, inside, , b] = /** @type {RegExpExecArray} */ (m);
  const p = x * 100;
  return p >= Number(a) && p < Number(b) ? inside : outside;
}

test('termStyle: every property cleared when off; marker and underline per mode as plan §1.5 says', () => {
  const dark = cueColours(makeRoles(/** @type {any} */ ({ content: CATALOGUE.content.dark }), null));
  const off = termStyle('marker', 'dark', dark, null);
  assert.ok(Object.values(off).every((v) => v === ''), 'nothing left on the span');
  const u = termStyle('underline', 'dark', dark, { a: 0, b: 0.5 });
  assert.equal(u['background-size'], `100% ${UNDERLINE_PX}px`);
  assert.equal(u['background-position'], '0 100%');
  assert.equal(u['box-decoration-break'], 'clone');
  assert.equal(u['-webkit-text-fill-color'], '', 'an underline never touches the glyphs');
  assert.equal(gradientAt(u['background-image'], 0.25), dark.underline);
  assert.equal(gradientAt(u['background-image'], 0.75), 'transparent');
  const m = termStyle('marker', 'dark', dark, { a: 0.2, b: 0.6 });
  assert.equal(m['-webkit-text-fill-color'], 'transparent');
  assert.equal(m['background-clip'], 'text, border-box');
  assert.equal(m['-webkit-background-clip'], 'text, border-box');
  const [glyphs, band] = m['background-image'].split('), ').map((x, i) => (i === 0 ? `${x})` : x));
  assert.equal(gradientAt(glyphs, 0.1), 'currentColor', 'unswept glyphs keep the span\'s own ink');
  assert.equal(gradientAt(glyphs, 0.4), dark.markerInk);
  assert.equal(gradientAt(band, 0.4), dark.mark);
  assert.equal(gradientAt(band, 0.7), 'transparent');
  const light = cueColours(makeRoles(/** @type {any} */ ({ content: CATALOGUE.content.light }), null));
  const lm = termStyle('marker', 'light', light, { a: 0, b: 1 });
  assert.equal(lm['-webkit-text-fill-color'], '', 'light families keep their ink on the 35% marker');
  assert.equal(gradientAt(lm['background-image'], 0.5), `${light.mark}59`, '35% = 0x59');
  assert.equal(bandGradient('a', 'b', 0, 1), 'linear-gradient(90deg, a 0.00%, b 0.00%, b 100.00%, a 100.00%)');
});

test('cue.term contrast at every sweep step, on every R1 palette: never light on yellow, never dark on the page', () => {
  for (const { name, stored } of R1_PALETTES) {
    const family = CATALOGUE.families[name.split('/')[0]];
    const content = CATALOGUE.content[family.mode];
    const roles = makeRoles(/** @type {any} */ ({ content }), /** @type {any} */ ({ tokens: stored }));
    const c = cueColours(roles);
    const ink = roles.color('ink');
    for (const page of [stored.bg, stored['bg-alt'], stored['bg-tint'], roles.color('surface')]) {
      for (const sweep of [0, 0.25, 0.5, 0.75, 1]) {
        const band = { a: 0, b: sweep };
        for (const style of /** @type {const} */ (['marker', 'underline'])) {
          const css = termStyle(style, family.mode, c, sweep > 0 ? band : null);
          for (const x of [0.05, 0.3, 0.55, 0.8, 0.98]) {
            const swept = sweep > 0 && x < sweep;
            if (style === 'underline') {
              if (swept) assert.ok(contrastRatio(c.underline, page) >= 3, `${name} underline on ${page}`);
              continue;
            }
            if (family.mode === 'dark') {
              const glyph = swept ? c.markerInk : ink;
              const behind = swept ? c.mark : page;
              const r = contrastRatio(glyph, behind);
              assert.ok(r >= (swept ? 12 : 4.5), `${name} sweep ${sweep} x ${x}: ${glyph} on ${behind} ${r.toFixed(2)}`);
              if (sweep > 0) assert.equal(gradientAt(css['background-image'].split('), ')[0] + ')', x), swept ? c.markerInk : 'currentColor');
            } else {
              const behind = swept ? composite(`${c.mark}59`, page) : page;
              const r = contrastRatio(ink, behind);
              assert.ok(r >= (swept ? 12 : 4.5), `${name} light sweep ${sweep}: ${r.toFixed(2)}`);
            }
          }
        }
      }
    }
  }
});

test('a term split into word pieces is one term: one ordinal, swept left to right across its pieces', () => {
  const host = document.createElement('div');
  host.innerHTML = '<span class="rt-k" data-dyn-term="0">electric</span><span class="rt-k" data-dyn-term="0"> </span>'
    + '<span class="rt-k" data-dyn-term="0">current</span> and <span class="rt-k" data-dyn-term="1">power</span>';
  const plain = document.createElement('div');
  plain.innerHTML = '<span class="rt-k">a</span> <span class="rt-k">b</span>';
  assert.deepEqual(termsOf([host, plain]).map((t) => t.map((s) => s.textContent).join('')), ['electric current', 'power', 'a', 'b']);
  // the band of a term over [0, 1] as each piece sees it: electric 0-0.5, space 0.5-0.5625, current 0.5625-1
  assert.deepEqual(pieceBand({ a: 0, b: 0.25 }, 0, 0.5), { a: 0, b: 0.5 });
  assert.equal(pieceBand({ a: 0, b: 0.25 }, 0.5625, 1), null, 'not reached yet');
  assert.deepEqual(pieceBand({ a: 0, b: 1 }, 0.5625, 1), { a: 0, b: 1 });
  assert.deepEqual(pieceBand({ a: 0.75, b: 1 }, 0, 0.5), null, 'swept off already');
  assert.equal(pieceBand(null, 0, 1), null);
  assert.equal(pieceBand({ a: 0, b: 1 }, 0.5, 0.5), null);
});

// --- look_here geometry ---------------------------------------------------------------------------------------------

test('outlinePath: outset >= 8 px clear of the target, 4 px stroke, rect / ring / bar, clamped to the critical zone', () => {
  const zone = ZONES.landscape.critical[0];
  const grow = FOCUS_OUTSET + FOCUS_PX / 2;
  const rect = /** @type {any} */ (outlinePath({ rect: { x: 400, y: 300, w: 200, h: 100 }, shape: 'rect', radius: 24 }, zone));
  assert.deepEqual(rect.box, { x0: 400 - grow, y0: 300 - grow, x1: 600 + grow, y1: 400 + grow });
  assert.ok(rect.box.x0 + FOCUS_PX / 2 <= 400 - FOCUS_OUTSET, 'the inner edge clears the target by the outset');
  assert.match(rect.d, /^M/);
  assert.ok(Math.abs(rect.length - (2 * (220 - 2 * 34) + 2 * (120 - 2 * 34) + 2 * Math.PI * 34)) < 1e-6, 'radius 24 + outset');
  const bar = /** @type {any} */ (outlinePath({ rect: { x: 400, y: 300, w: 600, h: 40 }, shape: 'bar', radius: 0 }, zone));
  assert.ok(bar.d.includes(`A${(60 / 2).toFixed(1)}`), 'a pill: radius half its height');
  const ring = /** @type {any} */ (outlinePath({ rect: { x: 500, y: 500, w: 60, h: 80 }, shape: 'ring', radius: 0 }, zone));
  assert.ok(ring.d.includes('A60.0 60.0'), ring.d);
  // clamped: a target at the zone's corner
  const edge = /** @type {any} */ (outlinePath({ rect: { x: zone.x0, y: zone.y0, w: 100, h: 50 }, shape: 'rect', radius: 0 }, zone));
  assert.deepEqual([edge.box.x0, edge.box.y0], [zone.x0 + FOCUS_PX / 2, zone.y0 + FOCUS_PX / 2]);
  for (const v of [rect, bar, ring, edge]) for (const k of ['x0', 'y0', 'x1', 'y1']) assert.equal(v.box[k] * 2, Math.round(v.box[k] * 2), 'half px');
  assert.equal(outlinePath({ rect: { x: 1, y: 1, w: 0, h: 10 }, shape: 'rect', radius: 0 }, zone), null);
  // 9:16: the zone that holds the target (the lower one under the action rail is narrower)
  const portrait = ZONES.portrait;
  const low = zoneOf(portrait, { x: 200, y: 1000, w: 100, h: 60 });
  assert.ok(low && low.y0 <= 1030 && low.y1 >= 1030);
  assert.equal(zoneOf({ ...portrait, critical: [] }, { x: 0, y: 0, w: 1, h: 1 }), null);
});

test('clampOf: a 9:16 target across y 840 is clamped to both critical boxes (shared x, combined y), never cut at 840', () => {
  const portrait = ZONES.portrait;
  const [upper, lower] = portrait.critical;
  assert.deepEqual([upper.x0, upper.y1, lower.x1, lower.y0], [192, 840, 780, 840], 'the 9:16 boxes this test assumes');
  const across = { x: 204, y: 790, w: 564, h: 120 }; // a list row of x 192-780 whose text crosses y 840
  assert.deepEqual(clampOf(portrait, across), { x0: 192, y0: 288, x1: 780, y1: 1248 });
  const path = /** @type {any} */ (outlinePath({ rect: across, shape: 'rect', radius: 12 }, clampOf(portrait, across)));
  const free = /** @type {any} */ (outlinePath({ rect: across, shape: 'rect', radius: 12 }, null));
  assert.deepEqual(path.box, free.box, 'not clamped into the text');
  const cut = /** @type {any} */ (outlinePath({ rect: across, shape: 'rect', radius: 12 }, zoneOf(portrait, across)));
  assert.ok(cut.box.y0 > free.box.y0 || cut.box.y1 < free.box.y1, 'the one-box clamp would have cut it');
  // inside one box: that box, as before (a target touching 840 from above overlaps only the upper one)
  assert.equal(clampOf(portrait, { x: 300, y: 700, w: 200, h: 140 }), upper);
  assert.equal(clampOf(portrait, { x: 300, y: 900, w: 200, h: 100 }), lower);
  assert.equal(clampOf(ZONES.landscape, { x: 400, y: 300, w: 10, h: 10 }), ZONES.landscape.critical[0]);
  // outside every box: zoneOf's nearest
  assert.equal(clampOf(portrait, { x: 900, y: 100, w: 50, h: 50 }), zoneOf(portrait, { x: 900, y: 100, w: 50, h: 50 }));
});

// --- the cue drawer, through a stand-in template ---------------------------------------------------------------------

/**
 * A stand-in template with list_build's or media_split's id (so the catalogue says what it consumes): one group per
 * board item, its text rich-lite (so [[terms]] are `.rt-k`), a fixed rect per item; it writes its own opacity only on
 * the text inside each group, never on the group (plan §1.4).
 * @param {string} id
 */
function standIn(id) {
  /** @type {any} */
  const tpl = {
    id,
    calls: /** @type {string[]} */ ([]),
    ctxSeen: /** @type {any} */ (null),
    build(/** @type {any} */ ctx) {
      tpl.ctxSeen = ctx;
      const items = (ctx.scene.board || []).map((/** @type {any} */ item, /** @type {number} */ i) => {
        const text = document.createElement('div');
        text.className = 'sx-text';
        text.appendChild(renderRich(item.text, { pending: ctx.pending, renderTex: ctx.renderTex }));
        const g = document.createElement('div');
        g.className = 'sx-group';
        g.appendChild(text);
        ctx.root.appendChild(g);
        return { id: item.id, g, text, rect: { x: 200, y: 200 + 120 * i, w: 600, h: 80 } };
      });
      const panel = document.createElement('div');
      ctx.root.appendChild(panel);
      return { items, panel };
    },
    update(/** @type {any} */ ctx, /** @type {number} */ t) {
      tpl.calls.push(`update ${t}`);
      for (const it of ctx.view.items) ctx.paint.style(it.text, 'opacity', t > 0.5 ? '1' : '0.5');
    },
    texts(/** @type {any} */ ctx) {
      return ctx.view.items.map((/** @type {any} */ it) => ({ el: it.text, role: 'body', critical: true, ink: ctx.roles.color('ink'), bg: ctx.roles.color('surface'), alpha: 1 }));
    },
    focusRect(/** @type {any} */ ctx, /** @type {string} */ target) {
      tpl.calls.push(`focus ${target}`);
      if (target === 'panel') return { rect: { x: 1000, y: 300, w: 700, h: 400 }, shape: 'rect', radius: 24, el: ctx.view.panel };
      const it = ctx.view.items.find((/** @type {any} */ x) => x.id === target.split(':')[0]);
      return it ? { rect: it.rect, shape: 'rect', radius: 24, el: it.g } : null;
    },
    itemEl(/** @type {any} */ ctx, /** @type {string} */ itemId) {
      const it = ctx.view.items.find((/** @type {any} */ x) => x.id === itemId);
      return it ? it.text : null;
    },
    dimGroups(/** @type {any} */ ctx) {
      return [...ctx.view.items.map((/** @type {any} */ it) => it.g), ctx.view.panel];
    },
  };
  return tpl;
}

/**
 * A one-scene lecture drawn by `tpl` (the stage's `templates` seam), with `events`.
 * @param {any} tpl @param {any[]} events @param {'landscape' | 'portrait'} [format] @param {(tl: any) => void} [edit]
 */
function oneScene(tpl, events, format = 'landscape', edit = () => undefined) {
  const tl = dynamicTimeline(format);
  const scene = tl.scenes[0];
  scene.look.template = tpl.id;
  scene.board = [
    { id: 'b1', kind: 'bullet', text: 'Voltage is the [[push]]' },
    { id: 'b2', kind: 'bullet', text: 'The [[current]] flows, the [[charge]] moves' },
  ];
  scene.events = events;
  tl.scenes = [scene];
  tl.total_duration = scene.duration;
  tl.chapters = [];
  edit(tl);
  return tl;
}

/** @param {any} tl @param {any} tpl @param {any} [opts] */
function stageFor(tl, tpl, opts = {}) {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const stage = createDynamicStage(root, tl, { mode: 'render', ...fast, templates: { [tpl.id]: tpl }, ...opts });
  return { stage, root, done: () => { stage.destroy(); root.remove(); } };
}

/** @param {any} stage */
const sceneEl = (stage) => /** @type {HTMLElement} */ (stage.el.querySelector('.dyn-scene'));

test('the stage applies cue.term to the template\'s term spans: the whole item, or its n-th term', async () => {
  const tpl = standIn('list_build');
  const tl = oneScene(tpl, [{ t: 1, kind: 'cue_term', target: 'b1' }, { t: 4, kind: 'cue_term', target: 'b2' }]);
  tl.presentation.cue_term_style = 'marker';
  const { stage, done } = stageFor(tl, tpl);
  await stage.seek(0);
  const spans = [...stage.el.querySelectorAll('.rt-k')];
  assert.deepEqual(spans.map((s) => s.textContent), ['push', 'current', 'charge']);
  for (const s of spans) assert.equal(/** @type {HTMLElement} */ (s).style.backgroundImage, '', 'before the cue');
  await stage.seek(45); // t 1.5: the first term is marked
  const st = (/** @type {Element} */ s) => /** @type {HTMLElement} */ (s).style;
  assert.match(st(spans[0]).backgroundImage, /linear-gradient/);
  assert.equal(st(spans[0]).getPropertyValue('-webkit-text-fill-color'), 'transparent', 'dark family: the glyph switch');
  assert.equal(st(spans[1]).backgroundImage, '');
  await stage.seek(5 * 30); // t 5: both terms of b2 (<item> names every term), b1 swept off
  assert.equal(st(spans[0]).backgroundImage, '');
  assert.match(st(spans[1]).backgroundImage, /linear-gradient/);
  assert.match(st(spans[2]).backgroundImage, /linear-gradient/);
  done();
  // <item>:term:<n> marks the n-th term only (a template that resolves the form: termSpans)
  const tpl2 = standIn('list_build');
  const tl2 = oneScene(tpl2, []);
  const one = stageFor(tl2, tpl2);
  await one.stage.seek(0);
  const ctx = tpl2.ctxSeen;
  const parsed = /** @type {any} */ (parseTarget('b2:term:1'));
  assert.deepEqual(termSpans(tpl2, ctx, /** @type {any} */ ({ kind: 'cue_term', target: 'b2:term:1', parsed })).map((s) => s.textContent), ['charge']);
  assert.deepEqual(termSpans(tpl2, ctx, /** @type {any} */ ({ kind: 'cue_term', target: 'b2:term:5', parsed: parseTarget('b2:term:5') })), []);
  assert.deepEqual(termSpans(tpl2, ctx, /** @type {any} */ ({ kind: 'cue_term', target: 'b2', parsed: parseTarget('b2') })).map((s) => s.textContent), ['current', 'charge']);
  assert.deepEqual(asElements(null), []);
  one.done();
});

test('the cues run after the template\'s update (they read its geometry as painted), on the outgoing slot too', async () => {
  const tpl = standIn('list_build');
  const tl = oneScene(tpl, [{ t: 1, kind: 'look_here', target: 'b2' }]);
  const { stage, done } = stageFor(tl, tpl);
  tpl.calls.length = 0;
  await stage.seek(45);
  assert.deepEqual(tpl.calls, ['update 1.5', 'focus b2']);
  done();
});

test('look_here: a 4 px focus outline drawn in 250 ms in the Slot\'s cue layer, the other groups dimmed to 70% over 300 ms', async () => {
  const tpl = standIn('list_build');
  const tl = oneScene(tpl, [{ t: 1, kind: 'look_here', target: 'b2', dur: 2 }]);
  const { stage, done } = stageFor(tl, tpl);
  await stage.seek(0);
  const scene = sceneEl(stage);
  const layer = /** @type {HTMLElement} */ (scene.querySelector('.dyn-cues'));
  assert.ok(layer, 'the cue layer');
  assert.equal(scene.lastElementChild, layer, 'above the content');
  assert.equal(scene.querySelector('.dyn-camera'), null, 'no camera: list_build never pushes');
  const path = /** @type {SVGElement} */ (layer.querySelector('path.dyn-cue-outline'));
  assert.equal(path.getAttribute('stroke'), tl.presentation.content.focus);
  assert.equal(path.getAttribute('stroke-width'), String(FOCUS_PX));
  const groups = [...stage.el.querySelectorAll('.sx-group')];
  await stage.seek(0);
  assert.equal(path.style.visibility, 'hidden');
  assert.deepEqual(groups.map((g) => /** @type {HTMLElement} */ (g).style.opacity), ['', '']);
  await stage.seek(30 + 3); // t 1.1: drawing, dimming
  const len = Number(path.getAttribute('stroke-dasharray')?.split(' ')[0]);
  const offset = Number(path.getAttribute('stroke-dashoffset'));
  assert.ok(offset > 0 && offset < len, `${offset} of ${len}`);
  const mid = Number(/** @type {HTMLElement} */ (groups[0]).style.opacity);
  assert.ok(mid < 1 && mid > DIM_ALPHA, String(mid));
  assert.equal(/** @type {HTMLElement} */ (groups[1]).style.opacity, '', 'the target\'s group is never dimmed');
  await stage.seek(2 * 30); // t 2: drawn, dimmed
  assert.equal(path.getAttribute('stroke-dashoffset'), '0.00');
  assert.equal(path.style.visibility, 'visible');
  assert.equal(Number(/** @type {HTMLElement} */ (groups[0]).style.opacity), DIM_ALPHA);
  await stage.seek(Math.round((3 + DIM_RAMP + 0.05) * 30)); // after the hold and the ramp back
  assert.equal(path.style.visibility, 'hidden');
  assert.deepEqual(groups.map((g) => /** @type {HTMLElement} */ (g).style.opacity), ['', '']);
  done();
});

test('look_here dims text in textBoxes: its ink and its background at 70% over what lies behind its group', async () => {
  const tpl = standIn('list_build');
  // a stand-in whose texts have a size, so textBoxes reports them (jsdom has no layout)
  const tl = oneScene(tpl, [{ t: 1, kind: 'look_here', target: 'b2', dur: 2 }]);
  const { stage, done } = stageFor(tl, tpl);
  await stage.seek(60);
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const real = proto.getBoundingClientRect;
  proto.getBoundingClientRect = function rect() {
    return this.classList && this.classList.contains('sx-text') ? { left: 0, top: 0, width: 100, height: 40, right: 100, bottom: 40 } : { left: 0, top: 0, width: 1920, height: 1080, right: 1920, bottom: 1080 };
  };
  try {
    const boxes = stage.textBoxes();
    assert.equal(boxes.length, 2);
    const roles = makeRoles(tl.presentation, tl.scenes[0].look);
    const ink = roles.color('ink');
    const surface = roles.color('surface');
    assert.equal(boxes[1].ink, ink, 'the target\'s text: undimmed');
    assert.equal(boxes[1].bg, surface);
    assert.ok(boxes[0].critical, 'dimmed text stays critical');
    assert.notEqual(boxes[0].ink, ink);
    // composited over the background's worst case (what lies behind the group)
    const under = /** @type {any} */ (tpl.ctxSeen).onBackground.bg;
    assert.equal(boxes[0].ink, composite(`${ink}b3`, under));
    assert.equal(boxes[0].bg, composite(`${surface}b3`, under));
    assert.ok(contrastRatio(boxes[0].ink, boxes[0].bg) < contrastRatio(ink, surface), 'dimming costs contrast, and preflight sees it');
  } finally {
    proto.getBoundingClientRect = real;
  }
  done();
});

test('push: a push_ok template\'s look_here scales the Slot\'s camera about the target (no outline, no dim); reduced motion keeps the outline (live only)', async () => {
  const tpl = standIn('media_split');
  const tl = oneScene(tpl, [{ t: 1, kind: 'look_here', target: 'panel', dur: 3 }]);
  const { stage, done } = stageFor(tl, tpl);
  await stage.seek(0);
  const scene = sceneEl(stage);
  const camera = /** @type {HTMLElement} */ (scene.querySelector('.dyn-camera'));
  assert.ok(camera && camera.firstElementChild?.classList.contains('dyn-content'), 'the content inside the camera');
  assert.equal(scene.querySelector('.dyn-cues path'), null, 'no outline path in the push form');
  await stage.seek(0);
  assert.equal(camera.style.transform, '');
  await stage.seek(Math.round((1 + PUSH_IN + 0.5) * 30));
  assert.equal(camera.style.transform, `scale(${(1 + PUSH_SCALE).toFixed(4)})`);
  assert.equal(camera.style.transformOrigin, '1350px 500px', 'the target\'s centre, whole px');
  for (const g of stage.el.querySelectorAll('.sx-group')) assert.equal(/** @type {HTMLElement} */ (g).style.opacity, '');
  await stage.seek(Math.round((4 + PUSH_IN) * 30));
  assert.equal(camera.style.transform, '');
  done();
  // the same lecture under reduced motion in the live player: the outline form
  const live = stageFor(tl, tpl, { mode: 'live', reducedMotion: true });
  live.stage.update(2);
  const path = live.stage.el.querySelector('.dyn-cues path.dyn-cue-outline');
  assert.ok(path, 'the outline form');
  assert.equal(/** @type {HTMLElement} */ (live.stage.el.querySelector('.dyn-camera')).style.transform, '');
  assert.equal(/** @type {SVGElement} */ (path).style.visibility, 'visible');
  live.done();
  // render mode never applies reduced motion (§4.8: the MP4 is never changed by the viewer's setting)
  const render = stageFor(tl, tpl, { reducedMotion: true });
  await render.stage.seek(Math.round((1 + PUSH_IN + 0.5) * 30));
  assert.equal(/** @type {HTMLElement} */ (render.stage.el.querySelector('.dyn-camera')).style.transform, `scale(${(1 + PUSH_SCALE).toFixed(4)})`);
  render.done();
});

test('cue frames are a pure function of t: any order of seeks gives the same DOM and key', async () => {
  const tpl = standIn('list_build');
  const events = [
    { t: 1, kind: 'cue_term', target: 'b1' }, { t: 1.2, kind: 'look_here', target: 'b2', dur: 1.5 },
    { t: 3, kind: 'cue_term', target: 'b2' }, { t: 4, kind: 'look_here', target: 'b1' },
  ];
  const frames = [0, 33, 40, 45, 70, 95, 100, 130, 160, 200];
  /** @param {number[]} order */
  const run = async (order) => {
    const { stage, done } = stageFor(oneScene(tpl, events), tpl);
    /** @type {Record<number, string>} */
    const out = {};
    for (const f of order) {
      const { key } = await stage.seek(f);
      // an emptied style attribute (`style=""`, left by removing the last property) draws nothing: not a difference
      out[f] = `${key}|${stage.el.outerHTML.replace(/ style=""/g, '')}`;
    }
    done();
    return out;
  };
  const forward = await run(frames);
  const backward = await run([...frames].reverse());
  const fresh = Object.fromEntries(await Promise.all(frames.map(async (f) => [f, (await run([f]))[f]])));
  for (const f of frames) {
    assert.equal(backward[f], forward[f], `frame ${f} (reverse)`);
    assert.equal(fresh[f], forward[f], `frame ${f} (fresh page)`);
  }
  assert.ok(new Set(Object.values(forward)).size > 5, 'the cues change the frames');
});

test('a scene whose template draws no cue keeps B2b\'s DOM and frame keys: board_generic with cue events', async () => {
  const keysAndDom = async (/** @type {any[] | undefined} */ events) => {
    const root = document.createElement('div');
    document.body.appendChild(root);
    const tl = dynamicTimeline();
    tl.scenes[0].board[1].text = 'Voltage is the [[push]]';
    if (events) tl.scenes[0].events = events;
    const stage = createDynamicStage(root, tl, { mode: 'render', ...fast });
    const out = [];
    for (const f of [0, 45, 90, 150, 290]) out.push(`${(await stage.seek(f)).key}|${stage.el.outerHTML}`);
    stage.destroy();
    root.remove();
    return out;
  };
  const without = await keysAndDom(undefined);
  const withCues = await keysAndDom([{ t: 1, kind: 'cue_term', target: 'b2' }, { t: 2, kind: 'look_here', target: 'b3' }]);
  assert.deepEqual(withCues, without);
  assert.ok(!without.some((x) => x.includes('dyn-cues') || x.includes('dyn-camera')));
});

test('ctx.motion (the timeline\'s scale and text entrance) and ctx.outgoing (the scene a crossfade enters from)', async () => {
  assert.deepEqual(motionOf({}), { scale: 1, entrance: DEFAULT_TEXT_ENTRANCE });
  assert.deepEqual(motionOf({ motion_scale: 0.85, text_entrance: 'baseline_reveal' }), { scale: 0.85, entrance: 'baseline_reveal' });
  assert.deepEqual(motionOf({ motion_scale: 0, text_entrance: 'Bad Id' }), { scale: 1, entrance: DEFAULT_TEXT_ENTRANCE });
  assert.deepEqual(motionOf({ motion_scale: 9 }), { scale: 1, entrance: DEFAULT_TEXT_ENTRANCE });
  assert.ok(Object.isFrozen(motionOf({})));
  const tpl = standIn('list_build');
  const tl = dynamicTimeline();
  tl.presentation.motion_scale = 1.15;
  tl.presentation.text_entrance = 'fade_rise';
  tl.scenes[1].look.template = 'list_build'; // the chapter card, entered by a 0.45 s crossfade
  tl.scenes[1].board = [{ id: 'c1', kind: 'bullet', text: 'One' }];
  /** @type {any[]} */
  const seen = [];
  const spy = { ...tpl, update(/** @type {any} */ ctx, /** @type {number} */ t) { seen.push([t, ctx.outgoing]); } };
  spy.build = tpl.build;
  const { stage, done } = stageFor(tl, spy);
  await stage.seek(300 + 6); // 0.2 s into scene 1
  const [t, out] = seen[seen.length - 1];
  assert.ok(Math.abs(t - 0.2) < 1e-9);
  assert.equal(out.index, 0);
  assert.equal(out.scene, tl.scenes[0]);
  assert.equal(out.look, tl.scenes[0].look);
  assert.equal(out.kind, 'crossfade');
  assert.ok(Math.abs(out.p - 0.2 / 0.45) < 1e-9);
  assert.ok(Object.isFrozen(out));
  await stage.seek(330); // 1 s in: no transition
  assert.equal(seen[seen.length - 1][1], null);
  assert.deepEqual(tpl.ctxSeen.motion, { scale: 1.15, entrance: 'fade_rise' });
  assert.equal(tpl.ctxSeen.entityRole('R'), null);
  done();
});

test('constants: the catalogue\'s layout tokens and the design\'s timings', () => {
  assert.equal(DIM_ALPHA, 0.7);
  assert.equal(FOCUS_PX, LAYOUT_TOKENS.outline.focus_px);
  assert.ok(FOCUS_OUTSET >= 8);
  assert.equal(UNDERLINE_PX, 4);
  assert.equal(TERM_DRAW, 0.3);
  assert.equal(OUTLINE_DRAW, 0.25);
  assert.ok(DIM_RAMP >= 0.3, 'R-54');
  assert.ok(PUSH_SCALE >= 0.02 && PUSH_SCALE <= 0.03, '§4.3 media_split: <= 3%, §4.8: 2-6%');
});
