# tests-py


## summary
The Python test suite (tests/**, 320 .py files, ~68.4k lines, 3,095 test functions: pipeline 669, compose 466, api 428, core 423, providers 337, manim 225, jobs 212, evals 157, storage 43, schemas 36, invariance 36, usage 24, determinism 24, branding 11, plus 4 top-level) runs fully offline. It is isolated by tests/conftest.py, which forces fake providers, blank API keys, closed DYNAMIC_FEATURES gates and session-private TEMP/SCRATCH dirs before any `aadhi` import. Each test gets a fresh SQLite DB and data dir through the `app_env` fixture.

Markers are `slow` (real Chromium, ffmpeg or Manim) and `container` (checks the Docker test image must pass on ffmpeg 5.1, always also `slow`). `network` is declared but no test uses it (see bugs_or_risks). asyncio_mode is "auto", so plain `async def` tests run without a marker.

The Dynamic-video tests live in four places:
- tests/compose/dynamic: browser-free unit tests for colour, container QA, preflight and stage-call classification, plus slow Chromium tests against a stub stage whose pixels are a pure function of (timeline, t).
- tests/compose/motion: the motion engine (chunk planner, cache key, ffmpeg command lines pinned to §7.5, media-frame registry, cgroup-aware W, cross-process CPU slots, presigned-link refresh). There are slow end-to-end renders against a second stub stage that draws a 16-bit frame-number barcode, so decoding the MP4 proves every frame appears exactly once across chunk joins.
- tests/determinism: the B1 acceptance on the REAL stage (/render-dynamic served by uvicorn through the module-scoped `real_app` fixture). It covers 1x300/3x100/reverse hash equality, the timer guard, danda font loading, live-vs-MP4 SSIM, decoded-frame continuity at joins, layout and R-29 checks, and that the live player's CSS does not change the stage.
- tests/compose/test_dynamic_presentation.py: the B1 minimal TimelinePresentation/ResolvedLook resolver (aadhi/compose/presentation.py) and cluster-safe 9:16 phrase captions.

B0 is guarded by:
- tests/schemas/test_v3_schema.py: a v2 field baseline, V3_FIELDS with exclude_if, the newer_document refusal, and that the generated web schemas are current.
- tests/core/test_transform_survival.py: maximal fixtures shared with the JS editor tests.
- tests/core/test_assets_auth.py and tests/api/test_asset_auth_dynamic.py: the frozen FIELD_RULES table.
- tests/invariance: classic goldens and the differential pixel test, run in CI's container job.
- tests/usage: the music price and fail-closed rules.

No Python tests exist yet for any B2 or B3 module (themes, textfit, zones, kinetic, direct, events, gallery, personas, casting, history, music, mix). B1's Python modules each have dedicated tests. Two B1 requirements, easing ≤ 1e-4 and grapheme splitting, are covered by JS tests (web/tests/motion/*.test.js), with Python covering cluster-safe caption splitting.

## contracts
Contracts pinned by tests (changing any of these breaks a test):

SCHEMA / B0
- DYNAMIC_FEATURES == ("presentation","items_v3","cuts","overlays","clip","voice_override"); READER_SCHEMA_VERSION 3, CLASSIC 2; NewerDocumentError.code "newer_document" and not a ValueError subclass (tests/schemas/test_v3_schema.py:93-98).
- Every field added to a v2 model (baseline tests/schemas/fixtures/v2_model_fields.json) must be in cls.V3_FIELDS with exclude_if true at default. New models in aadhi/schemas/{screenplay,timeline,manifest}.py must be in V3_ONLY_MODELS or carry V3_MODEL (test_v3_schema.py:41-90).
- The generated web schemas must be current: `python -m aadhi.schemas.generate` (test_v3_schema.py:465-472).
- The maximal fixture must equal its canonical dump: regenerate with `python -m tests.schemas.v3_fixtures` (test_v3_schema.py:121-124).
- FIELD_RULES is frozen: 9 rows, including ClipScene.clip / Overlay.clip {clip, upload, video} motion; Presentation.music {music, upload} audio; Beat.voice_override_asset_key {narration_upload} audio (tests/core/test_assets_auth.py:123-145).
- The schema has exactly 8 asset-key fields (test_assets_auth.py:303-312).
- music, clip, clip_proxy, clip_sprite, waveform, voice_sample, thumbnail and narration_upload are public; calibration stores under private/ (test_assets_auth.py:148-162).
- Classic goldens are byte-identical: tests/invariance/goldens/** including _api/generation_options.json, which has no voice or music keys (test_goldens.py:326-330).

STAGE CONTRACT v3 (stubs plus the real page)
- window.aadhiStage = {version: 3, ready -> {webgl}, info() -> {width, height, fps, frames, scenes:[{index, f0, f1}]}, mediaPlan(f0, f1) -> [{key, asset_key, kind 'video'|'aadhi', srcFps, frames[], size[w,h]}], seek(f) -> {key}, textBoxes() -> [{x, y, w, h, role, critical, slot, ink, bg, overflow, scene, size, weight, min, text}], setLayers({captions, stage})}.
- window.aadhiStageGuard.trips; the real guard throws code "timer_guard" with "timer guard" in the message (tests/api/test_render_dynamic_route.py:260-311; tests/determinism/test_frame_hashes.py:359-381).
- The token travels in the fragment only: stage_url == base + "/render-dynamic#token=..." (test_stage_page.py:34-37).
- The page drops the fragment, and the timeline is fetched with a Bearer token.
- Media frames: /__frames/[<chunk>/]<key>/<n, 6 digits>.jpg; keys look like "v-<hash>-<w>x<h>"; 8-digit indices and path traversal are rejected (tests/compose/motion/test_media.py:275-284).
- StageError codes and retryability:
  - render_page_<boot code>: retryability follows the boot code.
  - render_page_timeline_http, render_page_http, render_page_script_error, stage_contract: not retryable.
  - stage_call_failed: not retryable, though the engine still retries it once on a fresh page.
  - stage_crashed, stage_capture_failed, stage_refresh_failed: retryable.
  - Sources: test_stage_calls.py; test_cache.py:61-68.
- /render-dynamic returns 404 when DYNAMIC_ENABLED is off or the file is missing; it has the same CSP and referrer policy as /render-frame and includes data-aadhi-render="dynamic" (test_render_dynamic_route.py:53-94).

MOTION ENGINE
- Chunk encoder argv, exactly: -protocol_whitelist file,pipe -f image2pipe -c:v mjpeg -framerate 30 -i pipe:0 -vf <BT.601 full -> BT.709 tv + setparams> libx264 high veryfast crf 17 maxrate 12M bufsize 24M g 15 keyint_min 15 sc_threshold 0 bf 2 open-gop=0 -threads 4 -frames:v N -an -f mpegts (tests/compose/motion/test_encode.py:45-55).
- Settings: MOTION_X264_PRESET is a Literal; MOTION_X264_CRF.
- Final mux: +faststart+negative_cts_offsets; video and audio are both copied; mov_text default track; -map_chapters 2. Master audio: AAC 384k, 48 kHz, stereo (test_encode.py:164-198).
- Chunk planner: scene-aligned, max 600, long scenes split evenly, at most one short chunk in a row (test_chunks.py).
- Chunk key: 64 hex characters. It covers:
  - the chunk's own scenes plus the outgoing previous scene;
  - the presentation, captions in range and chapter titles;
  - the bundle digest of web/js/dynamic and render-dynamic.html;
  - the encoder and capture fingerprints (quality 92, layers);
  - the Chromium version and the media digests.
- STAGE_TIMELINE_FIELDS must contain every `timeline.X` read in web/js/dynamic/**.js (test_chunks.py:197-213).
- ChunkCache files are <key>.ts plus a marker {frames, bytes, keys}; they live in the render workspace and only a retry reuses them (test_cache.py).
- Workers: MAX_AUTO_WORKERS=6; W = max(1, min(6, floor(quota)-1, physical-1, affinity-1)); cpu_slot_count = physical-1 unless RENDER_CPU_SLOTS is set; lock files are slot-NNN.lock in RENDER_SLOT_DIR, default <work_root>/cpu-slots (test_resources.py).
- native_frames: page index n -> floor(n * rate / srcFps).
- extract_args: -ss (first - 0.5)/fps placed before -i, -q:v 3, f_%06d.jpg (test_encode.py:78-130).
- caption_modes(options, tl) -> (burn, soft) (tests/compose/motion/test_job.py:40-49). FatalJobError "dynamic_disabled" (test_job.py:103-112).

PREFLIGHT / QA
- PREFLIGHT_SCHEMA = 1. summary() has exactly these keys: schema, ok, format, frames, fps, sampled, rounds, seconds, webgl, checks, items, remedies, stats, problems, warnings. checks covers text_overflow, safe_zones, contrast and type_floor (tests/compose/dynamic/test_preflight_checks.py:391-401).
- Contrast thresholds: caption 7.0; formula 4.5; 3.0 at ≥ 32 px or ≥ 24 px at weight 700; otherwise 4.5 (test_preflight_checks.py:155-162).
- Type floor: chips 40 px in 16:9; labels 44 px in 9:16; 9:16 text at ≤ 64 px needs weight ≥ 600. The 9:16 shrink floor is 40 px.
- Zones (aadhi/compose/dynamic/preflight.py:75-78, marked "until B2's zones.py"):
  - landscape (54-912, 96-1824);
  - portrait (288-840, 192-888) and (840-1248, 120-780).
- Errors: PreflightBlocked code "preflight_blocked"; ContainerCheckFailed code "render_invalid".
- The motion QA document is a superset of the classic RenderSummary.options.qa keys, plus engine, schema, starts{video, video_raw, audio, av_offset}, constant_frame_rate, remedies[{remedy, problems}] and preflight (tests/compose/dynamic/test_container.py:31-32, 110-129).

TIMELINE PRESENTATION (B1 minimal)
- Presentation fields: style, format, content{ink, ink2, entity:0..2, warn, ok, mark, focus}, fonts{display, body, mono}, personality, caption_style, cue_term_style, captions_burn, sfx, motion, generated_seconds, engine_version.
- Look fields: template, tokens{struct.accent, bg, bg-alt, bg-tint, surface}, background{preset, params{tone}, seed, frozen_at}, transition_in{kind, seconds}, hold_reason, variant, text_fit{slot: {size, lines, scrim}}.
- DYNAMIC_FPS 30; a Dynamic timeline has no intro and schema_version 3 (tests/compose/test_dynamic_presentation.py:90-136).
- PORTRAIT_CAPTION_LINE_CHARS = 16 (aadhi/compose/presentation.py:44); a cue holds at most 2 lines and 32 characters.

ENV / SETTINGS USED BY TESTS
- AADHI_UPDATE_GOLDENS; AADHI_EXPECT_FFMPEG; AADHI_NETWORK_TESTS; AADHI_API_TEST_FAKES (all|none|missing); AADHI_INVARIANCE_BASE.
- DYNAMIC_ENABLED, DYNAMIC_FEATURES, RENDER_MOTION_WORKERS, RENDER_CPU_SLOTS, RENDER_SLOT_DIR, MOTION_X264_PRESET/CRF.

PARITY FIXTURE
- evals/render_parity.py:438-594: dynamic_timeline(fmt, family). 366 frames; scenes at 0/140/279; seed 20261010, 20 samples; SSIM 0.98.
- LIVE_STYLESHEETS; DYNAMIC_RICH_TEXT. Portrait uses caption_style bold_outline.

## conventions
- Every test module starts with `from __future__ import annotations` and a docstring. It names the design section it guards (e.g. "DYNAMIC_VIDEO_DESIGN §7.12") and the rule ids it covers (R-29, R-32, R-53, R-59).
- Test names are full sentences: test_a_retry_reuses_the_cached_chunks, test_a_problem_that_survives_its_remedy_is_an_engine_bug. Spelling is British (colour, authorisation, normalise).
- Plain asserts carry a context message (`assert x, f"..."`). Section separators look like `# --- name ---...`. Lines are at most 120 characters (ruff). Under tests/** ruff ignores S105-S108, S113, S310, S324, B011, B017 and ASYNC.
- Settings:
  - Use `Settings(_env_file=None, **overrides)` for pure tests; .env is never read.
  - Use the `app_env` fixture when a DB, storage or the app is needed. It reads the forced env plus .env, and gives a fresh SQLite DB and DATA_DIR per test.
  - Change a setting with `app_env.model_copy(update={...})`. In provider tests, use the `make_settings` fixture.
- Async: asyncio_mode=auto, so `async def test_...` needs no marker. Sync tests call `asyncio.run(...)` for engine and job entry points.
- Markers:
  - `slow`: anything that launches Chromium, ffmpeg, Manim, uvicorn or subprocesses doing real work.
  - `container`: anything the Docker test image must prove on Debian's ffmpeg 5.1 plus the pinned headless shell; always together with `slow`.
  - Module-level `pytestmark = pytest.mark.slow` or `[pytest.mark.slow, pytest.mark.container]`.
  - Network tests are opt-in through `skipif(os.environ.get("AADHI_NETWORK_TESTS") != "1")`.
- Missing tools skip, not fail: helpers.tools() and need_chromium() (tests/compose/dynamic/helpers.py:68-87), need_real_stage() (tests/determinism/conftest.py:39-42). The exception is test_ffmpeg_features.py:53-56, which fails when AADHI_EXPECT_FFMPEG is set.
- Offline fakes:
  - Production fakes are forced by env (LLM/TTS/IMAGE/VIDEO_PROVIDER=fake).
  - tests/pipeline/fakes.install_providers(monkeypatch) patches aadhi.pipeline.integrations seams (ScriptedLLM, FakeTTS, FakeImage, FakeVideo, FakeGif, FakeRenderer).
  - tests/providers/fakes.py and media_fakes.py are SDK stand-ins; respx mocks HTTP transports.
  - tests/api/fakes only replace modules that are missing.
- Stub servers are `http.server.ThreadingHTTPServer` on 127.0.0.1:0 in a daemon thread, used as context managers that yield (base, state). The state records request paths, so a test can prove what never reached the server.
- There are two stub stages:
  - tests/compose/dynamic/stub_stage: a pure (timeline, t) picture with config-driven faults for preflight, parity and the determinism harness.
  - tests/compose/motion/stub_stage: a frame-number barcode plus media quadrant, for engine frame exactness.
- The real stage is exercised only through tests/determinism (`real_app`, module scope) and tests/api/test_render_dynamic_route.py (`live_app`).
- Fixture timelines:
  - For stage and QA: `helpers.dynamic_timeline(fmt)`, i.e. evals/render_parity.dynamic_timeline, loaded by path.
  - For engine-only work: `tests/compose/motion/fixtures.dynamic_timeline(durations, media_scene=, quiz_scene=, size=, fmt=)`.
  - For the timeline builder: tests/compose/factories.screenplay_dict plus a `presentation` from tests/schemas/v3_fixtures.PRESENTATION.
- Synthetic media is deterministic: make_wav, make_bits_video (H.264 with B-frames), jpeg_frames with a barcode, Pillow PNGs.
- Every pass/fail check comes with a negative control:
  - the stub with Math.random jitter must be caught;
  - a render shifted by 30 frames must fail parity;
  - a mux without negative_cts_offsets must start late;
  - an untagged encode must fail the colour check.
- Determinism hashes (SHA-256 of the CDP JPEG at q92) are compared only within one machine; frames are never compared against stored hashes from another platform.
- Tolerances are named constants at the top of the module (SSIM_MIN, JOIN_SSIM_FLOOR, ...).
- Property tests use parametrize over seeds with `random.Random(seed)` (test_chunks.py:89).
- Code-reality scans (regex over web/js/dynamic) guard the Python/JS contract (test_chunks.py:197-213).
- Generated artefacts carry a currency test plus a regeneration command: python -m aadhi.schemas.generate; python -m tests.schemas.v3_fixtures; AADHI_UPDATE_GOLDENS=1; scripts/gen_env_example.py (.env.example); scripts/lock_requirements.py.
- Windows: subprocesses pass creationflags=CREATE_NO_WINDOW and env=subprocess_env(), and text files are written with newline="\n".

## dynamic_status
B0 (inert schema, gates, asset auth, transform survival, invariance, ffmpeg 5.1 checks, pricing, batch-4 regressions)
- Fully tested in this slice: tests/schemas/test_v3_schema.py, tests/core/test_transform_survival.py, tests/core/test_assets_auth.py, tests/api/test_schema_v3_api.py, tests/api/test_asset_auth_dynamic.py, tests/jobs/test_newer_document.py, tests/core/test_legacy_v3_gate.py, tests/invariance/*, tests/compose/test_ffmpeg_features.py, tests/compose/test_loudness.py, tests/compose/test_format_plumbing.py, tests/usage/*, tests/api|pipeline/test_batch4_regressions.py.

B1
- Every Python module of aadhi/compose/dynamic (colour, container, determinism, preflight, stage_page) and aadhi/compose/motion (cache, chunks, encode, engine, job, media, resources, stage) has dedicated tests. So do aadhi/compose/presentation.py and /render-dynamic.

B1 test requirements from design §10.3 and where they are covered:
1. Determinism 1x300 / 3x100 / reverse:
   - real stage, both formats (slow + container): tests/determinism/test_frame_hashes.py:340-356 test_real_stage_frames_are_identical_in_any_order;
   - harness on the stub, with a non-deterministic negative: :317-334;
   - plans: :256-264. 3x100 runs three pages concurrently (aadhi/compose/dynamic/determinism.py:88-108).
2. Parity SSIM ≥ 0.98 at 20 instants:
   - tests/determinism/test_render_acceptance.py:241-244 test_live_player_matches_the_mp4 (real engine, both formats, seeded 20 frames);
   - compositor-only ≥ 0.99: :234-238;
   - stub harness with negative control: tests/compose/dynamic/test_render_parity_dynamic.py:62-85;
   - pixel-equal live CSS: tests/determinism/test_live_css.py:108-127.
3. Easing ≤ 1e-4: NOT in Python. Covered by web/tests/motion/easing.test.js:39-50 (JS).
4. Grapheme splitting Tamil/Devanagari/Telugu:
   - JS: web/tests/motion/text.test.js:17-60 (also Kannada).
   - Python covers cluster-safe 9:16 caption splitting for ta/hi/te/ml/en: tests/compose/test_dynamic_presentation.py:297-311. Danda font loading: test_frame_hashes.py:416-436. Tamil title fitting: test_stage_layout.py:126-133.
5. Exact frame count:
   - test_render_acceptance.py:199-216 (366 frames, every chunk);
   - test_engine_slow.py:300-340 (barcode frames == range(285));
   - test_container.py:182-195 and 222-242 (short-chunk remedy);
   - test_ffmpeg_features.py:162-177;
   - test_chunks.py:115-121.
6. First video PTS 0 and A/V start within one frame:
   - test_ffmpeg_features.py:180-224 (raw -ignore_editlist, plus a control);
   - test_container.py:182-217 and 247-253 (late audio -> av_start remux);
   - test_engine_slow.py:320;
   - test_render_acceptance.py:206-207;
   - argv pinned in test_encode.py:180-192.
7. Guard trips on a stray timer:
   - real stage: test_frame_hashes.py:359-381 and tests/api/test_render_dynamic_route.py:305-311;
   - stub: tests/compose/dynamic/test_stage_page.py:119-127;
   - classification: test_stage_calls.py:30-35.
8. W from fake cgroup files: tests/compose/motion/test_resources.py:240-315 and tests/determinism/test_engine_resources.py:118-149.
9. Slot locking across processes: test_resources.py:361-401 and test_engine_resources.py:185-219 (4-process contention, killed holder).
10. Decoded-frame continuity at chunk joins:
    - test_render_acceptance.py:258-275 (real stage, SSIM neighbour test);
    - test_engine_slow.py:300-312 (barcode, 6 joins), 343-349 (cache reuse), 367-372 (page recycling);
    - test_media_rate_slow.py:58-73 (media frames independent of chunk start).
- Also covered:
  - the source-scan side: test_chunks.py:265-281 (Python) and web/tests/dynamic/sourceScan.test.js (JS);
  - preflight framework: test_preflight_checks.py, test_preflight_stage.py, test_render_acceptance.py:278-283;
  - engine switch and the DYNAMIC_ENABLED kill switch: tests/compose/motion/test_job.py, tests/api/test_dynamic_flag_serve.py.
- Not tested: the benchmark harness scripts/bench_motion.py (only mentioned at test_engine_slow.py:7) and the compose `motion` profile. Only the cgroup arithmetic of the 8-CPU profile is covered (test_resources.py:297; test_engine_resources.py:40).
- Manual or owner items stay open per the design (side-by-side review, container benchmark, first CI container run).

B2 / B3
- No Python test references themes, textfit, zones, kinetic, direct, events, gallery, cues, personas, casting, history, music providers or mix (grep over tests/ is empty).
- B2 already exists as placeholders or stand-ins in tests:
  - preflight CRITICAL_BANDS "until B2's zones.py" (aadhi/compose/dynamic/preflight.py:73-78);
  - evals/render_parity.py:501-533 `_kinetic_chunks` "a stand-in for B2's kinetic.py";
  - aadhi/compose/presentation.py FAMILIES / content_tokens / structure_tokens: the B1 minimal ResolvedLook, used by tests and by the parity eval at :540-548;
  - test_dynamic_presentation.py:157-173: unreleased templates (formula_focus) fall back to board_generic.
- B3 groundwork:
  - only B0 pricing and billing tests (tests/usage/test_music_and_fail_closed.py; test_billing_coverage.py, whose GenerationOptions.voice/.music are duck-typed SimpleNamespace because the fields do not exist yet);
  - schema fixtures carrying voices, calibration and music (tests/schemas/v3_fixtures.py:38-50);
  - FIELD_RULES for music and narration_upload;
  - the B1 master-audio chain (classic bgm sidechain) pinned in test_encode.py:164-177.

Divergences between tests and the design doc:
- (a) Design §7.5 has no -threads; tests pin `-threads 4` (test_encode.py:49-52). This is a recorded deviation.
- (b) The design lists "determinism ... in Docker". All determinism tests are marked slow; only test_frame_hashes is also `container`, but the CI container job's step 4 (`slow and not network and not container`) also runs inside Docker, so the whole suite does run there.
- (c) The design's B1 Python-facing test list includes easing and grapheme splitting; those live in JS, not Python.
- (d) The parity fixture uses caption_style "bold_outline" for portrait (evals/render_parity.py:568), a B2 caption style per the design's B2 scope.
- (e) The design B1 status says 12 of 168 9:16 chunks are under 1.0 s; test_dynamic_presentation.py:345 pins "≤ 3 under 0.7 s and ≤ 12 under 1.0 s, min ≥ 0.6", consistent with that.
- (f) The design §10.2 "Docker test job passes ... from B1 the determinism suite": test_ci.py:115-133 checks only the marker expressions, not that tests/determinism is selected.

## gaps_todos
- No TODO/FIXME/XXX in tests/** (grep).
- Dead "not built yet" guards left from parallel development:
  - tests/api/test_render_dynamic_route.py:266-269 (ImportError fallback for aadhi.compose.motion.chunks, which now exists);
  - tests/determinism/test_engine_resources.py:103 (`pytest.importorskip("aadhi.compose.motion.resources")`);
  - tests/determinism/test_render_acceptance.py:187 (importorskip engine);
  - tests/determinism/conftest.py:39-42 (skip while the stage bundle is absent; it is present now);
  - tests/api/test_main.py:188, tests/api/test_version_actions.py:152, tests/pipeline/test_fake_content.py:96, tests/pipeline/test_orchestrator.py:428 (modules "not available yet").
- tests/determinism/test_frame_hashes.py:284-285: `settings_for(base)` ignores its argument. Cosmetic double blank lines at 314-316.
- The `container` marker is registered three times (pyproject.toml, tests/compose/conftest.py:15-19, tests/determinism/conftest.py:28-29); `slow` twice (tests/conftest.py:90-91). Harmless.
- "Maximal" fixtures are maintained by hand. Docstrings claim tests/schemas/fixtures/screenplay_v3_maximal.json and tests/core/fixtures/transform_survival_dynamic.json use every v3 field and enum value, but tests only check that every FEATURE is used (test_v3_schema.py:150-151; test_transform_survival.py:228-229, 244). No test iterates V3_FIELDS/V3_VALUES against the fixtures, and the classic projection lists (_V3_SCENE_FIELDS/_V3_BEAT_FIELDS/_V3_ITEM_FIELDS, test_transform_survival.py:70-73) are hand-kept.
- No Python tests yet for any B2 deliverable: themes.py/themes.json validator; fonts and the WOFF2 check; icon index; textfit; zones; kinetic; direct; events; cue limits; SFX placement; catalogue parity; gallery goldens.
- No Python tests yet for any B3 deliverable: personas, casting, history, calibration, Azure/OpenAI/ElevenLabs v4 adapters, music providers (procedural/upload/lyria/fake), pipeline/music.py, mix.py, voice_sample/calibration assets, new credential providers.
- No test for scripts/bench_motion.py or the `motion` compose profile.
- test_billing_coverage.py duck-types GenerationOptions.voice/.music (lines 1-6) and must be rewritten against the real models when B3/B4 add them.

## bugs_or_risks
1. **The blocking container job can pass green while skipping the B1 suites.**
   - The Dockerfile test stage sets AADHI_CONTAINER_TESTS=1 (Dockerfile:132), and test_ci.py:72 asserts that it is set, but nothing reads it (a grep over *.py, *.yml, *.toml, *.md and Dockerfile finds only those two sites plus the design doc).
   - Only test_ffmpeg_features.py:53-56 turns a missing tool into a failure (via AADHI_EXPECT_FFMPEG). Every other container or slow Dynamic test skips instead:
     - tests/compose/dynamic/helpers.py:68-74 tools(), :84-87 need_chromium();
     - tests/compose/motion/test_engine_slow.py:244-251 (ffmpeg fixture skips if ffmpeg or Chromium is missing);
     - tests/determinism/conftest.py:39-42 (skips if the stage bundle is missing).
   - So a test image without Playwright's Chromium would report the determinism, parity and engine container tests as skipped, not failed.
2. **A developer .env can flip motion and Dynamic settings for app_env tests.**
   - tests/conftest.py docstring (lines 3-4) promises a developer .env cannot affect tests. But _FORCED_ENV (lines 32-84) forces DYNAMIC_FEATURES and omits DYNAMIC_ENABLED, RENDER_MOTION_WORKERS, RENDER_CPU_SLOTS, RENDER_SLOT_DIR, MOTION_X264_PRESET and MOTION_X264_CRF.
   - Settings reads ROOT_DIR/.env (aadhi/config.py:67-68), and app_env uses get_settings().
   - tests/compose/motion/test_job.py:107 asserts `app_env.dynamic_enabled is False`, so DYNAMIC_ENABLED=true in a developer .env would fail it. Today's .env has no such lines (checked).
   - B3 must also add any new provider keys (Azure, ElevenLabs v4, Lyria/music) and a fake MUSIC provider to _FORCED_ENV.
3. **The `network` marker is declared but never used.**
   - pyproject.toml declares it, yet no test applies `pytest.mark.network` (grep). tests/providers/test_network.py:12-16 uses skipif plus slow instead.
   - The CI selections `-m "... and not network"` (test_ci.py:127-129) therefore filter nothing. Network tests are excluded only by the env check, so CI with AADHI_NETWORK_TESTS=1 would run them inside the "slow" step.
4. **Safe-zone rectangles are hard-coded in two places.**
   - tests/api/test_render_dynamic_route.py:278-285 duplicates the zone numbers from aadhi/compose/dynamic/preflight.py:75-78.
   - When B2's zones.py becomes the source of truth, the copies can drift silently. Tests should import from zones.py.
5. **The test-only engine stand-in hard-codes design tokens.**
   - evals/render_parity.py:442-447 has CONTENT_TOKENS and LOOK_TOKENS literally, and _family_tokens (:540-548) imports aadhi.compose.presentation.FAMILIES, content_tokens and structure_tokens.
   - B2 moving families into compose/dynamic/themes.py must keep that API or update the eval. test_live_css.py, test_render_acceptance.py and test_render_parity_dynamic.py all depend on it.
6. **Some engine renders write outside their own tmp_path.**
   - tests/compose/motion/test_engine_slow.py:261-266 creates `tmp.parent / "web"`.
   - For tests that pass tmp_path directly, this is pytest's shared basetemp directory, not the test's own tmp_path. It is harmless serially but would collide under parallel workers.
7. **tests/determinism/conftest.py:59-80 changes process-wide state for a whole module.**
   - real_app uses a module-scoped pytest.MonkeyPatch and clears the global settings, engine and storage caches.
   - Any function-scoped `app_env` test placed in a tests/determinism module would interleave with it and could reset the real app's engine cache mid-module. No such test exists today; it is a trap for new B2 gallery tests.
8. **Expected test changes, not bugs.**
   - test_dynamic_presentation.py:157-173 asserts that "formula_focus" falls back to board_generic; it will fail on purpose when B2 releases that template.
   - test_encode.py:164-177 pins the B1 classic BGM sidechain master chain, which B3's mix.py will replace.
   - Adding GenerationOptions.voice/.music without exclude-at-default changes tests/invariance/goldens/_api/generation_options.json (test_goldens.py:326-330). That requires a declared classic change.

## b2_b3_touchpoints
B2 (visual system) hooks into the existing tests at these points:
- web/js/dynamic templates and themes reading new `timeline.X` fields must add them to STAGE_TIMELINE_FIELDS (aadhi/compose/motion/chunks.py:47). This is enforced by tests/compose/motion/test_chunks.py:265-281.
- Any new per-scene input to the picture must enter chunk_slices and chunk_key; follow the pattern in test_chunks.py:202-262. Fonts, icons and themes.json served under web/ change bundle_digest (test_chunks.py:293-307).
- zones.py should replace preflight.CRITICAL_BANDS and CAPTION_BANDS (aadhi/compose/dynamic/preflight.py:73-79, comment "until B2's zones.py"). Update or derive the hard-coded copies in tests/compose/dynamic/test_preflight_checks.py:69-129 and tests/api/test_render_dynamic_route.py:278-285.
- R-29 and R-53 thresholds are pinned in test_preflight_checks.py:131-180; textfit.py min sizes feed the `min` box field and plan_remedies (:237-251).
- kinetic.py replaces the stand-in evals/render_parity.py:501-533 `_kinetic_chunks` and aadhi.compose.presentation.portrait_caption_cues.
  - Existing pins: tests/compose/test_dynamic_presentation.py:245-375 (cluster-safe splits for ta/hi/te/ml/en, word timing, ≤ 12 chunks under 1.0 s, no orphans) and test_render_parity_dynamic.py:53-59 (≤ 32 chars, ≤ 2 lines).
  - Preflight samples kinetic_captions starts: test_preflight_checks.py:298-321; test_preflight_stage.py:85-93.
- themes.py (§4.7, 4 families) must stay compatible with presentation.FAMILIES, content_tokens, structure_tokens, accent_hue and family_for:
  - tests: tests/compose/test_dynamic_presentation.py:90-136, 191-234;
  - eval: evals/render_parity.py:540-548.
  - Live-CSS parity covers only "dark" and "paper_ink" (tests/determinism/test_live_css.py:108-109); extend it to all 4 R1 families.
- direct.py (template selection) changes test_dynamic_presentation.py:108-173. Line 167 expects unreleased templates to fall back to board_generic; transitions are pinned to presentation.TRANSITIONS=("cut","crossfade") at aadhi/compose/presentation.py:38. Property tests should follow test_chunks.py:157-176 (seeded random lessons).
- New templates, backgrounds and transitions need:
  - determinism coverage: extend `standard_plans` runs in tests/determinism/test_frame_hashes.py:340-356 with timelines using each template;
  - layout checks: the `_boxes` pattern in tests/determinism/test_stage_layout.py:354-361;
  - parity: tests/determinism/test_render_acceptance.py:185-196. Its fixture is evals/render_parity.py:550-593, all scenes board_generic.
  - Gallery golden frames would be new `slow` + `container` tests using `real_app` plus open_stage. Hashes must be compared within one platform only.
- Stub stages must follow any stage-contract change, e.g. new textBoxes fields: tests/compose/dynamic/stub_stage/stage.js:134-161 and render.js:76-84; tests/compose/motion/stub_stage/stage-stub.js:189-211.
- New SceneLook or Presentation fields (tests/schemas/test_v3_schema.py:41-90; regenerate web schemas at :465-472) also need:
  - the maximal fixtures (tests/schemas/v3_fixtures.py:18-191; tests/core/fixtures/transform_survival_dynamic.json);
  - transform-survival projections (tests/core/test_transform_survival.py:70-83).
- New settings regenerate .env.example (tests/evals/test_tooling.py:82-117). Vendored fonts and icons via npm keep package-lock in sync (test_tooling.py:663-676) and the Dockerfile allow-list (:359-376).

B3 (audio) hooks:
- The tests/conftest.py:32-84 _FORCED_ENV block must blank the new keys (Azure, ElevenLabs v4, Lyria/music) and force a fake music provider. Provider tests use tests/providers/conftest.py make_settings.
- Personas and casting extend the voice catalogue tests in tests/providers/test_voices_factory.py:22-153; adapters follow tests/providers/test_tts_adapters.py (mocked transports, slow); the fake follows tests/providers/test_tts_fake.py.
- Schema contracts already in the fixtures:
  - PRESENTATION.voices {narrator, aadhi, student, calibration{natural_wpm, control}} and music {source, provider, mood, asset_key, mode, level} (tests/schemas/v3_fixtures.py:38-50);
  - manifest v3 fields lufs, speaker_gains, persona, words_exact and MusicAsset (tests/schemas/test_v3_schema.py:406-422);
  - V3_ONLY_MODELS includes VoiceCast, VoiceCalibration, MusicChoice, MusicPlan, MusicCue, SfxCue, MusicAsset (:47-52).
- Asset kinds and auth: Presentation.music.asset_key and Beat.voice_override_asset_key rules (tests/core/test_assets_auth.py:123-135); voice_sample public and calibration private (:148-162). Any new key field must join FIELD_RULES (:292-312).
- Pricing and budget: tests/usage/test_music_and_fail_closed.py:69-134 and tests/usage/test_billing_coverage.py:37-95. The latter duck-types opts.voice/opts.music; switch it to the real GenerationOptions fields. Keep them excluded at default or regenerate tests/invariance/goldens/_api/generation_options.json under a declared classic change (tests/invariance/test_goldens.py:326-330).
- mix.py replaces or extends the master chain pinned at tests/compose/motion/test_encode.py:133-177. Pins there:
  - scene_audio_spec equal to the classic mix;
  - master_audio_command: AAC 384k, sidechaincompress BGM, bgm_volume.
- Engine audio RMS checks: tests/compose/motion/test_engine_slow.py:325-332.
- Loudness: use aadhi.compose.loudness (tests/compose/test_loudness.py) and the container loudness remedy (tests/compose/dynamic/test_container.py:275-291, LoudnessTarget(-16.0, -1.5, 9.0)). Mix ±1 LU tests should be slow + container.
- SFX (R-49) builds on aadhi.compose.sounds (tests/compose/test_sounds.py).
- "Baked gains change the scene-audio key but not beat keys" must keep classic scene_hashes goldens unchanged (tests/invariance/goldens/*/scene_hashes.json; test_v3_schema.py:428-448 shows the scene_hash exclusion pattern).
- New dependencies (scipy for procedural music; an Azure SDK) must be pinned in requirements.lock, including the Linux closure (tests/evals/test_tooling.py:156-225).

## files
- tests/conftest.py (157): Global isolation before any aadhi import: forced env, session temp dir, slow marker, app_env/db_session/asset_store/job_ctx fixtures || _FORCED_ENV L32-84 (fake providers, blank keys, DYNAMIC_FEATURES='', ALLOW_FAKE_PROVIDERS=false, STORED_API_KEYS_ENABLED=false, RENDER_WORK_DIR='', SCRATCH_DIR); SESSION_TEMP L25; pytest_configure L90 (slow only); pytest_sessionfinish L94 removes session dir only on pass; app_env L100-129 (per-test DATA_DIR + sqlite, caches reset, create_all); db_session L132; asset_store L143; job_ctx L152 -> FakeJobContext
- tests/helpers.py (79): FakeJobContext implementing the JobContext protocol in memory || FakeJobContext L19-79: events list, usages, record_usage prices via aadhi.usage.pricing and enforces budget_usd (BudgetExceeded), check_cancelled -> JobCancelled, session() -> session_scope(), assert_lease no-op, ensure_budget
- pyproject.toml (0): pytest config (testpaths, addopts -p no:cacheprovider -ra, asyncio_mode auto, markers slow/network/container) and ruff (line-length 120, tests ignore S105-S108, S113, S310, S324, B011, B017, ASYNC) || [tool.pytest.ini_options] markers; [tool.ruff.lint.per-file-ignores] tests/**
- tests/compose/dynamic/helpers.py (214): Shared Dynamic QA helpers: loads evals/render_parity.py and evals/video_qa.py by path, Dynamic fixture timelines, tool/Chromium skips, the §7.5 reference encoder, barcode frames, reference_render || CHUNK_VF L30; chunk_encode_args L34; parity_eval L41; video_qa L51; settings() L60 = Settings(_env_file=None); dynamic_timeline(fmt) L64; tools() L68 skips if ffmpeg missing; need_chromium L84; encode_chunk L109; join_and_mux L117 (negative_cts / audio_delay switches for broken files); jpeg_frames + draw_barcode/read_barcode L140-178 (12-bit); decoded_numbers L181; reference_render L189 (open_stage + CDP JPEG + chunked encode, shift for negative tests)
- tests/compose/dynamic/stage_server.py (84): Loopback server for the dynamic stub stage (/render-dynamic, /web/js/dynamic/{render,stage}.js, /stub-config.js, Bearer-checked /api/render/timeline) || serve_stage(token, timeline, config) L70 yields (base, StageServerState{requests, timeline_requests})
- tests/compose/dynamic/stub_stage/stage.js (171): Pure-function stub of createDynamicStage with injectable faults (overflow/unsafe/contrast), honoured remedies (shrink/safe/scrim), random jitter and strayTimer || createDynamicStage L24; textBoxes L155 (box schema x,y,w,h,role,critical,slot,ink,bg,overflow,scene,size,weight,min=36,text); setLayers L162; seek returns {key}
- tests/compose/dynamic/stub_stage/render.js (57): Stub window.aadhiStage contract v3: token from fragment, Bearer fetch, guard installed after ready ('aadhi-guard:' errors), bootError/throwAtBoot/version faults || installGuard L42; window.aadhiStage L76 {version, ready, info, mediaPlan:[] , seek, textBoxes, setLayers}
- tests/compose/dynamic/test_colour.py (87): CSS colour parsing (hex/rgb/hsl/color()/oklch/oklab/display-p3) and WCAG contrast || test_parse_colour L38, test_unreadable_colours_are_none L44, test_oklch_and_oklab_match_srgb L48, test_wcag_contrast_reference_values L62 (#0FE3FE on #1B1F3B > 8:1), test_text_contrast_composites_ink_and_refuses_translucent_backgrounds L74
- tests/compose/dynamic/test_container.py (291): Post-render container checks and remedy loop (§7.12): pure remedy ordering + QA document, then real MPEG-TS/MP4 muxes (slow+container) || CLASSIC_KEYS L31 (API RenderSummary.options.qa); test_grid_step L38; FakeRemedies L49; test_verify_container_applies_each_remedy_once_in_order L78; test_a_problem_that_survives_its_remedy_is_an_engine_bug L100 (code render_invalid); test_motion_qa_document L110 (starts{video,video_raw,audio,av_offset}, engine motion, schema QA_SCHEMA); test_record_qa_merges_into_the_render_options L132; test_a_correct_mux_passes_every_check L182 (exact frames, PTS 0 raw and edited, av<1/30, BT.709 tv); test_without_negative_cts_offsets_the_start_is_late_and_a_remux_fixes_it L200; test_a_short_chunk_is_rendered_again L222; test_late_audio_wrong_tags_missing_tracks L247; test_loudness_target L277
- tests/compose/dynamic/test_preflight_checks.py (423): Browser-free preflight: TextBox parsing, R-59 zones, burned-caption band, R-29 type floor, R-53 contrast thresholds, overflow, remedies, sampling, StageInfo, finalise statuses, result schema 1 || INFO L34 (366 frames, scenes 0-140-279-366); test_landscape_critical_zone L78; test_portrait_critical_zone L92; test_burned_captions_take_their_band... L109; test_type_floor_warns_below_the_r29_sizes_and_weights L131; test_contrast_thresholds L155 (caption 7.0, formula 4.5, >=32px or >=24px@700 3.0, else 4.5); test_overflow_findings L186; test_plan_remedies L237 (9:16 shrink floor 40.0); test_apply_remedies_writes_the_look L253; test_sample_frames_landscape L282 (24 spread + scene bounds + beat settle frames); test_sample_frames_portrait_adds_caption_chunk_starts L298; test_result_summary_schema L391 (exact key set, checks text_overflow/safe_zones/contrast/type_floor); test_raise_if_blocked L416 (preflight_blocked)
- tests/compose/dynamic/test_preflight_stage.py (100): Preflight end to end in Chromium on the stub stage (slow): one-round pass, remedies + reload, block/warn, portrait chunk sampling, classic refused || test_clean_timeline_passes_in_one_round L32; test_remedies_fix_what_the_stage_honours L49; test_unfixed_problems_block_or_warn L68; test_portrait_samples_caption_chunks_and_checks_the_reel_zones L85; test_classic_timelines_are_refused L96
- tests/compose/dynamic/test_render_parity_dynamic.py (85): Dynamic part of evals/render_parity.py: SSIM properties, seeded 20 instants, fixture shape, stub-stage live vs MP4 parity with a late-render negative control || test_ssim_properties L17; test_sample_frames_are_seeded L34; test_dynamic_fixture_timeline L44 (12.2 s, board_generic, mesh_drift/colour_block, cut then crossfades; portrait kinetic chunks <=32 chars, <=2 lines); test_live_stage_vs_mp4_on_the_stub_stage L63 (slow; worst_ssim>=0.98, stage_ssim>=0.999, shift=30 fails)
- tests/compose/dynamic/test_stage_calls.py (110): StagePage._call/_send error classification with fake page/CDP: guard/page-code errors final, crashes retryable, capture failures retryable || test_an_error_of_the_page_code_is_final L30 (stage_call_failed, guard_trips recorded); test_a_crashed_page_may_be_retried L38 (stage_crashed); test_a_capture_on_a_dead_renderer_is_a_retryable_crash L82; test_any_other_capture_failure_is_retryable_too L91 (stage_capture_failed); test_the_engine_retries_a_chunk_whose_capture_crashed L104
- tests/compose/dynamic/test_stage_page.py (160): stage_page driver in Chromium against the stub (slow): token fragment, worker-served timeline, network policy, boot/contract errors, stray-timer guard, caption-only alpha layer || test_stage_url_keeps_the_token_in_the_fragment L34; test_page_fetches_the_timeline_with_the_token L40; test_worker_serves_the_timeline_and_media_frames L54; test_network_policy_blocks_other_origins L75; test_boot_failures L90 (render_page_<code>); test_a_script_error_during_boot_fails_fast L99; test_contract_version_is_checked L110 (stage_contract); test_a_stray_timer_trips_the_guard_and_is_reported L119; test_caption_layer_is_captured_with_alpha L130; test_a_refused_timeline_is_not_retried L145; test_a_missing_render_page_fails_at_once L155
- tests/compose/motion/fixtures.py (155): Motion-engine fixtures: minimal Dynamic presentation/look, dynamic_timeline(durations, ...), synthetic wav, 16-bit barcode video (B-frames), gray frame decoding, SimpleCtx || AUDIO_KEY/CLIP_KEY/TOKEN L20-22; presentation L35; look L42 (board_generic, mesh_drift/colour_block alternate, cut then crossfade 0.4); dynamic_timeline L49 (media_scene, quiz_scene, captions, size, fmt); make_wav L80; bits_image L91; make_bits_video L103; gray_frames L114; decode_bits L124; SimpleCtx L135
- tests/compose/motion/stage_server.py (103): Stub stage server for the engine (records every request to prove /__frames never reaches the server); optional web_dir to serve the real stage || serve_stage(token, timeline, config, web_dir) L94; _Server.request_queue_size=128 L90
- tests/compose/motion/stub_stage/stage-stub.js (191): Engine stub stage: top quarter = 16-bit frame number, scene colour band, media frame quadrant from /__frames/<key>/<n:06d>.jpg, caption quadrant white; modes bad_info/bad_version/boot_error/bad_plan/foreign_media || srcIndex L101 floor((t-start+trim_in)*srcFps) % total; framesKey L111 v-<12>-WxH; mediaPlan L117; seek L143
- tests/compose/motion/test_cache.py (68): ChunkCache store/cached/adopt/discard, key validation, workspace-scoped reuse, retryable chunk failures || test_store_cached_adopt_discard L20 (marker {frames, bytes, keys}); test_keys_are_validated L40; test_cache_lives_in_the_render_workspace L47; test_which_chunk_failures_are_captured_again L61
- tests/compose/motion/test_chunks.py (239): Scene frame bounds = classic rounding, chunk planner (scene-aligned, even splits, max 600), random-lesson property test, chunk key coverage, STAGE_TIMELINE_FIELDS source scan, bundle digest || test_scene_frame_bounds_match_the_classic_rounding L47; test_plan_groups_short_scenes_into_scene_aligned_chunks L56; test_plan_splits_long_scenes_evenly L67; test_plan_properties_on_random_lessons L89 (40 seeds); test_chunk_key_covers_its_scenes_the_outgoing_scene_and_the_setup L134; test_chunk_key_covers_presentation_and_captions_in_range L163; test_chunk_key_covers_the_chapter_title_drawn_as_the_kicker L179; test_every_timeline_field_the_stage_reads_is_in_the_key L197 (regex scan of web/js/dynamic/**.js for timeline.X); test_media_keys_found_in_slices L216; test_bundle_digest_follows_the_stage_files L225
- tests/compose/motion/test_encode.py (198): Pins the motion ffmpeg command lines: chunk encoder = §7.5 + -threads 4, scaling, frame runs, extraction seek half a frame early, native-rate mapping, classic-equivalent scene audio, master AAC 384k, final mux || DESIGN_VF/DESIGN_ENCODE L34-38; test_chunk_encoder_is_the_design_command L45; test_chunk_encoder_settings_and_scaling L57; test_frame_runs L71; test_extract_args_seek_half_a_frame_early L78; test_native_frames_are_a_pure_function_of_the_page_index L107; test_scene_audio_matches_the_classic_mix L133; test_master_audio_is_encoded_once_aac_48k_stereo_384k L164 (bgm sidechaincompress, stream_loop); test_final_mux_starts_at_zero_with_chapters_and_the_caption_track L180 (+faststart+negative_cts_offsets, -c:v copy, -c:a copy, mov_text)
- tests/compose/motion/test_engine_slow.py (251): render_motion end to end on the stub stage with real Chromium + ffmpeg (slow; two also container): barcode-exact frames across joins, media frames, soft vs burned captions, cache reuse, recycling, presigned refresh, contract errors, missing assets, failed audio master, cancel || _settings L261 (fixed stand-in web_dir so bundle digest is stable); test_landscape_render_is_frame_exact_across_chunk_joins L300 (285 frames, 6 chunks, numbers==range, container QA, audio RMS); test_a_retry_reuses_the_cached_chunks L343; test_served_timeline_portrait_and_burned_captions L356; test_page_recycling_keeps_every_frame L367; test_presigned_links_are_signed_again_for_every_page L375; test_contract_violations_fail_without_retry L400; test_missing_clip_fails_before_capture L408; test_missing_narration_fails_before_capture L415; test_a_failed_audio_master_stops_the_capture L425; test_cancel_stops_the_capture L436
- tests/compose/motion/test_job.py (197): render_video engine switch (classic vs motion), caption_modes, dynamic warnings, DYNAMIC_ENABLED gate, full job against stub (slow) with QA stored, preflight block before capture || test_caption_modes L40; test_panel_warnings_say_what_the_dynamic_video_does L52; test_classic_timeline_never_reaches_the_motion_engine L64; test_dynamic_timeline_takes_the_motion_engine L83; test_dynamic_render_needs_the_admin_flag L103 (dynamic_disabled; asserts app_env.dynamic_enabled is False); test_dynamic_job_renders_stores_and_records_qa L134; test_preflight_block_stops_before_capture L168
- tests/compose/motion/test_media.py (140): mediaPlan validation/merge/limits, /__frames path parsing and registry, media file resolution, StageOptions flags/fingerprint, scene_mismatch, hashable_timeline strips presigned URLs || test_parse_media_plan_validates_and_merges L229; test_parse_media_plan_rejects_contract_violations L247; test_frames_key_and_source_asset L252; test_frame_registry_paths L275; test_frame_registry_serves_only_the_current_chunk L287; test_stage_options_flags_and_fingerprint L309 (no --run-all-compositor-stages-before-draw, capture quality 92); test_timeline_helpers L326
- tests/compose/motion/test_media_rate_slow.py (73): 24 fps clip on the 30 fps grid: extracted frame per page index is independent of chunk plan/run start (slow) || test_resampled_clip_frames_do_not_depend_on_the_run_start L58
- tests/compose/motion/test_refresh.py (134): Presigned links re-signed on every page load, remedies kept, refresh failure retryable || test_every_page_load_serves_freshly_signed_links L131; test_a_failed_refresh_is_a_retryable_stage_error L163 (stage_refresh_failed); test_render_job_refresher_signs_again_and_keeps_the_remedies L176
- tests/compose/motion/test_resources.py (194): cgroup v1/v2 quota parsing from fake files, auto W formula, overrides, slot dir, CpuSlots in-process and across processes, acquire wait/cancel || test_parse_cpu_max_and_cfs L240; test_cgroup_v2_quota_tightest_limit_from_own_cgroup_upwards L256; test_cgroup_v1_quota L278; test_auto_workers L304 (MAX_AUTO_WORKERS=6 cases); test_worker_override_and_slot_count L308; test_slots_in_one_process L324 (slot-000.lock...); test_slots_are_shared_across_processes L361; test_acquire_waits_for_a_free_slot_and_honours_cancel L377
- tests/compose/test_dynamic_presentation.py (375): B1 presentation/look resolution (aadhi/compose/presentation.py) behind DYNAMIC_ENABLED; classic byte-identical; 9:16 cluster-safe phrase captions (ta/hi/te/ml/en) and no-flash grouping on the showcase || test_classic_timelines_are_byte_identical_with_the_flag_on L70; test_presentation_is_resolved L90 (content keys, fonts outfit/inter/jetbrains_mono, DYNAMIC_FPS 30, schema_version 3, intro None); test_looks_of_every_scene L108 (tokens struct.accent/bg/bg-alt/bg-tint/surface; colour_block for chapter cards; R-23/R-54 transitions; 3 accents); test_the_teachers_look_is_honoured_within_what_b1_draws L157 (formula_focus -> board_generic); test_portrait_reels_get_short_phrase_captions L176; test_content_inks_hold_contrast_on_every_background_tone L216 (ink>=7, ink2>=4.5); test_narrow_captions_split_long_words_between_clusters_and_keep_word_timing L298; test_portrait_captions_never_flash_by L336 (min>=0.6, <=3 under 0.7, <=12 under 1.0)
- tests/compose/test_ffmpeg_features.py (266): ffmpeg 5.1 feature checks (slow+container): version pin via AADHI_EXPECT_FFMPEG, required filters, mjpeg pipe chunk settings, MPEG-TS concat frame-exact, negative_cts_offsets raw PTS 0 vs control, loudnorm/ebur128/alimiter, zscale HDR tonemap || EXPECT_ENV L39; CHUNK_ENCODE L44 (no -threads); tools fixture L49-57 fails if EXPECT set and ffmpeg missing; test_mpegts_chunks_concatenate_frame_exactly L162; test_negative_cts_offsets_start_video_and_audio_at_zero L180
- tests/compose/conftest.py (19): Registers the container marker for compose tests || pytest_configure L15
- tests/compose/factories.py (152): Hand-written classic screenplay covering every scene type + synthetic manifest (SPEECH=1.0 s per beat) || screenplay_dict L18; make_screenplay L88; scene_audio L94; make_manifest L123
- tests/compose/dbutil.py (51): DB rows (user/project/version/render) for render_video tests || create_rows L22; get_render L46
- tests/determinism/conftest.py (80): real_app module fixture: real create_app under uvicorn (via evals/render_parity.live_server) with DYNAMIC_ENABLED, own data dir/db; skips if stage bundle or Chromium missing || STAGE_FILES L25; need_real_stage L39; real_app L45-80 (pytest.MonkeyPatch at module scope)
- tests/determinism/test_frame_hashes.py (217): §7.4(a) determinism (slow+container): harness self-tests on the stub (incl. non-deterministic negative), real stage 1x300/3x100/reverse in both formats, real-stage timer guard, danda caption font loading || test_standard_plans L36; test_report_mismatches L47; test_spot_check_on_a_fresh_page L76; test_stub_stage_is_deterministic_in_any_order L97; test_the_harness_notices_a_non_deterministic_stage L109; test_real_stage_frames_are_identical_in_any_order L121; test_real_stage_guard_trips_on_a_stray_timer L139; test_a_danda_caption_is_identical_on_fresh_pages_in_any_order L197
- tests/determinism/test_render_acceptance.py (156): B1 acceptance on the real stage + real motion engine, both formats (slow): container checks, live vs render-page SSIM, live vs MP4 SSIM at 20 seeded instants, decoded-frame continuity at joins, preflight pass || SSIM_MIN 0.98, STAGE_SSIM_MIN 0.99, JOIN_SSIM_FLOOR 0.95, JOIN_MARGIN 0.005 L38-41; rendered fixture L57 (chunk 40-80); test_container_checks_pass L71 (366 frames, >=5 joins, PTS 0 raw, av<1/30, BT.709); parity fixture L92; test_live_stage_draws_what_the_render_page_draws L106; test_live_player_matches_the_mp4 L113; test_decoded_frames_continue_across_chunk_joins L130; test_preflight_passes_on_the_fixture L151
- tests/determinism/test_engine_resources.py (139): W from fake cgroup files (deployment examples) and slot locking under 4-process contention + killed holder (slow) || test_workers_from_the_files_a_container_sees L38; test_workers_from_cgroup_v1_files L62; test_slot_locking_across_processes_under_contention L105; test_a_killed_holder_gives_its_slots_back L127
- tests/determinism/test_live_css.py (127): Live player stylesheets (tokens/base/player/panels/dynamic) leave .dyn-stage computed style, boxes and pixels identical to render page (dark and paper_ink, both formats; slow) || PROPS L28; test_live_stylesheets_leave_the_stage_unchanged L110
- tests/determinism/test_stage_layout.py (169): board_generic real-layout checks (slow): crowded 9:16 quiz compacts/reports overflow, title fitter keeps words whole (incl. Tamil), R-29 weights/label sizes || _timeline L37 (synthetic presentation shape); test_a_crowded_portrait_quiz_tightens_to_fit... L81; test_options_that_cannot_fit_report_overflow_for_preflight L90; test_the_title_fitter_keeps_words_whole L119; test_a_word_wider_than_the_column_may_break... L126; test_card_text_weight_and_label_sizes_follow_r29 L159
- tests/api/test_render_dynamic_route.py (311): /render-dynamic route: 404 when flag off/file missing, same CSP/no-referrer as /render-frame; slow real-page contract test in sandboxed Chromium (ready, info vs scene_frame_bounds, order-independent frames, safe zones, mediaPlan, caption alpha, timer guard) || test_the_page_is_hidden_while_dynamic_is_off L53; test_the_page_has_the_render_frame_policies L60; test_the_page_is_404_when_the_file_is_missing L87; test_the_stage_contract_in_chromium L253 (hard-coded zones L278-279; guard code 'timer_guard' L306)
- tests/schemas/test_v3_schema.py (477): Schema v3 B0 contract: every new field declared in V3_FIELDS with exclude_if, frozen DYNAMIC_FEATURES, classic round-trip as v2, maximal fixture current, newer_document refusal, validators, timeline/manifest v3 fields, scene_hash exclusions, carry_over, generated web schemas current || V2_FIELDS L41 (from fixtures/v2_model_fields.json); V3_ONLY_MODELS L47-52; test_every_new_field_is_a_declared_v3_field L67; test_every_v3_field_is_left_out_of_the_json_at_its_default L81; test_feature_names_and_labels_are_the_frozen_contract L93; test_maximal_fixture_file_is_current L121; test_a_newer_screenplay_is_refused_never_stripped L190; test_timeline_v3_fields_mark_version_3_and_stay_inside_their_scene L354; test_manifest_v3_fields_are_inert_until_used L406; test_look_and_overlays_never_change_the_scene_hash_but_cuts_do L428; test_web_schemas_are_generated_from_the_models L465
- tests/schemas/v3_fixtures.py (243): Maximal Dynamic screenplay (every v3 feature) + PRESENTATION dict + with_feature(); regenerates fixtures/screenplay_v3_maximal.json via python -m tests.schemas.v3_fixtures || PRESENTATION L18-58 (voices{narrator,aadhi,student,calibration{natural_wpm,control}}, music{source,provider,mood,asset_key,mode,level}, pins, hook, entity_colors...); maximal_screenplay_dict L65; canonical_maximal L194; write_fixture L201; with_feature L216
- tests/core/test_assets_auth.py (448): Asset authorisation B0: frozen FIELD_RULES table, public/private kinds, record_asset_refs, asset_uses (classic + Dynamic), every *asset_key* field authorised, 422 errors, import strip || FROZEN_TABLE L123-135; test_field_rules_are_the_frozen_table L138; test_dynamic_asset_kinds_are_public_and_calibration_stays_private L148; test_every_key_field_of_the_schema_is_authorised L292-312; test_asset_uses_covers_every_rule_on_the_maximal_dynamic_document L315; test_foreign_and_wrong_kind_dynamic_keys_are_refused L353; test_strip_clears_optional_keys_and_removes_overlays_and_clip_scenes L400
- tests/core/test_transform_survival.py (480): Every server transform (validate/loaders, PUT/GET, export/import, revert, changes, regenerate carry-over, translate) returns the maximal docs unchanged; params classic/dynamic/schema_maximal; DYNAMIC_FEATURES all on (autouse) || ASSETS L57; _V3_SCENE_FIELDS/_V3_BEAT_FIELDS/_V3_ITEM_FIELDS L70-73; _TEXT_KEYS L78; _classic L95; kind/document fixtures L195-205; _every_feature L208; test_the_fixture_is_fully_modelled_and_maximal L222; test_put_and_get_keep_every_field L314; test_export_and_import_keep_every_field L325; test_revert_endpoint... L338; test_regenerate_carry_over... L430; test_translate_changes_only_the_texts L466
- tests/invariance/test_goldens.py (155): Classic goldens byte-for-byte (screenplay, scene_hashes, timelines, ffmpeg segments incl. burn/soft variants, default GenerationOptions JSON); regenerate with AADHI_UPDATE_GOLDENS=1 || check_or_update L23; test_classic_golden L45; test_classic_segment_command_lines L50; test_classic_caption_variants L69; test_classic_fixtures_have_no_presentation L108; test_default_generation_options_json L151 (goldens/_api/generation_options.json)
- tests/invariance/test_ci.py (75): CI container job exists and blocks; Dockerfile test target env || test_container_job_runs_every_container_suite_and_blocks L15 (-m 'not slow and not network', 'container and not network', 'slow and not network and not container'); test_dockerfile_test_target L66 (AADHI_CONTAINER_TESTS=1, AADHI_EXPECT_FFMPEG=5.1)
- tests/invariance/README.md (98): Harness doc: goldens, capture, differential pixel test tolerances, CI steps, classic-change label/trailer, regenerating goldens || Tolerances (16 px > 24 levels, mean <= 0.05; burn 0.5; audio <=2 outside last 0.5 s)
- tests/invariance/goldens.py / classic_fixture.py / pixeldiff.py / differential.py / render_classic.py (1811): Invariance machinery (skimmed: headers + symbols): pinned settings, synthetic manifest (0.2 s + 0.3 s/word), command-line normaliser, tolerance-aware frame/audio comparer, merge-base vs HEAD renderer || goldens.UPDATE_ENV L38, DOCUMENTS L39, FFMPEG_VERSION (5,1) L40, settings_for_goldens L76, segments_document L195; classic_fixture.INVARIANCE_VERSION L46, synthetic_manifest L293; pixeldiff.Tolerance L47; differential.BURN_MAX_MEAN_ABS L126
- tests/usage/test_billing_coverage.py (105): B0 budget pre-check counts music, premium voices and calibration on server keys; opts.voice/opts.music duck-typed with SimpleNamespace || test_generated_music_counts_on_the_gemini_key L48; test_premium_voices_and_their_calibration_count L63; test_a_pinned_paid_narrator_counts L84
- tests/usage/test_music_and_fail_closed.py (139): ElevenLabs list prices, music operation prices, unknown operations charged at global defaults (fake stays free) || test_music_prices L69; test_unknown_operations_are_charged_at_the_global_defaults L103
- tests/api/conftest.py (86): api fixture (create_app + lifespan, browser clients with Origin + X-Aadhi-CSRF), fallback fakes only for missing modules || Api L32 (client/login/editor/user/db); api fixture L72; FAKES_IN_USE L20
- tests/evals/test_tooling.py (727): Repo tooling gates new batches must keep green: .env.example generated/current, requirements.lock satisfies ranges + Linux closure, Dockerfile allow-list, npm lockfile in sync, ruff on owned paths || test_env_example_lists_every_setting_with_secrets_empty L82; test_env_example_is_committed_and_current L97; test_lock_satisfies_requirement_ranges L167; test_lock_covers_the_linux_dependency_closure L216; test_owned_paths_are_ruff_clean L243; test_package_scripts_and_lockfile_in_sync L663