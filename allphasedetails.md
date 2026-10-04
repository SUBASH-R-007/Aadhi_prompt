# Aadhi EduEngine — Complete Documentation (Phases 1–21)

This document describes Aadhi EduEngine as it stands after Phase 21, for developers who will change the code and for
stakeholders who need to know what works, what was verified and what is still limited. The first part covers the whole
system (product, phase summary, architecture, module map, running locally, testing); the middle part has one section per
phase; the last part lists the current known limitations, a glossary and the environment variables. The detailed history
of every phase (audits, measurements, issues found, full test results) stays in `all-phases-details.md`.

## Contents

- 1. Product overview
- 2. Phase summary
- 3. Architecture
- 4. Module map
- 5. Running locally
- 6. Testing
- Phase 1 — Mascot Reliability
- Phase 2 — Reliable Video Recording / Export
- Phase 3 — Asset Library
- Phase 4 — Visual Router
- Phase 5 — AI Media Cache, Deduplication & Reuse
- Phase 6 — Visual Review, Approval & Scene-Level Visual Control
- Phase 7 — Recording Performance, Export Pipeline & Video Output
- Phase 8 — Multi-Provider AI Media System
- Phase 9 — Recovery, Resumability & Fault-Tolerant Generation
- Phase 10 — Secure Manim Sandbox
- Phase 11 — Source Document Formatting Assistant
- Phase 12 — AI Presenter / AI Teacher
- Phase 13 — Cinematic Scene Generation
- Phase 14 — Intelligent Scene Composer
- Phase 15 — AI Visual Director
- Phase 16 — Presenter + Visual Synchronization
- Phase 17 — Professional Video Styling System
- Phase 18 — AI Video Quality & Consistency Engine
- Phase 19 — Advanced Video Editor
- Phase 20 — End-to-End AI Educational Video Studio
- Phase 21 — UI/UX Refinement, Simplification & Product Polish
- Known limitations (current, after Phase 21)
- Glossary
- Appendix A — Environment variables
- Appendix B — Where to find more

## 1. Product overview

**Aadhi — Educational Video Studio** (the page title since Phase 21) turns a teacher's document or notes into a narrated
educational video, in one browser page served by the application's own server, stored under the teacher's account.

### What a teacher does, end to end

1. **Sign in** with an account the admin created (Settings → General → "Add a user").
2. **Create a lesson** from the top bar (Home · Create · Library · Videos · Settings): *Upload a document* (PDF, DOCX,
   TXT), *Paste your notes* or *Open a lesson file* (`.json`). For a document, the Document Assistant first shows how
   Aadhi read it — sections to teach, things to check, ideas for visuals and quiz questions, each traced to its place in
   the source — then "Write the lesson from this".
3. **The lesson is written** by an AI text model (Gemini or OpenAI) on the server, as a durable run that survives a
   closed tab or a restart. Each scene records whether its content came from the source or from the AI.
4. **The Studio's seven stages** guide the rest: Content · Lesson · Visuals & presenter · Style · Review & edit ·
   Preview · Export. Each shows its state in icon and words (Complete, In progress, Ready, Needs attention, Not started)
   and one main action; any stage opens directly, and a reopened lesson lands on its next useful stage.
   - *Visuals & presenter:* "Prepare visuals" finds or makes each scene's picture, clip, chart or animation — the
     Library and the built-in renderers first, AI media last — and lists problems with Retry · Choose existing ·
     Continue without. The presenter is Aadhi (the mascot), the drawn Aadhi Teacher, an AI presenter clip or nobody.
   - *Style:* Academic, Cinematic Education, Children's Education or Corporate Training, with a few bounded choices;
     a style change never makes pictures or clips again.
   - *Review & edit:* Visual Review (approve, change or remove each visual, presenter and layout), the editor (reorder,
     hide, split, retime and rewrite scenes) and the quality check ("✓ Quality looks good" / "⚠ N things to review").
   - *Preview* plays the lesson; *Export* records it into a WebM video (plus an MP4 copy where the server can make one,
     WebVTT subtitles and chapters) listed under "Your videos".
5. **Reopen later** from "Your lessons" (each lesson's stage in words: Being written, Ready to edit, Needs attention,
   Ready to export, Video ready, …); edits, approvals, style and quality state are kept.

The classic one-step generation (document straight to a playing lesson) remains under Settings → Advanced. Providers,
models, rule ids and fingerprints are shown only when the page is opened with `?visualDebug`.

### Main concepts

| Concept | Meaning |
|---|---|
| Lesson | One saved project: a JSON document (`projects.json_data`) with the scenes, the concept map and every plan, decision and setting made for them; the single source of truth for preview, review, quality and export. |
| Scene | One step of the lesson: title, board content (HTML), narration with `[SYNC]` / `[PAUSE]` markers, a type (title, content, example, quiz_checkpoint, summary, …), a stable `scene_id` and its plans. |
| Visual | What a scene shows in a *slot* (main or side): a library asset, a built-in renderer (chart, graph, 3D model, code terminal, skill tree, quiz, p5), a Manim animation, an AI image or video, a GIF, or nothing. Chosen by the Visual Router. |
| Presenter | Aadhi (clip-based mascot), Aadhi Teacher (drawn SVG with expressions and gestures), an AI presenter clip, a custom presenter, or none; planned by the Presenter Director. |
| Scene style | *Classic* (the original layouts, unchanged) or *Cinematic* (composed scenes); composition, direction, synchronization and video styles apply only to Cinematic scenes. |
| Style | The lesson's look in Cinematic mode: one of four versioned style families, resolved on the server into design tokens with an accessibility guard. |
| Studio | The guided workflow from source to exported video (Phase 20, refined in 21); it orchestrates the other systems and generates nothing itself. |
| Editor | The workspace around the live stage for refining a lesson by hand: scene list, timeline, inspector, undo / redo, autosave in place. |
| Visual Review | Where the teacher approves, changes or removes each scene's visual, presenter and layout; its decisions are authoritative for preview, reload and export. |
| Quality | A deterministic, read-only report (81 rules) on readability and consistency, with safe repairs and suggestions; it never blocks an export. |
| Preview | Playing the lesson in the page; the live stage is the only renderer, so the preview is what the video shows. |
| Export | Recording that same page (tab capture), uploading in chunks, validating on the server, storing the video with subtitles, chapters and an optional MP4 copy. |

## 2. Phase summary

The status column is shortened from the status table of `all-phases-details.md`, which keeps the full wording.

| Phase | Topic | Status | Outcome (one line) |
|---|---|---|---|
| 1 | Mascot reliability | Complete with limitations | One `MascotController` keeps Aadhi moving: placement clips, narration-driven states, preloading, an animated poster fallback. |
| 2 | Reliable video recording / export | Complete with limitations | The lesson tab is recorded, uploaded in resumable chunks, validated with ffprobe, stored per user and downloadable later. |
| 3 | Reusable Asset Library | Complete with limitations | Media registered once by content hash, with stable IDs, ownership, lesson references, safe deletion and signed links. |
| 4 | Visual Router | Complete with limitations | One rule chain plans every scene's visual (asset, existing media, library, built-in, Manim, then AI); planning never generates. |
| 5 | AI caching | Implemented; its export blocker was resolved in Phase 6 (all suites pass) | An equivalent AI image or video is generated once per user and reused; forced regeneration keeps the old version. |
| 6 | Visual review | Complete | Keep / change / remove / new AI version per scene slot, saved with the lesson and honoured by reload and export. |
| 7 | Recording performance, MP4 / subtitles / chapters | Complete | Measured 30 fps recording mode without stalls; MP4 copy, WebVTT subtitles and chapters from the lesson's own timing. |
| 8 | Multi-provider AI media | Complete (no real provider produced media here; Pollinations now paid) | One provider layer with registry, capabilities, controlled fallback, bounded retries, output validation and provenance. |
| 9 | Recovery / resumability | Complete (verified with stand-in providers) | Durable runs, leases, attempts and a recovery manager: interrupted work continues, never a duplicate paid generation. |
| 10 | Manim sandbox | Complete (Windows AppContainer runtime tested; Docker runtime implemented, untested) | AI-written Manim code runs only in an OS sandbox as a durable job; no unsafe fallback. |
| 11 | Source document formatting assistant | Complete (AI analysis verified with the stand-in model only) | PDF, DOCX, TXT and pasted text analysed with provenance into an editable Aadhi-ready structure for lesson writing. |
| 12 | AI presenter / AI teacher | Complete (AI presenter with the stand-in provider only; Aadhi Teacher verified in Chrome) | Presenter Director, the drawn Aadhi Teacher, speech timelines from the narration, presenter decisions in Visual Review. |
| 13 | Cinematic scene generation | Complete (verified in Chrome; AI background with the stand-in only) | Composition plans (templates, safe areas, camera, timeline, transitions, backgrounds) rendered by the page and the export. |
| 14 | Intelligent scene composer | Complete (verified in Chrome at 720p and 1080p; AI-assisted composition with the stand-in model only) | Scene intent plus scored candidate decisions choose each composition deterministically, with repair and fallback. |
| 15 | AI visual director | Complete (verified in Chrome at 720p and 1080p; AI-assisted direction with the stand-in model only) | A per-scene teaching strategy passed to the router, presenter and composer as preferences. |
| 16 | Presenter + visual synchronization | Complete (verified in Chrome at speech rates 1.0 and 1.3; AI alignment with the stand-in model only) | One synchronization plan per scene fires highlights, labels, camera moves and presenter acts on the narration's clock. |
| 17 | Professional video styling system | Complete (four style families rendered and inspected in Chrome; old lessons unchanged; no media regenerated) | Four versioned style families as tokens, resolved once per scene on the server with an accessibility guard. |
| 18 | AI video quality & consistency engine | Complete (verified in all four styles; AI terminology assistance with the stand-in model only) | Deterministic lesson-wide quality report (81 rules), safe repairs, suggestions and the Quality panel. |
| 19 | Advanced video editor | Complete (verified in Chrome on a real lesson; preview and export matching; nothing generated by the editor) | Scene list, timeline, inspector and undo / redo around the live stage; edits saved in place with revisions. |
| 20 | End-to-end AI educational video studio | Complete (two lessons rendered and inspected; user edits protected; stand-in providers only) | One Studio workflow from source to export over Phases 1–19, with durable lesson-writing runs and derived lesson state. |
| 21 | UI/UX refinement, simplification & product polish | Complete (preview and export match; stand-in providers only) | One product shell and design system, plain words, keyboard and screen-reader support, layouts for desktop, tablet and phone. |

## 3. Architecture

### 3.1 Backend

- **FastAPI app (`server.py`).** Serves the page, its modules and stylesheets, login, the original generator routes
  (`/generate-script`, `/generate-audio`, `/render`, `/generate-ai-video`, `/generate-ai-image`, `/upload-*`,
  `/save-history`, `/start-ai-server`, `/get-image`, `/get-gif`, …) and `/api/ai-media/...`, and mounts the routers of
  exports, assets, visuals, presenters, cinematic, quality, editor, source documents and the Studio. At start-up it adds
  missing tables and columns, creates the default admin on an empty database, registers Aadhi's shared media as system
  assets and starts the recovery manager.
- **Database.** SQLAlchemy over SQLite by default (`sqlite:///./projects.db`) or `DATABASE_URL` (`postgres://` is
  rewritten to `postgresql://`). Tables: `users`, `projects` (lessons), `video_exports`, `export_outputs`, `assets`,
  `asset_references`, `ai_generations`, `ai_generation_locks`, `ai_generation_runs`, `ai_generation_attempts`,
  `source_documents`, `document_analyses`, `presenter_profiles`. `create_all` adds tables, `database.ensure_schema`
  (Phase 9) adds columns and indexes. Phases 13–21 changed no schema: their data lives in the lesson JSON.
- **Storage.** Local disk through `storage.py` (`LocalStorage`), which turns server-chosen keys into paths and refuses
  unsafe ones. Volumes: `assets` (`ASSETS_DIR`, default `./assets`; uploads and AI images once per content),
  `static` (`STATIC_DIR`, default `static_videos/`; narration, Manim renders, AI and presenter videos), `system`
  (`video_template/`; Aadhi's clips, posters, intro, music) and `exports` (`EXPORTS_DIR`, default `./exports`).
  `/static`, `/images`, `/video_template` and `/final_videos` are public mounts; library files and exports are private.
- **Authentication.** `POST /api/login` returns a JWT (HS256, signed with `JWT_SECRET`, valid 7 days); passwords are
  bcrypt hashes; only `admin` may create users (`POST /api/register`). Scoped link tokens (`access.py`) let media
  elements load private files — 12 hours for a library asset, 30 minutes for an export — and are never logins.
- **AI provider layer.** One interface (`ai_providers.py`) for Pollinations and Gemini image (pictures), Veo, local LTX
  and the manual workflow (videos) and a presenter media type with no real provider yet. `ai_media.py` picks one,
  consults the AI cache (`ai_cache.py`) first, retries only transient failures, falls back in a controlled way,
  validates the output and registers it as a library asset with provenance. Text models (Gemini, OpenAI) use one call
  (`source_documents.call_model`); narration audio through `/generate-audio` (Edge-TTS by default; Gemini, OpenAI or
  ElevenLabs voices with keys). **Stand-ins** (`AI_FAKE_PROVIDER=1`, test servers): every provider replaced by local
  ones (ffmpeg media, a deterministic lesson writer, with `FAKE_TTS=1` a speech-like tone); no real one registered.
- **Background runs and recovery.** Provider generations, Manim renders, AI document analyses and lesson writing are
  durable runs (`ai_generation_runs`; kinds AI media, `manim`, `document_analysis`, `lesson_script`) with their request,
  a guarded state machine (`ai_runs.py`), a heartbeat-renewed lease and one attempt row per provider call. The recovery
  manager (`ai_recovery.py`, at start-up and periodically) resumes a saved provider job instead of submitting a new one,
  finalizes idempotently, checks Visual Review decisions first, and marks a run *needs attention* rather than risk a
  duplicate paid generation.
- **Manim sandbox.** Manim code is untrusted: checked statically (`manim_security.py`), rendered as a durable job
  (`manim_jobs.py`) in an OS sandbox (`manim_sandbox.py`: Windows AppContainer + Job Object, tested; Docker, untested)
  with no network, no secrets, its own workspace and resource limits, and validated before it becomes an asset. Without
  a secure runtime: "Secure Manim execution is unavailable in this deployment." — no unsafe fallback.

### 3.2 Frontend

- **One page, `index.html`:** the stage (board, side panel, mascot layer, captions), the scene renderers (Chart.js,
  JSXGraph, Three.js, MathJax, Prism, GSAP; lesson p5 code in a sandboxed frame), playback and narration, the
  lesson-writing prompt (`getSystemPrompt`) and the adapters that connect each module to the page.
- **UMD modules** loaded as classic scripts, each exposing a global for the page and `module.exports` for Node tests:
  `AadhiMascot`, `AadhiExport`, `AadhiAssets`, `AadhiVisuals`, `AadhiReview`, `AadhiSources`, `AadhiPresenter`,
  `AadhiCinematic`, `AadhiEditor`, `AadhiEditorUI`, `AadhiStudio`. `app.js`, loaded last, is the app shell.
- **Stylesheets** in order: `ui.css` (product tokens, first), the page's own styles, `studio.css`, `editor.css`,
  `stage-fit.css`, `product.css` (last). Product colours never follow the lesson's video style.
- **The live stage is the only renderer.** Preview, playback, the editor's view and the export use the same page. The
  export records that tab (`getDisplayMedia` + `MediaRecorder`, 30 fps, 8 Mbit/s up to 720p and 12 Mbit/s above,
  captures over 1080p scaled down) with the product UI hidden, so the preview is what the video shows. (In preview the
  Default voice may be the browser's speech; synchronized scenes and drawn or AI presenters use the server's audio.)

### 3.3 The lesson pipeline

```
 source: PDF / DOCX / TXT (blocks extracted by sources.js) · pasted notes · .json lesson file
   │  Document Assistant: source_documents.py + source_analysis.py (structure, findings, provenance, edits)
   ▼
 Studio lesson run: studio.py + studio_screenplay.py — durable "lesson_script" run → text model (stand-in on
   test servers) → check_screenplay → trace (scene.source) → save_lesson  ⇒  the lesson (projects.json_data)
   ▼
 Visual Router and media: visuals.py → scene.visual_plan (review decision, explicit asset, existing media,
   library match, built-in renderer, Manim, AI last)
     AI needed  → ai_media.py: cache (ai_cache.py) → provider (ai_providers.py) → run (ai_runs.py, ai_recovery.py)
     Manim code → manim_jobs.py → sandbox (manim_sandbox.py)            both → Asset Library (assets.py)
   ▼
 presenter: presenters.py → scene.presenter_plan (drawn by presenter.js; Aadhi by mascot.js)
   ▼
 Cinematic scene style only:
   direction    visual_director.py → scene.visual_direction (a preference for router, presenter and composer)
   composition  scene_intent.py + composer.py → cinematic.py → scene.cinematic_plan (layers, camera, timeline)
   sync         sync_director.py → cinematic_plan.sync (events at narration positions)
   ▼
 style: styles.py → cinematic_plan.style.look (tokens, applied by cinematic.js)
   ▼
 editor: editor.js / editor_ui.js ↔ editor_api.py (scene ids, scene.edit, payload.editor, revision checks)
   ▼
 quality: quality.py + quality_style / _media / _education / _timing.py → a report on demand, never stored
   ▼
 Visual Review: review.js ↔ /api/visuals/review, /api/presenters/review, /api/cinematic/review, …
   ▼
 preview: the live page (index.html + cinematic.js), same plans, same renderer
   ▼
 export: export.js records the tab → exports.py + export_outputs.py → WebM, MP4, WebVTT, chapters → Your videos
```

### 3.4 Lesson data and cross-phase rules

A lesson's JSON carries, per scene, `scene_id` (19), `visual_plan` (4), `visual_review` (6, 12, 15),
`presenter_plan` (12), `cinematic_plan` with `sync` and `style.look` (13, 16, 17), `visual_direction` (15), `edit` (19)
and `source` (20); and at lesson level `source_document` (11), `cinematic_style` (17), `editor` (19) and `studio` (20).

- **Planning never generates:** media is made only by explicit actions, the AI Visuals setting or "Prepare visuals".
- **The user's explicit choices win** over stored plans, AI suggestions and defaults; server-side writers re-apply their
  change to the freshly loaded lesson and write only if its revision is unchanged (`editor_api.update_lesson`).
- **Nothing is generated twice** (library and cache first, identical requests attached, recovery resumes) and **secrets
  stay on the server** (environment only, provider errors sanitized).

## 4. Module map

### 4.1 Backend (Python)

| File | Responsibility | Introduced in phase (later changes) |
|---|---|---|
| `server.py` | FastAPI app: authentication, page and module routes, original generator routes, router wiring, start-up and shutdown | Before Phase 1 (every phase) |
| `database.py` | Engine and sessions from `DATABASE_URL`; `ensure_schema`, the additive migration step | Before Phase 1 (9) |
| `models.py` | ORM tables | Before Phase 1 (tables added in 2, 3, 5, 7, 8, 9, 11, 12) |
| `storage.py` | `LocalStorage`: storage keys to paths, path safety | 3 |
| `media.py` | File type from the file's own bytes and ffprobe; shared media checks | 3 |
| `access.py` | Signed, scoped link tokens for assets and exports | 3 |
| `exports.py` | Export jobs, resumable upload, validation, storage, signed links, download, cleanup, background MP4, export recovery | 2 (3, 7, 9, 20) |
| `export_outputs.py` | WebVTT subtitles, chapters, encoder detection, MP4 conversion | 7 (20, 21) |
| `assets.py` | Asset Library: register, adopt in place, deduplicate, references, resolve, metadata editing, system assets, API | 3 (4, 6, 7, 12, 13) |
| `visuals.py` | Visual Router: requests, rule chain, library matching, review decisions, plan storage, lesson batch helpers | 4 (5, 6, 8, 9, 10, 15, 19, 20) |
| `ai_cache.py` | AI generation identity and hash, lookups, entries, locks, counters | 5 (8, 9, 12) |
| `ai_providers.py` | Provider interface, adapters, registry and selection, error categories, sanitizing, stand-ins | 8 (9, 12, 20) |
| `ai_media.py` | AI media service: selection, cache, retries, fallback, validation, provenance, run execution and recovery | 8 (9, 12, 20) |
| `ai_runs.py` | Run state machine, guarded transitions, leases and heartbeats, attempt history | 9 (10, 12) |
| `ai_recovery.py` | Recovery manager: start-up and periodic passes, executors by run kind, sweeps | 9 (10) |
| `manim_security.py` | Render profiles and hard limits, static checks (AST allow-list), code fingerprint | 10 |
| `manim_sandbox.py` | Runtime detection, Windows AppContainer + Job Object runtime, Docker runtime, workspaces, health check | 10 |
| `manim_runner.py` | Trusted bootstrap run inside the sandbox (profile, frame counter, audit hook) | 10 |
| `manim_jobs.py` | Manim renders as durable jobs: cache, output validation, asset registration, cancel, concurrency | 10 (20) |
| `source_analysis.py` | Rule-based structural analysis of source documents (pure functions) | 11 (21) |
| `source_documents.py` | Source documents and analyses: service, AI analysis job, the shared text-model call, API | 11 (20) |
| `presenters.py` | Presenter profiles, Presenter Director, safe composition, speech timelines, presenter clips and review | 12 (14, 15, 16, 19, 20) |
| `cinematic.py` | Composition plans (templates, layers, safe areas, camera, timeline, transitions, backgrounds), validation, review; the cinematic API with direction and style routes | 13 (14–17, 19, 20) |
| `scene_intent.py` | Scene intent: purpose, content, density, media, priority, ambiguity | 14 (15, 16, 18) |
| `composer.py` | Candidate composition decisions, scoring, repair, fallback; optional AI-assisted composition; the plan's `sync` | 14 (15, 16) |
| `visual_director.py` | Scene understanding and the per-scene visual direction plan | 15 (16) |
| `sync_director.py` | Synchronization plans: events anchored to narration positions | 16 |
| `styles.py` | Style registry (four families, versions), tokens, resolver, accessibility guard, fingerprint | 17 |
| `quality.py` | Quality engine core: context, report, fingerprints, staleness, API | 18 (19) |
| `quality_style.py`, `quality_media.py`, `quality_education.py`, `quality_timing.py` | Quality rule families: style / typography / colour / background; presenter and media; terminology, concepts, formulas, code, diagrams (optional AI term grouping); timing, camera, motion, density | 18 (19) |
| `editor_api.py` | Scene ids, the editor data contract, `GET` / `PUT /api/editor/{id}`, compare-and-set `update_lesson`, `reviewed_scene` | 19 (20) |
| `studio.py` | Studio API: lesson-writing runs, the derived lesson state, checkpoints, the lesson list | 20 |
| `studio_screenplay.py` | Screenplay parsing, checking and repair; the test servers' stand-in writer; source traceability | 20 |

### 4.2 Frontend (JavaScript and CSS)

| File | Responsibility | Introduced in phase (later changes) |
|---|---|---|
| `index.html` | The page: stage, scene renderers, playback, narration and captions, lesson prompt, module adapters, export hooks | Before Phase 1 (every phase) |
| `app.js` | App shell: top bar, Settings dialog, player bar More menu, notices, inert page and focus handling | Before Phase 1 as the history modal; rewritten in 21 |
| `mascot.js` | `MascotController`: Aadhi's clips, behaviour states, preloading, stall recovery, poster fallback, metrics | 1 (7, 13) |
| `export.js` | `LessonRecorder`, `ExportFlow`, upload client, "Your videos" panel and history | 2 (7, 18, 20, 21) |
| `assets.js` | Asset API client, lesson asset resolver, Library panel (browse, pick, upload, describe) | 3 (4, 6, 7, 21) |
| `visuals.js` | Visual Router client, AI Visuals setting, AI media and providers API, source labels | 4 (5, 8, 9, 20, 21) |
| `review.js` | Visual Review: visual, presenter, layout, direction, synchronization, style and quality blocks | 6 (8, 12–18, 21) |
| `sources.js` | Document extraction (pdf.js, mammoth, text) and the Document Assistant panel | 11 (21) |
| `presenter.js` | Presenter settings, the presenter layer, the drawn Aadhi Teacher, presenter acting | 12 (16, 21) |
| `cinematic.js` | `CinematicStage`: layers, camera, synchronization runtime, style application, caption fit; settings and API client | 13 (14–18, 20, 21) |
| `editor.js` | Editing model: commands, undo / redo, autosave, drafts | 19 (21) |
| `editor_ui.js` | Editor workspace: top bar, scene list, timeline, inspector | 19 (21) |
| `studio.js` | Studio UI: Your lessons, Create, writing progress, the seven stages | 20 (21) |
| `ui.css` | Product design tokens (`--ui-*`) and opt-in `.ui-*` classes | 21 |
| `product.css` | Shell styles: top bar, start screen, Settings, player bar, dialogs, focus, reduced motion | 21 |
| `stage-fit.css` | The two caption-fit rules for Cinematic scenes | 21 |
| `studio.css` | Studio styles | 20 (21) |
| `editor.css` | Editor styles | 19 (21) |

Also `video_template/` (Aadhi's clips, posters, intro, music), `sandbox/Dockerfile.manim` (10) and `tests/`. Other root
files (`old_index.html`, `index2.html`, `app_test.js`, `speak.js`, `main_script.js`, `check_inline.js`, `patch_*.py`,
`fix*.py`, root `test_*.py`, `extractor.py`, `update.py`, `upgrade_history.py`, `rewrite_sync.py`, `run_tts.py`,
`safe_escape.py`) are neither loaded by `index.html` nor imported by `server.py`.

## 5. Running locally

### 5.1 Prerequisites

- **Python** in a virtual environment at `.venv` (Python 3.11 on the development machine and in the `Dockerfile`), with
  `pip install -r requirements.txt`.
- **ffmpeg and ffprobe** on `PATH`: media validation, export finishing, the MP4 copy (needs `libx264` and AAC; otherwise
  only the WebM is offered) and the stand-in media and voice. Without ffprobe, checks fall back to file signatures.
- **Chrome or Edge** for exporting (tab capture with tab audio needs a Chromium-based browser).
- **Node.js** (Node 24 on the development machine) and `playwright-core` for the frontend tests and browser checks.
- **Optional:** LaTeX for Manim `Tex` / `MathTex` scenes (not installed on the development machine; the server adds a
  MiKTeX folder to `PATH` when it finds one); Windows 10/11 for the tested sandbox runtime, or Docker (untested).

### 5.2 Configuration

Copy `.env.example` to `.env`; every variable is optional and Appendix A explains each. The main ones: `DATABASE_URL`,
`JWT_SECRET` (without it everyone is logged out at each restart), `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ASSETS_DIR`,
`EXPORTS_DIR`, `STATIC_DIR`, `AI_GENERATION_ENABLED`, and the test-only `AI_FAKE_PROVIDER` and `FAKE_TTS`. `server.py`
and `database.py` load `.env` with `override=True`: a value in `.env` beats the same variable set in the shell.

### 5.3 Starting the server

From the repository folder (the server reads `index.html` and mounts its media folders relative to it):

```
./.venv/Scripts/python.exe -m uvicorn server:app --host 127.0.0.1 --port 9942
```

Open `http://127.0.0.1:9942` (the development server the Phase 1–4 browser checks expect). `python server.py` starts the
same app on port 8000 on all interfaces; the `Dockerfile` uses port 7860. **First sign-in:** a fresh database gets the
configured default admin account (user `admin`, with the default password set in `server.py`'s start-up code; not
reproduced here). Only the admin can add users (Settings → General → "Add a user").

### 5.4 Demo / stand-in mode (no AI keys)

To try the whole flow without keys, run a second server with its own database and folders and the stand-ins on, as the
browser checks do. In PowerShell, for example:

```
$d = "$env:TEMP\aadhi-demo"
foreach ($s in 'assets', 'exports', 'static', 'jobs') { New-Item -ItemType Directory -Force "$d\$s" | Out-Null }
$env:DATABASE_URL = "sqlite:///" + ("$d\demo.db" -replace '\\', '/')
$env:ASSETS_DIR = "$d\assets"; $env:EXPORTS_DIR = "$d\exports"; $env:STATIC_DIR = "$d\static"
$env:AI_FAKE_STATE_DIR = "$d\jobs"; $env:MANIM_SANDBOX_DIR = "$d\sandbox"; $env:JWT_SECRET = "<long random text>"
$env:AI_FAKE_PROVIDER = "1"; $env:FAKE_TTS = "1"; $env:AI_GENERATION_ENABLED = "1"
./.venv/Scripts/python.exe -m uvicorn server:app --host 127.0.0.1 --port 9950
```

The stand-in writer builds lessons only from the source text, the stand-in providers make simple ffmpeg pictures and
clips, and the voice is a tone as long as the text. Keep these settings out of `.env` and away from the real database: a
server without stand-ins fails, rather than continues, a stand-in run it finds.

### 5.5 What needs keys

| Feature | Without a key |
|---|---|
| Lesson writing (Studio), classic one-step generation | Need `GEMINI_API_KEY` or `OPENAI_API_KEY`; otherwise the Studio says no lesson writer is set up (documents and lesson files stay available) |
| Document Assistant | The rule-based structural analysis always works; the AI analysis needs a text model key |
| AI images | Pollinations' free endpoint now asks for payment (HTTP 402); Gemini images need `GEMINI_IMAGE_ENABLED=1` and `GEMINI_API_KEY` |
| AI videos | Veo needs `GEMINI_API_KEY`; LTX needs a local GPU (shown as not available); the manual workflow ("By hand") always works |
| AI presenter clips | No real presenter provider exists in the code; Aadhi and the drawn Aadhi Teacher need none |
| Voices | Edge-TTS (the default server voice) needs network access, no key; Gemini, OpenAI and ElevenLabs voices need keys |
| AI-assisted composition, direction, alignment, terminology; Manim auto-heal | Need a text model key (Gemini for auto-heal); the deterministic modes need none |
| GIF search | Needs `GIPHY_API_KEY` |

The library, built-in visuals, sandboxed Manim, presenter planning, composition, synchronization, styles, editor,
quality, Visual Review, preview and export work without any key.

## 6. Testing

No test plays sound and no test calls a real AI provider. Every suite below passed on the final Phase 21 code.

- **Backend:** `./.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"` (`unittest` with FastAPI's
  `TestClient`). Every module imports `tests/backend_env.py` first: a throwaway database and storage folders (the real
  `projects.db`, `exports/` and `assets/` are never used), a test `JWT_SECRET`, `AI_GENERATION_ENABLED=0`,
  `AI_RECOVERY_ENABLED=0` (recovery tests run the manager themselves) and a temporary sandbox folder. Providers are
  mocked with the network blocked; `tests/test_manim_sandbox.py` uses the real sandbox and is skipped without one.
- **Frontend:** `node --test "tests/*.test.js"` — Node's built-in runner over the UMD modules, with fake DOMs from
  `tests/helpers/`; some tests read `index.html` and the stylesheets to check wiring and design rules. No npm packages.
- **Browser checks:** `node tests/<name>.mjs` for `tests/*_browser_check.mjs` and `tests/export_e2e.mjs`. They drive
  headless Chrome (`channel: 'chrome'`) through `playwright-core` (`npm install --no-save playwright-core`, or
  `PLAYWRIGHT_CORE_DIR`); most also need ffmpeg / ffprobe, the `.venv` and `AADHI_PASSWORD` set to the default admin's
  password (`AADHI_USER` defaults to `admin`).
  - The checks from Phase 5 on start **their own throwaway server** (never port 9942) with a fresh SQLite database and
    asset / export / static folders under their output folder, a test `JWT_SECRET`, `AI_FAKE_PROVIDER=1` (and from
    Phase 12 on `FAKE_TTS=1`); most take `<NAME>_CHECK_PORT` and `<NAME>_CHECK_OUT`. The Phase 21 check runs two.
  - The Phase 1–4 checks (`mascot_browser_check.mjs`, `export_e2e.mjs`, `assets_browser_check.mjs`,
    `visuals_browser_check.mjs`) use the **running development server** at `http://127.0.0.1:9942` (or a URL given as
    the first argument); `E2E_USE_ASSETS=1` runs the export check with library media.
  - Phase 21 ran the timing-sensitive checks (presenter, cinematic, recording performance) alone; under parallel load
    some checks timed out and passed when re-run on their own.
- **Silent browsers:** Chrome runs with `--mute-audio --disable-audio-output` and `speechSynthesis` stubbed on every
  page; the export's recording browser cannot be muted (a muted tab records silence), so it keeps
  `--disable-audio-output` and asks the capture for `suppressLocalAudioPlayback`. `FAKE_TTS` servers speak a tone.
- **No real provider calls:** throwaway servers register no real media provider, and their writer and text-model modes
  use the stand-in; backend tests switch generation off and mock providers.

### Results after Phase 21

| Suite | File | Server | Result |
|---|---|---|---|
| Backend (unittest) | `tests/test_*.py` (30 modules) | none | **887/887** |
| Frontend (node --test) | `tests/*.test.js` (28 files) | none | **490/490** |
| Phase 1 mascot | `mascot_browser_check.mjs` | development (9942) | 27/27 |
| Phase 2 export | `export_e2e.mjs` | development (9942) | 30/30 |
| Export with library assets | `export_e2e.mjs` with `E2E_USE_ASSETS=1` | development (9942) | 35/35 |
| Phase 3 assets | `assets_browser_check.mjs` | development (9942) | 27/27 |
| Phase 4 visual router | `visuals_browser_check.mjs` | development (9942) | 24/24 |
| Phase 5 AI cache | `ai_cache_browser_check.mjs` | own | 11/11 |
| Phase 6 Visual Review | `visual_review_browser_check.mjs` | own | 17/17 |
| Phase 7 recording performance (720p) | `export_performance_browser_check.mjs` | own | 18/18 (28.5 fps effective, 0 stalls, page 127.8 fps) |
| Phase 8 providers | `ai_providers_browser_check.mjs` | own | 15/15 |
| Phase 9 recovery | `ai_recovery_browser_check.mjs` | own | 15/15 |
| Phase 10 Manim sandbox | `manim_sandbox_browser_check.mjs` | own (real sandbox) | 18/18 |
| Phase 11 source documents | `source_documents_browser_check.mjs` | own | 18/18 |
| Phase 12 presenter | `presenter_browser_check.mjs` | own | 22/22 |
| Phase 13 cinematic | `cinematic_browser_check.mjs` | own | 25/25 |
| Phase 14 composer | `composer_browser_check.mjs` | own | 17/17 |
| Phase 15 direction | `direction_browser_check.mjs` | own | 23/23 |
| Phase 16 synchronization | `sync_browser_check.mjs` | own | 17/17 |
| Phase 17 styles | `style_browser_check.mjs` | own | 21/21 |
| Phase 18 quality | `quality_browser_check.mjs` | own | 22/22 |
| Phase 19 editor | `editor_browser_check.mjs` | own | 52/52 |
| Phase 20 Studio | `studio_browser_check.mjs` | own | 69/69 |
| Phase 21 product | `phase21_browser_check.mjs` | own (two servers) | **81/81** |
| Static checks | — | — | Inline scripts and all JS files parse; browser checks parse; Python modules compile |

---

# Phase-by-phase documentation

## Phase 1 — Mascot Reliability

**Status:** Complete with limitations · **Goal:** Aadhi, the mascot, could freeze, disappear or ignore the narration
during a lesson. Phase 1 set out to make sure Aadhi is never unintentionally static and that he follows the narration
and the scene playback.

### What was built
- `MascotController` in a new `mascot.js`: one object that owns every mascot `<video>` layer.
- Six behaviour states: `idle`, `talking`, `explaining`, `thinking`, `question`, `success`.
- Placement handling: the scene's `aadhi_position` selects the clip. `resolvePlacement()` keeps the old layout rules
  and repairs `"popup"` and capitalised values.
- Clip care: per-lesson preloading, load retries, crossfades, a watchdog, stall detection and restart on `ended`.
- An animated fallback built on new poster frames (`video_template/posters/*.jpg`, frame 0 of each clip).
- Narration and quiz events that drive the behaviour state.
- An optional scene field `mascot_state`, added to the screenplay generation prompt.
- `PlaybackGate`: one "Enable playback" button when the browser blocks media.
- A recording mode and a debug HUD (`?mascotDebug=1`).
- Integration in `index.html`; a `/mascot.js` route in `server.py`.

### How it works
- **Starting point.** Five full-screen position clips in `video_template/` (left, right, center, popup, empty studio)
  were switched by CSS class. `play()` failures were only logged. Nothing handled `error`, `stalled`, `waiting` or
  `ended`; only metadata was preloaded; there was no fallback. Scenes with `"popup"` or capitalised positions hid
  Aadhi silently. `[SYNC]` and `[PAUSE]` did not affect the mascot. Blocked narration audio was retried every
  100 ms, and blocked browser speech hung. The project has no per-state (talking / idle) clips.
- **Placements.** Valid values are `left`, `right`, `center`, `popup_bottom_left`, `popup_bottom_right` and
  `hidden`. Values are trimmed and lower-cased; `popup` maps to `popup_bottom_left` and `none` to `hidden`. Both
  popup placements use the popup clip; `hidden` uses the empty-studio clip.
- **Old scenes.** A scene without `aadhi_position` keeps the old rules: title and content scenes show Aadhi on the
  left unless their HTML is long or `force_background` says otherwise. `ai_video` scenes keep Aadhi on the left.
- **States.** A change to the current state is a no-op: nothing reloads or restarts. The fallback is a separate
  display mode, not a state. A state switches clips only when the placement has a clip marked with `data-state`.
- **Timing rules.** Lesson preloading waits at most 8 s. Failed loads retry at 1 s and 3 s. A crossfade starts only
  once the incoming clip has a frame. A 1 s watchdog keeps exactly one clip playing. A clip that ends restarts. A clip
  with no progress for 3.5 s counts as stalled.
- **Fallback.** When a clip cannot play, the controller shows that clip's poster frame with a CSS breathing and sway
  motion and a talking indicator. The last resort is the empty-studio still.
- **Narration mapping.** Segment start → `talking`; a `[SYNC]` reveal → `explaining`; `[PAUSE]` → `thinking`;
  hold or end → `idle`. Quiz checkpoints → `question`, then `thinking` during the countdown, then `success`.
- **Blocked playback.** `PlaybackGate` collects every blocked play attempt. One click on its button retries them all
  from inside the user gesture, as browsers require.
- **Cue bubble.** Its position beside Aadhi's head is a hand-measured fraction of each clip's frame. The popup
  framing has no bubble, because the board covers his face there.
- **Loading.** `mascot.js` runs as a classic script (`window.AadhiMascot`) and as a CommonJS module for Node tests.
- **Later changes.** Phase 7 added playback measurements (`resetMetrics()`, `getMetrics()`). Phase 12 made the clip
  mascot one presenter option next to the illustrated Aadhi Teacher (`presenter.js`).

### Key files
- `mascot.js` — `MascotController`, `PlaybackGate`, `resolvePlacement()`, states, placements and timing.
- `video_template/aadhi_left.mp4`, `aadhi_right.mp4`, `aadhi_center.mp4`, `aadhi_popup.mp4`, `no_aadhi.mp4` — the
  five shipped position clips.
- `video_template/posters/*.jpg` — the five poster frames used by the fallback (new).
- `index.html` — creates the controller, wires narration and quiz events, mascot styles and the debug HUD.
- `server.py` — serves `/mascot.js`.
- `tests/mascot.test.js`, `tests/helpers/fake-dom.js` — unit tests on a fake DOM.
- `tests/mascot_browser_check.mjs`, `tests/fixtures/mascot_lesson.json` — real-browser check and its lesson.
- `README.md` — updated.

### API and data
- Route: `GET /mascot.js`.
- Scene fields: `aadhi_position` (existing, now repaired); `mascot_state` (new, optional, backward compatible).
- Page markup read by the controller: `data-asset` and optional `data-state` on clip layers;
  `data-fallback-src` and `data-mascot-cues` on the container.
- URL flag: `?mascotDebug=1`.
- No database tables or environment variables.

### Tests and results (at the phase's close)
- Unit tests (Node `node:test`, fake DOM): 24/24.
- Real-browser check (Chrome via playwright-core): 27/27. It covers every state and placement, a broken clip falling
  back to the animated poster, the built-in demo lesson (no `aadhi_position`) and the Enable-playback click.
- Fixed during the phase: the playback-gate button rendered off-screen (a later `.btn-gold` rule overrode
  `position: fixed`); the cue bubble floated unattached in the popup framing (removed there); a misleading fallback
  reason for failed clips.
- The check still passed 27/27 in later regressions (for example the Phase 19 baseline).
- Record note: Phases 1 and 2 had no written record at the time. Their sections were written in Phase 3 from the
  code, the tests and the end-of-phase reports.

### Limitations and follow-ups
- No per-state clips exist, so talking and explaining look alike while a clip plays. The controller switches to state
  clips if they are added with `data-state`.
- No blinking for the photoreal stills. (Phase 12's illustrated Aadhi Teacher blinks; the clip mascot is unchanged.)
- A real autoplay block cannot be triggered under automation, so the check simulates it.
- Safari and Firefox were not tested.
- White frames appear in headless screenshots during View Transitions (pre-existing, also on the original page).
- Cue positions are hand-measured from the current clips.
- Clip stalls during recording were later traced to 60 fps tab capture starving the clip decoder. The fallback had
  covered them; Phase 7 moved recording to 30 fps, which removed the stalls in its benchmark.

## Phase 2 — Reliable Video Recording / Export

**Status:** Complete with limitations · **Goal:** The app could record a lesson, but it kept the whole video in
memory, stored nothing on the server and lost narration spoken by the browser. Phase 2 set out to turn a lesson into a
complete video that is saved on the server and downloadable later, with visible progress, retries and understandable
errors.

### What was built
- A `video_exports` table (`models.VideoExport`) and an `exports.py` router under `/api/exports`.
- Export jobs with status transitions enforced on the server.
- A resumable chunked upload, streamed to disk.
- Server-side validation (magic bytes and ffprobe), a lossless remux and silence detection.
- Storage at `exports/<user>/<id>.<ext>`, listing and detail routes, and an authenticated download with byte ranges.
- 30-minute signed links for preview and download.
- A conservative cleanup. In the current code it runs at most hourly, never touches completed exports, marks jobs
  that stopped reporting as failed and removes partial or orphaned upload files.
- `export.js`: an API client (`ExportApi`), `LessonRecorder`, `ExportFlow` and an export panel with progress, preview,
  download, retry and history.
- `index.html`: media preparation before recording, server Edge-TTS narration while exporting, cached simulation
  videos, a fix for the History reload loop, and "My Videos" buttons.
- `server.py`: `/save-history` returns the project id; scoped (link) tokens are rejected as logins.

### How it works
- **Starting point.** Two recorders (● manual, ⚡ "Ultimate Export") used `getDisplayMedia` and `MediaRecorder`
  (WebM VP9, 15 Mbit/s). The whole video was held in memory as one chunk and downloaded through an object URL that was
  revoked at once. Nothing was uploaded or stored. "Default (Edge-TTS)" narration actually used the browser's
  `speechSynthesis`, which tab capture cannot record. Simulations and AI videos were generated while recording.
  Opening a project from History reloaded the page forever.
- **Flow.** `ExportFlow` runs prepare → capture → record → upload → save.
- **Prepare.** Media is prepared before recording, so nothing is generated during it. Simulation videos come from the
  cache instead of being rendered during the recording.
- **Narration.** While exporting, narration plays through the server's Edge-TTS audio, which tab capture records.
- **Record.** The user shares the tab. `LessonRecorder` validates the shared stream, records with a 1 s timeslice,
  builds the file only in `onstop`, and keeps a 1.5 s tail after the last scene.
- **Upload.** The file goes up in 8 MB `PUT` chunks at byte offsets (`?offset=`). `GET …/upload` reports the bytes
  received, so an interrupted upload resumes where it stopped.
- **Status rules.** The browser may move a job QUEUED → PREPARING → RECORDING → UPLOADING, or to FAILED or CANCELLED.
  Only `/complete` sets PROCESSING and COMPLETED. A retry is a new row; failed and completed attempts are kept.
- **Validation.** `/complete` checks the container by its magic bytes and ffprobe. It remuxes losslessly to fix the
  duration and seek index. An audio track never louder than −60 dB counts as no sound (`has_audio` false).
- **Access.** Only the owner can download, with a login token or a 30-minute signed link for that export. Link tokens
  carry a scope and are never accepted as logins. The storage key is never sent to clients.
- **Later changes.** Phase 3 moved storage, media inspection and signed links into shared `storage.py`, `media.py`
  and `access.py`. Phase 7 changed recording to 30 fps at 8 / 12 Mbit/s, started the recorder only after playback is
  confirmed, and added an MP4 copy, WebVTT subtitles, chapters and library registration of finished videos.

### Key files
- `exports.py` — export router: jobs, status rules, upload, validation, storage, links, download and cleanup.
  (Phase 7 added outputs; Phase 9 added recovery.)
- `export.js` — `ExportApi`, `LessonRecorder`, `ExportPanel`, `ExportFlow`.
- `models.py` — `VideoExport`.
- `index.html` — export hooks (prepare, play, stop), Edge-TTS narration during export, History fix, buttons.
- `server.py` — router wiring, `/save-history`, rejection of scoped tokens in `get_current_user`.
- `.env.example`, `.gitignore`, `README.md` — updated.
- `tests/test_exports.py`, `tests/export.test.js`, `tests/export_e2e.mjs`, `tests/fixtures/export_lesson.json` —
  backend, unit and end-to-end tests and the test lesson.

### API and data
- `POST /api/exports` — create a job (QUEUED).
- `PATCH /api/exports/{id}` — browser-side progress: PREPARING, RECORDING, UPLOADING, FAILED, CANCELLED.
- `GET /api/exports/{id}/upload` — bytes received so far (to resume).
- `PUT /api/exports/{id}/upload?offset=` — append one chunk.
- `POST /api/exports/{id}/complete` — validate and store: PROCESSING, then COMPLETED.
- `GET /api/exports[?project_id=]` — the user's exports, newest first. `GET /api/exports/{id}` — one export.
- `POST /api/exports/{id}/link` — short-lived signed URLs for preview and download.
- `GET /api/exports/{id}/download` — the file, owner only, with byte ranges.
- The `/outputs/…` routes came later (Phase 7).
- Table `video_exports`: `id` (uuid4 hex), `project_id`, `user_id`, `title`, `source` (lesson | manual), `status`,
  `stage`, `progress`, `format`, `mime_type`, `file_name`, `storage_key` (never sent to clients), `file_size`,
  `duration_seconds`, `width`, `height`, `has_audio`, `error_message`, `retry_of_id`, timestamps.
- `/save-history` now returns the project id.
- Settings in `.env.example`: `JWT_SECRET` (signs logins and links; without it a new key is made at each start),
  `EXPORTS_DIR` (default `./exports`), `EXPORT_MAX_BYTES` (default 8 GB).

### Tests and results (at the phase's close)
- Backend: 16/16.
- Frontend unit: 43/43 (24 Phase 1 + 19 export).
- Phase 1 browser check: 27/27.
- End-to-end: 30/30. It logs in, records the whole lesson in headless Chrome, uploads, persists, refreshes, previews
  and seeks, downloads, cancels a share, checks second-account isolation, and inspects the downloaded file with
  ffprobe / ffmpeg (VP9 + Opus, audible narration, every scene present).
- Fixed during the phase: Playwright's default `--mute-audio` made the test recording silent (test harness); the
  "My Videos" button was hidden under the page title; the export panel sat under the control bar and subtitles.

### Limitations and follow-ups
- The end-to-end test ran in headless Chrome with an auto-accepted share dialog.
- Chrome and Edge only.
- Recording is real-time: about 1.4 GB for 12 minutes at 15 Mbit/s. (Phase 7 lowered this to 8 / 12 Mbit/s at
  30 fps.)
- The recording is held in memory until it is uploaded. (Still true in Phase 9: a refresh during upload loses it.)
- Signed links are bearer links for 30 minutes.
- Storage is local disk only; Railway and Hugging Face disks are wiped on restart without a volume.
- `JWT_SECRET` should be set.
- The end-to-end check "Aadhi never still for 1 s" failed from the end of Phase 3 to Phase 5. Phase 6 found the
  cause: exports started from a saved lesson's start card recorded the lesson under that card's blur. The fix made
  it pass again (resolved in Phase 6).
- The History modal itself was later removed; the Studio lists lessons (changed in Phase 21).

## Phase 3 — Asset Library

**Status:** Complete with limitations · **Goal:** Lesson media were disposable URLs owned by one lesson or export,
and nothing recorded their owner, type, size, hash or use. Phase 3 gives each image, sound or video one
registration with a stable ID and metadata. Lessons and the video exporter refer to it by that ID, and the app knows
where it is used.

### What was built
- New tables `assets` (`models.Asset`) and `asset_references` (`models.AssetReference`).
- `assets.py`: `AssetLibrary` (register, adopt, adopt_url, attach, resolve, references, delete), the `/api/assets`
  router, registration of 13 shipped system files at startup, and the `display_name()` sanitiser.
- Shared building blocks: `storage.py` (`LocalStorage`), `media.py` (file type from bytes plus ffprobe) and
  `access.py` (signed links). `exports.py` was moved onto them with unchanged behaviour, so there is one storage
  engine.
- Thin wrappers in `server.py` that add `asset_id` to the answers of `/render`, `/generate-ai-video`,
  `/generate-audio`, `/upload-media` and `/upload-image`.
- Reference recording in `/save-history`, and optional asset-ID fields in lessons.
- A security fix: `/upload-media` built its path from the client's file name, so on Windows a name such as
  `/../../x` resolved outside `static_videos/` (path traversal).
- `assets.js`: `AssetApi`, the lesson resolver (`collectAssetIds`, `applyResolved`, `resolveLesson`) and
  `AssetLibraryPanel`, opened by 🗂 **Assets** on the start screen. (Phase 21: the top bar's Library.)
- The phase record itself, `all-phases-details.md`.

### How it works
- **Audit findings.** Generated media landed in `static_videos/`, named by a hash of the request: Manim
  `<Scene>_<hash>.mp4`, AI videos `ai_video_<hash>.mp4`, narration `audio_<hash>.mp3|wav`, uploads
  `<uuid8>_<client name>`, DOCX images `img_<uuid>.png`. Side-panel images came from `/get-image` (Pollinations,
  cached in `images/`, no login). `static_videos/`, `images/` and `video_template/` were public mounts. Scenes referred
  to media only by URL. Narration was never stored in a lesson. Nothing recorded ownership, type, size, duration,
  hash or use.
- **Asset record.** Each asset has a random uuid4-hex ID, a `scope_key` (`system` or `user:<id>`), an `owner_id`
  (NULL for system assets), a `kind` (video, audio, image), a `source` (mascot, narration, manim, ai-video, upload,
  system) and a `status`. It stores a display `file_name`, a sniffed `mime_type`, a `storage_volume` with a
  server-generated `storage_key` (never sent to clients), size, SHA-256, duration, width, height and `has_audio`.
  `details` (JSON) holds source facts: voice / engine / text for narration, scene for Manim, prompt for AI video,
  placement for mascot clips. (Later phases added the sources `ai-image` and `export`.)
- **Storage.** `LocalStorage` is the only code that turns keys into paths. It rejects absolute paths, `..`,
  backslashes, drive letters and empty segments. Volumes: `assets` (`ASSETS_DIR`, uploads stored as
  `blobs/<sha[:2]>/<sha>.<ext>`), `static` (`static_videos/`) and `system` (`video_template/`). No object storage.
- **Registration.** Upload → size limit → type from the file's own bytes and ffprobe → streamed SHA-256 →
  deduplicate → store → row. Generator output and shipped system files are adopted in place, without a copy.
  Unchanged files are not read again. Registration in the generator wrappers can never break a generator.
- **Deduplication.** By content hash, never by name. The same content for the same owner is the same asset. For
  another owner it is a separate row pointing at the one stored file. Files are shared across volumes too: an upload
  identical to a generated file reuses that file.
- **Access control.** Private assets are visible to their owner only; others get 404, as for unknown IDs. System
  assets are readable by every signed-in user and cannot be deleted. File content is served only by
  `GET /api/assets/{id}/content`, with a login token or a 12-hour signed link for that one asset. Link tokens are
  not logins. Responses use `nosniff`, inline disposition with a sanitised name, and byte ranges.
- **Lifecycle.** `pending` (reserved for generators that finish later), `ready`, `failed` (file missing or invalid;
  set automatically when a file disappears) and `deleted` (soft). Registering the same content again repairs a failed
  asset or revives a deleted one with the same ID.
- **References.** One row per asset, saved lesson and place, such as `scenes[3].video_asset_id`, written on every
  save. Only your own or shared assets are recorded, so another user cannot pin your asset. The scan covers asset-ID
  fields, `asset:` markers and plain `/static/...` URLs of older lessons, which are adopted for the owner.
- **Safe deletion.** Delete is refused (409) while any saved lesson uses the asset. Otherwise it is a soft delete. The
  physical file is removed only if it lives in the managed `assets` volume and no other asset uses it. Files in
  `static_videos/` and `video_template/` are never deleted by the library.
- **Lesson resolver.** The page collects asset IDs, resolves them in one call and writes short-lived URLs into the
  fields the renderer already reads. A `data-asset-src` marker keeps the ID, so a lesson can be re-resolved when
  links expire. Lessons resolve before the first scene (alongside the mascot preload) and at the start of export
  preparation, which reports missing assets before recording. Every place in `index.html` that stores a generated
  or uploaded media URL also stores the returned `asset_id`.

### Key files
- `assets.py` — `AssetLibrary`, the assets router, system-asset registration, `display_name()`.
- `storage.py` — `LocalStorage` key-to-path mapping and `sha256_file` (streamed hash).
- `media.py` — media inspection (magic bytes, ffprobe, remux, audio peak), shared with exports.
- `access.py` — signed link tokens and media request authentication, shared with exports.
- `assets.js` — `AssetApi`, lesson resolver, `AssetLibraryPanel`.
- `models.py` — `Asset`, `AssetReference`.
- `server.py` — library setup, generator wrappers, reference recording in `/save-history`, `/assets.js` route.
- `exports.py` — moved onto the shared modules (behaviour unchanged).
- `index.html` — resolving lessons, storing `asset_id`s, the Assets button.
- `tests/backend_env.py`, `tests/test_assets.py`, `tests/assets.test.js`, `tests/assets_browser_check.mjs` — shared
  backend test setup and the asset tests.

### API and data
| Endpoint | Purpose |
|---|---|
| `POST /api/assets` | Upload a file (multipart); validated, deduplicated; returns the asset and `deduplicated` |
| `GET /api/assets` | Your assets and shared system assets; filters `kind`, `source`, `scope` (`mine`/`system`), `q` (name), `status`; paginated |
| `GET /api/assets/{id}` | One asset with its reference count and your saved lessons that use it |
| `DELETE /api/assets/{id}` | Delete your unused asset (403 system, 409 in use, 404 not yours) |
| `POST /api/assets/resolve` | Asset IDs → 12-hour content URLs; unusable IDs listed in `missing` |
| `GET /api/assets/{id}/content` | The file (login token or signed link); 410 if the file is missing |

- `PATCH` was not added in this phase (added in Phase 4).
- Tables: `assets` (unique on scope, volume and key) and `asset_references` (`asset_id`, `project_id`, `field`).
- Lesson fields (all optional, backward compatible): `video_asset_id`, `manim_asset_id`,
  `side_panel.video_asset_id`, `uploaded_image_assets[imgId]`, and `<img src="asset:ID">` in a scene's `html`.
- Generator answers gain `asset_id`. New route `GET /assets.js`.
- Settings in `.env.example`: `ASSETS_DIR` (default `./assets`), `ASSET_MAX_BYTES` (default 2 GB).
  `.gitignore` adds `/assets/`.

### Tests and results (at the phase's close)
- Backend (`unittest` + FastAPI TestClient, one throwaway database and storage): 39/39 (16 export + 23 asset). The
  asset tests cover metadata, invalid files (text, MIME-spoofed HTML, SVG with script, empty, truncated MP4), dedup
  by content, cross-user isolation, signed links, missing files (410 → failed → repaired), safe deletion, reference
  scanning including legacy URLs, `/upload-media` traversal, and 300 assets listed and resolved without reading files.
- Frontend unit: 50/50 (24 mascot + 19 export + 7 asset). Phase 1 check: 27/27. Asset Library browser check: 19/19.
- Phase 2 export end-to-end: 29/30. Export end-to-end with library assets (`E2E_USE_ASSETS=1`): 34/35. Both failed
  only "Aadhi never still for 1 s" (1.56 s and 2.04 s). Every mandatory flow item passed. Earlier in the phase,
  before the silent test setup, the same code passed 30/30 (twice) and 35/35.
- Restart with the library takes about 2 s and registers no duplicates. Resolving 300 assets is one query. Existing
  data stayed intact after `create_all` added the new tables.
- Fixed during the phase: the traversal bug; uploads identical to generated files stored twice; `resolve` committing
  once per asset (3.2 s for 300; batched); the resolver re-matching its own marker; export preparation preloading the
  `asset:` marker (greedy regex); several panel bugs.
- Since this phase all browser tests run silently: `--disable-audio-output`, stubbed browser speech, and
  `suppressLocalAudioPlayback` for the export capture. A probe confirmed the recording still gets the sound.

### Limitations and follow-ups
- The recorded-mascot smoothness check failed in the final runs. It was blamed on CPU load at the time; Phase 6 found
  the real cause, the start card's blur recorded over the lesson (resolved in Phase 6).
- Under the same load, recordings ran longer than in Phase 2; nothing was cut, because each scene waits for its video.
- Storage is still local disk; production persistence is not solved.
- The public `/static`, `/images` and `/video_template` mounts remain public for older lessons. Signed content links
  are bearer links for 12 hours.
- `/get-image` results are not registered, because that route has no login. (Phase 5 added the authenticated
  `/generate-ai-image`, whose images are library assets; `/get-image` stays for older pages.)
- No garbage collection of unused library files. Projects can never be deleted, so references accumulate.
- The start screen did not fit on phones (pre-existing). (Phase 12 made it scroll inside the window; Phase 21
  redesigned it with narrow-screen layouts.)
- Pre-existing: no favicon (404); "Logo video play error AbortError" logged when the intro is skipped.
- No per-scene asset picker in the lesson editor, and library search matched names only. (Phase 6 Visual Review
  added "Choose from Asset Library" and extended search to descriptions, keywords and generated-media prompts.)
- Deferred: library matching and side-image sourcing to Phase 4 (done there); generation caching to Phase 5 (done
  there); 60 fps recording cost to Phase 7 (changed to 30 fps there); providers to Phase 8; cleanup of orphaned files
  and recovery of missing files to Phase 9; Manim to Phase 10 (sandboxed there).

## Phase 4 — Visual Router

**Status:** Complete with limitations · **Goal:** Each scene called a provider directly, so every preview or export
of an unfinished AI scene could hit a paid service, and nothing looked in the Asset Library first. Phase 4 adds one
central place that decides where each visual comes from, with AI generation as the last resort ("Gemini is a visual
provider, not the visual engine"). Planning is separate from generating, AI can be switched off, and preview and
export show the same visuals.

### What was built
- `visuals.py`: visual requests, the `VisualSource` and `MediaType` enums, a fixed rule chain, the `VisualRouter`,
  one-query candidate loading, reference recording and `POST /api/visuals/plan`.
- `visuals.js`: the page client. It holds the "AI Visuals" setting, plan options, `shouldAutoGenerate`, plan storage
  in scenes, source labels, debug rows and the API client.
- Page integration in `index.html`: lessons are planned at presentation start and export preparation, and scenes
  render from their plans.
- `PATCH /api/assets/{id}` for an asset's description and keywords.
- A server-wide AI kill switch (`AI_GENERATION_ENABLED=0`) on both AI generators.
- An optional screenplay `visual` intent, added to the generation prompt.
- Follow-up: description and keyword editing in the Asset Library panel.

### How it works
- **Audit findings.** AI videos were generated on every preview of a scene without `video_url`. Side images were
  preloaded from Pollinations for every image panel at lesson start. Export preparation generated every missing AI
  video and image. AI videos were already registered with their prompt (Phase 3) but never reused.
- **Requests.** `requests_from_scenes()` makes one `VisualRequest` per slot: the main visual, the side panel, each
  `asset:ID` or `<img>`, and each uploaded image.
- **Rule chain.** The first rule that answers wins:
  1. `ExplicitAssetRule` — an asset the lesson names. Usable → ASSET or SYSTEM_ASSET; unusable → NONE with an error,
     never a substitute.
  2. `ExistingMediaRule` — media the scene already has: a library content URL, a `/static` file checked on disk, or
     an external URL (UPLOADED_ASSET).
  3. `PreviousMatchRule` — a library asset matched earlier stays chosen while usable (preview == export).
  4. `BuiltInRendererRule` — the scene carries data for chart, graph, 3d_model, terminal, animation, skill_tree, quiz,
     p5 or svg → PROCEDURAL; gif → EXTERNAL_MEDIA.
  5. `ManimRule` — the scene has Manim code → MANIM (existing `/render` pipeline).
  6. `NoVisualRule` — quiz, text or "none" intent; an equation intent with TeX → PROCEDURAL MathJax.
  7. `LibraryMatchRule` — a deterministic match against your and the shared assets.
  8. `AiGenerationRule` — AI_VIDEO or AI_IMAGE as a plan only. With AI off: NONE with `would_require`.
- **Plan.** A `VisualPlan` holds slot, source, media, selection, asset ID, URL, renderer, provider,
  `requires_generation`, `would_require`, score, reason, error and debug data.
- **Order decision.** Media a scene already has comes before any library match, so existing lessons never change what
  they show. Built-in renderers apply only when the scene carries their data, so they cannot hide a library asset.
- **Library matching.** Words from the request (concept ×2, keywords ×2, description ×1) are matched against the
  asset's metadata words (keywords and concepts 1.0, description or prompt 0.85, scene / role 0.6, placement 0.5,
  Aadhi words on shared mascot pictures 0.6, file name 0.35). Score = weighted coverage, plus 0.15 when the concept
  appears as a phrase, capped at 1. Stopwords, camelCase, simple stemming and negation ("no_aadhi", "without people")
  are handled. A file name alone can never reach the threshold (`VISUAL_MATCH_THRESHOLD`, 0.6). Images match only
  image slots, videos only video slots. Ties: own before shared, then larger, then newer.
- **Cost.** One database query per plan loads the candidates. Files are stat'ed only for chosen assets and a scene's
  own `/static` media; no directory scans and no decoding.
- **Side panels** get library images or videos, or a generated image, never an AI video. `preferred_media_type`
  decides image or video when the intent names neither.
- **Controls.** Per request: `allow_ai_generation`, `prefer_existing_assets`, `prefer_procedural`,
  `preferred_media_type`. Server-wide: `AI_GENERATION_ENABLED=0` makes `/generate-ai-video` and uncached
  `/get-image` answer 403, and plans say what would have been needed.
- **"AI Visuals" setting** (per browser): *Auto: images* (default; AI videos only when the user clicks "Generate AI
  video"), *Auto: images + videos*, or *Off*. Planning never generates in any mode.
- **Screenplay intent.** The optional `visual` field (`concept`, `description`, `keywords`, `type`: image, video,
  diagram, chart, graph, equation, none) lets Gemini describe the visual while the router decides. Scenes without it
  route as before.
- **Storage and references.** The page stores main and side plans in `scene.visual_plan`, saved with the lesson. With
  a `project_id`, chosen library assets are recorded as used at `scenes[i].visual_plan.<slot>`: one row per slot,
  replaced when the choice changes. Saving the lesson rescans `visual_plan` too.
- **Preview vs export.** The preview renders from the plan and generates only what the setting allows. The export
  re-plans (earlier choices kept), prepares only what the setting allows and warns about the rest.
- **Page states.** A library clip plays; an unusable explicit asset shows a "Visual unavailable" card with a replace
  or re-render button; a planned AI video not yet made shows "This scene needs an AI video — Generate". Each scene
  shows a source badge; `?visualDebug=1` prints each decision with `console.table`.
- **Later changes.** Phase 5 added a cache step after routing. Phase 6 added `ReviewDecisionRule` as rule 0. Phase 8
  made the AI media layer name the provider (the router names none). Phase 15 added per-scene route preferences.

### Key files
- `visuals.py` — requests, enums, rules, router, candidate loading, reference recording, plan endpoint.
- `visuals.js` — AI Visuals setting, plan options, `shouldAutoGenerate`, plan storage, labels, debug rows, API client.
- `index.html` — planning at presentation start and export preparation, rendering from plans, badges, debug output.
- `assets.py` — `PATCH /api/assets/{id}`, `visual_plan` references, metadata validation (follow-up).
- `assets.js` — metadata editor in the Asset Library panel (follow-up).
- `server.py` — router wiring, the AI kill switch on both generators, `/visuals.js` route.
- `tests/test_visuals.py`, `tests/visuals.test.js`, `tests/visuals_browser_check.mjs` — router tests.
- `tests/backend_env.py` — AI off for all backend tests.

### API and data
- `POST /api/visuals/plan` — plans every visual of a lesson and never generates. Body: `scenes`, the four controls,
  optional `project_id` (must be yours, else 404) and `debug`. Returns `plans`, `summary`, `ai_generation_enabled`
  and `match_threshold`. 413 over 400 scenes; 422 for a bad `preferred_media_type`; 401 without login.
- `PATCH /api/assets/{id}` — `description` (≤ 1000 chars) and `keywords` (≤ 30, each ≤ 60 chars). Whitespace is
  tidied and repeated keywords dropped case-insensitively. An empty field clears it; an omitted field is unchanged;
  other details are kept. 422 over a limit, 403 shared asset, 404 not yours or deleted.
- `POST /generate-ai-video` and `GET /get-image` — 403 when `AI_GENERATION_ENABLED=0` (cached images still served).
- Route `GET /visuals.js`.
- No schema change. Plans live in the lesson JSON (`scene.visual_plan`); references use `asset_references`;
  descriptions and keywords live in `assets.details`.
- Settings: `AI_GENERATION_ENABLED` (default on), `VISUAL_MATCH_THRESHOLD` (default 0.6). Page setting stored in
  `localStorage` (`aadhi_ai_visuals`).

### Tests and results (at the phase's close)
- `tests/test_visuals.py` (27): the 24 required cases plus reference recording, side panels, negation and asset
  descriptions. Cases include explicit selection, private and shared access, matches by concept and keyword (not file
  name), AI fallback, AI disabled, legacy URLs, deleted / failed / missing assets, planning never calling a generator,
  and 600 assets × 60 scenes in under 3 s and 15 queries.
- `tests/visuals.test.js` (7): setting, plan options, auto-generation matrix, stored plans, labels, debug rows, errors.
- `tests/visuals_browser_check.mjs` (24, silent Chrome): a clip matched by its description, a Chart.js panel, an AI
  video generated on click with one mocked request, badges, debug trace, and an export showing the same visuals as the
  preview (checked by pixel colour in the downloaded WebM).
- Final run: backend 66/66; frontend unit 57/57; Phase 1 27/27; Phase 4 browser 24/24; Phase 3 browser 19/19.
- Phase 2 export 29/30 and library-asset export 34/35; both failed only the mascot smoothness check (1.73 s, 2.68 s).
  Every mandatory flow item passed.
- No AI provider was called: backend tests ran with AI off and generators patched to fail; browser tests intercepted
  `/generate-ai-video` and `/get-image`.
- Follow-up run: backend 72/72; frontend unit 61/61; Phase 1 27/27; Phase 3 26/26; Phase 4 24/24; exports again
  29/30 and 34/35 on the same check. A test describes an uploaded clip and the router then selects it (relevance 0.67
  in the backend test, 0.84 in the browser check).
- Fixed during the phase: side images preloaded before planning; stale `video_url` / `manim_video_url` overriding the
  plan; the shared "no Aadhi" still matching "Aadhi the mascot"; skipped view transitions raising a page error; the
  setup card growing taller than a 720 px screen.

### Limitations and follow-ups
- Matching is word-based: an upload is found only through words in its metadata. (The follow-up added the editor.)
- Side panels never get AI videos. AI images still came from Pollinations and the provider was not configurable.
  (Phase 8 added a provider registry; Pollinations' free endpoint now answers 402.)
- The AI Visuals setting is per browser, not per account or lesson. (Phase 21 moved it into Settings → Visuals & AI;
  it is still stored per browser.)
- Replacing an unusable explicit asset is a button, never automatic.
- Plans carry 12-hour signed links, refreshed at every presentation start and export.
- The export smoothness check failed under load. (Resolved in Phase 6: the start card's blur was the cause.)
- Deferred: a persistent generation cache to Phase 5 (done there); a per-scene picker and override UI to Phase 6 (done
  there as Visual Review); providers to Phase 8; Manim only through the unchanged `/render` to Phase 10.

## Phase 5 — AI Media Cache, Deduplication & Reuse

**Status:** Implemented; its export blocker was resolved in Phase 6 (all suites pass) · **Goal:** AI media was
"cached" only by a file named after the prompt, shared by all users and providers and unusable with AI off. Phase 5
makes the rule "generate once, reuse many times": an AI image or video is never generated again when an equivalent,
valid generated asset already exists for the user. The cache reuses Phase 3 library assets and sits after the Phase 4
router's decision that AI is needed.

### What was built
- `ai_cache.py`: canonical identity, hashing, `PROVIDER_SETTINGS`, lookups, cache entries, the lock and counters.
- New tables `ai_generations` and `ai_generation_locks`.
- A cache flow in `/generate-ai-video` and a new authenticated `/generate-ai-image`, both with `force_regenerate`.
- A cache step in the visual router: AI plans that are already generated become `selection: "cached"`.
- `GET /api/ai-cache/stats` with counters.
- Page integration: `AiMediaApi`, reuse / generation status labels, "(reused)" badges and "↻ New AI version".
- A test-only stand-in provider (`AI_FAKE_PROVIDER=1`).

### How it works
- **Audit findings.** `/generate-ai-video` (Veo, LTX or manual) wrote
  `static_videos/ai_video_<sha256(prompt)[:16]>.mp4`. The "cache" was that file, keyed by prompt only and shared by
  all users and providers. The kill switch ran first, so nothing cached worked with AI off, and a regeneration would
  overwrite a file lessons used. `/get-image` cached Pollinations images by prompt only, unregistered, and cached a
  fallback image as if it were the requested one.
- **Identity.** `{v, media_type, provider, model, prompt, parameters}`, with only the settings the code really sends:
  Veo aspect ratio and person generation; LTX negative prompt, width, height, frames, fps, steps, guidance, seed;
  Pollinations width, height, nologo; manual: none. The random Pollinations seed is provenance, not identity. `v`
  versions the scheme. `PROVIDER_SETTINGS` feeds both the identity and the provider calls, so they cannot drift.
- **Canonical form.** Text is Unicode NFC with unified line endings, collapsed whitespace and trimmed ends. Case and
  punctuation are kept, because they can change the output. JSON uses sorted keys, no insignificant whitespace, UTF-8
  kept and list order kept.
- **Two hashes.** The generation hash is the SHA-256 of that JSON. It is separate from the file's content hash
  (`assets.sha256`). Two different requests whose files happen to be identical are two entries on one asset.
- **Endpoint flow.** identity → generation hash → lookup (own entries, then shared `system`). On a hit, return the
  existing asset with no provider call, even with `AI_GENERATION_ENABLED=0`. On a miss: kill switch → lock
  (scope, hash) → recheck → provider → register asset → record entry → release.
- **Lookup query.** `ai_generations` joined to `assets`, filtered by hash and by scope `user:<id>` or `system`,
  newest first. It is one indexed query per request or per lesson plan. A hit must be `ready`, the right kind, usable
  by the user, with a readable file and matching provider and model. Anything else is a miss; a missing file marks
  the asset failed. Lookups never change lessons.
- **Scope (Option A).** Entries are private to the user who generated them. `system` entries are shared, but nothing
  creates them automatically yet. Two independent guards stop cross-user reuse: the scope filter in the query and the
  library's access check on every hit.
- **Force regenerate.** `force_regenerate: true` skips the lookup (the kill switch still applies). It writes a new
  file, asset and entry (`forced`). The old asset, its file and lesson references stay as they were. Later identical
  requests reuse the newest version. Not available in the manual workflow.
- **Concurrency.** Identical requests take a row in `ai_generation_locks` (primary key scope + hash). The first
  generates; the others poll every 0.25 s, recheck the cache and reuse its asset. It works across threads, event loops
  and processes that share the database. A lock older than `AI_CACHE_LOCK_MINUTES` (30) counts as abandoned. Forced
  regenerations are not serialized.
- **Files.** Each generated video gets a new file `ai_video_<hash16>_<rand8>.mp4` in `static_videos/`, so no file a
  lesson uses is overwritten. AI images go into the library's content-addressed store (`assets/blobs`), served by
  signed links, not publicly. The manual workflow uses one file name per user and request
  (`ai_video_<sha256(user|hash)[:16]>.mp4`).
- **Provenance.** Asset details gain `generation` `{hash, provider, model, media_type, parameters[, seed]}` next to
  `prompt`. No secrets are stored.
- **Router step.** Matching is untouched. After routing, AI plans (and "AI needed but off" plans) are checked against
  the cache in one batch. Hits become `selection: "cached"`, `cache_hit: true`, with the asset and a signed link.
- **Preview and export** plan and generate through the same identity, so both orders end on one asset: one file, one
  entry, many lesson references.
- **Stand-in provider.** With `AI_FAKE_PROVIDER=1`, both providers become a local ffmpeg stand-in (`fake`) with its
  own identities, and the real providers refuse to run.
- **Later changes.** Phase 8 moved the generators behind the AI media layer (`ai_media.py`), with one identity per
  candidate provider. Phase 9 added `record_once`, `adopt_lock` and `sweep_locks` for recovery.

### Key files
- `ai_cache.py` — identity, canonicalization, hashing, `PROVIDER_SETTINGS`, `AICache` (lookups, entries, lock,
  counters).
- `models.py` — `AIGeneration`, `AIGenerationLock`.
- `server.py` — cache flow in `/generate-ai-video`, new `/generate-ai-image`, `/api/ai-cache/stats`, stand-in switch.
- `visuals.py` — `reuse_cached_generations`, the router's cache step.
- `visuals.js` — `AiMediaApi`, `generationStatus`, "(reused)" badges, `cache_hit` kept in stored plans.
- `index.html` — AI video scenes and export preparation through `AiMediaApi`; side images from `/generate-ai-image`;
  status label; "↻ New AI version".
- `assets.py`, `assets.js` — AI image assets and the "AI image" source label.
- `tests/test_ai_cache.py`, `tests/ai_cache_browser_check.mjs` — cache tests.

### API and data
- `POST /generate-ai-video` — optional `force_regenerate`. Answers add `cache_hit` and `generated`, and keep
  `status`, `video_url`, `asset_id` and `cached` (= `cache_hit`). 400 for an empty prompt. Manual answers carry the
  per-user file name.
- `POST /generate-ai-image` (new, login) — `{prompt, subject_name?, force_regenerate?}` →
  `{status, url, image_url, asset_id, cache_hit, generated[, fallback]}`. 403 on a miss with AI off.
- `GET /api/ai-cache/stats` (new, login) — counters since start: lookups, hits, misses, generated, bypassed, waited,
  plan_hits and provider calls. No prompts.
- `POST /api/visuals/plan` — plans may be `selection: "cached"` with `cache_hit: true`.
- `GET /get-image` — unchanged, kept for older pages; the current page no longer uses it.
- Table `ai_generations`: `generation_hash`, `scope_key`, `asset_id`, `media_type`, `provider`, `model`, `identity`
  (JSON), `forced`, `created_at`; index on (`scope_key`, `generation_hash`).
- Table `ai_generation_locks`: `key` (`<scope_key>:<generation_hash>`), `created_at`.
- Both tables are created by `create_all` at startup; existing tables are unchanged, with no migration.
- Settings: `AI_CACHE_LOCK_MINUTES` (default 30); `AI_FAKE_PROVIDER=1` (tests only).

### Tests and results (at the phase's close)
- `tests/test_ai_cache.py` (19): the 24 required cases plus canonicalization, AI images as library assets, the
  fallback image not cached as the request, the manual workflow, validation and counters, an abandoned lock, and one
  indexed query for 40 lookups among 500 foreign entries. Cases include miss then hit, every identity field,
  force regenerate, private and shared entries, the kill switch, three concurrent requests → one generation, and both
  preview / export orders. Providers are local fakes; any network call fails the test. Mutation-checked.
- `tests/visuals.test.js` (+4): cache-hit and generated responses, status text, labels, force-regenerate bodies.
- `tests/ai_cache_browser_check.mjs` (11, silent Chrome, its own server with stand-ins): lesson A generates once;
  lesson B with the same visuals gets the same asset IDs with no provider call; "New AI version" makes exactly one new
  asset and keeps the old one playable.
- Full regression: backend 91/91; frontend unit 65/65; Phase 1 27/27; Phase 3 26/26; Phase 4 24/24; Phase 5 11/11.
- Phase 2 export 29/30 and library-asset export 34/35, failing only the mascot smoothness check (2.85 s, 2.63 s).
- No AI provider was called during testing.
- Final status: "Implemented — not claimed complete", because the Phase 2 export suite was not fully passing.

### Limitations and follow-ups
- The cache is private per user: two users asking for the same visual each generate once. Shared reuse exists only
  through `system` entries, which nothing creates automatically.
- AI videos generated before Phase 5 have no identity, so they are not cache hits. The router still reuses them
  through the library match on their prompt.
- The manual workflow's file name is now per user and request.
- The lock is database-backed with polling. A waiting request keeps its HTTP request open while the first generates;
  if that generation fails, the waiting request tries itself. (Phase 8 moved video generation to background runs.)
- Counters are per process and reset on restart.
- After a forced regeneration, later identical requests reuse the newest version.
- `/get-image` stays outside the cache. (Still so in Phase 8.)
- The export smoothness failure was traced at the time to 60 fps VP9 tab capture. Phase 6 found the real cause, the
  start card's blur, and both export suites passed (resolved in Phase 6). Phase 7 later moved recording to 30 fps.
- Deferred: a review UI for cached media to Phase 6 (done there as Visual Review); recording cost to Phase 7; new
  providers into `PROVIDER_SETTINGS` to Phase 8 (done there); cleanup to Phase 9. Unused cached assets are identifiable
  but not cleaned up; Phase 9 sweeps abandoned lock rows.

## Phase 6 — Visual Review, Approval & Scene-Level Visual Control

**Status:** Complete · **Goal:** Before Phase 6 the user could not see, approve or remove the visual picked for a
scene. The only scene editing was the JSON script editor and a background picker. Phase 6 lets the user see every
scene's visual before export, understand why it was chosen, and keep, replace, remove or regenerate it. It builds on
the Phase 4 router (first choice) and the Phase 5 AI cache (reuse) and replaces neither.

### What was built
- A Visual Review panel (`review.js`) that reuses the Asset Library frame.
- A summary line, e.g. "12 visuals · ✓ 8 approved · ● 3 need review · ✎ 1 changed · — 1 without visual".
- Filters: Needs review, Approved, Changed, No visual, AI, Asset Library, Built-in, Manim.
- A scene list with status chips (icon + words), and a detail view per scene: number, title, purpose, preview,
  source in words, status, "Why" (the router's reason in plain words) and notes.
- Previews: image, muted video with controls, a Chart.js mini chart, or a described placeholder ("Not generated
  yet", "No visual for this scene", "Visual unavailable").
- Actions: [Keep], [Change ▾: Choose from Asset Library · Generate (new) AI version · Back to automatic choice],
  [Remove / Restore automatic choice].
- Navigation: [‹ Previous] [Next ›] [Next needing review] [Show in lesson].
- Entry points: 🖼 in the control bar, and "Review the visuals of this lesson" on a saved lesson's start card.
- A new endpoint, `POST /api/visuals/review`, and a new first router rule, `ReviewDecisionRule`.
- An Asset Library *pick mode*, and library search over descriptions, keywords and generated media prompts.
- Review status in the scene list badges (e.g. "🗂 Asset Library · ✎ Changed").
- `?visualDebug=1` adds the source, reason, asset ID and cache status.

### How it works
- **States per slot** (main and side): *pending* (no entry; the router's choice, not an error), *approved* (kept;
  stores the asset when the plan had one), *changed* (replaced by a chosen asset), *removed* (no visual).
- **Storage:** `scene.visual_review.<slot> = {status, asset_id?, source?, fingerprint, reviewed_at}` in the lesson
  JSON. Plans carry `review_status` and, when the scene changed after the decision, `review_stale`. New plan
  selections are `reviewed`, `approved` and `removed`.
- **`ReviewDecisionRule`** runs before the explicit-asset rule, so preview, reload and export agree:
  - removed → no visual, never re-added;
  - changed → the chosen asset; if unusable, "Visual unavailable", never a substitute;
  - approved with an asset → that asset while usable, else routed again as pending;
  - approved without an asset → routing reproduces the same choice deterministically.
- **Fingerprint:** a hash of what the scene asks the slot to show. Main: type, prompt, Manim / SVG / p5 code, visual
  intent. Side: title, side panel without produced media fields, visual intent. A generated video or a resolved
  link does not change it; editing the prompt, chart data or picture request does.
- **After an edit:** an approval goes back to pending and is flagged. An explicit *changed* or *removed* choice is
  kept and flagged ("the scene changed after this choice"). A user's choice is never dropped silently. A regenerated
  lesson is a new set of scenes, so everything starts pending.
- **Persistence:** the endpoint writes the decision, the scene's recomputed plans and, optionally, the page's copy of
  the scene into the saved lesson in place (no new history snapshot). It then re-records asset references. Opening
  the review saves an unsaved lesson first. The page mirrors each decision in `slides` and its local backup.
- **Access:** only the lesson's owner can review. A chosen asset must be the user's own or shared (shared Aadhi
  assets allowed). The router applies `usable_asset` again at every plan, so an edited lesson cannot show another
  user's asset.
- **AI cache:** "Generate AI video/image" goes through the normal generator (cache first) and keeps the result as
  *approved*. "Generate new AI version" sends Phase 5 `force_regenerate`, creates a new asset (the old one stays in
  the library and other lessons) and marks the scene *changed*. Opening or navigating the review never generates.
- **Export:** preparation replans the lesson. Removed visuals are neither generated nor rendered; chosen and approved
  assets are used; pending visuals behave as before. Approving every scene is not required.
- **AI off:** cached and existing assets, library choices and built-in / Manim visuals still work. Generate actions
  are disabled with an explanation; the server's 403 is shown if reached.
- **Accessibility:** keyboard-reachable buttons, visible focus, labels, alt text, `aria-live` status, status never by
  colour alone, stacked layout on phones.
- **Unsaved changes:** leaving a scene while its decision is saving asks "Discard this change?". The Asset Library's
  description editor now asks the same.
- Later changes: Phases 12, 13 and 15 added `presenter`, `composition` and `direction` entries to the same
  `scene.visual_review` map. Phase 19 made the review routes refuse (409) a scene whose id differs from the saved
  one. Phase 20 routed review writes through `editor_api.update_lesson` (compare-and-set on the lesson revision; the
  saved scene is the base). Phase 21 replaced the provider line with a plain "Origin" line (provider only with
  `?visualDebug`) and renamed filters and labels in plain words (e.g. "Animation" instead of "Manim").

### Key files
- `review.js` — the Visual Review panel module, served at `/review.js`.
- `visuals.py` — `ReviewDecisionRule`, fingerprints, staleness, `POST /api/visuals/review`.
- `visuals.js` — page client; plan fields and labels.
- `assets.py`, `assets.js` — search over the `details` JSON; pick mode; unsaved-description question.
- `index.html` — entry points, the `slides` mirror, the start-card and full-script editor fixes.
- `server.py` — the `/review.js` route.
- `tests/test_visual_review.py`, `tests/review.test.js`, `tests/visual_review_browser_check.mjs` — Phase 6 tests.

### API and data
| Endpoint | Change |
|---|---|
| `POST /api/visuals/review` (new, login) | `{project_id, scene_index, slot: main\|side, action: keep\|choose\|remove\|reset, asset_id?, scene?, allow_ai_generation?}` → `{review, plans}`; 404 unknown lesson / scene / slot / asset (incl. other users'), 409 unusable asset, 422 bad slot / action / kind / missing `asset_id`; never generates |
| `POST /api/visuals/plan` | plans carry `review_status`, `review_stale`; selections `reviewed`, `approved`, `removed` |
| `GET /api/assets?q=` | also matches description, keywords and generated media prompts (no new index) |
| `GET /review.js` | the page module |

- No database change. Decisions live in the lesson JSON; references use `asset_references`
  (`scenes[i].visual_review.<slot>`).
- Asset choice errors: another user's asset 404; deleted, missing or failed 409; wrong kind 422 (a main visual must
  be a video).

### Tests and results (at the phase's close)
- `tests/test_visual_review.py` (19): the 20 required cases plus edits, the page's scene copy, validation and library
  search. Mutation-checked (rule order, staleness, access check).
- `tests/review.test.js` (12) and `tests/assets.test.js` (+1): statuses, previews, actions, failed save, new AI
  version, AI off, navigation, filters, unsaved edits.
- `tests/visual_review_browser_check.mjs` (17 checks, silent, real Chrome, stand-in providers): seven scenarios
  including Keep, Change, Remove, AI cache, new AI version and export, plus the phone layout.
- Static checks pass. Backend 110/110 (91 + 19). Frontend 78/78 (65 + 13).
- Browser checks: Phase 1 27/27, Phase 3 26/26, Phase 4 24/24, Phase 5 11/11, Phase 6 17/17.
- Phase 2 export end-to-end 30/30; export with library assets 35/35.
- The Phase 2 mascot smoothness metric, failing since Phase 3, passes again after the start-card fix.
- No AI provider was called during testing.

### Limitations and follow-ups
- Fixed in the phase: exports started from a saved lesson's start card recorded the lesson under that card (blurred,
  "▶ Play Video" on top). The export's play step now hides the card; no recording setting changed.
- Fixed in the phase: the full-script editor assigned to the `const slides` array and could never apply; it now
  replaces the contents in place. A chosen AI asset is no longer labelled "Asset Library".
- "Change" does not offer switching a slot to another built-in renderer type.
- Built-in visuals other than charts are described, not drawn; "Show in lesson" shows them.
- Removing a side visual shows the lesson's concept map.
- Keeping a planned, not yet generated AI visual approves the plan; it is generated later as before.
- Board images inside a scene's HTML are not reviewed; only the main and side visuals are.
- Reviewing needs a saved lesson.
- Visual Review notes never displayed (a DOM `append` bug); found and fixed in Phase 8.
- Deferred: recording performance (done in Phase 7); more providers for "new AI version" (done in Phase 8).
  Unused review-chosen or regenerated assets are identifiable but not cleaned up; no later phase record reports
  such a cleanup.

## Phase 7 — Recording Performance, Export Pipeline & Video Output

**Status:** Complete · **Goal:** Aadhi's clips stalled while a lesson was recorded, and nothing measured a
recording. Phase 7 measured the Phase 2 pipeline, fixed what the data showed, and chose frame rate, resolution and
bitrate from measurements. It made the start and end of a recording exact, and added an MP4 copy (when a real encoder
exists), WebVTT subtitles from the lesson's narration, and chapters from the scene timing.

### What was built
- Measurement instrumentation:
  - `mascot.js`: `resetMetrics()` / `getMetrics()` (clip switches, fallbacks, watchdog stalls, `waiting` /
    `stalled` events, reloads, play failures, the last 300 events with buffer state).
  - `export.js`: `LessonRecorder.metrics` (track settings, MIME type, bitrates, chunks, bytes, start / stop latency)
    and a frame monitor over the page; `ExportFlow.lastMetrics` combines recorder, page and mascot figures.
  - `tests/export_performance_browser_check.mjs`: a whole export plus ffprobe facts and Chrome CPU time.
- A recording mode that switches the glass blur off only while recording.
- Start / stop synchronization: the recorder starts only after playback is confirmed.
- New capture settings: 30 fps, 8 / 12 Mbit/s, captures above 1920×1080 scaled down.
- Codec capability detection with a clear "unsupported browser" message.
- An MP4 copy made in the background with the server's ffmpeg (libx264 + AAC).
- WebVTT subtitles and chapters built on the server from the page's own log.
- A new `export_outputs` table and output endpoints.
- Finished videos registered as Asset Library assets (source `export`, "Lesson video").
- Honest progress steps and an MP4-only retry.

### How it works
- **Stall diagnosis:** normal playback had no stalls. During recording, stalls happened with the clip fully loaded
  (1.9–4.6 s decoded ahead): the decoder was starved of time. Headless Chrome here is GPU-accelerated (checked in
  `chrome://gpu`), so these are the machine's real limits.
- **60 fps** was not deliverable (~40 uneven fps) and caused every stall (3–4 per lesson). **At 1080p** the page was
  the bottleneck: 30 fps without recording, 85 fps with the glass blur (`backdrop-filter`) disabled.
- **Recording mode:** `NORMAL` → `EXPORTING` (`body[data-mode="exporting"]`) → `RECORDING` (`body[data-recording]`,
  also for manual ● recordings). Under `body[data-recording]` the blur is `none`. The panels are 82–90% opaque, so
  the look is kept (board SSIM 0.96). The blur returns in a `finally` hook, also after errors and cancellation.
- **Start / stop sequence:** prepare (Visual Review decisions applied, media preloaded, missing media reported) →
  choose the tab → hide controls and panel → start playback → wait for the intro logo video's `playing` event (3 s
  limit) → start the recorder → time scenes, captions and mascot data from the recorder's `start` event → last scene
  → 1.5 s tail → stop → build the file → upload → validate and save.
- **Frame rate decision:** 30 fps. In the benchmark it gave 0 stalls and 28.6–29.0 delivered fps; 24 fps was equally
  stable but less smooth.
- **Codec order:** `video/webm;codecs=vp9,opus` → `vp8,opus` → `h264,opus` → `video/webm` → `video/mp4` (only if the
  browser records MP4 itself). Never an MP4 label on WebM data. Opus at the browser's default bitrate; 1 s timeslice.
- **Resolution:** the tab's own size is kept. Captures above 1920×1080 are scaled down with `applyConstraints` after
  capture starts. Asking `getDisplayMedia` for a maximum size made Chrome upscale a 720p tab, so that was removed.
- **Bitrate:** 8 Mbit/s up to 720p, 12 Mbit/s above. Text at 8 and 15 Mbit/s looked identical. One lesson went from
  167 MB to 64 MB (720p).
- **Audio:** all page sound reaches the recording once through the captured tab audio. Mascot clips are muted, so
  there is no duplicate track or feedback loop.
- **Chunks and progress:** data is flushed every second and released once the file is built. An empty recording is
  reported ("came out empty"). Steps: Preparing lesson (asset *n of m*), Choose tab, Recording (scene *n of m*),
  Finalizing, Uploading (real bytes), Checking and saving the video (no percentage). No step invents a percentage.
- **MP4 copy:** made only if the server's ffmpeg offers `libx264` and `aac`; otherwise the output is `unavailable`
  with the reason. It runs after the export is saved (`make_mp4`), one at a time (semaphore), with an argument list
  (no shell, no user text). Settings: `-preset veryfast -crf 22 -pix_fmt yuv420p -threads 2`, AAC 160k,
  `+faststart`, subtitles as `mov_text`, chapters from an ffmetadata file. Timeout `max(300 s, 6 × duration)`.
  Output goes to a `.part.mp4` file, is validated with ffprobe (H.264, AAC, duration within 2 s / 5 % of the WebM),
  then renamed. A failure marks only the `mp4` output `failed`; the WebM stays first-class.
- **Subtitles:** while recording, the page logs every caption line of `#subtitle-track` with start and end on the
  recording clock and sends it with `/complete`. No speech-to-text. `[SYNC]` / `[PAUSE]` markers and HTML tags are
  removed (`x < y` is kept and escaped). Cues are sorted, never overlap, end inside the video, and use at most two
  lines of 42 characters. A line without an end gets its reading time (≤ 7 s).
- **Chapters:** one per scene title from the logged scene starts, "Introduction" at 00:00 unless a scene starts in the
  first second, strictly increasing, nothing past the end. Offered as a readable list (`00:00 Introduction`, …) and
  embedded in the MP4. The page's old "YouTube chapters" helper was removed.
- **Library:** the WebM and the MP4 become Phase 3 assets, excluded from the router's candidates (like narration).
- **Experiment knobs:** `localStorage` `aadhi_export_fps` (10–60), `aadhi_export_max_height`, `aadhi_export_bitrate`;
  test env `EXPORT_TEST_FPS`, `EXPORT_TEST_BITRATE`, `EXPORT_TEST_VIEWPORT`, `EXPORT_TEST_MAX_HEIGHT`.

### Key files
- `export.js` — recorder (`LessonRecorder`), flow (`ExportFlow`), panel, API client, metrics.
- `index.html` — export hooks, recording mode, caption and scene log.
- `exports.py` — export API, outputs, background MP4 (`make_mp4`).
- `export_outputs.py` — subtitles (`build_vtt`), chapters (`build_chapters`), encoder detection, `convert_to_mp4`.
- `mascot.js` — playback measurements.
- `models.py` — `ExportOutput` (`export_outputs` table).
- `assets.py`, `assets.js`, `visuals.py` — export videos as assets, excluded from scene visuals.
- `tests/test_export_outputs.py`, `tests/export_performance_browser_check.mjs` — new tests.

### API and data
- `GET /api/exports/{id}/outputs/{kind}` (new): `mp4`, `vtt` or `chapters`; signed link or owner, same access rules
  as the video.
- `POST /api/exports/{id}/outputs/mp4` (new): make or retry the MP4 copy without touching the WebM.
- `POST /api/exports/{id}/link`: now also returns `output_urls`.
- `/complete`: accepts the page's timeline (caption lines and scene starts); older clients without one still work.
- New table `export_outputs`: export, kind (`webm|mp4|vtt|chapters|timeline`), status, file, size, type, asset id,
  error. The code adds a unique `(export_id, kind)` constraint and statuses
  `pending|processing|ready|failed|unavailable`.
- The ready panel checks the MP4 every 3 s for up to 30 min and adds its button when ready.

### Tests and results (at the phase's close)
- `tests/test_export_outputs.py` (15): VTT timing, splitting and cleaning; special characters and maths; empty
  narration; chapters; a real conversion with embedded subtitles and chapters; a failed conversion leaving no files;
  API cases (signed links, other users refused, MP4 failure and retry, no encoder, older clients, assets).
- `tests/export.test.js` (+6) and `tests/mascot.test.js` (+1).
- `tests/export_performance_browser_check.mjs` (18 checks, silent, real Chrome, stand-in providers).
- Static checks pass. Backend 125/125 (110 + 15). Frontend 85/85 (78 + 7).
- Phase 1 27/27, Phase 3 26/26, Phase 4 24/24, Phase 5 11/11, Phase 6 17/17, Phase 2 export 30/30, export with
  library assets 35/35, Phase 7 18/18.

| Window | Checks | Stalls / fallbacks | Delivered fps | Aadhi longest still | File (≈69 s) | Outputs |
|---|---|---|---|---|---|---|
| 1280×720 | 18/18 | 0 / 0 | 28.3 | 0.00 s | 63.9 MB VP9/Opus | MP4 ready, 13 cues, 8 chapters |
| 1920×1080 | 18/18 | 0 / 0 | 27.9 | 0.00 s | 95.4 MB VP9/Opus | MP4 ready, 13 cues, 8 chapters |

- Against the phase start: stalls 3–4 → 0, fallbacks 2–3 → 0, 41 of 60 fps → 28 of 30, file 167 MB → 64 MB,
  upload and save 57 s → 26–46 s. No AI provider was called during testing.

### Limitations and follow-ups
- The MP4 copy needs ffmpeg with libx264 and AAC on the server; otherwise only the WebM, subtitles and chapters.
- Subtitles follow the captions the page showed; narration with captions hidden produces no cues.
- One MP4 conversion at a time. A restart during a conversion left it `processing` (resolved in Phase 9: such MP4
  copies are made again at startup).
- The glass blur is absent from recordings. (Phase 13 later removed the blur in composed scenes.)
- Tab capture needs the user's "Share this tab" choice and a Chromium-based browser for tab audio.
- Delivered fps depends on the machine (~27–29 of 30 here). Headless page fps above 60 means spare capacity.
- The opening chapter was always "Introduction", duplicating a scene of that name (Phase 20 known issue). Phase 21
  uses "Opening", "Lesson start" or "Opening N" when a scene chapter already has that name.
- Deferred to Phase 9: orphaned export files and old outputs are not cleaned up automatically. Phase 9 handled
  interrupted `/complete` and MP4 work; no later record reports cleanup of orphaned export files.

## Phase 8 — Multi-Provider AI Media System

**Status:** Complete (no real provider produced media here; Pollinations now paid) · **Goal:** Images used only
Pollinations and videos only whatever "Start AI Server" loaded, with no fallback, retries or capability model.
Pollinations' free endpoint now answers HTTP 402. Phase 8 built one provider-independent AI media layer that picks a
provider per request, checks capabilities, falls back in a controlled way, retries only what is worth retrying and
validates output, while the Phase 3 library, Phase 4 router, Phase 5 cache and Phase 6 review stay authoritative.

### What was built
- `ai_providers.py`: the provider abstraction, adapters, error model and `ProviderRegistry`.
- `ai_media.py`: the media layer: selection, cache, generation, retries, fallback, validation, registration, runs.
- Adapters: `pollinations` (image), `gemini-image` (image, opt-in), `veo` (video, job), `ltx` (local GPU video),
  `manual` (user places the file), and `fake` / `fake-alt` stand-ins (tests only).
- Background video generation with a new `ai_generation_runs` table and runs endpoints.
- Output validation with hard checks (reject) and soft checks (warnings).
- An "AI providers" panel under the AI Visuals setting.
- Visual Review shows provider, model, backup provider and quality warnings.
- Structured `[AI MEDIA]` log lines and provider counters in `/api/ai-cache/stats`.
- A fixed Veo download (`client.files.download(file=video)`); the old field did not exist in the SDK.

### How it works
- **Flow:** router ("an AI video / image is needed", names no provider) → media layer → registry (ordered candidates,
  each usable or not, with why) → cache lookup per candidate → generate (one per identity, per-provider concurrency
  limit, bounded retries, next provider) → validate → Asset Library → cache entry → run record.
- **Types:** `MediaRequest` (media type, prompt, optional `provider`, `allow_fallback`, `model`, `aspect_ratio`,
  `duration_seconds`, `force_regenerate`, purpose, project, scene, slot; no endpoint, key or provider parameter).
  `GenerationResult` (status `queued|running|completed|failed|cancelled|unsupported`, provider, model, path, job id,
  metadata, error, timing). `Capabilities` (media types, execution `sync|job|local|manual`, models, aspect ratios,
  durations, inputs, negative prompt, audio, prompt length, `paid`).
- **`AIProvider` interface:** `capabilities()`, `configuration()` (env only, no network call), `settings_for(request)`
  (what is sent = what the cache identity holds), `check(request)`, `generate(...)`. `SyncProvider` runs one blocking
  call in a worker thread under a timeout. `JobProvider` submits, polls (bounded, cancellable) and fetches; a job that
  ran out of time is never resubmitted.
- **Errors:** `ProviderError` categories `configuration`, `auth`, `unsupported`, `rejected`, `rate_limited` (with
  `retry_after`), `unavailable`, `timeout`, `invalid_output`, `cancelled`, classified in one place. Messages are
  sanitized (keys, bearer tokens, `key=` / `token=` / signature parameters, configured secret values).
- **Selection:** videos prefer the provider "Start AI Server" chose; images prefer `AI_IMAGE_PROVIDER`, else the first
  of `AI_IMAGE_PROVIDERS`. An explicit `provider` is never silently replaced unless `allow_fallback: true`. The
  manual workflow is never a fallback. No quality scores exist; order comes from configuration and the request.
- **Recent failures** (rate limit, unavailable, timeout) move a provider behind others for `retry_after` or
  `AI_PROVIDER_COOLDOWN` (60 s), but never exclude it. HTTP 402 counts as `auth`, with `AI_PROVIDER_AUTH_COOLDOWN`
  (600 s).
- **Retry and fallback:** `rate_limited` waits for `Retry-After` if ≤ 20 s, then the next provider. `unavailable` and
  answer timeouts get bounded retries (2 tries, backoff), then the next provider. A provider job that ran out of time
  is not retried. `auth` and `invalid_output` go to the next provider. `rejected` neither retries nor falls back.
- When every provider fails: one normalized error (429 with `Retry-After`, 422, 504, 502 or 503) naming each reason;
  no asset, cache entry or file left. The image subject-image fallback applies after provider failures only.
- **Cache:** identity scheme unchanged (`v` 1), so pre-Phase-8 entries are reused. A requested model, shape or
  duration changes the identity. A result is cached under the provider that really made it. A fallback's identity is
  checked only when that fallback would be tried; a healthy preferred provider is never replaced by a backup's older
  result. With AI off, any candidate's earlier result is served.
- **Validation:** hard checks: file exists, ≥ 256 bytes, within `AI_MAX_IMAGE_BYTES` / `AI_MAX_VIDEO_BYTES`, decodable
  (ffprobe), right kind, ≥ 32 px per side, videos ≥ 0.5 s. Soft checks (warnings): one flat colour, shape > 8 % off,
  duration > 50 % off, no audio from an audio provider.
- **Provenance:** `details.generation` adds `requested_provider`, `fallback_from`, `attempts`, `generation_ms`,
  `job_id`, `run_id`, `warnings`, `purpose` to the Phase 5 fields.
- **Background runs:** `POST /generate-ai-video` with `wait: false` answers at once when no provider is needed, else
  `202 {status, run_id}`. The page follows the run every 2 s. A run whose heartbeat stopped is reported
  `interrupted`, not resumed.
- **Security and cost:** keys from the environment only; provider output fetched over https from allow-listed public
  hosts (redirects checked), size-capped and time-bounded; paid providers opt-in; per-provider limits
  (`<NAME>_MAX_CONCURRENT`).
- **Adding a provider:** subclass `SyncProvider` or `JobProvider`, implement `capabilities()` and `configuration()`,
  map errors, add settings to `ai_cache.PROVIDER_SETTINGS`, register it in `ProviderRegistry.__init__`, add mock and
  media-layer tests, check `GET /api/ai-media/providers`.

### Key files
- `ai_providers.py` — `MediaRequest`, `GenerationResult`, `Capabilities`, `AIProvider`, `SyncProvider`, `JobProvider`,
  `ProviderError`, adapters, `ProviderRegistry`, safe download, sanitizing.
- `ai_media.py` — the media layer; `call_provider` is the single provider call (the test seam).
- `server.py` — generator endpoints through the layer; providers and runs endpoints.
- `visuals.py` — asks the layer which provider would make a planned visual; names no provider.
- `ai_cache.py` — settings of the new providers.
- `models.py` — `AIGenerationRun`.
- `visuals.js` — `AiMediaApi({background: true})`, `provenanceText`, `ProvidersApi`, `stateLabel`.
- `review.js`, `index.html` — provider line, quality note, providers panel.
- `.env.example` — every provider variable.

### API and data
- `POST /generate-ai-video`: optional `provider`, `allow_fallback`, `model`, `aspect_ratio`, `duration_seconds`,
  `wait`, `project_id`, `scene_index`, `slot`. Answers add `provider`, `model`, `requested_provider`,
  `fallback_from`, `run_id`, `generation_ms`, `warnings`. `202 {run_id}` with `wait: false`.
- `POST /generate-ai-image`: the same optional fields, no `wait`.
- `GET /api/ai-media/providers` (new): states, reasons, capabilities, preference, order, fallback switch; no secrets.
- `GET /api/ai-media/runs`, `GET /api/ai-media/runs/{id}`, `POST /api/ai-media/runs/{id}/cancel` (new).
- `POST /api/visuals/plan`, `/api/visuals/review`: plans may carry `provider`, `model`, `fallback_from`,
  `quality_warnings`.
- `GET /api/ai-cache/stats`: adds `provider_calls_by_provider` and `media` counters.
- New table `ai_generation_runs`: status, requested / actual provider, model, fallback, identity hash, job id,
  attempts, cache hit, asset, sanitized error and HTTP status, selection, heartbeat (no prompt).
- Env: `AI_IMAGE_PROVIDER`, `AI_IMAGE_PROVIDERS` (default `pollinations,gemini-image`), `AI_VIDEO_PROVIDERS` (default
  `veo,ltx`), `AI_PROVIDER_FALLBACK`, `AI_PROVIDER_COOLDOWN`, `AI_PROVIDER_AUTH_COOLDOWN`, `AI_PROVIDER_MAX_ATTEMPTS`,
  `AI_PROVIDER_MAX_RETRY_WAIT`, `AI_MAX_IMAGE_BYTES`, `AI_MAX_VIDEO_BYTES`, `GEMINI_IMAGE_ENABLED`, `GEMINI_API_KEY`,
  `VEO_ENABLED`, `VEO_MODEL`, `<NAME>_MAX_CONCURRENT`, `AI_MEDIA_LOG`; tests: `AI_FAKE_PROVIDER`.

### Tests and results (at the phase's close)
- `tests/test_ai_providers.py` (33, network blocked): error classification, `Retry-After`, secret masking, safe
  downloads, each adapter, the registry.
- `tests/test_ai_media.py` (23, through the API, stand-ins only): cache matrix, fallback matrix, runs, safety, router
  and review.
- Existing suites: only the mocking seam changed (now `server.ai_media.call_provider`); no assertion changed.
- `tests/visuals.test.js` (+5); `tests/ai_providers_browser_check.mjs` (12 checks, silent, real Chrome).
- Static checks pass. Backend 181/181 (125 + 33 + 23). Frontend 90/90 (85 + 5).
- Phase 1 27/27, Phase 3 26/26, Phase 4 24/24, Phase 5 11/11, Phase 6 17/17, Phase 8 12/12, Phase 2 export 30/30,
  export with library assets 35/35, Phase 7 18/18 (0 stalls, 28.6 fps).
- Live validation: one call through the Pollinations adapter returned HTTP 500; a raw request returned HTTP 402
  Payment Required (x402, 0.01 USDC per image). No image was produced. No other real provider was called.

### Limitations and follow-ups
- Fixed in the phase: router planned images "via pollinations" regardless; Visual Review notes rendered as
  "[object HTMLParagraphElement]" (Phase 6 bug); the first cooldown rule excluded the only provider; `token=` outside
  URLs was not masked; 402 was first treated as a refusal.
- **Pollinations requires payment.** Automatic AI images need `GEMINI_IMAGE_ENABLED=1` and a `GEMINI_API_KEY`;
  otherwise image requests fail cleanly. Still true per the status table.
- No real provider produced media. Veo and Gemini image are tested with mocked clients only. The configured Veo model
  (`veo-2.0-generate-001`) is no longer listed by Google; set `VEO_MODEL` after checking access.
- Background runs were reported interrupted after a restart, not resumed, and the runs table was not cleaned up
  (both resolved in Phase 9).
- Images are generated while the request waits; only videos use background runs.
- Recent-failure memory and concurrency limits are per process. Blank detection is a heuristic.
- `/get-image` (legacy, unauthenticated) still calls Pollinations directly, outside the layer.
- Phase 21 moved the providers panel to Settings → Visuals & AI and shows the provider line only with `?visualDebug`.

## Phase 9 — Recovery, Resumability & Fault-Tolerant Generation

**Status:** Complete (verified with stand-in providers) · **Goal:** Phase 8 runs did not store the request, so
nothing could resume, and a server restart lost work. Worse, a polling error could submit a second paid provider
job. Phase 9 makes generation durable: after a restart, crash, refresh, network loss or provider failure, Aadhi keeps
what was done and continues from the right point, with no duplicate paid generation and no lost result.

### What was built
- `ai_runs.py`: a run state machine with guarded transitions, leases and attempt history.
- `ai_recovery.py`: the recovery manager (at startup, then periodic).
- Durable run execution and recovery in `ai_media.py`; recoverable job providers in `ai_providers.py`.
- A new `ai_generation_attempts` table and new columns on `ai_generation_runs`.
- `database.ensure_schema`: an additive migration step (missing columns and named indexes).
- Lesson batches: `POST /api/ai-media/lessons/{project_id}/generate`, used by export preparation.
- Export recovery (`recover_exports`): unfinished `/complete` and MP4 work finished at startup.
- Page reattachment: "Still generating on the server…" after a refresh.
- The providers panel lists active and needing-attention runs with **Retry / Dismiss / Cancel**.
- Housekeeping of old finished runs.

### How it works
- **Run vs attempt:** a run is the logical request; each provider call is an attempt row, never overwritten.
  *Resume* = continue the same provider job; *retry* = a new attempt (only when safe); *regenerate* = a new run with
  `force_regenerate`.
- **Run states:** `queued`, `running`, `recovering`, `cancel_requested`, `completed`, `failed`, `cancelled`,
  `needs_attention`. Allowed transitions are listed in `ai_runs.TRANSITIONS`; any other is refused.
- **Attempt states:** `starting`, `submitted`, `downloading`, `registering`, `completed`, `failed`, `lost`,
  `ambiguous`, `cancelled`.
- **Submission safety:** `starting` is written before the provider call; the job id and handle are written right
  after acceptance, before polling. A crash in between is an unknown submission: paid provider → `needs_attention`
  ("No duplicate generation was started automatically"); free provider → tried again. No provider documents an
  idempotency key, so none is sent.
- **Identical requests** (same `request_hash`) attach to the active run: one generation.
- **Leases:** a worker owns a run through a lease (`AI_JOB_LEASE_SECONDS`, 60) renewed every
  `AI_JOB_HEARTBEAT_SECONDS` (15). Claiming is one conditional UPDATE. Every later write checks the owner; a worker
  that lost its lease stops (`LeaseLost`). A stopping server expires its own leases at once.
- **Recovery manager:** runs at startup and every `AI_RECOVERY_INTERVAL` (15 s), or at once when work is queued. It
  claims due runs atomically, `AI_JOB_CONCURRENCY` (2) at a time. Per run:
  - asset already registered → finalize;
  - output on disk → validate and register (idempotent) → finalize;
  - provider job saved → poll the same job, then fetch and register. Job lost: free → retry, paid → needs attention.
    Job failed → next provider. Temporary trouble → back off;
  - provider call with no saved answer: free → retry, paid → needs attention;
  - cancel requested → cancel the provider job where supported;
  - queued with no attempt → check Visual Review, then cache, then generate.
- **Limits:** a run interrupted `AI_RECOVERY_MAX_ATTEMPTS` (3) times becomes needs attention. Backoff grows 30 s,
  60 s, 120 s … up to 15 min, so there is no retry storm after a restart.
- **Provider recovery:** `JobProvider` gains `recoverable`, `job_handle`, `restore_job`, `resume` (polls, never
  submits), `cancel_supported` and `cancel_handle`; new category `lost`. Veo's handle is the operation name; a 404 is
  `lost`; cancel is declared unsupported. Single-call providers (Pollinations, Gemini image, LTX) cannot be reattached.
- **Finalization is idempotent:** validate (recovered output is never trusted), register (videos adopted in place;
  images kept in a private work folder until registered), save the asset on the attempt, `record_once` in the cache,
  complete the run. Repeating after a crash creates no second asset, cache entry or reference.
- **Cache and locks:** the cache is checked before any new attempt. A recovered run adopts its identity's generation
  lock (`adopt_lock`); abandoned locks are swept (`sweep_locks`).
- **Lessons:** each finished run updates the scene's stored plan (`store_scene_plans`). It fills only an empty slot
  and never acts for a forced New AI Version (a fix from the regression run). Later phases record the scene id with
  the run, so a result finds its scene by id (Phase 20).
- **Visual Review stays authoritative:** `still_wanted` stops generation for removed visuals, slots with another
  chosen visual (unless it is a requested New AI Version) and scenes whose request changed. Such runs end `cancelled`.
- **Browser:** the server is the source of truth. On load the page fetches the lesson's active runs and follows them;
  failed polls are tolerated; no invented percentages.
- **Exports:** `/complete` keeps its request in `tmp/<id>.complete.json`. At startup, exports left `PROCESSING` are
  finished from what exists (moved file or waiting upload), else failed with a clear message. MP4 copies left
  `pending` / `processing` are made again.
- **Cancellation:** queued → cancelled at once. Running → `cancel_requested`; the owner, another process via its
  heartbeat, or the manager cancels the provider job and records the answer (`cancelled`, `failed`, `unsupported`,
  `not_submitted`).
- **Cleanup:** completed, failed and cancelled runs older than `AI_RUN_RETENTION_DAYS` (90) are removed with their
  attempts. Active and needs-attention runs are kept.
- **Security:** runs, history, cancel and resolve are owner-only (404 otherwise). Logs never hold prompts or secrets.

### Key files
- `ai_runs.py` — state machine (`TRANSITIONS`), guarded transitions, leases, `heartbeat`, `LeaseLost`, attempts.
- `ai_recovery.py` — `RecoveryManager` (claiming, concurrency, backoff, executors by kind from Phase 10).
- `ai_media.py` — durable execution and recovery of AI media runs.
- `ai_providers.py` — recoverable jobs, the `lost` category, stand-in job store in `AI_FAKE_STATE_DIR`.
- `ai_cache.py` — `record_once`, `adopt_lock`, `sweep_locks`.
- `database.py` — `ensure_schema`.
- `models.py` — new run columns and `AIGenerationAttempt`.
- `visuals.py` — `store_scene_plans`, `still_wanted`.
- `exports.py` — `recover_exports`.
- `server.py`, `visuals.js`, `index.html` — endpoints, page reattachment, providers panel.

### API and data
- `POST /generate-ai-video`, `/generate-ai-image`: durable runs; identical active requests attach; `project_id`,
  `scene_index`, `slot` tie a generation to its scene.
- `GET /api/ai-media/runs`: filters `project_id`, `active`, `batch_id`.
- `GET /api/ai-media/runs/{id}`: adds `state_label`, `recovery_count`, `history`, `explanation`, `actions`, `prompt`,
  scene.
- `POST /api/ai-media/runs/{id}/cancel`: queued → cancelled at once; running → persisted cancel request.
- `POST /api/ai-media/runs/{id}/resolve` (new): `retry` (sets `allow_duplicate`) or `dismiss` a needs-attention run.
- `POST /api/ai-media/lessons/{project_id}/generate` (new): lesson batch.
- `GET /api/ai-media/providers`: adds `generations` (active, recovering, needing attention).
- `ai_generation_runs` gains `request`, `request_hash`, `project_id`, `scene_index`, `slot`, `batch_id`,
  `lease_owner`, `lease_expires_at`, `recovery_count`, `cancel_requested_at`, `not_before`, `last_checked_at`,
  `allow_duplicate`, plus four indexes.
- New table `ai_generation_attempts`: run, number, provider, model, hash, state, job id, job handle (no secrets),
  output path, asset, error category / message, times recovered, owner, detail, timestamps.
- Migration applied to this installation's `projects.db`: 13 columns added, all 74 lessons kept, a copy taken first.
- Env: `AI_RECOVERY_ENABLED` (1), `AI_RECOVERY_INTERVAL` (15), `AI_JOB_LEASE_SECONDS` (60),
  `AI_JOB_HEARTBEAT_SECONDS` (15), `AI_JOB_CONCURRENCY` (2), `AI_RECOVERY_MAX_ATTEMPTS` (3), `AI_RUN_RETENTION_DAYS`
  (90). Test only: `AI_TEST_CRASH_AT`, `AI_FAKE_STATE_DIR`, `<NAME>_JOB_SECONDS`, `<NAME>_PAID`.

### Tests and results (at the phase's close)
- `tests/test_ai_recovery.py` (23): crash points with a new service continuing from the database alone; exclusive
  leases; the schema step; resume of the same job; ambiguous paid / free submissions; lost and failed jobs; cache
  first; attach; two managers; cancel; backoff; lesson batch across a restart; Visual Review; housekeeping; exports.
- `tests/visuals.test.js` (+2). Backend tests set `AI_RECOVERY_ENABLED=0`; recovery tests drive the manager.
- `tests/ai_recovery_browser_check.mjs` (14 checks): real server processes killed (`taskkill /F`) or crashed
  (`os._exit`) and restarted; every provider job is a file, so duplicates are counted.
- Static checks pass. Backend 204/204 (181 + 23). Frontend 92/92 (90 + 2).
- Phase 1 27/27, Phase 3 26/26, Phase 4 24/24, Phase 5 11/11 (10/11 in the first pass, fixed), Phase 6 17/17,
  Phase 8 12/12, Phase 9 14/14, Phase 2 export 30/30, export with library assets 35/35, Phase 7 18/18.
- No AI provider was called during testing.

### Limitations and follow-ups
- Fixed in the phase: a polling error resubmitted a provider job; a server shutdown cancelled provider jobs; two
  wrong transitions; a provider-side job failure was deferred instead of falling back; a recovered image left its
  work file; the lesson update replaced visuals, including after a New AI Version.
- No real provider recovery was exercised. Veo recovery is tested only with mocked clients.
- A browser refresh during an upload loses the recording held in page memory (the server keeps the uploaded part).
- After a crash, recovery waits for the lease to expire (up to 60 s).
- A waiting image request whose server restarts gets a connection error; the next identical request hits the cache.
- Recent-failure memory and per-provider limits remain per process. Veo and single-call providers cannot be
  cancelled on the provider side.
- Deferred: Manim renders as durable runs (done in Phase 10). Later phases reuse the pattern: Phase 11
  (`document_analysis` runs), Phase 12 (presenter jobs), Phase 20 (`lesson_script` runs).
- Phase 21 adapted the recovery browser check (14 → 15) for the plain-words review view.

## Phase 10 — Secure Manim Sandbox

**Status:** Complete (Windows AppContainer runtime tested; Docker runtime implemented, untested) · **Goal:** Scenes
carry Manim code written by an AI or pasted by a user. Before Phase 10, `/render` ran it inside the server's
environment, with its secrets, network and files, no limits and a broken timeout. Phase 10 treats that code as
untrusted: it runs only in an isolated OS sandbox, as a durable Phase 9 job, and its video becomes a library asset.
Without a secure runtime it answers "Secure Manim execution is unavailable in this deployment." and never falls back.

### What was built
- Four new modules, none of the sandbox in `server.py`: security checks, the sandbox and its runtimes, the
  in-sandbox runner, and the Phase 9 job integration (see Key files).
- `sandbox/Dockerfile.manim`: the Docker image (manim 0.21.0, no pip, non-root); untested.
- A new `/render` replacing the unsafe one, `GET /api/manim/sandbox`, and a run kind `"manim"` with recovery dispatch.

### How it works
- **Threat model:** the code may try to read secrets, write outside its folder, reach the network, start processes or
  escape Python, exhaust resources, or outlive its job. Kernel and hypervisor escapes are out of scope.
- **Request path:** static checks (size, scenes, AST allowlist; 422 early) → secure runtime available? (else 503) →
  cache (same code + Manim version + profile → that asset) → persistent job → sandbox → output validation → Asset
  Library + cache entry → lesson plan → Visual Review → export (reuses the asset).
- **Windows AppContainer + Job Object** (tested):
  - profile `AadhiEduEngine.ManimSandbox` gives a low-integrity AppContainer token, so the user's files and the repo
    are denied even under the same account;
  - a per-job capability SID (`aadhiManimJob<id>`) is granted only that job's workspace;
  - the process starts suspended, joins a Job Object, then resumes. Limits: 1 active process, memory, CPU time, hard
    CPU rate cap, UI restrictions, kill-on-job-close (dies with the server);
  - the environment block is built from scratch (no API key, JWT secret or database URL);
  - command `python -I -S -B runner.py job.json`.
- **Docker runtime:** `--network none --read-only --tmpfs /tmp:rw,noexec,nosuid --user 10001:10001 --cap-drop ALL
  --security-opt no-new-privileges`, pids, memory and CPU limits, only the workspace mounted. Command line
  unit-tested; never run here.
- **Selection:** `MANIM_SANDBOX_ENABLED=0` → unavailable. `MANIM_SANDBOX_RUNTIME` = `auto`, `windows-appcontainer` or
  `docker`; any other value is refused. Each runtime checks itself.
- **Defence in depth** (each tested bypassed, to prove the OS refuses on its own):
  - static checks: import allowlist (no os, sys, subprocess, socket, ctypes, importlib, winreg, pathlib), forbidden
    calls (eval, exec, compile, `__import__`, open, …), forbidden attributes, dunder access → 422 `unsafe_code`;
  - an audit hook in the runner refuses process creation, sockets, ctypes, registry and file access outside the
    workspace and Python runtime;
  - the runner forces the profile's resolution, fps and background, counts frames, and stops a mid-render change.
- **Files:** workspace `jobs/job-<id>/{source,output,media,temp,home,logs}` under `MANIM_SANDBOX_DIR`. Python and the
  virtualenv get read/execute once. Output is accepted only as a regular `output/scene.mp4`, copied to
  `static_videos/manim_<fp16>_<attempt8>.mp4`; the workspace is then deleted. Paths are redacted from errors.
- **Network and processes:** no network capability (internet, DNS, 127.0.0.1, ::1, private ranges, 169.254.169.254
  and UDP verified refused). Child processes are refused. Timeout, cancel, lost lease, shutdown and server death stop
  the whole job before the worker gives up.
- **Profiles** (each limit adjustable only up to `HARD_MAX`: 1920×1080, 60 fps, 300 s video, 900 s timeout,
  4096 MB, 500 MB output, 64 processes, 200 KB source, 5 scenes):

| Profile | Resolution | FPS | Max video | Timeout | Memory |
|---|---|---|---|---|---|
| `preview` | 854×480 | 15 | 45 s | 90 s | 1024 MB |
| `standard` (default, used by the page) | 1280×720 | 30 | 60 s | 180 s | 1536 MB |
| `high_quality` | 1920×1080 | 30 | 60 s | 300 s | 2048 MB |

- **Common limits:** CPU 50 %, CPU time ≤ timeout, 1 process, 150 MB output, 800 MB workspace (checked each second),
  100 KB source, 3 scenes, frames = duration × fps. Breaches end with `memory_limit`, `timeout`, `cpu_limit`,
  `output_limit`, `frame_limit`, `resolution_limit`, `fps_limit`, `source_limit` or `scene_limit`.
- **Validation:** ffprobe must decode a video stream; size must equal the profile's; duration and file size within
  limits. A text file named `.mp4` → `invalid_output`; a 320×240 video → `resolution_limit`.
- **Phase 9 integration:** a render is an `ai_generation_runs` row with `kind = "manim"`; attempts gain the state
  `rendering`. `RecoveryManager.register("manim", manim_service)` dispatches by kind. Crash before or while rendering →
  rendered again; after the render → the output registered; after registration → finalized. Identical requests
  attach; two managers render once; cancel stops the sandbox.
- **Cache:** identity = sha256 of normalized code + Manim version + scene + profile width, height, fps and background,
  stored through the Phase 5 cache (provider `manim`, model `manim 0.21.0`). Old `static_videos/<Scene>_<sha16>.mp4`
  files (720p30) are adopted, so existing lessons do not re-render. `force` makes a new version.
- **Router and review:** `ManimRule` reuses an earlier render without rendering. Renders carry `project_id`,
  `scene_index`, `slot`, so a late result lands in the lesson (never replacing a shown visual). `still_wanted` skips
  removed, replaced or changed scenes (`cancelled / not_wanted`).
- **Assets:** adopted in place (`source = "manim"`); `details.generation` records hash, fingerprint, profile, Manim
  version, runtime, run, attempt and resource figures, never the code or a secret.

### Key files
- `manim_security.py` — `PROFILES`, `HARD_MAX`, `MANIM_VERSION`, `check_source`, normalization, fingerprint.
- `manim_sandbox.py` — `ManimSandbox` (`runtime()`, `execute()`), `WindowsAppContainerRuntime`, `DockerRuntime`,
  workspaces, health check, orphan sweep, failure categories and messages.
- `manim_runner.py` — in-sandbox bootstrap: profile, frame counter, audit hook.
- `manim_jobs.py` — `ManimService`: jobs, recovery, validation, cache, assets, concurrency.
- `sandbox/Dockerfile.manim` — Docker image (untested).
- `server.py` — new `/render`, `/api/manim/sandbox`, wiring, startup sweep, shutdown, cancel routing, run view.
- `visuals.py` — `ManimRule` reuse, `still_wanted` for renders.
- `ai_runs.py`, `ai_recovery.py` — `rendering` state, shared heartbeat, executors by kind, workspace sweep.
- `index.html` — lesson slot on renders, escaped friendly errors.

### API and data
- `POST /render`: `{code, profile?, project_id?, scene_index?, slot?, force?, wait?}`. Success: `{status, video_url,
  asset_id, cached, cache_hit, generated, provider, model, run_id}`. `wait:false` → 202 `{run_id}`.
- Errors: `{status:"error", detail, category}` with header `X-Error-Category`: 422 refused or failed, 413 too large,
  429 busy, 499 cancelled, 502 invalid output, 503 unavailable, 504 timeout.
- `GET /api/manim/sandbox` (login): availability, runtime, isolation, health check (`check=true` reruns it), profiles,
  limits, concurrency, counters; no host path or reason text. The runs API shows Manim runs with `kind`, `profile`
  and the label "Rendering", never the code; cancel is routed by kind.
- Database: one column `ai_generation_runs.kind VARCHAR(20)` (NULL = AI media), added by `ensure_schema`. A verified
  backup of the development database (78 lessons, 115 assets) was taken before the change.
- Env: `MANIM_SANDBOX_ENABLED`, `MANIM_SANDBOX_RUNTIME`, `MANIM_SANDBOX_DIR`, `MANIM_SANDBOX_IMAGE`,
  `MANIM_SANDBOX_PROFILE`; limits `MANIM_TIMEOUT`, `MANIM_MEMORY_LIMIT_MB`, `MANIM_CPU_LIMIT_PERCENT`,
  `MANIM_MAX_PROCESSES`, `MANIM_MAX_OUTPUT_MB`, `MANIM_MAX_WORKSPACE_MB`, `MANIM_MAX_DURATION`, `MANIM_MAX_FPS`,
  `MANIM_MAX_RESOLUTION`, `MANIM_MAX_SOURCE_KB`, `MANIM_MAX_SCENES`; queues `MANIM_MAX_CONCURRENT`,
  `MANIM_MAX_PER_USER`, `MANIM_MAX_QUEUED_PER_USER`, `MANIM_MAX_QUEUED`; `MANIM_AUTO_HEAL_ATTEMPTS`. Test only:
  `MANIM_TEST_CRASH_AT` (with `AI_FAKE_PROVIDER=1`). Defaults are the secure ones.

### Tests and results (at the phase's close)
- `tests/test_manim_sandbox.py` (31, real AppContainer; skipped without a secure runtime): OS isolation probes with no
  Python defences (files, network, processes, resources, identity), runner tests with real Manim (7 legitimate
  scenes, hook refusals, memory, frame, resolution / fps, timeout), 14 static escape attempts, no unsafe fallback.
- `tests/test_manim_jobs.py` (20): render → asset → cache, crash points, leases, two managers, cancel, attach, limits,
  Visual Review, router reuse, orphan sweep, API contract, schema step.
- `tests/manim_sandbox_browser_check.mjs` (17 checks, real server processes, real sandbox, real Chrome, silent).
- Backend 255/255 (204 + 31 + 20). Frontend 92/92. Phase 1 27/27, Phase 2 export 30/30, export with library assets
  35/35, Phase 3 26/26, Phase 4 24/24, Phase 5 11/11, Phase 6 17/17, Phase 7 18/18, Phase 8 12/12, Phase 9 14/14,
  Phase 10 17/17. Static checks pass.
- The Phase 4 test helper now patches `manim_service.render` / `manim_sandbox.execute` (not `server._render_manim`).
- Performance (standard profile, 5 s scene, median of 3): sandboxed 5.62 s vs direct 5.11 s (+0.5 s, about 10 %).
  Peak memory 369–376 MB. First render on a machine 21.8 s (one-time grant). Health check 0.1–0.35 s.

### Limitations and follow-ups
- Fixed in the phase: unsafe `/render`; its broken timeout; frame limit counting calls instead of frames;
  resolution / fps raised mid-render (a 3840-wide, 120 fps video in the first run); sockets misreported; raw
  cancellations; sandbox threads outliving workers; the 260-character path limit.
- **Registry reads:** Windows lets every AppContainer read some keys, including `HKCU\Environment`. A deny entry and
  LPAC did not help. Only the static checks and audit hook stop it. Keep secrets out of Windows user environment
  variables on a render host; prefer Docker on servers. System files readable by all AppContainers stay readable.
- The Docker runtime and `sandbox/Dockerfile.manim` are untested (no Docker here). Still true per the status table.
- Concurrency counters are per process (queue caps are in the database). Static checks may refuse harmless code
  (e.g. `open`, dunders, numpy file I/O) as `unsafe_code`.
- LaTeX is not installed, so `Tex` / `MathTex` scenes fail; the Gemini auto-heal is told to use `Text`.
- The current Hugging Face Space has no Docker daemon and is not Windows, so Manim reports unavailable until a render
  host with a secure runtime exists.
- Later: Phase 11 registers `document_analysis` runs with the same manager; Phase 20 records the scene id on renders;
  Phase 21 shows "Animation" in plain words and the browser check grew to 18/18.

## Phase 11 — Source Document Formatting Assistant

**Status:** Complete (AI analysis verified with the stand-in model; no real model key on this machine) · **Goal:** The
page flattened uploaded PDF and DOCX files into plain text before lesson generation, so headings, lists, tables and code
were lost, and TXT was not accepted at all. Phase 11 added an assistant that extracts a document with its structure,
analyses it, shows what is usable, unclear or missing, and prepares an "Aadhi-ready structure" the user can correct and
hand to the existing lesson generation. It never invents educational content and always shows where an item came from.

### What was built
- Structure-preserving extraction in the browser for PDF, DOCX, TXT and pasted text, reusing the page's pdf.js and
  mammoth (no new dependency). TXT upload is new, also for direct generation.
- Stored, never-changed document versions on the server (`source_documents` table).
- A rule-based structural analysis (no AI, instant): outline, content found, findings, readiness, recommendations and
  the Aadhi-ready structure.
- An optional AI analysis as a durable Phase 9 run of kind `document_analysis`: chunked, checkpointed, recoverable and
  cancellable. AI items are kept only if they can be traced to the document.
- The Document Assistant panel: overview, objectives, structure, issues, recommendations, the editable Aadhi-ready
  structure, Save, and "Use for Lesson Generation", which feeds the existing Lesson Director.
- Two fixes in the existing page: a shadowed `finalJsonData` in the `/generate-script` reader made every AI-generated
  lesson fail (also in the committed original `e413bf0`), and the chosen file name was inserted as HTML (now escaped).

### How it works
- **Extraction (`sources.js`)** produces blocks `{type, text, level, page, rows, alt, lang}` plus a SHA-256 of the
  file. Only text blocks reach the server: no file is stored and image data is not sent.
  - PDF: headings from font size; paragraphs, lists, captions, formulas, page numbers; monospace text becomes code with
    indentation rebuilt from positions; running headers and footers are dropped.
  - DOCX: mammoth HTML with Word's Title, Heading, Caption and Code styles → blocks, tables, images (alt text only).
  - TXT and pasted text: markdown, underlined and numbered headings, lists, fenced code, formulas, page markers.
- **Identity.** The server assigns block ids `b1…bn`, heading paths, paragraph numbers and a language. Identity is
  owner + file name + `content_sha256` (normalized blocks plus the extractor version). The same content returns the
  same document (`reused`); changed content under the same name becomes a new version.
- **Outline.** The title is a Word Title style, a `Title:` line or a unique top heading, else "not provided" (with an
  issue). Sections come from the top heading level, subtopics from the next. Objectives, prerequisites, summary,
  questions and references headings are recognised in English, Tamil and Hindi and feed their own parts.
- **Content found** (provenance `source`): definitions, explanations, examples, formulas (verbatim, variables checked
  against nearby text), code (verbatim, language guessed, expected output), visual references, important points,
  objectives, prerequisites, summary and source questions.
- **Findings** (provenance `analysis`) have a type, severity, why it matters, a reference and a suggestion. Types:
  missing title or headings, long paragraphs, overloaded or empty sections, unexplained formula symbols or code, a
  missing figure, repeated content, a term before its definition, an example before its concept, no objectives /
  examples / summary, and text that reads like an instruction to an AI. Each maps to one quality class.
- **Readiness** is the most serious class among open warnings (`well_structured`, `partially_structured`,
  `needs_reorganization`, `missing_context`, `ambiguous`, `incomplete`). `ready_for_generation` means no open warning.
  **Recommendations** derive from findings and can be accepted or rejected; only "summary last" changes the structure.
- **Aadhi-ready structure** (JSON): title, subject, audience, difficulty ("requires your input" until known),
  language, prerequisites, objectives, and teaching sections in order (summary last, references out). Each subtopic
  holds definitions, explanations, examples, formulas, code, visual opportunities, important points, quiz candidates.
- **Language** is detected from the script (12 scripts, plus English by common words). The source is never
  translated. The English-wording rules (definitions, examples, objectives) apply to English only.
- **Provenance.** Every item has `provenance` (`source`, `analysis`, `ai_suggestion`, `user` / `user_edited` with the
  original kept) and a `ref` (block ids, PDF page, nearest heading, paragraph number). The panel shows badges.
- **AI analysis.**
  - It uses the same Gemini / OpenAI clients and `.env` keys as `/generate-script` and the page's model choice, and
    respects `AI_GENERATION_ENABLED`. Without a key the result is honestly structural only.
  - Rules go in the system message; the document goes in the user message inside `<document>` tags, declared
    untrusted data. The answer is schema-checked JSON with one bounded repair (the bad answer is sent back, not the
    document). If it stays malformed, the AI part fails visibly and the structural result stays.
  - Traceability: cited blocks must be in the chunk, quotes must occur word for word (normalized), terms must occur.
    Other items are dropped and counted.
  - Chunks are whole sections up to 12,000 characters, else sub-sections, else block runs; above 40 chunks the AI
    analysis is refused. A global pass gets the outline and findings, not the text.
  - Each chunk is a checkpoint in `chunk_results`, written under the lease; recovery continues with the next chunk.
    A run that gives up closes the analysis with the structural result.
- **Reuse and staleness.** The same configuration (analysis version, mode, provider, model) reuses an analysis; a
  running one is attached to; `force` makes a new one. An analysis is stale if a newer file version exists, or it
  was made for other content or an older analysis version. Then the handoff is disabled and `lesson-input` gives 409.
- **Editing.** Title, subject, audience, difficulty, section titles / order / inclusion, objectives (AI ones start
  not accepted), prerequisites, and issue and recommendation status. Edits are separate JSON, validated (sections a
  permutation, ids exist, bounded lengths) and applied at read time. Found content is not editable. Editing is refused
  while an AI analysis runs.
- **Handoff.** `lesson_input()` renders the effective structure as text under a header saying it is material to
  teach, not instructions. Each line is tagged with provenance and location. Excluded sections, unaccepted
  suggestions and rejected recommendations are left out. The page sets `window.preparedSource` and runs the same
  `runGeneration()`. (Changed in Phases 20–21: the button is now "Write the lesson from this" and goes to the Studio;
  the one-step path runs only when chosen under Settings → Advanced.)
- **Security.** Request ≤ 6 MB; ≤ 6,000 blocks, 50,000 characters per block, 800,000 in total; tables ≤ 300 × 30;
  the page refuses files over 20 MB. Owner-only access (404 for others). Instruction-like text is flagged and handed
  over as material. Model errors mask keys. `[DOCUMENT]` log lines carry ids and counts, never document text.

### Key files
- `sources.js` — page extraction, API client, edit session and the Document Assistant panel (`window.AadhiSources`).
- `source_analysis.py` — pure analysis: normalization, hashing, language, outline, findings, readiness, Aadhi-ready
  structure, chunking, AI verification and merge, edits, `lesson_input()`.
- `source_documents.py` — `DocumentService` (storage, reuse, AI job, staleness), `call_model`, stand-in, API router.
- `models.py` — the `SourceDocument` and `DocumentAnalysis` tables.
- `server.py` — router, recovery registration, `/sources.js`, `source_document` on `/save-history`.
- `index.html` — the Analyze & Prepare button (`#analyze-btn`, today labelled "Prepare the document"), TXT upload,
  panel wiring (`window.documentAssistant`), `runGeneration()`, the generation fix.
- Tests: `tests/test_source_documents.py`, `tests/sources.test.js`, `tests/source_documents_browser_check.mjs`;
  fixtures in `tests/fixtures/sources/` (four `.txt` documents A–D and `make_docx.py`).

### API and data
| Endpoint | Purpose |
|---|---|
| `POST /api/source-documents` | submit extracted blocks; returns the document version and `reused` |
| `GET /api/source-documents/{id}` | the document and its analyses (with stale flags) |
| `POST /api/source-documents/{id}/analyze` | `{mode: auto\|structural\|ai, provider, model, force}` → the analysis (reused, completed or running); `mode: "ai"` without a model → 503 |
| `GET /api/source-analyses/{id}` | the analysis with edits applied, readiness, stale flag and reason, AI status |
| `PUT /api/source-analyses/{id}/edits` | save corrections (validated) |
| `GET /api/source-analyses/{id}/lesson-input` | the prepared source for the Lesson Director (409 when stale or running) |
| `POST /api/source-analyses/{id}/cancel` | stop a running AI analysis |

- `source_documents` (new): owner, file name, source type (pdf / docx / txt / text), `content_sha256`,
  `file_sha256`, extractor version, `version`, pages, language, counts, blocks (JSON).
- `document_analyses` (new): document, owner, `content_sha256`, `analysis_version`, `config_key`, mode, provider,
  model, status, `run_id`, `chunk_results`, `result`, `edits`, timestamps.
- Saved lessons may carry `source_document: {document_id, analysis_id}`. No existing table changed; a backup
  `projects.db.before-phase11` was verified first (82 lessons, 124 assets, all kept).
- No new settings: `GEMINI_API_KEY`, `OPENAI_API_KEY`, `AI_GENERATION_ENABLED`, Phase 9 `AI_JOB_*` / `AI_RECOVERY_*`
  and `AI_MEDIA_LOG`. Test only: `AI_FAKE_PROVIDER`, `FAKE_LLM_MODE` (ok, malformed_once, malformed, fabricate, fail),
  `FAKE_LLM_SECONDS`.

### Tests and results (at the phase's close)
- New suites: `tests/test_source_documents.py` (32 tests), `tests/sources.test.js` (12) and
  `tests/source_documents_browser_check.mjs` (18 checks in real Chrome: PDF, TXT with the stand-in AI, a real Word
  document edited and used, Tamil, pasted text, stale; the generated lesson played, reviewed and exported).
- Backend 287/287 (+32), frontend 104/104 (+12), Phase 11 browser check 18/18; all earlier suites passed.
- Two new tests first failed in the full run (test isolation); both were fixed and the backend rerun.

### Limitations and follow-ups
- PDF structure comes from font sizes and positions: no multi-column layouts, no scanned PDFs, no OCR.
- mammoth drops Word equations (OMML); Word lists become paragraphs unless their numbering is recognised.
- DOCX and TXT references give heading and paragraph, not page.
- Recommendations are advice to the Lesson Director; the assistant never rewrites the source.
- Later: Phase 20's Studio uses the assistant for "From a document" and reuses `call_model` for lesson writing. Phase
  20 recorded that `$$…$$` display equations are not listed as formulas; Phase 21 does not list this as resolved.
  Phase 21 rewrote the panel's wording for teachers and stopped taking ordinary short words in a sentence-like formula
  as symbols.

## Phase 12 — AI Presenter / AI Teacher

**Status:** Complete (AI presenter pipeline verified with the stand-in provider; no real presenter provider exists
here; Aadhi Teacher verified in Chrome) · **Goal:** Lessons had only the clip-based Aadhi mascot, placed by the
screenplay's `aadhi_position`, and no configured provider could make a presenter speak a supplied narration. Phase 12
built a reusable, provider-independent presenter that appears where it helps teaching, speaks the lesson's own
narration, uses expressions and gestures, keeps the content readable and stays the same across scenes. It supports the
lesson and is the foundation for Phase 13; it implements no cinematic composition.

### What was built
- A presenter model with four types: `mascot` (Aadhi, the existing `MascotController`), `illustrated` (Aadhi Teacher,
  drawn by the page), `ai_avatar` (a generated clip) and `custom` (a user's presenter with a reference picture).
  Built-in profiles `aadhi`, `aadhi-teacher`, `ai-teacher`; custom profiles in the new `presenter_profiles` table.
- A rule-based Presenter Director that writes an optional `scene.presenter_plan`, with safe composition.
- A speech timeline from the lesson's own TTS segments, and the Aadhi Teacher: an SVG presenter with expressions,
  gestures, blinking, breathing and a voice-driven mouth.
- An AI presenter clip pipeline through the existing AI media layer (capabilities, cache, durable runs, recovery,
  cancellation, Asset Library), with a test stand-in provider (`fake-presenter`).
- A presenter item per scene in Visual Review, start-screen presenter settings, and playback and export from the same
  saved plan.

### How it works
- **Vocabularies** (normalized; provider names stay in adapters):

  | Kind | Values |
  |---|---|
  | behaviours | idle, talking, explaining, thinking, listening, welcoming, concluding |
  | expressions | neutral, friendly, engaged, thinking, surprised, happy, encouraging, serious |
  | gestures | none, open_hand, point, counting, explaining, emphasis, thinking, welcome |
  | positions / placements | left, center, right, hidden / side, pip, foreground, background |

- **Fallbacks.** A presenter that cannot show an expression or gesture shows the nearest one (surprised → engaged,
  counting → explaining). This is recorded (`expression_shown`, `fallbacks`) and never fails.
- **Identity** is never a prompt string: `appearance_hash` covers id, version, appearance and reference picture.
- **Director rules by scene role:** intro → shown, side, welcoming; visual → explaining, points when the narration
  says "look at"; explanation → explaining, counting when the narration counts; math and text-heavy → shown small
  (picture-in-picture in its own zone); quiz → listening, encouraging; summary → concluding; code, technical
  (Manim / simulation) and video → hidden, because the visual needs the canvas.
- **Show presenter setting.** "As the lesson says" (default) follows `aadhi_position`; with Aadhi this is exactly the
  old behaviour, so existing lessons are unchanged. "Automatic" applies the role rules, "Always" makes hidden scenes
  small, "Off" hides the presenter. Teaching style (friendly, energetic, calm, formal) adjusts expressions; Advanced
  settings can fix expression or gesture. Visual Review decisions are applied first.
- **Safe composition.** Side zones are x 0.02–0.28 and 0.72–0.98 (height 0.14–0.86); picture-in-picture is a small
  box low in the presenter's own zone, above the subtitles. A conflicting picture-in-picture moves to the side; any
  other conflict hides the presenter, with the reason. Drawn and AI presenters never use the popup layouts.
- **One audio pipeline.** `POST /api/presenters/speech` builds the timeline from the same `[PAUSE]` segments and
  `/generate-audio` files the page plays: timing, a loudness envelope (25 values a second, from ffmpeg) and
  `audio_sha256`. Scenes with a drawn or AI presenter use server audio in preview too, so the presenter follows what is
  heard and recorded.
- **What each presenter does.** The Aadhi Teacher's mouth opens with the voice's loudness; this is not phoneme lip
  sync and is never called that (with browser speech it falls back to a plain talking animation). An AI clip is muted,
  re-synced at each segment to `segment.start + audio.currentTime`, rate-matched and paused on hold. Lip sync is
  claimed only when the provider's capability says so.
- **Providers.** `presenter` is a media type (a video). Capability flags: `lip_sync`, `audio_input`, `expressions`,
  `gestures`, `transparent_background`, `reference_image`, `consistent_identity`, `cancellation`, checked before any
  generation. Without a provider the answer is 503 ("AI presenter generation isn't configured yet …").
  `/api/presenters` lists providers that cannot present (Veo) with the reason.
- **Cache identity** (Phase 5): presenter id and version, appearance hash, speech audio hash, behaviour, expression and
  gesture as shown, background, provider, model, parameters; not position. "New version" is `force_regenerate`.
- **Assets.** A clip becomes a library asset (source `ai-presenter`; `details.presenter`, `details.generation`, no
  speech data or secret). `presenter_plan.media` is a lesson reference, so a used clip cannot be deleted.
- **Recovery** (Phase 9). The presenter request is saved with the run, so a restart resumes the same provider job with
  no second submission; an ambiguous paid submission becomes "needs attention". `presenter_still_wanted` skips scenes
  whose presenter was removed in Visual Review. A finished clip lands in the saved lesson's `presenter_plan.media`.
- **Visual Review.** A `presenter` item per scene, stored in `scene.visual_review.presenter`. Actions: Keep, Move,
  Choose a clip or picture, Remove, Restore automatic, and for AI presenters Generate / New Version. Opening the review
  never generates. The decision is authoritative for the Director, recovery and export.
- **Playback and export.** `renderSlide` asks the stage for the scene's effective plan; the legacy path returns none.
  A plan sets the board layout (`applyAadhiLayout`) and hides Aadhi's clip for drawn or AI presenters; the layer is
  cleared on every scene change. Export uses the same plans and preloads timelines and clips. A missing AI clip is a
  warning, never generated automatically (it may be billed); "Use Aadhi Teacher instead" is off by default.

### Key files
- `presenters.py` — presenter model, profiles, Director (`plan_scene`), safe composition, speech timeline, presenter
  requests, recovery checks and the `/api/presenters` router.
- `presenter.js` — page module (`window.AadhiPresenter`): settings, presenter stage, the illustrated teacher, API
  client, review facts.
- `ai_providers.py` — presenter media type, capability flags, `MediaRequest.presenter`, `FakePresenterProvider`,
  presenter provider order.
- `ai_media.py`, `ai_cache.py`, `ai_runs.py` — presenter output paths, validation, registration, the unavailable
  message and request hash; a presenter asset is a video; the presenter request field on runs.
- `assets.py`, `models.py` — the `ai-presenter` source and clip as a lesson reference; the `PresenterProfile` table.
- `server.py` — router, completion hook, still-wanted check, `/presenter.js`, run view shape, test TTS stand-in.
- `review.js` — the presenter item, decisions, generation and detail.
- `index.html` — settings block, `renderSlide` and narration hooks, planning, export preparation, review adapter.
- Tests: `tests/test_presenters.py`, `tests/presenter.test.js`, `tests/presenter_browser_check.mjs`.

### API and data
| Endpoint | Purpose |
|---|---|
| `GET /api/presenters` | profiles (built-in and own), what each can do here, presenter providers and non-capable providers, vocabularies |
| `POST /api/presenters/profiles` | create a custom presenter |
| `DELETE /api/presenters/profiles/{id}` | delete a custom presenter |
| `POST /api/presenters/plan` | the Director's plans (validated settings; never generates; unknown values → 422) |
| `POST /api/presenters/speech` | the speech timeline of a narration |
| `POST /api/presenters/generate` | a presenter clip: wait, or 202 with a run to follow |
| `POST /api/presenters/review` | record a presenter review decision, re-plan and save the scene |

- **`presenter_profiles`** (new): owner, name, appearance, `reference_asset_id`, defaults, version. No existing
  table changed; backup `projects.db.before-phase12` was verified first (86 lessons, 133 assets, all kept).
- Lesson JSON: optional `scene.presenter_plan` (`enabled`, `presenter_id`, `type`, `position`, `placement`, `layout`,
  `box`, `behavior`, `expression`, `gesture`, the `*_shown` values and fallbacks, `reason`, `review_status`, `media`)
  and `scene.visual_review.presenter`.
- Settings: `AI_PRESENTER_PROVIDERS` (presenter provider order, none by default), `AI_PRESENTER_PROVIDER` (preferred).
  Test only: `FAKE_TTS=1` (a speech-like tone, only with `AI_FAKE_PROVIDER=1`), `FAKE_PRESENTER_JOB_SECONDS`.

### Tests and results (at the phase's close)
- New suites: `tests/test_presenters.py` (25 tests), `tests/presenter.test.js` (12) and
  `tests/presenter_browser_check.mjs` (20 checks in real Chrome with the stand-in presenter and TTS: scenes A–F with
  no overlap, the export frame, the teacher's mouth, a server kill recovered from the same job).
- Backend 312/312 (+25), frontend 116/116 (+12), Phase 12 browser check 20/20. All earlier suites passed (Phase 7:
  28.7 fps effective, 0 stalls), some after fixes.
- Regressions fixed: the stand-in in the Phase 8 provider status (3 backend tests failed); profiles loaded before
  login opened the login box; the 1119 px start screen hid its top on an 800 px window (it now scrolls inside).
- Screenshots of scenes A–F were inspected and the drawn teacher's defects fixed (neck gap, detached arms, clipped
  hands). (Phase 21 later raised the presenter browser check to 22 checks.)

### Limitations and follow-ups
- No real AI presenter provider here; adding one needs a new dependency and approval. The adapter, capabilities,
  cache, recovery and review are ready for it.
- The Aadhi Teacher's mouth follows loudness, not phonemes, so it is not lip sync.
- Picture-in-picture makes the presenter small but does not widen the board. (Phase 13's composition templates give
  composed scenes their own layouts.)
- Drawn or AI presenter scenes use server TTS in preview, so the Default engine's preview voice becomes the server's
  Edge voice. (Phase 16 extended server audio in preview to every synchronized scene.)
- Custom presenters are a foundation only: no provider here accepts a reference picture, so they show as unavailable.
- Later: Phase 15 maps direction interactions onto profile gestures, Phase 16 makes presenters act at narration
  moments, Phase 17 frames the presenter by style, Phase 20 writes finished clips through `editor_api.update_lesson`
  (the scene found by id when the run recorded one), and Phase 21 shows the provider only in debug and escapes the
  drawn teacher's attribute values.

## Phase 13 — Cinematic Scene Generation

**Status:** Complete (composition, camera, transitions, review and export verified in Chrome; AI background verified
with the stand-in only) · **Goal:** Scenes were laid out by four CSS variables per `aadhi_position`, with a fixed
crossfade, a slow board zoom and no safe-area model. Phase 13 composes the router's visual, the Phase 12 presenter,
the scene's text and a background into designed scenes: placement, size, entrance timing, camera framing and
transitions. The composition is data (a plan per scene), rendered by the page and recorded by the export, so preview
and video match. Educational content keeps priority: nothing important is covered, cropped or pushed into the subtitles.

### What was built
- A cinematic composer (`cinematic.py`) that writes `scene.cinematic_plan`. It never selects visuals and never changes
  text, formulas or code.
- Nine templates as data: `presenter_intro`, `presenter_explanation`, `presenter_plus_visual`, `diagram_focus`,
  `formula_focus`, `code_focus`, `visual_focus`, `quiz`, `summary` (Phase 14 added `comparison`).
- A safe-area model, a camera with safety checks, a scene timeline, per-lesson transitions and backgrounds.
- An optional, explicit AI background through the existing AI image layer.
- `CinematicStage` in `cinematic.js`, which renders the plan with the page's own elements.
- A composition item and scene inspector in Visual Review.
- Start-screen settings: Scene style (Classic / Cinematic), Transitions, Background and Camera motion (Subtle / None).
- An optional screenplay `composition` field, added to the generation prompt.
- `mascot.suspend` to pause Aadhi's clip when a background covers the studio.

### How it works
- **Plan fields:** `version`, `template` (label and reason), `shot`, `style`, `background`, `layers`, `camera`,
  `transition`, `duration`, `timeline`, `safe_areas`, `presenter`, `notes`, `warnings`, `review_status` /
  `review_stale`, `fingerprint`, `plan_hash`.
- **Layers** have an id, type, role, normalized box (`x, y, w, h` in 0..1), `z`, visibility, opacity, scale,
  start / end, enter / exit animation, importance and whether the camera moves them. They point to content, never copy
  it: the visual layer to `visual_plan`, the presenter to `presenter_plan`, text to the scene's own fields, labels to
  `composition.labels` by index. Order: background 0, visual 20, board 30, presenter 40, labels 50, title 60,
  subtitles 90.
- **Validation** (`validate_plan`) runs on every plan as a safety net: boxes on the frame, known types and animations,
  sane timing and opacity, no important overlaps or subtitle-band intrusion, and camera framings that keep important
  layers whole.
- **Template choice:** a Visual Review choice, else the screenplay's choice, else a fixed table by the Phase 12 scene
  role (intro → presenter_intro, explanation → presenter_explanation, visual → presenter_plus_visual, math →
  formula_focus, code → code_focus, quiz, summary, technical / video → visual_focus). An intro with a visual becomes
  presenter_plus_visual. Layouts mirror for a presenter on the left.
- **Classic** (the default scene style) produces no plan, so existing lessons render exactly as before.
- **Safe areas** (16:9): title band y 0.05–0.15, content y 0.19–0.81, subtitle band y ≥ 0.85, presenter zone
  x 0.70–0.97. Boxes come from the template, never free pixels. The presenter's face (top 42 % of its box) must stay
  clear.
- **Camera.** Moves: static, slow_zoom_in, slow_zoom_out, pan_left / right / up / down, focus. Shots set the zoom cap
  (1.03–1.10, never above 1.12). A framing is allowed only if every important layer stays whole, at least 2 % from the
  edge and clear of the title and subtitles along the whole path. The composer takes the largest safe zoom nearest the
  target, else a static camera with the reason. Typical moves are 1.05–1.07×.
- **Camera rendering.** Title and subtitles are overlays the camera never moves. The page applies one transform per
  layer (`translate(vw, vh) scale`), all from the same framing, so layers move as one picture. Formula focus re-aims
  once MathJax has typeset. The camera stays still while Aadhi's studio clip is on screen, when motion is "none", or
  when the viewer prefers reduced motion (preview only).
- **Timing.** Duration is estimated from the narration (2.6 words/s plus pauses, at least 4 s). Labels enter at their
  time or at their `[SYNC]` reveal; emphasis is a short gold pulse. The stage's scene clock holds and resumes with the
  narration. Board text that does not fit shrinks in steps, never below 80 %; an overflow is reported.
- **Transitions.** One kind per lesson unless a scene asks: fade, crossfade, slide (3 % with a fade) or cut, 0.5–0.6 s
  on the page's View Transitions. They never delay the narration. Slide becomes fade without motion. (Phase 17 added
  soft_fade, zoom and wipe.)
- **Backgrounds.** Gradient (default), solid, studio (Aadhi's clip), a picture or clip from the Asset Library (under a
  scrim), or an AI background. A missing or inaccessible asset falls back to the gradient with a note and an export
  warning; another user's asset is never shown. Aadhi's lessons keep the studio.
- **AI background.** `POST /api/cinematic/background` → `background_request()` → the AI image layer (provider choice,
  cache, durable runs, recovery, Asset Library). The prompt is the style's words plus the user's wish, always calm and
  empty ("no text, no people"). Cache identity is prompt + provider + model + parameters (16:9). Generated only on
  click; "Generate a new background" is `force_regenerate`. A finished run lands in the scenes that wait for it.
- **Integration.** The presenter plan still decides who presents and how they act; the composition decides box, size,
  order and entrance and hands the box to `presenterStage`. The visual plan still decides what the visual is; the
  composition places it. A removed visual has no layer. Diagrams use `object-fit: contain`. In a cinematic scene the
  visual appears at once (Classic first shows the Skill Tree for 3 s).
- **Visual Review.** A composition item per scene in `scene.visual_review.composition` (`status`, `overrides`,
  `fingerprint`, `reviewed_at`), applied first by the composer. The scene inspector draws the composition to scale,
  lists facts and element states, and offers Keep, Change, Back to automatic and Preview this scene. It never
  generates. The fingerprint covers content, visual, presenter, explicit composition and lesson style; a change makes
  an approval pending.
- **Performance.** The glass blur, recomputed each frame of a camera move, cost about a fifth of the frame rate, so
  composed scenes drop it; Classic keeps it.

### Key files
- `cinematic.py` — composer, templates, layout boxes, validation, camera safety, timeline, backgrounds, review,
  inspector facts and the `/api/cinematic` router.
- `cinematic.js` — page module (`window.AadhiCinematic`): settings, `CinematicStage`, camera maths, inspector words,
  settings panel.
- `review.js` — the composition item and scene inspector.
- `mascot.js` — `suspend` (pauses Aadhi's clip behind a background).
- `assets.py` — the background as a lesson reference.
- `server.py` — router, `/cinematic.js`, the background completion hook.
- `index.html` — module, styles, settings block, scene hooks, transitions, planning, export preparation, review
  adapter, the optional `composition` field in the generation prompt.
- Tests: `tests/test_cinematic.py`, `tests/cinematic.test.js`, `tests/cinematic_browser_check.mjs`,
  `tests/fixtures/cinematic_diagrams.py`; `tests/mascot.test.js` (+1).

### API and data
| Endpoint | Purpose |
|---|---|
| `GET /api/cinematic` | vocabulary: templates, camera moves, shots, transitions, backgrounds, styles, animations, text roles, safe areas |
| `POST /api/cinematic/plan` | composition of every scene (Classic: none); never generates. 422 bad settings, 413 over 200 scenes, 404 another user's lesson |
| `POST /api/cinematic/review` | keep / change / reset a scene's composition (saved with the lesson). 422 unknown action or override, 404 another user's lesson or scene |
| `POST /api/cinematic/background` | an AI background (cache, runs, recovery); 403 when AI generation is off |
| `GET /cinematic.js` | the page module |

- Screenplay field `composition` (optional): `template`, `presenter_position`, `visual_position`, `camera`, `shot`,
  `focus`, `transition`, `background`, up to 6 `labels` (`text`, `at`: seconds or `{"sync": n}`) and `emphasis`.
  Unknown values are dropped with a warning.
- No new setting and no database change: plans and decisions live in the lesson JSON. The background asset is a
  lesson reference (`scenes[i].cinematic_plan.background`), so a used background cannot be deleted.
- Later phases added `/api/cinematic/regenerate` (14), `/direction*` (15) and `/style` (17).

### Tests and results (at the phase's close)
- Backend 337/337 (+25), frontend 133/133 (+16 Phase 13, +1 mascot), Phase 13 browser check 24/24. All earlier suites
  passed (Phase 7: 28.7 fps effective, 0 stalls).
- Export with library assets was 34/35 in the full run: two different scenes compressed to the same JPEG size (19,532
  bytes). Rerun unchanged: 35/35. The test was not changed.
- Measured: the cinematic export delivered 1,707 frames in 58.5 s (29.2 of 30 fps). A camera-move scene ran at 61–63
  page fps with the glass blur and 76–79 without it.
- Visual check at 1280×720 and 1920×1080: scenes A–G and Aadhi's lessons, nothing overlapping and text fitting.
  Preview versus export frame for scene B: mean difference 10.8 of 255 on a 32×18 grid.
- Defects found by looking were fixed: boards ignoring box height, the Skill Tree shown before the visual, cropped
  diagrams, small formula and code, label chips unreadable on Aadhi's wall, content within 18 px of the edge.
  (Phase 21 later raised this browser check to 25 checks.)

### Limitations and follow-ups
- Aadhi is filmed in full-frame studio clips: his lessons keep the studio background, a still camera while he is on
  screen, and he cannot be made small. Full composition needs the Aadhi Teacher, an AI presenter or no presenter.
- Timing is estimated from the narration's words; only `[SYNC]`-anchored labels and emphasis follow the real
  narration. (Phase 16 anchored camera, label and emphasis moments to narration positions.)
- Label and emphasis text come only from the screenplay's `composition` field. (Phase 15 added labels from its
  direction, e.g. a formula's symbols.)
- Background clips from the library decode alongside the scene; the cost was measured only for pictures.
- The inspector changes whole-scene choices only; it is not an editor. (Phase 19 added the editor, which writes such
  choices through these composition overrides.)
- No real AI background was validated: Pollinations answers 402 and there is no Gemini or OpenAI key.

## Phase 14 — Intelligent Scene Composer

**Status:** Complete (automatic composition verified in Chrome at 1280×720 and 1920×1080, preview and export;
AI-assisted composition verified with the stand-in model only; no real text model key on this machine) · **Goal:**
Phase 13 chose a template from a fixed role table with fixed placements, so it could not express a smaller presenter,
a larger visual, a motion level or information density. Phase 14 decides automatically, per scene, what the scene
teaches, which element leads, the presenter's role and size, the visual's place and size, text sizing, camera, motion,
emphasis and timing. The output is a Phase 13 plan. It is deterministic by default; a text model may help with
ambiguous scenes, limited to enum values and checked by the rules.

### What was built
- `scene_intent.py`: a structured scene intent (purpose, content, density, media, priority, weights, ambiguity,
  reading time, reasons, hash). No model is needed.
- `composer.py`: rule candidates, scoring, explicit choices, validation, bounded repairs, a safe fallback and optional
  AI-assisted composition.
- A `comparison` template (a full-width table with equal columns, or two lists side by side).
- Media-aware layout: visual boxes follow the asset's own shape; short text gets a card sized to it.
- Timing rules for presenter, visual, labels, formula emphasis and camera.
- Visual Review additions: composition summary, more overrides, "Automatic", Regenerate composition and a
  "Composition" filter.
- `POST /api/cinematic/regenerate` and composer settings on the page.

### How it works
- **Pipeline.** Scene → intent → 1–3 rule candidates (+1 from a model for an ambiguous scene in AI-assisted mode) →
  explicit choices on top (screenplay, then the user) → each candidate rendered by `cinematic.build_plan`, validated,
  tightened, repaired and scored → the best valid one, else the safe fallback → a Phase 13 plan plus a `composition`
  record (source, decision, locked choices, reasons, repairs, intent, ai).
- **Priority:** hard constraints > the user's choices (Visual Review) > the screenplay's composition > lesson settings
  > the composer's decision > the safe fallback. A lesson set to no motion keeps every camera still. Composing never
  generates media and never calls the AI media cache.
- **Purposes** (ordered rule list): intro, transition, definition, explanation, example, diagram, formula, code,
  process, comparison, quiz, summary, recap, demonstration. Examples: a quiz scene → quiz; simulation or AI video →
  demonstration; a "vs" title, a table of two or more columns or two lists → comparison; three or more steps →
  process; a visual with little text → diagram; otherwise explanation.
- **Intent inputs.** Content flags come from the board's HTML (parsed, never interpreted) and the visual plan. Density
  (low / medium / high) and reading time come from words, code lines, table cells and formulas. Media facts: visual
  kind, aspect (the library asset's size, else a per-kind default), presenter type, availability (an AI presenter
  without a clip is not available) and transparency. Ambiguity is set when two primaries compete.
- **Decision dimensions:** template; presenter role (dominant / secondary / small / hidden) and side; visual role
  (fullscreen / dominant / secondary / side_panel / hidden) and share; board size; camera; motion level (none /
  subtle / moderate); emphasis; timing; transition.
- **Rules per purpose** (examples): introduction → presenter large beside the hero title; definition → the words lead,
  the visual supports; diagram → diagram large, presenter small; formula → formula board, presenter small; code →
  presenter away, still camera; comparison → symmetric sides, presenter small, still camera; demonstration → the visual
  takes the stage. High density prefers no camera motion and a smaller or hidden presenter.
- **Scoring** (deterministic, weights in code): educational fit, hierarchy (one clear primary), readability, presenter
  role against its weight, density, motion appropriateness, consistency (keep the lesson's presenter side; avoid three
  identical layouts in a row), balance. Ties go to candidate order (an AI suggestion first, then the rules); never
  random.
- **Timing.** The presenter enters after the title (intro) or the question (quiz). Labels enter one after another.
  The formula is emphasised at the first `[SYNC]` reveal, with the camera leaning in. The visual is emphasised when the
  narration points at it.
- **Explicit choices.** A layout chosen by the user or screenplay keeps its own arrangement; the composer still sizes
  the visual and repairs. A user's visible presenter side brings back a presenter the screenplay hid.
- **Hard constraints:** every Phase 13 rule, plus readability (board text, estimated from the page's type sizes, never
  below 80 %; code by its widest line too).
- **Repairs** (one step at a time, recorded): still camera; presenter steps back, then small; visual dominant →
  secondary; presenter away; visual to a side panel. A user's choice that breaks a hard constraint is repaired and the
  inspector says "your choice could not be kept".
- **Fallback:** main content centred, presenter small if there is room, still camera; last resort the Phase 13 layout.
  Explicit choices are not applied to it, and the inspector says so. A lesson never fails because of the composer.
- **AI-assisted composition** (optional; Automatic makes no model call).
  - Only ambiguous scenes are asked: at most 6 per lesson, in parallel, within a 25 s budget. A late answer is not
    waited for; a scene past the sixth is recorded as not asked.
  - The brief is the structured intent plus short excerpts, declared data. It uses Phase 11's `call_model` (Gemini /
    OpenAI, the page's provider) or the composer's stand-in on test servers.
  - The answer is one JSON object of enum values. It is checked, gets one bounded repair, and unknown fields are
    ignored (only their names are listed, capped). The model never emits code, HTML, CSS or coordinates.
  - The suggestion becomes one more candidate with a small bonus. It cannot hide the visual, raise motion above the
    lesson's or move the presenter off the lesson's side. Failure, timeout or no key leaves the rules in charge.
  - It is kept in the plan's `composition.ai`, keyed by intent hash, provider, model and composer version, and reused
    by later plans and by Keep / Change. "Regenerate composition" asks again about that scene only.
- **Review.** Keep and Change compose the scene in the lesson's context (neighbours and kept AI suggestion), so what
  is approved is what was shown. Regenerate withdraws an approval but keeps the user's choices. Compositions are never
  approved automatically. The fingerprint includes the composer version and, when a model decided, the model.

### Key files
- `scene_intent.py` — scene intent: board parser, purpose rules, density, media, priority, weights, ambiguity.
- `composer.py` — candidates, scoring, overrides, timing, repairs, fallback, AI brief / validation / stand-in,
  `compose_lesson`.
- `cinematic.py` — `build_plan` renders a decision; `phase13_decision`; the comparison template; layout shares,
  compact cards and picture-shaped visuals; the composer in the fingerprint; the new endpoint and settings.
- `cinematic.js` — composition summary, the Composition setting, the regenerate call, a stricter text fit.
- `review.js` — the inspector's composition block, overrides with "Automatic", regeneration, the Composition filter.
- `index.html` — composer settings, review adapter, comparison and inspector styles.
- `presenters.py` — a malformed visual plan no longer crashes.
- Tests: `tests/test_composer.py`, `tests/composer.test.js`, `tests/composer_browser_check.mjs`,
  `tests/helpers/cinematic-dom.js` (fake DOM moved out of `cinematic.test.js`).

### API and data
| Endpoint | Change |
|---|---|
| `POST /api/cinematic/plan` | composed by the Intelligent Scene Composer; settings `composer` (rules / ai), `composer_provider`, `composer_model`; plans carry `composition` |
| `POST /api/cinematic/regenerate` | one scene's composition decided again (no media; an approval withdrawn) |
| `POST /api/cinematic/review` | overrides also `presenter_size`, `visual_size`, `motion`; `"auto"` removes a choice |
| `GET /api/cinematic` | `composer`: modes and whether AI-assisted composition can run, per provider |

- No database change: the composition lives in the lesson's plans (`cinematic_plan.composition`).
- The code reads the AI budget from `COMPOSER_AI_BUDGET` (default 25 seconds). Test only: `FAKE_LLM_MODE`,
  `FAKE_LLM_SECONDS` drive the stand-in.

### Tests and results (at the phase's close)
- Backend 373/373 (+36), frontend 140/140 (+7), Phase 14 browser check 17/17. All earlier suites passed (Phase 7: 28.5
  fps effective, 0 stalls, page 127.6 fps).
- The first full run had two failures, both fixed: the Phase 1 mascot check (the login box opened on the new
  `GET /api/cinematic` call; now stubbed) and the Phase 13 "presenter + visual" scene (an explicit layout got a small
  presenter).
- Visual check: a nine-scene mini-lesson composed automatically and inspected at 1280×720 and 1920×1080 over four
  rounds. Nothing overlaps; the page's text fit reported 0.84–1.0. Preview versus export frame: mean difference 5.9
  of 255 (5 in the final run).
- An end-of-phase code review and fact-check fixed, among others: the 25 s budget not honoured, regenerate asking other
  scenes, Keep / Change composed without neighbours, approvals surviving an AI decision, and AI answers able to hide
  the visual or raise motion.
- No real LLM validation: only the stand-in model (valid, repaired, malformed, fabricated, failing answers) and a real
  provider name with no key.

### Limitations and follow-ups
- Text size is estimated from the page's type sizes; the page's own text fit remains the safety net.
- Aadhi keeps his studio constraints (no small form; a still camera while on screen).
- The AI suggestion only chooses among existing building blocks. (Phase 15 added the visual direction layer.)
- Consistency covers presenter side and repeated layouts only. (Phase 17 added lesson-wide styling.)
- Later: Phase 16 builds each scene's synchronization plan inside the composition step. Phase 21 renamed the
  "Composition" filter and wording to "Layout".

## Phase 15 — AI Visual Director

**Status:** Complete (deterministic direction verified in Chrome at 1280×720 and 1920×1080, preview and export;
AI-assisted direction verified with the stand-in model only; no real text model key on this machine) · **Goal:** The
composer arranged existing blocks but did not decide how a concept should be taught visually. Phase 15 decides, per
scene, the visual strategy: what is taught, what the learner should understand, what carries the explanation (a step
flow, a timeline, a formula symbol by symbol, code beside its output, a comparison, a diagram, key points), what the
presenter does, and what camera and motion mean. The result is a structured plan that the router, Presenter Director
and composer consume; the director never generates media.

### What was built
- `visual_director.py`: scene understanding, a bounded strategy vocabulary, rule candidates, hard constraints,
  repair, scoring, fallback, continuity, user choices and optional AI-assisted direction → `scene.visual_direction`.
- Consumers: a Visual Router preference (`visuals.route_preference`, read as `VisualRequest.preference`),
  `presenters.direction_interaction` (interaction → profile gestures and expressions) and a composer direction layer.
- Data-driven board representations on the Phase 13 page (`BOARD_STYLES`): a step flow with numbered cards and
  connectors, a timeline with date badges, code with an output card, key points as cards; a "together" reveal; label
  chips from the direction.
- A direction block in Visual Review with Change direction, Back to automatic and Regenerate direction.
- Settings "Visual direction: Automatic / AI-assisted" and "Learners: General / Beginner / Intermediate / Advanced".
- A fix for an earlier gap: a router plan that found nothing no longer gets a visual area in Phases 13–14.

### How it works
- **Planning chain** (cinematic style only; Classic is untouched): direction first (`POST /api/cinematic/direction`,
  saved with the lesson), then the router, the presenter and the composition, which returns the directions it
  followed. Responsibilities stay put: Phase 15 says what should teach; 14 composes; 4 obtains the visual; 12
  presents; 13 renders; Visual Review stays authoritative.
- **Scene understanding** (`understand`, no model) reuses Phase 14's board parser (`scene_intent._Board`, now also
  keeping list items, table headers, keywords, paragraphs, the definition and the block after code) and purpose rules
  (`scene_intent.classify`), Phase 11's `formula_parts`, and the lesson's `concept_map` / `concept_id`. Signals:
  concept and key terms, defined term, formula symbols and their meanings in the scene's words, code and whether its
  output is stated, steps, dated events (new), comparison sides, presenter, learner level, narration and `[SYNC]`.
- **Two visuals kept apart:** `asked_visual` (what the screenplay asks for; the only input to the router preference)
  and the visual the scene will really show (the router's plan; a found-nothing or removed visual means none).
- **Vocabulary.** 12 families and 30 strategies (conceptual, structural, process, temporal, comparative, mathematical,
  programming, scientific, data, demonstration, assessment, summary); 20 "what teaches" kinds; presenter roles
  (dominant, secondary, guide, demonstrator, hidden); interactions (introduces, explains, points to the visual / board,
  pauses for the visual, summarizes, asks); camera and motion intents; reason codes with fixed short sentences.
- **Rules per scene kind** (1–3 candidates): introduction → the presenter introduces; definition → its own card, or
  diagram-first; process → a step flow built step by step, the presenter a small guide; timeline → events in order;
  formula → symbol by symbol, symbols labelled, camera leaning in; code → code beside its output when stated, else a
  walkthrough, presenter away, still camera; comparison → both sides together, still camera; diagram → diagram-first,
  presenter small and pointing (Rule G: small when the visual evidence is primary); quiz → the question with minimal
  distraction; summary → key points as cards; simulation / video → the visual takes the stage.
- **Score** (deterministic): learning-goal fit, representation support, clarity, density, what exists (approved or
  explicit assets count more), presenter compatibility, duration, continuity, simplicity for beginners. Ties go to
  rule order; an AI suggestion goes first with a small bonus. Goals and focus are plain words ("follow the 4 steps in
  order"); a title that is not a concept name ("Quick check", "Worked example: …") is never used as one.
- **Hard constraints:** each strategy needs its content (a step flow ≥ 2 steps, a timeline ≥ 3 dated events, code
  with output needs the output sentence, a formula, two sides, a definition, a question, a real visual).
- **Repairs:** no presenter → hidden; a missing supporting visual is dropped; a build needs items and time; code,
  quizzes and comparisons keep a still camera; dense text → no dominant presenter; a demonstrator without a visual
  becomes a guide. **Fallback:** the board with the presenter beside it, revealed together, still camera.
- **Continuity.** A bounded lesson memory (at most 40 concepts) maps a concept to the scene that first showed it, its
  representation, strategy and terms. A later scene of the same concept prefers the same representation where its
  content allows and records `continuity.from_scene`. It never overrides a representation the content requires.
- **User choices.** `scene.visual_review.direction = {status: "changed", overrides}` for strategy, what teaches,
  presenter role, camera, motion and preference. Unsupported choices are set aside with a message; values outside the
  vocabulary are ignored; "Automatic" gives one choice back. A changed direction changes the composition fingerprint,
  so an approved composition re-opens.
- **AI-assisted direction** (optional; Automatic makes no model call). Only undecided scenes (two valid candidates
  within 0.05) are asked: at most 6 per lesson, in parallel, within 25 s. The brief is structured and bounded,
  declared data. The answer is one JSON object of enum values (strategy, what teaches from the available visuals only,
  presenter role, camera, motion, complexity, up to 3 emphasis targets, up to 4 reason codes), with one bounded repair.
  It is kept keyed by what the screenplay says, provider, model and version, and rebuilt from the server's own checks
  on reuse (a record sent back by the page is never trusted). It uses Phase 11's `call_model` or a test stand-in.
- **Router integration.** `visual_direction.route = {prefer_existing: true, preferred_media, match_terms}`, derived
  only from the screenplay's request and the user's preference. The router uses it for library ranking and library
  before generation (it never turns AI on); match terms are a tie-breaking library bonus, never in a prompt, the AI
  cache identity or `still_wanted`. It is not part of a visual's review fingerprint.
- **Presenter integration.** Interactions map onto existing gestures and expressions (introduces → welcome, points →
  point, summarizes → open hand, asks / pauses → listening), only in the Director's own choice. The lesson's and Visual
  Review's choices win.
- **Composer integration.** The direction is a soft layer below the screenplay's and the user's composition choices:
  presenter and visual roles, a small bonus for layouts that show the representation, camera intent → a Phase 13 move,
  the board representation, the reveal, emphasis, and labels only where they add meaning (a formula's symbols, never
  a repeat of the board). Plans carry codes and pointers; texts live in the scene's direction.

### Key files
- `visual_director.py` — understanding, vocabulary, candidates, checks, repair, scoring, fallback, continuity, user
  overrides, AI brief / validation / stand-in, `direct_lesson`.
- `scene_intent.py` — board parser keeps texts; `classify` extracted; a found-nothing router plan is no visual.
- `composer.py` — the direction layer and board-style text estimates.
- `cinematic.py` — board representations, direction labels, reveal, direction in plan and fingerprint,
  `found_nothing`, direction endpoints and settings, review in the page's lesson context.
- `visuals.py`, `presenters.py` — the router preference (`route_preference`); `direction_interaction`.
- `cinematic.js` — representations, date badges, direction labels, API client, direction summary, settings.
- `review.js` — the direction block and actions.
- `index.html` — the planning chain, review adapter, representation styles, the together reveal.
- Tests: `tests/test_visual_director.py`, `tests/direction.test.js`, `tests/direction_browser_check.mjs`; changed
  `tests/test_visuals.py`, `tests/test_presenters.py`, `tests/presenter.test.js`.

### API and data
| Endpoint | Purpose |
|---|---|
| `POST /api/cinematic/direction` | every scene's direction (cinematic style; nothing generated); saved with the lesson |
| `POST /api/cinematic/direction/regenerate` | one scene's direction decided again (AI-assisted: a fresh suggestion), then its composition; never media |
| `POST /api/cinematic/direction/review` | the user's direction choices (change / reset; "auto" gives one back) |
| `POST /api/cinematic/plan`, `/review`, `/regenerate` | also return the direction(s) each composition followed; `/review` takes the page's scenes as context |
| `GET /api/cinematic` | `director`: modes, availability per provider, the vocabulary |

- No database change: the direction lives in the scene (`visual_direction`, `visual_review.direction`).
- The code reads the AI budget from `DIRECTOR_AI_BUDGET` (default 25 seconds). Phase 16 reuses this setting for its
  own AI alignment budget.

### Tests and results (at the phase's close)
- Backend 439/439 (+66), frontend 148/148 (+8), Phase 15 browser check 23/23 (preview / export frame difference 6.1).
  All earlier suites passed (Phase 7: 28.3 fps effective, 0 stalls, page 124.3 fps).
- The full run was interrupted once by the machine sleeping and slowed once by heavy load. Affected checks (Phases 4,
  12, 13) were rerun alone on the final code and passed; no code changed for this.
- Visual check: a representative lesson (introduction, definition, process, diagram and its returning concept,
  formula, code, comparison, timeline, quiz, summary) directed automatically and inspected at both sizes. Nothing
  overlaps; text fit 0.84–1.0; no AI generation ran.
- End-of-phase security and correctness reviews fixed, among others: crafted scenes stalling the server (texts now
  bounded, 200-scene cap on direction review), AI records trusted as sent, out-of-vocabulary values causing 500s, the
  "Still" motion choice not stopping the camera, and strategies that contradict the content being accepted.
- No real LLM validation: only the stand-in model (also a slow one against the budget) and a provider with no key.

### Limitations and follow-ups
- The director chooses among what the lesson has. It can say a diagram would help ("visual_need": wanted) but never
  creates a visual request, prompt or slot; adding a visual stays a Visual Review action.
- Representations come from the lesson's markup; there is no new diagram generator. The output card shows the
  screenplay's sentence, not the program's real output (nothing is executed).
- Emphasis targets are scene-level. (Phase 16 added timed moments for list items, table columns, marked terms, the
  output card and the formula; single code lines and diagram parts still need structured content.)
- Continuity covers representations and terms. (Phase 17 added lesson-wide style colours.)
- Scenes carry no link back to Phase 11's source sections, so the direction records the concept, not page or paragraph
  references. (Phase 20 added per-scene `scene.source` references, by word overlap, for lessons written in the Studio.)
- Later: Phase 16 uses the direction's emphasis, presenter interaction and camera intent to time moments against the
  narration; in Phase 21 the direction summary takes the debug flag (technical details behind `?visualDebug`).

## Phase 16 — Presenter + Visual Synchronization

**Status:** Complete (synchronization verified in Chrome at 1280×720 with the server's voice, preview and export,
on time at speech rates 1.0 and 1.3; AI-assisted alignment verified with the stand-in model only; no real text model
key on this machine) · **Goal:** Scenes were timed loosely: `[SYNC]` reveals were checked on `timeupdate` (about
250 ms jitter), the stage clock could start up to 0.8 s after the narration, and the preview spoke with the browser's
voice while the export recorded the server's audio. Phase 16 decides when each element acts, so that the narration,
presenter, visual, text, labels and camera teach the same thing at the same moment. It adds one synchronization plan
per scene, played by the existing Phase 13 page (preview = playback = export).

### What was built
- `sync_director.py`, the deterministic Synchronization Director. Its inputs: the narration (segments split at
  `[PAUSE]` as the page does, `[SYNC]` beats, sentences, concept words), the Phase 15 direction, the Phase 14/13
  composed scene and the Phase 12 presenter capabilities.
- The plan, stored at `scene.cinematic_plan.sync`: semantic events anchored to the narration, compiled to narration
  positions `{segment, ratio}`, with estimated seconds as the fallback. It also holds the attention flow, an end
  hold, metrics and a fingerprint.
- A synchronization runtime in `cinematic.js` (`CinematicStage`), with clocks, event dispatch, camera moves from the
  current framing and `syncSummary`. It also fixes an early `[SYNC]` beat that was lost during formula typesetting.
- Presenter acting at a moment: `presenters.py` (`sync_capabilities`, `acting_for`) and `presenter.js` (`act`).
- Server audio in the preview for synchronized scenes, with a per-segment browser-voice fallback.
- Optional AI-assisted alignment for targets the rules cannot place.
- A read-only "Synchronization" block in Visual Review's composition inspector.
- Re-planning when a scene's narration or board is edited, and when a visual decision is made in Visual Review.

### How it works
- **Positions.** A position is the page's existing `[SYNC]` rule: character index ÷ the segment's clean-text length.
  It does not change with the voice, the audio file or the speech rate. New narration text recompiles the timing;
  nothing else is regenerated.
- **No word timing exists.** Edge-TTS word boundaries are discarded by the audio path. Sentence and concept anchors
  are placed by character ratio inside their segment: exact with the test server's tone, approximate with a real voice.
- **Clocks, in order of preference:**
  - the narration audio actually playing (`requestAnimationFrame`, media time);
  - browser speech: each segment's start is known, and the estimate runs inside the segment;
  - no narration or muted: estimated seconds from the scene start.
- **Preview = export.** A synchronized scene plays the server audio in the preview too. If the server voice fails
  for a segment, the browser voice speaks that segment and the moments follow the speech clock from that segment on.
  The next segment's server audio takes the clock back. A scene is never silent.
- **Never before its word.** An event fires at its position, at most 0.06 s early. Each event has a lateness budget
  (0.25 s default, 0.35 s presenter, 0.4 s camera). The browser check measures against it; the page does not enforce it.
- **Events** (a bounded vocabulary, only what the renderer and presenter can really do):

| Type | Placed at | Page action |
|---|---|---|
| `diagram_focus` / `visual_highlight` | the sentence pointing at the visual ("look at…"); a diagram term when named (at most two `diagram_focus` per scene, the look cue included) | the visual pulses |
| `formula_emphasis` | the first `[SYNC]` beat (the formula's reveal), else the sentence stating it | the formula pulses |
| `label_enter` | a symbol's label when its meaning is spoken; a screenplay label at its `[SYNC]` | the chip enters |
| `text_emphasis` | a timeline date as said; a comparison side when named; a step when named; the marked defined term | the item glows |
| `text_reveal` | the sentence saying what the code prints (else a share of the scene) | the hidden output card fades in |
| `camera_focus` / `camera_return` | the key teaching moment (first `[SYNC]`, first step, look cue); a return at 82 % of a long scene | one camera move from the current framing |
| `presenter_point` / `_explain` / `_summarize` | pointing when attention moves to the visual, formula or output (twice at most); explaining at the next sentence; summing up | gesture / expression or narration state changes |

- `label_exit`, `presenter_pause` and `presenter_emphasis` are in the vocabulary, but the director does not place them.
- **Educational rules:**
  - One primary emphasis at a time. Two within 0.8 s on different targets: the more important keeps the moment, even
    when it comes second. Camera moves are not primary emphasis; the return ranks last.
  - Caps: 6 labels, 8 text emphases, one camera focus and one return, 4 of each other type, 24 events in all.
  - The result stays visible: a scene with a result (code output, formula, steps, timeline) holds it 0.3–0.7 s by
    complexity before the next scene (0.1 s more for beginners, at most 0.7 s).
  - Attention moves presenter → visual → detail → presenter → result, recorded with the plan.
  - Orphans are removed: a visual removed in Visual Review takes its events with it; a board target the page could not
    find is never sent.
  - A short scene (under 4 s, or one sentence) keeps three moments besides its labels; labels are always kept.
  - A camera focus lasts 0.5–4 s. The return needs a scene of 9 s or more with the focus held at least 4 s. A scene
    whose plan has no camera moment plays its planned camera move as before.
- **Presenter acting.** The drawn teacher switches gesture and expression in place; its mouth keeps following the
  voice. Aadhi changes narration state only, and only while talking (never over the quiz's question or success
  states). An AI clip cannot act, so the visual carries the moment.
- **Concepts are references.** Events carry `{kind, index}` references (a label, a step, a column), resolved on the
  page. This keeps Phase 13's rule that a plan points to the lesson's text and never copies it.
- **AI-assisted alignment (optional).** It runs only in AI-assisted mode (in the code: the lesson's `director`
  setting is `ai`, in cinematic mode). It covers targets the rules could not place by name: an unnamed comparison
  side, a paraphrased step, the visual without a look cue, the code's output.
  - The model chooses only a sentence number (first 20 sentences) for each listed target id. Enum ids and in-range
    numbers only, with one bounded repair.
  - At most 6 scenes per composition, within Phase 15's `DIRECTOR_AI_BUDGET` (25 s). A failure or timeout leaves the
    rules in charge.
  - The outcome is kept with the scene (`sync.ai`) and re-validated when reused. A failed, late or invalid attempt is
    shown, not re-asked, until the scene's sentences or targets change or the user regenerates that scene. A
    regenerated scene is asked first, so the cap never skips it. Skipped or unavailable scenes are asked later.
- **Re-planning.** Editing a scene's narration or board in the script editor re-plans the lesson first, then draws
  and saves the scene once. Direction and composition come back the same; no media is generated.
- **Responsibilities unchanged:** Phase 15 says what, 14 composes, 13 renders, 12 presents, Visual Review approves.

### Key files
- `sync_director.py` — the director: narration model, `synchronize`, `validate`, `fingerprint`, AI alignment
  (`align_lesson`).
- `composer.py` — calls the director for each composed scene, adds the plan's `sync`, presenter capabilities, the AI
  alignment call.
- `cinematic.js` — the runtime: clocks, dispatch, camera moves from the current framing, `syncSummary`.
- `presenter.js` — `act`: the drawn teacher's gesture and expression changed in place.
- `presenters.py` — `sync_capabilities` and `acting_for`: what a presenter can do at a moment.
- `review.js` — the Synchronization block, narration retiming helpers, re-plan after a visual decision.
- `index.html` — server audio in preview with speech fallback, the narration clock, presenter acting, the end hold,
  the script-editor re-plan.
- `scene_intent.py` — the board's marked words in page order. `visual_director.py` — a huge `[PAUSE:n]` bounded.
- Tests: `tests/test_sync_director.py`, `tests/sync.test.js`, `tests/sync_review.test.js`,
  `tests/sync_browser_check.mjs`; updated `tests/test_presenters.py`, `tests/presenter.test.js`.

### API and data
- No new endpoint and no database change. The existing `POST /api/cinematic/plan` returns plans with `sync`;
  `POST /api/cinematic/regenerate` (one scene) re-asks that scene's alignment in AI-assisted mode.
- `scene.cinematic_plan.sync` (from the code): `version`, `timing_source` (`narration` | `estimate`), `narration`
  (segments, `estimate_total`), `events`, `attention`, `end_hold` (0–0.7), `fallback` (`estimate` | `simplified` |
  null), `metrics`, `fingerprint`, optional `ai`.
- Each event: `id`, `type`, `target` (layer: visual, board, labels, presenter, camera), `at` `{segment, ratio}`,
  `estimate`, `duration` (0–10 s), `anchor`, `concept`, `priority`, `depends_on`, `params`.
- The fingerprint covers the narration, the direction's fingerprint, the composition's plan hash and the presenter.
- Bounds: 200 narration segments, a `[PAUSE:n]` at most 60 s. Setting reused: `DIRECTOR_AI_BUDGET` (Phase 15).

### Tests and results (at the phase's close)
- Silent regression (Chrome with `--disable-audio-output`, speech stubbed, no real AI call).
- Backend 491/491 (+52 Phase 16). Frontend 180/180 (+32). Phase 16 browser check 17/17 (preview/export frame
  difference 4.7). Phases 1–15 browser checks pass (exceptions below); Phase 7: 28.6 fps effective, 0 stalls.
- Phase 13 failed once in the full run (23/24, page 28 fps against a 30 fps floor). It passed 24/24 alone three times
  (76, 77, 79 fps). The cause was not identified; no code was changed.
- Phase 5 (a button redrawn during a click, on a Classic scene) and export with library assets (two JPEGs with equal
  byte counts; the export was correct) each failed once in the final re-run and passed alone.
- Measured timing: 25 moments with a measurable word position, maximum error 0.05 s, mean 0.04 s (0.06 s and 0.04 s
  in the full regression). The same moments land on their words at speech rate 1.3.
- The export recorded 28 moments, all on time, at the preview's positions. Code-scene frame difference 4.7 of 255.
- A representative lesson was rendered and inspected moment by moment (definition, diagram, formula, code, comparison,
  timeline, summary). The first inspection round was rejected and fixed (timeline glow, comparison glow).
- LLM validation: not done for real (no key). Verified with the stand-in model and the "no key" path only.

### Limitations and follow-ups
- Positions are character ratios, not word timings. They are exact for the test tone and approximate for a real
  voice. Keeping Edge-TTS word boundaries would make them exact; that was out of scope.
- Concepts are found by their words. A paraphrase needs AI alignment; otherwise the moment falls back to a share of
  the scene or is left out.
- Emphasis targets are what the page can find: the visual as a whole, top-level list items, table columns, the two
  sides of a comparison, marked terms, the output card, the formula. No single code line or diagram part.
- Presenter acting is in place, not animated (no tween between poses).
- With browser speech (a preview fallback only), moments inside a segment follow the estimate.
- Synchronization exists in the cinematic style only; Classic is unchanged.
- The plan is read-only in Visual Review. Phase 19's editor edits scenes (order, minimum duration, narration, which
  Phase 16 retimes through the planner); it does not move individual moments.
- Changed in Phase 21: redrawing a paused lesson no longer starts a synchronized scene's narration on its own, and the
  Synchronization block shows server reasons only with `?visualDebug`.

---

## Phase 17 — Professional Video Styling System

**Status:** Complete (four versioned style families rendered and inspected in Chrome at 1280×720, preview and export;
accessibility checked in the page; old lessons unchanged; no media regenerated) · **Goal:** A lesson's look was a
"typography" choice (`academic` | `modern`) kept per browser, over about 600 hard-coded colours. Phase 17 gives
every lesson a deliberate visual identity: one of four style families, expressed as structured, versioned tokens
that the existing cinematic page consumes. Style decides how a scene looks, never what it teaches or when.

### What was built
- `styles.py`, the style registry: 4 families × versions, about 110 semantic tokens per look, preferences, bounded
  user overrides, one resolver, an accessibility guard and a fingerprint.
- Four families: Academic, Cinematic Education (the default, the page's existing look), Children's Education and
  Corporate Training.
- The effective style in every cinematic plan (`scene.cinematic_plan.style.look`), applied by the page as CSS custom
  properties (`--st-<token>`).
- A tokenized cinematic CSS in `index.html`: colour, type and shape rules read `var(--st-<token>, <old value>)`.
- A style picker in the cinematic settings panel, with bounded choices and the style's suggestions.
- Per-scene accent and background in Visual Review's composition Change form, and a Style block in the inspector.
- Persistence: the style is saved with the lesson and changed in place (`POST /api/cinematic/style`).
- Presenter frames and name cards, visual frames and chart colours per style.

### How it works
- **One resolver, on the server.** `styles.resolve` runs once per scene when the composition is planned. The page
  never works out a style: `CinematicStage.applyLook` writes the plan's variables on `<html>` and sets
  `body[data-cine-style / -tone / -emphasis / -caption / -pframe / -vframe / -motion-level]`.
- **The page re-checks values.** Names must match `--st-[a-z0-9-]`, at most 300. Values pass the same allow-list as the
  server: only rgb/rgba, hsl/hsla, gradients, clamp, calc, min, max, cubic-bezier and drop-shadow; ASCII only; no
  `url`; quotes only around font names.
- **Nothing per frame.** A style resolves in well under 1.5 ms. The page writes only the variables that changed and
  reads entrance timing once per scene.
- **Tokens** cover background, surfaces, text, colour roles (accent, secondary, info / success / error, callouts),
  tables, code and output panels, captions, labels, title bar, presenter frame, visual frame, chart colours,
  typography, spacing, shapes and motion.
- **Preferences** (enums): tone (dark / light); emphasis (glow / outline / soft, always shape plus colour); captions
  (shadow / box); presenter frame (none / clean / card / rounded) and name card; visual frame (panel / card / rounded /
  plain); background pattern (none / dots / grid / paper); motion (low / standard); camera (still / subtle); transition.
- **Fonts:** only what the page already loads (Inter, Outfit, JetBrains Mono) plus the system serif (Georgia).
- **Composition, not inheritance.** Each family is the base plus its own values. Versions sit side by side
  (`academic@1`). A lesson keeps its `style_version`; an unknown version resolves to the latest the server knows.
- **The four families:**

| Family | Look | Typography | Surfaces and frames | Motion / transition / camera |
|---|---|---|---|---|
| Academic | light: ivory paper, ink-navy text, crimson accent, faint rules | Georgia titles and headings, Inter body | white cards, thin rules, small radii, boxed captions, outline emphasis | low, fade, subtle |
| Cinematic Education (default) | dark: indigo and violet, gold accent (the existing look) | Outfit titles, capital headings, Inter light body | translucent violet card, gold edge, glow emphasis | standard, fade, subtle |
| Children's Education | light: cream and sky blue, orange accent, gentle dots | Outfit throughout, text ×1.06, more spacing | rounded cards, large radii, rounded presenter frame with name card, soft emphasis | standard, soft fade, subtle |
| Corporate Training | dark: crisp navy, sky-blue accent, faint grid | Inter throughout, tight tracking | flat navy cards, small radii, presenter card with name card, outline emphasis | low, crossfade, subtle |

- Code panels stay dark in every style (Prism's dark theme); code has its own accent per style, checked against the
  panel. The output card follows the style.
- **User choices (bounded).** Lesson: style, accent (gold, coral, crimson, teal, sky, violet, emerald; each with dark
  and light variants), text size, animation, background, caption size; advanced: code size, formula size, diagram
  frame. Scene: accent and background only. No raw colour, size, font, CSS or URL is accepted; unknown keys or values
  get 422 from the API and are dropped on save. Choices wait until a style is picked.
- **Precedence:** accessibility > the user's explicit choices > Phases 16 / 15 / 14 decisions > the style's
  preferences > decorative defaults.
- **Suggestions, not pre-fill.** Picking a style changes only the style. Its transition and camera preferences are
  offered ("This style suggests Soft fade transitions." — **Use the style's suggestions**). An automatic pre-fill was
  tried first; it re-opened every Visual Review approval and could turn a still camera into a moving one.
- **Accessibility.** Main text colours are checked against what they sit on (WCAG relative luminance, translucent
  surfaces composited): body text, labels, captions and similar ≥ 4.5:1; titles and headings ≥ 3:1; the accent's
  marks (bullets, ticks, step numbers) ≥ 3:1. A failing colour moves toward whichever end (black or white) can reach
  the ratio, and the adjustment is reported in Visual Review. Text sizes are bounded to 0.9–1.3×.
- On light styles, inline lesson colours that would not read are replaced by the style's text colour. Over a picture
  background, non-default styles put the title and label chips on the style's surface. Reduced motion uses plain
  fades, a still camera, and stops decorative loops (preview only).
- **What a style change invalidates:** only the style-derived rendering. The composition fingerprint does not include
  style colours or type, so Visual Review approvals, direction, synchronization timing, presenter plans, assets and AI
  media are untouched. Nothing is regenerated. Changing or undoing a scene's accent / background keeps its approval.
- **AI media.** Scene visual requests never include the style. An AI background (on request only) uses the family's
  words; Cinematic Education uses the old default words, so cached backgrounds are reused.
- **Old lessons.** No style → `cinematic_education@1` with `legacy: true`, whose tokens equal the page's old values.
  The plan hash and composition fingerprint are identical to before Phase 17.
- **Frames and charts.** The presenter frame and name card follow the style; Phase 12's presenter itself is never
  changed. The name card (Children's and Corporate only) shows the profile's name inside the presenter layer; never for
  Aadhi, never while the presenter is hidden or missing. Side picture and chart panels take the visual frame. Charts
  read the current scene's chart colours; pie, doughnut and polar charts keep their own colours.

### Key files
- `styles.py` — the registry: families, tokens, accents, overrides, `resolve`, `ensure_contrast`, the accessibility
  guard, `safe_css_value`, `fingerprint`, `plan_look`, `catalog`, `lesson_choice`, `background_words`.
- `cinematic.py` — the effective style in each plan, settings and scene-override validation, `looks` in the
  vocabulary, `POST /api/cinematic/style`, the background route's style words, new transitions, approval kept on a
  style-only review change.
- `server.py` — `cinematic_style` in `/save-history`.
- `cinematic.js` — `applyLook`, entrance timing and emphasis colour from tokens, the light-style contrast guard, the
  presenter name card, the style picker, suggestions and choices, `styleSummary`.
- `review.js` — the Style block and the scene accent / background selects.
- `index.html` — tokenized cinematic CSS, per-style board, captions and frames, reduced motion, saving and restoring
  the lesson's style, chart colours.
- Tests: `tests/test_styles.py`, `tests/style.test.js`, `tests/style_review.test.js`, `tests/style_wiring.test.js`,
  `tests/style_browser_check.mjs`.

### API and data
- `POST /api/cinematic/style` — body `{project_id, style, style_version, style_overrides}`. Stores the cleaned choice
  in the lesson as `payload.cinematic_style`, in place, with no new history entry. `style: null` returns to the
  original look. 404 for another user's lesson, 422 for unknown values. (Changed in Phase 20: it writes through
  `editor_api.update_lesson` and returns the lesson's `revision`.)
- `GET /api/cinematic` — the vocabulary now includes `looks`: the default family, the family catalog and the options.
- `/save-history` accepts `cinematic_style`, cleaned by `styles.lesson_choice` (unknown family or override dropped).
- Cinematic settings gain `style`, `style_version` and `style_overrides`.
- Override options (from the code): `accent`, `text_size` (standard / large / larger), `motion`, `background`
  (plain / subtle / rich), `caption_size`, `code_size`, `formula_size`, `diagram_frame`. Scene choices are stored as
  Visual Review composition overrides (`style_accent`, `style_background`).
- Plan: `scene.cinematic_plan.style.look` = `{schema, id, family, version, label, variant, tone, prefs, overrides,
  scene_overrides, adjustments, fingerprint, legacy, css}`.
- The fingerprint covers the schema, family, version, legacy variant and the lesson's and scene's overrides.
- No database change: the style lives in the lesson JSON and in each plan.

### Tests and results (at the phase's close)
- Silent regression, every suite on the final code.
- Backend 552/552 (+61 Phase 17). Frontend 211/211 (+31). Phase 17 browser check 21/21 (also 21/21 in two earlier
  runs). Phases 1–16 all pass; Phase 7 measured 28.4 fps effective, 0 stalls.
- Measured: a scene's style resolves in well under 1.5 ms (200 scenes in 46–84 ms). A style switch makes no
  generation request. Every `--st-*` token on the page matches `styles.py` in all four styles.
- All four styles were rendered and inspected at 1280×720; the first round was rejected and fixed. Cinematic
  Education matches a lesson saved before Phase 17.
- Lowest contrast per style (body text / titles): Academic 8.1 / 14.3, Cinematic Education 8.75 / 16.0,
  Children's Education 4.69 / 13.0, Corporate Training 7.74 / 13.0.
- Preview vs export: mean frame difference 1.0 (Academic), 1.2 (Corporate), 1.5 (Children's) on a 32×18 grid; no white
  or grey flash at scene changes.
- LLM validation: not applicable (Phase 17 makes no model call). No AI media was generated.

### Limitations and follow-ups
- 16:9 only; a 9:16 or 1:1 layout would need new frame geometry, not a style.
- Cinematic mode only; Classic scenes keep their original look.
- Media is framed, never recoloured: library pictures, AI media, Manim renders, Aadhi's studio clips. The graph, 3D,
  GIF and clip side panels keep their dark look; code panels stay dark.
- On light styles, inline lesson colours are replaced when unreadable; inline backgrounds are left as written.
- No AI style assistance: the user picks among the four.
- The drawn teacher's colours, Aadhi's look and AI presenters never change with the style.
- The CSS relies on `:has()`, `max()` and clip-path view transitions (Chrome 111+).
- Phase 19's editor changes a scene's accent and background density; the lesson style stays in Phase 17's settings.
- A two-line caption could touch the presenter's name card (a Phase 20 known issue). Resolved in Phase 21: captions fit
  their band, and the name card steps aside while the player bar lifts the caption (preview only).
- Changed in Phase 21: the style panel moved to Settings → Lesson defaults (the Studio mounts it), the picker is one
  radio group, product UI colours never follow the lesson style, and Visual Review's layout preview is neutral.

---

## Phase 18 — AI Video Quality & Consistency Engine

**Status:** Complete (deterministic lesson-wide quality report, safe repairs and the Visual Review quality panel
verified in Chrome in all four styles, preview and export; optional AI terminology assistance verified with the
stand-in model only) · **Goal:** Each scene had its own validators, but nothing compared scenes across a lesson, and
there was no terminology registry, lesson-level evaluation or quality UI. Phase 18 evaluates the whole lesson against
the project's own contracts, explains inconsistencies in plain words, and repairs only what is safe, through the
existing systems.

### What was built
- `quality.py`, the Quality & Consistency Engine core: deterministic, read-only, versioned (`quality_rules@1`).
- Four rule families: `quality_style.py` (20 rules), `quality_media.py` (18), `quality_education.py` (18) and
  `quality_timing.py` (23), plus 2 core rules: 81 rules in all.
- A findings model (severity, evidence, repair), a lesson report with per-dimension and per-scene status, and a
  consistency registry derived from the lesson.
- Scene and lesson fingerprints, stale-report detection and incremental re-checking.
- `POST /api/quality/lesson`, which never saves, generates, approves or touches recovery.
- A Quality panel in Visual Review, per-scene chips, inspector blocks and a "Quality" filter.
- "Fix automatically" (approval-safe re-plans) and "Apply suggestion" (through Visual Review's composition review).
- A quality check before every export, and optional AI help to group ambiguous terms (on request only).

### How it works
- **One source of truth per concern.** Each check compares the stored result with what its own system says now:
  styles and contrast from `styles.py`; `cinematic.validate_plan`; `composer.text_need`; `sync_director`'s
  `narration_model` and `validate`; `visual_director.understand`; `scene_intent.board_facts`; `presenters.py`.
- **Inputs.** The lesson as planned, its settings and concept map. On the server it also reads media metadata (one
  asset query, one runs query, plain file checks; nothing is marked missing) and the plans the current planning gives
  now (`compose_lesson` with `ai: cached_only`, so no model call).
- **A finding** has a stable `id` (rule + scene + element + evidence key), a `rule` (`<family>.<what>`), a
  `dimension`, a `severity`, a `scene` (0-based in data, 1-based in words), an `element`, a plain-words `message`,
  small structured `evidence`, a `repair` (kind, class, action, label), a `repair_status` and the scene `fingerprint`.
- **Dimensions** (16): presenter, style, typography, colour, terminology, education, formulas, code, diagrams, camera,
  motion, background, density, timing, media, preview_export.
- **Severities:** *info* (intentional or harmless, e.g. a scene accent chosen in Visual Review; never a problem);
  *notice* (worth a look); *warning* (readability or consistency); *error* (a contract violation: a stale plan, a
  missing background, a moving camera without motion); *blocking* (a shown visual whose file is gone).
- **Report status:** blocking → `blocked`, error → `attention`, notice or warning → `review`, else `good`.
- **The registry** records the style (id, family, version, tone, scene accents), captions, backgrounds, the presenter
  contract, media sources, terminology groups with every written form and their scenes, concepts, abbreviations,
  formula symbols, code languages, diagram labels, camera moves, transitions and pacing.
- **Fingerprints.** A scene fingerprint covers the whole planned scene, its position, the scene count and every lesson
  setting. Numbers are normalised (1.0 from the browser equals 1) and volatile parts are left out (review timestamps,
  AI records, notes, signed links). The lesson fingerprint adds the rules version, settings, concept map, media digest
  and whether plans were recomputed. A report with different rules or fingerprint is stale (`is_stale`); the page
  shows "The lesson changed since this check".
- **Incremental.** A scene's findings are reused from an in-process, thread-safe memo while its fingerprint and own
  media are unchanged. A result whose checks failed is never kept. Cross-scene checks always run.
- **What the families check:**
  - *Style*: saved looks against what Phase 17 resolves now (stale, tampered, mixed styles); readability via Phase 17's
    guard; plan validity; text fit (under 80 % at the style's size); crowded boards; title casing; backgrounds that
    fell back; three or more scene accents (notice).
  - *Presenter and media*: unexplained presenter switches (error); intentional Visual Review choices (info); side
    flips and size jumps; Aadhi off his studio; missing files (blocking), kind / aspect / provenance problems, failed
    runs. A removed visual is never reported; nothing suggests regenerating.
  - *Education*: terminology from the lesson's emphasised words (casing, hyphen / space variants, abbreviations never or
    doubly spelled out, a concept named differently); formulas in every MathJax form, including single `$…$` (a symbol
    with two meanings, unexplained symbols); code (an uncoloured language, mixed tabs, lines over 70 characters,
    blocks over 18 lines); diagram label casing. Content findings never repair anything.
  - *Timing*: Phase 16 / 13 plans checked, never re-timed (invalid sync, moments after the scene's end, missing
    targets, moving camera without motion); captions too long for two lines; too much to read; repetitive layouts and
    moves; overloaded or near-empty scenes.
  - *Core preview / export*: a cinematic scene without a plan, or a stored plan whose layout or moments differ from
    what the current planning gives. It compares layout and moments, never hashes that carry style colours.
- **One cause, one finding.** Findings fixed by the same re-plan or suggestion fold into the most important one (a
  stale plan first); the others are named in its evidence.
- **Repairs (detect → suggest → repair).** Each repair is classed: presentation, timing, composition, visual,
  presenter or content.
  - Automatic only for a re-plan of derived plans that keeps every approval. The page asks the existing planning for
    fresh plans and applies them only to the scenes the finding names.
  - A re-plan that would re-open an approved scene is offered as a suggestion that says so.
  - Suggestions go through Visual Review's composition review (scene accent / background, or a choice such as a
    smaller presenter); they become the user's choice.
  - Never repaired: wording, terminology, formulas, code, media, presenter identity. Nothing writes a review record
    behind the user's back.
- **The page.** The panel headline shows good, things to review, things needing attention or "✕ The lesson cannot be
  exported as reviewed". Findings are grouped by scene with an icon and words for severity (never colour alone), plus
  "Show scene", "Check again" and a fix button only where one exists. Rule ids, evidence and fingerprints show only
  with `?visualDebug`. Quality never changes an approval.
- **Before export** the report runs once more. Up to 12 warnings and errors appear under "The quality check found
  things to review". The export still goes on; it has always warned rather than blocked.
- **AI assistance (optional).** Only for ambiguous term pairs (an abbreviation and the term it may stand for; two
  names for one concept). It needs AI-assisted mode (`director: "ai"`) and `assist: true` in the request. One call
  plus at most one repair within 20 s, at most 8 pairs, true / false answers only. Results are notices "suggested by
  the assistant". Ordinary reports and the pre-export check never call a model.

### Key files
- `quality.py` — the core: lesson context, findings, severities, dimensions, fingerprints, memo, folding, the report,
  the two preview / export rules, and the router.
- `quality_style.py` — style, text, colour and background rules.
- `quality_media.py` — presenter and media rules; `load_media` reads media metadata without marking anything.
- `quality_education.py` — terminology, concepts, formulas, code and diagram rules; the optional assistant.
- `quality_timing.py` — timing, camera, motion and density rules.
- `server.py` — mounts the quality router. `cinematic.js` — `CinematicApi.quality`.
- `review.js` — the Quality panel, scene chips and blocks, the Quality filter, "Show scene"; a presenter decision now
  re-plans the composition.
- `index.html` — the review adapter (report, re-check, stale key, repairs, suggestions) and the pre-export check.
- `export.js` — quality findings under their own heading; a user's Cancel is not logged as an error.
- `scene_intent.py` — its formula pattern bounded.
- Tests: `tests/test_quality.py`, `tests/test_quality_style.py`, `tests/test_quality_media.py`,
  `tests/test_quality_education.py`, `tests/test_quality_timing.py`, `tests/test_quality_integration.py`,
  `tests/quality_review.test.js`, `tests/quality_wiring.test.js`, `tests/quality_browser_check.mjs`.

### API and data
- `POST /api/quality/lesson` — body: `scenes`, `settings`, `project_id`, `concept_map`, `recompose` (default true),
  `assist` (default false). Returns the report. 413 above 200 scenes or 3,000,000 characters of HTML and narration;
  404 for another user's lesson.
- `GET /api/quality` (from the code) — the contract: rules version, severities, dimensions, repair classes.
- Report fields: `rules`, `version`, `fingerprint`, `status`, `summary`, `dimensions`, `scenes`, `issues` (most serious
  first), `registry`, `limitations`, and the families that ran.
- Bounds: texts read per check (titles 300, narration 12,000, HTML 30,000 characters); the scenes' own checks stop
  at 20 s and the rest is reported as not checked.
- No table, no lesson field, no history entry, no database change: the report is derived on demand.

### Tests and results (at the phase's close)
- Silent regression. Backend 760/760 (+208 Phase 18). Frontend 239/239 (+28). Phase 18 quality check 22/22 (full
  run and alone). All other suites pass except as noted; Phase 7 measured 28.4 fps effective, 0 stalls.
- Phase 3 was 25/26: 198 test uploads pushed the 13 shared Aadhi clips past the library's first page (200 items).
  Phase 18 does not touch the library. (Resolved in Phase 21: 27/27.)
- Phase 9 was 13/14 in the full run (a WebSocket error during the check's own restart) and 14/14 alone.
- Measured: 40–60 ms per 8–11-scene report on the server (120–150 ms via HTTP), 32 scenes in about 110 ms, 60 scenes
  through the API in about 0.5 s. Editing one scene re-checks only that scene.
- The representative lesson in all four styles: "✓ Quality: good" in every style (one info finding).
- A crafted problem lesson listed exactly its six problems (eleven findings before folding).
- Preview vs export: mean frame difference 1.1 of 255.
- LLM validation: not done for real; the stand-in model and the "no model" path only.

### Limitations and follow-ups
- Not read: the content of pictures, clips and AI media (metadata only), mathematical correctness, whether code runs,
  and the real voice length (durations are estimates).
- Terminology is pattern-based; different names for one thing are found only by the optional assistant. Common
  symbols (x, y) can draw an "unexplained symbol" notice.
- Text fit uses the composer's estimate, not a browser measurement.
- No screenplay field names a presenter. AI runs are matched by stored scene number, which can drift after moves.
- The report is not stored; the page compares a kept report by a key, not the server's fingerprint.
- Export findings are shown, not enforced. The endpoint has no rate limit; a large lesson can take seconds.
- 16:9 only. Classic gets only the checks that apply to it; composition, style and preview / export need cinematic.
- In the preview only, the player's progress line crossed the captions (also in Phase 17). Resolved in Phase 21.
- Later phases: Phase 19 shows findings per scene in the editor, records a hidden scene's findings as info and adds
  `timing.hold_shorter`. Phase 20 records the check as the Studio's "quality" checkpoint. Phase 21 reworded the
  headline ("✓ Quality looks good" / "⚠ N things to review"); the engine was untouched.

## Phase 19 — Advanced Video Editor

**Status:** Complete (scene list, timeline, inspector and undo / redo around the live stage; edits saved in place with
revisions and conflict recovery; verified in Chrome at 1280×720 on a real lesson: edited, saved, reloaded, previewed and
exported, preview and export matching; nothing generated by the editor) · **Goal:** An educator needed to refine an
already generated lesson by hand: order, timing, narration, presenter, visual, text, background, transitions, camera,
captions, visibility and structure. The audit found that scenes had no identity, nothing updated a lesson in place, and
there was no stale-save protection, no duration control and no undo. The phase built an editing layer over the existing
lesson, with no second renderer, planner, synchronization, style, quality, review, asset or export system.

### What was built
- **Scene identity:** every scene has a `scene_id` (`s-` plus 12 hex characters).
- **Editing model** (`editor.js`, `window.AadhiEditor`): an `EditorModel` over the page's own `slides`, edited in place.
  Commands are plain JSON `{type, scene_id, before, after}`, with undo / redo, transactions, replay by scene id,
  `Autosave` and `Draft`.
- **Workspace** (`editor_ui.js`, `AadhiEditorUI.EditorWorkspace`) around the live stage: a top bar, a scene list, a
  timeline and an inspector. It opens from the 🎬 Editor button.
- **Save route** (`editor_api.py`): `GET` / `PUT /api/editor/{id}` and `GET /api/editor/{id}/status`.
- **Edits:** reorder (drag, buttons, keys), duplicate, delete, insert (blank, library picture, copy), split at pause
  markers, hide, minimum duration, narration, mute, captions, title / subtitle / labels, presenter, visual, camera,
  transition, background, scene accent and background density.
- **Playback support** in `index.html`: minimum duration, hidden scenes, muted narration, caption visibility and a
  still preview while paused.
- **Quality per scene:** Phase 18 findings follow a scene by its id and are re-checked only where something changed.

### How it works
- **Editor data on each scene (`scene.edit`):** `hidden`; `min_seconds` (hold at least; up to 600 s, the inspector takes
  0.5–600 s); `captions: "off"`; `narration_muted`; `origin` (inserted / duplicated / split) and `from`; `original`.
  `original` keeps the generated value of each source field the editor changed (title, subtitle, narration, html,
  labels), so the UI can show "Generated" vs "Edited" and Revert is exact. No `scene.edit` means a generated scene.
- **Lesson-level data (`payload.editor`):** `version`, `captions.visible` and `generated_order` (to show moved scenes).
  Every save keeps it.
- **Old lessons:** they get ids when opened, derived from the lesson and the position (the same on every load). The
  editor stores them at once, before any edit. Duplicated, inserted and split scenes get new ids.
- **Where each edit goes (only what the architecture already honours):**
  - Order, duplicate, delete, insert, hide and split change the scenes array and `scene.edit`.
  - Duration sets `min_seconds`. Narration is never sped up. The plan carries it as presentation timing, outside every
    approval fingerprint.
  - Narration edits the scene's text; Phase 16 retimes it through the planner.
  - Mute and captions go to `scene.edit` / `payload.editor.captions`.
  - Camera, transition, presenter size / side / hidden, visual size / side, background kind, scene accent and
    background density go through Visual Review's composition overrides, as the user's choice with unchanged approval
    rules.
  - The visual itself goes through Visual Review's own choose / remove.
  - Title, subtitle and labels edit the scene's fields; as for any content change, the composition approval reopens.
  - The lesson's style stays in Phase 17's settings. The board's HTML is kept for Revert but has no inspector field.
- **Splitting** happens only at the narration's own `[PAUSE]` markers. Both parts share the board; the second gets a new
  id. Without pause markers, splitting is not offered (no model cuts text).
- **Insertion:** a blank scene, a picture from the Asset Library picker, or a duplicate. Nothing is generated.
- **Undo / redo:** a drag or a typing visit is one step; 200 steps are kept. Undoing a layout choice sends the previous
  value through the same route and gives back an approval the user had given to that layout.
- **Saving in place:** `PUT /api/editor/{id}` writes the same lesson row, 1.5 s after the last edit, one save at a time.
  A drag saves when it ends. "Save version" is the existing `/save-history`, which creates a history entry on purpose.
- **Revisions:** the token is the lesson row's `updated_at`, which every in-place writer changes. The write is a
  compare-and-set. A stale save reloads the newer lesson and replays the unsaved commands by scene id. An edit whose
  scene is gone is reported, never silently dropped.
- **Structure during generation:** AI results are attached by scene position. While a lesson has AI runs in progress,
  moving, adding, removing or splitting scenes is refused (409). The editor polls the status and says why. Property
  edits are allowed.
- **Visual Review writes by position:** the editor saves its own edits first and sends nothing if they could not be
  saved. The review and regenerate routes (composition, direction, visuals, presenter) refuse (409) a scene whose id
  differs from the one saved at that position (`same_scene`), and return the lesson's revision. Replies are applied by
  scene id; a plan made for an older order is discarded and asked again.
- **Drafts:** unsaved edits are kept in this browser under `aadhi.editor.draft.<id>` and offered back when the editor
  opens. They replay by scene id; what cannot be applied is reported. Layout choices are not replayed (Visual Review
  already saved them).
- **Validation (nothing from the client is trusted):** ownership (404); at most 200 scenes; valid unique ids; the editor
  data contract (unknown keys dropped, types and ranges enforced; 422); bounded texts (title and subtitle 2,000,
  narration 50,000, board HTML 300,000 characters; 422); no NaN / Infinity (422); scenes at most 8 million characters
  (413); every library asset referenced, in any field or form, usable by the user (422).
- **Safety rules:** a lesson keeps at least one visible scene. After a save the asset references are recorded again, so
  an asset still in use cannot be deleted. Deleting a scene never deletes an asset, a cache entry or a file.
- **Playback (one renderer, the live page):**
  - A scene holds at least `min_seconds` (timed excluding pauses). A longer narration is never cut or sped up. A silent
    or muted scene lasts at least 5 s.
  - Hidden scenes are skipped by auto-advance, previous / next, the scrubber, progress, export preparation, chapters and
    the lesson-wide AI generation. The editor's own ←/→ and ⏮/⏭ still visit them.
  - Muted narration makes no TTS request and shows empty captions.
  - Captions off (per lesson or per scene) hide the caption line and leave it out of the export's subtitles.
  - The editor never starts playback on its own. While paused, a chosen scene is shown still.
  - The preview is the live stage and the export records the same page, so what the editor shows is what is exported.
- **Quality:** a property edit changes only that scene's quality fingerprint. A fingerprint includes the scene's place
  and the scene count, so a structural edit re-checks the scenes whose place changed. A hold shorter than the narration
  is a notice (`timing.hold_shorter`); a hidden scene's findings are info.
- **Approvals:** presentation edits (duration, captions, mute, visibility) never touch an approval. A scene accent or
  background density never withdraws one (Phase 17's rule). A duplicated scene starts with no approvals. Moving scenes
  recomposes their neighbours, whose approvals follow their own fingerprints.
- **Workspace details:** the top bar shows undo / redo, the save state (Saved / Saving… / Unsaved changes / Couldn't
  save, with Retry), quality, Preview (hides the panels), Save version and help. The timeline has scene blocks sized by
  play time, narration / presenter / visual / caption tracks, transition markers, striped hidden scenes, a snapping
  playhead and zoom. The inspector shows "Generated" vs "Edited" with Revert; ids and fingerprints only with
  `?visualDebug`. Shortcuts work only while the editor is open and never while typing (Space, Ctrl/Cmd+Z, Ctrl+Y,
  Delete, ←/→, Alt+←/→, Esc, ?). On tablets the inspector is a bottom sheet; at ≤ 700 px there is no stage preview.

### Key files
- `editor_api.py` — scene ids, the editor data contract, the `/api/editor` router, and the identity check
  (`same_scene`) used by the review routes.
- `editor.js` — `AadhiEditor`: the editing model, undo / redo, Autosave and Draft.
- `editor_ui.js` — `AadhiEditorUI.EditorWorkspace`: top bar, scene list, timeline, inspector.
- `editor.css` — the workspace styles.
- `server.py` — mounts the editor router and static files; `/save-history` keeps editor data and refuses NaN /
  Infinity; the lesson-wide AI generation skips hidden scenes.
- `cinematic.py` — the minimum duration in the plan as presentation timing; identity check and revision on the review
  and regenerate routes.
- `visuals.py`, `presenters.py` — the same identity check and revision on their routes.
- `quality.py`, `quality_timing.py` — hidden scenes' findings as info; timing follows the minimum duration and the
  played order; `timing.hold_shorter`.
- `index.html` — the 🎬 Editor button, the editor's adapter over existing systems, and the playback changes.
- Tests: `tests/test_editor_api.py`, `tests/test_editor_playback.py`, `tests/editor.test.js`, `tests/editor_ui.test.js`,
  `tests/playback_edit.test.js`, `tests/editor_wiring.test.js`, `tests/editor_browser_check.mjs`.

### API and data
- `GET /api/editor/{project_id}` — the scenes with ids, the revision and whether generation is in progress.
- `GET /api/editor/{project_id}/status` — the status the editor polls while structural edits wait.
- `PUT /api/editor/{project_id}` — in-place save with a revision check (404, 409, 413, 422 as above).
- Review and regenerate routes (composition, direction, visuals, presenter): 409 on an identity mismatch; they return
  the revision.
- Data: `scene.scene_id`, `scene.edit` and `payload.editor`, all inside the lesson's JSON. Label objects `{text, at}`
  are accepted. Browser storage key `aadhi.editor.draft.<id>`.
- No database schema change.

### Tests and results (at the phase's close)
- Backend (unittest) **803/803** (760 before + 43). Frontend (node --test) **342/342** (239 before + 103).
- **Phase 19 editor check 52/52** on the final code, run alone (44/44 in the full run, before 8 in-page checks for
  narration, split, mute, labels and background density were added).
- Phases 1–18 browser checks all passed as before, except Phase 3 at 25/26: the shared Aadhi clips fall past the
  library's first page (200 items) because the development database holds many test uploads.
- Phase 7: 18/18 (28.6 fps effective, 0 stalls, page 129.2 fps). Phase 8: 12/12 re-run alone; 6/7 in the full run
  (page load timeout under load, a timing weakness of the check; no provider assertion failed).
- Visual validation: a nine-scene lesson edited in real Chrome at 1280×720, saved, reloaded, previewed and exported.
  Export frames matched the preview (mean difference 1.1, 1.7 and 1.8 of 255). Quality after the edits: "✓ Good"
  with four info findings.
- LLM validation: not applicable; the editor makes no model call.
- Notable defects fixed: a paused editor started playback on scene choice; a new scene lost its selection after
  Duplicate / Insert; a failed editor save let a Visual Review camera change overwrite another scene (now 409 by
  identity); replies applied by stale positions; lost updates; hardening (huge integers, NaN, no size limit, asset
  references in other forms, random ids for old lessons, deleting the last visible scene).

### Limitations and follow-ups
- **The preview is the live stage**, so the panels cover its edges while editing; Preview hides them. Phase 21 added a
  note at the stage's edge (≤ 1280 px); fitting the stage between the panels is still deferred.
- **Splitting** is offered only at pause markers; both parts share the board.
- **Structural edits wait** while AI runs are in progress.
- **Structural edits re-check later scenes** in quality (tens of milliseconds per report).
- **Trust:** `/save-history` and the review routes kept their earlier trust. Phase 20 routed every in-place writer
  through `update_lesson` and made reviews merge onto the saved scene (`reviewed_scene`).
- **Drafts stayed in the browser after logout.** Phase 21 clears them on sign-out.
- "Show in Visual Review" opens the lesson, not the selected scene. An approval given back on undo is remembered for
  the session only.
- No real-time collaboration, no frame-accurate trimming, no audio editing beyond muting, 16:9 only.
- Phase 20 opened the editor from the Studio. Phase 21 renamed "Save version" to "Save as a copy" and added the
  contextual inspector and four width layouts.

## Phase 20 — End-to-End AI Educational Video Studio

**Status:** Complete (one Studio workflow over Phases 1–19: document or pasted content → a durable, source-traced lesson
run → media → presenter → style → editor → quality → Visual Review → preview → export → reopen; two lessons rendered and
inspected in Chrome, preview and export matching; user edits protected from background writes; stand-in providers only,
no real provider keys on this machine) · **Goal:** Phases 1–19 worked as separate tools with no workflow state. The
screenplay was streamed by the page (lost if the tab closed), media generation lived in three places, and background
writers could overwrite an editor save. The phase joined everything into one workflow from source material to exported
video, adding only orchestration code (no second parser, router, cache, provider layer, job system or exporter).

### What was built
- **The Studio** (`studio.js`, `studio.css`, `window.AadhiStudio`): an overlay with Home, a writing-progress view and a
  lesson rail of seven stages. Entry: "🧭 Studio" on the start screen and the player bar.
- **A durable lesson-writing run** (`studio.py`): an `AIGenerationRun` of kind `lesson_script` with lease, heartbeat,
  cancel and restart recovery.
- **The screenplay contract, stand-in writer and traceability** (`studio_screenplay.py`).
- **A derived lesson state and user checkpoints** (`studio.py`), never a second state machine.
- **Override-safe writes** (`editor_api.update_lesson`, `reviewed_scene`, `next_stamp`; `visuals.scene_for_run`).
- **One save path** (`server.save_lesson`, the `/save-history` body).
- **Export lesson link and history filter** (`exports.py`, `export_outputs.py`, `export.js`).
- **Per-scene media attention items:** Retry · Choose existing · Continue without.
- Two source fixtures (`tests/fixtures/studio/photosynthesis.txt`, `newtons_second_law.txt`).

### How it works
- **Home:** create a lesson *From a document* (the Phase 11 Document Assistant, then "Use for Lesson Generation" and a
  "Name your lesson" step) or *From structured content* (pasted text, or a `.json` lesson file saved as its own
  lesson). "Your lessons" lists each lesson with its stage and Open / Continue.
- **Writing progress:** only the run's real stages (Understanding the source, Writing the lesson, Checking the lesson,
  Saving the lesson), with Cancel. On failure: "We couldn't write the lesson from this content. Your source is safe."
  with Try again. The finished lesson opens without autoplay; the URL keeps `?project_id=`.
- **The seven stages:** 1 Content · 2 Lesson · 3 Visuals & presenter · 4 Style · 5 Review & edit · 6 Preview · 7 Export.
  Each shows ✓ Done · ● In progress · ○ Not yet · ⚠ Needs attention, one truthful line and its actions. No provider,
  model, run id or fingerprint is shown unless `?visualDebug`.
- **Stepping aside:** the editor, preview and export need the live stage, so the Studio closes while they are open. The
  editor hands back on close. Visual Review opens above the Studio, which refreshes when it closes.
- **The writing run:** `POST /api/studio/lessons` takes the page's own prompt (`getSystemPrompt`), `text` or a prepared
  `source`, names, provider, model and style. It is modelled on Phase 11's analysis run; `ai_recovery` continues it
  after a restart, and a run that already saved its lesson finishes without asking the model again. An identical
  request joins the running one.
- **Pipeline:** the model (`source_documents.call_model`, now with a 600 s timeout and 65,536 output tokens for Gemini)
  → `check_screenplay` → `trace` → `save_lesson`.
- **Capacity:** writing runs in its own thread pool (3 threads, `LESSON_WRITER_THREADS`), never the shared one. A user
  can have two lessons being written at once; a third is refused (429). An unavailable writer is a plain 503. Source
  text is cut at 262,144 characters. Cancel stops at once; an in-flight answer is discarded.
- **`check_screenplay`:** accepts an object with scenes or a bare list, 1–200 scenes, at most 8 million characters. It
  repairs rather than refuses: unknown scene types and unaskable quizzes become content scenes, application-owned keys
  are removed, texts are bounded, NaN is refused. A malformed answer gets one bounded repair request.
- **The stand-in writer (test servers only, `AI_FAKE_PROVIDER=1`):** a deterministic lesson built only from the source
  text (sections, definitions, steps, formulas, code, tables, a "Diagram:" picture, a quiz from Question/Answer
  material, a presenter-led explanation, a summary, `[SYNC]` / `[PAUSE]` narration). It adds no facts.
- **Traceability:** `trace` marks each scene `scene.source = {origin: "source" | "ai", refs, coverage}` by deterministic
  word overlap with the text the writer received; it works for real models too. "Edited by you" comes from Phase 19's
  `scene.edit.original`. The lesson keeps `source_document` and `payload.studio.generated` (run, provider, model, time).
- **Derived state (`GET /api/studio/lessons/{id}`):** names, revision, a `fingerprint` (sha256 of what the video shows:
  scenes, editor data, style, with volatile parts such as `url` keys, link tokens, times and AI records removed),
  source, origins, media counts with per-scene items, Visual Review counts, style, editor counts, checkpoints, quality
  (with `stale`) and exports (whether the newest finished video matches the current fingerprint).
- **Stage order:** draft (no visible scenes) · exporting · generating (a media run is active) · needs attention (a run
  needs a person, a failed visual has nothing in its place, or a current quality check is blocked) · completed (the
  newest video matches) · ready to export (a current quality check, good or with findings; findings never block export)
  · review.
- **The lesson list:** at most 50 lessons and 10 being written, plus whether a writer is set up. Summaries are cached
  by revision and run / export state (about 2–6 s down to under 0.1 s for 50 large lessons).
- **Checkpoints:** `PUT …/checkpoint` records the user's decisions (structure, lesson, visuals, quality, export; `null`
  removes one) in `payload.studio.checkpoints` with the fingerprint they were made on. The page sends the fingerprint it
  read before the quality check, so a lesson changed meanwhile is recorded as already changed (compare-and-set).
- **User edits always win (`update_lesson`):** every server-side in-place writer (background visuals, presenter clips,
  backgrounds, Visual Review, presenter, composition and direction reviews, regenerate, the style route) re-applies its
  change to the freshly loaded lesson and writes only if `updated_at` is unchanged. It retries, gives up after four lost
  races (409), refuses NaN / Infinity (422), and writes nothing for a no-op. `next_stamp` keeps revisions moving
  forward. The page's older save paths ("Save version", the raw script editor, the save before exporting an unsaved
  lesson) still create a history entry each.
- **Review merge (`reviewed_scene`):** the saved scene is the base. The page's copy contributes only the review's own
  records per slot and media made in the preview. A plan showing nothing never replaces one that shows something.
- **Background results by id (`scene_for_run`):** the lesson batch, AI videos and Manim renders record `scene_id`. A
  result lands only on its scene and only in a still-empty slot.
- **Precedence:** the user's explicit choice → approved changes → the stored plan → AI suggestions → defaults.
- **Media:** "Prepare visuals" plans with the Visual Router (library first, AI cache, Visual Review decisions, Manim
  renders kept) and starts the existing lesson batch for what is missing. Batches continue with the page closed and
  after a restart. Continue without is Visual Review's removal, so the visual is not made again. A failed presenter
  clip or background is listed but not counted. Style, camera, layout, captions, quality and reopening never generate
  media.
- **Presenter and style:** the Phase 12 and Phase 17 panels are moved into the Studio's stages and put back afterwards.
- **Editor and quality:** the editor opens paused on the current scene. The Phase 18 result is recorded as the
  "quality" checkpoint with the fingerprint.
- **Export:** the recording's timeline carries `{project_id, fingerprint, revision}`. The history opens on "This lesson"
  and says "Matches the current lesson" or "Made before your latest changes".
- **Reopen:** restores scenes, editor data, style, source link and checkpoints; state is derived again from the server.
- **Security:** ownership checks (404) on every new route; bounds (prompt 60,000, text 262,144 characters, request body
  read in pieces and refused past 1.5 MB, unstorable characters 422). A stand-in run left in a database is failed by a
  server without `AI_FAKE_PROVIDER=1`. Recovery never writes a second lesson. Per-scene generation endpoints refuse
  another user's `project_id`. The Studio renders text only, no server HTML.

### Key files
- `studio.py` — `LessonScriptService` (run, pool, recovery), `lesson_fingerprint`, `media_state`, `derive_stage`,
  `lesson_state`, checkpoints, and the Studio router.
- `studio_screenplay.py` — `check_screenplay`, the stand-in writer and `trace`.
- `studio.js`, `studio.css` — the Studio UI.
- `editor_api.py` — `update_lesson`, `reviewed_scene`, `next_stamp`, `LessonChanged`.
- `server.py` — `save_lesson`, the Studio router and run kind, the Visual Router planner for the Studio, `project_id`
  ownership and `scene_id` on per-scene generation endpoints and the lesson batch, static routes.
- `visuals.py`, `presenters.py`, `cinematic.py` — writers through `update_lesson`; scenes found by id
  (`visuals.scene_for_run`); the style route returns the revision.
- `exports.py`, `export_outputs.py`, `export.js` — the export's lesson link and the history filter.
- `ai_providers.py`, `ai_media.py`, `manim_jobs.py` — `scene_id` carried in run and render requests.
- `source_documents.py` — `call_model`'s output room and timeout.
- `visuals.js` (`sceneId` on per-scene videos), `cinematic.js` (wide formulas and tables fitted).
- `index.html` — Studio entries and adapter, new-lesson state reset, source and Studio record kept by every save.
- Tests: `tests/test_studio.py`, `tests/test_studio_screenplay.py`, `tests/test_lesson_writes.py`,
  `tests/test_export_lesson_link.py`, `tests/studio.test.js`, `tests/studio_wiring.test.js`,
  `tests/export_history.test.js`, `tests/studio_browser_check.mjs`.

### API and data
- `POST /api/studio/lessons` — start (or join) a lesson-writing run.
- `GET /api/studio/runs/{run_id}`, `POST /api/studio/runs/{run_id}/cancel` — follow and cancel a run (owner only).
- `GET /api/studio/lessons` — the user's lessons with stages, lessons being written, and the writer flag.
- `GET /api/studio/lessons/{project_id}` — the derived lesson state.
- `PUT /api/studio/lessons/{project_id}/checkpoint` — record a decision with its fingerprint.
- Run kind `lesson_script`. Data in the lesson's JSON: `scene.source`, `payload.studio` (`generated`, `checkpoints`),
  `source_document`, `payload.editor.generated_order`. The export timeline carries the lesson link.
- Settings: `LESSON_WRITER_THREADS` (default 3). Test servers use `AI_FAKE_PROVIDER=1` and `FAKE_TTS=1`.
- No database schema change.

### Tests and results (at the phase's close)
- Backend **879/879** (803 before + 76). Frontend **394/394** (342 before + 52).
- **Phase 20 Studio check 69/69** (about 33 minutes): two lessons through the real Studio, plus a partial-failure
  scenario (failed pictures listed; Retry, Continue without and Choose existing each resolve one).
- Phases 1–19 all passed (Phase 19 52/52), except the known Phase 3 25/26. Phase 7: 18/18 (28.4 fps effective,
  0 stalls, page 124.9 fps). Everything ran on the final code.
- Visual validation: two 12-scene lessons (photosynthesis; Newton's second law) written, edited, styled, previewed and
  exported at 1280×720, inspected over three rounds. Export frames matched the preview (mean difference 1.8–3.0 of 255
  for lesson 1, 2.8–4.5 for lesson 2). Both videos are VP9 1280×720 with Opus audio.
- LLM validation: not done for real. The real-provider path of the lesson writer has no test.
- Notable defects fixed: an older Visual Review decision reverting a newer edit; no limit on concurrent writing; a
  chunked request bypassing the body limit; a stand-in run completable by production; recovery writing a second lesson;
  slow home list; a cut-off formula and table; an overflowing skill tree; a cut-off quiz explanation; a caption cut at
  a decimal point; a stale first subtitle; a replayed lesson no longer matching its export.

### Limitations and follow-ups
- **No real provider was used.** The lessons' wording in the rendered videos is the stand-in's.
- **The UI was functional, not refined:** the Studio was an overlay, the classic start screen remained, and Visual
  Review showed a Phase 8 "AI provider" line. Phase 21 refined the UI and made the provider line debug-only.
- **Two-line captions could touch the presenter's name card.** Phase 21 resolved this with the caption fit.
- **The export's opening chapter was always "Introduction"**, duplicating a scene of that name. Phase 21 resolved it.
- **Phase 3 25/26** (shared clips past the Library's first page). Phase 21 resolved it (27/27).
- Voice, presenter timings, composition planning and recording still need the page open.
- Pictures the page makes while preparing a scene are cached but not linked by scene id; presenter clips attach by
  position.
- The Document Assistant does not list `$$…$$` display equations as formulas.
- Two tabs are kept safe by revisions and the review merge, not merged live; on the same slot, the later decision wins.

## Phase 21 — UI/UX Refinement, Simplification & Product Polish

**Status:** Complete (one product: Home · Create · Library · Videos · Settings; one design system (`ui.css`); plain
words with technical details only behind `?visualDebug`; empty, loading, success and error states; keyboard, focus,
contrast and reduced motion; layouts for desktop, tablet and narrow screens; the Phase 20 known issues resolved and an
educator audit's findings mostly fixed; preview and export match, inspected in Chrome; stand-in providers only, no real
provider keys on this machine) · **Goal:** The Phase 20 product worked but did not feel like one application:
there was no way back Home from an open lesson, developer output was drawn on the stage and recorded into videos,
entry points were duplicated and there were no shared design tokens. The phase made it one finished application,
"Aadhi — Educational Video Studio", usable without knowing providers, caches, runs or fingerprints, with no new
generation system, engine, state machine or feature.

### What was built
- **App shell** (`app.js`, rewritten): the product top bar, the Settings dialog, notices, the More menu, the player bar
  shown on activity, and central focus management.
- **Navigation:** top bar Home · Create · Library · Videos · Settings; a rebuilt start screen; a lesson start card.
- **Design system:** `ui.css` (tokens and opt-in classes, loaded first) and `product.css` (shell styles, loaded last),
  adopted by the Studio, editor, Visual Review, Library, export panel, Document Assistant, start screen and dialogs.
- **Refined panels:** Studio, editor, Visual Review and quality, Library, Videos and the Document Assistant, all in
  plain words with empty, loading, success and error states.
- **Video-facing changes:** captions that fit their band (`stage-fit.css`, `captionFit` / `watchCaptions`), one plain
  missing-visual card (`visualNotice`), and the opening chapter naming rule.
- **Security and performance fixes**, and the removal of duplicate or dead UI.

### How it works
- **Top bar:** accessible names; hidden while an export records. Home stops a playing lesson, closes it and opens the
  Studio on "Your lessons". Create opens "How would you like to start?". Library and Videos open the existing panels.
- **Start screen:** "Create a lesson", "Open your lessons", a keyboard-accessible drop zone and "See an example lesson".
  A document goes through the Document Assistant into the Studio's naming step; a `.json` lesson file opens as a lesson.
  The classic one-step generation moved to Settings → Advanced.
- **Lesson start card** (reopened lesson): Open in Studio · Play the lesson · Export or see videos · Review the visuals
  · Back to Home.
- **Settings dialog:** the former start-screen controls, moved. Groups: General, Lesson defaults, Visuals & AI, Voice,
  Advanced. The raw lesson-file editor and the scene list appear only with `?visualDebug`. Tab stays inside.
- **Player bar:** every button named; a keyboard-accessible More menu. The four lesson tools (Review · Edit · Studio ·
  Export) show labels at 1280 px and up and at 641–1100 px, icons only otherwise. It shows while paused and, while
  playing, on activity; it hides 3 s later unless under the pointer, holding keyboard focus (`:focus-visible`) or with
  its menu open. While it shows, the caption is lifted 8 px above it and the presenter's name card steps aside. Preview
  only; the bar is hidden while an export records.
- **Removed or consolidated:** the View History modal, Restore Previous and the start-up restore code; one "Your videos"
  panel instead of four entries; the raw editor and Info modal moved to Advanced / debug.
- **Design tokens (`ui.css`):** `--ui-*` surfaces, text, gold primary, accent, status colours, border, focus, spacing
  4–32 px, radius 6–18 px and pill, three shadows, a type scale with a 12 px floor, control sizes and a z-index ladder.
  Opt-in `.ui-*` classes cover buttons, the focus ring and visually hidden text. Only `:root` and `.ui-*` selectors with
  literal values; a static test checks this and checks that `studio.css`, `editor.css` and `product.css` never use the
  lesson style's `--st-*` tokens. Product colours never follow the lesson style (measured identical across all four
  Phase 17 styles). Sentence case, visible focus, text ≥ 12 px, reduced motion for the product UI.
- **The Studio:** "Your lessons" rows with a chip in words (Being written, Ready to edit, Generating visuals, Needs
  attention, Ready for review, Ready to export, Exporting the video, Video ready). Create offers Upload a document ·
  Paste your notes · Open a lesson file. Writing shows the real stages, "You can safely leave this page" and an in-panel
  Stop confirmation. Stages show five states (✓ Complete · ● In progress · → Ready · ⚠ Needs attention · ○ Not started),
  "Next step" or "Still to do", and one primary action. A reopened lesson lands on its next useful stage. All seven fit
  at 1280×720; ≤ 900 px gives a compact stepper, ≤ 600 px a "Stage N of 7" select. Polling pauses while the tab is
  hidden; stored keys are per user (a hash of the user).
- **The editor:** four layouts by width (> 1100, 901–1100, 701–900 and ≤ 700 px), the transport row always visible, and
  "Show / Hide inspector". The contextual inspector (`inspectorLayout`) opens the scene's lead section right after
  "Scene" (Presenter, Visual or Text) and groups camera, transition, background and style under "Look and motion". It
  adds "Back to the Studio", a modal dialog with focus kept inside, and a danger confirmation for deletion. "Save
  version" became "Save as a copy".
- **Visual Review and quality:** an "Origin" line (Made with AI, From your library, From the shared Aadhi library, and
  so on) replaces the provider line, which appears only with `?visualDebug`. States and filters are in words;
  "Animation" / "Animated picture" replace "Manim" / "GIF search". The quality headline is "✓ Quality looks good" or
  "⚠ N things to review", with "Show scene" and "Check again". The Phase 18 engine is untouched.
- **Library:** shared and own assets are requested at the same time (`scope=system`, 50; `scope=mine`, 200). "Shared
  Aadhi assets" come first when browsing, the user's files first when picking. Narration clips are hidden by default.
  Asset ids, MIME types and hashes appear only in debug.
- **Videos panel:** "Your videos", with Download as the main action and "Export again" secondary; statuses in words.
- **Chapters (`export_outputs.build_chapters`):** the opening chapter is "Introduction" unless a scene already has that
  title (ignoring case and spacing); then "Opening", "Lesson start" or "Opening 2". Every scene chapter keeps its start.
- **Missing-visual card:** the stage used to draw error dumps, "[System Debug]" text and file paths, which were then
  recorded. It now shows one escaped card (title, one sentence, an optional action). Details appear only with
  `?visualDebug`, never while recording.
- **Caption fit:** the composition reserves the bottom 15 % for captions (108 px at 720p), but captions are sized in
  pixels. `captionFit` measures the caption before paint. Only if it would leave its band, it is lowered (down to an
  8 px / 1.5 % gutter) and, if still needed, its font is scaled (never below 85 %). Two inline variables
  (`--cine-caption-drop`, `--cine-caption-fit`) are read by `stage-fit.css`, cinematic scenes only. A caption that fits
  is drawn exactly as before; preview and export run the same code.
- **Focus model:** the shell restores focus centrally once the page behind a panel stops being inert, and finds the
  same control again if the panel redrew it. Panels ignore their keys while inert. Every panel moves focus in, keeps it
  inside, closes on Esc and returns focus to its opener.
- **Playback guards:** a paused lesson stays paused when its scene is redrawn (`redrawCurrentScene`); only the scene
  drawn last narrates; no scene transition runs while the Studio is open; particles are always made (hidden only while
  no export records) so reduced-motion users' videos keep them.
- **Security:** the Giphy key is gone (GIFs go through the server's `/get-gif`). Lesson p5 code runs in a sandboxed
  `srcdoc` frame (scripts only, no same-origin). Signing out clears the saved login, editor drafts, old shared Studio
  keys and the previous person's picture / presenter choices, then restarts the page; another person's sign-in does the
  same. The browser no longer keeps a lesson backup (it held signed links). Server and provider text is never rendered
  as HTML.
- **Performance:** the Visual Router plans behind the Studio's lesson state are cached by user, scenes, library and AI
  cache signatures and a 10-minute bucket (at most 64). pdf.js and mammoth load only for the document kind that needs
  them; the unused page-level p5 script is gone.

### Key files
- `ui.css` — the product design tokens (`--ui-*`) and opt-in `.ui-*` classes; loaded first.
- `product.css` — shell styles: top bar, start screen, Settings, start card, player bar, notices, dialogs, focus rings,
  reduced motion; loaded last.
- `stage-fit.css` — only the two caption-fit rules (`body[data-cinematic]`).
- `app.js` — the app shell (rewritten; it held the View History modal before).
- `index.html` — title, stylesheet order, top bar, start screen, start card, player bar, Home (`leaveLesson` /
  `goHome` / `openStudioHome`), `visualNotice`, the sandboxed p5 frame, sign-out clearing, `lessonDisplayName`.
- `studio.js`, `studio.css` — the refined Studio.
- `editor_ui.js`, `editor.css`, `editor.js` — the refined editor.
- `review.js`, `visuals.js` — Visual Review's origin line, words and focus; `originText`.
- `export.js`, `assets.js` — "Your videos" and the Library.
- `export_outputs.py` — the opening chapter rule.
- `cinematic.js` — `captionFit` / `watchCaptions`; plain-word settings panel; the style picker as one radio group.
- `presenter.js`, `sources.js`, `source_analysis.py` — escaped presenter markup; the Document Assistant for teachers;
  ordinary short words in formulas no longer taken as symbols.
- `server.py` — routes for the three new stylesheets; the Studio's planner cache.
- New tests: `tests/shell.test.js` (26), `tests/ui_tokens.test.js` (3), `tests/assets_panel.test.js` (11),
  `tests/export_panel.test.js` (7), `tests/phase21_browser_check.mjs` (81 checks).

### API and data
- No new API endpoint. Static routes for `ui.css`, `product.css` and `stage-fit.css`.
- The Library uses the existing `scope=system` / `scope=mine` filters; GIFs use the existing `/get-gif`.
- CSS variables `--cine-caption-drop` and `--cine-caption-fit` on `#subtitle-track`.
- Browser storage: the Studio's keys are per user (a hash of the user); drafts and old shared keys are cleared on
  sign-out; no lesson backup is kept.
- `?visualDebug` reveals technical details (providers, ids, raw errors, developer tools).
- No database schema change; the development database and its assets were kept.

### Tests and results (at the phase's close)
- Backend **887/887** (879 before + 8). Frontend **490/490** (394 before + 96).
- **Phase 21 check 81/81** (real Chrome, five viewports: 1440×900, 1280×720, 1024×768, 820×1180, 600×900; stand-ins
  only). It covers Home, Create and its errors, the stage rail, the editor, Visual Review, the four styles, captions at
  1280×720 and 1920×1080, the export's chapters, Settings, a keyboard-only path, focus, reduced motion and the Library.
- **Phases 1–20 all passing:** Phase 3 **27/27** (25/26 since Phase 18; one check added); Phase 8 15/15 and Phase 9
  15/15 (provider lines now checked in the debug view); Phase 10 18/18; Phase 12 22/22; Phase 13 25/25; Phase 19 52/52;
  Phase 20 69/69. Phase 7: 18/18 (28.5 fps effective, 0 stalls, page 127.8 fps). The others are unchanged.
- Run on the final code in three lanes. The visuals and AI cache checks timed out while two lanes ran together and
  passed when re-run alone. Earlier browser checks were adapted only where behaviour deliberately changed.
- Contrast: every token text colour on every product surface measures 6.9–20.1 : 1; gold button text 12.9 : 1.
- Visual validation: 104 screenshots at five viewports inspected; no overlapping controls. Preview and export compared
  scene by scene; the two-line caption now sits below the name card; one "Introduction" chapter.
- LLM validation: not done for real; Phase 21 changed no provider path.
- Notable defects fixed: captions re-wrapped in every video (removed); focus dropped on `<body>`; panels reacting under
  the sign-in dialog; particles missing from reduced-motion videos; a paused lesson starting on redraw; the player bar
  covering captions; an editor redraw loop that overflowed the stack.
- An educator audit (104 screenshots) found 5 high, 10 medium and 15 low UI items; most were fixed.

### Limitations and follow-ups
- **Real providers not exercised:** every browser check used the stand-in writer, media and voice.
- **Library pages:** at most 50 shared and 200 of the user's newest files per load; merged per-kind requests can push
  older items of one kind off the list; no "Load more" yet.
- **The editor's stage:** at ≤ 1280 px the panels cover part of the scene (a note says so). At 701–900 px the detail
  tracks are hidden while the inspector is open; at ≤ 700 px there is no stage.
- **The player bar covers the bottom of the stage** while it shows (preview only).
- Visual Review's debug view still shows the Phase 8 provider line, by design. The classic one-step generation keeps
  its older look.
- **The top bar is covered inside the Studio and the editor;** each has its own way back.
- **"Save as a copy" has no version history;** "Your lessons" has no Delete or Rename yet.
- Smaller wording items remain (three date formats, Title Case on export buttons, top bar icons without words at
  640 px and below, and others listed in the record).
- **On the stage (out of scope):** the narrated paragraph's highlight box is clipped at its lower edge; skill tree
  labels are small at 720p; the code card is briefly empty after a seek; at 600 and 820 px the live lesson reflows to
  portrait and the presenter is cut (preview only); under reduced motion the stage keeps its slow zoom and float.
- **Deferred:** the final sample-video workflow (`SAMPLE_KK.mp4`, not started); the editor's stage fitted between its
  panels; the top bar usable inside the Studio; lesson versions with Delete / Rename; "Load more" in the Library; a
  narrow-screen editor with stage and timeline; one date format.

---

## Known limitations (current, after Phase 21)

Consolidated from every phase's *Known limitations*, keeping only what is still true after Phase 21 (whose own list is
the most current); the phase that recorded each item is in brackets. Items marked "(code)" were read from the code.

### Real AI providers and keys
- **No real provider was exercised** from Phase 8 on: no text, image, video, presenter or voice key exists on the
  development machine, so every check used the stand-ins. Veo and Gemini image were tested with mocked clients only;
  every AI-assisted mode (analysis, composition, direction, alignment, terminology) with the stand-in model only. [8–21]
- **Pollinations' free image endpoint now asks for payment** (HTTP 402, observed 2026-09-29). Automatic AI images need
  `GEMINI_IMAGE_ENABLED=1` with a Gemini key, or a new adapter; the legacy `/get-image` still calls Pollinations
  directly, outside the provider layer. The project's Veo model (`veo-2.0-generate-001`) was no longer in Google's
  model list when checked: set `VEO_MODEL` to a model the key can use. [8]
- **No AI presenter provider exists;** custom presenters show as unavailable (none takes a reference picture). [12]
- The AI cache is private per user (shared `system` entries exist, but nothing creates them); AI videos made before
  Phase 5 have no generation identity and are reused only through the library match. [5]
- Recent-failure memory, per-provider concurrency limits, cache counters and Manim concurrency counters are per server
  process; Veo and the single-call providers cannot be cancelled on the provider's side; blank-output detection is a
  heuristic (one flat colour), and artistic quality is left to Visual Review. [5, 8, 9, 10]

### Recording and export
- Chrome or Edge only (tab capture with tab audio); the user must choose "Share this tab"; recording runs in real time;
  the delivered frame rate depends on the machine (about 27–29 of 30 fps on the development laptop). [2, 7]
- The recording stays in the page's memory until uploaded: a refresh during the upload loses it (the server keeps the
  uploaded part; only the same page can resume). [9]
- The MP4 copy needs ffmpeg with `libx264` and AAC (otherwise WebM only), one conversion at a time; subtitles follow the
  captions the page showed (none without on-screen captions); the glass blur is absent from recordings; quality findings
  are shown before an export, never enforced; **16:9 only** (no 9:16 or 1:1 frame geometry). [7, 17–19]

### Storage, security and deployment
- **Local disk only:** no object storage; Railway and Hugging Face disks are wiped on restart without a volume. [2, 3]
- `/static`, `/images` and `/video_template` stay public mounts for older lessons; signed links are bearer links (12 h
  for assets, 30 min for exports). `/get-image` and `/get-gif` answer without a login (code). [3]
- No clean-up of unused library files or unused generated assets; lessons cannot be deleted or renamed, so references
  and "Save as a copy" copies accumulate. [3, 5, 6, 21]
- `JWT_SECRET` must be set, or logins and links end at every restart. The default admin password is set in
  `server.py`'s code and no route changes a password (code). The quality endpoint has no rate limit. [2, 18]
- **Manim sandbox:** Windows lets every AppContainer read some registry keys, including the user's environment
  variables (closed only by the Python layers: keep secrets out of Windows user variables on a render host); the
  Docker runtime and `sandbox/Dockerfile.manim` are untested; the current Hugging Face Space reports Manim as
  unavailable; LaTeX is not installed on the development machine, so `Tex` / `MathTex` scenes fail there; the static
  checks may refuse unusual but harmless code; the first render on a machine includes a one-time grant (15–22 s). [10]
- After a crash, recovery waits for the lease to expire (up to `AI_JOB_LEASE_SECONDS`) before taking a run over. [9]

### Documents and lesson writing
- PDF structure is inferred from font sizes and positions: multi-column and scanned PDFs are not handled (no OCR); Word
  equations are dropped and Word lists may become paragraphs; DOCX / TXT references have no page numbers; the wording
  rules are English-only; `$$…$$` display equations are not listed as formulas by the Document Assistant; its
  recommendations are advice, the source is never rewritten. [11, 20]
- The voice, presenter speech timings, composition planning and recording run in the browser; only lesson writing and
  media generation continue with the page closed. [20]

### Visuals, presenter, composition and synchronization
- Library matching is word-based (uploads need a description or keywords); side panels never get AI videos; the AI
  Visuals setting is kept per browser. [4]
- Visual Review cannot switch a slot to another built-in renderer type, describes built-in visuals other than charts
  instead of drawing them, and does not review images inside a scene's board HTML; its debug view still shows the
  provider line, by design. [6, 21]
- **Aadhi's clips are full-frame studio videos:** the studio background stays, the camera stays still while he is on
  screen, he cannot be made small, and with no per-state clips talking and explaining look alike. [1, 13, 14]
- The Aadhi Teacher's mouth follows loudness, not phonemes; presenters switch poses in place (no tween); Aadhi changes
  narration state only; an AI clip cannot act. [12, 16]
- **No word timing:** a moment's position is a character ratio inside its narration segment — exact for the test tone,
  approximate for a real voice. Concepts are found by their words (a paraphrase only with AI alignment); emphasis
  targets are the whole visual, top-level list items, table columns, marked terms, the output card and the formula. [16]
- Camera timing and text size are estimated (the page's fit never shrinks text below 80 %); labels and emphasis come
  only from the screenplay; the director chooses only among what the lesson has, and the code's output card shows the
  screenplay's sentence (nothing is executed). Synchronization is read-only in Visual Review; the editor changes it
  only through the narration. [13–16, 19]

### Styles and quality
- Styles apply to Cinematic scenes only; media is framed, never recoloured; the graph, 3D, GIF and clip side panels and
  all code panels stay dark; there is no AI style recommendation; the CSS needs Chrome 111+. [17]
- The quality check does not read the content of pictures and clips, mathematical correctness, whether code runs, or
  the real voice length; terminology checks are pattern-based (common symbols can draw notices); only a Visual Review
  decision explains a different presenter; AI runs are matched to scenes by stored scene number. [18]

### Editor, Studio and product UI
- The editor's panels sit over the live full-screen stage: at ≤ 1280 px they cover part of the scene (a note says so);
  at 701–900 px the timeline's detail tracks hide while the inspector is open; at ≤ 700 px there is no stage. [19, 21]
- Splitting only at `[PAUSE]` markers; moving, adding, removing or splitting scenes waits while AI runs are in
  progress, and presenter clips attach by scene position. The editor's "Show in Visual Review" opens the lesson, not
  the selected scene (code). [19, 20]
- No real-time collaboration (two tabs are kept safe by revisions; two decisions on one slot at once: the later wins);
  no frame-accurate trimming; no audio editing beyond muting. [19, 20]
- Inside the Studio and the editor the top bar is covered (each has its own way back); "Save as a copy" has no version
  history; the classic one-step generation keeps its older look; a Library load lists at most 50 shared Aadhi assets
  and the user's 200 newest files (merged into one page when several kinds are asked), with no "Load more". [21]
- While the player bar shows it covers the bottom of the stage (preview only). Smaller wording remains: three date
  formats, Title Case export buttons, "Unsaved changes" while a field has focus, icon-only top bar at ≤ 640 px. [21]
- **On the stage (the video):** the narrated paragraph's highlight box is clipped at its lower edge; the skill tree's
  labels are small at 720p; after a seek the code card is empty for a moment; at 600 and 820 px the live lesson
  reflows to portrait and the presenter is cut (preview only); under reduced motion the stage keeps its slow zoom and
  float. [21]
- Safari and Firefox were never tested; a real autoplay block cannot be triggered under automation. [1]

## Glossary

| Term | Definition |
|---|---|
| Lesson | A saved project (`projects.json_data`): scenes, concept map, plans, decisions and settings; the single source of truth. |
| Scene | One step of a lesson: title, board HTML, narration, type, `scene_id` and plans. |
| Scene id | A scene's stable identity (`s-` + 12 hex characters) since Phase 19; results and edits find scenes by it. |
| Slot | A place in a scene that holds a visual: `main` or `side` (presenter and background are planned separately). |
| Visual plan | The router's answer per slot (`scene.visual_plan`): source, media type, asset or URL, renderer, provider, whether generation is required, and why. |
| Visual Router | The fixed rule chain (`visuals.py`) that plans each visual: review decision, explicit asset, existing media, earlier match, built-in renderer, Manim, no-visual intent, library match, AI. |
| Asset | A media file registered once in the Asset Library, with a stable ID, owner, kind, hash and metadata. |
| System asset | A shared, read-only asset (Aadhi's clips, posters, intro, music) visible to every user. |
| Signed link | A short-lived, scoped token in a URL that lets a media element load one private asset or export; never a login. |
| AI cache | The record (`ai_generations`) that maps a generation identity to its asset, so an equivalent request reuses it. |
| Generation identity | The canonical request (media type, provider, model, prompt, parameters) whose SHA-256 is the generation hash. |
| New AI version | A forced regeneration: a new asset, while the old one and its lesson references stay. |
| Provider | An adapter for one AI service (Pollinations, Gemini image, Veo, LTX, manual) behind the common interface. |
| Stand-in provider | Local replacements for every provider, writer and voice, active only with `AI_FAKE_PROVIDER=1` (test servers). |
| Run | A durable record of one logical generation, render, analysis or lesson-writing request (`ai_generation_runs`). |
| Attempt | One provider call within a run, with its saved job handle and outcome (`ai_generation_attempts`). |
| Lease | A run's time-limited ownership by one worker, renewed by a heartbeat; an expired lease lets recovery take over. |
| Needs attention | A run state that waits for a person because continuing could duplicate a paid generation. |
| Lesson batch | The server-side generation of every AI visual a saved lesson still needs ("Prepare visuals"). |
| Document Assistant | The Phase 11 panel that analyses a source document and prepares the Aadhi-ready structure. |
| Traceability | Each scene's `scene.source` (origin `source` or `ai`, references, coverage), from word overlap with the source. |
| Presenter Director | The rule-based planner (`presenters.py`) of whether, where and how a presenter appears in a scene. |
| Composition | A Cinematic scene's plan (`scene.cinematic_plan`): template, layers on the 16:9 frame, camera, timeline, transition. |
| Safe area | A reserved part of the frame (title band, subtitle band, content area, presenter zone) that important layers keep clear of. |
| Scene intent | The composer's reading of a scene: purpose, content, density, media, priority and ambiguity. |
| Visual direction | A scene's teaching strategy (`scene.visual_direction`), a preference for the router, presenter and composer. |
| Sync beat | A `[SYNC]` marker in the narration: board content is revealed when the narration reaches it. |
| Synchronization plan | The events of a scene (`cinematic_plan.sync`) anchored to narration positions: highlights, labels, camera moves, presenter acts. |
| Style family | One of four versioned looks (Academic, Cinematic Education, Children's Education, Corporate Training), e.g. `academic@1`. |
| Accessibility guard | The style step that checks text contrast and sizes and adjusts any colour that would fail. |
| Quality report | The Phase 18 result for a lesson: status (good, review, attention, blocked), findings by scene and dimension, registry. |
| Finding | One quality issue: rule, dimension, severity (info to blocking), scene, plain-words message, evidence and repair. |
| Fingerprint | A hash of what a decision or report depends on; when it changes, an approval or a report becomes stale. |
| Revision | The lesson row's update stamp; writers save only if it is unchanged (compare-and-set), else reload and re-apply. |
| Checkpoint | A user's Studio decision (structure, lesson, visuals, quality, export) stored with the lesson fingerprint it was made on. |
| Still preview | A scene drawn while the lesson is paused: shown complete, its narration not started (editor and redraws). |
| Caption fit | The Phase 21 step that lowers, then slightly shrinks (never below 85 %), a caption that would leave its band. |
| `?visualDebug` | URL flag that shows technical details (providers, models, ids, rules) normally hidden from teachers. |

## Appendix A — Environment variables

Names only; never commit values. Defaults are the code's. `.env` overrides the shell (`load_dotenv(override=True)`).

| Variable | Purpose | Default / note |
|---|---|---|
| `DATABASE_URL` | Database connection | `sqlite:///./projects.db`; `postgres://` rewritten to `postgresql://` |
| `JWT_SECRET` | Signs logins and link tokens | Random at each start if unset (everyone logged out) |
| `GEMINI_API_KEY` | Gemini text models, voices, Veo, Gemini image, Manim auto-heal | Empty; no key on the development machine |
| `GEMINI_API_KEY_2` … `_5`; `GOOGLE_API_KEY` | Extra keys rotated by the Gemini voice; counted as a Gemini key for Manim auto-heal | Optional |
| `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `GIPHY_API_KEY` | OpenAI text models and voices; ElevenLabs voices; GIF search (`/get-gif`) | Empty |
| `APP_USERNAME`, `APP_PASSWORD` | Listed in `.env.example` | Not read by the current code |
| `ASSETS_DIR`, `ASSET_MAX_BYTES` | Library storage; largest upload | `./assets`; 2 GB |
| `EXPORTS_DIR`, `EXPORT_MAX_BYTES`, `EXPORT_STALE_HOURS` | Finished videos; largest recording; an active export untouched this long counts as failed | `./exports`; 8 GB; 24 h |
| `STATIC_DIR` | Generated media (narration, renders, AI videos) | `static_videos` |
| `AI_GENERATION_ENABLED` | `0` turns every new AI generation off (cache hits still work) | 1 |
| `VISUAL_MATCH_THRESHOLD` | Minimum library match score | 0.6 |
| `AI_IMAGE_PROVIDERS`, `AI_IMAGE_PROVIDER` | Image provider order; preferred one | `pollinations,gemini-image`; first in order |
| `AI_VIDEO_PROVIDERS` | Video fallbacks after the one "Start the AI video service" chose | `veo,ltx` |
| `AI_PRESENTER_PROVIDERS`, `AI_PRESENTER_PROVIDER` | Presenter provider order; preferred one | None |
| `AI_PROVIDER_FALLBACK` | `0` never switches to a backup provider | 1 |
| `AI_PROVIDER_MAX_ATTEMPTS`, `AI_PROVIDER_MAX_RETRY_WAIT`, `AI_PROVIDER_RETRY_DELAY` | Tries per provider; longest honoured wait; delay between tries | 2; 20 s; 1 s |
| `AI_PROVIDER_COOLDOWN`, `AI_PROVIDER_AUTH_COOLDOWN`, `AI_PROVIDER_QUEUE_SECONDS` | How long a failing / credential-refusing provider goes last; longest wait for a concurrency slot | 60 s; 600 s; 600 s |
| `AI_MAX_IMAGE_BYTES`, `AI_MAX_VIDEO_BYTES` | Largest accepted generated file | 20 MB; 500 MB |
| `GEMINI_IMAGE_ENABLED`, `GEMINI_IMAGE_MODEL`, `GEMINI_IMAGE_MODELS` | Opt-in to billed Gemini images; model; extra models | 0; `gemini-2.5-flash-image` |
| `VEO_ENABLED`, `VEO_MODEL`, `VEO_MODELS`, `VEO_TIMEOUT` | Veo as a backup; model; extra models; timeout | 0; see Known limitations; 300 s |
| `<NAME>_ENABLED`, `<NAME>_TIMEOUT`, `<NAME>_MAX_CONCURRENT`, `<NAME>_POLL_SECONDS` | Per provider: `POLLINATIONS`, `GEMINI_IMAGE`, `VEO`, `LTX` | Adapter defaults |
| `AI_MEDIA_LOG`, `AI_CACHE_DEBUG`, `AI_CACHE_LOCK_MINUTES` | `[AI MEDIA]` log lines; cache logging; age at which a generation lock is abandoned | On; off; 30 min |
| `AI_RECOVERY_ENABLED`, `AI_RECOVERY_INTERVAL` | Continue interrupted runs; seconds between passes | 1; 15 s |
| `AI_JOB_LEASE_SECONDS`, `AI_JOB_HEARTBEAT_SECONDS` | Worker lease; renewal interval | 60 s; 15 s |
| `AI_JOB_CONCURRENCY`, `AI_RECOVERY_MAX_ATTEMPTS`, `AI_RUN_RETENTION_DAYS` | Recovery workers; recoveries before needs attention; days finished runs are kept | 2; 3; 90 |
| `LESSON_WRITER_THREADS` | Threads for lesson writing (bounded 1–8) | 3 |
| `COMPOSER_AI_BUDGET`, `DIRECTOR_AI_BUDGET` | Seconds for AI-assisted composition; direction and alignment | 25; 25 |
| `MANIM_SANDBOX_ENABLED`, `MANIM_SANDBOX_RUNTIME` | Manim rendering on / off; `auto`, `windows-appcontainer` or `docker` | 1; `auto` |
| `MANIM_SANDBOX_DIR`, `MANIM_SANDBOX_IMAGE`, `MANIM_SANDBOX_PROFILE` | Workspaces; Docker image; AppContainer profile | `%LOCALAPPDATA%\AadhiEduEngine\manim-sandbox`; `aadhi-manim-sandbox:0.21.0`; `AadhiEduEngine.ManimSandbox` |
| `MANIM_TIMEOUT`, `MANIM_MEMORY_LIMIT_MB`, `MANIM_CPU_LIMIT_PERCENT`, `MANIM_MAX_PROCESSES`, `MANIM_MAX_OUTPUT_MB`, `MANIM_MAX_WORKSPACE_MB`, `MANIM_MAX_DURATION`, `MANIM_MAX_FPS`, `MANIM_MAX_RESOLUTION`, `MANIM_MAX_SOURCE_KB`, `MANIM_MAX_SCENES` | Sandbox resource limits | Secure defaults, each capped by a hard maximum in `manim_security.py` |
| `MANIM_MAX_CONCURRENT`, `MANIM_MAX_PER_USER`, `MANIM_MAX_QUEUED_PER_USER`, `MANIM_MAX_QUEUED` | Sandboxes at once (server, user); queued animations (user, server) | 1; 2; 10; 50 |
| `MANIM_AUTO_HEAL_ATTEMPTS` | Times Gemini may rewrite code that crashed | 2 (0 = never) |
| `AI_FAKE_PROVIDER` | **Test servers only:** stand-ins for every provider, the writer and the text model | Off |
| `FAKE_TTS` | **Test only** (with `AI_FAKE_PROVIDER=1`): a speech-like tone instead of a TTS service | Off |
| `FAKE_LLM_MODE`, `FAKE_LLM_SECONDS` | **Test only:** stand-in model behaviour (`ok`, `malformed_once`, `malformed`, `fail`); delay per answer | `ok`; 0 |
| `AI_FAKE_FAIL`, `AI_FAKE_STATE_DIR` | **Test only:** make stand-ins fail (`<provider>:<media>:<failure>`); where stand-in jobs live | None; a temp folder |
| `AI_TEST_CRASH_AT`, `MANIM_TEST_CRASH_AT`, `<NAME>_JOB_SECONDS`, `<NAME>_PAID` | **Test only:** crash points; stand-in job length; treat a stand-in as billed | None |
| `AADHI_PASSWORD`, `AADHI_USER`, `PLAYWRIGHT_CORE_DIR`, `E2E_USE_ASSETS`, `<NAME>_CHECK_PORT`, `<NAME>_CHECK_OUT` | **Browser-check runner** (Node side): login, Playwright copy, library-media export, port and output folder | `AADHI_USER`: `admin` |

## Appendix B — Where to find more

- **`all-phases-details.md`** (repository root): the permanent record of every phase — objectives, audits,
  architecture, API tables, files changed, tests, measured results, issues found and fixed, known limitations and final
  status — including the guides "Adding a provider" (Phase 8) and "How to add or modify a safe render profile" (10).
- **`tests/`**: 30 backend modules (`test_*.py`, shared set-up in `backend_env.py`), 28 frontend files (`*.test.js`,
  fake DOMs in `helpers/`), 21 browser checks (`*_browser_check.mjs`, `export_e2e.mjs`) whose header comments say what
  each needs and covers, and the fixtures in `fixtures/`.
- **Module headers:** every module written in Phases 1–21 opens with a comment or docstring describing its contract.
- **`.env.example`**: the configuration variables with comments.
- **`README.md`**: the original setup notes, last updated in Phase 3; its feature and usage description predates the
  Studio and the Phase 21 shell. `DOCUMENTATION.md`, `handoff.md`, `Aadhi_EduEngine_Full_Context.md`,
  `aadhi_eduengine_report.md` and `PRESENTATION_SCRIPT.md` predate Phase 1.
