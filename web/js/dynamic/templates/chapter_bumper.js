// @ts-check
/**
 * `chapter_bumper` (DYNAMIC_VIDEO_DESIGN §4.3 A, R1, class `structure`; B2 plan §1.4 template contract, §1.6
 * TemplateSpec, §2; deviation 14): the boundary marker of a chapter or act, and the only scene where the structure
 * accent changes (R-18, R-52). Built on the pattern the B2c pilots set (`docs/plans/b2/b2c_design_notes.md`):
 * `_shared.js` for geometry, fitting and motion, `ctx.paint` for everything per frame, nothing drawn of its own that
 * cues.js owns.
 *
 * Data (§4.3, templates.py `_fit_chapter_bumper`): a `ChapterCardScene` (`TimedScene.chapter_label` and `title`; it
 * may be silent, so nothing here waits for a beat). The scene's chapter number is the digits of `chapter_label`
 * ("Part 2" -> "02"), else the 1-based place of the `current` entry of the mini-roadmap (`look.refs.roadmap`); the
 * label's remaining words ("Part") are the kicker above it, so no word of the label is dropped (plan §1.4 "never
 * drop content"): a label with no digits at all is drawn whole as the kicker and the scene has no numeral. A scene
 * that carries board items with text, or has neither a title nor a label, is not this template's (`drawsScene`):
 * board_generic draws it whole and every hook is handed on, list_build's convention.
 *
 * The background is the look's (`colour_block` for a chapter card, presentation.py `_background_preset`): this
 * template draws no block of its own, and every text takes the block inks the stage hands it
 * (`ctx.onBackground.ink` / `ink2`, the look's stored `block.ink` / `block.ink2` tokens, R-53).
 *
 * 16:9 (§4.3): the kicker, the outline numeral at 320 px and the title at 96 px set left in the `full` region, with
 * the mini-roadmap of all chapters along the bottom (`look.refs.roadmap`, filled from B2e: past chapters ticked, the
 * current one lit, the rest faint). 9:16: numeral 240 px, everything centred in the upper critical box
 * (`text-above`), no roadmap (the column is too narrow for one, and the caption band takes the lower box).
 *
 * The numeral is DECORATION, not R-29 type (deviation 14 "bumper numeral = decorative outline role outside R-29"):
 * its glyphs are a TINT of the block ink (NUMERAL_FILL_ALPHA) and `texts()` reports it as not critical, like
 * board_generic's kicker. §4.3 words it "outline numeral", and it was drawn with a `-webkit-text-stroke` until the
 * B2-demo review (D4): in HTML that stroke is painted OVER the fill, so for a display-face glyph whose outline
 * self-overlaps (the "1" of "01" is built from overlapping rectangles) every internal join was stroked too and the
 * numeral read as a grid of boxes at 320 px. A tint has no joins to draw; an outline would need SVG `<text>` with
 * `paint-order: stroke fill` over an opaque fill, which would paint a block-coloured patch over the background.
 *
 * Choreography (§4.3, §4.8): the colour-wipe into the bumper is B2f's (R-54, deviation 10) — until it lands the
 * scene is entered by the look's crossfade or cut, which the stage paints. Here: the numeral slides 40 px in 500 ms
 * (the kicker with it), the title follows 120 ms later with the personality's text entrance, the roadmap fades in and
 * the current chapter's node lights last. Template durations take the personality's scale (`_shared.js seconds` /
 * `enterAt`); cue timings never do (cues.js), and this template consumes no cue at all (templates.py `consumes`): a
 * bumper has no board item a `cue_term` or `look_here` could name, so `itemEl` and `focusRect` answer null.
 *
 * Determinism (§7.4, plan §P): every box is placed at build time from the catalogue's zones and regions (no
 * measurement in `update`), `layout` only fits the text (idempotent: it reads the laid-out page), `update` is a pure
 * function of t and writes every property it owns on every frame with a value (never ''), and translates land on
 * whole pixels (`paintRise`).
 */

import { h } from '../../shared/dom.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { mathSpans, plainText } from '../../player/richtext.js';
import { icon } from '../../player/icons.js';
import { fixesOf, fx } from '../core.js';
import {
  RADIUS, SPACE, TEXT_RISE, enterAt, fitLines, group, paintRise, paintTextEntrance, region, revealMask, richWords,
  scrimmedBg, seconds, slotRange,
} from './_shared.js';
import boardGeneric from './board_generic.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../core.js').Box} Box */
/** @typedef {import('../core.js').Zones} Zones */

// ---------------------------------------------------------------------------------------------
// Motion (§4.3 chapter_bumper, §4.8)
// ---------------------------------------------------------------------------------------------

/** §4.3: "the numeral slides 40 px (500 ms)" (the kicker enters with it, at the text rise). */
export const NUMERAL_RISE = 40;
export const NUMERAL_ENTER = 0.5;
/** ... "title 120 ms later" (the personality's text entrance, §4.8). */
export const TITLE_DELAY = 0.12;
export const TITLE_ENTER = 0.5;
/** The mini-roadmap fades in once the numeral is on its way (§4.8 "medium move 400-500 ms"). */
export const ROADMAP_DELAY = 0.24;
export const ROADMAP_ENTER = 0.45;
/** ... "the node lights": the current chapter's node ramps to full after the title has landed (§4.8 small move). */
export const LIGHT_AT = 0.72;
export const LIGHT = 0.3;

// ---------------------------------------------------------------------------------------------
// Layout (stage px; the catalogue's regions, R-29 floors, deviation 14 for the numeral)
// ---------------------------------------------------------------------------------------------

/** Line heights of the type scale (§4.6, plan §2: Indic display / heading >= 1.25); the numeral is set solid (1.0,
 * the display role's Latin minimum), as board_generic's title is at 1.06. chapter_bumper.test.js pins them to the
 * catalogue's `type.scale`. */
export const LINE_HEIGHT = Object.freeze({
  title: Object.freeze({ latin: 1.06, indic: 1.25 }),
  kicker: Object.freeze({ latin: 1.2, indic: 1.45 }),
  numeral: 1.0,
});

/**
 * Per-format layout. `kicker`: the label's size (R-29 label floor: 40 px in 16:9, 44 in 9:16), one line, cut with an
 * ellipsis as board_generic's kicker is. `numeral`: the decorative numeral's size (deviation 14; §4.3 "320 px" /
 * "240 px"). `title`: R-29 headings 84-120 capped at board_generic's 96 in 16:9, board_generic's
 * 56-88 in 9:16, at most 2 lines (one preferred). `top`: the first element's top inside the content box; `kickerGap`
 * / `titleGap`: below the kicker and below the numeral; `align`: the block's horizontal alignment (§4.3: 9:16 is
 * centred). `pip` / `pipGap` / `pipIcon` / `roadmapGap`: the mini-roadmap's node height, the gap between nodes, the
 * tick inside a done node and the gap above the row (16:9 only; `pip` null: the format draws no roadmap).
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    kicker: Object.freeze({ max: 40, min: 40, lines: 1 }),
    numeral: Object.freeze({ size: 320 }),
    title: Object.freeze({ max: 96, min: 84, lines: 2, prefer: 1 }),
    top: 64,
    kickerGap: 8,
    titleGap: 24,
    align: 'left',
    pip: 48,
    pipGap: 8,
    pipIcon: 32,
    roadmapGap: 40,
  }),
  portrait: Object.freeze({
    kicker: Object.freeze({ max: 44, min: 44, lines: 1 }),
    numeral: Object.freeze({ size: 240 }),
    title: Object.freeze({ max: 88, min: 56, lines: 2, prefer: 1 }),
    // the 9:16 stack is tight: with these gaps the title's box (y 609-840) still holds two Indic lines at 88 px
    // (1.25 line height: 220 px), the binding case of the fit
    top: 4,
    kickerGap: 8,
    titleGap: 16,
    align: 'center',
    pip: null,
    pipGap: 0,
    pipIcon: 0,
    roadmapGap: 0,
  }),
});

/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;
/** The numeral's glyphs are a tint of the block ink this faint (decoration; never a content ink, §4.2). Raised
 * from 0.14 when the outline stroke went (review D4): the tint alone now carries the numeral. */
export const NUMERAL_FILL_ALPHA = 0.22;
/** The mini-roadmap's node alphas (§4.3: past ticked, the current lit, the rest faint). */
export const PIP_ALPHA = Object.freeze({ done: 0.28, current: 1, upcoming: 0.12 });
/** A done node's tick is drawn at this alpha over its node. */
export const TICK_ALPHA = 0.9;

/**
 * The content box of a format (pure; whole stage px): 16:9 the `full` region (x 96-1824, y 54-912), 9:16 the upper
 * critical box (`text-above`, x 192-888, y 288-840, clear of the caption band in the lower one, R-59).
 * @param {'landscape' | 'portrait'} format
 * @returns {Box}
 */
export function contentBox(format) {
  return region({ format }, format === 'portrait' ? 'text-above' : 'full');
}

/**
 * Where each part of the bumper is drawn (pure; whole stage px): `kicker` (null without one), `numeral` (null
 * without one), `title` (its box reaches the bottom of the content area, so `fitLines`' line cap decides its height)
 * and `roadmap` (16:9 with entries: the node row along the bottom; null otherwise). The tops are fixed, so no frame
 * depends on a measurement.
 * @param {'landscape' | 'portrait'} format
 * @param {{kicker: boolean, numeral: boolean, roadmap: number}} shows   roadmap: how many chapters it lists
 * @returns {{kicker: Box | null, numeral: Box | null, title: Box, roadmap: Box | null}}
 */
export function boxes(format, shows) {
  const L = LAYOUT[format];
  const b = contentBox(format);
  const roadmap = L.pip !== null && shows.roadmap > 0
    ? { x0: b.x0, y0: b.y1 - L.pip, x1: b.x1, y1: b.y1 } : null;
  const bottom = roadmap ? roadmap.y0 - L.roadmapGap : b.y1;
  let y = b.y0 + L.top;
  /** @type {Box | null} */
  let kicker = null;
  if (shows.kicker) {
    const h0 = Math.round(L.kicker.max * LINE_HEIGHT.kicker.latin);
    kicker = { x0: b.x0, y0: y, x1: b.x1, y1: y + h0 };
    y += h0 + L.kickerGap;
  }
  /** @type {Box | null} */
  let numeral = null;
  if (shows.numeral) {
    const h0 = Math.round(L.numeral.size * LINE_HEIGHT.numeral);
    numeral = { x0: b.x0, y0: y, x1: b.x1, y1: y + h0 };
    y += h0 + L.titleGap;
  }
  return { kicker, numeral, title: { x0: b.x0, y0: y, x1: b.x1, y1: Math.max(y + L.title.min, bottom) }, roadmap };
}

/**
 * The text slots of this template (templates.py `_chapter_bumper_slots`, which mirrors this table; plan §1.6, §1.11):
 * slot -> format -> variant -> {w, max_lines, size, role, face, weight, line_height, prefer_lines, critical}. The
 * template has no variants, so every slot has only `default`. `kicker` and `numeral` are decoration (not critical:
 * the numeral by deviation 14, the kicker as board_generic's), `title` is the chapter's name and is critical. Line
 * heights are the Latin ones (Indic text takes LINE_HEIGHT's Indic values, which textfit.py's Indic measure uses).
 * Pure.
 * @param {Zones} landscape @param {Zones} portrait
 */
export function slotRows(landscape, portrait) {
  const land = contentBox('landscape');
  const port = contentBox('portrait');
  const wide = land.x1 - land.x0;
  const narrow = port.x1 - port.x0;
  /** @param {{max: number, min: number, lines: number, prefer?: number}} s @param {number} w */
  const title = (s, w) => ({
    w, max_lines: s.lines, size: [s.min, s.max], role: 'title', face: 'display', weight: 700,
    line_height: LINE_HEIGHT.title.latin, prefer_lines: s.prefer ?? null, critical: true,
  });
  /** @param {{max: number, min: number, lines: number}} s @param {number} w */
  const kicker = (s, w) => ({
    w, max_lines: s.lines, size: [s.min, s.max], role: 'label', face: 'body', weight: 600,
    line_height: LINE_HEIGHT.kicker.latin, prefer_lines: null, critical: false,
  });
  /** @param {{size: number}} s @param {number} w */
  const numeral = (s, w) => ({
    w, max_lines: 1, size: [s.size, s.size], role: 'label', face: 'display', weight: 700,
    line_height: LINE_HEIGHT.numeral, prefer_lines: null, critical: false,
  });
  return {
    kicker: {
      landscape: { default: kicker(LAYOUT.landscape.kicker, wide) },
      portrait: { default: kicker(LAYOUT.portrait.kicker, narrow) },
    },
    numeral: {
      landscape: { default: numeral(LAYOUT.landscape.numeral, wide) },
      portrait: { default: numeral(LAYOUT.portrait.numeral, narrow) },
    },
    title: {
      landscape: { default: title(LAYOUT.landscape.title, wide) },
      portrait: { default: title(LAYOUT.portrait.title, narrow) },
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
 * @param {'title' | 'kicker'} role @param {string} text
 */
export function lineHeight(role, text) {
  return INDIC.test(plainText(String(text || ''))) ? LINE_HEIGHT[role].indic : LINE_HEIGHT[role].latin;
}

/** The roadmap of a look (`refs.roadmap`, plan §1.2: [{title, state}]), [] without one or when it is malformed. */
export function roadmapOf(look) {
  const refs = look && typeof look === 'object' ? /** @type {any} */ (look).refs : null;
  const list = refs && typeof refs === 'object' ? refs.roadmap : null;
  if (!Array.isArray(list)) return [];
  return list.filter((e) => e && typeof e === 'object' && typeof e.state === 'string')
    .map((e) => ({ title: String(e.title || ''), state: String(e.state) }));
}

/**
 * What the bumper shows (pure): the chapter `number` as drawn ("02"; '' when the scene names none), the `kicker`
 * (the label's words around the number, or the whole label when it holds no digits) and the `title`. The number is
 * the first run of digits in `chapter_label`, else the 1-based place of the roadmap's `current` entry.
 * @param {TimedScene} scene
 * @param {{title: string, state: string}[]} roadmap
 * @returns {{number: string, kicker: string, title: string}}
 */
export function bumperText(scene, roadmap) {
  const label = String(scene.chapter_label || '').trim();
  const digits = /\d+/.exec(label);
  let number = digits ? digits[0] : '';
  let kicker = digits ? label.replace(digits[0], ' ').replace(/\s+/g, ' ').trim() : label;
  if (!number) {
    const at = roadmap.findIndex((e) => e.state === 'current');
    if (at >= 0) number = String(at + 1);
  }
  if (number.length === 1) number = `0${number}`;
  if (kicker === number) kicker = '';
  return { number, kicker, title: String(scene.title || '').trim() };
}

/**
 * Whether this template draws the scene whole: a chapter card is a number, a label and a title, so a scene that
 * carries board items with text (a look naming this template on a board scene) or has neither a title nor a label is
 * drawn by board_generic instead, every hook handed on (list_build's convention, B2c review CG-2).
 * @param {TimedScene} scene
 */
export function drawsScene(scene) {
  const board = (scene.board || []).some((item) => typeof item.text === 'string' && item.text.trim());
  if (board) return false;
  return !!(String(scene.title || '').trim() || String(scene.chapter_label || '').trim());
}

/**
 * Every TeX string the template renders for the scene (the static `tex` hook; render.js converts them before
 * `ready`): the `$...$` spans of the kicker and the title, inline (`richWords` mounts each with display off).
 * @param {TimedScene} scene
 * @param {any} look
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene, look) {
  const { kicker, title } = bumperText(scene, roadmapOf(look));
  /** @type {{tex: string, display: boolean}[]} */
  const out = [];
  for (const text of [kicker, title]) {
    for (const tex of mathSpans(String(text || ''))) if (tex.trim()) out.push({ tex, display: false });
  }
  return out;
}

/**
 * The nodes of the mini-roadmap inside `box` (pure; whole stage px): `n` equal nodes with `gap` between them, the
 * first starting and the last ending on the box's edges.
 * @param {Box} box @param {number} n @param {number} gap
 * @returns {{x: number, w: number}[]}
 */
export function pipRects(box, n, gap) {
  const total = box.x1 - box.x0;
  const width = (total - gap * Math.max(0, n - 1)) / Math.max(1, n);
  /** @type {{x: number, w: number}[]} */
  const out = [];
  for (let i = 0; i < n; i++) {
    const x0 = Math.round(box.x0 + i * (width + gap));
    const x1 = Math.round(box.x0 + i * (width + gap) + width);
    out.push({ x: x0, w: Math.max(1, x1 - x0) });
  }
  return out;
}

// ---------------------------------------------------------------------------------------------
// Painting helpers
// ---------------------------------------------------------------------------------------------

/**
 * A hex colour at alpha `a` as `rgba(r, g, b, a)` (every engine parses it, and the string is stable: `fx`).
 * @param {string} hex @param {number} a  0..1
 */
export function rgbaOf(hex, a) {
  const v = String(hex).slice(1);
  const [r, g, b] = [0, 2, 4].map((i) => Number.parseInt(v.slice(i, i + 2), 16) || 0);
  return `rgba(${r}, ${g}, ${b}, ${fx(Math.min(1, Math.max(0, a)))})`;
}

/** @param {TemplateContext} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.cbFallback);

/**
 * Place an element on a box with a horizontal alignment (build time; whole px).
 * @param {HTMLElement} el @param {Box} b @param {string} align
 */
function placeText(el, b, align) {
  Object.assign(el.style, {
    position: 'absolute', left: `${Math.round(b.x0)}px`, top: `${Math.round(b.y0)}px`,
    width: `${Math.round(b.x1 - b.x0)}px`, textAlign: align,
  });
}

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** @type {TemplateModule} */
export default {
  id: 'chapter_bumper',

  /**
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format } = ctx;
    if (!drawsScene(scene)) {
      // a board scene (or an empty chapter card) a look named this template on: board_generic draws it whole
      const inner = boardGeneric.build(ctx);
      inner.cbFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    // the roadmap always gives the chapter its number; only a format with a node row draws it (16:9)
    const roadmap = roadmapOf(ctx.look);
    const drawn = L.pip === null ? [] : roadmap;
    const text = bumperText(scene, roadmap);
    const b = boxes(format, { kicker: !!text.kicker, numeral: !!text.number, roadmap: drawn.length });
    const fixes = fixesOf(ctx.look);
    const baseline = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    const ink = ctx.onBackground.ink;
    const ink2 = ctx.onBackground.ink2;
    const root = h('div', {
      class: ['dyn-cb', `dyn-cb--${format}`],
      style: { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' },
    });

    // the kicker: the label's words around the number, on one line, cut with an ellipsis when it is too long
    // (board_generic's kicker: `texts()` reports no overflow for it)
    /** @type {any} */
    let kicker = null;
    if (text.kicker && b.kicker) {
      const lh = lineHeight('kicker', text.kicker);
      const el = h('div', {
        class: 'dyn-cb-kicker', dir: 'auto',
        style: {
          margin: '0px', fontFamily: 'var(--dyn-font-body)', fontWeight: '600', fontSize: `${L.kicker.max}px`,
          lineHeight: String(lh), whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', color: ink2,
        },
      });
      const { words, tex } = richWords(ctx, el, text.kicker);
      const holder = group('dyn-cb-kicker-group', el);
      placeText(holder, b.kicker, L.align);
      root.appendChild(holder);
      kicker = { el, holder, words, tex, size: L.kicker.max, min: L.kicker.min, alpha: 0 };
    }

    // the numeral: decoration (deviation 14), a tint of the block ink (no `-webkit-text-stroke`: in HTML it is
    // painted over the fill, so a self-overlapping glyph's internal joins are stroked too - review D4)
    /** @type {any} */
    let numeral = null;
    if (text.number && b.numeral) {
      const el = h('div', {
        class: 'dyn-cb-numeral', dir: 'ltr',
        style: {
          margin: '0px', fontFamily: 'var(--dyn-font-display)', fontWeight: '700', fontSize: `${L.numeral.size}px`,
          lineHeight: String(LINE_HEIGHT.numeral), fontVariantNumeric: 'tabular-nums',
          color: rgbaOf(ink, NUMERAL_FILL_ALPHA),
        },
        text: text.number,
      });
      const holder = group('dyn-cb-numeral-group', el);
      placeText(holder, b.numeral, L.align);
      root.appendChild(holder);
      numeral = { el, holder, size: L.numeral.size, overflow: 0, alpha: 0 };
    }

    // the title: the chapter's name (critical), with R-53's scrim remedy behind it when preflight asked for one
    const lh = lineHeight('title', text.title);
    const titleEl = h('h2', {
      class: 'dyn-cb-title', dir: 'auto',
      style: {
        margin: '0px', fontFamily: 'var(--dyn-font-display)', fontWeight: '700', lineHeight: String(lh),
        overflowWrap: 'anywhere', color: ink,
      },
    });
    const titleWords = richWords(ctx, titleEl, text.title);
    const mask = baseline ? revealMask(titleEl) : null;
    const scrim = fixes.slots.title && fixes.slots.title.scrim ? ctx.roles.color('scrim') : null;
    const titleHolder = group('dyn-cb-title-group', mask || titleEl);
    placeText(titleHolder, b.title, L.align);
    if (scrim) {
      Object.assign(titleHolder.style, {
        padding: `${SPACE[0]}px ${SPACE[2]}px ${SPACE[1]}px`, marginLeft: `-${SPACE[2]}px`,
        borderRadius: `${RADIUS.card}px`, background: scrim,
      });
    }
    root.appendChild(titleHolder);

    // the mini-roadmap along the bottom (16:9): one node per chapter, the done ones ticked
    /** @type {any} */
    let progressRow = null;
    const rm = b.roadmap;
    if (rm && drawn.length) {
      const row = h('div', { class: 'dyn-cb-progress', 'aria-hidden': 'true', style: { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' } });
      const rects = pipRects(rm, drawn.length, L.pipGap);
      const pipH = Number(L.pip);       // the node row's height (the format draws one: `rm` is not null)
      const tickPx = Number(L.pipIcon);
      // the pill and its tick are siblings, each painted at its own alpha (a tick inside a faint pill would inherit
      // the pill's opacity and all but disappear)
      const pips = drawn.map((entry, i) => {
        const pip = h('div', {
          class: ['dyn-cb-pip', `dyn-cb-pip--${entry.state}`],
          style: {
            position: 'absolute', left: `${rects[i].x - rm.x0}px`, top: '0px', width: `${rects[i].w}px`,
            height: `${pipH}px`, borderRadius: `${RADIUS.chip}px`,
          },
        });
        row.appendChild(pip);
        /** @type {SVGElement | null} */
        let tick = null;
        if (entry.state === 'done') {
          tick = icon('check', { class: 'dyn-cb-tick' });
          Object.assign(tick.style, {
            position: 'absolute', display: 'block',
            left: `${Math.round(rects[i].x - rm.x0 + (rects[i].w - tickPx) / 2)}px`,
            top: `${Math.round((pipH - tickPx) / 2)}px`,
            width: `${tickPx}px`, height: `${tickPx}px`,
          });
          row.appendChild(tick);
        }
        return { entry, pip, tick };
      });
      const holder = group('dyn-cb-progress-group', row);
      Object.assign(holder.style, {
        position: 'absolute', left: `${rm.x0}px`, top: `${rm.y0}px`,
        width: `${rm.x1 - rm.x0}px`, height: `${rm.y1 - rm.y0}px`,
      });
      root.appendChild(holder);
      progressRow = { holder, row, pips, alpha: 0 };
    }

    ctx.root.appendChild(root);
    return {
      root, kicker, numeral, progress: progressRow, text, boxes: b,
      title: { el: titleEl, holder: titleHolder, mask, words: titleWords.words, tex: titleWords.tex, lh, scrim,
        size: L.title.max, min: L.title.min, overflow: 0, alpha: 0 },
    };
  },

  /**
   * Fit the text (runs again when a late face arrives; idempotent, because it only reads the laid-out page): the
   * title at the largest 4 px size whose lines fit its box, and the numeral's own overflow (a chapter number too wide
   * for the frame, which `texts()` reports as a decorative overflow).
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    if (isFallback(ctx)) {
      if (typeof boardGeneric.layout === 'function') boardGeneric.layout(ctx);
      return;
    }
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
    const range = slotRange(ctx, 'title', { min: L.title.min, max: L.title.max, role: 'title' });
    const fit = fitLines(v.title.el, {
      max: range.max, min: range.min, lines: L.title.lines, prefer: L.title.prefer, lh: v.title.lh, step: SIZE_STEP,
    }, v.title.words, v.title.tex);
    v.title.size = fit.size;
    v.title.min = range.min;
    v.title.overflow = fit.overflow;
    if (v.kicker) {
      const kr = slotRange(ctx, 'kicker', { min: L.kicker.min, max: L.kicker.max, role: 'label' });
      v.kicker.el.style.fontSize = `${kr.max}px`;
      v.kicker.size = kr.max;
      v.kicker.min = kr.min;
    }
    if (v.numeral) {
      const nr = slotRange(ctx, 'numeral', { min: L.numeral.size, max: L.numeral.size, role: 'label' });
      v.numeral.el.style.fontSize = `${nr.max}px`;
      v.numeral.size = nr.max;
      const box = v.boxes.numeral ? v.boxes.numeral.x1 - v.boxes.numeral.x0 : 0;
      v.numeral.overflow = Math.max(0, v.numeral.el.offsetWidth - box);
    }
  },

  /**
   * Paint scene time t: the numeral's slide (the kicker with it), the title 120 ms later with the personality's text
   * entrance, the roadmap's fade and the current chapter's node lighting. A pure function of t: every property this
   * template owns is painted on every frame, with a value.
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
    const ink = ctx.onBackground.ink;

    const eNumeral = enterAt(ctx, t, 0, NUMERAL_ENTER);
    if (v.numeral) {
      v.numeral.alpha = eNumeral;
      paintRise(ctx, v.numeral.el, eNumeral, NUMERAL_RISE);
      p.style(v.numeral.el, 'visibility', eNumeral > 0 ? 'visible' : 'hidden');
    }
    if (v.kicker) {
      v.kicker.alpha = eNumeral;
      paintRise(ctx, v.kicker.el, eNumeral, TEXT_RISE);
      p.style(v.kicker.el, 'visibility', eNumeral > 0 ? 'visible' : 'hidden');
    }

    const eTitle = enterAt(ctx, t, seconds(ctx, TITLE_DELAY), TITLE_ENTER);
    v.title.alpha = eTitle;
    paintTextEntrance(ctx, v.title.el, v.title.mask, eTitle);

    if (v.progress) {
      const e = enterAt(ctx, t, seconds(ctx, ROADMAP_DELAY), ROADMAP_ENTER);
      v.progress.alpha = e;
      paintRise(ctx, v.progress.row, e, 0);
      p.style(v.progress.row, 'visibility', e > 0 ? 'visible' : 'hidden');
      const lit = ease('ease.move', progress(t, seconds(ctx, LIGHT_AT), seconds(ctx, LIGHT)));
      for (const pip of v.progress.pips) {
        const state = pip.entry.state;
        const alpha = state === 'current'
          ? PIP_ALPHA.upcoming + (PIP_ALPHA.current - PIP_ALPHA.upcoming) * lit
          : state === 'done' ? PIP_ALPHA.done : PIP_ALPHA.upcoming;
        p.style(pip.pip, 'background-color', ink);
        p.style(pip.pip, 'opacity', fx(alpha));
        if (pip.tick) {
          p.style(pip.tick, 'color', ink);
          p.style(pip.tick, 'opacity', fx(TICK_ALPHA));
        }
      }
    }
  },

  /**
   * Where a look_here points: nowhere (a bumper has no board item, and templates.py `consumes` declares no cue, so
   * the stage never asks).
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    return null;
  },

  /**
   * The element holding an item's text: none (a bumper draws no board item).
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    return null;
  },

  /**
   * The content groups a look_here would dim (the same elements at every t; this template never writes their
   * opacity): the kicker, the numeral, the title and the roadmap row.
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.kicker ? v.kicker.holder : null, v.numeral ? v.numeral.holder : null, v.title.holder,
      v.progress ? v.progress.holder : null].filter(Boolean);
  },

  /**
   * The morph anchor (plan §1.4): none yet. §4.3's shared anchor (the previous chapter's visual morphing into a
   * "from last chapter" chip, `look.refs.anchor`) needs the morph overlay, which is B2f; until then a bumper is
   * entered by the look's crossfade.
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: the box a medium is shown in: none (a bumper shows no picture or clip).
   * @param {TimedScene} scene
   * @param {Zones} zones
   * @param {'live' | 'render'} mode
   * @param {string | undefined} slot
   * @param {import('../core.js').Fixes} fixes
   * @returns {{w: number, h: number} | null}
   */
  mediaBox(scene, zones, mode, slot, fixes) {
    if (!drawsScene(scene)) return typeof boardGeneric.mediaBox === 'function' ? boardGeneric.mediaBox(scene, zones, mode, slot, fixes) : null;
    return null;
  },

  /**
   * Static: every TeX string the template renders for the scene (`texOf`; the same in both formats).
   * @param {TimedScene} scene
   * @param {import('../../shared/types.js').ResolvedLook | null} look
   * @param {'landscape' | 'portrait'} _fmt
   */
  tex(scene, look, _fmt) {
    if (!drawsScene(scene)) return typeof boardGeneric.tex === 'function' ? boardGeneric.tex(scene, look, _fmt) : [];
    return texOf(scene, look);
  },

  /**
   * The text on screen (preflight, §7.12): the title (slot `title`, critical, the chapter's name) and the decorative
   * kicker and numeral (slots `kicker` / `numeral`, not critical: board_generic's kicker precedent and deviation 14),
   * in the block inks on the background's worst case (or on the R-53 scrim over it).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const on = ctx.onBackground;
    /** @type {TextEntry[]} */
    const out = [];
    if (v.kicker) {
      out.push({
        el: v.kicker.el, role: 'label', slot: 'kicker', critical: false, ink: on.ink2, bg: on.bg,
        alpha: v.kicker.alpha, overflow: 0, size: v.kicker.size, min: v.kicker.min, weight: 600,
        text: plainText(String(v.text.kicker || '')),
      });
    }
    if (v.numeral) {
      out.push({
        el: v.numeral.el, role: 'label', slot: 'numeral', critical: false, ink: on.ink, bg: on.bg,
        alpha: v.numeral.alpha, overflow: v.numeral.overflow, size: v.numeral.size, min: v.numeral.size, weight: 700,
        text: v.text.number,
      });
    }
    out.push({
      el: v.title.el, role: 'title', slot: 'title', critical: true, ink: on.ink,
      bg: v.title.scrim ? scrimmedBg(v.title.scrim, on, on.ink) : on.bg,
      alpha: v.title.alpha, overflow: v.title.overflow, size: v.title.size, min: v.title.min, weight: 700,
      text: plainText(String(v.text.title || '')),
    });
    return out;
  },
};
