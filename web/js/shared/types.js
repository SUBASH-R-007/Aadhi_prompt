// @ts-check
/**
 * JSDoc type definitions mirroring web/schemas/timeline.schema.json (aadhi/schemas/timeline.py and
 * the screenplay types it embeds). Other modules reference them as
 * import('../shared/types.js').Timeline inside their own typedef comments.
 * This module has no runtime behaviour beyond the enum-like constants at the bottom.
 */

/**
 * @typedef {'heading' | 'bullet' | 'paragraph' | 'definition' | 'formula' | 'callout_info' | 'callout_tip'
 *   | 'callout_warning' | 'misconception' | 'code' | 'table' | 'figure' | 'example_step' | 'takeaway'} BoardItemKind
 */

/**
 * @typedef {object} FormulaVariable
 * @property {string} symbol_latex
 * @property {string} meaning
 * @property {string} [unit]
 * @property {string | null} [beat_id]   beat that explains the symbol (timeline: SyncCue 'var')
 */

/**
 * One typed item on a scene's board. Text fields use rich-lite markup (see richtext.js).
 * @typedef {object} BoardItem
 * @property {string} id
 * @property {BoardItemKind} kind
 * @property {string} [text]
 * @property {string | null} [term]              definition
 * @property {string | null} [latex]             formula (block TeX)
 * @property {FormulaVariable[]} [variables]     formula legend
 * @property {string | null} [language]          code
 * @property {string | null} [code]              code
 * @property {string[] | null} [headers]         table
 * @property {string[][] | null} [rows]          table
 * @property {string | null} [figure_id]         figure
 * @property {string | null} [caption]           figure
 * @property {string | null} [justification]     example_step (why) / misconception (correction)
 * @property {boolean} [blank]                   example_step shown as a blank until filled
 * @property {string | null} [misconception_id]
 * @property {string[]} [source_refs]
 */

/**
 * @typedef {object} TimedWord
 * @property {string} text
 * @property {number} start   scene-relative seconds
 * @property {number} end
 */

/**
 * @typedef {object} CaptionCue
 * @property {number} start
 * @property {number} end
 * @property {string} text    plain text, already line-broken
 */

/**
 * @typedef {object} TimedBeat
 * @property {string} beat_id
 * @property {number} index              position in the scene's combined beat order
 * @property {'main' | 'reveal'} [phase] quiz reveal beats use "reveal"
 * @property {number} start              scene-relative
 * @property {number} speech_end
 * @property {number} end                speech_end + pause_after
 * @property {string} narration
 * @property {string | null} [board_item_id]   revealed at start
 * @property {string | null} [fill_item_id]    blank example step filled at start
 * @property {string[]} [highlight_item_ids]   emphasised during [start, end)
 * @property {TimedWord[]} [words]
 * @property {CaptionCue[]} [captions]         scene-relative
 * @property {string | null} [visual_cue]
 * @property {boolean} [estimated]
 */

/**
 * @typedef {object} KenBurnsPoint
 * @property {number} [cx]     focus centre, normalised 0..1
 * @property {number} [cy]
 * @property {number} [scale]  >= 1
 */

/**
 * @typedef {object} KenBurns
 * @property {KenBurnsPoint} [start]
 * @property {KenBurnsPoint} [end]
 */

/**
 * @typedef {object} MediaRef
 * @property {'video' | 'image' | 'gif'} kind
 * @property {string | null} [asset_key]
 * @property {string | null} [url]
 * @property {string | null} [mime]
 * @property {number | null} [duration]
 * @property {number | null} [width]
 * @property {number | null} [height]
 * @property {'contain' | 'cover'} [fit]
 * @property {'loop' | 'freeze'} [end_behavior]
 * @property {KenBurns | null} [ken_burns]
 * @property {string | null} [attribution]
 * @property {string | null} [link_url]
 * @property {boolean} [render_in_mp4]
 */

/**
 * @typedef {'skill_tree' | 'figure' | 'image' | 'chart' | 'graph' | 'model_3d' | 'manim' | 'terminal'
 *   | 'quiz' | 'gif'} SidePanelKind
 */

/**
 * @typedef {object} TerminalSpec
 * @property {string} command
 * @property {string} [output]   revealed line by line across the scene
 */

/**
 * Side panel spec (screenplay SidePanel). Payload fields depend on `kind`; panels/index.js owns them.
 * @typedef {object} SidePanel
 * @property {SidePanelKind} kind
 * @property {string | null} [title]
 * @property {string} [rationale]
 * @property {string | null} [show_from_beat_id]
 * @property {string | null} [figure_id]
 * @property {string | null} [image_prompt]
 * @property {any} [chart]
 * @property {any} [graph]
 * @property {any} [model_3d]
 * @property {any} [manim]
 * @property {TerminalSpec | null} [terminal]
 * @property {{question: string, options: string[], correct_index: number} | null} [quiz]
 * @property {string | null} [gif_query]
 * @property {string | null} [override_asset_key]
 * @property {number} [variant]   "new AI version" of a generated image (Visual Review); absent = 0
 */

/**
 * @typedef {object} ResolvedSidePanel
 * @property {SidePanel} panel
 * @property {MediaRef | null} [media]
 * @property {number} [show_at]   scene-relative seconds
 */

/**
 * @typedef {object} QuizTiming
 * @property {string} question
 * @property {string[]} options
 * @property {number} correct_index
 * @property {string[]} feedback_wrong
 * @property {string} explanation
 * @property {number} countdown_start   scene-relative
 * @property {number} countdown_seconds
 * @property {number} reveal_start      scene-relative
 */

/**
 * @typedef {'left' | 'right' | 'center' | 'popup_bottom_left' | 'popup_bottom_right' | 'hidden'} MascotPosition
 */

/**
 * @typedef {object} Layout
 * @property {MascotPosition} [mascot_position]
 * @property {boolean} [show_side_panel]
 * @property {boolean} [fullscreen_media]
 * @property {boolean} [mascot_cues]       false: no cue bubble beside Aadhi (absent = shown)
 */

/**
 * @typedef {object} TimedScene
 * @property {string} scene_id
 * @property {number} index
 * @property {string} type
 * @property {string | null} [concept_id]
 * @property {string | null} [chapter_id]
 * @property {string} [title]
 * @property {string | null} [subtitle]
 * @property {string | null} [chapter_label]
 * @property {number} start            absolute seconds
 * @property {number} duration
 * @property {number} [audio_offset]   scene-relative start of narration
 * @property {Layout} [layout]
 * @property {string | null} [audio_asset_key]
 * @property {string | null} [audio_url]
 * @property {number} [audio_duration]
 * @property {BoardItem[]} [board]
 * @property {Record<string, MediaRef>} [figures]   board item id -> figure media
 * @property {TimedBeat[]} [beats]
 * @property {ResolvedSidePanel | null} [side_panel]
 * @property {MediaRef | null} [media]
 * @property {QuizTiming | null} [quiz]
 * @property {string | null} [p5_code]
 * @property {MediaRef | null} [poster]
 * @property {string[]} [objective_ids]
 * @property {SyncCue[]} [sync_cues]   word-anchored moments (absent when the scene has none)
 * @property {string | null} [audio_envelope]   narration loudness: base64, one byte (0..255) per 1/fps s from audio_offset
 * @property {number} [audio_envelope_fps]
 * @property {number} [hold_seconds]   end of `duration` added by the teacher's minimum duration (nothing new appears; absent when 0)
 */

/**
 * A visual moment anchored to a spoken word (aadhi/compose/sync.py), scene-relative seconds:
 * 'var' a formula legend row (part 'var:<n>') appears at start; 'emphasis' a board item is
 * highlighted during [start, end) and its part ('column:<n>' | 'term' | null) emphasised, replacing
 * the authored highlight of that item in beat_id; 'output' terminal output starts at start; 'focus'
 * the side panel pulses during [start, end).
 * @typedef {object} SyncCue
 * @property {'var' | 'emphasis' | 'output' | 'focus'} kind
 * @property {number} start
 * @property {number | null} [end]
 * @property {string | null} [item_id]
 * @property {string | null} [part]
 * @property {string | null} [beat_id]
 * @property {string} [words]   the spoken words that anchor it (Studio only)
 */

/**
 * @typedef {object} TitleCard
 * @property {string} line1
 * @property {string} [line2]
 * @property {number} start      absolute
 * @property {number} duration
 */

/**
 * @typedef {object} IntroSpec
 * @property {string | null} [logo_video_url]
 * @property {number} [logo_duration]
 * @property {string | null} [background_url]
 * @property {TitleCard[]} [cards]
 * @property {number} [duration]
 */

/**
 * @typedef {object} Chapter
 * @property {number} start   absolute
 * @property {string} title
 */

/**
 * @typedef {object} Branding
 * @property {Record<string, string>} [mascot_clips]   mascot_position -> clip URL
 * @property {string | null} [static_background_url]
 * @property {string | null} [bgm_url]
 * @property {number} [bgm_volume]
 * @property {string | null} [tick_url]
 * @property {string | null} [ding_url]
 * @property {string | null} [mascot_rig_url]
 */

/**
 * @typedef {object} ConceptNode
 * @property {string} id
 * @property {string} title
 * @property {string} [summary]
 * @property {string[]} [depends_on]
 * @property {'prerequisite' | 'core'} [kind]
 */

/**
 * @typedef {object} LearningObjective
 * @property {string} id
 * @property {string} text
 * @property {string} [bloom]
 * @property {string[]} [concept_ids]
 */

/**
 * @typedef {object} Timeline
 * @property {number} [schema_version]
 * @property {number | null} [version_id]
 * @property {number | null} [screenplay_revision]
 * @property {string} [language]
 * @property {string} [board_language]
 * @property {number} [fps]
 * @property {number} [width]
 * @property {number} [height]
 * @property {Record<string, any>} [meta]
 * @property {ConceptNode[]} [concept_map]
 * @property {LearningObjective[]} [learning_objectives]
 * @property {IntroSpec | null} [intro]
 * @property {TimedScene[]} scenes
 * @property {number} total_duration   includes the intro
 * @property {Chapter[]} [chapters]
 * @property {CaptionCue[]} [captions]   absolute
 * @property {Branding} [branding]
 * @property {number} [transition_seconds]
 * @property {boolean} [estimated]
 */

// ---------------------------------------------------------------------------------------------
// Player-side types
// ---------------------------------------------------------------------------------------------

/**
 * @typedef {'lead' | 'beat' | 'pause' | 'countdown' | 'reveal' | 'tail'} ScenePhase
 */

/**
 * Output of schedule.sceneStateAt(scene, t): everything visible is a pure function of (scene, t).
 * `terminalLines` is the array of terminal output lines revealed so far (a fresh array per call).
 * @typedef {object} SceneState
 * @property {ScenePhase} phase
 * @property {number} beatIndex                 position in scene.beats, -1 when no beat is current
 * @property {Set<string>} visibleItemIds
 * @property {Set<string>} filledItemIds
 * @property {Set<string>} highlightItemIds
 * @property {string | null} activeItemId       item narrated by the current beat
 * @property {string | null} caption            caption text at t
 * @property {number | null} countdownRemaining whole seconds left in the quiz countdown
 * @property {boolean} quizRevealed
 * @property {boolean} panelVisible
 * @property {string[]} terminalLines
 * @property {Set<string>} [pendingParts]       `${itemId}|var:<n>`: legend rows not spoken yet (hidden, space kept)
 * @property {Set<string>} [emphasisParts]      `${itemId}|column:<n>` / `${itemId}|term` emphasised now
 * @property {boolean} [panelFocus]             the side panel is pulsed (the narration points at it)
 * @property {import('../player/mascot-state.js').MascotState} [mascotState]   Aadhi's behaviour (mascot-state.js)
 * @property {import('../player/mascot-state.js').MascotCue | null} [mascotCue] cue bubble beside his head (null: none)
 */

/**
 * Rectangle in stage pixels (1920x1080 logical stage). `w`/`h` alias `width`/`height`.
 * @typedef {object} StageRect
 * @property {number} x
 * @property {number} y
 * @property {number} width
 * @property {number} height
 * @property {number} w
 * @property {number} h
 */

/**
 * @typedef {'live' | 'preview' | 'render'} PlayerMode
 */

/**
 * Result of Player.renderState / Player.renderIntro (render mode).
 * @typedef {object} RenderResult
 * @property {string} state_key
 * @property {StageRect | null} media_rect          scene media hole (stage pixels)
 * @property {StageRect | null} panel_media_rect    side-panel media hole (stage pixels)
 * @property {'contain' | 'cover' | null} media_fit
 */

/**
 * Side panel instance created by panels/index.js createPanel(container, resolvedSidePanel, ctx).
 * @typedef {object} PanelInstance
 * @property {HTMLElement} el
 * @property {(t: number, state: SceneState) => void} update
 * @property {() => (StageRect | DOMRect | Element | null)} [mediaRect]
 * @property {() => Promise<void>} [ready]
 * @property {() => void} destroy
 */

/**
 * @typedef {object} PanelContext
 * @property {PlayerMode} mode
 * @property {Timeline} timeline
 * @property {number} sceneIndex
 * @property {{ doneIds: Set<string>, activeId: string | null }} conceptState
 */

/** Board item kinds, in schema order. */
export const BOARD_ITEM_KINDS = Object.freeze([
  'heading', 'bullet', 'paragraph', 'definition', 'formula', 'callout_info', 'callout_tip', 'callout_warning',
  'misconception', 'code', 'table', 'figure', 'example_step', 'takeaway',
]);

/** Scene types rendered by the board scene view. */
export const BOARD_SCENE_TYPES = Object.freeze(['title', 'content', 'example', 'summary', 'key_takeaway', 'recap']);

/** Mascot positions accepted by Layout.mascot_position. */
export const MASCOT_POSITIONS = Object.freeze(['left', 'right', 'center', 'popup_bottom_left', 'popup_bottom_right', 'hidden']);

/** Analytics event names accepted by POST /api/analytics/events (aadhi.models.ANALYTICS_EVENTS). */
export const ANALYTICS_EVENTS = Object.freeze([
  'session_start', 'scene_enter', 'scene_complete', 'quiz_answer', 'pause', 'resume', 'seek', 'complete',
]);
