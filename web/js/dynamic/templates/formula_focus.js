// @ts-check
/**
 * `formula_focus` (DYNAMIC_VIDEO_DESIGN §4.3 B, R1, class `formula`, dense; B2 plan §A step 3: the B2c pilot that takes
 * MathJax, the entity tints and the hold freeze through every gate). One equation and its symbols:
 *
 * - **Data** (§4.3 "a `formula` item with `variables`"; templates.py `_fit_formula_focus`): exactly one formula item,
 *   at most one heading, nothing else on the board and no side panel shown. Any other scene a look sends here is drawn
 *   by `board_generic` instead (`fitsScene`), whole: this template never drops content it cannot lay out.
 * - **16:9**: the headline (the heading item, else the scene title) at the top of the critical box; below it the
 *   formula, MathJax SVG at up to 1.6x board_generic's formula size (its 52 px body x 1.25 = 65 px, so 104 px), fitted
 *   to the card's width in 4 px steps, inside a surface card that is the "box" of §4.3 ("the result is boxed": a
 *   4 px structure border round the card, drawn in on the formula's `draw` event, or there from its entrance); the
 *   formula's own sentence (`item.text`) under it; the legend (one row per variable: its symbol, meaning and unit)
 *   below the card on the background, one column, two from five rows on. The card and legend are centred together in
 *   the room under the headline.
 * - **9:16** (§4.3 "fitted to 768 px, breaking at '=' if wider"): the card is 768 px wide (x 156-924; shapes may span
 *   x 60-1020) and its content keeps to R-59's critical text column x 192-888, so the formula is fitted to 696 px: the
 *   design's 768 px is the card, the rule's 696 px its text (R-59 wins over §4.3, as deviation 13 settled for code).
 *   When even the formula's smallest size is wider, it is set again broken at its top-level "=" signs (`breakAtEquals`:
 *   one line per "=", aligned on it); when that is still wider, the overflow is reported (preflight blocks with
 *   "Shorten text"; Python's lint `portrait.formula_wide` warns earlier). A formula that would reach below y 840
 *   (where the TikTok rail narrows the critical column to x 120-780) wider than that column is first set smaller in
 *   the wide card, down to its minimum, until it ends above y 840; only when it cannot does it get the narrow card
 *   (x 156-816, text x 192-780). The sentence (left-aligned) and the legend keep to the text column x 192-780.
 *   Everything ends above the caption band (y 1020).
 * - **Fit** (`layout`): sizes shrink in 4 px steps to their R-29 minimums (legend, sentence, headline, formula last);
 *   when that is not enough the sentence is left out (§4.3 names no sentence for this template; the legend is its
 *   own); what still does not fit is reported as overflow (preflight blocks with "Shorten text", §7.12). Every legend
 *   row stays on screen once it entered: no row ever leaves to make room (RV L-13: 9:16 never makes content disappear,
 *   never a one-item carousel; B2c review V2 removed a "compact stack" that faded the oldest rows out).
 * - **Choreography** (§4.3, §4.8): the headline enters with the personality's text entrance (`ctx.motion.entrance`);
 *   the card fades in (400 ms) on the formula's entrance (its reveal event, else its beat, R-17's 120 ms lead); on each
 *   symbol's `var` event (`<item>:var:<symbol>`, events.py; B1's `var:<n>` sync cue until B2d writes them) the symbol
 *   tints to its lesson-wide entity colour (§4.7, R-30: `presentation.entity_colors` through `ctx.entityRole`; a
 *   symbol without a slot stays `ink`), a brace draws at it (300 ms) and its legend row enters. The brace is the
 *   symbol's own pointer, so it is placed at the symbol's glyphs (`bracePlace`, B2c review V1): under them when the
 *   room under them is clear of the formula's other glyphs, else over them (a numerator, whose fraction bar is right
 *   under it), else none (the tint and the legend row still mark it); a band of room is kept in the card above or
 *   below the formula where a brace reaches past it. A brace stays until the next symbol's cue or the first look_here
 *   after it, whichever comes first, the last one at most CUE_HOLD (B2c review V3: never a second pointer next to the
 *   R-16 walk's outline, never a lone brace through the hold), and leaves 30% faster than it drew. No camera motion
 *   (R-30: the template never transforms anything but its own text entrances, and its spec is not push_ok). Every
 *   duration is the personality's (`_shared.js` `seconds`).
 * - **Holds** (R-16, R-55): the background freezes from the scene start because the template is dense (the resolver
 *   writes `look.background.frozen_at = 0`, presentation.py DENSE_TEMPLATES; the stage pins the background clock and
 *   desaturates it, state.js / stage.js), and the R-16 hold's look_here walk over the symbols is drawn by cues.js from
 *   `focusRect(ctx, '<item>:var:<symbol>')`, which keeps the symbol's own legend row bright (`FocusRect.keep`: every
 *   legend row is a dim group of its own) while the rest dims. No SFX (R-49): the template makes no sound.
 * - **Emphasis** is cues.js's alone (§4.2): this module never writes `outline`, `text-decoration`, `filter` or the
 *   `mark` / `focus` roles; the tints are content colours, the braces and the box are drawings.
 *
 * Determinism (§7.4): `update` is a pure function of t and the laid-out page; every per-frame write goes through
 * `ctx.paint` and every element it paints is written on every frame (so no frame depends on the frames before);
 * `layout` only measures the page and places, sizes and fits (idempotent; it runs again when a late face arrives).
 * TeX is converted only through `ctx.renderTex` (`_shared.js` `tex` / `richWords`), and the static `tex()` declares
 * every string `build` converts, in both formats (the render page converts them before `ready`, plan §2).
 */

import { h, svg } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { composite, contrastRatio, mixOklch } from '../../motion/color.js';
import { splitWords } from '../../motion/text.js';
import { mathSpans } from '../../player/richtext.js';
import { planScene } from '../../player/schedule.js';
import { LAYOUT_TOKENS, fixesOf, fx, parseTarget } from '../core.js';
import { CUE_HOLD, cueEvents } from '../cues.js';
import { REVEAL_LEAD, itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import {
  RADIUS, TEXT_RISE, enterAt, entityColour, fitLines, group, paintTint, revealMask, richWords, seconds, slotRange,
  tex, texTokens, tintTargets, tintTex,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../../shared/types.js').SceneState} SceneState */
/** @typedef {import('../core.js').Box} Box */

export const ID = 'formula_focus';

// ---------------------------------------------------------------------------------------------
// Layout (stage px). aadhi/compose/dynamic/templates.py mirrors these as the template's slots (SlotSpec per slot and
// format, variant `default`); web/tests/dynamic/templates/formula_focus.test.js pins the two against each other.
// ---------------------------------------------------------------------------------------------

/**
 * Per-format geometry and text slots (R-29 sizes; size ranges are [min, max] px, the page fits in 4 px steps):
 * - `title`: the headline, board_generic's title slot (display face, 700; 9:16 one line preferred, R-25);
 * - `formula`: the TeX size (MathJax's em): 16:9 up to 1.6 x 65 px (§4.3), down to 64; 9:16 68-96 (R-29 asks
 *   weight >= 600 of 9:16 text at <= 64 px, and MathJax's glyphs have one weight: the formula stays above 64);
 * - `text`: the formula's own sentence (body; R-29 body 48-56 in 16:9, 56-64 in 9:16, weight >= 600 there);
 * - `legend`: a legend row (a label: R-29 >= 40 px in 16:9, >= 44 in 9:16).
 * `card`: the box's padding and (16:9) its smallest inner width (its x span: `geometry`);
 * `titleGap` / `legendGap` / `rowGap` / `columnGap`: spacing steps (the catalogue's 8 px scale); `twoColumns`: the 16:9
 * legend takes two columns from this many rows on.
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    title: Object.freeze({ max: 96, min: 72, lines: 2, prefer: 0, weight: 700 }),
    formula: Object.freeze({ max: 104, min: 64 }),
    text: Object.freeze({ max: 52, min: 48, lines: 2, weight: 400 }),
    legend: Object.freeze({ max: 48, min: 40, weight: 400 }),
    card: Object.freeze({ padX: 56, padY: 40, minInner: 640 }),
    titleGap: 40,
    legendGap: 32,
    rowGap: 8,
    columnGap: 48,
    twoColumns: 5,
  }),
  portrait: Object.freeze({
    title: Object.freeze({ max: 88, min: 56, lines: 2, prefer: 1, weight: 700 }),
    formula: Object.freeze({ max: 96, min: 68 }),
    text: Object.freeze({ max: 60, min: 56, lines: 3, weight: 600 }),
    legend: Object.freeze({ max: 52, min: 44, weight: 600 }),
    card: Object.freeze({ padX: 36, padY: 24, minInner: 0 }),
    titleGap: 24,
    legendGap: 16,
    rowGap: 4,
    columnGap: 0,
    twoColumns: Infinity,
  }),
});

/**
 * Line heights (§4.6 type scale, the catalogue's `type.scale`, pinned by the test): Latin display 1.05 (its range
 * 1.0-1.05) and body 1.35; Indic display 1.25 and body 1.45 (their floors), so Tamil and Devanagari marks above and
 * below the line are never cut.
 */
export const LINE_HEIGHT = Object.freeze({
  display: Object.freeze({ latin: 1.05, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
});
/**
 * The formula slot's text metrics besides its sizes (templates.py's SlotSpec; what `texts()` tells preflight): a TeX
 * picture reported as body text (its size is MathJax's em, set on the body face, whose ex MathJax scales by), weight
 * 400, line height 1 (the picture sets its own height; the slot's `max_lines` is none).
 */
export const FORMULA_SLOT = Object.freeze({ role: 'body', face: 'body', weight: 400, lineHeight: 1 });
/** Letter-spacing of the headline (dynamic.css `.dyn-title`, board_generic's; the slot's `tracking_em`). */
export const TITLE_TRACKING = -0.015;
/** Indic scripts (Devanagari .. Sinhala: U+0900-U+0DFF): text holding any takes the Indic line heights. */
const INDIC_FIRST = 0x0900;
const INDIC_LAST = 0x0dff;

/**
 * Where things go, from the format's zones (the catalogue's, core.js ZONES; §2.5, R-59):
 * - `title`: the headline's box, 16:9 the critical box's width from its top (this template has no kicker), 9:16 the
 *   headline region (zones.title: x 192-888 from y 340);
 * - `bottom`: where content ends: 16:9 the critical box's bottom (y 912), 9:16 above the caption band (zones.body,
 *   y 1020);
 * - `rail`: 9:16 the y below which the critical column narrows to x 120-780 (the TikTok action rail; Infinity in 16:9);
 * - `wide` / `narrow`: 9:16 the card's x spans, its padding round R-59's text column above the rail (x 192-888: the card
 *   is 768 px, §4.3) and round the text column that holds below it too (zones.textColumn, x 192-780); null in 16:9;
 * - `legend`: 9:16 the legend's and the sentence's x span (the text column); null in 16:9 (centred in the card);
 * - `below`: 9:16 the critical column's x span below the rail (x 120-780); null in 16:9;
 * - `centre` / `maxWidth`: 16:9 the critical box's centre and width (the card and the legend are centred on it).
 * @param {import('../core.js').Zones} zones
 */
export function geometry(zones) {
  if (zones.format === 'portrait') {
    const t = zones.title;
    const col = zones.textColumn;
    const pad = LAYOUT.portrait.card.padX;
    const rail = zones.critical.length > 1 ? zones.critical[1].y0 : Infinity;
    return {
      title: { x0: t.x0, x1: t.x1, y0: t.y0 }, bottom: zones.body.y1, rail,
      wide: { x0: t.x0 - pad, x1: t.x1 + pad }, narrow: { x0: col.x0 - pad, x1: col.x1 + pad },
      legend: { x0: col.x0, x1: col.x1 }, centre: (t.x0 + t.x1) / 2, maxWidth: t.x1 - t.x0,
      below: zones.critical.length > 1 ? { x0: zones.critical[1].x0, x1: zones.critical[1].x1 } : null,
    };
  }
  const c = zones.critical[0];
  return {
    title: { x0: c.x0, x1: c.x1, y0: c.y0 }, bottom: c.y1, rail: Infinity, wide: null, narrow: null, legend: null,
    centre: (c.x0 + c.x1) / 2, maxWidth: c.x1 - c.x0, below: null,
  };
}

/**
 * The widths the page lays each text slot out in at most (templates.py's SlotSpec `w`; the parity test's): the
 * headline's box, the formula's and the sentence's (the card's inner width: 16:9 the widest card, 9:16 the wide card
 * for the formula and the narrow one for the sentence, the conservative one), a legend row's (16:9 a column of the
 * two-column legend, 9:16 the text column).
 * @param {import('../core.js').Zones} zones
 */
export function slotWidths(zones) {
  const g = geometry(zones);
  const L = LAYOUT[zones.format];
  const pad = 2 * L.card.padX;
  if (g.wide && g.narrow && g.legend) {
    return { title: g.title.x1 - g.title.x0, formula: g.wide.x1 - g.wide.x0 - pad, text: g.narrow.x1 - g.narrow.x0 - pad, legend: g.legend.x1 - g.legend.x0 };
  }
  return { title: g.title.x1 - g.title.x0, formula: g.maxWidth - pad, text: g.maxWidth - pad, legend: (g.maxWidth - L.columnGap) / 2 };
}

/** The box: a 4 px structure border (§4.7 "card borders 4-6 px"; the catalogue's `border.structure_min`). */
export const BOX_PX = Number(LAYOUT_TOKENS.border.structure_min);
/** Brace stroke width, px (a drawing on the card: graphics >= 3:1, R-53; entity colours hold 4.5:1 on surface). */
export const BRACE_PX = 4;
/** A brace's room: the gap between it and its symbol's glyphs (and the gap it keeps clear past its tip), its height as
 * a share of the TeX size. */
export const BRACE_GAP = 8;
export const BRACE_SHARE = 0.24;
export const BRACE_MIN = 12;
export const BRACE_MAX = 28;
/** The gap between the formula (or its brace band) and its sentence. */
export const TEXT_GAP = 16;
/** A text-entrance mask's clip reaches this share of the text size past its box (Indic marks, TeX in a row). */
export const MASK_PAD = 0.3;

// Motion (§4.8; every one scaled by the personality, `_shared.js` seconds)
/** The headline enters after the scene's first frame (board_generic's TITLE_DELAY) over WORD_SECONDS. */
export const TITLE_DELAY = 0.08;
export const TITLE_SECONDS = 0.45;
/** §4.3: the formula fades in over 400 ms. */
export const FORMULA_FADE = 0.4;
/** The formula's sentence follows the formula. */
export const TEXT_DELAY = 0.15;
export const TEXT_SECONDS = 0.45;
/** A symbol tints over 300 ms on its var cue; its brace draws over 300 ms (§4.3 "braces draw (300 ms)"). */
export const TINT_SECONDS = 0.3;
export const BRACE_DRAW = 0.3;
/** §4.8: exits are 30% shorter than entrances. */
export const BRACE_EXIT = BRACE_DRAW * 0.7;
/** A legend row enters over 450 ms (§4.8 entrance 400-600 ms); rows with no cue follow the formula, staggered. */
export const ROW_SECONDS = 0.45;
export const ROW_STAGGER = 0.09;
/** The last symbol's brace stays at most this long after its cue (no next symbol ends it): a cue's hold without its
 * own `dur` (cues.js CUE_HOLD; B2c review V3). */
export const BRACE_HOLD = CUE_HOLD;
/** The box draws round the card over 600 ms on a `draw` event (§4.8 "stroke draw 600-1200 ms per group"). */
export const BOX_DRAW = 0.6;
/** Corner radius of a look_here outline's target box round a symbol (cues.js adds its outset). */
export const SYMBOL_RADIUS = 8;

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/**
 * The scene's formula item, or null.
 * @param {TimedScene} scene
 * @returns {BoardItem | null}
 */
export function formulaOf(scene) {
  return (scene.board || []).find((i) => i && i.kind === 'formula' && typeof i.latex === 'string' && i.latex.trim() !== '') || null;
}

/**
 * The scene's heading item, or null.
 * @param {TimedScene} scene
 * @returns {BoardItem | null}
 */
export function headingOf(scene) {
  return (scene.board || []).find((i) => i && i.kind === 'heading') || null;
}

/**
 * Whether this template can draw the scene whole (§4.3 data; templates.py `_fit_formula_focus` with the side-panel
 * rule this template adds): exactly one formula item with LaTeX, at most one heading, no other board item, no quiz,
 * main medium or poster, and no side panel shown. Anything else is drawn by board_generic (`build`).
 * @param {TimedScene} scene
 */
export function fitsScene(scene) {
  const board = scene.board || [];
  const formulas = board.filter((i) => i && i.kind === 'formula');
  const headings = board.filter((i) => i && i.kind === 'heading');
  if (formulas.length !== 1 || !formulaOf(scene) || headings.length > 1) return false;
  if (formulas.length + headings.length !== board.length) return false;
  if (scene.quiz || scene.media || scene.poster) return false;
  return !(scene.side_panel && scene.layout?.show_side_panel !== false);
}

/**
 * The symbols of a formula's variables, as written (`FormulaVariable.symbol_latex`), in legend order.
 * @param {BoardItem} item
 * @returns {string[]}
 */
export function symbolsOf(item) {
  return (item.variables || []).map((v) => String(v.symbol_latex || ''));
}

/**
 * The formula's LaTeX with each symbol of its variables in a `\color` placeholder (`_shared.js` tintTex, §P (g)).
 * @param {BoardItem} item
 */
export function formulaTex(item) {
  return tintTex(String(item.latex || ''), symbolsOf(item));
}

/**
 * The LaTeX split at its top-level "=" signs (outside every group, `\left...\right` pair and environment), or null
 * when it has none, or is already set on several lines (a top-level `\\` or `&`). Pure.
 * @param {string} latex
 * @returns {string[] | null}
 */
export function breakAtEquals(latex) {
  const src = String(latex || '');
  const toks = texTokens(src);
  let braces = 0;
  let pairs = 0;
  let envs = 0;
  /** @type {number[]} source offsets of the top-level "=" tokens */
  const at = [];
  for (let i = 0; i < toks.length; i++) {
    const v = toks[i].v;
    if (v === '{') braces++;
    else if (v === '}') braces--;
    else if (v === '\\left') pairs++;
    else if (v === '\\right') pairs--;
    else if (v === '\\begin') envs++;
    else if (v === '\\end') envs--;
    const top = braces === 0 && pairs === 0 && envs === 0;
    if (!top) continue;
    if (v === '&' || v === '\\\\') return null;
    // a script's "=" (x^= ...) belongs to its base, not to the equation
    const prev = i > 0 ? toks[i - 1].v : '';
    if (v === '=' && prev !== '^' && prev !== '_') at.push(toks[i].a);
  }
  if (!at.length) return null;
  /** @type {string[]} */
  const parts = [];
  let from = 0;
  for (const a of at) {
    parts.push(src.slice(from, a).trim());
    from = a + 1;
  }
  parts.push(src.slice(from).trim());
  // an empty side ("= 5", "a = = b") has nothing to set on its own line
  return parts.every((x) => x !== '') ? parts : null;
}

/**
 * The 9:16 broken form of a formula (§4.3 "breaking at '=' if wider"): `aligned`, the left side on its own line and
 * each "= ..." on the next, every line starting at the one alignment point (the "=" signs under the left side's start:
 * aligned on the "=" itself, the lines would form a staircase as wide as the whole formula, measured in real
 * Chromium), every part tinted like the whole formula (each part through
 * `tintTex` on its own, so no placeholder can land inside the environment's own markup; a symbol's placeholder colour
 * depends only on its place in the legend, so the parts share them). Null when the formula has no top-level "=".
 * @param {BoardItem} item
 * @returns {string | null}
 */
export function brokenTex(item) {
  const parts = breakAtEquals(String(item.latex || ''));
  if (!parts) return null;
  const symbols = symbolsOf(item);
  const tinted = parts.map((p) => tintTex(p, symbols).tex);
  return `\\begin{aligned}&${tinted[0]}${tinted.slice(1).map((p) => ` \\\\ &= ${p}`).join('')}\\end{aligned}`;
}

/**
 * Every TeX string this template converts for a scene in `fmt` (the static `tex` hook; plan §1.4, §2): the formula
 * (display, tinted), in 9:16 its broken form too (layout picks one), each variable's symbol (inline, its legend row)
 * and the `$...$` spans of the headline (a heading item's rich-lite text; a scene title is plain) and of the formula's
 * sentence. Repeats kept (render.js keeps the first).
 * @param {TimedScene} scene
 * @param {'landscape' | 'portrait'} fmt
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene, fmt) {
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  const item = formulaOf(scene);
  if (!item) return out;
  /** @param {string} s @param {boolean} display */
  const add = (s, display) => {
    if (typeof s === 'string' && s.trim()) out.push({ tex: s, display });
  };
  /** @param {unknown} text */
  const spans = (text) => {
    if (typeof text === 'string' && text.includes('$')) for (const s of mathSpans(text)) add(s, false);
  };
  const heading = headingOf(scene);
  if (heading) spans(heading.text);
  add(formulaTex(item).tex, true);
  if (fmt === 'portrait') add(brokenTex(item) || '', true);
  spans(item.text);
  for (const s of symbolsOf(item)) add(s, false);
  return out;
}

/**
 * The legend row of `symbol` (its index in the variables), matching the symbol as written, else with its whitespace
 * removed (core.js entityRole's rule); -1 for none.
 * @param {BoardItem} item @param {string} symbol
 */
export function variableIndex(item, symbol) {
  const symbols = symbolsOf(item);
  const exact = symbols.indexOf(symbol);
  if (exact >= 0) return exact;
  const bare = String(symbol).replace(/\s+/g, '');
  return symbols.findIndex((s) => s.replace(/\s+/g, '') === bare);
}

/**
 * When each variable is introduced (scene seconds; index = legend row), and whether a cue anchors it: its earliest
 * `var` event on `<item>:var:<symbol>` (events.py writes them on the anchor word, as `t` = the start of the visual),
 * else B1's word-anchored `var:<n>` sync cue (aadhi/compose/sync.py; R-17's 120 ms lead before its word, as
 * board_generic draws it), else none (`at: null`: the row and the tint come with the formula). Never before the formula
 * itself enters (`enter`).
 * @param {TimedScene} scene @param {BoardItem} item @param {number} enter
 * @returns {{at: number | null}[]}
 */
export function variableTimes(scene, item, enter) {
  const n = (item.variables || []).length;
  /** @type {(number | null)[]} */
  const fromEvents = new Array(n).fill(null);
  for (const e of scene.events || []) {
    if (!e || e.kind !== 'var' || typeof e.t !== 'number' || !Number.isFinite(e.t)) continue;
    const target = parseTarget(e.target);
    if (!target || target.form !== 'item:var' || target.item !== item.id) continue;
    const k = variableIndex(item, String(target.symbol));
    if (k < 0) continue;
    const cur = fromEvents[k];
    if (cur === null || e.t < cur) fromEvents[k] = e.t;
  }
  const sync = planScene(scene).sync.partAt;
  return fromEvents.map((t, k) => {
    let at = t;
    if (at === null) {
      const s = sync.get(`${item.id}|var:${k}`);
      if (typeof s === 'number' && Number.isFinite(s)) at = Math.max(0, s - REVEAL_LEAD);
    }
    return { at: at === null ? null : Math.max(enter, at) };
  });
}

/**
 * When the box draws (§4.3 "the result is boxed"): the earliest `draw` event on the formula item (`<item>`), never
 * before the formula enters; null without one (the box comes with the card).
 * @param {TimedScene} scene @param {BoardItem} item @param {number} enter
 * @returns {number | null}
 */
export function boxDrawAt(scene, item, enter) {
  let at = null;
  for (const e of scene.events || []) {
    if (!e || e.kind !== 'draw' || typeof e.t !== 'number' || !Number.isFinite(e.t)) continue;
    const target = parseTarget(e.target);
    if (!target || target.form !== 'item' || target.item !== item.id) continue;
    if (at === null || e.t < at) at = e.t;
  }
  return at === null ? null : Math.max(enter, at);
}

/**
 * The SVG path of a brace over [x0, x1] whose ends are at y, `height` px tall, its tip at the middle (quadratic
 * curves; whole and half px): `dir` 1 an under-brace (its tip below y), -1 an over-brace (its tip above y). Drawn
 * left to right.
 * @param {number} x0 @param {number} x1 @param {number} y @param {number} height @param {1 | -1} [dir]
 */
export function bracePath(x0, x1, y, height, dir = 1) {
  const w = Math.max(0, x1 - x0);
  const q = Math.min(height / 2, w / 4);
  const mid = x0 + w / 2;
  const half = (/** @type {number} */ v) => fx(Math.round(v * 2) / 2, 1);
  const ym = y + (dir * height) / 2;
  const tip = y + dir * height;
  return `M${half(x0)} ${half(y)}Q${half(x0)} ${half(ym)} ${half(x0 + q)} ${half(ym)}L${half(mid - q)} ${half(ym)}`
    + `Q${half(mid)} ${half(ym)} ${half(mid)} ${half(tip)}Q${half(mid)} ${half(ym)} ${half(mid + q)} ${half(ym)}`
    + `L${half(x1 - q)} ${half(ym)}Q${half(x1)} ${half(ym)} ${half(x1)} ${half(y)}`;
}

/**
 * @typedef {{x: number, y: number, w: number, h: number}} Rect
 * @typedef {{dir: 1 | -1, x0: number, x1: number, y: number}} BracePlace
 */

/**
 * Where a symbol's brace goes (B2c review V1; pure): at the symbol's own glyphs `r`, so it points at that symbol in any
 * formula, a stacked one included. On the open side first: under them (`dir` 1, its ends BRACE_GAP below them) when
 * no other glyph of the formula (`others`) lies anywhere below them across the symbol's x span, else over them
 * (`dir` -1) when none lies above (a numerator: its fraction bar and denominator are under it, so its brace goes over
 * it, never squeezed between it and the bar, which reads as part of the fraction). A symbol with glyphs on both sides
 * (the middle of a stack) takes the side with room for a brace (its gap, its height and a gap past its tip), under
 * first; else null (no brace: the tint and the legend row still mark it). Room past the formula's own box is the
 * card's band, which `layout` keeps (`braceBands`). Whole px.
 * @param {Rect} r   the symbol's glyphs (its first placeholder group as drawn)
 * @param {Rect[]} others   every other glyph of the formula (`glyphRects`)
 * @param {number} height   the brace's height
 * @returns {BracePlace | null}
 */
export function bracePlace(r, others, height) {
  const x0 = Math.round(r.x);
  const x1 = Math.round(r.x + r.w);
  const reach = 2 * BRACE_GAP + height;
  /** @param {number} y0 @param {number} y1 */
  const clear = (y0, y1) => !others.some((o) => Math.min(o.x + o.w, x1) - Math.max(o.x, x0) > 1
    && Math.min(o.y + o.h, y1) - Math.max(o.y, y0) > 0);
  const bottom = Math.round(r.y + r.h);
  const top = Math.round(r.y);
  const under = { dir: /** @type {1} */ (1), x0, x1, y: bottom + BRACE_GAP };
  const over = { dir: /** @type {-1} */ (-1), x0, x1, y: top - BRACE_GAP };
  if (clear(bottom, Infinity)) return under;
  if (clear(-Infinity, top)) return over;
  if (clear(bottom, bottom + reach)) return under;
  if (clear(top - reach, top)) return over;
  return null;
}

/**
 * The room the braces need past the formula's box `f` (px above its top and below its bottom, whole, >= 0): the card
 * keeps it as bands, so a brace never reaches the card's edge, the sentence or the headline.
 * @param {Rect} f @param {(BracePlace | null)[]} places @param {number} height
 */
export function braceBands(f, places, height) {
  let above = 0;
  let below = 0;
  for (const p of places) {
    if (!p) continue;
    // from the brace's ends to past its tip, with BRACE_GAP clear beyond
    if (p.dir === 1) below = Math.max(below, p.y + height + BRACE_GAP - (f.y + f.h));
    else above = Math.max(above, f.y - (p.y - height - BRACE_GAP));
  }
  return { above: Math.max(0, Math.ceil(above)), below: Math.max(0, Math.ceil(below)) };
}

/**
 * The boxes of a TeX picture's glyphs and rules (MathJax SVG: a glyph is a `<use>` of its cached path, a fraction bar
 * or a root's vinculum a `<rect>`, a stretchy part a `<path>`; nothing in `<defs>`), stage px, skipping the elements
 * inside `skip` (the symbol's own groups) and empty boxes.
 * @param {TemplateContext} ctx @param {Element} texEl @param {Element[]} skip
 * @returns {Rect[]}
 */
function glyphRects(ctx, texEl, skip) {
  /** @type {Rect[]} */
  const out = [];
  for (const el of texEl.querySelectorAll('use, rect, path, text, line, polygon')) {
    if (el.closest('defs') || skip.some((s) => s === el || s.contains(el))) continue;
    const r = ctx.rectOf(el);
    if (r && r.w > 0 && r.h > 0) out.push(r);
  }
  return out;
}

/**
 * The SVG path of the box: a rounded rectangle `w` x `h` with corner radius `r`, its stroke of `px` inside it,
 * clockwise from the top edge's left end.
 * @param {number} w @param {number} hh @param {number} r @param {number} px
 */
export function boxPath(w, hh, r, px) {
  const i = px / 2;
  const x0 = i;
  const y0 = i;
  const x1 = Math.max(x0, w - i);
  const y1 = Math.max(y0, hh - i);
  const rad = Math.max(0, Math.min(r - i, (x1 - x0) / 2, (y1 - y0) / 2));
  const q = (/** @type {number} */ v) => fx(v, 1);
  return `M${q(x0 + rad)} ${q(y0)}H${q(x1 - rad)}A${q(rad)} ${q(rad)} 0 0 1 ${q(x1)} ${q(y0 + rad)}V${q(y1 - rad)}`
    + `A${q(rad)} ${q(rad)} 0 0 1 ${q(x1 - rad)} ${q(y1)}H${q(x0 + rad)}A${q(rad)} ${q(rad)} 0 0 1 ${q(x0)} ${q(y1 - rad)}`
    + `V${q(y0 + rad)}A${q(rad)} ${q(rad)} 0 0 1 ${q(x0 + rad)} ${q(y0)}Z`;
}

/** @param {string} text */
function isIndic(text) {
  for (const c of String(text || '')) {
    const cp = /** @type {number} */ (c.codePointAt(0));
    if (cp >= INDIC_FIRST && cp <= INDIC_LAST) return true;
  }
  return false;
}

/**
 * A plain text (a scene title) as word spans (`.dyn-word`, Intl.Segmenter units: Indic clusters stay whole), as
 * board_generic's title has them, so `fitLines` sees a word broken inside.
 * @param {HTMLElement} host @param {string} text @param {string} locale
 * @returns {HTMLElement[]}
 */
function plainWords(host, text, locale) {
  /** @type {HTMLElement[]} */
  const out = [];
  for (const part of splitWords(text, locale)) {
    const span = h('span', { class: 'dyn-word', text: part.text });
    out.push(span);
    host.append(span, part.ws ? document.createTextNode(part.ws) : '');
  }
  return out;
}

/**
 * What text of `ink` on a translucent scrim sits on: the scrim over each colour the background shows, the one with
 * the least contrast against `ink` (R-53's worst case; board_generic's `scrimmedBg`).
 * @param {string} scrim @param {{bg: string, bgs?: string[]}} onBackground @param {string} ink
 */
function scrimmed(scrim, onBackground, ink) {
  const bgs = onBackground.bgs && onBackground.bgs.length ? onBackground.bgs : [onBackground.bg];
  let worst = composite(scrim, bgs[0]);
  for (const b of bgs.slice(1)) {
    const c = composite(scrim, b);
    if (contrastRatio(ink, c) < contrastRatio(ink, worst)) worst = c;
  }
  return worst;
}

/**
 * Wrap a text element in the personality's entrance mask (`_shared.js` revealMask; Crisp's baseline reveal), its clip
 * reaching MASK_PAD past the text (set at layout), or leave it bare (any other entrance moves no clip).
 * @param {TemplateContext} ctx @param {HTMLElement} el
 * @returns {HTMLElement | null}
 */
function maskFor(ctx, el) {
  return ctx.motion && ctx.motion.entrance === 'baseline_reveal' ? revealMask(el) : null;
}

/**
 * Give a mask its clip room round text of `size` px (padding out, margin back in: the layout does not move).
 * @param {HTMLElement | null} mask @param {number} size
 */
function padMask(mask, size) {
  if (!mask) return;
  const pad = Math.round(MASK_PAD * size);
  mask.style.padding = `${pad}px 0`;
  mask.style.margin = `-${pad}px 0`;
}

/**
 * Paint the personality's text entrance (`ctx.motion.entrance`, §4.8) at progress `e`: `_shared.js` paintTextEntrance's
 * rule (`baseline_reveal` slides the text up from below its mask's bottom edge, anything else fades with a TEXT_RISE
 * rise, reduced motion fades in place), with a 2D `translate` on whole px instead of `translate3d`. Measured in real
 * Chromium (B2c, Windows, --disable-partial-raster): TeX (an inline MathJax SVG) sliding by `translate3d` inside the
 * overflow-clipped mask of the baseline reveal rasterised up to 2 levels differently in a forward and a reverse render
 * of the same frames (the bottom row of the glyphs, while it moved); with `translate` 0 frames differed. A 2D transform
 * is painted in its parent's layer, at its whole-px offset, like a still element (§7.4).
 * @param {TemplateContext} ctx @param {HTMLElement} el @param {HTMLElement | null} mask @param {number} e
 */
function paintEntrance(ctx, el, mask, e) {
  const p = ctx.paint;
  const kind = ctx.motion ? ctx.motion.entrance : 'fade_rise';
  const reveal = kind === 'baseline_reveal' && !!mask && !ctx.reducedMotion;
  const dy = ctx.reducedMotion || e >= 1 ? 0 : Math.round((1 - e) * (reveal && mask ? mask.offsetHeight || 0 : TEXT_RISE));
  p.style(el, 'opacity', fx(reveal ? (e > 0 ? 1 : 0) : e));
  p.style(el, 'transform', dy ? `translate(0, ${dy}px)` : 'none');
}

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/**
 * @typedef {object} LegendRow
 * @property {number} index
 * @property {string} symbol
 * @property {HTMLElement} row
 * @property {HTMLElement | null} mask
 * @property {HTMLElement} slot     the row's place in the legend: its own dim group (a look_here on its symbol keeps it)
 * @property {number | null} at     its cue (null: with the formula)
 * @property {number} enter         its entrance start
 * @property {string} text
 */

/**
 * @typedef {object} Brace
 * @property {number} index        the variable
 * @property {SVGElement} path
 * @property {number} at           its cue
 * @property {number} until        when it leaves: the next symbol's cue or the first look_here after it, the last one
 *                                 at most BRACE_HOLD after its cue
 * @property {boolean} drawn       it has geometry (the symbol is in the formula, with room for a brace)
 */

/**
 * When each cued symbol's brace leaves (B2c review V3; pure): at the next cue of another symbol (strictly later), or
 * at the first look_here after its own cue (the R-16 walk's outline is then the one pointer on screen), whichever comes
 * first; the last symbol's at most BRACE_HOLD after its cue (never a lone brace through the hold). `cued` in cue order.
 * @param {{at: number}[]} cued @param {readonly {kind?: string, t?: unknown}[]} events
 * @returns {number[]}
 */
export function braceEnds(cued, events) {
  const looks = (events || []).filter((e) => e && e.kind === 'look_here' && typeof e.t === 'number' && Number.isFinite(e.t))
    .map((e) => /** @type {number} */ (e.t));
  return cued.map((c, j) => {
    const next = cued.slice(j + 1).find((d) => d.at > c.at);
    const look = looks.filter((t) => t > c.at).reduce((a, b) => Math.min(a, b), Infinity);
    return Math.min(next ? next.at : c.at + BRACE_HOLD, look);
  });
}

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.ffFallback);

/** @type {TemplateModule} */
export default {
  id: ID,

  /**
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format, roles } = ctx;
    if (!fitsScene(scene)) {
      // a scene this template cannot draw whole (a look naming it on another scene): board_generic draws it
      const inner = boardGeneric.build(ctx);
      inner.ffFallback = true;
      return inner;
    }
    const item = /** @type {BoardItem} */ (formulaOf(scene));
    const heading = headingOf(scene);
    const L = LAYOUT[format];
    const fixes = fixesOf(ctx.look);
    const ink = roles.color('ink');
    const ink2 = roles.color('ink2');
    const surface = roles.color('surface');
    const boxBorder = roles.color('struct.border');
    const onBg = ctx.onBackground;

    // --- the headline (text on the background): a heading's rich-lite text (TeX, [[terms]]), else the scene title
    const titleText = heading ? String(heading.text || '') : String(scene.title || '');
    const title = h('h2', {
      class: 'dyn-title ff-title', dir: 'auto',
      style: { margin: '0', color: onBg.ink, fontWeight: String(L.title.weight) },
    });
    /** @type {HTMLElement[]} */
    let words;
    /** @type {boolean[]} */
    let wordTex;
    if (heading) ({ words, tex: wordTex } = richWords(ctx, title, titleText));
    else {
      words = plainWords(title, titleText, ctx.locale);
      wordTex = words.map(() => false);
    }
    const titleMask = maskFor(ctx, title);
    // R-53 remedy: a scrim of the scene's `scrim` role behind the headline when preflight asked (fixes.slots.title)
    const titleScrim = fixes.slots.title && fixes.slots.title.scrim ? roles.color('scrim') : null;
    const titleBox = h('div', { class: 'ff-titlebox', style: { position: 'absolute', boxSizing: 'border-box' } }, titleMask || title);
    if (titleScrim) Object.assign(titleBox.style, { background: titleScrim, borderRadius: '16px' });
    const titleGroup = group('ff-head', titleBox);

    // --- the formula card (the box)
    const ft = formulaTex(item);
    const wideTex = h('span', { class: 'ff-tex', style: { display: 'inline-block', lineHeight: '0' } });
    tex(ctx, wideTex, ft.tex, true);
    const brokenString = format === 'portrait' ? brokenTex(item) : null;
    const brokenEl = brokenString ? h('span', { class: 'ff-tex ff-tex--broken', style: { display: 'none', lineHeight: '0' } }) : null;
    if (brokenEl && brokenString) tex(ctx, brokenEl, brokenString, true);
    const formulaHost = h('div', { class: 'ff-formula', style: { textAlign: 'center', lineHeight: '0', whiteSpace: 'nowrap' } }, wideTex, brokenEl);
    // the braces' room in the card past the formula's box (layout sizes them: braceBands)
    const bandAbove = h('div', { class: 'ff-band ff-band--above', 'aria-hidden': 'true' });
    const band = h('div', { class: 'ff-band', 'aria-hidden': 'true' });
    const sentence = typeof item.text === 'string' && item.text.trim() ? item.text : '';
    const textEl = sentence ? h('div', {
      class: 'ff-text', dir: 'auto',
      style: { fontWeight: String(L.text.weight), textAlign: 'center', overflowWrap: 'anywhere', color: ink },
    }) : null;
    let textWords = /** @type {{words: HTMLElement[], tex: boolean[]}} */ ({ words: [], tex: [] });
    if (textEl) textWords = richWords(ctx, textEl, sentence);
    const textMask = textEl ? maskFor(ctx, textEl) : null;
    const boxPathEl = svg('path', { class: 'ff-box-path', fill: 'none', 'stroke-width': String(BOX_PX), pathLength: '1' });
    boxPathEl.setAttribute('stroke', boxBorder);
    const boxSvg = svg('svg', { class: 'ff-box', 'aria-hidden': 'true', focusable: 'false' }, boxPathEl);
    Object.assign(/** @type {HTMLElement} */ (/** @type {unknown} */ (boxSvg)).style, { position: 'absolute', left: '0', top: '0', overflow: 'visible', pointerEvents: 'none' });
    const card = h('div', {
      class: 'ff-card',
      style: {
        position: 'absolute', boxSizing: 'border-box', borderRadius: `${RADIUS.card}px`, background: surface, color: ink,
        boxShadow: String(LAYOUT_TOKENS.shadow['2']),
      },
    }, boxSvg, bandAbove, formulaHost, band, textMask || textEl);

    // --- the braces (one per variable that has a cue; drawn in stage px at their symbols, bracePlace)
    // entrances (state.js itemTimings: a reveal event, else the beat that reveals the item, R-17's lead, else with
    // the scene): the formula's, and the headline's (a heading's own, else the scene start)
    const timings = itemTimings(scene);
    const enter = (timings.find((x) => x.id === item.id) || { enter: 0 }).enter;
    const titleStart = heading ? (timings.find((x) => x.id === heading.id) || { enter: 0 }).enter : 0;
    const times = variableTimes(scene, item, enter);
    const cued = times.map((x, k) => ({ k, at: x.at })).filter((x) => x.at !== null)
      .sort((a, b) => /** @type {number} */ (a.at) - /** @type {number} */ (b.at) || a.k - b.k);
    // the look_here cues cues.js draws on this scene (the walk): their starts end a brace
    const ends = braceEnds(cued.map((c) => ({ at: /** @type {number} */ (c.at) })),
      cueEvents(scene, ID).map((e) => ({ kind: e.kind, t: e.start })));
    const bracesSvg = svg('svg', { class: 'ff-braces', 'aria-hidden': 'true', focusable: 'false' });
    bracesSvg.setAttribute('viewBox', `0 0 ${ctx.zones.width} ${ctx.zones.height}`);
    bracesSvg.setAttribute('width', String(ctx.zones.width));
    bracesSvg.setAttribute('height', String(ctx.zones.height));
    Object.assign(/** @type {HTMLElement} */ (/** @type {unknown} */ (bracesSvg)).style, { position: 'absolute', left: '0', top: '0', overflow: 'visible', pointerEvents: 'none' });
    /** @type {Brace[]} */
    const braces = cued.map((c, j) => {
      const at = /** @type {number} */ (c.at);
      const sym = symbolsOf(item)[c.k];
      const path = svg('path', {
        class: 'ff-brace', fill: 'none', 'stroke-width': String(BRACE_PX), 'stroke-linecap': 'round',
        'stroke-linejoin': 'round', pathLength: '1', 'data-var': String(c.k),
      });
      // a drawing in the symbol's entity colour (the colour the symbol takes), else the quieter ink
      path.setAttribute('stroke', entityColour(ctx, sym) || ink2);
      bracesSvg.appendChild(path);
      return { index: c.k, path, at, until: ends[j], drawn: false };
    });
    const cardGroup = group('ff-main', card, bracesSvg);

    // --- the legend (text on the background): one row per variable, each in a slot that is a dim group of its own
    // (a look_here on a symbol keeps that symbol's row bright, FocusRect.keep; the row's text entrance is painted on
    // the row, never on the slot)
    const legendInk = onBg.ink;
    /** @type {LegendRow[]} */
    const rows = (item.variables || []).map((v, k) => {
      const symbol = String(v.symbol_latex || '');
      // the symbol in its entity colour (on a colour block: the block's ink; entity colours are validated on the
      // family's backgrounds and surfaces, R-53, not on an accent block)
      const symColour = onBg.block ? onBg.ink : entityColour(ctx, symbol) || onBg.ink;
      const sym = h('span', { class: 'ff-sym', style: { color: symColour, flex: 'none' } });
      tex(ctx, sym, symbol, false);
      const meaning = String(v.meaning || '');
      const unit = String(v.unit || '');
      const row = h('div', {
        class: 'ff-row', dir: 'auto',
        style: { display: 'flex', alignItems: 'baseline', columnGap: '0.45em', overflowWrap: 'anywhere', color: legendInk },
      }, sym, h('span', { class: 'ff-meaning' }, meaning,
        // the unit runs on after the meaning (never split inside), not pushed to the row's far end
        unit ? ' ' : null, unit ? h('span', { class: 'ff-unit', text: `(${unit})`, style: { whiteSpace: 'nowrap' } }) : null));
      const mask = maskFor(ctx, row);
      const slot = group('ff-slot', mask || row);
      const at = times[k].at;
      const enterRow = at !== null ? at : enter + seconds(ctx, TEXT_DELAY) + seconds(ctx, ROW_STAGGER) * k;
      return {
        index: k, symbol, row, mask, slot, at, enter: enterRow,
        text: `${symbol} ${meaning}${unit ? ` (${unit})` : ''}`,
      };
    });
    const legendScrim = fixes.slots.legend && fixes.slots.legend.scrim ? roles.color('scrim') : null;
    const legend = rows.length ? h('div', {
      class: 'ff-legend',
      style: { position: 'absolute', boxSizing: 'border-box', display: 'grid', fontWeight: String(L.legend.weight) },
    }, rows.map((r) => r.slot)) : null;
    if (legend && legendScrim) Object.assign(legend.style, { background: legendScrim, borderRadius: '16px' });

    const root = h('div', {
      class: ['ff', `ff--${format}`],
      style: { position: 'absolute', left: '0', top: '0', width: `${ctx.zones.width}px`, height: `${ctx.zones.height}px` },
    }, titleGroup, cardGroup, legend);
    ctx.root.appendChild(root);
    return {
      root, item, heading, titleText, title, titleMask, titleBox, titleScrim, words, wordTex, titleGroup,
      card, boxSvg, boxPathEl, formulaHost, wideTex, brokenEl, bandAbove, band, textEl, textMask, textWords, cardGroup,
      bracesSvg, braces, legend, rows, legendScrim, tints: ft.tints,
      /** @type {HTMLElement} the formula form on screen (layout may pick the broken one in 9:16) */
      texEl: wideTex,
      /** @type {Map<string, Element[]>} the placeholder groups of each symbol in the shown form */
      targets: new Map(), enter, titleStart, boxAt: boxDrawAt(scene, item, enter), times,
      groups: [titleGroup, cardGroup, ...rows.map((r) => r.slot)],
      ink, ink2, surface, legendInk,
      // what layout measured (texts() reports it)
      titleSize: L.title.max, titleMin: L.title.min, titleOverflow: 0, formulaSize: L.formula.max, formulaMin: L.formula.min,
      formulaOverflow: 0, textSize: L.text.max, textMin: L.text.min, textOverflow: 0, legendSize: L.legend.max,
      legendMin: L.legend.min, legendOverflow: 0, narrow: false, broken: false, laidOut: false, textShown: !!textEl,
      /** the room kept in the card above and below the formula for its braces (layout) */
      bands: { above: 0, below: 0 },
      // what update painted (texts() reports it)
      titleAlpha: 0, cardAlpha: 0, textAlpha: 0, rowAlpha: rows.map(() => 0),
    };
  },

  /**
   * Measure and fit (idempotent). The sizes start at their slots' largest (preflight's caps and the R-29 floors
   * applied, `slotRange`): the headline takes the largest size whose lines fit (`fitLines`), the formula the largest
   * whose width fits the card (9:16: broken at "=" when even its smallest is wider, §4.3), the sentence the largest
   * whose lines fit. While the card and the legend do not fit under the headline, the legend, the sentence, the
   * headline and last the formula (the scene's focus) shrink in that order, 4 px at a time, each to its minimum.
   * When even that does not fit, the formula's sentence (`item.text`, which §4.3 does not list among the template's
   * data) is left out and the sizes fitted again from their largest, so the legend (the symbols' meanings, the
   * template's own) keeps its room (R-25; the narration says the sentence). What still does not fit is reported as
   * overflow (preflight shrinks the slot or blocks with "Shorten text", §7.12); no legend row is ever dropped or faded
   * out to make room (RV L-13, B2c review V2). 9:16 lays the card out wide (768 px) unless the formula would then reach
   * below the rail outside the column there (R-59), else narrow. The card keeps bands above and below the formula for
   * the braces that reach past it, measured at every size tried (`braceBands`). Then the card, the box and the braces
   * are placed.
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    if (isFallback(ctx)) {
      if (typeof boardGeneric.layout === 'function') boardGeneric.layout(ctx);
      return;
    }
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
    const C = L.card;
    const g = geometry(ctx.zones);
    const fixes = fixesOf(ctx.look);
    const rTitle = slotRange(ctx, 'title', { min: L.title.min, max: L.title.max, role: 'title' });
    const rFormula = slotRange(ctx, 'formula', { min: L.formula.min, max: L.formula.max, role: FORMULA_SLOT.role });
    const rText = slotRange(ctx, 'text', { min: L.text.min, max: L.text.max, role: 'body' });
    const rLegend = slotRange(ctx, 'legend', { min: L.legend.min, max: L.legend.max, role: 'label' });
    /** @type {LegendRow[]} */
    const rows = v.rows;
    const n = rows.length;

    // --- static parts: line heights (§4.6: Indic text at the type scale's Indic floors), the headline's box (a scrim
    // reaches 24 px past it), MathJax's display container without its own 1em margins and line strut, the legend grid
    const titleLh = isIndic(v.titleText) ? LINE_HEIGHT.display.indic : LINE_HEIGHT.display.latin;
    const textLh = isIndic(v.textEl ? v.textEl.textContent : '') ? LINE_HEIGHT.body.indic : LINE_HEIGHT.body.latin;
    const rowLh = rows.some((r) => isIndic(r.text)) ? LINE_HEIGHT.body.indic : LINE_HEIGHT.body.latin;
    v.title.style.lineHeight = String(titleLh);
    const scrimPad = v.titleScrim ? 24 : 0;
    Object.assign(v.titleBox.style, {
      left: `${g.title.x0 - scrimPad}px`, top: `${g.title.y0 - (scrimPad ? 8 : 0)}px`,
      width: `${g.title.x1 - g.title.x0 + 2 * scrimPad}px`, padding: scrimPad ? `8px ${scrimPad}px` : '0',
    });
    for (const host of [v.wideTex, v.brokenEl]) {
      const box = host ? /** @type {HTMLElement | null} */ (host.querySelector('mjx-container')) : null;
      if (box) Object.assign(box.style, { margin: '0', lineHeight: '0', display: 'block' });
    }
    if (v.textEl) Object.assign(v.textEl.style, { lineHeight: String(textLh), textAlign: g.legend ? 'left' : 'center' });
    const twoColumns = n >= L.twoColumns;
    const braceH = (/** @type {number} */ f) => Math.max(BRACE_MIN, Math.min(BRACE_MAX, Math.round(BRACE_SHARE * f)));
    /** @param {HTMLElement} el @param {boolean} on */
    const show = (el, on) => {
      el.style.display = on ? 'inline-block' : 'none';
    };
    // the legend: legend order, one column (two in 16:9 from L.twoColumns rows on)
    if (v.legend) {
      Object.assign(v.legend.style, {
        lineHeight: String(rowLh), rowGap: `${L.rowGap}px`, columnGap: twoColumns ? `${L.columnGap}px` : '0',
        gridAutoFlow: twoColumns ? 'column' : 'row', gridTemplateColumns: twoColumns ? '1fr 1fr' : '1fr',
        gridTemplateRows: twoColumns ? `repeat(${Math.ceil(n / 2)}, auto)` : '',
      });
    }
    /**
     * Where each brace goes in the formula form `texEl` as laid out now (`bracePlace`; null: no brace), at TeX size
     * `fs`: each symbol's first placeholder group (`tintTargets`, idempotent) against the form's other glyphs.
     * @param {HTMLElement} texEl @param {number} fs
     * @returns {(BracePlace | null)[]}
     */
    const placeBraces = (texEl, fs) => {
      const targets = tintTargets(texEl, v.tints);
      return /** @type {Brace[]} */ (v.braces).map((b) => {
        const els = targets.get(symbolsOf(v.item)[b.index]) || [];
        const r = els.length ? ctx.rectOf(els[0]) : null;
        if (!r || !(r.w > 0 && r.h > 0)) return null;
        return bracePlace(r, glyphRects(ctx, texEl, [els[0]]), braceH(fs));
      });
    };
    /**
     * Size the card's brace bands for the form `texEl` at `fs` (the room its braces reach past the formula's box).
     * Above, the card's own top padding takes the part of an over-brace's reach that keeps BRACE_GAP clear of the box's
     * stroke (BOX_PX), so an over-brace costs the card only what the padding cannot give; below, the sentence follows,
     * so the band is the whole reach.
     * @param {HTMLElement} texEl @param {number} fs
     */
    const sizeBands = (texEl, fs) => {
      const f = v.braces.length ? ctx.rectOf(texEl) : null;
      const reach = f ? braceBands(f, placeBraces(texEl, fs), braceH(fs)) : { above: 0, below: 0 };
      const bands = { above: reach.above > 0 ? Math.max(0, reach.above + BOX_PX - C.padY) : 0, below: reach.below };
      v.bandAbove.style.height = `${bands.above}px`;
      v.band.style.height = `${bands.below}px`;
      v.bands = bands;
      return bands;
    };

    /**
     * The headline at most `cap` px: its fit, and where the content under it starts.
     * @param {number} cap
     */
    const fitTitle = (cap) => {
      const fit = fitLines(v.title, { max: Math.max(rTitle.min, cap), min: rTitle.min, lines: L.title.lines, lh: titleLh, prefer: L.title.prefer || undefined }, v.words, v.wordTex);
      padMask(v.titleMask, fit.size);
      return { fit, top: g.title.y0 + v.title.offsetHeight + L.titleGap };
    };

    /**
     * The formula at most `cap` px whose width fits `maxInner`: its form (9:16: the broken one when the whole one is
     * wider even at its smallest and the broken one overflows less) and size; `over`: the width still past `maxInner`.
     * @param {number} maxInner @param {number} cap
     */
    const fitFormula = (maxInner, cap) => {
      /** @param {HTMLElement} el */
      const widest = (el) => {
        let s = Math.max(rFormula.min, cap);
        for (;;) {
          v.formulaHost.style.fontSize = `${s}px`;
          const w = el.offsetWidth;
          if (w <= maxInner || s <= rFormula.min) return { size: s, over: w > maxInner + 2 ? w - maxInner : 0 };
          s = Math.max(rFormula.min, s - 4);
        }
      };
      show(v.wideTex, true);
      if (v.brokenEl) show(v.brokenEl, false);
      const whole = widest(v.wideTex);
      if (whole.over > 0 && v.brokenEl) {
        show(v.wideTex, false);
        show(v.brokenEl, true);
        const broken = widest(v.brokenEl);
        if (broken.over < whole.over) return { el: v.brokenEl, ...broken };
        show(v.brokenEl, false);
        show(v.wideTex, true);
      }
      v.formulaHost.style.fontSize = `${whole.size}px`;
      return { el: v.wideTex, ...whole };
    };

    /**
     * The card (its x span `span`: 9:16 fixed; null: 16:9, sized to its content) and the legend at the formula size
     * `fs`, the sentence (when `withText`) at most `xs` and the legend rows at `ls`: sizes and heights.
     * @param {{x0: number, x1: number} | null} span @param {HTMLElement} texEl @param {number} fs
     * @param {boolean} withText @param {number} xs @param {number} ls
     */
    const measure = (span, texEl, fs, withText, xs, ls) => {
      const maxInner = (span ? span.x1 - span.x0 : g.maxWidth) - 2 * C.padX;
      v.formulaHost.style.fontSize = `${fs}px`;
      const bands = sizeBands(texEl, fs);
      const text = withText ? v.textEl : null;
      if (v.textEl) /** @type {HTMLElement} */ (v.textMask || v.textEl).style.display = text ? '' : 'none';
      // the card's inner width: 9:16 fixed; 16:9 the widest of the formula and the sentence's own line, at least
      // minInner, at most the critical width (+ 1: offsetWidth rounds, and a box 0.4 px narrower than its one line
      // would wrap it)
      let inner = maxInner;
      if (!span) {
        let natural = texEl.offsetWidth + 1;
        if (text) {
          Object.assign(text.style, { fontSize: `${xs}px`, width: 'max-content', maxWidth: `${maxInner}px` });
          natural = Math.max(natural, text.offsetWidth + 1);
          text.style.maxWidth = '';
        }
        inner = Math.max(Math.min(C.minInner, maxInner), Math.min(maxInner, Math.ceil(natural)));
      }
      Object.assign(v.card.style, { width: `${inner + 2 * C.padX}px`, padding: `${C.padY}px ${C.padX}px` });
      let textFit = { size: xs, fits: true, overflow: 0 };
      if (text) {
        // 9:16: the text column (x 192-780, R-59 below the rail too), from the card's content edge
        const w = g.legend ? g.legend.x1 - g.legend.x0 : inner;
        // TEXT_GAP under the formula, or BRACE_GAP under a brace reaching past it (the band's own last gap)
        Object.assign(text.style, { width: `${w}px`, marginTop: `${Math.max(0, TEXT_GAP - bands.below)}px` });
        textFit = fitLines(text, { max: xs, min: rText.min, lines: L.text.lines, lh: textLh }, v.textWords.words, v.textWords.tex);
        padMask(v.textMask, textFit.size);
      }
      let legendW = 0;
      let legendH = 0;
      if (v.legend) {
        v.legend.style.fontSize = `${ls}px`;
        for (const r of rows) padMask(r.mask, ls);
        if (g.legend) legendW = g.legend.x1 - g.legend.x0;
        else if (twoColumns) legendW = g.maxWidth;
        else {
          Object.assign(v.legend.style, { width: 'max-content', maxWidth: `${g.maxWidth}px` });
          legendW = Math.min(g.maxWidth, Math.max(inner + 2 * C.padX, v.legend.offsetWidth + 1));
          v.legend.style.maxWidth = '';
        }
        v.legend.style.width = `${legendW}px`;
        legendH = v.legend.offsetHeight;
      }
      const cardH = v.card.offsetHeight;
      return {
        inner, cardH, legendW, legendH, total: cardH + (v.legend ? L.legendGap + legendH : 0), textFit, text: !!text,
        texW: texEl.offsetWidth, texH: texEl.offsetHeight, above: bands.above,
      };
    };

    /**
     * Fit everything with the card's x span `span` (see the method comment), with or without the sentence, the
     * formula at most `formulaCap` px.
     * @param {{x0: number, x1: number} | null} span @param {boolean} withText @param {number} [formulaCap]
     */
    const solve = (span, withText, formulaCap = rFormula.max) => {
      const maxInner = (span ? span.x1 - span.x0 : g.maxWidth) - 2 * C.padX;
      let caps = { title: rTitle.max, formula: Math.max(rFormula.min, Math.min(rFormula.max, formulaCap)), text: rText.max, legend: rLegend.max };
      for (let guard = 0; ; guard++) {
        const title = fitTitle(caps.title);
        const formula = fitFormula(maxInner, caps.formula);
        const m = measure(span, formula.el, formula.size, withText, caps.text, caps.legend);
        const avail = g.bottom - title.top;
        const spill = Math.max(0, m.total - avail);
        const out = { span, title, formula, m, legend: caps.legend, spill, avail, top: title.top };
        if (spill === 0 || guard > 200) return out;
        // shrink the next part that can, from the least to the most important (the formula is the scene's focus)
        const step = (/** @type {number} */ now, /** @type {number} */ min) => Math.max(min, now - 4);
        if (v.legend && caps.legend > rLegend.min) caps = { ...caps, legend: step(caps.legend, rLegend.min) };
        else if (m.text && m.textFit.size > rText.min) caps = { ...caps, text: step(m.textFit.size, rText.min) };
        else if (title.fit.size > rTitle.min) caps = { ...caps, title: step(title.fit.size, rTitle.min) };
        else if (formula.size > rFormula.min) caps = { ...caps, formula: step(formula.size, rFormula.min) };
        else return out; // everything at its minimum: what is left is overflow
      }
    };

    // 9:16: the wide card (§4.3's 768 px) unless the formula would reach below the rail outside the column there
    // (R-59); first the formula smaller in the wide card, 4 px at a time down to its minimum, until it ends above the
    // rail; only when even its minimum does not, the narrow card. The safe layout is the narrow card. A sentence
    // leaving no room for two legend rows is left out (see above).
    const attempt = (/** @type {boolean} */ withText) => {
      if (!g.wide || !g.narrow || !g.below) return solve(null, withText);
      if (fixes.safe) return solve(g.narrow, withText);
      /** Whether the wide card's formula crosses the rail outside the column there. @param {any} a */
      const crosses = (a) => {
        // the formula's bottom in the wide card: under the card's padding and its brace band above
        const bottom = a.top + C.padY + a.m.above + a.m.texH;
        const x1 = (g.wide.x0 + g.wide.x1) / 2 + a.m.texW / 2;
        return bottom > g.rail && x1 > /** @type {{x1: number}} */ (g.below).x1;
      };
      let wide = solve(g.wide, withText);
      while (crosses(wide) && wide.formula.size > rFormula.min) wide = solve(g.wide, withText, wide.formula.size - 4);
      return crosses(wide) ? solve(g.narrow, withText) : wide;
    };
    let a = attempt(true);
    if (v.textEl && a.spill > 0) a = attempt(false);
    v.textShown = !!v.textEl && a.m.text;
    v.narrow = !!a.span && a.span === g.narrow;
    v.texEl = a.formula.el;
    v.broken = a.formula.el === v.brokenEl;
    v.titleSize = a.title.fit.size;
    v.titleMin = rTitle.min;
    v.titleOverflow = a.title.fit.overflow;
    v.formulaSize = a.formula.size;
    v.formulaMin = rFormula.min;
    v.textSize = a.m.textFit.size;
    v.textMin = rText.min;
    v.textOverflow = v.textShown ? a.m.textFit.overflow : 0;
    v.legendSize = a.legend;
    v.legendMin = rLegend.min;
    // what still does not fit: past the bottom (the legend's when it has one: it is what runs out), or sideways
    v.legendOverflow = v.legend ? a.spill : 0;
    v.formulaOverflow = Math.max(a.formula.over, v.legend ? 0 : a.spill);

    // --- place: 16:9 the card and the legend as one block centred in the room under the headline; 9:16 under it
    const cardW = a.m.inner + 2 * C.padX;
    const cardH = a.m.cardH;
    const cardX = a.span ? a.span.x0 : Math.round(g.centre - cardW / 2);
    const y = a.span ? a.top : a.top + Math.max(0, Math.round((a.avail - a.m.total) / 2));
    Object.assign(v.card.style, { left: `${cardX}px`, top: `${y}px` });
    if (v.legend) {
      const legendX = g.legend ? g.legend.x0 : Math.round(g.centre - a.m.legendW / 2);
      const pad = v.legendScrim ? 16 : 0;
      Object.assign(v.legend.style, {
        left: `${legendX - pad}px`, top: `${y + cardH + L.legendGap - pad}px`, padding: pad ? `${pad}px` : '0',
        width: `${a.m.legendW + 2 * pad}px`,
      });
    }
    // the box round the card
    v.boxSvg.setAttribute('width', String(cardW));
    v.boxSvg.setAttribute('height', String(cardH));
    v.boxSvg.setAttribute('viewBox', `0 0 ${cardW} ${cardH}`);
    v.boxPathEl.setAttribute('d', boxPath(cardW, cardH, RADIUS.card, BOX_PX));

    // --- tints and braces: the placeholder groups MathJax drew (tintTargets tags them, so a second layout finds
    // them after they were recoloured); each brace at its symbol's first group, under or over it (bracePlace), in
    // the room the card's bands keep (sized for this form and size in the last measure)
    v.targets = tintTargets(v.texEl, v.tints);
    const bh = braceH(a.formula.size);
    const places = placeBraces(v.texEl, a.formula.size);
    /** @type {Brace[]} */ (v.braces).forEach((b, i) => {
      const at = places[i];
      b.drawn = !!at;
      if (at) b.path.setAttribute('d', bracePath(at.x0, at.x1, at.y, bh, at.dir));
      else b.path.removeAttribute('d');
    });
    v.laidOut = true;
  },

  /**
   * Paint scene time t.
   * @param {TemplateContext} ctx
   * @param {number} t
   * @param {SceneState} state
   */
  update(ctx, t, state) {
    if (isFallback(ctx)) {
      boardGeneric.update(ctx, t, state);
      return;
    }
    const v = ctx.view;
    const p = ctx.paint;
    // the headline: the personality's text entrance
    v.titleAlpha = enterAt(ctx, t, v.titleStart + seconds(ctx, TITLE_DELAY), TITLE_SECONDS);
    paintEntrance(ctx, v.title, v.titleMask, v.titleAlpha);

    // the card: fades in on the formula's entrance (§4.3: 400 ms); its sentence follows
    v.cardAlpha = enterAt(ctx, t, v.enter, FORMULA_FADE);
    p.style(v.card, 'opacity', fx(v.cardAlpha));
    p.style(v.card, 'visibility', v.cardAlpha > 0 ? 'visible' : 'hidden');
    if (v.textEl) {
      const e = enterAt(ctx, t, v.enter + seconds(ctx, TEXT_DELAY), TEXT_SECONDS);
      v.textAlpha = e * v.cardAlpha; // inside the card: both opacities apply
      paintEntrance(ctx, v.textEl, v.textMask, e);
    }

    // the box: drawn round the card on its draw event, else there with the card
    const boxK = v.boxAt === null ? (v.cardAlpha > 0 ? 1 : 0) : ease('ease.move', progress(t, v.boxAt, seconds(ctx, BOX_DRAW)));
    p.attr(v.boxPathEl, 'stroke-dasharray', '1 1');
    p.attr(v.boxPathEl, 'stroke-dashoffset', fx(1 - boxK, 4));
    p.style(v.boxPathEl, 'visibility', boxK > 0 ? 'visible' : 'hidden');

    // the tints (§4.7): each symbol from ink to its entity colour on its cue (with the formula without one)
    const symbols = symbolsOf(v.item);
    for (const tint of v.tints) {
      const els = v.targets.get(tint.symbol) || [];
      if (!els.length) continue;
      const colour = entityColour(ctx, tint.symbol);
      const k = symbols.indexOf(tint.symbol);
      const at = k >= 0 && v.times[k].at !== null ? v.times[k].at : v.enter;
      const e = colour ? enterAt(ctx, t, at, TINT_SECONDS) : 0;
      paintTint(ctx, els, !colour || e <= 0 ? null : e >= 1 ? colour : mixOklch(v.ink, colour, e));
    }

    // the braces: each draws on its cue and leaves when the next symbol's cue comes (§4.3)
    for (const b of /** @type {Brace[]} */ (v.braces)) {
      const on = b.drawn ? ease('ease.move', progress(t, b.at, seconds(ctx, BRACE_DRAW))) : 0;
      const off = Number.isFinite(b.until) ? ease('ease.move', progress(t, b.until, seconds(ctx, BRACE_EXIT))) : 0;
      const shown = on > 0 && off < 1;
      p.attr(b.path, 'stroke-dasharray', '1 1');
      p.attr(b.path, 'stroke-dashoffset', fx(1 - on, 4));
      p.style(b.path, 'opacity', shown ? fx(1 - off) : '0');
      p.style(b.path, 'visibility', shown ? 'visible' : 'hidden');
    }

    // the legend rows: each on its cue (the personality's text entrance), on the row itself (its slot is a dim group,
    // whose opacity is the stage's); a row that entered stays (RV L-13)
    for (const r of /** @type {LegendRow[]} */ (v.rows)) {
      const e = enterAt(ctx, t, r.enter, ROW_SECONDS);
      v.rowAlpha[r.index] = e;
      paintEntrance(ctx, r.row, r.mask, e);
    }
  },

  /**
   * Where cue.look_here points (plan §1.4, §1.7; the R-16 walk over the symbols): `<item>:var:<symbol>` the symbol's
   * first place in the formula (its placeholder group as drawn; the legend row when the symbol is not in the formula),
   * `<item>` the card, the heading's id the headline. Whole stage px; null for anything else or before layout.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    const v = ctx.view;
    const parsed = parseTarget(target);
    if (!parsed) return null;
    /**
     * @param {Element | null} el @param {Element} holder @param {number} radius
     * @param {'text' | 'shape'} kind @param {Element[]} [keep]
     * @returns {FocusRect | null}
     */
    const rectFor = (el, holder, radius, kind, keep = []) => {
      const r = el ? ctx.rectOf(el) : null;
      if (!r || !(r.w > 0 && r.h > 0)) return null;
      const x0 = Math.round(r.x);
      const y0 = Math.round(r.y);
      return { rect: { x: x0, y: y0, w: Math.round(r.x + r.w) - x0, h: Math.round(r.y + r.h) - y0 }, shape: 'rect', radius, el: holder, kind, keep };
    };
    // the card and a symbol drawn in it are shapes (the card spans x 156-924 in 9:16, past the text column, and the
    // formula's glyphs reach its padding): their outline goes round them, never across (cues.js shapeArea, CG-1)
    if (parsed.form === 'item' && parsed.item === v.item.id) return rectFor(v.card, v.card, RADIUS.card, 'shape');
    if (parsed.form === 'item' && v.heading && parsed.item === v.heading.id) return rectFor(v.title, v.title, 0, 'text');
    if (parsed.form === 'item:var' && parsed.item === v.item.id) {
      const k = variableIndex(v.item, String(parsed.symbol));
      if (k < 0) return null;
      const row = v.rows[k];
      const els = v.targets.get(symbolsOf(v.item)[k]) || [];
      // the symbol's own legend row stays bright while the walk points at it (its meaning, R-16)
      const inFormula = els.length ? rectFor(els[0], v.card, SYMBOL_RADIUS, 'shape', row ? [row.slot] : []) : null;
      if (inFormula) return inFormula;
      return row ? rectFor(row.row, row.row, SYMBOL_RADIUS, 'text') : null;
    }
    return null;
  },

  /**
   * The element holding an item's text, where a cue.term would find its [[term]] spans (plan §1.5): the formula's
   * sentence for the formula item, the headline for the heading. (The spec consumes no cue_term today, so cues.js
   * never calls this; kept so the contract is whole.)
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? /** @type {Element | null} */ (boardGeneric.itemEl(ctx, itemId)) : null;
    const v = ctx.view;
    if (itemId === v.item.id) return v.textEl;
    if (v.heading && itemId === v.heading.id) return v.title;
    return null;
  },

  /**
   * The content groups a look_here dims (all but the target's and the ones its FocusRect keeps): the headline, the
   * card (with its braces) and each legend row. The same elements at every t; this template writes the opacity of what
   * is inside them, never theirs.
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    return ctx.view.groups;
  },

  /**
   * The shared-element morph's anchor (§4.9: formula -> worked_steps; B2f): the formula as drawn, or null before
   * layout.
   * @param {TemplateContext} ctx
   */
  anchor(ctx) {
    if (isFallback(ctx)) return null;
    const v = ctx.view;
    const r = v.laidOut ? ctx.rectOf(v.texEl) : null;
    if (!r || !(r.w > 0 && r.h > 0)) return null;
    const x = Math.round(r.x);
    const y = Math.round(r.y);
    return { el: v.texEl, rect: { x, y, w: Math.round(r.x + r.w) - x, h: Math.round(r.y + r.h) - y } };
  },

  /**
   * Static: the box a medium of `slot` is sized for. This template shows none; a scene it hands to board_generic gets
   * board_generic's box, so `mediaPlan` and `ctx.media` agree.
   * @param {TimedScene} scene
   * @param {import('../core.js').Zones} zones
   * @param {'live' | 'render'} mode
   * @param {string | undefined} slot
   * @param {import('../core.js').Fixes} fixes
   */
  mediaBox(scene, zones, mode, slot, fixes) {
    if (!fitsScene(scene) && typeof boardGeneric.mediaBox === 'function') return boardGeneric.mediaBox(scene, zones, mode, slot, fixes);
    return null;
  },

  /**
   * Static: every TeX string this template converts for the scene (`texOf`; board_generic's for a scene it hands on).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} fmt
   */
  tex(scene, look, fmt) {
    if (!fitsScene(scene)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene, fmt);
  },

  /**
   * The text on screen (preflight, §7.12): the headline (slot `title`), the formula (slot `formula`: its box, its
   * TeX size, any width it runs past the card at its minimum), its sentence (slot `text`) and each legend row (slot
   * `legend`), with the colours they are drawn in and on (a scrim over the background's worst case when preflight
   * asked for one). Every one is critical (R-59, R-53).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
    const onBg = ctx.onBackground;
    /** @param {string | null} scrim @param {string} ink */
    const behind = (scrim, ink) => (scrim ? scrimmed(scrim, onBg, ink) : onBg.bg);
    /** @type {TextEntry[]} */
    const out = [];
    if (v.titleText.trim()) {
      out.push({ el: v.title, role: 'title', slot: 'title', critical: true, ink: onBg.ink, bg: behind(v.titleScrim, onBg.ink),
        alpha: v.titleAlpha, overflow: v.titleOverflow, size: v.titleSize, min: v.titleMin, weight: L.title.weight,
        text: (v.title.textContent || '').replace(/\s+/g, ' ').trim() });
    }
    out.push({ el: v.texEl, role: FORMULA_SLOT.role, slot: 'formula', critical: true, ink: v.ink, bg: v.surface,
      alpha: v.cardAlpha, overflow: v.formulaOverflow, size: v.formulaSize, min: v.formulaMin, weight: FORMULA_SLOT.weight,
      text: String(v.item.latex || '') });
    if (v.textEl && v.textShown) {
      out.push({ el: v.textEl, role: 'body', slot: 'text', critical: true, ink: v.ink, bg: v.surface, alpha: v.textAlpha,
        overflow: v.textOverflow, size: v.textSize, min: v.textMin, weight: L.text.weight,
        text: (v.textEl.textContent || '').replace(/\s+/g, ' ').trim() });
    }
    v.rows.forEach((/** @type {LegendRow} */ r, i) => {
      out.push({ el: r.row, role: 'label', slot: 'legend', critical: true, ink: v.legendInk, bg: behind(v.legendScrim, v.legendInk),
        alpha: v.rowAlpha[i], overflow: v.legendOverflow, size: v.legendSize, min: v.legendMin, weight: L.legend.weight, text: r.text });
    });
    return out;
  },
};
