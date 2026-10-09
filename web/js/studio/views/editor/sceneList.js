// @ts-check
/**
 * Scene list pane: type icons, titles, estimated durations, issue and stale badges; select
 * with click or Arrow keys; reorder by drag or keyboard (sortable handles); add (by type),
 * duplicate and delete the selected scene. `update()` re-renders the rows but keeps keyboard
 * focus on the scene button that had it (lint/save responses arrive while the user is
 * navigating the list with the arrow keys).
 */

import { h, clear } from '../../../shared/dom.js';
import { sortableList } from '../../components/sortable.js';
import { menuButton } from '../../components/menu.js';
import { button } from '../../components/form.js';
import { icon, SCENE_TYPE_ICONS } from '../../components/icons.js';
import { SCENE_TYPES, SCENE_TYPE_LABELS, sceneBeats } from '../../lib/screenplayEdit.js';
import { stripRichLite } from '../../lib/richLite.js';
import { estimateSpeechSeconds, formatDuration } from '../../util.js';
import { changeBadge } from './changes.js';

/**
 * Rough duration of a scene's narration (estimate + pauses + quiz countdown), without its minimum duration.
 * @param {any} scene
 */
export function estimateNarrationSeconds(scene) {
  let t = 0;
  for (const { beat } of sceneBeats(scene)) t += estimateSpeechSeconds(beat.narration || '') + Number(beat.pause_after || 0) + 0.15;
  if (scene.type === 'quiz_checkpoint') t += Number(scene.countdown_seconds || 8);
  if (scene.type === 'chapter_card' && t < 3) t = 3;
  return t;
}

/**
 * Rough scene duration for the editor: the narration estimate, or the scene's minimum duration
 * (`min_seconds`) when that is longer.
 * @param {any} scene
 */
export function estimateSceneSeconds(scene) {
  const t = estimateNarrationSeconds(scene);
  const hold = scene && typeof scene.min_seconds === 'number' && Number.isFinite(scene.min_seconds) ? scene.min_seconds : 0;
  return Math.max(t, hold);
}

/**
 * Scene list. Besides the issue and "needs build" badges, a row shows "Skipped" for a scene left out of the
 * video (`hidden`; it does not count toward the total), "Edited" / "New" / "Moved" compared with what Aadhi
 * generated (GET /changes, `changes`), and "now playing" for the scene the preview is playing (`playingId`).
 * `onAddPicture` (optional) adds "Picture from my library…" to the add menu.
 * @param {{ onSelect: (sceneId: string, focusInspector?: boolean) => void, onMove: (from: number, to: number) => void,
 *   onAdd: (type: string, afterIndex: number) => void, onDuplicate: (index: number) => void, onDelete: (index: number) => void,
 *   onAddPicture?: (afterIndex: number) => void }} opts
 */
export function createSceneList(opts) {
  /** @type {any} */
  let sp = { scenes: [] };
  /** @type {string | null} */
  let selectedId = null;
  /** @type {Map<string, { error: number, warning: number, info: number }>} */
  let counts = new Map();
  /** @type {Set<string>} */
  let stale = new Set();
  /** @type {Map<string, import('../../types.js').SceneChange>} */
  let changes = new Map();
  /** @type {string | null} */
  let playingId = null;

  const total = h('span', { class: 'muted small' });
  const addItems = SCENE_TYPES.map((t) => ({ label: SCENE_TYPE_LABELS[t], icon: SCENE_TYPE_ICONS[t], onClick: () => opts.onAdd(t, selectedIndex()) }));
  if (opts.onAddPicture) {
    const onAddPicture = opts.onAddPicture;
    addItems.push({ label: 'Picture from my library…', icon: 'upload', onClick: () => onAddPicture(selectedIndex()) });
  }
  const addMenu = menuButton('Add scene', addItems, { icon: 'plus', kind: 'outline', small: true, align: 'left' });
  const dupBtn = button('Duplicate scene', { kind: 'ghost', small: true, iconOnly: true, icon: 'copy' });
  const delBtn = button('Delete scene', { kind: 'ghost', small: true, iconOnly: true, icon: 'trash' });
  dupBtn.addEventListener('click', () => selectedIndex() >= 0 && opts.onDuplicate(selectedIndex()));
  delBtn.addEventListener('click', () => selectedIndex() >= 0 && opts.onDelete(selectedIndex()));

  const selectedIndex = () => (sp.scenes || []).findIndex((/** @type {any} */ s) => s.id === selectedId);

  const sortable = sortableList({
    items: /** @type {any[]} */ ([]),
    key: (s) => s.id,
    name: (s, i) => `scene ${i + 1}, ${stripRichLite(s.title) || SCENE_TYPE_LABELS[s.type]}`,
    label: 'Scenes',
    className: 'scene-list',
    render: (s, i) => sceneItem(s, i),
    onMove: (from, to) => opts.onMove(from, to),
  });

  /**
   * @param {any} s
   * @param {number} i
   */
  function sceneItem(s, i) {
    const c = counts.get(s.id);
    const selected = s.id === selectedId;
    const hidden = s.hidden === true;
    const playing = s.id === playingId;
    const mark = changeBadge(changes.get(s.id) || (changes.size && !changes.has(s.id) ? { scene_id: s.id, status: 'added', generated_index: null, current_index: null, fields_changed: [], history: 0 } : null));
    const title = stripRichLite(s.title || '') || (s.type === 'quiz_checkpoint' ? stripRichLite(s.question || '') : '') || SCENE_TYPE_LABELS[s.type];
    const select = h(
      'button',
      { type: 'button', class: ['scene-select', selected ? 'selected' : '', hidden ? 'is-hidden' : '', playing ? 'is-playing' : ''], 'aria-current': selected ? 'true' : undefined, dataset: { sceneIndex: String(i), sceneId: String(s.id) } },
      h('span', { class: 'scene-num' }, String(i + 1)),
      icon(SCENE_TYPE_ICONS[s.type] || 'board'),
      h('span', { class: 'scene-text' }, h('span', { class: 'scene-title' }, title), h('span', { class: 'scene-type muted' }, `${SCENE_TYPE_LABELS[s.type] || s.type} · ${hidden ? 'skipped' : formatDuration(estimateSceneSeconds(s))}`)),
      h(
        'span',
        { class: 'scene-badges' },
        playing ? h('span', { class: 'scene-playing', title: 'Now playing in the preview' }, icon('play', { size: 12 }), h('span', { class: 'sr-only' }, 'now playing')) : null,
        hidden ? h('span', { class: 'badge badge-outline badge-hidden', title: 'Skipped in the video (kept in the lecture)' }, 'Skipped') : null,
        mark ? h('span', { class: ['badge', 'badge-outline', 'badge-change'], dataset: { change: mark.status }, title: mark.title }, mark.label) : null,
        c && c.error ? h('span', { class: 'badge badge-bad', title: `${c.error} error(s)` }, h('span', { class: 'sr-only' }, 'errors: '), String(c.error)) : null,
        c && c.warning ? h('span', { class: 'badge badge-attention', title: `${c.warning} warning(s)` }, h('span', { class: 'sr-only' }, 'warnings: '), String(c.warning)) : null,
        stale.has(s.id) ? h('span', { class: 'badge badge-outline', title: 'Changed since the last build' }, icon('refresh', { size: 12 }), h('span', { class: 'sr-only' }, 'needs build')) : null,
      ),
    );
    select.addEventListener('click', () => opts.onSelect(s.id, false));
    select.addEventListener('keydown', (ev) => {
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        if (ev.altKey) return;
        ev.preventDefault();
        const next = i + (ev.key === 'ArrowDown' ? 1 : -1);
        const target = sp.scenes[next];
        if (!target) return;
        opts.onSelect(target.id, false);
        focusSelected();
      } else if (ev.key === 'Enter' && ev.shiftKey) {
        ev.preventDefault();
        opts.onSelect(s.id, true);
      }
    });
    return h('div', { class: ['scene-item', selected ? 'selected' : ''] }, select);
  }

  function focusSelected() {
    queueMicrotask(() => {
      const idx = selectedIndex();
      const btn = /** @type {HTMLElement | null} */ (el.querySelector(`.scene-select[data-scene-index="${idx}"]`));
      if (btn) btn.focus();
    });
  }

  const el = h(
    'section',
    { class: 'scene-pane', 'aria-label': 'Scenes' },
    h('div', { class: 'pane-head' }, h('h2', { class: 'pane-title' }, 'Scenes'), total),
    h('div', { class: 'row gap scene-tools' }, addMenu.el, dupBtn, delBtn),
    sortable.el,
    h('p', { class: 'muted small pane-help' }, 'Arrow keys move between scenes. Use the grip to reorder (Space, then arrows).'),
  );

  return {
    el,
    focusSelected,
    /**
     * @param {{ sp: any, selectedId: string | null, issueCounts: Map<string, { error: number, warning: number, info: number }>, staleScenes: Set<string>,
     *   changes?: Map<string, import('../../types.js').SceneChange>, playingId?: string | null }} next
     */
    update(next) {
      sp = next.sp;
      selectedId = next.selectedId;
      counts = next.issueCounts;
      stale = next.staleScenes;
      changes = next.changes || new Map();
      playingId = next.playingId || null;
      const scenes = sp.scenes || [];
      const shown = scenes.filter((/** @type {any} */ s) => s.hidden !== true);
      const secs = shown.reduce((/** @type {number} */ t, /** @type {any} */ s) => t + estimateSceneSeconds(s), 0);
      const skipped = scenes.length - shown.length;
      total.textContent = `${scenes.length} scenes${skipped ? ` (${skipped} skipped)` : ''} · ≈ ${formatDuration(secs)}`;
      dupBtn.disabled = selectedIndex() < 0;
      delBtn.disabled = selectedIndex() < 0 || (sp.scenes || []).length <= 1;
      const active = /** @type {HTMLElement | null} */ (document.activeElement);
      const focusedScene = active && el.contains(active) && active.classList.contains('scene-select') ? active.dataset.sceneId || null : null;
      sortable.update(sp.scenes || []);
      if (focusedScene !== null) {
        const again = [...el.querySelectorAll('.scene-select')].find((b) => /** @type {HTMLElement} */ (b).dataset.sceneId === focusedScene);
        if (again) /** @type {HTMLElement} */ (again).focus({ preventScroll: true });
      }
    },
    destroy() {
      addMenu.destroy();
      sortable.destroy();
      clear(el);
    },
  };
}
