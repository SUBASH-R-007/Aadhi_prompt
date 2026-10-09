// @ts-check
/**
 * Aadhi's behaviour state and the cue bubble beside his head, as pure functions of (scene, t), so live
 * playback, seeking and the MP4 render (render-mode screenshots) always agree. schedule.sceneStateAt()
 * calls mascotStateAt() (SceneState.mascotState / mascotCue), schedule.stateTimes() adds
 * mascotStateTimes() and schedule.stateKey() adds the cue, so the renderer screenshots every bubble change.
 *
 * Director rules (what the scene teaches -> how Aadhi acts; WHERE he stands stays the screenplay's
 * mascot_position, chosen by the scene writer or the teacher):
 *   title                           welcoming    (the clips show it as talking)
 *   summary / key_takeaway / recap  concluding   (shown as explaining)
 *   quiz_checkpoint                 question while asking (and its pauses), listening during the
 *                                   countdown, success from the reveal until the reveal beats end
 *   other scenes                    explaining while the current beat reveals or fills a board item,
 *                                   talking otherwise
 *   a long pause                    thinking: at least THINK_MIN_SILENCE s of silence after a beat's
 *                                   speech, from speech_end + THINK_DELAY (a short pause keeps the
 *                                   speaking state, so the bubble never flickers between sentences)
 *   lead / tail                     idle
 * The wanted behaviour is mapped onto what the clips can show (presenter-vocab.js actingFor with
 * CLIP_CAPABILITIES). Visible cues: thinking / listening -> 'dots', question -> 'question',
 * success -> 'success'; speaking and idle show no bubble.
 *
 * The bubble sits at CUE_ANCHORS (stage pixels beside the head, measured on the clips over their
 * frames: clear of the per-pixel union of all 192 frames plus an 8 px margin, checked by
 * tests/branding/test_mascot_assets.py test_cue_bubble_never_covers_aadhi); popup (a full-frame
 * close-up) and hidden (no Aadhi) have no free spot and show none.
 * Layout.mascot_cues === false (Settings.mascot_cues) turns the bubble off; the state is still computed.
 *
 * No DOM access; safe to import from Node tests.
 */

import { actingFor, CLIP_CAPABILITIES } from './presenter-vocab.js';
import { positionKey } from './layout.js';

/** @typedef {import('../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('../shared/types.js').ScenePhase} ScenePhase */
/** @typedef {'idle' | 'talking' | 'explaining' | 'thinking' | 'listening' | 'question' | 'success'} MascotState */
/** @typedef {'dots' | 'question' | 'success'} MascotCue */

/** Silence after a beat's speech (to the next beat of its slot) that counts as a deliberate pause. */
export const THINK_MIN_SILENCE = 1.2;
/** Delay after speech_end before Aadhi starts "thinking" in such a pause. */
export const THINK_DELAY = 0.6;

/** Bubble centre per mascot position (stage px; 1920x1080 stage = the 1280x720 clips scaled 1.5x). */
export const CUE_ANCHORS = Object.freeze({
  left: Object.freeze({ x: 211, y: 108 }),
  right: Object.freeze({ x: 1804, y: 121 }),
  center: Object.freeze({ x: 792, y: 116 }),
});
/** Bubble size in stage px (fixed, so its rectangle is the same in the player and the MP4). */
export const CUE_SIZE = Object.freeze({ w: 72, h: 56 });

/** @type {Readonly<Record<string, MascotCue>>} */
const CUE_FOR = Object.freeze({ thinking: 'dots', listening: 'dots', question: 'question', success: 'success' });

const EPS = 1e-6;

/**
 * Lookup data of schedule.planScene() that this module needs.
 * @typedef {object} BeatPlan
 * @property {import('../shared/types.js').TimedBeat[]} beats
 * @property {number[]} slotEnd   per beat index: end of the slot in which it is current
 */

/**
 * What the scene teaches, from its (typed) scene type.
 * @param {TimedScene} scene
 * @returns {'quiz' | 'intro' | 'summary' | 'explanation'}
 */
export function sceneRole(scene) {
  if (scene.quiz) return 'quiz';
  const type = String(scene.type || '');
  if (type === 'title') return 'intro';
  if (type === 'summary' || type === 'key_takeaway' || type === 'recap') return 'summary';
  return 'explanation';
}

/**
 * Scene-relative time at which beat `i`'s pause turns into "thinking", or null for a short pause.
 * @param {BeatPlan} plan
 * @param {number} i
 * @returns {number | null}
 */
function thinkingFrom(plan, i) {
  const b = plan.beats[i];
  const silence = plan.slotEnd[i] - b.speech_end;
  return silence >= THINK_MIN_SILENCE - EPS ? b.speech_end + THINK_DELAY : null;
}

/**
 * The behaviour the director wants at t (before mapping onto the clips' capabilities).
 * @param {TimedScene} scene
 * @param {BeatPlan} plan
 * @param {number} t
 * @param {ScenePhase} phase
 * @param {number} beatIndex
 * @param {string | null} activeItemId
 * @returns {string}
 */
export function wantedBehaviour(scene, plan, t, phase, beatIndex, activeItemId) {
  if (phase === 'countdown') return 'listening';
  if (phase === 'reveal') return 'success';
  const role = sceneRole(scene);
  // A quiz keeps asking through the gap between its last question beat and the countdown (the countdown
  // starts INTER_BEAT_GAP_SECONDS after that beat ends; 'tail' when the quiz has no reveal beats), so the
  // '?' never blinks off. Before the first beat ('lead') Aadhi is idle.
  if (role === 'quiz' && beatIndex < 0 && (phase === 'pause' || phase === 'tail') && scene.quiz
      && t < scene.quiz.countdown_start) return 'question';
  if ((phase !== 'beat' && phase !== 'pause') || beatIndex < 0) return 'idle';
  if (role === 'quiz') return 'question';
  if (phase === 'pause') {
    const from = thinkingFrom(plan, beatIndex);
    if (from !== null && t >= from) return 'thinking';
  }
  if (role === 'summary') return 'concluding';
  if (activeItemId) return 'explaining';
  return role === 'intro' ? 'welcoming' : 'talking';
}

/**
 * Bubble centre for a mascot position, or null where no bubble is drawn (popup, hidden).
 * @param {string | null | undefined} position   Layout.mascot_position
 * @returns {{ x: number, y: number } | null}
 */
export function cueAnchor(position) {
  const key = positionKey(position);
  return Object.prototype.hasOwnProperty.call(CUE_ANCHORS, key) ? CUE_ANCHORS[/** @type {keyof typeof CUE_ANCHORS} */ (key)] : null;
}

/**
 * Bubble rectangle in stage px for a mascot position (null: no bubble there).
 * @param {string | null | undefined} position
 * @returns {{ x: number, y: number, w: number, h: number } | null}
 */
export function cueRect(position) {
  const a = cueAnchor(position);
  return a ? { x: a.x - CUE_SIZE.w / 2, y: a.y - CUE_SIZE.h / 2, w: CUE_SIZE.w, h: CUE_SIZE.h } : null;
}

/**
 * Aadhi's state and visible cue at t. Called by schedule.sceneStateAt with its own plan and phase.
 * @param {TimedScene} scene
 * @param {BeatPlan} plan
 * @param {number} t
 * @param {ScenePhase} phase
 * @param {number} beatIndex
 * @param {string | null} activeItemId
 * @returns {{ state: MascotState, cue: MascotCue | null }}
 */
export function mascotStateAt(scene, plan, t, phase, beatIndex, activeItemId) {
  const wanted = wantedBehaviour(scene, plan, t, phase, beatIndex, activeItemId);
  const state = /** @type {MascotState} */ (actingFor({ behaviour: wanted }, CLIP_CAPABILITIES).behaviour || 'idle');
  const layout = scene.layout || {};
  const show = /** @type {any} */ (layout).mascot_cues !== false && cueAnchor(layout.mascot_position) !== null;
  return { state, cue: show ? CUE_FOR[state] || null : null };
}

/**
 * Times (scene-relative) at which mascotStateAt() changes besides the schedule's own phase and beat
 * boundaries: the start of "thinking" in each long pause.
 * @param {TimedScene} scene
 * @param {BeatPlan} plan
 * @returns {number[]}
 */
export function mascotStateTimes(scene, plan) {
  if (sceneRole(scene) === 'quiz') return [];
  /** @type {number[]} */
  const out = [];
  plan.beats.forEach((b, i) => {
    const from = thinkingFrom(plan, i);
    if (from !== null && from < plan.slotEnd[i]) out.push(from);
  });
  return out;
}
