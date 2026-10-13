# api-jobs-infra: aadhi/api/** (all routers + generation, errors, screenplay_service, timelines, deps, serializers, static, storable, sse, util, upload_io, quota, analytics_service), aadhi/jobs/** (worker, reader_version, queue, context, events, heartbeat, lease, limits, runner, inline, cleanup, base, util), aadhi/storage/**, aadhi/security/**, aadhi/auth/**, aadhi/legacy/**. Also read (outside slice, because the slice depends on them): aadhi/assets_auth.py, aadhi/schemas/features.py, the render_video handler in aadhi/compose/video.py (lines 1-60, 120-170, 380-525, 660-690, 900-1031), aadhi/compose/motion/job.py (all), stage_page.py header/route section, config.py Dynamic settings, models.py kinds/statuses. Repo root for all relative citations: C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt


## summary
This slice is the HTTP and durable-job backbone of Aadhi EduEngine v2. aadhi/main.py:88-110 mounts 21 routers (health, auth, meta, projects, versions, version_actions, changes, visual_review, renders, videos, exports, jobs, uploads, library, shares, analytics, usage, keys, admin, render_internal, pages) plus the /media router and the /web and /branding static mounts. Middleware order (main.py:245-260, outermost first) is SecurityHeaders, then CORS (only when origins are set), CSRF, BodySizeLimit, UnhandledError, then the routes. Every error is the envelope {detail, code, **extra} (api/errors.py). Authorisation is owner-or-admin, and a refusal returns 404, never 403 (api/deps.py:55-86). Writes are atomic compare-and-set UPDATEs on revision/status that also refuse while a version-mutating job is active. Helpers only flush; the endpoint commits, then wakes inline workers (generation.py:428-442). Jobs are rows in `jobs`, claimed with one atomic UPDATE. Every write is lease-fenced on (id, locked_by, attempts, status='running') (jobs/lease.py:61-68). A process-wide heartbeat thread, a reaper and restart recovery keep jobs alive or recover them. Handlers are registered with @job_handler and loaded from 4 modules (jobs/base.py:166-171). There are 7 job kinds (config.py:43-51); the 4 version-mutating ones are exclusive per version through the partial unique index uq_jobs_active_version. Storage is content-addressed (storage/assets.py): blob keys are unique per production, MIME types are allow-listed, and the paid kinds video,image take claims across processes. Security covers path-based CSP, CSRF via the X-Aadhi-CSRF header plus an Origin check, body limits, trusted-proxy client IP, in-memory rate limits, Fernet encryption of saved keys, and magic-byte upload validation. Auth uses HS256 session JWTs (token_version revocation) and purpose-bound scoped tokens ('render'). Legacy converts v1 lectures and projects.db into v2 screenplays. For Dynamic video, the slice has the B0 safety layer: the 409 newer_document handler, a worker pre-check of schema_version, 422 feature_unavailable write gates driven by DYNAMIC_FEATURES, asset authorisation extended to the clip/overlay/music/narration keys, and payer detection for voice/music budgets. It also has the B1 serving layer: the /render-dynamic page route (404 while DYNAMIC_ENABLED is off), DYNAMIC_ENABLED applied when a timeline is served (timelines.current_timeline, with dyn= in the ETag), and the render_video engine switch (compose/video.py:999-1003 into compose/motion/job.py). B2 and B3 have no API, job or storage surface in this slice yet. No Dynamic generation options, look/voice/music endpoints, gallery route, audio uploads or new GC kinds exist. The code has no TODO, FIXME or NotImplemented markers.

## contracts
All paths below are relative to C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt.

ERROR ENVELOPE: every error is {"detail", "code", **extra} (api/errors.py:90-98). Validation errors are pydantic-style lists [{loc, msg, type}]. Codes in use:
- 409: newer_document (extra: document, schema_version, reader_version; errors.py:201-211), revision_conflict (+current_revision), version_busy (+job_id), job_in_progress (+job_id), timeline_stale, all_scenes_hidden, render_preflight (+items), action_unavailable, engine_not_configured, no_screenplay, not_awaiting_review, revert_invalid, confirm_paid_required, job_not_retryable, conflict.
- 422: validation, feature_unavailable (+features list; screenplay_service.py:496-520).
- 429: budget (Retry-After until UTC midnight), rate_limited, render_busy / render_queue_full (+limit, Retry-After 60).
- 503: render_unavailable.
- 403: csrf, forbidden, password_change_required, api_keys_disabled, feature_disabled.

ROUTES (Dynamic and render related):
- POST /api/versions/{vid}/render (renders.py:170-236). Body RenderBody {burn_captions=False, include_intro=True, soft_subtitles=null, allow_degraded=True}, extra=forbid. Returns 202 {render, job}. Creates a Render row, then enqueues render_video with payload {render_id, burn_captions, include_intro, soft_subtitles?}.
- GET /api/versions/{vid}/render/preflight (239-266). Uses the classic compose.preflight on the STORED timeline: {version_id, has_timeline, timeline_stale, blocking, items, quality}.
- GET /api/renders/capabilities (269-282).
- GET /api/versions/{vid}/renders (285-299), each with preview_url.
- GET /api/renders/{rid}/download?file=video|srt|vtt (302-337).
- GET /api/videos (videos.py:39), with qa_ok from Render.options.qa.ok.
- GET /api/render/timeline (render_internal.py:53-80). Only a scoped 'render' Bearer token; claims vid (required), rid, include_intro.
- Pages (pages.py): /render-frame (57-60) and /render-dynamic (63-69, 404 unless DYNAMIC_ENABLED; app CSP + Referrer-Policy no-referrer; the token is in the URL fragment), plus /, /watch/{token}, /preview/{id}, /sandbox/p5.
- Timeline serving: GET /api/versions/{vid}/timeline (versions.py:146-160, ETag + If-None-Match, private no-cache), POST /api/versions/{vid}/timeline/preview (163), GET /api/public/watch/{token} (shares.py:134-154).
- Screenplay writes: PUT /api/versions/{vid}/screenplay (183-253), POST /api/versions/{vid}/lint (256-281), POST /api/versions/{vid}/scenes/{sid}/revert (changes.py:87), POST /api/versions/{vid}/scenes/{sid}/visual (visual_review.py:176).
- POST /api/projects/import (projects.py:232-288).

DYNAMIC_ENABLED SERVE RULE (timelines.py:110-138):
- Stored classic build and flag off: served as stored.
- Stale build (built_revision != revision): served as stored, unless the stored timeline is Dynamic and the flag is off. Then it is rebuilt from the current screenplay.
- Current build: rebuilt only when (flag AND screenplay.presentation is not None) != stored.presentation.style=='dynamic'.
- The ETag includes dyn=<flag> (timelines.py:80).
- Applied in versions.get_timeline:157, shares.public_watch:142 and render_internal._render_timeline:37. NOT applied in exports.chapters_txt:80 or renders preflight:159/255.
- The motion worker reuses api.timelines.resolve_timeline/signed_urls (compose/motion/job.py:70-90), so aadhi/api/timelines.py is a contract shared by the API and the workers.

FEATURE GATES:
- DYNAMIC_FEATURES names are frozen in B0: presentation, items_v3, cuts, overlays, clip, voice_override (schemas/features.py:54-62).
- Setting: config.py:366-377 (unknown name fails at start-up; empty by default).
- Enforced in PUT screenplay (versions.py:203), preview (172), lint (271), API import (projects.py:247, loc prefix 'file') and the import_legacy JSON job (legacy/jobs.py:106-111, FatalJobError code feature_unavailable).
- A feature the stored screenplay already uses stays writable (screenplay_service.py:476-493).
- Not gated: revert, Visual Review writes, duplicate, and the generation/regeneration/translate jobs.

NEWER_DOCUMENT:
- READER_SCHEMA_VERSION=3. NewerDocumentError is deliberately not a ValueError, so pydantic and `except ValidationError` do not swallow it (features.py:75-90).
- API: the handler is registered at errors.py:263. Explicit pre-write checks: PUT reads the old screenplay plus require_readable_manifest (versions.py:200-201), revert (changes.py:103-106), Visual Review (visual_review.py:86-93). Any read through safe_screenplay or validate_stored also raises it, giving 409.
- Workers: pre-check before the handler (worker.py:335-338; reader_version.py, which also checks payload source_version_id). Loader-raised errors map to failed/newer_document without retry (worker.py:404-405).

ASSET AUTH:
- FIELD_RULES (assets_auth.py:65-77) is the frozen B0 table. asset_uses + _dynamic_scene_uses (screenplay_service.py:68-123).
- authorize_asset_keys (422 asset_key.unknown / asset_key.kind) is called by PUT, preview, revert and Visual Review.
- API import strips refused keys, dropping overlays and clip scenes (screenplay_service.py:198-275), then record_asset_refs on kept keys (projects.py:266).
- record_asset_refs is called by uploads (uploads.py:73), the render job (compose/video.py:457-460) and pipeline dbops (dbops.py:125).

JOB KINDS AND PAYLOADS (ALL_JOB_KINDS config.py:43-51; VERSION_MUTATING_KINDS models.py:356; JOB_STATUSES/ACTIVE models.py:357-358):
- generate_lecture: {source_document_id, options, base_revision, review_source?} (projects.py:167-174). The continuation adds resume_state and base_revision (version_actions.py:264-266, 361-363).
- regenerate_scene: {scene_id, instructions, base_revision}.
- build_assets: {scene_ids|null, base_revision, confirm_paid_scene_ids?}.
- translate: version_id is the target; {source_version_id, target_language, translate_board, tts_voice}.
- render_video: {render_id, burn_captions, include_intro, soft_subtitles?} (RenderPayload compose/video.py:126-150).
- import_legacy: {legacy_db_path, owner_fallback?} | {json_asset_key}. No API enqueuer; admin-only retry.
- cleanup: {dry_run, force_assets, scan_documents}.
- Any payload may carry budget_usd (context.py:283).
- confirm_paid_scene_ids is stripped on retry (queue.py:767).

JOB ERROR CODES: newer_document, budget, personal_key_rejected, result_not_persisted, unexpected_cancel, no_handler, invalid_job, retries_exhausted, stale, worker_lost, retried, cancelled, lease_lost. Render-specific: timeline_stale, timeline_missing, empty, asset_missing, render_invalid, render_failed, workspace, dynamic_disabled, render_unavailable, disk_full, preflight_blocked, render_page_http.

RENDER TOKEN: scope 'render', claims {vid, rid?, include_intro?}, key HMAC(JWT_SECRET, 'aadhi-scoped:render') (auth/tokens.py:55-57, 111-145). Motion TTL 4 h (compose/motion/job.py:42).

STORAGE:
- Storage keys: <assets|private>/<kind>/<key>/<random>.<ext> (storage/assets.py:142-144).
- Public kinds are listed in models.py:244-247. Only the MIME types in EXT_BY_MIME (assets.py:71-87) can be stored.
- Uploads accept the extensions in security/uploads.py:59-75.

SECURITY HEADERS:
- CSP strings: headers.py:44-67. Permissions-Policy: headers.py:23-27.
- CSRF requires the X-Aadhi-CSRF: 1 header plus an allowed Origin or Sec-Fetch-Site for cookie requests. /api/auth/login is exempt (main.py:77).
- Body caps: upload paths at bodylimit.py:34.

SETTINGS (config.py:384-389): DYNAMIC_ENABLED (bool, env-level), RENDER_MOTION_WORKERS, RENDER_CPU_SLOTS, RENDER_SLOT_DIR, MOTION_X264_PRESET, MOTION_X264_CRF.

## conventions
Paths are relative to C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt.

ROUTERS:
- Each module declares `router = APIRouter(prefix=..., tags=[...], dependencies=RATE_LIMITED)`, a per-user limit (deps.py:105-112).
- Exceptions: public or token routes (shares/analytics use per-route deps; render_internal, pages, health and media have none).
- Endpoints are sync `def`, so FastAPI runs them in its threadpool. They are `async` only when awaiting the network (keys test, library describe, analytics ingest), and those push DB work through asyncio.to_thread.

AUTHORISATION:
- Only load_project/load_version (deps.py:55-86) decide access; denial is a 404 via not_found.
- Admin-only routes use AdminUser.
- Library items are always the requester's own.

ERRORS:
- Raise ApiException(status, code, detail, **extra) or validation_error.
- Domain exceptions map centrally (errors.py:252-275).
- Never echo input (clean_validation_errors); redact with settings.redact.

WRITES:
- One atomic UPDATE ... WHERE <expected state> RETURNING, or compare_and_set. Screenplay writes add `~exists(active VERSION_MUTATING job)` (versions.py:217-231).
- Helpers flush only; the route commits via commit_and_notify.
- Order for paid work: ensure_budget, then enqueue, then charge_generation_rate (generation.py:333-369), so a 409 never burns quota.
- Before storing, use StorableBody/ensure_storable. On read, use validate_stored/json_safe (legacy NaN/surrogates are tolerated).

NEW SCREENPLAY WRITE PATHS must call, in order:
1. safe_screenplay (old) and require_readable_manifest
2. require_features(..., already=lambda: old)
3. authorize_asset_keys
4. ensure_storable
5. lint and merge_issues

This is the PUT pattern at versions.py:192-215.

JOBS:
- Handlers are `@job_handler("kind")` async functions. They must be idempotent: content-addressed assets, CAS on base_revision, ctx.assert_lease in the same transaction.
- Blocking work goes through asyncio.to_thread or ctx.session().
- Raise FatalJobError(msg, code=...) for permanent failures, RetryableJobError for transient ones, BudgetExceeded, JobCancelled, AwaitingReview.
- progress/log/record_usage never block.
- Queue transitions are fenced by the lease (lease.py:61-68) and settle the version/render in the same transaction (queue.py:281-347).
- A new job kind needs: ALL_JOB_KINDS (config.py:43), models ALL_JOB_KINDS/VERSION_MUTATING_KINDS only if it mutates (that also changes the uq index text at models.py:367-376), HANDLER_MODULES (base.py:166), compose WORKER_KINDS, and docs.

ASSETS:
- compute_key/bytes_key/media_key build the content address; Produced(data|path, mime, ...); AssetStore.put / get_or_create.
- Every asset a project gets needs record_asset_refs.
- New kinds go into models.PUBLIC_ASSET_KINDS (a string column, no migration) and into cleanup.GC_ASSET_KINDS in the batch that produces them.
- Paid kinds belong in GENERATION_CLAIM_KINDS (config.py:127).

SERVE-TIME SWITCHES (MASCOT_CUES, DYNAMIC_ENABLED) must enter timeline_etag's identity (timelines.py:60-88) and be applied in resolve_timeline/current_timeline. The render job must rebuild with the same setting.

STYLE:
- Module docstrings document behaviour and status codes in prose, with British spelling and teacher-facing message text.
- Comments cite design sections ("design §6.7") and batches.
- Lines are ~120 chars. Type hints use `from __future__ import annotations`; `# noqa: BLE001` marks deliberate broad excepts with a reason.

TESTS:
- tests/api (app_env/client fixtures, factories), tests/jobs (make_worker).
- Names read as sentences (test_the_page_is_hidden_while_dynamic_is_off, tests/api/test_render_dynamic_route.py:53).
- Fakes keep everything offline.

DETERMINISM-RELEVANT:
- Stored timelines hold keys only; URLs are filled per request.
- build_timeline is pure, so the API, the render page endpoint and the render job rebuild identical timelines.

CONFIG: each setting is documented inline in config.py, and .env.example is generated (scripts/gen_env_example.py).

## dynamic_status
Paths are relative to C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt.

B0 IN THIS SLICE: present and wired.
- 409 newer_document envelope (errors.py:201-211, 263).
- Pre-write refusals in PUT, revert and Visual Review (versions.py:200-201, changes.py:103-106, visual_review.py:86-93).
- Worker pre-check plus loader mapping (worker.py:335-338, 360-371, 404-405; jobs/reader_version.py). Tests: tests/jobs/test_newer_document.py and tests/api/test_schema_v3_api.py.
- DYNAMIC_FEATURES gates on PUT, preview, lint, API import and the legacy JSON job (screenplay_service.py:471-520; legacy/jobs.py:104-111).
- Asset authorisation extended to clip-scene clip, overlay clip, presentation music and beat voice_override, with batched lookups, import stripping that drops overlays and clip scenes and fixes chapters/continues_from, and record_asset_refs (screenplay_service.py:68-275; projects.py:248-266; uploads.py:73).
- regenerate_scene refuses clip scenes with 409 action_unavailable (version_actions.py:124-126).
- Budget payers for Dynamic voices/calibration and AI music (generation.py:240-300). These read opts.voice and opts.music through getattr, but GenerationOptions (pipeline/base.py) has NO voice/music/mode/format fields yet, so this logic is dormant until B3/B4.
- Omni counted on the Gemini key (generation.py:298).

B1 IN THIS SLICE: present.
- /render-dynamic route, 404 while off (pages.py:63-69); tests at tests/api/test_render_dynamic_route.py.
- DYNAMIC_ENABLED applied when served, with the ETag dyn= (timelines.py:103-138, 80); used by versions.get_timeline, shares.public_watch and render_internal; tests at tests/api/test_dynamic_flag_serve.py.
- Engine switch in the render job (compose/video.py:999-1003 into compose/motion/job.py:100-205), which stores Render.options["qa"] (engine motion, schema, preflight, motion stats). videos._qa_ok reads qa.ok, which motion_qa sets (compose/dynamic/container.py:478-491).
- The render token is unchanged (same scope and claims). Workspace sweeps cover motion workspaces (same RenderWorkspace).

DIVERGENCES AND HALF-DONE ITEMS (code vs docs/DYNAMIC_VIDEO_DESIGN.md):
1. B1 calls DYNAMIC_ENABLED an "admin flag", but it is an env-level Settings field (config.py:384), not a runtime admin toggle (site_settings is B4). Nothing detects a mismatch between API and workers; OPERATIONS.md:164-170 only says to set it alike.
2. Through POST /render, presentation.captions_burn is never honoured. RenderBody.burn_captions is a plain bool defaulting to False and is always placed in the payload (renders.py:50, 57), and the Studio always sends the checkbox (web/js/studio/views/projectDetail.js:302, editor.js:1624). So motion/job.py:63-64 never falls back to presentation.captions_burn (default True, schemas/screenplay.py:799), contrary to §2.6 and the motion job docstring (motion/job.py:13-15, 58-61).
3. The render dialog's preflight API (GET .../render/preflight) is classic-only and reads the stored timeline. The Dynamic preflight (compose/dynamic/preflight.py) runs only inside the job; per B1 notes its remedies reach only the MP4. The B4 "render dialog with preflight fixes" is not started.
4. §6.7 / B0 tests say foreign keys are "stripped by import". The API import strips them, but the import_legacy JSON job (legacy/jobs.py:91-120) does not strip and does not record AssetRefs.
5. /api/meta exposes neither DYNAMIC_ENABLED, DYNAMIC_FEATURES nor any Dynamic catalogue (meta.py:138-208). The Studio has no server signal for Dynamic availability.
6. The /render-dynamic 404 gate is not exclusive: the same HTML is served by the static mount at /web/render-dynamic.html (static.py:133-148 allows any web file except tests/node_modules/dotfiles).
7. GET /api/renders/capabilities, the inline start_render check (renders.py:163-188) and Worker.log_capabilities (worker.py:662-675) all use the classic capability check (browser=True). The motion branch checks browser=False (motion/job.py:113), and nothing in the API reports motion-engine readiness.

NOT PRESENT (B2/B3/B4/B5/B6 scope):
- Dynamic GenerationOptions and the form; GET /api/dynamic/look-candidates; the scope endpoint and series.
- POST /api/versions/{vid}/look/colours|layouts; the PUT /screenplay response with screenplay + ETag (B5).
- tus endpoints and the normalize_clip job (B6); stock proxies.
- voice_sample / music endpoints; an audio upload purpose; the template gallery page route.
- Studio Permissions-Policy (camera/microphone/display-capture); new asset kinds in GC_ASSET_KINDS; music in generation claims.
- RenderBody format, draft or second-version options; analytics events for live/MP4 differences.

## gaps_todos
Paths are relative to C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt. The slice has no TODO, FIXME, XXX or NotImplemented markers (grep over api, jobs, storage, security, auth, legacy). The gaps are features that are wired but dormant, or missing pieces.

1. GenerationOptions lacks voice and music (and mode, format, look). generation.py:254-284 reads them through getattr, so those payer branches never run (dormant until B3/B4).
2. _PAID_TTS_KEYS treats elevenlabs as server-key only (None) and has no azure entry (generation.py:241). B3 adds personal ElevenLabs keys and Azure, so this must change. _PAID_MUSIC_KEYS has only lyria (246).
3. cleanup.GC_ASSET_KINDS (jobs/cleanup.py:61) has none of music, voice_sample, calibration, clip, clip_proxy, clip_sprite, waveform, thumbnail or narration_upload. This is by design: each joins in its batch.
4. `calibration` is in neither PUBLIC_ASSET_KINDS nor PRIVATE_ASSET_KINDS (models.py:244-249). storage_key_for will default it to private/ (storage/assets.py:143), but it is undeclared.
5. Generation claims cover only video,image by default (config.py:127; ClaimConfig storage/assets.py:202). Paid music (Lyria) is not claimed across processes.
6. jobs/limits.py:29-45 has no 'music' process limit.
7. Uploads accept only image/video (uploads.py:25-31; library reuses ALLOWED_MIMES at library.py:46,129). security/uploads.py:59-75 accepts audio only as mp3/wav, and EXT_BY_MIME (storage/assets.py:71-87) stores only audio/mpeg and audio/wav. There is no API path to create the `music`, `upload` (audio) or `narration_upload` assets that FIELD_RULES already authorises (assets_auth.py:75-76).
8. /api/meta has no Dynamic section (meta.py:138-208).
9. No gallery route. A web/dynamic/gallery.html would be served publicly by the static mount with no gate (static.py:60, 133-148).
10. Permissions-Policy stays all-off for camera, microphone and display-capture (security/headers.py:23-27). B6 needs per-page overrides; page_response's `extra` (pages.py:20-34) is the hook, because the middleware never overrides endpoint headers.
11. Render preflight API is classic-only (renders.py:155-160, 239-266). There is no API to run or show the Dynamic preflight.
12. RenderBody has no format, draft, second-version or destination fields (renders.py:45-60). B4 work.
13. Analytics EventIn rejects unknown event data (analytics.py:74-103). B4/B9 live-player flags (low-end fallback, reduced motion, music control) need new ANALYTICS_EVENTS and validators.
14. exports.chapters_txt (exports.py:74-83) and the render preflight use the stored timeline, not current_timeline, so they ignore the DYNAMIC_ENABLED serve rule.
15. The import_legacy JSON path (legacy/jobs.py:91-120) skips strip_unauthorized_asset_keys and record_asset_refs, and no API enqueues this job kind.
16. Test coverage that exists for this slice's Dynamic pieces: tests/api/test_render_dynamic_route.py, test_dynamic_flag_serve.py, test_asset_auth_dynamic.py, test_schema_v3_api.py, test_batch4_regressions.py; tests/jobs/test_newer_document.py; tests/core/test_legacy_v3_gate.py.
17. Missing tests I could not find: RenderBody burn_captions vs presentation.captions_burn; the render retry admission race; the import_legacy JSON job with foreign asset keys.

## bugs_or_risks
Paths are relative to C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt.

1. **(Medium, behaviour) A Dynamic lecture's caption policy is ignored for API renders.**
   - renders.py:50 `burn_captions: bool = False`, and job_options always writes it (renders.py:57). The Studio always sends it (projectDetail.js:302, editor.js:1624).
   - compose/motion/job.py:63-64 uses presentation.captions_burn (default True, schemas/screenplay.py:799) only when burn_captions is absent, which never happens through the API.
   - Result: every Dynamic MP4 rendered from the Studio is clean (soft mov_text) unless the teacher ticks the box. This contradicts §2.6's default (WhatsApp/Drive, burned captions).
   - Fix shape: make burn_captions `bool | None = None` and omit it from job_options when None.

2. **(Medium, performance) Serve-time rebuilds of whole timelines.**
   - When the stored build's engine differs from DYNAMIC_ENABLED plus presentation, current_timeline (timelines.py:110-138) calls build_timeline on every request that misses the ETag cache. It does so on every GET /api/public/watch/{token} (shares.py:142, which has no ETag at all) and on every /api/render/timeline call (render_internal.py:37-50).
   - This persists until the teacher rebuilds. After flipping the flag, every Dynamic-capable lecture is rebuilt per request in the API process.
   - B2 moves direct.py, kinetic.py and textfit measurement (Pillow and fonts) into the build, which makes this CPU-heavy, and the API image then needs the same fonts as the worker.
   - Also: the ETag identity (timelines.py:69-82) has no build or engine version, so a deploy that changes build_timeline output can give 304s for stale cached rebuilt timelines.

3. **(Medium, deployment) API and worker DYNAMIC_ENABLED can disagree.**
   - The worker builds with its own settings (compose/video.py:432-435, flag checked at motion/job.py:110-112). The API serves the player, /render-dynamic and /api/render/timeline with its own settings (render_internal.py:43-50 rebuilds with API settings).
   - If the API flag is on and the worker's is off, the classic /render-frame page can receive a Dynamic timeline from /api/render/timeline. If the worker's is on and the API's is off, /render-dynamic 404s and the job fails as render_page_http (stage_page.py:254-256).
   - Only documented (OPERATIONS.md:164-170); nothing enforces it.

4. **(Low-medium) The render retry path skips start_render's version-row serialisation.**
   - jobs.py:59-64 checks active_render_job before check_admission, with no lock_version_for_render, and does not re-check timeline_stale.
   - render_video is not covered by uq_jobs_active_version (models.py:363-376). A concurrent POST /render plus retry, or two retries of different failed renders of one version, can produce two active renders of the same version. They share no workspace (per-render), so the cost is double CPU, not corruption.

5. **(Low-medium) import_legacy JSON job keeps unauthorised asset keys** (legacy/jobs.py:113-117 calls create_project_from_screenplay directly).
   - Foreign keys stay in the screenplay. Because no AssetRef rows are recorded, the next PUT /screenplay for that project fails with 422 asset_key.unknown (screenplay_service.py:154-187) until the teacher removes them.
   - The timeline would still resolve URLs for keys that exist, so another project's media could appear.
   - Mitigated: no API route enqueues import_legacy (only ADMIN_KINDS retry, jobs.py:45, 206-207).

6. **(Low) Gaps in the worker's schema_version pre-check** (reader_version.py:49-55).
   - `as_integer()` on JSON: on SQLite a marker stored as a string "4" or a float 4.0 comes back non-int and is not detected. On PostgreSQL a non-integer string raises and is caught as "no refusal".
   - This is defence in depth only: check_reader_version in the loaders accepts digit strings and integral floats (features.py:93-113), and the worker maps the loader error to newer_document.

7. **(Low) The refusal paths in Worker.process persist the outcome with ctx=None and never close or finalise the context** (worker.py:327-338). Events already buffered by DBJobContext.open, such as the "Using your own API key" line (context.py:348-351), are dropped. No thread leaks, because start() was never called.

8. **(Low) Previews disagree with the serve rule.** GET .../render/preflight and chapters.txt use the stored timeline (renders.py:159, 255; exports.py:80). With the flag off and a stored Dynamic build, preflight items and chapter times describe the Dynamic timeline while the MP4 and player are classic, and vice versa.

9. **(Low) The /render-dynamic flag gate is cosmetic.** The page is also at /web/render-dynamic.html via WebStaticFiles (static.py:133-148), with the default Referrer-Policy rather than no-referrer. The token lives in the fragment, so nothing leaks.

10. **(Low, B3 risk) CSP blocks cross-origin audio fetches.** connect-src is 'self' only, and _media_sources adds origins only to img-src and media-src (security/headers.py:61-66). With S3 and no CDN, timeline media URLs are presigned bucket URLs (timelines.py:37-39), so any live-player fetch() of music or SFX from them is blocked. MediaElementAudioSourceNode output is silent without bucket CORS and crossorigin="anonymous". The design acknowledges this (§5.12, line 1362).

11. **(Low) Motion engine readiness is not reported.** Inline start_render (renders.py:183-188) and /api/renders/capabilities test only classic render capability. A host that can render classic but lacks the motion engine's needs (chrome-headless-shell, Playwright driver, disk) accepts the request; it then fails in the job (motion/job.py:113-121).

## b2_b3_touchpoints
Paths are relative to C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt.

B2 (visual system, templates, direct.py, events.py, kinetic.py, themes, textfit, zones, fonts, icons):

1. **Timeline serving.**
   - aadhi/api/timelines.py:110-138 (current_timeline) and render_internal.py:33-50 rebuild timelines in the API process. Anything B2 puts inside build_timeline also runs there, per request, when the flag and the build disagree, so the API image needs the fonts and Pillow WOFF2 support textfit uses.
   - Add a build or theme version (for example a themes.json hash or ENGINE version) to timeline_etag's identity (timelines.py:69-82) if B2 output can change without a version write.
   - Serve-time transforms belong in resolve_timeline (141-156); the motion worker reuses it (compose/motion/job.py:70-90).
2. **New asset-key fields.** Any key-bearing field B2 adds (background or texture images, the Aadhi frame-extraction cache when referenced from the screenplay, brand assets) must join FIELD_RULES (aadhi/assets_auth.py:65-77) and asset_uses / _dynamic_scene_uses (screenplay_service.py:68-123). Otherwise PUT and import neither authorise nor strip it.
3. **Feature gate.** B2 data rides the frozen `presentation` feature (schemas/features.py:54-62); require_features is already called at versions.py:172, 203, 271 and projects.py:247.
4. **SceneLook.lock / settled looks.** Today PUT /screenplay returns {version, issues, stale_scenes} (versions.py:248-253). B5 changes the contract to return the screenplay plus an ETag when looks are filled. merge_issues and scene_hash (screenplay_service.py:278-284, 455-468) feed staleness.
5. **Gallery.** web/dynamic/gallery.html, if placed under web/, is served unauthenticated by WebStaticFiles (api/static.py:133-148; deny-list at :60). For "dev-only", add a gated route in api/routers/pages.py (pattern at :63-69, page_response :20-34) and/or extend WEB_DENIED_SEGMENTS.
6. **Static serving of fonts and themes.** api/static.py:26-52 already maps .woff2/.woff/.ttf/.otf/.json/.svg. Unversioned paths are no-cache and vendor paths are immutable only when versioned (static.py:143-148). Licence .txt files are served as text/plain.
7. **CSP.** font-src 'self', img-src 'self' data: blob: (+CDN/S3), script-src 'self' (no wasm-unsafe-eval), worker-src 'self' blob: (security/headers.py:61-67). The render page and the players use the app CSP (pages.py:63-69). Icons and fonts must be same-origin; SVG icons as `<img>` or inline are fine.
8. **Studio discovery.** /api/meta (api/routers/meta.py:138-208) has no Dynamic flags or catalogue; families, templates or look candidates would go there or in a new router added to aadhi/main.py:88-110 with dependencies=RATE_LIMITED.
9. **Render options.** RenderBody (api/routers/renders.py:45-60) needs burn_captions Optional, so presentation.captions_burn and kinetic captions apply (compose/motion/job.py:58-67); clean_pill and bold_outline caption styles follow that choice.
10. **Preflight in the API.** GET .../render/preflight (renders.py:239-266) is classic; any B2 "safe zone / text fit" pre-checks shown before rendering need a Dynamic branch here.
11. **No job or queue changes.** direct.py, events.py and kinetic.py run inside build_assets/generate (VERSION_MUTATING_KINDS models.py:356).

B3 (audio: personas, casting, history, calibration, music providers, mix.py, live-player audio):

1. **Budget pre-check** (api/generation.py).
   - GenerationOptions must gain `voice` (premium_allowed, any_provider, narrator "provider:voice") and `music` (source "auto|generated|...", ai_opt_in) with exactly these names, because _voice_payers and _music_payers read them by getattr (generation.py:254-284).
   - Update _PAID_TTS_KEYS (:241): ElevenLabs gains personal keys, so map it to "elevenlabs" instead of None and fix _tts_usable (:249-251); add azure.
   - Extend _PAID_MUSIC_KEYS (:246) when ElevenLabs Music lands (B7).
   - _media_billed_to_server (:287-300) decides whether the daily budget applies.
2. **Keys.** api/routers/keys.py works off credentials.KEY_PROVIDERS and KEY_PROVIDER_LABELS (keys.py:31-45, 72-75), so adding elevenlabs/azure to the credentials module surfaces them automatically. jobs/context.py:316-351 resolves the job owner's keys, and :541-565 prices and bills calibration and sample usage (billed_to by provider name).
3. **Audio uploads.**
   - Purposes: api/routers/uploads.py:23-31 (PURPOSE_KINDS, ALLOWED_MIMES are image/video only) and library.py:46,129.
   - Formats: security/uploads.py:59-75 (_EXT audio is only mp3/wav; _check_audio :420-438); storage/assets.py:71-88 (EXT_BY_MIME / MIME_BY_EXT; the /media route uses MIME_BY_EXT, api/routers/media.py:26-29). Add m4a/aac/ogg/opus/flac here if accepted.
   - The upload cap paths are in security/bodylimit.py:34.
   - Uploaded or generated music must call record_asset_refs (assets_auth.py:80) with kind `music` or `upload` to satisfy FIELD_RULES["Presentation.music.asset_key"] (assets_auth.py:75).
4. **Asset kinds.**
   - music and voice_sample are already public (models.py:244-247). `calibration` is in neither list (models.py:244-249), so storage_key_for makes it private (storage/assets.py:142-144); declare it in PRIVATE_ASSET_KINDS.
   - Add music, voice_sample and calibration to GC_ASSET_KINDS (jobs/cleanup.py:61). Note that cleanup also protects keys referenced anywhere in version documents (cleanup.py:105-129, 208).
5. **Paid music generation.** Add `music` to GENERATION_CLAIM_KINDS (config.py:127; ClaimConfig storage/assets.py:199-216) so Lyria runs once across worker processes; get_or_create (storage/assets.py:427-482) handles in-flight de-duplication and owner-scoped failures. Optionally add a 'music' ProcessLimit (jobs/limits.py:29-45).
6. **Live-player audio.**
   - CSP media-src 'self' blob: +CDN/S3 and connect-src 'self' only (security/headers.py:61-66; _media_sources :32-41). Web Audio of music needs `<audio crossorigin="anonymous">` plus bucket CORS when S3 has no CDN (timelines.signed_urls :37-39). SFX fetched with fetch() must stay same-origin (/branding or /web).
   - The /media route sets CORP same-site (media.py:38); BrandingStaticFiles allows mp3/wav/ogg (static.py:54).
7. **Serving.** Music and voice sample URLs reach the player through resolve_timeline / resolve_urls (timelines.py:141-156) and media_url (:42-52) like other media; the ETag rules apply.
8. **Render.** The master mix and loudness live in compose/dynamic (mix.py) inside the render job. Results land in Render.options["qa"] via _finish_render / record_qa (compose/video.py:440-454; compose/dynamic/container.py:494-499) and surface through api/serializers.py:336-354 and videos._qa_ok (videos.py:33-36). A failed audio master maps to FatalJobError or RetryableJobError codes (motion/job.py:93-97).
9. **Meta and previews.** Persona and voice lists come from providers.factory.available_tts_providers through /api/meta (meta.py:51-57, 186). Voice-sample or music-preview endpoints would be new routers in main.py:88-110, with budget checks via ensure_budget (generation.py:303-330) and usage rows via usage.service.record_usage, as library describe does (library.py:298-311).
10. **Analytics.** A music Off/Low/Normal control in the player needs new ANALYTICS_EVENTS and EventIn data validators (api/routers/analytics.py:55-103).

## files
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/__init__.py (4): Package docstring only (app assembled by aadhi.main.create_app). || -
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/deps.py (119): Shared FastAPI deps and the single authz gate (owner or admin, not deleted, else 404). || valid_id:45; is_admin:50; load_project:55; load_version:70 (for_update locks the version row); get_app_settings:89 (app.state.settings); get_asset_store:95 (app.state.asset_store override for tests); user_rate_limit:105 / RATE_LIMITED:112 (API_RATE_LIMIT_PER_MINUTE per user); CurrentUser/PendingUser/OptionalUser/AdminUser/AppSettings/Store:114-119
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/errors.py (275): Error envelope, domain exception mapping, innermost 500 safety net. || STATUS_CODES:30; ApiException(status, code, detail, headers=, **extra):57; not_found:73; validation_error:78; envelope:90; clean_validation_errors:108 (strips echoed input); http_exception_parts:129 (honours .code/.extra/dict detail); _newer_document_handler:201-211 (409 newer_document + document, schema_version, reader_version); UnhandledErrorMiddleware:219; install_exception_handlers:252-275 (registers NewerDocumentError:263, JobInProgress 409, BudgetExceeded 429 budget + Retry-After to UTC midnight, UploadRejected, InvalidToken 401, IntegrityError 409 conflict, InvalidJobState 409, JobNotFound 404, Exception 500)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/generation.py (464): Helpers for endpoints that create versions or enqueue jobs: options parsing and sanitising, engine/provider checks, budget pre-check incl. Dynamic voice/music payers, rate tokens, version allocation, enqueue/commit. || MODEL_OVERRIDE_FIELDS:47; parse_options:63 (422 at options); request_keys:104 (resolve_keys personal>server>env); ensure_engine_configured:117 (422 engine_not_configured at options.llm_provider); ensure_image_provider_configured:138; sanitize_options:160 (strips non-admin / non-allow-listed / other-engine model overrides); version_options:181; ensure_engine_available:200 (409 engine_not_configured); complete_options:219; project_default_options:230; _PAID_TTS_KEYS:241 {gemini,openai,elevenlabs:None}; _PAID_MUSIC_KEYS:246 {lyria:gemini}; _tts_usable:249; _voice_payers:254-272 (getattr opts.voice.narrator 'provider:voice', premium_allowed+any_provider); _music_payers:275-284 (opts.music.source generated / auto+ai_opt_in); _media_billed_to_server:287-300 (veo/omni on gemini key); ensure_budget:303 (skipped only if engine AND all paid media are personal keys); ensure_generation_allowed:333; charge_generation_rate:359 (after enqueue; rollback on 429); allocate_version_number:372; create_version:380; enqueue_job:415; commit_and_notify:428; active_mutating_job:445; ensure_not_busy:460 (409 version_busy)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/screenplay_service.py (520): Asset-key authorisation (incl. Dynamic fields), import stripping, stale scenes, lint/issue bookkeeping, source chunk/ingest caches, DYNAMIC_FEATURES write gates. || AssetUse(loc,key,kinds,mime_prefixes,drop):45; asset_uses:68-100 (figures, side_panel/simulation/ai_video/interactive overrides, Presentation.music.asset_key:96-99); _dynamic_scene_uses:103-123 (clip of a clip scene (drop=scene), beats/reveal_beats overlays (drop=overlay), Beat.voice_override_asset_key; by attribute, any scene class); _IN_BATCH=500:126; _referenced_assets:129 (batched IN lists); asset_key_errors:154 (asset_key.unknown / asset_key.kind); authorize_asset_keys:181 (422); accessible_project_ids:190; strip_unauthorized_asset_keys:198; strip_asset_uses:238-275 (clears keys, deletes overlays/clip scenes deepest-last-first, removes scene ids from chapters and look.continues_from); stale_scenes:287; lint_screenplay:307; source_chunk_ids:346 / source_ingest:375 (LRU caches per extract key); issues_payload:426; served_issues:441; hidden_scene_ids:448; merge_issues:455; unavailable_features:476 (already= stored doc exempts features it already uses); require_features:496-520 (422 feature_unavailable, detail per feature + features=[names])
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/timelines.py (156): Serve-time timeline resolution, ETag, and the DYNAMIC_ENABLED serve switch (shared with the motion worker). || signed_urls:37 (s3 and no CDN); media_url:42; timeline_etag:60-88 (identity: id, has_timeline, updated_at, storage/CDN/media prefix, retired mascot/branding maps, cues=, dyn=:80; + time bucket for presigned URLs); etag_matches:91; built_dynamic:103 (presentation.style=='dynamic'); current_timeline:110-138 (rebuild via build_timeline when the engine the flag+screenplay want differs from the stored build; a stale build is served as built except a Dynamic one while the flag is off); resolve_timeline:141-156 (validate_stored, resolve_urls, applies MASCOT_CUES, json_safe)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/serializers.py (382): JSON shapes of API.md schemas with batched lookups; lesson stage; review checkpoints. || user_dict:36; review_stage:61 / review_stages:72; version_summary:86; render_facts:122; stage_fields:162; latest_render_dict:182; version_stage:194; review_checkpoints:203; project_summaries:253; source_dict:315; render_downloads:327; render_summary:336 (options incl. qa); render_summaries:357; share_url/share_dict:367-382
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/static.py (165): /web and /branding StaticFiles with explicit MIME, per-path cache, deny-list. || MIME_TYPES:26-52 (woff2/woff/ttf/otf/json/wasm/mp3/wav/ogg...); WEB_DENIED_SEGMENTS {tests,node_modules}:60; _VERSIONED_SEGMENT:63; WebStaticFiles:133 (allowed: everything except tests/node_modules/dotfiles; vendor+versioned path => immutable else no-cache); BrandingStaticFiles:151 (media types only, 1 week cache)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/storable.py (47): Refuse NaN/Infinity/lone surrogates in stored bodies. || StorableBody (wrap model_validator):27; ensure_storable:42
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/sse.py (36): SSE response wrapper that always closes the iterator. || SSE_HEADERS:16; closing:22; event_stream_response:34
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/util.py (99): Pure helpers. || utcnow:17; as_utc:22; iso:31; start_of_utc_day:39; canonical_json:45; sha256_hex:50; download_name:64; content_disposition:76; page_params:87 (limit 1..200)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/upload_io.py (119): Read multipart uploads with cap; probe image/video metadata. || read_upload:26 (413 too_large, 422 file.empty); MediaProbe:46; probe_image:54; probe_video:68 (ffprobe, protocol_whitelist file,pipe, 20 s timeout)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/quota.py (84): In-process daily per-key quotas for analytics ingest. || DailyQuota.consume:55; seconds_until_utc_midnight:30
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/analytics_service.py (239): Teacher analytics dashboard aggregation (SQL-side quiz first answers). || dashboard:176; _answer_groups:104; LOW_ACCURACY/HIGH_DROPOFF:31-32 (QuizScene only)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/__init__.py (1): empty package || -
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/renders.py (337): MP4 renders: start, preflight, capabilities, list, download; render admission. || RenderBody:45-60 (burn_captions: bool=False, include_intro=True, soft_subtitles: bool|None, allow_degraded=True; job_options always includes burn_captions); RENDER_ADMISSION_LOCK_KEY:41; lock_version_for_render:63 (no-op UPDATE RETURNING = row lock); active_render_job:86; active_render_counts:95; admission_lock:103 (pg_advisory_xact_lock); check_admission:111 (429 render_busy/render_queue_full + Retry-After 60); every_scene_hidden:131; scene_numbers:140; version_preflight:155 (classic compose.preflight on stored timeline); host_render_capabilities:163; start_render POST /api/versions/{vid}/render:170-236 (503 render_unavailable inline only, 409 timeline_stale/all_scenes_hidden/job_in_progress/render_preflight); render_preflight GET /api/versions/{vid}/render/preflight:239; render_capabilities GET /api/renders/capabilities:269; list_renders GET /api/versions/{vid}/renders:285; download_render GET /api/renders/{rid}/download:302
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/render_internal.py (80): GET /api/render/timeline for the render pages (scoped 'render' bearer token only). || _render_timeline:33-50 (include_intro bool + current build => build_timeline rebuild with API settings; else current_timeline (DYNAMIC_ENABLED applied) or stored); render_timeline:53-80 (claims vid required int, optional rid must belong to vid => 403, ?vid mismatch 403, deleted project/no timeline 404)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/pages.py (75): HTML pages from web_dir with no-cache + CSP. || page_response:20 (path containment, CSP kind, extra headers); / :37; /watch/{token}:43 (no-referrer); /preview/{version_id}:49; /render-frame:57 (no-referrer); /render-dynamic:63-69 (404 unless settings.dynamic_enabled; render-dynamic.html, app CSP, no-referrer); /sandbox/p5:72 (sandbox CSP)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/versions.py (281): Version detail, timeline (+ETag), preview, PUT screenplay, lint; newer-document and feature gates. || ScreenplayBody:44; ScreenplayPut:52; safe_screenplay:61 (ValidationError=>None; NewerDocumentError propagates => 409); safe_manifest:71; require_readable_manifest:80; require_version_language:88; get_version GET /{vid}:120; get_timeline GET /{vid}/timeline:146-160 (304 via ETag before loading; current_timeline); preview_timeline POST /{vid}/timeline/preview:163 (require_features:172, authorize_asset_keys:173, ValueError=>422 'timeline'); put_screenplay PUT /{vid}/screenplay:183-253 (busy 409, revision_conflict, old=safe_screenplay:200, require_readable_manifest:201, language, require_features:203, authorize:204, ensure_storable, lint+merge, CAS UPDATE with ~exists(active mutating job)); lint_version POST /{vid}/lint:256 (require_features:271)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/version_actions.py (488): Build, regenerate scene, plan/source review, duplicate, translate. || BuildBody:52; build_version POST /{vid}/build:60 (build_assets {scene_ids, base_revision}; 409 action_unavailable if all named hidden); regenerate_scene:108 (409 action_unavailable for non-PLANNABLE (clip) scene:124-126; regenerate_scene {scene_id, instructions, base_revision}); save_plan:203; approve_plan:239 (continuation generate_lecture with resume_state); save_source_review:285; approve_source:335; duplicate_version:380 (raw copy of docs, review sign-offs); translate_version:431 (target version + translate {source_version_id,target_language,translate_board,tts_voice})
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/projects.py (546): Projects list/create/import/detail/patch/delete/regenerate/source report. || create_project POST /api/projects:99 (multipart, generate_lecture payload {source_document_id, options, base_revision, review_source?}:167-174; AssetRef for source:146); _parse_import:177 (legacy detect, Screenplay.model_validate => NewerDocumentError 409); import_project POST /api/projects/import:232-288 (require_features prefix ('file',):247, strip_unauthorized_asset_keys:248, record_asset_refs(kept):266, build_assets enqueued); stored_options:291; get_project:304; patch_project:339; delete_project:373 (soft delete, cancel jobs, revoke shares); regenerate_project:406; source_report:499
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/changes.py (159): Teacher changes since generation; scene revert as one saved edit. || RevertBody:52; version_changes:63; scene_changes:71; revert_scene POST /{vid}/scenes/{scene_id}/revert:87-159 (newer stored manifest refused before write:106, authorize_asset_keys:116, no require_features, CAS UPDATE)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/visual_review.py (266): Visual Review reads, sign-offs and visual actions (new_version/choose_library/remove/retry). || _require_screenplay:86 (newer screenplay/manifest => 409 first); _url_signer:103; get_visual_review:139; put_visual_review:154; visual_action:176-238 (authorize_asset_keys:219, build_assets payload + confirm_paid_scene_ids, charge rate); _edited_scene:241
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/exports.py (83): Companion md/html, export.json (raw stored screenplay), chapters.txt (stored timeline). || companion_markdown:36; companion_html:51 (companion CSP); export_json:61; chapters_txt:74 (validate_stored on stored timeline, not current_timeline)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/jobs.py (239): Jobs list/detail/events/SSE/cancel/retry with access checks and retry admission. || GENERATION_KINDS:43; BUILD_KINDS:44; ADMIN_KINDS (import_legacy, cleanup):45; _admit_render_retry:59; load_job:67; _job_options:83; list_jobs:117; get_job:143; get_events:150; stream:169 (429 on TooManyStreams); cancel:187; retry:198-239 (engine/budget checks for generation/build, render admission, retry_job, Render row re-queued)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/uploads.py (105): POST /api/uploads teacher media (image/video only) + AssetRef + library. || Purpose Literal:23; PURPOSE_KINDS:25-30; ALLOWED_MIMES (png,jpeg,webp,gif,mp4,webm):31; upload_media:34 (record_asset_refs:73); _add_to_library:94
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/library.py (353): Per-teacher media library: list, upload, patch, delete, attach to project, suggestions, AI describe, admin media-cache stats. || list_library:70; upload_to_library:100 (uses uploads.ALLOWED_MIMES); patch_item:197; delete_item:209; attach_item:227 (own lectures only); library_suggestions:243; describe_item:314 (async, budget, usage recording); media_cache:347
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/media.py (73): GET /media/{storage_key}: public assets/ namespace only; local FileResponse or 302 presign. || INLINE_PREFIXES:22; media_mime:26 (storage.assets.MIME_BY_EXT); media_headers:32 (immutable, nosniff, media CSP, CORP same-site, attachment for non-media); build_media_router:45
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/meta.py (276): /api/meta for the Studio forms (per-user key availability), admin media providers and manim sandbox check. || features:138-154 (ai_video, manim, manim_freeform, generated_images, gifs, render, storage; no Dynamic flags); api_keys_meta:157; library_meta:162; generation_defaults:171; meta GET /api/meta:178-208; worker_cooldowns:216; media_providers:248; manim_sandbox:268
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/keys.py (245): Personal (/api/keys) and server (/api/admin/keys) API keys: write-only, test, audit. || ApiKeyBody:61; _provider:72 (KEY_PROVIDERS from credentials); list_personal_keys:153; save_personal_key:162; delete_personal_key:174; test_personal_key:189; list_server_keys:202; save_server_key:211; delete_server_key:223; test_server_key:239
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/shares.py (154): Share links and the public watch payload. || TOKEN_RE:32; resolve_share:54; create_share:70; list_shares:110; revoke_share:120; public_watch GET /api/public/watch/{token}:134-154 (rate limit per IP; resolve_timeline(current_timeline(...)); no ETag; atomic view_count)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/analytics.py (268): Player event ingest (share token or session) with layered quotas; dashboard. || EventIn:55-103 (typed data per event: quiz_answer, seek, pause{reason:blocked}; others no data); IngestBody:106; ingest_events POST /api/analytics/events:225; project_analytics:245
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/videos.py (104): GET /api/videos: the user's renders across lectures. || _qa_ok:33 (Render.options.qa.ok); list_videos:39
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/usage.py (42): Usage reports. || usage_me:26; usage_project:32; usage_all:39
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/health.py (49): /healthz, /readyz (db + storage). || healthz:21; readyz:27
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/auth.py (154): Login/logout/me/change-password. || login:54 (rate limit username|ip, dummy-hash timing, cookie, optional bearer); logout:91 (token_version bump); me:102; change_password:108
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/api/routers/admin.py (149): Admin user management with last-admin / self-lockout guards. || UserCreate:30; UserPatch:41; list_users:71; create_user:77; patch_user:102 (atomic guard:140-146)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/base.py (191): Job contract: exceptions, JobContext protocol, handler registry. || JobCancelled(reason):41; BudgetExceeded:49; RetryableJobError:53; FatalJobError(code):57; AwaitingReview(state):65; register_owner_scoped(JobCancelled,BudgetExceeded):80; JobContext Protocol:83-134; job_handler:142; HANDLER_MODULES:166-171 (pipeline.orchestrator, compose.video, legacy.jobs, jobs.cleanup); load_handlers:174
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/worker.py (702): Worker threads, outcome mapping, persistence with retries, maintenance (reaper, recovery, cleanup scheduling, host housekeeping). || import_handler_modules:100; Worker:119; resolve_kinds:188; start:198; stop:230; run_until_idle:264; process:314-358 (newer-document pre-check:335-338 => failed/newer_document, no handler run); _newer_document_refusal:360-371; _execute:381-422 (NewerDocumentError:404 => failed newer_document; FatalJobError => code; personal_key_rejected); _cancel_outcome:439; _error_outcome:459 (retry while attempts left); _persist:516; _fallback:555; _transition:568; log_capabilities:662 (classic render caps only); sweep_host_once:677; schedule_cleanup_once:694
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/reader_version.py (68): B0 pre-handler refusal of documents written by a newer build (one SQL query on schema_version markers). || NEWER_DOCUMENT=NewerDocumentError.code:28; _DOCUMENTS (screenplay, timeline, asset manifest):30; _version_ids (version_id + payload.source_version_id):37; newer_documents:45 (column['schema_version'].as_integer()); newer_document_refusal:59 (best effort; errors => None)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/queue.py (886): Durable queue: enqueue (exclusive for mutating kinds), atomic claim, fenced transitions, settle version/render, recovery, cancel, retry, summaries. || JobInProgress:48; JobNotFound:56; InvalidJobState:64; enqueue:133; _enqueue_exclusive:177 (ON CONFLICT DO NOTHING on uq_jobs_active_version); claim_next:212; has_claimable:250; settle_version:281; settle_render:326; touch:353; finish_job:393; fail_job:417; requeue_job:452; cancel_running:494; mark_awaiting_review:528; release_job:553 (max_attempts+1 refund); reap_stale:619; recover_own_host:683; request_cancel:734; ONE_JOB_PAYLOAD_KEYS {confirm_paid_scene_ids}:767; retry_job:770; complete_review:826; job_summary:846; active_job_for_version:867
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/context.py (891): DBJobContext: buffered, lease-fenced progress/log/usage, budgets in memory, per-user keys, poison-row resilience. || price_usage:96; spent_today:109; sanitize_usage:136; DBJobContext:245 (job_budget_usd from payload budget_usd or MAX_COST_PER_LECTURE_USD:283); open:316 (resolve_keys, logs personal key use); start/close/finalize:359-387; progress:471; log:506; provider_notice:524; check_cancelled:533; record_usage:541 (billed_to server|user); ensure_budget:567; flush:582; claim_owner:594; assert_lease:607; flush_sync:627
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/events.py (420): JobEvent serialisation and the SSE stream with caps, gap window, access rechecks. || TooManyStreams:44; event_dict:55; list_events:69; sse_message:81; default_can_access:92; _StreamSlots:108; _stream_body:272; JobStream:317; stream_job:378
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/heartbeat.py (169): One process-wide heartbeat thread for all leases. || HeartbeatService:39 (acquire/release refcount, beat_once:121, refresh_flags_once:144); heartbeat_service:167
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/lease.py (68): Worker ids and the fencing predicate. || BOOT_ID:20; HOSTNAME:21; Lease:33; make_worker_id:41; parse_worker_id:47; fence:61-68
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/limits.py (208): Process-wide FIFO semaphores usable across threads/loops. || LIMIT_NAMES (llm,tts,manim,render,image,video):29-30; limit_size:33; ProcessLimit:56; get_limit:159; configure_limits:168; acquire:189; acquire_sync:200
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/runner.py (75): Per-job event loop whose executor threads are abandoned (never awaited) on cancel. || run_job_loop:36; _cancel_leftovers:63
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/inline.py (64): WORKER_MODE=inline worker threads in the API process. || start_inline_workers:23 (schedule_cleanup, host_housekeeping); stop_inline_workers:45; notify_inline_workers:59
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/util.py (177): Strict JSON, redaction, truncation, DB error classification. || jsonable:57; redact_obj:102; redact_text:114; short_error:122; traceback_tail:132; is_connection_error:166; is_transient_db_error:173
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/cleanup.py (390): cleanup job: event/analytics retention, expired claims, host files, orphan asset GC; atomic daily scheduling. || GC_ASSET_KINDS (tts, scene_audio, manim, image, video, poster, screenshot, intermediate):61; GC_BLOCKING_KINDS:62; ORPHAN_ASSET_MIN_AGE_DAYS=30:63; _referenced_in_documents:105; _gc_assets:179 (guard in DELETE); run_cleanup:247; cleanup_job:330 (payload dry_run, force_assets, scan_documents); ensure_cleanup_scheduled:353 (INSERT...SELECT WHERE NOT EXISTS; priority 1000, max_attempts 1)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/jobs/__init__.py (17): Package map docstring. || -
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/storage/__init__.py (55): Storage factory and the per-process shared AssetStore. || build_storage:18; get_storage:29; get_asset_store:37; reset_storage_cache:51
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/storage/base.py (69): Storage protocol and key validation. || validate_key:20 (segments, <=8, Windows reserved); is_public_key ('assets/'):35; Storage Protocol:40
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/storage/local.py (82): Local filesystem storage with atomic no-overwrite writes. || LocalStorage:13 (_atomic_write:28; public_url/signed_url /media/<key>)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/storage/s3.py (317): S3/R2/MinIO storage with conditional puts and presigned URLs. || s3_media_origins:56 (for CSP); S3Storage:92 (_put_object:179 IfNoneMatch, 412 self-write check; signed_url:307 <=7 days)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/storage/assets.py (798): Content-addressed AssetStore: in-process de-dup, cross-process paid-kind claims with provider-operation hand-over, generation guard, MIME allow-list. || EXT_BY_MIME:71-87 (audio/mpeg, audio/wav, video/mp4, video/webm, png/jpeg/webp/gif, pdf, json, vtt, srt, txt, md, docx); MIME_BY_EXT:88; compute_key:95; bytes_key:103; normalize_prompt:108; media_key:117; storage_key_for:142 (assets/ if kind in PUBLIC_ASSET_KINDS else private/); Produced:147; register_owner_scoped:168; ClaimConfig (kinds default video,image):199; claim_owner:229; generation_guard:239; inherited_operation:273; record_claim_operation:280; AssetStore:307 (get:319, get_many:326, first_existing:337, url_for:349, local_copy:359, put:373, get_or_create:427, _produce:484, _claim_step:570)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/security/headers.py (123): CSP strings and the security-headers middleware. || PERMISSIONS_POLICY:23-27 (camera/microphone/display-capture=() ; autoplay/fullscreen=(self)); HSTS:28; _media_sources:32 (CDN or S3 presign origins); build_csp:44-67 (app: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data: blob: https://*.giphy.com +media; media-src 'self' blob: +media; connect-src 'self'; frame-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self' | sandbox | media 'sandbox; default-src none' | companion); csp_kind_for_path:70; SecurityHeadersMiddleware:80-123 (never overrides endpoint headers; nosniff, Referrer-Policy strict-origin-when-cross-origin, X-Frame-Options SAMEORIGIN, COOP same-origin, HSTS in production; media non-media types => attachment)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/security/csrf.py (110): CSRF for cookie-authenticated mutations. || CSRF_HEADER x-aadhi-csrf:29; SESSION_COOKIE_NAMES:31; allowed_origins:38 (BASE_URL, CORS_ORIGINS, loopback aliases); CSRFMiddleware:68 (exempt paths still refuse a foreign Origin)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/security/bodylimit.py (130): Request body caps (upload cap only for listed multipart paths). || DEFAULT_UPLOAD_PATHS ('/api/projects','/api/projects/import','/api/uploads','/api/library'):34; BodyTooLarge:38; limit_for:51; BodySizeLimitMiddleware:62
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/security/client_ip.py (85): Client IP honouring X-Forwarded-For only from TRUSTED_PROXIES. || is_trusted_proxy:53; client_ip_from_scope:61; client_ip:83
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/security/credentials_crypto.py (65): Fernet encryption of Studio-saved API keys (explicit key or HKDF from JWT secret). || CredentialUnreadable:30; encrypt_secret:54; decrypt_secret:59
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/security/ratelimit.py (179): In-memory sliding-window limits. || RateLimited (429 rate_limited + Retry-After):38; SlidingWindowLimiter:52; check_rate_limit:113; rate_limit dependency factory:136
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/security/redaction.py (87): Registry of runtime secrets for redaction. || register_secret:45; forget_secret:69; registered_secrets:76
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/security/strict_json.py (35): Strict JSON decoding (no NaN/Infinity/overflow). || loads_strict:30; finite_float:22
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/security/uploads.py (605): Upload allow-list with magic bytes and deep checks (polyglots, zip bombs, docx macros). || ALL_KINDS:56; _EXT:59-75 (png,jpg,gif,webp,mp4,webm,mp3,wav,pdf,docx,json,txt,md); UploadRejected:128; sniff:174; _check_image:356; _check_mp4:383; _check_audio:420 (mp3/wav only); validate_upload:539; safe_display_name:584
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/security/_asgi.py (58): ASGI helpers. || header:15; headers_all:23; normalize_origin:28; send_json_error:46
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/security/__init__.py (5): Package docstring. || -
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/auth/tokens.py (145): Session and scoped JWTs (HS256 only, 30 s leeway). || _scoped_key HMAC(JWT_SECRET,'aadhi-scoped:'+scope):55; create_session_token:83; decode_session_token:97; create_scoped_token:111 (reserved claims, ttl 1 s..7 d); decode_scoped_token:137
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/auth/deps.py (159): Auth dependencies (Bearer first, else cookie; token_version revocation). || token_from_request:72; user_from_token:84; resolve_auth:102; get_current_user_allow_pending:120; get_current_user:127 (403 password_change_required); get_optional_user:134; require_roles:146
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/auth/bootstrap.py (122): First-run admin bootstrap. || ensure_admin:54
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/auth/cookies.py (40): Session cookie (HttpOnly, SameSite=Lax, __Host- on https). || set_session_cookie:17; clear_session_cookie:31
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/auth/errors.py (65): AppHTTPException with code. || AppHTTPException:25; unauthenticated:53; forbidden:58; password_change_required:63
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/auth/passwords.py (183): bcrypt hashing and strength rules. || hash_password:114; verify_password:128 (dummy hash for unknown users); needs_rehash:150; check_password_strength:156
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/auth/service.py (68): Account ops shared by API/CLI. || find_user:14; authenticate:21; bump_token_version:40; set_password:51
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/auth/__init__.py (23): Re-exports. || -
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/legacy/jobs.py (135): import_legacy job (v1 projects.db or a JSON asset). || screenplay_from_json:33; parse_lecture_bytes:50; _db_path:58; _import_db:68; _import_json:91-120 (disabled_features gate:106-111 => FatalJobError feature_unavailable; NO asset-key stripping); import_legacy_job:123
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/legacy/convert.py (758): v1 lecture JSON (and the andryan fork) -> validated v2 Screenplay with warnings. || V1_SCENE_TYPES:47; is_legacy:121 (schema_version int>=2 => not legacy); _mascot_position:257; _common:285; _board_scenes:354; _quiz_scene:432; _convert_scene:494; _apply_edit:595 (hidden/min_seconds); convert_legacy:678 (writes schema_version 2); _validate_document:738
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/legacy/legacy_db.py (346): Import v1 projects.db read-only (users, projects). || UNUSABLE_HASH:48; warnings_to_issues:53; create_project_from_screenplay:91 (draft version, set_screenplay); _read_v1_rows:163 (mode=ro); latest_rows:196; _import_users:217 (v1 admin credential never imported); import_legacy_db:264
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/legacy/html_board.py (517): v1 board HTML -> typed board items (rich-lite). || parse_html:141; rich_text:231; BoardExtraction:259; extract_board:511
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/legacy/narration.py (173): v1 [SYNC]/[PAUSE] narration -> beat drafts. || BeatDraft:34; clean_narration:42; split_long:69; build_beats:105; cap_beats:142
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/legacy/panels.py (224): v1 side panels -> SidePanel kwargs. || model_3d_primitives:35; js_to_mathjs:41; adapt_manim_code:66; convert_side_panel:144
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/legacy/__init__.py (9): Re-exports. || convert_legacy, import_legacy_db, is_legacy
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/assets_auth.py (117): (outside slice, used by it) per-field asset rules and the one AssetRef helper. || FAMILY_MIMES:38; FieldRule:47; _CLIP {clip,upload,video}:61; FIELD_RULES:65-77 (5 classic + ClipScene.clip, Overlay.clip, Presentation.music {music,upload} audio, Beat.voice_override {narration_upload} audio); record_asset_refs:80 (ON CONFLICT DO NOTHING per 500, never commits)
- C:/Users/SUBASH/OneDrive/Documents/GitHub/Aadhi_prompt/aadhi/schemas/features.py (230): (outside slice, used by it) v3 feature names, reader-version marker, NewerDocumentError, usage walk. || READER_SCHEMA_VERSION=3:51; DYNAMIC_FEATURES:54-62; FEATURE_LABELS:65; NewerDocumentError (not a ValueError; code newer_document):75-90; check_reader_version:105; features_used:208; disabled_features:214; uses_v3:220; marked_version:225