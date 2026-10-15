# Research: deterministic motion graphics, live and MP4 (`motion-rendering`)

Prepared 2026-10-09 for Aadhi EduEngine (branch `overhaul/v2`). The repo was only read, never changed.

**How this was researched.** Sources are official docs, licence files, the npm registry, Chrome DevTools Protocol definitions and GitHub issues. I also ran my own benchmarks with Playwright 1.63, chrome-headless-shell 153 and ffmpeg 8.1. The scripts are in `scratchpad/bench/` (`bench.py`, `beginframe.py`, `pwshot.py`, `page.html`).

**Search limit.** Partway through, the shared WebSearch quota for this run ran out. After that I fetched known primary URLs directly. Three things could not be checked:
- How Remotion concatenates chunk audio without seams.
- The official Reels/Shorts/TikTok safe-zone pixel values.
- The licence of Noto *animated* emoji.

These are marked UNVERIFIED below.

**Evidence labels**
- **[M]** I measured it myself on this machine.
- **[P]** Primary source: official docs, licence text, protocol definition or registry.
- **[S]** Secondary or third-party source, such as an issue tracker or blog.
- **[E]** My own estimate or inference.

---

## 0. Summary

1. **"Seek, don't play" is the industry pattern for rendering web motion into a video.** Remotion, Motion Canvas, Revideo and HeyGen's HyperFrames all work this way: every visible property is a pure function of time. The renderer sets the time, waits until the page has settled, and captures the frame. Aadhi already has the hard part, because `schedule.js` `sceneStateAt(scene, t)` is pure. The missing step is going from discrete states to *every frame* [P][S].
2. **Virtual-time tricks are fragile and don't buy speed.** Here "virtual-time tricks" means the Playwright clock, CDP virtual time and `HeadlessExperimental.beginFrame`.
   - `beginFrame` works only in chrome-headless-shell. I confirmed it works on build 153 on Windows, but only through raw CDP, because Playwright cannot attach to targets created with BeginFrameControl.
   - In my probe, CSS animation time did **not** follow `frameTimeTicks` on Windows. That matches HyperFrames' statement that this determinism works on Linux only.
   - It is no faster: 33 ms/frame versus 36 ms/frame for JPEG at 1080p [M].
3. **Image encoding is the bottleneck, not the page.** At 1920×1080, one chrome-headless-shell worker took [M]:

   | Capture method | ms per frame |
   |---|---|
   | CDP `Page.captureScreenshot` JPEG q90 | ~36 |
   | CDP PNG with `optimizeForSpeed` | ~110 |
   | CDP PNG without `optimizeForSpeed` | ~363 |
   | Playwright `page.screenshot(type="png", omit_background=True)`, the call Aadhi uses today | **~390–400** |

   Remotion has just shipped a custom Chromium build that skips image encoding altogether ("shared-memory capture", v4.0.534, Oct 2026). That confirms where the cost sits [P].
4. **Parallelism.** On this laptop (i7-13650HX, 14 cores / 20 threads), steady-state aggregate throughput for DOM/CSS scenes was 26.5 fps with 1 worker, 53 with 4 and 59 with 6. With a full-screen WebGL shader background at half resolution it was 12.6, 32 and 36 fps [M].
5. **WebGL in headless Chromium runs on SwiftShader, which uses the CPU.** A 5-octave noise background at 1080p added about 80 ms per frame. At half resolution it added about 28 ms, and at quarter resolution about 18 ms. `backdrop-filter: blur(16px)` added about 22 ms [M]. Chromium is removing the *automatic* SwiftShader fallback for WebGL, so the explicit flags `--use-angle=swiftshader --enable-unsafe-swiftshader` will be needed [P].
6. **Render time at 30 fps.**

   | Video | This laptop | 8-vCPU Linux server [E] |
   |---|---|---|
   | 5-min 16:9 | ~3.8 min (DOM backgrounds) to ~5.8 min (WebGL throughout) [M+E] | ~6–10 min |
   | 3.5-min 9:16 reel | ~3.0–4.6 min | ~5–8 min |

7. **Licences.**
   - **GSAP:** don't adopt. Its free licence is proprietary. It forbids use in tools that let users build animations visually without code, and Webflow can end it at its discretion. Aadhi's Studio editor is exactly that kind of tool [P].
   - **Remotion:** don't adopt. It is source-available, and a company licence costs $0.01 per render with a $100/month minimum for "prompt-to-video apps" [P].
   - **anime.js v4.5 (MIT):** adopt. It has timeline `seek()`, `splitText`, SVG drawing, morphing and motion paths, and its bundle contains no eval.
   - **lottie-web 5.13 light build (MIT):** adopt. It contains no eval, so it works under the strict CSP.
   - **Paper Shaders 0.0.81 (Apache-2.0):** adopt. `setFrame(ms)` gives deterministic frames.
   - **Icons and emoji:** Lucide (ISC), Tabler (MIT), Phosphor (MIT), Material Symbols (Apache-2.0), Fluent Emoji (MIT), Noto Emoji (OFL/Apache) [P].
8. **Video inside a web stage is not frame-accurate.** Remotion and HyperFrames both pre-extract frames with ffmpeg and show them as images during the render. Aadhi should do the same, or keep compositing in ffmpeg as it does today [P].

---

## 1. Starting point in the repo (read-only)

- **Render page.** `web/render.html` plus `web/js/render/render.js` render a 1920×1080 stage at DPR 1. The page is transparent. It exposes `aadhiRender.states(scene)` (the times where the state changes) and `aadhiRender.show({scene, t})`.
- **Capture.** `aadhi/compose/screenshot.py` takes **one transparent PNG per visual state** with Playwright's `page.screenshot(type="png", omit_background=True, animations="disabled")`.
- **Composite.** ffmpeg places the mascot clip underneath, puts media into rectangles and cross-fades between state PNGs in premultiplied alpha (`frames.py`, `ffmpeg.py`).
- **Chromium flags** (`CHROMIUM_ARGS`) include `--disable-gpu`, `--force-color-profile=srgb`, `--font-render-hinting=none` and `--disable-lcd-text`.
- **Colour.** BT.709 conversion and `setparams` tagging are already done correctly (`ffmpeg.py` `BT709_*`).
- **Mascot clips** (`video_template/*_clean.mp4`) are **1280×720 at 24 fps, 8 s long** (checked with ffprobe).

**What this means.** The state-based compositor can only cut or cross-fade between still states, so continuous motion is impossible with it. Kinetic text, drawn lines, moving particles, animated gradients and camera moves all need per-frame capture. The state renderer should stay as the engine for the classic "Aadhi" tab, as requirement 7 asks.

---

## 2. Ways to make browser capture deterministic

| Approach | How it works | Verdict for Aadhi |
|---|---|---|
| **Explicit time ("seek, don't play")** | The page exposes `seek(t)`. Every library is driven to time *t*: WAAPI `currentTime`, `timeline.seek`, `goToAndStop`, a shader uniform. Then capture. Remotion: each component must produce the same output for the same frame, frames may render out of order across tabs, and wall-clock-driven animation causes flicker [P]. HyperFrames: the page exposes `window.__hf.seek(t)` and the renderer never calls `play()`. Frame time is quantised to `round(t*fps)/fps`, and libraries plug in through a `FrameAdapter` (GSAP, Lottie, WAAPI, Three.js, anime.js) [P, heygen.com Jun 22 2026]. | **Adopt.** Aadhi already computes `sceneStateAt(scene, t)` as a pure function. This works on Windows and Linux, in parallel and out of order. |
| Playwright Clock (`page.clock`, since v1.45) | Fakes `Date`, `setTimeout`/`setInterval`, `requestAnimationFrame`, `requestIdleCallback`, `performance` and `Event.timeStamp`. Provides `install`, `pause_at`, `run_for`, `fast_forward` and `set_fixed_time` [P]. The docs don't mention CSS animations, WAAPI or `<video>`, so they are presumably not controlled [E]. | Use only as a guard that freezes stray timers from third-party code in render mode. It is not the mechanism for time. |
| CDP `Emulation.setVirtualTimePolicy` | Experimental. Policies are `advance`, `pause` and `pauseIfNetworkFetchesPending`, with an optional budget [P]. | Avoid. Experimental, it complicates waiting on network and fonts, and it adds nothing over explicit seeking. |
| CDP `HeadlessExperimental.beginFrame` | Experimental and headless-only. The target must be created with BeginFrameControl. It takes `frameTimeTicks` and `interval` and can return a screenshot (JPEG, PNG or WebP, with `optimizeForSpeed`) [P]. It needs `--deterministic-mode`, `--enable-begin-frame-control`, `--run-all-compositor-stages-before-draw` and related flags [P/S]. | **Linux-only optimisation; don't depend on it.** See §2.1. |
| CDP `Animation` domain | Experimental. `setPlaybackRate(0)` freezes the document timeline; `seekAnimations`, `setPaused` and `setTiming` [P]. | Useful as a backstop: freeze any CSS animation you don't control. Own code should seek through WAAPI instead. |
| Screencast (`Page.startScreencast`, Playwright `recordVideo`, `page.screencast` since 1.59) | Pushes frames when content changes, at wall-clock speed, using `everyNthFrame` and acks [P]. | Reject. Not frame-accurate and not deterministic. |

### 2.1 beginFrame test on Windows [M]

- **Setup.** chrome-headless-shell 153.0.8010.12 with the HyperFrames flag set. The target was created with `Target.createTarget({enableBeginFrameControl:true})`.
- **It works.** Each call returned a screenshot and `hasDamage = true` for all 90 frames.
- **Playwright can't drive it.** The target is invisible to Playwright (`pages = 0`), so it needs a separate raw-CDP client, for example `websockets`.
- **Speed is the same.**

  | Method | JPEG q90 | PNG |
  |---|---|---|
  | `beginFrame` | 33.4 ms/frame | 136 ms/frame |
  | `captureScreenshot` | 36 ms/frame | 110 ms/frame |

- **CSS animation time did not advance on Windows.** After injecting `@keyframes` and stepping `frameTimeTicks` by 33.3 ms, `document.getAnimations()[0].currentTime` stayed at 66.7 ms. `performance.now()` followed the wall clock instead.
- **Agreement with other sources.** HyperFrames says BeginFrame determinism works on Linux only, with Windows and macOS falling back to `captureScreenshot`. Its BeginFrame mode also needs a warm-up loop (fonts would otherwise hang) and polling without rAF [P]. A third-party HyperFrames troubleshooting page claims Chromium 147+ removed `beginFrame` [S]. My test on headless-shell **153** contradicts that for the headless-shell binary, so the claim probably refers to new headless or full Chrome.
- **Long-term risk.** puppeteer-capture (MIT) rates its dependence on `HeadlessExperimental` as moderate-to-high risk beyond three years [S].

### 2.2 How to seek each piece of the stage

| Piece | Live mode, driven by the narration clock | Render mode, at time *t* | Source |
|---|---|---|---|
| WAAPI / CSS animations | `el.animate(...)`, then `pause()`; set `currentTime` each rAF. `document.getAnimations()` returns CSS animations, CSS transitions and WAAPI animations [P]. | The same `currentTime` assignment | MDN |
| anime.js v4 | `createTimeline({autoplay:false})`, then `tl.seek(ms)`. Set `engine.useDefaultMainLoop=false` and call `engine.update()` from Aadhi's own loop [P]. | `tl.seek(t*1000, true)` (`muteCallbacks`) | animejs.com |
| Motion (motion.dev) | `animate()` returns controls whose `time` can be read and set [P]. | `controls.time = t` | motion.dev |
| GSAP (reference only) | Paused timeline plus `totalTime(t)`; or remove `gsap.updateRoot` from the ticker and call `gsap.updateRoot(t)` [P]. | Same | gsap.com |
| lottie-web | `anim.goToAndStop(frame, true)`, where `true` means the value is a frame number [P]. | Same | github airbnb/lottie-web |
| dotLottie | `setFrame(n)` [P] | Same | LottieFiles docs |
| Paper Shaders | Constructed with `speed=0` and `frame`. `setFrame(ms)` sets `u_time` and **renders synchronously**. With `speed≠0` it runs its own rAF loop, and it pauses when the tab is hidden or the element is off screen [P, source read: `shader-mount.ts`]. | `setFrame(t*1000)` | github paper-design/shaders |
| Three.js | No `THREE.Clock`; compute the pose from *t* and call `renderer.render()` inside `seek` | Same; `preserveDrawingBuffer:true` | [E] |
| Particles | Closed form: `p(t) = p0 + v·t (mod bounds)` with a seeded PRNG | Same | [E]; tsParticles runs its own loop |
| `<video>` | Normal playback, kept in sync with the clock | **Pre-extracted frames**: ffmpeg writes a JPEG sequence at the output fps, shown as `<img>` per frame. Remotion's `<OffthreadVideo>` extracts frames outside the browser with FFmpeg [P]. HyperFrames extracts every `<video>` to JPEGs; a 5-s clip at 30 fps becomes 150 files [P]. | |
| Captions / karaoke | Word timings from TTS | Same pure function | Existing `schedule.js` |

---

## 3. Throughput benchmarks [M] and other data points

**Setup.** Windows 11, Intel i7-13650HX (14 cores / 20 threads), 16 GB RAM. Playwright 1.63 Python with chromium-headless-shell 153. Repo-style flags plus `--disable-gpu`.

**Test page** (`page.html`): animated linear-gradient background, 60 particles, 17 letters of kinetic title text, 3 sliding cards with box-shadows, and an SVG path being drawn. Each frame calls `seek(t)` through `page.evaluate` and then `Page.captureScreenshot`. Times are medians of 90–240 frames after 5 warm-up frames, one run each.

| Configuration (1920×1080 unless noted) | ms/frame | fps per worker | Average frame size |
|---|---|---|---|
| Playwright `page.screenshot` PNG + `omit_background` + `animations="disabled"` (today's call) | **402.8** | 2.5 | – |
| Playwright `page.screenshot` PNG + `omit_background` | 389.5 | 2.6 | – |
| Playwright `page.screenshot` JPEG q90 | 47.9 | 20.9 | – |
| CDP PNG | 362.8 | 2.8 | 779 KB |
| CDP PNG `optimizeForSpeed` | **110.2** | 9.1 | 1,670 KB |
| CDP PNG `optimizeForSpeed`, transparent default background | 112.5 | 8.9 | 1,670 KB |
| CDP JPEG q90 | 36.4 | 27.4 | 112 KB |
| CDP JPEG q90 `optimizeForSpeed` | 37.1 | 27.0 | 112 KB |
| CDP JPEG q80 `optimizeForSpeed` | 35.0 | 28.6 | 75 KB |
| CDP WebP q90 | 159.9 | 6.3 | 49 KB |
| JPEG q90, `--disable-frame-rate-limit` | 39.7 | 25.2 | |
| JPEG q90, `--disable-frame-rate-limit --disable-gpu-vsync` | 38.8 | 25.7 | |
| JPEG q90, **1080×1920** | 40.9 | 24.4 | 104 KB |
| PNG `optimizeForSpeed`, **1080×1920** | 112.7 | 8.9 | 1,601 KB |
| JPEG + `backdrop-filter: blur(16px)` on 3 cards | 58.7 | 17.0 | |
| JPEG + WebGL2 fbm background, full 1920×1080 (SwiftShader) | 114–117 | 8.5–8.8 | |
| Same, canvas at 0.5 scale | 64.6 | 15.5 | |
| Same, canvas at 0.25 scale | 54.0 | 18.5 | |
| `beginFrame` JPEG q90 (raw CDP) | 33.4 | 29.9 | |
| `beginFrame` PNG | 136.0 | 7.4 | |
| End to end: JPEG piped into ffmpeg (libx264 veryfast, crf 18), one worker | 48.8 | **20.5** | |

**Parallel workers** (separate browser processes, JPEG q90, steady state):

| Workers | DOM/CSS stage | WebGL background at 0.5 scale |
|---|---|---|
| 1 | 26.5 fps | 12.6 fps |
| 4 | 53.1 fps | 31.6 fps |
| 6 | 59.3 fps | 36.4 fps |

Scaling is clearly sub-linear past about 4 workers on this 6P+8E-core CPU.

**ffmpeg alone [M].**

| Job | Speed |
|---|---|
| libx264 veryfast, 1080p30 (smooth gradients) | 203 fps |
| libx264 medium, same input | 74.5 fps |
| Overlay a 1080p clip on a `gradients` source, then x264 veryfast | 92 fps |

Encoding is not the bottleneck when it runs in parallel with capture.

**Other data points**
- **Remotion, 1080×1920 30 fps with a full-length `<OffthreadVideo>`** (issue #4949): 9.5 fps on 16 vCPU, 14 fps on 32 vCPU, about 16 fps on 64–224 vCPU. Scaling is sub-linear and video frame extraction is a bottleneck [S].
- **Remotion, 1080p60 on 16 cores, concurrency 6** (issue #11911, 2 Oct 2026): 21.9 fps on average. It fell to 14.5 fps because of an O(n²) asset-rebuild bug. With the fix, 38.7–45 fps [S]. **Lesson:** watch per-chunk fps and recycle pages.
- **Small pages (320×180):** per-frame capture is capped by Chrome's 60 Hz frame cadence at about 51 ms. `--disable-frame-rate-limit` brings it to about 34 ms. Adding `--run-all-compositor-stages-before-draw` brings it to about 4 ms, but Chrome then spins at about 3.3 cores while idle [S, nyaucast #747, Chrome 154 headless-shell on Mac]. At 1080p, encoding dominates and the flag made no difference [M].
- **Remotion defaults:** JPEG at the browser's default quality of 80. PNG only for transparency. Concurrency defaults to half the CPU threads. Remotion recommends replacing GPU-heavy CSS (shadows, gradients, filters) with precomputed images on GPU-less cloud machines [P].
- **Remotion shared-memory capture** (v4.0.534): bitmaps go straight to FFmpeg without JPEG or PNG encoding. It needs Remotion's own Chromium 157 build and is not available on Windows [P].

---

## 4. Recommended rendering architecture ("motion engine")

```
Screenplay → Timeline (unchanged) → MotionPlan (new: per scene a list of typed motion presets, palette,
             background preset, transition, caption style, all data, no code)
                       │
          ┌────────────┴──────────────────────────────┐
     Live player (web)                           Render worker (Python)
  narration <audio> = master clock          1. preflight: fonts, WebGL probe, media pre-extract
  rAF → t → stage.seek(t)                   2. chunk plan: scene-aligned chunks of 240–600 frames
  (same stage code)                         3. W workers (one chromium-headless-shell each):
                                               for f in chunk: t=f/fps → await stage.seek(t)
                                               → CDP captureScreenshot(jpeg,q92,optimizeForSpeed)
                                               → bytes → ffmpeg image2pipe (mjpeg) → chunk.mp4
                                            4. ffmpeg-only segments: Aadhi intro/recap clip, teacher
                                               video inserts, cut/trim (no browser)
                                            5. concat demuxer -c copy (identical encode params)
                                            6. audio: ONE mix of narration + BGM + SFX for the whole
                                               edited timeline (ducking, loudnorm), muxed once
                                            7. QA (existing qa.py: frame count, tags, audio)
```

### 4.1 Stage contract (render mode)

```
aadhiStage.ready                                  // fonts (document.fonts.load for each family/weight), MathJax, images decoded
aadhiStage.seek(tSeconds) → Promise<{ frameKey, settled:true }>
aadhiStage.static(t0, t1) → boolean               // optional: nothing changes in [t0,t1) → reuse previous frame bytes
aadhiStage.mediaPlan() → [{src, rect, from, to}]  // video frames to pre-extract
```

Inside `seek`:
1. Quantise: `t = Math.round(t*fps)/fps`.
2. Update the DOM: transforms and opacity, batched writes, no reads in between.
3. Seek every adapter: anime.js timelines, WAAPI, Lottie, shaders, three.js.
4. Wait for images not yet decoded (`img.decode()`) and MathJax idle.
5. Resolve after **one** rAF. The existing contract waits two; with `captureScreenshot` the capture already waits for a fresh frame.

The live player calls the same `seek` from its rAF loop, using the existing `clock.js` time.

### 4.2 Parallel range rendering and seams

- **Split at scene boundaries,** keeping each chunk between 240 and 600 frames. Long scenes are split mid-scene; that is safe because every frame is a pure function of *t*.
- **Identical encoder settings in every chunk:** codec, profile, pix_fmt, fps, timebase and colour tags. Then concat with `-f concat -c copy`. Each chunk starts with an IDR frame by construction.
- **Audio is never rendered per chunk.** One mixed track is muxed at the end. This avoids AAC priming gaps at chunk joins. That rationale comes from AAC framing [E]; I could not verify Remotion's own approach because of the search limit.
- **Cache by content.** Chunk cache key = hash(chunk frames' state inputs, stage bundle hash, fonts, flags). The repo's `workspace.py` already caches by content, and editing one scene re-renders only its chunks.
- **Recycle each page every ~1,800 frames,** and whenever its fps drops below half of its first-chunk fps (see the Remotion slowdown bug above).

### 4.3 External media, cuts, Aadhi intro and the vertical format

- **Teacher-inserted external videos** (requirement 8). Use ffmpeg-only segments:
  1. Trim with `-ss/-to` after the input (accurate) or `trim/atrim`.
  2. Scale to fit, padding over a blurred, scaled copy of the same clip (`split → scale=cover → gblur → overlay`).
  3. `fps=30`, `yuv420p`, BT.709 tags; audio at 48 kHz stereo, loudness-matched.
  4. Concat with the rendered chunks.
  5. Only allow sources the teacher has rights to (uploads or direct file URLs). Downloading from YouTube conflicts with YouTube's Terms of Service [E].
- **Cuts.** Model them as a **time-remap table** (output time → source time, piecewise). The live player and the renderer both read it: the renderer skips frames in cut ranges, and the audio mix uses `atrim`+`concat` with 10–20 ms `afade` at every join to avoid clicks. No re-render is needed for a cut [E].
- **Aadhi intro/recap clips** (1280×720, 24 fps).
  - 16:9: upscale 1.5× to 1080p and convert with `fps=30` (frame duplication gives mild judder, acceptable for 5–10 s).
  - 9:16: avoid a full-height centre crop. It would need a **2.67× upscale** and look soft. Use either a 1080-wide 16:9 band (a 0.84× downscale, sharp), or a 720×720 centre crop scaled to 1080×1080 (1.5×), over an animated background with kinetic title text.
  - Better long term: commission 30-fps vertical Aadhi clips.
- **Title text over the Aadhi clip.** Either keep the existing transparent-overlay path (240 frames × 110 ms ≈ 26 s on one worker), or pre-extract the clip frames into the stage and capture JPEG, as in §2.2.

### 4.4 Render time estimates (30 fps)

**Assumptions** [M+E]:
- Capture throughput as measured in §3.
- +25% for x264 running alongside (from the measured end-to-end 20.5 fps versus 27 fps capture-only).
- About 35–45 s fixed cost: browser boot, fonts/MathJax, media extraction, concat, audio mix, mux, QA.
- The 8-vCPU figures assume about 0.55× the laptop's throughput. That factor is not measured.

| Video | Frames | Stage type | Laptop (i7-13650HX, 6 workers) | 8-vCPU Linux (4 workers) [E] | 16-vCPU Linux [E] |
|---|---|---|---|---|---|
| 5-min 16:9, 1920×1080 | 9,000 | DOM/CSS backgrounds | **~3.8 min** | ~6.5 min | ~4 min |
| | | 20% of time with WebGL background | ~4.2 min | ~7 min | ~4.5 min |
| | | WebGL background throughout (0.5 scale) | ~5.8 min | ~10 min | ~6 min |
| 3.5-min 9:16, 1080×1920 | 6,300 | DOM/CSS | **~3.0 min** | ~5 min | ~3 min |
| | | 20% WebGL | ~3.3 min | ~5.5 min | ~3.5 min |
| | | WebGL throughout | ~4.6 min | ~8 min | ~5 min |

Scaling rules [E]:
- 24 fps costs 0.8×; 60 fps costs 2×.
- A 720p draft costs about 0.5× (2.25× fewer pixels, partly offset by fixed costs).
- A static-run dedupe (§6, rule 11) could save 20–50% in scenes with long holds, but only when the background is also static.

---

## 5. GPU/WebGL in headless Docker, fonts, colour, audio

**WebGL**
- With `--disable-gpu`, headless-shell 153 on Windows still produced a WebGL2 context backed by SwiftShader. The renderer string was "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)))". The host's RTX 4060 was never used in headless-shell [M].
- Chromium's blink-dev "Intent to Remove: SwiftShader Fallback" (Feb 2025) says WebGL context creation will *fail* instead of falling back. The opt-in is `--enable-unsafe-swiftshader`, recommended only for trusted content. Samsung's policy page puts the hard failure at M139; Edge's policy page mentions 144 [P/S].
- **Action:** pass `--use-angle=swiftshader --enable-unsafe-swiftshader` explicitly whenever the stage uses WebGL. Add a WebGL2 probe to `compose/capabilities.py` that records `UNMASKED_RENDERER_WEBGL`. When it fails, fall back to CSS-gradient backgrounds instead of failing the render.
- **With a real GPU in production:** Remotion recommends `angle-egl` on Linux cloud GPUs, `swangle` (SwiftShader through ANGLE) without a GPU, and warns `angle` can leak memory on long renders [P]. SwiftShader is CPU-bound, so keep shader cost at or below ~30 ms/frame. Measured: about 28 ms with a 5-octave fbm at 0.5 scale.
- **Security:** `--enable-unsafe-swiftshader` raises risk only for untrusted content. The render page loads only Aadhi's bundle plus typed data. Keep the Chromium sandbox, route allow-list and proxy as they are today.

**Fonts**
- Vendor WOFF2 files only (OFL fonts). Call `document.fonts.load()` for every family, weight and script in use before `ready`. The existing `web/js/render/fonts.js` already preloads script fonts.
- **Emoji should be images,** not font glyphs. Windows renders Segoe UI Emoji and Linux renders Noto Color Emoji, so preview and MP4 would differ. Use Fluent Emoji (MIT) or Noto emoji SVG/PNG (Apache-2.0).
- **Golden-image determinism tests must run in the Linux container only.** Windows (DirectWrite) and Linux (FreeType) rasterise text differently. On Windows, compare with SSIM ≥ 0.98 [E].

**Colour**
- Chrome's JPEG screenshots are 4:2:0, **full-range BT.601 (JFIF, ffprobe `yuvj420p`, `bt470bg`)** with an embedded sRGB ICC profile [M].
- Convert them explicitly with `scale=in_color_matrix=bt601:in_range=full:out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709`. Verified with ffprobe: all four tags read bt709/tv [M].
- **Pitfall:** passing only `-color_primaries/-color_trc` as encoder options left primaries and trc "unknown" in my first encode. Use `setparams`, as the repo already does.
- Keep `--force-color-profile=srgb`.
- JPEG 4:2:0 followed by x264 4:2:0 softens small saturated red or blue text. Use q≥90, a minimum text size, and no pure-red thin strokes on dark backgrounds. Use PNG `optimizeForSpeed` (3× slower) only for scenes flagged "fine colour text".

**Audio**
- Build audio from the Timeline, never from capture timing: narration, BGM, SFX (whooshes at transition times taken from the same schedule) and ducking (the existing `sidechaincompress`).
- The audio and video then cannot drift, and parallel chunks never touch audio.

---

## 6. Libraries and licence verdicts

Versions and dates are from the npm registry on 2026-10-09 [P]. Licence texts were read from the GitHub LICENSE files [P].

| Library | Version (date) | Licence | Verdict | Notes |
|---|---|---|---|---|
| **anime.js** | 4.5.0 (2026-06-22) | MIT | **ADOPT (core tween engine)** | `createTimeline({autoplay:false})` with `seek(ms)`. Manual engine loop (`useDefaultMainLoop=false`, `engine.update()`). `splitText` (since 4.1) splits lines, words and characters. SVG: `createDrawable`, `morphTo`, `createMotionPath`. Has a Three.js adapter. ESM min bundle is about 119 KB with no `eval`/`new Function` (grep) [P/M]. |
| Motion (motion.dev) | 14.0.0 (2026-10-02) | MIT (© Motion B.V.) | Optional | Seekable `controls.time`. Its `splitText` is **Motion+ (paid)**. Strongest with React; Aadhi uses vanilla ES modules [P]. |
| **GSAP** | 3.15.0 (2026-04-13) | "Standard 'no charge' licence" (Webflow), classed Proprietary Free by ScanCode | **AVOID** | Prohibits use in tools that let users build visual animations without code. Defines competing products as tools that create, edit or manage animations through a visual interface. Webflow may terminate the licence at its discretion. Effective 2025-04-30, last modified 2025-05-30 [P]. Aadhi's Studio has a visual editor with animation controls, so the risk is material. |
| **lottie-web** | 5.13.0 (2025-05-21) | MIT | **ADOPT the light build** | Use `build/player/esm/lottie_light.min.js` (SVG renderer). It has no `eval`/`new Function`; the full `lottie.js` uses one for expressions [M, grep]. The maintainer confirmed expressions use eval [S]. Seek with `goToAndStop(frame, true)`. Releases are slow (last May 2025). |
| dotLottie web | 0.81.0 (2026-10-06) | MIT (ThorVG engine also MIT) | Optional | WASM engine of about 500 KB, fetched from a CDN by default, so it must be self-hosted. Needs WASM compile permission in the CSP (`'wasm-unsafe-eval'`) [P/S]. `setFrame(n)`. |
| Rive runtime (`@rive-app/canvas`) | 2.44.1 (2026-10-09) | MIT (runtime) | Optional (Aadhi intro rig) | The editor's free plan exports with a Rive splash screen. Cadet is $9/seat/month, Voyager $32, Enterprise $120 [P]. WASM, so same CSP note as dotLottie. |
| PixiJS | 8.22.0 (2026-10-01) | MIT | Optional | WebGL, so it runs on SwiftShader in the render worker. Use only if DOM, SVG or Canvas2D is not enough. |
| Three.js | 0.186.1 (2026-09-24) | MIT | Keep (already vendored) | Render with explicit *t*. |
| Theatre.js | core 0.7.2 (2024-05-19) | core Apache-2.0, **studio AGPL-3.0** | AVOID | Stale. Development moved to a private repo [P]. |
| Remotion | 4.0.534 (2026-10-07) | Remotion License (source-available) | AVOID as a dependency | Free for individuals and companies with up to 3 employees. Company licence: Creators $25/seat/month; Automators $0.01/render with a $100/month minimum, aimed explicitly at "prompt-to-video apps"; Enterprise from $500/month [P]. It would also mean a React rewrite. Borrow its ideas instead. |
| Motion Canvas | 3.17.2 (2024-12-14) | MIT | AVOID | Stale since Dec 2024. Canvas-only. |
| Revideo | 0.11.0 (2026-07-10) | MIT | Reference only | Headless rendering and worker splitting [P]. |
| **HyperFrames** (HeyGen) | 0.8.143 (2026-10-08) | Apache-2.0 | **Reference architecture**; external use optional | HTML/GSAP-first, Node ≥ 22. Shows `seek()`, the `FrameAdapter` pattern, video pre-extraction and parallel chunk concat [P]. About 59.6k stars [S]. |
| **Paper Shaders** (`@paper-design/shaders`) | 0.0.81 (2026-09-17) | Apache-2.0 (LICENSE checked) | **ADOPT (backgrounds)**, pin the version | 30 shaders: mesh-gradient, static-mesh/radial-gradient, grain-gradient, warp, swirl, waves, voronoi, god-rays, neuro-noise, perlin/simplex noise, dot-grid, metaballs, smoke-ring, water, liquid-metal, halftone, paper-texture, and more. `speed=0` + `setFrame(ms)` is deterministic. Defaults to `minPixelRatio=2`; set it to 1 and cap `maxPixelCount` for SwiftShader. Needs WebGL2. Keep the NOTICE file [P/M]. |
| webgl-noise (Ashima/Gustavson) | – | MIT | ADOPT for custom GLSL | Simplex and classic noise. |
| gl-transitions | 1.x | MIT (123/125 transitions MIT, 1 BSD-3, 1 BSD-2) [M] | ADOPT for image/video panel transitions in WebGL | `InvertedPageCurl` is BSD-3 and `StereoViewer` is BSD-2; keep their notices. |
| tsParticles | 4.4.0 | MIT | Avoid in the render path | Runs its own rAF loop and isn't seekable. Write closed-form particles instead. |
| Mediabunny | 1.61.3 (2026-10-05) | MPL-2.0 (file-level copyleft) | Optional | In-browser WebCodecs muxing for client-side draft export. Unmodified use is fine. |

**Icons, emoji, illustrations, fonts** (licence files read [P])

| Asset set | Licence | Verdict |
|---|---|---|
| **Lucide** | ISC (© 2026 Lucide), plus MIT for icons derived from Feather (listed in LICENSE) | ADOPT; ship the LICENSE |
| **Tabler Icons** | MIT (© 2020–2026 Paweł Kuna) | ADOPT |
| **Phosphor** (`@phosphor-icons/core`) | MIT (© 2023) | ADOPT. Six weights are useful for emphasis animation. |
| **Material Symbols / Icons** | Apache-2.0 | ADOPT |
| **Fluent Emoji** (Microsoft) | MIT | ADOPT (colour, flat and 3D PNG/SVG) |
| **Noto Emoji** | Fonts OFL-1.1; tools and most images Apache-2.0; flags public domain | ADOPT (images) |
| Twemoji (jdecked fork) | Graphics CC-BY 4.0 | OK only with attribution in credits; prefer Fluent or Noto |
| OpenMoji | CC BY-SA 4.0 | AVOID (share-alike) |
| Noto *animated* emoji | UNVERIFIED (believed CC BY 4.0) | Verify before use |
| **Open Peeps** | CC0 | ADOPT (characters for scenarios) |
| **Open Doodles** | CC0 | ADOPT |
| unDraw | Custom: free commercial use without attribution, but no packs, no replicating a service, no automated download, no AI training, and the site says not to add them to an app | AVOID bundling |
| LottieFiles free animations | "Lottie Simple License (FL 9.13.21)": commercial use and modification allowed, attribution optional; no compiling files to build a competing service; derivatives under the same terms | OK for a small hand-curated set with provenance recorded; never bulk import |
| Google Fonts (Lexend, Inter, Poppins, Baloo 2, Noto Sans Tamil/Devanagari, …) | Mostly OFL-1.1 | ADOPT (vendored WOFF2) |
| Motion tokens: Material 3 (`@material/web`, Apache-2.0) | Durations 50–1000 ms; emphasized `cubic-bezier(0.2,0,0,1)`, emphasized-decelerate `(0.05,0.7,0.1,1)`, emphasized-accelerate `(0.3,0,0.8,0.15)` [P] | ADOPT as constants |

---

## 7. Visual building blocks (what to put on the stage)

These are in priority order, cheapest first, with the measured render cost of each.

1. **Ambient backgrounds.**
   - *CSS layer, almost free (~36 ms/frame including encode):* animated multi-stop linear, radial and conic gradients by hue and angle; slowly drifting radial "blobs" (no CSS blur filter); SVG grid, dot and wave patterns; a static grain PNG at 4–8% opacity.
   - *WebGL layer (+18–28 ms at 0.25–0.5 scale):* Paper Shaders presets such as mesh-gradient, grain-gradient, warp and waves. Use them for intro, chapter and recap cards, or whole scenes when the budget allows.
   - Choose from a catalogue of about 12–20 presets × palettes so that no two consecutive scenes match.
2. **Particle fields.** Closed-form motion of 30–80 dots, sparks or icons, seeded. Use parallax depth (2–3 layers with different speeds and opacity).
3. **Kinetic typography** with anime.js `splitText`:
   - Per-word rise or fade with a 40–80 ms stagger.
   - Per-letter "pop" for titles.
   - Typewriter effect for code.
   - Highlighter sweep (an animated background-size underline) synced to the word that names the term (the existing `sync_cues`).
   - Number count-ups for statistics.
   - Equation reveals: MathJax SVG paths drawn with `createDrawable`.
4. **Diagram motion.** SVG line drawing, `morphTo` between shapes, objects moving along a path, arrows that grow, flow pulses along wires (for example in Ohm's-law circuits), and chart bars that grow. Chart.js must be seeked or drawn by hand, because its `animation:false` mode only shows end states.
5. **Camera moves.** Stage-level `transform: scale/translate` for push-ins (2–6% over a beat) and pans between board regions. Ken-Burns on stills already exists (`kenburns.js`).
6. **Transitions.**
   - *DOM to DOM:* in-stage clip-path circle or iris, diagonal wipe, slide with motion blur faked by a 2–3-copy opacity trail, colour-panel sweep (a full-screen palette panel that wipes in and out), zoom-through.
   - *Media to media or Aadhi clip ↔ stage:* ffmpeg `xfade`, which has 58 built-in transitions in ffmpeg 8.1, including fade, wipe/slide/smooth in four directions, circleopen/close, radial, zoomin, cover/reveal, hblur, pixelize and squeeze [M]. Or gl-transitions in a WebGL canvas for stage-embedded media.
   - For live parity, implement the same small subset in CSS.
7. **Lottie stickers and icons** for reactions, checkmarks and "aha" moments. 1–3 s accents, light build only.
8. **Progress and structure cues.** Chapter bar, beat counter, a "3 key ideas" tracker and a recap checklist. These are high-value signaling devices.

**What the learning research says** (recalled from the literature; I could not re-fetch the abstracts this session, so treat the numbers as approximate):
- Seductive details (interesting but irrelevant material) hurt learning: Sundararajan & Adesope 2020, *Educational Psychology Review*, negative pooled effect.
- Signaling (highlighting what matters) helps: Schneider et al. 2018, *Educational Research Review*, positive effects on retention and transfer.
- Animation beats static graphics on average, with a small effect: Berney & Bétrancourt 2016, *Computers & Education*.

So background motion should be low-contrast and slow, and the motion budget should go into signaling (highlights, arrows, reveals synced to narration), not decoration.

---

## 8. Pitfalls

1. **Wall-clock code anywhere in the stage.** That includes CSS transitions triggered by class changes, `setTimeout`, unseeded `Math.random`, `Date.now`, and libraries with their own rAF loop (tsParticles, Chart.js animations, Paper Shaders with `speed≠0`). Any of these gives flicker and chunk mismatches [P, Remotion].
2. **`page.screenshot(animations="disabled")`.** Playwright stops CSS animations, transitions and WAAPI for the screenshot, so it must not be used in the motion path [P]. It is also the slow PNG path (~400 ms/frame) [M].
3. **PNG without `optimizeForSpeed`** is 3.3× slower than with it [M]. Only alpha layers need PNG.
4. **`<video>` in the stage.** Seeking it is asynchronous and frame-inexact. Pre-extract frames instead [P].
5. **beginFrame.** It needs raw CDP with Playwright, and compositor-time determinism did not hold on Windows [M]. Keep it as an optional Linux path behind a flag.
6. **WebGL on SwiftShader.** It is expensive, and the automatic fallback is going away [M/P]. Probe it, cap shader resolution, and fall back to CSS.
7. **`backdrop-filter`/`filter: blur`.** +22 ms/frame [M], and the current render page already leaves glass blur out for parity. Use pre-blurred images or semi-opaque scrims.
8. **Colour shift in a JPEG pipe.** The input is full-range BT.601. Convert explicitly and verify the tags with ffprobe; QA already checks them [M].
9. **Frame-rate mismatch.** 24-fps Aadhi clips in a 30-fps output show duplicate frames. Keep the clips under 10 s or re-render them at 30 fps.
10. **Vertical reframing of 16:9 assets.** The mascot, Manim videos and AI clips need band or blurred-fill layouts, not crops. A 2.67× upscale looks soft.
11. **Long renders slow down.** Remotion hit an O(n²) per-frame asset rebuild in Oct 2026 [S]. Recycle pages and watch per-chunk fps.
12. **Text and emoji differ between Windows and Linux.** Use image emoji and run golden tests in the container.
13. **CSP.** lottie-web's full build needs `eval`; dotLottie and Rive need `'wasm-unsafe-eval'`. Stick with the light build and in-house GLSL to keep `script-src 'self'` unchanged.
14. **Seizure and comfort limits under "change colours often".** WCAG 2.3.1 forbids more than 3 flashes in any 1-second period. Large saturated-red flashes are the worst case. Palette switches must be cross-fades, not cuts.
15. **Over-parallelising.** Throughput rose only from 53 to 59 fps going from 4 to 6 workers [M]. Remotion found more cores gave diminishing returns [S]. Size the worker pool to physical cores.
16. **Licence drift.** GSAP's licence can be changed by Webflow. Paper Shaders is pre-1.0. Pin versions in `scripts/vendor.mjs` with SRI hashes (already the pattern in `web/vendor/manifest.json`) and keep a THIRD_PARTY_NOTICES file that includes Apache NOTICE files.

---

## 9. Sources

- Playwright Clock docs: https://playwright.dev/docs/clock · Release notes (Clock 1.45; headless-shell default 1.49; Chrome for Testing 1.57; `page.screencast` 1.59; WebP screenshots 1.62): https://playwright.dev/python/docs/release-notes · Issue #33566 (1.49 headless change): https://github.com/microsoft/playwright/issues/33566 · Python Page API (screenshot options): https://playwright.dev/python/docs/api/class-page
- CDP protocol definitions (read 2026-10-09): https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/HeadlessExperimental.pdl · …/Page.pdl · …/Emulation.pdl · …/Animation.pdl
- Chrome headless-shell: https://developer.chrome.com/docs/automation-and-testing/headless-chrome-shell · Chrome headless: https://developer.chrome.com/docs/chromium/headless · Chromium headless README: https://github.com/chromium/chromium/blob/main/headless/README.md · chrome-launcher flags (`--deterministic-mode`): https://github.com/GoogleChrome/chrome-launcher/blob/main/docs/chrome-flags-for-tools.md
- HeyGen, "HTML to Video" (Jun 22 2026): https://www.heygen.com/research/html-to-video · HyperFrames repo: https://github.com/heygen-com/hyperframes · HyperFrames troubleshooting mirror (third party): https://github.com/NousResearch/hermes-agent/blob/main/optional-skills/creative/hyperframes/references/troubleshooting.md
- puppeteer-capture: https://github.com/alexey-pelykh/puppeteer-capture · nyaucast capture timing issue: https://github.com/daiki-beppu/nyaucast/issues/747
- Remotion: https://www.remotion.dev/docs/render-frames · https://www.remotion.dev/docs/flickering · https://www.remotion.dev/docs/performance · https://www.remotion.dev/docs/gl-options · https://www.remotion.dev/docs/offthreadvideo · https://www.remotion.dev/docs/shared-memory-capture · https://www.remotion.dev/docs/renderer/render-media · https://www.remotion.dev/docs/lambda/concurrency · Licence: https://raw.githubusercontent.com/remotion-dev/remotion/main/LICENSE.md · Pricing: https://www.remotion.pro/license · Issues: https://github.com/remotion-dev/remotion/issues/4949 · https://github.com/remotion-dev/remotion/issues/11911 · https://github.com/remotion-dev/remotion/issues/11578
- SwiftShader: https://groups.google.com/a/chromium.org/g/blink-dev/c/yhFguWS_3pM/m/oLrB5up_BwAJ · https://chromium.googlesource.com/chromium/src/+/da170c2267b2201954efe1c8fa5d2d89ab09f7bf/docs/gpu/swiftshader.md · https://developer.samsung.com/browser/policy/enable-unsafe-swift-shader.html · https://learn.microsoft.com/en-my/deployEdge/microsoft-edge-policies/enableunsafeswiftshader
- GSAP licence: https://gsap.com/standard-license · ScanCode entry: https://scancode-licensedb.aboutcode.org/gsap-standard-no-charge-2025.html · Webflow announcement: https://webflow.com/blog/gsap-becomes-free · `gsap.updateRoot`: https://gsap.com/docs/v3/GSAP/gsap.updateRoot()/
- anime.js: https://animejs.com/documentation/engine · https://animejs.com/documentation/engine/engine-methods/update · https://animejs.com/documentation/timeline/timeline-methods/seek · https://animejs.com/documentation/text/splittext · https://animejs.com/documentation/svg · LICENSE: https://raw.githubusercontent.com/juliangarnier/anime/master/LICENSE.md
- Motion: https://motion.dev/docs/animate · https://motion.dev/docs/split-text · LICENSE: https://raw.githubusercontent.com/motiondivision/motion/main/LICENSE.md
- Lottie: https://github.com/airbnb/lottie-web · jsDelivr file listing for lottie-web@5.13.0 · dotLottie CSP/WASM guide: https://github.com/LottieFiles/dotlottie-web/wiki/CSP-and-WASM-Self%E2%80%90Hosting-Guide · dotLottie API: https://docs.lottiefiles.com/en/runtimes/distributions/js/v0.x/api/reference · Lottie Simple License: https://lottiefiles.com/page/license · lottie-web eval discussion: https://lightrun.com/answers/airbnb-lottie-web-thiselementsidestroy-is-not-a-function
- Theatre.js: https://github.com/theatre-js/theatre · Motion Canvas: https://github.com/motion-canvas/motion-canvas · Revideo: https://github.com/redotvideo/revideo · Rive pricing: https://rive.app/pricing
- Paper Shaders: https://github.com/paper-design/shaders (LICENSE; `packages/shaders/src/shader-mount.ts`; `src/shaders/`)
- Licence files: Lucide, Tabler, Phosphor core, Material Design Icons, Noto Emoji (+README), Twemoji LICENSE-GRAPHICS, Fluent UI Emoji, OpenMoji, gl-transitions (+ per-transition licence field from `gl-transitions.json`), tsParticles, PixiJS, rive-wasm, dotlottie-web, HyperFrames, Mediabunny, webgl-noise (all at raw.githubusercontent.com/<repo>/…/LICENSE)
- unDraw: https://undraw.co/license · Open Peeps: https://www.openpeeps.com/ · Open Doodles: https://www.opendoodles.com/
- MDN `getAnimations`: https://developer.mozilla.org/en-US/docs/Web/API/Document/getAnimations
- Material 3 motion tokens (Apache-2.0 source): https://cdn.jsdelivr.net/npm/@material/web@2/tokens/versions/v0_192/_md-sys-motion.scss
- npm registry (versions and dates): https://registry.npmjs.org/<package>
- WCAG 2.2 Understanding 1.4.3 Contrast (Minimum): https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html · 2.3.1 Three Flashes: https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html
- Learning research (citations from memory; abstracts not retrievable this session): Sundararajan & Adesope (2020) doi:10.1007/s10648-020-09522-4 · Schneider, Beege, Nebel & Rey (2018) doi:10.1016/j.edurev.2017.11.001 · Berney & Bétrancourt (2016) doi:10.1016/j.compedu.2016.06.005

---

## DESIGN IMPLICATIONS FOR AADHI

Each rule below is concrete. Numbers marked [M] come from my own measurements; the rest are recommended defaults to tune with `evals/`.

**A. Engines and modes**
1. Add a second render engine, `RENDER_ENGINE=motion` (the new default for the main "Dynamic" tab). Keep `states` (today's compositor) for the "Aadhi classic" tab. Both read the same `Timeline`. Choose per render; never mix engines within one render, except for ffmpeg-only segments.
2. **Frame rate 30 fps for both formats** (`RENDER_FPS=30`). Logical stage 1920×1080 (16:9) or 1080×1920 (9:16), DPR 1. Allow a 720p draft (`scale=0.667`) for previews.
3. **LLMs choose typed motion presets with parameters, never code:** enter/exit/emphasis/background/transition/caption-style IDs plus durations and palette IDs. Add these to the Screenplay/Timeline schema. The CSP stays `script-src 'self'`, with no wasm, unless Rive or dotLottie is adopted later.

**B. Stage contract and determinism**
4. Add `stage.seek(t)` with `t = round(t*30)/30`. It is pure and order-independent: chunks may render frames in any order. It resolves after state application, image decoding and MathJax idle, plus **1 rAF**. No fixed sleeps.
5. **Banned in stage code** (enforce with a lint rule plus a test that renders frames 900→0 in reverse and compares hashes): `setTimeout`/`setInterval` for visuals, `Date.now`, unseeded `Math.random`, CSS `transition` on stage elements, Chart.js animations, and any library rAF loop. Use a seeded PRNG (mulberry32) seeded by `hash(version_id, scene_index)`.
6. **Tween engine: anime.js 4.5.0** (MIT), vendored ESM (~119 KB). Every scene builds `createTimeline({autoplay:false})`. In render mode, `engine.useDefaultMainLoop=false` and `tl.seek(t*1000, true)`. Self-authored CSS keyframes must be driven through WAAPI (`pause()` + `currentTime`). Backstop: `Animation.setPlaybackRate(0)` over CDP in render mode. **Do not adopt GSAP.**
7. Backgrounds via **Paper Shaders 0.0.81** (pinned, Apache-2.0, NOTICE shipped): `speed:0`, `setFrame(t*1000)`, `minPixelRatio:1`. Canvas at **0.5 scale** in render (≤ ~0.52 MP). SwiftShader budget **≤ 30 ms/frame** (measured 28 ms for 5-octave fbm at 0.5 scale [M]). fbm octaves ≤ 4.
8. Lottie: **lottie-web 5.13.0 `esm/lottie_light.min.js`**, SVG renderer, `goToAndStop(frame, true)`. Reject files that use expressions (the importer checks for `x` expression fields). Keep 1–3 s accents.
9. Particles: closed form, **≤ 80 elements** per scene, 2–3 parallax layers, speeds of 10–60 px/s at 1080p, opacity 0.15–0.6.
10. **Video inside the stage:** in render mode, ffmpeg pre-extracts frames at 30 fps, at the panel's display size, as JPEG q90, and shows them via `<img>` per frame. Full-screen inserts are ffmpeg-only segments. The live player plays `<video>` kept in sync with the clock.

**C. Capture**
11. Capture with CDP `Page.captureScreenshot` `{format:"jpeg", quality:92, optimizeForSpeed:true}` on a session per page. About **36 ms/frame at 1080p, ~41 ms at 1080×1920** [M]. Never use Playwright `page.screenshot` in the motion path: it costs **~390–400 ms** for PNG [M], and `animations="disabled"` would kill motion. Use PNG with `optimizeForSpeed:true` (**~110 ms** [M]) only for transparent overlay layers (the Aadhi intro title overlay). Optional **static-run dedupe:** when `stage.static(t0,t1)` is true (no tween active and the background static), reuse the previous JPEG bytes instead of capturing.
12. **Chromium flags for the motion engine:** the current `CHROMIUM_ARGS` plus `--use-angle=swiftshader --enable-unsafe-swiftshader` when the plan contains WebGL; `--disable-frame-rate-limit` is optional (no gain at 1080p [M]). Do **not** use `--run-all-compositor-stages-before-draw` together with `--disable-frame-rate-limit` outside beginFrame mode, because of idle CPU spin [S]. Keep the Chromium sandbox and the network allow-list.
13. Add a WebGL2 capability probe in `compose/capabilities.py` that records `UNMASKED_RENDERER_WEBGL`. If it fails, swap WebGL background presets for their CSS-gradient twins and add a preflight warning, not a failure.
14. Keep `HeadlessExperimental.beginFrame` as an **optional, Linux-only, flag-gated path** (`RENDER_BEGINFRAME=0` by default). It needs a raw-CDP client and gives no speed gain (33 vs 36 ms [M]).

**D. Parallelism, encoding, seams**
15. Workers: `W = clamp(physical_cores − 1, 1, 6)`. That gives 4 on an 8-vCPU box and 6 on a 14-core box (4→6 workers gave +12% [M]). Run one browser process per worker. Recycle a page after **1,800 frames** or when its fps falls **< 50%** of its first chunk.
16. Chunks: scene-aligned, **240–600 frames** (8–20 s), assigned longest-first. Pipe each worker's JPEGs → `ffmpeg -f image2pipe -c:v mjpeg -framerate 30 -i -` → `scale=in_color_matrix=bt601:in_range=full:out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709` → `libx264 -profile:v high -preset veryfast` (draft) or `medium` (final), `-crf 18–20 -g 60`, identical across chunks. Concatenate with `-f concat -c copy`. QA: total frame count must equal the timeline exactly (existing ±0/1 check).
17. **Audio:** one mix for the whole edited timeline (narration + BGM ducking + SFX at schedule times), muxed once. Never encode audio per chunk. Editor cuts use a time-remap table; every audio join gets a **10–20 ms** fade.
18. Cache chunk outputs by content hash (stage bundle hash + chunk inputs + flags) in the existing render workspace. An edit re-renders only the touched chunks.

**E. Formats and Aadhi**
19. **Target lengths:** 16:9 at about 5 min (9,000 frames); 9:16 at 3–4 min (5,400–7,200 frames). **Expected render time** (30 fps, mostly CSS backgrounds): about 4 min (16:9) and 3 min (9:16) on a 14-core workstation [M+E]; about 6.5 min and 5 min on an 8-vCPU server [E]. With WebGL backgrounds throughout, about 1.5× those times. Show the estimate in the UI as `frames / (benchmark_fps × W_efficiency)`, calibrated by a 60-frame benchmark at worker start.
20. **Aadhi only in intro (≤ 10 s) and optional recap (≤ 10 s):** from the existing 1280×720@24 clips, converted with `fps=30`.
    - 9:16: use a 1080-wide 16:9 band (608 px tall, 0.84× scale) or a 720² centre crop to 1080² (1.5×), never full-height (2.67×).
    - Kinetic title text in the free area.
    - Transition out of the clip with ffmpeg `xfade` (circleopen, slideup or zoomin; 0.5–0.8 s), with a CSS twin in the live player.

**F. Visual-variety rules ("nothing monotonous")**
21. **Palette rotation:**
    - A new palette every scene (typically 20–45 s), with a hue shift of at least 30° from the previous scene.
    - Cross-fade over 600–1000 ms, never cut.
    - Each video draws its palette family by seed, so consecutive videos differ.
    - Ambient hue drift ≤ 2°/s.
    - Every palette must pass WCAG contrast ≥ 4.5:1 for body text and ≥ 3:1 for large text over its *worst* background frame. Enforce with a scrim of ≥ 55–70% opacity behind text blocks and check in CI.
22. **Background presets:** at least 16 (8 CSS, 8 shader). Never repeat a preset in consecutive scenes. Use at most 2 shader-heavy scenes per minute on CPU-only render hosts.
23. **Motion timing tokens** (Material 3):
    - Entrances 300–500 ms, emphasized-decelerate `cubic-bezier(0.05,0.7,0.1,1)`.
    - Exits 200–300 ms, emphasized-accelerate `cubic-bezier(0.3,0,0.8,0.15)`.
    - Emphasis pulses 150–250 ms.
    - Scene transitions 500–800 ms.
    - Text stagger 40–80 ms per word, 15–30 ms per letter.
    - Camera push-in 2–6% scale per beat.
    - No element may move faster than about 1 screen-width/s, except during transitions.
24. **Signal, don't decorate:**
    - Each narration beat triggers at least one *meaning-linked* motion: highlight, draw, arrow, count-up or reveal, timed to the word (`sync_cues`).
    - Background motion contrast stays low and slow, because seductive-details research shows decoration hurts learning.
    - At most 3 flashes/s anywhere (WCAG 2.3.1). No full-screen saturated-red flashes.
25. **Minimum text sizes** (render, logical px):
    - 16:9: body ≥ 36 px, captions ≥ 44 px, titles 72–120 px.
    - 9:16: body ≥ 48 px, captions ≥ 56–64 px, at most 2 caption lines, critical content inside the central ~1080×1420 region (UNVERIFIED heuristic; confirm with the platform-specs research).
    - JPEG q ≥ 90. No thin pure-red or pure-blue text on dark backgrounds, because of 4:2:0 chroma loss.

**G. Assets and licences**
26. **Vendor:**
    - Lucide (ISC + Feather MIT), Tabler (MIT), Phosphor (MIT), Material Symbols (Apache-2.0)
    - Fluent Emoji (MIT) and Noto emoji images (Apache-2.0) **as images**
    - Open Peeps and Open Doodles (CC0)
    - OFL fonts as WOFF2, including Noto Sans Tamil/Devanagari for captions
    - gl-transitions (MIT; BSD notices for the 2 non-MIT ones)
27. **Do not ship** GSAP, Remotion, Theatre.js Studio (AGPL), OpenMoji (BY-SA) or unDraw assets. Any LottieFiles assets must be hand-picked, with their URLs recorded.
28. Pin every vendored library by version and SRI in `web/vendor/manifest.json`. Generate `THIRD_PARTY_NOTICES` including the Apache NOTICE files.

**H. Verification**
29. **Determinism test** (in the Linux container), which must give a byte-identical frame hash for each frame:
    - Render the same 300-frame chunk twice.
    - Render it with two different chunkings (1×300 and 3×100).
    - Render it in reverse order.
30. **Parity test:** live-player screenshot versus MP4 frame at 20 random instants, **SSIM ≥ 0.98** (extend `evals/render_parity.py`).
31. **Performance regression:** a CI benchmark on a fixed 10-s scene must reach **≥ 20 fps per worker** at 1080p JPEG for CSS presets and **≥ 12 fps** for shader presets on the reference box. Fail when a preset breaks its ms/frame budget: CSS ≤ 45 ms and shader ≤ 70 ms including capture [M baselines 36/65 ms].
