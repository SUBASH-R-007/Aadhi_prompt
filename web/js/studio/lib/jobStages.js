// @ts-check
/**
 * Job status/stage helpers for the progress stepper (pure).
 */

export const GENERATE_STAGES = ['ingest', 'plan', 'script', 'validate', 'companion', 'assets', 'timeline'];

/**
 * The stages of a render_video job, per engine (one render never mixes engines: aadhi/compose/video.py render_video
 * picks it from the timeline). Classic: aadhi/compose/video.py `_render` (screenshots, segments, mux, `check` while
 * RENDER_OUTPUT_QA is on, store). Motion, for a Dynamic video (DYNAMIC_VIDEO_DESIGN §7.1): aadhi/compose/motion/job.py
 * `render_dynamic` (preflight, store) around engine.py `render_motion` (capture, mux, check). Both report `done` last.
 */
export const RENDER_STAGES = {
  classic: ['screenshots', 'segments', 'mux', 'check', 'store'],
  motion: ['preflight', 'capture', 'mux', 'check', 'store'],
};

/**
 * Stage names each job handler reports through `ctx.progress(stage, ...)`
 * (aadhi/pipeline/orchestrator.py, aadhi/compose/video.py, aadhi/legacy/jobs.py). render_video: the classic list;
 * stageStates picks the engine's (renderEngine).
 * @type {Record<string, string[]>}
 */
export const STAGES_BY_KIND = {
  generate_lecture: GENERATE_STAGES,
  regenerate_scene: ['script', 'validate', 'assets', 'timeline'],
  build_assets: ['assets', 'timeline'],
  translate: ['translate', 'assets', 'timeline'],
  render_video: RENDER_STAGES.classic,
  import_legacy: ['import'],
};

/** @type {Record<string, string>} */
export const STAGE_LABELS = {
  ingest: 'Read source',
  plan: 'Plan lecture',
  script: 'Write scenes',
  validate: 'Review & repair',
  companion: 'Companion sheet',
  assets: 'Voice & visuals',
  timeline: 'Timeline',
  translate: 'Translate',
  screenshots: 'Capture frames',
  segments: 'Encode scenes',
  preflight: 'Check layout',
  capture: 'Capture & encode',
  mux: 'Join & mix audio',
  check: 'Check video',
  store: 'Save video',
  import: 'Import',
};

/** Stages only the motion engine reports (the classic renderer's own are `screenshots` and `segments`). */
const MOTION_ONLY_STAGES = new Set(RENDER_STAGES.motion.filter((s) => !RENDER_STAGES.classic.includes(s)));

/**
 * The engine a render_video job runs, from the stages it reported: the motion engine's own (`preflight`, `capture`)
 * name it; without them (queued, or `mux`/`check`/`store` before any event is seen) the classic list is shown, which
 * is right for every state: mux, check and store have the same place in both lists.
 * @param {{ stage?: string }} job
 * @param {string[]} [seenStages]
 * @returns {'classic' | 'motion'}
 */
export function renderEngine(job, seenStages = []) {
  return [job.stage || '', ...seenStages].some((s) => MOTION_ONLY_STAGES.has(s)) ? 'motion' : 'classic';
}

/** @type {Record<string, string>} */
export const JOB_KIND_LABELS = {
  generate_lecture: 'Generate lecture',
  regenerate_scene: 'Regenerate scene',
  build_assets: 'Build assets',
  translate: 'Translate',
  render_video: 'Render MP4',
  import_legacy: 'Import',
  cleanup: 'Cleanup',
};

export const ACTIVE_STATUSES = new Set(['queued', 'running', 'awaiting_review']);
export const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled']);

/** @param {{ status: string } | null | undefined} job */
export function isActive(job) {
  return !!job && ACTIVE_STATUSES.has(job.status);
}

/** @param {{ status: string } | null | undefined} job */
export function isTerminal(job) {
  return !!job && TERMINAL_STATUSES.has(job.status);
}

/**
 * @typedef {{ name: string, label: string, state: 'done' | 'active' | 'pending' | 'failed' }} StageState
 */

/**
 * The stepper's stage list of a job: its kind's (a render's per engine), else the stages seen.
 * @param {{ kind: string, stage?: string }} job
 * @param {string[]} [seenStages]
 * @returns {string[]}
 */
export function stagesFor(job, seenStages = []) {
  if (job.kind === 'render_video') return RENDER_STAGES[renderEngine(job, seenStages)];
  return STAGES_BY_KIND[job.kind] || uniq([...seenStages, job.stage || ''].filter(Boolean));
}

/**
 * Stepper states for a job. `done` (reported after the last stage, before the job is marked finished) marks every
 * step done. A stage the list does not know (a newer server) never resets the stepper: the steps already seen in the
 * events stay done, and none is active (the bar still shows progress).
 * @param {{ kind: string, status: string, stage?: string, progress?: number }} job
 * @param {string[]} [seenStages]  stages observed in events (helps for kinds with custom stages)
 * @returns {StageState[]}
 */
export function stageStates(job, seenStages = []) {
  const stages = stagesFor(job, seenStages);
  const stage = job.stage || '';
  const current = stage === 'done' ? stages.length : stages.indexOf(stage);
  /** steps before this index are done */
  const doneBefore = current >= 0 || !stage ? current : Math.max(-1, ...seenStages.map((s) => stages.indexOf(s))) + 1;
  return stages.map((name, i) => {
    /** @type {StageState['state']} */
    let state = 'pending';
    if (job.status === 'succeeded') state = 'done';
    else if (i < doneBefore) state = 'done';
    else if (current >= 0 && i === current) {
      state = job.status === 'failed' || job.status === 'cancelled' ? 'failed' : job.status === 'awaiting_review' ? 'done' : 'active';
    }
    return { name, label: STAGE_LABELS[name] || humanizeStage(name), state };
  });
}

/** @param {string[]} xs */
function uniq(xs) {
  return [...new Set(xs)];
}

/** @param {string} s */
function humanizeStage(s) {
  const t = String(s).replace(/[_-]+/g, ' ').trim();
  return t ? t[0].toUpperCase() + t.slice(1) : t;
}

/**
 * User-facing label for a job status.
 * @param {string} status
 */
export function statusLabel(status) {
  return (
    {
      queued: 'Queued',
      running: 'Running',
      awaiting_review: 'Awaiting review',
      succeeded: 'Done',
      failed: 'Failed',
      cancelled: 'Cancelled',
      draft: 'Draft',
      generating: 'Generating',
      building: 'Building',
      ready: 'Ready',
    }[status] || status
  );
}
