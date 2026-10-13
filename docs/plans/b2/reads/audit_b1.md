# B1 audit

## verdict
B1 is substantially built. The code covers every B1 scope line and the five frozen contracts:
- the motion toolkit;
- the one-compositor stage with render page, guard and route;
- the engine: cgroup-aware workers, flock slots, CDP JPEG capture, chunk planner, x264 pipe, concat, negative_cts_offsets mux, media extraction via route.fulfill, cache keys, page recycling;
- preflight and container checks with remedies;
- the engine switch, board_generic, mesh_drift/colour_block, cut/crossfade, in-stage captions and caption-only layer, the minimal TimelinePresentation/ResolvedLook resolver and DYNAMIC_ENABLED.

Every test the B1 list names exists, and each test-list item is covered (`test` rows above). Divergences from the design are mostly documented in its own status paragraph.

B1 is not closed, though, and the status paragraph's 'acceptance met except the container benchmark' is too generous:
- The blocking Docker container job, including the determinism suite on Linux and ffmpeg 5.1, has never run. The commit is unpushed and nothing was run locally.
- The production-image benchmark has not run. Its documented command cannot work, because scripts/ and tests/ are not in the image; it needs bind mounts. So P1, the profile limits and Q7 are unset.
- The showcase acceptance render is a manual claim, not reproducible from the repo.
- The owner's frame-by-frame review is pending.

There are also real gaps:
- fit='contain_blur' is missing; clamp works only in the Dynamic stage; the portrait Player is still pillarboxed in 16:9 containers (all handed over from B0).
- Ken Burns scaling in render mode is an untested determinism risk.
- chart, graph, model_3d, skill_tree and quiz side panels are dropped silently.

Recommendation: before starting B2,
1. run the container suites and the benchmark (commands in the Docker benchmark how-to);
2. add a Ken Burns or picture case to the determinism suite;
3. decide how preflight remedies share `ResolvedLook.variant` and `text_fit` with B2's textfit and variants;
4. make one zones source.
Then begin B2's themes, zones and template plumbing.

## blockers_for_b2
Under the §10.2 gates, B1 is not closed yet. The design wants these settled before B2's contracts are frozen:
1. Gate 2 has never run. The Docker `test` job, including the B1 determinism suite on Debian ffmpeg 5.1 and Linux FreeType, has never executed, because HEAD a75acf8 is not pushed (origin/overhaul/v2 = 195be39) and there is no local Docker run. B2's 'gallery golden frames in Docker' depend on this pipeline.
2. The B1 benchmark has not been run. P1, the motion profile limits and Q7 are unset.
3. Contract conflicts to resolve before B2 freezes `TemplateSpec` / `SceneLook.lock` / the `themes.json` schema:
   (a) `ResolvedLook.variant` is used by preflight as the remedy flag `'safe'` (preflight.py:409-411, board_generic.js:1021). B2 template variants, such as the four 16:9 intro layouts, need the same field.
   (b) `ResolvedLook.text_fit` is designed as `{size, lines}` from textfit.py (schema comment timeline.py:217). B1 uses it as the preflight remedy channel `{size, scrim}` (preflight.py:412-421, board_generic.js:1018-1048).
4. One source for zones. B2's zones.py must replace three copies: core.js:360-387, preflight.py:75-82 and test_render_dynamic_route.py:278-279.
5. Theme and role API. B2's themes.py/themes.json must replace the hard-coded families in presentation.py:61-74 and _CONTENT_FIXED (:69-74), core.js DARK_CONTENT/DARK_STRUCT (:236-242), the literal caption key colour (dynamic.css:801-803, captions.js:21-23) and DISPLAY_FACES outfit/inter only (stage.js:47). Python and JS colour maths must stay equal (color.js:7-9).
6. Template plumbing B2 needs and B1 lacks:
   - a per-template media-box hook for mediaPlan (stage.js:528-533 knows only board_generic; other templates would get frames at full stage size);
   - `ctx.cues` and cues.js (types.js has no cues);
   - the Python TemplateSpec registry (aadhi/compose/dynamic/templates.py is absent);
   - direct.py routing (board_generic currently draws every scene type);
   - more transition kinds (TRANSITIONS frozen to cut/crossfade in transitions.js:16 and presentation.py:38).
7. A determinism risk to settle before full_bleed_media (Ken Burns 1.00→1.06). Render-mode pictures with Ken Burns use a CSS scale plus fractional translate (media.js:348-352, kenburns.js:41-43). B1's own measurement found scaling pictures made frames depend on render order (board_generic.js:884-886; design line 2505-2506). The determinism fixture has no pictures, so add a Ken Burns case to tests/determinism.
8. Content loss to be aware of: Dynamic silently drops chart/graph/model_3d/skill_tree/quiz side panels with no warning (board_generic.js:143-154, 200-209; job.py:45-55). chart_build must also keep Chart.js `animation:false`, which the scan enforces.

## unfinished_or_inconsistent
1. The benchmark harness cannot run as documented in the production image. scripts/ is not in the image (Dockerfile:78-87) and tests/ is excluded (.dockerignore:38). bench_motion.py reads tests/core/fixtures/showcase_ohms_law.json (:61) and the stub stage (:62).
2. CI (all jobs, including the blocking `container` job) has never run on B0/B1 code: commit a75acf8 is local only.
3. The 16-scene showcase acceptance render exists only as a claim in the docs (DYNAMIC_VIDEO_DESIGN.md:2465-2472). No test renders it through render_video + preflight.
4. Ken Burns uses a CSS scale in render mode (media.js:348-352), against the 'rising, never scaling' determinism rule (board_generic.js:884-886). No test covers it.
5. Dynamic drops chart/graph/model_3d/skill_tree/quiz side panels without any render warning.
6. Overlays are not drawn (B6), yet the engine fetches overlay clips as required before capture (engine.py:426-434, 948-950). A missing overlay asset fails a render it would not appear in.
7. A video-kind figure would be requested from /__frames but never planned: mediaPlan covers only scene.media and side_panel.media (media.js:141-152). Latent, since figures are normally images.
8. Two different frame counts:
   - preflight checks `info()` against expected_frame_count(total_duration) (preflight.py:683, 695);
   - the engine uses the scene-bounds total (engine.py:918-919, 978).
   They differ only for a scene shorter than half a frame, which would give a non-retryable stage_contract error in preflight.
9. Minor divergences from the design:
   - CROSSFADE_MAX 0.8 s vs §4.9's 300-500 ms (transitions.js:19);
   - clean_pill 9:16 weight 700 vs 600 (dynamic.css:775-778);
   - no WebGL2 probe in compose/capabilities.py (§7.8; only render.js probeWebgl, used when shader presets exist);
   - Dynamic output size follows the classic RENDER_WIDTH/HEIGHT (base.py:124-136);
   - the template contract differs from §4.2: layout()/texts() are added, ctx.cues is missing, and there is no Python TemplateSpec.
10. A docs/code mismatch. OPERATIONS.md:164-166 says that with DYNAMIC_ENABLED off the presentation is ignored, but timeline.py:776 still sizes the stage from presentation.format. A portrait screenplay with the flag off therefore becomes a 1080x1920 classic timeline (a B0 decision; test_dynamic_presentation.py:79-84 checks landscape only).
11. The source scan does not reach modules the stage imports from web/js/player, web/js/shared or web/js/render.
12. Unused code: determinism.spot_check (determinism.py:111-122) and the loudness branch of inspect_container (container.py:344-363). They are ready but not wired by the engine.
13. Working tree: tests/api/test_dynamic_flag_serve.py has only an import reorder (uncommitted), and the stray p.out is untracked.
14. B0 items handed to B1 are incomplete:
   - fit='contain_blur' is missing;
   - clamp works in the Dynamic stage only;
   - the portrait Player sizes its stage but the watch and Studio containers stay 16:9.

## docker_benchmark_howto
State at audit time: the Docker Desktop client 29.2.1 (context desktop-linux) is installed, but the daemon was not running. Start Docker Desktop with the WSL2 backend first. LF line endings are enforced by .gitattributes, so bind-mounting the checkout is safe. web/vendor is already filled (run `npm ci --no-audit --no-fund` again after package changes).

1. Resources for the motion profile (docker-compose.yml:170-175 asks for cpus "8", memory 10g, shm 2gb). The laptop has 16.8 GB RAM, and WSL2 gives the VM about 50% by default, which is below 10 GB. Create %UserProfile%\.wslconfig with:
```
[wsl2]
memory=12GB
processors=16
swap=8GB
```
Then run `wsl --shutdown`, restart Docker Desktop and check with `docker info --format "{{.NCPU}} {{.MemTotal}}"`.

2. CI container suites locally. These mirror .github/workflows/ci.yml:148-339 and the Dockerfile `test` target :117-139 (sets AADHI_EXPECT_FFMPEG=5.1, AADHI_CONTAINER_TESTS=1, WORKDIR /src). Run from the repo root in PowerShell, and omit CI's `--user $(id -u):$(id -g)` on Windows:
```
docker build --target test -t aadhi-eduengine:test .
$c = @('run','--rm','--shm-size=1g','--security-opt','seccomp=docker/app/chromium-seccomp.json','-v',"${PWD}:/src",'-w','/src','aadhi-eduengine:test')
docker @c sh -c "ffmpeg -hide_banner -version | head -n 1; python -m pytest --version"
docker @c python -m pytest -m "not slow and not network"              # fast suite + classic goldens
docker @c python -m pytest -m "container and not network"             # ffmpeg 5.1 checks, loudness, container checks, determinism (test_frame_hashes.py)
docker @c python -m pytest -m "slow and not network and not container" # incl. tests/determinism/test_render_acceptance.py, test_live_css.py, test_stage_layout.py
```
Differential pixel test. The image has no git, so export the base on the host. The base CI would use for this push is 195be39 (origin/overhaul/v2):
```
mkdir ..\aadhi-base; git archive --format=tar 195be39 | tar -x -C ..\aadhi-base; Push-Location ..\aadhi-base; npm ci --no-audit --no-fund; Pop-Location
mkdir ..\aadhi-inv
docker run --rm --shm-size=1g --security-opt seccomp=docker/app/chromium-seccomp.json -v "${PWD}:/src" -w /src -v "$((Resolve-Path ..\aadhi-base)):/base:ro" -v "$((Resolve-Path ..\aadhi-inv)):/out" aadhi-eduengine:test python tests/invariance/differential.py --base-root /base --head-root /src --out /out --keep-states
```
For the second run, add `--burn-captions` and use a fresh /out folder.

The alternative is to push overhaul/v2, which triggers the real `container` job on ubuntu-24.04. That is an owner decision, because the push is visible.

3. B1 benchmark in the production image under the `motion` profile:
   - Compose's `:?` interpolation (docker-compose.yml:21-23, 60) needs BASE_URL, POSTGRES_PASSWORD and JWT_SECRET even with `--no-deps`. Dummy values are fine because the bench never touches the database:
```
$env:BASE_URL='https://bench.invalid'; $env:POSTGRES_PASSWORD='bench'; $env:JWT_SECRET='bench-0123456789abcdef0123456789abcdef'
docker compose --profile motion build render-worker-motion        # target app -> aadhi-eduengine:latest
docker compose --profile motion run --rm --no-deps render-worker-motion python /app/docker/app/check_chromium.py   # sandbox works under Docker Desktop
```
   - The documented command (OPERATIONS.md:505-508) fails as-is: the image lacks scripts/ and tests/. Bind-mount them (bench ROOT becomes /app, so it finds /app/tests/core/fixtures and /app/web):
```
mkdir bench-out
docker compose --profile motion run --rm --no-deps -v "${PWD}/scripts:/app/scripts:ro" -v "${PWD}/tests:/app/tests:ro" -v "${PWD}/bench-out:/out" render-worker-motion python scripts/bench_motion.py --minutes 5 --json /out/bench-16x9.json
docker compose --profile motion run --rm --no-deps -v "${PWD}/scripts:/app/scripts:ro" -v "${PWD}/tests:/app/tests:ro" -v "${PWD}/bench-out:/out" render-worker-motion python scripts/bench_motion.py --format portrait --minutes 3.5 --burn-captions --json /out/bench-9x16.json
```
   - Useful variants: `--workers 4|6`, `--preset faster`, `--crf 17`, `--disable-frame-rate-limit` (the open §7.5 question), `--driver-per-worker`, `--stage stub` (engine and capture only).
   - With cpus 8, W auto-detects as min(6, 8-1, physical-1) = 6 via cgroup v2 cpu.max.
   - Record in OPERATIONS.md §4.2 and DYNAMIC_VIDEO_DESIGN §7.10/P1/Q7: fps per worker, aggregate fps, wall seconds, the phases, memory_per_worker_mb, and the projections for the 5-minute lesson and the 3.5-minute reel.

   A close substitute without compose is the test image, which has the same runtime layers:
```
docker run --rm --shm-size=2g --cpus=8 --memory=10g --security-opt seccomp=docker/app/chromium-seccomp.json -v "${PWD}:/src" -w /src aadhi-eduengine:test python scripts/bench_motion.py --minutes 5 --json bench-out/bench-16x9.json
```

Caveats:
- Docker Desktop on the laptop gives numbers for the production image but not for production hardware, which Q7 is about.
- The bench uses board_generic only. §7.10's formula_focus, chart_build and media_split templates arrive in B2, and Chart.js is not exercised at all, so plan a re-run after B2.
- The OneDrive-synced checkout is slow as a bind mount. Cloning into the WSL2 filesystem speeds these runs up.

## items

- [scope] web/js/motion: easing + cubic-bezier solver (motion tokens, pop) :: done :: None. :: web/js/motion/easing.js:30-89 (Newton + bisection, Chromium end-tangent extrapolation), :130-139 (ease.enter/exit/move/hero = §4.8 control points), :107-113 (pop, single 1.08 overshoot). Test web/tests/motion/easing.test.js:39-53 (2,001 points x 15 curves vs bisection reference, asserts <=1e-4 and <=1e-7), :101-141.

- [scope] web/js/motion: keyframe tracks :: done :: None. :: web/js/motion/track.js:124-152 (numbers, colours in OKLCH, arrays, objects; jumps; clamped colour progress); progress/stagger :173-194. Test web/tests/motion/track.test.js (order-independence :70-87).

- [scope] web/js/motion: OKLCH interpolation :: done :: None for B1. B2's Python themes validator must reuse the same Ottosson matrices (color.js:7-9 note); presentation.py:80-116 has its own copy. :: web/js/motion/color.js:282-306 (shorter hue arc, grey borrows hue, premultiplied alpha), :97-129 (CSS Color 4 gamut mapping), :325-333 WCAG contrast. Test web/tests/motion/color.test.js:83-94 (52^3 round trip exact), :111-147.

- [scope] web/js/motion: Intl.Segmenter splitting :: done :: None. :: web/js/motion/text.js:124-129 segmentGraphemes, :107-115 joinIndicConjuncts (Kannada, Tamil kssa/shri, older ICU), :147-168 splitWords, :181-207 splitLines (greedy/balanced, never inside a cluster). Test web/tests/motion/text.test.js:17-56, :59-109, :179-195.

- [scope] web/js/motion: stroke drawing :: done :: None. :: web/js/motion/draw.js:43-91 (strokeDash, strokeSegment, staggerProgress). Test web/tests/motion/draw.test.js. Used by the quiz ring board_generic.js:949-953.

- [scope] web/js/motion: mulberry32 (+ hashSeed) :: done :: No Python port of hashSeed exists in the repo (presentation.py:153-156 seeds via SHA-256 instead); only needed if B2 must reproduce JS seeds in Python. :: web/js/motion/random.js:23-32 mulberry32, :59-72 hashSeed (FNV-1a + fmix32, documented for a Python port). Test web/tests/motion/random.test.js:17-34, :66-73 pinned vectors.

- [scope] web/js/motion: count-up :: done :: None. :: web/js/motion/count.js:61-80 (en-IN lakh grouping default, numberingSystem). Test web/tests/motion/count.test.js.

- [scope] web/js/dynamic/stage.js (one compositor, live + render) :: done :: Registry holds only board_generic / mesh_drift / colour_block (stage.js:42-44); mediaPlan box hook knows only board_generic (stage.js:529-532). :: web/js/dynamic/stage.js:323-597 (update(t) live :464-472, seek :482-518 incl. font re-measure :504-513, info :520-522, mediaPlan :528-533, textBoxes :536-565, setLayers :572-580). Tests web/tests/dynamic/stage.test.js:194-227 (pure frames, reverse order), player.test.js:90-149.

- [scope] web/js/dynamic/render.js (render page boot) :: done :: None. :: web/js/dynamic/render.js:31 STAGE_VERSION=3, :67-99 installStageApi, :143-199 boot (token-in-fragment, fonts_missing, TeX converted before ready :171-176, first paint without media frames :182, guard after ready :188). Test web/tests/dynamic/render.test.js:60-200.

- [scope] web/js/dynamic/guard.js (timer guard) :: done :: None. :: web/js/dynamic/guard.js:38-69 (setTimeout/setInterval throw, log 'aadhi timer guard', window.aadhiStageGuard.trips); worker collects trips aadhi/compose/dynamic/stage_page.py:60,229-234.

- [scope] web/render-dynamic.html :: done :: None. :: web/render-dynamic.html:1-47 (data-aadhi-render=dynamic, tokens.css + dynamic.css only, one module script, no-referrer). Test web/tests/dynamic/render.test.js:191-200.

- [scope] /render-dynamic route :: done :: None. :: aadhi/api/routers/pages.py:63-69 (same CSP/no-referrer as /render-frame, 404 while DYNAMIC_ENABLED off). Tests tests/api/test_render_dynamic_route.py:53-94 and the Chromium contract test :251-311.

- [scope] Determinism source-scan test (§7.4) :: done :: Coverage boundary: modules the stage imports from outside the two dirs (web/js/player/schedule.js, richtext.js, kenburns.js, icons.js, web/js/shared/dom.js, web/js/render/fonts.js, render/render.js) are not scanned although they produce stage pixels. :: web/tests/dynamic/sourceScan.test.js:214-402 (TypeScript compiler API: clock, random, raf, timers, CSS motion, WAAPI, Chart.js animation, self-clocked libs, <video>, DOM in motion, struct-role, emphasis), :494-535 repository scan of web/js/dynamic/**, web/js/motion/**, web/css/dynamic.css and web/render-dynamic.html.

- [scope] aadhi/compose/motion: worker pool with cgroup-aware W :: done :: None. :: aadhi/compose/motion/resources.py:89-127 (cgroup v2 cpu.max up the hierarchy, v1 cfs), :139-148 cpuset, :163-178 W=min(6, quota-1, physical-1), RENDER_MOTION_WORKERS override; engine.py:929-930. Tests tests/compose/motion/test_resources.py:49-108, tests/determinism/test_engine_resources.py:38-69.

- [scope] Host-wide CPU slots (RENDER_SLOT_DIR, RENDER_CPU_SLOTS) :: done :: None. :: resources.py:181-192 (slot count, dir default <work>/cpu-slots), :200-332 (flock / msvcrt lock files, cancel-aware acquire); engine.py:951-960 (workers = slots obtained), :1003-1004 release after capture. Tests test_resources.py:117-194, test_engine_resources.py:105-139.

- [scope] CDP capture :: done :: None. :: aadhi/compose/dynamic/stage_page.py:371-375 (Page.captureScreenshot JPEG q92 optimizeForSpeed), :452-454 (Animation.setPlaybackRate 0 backstop), :557-559 (DPR 1, no reduced_motion), no page.clock anywhere. stage.py:252-254.

- [scope] Chunk planner (scene-aligned 240-600, longest first) :: done :: None. :: aadhi/compose/motion/chunks.py:36-37, :70-83 scene_frame_bounds, :91-139 plan_chunks, :142-144 assignment_order. Tests tests/compose/motion/test_chunks.py:47-122 (40 random lessons).

- [scope] Encoder pipe with MOTION_X264_PRESET / MOTION_X264_CRF :: done :: Documented deviation: -threads 4 added (design status line 2503). :: aadhi/compose/motion/encode.py:55-113 (exact §7.5 command + -threads 4), engine.py:248-331 _PipeEncoder, :699-715; config.py:388-389. Test tests/compose/motion/test_encode.py:45-68.

- [scope] Concat of MPEG-TS chunks :: done :: None. :: engine.py:1012-1025 (chunks.ffconcat), encode.py:244-262 (-c copy).

- [scope] Final mux with negative_cts_offsets :: done :: None. :: encode.py:60 FINAL_MOVFLAGS='+faststart+negative_cts_offsets', :244-262 (master audio stream-copied, chapters, default-on mov_text). Tests test_encode.py:180-198, tests/compose/dynamic/test_container.py:200-217.

- [scope] Media extraction served through route.fulfill :: done :: mediaPlan lists only scene.media and side_panel.media (media.js:141-152): a video-kind figure would be requested but never planned (latent). Overlays are never drawn but their clips are fetched as required (engine.py:426-434, 948-950). :: engine.py:548-582 (_extract_media, native-rate extraction), :683-696 (only media the lecture names); motion/media.py:63-177; stage_page.py:418-437 (route.fulfill from the worker); stage.py:53-92 FrameRegistry; page side web/js/dynamic/media.js:180-232 mediaPlan, :356-374 lookahead 10. Tests test_media.py, test_media_rate_slow.py:58-73, test_engine_slow.py:112-116, web/tests/dynamic/media.test.js:140-166.

- [scope] Chunk cache keys + cache :: done :: Deviations (documented): reuse only by a retry of the same render (per-render workspace), so 'a one-scene edit re-renders only its chunks' (§7.6) is not achieved; bundle identity is size+mtime, not content. :: chunks.py:152-253 (bundle digest, slices incl. outgoing scene + kicker chapters, captions in range, media identities, encoder, capture, Chromium), CHUNK_CACHE_VERSION=2 (:39); cache.py; engine.py:983-988, :756-766. Tests test_chunks.py:128-239, test_cache.py, test_engine_slow.py:145-154.

- [scope] Page recycling :: done :: None. :: engine.py:113-114, :787-793 (1,800 frames or fps < 50% of first chunk), :772-780 (failed chunk retried once on a fresh page). Test test_engine_slow.py:169-174.

- [scope] Preflight framework (textBoxes sampling, result schema in Render.options['qa']) :: partial :: §7.12 rows not implemented (preflight.py:44-46 defers): duration profile/fit (B4), Aadhi time (B4), 9:16 frame-0 hook band (B2/B4), licences (B6), music under speech and master loudness (B3), caption re-chunk (B2 kinetic.py). :: aadhi/compose/dynamic/preflight.py:430-462 sampling (24 spread, scene starts/ends, beat settle, 9:16 caption chunks), :281-316 checks text_overflow/safe_zones/contrast/type_floor, :376-422 remedies, :654-723 run_preflight; job.py:131-144; container.py:478-491 stores it. Tests tests/compose/dynamic/test_preflight_checks.py, test_preflight_stage.py, tests/compose/motion/test_job.py:167-197.

- [scope] Post-render container checks with remedies :: partial :: Loudness/true-peak check exists but is inactive: engine never passes a LoudnessTarget (engine.py:1039-1042) until B3's mix. Pixel-contrast re-render, static stretches and the 3-frame determinism spot check are not wired (container.py:22-23; determinism.spot_check at :118-122 unused by the engine). :: container.py:244-394 inspect_container (chunk counts, total frames, video start with and without edit list, A/V start, BT.709 tags, grid, declared rate, caption track, audibility, black edges), :435-470 verify_container remedies; engine.py:1026-1045. Tests test_container.py (container-marked real ffmpeg tests :180-291).

- [scope] Engine switch in the render job :: done :: None. :: aadhi/compose/video.py:999-1005; aadhi/compose/motion/job.py:100-205 (dynamic_disabled, render_unavailable, disk_full, preflight_blocked, store). Tests test_job.py:64-112, :133-164.

- [scope] board_generic template :: done :: Renders every scene type in B1, but side panels of kind chart, graph, model_3d, skill_tree and quiz are silently dropped in the Dynamic stage (live and MP4): mediaOf (:143-154) and cardSpecs (:200-209) handle only media panels and terminal; job.dynamic_warnings (job.py:45-55) emits no warning for them. :: web/js/dynamic/templates/board_generic.js (variants :228-235, card stack with MIN_DWELL re-timing :718-760, quiz with ring, media split, remedies via slotTuning :1018-1048, texts() :974-1010). Tests web/tests/dynamic/stage.test.js:33-505, tests/determinism/test_stage_layout.py.

- [scope] mesh_drift and colour_block backgrounds :: done :: Cheaper mesh_drift composite is an open item (largest per-frame capture cost). :: web/js/dynamic/backgrounds/mesh_drift.js:36-153 (seeded Lissajous blobs, 0.2 s drift step, 1/8 canvas, hold desaturation), colour_block.js:34-121 (static grain tile, block colours). Hold clock state.js:102-147. Tests web/tests/dynamic/captions_guard_backgrounds.test.js:89-139, state.test.js:60-79.

- [scope] Cut and crossfade transitions :: done :: Minor divergence: CROSSFADE_MAX = 0.8 s (transitions.js:19) vs §4.9 crossfade 300-500 ms (resolver emits 0.45). :: web/js/dynamic/transitions.js:16-71; resolver aadhi/compose/presentation.py:216-227 (first scene cut, colour-block neighbours crossfade, new chapter crossfade, cut inside a chapter, 0.45 s). Tests web/tests/dynamic/transitions.test.js, stage.test.js:229-241, tests/compose/test_dynamic_presentation.py:108-136.

- [scope] In-page phrase captions :: done :: Minor divergence: clean_pill in 9:16 is weight 700 (web/css/dynamic.css:775-778, captions.js:112) vs §4.11 'weight 600'. :: web/js/dynamic/captions.js:32-118 (clean_pill / bold_outline, fixed band, key words), state.js:168-184 (kinetic chunks with blanking, else beat phrase captions); 9:16 phrase cues aadhi/compose/presentation.py:267-320 wired in timeline.py:546-552. Tests captions_guard_backgrounds.test.js:18-62, test_dynamic_presentation.py:176-189, :336-375.

- [scope] Caption-only layer mode (setLayers) :: partial :: The page mode exists; the engine's burned second version (overlay pass of §7.5) is not implemented (open item, B4). :: stage.js:572-580, dynamic.css:48-50, stage_page.py:349-351 set_layers and :377-385 capture_png(transparent); tests tests/compose/dynamic/test_stage_page.py:130-142, test_render_dynamic_route.py:300-304, stage.test.js:419-438.

- [scope] Minimal TimelinePresentation and ResolvedLook :: done :: Resolver ignores SceneLook.variant (always None :231), palette_slot, presenter, continues_from, lock and Presentation.entity_colors, cue_term_style/personality effects, motion='calm' (passed through, never read by the stage); hold_reason freezes the background from scene start (frozen_at 0.0, :235). B2's themes.py/direct.py/events.py replace these. :: aadhi/schemas/timeline.py:193-219 (fields identical to design §6.7 lines 1633-1656); resolver aadhi/compose/presentation.py:201-261 (4 R1 families hard-coded :61-74); wired in aadhi/compose/timeline.py:729-801. Tests tests/compose/test_dynamic_presentation.py:70-196.

- [scope] Admin flag DYNAMIC_ENABLED :: done :: None (uncommitted diff in test_dynamic_flag_serve.py is an import reorder only). :: aadhi/config.py:384; pages.py:67; job.py:110-112; serve-time rebuild aadhi/api/timelines.py current_timeline (+ ETag 'dyn=' term). Tests tests/api/test_dynamic_flag_serve.py:82-154, test_render_dynamic_route.py:53-57, test_job.py:103-112.

- [scope] motion compose profile :: partial :: Limits are the design's starting point, never measured in the container (benchmark pending). :: docker-compose.yml:151-175 (render-worker-motion, profiles [motion], cpus 8, memory 10g, shm 2gb, seccomp); docs/OPERATIONS.md:490-500.

- [scope] Benchmark harness :: partial :: The documented container command (bench_motion.py:28-29, OPERATIONS.md:505-508) cannot run in the app image: Dockerfile:78-87 copies only aadhi/, alembic/, server.py, web/, docker/app/, video_template (no scripts/), and .dockerignore:38 excludes tests/ while the script reads tests/core/fixtures/showcase_ohms_law.json (:61) and tests/compose/motion/stub_stage (:62). Needs bind mounts (see how-to). Also §7.10 asks for formula_focus, chart_build and media_split templates; only board_generic exists and Chart.js is never exercised. :: scripts/bench_motion.py:1-427 (showcase repeated to --minutes, 1080p testsrc2 clip, real engine + /render-dynamic via a static server, fps/worker, phases, memory, projections).

- [contract] Stage contract v3 (window.aadhiStage) :: done :: Implementation is a superset of §7.3: extra `error` field, mediaPlan entries add asset_key/url/duration/end_behavior/trim_in, textBoxes add alpha/scene/slot/size/min/weight/text, seek(frame, {frames:false}) for the boot paint. Freeze these extras in INTERFACES.md if B2 relies on them. :: web/js/dynamic/render.js:31, :81-96, :183-187; worker side stage_page.py:54, :263-266 (version check), :141-187 StageInfo.parse/check; docs/INTERFACES.md:690+.

- [contract] /render-dynamic route :: done :: None. :: pages.py:63-69; tests test_render_dynamic_route.py:60-85.

- [contract] Settings RENDER_MOTION_WORKERS, RENDER_CPU_SLOTS, RENDER_SLOT_DIR, MOTION_X264_PRESET, MOTION_X264_CRF :: done :: None. :: aadhi/config.py:385-389; .env.example:344-354; OPERATIONS.md:172-180.

- [contract] Chunk cache key :: done :: Deviation from §7.6: mtime-based bundle identity; cross-render reuse not implemented (by design note). :: chunks.py:12-20 docstring, :198-253, CHUNK_CACHE_VERSION=2 (:39); STAGE_TIMELINE_FIELDS guarded by test_chunks.py:197-213.

- [contract] Preflight result schema :: done :: Added key type_floor in checks (documented in design status line 2500-2501). :: preflight.py:67 PREFLIGHT_SCHEMA=1, :538-563 summary/as_qa; container.py:61 QA_SCHEMA=1, :478-491. Test test_preflight_checks.py:391-414.

- [test] Determinism 1x300, 3x100, reverse in Docker :: partial :: Passed only on Windows per the doc; never run in the Docker test image (the container job never ran: HEAD a75acf8 is unpushed, origin/overhaul/v2 = 195be39). Fixture is the 3-scene Ohm's-law timeline with no pictures or clips (evals/render_parity.py:76-99, 550-593), so Ken Burns and media frames are not covered. :: tests/determinism/test_frame_hashes.py:121-137 (real stage via real app, both formats, slow+container), harness aadhi/compose/dynamic/determinism.py:38-108; also guard and danda cases :140-217. CI step .github/workflows/ci.yml:286-300.

- [test] Parity live vs MP4, SSIM >= 0.98 at 20 instants :: done :: Verified on Windows only; runs in CI's container 'Slow suite' step (slow marker, not container) which has never run. :: tests/determinism/test_render_acceptance.py:107-117 (plus compositor-only >= 0.99), evals/render_parity.py:438-441, 596-621, 696-722, 782-832; stub variant tests/compose/dynamic/test_render_parity_dynamic.py:62-85; CSS isolation test tests/determinism/test_live_css.py:108-127.

- [test] Easing accuracy <= 1e-4 :: done :: None. :: web/tests/motion/easing.test.js:39-53.

- [test] Grapheme splitting for Tamil, Devanagari and Telugu :: done :: None. :: web/tests/motion/text.test.js:17-56 (+ Kannada/Malayalam/Bengali :59-82); Python 9:16 cluster-safe captions tests/compose/test_dynamic_presentation.py:297-311.

- [test] Exact frame count :: done :: None. :: tests/compose/motion/test_engine_slow.py:102-110 (barcode-decoded frames 0..284 across 6 joins), test_render_acceptance.py:72-89 (366 frames), test_container.py:220-242.

- [test] First video PTS 0 and A/V start within one frame :: done :: None (ffmpeg 5.1 variant pending the container run). :: container.py:322-338; tests test_container.py:180-217, :245-253; test_engine_slow.py:122; test_render_acceptance.py:79-80.

- [test] Guard trips on a stray timer :: done :: None. :: test_frame_hashes.py:140-162 (real stage), web/tests/dynamic/render.test.js:104-111, captions_guard_backgrounds.test.js:66-85, tests/compose/dynamic/test_stage_page.py:119-127, test_render_dynamic_route.py:305-311.

- [test] W from fake cgroup files :: done :: None. :: tests/compose/motion/test_resources.py:49-98, tests/determinism/test_engine_resources.py:38-69.

- [test] Slot locking across processes :: done :: None. :: test_resources.py:154-194, test_engine_resources.py:105-139 (4 processes contending, killed holder).

- [test] Decoded-frame continuity across chunk joins :: done :: None. :: test_render_acceptance.py:131-148 (real stage, >=5 joins), test_engine_slow.py:102-110 (stub barcode).

- [acceptance] Showcase with synthetic presentation renders in 16:9 and 9:16 through board_generic, passing preflight and container checks :: partial :: No reproducible test or stored artifact renders the 16-scene showcase through render_video + preflight; bench_motion.py uses preview_timeline and skips preflight. Repeat it in the Linux container and keep the report. :: Claimed done by a manual run (docs/DYNAMIC_VIDEO_DESIGN.md:2465-2472; OPERATIONS.md:525-527). Automated acceptance covers only the 3-scene fixture (test_render_acceptance.py:58-104, :151-156); test_dynamic_presentation.py:324-354 builds the showcase timeline but does not render it.

- [acceptance] B1 benchmark (§7.10) in the production render image; P1, compose profile and Q7 set from it :: missing :: Run with bind mounts (how-to); record fps/worker, end-to-end, memory; set P1 (DYNAMIC_VIDEO_DESIGN.md:138), docker-compose.yml:174-175 limits, answer Q7 (:2678). Re-run once B2's formula/chart/media templates exist (§7.10 template list). :: Pending per DYNAMIC_VIDEO_DESIGN.md:2459, 2515-2517 and OPERATIONS.md:518-519; Docker Desktop client 29.2.1 is installed but the daemon was not running during this audit.

- [acceptance] Reviewer frame-by-frame side-by-side of MP4 and live player (2 transitions, 3 scenes) :: owner-only :: Owner review. :: DYNAMIC_VIDEO_DESIGN.md:2455, 2514-2515 (pending); automated proxies: test_render_acceptance.py, test_live_css.py.

- [open] Reviewer side-by-side :: owner-only :: Owner review. :: DYNAMIC_VIDEO_DESIGN.md:2514-2515.

- [open] B1 container benchmark under the motion profile :: missing :: Fix the documented command (mount scripts/ and tests/ or copy them in a bench target) and run it. :: See acceptance; harness packaging gap Dockerfile:78-87, .dockerignore:38, bench_motion.py:61-62.

- [open] CI container job first run on ffmpeg 5.1 (incl. determinism suite) :: missing :: Push the branch (owner decision) or run the suites locally in the test image; make 'Container suites (Docker test image)' a required check (owner setting). :: .github/workflows/ci.yml:148-339; HEAD a75acf8 not pushed (origin/overhaul/v2 = 195be39).

- [open] Preflight remedies reach only the MP4 until B2/B4 store them in the lecture :: deferred-by-design :: B2/B4. :: preflight.py:396-422 apply to the served copy only; job.py:149-152 capture uses pre.timeline; refresher re-applies them job.py:77-90.

- [open] 9:16 split picture small with tall cards; 9:16 terminal card can collapse before its last line :: deferred-by-design :: B2 media_split and code_terminal. :: board_generic.js:648-658 (media 25-55% of the body), cardSpecs terminal timing :200-209.

- [open] Analytics flags for allowed live/MP4 differences and the low-end fallback :: deferred-by-design :: B4. :: No analytics or frame-time fallback in web/js/dynamic; reducedMotion live-only stage.js:348.

- [open] Static-run deduplication, burned second version, draft renders :: deferred-by-design :: B4/B9. :: Frame keys only hashed into the cache marker (engine.py:704-713, 731-732); no overlay pass; EncoderSpec can scale (encode.py:90-94) but no draft path.

- [open] Cheaper mesh_drift composite :: deferred-by-design :: Later batch. :: mesh_drift.js:79-85, 122-145.

- [open] 9:16 caption chunks under 1.0 s on fast beats :: deferred-by-design :: B2 kinetic.py (cross-beat merge with measured widths). :: test_dynamic_presentation.py:336-354 tolerates <=12 under 1.0 s.

- [open] Card revealed on the same beat as a taller card is pushed out before it is shown :: deferred-by-design :: B2 code_terminal/media_split layouts. :: board_generic.js layout plan :718-760.

- [b0-open] Player end_behavior='clamp' :: partial :: Classic player/panels mapping of clamp and the JSDoc type (B6 when clip scenes ship; matters if a clip timeline is served with DYNAMIC_ENABLED off). :: Dynamic stage treats clamp as hold: web/js/dynamic/media.js:108-127, test web/tests/dynamic/media.test.js:49. Classic player would loop it: web/js/player/scenes/media.js:124 (v.loop = end_behavior !== 'freeze'); JSDoc web/js/shared/types.js:119-120 omits 'clamp'.

- [b0-open] Player fit='contain_blur' :: missing :: Blurred cover backdrop + contained clip in the stage (needed by B2 full_bleed_media 9:16 and B6 clip_scene). :: web/js/dynamic/media.js:285 maps every fit except 'cover' to 'contain'; web/css/dynamic.css:661-670 only contain/cover; classic player untouched.

- [b0-open] Portrait Player :: partial :: Containers stay 16:9: .watch-player aspect-ratio 16/9 (web/css/player.css:2203-2206) and Studio preview boxes (web/css/studio.css:2483, 3438, 3743), so a 1080x1920 reel is pillarboxed small. :: web/js/player/player.js _sizeStage(timeline.width, height) for Dynamic live/preview; test web/tests/dynamic/player.test.js:115-129.