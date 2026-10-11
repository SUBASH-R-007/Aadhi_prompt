# Research report: where a "dynamic" (mostly mascot-free) video mode fits into the Aadhi EduEngine codebase

Topic key: `codebase`. Date: 2026-10-09. Repo: `C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt`, branch `overhaul/v2`, HEAD `195be39`. The repo was only read; benchmarks ran from the scratchpad.

How sure each claim is:
- **[code]**: read directly in the repo, with the file and line given.
- **[measured]**: measured on this Windows 11 machine with Playwright 1.63.0 (from `.venv`) and ffmpeg 8.1.1. Production runs Debian bookworm with ffmpeg 5.1, so treat these numbers as rough.
- **[doc]**: from the project's own docs.
- **[web]**: an external primary source, listed in the Sources section.
- **[proposal]**: my design choice, not a fact.

---

## 0. Summary

1. **The design is right for the change. The assumptions inside it are not.** Every stage passes data through a few typed contracts: Screenplay → AssetManifest → Timeline → (web player | MP4 renderer). A new mode can come in as optional JSON fields that are left out at their defaults (`exclude_if`). That is exactly how `hidden`, `min_seconds`, `variant`, `sync_cues` and `audio_envelope` were added without changing a single existing lecture's JSON, hashes or MP4. The new mode should follow the same pattern.
2. **Aadhi and 16:9 are hard-coded in about 40 places** (tables in §2 and §3). Most are layout tables, the compositor's "background = mascot clip" slot, branding defaults and the fixed 1920×1080 stage in five files. Nothing in the data model rules out other designs.
3. **The MP4 renderer cannot show continuous motion today.** It takes one transparent screenshot per *visual state*, joined by 0.15 s cross-fades. CSS animations and transitions are switched off in render mode (`web/css/player.css:2343-2349`), and the browser runs with `reduced_motion="reduce"`. Media and the mascot are composited underneath by ffmpeg. Moving text, kinetic type and animated transitions need sampling every frame during motion. The cheapest correct fix is a **hybrid**:
   - keep one screenshot per static state;
   - add "motion windows" sampled every frame at segment-aligned times;
   - make motion a pure function of `t`, like `schedule.js` already is;
   - feed the backdrop through the existing "mascot clip" background slot as a pre-rendered looping video.
   
   Measured: a CDP `Page.captureScreenshot` PNG with alpha and `optimizeForSpeed` takes about **34 ms per frame** at both 1920×1080 and 1080×1920. Playwright's `page.screenshot`, which the code uses today, takes about **61–64 ms**.
4. **The 9:16 format is mostly a geometry change.** `Stage` already accepts a width and height (`web/js/player/stage.js:56-58`), `Timeline` has `width/height/fps` fields, and `Rect.from_stage` scales between stage and output sizes. The work is in:
   - the fixed stage constants and `render.html`;
   - the screenshot viewport;
   - the layout zone table;
   - captions (42 characters × 2 lines);
   - media aspect constants;
   - the Manim profile;
   - three CSS `aspect-ratio: 16/9` containers.
5. **"Monotonous" is built into the data today:**
   - one 80 s `bgm.mp3` looped under every video;
   - one server-default voice, with no loudness normalisation anywhere;
   - a single palette, with 244 literal colours in `player.css`/`panels.css`;
   - one zone table per mascot position;
   - the same logo and title-card intro every time.
6. **The editor already has the right shape for an NLE** (screenplay as the edit list, a timeline strip, split, hide, hold, undo, merge). Inserting external video and cutting it needs:
   - a new `clip` scene type with trim in/out and its own audio;
   - streaming upload and transcoding (uploads are read whole into memory and capped at 50 MB today);
   - trim handles and a razor tool in `timelineStrip.js`;
   - a range-cut command that maps onto existing primitives (delete beats, split, hide, trim).
7. **Existing bug found:** changing a scene's type in the editor silently drops `hidden` and `min_seconds`, because `convertSceneShape` copies only `COMMON_SCENE_KEYS` (`web/js/studio/lib/screenplayEdit.js:83-86, 301-304`). Any new scene-level field (`look`) would be dropped the same way.

---

## 1. Today's architecture as it affects this change

```
Studio #/new (newProject.js, optionsFields.js)
  └─ POST /api/projects (multipart: file + GenerationOptions JSON)  → Project.settings = options
worker generate_lecture (pipeline/orchestrator.py:_generate, lines 746-971)
  ingest → source_scope → brief → plan (prompts/plan.md) → script (per-scene, gen_models + canonicalize)
  → lint/critic/repair → companion → WRITE screenplay (CAS, line 955) → _build_and_finish
     assets.build_assets (TTS per beat, Manim, images/video chain, envelope) → AssetManifest
     compose.timeline.build_timeline → Timeline (stored on version)
Player (web/js/player/player.js) ← GET /api/versions/{vid}/timeline (resolve_urls at serve time)
MP4: render_video job (compose/video.py)
  build_timeline again (include_intro) → Playwright opens /render-frame (web/render.html + render/render.js)
  → aadhiRender.states(i) / show({scene,t}) → transparent PNG per distinct state (compose/screenshot.py)
  → ffmpeg segment per scene: background (mascot clip | static bg) → media in rects → state PNG stream
    (premultiplied cross-fades) → narration + tick/ding PCM (compose/ffmpeg.py build_segment)
  → concat (stream copy) → BGM ducking (sidechaincompress) + chapters + captions → QA (compose/qa.py)
```

Facts that limit what we can do:
- **[code]** The timeline time base is strict. Scenes are contiguous, inner times are relative to the scene, and the fade-in sits inside the scene (`aadhi/schemas/timeline.py:247-263`). Transitions must happen at the start of the incoming scene.
- **[code]** Segments are concatenated by **stream copy** (`build_final`, `ffmpeg.py:1051-1088`), so one scene's pixels can never overlap another's. An incoming transition must be composited inside the incoming segment, using inputs from the outgoing scene.
- **[code]** The render page rebuilds the timeline exactly as the job does (`api/routers/render_internal.py:33-49`), driven by the token claims. If presentation data lives **in the screenplay**, page and job stay identical without new claims. If it lived in render options, a new claim would be needed, like `include_intro`.
- **[code]** Four places call `build_timeline` or `preview_timeline`:
  - `orchestrator._timeline` (line 215);
  - `video._load_job` (line 431);
  - `render_internal._render_timeline` (line 42);
  - `api/routers/versions.py:154` (the editor preview).
  
  Putting everything in the Screenplay means none of their signatures change.
- **[code]** The asset cache uses content keys. `scene_hash` excludes `notes`, `intent`, `hidden` and `min_seconds` (`pipeline/assets.py:202-220`). A purely visual field (`look`) must be added to that exclusion list, so that a theme change never rebuilds narration.
- **[code]** Voice is set per lecture (`assets.voice_config`, `pipeline/assets.py:280-287`): `options.tts_voice`, then `settings.tts_voice`, then the provider default. `SceneAudio` stores `provider`/`voice` per scene (`schemas/manifest.py:38-47`), so a different voice per scene could be added later.

---

## 2. Where Aadhi and the mascot are hard-coded

| # | File:line | Assumption | What the dynamic mode needs |
|---|---|---|---|
| 1 | `aadhi/schemas/screenplay.py:501, 528` | `MascotPosition` Literal; every scene has `mascot_position` (default `left`) | Keep it, and ignore it in dynamic mode except for presenter scenes. Put the dynamic look in a new optional `look` field. |
| 2 | `aadhi/schemas/timeline.py:146-154` | `Layout.mascot_position`, `mascot_cues` | Add optional `Layout.style` / `TimedScene.look`. In dynamic mode `mascot_cues` is irrelevant except for presenter scenes. |
| 3 | `aadhi/schemas/timeline.py:215-224` | `Branding.mascot_clips`, a single `bgm_url` / `bgm_volume`, `static_background_url` | Add `Timeline.music` (a music plan) and per-scene backdrop keys. Keep `Branding` for Aadhi mode. |
| 4 | `aadhi/compose/base.py:43-50, 61-73` | `MASCOT_CLIPS` per position, `STATIC_BACKGROUND`, `LOGO_VIDEO`, `BGM`, `INTRO_LOGO_SECONDS = 5.5`, `INTRO_CARD_SECONDS = 3.0` | Add dynamic constants (backdrop catalogue, presenter limits, short logo sting). Don't change these. |
| 5 | `aadhi/compose/timeline.py:132-146` (`default_branding`) and `:149-168` (`build_intro`) | Every timeline gets mascot clips, one BGM, and logo plus two title cards on the Aadhi background | In dynamic mode build a different intro (an optional ≤ 3 s sting, no cards; scene 1 introduces the session). |
| 6 | `aadhi/compose/timeline.py:622-623, 728-729` | `Layout(mascot_position=scene.mascot_position, ...)`, the cue switch | Fill `TimedScene.look` (resolved theme tokens, template, transition, backdrop, presenter window). |
| 7 | `aadhi/compose/video.py:803-857` | `clip_for(scene)` = the mascot clip for the position; otherwise the static background; mascot loop phase continuity; crossfade on a position change | Reuse the same slot for the **backdrop clip** (theme), which gives backdrop crossfades between themes for free (`previous_background`, `ffmpeg.py:541-552, 919-927`). For presenter scenes, put the mascot clip *on top of* the backdrop with `visible_to`. |
| 8 | `aadhi/compose/ffmpeg.py:513-538` (`background_filter`) | The background is a looped clip, a still or a colour | Keep it. A seamlessly looping backdrop clip uses the `video` branch unchanged. |
| 9 | `aadhi/compose/video.py:912-921`, `ffmpeg.py:1023-1037` | One BGM file, looped, `bgm_delay = intro.logo_duration` | Generalise to a `music_filter(plan)`: several timed inputs, fades, one sidechain. |
| 10 | `web/js/player/layout.js:41-47, 97-109` | Zone table keyed by mascot position (board/side/media spans that avoid the mascot); a fixed caption band `{260, 900, 1400, 140}` | A new table of dynamic zones, per format (landscape/portrait) and template. |
| 11 | `web/js/player/player.js:299-304, 861-865, 641-654` | `MascotController` is always built; every scene sets the mascot position, cue and speech level | When `timeline.presentation.style === 'dynamic'`, build a `BackdropController` and create the mascot controller only for presenter windows. |
| 12 | `web/js/player/mascot.js`, `mascot-state.js`, `presenter-vocab.js` | Mascot behaviour states are computed in `schedule.sceneStateAt` | Leave them alone; they are used only in presenter windows. |
| 13 | `web/render.html:38-56` | Glass-panel colours tuned for compositing over the mascot | Use the dynamic CSS file instead. |
| 14 | `aadhi/pipeline/canonicalize.py:75-82, 423` | `_DEFAULT_MASCOT` per scene type | Leave it. Dynamic mode decides presenter use in a new art-direction stage (§6.2). |
| 15 | `aadhi/pipeline/gen_models.py:237` | The LLM can choose `mascot_position` | In dynamic mode, tell the writer to leave it null (prompt), or remove it from the dynamic gen model. |
| 16 | `aadhi/pipeline/presenter_lint.py`, `validate.py:830` | `layout.mascot_crowds_board` | Skip it in dynamic mode. Add the dynamic presenter lints instead (§6.4). |
| 17 | `aadhi/pipeline/prompts/style.md:4-6`, `scene_board.md:61`, `scene_chapter.md:16`, `scene_simulation.md:44`, `scene_ai_video.md:4,15`, `plan.md:124` | The narrator persona is "Aadhi, the blackbuck mascot"; `mascot_position` hints | The persona can stay as the *narrator's voice*. Add a dynamic and format block (pacing, presenter intro ≤ 10 s, no on-screen references to Aadhi). This needs a `PROMPT_VERSION` bump and an eval run. |
| 18 | `aadhi/pipeline/script.py:74, 177` | AI video prompts must not show the mascot | Still correct. |
| 19 | `aadhi/pipeline/repair.py:52-75` (`carry_over`) | Keeps `mascot_position`, `hidden`, `min_seconds` and overrides on regeneration | Must also keep `look`. Otherwise "regenerate scene" loses the scene's theme and template. |
| 20 | `aadhi/legacy/*` | v1 mascot placement mapping | No change. |
| 21 | `web/js/studio/lib/screenplayEdit.js:71, 83-86, 284` | `MASCOT_POSITIONS`; `COMMON_SCENE_KEYS` | Add `look` (and fix the bug with `hidden`/`min_seconds`, §11). |
| 22 | `web/js/studio/views/editor/inspector.js:113` | The "Mascot (Aadhi)" field on every scene | Show it only for Aadhi-mode lectures and presenter scenes. |
| 23 | `aadhi/api/timelines.py:55-75, 105-111` | The ETag includes the retired-clip maps and `mascot_cues`; `mascot_cues` is applied at serve time | Fine. Store resolved theme tokens in the timeline, so the catalogue never has to be part of the ETag. |
| 24 | `video_template/` | Mascot clips: **1280×720, 24 fps, exactly 8.000 s**; logo 10.0 s; **`bgm.mp3` only 80.04 s** (ffprobe, [measured]) | Presenter windows of ≤ 8 s play one loop with no seam. A 5-minute video loops the current BGM about 3.75 times. |

---

## 3. Where 16:9 and 1920×1080 are hard-coded

| File:line | Hard-coded | Change |
|---|---|---|
| `aadhi/compose/base.py:80-82` | `STAGE_WIDTH = 1920`, `STAGE_HEIGHT = 1080` | Add `stage_size(format)`. Keep the constants as the landscape default. |
| `aadhi/compose/timeline.py:752-754` | `Timeline(width=STAGE_WIDTH, height=STAGE_HEIGHT)` | Use the presentation format (portrait: 1080×1920). |
| `aadhi/compose/video.py:212, 269, 277, 350-353, 581-583, 918, 952-965` | Stage constant for rects, PNG normalising and `state_size`; **output size = `settings.render_width/height`** (global) | Derive the stage size from `tl.width/height`. Derive the output with an `output_size(tl, settings)` helper (portrait swaps the axes; scale by `render_height/1080` for quality). |
| `aadhi/compose/screenshot.py:23, 425-426` | Viewport and screen 1920×1080 | Take them from the timeline. |
| `web/render.html:5, 23-37` | `<meta viewport width=1920, height=1080>`; body and root fixed to 1920×1080 | Size them from the timeline at boot (`render.js`). Use CSS variables instead of literal pixels. |
| `web/js/render/render.js:25` | `STAGE = {1920,1080}` | Read it from the timeline. |
| `web/js/player/layout.js:26-27`; `player.js:189, 1100` | `new Stage(this.el)` uses the default size; intro rect `STAGE_WIDTH × STAGE_HEIGHT` | `new Stage(el, {width: tl.width, height: tl.height})`. The Stage class already accepts these options (`stage.js:56-58`). |
| `web/css/player.css:2203-2213` (`.watch-player`), `web/css/studio.css:2483` (`.preview-box`), `:3438`, `:3743` | `aspect-ratio: 16 / 9` | Add `[data-format=portrait]` variants (`9 / 16`, limited by viewport height). |
| `aadhi/compose/captions.py:18-19` | `MAX_LINE_CHARS = 42`, `MAX_LINES = 2`; `split_caption` takes no width | Thread a `width` parameter through (`tokenize` and `wrap_tokens` already take one). |
| `aadhi/compose/ffmpeg.py:1042` | Burn-in font = `height / 1080 * 22` | Make it per format (§14). |
| `aadhi/compose/qa.py:30` | `EDGE_SAMPLE = (320, 180)` | Make it aspect-aware: (180, 320) for portrait. |
| `aadhi/pipeline/assets.py:130-131` | `PANEL_IMAGE_ASPECT = "4:3"`, `SCENE_IMAGE_ASPECT = "16:9"` | Per format: portrait full-screen `9:16`, panel `3:4`. All of these are already in `ASPECT_DIMS` (`providers/_common.py:161-169`); Veo already supports 9:16 (`providers/video/veo.py:60`). |
| `aadhi/manim/aadhi_scene.py:64-65` | FULLSCREEN 16:9 profiles; PANEL `(1080, 1350)` | For portrait, use the existing **panel profile (1080×1350, 4:5)** for full-screen animations. Hardening allows at most 1920 px on a side and 1920×1350 px in total (`manim/runtime/hardening.py:38-40`); 1080×1350 fits. |
| `aadhi/evals/metrics.py:49` | `BOARD_ITEM_LIMIT = 7`, tuned for 1920×1080 | Per format. |
| `aadhi/compose/ffmpeg.py:857, 1002-1003` | Dataclass defaults of 1920×1080 | Harmless if callers always pass the size. |

---

## 4. Where the monotony comes from in today's data

**[code]/[measured]:**
- **Music:** one file, `video_template/bgm.mp3`, 80.04 s, looped (`Branding.bgm_url`), at a fixed `bgm_volume = 0.06` (about −24.4 dB). The live player loops it independently of the timeline (`audio.js:350-390`), so **preview and MP4 music do not line up**. The MP4 delays it by the logo length.
- **Voice:** one voice per lecture, taken from options, then the server, then the language default (`voices.py: EDGE_DEFAULTS`, e.g. `en-IN-NeerjaNeural`). Because the server sets the default, every lecture tends to get the same voice. There is **no loudness normalisation anywhere** (no `loudnorm`, `ebur128` or LUFS in `aadhi/`). Once voices rotate across providers, their levels will jump.
- **Colour:** one deep-purple and gold palette (`tokens.css`). `player.css` holds 183 literal colours and `panels.css` 61, against 73 and 38 `var(--c-*)` uses. The chart, graph and 3D panels have their own constants (`panels/util.js:11 THEME`, `model3d.js:53`). Manim uses a fixed background, `#1A0B2E` (`config.py:236`).
- **Layout:** six fixed zone tables. The board is the same glass card in every scene.
- **Motion:** in the MP4 every change is a 0.15 s cross-fade of whole state PNGs (`ffmpeg.py:70`). The live player has a few CSS keyframes (`ap-show`, `ap-rise`, `ap-fill`, `ap-pulse`). Render mode turns them all off.
- **Intro:** the same logo animation and two title cards on the same static background in every video (`build_intro`).
- **Sound effects:** tick and ding only, synthesised with numpy (`compose/sounds.py`). The same deterministic method can produce whooshes and pops with no licence risk.

---

## 5. Renderer: what continuous motion needs

### 5.1 Today
- `screenshot._capture_scene` (`compose/screenshot.py:309-334`) asks the page for `states(i)` and takes **one PNG per distinct state key**. A repeated key reuses its PNG.
- There is a cap: `MAX_STATES_PER_SCENE = 2000` (line 48).
- Each `show()` waits for `texIdle`, image decoding, `fonts.ready` and two animation frames (`render.js:224-237`, `player.js:1110-1124`).
- `ffmpeg.plan_states` (`ffmpeg.py:705-752`) lays the states out on exact frames and adds premultiplied cross-fade frames. **It already supports one-frame entries** (`base.append((mix.out, 1))`), so a run of per-frame PNGs can go through the same concat stream unchanged.
- The media rect is fixed for the whole scene. `_first_rect` takes the first reported rect (`video.py:205-215`), so media cannot move during a scene.

### 5.2 Options compared

| Option | What it is | Cost for 5 min at 30 fps (9,000 frames) | Fit with the codebase | Verdict |
|---|---|---|---|---|
| A. **Hybrid states + motion windows** | Static states as now. The page also reports `windows(i)`: time spans in which each output frame is sampled. Motion is a pure function of `t`. | About 2,500–3,500 captures (§14) × about 35–65 ms = **1.5–4 min per page**. Two pages run in parallel today. | Very high. Keeps media holes, ffmpeg compositing, the QA frame grid, the segment cache and the Aadhi path unchanged. | **Recommended** |
| B. Capture every frame in the browser (the Remotion idea) | Screenshot all 9,000 frames, backdrop included | 9,000 × about 35–65 ms = **5–10 min per page**, plus about 0.5–0.9 GB of PNGs. Video media still cannot be seeked deterministically in headless Chrome, so ffmpeg is still needed for media. | Medium | Keep only for short reels as a fallback |
| C. Adopt Remotion | A React renderer | n/a | Low. It needs a bundler and React, which conflicts with the no-bundler, `script-src 'self'` frontend. The **Company License is required for for-profit organisations with more than 3 employees** [web: Remotion LICENSE]. | Rejected |
| D. Use GSAP for motion | A tween library, seekable | n/a | Medium. The 2025 "Standard No Charge" licence forbids use in **no-code visual animation builders that compete with Webflow**, and is free only when **end users are not charged a fee** [web: GSAP licence]. A Studio where teachers choose animated templates is arguably close to that line. | Avoid. Write about 300 lines of in-house pure easing and keyframe helpers. |
| E. Deterministic BeginFrame capture | `HeadlessExperimental.beginFrame` with `--enable-begin-frame-control` | Not measured | Needs chrome-headless-shell and experimental CDP; parts of the domain are marked deprecated [web: CDP HeadlessExperimental] | Not needed if motion is a pure function of `t` |

Also checked:
- **Playwright's `page.clock`** overrides `Date`, timers, `requestAnimationFrame` and `performance`. Its docs do not mention CSS or Web Animations [web: Playwright clock]. So it does not make CSS animations seekable. We do not need it if motion is computed from `t`.
- **`document.getAnimations()`** returns CSS animations, CSS transitions and Web Animations, and has been widely available since 2020 [web: MDN]. As a fallback, we could pause every animation and set `currentTime` to seek any CSS motion we cannot easily rewrite.

### 5.3 Capture speed [measured]
- **Setup:** `scratchpad/research/bench/shot_bench.py`, 60 frames, a simple page with five animated cards, `--disable-gpu`.

| Method | 1920×1080 | 1080×1920 | Average PNG size |
|---|---|---|---|
| Playwright `page.screenshot(type=png, omit_background=True)` (current code) | 63.9 ms | 60.8 ms | n/a |
| CDP `Page.captureScreenshot` png with transparent background override | 47.4 ms | 50.6 ms | about 59 KB |
| + `optimizeForSpeed: true` (experimental parameter [web: CDP Page.pdl]) | **33.8 ms** | **33.3 ms** | about 100 KB |
| webp, quality 100 | 76 ms | 81 ms | n/a |
| jpeg, quality 90 (opaque) | 33.6 ms | 34.4 ms | n/a |

- **Conclusions:**
  - Inside motion windows, use CDP `Page.captureScreenshot` (`format: png`, `optimizeForSpeed: true`) after `Emulation.setDefaultBackgroundColorOverride {a: 0}`, and skip the full `settle()`. Images are decoded and TeX is ready at window entry; only one animation frame is needed after setting styles.
  - The orientation does not change the cost.
  - Real pages (MathJax SVG, shadows) will be slower. **Avoid `filter: blur()` and `backdrop-filter` in dynamic render CSS.**
- **Backdrop generation [measured]:** a 10 s 1920×1080 30 fps `gradients` lavfi clip encoded with libx264 medium/crf 20 took **2.4 s** and is 351 KB. The `gradients` source with a `type=radial|spiral` option exists in the local ffmpeg 8.1. **It is not verified on Debian's ffmpeg 5.1.** The raw look is crude, so generate backdrops with a deterministic Canvas2D page through the same Playwright pipeline once per theme, or ship a curated pack. Cache them as content-addressed assets.

### 5.4 Recommended renderer design for dynamic mode (file level)

1. **Backdrop layer: reuse the mascot slot.**
   - **`compose/video.py`:** in the dynamic branch, `clip_for(scene)` returns the scene's backdrop clip (`TimedScene.look.backdrop_asset_key`, made local through `_local_copies`) instead of a branding mascot file. `loop_phase_frames` keeps the loop continuous across scenes, and `previous_background` crossfades the old theme into the new one at a theme change (the existing `MASCOT_CROSSFADE_SECONDS = 0.6`).
   - **Backdrop loops** must be seamless: periodic motion with a period equal to the clip length, a recommended 10–20 s. Add slight grain or dither (e.g. `noise=alls=2:allf=t`) to dark gradients against H.264 banding [proposal].
2. **Presenter (Aadhi) window.**
   - A new optional `MediaLayer` from the branding clip with `visible_from = 0`, `visible_to = presenter.seconds`, and an alpha fade-out of 0.4–0.6 s. Placed over the backdrop and under the state stream.
   - **Portrait:** do not crop and upscale the 1280×720 clip. A 9:16 crop would be a 405×720 window upscaled about 2.67×. Instead, scale the whole 16:9 clip to a **1080×608 band**. That is a downscale and stays sharp; the kinetic title goes above and below it.
3. **Motion windows.**
   - **`web/js/player/schedule.js`:** add `motionWindows(scene)` → `[{t0, t1}]`, pure and unit-tested next to `stateTimes()`.
   - **`player.js`:** add `windows(sceneIndex)`.
   - **`render.js`:** `aadhiRender.windows(i)`, `aadhiRender.version = 3`, and `show({scene, t, fast})`.
   - **`compose/screenshot.py`:** quantise window times to the **segment's frame grid**. For segment frames `[f0, f1)`, sample `t_k = (f0 + k)/fps − scene.start`. That is the same `shift` that `scene_segment_spec` uses at `video.py:272`. Use the motion frame index as part of the state key, so identical frames are still deduplicated.
   - **Caps:** raise `MAX_STATES_PER_SCENE` or cap motion frames separately at `ceil(fps × scene.duration)`.
4. **State stream.** `plan_states` with fade 0 inside windows (a one-frame entry per sample) and the 0.15 s cross-fade elsewhere. That needs a `StateFrame.exact: bool` flag. No new ffmpeg mechanism is required.
5. **Scene transitions (incoming only, because of the stream-copy concat).**
   - **Transitions over the scene layer** (slide, push, wipe, zoom of the text layer): render them in the page during `[0, d)` of scene B. Mount the last state of scene A beside scene B's root and capture as a motion window.
   - **Transitions into or out of full-screen video:** use "dip to theme colour" or a backdrop-only transition. The outgoing media would otherwise freeze.
   - **Backdrop changes:** crossfade through `previous_background` (exists).
   - **Optional:** a deterministic whoosh at the transition, added to `SceneInputs` like tick and ding (synthesised in `compose/sounds.py`).
6. **Clip scenes with audio** (§9): in `ffmpeg.media_filter`, add input seek (`-ss trim_in` before `-i`) or `trim=start=…`. Add an `AudioClip` from the media file at `volume`, with narration ducking inside the segment.
7. **Music plan.** Replace `bgm_filter` with `music_filter(plan)`: N inputs, each `atrim`/`adelay`/`afade`, `amix`, then the existing sidechain against narration (`threshold=0.015:ratio=8:attack=15:release=450` today). Add an optional two-pass `loudnorm` on the final mix [proposal].
8. **Output size and QA.**
   - `output_size(tl)` everywhere `settings.render_width/height` is used, including the workspace prepare key and `video_key` inputs, which already include w/h.
   - `qa.EDGE_SAMPLE` should be aspect-aware.
   - The burned-in caption style should depend on the format.
9. **Captions in reels.** Two routes, both deterministic:
   - **(a)** libass ASS karaoke (`\k`/`\kf` per word from `TimedBeat.words`), built in `captions.py` and burned in by `subtitles_filter`. It costs almost nothing. libass is already detected by `capabilities.burn_captions`.
   - **(b)** In-page word-pop captions as states. That is about 1 state per spoken word, roughly 700 per 5 minutes.
   
   Prefer (a) for cost, and (b) only if the design needs effects libass cannot do [proposal].

---

## 6. Data model changes (exact, migration-safe)

Every new field is left out of the JSON at its default (`Field(..., exclude_if=...)`; the pattern is in `screenplay.py:245, 475, 539-542`), so stored lectures, scene hashes, checkpoint digests and MP4 output stay **byte-identical**. That needs pydantic ≥ 2.12, already the project's floor. No table changes; `Project.settings` and `ProjectVersion.generation_meta` are JSON already.

### 6.1 `aadhi/pipeline/base.py`: `GenerationOptions` (lines 72-111)
```python
video_style: Literal["aadhi", "dynamic"] = Field(default="aadhi", exclude_if=lambda v: v == "aadhi")
video_format: Literal["landscape", "portrait"] = Field(default="landscape", exclude_if=lambda v: v == "landscape")
voice_rotation: bool = Field(default=False, exclude_if=lambda v: v is False)    # pick per video when tts_voice is None
music: str | None = Field(default=None, pattern=r"^(auto|off|[a-z0-9_-]{1,40})$", exclude_if=lambda v: v is None)
```
- The API default stays `aadhi`, so old clients and `regenerate` keep their behaviour. The Studio sends `dynamic` by default.
- `target_minutes` already allows 3 or more. Use 5 for landscape and 3–4 for portrait.

### 6.2 `aadhi/schemas/screenplay.py`
```python
class PresenterSpec(StrictModel):
    clip: Literal["center", "left", "right", "popup"] = "center"
    seconds: float = Field(default=8.0, ge=2.0, le=10.0)        # Aadhi visible [0, seconds) of the scene

class SceneLook(StrictModel):                                  # never LLM-facing; set by pipeline/direct.py or the teacher
    theme: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,40}$")      # catalogue id
    template: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,40}$")   # motion layout template id
    transition_in: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,40}$")
    backdrop: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,40}$")
    presenter: PresenterSpec | None = None

class Presentation(StrictModel):
    style: Literal["aadhi", "dynamic"] = "dynamic"
    format: Literal["landscape", "portrait"] = "landscape"
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    music: str | None = None              # catalogue track id ("off" = none)
    captions: Literal["classic", "kinetic"] = "classic"

SceneBase.look: SceneLook | None = Field(default=None, exclude_if=lambda v: v is None)
Screenplay.presentation: Presentation | None = Field(default=None, exclude_if=lambda v: v is None)
```
Also:
- Add `"look"` to `scene_hash`'s exclude set (`assets.py:213`) and to `repair.carry_over`.
- Add `look` to the hidden-scene logic only where relevant; it has no effect there.
- Add `ClipScene` for external video (§9):
```python
class ClipScene(SceneBase):
    type: Literal["clip"]
    beats: list[Beat] = Field(default_factory=list, max_length=40)   # optional voice-over
    asset_key: str = Field(max_length=128)                            # upload / library video (AssetRef-authorised)
    trim_in: float = Field(default=0.0, ge=0.0)
    trim_out: float | None = Field(default=None, gt=0.0)              # None = clip end
    clip_audio: bool = True
    clip_volume_db: float = Field(default=0.0, ge=-40.0, le=6.0)
    fit: Literal["contain", "cover"] = "contain"
    credit: str = Field(default="", max_length=200)                   # attribution shown as a lower third
```
- Add `"clip"` to `Scene`, `SCENE_TYPES` and `SceneType`. **Keep it out of planning:** the `PlannedScene.type` validator and `plan_rules` should reject `clip`.
- **Blast radius [code]:** `ai_video` is mentioned in 22 backend files (96 hits) and 12 frontend files (29 hits). That is roughly the list of switches `clip` must join.

### 6.3 `aadhi/schemas/timeline.py`
- `Timeline.presentation: TimelinePresentation | None` (style, format, captions), left out when None. **`width/height` come from the format.**
- `TimedScene.look: ResolvedLook | None`, left out when None. It holds:
  - **resolved theme tokens**, so old timelines keep their look even if the catalogue changes;
  - template id;
  - `transition: {kind, seconds}`;
  - `backdrop_asset_key`;
  - `presenter: {clip_url, seconds}`.
- `MediaRef.trim_in: float = 0` and `audio: bool = False`, `volume_db: float = 0`, all left out at their defaults.
- `Timeline.music: MusicPlan | None`:
```python
class MusicCue(StrictModel):
    asset_key: str
    start: float
    end: float
    gain_db: float
    fade_in: float = 1.0
    fade_out: float = 2.0
    loop: bool = True

class MusicPlan(StrictModel):
    cues: list[MusicCue]
    duck: DuckSpec
```
- **The validator's invariants stay as they are.**

### 6.4 `AssetManifest`
- Narration: optionally store `SceneAudio.lufs` (measured) and the applied gain.
- Clip scenes: `SceneMedia.main` carries the probed video, so `MediaInfo` gains `has_audio: bool`.
- Backdrops and music are **not** per-scene manifest entries. They are catalogue assets with fixed content keys, re-created on demand like `compose/sounds.ensure_sounds`.
- Add `"backdrop"` and `"music"` to `models.PUBLIC_ASSET_KINDS`. This is a string column, so no migration.

---

## 7. Pipeline changes

### 7.1 New stage: `aadhi/pipeline/direct.py` (art direction, deterministic, no LLM)
- **Input:** Screenplay, options, seed, the user's recent picks. **Output:** Screenplay with `presentation` and each scene's `look` filled in.
- **Where it runs:**
  - in `orchestrator._generate`, after companion and before the CAS write (line 953), so the generated snapshot includes the look;
  - in `translate` (copy the look);
  - in a new `POST /api/versions/{vid}/look/shuffle` (pure, saved like `PUT /screenplay`).
- **Rules** (constants live in `direct.py`; numbers in §14):
  - Choose the theme per chapter, rotating within a seeded palette family.
  - Choose the template from the board's composition:
    - one formula → `formula-focus`;
    - a table → `compare`;
    - code → `code-focus`;
    - only `example_step` items → `stepper`;
    - three or fewer bullets plus an image panel → `split`;
    - a single `takeaway` → `big-statement`;
    - `title` → `kinetic-title`;
    - `quiz_checkpoint` → `quiz-show`;
    - `chapter_card` → `chapter-wipe`;
    - `summary` or `key_takeaway` → `checklist`;
    - media types → `fullscreen-media` with a lower third.
  - Never the same template twice in a row. Cycle through the transitions.
  - `presenter` only on scene 0 (and optionally the final recap or summary scene), with a lint cap.
- **Seed:** `sha256(project_id, version_number)`. Store the seed, so a rebuild is reproducible.

### 7.2 Voice rotation (`pipeline/assets.py:280-287`, `providers/tts/voices.py`)
- Resolve the voice **once, at generation start**, in `_generate` right after options are parsed (line 760). Do it when `video_style == "dynamic"`, `voice_rotation` is on and `tts_voice` is None.
- Pick from a curated pool per language and provider. Avoid the voices of the user's last N renders, which can be found from `generation_meta.options` of recent versions; no new table.
- **Write it into `options.tts_voice`.** It is then saved in `generation_meta["options"]` (line 939). Rebuilds and `build_assets` reuse it and stay stable.
- **Measured fact:** edge has only 3 `en-IN` voices (Neerja, Prabhat, Neerja Expressive). The multilingual `en-US` voices (Andrew, Ava, Emma, Brian) read Indian English text too. That is why the pool needs a provider/locale mix.
- **Add loudness normalisation per beat clip** in `pipeline/audio.py` (none exists today), so rotated voices sound equally loud. This changes the narration bytes, so apply it only to dynamic lectures, or under a version flag, to keep Aadhi MP4s identical.

### 7.3 Music selection: new `aadhi/compose/music.py`
- The catalogue (id, title, licence, BPM, mood, duration, loop points, integrated LUFS) is a JSON next to the files.
- **Store the files as content-addressed assets.** `video.branding_file` rejects any path with a slash (`_SAFE_BRANDING_NAME`, `video.py:113`), so a `/branding/music/` subfolder would play in the browser but **fail in the MP4**. Use assets, or flat file names in the branding folder.
- Selection is seeded and avoids the user's last 3 tracks. `MusicPlan` is built in `build_timeline` for dynamic timelines.
- The live player's `BackgroundMusic` must **seek to timeline time**, so preview matches the MP4. Today it loops freely.

### 7.4 Prompts and lint per format
- **`prompts/plan.md`** assumes 15 minutes ("Aim for 3–8 core concepts for a 15-minute lecture"; quizzes 35–50 s; "30–90 s per teaching scene"). Add a format block:
  - 5-minute landscape: 2–3 core concepts, at most one quiz;
  - 3–4-minute portrait: 1–2 concepts, scenes of 8–25 s, a hook in the first 3 s;
  - dynamic: scene 0 is a presenter intro of 25 words or fewer (about 10 s at the planner's 140 wpm), and the hook moves to scene 1.
  
  Bump `PROMPT_VERSION` and run the evals.
- **`pipeline/validate.py` thresholds** are global: `MAX_BOARD_ITEMS = 6`, `BEAT_MAX_WORDS = 60`, `SCENE_MAX_SECONDS_DEFAULT = 150`, `DURATION_TOLERANCE = 0.25`, `TABLE_MAX_COLS = 4`, `CODE_MAX_LINES = 18`, `CODE_MAX_LINE_CHARS = 80`. Make them per format. Add dynamic lints:
  - `presenter.too_long` (more than 10 s);
  - `presenter.misplaced` (not the first or last scene);
  - `look.template_repeat`;
  - `look.theme_stale` (same theme longer than the limit);
  - `portrait.code_too_wide`, and similar.
  
  None of these should be fixable by a rewrite (`fixable=False`), like `presenter_lint`.
- **`assets.py`:** pass the media aspect per format. Clip scenes need nothing generated; probe the uploaded file and validate the trims.

---

## 8. Player and frontend changes (`web/`)

- **`player.js`:**
  - `load()` reads `timeline.presentation`. When it is dynamic: `Stage(el, {width, height})`, `data-style="dynamic"` and `data-format`, a `BackdropController` instead of `MascotController`, and a presenter controller only for scenes with `look.presenter`.
  - `_renderFrame` already calls `sceneView.update(sceneT, state, frame)` on every animation frame, so continuous motion works in live and preview with no clock changes.
- **New `web/js/player/motion/`:**
  - `easing.js` with pure functions (cubic, expo, back, analytic spring);
  - `apply.js`, which writes inline `transform` and `opacity` from `t`;
  - one module per template under `templates/`, each exposing `build(scene, zones, theme)`, `update(t, state)`, `windows(scene)`, `mediaElement()`.
- **`scenes/index.js` `viewKind`:** dynamic maps each type to its template view. Board, quiz and card logic is reused through `schedule.sceneStateAt`, which stays the single source of truth.
- **`layout.js`:** add `computeDynamicZones(template, format)` with landscape and portrait tables. Keep `computeZones` byte-identical; `web/tests/player/layout.test.js` covers it.
- **Theming:**
  - A shared catalogue: Python `aadhi/compose/themes.py` generates `web/themes/catalogue.json`, with a parity test like the JSON schemas.
  - `applyTheme(root, tokens)` sets CSS variables on the scene root.
  - **A new stylesheet, `web/css/dynamic.css`, scoped to `.aadhi-player[data-style=dynamic]`.** The Aadhi look keeps its CSS and its render-parity baselines.
  - Panels: pass the theme into `panels/util.js THEME`, the chart/graph colours, and `model3d`'s default primitive colour.
- **Render mode:**
  - Dynamic CSS must not depend on `prefers-reduced-motion`, because Playwright launches with `reduced_motion="reduce"` (`screenshot.py:430`).
  - The live player may still snap motion for viewers who ask for reduced motion.
  - Keep `.render-mode * { animation: none }`. Dynamic motion is inline styles, not CSS animations.
- **Watch page and editor preview:** portrait containers (`player.css:2203-2213`, `studio.css:2483`).

---

## 9. Studio and NLE editor changes

### 9.1 New lecture (`web/js/studio/views/newProject.js`)
- The page already uses `createTabs` for "Upload a file | Paste your notes" (line 179).
- Add a top-level tab set: **"Dynamic video" (default) | "Aadhi presenter (classic)"**. Under Dynamic, show:
  - Format: **Landscape (about 5 min)** or **Reel 9:16 (3–4 min)**;
  - Voice: **Auto (rotates)** or a voice from `/api/meta` `available_tts_providers`;
  - Music: **Auto / choose / none**.
- The classic tab shows today's form unchanged. This meets "keep the Aadhi generation, put it in a new tab".
- `optionsFields.js` and `lib/optionsForm.js` send the new options. The projects list shows a style and format chip.

### 9.2 Project page (`projectDetail.js`)
- Add **"Make a reel from this lecture"**. That is a new version: a condense job reusing translate-style version creation, or "regenerate with `video_format=portrait`".
- The render dialog shows the format.

### 9.3 Editor (`views/editor/*`)
- **Look inspector section** (`inspector.js`, `inspectorFold.js`): theme, template (only compatible ones), transition, presenter (scenes 0 and last only), and **"Shuffle look"** (lecture-wide or per scene; undoable; a `changes.editLabel` entry).
- **Timeline strip** (`timelineStrip.js`, 608 lines, built from the same preview Timeline):
  - add a **theme lane** and a **music lane**;
  - on `clip` blocks, add trim handles (drag in/out, snapped to the frame grid at 1/fps), a **razor** at the playhead (`C`), and ripple delete;
  - **range select + Cut** maps to existing primitives: whole beats inside the range are deleted; clip scenes are trimmed or split (`splitScene.js` becomes time-based for clips); partial beats snap to word boundaries (from `TimedBeat.words`), which edits the narration and re-synthesises one beat (TTS is cached per beat; edge is free); whole scenes are hidden or deleted.
  - Every operation is a pure screenplay transform, like `splitScene.js`, so undo, merge (`sceneMerge.js`) and autosave keep working.
- **Inserting external video:**
  - an "Insert clip…" command at the playhead (between scenes) from upload or the library (`openLibraryPicker({kind:'video'})`);
  - a "B-roll over narration" option that uses the existing side-panel or AI-video overrides.
- **Upload pipeline (`api/routers/uploads.py:46-57`, `api/upload_io.py:26-38`)** reads the whole file into memory with a 50 MB cap (`config.upload_max_mb`). For clips:
  - stream to a temp file;
  - add a separate size and duration cap for `clip`;
  - run an `import_media` job: ffprobe, then transcode to H.264 yuv420p CFR at the timeline fps, AAC 48 kHz, longest side ≤ 1920; make a poster frame and waveform peaks.
  
  This makes trims frame-accurate and composites deterministic.
- **URL import:**
  - only direct media URLs through `providers/_http.fetch_limited` (https only, host allow-list, redirect cap, streamed size cap) plus private-IP blocking;
  - **no YouTube or other platform downloaders** (terms-of-service and licence risk; the licensing researcher should confirm);
  - `ClipScene.credit` is required when the source is a URL [proposal].

---

## 10. Tests and fixtures available

- **Python:** 2,668 `def test` functions in `tests/`. The previous session counted 3,970 collected tests, including parametrised ones. Relevant groups:
  - `tests/compose/`: 25 files, including `test_timeline.py`, `test_timeline_hidden.py`, `test_mascot_timeline.py`, `test_video.py`, `test_ffmpeg_builders.py`, `test_ffmpeg_slow.py`, `test_screenshot.py`, `test_qa.py`, `test_render_parity_*`, and `factories.py` (`make_screenplay`, `make_manifest`), plus a `stub_render` page and `stub_server.py`;
  - `tests/branding/test_mascot_assets.py`;
  - `tests/pipeline/` (52 files), including `test_presenter_lint.py` and `test_canonicalize.py`;
  - fixtures: `tests/core/fixtures/showcase_ohms_law.json` and `andryan_lesson.json`.
- **Frontend:** 959 `test(` calls under `web/tests/`. Relevant: `player/layout.test.js`, `schedule.test.js`, `mascot*.test.js`, `hold.test.js`, `render/render.test.js`, `studio/editor*.test.js`, `ui_tokens/contrast.test.js`; fixture `web/tests/player/fixtures/timeline.json`.
- **Evals:** `evals/render_parity.py` (preview against MP4 at the same instants) and `evals/video_qa.py` (frame accuracy).
- **Fixtures:** three PDFs plus their options in `evals/fixtures`. `web/examples/ohms-law.json` is the in-app example.

**Tests to add:**
- **Invariance:** an Aadhi lecture (no `presentation`) produces the same screenplay JSON, `scene_hash`, timeline JSON and segment keys, plus one slow MP4 byte or frame comparison. Copy the batch-4 approach.
- **Schedule and motion:** `motionWindows` is pure; `sceneStateAt` gives the same styles at the same `t`; quantisation hits the segment frame grid exactly.
- **Layout:** the portrait zone table has no overlaps, stays inside safe areas, and keeps text off the caption band.
- **Themes:** every theme passes contrast checks (reuse `ui_tokens/contrast.test.js`); the Python and JS catalogues match.
- **Direction:** the same seed gives the same looks; no template repeats; at most 2 presenter scenes, each ≤ 10 s.
- **Voice rotation and music:** deterministic, avoid recent picks, and persist into options.
- **Clip scene:** frame count is exact with trims; PCM length is exact; `clip_audio` ducking; QA on 1080×1920.
- **Editor:** razor, trim and range cut transforms (pure, like `editorSplit.test.js`); `convertSceneShape` keeps `hidden`, `min_seconds` and `look`.
- **Render page:** `aadhiRender.windows` contract; viewport sized from the timeline.

---

## 11. Risks, ranked

1. **Render time and disk.**
   - Motion windows at about 35–65 ms per frame. Budget 25–40% of frames dense.
   - Fast PNGs are about 100 KB each, so a 5-minute video means about 250–350 MB of transient PNGs. `RENDER_MIN_FREE_GB = 2` covers it.
   - **Mitigation:** CDP capture, `pages=3`, a light settle inside windows, and no blur filters.
2. **Preview and MP4 drift.**
   - Any motion not computed from `t` (CSS transitions, `setTimeout`, video elements) will differ.
   - **Mitigation:** motion is pure, so rule out CSS keyframes in `dynamic.css` with a lint test; extend `evals/render_parity.py`.
3. **Live music is not synced** to the timeline today (`audio.js:350-390`). Fix it with the music plan.
4. **Voice loudness jumps after rotation:** there is no normalisation today (§7.2).
5. **Editor bug:** `convertSceneShape` drops `hidden`/`min_seconds` (`screenplayEdit.js:301-304`) and would drop `look`. Fix it in phase 0 and add a test.
6. **Mascot clip quality in 9:16:** cropping means a 2.67× upscale. **Mitigation:** the 1080×608 band layout, or a native 9:16 Aadhi clip later (Veo supports 9:16, but keep the presenter consistent with the brand).
7. **ffmpeg 5.1 in Docker against 8.1 on the dev machine.** Check `gradients` options, `-/filter_complex`, `xfade` (exists since the 4.3 line [web: ffmpeg trac and source]) and libass karaoke on 5.1 in CI.
8. **External clip uploads:** memory (whole-file reads) and size (50 MB), codec variety, licensing and attribution, SSRF for URL imports.
9. **Prompt and lint changes** shift eval baselines. `INTEGRATION_ANDRYAN.md` already lists eval re-runs as open.
10. **Licences:** do not adopt Remotion or GSAP (§5.2). Music, fonts and backdrops need licences compatible with commercial use (for the licensing and music researchers).
11. **Branding paths:** `branding_file` forbids sub-folders, while `/branding` serves them (`api/static.py:151-161`). This is easy to get wrong with music packs.
12. **Batch 4 follow-ups:** the four issues reported at the end of the last session look fixed in code:
    - Space handling in `editor.js:2018-2021`;
    - the autosave hold (`saveError.kind === 'merged'`, `editor.js:541`);
    - the revert chapter listing (`changes.py:305-308`);
    - the Videos list redraw (`videos.js:9, 49`).
    
    I did not run the tests to confirm.

---

## 12. Phased plan (each phase ships green; Aadhi mode output byte-identical)

**Phase 0: plumbing, no visible change (about 1 week)**
- `stage_size(format)` and `output_size(tl)`; screenshot viewport and `render.html` sized from the timeline; captions `width` parameter; aspect-aware QA sampling.
- Fix the `convertSceneShape` key loss and add a test.
- Add the `Presentation`, `SceneLook` and `GenerationOptions` fields (all left out at their defaults); extend the `scene_hash` exclude set and `carry_over`; regenerate `web/schemas/*.schema.json` and the JSDoc types.
- Invariance tests, including one slow MP4.
- **Exit:** the full suite, typecheck, lint and consistency checks pass; MP4 output for `showcase_ohms_law.json` is identical.

**Phase 1: dynamic landscape MVP (2–3 weeks)**
- `direct.py`, the themes catalogue and contrast tests, backdrop generation and caching (kind `backdrop`), `dynamic.css`, motion module and eight templates (kinetic-title, bullets-cascade, split, formula-focus, compare, stepper, quiz-show, checklist, chapter-wipe).
- Presenter window for scene 0 (≤ 10 s); voice rotation and loudness normalisation; music catalogue, music plan and a live-synced BGM.
- Renderer: backdrop slot, presenter layer, motion windows with CDP capture, incoming transitions, transition sound effects.
- Studio: Dynamic and Aadhi tabs.
- **Exit:**
  - a 5-minute lecture renders in ≤ 2× its current render time on the same machine;
  - the render-parity eval stays within its threshold at 50 instants, including inside motion windows;
  - voices and music differ between two consecutive lectures by the same user.

**Phase 2: portrait reels (1–2 weeks)**
- Portrait zones and templates; the 1080×608 presenter band; kinetic ASS captions; media aspects per format; the Manim panel profile; reel-specific prompts and lints; "Make a reel from this lecture".
- **Exit:** a 1080×1920 MP4 passes QA (frame grid, audio, no black edges); text-size lint is clean; the reel is 3–4 min.

**Phase 3: NLE (2 weeks)**
- `ClipScene`; streaming upload and the `import_media` transcode; trim handles, razor, range cut, ripple delete; theme and music lanes; Look inspector and Shuffle.
- **Exit:** insert, trim, cut and render a 60 s external clip with its own audio, frame-exact (QA frame count ±0); every edit is undoable.

**Phase 4: tune and measure (ongoing)**
- Use existing analytics (`/api/projects/{id}/analytics`: drop-off per scene) to compare looks and templates; re-run the evals; tune performance (pages, PNG settings).

---

## 13. File-by-file change list (quick reference)

- **Backend schemas:** `aadhi/schemas/screenplay.py` (Presentation, SceneLook, PresenterSpec, ClipScene), `timeline.py` (TimelinePresentation, ResolvedLook, MusicPlan, MediaRef trims and audio), `manifest.py` (MediaInfo.has_audio, SceneAudio.lufs).
- **Pipeline:** `base.py` (options), **new `direct.py`**, `orchestrator.py` (voice resolution after options parse; direct before write; translate copies the look), `assets.py` (exclude `look`; aspects per format; clip scenes; loudness), `audio.py` (normalisation), `repair.py` (carry `look`), `validate.py` plus a new `look_lint.py`, `plan_rules.py` (`clip` not plannable), prompts (`plan.md`, `style.md`, `scene_*.md`), `gen_models.py` (dynamic: no `mascot_position`).
- **Compose:** `base.py` (stage per format, dynamic constants), `timeline.py` (dynamic intro, look resolution, music plan, width/height), `video.py` (dynamic branch: backdrop slot, presenter layer, windows, clip audio, output size), `ffmpeg.py` (exact state frames, `trim_in`, `music_filter`, transition sound mixing), `screenshot.py` (viewport from timeline, CDP fast capture, windows), `captions.py` (width, ASS karaoke), `qa.py` (aspect), `sounds.py` (whoosh and pop), **new `themes.py`, `backdrops.py`, `music.py`**.
- **Providers:** `tts/voices.py` (rotation pools); providers already support aspects.
- **API:**
  - `routers/projects.py`, `generation.py` (options validation);
  - `routers/meta.py` (themes, music and voice pools for the UI);
  - new `routers/look.py` (shuffle) or a `versions.py` addition;
  - `routers/uploads.py` and `upload_io.py` (streaming clip upload);
  - an `import_media` job;
  - `render_internal.py` (no change if the look lives in the screenplay).
- **Models:** `PUBLIC_ASSET_KINDS` += `backdrop`, `music`.
- **Web player:** `player.js`, `stage.js` (already parameterised), `layout.js` (+ dynamic zones), `schedule.js` (+ `motionWindows`), **new `motion/`, `backdrop.js`, `themes.js`**, `scenes/index.js` (+ dynamic views, clip view), `scenes/media.js` (trim, audio), `audio.js` (`BackgroundMusic` seek and plan), `captions.js` (kinetic), `intro.js`.
- **Render page:** `web/render.html` (dynamic size), `web/js/render/render.js` (`windows`, `version 3`, size).
- **CSS:** new `web/css/dynamic.css`; portrait variants in `player.css` and `studio.css`.
- **Studio:** `views/newProject.js` (style tabs, format, voice, music), `optionsFields.js`, `lib/optionsForm.js`, `projectDetail.js` (make a reel), `views/editor/inspector.js` (look), `timelineStrip.js` (lanes, trims, razor, cut), `splitScene.js` (time split for clips), `lib/screenplayEdit.js` (COMMON keys, `newScene('clip')`), `changes.js` (labels), `types.js` and `shared/types.js` (JSDoc).
- **Vendoring:** none needed for motion (in-house). Fonts for themes go through `scripts/vendor.mjs` (Fontsource packages, pinned).
- **Docs:** `ARCHITECTURE.md` §§3, 9, 10, `INTERFACES.md`, `API.md`, `PROMPTS.md`, `OPERATIONS.md` (render time and disk).

---

## 14. DESIGN IMPLICATIONS FOR AADHI (concrete parameters)

**Formats and frame grid**
1. Two stages: **landscape 1920×1080** and **portrait 1080×1920**, both **30 fps** (`render_fps`). Output size = the stage size × (`render_height` / 1080). Never resample state PNGs between orientations.
2. `target_minutes`: **5** for landscape, **4** for portrait (accept 3–4). Duration lint tolerance stays ±25% (`DURATION_TOLERANCE`).

**Aadhi presence**
3. Aadhi appears only in scenes whose `look.presenter` is set: **at most 2 per video** (scene 0, plus optionally the final recap or summary), each with `presenter.seconds` between 2 and **10 s**. **Default 8.0 s**, which is exactly one loop of the 8.000 s, 24 fps clip with no seam.
4. The presenter fades out over **0.5 s** to the theme backdrop.
5. In portrait, the 16:9 clip is shown as a **1080×608 band**. Never crop and upscale it (that would be 2.67×).
6. Scene 0 narration is **≤ 25 words** (about 10.7 s at 140 wpm).
7. Dynamic intro: no title cards. An optional logo sting of **≤ 3.0 s** in landscape (today's `INTRO_LOGO_SECONDS` is 5.5) and **0 s** in portrait.

**Motion renderer**
8. Static states keep the **0.15 s** cross-fade (`STATE_FADE_SECONDS`). Motion windows are sampled every frame at `t_k = (f0 + k)/30 − scene.start`.
9. Default motion durations [proposal; the design research may refine them]:
   - item entry **0.5 s** (15 frames), with a stagger of **0.12 s** between items revealed by the same beat;
   - incoming scene transition **0.5 s**;
   - theme backdrop crossfade **0.6 s** (existing `MASCOT_CROSSFADE_SECONDS`).
10. Capture budget: dense frames **≤ 40%** of all frames, so **≤ 3,600** for a 5-minute video. Capture with CDP `Page.captureScreenshot {format: png, optimizeForSpeed: true}` with a transparent background: about **34 ms/frame** [measured, simple page], against about 62 ms with `page.screenshot`.
11. Raise `MAX_STATES_PER_SCENE` from 2,000, or cap motion frames separately at `ceil(30 × scene_duration)`.
12. Media rects stay fixed per scene. Moving media is out of scope in v1.
13. No `filter: blur()` or `backdrop-filter` in dynamic render CSS. No CSS `@keyframes` or `transition` in `dynamic.css`; all motion is inline styles computed from `t`.

**Variety, enforced by `direct.py` and lint [proposal; colour and engagement research to confirm]**
14. Theme changes at **every chapter boundary**, and at the latest **every 60 s**.
15. The template never repeats in **consecutive** scenes, and **≥ 5** distinct templates appear per 5-minute video.
16. Transitions rotate through **≥ 4** kinds. The same transition is never used twice in a row.
17. Voice rotation avoids the voices of the user's **last 2** videos. Music avoids the user's **last 3** tracks.
18. Every theme passes **WCAG AA**: **≥ 4.5:1** for body text and **≥ 3:1** for text ≥ 24 px, against its surface (`contrast.test.js`).

**Audio**
19. Normalise narration per beat to one target (proposal: **−16 LUFS integrated, −1.5 dBTP**; the audio research should confirm) before joining, with `INTER_BEAT_GAP_SECONDS` staying **0.15 s**.
20. Music starts from today's **0.06 linear (≈ −24.4 dB)** and today's sidechain (`threshold 0.015, ratio 8, attack 15 ms, release 450 ms`). Tracks must last **≥ 180 s** or have marked seamless loop points; the current 80 s file loops 3.75× in 5 minutes.
21. Fade music in over **1 s** and out over **2 s** (today's values). Swap tracks at chapter boundaries only, with a **2 s** crossfade.
22. Clip scenes with their own audio duck that audio by the same sidechain when there is a voice-over. The default `clip_volume_db` is **0**.

**Captions**
23. Landscape: **2 lines × 42 characters** (unchanged). Portrait: `split_caption(width=24)` and **≤ 2 lines** (proposal; typography research to confirm), placed above the bottom **20%** of the frame for platform UI (proposal). Kinetic word highlighting comes from `TimedBeat.words` through ASS `\k`. Keep SRT and VTT outputs unchanged for accessibility.

**Media and layout per format**
24. Generated media aspects:
    - landscape: full-screen **16:9**, panel **4:3** (unchanged);
    - portrait: full-screen **9:16**, panel **3:4**;
    - Manim in portrait: the existing **1080×1350** panel profile.
25. Content limits in portrait (proposal; lint thresholds per format): `MAX_BOARD_ITEMS` **4** (6 today); `BEAT_MAX_WORDS` **30** (60); `CODE_MAX_LINES` **10** (18); `CODE_MAX_LINE_CHARS` **40** (80); `TABLE_MAX_COLS` **3** (4); scene length **≤ 25 s** in portrait and **≤ 45 s** in landscape dynamic (150 s today).

**Editor and external video**
26. Clip import:
    - streamed to disk; cap **500 MB** and **10 min** per clip (proposal);
    - transcoded to H.264 yuv420p **CFR 30**, AAC **48 kHz** stereo, longest side **≤ 1920**;
    - trims snap to **1/30 s**;
    - razor and range cut snap to frames for clips, and to **word boundaries** for narration (re-synthesising one beat).
27. Every NLE operation is a **pure screenplay transform** with an undo label, so preview, MP4, autosave and merge keep working.

**Compatibility**
28. Every new field is **left out of the JSON at its default**. Aadhi-mode lectures must keep an identical screenplay, `scene_hash`, timeline and MP4 (an invariance test that blocks the build). `look` is excluded from `scene_hash`.
29. Do not adopt Remotion (company licence for for-profit teams over 3 people) or GSAP (2025 licence limits on no-code animation builders and on charging end users). Write the easing and keyframe code in-house: about 300 lines, pure, unit-tested.

---

## Sources
- [Playwright: Clock](https://playwright.dev/docs/clock) — what `page.clock` overrides (Date, timers, rAF, performance); CSS and Web Animations not mentioned.
- [Chrome DevTools Protocol, Page.pdl](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/Page.pdl) — `captureScreenshot` parameters, including the experimental `optimizeForSpeed`.
- [CDP HeadlessExperimental (mirror)](https://cdpstatus.reactnative.dev/devtools-protocol/tot/HeadlessExperimental) and [puppeteer-capture README](https://cdn.jsdelivr.net/npm/puppeteer-capture@1.58.0/README.md) — BeginFrame-based deterministic capture needs BeginFrameControl and the headless shell.
- [MDN: Document.getAnimations()](https://developer.mozilla.org/en-US/docs/Web/API/Document/getAnimations) — returns CSS animations, transitions and Web Animations; widely available since September 2020.
- [Remotion LICENSE.md](https://raw.githubusercontent.com/remotion-dev/remotion/main/LICENSE.md) — free for individuals, non-profits and for-profit organisations with up to 3 employees; otherwise a Company License.
- [GSAP Standard License](https://gsap.com/community/standard-license/) and the [ScanCode copy of the 2025 text](https://scancode-licensedb.aboutcode.org/gsap-standard-no-charge-2025.html) — prohibited use in no-code visual animation builders competing with Webflow; no-charge only when end users are not charged.
- [FFmpeg vf_xfade.c at n4.3.1](https://fuchsia.googlesource.com/third_party/ffmpeg/+/refs/tags/n4.3.1/libavfilter/vf_xfade.c) and [FFmpeg trac Xfade wiki](https://trac.ffmpeg.org/wiki/Xfade?version=17) — xfade transitions in the 4.3 line.
- Local measurements on this machine (2026-10-09), saved under the scratchpad: `research/bench/shot_bench.py` (screenshot timings) and ffprobe/ffmpeg runs on `video_template/*` (clip durations, gradient backdrop timing).
