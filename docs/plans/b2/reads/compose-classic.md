# compose-classic: aadhi/compose/*.py top level (base, timeline, presentation, captions, sync, chapters, preflight, sounds, loudness, ffmpeg, video, screenshot, qa, frames, capabilities, workspace, aio, __init__)


## summary
This slice is the shared compose layer plus the whole classic (states) MP4 renderer. `timeline.build_timeline` (timeline.py:805) and `preview_timeline` (:824) both call `_assemble` (:717). That function is pure and deterministic: no I/O, no clock, and seeds come from sha256. It turns Screenplay + AssetManifest into a fully timed `Timeline` that stores asset keys only. `resolve_urls` (:867) fills the URLs at serve time from `meta["asset_storage_keys"]` plus a single `AssetStore.get_many` call.

Inside a scene, narration starts at SCENE_LEAD 0.5 s, the scene ends after SCENE_TAIL 0.8 s, quizzes add 1.0 s, and silent scenes last 4.0 s (base.py:75-81). Each beat is laid out from the narration audio (`_plan_from_audio` :274). When the audio is missing or stale, `_plan_estimated` (:298) is used instead at 0.065 s/char, reusing beats that still match and adding a fallback entry to meta. Missing media degrade to text boards (:503-524). Hidden scenes are dropped (:737), and `min_seconds` adds `hold_seconds` (:602).

Captions come from `captions.split_caption` (captions.py:208): at most 2 lines of 42 chars, timed from words, and gaps under 0.5 s are closed. Word-anchored `sync_cues` come from `sync.scene_sync_cues` (sync.py:508).

Dynamic (B1) enters only in `_assemble`. When `screenplay.presentation` is set and `DYNAMIC_ENABLED` is on (timeline.py:731), the builder:
- drops the intro (:734);
- uses 30 fps (`DYNAMIC_FPS`, :787);
- uses 16-char portrait phrase captions from `presentation.portrait_caption_cues` (:546-552);
- adds `TimedScene.look` from `presentation.resolve_looks` (:779-780);
- adds `Timeline.presentation` from `timeline_presentation` (:781).

The stage size follows `presentation.format` whether or not the flag is on (:776).

`presentation.py` holds a hard-coded B1 stand-in for B2: 4 OKLCH families, content tokens, accent rotation, background tone steps, and cut/crossfade selection.

`video.render_video` (video.py:976) is the job handler. It rebuilds the timeline with the current settings and then dispatches: a timeline with `presentation.style == "dynamic"` goes to `motion.job.render_dynamic` (:999-1003), anything else to the classic `_render` (:751). The classic path:
1. Playwright screenshots every render-mode state (screenshot.py).
2. Encodes one exact-frame-count segment per scene through pure ffmpeg filtergraph builders (ffmpeg.py): mascot clip / media rects / one concat stream of state PNGs with premultiplied crossfade PNGs (frames.py) / PCM audio with ticks and ding (sounds.py).
3. Caches segments in a durable workspace (workspace.py).
4. Concat-copies, adds BGM ducked by sidechaincompress, chapters, and optional burn-in or a mov_text track.
5. Verifies with qa.py and stores the files as content-addressed assets.

`preflight.py` lists degradations and quality issues before a render. `loudness.py` (B0) holds EBU R128 helpers that only `compose/dynamic/container.py` uses so far; the loudnorm and limiter helpers are used only by tests and are waiting for B3.

The motion engine reuses several pieces of this slice: CHROMIUM_ARGS / NetworkPolicy, tick_wav / ding_wav, TICK_VOLUME / DING_VOLUME, branding_file, bgm_filter, the qa helpers, RenderWorkspace, and _finish_render / render_warnings.

## contracts
**Timeline builder (INTERFACES.md:328-371)**
- `build_timeline(screenplay, manifest, *, settings, version_id=None, revision=None, include_intro=True, branding=None) -> Timeline` (timeline.py:805) must stay pure. `api/timelines.current_timeline` (api/timelines.py:110-138) rebuilds at serve time whenever `DYNAMIC_ENABLED` differs from the stored build.
- `preview_timeline(...)` (:824) always returns `estimated=True`.
- `resolve_urls(timeline, store, *, signed=False)` (:867).
- `Timeline.meta` keys: `subject_name`, `unit_name`, `session_number`, `session_title`, `asset_storage_keys` (removed by `resolve_urls`) and `fallbacks` (:766-774).
- Fallback reasons: `simulation_without_media`, `ai_video_without_media` (:586, :590), `no_audio`, `audio_mismatch` (:753-754). `preflight.MESSAGES` (preflight.py:31) and `BLOCKING_REASONS` (:28) depend on these strings, and so does the API's 409 `render_preflight`.

**Time base (also enforced by `Timeline._time_base`, schemas/timeline.py:427-452)**
- Scenes are contiguous from `intro.duration`.
- Beat, caption and quiz times are scene-relative. Narration starts at `audio_offset` = 0.5.
- Constants: base.py:75-81, `CAPTION_HOLD_SECONDS` 0.4 (timeline.py:107), `INTER_BEAT_GAP_SECONDS` 0.15 (schemas/manifest.py:28).
- All stored times are rounded to ms with `_r` (timeline.py:189).

**Dynamic timeline invariants**
- Built only when `presentation` is set and `dynamic_enabled(settings)` is true (timeline.py:731, presentation.py:323).
- `intro` is None and `fps` is 30 (timeline.py:734, :787). Width x height come from `stage_size(format)` (:776); the schema validator requires portrait exactly when height > width (schemas/timeline.py:443-448).
- `TimedScene.look` / `Timeline.presentation` are left out of the JSON at their defaults, so classic JSON is byte-identical (tests/compose/test_dynamic_presentation.py:70-76).
- `ResolvedLook` shape (presentation.py:229-238):
  - `background={preset, params:{tone}, seed, frozen_at}`
  - `transition_in={kind, seconds}`
  - `variant=None`, `presenter=None`, `text_fit={}`
- `TimelinePresentation`: `fonts={display, body:'inter', mono:'jetbrains_mono'}`, `engine_version=1` (presentation.py:250-261).
- Seed formula: `seed_for` = sha256 of `'{look_seed}:{look_epoch}:{scene_id}:{purpose}'`, first 4 bytes & 0x7FFFFFFF (:153-156). Stored timelines keep whatever was resolved.

**Frozen in B0** (design :2378)
- `base.STAGE_SIZES` (:89), `stage_size` (:105), `output_size` (:124), `timeline_stage` (:114).

**Captions**
- 2 lines x 42 chars, close gaps under 0.5 s, drop cues under 0.04 s (captions.py:22-27).
- `split_caption` signature with `width` and `clusters` (:208).
- SRT/VTT formats and the burn-in escaping (:307-372).
- 9:16 Dynamic: 16 chars/line, minimum 1.0 s, target 1.5 s (presentation.py:44-47).

**Branding**
- `/branding/<file>` stable paths (timeline.py:106, :120). Retired-file mapping happens at serve time (base.py:57, :70).
- `Branding.bgm_volume` 0.06 (timeline.py:156).

**Sounds**
- `TICK_KEY` / `DING_KEY` = `compute_key('scene_audio', {'sfx': name, 'sr': 48000}, version='sfx1')` (sounds.py:26-34). These are content addresses: changing the synthesis without a new name or version would change the bytes stored under the same key.

**Render job**
- Payload `{render_id, burn_captions?, include_intro?, soft_subtitles?}` (video.py:126-149).
- `Render.options` gets `qa` and `warnings` (:921-938).
- Error codes: `bad_payload`, `not_found`, `conflict`, `timeline_missing`, `timeline_stale`, `empty`, `asset_missing`, `config`, `render_unavailable`, `disk_full`, `render_invalid`, `render_failed`, `workspace`.
- Engine switch: `presentation.style == 'dynamic'` -> `motion.job.render_dynamic(ctx, job, payload, ws)` (:999-1003).
- `video._finish_render`, `_log_warnings`, `_mint_token`, `render_warnings`, `branding_file`, `RENDER_TOKEN_TTL_SECONDS`, `TICK_VOLUME` and `DING_VOLUME` are imported by `motion/job.py:104` and `motion/engine.py:69, 602, 940`. These are private names other modules depend on.

**Render page contract** (`window.aadhiRender`: ready/error/states/show/showIntro; screenshot.py:5-10)
- The token travels in the URL fragment (:154-159).
- `FATAL_PAGE_CODES` (:72).

**ffmpeg and workspace**
- Frame grid: track timescale `fps*512`, closed-form `-g fps*2` (ffmpeg.py:882-889). QA checks the grid (qa.py:111, :307).
- Every input gets `-protocol_whitelist file,pipe`; concat lists use `-safe 1` (ffmpeg.py:258-304).
- Workspace layout and `segment_key` (workspace.py:3-18, :95). The manifest fingerprint is `{timeline sha, ffmpeg, w, h, fps, crf, preset}` (video.py:777-779).

**Loudness**
- `LoudnessTarget` defaults -16 LUFS / -1.5 dBTP / LRA 9, with ranges enforced (loudness.py:67-78).
- `limiter_filter` defaults match design §5.12.

**Settings read in this slice** (config.py:283-332, :366, :384)
- `render_fps` / `render_width` / `render_height` / `render_crf` / `render_preset` / `render_concurrency`
- `render_burn_captions` / `render_include_intro` / `render_soft_subtitles` / `render_bgm` / `render_color_bt709` / `render_mascot_continuity` / `render_output_qa` / `render_glass_panels`
- `render_base_url` / `render_work_dir` / `render_keep_failed_hours` / `render_min_free_gb`
- `sync_word_anchors` / `sync_auto_emphasis` / `mascot_cues` / `dynamic_enabled`
- `manim_background`, `branding_dir`, `web_dir`, `ffmpeg_path` / `ffprobe_path`, `s3_presign_ttl_seconds`

**Preflight item shape**
- `{scene_index, scene_id, title, reason, blocking, message}`, with `+severity`/`code` for quality items and `+scene_number` from `render_warnings` (preflight.py:43-51, :161-170; video.py:641-643).

## conventions
**Docstrings and comments**
- Every module starts with a long docstring that states its contract, cites design sections and R-rules (e.g. presentation.py:1-12, timeline.py:1-27), and records "as built" deviations.
- Comments are plain-language, use British spelling (colour, normalise, synthesised) and explain why, citing design § or R-numbers.
- Constants are UPPER_CASE with unit suffixes (`_SECONDS`, `_FRAMES`, `_DB`, `_LUFS`).

**Typing and data models**
- `from __future__ import annotations` everywhere, full type hints, about 120-char lines.
- Value types are frozen dataclasses (Family, Input, Rect, MediaLayer, AudioClip, StateFrame, LoudnessTarget, SyncOptions).
- Schema models are pydantic `StrictModel` with `extra='ignore'` and `allow_inf_nan=False`, without `validate_assignment`. Code mutates models in place (timeline.py:757, :780; resolve_urls :883-939).
- Every v3 field uses `exclude_if` so classic JSON is unchanged.

**Purity and I/O**
- Builders are pure and unit-testable: `build_timeline`, `resolve_looks`, the `ffmpeg.build_*` functions (which return a `Command(args, files)` and never touch disk), `render_preflight`, `scene_sync_cues`, the caption splitters.
- I/O and blocking calls go through `asyncio.to_thread`. Every long await goes through `aio.await_polling` with `check_cancelled` polled about once per second.

**Determinism**
- Seeds are sha256-based, never `hash()` (presentation.py:9, :153; timeline.py:228).
- `random.Random(seed)` is used only for Ken Burns. There is no clock.
- Stored times are rounded to ms (`_r`, `round(...,3)`); iteration is sorted; assets have content-addressed keys (`compute_key`).
- `segment_frames` rounds each shared boundary once (video.py:183-198).

**Classic invariance is the overriding rule**
- Every Dynamic path is gated by `presentation is not None and dynamic_enabled(settings)` (timeline.py:731). The tests assert byte-identical classic timelines with the flag on (test_dynamic_presentation.py:70).
- Invariance goldens (tests/invariance) and a differential pixel test pin the classic commands. New B2/B3 code must not change classic bytes. Example: the tick/ding WAV bytes are content-addressed under fixed keys.

**Errors**
- `FatalJobError(msg, code=...)` for deterministic failures; `RetryableJobError` for transient ones.
- `FFmpegError` carries a redacted stderr tail (`settings.redact`). `ScreenshotError(code, retryable)`. `LoudnessError` subclasses `FFmpegError`.
- Broad excepts carry `# noqa: BLE001` plus a reason comment.
- Optional modules (jobs.limits, auth.tokens, pipeline.assets.scene_hash, tts.normalize) are imported lazily with an ImportError fallback.

**Security**
- No shell; allow-listed subprocess environment (ffmpeg.py:80-102).
- Per-input protocol whitelist; concat-safe names (ffmpeg.py:629-634); safe branding names (video.py:114, :169-180).
- Network policy for the render browser (screenshot.py:186-211).

**Tests**
- pytest with `Settings(_env_file=None, ...)` and the factories `make_screenplay` / `make_manifest` / `screenplay_dict` (tests/compose/factories.py).
- Dynamic tests use `flag_on` / `flag_off` fixtures (`render_fps=25`; test_dynamic_presentation.py:27-34).
- Slow ffmpeg/browser tests are in `*_slow.py`; there are container and determinism suites.
- Each review fix gets a regression test, and docs (ARCHITECTURE / INTERFACES / API / OPERATIONS) are updated for every contract.

## dynamic_status
**B0 pieces in this slice: present and matching design §10.3 B0 item 6 and item 9**
- `base.stage_size`/`output_size`/`timeline_stage` (base.py:89-136).
- `split_caption` `width` parameter (captions.py:208-226).
- Aspect-aware QA edge sampling (qa.py:149-155, :281).
- Burned captions in portrait sized from the short side (ffmpeg.py:1040-1054).
- `build_timeline` sizes the stage from `presentation.format` (timeline.py:776).
- `loudness.py` complete, with tests (tests/compose/test_loudness.py).

**B1 pieces: present**
- `presentation.py`: a minimal `TimelinePresentation` and `ResolvedLook`, covering:
  - the 4 R1 families as a hard-coded table (:61-66), whose numbers match the §4.7 L/C bands;
  - content tokens matching the §4.7 hex table (:69-74);
  - accent rotation (:169-173), which keeps neighbours at least 35° apart for 70° windows;
  - tone steps bg -> bg-alt -> bg-tint, reset per chapter (:216-217, :233);
  - `colour_block` for chapter cards, `mesh_drift` otherwise (:195-198);
  - cut inside a chapter, 0.45 s crossfade at chapter changes, crossfade forced into or out of `colour_block` (R-54) (:219-227);
  - hold freeze via `frozen_at` (:235).
- `DYNAMIC_FPS` 30 and no intro.
- 9:16 phrase captions through a minimum-cost DP (presentation.py:267-320), with cluster-safe word splits (captions.py:43-95), added after the B1 review.
- The engine switch in `video.render_video` (:999-1003).

**B2/B3 pieces: none exist**
- Missing: `compose/dynamic/themes.py`, `textfit.py`, `zones.py`, `kinetic.py`, `events.py`, `mix.py`, `pipeline/direct.py`, and `web/dynamic/` (themes.json, gallery).
- `sounds.py` still has only tick and ding.
- `Timeline.music`, `Timeline.sfx`, `TimedScene.events`, `TimedScene.kinetic_captions` and `ResolvedLook.text_fit`/`presenter`/`variant` are never set by `build_timeline`.

**Divergences from docs/DYNAMIC_VIDEO_DESIGN.md (most expected under B1 scope)**
1. Every scene, including quiz, simulation, ai_video, interactive and chapter_card, gets template `board_generic` (presentation.py:36, :214). §4.4 says board_generic never renders a non-board scene; that is a B2 property test.
2. Teacher look fields outside the B1 sets are dropped silently: `template` not in TEMPLATES, `background` not in BACKGROUNDS, `transition_in` not cut/crossfade (presentation.py:214-224). `variant`, `palette_slot`, `presenter`, `continues_from` and `lock` are ignored.
3. Structure accent and tone change per chapter in both formats (presentation.py:176-192, :232). §4.7 and §2.1 want per act in 9:16 (per segment for Social).
4. `chapter_indices` uses only `screenplay.chapters` and `scene.chapter_id` (:176-192). `build_chapters` falls back to chapter cards and then concept changes (timeline.py:688-700). A lecture without chapters therefore gets one accent for the whole video, and the tone sequence never resets.
5. Missing transitions: push, colour-block wipe, shared-element morph. The background set has 2 of the 6 R1 presets. The hold freeze applies only from `hold_reason`, not from the formula_focus / worked_steps / code_walkthrough templates (R-55).
6. Captions:
   - 9:16 chunks live in `Timeline.captions` / `TimedBeat.captions` at 16 chars x 2 lines, not in `TimedScene.kinetic_captions` (§4.11, §6.7).
   - There is no measured width (660 px), no 3.0 s maximum, no R-33 reading-rate check, no key-term flags (R-35), no blanking (R-31) and no Netflix line-break rules (never split article+noun etc., §2.6).
   - 16:9 Dynamic uses the classic `split_caption` unchanged (timeline.py:546-547), so R-32's 16:9 minimum of 1.0 s per cue is not enforced (`MIN_CUE_SECONDS` 0.04, captions.py:25).
7. `TimelinePresentation.fonts` body is always 'inter' (presentation.py:252). There is no Atkinson option and no Anek for Indic scripts (§4.6). `safe_zone` is always the default 'universal'.
8. Audio for Dynamic still goes through the classic path:
   - The motion engine mixes a tick every countdown second plus the ding at the classic volumes (motion/engine.py:482-500, using video.py:111-112).
   - It adds the branding `bgm.mp3` with sidechaincompress via `ffmpeg.bgm_filter` (engine.py:940-944, ffmpeg.py:1023-1037).
   - §4.10 wants one soft tick 150 ms before the reveal at -22 dB, the ding at -16 dB and a whoosh for bumpers. §5.12 says sidechaincompress is not used and wants a `MusicPlan.duck` envelope.
   - `TimelinePresentation.sfx` is carried but never checked by the motion engine audio.
9. `chapters.youtube_chapter_list` has no 'at least 3 chapters' rule (design §2.8, 16:9).
10. There are two OKLab/OKLCH converters: presentation.py:80-116 and compose/dynamic/colour.py:60 (`oklab_to_srgb`). B2's themes.py should own a single one.

## gaps_todos
There are no TODO, FIXME or XXX markers in the slice. The only `pragma: no cover` is screenshot.py:370.

**Dead or unused code**
- `base.ResolveUrl` (base.py:33) is referenced nowhere.
- `workspace._SEGMENT_SUFFIXES` still lists the legacy '.fades.ffconcat' (workspace.py:59). `build_segment` writes only `.filtergraph` and `.states.ffconcat`.
- In `presentation.portrait_caption_cues.cue_end`, the term `min(hold_end, times[a][0] + PORTRAIT_CAPTION_MIN_SECONDS)` is redundant because `max(..., hold_end)` follows it (presentation.py:288).
- `loudness.loudnorm_measure`, `loudnorm_normalize`, `static_gain_db` and `limiter_filter` (loudness.py:246-330) have no production caller yet; only tests/compose/test_loudness.py uses them. `ebur128` is used by compose/dynamic/container.py:346.

**Half-done or stand-in code (B1 stand-ins that B2/B3 replace)**
- The `FAMILIES` table (presentation.py:61).
- The `TEMPLATES` / `BACKGROUNDS` / `TRANSITIONS` sets (:36-38).
- `PORTRAIT_CAPTION_LINE_CHARS` 16, 'until kinetic.py (B2) measures them' (:43-44).
- `timeline_presentation` hard-codes the body and mono fonts (:252).
- `ResolvedLook.variant` is always None (:231); `text_fit` and `presenter` are never set.

**Missing B2/B3 modules**
- B2: themes.py, kinetic.py, events.py, textfit.py, zones.py, direct.py.
- B3: mix.py, music providers.
- `sounds.py` lacks the R-49 whoosh and soft impact and has no personality-seeded synthesis (§4.10).

**Missing from serve-time resolution**
- `resolve_urls` / `iter_media_refs` / `timeline_asset_keys` (timeline.py:848-864) do not cover `TimedScene.overlays[].media` (the schema has `TimedOverlay.media`, schemas/timeline.py:282-290), the `ResolvedLook.presenter` clip key, `MusicPlan.cues[].asset_key`, or any SFX beyond tick and ding.

**Tests**
- The slice is well covered: test_timeline*, test_captions, test_dynamic_presentation, test_format_plumbing, test_loudness, test_sounds, test_preflight, test_qa, test_ffmpeg_*, test_video, test_workspace, test_screenshot, test_capabilities, test_aio_frames, test_chapters, and the invariance goldens.
- Not covered by any test I found:
  - a flag-off **portrait** presentation going through the classic renderer and the live player; test_format_plumbing.py:84-93 checks only the stage size;
  - Dynamic sync-cue snapping on the 30 fps grid when `render_fps` differs;
  - `chapter_indices` for a lecture without chapters.

## bugs_or_risks
1. **Flag-off portrait lecture: the classic MP4 and the classic live player disagree on size.**
   - `_assemble` sizes the stage from `presentation.format` even when `DYNAMIC_ENABLED` is off (timeline.py:776). This is intended, per the B0 test at test_format_plumbing.py:84-93 and the assertion at test_dynamic_presentation.py:83.
   - The result is a classic timeline (intro, mascot, classic captions) at 1080x1920.
   - The classic render page sizes itself from `Timeline.width`/`height` (web/js/render/render.js:41-61), and the screenshot viewport and `output_size` follow it (screenshot.py:425-428, base.py:124-136). The MP4 is therefore a 9:16 classic video, with the 1280x720 mascot clips cover-cropped by `background_filter` (ffmpeg.py:523-531).
   - The classic live Player resizes only for Dynamic and says "a classic timeline is always 1920x1080" (web/js/player/player.js:319, :403), so the watch page shows 16:9.
   - The docs contradict each other: INTERFACES.md:367 says "flag off -> the classic timeline, unchanged", while INTERFACES.md:346 and base.py:116 / screenshot.py:23 / render.js:15 claim "1920x1080 for every classic timeline".
   - Burned captions in this case keep 42-char lines (`ctx.caption_width` is None, timeline.py:406, :732) at the classic glyph size (ffmpeg.py:1047). A 42-char line at about 80 px exceeds the 1080 px frame width, so libass will wrap it beyond 2 lines. That wrap is an estimate; I have not measured it.
   - The scenario is reachable because `DYNAMIC_FEATURES` (config.py:366) and `DYNAMIC_ENABLED` (config.py:384) are independent, and stored presentations stay editable once the flag is off (design §6.7).

2. **Dynamic sync cues snap to the wrong frame grid.**
   - `SyncOptions.from_settings` takes fps from `settings.render_fps` (sync.py:103), passed in at timeline.py:728.
   - A Dynamic timeline is always 30 fps (timeline.py:787).
   - With `RENDER_FPS=25`, which is the test fixture at test_dynamic_presentation.py:29, `_Snap` merges cue times within 40 ms instead of 33 ms. Cues are not frame-aligned for the Dynamic engine.
   - This is harmless today, but `events.py` (B2) is designed to consume sync cues and words (§6.8).

3. **R-54 crossfade can drop below 300 ms.**
   - `resolve_looks` uses `seconds = min(CROSSFADE_SECONDS, duration/2)` (presentation.py:227). A scene shorter than 0.6 s gets a forced `colour_block` crossfade under 300 ms, violating R-54.
   - Narrated scenes are at least about 1.3 s, so this is unreachable today. Clip scenes (B6) could be short.

4. **Chapter detection differs between looks and chapters.**
   - `chapter_indices` (presentation.py:176-192) ignores the chapter-card and concept fallbacks of `build_chapters` (timeline.py:688-700).
   - In a lecture with chapter cards but no `screenplay.chapters` or `chapter_id`, the chapters list shows several chapters, yet the looks keep one accent and never reset the tone or cross-fade at chapter starts. The `colour_block` card does still force a crossfade.

5. **Serve-time URL coverage gap (future risk).**
   - `iter_media_refs` (timeline.py:848-857) does not iterate `TimedScene.overlays` or the look `presenter`.
   - When B2/B6 populate those, their URLs stay None in served timelines unless this function and `timeline_asset_keys` (:860) are extended. `_scene_required_keys` (video.py:544-553) has the same gap for the classic renderer.

6. **`loudness._fmt` crashes on non-finite values.**
   - `int(value)` (loudness.py:209-210) raises OverflowError for inf and ValueError for nan.
   - Targets and measured values are validated or clamped before they reach it, but `limiter_filter` does not validate `attack_ms` / `release_ms` (:254-259).
   - This is low risk and depends on the caller; B3's mix.py must pass finite values.

7. **The motion engine depends on private classic helpers.**
   - It imports `video._finish_render`, `_log_warnings` and `_mint_token` (motion/job.py:104, motion/engine.py:602), plus `TICK_VOLUME` / `DING_VOLUME` / `branding_file` (engine.py:69, :940).
   - Refactoring video.py for B3 audio or classic changes will silently affect Dynamic renders.

8. **`presentation.py` reaches into captions privates.**
   - It imports `_CLOSE_GAP_SECONDS`, `_SENTENCE_END`, `_fits`, `_proportional_times` and `_token_times` (presentation.py:24-33).
   - A change to classic caption internals (which must stay byte-identical) can therefore change 9:16 Dynamic captions.

9. **Sound keys are fixed content addresses.**
   - `sound_key` hashes only `{sfx, sr}` with version 'sfx1' (sounds.py:26-30). Changing `tick_wav` / `ding_wav` synthesis would serve different bytes under the same key across stores, and would also break the classic goldens.
   - B3's per-personality, per-video seeded SFX need new names, or parameters folded into the key.

## b2_b3_touchpoints
**B2 (visual system: themes / textfit / zones / fonts / icons / kinetic / direct / events / gallery)**
- **timeline.py:729-733** (Dynamic gate and portrait caption width) and **:546-552** (`_timed_beats` caption branch):
  - `kinetic.py` should build `TimedScene.kinetic_captions` here, or right after `_build_scene` at :755.
  - It should replace `portrait_caption_cues`, add the 16:9 1.0 s minimum, key terms and blanking, and decide what `Timeline.captions` (the SRT/VTT sidecars) carries.
- **timeline.py:661** (`sync_cues`) and **:755-759** (per-scene assembly):
  - `events.py` runs after `_build_scene` to fill `TimedScene.events` (VisualEvent) from the beats, words and sync cues.
  - Pass the Dynamic fps (`DYNAMIC_FPS`, :110) into `SyncOptions` (sync.py:99-104), not `settings.render_fps`.
- **timeline.py:776-781** (stage size, `resolve_looks`, `timeline_presentation`):
  - `direct.py` writes `SceneLook` into the screenplay before this point (generation and save, §6.8).
  - `themes.py` replaces `presentation.FAMILIES` / `content_tokens` / `structure_tokens` (presentation.py:51-147).
  - `resolve_looks` (:201-240) must take the full template, variant, background and transition sets (push, colour wipe, morph). It must also handle the template-based hold freeze (R-55), `text_fit` from textfit.py, `presenter` (aadhi_intro / recap), and act-based accents for 9:16 (`chapter_indices` :176).
  - The B1 sets to widen are TEMPLATES / BACKGROUNDS / TRANSITIONS (presentation.py:36-38).
- **presentation.py:243-261**: fonts per §4.6 (Atkinson option, Anek for Indic), `safe_zone` (zones.py), and a bump of `ENGINE_VERSION` (:42) if the stage contract changes.
- **timeline.py:848-864**: `iter_media_refs` / `timeline_asset_keys` must yield presenter clips (Aadhi frame-extraction cache) and overlay media, so `resolve_urls` (:867) fills their URLs.
- **captions.py:84-161**:
  - `tokens_with_owner`, `_cluster_split`, `balance_lines` and `_group_tokens` are the line-break rules kinetic.py reuses (§4.11).
  - Promote the privates imported at presentation.py:24-33 to a public API rather than changing their classic behaviour (classic goldens).
- **chapters.py:54-83**: the 'at least 3 chapters' rule for Dynamic 16:9 exports (§2.8), kept conditional so classic output is unchanged.
- **base.py:89** `STAGE_SIZES` (frozen) and **ffmpeg.py:1040-1054**: Dynamic must not use libass (§4.11), so only the classic path keeps `subtitles_filter`.
- **video.py:556-564** `_copy_fonts` copies `web/vendor/fonts`. That folder holds only Inter, JetBrains Mono, Outfit and Noto Indic (no Poppins, Lexend, Atkinson or Anek, and no LICENSE files). B2 vendoring adds files there, and they will also be copied into classic burn-in renders.
- **preflight.py:31-40** `MESSAGES` / reasons, if B2 adds Dynamic degradation reasons. Keep `BLOCKING_REASONS` (:28) unchanged for classic.

**B3 (audio: personas, casting, history, calibration, music providers, mix.py, live-player audio)**
- **sounds.py:20-118**:
  - Add the R-49 set (whoosh, soft impact; chime = ding; one soft tick) as new `SOUND_NAMES` / `_GENERATORS` entries with their own keys.
  - Personality- or seed-dependent synthesis must enter `compute_key` inputs (`sound_key` :26).
  - Keep `tick_wav` / `ding_wav` bytes unchanged for classic.
- **loudness.py:67-330**:
  - `LoudnessTarget` (-14 or -16 by destination, §2.6 / §5.12).
  - `loudnorm_normalize` (:305) for music intake (§5.10 step 3) and the master chain.
  - `static_gain_db` (:246) for per-speaker gains (§5.4; applied when `scene_audio` is built in pipeline/audio.py, outside this slice).
  - `limiter_filter` (:254) for the linear-mode fallback.
  - `ebur128` (:273) for preflight and QA (already used by dynamic/container.py:346).
- **timeline.py:717-802 `_assemble`**: `mix.py` builds `Timeline.music` (MusicPlan with cues and the duck envelope) and `Timeline.sfx` (SfxCue) on every `build_timeline` (§6.8), gated like the presentation (:778). Passing them with `**{...}` like :801 keeps classic JSON unchanged.
- **timeline.py:145-159 `default_branding`**: `bgm_url` / `bgm_volume` 0.06 are classic only.
- **timeline.py:867-942 `resolve_urls`**:
  - Must resolve `MusicPlan.cues[].asset_key` and the SFX asset URLs for the live player's Web Audio. Today only `branding.tick_url` / `ding_url` are filled (:887-939).
  - Music must be same-origin or CORS (§5.12).
- **video.py:111-112** `TICK_VOLUME` / `DING_VOLUME`, **:169** `branding_file`, **:320-331** (classic quiz audio):
  - Reused by motion/engine.py:69, :482-500 and :940-944. B3 replaces that Dynamic path with mix.py's master and must honour `TimelinePresentation.sfx` (presentation.py:257).
  - The classic `scene_segment_spec` audio stays unchanged.
- **ffmpeg.py:1023-1037** `bgm_filter` (sidechaincompress) is classic only per §5.12; motion/encode.py:217-219 currently uses it for Dynamic and must switch to the mix.py master. Other relevant pieces:
  - `AudioClip` / `audio_mix_filter` (:780-828);
  - `AUDIO_RATE` (:68);
  - `FinalSpec.audio_bitrate` '192k' (:1007) is classic; Dynamic uses AAC-LC 384k (§2.8).
- **qa.py:192-199** (`audio_levels`, volumedetect) and **:261-278** (silence problem): R-48 loudness QA for Dynamic lives in dynamic/container.py and builds on loudness.py.
- **preflight.py**: none directly. The §7.12 Dynamic audio checks belong in compose/dynamic/preflight.py.

## files
- aadhi/compose/__init__.py (17): Package docstring listing the compose modules; the import is kept cheap. || Docstring only (module list :5-16).
- aadhi/compose/base.py (136): Compose contract docstring; branding file names; timing constants; stage and output size per format (frozen in B0). || MASCOT_CLIPS :46 (position -> *_clean.mp4); RETIRED_MASCOT_CLIPS :57; STATIC_BACKGROUND :64; LOGO_VIDEO :67; RETIRED_BRANDING_FILES :70; BGM 'bgm.mp3' :73; INTRO_LOGO_SECONDS 5.5, INTRO_CARD_SECONDS 3.0, SCENE_TRANSITION_SECONDS 0.4, SCENE_LEAD_SECONDS 0.5, SCENE_TAIL_SECONDS 0.8, SILENT_SCENE_SECONDS 4.0, QUIZ_REVEAL_HOLD_SECONDS 1.0 :75-81; STAGE_WIDTH/HEIGHT 1920x1080 :84-85; STAGE_SIZES {landscape,portrait} :89; stage_size(format) :105 (ValueError for unknown); timeline_stage(tl) :114; output_size(tl, settings) :124 (classic stage = RENDER_WIDTH x RENDER_HEIGHT exactly; other stages scaled by short side / 1080, even sides); ResolveUrl alias :33 (unused anywhere).
- aadhi/compose/timeline.py (942): Pure timeline builder (classic + Dynamic B1 wiring), chapters, and serve-time URL resolution. || STORAGE_KEYS_META 'asset_storage_keys' :104; FALLBACKS_META 'fallbacks' :105; BRANDING_PREFIX '/branding/' :106; CAPTION_HOLD_SECONDS 0.4 :107; MIN_EST_BEAT_SECONDS 0.6 :108; FULLSCREEN_TYPES :109; DYNAMIC_FPS 30 :110; default_branding :145 (bgm_volume 0.06, tick/ding None); build_intro :162; _r ms rounding :189; scene_beats :194; ken_burns_for (sha256-seeded Random) :226; _plan_from_audio :274; _plan_estimated :298; _speech_forms/_beat_matches/_audio_still_valid :356-390 (preview audio reuse); _Ctx (figures, storage_keys, fallbacks, sync, caption_width) :399; _media_ref/_main_media/_poster/_panel_media/_figures :413-500; fallback boards :503-524; _timed_beats :532 (classic split_caption vs portrait_caption_cues when caption_width set; last-cue hold); _build_scene :575 (duration, min_seconds hold, quiz timing, side panel show_at, sync cues :661); build_chapters :671; _assemble :717 (dynamic gate :731, portrait caption width :732-733, no intro for dynamic :734, hidden filter :737, stage size :776, looks + presentation :778-781, fps :787); build_timeline :805; preview_timeline :824; iter_media_refs :848; timeline_asset_keys :860; resolve_urls :867 (retired clip mapping, tick/ding via ensure_sounds).
- aadhi/compose/presentation.py (325): B1 Dynamic look resolver: TimelinePresentation and the per-scene ResolvedLook (OKLCH colour tokens, background preset and tone, cut/crossfade), plus the 9:16 minimum-cost phrase caption grouping. || TEMPLATES ('board_generic',) :36; BACKGROUNDS ('mesh_drift','colour_block') :37; TRANSITIONS ('cut','crossfade') :38; CROSSFADE_SECONDS 0.45 :40; TONES :41; ENGINE_VERSION 1 :42; PORTRAIT_CAPTION_LINE_CHARS 16 :44; PORTRAIT_CAPTION_MIN_SECONDS 1.0 / TARGET 1.5 :46-47; Family dataclass :51; FAMILIES (midnight_indigo, deep_teal, paper_ink, mint_studio) :61; _CONTENT_FIXED :69; oklch_hex (gamut clamp by chroma bisection) :104; content_tokens(mode) :119; structure_tokens(family, accent_hue) :130; seed_for (sha256 of 'look_seed:look_epoch:scene_id:purpose', 31 bit) :153; family_for (unknown id -> hash pick) :159; accent_hue (step 0,1,0.5 of window by chapter%3) :169; chapter_indices :176; _background_preset :195; resolve_looks :201; timeline_presentation :243 (generated_seconds excludes 'clip' scenes); portrait_caption_cues (DP grouping) :267; dynamic_enabled(settings) :323. Imports private helpers from captions (_CLOSE_GAP_SECONDS, _SENTENCE_END, _fits, _proportional_times, _token_times) :24-33.
- aadhi/compose/captions.py (372): Caption cue splitting (classic 42x2), cluster-safe long-word splitting (for narrow 9:16), and SRT/VTT/libass-safe serialisation. || MAX_LINE_CHARS 42, MAX_LINES 2, EST_SECONDS_PER_CHAR 0.065, MIN_CUE_SECONDS 0.04, _CLOSE_GAP_SECONDS 0.5, _SENTENCE_END (incl. danda) :22-27; _hard_split :36; _unsafe_cut (marks, ZWJ/ZWNJ, virama ccc 9) :43; _cluster_split :52; tokenize :75; tokens_with_owner :84; wrap_tokens :98; balance_lines :115; _fits :130; _group_tokens :139; _proportional_times :164; estimate_words :178; _token_times (owner-aware) :189; split_caption(text, words, *, start, end, width=42, clusters=False) :208; shift_cues :251; normalize_cues :256; _ms_pairs :276; escape_srt_text :307; escape_burn_in_text (U+2060 after backslashes, braces escaped) :326; escape_vtt_text :338; to_srt(burn_in=) :346; to_vtt :364.
- aadhi/compose/sync.py (526): Word-anchored sync cues (var / emphasis / output / focus) placed on spoken words, snapped to frames; pure. || FOCUS_SECONDS 1.6, FOCUS_MIN_GAP 4.0, MAX_FOCUS_PULSES 2, MAX_AUTO_EMPHASIS_PER_BEAT 2, MAX_CUES_PER_SCENE 64, MIN_EMPHASIS_SECONDS 1.2 :49-58; SyncOptions(fps, auto_emphasis).from_settings (fps = settings.render_fps) :92-104; norm_token/stem/tokens/is_content :116-146; Stream/word_stream :169-191; find_phrase :198; marked_phrases/column_phrases/item_anchors/variable_phrases :226-292; _Snap (merge within one frame) :300; explain_window :328; _var_cues :340; _item_cues :391; _emphasis_cues :426; _panel_cues :460; scene_sync_cues :508.
- aadhi/compose/chapters.py (118): YouTube description chapters and the ffmpeg FFMETADATA1 chapters document. || YOUTUBE_MIN_CHAPTER_SECONDS 10 :11; OPENING_TITLES :13; clean_title :17; title_key :23; opening_title :28; format_timestamp :44; youtube_chapter_list :54 (00:00 first, >= 10 s each; no >= 3 rule); youtube_chapters :86; to_ffmetadata :99.
- aadhi/compose/preflight.py (181): Pure render preflight: degradations (silent scenes, boards replacing missing media, panel or hotlink media missing from the MP4) and non-blocking quality items. || BLOCKING_REASONS {no_audio, audio_mismatch} :28; MEDIA_PANEL_KINDS :29; MESSAGES :31; render_preflight(timeline, issues=None, hidden=) :58; QUALITY_MAX_ITEMS 12, QUALITY_SEVERITIES, QUALITY_SOURCES {lint,critic,system} :102-107; quality_items :122 (errors first, lecture/scene alternation, '+N more' item); has_blocking :179.
- aadhi/compose/sounds.py (118): Deterministic numpy-synthesised tick and ding WAVs, stored once as content-addressed assets. || SAMPLE_RATE 48000, SOUND_VERSION '1', SOUND_KIND 'scene_audio', SOUND_NAMES ('tick','ding') :20-23; sound_key(name) = compute_key(kind, {sfx, sr}, version='sfx1') :26; TICK_KEY/DING_KEY :33-34; _to_wav :37; _envelope :50; tick_wav (90 ms, 1850+3700 Hz) :60; ding_wav (1.2 s, 4 inharmonic partials) :68; SoundAsset :84; ensure_sounds (blocking) :96; ensure_sounds_async :109.
- aadhi/compose/loudness.py (330): B0 EBU R128 helpers: ebur128 measurement, two-pass loudnorm with JSON parse, static gain, limiter filter (for Dynamic audio B3; classic does not use them). || SILENCE_LUFS -70 :33; _MEASURED_BOUNDS (LRA min 0.01) :37; LoudnessError(FFmpegError) :45; LoudnessTarget(integrated=-16, true_peak=-1.5, lra=9; validated ranges) :67; R128Summary (.silent, .as_dict) :82; LoudnormStats (.linear, .measurable, .as_dict) :114; parse_ebur128 :156; parse_loudnorm :184; _fmt :209; ebur128_filter :218; loudnorm_filter :224; static_gain_db :246; limiter_filter(limit=0.84, attack 5, release 50, level=0) :254; ebur128() :273; loudnorm_measure :289; loudnorm_normalize (returns (first, second)) :305.
- aadhi/compose/ffmpeg.py (1094): Async process runner (allow-listed env, timeout, cancel polling, tree kill), probing, version gate, and pure filtergraph / command builders for segments and the final mux. || AUDIO_RATE 48000, PROTOCOLS 'file,pipe', STATE_FADE_SECONDS 0.15, MASCOT_CROSSFADE_SECONDS 0.6, BT709_* :68-75; FFmpegError :86; subprocess_env :100; kill_tree :105; run_process :144; ffmpeg_base/run_ffmpeg :198-208; parse_ffmpeg_version/filter_script_flag/ffmpeg_version :220-249; Input/image_input/video_input/concat_input/state_list_input :258-304; MediaProbe/parse_probe/probe :313-378; ft/even/to_yuv/relabel_bt709/alpha_fade_in :386-422; Rect.from_stage :435; fit_filter :454; ken_burns_filter :467; clock_input :489; loop_phase_frames :500; background_filter :513; crossfade_clip_filter :541; MediaLayer :556; untagged_hd :578; media_filter :584; overlay_filter :618; concat_document/concat_list :643-663; StateFrame/StateMix/StatePlan/plan_states/state_mixes/state_stream_filter :667-773; AudioClip :780; audio_mix_filter :790; Command :837; SegmentSpec :845; video_codec_args (track timescale fps*512) :882; build_segment :892; FinalSpec (audio_bitrate 192k) :988; subtitle_language_code :1018; bgm_filter (sidechaincompress) :1023; subtitles_filter (portrait sized from the short side) :1040; build_final :1057.
- aadhi/compose/video.py (1031): The render_video job handler: classic states renderer end to end, plus the engine switch to the Dynamic motion engine. || RENDER_TOKEN_TTL_SECONDS :110; TICK_VOLUME 0.8, DING_VOLUME 0.9 :111-112 (also used by motion/engine.py:69); BLANK_STATE :113; _FONT_BY_LANG :115; RenderPayload.from_dict :126; _Job :153; branding_file :169; segment_frames :183; font_for_language :201; _first_rect :206; SceneInputs :227; scene_segment_spec :250; intro_segment_spec :335; _start_render/_load_job/_finish_render/_mark_render :384-469; _local_copies :472; _mint_token :508; _render_slot_factory :523; _scene_required_keys :544; render_warnings :624; _log_warnings :647; sweep_render_workspaces :677; _input_digests :690; _untagged_media :712; _clip_phases :734; _render :751; render_video :976 (dynamic dispatch :999-1003).
- aadhi/compose/screenshot.py (509): Playwright driver for /render-frame: render-mode state screenshots under a strict network policy (route allow-list, dead proxy, blocked WebSockets). || MAX_STATES_PER_SCENE 2000 :49; CHROMIUM_ARGS :51 (reused by motion/stage.py, dynamic/stage_page.py); FATAL_PAGE_CODES :72; ScreenshotError(code, retryable) :78; StateShot/IntroShot/CaptureResult :91-125; origin_of :133; render_url (token in fragment, &glass=1) :154; render_origin :162; missing_browser :172; NetworkPolicy (build/allows/proxy_bypass) :186; media_origins_for :214; page_error :228; normalize_states :252; _capture_scene (one PNG per distinct key) :310; capture_render_frames (viewport = timeline_stage) :345.
- aadhi/compose/qa.py (317): Post-render MP4 QA: exact frame count, frame grid, declared CFR, audio presence and volumedetect, black edges (aspect-aware), colour tags, soft track. || SILENCE_DB -60, BLACK_LUMA 10, EDGE_SAMPLE (320,180), EDGE_STRIP 2 :29-32; RenderQA (.ok, .summary) :37; parse_rate :92; parse_volumedetect :103; grid_stats :111; black_strips :126; edge_sample_size :149; expected_frame_count :158; packet_pts :179; audio_levels :192; gray_frame :202; inspect_render :214 (problems: no video, frames off by >1, no audio, silent narration); _grid_step :307.
- aadhi/compose/frames.py (80): State-PNG hygiene: RGBA8 at stage size, premultiplied crossfade mixes, transparent blank frame. || _is_rgba8_png :23; ensure_rgba_png :29; normalize_pngs :46; write_state_mixes (Image.blend in RGBa) :51; transparent_png :76.
- aadhi/compose/capabilities.py (172): Cached host check for MP4 rendering (ffmpeg/ffprobe, libx264, aac, the libass subtitles filter, optionally Chromium). || CACHE_TTL_SECONDS 300, FAILURE_TTL_SECONDS 30 :32-33; RenderCapabilities :40; resolve_tool :54; _listing :73; _has_entry :87; chromium_installed :96; _check :118; render_capabilities :150; reset_capabilities_cache :169.
- aadhi/compose/workspace.py (313): Durable per-render workspace: content-addressed segment cache with .ok markers, per-attempt dirs, and sweeping. || SEGMENT_CACHE_VERSION 1 :53; _SEGMENT_SUFFIXES (incl. legacy '.fades.ffconcat') :59; work_root :62; free_gb :69; file_digest :77; stat_digest :89; segment_key :95; wav_frames :121; RenderWorkspace (open/prepare/cached/adopt/store/finish) :140-226; workspace_ids :236; sweep_workspaces :250; sweep_legacy_temp :275; sweep_stale :292 (never raises).
- aadhi/compose/aio.py (95): Asyncio helpers: a deadline with cancel polling, and fail-fast gather. || CANCEL_POLL_SECONDS 1.0 :19; DeadlineExceeded :22; reap :32; await_polling :43; gather_or_cancel :76.