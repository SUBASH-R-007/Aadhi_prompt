// @ts-check
// full_bleed_media (DYNAMIC_VIDEO_DESIGN §4.3 B, R1, media; forced for an `ai_video` scene): the template contract and
// order independence on every fixture (tests/compose/dynamic/fixtures/templates/full_bleed_media.*.json: both formats,
// a dark and a light family each, an ai_video scene and a board with an application image, a matching and a
// mismatched aspect, Tamil + Devanagari + TeX), then its behaviour: the medium fills the frame, the fit it is shown
// with, the lower third's box and fit, the entrances, mediaBox against the laid-out frame and the clip's frame keys,
// what it reports to preflight (a scrim over a medium, never the page background), and the scenes it hands to
// board_generic whole. Each behaviour test was seen to fail on a mutation of the template.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import fullBleedMedia, {
  ASPECT_EPS, LABELS, LABEL_WEIGHT, LAYOUT, LINE_HEIGHT, MEDIA_ENTER, MISSING_LABEL_PX, ROW_DELAY, SIZE_STEP,
  TEXT_ENTER, TEXT_KINDS, drawsScene, kenBurnsScale, labelBox, lineHeight, mediaBg, mediaOf, mediaRegion, rowsOf,
  shownFit, slotRows, texOf, textCeiling, textItemsOf,
} from '../../../js/dynamic/templates/full_bleed_media.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import { scrimmedBg } from '../../../js/dynamic/templates/_shared.js';
import { REGIONS, ZONES, templateSlots } from '../../../js/dynamic/core.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { mediaKey, mediaSize } from '../../../js/dynamic/media.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
const FIXTURES = readdirSync(DIR).filter((n) => n.startsWith('full_bleed_media.') && n.endsWith('.json')).sort();
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const TEMPLATES = { full_bleed_media: fullBleedMedia };

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
    assert.deepEqual(await contractProblems(fullBleedMedia, tl), []);
    const last = Math.round(tl.total_duration * tl.fps) - 1;
    const frames = [0, 3, 8, 15, 24, 40, 60, 90, 120, 160, 200, 230, last].filter((f) => f <= last);
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
  const wrapped = { ...fullBleedMedia, build(/** @type {any} */ ctx) { ctxs.set(ctx.index, ctx); return fullBleedMedia.build(ctx); } };
  const tex = recordingTex();
  const { stage, done } = renderStage(tl, { templates: { full_bleed_media: wrapped }, renderTex: tex.renderTex, ...opts });
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
const textHost = (el) => (el.matches('.dyn-fbm-text, .dyn-word, h2') ? el
  : el.querySelector('.dyn-fbm-text') || ((el.textContent || '').trim() ? el : null));
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
    const stage = this.classList && this.classList.contains('dyn-stage');
    const w = stage ? px(this.style.width) || 0 : widthOf(this);
    const h = stage ? px(this.style.height) || 0 : fakeHeight(this);
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
/** `#rrggbbaa` as jsdom serialises it (the alpha to 2 decimals). @param {string} hex */
const rgba = (hex) => `rgba(${[1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)).join(', ')}, ${Number((parseInt(hex.slice(7, 9), 16) / 255).toFixed(2))})`;
/** Frame of scene time t in the first scene. @param {any} tl @param {number} t */
const frameAt = (tl, t) => Math.round(t * tl.fps);
const CLIP = { kind: 'video', asset_key: 'fbm-clip', url: '/media/fbm.mp4', duration: 10, src_fps: 30, end_behavior: 'freeze', width: 1920, height: 1080 };

// ---------------------------------------------------------------------------------------------
// Pure parts
// ---------------------------------------------------------------------------------------------

test('mediaRegion: the medium is the whole frame (full bleed, §4.3), outside the critical box on every side', () => {
  assert.deepEqual(mediaRegion(ZONES.landscape), { x0: 0, y0: 0, x1: 1920, y1: 1080 });
  assert.deepEqual(mediaRegion(ZONES.portrait), { x0: 0, y0: 0, x1: 1080, y1: 1920 });
  const zone = ZONES.landscape.critical[0];
  const box = mediaRegion(ZONES.landscape);
  assert.ok(box.x0 < zone.x0 && box.y0 < zone.y0 && box.x1 > zone.x1 && box.y1 > zone.y1);
});

test('labelBox: the bottom third of the frame, inside what text may use; 9:16 above the caption band in both bands', () => {
  const land = labelBox('landscape', ZONES.landscape);
  const full = REGIONS.landscape.full;
  assert.deepEqual(land, { x0: full.x0, y0: 720, x1: full.x1, y1: full.y1 });
  assert.equal(land.y0, 1080 - 1080 / 3, 'the bottom third of the frame');
  assert.ok(land.y1 < ZONES.landscape.caption.y0, 'clear of the caption band (burned captions)');
  const port = labelBox('portrait', ZONES.portrait);
  assert.deepEqual(port, { x0: 192, y0: 840, x1: 780, y1: ZONES.portrait.caption.y0 - 8 });
  // its width sits inside every critical band it can reach into (preflight's rule for a box that grows upward)
  for (const band of ZONES.portrait.critical) assert.ok(band.x0 <= port.x0 && port.x1 <= band.x1, JSON.stringify(band));
  assert.equal(textCeiling('portrait'), 288);
  assert.equal(textCeiling('landscape'), 54);
});

test('shownFit: a matching aspect covers the frame; 9:16 shows another aspect contain_blur (§4.3, §2.5)', () => {
  const wide = { kind: 'image', width: 1600, height: 900 };
  const four = { kind: 'image', width: 1600, height: 1200 };
  const tall = { kind: 'image', width: 1080, height: 1920 };
  assert.equal(shownFit(/** @type {any} */ (wide), ZONES.landscape), 'cover');
  assert.equal(shownFit(/** @type {any} */ (four), ZONES.landscape), 'cover', '16:9: the media covers the frame');
  assert.equal(shownFit(/** @type {any} */ (wide), ZONES.portrait), 'contain_blur');
  assert.equal(shownFit(/** @type {any} */ (tall), ZONES.portrait), 'cover', 'it already fills the frame');
  assert.equal(shownFit(null, ZONES.landscape), 'cover', 'no stored size: 16:9 is assumed');
  assert.equal(shownFit(null, ZONES.portrait), 'contain_blur');
  const nearly = { kind: 'image', width: 1000, height: Math.round(1000 / (16 / 9 + ASPECT_EPS / 2)) };
  assert.equal(shownFit(/** @type {any} */ (nearly), ZONES.landscape), 'cover', 'rounding is not a mismatch');
});

test('kenBurnsScale: a picture is extracted large enough for its own zoom (§P); a clip is never zoomed', () => {
  assert.equal(kenBurnsScale(null), 1);
  assert.equal(kenBurnsScale(/** @type {any} */ ({ kind: 'image' })), 1);
  assert.equal(kenBurnsScale(/** @type {any} */ ({ kind: 'image', ken_burns: { start: { scale: 1 }, end: { scale: 1.06 } } })), 1.06);
  assert.equal(kenBurnsScale(/** @type {any} */ ({ kind: 'image', ken_burns: { start: { scale: 1.2 }, end: { scale: 1.06 } } })), 1.2);
  assert.equal(kenBurnsScale(/** @type {any} */ ({ kind: 'image', ken_burns: {} })), 1.15, "kenburns.js's own default end scale");
  assert.equal(kenBurnsScale(/** @type {any} */ ({ kind: 'video', ken_burns: { end: { scale: 1.3 } } })), 1, 'a clip is never scaled');
});

test('rowsOf: the title is the keyword label, the subtitle and the one text item its sub-lines; nothing is dropped', () => {
  const scene = /** @type {any} */ ({ title: 'A city at dusk', subtitle: 'One formula', board: [{ id: 'b1', kind: 'paragraph', text: 'Every window obeys it' }] });
  assert.deepEqual(rowsOf(scene).map((r) => [r.slot, r.text, r.rich, r.item]), [
    ['label', 'A city at dusk', false, null],
    ['sub', 'Every window obeys it', true, 'b1'],
    ['sub', 'One formula', false, null],
  ]);
  assert.deepEqual(rowsOf(/** @type {any} */ ({ title: 'Only a title' })).map((r) => r.slot), ['label']);
  // without a title the item is the label itself
  assert.deepEqual(rowsOf(/** @type {any} */ ({ board: [{ id: 'b1', kind: 'bullet', text: 'The label' }], subtitle: 'under it' }))
    .map((r) => [r.slot, r.text]), [['label', 'The label'], ['sub', 'under it']]);
  // a definition's term joins its meaning in one row
  assert.equal(rowsOf(/** @type {any} */ ({ board: [{ id: 'd', kind: 'definition', term: 'Current', text: 'charge flowing' }] }))[0].text,
    'Current charge flowing');
  assert.deepEqual(rowsOf(/** @type {any} */ ({})), [], 'a clip with no words at all');
  assert.deepEqual(rowsOf(/** @type {any} */ ({ title: '  ', subtitle: '' })), [], 'blank is no row');
});

test('drawsScene: a medium and at most one text item; anything else is board_generic\'s whole', () => {
  const scene = /** @type {any} */ ({ title: 'T', media: { kind: 'image', url: '/a.png' }, board: [] });
  assert.equal(drawsScene(scene), true);
  assert.equal(drawsScene({ ...scene, media: null }), false, 'an ai_video without footage or still is a text board');
  assert.equal(drawsScene({ ...scene, media: null, side_panel: { media: { kind: 'image', url: '/a.png' }, show_at: 0 } }), true);
  assert.equal(drawsScene({ ...scene, media: null, side_panel: { media: { kind: 'image', url: '/a.png' }, show_at: 0 }, layout: { show_side_panel: false } }), false);
  assert.equal(drawsScene({ ...scene, board: [{ id: 'b1', kind: 'bullet', text: 'one' }] }), true);
  assert.equal(drawsScene({ ...scene, board: [{ id: 'b1', kind: 'bullet', text: 'one' }, { id: 'b2', kind: 'bullet', text: 'two' }] }), false, 'two text items are a board');
  assert.equal(drawsScene({ ...scene, board: [{ id: 'f', kind: 'formula', latex: 'x' }] }), false, 'a formula is not a label');
  assert.equal(drawsScene({ ...scene, quiz: { countdown_start: 1 } }), false, "a quiz is check's data");
  // a GIF the MP4 cannot show is still drawn here (as a missing medium), so the two modes share one layout
  assert.equal(drawsScene({ ...scene, media: { kind: 'gif', url: 'https://example.test/a.gif', render_in_mp4: false } }), true);
});

test('drawsScene: a shown side panel that is not the medium is board_generic\'s whole (never drop content, CG-2)', () => {
  // an ai_video's medium is its own footage; a terminal / chart / picture card beside it is content this template
  // lays out nothing for, and board_generic draws it (board_generic.js drawPanel)
  const base = /** @type {any} */ ({ title: 'T', media: { kind: 'video', url: '/a.mp4', asset_key: 'a' }, board: [] });
  const terminal = { panel: { kind: 'terminal', terminal: { command: 'python x.py', output: 'done' } }, show_at: 0 };
  assert.equal(drawsScene({ ...base, side_panel: terminal, layout: { show_side_panel: true } }), false);
  assert.equal(drawsScene({ ...base, side_panel: terminal }), false, 'shown unless the layout hides it');
  assert.equal(drawsScene({ ...base, side_panel: terminal, layout: { show_side_panel: false } }), true,
    'a panel the layout hides is nothing to draw');
  const chart = { panel: { kind: 'chart', title: 'Typical Current Draw (Amperes)' }, show_at: 0 };
  assert.equal(drawsScene({ ...base, side_panel: chart }), false);
  // the panel that *is* the medium (a board scene's application picture) is still drawn: slot `panel`
  const picture = /** @type {any} */ ({
    title: 'T', board: [{ id: 'b1', kind: 'bullet', text: 'one' }],
    side_panel: { panel: { kind: 'image' }, media: { kind: 'image', url: '/a.png' }, show_at: 0 },
  });
  assert.equal(mediaOf(picture)?.slot, 'panel');
  assert.equal(drawsScene(picture), true);
});

test('mediaBox answers only the slot it lays out: another slot gets board_generic\'s box, never the full frame', () => {
  const fixes = /** @type {any} */ ({ safe: false, slots: {} });
  const scene = /** @type {any} */ ({
    title: 'T', media: { kind: 'video', url: '/a.mp4', asset_key: 'a' }, board: [],
    side_panel: { panel: { kind: 'image' }, media: { kind: 'video', url: '/p.mp4', asset_key: 'p' }, show_at: 0 },
    layout: { show_side_panel: false },
  });
  assert.equal(mediaOf(scene, 'render')?.slot, 'main');
  assert.deepEqual(fullBleedMedia.mediaBox?.(scene, ZONES.landscape, 'render', 'main', fixes), { w: 1920, h: 1080 });
  // the panel clip is not laid out here: board_generic's panel box, not 1920x1080
  const panelBox = fullBleedMedia.mediaBox?.(scene, ZONES.landscape, 'render', 'panel', fixes);
  assert.deepEqual(panelBox, boardGeneric.mediaBox?.(scene, ZONES.landscape, 'render', 'panel', fixes));
  assert.ok(panelBox && panelBox.w < 1920, 'a panel clip is never asked for at the whole frame');
});

test('mediaOf / textItemsOf / texOf: what the scene shows and converts', () => {
  const tl = load('full_bleed_media.landscape.json');
  const s = tl.scenes[0];
  assert.equal(mediaOf(s, 'render')?.slot, 'main');
  assert.equal(mediaOf(s, 'render')?.shown, true);
  const panel = load('full_bleed_media.landscape.light.json').scenes[0];
  assert.equal(mediaOf(panel, 'render')?.slot, 'panel');
  const gif = { ...s, media: { kind: 'gif', url: 'https://example.test/a.gif', render_in_mp4: false } };
  assert.equal(mediaOf(gif, 'render')?.shown, false, 'a GIF is live only');
  assert.equal(mediaOf(gif, 'live')?.shown, true);
  assert.deepEqual(textItemsOf(/** @type {any} */ ({ board: [{ id: 'a', kind: 'formula' }, { id: 'b', kind: 'takeaway' }] })).map((i) => i.id), ['b']);
  assert.deepEqual(texOf(panel), [{ tex: 'I = V/R', display: false }], "the item's TeX, declared for the pre-pass");
  assert.deepEqual(texOf(s), [], 'a title and a subtitle are plain text');
});

test('TEXT_KINDS is templates.py TEXT_KINDS (the text items the fit of this template counts)', () => {
  const py = readFileSync(new URL('../../../../aadhi/compose/dynamic/templates.py', import.meta.url), 'utf-8');
  const m = /TEXT_KINDS: Final = frozenset\(\{([^}]*)\}\)/.exec(py);
  assert.ok(m);
  const kinds = [...(/** @type {RegExpExecArray} */ (m))[1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]).sort();
  assert.deepEqual([...TEXT_KINDS].sort(), kinds);
});

test('line heights per script (§4.6, plan §2 R-29): the type scale\'s, Indic text at the Indic floors', () => {
  const scale = /** @type {any} */ (CATALOGUE).type.scale;
  assert.ok(LINE_HEIGHT.display.latin >= scale.display.line_height.latin[0] && LINE_HEIGHT.display.latin <= scale.display.line_height.latin[1]);
  assert.equal(LINE_HEIGHT.display.indic, scale.display.line_height.indic[0]);
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.body.indic, scale.body.line_height.indic[0]);
  assert.equal(lineHeight('display', 'மின்சாரம்'), LINE_HEIGHT.display.indic);
  assert.equal(lineHeight('body', 'का नियम'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('body', 'One law'), LINE_HEIGHT.body.latin);
});

test('R-29 floors: every slot minimum is at or above its format floor, and 9:16 sub-lines are weight >= 600', () => {
  const floors = /** @type {any} */ (CATALOGUE).type.floors;
  assert.ok(LAYOUT.landscape.label.min >= floors.landscape.text_min);
  assert.ok(LAYOUT.landscape.sub.min >= floors.landscape.label_min);
  assert.ok(LAYOUT.portrait.label.min >= floors.portrait.text_min);
  assert.ok(LAYOUT.portrait.sub.min >= floors.portrait.label_min);
  assert.ok(LAYOUT.portrait.sub.max <= floors.portrait.heavy_max_px
    ? LAYOUT.portrait.subWeight >= floors.portrait.heavy_min_weight : true);
  assert.equal(LAYOUT.landscape.label.prefer, 1, 'R-25: one line when it fits');
});

test('the catalogue\'s full_bleed_media slots (templates.py) are slotRows exactly, once it is registered', () => {
  const spec = /** @type {any} */ (CATALOGUE.templates).full_bleed_media;
  const cat = /** @type {any} */ (templateSlots('full_bleed_media'));
  const want = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  if (!spec.implemented) {
    // before the integrator registers it (templates.py `implemented=True`, `slots=_full_bleed_media_slots()`), the
    // catalogue has no slot table for it; this test turns strict the moment it does
    assert.deepEqual(cat || {}, {}, 'an unregistered template has no slots in the catalogue');
    return;
  }
  for (const slot of Object.keys(want)) {
    for (const fmt of Object.keys(want[slot])) {
      for (const variant of Object.keys(want[slot][fmt])) {
        const got = cat[slot][fmt][variant];
        assert.deepEqual({ w: got.w, max_lines: got.max_lines, size: got.size, role: got.role, face: got.face,
          weight: got.weight, line_height: got.line_height, prefer_lines: got.prefer_lines, tracking_em: got.tracking_em },
        want[slot][fmt][variant], `${slot} ${fmt} ${variant}`);
      }
    }
  }
  assert.deepEqual(Object.keys(cat).sort(), Object.keys(want).sort());
  assert.deepEqual(spec.consumes.forms, { reveal: ['item'] });
  assert.equal(spec.push_ok, false, 'a clip is never pushed (cues.js showsClip)');
});

test('mediaBg: the scrim over the darkest and the lightest medium, the worse against the ink (never the page bg)', () => {
  const scrim = '#10132CC7';
  assert.equal(mediaBg(scrim, '#FFFFFF'), scrimmedBg(scrim, { bg: '#000000', bgs: ['#000000', '#FFFFFF'] }, '#FFFFFF'));
  assert.match(mediaBg(scrim, '#FFFFFF'), /^#[0-9a-f]{6}$/i, 'opaque: what preflight measures');
  assert.notEqual(mediaBg(scrim, '#FFFFFF'), mediaBg('#FDF2DDC7', '#FFFFFF'), 'the family\'s own scrim decides it');
});

// ---------------------------------------------------------------------------------------------
// On the stage
// ---------------------------------------------------------------------------------------------

test('the medium fills the frame and takes the fit shownFit gives it (the ref itself is never changed)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const m = mount(tl);
    await m.stage.seek(60);
    const v = m.view();
    const zones = ZONES[/** @type {'landscape' | 'portrait'} */ (tl.presentation.format)];
    assert.deepEqual(boxOf(v.frame), { x0: 0, y0: 0, x1: zones.width, y1: zones.height }, `${name}: the whole frame`);
    const fit = shownFit(v.media.ref, zones);
    assert.equal(v.mediaView.fit, fit, `${name}: ${fit}`);
    assert.ok(v.mediaView.el.classList.contains(`dyn-media--${fit}`));
    assert.equal(v.media.ref.fit, tl.scenes[0].media ? tl.scenes[0].media.fit : tl.scenes[0].side_panel.media.fit,
      'the scene keeps the fit it was built with');
    m.done();
  }
});

test('the lower third: bottom-anchored in its box, one size per slot, the scrim a band to the bottom of the frame', async () => {
  const undo = layoutModel();
  try {
    for (const name of ['full_bleed_media.landscape.json', 'full_bleed_media.portrait.json']) {
      const tl = load(name);
      const fmt = /** @type {'landscape' | 'portrait'} */ (tl.presentation.format);
      const L = LAYOUT[fmt];
      const m = mount(tl);
      await m.stage.seek(frameAt(tl, 4));
      const v = m.view();
      const box = labelBox(fmt, ZONES[fmt]);
      assert.deepEqual(v.box, box);
      assert.equal(v.rows.length, 2, 'the title and the subtitle');
      for (const row of v.rows) {
        assert.equal(px(row.inner.style.left), box.x0);
        assert.equal(px(row.inner.style.width), box.x1 - box.x0);
      }
      const tops = v.rows.map((/** @type {any} */ r) => px(r.inner.style.top));
      assert.ok(tops[0] < tops[1], 'the label above its sub-line');
      const bottom = tops[1] + fakeHeight(v.rows[1].text);
      assert.ok(Math.abs(bottom - box.y1) <= 1, `bottom-anchored: ${bottom} vs ${box.y1}`);
      assert.equal(v.blockTop, tops[0]);
      assert.ok(v.rows.every((/** @type {any} */ r) => r.overflow === 0), 'the fixture fits');
      assert.ok(v.rows[0].size >= L.label.min && v.rows[0].size <= L.label.max, `label ${v.rows[0].size}`);
      assert.ok(v.rows[1].size >= L.sub.min && v.rows[1].size <= L.sub.max, `sub ${v.rows[1].size}`);
      // the scrim: from the block's top padding to the bottom of the frame, the full width
      const scrim = boxOf(v.scrim);
      assert.deepEqual([scrim.x0, scrim.x1, scrim.y1], [0, ZONES[fmt].width, ZONES[fmt].height]);
      assert.equal(scrim.y0, v.blockTop - L.padY);
      assert.equal(v.scrim.style.background, rgba(m.ctx().roles.color('scrim')), 'the scrim role, >= 55% (§4.3)');
      m.done();
    }
  } finally {
    undo();
  }
});

test('the fit: the sub-lines give up size before the label; what is past the critical area is reported', async () => {
  const undo = layoutModel();
  try {
    const long = 'Wide words make maximum width demands on every single line of the lower third';
    // a subtitle one line too long for the third: the sub-line gives up size and the label keeps all of its
    const tl = edited('full_bleed_media.landscape.json', (s) => {
      s.subtitle = 'Pressure, flow and a narrow pipe decide the current in every wire';
    });
    const m = mount(tl);
    await m.stage.seek(frameAt(tl, 6));
    const v = m.view();
    assert.ok(v.rows[1].size < LAYOUT.landscape.sub.max && v.rows[1].size > LAYOUT.landscape.sub.min, `sub ${v.rows[1].size}`);
    assert.equal(v.rows[0].size, LAYOUT.landscape.label.max, 'the label kept its size');
    assert.ok(v.rows.every((/** @type {any} */ r) => r.overflow === 0));
    m.done();
    // far too much text: every slot at its minimum, and the block past the critical area is reported as overflow
    const over = edited('full_bleed_media.landscape.json', (s) => {
      s.title = `${long} ${long} ${long}`;
      s.subtitle = `${long} ${long} ${long} ${long} ${long} ${long} ${long} ${long}`;
    });
    const om = mount(over);
    await om.stage.seek(frameAt(over, 6));
    const ov = om.view();
    assert.equal(ov.rows[0].size, LAYOUT.landscape.label.min);
    assert.equal(ov.rows[1].size, LAYOUT.landscape.sub.min);
    const entries = /** @type {any[]} */ (fullBleedMedia.texts?.(om.ctx()));
    assert.ok(entries.some((e) => e.overflow > 0), 'reported (preflight: shrink, then block with "Shorten text")');
    assert.ok(entries.every((e) => e.alpha >= 0 && e.alpha <= 1));
    om.done();
    // a block a little taller than a third but inside the critical area is not an overflow
    const tall = edited('full_bleed_media.landscape.json', (s) => {
      s.board = [{ id: 'b1', kind: 'paragraph', text: 'A third sub-line that makes the block taller than a third' }];
      s.beats[0].board_item_id = 'b1';
    });
    const tm = mount(tall);
    await tm.stage.seek(frameAt(tall, 6));
    const tv = tm.view();
    assert.equal(tv.rows.length, 3);
    assert.ok(tv.blockH > labelBox('landscape', ZONES.landscape).y1 - labelBox('landscape', ZONES.landscape).y0,
      'taller than the third');
    assert.ok(tv.rows.every((/** @type {any} */ r) => r.overflow === 0), 'still safe: no block');
    assert.ok(tv.blockTop >= textCeiling('landscape'), 'inside the critical area');
    tm.done();
  } finally {
    undo();
  }
});

test('the medium enters by a fade and is never scaled or pushed; the scrim and the rows follow their beats', async () => {
  const tl = load('full_bleed_media.landscape.json');
  const m = mount(tl);
  await m.stage.seek(0);
  const v = m.view();
  assert.equal(v.frame.style.opacity, '0');
  assert.equal(v.frame.style.visibility, 'hidden');
  assert.equal(v.frame.style.transform, '', 'the template never transforms the frame (a clip is never scaled, §P)');
  await m.stage.seek(frameAt(tl, MEDIA_ENTER * 0.5 * 1.15)); // halfway (Soft: x1.15)
  assert.ok(Number(v.frame.style.opacity) > 0 && Number(v.frame.style.opacity) < 1, v.frame.style.opacity);
  await m.stage.seek(frameAt(tl, 2));
  assert.deepEqual([Number(v.frame.style.opacity), v.frame.style.visibility, v.frame.style.transform], [1, 'visible', '']);
  // the rows: the label first, its sub-line ROW_DELAY later, the scrim with the label
  const alphas = async (/** @type {number} */ t) => {
    await m.stage.seek(frameAt(tl, t));
    return v.rows.map((/** @type {any} */ r) => r.alpha);
  };
  assert.deepEqual(await alphas(0.02), [0, 0], 'before the label enters');
  const mid = await alphas(0.08 + ROW_DELAY + TEXT_ENTER * 1.15 * 0.5);
  assert.ok(mid[0] > mid[1] && mid[1] > 0, `staggered: ${mid}`);
  assert.deepEqual(await alphas(3), [1, 1], 'and both stay to the end of the scene');
  assert.deepEqual(await alphas(tl.total_duration - 0.1), [1, 1]);
  assert.equal(v.scrim.style.opacity, '1');
  assert.equal(v.scrim.style.visibility, 'visible');
  m.done();
  // reduced motion (live only): a fade in place
  const live = mount(load('full_bleed_media.landscape.json'), { mode: 'live', reducedMotion: true });
  live.stage.update(0.2);
  const lv = live.view();
  assert.equal(lv.rows[0].text.style.transform, 'none');
  live.done();
});

test('the personality text entrance: Crisp reveals from the baseline inside a mask, Soft fades with a rise', async () => {
  const crisp = mount(load('full_bleed_media.portrait.json')); // Deep Teal / Crisp
  await crisp.stage.seek(0);
  assert.equal(crisp.stage.el.querySelectorAll('.dyn-fbm .dyn-reveal-mask').length, 2, 'the label and its sub-line');
  crisp.done();
  const soft = mount(load('full_bleed_media.landscape.json')); // Midnight Indigo / Soft
  await soft.stage.seek(0);
  assert.equal(soft.stage.el.querySelectorAll('.dyn-fbm .dyn-reveal-mask').length, 0);
  soft.done();
});

test('mediaBox (static): the whole frame with the picture\'s Ken Burns headroom, under every fix and in both modes', () => {
  const fixes = [{ safe: false, slots: {} }, { safe: true, slots: {} },
    { safe: false, slots: { label: { size: 1, scrim: true }, sub: { size: 1, scrim: true } } }];
  for (const name of FIXTURES) {
    const tl = load(name);
    const s = tl.scenes[0];
    const zones = ZONES[/** @type {'landscape' | 'portrait'} */ (tl.presentation.format)];
    const own = /** @type {any} */ (mediaOf(s, 'render'));
    const k = kenBurnsScale(own.ref);
    for (const f of fixes) {
      for (const mode of /** @type {const} */ (['live', 'render'])) {
        // the slot the layout shows (`main` for an ai_video, `panel` for a board's application picture): the box of
        // any other slot is board_generic's, never the full frame
        assert.deepEqual(fullBleedMedia.mediaBox?.(s, zones, mode, own.slot, /** @type {any} */ (f)),
          { w: Math.ceil(zones.width * k), h: Math.ceil(zones.height * k) }, `${name} ${mode}`);
      }
    }
  }
  // a clip gets the frame itself (it is never zoomed)
  const tl = edited('full_bleed_media.landscape.json', (s) => { s.media = { ...CLIP }; });
  assert.deepEqual(fullBleedMedia.mediaBox?.(tl.scenes[0], ZONES.landscape, 'render', 'main', { safe: false, slots: {} }),
    { w: 1920, h: 1080 });
});

test('a clip: full bleed, a fade only, and its frames are the ones mediaPlan lists at mediaBox\'s size', async () => {
  for (const name of ['full_bleed_media.landscape.json', 'full_bleed_media.portrait.json']) {
    const tl = edited(name, (s) => { s.media = { ...CLIP }; });
    const zones = ZONES[/** @type {'landscape' | 'portrait'} */ (tl.presentation.format)];
    const m = mount(tl);
    await m.stage.seek(60);
    const v = m.view();
    assert.equal(v.frame.style.transform, '', 'a clip is never scaled (B2c: measured order-dependent)');
    const box = /** @type {any} */ (fullBleedMedia.mediaBox?.(tl.scenes[0], zones, 'render', 'main', { safe: false, slots: {} }));
    const key = mediaKey('video', CLIP.asset_key, mediaSize(CLIP, box));
    const img = /** @type {HTMLImageElement} */ (v.frame.querySelector('img'));
    assert.match(String(img.getAttribute('src')), new RegExp(`/__frames/${key}/\\d{6}\\.jpg$`));
    assert.deepEqual(m.stage.mediaPlan(60, 61).map((/** @type {any} */ e) => e.key), [key]);
    m.done();
    assert.deepEqual(await orderProblems(tl, [0, 8, 30, 60, 150, 239], { templates: TEMPLATES }), [], `${name}: a clip is order independent too`);
  }
});

test('texts(): the rows on the scrim over the medium, critical, with their slot, size, minimum and weight', async () => {
  const undo = layoutModel();
  try {
    const tl = load('full_bleed_media.landscape.light.json');
    const m = mount(tl);
    await m.stage.seek(frameAt(tl, 6));
    const ctx = m.ctx();
    const v = m.view();
    const entries = /** @type {any[]} */ (fullBleedMedia.texts?.(ctx));
    assert.deepEqual(entries.map((e) => e.slot), ['label', 'sub']);
    const [label, sub] = entries;
    assert.deepEqual([label.role, label.critical, label.weight, label.min], ['title', true, LABEL_WEIGHT, LAYOUT.landscape.label.min]);
    assert.deepEqual([sub.role, sub.critical, sub.weight, sub.min], ['label', true, LAYOUT.landscape.sub.weight || LAYOUT.landscape.subWeight, LAYOUT.landscape.sub.min]);
    for (const e of entries) {
      assert.equal(e.bg, mediaBg(v.scrimColour, e.ink), 'the scrim over the medium, never the page background');
      assert.notEqual(e.bg, ctx.onBackground.bg);
      assert.ok(e.size >= e.min);
    }
    assert.equal(label.ink, ctx.roles.color('ink'));
    assert.equal(sub.ink, ctx.roles.color('ink2'));
    m.done();
  } finally {
    undo();
  }
});

test('no medium this mode can show: the panel title on the card surface (never an empty box), a label to preflight', async () => {
  for (const edit of [
    (/** @type {any} */ s) => { s.side_panel.media = { kind: 'gif', url: 'https://example.test/a.gif', render_in_mp4: false, width: 400, height: 300 }; },
    (/** @type {any} */ s) => { s.side_panel.media.url = 'javascript:alert(1)'; },
  ]) {
    const tl = edited('full_bleed_media.landscape.light.json', edit);
    const m = mount(tl);
    await m.stage.seek(60);
    const v = m.view();
    assert.ok(v.missing, 'a label');
    assert.equal(v.missing.textContent, tl.scenes[0].side_panel.panel.title);
    assert.equal(v.frame.style.background, rgb(m.ctx().roles.color('surface')));
    const entry = /** @type {any[]} */ (fullBleedMedia.texts?.(m.ctx())).find((e) => e.el === v.missing);
    assert.deepEqual([entry.role, entry.critical, entry.size], ['label', false, MISSING_LABEL_PX.landscape]);
    m.done();
  }
  // no panel title either: a plain label
  const tl = edited('full_bleed_media.landscape.json', (s) => { s.media.url = 'javascript:alert(1)'; });
  const m = mount(tl);
  await m.stage.seek(60);
  assert.equal(m.view().missing.textContent, LABELS.mediaMissing);
  m.done();
});

test('cue hooks: itemEl keeps the [[term]] span, focusRect the frame or a shown row, dimGroups fixed', async () => {
  const undo = layoutModel();
  try {
    const tl = load('full_bleed_media.landscape.light.json');
    const m = mount(tl);
    await m.stage.seek(frameAt(tl, 6));
    const ctx = m.ctx();
    const v = m.view();
    const el = /** @type {Element} */ (fullBleedMedia.itemEl?.(ctx, 'b1'));
    assert.equal(el.querySelector('.rt-k')?.textContent, 'law', 'the [[term]] span is kept (richWords)');
    assert.equal(fullBleedMedia.itemEl?.(ctx, 'nope'), null);
    const panel = /** @type {any} */ (fullBleedMedia.focusRect?.(ctx, 'panel'));
    assert.deepEqual(panel.rect, { x: 0, y: 0, w: 1920, h: 1080 });
    assert.deepEqual([panel.kind, panel.el], ['shape', v.mediaGroup]);
    const row = /** @type {any} */ (fullBleedMedia.focusRect?.(ctx, 'b1'));
    assert.deepEqual([row.kind, row.el], ['text', v.lowerGroup]);
    assert.ok(['x', 'y', 'w', 'h'].every((k) => Number.isInteger(row.rect[k])), JSON.stringify(row.rect));
    assert.deepEqual(fullBleedMedia.focusRect?.(ctx, 'b1:term:0'), fullBleedMedia.focusRect?.(ctx, 'b1'));
    assert.equal(fullBleedMedia.focusRect?.(ctx, 'b1:line:0'), null, 'a form it does not draw');
    assert.equal(fullBleedMedia.focusRect?.(ctx, 'B 1'), null, 'not a target');
    await m.stage.seek(0);
    assert.equal(fullBleedMedia.focusRect?.(ctx, 'panel'), null, 'a medium not yet shown');
    assert.equal(fullBleedMedia.focusRect?.(ctx, 'b1'), null, 'a row not yet shown');
    assert.deepEqual(fullBleedMedia.dimGroups?.(ctx), [v.mediaGroup, v.lowerGroup]);
    m.done();
  } finally {
    undo();
  }
});

test('a scene it does not draw whole goes to board_generic, every hook handed on (nothing is dropped)', async () => {
  // the ai_video the asset stage could not make a still for: compose/timeline.py writes it as a text board
  const tl = edited('full_bleed_media.landscape.json', (s) => {
    s.media = null;
    s.board = [{ id: 'fallback-title', kind: 'heading', text: 'THE INVISIBLE RIVER' },
      { id: 'fallback-visual', kind: 'paragraph', text: 'A cinematic dolly shot of a mascot plugging in a charger, pushing in toward the socket as a warm light glows.' }];
    s.beats[0].board_item_id = 'fallback-title';
    s.beats[1].board_item_id = 'fallback-visual';
  });
  const s = tl.scenes[0];
  assert.equal(drawsScene(s), false);
  const m = mount(tl);
  await m.stage.seek(frameAt(tl, 6));
  const ctx = m.ctx();
  assert.equal(ctx.view.fbmFallback, true);
  assert.ok(m.stage.el.querySelector('.dyn-board'), 'board_generic drew it');
  const texts = /** @type {any[]} */ (fullBleedMedia.texts?.(ctx));
  const whole = texts.map((e) => (e.text || '').trim()).join(' ');
  for (const item of s.board) assert.ok(whole.includes(item.text.slice(0, 24)), `kept: ${item.id}`);
  assert.deepEqual(fullBleedMedia.dimGroups?.(ctx), boardGeneric.dimGroups?.(ctx));
  assert.deepEqual(fullBleedMedia.tex?.(s, s.look, 'landscape'), boardGeneric.tex?.(s, s.look, 'landscape'));
  assert.deepEqual(fullBleedMedia.mediaBox?.(s, ZONES.landscape, 'render', 'main', { safe: false, slots: {} }),
    boardGeneric.mediaBox?.(s, ZONES.landscape, 'render', 'main', { safe: false, slots: {} }));
  assert.equal(fullBleedMedia.itemEl?.(ctx, 'fallback-title'), boardGeneric.itemEl?.(ctx, 'fallback-title'));
  m.done();
  // the frames stay a pure function of t through the fallback too (the contract's dim-group rule is board_generic's
  // own business there: it declares no cue, so nothing ever dims its groups)
  assert.deepEqual(await orderProblems(tl, [0, 10, 60, 150, 299], { templates: TEMPLATES }), []);
});

test('the Indic fixture: the label at the Indic display line height, its sub-line at the Indic body one', async () => {
  const tl = load('full_bleed_media.portrait.indic.json');
  const m = mount(tl);
  await m.stage.seek(60);
  const v = m.view();
  assert.equal(Number(v.rows[0].text.style.lineHeight), LINE_HEIGHT.display.indic);
  assert.equal(Number(v.rows[1].text.style.lineHeight), LINE_HEIGHT.body.indic);
  assert.equal(v.rows[1].slot, 'sub');
  m.done();
});

test('a medium with no words at all: the frame alone, no scrim, nothing reported to preflight', async () => {
  const undo = layoutModel();
  try {
    const tl = edited('full_bleed_media.landscape.json', (s) => {
      s.title = '';
      s.subtitle = null;
    });
    const m = mount(tl);
    await m.stage.seek(60);
    const v = m.view();
    assert.deepEqual(v.rows, []);
    assert.equal(v.scrim.style.visibility, 'hidden');
    assert.equal(px(v.scrim.style.height), 0);
    assert.deepEqual(fullBleedMedia.texts?.(m.ctx()), []);
    assert.deepEqual(boxOf(v.frame), { x0: 0, y0: 0, x1: 1920, y1: 1080 });
    m.done();
  } finally {
    undo();
  }
});

test('the sizes step in 4 px (board_generic\'s rule, which textfit.py mirrors)', () => {
  assert.equal(SIZE_STEP, 4);
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    assert.equal((L.label.max - L.label.min) % SIZE_STEP, 0, `${fmt} label`);
    assert.equal((L.sub.max - L.sub.min) % SIZE_STEP, 0, `${fmt} sub`);
  }
});
