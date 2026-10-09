// @ts-check
/**
 * Editor preview: embeds the lecture Player (web/js/player/player.js, imported lazily) in
 * 'preview' mode. Saved + built versions use GET /timeline; unsaved drafts (or stale builds)
 * use POST /timeline/preview (estimated timings where no audio exists yet). Seeks to the
 * selected scene. Every refresh destroys the previous player (WebGL contexts, audio, timers).
 * After each scene change the board the player laid out is measured (`onMeasure`: its fit scale and
 * whether it still overflows at the smallest size). The stage is the same fixed 1920x1080 layout the MP4
 * render uses, so the measurement is what the video will show; nothing is measured while hidden.
 * Below the player, "Timing of scene N" (read-only, folded) lists when each beat of the shown scene starts
 * and the moments placed on spoken words (web/js/player/syncSummary.js over TimedScene.sync_cues), marked
 * "estimated" while the timings come from an estimate instead of the narration audio.
 * For the editor's timeline strip: `onTimeline` receives each loaded timeline (null when the preview is
 * blocked or failed), `onTimeUpdate` the playhead (at most once per animation frame) and `onPlayState`
 * play / pause; `seek(t)`, `togglePlay()`, `isPlaying()` and `seekSceneId(id)` drive the player. Scenes
 * are followed by id across refreshes (a skipped scene is not in the timeline, so draft and timeline
 * positions can differ).
 */

import { h, clear } from '../../../shared/dom.js';
import { get, post, ApiError } from '../../../shared/api.js';
import { button, checkbox } from '../../components/form.js';
import { loadPref, savePref } from '../../lib/draftStore.js';
import { errorMessage } from '../../errors.js';
import { measureBoard } from './quality.js';

/** Measure after the scene's first layout and again once TeX, code colouring and images settled (ms). */
const MEASURE_DELAYS = [400, 1600];

/**
 * @typedef {object} PreviewSource
 * @property {'built' | 'draft'} mode
 * @property {Record<string, any> | null} screenplay   draft (mode 'draft')
 * @property {string | null} blockedReason            e.g. local validation errors
 */

/**
 * @typedef {{ sceneId: string, index: number, fit: number, overflow: boolean, source: Record<string, any> | null }} BoardMeasure
 *   `source`: the screenplay the shown timeline was built from (null = the built lecture)
 */

/**
 * @typedef {object} PreviewHandle
 * @property {HTMLElement} el
 * @property {() => Promise<void>} refresh
 * @property {() => void} scheduleRefresh
 * @property {(index: number) => void} seekScene      by position in the shown timeline
 * @property {(sceneId: string) => boolean} seekSceneId   false when the scene is not in the shown timeline
 * @property {(t: number) => void} seek               absolute seconds
 * @property {() => void} togglePlay
 * @property {() => boolean} isPlaying
 * @property {() => string | null} sceneId   the scene the player shows (null for the intro or without a player)
 * @property {() => void} pause
 * @property {() => void} destroy
 */

/**
 * @param {{ versionId: number, getSource: () => PreviewSource, onSceneChange?: (index: number, sceneId: string | null) => void,
 *   onMeasure?: (m: BoardMeasure) => void, onTimeline?: (timeline: any | null) => void,
 *   onTimeUpdate?: (t: number, playing: boolean) => void, onPlayState?: (playing: boolean) => void,
 *   sceneNumber?: (sceneId: string) => number }} opts
 *   `sceneNumber`: the scene's number as the scene list shows it (skipped scenes counted; 0 when unknown); without
 *   it, scenes are numbered by their place in the shown timeline
 * @returns {PreviewHandle}
 */
export function createPreviewPane(opts) {
  const box = h('div', { class: 'preview-box', tabindex: '-1', 'aria-label': 'Lecture preview' });
  const status = h('div', { class: 'preview-status', role: 'status', 'aria-live': 'polite' });
  const refreshBtn = button('Refresh preview', { kind: 'outline', small: true, icon: 'refresh' });
  const auto = checkbox({ label: 'Auto-refresh after edits', checked: loadPref('previewAuto', true), onChange: (v) => savePref('previewAuto', v) });
  const openTab = h('a', { class: 'btn btn-ghost btn-sm', href: `/preview/${opts.versionId}`, target: '_blank', rel: 'noopener noreferrer' }, 'Open full preview');
  const syncBox = /** @type {HTMLDetailsElement} */ (h('details', { class: 'preview-sync', hidden: true }));
  const el = h('section', { class: 'preview-pane', 'aria-label': 'Preview' }, h('div', { class: 'preview-frame' }, box), h('div', { class: 'row gap wrap preview-tools' }, refreshBtn, auto, openTab), status, syncBox);

  /** @type {any} */
  let player = null;
  /** @type {any} */
  let controls = null;
  /** @type {Array<() => void>} */
  let unsubs = [];
  /** @type {any} */
  let PlayerMod = null;
  /** @type {any} */
  let ControlsMod = null;
  /** @type {any} */
  let SyncMod = null;
  let destroyed = false;
  let token = 0;
  /** @type {ReturnType<typeof setTimeout> | null} */
  let timer = null;
  let pendingSeek = -1;
  /** A scene to show once the next timeline is loaded (by id: positions change when scenes are skipped). @type {string | null} */
  let pendingSeekId = null;
  /** Latest player time waiting for the next animation frame (onTimeUpdate is called at most once per frame). */
  let frameTime = -1;
  let framePending = false;
  /** @type {AbortController | null} */
  let inflight = null;
  /** @type {ReturnType<typeof setTimeout>[]} */
  let measureTimers = [];
  /** @type {Record<string, any> | null} */
  let shownSource = null;

  const clearMeasure = () => {
    for (const t of measureTimers) clearTimeout(t);
    measureTimers = [];
  };

  /**
   * Measure the board of the scene the player shows (only board scenes have one).
   * @param {number} index
   * @param {any} scene   TimedScene
   */
  function scheduleMeasure(index, scene) {
    clearMeasure();
    if (!opts.onMeasure || !scene || typeof scene.scene_id !== 'string' || index < 0) return;
    const my = token;
    const source = shownSource;
    for (const delay of MEASURE_DELAYS) {
      measureTimers.push(
        setTimeout(() => {
          if (destroyed || my !== token || !player || player.sceneIndex !== index) return;
          const m = measureBoard(box);
          if (m && opts.onMeasure) opts.onMeasure({ sceneId: scene.scene_id, index, fit: m.fit, overflow: m.overflow, source });
        }, delay),
      );
    }
  }

  /**
   * The shown scene's timing in words (syncSummary); hidden for the intro or without the module.
   * @param {number} index
   * @param {any} scene   TimedScene
   */
  function showSync(index, scene) {
    clear(syncBox);
    const summary = SyncMod && typeof SyncMod.syncSummary === 'function' && scene && index >= 0 ? SyncMod.syncSummary(scene) : null;
    syncBox.hidden = !summary;
    if (!summary) return;
    const extra = summary.moments.length - summary.keyMoments.length;
    syncBox.append(
      h('summary', {}, `Timing of scene ${(opts.sceneNumber && opts.sceneNumber(String(scene.scene_id || ''))) || index + 1} `, summary.source === 'estimate' ? h('span', { class: 'badge badge-outline', title: summary.timing }, 'estimated') : null),
      h('p', { class: 'muted small' }, `Timing ${summary.timing}.`),
      h('ul', { class: 'plain-list small', 'aria-label': 'When each beat starts', dataset: { sync: 'beats' } }, summary.beats.map((/** @type {any} */ b, /** @type {number} */ n) => h('li', {}, `Beat ${n + 1}: ${b.when}`))),
      summary.moments.length
        ? h('ul', { class: 'plain-list small', 'aria-label': 'Moments placed on spoken words', dataset: { sync: 'moments' } }, summary.keyMoments.map((/** @type {any} */ m) => h('li', {}, `${m.when}: ${m.what}, ${m.anchor}`)))
        : h('p', { class: 'muted small' }, 'No moments placed on spoken words: board items appear when their beat starts.'),
    );
    if (extra > 0) syncBox.append(h('p', { class: 'muted small' }, `${extra} more moment${extra === 1 ? '' : 's'} in this scene.`));
  }

  const teardown = () => {
    clearMeasure();
    showSync(-1, null);
    for (const u of unsubs) u();
    unsubs = [];
    if (controls) {
      try {
        controls.destroy();
      } catch {
        /* ignore */
      }
    }
    if (player) {
      try {
        player.destroy();
      } catch {
        /* ignore */
      }
    }
    controls = null;
    player = null;
  };

  /** Id of the scene the player shows (null for the intro or without a player). */
  function shownSceneId() {
    if (!player || !player.timeline || typeof player.sceneIndex !== 'number' || player.sceneIndex < 0) return null;
    const s = player.timeline.scenes[player.sceneIndex];
    return s && typeof s.scene_id === 'string' ? s.scene_id : null;
  }

  /**
   * Hand the player's time to `onTimeUpdate` once per animation frame (the player reports every frame;
   * the strip only moves its playhead).
   * @param {number} t
   */
  function queueTime(t) {
    frameTime = t;
    if (framePending) return;
    framePending = true;
    const run = () => {
      framePending = false;
      if (destroyed || !opts.onTimeUpdate) return;
      opts.onTimeUpdate(frameTime, !!player && !player.paused);
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
    else setTimeout(run, 16);
  }

  async function loadModules() {
    if (PlayerMod) return true;
    try {
      const base = new URL('../../../player/', import.meta.url);
      PlayerMod = await import(/* @vite-ignore */ new URL('player.js', base).href);
      try {
        ControlsMod = await import(/* @vite-ignore */ new URL('controls.js', base).href);
      } catch {
        ControlsMod = null;
      }
      try {
        SyncMod = await import(/* @vite-ignore */ new URL('syncSummary.js', base).href);
      } catch {
        SyncMod = null;
      }
      return true;
    } catch (err) {
      console.warn('player unavailable', err);
      return false;
    }
  }

  async function refresh() {
    if (timer) clearTimeout(timer);
    timer = null;
    const my = ++token;
    if (inflight) inflight.abort();
    const src = opts.getSource();
    if (src.blockedReason) {
      status.textContent = src.blockedReason;
      if (opts.onTimeline) opts.onTimeline(null);
      return;
    }
    status.textContent = 'Building preview…';
    refreshBtn.disabled = true;
    const ac = new AbortController();
    inflight = ac;
    try {
      const [ok, timeline] = await Promise.all([
        loadModules(),
        src.mode === 'built'
          ? get(`/api/versions/${opts.versionId}/timeline`, { signal: ac.signal })
          : post(`/api/versions/${opts.versionId}/timeline/preview`, { screenplay: src.screenplay }, { signal: ac.signal }),
      ]);
      if (destroyed || my !== token) return;
      if (!ok || !PlayerMod || typeof PlayerMod.Player !== 'function') {
        status.textContent = 'The player could not be loaded. Use “Open full preview”.';
        return;
      }
      const keepIndex = pendingSeek >= 0 ? pendingSeek : player && typeof player.sceneIndex === 'number' && player.sceneIndex >= 0 ? player.sceneIndex : -1;
      const keepId = pendingSeekId || shownSceneId();
      teardown();
      clear(box);
      shownSource = src.mode === 'built' ? null : src.screenplay;
      player = new PlayerMod.Player(box, { mode: 'preview', analytics: null, captions: true, autoplay: false });
      await player.load(timeline);
      if (destroyed || my !== token) return;
      if (ControlsMod && typeof ControlsMod.Controls === 'function') {
        controls = new ControlsMod.Controls(box, player, { keyboardTarget: box });
      }
      if (typeof player.on === 'function') {
        unsubs.push(player.on('scenechange', (/** @type {any} */ d) => opts.onSceneChange && d && typeof d.index === 'number' && opts.onSceneChange(d.index, d.scene && typeof d.scene.scene_id === 'string' ? d.scene.scene_id : null)));
        if (opts.onTimeUpdate) unsubs.push(player.on('timeupdate', (/** @type {any} */ d) => d && typeof d.t === 'number' && queueTime(d.t)));
        if (opts.onPlayState) {
          const onPlayState = opts.onPlayState;
          unsubs.push(player.on('play', () => onPlayState(true)));
          unsubs.push(player.on('pause', () => onPlayState(false)));
          unsubs.push(player.on('ended', () => onPlayState(false)));
        }
        unsubs.push(player.on('scenechange', (/** @type {any} */ d) => d && typeof d.index === 'number' && scheduleMeasure(d.index, d.scene)));
        unsubs.push(player.on('scenechange', (/** @type {any} */ d) => d && typeof d.index === 'number' && showSync(d.index, d.scene)));
        unsubs.push(player.on('error', (/** @type {any} */ d) => console.warn('preview player:', d && d.message)));
      }
      const keepAt = keepId ? (timeline.scenes || []).findIndex((/** @type {any} */ s) => s && s.scene_id === keepId) : -1;
      if (keepAt >= 0) player.seekScene(keepAt);
      else if (keepIndex >= 0 && keepIndex < (timeline.scenes || []).length) player.seekScene(keepIndex);
      pendingSeek = -1;
      pendingSeekId = null;
      if (opts.onTimeline) opts.onTimeline(timeline);
      if (opts.onPlayState) opts.onPlayState(false);
      if (opts.onTimeUpdate && typeof player.currentTime === 'number') opts.onTimeUpdate(player.currentTime, false);
      if (typeof player.sceneIndex === 'number' && player.sceneIndex >= 0) {
        scheduleMeasure(player.sceneIndex, (timeline.scenes || [])[player.sceneIndex]);
        showSync(player.sceneIndex, (timeline.scenes || [])[player.sceneIndex]);
      }
      status.textContent = timeline.estimated ? 'Preview with estimated timings (voice not generated yet for edited beats).' : 'Preview of the built lecture.';
    } catch (err) {
      if (destroyed || my !== token) return;
      if (err instanceof Error && err.name === 'AbortError') return;
      if (opts.onTimeline) opts.onTimeline(null);
      if (err instanceof ApiError && err.status === 404) status.textContent = 'Not built yet: showing nothing until the draft is valid or the lecture is built.';
      else if (err instanceof ApiError && err.status === 422) status.textContent = 'The draft has errors; fix them to refresh the preview.';
      else status.textContent = `Preview unavailable: ${errorMessage(err)}`;
    } finally {
      if (inflight === ac) inflight = null;
      if (my === token) refreshBtn.disabled = false;
    }
  }

  refreshBtn.addEventListener('click', () => void refresh());

  return {
    el,
    refresh,
    scheduleRefresh() {
      if (!auto.input.checked) {
        status.textContent = 'Preview is out of date. Refresh to see your changes.';
        return;
      }
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void refresh(), 1800);
    },
    seekScene(index) {
      if (player && typeof player.seekScene === 'function' && player.timeline && index < player.timeline.scenes.length) {
        try {
          player.seekScene(index);
        } catch {
          /* ignore */
        }
      } else {
        pendingSeek = index;
      }
    },
    seekSceneId(sceneId) {
      const scenes = player && player.timeline && Array.isArray(player.timeline.scenes) ? player.timeline.scenes : null;
      if (!scenes) {
        pendingSeekId = sceneId;
        return false;
      }
      const index = scenes.findIndex((/** @type {any} */ s) => s && s.scene_id === sceneId);
      if (index < 0) return false;
      try {
        player.seekScene(index);
      } catch {
        /* ignore */
      }
      return true;
    },
    seek(t) {
      if (!player || typeof player.seek !== 'function') return;
      try {
        player.seek(t);
      } catch {
        /* ignore */
      }
    },
    togglePlay() {
      if (!player || typeof player.toggle !== 'function') return;
      try {
        player.toggle();
      } catch {
        /* ignore */
      }
    },
    isPlaying() {
      return !!player && player.paused === false;
    },
    sceneId() {
      return shownSceneId();
    },
    pause() {
      if (player && player.paused === false && typeof player.pause === 'function') player.pause();
    },
    destroy() {
      destroyed = true;
      clearMeasure();
      if (timer) clearTimeout(timer);
      if (inflight) inflight.abort();
      teardown();
    },
  };
}
