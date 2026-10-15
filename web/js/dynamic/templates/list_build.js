// @ts-check
/**
 * `list_build` (DYNAMIC_VIDEO_DESIGN §4.3 B, R1, class `enumeration`; B2 plan §A step 3, the B2c pilot): the
 * members of a set, one per narration beat.
 *
 * Data (§4.3, templates.py `_fit_list_build`): >= 2 `bullet` items, an optional `heading`, optional `icon_hint`s (the
 * resolver stores the matched icons in `look.icons`, §4.12; this template draws them with `_shared.js storedIcon`),
 * and in 16:9 an optional picture or clip panel (PICTURE_PANELS). Every board item other than the first heading that
 * has text is a list row (the registry only scores bullets, so in practice the rows are bullets). Nothing a timeline
 * names is silently dropped: a scene with a side panel this template does not draw (any panel in 9:16, a terminal,
 * chart, graph, model or skill-tree panel in 16:9; `drawsScene`) is drawn by board_generic whole, every hook handed
 * on (formula_focus's convention), and `_fit_list_build` scores such a scene 0 so direct.py never sends it here (B2c
 * review CG-2).
 *
 * 16:9 (§4.3): a numbered list. With something to show beside it (the scene's picture or clip panel, or a stored icon
 * for every row while rows take turns being current) the list takes the left 55% and the right 45% shows the panel
 * (its title on the card surface when this mode cannot show its medium, board_generic's convention) or the current
 * row's large icon (`feature_px`, §4.12); with nothing beside it the list is set in the centre 60% (the named regions of
 * §2.5, core.js REGIONS), so the right 45% is never left empty: a list where only some rows have icons is centred and
 * draws none (icons are decoration, §4.12 "no match means no icon"; one current row without an icon would leave the
 * right side blank, B2c review V12). The heading spans its variant's width above. Rows stay where the list puts them
 * (board order); a row enters in its place on its beat.
 *
 * 9:16 (§4.3, [RV L-13]): a persistent compact stack. The current row is large (56-64 px); when the next row enters,
 * the row before it shrinks in place to a compact 40 px row (R-29's 9:16 text floor) and the new row appears large
 * below it, never a one-item carousel. Every row's top is fixed (the compact rows above it), so nothing scrolls: only
 * the row that stops being current changes size, at the instant the next row enters, inside a short fade (DIP). A
 * scene whose rows no beat reveals (they all enter with the scene) is a plain list: every row large when the whole
 * list fits at the large minimum, else every row compact.
 *
 * Item states (plan §1.5, state.js `ITEM_STATES` / `itemStateStyle` / `itemStatesAt`): the row entered last is
 * `current` (weight 700, a 2 px `ink2` inset border on the row's card), every earlier one `past` (`ink2`, §4.3 "earlier
 * items are ink2, no accent colour"). §4.3 reads "bold, full ink with a 3 px ink outline"; the binding plan §1.5 state
 * is 2 px ink2 and never an outline (the look_here cue's), and it must read apart from a look_here (plan §1.5 "visually
 * distinct from look_here"): `focusRect` gives the row's card, so the look_here outline (4 px focus) runs FOCUS_OUTSET
 * outside the state border, never on it (B2c review V4: the outline used to lie exactly on the border, which in light
 * families, where `focus` is `ink`, made the two the same dark rectangle). Without any beat-revealed row there is no
 * current row and no state. The states are painted here, from
 * `itemStateStyle`, rather than through `_shared.js paintItemState`, because that helper clears properties ('' when a
 * state is off), which makes the element's style attribute depend on the order frames were painted in (the jsdom order
 * gate compares DOM); every value painted here is written on every frame.
 *
 * Choreography (§4.3, §4.8): each row slides 24 px in 450 ms on its beat (state.js `itemTimings`: a reveal event wins
 * over the beat, the beat starts it REVEAL_LEAD before its anchor, R-17), its number first and its text 80 ms later;
 * the text enters with the personality's entrance (`ctx.motion.entrance`: Crisp `baseline_reveal` slides it up inside a
 * mask, every other one is the row's fade and 24 px slide). Template durations take the personality's scale
 * (`ctx.motion.scale`, `_shared.js seconds` / `enterAt`); the cues keep their own (cues.js).
 *
 * Emphasis is cues.js's (§4.2, plan §1.4/§1.5): `itemEl` hands it a row's text (its [[term]] `.rt-k` spans, kept by
 * `_shared.js richWords`, so `<item>` and `<item>:term:<n>` both resolve), `focusRect` the row's card (a shape: the
 * outline goes round it, outside the state border, clamped only to the shapes area, cues.js shapeArea), `dimGroups`
 * the heading, every row (a 16:9 row's icon is inside its row's group, so a look_here on a row keeps its icon) and the
 * panel, which the stage dims (this template never writes their opacity: it animates their children).
 * Nothing here writes outline, text-decoration, filter, backdrop-filter or will-change, and no structure role colours a
 * row (§4.2): the numbers take the row's own ink.
 *
 * Determinism (§7.4, plan §P): `update` is a pure function of t; translates are whole pixels; the only per-frame size
 * change (9:16) is a font size taken from the two sizes `layout` measured. `layout` measures on clones in a hidden
 * host, so it never writes a property the painter owns (the painter skips a value it believes is already written).
 */

import { h } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { mixOklch } from '../../motion/color.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { LAYOUT_TOKENS, fixesOf, fx } from '../core.js';
import { FOCUS_OUTSET, FOCUS_PX } from '../cues.js';
import { shownIn } from '../media.js';
import { itemStateStyle, itemStatesAt, itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import {
  RADIUS, STATE_BORDER, STATE_WEIGHT, containRect, enterAt, fitLines, group, mediaAspect, paintMedia, paintRise,
  paintTextEntrance, region, revealMask, richWords, scrimmedBg, seconds, slotRange, storedIcon,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../../shared/types.js').MediaRef} MediaRef */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */

// ---------------------------------------------------------------------------------------------
// Motion (§4.3 list_build, §4.8)
// ---------------------------------------------------------------------------------------------

/** §4.3: each row slides 24 px ... */
export const RISE = 24;
/** ... in 450 ms on its beat ... */
export const ITEM_ENTER = 0.45;
/** ... number first, text 80 ms later. */
export const TEXT_DELAY = 0.08;
/** The heading's entrance (the rows' length, §4.8 "medium move 400-500 ms"). */
export const HEADING_ENTER = 0.45;
/** 16:9: a row's change from current to past (ink -> ink2, its border off) eases over this long (§4.8 small move). */
export const STATE_EASE = 0.3;
/** 9:16: the fade a row takes while it shrinks from large to compact, centred on the next row's entrance: out over
 * the first half, back over the second (the swap happens while the row is invisible, so no frame shows text
 * re-flowing). Each half (150 ms) is well inside §4.8's exits, 30% shorter than the 450 ms entrance. */
export const DIP = 0.3;
/** 16:9: the current row's icon enters with the row and leaves 30% faster (§4.8) when the next row takes over. */
export const ICON_ENTER = 0.45;
export const ICON_EXIT = ICON_ENTER * 0.7;
/** The panel's entrance (a picture or clip: §4.3 media_split's 500 ms, as a fade and rise; the scale is
 * media_split's). */
export const PANEL_ENTER = 0.5;
/** The current row's icon on the right (16:9 split): the catalogue's feature icon size (§4.12, A2 proposal 320 px). */
export const ICON_SIZE = Number(LAYOUT_TOKENS.icon.feature_px);
/** A panel shown from the scene's start enters this long after it (board_generic's media frame, 200 ms). */
export const PANEL_DELAY = 0.2;

// ---------------------------------------------------------------------------------------------
// Layout (stage px; R-29 floors; the catalogue's regions and layout tokens)
// ---------------------------------------------------------------------------------------------

/** Line heights of the type scale (aadhi/compose/dynamic/zones.py TYPE_SCALE, plan §2: Indic body >= 1.45, Indic
 * display / heading >= 1.25; list_build.test.js pins them to the catalogue's `type.scale`). */
export const LINE_HEIGHT = Object.freeze({
  heading: Object.freeze({ latin: 1.15, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
});

/**
 * A row's padding on every side (12 px): its text sits as far inside its card (where the state border is drawn) as
 * the look_here outline's outer edge sits outside it (cues.js: FOCUS_PX wide, its inner edge FOCUS_OUTSET outside the
 * card), so a row's text, its state border and the outline are evenly spaced. The rows fill their column up to the
 * critical zone's edge (9:16: the text column x 192-780, the part both critical boxes share, R-59); the outline around
 * a card is a shape's (cues.js shapeArea), so it is drawn whole just outside the column, never across the text.
 */
export const PAD = FOCUS_OUTSET + FOCUS_PX;
/** The label over a panel whose medium this mode cannot show (board_generic's .dyn-media-missing size; media_split's
 * MISSING_LABEL_PX in 16:9). */
export const MISSING_LABEL_PX = 52;
/** The side-panel kinds this template draws (16:9 only): a picture or a clip (§4.3 "the scene's panel"); a GIF shows
 * in the live player, its title card in the MP4 (media.js shownIn). templates.py LIST_PANELS mirrors it. */
export const PICTURE_PANELS = Object.freeze(['figure', 'image', 'manim', 'gif']);

/**
 * Per-format layout. `heading`: the heading's size range, lines and preferred lines (16:9: R-29 headings 84-120 px,
 * capped at board_generic's 96 so a list scene's heading matches a board title; 9:16: board_generic's 56-88, one line
 * preferred, R-25 "headline <= 1 line"). `item`: a current (16:9: every) row's size range (16:9: R-29 body 48-56 down
 * to 40 for a long list, above the 36 px text floor; 9:16: R-29 body 56-64). `row`: the 9:16 compact row (R-29 text
 * floor, §4.3 ">= 40 px rows"). `weight`: a row's weight when it is not current (16:9 board_generic's body weight;
 * 9:16 R-29 "<= 64 px at least 600"). `num` / `numGap`: the number column and its gap; `pad`: a row's padding, PAD on
 * every side (`padCompact`: a 9:16 compact row's top and bottom, which has no state border, so the stack stays
 * dense); `gap`: between rows; `headingGap`: between the heading and the list. list_build.test.js pins the slots this
 * table gives (`slotRows`) to the catalogue's once templates.py lists them.
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    heading: Object.freeze({ max: 96, min: 84, lines: 2, prefer: 1 }),
    item: Object.freeze({ max: 52, min: 40 }),
    row: null,
    weight: 400,
    num: 64,
    numGap: 16,
    padCompact: PAD,
    gap: 16,
    headingGap: 40,
  }),
  portrait: Object.freeze({
    heading: Object.freeze({ max: 88, min: 56, lines: 2, prefer: 1 }),
    item: Object.freeze({ max: 64, min: 56 }),
    row: Object.freeze({ max: 40, min: 40 }),
    weight: 600,
    num: 56,
    numGap: 12,
    padCompact: 4,
    gap: 12,
    headingGap: 28,
  }),
});

/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;

/**
 * The boxes of a format and variant (pure; whole stage px): `heading` (where the heading is set), `list` (the row
 * column: the rows' edges; a row's text starts PAD inside), `visual` (16:9 split: the right 45% beside the list, from
 * the top of `list` given at layout; null otherwise) and the list's `bottom`. 16:9 rows end at the body box's bottom
 * (y 900, as board_generic's cards: 24 px above the caption band); 9:16 rows fill the text column x 192-780, y to
 * 1020 (the part of the stage both critical boxes hold, R-59).
 * @param {'landscape' | 'portrait'} format
 * @param {'split' | 'centred' | 'stack' | 'list'} variant
 * @param {Zones} zones
 * @returns {{heading: Box, list: Box, visual: Box | null, bottom: number}}
 */
export function boxes(format, variant, zones) {
  const ctx = { format };
  if (format === 'portrait') {
    const col = zones.textColumn;
    return {
      heading: { x0: zones.title.x0, y0: zones.title.y0, x1: zones.title.x1, y1: zones.title.y1 },
      list: { x0: col.x0, y0: zones.title.y0, x1: col.x1, y1: col.y1 },
      visual: null,
      bottom: col.y1,
    };
  }
  const band = region(ctx, 'top-band');
  const top = zones.title.y0;
  const bottom = zones.body.y1;
  if (variant === 'split') {
    const left = region(ctx, 'left-55');
    const right = region(ctx, 'right-45');
    return {
      heading: { x0: band.x0, y0: top, x1: band.x1, y1: band.y1 },
      list: { x0: left.x0, y0: top, x1: left.x1, y1: bottom },
      visual: { x0: right.x0, y0: top, x1: right.x1, y1: bottom },
      bottom,
    };
  }
  const mid = region(ctx, 'centre-60');
  return {
    heading: { x0: mid.x0, y0: top, x1: mid.x1, y1: band.y1 },
    list: { x0: mid.x0, y0: top, x1: mid.x1, y1: bottom },
    visual: null,
    bottom,
  };
}

/**
 * The text slots of this template (aadhi/compose/dynamic/templates.py SlotSpec, the rows textfit.py fits; plan §1.6,
 * §1.11): slot -> format -> variant -> {w, max_lines, size, role, face, weight, line_height, prefer_lines}. The 16:9
 * variants are the layout's: `default` the split one (the narrower boxes, so a size fitted there is an upper bound for
 * the centred one too: B2e can store it without knowing which one the stage picks) and `centred`. `heading` per
 * variant width; `item` the row text (the row less PAD each side, the number column and its gap), laid out at
 * STATE_WEIGHT so becoming current never re-wraps it; `row` the 9:16 compact row (never current, its own weight). Line
 * heights are the Latin ones (Indic text takes LINE_HEIGHT's Indic values, which textfit.py's Indic measure must use).
 * Pure.
 * @param {Zones} landscape @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  /** @param {Box} b */
  const w = (b) => b.x1 - b.x0;
  /** @param {'landscape' | 'portrait'} fmt @param {Box} list */
  const textW = (fmt, list) => w(list) - 2 * PAD - LAYOUT[fmt].num - LAYOUT[fmt].numGap;
  /** @param {{max: number, min: number, lines: number, prefer: number}} s @param {number} width */
  const heading = (s, width) => ({
    w: width, max_lines: s.lines, size: [s.min, s.max], role: 'title', face: 'display', weight: 700,
    line_height: LINE_HEIGHT.heading.latin, prefer_lines: s.prefer,
  });
  /** @param {{max: number, min: number}} s @param {number} width @param {number} weight */
  const body = (s, width, weight) => ({
    w: width, max_lines: null, size: [s.min, s.max], role: 'body', face: 'body', weight,
    line_height: LINE_HEIGHT.body.latin, prefer_lines: null,
  });
  const split = boxes('landscape', 'split', landscape);
  const centred = boxes('landscape', 'centred', landscape);
  const tall = boxes('portrait', 'stack', portrait);
  const L = LAYOUT.landscape;
  const P = LAYOUT.portrait;
  return {
    heading: {
      landscape: { default: heading(L.heading, w(split.heading)), centred: heading(L.heading, w(centred.heading)) },
      portrait: { default: heading(P.heading, w(tall.heading)) },
    },
    item: {
      landscape: {
        default: body(L.item, textW('landscape', split.list), STATE_WEIGHT),
        centred: body(L.item, textW('landscape', centred.list), STATE_WEIGHT),
      },
      portrait: { default: body(P.item, textW('portrait', tall.list), STATE_WEIGHT) },
    },
    row: {
      portrait: { default: body(/** @type {{max: number, min: number}} */ (P.row), textW('portrait', tall.list), P.weight) },
    },
  };
}

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/** Indic scripts (Devanagari to Sinhala, U+0900-U+0DFF), whose text takes the taller line heights (plan §2). */
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
 * The scene's heading (its first `heading` item, if it has text) and its rows (every other item with text, in board
 * order).
 * @param {TimedScene} scene
 * @returns {{heading: BoardItem | null, rows: BoardItem[]}}
 */
export function listItems(scene) {
  /** @type {BoardItem | null} */
  let heading = null;
  /** @type {BoardItem[]} */
  const rows = [];
  for (const item of scene.board || []) {
    const text = typeof item.text === 'string' ? item.text.trim() : '';
    if (!text) continue;
    if (item.kind === 'heading' && !heading) heading = item;
    else if (item.kind !== 'heading') rows.push(item);
  }
  return { heading, rows };
}

/**
 * The scene's side panel when the layout shows it (null when there is none or `layout.show_side_panel` is false).
 * @param {TimedScene} scene
 */
export function shownPanel(scene) {
  const sp = scene.side_panel;
  return sp && scene.layout?.show_side_panel !== false ? sp : null;
}

/**
 * Whether this template draws the scene whole in `format` (B2c review CG-2; templates.py `_fit_list_build` gives 0
 * otherwise): no shown side panel, or (16:9) a picture or clip panel (PICTURE_PANELS). 9:16 draws no panel (the stack
 * takes the stage). Anything else is drawn by board_generic, every hook handed on.
 * @param {TimedScene} scene @param {'landscape' | 'portrait'} format
 */
export function drawsScene(scene, format) {
  const sp = shownPanel(scene);
  return !sp || (format === 'landscape' && PICTURE_PANELS.includes(String(sp.panel && sp.panel.kind)));
}

/**
 * The scene's picture or clip panel (16:9; a scene `drawsScene` takes): `ref` its medium when `mode` can show it
 * (board_generic's rule for a side panel: `media.shownIn`; else null and the panel's title is shown on the card surface
 * instead), `title` that title, `start` when it enters; null without one.
 * @param {TimedScene} scene @param {'live' | 'render'} mode
 * @returns {{ref: MediaRef | null, title: string, start: number} | null}
 */
export function panelOf(scene, mode) {
  const sp = shownPanel(scene);
  if (!sp || !PICTURE_PANELS.includes(String(sp.panel && sp.panel.kind))) return null;
  const ref = sp.media && shownIn(sp.media, mode) ? sp.media : null;
  return { ref, title: String((sp.panel && sp.panel.title) || scene.title || ''), start: Math.max(0, Number(sp.show_at) || 0) };
}

/**
 * Whether any row enters on a narration moment (a beat or a reveal event): only then do rows take turns being current.
 * @param {TimedScene} scene @param {readonly string[]} ids
 */
export function anchored(scene, ids) {
  const keep = new Set(ids);
  return itemTimings(scene).some((it) => keep.has(it.id) && !it.initial);
}

/**
 * Layout variant: 9:16 `stack` (rows take turns being current) or `list` (no row is beat-revealed); 16:9 `split` (a
 * panel, or a stored icon for every row while rows take turns: never a current row with nothing beside it, B2c review
 * V12) or `centred`.
 * @param {TimedScene} scene @param {'landscape' | 'portrait'} format @param {'live' | 'render'} mode
 * @param {any} look
 * @returns {'split' | 'centred' | 'stack' | 'list'}
 */
export function variantOf(scene, format, mode, look) {
  const { rows } = listItems(scene);
  const ids = rows.map((r) => r.id);
  const turns = anchored(scene, ids);
  if (format === 'portrait') return turns ? 'stack' : 'list';
  if (panelOf(scene, mode)) return 'split';
  const icons = look && look.icons && typeof look.icons === 'object' ? look.icons : {};
  return turns && ids.length > 0 && ids.every((id) => Object.prototype.hasOwnProperty.call(icons, id)) ? 'split' : 'centred';
}

/**
 * Every TeX string the template renders for the scene (the static `tex` hook; render.js converts them before `ready`):
 * the `$...$` spans of the heading and the rows, inline (`richWords` mounts each with display off).
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  const { heading, rows } = listItems(scene);
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  for (const item of heading ? [heading, ...rows] : rows) {
    for (const tex of mathSpans(String(item.text || ''))) if (tex.trim()) out.push({ tex, display: false });
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

// ---------------------------------------------------------------------------------------------
// Measuring (layout time, on clones)
// ---------------------------------------------------------------------------------------------

/**
 * A row's height and its words' state when laid out at `size` px and `weight`, with `padY` px above and below its text
 * (PAD at the sides), in a card `width` px wide: a clone of the card in the hidden measuring host (so no painted
 * property of the real card is touched). `broken[i]`: word i is
 * broken inside (a plain word taller than 1.5 lines: overflow-wrap broke it) or, for a word holding TeX, wider than the
 * text box (it runs out sideways); `wide`: px the widest TeX word passes the text box.
 * @param {HTMLElement} host @param {any} row @param {number} width @param {number} size @param {number} weight
 * @param {number} padY
 * @returns {{height: number, broken: boolean[], wide: number}}
 */
function measureRow(host, row, width, size, weight, padY) {
  const c = /** @type {HTMLElement} */ (row.card.cloneNode(true));
  Object.assign(c.style, {
    position: 'absolute', left: '0px', top: '0px', width: `${width}px`, fontSize: `${size}px`, fontWeight: String(weight),
    padding: `${padY}px ${PAD}px`, visibility: 'hidden',
  });
  host.appendChild(c);
  const text = /** @type {HTMLElement} */ (c.querySelector('.dyn-lb-text'));
  const words = /** @type {HTMLElement[]} */ ([...c.querySelectorAll('.dyn-word')]);
  const box = text ? text.clientWidth : 0;
  const broken = words.map((w, i) => (row.wordTex[i] ? w.offsetWidth > box + 1 : w.offsetHeight > 1.5 * size * row.lh));
  const wide = Math.max(0, ...words.map((w, i) => (row.wordTex[i] ? w.offsetWidth - box : 0)));
  const height = c.offsetHeight;
  c.remove();
  return { height, broken, wide };
}

/**
 * The largest size (SIZE_STEP steps, `min` last) at which `ok(heights)` holds for every row measured at it with every
 * word whole (a word broken even at `min`, wider than the box, does not count against a size; a TeX word too wide at
 * `min` holds the rows at `min`, where it runs out least: board_generic's / `_shared.js fitLines`' rule), and the
 * heights there. `fits` false: nothing fits, the rows are at `min`.
 * @param {HTMLElement} host @param {any[]} rows @param {number} width @param {{max: number, min: number}} range
 * @param {number} weight @param {number} padY @param {(heights: number[]) => boolean} ok
 * @returns {{size: number, fits: boolean, heights: number[], wide: number[]}}
 */
function fitRows(host, rows, width, range, weight, padY, ok) {
  const atMin = rows.map((r) => measureRow(host, r, width, range.min, weight, padY));
  const texTooWide = atMin.some((m, k) => m.broken.some((b, i) => b && rows[k].wordTex[i]));
  for (const s of texTooWide ? [range.min] : sizeSteps(range.max, range.min)) {
    const ms = s === range.min ? atMin : rows.map((r) => measureRow(host, r, width, s, weight, padY));
    const whole = ms.every((m, k) => m.broken.every((b, i) => !b || atMin[k].broken[i]));
    const heights = ms.map((m) => m.height);
    if (whole && ok(heights)) return { size: s, fits: true, heights, wide: ms.map((m) => m.wide) };
  }
  return { size: range.min, fits: false, heights: atMin.map((m) => m.height), wide: atMin.map((m) => m.wide) };
}

// ---------------------------------------------------------------------------------------------
// Painting helpers
// ---------------------------------------------------------------------------------------------

/**
 * `{color: role => colour}` for state.js `itemStateStyle` with the inks of text drawn on the background (a colour
 * block's block inks; the content inks anywhere else, where this is exactly `ctx.roles`).
 * @param {TemplateContext} ctx
 */
function stateRoles(ctx) {
  const on = ctx.onBackground;
  return { color: (/** @type {string} */ role) => (role === 'ink' ? on.ink : role === 'ink2' ? on.ink2 : ctx.roles.color(role)) };
}

/** @param {string} hex @param {number} a  0..1 @returns {string} `#rrggbbaa` */
function withAlpha(hex, a) {
  const k = Math.round(Math.min(1, Math.max(0, a)) * 255).toString(16).padStart(2, '0');
  return `${String(hex).slice(0, 7)}${k}`;
}

/**
 * Paint one row's text entrance at progress `e` (the number) and `eText` (the text, 80 ms later): the 24 px slide
 * (`paintRise`), or the personality's baseline reveal of the text inside its mask (Crisp).
 * @param {TemplateContext} ctx @param {any} row @param {number} e @param {number} eText
 */
function paintRowEntrance(ctx, row, e, eText) {
  paintRise(ctx, row.num, e, RISE);
  if (row.mask) paintTextEntrance(ctx, row.text, row.mask, eText);
  else paintRise(ctx, row.text, eText, RISE);
}

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** @type {TemplateModule} */
export default {
  id: 'list_build',

  /**
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format } = ctx;
    if (!drawsScene(scene, format)) {
      // a side panel this template does not draw (a look naming it on such a scene): board_generic draws it whole
      const inner = boardGeneric.build(ctx);
      inner.lbFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const { heading, rows: items } = listItems(scene);
    let variant = variantOf(scene, format, ctx.mode, ctx.look);
    const panel = variant === 'split' ? panelOf(scene, ctx.mode) : null;
    // 16:9 split by icons: every row's stored icon (feature size, §4.12), else none at all and the list centred (an
    // icon the schema check refuses, `storedIcon` null, counts as missing: V12, never a current row with nothing beside)
    /** @type {(SVGElement | null)[] | null} */
    let icons = null;
    if (variant === 'split' && !panel) {
      const drawn = items.map((item) => storedIcon(ctx, item.id, { size: ICON_SIZE, block: true }));
      if (drawn.every(Boolean)) icons = drawn;
      else variant = 'centred';
    }
    const fixes = fixesOf(ctx.look);
    const baseline = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    const ink = ctx.onBackground.ink;
    const root = h('div', {
      class: ['dyn-lb', `dyn-lb--${format}`, `dyn-lb--${variant}`],
      style: { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' },
    });

    // heading (the display face; R-53's scrim remedy behind it when preflight asked, as board_generic's title block)
    /** @type {any} */
    let head = null;
    if (heading) {
      const lh = lineHeight('heading', heading.text || '');
      const text = h('h2', {
        class: 'dyn-lb-heading', dir: 'auto',
        style: { margin: '0px', fontFamily: 'var(--dyn-font-display)', fontWeight: 700, lineHeight: String(lh), overflowWrap: 'anywhere', color: ink },
      });
      const { words, tex } = richWords(ctx, text, String(heading.text || ''));
      const mask = baseline ? revealMask(text) : null;
      const scrim = fixes.slots.heading && fixes.slots.heading.scrim ? ctx.roles.color('scrim') : null;
      const el = group('dyn-lb-heading-group', mask || text);
      el.style.position = 'absolute';
      if (scrim) Object.assign(el.style, { padding: '10px 24px 14px', marginLeft: '-24px', borderRadius: `${RADIUS.card}px`, background: scrim });
      root.appendChild(el);
      head = { item: heading, el, text, mask, words, tex, lh, scrim, size: L.heading.max, overflow: 0, alpha: 0 };
    }

    // rows: a dim group per row > its card (state border, painted ink / weight / size) > body (number + text)
    // a scrim preflight planned for a row slot (`item`, or a 9:16 compact row's `row`) goes behind every row
    const rowFix = (/** @type {string} */ slot) => !!(fixes.slots[slot] && fixes.slots[slot].scrim);
    const itemScrim = rowFix('item') || rowFix('row') ? ctx.roles.color('scrim') : null;
    const rows = items.map((item, k) => {
      const lh = lineHeight('body', item.text || '');
      const num = h('span', {
        class: 'dyn-lb-num', text: String(k + 1), 'aria-hidden': 'true',
        style: { flex: 'none', width: `${L.num}px`, fontWeight: 700, fontVariantNumeric: 'tabular-nums', lineHeight: String(lh) },
      });
      const text = h('div', { class: 'dyn-lb-text', dir: 'auto', style: { lineHeight: String(lh), overflowWrap: 'anywhere' } });
      const { words, tex } = richWords(ctx, text, String(item.text || ''));
      const mask = baseline ? revealMask(text) : null;
      const textBox = h('div', { class: 'dyn-lb-textbox', style: { flex: '1 1 auto', minWidth: '0px' } }, mask || text);
      const body = h('div', { class: 'dyn-lb-body', style: { display: 'flex', alignItems: 'flex-start', gap: `${L.numGap}px` } }, num, textBox);
      const card = h('div', {
        class: 'dyn-lb-item', dataset: { item: item.id },
        style: {
          boxSizing: 'border-box', width: '100%', borderRadius: `${RADIUS.card}px`,
          // 16:9: PAD on every side; a 9:16 row's padding changes with its form, so update paints it (h() skips
          // an undefined value)
          padding: format === 'landscape' ? `${PAD}px` : undefined,
          boxShadow: `inset 0 0 0 ${STATE_BORDER}px var(--dyn-state-border, transparent)`, fontFamily: 'var(--dyn-font-body)',
          background: itemScrim || 'transparent',
        },
      }, body);
      const el = group('dyn-lb-row', card);
      el.style.position = 'absolute';
      root.appendChild(el);
      return {
        item, k, el, card, body, num, text, mask, words, wordTex: tex, lh,
        top: 0, large: 0, compact: 0, overflowLarge: 0, overflowCompact: 0,
        ink, weight: L.weight, size: L.item.max, alpha: 0, compactNow: false, icon: /** @type {HTMLElement | null} */ (null),
      };
    });

    // 16:9 split: the scene's panel in a dim group of its own on the right; without one, each row's stored icon
    // (feature size, §4.12, in the row's ink: a content role) in the row's own dim group, placed on the right at
    // layout, so a look_here on a row keeps its icon undimmed
    /** @type {any} */
    let visual = null;
    if (panel) {
      // sized by this template's mediaBox for slot 'panel' (the box mediaPlan extracts a clip at)
      const view = panel.ref ? ctx.media(panel.ref, { slot: 'panel', start: panel.start, alt: panel.title }) : null;
      const frame = h('div', { class: 'dyn-lb-media', style: { position: 'absolute', overflow: 'hidden', borderRadius: `${RADIUS.media}px` } }, view ? view.el : null);
      /** @type {HTMLElement | null} */
      let label = null;
      if (!view || view.missing) {
        // nothing this mode can show (a GIF in the MP4, an unsafe URL): the panel's title on the card surface
        // (board_generic's and media_split's convention: never an empty box beside the list)
        frame.style.background = ctx.roles.color('surface');
        label = h('div', {
          class: 'dyn-lb-missing', dir: 'auto', text: panel.title || '·',
          style: {
            position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px', display: 'flex', alignItems: 'center',
            justifyContent: 'center', padding: '0 1em', textAlign: 'center', fontSize: `${MISSING_LABEL_PX}px`,
            fontWeight: '600', lineHeight: '1.2', color: ctx.roles.color('ink2'), overflowWrap: 'anywhere',
          },
        });
        frame.appendChild(label);
      }
      // the whole stage, so the frame's box (set at layout) is in stage px
      const el = group('dyn-lb-visual', frame);
      Object.assign(el.style, { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' });
      root.appendChild(el);
      visual = { el, frame, view, label, ref: panel.ref, start: panel.start, missing: !view || !!view.missing };
    } else if (icons) {
      rows.forEach((r, k) => {
        r.icon = h('div', { class: 'dyn-lb-icon', style: { position: 'absolute', color: ink } }, icons && icons[k]);
        r.el.appendChild(r.icon);
      });
    }

    ctx.root.appendChild(root);
    const ids = rows.map((r) => r.item.id);
    /** @type {Map<string, number>} */
    const enter = new Map(itemTimings(scene).map((t) => [t.id, t.enter]));
    return {
      variant, root, head, rows, visual, ids, enter, itemScrim, states: variant === 'stack' || (format === 'landscape' && anchored(scene, ids)),
      order: itemTimings(scene).filter((t) => ids.includes(t.id)).map((t) => t.id),
      allCompact: false, listTop: 0, largeSize: L.item.max, compactSize: L.row ? L.row.max : L.item.max,
      itemMin: L.item.min, rowMin: L.row ? L.row.min : L.item.min,
    };
  },

  /**
   * Measure and fit (runs again when a late face arrives; idempotent): the heading (`_shared.js`-style fit, its own
   * element's size, which nothing paints), then the rows on clones: 16:9 and a 9:16 plain list at the largest common
   * size whose whole list fits under the heading; a 9:16 stack at the largest current size at which, for every row,
   * the compact rows above it plus it, large, fit. Positions are set here, once; `update` only paints.
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
    const b = boxes(format, v.variant, zones);
    // the heading: its own element's size (nothing paints it), fitted in place
    let listTop = b.list.y0;
    if (v.head) {
      const hd = v.head;
      const range = slotRange(ctx, 'heading', { min: L.heading.min, max: L.heading.max, role: 'title' });
      Object.assign(hd.el.style, { left: `${b.heading.x0}px`, top: `${b.heading.y0}px`, width: `${b.heading.x1 - b.heading.x0}px` });
      const spec = { max: range.max, min: range.min, lines: L.heading.lines, prefer: L.heading.prefer, lh: hd.lh, step: SIZE_STEP };
      const fit = fitLines(hd.text, spec, hd.words, hd.tex);
      hd.size = fit.size;
      hd.min = range.min;
      hd.overflow = fit.overflow;
      // below the heading as laid out, never above one line of it at its minimum (jsdom has no layout)
      const bottom = hd.el.offsetTop + hd.el.offsetHeight;
      listTop = Math.round(Math.max(bottom, b.heading.y0 + range.min * hd.lh) + L.headingGap);
    }
    v.listTop = listTop;
    const width = b.list.x1 - b.list.x0;
    const avail = b.bottom - listTop;
    const rows = v.rows;
    const item = slotRange(ctx, 'item', { min: L.item.min, max: L.item.max, role: 'body' });
    const rowRange = L.row ? slotRange(ctx, 'row', { min: L.row.min, max: L.row.max, role: 'body' }) : null;
    v.itemMin = item.min;
    v.rowMin = rowRange ? rowRange.min : item.min;
    v.allCompact = false;
    const host = h('div', {
      class: 'dyn-lb-measure', 'aria-hidden': 'true',
      style: { position: 'absolute', left: '0px', top: '0px', width: `${width}px`, visibility: 'hidden' },
    });
    ctx.root.appendChild(host);
    try {
      if (v.variant === 'stack' && rowRange) {
        // 9:16 stack: every row's top is the compact rows that entered before it (entrance order); the current size is
        // the largest at which each row, large, fits below them
        const compact = rows.map((/** @type {any} */ r) => measureRow(host, r, width, rowRange.max, L.weight, L.padCompact));
        /** @type {Map<string, number>} */
        const tops = new Map();
        let y = 0;
        for (const id of v.order) {
          const r = rows.find((/** @type {any} */ x) => x.item.id === id);
          tops.set(id, y);
          y += compact[r.k].height + L.gap;
        }
        const below = (/** @type {number[]} */ hs) => rows.every((/** @type {any} */ r, k) => (tops.get(r.item.id) || 0) + hs[k] <= avail);
        const fit = fitRows(host, rows, width, item, STATE_WEIGHT, PAD, below);
        v.largeSize = fit.size;
        v.compactSize = rowRange.max;
        for (const r of rows) {
          r.top = tops.get(r.item.id) || 0;
          // px past the list's bottom (or a TeX word past the text box) in each form the row is drawn in
          r.overflowLarge = Math.max(0, r.top + fit.heights[r.k] - avail, fit.wide[r.k] > 2 ? fit.wide[r.k] : 0);
          r.overflowCompact = Math.max(0, r.top + compact[r.k].height - avail, compact[r.k].wide > 2 ? compact[r.k].wide : 0);
        }
      } else {
        // a list (16:9; a 9:16 scene no beat walks through): every row at one size, the whole list under the heading;
        // 16:9 laid out at STATE_WEIGHT (a row turning current never re-wraps), 9:16 at its own weight (no states)
        const total = (/** @type {number[]} */ hs) => hs.reduce((a, x) => a + x, 0) + L.gap * Math.max(0, hs.length - 1);
        let fit = fitRows(host, rows, width, item, format === 'landscape' ? STATE_WEIGHT : L.weight, PAD, (hs) => total(hs) <= avail);
        if (!fit.fits && rowRange) {
          // a 9:16 list too long for large rows: every row compact (R-29's floor), never fewer rows
          fit = fitRows(host, rows, width, rowRange, L.weight, L.padCompact, (hs) => total(hs) <= avail);
          v.allCompact = true;
        }
        v.largeSize = fit.size;
        v.compactSize = fit.size;
        let y = 0;
        for (const r of rows) {
          r.top = y;
          const over = Math.max(0, y + fit.heights[r.k] - avail, fit.wide[r.k] > 2 ? fit.wide[r.k] : 0);
          r.overflowLarge = over;
          r.overflowCompact = over;
          y += fit.heights[r.k] + L.gap;
        }
      }
    } finally {
      host.remove();
    }
    for (const r of rows) {
      Object.assign(r.el.style, { left: `${b.list.x0}px`, top: `${listTop + r.top}px`, width: `${width}px` });
      // 16:9 rows keep one size, set here; a 9:16 row's size (large / compact) is painted per frame, never set here
      if (format === 'landscape') r.card.style.fontSize = `${v.largeSize}px`;
    }
    // 16:9 split: the box beside the list: the panel contained in it, or each icon centred in it (in its row's group,
    // so at the box's position relative to the row)
    if (b.visual) {
      const box = { ...b.visual, y0: listTop };
      if (v.visual) {
        const r = containRect(mediaAspect(v.visual.ref), box);
        Object.assign(v.visual.frame.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
      }
      const x = Math.round(box.x0 + (box.x1 - box.x0 - ICON_SIZE) / 2);
      const y = Math.round(box.y0 + Math.max(0, (box.y1 - box.y0 - ICON_SIZE) / 2));
      for (const r of rows) {
        if (!r.icon) continue;
        Object.assign(r.icon.style, { left: `${x - b.list.x0}px`, top: `${y - listTop - r.top}px`, width: `${ICON_SIZE}px`, height: `${ICON_SIZE}px` });
      }
    }
  },

  /**
   * Paint scene time t: the heading's entrance; each row's entrance, state and (9:16) size; the right-hand visual. A
   * pure function of t: every property painted on an element is painted on every frame, with a value (never '').
   * @param {TemplateContext} ctx
   * @param {number} t
   * @param {import('../../shared/types.js').SceneState} _state
   */
  update(ctx, t, _state) {
    if (isFallback(ctx)) {
      boardGeneric.update(ctx, t, _state);
      return;
    }
    const v = ctx.view;
    const p = ctx.paint;
    const L = LAYOUT[ctx.format];
    const portrait = ctx.format === 'portrait';

    if (v.head) {
      const e = enterAt(ctx, t, v.enter.get(v.head.item.id) || 0, HEADING_ENTER);
      v.head.alpha = e;
      paintTextEntrance(ctx, v.head.text, v.head.mask, e);
    }

    const st = v.states ? itemStatesAt(ctx.scene, t, v.ids) : null;
    const sroles = stateRoles(ctx);
    const cur = itemStateStyle('current', sroles);
    const past = itemStateStyle('past', sroles);
    const delay = seconds(ctx, TEXT_DELAY);
    const halfDip = seconds(ctx, DIP) / 2;
    const toPast = seconds(ctx, STATE_EASE);
    for (const r of v.rows) {
      const start = v.enter.get(r.item.id) || 0;
      const e = enterAt(ctx, t, start, ITEM_ENTER);
      const eText = enterAt(ctx, t, start + delay, ITEM_ENTER);
      const s = st ? st.states.get(r.item.id) : undefined;
      let weight = /** @type {number} */ (L.weight);
      let colour = ctx.onBackground.ink;
      let border = 0;
      let alpha = 1;
      let compact = v.allCompact;
      if (s && portrait) {
        // 9:16 stack: large while current, compact from the next row's entrance; the swap happens at the bottom of a
        // fade centred on that entrance (the row is invisible when its size changes)
        const turn = nextEnter(v, r.item.id);
        compact = s.state === 'past';
        if (turn !== null) alpha = ease('ease.move', Math.min(1, Math.abs(t - turn) / halfDip));
        weight = compact ? past.weight ?? L.weight : cur.weight ?? STATE_WEIGHT;
        colour = compact ? past.ink : cur.ink;
        border = compact ? 0 : 1;
      } else if (s) {
        // 16:9: current (bold, full ink, the border) from its entrance; past from the next row's entrance, its ink and
        // border eased over STATE_EASE (its weight changes at once: weights do not interpolate)
        const k = s.state === 'past' ? ease('ease.move', progress(t, s.since, toPast)) : 0;
        weight = s.state === 'past' ? past.weight ?? L.weight : cur.weight ?? STATE_WEIGHT;
        colour = k > 0 ? mixOklch(cur.ink, past.ink, k) : cur.ink;
        border = 1 - k;
      }
      const size = compact ? v.compactSize : v.largeSize;
      r.ink = colour;
      r.weight = weight;
      r.size = size;
      r.compactNow = compact;
      r.alpha = eText * alpha;
      p.style(r.card, 'opacity', fx(alpha));
      p.style(r.card, 'visibility', e > 0 && alpha > 0 ? 'visible' : 'hidden');
      p.style(r.card, 'color', colour);
      p.style(r.card, 'font-weight', String(weight));
      if (portrait) {
        p.style(r.card, 'font-size', `${size}px`);
        p.style(r.card, 'padding', `${compact ? L.padCompact : PAD}px ${PAD}px`);
      }
      p.style(r.card, '--dyn-state-border', withAlpha(String(cur.border), border * e));
      paintRowEntrance(ctx, r, e, eText);
    }

    // 16:9 split: the panel, or the current row's icon (in with its row, out when the next row takes over)
    if (v.visual) {
      const vis = v.visual;
      vis.alpha = enterAt(ctx, t, vis.start > 0 ? vis.start : PANEL_DELAY, PANEL_ENTER);
      const e = vis.alpha;
      paintRise(ctx, vis.frame, e, RISE);
      p.style(vis.frame, 'visibility', e > 0 ? 'visible' : 'hidden');
      // painted even while hidden, so the element never keeps a frame from an earlier seek
      if (vis.view) paintMedia(ctx, vis.view, t);
    }
    for (const r of v.rows) {
      if (!r.icon) continue;
      const s = st ? st.states.get(r.item.id) : undefined;
      const e = s ? enterAt(ctx, t, v.enter.get(r.item.id) || 0, ICON_ENTER) : 0;
      const out = s && s.state === 'past' ? ease('ease.exit', progress(t, s.since, seconds(ctx, ICON_EXIT))) : 0;
      const a = e * (1 - out);
      // in: a fade and a rise; out: a fade in place
      paintRise(ctx, r.icon, a, out > 0 ? 0 : RISE);
      p.style(r.icon, 'visibility', a > 0 ? 'visible' : 'hidden');
    }
  },

  /**
   * Where a look_here points (plan §1.4, §1.5, §1.7): a row's text box (`<item>`, or `<item>:term:<n>`: the same row;
   * its number and text, PAD inside the row's edge, so the outline, FOCUS_OUTSET outside it, lies on the row's edge
   * over its state border), or the heading's text; its box as painted now (read after `update`) on whole stage px, a
   * rectangle with the radius that puts the outline's curve on the row's corners, and the dim group holding it. Null
   * for anything else or off the page.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    const v = ctx.view;
    const id = String(target || '').split(':')[0];
    const row = v.rows.find((/** @type {any} */ r) => r.item.id === id);
    const el = row ? row.card : v.head && v.head.item.id === id ? v.head.text : null;
    const holder = row ? row.el : v.head && v.head.item.id === id ? v.head.el : null;
    const r = el ? ctx.rectOf(el) : null;
    if (!r || !holder) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    const x1 = Math.round(r.x + r.w);
    const y1 = Math.round(r.y + r.h);
    // a row: its card (the box its state border is drawn on), a shape whose outline runs outside it (V4); the
    // heading: its text
    return {
      rect: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, shape: 'rect', radius: row ? RADIUS.card : 0, el: holder,
      kind: row ? 'shape' : 'text',
    };
  },

  /**
   * The element holding an item's text, where cue.term finds its [[term]] spans (`.rt-k`, one per word piece with the
   * term's `data-dyn-term`, `_shared.js richWords`): a row's text, or the heading's. One element in every format (a
   * 9:16 row changes size in place, so the same spans are the term's large and compact). Null for anything else
   * (board_generic's answer for a scene it draws).
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const v = ctx.view;
    const row = v.rows.find((/** @type {any} */ r) => r.item.id === itemId);
    if (row) return row.text;
    return v.head && v.head.item.id === itemId ? v.head.text : null;
  },

  /**
   * The content groups a look_here dims (all but the target's): the heading, every row, the right-hand visual; the same
   * elements at every t.
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.head ? v.head.el : null, ...v.rows.map((/** @type {any} */ r) => r.el), v.visual ? v.visual.el : null].filter(Boolean);
  },

  /**
   * The morph anchor (plan §1.4): none (a list has no single shared visual; B2f pairs templates that have one).
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: the box the panel's picture or clip is shown in at most (slot 'panel', 16:9 split: the right 45% from the
   * top of the title zone, which a list without a heading starts at, to the body box's bottom); null otherwise (9:16
   * draws no panel). The fixes do not change it (the panel's box depends only on where the list starts).
   * @param {TimedScene} _scene
   * @param {Zones} zones
   * @param {'live' | 'render'} _mode
   * @param {string | undefined} slot
   * @param {import('../core.js').Fixes} _fixes
   * @returns {{w: number, h: number} | null}
   */
  mediaBox(_scene, zones, _mode, slot, _fixes) {
    if (!drawsScene(_scene, zones.format)) return typeof boardGeneric.mediaBox === 'function' ? boardGeneric.mediaBox(_scene, zones, _mode, slot, _fixes) : null;
    if (zones.format !== 'landscape' || slot !== 'panel') return null;
    const b = /** @type {Box} */ (boxes('landscape', 'split', zones).visual);
    return { w: b.x1 - b.x0, h: b.y1 - b.y0 };
  },

  /**
   * Static: every TeX string the template renders for the scene (`texOf`; the same in both formats).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} _look
   * @param {'landscape' | 'portrait'} _fmt
   */
  tex(scene, _look, _fmt) {
    if (!drawsScene(scene, _fmt)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, _look, _fmt) : [];
    return texOf(scene);
  },

  /**
   * The text on screen (preflight, §7.12): the heading (slot `heading`) and each row (slot `item`; a 9:16 compact
   * row, slot `row`), critical, in the ink and at the size and weight painted now, on the background's worst case (or
   * on the R-53 scrim over it).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    /** @type {TextEntry[]} */
    const out = [];
    /** @param {string | null} scrim @param {string} ink */
    const bgOf = (scrim, ink) => (scrim ? scrimmedBg(scrim, ctx.onBackground, ink) : ctx.onBackground.bg);
    if (v.head) {
      const hd = v.head;
      out.push({
        el: hd.text, role: 'title', slot: 'heading', critical: true, ink: ctx.onBackground.ink, bg: bgOf(hd.scrim, ctx.onBackground.ink),
        alpha: hd.alpha, overflow: hd.overflow, size: hd.size, min: hd.min ?? hd.size, weight: 700, text: plainText(String(hd.item.text || '')),
      });
    }
    for (const r of v.rows) {
      const compact = ctx.format === 'portrait' && (r.compactNow || v.allCompact);
      out.push({
        el: r.body, role: 'body', slot: compact ? 'row' : 'item', critical: true, ink: r.ink, bg: bgOf(v.itemScrim, r.ink),
        alpha: r.alpha, overflow: compact ? r.overflowCompact : r.overflowLarge, size: r.size,
        min: compact ? v.rowMin ?? r.size : v.itemMin ?? r.size, weight: r.weight,
        text: `${r.k + 1} ${plainText(String(r.item.text || ''))}`,
      });
    }
    if (v.visual && v.visual.label) {
      out.push({
        el: v.visual.label, role: 'label', critical: false, ink: ctx.roles.color('ink2'), bg: ctx.roles.color('surface'),
        alpha: v.visual.alpha || 0, size: MISSING_LABEL_PX, weight: 600, text: v.visual.label.textContent || '',
      });
    }
    return out;
  },
};

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.lbFallback);

/**
 * When the row after `id` (in entrance order) enters: the moment a 9:16 row stops being current; null for the last.
 * @param {any} v @param {string} id
 * @returns {number | null}
 */
function nextEnter(v, id) {
  const i = v.order.indexOf(id);
  const next = i >= 0 ? v.order[i + 1] : undefined;
  if (next === undefined) return null;
  const at = v.enter.get(next);
  return typeof at === 'number' ? at : null;
}
