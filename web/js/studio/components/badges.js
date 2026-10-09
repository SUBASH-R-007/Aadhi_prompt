// @ts-check
/**
 * Status pills and issue-count badges (text + colour; never colour alone).
 */

import { h } from '../../shared/dom.js';
import { statusLabel } from '../lib/jobStages.js';
import { icon } from './icons.js';

/** @type {Record<string, string>} */
const STATUS_TONE = {
  ready: 'ok',
  succeeded: 'ok',
  draft: 'muted',
  queued: 'muted',
  generating: 'busy',
  running: 'busy',
  building: 'busy',
  awaiting_review: 'attention',
  failed: 'bad',
  cancelled: 'muted',
};

/**
 * @param {string} status
 * @param {string} [label]
 */
export function statusBadge(status, label) {
  const tone = STATUS_TONE[status] || 'muted';
  return h('span', { class: ['badge', `badge-${tone}`] }, tone === 'busy' ? h('span', { class: 'pulse', 'aria-hidden': 'true' }) : null, label || statusLabel(status));
}

/**
 * Issue counts like "2 errors · 3 warnings". Renders nothing visible when all are zero
 * (returns a "No issues" badge when `showClean`).
 * @param {{ error?: number, warning?: number, info?: number } | null | undefined} counts
 * @param {{ showClean?: boolean, compact?: boolean }} [opts]
 */
export function issueBadges(counts, opts = {}) {
  const c = counts || {};
  const parts = [];
  if (c.error) parts.push(h('span', { class: 'badge badge-bad', title: `${c.error} error(s)` }, icon('error', { size: 12 }), opts.compact ? String(c.error) : `${c.error} error${c.error === 1 ? '' : 's'}`));
  if (c.warning) parts.push(h('span', { class: 'badge badge-attention', title: `${c.warning} warning(s)` }, icon('warning', { size: 12 }), opts.compact ? String(c.warning) : `${c.warning} warning${c.warning === 1 ? '' : 's'}`));
  if (c.info && !opts.compact) parts.push(h('span', { class: 'badge badge-muted', title: `${c.info} note(s)` }, icon('info', { size: 12 }), `${c.info} note${c.info === 1 ? '' : 's'}`));
  if (!parts.length && opts.showClean) parts.push(h('span', { class: 'badge badge-ok' }, icon('check', { size: 12 }), 'No issues'));
  return h('span', { class: 'badges' }, parts);
}

/** "Stale" badge for scenes/timelines that need a rebuild. */
export function staleBadge(text = 'Needs build') {
  return h('span', { class: 'badge badge-attention', title: 'Edited since the last build' }, icon('refresh', { size: 12 }), text);
}

/**
 * Accessible progress bar.
 * @param {number} value 0..1
 * @param {string} label
 */
export function progressBar(value, label) {
  const pct = Math.round(Math.max(0, Math.min(1, value || 0)) * 100);
  return h(
    'div',
    { class: 'progress', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': String(pct), 'aria-label': label },
    h('div', { class: 'progress-fill', style: { width: `${pct}%` } }),
  );
}
