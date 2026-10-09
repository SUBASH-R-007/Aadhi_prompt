// @ts-check
/**
 * Job status/stage helpers for the progress stepper (pure).
 */

export const GENERATE_STAGES = ['ingest', 'plan', 'script', 'validate', 'companion', 'assets', 'timeline'];

/**
 * Stage names each job handler reports through `ctx.progress(stage, ...)`
 * (aadhi/pipeline/orchestrator.py, aadhi/compose/video.py, aadhi/legacy/jobs.py).
 * @type {Record<string, string[]>}
 */
export const STAGES_BY_KIND = {
  generate_lecture: GENERATE_STAGES,
  regenerate_scene: ['script', 'validate', 'assets', 'timeline'],
  build_assets: ['assets', 'timeline'],
  translate: ['translate', 'assets', 'timeline'],
  render_video: ['screenshots', 'segments', 'mux', 'store'],
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
  mux: 'Join & mix audio',
  store: 'Save video',
  import: 'Import',
};

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
 * Stepper states for a job. Unknown stage names keep earlier stages pending (the bar still
 * shows progress).
 * @param {{ kind: string, status: string, stage?: string, progress?: number }} job
 * @param {string[]} [seenStages]  stages observed in events (helps for kinds with custom stages)
 * @returns {StageState[]}
 */
export function stageStates(job, seenStages = []) {
  const stages = STAGES_BY_KIND[job.kind] || uniq([...seenStages, job.stage || ''].filter(Boolean));
  const current = stages.indexOf(job.stage || '');
  return stages.map((name, i) => {
    /** @type {StageState['state']} */
    let state = 'pending';
    if (job.status === 'succeeded') state = 'done';
    else if (current >= 0 && i < current) state = 'done';
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
