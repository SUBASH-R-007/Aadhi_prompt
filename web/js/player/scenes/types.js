// @ts-check
/**
 * Shared JSDoc types of the scene views (no runtime code).
 *
 * A scene view renders one TimedScene inside the scene root (whose CSS custom properties hold the
 * layout zones). The player calls update() every frame (live/preview) or once per render state
 * (render mode) with the pure schedule state.
 */

/**
 * @typedef {object} FrameInfo
 * @property {boolean} playing   the clock is running
 * @property {number} rate       playback rate
 * @property {boolean} seeked    discontinuity (seek, scene entry, first frame): resync media, no sounds
 */

/**
 * @typedef {object} SceneContext
 * @property {import('../../shared/types.js').PlayerMode} mode
 * @property {import('../../shared/types.js').Timeline} timeline
 * @property {import('../../shared/types.js').TimedScene} scene
 * @property {number} sceneIndex
 * @property {import('../layout.js').Zones} zones
 * @property {import('../richtext.js').TexRenderer} [renderTex]
 * @property {(language: string) => Promise<any>} [loadPrism]
 * @property {(event: string, detail: any) => void} emit           player event bus (e.g. quizanswer)
 * @property {(name: string) => void} [playSound]                  'tick' | 'ding' (live/preview)
 * @property {(url: string) => (HTMLVideoElement | null)} [takePreloaded]  adopt a preloaded video
 * @property {string} [sandboxUrl]                                 p5 sandbox page (default /sandbox/p5)
 * @property {() => number} [now]                                  ms clock for watchdogs
 */

/**
 * @typedef {object} SceneView
 * @property {HTMLElement} el
 * @property {(t: number, state: import('../../shared/types.js').SceneState, frame: FrameInfo) => void} update
 * @property {() => Promise<void>} ready                 async content settled (TeX, code, images)
 * @property {() => void} [layout]                       measurement-dependent work (board fit)
 * @property {() => (Element | null)} [mediaElement]     element whose box is the media hole (render)
 * @property {() => ('contain' | 'cover' | null)} [mediaFit]
 * @property {(playing: boolean, rate: number) => void} [setPlaying]
 * @property {(volume: number, muted: boolean) => void} [setVolume]
 * @property {() => void} destroy
 */

export {};
