// @ts-check
// chart_build (DYNAMIC_VIDEO_DESIGN §4.3 B, R1, quantity; owner decision 1 of 2026-10-16) and its chart engine
// (templates/_charts.js, which other templates' chart / graph panels use too): the template contract and order
// independence on every fixture (tests/compose/dynamic/fixtures/templates/chart_build.*.json: both formats, a dark and a
// light family each, bars, lines and graphs, Latin, Tamil and Devanagari) with the real Chart.js drawing into a
// recording canvas, then the behaviour: which scenes it draws (drawsScene <-> chartPlan, the CG-2 rule the Python fit
// mirrors), the limits and text budgets, the ticks, the timing of every mark (draw / count events, the personality's
// scale), the engine's pixel mapping against the real Chart.js's own elements, what it reports to preflight, where a
// look_here points, the canvas key, the fallback to board_generic, and the catalogue's slots once registered. Each
// behaviour test was seen to fail on a mutation of the module it tests.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';

import { installDom, uninstallDom } from '../../player/_dom.js';
import chartBuild, {
  CHART_DELAY, COMFORT, HEADLINE_DELAY, LAYOUT, ROW_DELAY, boxesOf, drawsScene, slotRows, texOf, wordsOf,
} from '../../../js/dynamic/templates/chart_build.js';
import {
  AXES_ENTER, BAR_GROW, BAR_STAGGER, CHART_PANEL_KINDS, CHART_TYPES, COUNT_UP, LIMITS, LINE_DRAW, SERIES_STAGGER, SIZES,
  SPACE, barRect, buildChart, categoryLabelsFit, chartPlan, chartTexts, chartTimes, codePoints, coverTicks, dimGroup,
  drawsChart, formatNumber, insideTicks, labelRange, layoutChart, paintChart, placeBeside, scaled, segmentMeetsRect,
  seriesRoles, spread, valueAxis, valueDecimals, wrapLines, xPixel, yPixel,
} from '../../../js/dynamic/templates/_charts.js';
import boardGeneric from '../../../js/dynamic/templates/board_generic.js';
import { CATALOGUE } from '../../../js/dynamic/catalogue.gen.js';
import { Painter, templateSlots } from '../../../js/dynamic/core.js';
import { contractProblems, orderProblems, recordingTex, renderStage } from './_harness.js';
import { installChartEnv } from './_chartenv.js';

/** @type {{contexts: any[], Chart: any}} */
let env;
before(() => {
  installDom();
  env = installChartEnv();
});
after(() => uninstallDom());

const DIR = new URL('../../../../tests/compose/dynamic/fixtures/templates/', import.meta.url);
const FIXTURES = readdirSync(DIR).filter((n) => n.startsWith('chart_build.') && n.endsWith('.json')).sort();
/** @param {string} name @returns {any} */
const load = (name) => JSON.parse(readFileSync(new URL(name, DIR), 'utf-8'));
const TEMPLATES = { chart_build: chartBuild };

// ---------------------------------------------------------------------------------------------
// The fixtures through the contract and the order gate
// ---------------------------------------------------------------------------------------------

test('the fixtures: both formats with a dark and a light family each; bars, lines and graphs; Latin and Indic text', () => {
  const seen = new Set();
  const kinds = new Set();
  let indic = 0;
  for (const n of FIXTURES) {
    const tl = load(n);
    seen.add(`${tl.presentation.format}:${tl.presentation.mode}`);
    const sp = tl.scenes[0].side_panel;
    kinds.add(`${tl.presentation.format}:${sp.panel.kind === 'chart' ? sp.panel.chart.chart_type : 'graph'}`);
    if (/[ऀ-ॿ஀-௿]/.test(JSON.stringify(tl.scenes[0]))) indic += 1;
    assert.ok(drawsScene(tl.scenes[0], tl.presentation.format), `${n}: the template draws its own fixture`);
  }
  for (const want of ['landscape:dark', 'landscape:light', 'portrait:dark', 'portrait:light']) assert.ok(seen.has(want), want);
  for (const want of ['landscape:bar', 'landscape:line', 'landscape:graph', 'portrait:bar', 'portrait:line', 'portrait:graph']) {
    assert.ok(kinds.has(want), want);
  }
  assert.ok(indic >= 2, 'a Tamil and a Devanagari fixture');
});

for (const name of FIXTURES) {
  test(`${name}: the template contract (plan §1.4, §1.5) and order independence (§7.4)`, async () => {
    const tl = load(name);
    assert.deepEqual(await contractProblems(chartBuild, tl), []);
    const last = Math.round(tl.total_duration * tl.fps) - 1;
    const frames = [0, 4, 9, 12, 20, 30, 45, 70, 100, 130, 160, 200, 230, last].filter((f) => f <= last);
    assert.deepEqual(await orderProblems(tl, frames, { templates: TEMPLATES }), []);
  });
}

// ---------------------------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------------------------

/** @param {any} tl @param {{mode?: 'live' | 'render', reducedMotion?: boolean}} [opts] */
function mount(tl, opts = {}) {
  /** @type {Map<number, any>} */
  const ctxs = new Map();
  const wrapped = { ...chartBuild, build(/** @type {any} */ ctx) { ctxs.set(ctx.index, ctx); return chartBuild.build(ctx); } };
  const tex = recordingTex();
  const { stage, done } = renderStage(tl, { templates: { chart_build: wrapped }, renderTex: tex.renderTex, ...opts });
  return { stage, done, tex, ctx: (i = 0) => ctxs.get(i), view: (i = 0) => ctxs.get(i).view };
}

/** A fixture with its first scene edited. @param {string} name @param {(s: any, tl: any) => void} edit */
function edited(name, edit) {
  const tl = load(name);
  edit(tl.scenes[0], tl);
  return tl;
}

/** @param {any} panel @returns {any} a scene showing `panel` beside `board` */
function sceneWith(panel, board = [{ id: 'h1', kind: 'heading', text: 'A heading' }]) {
  return { scene_id: 's', index: 0, type: 'content', title: 'T', start: 0, duration: 10, board, beats: [],
    side_panel: { panel, show_at: 0 }, layout: { show_side_panel: true } };
}

/** @param {Partial<any>} [over] */
const bar = (over = {}) => ({ kind: 'chart', title: 'C', chart: { chart_type: 'bar', labels: ['a', 'b', 'c'], datasets: [{ label: 'one', data: [1, 2, 3] }], ...over } });

/** Seek a stage to scene time t of scene 0. @param {any} stage @param {any} tl @param {number} t */
async function at(stage, tl, t) {
  const f = Math.max(0, Math.min(Math.round(tl.total_duration * tl.fps) - 1, Math.round(t * tl.fps)));
  await stage.seek(f);
}

// ---------------------------------------------------------------------------------------------
// Which scenes it draws (drawsScene <-> chartPlan; charts.py mirrors chartPlan, the fit refuses what this refuses)
// ---------------------------------------------------------------------------------------------

test('drawsScene: a shown chart or graph panel chartPlan draws, at most one heading beside it, nothing else', () => {
  assert.deepEqual([...CHART_PANEL_KINDS], ['chart', 'graph']);
  assert.deepEqual([...CHART_TYPES], ['bar', 'line']);
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    assert.equal(drawsScene(sceneWith(bar()), fmt), true, fmt);
    assert.equal(drawsScene(sceneWith(bar(), []), fmt), true, `${fmt}: the panel alone`);
    assert.equal(drawsScene(sceneWith({ kind: 'graph', graph: { functions: [{ expr: 'x' }] } }), fmt), true, `${fmt}: a graph`);
    assert.equal(drawsScene(sceneWith(bar(), [{ id: 'h1', kind: 'heading', text: 'a' }, { id: 'h2', kind: 'heading', text: 'b' }]), fmt), false, 'two headings');
    assert.equal(drawsScene(sceneWith(bar(), [{ id: 'b1', kind: 'bullet', text: 'a' }]), fmt), false, 'a bullet');
    assert.equal(drawsScene({ ...sceneWith(bar()), side_panel: null }, fmt), false, 'no panel');
    assert.equal(drawsScene({ ...sceneWith(bar()), layout: { show_side_panel: false } }, fmt), false, 'a hidden panel');
    assert.equal(drawsScene(sceneWith({ kind: 'image', title: 'x' }), fmt), false, 'a picture');
    assert.equal(drawsScene(sceneWith(bar({ chart_type: 'pie' })), fmt), false, 'a pie');
    assert.equal(drawsScene({ ...sceneWith(bar()), quiz: { question: 'q' } }, fmt), false, 'a quiz');
    assert.equal(drawsScene({ ...sceneWith(bar()), media: { kind: 'image', asset_key: 'a' } }, fmt), false, 'a medium');
  }
  // the 9:16 bar list: one series of non-negative values, at most LIMITS.portrait.barCategories
  assert.equal(drawsScene(sceneWith(bar({ datasets: [{ data: [1, 2, 3] }, { data: [1, 2, 3] }] })), 'landscape'), true);
  assert.equal(drawsScene(sceneWith(bar({ datasets: [{ data: [1, 2, 3] }, { data: [1, 2, 3] }] })), 'portrait'), false);
  assert.equal(drawsScene(sceneWith(bar({ datasets: [{ data: [1, -2, 3] }] })), 'landscape'), true);
  assert.equal(drawsScene(sceneWith(bar({ datasets: [{ data: [1, -2, 3] }] })), 'portrait'), false);
  assert.equal(drawsScene(sceneWith(bar()), undefined), true, 'the default format is 16:9');
});

test('chartPlan: the limits per format, every value finite, every label within its budget (charts.py mirrors it)', () => {
  const L = LIMITS.landscape;
  const P = LIMITS.portrait;
  const labels = (/** @type {number} */ n) => Array.from({ length: n }, (_, i) => `c${i}`);
  const data = (/** @type {number} */ n) => Array.from({ length: n }, (_, i) => i + 1);
  const chart = (/** @type {string} */ type, /** @type {number} */ n, /** @type {number} */ ns) => ({
    kind: 'chart', chart: { chart_type: type, labels: labels(n), datasets: Array.from({ length: ns }, () => ({ data: data(n) })) },
  });
  assert.ok(chartPlan(chart('bar', L.barCategories, 2), 'landscape'));
  assert.equal(chartPlan(chart('bar', L.barCategories + 1, 1), 'landscape'), null);
  assert.equal(chartPlan(chart('bar', 2, L.barSeries + 1), 'landscape'), null);
  assert.equal(chartPlan(chart('bar', 9, 3), 'landscape'), null, `${9 * 3} bars > ${L.bars}`);
  assert.ok(chartPlan(chart('line', L.lineCategories, L.lineSeries), 'landscape'));
  assert.equal(chartPlan(chart('line', L.lineCategories + 1, 1), 'landscape'), null);
  assert.equal(chartPlan(chart('line', 3, L.lineSeries + 1), 'landscape'), null);
  assert.ok(chartPlan(chart('bar', P.barCategories, 1), 'portrait'));
  assert.equal(chartPlan(chart('bar', P.barCategories + 1, 1), 'portrait'), null);
  assert.ok(chartPlan(chart('line', P.lineCategories, P.lineSeries), 'portrait'));
  assert.equal(chartPlan(chart('line', P.lineCategories + 1, 1), 'portrait'), null);
  // a 16:9 side panel ('panel'): the vertical layouts with fewer marks than chart_build's own 16:9 box
  const N = LIMITS.panel;
  assert.ok(N.barCategories < L.barCategories && N.lineCategories < L.lineCategories && N.plotW < L.plotW);
  assert.ok(chartPlan(chart('bar', N.barCategories, 1), 'panel'));
  assert.ok(chartPlan(chart('bar', N.bars / N.barSeries, N.barSeries), 'panel'));
  assert.equal(chartPlan(chart('bar', N.bars / N.barSeries + 1, N.barSeries), 'panel'), null, 'past the bars in all');
  assert.equal(/** @type {any} */ (chartPlan(chart('bar', N.barCategories, 1), 'panel')).horizontal, false);
  assert.equal(chartPlan(chart('bar', N.barCategories + 1, 1), 'panel'), null);
  assert.equal(chartPlan(chart('bar', 2, N.barSeries + 1), 'panel'), null);
  assert.equal(/** @type {any} */ (chartPlan(chart('bar', 3, 1), 'portrait')).horizontal, true, 'the 9:16 list');
  // chart_type defaults to bar; a value that is not a finite number refuses the chart
  assert.equal(/** @type {any} */ (chartPlan({ kind: 'chart', chart: { labels: ['a'], datasets: [{ data: [1] }] } }, 'landscape')).kind, 'bar');
  for (const bad of [Infinity, NaN, '3', null, true]) {
    assert.equal(chartPlan({ kind: 'chart', chart: { labels: ['a'], datasets: [{ data: [bad] }] } }, 'landscape'), null, String(bad));
  }
  assert.equal(chartPlan({ kind: 'chart', chart: { labels: ['a', 'b'], datasets: [{ data: [1] }] } }, 'landscape'), null, 'one value per label');
  // text budgets (code points of the stored text)
  const long = (/** @type {number} */ n) => 'x'.repeat(n);
  assert.ok(chartPlan(bar({ y_label: long(L.axisChars) }), 'landscape'));
  assert.equal(chartPlan(bar({ y_label: long(L.axisChars + 1) }), 'landscape'), null);
  assert.equal(chartPlan(bar({ x_label: long(P.axisChars + 1) }), 'portrait'), null);
  assert.equal(chartPlan(bar({ datasets: [{ label: long(L.seriesChars + 1), data: [1, 2, 3] }] }), 'landscape'), null);
  assert.equal(chartPlan(bar({ labels: ['a', 'b', long(P.categoryChars + 1)] }), 'portrait'), null);
  assert.ok(chartPlan(bar({ labels: ['a', 'b', long(P.categoryChars)] }), 'portrait'));
  // graphs: ranges, functions' labels (their own, else `y = <expr>`), points
  const graph = (/** @type {any} */ over) => ({ kind: 'graph', graph: { functions: [{ expr: 'x^2' }], ...over } });
  assert.ok(chartPlan(graph({}), 'portrait'));
  assert.equal(chartPlan(graph({ functions: [], points: [] }), 'landscape'), null);
  assert.equal(chartPlan(graph({ x_range: [2, 1] }), 'landscape'), null);
  assert.equal(chartPlan(graph({ y_range: [0, 0] }), 'landscape'), null);
  assert.equal(chartPlan(graph({ functions: [{ expr: long(L.functionChars) }] }), 'landscape'), null, '`y = ` + the expression');
  assert.ok(chartPlan(graph({ functions: [{ expr: long(L.functionChars), label: 'short' }] }), 'landscape'));
  assert.equal(chartPlan(graph({ points: [{ x: 1, y: 1, label: long(P.pointChars + 1) }] }), 'portrait'), null);
  assert.equal(chartPlan(graph({ functions: Array.from({ length: 5 }, () => ({ expr: 'x' })) }), 'landscape'), null);
  // any other kind
  assert.equal(chartPlan({ kind: 'model_3d' }, 'landscape'), null);
  assert.equal(chartPlan(null, 'landscape'), null);
  assert.equal(drawsChart(bar(), 'landscape'), true);
});

test('wrapLines / categoryLabelsFit: two lines of the band, else every label short enough to be slanted', () => {
  assert.equal(wrapLines('LED Bulb', 8), 1);
  assert.equal(wrapLines('LED Bulb', 5), 2);
  assert.equal(wrapLines('abc defghij klmn', 10), 3);
  assert.equal(wrapLines('Refrigerators', 8), Infinity, 'a word longer than a line');
  assert.equal(wrapLines('  a  b ', 1), 2, 'empty pieces of runs of spaces are dropped');
  assert.equal(codePoints('𝑥²'), 2, 'code points, as Python counts them');
  // 16:9: 5 categories have 7 code points a line (1000 / 5 / 28)
  assert.equal(categoryLabelsFit(['LED Bulb', 'Fan', 'Fridge', 'AC', 'Geyser'], 'landscape'), true);
  assert.equal(categoryLabelsFit(['Air conditioner', 'Fan'], 'landscape'), true, 'wraps in a wide band');
  assert.equal(categoryLabelsFit(Array.from({ length: 12 }, () => 'Refrigerator'), 'landscape'), true, '12 code points slant');
  assert.equal(categoryLabelsFit([...Array.from({ length: 11 }, () => 'Fan'), 'Air conditioner unit'], 'landscape'), false);
  assert.equal(categoryLabelsFit(['2019', '2020', '2021', '2022', '2023', '2024'], 'portrait'), true);
  assert.equal(categoryLabelsFit(['Jan 2019', 'September'], 'portrait'), false, '9 code points past 9:16 slant budget');
});

// ---------------------------------------------------------------------------------------------
// Numbers and ticks
// ---------------------------------------------------------------------------------------------

test('coverTicks / insideTicks / valueAxis: nice steps that hold the data, the smallest within the count', () => {
  assert.deepEqual(coverTicks(0, 15, 5), { ticks: [0, 5, 10, 15], step: 5 });
  assert.deepEqual(coverTicks(3.9, 7.4, 3), { ticks: [2.5, 5, 7.5], step: 2.5 });
  assert.deepEqual(coverTicks(0, 9, 3), { ticks: [0, 5, 10], step: 5 });
  // a range across 0 needs 3 ticks at the fewest: the smallest step that gets there, never a huge one
  assert.deepEqual(coverTicks(-0.72, 9.72, 2), { ticks: [-10, 0, 10], step: 10 });
  assert.deepEqual(coverTicks(0.1, 0.35, 4), { ticks: [0.1, 0.2, 0.3, 0.4], step: 0.1 });
  assert.deepEqual(insideTicks(-3, 3, 7), { ticks: [-3, -2, -1, 0, 1, 2, 3], step: 1 });
  assert.deepEqual(insideTicks(0, 5, 3), { ticks: [0, 2, 4], step: 2 }, 'the smallest step within the count');
  assert.deepEqual(insideTicks(0, 5, 2), { ticks: [0, 5], step: 5 });
  assert.equal(insideTicks(0.1, 0.95, 2).ticks.length >= 2, true, 'never fewer than 2 inside');
  const plan = /** @type {any} */ (chartPlan(bar({ datasets: [{ data: [0.04, 0.3, 15] }], labels: ['a', 'b', 'c'] }), 'landscape'));
  assert.deepEqual(valueAxis(plan, 5).ticks, [0, 5, 10, 15], 'bars hold 0');
  const neg = /** @type {any} */ (chartPlan(bar({ datasets: [{ data: [-3, 2, 4] }] }), 'landscape'));
  assert.ok(valueAxis(neg, 6).min <= -3 && valueAxis(neg, 6).max >= 4 && valueAxis(neg, 6).ticks.includes(0));
  const line = /** @type {any} */ (chartPlan({ kind: 'chart', chart: { chart_type: 'line', labels: ['a', 'b'], datasets: [{ data: [3.9, 7.4] }] } }, 'landscape'));
  assert.ok(valueAxis(line, 3).min > 0, 'a line far from 0 does not reach it');
  const near = /** @type {any} */ (chartPlan({ kind: 'chart', chart: { chart_type: 'line', labels: ['a', 'b'], datasets: [{ data: [1, 6.5] }] } }, 'landscape'));
  assert.equal(valueAxis(near, 4).min, 0, 'a line close to 0 holds it');
});

test('formatNumber / valueDecimals: the shortest form, the locale\'s grouping, a typographic minus, no -0', () => {
  assert.equal(valueDecimals(0.04), 2);
  assert.equal(valueDecimals(15), 0);
  assert.equal(valueDecimals(1.5), 1);
  assert.equal(valueDecimals(0.1 + 0.2), 1, 'float noise is not a decimal');
  assert.equal(formatNumber(1500000, 0, 'en-IN'), '15,00,000');
  assert.equal(formatNumber(1500000, 0, 'en-US'), '1,500,000');
  assert.equal(formatNumber(-2.5, 1, 'en'), '−2.5');
  assert.equal(formatNumber(-0.0001, 2, 'en'), '0.00');
  assert.equal(formatNumber(3, 1, 'ta-IN'), '3.0');
});

// ---------------------------------------------------------------------------------------------
// Timing (chartTimes)
// ---------------------------------------------------------------------------------------------

test('chartTimes: the axes, then bars 80 ms apart by category, a draw event revealing its own bar, counts from their event', () => {
  const plan = /** @type {any} */ (chartPlan(bar({ datasets: [{ data: [1, 2, 3] }, { data: [3, 2, 1] }] }), 'landscape'));
  const T = chartTimes(plan, [], 0.3, 1);
  assert.equal(T.axes, 0.3);
  const first = 0.3 + AXES_ENTER;
  assert.deepEqual(T.bars.map((r) => r.map((x) => Math.round(x * 1000))), [
    [first, first + 2 * BAR_STAGGER, first + 4 * BAR_STAGGER].map((x) => Math.round(x * 1000)),
    [first + BAR_STAGGER, first + 3 * BAR_STAGGER, first + 5 * BAR_STAGGER].map((x) => Math.round(x * 1000)),
  ]);
  assert.deepEqual(T.counts, [[null, null, null], [null, null, null]], 'values count with their bars');
  const own = chartTimes(plan, [{ t: 5, kind: 'draw', target: 'panel:bar:1:2' }, { t: 6, kind: 'count', target: 'panel:bar:0:0' },
    { t: 0.1, kind: 'draw', target: 'panel:bar:0:1' }, { t: 9, kind: 'reveal', target: 'panel:bar:0:2' }], 0.3, 1);
  assert.equal(own.bars[1][2], 5, 'a draw reveals its bar on its beat');
  assert.equal(own.bars[0][1], 0.3, 'never before the axes');
  assert.equal(own.bars[0][2], first + 4 * BAR_STAGGER, 'another kind is not drawn');
  assert.equal(own.counts[0][0], 6);
  // the personality's scale stretches the template's own durations
  const soft = chartTimes(plan, [], 0, 1.15);
  assert.ok(Math.abs(soft.bars[0][1] - 1.15 * (AXES_ENTER + 2 * BAR_STAGGER)) < 1e-9);
  // a draw on the panel starts the build
  assert.equal(chartTimes(plan, [{ t: 4, kind: 'draw', target: 'panel' }], 0.3, 1).bars[0][0], 4);
});

test('chartTimes: a line reaches its points in order, waiting at a point whose own beat has not come; curves and points follow', () => {
  const plan = /** @type {any} */ (chartPlan({ kind: 'chart', chart: { chart_type: 'line', labels: ['a', 'b', 'c', 'd'],
    datasets: [{ data: [1, 2, 3, 4] }, { data: [2, 3, 4, 5] }] } }, 'landscape'));
  const seg = LINE_DRAW / 3;
  const T = chartTimes(plan, [{ t: 6, kind: 'draw', target: 'panel:point:0:2' }], 0, 1);
  const L0 = T.lines[0];
  assert.equal(L0.start, AXES_ENTER);
  assert.ok(Math.abs(L0.arrive[1] - (AXES_ENTER + seg)) < 1e-9);
  assert.equal(L0.depart[2], 6, 'waits at point 1 for point 2\'s beat');
  assert.ok(Math.abs(L0.arrive[3] - (6 + 2 * seg)) < 1e-9, 'then goes on');
  assert.ok(Math.abs(T.lines[1].start - (AXES_ENTER + SERIES_STAGGER)) < 1e-9, 'series stagger');
  const g = /** @type {any} */ (chartPlan({ kind: 'graph', graph: { functions: [{ expr: 'x' }, { expr: 'x/2' }], points: [{ x: 1, y: 1 }, { x: 2, y: 1 }] } }, 'landscape'));
  const G = chartTimes(g, [{ t: 7, kind: 'draw', target: 'panel:point:2:1' }], 0, 1);
  assert.equal(G.curves[1].start, AXES_ENTER + SERIES_STAGGER);
  assert.equal(G.points[0], G.curves[1].end, 'marked points follow the curves');
  assert.equal(G.points[1], 7, 'or come on their own beat');
});

// ---------------------------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------------------------

test('the engine\'s pixel mapping is the real Chart.js\'s: bars (barThickness, grouped), line points and graph samples', async () => {
  for (const name of ['chart_build.landscape.json', 'chart_build.landscape.light.json', 'chart_build.portrait.json', 'chart_build.landscape.graph.json']) {
    const tl = load(name);
    const m = mount(tl);
    try {
      await at(m.stage, tl, tl.total_duration - 0.01);
      const parts = m.view().chart;
      const ch = parts.chart;
      assert.ok(ch, `${name}: a chart was made`);
      const g = parts.geo;
      const ox = g.canvas.x0;
      const oy = g.canvas.y0;
      assert.deepEqual([ch.chartArea.left + ox, ch.chartArea.top + oy, ch.chartArea.right + ox, ch.chartArea.bottom + oy].map(Math.round),
        [g.plot.x0, g.plot.y0, g.plot.x1, g.plot.y1].map(Math.round), `${name}: the chart area is the plot`);
      const plan = parts.plan;
      if (plan.kind === 'bar') {
        plan.series.forEach((/** @type {any} */ s, /** @type {number} */ si) => {
          ch.getDatasetMeta(si).data.forEach((/** @type {any} */ el, /** @type {number} */ i) => {
            const r = barRect(g, plan, si, i);
            const p = el.getProps(['x', 'y', 'base', 'width', 'height'], true);
            if (plan.horizontal) {
              assert.ok(Math.abs(p.y + oy - (r.y0 + r.y1) / 2) < 0.5 && Math.abs(Math.max(p.x, p.base) + ox - r.x1) < 0.5, `${name} bar ${si}:${i}`);
            } else {
              assert.ok(Math.abs(p.x + ox - (r.x0 + r.x1) / 2) < 0.5 && Math.abs(p.width - (r.x1 - r.x0)) < 0.5, `${name} bar ${si}:${i} x`);
              assert.ok(Math.abs(Math.min(p.y, p.base) + oy - r.y0) < 0.5 && Math.abs(Math.max(p.y, p.base) + oy - r.y1) < 0.5, `${name} bar ${si}:${i} y`);
            }
          });
        });
      } else {
        const scaleX = ch.scales.x;
        const scaleY = ch.scales.y;
        for (const v of [g.x.min, g.x.max, (g.x.min + g.x.max) / 2]) assert.ok(Math.abs(scaleX.getPixelForValue(v) + ox - xPixel(g, v)) < 0.5, `${name} x ${v}`);
        for (const v of [g.y.min, g.y.max, (g.y.min + g.y.max) / 3]) assert.ok(Math.abs(scaleY.getPixelForValue(v) + oy - yPixel(g, v)) < 0.5, `${name} y ${v}`);
      }
      // the gridlines are at exactly the engine's ticks
      if (!plan.horizontal) assert.deepEqual(ch.scales.y.ticks.map((/** @type {any} */ t) => t.value), g.yTicks);
    } finally {
      m.done();
    }
  }
});

test('the canvas holds marks only: no text drawn on it, every label page DOM reported to preflight', async () => {
  const tl = load('chart_build.landscape.graph.json');
  const m = mount(tl);
  try {
    await at(m.stage, tl, tl.total_duration - 0.01);
    const parts = m.view().chart;
    const calls = parts.chart.ctx.calls;
    assert.ok(calls.length > 0, 'it draws');
    assert.ok(!calls.some((/** @type {any[]} */ c) => c[0] === 'fillText' || c[0] === 'strokeText'), 'no canvas text');
    const cfg = parts.chart.config.options;
    assert.equal(cfg.animation, false);
    assert.equal(cfg.responsive, false);
    assert.deepEqual(cfg.events, []);
    assert.equal(cfg.devicePixelRatio, 1);
    const texts = chartBuild.texts(m.ctx());
    const slots = new Set(texts.map((t) => t.slot));
    for (const s of ['title', 'caption', 'axis', 'tick', 'series']) assert.ok(slots.has(s), s);
    for (const e of texts) {
      assert.equal(e.critical, true);
      assert.match(String(e.ink), /^#[0-9a-f]{6}$/i);
      assert.equal(e.bg, m.ctx().onBackground.bg);
      assert.ok(e.size >= e.min, `${e.slot} ${e.size} >= ${e.min}`);
    }
    // the series' labels in their series' colours (direct labels, R-27), the points' in ink
    const fnLabels = texts.filter((e) => e.slot === 'series' && /ohms/.test(String(e.text)));
    assert.deepEqual(fnLabels.map((e) => e.ink), parts.styles.slice(0, 2).map((/** @type {any} */ s) => s.colour));
  } finally {
    m.done();
  }
});

test('bars grow from 0 and their values count up with them; a count event counts on its own beat', async () => {
  const tl = load('chart_build.landscape.json');
  const m = mount(tl);
  try {
    await at(m.stage, tl, 0);
    const T = m.view().chart.times;
    const start = T.bars[0][0];
    await at(m.stage, tl, start + 0.3 * BAR_GROW);
    const parts = m.view().chart;
    const data = parts.chart.data.datasets[0].data;
    assert.ok(data[0] !== null && data[0] > 0 && data[0] < 0.04, `growing: ${data[0]}`);
    assert.equal(data[3], null, 'the AC waits for its beat');
    const v0 = parts.values[0][0];
    assert.notEqual(v0.el.textContent, '0.04', 'still counting');
    await at(m.stage, tl, tl.total_duration - 0.01);
    assert.deepEqual(parts.chart.data.datasets[0].data, [0.04, 0.3, 1.5, 7, 15]);
    assert.deepEqual(parts.values[0].map((/** @type {any} */ l) => l.el.textContent), ['0.04', '0.3', '1.5', '7', '15']);
    // a count event
    const counted = edited('chart_build.landscape.json', (s) => s.events.push({ t: 7.0, kind: 'count', target: 'panel:bar:0:2', anchored: true }));
    const c = mount(counted);
    try {
      await at(c.stage, counted, 7.0 + COUNT_UP / 2);
      assert.notEqual(c.view().chart.values[0][2].el.textContent, '1.5');
      await at(c.stage, counted, 6.9);
      assert.equal(c.view().chart.values[0][2].alpha, 0, 'hidden before its count');
    } finally {
      c.done();
    }
  } finally {
    m.done();
  }
});

test('focusRect: the plot, a bar once it grows, a reached point, a shown graph point (whole px, shapes); null otherwise', async () => {
  const tl = load('chart_build.landscape.json');
  const m = mount(tl);
  try {
    await at(m.stage, tl, 0);
    assert.equal(chartBuild.focusRect?.(m.ctx(), 'panel'), null, 'the axes are not in yet');
    assert.equal(chartBuild.focusRect?.(m.ctx(), 'panel:bar:0:4'), null);
    await at(m.stage, tl, 8.4);
    const p = chartBuild.focusRect?.(m.ctx(), 'panel');
    const b = chartBuild.focusRect?.(m.ctx(), 'panel:bar:0:4');
    assert.ok(p && b);
    for (const r of [p, b]) {
      assert.ok(['x', 'y', 'w', 'h'].every((k) => Number.isInteger(/** @type {any} */ (r).rect[k])));
      assert.equal(/** @type {any} */ (r).kind, 'shape');
    }
    const want = barRect(m.view().chart.geo, m.view().chart.plan, 0, 4);
    assert.equal(/** @type {any} */ (b).rect.x, Math.round(want.x0));
    assert.equal(chartBuild.focusRect?.(m.ctx(), 'panel:bar:1:0'), null, 'no such series');
    assert.equal(chartBuild.focusRect?.(m.ctx(), 'panel:point:0:0'), null, 'a bar chart has no points');
    assert.equal(chartBuild.focusRect?.(m.ctx(), 'h1'), null);
  } finally {
    m.done();
  }
  const g = load('chart_build.landscape.graph.json');
  const n = mount(g);
  try {
    await at(n.stage, g, g.total_duration - 0.01);
    const r = chartBuild.focusRect?.(n.ctx(), 'panel:point:2:1');
    assert.ok(r && r.shape === 'ring', 'a marked point (series = the number of functions)');
    assert.equal(chartBuild.focusRect?.(n.ctx(), 'panel:point:0:1'), null, 'a function has no marked points');
  } finally {
    n.done();
  }
});

test('the frame key notes the canvas data: other data never shares a key, the same data always does', async () => {
  const tl = load('chart_build.landscape.light.json');
  const m = mount(tl);
  try {
    await at(m.stage, tl, 0);
    const ctx = m.ctx();
    const parts = m.view().chart;
    // paint the chart alone into a fresh painter: only the chart's own writes and its note make the key
    const keyAt = (/** @type {number} */ t) => {
      const painter = new Painter();
      painter.begin();
      paintChart({ ...ctx, paint: painter }, parts, t);
      return painter.hash.digest();
    };
    const L = parts.times.lines[0];
    const mid = (L.depart[1] + L.arrive[1]) / 2;
    // two instants inside one segment of the line: the same labels, other canvas data
    assert.notEqual(keyAt(mid), keyAt(mid + 0.02), 'the line\'s head moved');
    assert.equal(keyAt(mid), keyAt(mid), 'a pure function of t');
    assert.equal(keyAt(tl.total_duration - 0.5), keyAt(tl.total_duration - 0.01), 'a hold repeats its key');
  } finally {
    m.done();
  }
});

test('the layout: the chart takes the column under the headline (and caption), inside the critical box, both formats', async () => {
  for (const name of FIXTURES) {
    const tl = load(name);
    const m = mount(tl);
    try {
      await at(m.stage, tl, 1);
      const v = m.view();
      const fmt = tl.presentation.format;
      const B = boxesOf(fmt);
      const g = v.chart.geo;
      assert.ok(g.box.x0 === B.column.x0 && g.box.x1 === B.column.x1 && g.box.y1 === B.bottom, name);
      assert.ok(g.plot.x0 >= B.column.x0 && g.plot.x1 <= B.column.x1, `${name}: the plot in the column`);
      assert.ok(v.titleSize >= LAYOUT[fmt].title.min && v.titleSize <= LAYOUT[fmt].title.max);
    } finally {
      m.done();
    }
  }
  assert.ok(COMFORT > 0 && COMFORT < 1);
  // 9:16 keeps every text in the column both critical bands hold (x 192-780)
  assert.deepEqual([boxesOf('portrait').column.x0, boxesOf('portrait').column.x1], [192, 780]);
});

test('wordsOf: the heading, else the scene title, else the chart\'s title; the chart\'s title as the caption when it differs', () => {
  const s = sceneWith(bar({}), [{ id: 'h1', kind: 'heading', text: 'Head $x$' }]);
  s.side_panel.panel.title = 'Own';
  assert.deepEqual(wordsOf(s), { headline: 'Head $x$', rich: true, item: 'h1', caption: 'Own' });
  const t = sceneWith(bar({}), []);
  t.side_panel.panel.title = 'Own';
  assert.deepEqual(wordsOf(t), { headline: 'T', rich: false, item: null, caption: 'Own' });
  t.title = '';
  assert.deepEqual(wordsOf(t), { headline: 'Own', rich: false, item: null, caption: '' });
  t.title = 'Own';
  assert.equal(wordsOf(t).caption, '', 'never the same text twice');
  assert.deepEqual(texOf(s), [{ tex: 'x', display: false }]);
  assert.deepEqual(texOf(t), []);
});

test('the headline enters on its beat, the caption ROW_DELAY later, the axes from the panel\'s own start', async () => {
  const tl = load('chart_build.landscape.json');
  const m = mount(tl);
  try {
    await at(m.stage, tl, 0);
    const v = m.view();
    assert.equal(v.titleEnter, Math.max(0, tl.scenes[0].beats[0].start - 0.12));
    assert.equal(v.captionEnter, v.titleEnter + ROW_DELAY);
    assert.equal(v.chart.times.axes, CHART_DELAY);
  } finally {
    m.done();
  }
  const later = edited('chart_build.landscape.light.json', (s) => { s.side_panel.show_at = 2.5; });
  const n = mount(later);
  try {
    await at(n.stage, later, 0);
    assert.equal(n.view().titleEnter, HEADLINE_DELAY, 'a scene title with no beat');
    assert.equal(n.view().chart.times.axes, 2.5);
  } finally {
    n.done();
  }
});

test('a scene it does not draw is board_generic\'s whole: build, layout, update, texts and the static hooks', async () => {
  const tl = edited('chart_build.landscape.json', (s) => { s.board.push({ id: 'b9', kind: 'bullet', text: 'one more' }); });
  const m = mount(tl);
  try {
    await at(m.stage, tl, 3);
    assert.equal(m.view().cbFallback, true);
    assert.equal(m.stage.el.querySelectorAll('.dyn-chart-canvas').length, 0);
    assert.deepEqual(chartBuild.texts?.(m.ctx()).map((e) => e.text), boardGeneric.texts?.(m.ctx()).map((e) => e.text));
    const zones = m.ctx().zones;
    assert.deepEqual(chartBuild.mediaBox?.(tl.scenes[0], zones, 'render', 'panel', { safe: false, slots: {} }),
      boardGeneric.mediaBox?.(tl.scenes[0], zones, 'render', 'panel', { safe: false, slots: {} }));
  } finally {
    m.done();
  }
  assert.equal(chartBuild.mediaBox?.(load('chart_build.landscape.json').scenes[0], /** @type {any} */ ({ format: 'landscape', width: 1920, height: 1080 }), 'render', 'panel', { safe: false, slots: {} }), null);
});

test('a preflight size cap and the safe layout reach every chart slot (slotRange); nothing goes below its minimum', async () => {
  const tl = edited('chart_build.landscape.json', (s) => { s.look.fixes = { safe: true, slots: {} }; });
  const m = mount(tl);
  try {
    await at(m.stage, tl, 9);
    for (const e of chartBuild.texts?.(m.ctx()) || []) assert.equal(e.size, e.min, `${e.slot} at its minimum`);
  } finally {
    m.done();
  }
});

test('preflight\'s scrim remedy (fixes.slots.title / caption): a scrim behind the text, reported as what it sits on', async () => {
  const tl = edited('chart_build.landscape.json', (s) => {
    s.look.fixes = { safe: false, slots: { title: { scrim: true }, caption: { scrim: true } } };
  });
  const m = mount(tl);
  try {
    await at(m.stage, tl, 3);
    const v = m.view();
    const ctx = m.ctx();
    const scrim = ctx.roles.color('scrim');
    assert.equal(v.titleScrim, scrim);
    assert.ok(v.titleBox.style.background, 'drawn behind the headline');
    assert.ok(v.captionBox.style.background, 'drawn behind the caption');
    const texts = chartBuild.texts?.(ctx) || [];
    const title = texts.find((e) => e.slot === 'title');
    const caption = texts.find((e) => e.slot === 'caption');
    assert.notEqual(title?.bg, ctx.onBackground.bg);
    assert.match(String(title?.bg), /^#[0-9a-f]{6}$/i);
    assert.notEqual(caption?.bg, ctx.onBackground.bg);
  } finally {
    m.done();
  }
  const plain = mount(load('chart_build.landscape.json'));
  try {
    await at(plain.stage, load('chart_build.landscape.json'), 3);
    assert.equal(plain.view().titleScrim, null);
    assert.equal(plain.view().titleBox.style.background, '');
  } finally {
    plain.done();
  }
});

test('a box too small for the chart reports its overflow (preflight blocks; nothing is clipped or left out)', async () => {
  const tl = load('chart_build.landscape.json');
  const m = mount(tl);
  try {
    await at(m.stage, tl, 0);
    const ctx = m.ctx();
    const parts = buildChart(ctx, /** @type {any} */ (chartPlan(tl.scenes[0].side_panel.panel, 'landscape')), {});
    ctx.root.appendChild(parts.el);
    // jsdom measures nothing, so give the labels a height: a 60 px box cannot hold a plot of 30% and its labels
    layoutChart(ctx, parts, { x0: 96, y0: 800, x1: 400, y1: 860 });
    paintChart(ctx, parts, 9);
    const texts = chartTexts(ctx, parts);
    assert.ok(texts.length > 0);
    assert.ok(texts.some((e) => Number(e.overflow) > 0), 'overflow reported');
    assert.equal(parts.geo.fits, false);
    for (const e of texts) assert.equal(e.size, e.min, `${e.slot}: every slot steps down to its minimum first`);
  } finally {
    m.done();
  }
});

test('a category label wider than its band is never broken inside a word: the labels are slanted instead', async () => {
  // a small layout model (jsdom has none): a word or the measuring line is 0.55 em a character wide
  const proto = /** @type {any} */ (window.HTMLElement.prototype);
  const saved = Object.getOwnPropertyDescriptor(proto, 'offsetWidth');
  const sizeOf = (/** @type {any} */ el) => {
    for (let e = el; e && e.style; e = e.parentElement) if (e.style.fontSize) return parseFloat(e.style.fontSize);
    return 16;
  };
  Object.defineProperty(proto, 'offsetWidth', {
    configurable: true,
    get() {
      const measured = this.classList.contains('dyn-word') || this.classList.contains('dyn-chart-measure');
      return measured ? (this.textContent || '').length * 0.55 * sizeOf(this) : 0;
    },
  });
  try {
    for (const [labels, slanted] of /** @type {[string[], boolean][]} */ ([
      [['Fan', 'AC', 'Geyser'], false],
      [Array.from({ length: 12 }, (_, i) => `Refrigerat${i}`), true],
    ])) {
      const tl = edited('chart_build.landscape.json', (s) => {
        s.side_panel.panel.chart.labels = labels;
        s.side_panel.panel.chart.datasets = [{ label: '', data: labels.map((_, i) => i + 1) }];
        s.events = [];
      });
      const m = mount(tl);
      try {
        await at(m.stage, tl, 1);
        const g = m.view().chart.geo;
        assert.equal(g.slanted, slanted, labels.join(','));
        for (const l of m.view().chart.cats) {
          assert.equal(l.wrap.style.transform, slanted ? 'rotate(-35deg)' : 'none');
          assert.equal(l.el.style.overflowWrap, 'normal', 'never broken inside a word');
        }
      } finally {
        m.done();
      }
    }
  } finally {
    if (saved) Object.defineProperty(proto, 'offsetWidth', saved);
    else delete proto.offsetWidth;
  }
});

test('the engine\'s own helpers are _shared.js\'s (it imports none of it, so _shared.js may import it for side panels)', async () => {
  const shared = await import('../../../js/dynamic/templates/_shared.js');
  const source = readFileSync(new URL('../../../js/dynamic/templates/_charts.js', import.meta.url), 'utf-8');
  assert.ok(!/from '\.\/_shared\.js'/.test(source), 'no import of _shared.js');
  assert.deepEqual([...SPACE], [...shared.SPACE]);
  for (const format of /** @type {const} */ (['landscape', 'portrait'])) {
    for (const look of [null, { fixes: { safe: true, slots: {} } }, { fixes: { safe: false, slots: { tick: { size: 41 } } } },
      { fixes: { safe: false, slots: { tick: { size: 12 } } } }, { fixes: { safe: false, slots: { tick: { scrim: true } } } },
      { text_fit: { tick: { size: 42 } } }]) {
      const ctx = /** @type {any} */ ({ format, look });
      for (const spec of [{ min: 40, max: 44 }, { min: 30, max: 60 }, { min: 50, max: 48 }]) {
        assert.deepEqual(labelRange(ctx, 'tick', spec), shared.slotRange(ctx, 'tick', { ...spec, role: 'label' }), JSON.stringify({ format, look, spec }));
      }
    }
  }
  for (const motion of [undefined, { scale: 0.85, entrance: 'x' }, { scale: 0, entrance: 'x' }]) {
    const ctx = /** @type {any} */ ({ motion });
    assert.equal(scaled(ctx, 0.6), shared.seconds(ctx, 0.6));
  }
  const a = dimGroup('x', document.createElement('span'));
  const b = shared.group('x', document.createElement('span'));
  assert.equal(a.className, b.className);
  assert.equal(a.outerHTML, b.outerHTML);
});

test('seriesRoles: a series in the lesson\'s entity map keeps its entity (R-30); the rest the free slots; then ink2 with dashes', () => {
  const ctx = /** @type {any} */ ({ entityRole: (/** @type {string} */ s) => (s === 'Nichrome' ? 'entity:1' : null) });
  assert.deepEqual(seriesRoles(ctx, ['Copper', 'Nichrome']).map((r) => r.role), ['entity:0', 'entity:1']);
  const mapped = /** @type {any} */ ({ entityRole: (/** @type {string} */ s) => ({ Copper: 'entity:2', Steel: 'entity:2' })[s] || null });
  assert.deepEqual(seriesRoles(mapped, ['Copper', 'Nichrome', 'Steel']).map((r) => r.role), ['entity:2', 'entity:0', 'entity:1'],
    'the mapped entity first; a second claim on it takes a free slot');
  const four = seriesRoles(ctx, ['a', 'b', 'c', 'd']);
  assert.deepEqual(four.map((r) => r.role), ['entity:0', 'entity:1', 'entity:2', 'ink2']);
  assert.ok(four[3].dash.length > 0 && four.slice(0, 3).every((r) => r.dash.length === 0));
});

test('spread / segmentMeetsRect / placeBeside: labels kept apart, lines kept off a label where the plot allows', () => {
  const s = spread([{ want: 10, size: 20 }, { want: 15, size: 20 }], 0, 100, 4);
  assert.deepEqual(s.at, [10, 34]);
  assert.equal(s.overflow, 0);
  assert.ok(spread([{ want: 0, size: 60 }, { want: 0, size: 60 }], 0, 100, 4).overflow > 0, 'too many for the room');
  const r = { x0: 0, y0: 0, x1: 10, y1: 10 };
  assert.equal(segmentMeetsRect(-5, 5, 15, 5, r), true);
  assert.equal(segmentMeetsRect(-5, -5, -1, 15, r), false);
  assert.equal(segmentMeetsRect(-5, 20, 20, -5, r), true, 'a diagonal through it');
  const g = { plot: { x0: 0, y0: 0, x1: 200, y1: 200 }, box: { x0: 0, y0: 0, x1: 200, y1: 200 } };
  const line = [[{ x: 0, y: 100 }, { x: 200, y: 100 }]];
  const at1 = placeBeside(g, [[50, 90], [50, 20]], 40, 20, [], line);
  assert.deepEqual(at1, { x: 50, y: 20 }, 'the spot the line leaves free');
  const at2 = placeBeside(g, [[50, 90]], 40, 20, [], line, { x: 70, y: 100 });
  assert.ok(at2.y + 20 < 100 || at2.y > 100, 'the free spot of the grid round the anchor');
  assert.ok(Math.abs(at2.y + 10 - 100) <= 40, 'the nearest one');
  assert.deepEqual(placeBeside(g, [[50, 90]], 40, 20, [], line), { x: 50, y: 90 }, 'no anchor, no search: the first in the plot');
});

test('the catalogue\'s chart_build slots (templates.py) are slotRows exactly, once it is registered', () => {
  const spec = /** @type {any} */ (CATALOGUE.templates).chart_build;
  const cat = /** @type {any} */ (templateSlots('chart_build'));
  const want = /** @type {any} */ (slotRows());
  if (!spec.implemented) {
    // before the integrator registers it (templates.py `implemented=True`, `slots=_chart_build_slots()`), the
    // catalogue has no slot table for it; this test turns strict the moment it does
    assert.deepEqual(cat || {}, {}, 'an unregistered template has no slots in the catalogue');
    return;
  }
  for (const slot of Object.keys(want)) {
    for (const fmt of Object.keys(want[slot])) {
      const got = cat[slot][fmt].default;
      assert.deepEqual({ w: got.w, max_lines: got.max_lines, size: got.size, role: got.role, face: got.face,
        weight: got.weight, line_height: got.line_height, prefer_lines: got.prefer_lines, tracking_em: got.tracking_em,
        tabular_nums: got.tabular_nums }, want[slot][fmt].default, `${slot} ${fmt}`);
    }
  }
  assert.deepEqual(Object.keys(cat).sort(), Object.keys(want).sort());
  assert.deepEqual(spec.consumes.forms, {
    count: ['panel', 'panel:bar', 'panel:point'], draw: ['panel', 'panel:bar', 'panel:point'],
    look_here: ['panel', 'panel:bar', 'panel:point'],
  });
  assert.equal(spec.push_ok, false, 'the outline form');
});

test('slotRows mirrors SIZES and LAYOUT (the table charts.py chart_build_slot_rows writes)', () => {
  const rows = /** @type {any} */ (slotRows());
  for (const fmt of /** @type {const} */ (['landscape', 'portrait'])) {
    for (const slot of /** @type {const} */ (['tick', 'value', 'axis', 'series'])) {
      assert.deepEqual(rows[slot][fmt].default.size, [SIZES[fmt][slot].min, SIZES[fmt][slot].max]);
      assert.equal(rows[slot][fmt].default.weight, SIZES[fmt][slot].weight);
    }
    assert.deepEqual(rows.title[fmt].default.size, [LAYOUT[fmt].title.min, LAYOUT[fmt].title.max]);
  }
  assert.equal(rows.title.portrait.default.w, 696);
  assert.equal(rows.tick.portrait.default.w, 588);
  assert.ok(SIZES.portrait.tick.weight >= 600, 'R-29: weight >= 600 at <= 64 px in 9:16');
});

test('the painter never writes outline, text-decoration, filter or will-change (cues.js owns emphasis)', async () => {
  const banned = /^(outline|text-decoration|filter|backdrop-filter|will-change)/;
  const proto = /** @type {any} */ (Painter.prototype);
  const real = proto.style;
  /** @type {string[]} */
  const seen = [];
  proto.style = function style(/** @type {any} */ el, /** @type {string} */ prop, /** @type {any} */ v) {
    seen.push(String(prop));
    return real.call(this, el, prop, v);
  };
  try {
    const tl = load('chart_build.portrait.json');
    const m = mount(tl);
    for (const t of [0, 1, 3, 6, 7.9]) await at(m.stage, tl, t);
    m.done();
  } finally {
    proto.style = real;
  }
  assert.ok(seen.length > 0);
  assert.deepEqual(seen.filter((p) => banned.test(p)), []);
  void env;
});
