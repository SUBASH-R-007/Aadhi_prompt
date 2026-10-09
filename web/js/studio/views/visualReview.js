// @ts-check
/**
 * Visual review (#/v/:vid/review): the picture or video of every scene in one list, checked before the
 * lecture is rendered (GET /api/versions/{vid}/visual-review). Each card shows a preview (a picture, a
 * video poster that plays on click, or what a built-in panel draws), where the visual came from (an AI
 * picture or video with its provider and model, the teacher's library or upload, a figure of the source
 * document, an animation, a built-in panel or a backup still), its status in plain words, the review
 * state, the prompt (folded) and the actions the server offers for it (`SceneVisual.actions`):
 *   Approve / Undo approval   PUT /visual-review/{scene_id}: a note for the teacher and the team; nothing
 *                             waits for it and it never changes what is built. An approval carries the
 *                             revision the list was read at (a 409 reloads the list: never a sign-off of a
 *                             visual request nobody looked at)
 *   New AI version            POST /scenes/{scene_id}/visual `new_version` (a new, paid generation; confirmed)
 *   Choose from library       the library picker (components/libraryPicker.js), then `choose_library`
 *                             (the server attaches the item to the lecture)
 *   Upload replacement        POST /api/library (the file joins the teacher's library; visible progress, a
 *                             leave guard, stopped when the page is left), then `choose_library`
 *   Remove visual             `remove` (confirmed)
 *   Try again / Build this scene   `retry` (a missing or out-of-date visual builds its scene); an AI video
 *                             that may already have been paid for is offered only "Generate again…", which
 *                             sends `confirm_paid` after its own dialog (also asked when the server answers
 *                             409 confirm_paid_required)
 * Screenplay changes carry the version's revision: a 409 `revision_conflict` reloads the list with a toast,
 * and a 409 because a job is busy with the version follows that job. Actions that enqueue a build show its
 * progress (jobProgress) and reload the list when it ends. A scene with a library match offers "N matches in
 * your library", which opens the picker with those matches first (GET /api/library/suggestions). Listing and
 * counts follow the server's summary: scenes with a visual (or a removed one), and "needs attention" = not
 * ready, or changed after the decision ("Approved before a change" for an approval).
 *
 * Opening the page never makes anything, and it never touches the editor's local draft: a notice says the
 * editor will offer to merge it. All text goes through textContent (the shared `h` helper).
 */

import { h, clear, append } from '../../shared/dom.js';
import { get, put, post, ApiError } from '../../shared/api.js';
import { button, linkButton, spinner, errorState, emptyState } from '../components/form.js';
import { confirmDialog } from '../components/modal.js';
import { jobProgress } from '../components/jobProgress.js';
import { icon } from '../components/icons.js';
import { captureFocus, restoreFocus } from '../components/focusKeep.js';
import { openLibraryPicker, uploadToLibrary, validateLibraryFile, acceptedExtensions } from '../components/libraryPicker.js';
import { loadDraft } from '../lib/draftStore.js';
import { isActive, statusLabel } from '../lib/jobStages.js';
import { deepEqual } from '../util.js';
import { href } from '../router.js';
import { errorMessage } from '../errors.js';
import { pageHeader, breadcrumbs, EDITABLE_STATUSES } from './common.js';

/** @typedef {import('../types.js').SceneVisual} SceneVisual */

/** Review states in words (a stale decision is shown with a note of its own). */
export const REVIEW_LABELS = Object.freeze(/** @type {Record<string, string>} */ ({
  pending: 'Not reviewed yet',
  approved: 'Approved',
  changed: 'Changed by you',
  removed: 'Removed by you',
}));
const REVIEW_ICONS = /** @type {Record<string, string>} */ ({ pending: 'info', approved: 'check', changed: 'wand', removed: 'close' });
const REVIEW_TONE = /** @type {Record<string, string>} */ ({ pending: 'muted', approved: 'ok', changed: 'busy', removed: 'muted' });

/** Visual statuses in words. */
export const STATUS_LABELS = Object.freeze(/** @type {Record<string, string>} */ ({
  ready: 'Ready',
  missing: 'Not made yet',
  failed: 'Could not be made',
  fallback: 'Showing a backup',
  ambiguous: 'Waiting for your decision',
  stale: 'Out of date',
}));
const STATUS_TONE = /** @type {Record<string, string>} */ ({ ready: 'ok', missing: 'attention', failed: 'bad', fallback: 'attention', ambiguous: 'attention', stale: 'attention' });
const STATUS_ICONS = /** @type {Record<string, string>} */ ({ ready: 'check', missing: 'info', failed: 'error', fallback: 'warning', ambiguous: 'warning', stale: 'refresh' });

/** Why a visual is not ready, when the server gives no reason of its own. */
export const STATUS_REASONS = Object.freeze(/** @type {Record<string, string>} */ ({
  missing: 'This visual has not been made yet. Build the lecture to make it.',
  failed: 'Making this visual failed.',
  fallback: 'The planned visual could not be used, so the scene shows a backup instead.',
  ambiguous: 'An earlier try at this AI video may already have been paid for, so Aadhi did not make it again on its own.',
  stale: 'The scene changed after this visual was made. Build the lecture to update it.',
}));

/** What a built-in panel draws, in words (it has no picture to preview). */
const BUILTIN_LABELS = /** @type {Record<string, string>} */ ({
  chart: 'Built-in chart',
  graph: 'Built-in graph',
  model_3d: 'Built-in 3D model',
  terminal: 'Built-in terminal animation',
  interactive: 'Interactive sketch',
});
const PLACEHOLDERS = /** @type {Record<string, string>} */ ({
  chart: 'A chart the player draws from the scene',
  graph: 'A graph the player draws from the scene',
  model_3d: 'A 3D model the player shows',
  terminal: 'A terminal animation the player types out',
  interactive: 'An interactive sketch (videos show its poster)',
  manim: 'An animation',
  none: 'No visual',
});
const KIND_ICONS = /** @type {Record<string, string>} */ ({ image: 'eye', video: 'video', figure: 'file', manim: 'sim', chart: 'chart', graph: 'chart', model_3d: 'star', terminal: 'code', interactive: 'sim', none: 'close' });
const SOURCE_ICONS = /** @type {Record<string, string>} */ ({ generated: 'wand', library: 'board', upload: 'upload', figure: 'file', manim: 'sim', builtin: 'chart', fallback: 'warning', none: 'close' });
const SEVERITY_WORDS = /** @type {Record<string, string>} */ ({ error: 'Needs fixing', warning: 'Please check', info: 'Note' });

/** Card buttons, in this order. */
export const ACTION_ORDER = Object.freeze(['approve', 'retry', 'confirm_paid_retry', 'new_version', 'choose_library', 'upload', 'remove']);
/** Jobs that change the version (one runs at a time; the page follows it). */
const MUTATING_KINDS = new Set(['generate_lecture', 'regenerate_scene', 'build_assets', 'translate']);

/**
 * Where the visual came from, in words.
 * @param {SceneVisual} sv
 */
export function sourceLabel(sv) {
  switch (sv && sv.source) {
    case 'generated':
      return sv.kind === 'video' ? 'AI video' : 'AI picture';
    case 'library':
      return 'From your library';
    case 'upload':
      return 'Your upload';
    case 'figure':
      return 'Figure from your document';
    case 'manim':
      return 'Animation';
    case 'builtin':
      return BUILTIN_LABELS[sv.kind] || 'Built-in visual';
    case 'fallback':
      return 'Backup still';
    default:
      return 'No visual';
  }
}

/**
 * "Made by <provider> (<model>)" for generated media, or null.
 * @param {SceneVisual} sv
 */
export function provenanceText(sv) {
  if (!sv || !sv.provider) return null;
  return sv.model ? `Made by ${sv.provider} (${sv.model})` : `Made by ${sv.provider}`;
}

/**
 * The review state (unknown values count as not reviewed).
 * @param {SceneVisual} sv
 * @returns {'pending' | 'approved' | 'changed' | 'removed'}
 */
export function reviewState(sv) {
  const s = sv && sv.review ? sv.review.state : null;
  return s === 'approved' || s === 'changed' || s === 'removed' ? s : 'pending';
}

/** Approved, and the scene has not changed since. @param {SceneVisual} sv */
export function isApproved(sv) {
  return reviewState(sv) === 'approved' && !(sv.review && sv.review.stale);
}

/**
 * The teacher approved this visual, then the scene changed (the server then sends the state "pending" with
 * `stale` true; "approved" with `stale` is accepted too).
 * @param {SceneVisual} sv
 */
export function approvedBeforeChange(sv) {
  const stale = !!(sv && sv.review && sv.review.stale);
  const state = reviewState(sv);
  return stale && (state === 'pending' || state === 'approved');
}

/**
 * Needs a look: the visual is not ready, or the scene changed after the teacher's decision (as the server
 * counts `summary.needs_attention`). A scene skipped in the video (`hidden`) never needs attention.
 * @param {SceneVisual} sv
 */
export function needsAttention(sv) {
  return !!sv && sv.hidden !== true && ((!!sv.status && sv.status !== 'ready') || !!(sv.review && sv.review.stale));
}

/**
 * Listed on the page: a scene with a visual, or whose visual the teacher removed (as the server counts
 * `summary.total`). Scenes that never had a visual are counted in a note instead.
 * @param {SceneVisual} sv
 */
export function isListed(sv) {
  return !!sv && (sv.kind !== 'none' || reviewState(sv) === 'removed');
}

/** The page's filters. */
export const FILTERS = Object.freeze(/** @type {Record<string, { label: string, empty: string, test: (sv: SceneVisual) => boolean }>} */ ({
  all: { label: 'All', empty: 'No scene of this lecture has a visual yet.', test: () => true },
  attention: { label: 'Needs attention', empty: 'Nothing needs your attention.', test: needsAttention },
  not_approved: { label: 'Not approved', empty: 'Every visual is approved.', test: (sv) => !isApproved(sv) },
}));

/**
 * Counts for the header (listed scenes only).
 * @param {SceneVisual[]} scenes
 */
export function summarize(scenes) {
  const out = { total: 0, approved: 0, not_approved: 0, needs_attention: 0 };
  for (const sv of scenes) {
    if (!isListed(sv)) continue;
    out.total += 1;
    if (isApproved(sv)) out.approved += 1;
    else out.not_approved += 1;
    if (needsAttention(sv)) out.needs_attention += 1;
  }
  return out;
}

/** @param {{ total: number, approved: number, not_approved: number, needs_attention: number }} s */
export function summaryText(s) {
  if (!s.total) return 'No scene of this lecture has a visual to review.';
  return `${plural(s.total, 'visual')}: ${s.approved} approved, ${s.not_approved} not approved, ${s.needs_attention} need${s.needs_attention === 1 ? 's' : ''} attention.`;
}

/**
 * The library kind a scene's visual can use (the picker's filter), or undefined for pictures and videos.
 * The server says which kinds the scene accepts (`accepts`); without it: an interactive sketch's still is a
 * picture, an animation is replaced by a video, any other scene that offers the choice takes either (the
 * server checks the item's kind again).
 * @param {SceneVisual} sv
 * @returns {'image' | 'video' | undefined}
 */
export function pickerKind(sv) {
  if (!sv) return undefined;
  if (Array.isArray(sv.accepts) && sv.accepts.length === 1) return sv.accepts[0] === 'image' ? 'image' : 'video';
  if (Array.isArray(sv.accepts) && sv.accepts.length > 1) return undefined;
  if (sv.kind === 'interactive') return 'image';
  if (sv.kind === 'manim') return 'video';
  return undefined;
}

/**
 * The reason the server gave for refusing a request, without a field name in front ("" when none).
 * @param {unknown} err
 */
function refusalReason(err) {
  if (!(err instanceof ApiError)) return '';
  const detail = err.detail;
  if (Array.isArray(detail)) {
    const first = detail[0];
    return typeof first === 'string' ? first : first && typeof first.msg === 'string' ? first.msg : '';
  }
  return typeof detail === 'string' ? detail : '';
}

/** File extensions a replacement upload may have. @param {SceneVisual} sv */
export function uploadAccept(sv) {
  return acceptedExtensions(pickerKind(sv));
}

/**
 * Client-side check of a replacement upload (the library's own check; the server checks again).
 * @param {{ name: string, size: number, type?: string }} file
 * @param {SceneVisual} sv
 * @param {number} maxMb
 * @returns {string | null}
 */
export function checkUpload(file, sv, maxMb) {
  return validateLibraryFile(file, maxMb, pickerKind(sv));
}

/**
 * Ask before generating an AI video that may already have been paid for.
 * @param {string} where  e.g. "Scene 3"
 * @returns {Promise<boolean>}
 */
export function confirmPaidRetry(where) {
  return confirmDialog({
    title: 'Generate this AI video again?',
    message: `${where}: an earlier try stopped before Aadhi heard back from the video service, so it may already have been paid for.`,
    details: ['Generating it again can mean paying for this video twice.', 'If you cancel, the scene keeps showing its still picture.'],
    confirmLabel: 'Generate again (may be paid twice)',
    danger: true,
  });
}

/** @param {number} n @param {string} one @param {string} [many] */
function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many || `${one}s`}`;
}

/**
 * @param {string} tone
 * @param {string} iconName
 * @param {string} text
 * @param {Record<string, string>} [dataset]
 */
function chip(tone, iconName, text, dataset) {
  return h('span', { class: ['badge', `badge-${tone}`], dataset }, icon(iconName, { size: 12 }), text);
}

/** @type {import('../types.js').ViewMount} */
export function mount(container, args) {
  return mountVisualReview(container, args);
}

/**
 * The page, with the library picker passed in (the Library page's picker by default).
 * @param {HTMLElement} container
 * @param {import('../types.js').ViewArgs} args
 * @param {{ pickLibraryItem?: (opts: { app: import('../types.js').AppContext, kind?: 'image' | 'video', projectId?: number, suggested?: import('../types.js').LibraryItem[] }) => Promise<any> }} [deps]
 */
export async function mountVisualReview(container, { app, params, query, signal }, deps = {}) {
  const vid = params.vid;
  const pick = deps.pickLibraryItem || openLibraryPicker;
  const self = href('visualReview', { vid });
  const left = () => !!(signal && signal.aborted);
  const body = h('div', { class: 'visual-review' }, spinner('Loading the visuals…'));
  container.appendChild(body);

  /** Started with the version request; its failure is reported only for a version that has a screenplay. */
  const reviewRequest = get(`/api/versions/${vid}/visual-review`).then(
    (/** @type {any} */ value) => ({ value, error: /** @type {unknown} */ (null) }),
    (/** @type {unknown} */ error) => ({ value: null, error: error || new Error('visual review failed') }),
  );
  /** @type {any} */
  let version;
  /** @type {any} */
  let meta = null;
  try {
    [version, meta] = await Promise.all([get(`/api/versions/${vid}`), app.meta().catch(() => null)]);
  } catch (err) {
    if (left()) return;
    clear(body);
    body.appendChild(errorState(errorMessage(err, 'Could not load this lecture.'), () => app.navigate(self, { replace: true })));
    app.reportError(err);
    return;
  }
  const first = await reviewRequest;
  if (left()) return;
  const projectId = version.project_id;
  app.setTitle(`${(version.screenplay && version.screenplay.session_title) || 'Lecture'} · Visual review`);
  clear(body);
  if (!version.screenplay) {
    body.appendChild(
      emptyState(
        'There is nothing to review yet',
        version.status === 'generating' ? 'Aadhi is still writing this lecture. Follow the progress on the project page.' : 'This version has no screenplay.',
        linkButton('Project page', href('project', { id: projectId }), { kind: 'outline' }),
      ),
    );
    return;
  }
  if (first.error) {
    body.appendChild(errorState(errorMessage(first.error, 'Could not load the visuals of this lecture.'), () => app.navigate(self, { replace: true })));
    app.reportError(first.error);
    return;
  }

  /** The screenplay revision the shown scenes were read at (the review's own, else the version's). */
  let revision = Number.isInteger(first.value && first.value.revision) ? first.value.revision : version.revision;
  /** @type {SceneVisual[]} */
  let scenes = Array.isArray(first.value && first.value.scenes) ? first.value.scenes : [];
  let filter = Object.prototype.hasOwnProperty.call(FILTERS, query.filter) ? query.filter : 'all';
  /** Scenes with an action in flight (their buttons are disabled). */
  const busy = new Set();
  /** Replacement uploads in flight, by scene (stopped when the page is left). @type {Map<string, AbortController>} */
  const uploads = new Map();
  /** The visible upload status of each scene (kept when its card is drawn again). @type {Map<string, string>} */
  const uploadText = new Map();
  let destroyed = false;
  /** @type {{ destroy: () => void } | null} */
  let jobWidget = null;
  let building = false;
  const maxMb = (meta && meta.limits && meta.limits.upload_max_mb) || 50;
  const me = app.user();
  const localDraft = me ? loadDraft(me.id, vid) : null;
  const hasDraft = !!(localDraft && localDraft.screenplay && !deepEqual(localDraft.screenplay, version.screenplay));
  /** List item of each shown scene (cards are replaced in place after an action). @type {Map<string, HTMLElement>} */
  const cards = new Map();

  const root = h('div', {});
  const live = h('div', { class: 'sr-only', 'aria-live': 'polite', 'aria-atomic': 'true' });
  const staleHost = h('div', { class: 'vr-stale-host' });
  const jobHost = h('section', { class: 'panel glass vr-job', 'aria-label': 'In progress', hidden: true });
  const summaryEl = h('p', { class: 'vr-summary' });
  const filtersEl = h('div', { class: 'vr-filters row gap wrap', role: 'group', 'aria-label': 'Show' });
  const list = h('ul', { class: 'vr-list', 'aria-label': 'Scene visuals' });
  const hiddenNote = h('p', { class: 'muted small vr-hidden-note', hidden: true });
  body.appendChild(root);

  const readOnly = () => !EDITABLE_STATUSES.has(version.status);
  const listed = () => scenes.filter(isListed);
  /** @param {SceneVisual} sv */
  const sceneNo = (sv) => (Number.isInteger(sv.index) ? sv.index + 1 : scenes.indexOf(sv) + 1);
  /** @param {SceneVisual} sv */
  const where = (sv) => `Scene ${sceneNo(sv)}`;
  /** @param {string} text */
  const announce = (text) => {
    live.textContent = text;
  };

  function render() {
    const snap = captureFocus(root);
    clear(root);
    const sp = version.screenplay || {};
    append(root, [
      breadcrumbs([{ label: 'Projects', hash: '#/projects' }, { label: 'Project', hash: href('project', { id: projectId }) }, { label: 'Visual review' }]),
      pageHeader(
        'Visual review',
        `${sp.session_title || 'Untitled lecture'} · version ${version.number ?? vid}`,
        linkButton('Open editor', href('editor', { id: projectId, vid }), { kind: 'outline', icon: 'board' }),
        version.has_timeline ? linkButton('Watch', `/preview/${vid}`, { kind: 'ghost', icon: 'play', newTab: true }) : null,
      ),
      h('p', { class: 'muted vr-intro' }, 'Check the picture or video of every scene before you render. Approving is a note for you and your team: rendering never waits for it, and nothing is made until you choose an action.'),
      hasDraft ? draftNotice() : null,
      readOnly() ? h('p', { class: 'notice warning' }, `This version is ${statusLabel(version.status).toLowerCase()}. You can look at its visuals, but change them only when it is ready.`) : null,
      staleHost,
      jobHost,
      summaryEl,
      filtersEl,
      h('h2', { class: 'sr-only' }, 'Scenes'),
      list,
      hiddenNote,
      live,
    ]);
    drawStale();
    drawSummary();
    drawFilters();
    drawList();
    restoreFocus(root, snap);
  }

  function draftNotice() {
    return h(
      'div',
      { class: 'notice warning vr-draft', dataset: { draft: 'local' } },
      h('strong', {}, 'You have unsaved editor changes to this lecture in this browser. '),
      'They are kept: changes made on this page are saved straight away, and the editor offers to merge your unsaved changes when you open it. ',
      h('a', { href: href('editor', { id: projectId, vid }) }, 'Open the editor'),
    );
  }

  /** "Needs a build" notice while scenes changed after their visual was made. */
  function drawStale() {
    clear(staleHost);
    if (readOnly()) return;
    const stale = scenes.filter((sv) => sv.status === 'stale').length;
    const unbuilt = !version.has_timeline || !!version.timeline_stale;
    if (!stale && !unbuilt) return;
    const build = button('Build changed scenes', { kind: 'gold', small: true, icon: 'build', disabled: building, dataset: { fk: 'build' }, title: 'Makes voice and visuals for the scenes that changed since the last build' });
    build.addEventListener('click', () => void buildChanged());
    staleHost.appendChild(
      h(
        'div',
        { class: 'notice warning row gap wrap vr-stale', dataset: { stale: String(stale) } },
        h(
          'span',
          { class: 'spacer' },
          !version.has_timeline ? 'The lecture is not built yet. ' : stale ? `${plural(stale, 'scene')} changed after ${stale === 1 ? 'its' : 'their'} visual was made. ` : 'The lecture has changes that are not built yet. ',
          'Build to make the missing and changed visuals; scenes that did not change are not made again.',
        ),
        build,
      ),
    );
  }

  function drawSummary() {
    const s = summarize(scenes);
    summaryEl.textContent = summaryText(s);
    summaryEl.dataset.approved = String(s.approved);
    summaryEl.dataset.attention = String(s.needs_attention);
  }

  function drawFilters() {
    clear(filtersEl);
    const shown = listed();
    for (const [key, f] of Object.entries(FILTERS)) {
      const n = shown.filter(f.test).length;
      const b = button(`${f.label} (${n})`, { kind: key === filter ? 'primary' : 'ghost', small: true, ariaPressed: key === filter, dataset: { fk: `filter:${key}`, filter: key } });
      b.addEventListener('click', () => setFilter(key));
      filtersEl.appendChild(b);
    }
  }

  function drawList() {
    clear(list);
    cards.clear();
    const all = listed();
    const shown = all.filter(FILTERS[filter].test);
    const hidden = scenes.length - all.length;
    hiddenNote.textContent = hidden ? `${plural(hidden, 'scene')} without a visual ${hidden === 1 ? 'is' : 'are'} not listed.` : '';
    hiddenNote.hidden = !hidden;
    if (!shown.length) {
      list.appendChild(h('li', { class: 'vr-empty muted' }, FILTERS[filter].empty));
      return;
    }
    for (const sv of shown) {
      const li = h('li', { class: 'vr-item' }, card(sv));
      cards.set(sv.scene_id, li);
      list.appendChild(li);
    }
  }

  /** @param {string} key */
  function setFilter(key) {
    if (!FILTERS[key] || key === filter) return;
    filter = key;
    app.replaceHash(href('visualReview', { vid }, { filter: key === 'all' ? undefined : key }));
    const snap = captureFocus(root);
    drawFilters();
    drawList();
    restoreFocus(root, snap);
  }

  /**
   * Show a newer state of one scene: its card is replaced in place (it stays listed until the filter or
   * the page is redrawn, so focus never jumps), the counts follow.
   * @param {SceneVisual} next
   */
  function replaceScene(next) {
    const i = scenes.findIndex((s) => s.scene_id === next.scene_id);
    scenes = i < 0 ? [...scenes, next] : scenes.map((s, j) => (j === i ? next : s));
    const li = cards.get(next.scene_id);
    const snap = captureFocus(root);
    if (li) {
      clear(li);
      li.appendChild(card(next));
    }
    drawStale();
    drawSummary();
    drawFilters();
    restoreFocus(root, snap);
    if (li && !root.contains(document.activeElement)) focusScene(next.scene_id, false);
  }

  /**
   * @param {string} sceneId
   * @param {boolean} on
   */
  function setBusy(sceneId, on) {
    if (on) busy.add(sceneId);
    else busy.delete(sceneId);
    const li = cards.get(sceneId);
    if (!li) return;
    for (const b of li.querySelectorAll('button[data-action]')) /** @type {HTMLButtonElement} */ (b).disabled = on;
    li.querySelector('.vr-card')?.setAttribute('aria-busy', on ? 'true' : 'false');
  }

  /**
   * Move focus to a scene's card heading.
   * @param {string} sceneId
   * @param {boolean} [scroll]
   */
  function focusScene(sceneId, scroll = true) {
    const article = [...list.querySelectorAll('article[data-scene]')].find((a) => /** @type {HTMLElement} */ (a).dataset.scene === sceneId);
    const heading = /** @type {HTMLElement | null} */ (article ? article.querySelector('.vr-card-title') : null);
    if (!heading) return;
    if (scroll && typeof heading.scrollIntoView === 'function') heading.scrollIntoView({ block: 'center' });
    heading.focus({ preventScroll: true });
  }

  /**
   * Show (or clear, with null) a scene's visible upload status.
   * @param {string} sceneId
   * @param {string | null} text
   */
  function showUpload(sceneId, text) {
    if (text == null) uploadText.delete(sceneId);
    else uploadText.set(sceneId, text);
    const el = /** @type {HTMLElement | null | undefined} */ (cards.get(sceneId)?.querySelector('.vr-upload-status'));
    if (!el) return;
    el.textContent = text || '';
    el.hidden = text == null;
  }

  /** While a replacement uploads, leaving the page asks first (and stops the upload). */
  function syncGuard() {
    if (uploads.size) {
      container.dataset.dirty = 'true'; // the browser asks before closing the tab
      app.setLeaveGuard(() =>
        confirmDialog({
          title: 'Stop uploading?',
          message: 'Your file is still uploading. If you leave now, it is not added to your library and the scene keeps its current visual.',
          confirmLabel: 'Leave the page',
          cancelLabel: 'Stay',
          danger: true,
        }),
      );
    } else {
      delete container.dataset.dirty;
      app.setLeaveGuard(null);
    }
  }

  // --- a card ---------------------------------------------------------------------------------
  /** @param {SceneVisual} sv */
  function card(sv) {
    const n = sceneNo(sv);
    const label = `Scene ${n}${sv.title ? `: ${sv.title}` : ''}`;
    const state = reviewState(sv);
    // Any decision made before the scene changed (an approval then reads "pending" with `stale`).
    const stale = !!(sv.review && sv.review.stale);
    const reviewLabel = approvedBeforeChange(sv) ? 'Approved before a change' : REVIEW_LABELS[state];
    const status = STATUS_LABELS[sv.status] ? sv.status : 'ready';
    const reason = status === 'ready' ? '' : sv.status_reason || STATUS_REASONS[status] || '';
    const provenance = provenanceText(sv);
    const actions = /** @type {string[]} */ (readOnly() || !Array.isArray(sv.actions) ? [] : sv.actions);
    const findings = Array.isArray(sv.findings) ? sv.findings : [];
    const matches = Number(sv.library_suggestions) || 0;
    const fileInput = actions.includes('upload')
      ? h('input', { type: 'file', class: 'sr-only', tabindex: '-1', 'aria-hidden': 'true', accept: uploadAccept(sv).join(','), dataset: { upload: sv.scene_id } })
      : null;
    if (fileInput) fileInput.addEventListener('change', () => void uploadReplacement(sv, /** @type {HTMLInputElement} */ (fileInput)));
    // A possibly paid video is made again only through its own confirmation, never a plain retry.
    const offered = ACTION_ORDER.filter((a) => actions.includes(a) && !(a === 'retry' && actions.includes('confirm_paid_retry')));
    const buttons = offered.map((a) => actionButton(sv, a, fileInput));
    if (matches > 0 && actions.includes('choose_library')) {
      const link = button(`${plural(matches, 'match', 'matches')} in your library`, { kind: 'ghost', small: true, icon: 'search', disabled: busy.has(sv.scene_id), dataset: { fk: `matches:${sv.scene_id}`, action: 'matches' } });
      link.classList.add('vr-matches');
      link.addEventListener('click', () => void chooseMatches(sv));
      buttons.push(link);
    }
    const upload = uploadText.get(sv.scene_id);
    // Visible progress; screen readers hear the start and the end through the page's live region instead of
    // every percentage.
    const uploadStatus = h('p', { class: 'vr-upload-status muted small', hidden: upload == null }, upload || '');
    return h(
      'article',
      { class: ['vr-card', needsAttention(sv) ? 'needs-attention' : ''], 'aria-label': label, 'aria-busy': busy.has(sv.scene_id) ? 'true' : 'false', dataset: { scene: sv.scene_id, status, state } },
      h(
        'header',
        { class: 'vr-card-head' },
        h('h3', { class: 'vr-card-title', tabindex: '-1', dataset: { fk: `scene:${sv.scene_id}` } }, h('span', { class: 'vr-scene-no' }, `Scene ${n}`), sv.title ? h('span', { class: 'vr-scene-title' }, sv.title) : null),
        h(
          'div',
          { class: 'badges' },
          chip(stale ? 'attention' : REVIEW_TONE[state], stale ? 'warning' : REVIEW_ICONS[state], reviewLabel, { review: state }),
          chip(STATUS_TONE[status], STATUS_ICONS[status], STATUS_LABELS[status], { status }),
          sv.hidden === true ? chip('outline', 'eye', 'Skipped in the video', { hidden: 'true' }) : null,
        ),
      ),
      sv.hidden === true ? h('p', { class: 'small muted vr-skipped-note' }, 'This scene is skipped in the video. Show it again in the editor to build its visual.') : null,
      preview(sv, label),
      h(
        'div',
        { class: 'vr-meta' },
        h('p', { class: 'vr-source row gap wrap' }, chip('outline', SOURCE_ICONS[sv.source] || 'board', sourceLabel(sv), { source: String(sv.source || 'none') }), provenance ? h('span', { class: 'muted small vr-provenance' }, provenance) : null),
        reason ? h('p', { class: ['small', 'vr-reason', status === 'failed' ? 'error-text' : ''] }, reason) : null,
        stale ? h('p', { class: 'small vr-review-stale' }, icon('warning', { size: 12 }), ' The scene changed after this decision. Look at it again.') : null,
        sv.review && sv.review.note ? h('p', { class: 'small vr-note' }, `Note: ${sv.review.note}`) : null,
        findings.length
          ? h(
              'ul',
              { class: 'plain-list small vr-findings' },
              findings.map((/** @type {any} */ f) => h('li', { dataset: { severity: f.severity || 'info' } }, icon(f.severity === 'error' ? 'error' : f.severity === 'warning' ? 'warning' : 'info', { size: 12 }), h('span', {}, h('span', { class: 'sr-only' }, `${SEVERITY_WORDS[f.severity] || 'Note'}: `), f.message || ''))),
            )
          : null,
        sv.prompt ? h('details', { class: 'vr-prompt' }, h('summary', {}, 'Prompt'), h('p', { class: 'small' }, sv.prompt)) : null,
      ),
      buttons.length ? h('div', { class: 'vr-actions row gap wrap' }, buttons, fileInput) : null,
      fileInput ? uploadStatus : null,
    );
  }

  /**
   * @param {SceneVisual} sv
   * @param {string} label
   */
  function preview(sv, label) {
    const kind = sv.kind;
    // A backup still is a picture, whatever the scene asked for.
    if (sv.url && (kind === 'video' || kind === 'manim') && sv.source !== 'fallback') return videoPreview(sv, label);
    const still = sv.url && (kind === 'image' || kind === 'figure' || kind === 'interactive' || sv.source === 'fallback') ? sv.url : sv.poster_url;
    if (still) return h('div', { class: 'vr-preview', dataset: { kind: String(kind) } }, h('img', { src: still, alt: `Visual of ${label}`, loading: 'lazy', decoding: 'async' }));
    const text = sv.status === 'missing' || sv.status === 'failed' ? STATUS_LABELS[sv.status] : PLACEHOLDERS[kind] || BUILTIN_LABELS[kind] || 'No preview';
    return h('div', { class: 'vr-preview vr-placeholder', dataset: { kind: String(kind) } }, icon(KIND_ICONS[kind] || 'board', { size: 28 }), h('span', {}, text));
  }

  /**
   * A poster with a play button; the video loads only when asked.
   * @param {SceneVisual} sv
   * @param {string} label
   */
  function videoPreview(sv, label) {
    const box = h('div', { class: 'vr-preview vr-video', dataset: { kind: String(sv.kind) } });
    const play = h(
      'button',
      { type: 'button', class: 'vr-play', 'aria-label': `Play the video of ${label}`, dataset: { fk: `play:${sv.scene_id}` } },
      sv.poster_url ? h('img', { src: sv.poster_url, alt: '' }) : null,
      h('span', { class: 'vr-play-badge' }, icon('play'), h('span', {}, 'Play')),
    );
    play.addEventListener('click', () => {
      const video = /** @type {HTMLVideoElement} */ (
        h('video', { src: sv.url, poster: sv.poster_url || undefined, controls: true, autoplay: true, muted: true, playsinline: true, preload: 'metadata', 'aria-label': `Video of ${label}` })
      );
      video.muted = true;
      // Media the browser cannot play as a video (an uploaded picture standing in for a clip) is shown as one.
      video.addEventListener('error', () => {
        clear(box);
        box.appendChild(h('img', { src: sv.url, alt: `Visual of ${label}` }));
      });
      clear(box);
      box.appendChild(video);
      video.focus();
    });
    box.appendChild(play);
    return box;
  }

  /**
   * @param {SceneVisual} sv
   * @param {string} action
   * @param {HTMLInputElement | null} fileInput
   */
  function actionButton(sv, action, fileInput) {
    const approved = isApproved(sv);
    /** @type {Record<string, [string, import('../components/form.js').ButtonKind, string, string?]>} */
    const specs = {
      approve: approved ? ['Undo approval', 'ghost', 'undo'] : ['Approve', 'gold', 'check'],
      retry: sv.status === 'stale' || sv.status === 'missing' ? ['Build this scene', 'outline', 'build', 'Makes this scene’s visual now (generated media counts toward your budget)'] : ['Try again', 'outline', 'retry', 'Makes this visual again (generated media counts toward your budget)'],
      confirm_paid_retry: ['Generate again…', 'outline', 'retry', 'The earlier try may already have been paid for'],
      new_version: ['New AI version', 'outline', 'wand', 'A new AI generation from the same description (counts toward your budget)'],
      choose_library: ['Choose from library', 'outline', 'search'],
      upload: ['Upload replacement', 'outline', 'upload'],
      remove: ['Remove visual', 'ghost', 'trash'],
    };
    const [text, kind, iconName, title] = specs[action];
    const b = button(text, { kind, small: true, icon: iconName, title: title || undefined, disabled: busy.has(sv.scene_id), dataset: { fk: `${action}:${sv.scene_id}`, action } });
    b.addEventListener('click', () => {
      if (action === 'approve') void setReview(sv, approved ? 'pending' : 'approved');
      else if (action === 'retry') void retry(sv, false);
      else if (action === 'confirm_paid_retry') void retry(sv, true);
      else if (action === 'new_version') void newVersion(sv);
      else if (action === 'choose_library') void chooseFromLibrary(sv);
      else if (action === 'upload' && fileInput) fileInput.click();
      else if (action === 'remove') void removeVisual(sv);
    });
    return b;
  }

  // --- actions --------------------------------------------------------------------------------
  /**
   * @param {SceneVisual} sv
   * @param {'approved' | 'pending'} state
   */
  async function setReview(sv, state) {
    const id = sv.scene_id;
    if (busy.has(id)) return;
    setBusy(id, true);
    try {
      // An approval covers what the teacher saw: the revision the list was read at (refused when it changed).
      const next = await put(`/api/versions/${vid}/visual-review/${encodeURIComponent(id)}`, state === 'approved' ? { state, revision } : { state });
      if (destroyed) return;
      setBusy(id, false);
      replaceScene(next && next.scene_id === id ? next : { ...sv, review: { ...(sv.review || {}), state, stale: false } });
      announce(state === 'approved' ? `${where(sv)}: visual approved.` : `${where(sv)}: approval undone.`);
    } catch (err) {
      if (destroyed) return;
      if (err instanceof ApiError && err.status === 409 && err.code === 'revision_conflict') {
        app.toast('This lecture was changed somewhere else, so the list was reloaded. Please look at the visual again.', { kind: 'warning' });
        setBusy(id, false);
        await reload(id);
        return;
      }
      app.reportError(err, 'Could not save the review.');
    } finally {
      if (!destroyed) setBusy(id, false);
    }
  }

  /**
   * POST /scenes/{scene_id}/visual with the current revision.
   * @param {SceneVisual} sv
   * @param {Record<string, any>} payload
   * @param {{ done?: string, job?: string, failed?: string, uploaded?: boolean }} texts  `uploaded`: the item is
   *   the teacher's upload of a moment ago (a refusal says that it stayed in the library)
   * @returns {Promise<boolean>}
   */
  async function visualAction(sv, payload, texts) {
    const id = sv.scene_id;
    if (busy.has(id)) return false;
    setBusy(id, true);
    /** @type {any} */
    let res = null;
    /** @type {unknown} */
    let failure = null;
    try {
      res = await post(`/api/versions/${vid}/scenes/${encodeURIComponent(id)}/visual`, { ...payload, revision });
    } catch (err) {
      failure = err;
    } finally {
      if (!destroyed) setBusy(id, false);
    }
    if (destroyed) return false;
    if (failure) return actionFailed(sv, payload, texts, failure);
    if (res && Number.isInteger(res.revision)) revision = res.revision;
    if (res && res.scene && res.scene.scene_id === id) replaceScene(res.scene);
    if (texts.done) announce(texts.done);
    if (res && res.job_id) await followJob(res.job_id, texts.job || `${where(sv)}: updating the visual`, id);
    return true;
  }

  /**
   * @param {SceneVisual} sv
   * @param {Record<string, any>} payload
   * @param {{ done?: string, job?: string, failed?: string, uploaded?: boolean }} texts
   * @param {unknown} err
   * @returns {Promise<boolean>}
   */
  async function actionFailed(sv, payload, texts, err) {
    if (err instanceof ApiError && err.status === 409) {
      if (err.code === 'confirm_paid_required' && !payload.confirm_paid) {
        if (await confirmPaidRetry(where(sv))) return visualAction(sv, { ...payload, confirm_paid: true }, texts);
        return false;
      }
      if (err.code === 'revision_conflict') {
        app.toast('This lecture was changed somewhere else, so the list was reloaded. Please try again.', { kind: 'warning' });
        await reload(sv.scene_id);
        return false;
      }
      if (await followConflictJob(err)) return false;
    }
    if (texts.uploaded && err instanceof ApiError && err.status >= 400 && err.status < 500 && err.status !== 401) {
      // The teacher's file is kept in their library either way: say so, with the server's reason in plain words.
      app.toast(`Your file was added to your library, but it could not be used here. ${refusalReason(err)}`.trim(), { kind: 'error' });
      return false;
    }
    app.reportError(err, texts.failed || 'Could not change the visual.');
    return false;
  }

  /**
   * A 409 because a job is already working on the version: follow that job.
   * @param {unknown} err
   */
  async function followConflictJob(err) {
    if (!(err instanceof ApiError) || (err.code !== 'job_in_progress' && err.code !== 'version_busy')) return false;
    const jobId = err.body && err.body.job_id;
    if (!jobId) return false;
    app.toast(errorMessage(err), { kind: 'warning' });
    await followJob(jobId, 'A job is working on this lecture', null);
    return true;
  }

  /** @param {SceneVisual} sv */
  async function newVersion(sv) {
    const noun = sv.kind === 'video' ? 'video' : 'picture';
    const ok = await confirmDialog({
      title: 'Make a new AI version?',
      message: `Aadhi makes a new AI ${noun} for ${where(sv).toLowerCase()} from the same description, then rebuilds that scene.`,
      details: ['This is a new generation: it costs money and counts toward your budget.', `The current ${noun} is not deleted. If a new version cannot be made, the current ${noun} stays.`],
      confirmLabel: 'Make a new version',
    });
    if (!ok || destroyed) return;
    await visualAction(sv, { action: 'new_version' }, { done: `${where(sv)}: a new version is being made.`, job: `${where(sv)}: making a new AI ${noun}`, failed: 'Could not start a new version.' });
  }

  /**
   * "N matches in your library": the picker opens with the scene's matches first
   * (GET /api/library/suggestions; without them, the plain picker).
   * @param {SceneVisual} sv
   */
  async function chooseMatches(sv) {
    if (busy.has(sv.scene_id)) return;
    /** @type {import('../types.js').LibraryItem[]} */
    let suggested = [];
    try {
      const res = await get(`/api/library/suggestions?version_id=${vid}`);
      const entry = ((res && Array.isArray(res.scenes) && res.scenes) || []).find((/** @type {any} */ s) => s && s.scene_id === sv.scene_id);
      suggested = ((entry && Array.isArray(entry.matches) && entry.matches) || []).map((/** @type {any} */ m) => m && m.item).filter((/** @type {any} */ it) => it && Number.isInteger(it.id));
    } catch {
      /* a limit or an older server: the plain picker still opens */
    }
    if (destroyed) return;
    await chooseFromLibrary(sv, suggested);
  }

  /**
   * @param {SceneVisual} sv
   * @param {import('../types.js').LibraryItem[]} [suggested]  shown first in the picker
   */
  async function chooseFromLibrary(sv, suggested) {
    if (busy.has(sv.scene_id)) return;
    /** @type {any} */
    let item;
    try {
      // No projectId: choose_library attaches the item to the lecture on the server.
      item = await pick(suggested ? { app, kind: pickerKind(sv), suggested } : { app, kind: pickerKind(sv) });
    } catch (err) {
      if (!destroyed) app.reportError(err, 'The library could not be opened.');
      return;
    }
    if (destroyed || !item || !Number.isInteger(item.id)) return;
    const name = item.title ? `“${item.title}”` : 'the item you chose';
    await visualAction(sv, { action: 'choose_library', library_item_id: item.id }, { done: `${where(sv)} now shows ${name} from your library.`, job: `${where(sv)}: using ${name}`, failed: 'Could not use that library item here.' });
  }

  /**
   * @param {SceneVisual} sv
   * @param {HTMLInputElement} fileInput
   */
  async function uploadReplacement(sv, fileInput) {
    const file = fileInput.files && fileInput.files[0];
    try {
      fileInput.value = '';
    } catch {
      /* read-only in some test DOMs */
    }
    if (!file || busy.has(sv.scene_id)) return;
    const problem = checkUpload(file, sv, maxMb);
    if (problem) {
      app.toast(problem, { kind: 'error' });
      return;
    }
    const id = sv.scene_id;
    setBusy(id, true);
    const abort = new AbortController();
    uploads.set(id, abort);
    syncGuard();
    showUpload(id, `Uploading ${file.name}…`);
    announce(`Uploading ${file.name}…`);
    /** @type {any} */
    let item;
    try {
      item = await uploadToLibrary(file, {}, {
        signal: abort.signal,
        onProgress: (f) => {
          if (!destroyed) showUpload(id, `Uploading ${file.name}… ${Math.round(f * 100)}%`);
        },
      });
    } catch (err) {
      if (!destroyed) {
        setBusy(id, false);
        announce('');
        app.reportError(err, 'The upload failed.');
      }
      return;
    } finally {
      uploads.delete(id);
      if (!destroyed) {
        showUpload(id, null);
        syncGuard();
      }
    }
    if (destroyed) return;
    setBusy(id, false);
    if (!item || !Number.isInteger(item.id)) {
      app.toast('The file was uploaded, but it could not be used here.', { kind: 'error' });
      return;
    }
    await visualAction(sv, { action: 'choose_library', library_item_id: item.id }, { done: `${where(sv)} now shows your upload.`, job: `${where(sv)}: using your upload`, failed: 'Your file was added to your library, but it could not be used here.', uploaded: true });
  }

  /** @param {SceneVisual} sv */
  async function removeVisual(sv) {
    const ok = await confirmDialog({
      title: `Remove the visual of ${where(sv).toLowerCase()}?`,
      message: 'The scene will no longer show this picture or video. Nothing is deleted from your library, and you can choose a visual again later.',
      confirmLabel: 'Remove visual',
      danger: true,
    });
    if (!ok || destroyed) return;
    await visualAction(sv, { action: 'remove' }, { done: `${where(sv)}: visual removed.`, job: `${where(sv)}: removing the visual`, failed: 'Could not remove the visual.' });
  }

  /**
   * @param {SceneVisual} sv
   * @param {boolean} paid  the earlier try may already have been paid for (asked first)
   */
  async function retry(sv, paid) {
    if (paid && !(await confirmPaidRetry(where(sv)))) return;
    if (destroyed) return;
    /** @type {Record<string, any>} */
    const payload = { action: 'retry' };
    if (paid) payload.confirm_paid = true;
    await visualAction(sv, payload, { done: `${where(sv)}: trying again.`, job: `${where(sv)}: making the visual again`, failed: 'Could not try again.' });
  }

  async function buildChanged() {
    if (building) return;
    building = true;
    drawStale();
    try {
      const res = await post(`/api/versions/${vid}/build`, { scene_ids: null });
      if (!destroyed && res && res.job) showJob(res.job, 'Building the changed scenes', null);
    } catch (err) {
      if (!destroyed && !(await followConflictJob(err))) app.reportError(err, 'Could not start the build.');
    } finally {
      building = false;
      if (!destroyed) drawStale();
    }
  }

  // --- jobs and reloads -----------------------------------------------------------------------
  /**
   * @param {number} jobId
   * @param {string} label
   * @param {string | null} sceneId
   */
  async function followJob(jobId, label, sceneId) {
    /** @type {any} */
    let job;
    try {
      job = await get(`/api/jobs/${jobId}`);
    } catch (err) {
      if (!destroyed) app.reportError(err, 'Could not follow the progress of the job.');
      return;
    }
    if (!destroyed) showJob(job, label, sceneId);
  }

  /**
   * Show a job's progress above the list; the list is reloaded when it ends (a failed job stays shown).
   * @param {any} job
   * @param {string} label
   * @param {string | null} sceneId  focus returns to this scene after the reload
   */
  function showJob(job, label, sceneId) {
    if (jobWidget) jobWidget.destroy();
    clear(jobHost);
    const widget = jobProgress(job, {
      compact: true,
      stream: false,
      fireInitial: true,
      onSuccess: () => {
        if (destroyed) return;
        announce(`${label}: done.`);
        jobHost.hidden = true;
      },
      onFinished: () => {
        if (destroyed) return;
        if (jobWidget === widget) jobWidget = null;
        void reload(sceneId);
      },
      // The widget's own Retry follows a new job: track it again, so leaving the page (or showing another
      // job) stops its polling.
      onReplaced: () => {
        if (!destroyed) jobWidget = widget;
      },
    });
    jobWidget = widget;
    jobHost.append(h('h2', { class: 'vr-job-title' }, label), widget.el);
    jobHost.hidden = false;
  }

  /** @param {string | null} focusId */
  async function reload(focusId) {
    try {
      const [fresh, review] = await Promise.all([get(`/api/versions/${vid}`), get(`/api/versions/${vid}/visual-review`)]);
      if (destroyed) return;
      version = fresh;
      revision = Number.isInteger(review && review.revision) ? review.revision : fresh.revision;
      scenes = Array.isArray(review && review.scenes) ? review.scenes : [];
      render();
      if (focusId && !root.contains(document.activeElement)) focusScene(focusId, false);
    } catch (err) {
      if (!destroyed) app.reportError(err, 'Could not reload the visuals.');
    }
  }

  /** A job already working on this version when the page opened. */
  async function checkActiveJob() {
    try {
      const res = await get(`/api/jobs?project_id=${projectId}&limit=20`);
      if (destroyed || jobWidget) return;
      const job = ((res && res.items) || []).find((/** @type {any} */ j) => j.version_id === vid && isActive(j) && MUTATING_KINDS.has(j.kind));
      if (job) showJob(job, 'A job is working on this lecture', null);
    } catch {
      /* optional */
    }
  }

  // A scene asked for in the address (the editor's "Review visual" links) is shown and focused.
  const wanted = typeof query.scene === 'string' ? scenes.find((sv) => sv.scene_id === query.scene && isListed(sv)) : null;
  if (wanted && !FILTERS[filter].test(wanted)) filter = 'all';
  render();
  void checkActiveJob();
  // After the app has attached the page and focused its heading.
  const focusTimer = wanted ? setTimeout(() => !destroyed && focusScene(wanted.scene_id), 0) : null;

  return {
    destroy() {
      destroyed = true;
      if (focusTimer) clearTimeout(focusTimer);
      if (jobWidget) jobWidget.destroy();
      jobWidget = null;
      for (const a of uploads.values()) a.abort(); // the app drops the page's leave guard itself
      uploads.clear();
    },
  };
}
