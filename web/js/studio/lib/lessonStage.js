// @ts-check
/**
 * The lesson's stage and its one next step, in plain words (project list and project page). Pure.
 *
 * The server derives `stage` and `next_step` from stored state (version status and review stage,
 * timeline_stale, renders, the active job) and sends them with ProjectSummary. This module gives each
 * stage its words, tone and icon and turns a next step into something the page can do. For a server
 * that sends no stage, `deriveStage` / `nextStepFor` derive the same answer from the summary fields the
 * page already has (they cannot tell a ready video from an out-of-date one without renders, so they stop
 * at "ready to render").
 */

import { href } from '../router.js';

/** @typedef {import('../types.js').LessonStage} LessonStage */
/** @typedef {import('../types.js').NextStep} NextStep */

/** Every stage, in workflow order. @type {LessonStage[]} */
export const STAGES = [
  'reading',
  'source_review',
  'planning',
  'plan_review',
  'writing',
  'ready_to_build',
  'building',
  'ready_to_render',
  'rendering',
  'video_ready',
  'video_outdated',
  'failed',
];

/**
 * @typedef {object} StageInfo
 * @property {string} label    short words for a chip ("Plan ready for review")
 * @property {string} summary  one sentence for the next-step card
 * @property {'ok' | 'busy' | 'attention' | 'bad' | 'muted'} tone  badge tone (always shown with words)
 * @property {string} icon     components/icons.js name
 * @property {number} step     index in WORKFLOW_STEPS (the step the lesson is on)
 * @property {boolean} [busy]  Aadhi is working: nothing to do but wait
 */

/** @type {Record<LessonStage, StageInfo>} */
export const STAGE_INFO = {
  reading: { label: 'Reading your document', summary: 'Aadhi is reading your document. This usually takes a minute or two.', tone: 'busy', icon: 'file', step: 0, busy: true },
  source_review: { label: 'Waiting for you to check the source', summary: 'Aadhi has read your document. Check what it found before the lecture is planned.', tone: 'attention', icon: 'eye', step: 0 },
  planning: { label: 'Planning the lecture', summary: 'Aadhi is planning the lecture from your document.', tone: 'busy', icon: 'wand', step: 1, busy: true },
  plan_review: { label: 'Plan ready for review', summary: 'The lecture plan is ready. Check it before Aadhi writes the scenes.', tone: 'attention', icon: 'eye', step: 1 },
  writing: { label: 'Writing the lecture', summary: 'Aadhi is writing the scenes. You can leave this page; the work carries on.', tone: 'busy', icon: 'wand', step: 2, busy: true },
  ready_to_build: { label: 'Ready to build', summary: 'The script has changes that are not in the voice-over and visuals yet. Build the lecture so the preview and the video match it.', tone: 'attention', icon: 'build', step: 3 },
  building: { label: 'Building voice and visuals', summary: 'Aadhi is making the voice-over and the visuals.', tone: 'busy', icon: 'build', step: 3, busy: true },
  ready_to_render: { label: 'Ready to make the video', summary: 'The lecture is built. Preview it, then make the MP4 video.', tone: 'ok', icon: 'film', step: 4 },
  rendering: { label: 'Making the video', summary: 'The MP4 video is being made. Long lectures take several minutes.', tone: 'busy', icon: 'film', step: 4, busy: true },
  video_ready: { label: 'Video ready', summary: 'Your video matches the current script. Download it or share the lecture with your students.', tone: 'ok', icon: 'check', step: 5 },
  video_outdated: { label: 'Video out of date', summary: 'The script changed after the latest video was made. Make the video again so it matches.', tone: 'attention', icon: 'refresh', step: 4 },
  failed: { label: 'Needs attention', summary: 'Something went wrong. Your document and your edits are safe; try again.', tone: 'bad', icon: 'warning', step: -1 },
};

/** The workflow strip under the next step (index = StageInfo.step; 5 = everything done). */
export const WORKFLOW_STEPS = ['Source', 'Plan', 'Script', 'Voice and visuals', 'Video'];

/**
 * A known stage, or null for anything else (a newer server's stage is shown by its label only).
 * @param {unknown} stage
 * @returns {LessonStage | null}
 */
export function knownStage(stage) {
  return typeof stage === 'string' && Object.prototype.hasOwnProperty.call(STAGE_INFO, stage) ? /** @type {LessonStage} */ (stage) : null;
}

/**
 * Words, tone and icon for a stage (unknown stages read as their own name, muted).
 * @param {string} stage
 * @returns {StageInfo}
 */
export function stageInfo(stage) {
  const known = knownStage(stage);
  if (known) return STAGE_INFO[known];
  const words = String(stage || '').replace(/[_-]+/g, ' ').trim();
  return { label: words ? words[0].toUpperCase() + words.slice(1) : 'Unknown', summary: '', tone: 'muted', icon: 'info', step: -1 };
}

/**
 * States of the workflow strip for a stage: done, current or to do. Empty when the stage does not say where the
 * lesson is (a generation failure, an unknown stage), so the strip never claims finished steps are still to do.
 * @param {string} stage
 * @param {string} [action]  the next step's action ("render" on 'failed': the latest video of a built lecture failed)
 * @returns {Array<{ label: string, state: 'done' | 'current' | 'todo' }>}
 */
export function workflowSteps(stage, action) {
  const at = stage === 'failed' && action === 'render' ? 4 : stageInfo(stage).step;
  if (at < 0) return [];
  return WORKFLOW_STEPS.map((label, i) => ({ label, state: i < at ? 'done' : i === at ? 'current' : 'todo' }));
}

/** Job kinds that write the version's media or timeline. */
const BUILD_KINDS = new Set(['build_assets', 'regenerate_scene', 'translate', 'import_legacy']);

/**
 * The stage of a version from summary fields, for servers that send none (no renders: a built lecture is
 * "ready to render").
 * @param {import('../types.js').VersionSummary | null | undefined} version
 * @param {import('../components/jobProgress.js').JobSummary | null | undefined} [job]  the project's active job
 * @returns {LessonStage | null}  null when there is no version yet and nothing runs
 */
export function deriveStage(version, job) {
  const active = !!job && (job.status === 'queued' || job.status === 'running');
  const forVersion = !!job && (!version || job.version_id == null || job.version_id === version.id);
  if (active && forVersion && job) {
    if (job.kind === 'render_video') return 'rendering';
    if (BUILD_KINDS.has(job.kind)) return 'building';
    if (job.kind === 'generate_lecture') {
      if (job.stage === 'ingest' || (!job.stage && !version)) return 'reading';
      if (job.stage === 'plan') return 'planning';
      if (job.stage === 'assets' || job.stage === 'timeline') return 'building';
      return 'writing';
    }
  }
  if (!version) return active ? 'reading' : null;
  if (version.status === 'awaiting_review') return version.review_stage === 'source' ? 'source_review' : 'plan_review';
  if (version.status === 'failed') return 'failed';
  if (version.status === 'generating') return 'writing';
  if (version.status === 'building') return 'building';
  if (!version.has_timeline || version.timeline_stale) return 'ready_to_build';
  return 'ready_to_render';
}

/**
 * The next step for a stage (the server's `next_step` wins; this is the fallback).
 * @param {string} stage
 * @param {{ projectId: number, versionId?: number | null }} ids
 * @returns {NextStep}
 */
export function nextStepFor(stage, ids) {
  const vid = ids.versionId || null;
  switch (stage) {
    case 'source_review':
      return { action: 'review_source', label: 'Check how Aadhi read your document', href: vid ? href('sourceReport', { id: ids.projectId, vid }) : null };
    case 'plan_review':
      return { action: 'review_plan', label: 'Review the plan', href: vid ? href('planReview', { id: ids.projectId, vid }) : null };
    case 'ready_to_build':
      return { action: 'build', label: 'Build the lecture', href: null };
    case 'ready_to_render':
      return { action: 'render', label: 'Make the video', href: null };
    case 'video_outdated':
      return { action: 'render', label: 'Make the video again', href: null };
    case 'video_ready':
      return { action: 'download', label: 'Your video is ready', href: null };
    case 'failed':
      return { action: 'retry', label: 'Try again', href: null };
    default:
      return { action: 'wait', label: 'Follow the progress', href: null };
  }
}

/**
 * A next-step link the Studio may follow: a Studio route ("#/...") or a same-site path ("/preview/9");
 * anything else (other sites, `javascript:`) is dropped.
 * @param {unknown} value
 * @returns {string | null}
 */
export function safeStepHref(value) {
  if (typeof value !== 'string' || /[\u0000-\u001f\u007f\\]/.test(value)) return null;
  const v = value.trim();
  if (v.startsWith('#/')) return v;
  if (v.startsWith('/') && !v.startsWith('//')) return v;
  return null;
}

/**
 * The stage and next step to show for a project: the server's when it sent them, else derived.
 * @param {{ id: number, stage?: string | null, next_step?: NextStep | null, current_version?: import('../types.js').VersionSummary | null, active_job?: any }} project  ProjectSummary
 * @returns {{ stage: string, next: NextStep, derived: boolean } | null}
 */
export function projectStage(project) {
  const v = project.current_version || null;
  const ids = { projectId: project.id, versionId: v ? v.id : null };
  if (typeof project.stage === 'string' && project.stage) {
    const step = project.next_step;
    const next = step && typeof step === 'object' && typeof step.label === 'string' && step.label.trim()
      ? { action: String(step.action || ''), label: step.label.trim(), href: safeStepHref(step.href) }
      : nextStepFor(project.stage, ids);
    return { stage: project.stage, next, derived: false };
  }
  const stage = deriveStage(v, project.active_job);
  return stage ? { stage, next: nextStepFor(stage, ids), derived: true } : null;
}
