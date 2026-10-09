// @ts-check
/**
 * Small view helpers shared by several Studio pages.
 */

import { h } from '../../shared/dom.js';
import { api } from '../../shared/api.js';
import { href } from '../router.js';
import { openModal } from '../components/modal.js';
import { jobProgress } from '../components/jobProgress.js';

/** Version statuses that have an editable screenplay. */
export const EDITABLE_STATUSES = new Set(['draft', 'ready', 'failed', 'building']);

/** Label of the primary link of a version waiting for its source review (`review_stage: "source"`). */
export const SOURCE_REVIEW_LABEL = 'Check the source';

/**
 * Primary link for a version: while awaiting review the source report (`review_stage: "source"`, the
 * teacher's source check) or the plan review, the editor when a screenplay exists, otherwise none.
 * @param {number} projectId
 * @param {{ id: number, status: string, review_stage?: string } | null | undefined} version
 * @returns {{ label: string, hash: string } | null}
 */
export function versionPrimaryLink(projectId, version) {
  if (!version) return null;
  if (version.status === 'awaiting_review' && version.review_stage === 'source') return { label: SOURCE_REVIEW_LABEL, hash: href('sourceReport', { id: projectId, vid: version.id }) };
  if (version.status === 'awaiting_review') return { label: 'Review plan', hash: href('planReview', { id: projectId, vid: version.id }) };
  if (EDITABLE_STATUSES.has(version.status)) return { label: 'Open editor', hash: href('editor', { id: projectId, vid: version.id }) };
  return null;
}

/**
 * Page header with title, optional subtitle and actions.
 * @param {string} title
 * @param {import('../../shared/dom.js').Child} [subtitle]
 * @param {...import('../../shared/dom.js').Child} actions
 */
export function pageHeader(title, subtitle, ...actions) {
  return h(
    'div',
    { class: 'page-header' },
    h('div', { class: 'page-heading' }, h('h1', {}, title), subtitle ? h('p', { class: 'page-subtitle' }, subtitle) : null),
    actions.length ? h('div', { class: 'page-actions row gap wrap' }, ...actions) : null,
  );
}

/**
 * Breadcrumb navigation.
 * @param {{ label: string, hash?: string }[]} items
 */
export function breadcrumbs(items) {
  return h(
    'nav',
    { class: 'breadcrumbs', 'aria-label': 'Breadcrumb' },
    h(
      'ol',
      {},
      items.map((it, i) =>
        h('li', {}, it.hash && i < items.length - 1 ? h('a', { href: it.hash }, it.label) : h('span', { 'aria-current': i === items.length - 1 ? 'page' : undefined }, it.label)),
      ),
    ),
  );
}

/**
 * Show a job in a modal until it finishes (or the user closes the dialog; the job keeps
 * running server-side).
 * @param {string} title
 * @param {import('../components/jobProgress.js').JobSummary} job
 * @param {{ onSuccess?: (job: any) => void, onFinished?: (job: any) => void, reviewHref?: string, description?: string }} [opts]
 * @returns {Promise<any>} final job (or the job at the time the dialog was closed)
 */
export function jobModal(title, job, opts = {}) {
  /** @type {any} */
  let latest = job;
  const progress = jobProgress(job, {
    reviewHref: opts.reviewHref,
    onUpdate: (j) => {
      latest = j;
    },
    onSuccess: (j) => {
      if (opts.onSuccess) opts.onSuccess(j);
    },
    onFinished: (j) => {
      latest = j;
      if (opts.onFinished) opts.onFinished(j);
    },
  });
  const modal = openModal({
    title,
    description: opts.description || 'You can close this dialog; the job keeps running and its progress stays visible on the project page.',
    body: progress.el,
    size: 'lg',
    actions: [{ label: 'Close', kind: 'outline', value: null }],
    onClose: () => progress.destroy(),
  });
  return modal.result.then(() => latest);
}

/**
 * Multipart upload helper (FormData) through the shared API client.
 * @param {string} path
 * @param {Record<string, string | Blob>} fields
 * @param {{ signal?: AbortSignal }} [opts]
 */
export function uploadForm(path, fields, opts = {}) {
  const form = new FormData();
  for (const [k, v] of Object.entries(fields)) form.append(k, v);
  return api(path, { method: 'POST', form, signal: opts.signal });
}
