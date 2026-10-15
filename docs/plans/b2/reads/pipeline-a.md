# pipeline-a: aadhi/pipeline generation pipeline part A (orchestrator, base, ingest, source_scope, brief, plan, plan_rules, plan_state, checkpoint, gen_models, canonicalize, screenplay_ops, dbops, fake_content, prompts/**, plus every a-m module: __init__, aio, assets, audio, chunking, codeblocks, companion, critic, docx_extract, envelope, integrations; prompting.py read as the prompt loader)


## summary
aadhi/pipeline has 67 files, 22,859 lines (the per-file counts are in `files`). Slice A is the content-generation backbone. It owns the job handlers, the stage contracts, ingest, source scoping, the concept brief, the planner and its deterministic fixes, LLM-stage checkpoints, the LLM response models, canonicalisation, DB helpers and the offline fake writer. Through its a-m modules it also owns asset building (TTS, Manim, images, AI video), narration audio joining, the loudness envelope, chunking, code-block handling, the companion sheet and the critic.

orchestrator.py registers four job handlers: generate_lecture, regenerate_scene, build_assets and translate. generate_lecture runs these steps in order: ingest, then optional source-review pause, then brief + plan, then optional plan-review pause, then script, then lint/critic/repair, then companion, then a CAS write of the screenplay (revision+1), then assets and timeline, then a final CAS. Every version write is a compare-and-set on `revision`, fenced by the job lease. Each finished LLM stage is checkpointed per job, so a retried job never pays twice. A still-wanted guard stops paid media as soon as the revision changes.

Source scoping (source_scope.py, 2,938 lines, deterministic) strips metadata, admin data, SME-script packaging and author names before chunking. The concept brief (brief.py) must account for every chunk. Chunks it skips never reach the planner, the scene writers, the critic, repair or the companion sheet. The planner (plan.py + plan_rules.py) feeds problems back through a re-ask loop, then normalises the plan deterministically.

None of the Dynamic-video generation work exists in this slice yet. This was not hand-written-and-missing: it is planned for B2/B3/B4. Missing items include GenerationOptions Dynamic fields, scope.py, casting.py, history.py, direct.py, music.py, fit.py, dynamic_lint.py, the dynamic prompts and the fake-writer extensions. Only B0's inert-schema support reaches this slice: PlannedScene refuses `clip`, plans rebuilt from a screenplay drop clip scenes and map v3 roles, and scene_hash excludes `look` and `overlays`. B1 (the motion engine) has no footprint in aadhi/pipeline; it lives in compose/motion, compose/dynamic, compose/presentation.py and web/js/dynamic. The orchestrator never creates `Screenplay.presentation`, so today a Dynamic timeline only appears for screenplays that already carry a presentation (synthetic fixtures). I found one confirmed crash bug in plan_rules (reproduced) and a chunk-id consistency risk for translated versions (see bugs_or_risks).

## contracts
JOB HANDLERS (orchestrator.py, registered with @job_handler): "generate_lecture":735, "regenerate_scene":979, "build_assets":1070, "translate":1127.

JOB PAYLOAD KEYS:
- base_revision, read as `payload.get("base_revision") or snap.revision`:751/995/1083. The translate payload has none:1139.
- options:760; source_document_id:780.
- resume_state {stage, version_id, base_revision, source_document_id}:763.
- review_source (must be exactly True):809.
- scene_id, instructions (cut to 4000):993-994.
- scene_ids (filtered to known ids):1091-1094.
- source_version_id, target_language, translate_board, tts_voice:1146-1160.
- confirm_paid_scene_ids (assets.CONFIRM_PAID_KEY:152).

AWAITINGREVIEW STATES:
- {"stage":"source_review" (source_review.RESUME_STAGE), version_id, base_revision, source_document_id}:457-459.
- {"stage":"plan_review", ...}:866-868.
- During the source review, `generation_meta['review_stage']='source'`:448.

VERSION STATUS MACHINE: generating -> (awaiting_review) -> building -> ready | failed. In-place jobs restore `ready` when built_revision is not None (986, 1077). Every write is a CAS on {"revision": base} via dbops.cas_version, lease-fenced (_cas:130).
- The screenplay write bumps revision to base+1 and sets status=building:955-958.
- The final write sets status=ready and built_revision=revision only when complete:237-247.
- A scene-scoped build that reused stale scenes leaves built_revision unchanged:1114.

GENERATION_META KEYS:
- options; models {brief, plan, script, critic, provider} or {translate, provider}; prompt_versions (every prompts/*.md); stage_seconds; cost_usd.
- fallback_scenes; written_by {job_id, kind, revision}:626-632; reused_stages.
- source_document_id; ingest_key; ingest {chunks, figures, pages, attach_original, truncated, detected_language, source_format, visual_notes, excluded:{category:n}}:787-791.
- brief (exclude_defaults); plan + lexicon_seed (only while awaiting review; popped at 936-937); plan_notes; repaired_scenes.
- review_stage; source_overrides (source_review); scene_history (<=5 per scene); regenerations[-20:]; resumed_builds[-10:]; last_build; assets {built, reused}; error {message, at}.
- generated_snapshot_key (changes.SNAPSHOT_META_KEY).
- Translations only: source_version_id, translated_from, translate_board.

PROGRESS: base.PIPELINE_PROGRESS:56-64 (stage names ingest/plan/script/validate/companion/assets/timeline).

ISSUE MODEL (base.py:358):
- code regex `^[a-z0-9_]+(\.[a-z0-9_]+)+$`; severity error|warning|info; source lint|critic|manim|assets|system.
- fixable; data is IssueData{category}, excluded when None.

INGESTRESULT (base.py:165):
- Chunk ids `^c\d{4,5}$`, text <= 2000.
- Visual note ids `^v\d{4,5}$`; excluded <= 500; visual_notes <= 500.
- source_format sme_script|notes|textbook|unknown.
- Extract key = compute_key("extract", {sha256, mime, max_pages, max_chars, ingest: INGEST_VERSION="8"}) (ingest.py:433-438). Stored as a private `extract` asset; SourceDocument.extracted_key = the latest extract.
- A version reads its own extract via generation_meta.ingest_key (ingest.py:543-558).

CONCEPTBRIEF (base.py:230): skipped_chunks <= 1000 (brief <= 500 + teacher set-asides). Skipped chunks reach no later stage.
- Brief cache key: compute_key("brief", {prompt sha256, model, prompt_version, cache "2", files}) (brief.py:520-526).

CHECKPOINTS (checkpoint.py):
- Key: compute_key("checkpoint", {job_id, stage, inputs}, "1"). Kind `intermediate`. Body {stage, job_id, data}.
- Stages: plan, script, scene, validate, companion, translate.
- Digest inputs from orchestrator._stage_inputs:532-543: version_id, base_revision, options, engine, models, prompt_versions, ingest_key, source {id, sha256}, teacher, plus source_overrides when present:818.
- Setting: llm_stage_checkpoints.

LLM SCHEMA RULES (gen_models.py:1-14, checked by providers.llm.schema.assert_llm_compatible):
- No ids; no unions except Optional; no tuples or open dicts; no numeric/length constraints.
- Dynamic per-template model names: GenSimulation_<template>, GenBoardScene_<template>.

CANONICAL IDS (canonicalize.py:5-13):
- Scene id = planned id; beats `{scene}-b{n}` (reveal beats continue the numbering); items `{scene}-i{n}`.
- Quiz option shuffle seeded by sha256(scene id).

PROMPTS:
- Every file starts with `<!-- PROMPT_VERSION: n -->` (prompting.py:23).
- Versions: brief 4, critic 4, library_describe 1, plan 2, practice 2, quality_terms 1, repair 3, scene_ai_video 2, scene_board 2, scene_chapter 2, scene_interactive 2, scene_quiz 2, scene_simulation 2, style 3, translate 1.
- The planner system prompt is style + plan (plan.py:60). Scene prompts are composed in script.py (part B).

ASSET IDENTITY (assets.py):
- scene_hash = sha256(canonical_json({scene: dump excluding _HASH_EXCLUDE, lexicon?, voice?}))[:32] (221-241).
- TTS clip key: compute_key("tts", {text, voice, provider, rate, model, language}) (528-532).
- scene_audio key: {clips, pauses, phases, narration, countdown, audio: AUDIO_VERSION "2"} (586-589).
- Envelope: ENVELOPE_FPS=30, stored in SceneAudio.envelope.
- Image key: media_key("image", ...) with variant > 0 adding "variant" (692-709). Video key: compute_key("video", {provider, model, aspect, [duration], prompt[, variant]}) (936-954).

FAKE RESPONDER REGISTRY: fake_content.RESPONDERS:1828-1840 plus prefixes:1841, keyed by response-model class name.

SETTINGS READ IN THIS SLICE:
- LLM: llm_provider, llm_stage_checkpoints, llm_max_parallel, job_max_attempts, redact().
- Ingest: max_source_pages, max_source_chars.
- Manim: manim_allow_freeform, manim_sandbox, manim_background, manim_quality.
- Image/video: image_provider, video_provider, image_fallback_providers, video_fallback_providers, max_ai_videos_per_lecture, image_style_suffix, image_model, veo_model, omni_model, veo_duration_seconds, ai_video_timeout_seconds, auto_video_backup.
- TTS: tts_max_parallel, tts_provider, tts_voice, tts_rate, default_language, elevenlabs_model, openai_tts_model, gemini_tts_model, ffmpeg_path.
- library_auto_save_generated.

INVARIANTS:
- JSON documents in generation_meta are replaced whole, never mutated in place (plan_state.py:5-7).
- Excluded values never reach job events or prompts (ingest.py:530-533; plan.py:8-10).
- build_assets never mutates the screenplay (assets.py:1).
- Hidden scenes are never built or listed stale (assets.py:244-253, 1546-1548).

## conventions
MODULE STRUCTURE:
- Every module starts with `from __future__ import annotations`.
- A long module docstring states behaviour, invariants and design references (e.g. "design §6.7").
- British spelling throughout (normalise, colour, canonicalise).
- ruff line-length 120, target py311 (pyproject.toml:51-53); E501 is ignored for long prompt strings.
- Constants are UPPER_CASE with a trailing comment on units and limits.
- Limits are read from schema metadata rather than duplicated (canonicalize._max_len:58).

PURE CODE VERSUS I/O:
- Prompt builders, plan_rules, brief validation, canonicalize and source_scope are pure: no I/O, no settings.
- All DB access is synchronous in dbops, called via asyncio.to_thread + ctx.session() (orchestrator._db:116).
- Cross-package calls go through integrations.py: lazy imports with graceful degradation, so tests monkeypatch one seam.

ERROR HANDLING:
- Broad `except Exception` only with a `# noqa: BLE001 - <reason>` comment.
- These are always re-raised: JobCancelled, BudgetExceeded and (in asset/critic/companion code) RateLimited.
- Advisory stages (brief, critic, practice, envelope, library) degrade to info or warning issues, never fail the lecture.
- Job-level failures map to FatalJobError(code=...) or RetryableJobError in _guard.
- Teacher-facing strings go through ctx.log in plain English, redacted via settings.redact. Provider errors are shown as type names or redacted text.

LLM CALL PATTERN:
- `async with integrations.limit("llm"): llm.generate_json(model=..., system=system_prompt(...), prompt=join_sections(json_section(...)...), schema=GenX, temperature=..., on_usage=ctx.record_usage, validate=<closure returning problem strings>, validation_retries=N)`.
- The validator returns all problems on the first pass and only hard problems afterwards.
- A deterministic normaliser then fixes the remainder.

PROMPT CONTEXT FORMAT:
- Markdown `## Title` sections with fenced JSON. Fakes parse the same sections back with extract_json, so fake output is a pure function of the prompt.
- Any prompt change must bump its PROMPT_VERSION. Versions are recorded in generation_meta and feed the brief cache and checkpoint digests.

DETERMINISM:
- Seeds come from sha256, never hash() (canonicalize.shuffle_order:113).
- Content-addressed keys use compute_key/canonical_json.
- Version bumps (INGEST_VERSION, BRIEF_CACHE_VERSION, CHECKPOINT_VERSION, AUDIO_VERSION) invalidate caches deliberately.
- Earlier cache keys are still looked up after a change, so already-paid work is not paid again (assets.py:47-56, 683-709).

SCHEMA EVOLUTION:
- New optional fields use Field(exclude_if=...) so stored JSON and digests are unchanged at defaults (base.py:102, 369).
- Signature evolution is tolerated via _accepts_kw (orchestrator.py:387).

TESTS:
- Live in tests/pipeline/* with fixtures from tests/pipeline/conftest.py: providers (install_providers: offline fakes, FakeLLM using fake_content responders), templates (fake equation_steps registry), sample_ingest, options, fast_audio.
- Scripted LLM responses use providers.llm.on("<SchemaName>", fn) and are inspected with llm.calls_for(...).
- Every batch must keep tests offline and extend the fakes (design §10.2 gate 4, §6.12).

## dynamic_status
Design batches B0 to B4 and what of each exists in this slice:

B0 (inert schema, done per design §10.3 "As built"). Footprint in this slice:
- PlannedScene refuses `clip` (base.py:283-289).
- plan_from_screenplay leaves non-plannable scenes out (screenplay_ops.py:46-50).
- planned_from_scene gives v3 roles as their classic counterparts via _PLAN_ROLE (canonicalize.py:665-701). This matches "rebuilt plans ... give v3 roles as their classic counterparts until B4".
- regenerate_scene refuses non-plannable scenes (orchestrator.py:1008-1009, FatalJobError bad_request). The 409 action_unavailable is raised earlier, in api/routers/version_actions.py:124.
- scene_hash excludes `look` and beat `overlays` but keeps cuts/speaker/voice_override (assets.py:215-241, docstring 230-232). The design cites stale line numbers "pipeline/assets.py:202–220" (§6.7, line 1626); the actual code is at 215-241.
- Dynamic usage of the new manifest fields: none. BeatAudio.persona/words_exact and SceneAudio.lufs/speaker_gains are never set by audio.py:219-223 or assets.py:618-625.

B1 (motion engine core, "built and integrated" per design §10.3 lines 2459-2526; container benchmark pending): nothing in aadhi/pipeline. B1 lives in compose/motion/*, compose/dynamic/*, compose/presentation.py (minimal ResolvedLook: board_generic, mesh_drift/colour_block, cut/crossfade) and web/js/dynamic/*. The orchestrator only reaches it through _timeline (orchestrator.py:215-229) -> compose.timeline.build_timeline, which resolves a look only when `screenplay.presentation` is set and DYNAMIC_ENABLED is on (timeline.py:729-801).

B2 (visual system): not started in this slice. There is no pipeline/direct.py, and nothing writes SceneBase.look.

B3 (audio): not started in this slice. Missing: pipeline/casting.py, pipeline/history.py, pipeline/music.py, persona-aware TTS, calibration, speaker gains.

B4 (generation, planned): not started.
- GenerationOptions (base.py:73-114) has none of the §6.3 fields: video_style, video_format, length_profile, reel_profile, destination, look, voice, music, aadhi_recap, captions_burn, sfx, series_part, focus_concepts. It also has no unused-field validator.
- Missing modules: pipeline/scope.py, fit.py, dynamic_lint.py.
- Missing prompts: prompts/dynamic_plan.md and prompts/dynamic_scene.md; style.md (v3) has no Dynamic section.
- gen_models has no visual_intent (GenPlannedScene:80), no new board kinds (GenBoardKind:31), and no speaker/answers/icon_hint/colour_is_content/sequence/level fields.
- base.NarrativeRole (191-193) is still classic. Note that 'check' is already classic; the design lists it as new in §6.5.
- plan_rules has no per-format budgets.
- fake_content writes classic kinds only.
- The orchestrator never creates Screenplay.presentation, casting or rotation. So in production no Dynamic lecture can be generated; only fixtures with a synthetic presentation reach the B1 engine.

Divergences between code and design to resolve:
1. Pause metadata naming. §6.4 says the scope pause sets `generation_meta.stage = "scope_review"`. The existing pause mechanisms use `generation_meta['review_stage']` (source_review.REVIEW_STAGE_KEY='review_stage', value 'source'; orchestrator.py:448) plus AwaitingReview `state["stage"]` ('plan_review' / 'source_review'; 457-459, 866-868). B4 should follow one convention.
2. §6.4 cites orchestrator.py:854–867 as the review_plan mechanism. It is accurate (854-868).
3. §6.10 relies on `orchestrator._version_brief` (exists, 338-361) and on passing the parent brief, extract and overrides in the payload. No such payload keys exist yet.
4. §6.1 lists casting before planning and direct.py after companion, before the screenplay CAS. Neither hook exists.
5. api/generation.py:270-282 reads `opts.voice` / `opts.music` via getattr (B0 note "names the budget pre-check already reads"). GenerationOptions is a StrictModel with extra='ignore' (screenplay.py:182-), so these keys are silently dropped today and the pre-check never sees them.
6. §6.1 expects assets to use format-specific image aspects and the stored AssetStyle suffix (unless colour_is_content), and Manim to use the stored asset-style background. Code uses fixed PANEL_IMAGE_ASPECT '4:3' / SCENE_IMAGE_ASPECT '16:9' (assets.py:135-136), settings.image_style_suffix (680-681) and settings.manim_background (914).
7. §6.9 lists presenter_lint as skipped in Dynamic. presenter_lint is part B, and no Dynamic switch exists anywhere in the pipeline.

## gaps_todos
MARKERS AND STUBS: no TODO/FIXME/XXX/NotImplemented markers anywhere in aadhi/pipeline/*.py. The only grep hit is the word "TODO" inside an acronym stop-list at quality/abbreviations.py:56.

DEAD OR DEFENSIVE CODE:
- orchestrator._plan:462-466: the fallback without `brief=` is unreachable because generate_plan always accepts it (plan.py:440-443).
- orchestrator._lint:396-401: the fallback without `ingest=` is likely unreachable (validate.lint is part B).
- base.py:5-16 contract docstring: make_plan, write_scenes, repair.repair and build_assets are entry points the orchestrator does not use. make_plan is used by evals/runner.py:61; build_assets by tests.
- brief.fake_brief_responder:609-663 is a test-only second fake brief. Production fakes use fake_content.brief_responder:750, so two fake briefs must be kept in sync.

COSTS AND BUDGETS:
- prompt_versions() (prompting.py:44-48) records every prompt file, including library_describe and quality_terms. These also enter the checkpoint digest (orchestrator.py:541), so bumping an unrelated prompt invalidates plan/script checkpoints for in-flight retries.

MISSING TESTS (grepped tests/pipeline):
- No test for _ensure_recap moving a recap out of a later chapter. The only recap test is test_plan_script.py:53.
- No test that a regenerate on a translated version reads the same extract and chunk ids.

DYNAMIC WORK NOT STARTED IN THIS SLICE (B2/B3/B4 per design):
- Files: pipeline/scope.py, casting.py, history.py, direct.py, music.py, fit.py, dynamic_lint.py; prompts dynamic_plan.md and dynamic_scene.md.
- Fields: GenerationOptions Dynamic fields; gen_models Dynamic fields; canonicalize for the new kinds (question/stat/step/quote/pair, answers, speaker, icon_hint, sequence, level, visual_intent, colour_is_content).
- Fakes: calibration and music fakes (B3) and the extended fake writer (B4, §6.12).

OPEN ITEM CARRIED FROM B0 (design line 2423-2424): an Omni/Veo create that gets no answer within a live job ends as `failed`, not ambiguous, in _submit_video (assets.py:1231-1237: status ABANDONED if SUBMITTED else FAILED). Still as described; not changed.

## bugs_or_risks
1. CONFIRMED CRASH: plan normalisation fails with IndexError when a chapter holds only a recap.
- Where: plan_rules._ensure_recap (551-572) pops a recap scene from whatever chapter holds it and inserts it at chapters[0][1]. If that chapter held only the recap, it becomes empty. _ensure_chapter_cards (575-577) then reads `ch.scenes[0]` and raises IndexError.
- Why it is not caught: empty chapters are filtered at 452, before the _ensure_* passes run.
- Reproduced with .venv: previous_session_summary set, chapter 2 = [recap only] -> "IndexError list index out of range".
- Impact: plan.make_validator (299-302) catches only ValueError, so the error escapes generate_json and generate_plan (469). The job fails and is retried by the generic path (orchestrator.py:330-335), with fresh LLM cost each time.
- Fix: re-filter empty chapters after _ensure_recap, or skip empty chapters in _ensure_chapter_cards.

2. RISK: translated versions lose their extract identity.
- Where: _translate writes generation_meta without `ingest_key` (and without source_overrides) (orchestrator.py:1188-1199). It does copy `brief` (1197-1199) and `source_document_id`.
- What breaks: a later regenerate_scene on the translation calls _load_ingest(meta without ingest_key) (364-384). That falls back to SourceDocument.extracted_key, which is the latest extract and may come from a newer INGEST_VERSION with different chunk ids (ingest.py:543-548 explains why versions must keep their own).
- Consequences: the copied brief's skipped_chunk ids and the scenes' source_refs can then name other text, so non-teaching chunks could reach the rewrite. _teacher_scoped (430-438) also cannot apply the teacher's set-asides, because overrides_for(meta, None) returns None.
- Fix: copy src.generation_meta['ingest_key'] (and source_overrides) into the translation meta.

3. RISK: the concept brief silently disappears on a rate limit.
- Where: _concept_brief (415-427) swallows every Exception except JobCancelled/BudgetExceeded. That includes RateLimited, which is a ProviderError subclass (providers/base.py:60).
- Impact: on a rate limit the lecture is planned without the brief. Chunk-level skips are then not applied: admin, scaffolding and duplicate chunks reach the planner and writers, and no retry happens.
- Inconsistency: critic (critic.py:301) and practice (companion.py:203) re-raise RateLimited.

4. RISK FOR B4: options can silently fall back to defaults.
- version_options (204-212) returns the project settings, or GenerationOptions() defaults, when the stored options fail validation.
- Once B4 adds the Dynamic validator (§6.3), a stored option set that fails it would make regenerate_scene / build_assets run with classic or default options without any warning.
- Related: GenerationOptions inherits extra='ignore', so unknown Dynamic keys are dropped silently (contracts item 5 above).

5. RISK: the B3/B6 audio fields are hashed but ignored.
- Beat.speaker, cuts and voice_override_asset_key are part of scene_hash (assets.py:215-218). TTS ignores all three: there is one lecture voice (voice_config 301-308; scene_audio 575-625), cuts are not applied, and the override recording is not used.
- They are gated by DYNAMIC_FEATURES today. If a flag is enabled before B3/B6 land, the audio silently would not reflect those fields.

6. MINOR: source_scope music-label inconsistency.
- _Scoper.is_label (2461-2465) and in_drop (2431-2432) call `_PRODUCTION.match(p)` directly. This bypasses production_label's rule that a "Music:" label must be ALL CAPS (268-274).
- Effect: a lowercase "Music: ..." line in an SME script ends a visual capture, or is treated as a label inside a drop block, inconsistently with the main path.

7. MINOR: chunking silently truncates long table rows.
- _split_long cuts any table row longer than 1700 characters to `limit - 200` (chunking.py:127).

8. MINOR: _ensure_quizzes is bounded to 50 passes (plan_rules.py:591). Beyond that, quiz gaps remain without a note.

## b2_b3_touchpoints
B2 (VISUAL SYSTEM: themes/templates/direct.py/events.py/kinetic.py/textfit/zones/fonts/icons)

1. direct.py, generation run. Hook point: in orchestrator._generate after the companion step (919-931) and before the screenplay CAS (933-960). There it fills Screenplay.presentation-dependent `SceneBase.look` for every scene (lock='auto').
- B4 must first create `presentation`; per design §6.1, casting and rotation run before planning, around 811-848.
- `look` must be set before `_record_generated_snapshot` (954). Otherwise "Generated vs edited" would show the looks as teacher edits.

2. direct.py in regenerate_scene: after replace_scenes (1027/1034) and before the CAS (1049-1053), direct only the rewritten scene.
- repair.carry_over already keeps the old look (repair.py:52-92).
- §6.8 locks apply: auto, settled, pinned.

3. direct.py in translate: before the CAS (1202-1206). Copy presentation and looks, and switch the display face to Anek for Indic scripts (§6.11).

4. No asset rebuild from looks: assets.scene_hash already excludes `look` and beat `overlays` (assets.py:215-218). direct.py's writes and editor look changes therefore never force build_assets. Keep that list in sync if new look-only fields are added.

5. events.py and kinetic.py: run inside compose.timeline.build_timeline, reached from orchestrator._timeline (215-229) via integrations.build_timeline_fn (integrations.py:307-313). Signature: fn(sp, manifest, settings=, version_id=, revision=). They need manifest audio word timings: BeatAudio.words / words_estimated from audio.build_scene_audio (audio.py:214-223) and SceneAudio.beats offsets (assets.py:618-625).

6. Format-aware media (B4 with B2 tokens):
- Image aspects: assets.PANEL_IMAGE_ASPECT / SCENE_IMAGE_ASPECT (135-136), used at 1288-1290, 1426, 1436-1437.
- Style suffix: _full_image_prompt (680-681), which needs Presentation.asset_style unless colour_is_content.
- Manim: ManimRenderRequest.background=settings.manim_background (914). It needs the stored asset-style background and colour map.

7. No LLM visual direction: gen_models and canonicalize must never expose `look` or template fields (canonicalize.py:409-427 builds scenes without them). B4 adds icon_hint, colour_is_content and the new item kinds to GenBoardItem (gen_models.py:139-154) and canonicalize._board_item (176-216).

8. Text fit and zones: no pipeline hook. They are compose/web side. Lint thresholds per format (§6.9) go in validate.py (part B) and the new dynamic_lint.py.

9. Fakes for the gallery and offline end-to-end runs: fake_content board_responder (1505-1534) and the RESPONDERS registry (1828-1840) must emit the new kinds in B4.

B3 (AUDIO)

1. Casting and personas.
- Current voice selection: assets.voice_config (301-308) picks one lecture-wide provider and voice (options.tts_voice > settings.tts_voice for the default language > integrations.default_voice:154-169).
- B3: resolve the voice per Beat.speaker from Presentation.voices (VoiceCast narrator/aadhi/student), with VoiceCalibration.control replacing options.tts_rate / settings.tts_rate.
- Persona registry seam: integrations.get_tts (80-82) / default_voice.

2. TTS keys.
- _Builder.clip_key (528-532) must include the persona and calibration control.
- _Builder.tts_clip (558-573) must synthesise with them. Per-sentence synthesis for words_exact also goes here.
- _Builder._reusable (534-555) compares scene-level SceneAudio.provider/voice/language with the single lecture voice; this must become persona-aware per beat.

3. Scene audio.
- _Builder.scene_audio (575-625). Its key (586-589) is where "baked gains change the scene-audio key but not beat keys" applies.
- Speaker gains are applied in audio.build_scene_audio (187-235), before encode_mp3 (234).
- Set BeatAudio.persona/words_exact (audio.py:219-223) and SceneAudio.lufs/speaker_gains (assets.py:618-625; schemas/manifest.py:47-48, 72-73).

4. Music and fit.
- pipeline/music.py runs after narration timing (§5.10). fit.py runs after build_assets (§6.4).
- Both hook into orchestrator._build_and_finish between _build_assets_guarded (672) and _timeline (675), and in _build (1100-1104).
- record_asset_refs for music via dbops.ensure_asset_refs (dbops.py:122-127), as assets.run already does for its keys (1613-1619).
- Paid music generation should go through the same generation_guard (orchestrator._build_assets_guarded:516-520; GENERATION_CLAIM_KINDS in storage.assets).
- Budget pre-checks via integrations.estimate_cost (319-328) / ctx.ensure_budget, as in assets._generate_video (1045-1052).

5. Inheritance on regenerate: _regenerate (991-1062) keeps sp.presentation through replace_scenes (screenplay_ops.py:15-22). Fresh look / re-cast must not re-roll voices there (§6.8 determinism).

6. Rotation history (history.py): needs DB reads at generation time. dbops (load_project:84, VersionSnapshot.generation_meta) is where snapshot helpers belong. Call them from orchestrator._generate before planning (811-848).

7. Live player audio: envelope.py (ENVELOPE_FPS=30, the mascot loudness envelope) is untouched by music. The design's live-player GainNode, SFX and music work is in web/js (outside this slice).

8. Fakes (design §6.12, B3): fake calibration, fake music (Lyria-shaped responses including a rejected one with lyrics), fake Azure word boundaries. These are registered like fake_content.ensure_fake_responders (1875-1885) or under tests/pipeline/fakes.install_providers.

## files
- aadhi/pipeline/orchestrator.py (1213): Job handlers generate_lecture / regenerate_scene / build_assets / translate; CAS writes, error->status mapping, LLM-stage checkpoints, same-job resume, source-review and plan-review pauses, still-wanted guard. || Conflict:103; VersionChanged(JobCancelled reason=version_changed):107; _db:116 (to_thread+ctx.session); _cas:130 (flush, ctx.assert_lease fence, dbops.cas_version); _issues_values:152; merge_asset_issues:168 (ASSET_SOURCES=('assets','manim'):94); StageClock:177; version_options:204 (meta.options -> project.settings -> GenerationOptions(), silently skipping invalid); _timeline:215 (integrations.build_timeline_fn; failure -> timeline.failed issue, previous timeline kept); _final_values:232 (built_revision only when complete); _mark_failed:250; _guard:287 (Conflict/VersionChanged -> {'skipped':'revision_conflict'}; RateLimited->RetryableJobError; IngestError/ProviderNotConfigured/ProviderError->FatalJobError; generic -> retry while attempts remain); _version_brief:338 (follows source_version_id <=8 hops, same source_document_id); _load_ingest:364 (own ingest_key -> source.extracted_key -> re-ingest -> empty); _accepts_kw:387; _lint:396; teacher_meta:404; _concept_brief:415 (never fails the lecture); _teacher_scoped:430; _pause_for_source_review:441 (AwaitingReview stage=source_review); _plan:462; _ensure_current:478; _ProductionGuard:485 (GUARD_RECHECK_SECONDS=5.0:95); _build_assets_guarded:516; _stage_inputs:532 (digest inputs); _plan_data/_plan_from:546/551; _script_data/_script_from:562/568; _repair_data/_repair_from:582/587; _sheet_from:598; _translation_from:607; WRITER_KEY='written_by':626; writer_mark:630; written_by_this_job:635; _build_and_finish:653; _resume_build:687; _record_generated_snapshot:715; generate_lecture:735/_generate:746 (ingest 776-793, overrides 794-809, brief+plan 811-872, review_plan pause 854-868, script 874-896, validate 898-917, companion 919-931, screenplay CAS 933-960, assets+timeline 962-971); regenerate_scene:979/_regenerate:991 (non-plannable refused 1008, scene_history <=SCENE_HISTORY_LIMIT=5:93, regenerations[-20:]); build_assets_job:1070/_build:1081 (complete=not stale_scenes:1114); translate_job:1127/_translate:1134.
- aadhi/pipeline/base.py (376): Pipeline contract: GenerationOptions, intermediate artifacts (IngestResult, ConceptBrief, LecturePlan), Issue; stage signature docstring. || SUPPORTED_LANGUAGES:45 (en-IN,en-US,en-GB,ta-IN,hi-IN,te-IN,kn-IN,ml-IN); PIPELINE_PROGRESS:56; GenerationOptions:73-114 (StrictModel => extra='ignore'; language, board_language, audience<=200, target_minutes 3-90, depth, teacher metadata, previous_session_summary/extra_instructions<=4000, include_quizzes, quiz_every_n_concepts 1-6, allow_manim/freeform/generated_images/ai_video/interactive/gifs, max_ai_videos 0-8, image_provider, prefer_library_visuals (exclude_if False), review_plan, tts_provider/voice/rate (regex), llm_provider, llm_model_plan/script); SourceChunk:117 (id ^c\d{4,5}$, text<=2000); ExcludedCategory:126; ExcludedItem:137; SourceMeta:146; VisualNote:156 (^v\d{4,5}$); IngestResult:165; NarrativeRole:191 (classic 10 roles only); BriefFact:196; BriefConcept:201; SkipReason:214; SkippedChunk:223; ConceptBrief:230 (skipped_chunks<=1000; cited_chunk_ids:248, skipped_chunk_ids:256); PlannedScene:261 (_plannable refuses clip:283-289; manim_template only for simulation/manim panel:291); PlannedChapter:298; LecturePlan:308 (_refs validator:320, assert_acyclic); Severity:346; IssueData:350; Issue:358 (code ^[a-z0-9_]+(\.[a-z0-9_]+)+$, source lint|critic|manim|assets|system, data exclude_if None).
- aadhi/pipeline/ingest.py (558): Stage 1: source file -> IngestResult (PDF via sandboxed pdf_worker subprocess, DOCX via mammoth, TXT/MD structure), text normalisation, source scoping, truncation, chunking, figure storage, content-addressed extract cache. || INGEST_VERSION='8':86; IngestError:94; readable_chars:101; source_kind:106; decode_text:118; _structure_lines:173; text_to_markdown:230; markdown_source:236; close_open_fence:242; INSTRUCTION_LIKE:258 / instruction_like_warning:270 (warning only, never a defence); pdf_timeout_seconds:311; run_pdf_worker:328 (python -I, minimal env _ENV_ALLOW:91, process-tree kill); _store_figure:358 (public 'figure' asset, bytes_key); _ingest_pdf:365 (attach_original heuristics: scanned>=30%, math_ratio>=0.02, bad_ratio>=0.01, <150 chars/page); pdf_title:400; _ingest_docx:424; extract_key:433 (sha256, mime, max_pages, max_chars, INGEST_VERSION); _build:441; ingest_source:507 (cache by extract_key, records extracted_key + AssetRefs, logs scope_summary counts only); load_ingest:537; load_version_ingest:543.
- aadhi/pipeline/source_scope.py (2938): Deterministic source scoping: moves document metadata to SourceMeta, admin/production/person data to ExcludedItem, author visual directions to visual notes; SME-script detection and continuous-lecture flattening; author-name scrubbing; safety-net fallbacks. || MAX_EXCLUDED=400:75; MAX_VISUAL_NOTES=500:76; VISUAL_NOTE_CHARS=1500:77; timecode/duration/structure regexes 91-394; production_label:268; _KeyInfo:406; _META_KEYS:414; _ADMIN_KEYS:423; _KEY_TABLE:496; _lookup:521; ScopeResult:552; plain_text:569; strip_timecodes:581; strip_body_timecodes:595; looks_like_person:614; contact_value:634; _value_ok:676; smart_title:739; split_sentences:812; _PersonMatcher:870; strip_title_admin:947; TitleLine:1007; parse_title_line:1027; key_info:1112; classify_kv:1117; _loose_kv:1142; header_line_value:1157 (used by leak lint); admin_key_line:1185; front_matter_span:1256; _units:1261; _script_table_columns:1376; is_script_label:1398; detect_format:1417; _Scoper:1482 (narration:1628, scrub_people:1667, _drop_board_repeats:1705, _scrub_output:1762, _classify:1850, _accept_run:1970, _front_end:2044, handle_kv:2186, handle_table:2272, _script_table:2327, start_drop/end_drop/in_drop:2385-2438, handle_structural:2468, _segment:2553, run:2591, step:2629, handle_label:2718, sme_line_dropped:2775, handle_heading:2793, handle_text:2822); MIN_KEEP_RATIO=0.15:2841; scope_source:2848 (normal -> safe mode -> as written + warning); resolve_visual_notes:2870; scope_summary:2923; excluded_counts:2936.
- aadhi/pipeline/brief.py (663): Concept brief: one structured LLM call (GenBrief) on the scoped chunks; chunk accounting validator; deterministic canonicalisation (keys, acyclic prereqs, teaching order, skip vetting, author-name scrub); cached as an 'intermediate' asset. || BRIEF_PROMPTS=('brief',):55; BRIEF_CONTEXT_CHARS=200_000; BRIEF_CACHE_VERSION='2'; MIN_BRIEF_CHARS=200; MAX_CONCEPTS=40; MAX_PROBLEMS=20; MAX_SKIPPED_SHARE=0.5:61; BriefUnavailable:72; brief_model:76 (fast tier, llm_model_plan override); _chunk_payload:86; brief_request:96; build_brief_prompt:106 (pure); concept_key:133; _cycle:140; teaching_signal:192; gen_cited:216; accounting_problems:223; brief_problems:252; make_validator:309 (all problems first pass, hard only later); teaching_order:321; person_values:350; skipped_from_gen:404; brief_from_gen:445; brief_cache_key:520 (prompt sha + model + PROMPT_VERSION + cache version + attached file hashes); _cached/_store:529/541; build_brief:563 (temperature 0.2, validation_retries=2); fake_brief_responder:609 (test helper only; production fake is fake_content.brief_responder).
- aadhi/pipeline/plan.py (479): Stage 2: plan prompt assembly (request, teacher meta, about-source note, Concepts to teach, templates, figures, visual suggestions, non-skipped chunks), grounding refusal, original-file attachment (Gemini only), GenPlan call + validation, conversion to LecturePlan + lexicon seed. || PLAN_PROMPTS=('style','plan'):60; PLAN_CONTEXT_CHARS=250_000; MAX_ATTACH_BYTES=20MB; MIN_GROUNDING_CHARS=200 / _PER_PAGE=40; PlanResult:70; TeacherMeta:78; plan_model:88; skipped_ids:93; plan_context:98; _chunk_payload:116; excluded_matcher:128; brief_payload:144; visual_note_payload:166; SOURCE_FORMAT_NOTES:185; LENGTH_NOTE:200; set_aside_note:204; about_source:213; with_document_meta:225; build_plan_prompt:233; request_from_prompt:283; make_validator:288 (later passes: normalize+to_lecture_plan, catches ValueError only); to_lecture_plan:308; lexicon_seed:363; source_note_for:386; scene_source_note:399; require_grounding:405; original_attachment:425; generate_plan:440 (temperature 0.5, validation_retries=2); make_plan:476.
- aadhi/pipeline/plan_rules.py (657): Pure plan checks (hard/soft) and deterministic fixes; Screenplay-limit fitting. || TEACHING_TYPES/HOOK_TYPES/BASE_SCENE_TYPES:23-25; MAX_CHAPTERS=40, MAX_CHAPTER_SCENES=60, MAX_SCENES=200, CHAPTER_TITLE_MAX=160, META_MAX, BRIDGE_MAX=300, MAX_VISUAL_NOTES=10 (28-34); DEFAULT_ROLE:35; PlanContext:46 (allowed_scene_types:64, allowed_panels:77); quiz_gaps:110; plan_problems:134 (duration within +/-25%:227-230); flow_problems:234; size_problems:274; plan_limit_problems:288; fit_plan:305; clean_title:366; NormalizeReport:374; normalize_plan:378; _ensure_flow:466; _fix_scene_type:483; _break_cycles:504; _new_scene:532; _ensure_hook:536; _ensure_recap:551; _ensure_chapter_cards:575; _ensure_quizzes:589 (<=50 passes); _ensure_objectives:621; _ensure_misconceptions:639; chapter_of:653.
- aadhi/pipeline/plan_state.py (87): Plan/lexicon/brief persistence in generation_meta (dict always replaced whole). || PLAN_KEY='plan':22; LEXICON_KEY='lexicon_seed':23; BRIEF_KEY='brief':24; load_plan:27; save_plan:40; load_lexicon_seed:49; lexicon_for_plan:61; load_brief:67; save_brief:80 (exclude_defaults=True).
- aadhi/pipeline/checkpoint.py (124): Job-scoped LLM-stage checkpoints (intermediate assets keyed by job id + stage + input digest), loaded only on attempt>1; per-scene checkpoints. || CHECKPOINT_KIND='intermediate':36; CHECKPOINT_VERSION='1':37; stage_digest:40 (sha256 of canonical_json); StageCheckpoints:45 (enabled needs int job_id>0 and settings.llm_stage_checkpoints; resumable when attempt>1; key:58; load:61; save:85 never raises); SceneCheckpoints:100 (STAGE='scene', LABEL='scenes', digest includes scene id + attached).
- aadhi/pipeline/gen_models.py (473): LLM-facing response models (no ids, no unions except Optional, no tuples/open dicts, no numeric constraints); per-template dynamic models via create_model. || GenSceneType:26; GenPanelKind:30; GenBoardKind:31 (classic 14 kinds only); GenBloom:35; GenMascotPosition:36; BOARD_SCENE_TYPES:38; GenModel:41 (extra='ignore', str_strip_whitespace); GenConcept:52; GenObjective:60; GenMisconception:67; GenGlossaryTerm:74; GenPlannedScene:80 (no visual_intent); GenChapter:108; GenPlan:115; GenVariable:133; GenBoardItem:139; GenBeat:157 (no speaker/answers); GenChartDataset..GenPrimitive3D:170-193; GenSidePanel:196; GenSceneCommon:234; GenBoardScene:241; GenChapterCard:248; GenQuizDistractor:253; GenQuiz:259; GenSimulationBase/Code:271/275; GenAIVideo:281; GenInteractive:289; GenFinding:299; GenCritique:311; GenTranslatedField/GenTranslation:316/321; GenPracticeProblem/GenPractice:325/335; _simulation_model:344; simulation_model:354; _panel_manim_model:363; board_scene_model:376; scene_family:384; model_for_scene:397; ALL_STATIC_MODELS:407; GenExcludedCategory:417; GenBriefFact..GenBrief:420-473 (_plain_questions:467).
- aadhi/pipeline/canonicalize.py (701): Gen output + PlannedScene -> canonical Scene with every id assigned; strict mode reports problems for the re-ask loop, lenient mode repairs/clamps; reverse mapping scene -> PlannedScene. || SCENE_ADAPTER:55; _max_len:58; MAX_BOARD_ITEMS:67, MAX_BEATS:68, MAX_REVEAL_BEATS:69, MAX_CHAPTER_BEATS:70 (read from schema); _NO_TRUNCATE:72; _DEFAULT_MASCOT:75; CanonicalizationError:85; CanonContext:94; child_id:107 ('{scene}-{prefix}{n}', <=64 chars); shuffle_order:113 (sha256(scene id) seeded Random); format_validation_error:121; _board_item:176; _checked_item:219; _beat_base:235; _plain_beats:256; _board_beats:266; _side_panel:315; intent_of:403; _common:409; _board_scene:435; _chapter_scene:462; _quiz_scene:476; _simulation_scene:518; _ai_video_scene:537; _interactive_scene:552; _BUILDERS:559; build_scene_dict:569; clamp_to_limits:574; canonicalize_scene:616; scene_problems:651; _PLAN_ROLE:667 (pretraining->prerequisite, predict->context, recap->synthesis); planned_from_scene:670.
- aadhi/pipeline/screenplay_ops.py (106): Whole-screenplay ops: replace scenes by id, rebuild a LecturePlan from a screenplay (clip scenes excluded), options aligned to screenplay languages, scene-writing context. || replace_scenes:15; _chunked:25; plan_from_screenplay:39 (PLANNABLE_SCENE_TYPES filter:50); options_for:87; lecture_context:92; position_of:102.
- aadhi/pipeline/dbops.py (161): Synchronous DB helpers (snapshots, CAS, status-guarded updates, AssetRef insertion). || VersionSnapshot:25 (get_screenplay/get_manifest via validate_stored:39-43); ProjectSnapshot:47; SourceSnapshot:61; load_version:73; load_project:84; load_source:102 (given id within project, else latest); record_extract:113; ensure_asset_refs:122 (= assets_auth.record_asset_refs); issue_counts:130; cas_version:138 (db.compare_and_set); set_status_if:143; set_current_version_if_unset:154.
- aadhi/pipeline/fake_content.py (1885): Deterministic offline LLM responders (LLM_PROVIDER=fake) derived only from prompt JSON sections: brief, plan, every scene family, critic, translation, practice; registry on FakeLLM. || text helpers clean_text:87, source_lines:99, prose_sentences:136, table_sentences:199, speakable:226, key_phrase:246, title_of:262, find_formula:280, latex_of:321, formula_steps:335, derivation:358; section_kind:453; concept_name:478; natural_case:495; qa_pairs:541; source_questions:583; SourceConcept:608; source_concepts:648; brief_responder:750; skip_reason:816; account_for_chunks:833; find_acronyms:863; _brief_concepts:886; _bridge:908; plan_responder:990; _scene_inputs:1205; _title_scene:1325; _content:1366; _worked_steps:1412; _situation:1455; _example:1481; board_responder:1505; chapter_responder:1537; quiz_responder:1590; _equation_animation:1667; _derivation_animation:1698; simulation_responder:1722; ai_video_responder:1751; interactive_responder:1764; critique_responder:1782 (always empty); translation_responder:1786; practice_responder:1794; RESPONDERS:1828; _PREFIXES:1841 ('GenSimulation_','GenBoardScene_'); responder_for:1844; schema_names:1854; register_fake_responders:1862; ensure_fake_responders:1875.
- aadhi/pipeline/prompts/brief.md (111): Concept-brief system prompt (teaching content only, account for every chunk, skip reasons). || PROMPT_VERSION: 4
- aadhi/pipeline/prompts/plan.md (153): Planner system prompt (appended after style.md): one continuous session, sequencing, narrative_role/bridge_in, visuals only with a reason, timing ~140 wpm, keys. || PROMPT_VERSION: 2
- aadhi/pipeline/prompts/style.md (109): Shared house style (REC, Aadhi blackbuck narrator persona, beats, board rules (at most 5 items), fading, grounding, languages); no Dynamic section yet. || PROMPT_VERSION: 3
- aadhi/pipeline/prompts/critic.md (58): Chapter critic prompt; flow tags [order]/[bridge]/[repeated_intro]/[packaging]. || PROMPT_VERSION: 4
- aadhi/pipeline/prompts/repair.md (33): Scene rewrite instructions incl. packaging/flow issue codes. || PROMPT_VERSION: 3
- aadhi/pipeline/prompts/scene_board.md (64): Board-scene writer (title/recap/content/example/summary shapes, side panel rules). || PROMPT_VERSION: 2
- aadhi/pipeline/prompts/scene_chapter.md (16): Chapter card writer. || PROMPT_VERSION: 2
- aadhi/pipeline/prompts/scene_quiz.md (40): Retrieval quiz writer. || PROMPT_VERSION: 2
- aadhi/pipeline/prompts/scene_simulation.md (44): Manim simulation writer (template params or free-form AadhiScene code). || PROMPT_VERSION: 2
- aadhi/pipeline/prompts/scene_ai_video.md (28): AI footage scene writer. || PROMPT_VERSION: 2
- aadhi/pipeline/prompts/scene_interactive.md (23): p5.js interactive scene writer. || PROMPT_VERSION: 2
- aadhi/pipeline/prompts/practice.md (23): Companion-sheet practice problems. || PROMPT_VERSION: 2
- aadhi/pipeline/prompts/translate.md (21): Field translation with ⟦n⟧ placeholders. || PROMPT_VERSION: 1
- aadhi/pipeline/prompts/quality_terms.md (18): Optional AI terminology assistant. || PROMPT_VERSION: 1
- aadhi/pipeline/prompts/library_describe.md (23): Media library item description. || PROMPT_VERSION: 1
- aadhi/pipeline/prompting.py (97): Prompt file loader (PROMPT_VERSION header), prompt_versions, section/json_section builders and extract_json parser used by fakes. || PROMPTS_DIR:22; _VERSION_RE:23; PromptFile:27; load_prompt:33 (lru_cache, name [a-z0-9_]+); prompt_versions:44 (default: every .md in dir); system_prompt:51; language_label:56; section:63; json_section:69; extract_json:75; join_sections:91.
- aadhi/pipeline/assets.py (1713): Stage 6: AssetManifest builder: per-beat TTS (content-addressed), joined scene narration + envelope, Manim renders timed to beat offsets, images/AI video via provider chains with fallbacks and paid-video checkpoints, figures, GIFs, overrides, library visuals; incremental reuse via scene_hash. || PANEL_IMAGE_ASPECT='4:3':135; SCENE_IMAGE_ASPECT='16:9':136; SCENE_PARALLEL=4:138; _PASSTHROUGH:139; CONFIRM_PAID_KEY:152; relevant_lexicon:207; _HASH_EXCLUDE:215 (notes,intent,hidden,min_seconds,look, beats/reveal_beats overlays); scene_hash:221; stale_scene_ids:244; carry_visual_variant:256; VoiceConfig:293; voice_config:301; AssetBuildResult:312; _Builder:341 (_tts_provider:373, _media_providers:406, _wanted_video_ids:516, speech:525, clip_key:528, _reusable:534, tts_clip:558, scene_audio:575 (scene_audio key 586-589), loudness_envelope:627, _full_image_prompt:680, image:776, _produce_image:854, manim:890, _generate_video:994, _checkpointed_video:1150, _submit_video:1208, ai_video:1239, side_panel:1404, scene_media:1450, build_scene:1515, run:1533); _backup_provider:1681; build_assets_detailed:1691; build_assets:1704.
- aadhi/pipeline/audio.py (235): Decode beat clips (ffmpeg), trim silence (RMS), join with fixed gaps/pauses/quiz countdown, encode MP3 with LAME header; sample-exact beat offsets and word timings. || SAMPLE_RATE=24000:29; AUDIO_VERSION='2':35; DECODE_PARALLEL=4:36; AudioError:41; run_ffmpeg:45; decode_pcm:63; encode_mp3:75; measure_silence:95; estimate_words:116; map_words:130; ClipInput:167; SceneAudioResult:180; build_scene_audio:187 (BeatAudio built 219-223 without persona/words_exact).
- aadhi/pipeline/envelope.py (65): Narration loudness envelope (1 byte per 1/30 s, base64) for the live player's mascot motion. || ENVELOPE_FPS=30; FLOOR_DBFS=-50; CEIL_DBFS=-15; loudness_envelope; encode_envelope; decode_envelope; narration_envelope.
- aadhi/pipeline/integrations.py (328): Lazy, monkeypatchable seams to providers, job limits, manim, compose and usage pricing. || llm_engine:45; get_llm:51 (registers fake responders for fake engine); llm_model:66 (tiers plan|script|critic|fast; override only for same engine); get_tts:80; get_image/get_video/get_gif:85-97; limit:103; apply_lexicon:120; normalize_for_speech:137; speech_text:149; default_voice:154; list_templates:183; get_template:195; validate_template_params:213; template_step_count:230; check_manim_code:271; manim_spec_problems:280; render_manim_fn:295; build_timeline_fn:307; estimate_cost:319.
- aadhi/pipeline/chunking.py (301): Markdown -> SourceChunks (headings/pages/tables/code respected), language detection, unsupported-script warning, pseudo-heading heuristic, truncation. || TARGET_CHARS=1200; MAX_CHARS=1900; PAGE_MARKER_RE:16; _blocks:33; _split_code:89; _split_long:114 (table rows cut to limit-200:127); chunk_markdown:148 (ids c0001.., text[:2000]); detect_language:197; unsupported_script:240; looks_like_heading:268; truncate_markdown:289; page_count_from_markdown:299.
- aadhi/pipeline/codeblocks.py (285): Fenced code handling, linear-time code detection and language hints. || CODE_SIGNALS:41; fence_open:71; closes_fence:82; fenced_segments:88; outside_fences:111; strip_fenced:120; looks_like_code:127; dense_code:146; escape_fence:158; tilde_divider:164; TILDE_SCAN_BUDGET=400_000:175; text_fences:194; code_language:265; fence_block:275; dedent_lines:282.
- aadhi/pipeline/companion.py (364): Companion sheet: derived formulas/definitions/misconceptions + LLM practice problems (skipping brief-skipped chunks); Markdown and escaped printable HTML rendering. || MIN_PROBLEMS=3, MAX_PROBLEMS=8:39; derive_formulas:51; derive_definitions:74; derive_misconceptions:92; practice_prompt:112; practice_validator:138; to_practice:175; generate_practice:188 (re-raises JobCancelled/BudgetExceeded/RateLimited, else [] + warning); build_sheet:213; render_markdown:243; render_html:308.
- aadhi/pipeline/critic.py (339): Deterministic grounding checks + per-chapter LLM judge with verbatim-quote verification; flow tag -> issue code; optional terminology assist. || CRITIC_PROMPTS:39; CHAPTER_SOURCE_CHARS=24_000; _CODE_BY_CATEGORY:41; FLOW_CODES:50; OUTLINE_MAX=120; critic_model:64; scene_text:73; render_scene_for_review:88; quoted_passages:126; deterministic_issues:157; finding_code:177; verify_findings:189; chapter_groups:222; lecture_outline:238; scene_before:248; build_critic_prompt:259; critique_chapter:286 (temperature 0.2; failure -> critic.unavailable info); critique:312.
- aadhi/pipeline/docx_extract.py (275): DOCX -> Markdown via mammoth HTML + bs4 walker, zip-bomb limits, embedded images, code styles as fences. || MAX_UNCOMPRESSED=250MB; MAX_ENTRIES=10_000; MAX_RATIO=200; MAX_IMAGES=150; CODE_STYLE_MAP:28; DocxRejected:33; check_zip:55; _normalise_image:76; _Converter:94; html_to_markdown:202; docx_to_markdown:256.
- aadhi/pipeline/aio.py (21): gather_all: gather that cancels and reaps siblings on failure. || gather_all:16
- aadhi/pipeline/__init__.py (4): Package docstring only. || -
- aadhi/pipeline/pdf_layout.py (412): Not in slice A (part B); not read. || -
- aadhi/pipeline/pdf_worker.py (206): Not in slice A (part B); not read. || -
- aadhi/pipeline/presenter_lint.py (39): Not in slice A (part B); not read. || -
- aadhi/pipeline/preview.py (698): Not in slice A (part B); not read. || -
- aadhi/pipeline/quality/ (11 files: __init__ 186, abbreviations 344, assist 147, code 125, extract 239, formulas 202, pacing 88, report 40, terms 227, text 244, titles 82) (1924): Not in slice A (part B); not read. || -
- aadhi/pipeline/repair.py (204): Not in slice A (part B); grepped only. || carry_over:52 (keeps look / v3 role; calls assets.carry_visual_variant:92); repair:200
- aadhi/pipeline/richlite.py (268): Not in slice A (part B); not read. || -
- aadhi/pipeline/scene_checks.py (168): Not in slice A (part B); not read. || -
- aadhi/pipeline/scene_context.py (313): Not in slice A (part B); not read. || -
- aadhi/pipeline/script.py (482): Not in slice A (part B); grepped only. || ScriptResult:117; write_scenes_detailed:410; write_scenes:479
- aadhi/pipeline/source_review.py (1441): Not in slice A (part B); grepped only. || REVIEW_STAGE_KEY='review_stage':64; REVIEW_STAGE_SOURCE='source':65; RESUME_STAGE='source_review':66; MAX_EXCLUDED=500:71; overrides_for:1122; effective_brief:1184; without_chunks:1252
- aadhi/pipeline/sync_lint.py (49): Not in slice A (part B); not read. || -
- aadhi/pipeline/textnorm.py (97): Not in slice A (part B); not read. || -
- aadhi/pipeline/timing.py (55): Not in slice A (part B); not read. || -
- aadhi/pipeline/translate.py (336): Not in slice A (part B); not read. || -
- aadhi/pipeline/validate.py (955): Not in slice A (part B); not read. || -