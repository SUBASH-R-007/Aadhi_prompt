// @ts-check
/**
 * Lesson stage chip and the "Next step" card (project list and project page). Words and icons come from
 * lib/lessonStage.js; the stage is always said in words, never by colour alone.
 */

import { h } from '../../shared/dom.js';
import { stageInfo, workflowSteps } from '../lib/lessonStage.js';
import { button, linkButton } from './form.js';
import { icon } from './icons.js';

/**
 * A badge saying where the lesson is ("Plan ready for review").
 * @param {string} stage
 * @param {{ prefix?: string }} [opts]  e.g. "Now: "
 */
export function stageChip(stage, opts = {}) {
  const info = stageInfo(stage);
  return h(
    'span',
    { class: ['badge', `badge-${info.tone}`, 'stage-chip'], dataset: { stage: String(stage) } },
    info.busy ? h('span', { class: 'pulse', 'aria-hidden': 'true' }) : icon(info.icon, { size: 12 }),
    `${opts.prefix || ''}${info.label}`,
  );
}

/**
 * @typedef {object} NextStepCardOptions
 * @property {string} stage
 * @property {import('../types.js').NextStep} next
 * @property {(action: string) => boolean} canDo      the page can carry out this action without a link
 * @property {(next: import('../types.js').NextStep, el: HTMLButtonElement) => void} onAction
 * @property {import('../../shared/dom.js').Child} [note]  e.g. unsaved editor changes
 * @property {import('../../shared/dom.js').Child} [extra] secondary links next to the primary action
 */

/**
 * The "Next step" card: the stage in words, one sentence, one primary action and the workflow strip.
 * A step with a link is a link; an action the page can do is a button; "wait" (Aadhi is working) has none.
 * @param {NextStepCardOptions} opts
 */
export function nextStepCard(opts) {
  const info = stageInfo(opts.stage);
  const next = opts.next;
  /** @type {HTMLElement | null} */
  let primary = null;
  if (next.href) {
    // a Studio route opens here; a page outside the Studio (the full-screen preview) in a new tab
    primary = linkButton(next.label, next.href, { kind: 'gold', icon: info.icon === 'check' ? 'play' : info.icon, newTab: !next.href.startsWith('#') });
  } else if (next.action && next.action !== 'wait' && next.action !== 'none' && opts.canDo(next.action)) {
    const b = button(next.label, { kind: 'gold', icon: next.action === 'retry' ? 'retry' : info.icon === 'check' ? 'download' : info.icon, dataset: { action: next.action } });
    b.addEventListener('click', () => opts.onAction(next, b));
    primary = b;
  }
  const steps = workflowSteps(opts.stage, next.action);
  return h(
    'section',
    { class: ['panel', 'glass', 'next-step', `next-step-${info.tone}`], 'aria-label': 'Next step', dataset: { stage: String(opts.stage), action: next.action || '' } },
    h('div', { class: 'next-step-head row gap wrap' }, h('h2', {}, 'Next step'), stageChip(opts.stage)),
    info.summary ? h('p', { class: 'next-step-summary' }, info.summary) : null,
    opts.note || null,
    primary || opts.extra ? h('div', { class: 'next-step-actions row gap wrap' }, primary, opts.extra || null) : null,
    steps.length ? h(
      'ol',
      { class: 'workflow-strip', 'aria-label': 'Progress' },
      steps.map((s) =>
        h(
          'li',
          { class: ['workflow-step', `is-${s.state}`], 'aria-current': s.state === 'current' ? 'step' : undefined },
          h('span', { class: 'workflow-dot', 'aria-hidden': 'true' }, s.state === 'done' ? icon('check', { size: 12 }) : null),
          h('span', { class: 'workflow-label' }, s.label),
          h('span', { class: 'sr-only' }, s.state === 'done' ? ' (done)' : s.state === 'current' ? ' (now)' : ' (to do)'),
        ),
      ),
    ) : null, // where a failure happened is unknown: no strip rather than every step "to do"
  );
}
