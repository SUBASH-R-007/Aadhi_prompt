// @ts-check
/**
 * Usage & budget (GET /api/usage/me?days=): today vs daily budget, spending by day (chart),
 * by provider/model and by operation, and what the user's own API keys paid (`own_key_usd`,
 * outside the daily budget).
 */

import { h, clear } from '../../shared/dom.js';
import { get } from '../../shared/api.js';
import { select, field, spinner, errorState } from '../components/form.js';
import { createChart } from '../components/chart.js';
import { progressBar } from '../components/badges.js';
import { formatUsd } from '../util.js';
import { pageHeader } from './common.js';

/**
 * Fill missing days with zero so the chart has a continuous axis (pure; exported for tests).
 * @param {{ date: string, usd: number }[]} byDay
 * @param {number} days
 * @param {Date} [today]
 * @returns {{ date: string, usd: number }[]}
 */
export function continuousDays(byDay, days, today = new Date()) {
  const map = new Map(byDay.map((d) => [d.date, Number(d.usd) || 0]));
  const out = [];
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    const key = d.toISOString().slice(0, 10);
    out.push({ date: key, usd: map.get(key) || 0 });
  }
  return out;
}

/** @type {import('../types.js').ViewMount} */
export function mount(container, { app, query }) {
  /** @type {{ destroy: () => void } | null} */
  let chart = null;
  let destroyed = false;
  /** Incremented per load: late responses and charts of an older load are discarded. */
  let loadToken = 0;
  const range = select({
    options: [
      { value: '7', label: 'Last 7 days' },
      { value: '30', label: 'Last 30 days' },
      { value: '90', label: 'Last 90 days' },
    ],
    value: ['7', '30', '90'].includes(query.days) ? query.days : '30',
    ariaLabel: 'Period',
  });
  const body = h('div', { class: 'usage' });
  container.append(pageHeader('Usage & budget', 'Estimated AI costs of your lectures.', field('Period', range)), body);
  range.addEventListener('change', () => {
    app.replaceHash(`#/usage?days=${range.value}`);
    void load();
  });

  async function load() {
    const token = ++loadToken;
    if (chart) chart.destroy();
    chart = null;
    clear(body);
    body.appendChild(spinner('Loading usage…'));
    try {
      const days = Number(range.value);
      const data = await get(`/api/usage/me?days=${days}`);
      if (destroyed || token !== loadToken) return;
      render(data, days, token);
    } catch (err) {
      if (destroyed || token !== loadToken) return;
      clear(body);
      body.appendChild(errorState('Could not load usage.', () => void load()));
      app.reportError(err);
    }
  }

  /**
   * @param {any} data
   * @param {number} days
   * @param {number} token  the load this render belongs to
   */
  function render(data, days, token) {
    clear(body);
    const budget = Number(data.daily_budget_usd) || 0;
    const today = Number(data.today_usd) || 0;
    const ratio = budget > 0 ? today / budget : 0;
    const ownKeys = Number(data.own_key_usd) || 0;
    body.append(
      h(
        'div',
        { class: 'stat-grid' },
        h(
          'div',
          { class: ['stat-card glass', ratio >= 0.9 ? 'warn' : ''] },
          h('div', { class: 'stat-value' }, `${formatUsd(today)}`, h('span', { class: 'stat-of' }, budget ? ` of ${formatUsd(budget)}` : '')),
          // today_usd is the server-billed spend the daily budget measures (own-key spend excluded).
          h('div', { class: 'stat-label' }, 'Budget used today'),
          ownKeys > 0 ? h('div', { class: 'muted small' }, 'Excludes spending paid with your own keys.') : null,
          budget ? progressBar(Math.min(1, ratio), 'Daily budget used') : h('div', { class: 'muted small' }, 'No daily limit'),
          ratio >= 1 ? h('p', { class: 'bad-text small' }, 'Daily budget reached: new generations wait until tomorrow (UTC).') : null,
        ),
        h('div', { class: 'stat-card glass' }, h('div', { class: 'stat-value' }, formatUsd(data.total_usd)), h('div', { class: 'stat-label' }, `Total, last ${days} days`)),
        ownKeys > 0
          ? h(
              'div',
              { class: 'stat-card glass own-keys' },
              h('div', { class: 'stat-value' }, formatUsd(ownKeys)),
              h('div', { class: 'stat-label' }, `Paid with your own keys, last ${days} days`),
              h('div', { class: 'muted small' }, 'Not counted toward your daily budget.'),
            )
          : null,
      ),
    );
    const chartHost = h('div', { class: 'chart-host' });
    body.appendChild(h('section', { class: 'panel glass' }, h('h2', {}, 'Spending by day'), chartHost));
    const series = continuousDays(data.by_day || [], days);
    void createChart(chartHost, {
      type: 'bar',
      labels: series.map((d) => d.date.slice(5)),
      datasets: [{ label: 'USD', data: series.map((d) => Math.round(d.usd * 10000) / 10000) }],
      summary: `Estimated spending per day over the last ${days} days.`,
      format: (v) => formatUsd(v),
    }).then((c) => {
      // A newer load (or leaving the page) owns the chart slot now: release this instance.
      if (destroyed || token !== loadToken) c.destroy();
      else chart = c;
    });
    const providers = data.by_provider || [];
    const ops = data.by_operation || [];
    body.appendChild(
      h(
        'div',
        { class: 'two-col' },
        h(
          'section',
          { class: 'panel glass' },
          h('h2', {}, 'By provider'),
          providers.length
            ? h('table', { class: 'table compact' }, h('thead', {}, h('tr', {}, ['Provider', 'Model', 'Calls', 'Cost'].map((t) => h('th', { scope: 'col' }, t)))), h('tbody', {}, providers.map((/** @type {any} */ p) => h('tr', {}, h('td', {}, p.provider), h('td', { class: 'mono small' }, p.model || '–'), h('td', {}, String(p.calls ?? 0)), h('td', {}, formatUsd(p.usd))))))
            : h('p', { class: 'muted' }, 'No usage in this period.'),
        ),
        h(
          'section',
          { class: 'panel glass' },
          h('h2', {}, 'By operation'),
          ops.length
            ? h('table', { class: 'table compact' }, h('thead', {}, h('tr', {}, ['Operation', 'Cost'].map((t) => h('th', { scope: 'col' }, t)))), h('tbody', {}, ops.map((/** @type {any} */ o) => h('tr', {}, h('td', {}, o.operation), h('td', {}, formatUsd(o.usd))))))
            : h('p', { class: 'muted' }, 'No usage in this period.'),
        ),
      ),
    );
  }

  void load();
  return {
    destroy() {
      destroyed = true;
      if (chart) chart.destroy();
    },
  };
}
