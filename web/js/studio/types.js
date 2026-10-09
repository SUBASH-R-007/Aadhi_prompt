// @ts-check
/**
 * Shared JSDoc types for the Studio (docs/API.md shapes and the view contract).
 * No runtime behaviour.
 */

/**
 * @typedef {object} User
 * @property {number} id
 * @property {string} username
 * @property {'admin' | 'editor' | string} role
 * @property {boolean} must_change_password
 * @property {boolean} is_active
 * @property {number | null} daily_budget_usd
 * @property {string} [created_at]
 * @property {string | null} [last_login_at]
 */

/**
 * @typedef {object} VersionSummary
 * @property {number} id
 * @property {number} number
 * @property {string} label
 * @property {string} status
 * @property {string} language
 * @property {number} revision
 * @property {number | null} built_revision
 * @property {boolean} timeline_stale
 * @property {boolean} has_timeline
 * @property {number | null} source_version_id
 * @property {{ error?: number, warning?: number, info?: number }} issue_counts
 * @property {string} created_at
 * @property {string} updated_at
 * @property {'source' | 'plan'} [review_stage]  only while awaiting review: what the review waits for
 */

/**
 * @typedef {object} ProjectSummary
 * @property {number} id
 * @property {string} title
 * @property {string} subject_name
 * @property {string} unit_name
 * @property {string} session_number
 * @property {string} session_title
 * @property {string} language
 * @property {{ id: number, username: string }} owner
 * @property {VersionSummary | null} current_version
 * @property {import('./components/jobProgress.js').JobSummary | null} active_job
 * @property {string} created_at
 * @property {string} updated_at
 * @property {LessonStage} [stage]        the current version's stage (absent on older servers)
 * @property {NextStep | null} [next_step] its one suggested action (absent on older servers)
 * @property {{ id: number, status: string, built_revision: number | null, matches_current: boolean } | null} [latest_render]
 *   the current version's newest render (absent on older servers)
 */

/**
 * Where a lesson is in its workflow, derived by the server from stored state (version status, review
 * stage, timeline_stale, renders, active job).
 * @typedef {'reading' | 'source_review' | 'planning' | 'plan_review' | 'writing' | 'ready_to_build' | 'building'
 *   | 'ready_to_render' | 'rendering' | 'video_ready' | 'video_outdated' | 'failed'} LessonStage
 */

/**
 * The one suggested action for a lesson's stage. `href` is a Studio route or same-site path, or null when
 * the action is done on the page (`build`, `render`, `retry`, ...).
 * @typedef {object} NextStep
 * @property {string} action
 * @property {string} label    plain words for the button
 * @property {string | null} href
 */

/**
 * GET /api/versions/{vid}/renders items (and the project page's renders).
 * @typedef {object} RenderSummary
 * @property {number} id
 * @property {number} version_id
 * @property {'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | string} status
 * @property {string} language
 * @property {number | null} built_revision
 * @property {number | null} duration_s
 * @property {string} chapters_text
 * @property {{ video?: string, srt?: string, vtt?: string }} downloads
 * @property {Record<string, any>} options
 * @property {import('./components/jobProgress.js').JobSummary | null} job
 * @property {string} created_at
 * @property {boolean} [matches_current]   made from the version's current revision with an up-to-date timeline
 *   (absent on older servers)
 * @property {string | null} [preview_url] signed, streamable MP4 URL when the server offers one
 */

/**
 * One item of GET /api/videos (the current user's videos across their lectures, newest first).
 * @typedef {object} VideoItem
 * @property {number} render_id
 * @property {number} project_id
 * @property {string} project_title
 * @property {number} version_id
 * @property {number} version_number
 * @property {string} created_at
 * @property {string} status
 * @property {number | null} duration_s
 * @property {number | null} size_bytes
 * @property {number | null} width
 * @property {number | null} height
 * @property {boolean} matches_current
 * @property {string | null} download_url
 * @property {string | null} preview_url   signed and streamable; null unless the render succeeded
 * @property {boolean | null} qa_ok        the server's check of the finished MP4 (null: not checked)
 * @property {string | null} [subject_name]   the lecture's fields that tell lectures sharing a title apart, as the
 *   project list does (absent on older servers)
 * @property {string | null} [unit_name]
 * @property {string | null} [session_number]
 * @property {string | null} [session_title]
 * @property {string | null} [project_language]
 * @property {string | null} [project_created_at]
 */

/**
 * GET /api/videos?limit=&offset=.
 * @typedef {object} VideoList
 * @property {VideoItem[]} items
 * @property {number} total
 */

/**
 * GenerationOptions (aadhi/pipeline/base.py) as the API sends and accepts them.
 * @typedef {object} GenerationOptions
 * @property {string} language
 * @property {string | null} board_language
 * @property {string} audience
 * @property {number} target_minutes
 * @property {'overview' | 'standard' | 'deep'} depth
 * @property {string | null} subject_name
 * @property {string | null} unit_name
 * @property {string | null} session_number
 * @property {string | null} session_title
 * @property {string} previous_session_summary
 * @property {string} extra_instructions
 * @property {boolean} include_quizzes
 * @property {number} quiz_every_n_concepts
 * @property {boolean} allow_manim
 * @property {boolean} allow_freeform_manim
 * @property {boolean} allow_generated_images
 * @property {boolean} allow_ai_video
 * @property {number} max_ai_videos
 * @property {boolean} allow_interactive
 * @property {boolean} allow_gifs
 * @property {boolean} [prefer_library_visuals]   use a well-matching library picture instead of generating one
 *   (default false; absent on servers that predate the library)
 * @property {boolean} review_plan
 * @property {string | null} tts_provider
 * @property {string | null} tts_voice
 * @property {string | null} tts_rate
 * @property {LlmEngineId | null} llm_provider   null = the server default engine
 * @property {string | null} llm_model_plan     admin override (always null for non-admins)
 * @property {string | null} llm_model_script   admin override (always null for non-admins)
 */

/**
 * GET /api/projects/{id}.
 * @typedef {object} ProjectDetail
 * @property {ProjectSummary} project
 * @property {VersionSummary[]} versions
 * @property {Array<{ id: number, filename: string, mime: string, size_bytes: number, page_count: number | null, created_at: string }>} sources
 * @property {import('./components/jobProgress.js').JobSummary[]} jobs   latest 20
 * @property {GenerationOptions} [options]   the project's stored options ("Regenerate lecture" starts from them);
 *   absent on servers that predate it
 */

/**
 * AI engine id (GenerationOptions.llm_provider; null in options = the server default engine).
 * "fake" is the offline test engine, only listed by test/demo servers.
 * @typedef {'gemini' | 'openai' | 'anthropic' | 'fake'} LlmEngineId
 */

/**
 * Model per pipeline tier of one engine.
 * @typedef {object} LlmModels
 * @property {string} plan
 * @property {string} script
 * @property {string} critic
 * @property {string} fast
 */

/**
 * Whose API key an engine runs on for the signed-in user: their own saved key, a server key
 * saved by an admin in the Studio, the server's .env key, or none.
 * @typedef {'personal' | 'server' | 'env'} KeySource
 */

/**
 * One entry of GET /api/meta `llm.engines`.
 * @typedef {object} LlmEngineInfo
 * @property {LlmEngineId} id
 * @property {string} label
 * @property {boolean} configured   false: no API key for the engine (neither the user's own key nor a server key)
 * @property {LlmModels} models
 * @property {KeySource | null} [key_source]   absent on servers without saved API keys
 */

/**
 * GET /api/meta `api_keys`.
 * @typedef {object} ApiKeysMeta
 * @property {boolean} enabled            keys can be saved in the Studio (server keys by admins)
 * @property {boolean} personal_enabled   every user may save their own keys
 */

/**
 * A saved key as the API shows it: never the key itself.
 * @typedef {object} CredentialView
 * @property {string} hint                    masked key, e.g. "sk-ant-…a1B2" (vendor prefix + last 4 characters)
 * @property {string} updated_at
 * @property {string | null} last_verified_at
 * @property {string | null} last_error       message of the latest failed test (null after a passed one)
 * @property {boolean} readable               false: the server can no longer decrypt it (re-enter the key)
 */

/**
 * One provider of GET /api/keys (also the body of PUT /api/keys/{provider}).
 * @typedef {object} PersonalKeyRow
 * @property {string} provider              'gemini' | 'openai' | 'anthropic'
 * @property {string} label
 * @property {CredentialView | null} personal
 * @property {boolean} server_available     a server key (Studio or .env) exists for the provider
 */

/**
 * GET /api/keys.
 * @typedef {object} PersonalKeysResponse
 * @property {boolean} enabled   personal keys are allowed on this server
 * @property {PersonalKeyRow[]} providers
 */

/**
 * One provider of GET /api/admin/keys (also the body of PUT /api/admin/keys/{provider}).
 * @typedef {object} ServerKeyRow
 * @property {string} provider
 * @property {string} label
 * @property {CredentialView | null} stored        the key saved in the Studio
 * @property {boolean} env                         the server's .env file has a key
 * @property {'stored' | 'env' | null} active_source
 */

/**
 * GET /api/admin/keys.
 * @typedef {object} ServerKeysResponse
 * @property {boolean} enabled   keys may be saved in the Studio
 * @property {ServerKeyRow[]} providers
 */

/**
 * POST /api/keys/{provider}/test and /api/admin/keys/{provider}/test.
 * @typedef {object} KeyTestResult
 * @property {boolean} ok
 * @property {string} message
 */

/**
 * GET /api/usage/me?days= (fields the Studio reads).
 * @typedef {object} UsageMe
 * @property {number | null} daily_budget_usd
 * @property {number} today_usd       spend that counts toward the daily budget
 * @property {number} total_usd
 * @property {number} [own_key_usd]   spend paid with the user's own API keys (never counts toward the budget)
 * @property {Array<{ date: string, usd: number }>} [by_day]
 * @property {Array<{ provider: string, model?: string | null, calls?: number, usd: number }>} [by_provider]
 * @property {Array<{ operation: string, usd: number }>} [by_operation]
 */

/**
 * GET /api/meta `llm`.
 * @typedef {object} LlmMeta
 * @property {LlmEngineId} provider   server default engine
 * @property {boolean} configured     the default engine is configured
 * @property {LlmModels} models       the default engine's models
 * @property {LlmEngineInfo[]} [engines]   absent on servers that predate per-lecture engines
 * @property {string[]} override_allowlist   admin model overrides (empty for non-admins)
 */

/**
 * One item of the user's media library: GET /api/library `items`, and the body of POST /api/library
 * and PATCH /api/library/{id}. A user only ever sees their own items.
 * @typedef {object} LibraryItem
 * @property {number} id
 * @property {string} asset_key            usable as `override_asset_key` once attached to a lecture
 * @property {'image' | 'video'} kind
 * @property {string} title                at most 120 characters
 * @property {string} description          at most 1000 characters
 * @property {string[]} keywords           at most 20, each at most 40 characters
 * @property {'upload' | 'generated' | 'figure'} source
 * @property {number | null} width
 * @property {number | null} height
 * @property {number | null} duration_s
 * @property {string} url                  signed content URL
 * @property {string | null} poster_url    a video's poster (signed), when there is one
 * @property {string} created_at
 * @property {string} updated_at
 * @property {string | null} last_used_at
 * @property {number} used_in              how many of the user's lectures it was added to or made for (asset refs;
 *                                        not reduced when a scene later shows other media)
 * @property {string | null} prompt        generated media: the description it was made from
 * @property {string | null} provider
 * @property {string | null} model
 */

/**
 * GET /api/library?q=&kind=&source=&limit=&offset=.
 * @typedef {object} LibraryList
 * @property {LibraryItem[]} items
 * @property {number} total
 */

/**
 * POST /api/library/{id}/describe: a suggestion the teacher can still change before saving.
 * @typedef {object} LibraryDescription
 * @property {string} title
 * @property {string} description
 * @property {string[]} keywords
 */

/**
 * GET /api/library/suggestions?version_id=: library items that fit each scene's visual need.
 * @typedef {object} LibrarySuggestions
 * @property {Array<{ scene_id: string, matches: Array<{ item: LibraryItem, score: number }> }>} scenes
 */

/**
 * The review state of one scene's visual (kept outside the screenplay: approving never changes a build).
 * @typedef {object} VisualReviewState
 * @property {'pending' | 'approved' | 'changed' | 'removed'} state
 * @property {boolean} stale            the scene's visual request changed after the decision
 * @property {string | null} note
 * @property {string | null} updated_at
 */

/**
 * One scene of GET /api/versions/{vid}/visual-review (also the answer of PUT .../visual-review/{scene_id}
 * and the `scene` of POST .../scenes/{scene_id}/visual).
 * @typedef {object} SceneVisual
 * @property {string} scene_id
 * @property {number} index              0-based position in the screenplay
 * @property {string} title
 * @property {boolean} [hidden]          skipped in the video: listed, but nothing builds it (no new version / retry)
 * @property {'image' | 'video' | 'figure' | 'manim' | 'chart' | 'graph' | 'model_3d' | 'terminal' | 'interactive' | 'none'} kind
 * @property {'generated' | 'library' | 'upload' | 'figure' | 'manim' | 'builtin' | 'fallback' | 'none'} source
 * @property {string | null} provider
 * @property {string | null} model
 * @property {string | null} prompt
 * @property {string | null} url         signed media URL
 * @property {string | null} poster_url
 * @property {'ready' | 'missing' | 'failed' | 'fallback' | 'ambiguous' | 'stale'} status
 * @property {string | null} status_reason   plain words
 * @property {VisualReviewState} review
 * @property {number} variant           0 = the first AI version
 * @property {Array<'approve' | 'new_version' | 'choose_library' | 'upload' | 'remove' | 'retry' | 'confirm_paid_retry'>} actions
 * @property {Array<'image' | 'video'>} [accepts]   library kinds a pick or upload for this scene may have
 * @property {Array<{ code: string, severity: string, message: string }>} findings
 * @property {number} library_suggestions   library items that match the scene (never the media it shows now)
 */

/**
 * GET /api/versions/{vid}/visual-review.
 * @typedef {object} VisualReview
 * @property {{ total: number, approved: number, pending: number, changed: number, removed: number, needs_attention: number }} summary
 * @property {SceneVisual[]} scenes
 * @property {number} [revision]   the screenplay revision the scenes were read at (send it with the actions)
 */

/**
 * POST /api/versions/{vid}/scenes/{scene_id}/visual.
 * @typedef {object} VisualActionResult
 * @property {number} revision
 * @property {SceneVisual} scene
 * @property {number | null} job_id     the build it started, if any
 */

/**
 * One scene of GET /api/versions/{vid}/changes: the scene compared with what Aadhi generated.
 * @typedef {object} SceneChange
 * @property {string} scene_id
 * @property {'unchanged' | 'edited' | 'added' | 'moved' | 'removed'} status
 * @property {number | null} generated_index   0-based position in the generated screenplay (null when added)
 * @property {number | null} current_index     0-based position now (null when removed)
 * @property {string[]} fields_changed         scene fields that differ from the generated scene
 * @property {number} history                  earlier versions kept by "Regenerate scene" (scene_history)
 */

/**
 * GET /api/versions/{vid}/changes. `available` is false when the version has no generated copy (imported,
 * or made before the copy was kept).
 * @typedef {object} VersionChanges
 * @property {boolean} available
 * @property {SceneChange[]} scenes
 */

/**
 * POST /api/versions/{vid}/scenes/{scene_id}/revert (body: `revision`, `to` = "generated" | "history",
 * optional `history_index`, optional `position` for a removed scene).
 * @typedef {object} RevertResult
 * @property {number} revision
 * @property {string} scene_id
 */

/**
 * @typedef {object} AppContext
 * @property {() => User | null} user
 * @property {() => Promise<any>} meta                     GET /api/meta (cached)
 * @property {() => void} invalidateMeta   drop the cached /api/meta (after saving or removing an API key)
 * @property {(hash: string, opts?: { replace?: boolean, force?: boolean }) => void} navigate
 * @property {(hash: string) => void} replaceHash   update the URL (query state) without re-routing
 * @property {(message: string, opts?: import('./components/toast.js').ToastOptions) => () => void} toast
 * @property {(user: User | null) => void} setUser
 * @property {(title: string) => void} setTitle
 * @property {(guard: ((next: any) => boolean | Promise<boolean>) | null) => void} setLeaveGuard
 * @property {(err: unknown, fallback?: string) => void} reportError   toast an error (auth errors are handled globally)
 * @property {() => void} continueAfterLogin   go to the route the user wanted before signing in
 */

/**
 * @typedef {object} ViewArgs
 * @property {Record<string, number>} params
 * @property {Record<string, string>} query
 * @property {AppContext} app
 * @property {AbortSignal} [signal]   aborted when the user leaves the route (also while the
 *   view is still loading): stop before setting a leave guard or opening dialogs
 */

/**
 * @typedef {object} ViewHandle
 * @property {() => void} [destroy]
 */

/**
 * @callback ViewMount
 * @param {HTMLElement} container
 * @param {ViewArgs} args
 * @returns {ViewHandle | void | Promise<ViewHandle | void>}
 */

export {};
