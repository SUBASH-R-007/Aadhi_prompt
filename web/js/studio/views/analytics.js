// @ts-check
/**
 * Learner analytics for a project (GET /api/projects/{id}/analytics?version_id=): summary
 * cards, per-scene funnel (enters/completes + drop-off), quiz accuracy with option
 * distributions, and flagged scenes.
 */

import { h, clear } from '../../shared/dom.js';
import { get } from '../../shared/api.js';
import { select, field, spinner, errorState, emptyState } from '../components/form.js';
import { createChart } from '../components/chart.js';
import { href } from '../router.js';
import { formatDuration } from '../util.js';
import { pageHeader, breadcrumbs } from './common.js';

/**
 * Funnel rows -> chart input (pure; exported for tests).
 * @param {any[]} scenes
 */
export function funnelChartInput(scenes) {
  const labels = scenes.map((s) => `${s.index + 1}. ${String(s.title || s.scene_id).slice(0, 24)}`);
  return {
    type: /** @type {const} */ ('bar'),
    labels,
    datasets: [
      { label: 'Started', data: scenes.map((s) => Number(s.enters) || 0) },
      { label: 'Completed', data: scenes.map((s) => Number(s.completes) || 0) },
    ],
    summary: `Viewers who started and completed each of ${scenes.length} scenes.`,
  };
}

/**
 * Percent text ("87%"); accepts 0..1 ratios.
 * @param {number | null | undefined} v
 */
export function pct(v) {
  if (v === null || v === undefined || !Number.isFinite(v)) return '–';
  return `${Math.round(v * 100)}%`;
}

/** @type {import('../types.js').ViewMount} */
export async function mount(container, { app, params, query }) {
  const projectId = params.id;
  /** @type {{ destroy: () => void } | null} */
  let chart = null;
  let destroyed = false;
  /** Incremented per load: late responses and charts of an older load are discarded. */
  let loadToken = 0;
  container.append(breadcrumbs([{ label: 'Projects', hash: '#/projects' }, { label: 'Project', hash: href('project', { id: projectId }) }, { label: 'Analytics' }]));
  const header = h('div', {});
  const body = h('div', { class: 'analytics' }, spinner('Loading analytics…'));
  container.append(header, body);

  /** @type {any} */
  let project;
  try {
    project = await get(`/api/projects/${projectId}`);
  } catch (err) {
    clear(body);
    body.appendChild(errorState('Could not load the project.'));
    app.reportError(err);
    return;
  }
  const versions = project.versions || [];
  const picker = select({
    options: [{ value: '', label: 'All versions' }, ...versions.map((/** @type {any} */ v) => ({ value: String(v.id), label: `v${v.number} (${v.language})` }))],
    value: query.version_id || '',
    ariaLabel: 'Version',
  });
  picker.addEventListener('change', () => {
    app.replaceHash(href('analytics', { id: projectId }, { version_id: picker.value }));
    void load();
  });
  header.append(pageHeader(`Analytics · ${project.project.title}`, 'How students watched the shared lecture.', field('Version', picker)));

  async function load() {
    const token = ++loadToken;
    if (chart) chart.destroy();
    chart = null;
    clear(body);
    body.appendChild(spinner('Loading analytics…'));
    try {
      const qs = picker.value ? `?version_id=${encodeURIComponent(picker.value)}` : '';
      const data = await get(`/api/projects/${projectId}/analytics${qs}`);
      if (destroyed || token !== loadToken) return;
      render(data, token);
    } catch (err) {
      if (destroyed || token !== loadToken) return;
      clear(body);
      body.appendChild(errorState('Could not load analytics.', () => void load()));
      app.reportError(err);
    }
  }

  /**
   * @param {any} data
   * @param {number} token  the load this render belongs to
   */
  function render(data, token) {
    clear(body);
    const s = data.summary || {};
    if (!Number(s.viewers) && !Number(s.sessions)) {
      body.appendChild(emptyState('No views yet', 'Create a share link on the project page and send it to your students.', h('a', { class: 'btn btn-gold', href: href('project', { id: projectId }) }, 'Share the lecture')));
      return;
    }
    body.appendChild(
      h(
        'div',
        { class: 'stat-grid' },
        stat('Viewers', String(s.viewers ?? 0)),
        stat('Sessions', String(s.sessions ?? 0)),
        stat('Completion rate', pct(s.completion_rate)),
        stat('Average watch time', formatDuration(s.avg_watch_seconds)),
      ),
    );
    const scenes = (data.scenes || []).slice().sort((/** @type {any} */ a, /** @type {any} */ b) => a.index - b.index);
    if (scenes.length) {
      const chartHost = h('div', { class: 'chart-host' });
      body.appendChild(h('section', { class: 'panel glass' }, h('h2', {}, 'Scene funnel'), h('p', { class: 'muted' }, 'Big gaps between “started” and “completed” show where students drop off.'), chartHost, dropoffTable(scenes)));
      void createChart(chartHost, funnelChartInput(scenes)).then((c) => {
        // A newer load (or leaving the page) owns the chart slot now: release this instance.
        if (destroyed || token !== loadToken) c.destroy();
        else chart = c;
      });
    }
    const quizzes = data.quizzes || [];
    if (quizzes.length) body.appendChild(quizSection(quizzes));
    const flags = data.flags || [];
    if (flags.length) {
      const titles = new Map(scenes.map((/** @type {any} */ x) => [x.scene_id, `${x.index + 1}. ${x.title || x.scene_id}`]));
      body.appendChild(
        h(
          'section',
          { class: 'panel glass' },
          h('h2', {}, 'Needs attention'),
          h('ul', { class: 'flag-list' }, flags.map((/** @type {any} */ f) => h('li', {}, h('strong', {}, titles.get(f.scene_id) || f.scene_id), ` — ${f.reason}`))),
        ),
      );
    }
  }

  /**
   * @param {string} label
   * @param {string} value
   */
  function stat(label, value) {
    return h('div', { class: 'stat-card glass' }, h('div', { class: 'stat-value' }, value), h('div', { class: 'stat-label' }, label));
  }

  /** @param {any[]} scenes */
  function dropoffTable(scenes) {
    return h(
      'details',
      { class: 'collapsible' },
      h('summary', {}, 'Scene table'),
      h(
        'div',
        { class: 'table-wrap' },
        h(
          'table',
          { class: 'table compact' },
          h('thead', {}, h('tr', {}, ['Scene', 'Started', 'Completed', 'Drop-off'].map((t) => h('th', { scope: 'col' }, t)))),
          h('tbody', {}, scenes.map((sc) => h('tr', { class: sc.dropoff_rate > 0.3 ? 'warn-row' : '' }, h('th', { scope: 'row' }, `${sc.index + 1}. ${sc.title || sc.scene_id}`, sc.hidden === true ? h('span', { class: 'muted small' }, ' (skipped in the video)') : null), h('td', {}, String(sc.enters)), h('td', {}, String(sc.completes)), h('td', {}, pct(sc.dropoff_rate))))),
        ),
      ),
    );
  }

  /** @param {any[]} quizzes */
  function quizSection(quizzes) {
    const rows = quizzes.map((q) => {
      const counts = Array.isArray(q.option_counts) ? q.option_counts : [];
      const total = counts.reduce((/** @type {number} */ a, /** @type {number} */ b) => a + b, 0) || 1;
      const dist = h(
        'ol',
        { class: 'option-dist', 'aria-label': 'Answer distribution' },
        counts.map((/** @type {number} */ c, /** @type {number} */ i) =>
          h(
            'li',
            { class: i === q.correct_index ? 'correct' : '' },
            h('span', { class: 'opt-letter' }, String.fromCharCode(65 + i)),
            h('span', { class: 'opt-bar' }, h('span', { class: 'opt-fill', style: { width: `${Math.round((c / total) * 100)}%` } })),
            h('span', { class: 'opt-count' }, `${c} (${Math.round((c / total) * 100)}%)${i === q.correct_index ? ' ✓' : ''}`),
          ),
        ),
      );
      return h('tr', {}, h('th', { scope: 'row' }, q.question), h('td', {}, String(q.answers ?? 0)), h('td', { class: Number.isFinite(q.accuracy) && q.accuracy < 0.5 ? 'bad-text' : '' }, pct(q.accuracy)), h('td', {}, dist));
    });
    return h(
      'section',
      { class: 'panel glass' },
      h('h2', {}, 'Quiz accuracy'),
      h('p', { class: 'muted' }, 'One answer per viewer per quiz. Popular wrong options point at misconceptions worth revisiting.'),
      h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', {}, h('tr', {}, ['Question', 'Answers', 'Accuracy', 'Options'].map((t) => h('th', { scope: 'col' }, t)))), h('tbody', {}, rows))),
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
