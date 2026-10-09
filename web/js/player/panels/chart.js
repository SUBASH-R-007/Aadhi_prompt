// @ts-check
/**
 * Chart panel (Chart.js, self-hosted). Live/preview: created when the panel first becomes visible
 * so the entry animation is seen (no animation under prefers-reduced-motion). Render: created in
 * ready() with animation:false, a fixed canvas size and devicePixelRatio 1.
 */

import { Disposer, h } from '../../shared/dom.js';
import { loadLib } from '../../shared/libs.js';
import { PALETTE, THEME, prefersReducedMotion, showNotice, stripRich, withAlpha } from './util.js';

/** @typedef {import('./types.js').PanelFactory} PanelFactory */

const CHART_TYPES = new Set(['bar', 'line', 'pie', 'doughnut', 'radar']);

/**
 * Build the Chart.js configuration for a ChartSpec (pure; unit-tested).
 * @param {any} spec  ChartSpec {chart_type, labels, datasets[{label, data}], x_label, y_label}
 * @param {{ animate: boolean, render: boolean }} opts
 * @returns {any}
 */
export function buildChartConfig(spec, opts) {
  const type = CHART_TYPES.has(spec && spec.chart_type) ? spec.chart_type : 'bar';
  const labels = (Array.isArray(spec && spec.labels) ? spec.labels : []).map(stripRich);
  const raw = Array.isArray(spec && spec.datasets) ? spec.datasets : [];
  const circular = type === 'pie' || type === 'doughnut';
  const datasets = raw.map((/** @type {any} */ ds, /** @type {number} */ i) => {
    const color = PALETTE[i % PALETTE.length];
    const data = (Array.isArray(ds && ds.data) ? ds.data : []).map((/** @type {any} */ v) => (Number.isFinite(Number(v)) ? Number(v) : null));
    const base = { label: stripRich(ds && ds.label) || `Series ${i + 1}`, data };
    if (circular) {
      return {
        ...base,
        backgroundColor: labels.map((_, k) => withAlpha(PALETTE[k % PALETTE.length], 0.82)),
        borderColor: THEME.surfaceSolid,
        borderWidth: 2,
        hoverOffset: opts.render ? 0 : 8,
      };
    }
    if (type === 'line') {
      return {
        ...base,
        borderColor: color,
        backgroundColor: withAlpha(color, 0.16),
        fill: raw.length === 1,
        tension: 0.3,
        borderWidth: 3,
        pointRadius: 4,
        pointBackgroundColor: color,
      };
    }
    if (type === 'radar') {
      return { ...base, borderColor: color, backgroundColor: withAlpha(color, 0.2), borderWidth: 2, pointBackgroundColor: color };
    }
    return { ...base, backgroundColor: withAlpha(color, 0.78), borderColor: color, borderWidth: 2, borderRadius: 6 };
  });

  const font = { family: THEME.fontSans, size: 15 };
  /** @param {unknown} title */
  const axis = (title, beginAtZero = false) => ({
    beginAtZero,
    title: { display: !!stripRich(title), text: stripRich(title), color: THEME.textDim, font: { ...font, weight: '600' } },
    ticks: { color: THEME.textDim, font },
    grid: { color: THEME.grid },
    border: { color: THEME.axis },
  });
  /** @type {any} */
  const options = {
    responsive: !opts.render,
    maintainAspectRatio: false,
    animation: opts.animate ? { duration: 900, easing: 'easeOutQuart' } : false,
    layout: { padding: 4 },
    plugins: {
      legend: {
        display: circular || raw.length > 1,
        position: 'bottom',
        labels: { color: THEME.text, font, boxWidth: 14, boxHeight: 14, padding: 12 },
      },
      tooltip: { enabled: !opts.render },
      title: { display: false },
    },
  };
  if (opts.render) {
    options.devicePixelRatio = 1;
    options.events = [];
  }
  if (type === 'bar' || type === 'line') {
    options.scales = { x: axis(spec && spec.x_label), y: axis(spec && spec.y_label, type === 'bar') };
  } else if (type === 'radar') {
    options.scales = {
      r: {
        angleLines: { color: THEME.grid },
        grid: { color: THEME.grid },
        pointLabels: { color: THEME.text, font },
        ticks: { color: THEME.textDim, backdropColor: 'rgba(0, 0, 0, 0)', font: { ...font, size: 12 } },
      },
    };
  }
  return { type, data: { labels, datasets }, options };
}

/** @type {PanelFactory} */
export function createChartPanel(body, rsp, ctx) {
  const disposer = new Disposer();
  const spec = rsp.panel && rsp.panel.chart;
  const render = ctx.mode === 'render';
  const wrap = h('div', { class: 'ap-chart-wrap' });
  const canvas = h('canvas', { class: 'ap-chart-canvas', role: 'img', 'aria-label': stripRich(rsp.panel && rsp.panel.title) || 'Chart' });
  wrap.appendChild(canvas);
  body.appendChild(wrap);

  /** @type {any} */
  let chart = null;
  let destroyed = false;
  let failed = false;
  /** @type {Promise<any> | null} */
  let libPromise = null;
  const lib = () => {
    if (!libPromise) libPromise = loadLib('chartjs');
    return libPromise;
  };

  /** @param {any} Chart */
  const create = (Chart) => {
    if (chart || destroyed || failed) return;
    if (!spec) {
      failed = true;
      showNotice(body, 'Chart data is missing.', 'warn');
      return;
    }
    if (render) {
      canvas.width = Math.max(1, wrap.clientWidth || 400);
      canvas.height = Math.max(1, wrap.clientHeight || 300);
    }
    try {
      chart = new Chart(canvas, buildChartConfig(spec, { render, animate: !render && !prefersReducedMotion() }));
    } catch (e) {
      failed = true;
      console.error('chart panel:', e);
      showNotice(body, 'This chart could not be drawn.', 'warn');
    }
  };
  /** @param {unknown} e */
  const libFailed = (e) => {
    if (failed) return;
    failed = true;
    console.error('chart panel: Chart.js failed to load', e);
    showNotice(body, 'Charts are unavailable right now.', 'warn');
  };

  disposer.add(() => {
    destroyed = true;
    if (chart) {
      chart.destroy();
      chart = null;
    }
  });

  let requested = false;
  return {
    update(_t, _state, visible) {
      if (render || !visible || requested) return;
      requested = true;
      lib().then(create, libFailed);
    },
    async ready() {
      let Chart;
      try {
        Chart = await lib();
      } catch (e) {
        libFailed(e);
        if (render) throw e;
        return;
      }
      if (render) create(Chart);
    },
    destroy: () => disposer.dispose(),
  };
}
