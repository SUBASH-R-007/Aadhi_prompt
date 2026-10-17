# Aadhi EduEngine v2 — Architecture

> Authoritative design for the v2 overhaul. Code contracts live in `aadhi/schemas/*`,
> `aadhi/*/base.py`, `aadhi/models.py`, `aadhi/config.py`, `aadhi/db.py`, `aadhi/storage/*`,
> `docs/API.md` and `docs/INTERFACES.md`. If this document and a contract file disagree, the
> contract file wins.

## 1. Principles

1. **Compile, don't perform.** A lecture is *built* before anyone watches it:
   source → validated screenplay → pre-rendered, timed assets → timeline → (live player | MP4).
   Nothing expensive (LLM, TTS, Manim, image/video generation) happens during playback.
2. **One timeline, two renderers.** The web player and the MP4 renderer consume the same
   `Timeline`; the MP4 renderer screenshots the *same* player code in render mode.
3. **Alignment by construction.** Narration is a list of `Beat`s; a beat may reveal one board item,
   fill one blank worked-example step, and highlight ≤ 2 earlier items. Each beat is synthesised as
   its own TTS clip, so beat boundaries are measured offsets. No `[SYNC]` counting.
4. **Untrusted content never executes.** Board content is typed data rendered with `textContent`;
   TeX goes through MathJax with page scanning off, `require` removed, html/bbox/unicode autoloads
   disabled and `ui/safe` loaded (`renderTex`, verified against injection payloads); graphs use
   math.js with an expression allow-list (no `eval`); p5 sketches run only in an opaque-origin
   sandboxed iframe; Manim code runs only in the sandbox after an AST allow-list check.
5. **Durable jobs, content-addressed assets.** Long work runs in DB-backed jobs with progress events
   (SSE), retries, cancellation and lease fencing. Every artifact is keyed by a hash of its inputs;
   stored timelines hold asset keys, URLs are resolved at serve time.
6. **Structured generation + validation + repair.** LLM calls use LLM-friendly generation models
   (`aadhi.pipeline.gen_models`), validated by Pydantic + semantic validators inside the re-ask
   loop; deterministic lint + an LLM critic find problems; only failing scenes are regenerated.
7. **Pedagogy over decoration.** No media quotas. Every visual carries a `rationale`. The planner
   follows Mayer's multimedia principles (coherence, signalling, redundancy, temporal contiguity,
   segmenting, pre-training, personalisation), retrieval practice, worked examples with fading
   (blank steps + fill beats), explicit misconception targeting, objectives that are taught *and*
   assessed.
8. **Cost is a first-class signal.** Every billable call is priced (`UsageEvent`); jobs and users have
   enforced budgets.
9. **Offline by default for tests.** Every provider has a deterministic `fake`; tests never touch the
   network or real keys (`tests/conftest.py`).
10. **Self-hosted frontend dependencies.** Libraries and fonts (incl. Tamil/Devanagari/Telugu/
    Kannada/Malayalam) are vendored into `web/vendor` by `npm run vendor`; CSP is `script-src 'self'`
    and the render worker needs no internet access.

## 2. Repository layout and ownership

```
aadhi/                     Python package (backend)
  config.py db.py models.py                                          [contract]
  credentials.py           API keys saved in the Studio: resolve_keys, save/delete, verify_key
  assets_auth.py           which stored media a project may name: FIELD_RULES (field -> kinds + MIME family),
                           record_asset_refs (the one way to add AssetRef rows)
  library.py               each user's media library: items, deterministic matcher, AI description
  review.py                Visual Review: per-scene visuals, sign-offs (fingerprints), visual actions
  changes.py               generated snapshot (store / load), "Generated vs edited" comparison, scene revert
  stage.py                 lesson stage + next step derived from stored state
  scratch.py               private scratch root for temporary work dirs (SCRATCH_DIR); the only place sweeps look
  schemas/                 screenplay.py, manifest.py, timeline.py   [contract]; jsonsafe (storable JSON);
                           features (schema v3: DYNAMIC_FEATURES, reader-version marker); generate (web/schemas)
  storage/                 base.py, local.py, assets.py, __init__   [contract]; s3.py
  auth/                    passwords, tokens (session + scoped), cookies, deps, bootstrap
  security/                headers (CSP), csrf, ratelimit, uploads, bodylimit, client_ip,
                           credentials_crypto (Fernet), redaction (registry of runtime secrets)
  usage/                   pricing, service (record, budgets, summaries)
  legacy/                  v1 JSON + v1 projects.db importers, "import_legacy" job
  jobs/                    base.py [contract]; queue, worker, context, events (SSE), inline,
                           limits (process-wide semaphores), cleanup ("cleanup" job), reader_version
                           (refuses jobs on documents of a newer build)
  providers/               base.py [contract]; llm/ (gemini, openai, anthropic, fake, schema), tts/, image/,
                           video/ (veo, omni, fake), gif/, factory.py, health (cooldowns), operations (paid video
                           checkpoints)
  pipeline/                base.py [contract]; gen_models, canonicalize, ingest, codeblocks, plan, script,
                           validate, critic, repair, companion, assets, audio, envelope (narration
                           loudness), translate, plan_state, checkpoint (LLM stage + per-scene checkpoints),
                           source_review (source report + review gate), presenter_lint, sync_lint,
                           quality/ (quality and consistency checks), orchestrator (job handlers),
                           fake_content; prompts/*.md
  manim/                   base.py [contract]; aadhi_scene.py, templates/, guard, sandbox, winjob,
                           runtime/hardening.py (in-sandbox block), render, qa, health (self-check)
  compose/                 base.py [contract]; timeline, sync (word-anchored cues), captions, chapters,
                           screenshot, ffmpeg, sounds, video ("render_video" job), preflight, capabilities,
                           qa, workspace, loudness (EBU R128 helpers for the Dynamic audio chain),
                           presentation (Dynamic timeline: TimelinePresentation, ResolvedLook, the flag; §14)
    compose/motion/        the motion render engine (§14): job (render_dynamic), engine, stage (capture
                           workers), resources (W, CPU slots), chunks, cache, encode, media (frame extraction)
    compose/dynamic/       stage_page (the /render-dynamic page driver), preflight, container (post-render
                           checks), colour (CSS colours, compositing, WCAG contrast), determinism (the 1x300 /
                           3x100 / reverse harness)
  evals/                   eval harness (python -m aadhi.evals ...)
  api/                     FastAPI routers + errors + deps + storable (JSON body guard); main.py builds the app
  cli.py worker.py         python -m aadhi.cli … | python -m aadhi.worker
alembic/                   migrations
web/                       frontend (native ES modules + JSDoc types, no bundler)
  index.html watch.html render.html render-dynamic.html (the motion engine's render page) sandbox/p5.html
  css/ (tokens, base, player, panels, studio, dynamic)  js/shared/ js/player/ js/render/ js/studio/
  js/dynamic/ (the Dynamic stage: one compositor for the live player and render-dynamic.html; §14)
  js/motion/ (the in-house motion toolkit, pure)
  vendor/ (generated: libs + fonts)            schemas/ (generated JSON schemas)  tests/
scripts/vendor.mjs         copies pinned libs/fonts from node_modules into web/vendor
scripts/clean_mascot_clip.py  reproduces the *_clean.mp4 clips (left: black bars filled; --watermark: the
                           "Veo" corner of right/center/popup/no_aadhi filled from a temporal-median plate;
                           --watermark --box: the sparkle of logo_animation_clean.mp4)
scripts/bench_motion.py    the motion engine benchmark (docs/OPERATIONS.md §4.2)
scripts/gen_env_example.py, gen_chromium_seccomp.py, lock_requirements.py   generate .env.example, the render
                           worker's seccomp profile and requirements.lock (each with --check)
scripts/dev_demo.py        the offline demo server (fake providers, data-demo/)
video_template/            branding: mascot clips (each <clip>_clean.mp4 replaces <clip>.mp4, which stays for
                           old timelines), posters/<clip>.jpg (frame 0 of each clip), bgm, logo, static
                           background (served at /branding)
evals/                     fixtures, video_qa.py (frame-accurate MP4 checks), render_parity.py (preview vs MP4)
docker/ tests/ docs/      tests/invariance: the classic invariance harness (goldens, differential pixel test);
                           tests/determinism: the motion engine's determinism, parity and real-page suites (§14);
                           tests/web: Studio and watch page checks in real Chromium;
                           docs/research: the reports behind docs/DYNAMIC_VIDEO_DESIGN.md
server.py                  thin shim: `from aadhi.main import app`
```

## 3. End-to-end flow

```
Studio upload ──POST /api/projects (multipart)──► Project + SourceDocument (private/ blob)
  (a file, or pasted notes      + ProjectVersion(number via atomic counter, status=generating) + Job(generate_lecture)
   sent as a .txt file)
worker: generate_lecture  (payload: source_document_id, options, base_revision, review_source?)
  1 ingest     PDF (PyMuPDF in a subprocess with timeout + page cap): markdown with
               <!-- page N --> markers, tables, LaTeX-ish maths, figures extracted (assets kind
               figure), chunks c0001…; scanned/math-heavy PDFs set attach_original=True.
               DOCX (mammoth→markdown + embedded images; zip-bomb checks). TXT/MD as-is.
               Code stays verbatim in ``` fences (MD fences, indented TXT/MD code that is mostly code
               lines, DOCX Code / Source Code / HTML Preformatted styles with soft line breaks, runs of
               monospace PDF lines with rebuilt indentation; a PDF set mostly in a monospaced font is
               read as text unless it also has proportional body text and its monospaced lines look
               like code, as in a lab record); a fence is one unit for chunking and scoping (only an
               excluded author's spaced name inside it is replaced). A "~~~~" divider line in TXT,
               DOCX or PDF stays text (escaped). Code detection runs in linear time on any input. TXT/MD setext headings (===/---) and
               `--- Page N ---` markers are recognised. Warnings (counts and pages, never the text):
               instruction-like text in the source, scripts the narration cannot speak.
               `INGEST_VERSION` 8 keys the extract cache; a version keeps reading its own extract
               (`generation_meta["ingest_key"]`), so its chunk ids stay valid.
     scope     source_scope (deterministic): document metadata (subject/unit/session) → SourceMeta
               (title cards only); SME/author/reviewer details, durations, timecodes, codes, dates,
               clip scaffolding (title cards, per-clip objectives, bridges, outros) → `excluded`
               (audit only — never sent to a model); the author's ANIMATION/visual directions →
               `visual_notes` (hints, never narrated). See docs/PROMPTS.md.
  2 brief      one structured call → ConceptBrief: the concepts to teach (facts, examples,
               prerequisites, teaching order, the source's own questions). Every chunk is cited or
               set aside (`skipped_chunks`, reason only); planners and scene writers then receive
               the cited chunks only. A failed brief falls back to ungated planning; a provider
               rate limit is not a failure: the attempt ends and the job is retried (as for the
               critic and the practice problems).
     source    only with `review_source`: AwaitingReview (stage `source_review`,
     review    `generation_meta["review_stage"]="source"`). The teacher reads the source report and
               may set parts aside, restore parts the brief set aside and rename concepts
               (`SourceOverrides`); approve-source continues with the corrected brief, and set-aside
               chunks reach no later stage (`pipeline/source_review.py`).
     plan      one structured call → LecturePlan (validated: ids, refs, DAG, templates) built from the
               brief: one continuous session, dependency order, every scene with a narrative_role and
               a bridge_in; seeds the lexicon from glossary_terms. review_plan → AwaitingReview.
  3 script     planned scenes generated IN PARALLEL, one type-specific call each (gen_models),
               plan + neighbouring scene intents + relevant chunks; canonicalize assigns ids
               (`{scene}-b{n}`, `{scene}-i{n}`), shuffles quiz options deterministically, copies intent.
  4 validate   lint → critic (grounding vs chunks, pedagogy rubric, flow) → repair (≤ 2 rounds,
               failing scenes only) → issues
  5 companion  derive formulas/definitions/misconceptions from the board; LLM writes practice problems
  6 assets     manifest only (screenplay untouched): per-beat TTS (lexicon applied, cached per beat),
               trimmed + joined with INTER_BEAT_GAP + pauses; Manim (templates first, sandboxed,
               timed to beats, visual QA); figures; images and AI video through the provider chain
               (§6) within budget (else stills); a loudness envelope of each narration file
               (`pipeline/envelope.py`, Aadhi's speech motion)
  7 timeline   build_timeline (+ word-anchored sync cues, `compose/sync.py`) → stored on the version
               via compare-and-set on revision (status=ready, built_revision=revision)
Studio: preview (player), edit (PUT screenplay with revision → lint, stale scenes), editor
        preview timeline (estimated durations), regenerate scene, build, render MP4, share, translate.
worker: render_video  tool check → timeline (rebuilt for include_intro) → preflight list → render-mode
        screenshots (Playwright) → ffmpeg segments (cached in a resumable workspace) → concat → BGM
        ducking → output check (QA) → MP4 + SRT/VTT + chapters
```

## 4. Data model (see `aadhi/models.py`)

* `ProjectVersion`: deferred JSON documents (`screenplay`, `asset_manifest`, `timeline`, `issues`,
  `generation_meta`) + cheap summaries (`issue_counts`, `has_timeline`), `revision` (bumped on
  every screenplay change, compare-and-set) and `built_revision` (revision the timeline was built
  from; `timeline_stale = built_revision != revision`). Status: draft | generating |
  awaiting_review | building | ready | failed. Typed accessors (`get_/set_screenplay` …) — JSON
  columns are replaced, never mutated in place.
* Storable JSON (`schemas/jsonsafe.py`): refuse on write, sanitise on read. Schema floats refuse
  NaN/Infinity (`StrictModel` `allow_inf_nan=False`); request bodies that are stored (`api/storable.py`
  `StorableBody`, `ensure_storable`) refuse non-finite numbers and lone surrogates anywhere with 422.
  Documents stored before that still load: readers (`get_screenplay/get_manifest/get_timeline`,
  `VersionSnapshot`) use `validate_stored` (bad numbers read as 0, surrogates as U+FFFD) and API
  responses `json_safe` (null / U+FFFD).
* `Issue.data` (optional, omitted when empty): structured detail, e.g. `{"category": "timeout"}` on
  `manim.render_failed` (`aadhi.manim.base.FAILURE_CATEGORIES`).
* Generation/translation create **new versions** (number from `Project.next_version_number` via
  `atomic_add`); edits, regenerate_scene and build_assets act in place via compare-and-set.
* `Asset` rows are content-addressed; blobs at `assets/<kind>/<key>/<random>.<ext>` (public) or
  `private/...` (sources, extracts, screenshots, `snapshot`s: the screenplay exactly as Aadhi wrote it).
  `AssetRef(project_id, asset_key)` authorises keys used in screenplays and enables GC; the cleanup GC
  never collects `snapshot` blobs. Rows are added only through `assets_auth.record_asset_refs` (uploads, imports,
  generated media, renders; idempotent, `ON CONFLICT DO NOTHING`), and a screenplay field accepts a key only when
  the asset's kind and MIME family fit it (`assets_auth.FIELD_RULES`, §13). The Dynamic kinds `music`, `clip`,
  `clip_proxy`, `clip_sprite`, `waveform`, `voice_sample`, `thumbnail` and `narration_upload` are public like the
  classic media; `calibration` stays private.
* `Job`/`JobEvent` (progress + SSE), `Render`, `UsageEvent`, `ShareLink`, `AnalyticsEvent`.
  `UsageEvent.billed_to` is `server` (server or `.env` key) or `user` (paid with the job starter's
  personal key); only `server` rows count toward the per-user daily budget.
* `ApiCredential` (`api_credentials`): one encrypted API key per owner and provider
  (`UniqueConstraint(owner_key, provider)`). `owner_key` is `server` or `user:<id>`; `user_id`
  (cascade delete, `NULL` for server keys), `provider` (`gemini` | `openai` | `anthropic`),
  `ciphertext` (Fernet), `key_hint` (vendor prefix + last 4), `created_by_id`, `last_verified_at`,
  `last_error`. Added by migration `0002_api_credentials`.
* `AssetClaim` (`asset_claims`, migration `0003_asset_claims`): cross-process claim on a paid
  generation (key PK, holder, job_id, worker_id, attempt, expires_at); see §5. Migration
  `0004_claim_operation` adds the nullable JSON `operation`: the holder's provider-operation record
  (no secrets, key fingerprint only), so a process that takes over the claim can resume the paid job.
* `LibraryItem` (`library_items`, migration `0005_library_items`, `aadhi/library.py`): one row per
  (user, asset key) (`UniqueConstraint(user_id, asset_key)`): that user's `title`, `description`,
  `keywords` for a picture or clip, `kind` (image | video), `source` (upload | generated | figure),
  `origin_project_id` (SET NULL) / `origin_scene_id`, `prompt` / `provider` / `model` (generated media),
  `search_text` (the item's words and stems, for search and the matcher's SQL prefilter) and
  `last_used_at`. The media is the shared content-addressed `Asset` (`asset_key` deliberately has no
  foreign key): ownership is never read from, and nothing per user is written to, an `Asset` row. Rows
  go with their user (CASCADE). Items are added by uploads (`POST /api/uploads`, `POST /api/library`)
  and by builds (`library.record_generated`, `LIBRARY_AUTO_SAVE_GENERATED`), never from a key named in
  a request. Deleting an item leaves asset refs and assets alone; the cleanup GC keeps any asset that
  is in somebody's library.
* `VisualReview` (`visual_reviews`, migration `0006_visual_reviews`, `aadhi/review.py`): the teacher's
  sign-off of one scene's visual per version (`UniqueConstraint(version_id, scene_id)`; version CASCADE,
  `updated_by` SET NULL): `state` (pending | approved | changed | removed), `fingerprint` (sha256 of the
  scene's visual request without narration, titles or rationale, plus the chosen asset key and the
  variant), `note`. Kept outside the screenplay, so approving never changes a scene hash or a build
  cache; a fingerprint that no longer matches makes the sign-off stale. `duplicate` copies the rows.
* `generation_meta` (besides prompt versions, `ingest_key`, `reused_stages` …): `review_stage`
  (`"source"` while waiting for the source review), `source_overrides` (the teacher's corrections,
  with the extract key they were made on), `scene_history` (earlier versions of a scene kept by scene
  regeneration, newest first) and `generated_snapshot_key` (the `snapshot` blob written with the
  generated screenplay by `generate_lecture` and `translate`; never by regeneration, saves or builds;
  `aadhi/changes.py` compares the current screenplay with it and puts scenes back). A translation copies its source
  version's `ingest_key` and `source_overrides`, so regenerating one of its scenes reads the extract its scenes cite
  and keeps the teacher's set-asides out, and its edit-time lint gets the exact chunk ids and the admin-leak check
  like an original version's; a translation made before that (or a copy of one) falls back to its source version's
  under the rules of the brief (`orchestrator._version_extract`: same project and source document, at most 8 hops).
* Scene fields `hidden` (skipped by the timeline, captions, chapters and the asset build; still in the
  lecture) and `min_seconds` (1–600: the scene lasts at least this long) are left out of the JSON at their
  defaults and ignored by the asset hash, so lectures that use neither store, hash, build and render exactly
  as before. No table changed in batch 4; the lesson stage, review checkpoints and video history are derived
  from existing rows (`aadhi/stage.py`, `api/serializers.py`).
* One active version-mutating job per version (partial unique index → 409 `job_in_progress`).
* Document versions (Dynamic video B0, §13): `screenplay`, `timeline` and `asset_manifest` carry `schema_version`
  (2 classic, 3 when a Dynamic field is used). Every loader refuses a document above 3 (`NewerDocumentError`:
  409 `newer_document` in the API, a fatal job error), so an older build never drops fields it does not know.

## 5. Job system (`aadhi/jobs`)

* Claim with ONE atomic `UPDATE … WHERE id=(SELECT … LIMIT 1 [FOR UPDATE SKIP LOCKED]) AND
  status='queued' RETURNING id, attempts`, filtered by `WORKER_KINDS`.
* Lease fencing: `locked_by` = host:pid:boot_uuid:thread, `attempts` = fencing token; every
  job-owned write includes both; mismatch ⇒ `JobCancelled(reason="lease_lost")`. Heartbeat thread
  every 10 s; reaper uses `coalesce(heartbeat_at, locked_at)`; startup requeues own-host jobs with a
  different boot uuid.
* `JobContext` is non-blocking: buffered events/usage flushed by a writer thread (≤ 1 s, on stage
  change, on `await ctx.flush()`), cancel flag refreshed by the heartbeat thread, in-memory budget.
* API keys: `DBJobContext.open` calls `credentials.resolve_keys(db, settings, job.user_id)` and uses
  the returned settings as `ctx.settings` (same object when no saved key applies, otherwise a copy
  with the provider keys replaced), so every provider the job builds sees the starter's keys.
  `ctx.key_sources` (`provider → personal | server | env | None`) sets `billed_to` on each usage
  row (`credentials.billed_to`; Veo usage is paid by the Gemini key). The daily budget counts only
  `server` rows, and only a paid server-billed call can trip it; the per-lecture budget counts
  everything. Keys never travel in the job payload. A provider 401/403 (or Gemini's 400
  `API_KEY_INVALID` / expired) on a personal key fails the job at once with
  `error_code="personal_key_rejected"` (no retry, no fallback to the server key): scene writing,
  translation and asset building re-raise it instead of degrading, and media on a personal key is
  de-duplicated in flight only among the same user's jobs.
* Paid generations run once across processes (`storage/assets.py`): after the in-process de-dup,
  `get_or_create` takes an `asset_claims` row for kinds in `GENERATION_CLAIM_KINDS` (video, image),
  renews it every TTL/4 and deletes it when done. Another process waits (bounded by
  `GENERATION_CLAIM_MAX_WAIT_SECONDS`, honouring its own cancel) and takes over a claim that expired
  or whose job lease is gone. The worker runs each handler as the claim owner (its lease). Without
  the table (migration not applied) generation proceeds unclaimed with one warning. A waiter's own
  cancel or budget stop is never handed to other waiters. Each saved Veo checkpoint is also written to
  the claim row (`asset_claims.operation`); taking over a dead claim moves the record in one atomic
  update, and the taker resumes a `submitted` operation only when it pays the same way (server key, or
  the same user's personal key). A submission that was never confirmed still raises
  `video.ambiguous_submission` and is not paid again.
* Still-wanted gate: before each paid generation of a build the orchestrator's guard re-reads the
  version revision (at most every 5 s); once it changed the job stops like a revision conflict
  (`VersionChanged`) with nothing paid. Collecting an already-submitted Veo job skips the guard
  (`get_or_create(guarded=False)`).
* LLM stage checkpoints (`pipeline/checkpoint.py`, `LLM_STAGE_CHECKPOINTS`): a retried
  `generate_lecture` / `translate` attempt reuses finished stages (plan + brief, scenes, critic +
  repair, companion, translation) stored as private `intermediate` assets keyed by (job, stage, digest
  of every input); `generation_meta["reused_stages"]` lists them; orphan GC removes them. Scenes are
  also checkpointed one by one as they finish (`SceneCheckpoints`, stage `scene`: key = the scene
  stage's inputs + scene id + whether the original file went to the writers), so an attempt stopped
  mid-way keeps its finished scenes; fallback scenes are never stored. `regenerate_scene` has none.
* Paid video checkpoints live in the job payload (`provider_operations`, `providers/operations.py`):
  saved before the paid request and when Veo accepts it, so a restarted attempt (or a retry, which
  copies the payload) polls the same operation; a submission whose answer was never saved is not
  re-submitted (`video.ambiguous_submission`).
* A `render_video` job that ends outside its handler (cancelled while queued, reaped, failed before
  start) settles its `Render` row (`queue.settle_render`).
* Newer documents: before a handler runs, the worker reads only the `schema_version` markers of the job's version
  (and of `payload["source_version_id"]`, which `translate` reads) in one query; a document of a newer build fails
  the job with `error_code="newer_document"` without running it (`jobs/reader_version.py`). A loader meeting one
  mid-job fails it the same way, never as a retry. Deploys therefore stop the workers, start the API, then start
  the workers on the same image (`docs/OPERATIONS.md` §4.8).
* `WORKER_MODE=inline` runs `WORKER_CONCURRENCY` threads (own event loop each) in the API process
  (dev). Production: `python -m aadhi.worker` processes; dedicated render workers via
  `WORKER_KINDS=render_video`. Worker processes (and inline mode) do host housekeeping: at start
  they log whether this host can render MP4s (when they claim `render_video`), and at start and
  hourly they remove this host's leftovers: Manim temp dirs of dead processes and sandbox
  containers past their deadline (`manim.sandbox.sweep_orphans`) and render workspaces that can no
  longer be resumed (`compose.video.sweep_render_workspaces`). Temporary work dirs live in the app's
  scratch root (`aadhi/scratch.py`, `SCRATCH_DIR`; default `<system temp>/aadhi`, `aadhi-<uid>` and
  0700 on POSIX), and the sweeps look only there: the system temp dir itself is never listed.
* Process-wide limits (`aadhi.jobs.limits`): LLM, TTS, Manim, render semaphores usable from any
  thread/loop.
* Kinds/payloads:

| kind | payload (+ job.version_id) | result |
|---|---|---|
| `generate_lecture` | `{source_document_id, options, base_revision, review_source?, resume_state?}` | `{version_id, issue_counts}` |
| `regenerate_scene` | `{scene_id, instructions, base_revision}` | `{version_id, scene_id}` |
| `build_assets` | `{scene_ids: list or null, base_revision}` | `{version_id}` |
| `translate` | `{source_version_id, target_language, translate_board, tts_voice?}` | `{version_id}` |
| `render_video` | `{render_id, burn_captions?, include_intro, soft_subtitles?}` (`burn_captions` left out when the request did not set it for a Dynamic lecture: the presentation decides, §14) | `{render_id, video_asset_key, …}` |
| `import_legacy` | `{legacy_db_path}` or `{json_asset_key}` | `{project_ids}` |
| `cleanup` | `{}` | `{deleted: {...}}` (also expired `asset_claims`, and this host's work files) |

* SSE: `GET /api/jobs/{id}/stream` — each poll opens a short session via a thread; caps per user
  and global; `retry: 5000`; `end` event then close.

## 6. Providers (`aadhi/providers`)

* `factory.get_llm/get_tts/get_image/get_video/get_gif` from Settings; fakes for tests.
* Keys (`aadhi/credentials.py`): providers read `GEMINI_API_KEY(S)`, `OPENAI_API_KEY` and
  `ANTHROPIC_API_KEY` from the `Settings` they are given and know nothing else. For a user U and
  provider P the key is U's personal key (when `USER_API_KEYS_ENABLED`), else the server key saved
  by an admin, else `.env` (saved keys only when `STORED_API_KEYS_ENABLED`). `resolve_keys(db,
  settings, user_id)` returns `ResolvedKeys(settings, sources)`: the *same* `Settings` object when
  no saved key applies (the provider caches are keyed by settings, so `.env`-only users keep sharing
  clients), else a cached `model_copy` with those keys replaced (a saved Gemini key also empties the
  `GEMINI_API_KEYS` pool). The cache is LRU-bounded and keyed by the base settings, the user and a
  fingerprint of the relevant rows, so a saved, replaced or deleted key takes effect on the next
  resolve. Because voices, images and Veo read the same fields, a saved Gemini or OpenAI key also
  powers them. The API resolves the requester's keys for `/api/meta`, engine validation and the
  budget pre-check; workers resolve the job starter's keys (§5).
* LLM engines: `gemini`, `openai`, `anthropic` (Claude) and the offline `fake` (tests, or when it is
  the default). `LLM_PROVIDER` is the server default; a lecture may pick any configured engine
  (`GenerationOptions.llm_provider`, the Studio's *AI engine* dropdown, listed by `/api/meta`
  `llm.engines`). The API refuses an unconfigured engine (422 `engine_not_configured`), and the
  choice is stored in the version's options, so every stage and later job of that version (brief,
  plan, scenes, critic, repair, companion, translate, Manim QA) uses the same engine:
  `pipeline.integrations.llm_engine(options, settings)` picks it, `llm_model(settings, tier,
  options)` the model, `get_llm(settings, engine)` the cached client.
* Models per tier (`plan`, `script`, `critic`, `fast`) come from `factory.llm_models(settings,
  engine)`: Gemini `LLM_MODEL_*`, OpenAI `OPENAI_MODEL_*`, Claude `ANTHROPIC_MODEL_*`. A GPT or
  Claude name set in `LLM_MODEL_*` configures that engine instead (`engine_for_model`), so `.env`
  files from before engines were selectable keep working. Admin model overrides apply only when
  they belong to the lecture's engine.
* Claude (`llm/anthropic.py` + `anthropic_common.py`, official `anthropic` SDK): every call is
  streamed (`client.beta.messages.stream`); `ANTHROPIC_TIMEOUT_SECONDS` caps each whole call (a
  connection dropped mid-stream surfaces as a raw `httpx2` error and is retried). Current
  models think adaptively by default, so neither `thinking` nor `temperature` is sent;
  `ANTHROPIC_EFFORT` → `output_config.effort` (only for models that have it, `xhigh`/`max` stepped
  down where unsupported), `ANTHROPIC_MAX_TOKENS` is the output budget (includes thinking). JSON comes
  from `output_config.format` with the `anthropic` schema dialect (`llm/schema.py`: every object
  closed; length/range/pattern/item-count constraints moved into the field description and checked
  by Pydantic); a schema the API refuses to compile is sent in the prompt instead. Prompt-cache
  breakpoints sit on the system prompt, the attachments and the first user turn (re-asks resend it).
  With `ANTHROPIC_REFUSAL_FALLBACK` on, models that accept it get `fallbacks="default"`, so a
  safety-classifier refusal is re-run server-side on another Claude model; a final refusal raises
  `ContentBlocked`. Usage reports the model that served the call and counts cache reads and writes
  (`meta.cache_read_tokens`, `meta.cache_write_tokens`) so pricing bills them at their own rates;
  after a fallback every attempt in `usage.iterations` is reported separately (the declined one with
  `meta.declined`), so budgets see the whole bill (`Completion.extra_usage`).
* All I/O async; sync SDK calls via `asyncio.to_thread`; retries with backoff (tenacity) on
  429/5xx/timeouts; Gemini key-pool rotation on 429.
* `generate_json(schema, validate=...)`: native JSON-schema mode with a converter
  (`llm/schema.py`: inline `$ref`, nullable via `anyOf[null]`, drop titles/defaults, enforce
  `assert_llm_compatible` = no oneOf/discriminator/prefixItems/additionalProperties:true), Pydantic
  validation, then the semantic `validate` hook; all problems go back to the model in the re-ask.
* Errors are redacted (`Settings.redact`), carry status + provider only.
* TTS: `openai` (the default `TTS_PROVIDER`, OpenAI TTS: the primary voice), `edge` (free; word boundaries; the
  keyless fallback), `elevenlabs` (`/with-timestamps`), `gemini`, `fake` (deterministic WAV with exact word timings).
  A new lecture narrates with OpenAI when the job has an OpenAI key (the job owner's personal key, else the server's
  `OPENAI_API_KEY` or Studio key); without one it narrates with the free Edge voice for the lecture's language and
  one lecture-level `assets.tts_fallback` warning (never a failed job; the budget pre-check bills the voice the build
  narrates with, so it counts no OpenAI cost then), and the Dynamic narrator is cast the same way
  (`authoring.narration_choice`). A lecture's explicit choice of another configured provider wins.
  `factory.narrating_tts_provider` states the rule; a built lecture keeps the voice it was made with until the teacher
  re-voices it (O17, `assets.build_assets` below). A saved voice is used only by the provider it belongs to
  (`voices.voice_fits`); otherwise the narrating provider's default for the language narrates, with a lecture-level
  info note `assets.tts_voice_replaced`. Voices per language in `tts/voices.py` (OpenAI's per language:
  `OPENAI_DEFAULTS`; Edge keeps its own per-language voices). Speech text =
  `normalize_for_speech(lexicon(beat.spoken or beat.narration))`; captions keep `narration`.
* Images: `gemini`, `pollinations`, `none`, `fake`. Video: `veo` (B-roll without the mascot — the
  mascot is the background layer), `omni` (Gemini Omni through the Interactions API, Veo 3.1's successor; same
  Gemini key, same checkpoint and resume rules), `none`, `fake`. GIFs: GIPHY hotlinks (live player only).
  `VIDEO_PROVIDERS` sets the video order exactly; while it is empty a chain that names `veo` gets `omni` appended
  (`Settings._video_order`, recorded as `Settings.auto_video_backup`). Veo answering 404 / 410 (a model no longer
  served) is `ProviderUnavailable`, so the chain moves on without a charge. That automatic successor is tried only
  while Veo is unavailable (that failure, an account refusal, or Veo's `unavailable` cooldown): after a Veo
  timeout, rate limit or bad output the scene keeps its still, since the accepted Veo job may still bill
  (`pipeline/assets.py`, `_generate_video`); a named `omni` backup is tried after any fallback-able failure. Both video
  adapters expose `.model`; Omni clips are keyed by `OMNI_MODEL`. The Omni adapter keeps one genai client per key
  and event loop (`GeminiCaller(per_loop=True)`): every job runs in its own loop, and the Interactions resource's
  pooled connections belong to the loop that opened them; the last clip of a job closes them (`loop_lease`).
* Media provider chain (`pipeline/assets.py` + `factory.media_chain`): `IMAGE_PROVIDER` /
  `VIDEO_PROVIDER` first (`none` switches the medium off), then `IMAGE_FALLBACK_PROVIDERS` /
  `VIDEO_FALLBACK_PROVIDERS`; a lecture's `GenerationOptions.image_provider` goes first when set.
  Error taxonomy (`providers/base.py`): `ProviderUnavailable` (HTTP 401/402/403: payment or account,
  permanent for that provider), `ModelUnavailable` (a `ProviderUnavailable`: HTTP 404/410 on a request naming the
  configured model, i.e. retired, renamed or misspelt, or for OpenAI a model the key may not use; nothing billed;
  raised by the Gemini and OpenAI speech adapters, and a TTS model falls back to Edge with an `assets.tts_fallback`
  warning, §7), `RateLimited`, `ContentBlocked`, `InvalidMediaOutput`, `OperationLost`. The chain never falls
  back after a safety refusal or a refused personal key; a rate limit falls back only when another provider
  remains. `providers/health.py` keeps a per-process cooldown per (provider, key scope): 60 s after a rate limit/outage/bad output, 600 s
  after a payment refusal; a cooling provider is tried last, never dropped. Adapters declare
  `paid`, `aspects`, `max_prompt_chars`, `resumable`. Output is validated (images ≥ 32 px and ≤
  `AI_MAX_IMAGE_BYTES`, Veo clips ≤ `AI_MAX_VIDEO_BYTES` and ≥ 0.5 s; flat or mis-shaped images are
  kept with `assets.media_suspect`). Pollinations downloads go through `_http.fetch_limited` (https,
  allow-listed host, ≤ 3 re-checked redirects, streamed size cap). `Asset.meta` records provider,
  model, aspect, timing, job, requested provider and fallback reasons. `/api/admin/media-providers`
  shows the chains (configuration and cooldowns only). Production refuses fake providers unless
  `ALLOW_FAKE_PROVIDERS`.

## 7. Pipeline (`aadhi/pipeline`)

* Concept focus: the model only ever sees teaching content (source scoping + brief citation gating);
  lint `content.admin_leak` / `content.duplicate_intro` and critic `flow.*` issues catch anything
  that slips through, including a removed value reintroduced by a teacher's edit. Preview the exact
  prompts for any document with `python -m aadhi.pipeline.preview <file>` (no model is called).
* Prompts: versioned files `prompts/*.md` with a `PROMPT_VERSION` header recorded in
  `generation_meta`. Concise, principled, non-contradictory; no "FATAL/CRASH" threats; never mention
  implementation details. Every stage that reads the source (`style.md` for plan/scenes/repair,
  `brief.md`, `critic.md`, `practice.md`) says that instructions inside the source are teaching
  content, never instructions to follow.
* `gen_models.py`: per-scene-type response models — no ids, no unions/tuples/open dicts; beats carry
  an inline optional board item (+ `fill_previous_blank`, `highlight` by step number), quizzes are
  `{question, correct, distractors[{text, why_wrong, misconception_id}], explanation, bloom}`;
  simulation models are built dynamically with the chosen template's `params_model`.
* `canonicalize.py`: gen output + PlannedScene → canonical Scene (ids, quiz shuffle seeded by scene id,
  intent, chapter_id, objective ids).
* Grounding: `IngestResult.chunks`; `source_refs` are chunk ids (lint `source.unknown_ref`), optional
  quotes checked by substring before the critic.
* Lexicon: plan `glossary_terms` → `Screenplay.lexicon` (spoken forms per language), editable.
* Lint codes (non-exhaustive): `board.too_many_items`, `board.item_too_long`, `board.item_unrevealed`
  (info), `beat.too_long`, `beat.too_short`, `scene.too_long`, `quiz.missing_for_concepts`,
  `quiz.answer_position_skew`, `panel.missing_rationale`, `concept.unused` (prerequisites exempt),
  `objective.untaught`, `objective.unassessed`, `misconception.untargeted`, `figure.unknown`,
  `example.blank_never_filled`, `example.fill_without_pause`, `manim.template_unknown`,
  `manim.params_invalid`, `manim.beats_steps_mismatch`, `manim.code_forbidden`, `narration.markup`,
  `source.unknown_ref`, `lecture.duration_mismatch`; presenter `layout.mascot_crowds_board` (warning:
  Aadhi in the centre of a board with code, a table or more than 4 items; `presenter_lint.py`); sync
  `formula.variable_beat_invalid` (warning: a legend row's `beat_id` is not in the scene or comes
  before the formula's reveal; `sync_lint.py`). Neither is fixable by a rewrite. Hidden scenes:
  `lecture.all_scenes_hidden` (warning), `chapter.all_scenes_hidden` and `scene.hidden` (info); a hidden
  scene's own findings are downgraded to info (out of the pre-render list, never a reason for a paid rewrite),
  and coverage, quiz spacing, lecture length and duplicate-intro checks count only the scenes that play.
  `lecture.duration_mismatch` counts `min_seconds`; `scene.too_long` deliberately does not.
* Quality and consistency (`pipeline/quality/`, appended by `validate.lint`; deterministic, bounded,
  English boards for the wording checks): `terminology.variant` / `.casing` /
  `.abbreviation_undefined` / `.abbreviation_late` (info), `terminology.abbreviation_conflict`
  (warning), `concept.naming_variant`, `figure.caption_variant` (info), `formula.symbol_conflict`
  (warning), `formula.notation_mismatch`, `formula.legend_missing` (info),
  `code.language_unhighlighted`, `code.mixed_indentation` (warning; the only fixable one),
  `code.mixed_languages`, `title.casing_mixed`, `pacing.dense_run`, `board.reading_time` (info). None is
  an error, so none selects a scene for a paid rewrite, and `quality.AUTHOR_CODES` (wording, notation,
  code language, the teacher's sync anchors) are never handed to `repair.rewrite_scene`. Optional AI
  assistant (`QUALITY_AI_TERMINOLOGY`, off): one fast-model call per generated lecture about at most
  8 ambiguous term pairs → `terminology.ambiguous` (info, `source=critic`). The editor adds
  `board.overflow` (warning) / `board.small_text` (info) from the fit the preview player measured;
  those are never stored. `quality_report` (POST /lint `quality`) carries safe repairs (exact field
  edits) and the lecture's consistency registry.
* Source report (`pipeline/source_review.py`, pure; `REVIEW_VERSION` 3): outline with roles, content
  inventory (formulas on their own lines, in LaTeX and, since batch 4, inside a sentence ("V = I × R": an
  explicit operator or exponent and at least one short symbol; "a = b", "x = 5", code and URLs never count),
  and their undefined symbols, code, figures, tables, questions), `source.*` findings with an
  advisory readiness verdict, what the brief made of each chunk and which chunks each scene cites;
  values source scoping removed are masked in every text it shows. It also applies the source
  review's `SourceOverrides` (`effective_brief`, `without_chunks`).
* `assets.build_assets(...) -> AssetManifest` — incremental by per-scene content hash; graceful
  degradation (simulation → content fallback board, ai_video → Ken-Burns still) reported as issues.
  The voice provider of a new lecture (no narration yet) is the lecture's choice, else `TTS_PROVIDER` (OpenAI TTS by
  default), else, when neither has an API key for the job, the free Edge voice with one lecture-level
  `assets.tts_fallback` warning ("OpenAI TTS ... has no API key; the free Edge voice ... was used instead"); the job
  does not fail. A built lecture keeps the voice it was made with on every later build and edit (owner decision O17:
  `schemas.manifest.built_voice` of the previous manifest; an Edge-built lecture stays on Edge when an OpenAI key
  appears); only the teacher's explicit re-voice (`POST /api/versions/{id}/voice`, `build_assets` with `revoice`)
  moves it, and the budget pre-check counts the kept voice (`api.generation.build_options`).
  A voice model the provider no longer serves (`ModelUnavailable`: a retired `GEMINI_TTS_MODEL`, or
  `OPENAI_TTS_MODEL` once OpenAI retires its speech models) never leaves the lecture silent: the build switches once
  to the free Edge voice for the lecture's language (`fallback_voice_config`) with one lecture-level
  `assets.tts_fallback` warning and a job-log warning, the scene that met the error is narrated again wholly in Edge,
  and its clips are keyed like an Edge lecture's. In a built lecture each scene Edge narrates in place of the kept
  voice (this fallback, or a kept provider without a key) records that voice (`SceneAudio.stand_in_for`), so the next
  build tries it again even when Edge narrated every scene; only a NEW lecture whose first build the fallback narrated
  wholly was made with Edge and keeps it (O17).
  Without Edge on the server the scene fails as before (`assets.tts_failed`).
  A `hidden` scene is never built, even when a rebuild names it (no provider is called); media built before
  it was hidden is kept and it is never listed stale, so the build still counts as complete; it becomes
  stale again when shown, if it changed. Hidden AI video scenes keep their place in the lecture's AI video
  limit, so hiding one never lets another start a paid clip.
  Media identity: image/video prompts are `normalize_prompt`-ed (NFC, whitespace collapsed) before they
  are keyed (`storage.assets.media_key`) and sent; the pre-normalisation key is still looked up (one
  `AssetStore.first_existing` query), so nothing cached is paid for twice. When a medium is off or not
  configured on the server, media made earlier for the same request by any known provider is still
  used (`assets.cached_media_used`); a lecture that opted out never gets any. Issue codes from the
  chain: `assets.image_fallback`, `assets.video_fallback`, `assets.image_provider_fallback`,
  `assets.media_suspect`, `video.ambiguous_submission`, `video.operation_lost`. A failed animation's
  issue says why in plain words and carries `data.category`.
* Visual Review hooks in `assets`: `SidePanel.variant` / `AIVideoScene.variant` (0–99, left out of the
  JSON at 0) add `"variant"` to the image / clip content key (and to an AI video's generated still)
  only when above 0, so every existing key and scene hash is unchanged and a "new AI version" is a new
  asset while the earlier one stays cached; providers whose `generate` takes `variant` get it as a seed
  hint (Pollinations derives its seed from the prompt). `repair.carry_over` keeps a variant when a
  rewrite leaves the request unchanged (`assets.carry_visual_variant`). A build job payload
  `confirm_paid_scene_ids` lets those scenes submit an ambiguous paid clip again. A teacher's upload or
  library pick that is gone is reported as `assets.override_missing` (the generated visual still
  shows). A new version that cannot be made (generation off on the server, or a provider failure)
  keeps the latest stored earlier version (`earlier_image_version` / `earlier_video_version`, with a
  warning; a transient failure is retried by the next build). With
  `GenerationOptions.prefer_library_visuals` a side panel's generated image is replaced by a picture of
  the lecture owner's library: one plan per build (`_plan_library`, one `LibraryMatcher`), in scene
  order, through `library.auto_match` on the image prompt alone (score ≥ `AUTO_USE_THRESHOLD` 0.6 and
  ≥ `AUTO_USE_MIN_COVERAGE` 0.5 of the prompt's words covered); a picture serves one scene, and media
  generated for this lecture or the picture the scene showed never stands in (info
  `assets.library_visual_used`). With `LIBRARY_AUTO_SAVE_GENERATED` the generated images and clips of
  the built scenes join the owner's library (`library.record_generated`, never fails the build). Both
  only when the job's user owns the lecture: an admin's build of someone else's lecture uses and fills
  no library.
* Library matching (`aadhi/library.py`, deterministic, no model): a scene's visual need
  (`visual_needs`: an image side panel's title + scene title + prompt, or an AI video scene's title and
  prompts) against each item's title, keywords, description and prompt. Words of any script, lower
  case, stop words, generic picture words, numbers and negated words dropped, a light English stemmer.
  The score (0..1) mixes how much of the request the item covers (0.6) with how much of the item's
  title or keywords the request names (0.4), plus 0.15 for a whole multi-word title or keyword.
  Bounded: the 600 most recently used items plus up to 400 older ones that share one of the request's
  longest words (two SQL queries), scored through an inverted index; item profiles are cached by their
  words; one suggestions run scores at most `MATCH_MAX_PAIRS` (20,000) item × scene pairs (each scene
  the most recently used of the items sharing one of its words). Suggestions: top 3 at ≥ 0.35, never
  the media the scene shows now (`review.shown_keys`).
* `translate`: per scene, protected/translatable fields (see `TRANSLATABLE_FIELDS`), rich-lite spans
  masked as `⟦n⟧` placeholders and verified; ids preserved; lexicon `keep_in_english` honoured.
* Narration envelope (`pipeline/envelope.py`): 30 one-byte RMS loudness values per second of each
  scene's narration MP3, base64 in `SceneAudio.envelope` (asset meta and manifest). Narration cached
  before it existed is measured once from the stored file; no TTS re-runs and a failed measurement
  never fails the build.
* Rich-lite tokenizing (`richlite.tokenize`) is linear on any input: bold and `[[keyword]]` closers are
  looked up in precomputed positions instead of a lazy scan per unclosed marker.

## 8. Manim (`aadhi/manim`)

* Templates implement the `Template` protocol (`params_model`, `step_count`, `info`,
  `render_source`). Library: `equation_steps`, `function_plot`, `vector_forces`, `block_diagram`,
  `circuit_basic`, `bar_compare`, `timeline_steps`, `geometry`, `matrix_ops`, `graph_traversal`,
  `wave`, `truth_table`. One step per beat (`step_count == len(beats)`).
* `AadhiScene` (injected): `BEAT_TIMES`, `TOTAL_DURATION`, `wait_until_beat(i)`, `finish()`, safe-area
  helpers, theme colours, Noto fonts for Indic labels.
* `guard.check_code`: AST allow-list (imports manim/math/numpy/random/itertools/functools only; no
  open/exec/eval/compile/__import__/globals/locals/vars/input/breakpoint/getattr/setattr/delattr,
  no dunder attributes, no SVGMobject/ImageMobject).
* `sandbox`: docker (`--network none --read-only --tmpfs /tmp --memory 2g --cpus 2 --pids-limit 256
  --user 1000 --cap-drop ALL --security-opt no-new-privileges`) or subprocess (env allow-list, temp
  cwd, timeout + process-tree kill, output cap). Production refuses free-form code without docker.
* `render.render_manim`: cache key = hash(spec, beat_times, quality, target, background, template API
  version, manim version); the original spec maps to the healed result; self-heal loop for free-form
  code; ffmpeg pads (freeze last frame) / trims to `total_duration`.
* `qa.review_frames`: 4 frames → vision model → overlap/cut-off/readability issues → one repair round.
* Hardening (defence in depth on top of the runner): the render script carries an in-sandbox block
  (`runtime/hardening.py`, appended as text, excluded from cache keys): a `sys.addaudithook` that
  denies sockets, ctypes, process spawning and file access outside the work/media/temp dirs
  (`MANIM_AUDIT_HOOK`), a frame budget (`ceil((total*1.5+10)*fps)`) and a lock on resolution/fps
  (hard max 1920×1350, 60 fps). `run_process` adds a disk watchdog (`MANIM_MAX_WORKSPACE_MB`), peak
  memory tracking (`MANIM_MEMORY_LIMIT_MB`, enforced on RSS), a Windows Job Object (`winjob.py`: kill on
  close, a looser committed-memory backstop of 2x the limit + 2 GB that only explains a failed exit, and a
  process-count limit) or a POSIX `RLIMIT_CPU` backstop of `4 x MANIM_TIMEOUT_SECONDS + 30` CPU-seconds
  (it only stops orphaned runaways; the wall-clock watchdog is the real limit; a SIGXCPU exit counts as a
  timeout); BLAS runs single-threaded; docker runs get labels, `timeout -s KILL`
  and `--ulimit fsize`. The output must be a regular file inside the media dir, at most
  `MANIM_MAX_OUTPUT_MB` and of the profile's exact size. Failures carry a category (timeout,
  memory_limit, output_limit, frame_limit, profile_limit, blocked, latex, invalid_output, …) and a
  plain message (`ManimError.category` / `.friendly`); `peak_memory_mb` and `frames` go to the asset
  meta. `health.sandbox_health` probes the sandbox as renders run it (with `MANIM_AUDIT_HOOK` as
  configured): network, environment and a host file outside the work dir (`files_denied`)
  (`python -m aadhi.cli manim-check`, admin `GET /api/admin/manim/sandbox`). Work dirs (`aadhi-manim-*`,
  `aadhi-m-*`, `aadhi-d-*`) and the health probe's dirs are created in the scratch root (`SCRATCH_DIR`);
  orphaned work dirs are swept only there, and only when their owner pid is dead or was reused (the lease
  `BOOT_ID` is per process). Logs shown to users or models hide the work dir, the scratch root, the
  temp dir, the Python install and the home dir.

## 9. Compose & render (`aadhi/compose`)

* Stored timelines carry asset keys; `resolve_urls(timeline, store)` fills URLs at serve time. It also
  maps a retired mascot clip to its replacement (`compose.base.RETIRED_MASCOT_CLIPS`: each of
  `aadhi_left/right/center/popup.mp4` and `no_aadhi.mp4` → its `_clean` re-encode), so published
  lectures lose the black bars and the "Veo" watermark without a rebuild (the timeline ETag includes
  the mapping). The intro logo is mapped the same way (`compose.base.RETIRED_BRANDING_FILES`:
  `logo_animation.mp4` → `logo_animation_clean.mp4`, without the generator's sparkle). The original files
  stay in the branding dir.
* Word-anchored sync (`compose/sync.py`, pure, at timeline build; `SYNC_WORD_ANCHORS`,
  `SYNC_AUTO_EMPHASIS`): beat word timings become `TimedScene.sync_cues` (`SyncCue`: `var` = a formula
  legend row appears when the narration names its meaning or symbol, or on its authored
  `FormulaVariable.beat_id`; `output` = terminal output starts at "prints/returns…"; `focus` = a pulse
  of the side visual on "look at this chart"; `emphasis` = an authored highlight starts when its beat
  names the item at least `MIN_EMPHASIS_SECONDS` before the beat ends and the previous beat did not light
  it, a named table column or definition term glows alone). At most 64 cues per scene.
  The live player and the render page read them through the same `schedule.js`, so preview and MP4
  change together. Lectures where nothing matches store and render exactly as before (`sync_cues`,
  `beat_id` are left out of the JSON when empty).
* Presenter: `Layout.mascot_cues` (False only with `MASCOT_CUES=false`; left out of the JSON when on;
  set from the current setting when a timeline is served, `api/timelines.resolve_timeline`, so stored
  timelines follow the flag like the rebuilt MP4 timeline) and `TimedScene.audio_envelope` /
  `audio_envelope_fps` (from `SceneAudio`, left out when absent).
* Chapters: the chapter at 00:00 is "Introduction" unless a real chapter has that name ("Opening",
  "Lesson start" or "Opening N" then), in `chapters.youtube_chapter_list` and `timeline.build_chapters`.
* Hidden scenes and minimum durations: `build_timeline` and `preview_timeline` skip `hidden` scenes (the
  remaining scenes stay contiguous and `TimedScene.index` counts only those, so captions, chapters and
  storage keys leave hidden scenes out in the player, the preview and the MP4 alike; a render of a lecture
  whose every scene is hidden fails with `empty`, and `POST .../render` refuses it up front). `min_seconds`
  pads the scene after its content: `TimedScene.hold_seconds` (left out when 0) is the added tail, beats,
  captions, sync cues and audio keep their timing, terminal lines and the quiz teaser keep the timing they
  have without the hold, and Aadhi idles through it.
* Time base invariants are validated by `Timeline` (contiguous scenes, fade-in inside the scene,
  `audio_offset`, scene-relative inner times, absolute captions/chapters).
* Stage size (Dynamic video B0): `compose.base.stage_size(format)` is 1920×1080 for landscape (and every classic
  lecture) and 1080×1920 for portrait; `build_timeline` stores it as `Timeline.width` × `height`. The renderer
  reads it back (`timeline_stage`: the page viewport, screenshot and state-PNG size, segment specs) and encodes
  `output_size(tl, settings)`: exactly `RENDER_WIDTH` × `RENDER_HEIGHT` for the classic stage, other stages scaled
  by the configured short side. Captions wrap to `split_caption(..., width=)` and the output check samples black
  edges transposed for portrait (`qa.edge_sample_size`).
* Render-mode page contract (`web/render.html` + `web/js/render/`), at the timeline's stage size (1920×1080 for
  every classic timeline; `render.js` `stageSize` / `applyStageSize` set the page's CSS size and viewport at boot),
  DPR 1:
  * `window.aadhiRender.ready` → true after timeline, fonts (all families incl. the timeline's script)
    and MathJax are loaded.
  * `aadhiRender.states(sceneIndex) → [{t, key}]`: scene-relative times at which the visual state
    changes (scene start, beat starts, fills, highlight on/off, panel show_at, terminal lines, quiz
    countdown seconds, reveal) — computed by the player's own schedule code.
  * `aadhiRender.show({scene, t}) → Promise<{state_key, media_rect, panel_media_rect, media_fit}>`
    resolves only after: MathJax idle (`texIdle()`), visible images decoded, `document.fonts.ready`
    after layout, Chart.js created with `animation:false`, three.js rendered once at a fixed angle,
    then two rAF ticks. Background is transparent; mascot/background video, captions and media
    elements are hidden (`visibility:hidden` → transparent holes).
  * `aadhiRender.showIntro({t})` renders intro title cards the same way.
* `video.render_video`: per scene: mascot clip (or static bg) → media in rects (`fit`, Ken-Burns via
  zoompan from `KenBurns`, `end_behavior`) → PNG overlays switched at state times with short alpha
  fades → scene audio + ticks/ding; segments in parallel (render semaphore), concat, BGM with
  `sidechaincompress`, chapters via ffmetadata, `+faststart`; optional caption burn-in using the
  vendored fonts. Chromium keeps its sandbox, network restricted to the app origin via `page.route`
  (`RENDER_BASE_URL` when set: then only that internal origin is allowed).
  ffmpeg/ffprobe always get `-protocol_whitelist file,pipe` and explicit formats.
* Preview parity: the mascot loop keeps its phase across scenes and only the scene layer fades in
  (0.6 s crossfade on a position change; `RENDER_MASCOT_CONTINUITY`); panels use the live player's
  translucent glass colours without the blur (`RENDER_GLASS_PANELS`, `web/render.html?glass=1`);
  state cross-fades are mixed in premultiplied alpha (one stream of PNGs, the mixed frames written before
  the encode), so a translucent panel keeps its opacity through a state change;
  stills and overlays are converted to BT.709, untagged HD media videos (intro logo, AI clips, Manim
  videos) are relabelled BT.709 rather than converted, and the MP4 is tagged BT.709 (`RENDER_COLOR_BT709`). An
  optional selectable mov_text caption track (`soft_subtitles`, default `RENDER_SOFT_SUBTITLES`) is
  never added with burned-in captions. `evals/render_parity.py` compares preview screenshots with the
  MP4 at the same instants.
* Before rendering: `compose/capabilities.py` checks ffmpeg/ffprobe, libx264/aac, libass and Chromium
  (cached; a missing tool fails once with `render_unavailable`, not retried); `compose/preflight.py`
  lists what the MP4 shows differently (silent scenes are blocking; boards replacing missing media,
  title-only panels, online GIFs left out); affected panels render title-only and the items are saved
  in `Render.options["warnings"]`. `preflight.quality_items` lists the version's open lint / reviewer /
  generation (system) errors and warnings (at most 12, `reason: "quality"`, never blocking) apart from
  those items.
* Workspace (`compose/workspace.py`): each render works under `<DATA_DIR>/render-work`
  (`RENDER_WORK_DIR`), one folder per attempt; encoded segments are cached by content so a retry
  re-encodes nothing that is unchanged; removed on success or cancel, kept `RENDER_KEEP_FAILED_HOURS`
  after a failure; refused below `RENDER_MIN_FREE_GB` (`disk_full`).
* Output check (`compose/qa.py`, `RENDER_OUTPUT_QA`): frame count vs the timeline, frame grid,
  declared rate, audio present/audible, black edges, colour tags, caption track. No picture, a frame
  count off by more than 1, no audio track or silence despite narration fail the job
  (`render_invalid`); the rest are warnings. The result is saved in `Render.options["qa"]`.

## 10. Frontend (`web/`)

Native ES modules served under `/web/`, JSDoc types (`npm run typecheck`), `node --test` unit tests
(jsdom available). The player renders a fixed **1920×1080 logical stage** scaled with a CSS transform
in every mode, so preview and export wrap text identically. (The render page is sized from the timeline, §9.) The
live player sizes a Dynamic stage from the timeline (1080×1920 for a reel, §14) and marks its root
(`data-stage-shape` plus `--ap-stage-w` / `--ap-stage-h`) while the stage is not 1920×1080, so the watch page's
`.watch-player` and the Studio preview's `.preview-box` take a 9:16 reel's shape; classic and 16:9 boxes are
unchanged.

### Player API (`web/js/player/player.js`)

```js
const player = new Player(rootEl, { mode: 'live' | 'preview' | 'render',
  analytics: { versionId, shareToken } | null, captions: true, autoplay: false });
await player.load(timeline);
player.play(); player.pause(); player.seek(seconds); player.seekScene(index);
player.on('timeupdate' | 'scenechange' | 'ended' | 'quizanswer' | 'error' | 'blocked', handler);
player.destroy();                       // releases WebGL contexts, timers, audio, blob URLs
// render mode: player.states(sceneIndex), player.renderState({sceneIndex, t}), player.renderIntro({t})
player.captionSize; player.captionSizeAdjustable;   // getters: false with a Dynamic stage (its captions keep their size)
player.captionBottom;                   // stage px under a Dynamic stage's caption band (null: the classic captions)
```

* Clock: the scene narration `<audio>` is the master clock while narration plays; a performance.now()
  clock covers the lead-in, pauses, countdown and tail. Everything visible is a pure function of
  `(scene, t)` (`schedule.js`), so seeking and render mode are exact.
* Next scene's audio/media preloaded. Scenes: board, chapter/title cards, media (video or Ken-Burns),
  quiz checkpoint, interactive (sandboxed p5). Panels per §ARCH-panels via `createPanel`.
* Mascot: `MascotController` crossfades the position clips; speech-reactive motion in live mode follows
  the build-time loudness envelope (`TimedScene.audio_envelope`, `audio.envelopeLevel`), else a WebAudio
  analyser of the narration element (created only when some narrated scene has no envelope), else a
  synthetic bob while speaking; optional Rive rig hook (`branding.mascot_rig_url`). Behaviour states
  are a pure function of `(scene, t)` (`mascot-state.js`, through `sceneStateAt`): idle in the lead-in
  and tail, explaining while a beat reveals or fills a board item and talking otherwise, thinking in
  pauses of at least 1.2 s (from 0.6 s after the speech), and in quizzes question / listening (countdown)
  / success (reveal). They go to `data-state` on the mascot layer; a cue bubble beside the head (dots, "?",
  a gold check) is drawn in the scene layer at fixed stage pixels for the left, right and centre
  positions, so the render screenshots show it exactly as the player does (`Layout.mascot_cues`).
  `presenter-vocab.js` maps wanted behaviours onto what the clips can show.
  Robustness (live/preview): only the clips the lesson uses load eagerly (`Player.load` passes the
  positions); each clip shows its poster (`/branding/posters/<clip>.jpg`) until it has a frame; the
  crossfade waits for the incoming clip's first frame (≤ 1.5 s); load errors retry after 1 s and 3 s;
  `tick()` (from the frame loop, ≤ 1/s) restarts clips the browser paused, pauses strays and treats
  3.5 s without progress as a stall. While the active clip is retrying, stalled, blocked or broken its
  poster breathes (CSS scale only; static background if the poster is missing; off with reduced
  motion). `getStatus()` reports state and counters (`MASCOT_TIMING` holds the constants).
* Autoplay blocked: when the browser refuses narration `play()` without a gesture the player emits
  `blocked`, pauses once (the `pause` event and its analytics record carry reason `blocked`) and waits
  for the user's play.
* Analytics: batched `fetch(..., {keepalive: true})` with the CSRF header (never sendBeacon).

### Studio (`web/index.html`, `web/js/studio/`)

Hash router: `#/login`, `#/change-password`, `#/projects`, `#/new`, `#/p/:id`, `#/p/:id/v/:vid/plan`,
`#/p/:id/v/:vid/source`, `#/p/:id/v/:vid/edit`, `#/v/:vid/review`, `#/p/:id/analytics`, `#/usage`,
`#/keys`, `#/library`, `#/videos`, `#/admin`. Job progress via SSE. Nav: Projects, New lecture, Library,
Videos, Usage (+ Admin); the links get a little narrower below 1280 px.

* Lesson stage (`lib/lessonStage.js`, `components/stage.js`): the project page's "Next step" card (the stage
  in words, one sentence, one primary action, and a strip Source · Plan · Script · Voice and visuals ·
  Video) and a stage chip with "Next: …" on each project card, from the server's `stage` / `next_step`
  (derived from the summary on older servers). Links are followed only when they are `#/…` or same-site
  `/…`; build, make the video and retry run on the page; unsaved editor changes in this browser turn
  build / render into "Open the editor to save your changes". Finished renders say "Up to date" / "Out of
  date" (`matches_current`) and have "Watch" when the server sends a `preview_url`.
* Videos (`views/videos.js`, `#/videos`): the user's videos across their lectures (`GET /api/videos`),
  filters All / Up to date / Out of date in `?filter=`, "Load more", a refresh every 10 s while a video is
  being made (keyboard focus kept; a read that only renews presigned links leaves the list as it is, and
  videos loaded past the 100-item re-read stay), playback in a dialog (`components/videoPreview.js`, HTML5
  `<video controls preload=metadata>` on the signed `preview_url`, released on close; one fresh link is asked
  for when the link no longer loads; focus goes back to the redrawn Watch) and download. Lectures sharing a
  title get the same suffix as on the project list.
* New lecture: "Try an example" imports the bundled `web/examples/ohms-law.json` (5 scenes, no paid
  media, lint-clean; only the voice-over is built) through `POST /api/projects/import`.
* Polish: technical details (render and job numbers, revisions, raw job stages) only with `?debug`
  (`lib/debug.js` `showTechnical()`); polls pause while the tab is hidden and poll once on return
  (`lib/visibility.js`); lectures that share a title get a short suffix (`lib/names.js`); `--fs-min`
  (0.75rem) is the type floor and `--c-text-subtle` / `--c-accent-text` the Studio's AA text tokens
  (`--c-text-faint` stays for the player and the renderer); `web/tests/ui_tokens/` enforces both.
* Editor (batch 4): a timeline strip under the panes from the same preview Timeline as the embedded
  player (`timelineStrip.js`: scene, narration, visual and caption lanes, chapter marks, zoom, seek, drag a
  block to move a scene, hidden scenes as thin marks); the preview reports and seeks by scene id
  (positions differ once scenes are hidden), the selection follows playback only while playing and never
  while typing, and a paused selection shows a still. Inspector: "Skip this scene in the video" (`hidden`,
  the last playing scene cannot be skipped) and "Show for at least" (`min_seconds`); "Split here" at a beat
  boundary (`splitScene.js`, a pure transform: a new scene id, the beats after the cut and the board items
  they reveal move, the visual and the hold stay with the first part); "Picture from my library…" in the
  add menu; foldable inspector sections. Save state in words with Retry / Review; "Save automatically"
  (off by default, per user, in this browser only; never while invalid, conflicting, locked, in a dialog,
  during "Save as a copy" or a revert, after a merge with clashes until a save by hand, or again after a
  failed save until the next change or Retry); "Save as a copy…" (`POST .../duplicate`, then the unsaved
  edits go to the copy); labelled undo ("Undo: Edit narration in scene 3", 200 steps; a job's result is its
  own step, "Changes from the job"); keyboard shortcuts (`?`, Space outside form controls and media, `[` /
  `]`, Delete, `components/shortcutsDialog.js`). A running job that writes the version locks editing (found
  when the editor starts one, and polled every 15 s while the tab is visible; pending inspector edits reach
  the draft first) and its result is merged field by field with local edits (`sceneMerge.js`, also for save
  conflicts, restored drafts and edits typed while a revert is out). The findings of a skipped scene are
  notes and offer no rebuild or rewrite; preflight lines number scenes as the scene list does. "Generated vs edited" (`changes.js`,
  `changesPanel.js`): Edited / New / Moved badges from `GET .../changes`, a side-pane list with removed
  scenes, and "Compare and revert…" (`POST .../scenes/{id}/revert`, confirmed first, undoable).
* Transforms keep what they do not understand (Dynamic video B0): a scene type change keeps every common scene
  field (`screenplayEdit.js` `COMMON_SCENE_KEYS`: `hidden`, `min_seconds`, `look`, `colour_is_content` …) and,
  between board types, the board with its `sequence` (`BOARD_SCENE_KEYS`); reference repair keeps a blank
  `question` and the beat that fills it (`BLANKABLE_KINDS`); "Split here" moves `Beat.answers` and the
  presentation hook with the items and beats they name. The transform-survival tests
  (`tests/core/test_transform_survival.py`, `web/tests/studio/transformSurvival.test.js`, over
  `tests/core/fixtures/transform_survival_dynamic.json` and `tests/schemas/fixtures/screenplay_v3_maximal.json`)
  are a gate of every batch: `PUT`/`GET`, revert, export/import, regeneration carry-over, translation, local
  drafts, merges, splits and every type change must leave a maximal Dynamic document unchanged.

* Library (`views/library.js`, `#/library`): the teacher's pictures and clips (`/api/library`) with
  search (debounced; kind and source filters kept in the URL), pages of 48 with "Load more", upload of
  several files (button or drop; checked before upload, one at a time with progress over
  `XMLHttpRequest` with the CSRF header), an edit dialog (title, description, keyword chips, "Suggest
  with AI" when `/api/meta` `library.ai_describe_enabled`; the suggestion is only saved with Save) and
  delete (the confirmation says how many lectures use it and that they keep working). The picker
  (`components/libraryPicker.js`, `openLibraryPicker({app, kind?, projectId?, title?, sources?})`)
  resolves with the chosen item, after `POST /api/library/{id}/attach` when a project is given; the
  editor's media controls ("Choose from library") and the Visual Review use it. Server strings are
  always set as text.
* Visual Review (`views/visualReview.js`, `#/v/:vid/review`, "Review visuals" on each editable version of
  the project page and in the editor): one card per scene with a visual (preview, source badge, "Made
  by provider (model)", folded prompt, status in plain words, sign-off chip, findings, the actions the
  server lists), filters (`?filter=all|attention|not_approved`), `?scene=` focus, a "Build changed
  scenes" banner and the running job. "New AI version" and the paid retry of an ambiguous clip ask for
  confirmation first; "Upload replacement" goes to the library, then becomes the scene's visual.
  Actions send the `revision` the review was read at. The editor's issues panel offers "Rebuild this
  scene", "Review visual" and, for `video.ambiguous_submission` / `video.operation_lost`, "Generate
  again…" (the paid confirmation); its inspector shows the visual of the last build; the project page
  shows "N visuals need attention" for the current version. The options form offers "Prefer pictures
  from my library" (`prefer_library_visuals`) when `/api/meta` has `library`.

* New lecture: "Upload a file | Paste your notes" (pasted text is cleaned of control characters and
  sent as a `.txt` file through the same upload, 200 to 200,000 characters or the server's
  `limits.max_source_chars`, enforced on submit with a message, never by cutting the text), and an opt-in "check how Aadhi read my document" (`review_source`).
* Source report (`views/sourceReport.js`, `#/p/:id/v/:vid/source`, "How Aadhi read your document"):
  readiness, things to check, outline with roles, what was set aside (counts only), concepts, formulas,
  every part with its status and which parts each scene cites; while the generation waits for the source
  review it saves corrections and continues. A version awaiting review carries `review_stage`, so links
  go to the source report or the plan review (which also forwards a source pause to the report).
* Issues panel: one-line headline, issues grouped by area, severity as icon and words, codes behind
  "Show technical details"; safe repairs ("Apply fix", one undoable edit), "Regenerate scene" only on
  fixable errors, "Build" while stale. The editor preview's measured board fit adds
  `board.overflow` / `board.small_text`. Under the preview, "Timing of scene N" lists beat start times
  and the moments placed on spoken words (`player/syncSummary.js`), marked "estimated" before the voice
  exists. A formula legend row can be set to appear with a chosen beat (`FormulaVariable.beat_id`).

* API keys: `#/keys` (`views/apiKeys.js`, cards from `components/apiKeyCard.js`) lists the user's
  personal keys (`/api/keys`); its nav link appears only when `/api/meta` `api_keys.personal_enabled`.
  While personal keys are switched off the page still lists keys saved earlier, with Remove only.
  The Admin page embeds the "Server API keys" section (`serverKeysSection`, `/api/admin/keys`;
  hidden when the server answers 404). A typed key lives only in its masked key input (a CSS-masked
  text field, not `type=password`, so browsers' password managers never offer to store it; emptied
  after saving or cancelling) and is scrubbed from any error shown. The New lecture form asks
  before leaving once a file or details were entered (e.g. via the link to `#/keys`). The Usage
  page's "Budget used today" is the server-billed `today_usd`. Saving or removing a key drops the
  cached `/api/meta`, so the *AI engine* dropdown (" (your key)" for engines on a personal key, a
  link to `#/keys` when an engine has no key) is rebuilt. A job that failed with
  `personal_key_rejected` links to `#/keys`; the Usage page shows `own_key_usd`.
* Media: the options form offers an *Image provider* picker when `/api/meta` lists `image.providers`;
  the Admin page has a "Media providers" panel (`/api/admin/media-providers`).
* Rendering: the editor's *Render MP4* dialog first loads `/render/preflight`; silent scenes offer
  "Fix first" (default) or "Render anyway" and the request's `allow_degraded` follows the choice (a
  409 `render_preflight` asks again). An optional "selectable caption track" sends `soft_subtitles`.
  The dialog also lists the quality check's open errors and warnings (at most 12, never blocking).
  The project page shows the preflight list, the quality list under its own heading and, per render,
  its output check and affected scenes. Both render dialogs (the editor's "Render MP4", the project page's "Make the
  video") tell a Dynamic render from a classic one only by the preflight's `presentation`
  (`lib/renderOptions.js` `dynamicRender(preflight)`); without that answer (the request failed, or the editor did not
  check a stale lecture) they show the classic dialog. For a Dynamic lecture they pre-check "Burn captions" from
  `presentation.captions_burn`, always send `burn_captions`, leave out the intro option and, in the editor, send
  `soft_subtitles` explicitly when the captions are not burned (pre-checked, the R-31 default). The job stepper
  (`lib/jobStages.js` `RENDER_STAGES`, `renderEngine`, `stagesFor`) follows the engine the job reports: classic
  screenshots, segments, mux, check, store; motion preflight, capture, mux, check, store; `done` marks every step
  done.

## 11. Security model (details: `docs/SECURITY.md`)

* Sessions: JWT `{sub, tv, typ:"session", iat, exp, jti}` HS256 in an httpOnly SameSite=Lax cookie
  (`__Host-aadhi_session` on https, `aadhi_session` on http dev); Bearer only when a client explicitly
  asks for a token. `tv` = `token_version` (bumped by logout, password/role/active changes). Role and
  active status loaded from the DB on every request; `must_change_password` enforced server-side
  (403 `password_change_required`).
* Scoped tokens (render): `{typ:"scoped", scope:"render", vid, exp}` signed with an HMAC key derived
  from JWT_SECRET (purpose label), passed in the URL *fragment* and sent as a header by render.js.
* CSRF: mutating requests with cookie auth require `X-Aadhi-CSRF: 1` AND a matching `Origin` /
  `Sec-Fetch-Site: same-origin` (or an allowed CORS origin). CORS: exact origins only, never `*`.
* CSP (app pages): `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';
  font-src 'self'; img-src 'self' data: blob: https://*.giphy.com <CDN>; media-src 'self' blob: <CDN>;
  connect-src 'self'; frame-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none';
  form-action 'self'; frame-ancestors 'self'`. Sandbox page (`/sandbox/p5`): `default-src 'none';
  script-src 'self' 'unsafe-eval' 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:;
  connect-src 'none'; frame-ancestors 'self'`, embedded with `sandbox="allow-scripts"` only (code via
  postMessage, origin-checked both ways, heartbeat watchdog). Media: `Content-Security-Policy:
  sandbox; default-src 'none'`, `nosniff`, `Content-Disposition: attachment` for non-media types.
* Uploads: extension + MIME + magic bytes allow-list, size caps, DOCX zip-bomb limits, PDF page caps,
  `Image.MAX_IMAGE_PIXELS`, server-generated names. JSON body cap (`MAX_JSON_BODY_MB`).
* Authorization: `load_version(user, vid)` (owner or admin, project not deleted) on every version
  route; cross-references (version ids in bodies, asset keys via `AssetRef`) must belong to the
  path project; share tokens derive project/version server-side; deleted projects are not watchable.
* Secrets: `SecretStr`, `Settings.redact()` on every error/log/job message, subprocess env
  allow-lists; production refuses unsafe configs (`validate_for_runtime`).
* API keys saved in the Studio: Fernet-encrypted at rest (`CREDENTIALS_ENCRYPTION_KEY`, else HKDF
  from `JWT_SECRET`), write-only over the API (hint only), decrypted only in memory and registered
  with the redaction registry (`security.redaction.register_secret`), which every
  `Settings.redact()` consults. Users reach only their own personal keys; server keys are
  admin-only. Unreadable rows are skipped, never fatal.
* Rate limits keyed by user and client IP (`TRUSTED_PROXIES` for X-Forwarded-For) + DB budgets.

## 12. Conventions

* Python 3.11, type hints, `ruff` clean, no blocking I/O in `async def`, atomic SQL for state changes.
* `logging.getLogger(__name__)` + redaction filter; never log secrets or full prompts at INFO.
* Tests: pytest with fakes, `app_env` fixture (fresh DB/data dir); `@pytest.mark.slow` for real
  manim/ffmpeg/playwright output; `@pytest.mark.container` (also slow) for the ffmpeg feature checks the Docker
  `test` image runs on Debian's ffmpeg 5.1 (`AADHI_EXPECT_FFMPEG=5.1` pins the version there). The image also sets
  `AADHI_CONTAINER_TESTS=1`, under which a missing tool or stage bundle fails the Dynamic suites instead of skipping
  them (`tests/compose/dynamic/helpers.missing`).
* Classic invariance (from B0): `tests/invariance/` goldens (fast suite) and the differential pixel test (merge
  base vs HEAD in the Docker `test` image, CI job `container`, measured tolerances) must pass in every batch; a
  classic change on purpose carries the `classic-change` PR label and regenerated goldens.
* Frontend: never `innerHTML` with dynamic data (`shared/dom.js`), never `eval`/`new Function`, no
  model ids as DOM ids, release everything in `destroy()`.

## 13. Dynamic video foundations (batch B0)

The Dynamic video mode (`docs/DYNAMIC_VIDEO_DESIGN.md`) ships in batches. B0 changes nothing a teacher or student
sees: classic lectures keep their screenplay JSON, scene hashes, timeline and MP4 (checked by `tests/invariance/`).
It lays down the contracts the later batches build on:

* **Inert schema v3.** Every Dynamic field and enum value of the design (§6.7, §8.2) exists in the screenplay,
  timeline and manifest models, is validated and kept by every transform, and is left out of the JSON at its
  default (`exclude_if`). `scene_hash` ignores `look` and the beats' `overlays` (they never change the audio) and
  keeps `cuts`, `speaker` and `voice_override_asset_key`; `repair.carry_over` keeps `look` and the scene's v3
  intent (role, `visual_intent`). Plans rebuilt from a screenplay (`plan_from_screenplay`, for repair and
  regenerate) leave clip scenes out and give v3 roles as their classic counterparts (`pretraining` ->
  `prerequisite`, `predict` -> `context`, `recap` -> `synthesis`) until B4 widens `PlannedScene`; a clip scene
  itself cannot be regenerated (409 `action_unavailable`).
* **Write gates.** `DYNAMIC_FEATURES` (`presentation`, `items_v3`, `cuts`, `overlays`, `clip`, `voice_override`)
  lists what a server accepts. Saves, lint, the timeline preview and imports that use another feature get 422
  `feature_unavailable` (`api.screenplay_service.require_features`); a feature the stored lecture already uses
  stays editable.
* **Reader-version marker** (`schemas/features.py`): documents that use a Dynamic field are written with
  `schema_version` 3; anything above 3 is refused by the API (409 `newer_document`; write paths, Visual Review included,
  check the stored screenplay and asset manifest before writing) and the workers (`jobs/reader_version.py`). Because B0 knows every v3 field, it is the rollback floor (`docs/OPERATIONS.md` §4.8).
* **Asset authorisation** (`assets_auth.py`): the clip of a clip scene, overlays, the music bed and the teacher's
  own narration are authorised like the classic key fields (`FIELD_RULES`, `asset_uses`); import strips what the
  user may not use.
* **Format plumbing** (§9): stage and output sizes come from the timeline (`stage_size`, `output_size`), so a
  portrait timeline renders on a 1080×1920 stage while the classic one stays 1920×1080. Burned-in captions size
  from the frame's short side (`ffmpeg.subtitles_filter`): a portrait render gets the classic glyph size.
* **Audio and pricing groundwork:** `compose/loudness.py` (EBU R128 measurement and two-pass `loudnorm`); the
  `music` price operation; unknown operations priced fail-closed; the up-front budget check counts generated music,
  premium voices and their calibration when the Dynamic options carry them.
* **Gemini Omni** (`providers/video/omni.py`): Veo 3.1's successor as a video provider (§6), because Google lists
  the Veo 3.1 previews for shutdown from 2026-10-22.
* **Gates:** the classic invariance harness and the ffmpeg 5.1 checks run in the Docker `test` image (CI job
  `container`), the transform-survival tests (§10) and the batch-4 regression tests
  (`tests/api/test_batch4_regressions.py`, `tests/pipeline/test_batch4_regressions.py`,
  `web/tests/studio/batch4Regressions.test.js`) run with the suites.

## 14. Dynamic video: the motion engine (batch B1)

B1 renders Dynamic lectures (`docs/DYNAMIC_VIDEO_DESIGN.md` §7) behind the admin flag `DYNAMIC_ENABLED` (off by
default). With the flag off `/render-dynamic` is 404 and a lecture with a `presentation` is built, played and
rendered as a classic lecture (no `Timeline.presentation`, the classic intro, `RENDER_FPS`, classic captions), except
that its stage still follows `presentation.format` (`compose/timeline.py` sizes every timeline with `stage_size`, a B0
decision): a portrait presentation gives a classic timeline on a 1080×1920 stage, which the classic renderer renders
on a 1080×1920 page and encodes as a portrait MP4 (`output_size`, §9: 1080×1920 at the default output size), while
the live player shows it on its 16:9 classic stage. The flag applies when a timeline is served
(`api/timelines.current_timeline`, like `MASCOT_CUES`): a build of the current revision whose
engine differs from what the flag and the screenplay ask for now is served rebuilt (`build_timeline`, as the render
job builds it); a stale build (the screenplay changed since) is served as built, except a Dynamic build while the
flag is off, which is rebuilt from the current screenplay as a classic timeline. So turning the flag off stops the
Dynamic stage at once and the player never disagrees with the MP4. The API and the render workers must share the
flag: the worker builds the timeline it renders with its own settings, and the API decides from its own flag whether
a request without `burn_captions` leaves it out of the job payload (`api/routers/renders.py`
`render_presentation`). A worker with the flag off would render such a lecture classic without `burn_captions`, and
the classic job then burns captions as `RENDER_BURN_CAPTIONS` says instead of not at all. The classic engine,
`web/render.html` (contract v2) and their output are unchanged (the invariance goldens and the differential pixel
test check it).

* **Timeline** (`compose/timeline.py`, `compose/presentation.py`). With the flag on, a screenplay with a
  `presentation` gets `Timeline.presentation` (`TimelinePresentation`: format, resolved content colours, fonts,
  caption style, `captions_burn`, `engine_version`) and one `ResolvedLook` per scene: the template and variant
  `compose/dynamic/direct.py` chose (`board_generic` as the fallback);
  background `colour_block` for chapter cards and `mesh_drift` elsewhere, toned `bg` / `bg-alt` / `bg-tint` within a
  chapter; `crossfade` (0.45 s) into a new chapter and into or out of a colour block (R-54), hard cuts inside a
  chapter (R-23); the structure colours of the four R1 theme families; seeds are SHA-256 of `(look_seed, look_epoch,
  scene id, purpose)`. From Dynamic video batch B2a the colours come from `compose/dynamic/themes.py` (on
  `colour_math.py`), the zones and type floors from `zones.py`, and the template registry from `templates.py`; the
  generated `web/js/dynamic/catalogue.gen.js` carries their geometry to the stage. The resolver writes look-engine
  generation 2: `look.tokens` also stores the block and scrim colours the stage derived at run time, a scene's palette
  slot follows `Timeline.chapters` (or `SceneLook.palette_slot`), crossfades last 0.3-0.45 s, and the presentation
  gains `content_alt`, `family`, `mode`, `motion_scale` and `entity_colors` (`DYNAMIC_VIDEO_DESIGN.md` §4.7, §6.7).
  A stored Dynamic timeline of an older generation is served rebuilt; the stage refuses a newer one. A Dynamic timeline has no classic intro, runs at
  30 fps (`DYNAMIC_FPS`) on the format's stage (1920×1080 or 1080×1920), so its word-anchored sync cues snap to that
  30 fps grid (`SyncOptions.from_settings(settings, fps=DYNAMIC_FPS)`; classic timelines keep `RENDER_FPS`) and, in
  9:16, wraps phrase captions at 16 characters,
  grouped by `presentation.portrait_caption_cues` so no chunk flashes by (R-32: ≥ 1.0 s where the beat allows it)
  and long words split only between grapheme clusters, keeping their word's timing.
* **Engine switch** (`compose/video.render_video`): a timeline whose `presentation.style` is `"dynamic"` goes to
  `compose/motion/job.render_dynamic`, every other one to the classic path; one render never mixes engines. The
  Dynamic branch checks the flag (`dynamic_disabled`), tools and disk, runs **preflight**, captures, runs the
  **container checks** and stores MP4, SRT and VTT like the classic path. `Render.options["qa"]` gets the motion QA
  document (`docs/API.md`). Captions are burned (drawn by the stage) when the request asks for it, else as
  `presentation.captions_burn` says (`motion.job.caption_modes`; `POST .../render` leaves an unset `burn_captions` out
  of a Dynamic lecture's payload), and a clean file gets the default-on caption track unless the request turns it
  off. Its render warnings are the classic ones worded for the motion engine plus one per side panel the stage does
  not draw yet (a chart, graph, 3D model, skill tree or quiz panel: reason `panel_not_drawn`,
  `motion.job.UNDRAWN_PANEL_KINDS`), in scene order: nothing is dropped silently.
* **One compositor** (`web/js/dynamic/`). `stage.js` `createDynamicStage(root, timeline, {mode})` paints a frame as a
  pure function of `(timeline, t)`: background (`backgrounds/mesh_drift.js` on a 1/8-size canvas, drifting in 0.2 s
  steps; `colour_block.js` with a coarse seeded grain; both frozen and desaturated during holds), template
  (`templates/board_generic.js`: title words rising in, cards entering 120 ms before their beat, older cards
  collapsing into a "+N earlier" chip, chapter cards, quizzes with a countdown ring and the answer, media and split
  layouts, in both formats), transition (`transitions.js`: cut, crossfade over the outgoing scene's final state,
  clamped to 300-500 ms and never longer than the scene) and captions (`captions.js`: `clean_pill` / `bold_outline` in
  a fixed band), styled by `web/css/dynamic.css` (also linked by `watch.html` and `index.html`). Colours come from the
  roles: the stage and each scene set `--dyn-ink` and `--dyn-page` (`stage.js`), which colour the stage's background,
  inherited text and the matte around contained media, and a card sets its own; the only literal colours
  in `dynamic.css` are neutral shadows and the captions (white on a dark pill or outline, and the key-term yellow
  until B2's `mark` role), so a light family gets no dark literal. The live player (`web/js/player/player.js`) loads
  it on demand for a Dynamic timeline: the narration stays the master clock; clips are real `<video>` elements,
  re-sought past 0.1 s of drift and played at the player's rate, and a paused clip that cannot seek yet is sought
  once it can. A reel resizes the stage to 1080×1920 and the page's box takes its shape (§10), and the control bar
  fits that box (`web/css/player.css` §12: the `ap-shaped-box` size container drops mute, then the time, the scene
  list and the speed as the box narrows; play/pause, the seek bar, captions and fullscreen always stay). The bar lifts
  the stage's captions only past their band's bottom (`Player.captionBottom`: 48 stage px in 16:9, 680 in 9:16,
  which a bar rarely reaches); the caption-size control is disabled ("Caption size: set by this video": the captions
  keep their format's size, as in the MP4, and the viewer's classic size preference is kept). A Dynamic lecture
  preloads no quiz tick or ding, computes the classic scene state only to duck the branding music, paints nothing in
  a hidden tab except at scene changes, and reports a stage failure once per scene and message (at most 20 per
  timeline). The render page `web/render-dynamic.html` + `render.js` drives the same code in render mode (stage
  contract v3, `docs/INTERFACES.md`). A medium the mode cannot show (in the render: a GIPHY GIF or anything kept out
  of the MP4) takes no frame: the scene is laid out without it. The same holds for a side panel `board_generic` does
  not draw yet (a chart, graph, 3D model, skill tree or quiz panel), in the live player and the MP4 alike; a render
  warning says so. In 9:16 a table of more than two columns is drawn one line per row under a legend of its headers
  (a grid would be clipped in the text column). A terminal panel's card enters with its first output line. A quiz
  question keeps its rich-lite markup (TeX, bold, terms, code) word by word, with bold at weight 900 against the 700 title (no heavier face in Inter or the Indic
  faces, so there it does not stand out); in 16:9 the title block ends 24 px left of the 96 px countdown ring. A
  medium shown `contain_blur` sits over a blurred, darkened copy of its own frame drawn in a 64 px wide canvas
  (`media.js` `BACKDROP_WIDTH`), none when it covers its box.
* **Motion toolkit** (`web/js/motion/`, in-house, pure): CSS-exact cubic-bezier easing and the §4.8 tokens, keyframe
  tracks (numbers, arrays, OKLCH colours), OKLCH parsing / mixing / gamut mapping and WCAG contrast,
  `Intl.Segmenter` grapheme / word / line splitting (Indic conjuncts kept whole), stroke drawing, mulberry32 with a
  documented `hashSeed`, an `en-IN` count-up. No GSAP, Remotion or tsParticles (§7.13).
* **Determinism** (§7.4). Every visual property is a function of `(timeline, frame)`.
  `web/tests/dynamic/sourceScan.test.js` parses `web/js/dynamic/**`, `web/js/motion/**`, `web/css/dynamic.css` and
  `web/render-dynamic.html` with the TypeScript compiler API and fails on wall clocks (`Date.now`, `performance.now`,
  `new Date()`, `document.timeline`), unseeded randomness, `requestAnimationFrame` and timers, CSS transitions,
  animations and `@keyframes`, `element.animate()`, Chart.js without `animation: false`, self-clocked libraries,
  `<video>`, DOM globals inside `web/js/motion`, and structure colours or emphasis on template content elements.
  Stage infrastructure may keep a reasoned exception on the same or the previous line:
  `// determinism-allow raf|timer|video: <reason>` (today: the one animation-frame wait of `seek()` and the live-only
  `<video>` elements); the motion toolkit and the templates have none. After `ready` the timer guard (`guard.js`)
  makes `setTimeout` / `setInterval` throw and records the trip (`window.aadhiStageGuard`, log line
  `aadhi timer guard`). The page is never driven with `page.clock`; CDP `Animation.setPlaybackRate(0)` is the backstop
  for stray CSS animation. Measured necessities (each of these made frames depend on the order they were rendered):
  no `will-change` and no promoted layers, except a `contain_blur` clip's box (`will-change: transform`: Chromium
  composites every shown 2D canvas, so without a layer for the box the clip above its backdrop changed with render
  order; a picture must not be promoted); moving cards snap to whole pixels and media frames rise instead of
  scaling; and the stage's Chromium is launched with `--disable-partial-raster` (`stage_page.DETERMINISM_ARGS`, the
  Dynamic stage only): partial raster re-rasterised only the invalidated part of a tile, whose antialiasing differed
  by a few levels from a full-tile raster (76 of 300 portrait frames differed between 1×300 and reverse; none with
  the flag, at no measurable capture cost).
* **Capture and encode** (`compose/motion/`). `W = min(6, CPU quota - 1, physical cores - 1, cpuset - 1)` workers
  (cgroup v2 `cpu.max` / v1 `cfs_quota_us`; `RENDER_MOTION_WORKERS` overrides), limited by host-wide CPU slots
  (`RENDER_CPU_SLOTS` lock files in `RENDER_SLOT_DIR`, `flock` / `msvcrt.locking`, released by the OS when a process
  dies). Each worker is one sandboxed `chrome-headless-shell` with one page (the classic capture's flags plus
  `--disable-partial-raster`, network policy and token-in-fragment; not launched with reduced motion); the workers
  share one Playwright driver. Every worker's page must report the timeline's stage size, rate, frame count and scene
  frame ranges before it captures (an empty scene list is a `stage_contract` mismatch), and a page that fails to come
  up is closed at once. Frames
  are captured with CDP `Page.captureScreenshot` (JPEG q92, `optimizeForSpeed`) and piped to one x264 encoder per
  chunk (the §7.5 command plus `-frames:v N` and `-threads 4`), as MPEG-TS. Chunks are scene-aligned, 240-600 frames,
  taken longest first; a chunk's packet count must equal its frames; a failed chunk (a crashed page, a wrong count,
  an encoder failure) is captured once more on a fresh page, a contract violation never is; pages are recycled
  after 1,800 frames or when their rate halves. Chunks are cached in the render workspace by content key (stage
  bundle, engine version, frame range, the timeline slices that touch them, media identities, encoder and capture
  parameters including the Chromium launch flags, Chromium version), so a retry captures only what is missing. The
  master audio (the classic per-scene mix and the ducked branding BGM, AAC-LC 48 kHz stereo 384 kbps) is encoded
  while the frames are captured; the final mux copies chunks and audio, adds the chapters and, for a clean file, a default-on `mov_text` track, with
  `+faststart+negative_cts_offsets`.
* **Media in the stage** (§7.7). For each chunk the worker asks `mediaPlan(f0, f1 + 10)`, fetches only sources the
  timeline names, extracts the listed source frames (`-ss` half a frame early) at the largest size they are shown,
  and answers the page's `/__frames/<key>/<n>.jpg` requests with `route.fulfill` from its workspace; the frames are
  deleted after the chunk and never reach the API. A clip at another rate than the page's grid (a 24 fps Veo clip on
  the 30 fps grid) is extracted at its own rate and page index n gets file frame `floor(n * rate / srcFps)`, so a
  frame never depends on where its chunk starts. A panel not shown yet lists its first frame (the page paints it while
  hidden). With presigned storage URLs every page the engine opens gets freshly signed links. The page's boot paint
  never asks for them (`ready` does not wait for per-chunk frames). A chunk that fails anywhere from its media plan
  on leaves none of its extracted frames behind. Overlay clips are not fetched before the capture until the stage
  draws overlays (B6: `motion.engine.OVERLAYS_DRAWN`), so a missing overlay asset cannot fail a render it would not
  appear in.
* **Preflight** (`compose/dynamic/preflight.py`, §7.12) loads the stage once and seeks 24 spread frames, every scene's
  first and last frame, every beat's settled frame (0.6 s after it starts: a card that later collapses is checked
  while it is on screen) and, in 9:16, every caption chunk's (in B1: phrase caption's) first frame. From
  `textBoxes()` it checks text overflow (too tall, past the column with the cards on screen with it, or clipped at
  the sides), safe zones (R-59 bands, from `zones.py` since B2a; with burned captions no other critical text may reach into
  the caption band), analytic contrast (R-53) and the R-29 type floor (labels ≥ 40/44 px, 9:16 text ≤ 64 px at weight
  ≥ 600; warn only). The page draws exactly the render's caption layer (`setLayers` after every load), so captions
  are checked only when the render burns them (the request's `burn_captions` wins over `presentation.captions_burn`).
  The page must report the engine's frame count, the scene-bounds total (`motion.chunks.scene_frame_bounds`: a final
  scene shorter than half a frame keeps one frame). Remedies (shrink to the slot minimum, the template's safe variant,
  the scrim) are written into the served timeline's looks (from B2a into `look.fixes` only; the stage still reads B1's
  `variant: "safe"` / `text_fit` of older timelines) and checked again, with one item kept per check, scene and
  slot across both rounds; what is still wrong blocks with a teacher action (`preflight_blocked`, "Shorten text");
  contrast and the type floor only warn. The title block reports its text in the slots `kicker` and `title` (a
  chapter card's subtitle in `title`); a scrim for either covers the whole block, and its text is then reported on
  the scrim composited over the background, so a contrast remedy that works ends `fixed`. A TeX word wider than the
  column at the title's minimum size holds the title at that size and blocks there.
* **Container checks** (`compose/dynamic/container.py`): exact frame count per chunk and in total (remedy: capture
  those chunks again, on a fresh browser that waits for a CPU slot of its own, cancel-aware, since the capture's
  slots were released when it ended), first video frame at 0 also without the edit list and the audio within one
  frame, BT.709 TV-range tags, frame grid and declared rate, caption track (remedy: mux again), audible sound
  (remedy: encode the audio again); black edges warn. Each remedy runs at most once; a problem that survives fails with `render_invalid`.
* **Not yet** (later batches): the burned second version, draft renders, static-run deduplication and the Studio
  render estimate (B4/B9); persisting preflight's remedies into the lecture so the live player shows them too (until
  then a remedy changes only the MP4); the analytics flags of the two allowed live/MP4 differences and the low-end
  fallback; `cues.js`, the other R1 templates and backgrounds (B2); the side panels `board_generic` leaves out (charts
  with B2's `chart_build`); overlays (B6); the WebGL probe in `capabilities.py` (B7).
