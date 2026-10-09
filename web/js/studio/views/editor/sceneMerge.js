// @ts-check
/**
 * Three-way merge for the editor (save conflicts, a job's changes, an old local draft): lib/conflict.js
 * `mergeScreenplays`, then a field-level pass over the scenes that BOTH sides changed.
 *
 * mergeScreenplays reports any scene changed on both sides as a conflict (mine wins). Here such a scene is
 * merged field by field instead: a top-level field changed on one side only takes that side's value; beats,
 * reveal beats and board items are merged by id the same way (one side may also add, remove or reorder them,
 * as long as the other side kept the list's order and members). Only when both sides changed the same field,
 * beat or item differently (or both changed a list's order or members, or the chapter) does the scene stay a
 * conflict with the teacher's version kept, as before. Merged scenes have their references repaired, and the
 * screenplay's cross-references are sanitised again.
 */

import { clone, deepEqual } from '../../util.js';
import { mergeScreenplays } from '../../lib/conflict.js';
import { repairSceneRefs, sanitizeScreenplayRefs } from '../../lib/screenplayEdit.js';

/** Scene lists merged item by item (by `id`). */
const ID_LISTS = new Set(['beats', 'reveal_beats', 'board']);

/** Marker for "both sides changed this differently". */
const CLASH = Symbol('clash');

/**
 * @param {Record<string, any>} base
 * @param {Record<string, any>} mine
 * @param {Record<string, any>} theirs
 * @returns {{ screenplay: Record<string, any>, conflicts: import('../../lib/conflict.js').Conflict[], diff: import('../../lib/conflict.js').ScreenplayDiff }}
 */
export function mergeDrafts(base, mine, theirs) {
  const merged = mergeScreenplays(base, mine, theirs);
  const sceneConflicts = merged.conflicts.filter((c) => c.kind === 'scene');
  if (!sceneConflicts.length) return merged;
  const B = byId(base);
  const M = byId(mine);
  const T = byId(theirs);
  /** @type {Map<string, any>} */
  const resolved = new Map();
  for (const c of sceneConflicts) {
    const b = B.get(c.key);
    const m = M.get(c.key);
    const t = T.get(c.key);
    if (!b || !m || !t) continue; // added on both sides, or deleted on one: a real conflict
    const scene = mergeScene(b, m, t);
    if (scene) resolved.set(c.key, scene);
  }
  if (!resolved.size) return merged;
  const scenes = (merged.screenplay.scenes || []).map((/** @type {any} */ s) => (resolved.has(s.id) ? resolved.get(s.id) : s));
  return {
    ...merged,
    screenplay: sanitizeScreenplayRefs({ ...merged.screenplay, scenes }),
    conflicts: merged.conflicts.filter((c) => !(c.kind === 'scene' && resolved.has(c.key))),
  };
}

/**
 * Field-level merge of one scene changed on both sides; null when the changes overlap.
 * @param {Record<string, any>} b
 * @param {Record<string, any>} m
 * @param {Record<string, any>} t
 * @returns {Record<string, any> | null}
 */
export function mergeScene(b, m, t) {
  // A changed type or chapter changes what the scene is (and the chapter lists merged elsewhere): not here.
  if (m.type !== t.type || b.type !== m.type) return null;
  if ((m.chapter_id ?? null) !== (t.chapter_id ?? null) || (b.chapter_id ?? null) !== (m.chapter_id ?? null)) return null;
  /** @type {Record<string, any>} */
  const out = {};
  const keys = new Set([...Object.keys(b), ...Object.keys(m), ...Object.keys(t)]);
  for (const key of keys) {
    const value = ID_LISTS.has(key) ? mergeList(b[key], m[key], t[key]) : pick(b[key], m[key], t[key]);
    if (value === CLASH) return null;
    if (value !== undefined) out[key] = clone(value);
  }
  return repairSceneRefs(out);
}

/**
 * The side that changed a value, or CLASH when both changed it differently.
 * @param {any} b
 * @param {any} m
 * @param {any} t
 */
function pick(b, m, t) {
  if (deepEqual(m, b)) return t;
  if (deepEqual(t, b) || deepEqual(t, m)) return m;
  return CLASH;
}

/**
 * Merge two edited copies of an id-keyed list. One side may change the order or the members; when both did,
 * the lists clash. An entry removed by one side and changed by the other also clashes.
 * @param {any[] | undefined} b
 * @param {any[] | undefined} m
 * @param {any[] | undefined} t
 * @returns {any[] | undefined | typeof CLASH}
 */
function mergeList(b, m, t) {
  if (!Array.isArray(b) || !Array.isArray(m) || !Array.isArray(t)) return pick(b, m, t);
  const ids = (/** @type {any[]} */ list) => list.map((x) => (x && typeof x === 'object' ? x.id : undefined));
  if ([b, m, t].some((list) => ids(list).some((id) => typeof id !== 'string') || new Set(ids(list)).size !== list.length)) return pick(b, m, t);
  const bIds = ids(b);
  const mineShaped = !deepEqual(ids(m), bIds);
  const theirsShaped = !deepEqual(ids(t), bIds);
  if (mineShaped && theirsShaped && !deepEqual(ids(m), ids(t))) return CLASH;
  const order = mineShaped ? ids(m) : ids(t);
  const Bm = new Map(b.map((x) => [x.id, x]));
  const Mm = new Map(m.map((x) => [x.id, x]));
  const Tm = new Map(t.map((x) => [x.id, x]));
  // Entries the shaping side removed must be unchanged on the other side.
  for (const id of bIds) {
    if (order.includes(id)) continue;
    const other = mineShaped ? Tm.get(id) : Mm.get(id);
    if (other !== undefined && !deepEqual(other, Bm.get(id))) return CLASH;
  }
  /** @type {any[]} */
  const out = [];
  for (const id of order) {
    const bi = Bm.get(id);
    const mi = Mm.get(id);
    const ti = Tm.get(id);
    if (bi === undefined) {
      // Added: by one side, or by both (then it must be the same entry).
      if (mi !== undefined && ti !== undefined && !deepEqual(mi, ti)) return CLASH;
      out.push(mi !== undefined ? mi : ti);
      continue;
    }
    if (mi === undefined || ti === undefined) {
      out.push(mi !== undefined ? mi : ti);
      continue;
    }
    const v = pick(bi, mi, ti);
    if (v === CLASH) return CLASH;
    out.push(v);
  }
  return out;
}

/**
 * @param {Record<string, any> | null | undefined} sp
 * @returns {Map<string, any>}
 */
function byId(sp) {
  return new Map(((sp && sp.scenes) || []).map((/** @type {any} */ s) => [s.id, s]));
}
