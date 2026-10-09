// @ts-check
/**
 * Inspector context passed to every editor section (JSDoc only).
 *
 * Edits go through `editScene(updater)`: the updater receives a deep clone of the CURRENT
 * scene (never a stale captured copy) and either mutates it or returns a replacement.
 * `structural: true` re-renders the inspector (lists, constrained selects); plain text edits
 * keep the DOM (and focus) as is. `coalesce` merges rapid edits of one field into one undo step.
 * `label` names the edit for Undo / Redo ("Undo: Delete beat in scene 3"); without one the label comes
 * from the coalesce key (changes.js `editLabel`), else "Edit scene".
 */

/**
 * @typedef {object} EditOptions
 * @property {boolean} [structural]
 * @property {string} [coalesce]
 * @property {string} [label]
 */

/**
 * @typedef {object} InspectorCtx
 * @property {any} sp
 * @property {any} scene
 * @property {any} meta
 * @property {number} projectId
 * @property {number} maxUploadMb
 * @property {(updater: (scene: any) => any, opts?: EditOptions) => void} editScene
 * @property {(updater: (sp: any) => any, opts?: EditOptions) => void} editScreenplay
 * @property {{ phase: 'main' | 'reveal', index: number } | null} selectedBeat
 * @property {(phase: 'main' | 'reveal', index: number) => void} selectBeat
 * @property {() => void} regenerateScene
 * @property {() => void} duplicateScene
 * @property {() => void} deleteScene
 * @property {Set<string>} openSections   ids of expanded <details> sections (kept across renders)
 * @property {import('../../types.js').SceneVisual | null} [visual]   the scene's visual at the last build
 *   (GET /visual-review; null until loaded or when unavailable)
 * @property {string} [reviewHref]   the Visual review at this scene
 * @property {(kind?: 'image' | 'video') => Promise<any>} [pickLibrary]   choose an item of the teacher's
 *   library; it is attached to the lecture and carries its `asset_key` (null when nothing was chosen)
 * @property {Set<string>} [closedSections]   keys of folded inspector sections (inspectorFold.js), kept across renders
 * @property {import('../../types.js').SceneChange | null} [change]   the scene compared with what Aadhi generated
 *   (GET /changes; null until loaded, when unavailable, or for a scene not saved yet)
 * @property {() => void} [revertScene]   "Compare and revert…" (shown for an edited scene, or one with earlier versions)
 * @property {(beatIndex: number) => void} [splitScene]   split the scene before main beat `beatIndex` (splitScene.js)
 */

export {};
