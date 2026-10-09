// @ts-check
/**
 * Live job progress (GET /api/jobs/{id}/stream SSE; falls back to polling when the stream is
 * refused, e.g. 429 too many streams): stage stepper, progress bar, status message, live log,
 * cost, cancel/retry, review link while awaiting review (the plan review, or the source report under
 * `reviewLabel`), callbacks on completion. A job
 * that failed because the provider rejected the user's own API key links to the API keys page.
 * The latest provider notice (an AI service is slow to answer or being retried: job events whose
 * `data.notice` is set, aadhi/providers/_notify.py) is shown under the status message until the
 * job logs anything else or ends; every notice also stays in the live log.
 * Screen readers hear stage and status changes through a polite live region (not every
 * progress tick). The action buttons are created once and only shown/hidden, so keyboard
 * focus on Cancel survives progress updates.
 * Polling backs off exponentially on 429/5xx/network errors (honouring Retry-After); pages
 * with many jobs pass `external: true` and push updates through `update(job)` instead.
 * Polling pauses while the tab is hidden and polls at once when it is visible again (an SSE stream
 * stays open). Stages read in plain words ("Voice & visuals"); the raw stage names are technical
 * details shown only with `?debug` (lib/debug.js).
 */

import { h, clear, append } from '../../shared/dom.js';
import { get, post, sse, ApiError } from '../../shared/api.js';
import { stageStates, isActive, isTerminal, JOB_KIND_LABELS, STAGE_LABELS, statusLabel } from '../lib/jobStages.js';
import { showTechnical } from '../lib/debug.js';
import { pageHidden, onceVisible } from '../lib/visibility.js';
import { formatUsd, formatDate } from '../util.js';
import { statusBadge, progressBar } from './badges.js';
import { button, linkButton } from './form.js';
import { icon } from './icons.js';
import { confirmDialog } from './modal.js';
import { toast } from './toast.js';
import { errorMessage } from '../errors.js';

/**
 * @typedef {object} JobSummary
 * @property {number} id
 * @property {string} kind
 * @property {string} status
 * @property {string} [stage]
 * @property {number} [progress]
 * @property {string} [message]
 * @property {number | null} [project_id]
 * @property {number | null} [version_id]
 * @property {string | null} [error]
 * @property {string | null} [error_code]
 * @property {number} [cost_usd]
 * @property {number} [attempts]
 * @property {string} [created_at]
 * @property {string | null} [started_at]
 * @property {string | null} [finished_at]
 */

/**
 * @typedef {object} JobEvent
 * @property {number} id
 * @property {string} created_at
 * @property {string} level
 * @property {string} stage
 * @property {string} message
 * @property {number | null} [progress]
 * @property {Record<string, unknown>} [data]
 */

/**
 * @typedef {object} JobProgressOptions
 * @property {(job: JobSummary) => void} [onSuccess]
 * @property {(job: JobSummary) => void} [onAwaitingReview]
 * @property {(job: JobSummary) => void} [onFinished]      any terminal state
 * @property {(job: JobSummary) => void} [onReplaced]      retry created a new job
 * @property {(job: JobSummary) => void} [onUpdate]
 * @property {boolean} [compact]                           card-sized (no stepper/log)
 * @property {boolean} [showLog]
 * @property {string} [reviewHref]                         review link shown while awaiting_review (the plan review)
 * @property {string} [reviewLabel]                        its label (default "Review plan"; a source pause
 *   links to the source report with e.g. "Check the source")
 * @property {number} [pollMs]
 * @property {boolean} [fireInitial]   run callbacks when the job is already finished/awaiting review
 * @property {boolean} [stream]        false = poll instead of opening an SSE stream (lists with
 *   many active jobs stay under SSE_MAX_STREAMS_PER_USER)
 * @property {boolean} [external]      neither stream nor poll: the owner pushes updates with
 *   `update(job)` (one page-level poll for many widgets)
 */

const MAX_LOG = 300;
/** Job `error_code` when a provider rejected the starter's personal API key (aadhi/jobs/worker.py). */
export const PERSONAL_KEY_REJECTED = 'personal_key_rejected';
/** Upper bound of the polling back-off. */
export const MAX_POLL_MS = 60_000;

/**
 * Next polling delay after a failed poll, or null to stop polling.
 * @param {unknown} err
 * @param {number} current  current delay (ms)
 * @param {number} base     normal delay (ms)
 * @returns {number | null}
 */
export function pollBackoff(err, current, base) {
  if (err instanceof ApiError) {
    if (err.status === 401 || err.status === 403 || err.status === 404) return null; // gone or not ours
    if (err.status === 429 || err.status >= 500) {
      const hinted = err.retryAfter && err.retryAfter > 0 ? err.retryAfter * 1000 : 0;
      return Math.min(MAX_POLL_MS, Math.max(hinted, current * 2, base));
    }
    return Math.min(MAX_POLL_MS, Math.max(current, base));
  }
  return Math.min(MAX_POLL_MS, Math.max(current * 2, base)); // network error
}

/**
 * A job stage in plain words ("assets" -> "Voice & visuals"), or its raw name with technical details.
 * @param {string} stage
 * @param {boolean} [technical]
 */
export function stageText(stage, technical = false) {
  if (!stage || technical) return stage || '';
  return STAGE_LABELS[stage] || stage.replace(/[_-]+/g, ' ');
}

/**
 * @param {JobSummary} initial
 * @param {JobProgressOptions} [opts]
 * @returns {{ el: HTMLElement, destroy: () => void, job: () => JobSummary, update: (job: JobSummary) => void }}
 */
export function jobProgress(initial, opts = {}) {
  let job = initial;
  /** @type {JobEvent[]} */
  let events = [];
  const seenIds = new Set();
  /** @type {{ close: () => void } | null} */
  let stream = null;
  /** @type {ReturnType<typeof setTimeout> | null} */
  let pollTimer = null;
  let polling = false;
  const basePoll = opts.pollMs || 3000;
  let pollDelay = basePoll;
  /** Cancels the wait for the tab to become visible again (polling paused while hidden). @type {(() => void) | null} */
  let cancelVisibleWait = null;
  const technical = showTechnical();
  let destroyed = false;
  let lastAnnounced = '';
  /** @type {Set<string>} */
  const fired = new Set();
  let logLoaded = false;

  const root = h('section', { class: ['job-progress', opts.compact ? 'compact' : ''], 'aria-label': 'Job progress', tabindex: '-1' });
  const live = h('div', { class: 'sr-only', 'aria-live': 'polite', 'aria-atomic': 'true' });
  const header = h('div', { class: 'job-header' });
  const stepper = h('ol', { class: 'stepper', 'aria-label': 'Stages' });
  const barHost = h('div', { class: 'job-bar' });
  const message = h('div', { class: 'job-message' });
  const notice = h('div', { class: 'job-notice', role: 'status', hidden: true });
  const errorBox = h('div', { class: 'job-error', role: 'alert', hidden: true });
  const actions = h('div', { class: 'job-actions row gap' });
  const logList = h('ol', { class: 'job-log', 'aria-label': 'Job log' });
  const logSummary = h('summary', {}, 'Live log');
  const logBox = h('details', { class: 'job-log-box' }, logSummary, logList);
  logBox.addEventListener('toggle', () => {
    if (logBox.open && !logLoaded) void loadBacklog();
  });

  root.append(header, live);
  if (!opts.compact) root.append(stepper);
  root.append(barHost, message);
  if (!opts.compact) root.append(notice);
  root.append(errorBox, actions);
  if (!opts.compact && opts.showLog !== false) root.append(logBox);

  const render = () => {
    clear(header);
    append(header, [
      h('span', { class: 'job-kind' }, JOB_KIND_LABELS[job.kind] || job.kind),
      statusBadge(job.status),
      job.cost_usd ? h('span', { class: 'job-cost muted', title: 'Estimated cost so far' }, formatUsd(job.cost_usd)) : null,
    ]);
    if (!opts.compact) {
      clear(stepper);
      const seenStages = [...new Set(events.map((e) => e.stage).filter(Boolean))];
      for (const st of stageStates(job, seenStages)) {
        stepper.appendChild(
          h(
            'li',
            { class: ['step', `step-${st.state}`], 'aria-current': st.state === 'active' ? 'step' : undefined },
            h('span', { class: 'step-dot', 'aria-hidden': 'true' }, st.state === 'done' ? icon('check', { size: 12 }) : st.state === 'failed' ? icon('close', { size: 12 }) : null),
            h('span', { class: 'step-label' }, st.label),
            h('span', { class: 'sr-only' }, ` (${st.state === 'done' ? 'done' : st.state === 'active' ? 'in progress' : st.state === 'failed' ? 'failed' : 'pending'})`),
          ),
        );
      }
    }
    clear(barHost);
    const value = job.status === 'succeeded' ? 1 : job.progress || 0;
    barHost.append(progressBar(value, `${JOB_KIND_LABELS[job.kind] || job.kind} progress`), h('span', { class: 'job-pct' }, `${Math.round(value * 100)}%`));
    barHost.classList.toggle('indeterminate', job.status === 'queued');
    message.textContent = job.status === 'queued' ? 'Waiting for a worker…' : job.message || statusLabel(job.status);
    if (!isActive(job)) notice.hidden = true;
    errorBox.hidden = !(job.status === 'failed' && job.error);
    clear(errorBox);
    if (job.error) {
      errorBox.append(`${job.error_code === 'budget' ? 'Budget limit reached: ' : ''}${job.error}`);
      // The provider refused the user's own key: link to where it can be replaced.
      if (job.error_code === PERSONAL_KEY_REJECTED) errorBox.append(' ', h('a', { href: '#/keys' }, 'Open API keys'));
    }
    renderActions();
    const announce = `${statusLabel(job.status)}${job.stage ? `, ${technical ? 'stage ' : ''}${stageText(job.stage, technical)}` : ''}`;
    if (announce !== lastAnnounced) {
      lastAnnounced = announce;
      live.textContent = announce;
    }
  };

  // Action buttons: built once, shown/hidden per status (re-creating them on every update
  // would drop keyboard focus to <body> each progress tick).
  const cancelBtn = button('Cancel', { kind: 'outline', small: true, icon: 'stop' });
  cancelBtn.addEventListener('click', async () => {
    const target = job.id;
    const ok = await confirmDialog({ title: 'Cancel job?', message: 'The job stops at the next safe point. Work done so far is kept in the cache.', confirmLabel: 'Cancel job', cancelLabel: 'Keep running', danger: true });
    if (!ok || destroyed || target !== job.id || !isActive(job)) return;
    cancelBtn.disabled = true;
    try {
      const res = await post(`/api/jobs/${job.id}/cancel`, {});
      if (res && res.job) setJob(res.job);
    } catch (err) {
      toast(errorMessage(err, 'Could not cancel the job.'), { kind: 'error' });
    } finally {
      cancelBtn.disabled = false;
    }
  });
  const reviewLink = opts.reviewHref ? linkButton(opts.reviewLabel || 'Review plan', opts.reviewHref, { kind: 'gold', icon: 'eye', small: true }) : null;
  const retryBtn = button('Retry', { kind: 'gold', small: true, icon: 'retry' });
  retryBtn.addEventListener('click', async () => {
    retryBtn.disabled = true;
    try {
      const res = await post(`/api/jobs/${job.id}/retry`, {});
      if (res && res.job && !destroyed) replace(res.job);
    } catch (err) {
      toast(errorMessage(err, 'Could not retry the job.'), { kind: 'error' });
    } finally {
      retryBtn.disabled = false;
    }
  });
  append(actions, [cancelBtn, reviewLink, retryBtn]);

  const renderActions = () => {
    const active = /** @type {HTMLElement | null} */ (document.activeElement);
    const hadFocus = !!active && actions.contains(active);
    cancelBtn.hidden = !isActive(job);
    if (reviewLink) reviewLink.hidden = job.status !== 'awaiting_review';
    retryBtn.hidden = !(job.status === 'failed' || job.status === 'cancelled');
    actions.hidden = cancelBtn.hidden && retryBtn.hidden && (!reviewLink || reviewLink.hidden);
    // The focused action disappeared (e.g. the job finished): keep focus inside the widget.
    if (hadFocus && active && (active.hidden || actions.hidden)) {
      const next = [retryBtn, reviewLink, cancelBtn].find((b) => b && !b.hidden && !actions.hidden);
      (next || root).focus();
    }
  };

  /** @param {JobEvent} ev */
  const addEvent = (ev) => {
    if (!ev || typeof ev.id !== 'number' || seenIds.has(ev.id)) return;
    seenIds.add(ev.id);
    events.push(ev);
    if (events.length > MAX_LOG) events = events.slice(-MAX_LOG);
    showNotice(ev);
    if (opts.compact || opts.showLog === false) return;
    const atBottom = logList.scrollHeight - logList.scrollTop - logList.clientHeight < 24;
    logList.appendChild(
      h(
        'li',
        { class: ['log-line', `log-${ev.level || 'info'}`] },
        h('time', { datetime: ev.created_at, class: 'muted' }, formatDate(ev.created_at).split(', ').pop() || ''),
        ev.stage ? h('span', { class: 'log-stage' }, stageText(ev.stage, technical)) : null,
        h('span', { class: 'log-text' }, ev.message || ''),
      ),
    );
    while (logList.children.length > MAX_LOG) logList.firstElementChild?.remove();
    logSummary.textContent = `Live log (${events.length})`;
    if (atBottom) logList.scrollTop = logList.scrollHeight;
  };

  /**
   * A provider notice replaces the line under the status message; any other event clears it.
   * @param {JobEvent} ev
   */
  const showNotice = (ev) => {
    const isNotice = !!(ev.data && typeof ev.data === 'object' && ev.data.notice);
    if (isNotice) {
      notice.textContent = ev.message || '';
      notice.classList.toggle('job-notice-warning', ev.level === 'warning');
    }
    notice.hidden = !isNotice || !isActive(job);
  };

  /** @param {JobSummary} next */
  const setJob = (next) => {
    if (!next || next.id !== job.id) return;
    job = { ...job, ...next };
    render();
    if (opts.onUpdate) opts.onUpdate(job);
    handleStatus();
  };

  /** Fire status callbacks once per (job, status). */
  const handleStatus = () => {
    const key = `${job.id}:${job.status}`;
    if (fired.has(key)) return;
    fired.add(key);
    if (job.status === 'awaiting_review') {
      stopUpdates();
      if (opts.onAwaitingReview) opts.onAwaitingReview(job);
    } else if (isTerminal(job)) {
      stopUpdates();
      if (job.status === 'succeeded' && opts.onSuccess) opts.onSuccess(job);
      if (opts.onFinished) opts.onFinished(job);
    }
  };

  const stopUpdates = () => {
    if (stream) stream.close();
    stream = null;
    polling = false;
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = null;
    if (cancelVisibleWait) cancelVisibleWait();
    cancelVisibleWait = null;
  };

  /** Past events for jobs that are not streamed (finished or awaiting review). */
  const loadBacklog = async () => {
    logLoaded = true;
    try {
      const res = await get(`/api/jobs/${job.id}/events?after=0&limit=200`);
      for (const ev of (res && res.items) || []) addEvent(ev);
    } catch {
      /* the log is best-effort */
    }
  };

  const startPolling = () => {
    if (polling || destroyed) return;
    polling = true;
    pollDelay = basePoll;
    const tick = async () => {
      pollTimer = null;
      if (!polling || destroyed) return;
      if (pageHidden()) {
        // hidden tab: no request now; poll as soon as it is visible again
        if (!cancelVisibleWait) {
          cancelVisibleWait = onceVisible(() => {
            cancelVisibleWait = null;
            if (polling && !destroyed && !pollTimer) void tick();
          });
        }
        return;
      }
      const polledId = job.id;
      try {
        const after = events.length ? events[events.length - 1].id : 0;
        const [j, evs] = await Promise.all([get(`/api/jobs/${job.id}`), opts.compact ? Promise.resolve(null) : get(`/api/jobs/${job.id}/events?after=${after}&limit=200`)]);
        if (destroyed || !polling || polledId !== job.id) return;
        for (const ev of (evs && evs.items) || []) addEvent(ev);
        pollDelay = basePoll;
        setJob(j);
      } catch (err) {
        if (destroyed || !polling || polledId !== job.id) return;
        const next = pollBackoff(err, pollDelay, basePoll);
        if (next === null) {
          stopUpdates();
          return;
        }
        pollDelay = next;
      }
      if (polling && !destroyed) pollTimer = setTimeout(() => void tick(), pollDelay);
    };
    void tick();
  };

  const connect = () => {
    if (destroyed || opts.external || !isActive(job) || job.status === 'awaiting_review') return;
    if (opts.stream === false || typeof EventSource === 'undefined') {
      startPolling();
      return;
    }
    try {
      stream = sse(
        `/api/jobs/${job.id}/stream`,
        {
          job_event: (data) => addEvent(data),
          job: (data) => setJob(data),
          end: (data) => {
            stream = null;
            if (data && typeof data === 'object') setJob(data);
            if (!isTerminal(job) && job.status !== 'awaiting_review') startPolling();
          },
        },
        {
          onError: () => {
            stream = null;
            startPolling();
          },
        },
      );
      logLoaded = true; // the stream replays events from the start
    } catch {
      startPolling();
    }
  };

  /** @param {JobSummary} next */
  const replace = (next) => {
    stopUpdates();
    job = next;
    events = [];
    seenIds.clear();
    clear(logList);
    notice.hidden = true;
    logSummary.textContent = 'Live log';
    logLoaded = false;
    render();
    if (opts.onReplaced) opts.onReplaced(next);
    connect();
  };

  render();
  if (isActive(job) && job.status !== 'awaiting_review') connect();
  else if (opts.fireInitial) queueMicrotask(handleStatus);
  else fired.add(`${job.id}:${job.status}`); // already in this state when shown: no callbacks

  return {
    el: root,
    job: () => job,
    /** Push a newer state of this job (external mode, or any owner with fresher data). */
    update(next) {
      if (!destroyed) setJob(next);
    },
    destroy() {
      destroyed = true;
      stopUpdates();
    },
  };
}
