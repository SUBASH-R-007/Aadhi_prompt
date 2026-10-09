# REC – Aadhi EduEngine v2

AI lecture-video generator for **Rajalakshmi Engineering College – Kutty Koncepts**. Upload a
textbook chapter or lecture notes (PDF / DOCX / TXT / MD), or paste your notes. Aadhi, our blackbuck
mascot, turns it into a narrated, animated lesson. It plays in the browser and exports as an MP4 with
captions and chapters.

Before anything is planned you can check how Aadhi read your document ("Let me check how Aadhi read
my document" on *New lecture* or *Regenerate*): the source report shows the outline, what was set
aside, the concepts found and things worth fixing in the source, and lets you set parts aside or
rename concepts before generation continues. Every version's report stays available from its
*Actions* menu ("How Aadhi read the source"). The editor's *Issues* panel groups the lecture checks
(teaching, terms and abbreviations, formulas, code, board and pacing ...) and offers safe one-click
fixes; the render dialog lists open errors and warnings without blocking the render.

v2 is a ground-up rebuild of the v1 prototype. The full rationale is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

| | v1 | v2 |
|---|---|---|
| Generation | one giant prompt → one huge JSON | plan → per-scene structured generation in parallel → lint + LLM critic → targeted repair |
| Source understanding | flattened PDF text | layout-aware extraction (headings, tables, figures, page-anchored chunks) + native PDF to multimodal models |
| Sync | `[SYNC]` tags + character-ratio estimates | narration *beats* aligned to board items by construction; per-beat TTS gives exact timings |
| Assets | generated while the lecture plays (spinners, "manual generation required") | pre-rendered before playback; content-addressed cache; graceful fallbacks |
| Manim | one-shot LLM code on the host | tested template library first; free-form code is AST-checked, sandboxed, self-healed and timed to the narration |
| Export | real-time screen recording (WebM) | deterministic MP4 render (H.264/AAC) + SRT/VTT + YouTube chapters |
| Scale | single process, globals, local disk | durable job queue, workers, Postgres, S3/R2 + CDN, budgets |
| Security | RCE, hardcoded secrets, XSS | sandboxing, strict CSP, typed rendering (no HTML from the model), CSRF, roles, rate limits |

## Quick start (development)

Requirements: Python 3.11, Node 22, FFmpeg on `PATH`. For Manim maths you also need a LaTeX
distribution (MiKTeX on Windows, TeX Live on Linux/macOS).

```bash
python -m venv .venv
```
```bash
.venv/Scripts/python -m pip install -c requirements.lock -r requirements.txt -r requirements-dev.txt   # Windows (.venv/bin/python on Linux/macOS)
```
```bash
.venv/Scripts/python -m playwright install chromium
```
```bash
npm ci          # also vendors the frontend libraries and fonts into web/vendor
```
```bash
.venv/Scripts/python server.py
```

Open http://127.0.0.1:8000. With API keys in `.env` this uses the real (billable) providers
configured there (Gemini by default). On first start an admin account is created. Set `ADMIN_PASSWORD` in
`.env` beforehand, or read the one-time generated password from `data/initial_admin_password.txt`.
You must change it at first login.

**No API keys, or just trying it out?** Run the offline demo server, which can never call a paid API
(it forces the fake LLM/image/video providers even when `.env` holds real keys, uses free Edge TTS
voices and keeps its own `data-demo/` folder):

```bash
.venv/Scripts/python scripts/dev_demo.py
```

Then open http://127.0.0.1:8010. The admin password is in `data-demo/initial_admin_password.txt`.
In demo mode the job cost shown is an *estimate* of what the same run would cost with the configured
real models; nothing is billed.

Configuration reference: [.env.example](.env.example). Operations and production deployment:
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## AI engine

Choose the AI engine (LLM) that writes a lecture with the **AI engine** dropdown in the lecture
options, when you upload a document or regenerate a lecture.

| Engine | Key in `.env` | Models: plan / script, critic, fast |
|---|---|---|
| Google Gemini | `GEMINI_API_KEY` | `LLM_MODEL_*` (`gemini-2.5-pro` / `gemini-2.5-flash`) |
| OpenAI | `OPENAI_API_KEY` | `OPENAI_MODEL_*` (`gpt-4.1` / `gpt-4.1-mini`) |
| Anthropic Claude | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL_*` (`claude-opus-5-5` for every tier) |

* An engine without a key shows as *(not configured)* and cannot be chosen; the API refuses it
  as well (422). The key can also be one saved in the Studio ([Your own API keys](#your-own-api-keys)).
* Without a choice the lecture uses the server default, `LLM_PROVIDER` (`gemini` unless set); when
  that engine has no key the dropdown starts on a configured one. Scene regeneration, plan approval
  and translation reuse the engine the version was generated with (refused if its key was removed).
* Scanned or maths-heavy PDFs are also attached as the original file for Gemini only; OpenAI and
  Claude work from the extracted text.
* Cost: every engine is priced and counts against `MAX_COST_PER_LECTURE_USD` and the daily budgets
  (except calls paid with your own key, which skip the daily budget).
  Claude list prices per 1M tokens are Opus 5.5 $4 input / $20 output and Sonnet 5.5 $2 / $10
  (`ANTHROPIC_MODEL_SCRIPT/CRITIC/FAST=claude-sonnet-5-5` is the cheaper setup). Prompt caching
  bills a prompt prefix that repeats across calls at a reduced rate. `ANTHROPIC_EFFORT` (default
  `high`) trades thinking depth for tokens.
* The offline demo (`scripts/dev_demo.py`) and the tests never call a paid engine.

**Pictures and clips.** Generated illustrations come from `IMAGE_PROVIDER` (Pollinations by
default, Gemini with a key) and AI clips from `VIDEO_PROVIDER` (Veo). Backups can be configured
(`IMAGE_FALLBACK_PROVIDERS`, `VIDEO_FALLBACK_PROVIDERS`); a lecture can pick its image provider in
the options, and the Admin page shows the state of each provider. Details in
[docs/OPERATIONS.md](docs/OPERATIONS.md#32-generated-images-and-ai-video-provider-chain).

## Your own API keys

API keys can also be saved in the Studio, so nobody has to edit `.env`:

* **API keys page** (every user): your **personal** keys. They are used only for lectures and jobs
  that you start.
* **Server API keys** on the **Admin** page (admins only): keys used for everyone. A saved server
  key replaces the matching `.env` key; removing it brings the `.env` key back into use.

For each provider, a job you start uses the first key that exists: **your personal key, then the
server key saved in the Studio, then the `.env` key**. If the provider rejects your personal key,
the job fails with a clear error. It never switches to the server key behind your back; fix or
remove your key and retry.

| Key | Powers |
|---|---|
| Google Gemini | the Gemini AI engine, Gemini voices, generated images and AI video |
| OpenAI | the OpenAI AI engine and OpenAI voices |
| Anthropic Claude | the Claude AI engine |

ElevenLabs, GIPHY and storage keys stay in `.env`.

* A key is encrypted as soon as it is saved and is never shown again. The page only shows a hint
  such as `sk-ant-…AbCd` and when it was last changed.
* **Test** checks a saved key with a free "list models" call to the provider. Nothing is generated
  and nothing is billed. The result (and any error) is kept next to the key.
* Calls paid with your personal key do not count toward your daily budget, but the per-lecture cap
  (`MAX_COST_PER_LECTURE_USD`) still applies, so a runaway job still stops. Usage still records and
  prices them, separately from server-paid spend: the **Usage** page shows them as "Paid with your
  own keys", and "Budget used today" counts only what the server pays (so does the "today" figure
  admins see for each user).
* The API keys page is at `#/keys` (top bar and account menu). A job that failed because the
  provider rejected your key links straight to it.
* The offline demo (`scripts/dev_demo.py`) has saved keys turned off.

Admins: the switches and the encryption key are in [docs/OPERATIONS.md](docs/OPERATIONS.md#31-api-keys-saved-in-the-studio);
how keys are protected is in [docs/SECURITY.md](docs/SECURITY.md).

## Media library

**Library** in the top bar (`#/library`) holds your own pictures and clips: everything you upload
(there, or in the editor) and, unless `LIBRARY_AUTO_SAVE_GENERATED=false`, the pictures and clips
that builds you start of your own lectures generate. Search them, filter by kind and source, give them
a title, description and keywords, and delete what you no longer need (lectures that use it keep
working). In the editor every media control of your own lectures has **Choose from library**, and the
options form can **prefer pictures from my library** over generating new ones when one matches the
picture's description well (each picture is used for one scene of a lecture, and a description you
edit always gets a new picture). Your library is private: nobody else, admins included, sees or uses
it. "Suggest with AI" fills in the words for you when an
admin turns on `LIBRARY_AI_DESCRIBE_ENABLED` (each suggestion is a billed model call).

## Visual Review

**Review visuals** (on each version of the project page, and in the editor) shows every scene's
picture, clip, animation or chart in one place: where it came from (AI, your library, your upload,
the document, built in), which service made it, and whether it is ready. Approve each visual, or ask
for a **new AI version** (a new paid generation of the same prompt; the old one stays cached and is
kept if the new one cannot be made), choose one from your library (**N matches in your library**
shows the best ones first), upload a replacement or remove it. Actions on one scene build only that
scene: render after **Build changed scenes** once every changed scene is built. An approval is kept until the scene's
visual changes, and approving never changes the lecture or its video. If an AI video may already
have been paid for by an attempt that stopped, it is made again only after you confirm.

## Next step and videos

Each project page says where the lesson is ("Plan ready for review", "Ready to make the video", "Video
out of date" ...) with one **Next step** button, and every project card shows its stage. Videos made
before the latest changes are marked **Out of date**. **Videos** in the top bar (`#/videos`) lists every
video of your lectures, newest first: watch one in the browser, download it, or open its lecture. Add
`?debug` to the Studio's address (`/?debug=1#/projects`) to also see render numbers, job numbers and
revisions. New to Aadhi? **Try an example** on *New lecture* imports a ready-written five-scene lecture on
Ohm's law (only its voice-over is built).

## Editing scenes

In the editor you can:

* **Skip a scene in the video**: it stays in the lecture (and in the scene list, striped and marked
  "Skipped") but leaves the player, the MP4, the captions and the chapters, and nothing is generated for
  it. Untick to bring it back.
* **Show a scene for at least N seconds** (1–600): the scene holds after its narration; nothing else
  moves.
* **Split a scene** at a beat ("Split here"): the beats after it, and the board items they show, become a
  new scene; the picture stays with the first part.
* Follow the lecture on the **timeline strip** under the panes (click to seek, drag a scene to move it),
  and press `?` for the keyboard shortcuts.
* **Save as a copy** (a new version with your unsaved changes) or turn on **Save automatically** (off by
  default, remembered in this browser). While Aadhi works on the version, editing waits and your changes
  are merged afterwards; where you and Aadhi changed the same thing your version is kept, nothing is saved
  automatically until you have looked and saved, and Undo shows Aadhi's version.

**Generated vs edited**: scenes you changed since Aadhi wrote them are marked *Edited*, *New* or *Moved*,
and the side pane lists them with the removed ones. **Compare and revert…** puts a scene back as Aadhi
wrote it (or as it was before a regeneration), and a removed scene can be restored; Ctrl+Z undoes it.
Lectures generated before this feature, and imported ones, show no markers.

## Upgrading from v1

```bash
.venv/Scripts/python -m aadhi.cli import-legacy --db projects.db
```

This imports v1 users and projects, converting the old `[SYNC]` scripts into v2 screenplays. The
v1 `admin` password was published in the v1 source code, so the imported `admin` account gets an
unusable password. Set a new one:

```bash
.venv/Scripts/python -m aadhi.cli set-password admin
```

See the incident notes in [docs/SECURITY.md](docs/SECURITY.md).

**Lessons from the earlier fork of the app** (`origin/andryan`) import the same way: a lesson JSON
through **Import JSON** on the Projects page (or `import-json`), or its whole `projects.db` with `import-legacy`.
Scenes hidden there come in as skipped scenes, minimum scene durations are kept, and what v2 does not
use (muted narration, captions turned off, presenter and style settings, its media ids) is listed as
import warnings. That app saved every save as a new row, so add `--latest-only` to import only the
newest save of each lesson:

```bash
.venv/Scripts/python -m aadhi.cli import-legacy --db projects.db --latest-only
```

## Project layout

```
aadhi/      backend (FastAPI app, jobs, pipeline, providers, manim, compose, auth, storage)
web/        frontend (Studio, Player, render page, p5 sandbox) — native ES modules
alembic/    database migrations
evals/      evaluation fixtures and results (python -m aadhi.evals); video_qa.py (MP4 checks),
            render_parity.py (preview vs MP4 comparison: python evals/render_parity.py)
tests/      pytest suites;  web/tests/: node --test suites
docs/       ARCHITECTURE, API, INTERFACES, SECURITY, OPERATIONS, EVALS, INTEGRATION_ANDRYAN
```

## Tests

```bash
.venv/Scripts/python -m pytest -m "not slow"
```
```bash
npm test
```

`web/tests/ui_tokens/` checks the Studio's colours against WCAG AA contrast and its 12 px type floor.

## License

MIT
