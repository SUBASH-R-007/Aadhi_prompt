# Inter-module interface table (v2)

Public functions each module MUST export with exactly these names/signatures so modules written in
parallel fit together. Contract files (`*/base.py`, `schemas/*`, `models.py`, `config.py`, `db.py`,
`storage/{base,local,assets,__init__}.py`, `web/js/shared/*`) are authoritative for types.

## aadhi.auth (owner: core)
```python
# passwords.py
hash_password(password: str) -> str                                   # bcrypt
verify_password(password: str, password_hash: str) -> bool            # accepts v1 passlib $2b$ hashes; constant-time on unknown users via dummy hash
check_password_strength(password: str, username: str, settings: Settings) -> list[str]   # [] = ok
# tokens.py
class InvalidToken(Exception)
create_session_token(user: User, settings: Settings) -> str           # {sub: str(id), tv, typ:"session", iat, exp, jti}; HS256
decode_session_token(token: str, settings: Settings) -> dict          # rejects typ != session; raises InvalidToken
create_scoped_token(scope: str, claims: dict, ttl_seconds: int, settings: Settings | None = None) -> str   # typ:"scoped", HMAC key derived from JWT_SECRET + scope
decode_scoped_token(token: str, scope: str, settings: Settings | None = None) -> dict
# cookies.py
set_session_cookie(response: Response, token: str, settings: Settings) -> None   # name = settings.cookie_name
clear_session_cookie(response: Response, settings: Settings) -> None
# deps.py (FastAPI dependencies; DB session via aadhi.db.DbSession)
get_current_user(...) -> User                 # 401 unauthenticated; 403 password_change_required when flagged
get_current_user_allow_pending(...) -> User   # same but allows must_change_password (me, change-password, logout)
get_optional_user(...) -> User | None
require_roles(*roles: str) -> Callable        # dependency factory -> User, 403 forbidden
# bootstrap.py
ensure_admin(db: Session, settings: Settings) -> None   # env ADMIN_PASSWORD or generated one-time password written to data_dir/initial_admin_password.txt (0600, path logged, never the password); production requires env
```

## aadhi.security (owner: core)
```python
# headers.py
build_csp(settings: Settings, kind: Literal["app", "sandbox", "media", "companion"] = "app") -> str
class SecurityHeadersMiddleware   # pure ASGI; app.add_middleware(SecurityHeadersMiddleware, settings=settings)
# csrf.py
class CSRFMiddleware              # app.add_middleware(CSRFMiddleware, settings=settings, exempt_paths=("/api/auth/login",))
# bodylimit.py
class BodySizeLimitMiddleware     # app.add_middleware(BodySizeLimitMiddleware, settings=settings) — JSON cap + upload cap, streamed
# client_ip.py
client_ip(request: Request, settings: Settings) -> str              # honours X-Forwarded-For only from TRUSTED_PROXIES
# ratelimit.py
rate_limit(bucket: str, *, per_minute: int | None = None, per_hour: int | None = None, key: Literal["user", "ip", "user_ip"] = "user_ip") -> Callable   # dependency; 429 rate_limited + Retry-After
check_rate_limit(bucket: str, key: str, *, per_minute=None, per_hour=None) -> None   # imperative form (login: username+ip)
reset_rate_limits() -> None
# uploads.py
@dataclass UploadInfo: mime: str; ext: str; kind: str   # image|video|audio|pdf|docx|json|text|markdown
class UploadRejected(Exception): status_code: int; code: str; message: str
validate_upload(filename: str, data: bytes, *, allowed_kinds: set[str], max_bytes: int) -> UploadInfo
safe_display_name(filename: str) -> str
# credentials_crypto.py — Fernet; key = CREDENTIALS_ENCRYPTION_KEY or HKDF-SHA256(resolved JWT secret, info=b"aadhi-api-credentials-v1")
class CredentialUnreadable(Exception)                               # wrong key (rotated JWT_SECRET / encryption key) or tampered token
encrypt_secret(plaintext: str, settings: Settings) -> str
decrypt_secret(token: str, settings: Settings) -> str               # raises CredentialUnreadable
# redaction.py — process-wide registry consulted by Settings.secret_values()/redact() of EVERY Settings instance
#   and by the logging RedactingFilter (aadhi/logging_setup.py) on every record
register_secret(value: str | None, owner: str | None = None) -> None
#   every decrypted or newly saved stored key is registered under its owner slot ("server:<provider>" /
#   "user:<id>:<provider>"): a slot holds one value (replaced on re-registration, never evicted); without an owner the
#   value joins a bounded LRU (MAX_REGISTERED)
forget_secret(owner: str) -> None                                   # a deleted key's slot (delete_credential)
registered_secrets() -> tuple[str, ...]
clear_registered_secrets() -> None                                  # tests
```

## aadhi.credentials (owner: core)
```python
KEY_PROVIDERS = ("gemini", "openai", "anthropic")
@dataclass ResolvedKeys: settings: Settings; sources: dict[str, str | None]   # provider -> "personal" | "server" | "env" | None
resolve_keys(db: Session, settings: Settings, user_id: int | None) -> ResolvedKeys
#   precedence per provider: personal (USER_API_KEYS_ENABLED) > server key saved in the Studio > .env; saved keys only when
#   STORED_API_KEYS_ENABLED. Returns the SAME settings object when no saved key applies (provider caches are keyed by it),
#   else a cached settings.model_copy(update=...) replacing gemini_api_key (+ gemini_api_keys=[]), openai_api_key,
#   anthropic_api_key; cache keyed by (id(base settings), user_id, fingerprint of the relevant rows), LRU-bounded, holds a
#   reference to the base settings. Unreadable ciphertext is skipped (logged once, redacted).
save_credential(db: Session, *, provider: str, api_key: str, scope: Literal["user", "server"], user: User,
                settings: Settings | None = None) -> ApiCredential
#   strips; 16..512 chars of [A-Za-z0-9_-] (CredentialError -> the API answers 422);
#   creates or replaces (concurrent saves converge on one row); clears last_verified_at / last_error; flushed, caller commits
delete_credential(db: Session, *, provider: str, scope: Literal["user", "server"], user: User) -> bool
get_credential(db: Session, *, provider: str, scope: Literal["user", "server"], user: User) -> ApiCredential | None
credential_view(row: ApiCredential, settings: Settings | None = None) -> dict
#   {"hint","updated_at","last_verified_at","last_error","readable"}; NEVER plaintext or ciphertext
server_key(db: Session, settings: Settings, provider: str) -> tuple[str, str, ApiCredential | None] | None
#   the active server key: ("stored", key, row) when saved in the Studio and readable, else ("env", key, None), else None
record_verification(db: Session, row_id: int, ok: bool, message: str) -> None   # ok: last_verified_at=now, last_error=None; else last_error
async verify_key(provider: str, api_key: str, settings: Settings) -> tuple[bool, str]
#   one authenticated list-models call with the official SDK (AsyncAnthropic / AsyncOpenAI models.list; google-genai
#   client.aio.models.list), short timeout, no retries, redacted message; tests always monkeypatch it
billed_to(sources: Mapping[str, str | None], usage_provider: str) -> str
#   "user" when the provider whose key pays for usage_provider ("veo" -> "gemini") is "personal" in sources, else "server"
personal_key_rejection(exc: BaseException, sources: Mapping[str, str | None]) -> str | None
#   job error message when exc (or an exception it wraps) is a ProviderError 401/403 (or Gemini's 400 API_KEY_INVALID /
#   "API key expired") on a provider whose key is "personal" (the worker then fails the job with error_code
#   "personal_key_rejected", without retry or fallback). Scene writing, translation and asset building re-raise such an
#   error instead of degrading it (fallback scene / kept source text / skipped medium).
personal_keys_enabled(settings: Settings) -> bool   # stored_api_keys_enabled and user_api_keys_enabled
key_hint(api_key: str) -> str                       # "<vendor prefix>…<last 4>"
```

## aadhi.usage (owner: core)
```python
# pricing.py
estimate_cost(usage: Usage, settings: Settings | None = None) -> float
# service.py
record_usage(db: Session, usage: Usage, *, user_id, project_id, job_id, cost_usd: float, billed_to: str = "server") -> UsageEvent
spent_today(db: Session, user_id: int) -> float                      # only billed_to == "server" (what the daily budget counts)
user_daily_budget(user: User | None, settings: Settings) -> float
check_budget(db: Session, *, user_id: int | None, job_cost_so_far: float, extra_usd: float, settings: Settings, job_budget_usd: float | None = None) -> None   # raises aadhi.jobs.base.BudgetExceeded
usage_for_user(db: Session, user: User, days: int, settings: Settings) -> dict
#   + "own_key_usd" (billed_to == "user", same period as total_usd); "today_usd" = spent_today (server-billed only)
usage_for_project(db: Session, project_id: int) -> dict
usage_admin(db: Session, days: int) -> dict                          # + "own_key_usd" per user and in total
```

## aadhi.legacy (owner: core)
```python
is_legacy(data: Any) -> bool
convert_legacy(data: dict | list) -> tuple[Screenplay, list[str]]       # (screenplay, warnings)
#   mascot placement: case-insensitive; aliases popup -> popup_bottom_left, none -> hidden; unknown values warn and use
#   left. A v1 scene without a position follows v1's rule: title/content scenes with <= 300 characters of HTML show
#   Aadhi on the left, anything else hides him; force_background overrides; AI video scenes stay left.
import_legacy_db(db: Session, path: Path, settings: Settings, *, owner_fallback: str = "admin",
                 on_progress=None, latest_only: bool = False) -> dict
#   latest_only (CLI import-legacy --latest-only): only the newest row (highest id) per lesson (owner, subject, unit,
#   session number, session title; case and outer spaces ignored; a row with none of them is its own lesson);
#   result["projects_superseded"] lists the others. legacy_db.latest_rows(rows) -> (rows to import, superseded ids)
#   Lessons saved by the friend's fork of v1: scene.edit.hidden -> Scene.hidden, scene.edit.min_seconds -> min_seconds
#   (clamped 1..600); muted narration, captions off, their asset ids / "asset:" images and plan / style / Studio /
#   editor keys -> one warning each
```

## aadhi.jobs (owner: jobs)
```python
# queue.py
class JobInProgress(Exception): job_id: int | None
enqueue(db: Session, kind: str, *, user_id=None, project_id=None, version_id=None, payload=None,
        priority: int = 100, max_attempts: int | None = None) -> Job        # flush; caller commits; raises JobInProgress (partial unique index)
request_cancel(db: Session, job_id: int) -> Job
retry_job(db: Session, job_id: int, *, user_id: int | None) -> Job
job_summary(job: Job) -> dict
active_job_for_version(db: Session, version_id: int) -> Job | None
active_job_for_project(db: Session, project_id: int) -> Job | None
complete_review(db: Session, job_id: int) -> Job                       # awaiting_review -> succeeded (API before enqueuing continuation)
# events.py
event_dict(ev: JobEvent) -> dict
list_events(db: Session, job_id: int, after_id: int = 0, limit: int = 200) -> list[dict]
stream_job(job_id: int, *, last_event_id: int = 0, user_id: int) -> AsyncIterator[str]   # SSE chunks; short sessions per poll
# inline.py
start_inline_workers(settings: Settings) -> Any ; stop_inline_workers(handle: Any) -> None
# limits.py
acquire(name: Literal["llm", "tts", "manim", "render", "image", "video"]) -> AsyncContextManager   # process-wide, any loop/thread
# context.py
class DBJobContext  # implements aadhi.jobs.base.JobContext
#   open(): ctx.settings = credentials.resolve_keys(db, settings, job.user_id).settings; ctx.key_sources = its sources;
#   each usage row gets billed_to = credentials.billed_to(ctx.key_sources, usage.provider); the daily-budget check counts
#   only "server" rows and only a paid server-billed increment can trip it (own-key and free usage pass even when the
#   user is already over), the per-job budget (max_cost_per_lecture_usd) counts all;
#   ensure_budget(estimated_cost_usd, provider=None): an estimate whose provider is paid by a personal key (e.g. "veo"
#   with a personal Gemini key) is checked against the job budget only; events are redacted with ctx.settings.redact
# cleanup.py — registers "cleanup"; run_cleanup() also deletes asset_claims expired > 1 h ("asset_claims" count) and,
#   outside a dry run, this host's work files (manim.sandbox.sweep_orphans, compose.video.sweep_render_workspaces)
# worker.py — Worker(..., host_housekeeping=False): True (python -m aadhi.worker, inline mode) logs render capability at
#   start (render_video workers) and runs sweep_host_once() at start and hourly; handlers run under
#   storage.assets.claim_owner(ctx.claim_owner()) so their generation claims name the job's lease
# queue.py — settle_render(db, job) marks a Render row whose render_video job ended outside the handler
```

## aadhi.storage.assets (owner: storage)
```python
normalize_prompt(text: str | None) -> str                       # NFC, whitespace runs -> one space, trimmed
media_key(kind, prompt, *, provider, model, aspect, params=None) -> str   # == compute_key(kind, {prompt, provider, model,
#   aspect}) for a normalised prompt and no params (cached media keeps its key)
AssetStore.first_existing(keys: Iterable[str], *, verify_blob: bool | None = None) -> Asset | None   # one query, in order
AssetStore.get_or_create(key, kind, producer, *, ..., guarded: bool = True)   # paid kinds (GENERATION_CLAIM_KINDS) take an
#   asset_claims row across processes; guarded=False skips the generation_guard (collecting already-billed work)
claim_owner(owner) / generation_guard(guard) -> context managers (contextvars, inherited by tasks)
holds_claim(key) -> bool ; record_claim_operation(key, operation: dict) -> bool   # the holder's Veo record on its claim row
inherited_operation(key) -> dict | None   # the record a takeover of a dead claim moved to this process (one atomic UPDATE)
```

## aadhi.pipeline (owner: pipeline)
```python
integrations.llm_engine(options: GenerationOptions | None, settings) -> str      # options.llm_provider or settings.llm_provider
integrations.get_llm(settings, engine: str | None = None) -> LLMProvider        # factory.get_llm + fake responder registration
integrations.llm_model(settings, tier: str, options: GenerationOptions | None = None, *, override: str | None = None) -> str
#   override (admin allow-listed model) wins only when engine == "fake" or engine_for_model(override) == engine;
#   else llm_models(settings, llm_engine(options, settings))[tier]
validate.lint(screenplay, options=None, *, chunk_ids=None, ingest: IngestResult | None = None,
              quality=quality.COMPUTE) -> list[Issue]
#   lint_scene also runs sync_lint.lint_sync(scene) and presenter_lint.lint_mascot_layout(scene); lint appends
#   quality.lint_quality(screenplay, quality) (quality: an analysis_of(screenplay) to share with quality_report;
#   None = the analysis failed, the quality family is skipped; the default computes it)
#   hidden scenes: lecture.all_scenes_hidden (warning), chapter.all_scenes_hidden / scene.hidden (info); a hidden
#   scene's own findings are downgraded to info; coverage / quiz spacing / length / duplicate intro / pacing runs /
#   abbreviation first use count shown scenes
validate.hidden_ids(screenplay_or_document) -> frozenset[str]
validate.hidden_as_notes(issues, hidden) -> list   # issues (dicts or Issue) of hidden scenes as info copies; served
#   and counted that way (PUT /screenplay, revert, version detail, orchestrator issue_counts), stored as they are
quality.analysis_of(screenplay) -> Analysis | None   # one analysis for lint_quality and quality_report (POST /lint)
quality.lint_quality(screenplay, analysis=<computed>) -> list[Issue]
quality.quality_report(screenplay, analysis=<computed>) -> {"repairs": [...], "registry": {...}}
quality.QUALITY_CODES / quality.AUTHOR_CODES (never handed to repair.rewrite_scene)
quality.assist.terminology_assist(ctx, screenplay, options=None) -> list[Issue]   # QUALITY_AI_TERMINOLOGY; one fast-model call
source_review.analyze(ingest) -> Analysis ; source_review.build_report(ingest, *, analysis=None, brief=None, overrides=None,
#   screenplay=None, filename="") -> SourceReport   # pure; values source scoping removed are masked
source_review.SourceOverrides(ingest_key, excluded_chunk_ids, restored_chunk_ids, concept_names, updated_at)
source_review.override_problems(ov, ingest, brief) -> list[dict] ; overrides_for(meta, ingest_key) -> SourceOverrides | None
source_review.effective_brief(brief, ingest, ov) -> ConceptBrief ; source_review.without_chunks(ingest, ids) -> IngestResult
#   REVIEW_STAGE_KEY = "review_stage" (generation_meta, "source" while paused), OVERRIDES_KEY = "source_overrides",
#   RESUME_STAGE = "source_review" (AwaitingReview state["stage"]); generate_lecture payload review_source: true pauses there
source_review.REVIEW_VERSION = "3" ; source_review.inline_formulas(line) -> [(formula, symbols)]   # formulas inside
#   a sentence: an explicit operator or exponent and at least one short symbol; never code, URLs or tables
envelope.narration_envelope(data, mime, *, ffmpeg="ffmpeg", fps=30) -> str | None   # base64, one byte per frame
source_scope.scope_source(markdown, *, figures=()) -> ScopeResult   # metadata / excluded / visual notes
brief.build_brief(ctx, ingest, options) -> ConceptBrief ; brief.build_brief_prompt(ingest, options) -> (system, user)
plan.generate_plan(ctx, ingest, options, meta=None, *, brief: ConceptBrief | None = None) -> PlanResult
preview  # python -m aadhi.pipeline.preview SOURCE [--options] [--out] [--stages] [--scene] [--engine]: exact prompts, no model call
critic.critic_model(settings, options=None) -> str             # critic tier of the lecture's engine
translate.translate_model(options, settings) -> str            # script tier of the lecture's engine (admin llm_model_script override wins)
companion.render_markdown(screenplay: Screenplay) -> str
companion.render_html(screenplay: Screenplay) -> str          # self-contained, escaped, uses /web/vendor/mathjax via renderTex-free static TeX (KaTeX-less: TeX shown in <code>) or MathJax script from /web/vendor
assets.scene_hash(scene) -> str                                # content hash used for staleness
assets.build_assets(ctx, screenplay, options, *, previous: AssetManifest | None = None, scene_ids: list[str] | None = None) -> AssetManifest
#   image / video: provider chain (IMAGE_PROVIDER + IMAGE_FALLBACK_PROVIDERS, options.image_provider first), earlier
#   results reused when generation is off on the server (assets.cached_media_used)
#   SidePanel.variant / AIVideoScene.variant > 0 add "variant" to the image / clip key (also an AI video's still);
#   at 0 every key is unchanged. job payload CONFIRM_PAID_KEY = "confirm_paid_scene_ids": scenes whose ambiguous paid
#   clip of an EARLIER job may be submitted again (once; jobs.queue.ONE_JOB_PAYLOAD_KEYS are dropped on retry).
#   Issue codes assets.override_missing (warning), assets.library_visual_used (info). A new version that cannot be
#   made keeps the latest stored earlier one (warning NEW_VERSION_UNAVAILABLE when generation is off on the server).
#   Libraries (prefer_library_visuals, LIBRARY_AUTO_SAVE_GENERATED) only when the job's user owns the lecture.
assets.carry_visual_variant(old_scene, new_scene) -> Scene   # keeps variant when the request is unchanged (repair.carry_over)
#   build_assets never builds a hidden scene (not even when scene_ids names it) and never lists it stale; scene_hash
#   ignores hidden and min_seconds (both kept by repair on regeneration). orchestrator: generate_lecture / translate
#   store changes.store_snapshot(...) with the generated screenplay (same compare-and-set) ->
#   generation_meta["generated_snapshot_key"]
base.GenerationOptions.image_provider: Literal["gemini", "pollinations"] | None = None   # None = the server's chain
base.GenerationOptions.prefer_library_visuals: bool = False   # left out of the JSON while False; side-panel images from
#   the lecture owner's library: library.auto_match on the image prompt, one plan per build in scene order (one
#   picture per scene; never media generated for this lecture or the scene's own picture; never for a variant > 0)
base.Issue.data: IssueData | None   # {"category": ...}; omitted from JSON when None
ingest.INGEST_VERSION = "8" ; ingest.load_version_ingest(ctx, ingest_key) -> IngestResult | None   # a version's own extract
pdf_worker result.json lines set in a monospaced font carry "mono": true and "lead" (leading spaces); missing = not code
checkpoint.StageCheckpoints(ctx) -> per-stage load/store of finished LLM stages (loaded only on attempt > 1 with a job id)
checkpoint.SceneCheckpoints(stages, stage_inputs) -> load(scene_id, attached) / save(...) per finished scene
#   (script.write_scenes_detailed(..., checkpoints=...)); reused scenes show once as "scenes" in reused_stages
plan_state.load_plan(version: ProjectVersion) -> LecturePlan | None ; plan_state.save_plan(version, plan) -> None
gen_models  # LLM response models; canonicalize.canonicalize_scene(planned, out, ...) -> Scene
orchestrator  # registers generate_lecture, regenerate_scene, build_assets, translate
fake_content.register_fake_responders() -> None               # realistic FakeLLM responses for dev/e2e
```

## aadhi.manim (owner: manim)
```python
templates.registry: dict[str, Template]
templates.list_templates() -> list[TemplateInfo]
templates.get_template(name: str) -> Template                  # KeyError
templates.validate_params(name: str, params: dict) -> tuple[BaseModel | None, list[str]]
validate_spec(spec: ManimSpec, n_beats: int | None = None) -> list[str]     # in aadhi/manim/__init__.py: params/guard/step-count problems
guard.check_code(code: str) -> list[str]
render.render_manim(ctx: JobContext, req: ManimRenderRequest) -> Awaitable[ManimRenderResult]   # raises ManimError
base.ManimRenderRequest.llm_provider: str | None = None        # lecture's AI engine for code repair / visual QA (not in the cache key)
base.ManimError(message, log_tail="", category="render_failed") ; .category in base.FAILURE_CATEGORIES ; .friendly -> str
sandbox.sweep_orphans(settings, now=None) -> {"temp_dirs": int, "containers": int}   # idempotent, this host only
health.sandbox_health(settings) -> SandboxHealth   # .as_dict(): booleans / short strings, never host paths
llm.get_llm(settings, engine=None) -> LLMProvider ; llm.fast_model(settings, engine=None) -> str   # that engine's fast tier
```

## aadhi.compose (owner: compose)
```python
timeline.build_timeline(screenplay, manifest, *, settings, version_id=None, revision=None,
                        include_intro=True, branding=None) -> Timeline
timeline.preview_timeline(screenplay, manifest | None, *, settings, version_id=None) -> Timeline   # estimated
#   both skip Scene.hidden (TimedScene.index counts shown scenes; captions / chapters / keys leave hidden ones out);
#   Scene.min_seconds pads the scene's end: TimedScene.hold_seconds (omitted when 0), nothing else moves
timeline.resolve_urls(timeline: Timeline, store: AssetStore, *, signed: bool = False) -> Timeline
#   also maps retired mascot clips (base.RETIRED_MASCOT_CLIPS) to their replacement (timeline.current_clip_url)
#   and the retired intro logo (base.RETIRED_BRANDING_FILES, timeline.current_branding_url)
timeline.default_branding(settings: Settings) -> Branding
sync.SyncOptions.from_settings(settings) -> SyncOptions | None   # None with SYNC_WORD_ANCHORS=false
sync.scene_sync_cues(beats, board, side_panel, duration, options) -> list[SyncCue]   # pure, sorted by start
captions.to_srt(cues) -> str ; captions.to_vtt(cues) -> str ; captions.split_caption(text, words) -> list[CaptionCue]
chapters.youtube_chapters(timeline: Timeline) -> str ; chapters.opening_title(taken) -> str   # never a duplicate "Introduction"
video  # registers "render_video"; video.sweep_render_workspaces(settings, session) -> int
preflight.render_preflight(timeline, issues=None, hidden=frozenset()) -> list[{scene_index, scene_id, title, reason, blocking, message}]
preflight.quality_items(issues, timeline=None, hidden=frozenset()) -> list[{..., reason: "quality", blocking: False, severity, code}]   # <= 12 (+1 "more")
#   hidden: ids of scenes skipped in the video, whose issues are never listed; the API adds scene_number (the
#   screenplay place, api/routers/renders.numbered); video.render_warnings(timeline, capture=None, numbers=None) too
#   within a severity, lecture-wide and scene findings alternate (scene findings are not pushed into "N more")
capabilities.render_capabilities(settings, *, browser=True, force=False) -> RenderCapabilities   # cached, blocking
qa.inspect_render(path, *, settings, fps, expected_frames, ...) -> RenderQA (Render.options["qa"]) ; workspace.RenderWorkspace, sweep_stale(settings, statuses)
```

## aadhi.providers (owner: providers)
```python
factory.LLM_ENGINES = ("gemini", "openai", "anthropic")          # user-selectable engines ("fake": tests / LLM_PROVIDER=fake only)
factory.LLM_ENGINE_LABELS: dict[str, str]                        # gemini "Google Gemini", openai "OpenAI", anthropic "Anthropic Claude", fake "Offline test engine"
factory.LLM_TIERS = ("plan", "script", "critic", "fast")
factory.engine_for_model(model: str) -> str | None               # gemini* -> gemini; gpt*/o<digit>*/chatgpt*/ft:gpt* (any case) -> openai; claude* -> anthropic; else None
factory.llm_configured(settings, engine: str | None = None) -> bool   # None = settings.llm_provider; anthropic needs ANTHROPIC_API_KEY; fake only where allowed
factory.llm_models(settings, engine: str | None = None) -> dict[str, str]   # {tier: model}; LLM_MODEL_<TIER> if engine_for_model(it) == engine, else gemini defaults / OPENAI_MODEL_<TIER> / ANTHROPIC_MODEL_<TIER>; fake: LLM_MODEL_<TIER>
factory.get_llm(settings=None, engine: str | None = None) -> LLMProvider   # None = settings.llm_provider; cached per settings + engine; fake refused (ProviderNotConfigured) unless allowed
factory.available_llm_engines(settings) -> list[dict]          # [{id,label,configured,models}] for LLM_ENGINES; "fake" first only where allowed
factory.get_tts(name=None, settings=None) -> TTSProvider
factory.get_image(settings=None) ; factory.get_video(settings=None) ; factory.get_gif(settings=None)
factory.media_chain(kind, settings) -> list[str]                # IMAGE_/VIDEO_PROVIDER then *_FALLBACK_PROVIDERS ([] for none)
factory.media_configured(kind, settings) -> bool ; factory.media_unavailable_reason(kind, name, settings) -> str | None
factory.available_image_providers(settings) -> list[dict]      # [{id,label,configured,paid}] a lecture may choose; [] when none
factory.media_provider_status(settings, worker_cooldowns=None) -> dict  # admin panel: chains, state, redacted reason, paid, cooldown;
#   worker_cooldowns: {(kind, provider): (seconds_left, category)} read from job events (api.routers.meta.worker_cooldowns)
health.provider_health() -> cooldowns per (provider, key scope) ; operations.PAYLOAD_KEY = "provider_operations"
#   operation record status: pending (saved, nothing outstanding) | submitting (a request awaits its answer) | submitted
#   | completed | failed | abandoned | lost | ambiguous; JobCheckpoint.submitted / usage_recorded / sending / not_sent
#   VeoVideo.reports_sending = True; ResumableVideoProvider.resume(..., checkpoint=None) notes its charge
factory.available_tts_providers(settings) -> list[dict]       # [{id,label,configured,word_timings,voices}]
llm.anthropic.AnthropicLLM(settings, *, client_factory=None, sleep=None, max_attempts=4)   # name "anthropic"; LLMProvider; ProviderNotConfigured without ANTHROPIC_API_KEY (unless client_factory)
#   Usage(provider="anthropic", model=<served model>, operation "llm" | "vision", input_tokens = input + cache writes + cache reads,
#         output_tokens, meta={"cache_read_tokens", "cache_write_tokens", ...})
llm.schema.to_provider_schema(model: type[BaseModel], provider: str) -> dict ; llm.schema.assert_llm_compatible(model) -> None
llm.fake.FakeLLM.register(schema_name: str, responder: Callable[[str, type[BaseModel]], dict | BaseModel]) -> None
tts.voices.default_voice(provider: str, language: str) -> str ; tts.voices.voices_for(provider, language=None) -> list[dict]
tts.normalize.normalize_for_speech(text: str, language: str) -> str
tts.normalize.apply_lexicon(text: str, lexicon: list[LexiconEntry], language: str) -> str
```

## aadhi.library (owner: library)
```python
# Each user's own media library (model LibraryItem, migration 0005). Nothing is read from / written to Asset rows for
# ownership or words; items come only from the user's uploads or builds they started. The caller commits.
add_item(db, *, user_id, asset_key, kind: "image"|"video", source: "upload"|"generated"|"figure", title=None,
         description=None, keywords=None, origin_project_id=None, origin_scene_id=None, prompt=None, provider=None,
         model=None, used=False) -> LibraryItem   # idempotent on (user_id, asset_key); fills only empty fields
#   (never overwrites the teacher's words); ValueError unless the asset is a stored picture / clip
get_item(db, user_id, item_id) -> LibraryItem      # 404 ApiException "not_found" for another user's item (admins too)
update_item(db, item, *, title=, description=, keywords=) -> LibraryItem ; delete_item(db, item) -> None   # row only
list_items(db, user_id, *, q="", kind=None, source: str | Sequence[str] | None = None, limit=48, offset=0)
#   -> (rows [(LibraryItem, Asset)], total)
used_in(db, user_id, keys) -> {asset_key: count of the user's own live lectures it was added to}   # asset refs; GROUP BY
item_view(item, asset, store, uses=0) -> ItemView ; item_views(db, store, user_id, items) -> [ItemView]
match(db, user_id, text, kind: str | None, *, limit=10, threshold=0.0, matcher=None) -> [(LibraryItem, score 0..1)]
#   deterministic; matcher: one LibraryMatcher already loaded for the user (db then unused)
auto_match(db, user_id, prompt, kind="image", *, exclude=(), skip=None, matcher=None) -> (LibraryItem, score) | None
#   automatic use: the image prompt only, score >= AUTO_USE_THRESHOLD and coverage >= AUTO_USE_MIN_COVERAGE
LibraryMatcher.load(db, user_id, *, kinds=None, texts=()) -> LibraryMatcher
LibraryMatcher.rank(text, kinds=None, *, limit, threshold, max_candidates=None)   # most recent candidates first
visual_needs(screenplay) -> [VisualNeed(scene_id, kinds, text, overridden)]   # image side panels, AI video scenes
suggestions(db, user_id, screenplay, *, per_scene=3, threshold=SUGGESTION_THRESHOLD, include_overridden=False,
            scene_ids=None, shown=None) -> [(VisualNeed, [(LibraryItem, score)])]
#   <= MATCH_MAX_PAIRS item scores per call; scene_ids ranks only those (same results); shown {scene: key} left out
attach_to_project(db, item, project) -> asset_key  # records the AssetRef (authorises the key in the screenplay); 409
#   "asset_missing"; the caller authorised both (get_item for the actor, the actor's OWN project)
record_generated(db, *, user_id, asset_key, kind, project_id, scene_id, prompt, provider, model, title=None,
                 settings=None) -> LibraryItem | None   # LIBRARY_AUTO_SAVE_GENERATED; never raises (savepoint on PostgreSQL)
media_cache_stats(db, *, days=30) -> dict          # admin aggregates (GET /api/admin/media-cache)
describe_image(store, asset, settings) -> ImageInput | None ; async describe(llm, *, model, item, image, on_usage=None)
#   -> {"title","description","keywords"} (prompt library_describe.md; tidied and bounded, never saved)
SUGGESTION_THRESHOLD = 0.35 ; AUTO_USE_THRESHOLD = 0.6 ; AUTO_USE_MIN_COVERAGE = 0.5 ; MATCH_MAX_PAIRS = 20_000 ;
KINDS ; SOURCES ; clean_title / clean_description /
#   clean_keywords(value, *, strict=True) (strict: ValueError with a readable message -> 422)
```

## aadhi.review (owner: review)
```python
# Visual Review (model VisualReview, migration 0006). Reads the stored screenplay, manifest and issues only.
visual_slot(sp, scene) -> VisualSlot(kind, field "side_panel"|"main"|"poster"|None, spec, chosen_key, figure_key,
                                     variant, prompt)
fingerprint(slot) -> str   # sha256[:32] of {kind, spec (no narration/title/rationale), chosen or figure asset, variant}
load_context(db, version, sp, manifest, stale, user_id, *, generate=None) -> ReviewContext   # one load per request
#   generate: generation_available(options, keys.settings) -> frozenset {"image","video"} (None: not checked)
scene_view(ctx, index, scene, *, url_for, suggestions=0) -> SceneVisual (+ "accepts") ; summary(items) -> dict
can_new_version(scene, media=None, generate=None) / can_retry(slot, status, media) / settled(media) /
accepted_kinds(scene) / shown_keys(sp, manifest) -> {scene_id: asset_key shown}
set_review(db, version_id, scene_id, state, fp, user_id, note=UNSET) -> None   # one atomic upsert, not committed
suggestion_counts(db, user_id, sp, *, scene_ids=None, shown=None) -> {scene_id: n}   # library.suggestions; never raises
new_version_scene(scene, *, media=None, generate=None) / chosen_scene(scene, key, kind, *, title="") /
removed_scene(scene) -> Scene   # 409
#   "action_unavailable" / 422 for a kind that does not fit (check_pick)
write_screenplay(db, version, sp, *, revision, project, store, now) -> int   # = PUT /screenplay: compare-and-set,
#   409 revision_conflict / version_busy, re-lint and merge issues; executes, does not commit
copy_reviews(db, source_version_id, target_version_id) -> int   # duplicate_version; skips scenes the target has
ambiguous(version, scene_id) -> bool   # video.ambiguous_submission / video.operation_lost on that scene
```

## aadhi.changes / aadhi.stage (owner: studio backend)
```python
# changes.py: the screenplay as Aadhi wrote it (private AssetStore blob, kind "snapshot", never collected by the GC)
changes.SNAPSHOT_META_KEY = "generated_snapshot_key" ; changes.SNAPSHOT_KIND = "snapshot"
changes.store_snapshot(assets, document, *, created_by=None) -> str     # idempotent; blocking
changes.load_generated(store, meta) -> Screenplay | None               # cached per key (immutable blobs)
changes.changes(generated, current, meta) -> {"available", "scenes": [{scene_id, status, generated_index,
#   current_index, fields_changed, history}]}   # status unchanged | edited | added | moved | removed
changes.scene_detail(generated, current, meta, scene_id) -> dict | None
changes.history_entries(meta, scene_id) -> list[dict]                  # scene_history, NEWEST FIRST (index 0)
changes.target_scene(generated, meta, scene_id, to, history_index=None) -> dict   # RevertError
changes.reverted(current, generated, scene, position=None) -> Screenplay           # pure; RevertError("revert_invalid")
changes.RevertError(code, message)   # "nothing_to_revert" -> 404, "revert_invalid" -> 409
# stage.py: pure
stage.derive(StageFacts) -> (stage, {"action", "label", "href"})   # STAGES, ACTIONS
stage.matches_current(built_revision, revision, version_built_revision) -> bool
```

## aadhi.api (owner: api)
```python
errors.ApiException(status: int, code: str, detail: Any, **extra)   # + exception handlers (incl. RequestValidationError -> code "validation")
deps.load_version(db, user, vid, *, for_update: bool = False) -> ProjectVersion
deps.load_project(db, user, project_id) -> Project
generation.parse_options(raw: str | dict | None, *, defaults: dict | None = None) -> GenerationOptions   # 422 validation
generation.sanitize_options(options, user, settings) -> GenerationOptions   # drops model overrides unless admin + allow-listed + same engine (any when fake)
#   every endpoint taking GenerationOptions: 422 validation, type "engine_not_configured", loc ["options", "llm_provider"]
#   when the effective engine (chosen, else LLM_PROVIDER) is not configured (generation.ensure_engine_configured)
generation.ensure_version_engine(version, project, settings, keys=None) -> None   # 409 "engine_not_configured" (+ "engine")
#   when the version's stored engine has no key: scene regenerate, translate, approve-plan (generation.version_options /
#   version_engine read it); generation.ensure_engine_available(engine, settings, keys=None) is the same check for an
#   engine id (job retries of generation kinds; a translate retry checks its source version's engine)
#   Engine checks, /api/meta "configured"/"key_source" and the budget pre-check run on the REQUESTER's resolved keys
#   (credentials.resolve_keys(db, settings, user.id).settings);
generation.ensure_budget(db, user, settings, *, engine=None, keys=None, options=None) -> None   # 429 "budget"; skipped
#   only when the effective engine AND every paid voice / image / video provider of options (gemini/openai/elevenlabs
#   voices, Gemini images, Veo) run on "personal" keys; a Gemini image or Veo backup in the chain, or the lecture's
#   image_provider choice, counts as a payer
generation.ensure_image_provider_configured(options, settings, keys=None) -> None   # 422 at ["options", "image_provider"]
#   (type "provider_not_configured") when generated images are off or the chosen provider has no key for the requester
storable.StorableBody / storable.ensure_storable(document, loc)   # 422 for NaN/Infinity/lone surrogates in stored bodies
# API keys routes: /api/keys (the caller's own personal keys) + /api/admin/keys (server keys, admin only); write-only
#   (credential_view), audit log lines without values, test endpoints rate limited per user; 403 "api_keys_disabled"
#   when saving while switched off
generation.complete_options(options) -> dict   # every GenerationOptions field (also those left out of stored dumps);
#   project_default_options and /api/meta generation_defaults use it
# Library routes (routers/library.py): /api/library (+ /{id}, /{id}/attach, /{id}/describe, /suggestions) are the
#   caller's own items only (404 otherwise, admins included); attach and Visual Review library picks only into the
#   caller's OWN lectures; describe 403 "feature_disabled" unless LIBRARY_AI_DESCRIBE_ENABLED; /api/admin/media-cache
# Visual Review routes (routers/visual_review.py): GET/PUT /api/versions/{vid}/visual-review[/{scene_id}],
#   POST /api/versions/{vid}/scenes/{scene_id}/visual (load_version: owner or admin); a hidden scene gets no
#   new_version / retry / confirm_paid_retry (409 action_unavailable) and never counts as needing attention
# Changes routes (routers/changes.py): GET /api/versions/{vid}/changes[/{scene_id}], POST
#   /api/versions/{vid}/scenes/{scene_id}/revert (load_version; saved like PUT /screenplay)
# Videos route (routers/videos.py): GET /api/videos (the caller's own, not deleted projects only, admins included)
serializers.stage_fields(...) / version_stage(db, v) -> {"stage", "next_step"} ; review_checkpoints(db, v, sp) -> dict
serializers.render_summary(render, job, version=None)   # + matches_current when the version is given
timelines.media_url(store, settings, storage_key) -> str | None   # streamable URL of a public file (capability URL,
#   or presigned on S3 without a CDN); GET /api/videos and GET /api/versions/{vid}/renders preview_url
routers.renders.every_scene_hidden(version) -> bool   # POST .../render: 409 all_scenes_hidden
main.create_app(settings: Settings | None = None) -> FastAPI ; main.app
```

## Frontend modules
```js
// web/js/player/player.js (owner: player)
export class Player { constructor(root, opts); load(timeline); play(); pause(); seek(s); seekScene(i);
  on(evt, fn); destroy(); states(sceneIndex) /* [{t,key}] */; renderState({sceneIndex, t}) /* -> {state_key, media_rect, panel_media_rect, media_fit} */; renderIntro({t}) }
  // events include 'blocked' (narration play() refused without a user gesture; the player pauses with reason 'blocked')
// web/js/player/mascot.js (owner: player)
export class MascotController { setPosition(); setPlaying(); setLevel(); setVisible(); clipUrl(); loadRig(); tick(); getStatus();
  mountCue(root, position); setState(state, cue); destroy() }   // the cue bubble lives in the scene root (in render screenshots)
// web/js/player/mascot-state.js (owner: player) — pure: mascotStateAt(scene, plan, t, phase, beatIndex, activeItemId)
//   -> {state, cue}; mascotStateTimes(scene, plan); CUE_ANCHORS / cueRect(position) (stage px; none for popup / hidden)
// web/js/player/presenter-vocab.js — behaviour / expression / gesture tables, actingFor(wanted, CLIP_CAPABILITIES)
// web/js/player/audio.js — envelopeLevel(scene, sceneT) -> 0..1 | null (TimedScene.audio_envelope); needsSpeechMeter(scenes)
//   speech level priority: build-time envelope > WebAudio meter > synthetic bob while speaking
// web/js/player/syncSummary.js — syncSummary(scene) -> {source, timing, beats[{beatId, start, when, estimated}], moments,
//   keyMoments}   // read-only words for the Studio preview ("Timing of scene N")
export function posterUrl(clipUrl) -> string | null   // /branding/<name>.mp4 -> /branding/posters/<name>.jpg
export const MASCOT_TIMING                             // crossfade, swap timeout, retry delays, watchdog, stall, play attempts
// web/js/player/schedule.js (owner: player) — pure, unit-tested
export function sceneStateAt(scene, t) -> { phase:'lead'|'beat'|'pause'|'countdown'|'reveal'|'tail', beatIndex, visibleItemIds:Set, filledItemIds:Set,
  highlightItemIds:Set, activeItemId, caption, countdownRemaining, quizRevealed, panelVisible, terminalLines,
  mascotState, mascotCue /* 'dots'|'question'|'success'|null */, pendingParts:Set /* legend rows waiting to be named */,
  emphasisParts:Set /* "item|column:n" / "item|term" */, panelFocus:boolean }
export function stateTimes(scene) -> number[]                 // where sceneStateAt output changes (render mode; incl. sync cues and
                                                              // thinking starts)
export function stateKey(state) -> string                     // adds m: / vp: / e: / pf: parts only when present (unchanged keys otherwise)
export function terminalLineTimes(lineCount, showAt, duration, outputAt = null) -> number[]   // = panels/timing.terminalLineTimes
//   (argument order there: showAt, duration, lineCount, outputAt); outputAt = the "output" sync cue; panelFocus puts
//   is-focus on the panel card
// web/js/player/richtext.js (owner: player)
export function renderRich(text) -> DocumentFragment          // rich-lite -> safe DOM; math spans rendered via renderTex
// web/js/player/panels/index.js (owner: panels)
export function createPanel(container, resolvedSidePanel, ctx) -> { el, update(t, sceneState), mediaRect?(), ready?(): Promise, destroy() }
//   ctx = { mode, timeline, sceneIndex, conceptState: {doneIds:Set, activeId} }
// web/js/shared/libs.js (owner: lead) — loadLib, loadThree, loadPrism, loadMathJax, renderTex(latex, display), texIdle()
// web/js/studio/components/libraryPicker.js (owner: library UI) — stable exports used by the Library page, the editor
//   and the Visual Review:
export function openLibraryPicker({ app, kind /* 'image'|'video' */, projectId, title, sources }) -> Promise<ItemView | null>
//   with projectId the chosen item is attached first (POST /api/library/{id}/attach) and carries the returned asset_key;
//   sources (e.g. ['upload', 'figure']) limits the items offered; null when the dialog is closed
export function uploadToLibrary(file, details = {}, { signal, onProgress } = {}) -> Promise<ItemView>   // XHR + CSRF header
export function validateLibraryFile(file, maxMb, kind) -> string | null ; export function acceptedExtensions(kind) -> string[]
// web/js/studio/views/visualReview.js (owner: review UI) — mountVisualReview(container, args, { pickLibraryItem? })
// web/js/studio/lib/optionsForm.js — hasLibrary(meta); buildGenerationOptions sends prefer_library_visuals only when
//   /api/meta has "library"
// web/js/studio/lib/lessonStage.js — STAGES, STAGE_INFO, WORKFLOW_STEPS, stageInfo(stage), workflowSteps(stage),
//   deriveStage(version, job) (servers without stage), nextStepFor(stage, ids), safeStepHref(href) ("#/…" or same-site
//   "/…" only), projectStage(project) -> {stage, next, derived} | null
// web/js/studio/components/stage.js — stageChip(stage, opts), nextStepCard({stage, next, canDo, onAction, note, extra})
// web/js/studio/components/videoPreview.js — openVideoPreview(opts) (HTML5 <video>, source released on close)
// web/js/studio/lib/debug.js — showTechnical(loc?) (?debug in the page or hash query)
// web/js/studio/lib/visibility.js — pageHidden(doc?), onceVisible(fn, doc?) -> cancel
// web/js/studio/lib/names.js — nameKey, uniqueJoin(parts, sep), distinctTitles(items, opts), withSuffix(title, suffix)
// web/js/studio/views/editor/splitScene.js — splitProblem(sp, sceneId, beatIndex), canSplitType(scene),
//   splitScene(sp, sceneId, beatIndex) -> {screenplay, scene}   (pure; scene = the new second part)
// web/js/studio/views/editor/sceneMerge.js — mergeDrafts(base, mine, theirs), mergeScene(b, m, t) (field by field;
//   beats and board items by id)
// web/js/studio/views/editor/changes.js — HISTORY_LIMIT (200), editLabel(key), changesBySceneId(res),
//   fieldWords(fields), changeBadge(change), restorePosition(all, removed)
// web/js/studio/views/editor/timelineStrip.js — stripModel(timeline, sp), fallbackModel(sp), createTimelineStrip(opts)
// web/js/studio/components/shortcutsDialog.js — openShortcutsDialog(groups)
```
