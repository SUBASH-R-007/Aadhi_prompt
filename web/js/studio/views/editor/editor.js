// @ts-check
/**
 * Screenplay editor (#/p/:id/v/:vid/edit): scene list | scene inspector | preview + issues.
 *
 * State: `base` (last saved/fetched screenplay at `revision`) and `draft` (local edits, kept
 * immutable so undo/redo is a stack of snapshots). Edits mark the editor dirty, autosave the
 * draft to localStorage, schedule lint (POST /lint) and a preview refresh.
 * Save: PUT /screenplay with the revision; 409 revision_conflict offers "reload theirs" or
 * "keep mine" (refetch, three-way merge, save again). Build / regenerate scene / render MP4 run
 * as jobs with live progress; downloads link to the companion sheet, JSON export and chapters.
 * Render MP4 first asks the server what the video would show differently from the editor
 * (GET /render/preflight): silent scenes offer "Fix first" (the default) or "Render anyway",
 * and the request's `allow_degraded` follows that choice (409 render_preflight asks again). The
 * dialog also lists the lecture's open errors and warnings ("quality check before export", at most
 * 12, never blocking). Issues offer safe repairs from POST /lint (`quality.repairs`, applied as one
 * undoable edit), "Regenerate scene" for fixable errors and "Build" while the build is stale; the
 * preview reports the board fit the player measured (`board.overflow` / `board.small_text`, never
 * part of the save checks).
 * "Review visuals" opens the Visual review (#/v/:vid/review). The inspector says where each scene's
 * visual came from at the last build (GET /visual-review, loaded after the editor and after each job),
 * media controls can reuse an item of the teacher's library, and media issues offer "Rebuild this scene"
 * (POST /build with that scene) or, for an AI video that may already have been paid for, "Generate
 * again…" (confirmed; POST /scenes/{id}/visual `retry` with `confirm_paid`).
 *
 * Inspector edits are bound to the scene the inspector was rendered for, and pending
 * (debounced) sub-editor edits are flushed before any state change (scene switch, add,
 * duplicate, delete, undo/redo, save, server reload), so they can never land in another scene.
 * Local drafts are per user (lib/draftStore.js). The mount stops early when the route was
 * left while it was loading (`signal`).
 *
 * Under the panes, the timeline strip (timelineStrip.js) shows the preview Timeline as lanes (scenes,
 * narration, visuals, captions) with the player's playhead: pressing it seeks, a scene block selects (or,
 * dragged, moves) its scene. While the preview plays, the playing scene is marked in the list and the
 * selection follows it unless the teacher is typing or edited in the last few seconds; while paused, choosing
 * a scene shows it still. The save state stays in words next to the title ("Couldn't save" with Retry,
 * "Changed elsewhere" with Review, waiting for a job); Undo / Redo say what they undo. "Save automatically"
 * (off by default, remembered per user in this browser) saves 3 s after the last change, only while the draft
 * is valid, nothing conflicts and no job is running; never after a merge with clashes (until a save by hand), and
 * after a failed save only once something changed (or with Retry). A running job that writes this version (found
 * at mount, when the editor starts one and by a poll every 15 s while the tab is visible) locks editing until it
 * finishes; its result is one undo step ("Changes from the job"). "More" offers "Save as a
 * copy" (POST /duplicate; the unsaved changes go into the copy, which opens) and the shortcuts ("?"). Scenes
 * can be split at a beat (splitScene.js), skipped in the video or held longer (sceneTiming.js), and compared
 * with what Aadhi generated (GET /changes: markers, a side-pane list and "Compare and revert…", which posts
 * /scenes/{id}/revert after confirming). Merges (conflicts, job results, old drafts) are field-level within a
 * scene (sceneMerge.js).
 */

import { h, clear, append } from '../../../shared/dom.js';
import { get, put, post, ApiError } from '../../../shared/api.js';
import { button, spinner, errorState, emptyState, checkbox, linkButton } from '../../components/form.js';
import { menuButton } from '../../components/menu.js';
import { openModal, confirmDialog, promptDialog, choiceDialog, alertDialog } from '../../components/modal.js';
import { jobProgress } from '../../components/jobProgress.js';
import { statusBadge, staleBadge } from '../../components/badges.js';
import { icon } from '../../components/icons.js';
import { captureFocus, restoreFocus } from '../../components/focusKeep.js';
import * as E from '../../lib/screenplayEdit.js';
import { localProblems, issuesFromValidationError, issuesByScene } from '../../lib/validationIssues.js';
import { saveDraft, loadDraft, clearDraft, loadPref, savePref } from '../../lib/draftStore.js';
import { isActive } from '../../lib/jobStages.js';
import { defaultManimTemplate, templatesOf } from '../../lib/manimTemplates.js';
import { debounce, deepEqual, formatRelative, isEditableTarget, uid } from '../../util.js';
import { href } from '../../router.js';
import { errorMessage } from '../../errors.js';
import { breadcrumbs, jobModal, EDITABLE_STATUSES } from '../common.js';
import { createSceneList } from './sceneList.js';
import { renderInspector } from './inspector.js';
import { createIssuesPanel } from './issuesPanel.js';
import { createPreviewPane } from './previewPane.js';
import { applyRepair, attachRepairs, fitIssue, preExportList } from './quality.js';
import { visualInfo } from './visualInfo.js';
import { confirmPaidRetry } from '../visualReview.js';
import { openLibraryPicker } from '../../components/libraryPicker.js';
import { openShortcutsDialog } from '../../components/shortcutsDialog.js';
import { createTimelineStrip } from './timelineStrip.js';
import { createChangesPanel } from './changesPanel.js';
import { splitScene as splitSceneAt, splitProblem } from './splitScene.js';
import { mergeDrafts } from './sceneMerge.js';
import { HISTORY_LIMIT, editLabel, changesBySceneId, fieldWords, restorePosition } from './changes.js';
import { revealFolded } from './inspectorFold.js';

const COALESCE_MS = 1200;
const MUTATING_KINDS = new Set(['generate_lecture', 'regenerate_scene', 'build_assets', 'translate']);
/** "Save automatically": quiet time after the last change before saving. */
export const AUTOSAVE_MS = 3000;
/** How often the editor looks for a job started elsewhere (only while the tab is visible and none is shown). */
export const JOB_POLL_MS = 15000;
/** While the preview plays, the selection follows it only after this long without an edit. */
export const FOLLOW_QUIET_MS = 3000;

/**
 * Shortcuts listed by "?" (editor, scene list, timeline, preview player).
 * @type {import('../../components/shortcutsDialog.js').ShortcutGroup[]}
 */
const SHORTCUTS = [
  {
    title: 'Editor',
    keys: [
      ['Ctrl + S', 'Save'],
      ['Ctrl + Z', 'Undo'],
      ['Ctrl + Shift + Z / Ctrl + Y', 'Redo'],
      ['Space', 'Play or pause the preview (when no button or field has the focus)'],
      ['[ / ]', 'Previous or next scene'],
      ['Delete', 'Delete the selected scene (asks first)'],
      ['?', 'Show these shortcuts'],
    ],
  },
  {
    title: 'Scene list',
    keys: [
      ['↑ / ↓', 'Choose the previous or next scene'],
      ['Shift + Enter', 'Go to the scene’s first field'],
      ['Space, then ↑ / ↓, then Space', 'Move a scene with its grip'],
      ['Alt + ↑ / Alt + ↓', 'Move a scene with its grip at once'],
    ],
  },
  {
    title: 'Timeline',
    keys: [
      ['← / →', 'On the time ruler: move the playhead by 1 s (Shift: 10 s); on the scenes: go to the previous or next scene'],
      ['Home / End', 'Start or end'],
      ['Enter', 'Choose the scene'],
    ],
  },
  {
    title: 'Preview player (when it has the focus)',
    keys: [
      ['Space / K', 'Play or pause'],
      ['J / L', 'Back or forward 10 s'],
      ['← / →', 'Back or forward 5 s'],
      ['P / N', 'Previous or next scene'],
      ['C', 'Captions on or off'],
      ['M', 'Mute'],
      ['F', 'Full screen'],
    ],
  },
];

/**
 * Whether the selection follows the scene the preview entered: only while it plays, to a scene of the draft
 * other than the selected one, and never while the teacher types in the inspector or edited a moment ago
 * (moving the selection re-renders the inspector).
 * @param {{ playing: boolean, sceneId: string | null, selectedId: string | null, known: boolean, typing: boolean, sinceEdit: number }} s
 */
export function shouldFollow(s) {
  return s.playing && !!s.sceneId && s.sceneId !== s.selectedId && s.known && !s.typing && s.sinceEdit >= FOLLOW_QUIET_MS;
}

/**
 * A timer that never keeps a non-browser runtime alive on its own (browsers return a number: nothing to do).
 * @param {() => void} fn
 * @param {number} ms
 */
function backgroundTimer(fn, ms) {
  const t = setTimeout(fn, ms);
  const handle = /** @type {any} */ (t);
  if (handle && typeof handle.unref === 'function') handle.unref();
  return t;
}

/** @type {import('../../types.js').ViewMount} */
export async function mount(container, { app, params, query, signal }) {
  const projectId = params.id;
  const vid = params.vid;
  const loading = h('div', {}, spinner('Loading the lecture…'));
  container.appendChild(loading);
  const left = () => !!(signal && signal.aborted);

  /** @type {any} */
  let version;
  /** @type {any} */
  let meta;
  const viewer = app.user();
  /** An admin may open someone else's lecture: who owns it decides whether the library is offered. @type {any} */
  let projectInfo = null;
  try {
    [version, meta, projectInfo] = await Promise.all([
      get(`/api/versions/${vid}`),
      app.meta(),
      viewer && viewer.role === 'admin' ? get(`/api/projects/${projectId}`).catch(() => null) : Promise.resolve(null),
    ]);
  } catch (err) {
    if (left()) return;
    clear(container);
    container.appendChild(errorState(errorMessage(err, 'Could not load this lecture.'), () => app.navigate(href('editor', { id: projectId, vid }), { replace: true })));
    app.reportError(err);
    return;
  }
  if (left()) return; // the route changed while loading: no guard, no dialogs
  clear(container);
  if (!version.screenplay) {
    container.appendChild(
      emptyState(
        version.status === 'awaiting_review' ? 'The plan is waiting for your review' : 'This lecture is not ready to edit yet',
        version.status === 'generating' ? 'Aadhi is still writing it. Follow the progress on the project page.' : 'There is no screenplay for this version.',
        version.status === 'awaiting_review' ? linkButton('Review plan', href('planReview', { id: projectId, vid }), { kind: 'gold' }) : null,
        linkButton('Project page', href('project', { id: projectId }), { kind: 'outline' }),
      ),
    );
    return;
  }

  // ------------------------------------------------------------------------------------------
  // State
  // ------------------------------------------------------------------------------------------
  /** @type {any} */
  let base = version.screenplay;
  /** @type {any} */
  let draft = base;
  /** Screenplay at load / last server sync (generation issues refer to it). */
  let serverSnapshot = base;
  let revision = version.revision;
  /** Undo / redo history: snapshots with what the step did ("Edit narration in scene 3"). @type {Array<{ sp: any, label: string }>} */
  let undoStack = [];
  /** @type {Array<{ sp: any, label: string }>} */
  let redoStack = [];
  let lastCoalesce = '';
  let lastCoalesceAt = 0;
  /** @type {string | null} */
  let selectedId = query.scene && E.findScene(draft, query.scene) ? query.scene : draft.scenes[0] ? draft.scenes[0].id : null;
  /** @type {{ phase: 'main' | 'reveal', index: number } | null} */
  let selectedBeat = null;
  /** @type {any[]} */
  let lintIssues = (version.issues || []).filter((/** @type {any} */ i) => i.source === 'lint');
  /** @type {any[]} */
  let persistedIssues = (version.issues || []).filter((/** @type {any} */ i) => i.source !== 'lint');
  let lintLoading = false;
  /** @type {string | null} */
  let lintNote = null;
  /** @type {Set<string>} */
  let staleScenes = new Set(version.stale_scenes || []);
  let saving = false;
  let destroyed = false;
  /** @type {AbortController | null} */
  let lintAbort = null;
  /** @type {{ el: HTMLElement, flush: () => void, destroy: () => void } | null} */
  let inspector = null;
  /** @type {{ destroy: () => void } | null} */
  let busyWidget = null;
  const openSections = new Set();
  /** Each scene's visual at the last build (GET /visual-review); empty until loaded or when unavailable. @type {Map<string, any>} */
  let visuals = new Map();
  const maxUploadMb = (meta.limits && meta.limits.upload_max_mb) || 50;
  /** Local checks know the server's templates (schema validation when published). */
  const checkOpts = { manimTemplates: templatesOf(meta) };
  const me = app.user();
  /** Drafts are stored per user; without a user nothing is stored. */
  const userId = me ? me.id : null;
  // Library items go only into the user's own lectures (POST /api/library/{id}/attach; the visual review hides
  // choose / upload there too). Only an admin can open someone else's; without the owner, nothing is offered.
  const owner = projectInfo && projectInfo.project && projectInfo.project.owner;
  const ownLecture = !!me && (me.role !== 'admin' || !!(owner && owner.id === me.id));
  /**
   * The last save that failed, until a save succeeds (shown in words with Retry / Review). `merged`: newer changes
   * (a job's result, a restored draft) were merged with clashes, keeping the teacher's version: nothing is saved
   * automatically until the teacher reviews and saves by hand (or reloads theirs).
   * @type {{ kind: 'network' | 'busy' | 'invalid' | 'conflict' | 'merged' } | null}
   */
  let saveError = null;
  /** "Draft restored" stays in the save state until the next change. */
  let restoredNote = false;
  /** "Save automatically" is per user and only in this browser; off by default. */
  const autosavePrefKey = userId ? `editorAutosave.u${userId}` : null;
  let autosaveOn = !!autosavePrefKey && loadPref(autosavePrefKey, /** @type {boolean} */ (false)) === true;
  /** A draft the server refused (422): automatic saving waits for the next change. @type {any} */
  let refusedDraft = null;
  /** A draft whose save failed (offline, 403/404/413/429/5xx): automatic saving waits for the next change or Retry. @type {any} */
  let failedDraft = null;
  /** "Save as a copy" is running: this version must not be saved automatically meanwhile. */
  let copying = false;
  /** A revert (or restore) request is out: edits wait until its result is loaded. */
  let reverting = false;
  /** The running job that writes this version: editing is locked until it finishes. @type {any} */
  let lockJob = null;
  let lockToastAt = 0;
  /** What changed since Aadhi wrote the lecture (GET /changes; null = not loaded or not offered). @type {import('../../types.js').VersionChanges | null} */
  let changes = null;
  /** @type {Map<string, import('../../types.js').SceneChange>} */
  let changeMap = new Map();
  /** The saved screenplay the comparison describes. @type {any} */
  let changesFor = null;
  /** The scene the preview is playing (null while paused). @type {string | null} */
  let playingId = null;
  let lastEditAt = 0;
  /** Inspector sections the teacher folded (kept while the editor is open). @type {Set<string>} */
  const closedSections = new Set();
  /** @type {ReturnType<typeof setTimeout> | null} */
  let pollTimer = null;

  const isDirty = () => draft !== base && !deepEqual(draft, base);

  /** Memoised scene equality (scenes are immutable objects, so results can be cached). */
  /** @type {WeakMap<object, WeakMap<object, boolean>>} */
  const eqMemo = new WeakMap();
  /**
   * @param {any} a
   * @param {any} b
   */
  const sceneEqual = (a, b) => {
    if (a === b) return true;
    if (!a || !b) return false;
    let inner = eqMemo.get(a);
    if (!inner) {
      inner = new WeakMap();
      eqMemo.set(a, inner);
    }
    let v = inner.get(b);
    if (v === undefined) {
      v = deepEqual(a, b);
      inner.set(b, v);
    }
    return v;
  };

  // ------------------------------------------------------------------------------------------
  // Layout
  // ------------------------------------------------------------------------------------------
  const saveStatus = h('span', { class: 'save-status', role: 'status', 'aria-live': 'polite', dataset: { state: 'saved' } });
  const saveRetry = button('Retry', { kind: 'outline', small: true, icon: 'retry', title: 'Try saving again' });
  const saveReview = button('Review', { kind: 'outline', small: true, title: 'See the newer version and keep or merge your changes' });
  const saveActions = h('span', { class: 'save-actions', hidden: true }, saveRetry, saveReview);
  const autosaveBox = checkbox({
    label: 'Save automatically',
    checked: autosaveOn,
    disabled: !autosavePrefKey,
    dataset: { fk: 'editor:autosave' },
    onChange: (on) => {
      autosaveOn = on;
      if (autosavePrefKey) savePref(autosavePrefKey, on);
      if (on) scheduleServerSave();
      else serverAutosave.cancel();
      renderChrome();
    },
  });
  autosaveBox.classList.add('autosave-toggle');
  const helpBtn = button('Keyboard shortcuts', { kind: 'ghost', small: true, iconOnly: true, icon: 'info', title: 'Keyboard shortcuts (?)' });
  /** What Undo / Redo would do, as the buttons' descriptions. */
  const undoDesc = h('span', { class: 'sr-only' });
  const redoDesc = h('span', { class: 'sr-only' });
  undoDesc.setAttribute('id', uid('undo-what'));
  redoDesc.setAttribute('id', uid('redo-what'));
  const versionInfo = h('span', { class: 'row gap version-info' });
  const undoBtn = button('Undo', { kind: 'ghost', small: true, iconOnly: true, icon: 'undo', title: 'Undo (Ctrl+Z)' });
  const redoBtn = button('Redo', { kind: 'ghost', small: true, iconOnly: true, icon: 'redo', title: 'Redo (Ctrl+Shift+Z)' });
  undoBtn.setAttribute('aria-describedby', String(undoDesc.getAttribute('id')));
  redoBtn.setAttribute('aria-describedby', String(redoDesc.getAttribute('id')));
  const saveBtn = button('Save', { kind: 'gold', small: true, icon: 'save', title: 'Save (Ctrl+S)' });
  const buildBtn = button('Build', { kind: 'outline', small: true, icon: 'build', title: 'Generate voice and visuals for changed scenes' });
  const renderBtn = button('Render MP4', { kind: 'outline', small: true, icon: 'film' });
  const downloads = menuButton(
    'Downloads',
    [
      { label: 'Companion sheet (Markdown)', icon: 'download', href: `/api/versions/${vid}/companion.md`, download: true },
      { label: 'Companion sheet (printable)', icon: 'file', href: `/api/versions/${vid}/companion.html`, newTab: true },
      { label: 'Screenplay JSON', icon: 'download', href: `/api/versions/${vid}/export.json`, download: true },
      { label: 'YouTube chapters', icon: 'download', href: `/api/versions/${vid}/chapters.txt`, download: true },
    ],
    { small: true, kind: 'ghost', icon: 'download' },
  );
  const previewLink = linkButton('Watch', `/preview/${vid}`, { kind: 'ghost', small: true, icon: 'play', newTab: true });
  const reviewLink = linkButton('Review visuals', href('visualReview', { vid }), { kind: 'ghost', small: true, icon: 'eye' });
  const moreMenu = menuButton(
    'More',
    [
      { label: 'Save as a copy…', icon: 'copy', onClick: () => void saveAsCopy() },
      { label: 'Keyboard shortcuts', icon: 'info', onClick: () => openShortcuts() },
    ],
    { small: true, kind: 'ghost', icon: 'more' },
  );
  const banner = h('div', { class: 'editor-banner', hidden: true });

  const sceneList = createSceneList({
    onSelect: (id, focusInspector) => selectScene(id, { focusInspector }),
    onMove: (from, to) => commitDraft(E.moveScene(draft, from, to), { structural: true, listOnly: true, label: 'Move scene' }),
    onAdd: (type, afterIndex) => addScene(type, afterIndex),
    onDuplicate: (index) => duplicateScene(index),
    onDelete: (index) => void deleteScene(index),
    onAddPicture: ownLecture ? (afterIndex) => void addPictureScene(afterIndex) : undefined,
  });
  const inspectorHost = h('div', { class: 'inspector-host', 'aria-label': 'Scene inspector', role: 'region' });
  const issues = createIssuesPanel({
    onSelect: (issue) => jumpToIssue(issue),
    onApplyRepair: (issue) => applyIssueRepair(issue),
    onRegenerate: (issue) => void regenerateScene(issue.scene_id || selectedId, regenerateInstructions(issue.scene_id)),
    onBuild: () => void build(),
    onRebuildScene: (issue) => void rebuildScene(issue.scene_id || null),
    onGenerateAgain: (issue) => void generateAgain(issue.scene_id || null),
    reviewHref: (sceneId) => href('visualReview', { vid }, { scene: sceneId }),
  });
  /**
   * Board fit measured by the player in the preview, per scene: the scene as measured (the measurement holds
   * while the draft scene is unchanged) and the issue it gave (null = it fits).
   * @type {Map<string, { scene: any, issue: any }>}
   */
  const fitResults = new Map();
  const preview = createPreviewPane({
    versionId: vid,
    onMeasure: (m) => recordFit(m),
    onSceneChange: (_index, sceneId) => onPlayerScene(sceneId),
    onTimeline: (timeline) => strip.setTimeline(timeline, draft),
    onTimeUpdate: (t, playing) => strip.setTime(t, playing),
    onPlayState: (playing) => onPlayState(playing),
    sceneNumber: (id) => E.sceneIndex(draft, id) + 1, // as the scene list numbers it (skipped scenes counted)
    getSource: () => {
      const local = localProblems(draft, checkOpts);
      if (local.length) return { mode: 'draft', screenplay: null, blockedReason: `Fix ${local.length} error${local.length === 1 ? '' : 's'} to refresh the preview.` };
      const built = !isDirty() && version.has_timeline && !version.timeline_stale;
      return { mode: built ? 'built' : 'draft', screenplay: built ? null : draft, blockedReason: null };
    },
  });
  const strip = createTimelineStrip({
    onSeek: (t) => preview.seek(t),
    // the selected scene's block shows its start again
    onSelectScene: (id) => (id === selectedId ? void preview.seekSceneId(id) : selectScene(id)),
    onMove: (fromId, beforeId) => moveSceneBefore(fromId, beforeId),
    onTogglePlay: () => preview.togglePlay(),
    onStep: (delta) => stepScene(delta),
  });
  const changesPanel = createChangesPanel({
    onSelect: (id) => E.findScene(draft, id) && selectScene(id),
    onRevert: (id) => void revertScene(id),
    onRestore: (change) => void restoreRemovedScene(change),
  });

  const root = h(
    'div',
    { class: 'editor', 'data-dirty': 'false' },
    h(
      'header',
      { class: 'editor-head' },
      breadcrumbs([{ label: 'Projects', hash: '#/projects' }, { label: 'Project', hash: href('project', { id: projectId }) }, { label: 'Editor' }]),
      h(
        'div',
        { class: 'editor-toolbar' },
        h('h1', { class: 'editor-title' }, draft.session_title || 'Untitled lecture'),
        versionInfo,
        saveStatus,
        saveActions,
        h('span', { class: 'spacer' }),
        undoBtn,
        redoBtn,
        undoDesc,
        redoDesc,
        saveBtn,
        autosaveBox,
        buildBtn,
        renderBtn,
        previewLink,
        reviewLink,
        downloads.el,
        moreMenu.el,
        helpBtn,
      ),
      banner,
    ),
    h('div', { class: 'editor-grid' }, h('aside', { class: 'pane pane-scenes' }, sceneList.el), h('div', { class: 'pane pane-inspector' }, inspectorHost), h('aside', { class: 'pane pane-side' }, preview.el, issues.el, changesPanel.el)),
    strip.el,
  );
  container.appendChild(root);

  // ------------------------------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------------------------------
  function combinedIssues() {
    const local = localProblems(draft, checkOpts);
    const edited = new Set();
    for (const s of draft.scenes) {
      if (!sceneEqual(s, E.findScene(serverSnapshot, s.id))) edited.add(s.id);
    }
    const generation = persistedIssues.filter((i) => !i.scene_id || (!edited.has(i.scene_id) && E.findScene(draft, i.scene_id)));
    const measured = measuredIssues();
    const found = local.length ? [...generation, ...measured] : [...lintIssues.filter((i) => !i.scene_id || E.findScene(draft, i.scene_id)), ...generation, ...measured];
    // A skipped scene's findings are notes (an unsaved skip too): never in the pre-render list, never a paid rewrite
    // or rebuild. Validity problems (the editor's checks, the server's schema) still block saving: they keep theirs.
    const asNotes = found.map((i) => (i.scene_id && i.severity !== 'info' && i.source !== 'server' && E.findScene(draft, i.scene_id)?.hidden ? { ...i, severity: 'info' } : i));
    return local.length ? [...local, ...asNotes] : asNotes;
  }

  /** Board-fit issues the preview measured, for scenes unchanged since (never part of the save checks). */
  function measuredIssues() {
    /** @type {any[]} */
    const out = [];
    for (const [id, m] of fitResults) {
      if (m.issue && sceneEqual(m.scene, E.findScene(draft, id))) out.push(m.issue);
    }
    return out;
  }

  /**
   * A measurement from the preview: kept per scene; the panels update only when the outcome changed.
   * @param {{ sceneId: string, index: number, fit: number, overflow: boolean, source: any } | null} m
   */
  function recordFit(m) {
    if (destroyed || !m || !m.sceneId) return;
    const scene = E.findScene(m.source || base, m.sceneId);
    if (!scene) return;
    const issue = fitIssue(m.sceneId, E.sceneIndex(draft, m.sceneId), m);
    const before = fitResults.get(m.sceneId);
    fitResults.set(m.sceneId, { scene, issue });
    if (!before || (before.issue && before.issue.message) !== (issue && issue.message) || before.scene !== scene) {
      renderIssues();
      renderSceneList();
    }
  }

  function renderChrome() {
    const dirty = isDirty();
    root.dataset.dirty = dirty ? 'true' : 'false';
    const locked = !!lockJob;
    root.classList.toggle('is-locked', locked);
    undoBtn.disabled = locked || !undoStack.length;
    redoBtn.disabled = locked || !redoStack.length;
    // The buttons keep their names ("Undo", "Redo"); what they would undo is their tooltip and description.
    const undoWhat = undoStack.length ? undoStack[undoStack.length - 1].label : '';
    const redoWhat = redoStack.length ? redoStack[redoStack.length - 1].label : '';
    undoBtn.title = undoWhat ? `Undo: ${undoWhat} (Ctrl+Z)` : 'Undo (Ctrl+Z)';
    redoBtn.title = redoWhat ? `Redo: ${redoWhat} (Ctrl+Shift+Z)` : 'Redo (Ctrl+Shift+Z)';
    undoDesc.textContent = undoWhat;
    redoDesc.textContent = redoWhat;
    saveBtn.disabled = saving || !dirty || locked;
    const sceneChanged = staleScenes.size > 0 || dirty || version.timeline_stale || !version.has_timeline;
    buildBtn.disabled = saving;
    buildBtn.classList.toggle('btn-attention', sceneChanged);
    renderBtn.disabled = saving || meta.features?.render === false;
    previewLink.toggleAttribute('hidden', !version.has_timeline);
    clear(versionInfo);
    append(versionInfo, [statusBadge(version.status), h('span', { class: 'muted small' }, `r${revision}`), version.has_timeline && version.timeline_stale ? staleBadge() : null]);
    renderSaveState(dirty);
  }

  /**
   * The save state in an icon and words; the live region changes only when the state does.
   * @param {boolean} dirty
   */
  function renderSaveState(dirty) {
    /** @type {[string, string, string]} state, icon, words */
    let s;
    if (saving) s = ['saving', 'refresh', 'Saving…'];
    else if (saveError && saveError.kind === 'conflict') s = ['conflict', 'warning', 'Changed elsewhere. Your changes are kept in this browser.'];
    else if (saveError && saveError.kind === 'merged' && dirty && !lockJob) s = ['merged', 'warning', 'Some of your edits clash with newer changes (your version kept). Review them and save.'];
    else if (lockJob) s = ['busy', 'info', dirty ? 'A job is working on this version: editing waits until it finishes. Your changes are kept.' : 'A job is working on this version: editing waits until it finishes.'];
    else if (saveError && saveError.kind === 'busy') s = ['busy', 'info', 'Waiting for the running job to finish. Your changes are kept in this browser.'];
    else if (saveError && saveError.kind === 'network' && dirty) s = ['error', 'error', 'Couldn’t save. Your changes are kept in this browser.'];
    else if (saveError && saveError.kind === 'invalid' && dirty) s = ['error', 'error', 'Couldn’t save: the server found problems (see Issues).'];
    else if (dirty && restoredNote) s = ['dirty', 'info', 'Draft restored'];
    else if (dirty) s = ['dirty', 'info', autosaveOn ? (localProblems(draft, checkOpts).length ? 'Unsaved changes: fix the errors to save' : 'Unsaved changes: saving automatically') : 'Unsaved changes'];
    else s = ['saved', 'check', 'All changes saved'];
    const [state, iconName, words] = s;
    if (saveStatus.dataset.state !== state || saveStatus.dataset.words !== words) {
      saveStatus.dataset.state = state;
      saveStatus.dataset.words = words;
      clear(saveStatus);
      saveStatus.append(icon(iconName, { size: 14 }), h('span', {}, words));
    }
    saveStatus.classList.toggle('dirty', dirty);
    saveRetry.hidden = state !== 'error' || !(saveError && saveError.kind === 'network');
    saveReview.hidden = state !== 'conflict';
    saveActions.hidden = saveRetry.hidden && saveReview.hidden;
  }

  function renderSceneList() {
    sceneList.update({ sp: draft, selectedId, issueCounts: issuesByScene(combinedIssues()), staleScenes: staleSet(), changes: changeMap, playingId });
    strip.setSelected(selectedId);
  }

  /** Stale = server-reported stale scenes plus scenes edited locally since the last save. */
  function staleSet() {
    const set = new Set(staleScenes);
    for (const s of draft.scenes) {
      if (!sceneEqual(s, E.findScene(base, s.id))) set.add(s.id);
    }
    return set;
  }

  function renderIssues() {
    issues.update(combinedIssues(), draft, {
      loading: lintLoading,
      note: lintNote,
      selectedSceneId: selectedId,
      timelineStale: !!(version.has_timeline && version.timeline_stale) || staleScenes.size > 0,
      busy: saving,
    });
  }

  /** @param {{ keepFocus?: boolean }} [opts] */
  function renderInspectorPane(opts = {}) {
    const snap = opts.keepFocus === false ? null : captureFocus(inspectorHost);
    const scrollTop = inspectorHost.parentElement ? inspectorHost.parentElement.scrollTop : 0;
    // Any edit still pending here goes to the scene its inspector was built for (bound below).
    const old = inspector;
    inspector = null;
    if (old) old.destroy();
    clear(inspectorHost);
    const scene = E.findScene(draft, selectedId);
    if (!scene) {
      inspectorHost.appendChild(emptyState('No scene selected', 'Choose a scene on the left or add one.'));
      return;
    }
    const sceneId = scene.id;
    inspector = renderInspector({
      sp: draft,
      scene,
      meta,
      projectId,
      maxUploadMb,
      editScene: (updater, o) => editSceneById(sceneId, updater, o),
      editScreenplay,
      selectedBeat,
      selectBeat,
      regenerateScene: () => void regenerateScene(),
      duplicateScene: () => duplicateScene(E.sceneIndex(draft, sceneId)),
      deleteScene: () => void deleteScene(E.sceneIndex(draft, sceneId)),
      openSections,
      closedSections,
      visual: visuals.get(sceneId) || null,
      reviewHref: href('visualReview', { vid }, { scene: sceneId }),
      pickLibrary: ownLecture ? (kind) => openLibraryPicker({ app, kind, projectId }) : undefined,
      change: changeMap.get(sceneId) || null,
      revertScene: () => void revertScene(sceneId),
      splitScene: (beatIndex) => splitSceneHere(sceneId, beatIndex),
    });
    inspectorHost.appendChild(inspector.el);
    if (inspectorHost.parentElement) inspectorHost.parentElement.scrollTop = scrollTop;
    restoreFocus(inspectorHost, snap);
  }

  /** Fill the inspector's "Visual" line from the latest visual review (the inspector is not re-rendered). */
  function updateVisualInfo() {
    const host = /** @type {HTMLElement | null} */ (inspectorHost.querySelector('[data-visual-info]'));
    if (!host) return;
    const id = host.dataset.visualInfo || '';
    clear(host);
    const line = visualInfo(visuals.get(id) || null, href('visualReview', { vid }, { scene: id }));
    if (line) host.appendChild(line);
  }

  /** Where each scene's visual came from at the last build (optional: nothing is shown without it). */
  async function loadVisuals() {
    try {
      const res = await get(`/api/versions/${vid}/visual-review`);
      if (destroyed) return;
      visuals = new Map(((res && Array.isArray(res.scenes) && res.scenes) || []).map((/** @type {any} */ sv) => [sv.scene_id, sv]));
      updateVisualInfo();
    } catch {
      /* an older server, or the review is unavailable: the inspector shows no visual line */
    }
  }

  function renderAll() {
    renderChrome();
    renderSceneList();
    renderInspectorPane();
    renderIssues();
  }

  // ------------------------------------------------------------------------------------------
  // Editing
  // ------------------------------------------------------------------------------------------
  /**
   * @param {any} next
   * @param {{ structural?: boolean, coalesce?: string, listOnly?: boolean, label?: string }} [opts]
   */
  function commitDraft(next, opts = {}) {
    if (next === draft) return;
    if (lockJob) {
      notifyLocked();
      if (opts.structural || opts.listOnly) {
        renderSceneList();
        renderInspectorPane();
      }
      return;
    }
    const now = Date.now();
    const merge = opts.coalesce && opts.coalesce === lastCoalesce && now - lastCoalesceAt < COALESCE_MS;
    if (!merge) {
      undoStack.push({ sp: draft, label: opts.label || 'Edit' });
      if (undoStack.length > HISTORY_LIMIT) undoStack.shift();
    }
    redoStack = [];
    lastCoalesce = opts.coalesce || '';
    lastCoalesceAt = now;
    lastEditAt = now;
    restoredNote = false;
    draft = next;
    if (selectedId && !E.findScene(draft, selectedId)) selectedId = draft.scenes[0] ? draft.scenes[0].id : null;
    afterChange(!!opts.structural && !opts.listOnly);
  }

  /** Editing waits while a job writes this version (said at most every few seconds). */
  function notifyLocked() {
    const now = Date.now();
    if (now - lockToastAt < 4000) return;
    lockToastAt = now;
    app.toast('Editing waits until the running job finishes (see the banner above).', { kind: 'warning' });
  }

  /** @param {boolean} rerenderInspector */
  function afterChange(rerenderInspector) {
    renderChrome();
    renderSceneList();
    if (rerenderInspector) renderInspectorPane();
    autosave();
    scheduleServerSave();
    lintSoon();
    preview.scheduleRefresh();
  }

  /**
   * "Edit narration in scene 3": the undo label of a scene edit.
   * @param {string} sceneId
   * @param {{ coalesce?: string, label?: string }} opts
   */
  function sceneEditLabel(sceneId, opts) {
    const what = opts.label || editLabel(opts.coalesce);
    const n = E.sceneIndex(draft, sceneId) + 1;
    return n > 0 ? `${what} in scene ${n}` : what;
  }

  /**
   * Edit one scene by id (never "the selected scene": a debounced edit may arrive after the
   * selection changed). Edits of a scene that no longer exists are dropped.
   * @param {string} sceneId
   * @param {(scene: any) => any} updater
   * @param {{ structural?: boolean, coalesce?: string, label?: string }} [opts]
   */
  function editSceneById(sceneId, updater, opts = {}) {
    if (destroyed || !E.findScene(draft, sceneId)) return;
    try {
      // Coalesce keys are per scene so typing in two scenes never merges into one undo step.
      const label = sceneEditLabel(sceneId, opts);
      const o = opts.coalesce ? { ...opts, coalesce: `${sceneId}|${opts.coalesce}`, label } : { ...opts, label };
      commitDraft(E.updateScene(draft, sceneId, updater), o);
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), { kind: 'warning' });
    }
  }

  /**
   * @param {(sp: any) => any} updater
   * @param {{ structural?: boolean, coalesce?: string, label?: string }} [opts]
   */
  function editScreenplay(updater, opts = {}) {
    if (destroyed) return;
    try {
      commitDraft(updater(draft), { ...opts, label: opts.label || 'Edit the lecture' });
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), { kind: 'warning' });
    }
  }

  /**
   * Deliver pending (debounced) inspector edits now, before the draft or the selection
   * changes; they are bound to their scene, this only fixes the ordering.
   */
  function flushPending() {
    if (inspector) inspector.flush();
  }

  function undo() {
    flushPending();
    if (!undoStack.length) return;
    if (lockJob) return notifyLocked();
    const step = /** @type {{ sp: any, label: string }} */ (undoStack.pop());
    redoStack.push({ sp: draft, label: step.label });
    draft = step.sp;
    lastCoalesce = '';
    lastEditAt = Date.now();
    restoredNote = false;
    if (selectedId && !E.findScene(draft, selectedId)) selectedId = draft.scenes[0] ? draft.scenes[0].id : null;
    renderAll();
    autosave();
    scheduleServerSave();
    lintSoon();
    preview.scheduleRefresh();
    app.toast(`Undone: ${step.label}.`, { timeout: 1500 });
  }

  function redo() {
    flushPending(); // a pending edit is a new change: it clears the redo stack, as it should
    if (!redoStack.length) return;
    if (lockJob) return notifyLocked();
    const step = /** @type {{ sp: any, label: string }} */ (redoStack.pop());
    undoStack.push({ sp: draft, label: step.label });
    draft = step.sp;
    lastCoalesce = '';
    lastEditAt = Date.now();
    restoredNote = false;
    if (selectedId && !E.findScene(draft, selectedId)) selectedId = draft.scenes[0] ? draft.scenes[0].id : null;
    renderAll();
    autosave();
    scheduleServerSave();
    lintSoon();
    preview.scheduleRefresh();
    app.toast(`Redone: ${step.label}.`, { timeout: 1500 });
  }

  /**
   * Choose a scene: the inspector shows it and, unless `seek` is false (the selection follows playback), the
   * preview shows it still (a skipped scene is not in the preview).
   * @param {string} id
   * @param {{ focusInspector?: boolean, beat?: { phase: 'main' | 'reveal', index: number } | null, seek?: boolean }} [opts]
   */
  function selectScene(id, opts = {}) {
    flushPending();
    const changed = id !== selectedId;
    selectedId = id;
    selectedBeat = opts.beat || null;
    if (changed || opts.beat) {
      app.replaceHash(href('editor', { id: projectId, vid }, { scene: id }));
      renderSceneList();
      renderInspectorPane({ keepFocus: false });
      renderIssues();
      if (opts.seek !== false && E.findScene(draft, id)) preview.seekSceneId(id);
    }
    if (opts.beat) focusBeat(opts.beat);
    else if (opts.focusInspector) {
      const first = /** @type {HTMLElement | null} */ ([...inspectorHost.querySelectorAll('input, select, textarea')].find((c) => !c.closest('[hidden]')) || null);
      if (first) first.focus();
    }
  }

  /** @param {number} delta   -1 previous scene, +1 next scene (in the lecture's order) */
  function stepScene(delta) {
    const i = E.sceneIndex(draft, selectedId || '');
    const next = draft.scenes[Math.max(0, Math.min(draft.scenes.length - 1, (i < 0 ? 0 : i) + delta))];
    if (next && next.id !== selectedId) selectScene(next.id);
  }

  /**
   * The preview entered a scene. While it plays, the scene is marked as playing, and the selection follows it
   * unless the teacher is typing in the inspector or edited in the last few seconds; while paused nothing moves.
   * @param {string | null} sceneId
   */
  function onPlayerScene(sceneId) {
    if (destroyed || !preview.isPlaying()) return;
    if (sceneId !== playingId) {
      playingId = sceneId;
      strip.setPlayingScene(playingId);
      renderSceneList();
    }
    const active = document.activeElement;
    const typing = !!active && inspectorHost.contains(active) && isEditableTarget(active);
    // (selectScene flushes a sub-editor's pending edit into its own scene first)
    if (!shouldFollow({ playing: true, sceneId, selectedId, known: !!sceneId && !!E.findScene(draft, sceneId), typing, sinceEdit: Date.now() - lastEditAt })) return;
    selectScene(/** @type {string} */ (sceneId), { seek: false });
  }

  /** @param {boolean} playing */
  function onPlayState(playing) {
    if (destroyed) return;
    strip.setPlaying(playing);
    // playing starts in the scene the player shows; paused, nothing is marked
    const now = playing ? preview.sceneId() : null;
    if (now !== playingId) {
      playingId = now;
      strip.setPlayingScene(playingId);
      renderSceneList();
    }
  }

  /**
   * @param {'main' | 'reveal'} phase
   * @param {number} index
   */
  function selectBeat(phase, index) {
    selectedBeat = { phase, index };
    for (const el of inspectorHost.querySelectorAll('.beat')) {
      const b = /** @type {HTMLElement} */ (el);
      b.classList.toggle('selected', b.dataset.beatPhase === phase && b.dataset.beatIndex === String(index));
    }
  }

  /** @param {{ phase: 'main' | 'reveal', index: number }} beat */
  function focusBeat(beat) {
    queueMicrotask(() => {
      const el = /** @type {HTMLElement | null} */ ([...inspectorHost.querySelectorAll('.beat')].find((b) => /** @type {HTMLElement} */ (b).dataset.beatPhase === beat.phase && /** @type {HTMLElement} */ (b).dataset.beatIndex === String(beat.index)) || null);
      if (!el) return;
      revealFolded(el, closedSections); // never focus inside a folded section
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      const ta = /** @type {HTMLElement | null} */ (el.querySelector('textarea'));
      if (ta) ta.focus({ preventScroll: true });
    });
  }

  /** @param {any} issue */
  function jumpToIssue(issue) {
    if (!issue.scene_id || !E.findScene(draft, issue.scene_id)) return;
    let beat = null;
    if (issue.beat_id) {
      const scene = E.findScene(draft, issue.scene_id);
      const found = scene ? E.sceneBeats(scene).find((b) => b.beat.id === issue.beat_id) : null;
      if (found) beat = { phase: found.phase, index: found.index };
    }
    selectScene(issue.scene_id, { beat, focusInspector: !beat });
  }

  /**
   * Apply a safe repair offered by the lecture check: one ordinary edit (undo restores it; the normal save
   * keeps it). Nothing changes when the draft moved on since the check.
   * @param {any} issue
   */
  function applyIssueRepair(issue) {
    flushPending();
    if (destroyed || !issue || !issue.repair) return;
    const next = applyRepair(draft, issue.repair);
    if (!next) {
      app.toast('This part of the lecture changed since the check. It is being checked again.', { kind: 'warning' });
      lintSoon();
      return;
    }
    lintIssues = lintIssues.filter((i) => i !== issue);
    commitDraft(next, { structural: true, label: 'Apply fix' });
    renderIssues();
    app.toast('Fixed. Undo with Ctrl+Z; save to keep it.', { kind: 'success', timeout: 3000 });
  }

  /**
   * Instructions for "Regenerate scene" from an issue: the scene's fixable errors, in words.
   * @param {string | null | undefined} sceneId
   */
  function regenerateInstructions(sceneId) {
    const found = combinedIssues().filter((i) => i.scene_id === sceneId && i.severity === 'error' && i.fixable);
    const lines = [...new Set(found.map((i) => String(i.message || '').trim()).filter(Boolean))].slice(0, 6);
    return lines.length ? `Fix these problems:\n- ${lines.join('\n- ')}`.slice(0, 2000) : '';
  }

  /**
   * @param {string} type
   * @param {number} afterIndex
   */
  function addScene(type, afterIndex) {
    flushPending();
    try {
      if (lockJob) return notifyLocked();
      const r = E.addScene(draft, type, afterIndex, { manimTemplate: defaultManimTemplate(meta) });
      selectedId = r.scene.id;
      selectedBeat = null;
      commitDraft(r.screenplay, { structural: true, label: 'Add scene' });
      app.replaceHash(href('editor', { id: projectId, vid }, { scene: r.scene.id }));
      sceneList.focusSelected();
      app.toast(`Added a ${E.SCENE_TYPE_LABELS[type] || type} scene. Write its narration to save.`, { kind: 'info' });
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), { kind: 'warning' });
    }
  }

  /** @param {number} index */
  function duplicateScene(index) {
    flushPending();
    if (index < 0 || !draft.scenes[index]) return;
    if (lockJob) return notifyLocked();
    let r;
    try {
      r = E.duplicateScene(draft, index);
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), { kind: 'warning' });
      return;
    }
    selectedId = r.scene.id;
    commitDraft(r.screenplay, { structural: true, label: `Duplicate scene ${index + 1}` });
    sceneList.focusSelected();
  }

  /**
   * Split a scene before one of its beats (splitScene.js): one undoable edit; the new second part is selected.
   * @param {string} sceneId
   * @param {number} beatIndex
   */
  function splitSceneHere(sceneId, beatIndex) {
    flushPending();
    if (lockJob) return notifyLocked();
    const problem = splitProblem(draft, sceneId, beatIndex);
    if (problem) {
      app.toast(problem, { kind: 'warning' });
      return;
    }
    const n = E.sceneIndex(draft, sceneId) + 1;
    const r = splitSceneAt(draft, sceneId, beatIndex);
    selectedId = r.scene.id;
    selectedBeat = null;
    commitDraft(r.screenplay, { structural: true, label: `Split scene ${n}` });
    app.replaceHash(href('editor', { id: projectId, vid }, { scene: r.scene.id }));
    sceneList.focusSelected();
    app.toast(`Scene ${n} was split: its beats from beat ${beatIndex + 1} on are now scene ${n + 1}. Undo with Ctrl+Z.`, { kind: 'success', timeout: 4000 });
  }

  /**
   * Move a scene before another one (the timeline strip; null = after the last scene that plays).
   * @param {string} fromId
   * @param {string | null} beforeId
   */
  function moveSceneBefore(fromId, beforeId) {
    flushPending();
    const from = E.sceneIndex(draft, fromId);
    if (from < 0) return;
    let to;
    if (beforeId) {
      const b = E.sceneIndex(draft, beforeId);
      if (b < 0) return;
      to = b > from ? b - 1 : b;
    } else {
      let lastShown = -1;
      draft.scenes.forEach((/** @type {any} */ s, /** @type {number} */ i) => {
        if (s.hidden !== true) lastShown = i;
      });
      to = lastShown >= from ? lastShown : lastShown + 1;
    }
    if (to === from) return;
    commitDraft(E.moveScene(draft, from, to), { structural: true, listOnly: true, label: `Move scene ${from + 1}` });
  }

  /**
   * "Picture from my library…": a content scene whose board shows the chosen picture as a figure (uploads and
   * document figures only: a lecture figure takes those). Its narration is left to the teacher.
   * @param {number} afterIndex
   */
  async function addPictureScene(afterIndex) {
    flushPending();
    if (lockJob) return notifyLocked();
    const item = await openLibraryPicker({ app, kind: 'image', projectId, sources: ['upload', 'figure'], title: 'Choose a picture for the new scene' });
    if (!item || destroyed || !item.asset_key) return;
    flushPending();
    try {
      const fig = E.addFigure(draft, { asset_key: item.asset_key, caption: E.truncateText(String(item.title || ''), 600), width: item.width ?? null, height: item.height ?? null });
      const at = Math.min(afterIndex, draft.scenes.length - 1);
      const r = E.addScene(fig.screenplay, 'content', at, { title: E.truncateText(String(item.title || ''), 240) });
      const next = E.updateScene(r.screenplay, r.scene.id, (s) => {
        const figure = E.newBoardItem('figure', s, fig.screenplay);
        figure.figure_id = fig.figureId;
        figure.caption = E.truncateText(String(item.title || ''), 600) || null;
        s.board = [figure];
      });
      selectedId = r.scene.id;
      selectedBeat = null;
      commitDraft(next, { structural: true, label: 'Add picture scene' });
      app.replaceHash(href('editor', { id: projectId, vid }, { scene: r.scene.id }));
      sceneList.focusSelected();
      app.toast('Added a picture scene. Write what Aadhi says about it to save.', { kind: 'info' });
    } catch (err) {
      app.toast(err instanceof Error ? err.message : String(err), { kind: 'warning' });
    }
  }

  /** @param {number} index */
  async function deleteScene(index) {
    flushPending();
    const scene = draft.scenes[index];
    if (!scene || draft.scenes.length <= 1) return;
    if (lockJob) return notifyLocked();
    const ok = await confirmDialog({ title: 'Delete scene?', message: `Scene ${index + 1} “${scene.title || E.SCENE_TYPE_LABELS[scene.type]}” will be removed. You can undo this.`, confirmLabel: 'Delete scene', danger: true });
    if (!ok || destroyed) return;
    flushPending();
    const at = E.sceneIndex(draft, scene.id); // the draft may have changed while the dialog was open
    if (at < 0 || draft.scenes.length <= 1) return;
    if (lockJob) return notifyLocked();
    const next = E.deleteScene(draft, at);
    selectedId = next.scenes[Math.min(at, next.scenes.length - 1)].id;
    commitDraft(next, { structural: true, label: `Delete scene ${at + 1}` });
    sceneList.focusSelected();
  }

  // ------------------------------------------------------------------------------------------
  // Autosave, lint
  // ------------------------------------------------------------------------------------------
  const autosave = debounce(() => {
    if (destroyed) return;
    if (isDirty()) saveDraft(userId, vid, { revision, screenplay: draft, base });
    else clearDraft(userId, vid);
  }, 800);

  /** "Save automatically": a quiet save AUTOSAVE_MS after the last change (the local draft stays the crash copy). */
  const serverAutosave = debounce(() => void autoSave(), AUTOSAVE_MS);

  /** Why the automatic save waits now (null = it may save). */
  function autosaveHold() {
    if (destroyed || !autosaveOn) return 'off';
    if (!isDirty()) return 'clean';
    if (saving) return 'saving';
    if (copying) return 'copying';
    if (reverting) return 'reverting';
    if (lockJob) return 'job';
    if (saveError && (saveError.kind === 'conflict' || saveError.kind === 'busy' || saveError.kind === 'merged')) return saveError.kind;
    if (refusedDraft !== null && draft === refusedDraft) return 'refused';
    if (failedDraft !== null && draft === failedDraft) return 'failed';
    if (localProblems(draft, checkOpts).length) return 'invalid';
    return null;
  }

  function scheduleServerSave() {
    if (autosaveHold() === null) serverAutosave();
    else serverAutosave.cancel();
  }

  async function autoSave() {
    if (autosaveHold() !== null) return;
    // A dialog is open (a confirmation, the conflict choice): try again once it is closed.
    if (document.querySelector('.modal-backdrop')) {
      serverAutosave();
      return;
    }
    await save({ quiet: true });
  }

  const lintSoon = debounce(() => void runLint(), 900);

  async function runLint() {
    if (destroyed) return;
    if (lintAbort) lintAbort.abort();
    const local = localProblems(draft, checkOpts);
    if (local.length) {
      lintLoading = false;
      lintNote = 'Fix the editor errors first; the full lecture check runs once the screenplay is valid.';
      renderIssues();
      renderSceneList();
      return;
    }
    const ac = new AbortController();
    lintAbort = ac;
    lintLoading = true;
    lintNote = null;
    renderIssues();
    const sent = draft;
    try {
      const res = await post(`/api/versions/${vid}/lint`, { screenplay: sent }, { signal: ac.signal });
      if (destroyed || ac.signal.aborted) return;
      // Safe repairs (quality.repairs) apply to this draft only; they are attached to the issues they fix.
      lintIssues = attachRepairs((res.issues || []).map((/** @type {any} */ i) => ({ source: 'lint', ...i })), res.quality && res.quality.repairs);
    } catch (err) {
      if (destroyed || (err instanceof Error && err.name === 'AbortError')) return;
      if (err instanceof ApiError && err.status === 422) {
        lintIssues = issuesFromValidationError(err.detail, sent);
      } else {
        lintNote = `The lecture check is unavailable: ${errorMessage(err)}`;
      }
    } finally {
      if (lintAbort === ac) {
        lintAbort = null;
        lintLoading = false;
        if (!destroyed) {
          renderIssues();
          renderSceneList();
        }
      }
    }
  }

  // ------------------------------------------------------------------------------------------
  // Save & conflicts
  // ------------------------------------------------------------------------------------------
  /**
   * Save the draft (PUT /screenplay with the revision). `quiet` (automatic saving): no toasts, and a conflict,
   * a running job or a refused screenplay only change the save state (no dialog while the teacher types).
   * @param {{ quiet?: boolean }} [opts]
   * @returns {Promise<boolean>}
   */
  async function save(opts = {}) {
    if (saving) return false;
    flushPending();
    if (!isDirty()) return true;
    if (lockJob) {
      if (!opts.quiet) notifyLocked();
      return false;
    }
    const local = localProblems(draft, checkOpts);
    if (local.length) {
      if (opts.quiet) return false;
      renderIssues();
      app.toast(`Fix ${local.length} error${local.length === 1 ? '' : 's'} before saving (see Issues).`, { kind: 'error' });
      jumpToIssue(local[0]);
      return false;
    }
    saving = true;
    renderChrome();
    const sent = draft;
    try {
      const res = await put(`/api/versions/${vid}/screenplay`, { screenplay: sent, revision });
      if (destroyed) return true;
      version = { ...version, ...res.version };
      revision = res.version.revision;
      base = sent;
      serverSnapshot = sent;
      persistedIssues = [];
      saveError = null;
      refusedDraft = null;
      failedDraft = null;
      // The PUT carries no `quality`: keep the repairs of the last check (applyRepair re-checks `before`).
      const repairs = lintIssues.flatMap((/** @type {any} */ i) => (i.repair ? [i.repair] : []));
      lintIssues = attachRepairs((res.issues || []).map((/** @type {any} */ i) => ({ source: 'lint', ...i })), repairs);
      staleScenes = new Set(res.stale_scenes || []);
      if (isDirty()) saveDraft(userId, vid, { revision, screenplay: draft, base });
      else clearDraft(userId, vid);
      changesSoon();
      return true;
    } catch (err) {
      if (destroyed) return false;
      if (err instanceof ApiError && err.code === 'revision_conflict') {
        saveError = { kind: 'conflict' };
        if (opts.quiet) return false;
        saving = false;
        return resolveConflict();
      }
      if (err instanceof ApiError && err.status === 422) {
        saveError = { kind: 'invalid' };
        refusedDraft = sent;
        lintIssues = issuesFromValidationError(err.detail, sent);
        renderIssues();
        if (!opts.quiet) app.toast('The server rejected the screenplay. See Issues for details.', { kind: 'error' });
      } else if (err instanceof ApiError && err.code === 'version_busy') {
        saveError = { kind: 'busy' };
        if (!opts.quiet) app.toast('A job is running on this version. Your changes are kept locally; save again when it finishes.', { kind: 'warning' });
        void checkActiveJob();
      } else {
        saveError = { kind: 'network' };
        failedDraft = sent; // automatic saving waits for the next change or Retry (no retry loop)
        if (!opts.quiet) app.reportError(err, 'Saving failed. Your changes are kept locally.');
      }
      return false;
    } finally {
      saving = false;
      if (!destroyed) {
        renderChrome();
        renderSceneList();
        renderIssues();
        // edits made while the request was out are saved too
        scheduleServerSave();
      }
    }
  }

  /** @returns {Promise<boolean>} */
  async function resolveConflict() {
    serverAutosave.cancel();
    const choice = await choiceDialog({
      title: 'This lecture was changed elsewhere',
      message: 'Someone (or a job) saved a newer version while you were editing.',
      details: ['Reload theirs: discard your changes and load the newer version.', 'Keep mine: load the newer version and re-apply your changes on top of it.'],
      choices: [
        { label: 'Cancel', value: 'cancel', kind: 'ghost' },
        { label: 'Reload theirs', value: 'theirs', kind: 'outline' },
        { label: 'Keep mine', value: 'mine', kind: 'gold' },
      ],
    });
    if (choice !== 'theirs' && choice !== 'mine') {
      saveError = { kind: 'conflict' };
      if (!destroyed) renderChrome();
      return false;
    }
    let fresh;
    try {
      fresh = await get(`/api/versions/${vid}`);
    } catch (err) {
      app.reportError(err, 'Could not load the newer version.');
      return false;
    }
    if (destroyed) return false;
    flushPending();
    saveError = null;
    if (choice === 'theirs') {
      adoptServerVersion(fresh);
      clearDraft(userId, vid);
      app.toast('Loaded the latest version.', { kind: 'success' });
      return false;
    }
    const merged = mergeDrafts(base, draft, fresh.screenplay);
    const mine = draft;
    adoptServerVersion(fresh, { keepHistory: true });
    undoStack.push({ sp: fresh.screenplay, label: 'Merge with the newer version' });
    draft = merged.screenplay;
    renderAll();
    autosave();
    if (merged.conflicts.length) {
      await alertDialog({
        title: 'Changes merged with conflicts',
        message: 'Your version was kept where both sides changed the same thing. Review these and save again:',
        details: merged.conflicts.map((c) => c.message),
        size: 'md',
      });
      lintSoon();
      return false;
    }
    if (deepEqual(mine, merged.screenplay) || isDirty()) return save();
    return true;
  }

  /**
   * Replace local state with a server version.
   * @param {any} fresh
   * @param {{ keepHistory?: boolean }} [opts]
   */
  function adoptServerVersion(fresh, opts = {}) {
    flushPending();
    version = fresh;
    revision = fresh.revision;
    base = fresh.screenplay;
    serverSnapshot = fresh.screenplay;
    draft = fresh.screenplay;
    persistedIssues = (fresh.issues || []).filter((/** @type {any} */ i) => i.source !== 'lint');
    lintIssues = (fresh.issues || []).filter((/** @type {any} */ i) => i.source === 'lint');
    staleScenes = new Set(fresh.stale_scenes || []);
    if (saveError && saveError.kind !== 'busy') saveError = null;
    refusedDraft = null;
    failedDraft = null;
    restoredNote = false;
    if (!opts.keepHistory) {
      undoStack = [];
      redoStack = [];
    }
    if (selectedId && !E.findScene(draft, selectedId)) selectedId = draft.scenes[0] ? draft.scenes[0].id : null;
    renderAll();
    preview.scheduleRefresh();
    lintSoon(); // stored issues carry no safe repairs: check the adopted screenplay again
    changesSoon();
  }

  /**
   * An undo step for a screenplay that came from the server (a job's result): one Undo takes back exactly that
   * change and says so. The redo history goes (redoing an older snapshot would silently revert the job too).
   * @param {any} sp    the screenplay before the change
   * @param {string} label
   */
  function pushServerStep(sp, label) {
    undoStack.push({ sp, label });
    if (undoStack.length > HISTORY_LIMIT) undoStack.shift();
    redoStack = [];
    lastCoalesce = '';
  }

  /** Refresh version summary after a job (keeps local edits when dirty). */
  async function refreshVersion() {
    try {
      const fresh = await get(`/api/versions/${vid}`);
      if (destroyed) return;
      flushPending();
      const before = draft;
      if (saveError && saveError.kind === 'busy') saveError = null;
      if (!isDirty()) {
        adoptServerVersion(fresh, { keepHistory: true });
        // The job changed the screenplay: Undo takes back the job's changes (never silently with an older edit).
        if (!deepEqual(before, draft)) {
          pushServerStep(before, 'Changes from the job');
          renderChrome();
        }
      } else if (fresh.revision !== revision) {
        const merged = mergeDrafts(base, draft, fresh.screenplay);
        adoptServerVersion(fresh, { keepHistory: true });
        // Two steps: the job's version replaced the draft, then the teacher's edits were merged on top. Undo shows
        // the job's version first, then the draft as it was before the job.
        if (!deepEqual(before, fresh.screenplay)) pushServerStep(before, 'Changes from the job');
        if (!deepEqual(fresh.screenplay, merged.screenplay)) pushServerStep(fresh.screenplay, "Merge with the job's changes");
        draft = merged.screenplay;
        // Set after adopting (which clears every kind but 'busy'): nothing is saved automatically until the
        // teacher has looked at the clashes and saved by hand.
        if (merged.conflicts.length) saveError = { kind: 'merged' };
        renderAll();
        autosave();
        if (merged.conflicts.length) {
          void alertDialog({
            title: 'Changes merged with conflicts',
            message: "Your version was kept where you and the job changed the same thing. Review these and save again (Undo shows the job's version):",
            details: merged.conflicts.map((c) => c.message),
            size: 'md',
          });
        }
      } else {
        version = { ...version, ...fresh, screenplay: undefined };
        staleScenes = new Set(fresh.stale_scenes || []);
        renderChrome();
        renderSceneList();
      }
      scheduleServerSave();
      void preview.refresh();
      void loadVisuals();
    } catch (err) {
      app.reportError(err);
    }
  }

  /** Ensure saved state before a server-side job. @param {string} action */
  async function requireSaved(action) {
    flushPending();
    if (!isDirty()) return true;
    const ok = await confirmDialog({ title: 'Save first?', message: `Your changes must be saved before you ${action}.`, confirmLabel: 'Save and continue' });
    return ok && save();
  }

  // ------------------------------------------------------------------------------------------
  // Jobs: build, regenerate, render
  // ------------------------------------------------------------------------------------------
  /** @param {unknown} err */
  async function handleJobConflict(err) {
    if (err instanceof ApiError && err.code === 'job_in_progress' && err.body && err.body.job_id) {
      try {
        const job = await get(`/api/jobs/${err.body.job_id}`);
        app.toast('Another job is already running for this version.', { kind: 'warning' });
        void jobModal('Job in progress', job, { onFinished: () => void refreshVersion() });
        return true;
      } catch {
        /* fall through */
      }
    }
    return false;
  }

  async function build() {
    if (!(await requireSaved('build'))) return;
    try {
      const res = await post(`/api/versions/${vid}/build`, { scene_ids: null });
      void checkActiveJob(); // editing locks at once (not only at the next poll), even if the dialog is closed
      void jobModal('Building voice & visuals', res.job, {
        description: 'Only scenes that changed since the last build are rebuilt.',
        onSuccess: () => {
          app.toast('Build finished.', { kind: 'success' });
        },
        onFinished: () => void refreshVersion(),
      });
    } catch (err) {
      if (!(await handleJobConflict(err))) app.reportError(err, 'Could not start the build.');
    }
  }

  /**
   * @param {string | null} [sceneId]   default: the selected scene
   * @param {string} [preset]   instructions filled in (from the issues panel), still editable
   */
  async function regenerateScene(sceneId = selectedId, preset = '') {
    const scene = E.findScene(draft, sceneId || '');
    if (!scene) return;
    if (!(await requireSaved('regenerate a scene'))) return;
    const instructions = await promptDialog({
      title: `Regenerate scene ${E.sceneIndex(draft, scene.id) + 1}`,
      label: 'What should change?',
      message: 'Aadhi rewrites this scene (narration, board, visuals) following your instructions. The rest of the lecture is untouched.',
      placeholder: 'e.g. Use a water-pipe analogy and add a worked example with numbers.',
      value: preset || undefined,
      hint: preset ? 'This uses the AI engine and counts toward your budget.' : undefined,
      multiline: true,
      maxLength: 2000,
      confirmLabel: 'Regenerate',
    });
    if (instructions === null) return;
    try {
      const res = await post(`/api/versions/${vid}/scenes/${encodeURIComponent(scene.id)}/regenerate`, { instructions });
      void checkActiveJob(); // editing locks at once (not only at the next poll), even if the dialog is closed
      void jobModal('Regenerating scene', res.job, { onSuccess: () => app.toast('Scene regenerated.', { kind: 'success' }), onFinished: () => void refreshVersion() });
    } catch (err) {
      if (!(await handleJobConflict(err))) app.reportError(err, 'Could not regenerate the scene.');
    }
  }

  /**
   * Build one scene again (its failed media are retried; nothing is rewritten).
   * @param {string | null} sceneId
   */
  async function rebuildScene(sceneId) {
    const scene = E.findScene(draft, sceneId || '');
    if (!scene) return;
    if (!(await requireSaved('rebuild this scene'))) return;
    try {
      const res = await post(`/api/versions/${vid}/build`, { scene_ids: [scene.id] });
      void checkActiveJob(); // editing locks at once (not only at the next poll), even if the dialog is closed
      void jobModal(`Rebuilding scene ${E.sceneIndex(draft, scene.id) + 1}`, res.job, {
        description: 'Makes this scene’s voice and visuals again. Generated pictures and videos count toward your budget.',
        onSuccess: () => app.toast('Scene rebuilt.', { kind: 'success' }),
        onFinished: () => void refreshVersion(),
      });
    } catch (err) {
      if (!(await handleJobConflict(err))) app.reportError(err, 'Could not rebuild the scene.');
    }
  }

  /**
   * Generate an AI video again although an earlier try may already have been paid for (asked first).
   * @param {string | null} sceneId
   */
  async function generateAgain(sceneId) {
    const scene = E.findScene(draft, sceneId || '');
    if (!scene) return;
    if (!(await requireSaved('generate the video again'))) return;
    const n = E.sceneIndex(draft, scene.id) + 1;
    if (destroyed || !(await confirmPaidRetry(`Scene ${n}`))) return;
    try {
      const res = await post(`/api/versions/${vid}/scenes/${encodeURIComponent(scene.id)}/visual`, { action: 'retry', confirm_paid: true, revision });
      if (destroyed) return;
      if (res && res.job_id) {
        void checkActiveJob(); // editing locks at once (not only at the next poll), even if the dialog is closed
        const job = await get(`/api/jobs/${res.job_id}`);
        void jobModal(`Generating the video of scene ${n} again`, job, { onSuccess: () => app.toast('The video was generated again.', { kind: 'success' }), onFinished: () => void refreshVersion() });
      } else {
        void refreshVersion();
      }
    } catch (err) {
      if (err instanceof ApiError && err.code === 'revision_conflict') {
        app.toast('This lecture was changed elsewhere. The newer version is loaded; try again after that.', { kind: 'warning' });
        void refreshVersion();
        return;
      }
      if (!(await handleJobConflict(err))) app.reportError(err, 'Could not generate the video again.');
    }
  }

  /** "Scene 3 (Title): message" for a render preflight item. @param {any} it */
  function preflightLine(it) {
    // Scenes are numbered as in the scene list (hidden scenes counted): the live draft first (unsaved moves), then
    // the server's `scene_number`, then the timeline position of older servers.
    const i = it && it.scene_id ? E.sceneIndex(draft, it.scene_id) : -1;
    const num = i >= 0 ? i + 1 : Number.isInteger(it && it.scene_number) ? it.scene_number : Number.isInteger(it && it.scene_index) ? it.scene_index + 1 : null;
    const where = num ? `Scene ${num}` : 'A scene';
    return `${where}${it && it.title ? ` (${it.title})` : ''}: ${(it && (it.message || it.reason)) || ''}`;
  }

  /**
   * What the MP4 would show differently from the editor; null when it could not be checked (the
   * render then goes ahead as before).
   * @returns {Promise<any>}
   */
  async function loadRenderPreflight() {
    try {
      return await get(`/api/versions/${vid}/render/preflight`);
    } catch {
      return null;
    }
  }

  /**
   * The preflight list inside a dialog body.
   * @param {any[]} items
   * @param {number} silent  number of blocking (silent) scenes
   */
  function preflightNotice(items, silent) {
    return h(
      'div',
      { class: ['notice', silent ? 'warning' : 'info', 'render-preflight'], 'data-preflight': silent ? 'blocking' : 'info' },
      h('p', {}, silent ? `Before you render: ${silent} scene${silent === 1 ? '' : 's'} would be silent in the video.` : 'Before you render: the video will differ from the editor here.'),
      h('ul', { class: 'plain-list small' }, items.map((it) => h('li', {}, preflightLine(it)))),
    );
  }

  /**
   * Preflight items about the video itself (the server may also list quality items, `reason: "quality"`:
   * the editor shows its own, fresher list of those under a heading of its own).
   * @param {any[]} items
   */
  function degradations(items) {
    return items.filter((it) => it && it.reason !== 'quality');
  }

  /**
   * Quality check before export: the lecture's open errors and warnings (the Issues panel's list). Never
   * blocks the render; it only lists what to review.
   */
  function qualityNotice() {
    const { items, more } = preExportList(combinedIssues(), draft);
    if (!items.length) return null;
    return h(
      'div',
      { class: ['notice', 'info', 'render-quality'], 'data-quality': String(items.length + more) },
      h('p', {}, 'The quality check found things to review. You can still render; fixing them first gives a better video.'),
      h('ul', { class: 'plain-list small' }, items.map((it) => h('li', { 'data-severity': it.severity }, `${it.severity === 'error' ? 'Needs fixing' : 'Please check'}: ${it.text}`))),
      more ? h('p', { class: 'muted small' }, `${more} more in the Issues panel.`) : null,
    );
  }

  /**
   * Silent scenes found by the server when the render was requested: render anyway?
   * @param {any[]} found
   */
  async function confirmDegradedRender(found) {
    const items = degradations(found);
    const silent = items.filter((it) => it && it.blocking).length;
    const modal = openModal({
      title: 'Render MP4',
      body: h('div', {}, preflightNotice(items, silent), h('p', {}, 'Build the lecture again (or fix the narration) so these scenes have sound, or render the video as it is.')),
      actions: [
        { label: 'Render anyway', kind: 'outline', value: 'degraded' },
        { label: 'Fix first', kind: 'gold', value: null, autofocus: true },
      ],
    });
    return (await modal.result) === 'degraded';
  }

  async function renderVideo() {
    if (!(await requireSaved('render a video'))) return;
    const captions = checkbox({ label: 'Burn captions into the video', checked: false, hint: 'Captions are also delivered as SRT/VTT files.' });
    const softSubs = checkbox({ label: 'Add a selectable caption track', checked: false, hint: 'Some players switch it on straight away. Not added when captions are burned in.' });
    const intro = checkbox({ label: 'Include the intro (logo and title cards)', checked: true });
    captions.input.addEventListener('change', () => {
      softSubs.input.disabled = captions.input.checked;
      if (captions.input.checked) softSubs.input.checked = false;
    });
    const stale = !version.has_timeline || version.timeline_stale;
    // A stale timeline is refused anyway (timeline_stale): only check what a built one would show.
    const pre = stale ? null : await loadRenderPreflight();
    if (destroyed) return;
    const items = degradations(pre && Array.isArray(pre.items) ? pre.items : []);
    const silent = items.filter((/** @type {any} */ it) => it && it.blocking).length;
    /** @type {import('../../components/modal.js').ModalAction[]} */
    const actions = [{ label: 'Cancel', kind: 'outline', value: null }];
    if (stale) actions.push({ label: 'Build first', kind: 'outline', value: 'build' });
    if (silent) {
      actions.splice(0, 1, { label: 'Render anyway', kind: 'outline', value: 'degraded' }, { label: 'Fix first', kind: 'gold', value: null, autofocus: true });
    } else {
      actions.push({ label: 'Render', kind: 'gold', value: 'render' });
    }
    const modal = openModal({
      title: 'Render MP4',
      description: 'Renders the built lecture exactly as the player shows it. Long lectures take several minutes.',
      body: h(
        'div',
        {},
        stale ? h('p', { class: 'notice warning' }, 'The lecture has changes that are not built yet. Build first so the video matches the editor.') : null,
        items.length ? preflightNotice(items, silent) : null,
        qualityNotice(),
        captions,
        softSubs,
        intro,
      ),
      actions,
    });
    const choice = await modal.result;
    if (choice === 'build') return build();
    if (choice !== 'render' && choice !== 'degraded') return;
    /** @type {Record<string, any>} */
    const body = { burn_captions: captions.input.checked, include_intro: intro.input.checked };
    if (softSubs.input.checked && !captions.input.checked) body.soft_subtitles = true;
    // Checked: refuse silent scenes unless the teacher chose to render anyway. Not checked: as before.
    if (pre) body.allow_degraded = choice === 'degraded';
    await startRender(body);
  }

  /** @param {Record<string, any>} body */
  async function startRender(body) {
    try {
      const res = await post(`/api/versions/${vid}/render`, body);
      void jobModal('Rendering MP4', res.job, {
        onSuccess: () =>
          app.toast('Your video is ready.', {
            kind: 'success',
            timeout: 0,
            action: { label: 'Downloads', onClick: () => app.navigate(href('project', { id: projectId }, { version: vid })) },
          }),
      });
    } catch (err) {
      if (err instanceof ApiError && err.code === 'timeline_stale') {
        const again = await confirmDialog({ title: 'Build needed', message: 'The timeline is older than the screenplay. Build the lecture first?', confirmLabel: 'Build now' });
        if (again) void build();
        return;
      }
      if (err instanceof ApiError && err.code === 'render_preflight') {
        // Scenes went silent since the check (or it could not run): show them and ask again.
        const found = err.body && Array.isArray(err.body.items) ? err.body.items : [];
        if (!destroyed && (await confirmDegradedRender(found))) await startRender({ ...body, allow_degraded: true });
        return;
      }
      if (!(await handleJobConflict(err))) app.reportError(err, 'Could not start the render.');
    }
  }

  /**
   * Look for a running job that writes this version. While there is one, the banner follows it and editing
   * is locked (the job commits its result on the revision it started from); when it finishes, the version is
   * reloaded (local edits made before are merged) and editing comes back.
   */
  async function checkActiveJob() {
    try {
      const res = await get(`/api/jobs?project_id=${projectId}&limit=20`);
      if (destroyed) return;
      const job = (res.items || []).find((/** @type {any} */ j) => j.version_id === vid && isActive(j) && MUTATING_KINDS.has(j.kind));
      if (busyWidget && job && lockJob && lockJob.id === job.id) return; // already followed
      if (busyWidget) busyWidget.destroy();
      busyWidget = null;
      clear(banner);
      banner.hidden = !job;
      setLock(job || null);
      if (!job) {
        if (saveError && saveError.kind === 'busy') {
          // the job that refused the save is gone: saving (and automatic saving) may go on
          saveError = null;
          renderChrome();
          scheduleServerSave();
        }
        return;
      }
      const widget = jobProgress(job, {
        compact: true,
        stream: false,
        reviewHref: href('planReview', { id: projectId, vid }),
        onFinished: () => {
          banner.hidden = true;
          if (busyWidget === widget) busyWidget = null;
          setLock(null);
          void refreshVersion();
        },
      });
      busyWidget = widget;
      banner.append(h('p', {}, 'A job is working on this version. Editing waits until it finishes; your unsaved changes are kept and merged afterwards.'), widget.el);
    } catch {
      /* optional */
    }
  }

  /**
   * Lock or unlock editing for a job that writes the version (the scene list and the inspector become inert;
   * the preview, issues and downloads stay usable).
   * @param {any} job
   */
  function setLock(job) {
    const was = !!lockJob;
    const on = !!job;
    // Deliver pending (debounced) inspector edits into the draft before the lock refuses edits: they are then
    // kept and merged after the job, as the banner says.
    if (on && !was) flushPending();
    lockJob = job;
    for (const el of [sceneList.el, inspectorHost]) {
      if (on) el.setAttribute('inert', '');
      else el.removeAttribute('inert');
    }
    strip.setLocked(on);
    if (on) serverAutosave.cancel(); // after the flush, which may have scheduled an automatic save
    if (was !== on) {
      renderChrome();
      if (!on) scheduleServerSave();
    }
  }

  /** Look for jobs started elsewhere every JOB_POLL_MS while the tab is visible and none is being followed. */
  function schedulePoll() {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = backgroundTimer(() => {
      pollTimer = null;
      if (destroyed) return;
      if (document.visibilityState !== 'hidden' && !busyWidget) void checkActiveJob().finally(schedulePoll);
      else schedulePoll();
    }, JOB_POLL_MS);
  }

  const onVisibility = () => {
    if (!destroyed && document.visibilityState === 'visible' && !busyWidget) void checkActiveJob();
  };
  document.addEventListener('visibilitychange', onVisibility);

  // ------------------------------------------------------------------------------------------
  // What changed since Aadhi wrote it (GET /changes), revert
  // ------------------------------------------------------------------------------------------
  async function loadChanges() {
    const before = selectedId ? changeMap.get(selectedId) || null : null;
    try {
      const res = await get(`/api/versions/${vid}/changes`);
      if (destroyed) return;
      changes = res && typeof res === 'object' ? res : null;
      changesFor = base;
    } catch {
      if (destroyed) return;
      changes = null; // an older server, or the comparison is unavailable: no markers
    }
    changeMap = changesBySceneId(changes);
    changesPanel.update(changes, changesFor, { busy: !!lockJob });
    renderSceneList();
    const now = selectedId ? changeMap.get(selectedId) || null : null;
    if (!deepEqual(before, now)) updateChangeLine();
  }

  const changesSoon = debounce(() => void loadChanges(), 600);

  /**
   * The inspector's comparison line follows the latest comparison: the inspector is re-rendered, but only
   * while the teacher is not typing in it (the line then catches up with the next scene change).
   */
  function updateChangeLine() {
    const active = document.activeElement;
    if (active && inspectorHost.contains(active) && isEditableTarget(active)) return;
    renderInspectorPane();
  }

  /**
   * "Compare and revert…": what goes back (the changed fields in words), to which version (as generated, or
   * before a regeneration when earlier versions are kept), confirmed first. The revert is a server edit on the
   * saved lecture (compare-and-set on the revision); Undo brings the edited scene back as an unsaved change.
   * @param {string} sceneId
   */
  async function revertScene(sceneId) {
    flushPending();
    if (lockJob) return notifyLocked();
    const c = changeMap.get(sceneId);
    if (!c) return;
    const n = E.sceneIndex(draft, sceneId) + 1;
    const words = fieldWords(c.fields_changed);
    const history = Math.max(0, Number(c.history) || 0);
    /** @type {Array<{ value: string, label: string }>} */
    const options = [];
    if (c.status === 'edited') options.push({ value: 'generated', label: 'As Aadhi wrote it' });
    // `scene_history` is kept newest first: history_index 0 is the scene as it was before the last regeneration,
    // history_index history - 1 the scene before the first one.
    for (let k = history; k >= 1; k--) options.push({ value: `history:${history - k}`, label: k === history ? 'As it was before the last regeneration' : `As it was before regeneration ${k}` });
    if (!options.length) return;
    let chosen = options[0].value;
    const radios = options.map((o, i) => {
      const input = h('input', { type: 'radio', class: 'checkbox', value: o.value, dataset: { revertTo: o.value } });
      input.checked = i === 0;
      input.addEventListener('change', () => {
        if (input.checked) chosen = o.value;
      });
      return h('label', { class: 'field field-inline revert-option' }, input, h('span', { class: 'field-label' }, o.label));
    });
    const group = `revert-${Date.now()}`;
    for (const r of radios) /** @type {HTMLInputElement} */ (r.querySelector('input')).setAttribute('name', group);
    const modal = openModal({
      title: `Revert scene ${n}?`,
      size: 'md',
      body: h(
        'div',
        { class: 'revert-dialog' },
        h('p', {}, c.status === 'edited' ? (words.length ? `These parts go back: ${words.join(', ')}.` : 'The scene goes back to an earlier version.') : 'The scene goes back to an earlier version.'),
        options.length > 1 ? h('div', { class: 'field-group', role: 'radiogroup', 'aria-label': 'Go back to' }, radios) : null,
        isDirty() ? h('p', { class: 'notice info' }, 'Your other unsaved changes are saved first.') : null,
        h('p', { class: 'muted small' }, 'You can undo this with Ctrl+Z (then save to keep the edited scene).'),
      ),
      actions: [
        { label: 'Cancel', kind: 'outline', value: null },
        { label: 'Revert scene', kind: 'danger', value: 'revert', autofocus: true },
      ],
    });
    if ((await modal.result) !== 'revert' || destroyed) return;
    if (!(await requireSaved('revert a scene'))) return;
    /** @type {Record<string, any>} */
    const body = { revision, to: chosen.startsWith('history:') ? 'history' : 'generated' };
    if (body.to === 'history') body.history_index = Number(chosen.slice('history:'.length));
    await postRevert(sceneId, body, `Scene ${n} was reverted.`);
  }

  /**
   * Bring a removed scene back where it was in Aadhi's order (after the nearest scene still there).
   * @param {import('../../types.js').SceneChange} c
   */
  async function restoreRemovedScene(c) {
    flushPending();
    if (lockJob) return notifyLocked();
    // Positions in the draft (saved first, below), so an unsaved reorder is taken into account.
    const all = (changes && Array.isArray(changes.scenes) ? changes.scenes : []).map((x) => {
      const at = x.status === 'removed' ? -1 : E.sceneIndex(draft, x.scene_id);
      return { ...x, current_index: at >= 0 ? at : null };
    });
    const position = restorePosition(all, /** @type {import('../../types.js').SceneChange} */ (all.find((x) => x.scene_id === c.scene_id) || c));
    const ok = await confirmDialog({
      title: 'Restore the removed scene?',
      message: `The scene Aadhi wrote${Number.isInteger(c.generated_index) ? ` as scene ${/** @type {number} */ (c.generated_index) + 1}` : ''} comes back as scene ${position + 1}.`,
      confirmLabel: 'Restore scene',
    });
    if (!ok || destroyed) return;
    if (!(await requireSaved('restore a scene'))) return;
    await postRevert(c.scene_id, { revision, to: 'generated', position }, 'The scene was restored.');
  }

  /**
   * POST /scenes/{id}/revert, then load the saved result. Undo goes back to the scene as it was before.
   * @param {string} sceneId
   * @param {Record<string, any>} body
   * @param {string} done
   */
  async function postRevert(sceneId, body, done) {
    const before = draft;
    reverting = true; // the editor stays live; automatic saving waits for the result
    serverAutosave.cancel();
    try {
      /** @type {import('../../types.js').RevertResult} */
      const res = await post(`/api/versions/${vid}/scenes/${encodeURIComponent(sceneId)}/revert`, body);
      if (destroyed) return;
      const fresh = await get(`/api/versions/${vid}`);
      if (destroyed) return;
      flushPending();
      // Edits made while the revert was out are merged on top of the reverted version (as refreshVersion does).
      const typed = draft !== before && !deepEqual(draft, before) ? draft : null;
      reverting = false;
      adoptServerVersion(fresh, { keepHistory: true });
      undoStack.push({ sp: before, label: 'Revert scene' });
      redoStack = [];
      if (typed) {
        const merged = mergeDrafts(before, typed, fresh.screenplay);
        pushServerStep(fresh.screenplay, 'Edits made during the revert');
        draft = merged.screenplay;
        if (merged.conflicts.length) saveError = { kind: 'merged' };
        renderAll();
        autosave();
        scheduleServerSave();
        lintSoon();
        preview.scheduleRefresh();
        if (merged.conflicts.length) app.toast(`Your edits made during the revert were kept (${merged.conflicts.length} conflict(s)). Save to keep them.`, { kind: 'warning' });
      }
      if (res && typeof res.scene_id === 'string' && E.findScene(draft, res.scene_id)) selectScene(res.scene_id);
      renderChrome();
      app.toast(`${done} Undo with Ctrl+Z.`, { kind: 'success' });
    } catch (err) {
      if (destroyed) return;
      if (err instanceof ApiError && err.code === 'revision_conflict') {
        app.toast('This lecture was changed elsewhere. The newer version is loaded; try again.', { kind: 'warning' });
        void refreshVersion();
      } else if (err instanceof ApiError && err.status === 404) {
        app.toast('There is nothing to go back to for this scene.', { kind: 'warning' });
        changesSoon();
      } else if (err instanceof ApiError && err.code === 'version_busy') {
        app.toast('A job is working on this version. Try again when it finishes.', { kind: 'warning' });
        void checkActiveJob();
      } else {
        app.reportError(err, 'Could not revert the scene.');
      }
    } finally {
      if (reverting) {
        reverting = false;
        if (!destroyed) scheduleServerSave(); // edits made while the request was out are saved as usual
      }
    }
  }

  // ------------------------------------------------------------------------------------------
  // Save as a copy, shortcuts
  // ------------------------------------------------------------------------------------------
  /**
   * "Save as a copy": a new version of this lecture (POST /duplicate) that receives the unsaved changes and
   * opens in the editor; this version stays as it was last saved (its videos too).
   */
  async function saveAsCopy() {
    flushPending();
    const dirty = isDirty();
    const local = dirty ? localProblems(draft, checkOpts) : [];
    if (local.length) {
      app.toast(`Fix ${local.length} error${local.length === 1 ? '' : 's'} before saving a copy (see Issues).`, { kind: 'error' });
      jumpToIssue(local[0]);
      return;
    }
    // This version stays as it was last saved: no automatic save of it while the copy is asked for and made.
    copying = true;
    serverAutosave.cancel();
    try {
      const label = await promptDialog({
        title: 'Save as a copy',
        label: 'Name of the copy',
        value: `Copy of v${version.number ?? vid}`,
        maxLength: 255,
        message: dirty
          ? 'The copy gets your unsaved changes and opens in the editor. This version stays as it was last saved, with its videos.'
          : 'The copy opens in the editor. This version stays as it is, with its videos.',
        confirmLabel: 'Save copy',
      });
      if (label === null || destroyed) return;
      const sent = draft;
      let copy;
      try {
        const res = await post(`/api/versions/${vid}/duplicate`, { label });
        copy = res && res.version;
      } catch (err) {
        app.reportError(err, 'Could not save a copy.');
        return;
      }
      if (!copy || !Number.isInteger(copy.id)) return;
      if (dirty) {
        try {
          await put(`/api/versions/${copy.id}/screenplay`, { screenplay: sent, revision: copy.revision });
        } catch {
          // The copy exists with the last saved lecture: its editor offers these changes to restore.
          saveDraft(userId, copy.id, { revision: copy.revision, screenplay: sent, base });
          app.toast('The copy was made, but your changes could not be saved into it yet: the copy’s editor offers them.', { kind: 'warning', timeout: 8000 });
        }
      }
      if (destroyed) return;
      // The unsaved changes went into the copy: this version keeps its saved state (no leave question).
      flushPending();
      draft = base;
      autosave.cancel();
      serverAutosave.cancel();
      clearDraft(userId, vid);
      renderChrome();
      app.toast(`Saved as “${label || `a copy`}”. You are editing the copy now.`, { kind: 'success' });
      app.navigate(href('editor', { id: projectId, vid: copy.id }));
    } finally {
      copying = false;
      // cancelled or failed: automatic saving resumes; after a successful copy draft === base, so this only cancels
      if (!destroyed) scheduleServerSave();
    }
  }

  function openShortcuts() {
    openShortcutsDialog(SHORTCUTS);
  }

  // ------------------------------------------------------------------------------------------
  // Wiring
  // ------------------------------------------------------------------------------------------
  undoBtn.addEventListener('click', undo);
  redoBtn.addEventListener('click', redo);
  saveBtn.addEventListener('click', async () => {
    if (await save()) app.toast('Saved.', { kind: 'success', timeout: 2000 });
  });
  buildBtn.addEventListener('click', () => void build());
  renderBtn.addEventListener('click', () => void renderVideo());
  saveRetry.addEventListener('click', async () => {
    if (await save()) app.toast('Saved.', { kind: 'success', timeout: 2000 });
  });
  saveReview.addEventListener('click', async () => {
    if (await resolveConflict()) app.toast('Saved.', { kind: 'success', timeout: 2000 });
  });
  helpBtn.addEventListener('click', () => openShortcuts());

  /**
   * Editor shortcuts without a modifier ("?", Space, [ / ], Delete): listened for on the document so they also
   * work after a click on empty space, but only for the editor (or the page body), never while typing, while a
   * dialog is open, or inside the preview (its player has its own keys).
   * @param {KeyboardEvent} ev
   */
  const onDocKey = (ev) => {
    if (destroyed || ev.defaultPrevented || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const target = /** @type {HTMLElement | null} */ (ev.target instanceof HTMLElement ? ev.target : null);
    if (!target || (target !== document.body && !root.contains(target))) return;
    if (isEditableTarget(target) || document.querySelector('.modal-backdrop')) return;
    if (preview.el.contains(target)) return;
    if (ev.key === '?') {
      ev.preventDefault();
      openShortcuts();
    } else if (ev.key === ' ' || ev.key === 'Spacebar') {
      // form controls (checkboxes, radios, colour pickers), media, buttons, links, toggles and the timeline's
      // slider handle Space themselves
      if (target.closest('button, a, summary, select, input, textarea, label, video, audio, [role="slider"], [role="button"], [role="menuitem"], [role="checkbox"], [role="radio"], [role="switch"], [role="tab"], [role="option"], [contenteditable]')) return;
      ev.preventDefault();
      preview.togglePlay();
    } else if (ev.key === '[' || ev.key === ']') {
      ev.preventDefault();
      stepScene(ev.key === '[' ? -1 : 1);
    } else if (ev.key === 'Delete') {
      // not from the inspector or the side pane: their controls are about parts of the scene, not the scene
      if (inspectorHost.contains(target) || target.closest('.pane-side')) return;
      const i = E.sceneIndex(draft, selectedId || '');
      if (i < 0) return;
      ev.preventDefault();
      void deleteScene(i);
    }
  };
  document.addEventListener('keydown', onDocKey);

  /** @param {KeyboardEvent} ev */
  const onKey = (ev) => {
    const mod = ev.ctrlKey || ev.metaKey;
    if (!mod) return;
    const key = ev.key.toLowerCase();
    if (key === 's') {
      ev.preventDefault();
      void save().then((ok) => ok && app.toast('Saved.', { kind: 'success', timeout: 2000 }));
    } else if ((key === 'z' && !ev.shiftKey) && !isEditableTarget(ev.target)) {
      ev.preventDefault();
      undo();
    } else if (((key === 'z' && ev.shiftKey) || key === 'y') && !isEditableTarget(ev.target)) {
      ev.preventDefault();
      redo();
    }
  };
  root.addEventListener('keydown', onKey);

  app.setLeaveGuard(async () => {
    flushPending();
    if (!isDirty()) return true;
    const choice = await choiceDialog({
      title: 'Unsaved changes',
      message: 'Save your changes before leaving? (A local copy is kept either way.)',
      choices: [
        { label: 'Stay', value: 'stay', kind: 'ghost' },
        { label: 'Leave without saving', value: 'leave', kind: 'outline' },
        { label: 'Save and leave', value: 'save', kind: 'gold' },
      ],
    });
    if (choice === 'leave') return true;
    if (choice === 'save') return save();
    return false;
  });

  /**
   * Offer to restore this user's local draft of this version. Runs after the editor is on
   * screen (the dialog names the lecture) and gives up when the editor was left meanwhile.
   */
  async function offerRestore() {
    if (destroyed) return;
    const local = loadDraft(userId, vid);
    if (!local || deepEqual(local.screenplay, base)) return;
    const when = local.savedAt ? formatRelative(local.savedAt) : 'earlier';
    const sameBase = local.revision === revision;
    const title = String(local.screenplay.session_title || base.session_title || '').trim() || 'this lecture';
    const choice = await choiceDialog({
      title: 'Restore unsaved changes?',
      message: sameBase
        ? `You have unsaved changes to “${title}” (version ${version.number ?? vid}) from ${when}.`
        : `You have unsaved changes to “${title}” (version ${version.number ?? vid}) from ${when}, made on revision ${local.revision}; the lecture is now at revision ${revision}.`,
      details: sameBase ? [] : [local.base ? 'Restoring merges your changes into the newer version.' : 'Restoring may overwrite the newer changes.'],
      choices: [
        { label: 'Discard them', value: 'discard', kind: 'outline' },
        { label: 'Restore', value: 'restore', kind: 'gold' },
      ],
    });
    if (destroyed) return; // dismissed by a route change: keep the draft
    if (choice === 'restore') {
      flushPending();
      let restored;
      if (sameBase || !local.base) {
        restored = E.sanitizeScreenplayRefs(local.screenplay);
      } else {
        const merged = mergeDrafts(local.base, local.screenplay, base);
        restored = merged.screenplay;
        if (merged.conflicts.length) {
          // the clashes are the teacher's to review: nothing is saved automatically until a save by hand
          saveError = { kind: 'merged' };
          app.toast(`Restored with ${merged.conflicts.length} conflict(s); your edits were kept.`, { kind: 'warning', timeout: 8000 });
        }
      }
      undoStack.push({ sp: draft, label: 'Restore unsaved changes' });
      redoStack = [];
      draft = restored;
      if (selectedId && !E.findScene(draft, selectedId)) selectedId = draft.scenes[0] ? draft.scenes[0].id : null;
      restoredNote = true;
      renderAll();
      lintSoon();
      scheduleServerSave();
      preview.scheduleRefresh();
    } else if (choice === 'discard') {
      clearDraft(userId, vid);
    }
  }

  app.setTitle(`${draft.session_title || 'Lecture'} · Editor`);
  renderAll();
  strip.setTimeline(null, draft); // the scene list's estimates until the preview has a timeline
  if (!EDITABLE_STATUSES.has(version.status)) app.toast(`This version is ${version.status}; some actions may be unavailable.`, { kind: 'warning' });
  void runLint();
  void preview.refresh().then(() => {
    if (destroyed) return;
    const idx = E.sceneIndex(draft, selectedId || '');
    if (idx > 0 && selectedId) preview.seekSceneId(selectedId);
  });
  void checkActiveJob().finally(() => {
    if (!destroyed) schedulePoll();
  });
  void loadVisuals();
  void loadChanges();
  // After the app has attached and focused the editor (the dialog returns focus there).
  const restoreTimer = setTimeout(() => void offerRestore(), 0);

  return {
    destroy() {
      clearTimeout(restoreTimer);
      const last = inspector;
      inspector = null;
      if (last) last.destroy(); // flushes pending sub-editor edits into the draft (their scene)
      autosave.flush();
      destroyed = true;
      serverAutosave.cancel();
      changesSoon.cancel();
      lintSoon.cancel();
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = null;
      if (lintAbort) lintAbort.abort();
      if (busyWidget) busyWidget.destroy();
      downloads.destroy();
      moreMenu.destroy();
      sceneList.destroy();
      preview.destroy();
      strip.destroy();
      root.removeEventListener('keydown', onKey);
      document.removeEventListener('keydown', onDocKey);
      document.removeEventListener('visibilitychange', onVisibility);
      app.setLeaveGuard(null);
    },
  };
}
