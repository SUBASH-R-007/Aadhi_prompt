// @ts-check
/**
 * Chart.js wrapper (self-hosted via shared/libs.js loadLib). Themed from CSS tokens, with an
 * accessible summary on the canvas and an optional data table toggle (screen readers and
 * keyboard users get the numbers, not only pixels). `destroy()` releases the chart.
 */

import { h, clear } from '../../shared/dom.js';
import { loadLib } from '../../shared/libs.js';

/**
 * @typedef {object} ChartInput
 * @property {'bar' | 'line' | 'pie' | 'doughnut' | 'radar'} type
 * @property {string[]} labels
 * @property {{ label: string, data: number[], color?: string, type?: string, yAxisID?: string }[]} datasets
 * @property {string} summary        accessible description
 * @property {Record<string, any>} [options]   extra Chart.js options (merged shallowly)
 * @property {(v: number) => string} [format]   value formatter for the table/tooltips
 */

/** Series colours from tokens (fallbacks match tokens.css). */
export function palette() {
  const css = typeof getComputedStyle === 'function' ? getComputedStyle(document.documentElement) : null;
  const v = (/** @type {string} */ name, /** @type {string} */ fb) => (css && css.getPropertyValue(name).trim()) || fb;
  return [v('--c-gold', '#ffd700'), v('--c-purple', '#b026ff'), v('--c-cyan', '#00e5ff'), v('--c-green', '#00ff88'), v('--c-red', '#ff5555'), '#ff9f43'];
}

/**
 * @param {string} hex
 * @param {number} alpha
 */
function withAlpha(hex, alpha) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/**
 * Render a chart into `container`.
 * @param {HTMLElement} container
 * @param {ChartInput} input
 * @returns {Promise<{ chart: any, destroy: () => void, update: (input: ChartInput) => void }>}
 */
export async function createChart(container, input) {
  const canvas = h('canvas', { role: 'img', 'aria-label': input.summary });
  const tableHost = h('div', { class: 'chart-table', hidden: true });
  const toggle = h('button', { type: 'button', class: 'btn btn-ghost btn-sm chart-toggle', 'aria-expanded': 'false' }, 'Show data table');
  toggle.addEventListener('click', () => {
    const show = tableHost.hidden;
    tableHost.hidden = !show;
    toggle.setAttribute('aria-expanded', show ? 'true' : 'false');
    toggle.textContent = show ? 'Hide data table' : 'Show data table';
  });
  const frame = h('div', { class: 'chart-frame' }, canvas);
  clear(container);
  container.append(frame, toggle, tableHost);
  renderTable(tableHost, input);

  /** @type {any} */
  let chart = null;
  try {
    const Chart = await loadLib('chartjs');
    chart = new Chart(canvas, buildConfig(input));
  } catch (err) {
    frame.replaceChildren(h('p', { class: 'muted' }, 'Chart unavailable; showing the data table.'));
    tableHost.hidden = false;
    toggle.hidden = true;
  }
  return {
    chart,
    update(next) {
      renderTable(tableHost, next);
      canvas.setAttribute('aria-label', next.summary);
      if (!chart) return;
      const cfg = buildConfig(next);
      chart.data = cfg.data;
      chart.options = cfg.options;
      chart.update();
    },
    destroy() {
      if (chart) chart.destroy();
      chart = null;
      clear(container);
    },
  };
}

/**
 * @param {ChartInput} input
 */
function buildConfig(input) {
  const colors = palette();
  const css = typeof getComputedStyle === 'function' ? getComputedStyle(document.documentElement) : null;
  const text = (css && css.getPropertyValue('--c-text-dim').trim()) || 'rgba(255,255,255,0.72)';
  const grid = 'rgba(255,255,255,0.08)';
  const circular = input.type === 'pie' || input.type === 'doughnut';
  const datasets = input.datasets.map((ds, i) => {
    const color = ds.color || colors[i % colors.length];
    return {
      label: ds.label,
      data: ds.data,
      type: ds.type,
      yAxisID: ds.yAxisID,
      borderColor: circular ? input.labels.map((_, j) => colors[j % colors.length]) : color,
      backgroundColor: circular ? input.labels.map((_, j) => withAlpha(colors[j % colors.length], 0.75)) : withAlpha(color, input.type === 'line' ? 0.2 : 0.65),
      borderWidth: 2,
      tension: 0.25,
      fill: input.type === 'line' ? false : undefined,
    };
  });
  const fmt = input.format;
  return {
    type: input.type,
    data: { labels: input.labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      plugins: {
        legend: { labels: { color: text } },
        tooltip: fmt ? { callbacks: { label: (/** @type {any} */ ctx) => `${ctx.dataset.label}: ${fmt(Number(ctx.parsed.y ?? ctx.parsed))}` } } : {},
      },
      scales: circular
        ? {}
        : {
            x: { ticks: { color: text }, grid: { color: grid } },
            y: { ticks: { color: text }, grid: { color: grid }, beginAtZero: true },
          },
      ...(input.options || {}),
    },
  };
}

/**
 * @param {HTMLElement} host
 * @param {ChartInput} input
 */
function renderTable(host, input) {
  const fmt = input.format || ((/** @type {number} */ v) => String(v));
  clear(host);
  host.appendChild(
    h(
      'table',
      { class: 'table compact' },
      h('caption', { class: 'sr-only' }, input.summary),
      h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, 'Label'), input.datasets.map((d) => h('th', { scope: 'col' }, d.label)))),
      h(
        'tbody',
        {},
        input.labels.map((label, i) => h('tr', {}, h('th', { scope: 'row' }, label), input.datasets.map((d) => h('td', {}, Number.isFinite(d.data[i]) ? fmt(d.data[i]) : '–')))),
      ),
    ),
  );
}
