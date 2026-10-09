// @ts-check
/**
 * Project page: versions (status/issues/stale, editor/plan/preview links, duplicate,
 * translate, set current, how the source was read), renders with downloads, share links
 * (create/copy/revoke/views), job history (cancel/retry/log), sources, and project actions (edit
 * details, regenerate, analytics, delete). A version waiting for its source review
 * (`review_stage: "source"`) links to the source report instead of the plan review. Above the renders,
 * "Before you render" lists how the video would differ from the editor and, under its own heading, the
 * quality check's open errors and warnings (render preflight `quality`; never blocking). Each version with a
 * screenplay links to its Visual review; the current, built version also says how many of its visuals need
 * attention (GET /visual-review, read once per revision).
 * "Next step" (top of the page) says where the lesson is (`stage`) and offers its one next action
 * (`next_step`, from the server; derived from the summary on older servers): a link, or build / make the
 * video / try again / go to the videos right here. Unsaved editor changes on this browser turn build and
 * render into "open the editor" first. Each render says whether it matches the current script
 * (`matches_current`: "Up to date" / "Out of date"); a failed one says why and can be tried again.
 * Render and job numbers, revisions and raw job stages are technical details shown only with `?debug`.
 */

import { h, clear } from '../../shared/dom.js';
import { get, post, patch, del, ApiError } from '../../shared/api.js';
import { button, linkButton, field, input, select, checkbox, spinner, errorState, emptyState, formGrid } from '../components/form.js';
import { statusBadge, issueBadges, staleBadge } from '../components/badges.js';
import { jobProgress, stageText } from '../components/jobProgress.js';
import { openModal, confirmDialog, promptDialog } from '../components/modal.js';
import { menuButton } from '../components/menu.js';
import { icon } from '../components/icons.js';
import { nextStepCard } from '../components/stage.js';
import { openVideoPreview } from '../components/videoPreview.js';
import { href } from '../router.js';
import { formatDate, formatDuration, formatUsd, formatBytes, formatRelative, copyText, absoluteUrl } from '../util.js';
import { JOB_KIND_LABELS, isActive } from '../lib/jobStages.js';
import { voicesFor } from '../lib/optionsForm.js';
import { loadDraft } from '../lib/draftStore.js';
import { showTechnical } from '../lib/debug.js';
import { projectStage } from '../lib/lessonStage.js';
import { nameKey, uniqueJoin } from '../lib/names.js';
import { errorMessage } from '../errors.js';
import { createOptionsForm } from './optionsFields.js';
import { pageHeader, breadcrumbs, versionPrimaryLink, jobModal, EDITABLE_STATUSES } from './common.js';
import { summarize } from './visualReview.js';

/**
 * Next-step actions this page carries out itself when the step has no link (`open_editor`, `review_plan`,
 * `review_source`, `preview` and `videos` get their link here instead; see `linkFor`).
 */
const PAGE_ACTIONS = new Set(['build', 'render', 'retry', 'download', 'watch', 'share']);

/** @type {import('../types.js').ViewMount} */
export function mount(container, { app, params, query }) {
  const projectId = params.id;
  /** @type {any} */
  let data = null;
  /** @type {any[]} */
  let shares = [];
  /** @type {Array<{ destroy: () => void }>} */
  let disposables = [];
  let destroyed = false;
  /** @type {number | null} */
  let renderVersionId = query.version ? Number(query.version) : null;
  /** Latest renders request: an older response never replaces a newer version's list. */
  let rendersToken = 0;
  /** Visuals needing attention per version state ("id:revision:built"), read once. @type {Map<string, Promise<number | null>>} */
  const attention = new Map();

  const root = h('div', { class: 'project-detail' }, spinner('Loading project…'));
  container.appendChild(root);

  const dispose = () => {
    for (const d of disposables) d.destroy();
    disposables = [];
  };

  /**
   * The version row (it carries `review_stage` while awaiting review).
   * @param {number | null | undefined} vid
   */
  const versionRow = (vid) => (data.versions || []).find((/** @type {any} */ v) => v.id === vid) || null;

  /**
   * Primary link for a version (the source report while it waits for the source review): from its row in
   * `versions`, which carries `review_stage`.
   * @param {any} v
   */
  const primaryFor = (v) => versionPrimaryLink(projectId, v ? versionRow(v.id) || v : null);

  async function load() {
    try {
      const [detail, shareRes] = await Promise.all([get(`/api/projects/${projectId}`), get(`/api/projects/${projectId}/shares`).catch(() => ({ items: [] }))]);
      if (destroyed) return;
      data = detail;
      shares = shareRes.items || [];
      render();
    } catch (err) {
      if (destroyed) return;
      clear(root);
      root.appendChild(errorState('Could not load this project.', () => void load()));
      app.reportError(err);
    }
  }

  function render() {
    dispose();
    clear(root);
    const p = data.project;
    app.setTitle(p.title || 'Project');
    const current = p.current_version;
    const primary = primaryFor(current);
    const actionsMenu = menuButton('More', [
      { label: 'Edit details', icon: 'file', onClick: () => void editDetails() },
      { label: 'Regenerate lecture', icon: 'wand', onClick: () => void regenerate() },
      { label: 'Analytics', icon: 'chart', onClick: () => app.navigate(href('analytics', { id: p.id })) },
      { label: 'Delete project', icon: 'trash', danger: true, onClick: () => void deleteProject() },
    ]);
    disposables.push(actionsMenu);
    // name parts once, and not the page title again ("Ohm's Law" as both title and session title)
    const subtitle = uniqueJoin([p.subject_name, p.unit_name, p.session_number, p.session_title].filter((part) => nameKey(part) !== nameKey(p.title)));
    const step = nextStepSection();
    // one gold action on the page: the next step's, when it offers one
    const stepLeads = !!step && !!step.querySelector('.next-step-actions .btn');
    root.append(
      breadcrumbs([{ label: 'Projects', hash: '#/projects' }, { label: p.title || 'Project' }]),
      pageHeader(
        p.title || 'Untitled lecture',
        subtitle || null,
        primary ? linkButton(primary.label, primary.hash, { kind: stepLeads ? 'primary' : 'gold', icon: 'board' }) : null,
        current && current.has_timeline ? linkButton('Preview', `/preview/${current.id}`, { kind: 'outline', icon: 'play', newTab: true }) : null,
        actionsMenu.el,
      ),
    );
    if (step) root.append(step);
    if (p.active_job) {
      const jobVersion = versionRow(p.active_job.version_id);
      const sourcePause = !!jobVersion && jobVersion.status === 'awaiting_review' && jobVersion.review_stage === 'source';
      const widget = jobProgress(p.active_job, {
        // a source pause links to the source report from the line below (not to the plan review)
        reviewHref: p.active_job.version_id && !sourcePause ? href('planReview', { id: p.id, vid: p.active_job.version_id }) : undefined,
        onFinished: () => void load(),
        onAwaitingReview: () => void load(),
      });
      disposables.push(widget);
      root.append(
        h(
          'section',
          { class: 'panel glass' },
          h('h2', {}, 'In progress'),
          widget.el,
          sourcePause ? h('p', { class: 'row gap wrap' }, h('span', {}, 'Aadhi has read the document and is waiting for you.'), linkButton('Check how Aadhi read your document', href('sourceReport', { id: p.id, vid: jobVersion.id }), { kind: 'gold', icon: 'eye', small: true })) : null,
        ),
      );
    }
    root.append(versionsSection(), rendersSection(), sharesSection(), jobsSection(), sourcesSection());
  }

  // --- next step ------------------------------------------------------------------------
  /** The current version as its row in `versions` (the row carries `review_stage`). */
  function currentRow() {
    const cv = data.project.current_version;
    return cv ? versionRow(cv.id) || cv : null;
  }

  /**
   * Unsaved editor changes for this version in this browser (the editor's local draft).
   * @param {number} vid
   */
  function hasLocalDraft(vid) {
    const me = app.user();
    return !!me && !!loadDraft(me.id, vid);
  }

  /** "Next step": where the lesson is and its one next action (null when there is nothing to say yet). */
  function nextStepSection() {
    const p = data.project;
    const v = currentRow();
    const found = projectStage({ ...p, current_version: v });
    if (!found) return null;
    /** @type {import('../types.js').NextStep} */
    let next = { ...found.next, href: found.next.href || linkFor(found.next.action, v) };
    // A project without a usable version is sent to its own page ("open"): no link to the page already open.
    if (next.href === href('project', { id: p.id })) next = { ...next, href: null };
    /** @type {HTMLElement | null} */
    let note = null;
    if (v && (next.action === 'build' || next.action === 'render') && EDITABLE_STATUSES.has(v.status) && hasLocalDraft(v.id)) {
      // building or rendering now would leave out the changes only this browser has
      note = h('p', { class: 'notice warning small', dataset: { unsavedDraft: 'true' } }, 'You have changes in the editor on this computer that are not saved yet. Save them in the editor first, so they are part of the lecture.');
      next = { action: 'open_editor', label: 'Open the editor to save your changes', href: href('editor', { id: p.id, vid: v.id }) };
    }
    return nextStepCard({
      stage: found.stage,
      next,
      note,
      canDo: (action) => !!v && PAGE_ACTIONS.has(action),
      onAction: (step, btn) => void runStep(step, btn),
    });
  }

  /**
   * A link for an action that has its own page (when the server sent none).
   * @param {string} action
   * @param {any} v  current version row
   * @returns {string | null}
   */
  function linkFor(action, v) {
    if (!v) return null;
    const id = data.project.id;
    if (action === 'open_editor' && EDITABLE_STATUSES.has(v.status)) return href('editor', { id, vid: v.id });
    if (action === 'review_plan') return href('planReview', { id, vid: v.id });
    if (action === 'review_source') return href('sourceReport', { id, vid: v.id });
    if (action === 'preview' && v.has_timeline) return `/preview/${v.id}`;
    if (action === 'videos') return '#/videos';
    return null;
  }

  /**
   * Carry out a next step that has no link.
   * @param {import('../types.js').NextStep} step
   * @param {HTMLButtonElement} btn
   */
  async function runStep(step, btn) {
    const v = currentRow();
    if (!v || btn.disabled) return;
    btn.disabled = true;
    try {
      if (step.action === 'build') await buildVersion(v);
      else if (step.action === 'render') await renderVersion(v);
      else if (step.action === 'retry') await retryVersion(v);
      else if (step.action === 'share') showSection('shares');
      else showRenders(v.id); // download / watch: this version's videos, on this page
    } finally {
      btn.disabled = false;
    }
  }

  /**
   * A job already running for this version (409 job_in_progress with its id): follow it instead.
   * @param {unknown} err
   */
  async function followRunningJob(err) {
    if (!(err instanceof ApiError) || err.code !== 'job_in_progress' || !err.body || !err.body.job_id) return false;
    try {
      const job = await get(`/api/jobs/${err.body.job_id}`);
      if (destroyed) return true;
      app.toast('Aadhi is already working on this lecture.', { kind: 'warning' });
      void jobModal(`${JOB_KIND_LABELS[job.kind] || 'Job'} in progress`, job, { onFinished: () => void load() });
      return true;
    } catch {
      return false;
    }
  }

  /** @param {any} v */
  async function buildVersion(v) {
    try {
      const res = await post(`/api/versions/${v.id}/build`, { scene_ids: null });
      if (destroyed) return;
      await load();
      void jobModal('Building voice and visuals', res.job, {
        description: 'Only scenes that changed since the last build are made again. You can close this dialog; the build carries on.',
        onSuccess: () => app.toast('The lecture is built.', { kind: 'success' }),
        onFinished: () => void load(),
      });
    } catch (err) {
      if (!(await followRunningJob(err))) app.reportError(err, 'Could not start the build.');
    }
  }

  /**
   * "Make the video": a few options and what the video would show differently, then POST /render (the
   * editor's dialog has every option).
   * @param {any} v
   */
  async function renderVersion(v) {
    const pre = await get(`/api/versions/${v.id}/render/preflight`).catch(() => null);
    if (destroyed) return;
    const items = (pre && Array.isArray(pre.items) ? pre.items : []).filter((/** @type {any} */ it) => it && it.reason !== 'quality');
    const silent = items.some((/** @type {any} */ it) => it.blocking);
    const captions = checkbox({ label: 'Burn captions into the video', checked: false, hint: 'Captions are also delivered as SRT and VTT files.' });
    const intro = checkbox({ label: 'Include the intro (logo and title cards)', checked: true });
    const modal = openModal({
      title: 'Make the video',
      description: 'Makes an MP4 of the built lecture, exactly as the preview shows it. Long lectures take several minutes. The editor has more options.',
      body: h('div', {}, preflightNotice(pre), captions, intro),
      actions: silent
        ? [
            { label: 'Make it anyway', kind: 'outline', value: 'degraded' },
            { label: 'Fix first', kind: 'gold', value: 'fix', autofocus: true },
          ]
        : [
            { label: 'Cancel', kind: 'outline', value: null },
            { label: 'Make the video', kind: 'gold', value: 'render' },
          ],
    });
    const choice = await modal.result;
    if (destroyed) return;
    if (choice === 'fix') {
      app.navigate(href('editor', { id: data.project.id, vid: v.id }));
      return;
    }
    if (choice !== 'render' && choice !== 'degraded') return;
    /** @type {Record<string, any>} */
    const body = { burn_captions: captions.input.checked, include_intro: intro.input.checked };
    if (pre) body.allow_degraded = choice === 'degraded';
    await startRender(v, body);
  }

  /**
   * @param {any} v
   * @param {Record<string, any>} body
   */
  async function startRender(v, body) {
    try {
      const res = await post(`/api/versions/${v.id}/render`, body);
      if (destroyed) return;
      renderVersionId = v.id;
      await load();
      void jobModal('Making the video', res.job, {
        onSuccess: () => app.toast('Your video is ready.', { kind: 'success' }),
        onFinished: () => void load(),
      });
    } catch (err) {
      if (destroyed) return;
      if (err instanceof ApiError && err.code === 'render_preflight') {
        // scenes went silent since the check: list them and ask again
        const found = err.body && Array.isArray(err.body.items) ? err.body.items : [];
        const ok = await confirmDialog({
          title: 'Some scenes would be silent',
          message: 'These scenes have no narration audio yet. Build the lecture again in the editor, or make the video as it is.',
          details: found.filter((/** @type {any} */ it) => it && it.reason !== 'quality').map((/** @type {any} */ it) => sceneNote(it)),
          confirmLabel: 'Make it anyway',
        });
        if (ok && !destroyed) await startRender(v, { ...body, allow_degraded: true });
        return;
      }
      if (err instanceof ApiError && err.code === 'timeline_stale') {
        app.toast(errorMessage(err), { kind: 'warning' });
        void load();
        return;
      }
      if (!(await followRunningJob(err))) app.reportError(err, 'Could not start the video.');
    }
  }

  /**
   * "Try again": retry the version's latest job when it failed or was cancelled; otherwise open the editor
   * (or, without a screenplay, offer to regenerate the lecture).
   * @param {any} v
   */
  async function retryVersion(v) {
    const latest = (data.jobs || []).find((/** @type {any} */ j) => j.version_id === v.id);
    if (latest && (latest.status === 'failed' || latest.status === 'cancelled')) {
      try {
        const res = await post(`/api/jobs/${latest.id}/retry`, {});
        if (destroyed) return;
        await load();
        if (res && res.job) void jobModal(`${JOB_KIND_LABELS[res.job.kind] || 'Job'}: trying again`, res.job, { onFinished: () => void load() });
      } catch (err) {
        if (!(await followRunningJob(err))) app.reportError(err, 'Could not try again.');
      }
      return;
    }
    if (EDITABLE_STATUSES.has(v.status) && v.status !== 'failed') {
      app.navigate(href('editor', { id: data.project.id, vid: v.id }));
      return;
    }
    await regenerate();
  }

  /**
   * Scroll to a section of this page and move focus to its heading.
   * @param {string} name  data-section value
   */
  function showSection(name) {
    const section = /** @type {HTMLElement | null} */ (root.querySelector(`[data-section="${name}"]`));
    if (!section) return;
    if (typeof section.scrollIntoView === 'function') section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const h2 = section.querySelector('h2');
    if (h2) {
      h2.setAttribute('tabindex', '-1');
      h2.focus({ preventScroll: true });
    }
  }

  // --- versions -------------------------------------------------------------------------
  function versionsSection() {
    const p = data.project;
    const versions = data.versions || [];
    const technical = showTechnical();
    const rows = versions.map((/** @type {any} */ v) => {
      const isCurrent = p.current_version && p.current_version.id === v.id;
      const primary = primaryFor(v);
      const menu = menuButton(
        'Actions',
        [
          { label: 'Duplicate', icon: 'copy', onClick: () => void duplicate(v), disabled: !EDITABLE_STATUSES.has(v.status) },
          { label: 'Translate…', icon: 'globe', onClick: () => void translate(v), disabled: !EDITABLE_STATUSES.has(v.status) },
          { label: 'Set as current', icon: 'check', onClick: () => void setCurrent(v), disabled: isCurrent },
          { label: 'Show renders', icon: 'film', onClick: () => showRenders(v.id) },
          { label: 'How Aadhi read the source', icon: 'file', onClick: () => app.navigate(href('sourceReport', { id: p.id, vid: v.id })) },
          { label: 'Export JSON', icon: 'download', href: `/api/versions/${v.id}/export.json`, download: true, disabled: !EDITABLE_STATUSES.has(v.status) },
        ],
        { small: true, kind: 'ghost' },
      );
      disposables.push(menu);
      return h(
        'tr',
        { class: isCurrent ? 'current' : '' },
        h('th', { scope: 'row' }, `v${v.number}`, isCurrent ? h('span', { class: 'badge badge-gold' }, 'Current') : null, v.label ? h('div', { class: 'muted small' }, v.label) : null),
        h('td', {}, statusBadge(v.status)),
        h('td', {}, v.language, v.source_version_id ? h('div', { class: 'muted small' }, 'translation') : null),
        h(
          'td',
          {},
          technical ? h('span', { class: 'mono small', title: 'Screenplay revision' }, `r${v.revision} `) : null,
          v.has_timeline && v.timeline_stale ? staleBadge() : null,
          v.has_timeline && !v.timeline_stale ? h('span', { class: 'muted small' }, 'Up to date') : null,
          !v.has_timeline ? h('div', { class: 'muted small' }, 'Not built yet') : null,
        ),
        h('td', {}, issueBadges(v.issue_counts, { showClean: true })),
        h('td', {}, h('time', { datetime: v.created_at, title: formatDate(v.created_at) }, formatRelative(v.created_at))),
        h(
          'td',
          { class: 'row gap nowrap' },
          primary ? linkButton(primary.label, primary.hash, { kind: 'primary', small: true }) : null,
          v.has_timeline ? linkButton('Preview', `/preview/${v.id}`, { kind: 'outline', small: true, newTab: true, icon: 'play' }) : null,
          EDITABLE_STATUSES.has(v.status) ? linkButton('Review visuals', href('visualReview', { vid: v.id }), { kind: 'ghost', small: true, icon: 'eye' }) : null,
          isCurrent && v.has_timeline && EDITABLE_STATUSES.has(v.status) ? attentionBadge(v) : null,
          menu.el,
        ),
      );
    });
    return h(
      'section',
      { class: 'panel glass' },
      h('h2', {}, 'Versions'),
      versions.length
        ? h(
            'div',
            { class: 'table-wrap' },
            h(
              'table',
              { class: 'table' },
              h('caption', { class: 'sr-only' }, 'Lecture versions'),
              h('thead', {}, h('tr', {}, ['Version', 'Status', 'Language', 'Build', 'Issues', 'Created', 'Actions'].map((t) => h('th', { scope: 'col' }, t)))),
              h('tbody', {}, rows),
            ),
          )
        : emptyState('No versions yet', 'Generation has not produced a version.'),
    );
  }

  /**
   * "N visuals need attention" for a version (filled in when its visual review has been read; nothing
   * when none does or it cannot be read). Links to the review, filtered to them.
   * @param {any} v
   */
  function attentionBadge(v) {
    const slot = h('span', { class: 'vr-attention-slot', dataset: { visualAttention: String(v.id) } });
    const key = `${v.id}:${v.revision}:${v.built_revision}`;
    let request = attention.get(key);
    if (!request) {
      request = get(`/api/versions/${v.id}/visual-review`).then(
        (res) => (res && res.summary && Number.isInteger(res.summary.needs_attention) ? res.summary.needs_attention : summarize(res && Array.isArray(res.scenes) ? res.scenes : []).needs_attention),
        () => null,
      );
      attention.set(key, request);
    }
    void request.then((n) => {
      if (destroyed || !n) return;
      slot.appendChild(
        h('a', { class: 'badge badge-attention', href: href('visualReview', { vid: v.id }, { filter: 'attention' }) }, icon('warning', { size: 12 }), `${n} visual${n === 1 ? ' needs' : 's need'} attention`),
      );
    });
    return slot;
  }

  /** @param {any} v */
  async function duplicate(v) {
    const label = await promptDialog({ title: `Duplicate v${v.number}`, label: 'Label for the copy', value: v.label ? `${v.label} (copy)` : 'Copy', maxLength: 255, confirmLabel: 'Duplicate' });
    if (label === null) return;
    try {
      const res = await post(`/api/versions/${v.id}/duplicate`, { label });
      app.toast(`Created v${res.version.number}.`, { kind: 'success' });
      await load();
    } catch (err) {
      app.reportError(err, 'Could not duplicate the version.');
    }
  }

  /** @param {any} v */
  async function setCurrent(v) {
    try {
      await patch(`/api/projects/${projectId}`, { current_version_id: v.id });
      app.toast(`v${v.number} is now the current version (share links without a pinned version follow it).`, { kind: 'success' });
      await load();
    } catch (err) {
      app.reportError(err, 'Could not change the current version.');
    }
  }

  /** @param {any} v */
  async function translate(v) {
    let meta;
    try {
      meta = await app.meta();
    } catch (err) {
      app.reportError(err);
      return;
    }
    const langs = (meta.languages || []).filter((/** @type {any} */ l) => l.code !== v.language);
    if (!langs.length) {
      app.toast('No other languages are available.', { kind: 'warning' });
      return;
    }
    const lang = select({ options: langs.map((/** @type {any} */ l) => ({ value: l.code, label: l.label })), value: langs[0].code });
    const voice = select({ options: [{ value: '', label: 'Default voice' }] });
    const board = checkbox({ label: 'Translate the board text too', checked: false, hint: 'Off keeps formulas and board text in the original language; narration is always translated.' });
    const fillVoices = () => {
      clear(voice);
      voice.appendChild(h('option', { value: '' }, 'Default voice'));
      for (const vo of voicesFor(meta, '', lang.value)) voice.appendChild(h('option', { value: vo.id }, `${vo.label} (${vo.language})`));
    };
    lang.addEventListener('change', fillVoices);
    fillVoices();
    const modal = openModal({
      title: `Translate v${v.number}`,
      description: 'Creates a new version with translated narration and new voice-over. Ids, formulas and code are preserved.',
      body: h('div', {}, formGrid(field('Target language', lang), field('Voice', voice)), board),
      actions: [
        { label: 'Cancel', kind: 'outline', value: null },
        {
          label: 'Translate',
          kind: 'gold',
          onClick: async () => {
            /** @type {Record<string, any>} */
            const body = { target_language: lang.value, translate_board: board.input.checked };
            if (voice.value) body.tts_voice = voice.value;
            return post(`/api/versions/${v.id}/translate`, body);
          },
        },
      ],
    });
    const res = await modal.result;
    if (!res || !res.job) return;
    await load();
    void jobModal(`Translating to ${lang.value}`, res.job, { onFinished: () => void load() });
  }

  // --- renders --------------------------------------------------------------------------
  function rendersSection() {
    const versions = data.versions || [];
    const p = data.project;
    const host = h('div', { class: 'renders' });
    const section = h('section', { class: 'panel glass', 'data-section': 'renders' }, h('h2', {}, 'Rendered videos'));
    if (!versions.length) return section;
    const vid = renderVersionId && versions.some((/** @type {any} */ v) => v.id === renderVersionId) ? renderVersionId : p.current_version ? p.current_version.id : versions[0].id;
    const picker = select({ options: versions.map((/** @type {any} */ v) => ({ value: String(v.id), label: `v${v.number}${v.label ? ` · ${v.label}` : ''} (${v.language})` })), value: String(vid), ariaLabel: 'Version' });
    picker.addEventListener('change', () => {
      renderVersionId = Number(picker.value);
      void loadRenders(renderVersionId, host);
    });
    section.append(h('div', { class: 'toolbar' }, field('Version', picker)), host);
    void loadRenders(vid, host);
    return section;
  }

  /** @param {number} vid */
  function showRenders(vid) {
    renderVersionId = vid;
    render();
    const section = /** @type {HTMLElement | null} */ (root.querySelector('[data-section="renders"]'));
    if (section) {
      if (typeof section.scrollIntoView === 'function') section.scrollIntoView({ behavior: 'smooth', block: 'start' });
      const h2 = section.querySelector('h2');
      if (h2) {
        h2.setAttribute('tabindex', '-1');
        h2.focus({ preventScroll: true });
      }
    }
  }

  /**
   * @param {number} vid
   * @param {HTMLElement} host
   */
  async function loadRenders(vid, host) {
    const token = ++rendersToken;
    clear(host);
    host.appendChild(spinner('Loading renders…'));
    try {
      const [res, preflight] = await Promise.all([
        get(`/api/versions/${vid}/renders`),
        // What the MP4 would show differently from the editor (silent scenes, fallback boards...).
        get(`/api/versions/${vid}/render/preflight`).catch(() => null),
      ]);
      if (destroyed || token !== rendersToken) return;
      clear(host);
      const notice = preflightNotice(preflight);
      if (notice) host.appendChild(notice);
      const quality = qualityNotice(preflight);
      if (quality) host.appendChild(quality);
      const items = res.items || [];
      if (!items.length) {
        host.appendChild(emptyState('No renders yet', 'When the lecture is built, choose “Make the video” at the top of this page or “Render MP4” in the editor.'));
        return;
      }
      for (const r of items) host.appendChild(renderRow(r));
    } catch (err) {
      if (destroyed || token !== rendersToken) return;
      clear(host);
      host.appendChild(errorState('Could not load renders.', () => void loadRenders(vid, host)));
    }
  }

  /**
   * "Before you render" list from GET /api/versions/{vid}/render/preflight (null when empty).
   * @param {any} pre
   */
  function preflightNotice(pre) {
    const items = (pre && Array.isArray(pre.items) ? pre.items : []).filter((/** @type {any} */ it) => it && it.reason !== 'quality');
    if (!items.length) return null;
    const title = pre.blocking ? 'Before you render: some scenes would be silent in the video' : 'Before you render: the video will differ from the editor here';
    return h(
      'details',
      { class: ['notice', pre.blocking ? 'warning' : 'info', 'render-preflight'], 'data-preflight': pre.blocking ? 'blocking' : 'info', open: pre.blocking ? true : undefined },
      h('summary', {}, `${title} (${items.length})`),
      h('ul', { class: 'plain-list small' }, items.map((/** @type {any} */ it) => h('li', {}, sceneNote(it)))),
    );
  }

  /**
   * The quality check before export (`pre.quality`, or items with `reason: "quality"` from older servers): the
   * lecture's open errors and warnings, under a heading of their own. Never blocks a render (null when none).
   * @param {any} pre
   */
  function qualityNotice(pre) {
    const listed = pre && Array.isArray(pre.quality) ? pre.quality : pre && Array.isArray(pre.items) ? pre.items.filter((/** @type {any} */ it) => it && it.reason === 'quality') : [];
    const items = listed.filter((/** @type {any} */ it) => it && it.message);
    if (!items.length) return null;
    const counted = items.filter((/** @type {any} */ it) => it.scene_id || it.code).length;
    return h(
      'details',
      { class: ['notice', 'info', 'render-quality'], 'data-quality': String(counted) },
      h('summary', {}, `Before you render: the quality check found things to review (${counted})`),
      h('p', { class: 'small' }, 'You can still render; fixing them in the editor first gives a better video.'),
      h('ul', { class: 'plain-list small' }, items.map((/** @type {any} */ it) => h('li', { 'data-severity': it.severity || 'warning' }, qualityLine(it)))),
    );
  }

  /**
   * A preflight item's scene number as the editor's scene list shows it (`scene_number`, skipped scenes counted);
   * older servers and stored render warnings without it: the timeline position.
   * @param {any} it
   * @returns {number | null}
   */
  function sceneNumberOf(it) {
    return Number.isInteger(it.scene_number) ? it.scene_number : Number.isInteger(it.scene_index) ? it.scene_index + 1 : null;
  }

  /** @param {any} it quality preflight item */
  function qualityLine(it) {
    if (!it.scene_id && !it.code) return it.message; // "N more ...: see Issues in the editor"
    const num = sceneNumberOf(it);
    const where = num !== null ? `Scene ${num}${it.title ? ` (${it.title})` : ''}` : it.scene_id ? it.title || it.scene_id : 'Whole lecture';
    return `${it.severity === 'error' ? 'Needs fixing' : 'Please check'}: ${where}: ${it.message}`;
  }

  /** @param {any} it preflight item / render warning */
  function sceneNote(it) {
    const num = sceneNumberOf(it);
    const where = num !== null ? `Scene ${num}` : 'A scene';
    return `${where}${it.title ? ` (${it.title})` : ''}: ${it.message || it.reason || ''}`;
  }

  /**
   * The server's check of a finished MP4 (Render.options.qa) and the scenes it rendered with a
   * fallback (Render.options.warnings).
   * @param {any} options
   */
  function renderChecks(options) {
    const opts = options && typeof options === 'object' ? options : {};
    const qa = opts.qa && typeof opts.qa === 'object' ? opts.qa : null;
    const warnings = Array.isArray(opts.warnings) ? opts.warnings : [];
    /** @type {HTMLElement[]} */
    const out = [];
    if (qa) {
      const notes = [...(Array.isArray(qa.problems) ? qa.problems : []), ...(Array.isArray(qa.warnings) ? qa.warnings : [])];
      const sound = qa.audible ? ', sound OK' : qa.has_audio === false ? ', no sound track' : qa.audible === false ? ', silent' : '';
      const line = notes.length ? `Video check: ${notes.join('; ')}` : `Video checked: ${qa.frames} frames at ${qa.fps} fps${sound}.`;
      out.push(h('p', { class: notes.length ? 'notice warning small' : 'muted small', 'data-qa': qa.ok ? 'ok' : 'failed' }, line));
    }
    if (warnings.length) {
      out.push(
        h(
          'details',
          { class: 'render-warnings' },
          h('summary', {}, `${warnings.length} scene${warnings.length === 1 ? '' : 's'} rendered differently from the editor`),
          h('ul', { class: 'plain-list small' }, warnings.map((/** @type {any} */ w) => h('li', {}, sceneNote(w)))),
        ),
      );
    }
    return out;
  }

  /**
   * Whether a finished video matches the current script (`matches_current`; nothing on older servers).
   * @param {boolean | undefined} matches
   */
  function currentBadge(matches) {
    if (matches === true) return h('span', { class: 'badge badge-ok', title: 'Made from the current script', dataset: { matchesCurrent: 'true' } }, icon('check', { size: 12 }), 'Up to date');
    if (matches === false) return h('span', { class: 'badge badge-attention', title: 'Made before your latest changes to the script', dataset: { matchesCurrent: 'false' } }, icon('refresh', { size: 12 }), 'Out of date');
    return null;
  }

  /**
   * Why a render did not finish (the job's message, already redacted by the server) and "Try again".
   * @param {import('../types.js').RenderSummary} r
   */
  function renderFailure(r) {
    const job = r.job;
    const reason = r.status === 'cancelled' ? 'The video was cancelled before it was finished.' : `The video could not be made${job && job.error ? `: ${job.error}` : '.'}`;
    const again = job && (job.status === 'failed' || job.status === 'cancelled') ? button('Try again', { kind: 'gold', small: true, icon: 'retry' }) : null;
    if (again && job) {
      again.addEventListener('click', async () => {
        again.disabled = true;
        try {
          await post(`/api/jobs/${job.id}/retry`, {});
          if (destroyed) return;
          app.toast('Making the video again.', { kind: 'info' });
          await load();
        } catch (err) {
          if (!(await followRunningJob(err))) app.reportError(err, 'Could not try again.');
        } finally {
          again.disabled = false;
        }
      });
    }
    return h('div', { class: 'render-failure row gap wrap', role: 'note' }, h('span', { class: 'error-text small' }, reason), again);
  }

  /** @param {import('../types.js').RenderSummary} r */
  function renderRow(r) {
    const dl = r.downloads || {};
    const technical = showTechnical();
    const preview = r.status === 'succeeded' && typeof r.preview_url === 'string' && r.preview_url ? button('Watch', { kind: 'outline', small: true, icon: 'play', dataset: { fk: `watch:${r.id}` } }) : null;
    if (preview && r.preview_url) {
      const src = r.preview_url;
      preview.addEventListener('click', () => {
        openVideoPreview({
          title: data.project.title || 'Video',
          src,
          downloadUrl: dl.video || null,
          details: [r.duration_s ? formatDuration(r.duration_s) : '', formatDate(r.created_at)].filter(Boolean).join(' · '),
          // the page may have been redrawn while the video played: focus goes back to this render's new Watch button
          returnFocus: () => /** @type {HTMLElement | null} */ (root.querySelector(`[data-fk="watch:${r.id}"]`) || root.querySelector(`[data-render-id="${r.id}"] a, [data-render-id="${r.id}"] button`)),
          // an expired signed link (S3 without a CDN): one fresh link from the version's render list
          refresh: async () => {
            const res = await get(`/api/versions/${r.version_id}/renders`);
            const fresh = res && Array.isArray(res.items) ? res.items.find((/** @type {any} */ x) => x && x.id === r.id) : null;
            return fresh && typeof fresh.preview_url === 'string' ? fresh.preview_url : null;
          },
        });
      });
    }
    const row = h(
      'article',
      { class: 'render-row', dataset: { renderId: String(r.id) } },
      h(
        'div',
        { class: 'row gap wrap' },
        h('strong', {}, technical ? `Render #${r.id}` : 'Video'),
        statusBadge(r.status),
        r.status === 'succeeded' ? currentBadge(r.matches_current) : null,
        h('span', { class: 'badge badge-outline' }, r.language),
        r.duration_s ? h('span', { class: 'muted' }, formatDuration(r.duration_s)) : null,
        technical && r.built_revision ? h('span', { class: 'muted' }, `revision ${r.built_revision}`) : null,
        h('time', { class: 'muted', datetime: r.created_at, title: formatDate(r.created_at) }, formatRelative(r.created_at)),
      ),
      r.status === 'succeeded'
        ? h(
            'div',
            { class: 'row gap wrap' },
            dl.video ? linkButton('Download MP4', dl.video, { kind: 'gold', small: true, icon: 'download', download: true }) : null,
            preview,
            dl.srt ? linkButton('Captions (SRT)', dl.srt, { small: true, icon: 'download', download: true }) : null,
            dl.vtt ? linkButton('Captions (VTT)', dl.vtt, { small: true, icon: 'download', download: true }) : null,
          )
        : null,
      r.status === 'failed' || r.status === 'cancelled' ? renderFailure(r) : null,
      ...renderChecks(r.options),
    );
    if (r.chapters_text) {
      const copy = button('Copy chapters', { kind: 'ghost', small: true, icon: 'copy' });
      copy.addEventListener('click', async () => {
        const ok = await copyText(r.chapters_text);
        app.toast(ok ? 'Chapters copied (paste into the YouTube description).' : 'Could not copy.', { kind: ok ? 'success' : 'error' });
      });
      row.append(h('details', { class: 'chapters' }, h('summary', {}, 'YouTube chapters'), h('pre', { class: 'mono' }, r.chapters_text), copy));
    }
    if (r.job && isActive(r.job)) {
      const widget = jobProgress(r.job, { compact: true, onFinished: () => void load() });
      disposables.push(widget);
      row.append(widget.el);
    }
    return row;
  }

  // --- shares ---------------------------------------------------------------------------
  function sharesSection() {
    const versions = data.versions || [];
    const versionSel = select({
      options: [{ value: '', label: 'Current version (follows updates)' }, ...versions.filter((/** @type {any} */ v) => v.has_timeline).map((/** @type {any} */ v) => ({ value: String(v.id), label: `v${v.number} only (${v.language})` }))],
      value: '',
    });
    const expiry = select({
      options: [
        { value: '', label: 'Never expires' },
        { value: '7', label: '7 days' },
        { value: '30', label: '30 days' },
        { value: '90', label: '90 days' },
        { value: '365', label: '1 year' },
      ],
      value: '30',
    });
    const create = button('Create share link', { kind: 'gold', icon: 'share', small: true });
    create.addEventListener('click', async () => {
      create.disabled = true;
      try {
        /** @type {Record<string, any>} */
        const body = {};
        if (versionSel.value) body.version_id = Number(versionSel.value);
        if (expiry.value) body.expires_in_days = Number(expiry.value);
        const res = await post(`/api/projects/${projectId}/shares`, body);
        const ok = await copyText(absoluteUrl(res.url));
        app.toast(ok ? 'Share link created and copied.' : 'Share link created.', { kind: 'success' });
        const list = await get(`/api/projects/${projectId}/shares`);
        shares = list.items || [];
        render();
      } catch (err) {
        app.reportError(err, 'Could not create the share link.');
      } finally {
        create.disabled = false;
      }
    });
    const now = Date.now();
    const rows = shares.map((s) => {
      const expired = s.expires_at && new Date(s.expires_at).getTime() < now;
      const state = s.revoked_at ? 'Revoked' : expired ? 'Expired' : 'Active';
      const url = absoluteUrl(s.url);
      const copy = button('Copy link', { kind: 'ghost', small: true, icon: 'copy', disabled: state !== 'Active' });
      copy.addEventListener('click', async () => {
        const ok = await copyText(url);
        app.toast(ok ? 'Link copied.' : 'Could not copy the link.', { kind: ok ? 'success' : 'error' });
      });
      const revoke = button('Revoke', { kind: 'danger', small: true, disabled: state !== 'Active' });
      revoke.addEventListener('click', async () => {
        const ok = await confirmDialog({ title: 'Revoke share link?', message: 'Students using this link will no longer be able to watch.', confirmLabel: 'Revoke', danger: true });
        if (!ok) return;
        try {
          await del(`/api/shares/${encodeURIComponent(s.token)}`);
          app.toast('Link revoked.', { kind: 'success' });
          const list = await get(`/api/projects/${projectId}/shares`);
          shares = list.items || [];
          render();
        } catch (err) {
          app.reportError(err, 'Could not revoke the link.');
        }
      });
      const v = (data.versions || []).find((/** @type {any} */ x) => x.id === s.version_id);
      return h(
        'tr',
        {},
        h('td', {}, h('a', { href: url, target: '_blank', rel: 'noopener noreferrer', class: 'mono small' }, url)),
        h('td', {}, v ? `v${v.number}` : 'Current'),
        h('td', {}, statusBadge(state === 'Active' ? 'ready' : 'cancelled', state)),
        h('td', {}, String(s.view_count ?? 0)),
        h('td', {}, s.expires_at ? formatDate(s.expires_at) : 'Never'),
        h('td', { class: 'row gap nowrap' }, copy, revoke),
      );
    });
    return h(
      'section',
      { class: 'panel glass', 'data-section': 'shares' },
      h('h2', {}, 'Share with students'),
      h('p', { class: 'muted' }, 'Students watch in the browser without an account. Views and quiz answers appear in Analytics.'),
      h('div', { class: 'row gap wrap align-end' }, field('Version', versionSel), field('Expiry', expiry), create),
      shares.length
        ? h(
            'div',
            { class: 'table-wrap' },
            h('table', { class: 'table' }, h('caption', { class: 'sr-only' }, 'Share links'), h('thead', {}, h('tr', {}, ['Link', 'Version', 'Status', 'Views', 'Expires', 'Actions'].map((t) => h('th', { scope: 'col' }, t)))), h('tbody', {}, rows)),
          )
        : h('p', { class: 'muted' }, 'No share links yet.'),
    );
  }

  // --- jobs -----------------------------------------------------------------------------
  function jobsSection() {
    const jobs = data.jobs || [];
    const technical = showTechnical();
    const rows = jobs.map((/** @type {any} */ j) => {
      const view = button('Details', { kind: 'ghost', small: true });
      const kind = JOB_KIND_LABELS[j.kind] || j.kind;
      view.addEventListener('click', () => void jobModal(technical ? `${kind} #${j.id}` : kind, j, { onFinished: () => void load() }));
      return h(
        'tr',
        {},
        technical ? h('td', {}, `#${j.id}`) : null,
        h('td', {}, kind),
        h('td', {}, statusBadge(j.status), j.error ? h('div', { class: 'small error-text' }, j.error) : null),
        h('td', {}, j.stage ? stageText(j.stage, technical) : '–'),
        h('td', {}, formatUsd(j.cost_usd)),
        h('td', {}, h('time', { datetime: j.created_at, title: formatDate(j.created_at) }, formatRelative(j.created_at))),
        h('td', {}, view),
      );
    });
    const columns = [...(technical ? ['Job'] : []), 'What', 'Status', 'Step', 'Cost', 'Started', ''];
    return h(
      'section',
      { class: 'panel glass' },
      h('details', { class: 'collapsible' }, h('summary', {}, h('h2', { class: 'inline' }, `Jobs (${jobs.length})`)), jobs.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'table compact' }, h('caption', { class: 'sr-only' }, 'Recent jobs'), h('thead', {}, h('tr', {}, columns.map((t) => h('th', { scope: 'col' }, t)))), h('tbody', {}, rows))) : h('p', { class: 'muted' }, 'No jobs.')),
    );
  }

  function sourcesSection() {
    const sources = data.sources || [];
    return h(
      'section',
      { class: 'panel glass' },
      h('h2', {}, 'Source documents'),
      sources.length
        ? h('ul', { class: 'plain-list' }, sources.map((/** @type {any} */ s) => h('li', {}, icon('file'), h('span', {}, s.filename), h('span', { class: 'muted' }, ` · ${formatBytes(s.size_bytes)}${s.page_count ? ` · ${s.page_count} pages` : ''} · ${formatDate(s.created_at)}`))))
        : h('p', { class: 'muted' }, 'No source document (imported lecture).'),
    );
  }

  // --- project actions ------------------------------------------------------------------
  async function editDetails() {
    const p = data.project;
    const fields = /** @type {const} */ ([
      ['title', 'Title', 255],
      ['subject_name', 'Subject', 240],
      ['unit_name', 'Unit', 240],
      ['session_number', 'Session number', 60],
      ['session_title', 'Session title', 255], // ProjectPatch.session_title max_length
    ]);
    /** @type {Record<string, HTMLInputElement>} */
    const inputs = {};
    const grid = formGrid(
      fields.map(([key, label, max]) => {
        inputs[key] = input({ value: p[key] || '', maxLength: max, required: key === 'title' });
        return field(label, inputs[key], { required: key === 'title' });
      }),
    );
    const modal = openModal({
      title: 'Edit project details',
      body: grid,
      actions: [
        { label: 'Cancel', kind: 'outline', value: null },
        {
          label: 'Save',
          kind: 'gold',
          submit: true,
          onClick: async () => {
            if (!inputs.title.value.trim()) throw new Error('The title is required.');
            /** @type {Record<string, string>} */
            const body = {};
            for (const [key] of fields) body[key] = inputs[key].value.trim();
            return patch(`/api/projects/${projectId}`, body);
          },
        },
      ],
    });
    if (await modal.result) {
      app.toast('Details saved.', { kind: 'success' });
      await load();
    }
  }

  async function regenerate() {
    let meta;
    try {
      meta = await app.meta();
    } catch (err) {
      app.reportError(err);
      return;
    }
    const user = app.user();
    // Start from the lecture's stored options (its AI engine, language, voice, ...), not the server
    // defaults: the form always posts llm_provider, so defaults would silently switch the engine.
    const detail = /** @type {import('../types.js').ProjectDetail | null} */ (data);
    const form = createOptionsForm(meta, {
      isAdmin: !!user && user.role === 'admin',
      includeMeta: true,
      initial: (detail && detail.options) || undefined,
    });
    const reviewSource = checkbox({
      label: 'Let me check how Aadhi read my document before planning',
      hint: 'Generation pauses after reading so you can see and adjust what is taught.',
    });
    const modal = openModal({
      title: 'Regenerate lecture',
      description: 'Creates a new version from the latest source document. Existing versions are kept.',
      body: h('div', {}, reviewSource, form.el),
      size: 'xl',
      actions: [
        { label: 'Cancel', kind: 'outline', value: null },
        {
          label: 'Regenerate',
          kind: 'gold',
          onClick: async () => {
            const { options, errors } = form.build();
            if (Object.keys(errors).length) throw new Error('Please fix the highlighted options.');
            /** @type {Record<string, any>} */
            const body = { options };
            if (reviewSource.input.checked) body.review_source = true;
            return post(`/api/projects/${projectId}/regenerate`, body);
          },
        },
      ],
    });
    const res = await modal.result;
    if (!res || !res.job) return;
    await load();
    if (reviewSource.input.checked) {
      // The "In progress" panel follows the job and links to the source report when it pauses.
      app.toast('Aadhi is reading the document; check it on this page when it pauses.', { kind: 'info' });
      return;
    }
    void jobModal('Generating a new version', res.job, {
      reviewHref: href('planReview', { id: projectId, vid: res.version.id }),
      onSuccess: () => app.navigate(href('editor', { id: projectId, vid: res.version.id })),
      onFinished: () => void load(),
    });
  }

  async function deleteProject() {
    const p = data.project;
    const ok = await confirmDialog({
      title: `Delete “${p.title}”?`,
      message: 'The project disappears from your list, running jobs are cancelled and all share links stop working.',
      details: ['Students will no longer be able to watch it.', 'An administrator can restore it from the database if needed.'],
      confirmLabel: 'Delete project',
      danger: true,
    });
    if (!ok) return;
    try {
      await del(`/api/projects/${projectId}`);
      app.toast('Project deleted.', { kind: 'success' });
      app.navigate('#/projects');
    } catch (err) {
      app.reportError(err, 'Could not delete the project.');
    }
  }

  void load();
  return {
    destroy() {
      destroyed = true;
      dispose();
    },
  };
}
