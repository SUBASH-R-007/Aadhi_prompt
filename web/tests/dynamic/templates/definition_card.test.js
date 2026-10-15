// @ts-check
// templates/definition_card.js (DYNAMIC_VIDEO_DESIGN §4.3 definition_card, B2 plan §1.4 template contract, §1.5 cues,
// §1.11 slots, §P): the four fixture timelines (tests/compose/dynamic/make_definition_card_fixtures.py) through the
// harness's contract and order gates, the pure helpers (data, variants, geometry, slots, TeX), the painted behaviour in
// both formats (the card's rise, the term then the meaning word by word, the example on its beat, the stored icon, the
// picture, the personality's entrance and scale, reduced motion, the cues the stage draws from the template's geometry,
// preflight's text entries and remedies) and the fit, on a fake text layout (jsdom has none).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { LAYOUT_TOKENS, Painter, TYPE_FLOORS, ZONES, fx, parseTarget, templateSlots } from '../../../js/dynamic/core.js';
import { FOCUS_OUTSET, FOCUS_PX, outlinePath, shapeArea, termPieces } from '../../../js/dynamic/cues.js';
import { itemTimings } from '../../../js/dynamic/state.js';
import { TEXT_RISE } from '../../../js/dynamic/templates/_shared.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import definitionCard, {
  BORDER_PX, CARD_ENTER, CARD_RISE, ICON_ENTER, LAYOUT, LINE_HEIGHT, MEANING_DELAY, MEDIA_ENTER, MEDIA_FROM,
  MISSING_LABEL_PX, SIZE_STEP, TERM_TRACKING_EM, TEXT_ENTER, VARIANTS, WORD_STAGGER, boxes, cardItems, drawsScene,
  lineHeight, pictureOf, slotRows, texOf, textWidth, variantOf,
} from '../../../js/dynamic/templates/definition_card.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['definition_card.landscape.json', 'definition_card.landscape.light.json',
  'definition_card.portrait.json', 'definition_card.portrait.indic.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { definition_card: definitionCard } };

/**
 * A render stage of `tl` with definition_card through the stage's test seam, and the context each scene was built with.
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...definitionCard, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return definitionCard.build(ctx); } };
  const run = renderStage(tl, { templates: { definition_card: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
  /** Seek scene `i` to the frame nearest scene time `t`; returns that frame's exact scene time. @param {number} i @param {number} t */
  const seekTo = async (i, t) => {
    const scene = tl.scenes[i];
    const f = Math.round((Number(scene.start) + t) * tl.fps);
    await run.stage.seek(f);
    return f / tl.fps - Number(scene.start);
  };
  return { ...run, contexts, seekTo, ctx: (/** @type {number} */ i) => contexts.get(i) };
}

/** The scene-relative entrance of item `id` (state.js itemTimings: a reveal event, else its beat's start less 120 ms). */
const enterOf = (/** @type {any} */ scene, /** @type {string} */ id) => /** @type {number} */ (itemTimings(scene).find((x) => x.id === id)?.enter);
/** A hex colour as the DOM reports a painted one. @param {string} hex */
const rgb = (hex) => `rgb(${[1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16)).join(', ')})`;
/** An opacity as a number ('' is 1: none painted). @param {string} v */
const op = (v) => (v === '' ? 1 : Number(v));

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, whole-px focus, terms, TeX, entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(definitionCard, load(name)), [], name);
});

test('every fixture paints the same frame forward, in reverse and on a fresh page (a frame is a pure function of t)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const total = Math.round(tl.total_duration * tl.fps);
    const frames = new Set();
    for (let f = 0; f < total; f += 9) frames.add(f);
    for (const s of tl.scenes) {
      for (const e of s.events || []) for (const d of [0, 0.2, 0.5]) frames.add(Math.min(total - 1, Math.round((s.start + e.t + d) * tl.fps)));
      for (const b of s.beats || []) frames.add(Math.min(total - 1, Math.round((s.start + b.start) * tl.fps)));
    }
    assert.deepEqual(await orderProblems(tl, [...frames].sort((a, b) => a - b), SEAM), [], name);
  }
});

test('the fixtures cover what the plan §G asks: both formats, dark and light, every variant, entrance, cue style and text case', () => {
  const seen = { formats: new Set(), modes: new Set(), variants: new Set(), entrances: new Set(), cues: new Set(), styles: new Set(), scripts: new Set() };
  for (const name of FIXTURES) {
    const tl = load(name);
    const fmt = tl.presentation.format;
    seen.formats.add(fmt);
    seen.modes.add(tl.presentation.mode);
    seen.entrances.add(tl.presentation.text_entrance);
    seen.styles.add(tl.presentation.cue_term_style);
    for (const s of tl.scenes) {
      seen.variants.add(variantOf(s, fmt, 'render', s.look));
      for (const e of s.events || []) seen.cues.add(e.kind);
      const text = (s.board || []).map((/** @type {any} */ b) => `${b.term || ''} ${b.text || ''}`).join(' ');
      if (/\p{Script=Tamil}/u.test(text)) seen.scripts.add('tamil');
      if (/\p{Script=Devanagari}/u.test(text)) seen.scripts.add('devanagari');
      if (/\$[^$]+\$/.test(text)) seen.scripts.add('tex');
      if (/[A-Za-z]{40,}/.test(text)) seen.scripts.add('long-word');
      if (/(?:\b[A-Z]{3,}\b.*){6}/.test(text)) seen.scripts.add('wide-caps');
    }
  }
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.variants].sort(), [...VARIANTS].sort());
  assert.deepEqual([...seen.entrances].sort(), ['baseline_reveal', 'fade_rise']);
  assert.deepEqual([...seen.styles].sort(), ['marker', 'underline']);
  assert.deepEqual([...seen.cues].sort(), ['cue_term', 'reveal']);
  assert.deepEqual([...seen.scripts].sort(), ['devanagari', 'long-word', 'tamil', 'tex', 'wide-caps']);
});

// --- pure helpers ---------------------------------------------------------------------------------------------------

test('cardItems: the first definition with text is the term, every other item with text an example row', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'x', kind: 'bullet', text: 'first example' },
    { id: 'd1', kind: 'definition', term: 'Resistance', text: 'how it opposes charge' },
    { id: 'e', kind: 'bullet', text: '  ' },
    { id: 'd2', kind: 'definition', term: 'Second', text: 'another one' },
    { id: 'b1', kind: 'bullet', text: 'a thin wire' },
  ] });
  const { term, rows } = cardItems(scene);
  assert.equal(term?.id, 'd1');
  assert.deepEqual(rows.map((r) => r.id), ['x', 'd2', 'b1'], 'empty text is no row; a second definition is a row');
  assert.deepEqual(cardItems(/** @type {any} */ ({ board: [] })), { term: null, rows: [] });
  // a definition with only a term (the meaning is optional in the schema) is still the term
  assert.equal(cardItems(/** @type {any} */ ({ board: [{ id: 'd', kind: 'definition', term: 'Only' }] })).term?.id, 'd');
});

test('variantOf: the look\'s variant when its data is there, else derived from the picture or the stored icon', () => {
  const tl = load('definition_card.landscape.json');
  const scene = tl.scenes[0];
  assert.equal(variantOf(scene, 'landscape', 'render', scene.look), 'card_icon_right');
  assert.equal(variantOf(scene, 'landscape', 'render', { ...scene.look, icons: {} }), 'centred', 'no icon: nothing beside the term');
  assert.equal(variantOf(scene, 'landscape', 'render', { ...scene.look, variant: 'nope' }), 'card_icon_right', 'an unknown name is derived');
  assert.equal(variantOf(scene, 'landscape', 'render', { ...scene.look, variant: 'centred' }), 'centred', 'centred always holds');
  assert.equal(variantOf(scene, 'portrait', 'render', scene.look), 'card_icon_right');
  const image = load('definition_card.landscape.light.json');
  const pic = image.scenes[0];
  assert.equal(variantOf(pic, 'landscape', 'render', pic.look), 'card_image_left');
  assert.equal(pictureOf(pic, 'render')?.slot, 'panel');
  assert.equal(variantOf(pic, 'portrait', 'render', pic.look), 'centred', '9:16 shows no picture, so the card is centred');
  // a medium this mode cannot show keeps its box (the panel's title card), never an empty layout
  const gif = { ...pic, side_panel: { panel: { kind: 'gif', title: 'A spark' }, media: { kind: 'gif', url: '/x.gif' }, show_at: 0 } };
  assert.deepEqual([pictureOf(gif, 'render')?.shown, pictureOf(gif, 'live')?.shown], [false, true]);
  assert.equal(variantOf(gif, 'landscape', 'render', { ...pic.look, icons: {} }), 'card_image_left');
});

test('drawsScene: a definition is needed; 16:9 takes a picture panel only, 9:16 nothing beside the card (CG-2)', () => {
  const tl = load('definition_card.landscape.json');
  const scene = tl.scenes[0];
  assert.ok(drawsScene(scene, 'landscape') && drawsScene(scene, 'portrait'));
  assert.ok(!drawsScene({ ...scene, board: [{ id: 'b', kind: 'bullet', text: 'no definition' }] }, 'landscape'));
  for (const kind of ['figure', 'image', 'manim', 'gif']) {
    const s = { ...scene, side_panel: { panel: { kind }, show_at: 0 }, layout: { show_side_panel: true } };
    assert.ok(drawsScene(s, 'landscape'), kind);
    assert.ok(!drawsScene(s, 'portrait'), `9:16 ${kind}`);
  }
  for (const kind of ['terminal', 'chart', 'graph', 'model_3d', 'skill_tree', 'quiz']) {
    const s = { ...scene, side_panel: { panel: { kind }, show_at: 0 }, layout: { show_side_panel: true } };
    assert.ok(!drawsScene(s, 'landscape'), kind);
  }
  const hidden = { ...scene, side_panel: { panel: { kind: 'chart' }, show_at: 0 }, layout: { show_side_panel: false } };
  assert.ok(drawsScene(hidden, 'landscape') && drawsScene(hidden, 'portrait'), 'a hidden panel is not drawn anyway');
  assert.ok(!drawsScene({ ...scene, media: { kind: 'image', url: '/m.png' } }, 'portrait'), '9:16: the scene\'s own picture too');
});

test('a scene this template does not draw is drawn by board_generic whole, every hook handed on', async () => {
  const tl = load('definition_card.portrait.json');
  tl.scenes[0].side_panel = { panel: { kind: 'image', title: 'P' }, media: { kind: 'image', url: '/media/p.png', width: 800, height: 600 }, show_at: 0 };
  tl.scenes[0].layout = { show_side_panel: true };
  const run = renderStage(tl, { templates: { definition_card: definitionCard } });
  await run.stage.seek(30);
  assert.ok(run.stage.el.querySelector('.dyn-board'), 'board_generic drew it');
  assert.equal(run.stage.el.querySelector('.dyn-dc'), null);
  const scene = tl.scenes[0];
  const zones = ZONES.portrait;
  const fixes = { safe: false, slots: {} };
  assert.deepEqual(definitionCard.tex?.(scene, scene.look, 'portrait'), boardGeneric.tex?.(scene, scene.look, 'portrait'));
  assert.deepEqual(definitionCard.mediaBox?.(scene, zones, 'render', 'panel', fixes), boardGeneric.mediaBox?.(scene, zones, 'render', 'panel', fixes));
  run.done();
});

test('texOf: the $...$ spans of the term, the meaning and the example rows, inline, in that order', () => {
  const scene = /** @type {any} */ ({ board: [
    { id: 'd1', kind: 'definition', term: 'Ohm $R$', text: 'is $V/I$ for a fixed $R$' },
    { id: 'b1', kind: 'bullet', text: 'see $P = VI$ and a \\$5 note' },
  ] });
  assert.deepEqual(texOf(scene), [{ tex: 'R', display: false }, { tex: 'V/I', display: false },
    { tex: 'R', display: false }, { tex: 'P = VI', display: false }]);
  assert.deepEqual(definitionCard.tex?.(scene, null, 'portrait'), texOf(scene));
});

test('lineHeight: the type scale\'s Latin values, the Indic minimums for Tamil and Devanagari (plan §2)', () => {
  assert.equal(lineHeight('display', 'Resistance'), LINE_HEIGHT.display.latin);
  assert.equal(lineHeight('display', 'மின்தடை'), LINE_HEIGHT.display.indic);
  assert.equal(lineHeight('body', 'पतला तार अधिक रोकता है'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('body', '**thin** [[wire]]'), LINE_HEIGHT.body.latin);
  const scale = CATALOGUE.type.scale;
  assert.equal(LINE_HEIGHT.display.latin, scale.display.line_height.latin[1]);
  assert.equal(LINE_HEIGHT.display.indic, scale.display.line_height.indic[0]);
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0]);
  assert.equal(LINE_HEIGHT.body.indic, scale.body.line_height.indic[0], 'plan §2: Indic body >= 1.45');
});

test('sizes keep R-29: nothing below the format\'s text floor, 9:16 text at <= 64 px at least 600', () => {
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    const floors = TYPE_FLOORS[fmt];
    for (const [slot, range, weight] of /** @type {const} */ ([['term', L.term, 700], ['meaning', L.meaning, L.meaningWeight], ['example', L.example, L.exampleWeight]])) {
      assert.ok(range.min >= floors.text_min, `${fmt} ${slot}: ${range.min} >= ${floors.text_min}`);
      assert.ok(range.max >= range.min);
      if (floors.heavy_max_px && range.min <= floors.heavy_max_px) {
        assert.ok(weight >= /** @type {number} */ (floors.heavy_min_weight), `${fmt} ${slot}: R-29 weight`);
      }
    }
  }
  assert.deepEqual(LAYOUT.landscape.term, { max: TYPE_FLOORS.landscape.heading?.[1], min: TYPE_FLOORS.landscape.heading?.[0], lines: 2, prefer: 1 }, 'R-29 headings 84-120');
  assert.deepEqual([LAYOUT.landscape.meaning.min, LAYOUT.landscape.meaning.max], TYPE_FLOORS.landscape.body, 'R-29 16:9 body 48-56');
  assert.deepEqual([LAYOUT.portrait.meaning.min, LAYOUT.portrait.meaning.max], TYPE_FLOORS.portrait.body, 'R-29 9:16 body 56-64');
  assert.equal(BORDER_PX, LAYOUT_TOKENS.border.card);
  assert.equal(SIZE_STEP, 4);
});

/**
 * preflight.py inside_bands: a box lies inside the union of the critical boxes, each box it overlaps holding its whole
 * width (a 9:16 card may cross y 840, where the upper box ends and the lower begins).
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

test('geometry: the card regions are the named regions, its text stays in the critical boxes, its outline fits the shapes area', () => {
  const land = ZONES.landscape;
  assert.deepEqual(boxes('landscape', 'centred', land).card, { x0: 442, y0: 54, x1: 1478, y1: 912 }, 'centre-60');
  assert.deepEqual(boxes('landscape', 'card_icon_right', land).card, { x0: 96, y0: 54, x1: 1824, y1: 912 }, 'full');
  const split = boxes('landscape', 'card_image_left', land);
  assert.deepEqual([split.card.x0, split.card.x1], [1058 + LAYOUT.landscape.gutter, 1824], 'right-45 less the gutter');
  assert.deepEqual([split.media?.x0, split.media?.x1], [96, 1034], 'left-55');
  const port = boxes('portrait', 'centred', ZONES.portrait);
  assert.deepEqual(port.card, { x0: 192 - LAYOUT.portrait.pad, y0: 340, x1: 780 + LAYOUT.portrait.pad, y1: 1020 },
    'the 9:16 text column grown by the card\'s padding');
  assert.equal(port.media, null);
  for (const [fmt, variants] of /** @type {const} */ ([['landscape', VARIANTS], ['portrait', ['centred']]])) {
    const zones = ZONES[fmt];
    for (const variant of variants) {
      const b = boxes(fmt, variant, zones);
      const L = LAYOUT[fmt];
      const text = { x: b.card.x0 + L.pad, y: b.card.y0 + L.pad, w: textWidth(fmt, variant, b.card), h: 120 };
      assert.ok(insideBands(text, zones.critical), `${fmt} ${variant}: the card's text inside the critical area (R-59)`);
      // the card is a shape: its outline is drawn round it, never across it (cues.js shapeArea)
      const card = { x: b.card.x0, y: b.card.y0, w: b.card.x1 - b.card.x0, h: 200 };
      const free = /** @type {any} */ (outlinePath({ rect: card, shape: 'rect', radius: 24 }, null)).box;
      const clamped = /** @type {any} */ (outlinePath({ rect: card, shape: 'rect', radius: 24 }, shapeArea(zones))).box;
      assert.deepEqual([clamped.x0, clamped.x1], [free.x0, free.x1], `${fmt} ${variant}: the outline is not clamped into the card`);
    }
  }
});

test('slotRows: the slot table templates.py mirrors (widths from the boxes and the icon column)', () => {
  const rows = slotRows(ZONES.landscape, ZONES.portrait);
  const L = LAYOUT.landscape;
  assert.equal(rows.term.landscape.centred.w, 1036 - 2 * L.pad);
  assert.equal(rows.term.landscape.card_icon_right.w, 1728 - 2 * L.pad - L.icon - L.iconGap);
  assert.equal(rows.term.landscape.card_image_left.w, 1824 - 1058 - L.gutter - 2 * L.pad);
  assert.equal(rows.term.landscape.default.w, rows.term.landscape.card_image_left.w, 'the default is the narrowest variant');
  assert.ok(rows.term.landscape.default.w < rows.term.landscape.centred.w);
  assert.equal(rows.term.portrait.default.w, 588, 'the 9:16 text column itself (the card is the column plus its padding)');
  assert.equal(rows.example.portrait.default.w, 588 - LAYOUT.portrait.marker, 'an example row starts after its marker');
  assert.equal(rows.example.landscape.centred.w, 1036 - 2 * L.pad - L.marker);
  assert.deepEqual(rows.term.landscape.centred.size, [L.term.min, L.term.max]);
  assert.deepEqual([rows.term.landscape.centred.role, rows.term.landscape.centred.face, rows.term.landscape.centred.weight], ['title', 'display', 700]);
  assert.equal(rows.term.landscape.centred.tracking_em, TERM_TRACKING_EM);
  assert.equal(rows.term.landscape.centred.prefer_lines, 1);
  assert.deepEqual([rows.meaning.portrait.default.size, rows.meaning.portrait.default.weight], [[56, 64], 600]);
  assert.deepEqual([rows.term.portrait.default.size, rows.term.portrait.default.max_lines], [[56, 88], 2]);
  assert.equal(rows.meaning.landscape.centred.max_lines, null, 'the meaning and the rows have no line cap of their own');
  assert.equal(rows.example.portrait.default.max_lines, null);
  assert.deepEqual(Object.keys(rows).sort(), ['example', 'meaning', 'term']);
  assert.deepEqual(Object.keys(rows.term.landscape).sort(), ['card_icon_right', 'card_image_left', 'centred', 'default']);
  assert.deepEqual(Object.keys(rows.term.portrait), ['default']);
});

test('the catalogue\'s definition_card slots are slotRows exactly, once templates.py lists them (implemented)', () => {
  const spec = /** @type {any} */ (CATALOGUE.templates).definition_card;
  const cat = /** @type {any} */ (templateSlots('definition_card'));
  const want = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  if (!spec.implemented) {
    // the integrator's step: templates.py `implemented=True, slots=_definition_card_slots()` and a catalogue rebuild
    // (test_templates.py keeps an unimplemented template's slot table empty)
    assert.deepEqual(cat, {}, 'an unimplemented template has no slots yet');
    return;
  }
  for (const slot of Object.keys(want)) {
    for (const fmt of Object.keys(want[slot])) {
      for (const variant of Object.keys(want[slot][fmt])) {
        const got = cat[slot][fmt][variant];
        assert.deepEqual({
          w: got.w, max_lines: got.max_lines, size: got.size, role: got.role, face: got.face, weight: got.weight,
          line_height: got.line_height, tracking_em: got.tracking_em, prefer_lines: got.prefer_lines,
        }, want[slot][fmt][variant], `${slot} ${fmt} ${variant}`);
      }
    }
  }
  assert.deepEqual(Object.keys(cat).sort(), Object.keys(want).sort());
  assert.deepEqual(spec.consumes.forms.cue_term.includes('item'), true);
});

test('mediaBox: the 16:9 left 55% for the picture beside the card; none in 9:16 or without a picture', () => {
  const tl = load('definition_card.landscape.light.json');
  const scene = tl.scenes[0];
  const fixes = { safe: false, slots: {} };
  assert.deepEqual(definitionCard.mediaBox?.(scene, ZONES.landscape, 'render', 'panel', fixes), { w: 1034 - 96, h: 912 - 54 });
  // the fixes never enlarge the box (a remedy only caps a size or holds a slot at its minimum)
  assert.deepEqual(definitionCard.mediaBox?.(scene, ZONES.landscape, 'render', 'panel', { safe: true, slots: { term: { size: 84, scrim: true } } }), { w: 938, h: 858 });
  // in 9:16 such a scene is board_generic's (drawsScene), so the box is board_generic's own
  assert.deepEqual(definitionCard.mediaBox?.(scene, ZONES.portrait, 'render', 'panel', fixes),
    boardGeneric.mediaBox?.(scene, ZONES.portrait, 'render', 'panel', fixes));
  const typical = load('definition_card.landscape.json').scenes[0];
  assert.equal(definitionCard.mediaBox?.(typical, ZONES.landscape, 'render', 'main', fixes), null, 'no picture: no box');
  assert.equal(definitionCard.mediaBox?.(typical, ZONES.portrait, 'render', 'main', fixes), null, '9:16 draws no picture');
});

// --- painted behaviour ----------------------------------------------------------------------------------------------

test('the card rises 24 px and fades in over 400 ms in the personality\'s time, on whole pixels (§4.3, §P)', async () => {
  const tl = load('definition_card.landscape.json'); // Soft: x1.15
  const scale = tl.presentation.motion_scale;
  const s = spyStage(tl);
  await s.seekTo(0, 0.1);
  let v = s.ctx(0).view;
  assert.equal(v.card.style.visibility, 'visible');
  assert.ok(op(v.card.style.opacity) > 0 && op(v.card.style.opacity) < 1);
  assert.match(v.card.style.transform, /^translate\(0, \d+px\)$/);
  const dy = Number(/translate\(0, (\d+)px/.exec(v.card.style.transform)?.[1]);
  assert.ok(dy > 0 && dy <= CARD_RISE, `a whole-px rise (${dy})`);
  await s.seekTo(0, CARD_ENTER * scale + 0.05);
  v = s.ctx(0).view;
  assert.deepEqual([op(v.card.style.opacity), v.card.style.transform], [1, 'none'], 'in after 400 ms x the scale');
  // the card's look: surface, the card radius, the structure border down its left edge (§4.2: structure only there)
  assert.equal(v.card.style.background, rgb(s.ctx(0).roles.color('surface')));
  assert.equal(v.card.style.borderRadius, `${LAYOUT_TOKENS.radius.card}px`);
  assert.match(v.card.style.borderLeft, new RegExp(`^${BORDER_PX}px solid `));
  assert.equal(v.card.style.borderLeftColor, rgb(s.ctx(0).roles.color('struct.border')));
  s.done();
});

test('the term enters on the definition\'s beat (120 ms before it) and the meaning follows, word by word (§4.3)', async () => {
  const tl = load('definition_card.landscape.json'); // Soft: fade_rise, so the words stagger
  const scene = tl.scenes[0];
  const scale = tl.presentation.motion_scale;
  const start = enterOf(scene, 'd1');
  assert.ok(Math.abs(start - (scene.beats[0].start - 0.12)) < 1e-9, 'R-17: 120 ms before its beat');
  const s = spyStage(tl);
  await s.seekTo(0, start - 0.1);
  let v = s.ctx(0).view;
  assert.equal(op(v.term.el.style.opacity), 0, 'not yet in');
  await s.seekTo(0, start + MEANING_DELAY + 0.06);
  v = s.ctx(0).view;
  assert.ok(op(v.term.el.style.opacity) > 0, 'the term is entering');
  assert.equal(v.term.mask, null, 'Soft fades and rises, it does not reveal from the baseline');
  const words = v.meaning.words;
  const first = op(words[0].style.opacity);
  const last = op(words.at(-1).style.opacity);
  assert.ok(first > 0 && last < first, `the words land one after another (${first} then ${last})`);
  // every word on whole pixels, the body text's 16 px rise
  assert.match(words[0].style.transform, /^(none|translate\(0, \d+px\))$/);
  await s.seekTo(0, start + MEANING_DELAY * scale + (v.meaning.words.length - 1) * WORD_STAGGER * scale + TEXT_ENTER * scale + 0.05);
  v = s.ctx(0).view;
  assert.deepEqual(v.meaning.words.map((/** @type {HTMLElement} */ w) => op(w.style.opacity)), v.meaning.words.map(() => 1), 'all in');
  assert.equal(v.meaning.words.at(-1).style.transform, 'none');
  assert.ok(TEXT_RISE < CARD_RISE, 'the card\'s rise is its own, not the text rise');
  s.done();
});

test('Crisp reveals the term and the meaning from their baseline inside a mask (the personality\'s entrance, §4.8)', async () => {
  const tl = load('definition_card.portrait.json');
  assert.equal(tl.presentation.text_entrance, 'baseline_reveal');
  const s = spyStage(tl);
  await s.seekTo(0, enterOf(tl.scenes[0], 'd1') + 0.15);
  const v = s.ctx(0).view;
  assert.ok(v.term.mask && v.term.el.parentElement === v.term.mask && v.term.mask.classList.contains('dyn-reveal-mask'));
  assert.equal(v.term.el.style.opacity, '1', 'a baseline reveal moves, it does not fade');
  assert.ok(v.meaning.mask, 'the meaning is revealed as one block (a per-word mask cannot keep the inline flow)');
  assert.equal(v.meaning.words[0].style.opacity, '', 'no word of its own is painted then');
  s.done();
});

test('an example row enters on its own beat or reveal event, with its marker dot in ink2', async () => {
  const tl = load('definition_card.landscape.json');
  const scene = tl.scenes[0];
  const reveal = scene.events.find((/** @type {any} */ e) => e.kind === 'reveal');
  assert.equal(enterOf(scene, 'b1'), reveal.t, 'the reveal event wins over the beat (state.js itemTimings)');
  const s = spyStage(tl);
  await s.seekTo(0, reveal.t - 0.2);
  let row = s.ctx(0).view.examples[0];
  assert.equal(row.box.style.visibility, 'hidden');
  await s.seekTo(0, reveal.t + 0.3);
  row = s.ctx(0).view.examples[0];
  assert.equal(row.box.style.visibility, 'visible');
  assert.ok(op(row.el.style.opacity) > 0);
  const dot = /** @type {HTMLElement} */ (row.box.querySelector('.dyn-dc-dot'));
  assert.equal(dot.style.background, rgb(s.ctx(0).roles.color('ink2')));
  assert.equal(row.box.style.color, rgb(s.ctx(0).roles.color('ink2')), 'the example is the subordinate line');
  s.done();
});

test('card_icon_right: the item\'s stored icon in its own dim group, beside the text in 16:9 and above it in 9:16', async () => {
  const land = spyStage(load('definition_card.landscape.json'));
  await land.seekTo(0, 2);
  let v = land.ctx(0).view;
  assert.ok(v.iconEl && v.iconGroup && v.iconEl.parentElement === v.iconGroup);
  assert.equal(v.iconEl.style.width, `${LAYOUT_TOKENS.icon.feature_px}px`, '16:9: the feature icon (§4.12)');
  assert.equal(v.iconEl.querySelector('svg')?.getAttribute('stroke'), 'currentColor', 'drawn in the content ink');
  assert.equal(v.iconEl.style.color, rgb(land.ctx(0).roles.color('ink')));
  assert.equal(v.inner.style.flexDirection, 'row');
  assert.equal(v.inner.lastElementChild, v.iconGroup, 'the icon column on the right');
  assert.ok(definitionCard.dimGroups?.(land.ctx(0)).includes(v.iconGroup));
  land.done();
  const port = spyStage(load('definition_card.portrait.json'));
  await port.seekTo(0, 2);
  v = port.ctx(0).view;
  assert.equal(v.iconEl.style.width, `${LAYOUT_TOKENS.icon.card_px}px`, '9:16: the card icon');
  assert.equal(v.inner.style.flexDirection, 'column');
  assert.equal(v.inner.firstElementChild, v.iconGroup, 'above the term');
  // it enters with the term
  const start = enterOf(port.ctx(0).scene, 'd1');
  await port.seekTo(0, start - 0.1);
  assert.equal(port.ctx(0).view.iconEl.style.visibility, 'hidden');
  await port.seekTo(0, start + ICON_ENTER * port.ctx(0).motion.scale + 0.05);
  assert.deepEqual([op(port.ctx(0).view.iconEl.style.opacity), port.ctx(0).view.iconEl.style.transform], [1, 'none']);
  port.done();
});

test('card_image_left: the picture contained in the left 55%, fading in and scaling 0.96 -> 1 about its centre', async () => {
  const tl = load('definition_card.landscape.light.json');
  const s = spyStage(tl);
  await s.seekTo(0, 0.2);
  let vis = s.ctx(0).view.visual;
  assert.ok(vis && vis.frame.contains(vis.view.el));
  assert.equal(vis.frame.style.borderRadius, `${LAYOUT_TOKENS.radius.media}px`);
  const left = Number.parseFloat(vis.frame.style.left);
  const width = Number.parseFloat(vis.frame.style.width);
  assert.ok(left >= 96 && left + width <= 1034, 'inside the left 55%');
  assert.ok(Math.abs(width / Number.parseFloat(vis.frame.style.height) - 1600 / 900) < 0.005, 'contained at its aspect');
  assert.match(vis.frame.style.transform, /^scale\(0\.9\d+\)$/);
  assert.ok(Number(vis.frame.style.opacity) < 1);
  await s.seekTo(0, MEDIA_ENTER * tl.presentation.motion_scale + 0.3);
  vis = s.ctx(0).view.visual;
  assert.deepEqual([op(vis.frame.style.opacity), vis.frame.style.transform], [1, 'none']);
  assert.ok(MEDIA_FROM < 1);
  // the card is on the other side, inside the right 45%
  assert.ok(Number.parseFloat(s.ctx(0).view.card.style.left) >= 1058);
  s.done();
});

test('a clip is never scaled, and a medium this mode cannot show keeps its box with the panel\'s title', async () => {
  const tl = load('definition_card.landscape.light.json');
  tl.scenes[0].side_panel.media = { kind: 'video', asset_key: 'dc-clip', url: '/media/clip.mp4', duration: 20, width: 1600, height: 900, fit: 'contain', end_behavior: 'freeze' };
  let s = spyStage(tl);
  await s.seekTo(0, 0.2);
  assert.equal(s.ctx(0).view.visual.clip, true);
  assert.equal(s.ctx(0).view.visual.frame.style.transform, 'none', 'a clip only fades (B2c deviation 28)');
  s.done();
  const gif = load('definition_card.landscape.light.json');
  gif.scenes[0].side_panel = { panel: { kind: 'gif', title: 'A spark' }, media: { kind: 'gif', url: '/media/spark.gif' }, show_at: 0 };
  s = spyStage(gif);
  await s.seekTo(0, 2);
  const vis = s.ctx(0).view.visual;
  assert.ok(vis.missing && vis.missing.textContent === 'A spark');
  assert.equal(vis.frame.style.background, rgb(s.ctx(0).roles.color('surface')), 'the title on the card surface, never an empty box');
  const label = /** @type {any[]} */ (definitionCard.texts?.(s.ctx(0))).find((e) => e.role === 'label');
  assert.deepEqual([label.critical, label.size], [false, MISSING_LABEL_PX.landscape]);
  s.done();
});

test('reduced motion (live player): the card, the words, the icon and the picture fade in place', async () => {
  const tl = load('definition_card.landscape.json');
  const s = spyStage(tl, { mode: 'live', reducedMotion: true });
  await s.seekTo(0, enterOf(tl.scenes[0], 'd1') + 0.1);
  const v = s.ctx(0).view;
  assert.equal(v.card.style.transform, 'none');
  assert.equal(v.term.el.style.transform, 'none');
  assert.equal(v.meaning.words[0].style.transform, 'none');
  assert.equal(v.iconEl.style.transform, 'none');
  assert.ok(op(v.card.style.opacity) > 0);
  s.done();
});

test('cue.term: the stage marks the definition\'s [[term]] through itemEl (both its fields, in the item\'s order)', async () => {
  const tl = load('definition_card.landscape.json');
  const cue = tl.scenes[0].events.find((/** @type {any} */ e) => e.kind === 'cue_term');
  const s = spyStage(tl);
  await s.seekTo(0, cue.t + 0.5);
  const ctx = s.ctx(0);
  const found = /** @type {Element[]} */ (definitionCard.itemEl?.(ctx, 'd1'));
  assert.deepEqual(found, [ctx.view.term.el, ctx.view.meaning.el], 'the term, then the meaning');
  const span = /** @type {HTMLElement} */ (ctx.view.meaning.el.querySelector('.rt-k'));
  assert.equal(span.textContent, 'opposes');
  assert.match(span.style.backgroundImage, /linear-gradient/, 'the stage drew the marker on the term');
  assert.equal(definitionCard.itemEl?.(ctx, 'b1'), ctx.view.examples[0].el, 'an example row is an item too');
  assert.equal(definitionCard.itemEl?.(ctx, 'nope'), null);
  // <item>:term:<n> picks the n-th term over the item's fields (cues.js termPieces)
  const two = load('definition_card.landscape.json');
  two.scenes[0].board[0].term = 'A [[resistor]]';
  two.scenes[0].board[0].text = 'how strongly it [[opposes]] charge';
  const s2 = spyStage(two);
  await s2.seekTo(0, 2);
  const target = 'd1:term:1';
  const pieces = termPieces(definitionCard, s2.ctx(0), /** @type {any} */ ({ kind: 'cue_term', target, parsed: parseTarget(target), start: 0, hold: 1, order: 0 }));
  assert.deepEqual(pieces.map((p) => p.span.textContent), ['opposes']);
  assert.deepEqual(pieces.map((p) => fx(p.to, 2)), ['1.00']);
  s.done();
  s2.done();
});

test('focusRect: the card for the definition (a shape), an example row\'s text, the picture for `panel`; whole px', async () => {
  const tl = load('definition_card.landscape.light.json');
  const s = spyStage(tl);
  await s.seekTo(0, 6);
  const real = s.ctx(0);
  const v = real.view;
  const rects = new Map([[v.card, { x: 1082.4, y: 300.6, w: 742.2, h: 420.4 }],
    [v.examples[0].box, { x: 1130, y: 600.2, w: 646, h: 90.6 }],
    [v.visual.frame, { x: 96, y: 150, w: 938, h: 528 }]]);
  const ctx = { ...real, rectOf: (/** @type {Element} */ el) => rects.get(el) || null };
  const card = /** @type {any} */ (definitionCard.focusRect?.(ctx, 'd1'));
  assert.deepEqual(card.rect, { x: 1082, y: 301, w: 743, h: 420 });
  assert.deepEqual([card.shape, card.radius, card.kind, card.el], ['rect', LAYOUT_TOKENS.radius.card, 'shape', v.textGroup]);
  assert.ok(['x', 'y', 'w', 'h'].every((k) => Number.isInteger(card.rect[k])));
  assert.deepEqual(/** @type {any} */ (definitionCard.focusRect?.(ctx, 'd1:term:0')).rect, card.rect, 'a term target points at its card');
  const row = /** @type {any} */ (definitionCard.focusRect?.(ctx, 'b1'));
  assert.deepEqual([row.kind, row.rect.y, row.rect.h], ['text', 600, 91]);
  const panel = /** @type {any} */ (definitionCard.focusRect?.(ctx, 'panel'));
  assert.deepEqual([panel.kind, panel.radius, panel.el], ['shape', LAYOUT_TOKENS.radius.media, v.visual.el]);
  assert.equal(definitionCard.focusRect?.(ctx, 'zz'), null);
  assert.equal(definitionCard.focusRect?.(ctx, 'option:0'), null, 'a form this template does not resolve');
  s.done();
});

test('dimGroups: the card\'s text, the icon and the picture; the same elements at every t, their opacity the stage\'s', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const s = spyStage(tl);
    await s.seekTo(0, 1);
    const ctx = s.ctx(0);
    const groups = /** @type {HTMLElement[]} */ (definitionCard.dimGroups?.(ctx));
    const want = [ctx.view.textGroup, ctx.view.iconGroup, ctx.view.visual ? ctx.view.visual.el : null].filter(Boolean);
    assert.deepEqual(groups, want, name);
    for (const g of groups) assert.equal(g.style.opacity, '', `${name}: the template never writes a group's opacity`);
    await s.seekTo(0, 3);
    assert.deepEqual(definitionCard.dimGroups?.(s.ctx(0)), groups, `${name}: the same groups at every t`);
    s.done();
  }
});

test('texts(): the term, the meaning and each example row, critical, on the card surface, at the size painted now', async () => {
  const tl = load('definition_card.landscape.json');
  const s = spyStage(tl);
  const scene = tl.scenes[0];
  await s.seekTo(0, enterOf(scene, 'b1') + 2);
  const ctx = s.ctx(0);
  const entries = /** @type {any[]} */ (definitionCard.texts?.(ctx));
  assert.deepEqual(entries.map((e) => e.slot), ['term', 'meaning', 'example']);
  const [term, meaning, example] = entries;
  assert.deepEqual([term.role, term.critical, term.weight, term.alpha], ['title', true, 700, 1]);
  assert.equal(term.text, 'Resistance');
  assert.equal(term.size, ctx.view.term.size);
  assert.equal(term.min, ctx.view.term.range.min);
  assert.deepEqual([meaning.role, meaning.weight], ['body', LAYOUT.landscape.meaningWeight]);
  assert.equal(meaning.text, 'how strongly a material opposes the flow of charge', 'plain text, the markup gone');
  assert.equal(example.ink.toLowerCase(), ctx.roles.color('ink2').toLowerCase());
  for (const e of entries) {
    assert.equal(e.critical, true);
    assert.match(String(e.ink), /^#[0-9a-f]{6}$/i);
    assert.equal(e.bg, ctx.roles.color('surface'), 'every text sits on the card');
    assert.ok(e.alpha >= 0 && e.alpha <= 1);
  }
  // before the card is in, nothing reads as settled
  await s.seekTo(0, 0.03);
  for (const e of /** @type {any[]} */ (definitionCard.texts?.(s.ctx(0)))) assert.ok(e.alpha < 1, e.slot);
  s.done();
});

test('preflight\'s remedies: a slot\'s size cap and the safe layout hold the text small (the scrim never applies here)', async () => {
  const tl = load('definition_card.landscape.json');
  tl.scenes[0].look.fixes = { safe: false, slots: { term: { size: 88, scrim: true }, meaning: { size: 48 } } };
  let s = spyStage(tl);
  await s.seekTo(0, 3);
  let v = s.ctx(0).view;
  assert.equal(v.term.size, 88, 'the cap (jsdom fits at the largest size allowed)');
  assert.equal(v.meaning.size, 48);
  assert.equal(v.card.style.background, rgb(s.ctx(0).roles.color('surface')), 'no scrim: the card is the surface');
  s.done();
  const safe = load('definition_card.portrait.json');
  safe.scenes[0].look.fixes = { safe: true, slots: {} };
  s = spyStage(safe);
  await s.seekTo(0, 3);
  v = s.ctx(0).view;
  assert.deepEqual([v.term.size, v.meaning.size], [LAYOUT.portrait.term.min, LAYOUT.portrait.meaning.min], 'safe: every slot at its minimum');
  s.done();
});

test('update writes every property on every frame with a value (no \'\': a cleared property would make the DOM order-dependent)', async () => {
  const proto = /** @type {any} */ (Painter.prototype);
  const real = proto.style;
  /** @type {string[]} */
  const empty = [];
  let inside = false;
  const wrapped = { ...definitionCard, update(/** @type {any} */ ctx, /** @type {number} */ t, /** @type {any} */ st) { inside = true; try { definitionCard.update(ctx, t, st); } finally { inside = false; } } };
  proto.style = function style(/** @type {any} */ el, /** @type {string} */ prop, /** @type {any} */ value) {
    if (inside && String(value) === '') empty.push(`${el.className} ${prop}`);
    return real.call(this, el, prop, value);
  };
  try {
    for (const name of FIXTURES) {
      const tl = load(name);
      const { stage, done } = renderStage(tl, { templates: { definition_card: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex });
      const total = Math.round(tl.total_duration * tl.fps);
      for (let f = 0; f < total; f += 7) await stage.seek(f);
      done();
    }
  } finally {
    proto.style = real;
  }
  assert.deepEqual([...new Set(empty)], []);
});

// --- the fit, on a fake text layout ---------------------------------------------------------------------------------

/**
 * Install a fake text layout on HTMLElement (jsdom has none): a glyph is 0.55 em wide, words wrap greedily, a word
 * wider than its line breaks across lines. Enough for this template's fit: the height of each text element (and of the
 * mask or row that holds it), a word's height, the text box's width. Returns the restore function.
 */
function fakeLayout() {
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const keys = ['offsetHeight', 'offsetWidth', 'clientWidth', 'offsetTop'];
  const saved = keys.map((k) => [k, Object.getOwnPropertyDescriptor(proto, k)]);
  const CW = 0.55;
  const TEXT = '.dyn-dc-term, .dyn-dc-meaning, .dyn-dc-example-text';
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
  /** The width of the box a text element wraps in: the card's text column, less an example row's marker. @param {Element} el */
  const boxOf = (el) => {
    const col = /** @type {HTMLElement | null} */ (el.closest('.dyn-dc-text'));
    const width = col ? Number.parseFloat(col.style.width) || 0 : 0;
    const row = el.closest('.dyn-dc-example');
    const portrait = !!el.closest('.dyn-stage--portrait');
    return width - (row ? LAYOUT[portrait ? 'portrait' : 'landscape'].marker : 0);
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
      if (el.matches(TEXT)) return lines(el, boxOf(el)) * lineH(el);
      const inner = /** @type {HTMLElement | null} */ (el.querySelector(TEXT));
      return inner && (el.classList.contains('dyn-reveal-mask') || el.classList.contains('dyn-dc-example'))
        ? lines(inner, boxOf(inner)) * lineH(inner) : 0;
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

/**
 * A one-scene timeline of `fmt` whose definition has `term` and `meaning`, with the example rows `rows`.
 * @param {'landscape' | 'portrait'} fmt @param {string} term @param {string} meaning
 * @param {{rows?: string[], variant?: string, icons?: boolean}} [opts]
 */
function cardTimeline(fmt, term, meaning, opts = {}) {
  const tl = load(fmt === 'portrait' ? 'definition_card.portrait.json' : 'definition_card.landscape.json');
  const scene = tl.scenes[0];
  const rows = opts.rows || [];
  scene.board = [{ id: 'd1', kind: 'definition', term, text: meaning },
    ...rows.map((text, i) => ({ id: `b${i}`, kind: 'bullet', text }))];
  scene.beats = scene.board.map((/** @type {any} */ item, /** @type {number} */ i) => ({
    beat_id: `k${i}`, index: i, phase: 'main', start: 0.6 + i * 1.5, speech_end: 1.8 + i * 1.5, end: 2 + i * 1.5,
    narration: 'x', board_item_id: item.id, captions: [],
  }));
  scene.events = [];
  scene.look.variant = opts.variant || 'centred';
  if (!opts.icons) scene.look.icons = {};
  return tl;
}

test('fit (16:9): the largest 4 px size whose text keeps its line cap; the card is its content, centred in its region', async () => {
  const restore = fakeLayout();
  try {
    const s = spyStage(cardTimeline('landscape', 'Resistance', 'how strongly a material opposes charge', { rows: ['A thin wire resists more'] }));
    await s.seekTo(0, 0.2);
    const v = s.ctx(0).view;
    const L = LAYOUT.landscape;
    assert.equal(v.term.size, L.term.max, '"Resistance" fits one line of the centred column at 120 px');
    assert.equal(v.meaning.size, L.meaning.max);
    assert.equal(v.examples[0].size, L.example.max);
    for (const p of v.parts) assert.equal(p.overflow, 0, p.slot);
    // the card: the stack plus its padding, centred in the centre-60 region
    const stack = v.parts.reduce((/** @type {number} */ a, /** @type {any} */ p, /** @type {number} */ i) => a + p.box.offsetHeight + (i ? (p.slot === 'term' || v.parts[i - 1].slot === 'term' ? L.termGap : L.rowGap) : 0), 0);
    const height = Math.round(stack + 2 * L.pad);
    assert.equal(v.card.style.height, `${height}px`);
    assert.equal(v.card.style.top, `${Math.round(54 + (912 - 54 - height) / 2)}px`);
    assert.equal(v.card.style.width, `${1478 - 442}px`);
    assert.equal(v.textCol.style.width, `${1036 - 2 * L.pad}px`);
    s.done();
    // a term too long for one line at 120 px takes the largest size that fits two (its cap), never a third
    const long = spyStage(cardTimeline('landscape', 'Electromagnetic induction in a closed loop of wire', 'short'));
    await long.seekTo(0, 0.2);
    const w = long.ctx(0).view;
    assert.ok(w.term.size < LAYOUT.landscape.term.max && w.term.size >= LAYOUT.landscape.term.min);
    assert.equal(w.term.size % SIZE_STEP, 0);
    long.done();
    // 16:9 card_icon_right: the icon stands beside the text, so a short card is never shorter than the icon
    const icon = spyStage(cardTimeline('landscape', 'Ohm', 'a short one', { variant: 'card_icon_right', icons: true }));
    await icon.seekTo(0, 0.2);
    const i = icon.ctx(0).view;
    assert.ok(i.iconEl);
    assert.ok(Number.parseFloat(i.card.style.height) >= LAYOUT.landscape.icon + 2 * L.pad,
      `the card holds the ${LAYOUT.landscape.icon} px icon (${i.card.style.height})`);
    icon.done();
  } finally {
    restore();
  }
});

test('fit: the stack shrinks in order (examples, meaning, term); what is still past the bottom is reported, never clipped', async () => {
  const restore = fakeLayout();
  try {
    const L = LAYOUT.landscape;
    const row = 'A thin wire resists far more than a thick one of the same material';
    // a stack that fits: nothing gives way, the term keeps its size, nothing overflows
    const fits = spyStage(cardTimeline('landscape', 'Resistance', 'how strongly a material opposes charge', { rows: [row, row] }));
    await fits.seekTo(0, 0.2);
    let v = fits.ctx(0).view;
    assert.equal(v.term.size, L.term.max, 'the headline keeps its size');
    for (const p of v.parts) assert.equal(p.overflow, 0, p.slot);
    fits.done();
    // far too much: the ladder runs through, and what is still past the bottom is reported (preflight blocks)
    const meaning = 'how strongly a material opposes the flow of electric charge through it, measured in ohms for a steady current';
    const over = spyStage(cardTimeline('landscape', 'Resistance', meaning, { rows: Array(6).fill(row) }));
    await over.seekTo(0, 0.2);
    v = over.ctx(0).view;
    // the ladder's order: a part is above its minimum only while everything before it is at its own
    if (v.meaning.size > v.meaning.range.min) assert.equal(v.examples[0].size, L.example.min, 'the examples give way first');
    if (v.term.size > v.term.range.min) {
      assert.equal(v.meaning.size, v.meaning.range.min, 'the meaning gives way before the term');
      assert.equal(v.examples[0].size, L.example.min);
    }
    assert.ok(v.parts.some((/** @type {any} */ p) => p.overflow > 0), 'the parts past the bottom report it ("Shorten text")');
    assert.equal(v.term.overflow, 0, 'the term, at the top of the stack, is not what overflows');
    const entries = /** @type {any[]} */ (definitionCard.texts?.(over.ctx(0)));
    assert.ok(entries.at(-1).overflow > 0, 'and texts() carries it to preflight');
    over.done();
  } finally {
    restore();
  }
});

test('fit (9:16): the column holds the card; its text keeps R-29\'s floors, and the icon leaves the stack less room', async () => {
  const restore = fakeLayout();
  try {
    const plain = spyStage(cardTimeline('portrait', 'மின்தடை', 'மின்னோட்டத்தைத் தடுக்கும் தன்மை', { rows: ['Thin wires resist more'] }));
    await plain.seekTo(0, 0.2);
    const v = plain.ctx(0).view;
    assert.equal(v.textCol.style.width, '588px', 'the 9:16 text column (x 192-780)');
    assert.equal(v.term.lh, LINE_HEIGHT.display.indic, 'Tamil takes the Indic line height');
    assert.ok(v.term.size >= LAYOUT.portrait.term.min && v.meaning.size >= LAYOUT.portrait.meaning.min);
    assert.equal(Number.parseFloat(v.card.style.left), 192 - LAYOUT.portrait.pad);
    plain.done();
    // the same card with the icon above the term: the stack has the icon's room less
    const icon = spyStage(cardTimeline('portrait', 'மின்தடை', 'மின்னோட்டத்தைத் தடுக்கும் தன்மை', { rows: ['Thin wires resist more'], variant: 'card_icon_right', icons: true }));
    await icon.seekTo(0, 0.2);
    const w = icon.ctx(0).view;
    assert.ok(w.iconEl, 'the icon is drawn');
    assert.ok(Number.parseFloat(w.card.style.height) > Number.parseFloat(v.card.style.height), 'the icon makes the card taller');
    assert.ok(w.meaning.size <= v.meaning.size, 'and leaves the stack less room');
    assert.ok(Number.parseFloat(w.card.style.height) <= 1020 - 340, 'the card stays inside its region');
    icon.done();
  } finally {
    restore();
  }
});

test('fit: a word wider than the column even at the floor costs the slot neither its size nor an overflow', async () => {
  const restore = fakeLayout();
  try {
    // the meaning and the example rows have no line cap of their own (LAYOUT lines null): the page breaks an over-wide
    // word, which costs lines but is reported only if the card then cannot hold the stack (preflight would block)
    assert.equal(LAYOUT.landscape.meaning.lines, null);
    assert.equal(LAYOUT.portrait.example.lines, null);
    const s = spyStage(cardTimeline('landscape', 'Resistance', 'Pneumonoultramicroscopicsilicovolcanoconiosis is unrelated'));
    await s.seekTo(0, 0.2);
    const v = s.ctx(0).view;
    assert.equal(v.meaning.size, LAYOUT.landscape.meaning.max);
    assert.deepEqual([v.meaning.lineOverflow, v.meaning.overflow], [0, 0], 'the broken word alone is no overflow');
    s.done();
    // the term does keep a cap (2 lines): a term is a few words, so a third line is a lint's business
    assert.equal(LAYOUT.landscape.term.lines, 2);
    assert.equal(LAYOUT.portrait.term.prefer, 1);
  } finally {
    restore();
  }
});
