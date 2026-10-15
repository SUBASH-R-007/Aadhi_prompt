// @ts-check
/**
 * Scene state for the Dynamic stage (DYNAMIC_VIDEO_DESIGN §7.3 seek step 2): the classic `schedule.sceneStateAt`
 * extended with `TimedScene.events`, plus the entrance time of every board item, the background's drift clock
 * (frozen during holds, R-55) and the caption on screen. Pure functions of (timeline, t); no DOM.
 */

import { planScene, sceneStateAt, selectCue } from '../player/schedule.js';

/** @typedef {import('../shared/types.js').Timeline} Timeline */
/** @typedef {import('../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../shared/types.js').SceneState} SceneState */

/** A reveal starts 120 ms before its anchor (R-17); beat-based reveals use the beat start as the anchor. */
export const REVEAL_LEAD = 0.12;
/** Items on screen from the start enter after the title, staggered (§4.8: 60-120 ms per item). */
export const INITIAL_DELAY = 0.35;
export const INITIAL_STAGGER = 0.09;

/**
 * @typedef {object} ItemTiming
 * @property {string} id
 * @property {number} enter     scene-relative start of the entrance
 * @property {number | null} fill  when a blank item is filled (null: never)
 * @property {number} order     position in board order
 * @property {boolean} initial  no beat or event reveals it: it enters with the scene, after the title
 */

/** @type {WeakMap<object, ItemTiming[]>} */
const timingCache = new WeakMap();

/**
 * Entrance times of a scene's board items, in entrance order (ties keep board order). A `reveal` / `fill` event
 * (events.py, B2) wins over the beat that reveals the item; an item no beat reveals enters at the scene start.
 * @param {TimedScene} scene
 * @returns {ItemTiming[]}
 */
export function itemTimings(scene) {
  const cached = timingCache.get(scene);
  if (cached) return cached;
  const plan = planScene(scene);
  /** @type {Map<string, number>} */
  const revealEvents = new Map();
  /** @type {Map<string, number>} */
  const fillEvents = new Map();
  for (const e of scene.events || []) {
    if (!e || typeof e.t !== 'number' || !Number.isFinite(e.t) || typeof e.target !== 'string') continue;
    const id = e.target.split(':')[0];
    const map = e.kind === 'reveal' ? revealEvents : e.kind === 'fill' ? fillEvents : null;
    if (map && (!map.has(id) || /** @type {number} */ (map.get(id)) > e.t)) map.set(id, e.t);
  }
  /** @type {ItemTiming[]} */
  const out = [];
  let initial = 0;
  (scene.board || []).forEach((item, order) => {
    let enter = revealEvents.get(item.id);
    let unanchored = false;
    if (enter === undefined) {
      const at = plan.revealAt.get(item.id);
      unanchored = at === undefined;
      enter = unanchored ? INITIAL_DELAY + INITIAL_STAGGER * initial++ : Math.max(0, /** @type {number} */ (at) - REVEAL_LEAD);
    }
    const fillAt = fillEvents.has(item.id) ? fillEvents.get(item.id) : plan.fillAt.get(item.id);
    out.push({ id: item.id, enter, fill: fillAt === undefined ? null : /** @type {number} */ (fillAt), order, initial: unanchored });
  });
  out.sort((a, b) => a.enter - b.enter || a.order - b.order);
  timingCache.set(scene, out);
  return out;
}

/**
 * The scene state at scene-relative t (schedule.sceneStateAt; events add their reveals and fills).
 * @param {TimedScene} scene
 * @param {number} t
 * @returns {SceneState}
 */
export function dynamicStateAt(scene, t) {
  const state = sceneStateAt(scene, t);
  if (!(scene.events && scene.events.length)) return state;
  const visible = new Set(state.visibleItemIds);
  const filled = new Set(state.filledItemIds);
  for (const it of itemTimings(scene)) {
    if (it.enter <= t) visible.add(it.id);
    else visible.delete(it.id);
    if (it.fill !== null && it.fill <= t && visible.has(it.id)) filled.add(it.id);
  }
  return { ...state, visibleItemIds: visible, filledItemIds: filled };
}

// ---------------------------------------------------------------------------------------------
// Chapters
// ---------------------------------------------------------------------------------------------

/**
 * The title of the chapter a scene starting at `start` is in: the last chapter of `Timeline.chapters`, in its order,
 * that starts at or before the scene (1 ms tolerance); '' without one. B1's board_generic kicker rule, now computed by
 * the stage and handed to the template (templates never read the timeline, B2 plan §1.4); the same rule as
 * aadhi/compose/motion/chunks.py `_kicker_chapters`, which hashes this title into every chunk key that shows the scene,
 * and aadhi/compose/presentation.py `chapter_indices` (the palette slot of a scene).
 * @param {Timeline} timeline
 * @param {number} start  the scene's start (absolute seconds)
 */
export function chapterTitleAt(timeline, start) {
  let title = '';
  for (const c of timeline.chapters || []) if (Number(c.start) <= Number(start) + 1e-3) title = String(c.title || '');
  return title;
}

// ---------------------------------------------------------------------------------------------
// Background drift clock (R-55)
// ---------------------------------------------------------------------------------------------

/** @type {WeakMap<object, {start: number, end: number}[]>} */
const frozenCache = new WeakMap();

/**
 * Absolute intervals during which the background is frozen: from `start + look.background.frozen_at` to the end
 * of every scene whose look has `frozen_at` set (a hold).
 * @param {Timeline} timeline
 */
export function frozenIntervals(timeline) {
  const cached = frozenCache.get(timeline);
  if (cached) return cached;
  /** @type {{start: number, end: number}[]} */
  const out = [];
  for (const s of timeline.scenes || []) {
    const at = s.look && s.look.background ? s.look.background.frozen_at : null;
    if (typeof at === 'number' && Number.isFinite(at)) {
      const start = Number(s.start) + Math.max(0, at);
      const end = Number(s.start) + Number(s.duration);
      if (end > start) out.push({ start, end });
    }
  }
  frozenCache.set(timeline, out);
  return out;
}

/**
 * The background's own time at absolute t: real time minus every frozen stretch before t. Continuous, so a
 * background never jumps when a hold ends, and a pure function of t.
 * @param {Timeline} timeline
 * @param {number} t
 */
export function backgroundTime(timeline, t) {
  let frozen = 0;
  for (const iv of frozenIntervals(timeline)) {
    if (t <= iv.start) break;
    frozen += Math.min(t, iv.end) - iv.start;
  }
  return t - frozen;
}

/** The hold's desaturation eases in over this long (R-55: 600 ms). */
export const FREEZE_EASE_SECONDS = 0.6;

/**
 * How far a scene's background is into its hold (0..1, linear; templates ease it), at scene-relative t.
 * @param {TimedScene} scene
 * @param {number} sceneT
 */
export function freezeProgress(scene, sceneT) {
  const at = scene.look && scene.look.background ? scene.look.background.frozen_at : null;
  if (typeof at !== 'number' || !Number.isFinite(at)) return 0;
  const u = (sceneT - Math.max(0, at)) / FREEZE_EASE_SECONDS;
  return u <= 0 ? 0 : u >= 1 ? 1 : u;
}

// ---------------------------------------------------------------------------------------------
// Captions (§4.11)
// ---------------------------------------------------------------------------------------------

/**
 * @typedef {object} CaptionView
 * @property {string[]} lines
 * @property {Set<string>} keys   lower-cased key words (coloured `mark`, R-35)
 * @property {string} id          stable identity of the chunk (for the frame key)
 */

/**
 * The caption on screen at scene-relative t: the scene's kinetic chunk (kinetic.py, B2; a `blank` chunk shows
 * nothing, R-31), else the phrase caption of the beat (Timeline captions, already line-broken).
 * @param {TimedScene} scene
 * @param {number} t
 * @param {SceneState | null} [state]
 * @returns {CaptionView | null}
 */
export function captionAt(scene, t, state = null) {
  const chunks = scene.kinetic_captions || [];
  if (chunks.length) {
    for (const c of chunks) {
      if (c.start <= t && t < c.end) {
        if (c.blank) return null;
        const lines = (c.lines && c.lines.length ? c.lines : [(c.words || []).map((w) => w.text).join(' ')]).map(String);
        const keys = new Set((c.words || []).filter((w) => w.key).map((w) => String(w.text).toLowerCase()));
        return { lines, keys, id: `k${c.start}` };
      }
    }
    return null;
  }
  const cue = state ? (state.caption ? { text: state.caption, start: t } : null) : selectCue(planScene(scene).cues, t);
  if (!cue || !cue.text) return null;
  return { lines: String(cue.text).split('\n'), keys: new Set(), id: `c${cue.text}` };
}

// ---------------------------------------------------------------------------------------------
// Item states (B2 plan §1.5; B2c)
// ---------------------------------------------------------------------------------------------

/**
 * The states a template may draw on an item besides its entrance and the two cues (plan §1.5, allow-listed: no other
 * state styling exists, and none uses `outline`, `text-decoration`, `mark` or `focus`, which are the cues'):
 * - `current`: the item being explained: weight 700 and a 2 px `ink2` border (the catalogue's `state_border_px`), so it
 *   reads apart from a look_here outline (4 px `focus`, outset >= 8 px) in every family;
 * - `past`: an earlier item: `ink2` text, no border (§4.3 list_build "earlier items are ink2, no accent colour");
 * - `done`: a completed item: an `ok` check icon and a 2 px `ok` border;
 * - `struck`: a refuted item: a line drawn through its text in `warn` (an SVG line, never text-decoration).
 * The look of each is `itemStateStyle`; templates/_shared.js `itemStateParts` / `paintItemState` draw it.
 */
export const ITEM_STATES = Object.freeze(['current', 'past', 'done', 'struck']);

/**
 * How an item state looks, from the scene's roles (plan §1.5): the text `weight` (null: the template's own), the text
 * `ink`, the `border` colour (null: none), the `check` icon colour (null: none) and the `strike` line colour (null:
 * none). Pure; throws for a state outside ITEM_STATES.
 * @param {string} state
 * @param {{color: (role: string) => string}} roles
 * @returns {{weight: number | null, ink: string, border: string | null, check: string | null, strike: string | null}}
 */
export function itemStateStyle(state, roles) {
  switch (state) {
    case 'current':
      return { weight: 700, ink: roles.color('ink'), border: roles.color('ink2'), check: null, strike: null };
    case 'past':
      return { weight: null, ink: roles.color('ink2'), border: null, check: null, strike: null };
    case 'done':
      return { weight: null, ink: roles.color('ink'), border: roles.color('ok'), check: roles.color('ok'), strike: null };
    case 'struck':
      return { weight: null, ink: roles.color('ink2'), border: null, check: null, strike: roles.color('warn') };
    default:
      throw new RangeError(`unknown item state: ${String(state)}`);
  }
}

/**
 * The current / past state of a scene's items at scene time t, from their entrances (`itemTimings`: reveal events
 * first, else the beat that reveals the item): the item entered last (by t) is `current` from its entrance; every
 * item entered before it is `past` from the moment the next one entered. Items not yet entered are absent. `ids`
 * restricts the items considered (a list_build's bullets without its heading, say). Pure; `since` lets a template ease
 * the change (e.g. `progress(t, since, 0.4)`).
 * @param {TimedScene} scene
 * @param {number} t
 * @param {readonly string[] | null} [ids]
 * @returns {{current: string | null, states: Map<string, {state: 'current' | 'past', since: number}>}}
 */
export function itemStatesAt(scene, t, ids = null) {
  const keep = ids ? new Set(ids) : null;
  const entered = itemTimings(scene).filter((it) => (!keep || keep.has(it.id)) && it.enter <= t);
  /** @type {Map<string, {state: 'current' | 'past', since: number}>} */
  const states = new Map();
  entered.forEach((it, k) => {
    const next = entered[k + 1];
    states.set(it.id, next ? { state: 'past', since: next.enter } : { state: 'current', since: it.enter });
  });
  return { current: entered.length ? entered[entered.length - 1].id : null, states };
}
