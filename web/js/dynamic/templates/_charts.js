// @ts-check
/**
 * Real plots for the Dynamic stage (DYNAMIC_VIDEO_DESIGN §4.3 `chart_build`, R-27, R-29, §4.7, §4.8; B2 plan §2
 * "Chart.js"; owner decision 1 of 2026-10-16: "build chart_build with the vendored Chart.js ... graph/chart panels
 * inside other templates draw real plots too"). One engine for the `chart_build` template and for any template's
 * `chart` / `graph` side panel, so a plot is drawn, timed, pointed at and reported the same way everywhere.
 *
 * What it draws (`chartPlan(panel, layout)`, pure; `null` = this engine does not draw the panel in that layout, and
 * the caller hands the scene on whole, B2c review CG-2). The layout class is `landscape` (chart_build's 16:9 box, the
 * critical box's width), `portrait` (a 9:16 text column: chart_build's, and a side panel's reel beat, x 192-780) or
 * `panel` (a chart in another template's 16:9 side panel box, at least about 760 x 520):
 * - a `chart` panel (`ChartSpec`) of type `bar` or `line` (pie, doughnut and radar are not drawn yet: their direct
 *   labels need a layout of their own, an open item), within LIMITS per format: 16:9 vertical bars (grouped up to 3
 *   series) and lines (up to 4 series); 9:16 a single-series bar chart of non-negative values as a labelled bar list
 *   (`horizontal`: each category's label above its bar, the value at the bar's end - category labels under narrow
 *   vertical bars do not fit the 588 px text column at R-29's 44 px), and lines of up to 6 short categories; in a
 *   16:9 side panel the vertical layouts with fewer marks and shorter labels;
 * - a `graph` panel (`GraphSpec`): its functions sampled with the hardened math.js of the classic graph panel
 *   (player/panels/graphMath.js: the allow-list, adaptive sampling, the automatic y range), and its marked points.
 *
 * How (plan §2 "Chart.js: marks + gridlines only in canvas; every text is DOM, positioned by scale pixels, in texts();
 * built in layout()"; B2-0 (h) measured this order-independent):
 * - The canvas holds the **marks and gridlines only**: Chart.js with `animation: false`, `responsive: false`,
 *   `events: []`, `devicePixelRatio: 1`, built in `layoutChart` and redrawn with `update('none')` from the data of
 *   frame t (`paintChart`), so every frame is a pure function of t. Its axes draw no tick, no title and no border; the
 *   value and x scales get exactly the ticks this engine chose (`afterBuildTicks`) and exactly the plot box
 *   (`layout.padding` = PAD round a canvas PAD larger than the plot), so the scale's pixel of a value is this engine's
 *   own mapping (`xPixel` / `yPixel`, the formulas of Chart.js's linear and offset category scales; a jsdom test pins
 *   the two against the real Chart.js). A bar's thickness is set (`barThickness`), so Chart.js puts each bar exactly
 *   where `barRect` says.
 * - **Every text is page DOM** (the title, the axis titles, the tick labels, the series' direct labels, the values,
 *   the points' labels), positioned by those pixels, fitted on the page and reported through `chartTexts` with its
 *   slot, size, minimum and weight; nothing is ever drawn as canvas text. Series are **direct-labelled** (R-27: no
 *   legend): a label at the end of each line or curve, or beside the last group of bars, in the series' colour.
 * - Colours by role only (§4.2, §4.7): up to 3 series take entity colours (a series whose label is in the lesson's
 *   entity map keeps that entity's colour, R-30's one mapping), further line series `ink2` with dash patterns;
 *   gridlines are `ink` at 10% (§4.3), the zero line `ink2`. No structure role, no emphasis (cues.js draws look_here
 *   round the rects `chartFocusRect` gives it).
 *
 * Motion (§4.3, §4.8; every duration times the personality's scale, `_shared.js seconds`; `chartTimes`, pure): the
 * axes fade in (300 ms); bars grow (600 ms, 80 ms stagger, category by category) and their values count up with them
 * (a `count` event starts a value's own 1 s count-up); a line draws through its points (1.2 s over the series) and a
 * curve across its x range (1.2 s), series 300 ms apart; a `draw` event on `panel:bar:<s>:<i>` /
 * `panel:point:<s>:<i>` (events.py: the narration names that category or point) reveals that mark on its beat, a
 * `draw` on `panel` starts the whole build. A line waits at a point whose own beat has not come yet. Nothing leaves.
 *
 * Layout and fit (`layoutChart`, idempotent: the stage may lay out again when a late face arrives): every label slot
 * starts at its largest size and all of them step down together (4 px) until the plot keeps its minimum share of the
 * box and every label fits; a category label wraps to 2 lines in its band, and when one does not fit there the
 * labels are set on a 35-degree slant instead. What still does not fit at the minimum sizes is reported as overflow
 * (preflight then shrinks or blocks with "Shorten text", §7.12), never clipped and never left out. The value labels of
 * bars are the one thing left out when they do not fit (all of them, never some): they repeat what the value axis
 * already says.
 *
 * Caller API (a template keeps the parts in its view):
 *   chartPlan(panel, format)        pure: the plan, or null
 *   buildChart(ctx, plan, opts)     DOM once (no measuring); queues Chart.js (and math.js for a graph) on ctx.pending
 *   layoutChart(ctx, parts, box)    measure, fit, place, (re)build the Chart.js chart in `box` (stage px)
 *   paintChart(ctx, parts, t)       frame t (scene seconds), through ctx.paint; returns nothing
 *   chartTexts(ctx, parts)          TextEntry[] for preflight
 *   chartFocusRect(ctx, parts, target)   `panel`, `panel:bar:<s>:<i>`, `panel:point:<s>:<i>` (whole stage px)
 *   parts.el                        the dim group to append to the template's root and return from dimGroups
 * The source scan's template rules hold here as in every template (no clock, no emphasis, no will-change / filter,
 * literal style names only, no TeX, no `.timeline`). It imports nothing from `_shared.js`, so `_shared.js` can import it
 * for its side panels without an import cycle (its few helpers here - `SPACE`, `labelRange`, `dimGroup`, `scaled` -
 * are `_shared.js`'s `SPACE`, `slotRange`, `group` and `seconds`, pinned equal by chart_build.test.js).
 *
 * Every size and limit below is a B2 proposal pending the owner's review; `aadhi/compose/dynamic/charts.py` mirrors
 * LIMITS and `chartPlan`'s refusals (a test runs both on one corpus), so a Python `fit` never takes a chart the page
 * hands on.
 */

import { h } from '../../shared/dom.js';
import { loadLib } from '../../shared/libs.js';
import { ease } from '../../motion/easing.js';
import { progress } from '../../motion/track.js';
import { splitWords } from '../../motion/text.js';
import { stripRich } from '../../player/panels/util.js';
import { autoYRange, createSafeMath, decimalsOf, sampleFunction, splitSegments } from '../../player/panels/graphMath.js';
import { LAYOUT_TOKENS, TYPE_FLOORS, canvas2d, fixesOf, fx, parseTarget } from '../core.js';

/** @typedef {import('./types.js').TemplateContext} TemplateContext */
/** @typedef {import('./types.js').TextEntry} TextEntry */
/** @typedef {import('./types.js').FocusRect} FocusRect */
/** @typedef {import('../core.js').Box} Box */

// ---------------------------------------------------------------------------------------------
// The shared layer's rules, without importing it (see the module comment)
// ---------------------------------------------------------------------------------------------

/** The 8 px spacing steps (`_shared.js` SPACE: the catalogue's layout tokens). */
export const SPACE = Object.freeze([...LAYOUT_TOKENS.spacing.steps]);

/**
 * A label slot's size range with preflight's fixes and the R-29 label floor (`_shared.js` slotRange's rule: a slot's
 * own size cap and the safe layout are upper bounds, never below the floor).
 * @param {TemplateContext} ctx @param {string} slot @param {{min: number, max: number}} spec
 * @returns {{min: number, max: number}}
 */
export function labelRange(ctx, slot, spec) {
  const floor = TYPE_FLOORS[ctx.format].label_min;
  const fixes = fixesOf(ctx.look);
  const own = Object.prototype.hasOwnProperty.call(fixes.slots, slot) ? fixes.slots[slot].size : null;
  const min = Math.max(floor, spec.min);
  let max = Math.max(min, spec.max);
  if (fixes.safe) max = min;
  if (own !== null && own !== undefined) max = Math.max(min, Math.min(max, own));
  return { min, max };
}

/**
 * A dim group the stage owns (`_shared.js` group: its opacity is never written here).
 * @param {string} cls @param {...Node} children
 */
export function dimGroup(cls, ...children) {
  return h('div', { class: ['dyn-group', cls] }, ...children);
}

/** A duration in the personality's scale (`_shared.js` seconds). @param {TemplateContext} ctx @param {number} x */
export function scaled(ctx, x) {
  const k = ctx.motion && ctx.motion.scale > 0 ? ctx.motion.scale : 1;
  return x * k;
}

// ---------------------------------------------------------------------------------------------
// What is drawn (pure)
// ---------------------------------------------------------------------------------------------

/** Side-panel kinds this engine draws. */
export const CHART_PANEL_KINDS = Object.freeze(['chart', 'graph']);
/** `ChartSpec.chart_type`s it draws (pie, doughnut and radar: not yet). */
export const CHART_TYPES = Object.freeze(['bar', 'line']);
/** UI words (kept together for localisation). */
export const LABELS = Object.freeze({ series: 'Series', undefinedOnRange: 'not defined on this range' });

/**
 * What a chart may carry per format (B2 proposals, pending the owner's review; aadhi/compose/dynamic/charts.py LIMITS
 * mirrors them value for value). Counts: bar categories, bar series, bars in all, line categories, line series. Text
 * budgets in code points of the stored (raw) text, so Python and JS count the same: a category label must fit its band
 * on at most 2 lines (estimated at `charPx` per code point over a band of `plotW` / categories, words split on spaces)
 * or, when any label does not, every label is at most `rotatedChars` (they are then set on a slant); `categoryChars`
 * caps a 9:16 bar list's labels (one line of the 588 px column); `seriesChars`, `axisChars`, `functionChars` (a
 * function's label, else `y = <expr>`) and `pointChars` cap the other labels.
 */
export const LIMITS = Object.freeze({
  landscape: Object.freeze({
    barCategories: 12, barSeries: 3, bars: 24, lineCategories: 16, lineSeries: 4,
    plotW: 1000, charPx: 28, rotatedChars: 12, categoryChars: 120, seriesChars: 28, axisChars: 100,
    functionChars: 28, pointChars: 24,
  }),
  portrait: Object.freeze({
    barCategories: 4, barSeries: 1, bars: 4, lineCategories: 6, lineSeries: 3,
    plotW: 420, charPx: 30, rotatedChars: 8, categoryChars: 18, seriesChars: 14, axisChars: 18,
    functionChars: 14, pointChars: 12,
  }),
  // a 16:9 side panel (a box of about 760 x 520 at the least, worked_steps' and key_terms' right 45%): the vertical
  // layouts with fewer marks; a plot about 560 px wide
  panel: Object.freeze({
    barCategories: 6, barSeries: 2, bars: 8, lineCategories: 8, lineSeries: 3,
    plotW: 560, charPx: 28, rotatedChars: 10, categoryChars: 120, seriesChars: 20, axisChars: 40,
    functionChars: 20, pointChars: 16,
  }),
});
/** @typedef {'landscape' | 'portrait' | 'panel'} ChartLayout */

/**
 * @typedef {object} ChartSeries
 * @property {string} label     the drawn label ('' when the series has none and is the only one)
 * @property {string} raw       the stored label
 * @property {number[]} values  bar / line values, one per category
 */

/**
 * @typedef {object} GraphFunction
 * @property {string} expr
 * @property {string} label    the drawn label (its own, else `y = <expr>`)
 */

/**
 * @typedef {object} ChartPlan
 * @property {'bar' | 'line' | 'graph'} kind
 * @property {boolean} horizontal         a 9:16 bar list
 * @property {string[]} categories        drawn category labels (bar / line)
 * @property {ChartSeries[]} series       bar / line
 * @property {string} xTitle
 * @property {string} yTitle
 * @property {GraphFunction[]} functions  graph
 * @property {{x: number, y: number, label: string}[]} points   graph
 * @property {[number, number]} xRange    graph: the stored range (`plotRanges` widens it to hold every point)
 * @property {[number, number] | null} yRange   graph: the stored range, or null (automatic)
 */

/** Code points of a stored string (Python's `len`). @param {unknown} s */
export function codePoints(s) {
  return Array.from(typeof s === 'string' ? s : '').length;
}

/**
 * Lines a label takes when wrapped greedily at `perLine` code points per line, words split on single spaces (Python's
 * `str.split(" ")` with the empty pieces dropped); Infinity when a word is longer than a line. Pure.
 * @param {string} text @param {number} perLine
 */
export function wrapLines(text, perLine) {
  const words = String(text || '').split(' ').filter((w) => w.length > 0).map((w) => codePoints(w));
  if (!words.length) return 1;
  if (!(perLine >= 1)) return Infinity;
  let lines = 1;
  let used = -1;
  for (const w of words) {
    if (w > perLine) return Infinity;
    if (used < 0) used = w;
    else if (used + 1 + w <= perLine) used += 1 + w;
    else {
      lines += 1;
      used = w;
    }
  }
  return lines;
}

/**
 * Whether category labels fit a vertical chart's bands (see LIMITS): each on at most 2 lines of its band, else all of
 * them short enough to be slanted. Pure; charts.py `category_labels_fit` is the same arithmetic.
 * @param {readonly string[]} labels   stored labels
 * @param {ChartLayout} fmt   the layout class
 */
export function categoryLabelsFit(labels, fmt) {
  const L = LIMITS[fmt];
  const n = Math.max(1, labels.length);
  const perLine = Math.floor(L.plotW / n / L.charPx);
  const slanted = labels.some((l) => wrapLines(l, perLine) > 2);
  return !slanted || labels.every((l) => codePoints(l) <= L.rotatedChars);
}

/** @param {unknown} v */
const finite = (v) => typeof v === 'number' && Number.isFinite(v);
/** @param {unknown} v @returns {string} */
const str = (v) => (typeof v === 'string' ? v : '');

/**
 * The plan of a side panel (pure; see the module comment): null for a panel this engine does not draw in `format`
 * (another kind, no payload, a chart type it does not draw, past LIMITS, a value that is not a finite number, a label
 * past its budget). `aadhi/compose/dynamic/charts.py chart_problem` refuses exactly the same panels.
 * @param {any} panel   SidePanel (`ResolvedSidePanel.panel`)
 * @param {ChartLayout} format   the layout class (chart_build: its format; a side panel: `panel`)
 * @returns {ChartPlan | null}
 */
export function chartPlan(panel, format) {
  if (!panel || typeof panel !== 'object') return null;
  const fmt = format === 'portrait' || format === 'panel' ? format : 'landscape';
  const L = LIMITS[fmt];
  const xTitleRaw = str(panel.kind === 'chart' ? panel.chart && panel.chart.x_label : panel.graph && panel.graph.x_label);
  const yTitleRaw = str(panel.kind === 'chart' ? panel.chart && panel.chart.y_label : panel.graph && panel.graph.y_label);
  if (codePoints(xTitleRaw) > L.axisChars || codePoints(yTitleRaw) > L.axisChars) return null;
  if (panel.kind === 'chart') {
    const spec = panel.chart;
    if (!spec || typeof spec !== 'object') return null;
    const type = spec.chart_type === undefined || spec.chart_type === null ? 'bar' : spec.chart_type;
    if (type !== 'bar' && type !== 'line') return null;
    const labels = spec.labels;
    const datasets = spec.datasets;
    if (!Array.isArray(labels) || !labels.length || !labels.every((l) => typeof l === 'string')) return null;
    if (!Array.isArray(datasets) || !datasets.length) return null;
    const n = labels.length;
    const ns = datasets.length;
    for (const ds of datasets) {
      if (!ds || typeof ds !== 'object' || !Array.isArray(ds.data) || ds.data.length !== n || !ds.data.every(finite)) return null;
      if (ds.label !== undefined && ds.label !== null && typeof ds.label !== 'string') return null;
      if (codePoints(ds.label) > L.seriesChars) return null;
    }
    const horizontal = type === 'bar' && fmt === 'portrait';
    if (type === 'bar') {
      if (n > L.barCategories || ns > L.barSeries || n * ns > L.bars) return null;
      if (horizontal) {
        if (datasets.some((ds) => ds.data.some((/** @type {number} */ v) => v < 0))) return null;
        if (labels.some((l) => codePoints(l) > L.categoryChars)) return null;
      }
    } else if (n > L.lineCategories || ns > L.lineSeries) {
      return null;
    }
    if (!horizontal && !categoryLabelsFit(labels, fmt)) return null;
    return {
      kind: type, horizontal,
      categories: labels.map((l) => stripRich(l)),
      series: datasets.map((ds, i) => {
        const own = stripRich(str(ds.label));
        return { label: own || (ns > 1 ? `${LABELS.series} ${i + 1}` : ''), raw: str(ds.label), values: ds.data.map(Number) };
      }),
      xTitle: stripRich(xTitleRaw), yTitle: stripRich(yTitleRaw),
      functions: [], points: [], xRange: [0, 1], yRange: null,
    };
  }
  if (panel.kind === 'graph') {
    const spec = panel.graph;
    if (!spec || typeof spec !== 'object') return null;
    const fns = Array.isArray(spec.functions) ? spec.functions : [];
    const pts = Array.isArray(spec.points) ? spec.points : [];
    if (!fns.length && !pts.length) return null;
    if (fns.length > 4 || pts.length > 20) return null;
    const xr = spec.x_range === undefined || spec.x_range === null ? [-5, 5] : spec.x_range;
    if (!Array.isArray(xr) || xr.length !== 2 || !xr.every(finite) || !(xr[1] > xr[0])) return null;
    const yr = spec.y_range === undefined ? null : spec.y_range;
    if (yr !== null && (!Array.isArray(yr) || yr.length !== 2 || !yr.every(finite) || !(yr[1] > yr[0]))) return null;
    /** @type {GraphFunction[]} */
    const functions = [];
    for (const fn of fns) {
      if (!fn || typeof fn !== 'object' || typeof fn.expr !== 'string' || !fn.expr) return null;
      if (fn.label !== undefined && fn.label !== null && typeof fn.label !== 'string') return null;
      const shown = fn.label ? fn.label : `y = ${fn.expr}`;
      if (codePoints(shown) > L.functionChars) return null;
      functions.push({ expr: fn.expr, label: stripRich(fn.label) || `y = ${fn.expr.trim()}` });
    }
    /** @type {{x: number, y: number, label: string}[]} */
    const points = [];
    for (const p of pts) {
      if (!p || typeof p !== 'object' || !finite(p.x) || !finite(p.y)) return null;
      if (p.label !== undefined && p.label !== null && typeof p.label !== 'string') return null;
      if (codePoints(p.label) > L.pointChars) return null;
      points.push({ x: p.x, y: p.y, label: stripRich(str(p.label)) });
    }
    return {
      kind: 'graph', horizontal: false, categories: [], series: [], xTitle: stripRich(xTitleRaw), yTitle: stripRich(yTitleRaw),
      functions, points, xRange: [xr[0], xr[1]], yRange: yr ? [yr[0], yr[1]] : null,
    };
  }
  return null;
}

/**
 * Whether this engine draws a `chart` / `graph` panel in layout `format` (one `chartPlan` accepts).
 * @param {any} panel @param {ChartLayout} format
 */
export function drawsChart(panel, format) {
  return chartPlan(panel, format) !== null;
}

// ---------------------------------------------------------------------------------------------
// Sizes and motion (B2 proposals, pending the owner's review)
// ---------------------------------------------------------------------------------------------

/**
 * Label slots per format: size range (px), weight, max lines. 16:9 labels 40-48 (R-29: labels >= 40, never < 36);
 * 9:16 44-52 at weight >= 600 (R-29: labels >= 44, weight >= 600 at <= 64 px). `tick`: tick and category labels;
 * `value`: a bar's or a point's value (tabular figures); `axis`: an axis title; `series`: a series' direct label, a
 * function's label or a marked point's label; `chart_title`: the title a caller asks for (a side panel's own title,
 * `buildChart` opts.title), at the top of the box.
 */
export const SIZES = Object.freeze({
  landscape: Object.freeze({
    tick: Object.freeze({ max: 44, min: 40, weight: 400, lines: 2 }),
    value: Object.freeze({ max: 44, min: 40, weight: 700, lines: 1 }),
    axis: Object.freeze({ max: 44, min: 40, weight: 600, lines: 2 }),
    series: Object.freeze({ max: 44, min: 40, weight: 700, lines: 2 }),
    chart_title: Object.freeze({ max: 48, min: 40, weight: 700, lines: 2 }),
  }),
  portrait: Object.freeze({
    tick: Object.freeze({ max: 48, min: 44, weight: 600, lines: 2 }),
    value: Object.freeze({ max: 48, min: 44, weight: 700, lines: 1 }),
    axis: Object.freeze({ max: 48, min: 44, weight: 600, lines: 2 }),
    series: Object.freeze({ max: 48, min: 44, weight: 700, lines: 2 }),
    chart_title: Object.freeze({ max: 52, min: 44, weight: 700, lines: 2 }),
  }),
});
/** The slot names a chart reports (`ResolvedLook.fixes.slots` keys); `chart_title` only for a caller's `title`. */
export const SLOTS = Object.freeze(['tick', 'value', 'axis', 'series', 'chart_title']);
/** Size steps when fitting (board_generic's and textfit.py's 4 px). */
export const SIZE_STEP = 4;
/** Line heights (§4.6 type scale's body role; Indic text the Indic minimum, plan §2 R-29). */
export const LINE_HEIGHT = Object.freeze({ latin: 1.35, indic: 1.45 });
const INDIC = new RegExp(`[${['Devanagari', 'Bengali', 'Gurmukhi', 'Gujarati', 'Oriya', 'Tamil', 'Telugu', 'Kannada',
  'Malayalam', 'Sinhala'].map((x) => `\\p{Script=${x}}`).join('')}]`, 'u');
/** @param {string} text */
export function lineHeightOf(text) {
  return INDIC.test(String(text || '')) ? LINE_HEIGHT.indic : LINE_HEIGHT.latin;
}

/** Gap between a label and the plot or another label (the spacing scale's 16 px). */
export const GAP = SPACE[1];
/** The canvas reaches this far past the plot box on every side (line caps, point markers: STROKE.point + a 3 px ring). */
export const PAD = 16;
/** A value label sits this far from its bar's end. */
export const VALUE_GAP = SPACE[0];
/** The plot keeps at least this share of the box's height and width, or the labels shrink. */
export const MIN_PLOT = Object.freeze({ h: 0.3, w: 0.45 });
/** Category labels that do not fit their band on 2 lines are set at this slant (degrees, rising to the right). */
export const SLANT = 35;
const SLANT_SIN = Math.sin((SLANT * Math.PI) / 180);
const SLANT_COS = Math.cos((SLANT * Math.PI) / 180);
/** Value-axis ticks are at least this many tick-label heights apart where the plot allows (a label's height + 8 px
 * at the closest, else the plot is too short). */
export const TICK_ROOM = 1.6;
/** A series label column (16:9 lines, all bars) takes at most this share of the box's width. */
export const COLUMN_SHARE = 0.25;
/** Boxes at least this wide label lines and curves in a column right of the plot; narrower ones inside the plot. */
export const COLUMN_MIN_W = 1000;
/** Line charts show each point's value when they have one series of at most this many points. */
export const LINE_VALUES_MAX = 8;
/** Marks: gridlines and the zero line (px), a line's width, a point's radius, a 9:16 bar list's bar thickness. */
export const STROKE = Object.freeze({ grid: 2, zero: 2, line: 6, point: 10, hbar: 24, barRadius: 6 });
/** Gridlines are `ink` at this alpha (§4.3 "gridlines at 10% alpha"). */
export const GRID_ALPHA = 0.1;
/** Line dash patterns for line series past the entity colours (§4.3 "ink2 tints with dash patterns"). */
export const DASHES = Object.freeze([Object.freeze([18, 10]), Object.freeze([6, 8]), Object.freeze([24, 8, 6, 8])]);
/** Of a 16:9 bar band, the bars take this share (the rest is the gap between categories). */
export const BAR_SHARE = 0.72;
/** Most tick labels a value axis or a graph's x axis shows. */
export const TICKS_MAX = Object.freeze({ y: 8, x: 11 });
/** Sampling budget of a graph's function (player/panels/graphMath.js sampleFunction). */
export const SAMPLES = Object.freeze({ initial: 160, maxDepth: 6, maxPoints: 1200 });

// Motion (§4.3 chart_build, §4.8), seconds before the personality's scale
/** The axes (gridlines, tick and axis labels) fade in over this (§4.3 "Axes fade in (300 ms)"). */
export const AXES_ENTER = 0.3;
/** A bar grows over this, the next one this much later (§4.3 "bars grow (600 ms, 80 ms stagger)"). */
export const BAR_GROW = 0.6;
export const BAR_STAGGER = 0.08;
/** A line draws through its points, a curve across its range, over this (§4.3 "the line draws (1–1.5 s)"). */
export const LINE_DRAW = 1.2;
/** Series start this far apart. */
export const SERIES_STAGGER = 0.3;
/** A `count` event's own count-up (§4.8 "count-up 800–1200 ms"). */
export const COUNT_UP = 1.0;
/** Labels that appear with a mark (a value, a series label, a point) fade in over this. */
export const LABEL_FADE = 0.25;
/** A graph's marked points follow its curves this far apart. */
export const POINT_STAGGER = 0.16;

// ---------------------------------------------------------------------------------------------
// Numbers (pure)
// ---------------------------------------------------------------------------------------------

/**
 * `value` with exactly `decimals` decimals in the scene's locale (grouping included; the typographic minus).
 * @param {number} value @param {number} decimals @param {string} [locale]
 */
export function formatNumber(value, decimals, locale) {
  const d = Math.max(0, Math.min(6, Math.round(decimals) || 0));
  const v = Math.abs(value) < 0.5 * 10 ** -d ? 0 : value;
  let s;
  try {
    s = new Intl.NumberFormat(locale || 'en', { minimumFractionDigits: d, maximumFractionDigits: d, useGrouping: true }).format(v);
  } catch {
    s = v.toFixed(d);
  }
  return s.replace(/^-/, '−');
}

/** The decimals a value is printed with (its shortest form, at most 4). @param {number} value */
export function valueDecimals(value) {
  if (!Number.isFinite(value) || value === 0) return 0;
  const s = String(Number(Math.abs(value).toPrecision(6)));
  if (s.includes('e-')) return 4;
  const dot = s.indexOf('.');
  return dot < 0 ? 0 : Math.min(4, s.length - dot - 1);
}

/**
 * The "nice" steps (1, 2, 2.5, 5 x 10^k) from well below `span / most` upwards, in ascending order. Pure.
 * @param {number} span @param {number} most
 * @returns {number[]}
 */
function niceSteps(span, most) {
  const k0 = Math.floor(Math.log10(span / Math.max(1, most))) - 1;
  /** @type {number[]} */
  const out = [];
  for (let k = k0; k <= k0 + 4; k++) {
    for (const m of [1, 2, 2.5, 5]) out.push(Number((m * 10 ** k).toPrecision(12)));
  }
  return out;
}

/** The multiples k*step for k in [first, last], cleaned of float noise and -0. @param {number} first @param {number} last @param {number} step */
function multiples(first, last, step) {
  /** @type {number[]} */
  const ticks = [];
  for (let k = first; k <= last && ticks.length < 64; k++) {
    const v = Number((k * step).toPrecision(12));
    ticks.push(Object.is(v, -0) || Math.abs(v) < step * 1e-9 ? 0 : v);
  }
  return ticks;
}

/**
 * Nice ticks (steps of 1, 2, 2.5, 5 x 10^k) whose first is at or below `lo` and last at or above `hi`: the smallest
 * step that needs at most `most` of them (never fewer than 2); when none does (a range across 0 needs 3 at the
 * fewest), the step that needs the fewest, the smallest of those. A value axis that holds every value. Pure.
 * @param {number} lo @param {number} hi @param {number} most
 * @returns {{ticks: number[], step: number}}
 */
export function coverTicks(lo, hi, most) {
  let a = lo;
  let b = hi;
  if (!(b > a)) {
    const d = Math.max(1, Math.abs(a) * 0.5);
    a -= a === 0 ? 0 : d;
    b += d;
  }
  const cap = Math.max(2, Math.floor(most));
  let best = { first: 0, last: 0, step: 1, count: Infinity };
  for (const step of niceSteps(b - a, cap)) {
    const first = Math.floor(a / step + 1e-9);
    const last = Math.ceil(b / step - 1e-9);
    const count = last - first + 1;
    if (count <= cap) return { ticks: multiples(first, last, step), step };
    if (count < best.count) best = { first, last, step, count };
  }
  return { ticks: multiples(best.first, best.last, best.step), step: best.step };
}

/**
 * Nice ticks inside [lo, hi] (a range that is kept): the smallest step that gives at most `most` of them and at
 * least 2; when no step gives both, the largest step that still gives 2. Pure.
 * @param {number} lo @param {number} hi @param {number} most
 * @returns {{ticks: number[], step: number}}
 */
export function insideTicks(lo, hi, most) {
  const cap = Math.max(2, Math.floor(most));
  const steps = niceSteps(hi - lo, cap);
  /** @type {{ticks: number[], step: number} | null} */
  let two = null;
  for (const step of steps) {
    const first = Math.ceil(lo / step - 1e-9);
    const last = Math.floor(hi / step + 1e-9);
    const count = last - first + 1;
    if (count >= 2 && count <= cap) return { ticks: multiples(first, last, step), step };
    if (count >= 2) two = { ticks: multiples(first, last, step), step };
  }
  return two || { ticks: [lo, hi], step: hi - lo };
}

/**
 * The span of the values a bar or line chart's value axis must hold (a bar chart's from 0, as `valueAxis`). Pure.
 * @param {ChartPlan} plan
 */
export function valueSpan(plan) {
  const all = plan.series.flatMap((s) => s.values);
  let lo = Math.min(...all);
  let hi = Math.max(...all);
  if (plan.kind === 'bar') {
    lo = Math.min(0, lo);
    hi = Math.max(0, hi);
  }
  return Math.max(1e-9, hi - lo);
}

/**
 * The value axis of a bar or line chart, at most `most` ticks: bars always hold 0 (a bar grows from its base, §4.3);
 * a line holds 0 when its values come close to it (autoYRange's rule). Pure.
 * @param {ChartPlan} plan @param {number} most
 */
export function valueAxis(plan, most) {
  const all = plan.series.flatMap((s) => s.values);
  let lo = Math.min(...all);
  let hi = Math.max(...all);
  if (plan.kind === 'bar') {
    lo = Math.min(0, lo);
    hi = Math.max(0, hi);
    if (lo === 0 && hi === 0) hi = 1;
  } else {
    const span = hi - lo;
    if (lo > 0 && lo < span * 0.25) lo = 0;
    if (hi < 0 && -hi < span * 0.25) hi = 0;
  }
  const t = coverTicks(lo, hi, most);
  return { min: t.ticks[0], max: t.ticks[t.ticks.length - 1], ticks: t.ticks, step: t.step };
}

// ---------------------------------------------------------------------------------------------
// Graph model (math.js through the hardened instance; pure given it)
// ---------------------------------------------------------------------------------------------

/**
 * @typedef {object} GraphModel
 * @property {[number, number]} xRange
 * @property {[number, number]} yRange
 * @property {{label: string, points: {x: number, y: number}[], ok: boolean, end: {x: number, y: number} | null}[]} curves
 *   each function's polyline (segments separated by a `y: NaN` point), whether it is defined anywhere on the range,
 *   and where its curve ends (its last finite point inside the y range: the direct label's anchor)
 */

/**
 * Sample a graph plan's functions (graphMath's adaptive sampling) and fix its ranges: the stored x range widened to
 * hold every marked point (5% of the span round them; never dropped), the stored y range likewise, else autoYRange of
 * the samples and the points. A function that does not compile or is undefined on the whole range draws no curve; its
 * label then says so. Pure given `safe`.
 * @param {ChartPlan} plan
 * @param {import('../../player/panels/graphMath.js').SafeMath | null} safe
 * @returns {GraphModel}
 */
export function graphModel(plan, safe) {
  let [x0, x1] = plan.xRange;
  const px = plan.points.map((p) => p.x);
  if (px.length && (Math.min(...px) < x0 || Math.max(...px) > x1)) {
    const lo = Math.min(x0, ...px);
    const hi = Math.max(x1, ...px);
    const pad = (hi - lo) * 0.05;
    x0 = Math.min(x0, Math.min(...px) - pad);
    x1 = Math.max(x1, Math.max(...px) + pad);
  }
  /** @type {number[]} */
  const uniform = [];
  const sampled = plan.functions.map((fn) => {
    if (!safe) return { label: fn.label, samples: null };
    try {
      const f = safe.compileExpr(fn.expr);
      const samples = sampleFunction(f, x0, x1, SAMPLES);
      if (!samples.uniform.some(Number.isFinite)) return { label: fn.label, samples: null };
      uniform.push(...samples.uniform);
      return { label: fn.label, samples };
    } catch {
      return { label: fn.label, samples: null };
    }
  });
  /** @type {[number, number]} */
  let yRange;
  const py = plan.points.map((p) => p.y);
  if (plan.yRange) {
    let [y0, y1] = plan.yRange;
    if (py.length && (Math.min(...py) < y0 || Math.max(...py) > y1)) {
      const pad = (Math.max(y1, ...py) - Math.min(y0, ...py)) * 0.05;
      y0 = Math.min(y0, Math.min(...py) - pad);
      y1 = Math.max(y1, Math.max(...py) + pad);
    }
    yRange = [y0, y1];
  } else {
    yRange = autoYRange(uniform, py);
  }
  const curves = sampled.map((c) => {
    if (!c.samples) return { label: c.label, points: [], ok: false, end: null };
    const segs = splitSegments(c.samples.points, yRange, c.samples.minDx);
    /** @type {{x: number, y: number}[]} */
    const points = [];
    segs.forEach((seg, k) => {
      if (k) points.push({ x: seg[0].x, y: NaN });
      for (const p of seg) points.push({ x: p.x, y: p.y });
    });
    let end = null;
    for (let k = points.length - 1; k >= 0; k--) {
      const p = points[k];
      if (Number.isFinite(p.y) && p.y >= yRange[0] && p.y <= yRange[1]) {
        end = { x: p.x, y: p.y };
        break;
      }
    }
    return { label: c.label, points, ok: points.length > 0, end };
  });
  return { xRange: [x0, x1], yRange, curves };
}

// ---------------------------------------------------------------------------------------------
// Timing (pure)
// ---------------------------------------------------------------------------------------------

/**
 * @typedef {object} ChartTimes
 * @property {number} axes                      the axes fade in from here
 * @property {number[][]} bars                  [series][category] a bar starts growing
 * @property {(number | null)[][]} counts       [series][category] a `count` event's own count-up start, else null
 * @property {{start: number, arrive: number[], depart: number[]}[]} lines   per series: when the line leaves each
 *   point (`depart`, the segment toward point i starts) and reaches it (`arrive`); point 0 arrives at `start`
 * @property {{start: number, end: number}[]} curves   per graph function
 * @property {number[]} points                  per marked graph point
 */

/**
 * When everything enters (see the module comment): `axesAt` is the caller's (the panel's own start), durations are
 * scaled by `scale` (the personality's, `_shared.js seconds`), `events` are the scene's `draw` / `count` events (the
 * caller hands only those it consumes). Pure.
 * @param {ChartPlan} plan
 * @param {readonly {t?: unknown, kind?: unknown, target?: unknown}[]} events
 * @param {number} axesAt
 * @param {number} scale
 * @returns {ChartTimes}
 */
export function chartTimes(plan, events, axesAt, scale) {
  const S = (/** @type {number} */ x) => x * (scale > 0 ? scale : 1);
  /** @type {Map<string, number>} */
  const draws = new Map();
  /** @type {Map<string, number>} */
  const counts = new Map();
  for (const e of events || []) {
    if (!e || typeof e.t !== 'number' || !Number.isFinite(e.t) || typeof e.target !== 'string') continue;
    const map = e.kind === 'draw' ? draws : e.kind === 'count' ? counts : null;
    if (!map) continue;
    const parsed = parseTarget(e.target);
    if (!parsed || !['panel', 'panel:bar', 'panel:point'].includes(parsed.form)) continue;
    const key = parsed.form === 'panel' ? 'panel' : `${parsed.series}:${parsed.index}`;
    if (!map.has(key) || /** @type {number} */ (map.get(key)) > e.t) map.set(key, e.t);
  }
  const axes = Math.max(0, axesAt);
  const panelDraw = draws.has('panel') ? Math.max(axes, /** @type {number} */ (draws.get('panel'))) : null;
  const first = panelDraw !== null ? Math.max(panelDraw, axes + S(AXES_ENTER)) : axes + S(AXES_ENTER);
  /** @param {string} key @returns {number | null} */
  const own = (key) => (draws.has(key) ? Math.max(axes, /** @type {number} */ (draws.get(key))) : null);
  const panelCount = counts.has('panel') ? /** @type {number} */ (counts.get('panel')) : null;
  const ns = plan.series.length;
  const n = plan.categories.length;
  /** @type {number[][]} */
  const bars = plan.series.map(() => new Array(n).fill(0));
  /** @type {(number | null)[][]} */
  const cnt = plan.series.map(() => new Array(n).fill(null));
  /** @type {{start: number, arrive: number[], depart: number[]}[]} */
  const lines = [];
  if (plan.kind === 'bar') {
    for (let i = 0; i < n; i++) {
      for (let s = 0; s < ns; s++) {
        const k = i * ns + s;
        const mine = own(`${s}:${i}`);
        bars[s][i] = mine !== null ? mine : first + k * S(BAR_STAGGER);
        const c = counts.has(`${s}:${i}`) ? /** @type {number} */ (counts.get(`${s}:${i}`)) : panelCount;
        cnt[s][i] = c === null ? null : Math.max(c, bars[s][i]);
      }
    }
  }
  if (plan.kind === 'line') {
    const seg = S(LINE_DRAW) / Math.max(1, n - 1);
    for (let s = 0; s < ns; s++) {
      const mine0 = own(`${s}:0`);
      const start = mine0 !== null ? mine0 : first + s * S(SERIES_STAGGER);
      const arrive = [start];
      const depart = [start];
      for (let i = 1; i < n; i++) {
        const mine = own(`${s}:${i}`);
        const d = mine !== null ? Math.max(arrive[i - 1], mine) : arrive[i - 1];
        depart.push(d);
        arrive.push(d + seg);
      }
      lines.push({ start, arrive, depart });
      for (let i = 0; i < n; i++) {
        const c = counts.has(`${s}:${i}`) ? /** @type {number} */ (counts.get(`${s}:${i}`)) : panelCount;
        cnt[s][i] = c === null ? null : Math.max(c, arrive[i]);
      }
    }
  }
  /** @type {{start: number, end: number}[]} */
  const curves = plan.functions.map((_, s) => {
    const mine = own(`${s}:0`);
    const start = mine !== null ? mine : first + s * S(SERIES_STAGGER);
    return { start, end: start + S(LINE_DRAW) };
  });
  const afterCurves = curves.length ? Math.max(...curves.map((c) => c.end)) : first;
  const F = plan.functions.length;
  const points = plan.points.map((_, j) => {
    const mine = own(`${F}:${j}`);
    return mine !== null ? mine : afterCurves + j * S(POINT_STAGGER);
  });
  return { axes, bars, counts: cnt, lines, curves, points };
}

// ---------------------------------------------------------------------------------------------
// Colours (§4.7: content roles only)
// ---------------------------------------------------------------------------------------------

/**
 * Each series' colour role and dash: a series whose label is in the lesson's entity map keeps that entity's role
 * (R-30), the rest take the free entity slots in order; past the three, `ink2` with a dash pattern (lines only:
 * LIMITS keeps bars at 3 series). Graph functions likewise; a graph's marked points are `ink`.
 * @param {TemplateContext} ctx @param {string[]} labels
 * @returns {{role: string, dash: number[]}[]}
 */
export function seriesRoles(ctx, labels) {
  /** @type {(string | null)[]} */
  const mapped = labels.map((l) => (typeof ctx.entityRole === 'function' && l ? ctx.entityRole(l) : null));
  const used = new Set();
  /** @type {(string | null)[]} */
  const roles = mapped.map((r) => {
    if (r && !used.has(r)) {
      used.add(r);
      return r;
    }
    return null;
  });
  const free = ['entity:0', 'entity:1', 'entity:2'].filter((r) => !used.has(r));
  let dash = 0;
  return roles.map((r) => {
    if (r) return { role: r, dash: [] };
    const next = free.shift();
    if (next) return { role: next, dash: [] };
    return { role: 'ink2', dash: [...DASHES[dash++ % DASHES.length]] };
  });
}

/** `#rrggbb` + an alpha in [0, 1] as `#rrggbbaa`. @param {string} hex @param {number} a */
export function withAlpha(hex, a) {
  const k = Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, '0');
  return `${String(hex).slice(0, 7)}${k}`;
}

// ---------------------------------------------------------------------------------------------
// Labels
// ---------------------------------------------------------------------------------------------

/**
 * One text label: `wrap` is placed (and slanted) at layout time, `el` holds the text and is painted (opacity), so the
 * two never write the same property.
 * @typedef {object} Label
 * @property {HTMLElement} wrap
 * @property {HTMLElement} el
 * @property {HTMLElement[]} words
 * @property {string} slot        'tick' | 'value' | 'axis' | 'series'
 * @property {string} text
 * @property {string} ink
 * @property {number} size
 * @property {number} min
 * @property {number} weight
 * @property {number} lh
 * @property {number} alpha
 * @property {number} overflow
 * @property {boolean} shown       laid out and drawn (unused pool labels and left-out values are not)
 * @property {boolean} nowrap
 * @property {{x: number, y: number, w: number, h: number}} box   laid-out box, stage px (unslanted)
 */

/**
 * @param {TemplateContext} ctx @param {string} slot @param {string} text @param {string} ink
 * @param {{nowrap?: boolean, align?: 'left' | 'right' | 'center', tabular?: boolean}} [opts]
 * @returns {Label}
 */
function makeLabel(ctx, slot, text, ink, opts = {}) {
  const S = SIZES[ctx.format][/** @type {'tick' | 'value' | 'axis' | 'series' | 'chart_title'} */ (slot)];
  const el = h('div', {
    class: ['dyn-chart-text', `dyn-chart-${slot}`], dir: 'auto',
    style: {
      margin: '0px', color: ink, fontFamily: 'var(--dyn-font-body)', fontWeight: String(S.weight),
      lineHeight: String(lineHeightOf(text)), textAlign: opts.align || 'left', overflowWrap: 'normal',
      whiteSpace: opts.nowrap ? 'nowrap' : 'normal', fontVariantNumeric: opts.tabular ? 'tabular-nums' : 'normal',
    },
  });
  /** @type {HTMLElement[]} */
  const words = [];
  for (const part of splitWords(text, ctx.locale)) {
    const span = h('span', { class: 'dyn-word', text: part.text });
    words.push(span);
    el.append(span, part.ws ? document.createTextNode(part.ws) : '');
  }
  const wrap = h('div', { class: 'dyn-chart-label', style: { position: 'absolute', left: '0px', top: '0px' } }, el);
  return {
    wrap, el, words, slot, text, ink, size: S.max, min: S.min, weight: S.weight, lh: lineHeightOf(text), alpha: 0,
    overflow: 0, shown: false, nowrap: !!opts.nowrap, box: { x: 0, y: 0, w: 0, h: 0 },
  };
}

// ---------------------------------------------------------------------------------------------
// Building (DOM once; no measuring)
// ---------------------------------------------------------------------------------------------

/**
 * What `buildChart` makes (the caller keeps it and hands it back).
 * @typedef {object} ChartParts
 * @property {ChartPlan} plan
 * @property {HTMLElement} el          the dim group (append it to the template's root; return it from dimGroups)
 * @property {HTMLElement} frame       the chart's box, placed by layoutChart
 * @property {HTMLCanvasElement} canvas
 * @property {HTMLElement} measure     hidden measuring line (layout only)
 * @property {{ink: string, ink2: string, bg: string | null}} colours   text inks and the colour behind the text
 * @property {{role: string, colour: string, dash: number[]}[]} styles  per series (or graph function)
 * @property {Label | null} title      the caller's title (opts.title), at the top of the box
 * @property {Label | null} xTitle
 * @property {Label | null} yTitle
 * @property {Label[]} cats            category labels (bar / line)
 * @property {Label[]} yTicks          pool (value axis, graph y)
 * @property {Label[]} xTicks          pool (graph x)
 * @property {Label[]} seriesLabels    per series / function (null entries: none drawn)
 * @property {(Label | null)[][]} values   [series][category] (bars; one-series lines of few points)
 * @property {Label[]} pointLabels     graph points (labelled ones)
 * @property {ChartTimes | null} times
 * @property {GraphModel | null} model
 * @property {any} Chart               the Chart.js constructor once loaded
 * @property {any} chart               the Chart.js instance (null without a canvas context: jsdom)
 * @property {string} failed           why the libraries did not load ('' when they did)
 * @property {any} geo                 the laid-out geometry (layoutChart)
 * @property {{x0: number, y0: number}} origin   the frame's top left (stage px): labels are placed relative to it
 * @property {string} lastKey          the data the canvas holds now (update('none') only when it changes)
 * @property {number} axesAlpha        painted now (texts / focus)
 * @property {number[][]} grow         [series][category] eased bar growth / point arrival now
 * @property {number[]} pointAlpha     a graph's marked points: shown now
 */

/**
 * The chart's DOM, once per scene (build time): the frame (a dim group of its own), its canvas and every label; queues
 * Chart.js (and, for a graph, math.js) on `ctx.pending`, so `layoutChart` runs once they are in.
 * @param {TemplateContext} ctx
 * @param {ChartPlan} plan
 * @param {{ink?: string, ink2?: string, bg?: string | null, start?: number, title?: string,
 *   events?: readonly {t?: unknown, kind?: unknown, target?: unknown}[]}} [opts]   the inks of the text and the colour
 *   behind it (default: the content inks on the scene background, `ctx.onBackground`); `start`: when the axes start to
 *   fade in (scene seconds; default 0); `events`: the scene's `draw` / `count` events the caller consumes (default
 *   none); `title`: a title drawn at the top of the box (a side panel's own title; plain text; default none)
 * @returns {ChartParts}
 */
export function buildChart(ctx, plan, opts = {}) {
  const ink = opts.ink || ctx.onBackground.ink;
  const ink2 = opts.ink2 || ctx.onBackground.ink2;
  const bg = opts.bg === undefined ? ctx.onBackground.bg : opts.bg;
  const frame = h('div', { class: 'dyn-chart-frame', style: { position: 'absolute', left: '0px', top: '0px' } });
  const canvas = /** @type {HTMLCanvasElement} */ (h('canvas', { class: 'dyn-chart-canvas', 'aria-hidden': 'true' }));
  Object.assign(canvas.style, { position: 'absolute', left: '0px', top: '0px' });
  const measure = h('div', {
    class: 'dyn-chart-measure', 'aria-hidden': 'true',
    style: { position: 'absolute', left: '0px', top: '0px', visibility: 'hidden', whiteSpace: 'nowrap', fontFamily: 'var(--dyn-font-body)' },
  });
  frame.append(canvas, measure);
  const names = plan.kind === 'graph' ? plan.functions.map((f) => f.label) : plan.series.map((s) => s.label || s.raw);
  const styles = seriesRoles(ctx, names).map((r) => ({ ...r, colour: ctx.roles.color(r.role) }));
  /** @param {Label} l */
  const add = (l) => {
    frame.appendChild(l.wrap);
    return l;
  };
  const ownTitle = typeof opts.title === 'string' ? stripRich(opts.title) : '';
  const title = ownTitle ? add(makeLabel(ctx, 'chart_title', ownTitle, ink, { align: 'center' })) : null;
  const xTitle = plan.xTitle ? add(makeLabel(ctx, 'axis', plan.xTitle, ink2, { align: 'center' })) : null;
  const yTitle = plan.yTitle ? add(makeLabel(ctx, 'axis', plan.yTitle, ink2, { align: plan.horizontal ? 'right' : 'left' })) : null;
  const cats = plan.categories.map((c) => add(makeLabel(ctx, 'tick', c, plan.horizontal ? ink : ink2, { align: plan.horizontal ? 'left' : 'center' })));
  const yTicks = plan.horizontal ? [] : Array.from({ length: TICKS_MAX.y }, () => add(makeLabel(ctx, 'tick', '0', ink2, { nowrap: true, align: 'right', tabular: true })));
  const xTicks = plan.kind === 'graph' ? Array.from({ length: TICKS_MAX.x }, () => add(makeLabel(ctx, 'tick', '0', ink2, { nowrap: true, align: 'center', tabular: true }))) : [];
  // a series' own label: every series of several, a one-series chart's when it says something the value axis does
  // not (a one-series label with no y title becomes the y title, below)
  const single = plan.kind !== 'graph' && plan.series.length === 1;
  const seriesTexts = plan.kind === 'graph'
    ? plan.functions.map((f) => f.label)
    : plan.series.map((s) => (single && (!s.label || !plan.yTitle || s.label === plan.yTitle) ? '' : s.label));
  /** @type {Label[]} */
  const seriesLabels = seriesTexts.map((text, s) => {
    const l = makeLabel(ctx, 'series', text, styles[s] && styles[s].role !== 'ink2' ? styles[s].colour : ink, { nowrap: false });
    return add(l);
  });
  let yTitleLabel = yTitle;
  if (single && !plan.yTitle && plan.series[0].label) {
    yTitleLabel = add(makeLabel(ctx, 'axis', plan.series[0].label, ink2, { align: plan.horizontal ? 'right' : 'left' }));
  }
  /** @type {(Label | null)[][]} */
  const values = plan.series.map((s, si) => s.values.map((v) => {
    const own = plan.kind === 'bar' || (plan.kind === 'line' && single && plan.categories.length <= LINE_VALUES_MAX);
    if (!own) return null;
    const final = formatNumber(v, valueDecimals(v), ctx.locale);
    return add(makeLabel(ctx, 'value', final, ink, { nowrap: true, align: 'center', tabular: true }));
  }));
  const pointLabels = plan.points.map((p) => add(makeLabel(ctx, 'series', p.label, ink, { nowrap: true })));
  const el = dimGroup('dyn-chart', frame);
  Object.assign(el.style, { position: 'absolute', left: '0px', top: '0px', right: '0px', bottom: '0px' });
  /** @type {ChartParts} */
  const parts = {
    plan, el, frame, canvas, measure, colours: { ink, ink2, bg }, styles, title, xTitle, yTitle: yTitleLabel, cats, yTicks, xTicks,
    seriesLabels, values, pointLabels,
    times: chartTimes(plan, opts.events || [], Math.max(0, Number(opts.start) || 0), ctx.motion && ctx.motion.scale > 0 ? ctx.motion.scale : 1),
    model: null, Chart: null, chart: null, failed: '', geo: null, origin: { x0: 0, y0: 0 }, lastKey: '', axesAlpha: 0,
    grow: plan.series.map((s) => s.values.map(() => 0)), pointAlpha: plan.points.map(() => 0),
  };
  const libs = [loadLib('chartjs'), plan.kind === 'graph' ? loadLib('mathjs') : Promise.resolve(null)];
  ctx.pending.push(Promise.all(libs).then(([Chart, math]) => {
    parts.Chart = Chart;
    if (plan.kind === 'graph') parts.model = graphModel(plan, math ? createSafeMath(math) : null);
  }, (e) => {
    parts.failed = e instanceof Error ? e.message : String(e);
    if (plan.kind === 'graph') parts.model = graphModel(plan, null);
  }));
  return parts;
}

// ---------------------------------------------------------------------------------------------
// Geometry (pure given the laid-out boxes)
// ---------------------------------------------------------------------------------------------

/**
 * The pixel (stage px) of x value `v`: a category axis puts category i at its band's centre (Chart.js's category
 * scale with `offset: true`), a linear one maps [min, max] onto the plot's width.
 * @param {any} g @param {number} v
 */
export function xPixel(g, v) {
  const p = g.plot;
  if (g.x.kind === 'category') return p.x0 + ((v + 0.5) / g.x.n) * (p.x1 - p.x0);
  return p.x0 + ((v - g.x.min) / (g.x.max - g.x.min)) * (p.x1 - p.x0);
}

/** The pixel (stage px) of y value `v` (linear, bottom up; a 9:16 bar list's category i at its row's bar). @param {any} g @param {number} v */
export function yPixel(g, v) {
  const p = g.plot;
  if (g.y.kind === 'category') return p.y0 + ((v + 0.5) / g.y.n) * (p.y1 - p.y0);
  return p.y1 - ((v - g.y.min) / (g.y.max - g.y.min)) * (p.y1 - p.y0);
}

/**
 * The rect (stage px, unrounded) of bar `i` of series `s` at its full value: Chart.js's grouped bars with a set
 * `barThickness` (a group of `ns` bars of `thick` px centred on the category; its value from the base, 0, which
 * Chart.js moves half the zero line's width toward the bar's end, so a bar stands on the line: a vertical chart's value
 * axis draws its 0 tick's gridline; the 9:16 list draws none).
 * @param {any} g @param {ChartPlan} plan @param {number} s @param {number} i
 * @returns {{x0: number, y0: number, x1: number, y1: number}}
 */
export function barRect(g, plan, s, i) {
  const v = plan.series[s].values[i];
  const ns = plan.series.length;
  if (plan.horizontal) {
    const c = yPixel(g, i);
    const a = xPixel(g, 0);
    const b = xPixel(g, v);
    return { x0: Math.min(a, b), y0: c - g.thick / 2, x1: Math.max(a, b), y1: c + g.thick / 2 };
  }
  const c = xPixel(g, i) - (ns * g.thick) / 2 + (s + 0.5) * g.thick;
  const zero = Array.isArray(g.yTicks) && g.yTicks.includes(0) ? STROKE.zero / 2 : 0;
  const a = yPixel(g, 0) - Math.sign(v) * zero;
  const b = yPixel(g, v);
  return { x0: c - g.thick / 2, y0: Math.min(a, b), x1: c + g.thick / 2, y1: Math.max(a, b) };
}

/**
 * Spread labels along one axis so none overlaps: `items` give their wanted start and size; they keep their order,
 * are pushed apart by `gap`, kept inside [lo, hi] when they fit, and the excess is returned. Pure.
 * @param {{want: number, size: number}[]} items @param {number} lo @param {number} hi @param {number} gap
 * @returns {{at: number[], overflow: number}}
 */
export function spread(items, lo, hi, gap) {
  const order = items.map((_, k) => k).sort((a, b) => items[a].want - items[b].want || a - b);
  const at = items.map((it) => it.want);
  let prev = -Infinity;
  for (const k of order) {
    at[k] = Math.max(at[k], lo, prev);
    prev = at[k] + items[k].size + gap;
  }
  const last = order.length ? order[order.length - 1] : -1;
  const excess = last >= 0 ? at[last] + items[last].size - hi : 0;
  if (excess > 0) {
    let next = hi;
    for (let j = order.length - 1; j >= 0; j--) {
      const k = order[j];
      at[k] = Math.min(at[k], next - items[k].size);
      next = at[k] - gap;
    }
  }
  const first = order.length ? at[order[0]] : lo;
  const total = order.reduce((a, k) => a + items[k].size, 0) + gap * Math.max(0, order.length - 1);
  return { at, overflow: Math.max(0, lo - first, total - (hi - lo)) };
}

// ---------------------------------------------------------------------------------------------
// Layout
// ---------------------------------------------------------------------------------------------

/**
 * Natural one-line width (px) of `text` at `size` and `weight` (the frame's hidden measuring line).
 * @param {ChartParts} parts @param {string} text @param {number} size @param {number} weight
 */
function natural(parts, text, size, weight) {
  const m = parts.measure;
  m.style.fontSize = `${size}px`;
  m.style.fontWeight = String(weight);
  m.textContent = text;
  return m.offsetWidth || 0;
}

/**
 * Set a label at `size` in a box of `width` (null: one line, its natural width) and measure it: its line count against
 * the slot's (`_shared.js` fitLines' count at one size) and its widest word against the box (a label never breaks
 * inside a word, so a word wider than the box is overflow: "2019" never becomes "201" over "9"). Returns its height.
 * @param {ChartParts} parts @param {Label} l @param {number} size @param {number | null} width @param {number} lines
 */
function setLabel(parts, l, size, width, lines) {
  l.size = size;
  l.el.style.fontSize = `${size}px`;
  l.el.style.whiteSpace = width === null || l.nowrap ? 'nowrap' : 'normal';
  if (width === null) {
    const w = Math.ceil(natural(parts, l.text, size, l.weight));
    l.wrap.style.width = `${w}px`;
    l.box.w = w;
    l.box.h = Math.round(size * l.lh);
    l.overflow = 0;
    return l.box.h;
  }
  l.wrap.style.width = `${Math.max(1, Math.floor(width))}px`;
  l.box.w = Math.max(1, Math.floor(width));
  const tall = l.el.offsetHeight || 0;
  const over = Math.round(tall / (size * l.lh)) > lines ? Math.max(1, tall - lines * size * l.lh) : 0;
  const widest = Math.max(0, ...l.words.map((w) => w.offsetWidth || 0));
  l.overflow = Math.max(over, widest - l.box.w > 1 ? Math.ceil(widest - l.box.w) : 0);
  l.box.h = Math.max(Math.round(size * l.lh), l.el.offsetHeight || Math.round(size * l.lh));
  return l.box.h;
}

/** Place a label's wrap at stage (x, y), relative to the frame's origin. @param {any} g @param {Label} l @param {number} x @param {number} y */
function put(g, l, x, y) {
  l.box.x = Math.round(x);
  l.box.y = Math.round(y);
  l.wrap.style.left = `${Math.round(x - g.origin.x0)}px`;
  l.wrap.style.top = `${Math.round(y - g.origin.y0)}px`;
  l.wrap.style.transform = 'none';
  l.wrap.style.transformOrigin = '0px 0px';
  l.wrap.style.display = 'block';
  l.shown = true;
}

/** @param {Label} l */
function hide(l) {
  l.wrap.style.display = 'none';
  l.shown = false;
  l.overflow = 0;
}

/**
 * The size of slot `slot` at fit level `k` (every slot steps down together; never below its minimum, nor above a
 * preflight size cap: `_shared.js slotRange`).
 * @param {TemplateContext} ctx @param {string} slot @param {number} k
 */
function sizeAt(ctx, slot, k) {
  const S = SIZES[ctx.format][/** @type {'tick' | 'value' | 'axis' | 'series' | 'chart_title'} */ (slot)];
  const r = labelRange(ctx, slot, { min: S.min, max: S.max });
  return Math.max(r.min, r.max - SIZE_STEP * k);
}

/**
 * Lay the chart out in `box` (stage px; idempotent): fit every label (largest sizes first, all slots stepping down
 * together), place them and the plot, and (re)build the Chart.js chart for the plot. Call after `ctx.pending`.
 * @param {TemplateContext} ctx
 * @param {ChartParts} parts
 * @param {Box} box
 */
export function layoutChart(ctx, parts, box) {
  if (parts.failed && ctx.mode === 'render') {
    // an MP4 must never show an empty plot: the render fails (the vendored library is part of the page)
    throw new Error(`chart: ${parts.failed}`);
  }
  Object.assign(parts.frame.style, {
    left: `${Math.round(box.x0)}px`, top: `${Math.round(box.y0)}px`,
    width: `${Math.round(box.x1 - box.x0)}px`, height: `${Math.round(box.y1 - box.y0)}px`,
  });
  const levels = Math.max(...SLOTS.map((s) => {
    const S = SIZES[ctx.format][/** @type {'tick' | 'value' | 'axis' | 'series' | 'chart_title'} */ (s)];
    return Math.ceil((S.max - S.min) / SIZE_STEP);
  }));
  parts.origin = { x0: Math.round(box.x0), y0: Math.round(box.y0) };
  let g = null;
  for (let k = 0; k <= levels; k++) {
    // the caller's title at the top of the box, the chart in the rest
    let inner = box;
    let titleOverflow = 0;
    if (parts.title) {
      const S = SIZES[ctx.format].chart_title;
      const hh = setLabel(parts, parts.title, sizeAt(ctx, 'chart_title', k), box.x1 - box.x0, S.lines);
      put({ origin: parts.origin }, parts.title, box.x0, box.y0);
      titleOverflow = parts.title.overflow;
      inner = { x0: box.x0, y0: box.y0 + hh + GAP, x1: box.x1, y1: box.y1 };
    }
    g = parts.plan.horizontal ? layoutList(ctx, parts, inner, k) : layoutCartesian(ctx, parts, inner, k);
    g.fits = g.fits && titleOverflow <= 0;
    if (g.fits) break;
  }
  parts.geo = g;
  buildCanvas(ctx, parts);
}

/**
 * The 16:9 / vertical layout: y title over the plot's left edge, y ticks right-aligned left of the plot, category or
 * x tick labels under it, the x title under them, a series label column right of the plot (or the labels inside it).
 * @param {TemplateContext} ctx @param {ChartParts} parts @param {Box} box @param {number} k
 */
function layoutCartesian(ctx, parts, box, k) {
  const plan = parts.plan;
  const W = box.x1 - box.x0;
  const H = box.y1 - box.y0;
  const tick = sizeAt(ctx, 'tick', k);
  const value = sizeAt(ctx, 'value', k);
  const axis = sizeAt(ctx, 'axis', k);
  const series = sizeAt(ctx, 'series', k);
  const Sz = SIZES[ctx.format];
  const g = /** @type {any} */ ({ box, origin: parts.origin, fits: true, k });
  const tickH = Math.round(tick * LINE_HEIGHT.latin);
  // the x labels start this far under the plot: clear of the lowest y tick label, which is centred on the plot's edge
  const xGap = Math.max(GAP, Math.round(tickH / 2) + SPACE[0] / 2);
  // titles
  const yT = parts.yTitle;
  const xT = parts.xTitle;
  const yTitleH = yT ? setLabel(parts, yT, axis, W, Sz.axis.lines) : 0;
  const xTitleH = xT ? setLabel(parts, xT, axis, W, Sz.axis.lines) : 0;
  // a function undefined on its whole range draws no curve: its label says so (never left out)
  if (plan.kind === 'graph' && parts.model) {
    parts.model.curves.forEach((c, s) => {
      const l = parts.seriesLabels[s];
      if (!l || c.ok) return;
      l.text = `${plan.functions[s].label}: ${LABELS.undefinedOnRange}`;
      l.el.textContent = l.text;
      l.words = [];
    });
  }
  // series labels: a column right of the plot, else inside it at the line's end
  const columnMode = plan.kind === 'bar' || W >= COLUMN_MIN_W;
  const drawn = parts.seriesLabels.filter((l) => l.text);
  let colW = 0;
  if (drawn.length && columnMode) {
    colW = Math.min(Math.round(W * COLUMN_SHARE), Math.ceil(Math.max(...drawn.map((l) => natural(parts, l.text, series, l.weight)))));
  }
  for (const l of parts.seriesLabels) {
    if (!l.text) {
      hide(l);
      continue;
    }
    if (columnMode) setLabel(parts, l, series, colW, Sz.series.lines);
    else setLabel(parts, l, series, null, 1);
  }
  // value labels room (bars: above the plot for positive bars, below for negative ones; one-series line values above)
  const values = parts.values.flat().filter(Boolean);
  const valueH = values.length ? Math.round(value * LINE_HEIGHT.latin) : 0;
  const all = plan.series.flatMap((s) => s.values);
  const roomAbove = values.length && (plan.kind === 'line' || all.some((v) => v > 0)) ? valueH + VALUE_GAP : 0;
  const roomBelow = values.length && plan.kind === 'bar' && all.some((v) => v < 0) ? valueH + VALUE_GAP : 0;
  // the x axis
  g.x = plan.kind === 'graph' ? { kind: 'linear' } : plan.kind === 'bar' ? { kind: 'category', n: plan.categories.length } : { kind: 'linear', min: -0.5, max: plan.categories.length - 0.5 };
  g.y = { kind: 'linear' };
  if (plan.kind === 'graph') {
    const m = /** @type {GraphModel} */ (parts.model);
    g.x.min = m.xRange[0];
    g.x.max = m.xRange[1];
    g.y.min = m.yRange[0];
    g.y.max = m.yRange[1];
  }
  // two passes: the y ticks depend on the plot's height, the plot's width on the y ticks' width
  let xLabelH = tickH;
  // how far left of the plot the slanted category labels reach (the first ones run down-left of their category):
  // the plot starts at least that far into the box
  let slantReach = 0;
  let slanted = false;
  let yTickW = 0;
  /** @type {number[]} */
  let yTicks = [];
  /** @type {number[]} */
  let xTicks = [];
  let yStep = 1;
  let xStep = 1;
  const graph = plan.kind === 'graph' ? /** @type {GraphModel} */ (parts.model) : null;
  for (let pass = 0; pass < 3; pass++) {
    const top = box.y0 + (yTitleH ? yTitleH + GAP : 0) + Math.max(roomAbove, Math.round(tickH / 2));
    const bottom = box.y1 - (xTitleH ? xTitleH + GAP : 0) - xLabelH - xGap - roomBelow;
    const plotH = bottom - top;
    // the y ticks: never closer than a label's height + 8 px (`room` of them at the most); about `airy` of them
    // (TICK_ROOM label heights apart) where the plot allows. Never fewer than 2: a plot too short for them is reported
    // by the verdict below.
    const room = Math.max(2, Math.min(TICKS_MAX.y, Math.floor(plotH / (tickH + SPACE[0])) + 1));
    const airy = Math.max(2, Math.min(room, Math.floor(plotH / (tickH * TICK_ROOM)) + 1));
    /** @param {{min: number, max: number, ticks: number[], step: number}} a */
    const take = (a) => {
      g.y.min = a.min;
      g.y.max = a.max;
      yTicks = a.ticks;
      yStep = a.step;
      g.ySpacing = yTicks.length > 1 ? (plotH * yStep) / (g.y.max - g.y.min) : Infinity;
    };
    if (graph && plan.yRange) {
      // a stored y range is kept: about `airy` nice ticks inside it, fewer while they crowd
      for (let m = airy; ; m--) {
        const t = insideTicks(graph.yRange[0], graph.yRange[1], m);
        take({ min: graph.yRange[0], max: graph.yRange[1], ticks: t.ticks, step: t.step });
        if (g.ySpacing >= tickH + SPACE[0] || m <= 2) break;
      }
    } else {
      // an axis that covers the data (a bar chart's holds 0; a graph's automatic range is autoYRange's without its 8%
      // padding, so a curve that touches 0 does not reach a whole step below it): of the axes whose labels keep
      // apart, the one the data fills best, a tick past `airy` costing a little; fewer ticks on a tie
      const span = graph ? (graph.yRange[1] - graph.yRange[0]) / 1.16 : 0;
      const lo = graph ? graph.yRange[0] + 0.08 * span : 0;
      const hi = graph ? graph.yRange[1] - 0.08 * span : 0;
      /** @type {{a: any, score: number} | null} */
      let best = null;
      for (let m = room; m >= 2; m--) {
        let a;
        if (graph) {
          const t = coverTicks(lo, hi, m);
          a = { min: t.ticks[0], max: t.ticks[t.ticks.length - 1], ticks: t.ticks, step: t.step };
        } else {
          a = valueAxis(plan, m);
        }
        const spacing = a.ticks.length > 1 ? (plotH * a.step) / (a.max - a.min) : Infinity;
        if (spacing < tickH + SPACE[0] && !(m === 2 && !best)) continue;
        const data = graph ? hi - lo : valueSpan(plan);
        const score = data / (a.max - a.min) - 0.04 * Math.max(0, a.ticks.length - airy);
        if (!best || score >= best.score - 1e-9) best = { a, score };
      }
      take(/** @type {{a: any}} */ (best).a);
    }
    const yDec = decimalsOf(yStep);
    yTickW = Math.ceil(Math.max(0, ...yTicks.map((v) => natural(parts, formatNumber(v, yDec, ctx.locale), tick, Sz.tick.weight))));
    const x0 = box.x0 + Math.max(yTickW + GAP, slantReach);
    const x1 = box.x1 - (colW ? colW + GAP : 0);
    g.plot = { x0, y0: top, x1, y1: bottom };
    // the x labels
    const plotW = x1 - x0;
    if (graph) {
      // the most nice ticks inside the x range whose labels keep GAP apart
      for (let xt = TICKS_MAX.x; ; xt--) {
        const t = insideTicks(graph.xRange[0], graph.xRange[1], xt);
        const dec = decimalsOf(t.step);
        const widest = Math.max(0, ...t.ticks.map((v) => natural(parts, formatNumber(v, dec, ctx.locale), tick, Sz.tick.weight)));
        const spacing = t.ticks.length > 1 ? (plotW * t.step) / (graph.xRange[1] - graph.xRange[0]) : Infinity;
        if (widest + GAP <= spacing || xt <= 2) {
          xTicks = t.ticks;
          xStep = t.step;
          break;
        }
      }
      xLabelH = tickH;
    } else {
      const n = plan.categories.length;
      const band = plotW / n;
      let fitsFlat = true;
      let tallest = tickH;
      for (const l of parts.cats) {
        const hh = setLabel(parts, l, tick, Math.max(1, band - SPACE[0]), Sz.tick.lines);
        if (l.overflow > 0) fitsFlat = false;
        tallest = Math.max(tallest, hh);
      }
      slanted = !fitsFlat;
      slantReach = 0;
      if (slanted) {
        tallest = 0;
        parts.cats.forEach((l, i) => {
          setLabel(parts, l, tick, null, 1);
          tallest = Math.max(tallest, Math.ceil(l.box.w * SLANT_SIN + l.box.h * SLANT_COS));
          // its top-left corner is the leftmost point, w cos(SLANT) left of its category's centre
          slantReach = Math.max(slantReach, Math.ceil(l.box.w * SLANT_COS - (i + 0.5) * band));
        });
      }
      xLabelH = tallest;
    }
  }
  const plot = g.plot;
  g.area = plot;
  g.slanted = slanted;
  g.yTicks = yTicks;
  g.xTicks = xTicks;
  g.yDec = decimalsOf(yStep);
  g.xDec = decimalsOf(xStep);
  g.n = plan.categories.length;
  // bars: a group of ns bars takes BAR_SHARE of the band
  if (plan.kind === 'bar') {
    const band = (plot.x1 - plot.x0) / g.x.n;
    g.thick = Math.max(2, Math.floor((band * BAR_SHARE) / plan.series.length));
  }
  // does it fit? the plot keeps its share, and its y tick labels a label's height apart
  const plotOk = plot.y1 - plot.y0 >= MIN_PLOT.h * H && plot.x1 - plot.x0 >= MIN_PLOT.w * W
    && g.ySpacing >= tickH + SPACE[0];
  // place the titles: the x title centred under the plot when it keeps its height there, else under the box
  if (yT) put(g, yT, box.x0, box.y0);
  if (xT) {
    const under = setLabel(parts, xT, axis, plot.x1 - plot.x0, Sz.axis.lines);
    if (under <= xTitleH && xT.overflow <= 0) put(g, xT, plot.x0, box.y1 - xTitleH);
    else {
      setLabel(parts, xT, axis, W, Sz.axis.lines);
      put(g, xT, box.x0, box.y1 - xTitleH);
    }
  }
  // y ticks
  parts.yTicks.forEach((l, j) => {
    if (j >= yTicks.length) {
      hide(l);
      return;
    }
    l.text = formatNumber(yTicks[j], g.yDec, ctx.locale);
    l.el.textContent = l.text;
    l.words = [];
    setLabel(parts, l, tick, null, 1);
    put(g, l, plot.x0 - GAP - l.box.w, yPixel(g, yTicks[j]) - l.box.h / 2);
  });
  // x labels
  let catOverflow = 0;
  if (graph) {
    parts.xTicks.forEach((l, j) => {
      if (j >= xTicks.length) {
        hide(l);
        return;
      }
      l.text = formatNumber(xTicks[j], g.xDec, ctx.locale);
      l.el.textContent = l.text;
      l.words = [];
      setLabel(parts, l, tick, null, 1);
      const cx = xPixel(g, xTicks[j]);
      const x = Math.min(box.x1 - l.box.w, Math.max(box.x0, cx - l.box.w / 2));
      put(g, l, x, plot.y1 + roomBelow + xGap);
    });
  } else {
    const band = (plot.x1 - plot.x0) / plan.categories.length;
    parts.cats.forEach((l, i) => {
      const cx = xPixel(g, i);
      const y = plot.y1 + roomBelow + xGap;
      if (slanted) {
        // the label's end under its category, rising to the right (rotated about its top-right corner): its leftmost
        // point is its top-left corner, its rightmost its bottom-right one
        put(g, l, cx - l.box.w, y);
        l.wrap.style.transformOrigin = '100% 0%';
        l.wrap.style.transform = `rotate(-${SLANT}deg)`;
        const reachLeft = cx - l.box.w * SLANT_COS;
        const reachRight = cx + l.box.h * SLANT_SIN;
        l.overflow = Math.max(0, Math.ceil(box.x0 - reachLeft), Math.ceil(reachRight - box.x1));
      } else {
        put(g, l, cx - (band - SPACE[0]) / 2, y);
      }
      catOverflow = Math.max(catOverflow, l.overflow);
    });
  }
  // series labels
  let seriesOverflow = 0;
  /** @type {{l: Label, end: {x: number, y: number}}[]} */
  const seriesItems = [];
  parts.seriesLabels.forEach((l, s) => {
    if (!l.text) return;
    // a function with no curve: its label at the top of the plot
    const end = seriesEnd(g, parts, s) || { x: plot.x1, y: plot.y0 + l.box.h / 2 };
    seriesItems.push({ l, end });
  });
  if (seriesItems.length) {
    if (columnMode) {
      const placed = spread(seriesItems.map((it) => ({ want: it.end.y - it.l.box.h / 2, size: it.l.box.h })), box.y0 + (yTitleH ? yTitleH + GAP : 0), plot.y1, SPACE[0]);
      seriesItems.forEach((it, j) => put(g, it.l, plot.x1 + GAP, placed.at[j]));
      seriesOverflow = placed.overflow;
    } else {
      // inside the plot, beside the series' end on the side its lines leave free (`placeBeside`)
      /** @type {{x0: number, y0: number, x1: number, y1: number}[]} */
      const near = [];
      const lines = polylines(g, parts);
      seriesItems.forEach((it) => {
        const w = it.l.box.w;
        const hh = it.l.box.h;
        const e = it.end;
        const d = SPACE[1];
        /** @type {[number, number][]} */
        const order = [[e.x - d - w, e.y - d - hh], [e.x - d - w, e.y + d], [plot.x1 - w, e.y - d - hh], [plot.x1 - w, e.y + d],
          [plot.x1 - w, plot.y0]];
        const at = placeBeside(g, order, w, hh, near, lines, e);
        put(g, it.l, at.x, at.y);
        near.push({ x0: at.x, y0: at.y, x1: at.x + w, y1: at.y + hh });
        it.l.overflow = Math.max(0, w - (plot.x1 - plot.x0));
      });
    }
  }
  // value labels (bars: all of them or none; a line's: above its points)
  let valuesFit = true;
  for (let s = 0; s < parts.values.length; s++) {
    for (let i = 0; i < parts.values[s].length; i++) {
      const l = parts.values[s][i];
      if (!l) continue;
      setLabel(parts, l, value, null, 1);
      const slot = plan.kind === 'bar' ? (plot.x1 - plot.x0) / g.x.n / plan.series.length : (plot.x1 - plot.x0) / g.n;
      if (l.box.w > slot - 4) valuesFit = false;
    }
  }
  g.valuesShown = valuesFit;
  for (let s = 0; s < parts.values.length; s++) {
    for (let i = 0; i < parts.values[s].length; i++) {
      const l = parts.values[s][i];
      if (!l) continue;
      if (!valuesFit) {
        hide(l);
        continue;
      }
      const v = plan.series[s].values[i];
      if (plan.kind === 'bar') {
        const r = barRect(g, plan, s, i);
        const cx = (r.x0 + r.x1) / 2;
        put(g, l, cx - l.box.w / 2, v < 0 ? r.y1 + VALUE_GAP : r.y0 - VALUE_GAP - l.box.h);
      } else {
        put(g, l, xPixel(g, i) - l.box.w / 2, yPixel(g, v) - STROKE.point - VALUE_GAP - l.box.h);
      }
    }
  }
  // a graph's point labels: beside the marker on the side its curve leaves free, clear of the labels already placed
  /** @type {{x0: number, y0: number, x1: number, y1: number}[]} */
  const taken = seriesItems.map((it) => ({ x0: it.l.box.x, y0: it.l.box.y, x1: it.l.box.x + it.l.box.w, y1: it.l.box.y + it.l.box.h }));
  parts.pointLabels.forEach((l, j) => {
    if (!l.text) {
      hide(l);
      return;
    }
    setLabel(parts, l, series, null, 1);
    const p = parts.plan.points[j];
    const at = pointLabelAt(g, graph, p, l.box.w, l.box.h, taken);
    put(g, l, at.x, at.y);
    taken.push({ x0: at.x, y0: at.y, x1: at.x + l.box.w, y1: at.y + l.box.h });
    l.overflow = Math.max(0, l.box.w - (box.x1 - box.x0));
  });
  // the verdict: the plot keeps its share and nothing overflows
  const labelOverflow = Math.max(
    yT ? yT.overflow : 0, xT ? xT.overflow : 0, catOverflow, seriesOverflow,
    ...parts.seriesLabels.map((l) => (l.shown ? l.overflow : 0)),
  );
  g.fits = plotOk && labelOverflow <= 0;
  g.share = (plot.y1 - plot.y0) / Math.max(1, H);
  g.plotShort = plotOk ? 0 : Math.max(1, Math.round(MIN_PLOT.h * H - (plot.y1 - plot.y0)),
    Math.round(MIN_PLOT.w * W - (plot.x1 - plot.x0)), Math.round((tickH + SPACE[0] - g.ySpacing) * Math.max(1, yTicks.length - 1)));
  g.seriesOverflow = seriesOverflow;
  return g;
}

/**
 * Where series (or graph function) `s` ends: its last value's point (a line), its last bar's end (bars), its curve's
 * last point inside the range (a graph). Null when there is none (a function undefined on the range).
 * @param {any} g @param {ChartParts} parts @param {number} s
 * @returns {{x: number, y: number} | null}
 */
function seriesEnd(g, parts, s) {
  const plan = parts.plan;
  if (plan.kind === 'graph') {
    const c = parts.model && parts.model.curves[s];
    return c && c.end ? { x: xPixel(g, c.end.x), y: yPixel(g, c.end.y) } : null;
  }
  const n = plan.categories.length;
  const v = plan.series[s].values[n - 1];
  if (plan.kind === 'bar') {
    const r = barRect(g, plan, s, n - 1);
    return { x: r.x1, y: v < 0 ? r.y1 : r.y0 };
  }
  return { x: xPixel(g, n - 1), y: yPixel(g, v) };
}

/**
 * Whether segment (ax, ay)-(bx, by) meets rect `r` (stage px). Pure.
 * @param {number} ax @param {number} ay @param {number} bx @param {number} by
 * @param {{x0: number, y0: number, x1: number, y1: number}} r
 */
export function segmentMeetsRect(ax, ay, bx, by, r) {
  if (Math.max(ax, bx) < r.x0 || Math.min(ax, bx) > r.x1 || Math.max(ay, by) < r.y0 || Math.min(ay, by) > r.y1) return false;
  // Liang-Barsky clipping of the segment against the rect
  let t0 = 0;
  let t1 = 1;
  const dx = bx - ax;
  const dy = by - ay;
  const edges = [[-dx, ax - r.x0], [dx, r.x1 - ax], [-dy, ay - r.y0], [dy, r.y1 - ay]];
  for (const [pp, qq] of edges) {
    if (pp === 0) {
      if (qq < 0) return false;
      continue;
    }
    const u = qq / pp;
    if (pp < 0) t0 = Math.max(t0, u);
    else t1 = Math.min(t1, u);
    if (t0 > t1) return false;
  }
  return true;
}

/**
 * Where a graph point's label goes (stage px, its top left): beside the marker on a side the curve through it leaves
 * free (a curve rising to the right fills the upper right and the lower left, so its label goes below right, else
 * above left; a falling one the other way; above right with no curve near), the first candidate inside the plot that
 * overlaps no label in `taken` and no curve; then one that overlaps no label; failing that, the first inside the box.
 * Pure given the layout.
 * @param {any} g @param {GraphModel | null} model @param {{x: number, y: number}} p @param {number} w @param {number} h
 * @param {{x0: number, y0: number, x1: number, y1: number}[]} taken
 * @returns {{x: number, y: number}}
 */
export function pointLabelAt(g, model, p, w, h, taken) {
  const cx = xPixel(g, p.x);
  const cy = yPixel(g, p.y);
  const r = STROKE.point + SPACE[0];
  // the slope (screen px) of the curve passing nearest the point, if one passes within r
  let slope = 0;
  let near = Infinity;
  for (const c of model ? model.curves : []) {
    for (let k = 1; k < c.points.length; k++) {
      const a = c.points[k - 1];
      const b = c.points[k];
      if (!Number.isFinite(a.y) || !Number.isFinite(b.y) || a.x > p.x || b.x < p.x || b.x === a.x) continue;
      const yAt = a.y + ((b.y - a.y) * (p.x - a.x)) / (b.x - a.x);
      const d = Math.abs(yPixel(g, yAt) - cy);
      if (d < near) {
        near = d;
        slope = (yPixel(g, b.y) - yPixel(g, a.y)) / Math.max(1e-9, xPixel(g, b.x) - xPixel(g, a.x));
      }
    }
  }
  const right = cx + r;
  const left = cx - r - w;
  const above = cy - r - h;
  const below = cy + r;
  /** @type {[number, number][]} */
  let order = [[right, above], [left, above], [right, below], [left, below]];
  if (near <= r && slope < -0.2) order = [[right, below], [left, above], [right, above], [left, below]];
  else if (near <= r && slope > 0.2) order = [[right, above], [left, below], [right, below], [left, above]];
  return placeBeside(g, order, w, h, taken, model ? model.curves.map((c) => c.points.map((q) => ({ x: xPixel(g, q.x), y: Number.isFinite(q.y) ? yPixel(g, q.y) : NaN }))) : [],
    { x: cx, y: cy });
}

/** A label placed inside the plot searches this grid round its anchor when its preferred spots meet a line. */
export const GRID_PX = 16;
export const GRID_STEPS = 12;

/**
 * The first of `order` (candidate top-lefts, stage px) for a w x h label that lies inside the plot, overlaps no rect in
 * `taken` and is crossed by no line of `lines` (polylines in stage px, NaN y between pieces); then the such spot of
 * a GRID_PX grid round `anchor` (GRID_STEPS steps each way) nearest to it; then the first inside the plot that
 * overlaps no rect; then the first inside the box; else the first, kept in the box. Pure.
 * @param {any} g @param {[number, number][]} order @param {number} w @param {number} h
 * @param {{x0: number, y0: number, x1: number, y1: number}[]} taken @param {{x: number, y: number}[][]} lines
 * @param {{x: number, y: number} | null} [anchor]   what the label names (a line's end, a point)
 * @returns {{x: number, y: number}}
 */
export function placeBeside(g, order, w, h, taken, lines, anchor = null) {
  const P = g.plot;
  const B = g.box;
  const clear = (/** @type {number} */ x, /** @type {number} */ y) => taken.every((t) => x + w <= t.x0 || x >= t.x1 || y + h <= t.y0 || y >= t.y1);
  const offLines = (/** @type {number} */ x, /** @type {number} */ y) => {
    const rect = { x0: x, y0: y, x1: x + w, y1: y + h };
    for (const line of lines) {
      for (let k = 1; k < line.length; k++) {
        const a = line[k - 1];
        const b = line[k];
        if (!Number.isFinite(a.y) || !Number.isFinite(b.y)) continue;
        if (segmentMeetsRect(a.x, a.y, b.x, b.y, rect)) return false;
      }
    }
    return true;
  };
  const inPlot = (/** @type {number} */ x, /** @type {number} */ y) => x >= P.x0 && x + w <= P.x1 && y >= P.y0 && y + h <= P.y1;
  for (const [x, y] of order) {
    if (inPlot(x, y) && clear(x, y) && offLines(x, y)) return { x, y };
  }
  // none of the preferred spots is free of the lines: the free spot of a grid round the anchor nearest to it
  if (lines.length && anchor) {
    /** @type {[number, number, number][]} */
    const grid = [];
    for (let i = -GRID_STEPS; i <= GRID_STEPS; i++) {
      for (let j = -GRID_STEPS; j <= GRID_STEPS; j++) {
        const x = Math.round(anchor.x - w / 2 + i * GRID_PX);
        const y = Math.round(anchor.y - h / 2 + j * GRID_PX);
        const dx = Math.max(x - anchor.x, 0, anchor.x - (x + w));
        const dy = Math.max(y - anchor.y, 0, anchor.y - (y + h));
        grid.push([x, y, dx * dx + dy * dy]);
      }
    }
    grid.sort((a, b) => a[2] - b[2] || a[1] - b[1] || a[0] - b[0]);
    for (const [x, y] of grid) {
      if (inPlot(x, y) && clear(x, y) && offLines(x, y)) return { x, y };
    }
  }
  for (const [x, y] of order) {
    if (inPlot(x, y) && clear(x, y)) return { x, y };
  }
  for (const [x, y] of order) {
    if (x >= B.x0 && x + w <= B.x1 && y >= B.y0 && y + h <= B.y1 && clear(x, y)) return { x, y };
  }
  const [x, y] = order[0];
  return { x: Math.max(B.x0, Math.min(B.x1 - w, x)), y: Math.max(B.y0, Math.min(B.y1 - h, y)) };
}

/**
 * The lines the chart draws, as polylines in stage px (a line chart's series through its points; a graph's curves
 * through their samples): what a label placed inside the plot keeps clear of. Pure given the layout.
 * @param {any} g @param {ChartParts} parts
 * @returns {{x: number, y: number}[][]}
 */
function polylines(g, parts) {
  const plan = parts.plan;
  if (plan.kind === 'graph' && parts.model) {
    return parts.model.curves.map((c) => c.points.map((q) => ({ x: xPixel(g, q.x), y: Number.isFinite(q.y) ? yPixel(g, q.y) : NaN })));
  }
  if (plan.kind === 'line') return plan.series.map((s) => s.values.map((v, i) => ({ x: xPixel(g, i), y: yPixel(g, v) })));
  return [];
}

/**
 * The 9:16 bar list: a header row (the category title left, the value title right; stacked when they do not share a
 * line), then a row per category - its label above its bar, the value at the bar's end. Rows are evenly pitched, so
 * a category scale puts each bar where its row says.
 * @param {TemplateContext} ctx @param {ChartParts} parts @param {Box} box @param {number} k
 */
function layoutList(ctx, parts, box, k) {
  const plan = parts.plan;
  const W = box.x1 - box.x0;
  const tick = sizeAt(ctx, 'tick', k);
  const value = sizeAt(ctx, 'value', k);
  const axis = sizeAt(ctx, 'axis', k);
  const series = sizeAt(ctx, 'series', k);
  const Sz = SIZES[ctx.format];
  const g = /** @type {any} */ ({ box, origin: parts.origin, fits: true, k, valuesShown: true, slanted: false, yTicks: [], xTicks: [] });
  let y = box.y0;
  // header: x title (left) and y title (right) on one line when they fit together
  const xT = parts.xTitle;
  const yT = parts.yTitle;
  const xw = xT ? natural(parts, xT.text, axis, xT.weight) : 0;
  const yw = yT ? natural(parts, yT.text, axis, yT.weight) : 0;
  let headerOverflow = 0;
  if (xT && yT && xw + GAP + yw <= W) {
    setLabel(parts, xT, axis, null, 1);
    setLabel(parts, yT, axis, null, 1);
    put(g, xT, box.x0, y);
    put(g, yT, box.x1 - yT.box.w, y);
    y += Math.max(xT.box.h, yT.box.h) + GAP;
  } else {
    for (const l of [xT, yT]) {
      if (!l) continue;
      setLabel(parts, l, axis, W, Sz.axis.lines);
      put(g, l, box.x0, y);
      headerOverflow = Math.max(headerOverflow, l.overflow);
      y += l.box.h + SPACE[0];
    }
    if (xT || yT) y += GAP - SPACE[0];
  }
  // the one-series label (when it says more than the value title): a header line of its own
  for (const l of parts.seriesLabels) {
    if (!l.text) {
      hide(l);
      continue;
    }
    setLabel(parts, l, series, W, Sz.series.lines);
    put(g, l, box.x0, y);
    headerOverflow = Math.max(headerOverflow, l.overflow);
    y += l.box.h + GAP;
  }
  // rows
  const n = plan.categories.length;
  const values = parts.values[0] || [];
  const valueW = Math.ceil(Math.max(0, ...values.map((l) => (l ? natural(parts, l.text, value, l.weight) : 0))));
  let labelH = 0;
  let catOverflow = 0;
  for (const l of parts.cats) {
    labelH = Math.max(labelH, setLabel(parts, l, tick, W, Sz.tick.lines));
    catOverflow = Math.max(catOverflow, l.overflow);
  }
  const thick = STROKE.hbar;
  const rowGap = SPACE[1];
  const pitch = labelH + SPACE[0] / 2 + thick + rowGap;
  const rowsTop = y;
  const L = Math.max(SPACE[4], W - valueW - VALUE_GAP);
  const maxV = Math.max(0, ...plan.series[0].values);
  // the plot: category centres every `pitch`, the first at the first row's bar
  const centre0 = rowsTop + labelH + SPACE[0] / 2 + thick / 2;
  g.plot = { x0: box.x0, y0: centre0 - pitch / 2, x1: box.x0 + L, y1: centre0 - pitch / 2 + n * pitch };
  g.x = { kind: 'linear', min: 0, max: maxV > 0 ? maxV : 1 };
  g.y = { kind: 'category', n };
  g.thick = thick;
  g.n = n;
  parts.cats.forEach((l, i) => put(g, l, box.x0, yPixel(g, i) - thick / 2 - SPACE[0] / 2 - l.box.h));
  values.forEach((l, i) => {
    if (!l) return;
    setLabel(parts, l, value, null, 1);
    const r = barRect(g, plan, 0, i);
    put(g, l, r.x1 + VALUE_GAP, (r.y0 + r.y1) / 2 - l.box.h / 2);
  });
  const bottom = rowsTop + n * pitch - rowGap;
  g.area = { x0: box.x0, y0: rowsTop, x1: box.x1, y1: bottom };
  const over = Math.max(0, Math.round(bottom - box.y1));
  g.listOverflow = over;
  g.share = 1;
  g.fits = over <= 0 && headerOverflow <= 0 && catOverflow <= 0;
  return g;
}

// ---------------------------------------------------------------------------------------------
// The canvas (Chart.js: marks and gridlines only)
// ---------------------------------------------------------------------------------------------

/**
 * (Re)build the Chart.js chart for the laid-out plot: the canvas the plot plus PAD, the scales the engine's own
 * (ticks, ranges, offsets), no text of any kind. Without a 2D context (jsdom) or without Chart.js no chart is made and
 * the labels are still laid out (from the same mapping).
 * @param {TemplateContext} ctx @param {ChartParts} parts
 */
function buildCanvas(ctx, parts) {
  const g = parts.geo;
  const plan = parts.plan;
  const cx0 = Math.floor(g.plot.x0 - PAD);
  const cy0 = Math.floor(g.plot.y0 - PAD);
  const cw = Math.ceil(g.plot.x1 + PAD) - cx0;
  const chh = Math.ceil(g.plot.y1 + PAD) - cy0;
  g.canvas = { x0: cx0, y0: cy0, x1: cx0 + cw, y1: cy0 + chh };
  const c = parts.canvas;
  if (parts.chart) {
    try {
      parts.chart.destroy();
    } catch {
      /* a chart that never drew */
    }
    parts.chart = null;
  }
  c.width = cw;
  c.height = chh;
  Object.assign(c.style, { left: `${cx0 - g.origin.x0}px`, top: `${cy0 - g.origin.y0}px`, width: `${cw}px`, height: `${chh}px` });
  parts.lastKey = '';
  const Chart = parts.Chart;
  if (!Chart || parts.failed) return;
  sweep(Chart);
  const context = canvas2d(c);
  if (!context) return;
  // the padding that puts the chart area exactly on the plot box (the canvas is whole px round it)
  const padding = {
    left: g.plot.x0 - cx0, top: g.plot.y0 - cy0, right: cx0 + cw - g.plot.x1, bottom: cy0 + chh - g.plot.y1,
  };
  parts.chart = new Chart(context, chartConfig(ctx, parts, padding));
}

/**
 * Destroy charts whose canvas left the page (the stage removes a scene's DOM without telling its template; Chart.js
 * keeps every chart in its registry until destroyed). Only this engine's canvases.
 * @param {any} Chart
 */
function sweep(Chart) {
  const all = Chart && Chart.instances ? Object.values(Chart.instances) : [];
  for (const ch of all) {
    const cv = ch && ch.canvas;
    if (cv && cv.classList && cv.classList.contains('dyn-chart-canvas') && !cv.isConnected) {
      try {
        ch.destroy();
      } catch {
        /* already gone */
      }
    }
  }
}

/**
 * The Chart.js configuration (no animation, no events, no text; the data of the first frame is filled by paintChart).
 * @param {TemplateContext} ctx @param {ChartParts} parts @param {{left: number, top: number, right: number, bottom: number}} padding
 */
function chartConfig(ctx, parts, padding) {
  const g = parts.geo;
  const plan = parts.plan;
  const ink = ctx.roles.color('ink');
  const ink2 = ctx.roles.color('ink2');
  const surface = ctx.roles.color('surface');
  // gridlines at ink 10%, the zero line ink2, both fading in with the axes (parts.axesAlpha, set before each update)
  const gridColour = (/** @type {any} */ c) => {
    const v = c && c.tick ? c.tick.value : null;
    return v === 0 ? withAlpha(ink2, parts.axesAlpha) : withAlpha(ink, GRID_ALPHA * parts.axesAlpha);
  };
  const lineWidth = (/** @type {any} */ c) => (c && c.tick && c.tick.value === 0 ? STROKE.zero : STROKE.grid);
  const hidden = { display: false };
  /** @param {number[]} ticks */
  const valueScale = (ticks, min, max, grid) => ({
    type: 'linear', display: true, min, max, ticks: hidden, title: hidden, border: hidden,
    grid: grid ? { display: true, drawTicks: false, color: gridColour, lineWidth } : { display: false, drawTicks: false },
    afterBuildTicks: (/** @type {any} */ scale) => {
      scale.ticks = ticks.map((value) => ({ value }));
    },
  });
  const categoryScale = (/** @type {number} */ n) => ({
    type: 'category', display: true, offset: true, labels: Array.from({ length: n }, (_, i) => String(i)),
    ticks: hidden, title: hidden, border: hidden, grid: { display: false, drawTicks: false },
  });
  /** @type {any} */
  let scales;
  /** @type {any[]} */
  let datasets;
  const n = plan.categories.length;
  if (plan.kind === 'bar') {
    scales = plan.horizontal
      ? { x: valueScale([], g.x.min, g.x.max, false), y: categoryScale(n) }
      : { x: categoryScale(n), y: valueScale(g.yTicks, g.y.min, g.y.max, true) };
    datasets = plan.series.map((s, k) => ({
      type: 'bar', data: new Array(n).fill(null), backgroundColor: parts.styles[k].colour, borderWidth: 0,
      borderRadius: STROKE.barRadius, borderSkipped: 'start', barThickness: g.thick, maxBarThickness: g.thick,
      grouped: true,
    }));
  } else if (plan.kind === 'line') {
    scales = { x: valueScale([], g.x.min, g.x.max, false), y: valueScale(g.yTicks, g.y.min, g.y.max, true) };
    datasets = plan.series.map((s, k) => ({
      type: 'line', data: new Array(n + 1).fill(null).map((_, i) => ({ x: Math.min(i, n - 1), y: null })),
      parsing: false, spanGaps: false, borderColor: parts.styles[k].colour, borderWidth: STROKE.line,
      borderDash: parts.styles[k].dash, borderCapStyle: 'round', borderJoinStyle: 'round', tension: 0, fill: false,
      pointRadius: (/** @type {any} */ c) => (c && c.raw && c.raw.p ? STROKE.point : 0),
      pointHoverRadius: 0, pointBackgroundColor: parts.styles[k].colour, pointBorderColor: surface, pointBorderWidth: 3,
      clip: PAD,
    }));
  } else {
    scales = { x: valueScale(g.xTicks, g.x.min, g.x.max, true), y: valueScale(g.yTicks, g.y.min, g.y.max, true) };
    const m = /** @type {GraphModel} */ (parts.model);
    datasets = m.curves.map((curve, k) => ({
      type: 'line', data: curve.points.concat([{ x: m.xRange[1], y: NaN }]).map((p) => ({ x: p.x, y: null })),
      parsing: false, spanGaps: false, borderColor: parts.styles[k].colour, borderWidth: STROKE.line,
      borderDash: parts.styles[k].dash, borderCapStyle: 'round', borderJoinStyle: 'round', tension: 0, fill: false,
      pointRadius: 0, pointHoverRadius: 0,
    }));
    datasets.push({
      type: 'line', showLine: false, data: plan.points.map((p) => ({ x: p.x, y: null })), parsing: false,
      pointRadius: STROKE.point, pointHoverRadius: 0, pointBackgroundColor: ink, pointBorderColor: surface, pointBorderWidth: 3,
      clip: PAD,
    });
  }
  return {
    type: plan.kind === 'bar' ? 'bar' : 'line',
    data: { labels: Array.from({ length: Math.max(1, n) }, (_, i) => String(i)), datasets },
    options: {
      animation: false, responsive: false, maintainAspectRatio: false, events: [], devicePixelRatio: 1,
      indexAxis: plan.horizontal ? 'y' : 'x',
      // the chart area is exactly the plot: no automatic padding for the markers (the canvas has PAD round the plot,
      // and the datasets may draw that far past the area, clip: PAD)
      layout: { padding, autoPadding: false },
      plugins: { legend: hidden, tooltip: { enabled: false }, title: hidden, subtitle: hidden, decimation: { enabled: false } },
      scales,
      elements: { line: { borderWidth: STROKE.line }, point: { radius: 0, hoverRadius: 0 } },
    },
  };
}

// ---------------------------------------------------------------------------------------------
// Painting (frame t)
// ---------------------------------------------------------------------------------------------

/** Quantise a progress for the canvas (its data, and so its pixels, are a function of the noted key). @param {number} x */
const q = (x) => Math.round(Math.max(0, Math.min(1, x)) * 1e4) / 1e4;

/**
 * Paint scene time t: the axes' fade, every label's opacity and count, and the canvas data of frame t (Chart.js
 * `update('none')` only when it changed). Every property gets a value on every call.
 * @param {TemplateContext} ctx @param {ChartParts} parts @param {number} t
 */
export function paintChart(ctx, parts, t) {
  const p = ctx.paint;
  const plan = parts.plan;
  const T = /** @type {ChartTimes} */ (parts.times);
  const g = parts.geo;
  if (!T || !g) return;
  const S = (/** @type {number} */ x) => scaled(ctx, x);
  const fade = (/** @type {number} */ at, /** @type {number} */ dur) => ease('ease.enter', progress(t, at, S(dur)));
  const axes = q(fade(T.axes, AXES_ENTER));
  parts.axesAlpha = axes;
  /** @param {Label | null} l @param {number} a */
  const show = (l, a) => {
    if (!l) return;
    const v = l.shown ? a : 0;
    l.alpha = v;
    p.style(l.el, 'opacity', fx(v));
    p.style(l.el, 'visibility', v > 0.001 ? 'visible' : 'hidden');
  };
  for (const l of [parts.title, parts.xTitle, parts.yTitle, ...parts.yTicks, ...parts.xTicks, ...parts.cats]) show(l, axes);
  /** @type {string[]} */
  const key = [`a${axes}`];
  /** @type {any[][]} */
  const data = [];
  if (plan.kind === 'bar') {
    plan.series.forEach((s, si) => {
      const row = [];
      s.values.forEach((v, i) => {
        const e = q(fade(T.bars[si][i], BAR_GROW));
        parts.grow[si][i] = e;
        row.push(e > 0 ? v * e : null);
        key.push(`${e}`);
        const c = T.counts[si][i];
        const k = c === null ? e : q(ease('ease.enter', progress(t, c, S(COUNT_UP))));
        const l = parts.values[si][i];
        if (l) {
          const a = c === null ? fade(T.bars[si][i], LABEL_FADE) : fade(c, LABEL_FADE);
          show(l, a);
          if (l.shown) p.text(l.el, formatNumber(v * k, valueDecimals(v), ctx.locale));
        }
      });
      data.push(row);
    });
    // a series label appears with its last bar
    parts.seriesLabels.forEach((l, si) => show(l, fade(T.bars[si][plan.categories.length - 1], LABEL_FADE)));
  } else if (plan.kind === 'line') {
    const n = plan.categories.length;
    plan.series.forEach((s, si) => {
      const L = T.lines[si];
      // the line's head: between the last point reached and the next
      let reached = -1;
      let head = null;
      for (let i = 0; i < n; i++) {
        if (t >= L.arrive[i] - 1e-9 && (i === 0 ? t >= L.start : true)) reached = i;
      }
      for (let i = 1; i < n; i++) {
        if (t > L.depart[i] && t < L.arrive[i]) {
          const u = q(progress(t, L.depart[i], L.arrive[i] - L.depart[i]));
          head = { i, u };
        }
      }
      /** @type {any[]} */
      const row = [];
      for (let i = 0; i < n; i++) {
        const on = i <= reached;
        row.push({ x: i, y: on ? s.values[i] : null, p: on });
        parts.grow[si][i] = on ? 1 : 0;
      }
      // the head point (constant array length: n + 1)
      if (head) {
        const a = s.values[head.i - 1];
        const b = s.values[head.i];
        row.splice(head.i, 0, { x: head.i - 1 + head.u, y: a + (b - a) * head.u, p: false });
      } else {
        row.push({ x: n - 1, y: null, p: false });
      }
      key.push(`${reached}:${head ? `${head.i}/${head.u}` : '-'}`);
      data.push(row);
      parts.values[si].forEach((l, i) => {
        if (!l) return;
        const c = T.counts[si][i];
        const at = c === null ? L.arrive[i] : c;
        show(l, fade(at, LABEL_FADE));
        const k = c === null ? (i <= reached ? 1 : 0) : q(ease('ease.enter', progress(t, c, S(COUNT_UP))));
        if (l.shown) p.text(l.el, formatNumber(s.values[i] * k, valueDecimals(s.values[i]), ctx.locale));
      });
      show(parts.seriesLabels[si], fade(L.arrive[n - 1], LABEL_FADE));
    });
  } else {
    const m = /** @type {GraphModel} */ (parts.model);
    m.curves.forEach((curve, si) => {
      const C = T.curves[si];
      const u = q(progress(t, C.start, C.end - C.start));
      const cut = m.xRange[0] + (m.xRange[1] - m.xRange[0]) * u;
      /** @type {any[]} */
      const row = [];
      let headDone = false;
      for (let k = 0; k < curve.points.length; k++) {
        const pt = curve.points[k];
        if (pt.x <= cut || u >= 1) {
          row.push({ x: pt.x, y: Number.isFinite(pt.y) && u > 0 ? pt.y : null });
          continue;
        }
        if (!headDone) {
          headDone = true;
          const prev = k > 0 ? curve.points[k - 1] : null;
          const ok = prev && Number.isFinite(prev.y) && Number.isFinite(pt.y) && u > 0;
          const f = ok && pt.x > prev.x ? (cut - prev.x) / (pt.x - prev.x) : 0;
          row.push({ x: cut, y: ok ? prev.y + (pt.y - prev.y) * f : null });
        }
        row.push({ x: pt.x, y: null });
      }
      if (!headDone) row.push({ x: m.xRange[1], y: null });
      key.push(`${u}`);
      data.push(row);
      parts.grow[si] = [u];
      show(parts.seriesLabels[si], curve.ok ? fade(C.end, LABEL_FADE) : axes);
    });
    const pts = plan.points.map((pt, j) => {
      const a = q(fade(T.points[j], LABEL_FADE));
      parts.pointAlpha[j] = a;
      show(parts.pointLabels[j], a);
      key.push(`p${a > 0 ? 1 : 0}`);
      return { x: pt.x, y: a > 0 ? pt.y : null };
    });
    data.push(pts);
  }
  const k = key.join('|');
  p.note(`chart:${k}`);
  const chart = parts.chart;
  if (chart && k !== parts.lastKey) {
    data.forEach((row, i) => {
      if (chart.data.datasets[i]) chart.data.datasets[i].data = row;
    });
    chart.update('none');
    parts.lastKey = k;
  }
}

// ---------------------------------------------------------------------------------------------
// Reporting and pointing
// ---------------------------------------------------------------------------------------------

/**
 * The chart's text on screen (preflight, §7.12): every drawn label, critical (it is the chart's content), on the
 * colour behind it, with its slot, size, minimum, weight and the overflow layout measured. The plot's own shortfall
 * (it could not keep its share at the minimum sizes) and a 9:16 list that runs past its box are reported on the first
 * label (they are the labels' fault: preflight shrinks or asks to shorten them).
 * @param {TemplateContext} ctx @param {ChartParts} parts
 * @returns {TextEntry[]}
 */
export function chartTexts(ctx, parts) {
  /** @type {TextEntry[]} */
  const out = [];
  const g = parts.geo;
  const extra = g ? Math.max(g.plotShort || 0, g.listOverflow || 0, g.seriesOverflow || 0) : 0;
  const labels = [parts.title, parts.yTitle, parts.xTitle, ...parts.yTicks, ...parts.xTicks, ...parts.cats, ...parts.seriesLabels,
    ...parts.values.flat(), ...parts.pointLabels];
  let first = true;
  for (const l of labels) {
    if (!l || !l.shown) continue;
    out.push({
      el: l.el, role: 'label', slot: l.slot, critical: true, ink: l.ink, bg: parts.colours.bg, alpha: l.alpha,
      overflow: Math.max(l.overflow, first ? extra : 0), size: l.size, min: l.min, weight: l.weight,
      text: l.el.textContent || '',
    });
    first = false;
  }
  return out;
}

/**
 * Where a look_here points (plan §1.7): `panel` the marks' area (the plot; a 9:16 list's rows; a shape), `panel:bar:<s>:<i>` a bar at its full size once it
 * has started to grow, `panel:point:<s>:<i>` a line's point once reached or a graph's marked point (series = the
 * number of functions) once shown (a ring). Whole stage px, read after `paintChart`; null for anything else or for a
 * mark not on screen.
 * @param {TemplateContext} ctx @param {ChartParts} parts @param {string} target
 * @returns {FocusRect | null}
 */
export function chartFocusRect(ctx, parts, target) {
  const g = parts.geo;
  const parsed = parseTarget(String(target || ''));
  if (!g || !parsed) return null;
  const plan = parts.plan;
  /** @type {{x0: number, y0: number, x1: number, y1: number} | null} */
  let r = null;
  /** @type {'rect' | 'ring'} */
  let shape = 'rect';
  let radius = 0;
  const s = typeof parsed.series === 'number' ? parsed.series : -1;
  const i = typeof parsed.index === 'number' ? parsed.index : -1;
  if (parsed.form === 'panel') {
    if (parts.axesAlpha > 0.01) r = g.area;
    radius = SPACE[0];
  } else if (parsed.form === 'panel:bar' && plan.kind === 'bar') {
    if (s >= 0 && s < plan.series.length && i >= 0 && i < plan.categories.length && parts.grow[s][i] > 0.01) {
      r = barRect(g, plan, s, i);
      radius = STROKE.barRadius;
    }
  } else if (parsed.form === 'panel:point') {
    let c = null;
    if (plan.kind === 'line' && s >= 0 && s < plan.series.length && i >= 0 && i < plan.categories.length && parts.grow[s][i] > 0) {
      c = { x: xPixel(g, i), y: yPixel(g, plan.series[s].values[i]) };
    }
    if (plan.kind === 'graph' && s === plan.functions.length && i >= 0 && i < plan.points.length && parts.pointAlpha[i] > 0.01) {
      c = { x: xPixel(g, plan.points[i].x), y: yPixel(g, plan.points[i].y) };
    }
    if (c) {
      const rr = STROKE.point + SPACE[0];
      r = { x0: c.x - rr, y0: c.y - rr, x1: c.x + rr, y1: c.y + rr };
      shape = 'ring';
      radius = rr;
    }
  }
  if (!r) return null;
  const x0 = Math.round(r.x0);
  const y0 = Math.round(r.y0);
  const x1 = Math.max(x0 + 1, Math.round(r.x1));
  const y1 = Math.max(y0 + 1, Math.round(r.y1));
  return { rect: { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }, shape, radius, el: parts.el, kind: 'shape' };
}
