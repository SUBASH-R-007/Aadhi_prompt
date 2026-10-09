// @ts-check
/**
 * Split a scene in two at a beat boundary: a pure screenplay transform (no model is asked to cut text).
 *
 *   splitProblem(sp, sceneId, beatIndex) -> null | plain-words reason
 *   splitScene(sp, sceneId, beatIndex)   -> { screenplay, scene }   (`scene` = the new second part)
 *
 * The cut is always between two main beats, never inside one: part A keeps beats [0, k), its id and its
 * visual (side panel, footage, sketch) and its minimum duration; part B gets a new id, beats [k, n) re-numbered
 * under it (`{B}-b1`, ...), and joins A's chapter right after it. Board scenes: B has A's type and takes the
 * board items that B's beats reveal or fill (A keeps everything visible from the start or revealed by its own
 * beats); highlights that would point across the cut are dropped by repairSceneRefs, and companion-sheet
 * entries that came from a moved item follow it. Real-world video and interactive scenes: B is a content scene
 * with an empty board (the footage or sketch stays with A). Quiz checkpoints, chapter cards and animations
 * (one animation step per beat) cannot be split. B starts visible or hidden like A.
 */

import { clone } from '../../util.js';
import { insertScene, isBoardScene, repairSceneRefs, truncateText, uniqueId, findScene, MAX_SCENES } from '../../lib/screenplayEdit.js';

/** Scene types whose beats are tied to something that cannot be cut (answer reveal, card, animation steps). */
const UNSPLITTABLE = /** @type {Record<string, string>} */ ({
  quiz_checkpoint: 'A quiz checkpoint cannot be split.',
  chapter_card: 'A chapter card cannot be split.',
  simulation: 'An animation cannot be split: each beat is one step of the animation.',
});

/**
 * Why the scene cannot be split before beat `beatIndex` (0-based index of the first beat of the second part),
 * or null when it can.
 * @param {Record<string, any>} sp
 * @param {string} sceneId
 * @param {number} beatIndex
 * @returns {string | null}
 */
export function splitProblem(sp, sceneId, beatIndex) {
  const scene = findScene(sp, sceneId);
  if (!scene) return 'This scene no longer exists.';
  if (UNSPLITTABLE[scene.type]) return UNSPLITTABLE[scene.type];
  const n = (scene.beats || []).length;
  if (n < 2) return 'A scene needs at least two beats to be split.';
  if (!Number.isInteger(beatIndex) || beatIndex < 1 || beatIndex >= n) return 'Choose a place between two beats.';
  if ((sp.scenes || []).length >= MAX_SCENES) return `A lecture can have at most ${MAX_SCENES} scenes.`;
  return null;
}

/**
 * True when the scene type can be split at all (the beat editor offers "Split here" only then).
 * @param {Record<string, any> | null | undefined} scene
 */
export function canSplitType(scene) {
  return !!scene && !UNSPLITTABLE[scene.type] && (scene.beats || []).length >= 2;
}

/**
 * @param {Record<string, any>} sp
 * @param {string} sceneId
 * @param {number} beatIndex   first beat of the second part (1 .. beats - 1)
 * @returns {{ screenplay: Record<string, any>, scene: Record<string, any> }}
 */
export function splitScene(sp, sceneId, beatIndex) {
  const problem = splitProblem(sp, sceneId, beatIndex);
  if (problem) throw new Error(problem);
  const index = (sp.scenes || []).findIndex((/** @type {any} */ s) => s.id === sceneId);
  const original = clone(sp.scenes[index]);
  const taken = new Set((sp.scenes || []).map((/** @type {any} */ s) => String(s.id)));
  const newId = uniqueId(`${original.id}-2`, taken);
  const board = isBoardScene(original);

  const keptBeats = original.beats.slice(0, beatIndex);
  const movedBeats = original.beats.slice(beatIndex);

  // Board items that go with the second part: revealed or filled by one of its beats.
  /** @type {Set<string>} */
  const movingItems = new Set();
  if (board) {
    for (const b of movedBeats) {
      if (b.board_item_id) movingItems.add(b.board_item_id);
      if (b.fill_item_id) movingItems.add(b.fill_item_id);
    }
  }
  /** @type {Map<string, string>} */
  const itemMap = new Map();
  /** @type {any[]} */
  const bBoard = [];
  if (board) {
    for (const item of original.board || []) {
      if (!movingItems.has(item.id)) continue;
      const nid = childId(newId, '-i', bBoard.length + 1);
      itemMap.set(item.id, nid);
      bBoard.push({ ...clone(item), id: nid });
    }
  }
  /** @type {Map<string, string>} */
  const beatMap = new Map();
  const bBeats = movedBeats.map((/** @type {any} */ b, /** @type {number} */ i) => {
    const nid = childId(newId, '-b', i + 1);
    beatMap.set(b.id, nid);
    const out = { ...clone(b), id: nid };
    if (board) {
      out.board_item_id = b.board_item_id ? itemMap.get(b.board_item_id) || null : null;
      out.fill_item_id = b.fill_item_id ? itemMap.get(b.fill_item_id) || null : null;
      out.highlight_item_ids = (b.highlight_item_ids || []).map((/** @type {string} */ id) => itemMap.get(id)).filter(Boolean);
    }
    return out;
  });
  // A legend row "appears when beat X speaks": X moved too (renamed) or the row is automatic again.
  for (const item of bBoard) {
    if (!Array.isArray(item.variables)) continue;
    item.variables = item.variables.map((/** @type {any} */ v) => {
      if (!v || !v.beat_id) return v;
      const mapped = beatMap.get(v.beat_id);
      if (mapped) return { ...v, beat_id: mapped };
      const { beat_id: _gone, ...rest } = v;
      return rest;
    });
  }

  /** @type {Record<string, any>} */
  const second = {
    id: newId,
    type: board ? original.type : 'content',
    concept_id: original.concept_id ?? null,
    chapter_id: original.chapter_id ?? null,
    title: original.title ? truncateText(`${original.title} (continued)`, 240) : '',
    subtitle: null,
    mascot_position: original.mascot_position || 'left',
    beats: bBeats,
    side_panel: null,
    objective_ids: [...(original.objective_ids || [])],
    intent: null,
    notes: '',
    board: bBoard,
  };
  if (original.hidden === true) second.hidden = true;

  /** @type {Record<string, any>} */
  const first = { ...original, beats: keptBeats };
  if (board) first.board = (original.board || []).filter((/** @type {any} */ item) => !movingItems.has(item.id));

  const scenes = sp.scenes.slice();
  scenes[index] = repairSceneRefs(first);
  /** @type {Record<string, any>} */
  let next = { ...sp, scenes };
  next = insertScene(next, repairSceneRefs(second), index + 1);
  next = moveCompanionRefs(next, original.id, newId, itemMap);
  return { screenplay: next, scene: /** @type {any} */ (findScene(next, newId)) };
}

/**
 * `{scene}-b{n}` / `{scene}-i{n}` within the 64-character slug limit (the scene part is shortened if needed).
 * @param {string} sceneId
 * @param {string} infix
 * @param {number} n
 */
function childId(sceneId, infix, n) {
  const suffix = `${infix}${n}`;
  return `${sceneId.slice(0, 64 - suffix.length)}${suffix}`;
}

/**
 * Companion-sheet entries derived from a board item ("<scene>/<item>") follow the item into the new scene.
 * @param {Record<string, any>} sp
 * @param {string} fromScene
 * @param {string} toScene
 * @param {Map<string, string>} itemMap
 */
function moveCompanionRefs(sp, fromScene, toScene, itemMap) {
  const sheet = sp.companion_sheet;
  if (!sheet || typeof sheet !== 'object' || itemMap.size === 0) return sp;
  let changed = false;
  /** @type {Record<string, any>} */
  const out = { ...sheet };
  for (const [key, list] of Object.entries(sheet)) {
    if (!Array.isArray(list)) continue;
    out[key] = list.map((entry) => {
      const ref = entry && typeof entry.source_item_id === 'string' ? entry.source_item_id : null;
      if (!ref || !ref.startsWith(`${fromScene}/`)) return entry;
      const mapped = itemMap.get(ref.slice(fromScene.length + 1));
      if (!mapped) return entry;
      changed = true;
      return { ...entry, source_item_id: `${toScene}/${mapped}` };
    });
  }
  return changed ? { ...sp, companion_sheet: out } : sp;
}
