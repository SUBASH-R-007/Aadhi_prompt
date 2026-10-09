// @ts-check
/**
 * Three-way merge for screenplay save conflicts (409 `revision_conflict`).
 *
 *   base   = the screenplay the local draft was started from (last fetched/saved revision)
 *   mine   = the local draft
 *   theirs = the screenplay now stored on the server
 *
 * "Keep mine" re-applies MY changes (relative to base) on top of theirs:
 *   - top-level fields (title, objectives, concept map, lexicon, ...) I changed win;
 *   - scenes are merged by id: scenes I modified/added win; scenes I deleted are removed;
 *     scenes only they changed/added/deleted keep their version;
 *   - scene order follows mine when I reordered, theirs otherwise (new scenes are inserted
 *     after their predecessor);
 *   - every overlapping change (both sides changed the same thing differently) is reported as
 *     a conflict so the teacher can review it.
 * Cross-references are sanitised afterwards so the result passes server validation.
 */

import { clone, deepEqual } from '../util.js';
import { sanitizeScreenplayRefs, syncChapterOrder } from './screenplayEdit.js';

/** @typedef {Record<string, any>} Screenplay */
/** @typedef {Record<string, any>} Scene */

/**
 * @typedef {object} Conflict
 * @property {'field' | 'scene'} kind
 * @property {string} key      field name or scene id
 * @property {string} message  human readable explanation
 */

/**
 * @typedef {object} ScreenplayDiff
 * @property {string[]} fields     top-level keys (except scenes) that changed
 * @property {string[]} added      scene ids added
 * @property {string[]} removed    scene ids removed
 * @property {string[]} modified   scene ids whose content changed
 * @property {boolean} reordered   relative order of surviving scenes changed
 */

/**
 * @param {Screenplay | null | undefined} sp
 * @returns {Map<string, Scene>}
 */
function sceneMap(sp) {
  return new Map(((sp && sp.scenes) || []).map((/** @type {Scene} */ s) => [s.id, s]));
}

/**
 * Describe what changed from `base` to `next`.
 * @param {Screenplay} base
 * @param {Screenplay} next
 * @returns {ScreenplayDiff}
 */
export function diffScreenplay(base, next) {
  const keys = new Set([...Object.keys(base || {}), ...Object.keys(next || {})]);
  keys.delete('scenes');
  const fields = [...keys].filter((k) => !deepEqual((base || {})[k], (next || {})[k])).sort();
  const b = sceneMap(base);
  const n = sceneMap(next);
  const added = [...n.keys()].filter((id) => !b.has(id));
  const removed = [...b.keys()].filter((id) => !n.has(id));
  const modified = [...n.keys()].filter((id) => b.has(id) && !deepEqual(b.get(id), n.get(id)));
  const commonBase = [...b.keys()].filter((id) => n.has(id));
  const commonNext = [...n.keys()].filter((id) => b.has(id));
  return { fields, added, removed, modified, reordered: !deepEqual(commonBase, commonNext) };
}

/**
 * True when the draft differs from the base in any way.
 * @param {Screenplay | null} base
 * @param {Screenplay | null} draft
 */
export function hasChanges(base, draft) {
  return !deepEqual(base, draft);
}

/**
 * Merge `mine` onto `theirs` relative to `base` (see module docs).
 * @param {Screenplay} base
 * @param {Screenplay} mine
 * @param {Screenplay} theirs
 * @returns {{ screenplay: Screenplay, conflicts: Conflict[], diff: ScreenplayDiff }}
 */
export function mergeScreenplays(base, mine, theirs) {
  /** @type {Conflict[]} */
  const conflicts = [];
  const diff = diffScreenplay(base, mine);
  /** @type {Screenplay} */
  const result = {};

  // -- top-level fields ------------------------------------------------------------
  // `chapters` is merged in two parts: chapter definitions (id/title/order) like any field,
  // scene membership per scene below (membership changes follow scene edits).
  const keys = new Set([...Object.keys(base || {}), ...Object.keys(mine || {}), ...Object.keys(theirs || {})]);
  keys.delete('scenes');
  keys.delete('chapters');
  {
    const b = chapterDefs(base);
    const m = chapterDefs(mine);
    const t = chapterDefs(theirs);
    if (!deepEqual(m, b) && !deepEqual(t, b) && !deepEqual(t, m)) {
      conflicts.push({ kind: 'field', key: 'chapters', message: 'Both you and the server changed the chapter list; your version was kept.' });
    }
    result.chapters = clone(deepEqual(m, b) ? t : m);
  }
  for (const key of keys) {
    const b = base[key];
    const m = mine[key];
    const t = theirs[key];
    if (deepEqual(m, b)) {
      if (t !== undefined) result[key] = clone(t);
      continue;
    }
    if (!deepEqual(t, b) && !deepEqual(t, m)) {
      conflicts.push({ kind: 'field', key, message: `Both you and the server changed "${key}"; your version was kept.` });
    }
    if (m !== undefined) result[key] = clone(m);
  }

  // -- scenes --------------------------------------------------------------------------
  const B = sceneMap(base);
  const M = sceneMap(mine);
  const T = sceneMap(theirs);
  const mineOrder = (mine.scenes || []).map((/** @type {Scene} */ s) => s.id);
  const theirOrder = (theirs.scenes || []).map((/** @type {Scene} */ s) => s.id);

  /** @type {Map<string, Scene>} */
  const chosen = new Map();
  /** Which side each chosen scene (and its chapter membership) comes from. @type {Map<string, Screenplay>} */
  const sideOf = new Map();
  // Scenes present in mine.
  for (const id of mineOrder) {
    const m = /** @type {Scene} */ (M.get(id));
    const b = B.get(id);
    const t = T.get(id);
    const myMembershipChanged = chapterOf(mine, id) !== chapterOf(base, id);
    if (!b) {
      // Added by me. Same id added by them too?
      if (t && !deepEqual(t, m)) {
        conflicts.push({ kind: 'scene', key: id, message: `Scene ${id} was also created on the server; your version was kept.` });
      }
      chosen.set(id, m);
      sideOf.set(id, mine);
    } else if (!deepEqual(m, b) || myMembershipChanged) {
      // Modified by me.
      if (!t) {
        conflicts.push({ kind: 'scene', key: id, message: `Scene ${id} was deleted on the server but you edited it; it was restored.` });
      } else if (!deepEqual(t, b) && !deepEqual(t, m)) {
        conflicts.push({ kind: 'scene', key: id, message: `Scene ${id} was changed on the server too; your version was kept.` });
      }
      chosen.set(id, m);
      sideOf.set(id, mine);
    } else if (t) {
      // Untouched by me: their version (possibly modified by them).
      chosen.set(id, t);
      sideOf.set(id, theirs);
    }
    // else: untouched by me and deleted by them -> dropped.
  }
  // Scenes only on their side (added by them), or present in base but deleted by me.
  for (const id of theirOrder) {
    if (chosen.has(id)) continue;
    const b = B.get(id);
    const t = /** @type {Scene} */ (T.get(id));
    if (!b) {
      chosen.set(id, t); // added by them
      sideOf.set(id, theirs);
    } else if (!M.has(id)) {
      // Deleted by me.
      if (!deepEqual(t, b)) {
        conflicts.push({ kind: 'scene', key: id, message: `You deleted scene ${id}, which was changed on the server; it stays deleted.` });
      }
    }
  }

  // -- order ---------------------------------------------------------------------------
  const skeleton = diff.reordered ? mineOrder : theirOrder;
  const extrasSource = diff.reordered ? theirOrder : mineOrder;
  /** @type {string[]} */
  const order = skeleton.filter((id) => chosen.has(id));
  const placed = new Set(order);
  extrasSource.forEach((id, i) => {
    if (!chosen.has(id) || placed.has(id)) return;
    // Insert after the nearest preceding scene (in its own list) that is already placed.
    let at = 0;
    for (let j = i - 1; j >= 0; j--) {
      const prev = extrasSource[j];
      const pos = order.indexOf(prev);
      if (pos >= 0) {
        at = pos + 1;
        break;
      }
    }
    order.splice(at, 0, id);
    placed.add(id);
  });

  result.scenes = order.map((id) => clone(/** @type {Scene} */ (chosen.get(id))));
  // Chapter membership: each scene keeps the chapter of the side its content came from.
  const chapterIds = new Set((result.chapters || []).map((/** @type {any} */ c) => c.id));
  /** @type {Map<string, string | null>} */
  const membership = new Map();
  for (const id of order) {
    const side = /** @type {Screenplay} */ (sideOf.get(id));
    const scene = /** @type {Scene} */ (chosen.get(id));
    const ch = chapterOf(side, id) ?? scene.chapter_id ?? null;
    membership.set(id, ch && chapterIds.has(ch) ? ch : null);
  }
  result.chapters = (result.chapters || []).map((/** @type {any} */ c) => ({
    ...c,
    scene_ids: order.filter((id) => membership.get(id) === c.id),
  }));
  const merged = syncChapterOrder(sanitizeScreenplayRefs(result));
  return { screenplay: merged, conflicts, diff };
}

/**
 * Chapter definitions without scene membership.
 * @param {Screenplay} sp
 */
function chapterDefs(sp) {
  return ((sp && sp.chapters) || []).map((/** @type {any} */ c) => {
    const { scene_ids: _ids, ...rest } = c;
    return rest;
  });
}

/**
 * Id of the chapter listing a scene (null when none).
 * @param {Screenplay} sp
 * @param {string} sceneId
 * @returns {string | null}
 */
function chapterOf(sp, sceneId) {
  const c = ((sp && sp.chapters) || []).find((/** @type {any} */ ch) => (ch.scene_ids || []).includes(sceneId));
  return c ? c.id : null;
}
