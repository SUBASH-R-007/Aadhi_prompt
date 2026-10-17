// @ts-check
/**
 * `outro_next` (DYNAMIC_VIDEO_DESIGN §4.3 A, R1, class `structure`; R-11; §2.3 the lesson and reel blueprints; §2.5
 * end-screen space; B2 plan §1.2 `refs.next`, §1.4 template contract, §1.6 TemplateSpec, §1.11 slots, §P): the closure
 * of a video and its next step ("Part n+1: ..." in a series). Built on the pattern the B2c pilots set
 * (`docs/plans/b2/b2c_design_notes.md`): `_shared.js` for geometry, text and motion, `ctx.paint` for everything per
 * frame, nothing drawn that cues.js owns.
 *
 * Data (§4.3, R-11 "no new content"; templates.py `_fit_outro_next`): a board scene (`content`, `example`, `summary`,
 * `key_takeaway`, `recap`, `title`) with at most ONE board item, a `takeaway`, `paragraph` or `heading` - the
 * **statement** (16:9 the takeaway; 9:16 the loop line that leads back to the opening question, §2.3 "the last spoken
 * line leads back into the opening question, for a seamless loop") - and the next step, `look.refs.next.title` (plan
 * §1.2; the resolver denormalises it: the next part's title in a series). The scene's own title is set small above the
 * statement (the **kicker**), or IS the statement when the scene has no item; a title that repeats the item's text is
 * drawn once. Nothing a timeline names is dropped: a scene with another item, a shown side panel or a medium, or with
 * nothing to show, is handed to `board_generic` whole, every hook with it (`drawsScene`, list_build's convention, B2c
 * review CG-2), and `_fit_outro_next` refuses exactly such a scene. Without `refs.next` there is no next card (nothing
 * is invented): the statement stands alone.
 *
 * Layout (catalogue geometry only: `_shared.js region`, `ctx.zones`; whole stage px; `plan`):
 * - 16:9 `next_up` (§4.3 "takeaway left, 'Next up' right"): the kicker and the statement in `left-55`, the next card
 *   in `right-45`, each centred vertically in the critical box, left-aligned (every template's text alignment); with
 *   only one of the two, it is set in `centre-60` and the text is centred in it (a short closing line left-aligned in
 *   that box reads as off-centre: seen in the reference frames).
 * - 16:9 `end_screen` (§2.5 "the last 15 s keeps a clean right half when end-screen space is on"; the scene is then
 *   exactly `Duration.end_screen` = 15 s long, timeline.py's business): everything in ONE column on the left half,
 *   x 96 to the end-screen box's left edge less a gutter, the next card below the statement; nothing is drawn right of
 *   the end-screen box's edge (`endScreenBox`: the right half of the frame, the catalogue's `end_screen.landscape.box`,
 *   which outro_next.test.js pins), so the platform's end-screen elements land on a clean half.
 * - 9:16 (§4.3 "<= 3 s call to action plus the loop line"): the statement in the hook band (`hook`, x 192-888,
 *   y 340-760: where frame 0 of a reel sets its hook, so the last frame reads into the first, §2.3), centred; the call
 *   to action - the next card - below it, its text exactly in the text column both critical boxes hold (x 192-780,
 *   R-59), clear of the caption band. The card is on screen for at most CTA_MAX = 3 s (R-11) and leaves at least
 *   LOOP_HOLD before the scene ends, so the reel's last frame is the loop line alone.
 * - The next card is `surface` with the card radius, shadow level 2 and a card-width structure border down its left
 *   edge (definition_card's card): the "Next up" label in `ink2`, the next title in `ink`. The kicker and the statement
 *   sit on the background in the block-aware inks the stage hands every template (`ctx.onBackground`, R-53), with
 *   preflight's scrim remedy behind them when it asks for one.
 * - Line heights are the type scale's per script (§4.6, plan §2 R-29: Indic display >= 1.25, body >= 1.45).
 *
 * Fit (`layout`): each text takes the largest size in 4 px steps that keeps its line cap and the height of its column
 * (`hook_question.js fitInBox`, `_shared.js fitLines`' rule with the box's height as a second condition); then, while
 * a column's stack is taller than the column, the statement shrinks first, then the next title, then the kicker.
 * Nothing is ever dropped or faded out to make room: text still past the column's bottom is reported through
 * `texts()` as overflow, so preflight blocks with "Shorten text" (plan §2; formula_focus's and list_build's rule).
 *
 * Motion (§4.3 "the card rises (500 ms)"; §4.8; template durations take the personality's scale, `_shared.js seconds`
 * / `enterAt`): the kicker fades in with the frame; the statement enters on its item's own beat (state.js
 * `itemTimings`: a reveal event wins, else the beat with R-17's 120 ms lead) with the personality's text entrance; the
 * next card rises CARD_RISE px over CARD_ENTER, in 16:9 NEXT_AFTER after the statement starts and in 9:16 with the
 * frame; in 9:16 it leaves by `ctaEnd` (a fall and a fade over CTA_EXIT, §4.8 "exits 30% shorter"), a pure function of
 * the scene's stored duration. The R-11 limits (CTA_MAX, CTA_MIN, LOOP_HOLD) are rule times and are NOT scaled by the
 * personality. Reduced motion (live only): everything fades in place. "The logo resolves" and the music's final phrase
 * (§4.3) are not drawn here: the logo bug is the Aadhi branding derivative (plan §2 "Aadhi"), which no timeline field
 * carries yet, and the music is B3's.
 *
 * Emphasis is cues.js's (§4.2, plan §1.4/§1.5): templates.py `consumes` declares the `reveal` of the item only, so the
 * stage hands this template no cue; `itemEl` / `focusRect` still answer for the statement's item (its `[[term]]`
 * `.rt-k` spans kept by `_shared.js richWords`) and `dimGroups` gives the kicker, the statement and the next card.
 * Nothing here writes outline, text-decoration, filter, backdrop-filter or will-change, and no content element takes a
 * structure role (§4.2): only the card's own left border does.
 *
 * Determinism (§7.4, plan §P): every box comes from the catalogue's zones and regions; `layout` measures the laid-out
 * page only (idempotent); `update` is a pure function of t that paints every property it owns on every frame with a
 * value (never ''), on whole pixels, with 2D translates (`_shared.js paintRise`).
 *
 * Every size and gap here is a B2 proposal pending the owner's review of the reference frames (the design gives the
 * layout, the choreography and the timings): aadhi/compose/dynamic/templates.py mirrors LAYOUT as this template's slots
 * (`slotRows`), and outro_next.test.js pins the two together.
 */

import { h } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { LAYOUT_TOKENS, fixesOf, fx, parseTarget } from '../core.js';
import { itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import { fitInBox } from './hook_question.js';
import {
  RADIUS, SPACE, TEXT_RISE, enterAt, group, paintRise, paintTextEntrance, region, revealMask, richWords, scrimmedBg,
  seconds, slotRange,
} from './_shared.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */

export const ID = 'outro_next';

/** The 16:9 variants (templates.py `variants`; 9:16 has none): `next_up` (§4.3's split) and `end_screen` (§2.5). */
export const VARIANTS = Object.freeze(['next_up', 'end_screen']);
export const DEFAULT_VARIANT = 'next_up';
export const END_SCREEN_VARIANT = 'end_screen';

/** The next card's label (§4.3 "'Next up' right"). English until B4's i18n, as predict's and hook_question's labels. */
export const NEXT_LABEL = 'Next up';

/** The item kinds the statement may be (§4.3 / R-11: closure, no new content; templates.py `_fit_outro_next`). */
export const STATEMENT_KINDS = Object.freeze(['takeaway', 'paragraph', 'heading']);
/** The scene types this template draws: the board scenes (aadhi.schemas.screenplay BoardScene.type). */
export const BOARD_TYPES = Object.freeze(['content', 'example', 'summary', 'key_takeaway', 'recap', 'title']);

// ---------------------------------------------------------------------------------------------
// Motion (§4.3 outro_next, R-11, §4.8), seconds
// ---------------------------------------------------------------------------------------------

/** The kicker fades in with the frame (hook_question's eyebrow). */
export const KICKER_ENTER = 0.35;
/** §4.8 entrance 400-600 ms (the pilots' 450 ms). */
export const TEXT_ENTER = 0.45;
/** §4.3: "the card rises (500 ms)". */
export const CARD_RISE = 24;
export const CARD_ENTER = 0.5;
/** 16:9: the next card follows the statement by this much (the takeaway first, then the step after it). */
export const NEXT_AFTER = 0.4;
/** R-11 (9:16): "<= 3 s call to action". A rule limit, never scaled by the personality. */
export const CTA_MAX = 3.0;
/** ... and never shorter than this (§4.3's 9:16 outro is >= 3 s, so CTA_MIN + LOOP_HOLD fits every outro). */
export const CTA_MIN = 2.0;
/** ... and gone this long before the scene ends, so the reel's last frame is the loop line alone (§2.3 the loop). */
export const LOOP_HOLD = 1.0;
/** The call to action leaves over this (§4.8: exits 30% shorter than the 500 ms entrance) ... */
export const CTA_EXIT = 0.35;
/** ... falling this far as it fades. */
export const CTA_DROP = 24;

// ---------------------------------------------------------------------------------------------
// Layout (stage px; the catalogue's regions, zones, R-29 floors and layout tokens)
// ---------------------------------------------------------------------------------------------

/** Line heights of the type scale (catalogue `type.scale`; plan §2: Indic display >= 1.25, Indic body >= 1.45). */
export const LINE_HEIGHT = Object.freeze({
  display: Object.freeze({ latin: 1.05, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
});
/** The display face's tracking (§4.6 display -0.02..-0.01, as hook_question and definition_card set it). */
export const TRACKING_EM = -0.015;
/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;
/** The next card's left border (the catalogue's `border.card`, 6 px) in the scene's structure border colour. */
export const BORDER_PX = Number(LAYOUT_TOKENS.border.card);

/**
 * Per-format layout (B2 proposals, pending the owner's review). Each text slot: its size range, line cap and weight.
 * `kicker` (the scene title above the statement) and `label` ("Next up") are labels at or above R-29's label floor
 * (40 px in 16:9, 44 px in 9:16); `statement` is set between R-29's 16:9 body and display ranges (64-96: a
 * closing sentence, not a headline) and below the 9:16 hook range (64-88: the loop line reads into the hook's band
 * but is not the hook); `next` is the next step's title (R-29 9:16: at <= 64 px at least weight 600 - every slot here
 * is 600 or 700). `kickerGap`: kicker to statement; `labelGap`: label to next title; `stackGap`: 16:9 end_screen the
 * statement to the card below it, 9:16 the hook band to the call-to-action card; `pad`: the card's padding; `gutter`:
 * 16:9 the end-screen column's distance from the clean half, 9:16 the card's distance from the caption band.
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    kicker: Object.freeze({ max: 48, min: 40, lines: 2, weight: 600 }),
    statement: Object.freeze({ max: 96, min: 64, lines: 5, weight: 700 }),
    label: Object.freeze({ max: 40, min: 40, lines: 1, weight: 600 }),
    next: Object.freeze({ max: 72, min: 56, lines: 3, weight: 700 }),
    kickerGap: SPACE[2],   // 24
    labelGap: SPACE[1],    // 16
    stackGap: SPACE[5],    // 48
    pad: SPACE[5],         // 48
    gutter: SPACE[2],      // 24
  }),
  portrait: Object.freeze({
    kicker: Object.freeze({ max: 48, min: 44, lines: 2, weight: 600 }),
    statement: Object.freeze({ max: 88, min: 64, lines: 4, weight: 700 }),
    label: Object.freeze({ max: 44, min: 44, lines: 1, weight: 600 }),
    next: Object.freeze({ max: 64, min: 56, lines: 2, weight: 700 }),
    kickerGap: SPACE[1],   // 16
    labelGap: SPACE[0],    // 8
    // 8, not 16: the card's room (y 768-1032 less its padding, 216 px) then holds the label and two Indic next lines
    // at the slot's minimum (44 x 1.45 + 8 + 2 x 56 x 1.25 = 212 px; outro_next.test.js pins this budget)
    stackGap: SPACE[0],    // 8
    pad: SPACE[2],         // 24
    gutter: SPACE[0],      // 8
  }),
});

/** The slots each text part reports in (texts(), `look.fixes.slots`, templates.py `_outro_next_slots`). */
export const SLOTS = Object.freeze(['kicker', 'statement', 'label', 'next']);

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/** Indic scripts (Devanagari to Sinhala), whose text takes the taller line heights (plan §2). */
const INDIC = new RegExp(`[${['Devanagari', 'Bengali', 'Gurmukhi', 'Gujarati', 'Oriya', 'Tamil', 'Telugu', 'Kannada',
  'Malayalam', 'Sinhala'].map((x) => `\\p{Script=${x}}`).join('')}]`, 'u');

/**
 * The line height of `text` in type role `role` (LINE_HEIGHT: Indic text takes the Indic value). Pure.
 * @param {'display' | 'body'} role @param {string} text
 */
export function lineHeight(role, text) {
  return INDIC.test(plainText(String(text || ''))) ? LINE_HEIGHT[role].indic : LINE_HEIGHT[role].latin;
}

/**
 * The next step's title from a look (`refs.next.title`, plan §1.2; the resolver denormalises it), '' without one or
 * when it is malformed.
 * @param {any} look
 */
export function nextOf(look) {
  const refs = look && typeof look === 'object' ? look.refs : null;
  const next = refs && typeof refs === 'object' ? refs.next : null;
  const title = next && typeof next === 'object' ? next.title : null;
  return typeof title === 'string' ? title.trim() : '';
}

/** @param {string} a @param {string} b */
const sameText = (a, b) => plainText(a).replace(/\s+/g, ' ').trim().toLocaleLowerCase()
  === plainText(b).replace(/\s+/g, ' ').trim().toLocaleLowerCase();

/**
 * What the outro shows (pure): the statement's `item` (the scene's takeaway, paragraph or heading with text; null
 * when the scene title is the statement), the `statement` itself, the `kicker` (the scene title above it; '' when the
 * title is the statement, is empty or repeats the item's text) and the `next` step's title ('' without
 * `look.refs.next`).
 * @param {TimedScene} scene
 * @param {any} look
 * @returns {{item: BoardItem | null, statement: string, kicker: string, next: string}}
 */
export function outroText(scene, look) {
  const title = String(scene.title || '').trim();
  const item = (scene.board || []).find((i) => STATEMENT_KINDS.includes(String(i.kind))
    && typeof i.text === 'string' && i.text.trim()) || null;
  const next = nextOf(look);
  if (!item) return { item: null, statement: title, kicker: '', next };
  const statement = String(item.text).trim();
  return { item, statement, kicker: title && !sameText(title, statement) ? title : '', next };
}

/**
 * The scene's side panel when the layout shows it (null without one): this template draws none.
 * @param {TimedScene} scene
 */
export function shownPanel(scene) {
  const sp = scene.side_panel;
  return sp && scene.layout?.show_side_panel !== false ? sp : null;
}

/**
 * Whether this template draws the scene whole (else board_generic does, every hook handed on). templates.py
 * `_fit_outro_next` refuses every scene this refuses (B2c review CG-2): a board scene with at most one item, a
 * takeaway, paragraph or heading; no shown side panel, scene medium, quiz or poster; and something to show - the
 * statement (the item's text or the scene title) or the next step. The same in both formats.
 * @param {TimedScene} scene
 * @param {'landscape' | 'portrait'} [_format]
 */
export function drawsScene(scene, _format) {
  if (!BOARD_TYPES.includes(String(scene.type))) return false;
  const board = scene.board || [];
  if (board.length > 1 || board.some((i) => !STATEMENT_KINDS.includes(String(i.kind)))) return false;
  if (shownPanel(scene) || scene.media || scene.quiz || scene.poster) return false;
  const text = outroText(scene, scene.look);
  return !!(text.statement || text.next);
}

/**
 * The variant drawn: in 16:9 the look's own when §4.3 / templates.py name it, else `next_up`; 9:16 has none ('').
 * @param {any} look @param {'landscape' | 'portrait'} format
 */
export function variantOf(look, format) {
  if (format === 'portrait') return '';
  const v = look && typeof look === 'object' ? String(look.variant) : '';
  return VARIANTS.includes(v) ? v : DEFAULT_VARIANT;
}

/**
 * The end-screen box of a 16:9 frame (pure): the right half, which stays clean while end-screen space is on (§2.5).
 * Derived from the stage's own zones, so the template reads no catalogue value of its own; outro_next.test.js pins it
 * to the catalogue's `end_screen.landscape.box` (zones.py END_SCREEN).
 * @param {Zones} zones
 * @returns {Box}
 */
export function endScreenBox(zones) {
  return { x0: Math.round(zones.width / 2), y0: 0, x1: zones.width, y1: zones.height };
}

/**
 * Where the parts go (pure; whole stage px): the layout `mode` and its `columns`, each a box, a vertical alignment and
 * the blocks stacked in it in order (`text`: the kicker and the statement; `card`: the next card), plus each block's
 * `width`. `shows`: whether the scene has a statement and a next step.
 * - 16:9 `next_up` with both: `split` (text in `left-55`, card in `right-45`); with one: `centred` (`centre-60`).
 * - 16:9 `end_screen`: one column from the critical box's left edge to the end-screen box's left edge less the gutter.
 * - 9:16 `reel`: the text centred in the hook band; the card top-aligned below it, grown around the text column by its
 *   padding and border (so the card's text sits exactly in x 192-780), ending the gutter above the caption band.
 * @param {'landscape' | 'portrait'} format
 * @param {string} variant
 * @param {{text: boolean, card: boolean}} shows
 * @param {Zones} zones
 * @returns {{mode: 'split' | 'centred' | 'end_screen' | 'reel', columns: {box: Box, align: 'center' | 'top', blocks: ('text' | 'card')[]}[]}}
 */
export function plan(format, variant, shows, zones) {
  const at = { format };
  /** @type {('text' | 'card')[]} */
  const both = [];
  if (shows.text) both.push('text');
  if (shows.card) both.push('card');
  if (format === 'portrait') {
    const L = LAYOUT.portrait;
    const hook = region(at, 'hook');
    const col = zones.textColumn;
    /** @type {{box: Box, align: 'center' | 'top', blocks: ('text' | 'card')[]}[]} */
    const columns = [];
    if (shows.text) columns.push({ box: hook, align: 'center', blocks: ['text'] });
    if (shows.card) {
      columns.push({
        box: { x0: col.x0 - L.pad - BORDER_PX, y0: hook.y1 + L.stackGap, x1: col.x1 + L.pad, y1: zones.caption.y0 - L.gutter },
        align: 'top', blocks: ['card'],
      });
    }
    return { mode: 'reel', columns };
  }
  const L = LAYOUT.landscape;
  if (variant === END_SCREEN_VARIANT) {
    const full = region(at, 'full');
    return { mode: 'end_screen', columns: [{ box: { ...full, x1: endScreenBox(zones).x0 - L.gutter }, align: 'center', blocks: both }] };
  }
  if (shows.text && shows.card) {
    return {
      mode: 'split',
      columns: [{ box: region(at, 'left-55'), align: 'center', blocks: ['text'] },
        { box: region(at, 'right-45'), align: 'center', blocks: ['card'] }],
    };
  }
  return { mode: 'centred', columns: [{ box: region(at, 'centre-60'), align: 'center', blocks: both }] };
}

/**
 * The width text is set in inside a column `box` (pure): the column's own width for the kicker and the statement, the
 * card's inner width (less its padding on both sides and its left border) for the label and the next title.
 * @param {'landscape' | 'portrait'} format @param {Box} box @param {'text' | 'card'} block
 */
export function textWidth(format, box, block) {
  const w = box.x1 - box.x0;
  return block === 'card' ? w - 2 * LAYOUT[format].pad - BORDER_PX : w;
}

/**
 * This template's text slots (templates.py SlotSpec, what textfit.py fits in Python; plan §1.6, §1.11):
 * slot -> format -> variant -> {w, max_lines, size, role, face, weight, line_height, tracking_em, prefer_lines,
 * critical}. 16:9 lists both variants, and `default` is the narrowest box each slot is ever set in (the end-screen
 * column for the kicker and the statement; the split's card for the label and the next title), so a size stored
 * without knowing the variant is an upper bound for every layout (`centred` is wider than both). 9:16 has one layout.
 * Line heights are the Latin ones (Indic text takes LINE_HEIGHT's Indic values, which textfit.py's Indic measure
 * uses). Pure.
 * @param {Zones} landscape @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  const both = { text: true, card: true };
  /** @param {'landscape' | 'portrait'} fmt @param {string} variant @param {Zones} zones @param {'text' | 'card'} block */
  const width = (fmt, variant, zones, block) => {
    const p = plan(fmt, variant, both, zones);
    const col = /** @type {{box: Box}} */ (p.columns.find((c) => c.blocks.includes(block)));
    return textWidth(fmt, col.box, block);
  };
  /** @param {{max: number, min: number, lines: number, weight: number}} s @param {number} w @param {'title' | 'label'} role */
  const spec = (s, w, role) => ({
    w, max_lines: s.lines, size: [s.min, s.max], role, face: role === 'title' ? 'display' : 'body', weight: s.weight,
    line_height: role === 'title' ? LINE_HEIGHT.display.latin : LINE_HEIGHT.body.latin,
    tracking_em: role === 'title' ? TRACKING_EM : 0, prefer_lines: null, critical: true,
  });
  /** @type {Record<string, Record<string, Record<string, any>>>} */
  const out = {};
  for (const [slot, block, role] of /** @type {const} */ ([['kicker', 'text', 'label'], ['statement', 'text', 'title'],
    ['label', 'card', 'label'], ['next', 'card', 'title']])) {
    const L = LAYOUT.landscape[slot];
    const P = LAYOUT.portrait[slot];
    const nextUp = width('landscape', DEFAULT_VARIANT, landscape, block);
    const endScreen = width('landscape', END_SCREEN_VARIANT, landscape, block);
    out[slot] = {
      landscape: {
        default: spec(L, Math.min(nextUp, endScreen), role),
        [DEFAULT_VARIANT]: spec(L, nextUp, role),
        [END_SCREEN_VARIANT]: spec(L, endScreen, role),
      },
      portrait: { default: spec(P, width('portrait', '', portrait, block), role) },
    };
  }
  return out;
}

/**
 * When the 9:16 call to action is gone (pure, scene-relative seconds): R-11's CTA_MAX after the scene starts at the
 * latest, LOOP_HOLD before the scene ends at the latest, never before CTA_MIN (and never past the scene's end).
 * @param {number} duration
 */
export function ctaEnd(duration) {
  const d = Number(duration) > 0 ? Number(duration) : 0;
  return Math.min(d, Math.max(CTA_MIN, Math.min(CTA_MAX, d - LOOP_HOLD)));
}

/**
 * Every TeX string the template renders for the scene (the static `tex` hook; render.js converts them before `ready`):
 * the `$...$` spans of the kicker, the statement and the next title, inline (`richWords` mounts each with display off).
 * @param {TimedScene} scene
 * @param {any} look
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene, look) {
  const { kicker, statement, next } = outroText(scene, look);
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  for (const text of [kicker, statement, next]) {
    for (const tex of mathSpans(String(text || ''))) if (tex.trim()) out.push({ tex, display: false });
  }
  return out;
}

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.onFallback);

/**
 * One text part (the kicker, the statement, the label, the next title).
 * @typedef {object} Part
 * @property {string} slot
 * @property {'title' | 'label'} role
 * @property {HTMLElement} el          the text element (what preflight measures)
 * @property {HTMLElement} box         the element stacked in its block (the reveal mask, or the text itself)
 * @property {HTMLElement} groupEl     its dim group
 * @property {HTMLElement | null} mask
 * @property {HTMLElement[]} words
 * @property {boolean[]} tex
 * @property {number} lh
 * @property {number} lines
 * @property {{min: number, max: number}} range
 * @property {number} size
 * @property {number} lineOverflow
 * @property {number} overflow
 * @property {number} alpha
 * @property {number} weight
 * @property {string} ink
 * @property {string} text
 */

/**
 * A text element for one part (build time; the size is set by `layout`).
 * @param {TemplateContext} ctx @param {'div' | 'h2'} tag @param {string} cls @param {string} text
 * @param {'display' | 'body'} face @param {number} weight @param {string} ink @param {boolean} [nowrap]
 */
function textEl(ctx, tag, cls, text, face, weight, ink, nowrap = false) {
  const lh = lineHeight(face, text);
  const el = h(tag, {
    class: cls, dir: 'auto',
    style: {
      margin: '0px', color: ink, fontFamily: face === 'display' ? 'var(--dyn-font-display)' : 'var(--dyn-font-body)',
      fontWeight: String(weight), lineHeight: String(lh), letterSpacing: face === 'display' ? `${TRACKING_EM}em` : '0em',
      overflowWrap: 'anywhere', whiteSpace: nowrap ? 'nowrap' : 'normal',
    },
  });
  const { words, tex } = richWords(ctx, el, text);
  return { el, words, tex, lh };
}

/** @type {TemplateModule} */
export default {
  id: ID,

  /**
   * The DOM: the text block (kicker and statement, each in its dim group, in one holder that carries preflight's
   * scrim) and the next card (in its dim group). No measuring here.
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format, roles } = ctx;
    if (!drawsScene(scene, format)) {
      // another item, a panel or a medium, or nothing to show: board_generic draws the scene whole (review CG-2)
      const inner = boardGeneric.build(ctx);
      inner.onFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const text = outroText(scene, ctx.look);
    const variant = variantOf(ctx.look, format);
    const shows = { text: !!text.statement, card: !!text.next };
    const layoutPlan = plan(format, variant, shows, ctx.zones);
    const fixes = fixesOf(ctx.look);
    const masked = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    const onInk = ctx.onBackground.ink;
    const onInk2 = ctx.onBackground.ink2;
    const root = h('div', {
      class: ['dyn-on', `dyn-on--${format}`, `dyn-on--${layoutPlan.mode}`],
      style: { position: 'absolute', left: '0px', top: '0px', width: `${ctx.zones.width}px`, height: `${ctx.zones.height}px` },
    });
    const enter = new Map(itemTimings(scene).map((x) => [x.id, x.enter]));
    const statementAt = text.item ? Math.max(0, Number(enter.get(text.item.id)) || 0) : 0;

    // the text block: the kicker above the statement, on the background (R-53's scrim behind both when asked)
    /** @type {any} */
    let textBlock = null;
    if (shows.text) {
      /** @type {Part[]} */
      const parts = [];
      if (text.kicker) {
        const k = textEl(ctx, 'div', 'dyn-on-kicker', text.kicker, 'body', L.kicker.weight, onInk2);
        const g = group('dyn-on-kicker-group', k.el);
        parts.push({
          slot: 'kicker', role: 'label', el: k.el, box: k.el, groupEl: g, mask: null, words: k.words, tex: k.tex,
          lh: k.lh, lines: L.kicker.lines, range: { min: L.kicker.min, max: L.kicker.max }, size: L.kicker.max,
          lineOverflow: 0, overflow: 0, alpha: 0, weight: L.kicker.weight, ink: onInk2, text: plainText(text.kicker),
        });
      }
      const s = textEl(ctx, 'h2', 'dyn-on-statement', text.statement, 'display', L.statement.weight, onInk);
      const mask = masked ? revealMask(s.el) : null;
      const g = group('dyn-on-statement-group', mask || s.el);
      parts.push({
        slot: 'statement', role: 'title', el: s.el, box: mask || s.el, groupEl: g, mask, words: s.words, tex: s.tex,
        lh: s.lh, lines: L.statement.lines, range: { min: L.statement.min, max: L.statement.max }, size: L.statement.max,
        lineOverflow: 0, overflow: 0, alpha: 0, weight: L.statement.weight, ink: onInk, text: plainText(text.statement),
      });
      const scrim = ['kicker', 'statement'].some((x) => fixes.slots[x] && fixes.slots[x].scrim) ? roles.color('scrim') : null;
      const holder = h('div', {
        class: 'dyn-on-text',
        style: { position: 'absolute', boxSizing: 'content-box', textAlign: layoutPlan.mode === 'centred' ? 'center' : 'left' },
      }, ...parts.map((p) => p.groupEl));
      if (scrim) {
        Object.assign(holder.style, {
          padding: `${SPACE[1]}px ${SPACE[2]}px`, borderRadius: `${RADIUS.card}px`, background: scrim,
        });
      }
      root.appendChild(holder);
      textBlock = { holder, parts, scrim, gap: L.kickerGap, top: 0 };
    }

    // the next card: "Next up" and the next step's title on the card surface
    /** @type {any} */
    let cardBlock = null;
    if (shows.card) {
      const ink = roles.color('ink');
      const ink2 = roles.color('ink2');
      const surface = roles.color('surface');
      const card = h('div', {
        class: 'dyn-on-card',
        style: {
          position: 'relative', boxSizing: 'border-box', width: '100%', borderRadius: `${RADIUS.card}px`,
          background: surface, padding: `${L.pad}px`, boxShadow: LAYOUT_TOKENS.shadow['2'],
        },
      });
      // the card's structure border goes on after it is made, so no content word shares the statement with the
      // structure role (§4.2, the source scan's struct-role rule; definition_card's card)
      const structBorder = roles.color('struct.border');
      card.style.borderLeft = `${BORDER_PX}px solid ${structBorder}`;
      const lab = textEl(ctx, 'div', 'dyn-on-label', NEXT_LABEL, 'body', L.label.weight, ink2, true);
      const nx = textEl(ctx, 'div', 'dyn-on-next', text.next, 'display', L.next.weight, ink);
      nx.el.style.marginTop = `${L.labelGap}px`;
      card.appendChild(lab.el);
      card.appendChild(nx.el);
      const g = group('dyn-on-card-group', card);
      g.style.position = 'absolute';
      root.appendChild(g);
      /** @type {Part[]} */
      const parts = [
        {
          slot: 'label', role: 'label', el: lab.el, box: lab.el, groupEl: g, mask: null, words: lab.words, tex: lab.tex,
          lh: lab.lh, lines: L.label.lines, range: { min: L.label.min, max: L.label.max }, size: L.label.max,
          lineOverflow: 0, overflow: 0, alpha: 0, weight: L.label.weight, ink: ink2, text: NEXT_LABEL,
        },
        {
          slot: 'next', role: 'title', el: nx.el, box: nx.el, groupEl: g, mask: null, words: nx.words, tex: nx.tex,
          lh: nx.lh, lines: L.next.lines, range: { min: L.next.min, max: L.next.max }, size: L.next.max,
          lineOverflow: 0, overflow: 0, alpha: 0, weight: L.next.weight, ink, text: plainText(text.next),
        },
      ];
      const reel = layoutPlan.mode === 'reel';
      const at = reel ? 0 : shows.text ? statementAt + seconds(ctx, NEXT_AFTER) : 0;
      const end = reel ? ctaEnd(scene.duration) : null;
      cardBlock = { card, groupEl: g, parts, surface, enter: at, end, alpha: 0, top: 0 };
    }

    ctx.root.appendChild(root);
    return { root, mode: layoutPlan.mode, plan: layoutPlan, variant, text, textBlock, cardBlock, statementAt };
  },

  /**
   * Measure and fit (idempotent: every pass starts from the slot ranges): each text at the largest 4 px size that
   * keeps its line cap and its column's height; then each column's stack shrunk (the statement, then the next title,
   * then the kicker) while it is taller than the column; then the blocks placed, centred (or top-aligned) in their
   * column, and what still passes the column's bottom recorded as overflow. `update` only paints.
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    if (isFallback(ctx)) {
      if (typeof boardGeneric.layout === 'function') boardGeneric.layout(ctx);
      return;
    }
    const v = ctx.view;
    const { format } = ctx;
    const L = LAYOUT[format];
    for (const column of v.plan.columns) {
      const box = /** @type {Box} */ (column.box);
      const colW = box.x1 - box.x0;
      const colH = box.y1 - box.y0;
      const blocks = column.blocks.map((/** @type {string} */ b) => (b === 'text' ? v.textBlock : v.cardBlock));
      // widths first, so the fit measures the lines the page will draw
      for (const b of blocks) {
        if (b === v.textBlock) b.holder.style.width = `${colW}px`;
        else Object.assign(b.groupEl.style, { left: `${box.x0}px`, width: `${colW}px` });
      }
      /** @type {Part[]} */
      const parts = blocks.flatMap((/** @type {any} */ b) => b.parts);
      // the most a single part could ever take: the column, less the gap to the other block and the card's padding
      const chrome = blocks.length > 1 ? L.stackGap : 0;
      for (const p of parts) {
        const range = slotRange(ctx, p.slot, { min: p.range.min, max: p.range.max, role: p.role });
        p.range = range;
        const inCard = !!v.cardBlock && v.cardBlock.parts.includes(p);
        const room = Math.max(1, colH - chrome - (inCard ? 2 * L.pad : 0));
        const fit = fitInBox(p.el, { max: range.max, min: range.min, lines: p.lines, lh: p.lh, step: SIZE_STEP },
          p.words, p.tex, room);
        p.size = fit.size;
        p.lineOverflow = fit.overflow;
        if (p.slot === 'label') {
          // one line that never wraps: px it runs past the card (never clipped silently, §7.12)
          const wide = p.el.scrollWidth - p.el.clientWidth;
          if (wide > 1) p.lineOverflow = Math.max(p.lineOverflow, wide);
        }
      }
      const blockHeight = (/** @type {any} */ b) => (b === v.textBlock
        ? b.parts.reduce((/** @type {number} */ a, /** @type {Part} */ p, /** @type {number} */ i) => a + (i ? b.gap : 0) + p.box.offsetHeight, 0)
        : b.card.offsetHeight);
      for (const b of blocks) {
        if (b === v.textBlock) b.parts.forEach((/** @type {Part} */ p, /** @type {number} */ i) => { p.box.style.marginTop = i ? `${b.gap}px` : '0px'; });
      }
      const total = () => blocks.reduce((/** @type {number} */ a, /** @type {any} */ b, /** @type {number} */ i) => a + (i ? L.stackGap : 0) + blockHeight(b), 0);
      // too tall: the statement gives way first, then the next title, then the kicker (the label is fixed)
      const order = ['statement', 'next', 'kicker'].map((s) => parts.find((p) => p.slot === s)).filter(Boolean);
      for (let guard = 0; guard < 200 && total() > colH; guard++) {
        const next = /** @type {Part | undefined} */ (order.find((p) => p && p.size > p.range.min));
        if (!next) break;
        next.size = Math.max(next.range.min, next.size - SIZE_STEP);
        next.el.style.fontSize = `${next.size}px`;
      }
      // the blocks' places: centred in the column (never above it) or top-aligned; then each part's overflow past the
      // column's bottom, reported through texts(), never clipped
      const height = total();
      let y = column.align === 'center' ? box.y0 + Math.max(0, Math.round((colH - height) / 2)) : box.y0;
      blocks.forEach((/** @type {any} */ b, /** @type {number} */ i) => {
        if (i) y += L.stackGap;
        b.top = Math.round(y);
        if (b === v.textBlock) {
          const pad = b.scrim ? [SPACE[1], SPACE[2]] : [0, 0];
          Object.assign(b.holder.style, { left: `${box.x0 - pad[1]}px`, top: `${b.top - pad[0]}px` });
          let py = b.top;
          b.parts.forEach((/** @type {Part} */ p, /** @type {number} */ k) => {
            if (k) py += b.gap;
            const bottom = py + p.box.offsetHeight;
            p.overflow = Math.max(p.lineOverflow, Math.max(0, Math.round(bottom - box.y1)));
            py = bottom;
          });
        } else {
          b.groupEl.style.top = `${b.top}px`;
          const bottom = b.top + b.card.offsetHeight;
          const past = Math.max(0, Math.round(bottom - box.y1));
          // what passes the bottom is the card's last line: the next title reports it (the label sits above it)
          for (const p of b.parts) p.overflow = Math.max(p.lineOverflow, p.slot === 'next' ? past : 0);
        }
        y += blockHeight(b);
      });
    }
  },

  /**
   * Paint scene time t: the kicker's fade, the statement's entrance on its beat, the next card's rise (and in 9:16 its
   * exit by `ctaEnd`). A pure function of t: every property painted is painted on every frame, with a value.
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
    if (v.textBlock) {
      for (const part of /** @type {Part[]} */ (v.textBlock.parts)) {
        if (part.slot === 'kicker') {
          const e = enterAt(ctx, t, 0, KICKER_ENTER);
          part.alpha = e;
          paintRise(ctx, part.el, e, TEXT_RISE);
          p.style(part.el, 'visibility', e > 0.001 ? 'visible' : 'hidden');
        } else {
          const e = enterAt(ctx, t, v.statementAt, TEXT_ENTER);
          part.alpha = e;
          paintTextEntrance(ctx, part.el, part.mask, e);
          p.style(part.box, 'visibility', e > 0.001 ? 'visible' : 'hidden');
        }
      }
    }
    if (v.cardBlock) {
      const c = v.cardBlock;
      const eIn = enterAt(ctx, t, c.enter, CARD_ENTER);
      const out = c.end === null ? 0 : ease('ease.exit', progress(t, c.end - seconds(ctx, CTA_EXIT), seconds(ctx, CTA_EXIT)));
      const a = eIn * (1 - out);
      c.alpha = a;
      for (const part of c.parts) part.alpha = a;
      const dy = ctx.reducedMotion ? 0 : Math.round((1 - eIn) * CARD_RISE + out * CTA_DROP);
      p.style(c.card, 'opacity', fx(a));
      p.style(c.card, 'transform', dy ? `translate(0, ${dy}px)` : 'none');
      p.style(c.card, 'visibility', a > 0.001 ? 'visible' : 'hidden');
    }
  },

  /**
   * Where a look_here would point (plan §1.4, §1.7; templates.py declares none for this template, so the stage does not
   * ask today): the statement's own text box for its item (`<item>`, `<item>:term:<n>`), as painted now, on whole stage
   * px. Null for anything else, for a statement that is the scene title, or for one not on screen yet.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    const v = ctx.view;
    const parsed = parseTarget(target);
    if (!parsed || !(parsed.form === 'item' || parsed.form === 'item:term') || !v.text.item || parsed.item !== v.text.item.id) return null;
    const part = v.textBlock ? v.textBlock.parts.find((/** @type {Part} */ x) => x.slot === 'statement') : null;
    if (!part || !(part.alpha > 0.01)) return null;
    const r = ctx.rectOf(part.el);
    if (!r || !(r.w > 0 && r.h > 0)) return null;
    const x0 = Math.round(r.x);
    const y0 = Math.round(r.y);
    return {
      rect: { x: x0, y: y0, w: Math.round(r.x + r.w) - x0, h: Math.round(r.y + r.h) - y0 },
      shape: 'rect', radius: 0, el: part.groupEl, kind: 'text',
    };
  },

  /**
   * The element holding an item's text, where cue.term finds its `[[term]]` spans (`.rt-k`, `_shared.js richWords`):
   * the statement, for its item. Null for anything else.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const v = ctx.view;
    if (!v.text.item || String(itemId) !== v.text.item.id || !v.textBlock) return null;
    const part = v.textBlock.parts.find((/** @type {Part} */ x) => x.slot === 'statement');
    return part ? part.el : null;
  },

  /**
   * The content groups a look_here dims (all but the target's): the kicker, the statement and the next card - the same
   * elements at every t; this template never writes their opacity (it animates their children).
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [...(v.textBlock ? v.textBlock.parts.map((/** @type {Part} */ x) => x.groupEl) : []),
      v.cardBlock ? v.cardBlock.groupEl : null].filter(Boolean);
  },

  /**
   * The morph anchor (plan §1.4): none - the outro is the video's last scene, so nothing continues it.
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: no medium (an outro shows none; a scene with one is board_generic's, whose box it keeps).
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
    return null;
  },

  /**
   * Static: every TeX string the template renders for the scene (`texOf`; the same in both formats).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} fmt
   */
  tex(scene, look, fmt) {
    if (!drawsScene(scene, fmt)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, fmt) : [];
    return texOf(scene, look);
  },

  /**
   * The text on screen (preflight, §7.12), all critical, at the size, weight and alpha painted now: the kicker (slot
   * `kicker`, `ink2`) and the statement (`statement`, `ink`) on the background's worst case or on the R-53 scrim over
   * it; the label (`label`, `ink2`) and the next title (`next`, `ink`) on the card's `surface`.
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    /** @type {TextEntry[]} */
    const out = [];
    /** @param {Part} part @param {string | null} bg */
    const entry = (part, bg) => ({
      el: part.el, role: part.role, slot: part.slot, critical: true, ink: part.ink, bg, alpha: part.alpha,
      overflow: part.overflow, size: part.size, min: part.range.min, weight: part.weight, text: part.text,
    });
    if (v.textBlock) {
      for (const part of /** @type {Part[]} */ (v.textBlock.parts)) {
        out.push(entry(part, v.textBlock.scrim ? scrimmedBg(v.textBlock.scrim, ctx.onBackground, part.ink) : ctx.onBackground.bg));
      }
    }
    if (v.cardBlock) {
      for (const part of /** @type {Part[]} */ (v.cardBlock.parts)) out.push(entry(part, v.cardBlock.surface));
    }
    return out;
  },
};
