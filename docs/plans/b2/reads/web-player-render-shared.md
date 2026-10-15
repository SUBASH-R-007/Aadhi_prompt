# web-player-render-shared: web/js/player/** (45 files), web/js/render/**, web/js/shared/**, web/watch.html, web/render.html, web/index.html, web/css/{tokens,base,player,panels,studio}.css


## summary
This slice is the browser side of lecture playback. web/js/player/player.js `Player` runs one Timeline in three modes. In `live` (the public /watch page) and `preview` (/preview and the Studio editor), the narration <audio> is the master clock and the player shows captions, records analytics (live only) and lets learners click quiz options. In `render`, Playwright drives the classic MP4 renderer through web/render.html and web/js/render/render.js (`window.aadhiRender` v2). Each frame, `_renderFrame(t)` (player.js:629) picks the scene with schedule.sceneIndexAt and computes `schedule.sceneStateAt(scene, sceneT)`. That function is pure: phase, revealed, filled and highlighted board items, caption, countdown, panel visibility, terminal lines, word-anchored parts and Aadhi's state and cue bubble. The result drives the scene view (board, card, media, quiz, interactive), the side panel, the CaptionsView, narration sync and the mascot. Render mode uses the same state list (stateTimes, stateKey, renderStateList) to screenshot every distinct visual state as a transparent PNG. Media, mascot and captions are left as holes that ffmpeg fills.

The player decides between the classic and the Dynamic stage in exactly one place: `isDynamicTimeline` (player.js:97, `timeline.presentation.style === 'dynamic'`) together with `mode !== 'render'` (player.js:315). In that case `load()` sizes the logical stage from Timeline.width and height (1080x1920 for a reel), skips the mascot, CaptionsView and SpeechMeter, and builds `web/js/dynamic/stage.js` createDynamicStage, imported on demand, inside the scene layer. `_renderDynamicFrame` (player.js:711) then calls `stage.update(t, {playing, rate, seeked})`, switches narration per scene, ducks the branding BGM and tracks analytics. Render mode is always classic; Dynamic MP4s use /render-dynamic, which is not in this slice.

shared/ holds the safe DOM builder (no innerHTML; URL allow-list), the CSRF API client, vendored-library loaders (MathJax locked down, Prism, Chart.js, math.js, three.js) and JSDoc types. The types mirror timeline.schema.json, including every v3 Dynamic type (TimelinePresentation, ResolvedLook, VisualEvent, KineticChunk, MusicPlan, SfxCue, ...). The CSS supplies the classic purple-glass look in stage pixels (player.css), the side panels (panels.css), the Studio UI (studio.css) and the tokens and base.

As B1 left it, the Dynamic live path is wired: frames, captions on/off, narration clock, scene analytics and reel stage sizing. These are missing in the slice:
- interaction with Dynamic checkpoints;
- quiz SFX in Dynamic live playback;
- music-plan playback;
- music level control;
- caption size for the stage;
- a portrait page container.

Live audio today is classic only: NarrationDeck, SoundEffects (tick/ding), BackgroundMusic (the branding BGM at 50% while a beat is speaking) and the SpeechMeter fallback. This is where B3's MusicPlan, GainNode and SFX playback would go. dynamic/** imports schedule.js, richtext.js, icons.js and kenburns.js from the player, plus shared/dom.js and libs.js. That makes these player modules shared, de facto frozen dependencies of the B2 templates.

## contracts
PLAYER
- Constructor and methods: `new Player(root, {mode:'live'|'preview'|'render', analytics:{shareToken?|versionId?}|null, captions, captionSize, autoplay, volume, muted, rate, sandboxUrl, deps})` (player.js:79-91, 141).
- `load(timeline)` rejects unless `scenes` is an array and `total_duration` is finite (303-305). It may be called again; a newer call supersedes an older one (_loadSeq, 306).
- Live API: play/pause/toggle/seek(seconds, {track, from})/seekScene/setRate (0.25..4)/setVolume/setMuted/setCaptions/setCaptionLift/setCaptionSize/on/off/destroy.
- Render API: states(i) -> [{t, key}]; introStates(); renderState({sceneIndex, t}) -> RenderResult {state_key, media_rect, panel_media_rect, media_fit} in stage px (types.js:617-622); renderIntro({t}). Calls are serialised (1166-1229).
- PLAYER_EVENTS (58-61): ready, timeupdate {t, duration, sceneIndex, sceneT}, scenechange {index, scene} (index -1 means intro), play, pause {t, reason?}, ended {t, completed}, quizanswer, error, ratechange, volumechange, captionschange {enabled, size}, buffering {buffering}, blocked {t, reason:'autoplay'}.
- BACKGROUND_TICK_MS 250 (108).

DYNAMIC STAGE INJECTION
- `isDynamicTimeline` is true only when `presentation.style === 'dynamic'` (97), and the stage is used only when mode !== 'render' (315).
- The stage factory `createDynamicStage(root, timeline, {mode:'live', captions, reducedMotion, onInvalidate})` must return {update(t, {playing, rate, seeked}), setLayers({captions}), destroy()} (366-377, 599, 724). It is mounted in `.ap-layer--scene`.
- The stage is sized from Timeline.width x height: integers in 1..4096, otherwise 1920x1080 (769-779).

SCHEDULE (shared with web/js/dynamic and the classic goldens)
- Exports used by dynamic/**: sceneStateAt, planScene, selectCue, sceneIndexAt (dynamic/state.js:8, stage.js:22, templates/board_generic.js:24).
- SceneState shape: types.js:581-598.
- stateKey format: `s{scene.index}-{fnv1a(parts)}`. The optional parts (m:, vp:, e:, pf:, x:) are appended only when present, so existing keys stay stable (schedule.js:429-451). Classic invariance goldens depend on this.
- TERMINAL_MAX_LINES 200 must match in schedule.js:87 and panels/timing.js:18.

RENDER PAGE (classic)
- `window.aadhiRender` v2 (render.js:258-279): {version:2, ready, error:{code, message}, states(i), show({scene, t}) -> {state_key, media_rect, panel_media_rect, media_fit, notices?}, showIntro({t})}.
- The token arrives as fragment `#token=<JWT-like>&glass=1`, is removed with history.replaceState and sent as `Authorization: Bearer` to GET /api/render/timeline (credentials omit, redirect error) (render.js:82-142).
- PLAYER_METHODS load/states/renderState/renderIntro/destroy (29).
- The page and viewport are sized from Timeline.width x height (41-61, 308).
- Fonts that fail to load make boot fail with `fonts_missing` (318).

ANALYTICS
- POST /api/analytics/events with body {viewer_id, events:[{event, t, scene_id?, scene_t?, data?}], share_token | version_id} (analytics.js:129-135).
- Limits: ≤100 events and ≤30 KB per request, keepalive, CSRF header via api().
- The event names in ANALYTICS_EVENTS (types.js:666) must equal aadhi/models.py:524.
- Server rules: pause data only {reason:'blocked'} (aadhi/api/routers/analytics.py:96); quiz_answer data {choice, correct} (player.js:1012).
- viewer id lives in sessionStorage 'aadhi_viewer_id' and matches /^[A-Za-z0-9_-]{16,64}$/.
- Preview mode never sends analytics (watch.js:71).

WATCH PAGE
- Routes /watch/{token} (6-200 characters of [A-Za-z0-9_-]) and /preview/{1-12 digit id} (watch.js:28-34).
- API: GET /api/public/watch/{token} -> {project, timeline, share_token}; GET /api/versions/{vid}/timeline.
- Preferences: localStorage 'aadhi_player_prefs' {volume, muted, rate, captions, captionSize} (watch.js:17, controls.js:347-351).
- SPEEDS [0.75, 1, 1.25, 1.5].

PANELS
- createPanel(container, rsp, ctx{mode, timeline, sceneIndex, conceptState, stageEl}) -> {el, update(t, state, frame?), mediaRect?, ready(), destroy()} (panels/index.js:2-20).
- panelStateTimes(scene) is merged into render states (player.js:1134-1142).

INTERACTIVE SANDBOX (interactive.js:3-12)
- postMessage messages {type:'ready'|'run'|'heartbeat'|'error'|'pause'|'resume'}.
- Messages are accepted only when ev.source is the iframe and origin === 'null'.

CLOCK (clock.js:37-56)
- maxExtrapolation 0.25 s, resyncThreshold 0.5 s, maxStall 8 s, rate 0.25..4.
- mediaMaster base = scene.start + audio_offset (player.js:756, 997).

NARRATION ENVELOPE
- TimedScene.audio_envelope is base64 with one byte per frame from audio_offset; audio_envelope_fps defaults to 30 (audio.js:18, 66-72).

CSS CUSTOM PROPERTIES
- --caption-lift (stage px) is set on `.aadhi-player` (player.js:612) and read by player.css:1578 and dynamic.css:751.
- Zone variables --board-x/y/w/h, --side-*, --title-*, --media-* (layout.js:154-170).
- --fit (board.js:429); --p (quiz ring).

STAGE GEOMETRY (classic)
- 1920x1080; the caption band is y ≥ 900 (layout.js:25-26, 103).

SECURITY INVARIANTS (dom.js:22-30, 62-65)
- Never innerHTML. The URL allow-list is http, https and blob, plus data:image/png|jpeg|gif|webp;base64.
- id and name attributes are forbidden unless __trusted.
- No CDN: CSP script-src 'self'; media-src 'self' blob: plus the media prefix; connect-src 'self' (aadhi/security/headers.py:63-65).

## conventions
GENERAL
- Every JS file starts with `// @ts-check` and is typechecked with tsconfig.json: checkJs, strictNullChecks, include web/js/**/*.js.
- Types are JSDoc typedefs; @typedef imports point at '../shared/types.js'.
- Native ES modules, no bundler. Third-party libraries are vendored under /web/vendor and loaded lazily (libs.js).

MODULE HEADERS
- Each module opens with a docblock stating its contract, the modes it serves and links to docs (ARCHITECTURE §, DYNAMIC_VIDEO_DESIGN §, INTERFACES.md).
- British spelling (colour, behaviour, normalise). Sentences explain why, and the docs cite file:line.

DOM
- DOM is built only with shared/dom.js h()/svg(): text goes through `text:` or text nodes, classes as string, array or object, style as an object.
- Model ids go into data-* attributes only.
- `<iframe>`/`<script>` need `__trusted`.
- Listeners and timers are registered through `Disposer` and released in destroy().

PURITY AND DETERMINISM
- Pure, DOM-free modules (schedule.js, mascot-state.js, presenter-vocab.js, layout.js, kenburns.js, panels/timing.js, graphMath.js, skillTreeLayout.js, syncSummary.js, format.js) say "No DOM access; safe to import from Node tests".
- Classic render-mode rules: everything visible is a function of (scene, t); render mode disables transitions and animations through CSS (`.render-mode *`, `.ap-panel[data-mode=render] *`); chart, graph and 3D draw statically; no Math.random in rendering.
- Exact float comparisons use the same values as stateTimes; panels add a 1e-6 tolerance.
- The Dynamic determinism bans of §7.4 apply only to web/js/dynamic/** and web/js/motion/** and dynamic.css, NOT to the player. clock.js, mascot.js and analytics.js legitimately use performance.now, Date.now and timers.

ERROR HANDLING
- Non-fatal problems go through Player._error, which emits 'error' or falls back to console.warn.
- Scene, panel and stage failures never throw out of the frame loop (try/catch around update; buildPanel falls back to emptyPanel).
- Failed play() promises are caught. AbortError is ignored; NotAllowedError triggers the 'blocked' flow.
- render.js records failures on window.aadhiRender.error {code, message} using RenderError codes (token_missing, timeline_http, player_api, fonts_missing, timeout, ...).

INJECTION FOR TESTS
- PlayerDeps (now, requestFrame, cancelFrame, renderTex, texIdle, loadPrism, createPanel, panelStateTimes, sendAnalytics, waitForFonts, createDynamicStage) and RenderDeps (window, fetch, importPlayer, loadMathJax, texIdle, frame, loadTimeoutMs).
- Modules autoboot only when their page marker is present: `__AADHI_NO_AUTOBOOT__`, `data-aadhi-render=page`.

TESTS
- `node --test "web/tests/**/*.test.{js,mjs}"` (package.json).
- jsdom through web/tests/player/_dom.js installDom() before dynamically importing modules. It fakes media playback (advanceMedia), ResizeObserver and fonts.
- Fixtures: web/tests/player/_fixture.js and web/tests/dynamic/_fixture.js.
- Test folders: web/tests/player/* (24 files), web/tests/panels/*, web/tests/render/* (fonts, render, sandbox), web/tests/dynamic/player.test.js (the Dynamic branch of the Player, with a fake stage).
- Style: tests named with plain-sentence descriptions; assert/strict; afterEach destroys players.

CSS
- Everything inside .ap-stage is in stage pixels; controls use real CSS px.
- Every animation needs a render-mode and a prefers-reduced-motion off switch.
- Classic classes are prefixed `ap-` (player), `bi-` (board items), `rt-` (rich text); the Studio uses `st-` and unprefixed names; Dynamic uses `dyn-`.
- Class names and @keyframes names are global across player.css, panels.css and dynamic.css on the same pages, so new names must be unique (see the collisions under bugs).

## dynamic_status
BUILT IN THIS SLICE (B0/B1)
1. The Player's Dynamic branch exists and is tested (web/tests/dynamic/player.test.js):
   - Detection: isDynamicTimeline (player.js:97). Live and preview only; render stays classic (315; test at player.test.js:131).
   - Stage sized from Timeline.width/height, so a reel is 1080x1920 (319, 769-779). Teardown destroys the stage and restores 1920x1080 (399-403).
   - No MascotController, CaptionsView, SpeechMeter or IntroView usage for Dynamic (317-337).
   - createDynamicStage is imported on demand (366-378) with opts {mode:'live', captions, reducedMotion, onInvalidate}.
   - _renderDynamicFrame (711-731) paints `stage.update(t, {playing, rate, seeked})`, keeps the narration as master clock (_syncNarration), ducks the branding BGM on 'beat' phases and emits timeupdate.
   - _enterDynamicScene (738-762) switches narration, preloads the next scene's audio, tracks scene_enter/scene_complete and emits scenechange.
   - The captions toggle maps to stage.setLayers({captions}) (599).
2. shared/types.js has every v3 typedef (§6.7/§8.2) plus V3_BOARD_ITEM_KINDS, DYNAMIC_FEATURES and READER_SCHEMA_VERSION=3 (B0).
3. render/render.js and render.html size the page from the timeline (B0 format plumbing). The classic contract v2 is unchanged, as §7.3 requires.
4. watch.html and index.html load dynamic.css; render.html does not.
5. Player modules reused by the Dynamic engine: schedule.js, richtext.js, icons.js, kenburns.js, shared/dom.js, shared/libs.js (imports at dynamic/state.js:8, stage.js:22, media.js:19, render.js:22-25, templates/board_generic.js:16-24).

NOT BUILT (design items that touch this slice)
- §9.7 live player:
  - Checkpoints (predict/check) that pause for an answer with "Show answer": nothing in the player. Dynamic quizzes are drawn by board_generic with no buttons (grep: no click/button in web/js/dynamic), so Dynamic lectures record no quiz_answer analytics at all. Classic quiz scenes never pause (scenes/quiz.js:2-8).
  - Music Off/Low/Normal: none.
  - The new analytics events (checkpoint answered/skipped, prediction, music level, captions toggled, seek-backs): ANALYTICS_EVENTS is still the 8 classic events (types.js:666; aadhi/models.py:524).
- §5.12 live-player music: not built. There is no `<audio>` -> MediaElementAudioSourceNode -> GainNode automated from Timeline.music.duck, and no SFX AudioBuffers from Timeline.sfx. player.js never reads timeline.music or timeline.sfx, and no backend code populates them yet (no MusicPlan(/SfxCue( constructors outside schemas).
- §9.6 safe-zone overlay toggle in watch previews: none.
- §7.3 allowed-difference analytics flags and the low-end frame-time fallback (24 ms for 2 s): none (B4/W2).
- Portrait page containers: none. `.watch-player` (player.css:2203-2213) and the Studio `.preview-box` (studio.css:2480-2486) are fixed at 16:9. The B0 "Open after B0: a portrait Player" item (design line 2427) is therefore only half done: the stage is sized, the page is not.
- Caption size for the Dynamic stage: none (stage.js has only setLayers).
- reducedMotion is read once at construction (player.js:196-197), not live.

DIVERGENCES FROM THE DESIGN
- §7.3 says stage.js plugs into the player shell for "controls, clock, captions toggle, analytics, checkpoint interaction". Checkpoint interaction and caption size are missing.
- §7.3 says "Each requestAnimationFrame reads t from clock.js and calls stage.update(t)". It does; the player also runs a 250 ms background ticker in hidden tabs that repaints the stage (500-512).
- Quiz audio diverges three ways:
  - §4.10/R-49 wants one soft tick 150 ms before the reveal plus a chime.
  - The B1 Dynamic MP4 mixes the classic per-second ticks plus the ding (aadhi/compose/motion/engine.py:482-500).
  - The live Dynamic player plays neither.
- BGM in Dynamic live mode is the classic branding loop at 0.5x while speaking (audio.js:374; player.js:335, 729). The MP4 uses ffmpeg sidechaincompress with fades, gated by RENDER_BGM (ffmpeg.py:1023-1037; motion/engine.py:942-944). Design §5.12 replaces both with MusicPlan.

## gaps_todos
STUBS AND EXPLICIT GAPS
- mascot.js:534-540 loadRig() always resolves null ("Not implemented yet"); presenter-vocab.js:14 says the same of the rig.
- There are no TODO/FIXME/XXX markers anywhere in the slice (grep).

HALF-DONE OR MISSING FEATURES (with locations)
1. Dynamic interaction: no checkpoint buttons, pause or "Show answer". _renderDynamicFrame never touches sceneView or playSound (player.js:711-731).
2. Dynamic audio: no MusicPlan or SFX playback; SoundEffects is created for Dynamic but never used (player.js:334); no music level control in controls.js.
3. Caption size is inert for Dynamic:
   - Player.setCaptionSize only touches CaptionsView (player.js:616-619).
   - Controls takes captionSize from player.captions, which is null for Dynamic (controls.js:100, 334).
   - So the "Aa M" button cycles nothing, and the watch.js captionSize preference (180) is ignored for Dynamic.
4. Portrait containers: player.css:2203-2213 and studio.css:2480-2486 are fixed at 16:9.
5. Side-panel frame info is never delivered:
   - panels/index.js:13-14 documents update(t, state, frame).
   - The player calls panel.update(sceneT, state) (player.js:788), and the panel-loader wrapper takes (t, state) only (panel-loader.js:102-104).
   - So manimVideo.js:247-252 (frame.rate) is dead code and the rate is always estimated.
6. The PanelContext typedef lacks stageEl, which the player passes (types.js:635-640 vs player.js:960, cast to any).
7. TYPE_LABELS (controls.js:43-55) has no v3 or `clip` scene types, so untitled scenes fall back to 'Scene'.
8. Font maps differ:
   - render/fonts.js:30 LANGUAGE_SCRIPTS lacks sa/ne, which player/fonts.js:13-14 has. The content scan still detects them.
   - player/fonts.js BASE_FONTS (20-29) preloads only Inter, Outfit and JetBrains Mono. The B2 Dynamic faces will arrive late in live mode; the stage relayouts on 'loadingdone' (dynamic/stage.js:366-375).
9. Doc mismatch: base.css:154-158 says MathJax styles are static with "no runtime <style> injection", but libs.js:118-123 appends mj.svgStylesheet() at runtime.
10. tokens.css:49-53 --side-*/--board-* percentage variables are unused by layout.js (it uses px zones).

MISSING TESTS
- No test checks CSS cascade conflicts between player.css and panels.css (duplicate @keyframes and class names; see bugs).
- web/tests/dynamic/player.test.js does not cover:
  - narration master-clock behaviour with Dynamic;
  - analytics for Dynamic;
  - caption size;
  - portrait containers.
- No test covers a classic timeline whose Timeline.width/height is not 1920x1080 in render mode.

## bugs_or_risks
1. CSS @keyframes name collision `ap-pulse` (CONFIRMED by grep; last definition wins per the CSS Animations spec).
   - Definitions: player.css:523-529 (cyan box-shadow ring for board highlights) and panels.css:253-261 (purple/gold `filter: drop-shadow` for the skill tree).
   - Load order: panels.css loads after player.css on watch.html:13-14, render.html:11-12 and index.html:16-17, so the panels version wins.
   - Effect: classic `.bi.is-highlight { animation: ap-pulse 1.8s infinite }` (player.css:516-521) pulses a purple drop-shadow filter instead of the cyan ring. Live and preview only; render mode and reduced motion disable animations.

2. CSS class collisions between player.css and panels.css (CONFIRMED: same top-level selectors in both files).
   - Selectors: `.ap-quiz-options`, `.ap-quiz-letter`, `.ap-quiz-mark`, `.ap-quiz-text`, `.ap-media-box`. panels.css wins equal-specificity properties.
   - `.ap-quiz-options {display:flex; flex-direction:column; gap:10px}` (panels.css:631-636) overrides the grid (player.css:1336-1345). The classic quiz checkpoint's `.is-two-col` (scenes/quiz.js:79; player.css:1343-1345) therefore never takes effect: options are always one column.
   - `.ap-quiz-letter` becomes 30px gold-soft instead of 46px purple (panels.css:671-682 vs player.css:1397-1407). `.ap-quiz-mark` becomes 22px instead of 34px.
   - `.ap-media-box` becomes `position: relative` with a 12px radius (panels.css:320-326 vs player.css:1206-1217). The live media corners are rounded while the MP4's composited media is rectangular.
   - render.html loads both files, so this is baked into classic MP4s and the classic goldens. Fixing it changes classic pixels and needs the `classic-change` approval path (design §3.6 / B0 note).

3. Dynamic live vs MP4 audio parity (CONFIRMED).
   - The motion-engine MP4 mixes a tick per countdown second and the reveal ding (aadhi/compose/motion/engine.py:482-500).
   - The live Dynamic path plays none: SFX are only reachable through the classic QuizSceneView ctx.playSound (scenes/quiz.js:131,137; player.js:937-939), and _renderDynamicFrame has no SFX path (711-731).
   - BGM differs: live plays the branding BGM whenever branding.bgm_url is set, with no RENDER_BGM gate, no fades and a 0.5x duck on schedule 'beat' phases (player.js:335, 729; audio.js:374). The MP4 uses sidechaincompress with a 1 s fade-in and 2 s fade-out, gated by settings.render_bgm (ffmpeg.py:1023-1037; motion/engine.py:942-944).
   - Classic live has the same BGM behaviour difference.

4. Reels on the watch page and Studio preview (CONFIRMED from CSS).
   - The Player sizes the stage 1080x1920 (player.js:319) and fits it with contain (stage.js:19-23) inside `.watch-player { aspect-ratio: 16/9 }` (player.css:2203-2213) or `.preview-box { aspect-ratio: 16/9 }` (studio.css:2480-2486).
   - A 9:16 reel is pillarboxed: on a 390 px-wide phone it shows about 123x219 px. This directly threatens the B2/B4 acceptance "legible at phone size" for the live player.

5. Caption-size control is a no-op for Dynamic (CONFIRMED; controls.js:100, 334, 342-345; player.js:616-619). Controls.captionLift uses the classic CAPTION_BOTTOM=40 (controls.js:23), but the Dynamic landscape caption band ends 48 px above the bottom (dynamic.css:772) and portrait captions are centred in their band (dynamic.css:754-756). The lift is approximate for Dynamic.

6. RISK (cross-slice, needs verification in compose/video.py): the render-mode Player never sizes its stage from Timeline.width/height.
   - _sizeStage runs only for live Dynamic (player.js:315-320), while render.js sizes the page and viewport from the timeline (render.js:41-61, 308).
   - aadhi/compose/timeline.py:776 derives width/height from screenplay.presentation.format even when `dynamic` is False (731).
   - So a portrait screenplay served or rendered while DYNAMIC_ENABLED is off (current_timeline rebuilds classic: aadhi/api/timelines.py:110-138) gives a classic timeline of 1080x1920. /render-frame would then get a 1080x1920 page holding a 1920x1080 stage scaled by 0.5625, with media rects in mismatched coordinates.

7. Dynamic quizzes record no analytics. There is no quiz_answer path: the stage has no buttons, and the _onSceneEvent wiring exists only for classic scene views (player.js:936, 1009-1015). Per-scene analytics are otherwise intact.

8. Wasted work in Dynamic mode:
   - SoundEffects preloads the tick/ding files (audio.js:312-318) and never plays them.
   - _renderDynamicFrame computes the classic sceneStateAt every frame (player.js:719), only for bgm ducking and `this.state`, while the stage computes its own state again.
   - In hidden tabs the 250 ms background ticker repaints the Dynamic stage (player.js:500-512).

9. Panel frame info is dropped at two layers (player.js:788; panel-loader.js:102-104), so manim panels follow the playback speed only through RateEstimator (manimVideo.js:249-252). This causes visible correction seeks after rate changes until the estimate converges.

## b2_b3_touchpoints
B2 (visual system: templates, themes, fonts, icons, cues, textfit, zones, kinetic, direct, events)

Shared player modules that B2 templates will import (keep them classic-stable; they are de facto frozen):
- schedule.js: planScene/sceneStateAt/selectCue/sceneIndexAt (dynamic/state.js:8, stage.js:22, board_generic.js:24).
  - events.py output (TimedScene.events, typed in types.js:264-279) must extend the state in dynamic/state.js, not in schedule.js.
  - Any schedule.js change must keep stateKey/stateTimes output identical for the classic goldens (schedule.js:394-505).
- richtext.js: mountTex/plainText/renderRich (board_generic.js:22) and mathSpans/texNode (dynamic/render.js:25).
  - `[[term]]` renders as span.rt-k (richtext.js:358-359), which cues.js cue_term styling will hook.
  - The classic `.rt-k`/`.rt-code` CSS (player.css:986-1007) leaks into the stage on watch.html. B1 resets it in the stage; new templates must keep that reset.
- icons.js: icon() with 21 fixed paths, unknown -> 'info' (icons.js:10-41; used by board_generic.js:23). The B2 Lucide/Tabler offline index for `icon_hint` (§4.12) needs its own module or an extension here.
- kenburns.js (dynamic/media.js:19).
- shared/dom.js h/svg/safeUrl; shared/libs.js loadMathJax/texIdle/loadLib('chartjs').
- panels/chart.js buildChartConfig(spec, {animate:false, render:true}) (chart.js:22) and panels/graphMath.js are reusable for chart_build-style templates. §7.4 bans Chart.js animations in dynamic/**.

Fonts:
- player/fonts.js BASE_FONTS/fontSpecsFor (fonts.js:20-44) are called from Player.load (player.js:362-364) with a 1500 ms live timeout (105).
- The B2 faces (Poppins, Lexend, Atkinson Hyperlegible Next, Anek) are not preloaded for live. Add the presentation's display/body faces there, or rely on the stage's loadingdone relayout (dynamic/stage.js:366-375).
- New vendored faces go into /web/vendor/fonts.css, which tokens.css:3 imports on every page.

Stage options and controls:
- Player.load's createDynamicStage call (player.js:369-377) is where caption style/size, the safe-zone overlay toggle (§9.6) and any per-viewer look options would be passed.
- setCaptionSize (616-619) and controls.js (100, 334-345) need to route the size to the stage.
- Controls.captionLift (controls.js:23-39) assumes the classic band (bottom 40). Kinetic captions (clean_pill, bold_outline) in 9:16 sit in a different band.

Page containers:
- player.css:2203-2213 (.watch-player 16:9) and studio.css:2480-2486 (.preview-box 16:9) must become format-aware for 9:16 gallery and phone-size legibility.

Dev gallery:
- B2's web/dynamic/gallery.html can reuse the Player shell or call createDynamicStage directly. watch.html shows the expected stylesheet stack (tokens, base, player, panels, dynamic; watch.html:11-15).

B3 (audio: personas, casting, history, calibration, music providers, mix.py, live-player audio)

Construction of live audio:
- Player.load (player.js:332-340) builds NarrationDeck, SoundEffects(branding tick/ding) and BackgroundMusic(branding.bgm_url, bgm_volume). For Dynamic (`dynamic` flag, 315), create a music bus from `timeline.music` (MusicPlan: cues, duck [(t abs, gain_db)], mode, level; types.js:298-318) and an SFX bank from `timeline.sfx` (SfxCue {t abs, sound, gain_db}; types.js:320-325). Skip the branding BGM when a MusicPlan is present.

Per-frame hook:
- _renderDynamicFrame (player.js:711-731): replace `bgm.setDucked(state.phase === 'beat')` (729) with evaluation of the duck curve at absolute t.
- Fire SFX whose t falls in (prevT, t] only when `playing && !seeked`, following the classic pattern in scenes/quiz.js:117-137 (seeked frames are silent).

Transport hooks:
- play() (player.js:439-469): resume the AudioContext inside the user gesture, following SpeechMeter.attach's rule (audio.js:242-276).
- _pauseOutputs (525-531); seek (540-553; re-sync music currentTime and the gain schedule); setRate (567-572; music playbackRate); _applyVolume (587-593; music GainNode and SFX gain); _teardownTimeline (391-422; destroy the new parts); visibilitychange resume (347-353).

audio.js (new classes go here):
- Reuse mediaUrl/safeUrl, isSameOrigin (98-105) and releaseMedia (108-116).
- Cross-origin music routed through WebAudio is silent without CORS and crossorigin="anonymous" (needsSpeechMeter's guard at 79-81 is the precedent). Design §5.12 says to serve from the app origin.
- CSP allows media-src 'self' blob: plus the media prefix, and connect-src 'self' for fetched SFX (aadhi/security/headers.py:63-65).

Viewer music control (Off / Low −26 LU / Normal):
- controls.js right group (216); emitPrefs (347-351); watch.js prefs (174-185, PREFS_KEY 17).
- Add a PLAYER_EVENTS entry (player.js:58-61).

Analytics:
- The new events must be added to ANALYTICS_EVENTS (types.js:666-668) AND aadhi/models.py:524 plus the per-event data validation in aadhi/api/routers/analytics.py (around line 96).

Parity:
- The MP4 audio today is motion/engine.py:482-500 (ticks and ding) plus 942-944 (branding BGM). When mix.py lands, the live player must use the same Timeline.music.duck breakpoints and Timeline.sfx times (§5.12 "The same list drives numpy (MP4) and Web Audio (live player)"). The current live Dynamic path plays neither ticks nor ding (bug 3).

Narration:
- It stays on NarrationDeck <audio> as the master clock (audio.js:125-226; clock.js mediaMaster 249).
- Speaker gains are baked into the scene audio, so no player change is needed.
- TimedScene.audio_envelope stays classic-mascot only (player.js:690).

## files
- web/watch.html (50): Watch/preview page shell (CSP script-src 'self', module watch.js). Loads tokens, base, player, panels and dynamic CSS in that order. || [data-player] box (31), [data-title] (28), keyboard hint (37-40), stylesheet order (11-15): player.css before panels.css (causes the cascade collisions below)
- web/render.html (69): Classic render page served at /render-frame. Transparent 1920x1080 by default; the --render-stage-width and --render-stage-height variables are set from the timeline. Optional glass-panel colours via the html[data-render-glass] fragment option. || html[data-aadhi-render=page] (2), inline <style> with stage vars (14-41), glass overrides (46-62), [data-aadhi-render-root] (67); dynamic.css is NOT loaded (classic only)
- web/index.html (32): Studio SPA shell (web/js/studio/app.js). Loads studio, player, panels and dynamic CSS so the editor preview can embed the Player. || [data-studio-root] (22); CSS order studio, player, panels, dynamic (15-18)
- web/js/player/watch.js (214): Boots the watch/preview page: parses the route, fetches the lecture, builds the Player and Controls, shows loading and error states, persists viewer preferences. || PREFS_KEY 'aadhi_player_prefs' (17); parseRoute (28; /watch/{6-200 token}, /preview/{id}); fetchLecture (51; GET /api/public/watch/{token} -> mode live + analytics {shareToken}; GET /api/versions/{vid}/timeline -> mode preview, analytics null); describeError (81); boot (143; Player opts from prefs 176-185; Controls 194; pagehide/bfcache handling 197-206); __AADHI_NO_AUTOBOOT__ (212)
- web/js/player/player.js (1264): The Player: timeline loading, transport, the classic/Dynamic split, per-frame rendering, narration sync, scene mounting, preloading, analytics hooks, the render-mode API. || PLAYER_EVENTS (58-61); PlayerDeps incl. createDynamicStage (63-77); isDynamicTimeline (97); NARRATION_DRIFT .25, RESYNC 1000ms, LIVE/RENDER font timeouts, BACKGROUND_TICK_MS 250 (101-108); constructor layers bg/scene/intro/captions (205-211), Clock ticking unless render (212-217); load (301-385: dynamic sizes stage 319; classic Mascot + CaptionsView 321-328; live audio 332-340; analytics 342-344; dynamic stage import/create 366-378); _teardownTimeline (391); play (439), pause (477), _syncBackgroundTicker (500), seek (540), seekScene (559), setRate (567), setVolume/setMuted/_applyVolume (575-593), setCaptions (596; dynamic -> setLayers), setCaptionLift (608; --caption-lift), setCaptionSize (616; classic only); _renderFrame (629; end handling 632-643; intro 652-662; classic state/scene/panel/captions/narration/mascot 663-702); _renderDynamicFrame (711); _enterDynamicScene (738); _sizeStage (769); _syncNarration (797); _stopAtEnd/_finishEnd (852/865); _enterScene (899); _onSceneEvent quiz_answer (1009); _preload/_warmTex/_takePreloaded (1021-1090); _track (1103); render API states (1124), introStates (1148), renderState/_renderStateNow (1166/1176), renderIntro (1214), _settle (1232); destroy (1253)
- web/js/player/clock.js (266): Master playback clock: performance.now time at the playback rate, slaved to the narration media element when it agrees, with capped extrapolation, a stall give-up and a monotonic guard. || MIN_RATE .25 / MAX_RATE 4 (37-38); Clock (40): time() (98; synced if |mt-ref| <= 0.5 s, or 1.0 s while synced; extrapolate <= 0.25 s past the last media update; give up after maxStall 8 s; never backwards except seek), play (139), pause (149), seek (161; resets sync), setRate (175), setMaster (186), onTick (199; rAF only while playing and ticking); mediaMaster(el, base) (249; null when paused/ended/error); isBuffering (264; readyState<3 or seeking)
- web/js/player/schedule.js (576): Pure scheduling. The scene's visible state is a function of (scene, t). It also produces render-mode state times and keys. Shared with web/js/dynamic (state.js, stage.js, board_generic.js). || selectCue (65); TERMINAL_MAX_LINES 200 (87); terminalOutputLines (95); contentEnd (109; hold_seconds); terminalLineTimes (127); syncPlan (158; sync_cues var/emphasis/output/focus); planScene (192; WeakMap cache: beats order, slotEnd, revealAt, fillAt, cues, narrationStart/End, revealEnd, panelShowAt, terminal); sceneStateAt (285; phases lead/beat/pause/countdown/reveal/tail; returns the SceneState fields of types.js:581); stateTimes (394); stateKey (429; 's{index}-{fnv1a}', optional parts appended only when present); normalizeExtraTimes (459); renderKeyAt (483); renderStateList (495); sceneIndexAt (514; -1 during intro, clamps past end); introCardAt (538); chapterIndexAt (551); conceptStateAt (566)
- web/js/player/captions.js (63): Classic caption overlay (live/preview). Text comes from SceneState.caption via textContent. Three sizes. || CAPTION_SIZES s/m/l (14); CaptionsView (16): show (40), setEnabled (49; .is-off), setSize (55; data-size), aria-live off; re-exports selectCue
- web/js/player/analytics.js (197): Batched learner analytics, POSTed to /api/analytics/events with keepalive and the CSRF header. Viewer id is per session. || ENDPOINT, VIEWER_KEY 'aadhi_viewer_id' (sessionStorage), MAX_EVENTS_PER_REQUEST 100, MAX_BODY_BYTES 30KB (12-15); randomId (24); viewerId (45); Analytics (83): track (116; filters by ANALYTICS_EVENTS; scene_t), body (129; share_token else version_id), batches (142), flush (164; a network error re-queues once, an HTTP error drops), destroy (187); 10 s flush timer, pagehide/visibility flush
- web/js/player/audio.js (410): Live/preview audio plumbing (render mode creates none of it). || ENVELOPE_FPS 30 (18); decodeEnvelope (28); envelopeLevel (66; TimedScene.audio_envelope); needsSpeechMeter (79; only when every narration URL is same-origin); isSameOrigin (98); releaseMedia (108); NarrationDeck (125; two <audio> slots, use (177), preload (198), setVolume/setRate/pauseAll); SpeechMeter (228; WebAudio analyser, attach on user gesture only while running); SoundEffects (305; tick/ding <audio>, play(name)); BackgroundMusic (350; looping branding BGM, volume = level*volume*(ducked?0.5:1) (374), setPlaying/setDucked/setVolume)
- web/js/player/controls.js (597): Watch/preview controls: play, previous/next scene, volume, time, scrubber with chapter marks and hover tooltip, captions and size, speed, scene drawer, fullscreen, keyboard, auto-hide, caption lift above the bar. || SPEEDS [0.75,1,1.25,1.5], SEEK_STEP 5, SEEK_JUMP 10 (19-21); CAPTION_BOTTOM 40 / CAPTION_GAP 14 (23-25); captionLift (35); TYPE_LABELS (43); sceneLabel (61); Controls (83): captionSize taken from player.captions (100), bindPlayer (250), syncCaptions (330), cycleCaptionSize (342), emitPrefs (347), stepScene (354; previous restarts the scene when >3 s in), bindScrubber (422; one analytics seek per drag), bindKeyboard (481; space/k, arrows, shift+arrows, j/l, p/n, c, m, f, Esc), bindIdle (549; 2600 ms), updateCaptionLift (579)
- web/js/player/stage.js (106): Logical stage (1920x1080 by default; the player resizes it for Dynamic reels), CSS-transform contain-fit into its viewport, coordinate conversion. || computeFit (19); stageRect (37; w/h aliases); Stage (42): fit (71), toStageRect (84), rectOf (95), ResizeObserver
- web/js/player/layout.js (170): Classic stage zones per mascot position (board, side, title, media, caption band). || STAGE_WIDTH 1920 / STAGE_HEIGHT 1080 (25-26); POSITIONS (39); positionKey (71); computeZones (92; captions {260,900,1400,140}); fitAspect (116); mediaAspect (133); intersects (144); applyZones (154; --board-x... CSS vars, data-position)
- web/js/player/intro.js (191): Classic intro: logo video, then title cards. Live syncs the logo video to the clock; render shows cards at full opacity. Dynamic timelines carry no intro (compose/timeline.py:734). || CARD_FADE .6 (17); cardOpacity (25); introStates (37); introKey (51); IntroView (56): covers (96), update (117), _syncLogo (138), setVolume (163), renderAt (177)
- web/js/player/mascot.js (711): Classic background layer: looping branding mascot clips with frame-gated crossfades, a poster fallback, a recovery watchdog, the speech bob and the cue bubble. Not created for Dynamic. || MASCOT_TIMING (53); posterUrl (94); MascotController (109): clipUrl (209), setPosition (220), setPlaying (375), tick watchdog (395), setLevel (449), mountCue (466), setState (486), getStatus (512), loadRig stub (538, 'Not implemented yet')
- web/js/player/mascot-state.js (176): Aadhi's behaviour state and cue bubble as pure functions of (scene, t). Used by sceneStateAt, stateTimes and stateKey. || THINK_MIN_SILENCE 1.2, THINK_DELAY .6 (42-44); CUE_ANCHORS / CUE_SIZE (47-53); sceneRole (72); wantedBehaviour (102); cueAnchor/cueRect (127/137); mascotStateAt (152); mascotStateTimes (167)
- web/js/player/presenter-vocab.js (117): Behaviour, expression and gesture vocabulary with nearest-capability fallback chains (pure). || BEHAVIOURS/EXPRESSIONS/GESTURES (18-22); *_FALLBACK (25-56); CLIP_CAPABILITIES (66); mapVocab (82); actingFor (109)
- web/js/player/board.js (475): Classic board renderer: typed BoardItems become DOM, then per-state classes (visible, filled, highlight, active, unsaid, emph), a deterministic font fit and keep-in-view. || LABELS (25); prismLanguage (76); MIN_FIT .62 (56); BoardView (89): renderItem (179; 14 classic kinds only, unknown -> paragraph), renderFormula/Code/Table/Figure/Step (251-357), update (363), ready (399), fit (427; 7-step binary search on --fit), keepInView (450)
- web/js/player/richtext.js (367): Rich-lite tokenizer (**b**, *i*, `code`, $tex$, [[k]], escapes) that builds DOM only, never parses HTML. Also a TeX conversion cache. Shared with dynamic/board_generic.js and dynamic/render.js. || parseRich (72; flanking rules, scan budget), plainText (248), mathSpans (259), texNode (288; 400-entry cache, clones), mountTex (312; is-pending/is-error), renderRich (338; classes rt-b/rt-i/rt-k/rt-code)
- web/js/player/syncSummary.js (161): A scene's sync cues and beat timing described in words, for the Studio preview (pure). || MAX_KEY_MOMENTS 8 (16); formatSeconds (60); syncSummary (107)
- web/js/player/panel-loader.js (120): Optional dynamic import of panels/index.js plus a safe wrapper for panel instances. || emptyPanel (30); loadPanelModule (49; memoised import, override for tests); loadPanelFactory (74); buildPanel (87; the wrapper's update takes (t, state) only and drops the frame argument, 102-108)
- web/js/player/fonts.js (71): Live/render font readiness for the base families and the lecture's Indic script. || SCRIPT_FONTS ta/hi/mr/sa/ne/te/kn/ml (9-18); BASE_FONTS Inter 300-700, Outfit 400/700/900, JetBrains Mono 400 (20-29); fontSpecsFor (36); ensureFonts (52; never rejects, optional timeout)
- web/js/player/icons.js (65): Inline SVG stroke icons (21 names) for the player and dynamic board_generic. || PATHS (10-30); FILLED (32); icon(name) (40; unknown name -> 'info'); ICON_NAMES (65)
- web/js/player/kenburns.js (43): Pure Ken Burns pan/zoom. Also used by dynamic/media.js. || kenBurnsAt (26), kenBurnsTransform (41)
- web/js/player/scenes/index.js (53): Picks the scene view class from TimedScene.type. || viewKind (20: chapter_card->card, simulation/ai_video->media, quiz_checkpoint->quiz, interactive, else board); createSceneView (40)
- web/js/player/scenes/types.js (46): JSDoc for scene views. || FrameInfo {playing, rate, seeked} (11); SceneContext (18; emit, playSound, takePreloaded, sandboxUrl); SceneView (34; update/ready/layout/mediaElement/mediaFit/setPlaying/destroy)
- web/js/player/scenes/board.js (43): Board scene view wrapping BoardView. || BoardSceneView (14): update, layout -> board.fit, ready
- web/js/player/scenes/card.js (39): Static chapter card scene. || ChapterCardView (13)
- web/js/player/scenes/quiz.js (160): Classic quiz checkpoint: clickable options (live/preview), countdown ring with a tick each second, ding at the reveal, then reveal and explanation. Never pauses the timeline. || QUIZ_LABELS (20); QuizSceneView (28): options as <button> when not render (47-59); choose (91; one answer, emits quizanswer {sceneIndex, sceneId, choice, correct}); update (113; audible = live && playing && !seeked (117); tick per remaining change (131); ding on reveal (137)); .ap-quiz-options.is-two-col (79)
- web/js/player/scenes/media.js (235): Simulation and ai_video scenes: aspect-fitted media box, video kept at scene time (loop or freeze), Ken Burns stills. Render mode leaves a hole and reports its rect. || DEFAULT_MEDIA_ASPECT 16/9 (29); titleFitsOneLine (44); placeMediaBox (59); mediaTimeAt (79); titleBand (90); MediaSceneView (100): syncVideo (186; drift .35), mediaElement/mediaFit (217-224)
- web/js/player/scenes/interactive.js (194): p5 sketch in the sandboxed iframe /sandbox/p5 using the postMessage protocol, with a watchdog. Render mode shows a poster. || SANDBOX_URL, LOAD_TIMEOUT_MS 10000, HEARTBEAT_TIMEOUT_MS 4000 (23-25); InteractiveSceneView (33): start (68), handleMessage (90; source + origin 'null'), setPlaying (172)
- web/js/player/panels/index.js (148): Side-panel factory and dispatch, plus the shared visibility and focus wrapper. || PANEL_FACTORIES (45: skill_tree, figure, image, chart, graph, model_3d, manim, terminal, quiz, gif); factoryFor (68; resolved media kind wins); createPanel (84; update(t, state, frame) 113, mediaRect null while hidden 143-146); re-exports panelStateTimes etc. (35)
- web/js/player/panels/types.js (105): JSDoc for panels. || PanelCtx, ResolvedSidePanel, PanelSceneState, PanelFrame (63), PanelImpl, Panel, Chrome
- web/js/player/panels/util.js (215): Panel helpers: theme constants for canvas/WebGL, rich-lite stripping, stage rects, timing helpers. || THEME (11), PALETTE (31), round3, finiteOr, withAlpha, stripRich (68), normalizeMode, prefersReducedMotion, sceneDuration (105; minus hold_seconds), findStage (120), rectInStage (132; uses timeline.width), containRect (162), showNotice, nextFrame, settleWithin
- web/js/player/panels/timing.js (156): Pure panel timing that must match schedule.js (terminal lines, quiz-teaser reveal). || TERMINAL_MAX_LINES 200 (18; must equal schedule.js:87); terminalLineTimes (67); terminalOutputAt (90); countAtOrBefore (105; +1e-6 tolerance); quizTeaserRevealTime (123); panelStateTimes (138)
- web/js/player/panels/chrome.js (77): Glass card chrome (kicker, title, body, separate backdrop layer). || KIND_LABELS (14); createChrome (43; data-mode, data-visible); setChromeVisible (74)
- web/js/player/panels/chart.js (179): Chart.js panel. Live animates on first show; render is static with DPR 1. || buildChartConfig (22; animate:false in render); createChartPanel (103)
- web/js/player/panels/figure.js (98): Still-image panels, included in the screenshot. || createStillPanel (22), decodeImage (76), createFigurePanel (96)
- web/js/player/panels/image.js (13): Generated/uploaded illustration panel (same as figure). || createImagePanel (11)
- web/js/player/panels/gif.js (54): GIPHY panel, live only. Render shows the title only. || DEFAULT_GIF_ATTRIBUTION (14); createGifPanel (17)
- web/js/player/panels/graph.js (388): Function-graph panel on a hardened math.js with adaptive sampling. Live strokes progressively; render draws it complete. || DRAW_SECONDS 1.6 (39); buildGraphModel (48); drawGraph (107); createGraphPanel (237)
- web/js/player/panels/graphMath.js (366): Expression allow-list (mirrors screenplay.py), hardened math.js, sampling, ranges, ticks (pure). || EXPR_FUNCS (9); DISABLED_FUNCTIONS (15); checkExpr (76); createSafeMath (115); sampleFunction (189); splitSegments (249); autoYRange (285); niceTicks (325); formatTick (356)
- web/js/player/panels/manimVideo.js (284): Video side panel synced to scene time with rate estimation. Render leaves a hole cut out of the backdrop with clip-path. || DRIFT_TOLERANCE .3, FREEZE_EPSILON .04 (19-21); quantizeRate (43); RateEstimator (53); videoTimeAt (89); holeClipPath (102); createManimPanel (115; frame.rate path 247-252, never reached via the player)
- web/js/player/panels/model3d.js (490): three.js primitives panel: live orbit and drag; render draws one frame at a fixed angle with preserveDrawingBuffer. || RENDER_ANGLE (19); buildPrimitives (40); orbitPosition (139); fitDistance (153); model3dFactory (164); createModel3dPanel (490)
- web/js/player/panels/quizTeaser.js (140): Mini quiz panel whose reveal is time-based. A local guess is possible in mode 'live' only; no analytics. || createQuizTeaserPanel (54; interactive = mode==='live' (61); reveal at quizTeaserRevealTime)
- web/js/player/panels/skillTree.js (284): Concept map panel (SVG): done, active and todo states, with a focused subset when the full map is not legible. || MIN_LEGIBLE_FONT 11 (21); resolveConceptState (29); focusAnchor (72); chooseView (92); createSkillTreePanel (103); drawTree (188)
- web/js/player/panels/skillTreeLayout.js (751): Sugiyama layered DAG layout (pure). || LR_ADVANTAGE 1.2, MAX_UPSCALE 1.25 (90-92); normalizeConcepts (106); assignLevels (132); wrapLabel (188); countPairCrossings (225); isotonic (264); focusConcepts (291); layoutSkillTree (647); curvePath (736)
- web/js/player/panels/terminal.js (109): Terminal panel. Line count comes from SceneState.terminalLines, or from times computed in the panel. || playerLineCount (24); createTerminalPanel (40); terminalLinesAt (105)
- web/js/render/render.js (400): Classic render page boot: token from the URL fragment, timeline fetch, Player in render mode, fonts and MathJax, then the window.aadhiRender v2 API. || TIMELINE_ENDPOINT '/api/render/timeline', TOKEN_PATTERN, PLAYER_METHODS, STAGE, MAX_STAGE_SIDE (25-33); stageSize (41); applyStageSize (55); takeToken (82); readOptions glass (100); fetchTimeline (112; Bearer, no cookies); loadPlayerClass (149); assertPlayerApi (164); normalizeResult (174); titleOnlyPanels (206); installApi version 2 (258); boot (285; settle 322-333, serialized enqueue 342-349, states/show/showIntro 362-386)
- web/js/render/fonts.js (112): Render-page font preloading: base families plus the Indic scripts detected in content or language. || BASE_FONTS (14), SCRIPT_FONTS (21), LANGUAGE_SCRIPTS (30; no sa/ne unlike player/fonts.js), scriptForLanguage (39), detectScripts (50), fontRequests (76), preloadFonts (99; missing faces -> fonts_missing error)
- web/js/shared/api.js (156): Same-origin fetch client: CSRF header on mutating requests, ApiError envelope, auth-error hook, SSE. || CSRF_HEADER 'X-Aadhi-CSRF' (7); ApiError (9); onAuthError (51); api (72); get/post/put/patch/del (116-125); sse (135)
- web/js/shared/dom.js (235): Safe DOM construction. Text-only insertion; URL allow-list; forbidden tags and attributes (id/name unless __trusted). || safeUrl (38; http/https/blob plus data:image), h (62; script/iframe/... need __trusted), svg (79), applyAttrs (91), classNames, append, clear, replace, $, $$, Disposer (187)
- web/js/shared/format.js (122): Pure number, time and text helpers. || clamp (13), lerp (24), num (34), formatTime (48), formatDurationLong (61), formatRate (78), roundTo (87), fnv1a (97), truncate (111), optionLetter (120)
- web/js/shared/libs.js (164): Lazy loaders for the vendored libraries and a locked-down MathJax. || LIBS chartjs/mathjs/prism/dompurify/marked (12); loadLib (43); loadThree (58); loadPrism (66); loadMathJax (90; no page scan, require removed, ui/safe; injects svgStylesheet at runtime 118-123); renderTex (148; serialized); texIdle (162)
- web/js/shared/types.js (668): JSDoc mirror of timeline.schema.json and screenplay.schema.json, including all v3 Dynamic types, plus enum constants. || BoardItemKind incl. v3 (15-18); TimedScene incl. look/events/kinetic_captions/overlays (196-229); TimelinePresentation (236); ResolvedLook (253); VisualEvent (265-279); KineticChunk (290); MusicCue/MusicPlan (299-318); SfxCue (321); TimedOverlay (329); Presentation (375); SceneLook (415); Timeline incl. presentation/music/sfx (545-568); SceneState (581); RenderResult (617); BOARD_ITEM_KINDS (643); V3_BOARD_ITEM_KINDS (649); DYNAMIC_FEATURES (653); READER_SCHEMA_VERSION 3 (657); ANALYTICS_EVENTS (666; must equal aadhi/models.py:524)
- web/css/tokens.css (54): Design tokens (purple glass, gold). Imports /web/vendor/fonts.css. || --c-* palette incl. --c-surface-solid for render (13); --font-sans/title/mono (36-39); geometry; legacy --side-*/--board-* percentages (50-53, unused by layout.js)
- web/css/base.css (262): Reset, buttons, glass, static MathJax SVG rules, spinner. || .btn-gold/.btn-outline (121-141); mjx-container rules (160-238; comment says no runtime style injection, but libs.js does inject); .spinner and @keyframes base-spin (242-256)
- web/css/player.css (2398): Classic stage visuals (stage px), controls, watch-page chrome, render-mode overrides, reduced motion. || .ap-stage (30), .ap-layer--bg #e9e8ee (46), buffering spinner (57-84), mascot & cue bubble (94-274), zones (280-348), board and items (354-979; .bi.is-highlight animation ap-pulse 516-521, @keyframes ap-pulse 523), rich text .rt-* (986-1017), card (1086), media scenes (1155-1297; .ap-media-box 1206), quiz (1303-1562; .ap-quiz-options grid 1336-1345, .ap-quiz-letter 1397), captions (1569-1606; --caption-lift 1578), intro (1612-1666), controls (1672-2086), watch page (2092-2330; .watch-player aspect-ratio 16/9 at 2203-2213), render mode (2336-2374), reduced motion (2380-2398)
- web/css/panels.css (798): Side-panel card styles (stage px) with render-mode and reduced-motion overrides. || .ap-panel (5), .ap-backdrop (35), .is-focus pulse (59-75), skill tree (136-307; @keyframes ap-pulse 253, duplicate name), .ap-media-box (320, collides), chart/graph/3D/video/terminal (371-619), quiz teaser .ap-quiz-options/.ap-quiz-letter/.ap-quiz-text/.ap-quiz-mark (631-694, collide with player.css), render mode (751-785)
- web/css/studio.css (4080): All Studio UI styles: shell, forms, cards, modals, editor panes, timeline strip, library, visual review, videos. || topbar/nav (68-196), buttons (200-264), forms (499-656), job progress (1426-1595), editor grid (1963-2059), preview pane .preview-box aspect-ratio 16/9 (2480-2486), .preview-sync (2507), timeline strip .tl-* (2867-3150), global reduced motion (3282-3291), library (3305-3631), visual review (3635-3883), workflow/videos (3888-4080)