// @ts-check
/**
 * Projects list: search, cards with status/current version/issue counts, live progress of
 * active jobs, new lecture and JSON import.
 *
 * Live job cards: the first MAX_STREAMS stream over SSE (per-user stream cap); the others are
 * refreshed by ONE page-level poll (GET /api/jobs?status=queued and ?status=running every
 * JOB_POLL_MS, backing off on 429/5xx) instead of one poll per card, so a page full of active
 * jobs stays far below API_RATE_LIMIT_PER_MINUTE. The poll pauses while the tab is hidden and runs
 * at once when it is visible again.
 * Each card says where the lesson is (`stage`, "Next: Review the plan"; derived from the summary on
 * older servers). Lectures that share a title get a short distinguishing suffix (session, unit,
 * subject, language, date), and repeated name parts are shown once.
 */

import { h, clear } from '../../shared/dom.js';
import { get } from '../../shared/api.js';
import { button, linkButton, input, spinner, emptyState, errorState } from '../components/form.js';
import { statusBadge, issueBadges, staleBadge } from '../components/badges.js';
import { jobProgress, pollBackoff, MAX_POLL_MS } from '../components/jobProgress.js';
import { openModal } from '../components/modal.js';
import { icon } from '../components/icons.js';
import { stageChip } from '../components/stage.js';
import { href } from '../router.js';
import { debounce, formatRelative } from '../util.js';
import { projectStage } from '../lib/lessonStage.js';
import { distinctTitles, nameKey, uniqueJoin, withSuffix } from '../lib/names.js';
import { pageHidden, onceVisible } from '../lib/visibility.js';
import { pageHeader, versionPrimaryLink, uploadForm, SOURCE_REVIEW_LABEL } from './common.js';

const PAGE_SIZE = 24;
/** Active jobs that get a live SSE stream; the rest share the page-level poll (SSE per-user cap). */
const MAX_STREAMS = 2;
/** Page-level job poll interval (2-3 requests per interval, whatever the number of cards). */
export const JOB_POLL_MS = 8000;
/** Statuses the page-level poll lists (documented single-value `status` filter). */
const POLLED_STATUSES = ['queued', 'running'];

/**
 * Updates for tracked jobs from the active-job listings.
 * @param {number[]} trackedIds
 * @param {{ items: any[], total: number }[]} listings  one listing per polled status
 * @returns {{ updates: Map<number, any>, missing: number[] }} `missing` = tracked jobs that left
 *   the active lists (finished or paused); only reported when the listings are complete
 */
export function matchActiveJobs(trackedIds, listings) {
  /** @type {Map<number, any>} */
  const byId = new Map();
  let complete = true;
  for (const l of listings) {
    const items = (l && Array.isArray(l.items)) ? l.items : [];
    for (const j of items) if (j && typeof j.id === 'number') byId.set(j.id, j);
    if (!l || typeof l.total !== 'number' || l.total > items.length) complete = false;
  }
  /** @type {Map<number, any>} */
  const updates = new Map();
  /** @type {number[]} */
  const missing = [];
  for (const id of trackedIds) {
    if (byId.has(id)) updates.set(id, byId.get(id));
    else if (complete) missing.push(id);
  }
  return { updates, missing };
}

/** @type {import('../types.js').ViewMount} */
export function mount(container, { app, query }) {
  /** @type {any[]} */
  let items = [];
  let total = 0;
  let q = query.q || '';
  /** @type {{ destroy: () => void }[]} */
  let jobWidgets = [];
  /** Job cards refreshed by the page-level poll (not streamed). */
  /** @type {Map<number, { update: (job: any) => void }>} */
  let polled = new Map();
  /** @type {ReturnType<typeof setTimeout> | null} */
  let pollTimer = null;
  let pollDelay = JOB_POLL_MS;
  let pollRunning = false;
  /** Cancels the wait for a hidden tab to become visible (the poll is paused meanwhile). @type {(() => void) | null} */
  let cancelVisibleWait = null;
  /** Distinguishing suffixes of lectures sharing a title (project id -> suffix). @type {Map<number, string>} */
  let suffixes = new Map();
  let destroyed = false;
  let loadToken = 0;

  const search = input({ type: 'search', value: q, placeholder: 'Search by title, subject or session', ariaLabel: 'Search projects', maxLength: 120 });
  const grid = h('div', { class: 'card-grid', 'aria-live': 'polite', 'aria-busy': 'false' });
  const footer = h('div', { class: 'list-footer' });
  const importBtn = button('Import JSON', { kind: 'outline', icon: 'upload' });
  const importInput = h('input', { type: 'file', class: 'sr-only', accept: '.json,application/json', tabindex: '-1', 'aria-hidden': 'true' });

  container.append(
    pageHeader('Projects', 'Your lectures, newest first.', linkButton('New lecture', '#/new', { kind: 'gold', icon: 'plus' }), importBtn, importInput),
    h('div', { class: 'toolbar' }, h('label', { class: 'search-box' }, icon('search'), search)),
    grid,
    footer,
  );

  const refreshSoon = debounce(() => void load(false), 600);

  const clearJobs = () => {
    for (const w of jobWidgets) w.destroy();
    jobWidgets = [];
    polled = new Map();
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = null;
    if (cancelVisibleWait) cancelVisibleWait();
    cancelVisibleWait = null;
  };

  const schedulePoll = () => {
    if (destroyed || pollTimer || cancelVisibleWait || !polled.size) return;
    pollTimer = setTimeout(() => {
      pollTimer = null;
      if (pageHidden()) {
        // hidden tab: no requests; poll as soon as the teacher is back
        cancelVisibleWait = onceVisible(() => {
          cancelVisibleWait = null;
          void pollJobs();
        });
        return;
      }
      void pollJobs();
    }, pollDelay);
  };

  /** One poll for every non-streamed job card. */
  async function pollJobs() {
    if (destroyed || !polled.size) return;
    if (pollRunning) {
      schedulePoll(); // a poll for an older card set is still in flight
      return;
    }
    pollRunning = true;
    const batch = polled;
    try {
      const listings = await Promise.all(POLLED_STATUSES.map((st) => get(`/api/jobs?status=${st}&limit=200`)));
      if (destroyed || batch !== polled) return;
      const { updates, missing } = matchActiveJobs([...batch.keys()], listings);
      for (const [id, job] of updates) batch.get(id)?.update(job);
      // Jobs that left the active lists finished (or paused for review): fetch their final state.
      for (const id of missing) {
        const job = await get(`/api/jobs/${id}`);
        if (destroyed || batch !== polled) return;
        batch.get(id)?.update(job);
        batch.delete(id);
      }
      pollDelay = JOB_POLL_MS;
    } catch (err) {
      pollDelay = pollBackoff(err, pollDelay, JOB_POLL_MS) ?? MAX_POLL_MS;
    } finally {
      pollRunning = false;
      if (batch === polled) schedulePoll();
    }
  }

  /** @param {boolean} append */
  async function load(append) {
    const token = ++loadToken;
    if (!append) {
      clearJobs();
      clear(grid);
      grid.appendChild(spinner('Loading projects…'));
    }
    grid.setAttribute('aria-busy', 'true');
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(append ? items.length : 0) });
      if (q) params.set('q', q);
      const res = await get(`/api/projects?${params}`);
      if (destroyed || token !== loadToken) return;
      items = append ? [...items, ...res.items] : res.items;
      total = res.total;
      render();
    } catch (err) {
      if (destroyed || token !== loadToken) return;
      clear(grid);
      grid.appendChild(errorState('Could not load projects.', () => void load(false)));
      app.reportError(err);
    } finally {
      grid.setAttribute('aria-busy', 'false');
    }
  }

  function render() {
    clearJobs();
    clear(grid);
    clear(footer);
    if (!items.length) {
      grid.appendChild(
        q
          ? emptyState('No matching projects', `Nothing matches “${q}”.`, button('Clear search', { kind: 'outline', onClick: () => setQuery('') }))
          : emptyState('No lectures yet', 'Upload a PDF or Word document and Aadhi will plan and script a lecture for you. New to Aadhi? The New lecture page also has a ready-written example to try.', linkButton('Create your first lecture', '#/new', { kind: 'gold', icon: 'plus' })),
      );
      return;
    }
    let streams = 0;
    suffixes = distinctTitles(items.map((p) => ({ ...p, title: cardTitle(p) })));
    for (const p of items) grid.appendChild(card(p, () => streams++ < MAX_STREAMS));
    pollDelay = JOB_POLL_MS;
    schedulePoll();
    footer.append(h('span', { class: 'muted' }, `Showing ${items.length} of ${total}`));
    if (items.length < total) footer.append(button('Load more', { kind: 'outline', onClick: () => void load(true) }));
  }

  /**
   * A card names the next step unless Aadhi is working (nothing to do) or the video is ready (the chip says so).
   * @param {{ stage: string, next: import('../types.js').NextStep }} stage
   */
  function showsNext(stage) {
    return !!stage.next.label && !['wait', 'none'].includes(stage.next.action) && stage.stage !== 'video_ready';
  }

  /**
   * The title a card shows (before any distinguishing suffix).
   * @param {any} p ProjectSummary
   */
  function cardTitle(p) {
    return p.title || p.session_title || 'Untitled lecture';
  }

  /**
   * @param {any} p ProjectSummary
   * @param {() => boolean} allowStream
   */
  function card(p, allowStream) {
    const v = p.current_version;
    const primary = versionPrimaryLink(p.id, v);
    const me = app.user();
    const title = cardTitle(p);
    const suffix = suffixes.get(p.id);
    // name parts once, and none that repeats the title
    const meta = uniqueJoin([p.subject_name, p.unit_name, p.session_number].filter((part) => nameKey(part) !== nameKey(title)));
    const stage = projectStage(p);
    const body = h(
      'article',
      { class: 'card project-card' },
      h(
        'h2',
        { class: 'card-title' },
        h('a', { href: href('project', { id: p.id }), title: suffix ? withSuffix(title, suffix) : undefined }, title, suffix ? h('span', { class: 'title-suffix' }, suffix.startsWith('(') ? ` ${suffix}` : ` · ${suffix}`) : null),
      ),
      meta ? h('p', { class: 'card-meta' }, meta) : null,
      p.session_title && nameKey(p.session_title) !== nameKey(title) ? h('p', { class: 'card-sub' }, p.session_title) : null,
      stage ? h('p', { class: 'card-stage' }, stageChip(stage.stage), showsNext(stage) ? h('span', { class: 'card-next' }, `Next: ${stage.next.label}`) : null) : null,
      h(
        'div',
        { class: 'row gap wrap card-badges' },
        v ? h('span', { class: 'badge badge-outline' }, `v${v.number}`) : null,
        v ? statusBadge(v.status) : statusBadge('draft', 'No version'),
        v && v.language ? h('span', { class: 'badge badge-outline', title: 'Narration language' }, v.language) : null,
        v && v.has_timeline && v.timeline_stale ? staleBadge() : null,
        v ? issueBadges(v.issue_counts, { compact: true }) : null,
      ),
      h('p', { class: 'card-foot muted' }, `Updated ${formatRelative(p.updated_at)}`, me && me.role === 'admin' && p.owner && p.owner.id !== me.id ? ` · ${p.owner.username}` : ''),
    );
    if (p.active_job) {
      const job = p.active_job;
      const live = job.status === 'queued' || job.status === 'running';
      const stream = live && allowStream();
      // a pause for the source review (the current version's review_stage) links to the source report
      const sourcePause = !!v && v.id === job.version_id && v.status === 'awaiting_review' && v.review_stage === 'source';
      const widget = jobProgress(job, {
        compact: true,
        stream,
        external: !stream,
        reviewHref: job.version_id ? href(sourcePause ? 'sourceReport' : 'planReview', { id: p.id, vid: job.version_id }) : undefined,
        reviewLabel: sourcePause ? SOURCE_REVIEW_LABEL : undefined,
        onFinished: () => refreshSoon(),
        onAwaitingReview: () => refreshSoon(),
      });
      jobWidgets.push(widget);
      if (live && !stream) polled.set(job.id, widget);
      body.appendChild(widget.el);
    }
    body.appendChild(
      h(
        'div',
        { class: 'card-actions row gap' },
        primary ? linkButton(primary.label, primary.hash, { kind: 'primary', small: true, icon: primary.label === 'Review plan' || primary.label === SOURCE_REVIEW_LABEL ? 'eye' : 'board' }) : null,
        linkButton('Details', href('project', { id: p.id }), { kind: 'outline', small: true }),
      ),
    );
    return body;
  }

  /** @param {string} value */
  function setQuery(value) {
    q = value.trim();
    search.value = q;
    app.replaceHash(href('projects', {}, { q }));
    void load(false);
  }

  const onSearch = debounce(() => setQuery(search.value), 300);
  search.addEventListener('input', onSearch);
  search.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') onSearch.flush();
    if (ev.key === 'Escape' && search.value) {
      search.value = '';
      onSearch();
    }
  });

  importBtn.addEventListener('click', () => importInput.click());
  importInput.addEventListener('change', async () => {
    const file = importInput.files && importInput.files[0];
    importInput.value = '';
    if (!file) return;
    if (!/\.json$/i.test(file.name)) {
      app.toast('Choose a .json file exported from Aadhi EduEngine (v1 or v2).', { kind: 'warning' });
      return;
    }
    importBtn.disabled = true;
    try {
      const res = await uploadForm('/api/projects/import', { file });
      showImportResult(res);
      void load(false);
    } catch (err) {
      app.reportError(err, 'Import failed.');
    } finally {
      importBtn.disabled = false;
    }
  });

  /** @param {any} res */
  function showImportResult(res) {
    const warnings = Array.isArray(res.warnings) ? res.warnings : [];
    const progress = res.job ? jobProgress(res.job, { onFinished: () => refreshSoon() }) : null;
    const modal = openModal({
      title: 'Lecture imported',
      size: 'lg',
      body: h(
        'div',
        {},
        h('p', {}, `“${res.project.title}” was imported as version ${res.version.number}. Voice and visuals are being built.`),
        warnings.length ? h('div', { class: 'notice warning' }, h('h3', {}, `${warnings.length} conversion warning${warnings.length === 1 ? '' : 's'}`), h('ul', {}, warnings.map((w) => h('li', {}, String(w))))) : null,
        progress ? progress.el : null,
      ),
      actions: [
        { label: 'Close', kind: 'outline', value: null },
        { label: 'Open project', kind: 'gold', value: 'open' },
      ],
      onClose: () => progress && progress.destroy(),
    });
    void modal.result.then((v) => {
      if (v === 'open') app.navigate(href('project', { id: res.project.id }));
    });
  }

  void load(false);
  return {
    destroy() {
      destroyed = true;
      clearJobs();
      onSearch.cancel();
      refreshSoon.cancel();
    },
  };
}
