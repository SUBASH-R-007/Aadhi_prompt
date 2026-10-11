// @ts-check
/**
 * Types of the scene-template contract (DYNAMIC_VIDEO_DESIGN §4.2):
 *
 *   export default {id, build(ctx), layout(ctx), update(ctx, t, state), focusRect(ctx, targetId), texts(ctx)}
 *
 * `build` creates the DOM (no measuring), `layout` measures and fits it once the fonts, TeX and pictures are in
 * (idempotent; deterministic because it only reads the laid-out page), `update` paints scene time `t` through
 * `ctx.paint` as a pure function of `t` and the scene state. Colours come only from `ctx.roles`. No runtime behaviour.
 */

/**
 * @typedef {object} TemplateContext
 * @property {import('../../shared/types.js').TimedScene} scene
 * @property {number} index                 scene index in the timeline
 * @property {import('../../shared/types.js').Timeline} timeline
 * @property {import('../../shared/types.js').ResolvedLook | null} look
 * @property {import('../core.js').Roles} roles
 * @property {import('../core.js').Zones} zones
 * @property {'landscape' | 'portrait'} format
 * @property {() => number} rng             seeded (mulberry32 of the look seed, the scene and the template)
 * @property {'live' | 'render'} mode
 * @property {boolean} reducedMotion        live player only: entrances fade without moving, no Ken Burns (§4.8)
 * @property {HTMLElement} root             the template's layer (stage size, top left at 0, 0)
 * @property {import('../core.js').Painter} paint
 * @property {Promise<unknown>[]} pending   TeX and pictures the first frame waits for
 * @property {(ref: import('../../shared/types.js').MediaRef, opts: {box: {w: number, h: number}, start?: number, alt?: string}) => import('../media.js').MediaView} media
 * @property {{ink: string, ink2: string, bg: string, block?: boolean}} onBackground   text colours for text drawn on
 *   the background, the worst-case colour behind it, and whether it is a colour block (structure marks use ink)
 * @property {(el: Element) => {x: number, y: number, w: number, h: number} | null} rectOf   stage-px rect
 * @property {string} locale
 * @property {import('../../player/richtext.js').TexRenderer} [renderTex]   TeX converter (tests; default MathJax)
 * @property {import('../media.js').MediaFrameInfo} [frameInfo]   live playback state for clips (render: none)
 * @property {any} [view]                    what `build` returned
 */

/**
 * @typedef {object} TextEntry
 * @property {Element} el
 * @property {string} role         'title' | 'label' | 'body' | 'caption' | 'chip'
 * @property {boolean} critical
 * @property {string} ink          hex
 * @property {string | null} bg    hex of what is behind the text (null: unknown)
 * @property {number} [alpha]      current opacity of the text (0 = not shown)
 * @property {number | boolean} [overflow]  px the text overflows its slot even at its minimum size (0: fits)
 * @property {string} [slot]       the `ResolvedLook.text_fit` key preflight's remedies address
 * @property {number} [size]       font size now, px
 * @property {number} [min]        the slot's smallest size, px
 * @property {number} [weight]
 * @property {string} [text]       the text (preflight messages)
 */

export {};
