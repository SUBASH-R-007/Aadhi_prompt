// @ts-check
/**
 * Defaults of the "Make the video" / "Render MP4" dialogs (pure). A Dynamic lecture (DYNAMIC_VIDEO_DESIGN §7.1: the
 * motion engine renders it) burns its captions as its presentation says (`captions_burn`, §2.6: burned unless the
 * teacher chose a clean file) and has no classic intro (logo and title cards), so its dialog pre-checks "Burn captions"
 * from the presentation, always sends the teacher's choice, and leaves the intro option out. A classic lecture's dialog
 * is unchanged.
 *
 * Which render a lecture gets is the server's answer only: GET /api/versions/{vid}/render/preflight `presentation`
 * (aadhi/api/routers/renders.py render_presentation: the screenplay has a presentation and DYNAMIC_ENABLED is on), null
 * for a classic render. Without that answer (the check failed, or was not made for a lecture that must be built
 * first) both dialogs (projectDetail.js and the editor) show the classic one: the screenplay alone cannot say, since
 * a lecture with a presentation renders classic while DYNAMIC_ENABLED is off. The classic dialog sends what it shows
 * (burn off unless ticked, the intro choice, which a Dynamic render ignores).
 */

/**
 * @typedef {object} DynamicRender
 * @property {'landscape' | 'portrait'} format
 * @property {boolean} captionsBurn   the lecture's caption policy: the "Burn captions" default
 */

/**
 * The Dynamic render a lecture gets, or null for a classic one (or when the server's answer is not available).
 * @param {any} preflight             GET /render/preflight body, or null when it is not available
 * @returns {DynamicRender | null}
 */
export function dynamicRender(preflight) {
  const pres = preflight && typeof preflight === 'object' ? preflight.presentation : null;
  if (!pres || typeof pres !== 'object') return null;
  return {
    format: pres.format === 'portrait' ? 'portrait' : 'landscape',
    captionsBurn: pres.captions_burn !== false, // the server always sends it; burned is the schema default
  };
}
