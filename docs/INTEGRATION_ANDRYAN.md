# Integration of `origin/andryan`

`origin/andryan` is a fork of the v1 prototype (the `index.html` + `server.py` monolith) extended
over 22 "phases". It was compared feature by feature with v2. Batch 1 ports the features where the
fork was better or where v2 had nothing; [batch 2](#batch-2) adds the presenter and synchronisation
work, the quality checks, the source report and the batch 1 carry-overs; [batch 3](#batch-3) adds the
media library and the Visual Review; [batch 4](#batch-4) adds the editor workflow, the guided Studio, the
video history and imports from the fork. Each one was
**re-implemented in the v2 architecture** (Screenplay -> Timeline -> live player + deterministic
renderer, durable jobs, content-addressed `AssetStore`, typed `Settings`, ES-module frontend,
Alembic). No monolith code was copied.

This page records what was taken, where it lives now, and what was deliberately left out. Feature
ids are those of the comparison. A `-corrected` id is the reviewed version of the features it names,
and it supersedes them.

## Batch 1: what was integrated

### Source ingest and storable JSON

| Feature id | Where it lives | Notes |
|---|---|---|
| `non-finite-json-guard`, `non-finite-json-guard-corrected`, `p19-save-validation-nan`, `p20-json-write-hardening` | `aadhi/schemas/jsonsafe.py`, `aadhi/api/storable.py`, `StrictModel` (`allow_inf_nan=False`), the screenplay, version-action, project and options routes, `ProjectVersion.get_*` / `VersionSnapshot.get_*` | The policy is to refuse on write and sanitise on read. Writes with NaN, Infinity, `1e999` or a lone surrogate get a 422 that names the JSON path. Rows stored earlier are served with `null` / U+FFFD and loaded by jobs with `validate_stored`. The database JSON serializer is unchanged. |
| `p11-fenced-code-preservation` | `aadhi/pipeline/codeblocks.py`, `chunking.py`, `source_scope.py`, `ingest.py`, `docx_extract.py` | A code fence is one unit and is kept verbatim. DOCX Code / Source Code / HTML Preformatted styles become fences. Word drops empty code paragraphs, so blank lines inside DOCX code are lost. |
| `p11-plain-text-heading-forms` | `aadhi/pipeline/ingest.py` | Setext headings and `--- Page N ---` markers are recognised. `Title:` lines are left to `source_scope`. |
| `p11-pdf-monospace-code` | `aadhi/pipeline/pdf_worker.py` (`mono`, `lead`), `pdf_layout.py` | Code detection is off when more than half the text is monospace. |
| `p11-instruction-like-text` | `aadhi/pipeline/ingest.py` (`INSTRUCTION_LIKE`) | The warning gives a count and page numbers, never the text, and the text is never removed. The prompt sentence came in batch 2. |
| `p11-script-language-detection` | `aadhi/pipeline/ingest.py` (`unsupported_script`) | Warns for 13 scripts. `detect_language` is unchanged. |

`INGEST_VERSION` is now 8. Each version keeps reading its own extract (`generation_meta["ingest_key"]`),
so the chunk ids its references point at stay valid.

### Mascot and player

| Feature id | Where it lives | Notes |
|---|---|---|
| `mascot-left-clip-black-bars`, `mascot-black-bar-fix`, `mascot-black-bar-fix-corrected` | `video_template/aadhi_left_clean.mp4`, `MASCOT_CLIPS["left"]`, `RETIRED_MASCOT_CLIPS` + `resolve_urls`, `scripts/clean_mascot_clip.py` | The bars are filled by mirroring a clean plate (see the rejected-approach list below). The original file stays. Lectures that are already published are served the clean clip without a rebuild. |
| `mascot-poster-assets` | `video_template/posters/`, `posterUrl()` in `web/js/player/mascot.js`, Dockerfile | Posters are found by name: `<clip>.mp4` maps to `posters/<clip>.jpg`. The schema is unchanged. |
| `mascot-animated-fallback` | `mascot.js`, `web/css/player.css` | Two poster frames breathe with a scale-only animation. It is off with reduced motion and in render mode. |
| `mascot-clip-error-recovery` | `mascot.js` | Retries after 1 s and 3 s, then marks the clip failed. Play failures switch to the fallback. |
| `mascot-frame-gated-crossfade` | `mascot.js` | The swap waits for `loadeddata`, at most 1.5 s. |
| `mascot-watchdog-stall-recovery` | `mascot.js` `tick()` | It is called from the player's frame loop, at most once a second. |
| `mascot-lesson-preload` | `mascot.js`, `Player.load` | Clips the lesson does not use load metadata only. |
| `playback-gate-autoplay-block` | `web/js/player/player.js` (`blocked` event) | No new button: the existing play button provides the gesture. |
| `mascot-debug-status-metrics` | `MascotController.getStatus()` | No on-screen debug overlay. |
| `legacy-placement-normalization` | `aadhi/legacy/convert.py` (`_mascot_position`) | v1 aliases and v1's implicit placement rule. |

### Generated images and AI video (providers)

| Feature id | Where it lives | Notes |
|---|---|---|
| `provider-error-taxonomy`, `pollinations-402-default` | `aadhi/providers/base.py` (`ProviderUnavailable`, `InvalidMediaOutput`, `OperationLost`), `_common.py`, adapters | HTTP 401/402/403 is permanent for the provider and is not retried every build. |
| `provider-fallback-chain` | `aadhi/pipeline/assets.py`, `factory.media_chain`, `IMAGE_/VIDEO_FALLBACK_PROVIDERS` | Separate fallback lists keep `IMAGE_PROVIDER` / `VIDEO_PROVIDER` semantics intact. |
| `provider-cooldown` | `aadhi/providers/health.py` | Per process, per (provider, key scope). A cooling provider is tried last, never dropped. |
| `provider-capabilities` | adapter attributes `paid`, `aspects`, `max_prompt_chars`, `resumable` | The preferred provider is never reordered. |
| `media-output-validation` | `image/verify.py`, `video/veo.py`, `assets.media_suspect` | The optional ffmpeg blank-video check was skipped. |
| `safe-provider-download` | `aadhi/providers/_http.py` `fetch_limited` | |
| `generation-provenance` | `Asset.meta` (provider, model, aspect, timing, job, requested provider, fallback reasons, resumed) | `MediaInfo` and the editor inspector are not changed yet (see open items). |
| `veo-adapter` | `aadhi/providers/video/veo.py` (`VEO_DURATION_SECONDS`, `resume()`) | |
| `p9-veo-operation-checkpoint`, `p9-paid-ambiguity-guard`, `p9-provider-attempt-history` | `aadhi/providers/operations.py` (job payload `provider_operations`), `assets.py` | Stored in the job payload, not in a new table. Ambiguous submissions are never re-submitted automatically. |
| `fake-failure-injection`, `p9-resumable-fake-provider-tests` | `tests/providers/media_fakes.py` | Test code only. Production modules carry no test hooks. |
| `provider-status-panel` | `GET /api/admin/media-providers`, Admin page "Media providers" | Admin only, configuration only, no provider call. |
| `secret-sanitization-patterns` | `Settings.redact` | |
| `production-fake-provider-guard` | `Settings.validate_for_runtime`, `ALLOW_FAKE_PROVIDERS` | |
| `per-request-provider-choice` | `GenerationOptions.image_provider`, `api/generation.py` (422), options form picker | Covers the image provider only. Model, aspect and duration stay server settings. |

### MP4 render

| Feature id | Where it lives | Notes |
|---|---|---|
| `chapters-opening-title-guard`, `chapters-opening-title-guard-corrected`, `p21-chapter-opening-name` | `aadhi/compose/chapters.py` `opening_title`, `timeline.build_chapters` | |
| `mp4-soft-subtitle-track` | `aadhi/compose/video.py`, `soft_subtitles` / `RENDER_SOFT_SUBTITLES` | Off by default, because MP4 marks its only caption track as enabled. |
| `cfr-grid-and-bt709` | `video.py`, `ffmpeg.py`, `screenshot.py` (`RENDER_COLOR_BT709`) | Frames were measured on the n/30 s grid, so `-bf 0` was not added. |
| `post-render-output-qa`, `video-qa-helper` | `aadhi/compose/qa.py`, `evals/video_qa.py` | `syncFrame`, `sharpness` and `colourArea` were not ported. |
| `render-capability-detection` | `aadhi/compose/capabilities.py`, `GET /api/renders/capabilities`, worker start-up log, `/api/meta` `features.render` | |
| `render-admission-limits` | `aadhi/api/routers/renders.py` (`check_admission`), also on job retries | |
| `durable-render-workspace` | `aadhi/compose/workspace.py`, worker and `cleanup` sweeps | Screenshots are retaken on retry. Identical ones still hit the segment cache. |
| `preview-export-parity-eval` | `evals/render_parity.py` | |
| `glass-blur-parity` | `web/render.html` (`glass=1`), `RENDER_GLASS_PANELS`, premultiplied state cross-fades (`ffmpeg.plan_states`, `frames.write_state_mixes`) | Takes the colours only. The blur is not reproduced. Cross-fades mix the two states, so a translucent panel never darkens during a state change. |
| `render-internal-base-url` | `RENDER_BASE_URL` | |
| `p9-render-row-settle` | `aadhi/jobs/queue.py` `settle_render` | |
| `missing-visuals-preflight` | `aadhi/compose/preflight.py`, `GET .../render/preflight`, `allow_degraded`, editor render dialog, project page | |

### Jobs safety

| Feature id | Where it lives | Notes |
|---|---|---|
| `cross-process-generation-lock`, `cross-process-generation-lock-corrected`, `p9-cross-process-generation-claim` | `aadhi/storage/assets.py` claims, `AssetClaim` model, migration `0003_asset_claims`, worker `claim_owner` | Also fixes two in-process waiter bugs in `get_or_create`. |
| `p9-llm-stage-checkpoint` | `aadhi/pipeline/checkpoint.py`, orchestrator | Checkpoints per stage; batch 2 adds per-scene checkpoints. |
| `p9-still-wanted-gate` | orchestrator guard, `storage.assets.generation_guard`, `guarded=False` for resumes | The whole revision is compared, not single scenes. |
| `p9-run-retention` | `aadhi/jobs/cleanup.py` (expired claims) | Job-row retention was skipped as optional. |
| `ai-generation-identity` | `storage.assets.normalize_prompt` / `media_key`, `assets._image_keys` / `_video_keys` | Keys are unchanged for normalised prompts, and the pre-normalisation key is still looked up. |
| `cache-serves-when-generation-off` | `AssetStore.first_existing`, `assets.earlier_image` / `earlier_video` | Never used when the lecture opted out of the medium. |

### Manim sandbox

| Feature id | Where it lives | Notes |
|---|---|---|
| `p10-runtime-audit-hook` | `aadhi/manim/runtime/hardening.py` | Appended to the script as text and kept out of cache keys. |
| `p10-frame-and-profile-enforcement` | `hardening.py` | |
| `p10-workspace-size-watchdog` | `aadhi/manim/sandbox.py` `run_process` | |
| `p10-output-validation` | `sandbox.find_output`, `_finalize_output`, `render._check_output` | |
| `p10-orphan-sweep` | `sandbox.sweep_orphans`, called by workers (start and hourly) and by `cleanup` | |
| `p10-windows-job-object` | `aadhi/manim/winjob.py`, POSIX `RLIMIT_CPU` | No `RLIMIT_AS`, because it breaks numpy and cairo. |
| `p10-sandbox-health-check` | `aadhi/manim/health.py`, `python -m aadhi.cli manim-check`, `GET /api/admin/manim/sandbox` | Admin only and rate limited. It is not part of `/api/meta`. |
| `p10-isolation-probe-tests` | `tests/manim/test_docker_isolation.py`, `test_hardening.py` | |
| `p10-failure-categories` | `ManimError.category` / `.friendly`, `Issue.data.category` | |
| `p10-render-metrics` | `peak_memory_mb`, `frames` in the asset meta | |
| `p10-render-profiles-hard-max` | bounded `MANIM_*` settings, hard maximum 1920×1350 at 60 fps | Merged into bounded settings, with no separate profiles. |

## Batch 1: deliberately not taken

* **Monolith code and its security problems.** None of these were ported: the hard-coded admin
  password, CORS `*`, unauthenticated routes (`/get-image`, `/get-gif`, the probe-anyone
  `/api/manim/sandbox`), 7-day bearer tokens, the second SQLAlchemy base with `ALTER TABLE` at
  start-up, `print` logging and naive `utcnow`. The same goes for `index.html` / `export.js` /
  `presenter.js` (UMD scripts and `innerHTML`) and the v1 debug and patch scripts.
* **Test hooks in production code** (`AI_TEST_CRASH_AT`, `MANIM_TEST_CRASH_AT`, `AI_FAKE_PROVIDER`,
  `AI_FAKE_FAIL`, `FAKE_TTS` tones, `AI_FAKE_STATE_DIR`). Their test value comes from test-only
  stand-ins instead (`tests/providers/media_fakes.py`, a `Crash` that behaves like a kill).
* **Parallel frameworks that duplicate v2**: `ai_runs` / `ai_recovery` (v2 has the durable job
  queue), the `AICache` / `AIGenerationLock` tables (v2 has the content-addressed `AssetStore` plus
  `asset_claims`), `VideoExport` / `ExportOutput` (v2 has `Render`), and the Node virtual-clock
  render worker (v2 has a deterministic ffmpeg compositor).
* **Rejected approaches**:
  * The `fillborders=smear` bar fix: it stretches Aadhi's hand into a streak once per loop.
  * The CSS clip-path mask over the bars: the asset was fixed instead.
  * `-bf 0`: measured unnecessary.
  * Glass blur in the MP4: only the colours are reproduced.
  * A `provider_operations` table: the checkpoint lives in the job payload.
* **Partial skips**, each listed in the tables above: the DOCX blank lines inside code, the ffmpeg
  blank-video check, a checkpoint for `regenerate_scene`, screenshot caching across render attempts,
  `MediaInfo` provenance in the editor, and the mascot debug HUD. (Per-scene LLM checkpoints came in
  batch 2.)

## Batch 2

Batch 2 follows the same rules as batch 1. These changes alter the default output, and the player
and the MP4 change identically:

* The right, center, popup and hidden mascot clips are watermark-free. Published lectures get the
  clean clips without a rebuild.
* A cue bubble beside Aadhi is on by default. `MASCOT_CUES=false` turns it off.
* Formula legend rows, terminal output, side-panel pulses and authored highlights land on the spoken
  word, in lectures where a match is found. `SYNC_WORD_ANCHORS=false` restores beat-start timing.

Two other changes affect only one side:

* The live speech bob follows the narration loudness. The MP4 never had the bob, so it is unchanged.
* Existing versions show new lint notes and warnings (quality, presenter, sync). None is an error, so
  saving, building and rendering are never blocked.

Four prompts changed, so the eval baselines must be re-run (see open items).

### Presenter and mascot

| Feature id | Where it lives | Notes |
|---|---|---|
| `mascot-behaviour-states` | `web/js/player/mascot-state.js`, `schedule.sceneStateAt` (`mascotState`, `mascotCue`), `data-state` on the mascot layer | Pure function of the scene and time, so seeking and render mode agree. States: idle in the lead-in and tail; explaining while a beat reveals or fills a board item, talking otherwise; thinking in pauses of at least 1.2 s; question, listening and success in quizzes. Our clips show behaviour states only, so there is no `Branding.mascot_state_clips`. |
| `mascot-state-cue-bubble` | `MascotController.mountCue` / `setState`, `web/css/player.css`, `Layout.mascot_cues`, `MASCOT_CUES` | Drawn in the scene root at fixed stage pixels (left, right and centre; none for popup and hidden), so the render screenshots it exactly. The anchors clear every frame of their clip (a slow test decodes all 192 frames), and a quiz keeps its "?" through the gap before the countdown. `MASCOT_CUES` is applied when a timeline is served, so the player and the MP4 always agree without a rebuild. Static in render mode and with reduced motion. The render key gains a cue part only while a bubble shows. Skipped: the "talking" indicator on the fallback poster, where the speech bob keeps running instead. |
| `speech-loudness-envelope` | `aadhi/pipeline/envelope.py`, `pipeline/assets.py` (asset meta and manifest), `SceneAudio.envelope`, `TimedScene.audio_envelope`, `web/js/player/audio.js` `envelopeLevel` | 30 values per second (the render frame rate, not their 25), about 72 KB per 30 minutes. Narration cached earlier is measured once from the stored file: no TTS re-run and no cache-key change. Priority: envelope, then the WebAudio meter, then the synthetic bob. |
| `presenter-vocab-fallback` | `web/js/player/presenter-vocab.js` | Their behaviour, expression and gesture tables, with the nearest-supported fallback. With our clips only behaviours map. |
| `presenter-director-rules` | role from the typed scene type in `mascot-state.js`; `aadhi/pipeline/presenter_lint.py` `layout.mascot_crowds_board` | The lint is advisory and never triggers a paid rewrite. Skipped: modes and teaching styles, English-regex role detection, automatic position overrides and a canonicalize default, because each would change generated screenplays. |
| `mascot-left-clip-black-bars`, `mascot-black-bar-fix` (watermark carry-over) | `video_template/{aadhi_right,aadhi_center,aadhi_popup,no_aadhi}_clean.mp4` and their posters, `MASCOT_CLIPS`, `RETIRED_MASCOT_CLIPS`, `scripts/clean_mascot_clip.py --watermark`, Dockerfile `no_aadhi*.mp4` | The "Veo" corner is filled from a temporal-median plate of the floor beside it. The script refuses a clip in which anything moves there. Size, 24 fps, 192 frames and pixel format are unchanged, and the audio is byte-identical. The originals stay. |
| Intro logo watermark (same carry-over) | `video_template/logo_animation_clean.mp4`, `LOGO_VIDEO`, `RETIRED_BRANDING_FILES` + `resolve_urls` (`current_branding_url`), `scripts/clean_mascot_clip.py --watermark --box`, Dockerfile `logo_animation*.mp4` | The generator's sparkle in the bottom-right corner of every logo frame is filled the same way, ramped towards the picture right of the box so the wall's vignette continues. 240 frames and the audio unchanged; the original stays and stored timelines are served the clean file. |

### Presenter and visual synchronisation

The cues are built once, at timeline build (`aadhi/compose/sync.py`), and stored as
`TimedScene.sync_cues` (`SyncCue`). The live player and the render page read them through the same
`schedule.js`. `sync_cues` and `FormulaVariable.beat_id` are left out of the JSON when empty, so
lectures with no match store, hash and render exactly as before.

| Feature id | Where it lives | Notes |
|---|---|---|
| `sync-formula-variable-labels`, `sync-formula-variable-labels-corrected` | `sync.py` (`var` cues), `FormulaVariable.beat_id`, `aadhi/pipeline/sync_lint.py` (`formula.variable_beat_invalid`), the board editor's "Appears" choice | A legend row appears when the narration first names its meaning, or the name of its Greek symbol. Only the formula's reveal beat and the narration-only beats after it are searched; otherwise the row shows with the formula. Rows that are still waiting keep their space. A valid authored `beat_id` wins; an invalid one is ignored and flagged by lint. Skipped: the prompt and gen-model change, which would shift the eval baselines. |
| `sync-output-reveal` | `sync.py` (`output`), `schedule.terminalLineTimes(…, outputAt)`, `panels/timing.terminalOutputAt` | Output starts at the first result word ("prints / printed / outputs / returns / displays / produces …"; the bare keywords "print" and "return" name the code, not its result) spoken after the panel appears and, without an authored show beat, after the code's reveal beat; otherwise the lines are spread as before. A parity test keeps both copies of the timing identical. |
| `sync-visual-focus-pulse` | `sync.py` (`focus`), `SceneState.panelFocus`, `is-focus` in `panels.css` | A 1.6 s pulse when a pointing word comes up to three words before a visual noun ("look at this chart"). A plural-looking form ("maps", "models") counts only after a plural determiner, and "here we map" is a verb. English only; at most 2 per scene, at least 4 s apart. A static outline in render mode and with reduced motion. Skipped: an authored `Beat.focus_panel` and presenter pointing. |
| `sync-word-anchored-emphasis` | `sync.py` (`emphasis`), `SceneState.emphasisParts`, `board.js` | An authored highlight starts when its beat names the item (its `[[keyword]]` or bold span, definition term or table header, else its first distinctive English word). A named table column or term glows on its own. It is re-timed only when named at least `MIN_EMPHASIS_SECONDS` (1.2 s) before its beat ends and not already lit by the previous beat; otherwise it stays lit for the whole beat (the fork held emphasis for a fixed 1.4 s instead). One-letter anchors (a header "A") are never used. `SYNC_AUTO_EMPHASIS` (off) adds emphasis without an authored highlight. Skipped: showing an item before its reveal, and the noisy `beat.unsignalled_mention` lint. |
| `sync-review-summary` | `web/js/player/syncSummary.js`, "Timing of scene N" under the editor preview (`previewPane.js`) | Read-only, and marked "estimated" before the voice exists. |

### Lesson quality and consistency (phase 18)

All checks live in `aadhi/pipeline/quality/` and run inside `validate.lint`. They are deterministic,
never call a model, and none is an error, so none selects a scene for a paid rewrite.
`repair.rewrite_scene` drops `quality.AUTHOR_CODES`, so a rewrite cannot rename a term, switch a code
language or change notation on its own.

| Feature id | Where it lives | Notes |
|---|---|---|
| `p18-terminology-consistency` | `quality/terms.py` | `terminology.variant` / `.casing` (info), read from typed fields. When a term differs in both hyphenation and capitals, it is reported once. |
| `p18-abbreviation-checks` | `quality/abbreviations.py` | `terminology.abbreviation_undefined` / `_late` (info) and `_conflict` (warning). English boards only; capitals used as style are ignored. |
| `p18-concept-naming` | `quality/terms.py` | `concept.naming_variant` (info). Only the same name written differently is compared. |
| `p18-formula-symbol-consistency` | `quality/formulas.py` | `formula.symbol_conflict` (warning), `formula.notation_mismatch` and `formula.legend_missing` (info). Units are never compared. |
| `p18-code-checks` | `quality/code.py` | `code.language_unhighlighted` (warning), `code.mixed_indentation` (warning, the only fixable code) and `code.mixed_languages` (info). A test keeps the language list equal to the player's `PRISM_LANGS`. |
| `p18-title-casing` | `quality/titles.py` | `title.casing_mixed` (info), per title level. Title cards and chapter cards are left out. |
| `p18-board-fit-measurement` | `web/js/studio/views/editor/quality.js` (`measureBoard`, `fitIssue`), `previewPane.js` | Uses the fit the preview player measured on the 1920×1080 stage, which is the MP4's stage. Reported as `board.overflow` / `board.small_text`; never stored and never blocking. Not recorded at render time yet (open item). |
| `p18-density-pacing` | `quality/pacing.py` | `pacing.dense_run` and `board.reading_time` (info). "Sparse scene" was not ported because it conflicts with keeping few words on screen. |
| `p18-diagram-label-checks` | `quality/terms.py` | `figure.caption_variant` (info). The AI-picture disclaimer belongs in the Studio, not in lint. |
| `p18-finding-contract-report` | `quality.js` (`areaOf`, `statusOf`) | The Issue contract is unchanged. |
| `p18-safe-repair-actions` | `quality_report` (POST /lint `quality.repairs`), the editor's "Apply fix", "Regenerate scene" (fixable errors only) and "Build" | A fix is one undoable draft edit. Nothing changes if the field has changed since the check. |
| `p18-quality-panel-ui` | `issuesPanel.js`, `studio.css` | One-line headline and areas (areas with only notes start folded). Severity is shown as an icon and in words; codes sit behind "Show technical details". |
| `p18-pre-export-check` | `compose/preflight.quality_items`, render preflight `quality`, the editor render dialog, the project page | Never blocks, and is listed apart from "the video will differ". |
| `p18-consistency-registry` | POST /lint `quality.registry` | Terms, concepts, abbreviations, symbols, code languages and figures, each with its scene ids. |
| `p18-ai-terminology-assist` | `quality/assist.py`, `prompts/quality_terms.md` (v1), the critic (whole lecture only), `QUALITY_AI_TERMINOLOGY` | Off by default. At most 8 pairs, on the fast model, with validated answers. A confident answer becomes `terminology.ambiguous` (info, source critic). |
| `p18-adversarial-bounds` | `quality/*`, `richlite.tokenize` | Inputs are bounded, and the fork's crafted cases are tested on 200-scene screenplays. The integration also made the rich-lite tokenizer linear: an unclosed `**` or `[[` made it quadratic, and it is equal to the old regex on 800,000 fuzzed strings. The review then bounded three more quadratic passes: term spellings clustered pairwise (`text.MAX_GROUP_FORMS`, 24 per term; 24,000 spellings took over an hour, now about 2 s), abbreviations scanned (only the first 30 are collected, and term initials are indexed once) and legend meanings per symbol (`formulas.MAX_DISTINCT_MEANINGS`). Each output is unchanged for real lessons. |

Skipped: `p18-media-checks`. The asset pipeline and the render preflight already report missing or
degraded media, and the aspect-ratio notice needs asset dimensions and the player's zone geometry.

### Source report and pasted notes (phases 11, 20, 21)

| Feature id | Where it lives | Notes |
|---|---|---|
| `p11-source-outline-and-roles` | `aadhi/pipeline/source_review.py` (`analyze`) | Built from the cleaned source only. Headings are read exactly as the chunker reads them, and a chunk is matched on its full heading path, so a sub-heading repeated under every unit ("Introduction") owns its own text. Heading roles are matched in English, Tamil and Hindi. Deliberately stricter than the fork: a generic word ("Sources", "Goals", "Practice") gives a role only as the whole heading or before a colon, so "Sources of Energy" stays a teaching section. Display only; never sent to a model. |
| `p11-content-inventory-formula-check` | same | Formulas (lines and LaTeX) are checked against one definition index per document. Also counts code blocks (and whether prose explains them), figures, tables and questions. Skipped: the definition, example and important-point classifiers, and the brief-prompt note. |
| `p11-source-quality-findings-readiness` | same; `GET /api/projects/{id}/versions/{vid}/source-report` | 12 `source.*` codes, at most 100 findings with per-code caps, and a bounded near-duplicate check. Readiness is advice only. "No objectives / examples / summary" was dropped, because the planner writes those anyway. Values removed by source scoping are masked everywhere. |
| `p11-source-report-panel` | `web/js/studio/views/sourceReport.js` (`#/p/:id/v/:vid/source`) | A separate page, linked from each version's Actions menu. "Mark as done" is stored only in the teacher's browser. |
| `p11-editable-source-structure-gate` | the `review_source` payload flag, the orchestrator pause (stage `source_review`), `PUT /source-review`, `POST /approve-source`, `effective_brief` / `without_chunks` | A payload flag rather than a `GenerationOptions` field, so options, schemas and checkpoint digests are unchanged. The plan endpoints answer 409 during the source review. Set-aside chunks reach no later stage: a concept still taught from a kept part also loses the examples, `must_explain` points and `why_it_matters` drawn only from the set-aside parts, and `skipped_chunks` keeps every brief skip plus every set-aside (up to 1,000). Skipped: reordering, metadata edits and teacher-written objectives (the last would need a prompt change). |
| `p20-paste-text-source`, `p20-paste-text-source-corrected`, `p21-create-paste-notes` | `web/js/studio/views/newProject.js` | Client-side only. The cleaned text is sent as a `.txt` file through the same upload, so server validation is unchanged. Web pages and lecture JSON are refused. Accepts 200 to 200,000 characters, or less when the server's `limits.max_source_chars` is lower. Longer notes are refused on submit with a message (the box has no `maxlength`, which browsers apply by silently cutting pasted text). |
| `p20-scene-source-trace` | `source_review.scene_trace`, in the report | Which parts each scene cites, and counts of citations to unknown parts. Skipped: the evals metric and any "AI-written" label. |

Limitation: for scanned or maths-heavy PDFs the original file still goes to the writers, so they can
still see set-aside pages there.

### Batch 1 carry-overs and audit fixes

| Item | Where it lives | Notes |
|---|---|---|
| `p9-llm-stage-checkpoint`, per scene | `checkpoint.SceneCheckpoints`, `script.write_scenes_detailed(…, checkpoints=)` | Each scene is stored as it finishes, and a retry writes only the rest. Fallback scenes are never stored. A test shows the resumed lecture equals an uninterrupted run. `regenerate_scene` still has no checkpoint. |
| `p11-instruction-like-text`, prompt sentence | `style.md` (3), `brief.md` (4), `critic.md` (4), `practice.md` (2) | One sentence in every stage that reads the source; a test checks every such system prompt. |
| Cross-job Veo resume | migration `0004_claim_operation` (`asset_claims.operation`), `storage/assets.py` (`record_claim_operation`, `inherited_operation`, `_stop_claim_quietly`), `providers/operations.py` | The takeover is atomic, and the operation is resumed only when the job pays the same way. An unconfirmed submission still needs attention and is never paid again. It covers graceful stops too: a holder cancelled, shut down or out of its lease leaves its claim expired with the outstanding record instead of deleting it. A taker whose own record is finished (e.g. failed after a 429) still resumes an inherited submitted operation. |
| Sweep root | `aadhi/scratch.py` (`SCRATCH_DIR`), `manim/sandbox.py`, `manim/render.py`, `manim/health.py`, `compose/workspace.py`, `tests/conftest.py` | Sweeps look only inside the scratch root; logs show it as `<scratch>`. Tests point `TEMP` / `TMP` / `TMPDIR`, `tempfile.tempdir` and `SCRATCH_DIR` at the session's own directory. |
| PDF worker | `ingest.run_pdf_worker` | After a timeout or a cancel, it kills the worker and waits (at most 10 s) before removing the temp dir. |
| Code-fence speed | `pipeline/codeblocks.py` | Lines are matched in batches with identical decisions (checked against the old code on 3,000 random documents). A cap of 400,000 checked body lines; `INGEST_VERSION` is unchanged. |

### Integration work

Requests between owners that the integration applied:

* The render preflight's `quality` list, shown under its own heading on the project page.
* `VersionSummary.review_stage`, read in one query for lists. The projects list, the project page and
  the job widgets link a source pause to the source report (`reviewLabel`), and the plan review
  forwards one there.
* `/api/meta` `limits.max_source_chars`, used by the paste box.
* In the editor: the timing summary under the preview, and the legend-row beat choice.
  `screenplayEdit` keeps a legend row's `beat_id` pointing at a beat of its scene.
* `formula.variable_beat_invalid` added to `AUTHOR_CODES`.
* Manim log scrubbing of the scratch root, and the health probe moved into the scratch root.
* Clearer Veo resume log lines.
* The linear rich-lite tokenizer.
* `studio.css` for the new panels.
* Test-forced defaults for the new settings.
* Regenerated `.env.example` and `web/schemas/*.schema.json`.

### Batch 2 review fixes

The review's confirmed findings, all fixed with regression tests:

* **Player and MP4**: `MASCOT_CUES` applied at serve time (`api/timelines.resolve_timeline`, in the ETag);
  no quiz bubble blink before the countdown; cue anchors clear of Aadhi's horns (right 1804,121 and
  centre 792,116); the clean intro logo.
* **Sync**: highlights named late or lit by the previous beat keep their beat-long timing; result words
  only for terminal output; no one-letter or Indic function-word anchors; no focus pulse on verbs.
* **Quality**: the three quadratic passes above; "Apply fix" ignores the outer whitespace the server
  strips; repairs survive a save and come back after "Reload theirs" or a job refresh; the first-use
  abbreviation repair skips headings set in capitals; the pre-export list includes generation and
  translation failures (`source: "system"`).
* **Source report**: outline matching on full heading paths (`REVIEW_VERSION` 2), set-aside prose
  filtered from the brief, no cut of `skipped_chunks`, the report's cited/skipped sets built once,
  no silent paste cut, stricter heading roles.
* **Carry-over**: graceful stops keep the outstanding Veo operation on the claim; a finished own record
  no longer hides an inherited submitted operation.

### Batch 2: deliberately not taken

* **Presenter**:
  * no AI avatar, custom presenter profiles or drawn SVG teacher (as agreed);
  * no presenter settings panel, `mascot_state` scene field, recording mode or cinematic suspend;
  * no per-state clips;
  * `presenter-safe-composition`, `presenter-speech-timeline-endpoint` and
    `presenter-review-decisions`: ours is better or equivalent.
* **Synchronisation**:
  * their AI alignment, camera moves and focus return, presenter acting, attention flow
    (`sync-director-plan`), end hold, emphasis arbitration and character-ratio narration clock;
  * v2 times everything from per-beat TTS word timings and one schedule.
* **Quality**:
  * heading levels, presenter consistency, timing and camera motion, and the style rules;
  * preview/export consistency (one Timeline already drives both), fingerprints (equivalent) and a
    separate quality endpoint (POST /lint returns the report).
* **Source**:
  * client-side structured extraction, source versioning and staleness, and traceable AI source
    analysis (ours is better or equivalent);
  * the lesson input handoff, the durable and stand-in lesson writers, and screenplay check and
    repair (the v2 pipeline already does these).

## Batch 3

Batch 3 follows the same rules. It adds two Studio pages and their backends: each teacher's **media
library** (phases 3, 4 and 5) and the **Visual Review** of a lecture's visuals (phases 4, 6, 9, 19 and
20). Default outputs are unchanged: no generated screenplay, scene hash, cache key, player frame or
MP4 changes until a teacher uses one of the new actions. The `variant` fields and
`prefer_library_visuals` are left out of the JSON at their defaults, and a golden test checks that
the image and clip keys at variant 0 are the earlier ones. Two new tables:
`0005_library_items` and `0006_visual_reviews`.

### Media library

| Feature id | Where it lives | Notes |
|---|---|---|
| `asset-library-browse` | `aadhi/library.py` (`list_items`, `item_view`), `GET /api/library` (`aadhi/api/routers/library.py`), `web/js/studio/views/library.js` (`#/library`, nav "Library") | Owner-only: admins see only their own library. Items whose file is gone are hidden. Search waits 300 ms, kind and source filters live in the URL, pages of 48 with "Load more". Cards never load videos (a placeholder until `poster_url` exists). Server strings are always set as text. |
| `asset-library-pick-reuse` | `library.attach_to_project`, `POST /api/library/{id}/attach`, `web/js/studio/components/libraryPicker.js` (`openLibraryPicker`), the editor's "Choose from library" (`mediaUpload.js`) | Attaching records the `asset_refs` row that authorises the key; the user's own lectures only (404 for admins too). No endpoint accepts a raw asset key. The picker can also upload a file, which is then chosen. |
| `asset-usage-references` | `library.used_in` (`used_in` in `ItemView`) | A count of the user's own live lectures the item was added to or made for (asset refs, which stay when a scene later shows other media), in one `GROUP BY`; the UI says "Added to N lectures". The list of lecture titles was skipped: the contract has a count. |
| `asset-delete-unused` | `DELETE /api/library/{id}`, the page's delete with a confirmation | Removes the library row only (the contract overrides the plan's "refuse while used"): lectures keep the media. The integration made the cleanup GC keep any asset that is in somebody's library (`library_items`), so a teacher's generated pictures are never collected under them. |
| `asset-description-keywords`, `asset-description-keywords-corrected` | `library_items.title` / `description` / `keywords` / `search_text`, `PATCH /api/library/{id}`, `POST /api/library` (multipart with optional words), the page's edit dialog | The words live per user in `library_items`; nothing is ever written to `Asset.meta` (two users with identical bytes keep separate words, tested). Strict validation of typed words (422), lenient tidying of system-filled ones. `search_text` holds words and stems, so search works in any script. After an upload each row has "Add details" instead of a dialog opening by itself. |
| `p4-asset-describe` | `library.describe` / `describe_image`, `prompts/library_describe.md`, `POST /api/library/{id}/describe`, "Suggest with AI" | Off by default (`LIBRARY_AI_DESCRIBE_ENABLED`; 403 `feature_disabled`). Fast model of the default engine with the user's resolved keys, a scaled picture or one ffmpeg frame of an MP4, 6 a minute, daily-budget pre-check (skipped on the user's own key), usage `purpose: library_describe`. A suggestion only: the teacher saves it with `PATCH`, and "Undo the suggestion" restores their words. `generation_meta["prompt_versions"]` of new lectures lists `library_describe` (metadata only, never a cache key). |
| `p4-library-match` | `library.match`, `auto_match`, `LibraryMatcher`, `visual_needs`, `suggestions`, `GET /api/library/suggestions`, `assets._plan_library` / `library_image` (`prefer_library_visuals`) | Deterministic word overlap (stop words, generic picture words, negation, a light stemmer, any script); bounded to 600 recent + 400 overlapping items per run (two queries, an inverted index; 10,000 items in under a second), item profiles cached by their words, at most 20,000 item × scene scores per request (`MATCH_MAX_PAIRS`). Suggestions: top 3 at ≥ 0.35, never the media the scene shows. Automatic use in the build (`auto_match`): the image prompt only (never titles), ≥ 0.6 (`AUTO_USE_THRESHOLD`) and ≥ half the prompt covered (`AUTO_USE_MIN_COVERAGE`); one plan and one matcher per build, a picture per scene; never media generated for this lecture or the scene's own picture; side-panel images only, never for a requested new version, only in a build the lecture's owner started. |
| `ai-cache-stats` | `library.media_cache_stats`, `GET /api/admin/media-cache?days=` | Admin-only aggregates: generated, provider calls, cost, stored, reuse by another lecture, library sizes by source. No prompts or users. Reuse within one lecture is not recorded anywhere, so it is not counted. |
| uploads join the library | `aadhi/api/routers/uploads.py` (`_add_to_library`) | Source `upload`, titled from the file name; never fails an upload; the response is unchanged. `/api/library` is one of the body-limit upload routes (integration fix: it was capped at `MAX_JSON_BODY_MB`). |
| generated media join the library | `assets._save_generated_to_library` → `library.record_generated` | `LIBRARY_AUTO_SAVE_GENERATED` (on). Into the library of the user who started the build when they own the lecture (an admin's build of someone else's lecture saves nothing: libraries never mix). Cheap, idempotent, never fails the build (a savepoint on PostgreSQL). Uploads and figures are never recorded as generated. |

### Visual Review

| Feature id | Where it lives | Notes |
|---|---|---|
| `p6-review-panel` | `aadhi/review.py` (`visual_slot`, `scene_view`, `summary`), `GET /api/versions/{vid}/visual-review`, `web/js/studio/views/visualReview.js` (`#/v/:vid/review`) | Built from the stored screenplay, manifest and issues: opening it never builds or generates. Signed / public media URLs only, never asset keys in the UI. One card per scene with a visual (or a removed one), filters All / Needs attention / Not approved in `?filter=`, `?scene=` focus, a "Build changed scenes" banner and the running job. The response also carries `revision` (integration), which the page sends with its actions. |
| `p6-approval-state` | `VisualReview` rows (`visual_reviews`), `review.fingerprint` / `review_view` / `set_review`, `PUT .../visual-review/{scene_id}` | The fingerprint covers the visual request (prompt, chosen asset, variant, figure and its image, Manim spec, chart data, p5 code, poster), never narration, titles or rationale. An approval of an earlier request reads as pending and stale; `changed` / `removed` are kept and flagged. Outside `scene_hash`, so approving never changes a build. `POST .../duplicate` copies the sign-offs (integration). Review notes are not offered in the UI (the API sets a note only together with approved or pending, which would reset a `changed` sign-off). |
| `p6-new-ai-version`, `force-regenerate-new-version`, `force-regenerate-new-version-corrected` | `SidePanel.variant` / `AIVideoScene.variant` (0–99), `assets._image_keys` / `_video_keys`, `review.new_version_scene`, `POST .../scenes/{scene_id}/visual` `new_version`, `aadhi/providers/image/pollinations.py` (`variant` seed) | `"variant"` joins the content key only above 0, so every existing key stays; the earlier asset stays cached. Saved with the revision check, then a `build_assets` job for that scene only, with the budget pre-check and the hourly generation limit; the UI asks before billing. Pollinations derives its seed from the prompt, so a new version changes the seed (unchanged at 0). A rewrite of the scene with an unchanged prompt keeps the variant (`repair.carry_over` → `assets.carry_visual_variant`, integration). |
| `p6-choose-from-library`, `p19-visual-choose-remove` | `review.chosen_scene` / `check_pick` / `removed_scene`, `choose_library` / `remove` actions, the page's "Choose from library" / "Upload replacement" / "Remove", the editor's media controls | Only the requester's own item (404 otherwise, nothing attached) and only in the requester's own lecture (403 for an admin on someone else's; integration, matching `attach`). An animation takes a video, an interactive poster a picture (422 otherwise). Removing a main-media or poster override brings the scene's own visual back (`changed`); a scene's own clip or animation cannot be removed. "Upload replacement" is `POST /api/library` then `choose_library`. |
| `p6-remove-visual`, `p6-unsaved-guard` | (equivalent) | The editor already removes panels and overrides; the review page never touches the editor's local draft and says when one exists. |
| `p6-review-rule-first` | `assets.OVERRIDE_MISSING_WARNINGS` → issue `assets.override_missing`, review status `missing` / `fallback` | A teacher's upload or library pick that is gone is never replaced silently; the generated visual still shows. |
| `p4-source-badges`, `generation-provenance` | `SceneVisual.source` / `visual_source` / `provider` / `model` (from `Asset.meta`), the review cards, `web/js/studio/views/editor/visualInfo.js` ("Visual at the last build" in the inspector) | `MediaInfo` is unchanged. No badges in the editor's scene list. |
| `p4-visual-intent` | `library.visual_needs` | No schema field: the library's visual-need text comes from validated screenplay fields, so suggestions and automatic use match on the same words. |
| `p9-needs-attention-ui`, `p20-media-attention-actions` | status `ambiguous` for `video.ambiguous_submission` / `video.operation_lost`, `confirm_paid` (409 `confirm_paid_required`), job payload `confirm_paid_scene_ids` (`assets.CONFIRM_PAID_KEY`), the editor's issues panel ("Rebuild this scene", "Review visual", "Generate again…"), the project page's "N visuals need attention" | A possibly billed AI video is submitted again only for the confirmed scene, and only once: the consent covers an earlier job's submission, not one the confirmed job makes itself, and a job retry drops it (`jobs.queue.ONE_JOB_PAYLOAD_KEYS`; integration). "Dismiss" was skipped (no endpoint); the "next step" panel does not exist in v2. |
| `job-run-views-attention` | per-scene attention in the review | The plain-language `explanation` in `job_summary` was skipped (open item). |
| `prefer_library_visuals` | `GenerationOptions.prefer_library_visuals`, `assets.library_image`, the options form's "Prefer pictures from my library" (integration; only when `/api/meta` has `library`) | Left out of stored options while false (checkpoint digests unchanged); `complete_options` serves every field to the Studio (project options, `/api/meta` defaults). Info issue `assets.library_visual_used`. |

### Batch 3 review fixes

A review of the integrated batch found these, all fixed with tests:

* **Bounded work and limits.** `library.suggestions` scores at most `MATCH_MAX_PAIRS` (20,000) item ×
  scene pairs per request (`rank(max_candidates=)`, the most recent candidates first), prepares each
  request once (`_score`), caches item profiles by their words, and ranks one scene for a write's
  answer (`scene_ids`, same counts as the review). Visual Review writes have their own 60-a-minute
  per-user limit.
* **Libraries are personal in builds too.** `prefer_library_visuals` and auto-save only when the job's
  user owns the lecture; an admin retry / new version of a teacher's lecture never uses or fills a
  library. One matcher per build.
* **Automatic use is precise.** Matched on the image prompt only with enough of it covered; a picture
  serves one scene; media generated for this lecture and the scene's own picture never stand in, so an
  edited prompt makes a new picture.
* **New AI versions** are offered and accepted only when that medium can be generated for the lecture
  with the requester's keys (`review.generation_available`); a build that still cannot make one keeps
  the latest earlier version (`earlier_image_version` / `earlier_video_version`), shown as `fallback`.
* **Approvals** may carry the revision the teacher saw (409 `revision_conflict` otherwise); the page
  sends it.
* **One-scene builds** that reuse other changed scenes no longer set `built_revision`, so the version
  stays `timeline_stale` and cannot be rendered with an unbuilt library pick.
* **Review view:** a chosen visual shows before its build, a removed one does not; the scene's own
  picture is never its library match; settled fallbacks (the AI video limit, generation off) offer no
  retry (and refuse one) and say to choose or upload; `accepts` per scene; suggestions for an
  unreadable screenplay are empty, not a 500.
* **Studio:** "Approved before a change" for the server's pending + stale shape; the job box's Retry
  is tracked (no polling after leaving); "N matches" opens the picker with the matches first
  ("Best matches"); the picker announces a count, not the grid; several `sources` are filtered by the
  server (`source=upload,figure`); replacement uploads show progress, guard leaving and stop with the
  page; the editor offers the library only in the user's own lecture; a refused upload says the file
  stayed in the library with the server's plain reason; "Added to N lectures" wording.

### Integration work

* Connections: the review uses the library service for picks, suggestions and automatic use; both
  suggestion paths now share `library.suggestions` defaults and `library.AUTO_USE_THRESHOLD`; the
  editor and the review page use the Library's picker; the migration chain is 0004 → 0005 → 0006
  (upgrade, downgrade and `alembic check` tested).
* Applied requests: `review.copy_reviews` in `duplicate_version` (idempotent); the variant carried
  through rewrites in `repair.carry_over`; `/api/meta` defaults through `complete_options`; the
  cleanup GC treats `library_items` as references; `/api/library` in the body-limit upload routes;
  library picks only in the requester's own lectures (also hidden from the review's `actions`);
  `revision` in the review response; the paid-retry consent scoped to one job and one earlier
  submission; the "Prefer pictures from my library" option; `mime` passed on
  from a picked item; `variant` in the JSDoc `SidePanel`; test-forced defaults for the two new
  settings; the documented routes in `router.test.js`; a slightly narrower nav below 1280 px, so a
  teacher's four links fit on one line at 1100 px (admins can still wrap, as before).
* Regenerated `.env.example` and `web/schemas/*.schema.json` (the `variant` fields).

### Batch 3: deliberately not taken

* Their asset library framework (UUID asset rows with `scope_key` / `owner_id`, system assets, a
  second storage layer): v2 keeps the content-addressed `AssetStore` and adds only per-user rows.
* Ownership or words on shared asset rows, a raw asset key in any library request, and admin access
  to other users' libraries.
* Lecture titles in `used_in`, refusing to delete a used item, extending the GC to uploads, video
  posters for library items and review cards (`poster_url` is null), review notes in the UI,
  "Dismiss" of attention items, badges in the editor's scene list, and `job_summary` explanations.

## Batch 4

Batch 4 follows the same rules. It adds the editor workflow of phase 19 (skipping and holding scenes,
splitting, the timeline strip, saving, undo, "Generated vs edited"), the guided Studio of phases 20 and 21
(lesson stage and next step, the Videos page, the example lecture, polish), and imports of lessons saved by
the fork. No table and no migration: the two new scene fields live in the screenplay JSON and everything
else is derived from existing rows. One new private asset kind (`snapshot`), no new settings.

Default outputs are unchanged. `hidden` and `min_seconds` are left out of the JSON at their defaults and
ignored by the asset hash, and `TimedScene.hold_seconds` is left out when 0, so a lecture that uses neither
has the same screenplay JSON, scene hashes, cache keys, timeline, player frames and MP4 (tested, including a
slow MP4 render). Three changes are deliberate:

* The source report counts formulas written inside a sentence ("V = I × R"; `REVIEW_VERSION` 3), so a
  source whose inline formula has unexplained symbols can now read as `missing_context`
  (`tests/api/test_source_report.py` was updated for that).
* The Studio shows render and job numbers, revisions and raw job stage names only with `?debug`.
* A running job that writes the version now locks the editor (before, editing went on and the save waited).

### Editor: skipping, holding and splitting scenes

| Feature id | Where it lives | Notes |
|---|---|---|
| `p19-hide-scene` | `SceneBase.hidden` (`schemas/screenplay.py`), `compose/timeline.py` (`build_timeline`, `preview_timeline`), `pipeline/assets.py`, `pipeline/validate.py`, `compose/video.py` (`empty`), `api/routers/renders.py` (409 `all_scenes_hidden`), the inspector's "Skip this scene in the video" (`views/editor/sceneTiming.js`), `sceneList.js`, `timelineStrip.js` | Both renderers skip the scene: the remaining scenes stay contiguous and `TimedScene.index` counts only shown scenes, so captions, chapters and keys leave it out alike. Never built, even when a rebuild names it (no paid work); media built before stays and it is never stale while hidden. A regenerated hidden scene stays hidden. Lint: `lecture.all_scenes_hidden` (warning), `chapter.all_scenes_hidden` and `scene.hidden` (info); a hidden scene's own findings, of every source, become notes (out of the pre-render list, never a paid rewrite or a rebuild; stored issues keep their severity and are served as notes); coverage, length, pacing and abbreviation checks count shown scenes. Hidden AI-video scenes keep their place in the per-lecture AI video limit. The editor will not skip the last scene that plays. Integration: the Visual Review, analytics and render additions below. |
| `p19-min-duration-hold` | `SceneBase.min_seconds` (1–600), `TimedScene.hold_seconds`, `web/js/player/schedule.js`, `panels/util.js`, `panels/timing.js`, the inspector's "Show for at least" | The extra time is added after the content: beats, captions, sync cues and audio keep their timing, terminal lines and the quiz teaser keep the timing they have without the hold, Aadhi idles. The fork accepted values below 1 s; ours does not (imports clamp). `lecture.duration_mismatch` counts the hold, `scene.too_long` deliberately does not. |
| `p19-split-scene` | `web/js/studio/views/editor/splitScene.js` ("Split here" from the second beat) | A pure screenplay transform: a new scene id, the beats after the cut and the board items they reveal or fill move (references repaired; companion-sheet entries and legend-row beats follow), the visual and the hold stay with the first part. Quizzes, chapter cards and animations (one step per beat) cannot be split; the second part of footage or a sketch scene becomes a content scene. In "Generated vs edited" the second part is `added`. |
| `p19-insert-duplicate-scenes` | the editor's add menu, "Picture from my library…" | A content scene showing the picture as a lecture figure (uploads and document figures only, which a figure accepts; own lectures only). Duplicating a scene already existed. |
| `p19-timeline-tracks` | `views/editor/timelineStrip.js` | Built from the same preview Timeline as the embedded player: scene, narration (estimated beats hatched), visual and caption lanes, chapter marks, playhead, zoom (remembered), seek by click or drag, drag a block more than 5 px to move a scene; keyboard slider; skipped scenes as thin marks; falls back to the scene list's estimates without a preview. |
| `p19-playhead-follow-still-preview` | `previewPane.js` (`seekSceneId`, scene id with each change), `editor.js` (`shouldFollow`) | Everything maps by scene id (positions differ once scenes are skipped). The selection follows playback only while playing, never while typing, and only 3 s after the last edit; a paused selection shows a still. |

### Editor: saving, undo and changes

| Feature id | Where it lives | Notes |
|---|---|---|
| `p19-save-state-retry` | `editor.js` | Saved / saving / unsaved / couldn't save (Retry) / changed elsewhere (Review) / waiting for a job, always in words with an icon; the live region speaks only on a change. |
| `p19-save-as-copy` | `editor.js` (`POST .../duplicate`, then `PUT /screenplay` on the copy) | The copy opens with the unsaved edits; the original stays as last saved. If the copy's save fails, the edits become the copy's local draft. |
| `p19-server-autosave` | `editor.js` ("Save automatically") | Off by default, per user, in this browser only (`aadhi.studio.pref.editorAutosave.u<id>`); 3 s after the last change; never while the draft is invalid, a save conflicts, a job runs, after a 422 (until the next edit) or while a dialog is open. |
| `p19-undo-labels-commands` | `views/editor/changes.js` (`editLabel`, `HISTORY_LIMIT` 200) | "Undo: Edit narration in scene 3" in the tooltip and the screen-reader description; the buttons keep their names. |
| `p19-editor-shortcuts-help` | `components/shortcutsDialog.js`, `editor.js` | `?`, Space, `[` / `]`, Delete (asks first); never while typing, in a dialog or in the preview. |
| `p19-inspector-sections` | `views/editor/inspectorFold.js` | Every section after the first folds; jumping to an issue unfolds its section. |
| `p19-generation-lock-status-poll` | `editor.js` (`checkActiveJob`, `JOB_POLL_MS` 15 s) | A queued, running or paused job that writes the version (the server's `VERSION_MUTATING_KINDS`) makes the scene list and inspector inert; polled while the tab is visible, on return and after a `version_busy` save; its result is merged with local edits. |
| `p19-conflict-replay` | `views/editor/sceneMerge.js` | Save conflicts, job results and restored drafts merge field by field (beats and board items by id) instead of reporting a conflict when both sides changed one scene. `lib/conflict.js` is unchanged. |
| `p19-generated-vs-edited-revert`, `p19-moved-origin-markers` | `aadhi/changes.py`, `api/routers/changes.py`, `pipeline/orchestrator.py` (snapshot), `views/editor/changes.js`, `changesPanel.js`, `inspector.js` | `generate_lecture` and `translate` store the generated screenplay as a private `snapshot` blob in the same compare-and-set as the screenplay (`generation_meta["generated_snapshot_key"]`; regeneration, saves and builds never touch it; the GC never collects it; `duplicate` copies the key). `GET .../changes` gives unchanged / edited (top-level fields) / added / moved (off the longest run in generated order) / removed; `GET .../changes/{scene_id}` both documents and the history; `POST .../scenes/{scene_id}/revert` the whole scene as generated or from `scene_history` (newest first), a removed one at a position, saved like `PUT /screenplay`. Editor: Edited / New / Moved badges, the side-pane list with Restore, "Compare and revert…" (confirmed, undoable). The revert is whole-scene: the editor does not show the generated text field by field yet. |

### Guided Studio and videos

| Feature id | Where it lives | Notes |
|---|---|---|
| `p20-derived-lesson-stage`, `p20-guided-workflow-next-step`, `p21-next-step-guidance` | `aadhi/stage.py`, `api/serializers.py` (`stage`, `next_step`, `latest_render` on ProjectSummary; `stage`, `next_step` on the version detail), `web/js/studio/lib/lessonStage.js`, `components/stage.js`, `projectDetail.js`, `projects.js` | Derived purely from stored state; the project list stays batched (two extra render queries however many projects). The "Next step" card: the stage in words, one sentence, one primary action and the Source · Plan · Script · Voice and visuals · Video strip; build, make the video (checks for silent scenes first) and retry run on the page; unsaved editor changes in this browser turn build / render into "Open the editor to save your changes"; only `#/…` and same-site links are followed. Older servers: derived from the summary. |
| `p20-render-matches-current` | `stage.matches_current`, `render_summary(..., version)`, `GET /api/versions/{vid}/renders`, the project page | "Up to date" / "Out of date" on finished renders. |
| `p20-review-checkpoints` | `serializers.review_checkpoints` (version detail `checkpoints`) | Source and plan: waiting / approved / not requested (from the approved review-pause jobs; `source.corrected`); visuals: the Visual Review sign-off counts, hidden scenes left out. The optional `review_marks` column and a mark-reviewed endpoint were not needed. |
| `video-history-library`, `in-browser-video-preview`, `p21-videos-panel-polish` | `api/routers/videos.py` (`GET /api/videos`), `web/js/studio/views/videos.js` (`#/videos`, nav "Videos"), `components/videoPreview.js` | The user's own projects only (admins too), deleted projects hidden; `preview_url` a streamable media URL for succeeded renders only. Filters All / Up to date / Out of date (`?filter=`, on the loaded pages), "Load more", a 10 s refresh while a video is being made (focus kept), playback in a dialog with `<video controls preload=metadata>` (source released on close), download first, a failed render says why with "Try again". |
| `p21-example-lesson` | `web/examples/ohms-law.json`, `newProject.js` ("Try an example"), `tests/api/test_example_lesson.py` | Imported through `POST /api/projects/import` as "Example: Ohm's law": 5 scenes with a graph, a worked example, a quiz and a summary; no AI writing, pictures or video; lint-clean; only the voice-over is built. |
| `p21-create-paste-notes` | (batch 2) | No change. |

### Studio polish

| Feature id | Where it lives | Notes |
|---|---|---|
| `p21-token-contrast-tests` | `web/css/tokens.css` (`--c-text-subtle` ≥ 6.1:1, `--c-accent-text` ≥ 6.5:1), `studio.css`, `web/tests/ui_tokens/contrast.test.js` | `--c-text-faint` (about 4.4:1) is used by the renderer, so the Studio's faint text moved to the new tokens instead. The light-theme check is skipped: `tokens.css` defines only a dark theme. |
| `p21-type-floor` | `--fs-min` (0.75rem), `studio.css` (`max()` floors for `em` sizes), `web/tests/ui_tokens/typeFloor.test.js` | Uppercase labels stay (the uppercase buttons come from `base.css`, which the renderer loads). |
| `p21-plain-words-debug` | `lib/debug.js` (`showTechnical`), project page, Videos, job widgets | Render / job numbers, revisions (the versions column is now "Build": Up to date / Needs build / Not built yet) and raw stage names only with `?debug`; stages in words ("Voice & visuals"). |
| `p21-poll-visibility` | `lib/visibility.js` (`pageHidden`, `onceVisible`), the projects poll, `jobProgress.js`, the Videos refresh, the editor's job poll | No requests while the tab is hidden; one poll on return; waiting stops with the page. |
| `p21-display-name-dedupe` | `lib/names.js` | Repeated name parts once; lectures sharing a title get a session, unit, subject, language or date suffix, else "(1)", "(2)". The intro title cards (`build_intro`) are unchanged, because changing them changes the MP4. |

### Importing lessons from the fork

| Feature id | Where it lives | Notes |
|---|---|---|
| `p20-legacy-import-studio-lessons`, `friend-db-lesson-import` | `aadhi/legacy/convert.py`, `legacy/legacy_db.py` (`latest_rows`, `latest_only`), `cli.py` (`import-legacy --latest-only`, integration), `POST /api/projects/import` | Their lessons are v1 lessons with additions. `edit.hidden` becomes `hidden` (the gap plan said "skip"; the contract keeps the scene, hidden), `edit.min_seconds` becomes `min_seconds` clamped to 1–600 s; muted narration, captions off, their media asset ids, `asset:` images and their plan / style / Studio / editor keys are import warnings and never followed. A fork lesson converts exactly like the same lesson saved by plain v1 apart from those two fields (tested); their extra tables are ignored (tested). The fork saves every save as a row, so `--latest-only` imports the newest row per lesson (owner + subject, unit, session, title, without case or outer spaces; a row with none of them is its own lesson) and lists the others in `projects_superseded`. |
| `data-model-mapping` | (no new tables) | Their video exports map to `Render` and `/api/videos`; their stored originals to the snapshot blob; their Studio checkpoints to jobs and Visual Review rows; their lesson JSON to our screenplay through the converter. |

### Leftovers from earlier batches

| Item | Where it lives | Notes |
|---|---|---|
| `p11-content-inventory-formula-check` (inline formulas) | `source_review.inline_formulas`, `REVIEW_VERSION` 3 | An explicit operator or exponent and at least one short symbol ("V = I x R", "I = V / R = 12 / 4 = 3"); "a = b", "x = 5", "1 + 1 = 2", "Total = 5 + 3", inline code, URLs, tables and code lines never count. Implicit products are not counted, with or without an exponent ("F = ma", "E = mc²", "A = πr²"), nor quantities with units written after a number ("1 km/h = 5/18 m/s", "1 kWh = 3.6 × 10⁶ J"). The number pattern has a single parse, so a long digit run or sum chain is scanned in linear time. |
| Pre-render list order (batch 2) | `compose/preflight.quality_items` | Within a severity, lecture-wide and scene findings alternate, so scene findings are not all pushed into "N more". |
| Sync lint anchor (batch 2) | `pipeline/sync_lint.py` | Uses the first beat that reveals a formula, like the timeline's sync cues. |
| Lint endpoint (batch 2) | `POST /lint`, `quality.analysis_of` | The quality analysis runs once for the issues and the report. |

### Integration work

* Connections: the editor's revert dialog sends `history_index` in the server's order (`scene_history` is
  newest first, so "before the last regeneration" is 0; the editor had assumed oldest first; tested with two
  regenerations); the hidden-scene lint codes are grouped under "Coverage and length" in the issues panel;
  `latest_render` and `SceneVisual.hidden` in the Studio's JSDoc types; regenerated `web/schemas/*.schema.json`
  (`hidden`, `min_seconds`, `hold_seconds`); `.env.example` unchanged (no new settings).
* Applied requests:
  * Visual Review: a hidden scene is listed with `hidden: true` and a "Skipped in the video" chip, keeps
    approve / choose / upload / remove, gets no `new_version`, `retry` or `confirm_paid_retry` (409
    `action_unavailable` before anything is charged, since the build skips it) and never counts as needing
    attention (server summary and page);
  * `POST .../render` refuses a lecture whose every scene is hidden up front (409 `all_scenes_hidden`)
    instead of a job failing with `empty`;
  * `GET /api/versions/{vid}/renders` carries `preview_url` (the project page's "Watch"), served by the same
    `api/timelines.media_url` as `GET /api/videos`;
  * analytics rows of hidden scenes carry `hidden: true` and the scene table says "(skipped in the video)";
  * `snapshot` added to `models.PRIVATE_ASSET_KINDS` (documentation; the namespace comes from
    `PUBLIC_ASSET_KINDS`, the GC list is unchanged);
  * `import-legacy --latest-only`;
  * the project page gives no link to itself for the `open` step of a project without a usable version.
* Checked and already consistent: the editor maps the preview by scene id; the editor's job lock uses the
  server's mutating kinds and active statuses; the revert body (`extra="forbid"`) matches what the editor
  sends; `/api/videos` accepts the Videos page's `limit=100`; the example lecture is served from `/web/` and
  copied into the Docker image; the editor reloads the version after a revert.

### Batch 4: review fixes

* Hidden scenes:
  * every issue of a hidden scene (lint, reviewer, system, asset build) is served and counted as a note
    (`validate.hidden_as_notes`: `PUT /screenplay`, revert, the version detail, the orchestrator's `issue_counts`)
    and stored at its real severity, so showing the scene brings it back; `quality_items` leaves them out of the
    pre-render list; the editor treats an unsaved skip the same way and its issues panel offers no Regenerate,
    Rebuild or Generate again for a skipped scene (only "Review visual"); `POST /build` naming only hidden scenes
    is 409 `action_unavailable` (a regeneration of a hidden scene is still allowed: it rewrites the text the
    teacher may show again);
  * the pacing run of dense boards and the abbreviation first-use / spelled-out checks skip hidden scenes
    (labels keep the editor's numbering); `board.reading_time` counts `min_seconds`;
  * preflight items, render warnings and the job log carry `scene_number` (the place in the screenplay, as the
    editor numbers scenes; `scene_index` stays the timeline place); the editor's render dialog, the project page
    and the preview's "Timing of scene N" use it.
* `POST /lint` with a failing quality analysis runs it once (`quality.COMPUTE` sentinel).
* Revert in place puts a scene back in its chapter's list when its `chapter_id` differs (one chapter lists it).
* Editor: a job result that clashes with unsaved edits holds automatic saving (`merged`) until a save by hand and
  says so in a dialog; adopting a job's result is its own labelled undo step ("Changes from the job", then
  "Merge with the job's changes"); jobs the editor starts lock it at once; Space leaves form controls and media to
  themselves; edits typed while a revert is out are merged on top of it; "Save as a copy" never saves the original
  meanwhile; a failed automatic save waits for the next change or Retry; pending inspector edits reach the draft
  before the job lock; the timeline's keyboard slider moves through the estimates without a preview.
* Studio: the Videos page leaves the list alone when only presigned links changed (Watch reads the newest link),
  keeps videos loaded past its 100-item re-read, uses the project list's title suffixes (`/api/videos` items carry
  the lecture's fields) and gives focus back to a redrawn Watch; the preview asks once for a fresh link before it
  says the video could not be loaded; a failed lesson shows no workflow strip unless the video of a built lecture
  failed (then the earlier steps are done).
* Source report: no implicit products with an exponent or quantities with units as inline formulas, and no
  backtracking on long digit runs (same results otherwise: 0 differences over 84,307 lines of this repo).

### Batch 4: deliberately not taken

* `p19-scene-captions-toggle`: dropping one scene's captions from the subtitle files hurts accessibility
  (WCAG 1.2.2) and needs a schema field outside the contract; the viewer's CC toggle and burn-in cover the
  real needs.
* Timeline strip: transition markers (our Timeline has no per-scene transitions), a presenter lane and
  snapping. The client-side "moved" calculation (the server computes it). An Esc cascade (dialogs already
  close on Esc).
* Field-by-field revert in the editor (`GET .../changes/{scene_id}` already returns both documents).
* The `review_marks` column and mark-reviewed endpoint; first-frame thumbnails in the video lists (cards never
  load videos, like the Library); a server-side filter for `/api/videos`; the optional `build_intro` title
  dedupe (MP4 change); removing uppercase from `base.css`; raising `--c-text-faint`.
* Skipping hidden scenes on import (they come in hidden), and translating only shown scenes (a hidden scene's
  text is translated too, because it is needed if the scene is shown again).

## Open items

* **Batch 3**:
  * `poster_url` for library videos and review cards (a frame at upload or build time); the grid and
    the cards show a placeholder until then;
  * the plain-language `explanation` of attention items in `job_summary` (`aadhi/jobs/queue.py`);
  * "Choose from library" for a lecture figure's `asset_key` in the board editor (pass
    `sources: ['upload', 'figure']` to the picker);
  * apply migrations `0005_library_items` and `0006_visual_reviews` before release.
* **Evals**: re-run the baselines with real model keys. Since the last run:
  * four prompts changed (style 3, brief 4, critic 4, practice 2);
  * lint counts changed (quality notes, presenter and sync warnings);
  * chunk boundaries changed for code-heavy and setext sources (`INGEST_VERSION` 8, batch 1).

  The `grounding.lexical_support_pct` metric (English only) is optional.
* **Prompts, optional**: ask the scene writer for `[[keyword]]` on each item's distinctive word, and
  for the legend meanings in the narration. More cues would then land on the spoken word. This needs
  a `PROMPT_VERSION` bump and an eval run.
* **Mascot, optional**: Aadhi reacting to `focus` sync cues (the same cue, so preview and MP4 stay
  equal).
* **Board fit at render time**: return `fitScale` / `overflowed` from `aadhiRender.show()` and record
  `board.overflow` for the MP4.
* **Media checks**: an aspect-ratio notice for media overrides (`assets.py` / uploads).
* **Source review**: a dedicated skip reason for chunks the teacher set aside, instead of `off_topic`.
  This needs `pipeline/base.py`, preview, report and eval labels.
* **Test fakes**: `tests/pipeline/fakes.py` `fast_audio` cannot decode its fake MP3, so fast tests
  build no envelopes and log one harmless warning per scene.
* **Generation provenance in the editor**: add optional `MediaInfo.provider` / `model` and a
  "Made by X (backup for Y)" line in the inspector.
* **A checkpoint for `regenerate_scene`.**
* **Veo resume, optional**: `_checkpointed_video` still marks a record `abandoned` when a `JobCancelled`
  (a lease lost while a checkpoint is saved) escapes the provider call, so that rare stop does not keep
  the operation for the next job. Re-raising it untouched would also leave a `submitting` record when the
  lease is lost before the request is sent, which makes the next job ask for a person needlessly.
* **Batch 4**:
  * the editor could show the generated text field by field from `GET .../changes/{scene_id}` before a
    revert;
  * the lesson stage says "Make the video" for a lecture whose every scene is hidden (the render is then
    refused with 409 `all_scenes_hidden`);
  * `translate` still translates hidden scenes (a paid step); their issues are served as notes (review fixes);
  * the companion sheet (`companion.md` / `.html`) and `export.json` still include hidden scenes: decide
    whether the printable sheet should follow the video;
  * a light theme for the contrast test, and a server filter for `/api/videos` (the page filters the loaded
    pages).
* **Before release**:
  * check the Pollinations 402 behaviour and that the `VEO_MODEL` default (`veo-2.0-generate-001`) is
    available; neither could be checked offline;
  * apply migration `0004_claim_operation`;
  * delete by hand any `aadhi-render-*`, `aadhi-manim-*`, `aadhi-m-*` and `aadhi-d-*` folders that
    older versions left in the system temp dir.
