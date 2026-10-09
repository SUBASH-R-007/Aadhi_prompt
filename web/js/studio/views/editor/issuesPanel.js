// @ts-check
/**
 * Issues panel: a one-line headline ("Looks good" / "3 things to review" / "1 needs fixing"), issues
 * grouped by area (areas with only notes start folded), severity as an icon AND words, click (or
 * Enter) to jump to the scene/beat, and a filter to the selected scene. Counts are announced politely.
 * Codes and sources stay behind "Show technical details".
 *
 * Actions, where a fix is safe: "Apply fix" for the deterministic repairs the lecture check offers
 * (an ordinary undoable edit, saved by the normal save), "Regenerate scene" for fixable errors (a paid
 * AI rewrite, only on click, through the editor's usual regenerate dialog), and "Build" while the
 * built lecture is older than the screenplay. Everything else is the author's to edit.
 *
 * Media problems of a scene (issues from the asset build, `source: "assets"`) offer "Rebuild this scene"
 * (warnings and errors: a build of that scene only, which retries its failed media) and "Review visual"
 * (the Visual review at that scene). An AI video that may already have been paid for
 * (`video.ambiguous_submission`, `video.operation_lost`) offers "Generate again…" instead of a rebuild:
 * the owner asks before anything is paid twice.
 *
 * A scene skipped in the video (`hidden`) is never built or rewritten: its issues (notes) offer no Regenerate,
 * Rebuild or Generate again, only "Review visual".
 */

import { h, clear } from '../../../shared/dom.js';
import { icon } from '../../components/icons.js';
import { button, checkbox } from '../../components/form.js';
import { countIssues } from '../../lib/validationIssues.js';
import { loadPref, savePref } from '../../lib/draftStore.js';
import { SEVERITY_WORDS, groupByArea, statusOf } from './quality.js';

/** @typedef {import('./quality.js').QualityIssue} QualityIssue */
/**
 * @typedef {object} IssuesState
 * @property {boolean} [loading]
 * @property {string | null} [note]
 * @property {string | null} [selectedSceneId]
 * @property {boolean} [timelineStale]   the built lecture is older than the screenplay ("Build" is offered)
 * @property {boolean} [busy]            saving or a job is starting: actions are disabled
 */

const SOURCE_LABELS = /** @type {Record<string, string>} */ ({ lint: 'check', critic: 'reviewer', manim: 'animation', assets: 'assets', system: 'system', local: 'editor', server: 'schema' });
const REGENERATE_SOURCES = new Set(['lint', 'critic', 'system']);
/** AI videos that may already have been paid for: generated again only on an explicit, confirmed request. */
export const PAID_ATTENTION_CODES = new Set(['video.ambiguous_submission', 'video.operation_lost']);

/**
 * The media actions an issue offers (none for issues that are not about a scene's media).
 * @param {QualityIssue} issue
 * @returns {{ rebuild: boolean, generateAgain: boolean, review: boolean }}
 */
export function mediaActionsOf(issue) {
  const media = !!issue && issue.source === 'assets' && !!issue.scene_id;
  const paid = media && PAID_ATTENTION_CODES.has(issue.code);
  return {
    rebuild: media && !paid && (issue.severity === 'warning' || issue.severity === 'error'),
    generateAgain: paid,
    review: media && !String(issue.code || '').startsWith('assets.tts'),
  };
}

/**
 * @param {{ onSelect: (issue: QualityIssue) => void, onApplyRepair?: (issue: QualityIssue) => void,
 *   onRegenerate?: (issue: QualityIssue) => void, onBuild?: () => void,
 *   onRebuildScene?: (issue: QualityIssue) => void, onGenerateAgain?: (issue: QualityIssue) => void,
 *   reviewHref?: (sceneId: string) => string }} opts
 * @returns {{ el: HTMLElement, update: (issues: QualityIssue[], sp: any, state: IssuesState) => void }}
 */
export function createIssuesPanel(opts) {
  const summary = h('div', { class: 'issues-summary', role: 'status', 'aria-live': 'polite' });
  const onlyScene = checkbox({ label: 'Only the selected scene', checked: false, onChange: () => draw() });
  const technical = checkbox({
    label: 'Show technical details',
    checked: !!loadPref('issuesTechnical', false),
    onChange: (v) => {
      savePref('issuesTechnical', v);
      draw();
    },
  });
  const buildBtn = button('Build', { kind: 'outline', small: true, icon: 'build', title: 'Generate voice and visuals for the changed scenes (no AI rewriting)' });
  buildBtn.hidden = true;
  buildBtn.addEventListener('click', () => opts.onBuild && opts.onBuild());
  const groups = h('div', { class: 'issues-groups', 'aria-label': 'Issues', role: 'region' });
  const note = h('p', { class: 'muted small', hidden: true });
  const el = h(
    'section',
    { class: 'issues-panel', 'aria-label': 'Issues' },
    h('div', { class: 'row space-between wrap' }, h('h2', { class: 'pane-title' }, 'Issues'), summary),
    h('div', { class: 'row gap wrap issues-tools' }, onlyScene, technical, buildBtn),
    note,
    groups,
  );
  /** @type {QualityIssue[]} */
  let issues = [];
  /** @type {any} */
  let sp = null;
  /** @type {IssuesState} */
  let state = {};
  /** Areas the user opened or closed (kept across redraws). @type {Map<string, boolean>} */
  const openAreas = new Map();

  /**
   * @param {QualityIssue} issue
   * @param {Map<string, string>} titles
   */
  function row(issue, titles) {
    const sev = issue.severity || 'warning';
    const where = issue.scene_id ? titles.get(issue.scene_id) || issue.scene_id : 'Whole lecture';
    const meta = [where, issue.beat_id || null];
    if (technical.input.checked) meta.push(SOURCE_LABELS[issue.source || 'lint'] || issue.source || '', issue.code);
    const jump = h(
      'button',
      { type: 'button', class: ['issue', `issue-${sev}`], disabled: !issue.scene_id, dataset: { code: issue.code } },
      icon(sev === 'error' ? 'error' : sev === 'warning' ? 'warning' : 'info'),
      h(
        'span',
        { class: 'issue-body' },
        h('span', { class: 'issue-sev small' }, SEVERITY_WORDS[sev] || sev),
        h('span', { class: 'issue-message' }, issue.message),
        h('span', { class: 'issue-meta muted' }, meta.filter(Boolean).join(' · ')),
      ),
    );
    jump.addEventListener('click', () => opts.onSelect(issue));
    /** @type {HTMLElement[]} */
    const actions = [];
    // skipped in the video: the build leaves it out, so nothing that builds or rewrites it is offered
    const skipped = !!issue.scene_id && !!sp && Array.isArray(sp.scenes) && sp.scenes.some((/** @type {any} */ s) => s && s.id === issue.scene_id && s.hidden === true);
    if (issue.repair && opts.onApplyRepair) {
      const fix = button(issue.repair.label || 'Apply fix', { kind: 'outline', small: true, icon: 'wand', disabled: !!state.busy, title: 'Changes only the listed words; undo with Ctrl+Z', dataset: { action: 'repair' } });
      fix.addEventListener('click', () => opts.onApplyRepair && opts.onApplyRepair(issue));
      actions.push(fix);
    }
    if (!skipped && sev === 'error' && issue.fixable && issue.scene_id && REGENERATE_SOURCES.has(issue.source || 'lint') && opts.onRegenerate) {
      const regen = button('Regenerate scene', { kind: 'ghost', small: true, icon: 'retry', disabled: !!state.busy, title: 'Aadhi rewrites this scene with the AI engine (counts toward your budget)', dataset: { action: 'regenerate' } });
      regen.addEventListener('click', () => opts.onRegenerate && opts.onRegenerate(issue));
      actions.push(regen);
    }
    const media = mediaActionsOf(issue);
    if (!skipped && media.rebuild && opts.onRebuildScene) {
      const rebuild = button('Rebuild this scene', { kind: 'outline', small: true, icon: 'build', disabled: !!state.busy, title: 'Makes this scene’s voice and visuals again (no AI rewriting; generated media counts toward your budget)', dataset: { action: 'rebuild' } });
      rebuild.addEventListener('click', () => opts.onRebuildScene && opts.onRebuildScene(issue));
      actions.push(rebuild);
    }
    if (!skipped && media.generateAgain && opts.onGenerateAgain) {
      const again = button('Generate again…', { kind: 'outline', small: true, icon: 'retry', disabled: !!state.busy, title: 'The earlier try may already have been paid for: you are asked first', dataset: { action: 'generate-again' } });
      again.addEventListener('click', () => opts.onGenerateAgain && opts.onGenerateAgain(issue));
      actions.push(again);
    }
    if (media.review && opts.reviewHref && issue.scene_id) {
      actions.push(h('a', { class: 'btn btn-ghost btn-sm', href: opts.reviewHref(issue.scene_id), dataset: { action: 'review-visual' } }, icon('eye'), h('span', { class: 'btn-label' }, 'Review visual')));
    }
    return h('li', { class: 'issue-row' }, jump, actions.length ? h('div', { class: 'row gap wrap issue-actions' }, ...actions) : null);
  }

  function draw() {
    clear(groups);
    const titles = new Map(((sp && sp.scenes) || []).map((/** @type {any} */ s, /** @type {number} */ i) => [s.id, `${i + 1}. ${s.title || s.type}`]));
    const shown = onlyScene.input.checked && state.selectedSceneId ? issues.filter((i) => i.scene_id === state.selectedSceneId) : issues;
    const status = statusOf(countIssues(issues));
    summary.textContent = state.loading ? 'Checking…' : status.text;
    summary.dataset.status = state.loading ? 'checking' : status.level;
    note.textContent = state.note || '';
    note.hidden = !state.note;
    buildBtn.hidden = !(state.timelineStale && opts.onBuild);
    buildBtn.disabled = !!state.busy;
    if (!shown.length) {
      groups.appendChild(h('ul', { class: 'issues-list', 'aria-label': 'Issues' }, h('li', { class: 'issue-empty muted' }, state.loading ? 'Checking the lecture…' : 'Nothing to fix here.')));
      return;
    }
    const grouped = groupByArea(shown, sp);
    for (const g of grouped) {
      const words = [g.counts.error ? `${g.counts.error} to fix` : '', g.counts.warning ? `${g.counts.warning} to check` : '', g.counts.info ? `${g.counts.info} note${g.counts.info === 1 ? '' : 's'}` : ''].filter(Boolean).join(', ');
      const remembered = openAreas.get(g.id);
      const open = remembered !== undefined ? remembered : grouped.length === 1 || g.counts.error + g.counts.warning > 0;
      const title = h('summary', { class: 'issue-group-title' }, `${g.label} `, h('span', { class: 'muted small' }, `(${words})`));
      const box = /** @type {HTMLDetailsElement} */ (
        h('details', { class: 'issue-group', open: open ? true : undefined, dataset: { area: g.id } }, title, h('ul', { class: 'issues-list', 'aria-label': g.label }, ...g.issues.map((i) => row(i, titles))))
      );
      // The user's choice (click, Enter or Space on the summary) is kept across redraws; defaults are not.
      title.addEventListener('click', () => openAreas.set(g.id, !box.open));
      groups.appendChild(box);
    }
  }

  return {
    el,
    update(nextIssues, nextSp, nextState) {
      issues = nextIssues;
      sp = nextSp;
      state = nextState;
      draw();
    },
  };
}
