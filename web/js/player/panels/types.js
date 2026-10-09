// @ts-check
/**
 * Shared JSDoc types for side panels (no runtime code).
 *
 * The timeline types mirror `aadhi/schemas/timeline.py` (ResolvedSidePanel, MediaRef, SidePanel)
 * loosely: panels only read the fields they need and tolerate missing optional data.
 */

/** @typedef {'live' | 'preview' | 'render'} PanelMode */

/**
 * @typedef {object} ConceptState
 * @property {Set<string> | string[]} [doneIds]
 * @property {string | null} [activeId]
 */

/**
 * @typedef {object} PanelCtx
 * @property {PanelMode} [mode]
 * @property {any} [timeline]          the Timeline being played
 * @property {number} [sceneIndex]     index of the scene that owns this panel
 * @property {ConceptState} [conceptState]
 * @property {HTMLElement | null} [stageEl]  optional: the 1920x1080 logical stage (for mediaRect)
 */

/**
 * @typedef {object} MediaRef
 * @property {'video' | 'image' | 'gif'} kind
 * @property {string | null} [url]
 * @property {string | null} [asset_key]
 * @property {string | null} [mime]
 * @property {number | null} [duration]
 * @property {number | null} [width]
 * @property {number | null} [height]
 * @property {'contain' | 'cover'} [fit]
 * @property {'loop' | 'freeze'} [end_behavior]
 * @property {string | null} [attribution]
 * @property {string | null} [link_url]
 * @property {boolean} [render_in_mp4]
 */

/**
 * @typedef {object} ResolvedSidePanel
 * @property {any} panel             SidePanel: {kind, title, chart, graph, model_3d, terminal, quiz, ...}
 * @property {MediaRef | null} [media]
 * @property {number} [show_at]      scene-relative seconds
 */

/**
 * Subset of `schedule.sceneStateAt` output that panels read.
 * @typedef {object} PanelSceneState
 * @property {boolean} [panelVisible]
 * @property {string[] | number | null} [terminalLines]  revealed terminal lines (array) or their count
 * @property {string} [phase]
 * @property {boolean} [panelFocus]   the narration points at the visual (a 'focus' sync cue)
 */

/** @typedef {{ x: number, y: number, width: number, height: number }} StageRect */

/**
 * Optional per-frame info from the player (player.js FrameInfo). Not part of the frozen panel
 * contract yet: panels use it when present and fall back to estimating from `t` otherwise.
 * @typedef {object} PanelFrame
 * @property {boolean} [playing]
 * @property {number} [rate]      playback speed (0.25 - 4)
 * @property {boolean} [seeked]   the clock jumped since the previous frame
 */

/**
 * What a kind-specific implementation returns; `createPanel` wraps it with the shared chrome.
 * @typedef {object} PanelImpl
 * @property {(t: number, state: PanelSceneState, visible: boolean, frame?: PanelFrame | null) => void} [update]
 * @property {() => (StageRect | null)} [mediaRect]
 * @property {() => Promise<void>} [ready]
 * @property {() => void} destroy
 */

/**
 * The public panel object returned by `createPanel`.
 * @typedef {object} Panel
 * @property {HTMLElement} el
 * @property {(t: number, sceneState?: PanelSceneState | null, frame?: PanelFrame | null) => void} update
 * @property {() => (StageRect | null)} [mediaRect]
 * @property {() => Promise<void>} ready
 * @property {() => void} destroy
 */

/**
 * @typedef {object} Chrome
 * @property {HTMLElement} el
 * @property {HTMLElement} head
 * @property {HTMLElement} titleEl
 * @property {HTMLElement} body
 */

/**
 * @callback PanelFactory
 * @param {HTMLElement} body
 * @param {ResolvedSidePanel} rsp
 * @param {Required<Pick<PanelCtx, 'mode'>> & PanelCtx} ctx
 * @param {Chrome} chrome
 * @returns {PanelImpl}
 */

export {};
