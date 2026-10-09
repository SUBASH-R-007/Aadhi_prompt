// @ts-check
/**
 * The inspector's "Visual" line: where the scene's visual came from at the last build (an AI picture or
 * video with its provider and model, the teacher's library, an upload, a figure, an animation, ...), its
 * status and review state in words, and a link to the Visual review at this scene. Read from
 * GET /api/versions/{vid}/visual-review; nothing is shown before it has loaded, when the server has no
 * visual review, or for a scene without a visual.
 */

import { h } from '../../../shared/dom.js';
import { icon } from '../../components/icons.js';
import { sourceLabel, provenanceText, reviewState, approvedBeforeChange, needsAttention, isListed, STATUS_LABELS, REVIEW_LABELS } from '../visualReview.js';

/**
 * @param {import('../../types.js').SceneVisual | null | undefined} sv
 * @param {string} [reviewHref]
 * @returns {HTMLElement | null}
 */
export function visualInfo(sv, reviewHref) {
  if (!sv || !isListed(sv)) return null;
  const state = reviewState(sv);
  const review = approvedBeforeChange(sv) ? 'Approved before a change' : REVIEW_LABELS[state];
  const provenance = provenanceText(sv);
  return h(
    'div',
    { class: 'visual-info small', dataset: { source: String(sv.source || 'none'), status: String(sv.status || '') } },
    h('span', { class: 'visual-info-label' }, 'Visual at the last build:'),
    h('span', { class: 'badge badge-outline' }, sourceLabel(sv)),
    provenance ? h('span', { class: 'muted visual-info-provenance' }, provenance) : null,
    h('span', { class: ['badge', needsAttention(sv) ? 'badge-attention' : 'badge-muted'] }, needsAttention(sv) ? icon('warning', { size: 12 }) : null, STATUS_LABELS[sv.status] || 'Ready'),
    h('span', { class: 'muted' }, review),
    reviewHref ? h('a', { href: reviewHref, class: 'visual-info-link' }, 'Review visual') : null,
  );
}
