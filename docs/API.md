# Aadhi EduEngine v2 — HTTP API

Base path `/api`. JSON in/out unless noted. Times are ISO-8601 UTC strings.

## Conventions

* **Auth**: session cookie (`__Host-aadhi_session` on https, `aadhi_session` on http dev; httpOnly,
  SameSite=Lax) set by login. `Authorization: Bearer <token>` is accepted for API clients that asked
  for a token (`issue_token: true` at login). Role and active status are re-read from the DB on every
  request.
* **CSRF**: every `POST/PUT/PATCH/DELETE` authenticated by cookie must send `X-Aadhi-CSRF: 1` and come
  from the same origin (`Origin`/`Sec-Fetch-Site` checked). Failure → `403 {"code":"csrf"}`.
* **Errors** (`aadhi/api/errors.py`): `{"detail": "<message>" | [pydantic errors], "code": "<machine code>",
  ...extra}` at the top level. Codes include `validation` (422; each `detail` entry has `loc`, `msg`,
  `type`, e.g. `engine_not_configured`), `unauthenticated` (401),
  `forbidden` (403), `password_change_required` (403, every endpoint except auth/me/change-password/
  logout while the flag is set), `csrf` (403), `api_keys_disabled` (403, saving a key, or testing
  a personal key, while saved keys are switched off), `feature_disabled` (403, a switched-off optional
  feature, e.g. the library's AI descriptions), `not_found` (404), `revision_conflict` (409, extra
  `current_revision`), `job_in_progress` (409, extra `job_id`), `version_busy` (409, extra `job_id`),
  `engine_not_configured` (409, extra `engine`),
  `timeline_stale` (409), `render_preflight` (409, extra `items`), `not_awaiting_review` (409),
  `source_unavailable` (409, the source extract can no longer be read), `no_screenplay` (409),
  `action_unavailable` / `no_visual` / `confirm_paid_required` (409, Visual Review; `action_unavailable` also for
  a build of only skipped scenes), `asset_missing`
  (409, a library item whose file is no longer stored), `too_large` (413),
  `unsupported_type` (415), `rate_limited` / `budget` / `render_busy` / `render_queue_full` (429,
  `Retry-After` header), `describe_failed` (502), `render_unavailable` / `unavailable` (503).
* **Storable JSON**: bodies that are stored (screenplay PUT / lint / preview, build, scene regenerate,
  plan, duplicate, translate, project PATCH, regenerate options, multipart `options`, imported files)
  are refused with 422 `validation` when they hold `NaN`, `Infinity`, an overflowing number (`1e999`)
  or a lone UTF-16 surrogate anywhere; `loc` ends with the JSON path of the value. Typed string
  fields refuse lone surrogates on every endpoint. Documents stored before this check are served
  with `null` for such numbers and U+FFFD for such characters (version detail, timeline,
  `export.json`, companion, chapters, project options), never a 500.
* **Roles**: `admin` (everything, all projects, server API keys), `editor` (own projects). Every
  user manages only their own personal API keys; nobody, admins included, can read or use another
  user's. Students never log in — they watch through share links.
* **Authorization**: every `/api/versions/{vid}/…` route resolves the version through
  `load_version(user, vid)` (owner or admin; project not deleted). Ids in bodies/queries must belong
  to the path project. Asset keys in a screenplay (`override_asset_key`, `poster_override_asset_key`,
  `SourceFigure.asset_key`) must be referenced by the project (`asset_refs`) and of an allowed kind.
  The media library (`/api/library`) is always the signed-in user's own: another user's item is 404
  for everybody, admins included, and library media go only into the user's own lectures.
* **Body limits**: JSON ≤ `MAX_JSON_BODY_MB` (5 MB); uploads ≤ `UPLOAD_MAX_MB`.
* **Lists**: `?limit=50&offset=0` → `{"items": [...], "total": N}`.

## Schemas

```jsonc
// User
{"id": 1, "username": "alice", "role": "editor", "must_change_password": false, "is_active": true,
 "daily_budget_usd": null, "created_at": "...", "last_login_at": "..."}
// VersionSummary
{"id": 7, "number": 3, "label": "", "status": "ready", "language": "en-IN", "revision": 4,
 "built_revision": 4, "timeline_stale": false, "has_timeline": true, "source_version_id": null,
 "issue_counts": {"error": 0, "warning": 3, "info": 1}, "created_at": "...", "updated_at": "..."}
// + "review_stage": "source" | "plan" only while status is "awaiting_review": what the review waits for
//   (the teacher's source check, or the plan); everywhere a VersionSummary appears (lists read it in one query)
// ProjectSummary
{"id": 3, "title": "Ohm's Law", "subject_name": "...", "unit_name": "...", "session_number": "Session 2",
 "session_title": "...", "language": "en-IN", "owner": {"id":1,"username":"alice"},
 "current_version": VersionSummary | null, "active_job": JobSummary | null,
 "created_at": "...", "updated_at": "...",
 "stage": "ready_to_render", "next_step": NextStep,   // the current version's lesson stage (see "Lesson stage")
 "latest_render": {"id": 2, "status": "succeeded", "built_revision": 4, "matches_current": true} | null}
// NextStep: one suggested action in plain words; href is a Studio route ("#/p/3/v/7/plan", "#/videos") or null
// when the action is a button on the project page
{"action": "render", "label": "Make the video", "href": null}
// JobSummary
{"id": 12, "kind": "generate_lecture", "status": "running", "stage": "script", "progress": 0.31,
 "message": "Writing scene 7/24", "project_id": 3, "version_id": 7, "error": null, "error_code": null,
 "cost_usd": 0.42, "attempts": 1, "created_at": "...", "started_at": "...", "finished_at": null}
// JobSummary.error_code of a failed job, e.g. "budget" (a cost limit was hit), "personal_key_rejected"
// (the provider refused the starter's personal API key - HTTP 401/403, or Gemini's 400
// API_KEY_INVALID / expired - for any call: scenes, translation, voices, images, AI video; never
// retried, never run on the server key instead; replace or remove the key under API keys, then
// retry), "cancelled", "error"
// JobEvent
{"id": 99, "job_id": 12, "created_at": "...", "level": "info", "stage": "script",
 "message": "...", "progress": 0.31, "data": {}}
// RenderSummary
{"id": 2, "version_id": 7, "status": "succeeded", "language": "en-IN", "built_revision": 4,
 "duration_s": 912.4, "chapters_text": "00:00 Introduction\n...",
 "downloads": {"video": "/api/renders/2/download?file=video", "srt": "...", "vtt": "..."},
 "job": JobSummary, "created_at": "..."}
// + "matches_current": bool where the render's version is known (GET /api/versions/{vid}/renders, POST .../render):
//   made from the version's current revision and its timeline is not stale ("Up to date" / "Out of date")
// + "preview_url": str | null on GET /api/versions/{vid}/renders: a streamable URL of a finished MP4 (served like
//   timeline media: a capability URL, or a short-lived presigned one on S3 without a CDN), null otherwise
// RenderSummary.options: {"burn_captions", "include_intro", "soft_subtitles"?,
//   "qa"?: {"ok", "expected_frames", "frames", "fps", "has_audio", "audible", "on_grid", "black_edges",
//           "color", "subtitle_tracks", "problems": [str], "warnings": [str], ...},
//   "warnings"?: [{"scene_index", "scene_id", "title", "reason", "message", "scene_number"?}]}   // scenes rendered with a fallback
//   (scene_index: the place in the timeline, which leaves skipped scenes out; scene_number: the 1-based place in the
//   screenplay, skipped scenes counted, as the editor numbers scenes; absent on renders made before it was added)
// Issue (aadhi/pipeline/base.py); "data" only when set, e.g. {"category": "timeout"} on manim.render_failed
{"code": "board.too_many_items", "severity": "warning", "message": "...", "scene_id": "s4",
 "beat_id": null, "source": "lint", "fixable": true}
```

## Auth

| Method & path | Body | Response |
|---|---|---|
| `POST /api/auth/login` | `{"username","password","issue_token"?: false}` | `200 {"user": User}` (+ `"access_token"` only when `issue_token`) and sets the cookie. 401 generic message. 429 rate limited (per username + client IP). |
| `POST /api/auth/logout` | – | `204`; bumps `token_version` (all sessions of the user end), clears cookie |
| `GET /api/auth/me` | – | `{"user": User}` |
| `POST /api/auth/change-password` | `{"current_password","new_password"}` | `204`; strength rules; bumps `token_version`; clears `must_change_password`; re-issues cookie |

## Meta

`GET /api/meta` (auth) →
```jsonc
{"version": "2.0.0",
 "languages": [{"code":"en-IN","label":"English (India)"}, ...],
 "tts": {"default_provider":"edge","providers":[{"id":"edge","label":"Microsoft Edge (free)","configured":true,
          "word_timings": true, "voices":[{"id":"en-IN-NeerjaNeural","label":"Neerja","language":"en-IN","gender":"Female"}]}]},
 "image": {"default_provider":"pollinations",                   // IMAGE_PROVIDER; providers: [] when it is none
           "providers":[{"id":"gemini","label":"Google Gemini / Imagen","configured":false,"paid":true},
                        {"id":"pollinations","label":"Pollinations","configured":true,"paid":false}]},
 "llm": {"provider":"gemini","configured":true,                  // server default engine (LLM_PROVIDER)
         "models":{"plan":"...","script":"...","critic":"...","fast":"..."},   // default engine's models
         "engines":[{"id":"gemini","label":"Google Gemini","configured":true,"key_source":"env",
                     "models":{"plan":"gemini-2.5-pro","script":"gemini-2.5-flash","critic":"...","fast":"..."}},
                    {"id":"openai","label":"OpenAI","configured":false,"key_source":null,"models":{...}},
                    {"id":"anthropic","label":"Anthropic Claude","configured":true,"key_source":"personal","models":{...}}],
         "override_allowlist": []},                              // admins only, else []
 "api_keys": {"enabled": true,             // STORED_API_KEYS_ENABLED (keys saved in the Studio)
              "personal_enabled": true},   // enabled and USER_API_KEYS_ENABLED
 "library": {"auto_save_generated": true,    // LIBRARY_AUTO_SAVE_GENERATED
             "ai_describe_enabled": false},  // LIBRARY_AI_DESCRIBE_ENABLED and an AI engine configured for this user
 "features": {"ai_video": false, "manim": true, "manim_freeform": true, "generated_images": true,
              "gifs": false, "render": true, "storage": "local"},
 "manim_templates": [{"name":"equation_steps","title":"...","description":"...","steps_hint":"..."}],
 "limits": {"upload_max_mb": 50, "max_source_chars": 400000,   // characters of any source generation reads
            "max_cost_per_lecture_usd": 5.0, "daily_budget_usd": 15.0},
 "generation_defaults": GenerationOptions}   // every field, also prefer_library_visuals (false)
```
`GET /healthz` → `{"ok": true}` (no auth). `GET /readyz` → DB + storage check.

`features.generated_images` / `features.ai_video` are true when any provider of the chain is usable
(`IMAGE_PROVIDER` / `VIDEO_PROVIDER` or a configured `*_FALLBACK_PROVIDERS` backup).
`features.render` with `WORKER_MODE=inline` is the full render check of this host (ffmpeg with
libx264/aac, ffprobe, Chromium), false exactly when `POST /render` would answer 503; with external
workers it is an advisory ffmpeg check of the API host.

**Image provider.** `GenerationOptions.image_provider` is `null` (the server's `IMAGE_PROVIDER`, then
its backups) | `"gemini"` | `"pollinations"`. `POST /api/projects` and `/regenerate` answer 422 at
`["options", "image_provider"]` (type `provider_not_configured`) when generated images are off on the
server or the chosen provider has no key for the requester. The build uses the choice first and adds
an `assets.image_provider_fallback` warning when it cannot.

**Library pictures.** `GenerationOptions.prefer_library_visuals` (default `false`, left out of stored
options while false, so stored options and checkpoints are unchanged) makes the build use a picture of
the lecture owner's library for a side panel's generated image instead of generating one: matched on
the image prompt alone (never the scene or panel title), with a score of at least 0.6
(`aadhi.library.AUTO_USE_THRESHOLD`) and the item's words covering at least half of the prompt's
(`AUTO_USE_MIN_COVERAGE`). One plan per build, in scene order: a picture serves one scene of the
lecture. Media generated for this lecture, and the picture the scene showed, never stand in (an edited
prompt makes a new picture; an unchanged one comes back from the cache). Never for a requested new
version, and only in a build the lecture's owner started (an admin's build of someone else's lecture
uses no library). The scene then carries an info issue `assets.library_visual_used`.
The Studio offers it ("Prefer pictures from my library") only when `/api/meta` has `library`.

**Keys are per user.** `llm.configured` (the default engine), `configured` on each `llm.engines`
entry and on each `tts.providers` entry, and `features` (`ai_video`, `generated_images`) reflect the
**current user's** resolved keys: their personal key, else a server key saved in the
Studio, else `.env` (see [API keys](#api-keys)). `key_source` says which one a lecture started now
would use: `"personal"`, `"server"` (saved in the Studio), `"env"` or `null` (no key). Engine
validation (422 / 409 `engine_not_configured`) uses the requesting user's keys the same way. The
daily-budget pre-check of the endpoints that start a job (429 `budget`) is skipped only when the
lecture's engine **and** every paid voice, generated-image and AI-video provider the job would use
(Gemini / OpenAI voices, Gemini images, Veo, including a Gemini or Veo backup in the provider chain
and the lecture's `image_provider`; free providers never count) run on the requester's
personal keys, because only then does none of that spend count toward the daily budget. A job that
would still bill server voices or images is refused up front instead of failing after the user
paid for its script.

**AI engine.** `llm.engines` feeds the Studio's *AI engine* dropdown. The offline engine
(`{"id":"fake","label":"Offline test engine"}`) is listed first only in tests or when
`LLM_PROVIDER=fake`. `GenerationOptions.llm_provider` is `null` (server default) | `"gemini"` |
`"openai"` | `"anthropic"` | `"fake"`. `POST /api/projects` and `POST /api/projects/{id}/regenerate`
refuse an engine that is not configured; with `null` the server default engine is checked, so
`null` is refused too when `LLM_PROVIDER` has no API key (`llm.configured` is then `false`):

```jsonc
// 422
{"code": "validation", "detail": [{"loc": ["options", "llm_provider"], "type": "engine_not_configured",
  "msg": "AI engine 'Anthropic Claude' is not configured on this server."}]}
```

When personal keys are allowed (`api_keys.personal_enabled`), this message and the 409 below end
with "You can add your own API key under API keys."; `type` / `code` are unchanged.

Admin model overrides (`llm_model_plan`, `llm_model_script`) are dropped when the model belongs to
another engine than the lecture's, or to no known engine (any allow-listed model is kept for the
offline engine). The
engine is stored with the version's options, so builds, scene regeneration and translations of
that version reuse it; a regenerate body without `llm_provider` keeps the project's stored engine,
while an explicit `null` switches to the server default. The Studio's *Regenerate lecture* form
starts from the project's stored options (`GET /api/projects/{id}` `options`), so it posts the
lecture's engine, language and settings unless the teacher changes them. LLM jobs on an existing version (scene
regenerate, approve-plan, translate, and retrying a generate / regenerate / translate job) answer
`409 {"code": "engine_not_configured", "engine": "<id>", "detail": "<message>"}` when that version's
engine (a translation: its source version's) has no API key for the requesting user any more (a
build is not checked; regenerate the lecture with another engine). The job then runs on the
requester's keys, not those of the user who generated the version.

## Projects

| Method & path | Body | Response |
|---|---|---|
| `GET /api/projects?q=&limit=&offset=` | – | `{"items":[ProjectSummary],"total":N}` |
| `POST /api/projects` | **multipart**: `file` (pdf/docx/txt/md), `options` (JSON string, GenerationOptions), `title`?, `review_source`? (bool, default false) | `201 {"project","version","job"}` — project + source doc + version 1 (`generating`) + `generate_lecture`. The Studio's "Paste your notes" sends the text as a `.txt` `file`, so the usual validation and limits apply. `review_source: true` pauses the job after the source was read (`awaiting_review`, `review_stage: "source"`) |
| `POST /api/projects/import` | **multipart**: `file` (.json: v2 Screenplay or v1 legacy JSON) | `201 {"project","version","job","warnings":[...]}` — converts/validates, enqueues `build_assets`. Lessons saved by the friend's fork of v1 (`origin/andryan`) are v1 lessons with additions: `scene.edit.hidden` becomes the scene's `hidden`, `scene.edit.min_seconds` its `min_seconds` (clamped to 1–600 s); muted narration, captions turned off, their media asset ids, `asset:` images and their plan / style / Studio / editor keys are ignored with a warning each. The Studio's "Try an example" imports `web/examples/ohms-law.json` here |
| `GET /api/projects/{id}` | – | `{"project": ProjectSummary, "versions": [VersionSummary], "sources": [{"id","filename","mime","size_bytes","page_count","created_at"}], "jobs": [JobSummary (latest 20)], "options": GenerationOptions}` — `options`: the project's stored generation options (validated; invalid legacy values fall back to the defaults; `llm_model_plan`/`llm_model_script` are `null` for non-admins) |
| `PATCH /api/projects/{id}` | `{"title"?, "subject_name"?, "unit_name"?, "session_number"?, "session_title"?, "current_version_id"?}` | `{"project": ProjectSummary}` (`current_version_id` must belong to the project) |
| `DELETE /api/projects/{id}` | – | `204` soft delete; same transaction cancels active jobs and revokes share links |
| `POST /api/projects/{id}/regenerate` | `{"options"?: GenerationOptions, "review_source"?: false}` | `201 {"version","job"}` new version from the latest source (`review_source` as on create; not stored in the project's options) |
| `GET /api/projects/{id}/versions/{vid}/source-report` | – | `{"version": VersionSummary, "available": bool, "reason": "" \| "no_source" \| "unreadable", "review": {"active": bool, "editable": bool}, "report": SourceReport \| null}` — how the version's source was read (`aadhi/pipeline/source_review.py` `SourceReport`: file, outline with roles, chunks with their status, inventory, `findings` (≤ 100, `source.*` codes, each with why / where / suggestion), `readiness` (advice, never blocks), scoping counts by category (never the values), concepts, accounting, `scenes` (which chunks each scene cites), `overrides`). Imported lectures: `available: false`; a translation reports its source version's extract; another user's project or a version of another project: 404. Cached per extract |

## Versions

| Method & path | Body | Response |
|---|---|---|
| `GET /api/versions/{vid}` | – | `VersionSummary` + `{"project_id", "screenplay": Screenplay|null, "issues": [Issue], "stale_scenes": [str], "generation_meta": {...}, "plan": LecturePlan|null, "stage", "next_step": NextStep, "checkpoints"}` (`plan` while awaiting review; `stage` / `next_step`: see [Lesson stage](#lesson-stage); `checkpoints`: `{"source": {"state": "waiting" \| "approved" \| "not_requested", "corrected": bool}, "plan": {"state"}, "visuals": {"total","approved","pending","changed","removed","stale"}}`, the teacher's reviews from stored state: the source and plan pauses of this version, and the Visual Review sign-offs of the scenes with a visual, hidden scenes left out) |
| `GET /api/versions/{vid}/timeline` | – | `Timeline` with URLs resolved at serve time; 404 if never built. `ETag: "r{revision}-b{built_revision}-{hash12}"`, supports `If-None-Match`. |
| `POST /api/versions/{vid}/timeline/preview` | `{"screenplay": Screenplay}` (unsaved draft ok) | Timeline built from the current manifest where beat clips still match, estimated durations elsewhere (`estimated: true`) — for the editor preview |
| `PUT /api/versions/{vid}/screenplay` | `{"screenplay": Screenplay, "revision": int}` | `200 {"version": VersionSummary, "issues": [Issue], "stale_scenes": [str]}`; 409 `revision_conflict` (extra `current_revision`); 409 `version_busy` while a mutating job runs; 422 validation (e.g. `min_seconds` outside 1–600). Every issue of a scene skipped in the video (`hidden`), from any source (lint, the reviewer, the system, asset builds), is served and counted (`issue_counts`) as a note (`info`); it is stored at its real severity, so showing the scene again brings that back (also in `GET /api/versions/{vid}` and the revert response) |
| `GET /api/versions/{vid}/changes` | – | `{"available": bool, "scenes": [{"scene_id", "status": "unchanged" \| "edited" \| "added" \| "moved" \| "removed", "generated_index": int \| null, "current_index": int \| null, "fields_changed": [str], "history": int}]}` — every scene compared with the screenplay as Aadhi wrote it (see [Generated vs edited](#generated-vs-edited)); `available: false` (imports, versions generated before snapshots were kept): the current scenes as `unchanged` with `generated_index: null`, `history` still counted |
| `GET /api/versions/{vid}/changes/{scene_id}` | – | `{"available", "scene_id", "status", "fields_changed", "generated": Scene \| null, "current": Scene \| null, "history": [{"at", "instructions", "revision", "scene"}]}` (history newest first); 404 when the scene is in neither and has no history |
| `POST /api/versions/{vid}/scenes/{scene_id}/revert` | `{"revision": int, "to": "generated" \| "history", "history_index"?: 0–99, "position"?: 0–1000}` | `{"revision", "scene_id", "version": VersionSummary, "issues", "stale_scenes"}` — puts the scene back as Aadhi wrote it, or as its `scene_history` entry `history_index` (0 = the scene before the last regeneration); a removed scene comes back at `position` (default: after the nearest earlier generated scene still in its generated order). Saved like `PUT /screenplay`: 409 `revision_conflict` (+ `current_revision`) / `version_busy` (+ `job_id`); 404 `not_found` when there is nothing to revert to; 409 `revert_invalid` when the lecture would no longer validate; 409 `no_screenplay` |
| `POST /api/versions/{vid}/lint` | `{"screenplay": Screenplay}` | `{"issues": [Issue], "quality": {"version": 1, "repairs": [{"code","scene_id","message","label","edits":[{"scene_id","path","before","after"}]}], "registry": {terms, concepts, abbreviations, symbols, code languages, figures, each with scene ids}}}` — `quality` is additive: safe repairs for some issues (matched by code, scene and message; exact field edits the editor applies as one undoable change) and the lecture's consistency registry |
| `POST /api/versions/{vid}/build` | `{"scene_ids": [str] | null}` | `202 {"job"}` (null = stale scenes); 409 `action_unavailable` when every scene named is skipped in the video (`hidden`: the build never makes it) |
| `POST /api/versions/{vid}/scenes/{scene_id}/regenerate` | `{"instructions": str}` | `202 {"job"}`; 409 `engine_not_configured` |
| `POST /api/versions/{vid}/plan` | `{"plan": LecturePlan}` | `200 {"version"}` (while awaiting review; validated); 409 `not_awaiting_review` while the version waits for its source review |
| `POST /api/versions/{vid}/approve-plan` | – | `202 {"job"}` (continuation job); 409 `engine_not_configured`; 409 `not_awaiting_review` while the version waits for its source review |
| `PUT /api/versions/{vid}/source-review` | `{"excluded_chunk_ids": [str], "restored_chunk_ids": [str], "concept_names": {concept_key: name}}` (≤ 500 / 500 / 40) | `200 {"version", "source_overrides"}` — the teacher's corrections while the version waits for its source review (set parts aside, restore parts the brief set aside, rename concepts; at least one part must stay); 409 `not_awaiting_review` otherwise; 409 `source_unavailable`; 422 `validation` for unknown chunks / concepts |
| `POST /api/versions/{vid}/approve-source` | – | `202 {"job"}` — the waiting job succeeds and a continuation plans the lecture with the corrections (it may pause again for plan review); budget and engine checks as approve-plan; 409 `not_awaiting_review` |
| `POST /api/versions/{vid}/duplicate` | `{"label": str}` | `201 {"version"}`; the copy also gets the version's Visual Review sign-offs |
| `POST /api/versions/{vid}/translate` | `{"target_language","translate_board": false,"tts_voice"?}` | `202 {"version","job"}` (new version, status generating; uses the source version's engine); 409 `engine_not_configured` |
| `POST /api/versions/{vid}/render` | `{"burn_captions": false, "include_intro": true, "soft_subtitles"?: bool, "allow_degraded": true}` | `202 {"render","job"}`; 409 `timeline_stale` unless `built_revision == revision`; 409 `all_scenes_hidden` when every scene is skipped in the video; 409 `job_in_progress` while the version renders; 429 `render_busy` (the user has `RENDER_MAX_PER_USER` active renders, extra `limit`) / `render_queue_full` (`RENDER_MAX_QUEUED` on the server), both with `Retry-After`; with `allow_degraded: false`, 409 `render_preflight` (extra `items`, see below) when a scene would be silent; `WORKER_MODE=inline` on a host that cannot render: 503 `render_unavailable` (the reasons only for admins). `soft_subtitles` (default `RENDER_SOFT_SUBTITLES`): a selectable caption track, never with burned-in captions |
| `GET /api/versions/{vid}/render/preflight` | – | `{"version_id","has_timeline","timeline_stale","blocking": bool,"items":[{"scene_index","scene_id","title","reason","blocking","message","scene_number"}],"quality":[...]}`: `items` = what the MP4 of the built timeline shows differently from the editor (`no_audio` / `audio_mismatch` are blocking; boards replacing missing media, title-only panels, online GIFs are not); `quality` = the version's open lint / reviewer / generation (system: a failed or untranslated scene) errors and warnings, errors first then in scene order, at most 12 plus a last "N more" line (`{"scene_index","scene_id","title","reason": "quality","blocking": false,"message","severity","code","scene_number"}`), never blocking; issues of scenes skipped in the video are not listed. `scene_index` is the place in the timeline (skipped scenes left out), `scene_number` the 1-based place in the screenplay as the editor numbers scenes (null without a scene); the 409 `render_preflight` items carry it too |
| `GET /api/renders/capabilities` | – | `{"available": bool, "advisory": bool, "burn_captions": bool, "reasons"?: [str], "checks"?: {...}}`; `advisory` with external workers (this host's tools only); `reasons` / `checks` in full for admins, a generic reason for others |
| `GET /api/versions/{vid}/renders` | – | `{"items":[RenderSummary]}` newest first, each with `matches_current` and `preview_url` |
| `GET /api/videos?limit=24&offset=0` | – | `{"items": [{"render_id","project_id","project_title","version_id","version_number","created_at","status","duration_s","size_bytes","width","height","matches_current","download_url","preview_url","qa_ok","subject_name","unit_name","session_number","session_title","project_language","project_created_at"}], "total": N}` — the current user's renders across their own, not deleted lectures, newest first (administrators too: other teachers' videos are reached through their projects); `download_url` is the authenticated attachment (null without an MP4); `preview_url` a streamable URL for an in-browser player, null unless the render succeeded; `qa_ok` the post-render check (null when it did not run); the lecture's `subject_name` … `project_created_at` let the Studio tell lectures sharing a title apart exactly as the project list does; `limit` 1–200 |
| `GET /api/renders/{rid}/download?file=video|srt|vtt` | – | file with `Content-Disposition: attachment; filename="<project title>.mp4"` (local) or 302 to a presigned URL with that disposition (S3) |
| `GET /api/versions/{vid}/visual-review` | – | `{"summary": {"total","approved","pending","changed","removed","needs_attention"}, "scenes": [SceneVisual], "revision": int}` — every scene's visual (see [Visual Review](#visual-review)); `revision`: the screenplay revision the scenes were read at; reads only, never builds or generates; 409 `no_screenplay`; 429 `rate_limited` past 60 a minute per user |
| `PUT /api/versions/{vid}/visual-review/{scene_id}` | `{"state": "approved" \| "pending", "note"?: str ≤ 300, "revision"?: int}` | `SceneVisual` — the teacher's sign-off of the visual as it is asked for now (`pending` resets it; without `note` the stored note is kept); an approval sent with the `revision` the teacher saw (`GET .revision`) is refused with 409 `revision_conflict` (+ `current_revision`) when the screenplay changed since (without `revision`: as before); 409 `no_visual` when approving a scene without a visual; 404 unknown scene; 429 `rate_limited` past 60 writes (sign-offs and actions) a minute per user |
| `POST /api/versions/{vid}/scenes/{scene_id}/visual` | `{"action": "new_version" \| "choose_library" \| "remove" \| "retry", "library_item_id"?: int, "confirm_paid"?: false, "revision": int}` | `{"revision": int, "scene": SceneVisual, "job_id": int \| null}` — see [Visual Review](#visual-review); 409 `revision_conflict` / `version_busy` / `action_unavailable` / `confirm_paid_required`; 422 when the library item's kind does not fit the scene; 404 for another user's library item; 403 `forbidden` for a library pick in someone else's lecture; `new_version` / `retry` get the budget pre-check (429 `budget`) and the hourly generation limit; 429 `rate_limited` past 60 writes a minute per user |
| `GET /api/versions/{vid}/companion.md` | – | `text/markdown` attachment |
| `GET /api/versions/{vid}/companion.html` | – | printable sheet; `Content-Security-Policy: sandbox allow-scripts; default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; font-src 'self'` |
| `GET /api/versions/{vid}/export.json` | – | Screenplay JSON attachment |
| `GET /api/versions/{vid}/chapters.txt` | – | YouTube chapters (from the timeline) |

## Jobs

| Method & path | Response |
|---|---|
| `GET /api/jobs?project_id=&status=&limit=` | `{"items":[JobSummary],"total":N}` |
| `GET /api/jobs/{id}` | `JobSummary` + `{"result": {...}|null}` |
| `GET /api/jobs/{id}/events?after=<event_id>&limit=200` | `{"items":[JobEvent]}` |
| `GET /api/jobs/{id}/stream` | `text/event-stream`: `retry: 5000`; `event: job_event` (JobEvent, `id:` = event id), `event: job` (JobSummary on change), `event: end` (final JobSummary) then close. `Last-Event-ID` supported. Comment heartbeat every 15 s. ≤ `SSE_MAX_STREAMS_PER_USER` per user (429). Access: job owner, project owner, admin. |
| `POST /api/jobs/{id}/cancel` | `202 {"job"}` |
| `POST /api/jobs/{id}/retry` | `202 {"job"}` (new job, same payload; failed/cancelled only). The new job runs under the requester: generation kinds answer 409 `engine_not_configured` when the requester cannot run the job's engine, and generation / build kinds get the daily-budget pre-check (429 `budget`); a `render_video` retry passes the render admission (409 `job_in_progress`, 429 `render_busy` / `render_queue_full`); a refused retry keeps the job's single retry |

## Uploads & media

| Method & path | Body | Response |
|---|---|---|
| `POST /api/uploads` | multipart `file`, `purpose` ∈ `scene_media` (video/image), `figure` (image), `side_panel` (image/video), `poster` (image); `project_id` | `201 {"asset_key","url","mime","kind","width"?,"height"?,"duration_s"?}`; records an `asset_ref` for the project. Allowed: png, jpg, webp, gif, mp4, webm. Magic bytes checked. A picture or clip also joins the uploader's [media library](#media-library) (source `upload`, titled from the file name; words the uploader gave the same file earlier are kept); a library problem never fails the upload, and the response is unchanged. |
| `GET /media/{storage_key:path}` | – | Local storage, `assets/` prefix only (private/ → 404). `Cache-Control: public, max-age=31536000, immutable`, `nosniff`, `Content-Security-Policy: sandbox; default-src 'none'`, Range requests. |
| `GET /branding/{file}` | – | Mascot clips, background, logo, bgm (long cache). |

## Visual Review

```jsonc
// SceneVisual
{"scene_id": "s4", "index": 3, "title": "The transistor", "hidden": false,  // hidden: skipped in the video
 "kind": "image",          // image | video | figure | manim | chart | graph | model_3d | terminal | interactive | none
 "source": "generated",    // generated | library | upload | figure | manim | builtin | fallback | none
 "visual_source": "generated",              // a copy of source
 "provider": "pollinations", "model": "",   // generated media only (from the asset's metadata), else null
 "prompt": "a transistor on a breadboard",  // the image prompt / video prompt / GIF query, else null
 "url": "/media/assets/image/…",            // the media shown, served like timeline URLs; null when none
 "poster_url": null,                        // always null for now
 "status": "ready",        // ready | missing | failed | fallback | ambiguous | stale
 "status_reason": "",      // plain words
 "review": {"state": "approved", "stale": false, "note": null, "updated_at": "..."},
 "variant": 0,             // "new AI version" number (0 = the first)
 "actions": ["approve", "new_version", "choose_library", "upload", "remove"],  // also retry, confirm_paid_retry
 "accepts": ["image", "video"],             // library kinds a pick / upload for this scene may have
 "findings": [{"code": "assets.media_degraded", "severity": "warning", "message": "..."}],  // asset / Manim issues (≤ 5)
 "library_suggestions": 2} // the requester's matching library items, never the media the scene shows (0 once
                           // the teacher chose a visual)
```

The visual of a scene is its main media for simulation, AI video and interactive scenes (an
interactive scene's poster), else its side panel; quiz teasers and the concept map are not visuals.
`scenes` lists every scene; the summary counts the scenes with a visual and the removed ones, and
`needs_attention` those whose status is not `ready` or whose sign-off is stale. A scene skipped in the
video (`"hidden": true` on its SceneVisual) is listed and can be approved, replaced or removed, but the
build leaves it out: `new_version`, `retry` and `confirm_paid_retry` are not listed for it (and are
refused with 409 `action_unavailable` before anything is charged), and it never counts as needing
attention. `choose_library` and
`upload` are listed only in the requester's own lectures. `new_version` is listed (and accepted) only
while that medium can be generated for the lecture with the requester's keys: the lecture's opt-in
(`allow_generated_images`, `allow_ai_video`) and a usable provider chain; an AI video scene that can
only get a still never gets one in place of a clip made earlier. `retry` is not listed for a visual a
rebuild cannot change (`fallback` / `missing` because of the lecture's AI video limit or generation
being off: the reason then says to choose or upload one), and such a retry is refused with 409
`action_unavailable` before anything is charged.

A visual the teacher chose that is not built yet is shown as chosen (its media, `source` `library` /
`upload`, status `stale`, "build the scene to use it"); a removed override is no longer shown. A new
version that could not be made (generation off on the server, or the provider failed) keeps showing the
latest earlier version, with status `fallback`. Each response scores the requester's library against
the scenes: at most 20,000 (item, scene) scores per request (`library.MATCH_MAX_PAIRS`; a write's answer
scores its own scene only), on top of the per-user limits.

**Sign-off** (`review.state`): `pending` (nothing stored), `approved`, `changed` (the teacher replaced
or re-made the visual here) or `removed`. Each sign-off stores a fingerprint of what the scene asks its
visual to show (the visual request without narration, titles or rationale, plus the chosen asset and
the variant). When that changes later the sign-off is `stale`: an approval then reads as `pending`;
`changed` / `removed` are kept and flagged. Sign-offs live outside the screenplay, so approving never
changes a scene hash, a cache key or a build. `POST .../duplicate` copies them.

**Actions** (`POST …/scenes/{scene_id}/visual`, on the screenplay `revision` the teacher saw):

* `new_version`: `variant` + 1 on the generated image panel or the AI video scene (saved like an editor
  save), then a `build_assets` job for that scene only (`job_id`). The new version has its own cache
  key; the earlier one stays cached and is shown if the new one cannot be made. Sign-off `changed`.
  409 `action_unavailable` when it cannot be generated now (see above).
* `choose_library`: `library_item_id` of the requester's own item, in the requester's own lecture; it is
  attached to the lecture (`asset_ref`) and set as the scene's override (side panel, simulation or AI
  video media, interactive poster). An animation scene takes a video, an interactive poster a picture.
  Sign-off `changed`.
* `remove`: removes the side panel (sign-off `removed`), or the teacher's override of main media or of a
  poster (the scene's own visual comes back: `changed`). A scene's own clip or animation cannot be
  removed (409 `action_unavailable`).
* `retry`: builds that scene again without changing the screenplay.

A build of only some scenes (`new_version`, `retry`, or `POST .../build` with `scene_ids`) that reuses
other changed scenes as they were stores its manifest and timeline but does not count as a build of the
revision: `timeline_stale` stays `true` and `POST .../render` answers 409 `timeline_stale` until a
full build (`scene_ids: null`, the review's "Build changed scenes").

When the scene's AI video may already have been billed by an attempt that stopped
(`video.ambiguous_submission` / `video.operation_lost`, status `ambiguous`), `new_version` and `retry`
answer 409 `confirm_paid_required` unless `confirm_paid: true`; the build job then carries
`confirm_paid_scene_ids` and submits that scene's clip again, once: if that submission stops before its
answer too, the scene needs a new confirmation, and `POST /api/jobs/{id}/retry` never copies the key. The Studio's "Upload replacement" is
`POST /api/library` followed by `choose_library`.

## Generated vs edited

When `generate_lecture` (or `translate`, for the new version) writes the screenplay, the screenplay exactly as
written is stored as a private, content-addressed blob (asset kind `snapshot`, never collected by the cleanup
GC) and its key goes to `generation_meta["generated_snapshot_key"]`. Scene regeneration, teacher saves and
builds never touch it; `duplicate` copies the key with the rest of the metadata. Scene hashes, cache keys and the
version document are unchanged.

`GET .../changes` compares each scene by id: `edited` lists the top-level scene fields whose values differ
(`fields_changed`, e.g. `title`, `beats`, `board`, `side_panel`, `hidden`, `min_seconds`); `added` is not in the
snapshot (also each part of a split scene after the first); `moved` has the same content out of the generated
order (scenes off the longest run that kept their generated order); `removed` is in the snapshot only and is
listed where it would go back. Both documents are read with the current schema, so fields added later with
defaults never read as edits. `history` counts the earlier versions kept by scene regeneration.

## Lesson stage

`stage` and `next_step` (on ProjectSummary and the version detail) are derived from stored state only
(`aadhi/stage.py`: version status and review stage, whether the timeline is built from the current revision,
the job running on the version and its renders), first match wins:

| Stage | When | `next_step.action` (`href`) |
|---|---|---|
| `source_review` / `plan_review` | awaiting review | `review_source` (`#/p/{id}/v/{vid}/source`) / `review_plan` (`#/p/{id}/v/{vid}/plan`) |
| `reading` / `planning` / `writing` / `building` | a version-mutating job is queued or running (from its kind and stage), or the status is `generating` / `building` | `wait` |
| `failed` | the version failed | `retry` (the version's latest failed job) |
| `rendering` | a render is queued or running | `wait` |
| `ready_to_build` | the timeline is not built from the current revision and no video exists | `build` |
| `video_outdated` | not built from the current revision, or built but only older videos exist | `build` / `render` |
| `video_ready` | a finished video was made from this revision | `watch` (`#/videos`) |
| `failed` | the latest render of this revision failed | `render` |
| `ready_to_render` | built, no video yet | `render` |

A project without a current version is `failed` with `open` (`#/p/{id}`). Labels are plain words; the Studio
accepts only `#/…` and same-site `/…` links and shows no button for an action it does not know.

## Media library

Each user's own pictures and clips: their uploads, the media that builds they started generated
(`LIBRARY_AUTO_SAVE_GENERATED`) and figures. Titles, descriptions and keywords are that user's words;
the media itself is the shared content-addressed asset, to which nothing per user is written.

```jsonc
// ItemView
{"id": 12, "asset_key": "upload-…", "kind": "image",   // image | video
 "title": "Plant cell", "description": "", "keywords": ["cell", "biology"],
 "source": "upload",                                    // upload | generated | figure
 "mime": "image/png", "size_bytes": 48211, "width": 1600, "height": 900, "duration_s": null,
 "url": "/media/assets/upload/…", "poster_url": null,   // poster_url: always null for now
 "created_at": "...", "updated_at": "...", "last_used_at": "..." | null,
 "used_in": 2,                                          // the user's own lectures it was added to or made for
                                                        // (asset refs: not reduced when a scene shows other media)
 "prompt": null, "provider": null, "model": null}       // generated media only
```

| Method & path | Body | Response |
|---|---|---|
| `GET /api/library?q=&kind=&source=&limit=48&offset=0` | – | `{"items": [ItemView], "total": N}`, newest first; `q`: every word must start a word of the title, keywords, description or prompt (stems; any script); `source`: one source or several comma-separated (`upload,figure`; 422 for an unknown one); `limit` ≤ 200; items whose file is gone are left out |
| `POST /api/library` | multipart `file`, `title`?, `description`?, `keywords`? (comma-separated) | `201 ItemView` — same validation and limits as `/api/uploads`, no lecture needed; the same file again returns the same item with the typed words applied (empty fields keep what it has) |
| `PATCH /api/library/{id}` | `{"title"?: 1–120 chars, "description"?: ≤ 1000, "keywords"?: [≤ 20 × ≤ 40 chars]}` | `ItemView`; repeats (ignoring case) dropped; 422 `validation` |
| `DELETE /api/library/{id}` | – | `204`; removes the library row only: lectures that use the media keep it |
| `POST /api/library/{id}/attach` | `{"project_id": int}` | `{"asset_key"}` — records the `asset_ref`, so the key can be used as an `override_asset_key` (or a figure's `asset_key`) in that lecture; the user's own lectures only (404 otherwise, admins included); 409 `asset_missing` |
| `GET /api/library/suggestions?version_id=` | – | `{"scenes": [{"scene_id", "matches": [{"item": ItemView, "score": 0..1}]}]}` — for each scene showing a generated picture or clip with nothing chosen yet, the user's best items (≤ 3, score ≥ 0.35; deterministic word matching; never the media the scene shows now; at most 20,000 item scores per request); `{"scenes": []}` for a version without a readable screenplay; 429 `rate_limited` past 30 a minute |
| `POST /api/library/{id}/describe` | – | `{"title","description","keywords"}` — a suggestion from the default AI engine's fast model, which sees the picture (or one frame of the clip) and the item's words (not saved: save it with `PATCH`); 403 `feature_disabled` unless `LIBRARY_AI_DESCRIBE_ENABLED`; 503 `unavailable` (no engine for this user); 502 `describe_failed`; 429 `rate_limited` (6 a minute) / `budget` (daily budget; skipped on the user's own key). Usage is recorded (`purpose: library_describe`) |

Every route is the signed-in user's own library: another user's item is 404 for everybody, admins
included. Mutations need the CSRF header like every cookie request.

## Sharing, watching, analytics

| Method & path | Body | Response |
|---|---|---|
| `POST /api/projects/{id}/shares` | `{"version_id"?: int, "expires_in_days"?: int}` | `201 {"token","url","version_id","expires_at"}` |
| `GET /api/projects/{id}/shares` | – | `{"items":[{"token","url","version_id","created_at","expires_at","revoked_at","view_count"}]}` |
| `DELETE /api/shares/{token}` | – | `204` (revoke) |
| `GET /api/public/watch/{token}` | – (no auth) | `{"timeline": Timeline, "project": {"title","subject_name","session_title"}, "share_token"}`; 404 if revoked, expired, or project deleted |
| `POST /api/analytics/events` | `{"share_token"?: str, "version_id"?: int, "viewer_id": "^[A-Za-z0-9_-]{16,64}$", "events": [{"event": ANALYTICS_EVENTS, "scene_id"?, "t": absolute seconds, "scene_t"?: seconds, "data"?: typed per event}]}` (≤ 100 events, ≤ 32 KB) | `202`. When `share_token` is present it alone determines project/version (cookie ignored); otherwise session auth + `version_id` the user may access. Sent with `fetch(keepalive)` + CSRF header. Per share-link daily caps. |
| `GET /api/projects/{id}/analytics?version_id=` | – | `{"summary": {"viewers","sessions","completion_rate","avg_watch_seconds"}, "scenes": [{"scene_id","title","index","enters","completes","dropoff_rate"}], "quizzes": [{"scene_id","question","answers","accuracy","option_counts":[...],"correct_index"}], "flags": [{"scene_id","reason"}]}` (one quiz answer per viewer per scene counted; a scene skipped in the video also has `"hidden": true`) |

Event `data`: `quiz_answer` `{"choice": int, "correct": bool}`; `seek` `{"from": number, "to": number}`;
others `{}`.

## Usage & budgets

| Method & path | Response |
|---|---|
| `GET /api/usage/me?days=30` | `{"total_usd","today_usd","daily_budget_usd","own_key_usd","by_day":[{"date","usd"}],"by_operation":[{"operation","usd"}],"by_provider":[{"provider","model","usd","calls"}]}` — `total_usd` and the breakdowns count everything over the last `days` UTC days; `own_key_usd`: the part of `total_usd` paid with the user's personal API keys (`billed_to = "user"`, same period), which the daily budget does not count; `today_usd`: today's spend billed to the server, i.e. what the daily budget measures (own-key spend excluded) |
| `GET /api/usage/projects/{id}` | `{"total_usd","by_job":[{"job_id","kind","usd","created_at"}],"by_operation":[...]}` |
| `GET /api/admin/usage?days=30` | `{"users":[{"user_id","username","total_usd","today_usd","own_key_usd"}],"total_usd","own_key_usd"}` — totals count everything (own-key spend included); `own_key_usd`: the part paid with users' personal API keys over the same period; `today_usd`: today's spend billed to the server, i.e. what the daily budget measures (own-key spend excluded, as in `/api/usage/me`) |

## API keys

Gemini, OpenAI and Anthropic keys saved in the Studio. **Personal** keys (`/api/keys`, every user)
are used only for jobs that user starts; **server** keys (`/api/admin/keys`, admins) are used for
everyone and replace the `.env` key. For each provider a job uses the starter's personal key, else
the server key, else the `.env` key; a rejected personal key fails the job (no fallback). Keys are
write-only: no response ever contains a key or its ciphertext. `{provider}` is `gemini`, `openai`
or `anthropic` (anything else: 404).

```jsonc
// CredentialView (a saved key, without the key)
{"hint": "sk-ant-…AbCd",              // vendor prefix (if any) + at most the last 4 characters
 "updated_at": "...", "last_verified_at": "..." | null,
 "last_error": "<redacted provider message, ≤ 300 chars>" | null,
 "readable": true}                      // false: can no longer be decrypted (encryption key changed); enter it again
```

| Method & path | Body | Response |
|---|---|---|
| `GET /api/keys` | – | `{"enabled": bool, "providers": [{"provider": "anthropic", "label": "Anthropic Claude", "personal": CredentialView \| null, "server_available": bool}]}` — `enabled`: personal keys can be saved and used (`STORED_API_KEYS_ENABLED` and `USER_API_KEYS_ENABLED`); `server_available`: a server key (Studio or `.env`) exists for that provider |
| `PUT /api/keys/{provider}` | `{"api_key": str}` | `200 {"provider","label","personal": CredentialView,"server_available"}` (creates or replaces); 403 `api_keys_disabled`; 422 `validation` (trimmed key must be 16-512 letters, digits, `-` or `_`, the characters the providers' keys use) |
| `DELETE /api/keys/{provider}` | – | `204` (also when no key was saved, and also while saved keys are switched off) |
| `POST /api/keys/{provider}/test` | – | `{"ok": bool, "message": str}` — one list-models call to the provider (nothing billed); stores the outcome on the key; 403 `api_keys_disabled`; 404 when no personal key is saved; 429 `rate_limited` (10 tests per minute per user, personal and server tests together) |

`ok: false` is a normal 200 answer (the provider refused the key or could not be reached); the
message is redacted. A failed test does not disable the key. `last_verified_at` is the time of the
last **successful** test; a failed test sets `last_error` and keeps `last_verified_at`, a
successful one clears `last_error`. Saving a new key clears both. Testing a saved key that can no
longer be decrypted (`readable: false`) answers `ok: false` without calling the provider or using
up the rate limit.

## Admin

| Method & path | Body | Response |
|---|---|---|
| `GET /api/admin/users` | – | `{"items":[User]}` |
| `POST /api/admin/users` | `{"username","password","role"}` | `201 {"user"}` (new users get `must_change_password=true`) |
| `PATCH /api/admin/users/{id}` | `{"role"?, "is_active"?, "daily_budget_usd"?, "password"?, "must_change_password"?}` | `{"user"}`; role/active/password changes bump `token_version` |
| `GET /api/admin/keys` | – | `{"enabled": bool, "providers": [{"provider","label","stored": CredentialView \| null, "env": bool, "active_source": "stored" \| "env" \| null}]}` — `enabled`: `STORED_API_KEYS_ENABLED`; `env`: the provider has a `.env` key; `active_source`: which server key jobs use (a user's personal key still wins for that user's jobs) |
| `PUT /api/admin/keys/{provider}` | `{"api_key": str}` | `200` the provider's entry as in the list (creates or replaces the server key); 403 `api_keys_disabled`; 422 `validation` (same rules as personal keys) |
| `DELETE /api/admin/keys/{provider}` | – | `204` (also when none was saved, and also while saved keys are switched off); removes only the key saved in the Studio, the `.env` key stays in use |
| `POST /api/admin/keys/{provider}/test` | – | `{"ok": bool, "message": str, "source": "stored" \| "env"}` — tests the active server key (saved in the Studio and readable, else `.env`; with saved keys switched off, the `.env` key); stores the result on a saved key; 404 when there is no server key at all; 429 `rate_limited` |
| `GET /api/admin/media-providers` | – | `{"image": Chain, "video": Chain, "cooldown_seconds", "auth_cooldown_seconds", "cooldowns_source"}`, Chain = `{"enabled","configured","preferred","usable","providers":[{"name","label","role","order","state","reason","paid","model","cooldown_seconds_left"}]}` with `role` preferred \| backup, `state` available \| not_configured \| cooling and a redacted `reason`. `cooldowns_source` is `this process` (inline workers) or `recent worker job events` (external workers: the server-scope cooldowns the workers logged, until their window ends). Server-level keys only, configuration and cooldowns only: no provider is built or called |
| `GET /api/admin/media-cache?days=30` | – | `{"days", "kinds": {"image" \| "video": {"generated","reused_from_other_lectures","provider_calls","cost_usd","stored"}}, "library": {"items", "by_source": {"upload","generated","figure"}}}` — aggregates only (no prompts, no users) over the last `days` (1..366); reuse within one lecture (a rebuild) is not recorded |
| `GET /api/admin/manim/sandbox` | – | the Manim sandbox self-check (as `python -m aadhi.cli manim-check`): `{"available","sandbox","isolated","isolation","manim_version","manim_version_ok","latex","network_denied","env_clean","files_denied","probe_ran","notes"}` (the probe runs with `MANIM_AUDIT_HOOK` as configured; `files_denied`: a host file outside the work dir could not be read), never host paths; starts a probe process, so 429 `rate_limited` past 6 a minute |

## Render worker (internal)

The render job opens `/render-frame#token=<scoped token>`; `render.js` reads the fragment and calls
`GET /api/render/timeline` with `Authorization: Bearer <scoped token>` (scope `render`, `vid` claim,
derived signing key). Response: Timeline with URLs resolved.

## Pages

| Path | Serves |
|---|---|
| `/` | `web/index.html` (Studio) |
| `/watch/{token}` | `web/watch.html` (public player) |
| `/preview/{version_id}` | `web/watch.html` (authenticated preview; fetches `/api/versions/{vid}/timeline`) |
| `/render-frame` | `web/render.html` |
| `/sandbox/p5` | `web/sandbox/p5.html` with its sandbox CSP |
| `/web/*` | static frontend files (incl. `/web/vendor`) |
