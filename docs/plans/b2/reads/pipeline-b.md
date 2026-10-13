# pipeline-b: aadhi/pipeline assets/audio/validate/lint/critic/repair/translate/companion/quality/orchestrator and every n-z module (plus the prompts they load)


## summary
INVENTORY of aadhi/pipeline (wc -l; [B] = this slice, read in full; [A] = part A). Python: __init__.py 4 [A], aio.py 21 [A], assets.py 1713 [B], audio.py 235 [B], base.py 376 [A], brief.py 663 [A], canonicalize.py 701 [A], checkpoint.py 124 [A], chunking.py 301 [A], codeblocks.py 285 [A], companion.py 364 [B], critic.py 339 [B], dbops.py 161 [A], docx_extract.py 275 [A], envelope.py 65 [A], fake_content.py 1885 [A], gen_models.py 473 [A], ingest.py 558 [A], integrations.py 328 [A], orchestrator.py 1213 [B], pdf_layout.py 412 [B], pdf_worker.py 206 [B], plan.py 479 [B, overlaps A], plan_rules.py 657 [B, overlaps A], plan_state.py 87 [B], presenter_lint.py 39 [B], preview.py 698 [B], prompting.py 97 [B], repair.py 204 [B], richlite.py 268 [B], scene_checks.py 168 [B], scene_context.py 313 [B], screenplay_ops.py 106 [B], script.py 482 [B, overlaps A], source_review.py 1441 [B], source_scope.py 2938 [B], sync_lint.py 49 [B], textnorm.py 97 [B], timing.py 55 [B], translate.py 336 [B], validate.py 955 [B]; quality/__init__.py 186, abbreviations.py 344, assist.py 147, code.py 125, extract.py 239, formulas.py 202, pacing.py 88, report.py 40, terms.py 227, text.py 244, titles.py 82 (all [B]). Prompts: brief.md 111, plan.md 153, scene_ai_video.md 28, scene_board.md 64, scene_chapter.md 16, scene_interactive.md 23, scene_quiz.md 40, scene_simulation.md 44, style.md 109 [A]; critic.md 58, repair.md 33, translate.md 21, practice.md 23, quality_terms.md 18 [B, read]; library_describe.md 23 (used by aadhi/library.py:971, not by the pipeline). There is no events*.py, review*.py other than source_review.py, lint*.py, or any Dynamic module (scope.py, casting.py, history.py, direct.py, music.py, fit.py, dynamic_lint.py) in aadhi/pipeline.

What the slice does. orchestrator.py holds the four job handlers: generate_lecture, regenerate_scene, build_assets and translate. generate_lecture runs ingest, then the concept brief, then the plan (with optional source-review or plan-review pauses), then script, then validate (lint + critic + repair), then the companion sheet. It then writes the screenplay with a compare-and-set on revision and calls _build_and_finish, which runs assets.build_assets_detailed and then integrations.build_timeline_fn(). Every LLM stage is checkpointed per job. Each version write is a lease-fenced compare-and-set.

assets.py builds the AssetManifest:
- Narration: TTS per beat, content-addressed. audio.py trims silence and joins the beats into one 24 kHz mono MP3 per scene with sample-exact offsets and word timings.
- Visuals: images and AI video go through provider chains with fallback, health cooldowns, paid-job checkpointing (Veo/Omni) and the library reuse/save path. Also Manim renders, figures and GIFs.
- Staleness: driven by scene_hash. B0 already excludes `look` and beat `overlays` from that hash.

Checks and repair:
- validate.lint is the single deterministic lint entry point. It covers board/beat/refs/manim/panel checks, quizzes, coverage, duration, source-packaging leaks, duplicate intros, sync_lint, presenter_lint and the quality/ family, and demotes hidden-scene findings to notes.
- critic.py adds grounding checks plus a per-chapter LLM judge.
- repair.py rewrites only failing scenes, at most 2 rounds. carry_over keeps teacher choices, including the v3 `look` and the v3 narrative role.
- translate.py translates path-addressed fields with ⟦n⟧ masking.
- companion.py derives the study sheet and has the LLM write the practice problems.

The remaining files (source_scope, source_review, pdf_*, textnorm, richlite, plan*, script, scene_*, prompting, preview, timing) are ingest, planning and scripting support.

Nothing in this slice generates or reads a Dynamic Presentation. The pipeline still produces only classic lectures. The Dynamic engine (B1) is reached only when a stored screenplay already carries `presentation`, and the timeline builder and motion renderer live outside this slice. For Dynamic, this slice currently contributes only B0 inert-schema support:
- the hash exclusions;
- carry_over of look and the v3 intent;
- clip scenes kept out of plans, repair and regenerate;
- translation of BoardItem.unit, BoardItem.date and ClipCaption.text.

## contracts
Pipeline entry points (job registry, contract in base.py):
- build_assets(ctx, screenplay, options, *, previous, scene_ids) -> AssetManifest (assets.py:1704). build_assets_detailed returns AssetBuildResult{manifest, issues, built, reused, asset_keys} (assets.py:311, :1691).
- Job handlers: @job_handler names 'generate_lecture' (orchestrator.py:735), 'regenerate_scene' (:979), 'build_assets' (:1070), 'translate' (:1127).
- Payload keys: base_revision, options, source_document_id, resume_state{stage, version_id, base_revision, source_document_id}, review_source, scene_id, instructions, scene_ids, source_version_id, target_language, translate_board, tts_voice, confirm_paid_scene_ids (assets.py:152).
- Other entry points: repair(ctx, sp, issues, ingest, options, *, brief) (repair.py:200); translate_screenplay(..., translate_board, progress, options) -> TranslateResult (translate.py:238); build_sheet (companion.py:213); critique(ctx, sp, ingest, options, *, scene_ids, brief) (critic.py:312).
- validate.lint(screenplay, options, *, chunk_ids, ingest, quality=quality.COMPUTE) -> list[Issue] (validate.py:912). quality=None means the quality analysis failed. quality.COMPUTE is the sentinel the batch-4 regression tests cover.

Hashing and staleness (frozen by B0, design §6.7):
- scene_hash = sha256(canonical_json({scene: dump(exclude=_HASH_EXCLUDE), lexicon?: relevant entries, voice?}))[:32] (assets.py:215-241).
- The exclusion set is notes, intent, hidden, min_seconds, look, and beats[]/reveal_beats[].overlays. cuts, speaker, answers and voice_override_asset_key stay in the hash.
- stale_scene_ids never lists hidden scenes (assets.py:244-253).
- A test pins this: tests/schemas/test_v3_schema.py:428.

Content keys (changing any input re-pays for media):
- TTS clip: compute_key('tts', {text, voice, provider, rate, model, language}) (assets.py:528-532).
- scene_audio: compute_key('scene_audio', {clips, pauses, phases, narration, countdown, audio: AUDIO_VERSION='2'}) (assets.py:586-589).
- Image: _image_keys(prompt + settings.image_style_suffix, provider, model per _key_models, aspect, variant>0 only) (assets.py:680-709).
- Video: _video_keys(provider, model, aspect '16:9', veo duration, prompt, variant) (assets.py:936-954).
- Earlier default models are looked up only (_EARLIER_DEFAULT_MODELS :146).

Manifest assembly:
- AssetManifest{language, audio, media, scene_hashes (complete scenes only), stale_scenes} (assets.py:1606-1612).
- Asset refs go through dbops.ensure_asset_refs, which is assets_auth.record_asset_refs (dbops.py:122-127; assets.py:1613-1619).

Issue model (base.py:358):
- Fields: code 'area.problem', severity, message, scene_id, beat_id, source in {lint, critic, manim, assets, system}, fixable, data{category}.
- scenes_needing_repair picks only fixable errors and fixable critic warnings (validate.py:940-955).
- AUTHOR_CODES are never passed to a rewrite (quality/__init__.py:73; repair.py:127).
- ASSET_SOURCES=('assets','manim'): asset issues of reused scenes are kept across builds (orchestrator.py:94, :168-174).
- Hidden-scene issues are stored at their real severity and served or counted as info through hidden_as_notes (validate.py:868-882; orchestrator.py:152-155).

generation_meta keys written here:
- options, models{brief, plan, script, critic, provider}, prompt_versions, stage_seconds, cost_usd, fallback_scenes, written_by{job_id, kind, revision}.
- reused_stages, repaired_scenes, plan_notes, brief, plan, lexicon_seed (only while awaiting review), review_stage, source_overrides.
- scene_history (5 per scene), regenerations (20), last_build, resumed_builds (10), generated_snapshot_key.
- source_document_id, ingest_key, ingest{...}, source_version_id, translated_from, translate_board, error.

Status flow: generating -> (awaiting_review) -> building -> ready | failed. In-place jobs restore 'ready' when the version was built before. Stage progress budgets come from PIPELINE_PROGRESS (base.py:56-64).

Plan review: the AwaitingReview state stage is 'plan_review' (orchestrator.py:866-868). Source review uses 'source_review' (source_review.py:66) with generation_meta['review_stage']='source' (:64-65). The design's scope_review pause must reuse this mechanism (§6.4).

Translation: field list in translate.py:41-43 and :66-141, mirroring schemas/screenplay.TRANSLATABLE_FIELDS (screenplay.py:66-75). Protected fields (icon_hint, value, credit, attribution) are never sent. Placeholder syntax is ⟦n⟧ (richlite.py:221); replace_outside_markup refuses any text containing ⟦ or ⟧ (quality/text.py:222).

Prompt versions: every prompt file starts with '<!-- PROMPT_VERSION: n -->' (prompting.py:22). prompt_versions() with no names covers every file in prompts/ (prompting.py:44-48) and feeds the stage checkpoint digest (orchestrator.py:541).

Settings and constants read here:
- tts_max_parallel, tts_rate default '+0%' (config.py:175), image_style_suffix (config.py:185), manim_background '#1A0B2E' (config.py:283), manim_quality, manim_sandbox, video_provider, image_provider, *_fallback_providers, auto_video_backup, max_ai_videos_per_lecture, veo_duration_seconds, ai_video_timeout_seconds, library_auto_save_generated, quality_ai_terminology, llm_max_parallel, job_max_attempts, ffmpeg_path.
- dynamic_features defaults to [] (config.py:366): cuts, overlays, voice_override and presentation writes are gated, so the latent gaps below are unreachable in production today.

Timing constants: INTER_BEAT_GAP_SECONDS=0.15 (schemas/manifest.py:28); words per second per language (timing.py:18); 24 kHz mono 64k MP3 narration (audio.py:29, :34).

## conventions
Module docstrings:
- Each one states the behaviour contract exhaustively (codes, edge cases, what is never done). New modules are expected to do the same, and the docstrings name the design sections (e.g. assets.py:229-233, repair.py:63-66).
- Comments are plain English, explaining why a choice was made.

Job-level exceptions pass through every degrade path. _PASSTHROUGH = (JobCancelled, BudgetExceeded, RateLimited, asyncio.CancelledError) (assets.py:139, script.py:71). A refused personal API key always propagates (credentials.personal_key_rejection; assets.py:393-395, script.py:316, translate.py:279). Everything else degrades to an Issue, never an exception: assets.py:1495-1513, script.py:315-321, critic.py:301-308, companion.py:203-209.

Errors shown to users are always redacted and truncated: settings.redact(str(exc))[:160|200|300].

Blocking work: blocking I/O and DB run in asyncio.to_thread with ctx.session(). Concurrency uses gather_all plus semaphores sized from settings, and every provider call is wrapped in integrations.limit('llm'|'tts'|'image'|'video').

Providers are reached only through the aadhi.pipeline.integrations seam (get_llm, get_tts, get_image, get_video, get_gif, speech_text, default_voice, render_manim_fn, build_timeline_fn), so fakes and tests can swap them. LLM calls use llm.generate_json(model, system=system_prompt(...), prompt=join_sections(json_section(...)...), schema=Gen*, temperature, on_usage=ctx.record_usage, validate=..., validation_retries=N). Validators return every problem (soft and hard) on the first call and only hard problems after that (plan.py:288-305, script.py:207-247, companion.py:138-172, translate.py:158-198).

Content addressing: assets are content-addressed (compute_key, storage.assets.get_or_create(key, kind, produce, created_by, inflight_scope, guarded)). Any new input must be added to a key only when non-default, so classic keys and hashes stay byte-identical (the B0 'exclude_if at default' rule; assets.py:946, :948, :702).

Issue codes are 'area.what' (validated, base.py:371-376). Lint issues are created with _issue(...) and source='lint'. Severity drives repair: only fixable errors and fixable critic warnings trigger a paid rewrite.

Determinism and bounds: everything is a pure function of inputs and stored seeds. Regexes are bounded, with no nested unbounded repetition ('p18-adversarial-bounds', quality/text.py:3-7, richlite.py:26-32), and texts are cut before scanning.

Version writes are compare-and-set on revision via _cas, lease-fenced (orchestrator.py:130-145). A stale job stops as Conflict and never overwrites. Paid generations re-check revision through _ProductionGuard (orchestrator.py:485-513).

Tests are offline with fakes (tests/pipeline/*; preview.py forces fake providers at preview.py:85-102). B0 v3 coverage of this slice lives in:
- tests/core/test_transform_survival.py:430 (carry_over), :466 (translate);
- tests/schemas/test_v3_schema.py:428 (scene_hash).

## dynamic_status
What exists in this slice for Dynamic, all from B0 and all inert:
- scene_hash excludes look and beat overlays, so a look or overlay change never triggers a build (assets.py:215-241). Tested at tests/schemas/test_v3_schema.py:428.
- repair.carry_over keeps `look` and the v3 narrative role and visual_intent (repair.py:73-79).
- Repair and regenerate never rewrite clip scenes (repair.py:167; orchestrator.py:1008-1009).
- plan_from_screenplay leaves clip scenes out (screenplay_ops.py:50).
- Translation sends BoardItem.unit, BoardItem.date and ClipCaption.text (translate.py:42, :105-106) and never sends the protected fields.
- AssetBuildResult goes through record_asset_refs (dbops.py:122-127).
- The Gemini Omni video provider and model-refresh key lookups were added in the WIP commit (assets.py:142-150, :683-709, :936-1081); this is not Dynamic-specific.

What does not exist. Nothing from B2, B3 or B4 is present, and the pipeline cannot produce a Dynamic lecture: no Presentation is created anywhere (grep finds no 'presentation' in aadhi/pipeline).

Divergences from docs/DYNAMIC_VIDEO_DESIGN.md:
1. §6.1 flow: orchestrator._generate (orchestrator.py:746-971) has none of scope.py, casting.py, rotation, direct.py, music.py or fit.py; no such files exist. GenerationOptions (base.py:73-114) lacks video_style, video_format, length_profile, reel_profile, destination, look, voice, music, aadhi_recap, captions_burn, sfx, series_part and focus_concepts, yet api/generation.py:270-282 already reads opts.voice and opts.music through getattr.
2. §6.1 and §5.4 assets:
   - One VoiceConfig for the whole lecture (assets.py:292-308, :355) and no per-speaker persona. Rate comes from options.tts_rate or settings.tts_rate, not the stored calibration.
   - No speaker gains in the scene_audio key or in audio.build_scene_audio (assets.py:586-589; audio.py:187).
   - BeatAudio.persona, words_exact, SceneAudio.lufs and speaker_gains are never set.
   - Images are always 16:9 or 4:3 (assets.py:135-136) with settings.image_style_suffix (assets.py:680-681), not the stored AssetStyle; colour_is_content is ignored.
   - Manim uses settings.manim_background (assets.py:914) and ManimRenderRequest has no format or colour map (manim/base.py:28-40).
3. §6.9: there is no dynamic_lint.py. validate.py uses the classic global thresholds (:74-107) and still runs presenter_lint, which the design says to skip (:830). _lint_duration compares against options.target_minutes (:404-413), but Dynamic requires the default target_minutes, so a Dynamic lecture would get a false lecture.duration_mismatch. Issue has no 'audience' field (base.py:358-369).
4. §6.9 last paragraph: critic.md (v4) has no Dynamic checks. critic.scene_text (critic.py:73-85) omits the v3 item fields value, unit and date and FigurePin labels, so a critic claim quoting a stat value is dropped as 'claim not found' (critic.py:196-198).
5. §6.11 translation: no voice re-cast (orchestrator.py:1157-1161 only takes payload tts_voice), no Anek font switch, no dropping of narration cuts and no ⟦o:id⟧ overlay-anchor markers. Cuts and overlays are copied verbatim (translate.py:268-287), and the transform-survival test asserts exactly that. translate.md v1 still says 'The narration is spoken by Aadhi', which conflicts with the §6.5 persona rule that the narrator never calls itself Aadhi.
6. §6.5: scene_context hard-codes target_words = est_seconds*2.3 (scene_context.py:253). plan.build_plan_prompt sends only target_minutes (plan.py:243-259). plan_rules checks ±25% of target_minutes (plan_rules.py:227-230). There are no format budgets, hook, chapters.builds_on or check_order.
7. Line-number drift in the design's citations: it cites 'pipeline/assets.py:202–220' for the hash exclusion, which is now assets.py:215-241. It cites orchestrator.py:854–867 for the plan-review pause, which is still about right (854-868). It cites audio.py:31 KEEP_PAD_SECONDS, which is still correct.

Open B0 items owned by this slice:
- The ambiguous Omni/Veo create record (assets.py:1231-1237: an exception other than PaidSubmissionUncertain marks the record FAILED or ABANDONED).
- record_asset_refs for music, which is not produced yet.

## gaps_todos
There are no TODO, FIXME or XXX markers, no NotImplementedError and no stubs anywhere in my slice (grep found none). The gaps are feature-level:

1. No music handling.
   - AssetManifest.music (schemas/manifest.py:137) is never read or carried over by the builder. assets.py:1606-1612 builds a fresh manifest from audio, media, hashes and stale only.
   - _keys_of (assets.py:326-333) ignores music keys, so they would get no AssetRef and no existence check.
2. ClipScene in assets. scene_media (assets.py:1450-1513) has no step for the clip itself (ClipRange.asset_key), so media.main stays None. Its voice-over beats are synthesised with the lecture voice. This is B6 work.
3. Beat.voice_override_asset_key, cuts and speaker are not used by assets.
   - tts_clip and scene_audio ignore them (assets.py:558-625), although they are in scene_hash (assets.py:215-233).
   - Narration upload and cut splicing are B5/B6; persona per speaker is B3.
4. v3 board item kinds (question, stat, step, quote, pair) have no per-kind limits; ITEM_LIMITS.get(k, 200) falls back to 200 (validate.py:86-101, :139).
   - The value and unit fields are not counted toward the item limit.
   - The leak lint's visible_texts omits value, unit, date and pins labels (validate.py:564-586).
   - companion and quality ignore the new kinds (companion.py:51-104; quality/extract.py:128).
5. FigurePin.label (screenplay.py:314) is learner-visible text, but it is not in TRANSLATABLE_FIELDS (screenplay.py:66-75) or in translate.scene_fields (translate.py:77-86), so it stays untranslated.
6. Lexicon spoken forms are not translated. TRANSLATABLE_FIELDS lists 'lexicon[].spoken' (screenplay.py:74), but translate.screenplay_fields (translate.py:110-141) never sends it.
7. preview.py's forced environment covers only today's providers: no MUSIC or AZURE keys and no music provider override (preview.py:78-102). Harmless today because preview never runs assets.
8. The pipeline has no Dynamic fakes yet. The B3 fakes (calibration, Lyria-shaped music, Azure word boundaries) and the B4 writer fakes are outside this slice (fake_content.py, part A).
9. Missing tests for Dynamic behaviour, which is expected since the features are missing:
   - persona per beat;
   - speaker gains changing the scene-audio key but not beat keys;
   - music carry-over in a rebuild;
   - per-format image aspect;
   - Dynamic lint;
   - translation dropping cuts and re-anchoring overlays.

## bugs_or_risks
1. LATENT DATA LOSS (B3): music beds are dropped on every assets build. assets.py:1606-1612 constructs AssetManifest(language, audio, media, scene_hashes, stale_scenes) and never copies previous.music. Once pipeline/music.py stores MusicAsset entries, any later build_assets job (an edit, a scene rebuild, regenerate_scene via _build_and_finish at orchestrator.py:672, or a translation) writes a manifest without them. The Presentation.music.asset_key would then point at an asset the manifest no longer lists. _keys_of (assets.py:326) also skips music, so music keys get no record_asset_refs or existence check.

2. LATENT SILENT NO-OP (gated by DYNAMIC_FEATURES, which is empty by default per config.py:366):
   - Editing Beat.cuts or voice_override_asset_key changes scene_hash (assets.py:215-233 keeps them in the hash on purpose). The scene is rebuilt, but the TTS clip key (assets.py:528-532) and the scene_audio key (:586-589) do not include them, so the build reuses byte-identical audio and records the new hash.
   - The manifest then claims the scene is up to date with the cut or the teacher's recording, while neither was applied.
   - The same happens for Beat.speaker: 'student' and 'aadhi' beats are voiced with the narrator voice (single VoiceConfig, assets.py:355).

3. POSSIBLE DOUBLE BILLING (open B0 item). In _submit_video (assets.py:1208-1237), any exception other than PaidSubmissionUncertain marks the operation record FAILED, or ABANDONED if it was SUBMITTED. A create request that timed out without a confirmed answer, raised as a generic error, is therefore resubmitted by the next build. The design's 'Open after B0' list records this.

4. TRANSLATION CORRUPTS EDIT ANCHORS (gated, B5/B6). translate_screenplay copies the whole scene JSON and replaces only the text paths (translate.py:259, :285-287, :327-336). Beat.cuts (word ranges) and Overlay.at_word/at_text (screenplay.py:442-443) survive verbatim against translated narration, so they would cut or anchor at the wrong words. §6.11 requires dropping cuts and carrying ⟦o:id⟧ markers; tests/core/test_transform_survival.py:466 currently asserts verbatim survival.

5. FALSE LINT ON DYNAMIC LECTURES (B4 blocker). Once a Dynamic lecture exists:
   - _lint_duration (validate.py:404-413) compares against options.target_minutes, which §6.3 forces to its default of 15, so a 3-minute reel always gets lecture.duration_mismatch.
   - plan_rules.plan_problems (plan_rules.py:227-230) pushes the planner toward 15 minutes the same way.
   - presenter_lint (validate.py:830) and the global MAX_BOARD_ITEMS=6 and BEAT_MAX_WORDS=60 (validate.py:74-75, reused by script.py:69-70) apply instead of the per-format values (9:16 allows 4 items and 20 words).

6. CRITIC AND LEAK BLIND SPOTS FOR v3 ITEMS. critic.scene_text (critic.py:73-85) and validate.visible_texts (validate.py:564-586) do not read BoardItem.value, unit, date or FigurePin.label.
   - A critic finding whose claim quotes a stat value is discarded as 'claim not found' (critic.py:196-198). That undermines the §6.9 'no claims beyond the source in stat' check.
   - A course code or date leaked into stat.unit or step.date escapes content.admin_leak.

7. ISSUE SOURCE DROPPED ON REBUILD (B3 risk). Rebuilds keep only issues whose source is in ASSET_SOURCES=('assets','manim') (orchestrator.py:94, :168-174, :677), and Issue.source is a closed Literal (base.py:364). New music or casting issues must use 'assets' or extend both, or they will be lost or duplicated across rebuilds.

8. ADDING ANY PROMPT FILE CHANGES EVERY LECTURE'S DIGEST. prompt_versions() with no arguments globs every prompts/*.md (prompting.py:44-48) and feeds _stage_inputs (orchestrator.py:541) and generation_meta. Adding dynamic_plan.md or dynamic_scene.md therefore changes stored prompt_versions for classic lectures too, and a job retried across that deploy cannot reuse its stage checkpoints (one-off LLM cost). Check eval baselines that compare prompt_versions.

9. LATENT HIDDEN-SCENE REPAIR. repair_detailed passes critic issues (`other`) to scenes_needing_repair without hidden_as_notes (repair.py:158, :168), and critique reviews hidden scenes (critic.py:222-235). This is unreachable today, because repair_detailed runs only on freshly generated screenplays (orchestrator.py:908), where no scene is hidden. A future 'fix issues' entry point would pay to rewrite hidden scenes.

10. MINOR: _video_info labels every provider's clip source='veo', Omni included (assets.py:1092-1094). _generated_media relies on that label (assets.py:1637). Provenance is still correct in Asset.meta.

## b2_b3_touchpoints
B2 (visual system: direct.py, events.py, kinetic.py, themes, textfit, zones):

1. Where direct.py runs. In orchestrator._generate it should run after the companion sheet (orchestrator.py:919-931) and before the screenplay is dumped and written (document = sp.model_dump at :953, CAS at :955-960), so looks are stored with the generated snapshot (_record_generated_snapshot :954). It must also run in _regenerate after replace_scenes (:1027-1034): re-fit only when the look is 'auto' or the template no longer fits, since carry_over already keeps the look (repair.py:73-74). Translation (orchestrator.py:1183-1206) keeps looks through model_dump.
2. Looks never trigger asset builds: look is in _HASH_EXCLUDE (assets.py:215-218). Keep any new direct.py output under SceneLook or excluded fields. Adding a hashed field forces rebuilds.
3. colour_is_content and AssetStyle must gate:
   - the image style suffix in _full_image_prompt (assets.py:680-681);
   - the image keys (_image_keys :692-709; add inputs only when non-default, so classic keys stay unchanged);
   - the Manim background and colour map (assets.py:911-916; ManimRenderRequest manim/base.py:28-40 needs fields);
   - the per-format aspect (PANEL_IMAGE_ASPECT/SCENE_IMAGE_ASPECT assets.py:135-136, used at :1288, :1290, :1426, :1436, :1437, :945, :1113, :1215).
4. textfit and dynamic_lint: append Dynamic findings in validate.lint (validate.py:912-937), with per-format thresholds replacing the constants at :74-107. Skip lint_mascot_layout (:830) and _lint_duration (:404-413) for Dynamic. Repair selection follows Issue.fixable (validate.py:940-955). Add 'audience' to Issue (base.py:358) for the §6.9 teacher/system split. script.py:69-70 reuses validate.MAX_BOARD_ITEMS/BEAT_MAX_WORDS inside the writer loop, so per-format limits must reach family_problems (script.py:159-184).
5. kinetic.py and events.py consume audio word timings: BeatAudio.words and words_estimated from audio.map_words/estimate_words (audio.py:116-163, :213-222). words_exact should be set where the provider word count equals the caption tokens (audio.py:141-142), with no `spoken` override (ClipInput needs that flag, audio.py:166-176).
6. Critic and leak lint must read v3 item fields: critic.scene_text (critic.py:73-85), render_scene_for_review (:88-119, add speaker and answers), validate.visible_texts (:564-586). The critic.md Dynamic checks need a PROMPT_VERSION bump.

B3 (audio: personas, casting, history, calibration, music, mix, live-player audio):

1. casting.py and history.py run in orchestrator._generate before planning (§6.1). The plan-stage inputs digest (orchestrator.py:532-543) must include the chosen Presentation so retries do not re-roll it. Regenerate and translate must inherit or re-cast from the stored Presentation, not options (orchestrator.py:1011, :1155-1161).
2. Per-speaker TTS:
   - Replace the single VoiceConfig (assets.py:292-308, :354-355) with a per-beat persona from Presentation.voices[beat.speaker] plus the stored VoiceCalibration.control. Classic stays on voice_config.
   - The clip key (assets.py:528-532) must include the persona's provider, voice, model and rate exactly as sent. Keep classic keys unchanged by deriving the same dict for classic.
   - tts_clip (:558-573) needs the voice per call.
   - _reusable (:534-555) compares a single (provider, voice, language) per scene and must compare per beat.
   - SceneAudio.provider and voice (:578) remain scene-level, with BeatAudio.persona per beat.
3. Speaker gains and loudness:
   - Measure per-speaker loudness after all beats exist (the build has the PCM in audio.build_scene_audio, audio.py:194-198).
   - Apply static gains when building scene_audio (audio.py:187-235: add a gains parameter).
   - Add the gains to the scene_audio key at assets.py:586-589, only when present, so classic keys stay the same.
   - Store SceneAudio.lufs and speaker_gains (assets.py:618-625).
   - Bump AUDIO_VERSION only if classic output changes, which it must not.
   - The 10 s window over 185 wpm re-synthesis also belongs after scene_audio, using BeatAudio.speech_duration and words.
4. Music:
   - pipeline/music.py runs in the asset stage after narration (inside or after _Builder.run, assets.py:1599-1622).
   - Carry previous.music into the new manifest (fix at :1606-1612).
   - Add music keys to _keys_of (:326-333) or to the refs at :1613-1619, so record_asset_refs covers kind 'music' (B0 open item).
   - Issues: use source 'assets' (ASSET_SOURCES, orchestrator.py:94).
   - Budget pre-checks use integrations.estimate_cost and ctx.ensure_budget, like _generate_video (assets.py:1045-1052).
   - Personal-key scoping uses _key_scope and _health_scope (:397-403).
5. Rate control: Dynamic must ignore options.tts_rate (assets.py:307), because R-36 calibration supplies the control.
6. Translation must re-cast voices for the target language (§5.3) where the translate job builds options (orchestrator.py:1155-1163) before _build_and_finish (previous=None at :1211).
7. Preview and offline safety: add new provider key variables (Azure, ElevenLabs music) and fake music and TTS provider overrides to preview.forced_environment and API_KEY_VARS (preview.py:78-102). B3 fakes for calibration and Lyria belong in fake_content.py (part A) and providers/*/fake.py.

## files
- aadhi/pipeline/assets.py (1713): Stage 6: build the AssetManifest (TTS per beat, scene narration, images/AI video provider chains with fallback and paid-job checkpoints, Manim, figures, GIFs, library reuse/save); incremental via scene_hash; never mutates the screenplay. || PANEL_IMAGE_ASPECT='4:3' :135, SCENE_IMAGE_ASPECT='16:9' :136 (hard-coded; no portrait), SCENE_PARALLEL=4 :138, _PASSTHROUGH :139, _KNOWN_MEDIA_PROVIDERS :142, _EARLIER_DEFAULT_MODELS :146, CONFIRM_PAID_KEY :152, OVERRIDE_MISSING_WARNINGS :154, NEW_VERSION_UNAVAILABLE :160, _PERMANENT :190; relevant_lexicon :207; _HASH_EXCLUDE (notes,intent,hidden,min_seconds,look,beats/reveal_beats[].overlays) :215; scene_hash :221; stale_scene_ids :244; carry_visual_variant :256; VoiceConfig/voice_config (one voice for the whole lecture; rate=options.tts_rate or settings.tts_rate) :292/:301; AssetBuildResult :311; _keys_of (audio+media keys only, no music) :326; _Builder :341 (_tts_provider :373, _media_providers :406, _ordered :451, clip_key :528, _reusable :534, tts_clip :558, scene_audio :575 with key at :586-589, loudness_envelope :627, image :776, _produce_image :854, manim :890 (background=settings.manim_background :914), _video_keys :936, _generate_video :994, _collecting :1083, _produce_video :1096, _checkpointed_video :1150, _submit_video :1208, ai_video :1239, _plan_library/library_image :1335/:1376, side_panel :1404, scene_media :1450, build_scene :1515, run :1533 (manifest built :1606-1612, asset refs :1613-1619)); _backup_provider :1681; build_assets_detailed :1691; build_assets :1704
- aadhi/pipeline/audio.py (235): Scene narration: decode each beat clip to 24 kHz mono PCM via ffmpeg, RMS silence-trim, join with INTER_BEAT_GAP + pause_after (+ quiz countdown), encode to MP3 with a LAME/Xing header, and give exact beat offsets and word timings (provider timings mapped, else estimated). || SAMPLE_RATE=24000 :29, KEEP_PAD_SECONDS=0.03 :31, MP3_BITRATE='64k' :34, AUDIO_VERSION='2' :35 (part of the scene_audio key), AudioError :41, run_ffmpeg :45, decode_pcm :63, encode_mp3 :75, measure_silence :95, estimate_words :116, map_words (exact when provider word count == tokens) :130, ClipInput :166, SceneAudioResult :179, build_scene_audio :187
- aadhi/pipeline/critic.py (339): Critic: deterministic grounding checks (quotes found in the source, teaching scenes cite a chunk), the optional AI terminology assistant, then an LLM judge per chapter (prompts/critic.md v4); findings are verified against the scene and source text before they become Issues. || CRITIC_PROMPTS :39, _CODE_BY_CATEGORY :41, FLOW_CODES :50, critic_model :64, scene_text (learner-visible text used for quote checks; omits v3 value/unit/date) :73, render_scene_for_review :88, quoted_passages :126, deterministic_issues :157, finding_code :177, verify_findings :189, chapter_groups :222, lecture_outline :238, build_critic_prompt :259, critique_chapter :286, critique :312
- aadhi/pipeline/companion.py (364): Companion study sheet: formulas, definitions and misconceptions derived from board items; practice problems written by the LLM (prompts/practice.md); rendered as escaped Markdown and printable HTML. || derive_formulas :51, derive_definitions :74, derive_misconceptions :92, practice_prompt :112, practice_validator :138, to_practice :175, generate_practice :188, build_sheet :213, render_markdown :243, render_html :308
- aadhi/pipeline/repair.py (204): Rewrite only the scenes with fixable errors or critic warnings (at most 2 rounds), keeping the teacher's choices; AUTHOR_CODES are never handed to a rewrite. || MAX_ROUNDS=2 :37, RepairResult :41, issue_lines :48, carry_over (chapter_id, hidden, min_seconds, look, v3 narrative_role/visual_intent, mascot_position, overrides, notes, panel override, variant) :52, rewrite_scene :95, repair_detailed (writable = PLANNABLE_SCENE_TYPES only) :143, repair :200
- aadhi/pipeline/translate.py (336): Translate a screenplay into a new version: path-addressed fields per scene, ⟦n⟧ masking of maths, code and keep-in-English terms, script-language check, scene-level fallback on failure. || BATCH_FIELDS=120 :39, _BOARD_ITEM_FIELDS (+unit,date v3) :42, scene_fields (+captions[].text v3) :66, screenplay_fields :110, set_path :147, make_validator :158, translate_model :201, translate_prompt :207, _translate_items :220, translate_screenplay :238, _apply :327
- aadhi/pipeline/validate.py (955): Deterministic lint (pure, no I/O): board/beats/refs/manim/panels/media, quizzes, coverage, duration, source-packaging and header leaks, duplicate intros, sync and presenter lint, the quality family; hidden-scene handling; selection of scenes to repair. || MAX_BOARD_ITEMS=6 :74, BEAT_MAX_WORDS=60 :75, SCENE_MAX_SECONDS :78-79, ITEM_LIMITS (no v3 kinds; default 200) :86, CODE_MAX_LINES/CODE_MAX_LINE_CHARS/TABLE_MAX_* :103-107, _item_length_problem :121, _lint_board :150, _lint_beats :191, _lint_refs :211, _lint_manim :233, _lint_panel :275, _lint_media_scene :300, _lint_quizzes :322, _lint_coverage :369, lecture_estimate :398, _lint_duration (target_minutes) :404, PACKAGING_PATTERNS :434, content_patterns :503, packaging_phrases :520, subject_nouns_in :548, visible_texts :564, header_values :589, header_needles :625, title_admin :680, lint_source_leaks :710, lint_intros :781, lint_scene :818 (presenter lint :830), hidden_ids :858, hidden_as_notes :868, lint_hidden :885, lint :912, scenes_needing_repair :940
- aadhi/pipeline/orchestrator.py (1213): Job handlers generate_lecture, regenerate_scene, build_assets and translate: lease-fenced compare-and-set version writes, status transitions, LLM stage checkpoints, the still-wanted guard on paid media, resume after a retry. || ASSET_SOURCES=('assets','manim') :94, Conflict :103, VersionChanged :107, _cas :130, _issues_values :152, merge_asset_issues :168, StageClock :177, version_options :204, _timeline :215, _final_values :232, _mark_failed :250, _guard :287, _version_brief :338, _load_ingest :364, _lint :396, teacher_meta :404, _pause_for_source_review :441, _ProductionGuard :485, _build_assets_guarded :516, _stage_inputs (digest incl. prompt_versions()) :532, WRITER_KEY/writer_mark/written_by_this_job :626/:630/:635, _build_and_finish :653, _resume_build :687, generate_lecture :735, _generate :746 (plan-review pause :854-868; script :874-896; validate :898-917; companion :919-931; screenplay write :933-960; assets :962-971), regenerate_scene/_regenerate :979/:991 (clip scenes refused :1008), build_assets_job/_build :1070/:1081, translate_job/_translate :1127/:1134
- aadhi/pipeline/presenter_lint.py (39): layout.mascot_crowds_board: a centred Aadhi on a board with code, a table or more than 4 items. || CENTER_MAX_ITEMS=4 :23, lint_mascot_layout :27
- aadhi/pipeline/sync_lint.py (49): formula.variable_beat_invalid for FormulaVariable.beat_id anchors. || lint_sync :23
- aadhi/pipeline/timing.py (55): Duration estimates before TTS exists, from words per second per language. || _WORDS_PER_SECOND :18, word_count :22, speech_seconds :31, beat_seconds :37, scene_seconds :42, lecture_seconds :53
- aadhi/pipeline/textnorm.py (97): Repair ligatures, odd spaces, invisible characters and mojibake in extracted text (no NFKC). || LIGATURES :17, MOJIBAKE :31, normalize_extracted_text :83
- aadhi/pipeline/prompting.py (97): Prompt files with a PROMPT_VERSION header and helpers that assemble Markdown sections with JSON blocks. || PROMPTS_DIR :21, load_prompt :33, prompt_versions (default: every *.md) :44, system_prompt :51, language_label :56, section :63, json_section :69, extract_json :75, join_sections :91
- aadhi/pipeline/richlite.py (268): Rich-lite markup: a linear tokenizer, plain/HTML/Markdown conversion, narration-markup detection, ⟦n⟧ masking for translation. || Span :21, tokenize :91, tex_to_plain :123, to_plain :135, to_html :143, to_markdown :163, escape_markdown_html :185, narration_markup :214, PLACEHOLDER_RE :221, mask :224, unmask :239, placeholder_problems :244, markup_balance_problems :260
- aadhi/pipeline/scene_checks.py (168): First-pass pedagogy and language checks inside the scene-writer re-ask loop. || script_share :49, language_problem :61, _redundancy_problems :103, _quiz_problems :116, _example_problems :127, _signalling_problems :149, pedagogy_problems :159
- aadhi/pipeline/scene_context.py (313): Prompt context per scene (lecture summary, outline, neighbours, relevant and nearby source, figures, visual notes, glossary, Manim template). || SCENE_SOURCE_CHARS :30, relevant_chunks :80, split_scene_source :116, ScenePosition :134, positions :145, LectureContext :158, scene_prompt_sections :218 (target_words = est_seconds*2.3 :253)
- aadhi/pipeline/screenplay_ops.py (106): Replace scenes in a screenplay and rebuild a LecturePlan from a screenplay (clip scenes left out). || replace_scenes :15, plan_from_screenplay :39, options_for :87, lecture_context :92, position_of :102
- aadhi/pipeline/script.py (482): Stage 3: one structured LLM call per planned scene, run in parallel; validation inside the re-ask loop; a deterministic fallback scene; screenplay assembly. || FAMILY_PROMPTS :61, MAX_BOARD_ITEMS/MAX_BEAT_WORDS (=validate) :69-70, FALLBACK_NOTE :72, script_model :78, template_step_problem :135, family_problems :159, code_problems :187, make_scene_validator :207, fallback_scene :250, write_scene :291, assemble_screenplay (no presentation) :374, write_scenes_detailed :410
- aadhi/pipeline/plan.py (479): Stage 2: one structured planning call, validated and normalised into a LecturePlan plus a lexicon seed; grounding refusal for unreadable sources. || PLAN_PROMPTS :60, plan_model :88, plan_context :98, brief_payload :144, about_source :213, build_plan_prompt (request uses target_minutes) :233, make_validator :288, to_lecture_plan :308, lexicon_seed :363, require_grounding :405, original_attachment :425, generate_plan :440
- aadhi/pipeline/plan_rules.py (657): Pure plan checks and deterministic fixes (hook, recap, chapter cards, quizzes, objectives, misconceptions, flow roles); Screenplay size limits. || DEFAULT_ROLE :35, PlanContext :46, quiz_gaps :110, plan_problems (±25% of target_seconds :227-230) :134, flow_problems :234, plan_limit_problems :288, fit_plan :305, normalize_plan :378
- aadhi/pipeline/plan_state.py (87): Keep the plan, lexicon seed and brief in generation_meta. || PLAN_KEY/LEXICON_KEY/BRIEF_KEY :22-24, load_plan :27, save_plan :40, lexicon_for_plan :61, load_brief :67, save_brief :80
- aadhi/pipeline/preview.py (698): Offline CLI that previews prompts in an isolated workspace with the fake providers forced on. || API_KEY_VARS :78, forced_environment :85, isolated_workspace :116, PreviewContext :152, RecordingLLM :196, recording_llm :220, collect :358, render :600, run_preview :615, main :658
- aadhi/pipeline/pdf_layout.py (412): Turn the PDF worker's layout into Markdown (reading order, headings, tables, figures, code fences, removal of running headers). || is_display_equation :43, label_starts_paragraph :69, PdfLayout :96, _mono_is_code :122, _PageWriter :234, build_layout :375
- aadhi/pipeline/pdf_worker.py (206): Standalone PyMuPDF subprocess that writes result.json and the images. || _page_blocks :44, _page_tables :79, _page_images :115, extract :151, main :183
- aadhi/pipeline/source_review.py (1441): Deterministic source report (outline, inventory, findings, readiness) and the teacher's corrections before planning. || REVIEW_VERSION='3' :61, SourceOverrides :163, SourceReport :306, role_of :344, inline_formulas :654, analyze :878, readiness :1092, overrides_for :1122, override_problems :1131, effective_brief :1184, without_chunks :1252, scene_trace :1288, build_report :1311
- aadhi/pipeline/source_scope.py (2938): Source scoping: separate teachable content from metadata, admin data, SME production scaffolding, timecodes and author names; collect visual notes. || _ADMIN_KEYS :423, looks_like_person :614, contact_value :634, strip_title_admin :947, parse_title_line :1027, key_info :1112, classify_kv :1117, header_line_value :1157, admin_key_line :1185, is_script_label :1398, detect_format :1417, _Scoper :1482 (run :2591, step :2629), scope_source :2848, resolve_visual_notes :2870, scope_summary :2923, excluded_counts :2936
- aadhi/pipeline/quality/__init__.py (186): Quality and consistency lint family plus the Studio quality report (safe repairs and a registry). || QUALITY_CODES :64, AUTHOR_CODES :73, analyse :92, COMPUTE :113, analysis_of :116, lint_quality :126, registry :134, quality_report :170
- aadhi/pipeline/quality/extract.py (239): Typed facts per scene (board fields with paths, occurrences, term groups). || Occurrence :39, BoardField :47, SceneFacts :64, Facts :79, title_name :115, _board_fields (text/term/caption/justification only) :124, extract :211
- aadhi/pipeline/quality/text.py (244): Bounded helpers: group_key/sep_sig/loose, form clustering, edits that skip markup. || group_key :63, sep_sig :69, loose :81, term_ok :119, form_classes :126, latin_board :158, Lesson :168, replace_outside_markup :218
- aadhi/pipeline/quality/terms.py (227): terminology.variant/casing, concept.naming_variant, figure.caption_variant (English boards only). || term_findings :92, concept_names :143, concept_findings :162, figure_captions :196, figure_findings :211
- aadhi/pipeline/quality/abbreviations.py (344): Abbreviations never spelled out, spelled out late, or spelled out two ways. || NOT_ABBREVIATIONS :53, analyse :183, abbreviation_findings :298, registry_entries :330
- aadhi/pipeline/quality/formulas.py (202): formula.symbol_conflict, notation_mismatch, legend_missing. || base_letters :61, symbol_meanings :100, formula_findings :132
- aadhi/pipeline/quality/code.py (125): code.language_unhighlighted (mirrors PRISM_LANGS in web/js/player/board.js), mixed_indentation, mixed_languages. || HIGHLIGHTED :25, code_findings :90
- aadhi/pipeline/quality/pacing.py (88): pacing.dense_run and board.reading_time notes. || DENSE_WEIGHT :22, READ_WPM :25, pacing_findings :50
- aadhi/pipeline/quality/titles.py (82): title.casing_mixed. || title_casing :29, title_findings :69
- aadhi/pipeline/quality/report.py (40): Finding and Repair dataclasses, lint_issue helper. || Repair :17, Finding :23, lint_issue :28, repair_entry :35
- aadhi/pipeline/quality/assist.py (147): Optional AI terminology assistant (QUALITY_AI_TERMINOLOGY) that produces terminology.ambiguous notes. || GenTermAnswers :45, ambiguous_pairs :59, terminology_assist :109
- aadhi/pipeline/prompts/critic.md|repair.md|translate.md|practice.md|quality_terms.md (153): System prompts loaded by critic.py, repair.py, translate.py, companion.py and quality/assist.py. || critic.md PROMPT_VERSION 4 (no Dynamic checks); repair.md v3; translate.md v1 ('The narration is spoken by Aadhi'); practice.md v2; quality_terms.md v1