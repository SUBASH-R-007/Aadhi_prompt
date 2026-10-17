// @ts-check
/**
 * `key_terms` (DYNAMIC_VIDEO_DESIGN §4.3 A, R1, class `definition`; B2 plan §1.4, §1.5, §1.11, §P; the pattern of
 * `docs/plans/b2/b2c_design_notes.md`): the pre-training of 2-4 component terms (R-05), one term per narration beat.
 *
 * Data (§4.3, templates.py `_fit_key_terms`): a `pretraining` BoardScene with 2-4 `definition` items (`term` <= 3
 * words, `text` the meaning <= 8 words, an `icon_hint` whose matched icon the resolver stores in `look.icons`, §4.12;
 * the icons are ALL OR NOTHING, B2c review V12: with one missing no card takes one, so the terms of a row stay on
 * one line)
 * and at most one `heading` (templates.py `_shape`'s `extra`). Nothing a timeline names is dropped: every item that is
 * not the heading is a term card, with its `term` and its meaning; in 16:9 at most PANEL_TERMS_MAX terms may stand
 * beside the scene's **side panel** (`_shared.js panelOf`: the picture, animation, terminal session or authored title
 * that illustrates the terms; in 9:16 drawn as its own beat before the terms), and a scene with a scene medium, with a
 * panel of a kind `_shared.js` does not draw, or with more terms than fit beside one in 16:9 (`drawsScene`) is
 * handed to board_generic whole, every hook passed on (list_build's and formula_focus's
 * convention). `_fit_key_terms` refuses exactly the same scenes, so the director never sends one here for the page to
 * draw as board_generic (B2c review CG-2, the demo review's D5).
 *
 * 16:9 (§4.3 "2-4 cards in a row (card width = (1728 - gutters) / n)"): the cards share the 12-column grid box
 * (x 96-1824, 1728 px) with the grid's 24 px gutters between them, so n cards are `(1728 - 24 (n - 1)) / n` px wide
 * (852 / 560 / 414 for 2 / 3 / 4). Each card is a `surface` card (radius 24, shadow 1) holding the item's stored icon
 * (the catalogue's 96 px card size), its term and its meaning. Every card takes the same height (the tallest card's),
 * so the row reads as one band, and the row is centred in the space under the heading. With a panel the cards share
 * the left 55% (x 96-1034) instead and the panel takes the right 45%: at PANEL_TERMS_MAX terms a card is then 457 px
 * wide, still wider than the narrowest card the slot sizes were fitted against (414 px), so the stored sizes stay
 * upper bounds (list_build's rule).
 *
 * 9:16 with a side panel (owner decision 4, 2026-10-16): the panel is its own short full-stage beat BEFORE the
 * terms, never above them (`_shared.js reelWindow`: from the scene's start until the first narrated term, 2-4 s), then
 * leaves, and the heading and the rows enter as below - so the rows keep the whole column and R-29's floor. Any number
 * of terms (2-4) may follow a reel panel; PANEL_TERMS_MAX is the 16:9 side-by-side limit only.
 *
 * 9:16 (§4.3 "stacked rows in the text column"): rows, not cards - the text column (x 192-780, the part of the stage
 * both critical boxes hold, R-59) is 588 px wide, which a card's padding and a 96 px icon would spend on chrome rather
 * than on the terms. Each row sets its term (with its icon inline before it on one baseline, a square of the
 * catalogue's `inline_em` of the term's own FITTED size, `inlineIcon`) over its meaning, on the background, one
 * under the other from the heading down. A reel therefore reads 2-3 terms
 * comfortably; four terms with full 8-word meanings do not fit at R-29's floors even here, where the template reports
 * the overflow (`texts()`) and preflight blocks with "Shorten text" rather than dropping or shrinking a term past its
 * floor (plan §1.4 "never drop content"; §4.3 calls the template "optional in 9:16", and R-25 keeps a reel to <= 3
 * items anyway).
 *
 * Type sizes (B2-demo proposal, pending the owner's review, on the precedent of deviation 31: below R-29's nominal
 * ranges, above its floors): 16:9 term 48-64 (R-29 headings 84-120 cannot be set in a 414 px card), meaning 40-48
 * (body 48-56); 9:16 term 44-64, meaning 40-48 (body 56-64) at weight 600, so R-29's 9:16 weight clause (text at
 * <= 64 px is at least 600) holds. Both texts step down together in 4 px steps (`sizeSteps`, board_generic's and
 * textfit.py's step) until every card fits; the sizes a scene is drawn at are one pair for the whole scene, so no two
 * terms are set differently.
 *
 * Choreography (§4.3, §4.8): one card per beat (state.js `itemTimings`: a `reveal` event wins over the beat, which
 * starts REVEAL_LEAD before its anchor word, R-17) - the card rises 32 px in 450 ms, the icon's strokes draw over
 * 400 ms (`pathLength="1"` plus a dash offset, so the draw is the same whatever the path's real length), and the term
 * enters with the personality's text entrance (`ctx.motion.entrance`) with the meaning 80 ms behind it. A `draw` event
 * on the item (templates.py `consumes`; events.py writes them from B2d) moves the icon's draw to its own time instead.
 * Template durations take the personality's scale (`ctx.motion.scale`, `_shared.js seconds` / `enterAt`); the cues keep
 * their own (cues.js).
 *
 * Emphasis is cues.js's (§4.2, plan §1.4/§1.5): `itemEl` hands it the item's term and meaning elements, in field order
 * (term, text), with their `[[term]]` `.rt-k` spans kept by `_shared.js richWords`, so `cue.term` on `<item>` sweeps
 * every term of the card and `<item>:term:<n>` the n-th across both fields; `focusRect` gives the card (16:9: a shape,
 * so its outline is drawn whole around it) and, through `_shared.js panelFocusRect`, the panel (`panel`) and a
 * terminal's n-th output line (`panel:row:<n>`); `dimGroups` the heading, every card and the panel. Nothing here writes `outline`,
 * `text-decoration`, `filter`, `backdrop-filter` or `will-change`, no structure role colours a term, and no clock,
 * randomness or `.timeline` is read (the source scan, web/tests/dynamic/sourceScan.test.js).
 *
 * Determinism (§7.4, plan §P): `update` is a pure function of t, every property it paints is painted on every frame
 * with a value (never ''), translates are whole pixels (`paintRise`), and the only per-frame geometry is the icon's
 * dash offset. `layout` measures on clones in a hidden host, so it never writes a property the painter owns.
 */

import { h } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { GRID, LAYOUT_TOKENS, fixesOf, fx } from '../core.js';
import { itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import {
  PANEL_TERM, RADIUS, buildPanel, drawsPanel, enterAt, fitLines, group, layoutPanel, paintPanel, paintRise,
  paintTextEntrance, panelFocusRect, panelMediaBox, panelOf, panelTexts, reelEntrances, reelPanelBox, reelWindow,
  region, revealMask, richWords, scrimmedBg, seconds, shownPanel, slotRange, storedIcon,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */

// ---------------------------------------------------------------------------------------------
// Motion (§4.3 key_terms, §4.8)
// ---------------------------------------------------------------------------------------------

/** §4.3: a card rises 32 px ... */
export const RISE = 32;
/** ... in 450 ms on its beat ... */
export const ITEM_ENTER = 0.45;
/** ... its icon's strokes draw in 400 ms ... */
export const ICON_DRAW = 0.4;
/** ... and its term and meaning are 80 ms apart. */
export const TEXT_DELAY = 0.08;
/** The heading's entrance (the cards' length, §4.8 "medium move 400-500 ms"). */
export const HEADING_ENTER = 0.45;

// ---------------------------------------------------------------------------------------------
// Layout (stage px; R-29 floors; the catalogue's grid, zones and layout tokens)
// ---------------------------------------------------------------------------------------------

/** The 16:9 card icon: the catalogue's card icon size (§4.12, 96 px; an A2 proposal there). */
export const ICON_SIZE = Number(LAYOUT_TOKENS.icon.card_px);
/** The 9:16 row icon: the catalogue's inline size, in the term's own em, so it sits in the term's line. */
export const ICON_INLINE_EM = Number(LAYOUT_TOKENS.icon.inline_em);
/** The space after a 9:16 inline icon, in the term's own em (a word space, so the icon reads as part of the line). */
export const ICON_GAP_EM = 0.3;

/**
 * The 9:16 inline icon at a term set in `size` px: its square side and the space after it, both whole px. The em
 * lengths are resolved HERE, against the term's fitted size, rather than written as `em` on the icon: the icon is a
 * sibling of the term element, so an `em` on it would resolve against the inherited base size, not the term's
 * 44-64 px (review D2). Pure.
 * @param {number} size
 * @returns {{icon: number, gap: number}}
 */
export function inlineIcon(size) {
  return { icon: Math.round(size * ICON_INLINE_EM), gap: Math.round(size * ICON_GAP_EM) };
}

/**
 * Write the inline icon's size into a row (`layout` on the real rows, `measureCard` on the clones, so both see the
 * same height): the host's box, the SVG's own box and the space after it, at a term of `size` px.
 * @param {HTMLElement | null} host @param {number} size
 */
export function sizeInlineIcon(host, size) {
  if (!host) return;
  const { icon, gap } = inlineIcon(size);
  host.style.width = `${icon}px`;
  host.style.height = `${icon}px`;
  host.style.marginRight = `${gap}px`;
  const el = /** @type {HTMLElement | null} */ (host.querySelector('.dyn-sicon'));
  if (el) {
    el.style.width = `${icon}px`;
    el.style.height = `${icon}px`;
  }
}
/** The gutter between the 16:9 cards: the grid's own (§2.5, 24 px), so the cards sit on the 12-column grid. */
export const CARD_GUTTER = Number(GRID.landscape.gutter);
/** A 16:9 card's shadow (§4.3 "soft shadow": the catalogue's level 1). */
export const CARD_SHADOW = String(LAYOUT_TOKENS.shadow['1']);
/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;

/** Line heights of the type scale (zones.py TYPE_SCALE, plan §2: Indic body >= 1.45, Indic heading >= 1.25;
 * key_terms.test.js pins them to the catalogue's `type.scale`). */
export const LINE_HEIGHT = Object.freeze({
  heading: Object.freeze({ latin: 1.15, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
});

/**
 * Per-format layout. `heading`: its size range, line limit and preferred lines (16:9 R-29 headings 84-120 capped at
 * board_generic's 96; 9:16 board_generic's 56-88, one line preferred). `term` / `meaning`: the two text sizes, which
 * step down together; `pad`: a 16:9 card's padding on every side (a 9:16 row has none: it is text on the background);
 * `iconGap`: between the icon and the term (16:9, where the icon is a block above it); `textGap`: between the term and
 * its meaning; `gap`: between the 9:16 rows (16:9 cards use CARD_GUTTER); `headingGap`: between the heading and the
 * cards; `meaningWeight`: the meaning's weight (9:16: R-29's weight clause, >= 600 at <= 64 px).
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    heading: Object.freeze({ max: 96, min: 84, lines: 2, prefer: 1 }),
    term: Object.freeze({ max: 64, min: 48, lines: 2 }),
    meaning: Object.freeze({ max: 48, min: 40 }),
    pad: 32,
    iconGap: 24,
    textGap: 8,
    gap: CARD_GUTTER,
    headingGap: 40,
    meaningWeight: 400,
    card: true,
  }),
  portrait: Object.freeze({
    heading: Object.freeze({ max: 88, min: 56, lines: 2, prefer: 1 }),
    term: Object.freeze({ max: 64, min: 44, lines: 2 }),
    meaning: Object.freeze({ max: 48, min: 40 }),
    pad: 0,
    iconGap: 0,
    textGap: 8,
    gap: 16,
    headingGap: 28,
    meaningWeight: 600,
    card: false,
  }),
});

/** The term's weight in both formats (a definition's term is set bold, §4.3 "term then meaning"). */
export const TERM_WEIGHT = 700;

/**
 * How many terms may stand beside the scene's panel in 16:9 (`drawsScene`; templates.py `KEY_TERMS_PANEL_MAX`
 * mirrors it). Two cards in the left 55% are 457 px wide, above the 414 px the slot sizes were fitted against;
 * three would be 296 px, which R-29's term floor (48 px) cannot be set in. A scene with more terms and a panel is
 * board_generic's whole.
 */
export const PANEL_TERMS_MAX = 2;

/**
 * The boxes of a format (pure; whole stage px): `heading` (where the heading is set), `cards` (the band the cards or
 * rows fill, from the title zone's top: `layout` moves its top under the heading), `visual` (the scene's panel, 16:9
 * only; null without one) and `bottom`. 16:9 cards end at the body box's bottom (y 900, board_generic's cards: 24 px
 * above the caption band) and span the grid box, or the left 55% beside a panel; 9:16 rows fill the text column
 * (x 192-780) down to y 1020.
 * @param {'landscape' | 'portrait'} format
 * @param {Zones} zones
 * @param {boolean} [panel]   the scene shows a side panel beside the cards (16:9 only)
 * @returns {{heading: Box, cards: Box, visual: Box | null, bottom: number}}
 */
export function boxes(format, zones, panel = false) {
  if (format === 'portrait') {
    const col = zones.textColumn;
    return {
      heading: { x0: zones.title.x0, y0: zones.title.y0, x1: zones.title.x1, y1: zones.title.y1 },
      cards: { x0: col.x0, y0: zones.title.y0, x1: col.x1, y1: col.y1 },
      visual: null,
      bottom: col.y1,
    };
  }
  const band = region({ format }, 'top-band');
  const bottom = zones.body.y1;
  const heading = { x0: band.x0, y0: zones.title.y0, x1: band.x1, y1: band.y1 };
  if (panel) {
    const left = region({ format }, 'left-55');
    const right = region({ format }, 'right-45');
    return {
      heading,
      cards: { x0: left.x0, y0: zones.title.y0, x1: left.x1, y1: bottom },
      visual: { x0: right.x0, y0: zones.title.y0, x1: right.x1, y1: bottom },
      bottom,
    };
  }
  const grid = GRID.landscape.box;
  return { heading, cards: { x0: grid.x0, y0: zones.title.y0, x1: grid.x1, y1: bottom }, visual: null, bottom };
}

/**
 * The width of one of `n` cards in the 16:9 row (§4.3: the band less the gutters between them, divided by n; whole
 * px, the remainder left at the right edge rather than spread over the cards, so every card is equal). `box`: the
 * band the cards share (the 12-column grid box by default; the left 55% beside a panel).
 * @param {number} n
 * @param {Box} [box]
 */
export function cardWidth(n, box) {
  const count = Math.max(1, n);
  const b = box || GRID.landscape.box;
  return Math.floor((b.x1 - b.x0 - CARD_GUTTER * (count - 1)) / count);
}

/**
 * A side panel's terminal card slot in a panel box `width` px wide (`_shared.js PANEL_TERM`; templates.py
 * `_panel_terminal_slot`).
 * @param {'landscape' | 'portrait'} fmt @param {number} width
 */
function terminalSlot(fmt, width) {
  const T = PANEL_TERM[fmt];
  return {
    w: width - 2 * T.pad, max_lines: null, size: [T.min, T.max], role: 'body', face: 'mono', weight: T.weight,
    line_height: T.lh, prefer_lines: null,
  };
}

/**
 * The text slots of this template (templates.py SlotSpec, what textfit.py fits; plan §1.6, §1.11):
 * slot -> format -> variant -> {w, max_lines, size, role, face, weight, line_height, prefer_lines}. key_terms has no
 * look variants, so every slot has only `default`. The 16:9 term and meaning widths are the NARROWEST card's (four
 * terms, 414 px less its padding), so a size fitted against them is an upper bound for a two- or three-term scene too
 * (list_build's rule for its split / centred widths). Line heights are the Latin ones; Indic text takes LINE_HEIGHT's
 * Indic values, which a measured fit must use. Pure.
 * @param {Zones} landscape @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  const L = LAYOUT.landscape;
  const P = LAYOUT.portrait;
  const land = boxes('landscape', landscape);
  const port = boxes('portrait', portrait);
  /** @param {{max: number, min: number, lines: number, prefer: number}} s @param {number} w */
  const heading = (s, w) => ({
    w, max_lines: s.lines, size: [s.min, s.max], role: 'title', face: 'display', weight: TERM_WEIGHT,
    line_height: LINE_HEIGHT.heading.latin, prefer_lines: s.prefer,
  });
  /** @param {{max: number, min: number, lines: number}} s @param {number} w */
  const term = (s, w) => ({
    w, max_lines: s.lines, size: [s.min, s.max], role: 'title', face: 'display', weight: TERM_WEIGHT,
    line_height: LINE_HEIGHT.heading.latin, prefer_lines: null,
  });
  /** @param {{max: number, min: number}} s @param {number} w @param {number} weight */
  const meaning = (s, w, weight) => ({
    w, max_lines: null, size: [s.min, s.max], role: 'body', face: 'body', weight,
    line_height: LINE_HEIGHT.body.latin, prefer_lines: null,
  });
  const landText = cardWidth(4) - 2 * L.pad;
  const portText = port.cards.x1 - port.cards.x0 - 2 * P.pad;
  const panelBand = /** @type {Box} */ (boxes('landscape', landscape, true).visual);
  return {
    heading: {
      landscape: { default: heading(L.heading, land.heading.x1 - land.heading.x0) },
      portrait: { default: heading(P.heading, port.heading.x1 - port.heading.x0) },
    },
    term: {
      landscape: { default: term(L.term, landText) },
      portrait: { default: term(P.term, portText) },
    },
    meaning: {
      landscape: { default: meaning(L.meaning, landText, L.meaningWeight) },
      portrait: { default: meaning(P.meaning, portText, P.meaningWeight) },
    },
    // a terminal panel's card, mono, in the panel's box less its padding (16:9 the right 45%; 9:16 the reel beat's
    // card in the text column, `_shared.js reelPanelBox`); `_shared.js layoutPanel` fits it on the page and
    // `_shared.js panelTexts` reports it in this slot
    terminal: {
      landscape: { default: terminalSlot('landscape', panelBand.x1 - panelBand.x0) },
      portrait: { default: terminalSlot('portrait', portrait.textColumn.x1 - portrait.textColumn.x0) },
    },
  };
}

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/** Indic scripts (Devanagari to Sinhala), whose text takes the taller line heights (plan §2). */
const INDIC = new RegExp(`[${['Devanagari', 'Bengali', 'Gurmukhi', 'Gujarati', 'Oriya', 'Tamil', 'Telugu', 'Kannada',
  'Malayalam', 'Sinhala'].map((x) => `\\p{Script=${x}}`).join('')}]`, 'u');

/**
 * The line height of `text` in type role `role` (LINE_HEIGHT: Indic text takes the Indic value).
 * @param {'heading' | 'body'} role @param {string} text
 */
export function lineHeight(role, text) {
  return INDIC.test(plainText(String(text || ''))) ? LINE_HEIGHT[role].indic : LINE_HEIGHT[role].latin;
}

/**
 * The scene's heading (its first `heading` item that has text) and its term items (every other item with a term or
 * text, in board order: definitions in practice, and nothing a timeline names is dropped).
 * @param {TimedScene} scene
 * @returns {{heading: BoardItem | null, terms: BoardItem[]}}
 */
export function termItems(scene) {
  /** @type {BoardItem | null} */
  let heading = null;
  /** @type {BoardItem[]} */
  const terms = [];
  for (const item of scene.board || []) {
    const text = typeof item.text === 'string' ? item.text.trim() : '';
    const term = typeof item.term === 'string' ? item.term.trim() : '';
    if (item.kind === 'heading' && !heading && text) heading = item;
    else if (item.kind !== 'heading' && (text || term)) terms.push(item);
  }
  return { heading, terms };
}

/**
 * Whether this template draws the scene whole in `format`: it draws no scene medium, and a shown side panel only of a
 * kind `_shared.js` draws (`drawsPanel`) - in 16:9 beside at most PANEL_TERMS_MAX terms, in 9:16 as its own beat
 * before any number of terms (owner decision 4). Anything else is board_generic's, and `_fit_key_terms` scores such a
 * scene 0, so direct.py never sends it here (B2c review CG-2).
 * @param {TimedScene} scene
 * @param {'landscape' | 'portrait'} [format]
 */
export function drawsScene(scene, format = 'landscape') {
  if (scene.media) return false;
  if (!shownPanel(scene)) return true;
  if (format !== 'landscape') return drawsPanel(scene, format);
  return drawsPanel(scene, format) && termItems(scene).terms.length <= PANEL_TERMS_MAX;
}

/**
 * The scene's panel as this template draws it (`_shared.js panelOf`; 16:9 beside the cards, 9:16 as its reel beat),
 * or null.
 * @param {TimedScene} scene @param {'landscape' | 'portrait'} format @param {'live' | 'render'} [mode]
 */
export function panelFor(scene, format, mode = 'live') {
  return panelOf(scene, mode, format);
}

/**
 * The 9:16 panel beat (`_shared.js reelWindow`) of a scene this template draws with a panel, else null (16:9, or no
 * panel).
 * @param {TimedScene} scene @param {'landscape' | 'portrait'} format
 * @returns {{start: number, end: number} | null}
 */
export function reelBeat(scene, format) {
  return format === 'portrait' && panelOf(scene, 'render', format) ? reelWindow(scene) : null;
}

/**
 * Every TeX string the template renders for the scene (the static `tex` hook; render.js converts them before `ready`):
 * the `$...$` spans of the heading and of each item's term and meaning, in field order, inline (`richWords` mounts
 * each with display off).
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  const { heading, terms } = termItems(scene);
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  /** @param {string} text */
  const add = (text) => {
    for (const tex of mathSpans(String(text || ''))) if (tex.trim()) out.push({ tex, display: false });
  };
  if (heading) add(heading.text || '');
  for (const item of terms) {
    add(item.term || '');
    add(item.text || '');
  }
  return out;
}

/**
 * The 4 px-step sizes from `max` down to `min` (the minimum itself last).
 * @param {number} max @param {number} min
 */
export function sizeSteps(max, min) {
  /** @type {number[]} */
  const out = [];
  for (let s = max; s > min; s -= SIZE_STEP) out.push(s);
  out.push(min);
  return out;
}

/**
 * The size pairs the cards are tried at, largest first: the term's and the meaning's steps walked together, each
 * holding at its own minimum once it is reached (so both texts shrink in step and neither goes below its R-29 floor).
 * @param {{term: {max: number, min: number}, meaning: {max: number, min: number}}} L
 * @returns {{term: number, meaning: number}[]}
 */
export function sizePairs(L) {
  const terms = sizeSteps(L.term.max, L.term.min);
  const meanings = sizeSteps(L.meaning.max, L.meaning.min);
  const n = Math.max(terms.length, meanings.length);
  /** @type {{term: number, meaning: number}[]} */
  const out = [];
  for (let k = 0; k < n; k++) {
    out.push({ term: terms[Math.min(k, terms.length - 1)], meaning: meanings[Math.min(k, meanings.length - 1)] });
  }
  return out;
}

/**
 * When the item's icon draws: its `draw` event (the earliest one on the item, templates.py `consumes`), else the
 * item's own entrance.
 * @param {TimedScene} scene @param {string} id @param {number} enter
 */
export function drawAt(scene, id, enter) {
  let at = null;
  for (const e of scene.events || []) {
    if (!e || e.kind !== 'draw' || typeof e.t !== 'number' || !Number.isFinite(e.t)) continue;
    if (typeof e.target !== 'string' || e.target.split(':')[0] !== id) continue;
    if (at === null || e.t < at) at = e.t;
  }
  return at === null ? enter : Math.max(0, at);
}

// ---------------------------------------------------------------------------------------------
// Measuring (layout time, on clones)
// ---------------------------------------------------------------------------------------------

/**
 * What a text element of a card looks like at `size` px: `broken[i]` (word i is broken inside - a plain word taller
 * than 1.5 lines, or a TeX word wider than its box) and `wide` (px the widest TeX word passes its box).
 * @param {HTMLElement | null} el @param {HTMLElement[]} words @param {boolean[]} texFlags @param {number} size
 * @param {number} lh
 * @returns {{broken: boolean[], wide: number}}
 */
function textProblems(el, words, texFlags, size, lh) {
  const box = el ? el.clientWidth : 0;
  const broken = words.map((w, i) => (texFlags[i] ? w.offsetWidth > box + 1 : w.offsetHeight > 1.5 * size * lh));
  const wide = Math.max(0, ...words.map((w, i) => (texFlags[i] ? w.offsetWidth - box : 0)));
  return { broken, wide };
}

/**
 * One card measured at `sizes`, on a clone in the hidden measuring host (so no painted property of the real card is
 * touched): its height in a `width` px box, and the state of its term's and its meaning's words.
 * @param {HTMLElement} host @param {any} card @param {number} width @param {{term: number, meaning: number}} sizes
 * @returns {{height: number, broken: boolean[], wide: number}}
 */
function measureCard(host, card, width, sizes) {
  const clone = /** @type {HTMLElement} */ (card.card.cloneNode(true));
  Object.assign(clone.style, {
    position: 'absolute', left: '0px', top: '0px', width: `${width}px`, height: 'auto', visibility: 'hidden',
  });
  host.appendChild(clone);
  const term = /** @type {HTMLElement | null} */ (clone.querySelector('.dyn-kt-term'));
  const meaning = /** @type {HTMLElement | null} */ (clone.querySelector('.dyn-kt-meaning'));
  const line = /** @type {HTMLElement | null} */ (clone.querySelector('.dyn-kt-termline'));
  if (term) term.style.fontSize = `${sizes.term}px`;
  if (meaning) meaning.style.fontSize = `${sizes.meaning}px`;
  // the 9:16 icon grows with the term, so the clone must carry its fitted size too (its row's height depends on it)
  if (line) line.style.fontSize = `${sizes.term}px`;
  sizeInlineIcon(/** @type {HTMLElement | null} */ (clone.querySelector('.dyn-kt-icon--inline')), sizes.term);
  const termWords = /** @type {HTMLElement[]} */ (term ? [...term.querySelectorAll('.dyn-word')] : []);
  const meaningWords = /** @type {HTMLElement[]} */ (meaning ? [...meaning.querySelectorAll('.dyn-word')] : []);
  const a = textProblems(term, termWords, card.termTex, sizes.term, card.termLh);
  const b = textProblems(meaning, meaningWords, card.meaningTex, sizes.meaning, card.meaningLh);
  const height = clone.offsetHeight;
  clone.remove();
  return { height, broken: [...a.broken, ...b.broken], wide: Math.max(a.wide, b.wide) };
}

/**
 * The largest size pair at which `ok(heights)` holds for every card measured at it with every word whole (a word
 * broken even at the minimum, wider than its box, does not count against a size; a TeX word too wide at the minimum
 * holds the cards there, where it runs out least: board_generic's / `_shared.js fitLines`' rule), and the heights
 * there. `fits` false: nothing fits and the cards are at their minimum.
 * @param {HTMLElement} host @param {any[]} cards @param {number} width
 * @param {{term: {max: number, min: number}, meaning: {max: number, min: number}}} L
 * @param {(heights: number[]) => boolean} ok
 * @returns {{sizes: {term: number, meaning: number}, fits: boolean, heights: number[], wide: number[]}}
 */
function fitCards(host, cards, width, L, ok) {
  const pairs = sizePairs(L);
  const min = pairs[pairs.length - 1];
  const atMin = cards.map((c) => measureCard(host, c, width, min));
  const texTooWide = atMin.some((m) => m.wide > 2);
  for (const sizes of texTooWide ? [min] : pairs) {
    const ms = sizes === min ? atMin : cards.map((c) => measureCard(host, c, width, sizes));
    const whole = ms.every((m, k) => m.broken.every((b, i) => !b || atMin[k].broken[i]));
    const heights = ms.map((m) => m.height);
    if (whole && ok(heights)) return { sizes, fits: true, heights, wide: ms.map((m) => m.wide) };
  }
  return { sizes: min, fits: false, heights: atMin.map((m) => m.height), wide: atMin.map((m) => m.wide) };
}

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** @type {TemplateModule} */
export default {
  id: 'key_terms',

  /**
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format } = ctx;
    if (!drawsScene(scene, format)) {
      // a medium or a panel this template does not draw: board_generic draws the scene whole
      const inner = boardGeneric.build(ctx);
      inner.ktFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const { heading, terms: items } = termItems(scene);
    const fixes = fixesOf(ctx.look);
    const baseline = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    const ink = ctx.onBackground.ink;
    const surface = ctx.roles.color('surface');
    const root = h('div', {
      class: ['dyn-kt', `dyn-kt--${format}`],
      style: { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' },
    });

    // the heading (the display face; R-53's scrim remedy behind it when preflight asked, board_generic's title block)
    /** @type {any} */
    let head = null;
    if (heading) {
      const lh = lineHeight('heading', heading.text || '');
      const text = h('h2', {
        class: 'dyn-kt-heading', dir: 'auto',
        style: {
          margin: '0px', fontFamily: 'var(--dyn-font-display)', fontWeight: String(TERM_WEIGHT),
          lineHeight: String(lh), overflowWrap: 'anywhere', color: ink,
        },
      });
      const { words, tex } = richWords(ctx, text, String(heading.text || ''));
      const mask = baseline ? revealMask(text) : null;
      const scrim = fixes.slots.heading && fixes.slots.heading.scrim ? ctx.roles.color('scrim') : null;
      const el = group('dyn-kt-heading-group', mask || text);
      el.style.position = 'absolute';
      if (scrim) {
        Object.assign(el.style, {
          padding: '10px 24px 14px', marginLeft: '-24px', borderRadius: `${RADIUS.card}px`, background: scrim,
        });
      }
      root.appendChild(el);
      head = { item: heading, el, text, mask, words, tex, lh, scrim, size: L.heading.max, min: L.heading.min, overflow: 0, alpha: 0 };
    }

    // a dim group per item > its card (16:9: the surface card; 9:16: a row on the background) > icon, term, meaning
    // a scrim preflight planned for a text slot goes behind the row (9:16 only: a 16:9 card is opaque already)
    const rowScrim = !L.card && ((fixes.slots.term && fixes.slots.term.scrim) || (fixes.slots.meaning && fixes.slots.meaning.scrim))
      ? ctx.roles.color('scrim') : null;
    // the stored icons, all or nothing (B2c review V12, recap_retrieval's and list_build's rule): one card with an
    // icon beside cards without one sets its term ~ICON_SIZE lower than its neighbours' (review D6)
    const drawn = items.map((item) => storedIcon(
      ctx, item.id, L.card ? { size: ICON_SIZE, block: true } : { size: inlineIcon(L.term.max).icon }));
    const icons = drawn.length > 0 && drawn.every(Boolean) ? drawn : null;
    const cards = items.map((item, k) => {
      const termLh = lineHeight('heading', item.term || '');
      const meaningLh = lineHeight('body', item.text || '');
      const icon = icons ? icons[k] : null;
      // every style key is written literally (the source scan refuses a style object it cannot read, plan §P)
      const iconHost = icon
        ? h('span', {
          class: ['dyn-kt-icon', L.card ? 'dyn-kt-icon--block' : 'dyn-kt-icon--inline'],
          style: { color: ink, display: L.card ? 'block' : 'inline-block' },
        }, icon)
        : null;
      if (iconHost && L.card) {
        iconHost.style.width = `${ICON_SIZE}px`;
        iconHost.style.height = `${ICON_SIZE}px`;
        iconHost.style.marginBottom = `${L.iconGap}px`;
      } else if (iconHost) {
        // the inline icon never shrinks in the term's flex line; `layout` sizes it from the fitted term size
        iconHost.style.flexShrink = '0';
        sizeInlineIcon(iconHost, L.term.max);
      }
      const termEl = h('div', {
        class: 'dyn-kt-term', dir: 'auto',
        style: {
          fontFamily: 'var(--dyn-font-display)', fontWeight: String(TERM_WEIGHT), lineHeight: String(termLh),
          overflowWrap: 'anywhere', color: ink,
        },
      });
      const term = richWords(ctx, termEl, String(item.term || ''));
      const termMask = baseline ? revealMask(termEl) : null;
      const meaningEl = h('div', {
        class: 'dyn-kt-meaning', dir: 'auto',
        style: {
          marginTop: `${L.textGap}px`, fontFamily: 'var(--dyn-font-body)', fontWeight: String(L.meaningWeight),
          lineHeight: String(meaningLh), overflowWrap: 'anywhere', color: ink,
        },
      });
      const meaning = richWords(ctx, meaningEl, String(item.text || ''));
      const meaningMask = baseline ? revealMask(meaningEl) : null;
      // 9:16: the inline icon sits in the term's own line, before its first word. The line is a flex box with its
      // own font size (the term's, written by `layout`): without it the block-level term dropped to the next line
      // and the icon's em lengths resolved against the inherited base size (review D2). Every style key literal
      // (the source scan).
      const termBox = termMask || termEl;
      const termLine = iconHost && !L.card
        ? h('div', {
          class: 'dyn-kt-termline',
          style: { display: 'flex', alignItems: 'baseline', width: '100%', fontSize: `${L.term.max}px` },
        }, iconHost, termBox)
        : termBox;
      if (termLine !== termBox) termBox.style.minWidth = '0px';  // the term wraps inside the line, never overflows
      const card = h('div', {
        class: 'dyn-kt-card', dataset: { item: item.id },
        style: {
          boxSizing: 'border-box', width: '100%', textAlign: 'start', padding: `${L.pad}px`,
          background: L.card ? surface : rowScrim || 'transparent',
          borderRadius: L.card || rowScrim ? `${RADIUS.card}px` : '0px',
          boxShadow: L.card ? CARD_SHADOW : 'none',
        },
      }, iconHost && L.card ? iconHost : null, termLine, meaningMask || meaningEl);
      const el = group('dyn-kt-row', card);
      el.style.position = 'absolute';
      root.appendChild(el);
      return {
        item, k, el, card, icon, iconHost, termEl, meaningEl, termMask, meaningMask,
        termLine: termLine === termBox ? null : termLine,
        words: term.words, termTex: term.tex, meaningWords: meaning.words, meaningTex: meaning.tex,
        termLh, meaningLh,
        // an icon drawn with its strokes: `pathLength="1"` makes the dash the whole path whatever its real length
        paths: icon ? drawablePaths(icon) : [],
        filled: !!icon && icon.getAttribute('fill') === 'currentColor',
        top: 0, height: 0, overflow: 0, alpha: 0, termAlpha: 0, meaningAlpha: 0,
      };
    });

    // the scene's panel: 16:9 beside the cards; 9:16 its own beat before them, entering at the beat's start (owner
    // decision 4; `_shared.js` builds, lays out, paints and reports it)
    const beat = reelBeat(scene, format);
    /** @type {import('./_shared.js').PanelParts | null} */
    let panelParts = null;
    const panel = panelFor(scene, format, ctx.mode);
    if (panel) {
      panelParts = buildPanel(ctx, beat ? { ...panel, start: beat.start } : panel);
      root.appendChild(panelParts.el);
    }

    ctx.root.appendChild(root);
    const ids = cards.map((c) => c.item.id);
    // with a reel beat, what would enter during it enters as it ends (`_shared.js reelEntrances`)
    /** @type {Map<string, number>} */
    const enter = beat ? reelEntrances(scene, beat.end)
      : new Map(itemTimings(scene).map((timing) => [timing.id, timing.enter]));
    return {
      root, head, cards, panel: panelParts, ids, enter, surface, rowScrim, beat,
      // an icon never draws before its card is there (a `draw` event inside a reel beat waits for the card)
      draw: new Map(cards.map((c) => {
        const at = enter.get(c.item.id) || 0;
        return [c.item.id, beat ? Math.max(at, drawAt(scene, c.item.id, at)) : drawAt(scene, c.item.id, at)];
      })),
      width: 0, cardsTop: 0, sizes: { term: L.term.max, meaning: L.meaning.max },
    };
  },

  /**
   * Measure and fit (runs again when a late face arrives; idempotent): the heading in place, then the cards on clones
   * at the largest size pair whose row (16:9: the tallest card) or stack (9:16) fits under it. Positions and the two
   * text sizes are set here, once; `update` only paints.
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
    const b = boxes(format, zones, !!v.panel);
    let top = b.cards.y0;
    if (v.head) {
      const hd = v.head;
      const range = slotRange(ctx, 'heading', { min: L.heading.min, max: L.heading.max, role: 'title' });
      Object.assign(hd.el.style, {
        left: `${b.heading.x0}px`, top: `${b.heading.y0}px`, width: `${b.heading.x1 - b.heading.x0}px`,
      });
      const fit = fitLines(hd.text, {
        max: range.max, min: range.min, lines: L.heading.lines, prefer: L.heading.prefer, lh: hd.lh, step: SIZE_STEP,
      }, hd.words, hd.tex);
      hd.size = fit.size;
      hd.min = range.min;
      hd.overflow = fit.overflow;
      // below the heading as laid out, never above one line of it at its minimum (jsdom has no layout)
      const bottom = hd.el.offsetTop + hd.el.offsetHeight;
      top = Math.round(Math.max(bottom, b.heading.y0 + range.min * hd.lh) + L.headingGap);
    }
    // the panel, from the cards' top down (16:9; list_build's panel box), or alone on the reel's stage (9:16)
    if (v.panel && v.beat) layoutPanel(ctx, v.panel, reelPanelBox(v.panel.panel, zones));
    else if (v.panel && b.visual) layoutPanel(ctx, v.panel, { ...b.visual, y0: top });
    v.cardsTop = top;
    const avail = b.bottom - top;
    const cards = v.cards;
    const width = format === 'landscape' ? cardWidth(cards.length, b.cards) : b.cards.x1 - b.cards.x0;
    v.width = width;
    const term = slotRange(ctx, 'term', { min: L.term.min, max: L.term.max, role: 'title' });
    const meaning = slotRange(ctx, 'meaning', { min: L.meaning.min, max: L.meaning.max, role: 'body' });
    const ranges = { term, meaning };
    const host = h('div', {
      class: 'dyn-kt-measure', 'aria-hidden': 'true',
      style: { position: 'absolute', left: '0px', top: '0px', width: `${width}px`, visibility: 'hidden' },
    });
    ctx.root.appendChild(host);
    let fit;
    try {
      if (format === 'landscape') {
        // a row of equal cards: the largest pair at which the tallest card fits the band
        fit = fitCards(host, cards, width, ranges, (hs) => Math.max(0, ...hs) <= avail);
      } else {
        // a stack: the largest pair at which every row plus the gaps fits the column
        const total = (/** @type {number[]} */ hs) => hs.reduce((a, x) => a + x, 0) + L.gap * Math.max(0, hs.length - 1);
        fit = fitCards(host, cards, width, ranges, (hs) => total(hs) <= avail);
      }
    } finally {
      host.remove();
    }
    v.sizes = fit.sizes;
    if (format === 'landscape') {
      const height = Math.max(0, ...fit.heights);
      // the row centred in the band under the heading (never above its top)
      const y = Math.round(top + Math.max(0, (avail - height) / 2));
      cards.forEach((/** @type {any} */ c, k) => {
        c.top = y - top;
        c.height = height;
        c.overflow = Math.max(0, height - avail, fit.wide[k] > 2 ? fit.wide[k] : 0);
        Object.assign(c.el.style, {
          left: `${b.cards.x0 + k * (width + L.gap)}px`, top: `${y}px`, width: `${width}px`, height: `${height}px`,
        });
        c.card.style.height = `${height}px`;
      });
    } else {
      let y = 0;
      cards.forEach((/** @type {any} */ c, k) => {
        c.top = y;
        c.height = fit.heights[k];
        c.overflow = Math.max(0, y + fit.heights[k] - avail, fit.wide[k] > 2 ? fit.wide[k] : 0);
        Object.assign(c.el.style, { left: `${b.cards.x0}px`, top: `${top + y}px`, width: `${width}px` });
        y += fit.heights[k] + L.gap;
      });
    }
    for (const c of cards) {
      c.termEl.style.fontSize = `${fit.sizes.term}px`;
      c.meaningEl.style.fontSize = `${fit.sizes.meaning}px`;
      // the 9:16 term line carries the term's size, and its icon is a square of ICON_INLINE_EM of it (review D2)
      if (c.termLine) c.termLine.style.fontSize = `${fit.sizes.term}px`;
      if (c.iconHost && !L.card) sizeInlineIcon(c.iconHost, fit.sizes.term);
    }
  },

  /**
   * Paint scene time t: the heading's entrance, then each card's rise, its icon's stroke draw and its term and
   * meaning 80 ms apart. A pure function of t: every property painted on an element is painted on every frame, with a
   * value (never '').
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
    // the scene's panel: its entrance, its medium and a terminal's output lines (`_shared.js paintPanel`); in a reel
    // beat it leaves as the beat ends and shows every output line
    if (v.panel && v.beat) paintPanel(ctx, v.panel, t, state, { until: v.beat.end, allLines: true });
    else if (v.panel) paintPanel(ctx, v.panel, t, state);

    if (v.head) {
      const e = enterAt(ctx, t, v.enter.get(v.head.item.id) || 0, HEADING_ENTER);
      v.head.alpha = e;
      paintTextEntrance(ctx, v.head.text, v.head.mask, e);
    }

    const delay = seconds(ctx, TEXT_DELAY);
    for (const c of v.cards) {
      const start = v.enter.get(c.item.id) || 0;
      const e = enterAt(ctx, t, start, ITEM_ENTER);
      const eTerm = e;
      const eMeaning = enterAt(ctx, t, start + delay, ITEM_ENTER);
      c.alpha = e;
      c.termAlpha = Math.min(1, Math.max(0, e * eTerm));
      c.meaningAlpha = Math.min(1, Math.max(0, e * eMeaning));
      // the card: the §4.3 fade and 32 px rise, on whole pixels
      paintRise(ctx, c.card, e, RISE);
      p.style(c.card, 'visibility', e > 0 ? 'visible' : 'hidden');
      paintTextEntrance(ctx, c.termEl, c.termMask, eTerm);
      paintTextEntrance(ctx, c.meaningEl, c.meaningMask, eMeaning);
      // the icon: its strokes draw over ICON_DRAW (a filled icon fades in: it has no strokes to draw)
      if (c.iconHost) {
        const d = ease('ease.enter', progress(t, v.draw.get(c.item.id) || 0, seconds(ctx, ICON_DRAW)));
        p.style(c.iconHost, 'opacity', fx(c.filled ? d : 1));
        for (const path of c.paths) p.attr(path, 'stroke-dashoffset', fx(1 - d, 4));
      }
    }
  },

  /**
   * Where a look_here points (plan §1.4, §1.7): an item's card (16:9 a shape, whose outline is drawn whole around it,
   * cues.js shapeArea; 9:16 a row of text), or the heading's text; its box as painted now (read after `update`) on
   * whole stage px, with the dim group holding it. Null for anything else or off the page. key_terms declares no
   * look_here in `consumes` today (§4.3 gives it no hold), so the cue layer never asks; the hook is here because the
   * contract has it and a later `consumes` must not need a template change.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    const v = ctx.view;
    const id = String(target || '').split(':')[0];
    // `panel` is a reserved target word (plan §1.7), so no board item can have that id
    if (id === 'panel') return v.panel ? panelFocusRect(ctx, v.panel, String(target || '')) : null;
    const card = v.cards.find((/** @type {any} */ c) => c.item.id === id);
    const el = card ? card.card : v.head && v.head.item.id === id ? v.head.text : null;
    const holder = card ? card.el : v.head && v.head.item.id === id ? v.head.el : null;
    const r = el ? ctx.rectOf(el) : null;
    if (!r || !holder) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    const x1 = Math.round(r.x + r.w);
    const y1 = Math.round(r.y + r.h);
    const shape = !!card && LAYOUT[ctx.format].card;
    return {
      rect: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, shape: 'rect', radius: shape ? RADIUS.card : 0, el: holder,
      kind: shape ? 'shape' : 'text',
    };
  },

  /**
   * The elements holding an item's text, where cue.term finds its [[term]] spans (`.rt-k`, one per word piece with the
   * term's `data-dyn-term`, `_shared.js richWords`): the item's term and its meaning, in field order (term, text), so
   * `<item>` sweeps every term of the card and `<item>:term:<n>` the n-th across both; or the heading's text.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const v = ctx.view;
    const card = v.cards.find((/** @type {any} */ c) => c.item.id === itemId);
    if (card) return [card.termEl, card.meaningEl];
    return v.head && v.head.item.id === itemId ? v.head.text : null;
  },

  /**
   * The content groups a look_here dims (all but the target's): the heading, every card and the scene's panel; the
   * same elements at every t.
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.head ? v.head.el : null, ...v.cards.map((/** @type {any} */ c) => c.el), v.panel ? v.panel.el : null]
      .filter(Boolean);
  },

  /**
   * The morph anchor (plan §1.4): none (a row of cards has no single shared visual; B2f pairs templates that have
   * one).
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: the only medium this template shows is its panel's, in slot `panel`: 16:9 the panel's whole box from the
   * top of the title zone, 9:16 the reel beat's box (`_shared.js reelPanelBox`) - the largest each is ever shown in,
   * so the picture only ever downscales (§P). A scene it hands to board_generic gets board_generic's box, so
   * `mediaPlan` and `ctx.media` agree.
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
    const panel = panelFor(scene, zones.format, mode);
    if (!panel) return null;
    if (zones.format === 'portrait') return panelMediaBox(scene, reelPanelBox(panel, zones), slot);
    const b = boxes(zones.format, zones, true);
    return b.visual ? panelMediaBox(scene, b.visual, slot) : null;
  },

  /**
   * Static: every TeX string the template renders for the scene (`texOf`; the same in both formats). The panel brings
   * none (a terminal card and a title card are plain text).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} fmt
   */
  tex(scene, look, fmt) {
    if (!drawsScene(scene, fmt)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene);
  },

  /**
   * The text on screen (preflight, §7.12): the heading (slot `heading`, on the background or its R-53 scrim) and each
   * item's term (slot `term`) and meaning (slot `meaning`), critical, at the size and weight painted now, on the
   * card's surface (16:9) or on the background's worst case (9:16, or its scrim).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
    /** @type {TextEntry[]} */
    const out = [];
    const ink = ctx.onBackground.ink;
    /** @param {string | null} scrim @param {string} on */
    const bgOf = (scrim, on) => (scrim ? scrimmedBg(scrim, ctx.onBackground, on) : ctx.onBackground.bg);
    if (v.head) {
      const hd = v.head;
      out.push({
        el: hd.text, role: 'title', slot: 'heading', critical: true, ink, bg: bgOf(hd.scrim, ink), alpha: hd.alpha,
        overflow: hd.overflow, size: hd.size, min: hd.min, weight: TERM_WEIGHT,
        text: plainText(String(hd.item.text || '')),
      });
    }
    // a 16:9 card is opaque (surface); a 9:16 row is text on the background, or on the scrim preflight asked for
    const bg = L.card ? v.surface : bgOf(v.rowScrim, ink);
    for (const c of v.cards) {
      const term = plainText(String(c.item.term || ''));
      const meaning = plainText(String(c.item.text || ''));
      if (term) {
        out.push({
          el: c.termEl, role: 'title', slot: 'term', critical: true, ink, bg, alpha: c.termAlpha, overflow: c.overflow,
          size: v.sizes.term, min: L.term.min, weight: TERM_WEIGHT, text: term,
        });
      }
      if (meaning) {
        out.push({
          el: c.meaningEl, role: 'body', slot: 'meaning', critical: true, ink, bg, alpha: c.meaningAlpha,
          overflow: c.overflow, size: v.sizes.meaning, min: L.meaning.min, weight: L.meaningWeight, text: meaning,
        });
      }
    }
    // the panel's own text: a terminal card (slot `terminal`, critical) or a title card's label (not critical)
    if (v.panel) out.push(...panelTexts(ctx, v.panel));
    return out;
  },
};

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.ktFallback);

/**
 * The paths of a stored icon prepared for the stroke draw (§4.3 "the icon's strokes draw"): `pathLength="1"` and a
 * dash of the whole path, so one dash offset in [0, 1] draws any path, whatever its real length (`getTotalLength` is
 * not available in every renderer and would make the frame depend on the measured geometry).
 * @param {SVGElement} icon
 * @returns {SVGElement[]}
 */
function drawablePaths(icon) {
  /** @type {SVGElement[]} */
  const out = [];
  for (const path of icon.querySelectorAll('path')) {
    path.setAttribute('pathLength', '1');
    path.setAttribute('stroke-dasharray', '1 1');
    out.push(/** @type {SVGElement} */ (path));
  }
  return out;
}
