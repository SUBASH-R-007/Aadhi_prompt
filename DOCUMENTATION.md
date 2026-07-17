# 📚 REC – Aadhi EduEngine (KuttyKoncepts) — Complete Codebase Documentation

> **The single source of truth for this project.** Hand this document to any developer, AI assistant, or IDE to give them 100% technical context. Supersedes and expands `handoff.md`.
>
> Last updated: July 2026 (post pedagogy-engine upgrade).

---

## 1. Executive Summary

**Aadhi EduEngine** converts any textbook PDF/DOCX/TXT into a complete, cinematic, narrated video lesson — automatically. An LLM (Gemini 2.5 Pro by default) reads the source document against a ~300-line **Master Prompt** and emits a single strict-JSON "screenplay" (concept map + scenes + narration + animation code + quizzes + practice sheet). A zero-framework browser engine then *performs* that screenplay in real time: synchronized kinetic typography, TTS narration with deliberate pauses, live-compiled Manim math animations, AI-generated cinematic B-roll (Veo), interactive side panels, full-screen quiz checkpoints with countdowns, and a mascot (**Aadhi — a blackbuck, Tamil Nadu's state animal**, wearing a purple jacket and goggles). One click records the performance to `.webm` video and exports YouTube chapter timestamps plus a printable companion practice sheet.

**Pipeline in one line:**
```
PDF → (browser text extraction) → /generate-script (Gemini/OpenAI, streaming)
    → JSON screenplay → frontend performance engine
    → [TTS + Manim + Veo + charts/3D/quiz] → recorded MP4 + chapters.txt + practice sheet
```

---

## 2. Repository Map

### Core files (the entire product lives in these)

| File | Role |
|---|---|
| `index.html` (~310 KB) | **The monolithic frontend.** All CSS, all UI, the presentation engine, the sync engine, quiz checkpoints, export pipeline, auth UI, and the Master LLM Prompt (`getSystemPrompt()`, bottom of file). |
| `server.py` (~1,170 lines) | **FastAPI backend.** Auth (JWT), LLM proxy (streaming), TTS (4 engines), Manim compile + auto-heal, Veo/LTX video generation, image/GIF proxies, uploads, project history, WebSocket log streaming. |
| `models.py` | SQLAlchemy models: `User`, `Project`. |
| `database.py` | Engine/session factory. SQLite locally; `DATABASE_URL` (Postgres, Railway-style `postgres://`→`postgresql://` fix) in cloud. |
| `app.js` | Small companion script served at `/app.js`: History modal logic + magnetic button hover effect. |
| `requirements.txt` | fastapi, uvicorn, manim, pydantic, google-genai, edge-tts, python-dotenv, python-multipart, openai, httpx, sqlalchemy, pyjwt, passlib[bcrypt], bcrypt, psycopg2-binary. |
| `Dockerfile` | python:3.11-slim + ffmpeg + full TeX Live + Cairo/Pango for Manim. Runs as user 1000 on port **7860** (Hugging Face Spaces convention). |
| `.env` / `.env.example` | API keys and app credentials (see §13). |
| `PRESENTATION_SCRIPT.md` | Two-speaker expo presentation script. |
| `handoff.md` | Earlier (now superseded) handoff document. |

### Supporting directories (mostly generated / cached at runtime)

| Directory | Contents |
|---|---|
| `static_videos/` | Served at `/static`. **The universal media cache**: TTS audio (`audio_<hash>.mp3/.wav`), compiled Manim (`<Scene>_<hash>.mp4`), Veo/LTX videos (`ai_video_<hash>.mp4`), uploaded images/media. |
| `media/` | Manim's raw build output (`media/videos/temp_<hash>/720p30/<Scene>.mp4`, partial movie files, SVG text caches). Final videos are *copied* from here to `static_videos/`. |
| `images/` | Cached pollinations.ai contextual images (md5-of-prompt keyed). |
| `final_videos/`, `video_template/` | Additional static mounts. |
| `.claude/launch.json` | Dev-server launch config (`python server.py`, port 8000). |

### Debris / scratch files (safe to ignore or clean up)

`old_index.html`, `index2.html`, `temp.html`, `temp.js`, `test*.js` (test_script_0–16 etc.), `app_test.js`, `error_script_12.js`, `diff.txt`, `extracted.txt`, `out.txt`, `temp_log.txt`, `debug_failed_json.txt` (LLM JSON parse failures get dumped here), assorted `.wav`/`.mp3` test audio, one-off Python utilities (`fix*.py`, `patch_index*.py`, `rewrite_sync.py`, `update.py`, `upgrade_history.py`, `extractor.py`, `safe_escape.py`, `test_*.py`), `run_tts.py` (a *supertonic* TTS experiment — **not** part of the production pipeline despite older docs saying otherwise), `sample_template.docx`, `Digital_System_Design_*.html` (an exported presentation), `projects.db` (dev SQLite DB), `vercel.json` (static-hosting config for a frontend-only deploy; the real deploy path is the Dockerfile).

---

## 3. System Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│  BROWSER (index.html — single file, no framework)                      │
│                                                                        │
│  Upload screen ──► pdf.js / mammoth.js text extraction (client-side)   │
│        │                                                               │
│        ▼                                                               │
│  fetch POST /generate-script  (prompt_text = MasterPrompt + PDF text)  │
│        │            ◄─ streaming "PROGRESS:n" lines + WS updates       │
│        ▼                                                               │
│  FINAL_JSON:{screenplay} ──► slides[] ──► renderSlide() loop           │
│        │                                                               │
│  ┌─────┴──────────────────────────────────────────────────────┐        │
│  │ PERFORMANCE ENGINE                                          │        │
│  │  • slideSyncState (audio/video/advance gating)             │        │
│  │  • speakNarration(): [PAUSE] segmentation + [SYNC] reveals │        │
│  │  • runQuizCheckpoint(): countdown → reveal → advance       │        │
│  │  • updateSidePanel(): 9 panel types                        │        │
│  │  • applyAadhiLayout(): mascot state machine                │        │
│  │  • autoExportRecorder: getDisplayMedia → webm + chapters   │        │
│  └────────────────────────────────────────────────────────────┘        │
│         │ fetches media on demand                                      │
└─────────┼──────────────────────────────────────────────────────────────┘
          ▼
┌────────────────────────────────────────────────────────────────────────┐
│  FASTAPI BACKEND (server.py)  —  all media cached in static_videos/    │
│   /generate-audio  → edge-TTS | Gemini TTS | OpenAI TTS | ElevenLabs   │
│   /render          → manim subprocess (+ regex auto-fix + AI auto-heal)│
│   /generate-ai-video → Veo 2.0 API | local LTX-Video | manual mode     │
│   /get-image       → pollinations.ai (md5 cache)                       │
│   /get-gif         → Giphy API                                         │
│   /ws/progress     → live log streaming (Manim logs, LLM progress)     │
│   /api/login,/api/register,/api/me → JWT auth                          │
│   /save-history,/get-history,/api/projects/{id} → SQL persistence      │
└────────────────────────────────────────────────────────────────────────┘
          ▼
   SQLite (dev) / PostgreSQL via DATABASE_URL (cloud)
```

**Key design principles:**
- **The LLM is the director, not the renderer.** All media is generated/performed locally from its JSON screenplay.
- **Everything is cached by content hash.** Same narration text + voice → same audio file; same Manim code → same video; same Veo prompt → same B-roll. Replays are free and offline-capable.
- **The frontend performs; the backend produces.** The browser never holds API keys (except the legacy Giphy client key, see §14).

---

## 4. The JSON Screenplay Contract

The Master Prompt (in `getSystemPrompt()`, `index.html` bottom `<script>`) forces the LLM to return **one JSON object with exactly seven keys**:

```jsonc
{
  "subject_name":   "Strength of Materials",      // course codes stripped
  "unit_name":      "Solids",
  "session_number": "Session 1",
  "session_title":  "Properties of Solids",
  "concept_map":    [ {"id": "c1", "title": "Stress", "depends_on": []} ],  // 3–10 nodes, NEVER empty (frontend crashes)
  "scenes":         [ /* see scene types below */ ],
  "companion_sheet": "# Key Formulas...\n# Common Misconceptions...\n# Practice Problems...\n# Answer Key..."  // markdown handout
}
```

Every scene carries: `concept_id` (MUST match a concept_map id), `aadhi_position` (`left|right|center|popup_bottom_left|popup_bottom_right|hidden`), and a `narration` string (mandatory for ALL types).

### Narration markers (parsed by the frontend)
- **`[SYNC]`** — placed before each on-screen point; the player reveals the matching DOM element at the exact spoken moment. Count of `[SYNC]` tags must match count of animatable elements (every sentence in its own `<p>`/`<li>` tag or the sync engine desyncs).
- **`[PAUSE]` / `[PAUSE:n]`** — inserts real silence (1.5 s default / n seconds). Required after definitions, formulas, and prediction questions. Does not consume a `[SYNC]` slot.

### Scene types (11)

| # | `type` | Purpose / special fields |
|---|---|---|
| 1 | `title` | Chapter transitions only (auto-intro covers the opening — never scene #1). |
| 2 | `content` | Main teaching scene. `html` (max 3–4 bullets, one sentence per tag), mandatory `side_panel`. |
| 3 | `simulation` | Full-screen Manim. `visual_reasoning` (chain-of-thought), `manim_code` (raw Python, one `Scene` subclass, ≤15 s). |
| 4 | `example` | Worked problems; used in the faded-example sequence (full → blanked `______` steps → checkpoint). |
| 5 | `key-takeaway` | **Retrieval style**: narration challenges recall (`[PAUSE:3]`) before revealing items one-by-one via `[SYNC]`. |
| 6 | `summary` | Comparison tables (`<table>`). |
| 7 | `ai_video` | Full-screen cinematic B-roll. `prompt` (Veo-formatted, MUST include the mascot description), optional `ai_audio_source: "video"` to use native video audio instead of TTS. |
| 8 | `p5_simulation` | Interactive p5.js canvas in an iframe (`p5_code`). |
| 9 | `quiz_checkpoint` | **Full-screen retrieval practice.** `question`, `options[]`, `correct_index`, `feedback_wrong[]` (index-aligned; "" at correct index; distractors built from real misconceptions), `explanation`, `countdown_seconds` (default 8), `narration` (ask + "pause and try"), `reveal_narration` (answer + why the trap tempts). Quota: ≥1 per 3 concept nodes. |
| 10 | `chapter_card` | Full-screen "Part N" transition card. `chapter_label`, `title`, `subtitle`. One per major concept node → becomes YouTube chapter markers. |
| 11 | `recap` | "Previously in…" scene (mandatory 2nd scene when `session_number > 1`). Rendered as content + purple "Previously" badge. |

### Side panel types (9) — the `side_panel` object on content scenes

`skill_tree` (concept map), `image` (AI image via prompt), `chart` (Chart.js bar/line/pie/doughnut), `3d_model` (Three.js atom/torus/box), `quiz` (small side quiz — full checkpoints preferred), `terminal` (typed hacker terminal), `graph` (JSXGraph function plots), `manim` (small side animation, heavily scaled down), `gif` (Giphy; restricted to intros/transitions by the GIF-discipline rule).

### In-`html` enrichment vocabulary
`<div class='definition'>`, `<span class="keyword">`, `<div class="info-callout|tip-callout|warning-callout">` (warning-callout carries the mandatory **"Common Misconception: …"**), `<pre><code class="language-x">`, `<table>`, `<img class="context-gif" data-query="...">` (auto-fetched Giphy), `<div class='board-image-placeholder' data-img-id='...'>` (placeholder for source-document figures — the user uploads the real image, stored per-slide in `uploaded_images`), MathJax `$...$` / `$$...$$`.

### Pedagogy rules enforced by the Master Prompt (the "learning science" layer)
1. **Predict-before-reveal** — prediction question + `[PAUSE:2]` before every simulation/ai_video.
2. **Misconception targeting** — one warning-callout per concept; quiz distractors derive from them.
3. **Example AND non-example** after every definition.
4. **Dual coding** — narration must explicitly reference the side-panel visual.
5. **Elaborative bridging** — every narration opens by verbalizing the concept-map edge from the previous scene.
6. **Faded worked examples** — full → partially-blanked → checkpoint.
Plus: concrete-before-abstract ordering, cold-open hook with skill-tree roadmap as scene 1, deliberate pacing (`[PAUSE]` rules), chapter cards at concept boundaries, recap scene for later sessions, retrieval-style takeaways, reaction-GIF discipline, multimedia placement discipline (no B-roll mid-derivation), ai_video quota (≥5–6), single-topic-per-slide, split heavy content with "(Contd.)", learning objectives compressed to exactly one slide, script-parsing mode (pre-written scripts with "Board content"/"Aadhi speaks"/"Animation" directions are mapped faithfully, not rewritten).

---

## 5. Backend Reference (`server.py`)

### Startup behavior
- Loads `.env` (override=True). Auto-injects MiKTeX into PATH on Windows if `latex` isn't found.
- `models.Base.metadata.create_all(engine)` — creates tables on boot (no migrations; schema changes require dropping tables).
- Seeds admin user: **`admin` / `kutty@KONCEPTS$2026`** if missing.
- `SECRET_KEY` from `JWT_SECRET` env or random per-boot (⚠️ tokens invalidate on restart without it). Tokens: HS256, 7-day expiry.
- CORS: `allow_origins=["*"]`.
- Static mounts: `/static` → `static_videos/`, `/final_videos`, `/images`, `/video_template`.
- `python server.py` → uvicorn on `0.0.0.0:8000`. Docker CMD → port 7860.

### Endpoints

| Endpoint | Auth | Behavior |
|---|---|---|
| `GET /` | — | Serves `index.html` fresh from disk each request. |
| `GET /app.js` | — | Serves companion script. |
| `POST /api/login` | — | OAuth2 form → `{access_token}` (JWT, `sub`=username). |
| `POST /api/register` | admin only | Creates user (403 for non-admin callers). |
| `GET /api/me` | JWT | `{username}`. |
| `WS /ws/progress` | — | Broadcast channel: `MANIM_LOG:`, `GEMINI_PROGRESS:`, `OPENAI_PROGRESS:` messages to all clients. |
| `POST /generate-script` | JWT | **The LLM proxy.** Body: `{prompt_text, model_name, api_provider}`. Streams plain-text lines: `STATUS:STARTED`, `PROGRESS:<chars>`, then `FINAL_JSON:<minified json>` or `ERROR:<msg>`. Gemini path: `generate_content_stream` with `response_mime_type=application/json`, 65,536 max tokens, temp 0.2, run in a thread+queue (20 min httpx timeout). OpenAI path: `response_format=json_object`. Parsing: strips ``` fences, `json.JSONDecoder().raw_decode` (tolerates trailing junk); failures dumped to `debug_failed_json.txt`. |
| `POST /generate-audio` | JWT | Body: `{text, voice, tts_engine}`. Cache: sha256(text+voice+engine)[:16] → `static_videos/audio_<hash>.mp3|wav`. Engines: **default** = edge-tts (free); **gemini*** = `gemini-2.5-flash-preview-tts` with **API key rotation** across `GEMINI_API_KEY`, `_2`…`_5` on 429s, raw PCM wrapped into WAV (24 kHz mono 16-bit); **openai*** = `gpt-4o-audio-preview` (wav) or `tts-1(-hd)` (mp3→wav via ffmpeg); **elevenlabs*** = `eleven_multilingual_v2`. Any failure → silent fallback to edge-tts `en-US-GuyNeural`. Corrupt cached WAVs (missing RIFF header) auto-invalidated. |
| `POST /render` | JWT | **Manim compiler.** (1) Regex auto-corrects known LLM hallucinations (`.get_top_left()`→`.get_corner(UL)`, `SVGMobject`/`ImageMobject`→placeholder shapes, `GrowArrow`→`Create`, `Rectangle(corner_radius=…)`→`RoundedRectangle`, etc.). (2) Injects `config.background_color = '#1A0B2E'` (board purple). (3) Cache by sha256(code) → `<Scene>_<hash>.mp4`. (4) Runs `manim -qm temp_<hash>.py <Scene>` as async subprocess, streaming every log line over the WebSocket. (5) **AI auto-heal:** on failure, sends the error log + code to `gemini-2.5-flash` (temp 0.1) for a rewrite, retries up to 2×. 60 s timeout. Output copied from `media/videos/...` to `static_videos/`. ⚠️ Executes LLM Python unsandboxed — see §14. |
| `POST /regenerate-manim` | JWT | User-triggered rewrite of a scene's Manim code via `gemini-2.5-flash`, with optional `user_feedback` incorporated. |
| `POST /start-ai-server` | JWT | Selects the video-generation profile (global `ltx_pipeline`): `rtx_3090_2x` (local LTX-Video, multi-GPU balanced), `rtx_4060_laptop` (LTX with CPU offload + VAE tiling for 8 GB VRAM), `gemini_api_video` (Veo), `no_ai_generation` (manual mode — **the boot default**). |
| `POST /generate-ai-video` | JWT | Cache by sha256(prompt) → `ai_video_<hash>.mp4`. **Manual mode** returns `{status:"manual_required", filename, prompt}` → frontend shows instructions to drop the file into `static_videos/`. **Gemini mode** calls **`veo-2.0-generate-001`** (16:9), polls up to 5 min, downloads bytes. **Local LTX** renders 1280×704, 257 frames (~10.2 s @ 25 fps), 60 steps, guidance 4.5, fixed seed 42. |
| `GET /get-image?prompt=&subjectName=` | — | Contextual images via **pollinations.ai** (800×1200), md5-cached in `images/`; falls back to an abstract subject background. |
| `GET /get-gif?query=&randomize=` | — | Giphy search (needs `GIPHY_API_KEY` in .env), top-10, rating G. |
| `POST /upload-image` | JWT | Base64 image (e.g. extracted from DOCX or user-provided board figures) → `static_videos/img_<uuid>.png`. |
| `POST /upload-media` | JWT | Multipart file upload → `static_videos/`. |
| `POST /save-history` | JWT | Persists `{subject_name, unit_name, session_number, session_title, concept_map, scenes, companion_sheet}` as a `Project` row (payload JSON in `json_data`). Returns `/?project_id=<id>`. |
| `GET /get-history` | JWT | Current user's projects (id, url, display name, timestamp). |
| `GET /api/projects/{id}` | JWT | Full JSON payload of one project (404 if not owner's). |

### Database schema (`models.py`)

```
users:    id PK · username UNIQUE · password_hash (bcrypt)
projects: id PK · user_id FK→users.id · subject_name · unit_name · session_number
          · session_title · json_data (TEXT: full screenplay payload)
          · created_at · updated_at
```

---

## 6. Frontend Engine Reference (`index.html`)

Single file: CSS (lines ~1–2450), HTML body (upload screen, presentation board, side panels, modals, control bar), then one large main `<script>` plus a small `getSystemPrompt()` script at the very end. External CDN libs: GSAP, Chart.js, JSXGraph, Three.js/model-viewer, Prism.js, MathJax, pdf.js 3.11, mammoth.js (DOCX), p5.js (in simulation iframes).

### Global state
- `slides` — the scene array (mutated in place: `slides.length = 0; slides.push(...)`).
- `currentSlide`, `window.currentSlideRenderId` (stale-render guard), `window.currentNarrationId` (stale-audio guard).
- `ttsState` — `{isPlaying, isMuted, rate}` + engine/voice from `#tts-engine-select`, `#voice-select`, `#gemini-voice-select`.
- `window.slideSyncState` — per-slide advance gate: `{audioFinished, videoFinished, hasAudio, hasVideo, isReadyToAdvance()}`. `window.checkAndAdvanceSlide()` advances only when gates pass AND `ttsState.isPlaying`.
- `window.audioCache` — narration-segment → `{audio_url}` memo.
- `window.companionSheet`, `window.exportSceneLog`, `window.exportStartTime` — publishing exports.
- `window.INJECTED_DATA` — full payload when a saved project is loaded via `?project_id=`.
- `currentSubjectName/currentUnitName/currentSessionNumber/currentSessionTitle`, `conceptMapData`.
- `localStorage`: `jwt_token`, `saved_slides_backup` (auto-backup for crash recovery / "Restore Previous").
- `window.API_BASE_URL` — prefix applied to `/static/...` URLs for split-host deployments.

### The render pipeline — `renderSlide(index)` (~line 3680)
1. Bump `currentSlideRenderId`; log `{t, title, type, concept_id}` to `exportSceneLog` when exporting.
2. Build `slideSyncState` (ai_video with `ai_audio_source:'video'` gates on the video; everything else gates on TTS).
3. Kill any playing narration (`speakNarration(null)`).
4. `applyAadhiLayout()` — mascot position/state machine; ai_video forces Aadhi visible.
5. Side-zone policy: hidden for `ai_video`, `quiz_checkpoint`, `chapter_card` (board widens); otherwise `updateSidePanel(slide)` (with a look-ahead swap during full-screen sims for seamless background transitions).
6. Progress bar, scrubber dots (click-to-seek with tooltips), concept-map highlight.
7. `performDOMUpdate()` — type-dispatch: `title` | `chapter_card` (badge card) | `quiz_checkpoint` (`buildQuizCheckpointHTML`) | `visual/simulation/ai_video` (full-screen visual container; Manim via `/render`, ai_video via cache/`/generate-ai-video` incl. manual-mode instructions) | generic content path (`recap` badge, MathJax unescaping, `board-image-placeholder` substitution from `slide.uploaded_images`, context-GIF hydration, Prism highlight).
8. `finalizeSlideAnimations()` — cinematic zoom, **cascade prep**: selects animatable elements (`h2,h3,p,li,.math-block,.definition,.formula-block,.info-callout,.warning-callout,.tip-callout,pre`) with a **DOM-descendant filter** (nested matches excluded) and **media exclusion** (img/table render immediately — they don't consume `[SYNC]` slots); hides them (`cascade-hidden`); MathJax + Prism re-typeset; then narration dispatch: `quiz_checkpoint` → `runQuizCheckpoint()`; native-audio ai_video → video `onended`; narration → `speakNarration(narration, animateItems)`; silent → timed cascade + 5 s fallback advance.
9. Executed inside `document.startViewTransition`, with finalize chained on **`transition.updateCallbackDone`** (NOT `ready` — `ready` can race ahead of the DOM commit; this caused a real skipped-scene bug, fixed July 2026).

### The narration engine — `speakNarration(text, syncElements, opts)` (~line 4754)
- `parseNarrationSegments()` splits at `[PAUSE]`/`[PAUSE:n]` → `[{text, pauseAfter}]` (empty segments merge their silence into the previous one).
- `[SYNC]`-counts distribute `syncElements` across segments (proportional-by-length fallback when no SYNC tags; leftover elements go to the last segment).
- **Default engine** (browser `speechSynthesis`): utterance chain with `setTimeout` gaps.
- **Server engines**: all segment audio **prefetched in parallel** (`/generate-audio`, memoized in `audioCache`), then played sequentially. Per segment: char-ratio sync timings (`charIndex/totalChars × duration`), anchor-word fallback for legacy scripts, sentence-split subtitle track, `timeupdate` reveals + **`setNarrationActiveItem()`** golden highlight on the currently-narrated element, end-of-segment fallback reveal, 100 ms play/pause watchdog interval. Between segments: `waitPause()` honors pause/cancel. 
- Completion → `finishNarration()`: clears highlight, then `opts.onEnded()` if provided (quiz flow) else sets `audioFinished` + advance. Muted-with-text calls still fire `opts.onEnded` (prevents quiz deadlock in muted previews).
- `opts.onEnded` is the only extension point; `speakNarration(null)` is the cancellation idiom.

### Quiz checkpoint — `buildQuizCheckpointHTML` / `runQuizCheckpoint` (~line 2972)
Question + lettered options (divs — deliberately outside cascade selectors) + countdown ring + explanation block. Flow: question narration → `onEnded` → countdown (min 3 s; conic-gradient ring drains; `playTick()` each second; holds while paused; renderId-guarded) → `playDing()`, correct option glows green, `feedback_wrong[i]` fills in under wrong options, explanation reveals → `reveal_narration` plays → explicit `onEnded` advances. Clicking options during the countdown gives instant local feedback. A 20×150 ms retry guard tolerates late DOM commits; genuinely missing markup advances rather than deadlocking.

### Side panels — `updateSidePanel` + renderers (~line 3111)
`flipToPanel()` shows the concept map for 3 s then flips to the target panel. Renderers: `renderSideChart` (Chart.js), `renderSide3DModel` (Three.js), `renderSideQuiz` (side quiz, 10 s auto-reveal), `renderSideTerminal` (typewriter), `renderSideGraph` (JSXGraph), `renderSideVideo` (small Manim), `renderSideGif` (Giphy), `spawnSideAnimations`, concept map default. `imagePreloader` queues all AI images at presentation start to defeat rate limits.

### Mascot & backgrounds
`applyAadhiLayout(position, slide)` drives mascot video layers (idle/talking crossfades) and CSS vars (`--board-left`, `--board-width`). `toggleBackgroundFlawlessly()` (keys **B**/**T**) swaps mascot ↔ board/tree backgrounds and persists per-slide as `slide.force_background`. `startBGM()` loops ambient music at ~1% volume.

### Presentation flow & controls
`showStartOverlay()` → image preloading → `startPresentationSequence(isExporting)` → skippable cinematic intro (auto title card from subject/session metadata — why the LLM must NOT generate an opening title scene) → `startMainPresentation()` → `renderSlide(0)`. Keyboard: ←/→/Space navigate, B/T background. Control bar: play/pause, mute, rate slider, voice/engine pickers, scrubber, and buttons: ⚡ auto-export, `{ }` JSON download, 📝 companion sheet, `<P>` master prompt, ↓ export HTML.

### Export pipeline (~line 5820)
⚡ button → `getDisplayMedia` (user MUST pick "Share this tab" + "Also share tab audio"; audio-track check aborts otherwise) → `MediaRecorder` (vp9/h264 webm, 15 Mbps) → UI hidden → full auto-play run → on final slide `recorder.stop()` → downloads `ultimate_presentation_export.webm` + **`youtube_chapters.txt`** (from `exportSceneLog`: `00:00 Introduction` + one line per `chapter_card`, falling back to concept transitions; `mm:ss`/`h:mm:ss`, deduped ascending) + **`Aadhi_Companion_Sheet.md`**.

### Generation & data flow (~line 5400)
`handleFiles` → pdf.js page-by-page text extraction (DOCX via mammoth, TXT direct; direct-JSON upload also supported) → `getSystemPrompt() + extracted text` → streamed `/generate-script` → `robustParseJSON` → metadata + `window.companionSheet` captured → `slides` replaced → `saveProjectBackup()` (localStorage) → `/save-history` (server) → start overlay. Saved projects reload via `/?project_id=N` → `loadInitialProject()` → `INJECTED_DATA`.

### Auth UI
`submitAuth()` → `/api/login` → `jwt_token` in localStorage; `checkUserRoleAndLoad()` gates the app; admin modal (`submitAdminRegister`) creates users; `logout()` clears the token. ⚠️ Most `fetch` calls to protected endpoints must carry `Authorization: Bearer <token>` — verify header injection when touching fetch code (roadmap item).

### Script editor & review
`populateEditor()` — modal for editing the current slide's JSON, the current voiceover, or the entire script (teacher review safeguard). "Presentation Overview" modal forces backgrounds per scene. `regenerate-manim` button wires user feedback into an AI rewrite.

---

## 7. Caching Model (why replays are instant & offline-capable)

| Asset | Key | Location |
|---|---|---|
| TTS audio | sha256(text+voice+engine)[:16] | `static_videos/audio_*.mp3/wav` + in-page `audioCache` |
| Manim video | sha256(post-fix code)[:16] | `static_videos/<Scene>_<hash>.mp4` |
| AI B-roll | sha256(prompt)[:16] | `static_videos/ai_video_<hash>.mp4` |
| Context image | md5(prompt) | `images/<md5>.jpg` |
| Whole project | Project row | `projects.db` / Postgres + localStorage backup |

**Expo/demo implication:** one full playthrough pre-caches everything; the demo then runs with zero internet (except Giphy side panels).

---

## 8. Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` (+ `_2`…`_5`) | Script generation, Gemini TTS (rotated on 429), Veo video, Manim auto-heal. |
| `OPENAI_API_KEY` | Optional: GPT-4o script generation + OpenAI TTS. |
| `ELEVENLABS_API_KEY` | Optional: premium voices. |
| `GIPHY_API_KEY` | Backend GIF search (`/get-gif`). |
| `JWT_SECRET` | Stable JWT signing key (unset → random per boot → sessions die on restart). |
| `DATABASE_URL` | Postgres in cloud; absent → `sqlite:///./projects.db`. |
| `APP_USERNAME`/`APP_PASSWORD` | Legacy interface lock (superseded by DB auth). |

**Default admin:** `admin` / `kutty@KONCEPTS$2026` (seeded at boot).

---

## 9. Running & Deploying

**Local (Windows dev machine):** Python 3.10+, FFmpeg on PATH, MiKTeX (auto-detected), `pip install -r requirements.txt`, `python server.py` → http://127.0.0.1:8000. Dev-server config in `.claude/launch.json`.

**Docker / Hugging Face Spaces:** `Dockerfile` installs ffmpeg + full TeX Live + Cairo/Pango, runs uvicorn as user 1000 on **7860**.

**Railway / cloud:** set `DATABASE_URL` (postgres scheme auto-fixed), `JWT_SECRET`, API keys. Frontend URL-routing for split deployments handled via `window.API_BASE_URL` prefixing (see git history: "dynamic localhost and railway routing").

**`vercel.json`:** static-only fallback deploy of the frontend (all routes → index.html); not the primary path.

---

## 10. Recent Major Work (July 2026 — the Pedagogy Engine upgrade)

All in the working tree (uncommitted at the time of writing), verified end-to-end in-browser:

1. **`quiz_checkpoint` scene type** — full-screen countdown quiz with misconception feedback (frontend CSS+JS + prompt spec + quota).
2. **`[PAUSE]` narration markers** — segment-based `speakNarration` rewrite; parallel segment prefetch; works across all 4 TTS engines.
3. **Narration highlighting** — `.narration-active` golden glow tracks the spoken element.
4. **`chapter_card` + `recap` scene types** — segmenting + cross-session spaced review.
5. **Master Prompt learning-science layer** — §4's pedagogy rules; `companion_sheet` added to the contract (7 keys).
6. **Publishing exports** — `youtube_chapters.txt` + `Aadhi_Companion_Sheet.md` on export; 📝 control-bar button; `companion_sheet` persisted through `/save-history` (backend model updated).
7. **Bug fixes:** View-transition race (`ready` → `updateCallbackDone`) that could skip scenes; muted-preview quiz deadlock (explicit `onEnded`); earlier session: ai_video advance gating on TTS vs native audio, DOM-descendant sync filter, img/table sync exclusion, title clamps, scrollable board.

Prior uncommitted work: master prompt extracted into `getSystemPrompt()`, prompt/JSON download buttons, `bcrypt` unpinned in requirements.

---

## 11. Known Issues, Risks & Tech Debt

1. **`/render` executes LLM-generated Python unsandboxed** (`manim` subprocess with full env). Mitigations exist (regex fixes, auto-heal, 60 s timeout) but true sandboxing (container/user-jail/AST allowlist) is the top roadmap item.
2. **JWT header coverage** — some frontend fetches may lack the `Authorization` header; audit needed (roadmap #2 from handoff).
3. **Giphy client-side key** — a hardcoded public Giphy key exists in `index.html` for `context-gif` hydration (the backend route properly uses `.env`); should be routed through `/get-gif`.
4. **Default admin password is in-repo** — rotate for any public deployment; set `JWT_SECRET`.
5. **No DB migrations** — `create_all` only; schema changes on SQLite require dropping tables (the historical `user_id` crash).
6. **Muted mode never auto-advances** (by design, except quiz reveals) — documented behavior, occasionally surprises testers.
7. **Repo hygiene** — large binary/media debris tracked in git (see §2 debris list); Git LFS mentioned in README but media caches would be better git-ignored.
8. **`/get-image` & `/get-gif` are unauthenticated** and proxy external services — rate-limit or auth-gate before public deploy.
9. **CORS `*`** — tighten for production.
10. **Recording depends on the user checking "share tab audio"** — guarded by an audio-track check + alert, but still the #1 live-demo footgun.

---

## 12. Quick Reference Card

```
Run:            python server.py            → http://127.0.0.1:8000
Login:          admin / kutty@KONCEPTS$2026
Generate:       upload PDF → Generate Presentation (Gemini 2.5 Pro default)
Keys:           ← → Space navigate · B/T toggle background
Export:         ⚡ button → share tab + tab audio → webm + chapters + sheet
Downloads:      { } screenplay JSON · 📝 companion sheet · <P> master prompt
Manim cache:    static_videos/<Scene>_<hash>.mp4
Audio cache:    static_videos/audio_<hash>.*
Prompt lives:   index.html → getSystemPrompt()  (only copy; backend receives it per-request)
Sync markers:   [SYNC] = reveal point · [PAUSE]/[PAUSE:n] = real silence
Scene types:    title · content · simulation · example · key-takeaway · summary
                · ai_video · p5_simulation · quiz_checkpoint · chapter_card · recap
Side panels:    skill_tree · image · chart · 3d_model · quiz · terminal · graph · manim · gif
JSON keys (7):  subject_name · unit_name · session_number · session_title
                · concept_map · scenes · companion_sheet
```
