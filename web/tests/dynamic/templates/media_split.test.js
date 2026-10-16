// @ts-check
// media_split (B2c pilot; DYNAMIC_VIDEO_DESIGN §4.3 B, B2 plan §A step 3: clip, mediaBox, look_here): the template
// contract and order independence on every fixture (tests/compose/dynamic/fixtures/templates/media_split.*.json: both
// formats, a dark and a light family each, typical text, the longest lint-limit text in wide glyphs, one very long
// word, Tamil, Hindi and Tamil + English + TeX, a terminal), then its behaviour: the 16:9 split and its variants, the
// 9:16 headline / medium / key phrase, the entrances, the push room, mediaBox against the laid-out frame under every
// fix, the clip's frame keys, the cues' hooks, what it reports to preflight. Each behaviour test was seen to fail on a
// mutation of the template (see the report of B2c).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import mediaSplit, {
  DEFAULT_VARIANT, EXIT_FACTOR, LABELS, LAYOUT, LINE_HEIGHT, MEDIA_FROM, MEDIA_MIN_SHARE, MIN_DWELL, TEXT_ENTER, TEXT_KINDS, clipShown, landscapeColumn,
  lineHeight, mediaOf, phraseSchedule,
  portraitBoxes, pushArea, pushTargets, splitRegions, terminalOf, texOf, textItemsOf, variantOf,
} from '../../../js/dynamic/templates/media_split.js';
import { pushHeadroom } from '../../../js/dynamic/templates/_shared.js';
import { PUSH_IN, PUSH_SCALE, cueEvents, cueNeeds, termSpans } from '../../../js/dynamic/cues.js';
import { sceneStateAt } from '../../../js/player/schedule.js';
import { REGIONS, ZONES, parseTarget, templateBehaviour, templateSlots } from '../../../js/dynamic/core.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { mediaKey, mediaSize } from '../../../js/dynamic/media.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
const FIXTURES = readdirSync(DIR).filter((n) => n.startsWith('media_split.') && n.endsWith('.json')).sort();
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const TEMPLATES = { media_split: mediaSplit };

test('the fixtures: both formats, a dark and a light family in each', () => {
  const seen = FIXTURES.map((n) => {
    const tl = load(n);
    return `${tl.presentation.format}:${tl.presentation.mode}`;
  });
  for (const want of ['landscape:dark', 'landscape:light', 'portrait:dark', 'portrait:light']) assert.ok(seen.includes(want), want);
});

for (const name of FIXTURES) {
  test(`${name}: the template contract (plan §1.4, §1.5) and order independence (§7.4)`, async () => {
    const tl = load(name);
    assert.deepEqual(await contractProblems(mediaSplit, tl), []);
    const last = Math.round(tl.total_duration * tl.fps) - 1;
    const frames = [0, 5, 12, 20, 30, 45, 60, 90, 120, 150, 180, 200, 215, 230, 260, last];
    assert.deepEqual(await orderProblems(tl, frames, { templates: TEMPLATES }), []);
  });
}

// ---------------------------------------------------------------------------------------------
// Helpers: a stage with the template (its build context kept), and a small layout model for jsdom (no layout engine):
// text takes ceil(characters x 0.55 em / width) lines of size x line-height; an element placed at layout time keeps
// its left / top / width / height; a word wider than its line breaks.
// ---------------------------------------------------------------------------------------------

/** @param {any} tl @param {{mode?: 'live' | 'render', reducedMotion?: boolean}} [opts] */
function mount(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const ctxs = new Map();
  const wrapped = { ...mediaSplit, build(/** @type {any} */ ctx) { ctxs.set(ctx.index, ctx); return mediaSplit.build(ctx); } };
  const tex = recordingTex();
  const { stage, done } = renderStage(tl, { templates: { media_split: wrapped }, renderTex: tex.renderTex, ...opts });
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
    if (w !== null) {
      const marker = el.closest && el.closest('.dyn-ms--landscape .dyn-ms-text') ? LAYOUT.landscape.marker : 0;
      return w - marker;
    }
  }
  return 1920;
};
/** @param {any} el the element whose text sets a box's height */
const textHost = (el) => (el.matches('.dyn-ms-text, h2, pre, .dyn-word') ? el
  : el.querySelector('.dyn-ms-text, h2, pre') || ((el.textContent || '').trim() ? el : null));
/** @param {any} el */
function fakeHeight(el) {
  const own = px(el.style && el.style.height);
  if (own !== null) return own;
  const host = textHost(el);
  if (!host) return 0;
  const size = px(up(host, 'fontSize')) || 16;
  const lh = Number(up(host, 'lineHeight')) || 1.3;
  const w = widthOf(host);
  if (host.tagName === 'PRE') {
    return [...host.children].reduce((a, c) => a + Math.max(1, Math.ceil(((c.textContent || '').length * 0.6 * size) / w)), 0) * size * lh;
  }
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
      // in the 16:9 column: below the groups before it
      if (e.classList && e.classList.contains('dyn-group') && e.parentElement && e.parentElement.classList.contains('dyn-ms-column')) {
        for (const s of e.parentElement.children) {
          if (s === e) break;
          y += fakeHeight(s) + (px(s.style.marginTop) || 0) + (px(s.style.marginBottom) || 0);
        }
        y += px(e.style.marginTop) || 0;
      }
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
const boxOf = (el) => ({ x0: px(el.style.left), y0: px(el.style.top), x1: (px(el.style.left) || 0) + (px(el.style.width) || 0), y1: (px(el.style.top) || 0) + (px(el.style.height) || 0) });
/** A fixture with its first scene edited. @param {string} name @param {(s: any, tl: any) => void} edit */
function edited(name, edit) {
  const tl = load(name);
  edit(tl.scenes[0], tl);
  return tl;
}
/** `#rrggbb` as jsdom serialises a style colour. @param {string} hex */
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;
/** Frame of scene time t in the first scene. @param {any} tl @param {number} t */
const frameAt = (tl, t) => Math.round(t * tl.fps);

// ---------------------------------------------------------------------------------------------
// Pure parts
// ---------------------------------------------------------------------------------------------

test('splitRegions: 55% medium, 45% text (§4.3, §2.5 regions), the media side by variant, the text a gutter off it', () => {
  const full = REGIONS.landscape.full;
  const right = splitRegions('media_right');
  assert.deepEqual(right.media, { x0: 886, y0: full.y0, x1: 1824, y1: full.y1 }, 'the mirror of left-55');
  assert.deepEqual(right.text, { x0: 96, y0: full.y0, x1: 862 - LAYOUT.landscape.gutter, y1: full.y1 });
  const left = splitRegions('media_left');
  assert.deepEqual(left.media, REGIONS.landscape['left-55']);
  assert.deepEqual(left.text, { ...REGIONS.landscape['right-45'], x0: REGIONS.landscape['right-45'].x0 + LAYOUT.landscape.gutter });
  for (const s of [left, right]) {
    assert.equal(s.media.x1 - s.media.x0, 938);
    assert.equal(s.text.x1 - s.text.x0, 742);
  }
  assert.equal(variantOf(null), DEFAULT_VARIANT);
  assert.equal(variantOf(/** @type {any} */ ({ variant: 'safe' })), 'media_right', "B1's safe is no media side");
  assert.equal(variantOf(/** @type {any} */ ({ variant: 'media_left' })), 'media_left');
  assert.equal(variantOf(/** @type {any} */ ({ variant: 'toString' })), 'media_right');
});

test('pushArea: text inside it, pushed by 1 + PUSH_SCALE about any origin in the bounds, stays inside the zone', () => {
  const zone = { x0: 96, y0: 54, x1: 1824, y1: 912 };
  assert.deepEqual(pushArea(zone, { x0: 500, y0: 500, x1: 500, y1: 500 }, 1), zone, 'no push: the zone itself');
  const s = 1 + PUSH_SCALE;
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let n = 0; n < 300; n++) {
    const ox = [zone.x0 + rnd() * 1728, zone.x0 + rnd() * 1728].sort((a, b) => a - b);
    const oy = [zone.y0 + rnd() * 858, zone.y0 + rnd() * 858].sort((a, b) => a - b);
    const o = { x0: ox[0], x1: ox[1], y0: oy[0], y1: oy[1] };
    const a = pushArea(zone, o);
    for (const [ax, ay] of [[a.x0, a.y0], [a.x1, a.y1], [a.x0, a.y1], [a.x1, a.y0]]) {
      for (const [cx, cy] of [[o.x0, o.y0], [o.x1, o.y1], [o.x0, o.y1], [o.x1, o.y0]]) {
        const x = cx + s * (ax - cx);
        const y = cy + s * (ay - cy);
        assert.ok(x >= zone.x0 - 1e-9 && x <= zone.x1 + 1e-9 && y >= zone.y0 - 1e-9 && y <= zone.y1 + 1e-9, JSON.stringify({ a, o }));
      }
    }
  }
  // tight: a pixel further out leaves the zone for the worst origin
  const o = { x0: 1400, x1: 1400, y0: 480, y1: 480 };
  const a = pushArea(zone, o);
  assert.ok(o.x0 + s * (a.x0 - 1 - o.x0) < zone.x0, 'rounded inwards, by less than a pixel');
});

test('pushTargets: only look_here cues this template consumes', () => {
  const tl = load('media_split.landscape.json');
  const s = tl.scenes[0];
  assert.deepEqual(pushTargets(s), { item: false, panel: true });
  assert.deepEqual(pushTargets({ ...s, events: [{ t: 1, kind: 'look_here', target: 'b2' }] }), { item: true, panel: false });
  assert.deepEqual(pushTargets({ ...s, events: [{ t: 1, kind: 'look_here', target: 'panel:row:2' }] }), { item: false, panel: true });
  assert.deepEqual(pushTargets({ ...s, events: [{ t: 1, kind: 'cue_term', target: 'b1' }] }), { item: false, panel: false });
  assert.deepEqual(pushTargets({ ...s, events: [{ t: 1, kind: 'look_here', target: 'b1:line:0' }] }), { item: false, panel: false }, 'a form it does not consume');
  assert.deepEqual(pushTargets({ ...s, events: [] }), { item: false, panel: false });
  assert.equal(templateBehaviour('media_split')?.pushOk, true, '§4.3: media_split pushes');
  // a scene that shows a clip never pushes (cues.js showsClip), so its text keeps the full regions
  const clip = { kind: 'video', asset_key: 'c', url: '/media/c.mp4', duration: 10, fit: 'contain_blur' };
  assert.deepEqual(pushTargets({ ...s, side_panel: { ...s.side_panel, media: clip } }), { item: false, panel: false });
  assert.deepEqual(pushTargets({ ...s, media: clip, events: [{ t: 1, kind: 'look_here', target: 'b2' }] }), { item: false, panel: false });
});

test('phraseSchedule (9:16): one phrase at a time, the one before leaving 30% faster just before; MIN_DWELL for unanchored', () => {
  const ex = TEXT_ENTER * EXIT_FACTOR;
  const beats = phraseSchedule([{ id: 'a', enter: 0.5, initial: false }, { id: 'b', enter: 3, initial: false }, { id: 'c', enter: 6, initial: false }], 10);
  assert.deepEqual(beats.map((p) => p.enter), [0.5, 3, 6], 'on beats: each enters on time');
  assert.ok(Math.abs(beats[0].exit - (3 - ex)) < 1e-12 && Math.abs(beats[1].exit - (6 - ex)) < 1e-12, 'the one before leaves just before');
  assert.equal(beats[2].exit, Infinity, 'the last one stays');
  const free = phraseSchedule([{ id: 'a', enter: 0.35, initial: true }, { id: 'b', enter: 0.44, initial: true }], 20);
  assert.ok(Math.abs(free[1].enter - (0.35 + TEXT_ENTER + MIN_DWELL + ex)) < 1e-12, 'unanchored: waits for MIN_DWELL');
  assert.ok(Math.abs(free[0].exit - (free[1].enter - ex)) < 1e-12);
  const short = phraseSchedule([{ id: 'a', enter: 0.35, initial: true }, { id: 'b', enter: 0.44, initial: true }], 2);
  assert.ok(Math.abs(short[1].enter - Math.max(2 - TEXT_ENTER - 1, 0.35 + ex)) < 1e-12, 'but enters a second before a short scene ends (once the one before has left)');
  const soft = phraseSchedule([{ id: 'a', enter: 0, initial: false }, { id: 'b', enter: 3, initial: false }], 10, 1.15);
  assert.ok(Math.abs(soft[0].exit - (3 - TEXT_ENTER * 1.15 * EXIT_FACTOR)) < 1e-12, 'the personality scales the exit');
  const fast = phraseSchedule([{ id: 'a', enter: 1, initial: false }, { id: 'b', enter: 1.1, initial: false }], 10);
  assert.equal(fast[0].exit, 1, 'beats too close: the earlier one leaves at once');
  assert.ok(Math.abs(fast[1].enter - (1 + ex)) < 1e-12, '... and the next waits for that exit');
  for (const list of [beats, free, short, soft, fast]) {
    for (let k = 1; k < list.length; k++) assert.ok(list[k].enter >= list[k - 1].exit + ex - 1e-12, 'never two phrases at once');
  }
});

test('mediaOf / terminalOf / textItemsOf / texOf: what the scene shows', () => {
  const tl = load('media_split.landscape.json');
  const s = tl.scenes[0];
  const m = mediaOf(s, 'render');
  assert.equal(m?.slot, 'panel');
  assert.equal(m?.shown, true);
  const video = { kind: 'video', asset_key: 'clip-1', url: '/m.mp4', width: 1280, height: 720 };
  assert.equal(mediaOf({ ...s, media: video }, 'render')?.slot, 'main', "the scene's own medium first");
  const gif = { ...s, side_panel: { ...s.side_panel, media: { kind: 'gif', url: 'https://example.test/a.gif', render_in_mp4: false } } };
  assert.equal(mediaOf(gif, 'render')?.shown, false, 'a GIF is live only');
  assert.equal(mediaOf(gif, 'live')?.shown, true);
  assert.equal(mediaOf({ ...s, layout: { show_side_panel: false } }, 'render'), null);
  assert.equal(terminalOf(s), null);
  const term = load('media_split.landscape.terminal.json').scenes[0];
  assert.deepEqual(terminalOf(term)?.lines, ['t=0  I=0.00 A', 't=1  I=1.25 A', 't=2  I=2.00 A', 't=3  I=2.00 A']);
  assert.deepEqual(textItemsOf(/** @type {any} */ ({ board: [{ id: 'a', kind: 'formula' }, { id: 'b', kind: 'bullet' }, { id: 'c', kind: 'figure' }] })).map((i) => i.id), ['b']);
  assert.deepEqual(texOf(s), [{ tex: 'I = V/R', display: false }]);
  assert.deepEqual(texOf(/** @type {any} */ ({ board: [{ id: 'd', kind: 'definition', term: 'The $x$ term', text: 'is $y^2$' }, { id: 'f', kind: 'formula', latex: 'z' }] })),
    [{ tex: 'x', display: false }, { tex: 'y^2', display: false }], 'term then text; other kinds are not drawn');
});

test('TEXT_KINDS is templates.py TEXT_KINDS (the text items the fit of media_split counts)', () => {
  const py = readFileSync(new URL('../../../../aadhi/compose/dynamic/templates.py', import.meta.url), 'utf-8');
  const m = /TEXT_KINDS: Final = frozenset\(\{([^}]*)\}\)/.exec(py);
  assert.ok(m);
  const kinds = [...(/** @type {RegExpExecArray} */ (m))[1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]).sort();
  assert.deepEqual([...TEXT_KINDS].sort(), kinds);
});

test('the catalogue slots of media_split are its LAYOUT (registered: never skipped, B2c review CG-4)', () => {
  const spec = /** @type {any} */ (CATALOGUE.templates).media_split;
  const slots = /** @type {any} */ (templateSlots('media_split'));
  assert.equal(spec.implemented, true);
  for (const v of ['media_left', 'media_right']) {
    const t = slots.title.landscape[v];
    assert.deepEqual([t.w, t.max_lines, t.size, t.weight, t.line_height], [742, 3, [LAYOUT.landscape.title.min, LAYOUT.landscape.title.max], 700, LINE_HEIGHT.display.latin]);
    const b = slots.body.landscape[v];
    assert.deepEqual([b.w, b.size, b.weight, b.line_height], [742 - LAYOUT.landscape.marker, [LAYOUT.landscape.body.min, LAYOUT.landscape.body.max], 400, LINE_HEIGHT.body.latin]);
  }
  const t = slots.title.portrait.default;
  assert.deepEqual([t.w, t.max_lines, t.prefer_lines, t.size, t.line_height], [696, 2, 1, [LAYOUT.portrait.title.min, LAYOUT.portrait.title.max], LINE_HEIGHT.display.latin]);
  const b = slots.body.portrait.default;
  assert.deepEqual([b.w, b.max_lines, b.size, b.weight, b.line_height], [780 - 192, 2, [LAYOUT.portrait.body.min, LAYOUT.portrait.body.max], 700, LINE_HEIGHT.body.latin]);
  // the forms per kind (CG-5): a term or an item for cue_term, never a panel; look_here also on a term (its item)
  assert.deepEqual(spec.consumes.forms, { reveal: ['item'], cue_term: ['item', 'item:term'], look_here: ['item', 'item:term', 'panel', 'panel:row'], output: ['panel'] });
});

test('line heights per script (§4.6, plan §2 R-29; B2c review V5): the type scale\'s, Indic text at the Indic floors', () => {
  const scale = /** @type {any} */ (CATALOGUE).type.scale;
  assert.ok(LINE_HEIGHT.display.latin >= scale.display.line_height.latin[0] && LINE_HEIGHT.display.latin <= scale.display.line_height.latin[1]);
  assert.equal(LINE_HEIGHT.display.indic, scale.display.line_height.indic[0]);
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.body.indic, scale.body.line_height.indic[0]);
  assert.equal(lineHeight('body', 'Ohm விதி: $R = V/I$'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('body', 'बल'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('body', 'Power is $P = VI$'), LINE_HEIGHT.body.latin);
  assert.equal(lineHeight('display', 'மின்தடை'), LINE_HEIGHT.display.indic);
});

test('the Indic fixture\'s phrase is set at the Indic body line height (no Tamil mark meets the line under it)', async () => {
  const tl = load('media_split.portrait.indic.json');
  const m = mount(tl);
  await m.stage.seek(30);
  const v = m.view();
  const tamil = v.items.filter((/** @type {any} */ it) => /[\u0B80-\u0BFF]/u.test(it.content.textContent || ''));
  assert.ok(tamil.length > 0, 'the fixture has Tamil phrases');
  for (const it of tamil) assert.equal(Number(it.content.style.lineHeight), LINE_HEIGHT.body.indic);
  if (/[\u0B80-\u0BFF]/u.test(v.titleText)) assert.equal(Number(v.title.style.lineHeight), LINE_HEIGHT.display.indic);
  m.done();
});

test('mediaBox (static): the largest box shown, with push headroom; the same under every fix and in both variants', () => {
  const tl = load('media_split.landscape.json');
  const s = tl.scenes[0];
  const fixes = [{ safe: false, slots: {} }, { safe: true, slots: {} }, { safe: false, slots: { title: { size: 1, scrim: true }, body: { size: 1, scrim: false } } }];
  for (const f of fixes) {
    for (const variant of ['media_left', 'media_right', null]) {
      const sc = { ...s, look: { ...s.look, variant } };
      assert.deepEqual(mediaSplit.mediaBox?.(sc, ZONES.landscape, 'render', 'panel', /** @type {any} */ (f)), pushHeadroom({ w: 938, h: 858 }));
    }
  }
  const p = load('media_split.portrait.json').scenes[0];
  const box = /** @type {any} */ (mediaSplit.mediaBox?.(p, ZONES.portrait, 'render', 'panel', { safe: false, slots: {} }));
  assert.equal(box.w, Math.ceil(960 * 1.06));
  const L = LAYOUT.portrait;
  const bodyLh = Math.min(...p.board.map((/** @type {any} */ i) => lineHeight('body', `${i.term || ''} ${i.text || ''}`)));
  const bare = portraitBoxes(/** @type {any} */ ({ ...p, events: [] }), Math.floor(L.title.min * lineHeight('display', p.title)) - 1, Math.floor(L.body.min * bodyLh) - 1).media;
  assert.ok(box.h >= Math.ceil((bare.y1 - bare.y0) * 1.06) - (pushTargets(p).item ? 30 : 0), 'under a one-line headline, above a one-line phrase');
  const tall = /** @type {any} */ (mediaSplit.mediaBox?.({ ...p, title: '', board: [], events: [] }, ZONES.portrait, 'render', 'main', { safe: false, slots: {} }));
  assert.equal(tall.h, Math.ceil((1040 - 340) * 1.06), 'no headline, no phrase: the tall region');
});

test('portraitBoxes: the phrase ends above the caption band in the column below the rail; a push narrows text to its push area', () => {
  const p = load('media_split.portrait.json').scenes[0];
  const calm = portraitBoxes(/** @type {any} */ ({ ...p, events: [] }), 60, 150);
  // one left edge with the headline (V7), the rail's x 780, 8 px above the caption band (the catalogue's, CG-3)
  assert.deepEqual([calm.phrase.x0, calm.phrase.x1, calm.phrase.y1], [192, 780, LAYOUT.portrait.phraseBottom]);
  assert.equal(LAYOUT.portrait.phraseBottom, ZONES.portrait.caption.y0 - 8);
  assert.equal(calm.phrase.y0, LAYOUT.portrait.phraseBottom - 150);
  assert.deepEqual([calm.title.x0, calm.title.x1], [192, 888]);
  assert.deepEqual(calm.media, { x0: 60, y0: 460, x1: 1020, y1: LAYOUT.portrait.phraseBottom - 150 - LAYOUT.portrait.stageGap });
  const pushed = portraitBoxes(p, 60, 150); // look_here on the panel and on p3
  const s = 1 + PUSH_SCALE;
  const origins = [[540, 340], [540, 1040], [(pushed.phrase.x0 + pushed.phrase.x1) / 2, 840], [(pushed.phrase.x0 + pushed.phrase.x1) / 2, LAYOUT.portrait.phraseBottom]];
  for (const [ox, oy] of origins) {
    const at = (/** @type {number} */ x, /** @type {number} */ y) => [ox + s * (x - ox), oy + s * (y - oy)];
    const [a, b] = [at(pushed.phrase.x0, pushed.phrase.y0), at(pushed.phrase.x1, pushed.phrase.y1)];
    assert.ok(a[0] >= 120 - 1e-9 && b[0] <= 780 + 1e-9 && a[1] >= 840 - 1e-9 && b[1] <= 1040 + 1e-9, `phrase pushed about ${ox},${oy}`);
    const [c, d] = [at(pushed.title.x0, pushed.title.y0), at(pushed.title.x1, pushed.title.y0 + 60)];
    assert.ok(c[0] >= 192 - 1e-9 && d[0] <= 888 + 1e-9 && c[1] >= 288 - 1e-9, `headline pushed about ${ox},${oy}`);
  }
  assert.ok(pushed.phrase.x0 > 192 && pushed.phrase.x1 < 780 && pushed.title.x0 > 192, 'the push costs room only when it pushes');
  assert.equal(pushed.phrase.x0, pushed.title.x0, 'pushed too: one left edge');
  const none = portraitBoxes(/** @type {any} */ ({ ...p, title: '', board: [] }), 0, 0);
  assert.deepEqual(none.media, { x0: 60, y0: 340, x1: 1020, y1: 1040 }, 'without headline and phrase: the tall region');
});

test('portraitBoxes: an oversized phrase never collapses the medium (MEDIA_MIN_SHARE, B2 review F3)', () => {
  const p = load('media_split.portrait.json').scenes[0];
  const stage = REGIONS.portrait.stage;
  const floor = Math.round((stage.y1 - stage.y0) * MEDIA_MIN_SHARE);
  const calm = /** @type {any} */ ({ ...p, events: [] });
  // the five-line body of the showcase's s13: before the floor the medium kept about 110 px and `containRect` drew a
  // chart stand-in too small to read
  for (const phraseH of [300, 400, 500, 700]) {
    const m = portraitBoxes(calm, 60, phraseH).media;
    assert.ok(m.y1 - m.y0 >= floor, `phrase ${phraseH}: medium ${m.y1 - m.y0} px >= ${floor}`);
    assert.ok(m.y1 <= stage.y1, 'never below the stage');
  }
  // a phrase that fits its two lines leaves the medium untouched (the floor binds for no real scene: the fixtures,
  // and templates.py MEDIA_SPLIT_PHRASE_WORDS, keep the phrase inside its region)
  const L = LAYOUT.portrait;
  const two = Math.ceil(2 * L.body.max * LINE_HEIGHT.body.indic);
  const fits = portraitBoxes(calm, 60, two).media;
  assert.equal(fits.y1, L.phraseBottom - two - L.stageGap, 'sized from the phrase, not the floor');
  assert.ok(fits.y1 - fits.y0 > floor && (fits.y1 - fits.y0) / (stage.y1 - stage.y0) > 0.6);
});

// ---------------------------------------------------------------------------------------------
// On the stage
// ---------------------------------------------------------------------------------------------

test('16:9 layout: the medium in its 55% region at its aspect, the column in the 45% (variant), top level with the medium', async () => {
  const undo = layoutModel();
  try {
    for (const variant of ['media_right', 'media_left']) {
      const tl = edited('media_split.landscape.json', (s) => {
        s.look.variant = variant;
        s.events = s.events.filter((/** @type {any} */ e) => e.kind !== 'look_here'); // no push: the full regions
      });
      const m = mount(tl);
      await m.stage.seek(150);
      const v = m.view();
      const regions = splitRegions(variant);
      const frame = boxOf(v.frame);
      assert.deepEqual(frame, { x0: regions.media.x0, y0: 219, x1: regions.media.x1, y1: 747 }, `${variant}: 938 x 528 (16:9), centred`);
      assert.deepEqual(v.textBox, regions.text, 'no push: the whole text region');
      assert.equal(px(v.column.style.left), regions.text.x0);
      assert.equal(px(v.column.style.width), 742);
      assert.equal(px(v.column.style.top), frame.y0, 'level with the medium');
      assert.equal(v.bodySize, LAYOUT.landscape.body.max, 'room to spare: the largest body size');
      assert.ok(v.titleSize <= LAYOUT.landscape.title.max && v.titleSize >= LAYOUT.landscape.title.min);
      m.done();
    }
  } finally {
    undo();
  }
});

test('16:9 fit: the title gives up size before the body overflows; at the minimums the overflow is reported', async () => {
  const undo = layoutModel();
  try {
    const long = 'Wide words make maximum width demands on every line';
    // the model: a title of two lines at its minimum and three above it; items of 4, 4 and 2 lines at the body
    // minimum, which fit below the title only when it is at its own minimum
    const tl = edited('media_split.landscape.json', (s) => {
      s.events = [];
      s.title = 'Wattage warms wide metal wires so quickly';
      s.board[1] = { id: 'b2', kind: 'bullet', text: 'word '.repeat(20).trim() };
      s.board[0].text = 'word '.repeat(20).trim();
      s.board[2].text = 'word '.repeat(10).trim();
    });
    const m = mount(tl);
    await m.stage.seek(290);
    const v = m.view();
    const L = LAYOUT.landscape;
    assert.equal(v.titleSize, L.title.min, 'the title gave up its size (alone it would take its maximum)');
    assert.ok(v.items.every((/** @type {any} */ it) => it.overflow === 0), 'so the body fits');
    m.done();
    const crowded = edited('media_split.landscape.json', (s) => {
      s.events = [];
      s.title = `${long} ${long}`;
      for (const it of s.board) it.text = `${long} ${long} ${long}`;
    });
    const c = mount(crowded);
    await c.stage.seek(290);
    const cv = c.view();
    assert.equal(cv.titleSize, L.title.min);
    assert.equal(cv.bodySize, L.body.min);
    const texts = /** @type {any[]} */ (mediaSplit.texts?.(c.ctx()) || []);
    const body = texts.filter((e) => e.slot === 'body');
    assert.ok(body.some((e) => e.overflow > 0), 'what does not fit is reported (preflight: shrink, then block)');
    assert.ok(texts.find((e) => e.slot === 'title').overflow > 0, 'a title past its 3 lines too');
    c.done();
  } finally {
    undo();
  }
});

test('16:9 push room: with a look_here push the column keeps clear, so pushed text stays in the critical box', async () => {
  const undo = layoutModel();
  try {
    for (const [target, name] of [['panel', 'media_split.landscape.json'], ['b2', 'media_split.landscape.json'], ['panel:row:1', 'media_split.landscape.terminal.json']]) {
      const tl = edited(name, (s) => {
        s.events = [{ t: 6, kind: 'look_here', target, dur: 2, anchored: true }];
      });
      const m = mount(tl);
      await m.stage.seek(frameAt(tl, 7.5));
      const v = m.view();
      const regions = splitRegions(v.variant);
      assert.ok(v.textBox.x0 > regions.text.x0, `${target}: the column keeps clear of the zone edge`);
      const zone = ZONES.landscape.critical[0];
      const frame = v.mediaBox;
      const top = px(v.column.style.top) || 0;
      const blockH = v.items.reduce((/** @type {number} */ a, /** @type {any} */ it, /** @type {number} */ k) => a + fakeHeight(it.group) + (k ? LAYOUT.landscape.itemGap : 0), 0)
        + (v.title ? fakeHeight(v.title) + LAYOUT.landscape.titleGap : 0);
      const box = { x0: v.textBox.x0, x1: v.textBox.x1, y0: top, y1: top + blockH };
      const s = 1 + PUSH_SCALE;
      /** @type {number[][]} */
      const origins = [];
      if (target.startsWith('panel')) {
        const cx = (frame.x0 + frame.x1) / 2;
        origins.push(...(target === 'panel' ? [[cx, (frame.y0 + frame.y1) / 2]] : [[cx, frame.y0], [cx, frame.y1]]));
      } else {
        const r = /** @type {any} */ (mediaSplit.focusRect?.(m.ctx(), target));
        origins.push([r.rect.x + r.rect.w / 2, r.rect.y + r.rect.h / 2]);
      }
      for (const [ox, oy] of origins) {
        const x0 = ox + s * (box.x0 - ox);
        const x1 = ox + s * (box.x1 - ox);
        const y0 = oy + s * (box.y0 - oy);
        const y1 = oy + s * (box.y1 - oy);
        assert.ok(x0 >= zone.x0 - 1 && x1 <= zone.x1 + 1 && y0 >= zone.y0 - 1 && y1 <= zone.y1 + 1, `${target}: pushed ${JSON.stringify({ x0, x1, y0, y1 })}`);
      }
      if (target === 'b2') {
        // the room an item push takes comes from the items' centres as laid out, not from anywhere in the column
        const anywhere = landscapeColumn(regions.text, zone, frame, { item: true, panel: false }, 'centre', null);
        assert.ok(v.textBox.y1 - v.textBox.y0 > anywhere.y1 - anywhere.y0, 'the exact pass gives the column back room');
      }
      // the camera does push (cues.js) at that moment
      const camera = /** @type {HTMLElement} */ (m.stage.el.querySelector('.dyn-camera'));
      assert.match(camera.style.transform, /^scale\(1\.0[1-3]\d*\)$/);
      m.done();
    }
  } finally {
    undo();
  }
});

test('9:16 layout: headline, the medium between it and the phrase, one phrase box above the caption band', async () => {
  const undo = layoutModel();
  try {
    const tl = edited('media_split.portrait.json', (s) => {
      s.events = s.events.filter((/** @type {any} */ e) => e.kind !== 'look_here');
    });
    const m = mount(tl);
    await m.stage.seek(100);
    const v = m.view();
    const L = LAYOUT.portrait;
    assert.deepEqual([px(v.titleGroup.style.left), px(v.titleGroup.style.top), px(v.titleGroup.style.width)], [192, 340, 696]);
    const phraseH = Math.max(...v.items.map((/** @type {any} */ it) => fakeHeight(it.group)));
    assert.equal(v.textBox.y1, L.phraseBottom);
    assert.equal(Math.round(v.textBox.y0), Math.floor(L.phraseBottom - phraseH));
    for (const it of v.items) assert.deepEqual([px(it.group.style.left), px(it.group.style.width), px(it.group.style.top)], [192, 588, v.textBox.y0]);
    const frame = boxOf(v.frame);
    assert.ok(frame.y0 >= 460 && (frame.y1 || 0) <= v.textBox.y0 - L.stageGap + 1, 'between the headline and the phrase');
    assert.ok(Math.abs(((frame.x0 || 0) + frame.x1) / 2 - 540) <= 1, 'centred in the stage (whole px)');
    assert.ok(v.items.every((/** @type {any} */ it) => px(it.content.style.fontSize) === v.bodySize), 'every phrase at one size');
    m.done();
    // phrases of different lengths: all at the size the longest fits at, none past its two lines
    const mixed = edited('media_split.portrait.json', (s) => {
      s.events = [];
      s.board[0].text = 'Short';
      s.board[1].text = `${'x'.repeat(21)} ${'y'.repeat(22)}`;
    });
    const mm = mount(mixed);
    await mm.stage.seek(290);
    assert.equal(mm.view().bodySize, L.body.min);
    assert.deepEqual(/** @type {any[]} */ (mediaSplit.texts?.(mm.ctx())).filter((e) => e.slot === 'body').map((e) => e.overflow), [0, 0, 0]);
    mm.done();
    // a phrase past its two lines even at the minimum: reported (preflight: shrink, then block)
    const over = edited('media_split.portrait.json', (s) => {
      s.events = [];
      s.board[2].text = 'word '.repeat(30).trim();
    });
    const om = mount(over);
    await om.stage.seek(290);
    const ov = /** @type {any[]} */ (mediaSplit.texts?.(om.ctx())).filter((e) => e.slot === 'body').map((e) => e.overflow);
    assert.deepEqual([ov[0], ov[1]], [0, 0]);
    assert.ok(ov[2] > 0, `overflow ${ov[2]}`);
    om.done();
  } finally {
    undo();
  }
});

test('the laid-out medium never exceeds mediaBox (less its push headroom) under any fix, in either format', async () => {
  const undo = layoutModel();
  try {
    const fixes = [null, { safe: true, slots: {} }, { safe: false, slots: { title: { size: 1, scrim: true }, body: { size: 1, scrim: true } } }];
    // the bound is tight: a one-line headline and one-line phrases at the body minimum (a size cap) lay the medium out
    // in exactly the box mediaBox plans (up to the page's rounding of a measured height)
    const tight = edited('media_split.portrait.json', (s) => {
      s.events = [];
      s.title = 'Glow';
      for (const it of s.board) it.text = 'Hot';
      s.look.fixes = { safe: false, slots: { body: { size: 48 } } };
    });
    const t = mount(tight);
    await t.stage.seek(290);
    const tf = boxOf(t.view().frame);
    const tb = /** @type {any} */ (mediaSplit.mediaBox?.(tight.scenes[0], ZONES.portrait, 'render', 'panel', /** @type {any} */ (tight.scenes[0].look.fixes)));
    assert.ok((tf.y1 - (tf.y0 || 0)) * 1.06 <= tb.h && (tf.y1 - (tf.y0 || 0) + 3) * 1.06 > tb.h, `tight: ${JSON.stringify(tf)} in ${JSON.stringify(tb)}`);
    t.done();
    for (const name of FIXTURES) {
      for (const f of fixes) {
        for (const edit of [(/** @type {any} */ s) => s, (/** @type {any} */ s) => { s.title = ''; }]) {
          const tl = edited(name, (s) => {
            if (f) s.look.fixes = f;
            edit(s);
          });
          const m = mount(tl);
          await m.stage.seek(150);
          const v = m.view();
          const box = /** @type {any} */ (mediaSplit.mediaBox?.(tl.scenes[0], ZONES[tl.presentation.format], 'render', 'panel', /** @type {any} */ (f || { safe: false, slots: {} })));
          const frame = boxOf(v.frame);
          assert.ok((frame.x1 - (frame.x0 || 0)) * 1.06 <= box.w + 1e-6 && (frame.y1 - (frame.y0 || 0)) * 1.06 <= box.h + 1e-6, `${name} ${JSON.stringify(f)}: ${JSON.stringify(frame)} in ${JSON.stringify(box)}`);
          m.done();
        }
      }
    }
  } finally {
    undo();
  }
});

test('the medium enters: a fade and a 0.96 -> 1 scale (§4.3; downscale only, §P); no scale under reduced motion (live)', async () => {
  const tl = load('media_split.landscape.json');
  const m = mount(tl);
  await m.stage.seek(0);
  const v = m.view();
  assert.equal(v.frame.style.opacity, '0');
  assert.equal(v.frame.style.visibility, 'hidden');
  assert.equal(v.frame.style.transform, `scale(${MEDIA_FROM.toFixed(4)})`);
  await m.stage.seek(frameAt(tl, 0.15 + 0.25 * 1.15)); // halfway (Soft: x1.15)
  const mid = Number(/^scale\(([\d.]+)\)$/.exec(v.frame.style.transform)?.[1]);
  assert.ok(mid > MEDIA_FROM && mid < 1, `mid-entrance ${v.frame.style.transform}`);
  assert.ok(Number(v.frame.style.opacity) > 0 && Number(v.frame.style.opacity) < 1);
  await m.stage.seek(frameAt(tl, 1));
  assert.deepEqual([Number(v.frame.style.opacity), v.frame.style.transform, v.frame.style.visibility], [1, 'none', 'visible']);
  m.done();
  const live = mount(load('media_split.landscape.json'), { mode: 'live', reducedMotion: true });
  live.stage.update(0.3);
  const lv = live.view();
  assert.equal(lv.frame.style.transform, 'none', 'reduced motion: a fade in place');
  assert.ok(Number(lv.frame.style.opacity) > 0 && Number(lv.frame.style.opacity) < 1);
  live.done();
  // a clip only fades in, in both modes (measured order-dependent when scaled, B2c: test_template_clips.py)
  const clip = { kind: 'video', asset_key: 'c', url: '/media/c.mp4', duration: 10, src_fps: 30, fit: 'contain_blur', end_behavior: 'freeze' };
  const still = mediaOf(tl.scenes[0], 'render');
  assert.equal(clipShown(still), false);
  for (const mode of /** @type {const} */ (['render', 'live'])) {
    const ctl = load('media_split.landscape.json');
    ctl.scenes[0].side_panel.media = { ...clip };
    assert.equal(clipShown(mediaOf(ctl.scenes[0], mode)), true);
    const c = mount(ctl, { mode });
    if (mode === 'render') await c.stage.seek(frameAt(ctl, 0.15 + 0.25 * 1.15));
    else c.stage.update(0.15 + 0.25 * 1.15);
    const cv = c.view();
    assert.equal(cv.frame.style.transform, 'none', `${mode}: a clip is never scaled`);
    assert.ok(Number(cv.frame.style.opacity) > 0 && Number(cv.frame.style.opacity) < 1, `${mode}: it fades in`);
    c.done();
  }
});

test('16:9: text items enter one per beat; 9:16: one key phrase at a time', async () => {
  const tl = load('media_split.landscape.json');
  const m = mount(tl);
  await m.stage.seek(0);
  const v = m.view();
  const alphas = async (/** @type {number} */ t) => {
    await m.stage.seek(frameAt(tl, t));
    return v.items.map((/** @type {any} */ it) => it.alpha);
  };
  assert.deepEqual(await alphas(0.3), [0, 0, 0], 'before the first beat');
  assert.deepEqual(await alphas(2.5), [1, 0, 0]);
  assert.deepEqual(await alphas(4.5), [1, 1, 0]);
  assert.deepEqual(await alphas(9.5), [1, 1, 1], 'all three stay (16:9)');
  m.done();
  const ptl = load('media_split.portrait.json');
  const p = mount(ptl);
  await p.stage.seek(0);
  const pv = p.view();
  for (const [t, want] of /** @type {[number, number[]][]} */ ([[0.2, [0, 0, 0]], [2, [1, 0, 0]], [5, [0, 1, 0]], [9, [0, 0, 1]]])) {
    await p.stage.seek(frameAt(ptl, t));
    assert.deepEqual(pv.items.map((/** @type {any} */ it) => it.alpha), want, `t ${t}`);
    assert.deepEqual(pv.items.map((/** @type {any} */ it) => it.exitEl.style.visibility), want.map((a) => (a ? 'visible' : 'hidden')));
  }
  // during a change: the old phrase leaves before the new one enters (never both)
  const sched = phraseSchedule(itemTimingsOf(ptl.scenes[0]), 10, ptl.presentation.motion_scale);
  for (let t = 0; t < 10; t += 1 / 30) {
    await p.stage.seek(Math.min(299, Math.round(t * 30)));
    assert.ok(pv.items.filter((/** @type {any} */ it) => it.alpha > 0).length <= 1, `t ${t.toFixed(2)}: one phrase at most`);
  }
  assert.equal(sched.length, 3);
  p.done();
});

/** @param {any} scene */
function itemTimingsOf(scene) {
  return scene.board.map((/** @type {any} */ i, /** @type {number} */ k) => ({ id: i.id, enter: scene.beats[k].start - 0.12, initial: false }));
}

test('the personality text entrance: Crisp reveals from the baseline inside a mask, Soft fades with a rise (no mask)', async () => {
  const crisp = mount(load('media_split.portrait.json')); // Deep Teal / Crisp
  await crisp.stage.seek(0);
  assert.equal(crisp.stage.el.querySelectorAll('.dyn-ms .dyn-reveal-mask').length, 4, 'the headline and three phrases');
  await crisp.stage.seek(frameAt(load('media_split.portrait.json'), 0.6));
  const inner = crisp.view().items[0].inner;
  assert.ok(['0', '1'].includes(inner.style.opacity), 'baseline reveal: shown whole, slid inside its mask');
  crisp.done();
  const soft = mount(load('media_split.landscape.json')); // Midnight Indigo / Soft
  await soft.stage.seek(0);
  assert.equal(soft.stage.el.querySelectorAll('.dyn-ms .dyn-reveal-mask').length, 0);
  soft.done();
});

test('a terminal panel: a code card whose output lines appear on their times; panel:row:<n> is its line', async () => {
  const undo = layoutModel();
  try {
    const tl = load('media_split.landscape.terminal.json');
    const m = mount(tl);
    await m.stage.seek(0);
    const v = m.view();
    assert.equal(v.term.lines.length, 4);
    assert.equal(v.frame.querySelector('.dyn-ms-term-cmd').textContent, '$ python circuit.py --steps 4');
    const shown = async (/** @type {number} */ t) => {
      await m.stage.seek(frameAt(tl, t));
      return v.term.lines.map((/** @type {HTMLElement} */ l) => l.style.visibility);
    };
    const early = await shown(0.6);
    const late = await shown(9.9);
    assert.ok(early.filter((x) => x === 'visible').length < late.filter((x) => x === 'visible').length);
    assert.deepEqual(late, ['visible', 'visible', 'visible', 'visible']);
    const row = /** @type {any} */ (mediaSplit.focusRect?.(m.ctx(), 'panel:row:1'));
    assert.equal(row.shape, 'bar');
    assert.equal(row.el, v.term.lines[1]);
    assert.equal(mediaSplit.focusRect?.(m.ctx(), 'panel:row:9'), null, 'a row the terminal does not have');
    const entry = /** @type {any[]} */ (mediaSplit.texts?.(m.ctx())).find((e) => e.slot === 'terminal');
    assert.equal(entry.critical, true);
    assert.equal(entry.bg, m.ctx().roles.color('surface'));
    assert.ok(entry.size >= LAYOUT.landscape.terminal.min && entry.size <= LAYOUT.landscape.terminal.max);
    m.done();
  } finally {
    undo();
  }
});

test('no medium the render can show: the panel title on the card surface, reported as a label (never an empty box)', async () => {
  for (const edit of [
    (/** @type {any} */ s) => { s.side_panel = { panel: { kind: 'chart', title: 'Current over time', rationale: '' }, media: null, show_at: 0 }; },
    (/** @type {any} */ s) => { s.side_panel.media = { kind: 'gif', url: 'https://example.test/a.gif', render_in_mp4: false, width: 400, height: 300 }; },
    (/** @type {any} */ s) => { s.side_panel.media.url = 'javascript:alert(1)'; },
  ]) {
    const tl = edited('media_split.landscape.json', edit);
    const m = mount(tl);
    await m.stage.seek(60);
    const v = m.view();
    assert.ok(v.missing, 'a label');
    assert.equal(v.missing.textContent, tl.scenes[0].side_panel.panel.title);
    assert.equal(v.frame.style.background, rgb(m.ctx().roles.color('surface')), 'on the card surface');
    const entry = /** @type {any[]} */ (mediaSplit.texts?.(m.ctx())).find((e) => e.el === v.missing);
    assert.deepEqual([entry.role, entry.critical], ['label', false]);
    m.done();
  }
  // no title either: a plain label
  const tl = edited('media_split.landscape.json', (s) => { s.side_panel = null; });
  const m = mount(tl);
  await m.stage.seek(60);
  assert.equal(m.view().missing.textContent, LABELS.mediaMissing);
  m.done();
});

test('a clip: its frames are the ones mediaPlan lists, at the size of mediaBox (one function, plan §1.4)', async () => {
  for (const name of ['media_split.landscape.json', 'media_split.portrait.json']) {
    const tl = edited(name, (s) => {
      s.side_panel = { panel: { kind: 'figure', title: 'Clip', rationale: '', figure_id: 'fig' },
        media: { kind: 'video', asset_key: 'clip-1', url: '/m.mp4', duration: 4, width: 1920, height: 1080, fit: 'contain', end_behavior: 'freeze' }, show_at: 0 };
    });
    const m = mount(tl);
    await m.stage.seek(60);
    const v = m.view();
    const box = /** @type {any} */ (mediaSplit.mediaBox?.(tl.scenes[0], ZONES[tl.presentation.format], 'render', 'panel', { safe: false, slots: {} }));
    const key = mediaKey('video', 'clip-1', mediaSize(tl.scenes[0].side_panel.media, box));
    const img = /** @type {HTMLImageElement} */ (v.frame.querySelector('img'));
    assert.match(String(img.getAttribute('src')), new RegExp(`/__frames/${key}/\\d{6}\\.jpg$`));
    const plan = m.stage.mediaPlan(60, 61);
    assert.deepEqual(plan.map((/** @type {any} */ e) => e.key), [key]);
    m.done();
    assert.deepEqual(await orderProblems(tl, [0, 10, 30, 60, 150, 290], { templates: TEMPLATES }), [], `${name}: a clip is order independent too`);
  }
});

test('cue hooks: itemEl gives the term spans in field order; focusRect the frame or a shown item; dimGroups fixed', async () => {
  const undo = layoutModel();
  try {
    const tl = load('media_split.landscape.json');
    const m = mount(tl);
    await m.stage.seek(frameAt(tl, 9));
    const ctx = m.ctx();
    const v = m.view();
    const def = /** @type {Element[]} */ (mediaSplit.itemEl?.(ctx, 'b2'));
    assert.ok(Array.isArray(def) && def.length === 2, 'a definition: its term, then its text');
    assert.equal(def[0].textContent, 'Voltage');
    const bullet = /** @type {Element} */ (mediaSplit.itemEl?.(ctx, 'b1'));
    assert.equal(bullet.querySelector('.rt-k')?.textContent, 'Current', 'the [[term]] span is kept');
    assert.equal(mediaSplit.itemEl?.(ctx, 'nope'), null);
    assert.equal(mediaSplit.itemEl?.(ctx, 'option:0'), null);
    const panel = /** @type {any} */ (mediaSplit.focusRect?.(ctx, 'panel'));
    assert.deepEqual(panel.rect, { x: 886, y: 219, w: 938, h: 528 });
    assert.equal(panel.el, v.frame);
    const item = /** @type {any} */ (mediaSplit.focusRect?.(ctx, 'b3'));
    assert.equal(item.el, v.items[2].inner);
    assert.ok(['x', 'y', 'w', 'h'].every((k) => Number.isInteger(item.rect[k])));
    // a term of an item points at its item (the catalogue lets look_here name one: B2c review CG-5)
    assert.deepEqual(mediaSplit.focusRect?.(ctx, 'b1:term:0'), mediaSplit.focusRect?.(ctx, 'b1'));
    // the medium is a shape (its outline goes round it, never across, cues.js shapeArea: CG-1); an item is text
    assert.deepEqual([panel.kind, item.kind], ['shape', 'text']);
    assert.equal(mediaSplit.focusRect?.(ctx, 'b1:line:0'), null, 'a form it does not draw');
    assert.equal(mediaSplit.focusRect?.(ctx, 'B 1'), null, 'not a target');
    await m.stage.seek(frameAt(tl, 0.1));
    assert.equal(mediaSplit.focusRect?.(ctx, 'b3'), null, 'an item not yet shown');
    assert.equal(mediaSplit.focusRect?.(ctx, 'panel'), null, 'a medium not yet shown');
    const groups = /** @type {Element[]} */ (mediaSplit.dimGroups?.(ctx));
    assert.deepEqual(groups, [v.titleGroup, ...v.items.map((/** @type {any} */ it) => it.group), v.mediaGroup]);
    m.done();
  } finally {
    undo();
  }
});

test('16:9: every item but a heading starts with a dot in the marker column (B2c review V10: no item reads as the one above\'s paragraph)', async () => {
  const tl = edited('media_split.landscape.json', (s) => {
    s.board = [
      { id: 'b1', kind: 'bullet', text: 'A bullet' }, { id: 'b2', kind: 'definition', term: 'Term', text: 'its meaning' },
      { id: 'b3', kind: 'callout_warning', text: 'Careful' },
    ];
  });
  const m = mount(tl);
  await m.stage.seek(30);
  const dots = m.view().items.map((/** @type {any} */ it) => !!it.inner.querySelector('.dyn-ms-marker .dyn-ms-dot'));
  assert.deepEqual(dots, [true, true, true]);
  m.done();
  const head = edited('media_split.landscape.json', (s) => {
    s.board = [{ id: 'h', kind: 'heading', text: 'A sub-head' }, { id: 'b1', kind: 'paragraph', text: 'A paragraph' }];
  });
  const hm = mount(head);
  await hm.stage.seek(30);
  assert.deepEqual(hm.view().items.map((/** @type {any} */ it) => !!it.inner.querySelector('.dyn-ms-dot')), [false, true], 'a heading is the column\'s sub-head');
  assert.ok(hm.view().items.every((/** @type {any} */ it) => it.inner.querySelector('.dyn-ms-marker')), 'every text starts at one x');
  hm.done();
});

test('§4.8 grammar on every fixture: nothing enters while the camera pushes in or out (B2c review V6)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const scene = tl.scenes[0];
    const m = mount(tl);
    await m.stage.seek(0);
    const v = m.view();
    const scale = m.ctx().motion.scale;
    const moves = cueNeeds(scene, 'media_split').camera
      ? cueEvents(scene, 'media_split').filter((e) => e.kind === 'look_here').flatMap((e) => [[e.start, e.start + PUSH_IN], [e.hold, e.hold + PUSH_IN * EXIT_FACTOR]])
      : [];
    const entrances = [
      ...v.items.map((/** @type {any} */ it) => [it.enter, it.enter + TEXT_ENTER * scale, it.id]),
      [v.mediaStart, v.mediaStart + 0.5 * scale, 'medium'],
    ];
    for (const [a, b] of moves) {
      for (const [x, y, what] of entrances) assert.ok(y <= a + 1e-6 || x >= b - 1e-6, `${name}: ${what} enters ${x}-${y} during the move ${a}-${b}`);
      // a terminal's output lines neither (the schedule's lines shown, sampled through the move)
      const lines = new Set();
      for (let t = a; t <= b; t += 1 / 60) lines.add(sceneStateAt(scene, t).terminalLines.length);
      assert.equal(lines.size, 1, `${name}: an output line appears during the move ${a}-${b}`);
    }
    m.done();
  }
});

test('look_here in the live player under reduced motion: the outline form, no push (§4.8)', async () => {
  const undo = layoutModel();
  try {
    const tl = load('media_split.landscape.json');
    const live = mount(tl, { mode: 'live', reducedMotion: true });
    live.stage.update(7.5);
    const path = /** @type {SVGElement} */ (live.stage.el.querySelector('.dyn-cues path.dyn-cue-outline'));
    assert.ok(path, 'the outline layer');
    assert.equal(path.style.visibility, 'visible');
    const camera = /** @type {HTMLElement} */ (live.stage.el.querySelector('.dyn-camera'));
    assert.equal(camera.style.transform, '');
    // the other groups dim; the medium (the target) does not
    const v = live.view();
    assert.equal(v.mediaGroup.style.opacity, '');
    assert.equal(Number(v.titleGroup.style.opacity), 0.7);
    live.done();
  } finally {
    undo();
  }
});

test('texts(): slots, roles, sizes, weights; a scrim fix puts the text on the scrim', async () => {
  const tl = load('media_split.landscape.json');
  const m = mount(tl);
  await m.stage.seek(290);
  const ctx = m.ctx();
  const entries = /** @type {any[]} */ (mediaSplit.texts?.(ctx));
  assert.deepEqual(entries.map((e) => [e.slot, e.role, e.critical]), [['title', 'title', true], ['body', 'body', true], ['body', 'body', true], ['body', 'body', true]]);
  assert.deepEqual(entries.map((e) => e.weight), [700, 400, 400, 400]);
  assert.ok(entries.every((e) => e.alpha === 1 && e.ink === ctx.onBackground.ink && e.bg === ctx.onBackground.bg));
  assert.deepEqual(entries.slice(1).map((e) => e.min), [LAYOUT.landscape.body.min, LAYOUT.landscape.body.min, LAYOUT.landscape.body.min]);
  m.done();
  // a heading and a takeaway are bold; 9:16 phrases are 700 (R-29: >= 600 at <= 64 px)
  const bold = edited('media_split.landscape.json', (s) => { s.board[0].kind = 'heading'; s.board[2].kind = 'takeaway'; });
  const b = mount(bold);
  await b.stage.seek(290);
  assert.deepEqual(/** @type {any[]} */ (mediaSplit.texts?.(b.ctx())).map((e) => e.weight), [700, 700, 400, 700]);
  b.done();
  const p = mount(load('media_split.portrait.json'));
  await p.stage.seek(290);
  assert.ok(/** @type {any[]} */ (mediaSplit.texts?.(p.ctx())).filter((e) => e.slot === 'body').every((e) => e.weight === 700 && e.size >= 48 && e.size <= 64));
  p.done();
  for (const slot of ['title', 'body']) {
    const scrimmed = edited('media_split.landscape.json', (s) => { s.look.fixes = { safe: false, slots: { [slot]: { scrim: true } } }; });
    const sm = mount(scrimmed);
    await sm.stage.seek(290);
    const sctx = sm.ctx();
    const scrim = /** @type {HTMLElement} */ (sm.stage.el.querySelector('.dyn-ms-scrim'));
    assert.ok(scrim, `16:9: one scrim behind the column (${slot})`);
    const all = /** @type {any[]} */ (mediaSplit.texts?.(sctx));
    assert.ok(all.every((e) => e.bg !== sctx.onBackground.bg), 'every text in the column is on it');
    sm.done();
  }
  const ps = edited('media_split.portrait.json', (s) => { s.look.fixes = { safe: false, slots: { body: { scrim: true } } }; });
  const pm = mount(ps);
  await pm.stage.seek(290);
  const pe = /** @type {any[]} */ (mediaSplit.texts?.(pm.ctx()));
  assert.equal(pm.stage.el.querySelectorAll('.dyn-ms-scrim').length, 1, '9:16: one per slot');
  assert.equal(pe.find((e) => e.slot === 'title').bg, pm.ctx().onBackground.bg, 'the headline is not on the phrase scrim');
  assert.notEqual(pe.find((e) => e.slot === 'body').bg, pm.ctx().onBackground.bg);
  pm.done();
});

test('<item>:term:<n> (plan §1.7 as B2c extends it) counts the terms over the term field, then the text field', async () => {
  const tl = edited('media_split.landscape.json', (s) => {
    s.board[1] = { id: 'b2', kind: 'definition', term: '[[Voltage]]', text: 'the [[push]] that drives [[charge]]' };
  });
  const m = mount(tl);
  await m.stage.seek(290);
  const ctx = m.ctx();
  /** @param {string} target */
  const cue = (target) => ({ kind: 'cue_term', target, parsed: /** @type {any} */ (parseTarget(target)), start: 0, hold: 1, order: 0 });
  const words = (/** @type {string} */ target) => termSpans(mediaSplit, ctx, /** @type {any} */ (cue(target))).map((e) => e.textContent);
  assert.deepEqual(words('b2:term:0'), ['Voltage']);
  assert.deepEqual(words('b2:term:1'), ['push']);
  assert.deepEqual(words('b2:term:2'), ['charge']);
  assert.deepEqual(words('b2:term:3'), []);
  assert.deepEqual(words('b2'), ['Voltage', 'push', 'charge'], '<item>: every term');
  m.done();
});

test('callouts carry their label before the text; definitions join term and text', async () => {
  const tl = load('media_split.landscape.light.json');
  const m = mount(tl);
  await m.stage.seek(290);
  const v = m.view();
  assert.ok(v.items[1].content.textContent.startsWith(`${LABELS.callout_warning} Pneumono`), v.items[1].content.textContent);
  assert.equal(v.items[1].fields.length, 1, 'the label is not a rich field (no [[term]] counted in it)');
  m.done();
  const d = mount(load('media_split.landscape.json'));
  await d.stage.seek(290);
  assert.equal(d.view().items[1].content.textContent, `Voltage${LABELS.definitionJoin}the push that drives it`);
  d.done();
});

test('Indic and mixed text: Tamil and Hindi clusters stay whole, [[terms]] and TeX kept, every TeX declared', async () => {
  const tl = load('media_split.portrait.indic.json');
  const m = mount(tl);
  await m.stage.seek(290);
  const v = m.view();
  const words = v.items[0].words.map((/** @type {HTMLElement} */ w) => w.textContent);
  assert.deepEqual(words, ['மின்னோட்டம்', 'என்பது', 'ஓட்டம்']);
  assert.equal(v.items[1].content.querySelector('.rt-k')?.textContent, 'धक्का');
  assert.equal(v.items[2].content.querySelector('.fake-mjx')?.textContent, 'R = V/I');
  assert.deepEqual(m.tex.seen.map((x) => x.tex), mediaSplit.tex?.(tl.scenes[0], tl.scenes[0].look, 'portrait').map((x) => x.tex));
  m.done();
});

test('a crossfade between two media_split scenes is order independent (the outgoing scene painted at its end)', async () => {
  const tl = load('media_split.landscape.json');
  const second = structuredClone(tl.scenes[0]);
  Object.assign(second, { scene_id: 's-two', index: 1, start: 10, title: 'Second scene' });
  second.look = { ...second.look, variant: 'media_left', transition_in: { kind: 'crossfade', seconds: 0.45 } };
  tl.scenes.push(second);
  tl.total_duration = 20;
  assert.deepEqual(await orderProblems(tl, [290, 300, 303, 306, 310, 320, 400, 599], { templates: TEMPLATES }), []);
});
