// @ts-check
/**
 * JSDoc type definitions mirroring web/schemas/timeline.schema.json (aadhi/schemas/timeline.py and
 * the screenplay types it embeds) and the schema v3 (Dynamic video) types of
 * web/schemas/screenplay.schema.json. Other modules reference them as
 * import('../shared/types.js').Timeline inside their own typedef comments.
 * This module has no runtime behaviour beyond the enum-like constants at the bottom.
 *
 * Schema v3 (design §6.7, §8.2): every Dynamic field is optional and absent from classic documents;
 * a document using one carries schema_version 3 (aadhi/schemas/features.py).
 */

/**
 * The last five kinds are v3 (Dynamic lectures, feature "items_v3").
 * @typedef {'heading' | 'bullet' | 'paragraph' | 'definition' | 'formula' | 'callout_info' | 'callout_tip'
 *   | 'callout_warning' | 'misconception' | 'code' | 'table' | 'figure' | 'example_step' | 'takeaway'
 *   | 'question' | 'stat' | 'step' | 'quote' | 'pair'} BoardItemKind
 */

/**
 * A point on a source figure placed by the teacher (v3).
 * @typedef {object} FigurePin
 * @property {string} id
 * @property {number} x        0..1 of the figure's width
 * @property {number} y        0..1 of the figure's height
 * @property {string} [label]
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
 * @property {boolean} [blank]                   example_step / question shown as a blank until filled
 * @property {string | null} [misconception_id]
 * @property {string[]} [source_refs]
 * @property {string | null} [value]             v3 stat: the number as written
 * @property {string | null} [unit]              v3 stat
 * @property {string | null} [date]              v3 step of a timeline
 * @property {string | null} [icon_hint]         v3: one keyword (never translated)
 * @property {FigurePin[]} [pins]                v3 figure: teacher-placed points
 * @property {'recall' | 'apply' | 'transfer' | null} [level]   v3 question
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
 * @property {'contain' | 'cover' | 'contain_blur'} [fit]   'contain_blur': v3 inserted clips
 * @property {'loop' | 'freeze'} [end_behavior]   the schema also has v3 'clamp' (inserted clips): typed here when the
 *   player handles it (B6); a classic timeline never carries it
 * @property {KenBurns | null} [ken_burns]
 * @property {string | null} [attribution]
 * @property {string | null} [link_url]
 * @property {boolean} [render_in_mp4]
 * @property {number} [trim_in]                 v3: seconds into the source (absent = 0)
 * @property {boolean} [audio]                  v3: the clip's own sound plays (absent = false)
 * @property {number} [volume_db]               v3 (absent = 0)
 * @property {[number, number] | null} [focus]  v3: cover focus point, 0..1
 * @property {number | null} [src_fps]          v3
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
 * @property {boolean} [colour_is_content]   v3: the picture's colours carry meaning (absent = false)
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
 * @property {ResolvedLook | null} [look]            v3 Dynamic: the resolved look (absent in classic timelines)
 * @property {VisualEvent[]} [events]                v3 Dynamic: sorted by t (absent when none)
 * @property {KineticChunk[]} [kinetic_captions]     v3 Dynamic (absent when none)
 * @property {TimedOverlay[]} [overlays]             v3 (absent when none)
 */

// ---------------------------------------------------------------------------------------------
// Schema v3 (Dynamic video): timeline side (aadhi/schemas/timeline.py)
// ---------------------------------------------------------------------------------------------

/**
 * @typedef {object} TimelinePresentation
 * @property {'dynamic'} style
 * @property {'landscape' | 'portrait'} format
 * @property {string} [safe_zone]
 * @property {Record<string, string>} content   resolved content tokens (hex): ink, ink2, entity:0..2, warn, ok, mark, focus
 * @property {Record<string, string>} fonts     role -> family id
 * @property {string} personality
 * @property {string} caption_style
 * @property {string} cue_term_style
 * @property {boolean} captions_burn
 * @property {boolean} sfx
 * @property {'standard' | 'calm'} motion
 * @property {number} generated_seconds
 * @property {number} [engine_version]           look-engine generation (1 = B1's resolver, 2 = B2's; absent = 1);
 *   the stage contract stays aadhiStage v3. Feature-detect the fields below, never branch on this.
 * @property {Record<string, string>} [content_alt]   B2a: the other mode's content tokens (code editor surface)
 * @property {string} [family]                   B2a: the resolved theme family id
 * @property {'dark' | 'light'} [mode]           B2a: the family's mode
 * @property {number} [motion_scale]             B2a: the personality's duration scale (absent = 1, B1's timings)
 * @property {Record<string, number>} [entity_colors]  B2a: lesson-wide symbol/entity -> entity slot 0..2 (R-30)
 */

/**
 * Preflight's remedy of one text slot (fixes channel; each key present only when set).
 * @typedef {object} SlotFix
 * @property {number} [size]       px: the slot's size is capped here (its minimum when it overflowed)
 * @property {boolean} [scrim]     the >= 55% scrim behind text drawn on the background (R-53)
 */

/**
 * Preflight's remedies of one scene (plan B2 §1.2, deviation 2); always both keys when present.
 * @typedef {object} LookFixes
 * @property {boolean} safe                     the template's compact, zone-safe layout (R-59)
 * @property {Record<string, SlotFix>} slots    text slot -> its remedies
 */

/**
 * An icon's geometry (SVG path data only; sanitise again before drawing).
 * @typedef {object} LookIcon
 * @property {string} name
 * @property {string} set
 * @property {string} version
 * @property {[number, number, number, number]} viewBox   [min_x, min_y, width, height]
 * @property {number} stroke_width
 * @property {boolean} filled
 * @property {string[]} paths                   SVG path `d` strings
 */

/**
 * @typedef {object} RoadmapEntry
 * @property {string} title
 * @property {'done' | 'current' | 'upcoming'} state
 */

/**
 * @typedef {object} RefAnchor
 * @property {'formula' | 'figure' | 'takeaway'} kind
 * @property {string} [text]     exactly one of text and tex
 * @property {string} [tex]
 * @property {string} [icon]
 */

/**
 * Cross-scene data a template shows, denormalised by the resolver (each key present only when set).
 * @typedef {object} LookRefs
 * @property {RoadmapEntry[]} [roadmap]
 * @property {RefAnchor} [anchor]
 * @property {{text: string, resolve_t?: number}} [prediction]   resolve_t: scene-relative seconds
 * @property {{title: string}} [next]
 * @property {string} [accent_next]      #RRGGBB: the next scene's block colour
 */

/**
 * @typedef {object} ResolvedLook
 * @property {string} template
 * @property {string | null} [variant]
 * @property {Record<string, string>} tokens     resolved hex: struct.accent, bg, bg-alt, bg-tint, surface (B1); from
 *   engine_version 2 also block.base, block.highlight, block.ink, block.ink2 and scrim (#RRGGBBAA)
 * @property {Record<string, any>} background    {preset, params, seed, frozen_at}
 * @property {Record<string, any>} transition_in {kind, seconds}
 * @property {string | null} [hold_reason]
 * @property {Record<string, any> | null} [presenter]   {clip_key, crop, rect, t_exit, src_fps}
 * @property {Record<string, any>} [text_fit]    slot -> {size, lines}; also preflight's remedies before B2a ({size},
 *   {scrim}, with `variant: "safe"`): the legacy channel the stage still reads after `fixes` (core.js fixesOf)
 * @property {LookFixes} [fixes]                 B2a: preflight's remedies (served timelines only); the stage reads them
 *   first
 * @property {Record<string, LookIcon>} [icons]  B2a: board item id -> icon
 * @property {LookRefs} [refs]                   B2a
 * @property {number} [accent_fade]              B2a: seconds (0.6-1.0) over which the accent cross-fades in (R-52)
 * @property {string} [continues_from]           B2a: the scene just before, whose final state this one continues
 */

/**
 * @typedef {'reveal' | 'fill' | 'cue_term' | 'look_here' | 'draw' | 'count' | 'var' | 'output' | 'countdown'
 *   | 'answer' | 'exit'} VisualEventKind
 */

/**
 * A visual moment: Python decides when (events.py), the stage decides how. Scene-relative seconds.
 * @typedef {object} VisualEvent
 * @property {number} t
 * @property {VisualEventKind} kind
 * @property {string | null} [target]   board item id, "item:part", "panel"
 * @property {number | null} [dur]
 * @property {boolean} [anchored]
 * @property {string | null} [sfx]
 * @property {string} [words]           anchor words (Studio only)
 */

/**
 * @typedef {object} KineticWord
 * @property {string} text
 * @property {number} start
 * @property {number} end
 * @property {boolean} [key]
 */

/**
 * @typedef {object} KineticChunk
 * @property {number} start
 * @property {number} end
 * @property {KineticWord[]} words
 * @property {string[]} lines
 * @property {boolean} [blank]
 */

/**
 * @typedef {object} MusicCue
 * @property {string} asset_key
 * @property {number} start          absolute seconds
 * @property {number} end
 * @property {number} [offset]
 * @property {number} [gain_lu]
 * @property {number} fade_in
 * @property {number} fade_out
 * @property {'qsin' | 'tri'} [xfade]
 * @property {Array<[number, number]>} [loops]
 */

/**
 * @typedef {object} MusicPlan
 * @property {MusicCue[]} cues
 * @property {Array<[number, number]>} duck   (absolute t, gain dB)
 * @property {'bed' | 'gaps_bookends' | 'off'} mode
 * @property {number} [ref_lufs]
 * @property {'normal' | 'low'} [level]
 */

/**
 * @typedef {object} SfxCue
 * @property {number} t              absolute seconds
 * @property {string} sound
 * @property {number} gain_db
 */

/**
 * An overlay resolved to scene-relative seconds (clamped to its scene).
 * @typedef {object} TimedOverlay
 * @property {string} id
 * @property {number} start
 * @property {number} end
 * @property {MediaRef} media
 * @property {'full' | 'panel'} [region]
 * @property {'mute' | 'quiet'} [sound]
 */

// ---------------------------------------------------------------------------------------------
// Schema v3 (Dynamic video): screenplay side (aadhi/schemas/screenplay.py)
// ---------------------------------------------------------------------------------------------

/**
 * @typedef {object} VoiceCalibration
 * @property {number} natural_wpm
 * @property {string} control
 */

/**
 * @typedef {object} VoiceCast
 * @property {string} narrator                 VoicePersona id
 * @property {string | null} [aadhi]
 * @property {string | null} [student]
 * @property {Record<string, VoiceCalibration>} [calibration]
 */

/**
 * @typedef {object} MusicChoice
 * @property {'generated' | 'library' | 'upload' | 'procedural' | 'off'} source
 * @property {string | null} [provider]
 * @property {string | null} [mood]
 * @property {string | null} [asset_key]
 * @property {'bed' | 'gaps_bookends' | 'off'} [mode]
 * @property {'normal' | 'low'} [level]
 */

/**
 * @typedef {object} HookRef
 * @property {string} question_item            "<scene_id>/<item_id>"
 * @property {'question' | 'true_false' | 'stakes' | 'mystery' | 'challenge' | 'story' | 'stat'} kind
 * @property {string | null} [answer_beat]     "<scene_id>/<beat_id>"
 */

/**
 * Screenplay.presentation (absent = the classic Aadhi presenter mode).
 * @typedef {object} Presentation
 * @property {'dynamic'} [style]
 * @property {'landscape' | 'portrait'} [format]
 * @property {'standard' | 'platform_safe'} [length_profile]
 * @property {'learning' | 'social'} [reel_profile]
 * @property {'messaging' | 'lms' | 'youtube' | 'lesson_page'} [destination]
 * @property {number} look_seed
 * @property {number} [look_epoch]
 * @property {number} [layout_epoch]
 * @property {string} theme_family
 * @property {string} personality
 * @property {string} display_font
 * @property {string} caption_style
 * @property {'marker' | 'underline'} cue_term_style
 * @property {'fullframe' | 'card_left' | 'pip' | 'split' | null} [intro_layout]
 * @property {number | null} [reel_card_x]
 * @property {string} hook_kind
 * @property {string[]} [check_order]
 * @property {string | null} [greeting_id]
 * @property {{ family: string, personality: string }} asset_style
 * @property {VoiceCast} voices
 * @property {MusicChoice} music
 * @property {Record<string, number>} [entity_colors]   entity -> content slot 0..2
 * @property {HookRef | null} [hook]
 * @property {boolean} [aadhi_recap]
 * @property {boolean} [captions_burn]
 * @property {boolean} [sfx]
 * @property {'standard' | 'calm'} [motion]
 * @property {Array<'theme' | 'personality' | 'display_font' | 'voice' | 'music' | 'caption_style'>} [pins]
 */

/**
 * @typedef {object} PresenterSpec
 * @property {'left' | 'center'} clip
 * @property {number} seconds
 * @property {'fullframe' | 'card_left' | 'pip' | 'split' | 'card'} layout
 */

/**
 * SceneBase.look: set by direct.py or the teacher, never LLM-facing.
 * @typedef {object} SceneLook
 * @property {string | null} [template]
 * @property {string | null} [variant]
 * @property {string | null} [background]
 * @property {number | null} [palette_slot]
 * @property {string | null} [transition_in]
 * @property {'formula' | 'code' | 'diagram' | 'think' | null} [hold_reason]
 * @property {PresenterSpec | null} [presenter]
 * @property {string | null} [continues_from]   scene id
 * @property {'auto' | 'settled' | 'pinned'} [lock]
 */

/**
 * Beat.cuts: caption tokens [start, end) removed from the narration.
 * @typedef {object} WordRange
 * @property {number} start
 * @property {number} end
 * @property {string} text
 * @property {'splice' | 'revoice'} [mode]
 */

/**
 * @typedef {object} ClipRange
 * @property {string} asset_key
 * @property {number} in_frame     30 fps grid
 * @property {number} out_frame    exclusive
 */

/**
 * Beat.overlays: B-roll shown over the narration from an anchor word.
 * @typedef {object} Overlay
 * @property {string} id
 * @property {number} at_word
 * @property {string} at_text
 * @property {ClipRange} clip
 * @property {'full' | 'panel'} [region]
 * @property {'mute' | 'quiet'} [sound]
 * @property {string | null} [concept_id]
 * @property {boolean} [keep_full_length]
 */

/**
 * @typedef {object} ClipCaption
 * @property {number} start   clip-relative seconds
 * @property {number} end
 * @property {string} text
 */

/**
 * The fields a clip scene (type "clip", feature "clip") adds to the common scene fields.
 * @typedef {object} ClipSceneFields
 * @property {'clip'} type
 * @property {ClipRange} clip
 * @property {'keep' | 'mute'} [sound]
 * @property {number} [gain_db]
 * @property {'contain_blur' | 'contain' | 'cover'} [fit]
 * @property {{ landscape?: [number, number], portrait?: [number, number] }} [focus]
 * @property {'freeze' | 'loop'} [end_behavior]
 * @property {ClipCaption[]} [captions]
 * @property {string} [credit]
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
 * @property {TimelinePresentation | null} [presentation]   v3 Dynamic (absent in classic timelines)
 * @property {MusicPlan | null} [music]                     v3 Dynamic
 * @property {SfxCue[]} [sfx]                               v3 Dynamic (absent when none)
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

/** Board item kinds, in schema order (the classic kinds; the v3 kinds are V3_BOARD_ITEM_KINDS). */
export const BOARD_ITEM_KINDS = Object.freeze([
  'heading', 'bullet', 'paragraph', 'definition', 'formula', 'callout_info', 'callout_tip', 'callout_warning',
  'misconception', 'code', 'table', 'figure', 'example_step', 'takeaway',
]);

/** Schema v3 board item kinds (Dynamic lectures, feature "items_v3"; aadhi.schemas.screenplay.V3_BOARD_ITEM_KINDS). */
export const V3_BOARD_ITEM_KINDS = Object.freeze(['question', 'stat', 'step', 'quote', 'pair']);

/** DYNAMIC_FEATURES names (aadhi/schemas/features.py): a server refuses writes using a feature it does not enable
 * (422 feature_unavailable). */
export const DYNAMIC_FEATURES = Object.freeze(['presentation', 'items_v3', 'cuts', 'overlays', 'clip', 'voice_override']);

/** The highest screenplay / timeline schema_version this frontend understands; a server answers 409 newer_document
 * for documents above what it reads. */
export const READER_SCHEMA_VERSION = 3;

/** Scene types rendered by the board scene view. */
export const BOARD_SCENE_TYPES = Object.freeze(['title', 'content', 'example', 'summary', 'key_takeaway', 'recap']);

/** Mascot positions accepted by Layout.mascot_position. */
export const MASCOT_POSITIONS = Object.freeze(['left', 'right', 'center', 'popup_bottom_left', 'popup_bottom_right', 'hidden']);

/** Analytics event names accepted by POST /api/analytics/events (aadhi.models.ANALYTICS_EVENTS). */
export const ANALYTICS_EVENTS = Object.freeze([
  'session_start', 'scene_enter', 'scene_complete', 'quiz_answer', 'pause', 'resume', 'seek', 'complete',
]);
