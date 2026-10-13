// @ts-check
/**
 * Types of the scene-template contract (DYNAMIC_VIDEO_DESIGN §4.2; B2 plan §1.4, frozen by B2):
 *
 *   export default {
 *     id, build(ctx), layout(ctx), update(ctx, t, state), texts(ctx),
 *     focusRect(ctx, target) -> {rect, shape: 'rect' | 'ring' | 'bar', radius} | null   whole stage px
 *     itemEl(ctx, itemId) -> Element | null       the item's text: cue.term finds its .rt-k spans inside
 *     dimGroups(ctx) -> Element[]                 content groups look_here dims to 70% (all but the target's)
 *     anchor(ctx) -> {el, rect} | null            the shared-element morph's source / target
 *     mediaBox(scene, zones, mode, slot, fixes) -> {w, h} | null   static: the largest box `slot`'s medium is shown in
 *     tex(scene, look, fmt) -> [{tex, display}]   static: every TeX string the template renders for the scene
 *   }
 *
 * `build` creates the DOM (no measuring), `layout` measures and fits it once the fonts, TeX and pictures are in
 * (idempotent; deterministic because it only reads the laid-out page), `update` paints scene time `t` through
 * `ctx.paint` as a pure function of `t` and the scene state. Colours come only from `ctx.roles`. Templates never
 * transform `ctx.root` and never draw emphasis (cues.js does, from the geometry focusRect / itemEl / dimGroups give
 * it), and never read the timeline: what they show of other scenes reaches them through the context (`chapter`) or the
 * look (`look.refs`, denormalised by the resolver). The static hooks take no context, so the stage can ask them before
 * (and without) building a scene: `mediaBox` sizes the clips `mediaPlan` lists and `ctx.media` shows (one function, so
 * every frame the page asks for is a frame the worker extracted), `tex` is converted before `ready` (render.js), so no
 * MathJax work happens while frames are captured. No runtime behaviour.
 */

/**
 * @typedef {object} TemplateContext
 * @property {import('../../shared/types.js').TimedScene} scene
 * @property {number} index                 scene index in the timeline
 * @property {import('../../shared/types.js').ResolvedLook | null} look
 * @property {string} chapter               the title of the chapter the scene is in (Timeline.chapters, state.js
 *                                          chapterTitleAt; '' without one): what board_generic's kicker shows
 * @property {import('../core.js').Roles} roles
 * @property {import('../core.js').Zones} zones
 * @property {'landscape' | 'portrait'} format
 * @property {() => number} rng             seeded (mulberry32 of the look seed, the scene and the template); decoration
 *                                          only (layout randomness is decided in Python and stored)
 * @property {'live' | 'render'} mode
 * @property {boolean} reducedMotion        live player only: entrances fade without moving, no Ken Burns (§4.8)
 * @property {HTMLElement} root             the template's layer (stage size, top left at 0, 0)
 * @property {import('../core.js').Painter} paint
 * @property {Promise<unknown>[]} pending   TeX and pictures the first frame waits for
 * @property {(ref: import('../../shared/types.js').MediaRef, opts: MediaOptions) => import('../media.js').MediaView}
 *   media
 * @property {{ink: string, ink2: string, bg: string, bgs?: string[], block?: boolean}} onBackground   text colours for
 *   text drawn on the background, the worst-case colour behind it (against `ink`), every colour the background shows
 *   (`bgs`: the worst case of text over a translucent scrim depends on the text's own ink), and whether it is a colour
 *   block (structure marks use ink)
 * @property {(el: Element) => {x: number, y: number, w: number, h: number} | null} rectOf   stage-px rect
 * @property {string} locale
 * @property {import('../../player/richtext.js').TexRenderer} [renderTex]   TeX converter (tests; default MathJax)
 * @property {import('../media.js').MediaFrameInfo} [frameInfo]   live playback state for clips (render: none)
 * @property {any} [view]                    what `build` returned
 */

/**
 * `ctx.media(ref, opts)`: a picture or clip on the stage. The box the medium is sized for is the template's own
 * `mediaBox(scene, zones, mode, slot, fixes)` for `slot` (the full stage without one), the box `mediaPlan` extracts a
 * clip at, so a clip's frame key always names frames the worker has. A picture (it has no frame key) may pass the box
 * it is shown in instead; a clip's `box` is ignored.
 * @typedef {object} MediaOptions
 * @property {string} [slot]     'main' | 'panel' (media.js sceneClips) or a slot of the template's own
 * @property {number} [start]    scene-relative time the clip starts playing
 * @property {string} [alt]
 * @property {{w: number, h: number}} [box]   pictures only
 */

/**
 * What `focusRect` returns: the target's box in whole stage px, its shape (cues.js draws the look_here outline around
 * it, outset >= 8 px) and its own corner radius.
 * @typedef {object} FocusRect
 * @property {{x: number, y: number, w: number, h: number}} rect
 * @property {'rect' | 'ring' | 'bar'} shape
 * @property {number} radius
 */

/**
 * A scene template (stage.js TEMPLATES).
 * @typedef {object} TemplateModule
 * @property {string} id
 * @property {(ctx: TemplateContext) => any} build
 * @property {(ctx: TemplateContext) => void} [layout]
 * @property {(ctx: TemplateContext, t: number, state: import('../../shared/types.js').SceneState) => void} update
 * @property {(ctx: TemplateContext) => TextEntry[]} [texts]
 * @property {(ctx: TemplateContext, target: string) => FocusRect | null} [focusRect]
 * @property {(ctx: TemplateContext, itemId: string) => Element | null} [itemEl]
 * @property {(ctx: TemplateContext) => Element[]} [dimGroups]
 * @property {(ctx: TemplateContext) => {el: Element, rect: {x: number, y: number, w: number, h: number}} | null}
 *   [anchor]
 * @property {(scene: import('../../shared/types.js').TimedScene, zones: import('../core.js').Zones,
 *   mode: 'live' | 'render', slot: string | undefined, fixes: import('../core.js').Fixes)
 *   => {w: number, h: number} | null} [mediaBox]
 * @property {(scene: import('../../shared/types.js').TimedScene,
 *   look: import('../../shared/types.js').ResolvedLook | null, fmt: 'landscape' | 'portrait')
 *   => {tex: string, display: boolean}[]} [tex]
 */

/**
 * @typedef {object} TextEntry
 * @property {Element} el
 * @property {string} role         'title' | 'subtitle' | 'label' | 'body' | 'caption' | 'chip'
 * @property {boolean} critical
 * @property {string} ink          hex
 * @property {string | null} bg    hex of what is behind the text, opaque (a scrim composited over the background
 *                                 below it; null: unknown)
 * @property {number} [alpha]      current opacity of the text (0 = not shown)
 * @property {number | boolean} [overflow]  px the text overflows its slot even at its minimum size (0: fits)
 * @property {string} [slot]       the slot preflight's remedies address (`ResolvedLook.fixes.slots` key)
 * @property {number} [size]       font size now, px
 * @property {number} [min]        the slot's smallest size, px
 * @property {number} [weight]
 * @property {string} [text]       the text (preflight messages)
 */

export {};
