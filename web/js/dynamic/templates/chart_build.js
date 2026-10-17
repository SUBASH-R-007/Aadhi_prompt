// @ts-check
/**
 * `chart_build` (DYNAMIC_VIDEO_DESIGN §4.3 B, R1, class `quantity`; owner decision 1 of 2026-10-16): a `chart` panel
 * drawn as a real plot by the vendored Chart.js, built on the narration's beats. Data (§4.3, §4.4's `chart` row,
 * templates.py `_fit_chart_build`): a scene whose content is its `chart` (or, while `function_plot` is unbuilt, its
 * `graph`) side panel, with at most one heading beside it. A scene this template cannot draw whole - another board
 * item, no shown chart or graph panel, or a chart past what `_charts.js chartPlan` draws in this format - is handed to
 * `board_generic` whole, every hook with it (`drawsScene`, B2c review CG-2; `aadhi/compose/dynamic/charts.py` makes
 * the Python fit refuse exactly the same scenes).
 *
 * Layout (§2.5 zones and regions from the catalogue; whole stage px):
 * - 16:9: the headline (the heading item, else the scene's title, else the chart's own title) across the critical box
 *   from the title zone's top (y 112), one line preferred and two allowed; the chart's own title under it as a caption
 *   when it says something the headline does not; the chart in the rest of the critical box (x 96-1824, to y 912).
 * - 9:16: the headline in the `headline` region (x 192-888, from y 340), the caption and the chart in the text column
 *   both critical bands hold (x 192-780; the lower band ends at the TikTok rail, x 780), the chart ending 8 px above the
 *   caption band (the catalogue's `zones.caption.y0`). A bar chart is drawn there as a labelled bar list (`_charts.js`).
 * - Everything inside the chart (`_charts.js`): marks and gridlines on the canvas, every label page text.
 *
 * Motion (§4.3, §4.8; every duration times the personality's scale): the headline enters with the personality's text
 * entrance (its heading's beat, else HEADLINE_DELAY), the caption ROW_DELAY later; the chart's axes fade in from the
 * panel's own start (`show_at`, else CHART_DELAY), then its bars grow, its lines and curves draw and its values count up
 * (`_charts.js chartTimes`: a `draw` event on `panel:bar:<s>:<i>` / `panel:point:<s>:<i>` reveals that mark on its
 * beat, a `count` event starts a value's own count-up). Nothing leaves; the background keeps moving (not dense).
 *
 * Cues (cues.js draws them; this template draws no emphasis, §4.2): `look_here` on `panel` (the plot), on
 * `panel:bar:<s>:<i>` (§4.3 "the named bar takes cue.lookHere") or on `panel:point:<s>:<i>` - the outline form (the
 * spec is not push_ok). `itemEl` gives the heading's `[[term]]` spans should a cue_term ever be consumed.
 *
 * Every constant here is a B2 proposal pending the owner's review; `aadhi/compose/dynamic/charts.py` mirrors LAYOUT and
 * `_charts.js` SIZES as the template's slots (`slotRows`), and the jsdom test pins the two together.
 */

import { h } from '../../shared/dom.js';
import { mathSpans } from '../../player/richtext.js';
import { splitWords } from '../../motion/text.js';
import { stripRich } from '../../player/panels/util.js';
import { ZONES, fixesOf } from '../core.js';
import { itemTimings } from '../state.js';
import boardGeneric from './board_generic.js';
import {
  SPACE, enterAt, fitLines, group, paintTextEntrance, region, revealMask, richWords, scrimmedBg, shownPanel, slotRange,
} from './_shared.js';
import {
  SIZES as CHART_SIZES, buildChart, chartFocusRect, chartPlan, chartTexts, layoutChart, paintChart,
} from './_charts.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TemplateModule} TemplateModule */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('../../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../../shared/types.js').BoardItem} BoardItem */
/** @typedef {import('../core.js').Zones} Zones */
/** @typedef {import('../core.js').Box} Box */

export const ID = 'chart_build';

/**
 * Per-format layout (stage px; B2 proposals, pending the owner's review).
 * - `title`: the headline, display face 700 (16:9 64-96, 9:16 56-88: R-29's 16:9 headings are 84-120 and its 9:16 hook
 *   band 88-104, above the floors as deviation 31 allows), one line preferred, two allowed.
 * - `caption`: the chart's own title under the headline, body face (16:9 44-52 at 600; 9:16 44-52 at 700: R-29 asks
 *   >= 600 at or below 64 px), at most 2 lines, `ink2`.
 * - `titleGap`: headline to caption; `chartGap`: the last of them to the chart.
 */
export const LAYOUT = Object.freeze({
  landscape: Object.freeze({
    title: Object.freeze({ max: 96, min: 64, lines: 2, prefer: 1 }),
    caption: Object.freeze({ max: 52, min: 44, lines: 2 }),
    captionWeight: 600,
    titleGap: SPACE[1],
    chartGap: SPACE[4],
  }),
  portrait: Object.freeze({
    title: Object.freeze({ max: 88, min: 56, lines: 2, prefer: 1 }),
    caption: Object.freeze({ max: 52, min: 44, lines: 2 }),
    captionWeight: 700,
    titleGap: SPACE[1],
    chartGap: SPACE[3],
  }),
});
/** The headline's weight and tracking (full_bleed_media's label, so the slot measures like it). */
export const TITLE_WEIGHT = 700;
export const TITLE_TRACKING_EM = -0.015;
/** Line heights (§4.6 type scale): the headline the display role's, the caption the body role's; Indic the Indic minimums. */
export const LINE_HEIGHT = Object.freeze({
  display: Object.freeze({ latin: 1.05, indic: 1.25 }),
  body: Object.freeze({ latin: 1.35, indic: 1.45 }),
});
const INDIC = new RegExp(`[${['Devanagari', 'Bengali', 'Gurmukhi', 'Gujarati', 'Oriya', 'Tamil', 'Telugu', 'Kannada',
  'Malayalam', 'Sinhala'].map((x) => `\\p{Script=${x}}`).join('')}]`, 'u');
/** @param {'display' | 'body'} role @param {string} text */
export function lineHeight(role, text) {
  return INDIC.test(String(text || '')) ? LINE_HEIGHT[role].indic : LINE_HEIGHT[role].latin;
}
/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;

// Motion (§4.8), seconds before the personality's scale
/** The headline enters this long after the scene starts when no beat reveals it (media_split's TITLE_DELAY). */
export const HEADLINE_DELAY = 0.08;
/** §4.8 text entrance 400-600 ms. */
export const TEXT_ENTER = 0.45;
/** The caption follows the headline by this (§4.8: 60-120 ms per item). */
export const ROW_DELAY = 0.12;
/** The chart's axes start this long after the scene starts when its panel shows from the start. */
export const CHART_DELAY = 0.3;
/** The headline and caption shrink (to their minimums) until the plot has at least this share of the chart's box. */
export const COMFORT = 0.42;

// ---------------------------------------------------------------------------------------------
// What the scene shows (pure)
// ---------------------------------------------------------------------------------------------

/**
 * The scene's heading item (its only board item, when it has one), else null.
 * @param {TimedScene} scene
 * @returns {BoardItem | null}
 */
export function headingOf(scene) {
  return (scene.board || []).find((i) => i && i.kind === 'heading') || null;
}

/**
 * Whether this template draws the scene whole: a shown `chart` or `graph` side panel `_charts.js chartPlan` draws in
 * `format`, and nothing on the board but at most one heading (templates.py `_panel_only`); never a quiz, never a scene
 * with a medium of its own. Pure; `aadhi/compose/dynamic/charts.py` and `_fit_chart_build` refuse exactly the scenes
 * this refuses (B2c review CG-2).
 * @param {TimedScene} scene
 * @param {'landscape' | 'portrait'} [format]
 */
export function drawsScene(scene, format = 'landscape') {
  if (!scene || scene.quiz || scene.media) return false;
  const sp = shownPanel(scene);
  if (!sp) return false;
  const board = scene.board || [];
  if (board.length > 1 || board.some((i) => !i || i.kind !== 'heading')) return false;
  return chartPlan(sp.panel, format === 'portrait' ? 'portrait' : 'landscape') !== null;
}

/**
 * The headline and the caption (pure): the headline is the heading item's rich-lite text, else the scene's title,
 * else the chart's own title (plain); the caption is the chart's own title when the headline is something else and
 * the two differ (nothing the scene carries is left out but the scene title beside a heading, formula_focus's rule).
 * @param {TimedScene} scene
 * @returns {{headline: string, rich: boolean, item: string | null, caption: string}}
 */
export function wordsOf(scene) {
  const heading = headingOf(scene);
  const sp = shownPanel(scene);
  const own = sp && typeof sp.panel.title === 'string' ? stripRich(sp.panel.title) : '';
  const title = String(scene.title || '').trim();
  if (heading) {
    const text = String(heading.text || '');
    return { headline: text, rich: true, item: heading.id, caption: own && own !== stripRich(text) ? own : '' };
  }
  if (title) return { headline: title, rich: false, item: null, caption: own && own !== title ? own : '' };
  return { headline: own, rich: false, item: null, caption: '' };
}

/**
 * The boxes (pure; whole stage px): the headline's (`title`), the caption's and the chart's text column, and where the
 * chart must end. 16:9: the critical box from the title zone's top; 9:16: the headline region, the text column both
 * critical bands hold (x 192-780) and the caption band's top less 8 px.
 * @param {'landscape' | 'portrait'} format
 * @returns {{title: Box, column: Box, bottom: number}}
 */
export function boxesOf(format) {
  const at = { format };
  if (format === 'portrait') {
    const headline = region(at, 'headline');
    const text = ZONES.portrait.textColumn;
    return {
      title: { x0: headline.x0, y0: headline.y0, x1: headline.x1, y1: headline.y1 },
      column: { x0: text.x0, y0: headline.y0, x1: text.x1, y1: ZONES.portrait.caption.y0 - SPACE[0] },
      bottom: ZONES.portrait.caption.y0 - SPACE[0],
    };
  }
  const full = region(at, 'full');
  const top = ZONES.landscape.title.y0;
  return {
    title: { x0: full.x0, y0: top, x1: full.x1, y1: ZONES.landscape.title.y1 },
    column: { x0: full.x0, y0: top, x1: full.x1, y1: full.y1 },
    bottom: full.y1,
  };
}

/**
 * Every TeX string this template converts (the static `tex` hook; render.js converts them before `ready`): the `$...$`
 * spans of the heading's text. A scene title and the chart's labels are plain.
 * @param {TimedScene} scene
 * @returns {{tex: string, display: boolean}[]}
 */
export function texOf(scene) {
  const heading = headingOf(scene);
  if (!heading || !String(heading.text || '').includes('$')) return [];
  return mathSpans(String(heading.text || '')).filter((x) => x.trim()).map((x) => ({ tex: x, display: false }));
}

/**
 * This template's text slots as `aadhi/compose/dynamic/charts.py chart_build_slots` holds them (plan §1.6 SlotSpec: one
 * `default` variant per format). The jsdom test pins the catalogue's rows to these. Pure.
 */
export function slotRows() {
  /** @param {'landscape' | 'portrait'} fmt */
  const rows = (fmt) => {
    const L = LAYOUT[fmt];
    const B = boxesOf(fmt);
    const titleW = B.title.x1 - B.title.x0;
    const colW = B.column.x1 - B.column.x0;
    const C = CHART_SIZES[fmt];
    /** @param {'tick' | 'value' | 'axis' | 'series'} slot */
    const chart = (slot) => ({
      w: colW, max_lines: C[slot].lines, size: [C[slot].min, C[slot].max], role: 'label', face: 'body',
      weight: C[slot].weight, line_height: LINE_HEIGHT.body.latin, prefer_lines: null, tracking_em: 0,
      tabular_nums: slot === 'tick' || slot === 'value',
    });
    return {
      title: {
        w: titleW, max_lines: L.title.lines, size: [L.title.min, L.title.max], role: 'title', face: 'display',
        weight: TITLE_WEIGHT, line_height: LINE_HEIGHT.display.latin, prefer_lines: L.title.prefer,
        tracking_em: TITLE_TRACKING_EM, tabular_nums: false,
      },
      caption: {
        w: colW, max_lines: L.caption.lines, size: [L.caption.min, L.caption.max], role: 'label', face: 'body',
        weight: L.captionWeight, line_height: LINE_HEIGHT.body.latin, prefer_lines: null, tracking_em: 0,
        tabular_nums: false,
      },
      tick: chart('tick'),
      value: chart('value'),
      axis: chart('axis'),
      series: chart('series'),
    };
  };
  const land = rows('landscape');
  const port = rows('portrait');
  /** @type {Record<string, Record<string, Record<string, any>>>} */
  const out = {};
  for (const slot of Object.keys(land)) {
    out[slot] = { landscape: { default: /** @type {any} */ (land)[slot] }, portrait: { default: /** @type {any} */ (port)[slot] } };
  }
  return out;
}

// ---------------------------------------------------------------------------------------------
// The template
// ---------------------------------------------------------------------------------------------

/** True once `build` handed the scene to board_generic (every hook then hands on too). @param {any} ctx */
const isFallback = (ctx) => !!(ctx.view && ctx.view.cbFallback);

/**
 * Plain text as word spans (Intl.Segmenter units: Indic clusters stay whole).
 * @param {HTMLElement} host @param {string} text @param {string} locale
 * @returns {HTMLElement[]}
 */
function wordSpans(host, text, locale) {
  /** @type {HTMLElement[]} */
  const out = [];
  for (const part of splitWords(text, locale)) {
    const span = h('span', { class: 'dyn-word', text: part.text });
    out.push(span);
    host.append(span, part.ws ? document.createTextNode(part.ws) : '');
  }
  return out;
}

/** @type {TemplateModule} */
export default {
  id: ID,

  /**
   * @param {TemplateContext} ctx
   */
  build(ctx) {
    const { scene, format, roles } = ctx;
    if (!drawsScene(scene, format)) {
      const inner = boardGeneric.build(ctx);
      inner.cbFallback = true;
      return inner;
    }
    const L = LAYOUT[format];
    const onBg = ctx.onBackground;
    const fixes = fixesOf(ctx.look);
    const masked = !!ctx.motion && ctx.motion.entrance === 'baseline_reveal';
    const sp = /** @type {import('../../shared/types.js').ResolvedSidePanel} */ (shownPanel(scene));
    const plan = /** @type {import('./_charts.js').ChartPlan} */ (chartPlan(sp.panel, format));
    const words = wordsOf(scene);
    const timings = itemTimings(scene);

    // the headline (text on the background)
    const title = h('h2', {
      class: 'dyn-cb-title', dir: 'auto',
      style: {
        margin: '0px', color: onBg.ink, fontFamily: 'var(--dyn-font-display)', fontWeight: String(TITLE_WEIGHT),
        lineHeight: String(lineHeight('display', words.headline)), letterSpacing: `${TITLE_TRACKING_EM}em`,
        overflowWrap: 'anywhere',
      },
    });
    /** @type {HTMLElement | null} */
    let field = null;
    let titleWords;
    if (words.rich) {
      field = h('span', { class: 'dyn-cb-field' });
      titleWords = richWords(ctx, field, words.headline);
      title.appendChild(field);
    } else {
      const ws = wordSpans(title, words.headline, ctx.locale);
      titleWords = { words: ws, tex: ws.map(() => false) };
    }
    const titleScrim = fixes.slots.title && fixes.slots.title.scrim ? roles.color('scrim') : null;
    const titleBox = h('div', { class: 'dyn-cb-titlebox', style: { position: 'absolute', boxSizing: 'border-box' } },
      masked ? revealMask(title) : title);
    if (titleScrim) Object.assign(titleBox.style, { background: titleScrim, borderRadius: `${SPACE[1]}px` });

    // the caption: the chart's own title (plain)
    /** @type {HTMLElement | null} */
    let caption = null;
    /** @type {HTMLElement[]} */
    let captionWords = [];
    /** @type {HTMLElement | null} */
    let captionBox = null;
    const captionScrim = fixes.slots.caption && fixes.slots.caption.scrim ? roles.color('scrim') : null;
    if (words.caption) {
      caption = h('div', {
        class: 'dyn-cb-caption', dir: 'auto',
        style: {
          margin: '0px', color: onBg.ink2, fontFamily: 'var(--dyn-font-body)', fontWeight: String(L.captionWeight),
          lineHeight: String(lineHeight('body', words.caption)), overflowWrap: 'anywhere',
        },
      });
      captionWords = wordSpans(caption, words.caption, ctx.locale);
      captionBox = h('div', { class: 'dyn-cb-captionbox', style: { position: 'absolute', boxSizing: 'border-box' } },
        masked ? revealMask(caption) : caption);
      if (captionScrim) Object.assign(captionBox.style, { background: captionScrim, borderRadius: `${SPACE[1]}px` });
    }
    const headGroup = group('dyn-cb-head', titleBox, captionBox);

    // the chart: its axes from the panel's own start; the draw / count events this spec consumes
    const titleEnter = words.item !== null
      ? (timings.find((x) => x.id === words.item) || { enter: HEADLINE_DELAY }).enter
      : HEADLINE_DELAY;
    const showAt = Math.max(0, Number(sp.show_at) || 0);
    const events = (scene.events || []).filter((e) => e && (e.kind === 'draw' || e.kind === 'count'));
    const chart = buildChart(ctx, plan, { start: showAt > 0 ? showAt : CHART_DELAY, events });
    const root = h('div', {
      class: ['dyn-cb', `dyn-cb--${format}`],
      style: { position: 'absolute', left: '0px', top: '0px', width: `${ctx.zones.width}px`, height: `${ctx.zones.height}px` },
    }, headGroup, chart.el);
    ctx.root.appendChild(root);
    return {
      root, format, plan, chart, headGroup, title, titleBox, titleWords, titleScrim, field, item: words.item,
      caption, captionBox, captionWords, captionScrim,
      titleMask: masked ? /** @type {HTMLElement} */ (titleBox.firstElementChild) : null,
      captionMask: masked && captionBox ? /** @type {HTMLElement} */ (captionBox.firstElementChild) : null,
      titleEnter, captionEnter: titleEnter + ROW_DELAY,
      titleSize: L.title.max, titleRange: { min: L.title.min, max: L.title.max }, titleOverflow: 0, titleAlpha: 0,
      captionSize: L.caption.max, captionRange: { min: L.caption.min, max: L.caption.max }, captionOverflow: 0,
      captionAlpha: 0,
    };
  },

  /**
   * Measure and fit (idempotent): the headline at the largest size whose lines fit (one preferred), the caption under
   * it, then the chart in the rest of the column (`_charts.js layoutChart`).
   * @param {TemplateContext} ctx
   */
  layout(ctx) {
    if (isFallback(ctx)) {
      if (typeof boardGeneric.layout === 'function') boardGeneric.layout(ctx);
      return;
    }
    const v = ctx.view;
    const L = LAYOUT[ctx.format];
    const B = boxesOf(ctx.format);
    const titleW = B.title.x1 - B.title.x0;
    const colW = B.column.x1 - B.column.x0;
    const titleRange = slotRange(ctx, 'title', { min: L.title.min, max: L.title.max, role: 'title' });
    const captionRange = slotRange(ctx, 'caption', { min: L.caption.min, max: L.caption.max, role: 'label' });
    v.titleRange = titleRange;
    v.captionRange = captionRange;
    Object.assign(v.titleBox.style, { left: `${B.title.x0}px`, top: `${B.title.y0}px`, width: `${titleW}px` });
    const lh = Number(v.title.style.lineHeight) || LINE_HEIGHT.display.latin;
    const clh = v.caption ? Number(v.caption.style.lineHeight) || LINE_HEIGHT.body.latin : LINE_HEIGHT.body.latin;
    // The headline and the caption give up size (4 px at a time, to their minimums) before the chart is left without
    // room: the chart is the content. Each pass fits them at their largest size up to the cap, then lays the chart out
    // in what they leave; the first pass whose chart fits with its plot at COMFORT of the chart's box is kept, else the
    // last (everything at its minimum: the most room the chart can have; what still does not fit is reported by it).
    let titleCap = titleRange.max;
    let captionCap = captionRange.max;
    for (;;) {
      const fit = fitLines(v.title, {
        max: titleCap, min: titleRange.min, lines: L.title.lines, lh, prefer: L.title.prefer, step: SIZE_STEP,
      }, v.titleWords.words, v.titleWords.tex);
      v.titleSize = fit.size;
      v.titleOverflow = fit.overflow;
      let y = B.title.y0 + (v.titleBox.offsetHeight || Math.round(fit.size * lh * L.title.prefer));
      if (v.caption && v.captionBox) {
        y += L.titleGap;
        Object.assign(v.captionBox.style, { left: `${B.column.x0}px`, top: `${Math.round(y)}px`, width: `${colW}px` });
        const cf = fitLines(v.caption, {
          max: captionCap, min: captionRange.min, lines: L.caption.lines, lh: clh, step: SIZE_STEP,
        }, v.captionWords);
        v.captionSize = cf.size;
        v.captionOverflow = cf.overflow;
        y += v.captionBox.offsetHeight || Math.round(cf.size * clh);
      }
      // the chart: the rest of the column
      const top = Math.round(y + L.chartGap);
      layoutChart(ctx, v.chart, { x0: B.column.x0, y0: top, x1: B.column.x1, y1: B.bottom });
      const done = titleCap <= titleRange.min && (!v.caption || captionCap <= captionRange.min);
      if ((v.chart.geo && v.chart.geo.fits && v.chart.geo.share >= COMFORT) || done) break;
      titleCap = Math.max(titleRange.min, titleCap - SIZE_STEP);
      captionCap = Math.max(captionRange.min, captionCap - SIZE_STEP);
    }
  },

  /**
   * Paint scene time t.
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
    const te = enterAt(ctx, t, v.titleEnter, TEXT_ENTER);
    v.titleAlpha = te;
    paintTextEntrance(ctx, v.title, v.titleMask, te);
    p.style(v.titleBox, 'visibility', te > 0.001 ? 'visible' : 'hidden');
    if (v.caption && v.captionBox) {
      const ce = enterAt(ctx, t, v.captionEnter, TEXT_ENTER);
      v.captionAlpha = ce;
      paintTextEntrance(ctx, v.caption, v.captionMask, ce);
      p.style(v.captionBox, 'visibility', ce > 0.001 ? 'visible' : 'hidden');
    }
    paintChart(ctx, v.chart, t);
  },

  /**
   * Where a look_here points (plan §1.7): the chart's plot (`panel`), a bar (`panel:bar:<s>:<i>`) or a point
   * (`panel:point:<s>:<i>`), as painted now; null for anything else.
   * @param {TemplateContext} ctx
   * @param {string} target
   * @returns {FocusRect | null}
   */
  focusRect(ctx, target) {
    if (isFallback(ctx)) return typeof boardGeneric.focusRect === 'function' ? boardGeneric.focusRect(ctx, target) : null;
    return chartFocusRect(ctx, ctx.view.chart, target);
  },

  /**
   * The heading's text, where cue.term finds its `[[term]]` spans (`_shared.js richWords`); null for anything else.
   * @param {TemplateContext} ctx
   * @param {string} itemId
   * @returns {Element | Element[] | null}
   */
  itemEl(ctx, itemId) {
    if (isFallback(ctx)) return typeof boardGeneric.itemEl === 'function' ? boardGeneric.itemEl(ctx, itemId) : null;
    const v = ctx.view;
    return v.item !== null && String(itemId) === v.item ? v.field : null;
  },

  /**
   * The content groups a look_here dims (all but the target's): the headline block and the chart. The same elements
   * at every t.
   * @param {TemplateContext} ctx
   * @returns {Element[]}
   */
  dimGroups(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.dimGroups === 'function' ? boardGeneric.dimGroups(ctx) : [];
    const v = ctx.view;
    return [v.headGroup, v.chart.el];
  },

  /**
   * The shared-element morph's anchor (§4.4, B2f): none yet.
   * @param {TemplateContext} _ctx
   * @returns {null}
   */
  anchor(_ctx) {
    return null;
  },

  /**
   * Static: this template shows no medium (a chart is drawn, never extracted); board_generic's box for a scene it
   * draws instead.
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
   * The text on screen (preflight, §7.12): the headline (slot `title`), the caption (slot `caption`) and every chart
   * label (`_charts.js chartTexts`), critical, on the background's worst case (or the scrim a fix asked for).
   * @param {TemplateContext} ctx
   * @returns {TextEntry[]}
   */
  texts(ctx) {
    if (isFallback(ctx)) return typeof boardGeneric.texts === 'function' ? boardGeneric.texts(ctx) : [];
    const v = ctx.view;
    const onBg = ctx.onBackground;
    const L = LAYOUT[ctx.format];
    /** @type {TextEntry[]} */
    const out = [{
      el: v.title, role: 'title', slot: 'title', critical: true, ink: onBg.ink,
      bg: v.titleScrim ? scrimmedBg(v.titleScrim, onBg, onBg.ink) : onBg.bg, alpha: v.titleAlpha,
      overflow: v.titleOverflow, size: v.titleSize, min: v.titleRange.min, weight: TITLE_WEIGHT,
      text: v.title.textContent || '',
    }];
    if (v.caption) {
      out.push({
        el: v.caption, role: 'label', slot: 'caption', critical: true, ink: onBg.ink2,
        bg: v.captionScrim ? scrimmedBg(v.captionScrim, onBg, onBg.ink2) : onBg.bg, alpha: v.captionAlpha,
        overflow: v.captionOverflow, size: v.captionSize, min: v.captionRange.min, weight: L.captionWeight,
        text: v.caption.textContent || '',
      });
    }
    return [...out, ...chartTexts(ctx, v.chart)];
  },
};
