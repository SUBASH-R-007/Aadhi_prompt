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
#   "user" when the provider whose key pays for usage_provider (USAGE_KEY_PROVIDER: "veo", "omni", "lyria" -> "gemini")
#   is "personal" in sources, else "server"
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
#   operations llm | vision | tts | image | video | gif | music; "music" = {"unit": USD per song, "second": USD per s}
#   per provider and model family; video provider "omni" per second (OMNI_VIDEO_USD_PER_MILLION_TOKENS x
#   OMNI_VIDEO_TOKENS_PER_SECOND); any OTHER operation fails closed: every reported quantity at the global default
#   rate of its kind (at least one unit), one warning per operation and process ("fake" stays free)
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
# reader_version.py — NEWER_DOCUMENT = "newer_document" (job error_code)
newer_documents(db: Session, version_ids: list[int]) -> list[NewerDocumentError]   # one query on the schema_version markers
newer_document_refusal(db: Session, version_id: int | None, payload: dict | None) -> str | None
#   the worker calls it before a handler (the job's version and payload["source_version_id"]): a document above
#   READER_SCHEMA_VERSION fails the job with error_code "newer_document" without running it; a NewerDocumentError a
#   loader raises mid-job fails it the same way (fatal, never retried)
```

## aadhi.schemas (owner: lead; contract files) — schema v3, Dynamic video B0
```python
# features.py (pure): feature names, the reader-version marker, the usage walk
CLASSIC_SCHEMA_VERSION = 2 ; V3_SCHEMA_VERSION = 3 ; READER_SCHEMA_VERSION = 3   # newer documents are refused
DYNAMIC_FEATURES = ("presentation", "items_v3", "cuts", "overlays", "clip", "voice_override")   # frozen names
FEATURE_LABELS: dict[str, str]                                # plain words for the 422 message
class NewerDocumentError(Exception): code = "newer_document"; document: str; version: int; reader: int
#   NOT a ValueError: pydantic never wraps it, so no `except ValidationError` turns it into a 422 or "unreadable"
check_reader_version(data: Any, document: str) -> None        # raises NewerDocumentError; every model calls it first
features_used(model) -> {feature: [location]} ; disabled_features(model, enabled) -> same ; uses_v3(model) -> bool
marked_version(model, current: int) -> int                    # 3 when a v3 field is used, a 3 without one -> 2
#   models declare V3_FIELDS (field -> feature), V3_VALUES (field -> value -> feature) and V3_MODEL; a field counts as
#   used when it would be serialised (exclude_if); Screenplay / Timeline / AssetManifest set schema_version on
#   validation and on every dump, so a model_copy can never write a v3 document as 2
# generate.py — python -m aadhi.schemas.generate [--check]: web/schemas/screenplay.schema.json + timeline.schema.json
#   (byte-for-byte; CI and tests/schemas/test_v3_schema.py); the JSDoc mirrors in web/js/shared/types.js are by hand
# config: Settings.dynamic_features (DYNAMIC_FEATURES env, comma list; unknown names fail at start-up)
# --- timeline.py, Dynamic video B2a (additive; every field left out of the JSON at its default, so a B1 timeline
#     reads and writes back byte for byte; DYNAMIC_VIDEO_DESIGN §6.7 "As built in B2a")
# TimelinePresentation:
#   engine_version: int = 1 (>= 1)   # the LOOK-ENGINE GENERATION (1 = B1's resolver, 2 = B2's; the stage contract
#                                    # stays aadhiStage v3); compose.presentation.ENGINE_VERSION = 2
#   content_alt: dict[str, str] = {} # the other mode's content tokens, the keys of `content`
#   family: CatalogueId | None ; mode: "dark" | "light" | None
#   motion_scale: float = 1.0        # 0 < x <= 4; the personality's duration scale
#   entity_colors: dict[str (1..80 chars), int 0..2] = {}   # lesson-wide, <= 60 (MAX_ENTITY_COLORS), as Presentation's
# ResolvedLook:
#   tokens: dict[str, str]           # engine 2: struct.accent, bg, bg-alt, bg-tint, surface, block.base,
#                                    # block.highlight, block.ink, block.ink2 (#RRGGBB upper case), scrim (#RRGGBBC7);
#                                    # engine 1: the first five
#   fixes: LookFixes | None          # {safe: bool = False, slots: {slot (1..60 chars): SlotFix}} (both keys written);
#                                    # SlotFix {size?: 0 < x <= 1000, scrim?: bool}: preflight's remedy channel
#   icons: dict[Slug, LookIcon] = {} # <= 32; LookIcon {name, set, version, viewBox [x, y, w > 0, h > 0],
#                                    # stroke_width 0..8 = 2.0, filled = False, paths: 1..32 x <= 4096 chars, ICON_PATH_RE}
#   refs: LookRefs | None            # {roadmap?: [{title, state: done|current|upcoming}] (<= 12), anchor?: {kind:
#                                    # formula|figure|takeaway, text | tex (exactly one), icon?}, prediction?: {text,
#                                    # resolve_t? >= 0}, next?: {title}, accent_next?: "#RRGGBB"}
#   accent_fade: float | None (0.6..1.0) ; continues_from: Slug | None
#   variant: B1's "safe" is a legacy value read for engine-1 timelines only; a B2-resolved look never carries it
# new models (v3-only): SlotFix, LookFixes, LookIcon, RoadmapEntry, RefAnchor, RefPrediction, RefNext, LookRefs;
#   JSDoc mirrors in web/js/shared/types.js; web/schemas/timeline.schema.json regenerated
# --- timeline.py, Dynamic video B2b (DYNAMIC_VIDEO_DESIGN §6.7 "As built in B2b")
# TimelinePresentation.fonts: TimelineFonts   # was dict[str, str]
#   TimelineFonts(StrictModel) {display?, body?, mono?, indic?}: each CatalogueId | None, left out when None, so a
#   stored B1 {display, body, mono} reads and writes back byte for byte; unknown keys are ignored (as every schema
#   model); values are family / group ids of compose/dynamic/faces.py, never CSS text
#   indic ("noto_sans" | "anek") marks the B2 form (the stage feature-detects it; web/js/dynamic/fonts.js)
#   v3-only model (tests/schemas/test_v3_schema.py); JSDoc TimelineFonts in web/js/shared/types.js; schema regenerated
# compose.presentation.timeline_presentation stores fonts = faces.resolver_fonts(presentation.display_font) (B1's
#   faces in the B2 form; ENGINE_VERSION stays 2)
```

## aadhi.assets_auth (owner: api + pipeline)
```python
FIELD_RULES: dict[str, FieldRule]   # frozen table of design §6.7: field -> allowed asset kinds + MIME family
#   SourceFigure.asset_key, SidePanel / SimulationScene / AIVideoScene.override_asset_key,
#   InteractiveScene.poster_override_asset_key (classic, unchanged); ClipScene.clip.asset_key and
#   Overlay.clip.asset_key {clip, upload, video} motion; Presentation.music.asset_key {music, upload} audio;
#   Beat.voice_override_asset_key {narration_upload} audio. A new key field must be added here and to
#   api.screenplay_service.asset_uses (tests/core/test_assets_auth.py enforces it)
FieldRule.fits(kind: str, mime: str) -> bool ; FAMILY_MIMES = {visual, motion, visual_or_motion, audio}
record_asset_refs(db: Session, project_id: int, keys: Iterable[str]) -> int   # THE way to add AssetRef rows: idempotent,
#   INSERT ... ON CONFLICT DO NOTHING per 500 keys, never commits (uploads, imports, generated media, renders;
#   pipeline name dbops.ensure_asset_refs)
# models.PUBLIC_ASSET_KINDS += music, clip, clip_proxy, clip_sprite, waveform, voice_sample, thumbnail,
#   narration_upload (served from /media); calibration stays private; each joins jobs.cleanup.GC_ASSET_KINDS in the
#   batch that starts producing it
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
translate.scene_fields(scene: dict, translate_board: bool, keep: list[str]) -> list[FieldItem]   # with translate_board also
#   the v3 on-screen fields board[].unit, board[].date and a clip scene's captions[].text (TRANSLATABLE_FIELDS);
#   icon_hint, value, credit and attributions are protected
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
assets.voice_config(settings, options, language, tts) -> VoiceConfig(provider, voice, rate, model, language)
assets.FALLBACK_TTS_PROVIDER = "edge" ; assets.fallback_voice_config(settings, language, tts, rate) -> VoiceConfig
#   the voice narrating once the lecture's voice model is no longer served (ModelUnavailable): TTS_VOICE when Edge is
#   the server's provider and language == DEFAULT_LANGUAGE, else Edge's default voice for the language; same rate.
#   The build switches once (lecture-level warning assets.tts_fallback + a job-log warning); the scene that met the
#   error is narrated again wholly in it. Without Edge on the server: assets.tts_failed as before
#   _Builder.clip_key(text, voice=None) -> key   # content key of one beat's clip in voice (default: the build's), so
#   fallback clips are keyed exactly like an Edge lecture's
#   build_assets never builds a hidden scene (not even when scene_ids names it) and never lists it stale; scene_hash
#   ignores hidden and min_seconds (both kept by repair on regeneration), and the Dynamic look and the beats' overlays
#   (cuts, speaker and voice_override_asset_key stay in: they change the audio); repair.carry_over keeps look and the
#   scene's v3 intent (narrative_role pretraining/predict/recap, visual_intent). screenplay_ops.plan_from_screenplay
#   leaves clip scenes out and canonicalize.planned_from_scene maps v3 roles to classic ones (until B4).
#   Clip keys carry OMNI_MODEL for provider "omni", VEO_MODEL for every other provider (keys unchanged); provenance,
#   checkpoint and budget estimate use the adapter's .model (else VEO_MODEL).
#   dbops.ensure_asset_refs(db, project_id, keys) -> int   # = assets_auth.record_asset_refs (generated media) orchestrator: generate_lecture / translate
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
#   translate copies the source version's ingest_key and source_overrides into the translation's generation_meta;
#   regenerate_scene reads the extract its scenes cite (orchestrator._version_extract: the version's own, else, for
#   an older translation, its source version's under _version_brief's rules); a RateLimited concept brief propagates
#   (the job is retried) instead of planning without it
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
sync.SyncOptions.from_settings(settings, *, fps=None) -> SyncOptions | None   # None with SYNC_WORD_ANCHORS=false;
#   fps: the frame grid cues snap to (None = RENDER_FPS, every classic timeline; build_timeline passes DYNAMIC_FPS 30
#   for a Dynamic timeline)
sync.scene_sync_cues(beats, board, side_panel, duration, options) -> list[SyncCue]   # pure, sorted by start
captions.to_srt(cues) -> str ; captions.to_vtt(cues) -> str
captions.split_caption(text, words, *, start=None, end=None, width=MAX_LINE_CHARS) -> list[CaptionCue]   # <= 2 lines x width (42)
base.STAGE_SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}   # frozen in B0
base.stage_size(format: str | None = None) -> tuple[int, int]      # None = landscape (classic); ValueError for others
base.timeline_stage(tl) -> (tl.width, tl.height)                    # the render page's viewport and screenshot size
base.output_size(tl, settings) -> tuple[int, int]                   # the classic stage: exactly RENDER_WIDTH x
#   RENDER_HEIGHT; other stages scaled by the configured short side / 1080, even sides (1920x1080 -> a 1080x1920 reel)
#   build_timeline: Timeline.width x height = stage_size(screenplay.presentation.format) (1920x1080 without presentation)
loudness.ebur128(path, settings, *, true_peak=True, timeout=600, check_cancelled=None) -> R128Summary   # LUFS, LRA, peaks
loudness.loudnorm_measure(path, settings, target=None, *, ...) -> LoudnormStats   # first loudnorm pass (print_format=json)
loudness.loudnorm_normalize(src, dest, settings, target=None, *, measured=None, linear=True, ...) -> (first, second)
#   two passes; refuses silent input (LoudnessError); parse_ebur128 / parse_loudnorm, loudnorm_filter, ebur128_filter,
#   static_gain_db, limiter_filter; LoudnessTarget(integrated=-16.0, true_peak=-1.5, lra=9.0); as_dict(): -inf -> None
chapters.youtube_chapters(timeline: Timeline) -> str ; chapters.opening_title(taken) -> str   # never a duplicate "Introduction"
video  # registers "render_video"; video.sweep_render_workspaces(settings, session) -> int
preflight.render_preflight(timeline, issues=None, hidden=frozenset()) -> list[{scene_index, scene_id, title, reason, blocking, message}]
preflight.quality_items(issues, timeline=None, hidden=frozenset()) -> list[{..., reason: "quality", blocking: False, severity, code}]   # <= 12 (+1 "more")
#   hidden: ids of scenes skipped in the video, whose issues are never listed; the API adds scene_number (the
#   screenplay place, api/routers/renders.numbered); video.render_warnings(timeline, capture=None, numbers=None) too
#   within a severity, lecture-wide and scene findings alternate (scene findings are not pushed into "N more")
capabilities.render_capabilities(settings, *, browser=True, force=False) -> RenderCapabilities   # cached, blocking
qa.inspect_render(path, *, settings, fps, expected_frames, ..., size=None) -> RenderQA (Render.options["qa"]) ; workspace.RenderWorkspace, sweep_stale(settings, statuses)
qa.edge_sample_size(width, height) -> (320, 180) | (180, 320)   # black-edge sampling, transposed for portrait (also evals/video_qa.py)
# --- Dynamic video, B1 (DYNAMIC_VIDEO_DESIGN §7; only while DYNAMIC_ENABLED is on; classic timelines never get here)
presentation.resolve_looks(screenplay, timed_scenes, chapters=None) -> list[ResolvedLook]   # pure; B2a: chapters
#   default to timeline.build_chapters(...) (_assemble passes the list it stores in Timeline.chapters); palette slot =
#   SceneLook.palette_slot, else chapter_indices; look.tokens = themes.scene_tokens(family, slot) (the ten keys); the
#   tone step resets at each chapter; transition_in: cut into scene 0, crossfade into/out of colour_block, else the
#   requested cut/crossfade, else crossfade at a new chapter or a changed struct.accent, else cut; a crossfade lasts
#   crossfade_seconds(d) (None -> cut); continues_from kept only when it names the previous scene
presentation.timeline_presentation(presentation, timed_scenes, total) -> TimelinePresentation   # engine_version 2;
#   also content_alt (themes.content_alt_tokens(mode)), family, mode, motion_scale, entity_colors
presentation.ENGINE_VERSION = 2 ; CROSSFADE_SECONDS 0.45, CROSSFADE_MIN_SECONDS 0.3, CROSSFADE_MAX_SECONDS 0.5 (=
#   transitions.js) ; CHAPTER_START_EPS = 1e-3
presentation.chapter_indices(scenes, chapters) -> [int]   # B2a signature (B1: (screenplay, scenes)); index in
#   Timeline.chapters of the last chapter starting <= scene.start + 1e-3 (board_generic's kicker rule), 0 when none
presentation.crossfade_seconds(duration) -> float | None   # round(min(0.45, max(0.3, d / 2)), 3); None when d < 0.3
presentation.stage_personality(personality, family) -> str   # the personality when released in R1, else the family's
presentation.motion_scale(personality, family) -> float      # PERSONALITIES[stage_personality(...)].duration_scale
presentation.dynamic_enabled(settings) -> bool ; presentation.seed_for(presentation, scene_id, purpose) -> int (31 bit)
#   build_timeline: a screenplay with a presentation + the flag on -> Timeline.presentation, TimedScene.look, no intro,
#   fps 30 (timeline.DYNAMIC_FPS; sync cues on that grid), 9:16 phrase captions at PORTRAIT_CAPTION_LINE_CHARS (16);
#   flag off -> the classic timeline, except that Timeline.width x height still follow presentation.format
#   (stage_size: a portrait presentation gives a classic timeline on a 1080x1920 stage)
presentation.portrait_caption_cues(text, words, *, start, speech_end, hold_end, width=16) -> [CaptionCue]
#   9:16 only: minimum-cost grouping (no chunk under PORTRAIT_CAPTION_MIN_SECONDS 1.0 s where the beat allows it),
#   long words split between grapheme clusters keeping their word's timing (captions.tokens_with_owner(text, width,
#   clusters=True); split_caption(..., clusters=False) is the classic splitter, unchanged)
api.timelines.current_timeline(version, settings) -> stored | rebuilt   # DYNAMIC_ENABLED applies when served
#   (versions, shares, /api/render/timeline): a build of the current revision whose engine differs from what the flag
#   and the screenplay ask for now is served as build_timeline builds it now; a stale build is served as built,
#   except a Dynamic build while the flag is off (rebuilt classic from the current screenplay); timeline_etag carries
#   the flag. B2a: a Dynamic build of the current revision, still wanted Dynamic, whose built_engine(stored) is below
#   presentation.ENGINE_VERSION is also served rebuilt (a newer generation is served as stored: the stage refuses
#   it); timeline_etag's identity also carries f"engine={ENGINE_VERSION}"; a 304 is answered before any rebuild
api.timelines.built_engine(stored) -> int   # presentation.engine_version when an int >= 1 (bools excluded), else 1
video.render_video: timeline.presentation.style == "dynamic" -> motion.job.render_dynamic(ctx, job, payload, ws)
motion.job.caption_modes(payload, timeline) -> (burn, soft)   # burn: payload["burn_captions"] when present, else
#   presentation.captions_burn (POST /render leaves an unset burn_captions out of a Dynamic lecture's payload:
#   api.routers.renders.RenderBody.job_options(dynamic=True)); soft: a clean file's default-on track unless the
#   payload's soft_subtitles is false
motion.job.dynamic_warnings(items, timeline=None, numbers=None) -> items   # video.render_warnings items worded for
#   the motion engine; with timeline also undrawn_panel_warnings(timeline, numbers), every item in scene order
motion.job.undrawn_panel_warnings(timeline, numbers=None) -> [{scene_index, scene_id, title, reason: "panel_not_drawn",
#   blocking: False, message, scene_number?}]   # one per shown side panel of a kind in UNDRAWN_PANEL_KINDS (chart,
#   graph, model_3d, skill_tree, quiz: the stage does not draw them yet); a template that draws a kind removes it
motion.engine.render_motion(ctx, timeline, out_path, settings, *, burn_captions=False, soft_subtitles=False,
                            token=None, served=None, work_dir=None, cache=None, local_files=None, title=None,
                            config: MotionConfig | None = None, refresh_served=None) -> MotionResult
#   served: the timeline the page receives (preflight's PreflightResult.timeline); refresh_served(): served with its
#   presigned URLs signed again, called before every page load (motion.job.refresher: S3 without a CDN only, the
#   preflight remedies kept); cache: ChunkCache.in_workspace(root);
#   local_files: asset key -> file already on disk; raises MotionError(code, retryable), StageError, FFmpegError,
#   ContainerCheckFailed. MotionConfig(workers, chunk_min=240, chunk_max=600, recycle_frames=1800,
#   recycle_slowdown=0.5, stage=StageOptions(), use_slots=True, sample_memory_seconds=2.0, encoder_timeout=600,
#   shared_driver=True). MotionResult.summary() -> Render.options["qa"]["motion"] (docs/API.md)
#   every worker's page (and the remedy's) must match the timeline before it captures: StageInfo.check(size, fps,
#   frames) and motion.stage.scene_mismatch(info, bounds) (an empty scene list for a timeline with scenes mismatches:
#   stage_contract); the rerender_chunks remedy acquires 1 CPU slot of its own (cancel-aware) and holds it until its
#   browser is closed
motion.engine.video_media_keys(timeline, *, overlays=OVERLAYS_DRAWN) -> [asset key]   # video media fetched before the
#   capture (scene and panel media; overlay media only with overlays); OVERLAYS_DRAWN = False until B6's overlay layer
motion.resources.motion_workers(settings, budget=None) -> int ; CpuBudget.detect() ; CpuSlots.from_settings(settings,
#   budget).acquire(want, check_cancelled=, on_wait=) -> SlotLease(count).release()   # RENDER_SLOT_DIR/slot-NNN.lock
motion.chunks.scene_frame_bounds(timeline, fps=None) -> [(f0, f1)] ; plan_chunks(bounds, min_frames, max_frames)
#   -> [Chunk(index, f0, f1)] ; chunk_key(timeline, chunk, *, bundle, encoder, capture, chromium, media, data) -> 64 hex
#   = sha256 of {v: CHUNK_CACHE_VERSION (3 from B2a), engine_version, frames, timeline slices (scenes, outgoing scene,
#   the chapter title each shows as its kicker, background_clock, global fields, captions in range), media identities,
#   stage bundle digest, encoder and capture parameters, Chromium version}; STAGE_TIMELINE_FIELDS lists every top-level
#   field the stage reads (all hashed)
motion.chunks.background_clock(data, t) -> float   # B2a: state.js backgroundTime / frozenIntervals mirrored: t minus
#   every frozen stretch [start + max(0, frozen_at), start + duration) before t; the slices hold
#   round(background_clock(data, f0 / fps), 6) (an earlier scene's hold moves later chunks' backgrounds)
motion.media.native_frames(frames, *, src_fps, rate) -> {page index: file frame}  # floor(n * rate / srcFps): a clip
#   at another rate than the page grid (24 fps Veo) is extracted at its own rate, frame-exact in any chunk plan
motion.encode.EncoderSpec.from_settings(settings, fps, out_size).chunk_args(frames=, output=, capture=)   # §7.5 + -threads 4
motion.encode.master_audio_command(MasterAudioSpec(audio_list, total_seconds, output="master.m4a", bgm=None, ...),
#   script_flag=) -> Command        # AAC-LC 48 kHz stereo 384k, encoded while the frames are captured
motion.encode.final_mux_command(MotionFinalSpec(video_list, audio, metadata, total_seconds, output,
#   soft_subtitles=None, subtitle_language="und")) -> Command   # -c copy both, +faststart+negative_cts_offsets
motion.stage.StageSession(settings=, size=, token=, served=, base_url=, media_origins=, options=StageOptions(
#   webgl, jpeg_quality=92, ready_timeout, call_timeout, layers, extra_args=()), playwright=None, refresh_served=None)
#   StageOptions.chromium_args() = [*CHROMIUM_ARGS, *DETERMINISM_ARGS, *(WEBGL_ARGS if webgl), *extra_args], part of
#   capture_fingerprint() and so of the chunk key; stage_page.stage_browser launches with the same list
#   a CDP capture on a dead renderer raises StageError("stage_crashed", retryable) (the chunk is captured again)
#   /__frames/[<chunk>/]<media key>/<source frame>.jpg answered from FrameRegistry (route.fulfill); else 404
dynamic.stage_page.stage_browser(settings, *, size, base_url=None, media_origins=(), webgl=False, call_timeout_s=60,
#   check_cancelled=None, extra_args=(), playwright=None) ; open_stage(..., token, served_timeline, frames) -> StagePage
#   StagePage: load(), info() -> StageInfo, seek(frame) -> key, text_boxes(), media_plan(f0, f1), set_layers(captions=,
#   stage=), capture_jpeg(q=92), capture_png(transparent=); errors / guard_trips collected; StageError codes:
#   render_page_* (boot), stage_contract (never retried), stage_call_failed (the page's code), stage_crashed (retryable)
#   stage_page.DETERMINISM_ARGS = ("--disable-partial-raster",)   # Dynamic stage only, on top of
#   screenshot.CHROMIUM_ARGS (order-independent frames, DYNAMIC_VIDEO_DESIGN §7.4); the classic capture never gets it.
#   StageBrowser.open(...) closes a page that fails to come up before raising; only a loaded page joins .pages
dynamic.preflight.run_preflight(settings, timeline, *, token, served_timeline=None, base_url=None, media_origins=(),
#   check_cancelled=None, ..., captions=None) -> PreflightResult(ok, timeline (served, with remedies), sampled,
#   remedies, summary(), as_qa()); captions: whether the render burns captions (None = presentation.captions_burn);
#   the page's caption layer is set to it after every load (the remedy round's reload too), so caption boxes are
#   checked only when burned; expected frames = motion.chunks.total_frames(scene_frame_bounds(timeline, fps)); one
#   item per (check, scene, slot) across both rounds; raise_if_blocked(result) -> PreflightBlocked (code
#   preflight_blocked); sample_frames(info, timeline)
dynamic.container.inspect_container(path, *, settings, fps, expected_frames, expect_audio, expect_subtitles=False,
#   chunks=(), loudness=None, size=None) -> ContainerQA ; verify_container(inspect, remedies) -> ContainerQA |
#   ContainerCheckFailed (render_invalid) ; motion_qa(container, preflight) -> dict ; record_qa(ctx, render_id, qa)
dynamic.determinism.run_plans(open_page, standard_plans(0, 300, 3)) -> DeterminismReport (1x300, 3x100, reverse)
dynamic.preflight (B2a): remedies go to the fixes channel only
#   apply_remedies(timeline_dict, remedies) -> new dict: safe_variant -> look.fixes.safe = True; shrink ->
#   look.fixes.slots[slot].size; scrim -> look.fixes.slots[slot].scrim = True; look.variant / look.text_fit are never
#   written; fixes is created as {safe, slots} when absent (a malformed safe or slots replaced, other keys kept);
#   idempotent; the input is not changed; missing looks, out-of-range scenes, unknown kinds and a sizeless shrink skipped
#   look_is_safe(look) -> bool (fixes.safe is True or B1's variant == "safe") ; safe_scenes(timeline_dict) -> frozenset
#   evaluate_box(..., safe: bool = False, ...) / evaluate_boxes(..., safe_scenes=frozenset(), ...) replace variant(s)=;
#   plan_remedies: a slot minimum that is not positive uses the format's floor; SAFE_VARIANT ("safe") is read only;
#   MIN_TEXT_PX, MIN_LABEL_PX, CRITICAL_BANDS, CAPTION_BANDS, PORTRAIT_* keep their names and values, from zones.py
# --- Dynamic video, B2a: the catalogue modules (pure data and functions; imported only inside the Dynamic gate) ---
dynamic.colour_math   # the one colour module: srgb/linear, OKLab/OKLCH (Ottosson), gamut_map_oklch (CSS Color 4, as
#   color.js), palette_hex, js_round, parse_hex / to_hex, mix_oklch / mix_oklch_values, desaturate, composite /
#   source_over, relative_luminance, contrast_ratio, worst_contrast, simulate_cvd (Machado 2009), oklab_distance,
#   red_ratio; parity: tests/fixtures/colour_vectors.json (node scripts/gen_colour_vectors.mjs [--check])
dynamic.themes        # §4.7 catalogue + validator: content_tokens(mode), content_alt_tokens(mode), FAMILIES (12; 4 R1),
#   accent_hue(family, slot), structure_tokens, scene_colours / scene_tokens(family, slot) -> the ten look.tokens keys
#   (DERIVED_KEYS: block.base, block.highlight, block.ink, block.ink2, scrim), block_colours, ink_on, block_ink2,
#   scrim_colour, scrimmed_bg, code_surface(family), PERSONALITIES (6; crisp, soft R1), caption styles, cue colours,
#   validate(...) -> Report(findings, advisories, measures, scrims, scrim_roles, nudges, red_lowered,
#   chroma_restricted; released_findings(), worst(code, scope)), forced_scrims()
dynamic.zones         # ZONES_VERSION 1; Box(x0, y0, x1, y1); ZONES[fmt].as_stage_dict() == core.js ZONES[fmt];
#   zones_for(fmt); GRID (16:9, 12 x 122 px, 24 px gutters); REGIONS[fmt][name]; END_SCREEN (x 960-1920, 15 s);
#   YT_SHORTS, META_REELS, TIKTOK, UNIVERSAL = intersect_zones(...), PlatformZone.safe_boxes(); TYPE_FLOORS[fmt],
#   min_text_px(fmt), min_label_px(fmt); TYPE_SCALE; LAYOUT (spacing, radius, border, outline, shadow, icon)
dynamic.templates     # TEMPLATES (41 TemplateSpec), IMPLEMENTED ('board_generic',), template(id), registry_problems();
#   TemplateSpec(id, semantic_class, representational, release, duration {fmt: Duration(min, max, per_item=False,
#   end_screen=None)}, consumes Consumes(kinds, targets), data_fit, implemented, formats, variants {fmt: (...)},
#   forced_for, holds, dense, push_ok, slots {slot: {fmt: {variant: SlotSpec}}}); .fit(scene, fmt, *, last=False,
#   chapter_end=False) -> 0 | 0.2 | 0.5 | 0.8 | 1.0; .events(scene, timed, fmt) -> [] until B2d;
#   SEMANTIC_CLASSES (19, frozen); signals(scene, *, last, chapter_end) -> (Signal(id, semantic_class, preferred,
#   alternatives, forced), ...); CROSS_CLASS_ALTERNATIVES; ROLES (14), BLOCK_ROLES; BACKGROUNDS (15), TRANSITIONS (8)
#   target grammar: parse_target(text) -> Target(form, item, index, series, symbol) (ValueError outside the grammar),
#   format_target(target), target_form(text), TARGET_FORMS (12), RESERVED_TARGET_WORDS {'panel', 'option'}
dynamic.catalogue     # python -m aadhi.compose.dynamic.catalogue [--check] -> web/js/dynamic/catalogue.gen.js
#   catalogue() -> plain data; render() -> the file text (sorted keys, LF, <= 120 columns, byte-deterministic);
#   first_difference(current, expected) -> line | None; CATALOGUE_VERSION = 1; --check exits 1 naming the first
#   differing line (CI "Generated files are current"). Top-level keys: version, zones_version, zones, regions, grid,
#   end_screen, platforms, type, layout, captions {style: {release, look, metrics (geometry) | null, colours | null}},
#   cues, templates {id: {class, ..., duration {fmt: {min, max, per_item, end_screen}}, slots, consumes}},
#   semantic_classes, targets, roles {colour, block}, backgrounds, transitions, personalities, scrims, studio_surface
#   (null), families, content. Runtime use is geometry only (web/tests/dynamic/sourceScan.test.js CATALOGUE_RUNTIME)
# --- Dynamic video, B2b: assets (imported only inside the Dynamic gate; DYNAMIC_VIDEO_DESIGN §4.6, §4.12) ---------
dynamic.faces         # the one face table; pure data and functions, no Pillow
#   python -m aadhi.compose.dynamic.faces [--check] -> web/css/dynamic-fonts.css (one @font-face per B2b face,
#   font-display: block, url('../vendor/<file>')) + web/js/dynamic/faces.gen.js (export const FACES, frozen; keys
#   JS_TOP_LEVEL_KEYS: version, b1_display, defaults, generic, scripts, languages, indic, families, faces); --check
#   exits 1 naming the first differing line; render_css(), render_js(), table()
#   FACES_VERSION 1; ROLES (display, body, mono, indic); INDIC_SCRIPTS (tamil, devanagari, telugu, kannada, malayalam);
#   LANGUAGE_SCRIPTS; OFL, OFL_TEXT_MARK; FONTSOURCE_VERSION '5.3.0'; DEFAULT_FONTS {display: outfit, body: inter,
#   mono: jetbrains_mono, indic: noto_sans}; B1_DISPLAY (outfit, inter); DISPLAY_FACES_R1 (outfit, poppins, lexend;
#   themes.FACES / DISPLAY_FACES_R1 re-export it); GENERIC {display, body: 'sans-serif', mono: 'ui-monospace, monospace'}
#   Family(id, name, role, release, licence, note, package, version, css 'classic' | 'dynamic' | None (W2, not
#   vendored), file_stem, weights, axes (Axis(tag, minimum, maximum, default) as the FILE defines it: Anek wght
#   100-800, default 500), subsets, script, requests); .vendored, .variable, .directory ('fonts' |
#   'fonts/dynamic/<id>'). FAMILIES (by id); INDIC_GROUPS {noto_sans | anek: {script: family id}}
#   Face(family, subset, weight (lo, hi), file (under web/vendor), source (under node_modules), unicode_range);
#   .ranges, .covers(cp); FACES (declaration order); faces_of(id); parse_unicode_range(text)
#   Fonts = Mapping | pydantic BaseModel | None; fonts_mapping(fonts) -> dict (a model dumped with exclude_none, None ->
#   {}, anything else TypeError); is_b2_fonts(fonts) (a str `indic`); stage_roles(fonts) -> {role: family id} (B1 form
#   vs B2 form, = fonts.js stageRoles); resolver_fonts(display_font) -> {display, body, mono, indic} (B1's faces);
#   script_for_language(lang); indic_families(group, language) (own script first); stack(fonts, role, language) ->
#   family ids; css_stack(fonts, role, language) -> str (= fonts.js fontStacks)
#   for text fit: match_weight(available ranges, desired) (CSS Fonts 4 §5.2); face_for(family_id, weight, codepoint)
#   -> Face | None (matched weight, then the LAST-declared face whose range covers it); web_path(face, web_dir=None)
dynamic.icons         # python -m aadhi.compose.dynamic.icons [--check] -> aadhi/compose/dynamic/icon_index.json
#   (committed; needs node_modules to generate; --check exits 1 when stale, 2 when it cannot generate)
#   INDEX_VERSION 1; SETS (IconSet(id, package, version, licence, licence_marks, svg_dir, metadata)): lucide
#   (lucide-static 1.48.0, ISC, icons/, tags.json), tabler (@tabler/icons 3.48.0, MIT, icons/outline/, icons.json);
#   SET_ORDER (lucide, tabler); TIERS ((lucide, name), (tabler, name), (lucide, tag), (tabler, tag)) (owner decision);
#   STYLE {viewBox (0, 0, 24, 24), stroke_width 2.0, filled False, round caps and joins}; HINT_MAX_CHARS 24,
#   HINT_MIN_KEY 2, NAME_MAX_CHARS 64, SVG_MAX_BYTES 64 KiB, COORD_MAX 1e5, DECIMALS_MAX 6, DIGITS_MAX 15;
#   NEGATIONS {off, slash}, NEGATION_LAST {x}; EXCLUDED_CATEGORIES {tabler: {Brand}}; PREFERRED, IGNORED_TAGS (per
#   set; B2b proposals for the owner's review)
#   errors: IconError(ValueError) (one icon cannot be normalised), IconIndexError(RuntimeError) (the committed index is
#   missing, damaged or stale), IndexBuildError(RuntimeError) (cannot generate from what is installed)
#   parse_path(d) -> [(command, [Decimal])] (strict SVG 2 grammar); canonical_path(d) -> str (exact, no exponent,
#   <= 4096 chars, matches timeline.ICON_PATH_RE); fmt(Decimal); shape_path(tag, attrs) (SVG 2 basic-shape
#   equivalents); parse_svg(text) (refuses DTDs, entities, PIs other than a leading XML declaration, CDATA, foreign
#   namespaces, namespaced attributes, nesting, text); normalise_svg(text, *, set_id, name, version) -> LookIcon;
#   sanitise_icon(value) -> LookIcon | None (schema validation, then every path re-spelt canonically)
#   normalise_key(text) -> str | None; hint_forms(key) (as written, without hyphens, English singulars of the last
#   word); negates(name, key); match(hint, *, path=INDEX_PATH) -> IconMatch(set, name, via 'name' | 'tag', form) |
#   None (None for a non-str hint, a hint holding a character other than letters, digits and word separators, or a
#   one-character key); icon_for(hint, *, path) -> LookIcon | None (a fresh model each call; IconIndexError when a tag
#   or alias names a missing icon or a stored path fails sanitising); scene_icons(items, *, path) -> {item_id:
#   LookIcon} (items: objects with id / icon_hint, e.g. BoardItem, or mappings with those keys, else TypeError; item
#   order; <= ICONS_PER_SCENE_MAX 32; the first icon kept for a repeated id); load_index(path) (lru_cache; checks
#   version, set order, style, packages, versions and value types) -> {set: _SetIndex(version, icons, aliases, tags)}
#   build_index(node_modules, *, prune=True), render_index(node_modules, *, data=None), main(['--check' | ...])
#   index (version 1): {version, generator, order, style, sets: {lucide | tabler: {package, version, licence,
#   licence_text, icons {name: [d]}, aliases {deprecated name: icon}, tags {key: icon}, excluded {name: reason}}}};
#   keys sorted, one entry per line, LF, UTF-8. Run-time use reads the JSON only (no XML parser imported; tested)
dynamic.kerning       # the measured kerning of the vendored Latin faces (B2b review F1, F2); read at run time as JSON
#   python -m aadhi.compose.dynamic.kerning [--check] -> aadhi/compose/dynamic/kerning.json (committed; measures in
#   Chromium with web/vendor, 85-90 min; --check exits 1 when it differs, 2 when it cannot measure; NOT in CI)
#   VERSION 3; MEASURE_PX 1000; EPSILON_MILLI 1; MARGIN_MILLI 0.1; CONTEXT_TOL_MILLI 0.05; OPEN_TOL_MILLI 0.05;
#   COVER_MIN 2; LETTER_SPACING ('0px', '10px'); KerningError(RuntimeError) (missing, damaged, not the face table's)
#   load(path=TABLE_PATH) -> {face file name: FaceKerning} (lru_cache, fails closed); FaceKerning(positive, negative,
#   up, down, open, open4, chain): upper(a, b), lower(a, b) (+/- EPSILON for an unlisted pair), context(a, b, c) ->
#   (above, below), bounds(run) -> (wider, lower) em, bounded(run) -> bool (a run of 4+ holding an open triple, an
#   open 4-run or two chain characters among any four is never bounded); is_mark(ch); table_faces(); face_name(...);
#   chain_characters(contexts); near_triples(contexts, chain, chars); cover(runs, minimum=COVER_MIN); measure(...)
dynamic.textfit       # measured text fit; loads PIL.ImageFont (a classic build never imports it); not wired into
#   direct.py / preflight / the chunker yet (B2d/B2e)
#   REF_PX 2048, PROBE_PX 64, SLACK_LATIN 0.04, SLACK_INDIC 0.08, KERN_FLOOR_EM 0.06 (per word), SIZE_STEP 4.0,
#   UNCOVERED_EM 1.0 (estimate only), NOTDEF_PROBE U+10FFFF, SEPARATORS (JS \s plus U+200B, minus the no-break spaces)
#   errors: TextFitError(RuntimeError), FaceError(TextFitError) (a face cannot be read or its axes differ from the
#   table's: never a fallback)
#   text model: ignorable(ch); graphemes(text) (Indic conjuncts kept whole; coarser than ICU for Tamil and Kannada);
#   words(text) -> ((word, separator), ...) (= motion/text.js splitWords); tracking_count(clusters, tracking_em)
#   faces: FaceKey(path, wght, axes); load_face(key, size=REF_PX) (LRU; Layout.BASIC; axes checked and set);
#   face_key(face, weight, web_dir=None) (wght clamped to the face's range); has_glyph(key, ch) (cmap presence via the
#   .notdef signature); advance_em(key, text); every Pillow call holds one process RLock
#   stack_for(fonts, role, language); measure(text, *, fonts: faces.Fonts, role 'display' | 'body' | 'mono', weight,
#   language='', tracking_em=0, web_dir=None) -> Measured(text, words, stack, weight, tracking_em; .reliable,
#   .uncovered, .width(word, size, bias='conservative' | 'raw' | 'optimistic', clusters=None), .sep(word, size))
#   Word(text, clusters, em, sep_em, sep_units, kern_pos, kern_neg, kern_bounded; .reliable, .indic, .slack);
#   Cluster(text, key, indic, uncovered; .drawn, .em, .reliable); kern_bounds(clusters) -> (wider, narrower, bounded)
#   widths: raw = advance + letter-spacing; conservative = advance x (1 + slack) + KERN_FLOOR_EM + kern_pos +
#   letter-spacing; optimistic = max(0, advance x (1 - slack) - kern_neg) + letter-spacing
#   fitting: word_kinds(m, width, min_size, bias) -> ('break' | 'whole' | 'either', ...); layout(m, size, width,
#   max_lines, min_size, bias) -> Layout(size, lines, widths, whole, fits); sizes(lo, hi, step=4) (hi down, lo last);
#   fit(m, *, width, size, max_lines, prefer_lines=None, step=4, bias='conservative') -> FitResult; fit_text(text, *,
#   width, size, max_lines, role, weight, fonts, language='', tracking_em=0, prefer_lines=None, step=4,
#   web_dir=None); fit_slot(text, spec: templates.SlotSpec, *, fonts, language='', width=None (overrides spec.w where
#   the template's text box is narrower: board_generic's kicker is w - 30), step=4, web_dir=None); clear_caches()
#   FitResult(size, lines, widths, estimate, fits, reliable, uncovered, width): fits True | False when reliable (Latin
#   text the kerning table bounds), None otherwise (Indic, combining sequences, uncovered characters, unbounded runs:
#   an estimate, never blocking); .stored() -> {"size", "lines": line count} (the proposed §6.7 text_fit entry, for
#   certified results only; the stage reads text_fit[slot].size as a size cap)
```

## Repository scripts (B2b)
```python
# scripts/gen_third_party_notices.py — python scripts/gen_third_party_notices.py [--check]   (CI after npm ci)
#   render(lock_path=LOCK, node_modules=NODE_MODULES) -> str; runtime_packages(lock, direct) -> [Package(name,
#   version, licence, required_by, direct)] (every non-dev package-lock entry except EXCLUDED_SCOPES @types/*);
#   licence_texts(pkg, node_modules) -> [(file, text)] (top-level LICENSE* / LICENCE* / COPYING* / NOTICE* /
#   COPYRIGHT*, else FALLBACK_SOURCES: seedrandom's README section, javascript-natural-sort's source header)
#   VENDORED: (Vendored(package, version (reviewed at), source, served, components, notices, credits, licence_file,
#   module_table), ...): every file vendor.mjs serves (SERVED_ELSEWHERE: the fonts, fonts.css, manifest.json);
#   Component(name, version (the one the bundle holds), licence, contained, texts (scripts/third_party_licences/*.txt
#   or a pointer to a package section), markers, modules, notices); check_vendored(entry, text, path) fails on a
#   version drift, a missing marker, a p5 module (browserify_packages(text)) no component lists, or an unacknowledged
#   copyright statement (COPYRIGHT_STATEMENT)
#   NoticeError(RuntimeError); main(['--check']): exit 1 when stale, 2 when it cannot generate (a package missing, at
#   another version than the lock, or without licence text; a VENDORED check failing); output byte-deterministic,
#   packages sorted, LF (.gitattributes)
```
```js
// scripts/vendor_faces.mjs — vendorFaces(faces /* faces.gen.js FACES */, nodeModules, out) -> written[]: copies each
//   B2b face and its family's LICENSE into web/vendor/fonts/dynamic/<id>/; throws on a version other than the pin, a
//   LICENSE without OFL_TEXT ('SIL OPEN FONT LICENSE Version 1.1'), a unicode-range differing from the package's
//   unicode.json, or a path outside the family folder. scripts/vendor.mjs imports FACES and calls it after the
//   classic fonts, and copies mathjs/lib/browser/math.js.LICENSE.txt beside math.js
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
factory.VIDEO_PROVIDERS = ("veo", "omni", "fake")   # MEDIA_LABELS["omni"] = "Google Gemini Omni"; both need a Gemini key
#   Settings.video_providers (VIDEO_PROVIDERS) replaces video_provider + video_fallback_providers when set; else a chain
#   that names veo gets "omni" appended (Settings._video_order; Settings.auto_video_backup == "omni"), so media_chain
#   already holds the effective order. The appended successor is tried only while Veo is unavailable (assets
#   _generate_video: Veo's 404/410/account refusal now or in its "unavailable" cooldown); a named omni backup always.
video.omni.OmniVideo(settings, *, client_factory=None, sleep=None, poll_interval_s=10.0, max_attempts=None)   # "omni", paid,
#   resumable, reports_sending; Interactions API (client.aio.interactions create/get/cancel, SDK retries off);
#   .model (OMNI_MODEL), billed_seconds() = ESTIMATED_SECONDS (10.0); Usage(provider="omni", operation="video",
#   seconds = video output tokens / VIDEO_TOKENS_PER_SECOND (5792)); 404/410 on create -> ProviderUnavailable;
#   one genai client per key and event loop (gemini_common.GeminiCaller(per_loop=True)); generate/resume hold
#   GeminiCaller.loop_lease(), whose last holder in a loop closes that loop's clients in it
#   VeoVideo.model (VEO_MODEL); Veo's 404/410 on create (model no longer served) -> ProviderUnavailable, nothing billed
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
base.ModelUnavailable(ProviderUnavailable)   # the provider no longer serves the configured model: HTTP 404 / 410 on a
#   request naming it (retired, renamed, misspelt; for OpenAI also a model the key may not use: 404 model_not_found);
#   nothing billed, never retried. Raised by tts.gemini.GeminiTTS.synthesize (GEMINI_TTS_MODEL) and
#   tts.openai.OpenAITTS.synthesize (OPENAI_TTS_MODEL) after that one request (MODEL_GONE_STATUSES = (404, 410));
#   the asset build then narrates with Edge (pipeline assets.fallback_voice_config)
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
#   schemas.features.NewerDocumentError -> 409 "newer_document" (+ document, schema_version, reader_version)
screenplay_service.require_features(sp, settings, *, prefix=("body", "screenplay"), already=None) -> None
#   422 "feature_unavailable" (+ features) when sp uses a feature settings.dynamic_features does not enable;
#   already() returns the stored screenplay, whose features stay writable (PUT, lint, preview: the stored version;
#   import: prefix ("file",), no already). unavailable_features(sp, enabled, *, already=None) -> {feature: [loc]}
screenplay_service.asset_uses(sp) -> list[AssetUse]   # every key with its FIELD_RULES rule (classic + the Dynamic
#   clip / overlay / music / narration fields) -> asset_key_errors / authorize_asset_keys (422) and
#   strip_unauthorized_asset_keys (import: optional keys cleared, an overlay or clip scene without its clip removed)
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
#   (+ Veo or Gemini Omni in the video chain; Dynamic options when present, read with getattr: options.voice
#   {premium_allowed, any_provider, narrator "provider:voice"} and options.music {source, ai_opt_in} add the paid
#   voice and music payers, incl. the voices' calibration)
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
routers.renders.render_presentation(version, settings) -> {"style": "dynamic", "format", "captions_burn"} | None
#   the Dynamic presentation a render would use (stored screenplay has a presentation and DYNAMIC_ENABLED is on in
#   THIS process; schema defaults "landscape" / true), None = classic; GET .../render/preflight "presentation"
routers.renders.RenderBody.job_options(*, dynamic=False) -> dict   # burn_captions: None (unset) is left out when
#   dynamic (the presentation decides, motion.job.caption_modes), else stored as false; an explicit value always kept
main.create_app(settings: Settings | None = None) -> FastAPI ; main.app
```

## Frontend modules
```js
// web/js/player/player.js (owner: player)
export class Player { constructor(root, opts); load(timeline); play(); pause(); seek(s); seekScene(i);
  on(evt, fn); destroy(); states(sceneIndex) /* [{t,key}] */; renderState({sceneIndex, t}) /* -> {state_key, media_rect, panel_media_rect, media_fit} */; renderIntro({t});
  get captionSize /* 's'|'m'|'l': the classic captions' size, else the viewer's kept preference */;
  get captionSizeAdjustable /* false with a Dynamic stage: its captions keep their format's size */;
  captionBottom /* stage px below a Dynamic stage's caption band (48 in 16:9, 680 in 9:16), null for classic */;
  setCaptionSize(size) /* with a Dynamic stage only the preference is kept */ }
  // events include 'blocked' (narration play() refused without a user gesture; the player pauses with reason 'blocked')
  // a stage that is not 1920x1080 (a Dynamic reel) marks the root: data-stage-shape ('portrait' | 'landscape' |
  //   'square') and --ap-stage-w / --ap-stage-h (stage px), read by player.css .watch-player and studio.css .preview-box
  //   (the `ap-shaped-box` size container); removed when the player goes back to 1920x1080
export const MAX_STAGE_FAILURES = 20   // distinct Dynamic stage failures ('scene|message') reported per timeline
// web/js/player/controls.js — captionLift(stageBottom, barTop, scale, captionBottom = CAPTION_BOTTOM) -> px (the lift
//   that keeps captions above the shown bar, from where they end); CAPTION_SIZE_FIXED_LABEL = 'Caption size: set by
//   this video' (the disabled size button while captionSizeAdjustable is false)
// web/js/player/audio.js — BackgroundMusic.hasTrack (getter: a usable music URL; without it nothing plays)
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
// web/js/studio/components/stage.js — stageChip(stage, opts), nextStepCard({stage, next, canDo, onAction, note, extra,
//   stageAction}) (stageAction: the stage's own next action when the page replaced `next`, for the workflow strip)
// web/js/studio/components/videoPreview.js — openVideoPreview(opts) (HTML5 <video>, source released on close)
// web/js/studio/lib/debug.js — showTechnical(loc?) (?debug in the page or hash query)
// web/js/studio/lib/visibility.js — pageHidden(doc?), onceVisible(fn, doc?) -> cancel
// web/js/studio/lib/names.js — nameKey, uniqueJoin(parts, sep), distinctTitles(items, opts), withSuffix(title, suffix)
// web/js/studio/views/editor/splitScene.js — splitProblem(sp, sceneId, beatIndex), canSplitType(scene),
//   splitScene(sp, sceneId, beatIndex) -> {screenplay, scene}   (pure; scene = the new second part; references to
//   moved items and beats follow them: companion entries, Beat.answers, presentation.hook; part B keeps the board's
//   sequence, never A's look)
// web/js/studio/lib/screenplayEdit.js — COMMON_SCENE_KEYS (every SceneBase field: a type change keeps them all, incl.
//   hidden, min_seconds, look, colour_is_content), BOARD_SCENE_KEYS (['board', 'sequence']: kept between board
//   types), BLANKABLE_KINDS (Set ['example_step', 'question']: kinds a beat may fill)
// web/js/render/render.js — stageSize(timeline) -> {width, height} (timeline.width x height, else 1920x1080);
//   applyStageSize(doc, size) sets --render-stage-width / --render-stage-height and the viewport at boot
// web/js/studio/errors.js — errorMessage(err, fallback?): plain words per API code (incl. newer_document,
//   feature_unavailable with the features' labels from detail)
// web/js/studio/views/editor/sceneMerge.js — mergeDrafts(base, mine, theirs), mergeScene(b, m, t) (field by field;
//   beats and board items by id)
// web/js/studio/views/editor/changes.js — HISTORY_LIMIT (200), editLabel(key), changesBySceneId(res),
//   fieldWords(fields), changeBadge(change), restorePosition(all, removed)
// web/js/studio/views/editor/timelineStrip.js — stripModel(timeline, sp), fallbackModel(sp), createTimelineStrip(opts)
// web/js/studio/lib/jobStages.js — RENDER_STAGES {classic: [screenshots, segments, mux, check, store], motion:
//   [preflight, capture, mux, check, store]}; renderEngine(job, seenStages) -> 'classic' | 'motion' (motion once
//   'preflight' or 'capture' is seen); stagesFor(job, seenStages); stageStates(job, seenStages) ('done' marks every step
//   done; an unknown stage never resets the steps already seen)
// web/js/studio/lib/renderOptions.js — dynamicRender(preflight) -> {format, captionsBurn} | null: the Dynamic
//   render of GET .../render/preflight "presentation" (null: classic, or no answer); the editor's and the project
//   page's render dialogs take their Dynamic defaults from it
// web/js/studio/components/shortcutsDialog.js — openShortcutsDialog(groups)
// --- Dynamic video, B1 -------------------------------------------------------------------------------------------
// web/js/motion/ (owner: motion) — pure ESM, no DOM at import time, no clock; index.js re-exports everything
//   easing.js  ease(name, x) (§4.8 tokens 'ease.enter' | 'ease.exit' | 'ease.move' | 'ease.hero' | 'linear' | 'pop',
//              CSS keywords, step-start/end, 'cubic-bezier(...)' strings or functions; unknown names throw);
//              cubicBezier(x1, y1, x2, y2) -> fn (CSS-exact, linear outside 0..1); EASINGS; POP_OVERSHOOT 1.08
//   track.js   track([{t, v, ease?}]) -> (t) => value (numbers, arrays, objects, colours mixed in OKLCH -> hex); a
//              keyframe's ease shapes the segment ARRIVING at it; equal t = a jump; held before/after; progress(t,
//              start, duration), interpolate, stagger(index, count, each, {from})
//   color.js   parseColor (hex, rgb(), hsl(), oklch(), oklab()), toOklch, fromOklch, toHex, mixOklch(a, b, p) -> css
//              (shorter hue arc, gamut-mapped as CSS Color 4), contrastRatio(a, b) (WCAG 2), composite(top, bottom)
//              (source-over in gamma-encoded sRGB, the maths of aadhi/compose/dynamic/colour.py composite)
//   text.js    segmentGraphemes(str, locale) (Intl.Segmenter; Indic conjuncts re-joined), splitWords(str, locale) ->
//              [{text, ws, index, wordLike}] (punctuation attached, NBSP keeps "10 V" together),
//              splitLines(str, maxWidth, measure, {locale, balance})
//   draw.js    strokeDash(pathLength, p, {reverse}) -> {dasharray, dashoffset, visible} (visible false at p = 0)
//   random.js  mulberry32(seed), hashSeed(...parts) -> uint32 (FNV-1a + murmur3 finaliser, documented in the file for
//              a Python port; integers and strings only)
//   count.js   countUp(from, to, p, {decimals, locale = 'en-IN', numberingSystem}) (never "-0", exact end at p = 1)
// web/js/dynamic/stage.js (owner: stage) — the one compositor of the live player and the render page
export function createDynamicStage(root, timeline, { mode /* 'live' | 'render' */, captions, reducedMotion, texIdle,
  frame, onInvalidate, renderTex }) -> { el, update(t, frameInfo?) /* live */, seek(frame) -> Promise<{key}> /* render */,
  info(), mediaPlan(f0, f1), textBoxes(), setLayers({captions, stage}), layers(), destroy() }
//   refuses a timeline without presentation.style 'dynamic'; templates TEMPLATES {board_generic}, backgrounds
//   BACKGROUNDS {mesh_drift, colour_block}; templates/board_generic.js mediaOf(scene, mode), variantOf(scene, mode),
//   mediaBoxMax(scene, zones, mode = 'render'), cardSpecs(scene, format); media.js shownIn(ref, mode)
//   stage.js syncClip(v, t, playing, frameInfo) (a live presenter clip: re-sought past LIVE_DRIFT, at the player's rate)
//   the stage and every scene set --dyn-ink / --dyn-page (the roles' ink and page colour) for dynamic.css
//   templates/types.js TemplateContext.onBackground {ink, ink2, bg (worst case against ink), bgs (every colour the
//   background shows), block}; a template is a TemplateModule (B2a, below; no ctx.cues: cues are stage-driven)
//   board_generic.js TITLE_BLOCK_SLOTS ['kicker', 'title'] (a chapter card's subtitle reports in 'title'; a scrim
//   planned for either covers the whole title block), SCRIM_ALPHA_HEX 'c7', scrimmedBg(scrim, onBackground, ink) (the
//   scrim composited over the background, worst case against ink), RING_SIZE 96, RING_GAP 24 (16:9: the title block
//   ends that far left of the ring), RING_TRACK_ALPHA {lightInk: 0.18, darkInk: 0.25}; a quiz question keeps its
//   rich-lite markup word by word (bold at weight 900, dynamic.css .dyn-title .rt-b)
//   media.js mediaFit(ref) -> 'cover' | 'contain' | 'contain_blur'; contain_blur: the medium over a blurred, darkened
//   copy of its own frame in a canvas BACKDROP_WIDTH 64 px wide (BACKDROP_BLUR 2, BACKDROP_BRIGHTNESS 0.6), stretched
//   over the box; backdropShown(sw, sh, bw, bh) (false when the medium covers its box to within 1 px a side: no
//   backdrop, canvas hidden); coverCrop(w, h, cw, ch); no other frame is planned for it. MediaView (live) seeks a
//   paused clip to the last requested time once it can ('loadedmetadata', 'seeked'), never twice for one time
//   transitions.js CROSSFADE_MIN 0.3, CROSSFADE_MAX 0.5, CROSSFADE_DEFAULT 0.45 (seconds; never longer than the scene)
// web/js/dynamic/render.js (owner: stage) — the render page web/render-dynamic.html, served at GET /render-dynamic:
//   window.aadhiStage = { version: 3,
//     ready,              Promise<{webgl}>: timeline fetched with the fragment token (as /render-frame), every font in
//                         use loaded, every TeX string converted, MathJax idle, frame 0 painted (without the per-chunk
//                         media frames); rejects with {code, message}; methods throw 'not_ready' before it
//     info()              {width, height, fps, frames, scenes: [{index, f0, f1}]} (f0/f1 = the engine's scene bounds)
//     mediaPlan(f0, f1)   [{key, asset_key, kind: 'video' | 'aadhi', srcFps, frames: [n...], size: [w, h], url,
//                         duration, end_behavior, trim_in}] for frames [f0, f1); a clip not started yet (a panel
//                         before its show_at) lists its first frame: the page paints it into the hidden panel
//     seek(frame)         Promise<{key}>: t = frame / fps; paints, waits for TeX, pictures and the media frames
//                         (/__frames/<key>/<n>.jpg, 10 prefetched; frame n = the picture at source time n / srcFps),
//                         then one animation frame; a font face the frame pulled in (document.fonts loading) is
//                         awaited and the frame measured and painted again; calls are serialised; any order; key =
//                         hash of every painted value
//     textBoxes()         [{x, y, w, h, role, critical, ink, bg, overflow /* px: too tall, past the column with
//                         the cards on screen with it, or clipped at the sides */, alpha, scene, slot?, size?, min?,
//                         weight?, text?}] of the frame on screen (roles: title, subtitle, label, body, chip, caption;
//                         during a quiz countdown also a non-critical label in slot 'ring', the countdown number);
//                         bg is opaque: for text over the title block's scrim, the scrim composited over the
//                         background; the scrim remedy (fixes.slots[slot].scrim, or B1's text_fit[slot].scrim)
//                         for 'kicker' or 'title' scrims the whole title block (the subtitle reports in slot 'title')
//     setLayers(spec)     {captions, stage} -> the layers; {stage: false, captions: true} = the caption-only layer on a
//                         transparent page; render mode starts with captions = presentation.captions_burn, and the
//                         engine and preflight set the render's own caption layer after every load
//     texConversions()    B2a, additive: the TeX conversions started after the strings the templates declare
//                         (their static tex()) were converted, the first frame's included, plus a declared string
//                         whose conversion failed and is retried; 0 = the declarations are complete; throws
//                         'not_ready' before ready }
//   after ready: setTimeout / setInterval throw (guard.js), each trip recorded in window.aadhiStageGuard.trips and
//   logged as "aadhi timer guard: ..."
//   B2a: ready rejects with code 'timeline_invalid' before any font loads when presentation.engine_version is above
//   engine.js ENGINE_VERSION; the page converts TeX through pageTexRenderer (one conversion per string, clones handed
//   out, never evicted, a failed conversion dropped so the next mount retries) and gives the stage the same converter
// --- Dynamic video, B2a (additive; DYNAMIC_VIDEO_DESIGN §4.2, §6.7, §7.3) --------------------------------------------
// web/js/dynamic/engine.js — no imports, no DOM: ENGINE_VERSION = 2 (= aadhi/compose/presentation.py);
//   engineVersion(timeline) -> presentation.engine_version when an integer >= 1, else 1 (as api.timelines.built_engine);
//   engineProblem(timeline) -> null | "the timeline was built by look engine N; this page draws up to 2 (reload ...)"
// web/js/dynamic/catalogue.gen.js — GENERATED (python -m aadhi.compose.dynamic.catalogue): export const CATALOGUE,
//   frozen all the way down; imported by core.js only; runtime reads geometry only (sourceScan CATALOGUE_RUNTIME)
// web/js/dynamic/core.js — ROLES = CATALOGUE.roles.colour (B1's 14, same order); BLOCK_ROLES = CATALOGUE.roles.block
//   ['block.base', 'block.highlight', 'block.ink', 'block.ink2', 'scrim']; ZONES from CATALOGUE.zones in B1's key
//   order, JSON-identical to B1, frozen deep; TYPE_FLOORS = CATALOGUE.type.floors; templateSlots(id) -> slot -> format
//   -> variant -> SlotSpec | null (own keys only); BLOCK_INK2_CONTRAST = 3, blockInk2(ink, bgs), SCRIM_ALPHA_HEX 'c7'
//   (moved here; stage.js and board_generic.js re-export them)
//   makeRoles(presentation, look) -> {mode, tokens, color(role), inkOn(bg | bgs), on(mode)}: color() takes ROLES and
//   BLOCK_ROLES (RangeError 'unknown colour role' otherwise); each block role is look.tokens' value when valid
//   (#RRGGBB; scrim #RRGGBBAA), per field and never by engine_version, else B1's derivation (base: dark families mix
//   the accent 38% toward bg, light keep it; highlight: base 35% back to the accent (dark) or 10% toward white (light);
//   ink: inkOn([base, highlight]); ink2: blockInk2(ink, [base, highlight]); scrim: `${bg}c7`); on(mode): the scene's
//   own roles for its mode, else presentation.content_alt's content roles with the scene's structure and block roles
//   (RangeError when content_alt is missing, incomplete or of the wrong mode, or the mode is unknown)
//   fixesOf(look) -> {safe, slots: {slot: {size: number | null, scrim: boolean}}}: fixes first, B1's text_fit /
//   variant second (size: the fixes value when a positive number, else the legacy one; scrim and safe: either channel)
// web/js/dynamic/state.js — chapterTitleAt(timeline, start) -> the last of Timeline.chapters (array order) starting
//   <= start + 1e-3, '' when none (the kicker rule, moved from board_generic)
// web/js/dynamic/stage.js — TEMPLATES: Record<string, TemplateModule>; templateFor(scene) (own keys only);
//   mediaBoxOf(scene, zones, mode, slot) -> {w, h}: the template's static mediaBox(scene, zones, mode, slot,
//   fixesOf(look)), the full stage when it is missing or invalid; mediaPlan uses it with each entry's slot;
//   ctx.media(ref, {slot?, start?, alt?, box?}): a clip's box always from mediaBoxOf, a picture may pass its own;
//   createDynamicStage throws TypeError with code 'timeline_invalid' for a classic timeline or a newer engine
// web/js/dynamic/templates/types.js — TemplateModule {id, build(ctx), layout?(ctx), update(ctx, t, state),
//   texts?(ctx), focusRect?(ctx, target) -> {rect: {x, y, w, h}, shape: 'rect' | 'ring' | 'bar', radius} | null (whole
//   px), itemEl?(ctx, itemId) -> Element | null, dimGroups?(ctx) -> Element[], anchor?(ctx) -> {el, rect} | null};
//   static mediaBox?(scene, zones, mode, slot, fixes) -> {w, h} | null, static tex?(scene, look, fmt) -> [{tex,
//   display}]; TemplateContext has no `timeline`, gains `chapter` (chapterTitleAt) and media: MediaOptions;
//   renderTex is the render page's pageTexRenderer in render mode (counted, never evicted), richtext's cached
//   MathJax (400 strings) in live mode and tests, and is passed to every mountTex / renderRich / texNode call
// web/js/dynamic/templates/board_generic.js — kickerOf(scene, chapter); texOf(scene) (B1's TeX field list);
//   MIN_TEXT / MIN_LABEL from TYPE_FLOORS, BODY_WEIGHT from templateSlots('board_generic'); CARD_RADIUS 24,
//   FRAME_RADIUS 28, FIGURE_BOX_WIDTH 1200, FIGURE_SLOT 'figure'; default export adds focusRect (targets <item>,
//   <item>:<part>, option:<i>, panel...; radius 24, 28 for panel), itemEl (the card body; in 9:16 a split
//   misconception's every target names its myth card, B2c), dimGroups ([title block, frame?, ...cards]), anchor
//   (null), mediaBox (slot 'figure': 1200 x figureMax, else mediaBoxMax), tex (texOf); slotTuning reads fixesOf;
//   the title-block scrim is roles.color('scrim')
// web/js/dynamic/backgrounds/colour_block.js — blockColor(roles) / blockColors(roles) read roles 'block.base' /
//   'block.highlight'
// web/js/dynamic/render.js — timelineTex(timeline): the union over scenes of templateFor(scene).tex(scene, look, fmt),
//   first occurrence kept; pageTexRenderer(convert) -> {renderTex, markDeclared(), conversionsAfterDeclared()}
//   (boot calls markDeclared() once the declared strings are converted, before the stage builds frame 0);
//   STAGE_VERSION stays 3
// web/js/player/player.js — load() rejects a live or preview Dynamic timeline of a newer engine with TypeError code
//   'timeline_invalid' before building or resizing anything (imports ../dynamic/engine.js statically)
// --- Dynamic video, B2b (additive; DYNAMIC_VIDEO_DESIGN §4.6, §7.3, §7.4) -----------------------------------------
// web/js/dynamic/faces.gen.js — GENERATED (python -m aadhi.compose.dynamic.faces): export const FACES, frozen; data
//   only (sourceScan rule 'faces-data'), read in the stage by fonts.js only (and by scripts/vendor.mjs at install)
// web/css/dynamic-fonts.css — GENERATED: the B2b @font-face rules (Poppins, Lexend, Atkinson Hyperlegible Next,
//   Anek <Script>; new family names only); linked after dynamic.css by render-dynamic.html, index.html, watch.html
//   (not by the classic render.html); scanned like dynamic.css
// web/js/dynamic/fonts.js — the stage's type, mirroring faces.py:
//   B1_DISPLAY_FACES {outfit: 'Outfit', inter: 'Inter'}; isB2Fonts(fonts) (a string `indic`); stageRoles(presentation)
//   -> {display, body, mono, indic} (= faces.stage_roles; own-property lookups); indicFamilies(group, language)
//   fontStacks(presentation, language) -> {display, body, mono} CSS stacks (moved from stage.js, which re-exports it):
//   B1 form = B1's code except the own-property lookup of the display id (an Object.prototype name now gives Outfit);
//   B2 form = built from the table (display: display face, body face, Indic families; body: body face, Indic
//   families; mono: mono face; the lecture's script first)
//   stageFontRequests(timeline) -> FontRequest[] {family, weight, sample}: B1 form -> render/fonts.js
//   fontRequests(timeline), unchanged; B2 form -> the body, display and mono families' `requests` weights (each family
//   once, in that order) with LATIN_SAMPLE, then the Indic group's family for each detected script (sorted by script)
//   with that script's sample + LATIN_SAMPLE. For the resolver's fonts: display outfit = fontRequests exactly;
//   display inter = fontRequests without the Outfit faces (no stack holds Outfit)
// web/js/dynamic/render.js — boot preloads stageFontRequests(timeline) (was fontRequests) before ready
// web/js/render/fonts.js — exports LATIN_SAMPLE (no behaviour change)
// web/js/shared/types.js — TimelineFonts typedef {display?, body?, mono?, indic?}
// web/js/player/fonts.js ensureFonts (live and Studio) is unchanged: it preloads B1's faces only (B2e: preload
//   stageFontRequests for Dynamic timelines once direct.py names other faces)
```
