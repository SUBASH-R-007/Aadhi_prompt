// @ts-check
/**
 * Scene transitions (DYNAMIC_VIDEO_DESIGN §4.9). A transition lives inside the incoming scene B: during [0, d) of
 * B the stage paints scene A's final state below B and combines them, so the timeline's time base is unchanged.
 * B1 ships the hard cut and the crossfade; every value is a pure function of the scene time.
 */

import { ease } from '../motion/easing.js';
import { fx } from './core.js';

/** @typedef {import('../shared/types.js').Timeline} Timeline */
/** @typedef {import('../shared/types.js').TimedScene} TimedScene */
/** @typedef {import('./core.js').Painter} Painter */

/** The transitions this stage draws; any other kind is drawn as a cut until its release. */
export const TRANSITIONS = Object.freeze(['cut', 'crossfade']);
/** Crossfades are 300-500 ms (§4.9) and never shorter than 300 ms (R-54). */
export const CROSSFADE_MIN = 0.3;
export const CROSSFADE_MAX = 0.8;
export const CROSSFADE_DEFAULT = 0.45;

/**
 * The transition into a scene (`look.transition_in`), normalised: `{kind, seconds}`.
 * @param {TimedScene} scene
 * @returns {{kind: 'cut' | 'crossfade', seconds: number}}
 */
export function transitionOf(scene) {
  const raw = scene.look && scene.look.transition_in ? scene.look.transition_in : null;
  if (!raw || raw.kind !== 'crossfade') return { kind: 'cut', seconds: 0 };
  const asked = Number(raw.seconds);
  const seconds = Number.isFinite(asked) && asked > 0 ? asked : CROSSFADE_DEFAULT;
  const d = Math.min(Math.max(seconds, CROSSFADE_MIN), CROSSFADE_MAX, Math.max(0, Number(scene.duration) || 0));
  return d > 0 ? { kind: 'crossfade', seconds: d } : { kind: 'cut', seconds: 0 };
}

/**
 * The transition running at scene-relative t of scene `index`, or null when none runs (a cut, the first scene, or
 * after its end). `p` is the linear progress in [0, 1).
 * @param {Timeline} timeline
 * @param {number} index
 * @param {number} sceneT
 * @returns {{kind: 'crossfade', p: number, outgoing: number | null} | null}
 */
export function transitionAt(timeline, index, sceneT) {
  if (index <= 0) return null;
  const scene = timeline.scenes[index];
  const tr = transitionOf(scene);
  if (tr.kind === 'cut' || !(sceneT < tr.seconds)) return null;
  const p = Math.max(0, sceneT) / tr.seconds;
  return { kind: 'crossfade', p, outgoing: index - 1 };
}

/**
 * Paint a transition: the outgoing slot under the incoming one; a crossfade fades the incoming scene in over the
 * fully painted outgoing scene (both are opaque, so this is the linear mix of the two pictures).
 * @param {Painter} paint
 * @param {HTMLElement} incoming
 * @param {HTMLElement | null} outgoing
 * @param {{kind: string, p: number} | null} tr
 */
export function paintTransition(paint, incoming, outgoing, tr) {
  if (!tr || !outgoing) {
    paint.style(incoming, 'opacity', '');
    paint.style(incoming, 'z-index', '2');
    return;
  }
  paint.style(outgoing, 'z-index', '1');
  paint.style(outgoing, 'opacity', '');
  paint.style(incoming, 'z-index', '2');
  paint.style(incoming, 'opacity', fx(ease('ease-in-out', Math.min(1, Math.max(0, tr.p))), 4));
}
