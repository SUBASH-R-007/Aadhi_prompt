# web-studio: Frontend Studio (web/js/studio/**, 77 files, 23,244 lines; all read in full)


## summary
The Studio is a no-bundler, native-ES-module SPA (`// @ts-check` + JSDoc, typechecked by `tsc -p tsconfig.json`, tested with `node --test` + jsdom in web/tests/studio/*.test.js). app.js boots auth (GET /api/auth/me), draws the top bar and lazily imports one view per hash route (router.js ROUTES:28-44). Every view exports `mount(container, {app, params, query, signal})` and returns `{destroy}`.

Generation form: views/newProject.js (source tabs Upload/Paste, title, "check how Aadhi read my document") plus views/optionsFields.js createOptionsForm, built from GET /api/meta. Its values go through lib/optionsForm.js buildGenerationOptions:138-242 into a classic GenerationOptions payload, sent multipart to POST /api/projects. "Regenerate lecture" (projectDetail.js:980-1035) reuses the same form, starting from the project's stored options.

The editor (views/editor/editor.js, 2,175 lines) keeps an immutable `base` / `draft` / undo-stack model. Edits go through pure transforms in lib/screenplayEdit.js, splitScene.js and sceneMerge.js / lib/conflict.js. The editor autosaves to localStorage per user and version, with optional "Save automatically". It saves with PUT /screenplay and a revision (409 runs a 3-way merge), lints with POST /lint (safe repairs), and previews through the real player (POST /timeline/preview). It also has a timeline strip, "Changes since Aadhi wrote it" with revert, job locking, and "Save as a copy".

Other pages: plan review, source report, visual review, project page (next-step card, versions, render dialog with preflight, renders, shares, jobs), projects list, videos, library and library picker, usage, analytics, API keys, admin.

Dynamic status: B0 work exists in this slice and is tested by web/tests/studio/transformSurvival.test.js. It covers transform survival of look / colour_is_content / hidden / min_seconds / sequence, blankable `question` items, splitScene remapping of Beat.answers and presentation.hook, and the newer_document / feature_unavailable messages. B1 reaches the Studio only indirectly: the editor's preview pane loads web/js/player/player.js, which draws a Dynamic timeline with web/js/dynamic/stage.js.

None of the §9 Studio UX for B4 exists yet: style tabs, Dynamic form, course picker, destination, scope/series step, Dynamic plan review, the new render dialog and export bundle, style/format chips, previews. None of the §8 / B5 editor exists either: Script view, cuts, Look inspector, New colours / New layouts, new lanes, runtime meter, editors for the new item kinds. /api/meta (aadhi/api/routers/meta.py:138-208) publishes no Dynamic flag, and the server's GenerationOptions (aadhi/pipeline/base.py ~73-112) has no Dynamic fields.

## contracts
Routes (router.js:28-44): #/login, #/change-password, #/projects, #/new, #/p/:id, #/p/:id/v/:vid/plan, #/p/:id/v/:vid/source, #/p/:id/v/:vid/edit (?scene=), #/v/:vid/review (?filter=&scene=), #/p/:id/analytics, #/usage, #/keys, #/library, #/videos, #/admin (admin role). Params are positive integers (router.js:95).

View contract (types.js:405-439): mount(container, {params, query, app, signal}) -> {destroy}. AppContext has user/meta (cached GET /api/meta)/navigate/replaceHash/toast/setLeaveGuard/reportError. InspectorCtx is in editor/context.js:21-45. Edits go through editScene(updater, {structural, coalesce, label}). The updater receives a clone of the current scene; edits are bound to a scene id (editor.js:727-737).

API calls (request -> response fields read):
- POST /api/projects, multipart {file, options: JSON GenerationOptions, title?, review_source:'true'?} -> {project, version, job} (newProject.js:293-298).
- POST /api/projects/import {file, title?} (newProject.js:338, projects.js:317).
- POST /api/projects/{id}/regenerate {options, review_source?} -> {job, version} (projectDetail.js:1014-1017). The server merges the sent options over the stored ones (aadhi/api/generation.py:63-92, projects.py:422).
- GET /api/versions/{vid} -> {screenplay, revision, status, issues, stale_scenes, has_timeline, timeline_stale, review_stage, plan, generation_meta, number}.
- PUT /api/versions/{vid}/screenplay {screenplay, revision} -> {version, issues, stale_scenes}. It returns NO screenplay (aadhi/api/routers/versions.py:183-253); the editor sets base = sent (editor.js:1181). Error codes: 409 revision_conflict / version_busy, 422 (validation incl. feature_unavailable), 409 newer_document.
- POST /lint {screenplay} -> {issues, quality.repairs[{code, scene_id, message, label, edits[{scene_id, path, before, after}]}]} (editor.js:1125; quality.js:20-22).
- POST /timeline/preview {screenplay} and GET /timeline (previewPane.js:240-242).
- POST /build {scene_ids}.
- GET /render/preflight -> {items[{scene_id, scene_number, scene_index, title, message, reason, blocking}], blocking, quality[]}.
- POST /render {burn_captions, include_intro, soft_subtitles?, allow_degraded?} (editor.js:1624-1627; server RenderBody aadhi/api/routers/renders.py:50-59). 409 codes: render_preflight, timeline_stale.
- GET /renders -> RenderSummary with options.qa {ok, frames, fps, audible, has_audio, problems, warnings} and options.warnings (projectDetail.js:680-703).
- POST /scenes/{id}/regenerate {instructions}.
- POST /scenes/{id}/revert {revision, to: generated|history, history_index?, position?}.
- GET /changes.
- GET /visual-review; PUT /visual-review/{scene_id} {state, revision?}; POST /scenes/{id}/visual {action, revision, library_item_id?, confirm_paid?}.
- POST /duplicate {label}.
- POST /plan {plan}; POST /approve-plan.
- GET /api/projects/{id}/versions/{vid}/source-report; PUT /source-review; POST /approve-source.
- POST /api/uploads multipart {file, purpose, project_id} -> {asset_key, url, kind, mime, width, height} (mediaUpload.js:129).
- /api/library, /api/library/{id}/attach {project_id} -> {asset_key}, /describe, /api/library/suggestions?version_id=.
- /api/jobs/{id}, /stream (SSE events job_event/job/end), /events?after=&limit=, /cancel, /retry; GET /api/jobs?project_id=&limit=20 and ?status=.
- /api/videos, /api/keys, /api/admin/*, /api/usage/me, /api/projects/{id}/analytics, /api/projects/{id}/shares.

/api/meta fields consumed: languages, tts{default_provider, providers[{id, label, configured, voices[]}]}, image{default_provider, providers}, llm{provider, configured, models, engines, override_allowlist}, api_keys, library, features{manim, manim_freeform, generated_images, ai_video, gifs, render}, manim_templates, limits{upload_max_mb, max_source_chars, password_min_length}, generation_defaults. There is no Dynamic flag (aadhi/api/routers/meta.py:138-154).

Invariants the transforms keep (screenplayEdit.js:3-16):
- one reveal per item;
- fills only blank BLANKABLE_KINDS items revealed at or before the filling beat;
- at most 2 highlights, only of visible items;
- board refs only in board scenes;
- quiz arrays aligned;
- ids `{scene}-b{n}` / `{scene}-i{n}`, sequential and never reused (sequentialId:155).

COMMON_SCENE_KEYS / BOARD_SCENE_KEYS (screenplayEdit.js:89-94) are checked against web/schemas/screenplay.schema.json by web/tests/studio/transformSurvival.test.js:160. Local draft format (draftStore.js:19-25): {revision, savedAt, screenplay, base}. Issue shape (validationIssues.js:16-25): {code, severity, message, scene_id, beat_id, source, fixable}.

## conventions
- Every file starts with `// @ts-check` and a module docblock in plain English (British spelling: colour, centre). Types are JSDoc only; typecheck with `npm run typecheck` (tsc), test with `node --test "web/tests/**/*.test.{js,mjs}"`, using jsdom helpers web/tests/studio/_dom.js and _views.js and fixtures web/tests/studio/fixtures.js. The transform-survival style uses "owned leaves" regexes (transformSurvival.test.js:286-330), with a shared Python fixture at tests/core/fixtures/transform_survival_dynamic.json.
- DOM is built only via `h()` / `svg()` from web/js/shared/dom.js, with text through textContent and never innerHTML. dom.js forbids `name` attributes, so radio names are set as properties (manimEditor.js:44, quizEditor.js:35). viewBox is set with setAttribute (icons.js:89).
- Pure logic lives in lib/ and the editor's pure modules (splitScene.js, sceneMerge.js, changes.js, quality.js). These are immutable, return new objects (clone = structuredClone) and are unit-testable without DOM.
- Views guard async work with `signal.aborted`, `destroyed` flags and load tokens (projects.js:160-184, projectDetail.js:586-613). Polling pauses on hidden tabs (lib/visibility.js) and backs off (jobProgress.pollBackoff).
- Editing discipline: flushPending() before any state change (editor.js:756). Undo labels: "Edit narration in scene 3" (editor.js:714). Typing coalesces per scene+field key `data-fk` (editor.js:732). Focus is restored with data-fk (focusKeep.js). Structural edits re-render the inspector.
- UI text: plain words, never colour alone (badges always have text). Raw ids, revisions and stages appear only with ?debug (lib/debug.js). Errors go through app.reportError / errorMessage(ApiError.code). Server strings are never HTML.
- Accessibility: live regions, roving tabindex, modal focus trap with inert app root, and keyboard alternatives for every drag (sortable.js).
- Storage keys: `aadhi.studio.draft.u<user>.v<vid>`, `aadhi.studio.lastUser`, `aadhi.studio.pref.<key>` (e.g. editorAutosave.u<id>, previewAuto, timelineZoom, issuesTechnical), `aadhi.sourceReport.done.v<vid>`. Drafts are wiped on sign-out and on a user switch.
- Fields left at defaults are deleted rather than set (sceneTiming.js:51-52, 94-95: `delete s.hidden` / `delete s.min_seconds`), so untouched scenes stay byte-identical. New code should do the same for v3 fields (`exclude_if` defaults on the server).

## dynamic_status
What exists in this slice:
- B0 transform survival. COMMON_SCENE_KEYS incl. hidden / min_seconds / look / colour_is_content (screenplayEdit.js:89-92). BOARD_SCENE_KEYS incl. `sequence` (94). BLANKABLE_KINDS = example_step + question, used by applyKindDefaults:260, fillOptions:737, setItemBlank:914 and repairSceneRefs:1023, 1052. convertSceneShape copies them (315, 321).
- splitScene. Part A keeps every field incl. look (splitScene.js:141). Part B copies `sequence` (138) and `hidden` (137). Beats move with all their fields (cuts, overlays, speaker, answers, voice_override). Beat.answers and presentation.hook.question_item / answer_beat follow moved items and beats (moveAnswerRefs:177-214).
- Merges are field-level, so look / presentation survive unless both sides changed them (sceneMerge.js:64-77; conflict.js:108-120 treats top-level `presentation` atomically).
- errors.js:47-56 handles newer_document and feature_unavailable.
- Batch-4 fixes (components/stage.js stageAction, editor saveAsCopy later-edits draft) have tests in web/tests/studio/batch4Regressions.test.js.
- B1 reaches the Studio only through the player: previewPane.js:254 creates `new Player(box, {mode:'preview'})`, and web/js/player/player.js:97-98, 366-375 loads web/js/dynamic/stage.js for timelines with presentation.style == 'dynamic' (only while DYNAMIC_ENABLED, aadhi/compose/timeline.py:731). A Dynamic lecture can therefore be previewed and edited today, but only if one exists: imported, or saved with DYNAMIC_FEATURES containing `presentation`. The Studio cannot create one.

How look and presentation fields survive edits:
- Scene `look` and `colour_is_content`: kept by updateScene, changeSceneType, duplicateScene (verbatim, including lock and continues_from), split part A, applyRepair, local drafts, and merges. Dropped on split part B (by design; B has no look) and by server revert (design open question). The editor never edits them.
- SidePanel.colour_is_content: kept by in-panel edits; dropped when the panel kind changes (sidePanelOfKind:1156-1172).
- Beat v3 fields (speaker, answers, cuts, overlays, voice_override_asset_key) and BoardItem v3 fields (value, unit, date, icon_hint, pins, level): kept by every spread-based edit (updateBeat:689, updateBoardItem:880-881, setBoardItemKind via applyKindDefaults `...item`:243). There is no UI to edit any of them.
- Top-level `presentation`: never edited except the hook remap in splitScene. Sent back whole on every PUT.

Not present (vs design §8-§9, B4/B5):
- Style tabs Dynamic/Presenter.
- The Dynamic form (format cards, platform-safe, destination, course picker, Style: Look/Voice/Music/Aadhi/Captions/SFX/Reel energy, Use as my default, estimate).
- Scope/series step (no scope_review stage in lessonStage.js:19-58).
- Dynamic plan review (planReview.js has no bookends, segment structure or runtime meter).
- The render dialog's destination / loudness / both versions / 2 parts / Calm / preflight one-click fixes.
- The export bundle (only MP4/SRT/VTT/chapters text: projectDetail.js:779-799).
- Style and format chips.
- Look preview strip, voice samples, music preview, safe-zone overlay.
- The B5 editor: Script view, cuts, re-voice, Look inspector, New colours / New layouts / Shuffle, runtime meter, Look/Music/Clips/Chapters lanes, new shortcuts (I/O/S/N...), editors for the new item kinds, speaker/answers/icon_hint/level/visual_intent/sequence controls, a colour_is_content toggle, and look-aware save adoption.
- B3 keys page for elevenlabs/azure (lib/apiKeys.js:14).

Divergences from docs/DYNAMIC_VIDEO_DESIGN.md:
- (a) §6.7 and §10.3 B0 cite `screenplayEdit.js:83–86, 301–304` for convertSceneShape. The code is now at 89-92 and 312-364.
- (b) §9.1 says newProject.js "already uses createTabs". It does, but for Upload/Paste source tabs (newProject.js:179-189), not for style.
- (c) The splitScene docstring (splitScene.js:16-17) says part B's look "is chosen for it when the lecture is saved". No direct.py exists (aadhi/pipeline has no direct/scope/casting/fit/music modules), and PUT does not fill looks (versions.py:249-253). Part B also never gets `look.continues_from` (§8.8).
- (d) quality.js:12-13 and previewPane.js:8-9 say the preview stage is the "same fixed 1920x1080" as the MP4. That is false for portrait Dynamic. measureBoard (quality.js:245-252) looks for `.ap-board`, which the Dynamic stage does not produce, so board-fit warnings are silently absent for Dynamic lectures.
- (e) §6.3: the classic payload builder always sends target_minutes / include_quizzes / quiz_every_n_concepts / tts_rate (optionsForm.js:157, 173-174, 213-215), which the Dynamic validator must refuse. A new builder path is required.
- (f) The preview-player keys listed in the editor (editor.js:126-137: J/L ±10 s, ←/→ ±5 s, P/N) differ from §8.8 (J back 5 s with repeats, L play with speed-ups, ←/→ word or frame).
- (g) The B0 "Open after B0" note that B5 must keep Beat.answers and the hook valid in sanitizeScreenplayRefs / deleteBoardItem / removeBeat / deleteScene / moveScene is confirmed: still not done.

## gaps_todos
- No TODO/FIXME/XXX/NotImplemented anywhere in web/js/studio (grep).
- No Dynamic UI at all (see dynamic_status). types.js GenerationOptions (127-158) and VersionSummary (19-35) lack Dynamic fields. /api/meta has no Dynamic or DYNAMIC_FEATURES flag (aadhi/api/routers/meta.py:138-154). There is no Studio admin control for DYNAMIC_ENABLED or site settings (admin.js).
- New item kinds (question, stat, step, quote, pair) are not in BOARD_ITEM_KINDS / BOARD_ITEM_LABELS / TEXT_REQUIRED_KINDS (screenplayEdit.js:44-67). The board editor shows them as "<kind> (unknown)" with only a Text field (boardEditor.js:219-220). Preview shows text only (richPreview.js:167-168), not the classic fallback map of §6.7. localProblems has no required-field checks for them (validationIssues.js:83-99).
- Blank questions are fillable in screenplayEdit, but the UI offers neither the "Start blank" checkbox (boardEditor.js:190-201, example_step only) nor the "Fills blank step" select (beatsEditor.js:156 checks only example_step).
- `clip` scene type: not in SCENE_TYPES (screenplayEdit.js:27-30), SCENE_TYPE_LABELS, SCENE_TYPE_ICONS (icons.js:95-107) or UNSPLITTABLE (splitScene.js:24-28). A clip scene with ≥2 voice-over beats can be split at a beat, giving a `content` part B: not the §8.5 time-based split. It is gated today by the `clip` feature.
- changes.js FIELD_WORDS (64-100) and EDIT_LABELS (13-30) have no entries for look / sequence / colour_is_content / cuts / overlays (fallback text such as "colour is content").
- No tests for any Dynamic form, render dialog or B5 editor (none exist). transformSurvival.test.js covers sidePanelOfKind only for the same kind (its EDITS table, 'sidePanelOfKind (same kind)').
- Editor preview frame is fixed at 16:9 (web/css/studio.css:2480-2484). A 9:16 reel shows letterboxed and small.

## bugs_or_risks
1. Render-job stepper does not know the motion engine's stages, and it regresses for classic renders too.
   - jobStages.js:18 lists render_video as ['screenshots','segments','mux','store'], and STAGE_LABELS (23-37) has no 'preflight', 'capture' or 'check'.
   - The motion engine reports 'preflight' (aadhi/compose/motion/job.py:131) and 'capture' (engine.py:938-991, most of the render), then 'mux' (1010) and 'check' (1033). Classic reports 'check' at aadhi/compose/video.py:926.
   - stageStates (jobStages.js:74-87) uses indexOf(job.stage). For an unknown stage that is -1, so every step shows 'pending'.
   - Result for a Dynamic render: the stepper sits at all-pending through the whole capture, then jumps.
   - Result for a classic render: the stepper falls back from mux=done to all-pending during 'check'.

2. Editor transforms can produce screenplays the server refuses, with no UI fix.
   - The server requires every Beat.answers to name a board item of an earlier scene (aadhi/schemas/screenplay.py:1255-1266).
   - These operations do not touch `answers`: deleteScene (screenplayEdit.js:534-543), deleteBoardItem (844-854), moveScene (432-435, also the timeline drag, editor.js:993-1011), changeSceneType board→non-board (convertSceneShape:319-358 empties the board), sanitizeScreenplayRefs (1087-1112) and repairSceneRefs. localProblems does not check it either.
   - Any of them on a lecture using `answers` gives a 422 "not a board item of an earlier scene". The beat editor has no field to fix `answers`, so the teacher's only remedy is Undo.
   - presentation.hook.question_item / answer_beat also dangle after deletes. They are not validated server-side, but are consumed downstream.
   - Reachable only where DYNAMIC_FEATURES includes `presentation`. Listed for B5 in the design's "Open after B0".

3. Side panel loses `colour_is_content` on a kind switch.
   - sidePanelOfKind keeps only title / rationale / show_from_beat_id when the kind changes (screenplayEdit.js:1156-1172), so `side_panel.colour_is_content` (screenplay.py:653) is silently dropped.
   - That flag stops the palette suffix and the Manim colour mapping for colour-critical pictures (§6.6).

4. splitScene part B gets no `colour_is_content` and no look anchor.
   - Part B (splitScene.js:122-138) does not inherit `colour_is_content`, although figure board items revealed by B's beats move into it. A colour-critical figure can lose its protection.
   - Part B also gets no `look.continues_from` (§8.8).

5. B5 save contract is not met yet.
   - save() adopts `base = sent` (editor.js:1181). The server stores `body.screenplay.model_dump(mode='json')` (versions.py:205) and returns no screenplay (249-253).
   - Today this is harmless. Once B5's direct.py fills looks on save, the editor would drop server-settled looks on its next PUT and break `lock: settled`.
   - In-flight edits will need mergeDrafts(sent, draft, res.screenplay).

6. `presentation` is merged as one atomic top-level field (conflict.js:108-120).
   - A server change (e.g. New colours bumps look_epoch) plus a teacher change to the hook would be reported as a conflict with the teacher's whole presentation kept. The server's look_epoch would be lost.

7. Regenerating a future Dynamic lecture will send fields the Dynamic validator refuses.
   - Regenerate uses the classic form (projectDetail.js:992-1017). buildGenerationOptions always emits target_minutes, include_quizzes, quiz_every_n_concepts and tts_rate (optionsForm.js:157, 173-174, 213-215).
   - The §6.3 validator will refuse these unless they are at defaults, and any edit to them will be refused.
   - Dynamic keys survive only because the server merges the request over the stored options (generation.py:70-92).

8. Shortcuts fire on focused buttons and checkboxes.
   - '[', ']', 'Delete' and '?' check only isEditableTarget (editor.js:2020), which treats checkbox / radio / button as non-editable (util.js:229-232).
   - So '[' or ']' on a focused button switches scenes. §8.8 requires the guard to cover buttons, checkboxes and radios.

9. The render dialogs always show "Include the intro (logo and title cards)" (editor.js:1586, projectDetail.js:279), but a Dynamic timeline has no classic intro (timeline.py:17-19). For Dynamic lectures the option is misleading.

## b2_b3_touchpoints
B2 (visual system) and B3 (audio) have almost no direct hooks in web/js/studio. Their outputs reach the Studio through the player (previewPane.js:254 → web/js/player/player.js → web/js/dynamic/stage.js) and through server fields that the Studio already preserves verbatim (scene `look`, `colour_is_content`, `presentation`).

Concrete touchpoints:

1. B2 `SceneLook.lock` and `direct.py` on save. The editor must adopt the PUT response at editor.js:1176-1193 (`base = sent`) and merge in-flight edits. splitScene's part B (splitScene.js:122-138) needs `look.continues_from` and the colour_is_content rule.

2. B2 template / text-fit preview.
   - quality.js measureBoard (245-252) only understands the classic `.ap-board`. Dynamic overflow measurement would need a stage API (e.g. textBoxes(), used by preflight) instead.
   - The editor preview box is fixed at 16:9 (web/css/studio.css:2480-2484) and must follow `timeline.presentation.format`.
   - timelineStrip.stripModel (timelineStrip.js:127-167) ignores TimedScene.look, events, kinetic_captions and Timeline.music / sfx; the Look and Music lanes go here. Its `intro` detection (158) is classic-only.

3. B2 gallery and themes: no Studio code. A look preview strip (§9.6) would be a new component inside the B4 Dynamic form (optionsFields.js / newProject.js).

4. B3 keys and providers.
   - lib/apiKeys.js:14 KEY_PROVIDERS and the providerLabel fallback (apiKeys.js:88-91; optionsForm.js LLM_ENGINE_LABELS:37) need elevenlabs and azure.
   - optionsForm.js:31 TTS_PROVIDERS lacks azure.
   - The voice picker voicesFor (optionsForm.js:446-458) and the Voice fieldset (optionsFields.js:270-277) become the persona and accent controls.

5. B3 job stages and usage.
   - Any new job kind or stage (re-voice, music, calibration) needs JOB_KIND_LABELS / STAGES_BY_KIND / STAGE_LABELS (jobStages.js:13-48).
   - If such a job mutates the version, it must also be added to editor.js:84 MUTATING_KINDS and visualReview.js:106.
   - New usage operations ('music', calibration) appear automatically in usage.js:145-147 (raw operation names).

6. B3 live-player audio (Music Off/Low/Normal) is player-side, not in the Studio.

Then B4/B5:
- newProject.js:114-444 needs style tabs, using components/tabs.js:21 and draftStore loadPref/savePref:179-201.
- optionsFields.js:53-321 and optionsForm.js:138-242 need a Dynamic builder that never sends the unused fields.
- /api/meta (aadhi/api/routers/meta.py:138-154) needs a Dynamic flag.
- projectDetail.js:109-114 actions, 273-305 render dialog, 742-806 downloads, 980-1035 regenerate.
- editor.js:1582-1629 render dialog.
- planReview.js:304-398.
- lessonStage.js:19-58 and common.js:25-31 for a 'scope' review stage.
- projects.js:227-286 and projectDetail.js:385-450 for chips.
- inspector.js:50-218 for the Look section and colour_is_content.
- boardEditor.js:132-221, beatsEditor.js:81-247, richPreview.js:116-170 and validationIssues.js:67-116 for the new kinds, speaker, answers and level.
- screenplayEdit.js 432-543, 643, 844 and 1087-1112 for answers and hook integrity.
- changes.js:13-100 for labels.
- editor.js:96-138 and 2016-2042 for shortcuts.
- sceneList.js:24-41 and util.js:208 for the runtime meter's estimate base.

## files
- web/js/studio/app.js (397): Studio bootstrap: auth, top bar, lazy view loading, global error/beforeunload handling || VIEWS:32-48 route->lazy import; NAV:51-59 (API keys only when meta.api_keys.personal_enabled); startStudio:65; AppContext impl:114-153; mountView:247-303 (per-view AbortController, leave guard scoped to current mount); signOut clears drafts:208-225; beforeunload on [data-dirty=true]:344-349
- web/js/studio/brand.js (4): Brand strings || APP_TITLE, APP_SUBTITLE
- web/js/studio/router.js (357): Hash router with leave guard and history-index stamping || ROUTES:28-44; parseHash:58; matchRoute:83 (positive int params); href:153; guardRoute:184; HashRouter:214 (restoreAfterRejection:304, dispatch:328)
- web/js/studio/types.js (441): JSDoc-only API shapes || VersionSummary:19-35 (no style/format); ProjectSummary:37-55; LessonStage:60; RenderSummary:74; GenerationOptions:127-158 (classic fields only, no Dynamic fields); SceneVisual:340; SceneChange:381; AppContext:405-418; ViewMount:434
- web/js/studio/util.js (276): Pure helpers || debounce:19; clone (structuredClone):60; deepEqual (undefined==absent):73; SPEECH_WPM=150:208; estimateSpeechSeconds:214; isEditableTarget:223-234 (checkbox/radio/button NOT editable)
- web/js/studio/errors.js (73): ApiError code -> plain-words message || errorMessage:14; newer_document:47-48 and feature_unavailable:49-56 (Dynamic B0 codes); isAuthError:71
- web/js/studio/components/apiKeyCard.js (267): One API-key card (masked entry, test, remove) || apiKeyCard:49; key hygiene (input cleared, scrubSecret)
- web/js/studio/components/badges.js (66): Status/issue/stale badges, progress bar || statusBadge:28; issueBadges:39; staleBadge:50; progressBar:59
- web/js/studio/components/chart.js (155): Chart.js wrapper with data table || createChart:45; palette:22
- web/js/studio/components/chips.js (148): Chip list input and toggle multi-select || chipsInput:25; chipsSelect:106
- web/js/studio/components/dropzone.js (116): File drop zone || dropZone:18
- web/js/studio/components/focusKeep.js (54): Keep focus across re-renders via data-fk || captureFocus:16; restoreFocus:38
- web/js/studio/components/form.js (343): Accessible form primitives || field:27; group:72; input:111; textarea:144 (mono Tab handler); select:200 (unknown value kept as '<v> (unknown)' option:213-217); checkbox:230; button:255; linkButton:281; spinner/emptyState/errorState
- web/js/studio/components/icons.js (121): Inline SVG icons || PATHS:10; icon:61; SCENE_TYPE_ICONS:95-107 (no clip); brandMark:113
- web/js/studio/components/jobProgress.js (440): Live job widget (SSE, polling fallback, stepper, log, cancel/retry/review link) || jobProgress:124; pollBackoff:97; stageText:114; PERSONAL_KEY_REJECTED:86
- web/js/studio/components/libraryPicker.js (676): Media library helpers and picker dialog (attach to lecture) || openLibraryPicker:397 (POST /api/library/{id}/attach:485); uploadToLibrary:360 (XHR with CSRF:308-350); libraryCard:229; validateLibraryFile:123
- web/js/studio/components/menu.js (115): ARIA menu button || menuButton:28
- web/js/studio/components/modal.js (339): Dialogs (focus trap, inert root, busy-safe) || openModal:68; closeAllModals:230; confirmDialog:239; promptDialog:264; alertDialog:305; choiceDialog:320
- web/js/studio/components/schemaForm.js (571): JSON-Schema form generator (Manim params) || createSchemaForm:64; InvalidJson:49; kind editors:210-549
- web/js/studio/components/shortcutsDialog.js (68): Keyboard shortcuts dialog || openShortcutsDialog:43
- web/js/studio/components/sortable.js (329): Keyboard/drag sortable list with grab state across re-renders || sortableList:54; grabState:40; resetSortableState:45
- web/js/studio/components/stage.js (80): Lesson stage chip and Next-step card || stageChip:17; nextStepCard:44 (stageAction option:35-36,58 added in B0 batch-4 fix)
- web/js/studio/components/tabs.js (106): WAI-ARIA tabs (lazy panels) || createTabs:21
- web/js/studio/components/toast.js (78): Toasts || initToasts:26; toast:39
- web/js/studio/components/videoPreview.js (89): MP4 preview dialog with one fresh-link retry || openVideoPreview:39; releaseVideo:17
- web/js/studio/lib/apiKeys.js (91): Key validation/scrubbing/status texts || KEY_PROVIDERS:14 = gemini/openai/anthropic (no elevenlabs/azure); validateApiKey:31; scrubSecret:49
- web/js/studio/lib/conflict.js (239): 3-way screenplay merge (scene-level) for 409 conflicts || diffScreenplay:56; mergeScreenplays:86-217 (top-level fields incl. `presentation` merged atomically:108-120; chapters membership by side:201-214; ends with sanitizeScreenplayRefs+syncChapterOrder)
- web/js/studio/lib/debug.js (32): ?debug technical-details switch || showTechnical:25
- web/js/studio/lib/draftStore.js (201): Per-user localStorage drafts and UI prefs || draftKey:48 `aadhi.studio.draft.u<user>.v<vid>`; saveDraft:60 (drops base when too big); loadDraft:88; clearAllDrafts:122; noteSignedInUser:154; loadPref/savePref:179-201 `aadhi.studio.pref.*`
- web/js/studio/lib/expr.js (40): Graph expression allow-list mirror || checkExpr:19
- web/js/studio/lib/jobStages.js (119): Job stage labels/stepper states || STAGES_BY_KIND:13-21 (render_video = screenshots/segments/mux/store only); STAGE_LABELS:23-37 (no preflight/capture/check); stageStates:74
- web/js/studio/lib/jsonSchema.js (361): Minimal JSON-schema analyze/default/validate || deref:59; analyze:90; defaultFor:148; validateValue:227
- web/js/studio/lib/lessonStage.js (188): Lesson stage words and next step || STAGES:19; STAGE_INFO:45-58 (no scope_review stage); workflowSteps:91; deriveStage:107; nextStepFor:135; projectStage:176
- web/js/studio/lib/manimTemplates.js (119): Manim template meta helpers || templatesOf:29; defaultManimTemplate:87; manimSpecProblem:103
- web/js/studio/lib/names.js (111): Distinct lecture titles || uniqueJoin:25; distinctTitles:62
- web/js/studio/lib/optionsForm.js (517): GenerationOptions form values <-> payload, meta helpers, file validation || OPTION_LIMITS:17; TTS_PROVIDERS:31 (edge/gemini/openai/elevenlabs/fake); FALLBACK_DEFAULTS:47-75; optionsFormDefaults:87 (non-booleans String()-ed); buildGenerationOptions:138-242 (ALWAYS emits target_minutes:157, include_quizzes/quiz_every_n_concepts:173-174, tts_rate:213-215; only known keys emitted); voicesFor:446; validateSourceFile:478; MEDIA_RULES:493
- web/js/studio/lib/password.js (144): Password hints mirroring server || passwordHints:86; blockingHints:57; generateTempPassword:134
- web/js/studio/lib/planEdit.js (341): Pure LecturePlan edits || findCycle:18; addPlannedScene:201; updatePlannedScene:284 (spreads, unknown fields kept); validatePlan:306
- web/js/studio/lib/richLite.js (116): Rich-lite tokenizer || tokenizeRichLite:15; stripRichLite:112
- web/js/studio/lib/screenplayEdit.js (1228): Pure immutable screenplay transforms (the editor's edit decision layer) || BOARD_TYPES:26; SCENE_TYPES:27-30 (no clip); BOARD_ITEM_KINDS:44-47 (no question/stat/step/quote/pair); TEXT_REQUIRED_KINDS:65-67; COMMON_SCENE_KEYS:89-92 (incl hidden,min_seconds,look,colour_is_content); BOARD_SCENE_KEYS:94 (board,sequence); BLANKABLE_KINDS:96 (example_step,question); applyKindDefaults:241; newScene:283; convertSceneShape:312-364; moveScene:432; insertScene:444; addScene:468; duplicateScene:484-526; deleteScene:534-543; changeSceneType:553; removeBeat:643; updateBeat:684; setBeatReveal/Fill/Highlights:767-812; deleteBoardItem:844; updateBoardItem:876 (id/kind/blank excluded); setBoardItemKind:894; setItemBlank:910; repairSceneRefs:1004-1078; sanitizeScreenplayRefs:1087-1112; setSceneChapter:1122; addFigure:1139; sidePanelOfKind:1154-1207
- web/js/studio/lib/validationIssues.js (254): Local save-blocking checks; 422 detail -> issues; sorting/counting || FORBIDDEN_TEX:33; localProblems:67-116 (no new-kind or Beat.answers checks); issuesFromValidationError:182; sortIssues:217; issuesByScene:244
- web/js/studio/lib/visibility.js (36): Page-hidden helpers for polling || pageHidden:12; onceVisible:22
- web/js/studio/views/common.js (111): Shared view helpers || EDITABLE_STATUSES:13; versionPrimaryLink:25 (source/plan review/editor); jobModal:74; uploadForm:107
- web/js/studio/views/newProject.js (444): New lecture page: source (Upload/Paste tabs), options, example import, job follow || cleanPasted:46; pastedProblem:93; mount:114; source createTabs:179-189 (source tabs, not style tabs); submit -> POST /api/projects {file, options, title?, review_source?}:270-324; example import:326-355; showProgress:392-429
- web/js/studio/views/optionsFields.js (321): GenerationOptions form UI from /api/meta || createOptionsForm:53-321 (sections Lecture details 233-245, AI engine 247-256, Language & audience incl Target length 257-267, Pedagogy, Media, Voice, Context 268-283, admin overrides 284-294); values():297; build():309
- web/js/studio/views/projects.js (361): Project list with stage chips, live job cards (2 SSE + 1 page poll), JSON import || matchActiveJobs:46; mount:67; card:227-286 (no style/format chip); import:306-325
- web/js/studio/views/projectDetail.js (1063): Project page: next step, versions, renders/downloads/QA, shares, jobs, sources, regenerate, delete || PAGE_ACTIONS:47; render:102; nextStepSection:172-196; renderVersion (render dialog: burn_captions, include_intro, allow_degraded):273-305; startRender:311; versionsSection:385-450; preflightNotice:619; qualityNotice:636; renderChecks (Render.options.qa/warnings):680; renderRow:742-806; regenerate:980-1035
- web/js/studio/views/planReview.js (479): Plan review editor (objectives, concept DAG, misconceptions, chapters/planned scenes) and approve || mount:27; source-pause redirect:47-51; chaptersSection:304; plannedSceneEditor:355-398 (SCENE_TYPES, side panel, Manim template, est_seconds); save POST /plan:411; approve POST /approve-plan:429-454
- web/js/studio/views/sourceReport.js (582): How Aadhi read the document; source review edits and approve-source || VERDICTS:25; readinessOf:64; draftFrom/draftBody:91-107; effectiveStatus:114; save PUT /source-review:522; approve POST /approve-source:545
- web/js/studio/views/visualReview.js (1082): Per-scene visual review (approve, new AI version, library, upload, remove, retry/paid retry) || REVIEW_LABELS:52; STATUS_LABELS:62; sourceLabel:112; needsAttention:173; summarize:197; pickerKind:223; confirmPaidRetry:267; mountVisualReview:303; visualAction POST /scenes/{id}/visual:770
- web/js/studio/views/library.js (695): Library page (search, filters, uploads, edit details, AI describe, remove) || aiDescribeEnabled:63; mount:80; editItem:301; uploadFiles:583
- web/js/studio/views/videos.js (331): All my videos, filters, polling, preview || FILTERS:39; videoFacts:66; mount:79; refresh window:161-190
- web/js/studio/views/apiKeys.js (312): Personal API keys page and admin server-keys section || describePersonal:42; describeServer:87; mount:137; serverKeysSection:230
- web/js/studio/views/admin.js (325): Users, budgets, server keys, media provider chains, spending || parseBudget:25; renderMediaProviders:58; mount:89 (no Dynamic/site-settings controls)
- web/js/studio/views/analytics.js (206): Learner analytics (funnel, quizzes, flags) || funnelChartInput:20; pct:37; mount:43
- web/js/studio/views/usage.js (160): Usage and budget || continuousDays:23; mount:35
- web/js/studio/views/login.js (118): Sign-in with 429 countdown || mount:15
- web/js/studio/views/changePassword.js (117): Change password (forced flow) || mount:15
- web/js/studio/views/editor/editor.js (2175): Screenplay editor orchestration: state, undo, save/conflict, lint, autosave, jobs, render dialog, revert, save-as-copy, shortcuts || MUTATING_KINDS:84; AUTOSAVE_MS:86; SHORTCUTS:96-138; shouldFollow:146; mount:163; state base/draft/undoStack:207-290; combinedIssues:465; commitDraft:664-688; editSceneById:727; undo/redo:760-796; splitSceneHere:970; moveSceneBefore:993; addPictureScene:1018; deleteScene:1046; autosave (local):1066; autosaveHold:1076; runLint POST /lint:1107-1146; save PUT /screenplay (base = sent at 1181):1157-1229; resolveConflict (mergeDrafts):1232-1284; adoptServerVersion:1291; refreshVersion (job result merge):1330-1377; build/regenerate/rebuild/generateAgain:1405-1500; renderVideo dialog (burn_captions, soft_subtitles, include_intro, allow_degraded):1582-1629; checkActiveJob/setLock:1664-1724; loadChanges:1745; revertScene/postRevert:1781-1912; saveAsCopy:1922-1986; onDocKey:2016-2042; leave guard:2063; offerRestore:2084-2129
- web/js/studio/views/editor/context.js (47): InspectorCtx JSDoc contract || EditOptions:14; InspectorCtx:21-45
- web/js/studio/views/editor/inspector.js (257): Scene inspector composition || renderInspector:50 (type select with lossy confirm:56-76, mascot select:113, chapter:115, intent/notes:91-100); per-type sections:128-186; beats/side panel:188-190; foldKey:224; changeLine:233 (no Look section, no colour_is_content toggle)
- web/js/studio/views/editor/inspectorFold.js (66): Foldable inspector sections || foldSection:21; revealFolded:54
- web/js/studio/views/editor/beatsEditor.js (248): Beat list editor (narration, spoken, pause, reveal/fill/highlights, visual cue, sources, split here) || beatsEditor:37; beatEditor:81-247; hasBlanks only example_step:156 (blank questions not offered); no speaker/answers/cuts/overlays controls
- web/js/studio/views/editor/boardEditor.js (405): Board items editor || boardEditor:33 (kind select from BOARD_ITEM_KINDS:48-52); itemEditor:83-243 (switch:132-221; new kinds fall to default text-only:219-220; blank checkbox only for example_step:190-201); variablesEditor:256; tableEditor:318
- web/js/studio/views/editor/sidePanelEditor.js (422): Side panel editor per kind || sidePanelEditor:39 (kind switch via sidePanelOfKind:46); chartForm:186; graphForm:242; modelForm:323; teaserForm:398
- web/js/studio/views/editor/manimEditor.js (188): Manim template/code editor || manimEditor:27 (debounced emit, flush/destroy)
- web/js/studio/views/editor/quizEditor.js (102): Quiz checkpoint editor || quizEditor:22
- web/js/studio/views/editor/mediaUpload.js (155): Override media upload / library pick || libraryKindOf:29; mediaUpload:41 (POST /api/uploads {file,purpose,project_id}:129)
- web/js/studio/views/editor/richPreview.js (170): Board item previews (player renderRich, TeX) || renderRichInto:68; boardItemPreview:105; updateBoardItemPreview:116 (new kinds -> default text only:167-168)
- web/js/studio/views/editor/sceneList.js (188): Scene list pane || estimateNarrationSeconds:24; estimateSceneSeconds:37; createSceneList:52 (total duration line:169-171)
- web/js/studio/views/editor/sceneTiming.js (108): Skip scene / minimum duration || parseHold:24; sceneTimingSection:36
- web/js/studio/views/editor/splitScene.js (252): Split a scene at a beat boundary (pure) || UNSPLITTABLE:24-28; splitProblem:38; canSplitType:53; splitScene:63-152 (part B shape:122-138, sequence copied:138); movedRef:161; moveAnswerRefs:177-214 (Beat.answers + presentation.hook); childId:222; moveCompanionRefs:234
- web/js/studio/views/editor/sceneMerge.js (146): Field-level merge of scenes changed on both sides || mergeDrafts:31; mergeScene:64 (type/chapter change -> conflict); pick:85; mergeList (beats/reveal_beats/board by id):99
- web/js/studio/views/editor/changes.js (158): Undo labels and GET /changes helpers || HISTORY_LIMIT=200:10; EDIT_LABELS:13-30; editLabel:37; changesBySceneId:53; FIELD_WORDS:64-100 (no look/sequence/colour_is_content words); changeBadge:122; restorePosition:143
- web/js/studio/views/editor/changesPanel.js (113): Changes-since-generated side panel || createChangesPanel:23
- web/js/studio/views/editor/issuesPanel.js (192): Issues panel with repair/regenerate/rebuild/review actions || PAID_ATTENTION_CODES:43; mediaActionsOf:50; createIssuesPanel:67
- web/js/studio/views/editor/quality.js (286): Issue areas, headline, lint repairs, pre-export list, board fit measurement || AREAS:28; areaOf:73; statusOf:94; attachRepairs:139; applyRepair:185; preExportList:221; measureBoard (.ap-board):245; fitIssue:261
- web/js/studio/views/editor/previewPane.js (371): Embedded player preview (built or draft timeline) || createPreviewPane:66; refresh (GET /timeline or POST /timeline/preview):222-297; new Player(box,{mode:'preview'}):254
- web/js/studio/views/editor/timelineStrip.js (608): Timeline strip (ruler, scenes, narration, visuals, captions lanes) || stripModel:127; fallbackModel:174; createTimelineStrip:219 (lanes:249-276; intro block:332)
- web/js/studio/views/editor/visualInfo.js (34): Inspector 'Visual at the last build' line || visualInfo:19