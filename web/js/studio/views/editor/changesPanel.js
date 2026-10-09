// @ts-check
/**
 * "Changes since Aadhi wrote it" (side pane, folded by default): the saved lecture compared with the
 * screenplay Aadhi generated (GET /api/versions/{vid}/changes). Lists edited scenes with what changed, scenes
 * added in the editor, moved scenes, scenes with earlier versions kept by "Regenerate scene", and removed
 * scenes, which can be restored. Revert and restore always ask first (the editor shows what goes back).
 * Hidden until the comparison has loaded; a version without a generated copy (imported, or made earlier)
 * says so.
 */

import { h, clear } from '../../../shared/dom.js';
import { button } from '../../components/form.js';
import { SCENE_TYPE_LABELS } from '../../lib/screenplayEdit.js';
import { stripRichLite } from '../../lib/richLite.js';
import { fieldWords } from './changes.js';

/** @typedef {import('../../types.js').SceneChange} SceneChange */
/** @typedef {import('../../types.js').VersionChanges} VersionChanges */

/**
 * @param {{ onSelect: (sceneId: string) => void, onRevert: (sceneId: string) => void, onRestore: (change: SceneChange) => void }} opts
 */
export function createChangesPanel(opts) {
  let open = false;
  const summary = h('summary', {}, 'Changes since Aadhi wrote it');
  const body = h('div', { class: 'changes-body' });
  const details = /** @type {HTMLDetailsElement} */ (h('details', { class: 'changes-details' }, summary, body));
  details.addEventListener('toggle', () => {
    open = details.open;
  });
  const el = h('section', { class: 'changes-panel', 'aria-label': 'Changes since Aadhi wrote it', hidden: true }, details);

  /**
   * @param {any} sp
   * @param {string} sceneId
   */
  function sceneName(sp, sceneId) {
    const scenes = (sp && sp.scenes) || [];
    const i = scenes.findIndex((/** @type {any} */ s) => s.id === sceneId);
    if (i < 0) return sceneId;
    const s = scenes[i];
    const title = stripRichLite(s.title || (s.type === 'quiz_checkpoint' ? s.question || '' : '')).trim() || SCENE_TYPE_LABELS[s.type] || s.id;
    return `Scene ${i + 1} · ${title}`;
  }

  return {
    el,
    /**
     * @param {VersionChanges | null} res   null: not loaded / not offered by the server (the panel hides)
     * @param {any} sp   the saved screenplay the comparison was made for (names and numbers)
     * @param {{ busy?: boolean }} [state]
     */
    update(res, sp, state = {}) {
      clear(body);
      el.hidden = !res;
      if (!res) return;
      details.open = open;
      if (!res.available) {
        summary.textContent = 'Changes since Aadhi wrote it';
        body.append(h('p', { class: 'muted small' }, 'Not available for this version: there is no copy of what Aadhi generated (it was imported, or made before copies were kept).'));
        return;
      }
      const all = Array.isArray(res.scenes) ? res.scenes : [];
      const edited = all.filter((c) => c.status === 'edited');
      const added = all.filter((c) => c.status === 'added');
      const moved = all.filter((c) => c.status === 'moved');
      const removed = all.filter((c) => c.status === 'removed');
      const older = all.filter((c) => c.status !== 'removed' && c.status !== 'edited' && (Number(c.history) || 0) > 0);
      const counts = [
        edited.length ? `${edited.length} edited` : '',
        added.length ? `${added.length} new` : '',
        moved.length ? `${moved.length} moved` : '',
        removed.length ? `${removed.length} removed` : '',
      ].filter(Boolean);
      summary.textContent = `Changes since Aadhi wrote it${counts.length ? ` (${counts.join(', ')})` : ''}`;
      if (!edited.length && !added.length && !moved.length && !removed.length && !older.length) {
        body.append(h('p', { class: 'muted small' }, 'The saved lecture is as Aadhi wrote it.'));
        return;
      }
      /** @type {HTMLElement[]} */
      const rows = [];
      /**
       * @param {SceneChange} c
       * @param {string} what
       * @param {HTMLElement | null} action
       */
      const row = (c, what, action) => {
        const show = h('button', { type: 'button', class: 'link-button changes-scene', dataset: { sceneId: c.scene_id } }, sceneName(sp, c.scene_id));
        show.addEventListener('click', () => opts.onSelect(c.scene_id));
        rows.push(h('li', { class: 'changes-row', dataset: { change: c.status } }, h('span', { class: 'changes-text' }, show, h('span', { class: 'muted small' }, ` — ${what}`)), action));
      };
      const revertBtn = (/** @type {SceneChange} */ c, /** @type {string} */ label) => {
        const b = button(label, { kind: 'ghost', small: true, icon: 'undo', disabled: !!state.busy });
        b.addEventListener('click', () => opts.onRevert(c.scene_id));
        return b;
      };
      for (const c of edited) {
        const words = fieldWords(c.fields_changed);
        row(c, words.length ? `edited: ${words.join(', ')}` : 'edited', revertBtn(c, 'Revert…'));
      }
      for (const c of older) row(c, 'regenerated; earlier versions kept', revertBtn(c, 'Restore…'));
      for (const c of added) row(c, 'added in the editor', null);
      for (const c of moved) row(c, Number.isInteger(c.generated_index) ? `moved (Aadhi placed it as scene ${/** @type {number} */ (c.generated_index) + 1})` : 'moved', null);
      for (const c of removed) {
        const restore = button('Restore…', { kind: 'ghost', small: true, icon: 'undo', disabled: !!state.busy });
        restore.addEventListener('click', () => opts.onRestore(c));
        const where = Number.isInteger(c.generated_index) ? `Scene ${/** @type {number} */ (c.generated_index) + 1} of Aadhi’s version` : 'A scene of Aadhi’s version';
        rows.push(h('li', { class: 'changes-row', dataset: { change: 'removed' } }, h('span', { class: 'changes-text' }, `${where} — removed`), restore));
      }
      body.append(h('ul', { class: 'plain-list changes-list' }, rows));
    },
  };
}
