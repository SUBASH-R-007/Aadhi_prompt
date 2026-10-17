// @ts-check
// templates/outro_next.js (DYNAMIC_VIDEO_DESIGN §4.3 outro_next, R-11, §2.3, §2.5 end-screen space; B2 plan §1.2
// refs.next, §1.4 template contract, §1.6 TemplateSpec, §1.11 slots, §P): the four fixture timelines
// (tests/compose/dynamic/make_outro_next_fixtures.py) through the harness's contract and order gates; the pure helpers
// (what the scene shows, drawsScene against the Python fit it must agree with, the layout plan in every mode, the
// end-screen half, the slot table, the call to action's end, TeX); the painted choreography in both formats (the
// kicker, the statement on its beat with the personality's entrance, the next card rising after it in 16:9, the call
// to action in and out within R-11's 3 s in 9:16, reduced motion); preflight's text entries and remedies; the fallback
// to board_generic; and the fit and the stack's shrink order on a fake text layout (jsdom has none).
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { Painter, TYPE_FLOORS, ZONES, fx, templateSlots } from '../../../js/dynamic/core.js';
import outroNext, {
  BOARD_TYPES, BORDER_PX, CARD_ENTER, CARD_RISE, CTA_DROP, CTA_EXIT, CTA_MAX, CTA_MIN, DEFAULT_VARIANT,
  END_SCREEN_VARIANT, KICKER_ENTER, LAYOUT, LINE_HEIGHT, LOOP_HOLD, NEXT_AFTER, NEXT_LABEL, SIZE_STEP, SLOTS,
  STATEMENT_KINDS, TEXT_ENTER, TRACKING_EM, VARIANTS, ctaEnd, drawsScene, endScreenBox, lineHeight, nextOf,
  outroText, plan, slotRows, texOf, textWidth, variantOf,
} from '../../../js/dynamic/templates/outro_next.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';

before(() => installDom());
after(() => uninstallDom());

const FIXTURES = ['outro_next.landscape.json', 'outro_next.landscape.endscreen.json', 'outro_next.portrait.json',
  'outro_next.portrait.hindi.json'];
const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const SEAM = { templates: { outro_next: outroNext } };

/**
 * A render stage of `tl` with outro_next through the stage's test seam, and the context each scene was built with.
 * @param {any} tl @param {{reducedMotion?: boolean, mode?: 'live' | 'render'}} [opts]
 */
function spyStage(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const contexts = new Map();
  const wrapped = { ...outroNext, build(/** @type {any} */ ctx) { contexts.set(ctx.index, ctx); return outroNext.build(ctx); } };
  const run = renderStage(tl, { templates: { outro_next: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex, ...opts });
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
/** @param {any} v @param {string} slot */
const partOf = (v, slot) => [...(v.textBlock ? v.textBlock.parts : []), ...(v.cardBlock ? v.cardBlock.parts : [])]
  .find((p) => p.slot === slot);

// --- the fixtures through the harness -------------------------------------------------------------------------------

test('every fixture keeps the template contract (harness: dim groups, no banned writes, TeX declared, text entries)', async () => {
  for (const name of FIXTURES) assert.deepEqual(await contractProblems(outroNext, load(name)), [], name);
});

test('every fixture paints the same frame forward, in reverse and on a fresh page (a frame is a pure function of t)', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const total = Math.round(tl.total_duration * tl.fps);
    const frames = new Set();
    for (let f = 0; f < Math.min(total, 330); f += 6) frames.add(f);
    // the frames around each scene's start (its crossfade) and the choreography's moments: the statement's beat, the
    // card's rise, the call to action's exit and the scene's last frame
    for (const s of tl.scenes) {
      const marks = [0, 0.1, 0.3, 0.6, 0.9, 1.2, 1.6, 2.0, 2.4, 2.7, 3.0, s.duration - 0.05];
      for (const e of s.events || []) marks.push(e.t, e.t + 0.2, e.t + 0.5);
      for (const d of marks) frames.add(Math.min(total - 1, Math.max(0, Math.round((s.start + d) * tl.fps))));
    }
    assert.deepEqual(await orderProblems(tl, [...frames].sort((a, b) => a - b), SEAM), [], name);
  }
});

test('the fixtures cover both formats, a dark and a light family, both entrances, every layout and script', () => {
  const seen = { formats: new Set(), modes: new Set(), entrances: new Set(), layouts: new Set(), cases: new Set(), scripts: new Set() };
  for (const name of FIXTURES) {
    const tl = load(name);
    const fmt = tl.presentation.format;
    seen.formats.add(fmt);
    seen.modes.add(tl.presentation.mode);
    seen.entrances.add(tl.presentation.text_entrance);
    for (const s of tl.scenes) {
      const text = outroText(s, s.look);
      seen.layouts.add(plan(fmt, variantOf(s.look, fmt), { text: !!text.statement, card: !!text.next }, ZONES[fmt]).mode);
      seen.cases.add(text.next ? 'next' : 'no-next');
      seen.cases.add(text.kicker ? 'kicker' : 'no-kicker');
      seen.cases.add(text.item ? 'item' : 'title-statement');
      if (texOf(s, s.look).length) seen.cases.add('tex');
      if (/\p{Script=Tamil}/u.test(text.statement)) seen.scripts.add('tamil');
      if (/\p{Script=Devanagari}/u.test(text.statement)) seen.scripts.add('devanagari');
      if (/^[\x20-\x7e]+$/.test(text.statement)) seen.scripts.add('latin');
      assert.equal(drawsScene(s, fmt), true, `${name} ${s.scene_id}: the template draws every fixture scene`);
    }
  }
  assert.deepEqual([...seen.formats].sort(), ['landscape', 'portrait']);
  assert.deepEqual([...seen.modes].sort(), ['dark', 'light']);
  assert.deepEqual([...seen.entrances].sort(), ['baseline_reveal', 'fade_rise']);
  assert.deepEqual([...seen.layouts].sort(), ['centred', 'end_screen', 'reel', 'split']);
  assert.deepEqual([...seen.cases].sort(), ['item', 'kicker', 'next', 'no-kicker', 'no-next', 'tex', 'title-statement']);
  assert.deepEqual([...seen.scripts].sort(), ['devanagari', 'latin', 'tamil']);
});

// --- pure helpers ---------------------------------------------------------------------------------------------------

test('outroText: the item is the statement under the scene title; the title alone is the statement; refs.next is the step', () => {
  const look = { refs: { next: { title: '  Part 2: Series circuits ' } } };
  const one = outroText(/** @type {any} */ ({ title: 'What we learned', board: [{ id: 'i1', kind: 'takeaway', text: ' V = IR ties them. ' }] }), look);
  assert.deepEqual({ ...one, item: one.item && one.item.id }, { item: 'i1', statement: 'V = IR ties them.', kicker: 'What we learned', next: 'Part 2: Series circuits' });
  // the title alone is the statement (no kicker); no refs.next -> no step
  assert.deepEqual(outroText(/** @type {any} */ ({ title: 'Keep the current flowing', board: [] }), null),
    { item: null, statement: 'Keep the current flowing', kicker: '', next: '' });
  // a title repeating the item is drawn once (case and spacing ignored, rich-lite markup read as plain text)
  const same = outroText(/** @type {any} */ ({ title: 'KEEP  it  flowing', board: [{ id: 'i1', kind: 'heading', text: '**Keep** it flowing' }] }), null);
  assert.equal(same.kicker, '');
  assert.equal(same.statement, '**Keep** it flowing');
  // an item with no text is no statement: the title takes its place
  assert.equal(outroText(/** @type {any} */ ({ title: 'T', board: [{ id: 'i1', kind: 'paragraph', text: '  ' }] }), null).statement, 'T');
});

test('nextOf: refs.next.title, trimmed; \'\' without one or when it is malformed', () => {
  assert.equal(nextOf(null), '');
  assert.equal(nextOf({}), '');
  assert.equal(nextOf({ refs: { next: null } }), '');
  assert.equal(nextOf({ refs: { next: { title: 7 } } }), '');
  assert.equal(nextOf({ refs: { next: { title: ' Part 3 ' } } }), 'Part 3');
});

/**
 * templates.py `_fit_outro_next` (the version the integrator registers with this template; outro_next's `requests`):
 * a BoardScene, no side panel, at most one item of STATEMENT_KINDS, and the item's text or the scene title to show.
 * The Python test tests/compose/dynamic/test_outro_next_fixtures.py runs the same cases through the real fit and this
 * module's drawsScene in node once the template is registered; here the mirror pins the direction that matters
 * (fit > 0 implies drawsScene, B2c review CG-2) and drawsScene's own answers.
 * @param {any} scene
 */
function pythonFit(scene) {
  if (!BOARD_TYPES.includes(scene.type) || scene.side_panel) return false;
  const board = scene.board || [];
  if (board.length > 1 || board.some((/** @type {any} */ i) => !STATEMENT_KINDS.includes(i.kind))) return false;
  return !!(String(scene.title || '').trim() || board.some((/** @type {any} */ i) => String(i.text || '').trim()));
}

test('drawsScene: a board scene with at most one takeaway / paragraph / heading and something to show; fit > 0 implies it', () => {
  const panel = { panel: { kind: 'image', title: 'x' }, media: null, show_at: 0 };
  const T = (/** @type {string} */ kind, text = 'A closing line') => ({ id: 'i1', kind, text });
  /** @type {[string, any, boolean][]} [case, scene, drawsScene] */
  const cases = [
    ['takeaway', { type: 'summary', title: 'T', board: [T('takeaway')] }, true],
    ['paragraph', { type: 'content', title: 'T', board: [T('paragraph')] }, true],
    ['heading', { type: 'key_takeaway', title: '', board: [T('heading')] }, true],
    ['title only', { type: 'summary', title: 'Keep going', board: [] }, true],
    ['next only', { type: 'summary', title: '', board: [], look: { refs: { next: { title: 'Part 2' } } } }, true],
    ['nothing', { type: 'summary', title: ' ', board: [] }, false],
    ['empty item, no title', { type: 'summary', title: '', board: [T('takeaway', ' ')] }, false],
    ['two items', { type: 'summary', title: 'T', board: [T('takeaway'), { id: 'i2', kind: 'paragraph', text: 'x' }] }, false],
    ['a bullet', { type: 'summary', title: 'T', board: [T('bullet')] }, false],
    ['a table', { type: 'summary', title: 'T', board: [{ id: 'i1', kind: 'table', text: '' }] }, false],
    ['a panel', { type: 'summary', title: 'T', board: [T('takeaway')], side_panel: panel }, false],
    ['a hidden panel', { type: 'summary', title: 'T', board: [T('takeaway')], side_panel: panel, layout: { show_side_panel: false } }, true],
    // a medium exists only on a simulation / ai_video scene (compose/timeline.py _main_media), never on a board scene
    ['a medium', { type: 'simulation', title: 'T', board: [], media: { kind: 'video', asset_key: 'a' } }, false],
    ['a quiz', { type: 'quiz_checkpoint', title: 'T', board: [], quiz: { question: 'q' } }, false],
    ['a chapter card', { type: 'chapter_card', title: 'T', board: [] }, false],
    ['an ai_video fallback board', { type: 'ai_video', title: 'T', board: [T('paragraph')] }, false],
  ];
  for (const [name, scene, want] of cases) {
    for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
      assert.equal(drawsScene(scene, fmt), want, `${name} ${fmt}`);
      if (pythonFit(scene)) assert.equal(drawsScene(scene, fmt), true, `${name} ${fmt}: fit accepts it, so the page must draw it`);
    }
  }
  for (const type of BOARD_TYPES) assert.equal(drawsScene(/** @type {any} */ ({ type, title: 'T', board: [] }), 'landscape'), true, type);
});

test('variantOf: 16:9 next_up unless the look names end_screen; 9:16 has no variant', () => {
  assert.deepEqual([...VARIANTS], ['next_up', 'end_screen']);
  assert.equal(DEFAULT_VARIANT, 'next_up');
  assert.equal(variantOf(null, 'landscape'), 'next_up');
  assert.equal(variantOf({ variant: 'safe' }, 'landscape'), 'next_up', 'B1\'s legacy value is not a variant');
  assert.equal(variantOf({ variant: 'end_screen' }, 'landscape'), 'end_screen');
  assert.equal(variantOf({ variant: 'end_screen' }, 'portrait'), '', '§2.5: end-screen space is 16:9 only');
});

test('endScreenBox is the catalogue\'s end-screen half (zones.py END_SCREEN), read from the stage\'s own zones', () => {
  const cat = /** @type {any} */ (CATALOGUE).end_screen.landscape.box;
  assert.deepEqual(endScreenBox(ZONES.landscape), { x0: cat.x0, y0: cat.y0, x1: cat.x1, y1: cat.y1 });
});

/**
 * preflight.py inside_bands: a box lies inside the union of the critical boxes, and every box its vertical extent
 * overlaps holds its whole width.
 * @param {{x0: number, y0: number, x1: number, y1: number}} b
 * @param {{x0: number, y0: number, x1: number, y1: number}[]} bands
 */
function insideBands(b, bands) {
  let covered = 0;
  for (const band of bands) {
    const overlap = Math.min(b.y1, band.y1) - Math.max(b.y0, band.y0);
    if (overlap <= 1) continue;
    if (b.x0 < band.x0 - 1 || b.x1 > band.x1 + 1) return false;
    covered += overlap;
  }
  return covered >= (b.y1 - b.y0) - 1;
}

test('plan: every layout\'s text sits inside the critical area, clear of the caption band, on whole px', () => {
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const zones = ZONES[fmt];
    for (const variant of fmt === 'landscape' ? VARIANTS : ['']) {
      for (const shows of [{ text: true, card: true }, { text: true, card: false }, { text: false, card: true }]) {
        const p = plan(fmt, variant, shows, zones);
        assert.equal(p.columns.flatMap((c) => c.blocks).sort().join(), Object.entries(shows).filter(([, v]) => v).map(([k]) => k).sort().join(),
          `${fmt} ${variant} ${JSON.stringify(shows)}: every block placed once`);
        for (const col of p.columns) {
          const b = col.box;
          assert.ok([b.x0, b.y0, b.x1, b.y1].every(Number.isInteger), `${fmt} ${p.mode}: whole px`);
          for (const block of col.blocks) {
            // the text's own box: the column (text block) or the card less its padding and border
            const pad = block === 'card' ? LAYOUT[fmt].pad : 0;
            const text = { x0: b.x0 + pad + (block === 'card' ? BORDER_PX : 0), y0: b.y0 + pad, x1: b.x1 - pad, y1: b.y1 - pad };
            assert.equal(text.x1 - text.x0, textWidth(fmt, b, block), `${fmt} ${p.mode} ${block}: textWidth`);
            assert.ok(insideBands(text, zones.critical), `${fmt} ${p.mode} ${block}: inside the critical area (R-59)`);
            assert.ok(b.y1 <= zones.caption.y0, `${fmt} ${p.mode} ${block}: above the caption band`);
          }
        }
      }
    }
  }
  // §4.3: takeaway left, "Next up" right; a lone block in the centre
  const split = plan('landscape', 'next_up', { text: true, card: true }, ZONES.landscape);
  assert.equal(split.mode, 'split');
  assert.deepEqual(split.columns.map((c) => [c.blocks[0], c.box.x0, c.box.x1]), [['text', 96, 1034], ['card', 1058, 1824]]);
  assert.equal(plan('landscape', 'next_up', { text: true, card: false }, ZONES.landscape).columns[0].box.x0, 442, 'centre-60');
  // §2.5: with end-screen space nothing is set in the right half (the column ends a gutter before it)
  const end = plan('landscape', 'end_screen', { text: true, card: true }, ZONES.landscape);
  assert.equal(end.columns.length, 1);
  assert.deepEqual(end.columns[0].blocks, ['text', 'card'], 'the next card below the statement');
  assert.equal(end.columns[0].box.x1, endScreenBox(ZONES.landscape).x0 - LAYOUT.landscape.gutter);
  // 9:16: the loop line in the hook band, the call to action below it with its text exactly in the text column
  const reel = plan('portrait', '', { text: true, card: true }, ZONES.portrait);
  assert.deepEqual(reel.columns[0].box, { x0: 192, y0: 340, x1: 888, y1: 760 }, 'the hook band (§2.5)');
  const card = reel.columns[1].box;
  assert.equal(card.x0 + LAYOUT.portrait.pad + BORDER_PX, ZONES.portrait.textColumn.x0);
  assert.equal(card.x1 - LAYOUT.portrait.pad, ZONES.portrait.textColumn.x1);
  assert.ok(card.y0 > reel.columns[0].box.y1, 'below the loop line, never over it');
});

test('the boxes hold the most each layout can set: the 9:16 card holds the label and two next lines at the largest size', () => {
  const P = LAYOUT.portrait;
  const reel = plan('portrait', '', { text: true, card: true }, ZONES.portrait);
  const card = reel.columns[1].box;
  const inner = card.y1 - card.y0 - 2 * P.pad;
  // Latin at the slot maximum; Indic at the slot minimum (fitInBox steps the size down until the height fits)
  assert.ok(P.label.max * LINE_HEIGHT.body.latin + P.labelGap + P.next.lines * P.next.max * LINE_HEIGHT.display.latin <= inner,
    'Latin: two 64 px lines');
  assert.ok(P.label.max * LINE_HEIGHT.body.indic + P.labelGap + P.next.lines * P.next.min * LINE_HEIGHT.display.indic <= inner,
    'Indic: two 56 px lines at the 1.25 line height');
  // the hook band holds the kicker's line and the loop line's 4 lines at the slot minimum, Indic included
  const hook = reel.columns[0].box;
  assert.ok(P.kicker.min * LINE_HEIGHT.body.indic + P.kickerGap + P.statement.lines * P.statement.min * LINE_HEIGHT.display.indic
    <= hook.y1 - hook.y0, 'an Indic kicker and four Indic lines at the minimum');
});

test('sizes keep R-29: every slot at or above its floor, labels at or above the label floor, 9:16 weights >= 600', () => {
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    const L = LAYOUT[fmt];
    const floors = TYPE_FLOORS[fmt];
    for (const slot of SLOTS) {
      const s = /** @type {any} */ (L)[slot];
      assert.ok(s.min >= floors.text_min, `${fmt} ${slot}: above the text floor`);
      if (slot === 'kicker' || slot === 'label') assert.ok(s.min >= floors.label_min, `${fmt} ${slot}: a label`);
      if (floors.heavy_max_px && s.min <= floors.heavy_max_px) assert.ok(s.weight >= /** @type {number} */ (floors.heavy_min_weight), `${fmt} ${slot}: R-29 weight`);
      assert.ok(s.min <= s.max && s.lines >= 1);
    }
  }
  assert.equal(SIZE_STEP, 4, 'board_generic\'s and textfit.py\'s 4 px steps');
  const scale = /** @type {any} */ (CATALOGUE).type.scale;
  assert.equal(LINE_HEIGHT.display.latin, scale.display.line_height.latin[1]);
  assert.equal(LINE_HEIGHT.body.latin, scale.body.line_height.latin[0]);
  assert.ok(LINE_HEIGHT.display.indic >= scale.display.line_height.indic[0] && LINE_HEIGHT.body.indic >= scale.body.line_height.indic[0]);
  assert.ok(TRACKING_EM >= scale.display.tracking_em[0] && TRACKING_EM <= scale.display.tracking_em[1]);
  assert.equal(lineHeight('display', 'மின்னழுத்தம்'), LINE_HEIGHT.display.indic);
  assert.equal(lineHeight('body', 'आज बस इतना'), LINE_HEIGHT.body.indic);
  assert.equal(lineHeight('display', 'Ohm\'s law'), LINE_HEIGHT.display.latin);
});

test('slotRows: the table templates.py mirrors (default = the narrowest box), and the catalogue\'s once registered', () => {
  const rows = /** @type {any} */ (slotRows(ZONES.landscape, ZONES.portrait));
  assert.deepEqual(Object.keys(rows).sort(), [...SLOTS].sort());
  assert.deepEqual([rows.statement.landscape.next_up.w, rows.statement.landscape.end_screen.w, rows.statement.landscape.default.w,
    rows.statement.portrait.default.w], [938, 840, 840, 696]);
  assert.deepEqual([rows.next.landscape.next_up.w, rows.next.landscape.end_screen.w, rows.next.landscape.default.w,
    rows.next.portrait.default.w], [664, 738, 664, 588]);
  assert.deepEqual(rows.statement.landscape.default.size, [LAYOUT.landscape.statement.min, LAYOUT.landscape.statement.max]);
  assert.equal(rows.statement.landscape.default.face, 'display');
  assert.equal(rows.kicker.portrait.default.role, 'label');
  for (const slot of SLOTS) for (const fmt of ['landscape', 'portrait']) {
    for (const spec of Object.values(rows[slot][fmt])) assert.equal(/** @type {any} */ (spec).critical, true, `${slot} ${fmt}: critical`);
  }
  const cat = /** @type {any} */ (templateSlots('outro_next'));
  const spec = /** @type {any} */ (CATALOGUE.templates).outro_next;
  if (!cat || !Object.keys(cat).length) {
    // templates.py registers the template (implemented=True, slots=_outro_next_slots()) in the integrator's change;
    // until then the catalogue must not claim it is implemented, and this check binds as soon as it does
    assert.equal(spec.implemented, false, 'not registered yet: the catalogue agrees');
    return;
  }
  assert.equal(spec.implemented, true);
  assert.deepEqual(Object.keys(cat).sort(), Object.keys(rows).sort());
  for (const slot of Object.keys(rows)) {
    for (const fmt of Object.keys(rows[slot])) {
      assert.deepEqual(Object.keys(cat[slot][fmt]).sort(), Object.keys(rows[slot][fmt]).sort(), `${slot} ${fmt}: variants`);
      for (const variant of Object.keys(rows[slot][fmt])) {
        const got = cat[slot][fmt][variant];
        const want = rows[slot][fmt][variant];
        assert.deepEqual({ w: got.w, max_lines: got.max_lines, size: got.size, role: got.role, face: got.face, weight: got.weight,
          line_height: got.line_height, tracking_em: got.tracking_em, prefer_lines: got.prefer_lines, critical: got.critical },
        want, `${slot} ${fmt} ${variant}`);
      }
    }
  }
  assert.deepEqual(spec.variants, { landscape: [...VARIANTS], portrait: [] }, 'the variants templates.py declares');
  assert.deepEqual(spec.consumes.kinds, ['reveal'], 'the statement\'s reveal only: no cue is drawn on an outro');
});

test('ctaEnd: R-11\'s <= 3 s call to action, gone LOOP_HOLD before the end, never before CTA_MIN', () => {
  const dur = /** @type {any} */ (CATALOGUE).templates.outro_next.duration.portrait;
  assert.ok(dur.min >= CTA_MIN + LOOP_HOLD, '§4.3\'s shortest reel outro still ends on the loop line alone');
  assert.equal(CTA_MAX, 3, 'R-11: <= 3 s');
  for (let d = dur.min; d <= dur.max + 1e-9; d += 0.1) {
    const end = ctaEnd(d);
    assert.ok(end <= CTA_MAX + 1e-9 && end >= CTA_MIN - 1e-9, `${d}`);
    assert.ok(d - end >= LOOP_HOLD - 1e-9, `${d}: the last ${LOOP_HOLD} s is the loop line alone`);
  }
  assert.equal(ctaEnd(3), 2);
  assert.equal(ctaEnd(4), 3);
  assert.equal(ctaEnd(5), 3);
  assert.equal(ctaEnd(1), 1, 'never past the scene\'s end');
  assert.equal(ctaEnd(Number.NaN), 0);
});

test('texOf: the $...$ spans of the kicker, the statement and the next title, inline, in order', () => {
  const scene = /** @type {any} */ ({ title: 'About $R$', board: [{ id: 'i1', kind: 'takeaway', text: 'So $V = IR$ holds' }] });
  assert.deepEqual(texOf(scene, { refs: { next: { title: 'Power: $P = VI$' } } }),
    [{ tex: 'R', display: false }, { tex: 'V = IR', display: false }, { tex: 'P = VI', display: false }]);
  assert.deepEqual(texOf(/** @type {any} */ ({ title: 'plain', board: [] }), null), []);
});

test('mediaBox and the static hooks: no medium, no anchor', () => {
  const scene = /** @type {any} */ ({ type: 'summary', title: 'T', board: [] });
  assert.equal(outroNext.mediaBox?.(scene, ZONES.landscape, 'render', 'panel', { safe: false, slots: {} }), null);
  assert.equal(outroNext.mediaBox?.(scene, ZONES.portrait, 'render', undefined, { safe: false, slots: {} }), null);
  assert.equal(outroNext.anchor?.(/** @type {any} */ ({})), null);
});

// --- painted behaviour ----------------------------------------------------------------------------------------------

test('16:9 split: the kicker with the frame, the statement on its beat, the next card rising NEXT_AFTER later', async () => {
  const tl = load('outro_next.landscape.json');
  const k = scaleOf(tl);
  const reveal = tl.scenes[0].events.find((/** @type {any} */ e) => e.kind === 'reveal').t;
  const run = spyStage(tl);
  try {
    const v = () => run.ctx(0).view;
    await run.seekTo(0, 0);
    assert.equal(v().mode, 'split');
    assert.equal(op(partOf(v(), 'kicker').el.style.opacity), 0);
    assert.equal(v().cardBlock.card.style.visibility, 'hidden');
    await run.seekTo(0, (KICKER_ENTER * k) + 0.05);
    assert.equal(op(partOf(v(), 'kicker').el.style.opacity), 1, 'the kicker is in with the frame');
    assert.equal(v().statementAt, reveal, 'the statement enters on its reveal event (R-17)');
    assert.equal(partOf(v(), 'statement').alpha, 0, 'not before its beat');
    const cardAt = reveal + NEXT_AFTER * k;
    assert.ok(Math.abs(v().cardBlock.enter - cardAt) < 1e-9);
    await run.seekTo(0, cardAt - 0.04);
    assert.equal(op(v().cardBlock.card.style.opacity), 0, 'the card waits for the statement');
    assert.ok(partOf(v(), 'statement').alpha > 0, 'the statement is entering');
    assert.ok(partOf(v(), 'statement').mask, 'Crisp reveals the statement from its baseline (a mask)');
    const mid = await run.seekTo(0, cardAt + (CARD_ENTER * k) / 2);
    const dy = Number.parseFloat(/[-\d.]+px\)$/.exec(v().cardBlock.card.style.transform)?.[0] || '0');
    assert.ok(dy > 0 && dy < CARD_RISE && Number.isInteger(dy), `the card rises on whole px at ${mid}: ${dy}`);
    await run.seekTo(0, cardAt + CARD_ENTER * k + 0.05);
    assert.equal(op(v().cardBlock.card.style.opacity), 1);
    assert.equal(v().cardBlock.card.style.transform, 'none', 'landed');
    await run.seekTo(0, tl.scenes[0].duration - 0.05);
    assert.equal(op(v().cardBlock.card.style.opacity), 1, '16:9: the next card stays to the end (§4.3)');
    assert.equal(v().cardBlock.end, null);
    assert.equal(partOf(v(), 'label').el.textContent, NEXT_LABEL);
    assert.equal(partOf(v(), 'next').el.textContent, 'Part 2: Series and parallel circuits');
  } finally {
    run.done();
  }
});

test('16:9 without a next step: the statement alone in centre-60, no card is invented', async () => {
  const run = spyStage(load('outro_next.landscape.json'));
  try {
    await run.seekTo(1, 1.5);
    const v = run.ctx(1).view;
    assert.equal(v.mode, 'centred');
    assert.equal(v.cardBlock, null);
    assert.equal(v.text.item, null);
    assert.equal(partOf(v, 'statement').el.textContent, 'Keep the current flowing', 'the scene title is the statement');
    assert.equal(partOf(v, 'kicker'), undefined);
    assert.equal(v.textBlock.holder.style.left, '442px');
    assert.equal(v.textBlock.holder.style.width, `${1478 - 442}px`);
    assert.equal(v.textBlock.holder.style.textAlign, 'center', 'alone in the centre, the text is centred');
  } finally {
    run.done();
  }
});

test('16:9 end_screen: the statement and the card stacked in the left half, nothing right of the end-screen edge', async () => {
  const tl = load('outro_next.landscape.endscreen.json');
  assert.equal(tl.scenes[0].duration, /** @type {any} */ (CATALOGUE).end_screen.landscape.seconds, '§2.5: exactly 15 s');
  const run = spyStage(tl);
  try {
    await run.seekTo(0, 3);
    const v = run.ctx(0).view;
    assert.equal(v.mode, 'end_screen');
    assert.equal(v.textBlock.holder.style.textAlign, 'left', 'beside or above the card, the text keeps the left edge');
    const edge = endScreenBox(ZONES.landscape).x0;
    const right = (/** @type {HTMLElement} */ el) => Number.parseFloat(el.style.left) + Number.parseFloat(el.style.width);
    assert.ok(right(v.textBlock.holder) <= edge - LAYOUT.landscape.gutter);
    assert.ok(right(v.cardBlock.groupEl) <= edge - LAYOUT.landscape.gutter);
    assert.equal(v.cardBlock.groupEl.style.left, '96px');
    assert.ok(Number.parseFloat(v.cardBlock.groupEl.style.top) >= Number.parseFloat(v.textBlock.holder.style.top) + LAYOUT.landscape.stackGap,
      'the card below the statement');
    assert.equal(partOf(v, 'statement').mask, null, 'Soft: a fade and a rise, no mask');
  } finally {
    run.done();
  }
});

test('9:16: the call to action rises with the frame and leaves by ctaEnd (<= 3 s, R-11); the loop line stays', async () => {
  const tl = load('outro_next.portrait.json');
  const k = scaleOf(tl);
  const run = spyStage(tl);
  try {
    const v = () => run.ctx(0).view;
    await run.seekTo(0, 0);
    assert.equal(v().mode, 'reel');
    const end = ctaEnd(tl.scenes[0].duration);
    assert.equal(v().cardBlock.end, end);
    assert.equal(v().cardBlock.enter, 0, '9:16: the call to action opens the outro');
    await run.seekTo(0, CARD_ENTER * k + 0.05);
    assert.equal(op(v().cardBlock.card.style.opacity), 1);
    assert.equal(partOf(v(), 'next').alpha, 1, 'the card\'s text is settled (preflight samples it critical)');
    const exitAt = end - CTA_EXIT * k;
    await run.seekTo(0, exitAt - 0.04);
    assert.equal(op(v().cardBlock.card.style.opacity), 1, 'still in just before its exit');
    await run.seekTo(0, exitAt + (CTA_EXIT * k) / 2);
    const a = op(v().cardBlock.card.style.opacity);
    assert.ok(a > 0 && a < 1, `leaving: ${a}`);
    const dy = Number.parseFloat(/[-\d.]+px\)$/.exec(v().cardBlock.card.style.transform)?.[0] || '0');
    assert.ok(dy > 0 && dy <= CTA_DROP && Number.isInteger(dy), `falls on whole px: ${dy}`);
    await run.seekTo(0, end + 0.02);
    assert.equal(op(v().cardBlock.card.style.opacity), 0);
    assert.equal(v().cardBlock.card.style.visibility, 'hidden');
    assert.ok(end <= CTA_MAX);
    await run.seekTo(0, tl.scenes[0].duration - 0.05);
    assert.equal(partOf(v(), 'statement').alpha, 1, 'the last frame is the loop line');
    assert.equal(partOf(v(), 'next').alpha, 0);
  } finally {
    run.done();
  }
  // the 3 s Hindi outro: the call to action leaves at 2.0 s
  const hi = load('outro_next.portrait.hindi.json');
  const run2 = spyStage(hi);
  try {
    await run2.seekTo(1, 0.1);
    assert.equal(run2.ctx(1).view.cardBlock.end, 2);
    await run2.seekTo(1, 2.05);
    assert.equal(op(run2.ctx(1).view.cardBlock.card.style.opacity), 0);
  } finally {
    run2.done();
  }
});

test('reduced motion (live player): everything fades in place, no rise and no fall', async () => {
  const run = spyStage(load('outro_next.portrait.json'), { mode: 'live', reducedMotion: true });
  try {
    await run.seekTo(0, 0.2);
    const v = run.ctx(0).view;
    assert.equal(v.cardBlock.card.style.transform, 'none');
    assert.ok(op(v.cardBlock.card.style.opacity) > 0 && op(v.cardBlock.card.style.opacity) < 1, 'it still fades');
    assert.equal(partOf(v, 'kicker').el.style.transform, 'none');
    await run.seekTo(0, ctaEnd(4) - 0.1);
    assert.equal(v.cardBlock.card.style.transform, 'none');
  } finally {
    run.done();
  }
});

test('colours: background text in the stage\'s block-aware inks; the card on surface with only its border structural', async () => {
  const run = spyStage(load('outro_next.landscape.json'));
  try {
    await run.seekTo(0, 2);
    const ctx = run.ctx(0);
    const v = ctx.view;
    assert.equal(partOf(v, 'statement').el.style.color, rgb(ctx.onBackground.ink));
    assert.equal(partOf(v, 'kicker').el.style.color, rgb(ctx.onBackground.ink2));
    assert.equal(v.cardBlock.card.style.backgroundColor || v.cardBlock.card.style.background.replace(/ .*/, ''), rgb(ctx.roles.color('surface')));
    assert.equal(partOf(v, 'next').el.style.color, rgb(ctx.roles.color('ink')));
    assert.equal(partOf(v, 'label').el.style.color, rgb(ctx.roles.color('ink2')));
    assert.match(v.cardBlock.card.style.borderLeft, new RegExp(`^${BORDER_PX}px solid`));
    for (const p of [partOf(v, 'statement'), partOf(v, 'next')]) assert.equal(p.el.style.outline, '', 'never an outline (cues.js)');
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
  const wrapped = { ...outroNext, update(/** @type {any} */ ctx, /** @type {number} */ t, /** @type {any} */ st) { inside = true; try { outroNext.update(ctx, t, st); } finally { inside = false; } } };
  proto.style = function style(/** @type {any} */ el, /** @type {string} */ prop, /** @type {any} */ value) {
    if (inside && String(value) === '') empty.push(`${el.className} ${prop}`);
    return real.call(this, el, prop, value);
  };
  try {
    for (const name of FIXTURES) {
      const tl = load(name);
      const { stage, done } = renderStage(tl, { templates: { outro_next: /** @type {any} */ (wrapped) }, renderTex: recordingTex().renderTex });
      const total = Math.round(tl.total_duration * tl.fps);
      for (let f = 0; f < total; f += 4) await stage.seek(f);
      done();
    }
  } finally {
    proto.style = real;
  }
  assert.deepEqual([...new Set(empty)], []);
});

test('dimGroups, itemEl and focusRect: the kicker, the statement and the card; the statement for its item', async () => {
  const run = spyStage(load('outro_next.landscape.json'));
  try {
    await run.seekTo(0, 0.1);
    const ctx = run.ctx(0);
    const groups = /** @type {Element[]} */ (outroNext.dimGroups?.(ctx));
    assert.deepEqual(groups.map((g) => g.className),
      ['dyn-group dyn-on-kicker-group', 'dyn-group dyn-on-statement-group', 'dyn-group dyn-on-card-group']);
    assert.equal(outroNext.focusRect?.(ctx, 'on-l-i1'), null, 'not on screen before its beat');
    await run.seekTo(0, 3);
    assert.deepEqual(outroNext.dimGroups?.(ctx), groups, 'the same elements at every t');
    const item = ctx.view.text.item.id;
    assert.equal(outroNext.itemEl?.(ctx, item), partOf(ctx.view, 'statement').el);
    assert.equal(outroNext.itemEl?.(ctx, 'nope'), null);
    assert.equal(outroNext.focusRect?.(ctx, 'panel'), null);
    assert.equal(outroNext.focusRect?.(ctx, 'nope'), null);
    const r = outroNext.focusRect?.(ctx, item);
    if (r) {
      assert.ok(['x', 'y', 'w', 'h'].every((key) => Number.isInteger(/** @type {any} */ (r.rect)[key])));
      assert.equal(r.kind, 'text');
    }
  } finally {
    run.done();
  }
  // a statement that is the scene title belongs to no item
  const run2 = spyStage(load('outro_next.landscape.json'));
  try {
    await run2.seekTo(1, 1);
    assert.equal(outroNext.itemEl?.(run2.ctx(1), 'on-l-title'), null);
  } finally {
    run2.done();
  }
});

test('a scene this template does not draw goes to board_generic whole, every hook handed on', async () => {
  const tl = load('outro_next.landscape.json');
  tl.scenes[0].board.push({ id: 'b2', kind: 'bullet', text: 'A point an outro does not draw' });
  const run = spyStage(tl);
  try {
    await run.seekTo(0, 2);
    const ctx = run.ctx(0);
    assert.equal(ctx.view.onFallback, true);
    assert.ok(ctx.root.querySelector('.dyn-board'), 'board_generic\'s DOM');
    assert.deepEqual(/** @type {any[]} */ (outroNext.texts?.(ctx)).map((e) => e.text), /** @type {any[]} */ (boardGeneric.texts?.(ctx)).map((e) => e.text));
    assert.deepEqual(outroNext.dimGroups?.(ctx), boardGeneric.dimGroups?.(ctx));
    assert.deepEqual(outroNext.tex?.(tl.scenes[0], tl.scenes[0].look, 'landscape'), boardGeneric.tex?.(tl.scenes[0], tl.scenes[0].look, 'landscape'));
    assert.deepEqual(outroNext.itemEl?.(ctx, 'b2'), boardGeneric.itemEl?.(ctx, 'b2'));
  } finally {
    run.done();
  }
});

// --- preflight: text entries and remedies ---------------------------------------------------------------------------

test('texts(): all four critical, at the sizes and alphas painted now, on the background or on the card\'s surface', async () => {
  const run = spyStage(load('outro_next.landscape.json'));
  try {
    await run.seekTo(0, 3);
    const ctx = run.ctx(0);
    const entries = /** @type {any[]} */ (outroNext.texts?.(ctx));
    assert.deepEqual(entries.map((e) => [e.slot, e.role, e.critical]),
      [['kicker', 'label', true], ['statement', 'title', true], ['label', 'label', true], ['next', 'title', true]]);
    const surface = ctx.roles.color('surface');
    assert.deepEqual(entries.map((e) => e.bg), [ctx.onBackground.bg, ctx.onBackground.bg, surface, surface]);
    assert.deepEqual(entries.map((e) => e.ink), [ctx.onBackground.ink2, ctx.onBackground.ink, ctx.roles.color('ink2'), ctx.roles.color('ink')]);
    assert.deepEqual(entries.map((e) => e.alpha), [1, 1, 1, 1], 'settled');
    assert.deepEqual(entries.map((e) => e.size), [LAYOUT.landscape.kicker.max, LAYOUT.landscape.statement.max, LAYOUT.landscape.label.max, LAYOUT.landscape.next.max],
      'jsdom has no layout: every text fits at its largest size');
    assert.deepEqual(entries.map((e) => e.min), [LAYOUT.landscape.kicker.min, LAYOUT.landscape.statement.min, LAYOUT.landscape.label.min, LAYOUT.landscape.next.min]);
    assert.deepEqual(entries.map((e) => e.overflow), [0, 0, 0, 0]);
    assert.deepEqual(entries.map((e) => e.text), ['What we learned',
      'Voltage pushes, current flows, resistance opposes: V = IR ties all three together.', NEXT_LABEL,
      'Part 2: Series and parallel circuits']);
  } finally {
    run.done();
  }
});

test('preflight\'s remedies: a size cap and `safe` hold a slot small; a scrim goes behind the background text only', async () => {
  const tl = load('outro_next.landscape.json');
  tl.scenes[0].look.fixes = { safe: false, slots: { statement: { size: 72, scrim: true }, next: { size: 60 } } };
  const run = spyStage(tl);
  try {
    await run.seekTo(0, 3);
    const ctx = run.ctx(0);
    const v = ctx.view;
    assert.equal(partOf(v, 'statement').size, 72, 'the fitted size never passes the cap');
    assert.equal(partOf(v, 'next').size, 60);
    assert.ok(v.textBlock.scrim, 'the scrim is behind the kicker and the statement');
    assert.ok(v.textBlock.holder.style.background, 'painted on the holder');
    assert.equal(v.textBlock.holder.style.left, `${96 - 24}px`, 'the scrim grows outward: the text keeps its column');
    const entries = /** @type {any[]} */ (outroNext.texts?.(ctx));
    assert.notEqual(entries[1].bg, ctx.onBackground.bg, 'reported on the scrimmed background');
    assert.equal(entries[3].bg, ctx.roles.color('surface'), 'the card\'s text keeps its surface');
  } finally {
    run.done();
  }
  const safe = load('outro_next.portrait.json');
  safe.scenes[0].look.fixes = { safe: true, slots: {} };
  const run2 = spyStage(safe);
  try {
    await run2.seekTo(0, 2);
    const v = run2.ctx(0).view;
    for (const slot of SLOTS) assert.equal(partOf(v, slot).size, /** @type {any} */ (LAYOUT.portrait)[slot].min, `\`safe\`: ${slot} at its minimum`);
  } finally {
    run2.done();
  }
});

// --- the fit, on a fake text layout ---------------------------------------------------------------------------------

/**
 * Install a fake text layout on HTMLElement (jsdom has none): a glyph is 0.55 em wide and words wrap greedily inside
 * the width their block gives them (the text holder's, or the card's less its padding and border). Returns the
 * restore function.
 * @param {'landscape' | 'portrait'} fmt
 */
function fakeLayout(fmt) {
  const proto = /** @type {any} */ (HTMLElement.prototype);
  const keys = ['offsetHeight', 'offsetWidth', 'clientWidth', 'scrollWidth'];
  const saved = keys.map((k) => [k, Object.getOwnPropertyDescriptor(proto, k)]);
  const CW = 0.55;
  const L = LAYOUT[fmt];
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
    const holder = /** @type {HTMLElement | null} */ (el.closest('.dyn-on-text'));
    if (holder) return Number.parseFloat(holder.style.width) || 0;
    const card = /** @type {HTMLElement | null} */ (el.closest('.dyn-on-card-group'));
    return card ? (Number.parseFloat(card.style.width) || 0) - 2 * L.pad - BORDER_PX : 0;
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
  const TEXT = ['dyn-on-kicker', 'dyn-on-statement', 'dyn-on-label', 'dyn-on-next'];
  /** @param {HTMLElement} el @returns {number} */
  const height = (el) => {
    const lh = Number.parseFloat(el.style.lineHeight || '') || 1.3;
    if (el.classList.contains('dyn-word')) return lh * fontSize(el);
    if (TEXT.some((c) => el.classList.contains(c))) return lines(el) * lh * fontSize(el);
    if (el.classList.contains('dyn-reveal-mask')) return height(/** @type {HTMLElement} */ (el.firstElementChild));
    if (el.classList.contains('dyn-on-card')) {
      const [lab, next] = /** @type {HTMLElement[]} */ ([...el.children]);
      return 2 * L.pad + height(lab) + L.labelGap + height(next);
    }
    return 0;
  };
  Object.defineProperty(proto, 'offsetHeight', { configurable: true, get() { return height(this); } });
  Object.defineProperty(proto, 'offsetWidth', { configurable: true, get() { return (/** @type {HTMLElement} */ (this).textContent || '').length * CW * fontSize(this); } });
  Object.defineProperty(proto, 'clientWidth', { configurable: true, get() { return boxWidth(this); } });
  Object.defineProperty(proto, 'scrollWidth', { configurable: true, get() { return Math.max(boxWidth(this), (/** @type {HTMLElement} */ (this).textContent || '').length * CW * fontSize(this)); } });
  return () => {
    for (const [k, d] of saved) {
      if (d) Object.defineProperty(proto, /** @type {string} */ (k), /** @type {PropertyDescriptor} */ (d));
      else delete proto[/** @type {string} */ (k)];
    }
  };
}

/**
 * The first scene of a fixture with its statement (and optionally its title and next step) replaced.
 * @param {string} name @param {string} statement @param {{title?: string, next?: string | null}} [opts]
 */
function outroTimeline(name, statement, opts = {}) {
  const tl = load(name);
  tl.scenes = [tl.scenes[0]];
  tl.total_duration = tl.scenes[0].duration;
  tl.chapters = [tl.chapters[0]];
  tl.captions = [];
  for (const b of tl.scenes[0].beats) b.captions = [];
  tl.scenes[0].board[0].text = statement;
  if (opts.title !== undefined) tl.scenes[0].title = opts.title;
  if (opts.next !== undefined) {
    if (opts.next === null) delete tl.scenes[0].look.refs;
    else tl.scenes[0].look.refs = { next: { title: opts.next } };
  }
  return tl;
}

test('fit: each text at the largest size its lines and column allow; past the floor it is reported, never clipped', async () => {
  const restore = fakeLayout('landscape');
  try {
    const short = spyStage(outroTimeline('outro_next.landscape.json', 'Three ideas, one law.'));
    await short.seekTo(0, 3);
    let v = short.ctx(0).view;
    assert.equal(partOf(v, 'statement').size, LAYOUT.landscape.statement.max);
    assert.equal(partOf(v, 'statement').overflow, 0);
    // the stack is centred in the critical box: its top is where half the room is left above it
    const total = partOf(v, 'kicker').box.offsetHeight + LAYOUT.landscape.kickerGap + partOf(v, 'statement').box.offsetHeight;
    assert.equal(Number.parseFloat(v.textBlock.holder.style.top), 54 + Math.round((858 - total) / 2));
    short.done();
    // a statement that needs more than five lines at the top size steps down until it fits
    // (16 four-letter words: six lines of three at 96 px, four lines of four at 88 px)
    const long = spyStage(outroTimeline('outro_next.landscape.json', 'word '.repeat(16).trim()));
    await long.seekTo(0, 3);
    v = long.ctx(0).view;
    const p = partOf(v, 'statement');
    assert.ok(p.size < LAYOUT.landscape.statement.max && p.size >= LAYOUT.landscape.statement.min, `fitted to ${p.size}`);
    assert.equal(p.overflow, 0);
    long.done();
    // more than any size can hold: the floor, and the overflow reported as critical (preflight blocks "Shorten text")
    const huge = spyStage(outroTimeline('outro_next.landscape.json', 'overflowing '.repeat(60).trim()));
    await huge.seekTo(0, 3);
    v = huge.ctx(0).view;
    assert.equal(partOf(v, 'statement').size, LAYOUT.landscape.statement.min);
    const entry = /** @type {any[]} */ (outroNext.texts?.(huge.ctx(0))).find((e) => e.slot === 'statement');
    assert.ok(entry.overflow > 0 && entry.critical, 'reported, never silently clipped');
    huge.done();
  } finally {
    restore();
  }
});

test('fit: a stack too tall for its column gives way in order - the statement first, then the next title, then the kicker', async () => {
  const restore = fakeLayout('landscape');
  try {
    // the end-screen column holds the kicker, the statement and the card: a statement of four lines at 96 px and a
    // three-line next title cannot all fit 858 px, so the statement shrinks first
    const tl = outroTimeline('outro_next.landscape.endscreen.json', 'word '.repeat(48).trim(),
      { title: 'A scene title', next: 'next '.repeat(24).trim() });
    const run = spyStage(tl);
    await run.seekTo(0, 3);
    const v = run.ctx(0).view;
    const s = partOf(v, 'statement');
    const n = partOf(v, 'next');
    const k = partOf(v, 'kicker');
    assert.ok(s.size < LAYOUT.landscape.statement.max, `the statement gave way: ${s.size}`);
    if (s.size > s.range.min) assert.equal(n.size, LAYOUT.landscape.next.max, 'the next title keeps its size while the statement can shrink');
    if (n.size < LAYOUT.landscape.next.max) assert.equal(s.size, s.range.min, 'the next title shrinks only once the statement is at its floor');
    if (k.size < LAYOUT.landscape.kicker.max) assert.equal(n.size, n.range.min, 'the kicker last');
    // nothing is placed right of the end-screen column, whatever the fit did
    assert.ok(Number.parseFloat(v.cardBlock.groupEl.style.left) + Number.parseFloat(v.cardBlock.groupEl.style.width)
      <= endScreenBox(ZONES.landscape).x0 - LAYOUT.landscape.gutter);
    run.done();
  } finally {
    restore();
  }
});

test('fit (9:16): the loop line and the call to action keep to their own boxes; the card\'s overflow is the next title\'s', async () => {
  const restore = fakeLayout('portrait');
  try {
    const run = spyStage(outroTimeline('outro_next.portrait.json', 'So: can a thin wire really stop a current?'));
    await run.seekTo(0, 2);
    let v = run.ctx(0).view;
    const card = v.plan.columns[1].box;
    assert.equal(Number.parseFloat(v.cardBlock.groupEl.style.top), card.y0, 'top-aligned under the hook band');
    assert.ok(v.cardBlock.card.offsetHeight <= card.y1 - card.y0, 'the card fits above the caption band');
    for (const slot of SLOTS) assert.equal(partOf(v, slot).overflow, 0, slot);
    run.done();
    const long = spyStage(outroTimeline('outro_next.portrait.json', 'So: can a thin wire really stop a current?',
      { next: 'An extremely long next part title that cannot possibly fit two lines of the card at all' }));
    await long.seekTo(0, 2);
    v = long.ctx(0).view;
    assert.equal(partOf(v, 'next').size, LAYOUT.portrait.next.min);
    assert.ok(partOf(v, 'next').overflow > 0, 'reported on the next title');
    assert.equal(partOf(v, 'label').overflow, 0);
    long.done();
  } finally {
    restore();
  }
});

test('every painted number goes through fx and every translate is on whole pixels', async () => {
  const run = spyStage(load('outro_next.portrait.hindi.json'));
  try {
    for (const t of [0.1, 0.3, 1.9, 2.1, 2.5, 4.8]) {
      await run.seekTo(0, t);
      const v = run.ctx(0).view;
      const card = v.cardBlock.card;
      assert.equal(String(Number(card.style.opacity)), card.style.opacity, `${t}: a plain number`);
      assert.equal(Number(card.style.opacity), Number(fx(Number(card.style.opacity))));
      for (const el of [card, partOf(v, 'statement').el, partOf(v, 'kicker').el]) {
        const dy = /translate\(0, (-?[\d.]+)px\)/.exec(el.style.transform);
        if (dy) assert.ok(Number.isInteger(Number(dy[1])), `${t} ${el.className}: whole-pixel translate`);
      }
    }
  } finally {
    run.done();
  }
});
