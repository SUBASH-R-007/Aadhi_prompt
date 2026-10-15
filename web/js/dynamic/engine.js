// @ts-check
/**
 * The look-engine generations the Dynamic stage draws (B2 plan §1.3, deviation register 21). A timeline records the
 * generation of the resolver that built it in `TimelinePresentation.engine_version` (1 = B1's, 2 = B2a's,
 * 3 = the demo phase's director; absent = 1);
 * the stage contract stays aadhiStage v3. The stage refuses a timeline of a generation newer than it knows (a newer
 * server than this page: the render page rejects `ready` with `timeline_invalid`, the live player's `load()` rejects
 * with that code) and otherwise draws whatever fields the timeline carries: it feature-detects them and never branches
 * on the generation for drawing.
 *
 * No imports and no DOM: the live player (web/js/player/player.js) checks a Dynamic timeline with this before it builds
 * anything, without loading the stage itself.
 */

/** The newest look-engine generation this stage draws (aadhi/compose/presentation.py ENGINE_VERSION). */
export const ENGINE_VERSION = 3;

/**
 * A timeline's look-engine generation, read as aadhi/api/timelines.py `built_engine` reads it: an integer >= 1 is
 * the generation; anything else (absent, null, a string, a fraction, a boolean) is B1's default, 1.
 * @param {any} timeline
 */
export function engineVersion(timeline) {
  const v = timeline && timeline.presentation ? timeline.presentation.engine_version : undefined;
  return typeof v === 'number' && Number.isInteger(v) && v >= 1 ? v : 1;
}

/**
 * Why this stage cannot draw the timeline (a sentence for the error), or null when it can.
 * @param {any} timeline
 * @returns {string | null}
 */
export function engineProblem(timeline) {
  const v = engineVersion(timeline);
  if (v <= ENGINE_VERSION) return null;
  return `the timeline was built by look engine ${v}; this page draws up to ${ENGINE_VERSION} (reload to update it)`;
}
