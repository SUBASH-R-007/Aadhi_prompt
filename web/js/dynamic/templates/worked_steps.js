// @ts-check
/**
 * `worked_steps` (DYNAMIC_VIDEO_DESIGN §4.3 B, R1, class `procedure`, dense; B2 plan §A step 4, the pattern of
 * `docs/plans/b2/b2c_design_notes.md`): a worked example built one step at a time, with the formula it works from
 * beside it.
 *
 * Data (§4.3, templates.py `_fit_worked_steps`): one or more `example_step` items (`text` the step, rich-lite with
 * `$TeX$`; `justification` why it holds; `blank` a step withheld until its fill beat), an optional top line (a
 * `heading`, or - what the generator writes for the example's setup - a `callout_info` / `paragraph`) and an optional
 * `formula` item, and the scene's **side panel** (`_shared.js panelOf`: the example's figure, picture, animation,
 * terminal session, or the authored title of a chart, plot or model the Dynamic engine draws nowhere yet) - in 16:9
 * beside the steps, only without a formula; in 9:16 as its own short full-stage beat BEFORE the steps (owner decision
 * 4, 2026-10-16: `_shared.js reelWindow`), with or without a formula, so the step stack keeps the whole column and
 * R-29's floor. Nothing a timeline names is silently dropped: a scene with anything else on its board (a second top
 * line, a quiz, a medium, a poster), with a panel of a kind `_shared.js` does not draw, or with a 16:9 panel AND a
 * formula (one visual box, which the formula's card takes) is drawn by **board_generic** whole, every hook handed
 * on (`drawsScene`, formula_focus's and list_build's convention), and `_fit_worked_steps` scores such a scene
 * 0, so direct.py never sends it here (B2c review CG-2: a fit that takes a scene the page refuses makes the page
 * silently draw board_generic).
 *
 * 16:9 (§4.3): the steps in the left 55% (`split`) with the formula's card - or the scene's panel - in the right 45%;
 * with neither, the steps are set in the centre 60% (`centred`), so the right side is never left empty (list_build's
 * V12 rule). The top line spans its variant's width above both.
 *
 * 9:16 with a side panel: the panel alone on the stage from the scene's start until the first narrated step (2-4 s;
 * everything that would have entered meanwhile - the top line, the formula's card - enters as it leaves), then
 * the layout below, unchanged.
 *
 * 9:16 (§4.3, [RV L-13]): the top line, then the formula's card across the text column, then a **persistent compact
 * stack** of the steps: the current step is large (56-64 px) and every step before it is a compact 40 px row (R-29's
 * 9:16 text floor), its justification hidden. Each step's top is fixed (the compact rows above it), so nothing
 * scrolls: only the step that stops being current changes size, at the instant the next one enters, inside a short
 * fade (`DIP`), so no frame shows text re-flowing. A scene whose steps no beat reveals is a plain list: one size for
 * every step, compact when the large form does not fit.
 *
 * Item states (plan §1.5, state.js `ITEM_STATES` / `itemStateStyle`): the step entered last is `current` (weight 700,
 * a 2 px `ink2` inset border), every earlier one `past` (`ink2`, no border), and a `blank` step whose answer has
 * finished typing while it is still the last one is `done` (§4.3 "the final answer gets an `ok` outline and check
 * icon": an `ok` border and the `ok` check of `_shared.js itemStateParts`). `past` wins over `done`, so only the final
 * answer keeps its check. The states are painted here, from `itemStateStyle` (list_build's reasoning): every property
 * is written on every frame with a value, never `''`, so an element's style attribute never depends on the order
 * frames were painted in, and the ink and the border colour cross-fade over `STATE_EASE` (`mixOklch`), which
 * `_shared.js paintItemState` cannot do. Weights never interpolate: a step's weight changes at its state's instant.
 *
 * Choreography (§4.3, §4.8): each step slides 24 px in 400 ms on its beat (state.js `itemTimings`: a reveal event
 * wins over the beat, which starts REVEAL_LEAD before its anchor, R-17), its number first and its text 80 ms later,
 * with the personality's text entrance (`ctx.motion.entrance`). A `blank` step shows a rule until its fill moment and
 * then **types in** at 30 chars/s (`TYPE_CPS`), word by word over each word's own characters, the whole answer laid
 * out from the start so no frame re-wraps. A `countdown` event on a blank step draws the think ring beside it (§4.3's
 * "4-6 s think ring" before a "Your turn" fill; the event's `dur`, else 4 s). The formula's card fades in with the
 * scene and its symbols take their lesson-wide entity colours (§4.7, R-30, `_shared.js tintTex` / `paintTint`). The
 * template's own durations take the personality's scale (`_shared.js seconds` / `enterAt`); the cues keep their own
 * (cues.js). The background is frozen and SFX are off for the whole scene (R-55, R-49: `dense` in templates.py, which
 * the resolver reads).
 *
 * Emphasis is cues.js's (§4.2, plan §1.4/§1.5): `focusRect` gives a step's card (a shape, so the outline runs outside
 * its state border, B2c review V4), the `<item>:step:<n>` form (the scene's n-th step, 1-based), the formula's card
 * and a symbol in it (`<item>:var:<symbol>`, kept bright with the formula's group), and - through
 * `_shared.js panelFocusRect` - the panel (`panel`) and a terminal's n-th output line (`panel:row:<n>`); `itemEl`
 * hands the term spans of a step's text and justification (its field order) and of the top line; `dimGroups` the top
 * line, every step and the formula or the panel. Nothing here writes `outline`, `text-decoration`, `filter`,
 * `backdrop-filter` or `will-change`, no structure role colours content, and the cue roles (`mark`, `focus`) are
 * never used: the blank rule and the think ring are drawn in `ink2`.
 *
 * Determinism (§7.4, plan §P): `update` is a pure function of t (every value comes from `(ctx, t)`; the typing share,
 * the state ramps and the ring are closed forms in t); translates are whole pixels; the only per-frame size change
 * (9:16) is one of the two sizes `layout` measured. `layout` measures on clones in a hidden host, so it never writes a
 * property the painter owns.
 *
 * Open (recorded in the B2 handoff): §4.3's "on a fill the value flies into its slot" needs a stored value -> symbol
 * mapping that no contract carries yet (events.py, B2d/B2e); the fill is drawn as the typing-in §4.3 also specifies.
 * Every size here is a B2 proposal pending the owner's review through the reference frames.
 */

import { h, svg } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { mixOklch } from '../../motion/color.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { LAYOUT_TOKENS, fixesOf, fx } from '../core.js';
import { FOCUS_OUTSET, FOCUS_PX } from '../cues.js';
import { itemStateStyle, itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import { brokenTex } from './formula_focus.js';
import {
  PANEL_TERM, PANEL_TERM_SLOT, RADIUS, STATE_BORDER, STATE_WEIGHT, buildPanel, drawsPanel, enterAt, entityColour,
  fitLines, group, itemStateParts, layoutPanel, paintPanel, paintRise, paintTextEntrance, paintTint, panelFocusRect,
  panelMediaBox, panelOf, panelTexts, reelPanelBox, reelTimings, reelWindow, region, revealMask, richWords,
  scrimmedBg, seconds, shownPanel, slotRange, tex, tintTargets, tintTex,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */

export const ID = 'worked_steps';

// ---------------------------------------------------------------------------------------------
// Motion (§4.3 worked_steps, §4.8)
// ---------------------------------------------------------------------------------------------

/** §4.3: each step slides in ... */
export const RISE = 24;
/** ... in 400 ms on its beat ... */
export const STEP_ENTER = 0.4;
/** ... its number first and its text 80 ms later (list_build's row rhythm). */
export const TEXT_DELAY = 0.08;
/** The top line's entrance (§4.8 "medium move 400-500 ms"). */
export const HEADING_ENTER = 0.45;
/** A state change (ink and border) eases over this long (§4.8 small move); weights change at the instant. */
export const STATE_EASE = 0.3;
/** 9:16: the fade a step takes while it shrinks from large to compact, centred on the next step's entrance (out over
 * the first half, back over the second: the swap happens while the row is invisible, list_build's DIP). */
export const DIP = 0.3;
/** §4.3: a blank types in at 30 chars/s on its fill beat. */
export const TYPE_CPS = 30;
/** A TeX word counts as its LaTeX source's characters (its glyphs are one picture): with no source, this many. */
export const TEX_CHARS = 8;
/** The blank rule fades out as the answer starts typing. */
export const BLANK_FADE = 0.2;
/** The formula's card fades in just after the scene starts, over §4.3 formula_focus's 400 ms. */
export const FORMULA_DELAY = 0.15;
export const FORMULA_FADE = 0.4;
/** A `countdown` without its own `dur` runs 4 s (§4.3: "a 4-6 s think ring"); the ring fades in and out. */
export const COUNTDOWN_HOLD = 4;
export const COUNTDOWN_FADE = 0.3;

// ---------------------------------------------------------------------------------------------
// Layout (stage px; R-29 floors; the catalogue's regions, zones and layout tokens). templates.py mirrors the text
// slots (`_worked_steps_slots`), and worked_steps.test.js pins the two against each other.
// ---------------------------------------------------------------------------------------------

/**
 * A step's padding on every side (12 px): its text sits as far inside its card (where the state border is drawn) as a
 * look_here outline's outer edge sits outside it (cues.js: FOCUS_PX wide, FOCUS_OUTSET outside the card), so text,
 * state border and outline are evenly spaced (list_build's PAD).
 */
export const PAD = FOCUS_OUTSET + FOCUS_PX;
/** The state border's width (the catalogue's `outline.state_border_px`), re-exported for the tests. */
export const BORDER = STATE_BORDER;
/** The blank rule: 4 px tall, fully rounded, on the first line of the withheld answer. */
export const BLANK_PX = 4;
/** The think ring: its diameter as a share of the step's size, and its stroke. */
export const RING_SHARE = 0.9;
export const RING_PX = 4;
/** Corner radius of a look_here outline's target box round a symbol of the formula (cues.js adds its outset). */
export const SYMBOL_RADIUS = 8;
/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;
/** The formula card's shadow (the catalogue's level 2, formula_focus's card). */
export const CARD_SHADOW = String(LAYOUT_TOKENS.shadow['2']);

/**
 * Line heights of the type scale (zones.py TYPE_SCALE, plan §2: Indic body >= 1.45, Indic display / heading >= 1.25;
 * the test pins them to the catalogue's `type.scale`).
 */
export const LINE_HEIGHT = Object.freeze({
  heading: Object.freeze({ latin: 1.15, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
});

/**
 * Per-format layout. `heading`: the top line's size range and lines (16:9 R-29 headings 84-120 capped at
 * board_generic's 96; 9:16 56-88, one line preferred, R-25). `step`: a step's text (16:9 R-29 body 48-56 down to 40
 * for a long derivation, above the 36 px text floor; 9:16 R-29 body 56-64). `row`: the 9:16 compact row (R-29's text
 * floor, §4.3 ">= 40 px rows"). `why`: the justification, a label at R-29's label floor (16:9 40, 9:16 44), one size.
 * `formula`: the TeX size in the card (16:9 64-96, 9:16 68-88: above R-29's 64 px heavy rule, since MathJax's glyphs
 * have one weight - formula_focus's slot). `weight`: a step's weight when it is not
 * current (16:9 board_generic's body weight; 9:16 R-29 "<= 64 px at least 600"). `num` / `numGap`: the step-number
 * column and its gap; `padCompact`: a 9:16 compact row's top and bottom (it has no state border, so the stack stays
 * dense); `gap` between steps; `headingGap` under the top line; `whyGap` between a step's text and its justification;
 * `card`: the formula card's padding and the gap below it.
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    heading: Object.freeze({ max: 96, min: 84, lines: 2, prefer: 1 }),
    step: Object.freeze({ max: 52, min: 40 }),
    row: null,
    why: Object.freeze({ max: 40, min: 40, lines: 2 }),
    formula: Object.freeze({ max: 96, min: 64 }),
    weight: 400,
    whyWeight: 400,
    num: 64,
    numGap: 16,
    padCompact: PAD,
    gap: 16,
    headingGap: 40,
    whyGap: 8,
    card: Object.freeze({ padX: 40, padY: 32, gap: 32, textGap: 16 }),
  }),
  portrait: Object.freeze({
    heading: Object.freeze({ max: 88, min: 56, lines: 2, prefer: 1 }),
    step: Object.freeze({ max: 64, min: 56 }),
    row: Object.freeze({ max: 40, min: 40 }),
    why: Object.freeze({ max: 44, min: 44, lines: 2 }),
    // R-29 asks weight >= 600 of 9:16 text at <= 64 px and MathJax's glyphs have one weight, so the formula stays
    // above 64 px (formula_focus's reading of the same rule)
    formula: Object.freeze({ max: 88, min: 68 }),
    weight: 600,
    whyWeight: 600,
    num: 56,
    numGap: 12,
    padCompact: 4,
    gap: 12,
    headingGap: 28,
    whyGap: 4,
    card: Object.freeze({ padX: 28, padY: 24, gap: 24, textGap: 12 }),
  }),
});

/**
 * The boxes of a format and variant (pure; whole stage px): `heading` (the top line), `steps` (the step column: the
 * cards' edges, their text PAD inside), `visual` (the box of the one thing beside the steps - the formula's card, or
 * the scene's side panel: 16:9 `split` the right 45%, 9:16 the step column, where `layout` puts the formula's card
 * above the steps; null with neither) and `bottom`, where content ends. 16:9 steps end at the body box's bottom
 * (y 900, board_generic's cards); 9:16 they fill the text column (x 192-780: the part both critical boxes hold,
 * R-59) down to y 1020.
 * @param {'landscape' | 'portrait'} format
 * @param {'split' | 'centred' | 'stack' | 'list'} variant
 * @param {Zones} zones
 * @param {boolean} visual   the scene has a formula card or a panel beside the steps
 * @returns {{heading: Box, steps: Box, visual: Box | null, bottom: number}}
 */
export function boxes(format, variant, zones, visual) {
  const ctx = { format };
  if (format === 'portrait') {
    const col = zones.textColumn;
    const steps = { x0: col.x0, y0: zones.title.y0, x1: col.x1, y1: col.y1 };
    return {
      heading: { x0: zones.title.x0, y0: zones.title.y0, x1: zones.title.x1, y1: zones.title.y1 },
      steps,
      visual: visual ? { ...steps } : null,
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
      steps: { x0: left.x0, y0: top, x1: left.x1, y1: bottom },
      visual: { x0: right.x0, y0: top, x1: right.x1, y1: bottom },
      bottom,
    };
  }
  const mid = region(ctx, 'centre-60');
  return {
    heading: { x0: mid.x0, y0: top, x1: mid.x1, y1: band.y1 },
    steps: { x0: mid.x0, y0: top, x1: mid.x1, y1: bottom },
    visual: null,
    bottom,
  };
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
 * slot -> format -> variant -> {w, max_lines, size, role, face, weight, line_height, prefer_lines}. The 16:9 variants
 * are the layout's: `default` the split one (the narrower column, so a size fitted there is an upper bound for the
 * centred one too) and `centred`; the formula slot has only `default` (the centred variant has no formula). `step` and
 * `why` are laid out at the current weight (STATE_WEIGHT) in 16:9, so becoming current never re-wraps them; `row` is
 * the 9:16 compact row. Line heights are the Latin ones (Indic text takes LINE_HEIGHT's Indic values, which
 * textfit.py's Indic measure uses). Pure.
 * @param {Zones} landscape @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  /** @param {Box} b */
  const w = (b) => b.x1 - b.x0;
  /** @param {'landscape' | 'portrait'} fmt @param {Box} steps */
  const textW = (fmt, steps) => w(steps) - 2 * PAD - LAYOUT[fmt].num - LAYOUT[fmt].numGap;
  /** @param {{max: number, min: number, lines: number, prefer: number}} s @param {number} width */
  const heading = (s, width) => ({
    w: width, max_lines: s.lines, size: [s.min, s.max], role: 'title', face: 'display', weight: 700,
    line_height: LINE_HEIGHT.heading.latin, prefer_lines: s.prefer,
  });
  /** @param {{max: number, min: number}} s @param {number} width @param {number} weight @param {string} role
   * @param {number | null} lines @param {number} [lh] */
  const body = (s, width, weight, role, lines, lh = LINE_HEIGHT.body.latin) => ({
    w: width, max_lines: lines, size: [s.min, s.max], role, face: 'body', weight, line_height: lh, prefer_lines: null,
  });
  const split = boxes('landscape', 'split', landscape, true);
  const centred = boxes('landscape', 'centred', landscape, false);
  const tall = boxes('portrait', 'stack', portrait, true);
  const L = LAYOUT.landscape;
  const P = LAYOUT.portrait;
  const stepL = textW('landscape', split.steps);
  const stepC = textW('landscape', centred.steps);
  const stepP = textW('portrait', tall.steps);
  return {
    heading: {
      landscape: { default: heading(L.heading, w(split.heading)), centred: heading(L.heading, w(centred.heading)) },
      portrait: { default: heading(P.heading, w(tall.heading)) },
    },
    step: {
      landscape: {
        default: body(L.step, stepL, STATE_WEIGHT, 'body', null),
        centred: body(L.step, stepC, STATE_WEIGHT, 'body', null),
      },
      portrait: { default: body(P.step, stepP, STATE_WEIGHT, 'body', null) },
    },
    row: { portrait: { default: body(/** @type {{max: number, min: number}} */ (P.row), stepP, P.weight, 'body', null) } },
    why: {
      landscape: {
        default: body(L.why, stepL, L.whyWeight, 'label', L.why.lines),
        centred: body(L.why, stepC, L.whyWeight, 'label', L.why.lines),
      },
      portrait: { default: body(P.why, stepP, P.whyWeight, 'label', P.why.lines) },
    },
    // the formula is a TeX picture: textfit does not measure it (`max_lines` null, line height 1), and it is reported
    // as body text at its TeX size (formula_focus's FORMULA_SLOT)
    formula: {
      landscape: { default: body(L.formula, w(/** @type {Box} */ (split.visual)) - 2 * L.card.padX, 400, 'body', null, 1) },
      portrait: { default: body(P.formula, w(tall.steps) - 2 * P.card.padX, 400, 'body', null, 1) },
    },
    meaning: {
      landscape: { default: body(L.why, w(/** @type {Box} */ (split.visual)) - 2 * L.card.padX, L.whyWeight, 'label', L.why.lines) },
      portrait: { default: body(P.why, w(tall.steps) - 2 * P.card.padX, P.whyWeight, 'label', P.why.lines) },
    },
    // a terminal panel's card, mono, in the panel's own box less its padding (16:9 `split`: the right 45%; 9:16 the
    // reel beat's card in the text column, `_shared.js reelPanelBox`); `_shared.js layoutPanel` fits it on the page
    // and `_shared.js panelTexts` reports it in this slot
    terminal: {
      landscape: { default: terminalSlot('landscape', w(/** @type {Box} */ (split.visual))) },
      portrait: { default: terminalSlot('portrait', portrait.textColumn.x1 - portrait.textColumn.x0) },
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

/** The board-item kinds the top line may be: a heading, or the setup line the generator writes for an example. */
export const TOP_KINDS = Object.freeze(['heading', 'callout_info', 'paragraph']);

/**
 * What the scene shows: its `top` line (the first item of TOP_KINDS with text), its `steps` (every `example_step`
 * with text, in board order) and its `formula` (the first `formula` item with LaTeX).
 * @param {TimedScene} scene
 * @returns {{top: BoardItem | null, steps: BoardItem[], formula: BoardItem | null}}
 */
export function stepItems(scene) {
  /** @type {BoardItem | null} */
  let top = null;
  /** @type {BoardItem | null} */
  let formula = null;
  /** @type {BoardItem[]} */
  const steps = [];
  for (const item of scene.board || []) {
    if (!item) continue;
    const text = typeof item.text === 'string' ? item.text.trim() : '';
    if (item.kind === 'example_step') {
      if (text) steps.push(item);
    } else if (TOP_KINDS.includes(String(item.kind))) {
      if (text && !top) top = item;
    } else if (item.kind === 'formula' && typeof item.latex === 'string' && item.latex.trim() && !formula) {
      formula = item;
    }
  }
  return { top, steps, formula };
}

/**
 * Whether this template draws the scene whole in `format` (templates.py `_fit_worked_steps` scores 0 otherwise, or
 * the director picks it and the page silently draws board_generic: B2c review CG-2, the demo review's D5): at least
 * one `example_step` with text, at most one top line, at most one formula, nothing else on the board, no quiz, main
 * medium or poster, and a shown side panel only of a kind `_shared.js` draws (`drawsPanel`) - in 16:9 without a
 * formula (the right 45% holds one visual), in 9:16 with or without one (the panel is its own beat before the steps,
 * owner decision 4). Anything else is board_generic's, every hook handed on.
 * @param {TimedScene} scene
 * @param {'landscape' | 'portrait'} [format]
 */
export function drawsScene(scene, format = 'landscape') {
  const board = (scene.board || []).filter(Boolean);
  const { top, steps, formula } = stepItems(scene);
  if (!steps.length) return false;
  const counted = steps.length + (top ? 1 : 0) + (formula ? 1 : 0);
  if (counted !== board.length) return false;
  if (scene.quiz || scene.media || scene.poster) return false;
  if (!shownPanel(scene)) return true;
  if (format !== 'landscape') return drawsPanel(scene, format);
  return !formula && drawsPanel(scene, format);
}

/**
 * The scene's panel as this template draws it (`_shared.js panelOf`; 16:9 beside the steps, without a formula; 9:16
 * as its reel beat), or null. A 16:9 scene with a formula never has one here.
 * @param {TimedScene} scene @param {'landscape' | 'portrait'} format @param {'live' | 'render'} [mode]
 */
export function panelFor(scene, format, mode = 'live') {
  if (format === 'landscape' && stepItems(scene).formula) return null;
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
 * Whether any of `ids` enters on a narration moment (a beat or a reveal event): only then do the steps take turns
 * being current.
 * @param {TimedScene} scene @param {readonly string[]} ids
 */
export function anchored(scene, ids) {
  const keep = new Set(ids);
  return itemTimings(scene).some((it) => keep.has(it.id) && !it.initial);
}

/**
 * Whether the steps take states at all (list_build's rule, plus the blanks): any step is beat- or event-revealed, or
 * any step has a fill moment (its answer finishes as `done`).
 * @param {TimedScene} scene @param {readonly string[]} ids
 */
export function statesOn(scene, ids) {
  const keep = new Set(ids);
  return anchored(scene, ids) || itemTimings(scene).some((it) => keep.has(it.id) && it.fill !== null);
}

/**
 * Layout variant: 16:9 `split` (a formula card or the scene's panel beside the steps) or `centred`; 9:16 `stack` (the
 * steps take turns being current) or `list` (no step is beat-revealed). The same in both modes: the panel is laid out
 * whether or not this mode can show its medium, so the live player and the MP4 share one layout (media_split's rule).
 * @param {TimedScene} scene @param {'landscape' | 'portrait'} format
 * @returns {'split' | 'centred' | 'stack' | 'list'}
 */
export function variantOf(scene, format) {
  const { steps, formula } = stepItems(scene);
  if (format === 'portrait') return anchored(scene, steps.map((s) => s.id)) ? 'stack' : 'list';
  return formula || panelFor(scene, format, 'render') ? 'split' : 'centred';
}

/**
 * The formula's LaTeX with each symbol of its variables wrapped in a `\color` placeholder (`_shared.js tintTex`).
 * @param {BoardItem} item
 */
export function formulaTex(item) {
  return tintTex(String(item.latex || ''), (item.variables || []).map((v) => String(v.symbol_latex || '')));
}

/**
 * Every TeX string the template renders for a scene in `fmt` (the static `tex` hook; render.js converts them before
 * `ready`): the formula (display, tinted; 9:16 its form broken at "=" too, which layout may pick) and the `$...$`
 * spans of the top line, of each step and of each justification (inline, as `richWords` mounts them).
 * @param {TimedScene} scene @param {'landscape' | 'portrait'} fmt
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene, fmt) {
  const { top, steps, formula } = stepItems(scene);
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  /** @param {string} s @param {boolean} display */
  const add = (s, display) => {
    if (typeof s === 'string' && s.trim()) out.push({ tex: s, display });
  };
  if (formula) {
    add(formulaTex(formula).tex, true);
    // 9:16 may break it at its "=" signs (formula_focus's `brokenTex`: an `aligned` form, tinted the same way)
    if (fmt === 'portrait') add(brokenTex(formula) || '', true);
    for (const s of mathSpans(String(formula.text || ''))) add(s, false);
  }
  for (const item of [...(top ? [top] : []), ...steps]) {
    for (const s of mathSpans(String(item.text || ''))) add(s, false);
    for (const s of mathSpans(String(item.justification || ''))) add(s, false);
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
 * The characters each word of `words` counts for while an answer types in (§4.3's 30 chars/s): a plain word its own
 * characters, a word holding TeX its LaTeX source's (its glyphs are one picture; TEX_CHARS without a source). Read at
 * build time, before MathJax replaces the span, so it never depends on what the converter drew.
 * @param {HTMLElement[]} words @param {boolean[]} texFlags @param {string} text   the item's rich-lite text
 * @returns {number[]}
 */
export function wordChars(words, texFlags, text) {
  const maths = mathSpans(String(text || ''));
  let k = 0;
  return words.map((span, i) => {
    if (texFlags[i]) {
      const src = maths[k++];
      return Math.max(1, (typeof src === 'string' ? src.length : 0) || TEX_CHARS);
    }
    return Math.max(1, (span.textContent || '').length);
  });
}

/**
 * How far each word of an answer has typed in at scene time t (0..1 per word, in order): nothing before `fill`, each
 * word over its own characters at `TYPE_CPS`. Pure.
 * @param {number[]} chars @param {number | null} fill @param {number} t
 * @returns {number[]}
 */
export function typedAt(chars, fill, t) {
  if (fill === null) return chars.map(() => 1);
  const typed = Math.max(0, t - fill) * TYPE_CPS;
  let at = 0;
  return chars.map((n) => {
    const p = Math.min(1, Math.max(0, (typed - at) / n));
    at += n;
    return p;
  });
}

/** How long an answer takes to type in (seconds; its characters at TYPE_CPS). @param {number[]} chars */
export function typeSeconds(chars) {
  return chars.reduce((a, n) => a + n, 0) / TYPE_CPS;
}

/**
 * The `countdown` event on item `id` (the think ring): its start and length (the event's `dur`, else COUNTDOWN_HOLD);
 * null without one. The earliest wins.
 * @param {TimedScene} scene @param {string} id
 * @returns {{t: number, dur: number} | null}
 */
export function countdownOf(scene, id) {
  /** @type {{t: number, dur: number} | null} */
  let out = null;
  for (const e of scene.events || []) {
    if (!e || e.kind !== 'countdown' || typeof e.t !== 'number' || !Number.isFinite(e.t)) continue;
    if (String(e.target || '').split(':')[0] !== id) continue;
    const dur = typeof e.dur === 'number' && Number.isFinite(e.dur) && e.dur > 0 ? e.dur : COUNTDOWN_HOLD;
    if (!out || e.t < out.t) out = { t: e.t, dur };
  }
  return out;
}

/**
 * Every state a step takes, in order (pure, plan §1.5): `current` at its entrance, `done` when its blank finishes
 * typing while it is still the last step entered, `past` when the next step enters (which wins over `done`, so only
 * the final answer keeps its check).
 * @param {number} enter @param {number | null} nextEnter @param {number | null} doneAt
 * @returns {{at: number, state: 'current' | 'past' | 'done'}[]}
 */
export function stateTrack(enter, nextEnter, doneAt) {
  /** @type {{at: number, state: 'current' | 'past' | 'done'}[]} */
  const out = [{ at: enter, state: 'current' }];
  if (doneAt !== null && (nextEnter === null || doneAt < nextEnter)) out.push({ at: doneAt, state: 'done' });
  if (nextEnter !== null) out.push({ at: nextEnter, state: 'past' });
  return out;
}

/**
 * The state at scene time t: the last one the track reached, when it started and the state before it (null: the
 * step's look without a state, which its entrance eases from). Null before the step enters.
 * @param {{at: number, state: 'current' | 'past' | 'done'}[]} track @param {number} t
 * @returns {{state: 'current' | 'past' | 'done', since: number, from: string | null} | null}
 */
export function stateAt(track, t) {
  let i = -1;
  for (let k = 0; k < track.length; k++) if (track[k].at <= t) i = k;
  if (i < 0) return null;
  return { state: track[i].state, since: track[i].at, from: i > 0 ? track[i - 1].state : null };
}

// ---------------------------------------------------------------------------------------------
// Measuring (layout time, on clones)
// ---------------------------------------------------------------------------------------------

/**
 * A step's height and its words' state when laid out at `size` px and `weight`, with `padY` above and below (PAD at
 * the sides) in a card `width` px wide, with or without its justification: a clone of the card in the hidden measuring
 * host, so no painted property of the real card is touched. `broken[i]`: word i is broken inside (a plain word taller
 * than 1.5 lines) or, holding TeX, wider than the text box; `wide`: px the widest TeX word passes the text box.
 * @param {HTMLElement} host @param {any} step @param {number} width @param {number} size @param {number} weight
 * @param {number} padY @param {boolean} withWhy @param {number} whySize
 * @returns {{height: number, broken: boolean[], wide: number}}
 */
function measureStep(host, step, width, size, weight, padY, withWhy, whySize) {
  const c = /** @type {HTMLElement} */ (step.card.cloneNode(true));
  Object.assign(c.style, {
    position: 'absolute', left: '0px', top: '0px', width: `${width}px`, fontSize: `${size}px`,
    fontWeight: String(weight), padding: `${padY}px ${PAD}px`, visibility: 'hidden',
  });
  const why = /** @type {HTMLElement | null} */ (c.querySelector('.dyn-ws-why'));
  if (why) {
    why.style.display = withWhy ? 'block' : 'none';
    why.style.fontSize = `${whySize}px`;
  }
  host.appendChild(c);
  const text = /** @type {HTMLElement} */ (c.querySelector('.dyn-ws-text'));
  const words = /** @type {HTMLElement[]} */ ([...c.querySelectorAll('.dyn-ws-text .dyn-word')]);
  const box = text ? text.clientWidth : 0;
  const broken = words.map((w, i) => (step.wordTex[i] ? w.offsetWidth > box + 1 : w.offsetHeight > 1.5 * size * step.lh));
  const wide = Math.max(0, ...words.map((w, i) => (step.wordTex[i] ? w.offsetWidth - box : 0)));
  const height = c.offsetHeight;
  c.remove();
  return { height, broken, wide };
}

/**
 * The largest size (SIZE_STEP steps, `min` last) at which `ok(heights)` holds for every step measured at it with
 * every word whole (a word broken even at `min` does not count against a size; a TeX word too wide at `min` holds the
 * steps at `min`, where it runs out least: board_generic's and `_shared.js fitLines`' rule), and the heights there.
 * `fits` false: nothing fits and the steps are at `min`.
 * @param {HTMLElement} host @param {any[]} steps @param {number} width @param {{max: number, min: number}} range
 * @param {number} weight @param {number} padY @param {boolean} withWhy @param {number} whySize
 * @param {(heights: number[]) => boolean} ok
 * @returns {{size: number, fits: boolean, heights: number[], wide: number[]}}
 */
function fitSteps(host, steps, width, range, weight, padY, withWhy, whySize, ok) {
  const atMin = steps.map((s) => measureStep(host, s, width, range.min, weight, padY, withWhy, whySize));
  const texTooWide = atMin.some((m, k) => m.broken.some((b, i) => b && steps[k].wordTex[i]));
  for (const s of texTooWide ? [range.min] : sizeSteps(range.max, range.min)) {
    const ms = s === range.min ? atMin : steps.map((x) => measureStep(host, x, width, s, weight, padY, withWhy, whySize));
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

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.wsFallback);

/**
 * What one state looks like on a step (plan §1.5 through `itemStateStyle`, with this template's weights): the text
 * weight and ink, the border colour and its alpha, and whether the check is shown. `null`: the step's look before any
 * state (its entrance eases the border in from it).
 * @param {TemplateContext} ctx @param {string | null} state @param {number} weight
 * @returns {{weight: number, ink: string, border: string, borderAlpha: number, check: number}}
 */
function stateLook(ctx, state, weight) {
  const roles = stateRoles(ctx);
  const ink2 = ctx.onBackground.ink2;
  if (!state) return { weight, ink: ctx.onBackground.ink, border: ink2, borderAlpha: 0, check: 0 };
  const s = itemStateStyle(state, roles);
  return {
    weight: state === 'past' ? weight : STATE_WEIGHT,
    ink: s.ink,
    border: s.border || ink2,
    borderAlpha: s.border ? 1 : 0,
    check: s.check ? 1 : 0,
  };
}

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** @type {TemplateModule} */
export default {
  id: ID,

  /**
   * The DOM, once per scene (no measuring): the top line, a card per step (number, text, blank rule, justification,
   * state parts, think ring) and the formula's card.
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format } = ctx;
    if (!drawsScene(scene, format)) {
      // a board this template does not draw whole: board_generic draws it, every hook handed on
      const inner = boardGeneric.build(ctx);
      inner.wsFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const { top, steps: items, formula } = stepItems(scene);
    const variant = variantOf(scene, format);
    const fixes = fixesOf(ctx.look);
    const baseline = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    const ink = ctx.onBackground.ink;
    const ink2 = ctx.onBackground.ink2;
    const root = h('div', {
      class: ['dyn-ws', `dyn-ws--${format}`, `dyn-ws--${variant}`],
      style: { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' },
    });

    // --- the top line (text on the background; R-53's scrim remedy behind it when preflight asked)
    /** @type {any} */
    let head = null;
    if (top) {
      const lh = lineHeight('heading', top.text || '');
      const text = h('h2', {
        class: 'dyn-ws-heading', dir: 'auto',
        style: {
          margin: '0px', fontFamily: 'var(--dyn-font-display)', fontWeight: '700', lineHeight: String(lh),
          overflowWrap: 'anywhere', color: ink,
        },
      });
      const { words, tex: texFlags } = richWords(ctx, text, String(top.text || ''));
      const mask = baseline ? revealMask(text) : null;
      const scrim = fixes.slots.heading && fixes.slots.heading.scrim ? ctx.roles.color('scrim') : null;
      const el = group('dyn-ws-heading-group', mask || text);
      el.style.position = 'absolute';
      if (scrim) {
        Object.assign(el.style, {
          padding: '10px 24px 14px', marginLeft: '-24px', borderRadius: `${RADIUS.card}px`, background: scrim,
        });
      }
      root.appendChild(el);
      head = { item: top, el, text, mask, words, tex: texFlags, lh, scrim, size: L.heading.max, min: L.heading.min, overflow: 0, alpha: 0 };
    }

    // --- the steps: a dim group per step > its card (state border, painted ink / weight / size) > number + text
    const stepScrim = fixes.slots.step && fixes.slots.step.scrim ? ctx.roles.color('scrim') : null;
    const steps = items.map((item, k) => {
      const lh = lineHeight('body', item.text || '');
      const num = h('span', {
        class: 'dyn-ws-num', text: `${k + 1}`, 'aria-hidden': 'true',
        style: { flex: 'none', width: `${L.num}px`, fontWeight: '700', fontVariantNumeric: 'tabular-nums', lineHeight: String(lh) },
      });
      const text = h('div', { class: 'dyn-ws-text', dir: 'auto', style: { lineHeight: String(lh), overflowWrap: 'anywhere' } });
      const { words, tex: texFlags } = richWords(ctx, text, String(item.text || ''));
      const mask = baseline ? revealMask(text) : null;
      const blank = item.blank ? h('div', {
        class: 'dyn-ws-blank', 'aria-hidden': 'true',
        style: {
          position: 'absolute', left: '0px', right: '0px', height: `${BLANK_PX}px`, borderRadius: `${BLANK_PX / 2}px`,
          background: ink2, top: '0px',
        },
      }) : null;
      const whyText = typeof item.justification === 'string' ? item.justification.trim() : '';
      const why = whyText ? h('div', {
        class: 'dyn-ws-why', dir: 'auto',
        style: {
          display: 'block', marginTop: `${L.whyGap}px`, fontWeight: String(L.whyWeight), lineHeight: String(lineHeight('body', whyText)),
          overflowWrap: 'anywhere', color: ink2, fontSize: `${L.why.max}px`,
        },
      }) : null;
      const whyWords = why ? richWords(ctx, why, whyText) : { words: [], tex: [] };
      const textBox = h('div', {
        class: 'dyn-ws-textbox', style: { flex: '1 1 auto', minWidth: '0px', position: 'relative' },
      }, mask || text, blank, why);
      const body = h('div', {
        class: 'dyn-ws-body', style: { display: 'flex', alignItems: 'flex-start', gap: `${L.numGap}px` },
      }, num, textBox);
      const card = h('div', {
        class: 'dyn-ws-item', dataset: { item: item.id },
        style: {
          position: 'relative', boxSizing: 'border-box', width: '100%', borderRadius: `${RADIUS.card}px`,
          // 16:9: PAD on every side; a 9:16 step's padding changes with its form, so update paints it
          padding: format === 'landscape' ? `${PAD}px` : undefined,
          fontFamily: 'var(--dyn-font-body)', background: stepScrim || 'transparent',
        },
      }, body);
      const parts = itemStateParts(card);
      // the think ring of a `countdown` on this step (§4.3), left of where the done check is drawn
      const count = countdownOf(scene, item.id);
      /** @type {{el: SVGElement, circle: SVGElement, length: number} | null} */
      let ring = null;
      if (count) {
        // a 100 x 100 viewBox: layout sets the ring's px size and the stroke width that makes it RING_PX on screen
        const circle = svg('circle', {
          cx: '50', cy: '50', r: '44', fill: 'none', 'stroke-width': '8', 'stroke-linecap': 'round',
          transform: 'rotate(-90 50 50)',
        });
        circle.setAttribute('stroke', ink2);
        const el = svg('svg', { class: 'dyn-ws-ring', viewBox: '0 0 100 100', 'aria-hidden': 'true', focusable: 'false' }, circle);
        Object.assign(/** @type {HTMLElement} */ (/** @type {unknown} */ (el)).style, { position: 'absolute', top: '50%', pointerEvents: 'none' });
        card.appendChild(el);
        ring = { el, circle, length: 2 * Math.PI * 44 };
      }
      const el = group('dyn-ws-row', card);
      el.style.position = 'absolute';
      root.appendChild(el);
      const chars = wordChars(words, texFlags, String(item.text || ''));
      return {
        item, k, el, card, body, num, text, mask, words, wordTex: texFlags, why, whyWords, whyText, blank, parts, ring,
        count, chars, typeFor: typeSeconds(chars), lh,
        top: 0, height: 0, overflowLarge: 0, overflowCompact: 0,
        ink, weight: L.weight, size: L.step.max, whySize: L.why.max, alpha: 0, whyAlpha: 0, compactNow: false,
        typed: 1, fillAt: /** @type {number | null} */ (null),
        /** @type {{at: number, state: 'current' | 'past' | 'done'}[]} */ track: [],
      };
    });

    // --- the formula's card (surface; its symbols take their entity colours, §4.7)
    /** @type {any} */
    let card = null;
    if (formula) {
      const ft = formulaTex(formula);
      const wide = h('span', { class: 'dyn-ws-tex', style: { display: 'inline-block', lineHeight: '0' } });
      tex(ctx, wide, ft.tex, true);
      const brokenString = format === 'portrait' ? brokenTex(formula) : null;
      const broken = brokenString ? h('span', { class: 'dyn-ws-tex dyn-ws-tex--broken', style: { display: 'none', lineHeight: '0' } }) : null;
      if (broken && brokenString) tex(ctx, broken, brokenString, true);
      const host = h('div', {
        class: 'dyn-ws-formula', style: { textAlign: 'center', lineHeight: '0', whiteSpace: 'nowrap' },
      }, wide, broken);
      const meaningText = typeof formula.text === 'string' ? formula.text.trim() : '';
      const meaning = meaningText ? h('div', {
        class: 'dyn-ws-meaning', dir: 'auto',
        style: {
          marginTop: `${L.card.textGap}px`, textAlign: 'center', fontWeight: String(L.whyWeight),
          lineHeight: String(lineHeight('body', meaningText)), overflowWrap: 'anywhere',
          color: ctx.roles.color('ink2'), fontSize: `${L.why.max}px`,
        },
      }) : null;
      const meaningWords = meaning ? richWords(ctx, meaning, meaningText) : { words: [], tex: [] };
      const box = h('div', {
        class: 'dyn-ws-card',
        style: {
          position: 'absolute', boxSizing: 'border-box', borderRadius: `${RADIUS.card}px`,
          background: ctx.roles.color('surface'), color: ctx.roles.color('ink'), boxShadow: CARD_SHADOW,
          padding: `${L.card.padY}px ${L.card.padX}px`,
        },
      }, host, meaning);
      const el = group('dyn-ws-visual', box);
      Object.assign(el.style, { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' });
      root.appendChild(el);
      card = {
        item: formula, el, box, host, wide, broken, brokenString, meaning, meaningWords, meaningText, tints: ft.tints,
        /** @type {Map<string, Element[]>} */ targets: new Map(), texEl: wide,
        size: L.formula.max, min: L.formula.min, overflow: 0, meaningSize: L.why.max, meaningOverflow: 0, alpha: 0,
        laidOut: false,
      };
    }

    // --- the scene's panel: 16:9 in the visual box the formula's card would have taken (formula-less scenes only:
    // `drawsScene` / `panelFor`); 9:16 alone on the stage as its own beat before the steps (owner decision 4),
    // entering at the beat's start. `_shared.js` builds, lays out, paints and reports it
    const beat = reelBeat(scene, format);
    /** @type {import('./_shared.js').PanelParts | null} */
    let panelParts = null;
    const panel = panelFor(scene, format, ctx.mode);
    if (panel) {
      panelParts = buildPanel(ctx, beat ? { ...panel, start: beat.start } : panel);
      root.appendChild(panelParts.el);
    }

    ctx.root.appendChild(root);
    const ids = items.map((s) => s.id);
    // with a reel beat, what would enter during it enters as it ends (`_shared.js reelTimings`)
    const timings = beat ? reelTimings(scene, beat.end) : itemTimings(scene);
    /** @type {Map<string, number>} */
    const enter = new Map(timings.map((it) => [it.id, it.enter]));
    /** @type {Map<string, number | null>} */
    const fill = new Map(timings.map((it) => [it.id, it.fill]));
    const order = timings.filter((it) => ids.includes(it.id)).map((it) => it.id);
    const states = statesOn(scene, ids);
    for (const s of steps) {
      const at = enter.get(s.item.id) || 0;
      const fillAt = s.item.blank ? (fill.get(s.item.id) ?? null) : null;
      const next = nextEnter(order, enter, s.item.id);
      const doneAt = fillAt === null ? null : fillAt + s.typeFor;
      s.fillAt = fillAt;
      s.track = states ? stateTrack(at, next, doneAt) : [];
    }
    return {
      variant, root, head, steps, card, panel: panelParts, ids, enter, order, states, stepScrim, beat,
      // the formula's card enters with the scene, or - after a reel beat - at its item's entrance as the beat
      // held it back (`_shared.js reelEntrances`: the beat's end, staggered), with the content
      formulaAt: beat && formula ? (enter.get(formula.id) ?? beat.end) : FORMULA_DELAY,
      allCompact: false, stepsTop: 0, largeSize: L.step.max, compactSize: L.row ? L.row.max : L.step.max,
      stepMin: L.step.min, rowMin: L.row ? L.row.min : L.step.min, whyMin: L.why.min,
    };
  },

  /**
   * Measure and fit (runs again when a late face arrives; idempotent): the top line, then the formula's card, then the
   * steps on clones - 16:9 and a 9:16 plain list at the largest common size whose whole column fits, a 9:16 stack at
   * the largest current size at which, for every step, the compact steps above it plus it, large, fit. Positions are
   * set here, once; `update` only paints.
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
    const b = boxes(format, v.variant, zones, !!v.card || (!!v.panel && !v.beat));

    // --- the top line: its own element's size (nothing paints it), fitted in place
    let top = b.steps.y0;
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
      // below the top line as laid out, never above one line of it at its minimum (jsdom has no layout)
      const bottom = hd.el.offsetTop + hd.el.offsetHeight;
      top = Math.round(Math.max(bottom, b.heading.y0 + range.min * hd.lh) + L.headingGap);
    }

    // --- the formula's card: the largest TeX size (9:16: of the whole or the broken form) that fits its inner width
    if (v.card && b.visual) {
      const c = v.card;
      const range = slotRange(ctx, 'formula', { min: L.formula.min, max: L.formula.max, role: 'body' });
      const inner = (b.visual.x1 - b.visual.x0) - 2 * L.card.padX;
      /** @param {HTMLElement} el */
      const widest = (el) => {
        let s = Math.max(range.min, range.max);
        for (;;) {
          c.host.style.fontSize = `${s}px`;
          const w = el.offsetWidth;
          if (w <= inner || s <= range.min) return { size: s, over: w > inner + 2 ? w - inner : 0 };
          s = Math.max(range.min, s - SIZE_STEP);
        }
      };
      /** @param {HTMLElement} el @param {boolean} on */
      const show = (el, on) => {
        el.style.display = on ? 'inline-block' : 'none';
      };
      show(c.wide, true);
      if (c.broken) show(c.broken, false);
      let fit = { el: /** @type {HTMLElement} */ (c.wide), ...widest(c.wide) };
      if (fit.over > 0 && c.broken) {
        show(c.wide, false);
        show(c.broken, true);
        const alt = widest(c.broken);
        if (alt.over < fit.over) fit = { el: /** @type {HTMLElement} */ (c.broken), ...alt };
        else {
          show(c.broken, false);
          show(c.wide, true);
        }
      }
      c.host.style.fontSize = `${fit.size}px`;
      // layout runs again when a late face arrives and may pick the other form: the tint targets are found again from
      // the form that is shown (tintTargets is idempotent), never kept from the hidden one
      if (c.texEl !== fit.el) c.targets = new Map();
      c.texEl = fit.el;
      c.size = fit.size;
      c.min = range.min;
      c.overflow = fit.over;
      // MathJax's display container brings its own margins and line strut: the card measures its picture only
      for (const host of [c.wide, c.broken]) {
        const mjx = host ? /** @type {HTMLElement | null} */ (host.querySelector('mjx-container')) : null;
        if (mjx) Object.assign(mjx.style, { margin: '0px', lineHeight: '0', display: 'block' });
      }
      if (c.meaning) {
        const mRange = slotRange(ctx, 'meaning', { min: L.why.min, max: L.why.max, role: 'label' });
        const mFit = fitLines(c.meaning, {
          max: mRange.max, min: mRange.min, lines: L.why.lines, lh: lineHeight('body', c.meaningText), step: SIZE_STEP,
        }, c.meaningWords.words, c.meaningWords.tex);
        c.meaningSize = mFit.size;
        c.meaningMin = mRange.min;
        c.meaningOverflow = mFit.overflow;
      }
      const width = b.visual.x1 - b.visual.x0;
      c.box.style.width = `${width}px`;
      c.box.style.left = `${b.visual.x0}px`;
      const height = c.box.offsetHeight;
      if (format === 'portrait') {
        c.box.style.top = `${top}px`;
        top = Math.round(top + height + L.card.gap);
      } else {
        // centred on the step column's own box (never above its top)
        const room = Math.max(0, (b.visual.y1 - top) - height);
        c.box.style.top = `${Math.round(top + room / 2)}px`;
      }
      c.laidOut = true;
    }
    // --- the panel: 16:9 in the same visual box, from the steps' top (list_build's panel box); 9:16 alone on the
    // reel's stage (`_shared.js reelPanelBox`), which moves nothing of the step layout
    if (v.panel && v.beat) layoutPanel(ctx, v.panel, reelPanelBox(v.panel.panel, zones));
    else if (v.panel && b.visual) layoutPanel(ctx, v.panel, { ...b.visual, y0: top });
    v.stepsTop = top;

    // --- the steps
    const width = b.steps.x1 - b.steps.x0;
    const avail = b.bottom - top;
    const steps = v.steps;
    const step = slotRange(ctx, 'step', { min: L.step.min, max: L.step.max, role: 'body' });
    const rowRange = L.row ? slotRange(ctx, 'row', { min: L.row.min, max: L.row.max, role: 'body' }) : null;
    const whyRange = slotRange(ctx, 'why', { min: L.why.min, max: L.why.max, role: 'label' });
    v.stepMin = step.min;
    v.rowMin = rowRange ? rowRange.min : step.min;
    v.whyMin = whyRange.min;
    v.allCompact = false;
    for (const s of steps) s.whySize = whyRange.max;
    const host = h('div', {
      class: 'dyn-ws-measure', 'aria-hidden': 'true',
      style: { position: 'absolute', left: '0px', top: '0px', width: `${width}px`, visibility: 'hidden' },
    });
    ctx.root.appendChild(host);
    try {
      if (v.variant === 'stack' && rowRange) {
        // 9:16 stack: every step's top is the compact steps that entered before it; the current size is the largest at
        // which each step, large (with its justification), fits below them
        const compact = steps.map((/** @type {any} */ s) => measureStep(host, s, width, rowRange.max, L.weight, L.padCompact, false, whyRange.max));
        /** @type {Map<string, number>} */
        const tops = new Map();
        let y = 0;
        for (const id of v.order) {
          const s = steps.find((/** @type {any} */ x) => x.item.id === id);
          tops.set(id, y);
          y += compact[s.k].height + L.gap;
        }
        const below = (/** @type {number[]} */ hs) => steps.every((/** @type {any} */ s, k) => (tops.get(s.item.id) || 0) + hs[k] <= avail);
        const fit = fitSteps(host, steps, width, step, STATE_WEIGHT, PAD, true, whyRange.max, below);
        v.largeSize = fit.size;
        v.compactSize = rowRange.max;
        for (const s of steps) {
          s.top = tops.get(s.item.id) || 0;
          s.height = fit.heights[s.k];
          s.overflowLarge = Math.max(0, s.top + fit.heights[s.k] - avail, fit.wide[s.k] > 2 ? fit.wide[s.k] : 0);
          s.overflowCompact = Math.max(0, s.top + compact[s.k].height - avail, compact[s.k].wide > 2 ? compact[s.k].wide : 0);
        }
      } else {
        // one size for every step, the whole column under the top line (16:9 at the current weight, so a step turning
        // current never re-wraps; a 9:16 plain list that does not fit large goes compact, never fewer steps)
        const total = (/** @type {number[]} */ hs) => hs.reduce((a, x) => a + x, 0) + L.gap * Math.max(0, hs.length - 1);
        let fit = fitSteps(host, steps, width, step, format === 'landscape' ? STATE_WEIGHT : L.weight, PAD, true, whyRange.max, (hs) => total(hs) <= avail);
        if (!fit.fits && rowRange) {
          fit = fitSteps(host, steps, width, rowRange, L.weight, L.padCompact, false, whyRange.max, (hs) => total(hs) <= avail);
          v.allCompact = true;
        }
        v.largeSize = fit.size;
        v.compactSize = fit.size;
        let y = 0;
        for (const s of steps) {
          s.top = y;
          s.height = fit.heights[s.k];
          const over = Math.max(0, y + fit.heights[s.k] - avail, fit.wide[s.k] > 2 ? fit.wide[s.k] : 0);
          s.overflowLarge = over;
          s.overflowCompact = over;
          y += fit.heights[s.k] + L.gap;
        }
      }
    } finally {
      host.remove();
    }
    for (const s of steps) {
      Object.assign(s.el.style, { left: `${b.steps.x0}px`, top: `${v.stepsTop + s.top}px`, width: `${width}px` });
      // 16:9 steps keep one size, set here; a 9:16 step's size (large / compact) is painted per frame
      if (format === 'landscape') s.card.style.fontSize = `${v.largeSize}px`;
      if (s.why) s.why.style.fontSize = `${whyRange.max}px`;
      // the blank rule on the first line of the withheld answer, and the think ring beside the step
      if (s.blank) s.blank.style.top = `${Math.round(0.82 * v.largeSize)}px`;
      if (s.ring) {
        const size = Math.max(RING_PX * 4, Math.round(v.largeSize * RING_SHARE));
        Object.assign(/** @type {HTMLElement} */ (/** @type {unknown} */ (s.ring.el)).style, {
          width: `${size}px`, height: `${size}px`, right: `${PAD + size + 16}px`, marginTop: `${-Math.round(size / 2)}px`,
        });
        // the stroke in the 100 x 100 viewBox that draws RING_PX on screen (its radius 44 keeps it inside the box)
        s.ring.circle.setAttribute('stroke-width', fx(Math.min(12, (RING_PX * 100) / size), 2));
      }
    }
  },

  /**
   * Paint scene time t: the top line's entrance; each step's entrance, state, typing and (9:16) size; the formula's
   * card and its tints. A pure function of t: every property painted on an element is painted on every frame, with a
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
    const L = LAYOUT[ctx.format];
    const portrait = ctx.format === 'portrait';

    if (v.head) {
      const e = enterAt(ctx, t, v.enter.get(v.head.item.id) || 0, HEADING_ENTER);
      v.head.alpha = e;
      paintTextEntrance(ctx, v.head.text, v.head.mask, e);
    }

    const delay = seconds(ctx, TEXT_DELAY);
    const halfDip = seconds(ctx, DIP) / 2;
    const ease2 = seconds(ctx, STATE_EASE);
    for (const s of v.steps) {
      const start = v.enter.get(s.item.id) || 0;
      const e = enterAt(ctx, t, start, STEP_ENTER);
      const eText = enterAt(ctx, t, start + delay, STEP_ENTER);
      const now = s.track.length ? stateAt(s.track, t) : null;
      const look = stateLook(ctx, now ? now.state : null, L.weight);
      const before = stateLook(ctx, now ? now.from : null, L.weight);
      const k = now ? ease('ease.move', progress(t, now.since, ease2)) : 1;
      const ink = k >= 1 ? look.ink : mixOklch(before.ink, look.ink, k);
      const borderColour = k >= 1 || before.border === look.border ? look.border : mixOklch(before.border, look.border, k);
      const borderAlpha = before.borderAlpha + (look.borderAlpha - before.borderAlpha) * k;
      const check = before.check + (look.check - before.check) * k;
      // 9:16 stack: large while current or done, compact from the next step's entrance; the swap happens at the bottom
      // of a fade centred on that entrance (the step is invisible while its size changes)
      let compact = v.allCompact;
      let alpha = 1;
      if (now && portrait && v.variant === 'stack') {
        compact = now.state === 'past';
        const turn = nextEnter(v.order, v.enter, s.item.id);
        if (turn !== null) alpha = ease('ease.move', Math.min(1, Math.abs(t - turn) / halfDip));
      }
      const size = compact ? v.compactSize : v.largeSize;
      s.ink = ink;
      s.weight = look.weight;
      s.size = size;
      s.compactNow = compact;
      s.alpha = eText * alpha;
      p.style(s.card, 'opacity', fx(alpha));
      p.style(s.card, 'visibility', e > 0 && alpha > 0 ? 'visible' : 'hidden');
      p.style(s.card, 'color', ink);
      p.style(s.card, 'font-weight', String(look.weight));
      p.style(s.card, '--dyn-state-border', withAlpha(borderColour, borderAlpha * e));
      if (portrait) {
        p.style(s.card, 'font-size', `${size}px`);
        p.style(s.card, 'padding', `${compact ? L.padCompact : PAD}px ${PAD}px`);
      }
      // the done check (its strike layer is never used: no step is `struck`)
      p.style(s.parts.check, 'color', ctx.roles.color('ok'));
      p.style(s.parts.check, 'opacity', fx(check * e));
      p.style(s.parts.check, 'visibility', check * e > 0 ? 'visible' : 'hidden');
      p.style(s.parts.strike, 'visibility', 'hidden');
      // the number, then the text (the personality's entrance)
      paintRise(ctx, s.num, e, RISE);
      if (s.mask) paintTextEntrance(ctx, s.text, s.mask, eText);
      else paintRise(ctx, s.text, eText, RISE);
      // a blank answer: the rule until its fill, then the words typing in at TYPE_CPS
      if (s.blank) {
        const typed = typedAt(s.chars, s.fillAt, t);
        const out = s.fillAt === null ? 1 : ease('ease.move', progress(t, s.fillAt, seconds(ctx, BLANK_FADE)));
        p.style(s.blank, 'opacity', fx(eText * (1 - out)));
        p.style(s.blank, 'visibility', eText > 0 && out < 1 ? 'visible' : 'hidden');
        s.typed = typed.length ? typed.reduce((a, /** @type {number} */ x) => a + x, 0) / typed.length : 1;
        s.words.forEach((/** @type {HTMLElement} */ word, i) => {
          p.style(word, 'opacity', fx(typed[i]));
          p.style(word, 'visibility', typed[i] > 0 ? 'visible' : 'hidden');
        });
      } else {
        s.typed = 1;
      }
      // the justification: with the step's text, hidden in a compact row (it is not laid out there)
      if (s.why) {
        const a = compact ? 0 : eText;
        s.whyAlpha = a * alpha;
        p.style(s.why, 'opacity', fx(a));
        p.style(s.why, 'visibility', a > 0 ? 'visible' : 'hidden');
      }
      // the think ring: it empties over the countdown, fading in and out
      if (s.ring && s.count) {
        const into = ease('ease.move', progress(t, s.count.t, COUNTDOWN_FADE));
        const gone = ease('ease.move', progress(t, s.count.t + s.count.dur, COUNTDOWN_FADE));
        const a = into * (1 - gone);
        const run = Math.min(1, Math.max(0, (t - s.count.t) / s.count.dur));
        p.style(/** @type {SVGElement} */ (s.ring.el), 'opacity', fx(a));
        p.style(/** @type {SVGElement} */ (s.ring.el), 'visibility', a > 0 ? 'visible' : 'hidden');
        p.attr(s.ring.circle, 'stroke-dasharray', `${fx(s.ring.length, 2)} ${fx(s.ring.length, 2)}`);
        p.attr(s.ring.circle, 'stroke-dashoffset', fx(s.ring.length * run, 2));
      }
    }

    // --- the formula's card: its entrance, and each symbol's entity colour (R-30)
    if (v.card) {
      const c = v.card;
      c.alpha = enterAt(ctx, t, v.formulaAt, FORMULA_FADE);
      paintRise(ctx, c.box, c.alpha, RISE);
      p.style(c.box, 'visibility', c.alpha > 0 ? 'visible' : 'hidden');
      if (!c.targets.size && c.tints.length) c.targets = tintTargets(c.texEl, c.tints);
      for (const tint of c.tints) {
        paintTint(ctx, /** @type {Element[]} */ (c.targets.get(tint.symbol) || []), entityColour(ctx, tint.symbol));
      }
    }

    // --- the scene's panel: its entrance, its medium and a terminal's output lines (`_shared.js paintPanel`); in a
    // reel beat it leaves as the beat ends and shows every output line
    if (v.panel && v.beat) paintPanel(ctx, v.panel, t, state, { until: v.beat.end, allLines: true });
    else if (v.panel) paintPanel(ctx, v.panel, t, state);
  },

  /**
   * Where a look_here points (plan §1.4, §1.5, §1.7): a step's card (`<item>`, `<item>:step:<n>` the scene's n-th
   * step, 1-based, or `<item>:term:<n>`: the step holding it), the top line's text, the formula's card or a symbol
   * drawn in it (`<item>:var:<symbol>`, which keeps the formula's group bright). Its box as painted now (read after
   * `update`), on whole stage px, with the radius that puts the outline's curve on the target's corners.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    const v = ctx.view;
    /**
     * @param {Element | null} el @param {Element | null} holder @param {number} radius
     * @param {'text' | 'shape'} kind @param {Element[]} [keep]
     * @returns {FocusRect | null}
     */
    const rectFor = (el, holder, radius, kind, keep = []) => {
      const r = el ? ctx.rectOf(el) : null;
      if (!r || !holder || !(r.w > 0 && r.h > 0)) return null;
      const x0 = Math.round(r.x);
      const y0 = Math.round(r.y);
      return {
        rect: { x: x0, y: y0, w: Math.round(r.x + r.w) - x0, h: Math.round(r.y + r.h) - y0 }, shape: 'rect', radius,
        el: holder, kind, keep,
      };
    };
    const text = String(target || '');
    const id = text.split(':')[0];
    const part = text.split(':')[1] || '';
    const arg = text.split(':').slice(2).join(':');
    // `panel` is a reserved target word (plan §1.7), so no board item can have that id
    if (id === 'panel') return v.panel ? panelFocusRect(ctx, v.panel, text) : null;
    if (part === 'step') {
      const n = Number(arg);
      const s = Number.isInteger(n) && n >= 1 ? v.steps[n - 1] : undefined;
      return s ? rectFor(s.card, s.el, RADIUS.card, 'shape') : null;
    }
    if (part === 'var' && v.card && id === v.card.item.id) {
      if (!v.card.targets.size && v.card.tints.length) v.card.targets = tintTargets(v.card.texEl, v.card.tints);
      const els = /** @type {Element[]} */ (v.card.targets.get(arg) || []);
      const sym = els.length ? rectFor(els[0], v.card.el, SYMBOL_RADIUS, 'shape', [v.card.box]) : null;
      return sym || rectFor(v.card.box, v.card.el, RADIUS.card, 'shape');
    }
    const step = v.steps.find((/** @type {any} */ s) => s.item.id === id);
    if (step) return rectFor(step.card, step.el, RADIUS.card, 'shape');
    if (v.head && v.head.item.id === id) return rectFor(v.head.text, v.head.el, 0, 'text');
    if (v.card && v.card.item.id === id) return rectFor(v.card.box, v.card.el, RADIUS.card, 'shape');
    return null;
  },

  /**
   * The elements holding an item's text, where cue.term finds its [[term]] spans (`.rt-k`, `_shared.js richWords`): a
   * step's text and then its justification (the item's field order, so `<item>:term:<n>` counts across both), the top
   * line's text, or the formula's meaning. Null for anything else.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const v = ctx.view;
    const step = v.steps.find((/** @type {any} */ s) => s.item.id === itemId);
    if (step) return step.why ? [step.text, step.why] : step.text;
    if (v.head && v.head.item.id === itemId) return v.head.text;
    if (v.card && v.card.item.id === itemId && v.card.meaning) return v.card.meaning;
    return null;
  },

  /**
   * The content groups a look_here dims (all but the target's): the top line, every step and the formula's card or
   * the scene's panel; the same elements at every t. This template writes the opacity of what is inside them, never
   * theirs.
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.head ? v.head.el : null, ...v.steps.map((/** @type {any} */ s) => s.el), v.card ? v.card.el : null,
      v.panel ? v.panel.el : null].filter(Boolean);
  },

  /**
   * The shared-element morph's anchor (§4.9 "formula -> worked_steps"; B2f): the formula as drawn, or null without one
   * (or before layout).
   * @param {TemplateContext} ctx
   */
  anchor(ctx) {
    if (isFallback(ctx)) return null;
    const v = ctx.view;
    const r = v.card && v.card.laidOut ? ctx.rectOf(v.card.texEl) : null;
    if (!r || !(r.w > 0 && r.h > 0)) return null;
    const x = Math.round(r.x);
    const y = Math.round(r.y);
    return { el: v.card.texEl, rect: { x, y, w: Math.round(r.x + r.w) - x, h: Math.round(r.y + r.h) - y } };
  },

  /**
   * Static: the box a medium of `slot` is sized for. The only medium this template shows is its panel's, in slot
   * `panel`: 16:9 its whole visual box from the top of the title zone, 9:16 the reel beat's box
   * (`_shared.js reelPanelBox`) - the largest it is ever shown in, so the picture only ever downscales, §P;
   * preflight's fixes cannot enlarge it, list_build's reasoning. A scene it hands to
   * board_generic gets board_generic's box, so `mediaPlan` and `ctx.media` agree.
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
    const b = boxes(zones.format, variantOf(scene, zones.format), zones, true);
    return b.visual ? panelMediaBox(scene, b.visual, slot) : null;
  },

  /**
   * Static: every TeX string this template converts for the scene (`texOf`; board_generic's for a scene it hands on).
   * The panel brings none (a terminal card and a title card are plain text).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} fmt
   */
  tex(scene, look, fmt) {
    if (!drawsScene(scene, fmt)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene, fmt);
  },

  /**
   * The text on screen (preflight, §7.12): the top line (slot `heading`), each step (slot `step`; a 9:16 compact row,
   * slot `row`), each justification (slot `why`), the formula (slot `formula`) and its meaning (slot `meaning`), and
   * the panel's terminal card (slot `terminal`) or title label, in the ink and at the size and weight painted now, on
   * the background's worst case (or on the R-53 scrim over it) or on the card's surface. Every one but a title card's
   * label is critical (R-59, R-53).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
    const onBg = ctx.onBackground;
    /** @type {TextEntry[]} */
    const out = [];
    /** @param {string | null} scrim @param {string} ink */
    const bgOf = (scrim, ink) => (scrim ? scrimmedBg(scrim, onBg, ink) : onBg.bg);
    if (v.head) {
      const hd = v.head;
      out.push({
        el: hd.text, role: 'title', slot: 'heading', critical: true, ink: onBg.ink, bg: bgOf(hd.scrim, onBg.ink),
        alpha: hd.alpha, overflow: hd.overflow, size: hd.size, min: hd.min ?? hd.size, weight: 700,
        text: plainText(String(hd.item.text || '')),
      });
    }
    for (const s of v.steps) {
      const compact = ctx.format === 'portrait' && (s.compactNow || v.allCompact);
      out.push({
        el: s.body, role: 'body', slot: compact ? 'row' : 'step', critical: true, ink: s.ink, bg: bgOf(v.stepScrim, s.ink),
        alpha: s.alpha * (s.typed === undefined ? 1 : s.typed), overflow: compact ? s.overflowCompact : s.overflowLarge,
        size: s.size, min: compact ? v.rowMin ?? s.size : v.stepMin ?? s.size, weight: s.weight,
        text: `${s.k + 1} ${plainText(String(s.item.text || ''))}`,
      });
      if (s.why && !compact) {
        out.push({
          el: s.why, role: 'label', slot: 'why', critical: true, ink: onBg.ink2, bg: bgOf(v.stepScrim, onBg.ink2),
          alpha: s.whyAlpha, overflow: 0, size: s.whySize, min: v.whyMin ?? s.whySize, weight: L.whyWeight,
          text: plainText(String(s.whyText || '')),
        });
      }
    }
    if (v.card) {
      const c = v.card;
      out.push({
        el: c.texEl, role: 'body', slot: 'formula', critical: true, ink: ctx.roles.color('ink'),
        bg: ctx.roles.color('surface'), alpha: c.alpha, overflow: c.overflow, size: c.size, min: c.min, weight: 400,
        text: String(c.item.latex || ''),
      });
      if (c.meaning) {
        out.push({
          el: c.meaning, role: 'label', slot: 'meaning', critical: true, ink: ctx.roles.color('ink2'),
          bg: ctx.roles.color('surface'), alpha: c.alpha, overflow: c.meaningOverflow, size: c.meaningSize,
          min: c.meaningMin ?? c.meaningSize, weight: L.whyWeight, text: plainText(String(c.meaningText || '')),
        });
      }
    }
    // the panel's own text: a terminal card (slot `terminal`, critical) or a title card's label (not critical)
    if (v.panel) out.push(...panelTexts(ctx, v.panel));
    return out;
  },
};

/**
 * When the step after `id` enters (in entrance order): the moment `id` stops being the current step; null for the
 * last.
 * @param {string[]} order @param {Map<string, number>} enter @param {string} id
 * @returns {number | null}
 */
function nextEnter(order, enter, id) {
  const i = order.indexOf(id);
  const next = i >= 0 ? order[i + 1] : undefined;
  if (next === undefined) return null;
  const at = enter.get(next);
  return typeof at === 'number' ? at : null;
}
