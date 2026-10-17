// @ts-check
/**
 * `myth_fact` (DYNAMIC_VIDEO_DESIGN §4.3 C, R1, class `refutation`; R-10, R-63; B2 plan §1.4 template contract, §1.5
 * cues and item states, §1.11 slots, §P; the pattern of `docs/plans/b2/b2c_design_notes.md`): a belief corrected.
 * A statement card labelled "Common belief" (the `warn` icon) is struck through (state.js `struck`: an SVG line in
 * `warn`, never text-decoration), shrinks and dims, and the larger FACT card (the `ok` icon, full contrast) slides in
 * with the correction and stays as the scene's final frame (R-63: the fact is final and visually dominant).
 *
 * Data (§4.3; templates.py `_fit_myth_fact`): exactly one `misconception` item with its correction (`text` the belief,
 * `justification` the fact). Owner decision 3 (2026-10-16): a scene that carries the misconception beside the
 * scene's DEFINITIONS (the showcase lecture's s5: two definitions, then the misconception) is this template's too,
 * and draws the definitions as **supporting lines** above the refutation - in 16:9 only, at most SUPPORT_MAX of them,
 * with at most one `heading`. `key_terms` stays pure (its fit refuses a misconception). In 16:9 the scene's side
 * panel is drawn beside the supporting lines through `_shared.js` (a picture, a clip, a terminal card or a titled
 * stand-in, `drawsPanel`). Anything else - a second misconception, one without its correction, any other item kind,
 * a scene medium, a panel `_shared.js` does not draw, and in 9:16 a heading, a definition or any panel (a reel's
 * column holds the two cards alone: measured, the belief and the correction already take up to 672 of its 680 px) -
 * is handed to board_generic whole, every hook with it (`drawsScene`, list_build's convention). `_fit_myth_fact`
 * refuses the same scenes (B2c review CG-2), and is stricter still where text would not fit (templates.py
 * `MYTH_FACT_CAPS`, measured against LAYOUT below), so the director never sends a scene that would block preflight.
 *
 * 16:9 (whole stage px, the catalogue's regions and grid; the band is the title zone's top to the body zone's
 * bottom, y 112-900, 24 px above the caption band): the heading across the `top-band` width (x 96-1824); under it the
 * supporting lines in `left-55` (x 96-1034, 938 px: a body line of <= 45 characters, §4.6) and the panel in `right-45`
 * beside them (across the grid box when there are no lines); under those the refutation row on the 12-column grid:
 * the myth card in columns 1-5 (x 96-802) and the fact card in columns 6-12 (x 826-1824), so the fact is the wider,
 * larger card. Before the refutation the myth card stands alone, centred on the grid; on the refutation it is pushed
 * aside to its column while it shrinks and dims, and the fact card slides in from the right. With a panel the row
 * stands on the band's bottom and the panel takes the height that is left; without one the lines and the row are
 * centred together in the band under the heading (the row alone, in the band).
 *
 * 9:16: one column. The cards are the text column (x 192-780, the part both critical boxes hold, R-59) grown by their
 * padding and border, so their text sits exactly in the column while the cards themselves, shapes, reach past it
 * (definition_card's convention). §4.3: "the myth card in the upper stage, pushed up and shrunk by the fact card":
 * the myth stands alone, centred in the band (y 340-1020); on the refutation it moves up as it shrinks and the fact
 * card rises in below it, the pair (the shrunk myth over the fact) centred in the band.
 *
 * The shrink (§4.3 "shrinks to 70% and dims to 40%"): a 2D transform about the card's top-left corner (16:9) or its
 * top centre (9:16), to SHRINK - but never below R-29's floors: the card shrinks to the largest of SHRINK, the label
 * floor over the label's size and the text floor over the statement's fitted size (`shrinkScale`), so a struck card
 * is still set in legal type (a B2 reading, pending the owner's review: 0.8334 in 16:9, 0.8462-0.9091 in 9:16 at
 * this template's sizes). The dim is the card's own opacity (DIM), inside its dim group (the stage owns the group's).
 *
 * Choreography (§4.3, §4.8; template durations take the personality's scale through `_shared.js seconds` /
 * `enterAt`, R-49: no sound here): the heading and each supporting line enter on their own beats with the
 * personality's text entrance; the myth card fades in and rises CARD_RISE px on its beat (state.js `itemTimings`: a
 * `reveal` event wins, else the beat, R-17's 120 ms lead). The REFUTATION (`refuteTimes`): the strike starts at the
 * item's earliest `draw` event (templates.py `consumes`), else at the middle of the narration of the beat that
 * reveals it (the belief is said first and its correction second: B2 reading), else FACT_DELAY after the myth enters
 * (board_generic's 9:16 delay), never before MYTH_READ after the myth enters and never later than FACT_TAIL before the
 * scene ends; the strike draws over STRIKE_DRAW; as it completes the myth gives way (pushed aside or up, shrunk and
 * dimmed over SHRINK_SECONDS) and FACT_LAG later the fact card comes in over FACT_ENTER (at the item's `answer` event
 * when it has one, the strike then timed back from it). Under reduced motion (live only) nothing travels: the myth
 * stands at its final place from the start and the fact fades in place.
 *
 * Emphasis is cues.js's (§4.2, plan §1.4/§1.5): `itemEl` hands it an item's text in its field order (a supporting
 * definition's term then its meaning; the misconception's belief then its correction, so `<item>:term:<n>` counts
 * over `text` then `justification`, the order templates.py's target grammar names), every run built by
 * `_shared.js richWords` so the `[[term]]` `.rt-k` spans survive; `focusRect` gives the heading or a supporting line
 * (text), the misconception's card - the fact card once it is in, the myth card before - (a shape) and, through
 * `_shared.js panelFocusRect`, the panel and a terminal's output line; `dimGroups` the heading, every supporting line,
 * both cards and the panel. Nothing here writes `outline`, `text-decoration`, `filter`, `backdrop-filter` or
 * `will-change`, no structure role colours anything (the cards' edges are the content roles `warn` / `ok`, which
 * §4.2 gives errors and myths and their corrections), and no clock, randomness or `.timeline` is read.
 *
 * Determinism (§7.4, plan §P): `update` is a pure function of t; every property it paints is painted on every frame
 * with a value (never ''); translates are whole pixels and 2D. Sizes are decided in `layout` from the laid-out page;
 * the strike's lines are measured there with the myth card's painted transform lifted (and put back, unchanged), so a
 * layout that runs again mid-scene (a late face) measures the same lines.
 *
 * The sizes, gaps, the shrink reading and the refutation timing are B2 proposals pending the owner's review:
 * aadhi/compose/dynamic/templates.py mirrors LAYOUT as this template's slots (`slotRows`), and a test pins the two
 * together.
 */

import { h } from '../../shared/dom.js';
import { icon } from '../../player/icons.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { GRID, LAYOUT_TOKENS, TYPE_FLOORS, fixesOf, fx, parseTarget } from '../core.js';
import { itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import {
  PANEL_TERM, RADIUS, SPACE, STRIKE_DRAW, buildPanel, drawsPanel, enterAt, fitLines, gridSpan, group, itemStateParts,
  layoutPanel, layoutStrike, paintItemState, paintPanel, paintTextEntrance, panelFocusRect, panelMediaBox,
  panelOf, panelTexts, region, revealMask, richWords, scrimmedBg, seconds, shownPanel, slotRange,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */

export const ID = 'myth_fact';

/** UI labels (kept together for localisation; §4.3 "labelled 'Common belief'", the FACT card). */
export const LABELS = Object.freeze({ myth: 'Common belief', fact: 'Fact' });

/**
 * How many supporting definitions a scene may carry beside its misconception (owner decision 3): two in 16:9 (the
 * showcase's s5; templates.py `MYTH_FACT_SUPPORT_MAX` mirrors it), none in 9:16 (the reel's column holds the two
 * cards alone).
 */
export const SUPPORT_MAX = Object.freeze({ landscape: 2, portrait: 0 });

// ---------------------------------------------------------------------------------------------
// Motion (§4.3 myth_fact, §4.8), seconds before the personality's scale
// ---------------------------------------------------------------------------------------------

/** The myth card fades in and rises this far (the B2 cards' 24 px) ... */
export const CARD_RISE = 24;
/** ... over this long (§4.8 entrance 400-600 ms). */
export const CARD_ENTER = 0.45;
/** The heading's and a supporting line's text entrance. */
export const TEXT_ENTER = 0.45;
/** §4.3: "the card shrinks to 70% and dims to 40%" (held at R-29's floors: `shrinkScale`). */
export const SHRINK = 0.7;
export const DIM = 0.4;
/** The myth gives way (pushed aside or up, shrunk, dimmed) over this long, as the fact comes in. */
export const SHRINK_SECONDS = 0.5;
/** The fact card's entrance, travelling FACT_TRAVEL px: in from the right in 16:9, up from below in 9:16 ... */
export const FACT_ENTER = 0.5;
/** ... starting this long after the myth starts to give way (by then it has mostly cleared the fact's place). */
export const FACT_LAG = 0.15;
export const FACT_TRAVEL = Object.freeze({
  landscape: Object.freeze({ x: 48, y: 0 }),
  portrait: Object.freeze({ x: 0, y: 32 }),
});
/** The belief stays unstruck at least this long after its card starts to enter (it must be read before it is
 * refuted; a reading time, so the personality's scale does not apply). */
export const MYTH_READ = 1.2;
/** A myth no beat reveals is refuted this long after it enters (board_generic's 9:16 FACT_DELAY). */
export const FACT_DELAY = 1.5;
/** The refutation starts at least this long before the scene ends, so the fact is in and read (R-63: it is the final
 * frame). */
export const FACT_TAIL = 2.0;

// ---------------------------------------------------------------------------------------------
// Layout (stage px; R-29 floors; the catalogue's regions, grid and layout tokens)
// ---------------------------------------------------------------------------------------------

/** Line heights of the type scale (§4.6; plan §2: Indic heading >= 1.25, body >= 1.45); the label is English. */
export const LINE_HEIGHT = Object.freeze({
  heading: Object.freeze({ latin: 1.15, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
  label: 1.2,
});
/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;
/** The cards' edge (the catalogue's `border.card`, 6 px) in `warn` (the myth) or `ok` (the fact). */
export const BORDER_PX = Number(LAYOUT_TOKENS.border.card);
/** A heading's and a supporting term's weight; the labels' too. */
export const BOLD = 700;
/** Between a supporting definition's term and its meaning (the classic surfaces' "term — text"). */
export const SUPPORT_SEPARATOR = ' — ';

/**
 * Per-format layout (B2 proposals, pending the owner's review). `heading`: 16:9 R-29 headings at key_terms' 84-96, two
 * lines with one preferred. `support`: a supporting line's one size (term and meaning), 40-48 (below R-29's body
 * 48-56, above its floor: subordinate lines, deviation 31's precedent). `myth` / `fact`: the statements' ranges, the
 * fact's larger in both formats (§4.3 "larger"), at the body weight `weight` (9:16: R-29's >= 600 at <= 64 px).
 * `label`: the cards' label size, a fixed size above R-29's label floor so the card can shrink (`shrinkScale`).
 * `pad`: a card's padding; `labelGap`: label row to statement; `headingGap` / `supportGap` / `rowGap`: heading to
 * what is under it, line to line, the lines (or heading) to the refutation row; `gap`: 9:16 myth to fact. `mythCols` /
 * `factCols`: the 16:9 row's grid columns (0-based, inclusive).
 *
 * The room these give, measured with textfit on the vendored faces (Latin, Tamil, Malayalam, Hindi, Telugu; templates.py
 * `MYTH_FACT_CAPS` keeps every scene the director sends here within it): 16:9 at the minimum sizes, two 3-line
 * supporting lines, a 4-line belief and a 4-line correction take 737 px of the band's 788 (780 at the Indic line
 * heights); with a 1-line heading, one 3-line line (727 Indic). 9:16: a 3-line belief shrunk to 0.9091 and a 4-line
 * correction take 672 of 680 (a 3-line correction at the Indic line heights, 634).
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    heading: Object.freeze({ max: 96, min: 84, lines: 2, prefer: 1 }),
    support: Object.freeze({ max: 48, min: 40 }),
    myth: Object.freeze({ max: 52, min: 44 }),
    fact: Object.freeze({ max: 56, min: 48 }),
    label: 48,
    weight: 400,
    pad: SPACE[2],
    labelGap: SPACE[0],
    headingGap: SPACE[3],
    supportGap: SPACE[1],
    rowGap: SPACE[2],
    gap: SPACE[2],
    mythCols: Object.freeze([0, 4]),
    factCols: Object.freeze([5, 11]),
  }),
  portrait: Object.freeze({
    heading: null,
    support: null,
    myth: Object.freeze({ max: 52, min: 44 }),
    fact: Object.freeze({ max: 56, min: 48 }),
    label: 52,
    weight: 600,
    pad: SPACE[2],
    labelGap: SPACE[0],
    headingGap: 0,
    supportGap: 0,
    rowGap: 0,
    gap: SPACE[2],
    mythCols: null,
    factCols: null,
  }),
});

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/** Indic scripts (Devanagari to Sinhala), whose text takes the taller line heights (plan §2). */
const INDIC = new RegExp(`[${['Devanagari', 'Bengali', 'Gurmukhi', 'Gujarati', 'Oriya', 'Tamil', 'Telugu', 'Kannada',
  'Malayalam', 'Sinhala'].map((x) => `\\p{Script=${x}}`).join('')}]`, 'u');

/**
 * The line height of `text` in type role `role` (LINE_HEIGHT: Indic text takes the Indic value). Pure.
 * @param {'heading' | 'body'} role @param {string} text
 */
export function lineHeight(role, text) {
  return INDIC.test(plainText(String(text || ''))) ? LINE_HEIGHT[role].indic : LINE_HEIGHT[role].latin;
}

/** @param {unknown} s */
const filled = (s) => typeof s === 'string' && s.trim() !== '';

/**
 * The scene's parts as this template reads them: `myth` (its one misconception with a belief and a correction, else
 * null), `supports` (its definitions, in board order), `heading` (its first heading) and `other` (every item it has
 * no place for: a second misconception or heading, a misconception without its correction, any other kind). Pure.
 * @param {TimedScene} scene
 * @returns {{myth: BoardItem | null, supports: BoardItem[], heading: BoardItem | null, other: BoardItem[]}}
 */
export function sceneParts(scene) {
  /** @type {BoardItem | null} */
  let myth = null;
  /** @type {BoardItem | null} */
  let heading = null;
  /** @type {BoardItem[]} */
  const supports = [];
  /** @type {BoardItem[]} */
  const other = [];
  for (const item of scene.board || []) {
    if (item.kind === 'misconception' && !myth && filled(item.text) && filled(item.justification)) myth = item;
    else if (item.kind === 'definition') supports.push(item);
    else if (item.kind === 'heading' && !heading) heading = item;
    else other.push(item);
  }
  return { myth, supports, heading, other };
}

/**
 * Whether this template draws the scene whole in `format` (templates.py `_fit_myth_fact` scores every other scene 0,
 * so direct.py never sends one here, B2c review CG-2): one misconception with its correction; beside it only
 * definitions (at most SUPPORT_MAX[format]) and one heading (16:9 only); no scene medium; a shown side panel only in
 * 16:9 and of a kind `_shared.js` draws. Anything else is board_generic's, every hook handed on.
 * @param {TimedScene} scene
 * @param {'landscape' | 'portrait'} [format]
 */
export function drawsScene(scene, format = 'landscape') {
  if (scene.media) return false;
  const parts = sceneParts(scene);
  if (!parts.myth || parts.other.length > 0) return false;
  if (parts.supports.length > SUPPORT_MAX[format]) return false;
  if (format === 'portrait' && parts.heading) return false;
  if (!shownPanel(scene)) return true;
  return format === 'landscape' && drawsPanel(scene, format);
}

/**
 * The scene's panel as this template draws it (16:9 only; `_shared.js panelOf`), or null.
 * @param {TimedScene} scene @param {'landscape' | 'portrait'} format @param {'live' | 'render'} [mode]
 */
export function panelFor(scene, format, mode = 'live') {
  return format === 'landscape' ? panelOf(scene, mode, format) : null;
}

/**
 * The earliest event of `kind` on item `id` (its target `<id>` or `<id>:<part>`), or null.
 * @param {TimedScene} scene @param {string} kind @param {string} id
 * @returns {number | null}
 */
export function eventAt(scene, kind, id) {
  /** @type {number | null} */
  let at = null;
  for (const e of scene.events || []) {
    if (!e || e.kind !== kind || typeof e.t !== 'number' || !Number.isFinite(e.t)) continue;
    if (typeof e.target !== 'string' || e.target.split(':')[0] !== id) continue;
    if (at === null || e.t < at) at = e.t;
  }
  return at;
}

/**
 * When the refutation happens (see the module header), scene-relative seconds: `strike` (the line starts to draw),
 * `shrink` (the drawn-through myth starts to give way: pushed aside or up, shrunk, dimmed) and `fact` (the fact card
 * starts to enter, FACT_LAG after it, so the myth has mostly cleared the fact's place by then). A `draw` event on the
 * item times the strike, an `answer` event the fact (and the strike before it, when there is no `draw`); without
 * either, the strike is the middle of the revealing beat's narration (else FACT_DELAY after the entrance), held
 * between MYTH_READ after the entrance and FACT_TAIL before the scene's end. Never before the myth card is in.
 * `enter`: the myth card's entrance; `k`: the personality's duration scale (`_shared.js seconds`). Pure.
 * @param {TimedScene} scene @param {string} id @param {number} enter @param {number} [k]
 * @returns {{strike: number, shrink: number, fact: number}}
 */
export function refuteTimes(scene, id, enter, k = 1) {
  const scale = k > 0 ? k : 1;
  const draw = eventAt(scene, 'draw', id);
  const answer = eventAt(scene, 'answer', id);
  let strike;
  if (draw !== null) {
    strike = draw;
  } else if (answer !== null) {
    strike = answer - (STRIKE_DRAW + FACT_LAG) * scale;
  } else {
    const beat = (scene.beats || []).find((b) => b && b.board_item_id === id);
    const start = beat ? Number(beat.start) : NaN;
    const end = beat ? (Number.isFinite(Number(beat.speech_end)) ? Number(beat.speech_end) : Number(beat.end)) : NaN;
    strike = Number.isFinite(start) && Number.isFinite(end) && end > start ? (start + end) / 2 : enter + FACT_DELAY;
    strike = Math.min(Math.max(strike, enter + MYTH_READ), Number(scene.duration) - FACT_TAIL);
  }
  // never before the card is in
  strike = Math.max(strike, enter + CARD_ENTER * scale);
  const drawn = strike + STRIKE_DRAW * scale;
  const fact = answer !== null ? Math.max(answer, drawn + FACT_LAG * scale) : drawn + FACT_LAG * scale;
  return { strike, shrink: fact - FACT_LAG * scale, fact };
}

/**
 * How far the myth card shrinks (§4.3's SHRINK, held at R-29's floors so a struck card is still set in legal type):
 * the largest of SHRINK, the label floor over the label size and the text floor over the statement's size, rounded
 * UP to 4 decimals (a floating-point product never lands a hair under a floor). Pure.
 * @param {'landscape' | 'portrait'} format @param {number} mythSize
 */
export function shrinkScale(format, mythSize) {
  const floors = TYPE_FLOORS[format];
  const L = LAYOUT[format];
  const s = Math.max(SHRINK, floors.label_min / L.label, floors.text_min / Math.max(1, mythSize));
  return Math.min(1, Math.ceil(s * 10000) / 10000);
}

/**
 * The fixed boxes of a format (pure; whole stage px): `band` (where everything is laid out, top to bottom), `heading`
 * (16:9: the heading's width, the `top-band` region's), `column` (16:9: the supporting lines' column, `left-55`; 9:16:
 * the cards' text column), `myth` / `fact` (16:9: the row's two grid spans; 9:16: the card box, the text column grown
 * by the card's padding and border, both cards) and `panel` (16:9: `right-45` beside supporting lines, else the grid
 * box - its x range; its height is the layout's; 9:16: null).
 * @param {'landscape' | 'portrait'} format @param {Zones} zones @param {boolean} [beside]   16:9: supporting lines
 *   stand left of the panel
 * @returns {{band: Box, heading: Box, column: Box, myth: Box, fact: Box, panel: Box | null}}
 */
export function boxes(format, zones, beside = true) {
  const L = LAYOUT[format];
  if (format === 'portrait') {
    const col = zones.textColumn;
    const band = { x0: col.x0, y0: zones.title.y0, x1: col.x1, y1: col.y1 };
    const card = { x0: col.x0 - L.pad - BORDER_PX, y0: band.y0, x1: col.x1 + L.pad, y1: band.y1 };
    return { band, heading: { ...band }, column: { ...col }, myth: card, fact: { ...card }, panel: null };
  }
  const at = { format };
  const grid = GRID.landscape.box;
  const band = { x0: grid.x0, y0: zones.title.y0, x1: grid.x1, y1: zones.body.y1 };
  const top = region(at, 'top-band');
  const left = region(at, 'left-55');
  const right = region(at, 'right-45');
  const myth = gridSpan(/** @type {number[]} */ (L.mythCols)[0], /** @type {number[]} */ (L.mythCols)[1]);
  const fact = gridSpan(/** @type {number[]} */ (L.factCols)[0], /** @type {number[]} */ (L.factCols)[1]);
  return {
    band,
    heading: { x0: top.x0, y0: band.y0, x1: top.x1, y1: band.y1 },
    column: { x0: left.x0, y0: band.y0, x1: left.x1, y1: band.y1 },
    myth: { ...myth, y0: band.y0, y1: band.y1 },
    fact: { ...fact, y0: band.y0, y1: band.y1 },
    panel: beside ? { x0: right.x0, y0: band.y0, x1: right.x1, y1: band.y1 } : { ...band },
  };
}

/**
 * The text width inside a card box `b` of `format` (its padding on both sides and its border on the left). Pure.
 * @param {'landscape' | 'portrait'} format @param {Box} b
 */
export function cardTextWidth(format, b) {
  return b.x1 - b.x0 - 2 * LAYOUT[format].pad - BORDER_PX;
}

/**
 * This template's text slots (templates.py SlotSpec, what textfit.py fits; plan §1.6, §1.11): slot -> format ->
 * variant -> {w, max_lines, size, role, face, weight, line_height, prefer_lines}. No look variants, so `default`
 * only. `heading` / `support` / `terminal` exist in 16:9 only (9:16 draws none of them). A supporting line mixes its
 * bold term with its regular meaning; its slot is measured at the bold weight, an upper bound on its width. `terminal`
 * is a side panel's terminal card in `right-45` less its padding, the narrowest box it can be drawn in. Line heights
 * are the Latin ones (Indic text takes LINE_HEIGHT's Indic values). Pure.
 * @param {Zones} landscape @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  const L = LAYOUT.landscape;
  const P = LAYOUT.portrait;
  const land = boxes('landscape', landscape, true);
  const port = boxes('portrait', portrait);
  const col = land.column.x1 - land.column.x0;
  const headingW = land.heading.x1 - land.heading.x0;
  /** @param {number} w @param {number} size */
  const label = (w, size) => ({
    w, max_lines: 1, size: [size, size], role: 'label', face: 'body', weight: BOLD, line_height: LINE_HEIGHT.label,
    prefer_lines: null,
  });
  /** @param {number} w @param {{max: number, min: number}} s @param {number} weight */
  const body = (w, s, weight) => ({
    w, max_lines: null, size: [s.min, s.max], role: 'body', face: 'body', weight, line_height: LINE_HEIGHT.body.latin,
    prefer_lines: null,
  });
  const H = /** @type {{max: number, min: number, lines: number, prefer: number}} */ (L.heading);
  const panel = /** @type {Box} */ (land.panel);
  return {
    heading: {
      landscape: {
        default: {
          w: headingW, max_lines: H.lines, size: [H.min, H.max], role: 'title', face: 'display', weight: BOLD,
          line_height: LINE_HEIGHT.heading.latin, prefer_lines: H.prefer,
        },
      },
    },
    support: { landscape: { default: body(col, /** @type {{max: number, min: number}} */ (L.support), BOLD) } },
    label: {
      landscape: { default: label(cardTextWidth('landscape', land.myth), L.label) },
      portrait: { default: label(cardTextWidth('portrait', port.myth), P.label) },
    },
    myth: {
      landscape: { default: body(cardTextWidth('landscape', land.myth), L.myth, L.weight) },
      portrait: { default: body(cardTextWidth('portrait', port.myth), P.myth, P.weight) },
    },
    fact: {
      landscape: { default: body(cardTextWidth('landscape', land.fact), L.fact, L.weight) },
      portrait: { default: body(cardTextWidth('portrait', port.fact), P.fact, P.weight) },
    },
    terminal: {
      landscape: {
        default: {
          w: panel.x1 - panel.x0 - 2 * PANEL_TERM.landscape.pad, max_lines: null,
          size: [PANEL_TERM.landscape.min, PANEL_TERM.landscape.max], role: 'body', face: 'mono',
          weight: PANEL_TERM.landscape.weight, line_height: PANEL_TERM.landscape.lh, prefer_lines: null,
        },
      },
    },
  };
}

/**
 * Every TeX string the template renders for the scene (the static `tex` hook; render.js converts them before `ready`):
 * the `$...$` spans of the heading, of each supporting term and meaning and of the belief and its correction, inline
 * (`richWords` mounts each with display off).
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  const { myth, supports, heading } = sceneParts(scene);
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  /** @param {string | null | undefined} text */
  const add = (text) => {
    if (typeof text !== 'string' || !text.includes('$')) return;
    for (const tex of mathSpans(text)) if (tex.trim()) out.push({ tex, display: false });
  };
  if (heading) add(heading.text);
  for (const item of supports) {
    add(item.term);
    add(item.text);
  }
  if (myth) {
    add(myth.text);
    add(myth.justification);
  }
  return out;
}

// ---------------------------------------------------------------------------------------------
// Building
// ---------------------------------------------------------------------------------------------

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.mfFallback);

/**
 * One of the two cards: its dim group, the card, its label, the statement's text element (what is fitted and
 * reported), the inline run inside it (whose line boxes the strike follows) and its fit.
 * @typedef {object} Card
 * @property {'myth' | 'fact'} part
 * @property {HTMLElement} el          the dim group
 * @property {HTMLElement} card
 * @property {HTMLElement} label       the label's text
 * @property {HTMLElement} text        the statement (block)
 * @property {HTMLElement} inline      the statement's inline run
 * @property {HTMLElement[]} words
 * @property {boolean[]} tex
 * @property {number} lh
 * @property {import('./_shared.js').ItemStateParts | null} parts   the myth's strike
 * @property {{min: number, max: number}} range
 * @property {number} size
 * @property {number} lineOverflow
 * @property {number} overflow
 * @property {number} height
 * @property {number} dx               the myth's offset before the refutation (16:9: centred on the grid)
 * @property {number} dy               (9:16: centred in the band)
 * @property {number} alpha            its opacity now (entrance x dim)
 * @property {number} scale            its scale now
 * @property {boolean} struck
 * @property {string} plain
 */

/**
 * A heading or a supporting line: its dim group, the text element (fitted, reported), its mask and its fit.
 * @typedef {object} Line
 * @property {BoardItem} item
 * @property {HTMLElement} el          the dim group
 * @property {HTMLElement} text
 * @property {HTMLElement | null} mask
 * @property {HTMLElement} box         what the stack measures (the mask, else the text)
 * @property {HTMLElement[]} fields    the item's fields in their order (cue.term's targets)
 * @property {HTMLElement[]} words
 * @property {boolean[]} tex
 * @property {number} lh
 * @property {string | null} scrim
 * @property {{min: number, max: number}} range
 * @property {number} size
 * @property {number} lineOverflow
 * @property {number} overflow
 * @property {number} enter
 * @property {number} alpha
 * @property {string} plain
 */

/** @type {TemplateModule} */
export default {
  id: ID,

  /**
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format } = ctx;
    if (!drawsScene(scene, format)) {
      // a scene this template does not draw whole: board_generic draws it, every hook handed on
      const inner = boardGeneric.build(ctx);
      inner.mfFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const { myth, supports, heading } = sceneParts(scene);
    const item = /** @type {BoardItem} */ (myth);
    const fixes = fixesOf(ctx.look);
    const masked = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    const onInk = ctx.onBackground.ink;
    const ink = ctx.roles.color('ink');
    const ink2 = ctx.roles.color('ink2');
    const surface = ctx.roles.color('surface');
    const warn = ctx.roles.color('warn');
    const ok = ctx.roles.color('ok');
    /** @type {Map<string, number>} */
    const enter = new Map(itemTimings(scene).map((x) => [x.id, x.enter]));
    const root = h('div', {
      class: ['dyn-mf', `dyn-mf--${format}`],
      style: { position: 'absolute', left: '0px', top: '0px', width: `${ctx.zones.width}px`, height: `${ctx.zones.height}px` },
    });

    // the heading (16:9; the display face, R-53's scrim remedy behind it when preflight asked, key_terms' rule)
    /** @type {Line | null} */
    let head = null;
    if (heading && L.heading) {
      const textValue = String(heading.text || '');
      const lh = lineHeight('heading', textValue);
      const scrim = fixes.slots.heading && fixes.slots.heading.scrim ? ctx.roles.color('scrim') : null;
      const text = h('h2', {
        class: 'dyn-mf-heading', dir: 'auto',
        style: {
          margin: '0px', fontFamily: 'var(--dyn-font-display)', fontWeight: String(BOLD), lineHeight: String(lh),
          overflowWrap: 'anywhere', color: onInk,
        },
      });
      if (scrim) {
        Object.assign(text.style, { background: scrim, borderRadius: `${SPACE[0]}px` });
      }
      const { words, tex } = richWords(ctx, text, textValue);
      const mask = masked ? revealMask(text) : null;
      const el = group('dyn-mf-heading-group', mask || text);
      el.style.position = 'absolute';
      root.appendChild(el);
      head = {
        item: heading, el, text, mask, box: mask || text, fields: [text], words, tex, lh, scrim,
        range: { min: L.heading.min, max: L.heading.max }, size: L.heading.max, lineOverflow: 0, overflow: 0,
        enter: enter.get(heading.id) || 0, alpha: 0, plain: plainText(textValue),
      };
    }

    // the supporting lines (16:9): "term — meaning", the term bold, on the background (or the scrim preflight asked
    // for), one dim group each
    const supportScrim = fixes.slots.support && fixes.slots.support.scrim ? ctx.roles.color('scrim') : null;
    /** @type {Line[]} */
    const lines = L.support ? supports.map((def) => {
      const termValue = typeof def.term === 'string' ? def.term : '';
      const meaningValue = typeof def.text === 'string' ? def.text : '';
      const lh = lineHeight('body', `${termValue} ${meaningValue}`);
      const text = h('div', {
        class: 'dyn-mf-support', dir: 'auto', dataset: { item: def.id },
        style: {
          fontFamily: 'var(--dyn-font-body)', fontWeight: String(L.weight), lineHeight: String(lh),
          overflowWrap: 'anywhere', color: onInk,
        },
      });
      if (supportScrim) {
        Object.assign(text.style, { background: supportScrim, borderRadius: `${SPACE[0]}px` });
      }
      /** @type {HTMLElement[]} */
      const fields = [];
      /** @type {HTMLElement[]} */
      const words = [];
      /** @type {boolean[]} */
      const tex = [];
      if (filled(termValue)) {
        const termEl = h('span', { class: 'dyn-mf-support-term', style: { fontWeight: String(BOLD) } });
        const w = richWords(ctx, termEl, termValue);
        text.appendChild(termEl);
        fields.push(termEl);
        words.push(...w.words);
        tex.push(...w.tex);
      }
      if (filled(meaningValue)) {
        if (fields.length) text.appendChild(document.createTextNode(SUPPORT_SEPARATOR));
        const meaningEl = h('span', { class: 'dyn-mf-support-meaning' });
        const w = richWords(ctx, meaningEl, meaningValue);
        text.appendChild(meaningEl);
        fields.push(meaningEl);
        words.push(...w.words);
        tex.push(...w.tex);
      }
      const mask = masked ? revealMask(text) : null;
      const el = group('dyn-mf-support-group', mask || text);
      el.style.position = 'absolute';
      root.appendChild(el);
      const S = /** @type {{max: number, min: number}} */ (L.support);
      const plain = [termValue, meaningValue].filter(filled).map((x) => plainText(x)).join(SUPPORT_SEPARATOR);
      return {
        item: def, el, text, mask, box: mask || text, fields, words, tex, lh, scrim: supportScrim,
        range: { min: S.min, max: S.max }, size: S.max, lineOverflow: 0, overflow: 0, enter: enter.get(def.id) || 0,
        alpha: 0, plain,
      };
    }) : [];

    // the two cards: a label row (icon + label) over the statement, on the card surface with a content-role edge
    /**
     * @param {'myth' | 'fact'} part @param {string} value @param {string} tone
     * @returns {Card}
     */
    const makeCard = (part, value, tone) => {
      const range = part === 'myth' ? L.myth : L.fact;
      const lh = lineHeight('body', value);
      const labelText = h('span', { class: 'dyn-mf-label-text', text: part === 'myth' ? LABELS.myth : LABELS.fact });
      const labelRow = h('div', {
        class: ['dyn-mf-label', `dyn-mf-label--${part}`],
        style: {
          display: 'flex', alignItems: 'center', gap: '0.3em', fontSize: `${L.label}px`, fontWeight: String(BOLD),
          lineHeight: String(LINE_HEIGHT.label), color: tone, whiteSpace: 'nowrap',
        },
      }, icon(part === 'myth' ? 'warning' : 'check', { class: 'dyn-icon' }), labelText);
      const inline = h('span', { class: 'dyn-mf-run' });
      const { words, tex } = richWords(ctx, inline, value);
      const text = h('div', {
        class: ['dyn-mf-statement', `dyn-mf-statement--${part}`], dir: 'auto',
        style: { fontWeight: String(L.weight), lineHeight: String(lh), overflowWrap: 'anywhere', color: ink },
      }, inline);
      // the strike's host: positioned, holding the statement and (the myth) its strike layer
      const host = h('div', { class: 'dyn-mf-statement-host', style: { position: 'relative', marginTop: `${L.labelGap}px` } }, text);
      const parts = part === 'myth' ? itemStateParts(host) : null;
      const card = h('div', {
        class: ['dyn-mf-card', `dyn-mf-card--${part}`], dataset: { item: item.id },
        style: {
          position: 'absolute', boxSizing: 'border-box', borderRadius: `${RADIUS.card}px`, background: surface,
          padding: `${L.pad}px`, boxShadow: String(LAYOUT_TOKENS.shadow[part === 'myth' ? '1' : '2']),
          fontFamily: 'var(--dyn-font-body)', color: ink,
          transformOrigin: format === 'portrait' ? '50% 0%' : '0% 0%',
        },
      }, labelRow, host);
      card.style.borderLeft = `${BORDER_PX}px solid ${tone}`;
      const el = group(`dyn-mf-${part}-group`, card);
      Object.assign(el.style, { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' });
      return {
        part, el, card, label: labelText, text, inline, words, tex, lh, parts, range: { min: range.min, max: range.max },
        size: range.max, lineOverflow: 0, overflow: 0, height: 0, dx: 0, dy: 0, alpha: 0, scale: 1, struck: false,
        plain: plainText(value),
      };
    };
    const mythCard = makeCard('myth', String(item.text || ''), warn);
    const factCard = makeCard('fact', String(item.justification || ''), ok);
    root.appendChild(mythCard.el);
    root.appendChild(factCard.el);

    // the scene's panel (16:9 only; `_shared.js` builds, lays out, paints and reports it)
    /** @type {import('./_shared.js').PanelParts | null} */
    let panelParts = null;
    const panel = panelFor(scene, format, ctx.mode);
    if (panel) {
      panelParts = buildPanel(ctx, panel);
      root.appendChild(panelParts.el);
    }

    ctx.root.appendChild(root);
    const mythEnter = enter.get(item.id) || 0;
    const k = seconds(ctx, 1);
    return {
      root, head, lines, myth: mythCard, fact: factCard, panel: panelParts, item, ink, ink2, surface, warn, ok,
      onInk, enter: mythEnter, times: refuteTimes(scene, item.id, mythEnter, k), scale: 1, rowTop: 0,
    };
  },

  /**
   * Measure and fit (idempotent; it runs again when a late face arrives): every text at the largest size its room
   * allows (`_shared.js fitLines`), then, while the composition is taller than the band, the supporting lines step
   * down first, then the belief, then the correction, then the heading (the headline keeps its size longest); then the
   * shrink scale, the places, the panel's box and the strike's lines. What still does not fit is reported through
   * `texts()` as overflow (preflight blocks with "Shorten text"); nothing is dropped. `update` only paints.
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    if (isFallback(ctx)) {
      if (typeof boardGeneric.layout === 'function') boardGeneric.layout(ctx);
      return;
    }
    const v = ctx.view;
    const { format, zones } = ctx;
    const L = LAYOUT[format];
    const portrait = format === 'portrait';
    /** @type {Card} */
    const myth = v.myth;
    /** @type {Card} */
    const fact = v.fact;
    /** @type {Line[]} */
    const lines = v.lines;
    /** @type {Line | null} */
    const head = v.head;
    const b = boxes(format, zones, lines.length > 0);
    const bandH = b.band.y1 - b.band.y0;

    // the cards' boxes (their width; their height is their content's), the heading's and the lines' columns
    for (const [c, box] of /** @type {[Card, Box][]} */ ([[myth, b.myth], [fact, b.fact]])) {
      Object.assign(c.card.style, { left: `${box.x0}px`, top: `${box.y0}px`, width: `${box.x1 - box.x0}px`, height: 'auto' });
    }
    if (head) {
      Object.assign(head.el.style, { left: `${b.heading.x0}px`, top: `${b.band.y0}px`, width: `${b.heading.x1 - b.heading.x0}px` });
    }
    for (const line of lines) {
      Object.assign(line.el.style, { left: `${b.column.x0}px`, top: `${b.band.y0}px`, width: `${b.column.x1 - b.column.x0}px` });
    }

    // each text on its own: the largest 4 px size whose lines fit the room there is (_shared.js fitLines)
    const chrome = 2 * L.pad + Math.round(L.label * LINE_HEIGHT.label) + L.labelGap;
    for (const c of [myth, fact]) {
      const range = slotRange(ctx, c.part, { min: c.range.min, max: c.range.max, role: 'body' });
      c.range = range;
      const room = Math.max(1, Math.floor((bandH - chrome) / (range.min * c.lh)));
      const fit = fitLines(c.text, { max: range.max, min: range.min, lines: room, lh: c.lh, step: SIZE_STEP }, c.words, c.tex);
      c.size = fit.size;
      c.lineOverflow = fit.overflow;
    }
    if (head && L.heading) {
      const range = slotRange(ctx, 'heading', { min: L.heading.min, max: L.heading.max, role: 'title' });
      head.range = range;
      const fit = fitLines(head.text, {
        max: range.max, min: range.min, lines: L.heading.lines, prefer: L.heading.prefer, lh: head.lh, step: SIZE_STEP,
      }, head.words, head.tex);
      head.size = fit.size;
      head.lineOverflow = fit.overflow;
    }
    if (lines.length && L.support) {
      const range = slotRange(ctx, 'support', { min: L.support.min, max: L.support.max, role: 'body' });
      // one size for every supporting line (no two set differently): the smallest of their own fits
      let size = range.max;
      for (const line of lines) {
        line.range = range;
        const room = Math.max(1, Math.floor(bandH / (range.min * line.lh)));
        const fit = fitLines(line.text, { max: range.max, min: range.min, lines: room, lh: line.lh, step: SIZE_STEP }, line.words, line.tex);
        line.lineOverflow = fit.overflow;
        size = Math.min(size, fit.size);
      }
      for (const line of lines) {
        line.size = size;
        line.text.style.fontSize = `${size}px`;
      }
    }

    // 16:9: the heading on the band's top, then (under it, headingGap below) the lines, then (rowGap below them) the row
    const headHeight = () => (head ? head.box.offsetHeight : 0);
    const linesHeight = () => lines.reduce((a, line, i) => a + (i ? L.supportGap : 0) + line.box.offsetHeight, 0);
    // the composition's height now (16:9: the panel takes whatever height is left, so it is not counted)
    const need = () => {
      myth.height = myth.card.offsetHeight;
      fact.height = fact.card.offsetHeight;
      if (portrait) {
        const s = shrinkScale(format, myth.size);
        return Math.max(myth.height, Math.ceil(s * myth.height) + L.gap + fact.height);
      }
      const headH = headHeight();
      return (headH ? headH + L.headingGap : 0) + (lines.length ? linesHeight() + L.rowGap : 0)
        + Math.max(myth.height, fact.height);
    };
    // too tall: the supporting lines give way first, then the belief, then the correction, then the heading (the
    // headline keeps its size longest; the correction stays larger than the belief: its range starts above)
    /** @type {{size: () => number, min: () => number, set: (s: number) => void}[]} */
    const order = [];
    if (lines.length) {
      order.push({
        size: () => lines[0].size, min: () => lines[0].range.min,
        set: (s) => { for (const line of lines) { line.size = s; line.text.style.fontSize = `${s}px`; } },
      });
    }
    for (const c of [myth, fact]) {
      order.push({ size: () => c.size, min: () => c.range.min, set: (s) => { c.size = s; c.text.style.fontSize = `${s}px`; } });
    }
    if (head) {
      order.push({ size: () => head.size, min: () => head.range.min, set: (s) => { head.size = s; head.text.style.fontSize = `${s}px`; } });
    }
    for (let guard = 0; guard < 200 && need() > bandH; guard++) {
      const next = order.find((o) => o.size() > o.min());
      if (!next) break;
      next.set(Math.max(next.min(), next.size() - SIZE_STEP));
    }
    need();
    v.scale = shrinkScale(format, myth.size);

    // places
    if (portrait) {
      // the pair (the shrunk myth over the fact) centred in the band; before the refutation the myth stands alone,
      // centred, and the fact pushes it up to its place (§4.3)
      const top = b.band.y0;
      const shrunkH = Math.ceil(v.scale * myth.height);
      const pairTop = top + Math.max(0, Math.round((bandH - (shrunkH + L.gap + fact.height)) / 2));
      myth.card.style.top = `${pairTop}px`;
      myth.dx = 0;
      myth.dy = top + Math.max(0, Math.round((bandH - myth.height) / 2)) - pairTop;
      const factTop = pairTop + shrunkH + L.gap;
      fact.card.style.top = `${factTop}px`;
      myth.overflow = Math.max(myth.lineOverflow, myth.height - bandH);
      fact.overflow = Math.max(fact.lineOverflow, factTop + fact.height - b.band.y1);
      v.rowTop = pairTop;
    } else {
      const headH = headHeight();
      const linesH = linesHeight();
      const rowH = Math.max(myth.height, fact.height);
      // under the heading (the band's top without one)
      const under = b.band.y0 + (headH ? headH + L.headingGap : 0);
      let linesTop;
      let rowTop;
      if (v.panel) {
        // the row stands on the band's bottom; the lines under the heading, and the panel takes the height left
        linesTop = under;
        rowTop = Math.max(lines.length ? under + linesH + L.rowGap : under, b.band.y1 - rowH);
      } else {
        // the lines and the row together, centred in the band under the heading (the row alone: in the band)
        const blockH = (lines.length ? linesH + L.rowGap : 0) + rowH;
        linesTop = under + Math.max(0, Math.round((b.band.y1 - under - blockH) / 2));
        rowTop = linesTop + (lines.length ? linesH + L.rowGap : 0);
      }
      linesTop = Math.round(linesTop);
      rowTop = Math.round(rowTop);
      let y = linesTop;
      lines.forEach((line, i) => {
        if (i) y += L.supportGap;
        line.el.style.top = `${y}px`;
        y += line.box.offsetHeight;
      });
      v.rowTop = rowTop;
      myth.card.style.top = `${rowTop}px`;
      fact.card.style.top = `${rowTop}px`;
      // before the refutation the myth stands alone, centred on the grid
      const grid = GRID.landscape.box;
      myth.dx = Math.round((grid.x0 + grid.x1) / 2 - (b.myth.x0 + (b.myth.x1 - b.myth.x0) / 2));
      myth.dy = 0;
      myth.overflow = Math.max(myth.lineOverflow, rowTop + myth.height - b.band.y1);
      fact.overflow = Math.max(fact.lineOverflow, rowTop + fact.height - b.band.y1);
      // text above the row past the band's bottom (the row under it reports its own overflow too)
      if (head) head.overflow = Math.max(head.lineOverflow, b.band.y0 + headH - b.band.y1);
      for (const line of lines) line.overflow = Math.max(line.lineOverflow, linesTop + linesH - b.band.y1);
      // the panel: beside the lines (across the grid box when there are none), under the heading, down to the row
      if (v.panel && b.panel) {
        layoutPanel(ctx, v.panel, {
          x0: b.panel.x0, y0: under, x1: b.panel.x1, y1: Math.max(under + 1, rowTop - L.rowGap),
        });
      }
    }
    // the strike's lines, through the belief's line boxes, measured with the card's painted transform lifted (the
    // lines live inside the card, in its untransformed coordinates); the same value is written back, so the painter's
    // record of it stays true
    const painted = myth.card.style.transform;
    const lift = painted !== '' && painted !== 'none';
    if (lift) myth.card.style.transform = 'none';
    if (myth.parts) layoutStrike(ctx, myth.parts, myth.inline);
    if (lift) myth.card.style.transform = painted;
  },

  /**
   * Paint scene time t: the heading and supporting lines on their beats, the myth card's entrance, the strike, the
   * myth giving way and the fact coming in, and the panel. A pure function of t: every property painted on an element
   * is painted on every frame, with a value (never '').
   * @param {TemplateContext} ctx
   * @param {number} t
   * @param {import('../../shared/types.js').SceneState} state
   */
  update(ctx, t, state) {
    if (isFallback(ctx)) {
      boardGeneric.update(ctx, t, state);
      return;
    }
    const v = ctx.view;
    const p = ctx.paint;
    const L = LAYOUT[ctx.format];
    const still = ctx.reducedMotion;
    // the heading and the supporting lines, each with the personality's text entrance on its own beat
    for (const line of /** @type {Line[]} */ ([...(v.head ? [v.head] : []), ...v.lines])) {
      const e = enterAt(ctx, t, line.enter, TEXT_ENTER);
      line.alpha = e;
      paintTextEntrance(ctx, line.text, line.mask, e);
      p.style(line.box, 'visibility', e > 0.001 ? 'visible' : 'hidden');
    }
    // the myth: in on its beat; struck; then pushed aside (16:9) or up (9:16), shrunk and dimmed as the fact comes in
    /** @type {Card} */
    const myth = v.myth;
    const e = enterAt(ctx, t, v.enter, CARD_ENTER);
    const k = enterAt(ctx, t, v.times.strike, STRIKE_DRAW);
    const m = enterAt(ctx, t, v.times.shrink, SHRINK_SECONDS);
    const scale = 1 - (1 - v.scale) * m;
    const x = still ? 0 : Math.round(myth.dx * (1 - m));
    const y = (still ? 0 : Math.round(myth.dy * (1 - m))) + (still || e >= 1 ? 0 : Math.round((1 - e) * CARD_RISE));
    myth.alpha = e * (1 - (1 - DIM) * m);
    myth.scale = scale;
    myth.struck = k > 0;
    p.style(myth.card, 'opacity', fx(myth.alpha));
    const sc = fx(scale, 4);
    const shrunk = sc !== fx(1, 4);
    p.style(myth.card, 'transform', x || y || shrunk ? `translate(${x}px, ${y}px)${shrunk ? ` scale(${sc})` : ''}` : 'none');
    p.style(myth.card, 'visibility', e > 0.001 ? 'visible' : 'hidden');
    paintItemState(ctx, myth.text, myth.parts, k > 0 ? 'struck' : null, k, { weight: L.weight, ink: v.ink });
    // the fact: in from the right (16:9) or from below (9:16), the scene's final frame
    /** @type {Card} */
    const fact = v.fact;
    const f = enterAt(ctx, t, v.times.fact, FACT_ENTER);
    const travel = FACT_TRAVEL[ctx.format];
    const fxp = still || f >= 1 ? 0 : Math.round(travel.x * (1 - f));
    const fyp = still || f >= 1 ? 0 : Math.round(travel.y * (1 - f));
    fact.alpha = f;
    fact.scale = 1;
    p.style(fact.card, 'opacity', fx(f));
    p.style(fact.card, 'transform', fxp || fyp ? `translate(${fxp}px, ${fyp}px)` : 'none');
    p.style(fact.card, 'visibility', f > 0.001 ? 'visible' : 'hidden');
    // the scene's panel: its entrance, its medium and a terminal's output lines (`_shared.js paintPanel`)
    if (v.panel) paintPanel(ctx, v.panel, t, state);
  },

  /**
   * Where a look_here points (plan §1.4, §1.5, §1.7), on whole stage px as painted now (read after `update`): the
   * heading or a supporting line (`<item>`, text); the misconception (`<item>` or `<item>:term:<n>`) - the fact card
   * once it is in, the myth card before (a shape, so its outline is drawn round it, cues.js shapeArea); `panel` and
   * `panel:row:<n>` through `_shared.js panelFocusRect`. Null for anything else, or for something not on screen.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    const v = ctx.view;
    const parsed = parseTarget(String(target || ''));
    if (!parsed) return null;
    if (parsed.form === 'panel' || parsed.form === 'panel:row') return v.panel ? panelFocusRect(ctx, v.panel, String(target)) : null;
    if (parsed.form !== 'item' && parsed.form !== 'item:term') return null;
    const id = String(parsed.item);
    /** @type {Element | null} */
    let el = null;
    /** @type {Element | null} */
    let holder = null;
    let radius = RADIUS.card;
    /** @type {'text' | 'shape'} */
    let kind = 'shape';
    if (id === v.item.id) {
      /** @type {Card} */
      const c = v.fact.alpha > 0.01 ? v.fact : v.myth;
      el = c.alpha > 0.01 ? c.card : null;
      holder = c.el;
    } else {
      /** @type {Line | undefined} */
      const line = [...(v.head ? [v.head] : []), ...v.lines].find((x) => x.item.id === id);
      if (line) {
        el = line.alpha > 0.01 ? line.text : null;
        holder = line.el;
        radius = SPACE[0];
        kind = 'text';
      }
    }
    const r = el ? ctx.rectOf(el) : null;
    if (!r || !(r.w > 0 && r.h > 0)) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    return {
      rect: { x: x0, y: y0, w: Math.round(r.x + r.w) - x0, h: Math.round(r.y + r.h) - y0 }, shape: 'rect', radius,
      el: /** @type {Element} */ (holder || el), kind,
    };
  },

  /**
   * The elements holding an item's text, where cue.term finds its [[term]] spans (`.rt-k`, `_shared.js richWords`),
   * in the item's field order: the heading's text; a supporting definition's term then its meaning; the
   * misconception's belief then its correction (two cards, so `<item>:term:<n>` reaches the correction's terms too:
   * the B2a open item's array form). Null for anything else.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const v = ctx.view;
    const id = String(itemId);
    if (id === v.item.id) return [v.myth.text, v.fact.text];
    const line = /** @type {Line[]} */ ([...(v.head ? [v.head] : []), ...v.lines]).find((x) => x.item.id === id);
    if (!line || !line.fields.length) return null;
    return line.fields.length === 1 ? line.fields[0] : line.fields;
  },

  /**
   * The content groups a look_here dims (all but the target's): the heading, every supporting line, the two cards and
   * the panel. The same elements at every t; this template never writes their opacity (it animates the parts inside
   * them).
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.head ? v.head.el : null, ...v.lines.map((/** @type {Line} */ x) => x.el), v.myth.el, v.fact.el,
      v.panel ? v.panel.el : null].filter(Boolean);
  },

  /**
   * The morph anchor (plan §1.4): none yet (B2f pairs the templates that have one).
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: the only medium this template shows is its panel's, in slot `panel` (16:9): the largest box the panel is
   * ever laid out in - `right-45` (or the grid box with nothing to its left) over the whole band - so it only ever
   * downscales (§P). Null in 9:16 (no panel) and for any other slot; a scene drawn by board_generic gets
   * board_generic's box.
   * @param {TimedScene} scene
   * @param {Zones} zones
   * @param {'live' | 'render'} mode
   * @param {string | undefined} slot
   * @param {import('../core.js').Fixes} fixes
   * @returns {{w: number, h: number} | null}
   */
  mediaBox(scene, zones, mode, slot, fixes) {
    if (!drawsScene(scene, zones.format)) {
      return typeof boardGeneric.mediaBox === 'function' ? boardGeneric.mediaBox(scene, zones, mode, slot, fixes) : null;
    }
    if (zones.format !== 'landscape' || !panelFor(scene, 'landscape', mode)) return null;
    const b = boxes('landscape', zones, sceneParts(scene).supports.length > 0);
    return b.panel ? panelMediaBox(scene, b.panel, slot) : null;
  },

  /**
   * Static: every TeX string this template renders for the scene (`texOf`; the same in both formats).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} fmt
   */
  tex(scene, look, fmt) {
    if (!drawsScene(scene, fmt)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene);
  },

  /**
   * The text on screen (preflight, §7.12): the heading (slot `heading`) and the supporting lines (`support`) on the
   * background or the scrim preflight asked for; each card's label (`label`) in its tone and statement (`myth`,
   * `fact`) on the card surface, at the size painted now (a shrunk card's text is reported at its shrunk size, which
   * `shrinkScale` holds at R-29's floors), with the card's opacity as its alpha (a dimmed myth is not critical); and
   * the panel's own text (`_shared.js panelTexts`).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
    /** @type {TextEntry[]} */
    const out = [];
    /** @param {string | null} scrim */
    const bgOf = (scrim) => (scrim ? scrimmedBg(scrim, ctx.onBackground, v.onInk) : ctx.onBackground.bg);
    if (v.head) {
      const hd = /** @type {Line} */ (v.head);
      out.push({
        el: hd.text, role: 'title', slot: 'heading', critical: true, ink: v.onInk, bg: bgOf(hd.scrim), alpha: hd.alpha,
        overflow: hd.overflow, size: hd.size, min: hd.range.min, weight: BOLD, text: hd.plain,
      });
    }
    for (const line of /** @type {Line[]} */ (v.lines)) {
      out.push({
        el: line.text, role: 'body', slot: 'support', critical: true, ink: v.onInk, bg: bgOf(line.scrim),
        alpha: line.alpha, overflow: line.overflow, size: line.size, min: line.range.min, weight: L.weight,
        text: line.plain,
      });
    }
    for (const c of /** @type {Card[]} */ ([v.myth, v.fact])) {
      const alpha = Math.max(0, Math.min(1, c.alpha));
      out.push({
        el: c.label, role: 'label', slot: 'label', critical: true, ink: c.part === 'myth' ? v.warn : v.ok, bg: v.surface,
        alpha, size: L.label * c.scale, weight: BOLD, text: c.part === 'myth' ? LABELS.myth : LABELS.fact,
      });
      out.push({
        el: c.text, role: 'body', slot: c.part, critical: true, ink: c.struck ? v.ink2 : v.ink, bg: v.surface, alpha,
        overflow: c.overflow, size: c.size * c.scale, min: c.range.min * c.scale, weight: L.weight, text: c.plain,
      });
    }
    if (v.panel) out.push(...panelTexts(ctx, v.panel));
    return out;
  },
};
