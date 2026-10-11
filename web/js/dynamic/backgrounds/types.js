// @ts-check
/**
 * Types shared by the background presets (DYNAMIC_VIDEO_DESIGN §4.5). A preset is
 * `export default {id, build(ctx) -> BackgroundView}`; the view's `update(paint, t, freeze)` paints the background
 * at background-clock time `t` (frozen during holds, R-55) with the hold's progress `freeze` (0..1).
 * No runtime behaviour.
 */

/**
 * @typedef {object} BackgroundContext
 * @property {import('../core.js').Roles} roles
 * @property {import('../core.js').Zones} zones
 * @property {Record<string, any>} params   ResolvedLook.background.params (e.g. {tone})
 * @property {number} seed                  ResolvedLook.background.seed
 * @property {'live' | 'render'} mode
 * @property {Promise<unknown>[]} pending   work the first frame must wait for (render mode)
 */

/**
 * @typedef {object} BackgroundView
 * @property {HTMLElement} el
 * @property {(paint: import('../core.js').Painter, t: number, freeze: number) => void} update
 * @property {() => string[]} colors   every colour the preset paints (hex), for the analytic contrast check (R-53)
 */

/**
 * @typedef {object} BackgroundPreset
 * @property {string} id
 * @property {(ctx: BackgroundContext) => BackgroundView} build
 */

export {};
