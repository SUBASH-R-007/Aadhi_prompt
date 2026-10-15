# Aadhi EduEngine v2 — Operations

How to run REC - Aadhi EduEngine locally, configure it, deploy it with Docker Compose, keep it
healthy, and upgrade from v1. Architecture: `docs/ARCHITECTURE.md`; HTTP API: `docs/API.md`;
eval harness: `docs/EVALS.md`.

## 1. Processes at a glance

| Process | Command | Needs |
|---|---|---|
| API + studio + player | `uvicorn aadhi.main:app` (dev: `python server.py`) | DB, storage, `web/vendor` |
| Job worker | `python -m aadhi.worker` | DB, storage, LLM/TTS keys, ffmpeg, Manim (+ TeX) |
| Render worker | `python -m aadhi.worker` with `WORKER_KINDS=render_video` | DB, storage, ffmpeg, Chromium, fonts, BASE_URL reachable |
| Database | SQLite (dev) / PostgreSQL 16 (production) | |
| Blob storage | local directory (`STORAGE_LOCAL_DIR`) or S3 / Cloudflare R2 / MinIO | |

In development `WORKER_MODE=inline` runs `WORKER_CONCURRENCY` worker threads inside the API process,
so one command is enough. In production the API never runs jobs (`WORKER_MODE=external`).

## 2. Local development

Prerequisites: Python 3.11, Node 22, ffmpeg/ffprobe on `PATH`; for Manim formulas a LaTeX
distribution (MiKTeX on Windows, TeX Live on Linux/macOS).

```bash
# Python environment (Windows paths shown; on Linux/macOS use .venv/bin/python)
python -m venv .venv
# requirements.lock is a constraints file: exact versions, while the requirements files choose
# what gets installed (the Docker image installs requirements.txt only, never the dev tools)
.venv/Scripts/python.exe -m pip install -c requirements.lock -r requirements.txt -r requirements-dev.txt
.venv/Scripts/python.exe -m playwright install chromium     # only needed for MP4 renders

# Frontend libraries and fonts -> web/vendor (npm ci runs scripts/vendor.mjs; `npm run vendor` runs it again)
npm ci

# Optional: configuration (everything has a safe development default)
cp .env.example .env

# Run
.venv/Scripts/python.exe server.py      # http://127.0.0.1:8000
```

**What `npm ci` vendors (from Dynamic video batch B2b on).** Besides the libraries and the classic fonts
(`web/vendor/fonts/`, `fonts.css`, unchanged), `scripts/vendor.mjs` does two new things:
* It copies the Dynamic stage's faces (Poppins, Lexend, Atkinson Hyperlegible Next, Anek per Indic script) into
  `web/vendor/fonts/dynamic/<family>/`, each with its package's LICENSE. The face list comes from the generated
  `web/js/dynamic/faces.gen.js`, through `scripts/vendor_faces.mjs`, which stops the install on a wrong package
  version, a licence that is not OFL 1.1, or a unicode-range that differs from the package's.
* It copies `mathjs/math.js.LICENSE.txt` beside `math.js`.

`npm ci` also installs the icon packages `lucide-static` (about 51 MB unpacked) and `@tabler/icons` (about 11 MB).
Only the Python icon generator reads them; nothing of them is copied to `web/vendor` or into the image. The licences
of everything served are in the generated `THIRD_PARTY_NOTICES` at the repository root, which the image ships at
`/app/THIRD_PARTY_NOTICES`.

**First admin password.** On first start, when no admin exists, the server creates the
`ADMIN_USERNAME` account (default `admin`). If `ADMIN_PASSWORD` is set it is used; otherwise a random
one-time password is written to `DATA_DIR/initial_admin_password.txt` (default
`data/initial_admin_password.txt`, mode 0600). Only the file path is logged. You must change it at
the first login; delete the file afterwards. Production refuses to generate one: set
`ADMIN_PASSWORD` for the first start and rotate it immediately.

**Demo mode without API keys.** Fake providers are deterministic and free:

```bash
LLM_PROVIDER=fake TTS_PROVIDER=fake IMAGE_PROVIDER=fake VIDEO_PROVIDER=fake .venv/Scripts/python.exe server.py
```

(PowerShell: `$env:LLM_PROVIDER='fake'; ...`). Uploading a PDF then produces a complete lecture
with placeholder content, which is ideal for UI work and demos. `scripts/dev_demo.py` (see the
README) goes further: it also turns off API keys saved in the Studio
(`STORED_API_KEYS_ENABLED=false`) and keeps its own `data-demo/` folder.

**Separate worker** (closer to production): start the API with `WORKER_MODE=external` and run
`.venv/Scripts/python.exe -m aadhi.worker` in a second terminal. Both `.claude/launch.json`
configurations (`aadhi-server`, `aadhi-worker`) use the venv interpreter.

**Checks before a commit**

```bash
.venv/Scripts/python.exe -m ruff check aadhi tests scripts evals docker
.venv/Scripts/python.exe -m pytest -m "not slow and not network"   # fast suite
.venv/Scripts/python.exe -m pytest -m slow                         # real manim/ffmpeg/playwright output
npm run typecheck && npm test
.venv/Scripts/python.exe scripts/gen_env_example.py --check        # .env.example in sync with config
.venv/Scripts/python.exe scripts/gen_chromium_seccomp.py --check   # render-worker seccomp profile current
.venv/Scripts/python.exe scripts/lock_requirements.py --check      # lock matches the venv + Linux-only pins
.venv/Scripts/python.exe evals/make_fixtures.py --check             # eval fixtures current
.venv/Scripts/python.exe -m aadhi.schemas.generate --check          # web/schemas/*.schema.json match the models
.venv/Scripts/python.exe -m aadhi.compose.dynamic.catalogue --check # web/js/dynamic/catalogue.gen.js is current
.venv/Scripts/python.exe -m aadhi.compose.dynamic.faces --check     # dynamic-fonts.css + faces.gen.js current
.venv/Scripts/python.exe -m aadhi.compose.dynamic.icons --check     # icon_index.json current (needs node_modules)
.venv/Scripts/python.exe scripts/gen_third_party_notices.py --check # THIRD_PARTY_NOTICES current (needs node_modules)
node scripts/gen_colour_vectors.mjs --check                         # tests/fixtures/colour_vectors.json is current
```

The Dynamic catalogue (`web/js/dynamic/catalogue.gen.js`, from Dynamic video batch B2a) is generated from
`aadhi/compose/dynamic/` (zones, templates, themes): after changing those, regenerate it with
`python -m aadhi.compose.dynamic.catalogue` and commit it with them; `--check` exits 1 and names the first differing
line (CI's "Generated files are current" step runs it). The colour vectors are written from the stage's JavaScript
colour code by `node scripts/gen_colour_vectors.mjs`; both test suites read them, and `npm test` fails when they are
stale.

Dynamic video batch B2b added three generators (`DYNAMIC_VIDEO_DESIGN.md` §4.6, §4.12). All three are checked in
CI's "Generated files are current" step; the icons and notices checks run after `npm ci`, because they read
`node_modules`. Each `--check` exits 1 when the committed file is stale; the icons and notices generators exit 2 when
they cannot generate from what is installed (fail closed). B2b's fourth generator, the kerning table, was retired in
B2b-H: text fit now shapes with HarfBuzz, and the text-fit parity probe below checks it.

* **Face table** (`aadhi/compose/dynamic/faces.py` -> `web/css/dynamic-fonts.css` and
  `web/js/dynamic/faces.gen.js`). After changing `faces.py`, run `python -m aadhi.compose.dynamic.faces`, then
  `npm run vendor` (which copies the faces from the generated table), and commit both generated files. A new face
  also needs its exact-pinned Fontsource package in `package.json`, then the notices below and the extended text-fit
  parity probe (below), which measures every vendored face against the page.
* **Icon index** (`aadhi/compose/dynamic/icon_index.json`, committed). After bumping `lucide-static` or
  `@tabler/icons` in `package.json`:
  1. `npm install`;
  2. `python -m aadhi.compose.dynamic.icons`. It refuses a changed licence text, a listed icon without a file, an
     ambiguous deprecated file, or a `PREFERRED` / `IGNORED_TAGS` entry the new release made stale or shadowed: read
     the release and update the lists in `icons.py`;
  3. `python scripts/gen_third_party_notices.py`;
  4. review the index diff before committing.
* **`THIRD_PARTY_NOTICES`** (`scripts/gen_third_party_notices.py`). After any change to the npm dependencies, run
  `python scripts/gen_third_party_notices.py` and commit the file. It fails closed when a package's installed version
  differs from the lock (run `npm ci`), when a package has no licence text, or when a vendored bundle no longer
  matches its reviewed `VENDORED` entry. The entry fails on a new package version, a bundled component's evidence that
  is gone, a p5 module no component lists, or a copyright statement nobody acknowledged. Then review the new bundle,
  update the entry and the texts in `scripts/third_party_licences/`, and regenerate.
* **Text-fit parity probe** (`tests/determinism/test_textfit_parity.py`, slow). Added in Dynamic video batch B2b-H,
  replacing B2b's kerning table. The text fit (`aadhi/compose/dynamic/textfit.py`, `shaping.py`) shapes text with
  HarfBuzz on the vendored faces. The probe runs the real `/render-dynamic` page in Playwright's Chromium, over every
  vendored face and weight the stage draws, in Latin, the five Indic scripts and mixed text. It checks that each text
  is drawn within 1/16 px + 0.05% of its shaped width, and within textfit's own margins (`DYNAMIC_VIDEO_DESIGN.md` §4.6
  "As built (B2b-H)"). It needs `web/vendor` and Playwright's Chromium.
  - **Default form** (about 2 minutes): runs in the slow suite, so in CI's slow job and the container job.
  - **Extended form** (every 4 px size of each slot's range and 6,000 random runs per Latin style: about 3.9 million
    word widths, 5.5-7.5 minutes): runs only on demand, because of the owner's rule of no many-minute measurement in
    CI:

    ```bash
    AADHI_TEXTFIT_PARITY_FULL=1 .venv/Scripts/python.exe -m pytest -m slow tests/determinism/test_textfit_parity.py -s --basetemp=C:/t/parity
    # PowerShell: $env:AADHI_TEXTFIT_PARITY_FULL=1; .venv/Scripts/python.exe -m pytest -m slow tests/determinism/test_textfit_parity.py -s --basetemp=C:/t/parity
    ```

    `-s` prints the max and median residual per style and size.

  Run the extended form after any of these:
  - a vendored face changes;
  - a `uharfbuzz`, `fonttools` or `brotli` bump (this is why `requirements.txt` bounds `uharfbuzz` to `<0.57`);
  - a Playwright (Chromium) bump;
  - a change to how `textfit.py` or `shaping.py` shapes.

  A failure means the page and HarfBuzz no longer agree. Read the printed residuals before touching the margins.

**Classic invariance (from Dynamic video batch B0).** The classic (Aadhi presenter) output must not change while
the Dynamic mode is built: `tests/invariance/` holds byte-for-byte goldens of the classic screenplay, scene hashes,
timelines and every ffmpeg command line (part of the fast suite; the render's command lines also with burned-in
captions and with the soft caption track), and a differential pixel test that renders the showcase lecture with the
merge base and with HEAD and compares the frames within measured tolerances (`tests/invariance/README.md`;
`python tests/invariance/differential.py --base-ref origin/main --out OUT`; a render that fails is a failure, never
compared through an earlier run's files). A change of the classic output on purpose carries the `classic-change` PR
label and regenerated goldens (`AADHI_UPDATE_GOLDENS=1`). CI reads the label at run time, so adding it re-runs the
check and the push after the merge inherits it; a direct push without a PR carries a reviewed
`Classic-Change: approved` commit trailer instead.

**Container suites.** The Dockerfile's `test` target is the runtime image plus the test tools (never deployed). CI's
`container` job builds it and runs, against the checkout mounted at `/src`, the fast suite, the ffmpeg 5.1 feature
checks (`pytest -m container`; the image sets `AADHI_EXPECT_FFMPEG=5.1`, so a base image that moves Debian's ffmpeg
fails) together with the Dynamic determinism suite of B1 (`tests/determinism/test_frame_hashes.py`: 300 frames of the
real stage as 1x300, 3x100 and in reverse give identical hashes; it sets `DYNAMIC_ENABLED` itself and uses the image's
Chromium and the checkout's `web/vendor` fonts; `test_kenburns_determinism.py` does the same for a Ken Burns still
and `contain_blur` stills and clips), the differential pixel test (a second one with burned-in captions when the
caption path changed) and the rest of the slow suite. The image sets `AADHI_CONTAINER_TESTS=1`: there a missing tool
or bundle (ffmpeg / ffprobe, Playwright, its Chromium, the Dynamic stage bundle; `tests/compose/dynamic/helpers.missing`,
also the live app of `tests/api/test_render_dynamic_route.py` and the tools of `tests/invariance/test_render_slow.py`)
fails the B1 suites instead of skipping them, so the job cannot pass green by skipping; on a developer machine they
skip as before. Make "Container suites (Docker test image)" a required check in the repository's branch protection.
From B2b-H on, the fast suite there runs these face checks:
- `tests/compose/dynamic/test_textfit.py` pins the HarfBuzz-shaped widths of every vendored WOFF2 face. These do not
  depend on the platform: fontTools decodes the files, and the pinned `uharfbuzz` wheel bundles its own HarfBuzz.
- `test_faces.py` still opens each face with Pillow, as a readability check.

The slow suite there also runs the text-fit parity probe (above), which will be its first run against Linux Chromium.
The job has not run yet.
Locally (needs a Docker daemon; in PowerShell write `${PWD}`, put the command on one line and leave out `--user`,
which uses the POSIX `id`):

```bash
docker build --target test -t aadhi-eduengine:test .
docker run --rm --shm-size=1g --security-opt seccomp=docker/app/chromium-seccomp.json \
  --user "$(id -u):$(id -g)" -v "$PWD:/src" aadhi-eduengine:test python -m pytest -m "container and not network"
```

After changing `requirements.txt` / `requirements-dev.txt`: install, run the tests, then
`scripts/lock_requirements.py` rewrites the lock. `pip freeze` cannot see Linux-only dependencies on
Windows (for example `uvloop` from `uvicorn[standard]`); they are pinned with environment markers in
`PLATFORM_PINS`, and the script fails when the Linux dependency closure needs one that is missing.

Dynamic video batch B2b-H added three runtime dependencies for the text fit (the "Dynamic video text fit" block of
`requirements.txt`):

| Package | Bound | Locked | Licence |
|---|---|---|---|
| `uharfbuzz` | `>=0.56.2,<0.57` | 0.56.2 | Apache-2.0; bundles HarfBuzz 14.5.0 under its "Old MIT" licence |
| `fonttools` | `>=4.66,<5` | 4.66.1 | MIT |
| `brotli` | `>=1.2,<2` | 1.2.0 | MIT |

- **Wheels.** Each publishes CPython 3.11 wheels for `win_amd64` and manylinux x86_64 (`uharfbuzz` as cp310-abi3).
  None has required dependencies, none needed a `PLATFORM_PINS` entry, and the Docker image installs them with the
  rest of `requirements.txt`.
- **The `uharfbuzz` bound.** It stays within the 0.56 series on purpose: a minor release can change shaping. Raise the
  bound only after the extended text-fit parity probe passes with the new version.
- **`brotli` and the HTTP clients.** Once `brotli` is installed, `httpx`, `urllib3` and `aiohttp` import it and
  advertise `br` in `Accept-Encoding`; responses are decoded transparently.
- **Classic isolation.** A classic build must still load none of `uharfbuzz`, `fontTools` or `brotli`
  (`tests/compose/test_dynamic_isolation.py`). `brotli` is excused only beside an HTTP client that imports it.
- **Notices.** Their licence notices are not in `THIRD_PARTY_NOTICES`, which covers npm packages and `web/vendor` only.
  This is an open point for the owner (`DYNAMIC_VIDEO_DESIGN.md` §4.12).

Tests always force fake providers and blank every API key (`tests/conftest.py`), even when your
`.env` holds real keys. They also pin the motion engine's settings to their defaults (`DYNAMIC_ENABLED=false`,
`RENDER_MOTION_WORKERS=0`, `RENDER_CPU_SLOTS=0`, `RENDER_SLOT_DIR` empty, `MOTION_X264_PRESET=veryfast`,
`MOTION_X264_CRF=17`), so a developer `.env` cannot change what a test builds or renders; tests that need another
value set it on their settings. Tests that need the network are marked `network` and only run with
`AADHI_NETWORK_TESTS=1`.

Slow suites that need more than Python: the Dynamic suites (`tests/determinism` and the slow tests of
`tests/compose/motion` and `tests/compose/dynamic`) need ffmpeg and Playwright's Chromium and skip without them
outside the Docker test image. `tests/web/test_player_controls_fit.py` (the control bar of a reel's box, in real
Chromium) needs Playwright with its Chromium and the vendored fonts from `npm ci` (`web/vendor`, git-ignored); it
skips without Playwright or `web/vendor`. On Windows, run the slow motion tests with a short `--basetemp` (for example
`pytest -m slow --basetemp C:\t\pt`): their work files under pytest's default temp path can pass the 260-character
`MAX_PATH`. B2b added three slow Chromium files: `tests/determinism/test_stage_fonts.py`, `test_icon_pixels.py` and
`test_textfit_page.py`. B2b-H adds `test_textfit_parity.py` (about 2 minutes); its extended form (5.5-7.5 minutes) runs
only with `AADHI_TEXTFIT_PARITY_FULL=1`. B2b's `test_kerning_table.py` and `AADHI_KERNING_REMEASURE` are gone.
The fast suite's face tests (`tests/compose/dynamic/test_faces.py`, `test_textfit.py`) read the vendored faces in
`web/vendor`: run `npm ci` first. `test_textfit.py` also needs `uharfbuzz`, `fonttools` and `brotli` from
`requirements.txt`. Without `web/vendor` they skip; inside the Docker test image `test_textfit.py`
fails instead (`tests/compose/dynamic/helpers.missing`).

## 3. Configuration

Every setting is an environment variable (or a line in `.env`; real environment variables win).
The complete, commented list with defaults is **`.env.example`**, generated from
`aadhi/config.py` by `python scripts/gen_env_example.py` (CI fails if it is stale). Secrets
(`*_API_KEY`, `JWT_SECRET`, `CREDENTIALS_ENCRYPTION_KEY`, `DATABASE_URL`, `ADMIN_PASSWORD`,
`S3_*`) are `SecretStr` values: they never appear in logs, error messages or job events.

`APP_ENV=production` refuses to start with an unsafe configuration (`Settings.validate_for_runtime`):
`JWT_SECRET` shorter than 32 characters, a non-https `BASE_URL` or CORS origin, free-form Manim
without the docker sandbox, a missing `DATABASE_URL`, local storage inside the source tree,
`WORKER_MODE=inline` on PostgreSQL, or an offline stand-in provider (`*_PROVIDER=fake`, `fake` in
`*_FALLBACK_PROVIDERS`) unless `ALLOW_FAKE_PROVIDERS=true` (offline demos only).

Bounded settings fail validation at startup when a `.env` value is outside its range, e.g.
`MANIM_TIMEOUT_SECONDS` (10-900), `MANIM_MAX_CONCURRENT` (0-16), `MANIM_MEMORY_LIMIT_MB` (256-8192),
`MANIM_MAX_WORKSPACE_MB` (64-4096), `MANIM_MAX_OUTPUT_MB` (16-2048); the caps are generous for
480p-1080p renders.

Lecture look and checks (each takes effect when a timeline is built again, unless noted):

* `SYNC_WORD_ANCHORS` (on): formula legend rows, terminal output, side-panel focus pulses and authored
  highlights follow the spoken word, in the player and the MP4 alike; off = beat-start timing as before.
  An authored highlight is re-timed only when its beat names it at least 1.2 s before the beat ends and
  the previous beat did not already light it; otherwise it stays lit for the whole beat.
  `SYNC_AUTO_EMPHASIS` (off) also highlights a visible board item when the narration names it (at most
  2 per beat, never over an authored highlight).
* `MASCOT_CUES` (on): the small bubble beside Aadhi (thinking dots in long pauses and the quiz
  countdown, "?" while he asks, a gold check at the reveal), drawn identically in preview and MP4.
  Applies to every lecture at once when its timeline is served (no rebuild needed; the timeline ETag
  changes with it), so the player and a new MP4 always agree.
* `QUALITY_AI_TERMINOLOGY` (off): one fast-model call per generated lecture asks about at most 8
  ambiguous term pairs; answers are notes only and count toward the job budget. The deterministic
  quality checks always run and never call a model.

Dynamic video (batch B0 lays the foundations; nothing changes for classic lectures):

* `DYNAMIC_FEATURES` (empty): the Dynamic-video features this server accepts in edits and imports, a comma list
  of `presentation`, `items_v3`, `cuts`, `overlays`, `clip`, `voice_override` (unknown names fail at start-up).
  Every Dynamic field is read and kept from B0 on, but a save, lint, timeline preview or import that uses a
  feature not listed is refused (422 `feature_unavailable`), except a feature the stored lecture already uses,
  which stays editable after it is switched off. Leave it empty until the batch that ships a feature says so.
* Lectures, timelines and asset manifests that use a Dynamic field are stored with `schema_version` 3 (classic
  ones keep 2). A build older than B0 would drop those fields and write the lecture back without them, so **B0
  is the rollback floor**: once a version-3 document exists, never deploy a build older than B0 (section 4.8).
  A newer build's documents are refused instead of misread: the API answers 409 `newer_document` and a job
  fails with `error_code` `newer_document` without running.

Dynamic video, the motion render engine (batch B1; section 4.2 "Dynamic videos" for sizing and the benchmark):

* `DYNAMIC_ENABLED` (off): the admin switch. On: a lecture with a `presentation` gets a Dynamic timeline and its MP4
  is captured frame by frame from `/render-dynamic` by the motion engine. Off: `/render-dynamic` answers 404 and such
  a lecture is built, played and rendered as a classic lecture (classic intro, `RENDER_FPS`, classic captions), with
  one exception: its stage still follows `presentation.format` (`aadhi/compose/timeline.py`, a B0 decision), so a
  portrait presentation gives a classic timeline on a 1080x1920 stage, rendered as a portrait classic MP4 while the
  player shows it in its 16:9 box. Classic lectures never change. Flipping it takes effect at once, without
  rebuilding: a build of the current revision made under the other value is served rebuilt with the current flag
  (the Studio and watch players, public shares, the render page; the timeline ETag carries the flag), exactly as the
  render job builds it, so the player and the MP4 always agree. A build that is already stale (the lecture was edited
  since) is served as built until it is built again, except a Dynamic one while the flag is off, which is served
  rebuilt as classic. Set it alike on the API and on every render worker: the worker builds the timeline it renders
  with its own settings, while the player and `/render-dynamic` are served by the API, and the API decides from its
  own flag whether a render request without `burn_captions` leaves the choice to the lecture's presentation. A worker
  with the flag off would render such a lecture classic with no `burn_captions` in its payload, and the classic job
  then burns captions as `RENDER_BURN_CAPTIONS` says instead of leaving them off.
* `RENDER_MOTION_WORKERS` (0 = auto): capture workers per motion render; auto is `min(6, CPU quota - 1, physical
  cores - 1, cpuset - 1)` read from the container's cgroup and, on Linux, the process's CPU affinity (Python's
  `os.cpu_count()` sees the host's cores, not the quota).
* `RENDER_CPU_SLOTS` (0 = physical cores - 1) and `RENDER_SLOT_DIR` (empty = `<render work dir>/cpu-slots`): every
  worker of every motion render on the host holds one slot (a lock file `slot-NNN.lock`); a render waits for at least
  one free slot and starts with what it got, so concurrent renders never oversubscribe the machine. Point every
  render container of one host at the same directory on a local filesystem (lock files on a network share are not
  reliable); the OS releases a crashed process's slots.
* `MOTION_X264_PRESET` (`veryfast`; `ultrafast` ... `slow`) and `MOTION_X264_CRF` (17): the chunk encoder. Encoding
  shares the cores with the capture; every chunk uses the same parameters (they are part of the chunk cache key).

Media library and Visual Review:

* `LIBRARY_AUTO_SAVE_GENERATED` (on): pictures and clips a build generates also join the library of
  the lecture's owner when the owner started the build (an admin's build of someone else's lecture
  saves nothing), titled from the prompt. Bookkeeping
  only: a failure is logged and never fails the build. Uploads always join the uploader's library.
* `LIBRARY_AI_DESCRIBE_ENABLED` (off): "Suggest with AI" on the Library page asks the default AI
  engine's fast model for a title, description and keywords of one item (it sees a scaled picture or
  one frame of a clip). Each call is billed and counted against the daily budget like any model call
  (unless the user's own key pays), 6 a minute per user. Nothing is saved until the teacher saves it.
* The cleanup job's asset GC keeps every asset that is in somebody's library (`library_items`).
* "Generated vs edited" keeps the screenplay exactly as Aadhi wrote it as one private `snapshot` asset per
  generated or translated version (`generation_meta["generated_snapshot_key"]`, a few KB of JSON; identical
  screenplays share one blob). The cleanup GC never collects this kind; deleting a project leaves it like
  the project's other assets. Versions generated before batch 4 and imported lectures have none, so their
  editor shows no change markers. Nothing needs to be configured.
* `GET /api/videos` (the Studio's Videos page) lists each teacher's own renders; its preview links are
  served like timeline media (capability URLs, or presigned at serve time on S3 without a CDN).
* `GET /api/admin/media-cache?days=30` (admins) shows how many pictures and clips were generated,
  billed and reused from other lectures, and the library sizes.
* `SCRATCH_DIR` (empty = `<system temp>/aadhi`, `aadhi-<uid>` on POSIX): the private root of the app's
  temporary work dirs (Manim renders, the sandbox health probe). Host sweeps look only inside it, never
  at other folders of the system temp dir. On POSIX the default root is created 0700 and a symlink or
  another user's directory there is refused; an explicit `SCRATCH_DIR` is only created when missing.

### 3.1 API keys saved in the Studio

Gemini, OpenAI and Anthropic keys can be saved in the Studio as well as in `.env`. Admins save
**server keys** on the Admin page; they are used for everyone and replace the matching `.env` key.
Every user can save **personal keys** on the API keys page; they are used only for jobs that user
starts. For a job, each provider's key is the starter's personal key, else the Studio server key,
else the `.env` key. A Gemini key saved in the Studio replaces the whole `.env` Gemini pool
(`GEMINI_API_KEY` and `GEMINI_API_KEYS`), so there is no 429 key rotation while it is in use.

| Setting | Default | Meaning |
|---|---|---|
| `STORED_API_KEYS_ENABLED` | `true` | Master switch for keys saved in the Studio, server and personal. `false` ignores every saved key and refuses new ones; only `.env` keys are used. |
| `USER_API_KEYS_ENABLED` | `true` | Allow personal keys. `false` ignores and refuses personal keys; server keys still work. |
| `CREDENTIALS_ENCRYPTION_KEY` | empty | Fernet key that encrypts saved keys. Empty derives one from `JWT_SECRET`, so rotating `JWT_SECRET` then makes saved keys unreadable. Set it in production. |

Generate the encryption key once and keep it in the secret store of every API and worker process
(each process decrypts keys itself):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Changing `CREDENTIALS_ENCRYPTION_KEY`, or `JWT_SECRET` while it is empty, never stops the server.
Saved keys that can no longer be decrypted are treated as not saved (jobs use the next source in
the order above), and the Studio marks them for re-entry. Details: `docs/SECURITY.md` §3.7.

### 3.2 Generated images and AI video: provider chain

`IMAGE_PROVIDER` / `VIDEO_PROVIDER` name the preferred provider (`none` switches the medium off,
backups included). `IMAGE_FALLBACK_PROVIDERS` (gemini, pollinations, fake) and
`VIDEO_FALLBACK_PROVIDERS` (veo, omni, fake) list backups, tried in order when the preferred provider is
not configured, cooling down, or fails in a way another provider may not (payment or account
refusal, outage, rate limit, unusable output). The chain never falls back after a safety refusal or
a refused personal key. **A paid backup costs money**: a Gemini image backup bills the server
Gemini key, or the teacher's own key when they saved one, and counts in the budget pre-check.
Teachers may pick the image provider per lecture (*Image provider* in the options form) among the
configured ones.

* Pollinations needs no key, but its endpoint may ask for payment or an account (HTTP 402). That is
  treated as permanent for the provider: the scene keeps its hash (not retried every build) and the
  provider cools down. If your Pollinations images stop, set `IMAGE_FALLBACK_PROVIDERS=gemini`.
* Only a failure of the provider call itself moves on to the next provider: a storage, claim or
  database failure after the media was generated fails the scene (retried by the next build) without
  paying a backup.
* Cooldowns are per worker process and per key scope: `MEDIA_PROVIDER_COOLDOWN_SECONDS` (60) after a
  rate limit, outage, timeout or unusable output, `MEDIA_PROVIDER_AUTH_COOLDOWN_SECONDS` (600) after
  HTTP 401/402/403. A cooling provider is tried last, never dropped. The Admin page's "Media
  providers" panel shows each chain, its state and cooldowns (configuration only, no provider call).
  With inline workers those are the API process's own cooldowns; with external workers
  (`WORKER_MODE=external`) the panel reads the cooldowns the workers logged in recent job events,
  so a provider that recovered meanwhile still shows until its cooldown window ends.
* A lecture's own image provider choice is honoured even when the server default made the same image
  before: that earlier image is reused only when the chosen provider fails.
* Output limits: `AI_MAX_IMAGE_BYTES`, `AI_MAX_VIDEO_BYTES`; flat or mis-shaped images are kept and
  flagged (`assets.media_suspect`). `VEO_DURATION_SECONDS` requests a clip length (billed per second;
  Veo 3.x makes 4, 6 or 8 s clips and other values round up; empty = the model's default, 8 s). The
  default `VEO_MODEL` is a preview with an announced earliest shutdown: see section 3.3.
* Restarts and Veo: the operation is saved in the job before the paid request and as soon as Veo
  accepts it, so a restarted (or retried) job polls the same operation instead of paying again; its
  charge is noted as soon as it is reported, so a restart during the download never bills it twice.
  Collecting such a clip is not blocked by the lecture or daily budget (its charge was already
  counted). Only a request still waiting for Veo's answer is outstanding (`submitting`): when Veo
  answered with an error (429, 5xx) the record is `pending` and a restart during the retry wait
  submits normally. A submission whose answer was never saved is **not** re-submitted automatically
  (the scene shows its still with `video.ambiguous_submission`); an explicit rebuild of that scene
  generates it once.
* **Gemini Omni** (`omni`, `OMNI_MODEL`, default `gemini-omni-1.1-flash`) is the successor Google names for the
  Veo 3.1 previews, which it lists for shutdown from **2026-10-22**. It runs on the same Gemini key as Veo (the
  server's, or the teacher's own) through the Gemini Interactions API. While `VIDEO_PROVIDERS` is empty, a video
  chain that names `veo` gets `omni` appended as its last backup, so clips keep coming once Veo is gone. That
  automatic successor is used **only while Veo is unavailable**: Veo answering 404 / 410 (a model no longer
  served) or refusing the account or payment (401 / 402 / 403) bills nothing, counts as unavailable and moves Veo
  behind Omni for `MEDIA_PROVIDER_AUTH_COOLDOWN_SECONDS`. After any other Veo failure (a poll timeout, a rate
  limit, a server error, unusable output) the scene shows its still image, as before Omni existed: a Veo job that
  was accepted may still finish and bill, so a second paid clip is never started for it, and a short Veo cooldown
  never puts Omni first. To use Omni after those failures as well, name it: `VIDEO_FALLBACK_PROVIDERS=omni` or
  `VIDEO_PROVIDERS=veo,omni` (a timed-out Veo job may then be billed next to the Omni clip; both charges count in
  the budgets). `VIDEO_PROVIDERS` (e.g. `veo,omni`, `omni`, or `none`) sets the exact order and replaces
  `VIDEO_PROVIDER` + `VIDEO_FALLBACK_PROVIDERS`; `VIDEO_PROVIDERS=veo` turns the automatic successor off. Omni is
  billed per output token, about USD 0.10 per second of 720p video (section 4.6); the budget check before a clip
  assumes 10 s. Its background jobs need Google to store the interaction (kept 55 days on the paid tier, 1 day on
  the free tier, which has no Omni access anyway); the job id is what a restarted worker resumes, with the same
  checkpoint rules as Veo below. Omni clips are keyed by `OMNI_MODEL` (Veo's by `VEO_MODEL`), so a new Omni model
  means new clips. Each job (its own event loop) uses its own Omni connection, closed when the job's last clip is
  done.
* Paid generations are claimed across worker processes (`asset_claims`, `GENERATION_CLAIM_*`), so two
  workers never pay for the same image or clip; a claim of a crashed worker expires after
  `GENERATION_CLAIM_TTL_SECONDS` and is taken over.
* When a medium is off or not configured, images and clips generated earlier for the same request
  are still used (info issue `assets.cached_media_used`), unless the lecture opted out. The request
  includes `IMAGE_MODEL` (Gemini) / `VEO_MODEL` (`OMNI_MODEL` for Omni clips); media made under the defaults before
  the 2026-10 model refresh is still found while these are left at their defaults (section 3.3).

### 3.3 AI models

Defaults as checked against Google's and OpenAI's model, deprecation and pricing pages on 2026-10-10
([Gemini models](https://ai.google.dev/gemini-api/docs/models),
[Gemini deprecations](https://ai.google.dev/gemini-api/docs/deprecations),
[Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing),
[OpenAI deprecations](https://developers.openai.com/api/docs/deprecations),
[OpenAI pricing](https://developers.openai.com/api/docs/pricing)). Prices are per 1M tokens unless
noted; `aadhi.usage.pricing` holds them (section 4.6).

| Setting | Default | Why, and the alternatives |
|---|---|---|
| `LLM_MODEL_PLAN`, `_SCRIPT`, `_CRITIC`, `_FAST` | `gemini-3.8-flash` | Stable (GA) and Google's recommendation for new projects; Gemini 2.5 models are served only to keys that used them before. $0.75 / $3.75, doubling to $1.50 / $7.50 on 2027-01-01. Planning uses it as well: `gemini-3.1-pro-preview` ($2 / $12) is still a preview, so it is an opt-in (`LLM_MODEL_PLAN=gemini-3.1-pro-preview`). `gemini-3.5-flash-lite` ($0.30 / $2.50) is a cheaper fast tier. |
| `GEMINI_TTS_MODEL` | `gemini-3.8-flash-tts` | The 2.5 preview TTS models shut down on 2026-11-17. About $0.014 per minute of speech (double from 2027-01-01); `gemini-3.8-flash-lite-tts` costs two thirds of that. A retired or misspelt model (HTTP 404 / 410) no longer silences lectures: the adapter raises `ModelUnavailable` after that one request (not retried, nothing billed), and builds narrate with the free Edge voice for the lecture's language with an `assets.tts_fallback` warning until the setting is fixed; a rebuild then restores the chosen voice. Without Edge on the server the scenes fail with `assets.tts_failed`. |
| `OPENAI_TTS_MODEL` | `gpt-4o-mini-tts` | **Every model `/v1/audio/speech` accepts is scheduled to retire on 2027-01-06**: `tts-1`, `tts-1-hd` and the dated `gpt-4o-mini-tts-2025-*` snapshots by name, and the undated `gpt-4o-mini-tts` alias defaults to `gpt-4o-mini-tts-2025-12-15`, which is itself on that list. OpenAI names `gpt-realtime-2.1-mini` as the replacement, but it supports only `v1/realtime` (not `audio/speech`, Chat Completions or Responses). Before 2027-01-06, move deployments and lectures that use OpenAI voices to `TTS_PROVIDER=gemini` or `edge`, or build a Realtime-API TTS adapter. After the retirement OpenAI answers `/v1/audio/speech` with HTTP 404 `model_not_found` (or 410); the adapter raises `ModelUnavailable` after that one request (not retried, nothing billed), and the asset build narrates with the free Edge voice for the lecture's language and adds one lecture-level `assets.tts_fallback` warning, as for a retired `GEMINI_TTS_MODEL`. OpenAI gives the same 404 when the API key has no access to the model, and the Edge fallback with its warning applies there too. Without Edge on the server the scenes still fail with `assets.tts_failed`. The advice to move stands: Edge is only a fallback, and each build first makes one unbilled 404 request per beat already in flight before it switches. $0.60 text + $12 audio per 1M tokens (about $0.015 per minute). |
| `IMAGE_MODEL` | `gemini-nano-banana-2.1` | Stable (released 2026-10-06), recommended for new projects and listed as the successor of `gemini-3.1-flash-image`, at half its price ($0.034 per 1K image against $0.067). It always thinks (default level `medium`, it cannot be turned off): the prompt ($1.50) and the thinking tokens ($7.50 per 1M) are billed on top of the image and are counted in budgets. `gemini-3.1-flash-lite-image` is the low-latency option at the same price per image; `gemini-2.5-flash-image` is limited-access (shutdown from 2027-03-15) and Imagen was shut down on 2026-08-17. |
| `VEO_MODEL` | `veo-3.1-fast-generate-preview` | Veo 2 and 3.0 were shut down on 2026-06-30. Veo 3.1 Fast costs $0.10 per second at 720p ($0.80 per 8-second clip); `veo-3.1-generate-preview` is $0.40/s and `veo-3.1-lite-generate-preview` $0.05/s (Lite takes no reference images). **All Veo 3.1 models are previews with an earliest shutdown of 2026-10-22**; Google names `gemini-omni-1.1-flash` as the replacement, supported as the `omni` video provider (next row, section 3.2): a chain with `veo` switches to it automatically once Veo is no longer served (not after a Veo timeout or rate limit). |
| `OMNI_MODEL` | `gemini-omni-1.1-flash` | Gemini Omni video, served only through the Interactions API. $17.50 per 1M video output tokens at 5,792 tokens per second of 720p video (about $0.10 per second, like Veo 3.1 Fast), $1.50 per 1M input tokens; paid tier only. `gemini-omni-flash-preview` is priced the same. The adapter sends no clip length (the model picks it, up to 10 s) and writes Veo's exclusions into the prompt (Omni has no negative prompt). |
| `OPENAI_MODEL_*` | `gpt-4.1` / `gpt-4.1-mini` | Unchanged: neither is on OpenAI's deprecation list. The current reasoning models (`gpt-5.6-sol`, `-terra`, `-luna`, `gpt-6-*`) work when configured. |
| `ANTHROPIC_MODEL_*` | `claude-opus-5-5` | Unchanged. |

How the adapters treat the current models:

* Gemini 3 and later get no `temperature` (Google asks to keep the default 1.0; lower values may loop,
  and 3.8 Flash ignores sampling parameters); Gemini 1.x / 2.x still get the caller's value. Thinking
  stays at the model default (`medium` on 3.8 Flash) and is billed as output.
* OpenAI reasoning models (o-series, gpt-5 and later, gpt-6) get no `temperature`: OpenAI accepts
  sampling parameters only at reasoning effort `none`, and the adapter leaves the effort at its default.
* Gemini 3.8 TTS answers with WAV (earlier models with raw PCM, which is still wrapped). Every voice in
  the catalogue is one of its 30 prebuilt voices.
* Gemini 3 image models may return interim "thought" images before the final one; only the final
  image is kept.
* Veo 3.x: 16:9 by default and 9:16 on request, 720p, 4, 6 or 8 s (8 with a reference image, which
  becomes an asset reference on 3.1 / 3.1 Fast and the first frame on Lite). Veo 3 always adds an
  audio track; the player and the MP4 keep B-roll muted.

Changing a model affects new generations only. Stored lectures keep their audio, pictures and clips:
asset keys include the model, so nothing stored is invalidated or regenerated by itself.

Pictures and clips: a scene that is built again looks its picture or clip up under the configured
`IMAGE_MODEL` / `VEO_MODEL`. While these are left at their defaults, media made under the defaults before
this refresh (`gemini-2.5-flash-image`, `veo-2.0-generate-001`) is looked up as well, so it is reused
instead of paid for again, and it is still shown when the medium is off (`IMAGE_PROVIDER` /
`VIDEO_PROVIDER=none`). New media is always made with the configured model. An `IMAGE_MODEL` /
`VEO_MODEL` set to any other value is your own choice of model: scenes built again under it get new,
paid pictures and clips.

Narration: a lecture voiced by Gemini or OpenAI TTS keeps its narration until it is built again. Its
next full asset build (`POST .../build` with `scene_ids: null`, the review's "Build changed scenes")
re-synthesises the narration of every scene with the new model, at TTS cost, even though no scene is
shown as changed (scene hashes do not cover the voice model). The same holds after any `*_TTS_MODEL`
change. A build of only some scenes (Visual Review `retry` / `new_version`, or `POST .../build` with
`scene_ids`) re-voices only those scenes and reuses the others as they are. The lecture, and a render
made from it, then mixes narration from the old and new models until its next full build. If that
matters, pin `GEMINI_TTS_MODEL` / `OPENAI_TTS_MODEL` to the previous value until a lecture has been fully
rebuilt (possible only until that model shuts down: see the table above). Edge narration, and the
generated pictures and clips of unchanged scenes, are reused.

## 4. Production deployment (Docker Compose)

```
            https                       +-------------+      +-----------+
 browser ---------> reverse proxy ----> |    api      | ---> | postgres  |
                    (TLS, Caddy/nginx)  +-------------+      +-----------+
                                              |   shared /data volume (or S3/R2 + CDN)
                         +--------------------+---------------------+
                         |                                          |
                  +-------------+                          +----------------+
                  |   worker    |  generate / build /      | render-worker  |  render_video only
                  | (N replicas)|  translate / import      | (Chromium +    |
                  +-------------+                          |  ffmpeg)       |
                                                           +----------------+
```

### 4.1 First deployment

1. Create `.env` next to `docker-compose.yml` (never commit it):

   ```bash
   POSTGRES_PASSWORD=<long random>
   JWT_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(48))">
   BASE_URL=https://lectures.example.edu
   ADMIN_PASSWORD=<temporary, rotate after first login>
   CREDENTIALS_ENCRYPTION_KEY=<python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
   GEMINI_API_KEY=...            # and/or OPENAI_API_KEY, ANTHROPIC_API_KEY, ELEVENLABS_API_KEY
   ```

   `.env` is also passed to every app container (`env_file`), so any setting from `.env.example`
   can be set there. Prefer your platform's secret store for keys where available. The Gemini,
   OpenAI and Anthropic keys can instead be saved by an admin on the Studio's Admin page after the
   first login (section 3.1).
2. `docker compose build` (multi-stage image: `npm ci` vendor bundle, Python venv from
   `requirements.lock`, ffmpeg, TeX Live for MathTex, Noto + Indic fonts, Playwright Chromium,
   non-root user `aadhi`, tini as PID 1). From Dynamic video batch B2b, the `web` stage also copies
   `scripts/vendor_faces.mjs` and `web/js/dynamic/faces.gen.js`, which `vendor.mjs` imports (`tests/evals/test_tooling.py`
   checks that the stage copies every local module it imports), and the runtime image carries `THIRD_PARTY_NOTICES` at
   `/app`. B2b-H adds three Python packages (`uharfbuzz`, `fonttools`, `brotli`; section 2) to the venv stage. The image
   build has not been run since B2b.
3. `docker compose up -d`. The API runs `python -m aadhi.cli migrate` on start (`RUN_MIGRATIONS=1`,
   retried while Postgres starts) and workers wait for the API health check, so migrations always
   finish before any job runs. Only the API sets `RUN_MIGRATIONS`.
4. Put a TLS reverse proxy in front of `127.0.0.1:8000` (`AADHI_PORT`). Example Caddyfile:

   ```
   lectures.example.edu {
       reverse_proxy 127.0.0.1:8000
       request_body { max_size 60MB }
   }
   ```

   A proxy on the host reaches the API through the published port, so the containers see the
   compose network's bridge gateway as the client, not `127.0.0.1`. The compose file pins that
   network to `AADHI_SUBNET` (default `172.30.0.0/24`) with gateway `AADHI_GATEWAY` (default
   `172.30.0.1`), and both `FORWARDED_ALLOW_IPS` (uvicorn `--proxy-headers`) and `TRUSTED_PROXIES`
   (app rate limits / client IP) default to that gateway. Without this, every user would share the
   gateway's per-IP login and API rate limits. If `172.30.0.0/24` collides with a local network, set
   `AADHI_SUBNET` and `AADHI_GATEWAY` together in `.env`. A proxy running in its own container on
   another network must be listed explicitly in both variables instead.
   Cookies become `Secure` with the `__Host-` prefix automatically when `BASE_URL` is https.
5. Log in as `admin` with `ADMIN_PASSWORD`, change the password, then remove `ADMIN_PASSWORD`
   from `.env` (it is only used when no admin exists). Compose does not require it afterwards;
   production still refuses to start while no admin exists and `ADMIN_PASSWORD` is empty.

Manual migration or operator commands run in a one-off container:

```bash
docker compose run --rm api python -m aadhi.cli migrate
docker compose run --rm api python -m aadhi.cli list-users
docker compose run --rm -T api python -m aadhi.cli set-password admin --password-stdin --must-change
```

### 4.2 Workers and render workers

* `worker`: `WORKER_KINDS=generate_lecture,regenerate_scene,build_assets,translate,import_legacy,cleanup`.
* `render-worker`: `WORKER_KINDS=render_video` only. MP4 rendering (Chromium screenshots + ffmpeg)
  is CPU and memory heavy; dedicated render workers keep it from starving lecture generation.
  Each render worker needs roughly 2 CPUs and 2-3 GB RAM per concurrent render
  (`RENDER_CONCURRENCY` segments in parallel) and `shm_size: 1gb`. The render worker loads the
  player from `BASE_URL`; it must be able to reach that origin. Set `RENDER_BASE_URL` (e.g.
  `http://api:8000`) when the worker should use an internal address instead: its browser is then
  allowed to reach only that origin.
* Render admission: `RENDER_MAX_PER_USER` (2) active renders per teacher and `RENDER_MAX_QUEUED` (50)
  on the server (429 `render_busy` / `render_queue_full`; 0 = unlimited). With `WORKER_MODE=inline`
  the API refuses renders on a host without ffmpeg (libx264/aac), ffprobe or Chromium (503
  `render_unavailable`); a render worker logs at start whether it can render.
* Render workspaces: `<DATA_DIR>/render-work` (`RENDER_WORK_DIR`), one folder per render; a retry
  reuses the segments already encoded. Removed on success or cancel, kept `RENDER_KEEP_FAILED_HOURS`
  (24) after a failure; a render does not start below `RENDER_MIN_FREE_GB` (2) of free disk
  (`disk_full`). Workers sweep stale workspaces at start and hourly, the `cleanup` job too. Size the
  volume for a few renders' segments.
* Every MP4 is checked before it is stored (`RENDER_OUTPUT_QA`): no picture, a wrong frame count, no
  audio track or silence despite narration fail the job with `render_invalid`; the result is shown
  per render. Other render error codes: `render_unavailable` (tools missing), `disk_full`,
  `workspace` (the workspace could not be used).
* Look of the MP4 (all on by default, each can be switched off): `RENDER_MASCOT_CONTINUITY`
  (continuous mascot loop, as the player), `RENDER_GLASS_PANELS` (the player's translucent panel
  colours; state cross-fades keep their opacity), `RENDER_COLOR_BT709` (BT.709 colours and tags;
  untagged HD media videos such as the intro logo, AI clips and Manim videos are relabelled BT.709, not
  converted; off = the old untagged output). On Windows `FFMPEG_PATH` / `FFPROBE_PATH` may be given with
  or without `.exe`.
  `RENDER_SOFT_SUBTITLES` (off) adds a selectable caption track by default; some players switch it
  on at once, which is why it is off.
* **Chromium keeps its sandbox.** As the non-root user `aadhi`, Chromium's sandbox needs user
  namespaces (`clone`/`unshare`/`setns`), which Docker's default seccomp profile only allows with
  `CAP_SYS_ADMIN`. The `render-worker` therefore runs with
  `security_opt: seccomp=./docker/app/chromium-seccomp.json`: Docker's default policy plus exactly
  those three syscalls (generated by `scripts/gen_chromium_seccomp.py`). Never use
  `seccomp=unconfined` or `cap_add: SYS_ADMIN` instead. Any other service whose `WORKER_KINDS`
  includes `render_video` needs the same `security_opt`.
* The host kernel must allow unprivileged user namespaces. Debian/Ubuntu kernels do by default, but
  Ubuntu 23.10+ restricts them through AppArmor:
  `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` (persist it in
  `/etc/sysctl.d/60-aadhi-userns.conf`). On older Debian kernels check `kernel.unprivileged_userns_clone=1`.
* Verify after every host or image change (exit 0 prints "with sandbox: ok"; a sandbox failure
  prints "No usable sandbox" with a hint):

  ```bash
  docker compose run --rm --no-deps render-worker python /app/docker/app/check_chromium.py
  ```
* Jobs are claimed atomically from Postgres (`FOR UPDATE SKIP LOCKED`), so any number of replicas is
  safe: `docker compose up -d --scale worker=3 --scale render-worker=2`. Stopping a worker is
  graceful (SIGTERM, running jobs get time to finish, then are released); a crashed worker's jobs are
  requeued by the lease reaper after `JOB_STALE_SECONDS`.
* Per-process concurrency: `WORKER_CONCURRENCY` (jobs), `LLM_MAX_PARALLEL`, `TTS_MAX_PARALLEL`,
  `MANIM_MAX_CONCURRENT`, `RENDER_CONCURRENCY`. Keep
  `processes x (DB_POOL_SIZE + DB_MAX_OVERFLOW)` below Postgres `max_connections` (default 100).
* **Dynamic videos (motion engine, from batch B1).** With `DYNAMIC_ENABLED=true` a Dynamic lecture's render runs
  preflight (seconds: the stage is loaded once and sampled frames are checked; a lecture whose text cannot be fixed
  automatically fails with `preflight_blocked` and a teacher action, before any capture), then captures every frame
  with W parallel workers (one sandboxed headless Chromium and one x264 encoder each; one shared Playwright driver),
  joins the chunks and checks the file (`render_invalid` only when an automatic remedy failed). The render worker
  needs the same Chromium seccomp profile as above. Its capture Chromium gets the classic capture's flags plus
  `--disable-partial-raster` (frames that do not depend on the order they were rendered in; no measurable cost); the
  classic capture's flags are unchanged. A remedy that captures chunks again after the file check waits for a CPU slot
  of its own, like any worker. Errors: `dynamic_disabled` (a Dynamic timeline reached a worker with the flag off),
  `preflight_blocked`, `render_invalid`, `render_unavailable`, `disk_full`. A failed attempt keeps its encoded chunks
  in the workspace, so the retry captures only the missing ones. Side panels the Dynamic stage does not draw yet
  (charts, graphs, 3D models, skill trees, quiz panels) are left out of the video with a render warning
  (`panel_not_drawn`).
* **The `motion` compose profile.** The default `render-worker` (4 CPUs, 6 GB, 1 GB shm) is too small for W workers.
  `render-worker-motion` takes the same jobs (classic renders too) with 8 CPUs, 10 GB and `shm_size: 2gb`, so run it
  instead of the default one:

  ```bash
  docker compose --profile motion up -d --scale render-worker=0
  ```

  Its CPU slots live in `/data/app/render-work/cpu-slots` on the shared appdata volume (every render container of
  the stack shares them; `RENDER_SLOT_DIR` overrides). The limits are the design's starting point; set them from the
  benchmark below run on the production host.
* **Benchmark** (`scripts/bench_motion.py`, the production capture path against the real stage; never touches
  `data/`, never calls a provider). The production image has neither `scripts/` nor the showcase fixture under
  `tests/` (the Dockerfile copies an allow-list, and `.dockerignore` leaves `tests` out), so run it with both
  bind-mounted read-only: in the container the script's root is then `/app`, and it uses the image's `web/` with its
  vendored fonts. Compose interpolates the whole file, so `BASE_URL`, `POSTGRES_PASSWORD` and `JWT_SECRET` must be
  set (in `.env` or the shell; any value does, the benchmark uses no database) even with `--no-deps`:

  ```bash
  docker compose --profile motion build render-worker-motion
  docker compose --profile motion run --rm --no-deps render-worker-motion \
      python /app/docker/app/check_chromium.py                    # the Chromium sandbox works on this host
  mkdir -p bench-out                                              # writable by the container user (uid 1000)
  docker compose --profile motion run --rm --no-deps \
      -v "$PWD/scripts:/app/scripts:ro" -v "$PWD/tests:/app/tests:ro" -v "$PWD/bench-out:/out" \
      render-worker-motion python scripts/bench_motion.py --minutes 5 --json /out/bench-16x9.json   # 5-minute lesson
  docker compose --profile motion run --rm --no-deps \
      -v "$PWD/scripts:/app/scripts:ro" -v "$PWD/tests:/app/tests:ro" -v "$PWD/bench-out:/out" \
      render-worker-motion python scripts/bench_motion.py --format portrait --minutes 3.5 --burn-captions \
      --json /out/bench-9x16.json                                                                   # 3.5-minute reel
  ```

  (PowerShell: `${PWD}` instead of `$PWD`, and the backslash continuations on one line.) Options: `--workers N`,
  `--preset faster`, `--crf 17`, `--stage stub` (engine and capture only), `--driver-per-worker`,
  `--disable-frame-rate-limit` (an experiment). It prints fps per worker and in aggregate, the phases (boot, capture,
  mux, checks), peak memory of browsers, encoders and the driver, and projections for the 5-minute lesson and the
  3.5-minute reel. Under the profile's limit of 8 CPUs the quota allows W = 6 (the host's physical cores and cpuset
  can lower it). The benchmark renders the showcase through `board_generic` only, so run it again once B2's
  templates exist (DYNAMIC_VIDEO_DESIGN §7.10).

  **Measured on the development laptop only** (i7-13650HX, 14 physical cores / 20 threads, 16.8 GB, Windows 11,
  chrome-headless-shell 153, ffmpeg 8.1.1; W = 6 auto; the same configuration varied by up to ±25% between runs).
  **The production-image benchmark under the `motion` profile is still pending** (Docker Desktop is installed on the
  development machine, but its C: drive has about 7 GB free, too little for the production image), so P1 and the
  profile limits are not set yet:

  | Run | Frames | fps per worker | Aggregate | End to end | Per video minute |
  |---|---|---|---|---|---|
  | Benchmark, 5-min 16:9, clean | 9,008 | 10.2-11.8 | 55-59 fps | 163-170 s | 33-34 s |
  | Benchmark, 3.5-min 9:16, burned captions | 6,572 | 9.0-9.6 | 48 fps | 144 s | 39 s |
  | Render job, showcase 16:9 (5.2 min, 3 clips + 1 simulation, BGM), burned | 9,356 | 6.1-9.1 | 35-48 fps | 250-281 s | 48-54 s |
  | Render job, showcase 9:16, burned | 9,356 | 8.0-11.1 | 48-60 fps | 191-206 s | 37-40 s |
  | Render job, showcase 16:9, clean + caption track | 9,356 | 8.9-9.6 | 51 fps | 193 s | 37 s |

  Fixed costs per render: preflight 4-12 s (56-289 sampled frames), boot 3-5 s, mux 0.5-1.8 s, container checks
  2-6 s. Memory: 6 browsers 1.8-4.4 GB, 6 encoders 1.7 GB (x264 `-threads 4`: 280-290 MB each), one Playwright driver
  0.2-0.3 GB (a driver per worker adds about 140 MB per worker, at no measurable speed gain), so about 0.6-1.0 GB per
  worker. One page alone: `seek()` 4-8 ms plus the CDP JPEG capture 37-55 ms per 1080p frame; the `mesh_drift`
  background's canvas accounts for 8-20 ms of the capture.

### 4.3 Manim sandbox

The default stack renders **templates only** (`MANIM_SANDBOX=subprocess`,
`MANIM_ALLOW_FREEFORM=false`): template code is generated by the app, not by the model. To allow
model-written (free-form) Manim code in production it must run in the locked-down container
sandbox (`--network none --read-only --cap-drop ALL`, memory/CPU/pids limits):

1. Build the sandbox image: `docker compose --profile sandbox build manim-sandbox`
   (`docker/manim-sandbox/Dockerfile`, tag `MANIM_DOCKER_IMAGE`).
2. Either run workers directly on a VM with Docker (`MANIM_SANDBOX=docker`,
   `MANIM_ALLOW_FREEFORM=true`), or use the `docker-sandbox` compose profile
   (`docker compose --profile docker-sandbox up -d manim-worker`). That service uses the
   `worker-docker` image target (docker CLI), mounts `/var/run/docker.sock` (root-equivalent on the
   host: use a dedicated VM) and shares `MANIM_HOST_TMP` at the same path on host and container,
   because the sandbox bind-mounts its work directory. Set `DOCKER_GID` to the host's docker group
   id.
3. Create the shared work directory on the host, owned by the container user (uid 1000), before
   starting the service; the entrypoint refuses to start when `TMPDIR` is not writable (otherwise
   Python would silently fall back to `/tmp` and the sandbox would find no output):

   ```bash
   sudo install -d -o 1000 -g 1000 -m 0750 /var/lib/aadhi/manim-tmp   # or your MANIM_HOST_TMP
   ```
4. Stop the regular `worker` from claiming generation jobs, so free-form Manim never depends on which
   worker wins a job. In `.env`:

   ```bash
   WORKER_KINDS=import_legacy,cleanup
   ```

   then `docker compose --profile docker-sandbox up -d worker manim-worker`. The `render-worker`
   keeps `render_video` regardless of `WORKER_KINDS`.

Work dirs are created inside the scratch root (`SCRATCH_DIR`, section 3). With the docker sandbox the
default root lives under the shared `TMPDIR` (`MANIM_HOST_TMP`), so the bind mount works unchanged; an
explicit `SCRATCH_DIR` must also be on that host-shared path.

Hardening inside every sandbox (also the subprocess runner): a runtime audit hook refuses network,
process spawning (except LaTeX helpers) and file access outside the work, media and temp dirs
(`MANIM_AUDIT_HOOK`, on); a frame budget and fixed resolution/fps; a disk watchdog
(`MANIM_MAX_WORKSPACE_MB`), a memory ceiling (`MANIM_MEMORY_LIMIT_MB` on RSS; on Windows the Job
Object only adds a looser committed-memory backstop; `--memory` / `--cpus` with docker,
`MANIM_DOCKER_CPUS`), a POSIX CPU backstop of `4 x MANIM_TIMEOUT_SECONDS + 30` CPU-seconds, a process cap on Windows
(`MANIM_MAX_PROCESSES`) and an output cap (`MANIM_MAX_OUTPUT_MB`). A failed animation tells the
teacher why in plain words (time limit, memory, frame limit, LaTeX, blocked action, ...). Workers
remove leftover Manim temp dirs of dead processes (inside the scratch root only) and docker sandbox
containers past their deadline at start and hourly (the `cleanup` job too). Leftover `aadhi-render-*`,
`aadhi-manim-*`, `aadhi-m-*` and `aadhi-d-*` folders that older versions left directly in the system
temp dir are no longer swept; delete them once by hand.

Check the sandbox after every host or image change (booleans only, no paths; exit 1 when it is
unavailable or the probe reached the network, saw secrets or read a host file outside its work dir,
`files_denied`). The probe runs with the audit hook exactly as renders do, so with
`MANIM_AUDIT_HOOK=false` it says so and usually fails the file check:

```bash
python -m aadhi.cli manim-check            # or --json; admins: GET /api/admin/manim/sandbox
```

### 4.4 Storage, S3/R2 and CDN

* Local storage lives in the `appdata` volume (`/data/storage`), shared by the API and all workers.
  It only works when every process runs on the same host.
* Multi-host deployments use object storage: `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_REGION`,
  `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, and for Cloudflare R2 / MinIO `S3_ENDPOINT_URL`.
  Install the optional dependency (`boto3`, already in `requirements.lock`).
* Put a CDN in front of the bucket's public `assets/` prefix and set `CDN_BASE_URL`; URLs are
  then stable and cacheable (`CDN_BASE_URL` is also added to the CSP). Without a CDN, presigned
  URLs (`S3_PRESIGN_TTL_SECONDS`) are generated at serve time. `private/` objects (sources,
  extracts, screenshots, generated-screenplay snapshots) are never public: deny public access to that
  prefix in the bucket policy.
* Test S3 locally with MinIO. MinIO's root credentials are taken from `S3_ACCESS_KEY_ID` /
  `S3_SECRET_ACCESS_KEY`, and `.env.example` leaves both empty, so set **all** of these in `.env`
  (the app and MinIO must use the same keys; empty values would give MinIO its built-in defaults
  and the app no credentials):

  ```bash
  STORAGE_BACKEND=s3
  S3_ENDPOINT_URL=http://minio:9000
  S3_BUCKET=aadhi
  S3_REGION=us-east-1
  S3_ACCESS_KEY_ID=aadhi-minio
  S3_SECRET_ACCESS_KEY=<at least 8 random characters>
  ```

  then `docker compose --profile s3 up -d minio minio-init` and restart the app services.

### 4.5 Backups and restore

* **PostgreSQL** (projects, versions, screenplays, jobs, users, usage):

  ```bash
  docker compose exec -T postgres pg_dump -U aadhi -Fc aadhi > backups/aadhi-$(date +%F).dump
  # restore into an empty database
  docker compose exec -T postgres pg_restore -U aadhi -d aadhi --clean --if-exists < backups/aadhi-2026-10-01.dump
  ```

  Run it nightly (cron/systemd timer), keep at least 14 days, copy off-site, and test a restore
  every quarter. API keys saved in the Studio are in the dump only as ciphertext (`api_credentials`);
  a restore can read them only with the same `CREDENTIALS_ENCRYPTION_KEY` (or, when that is empty,
  the same `JWT_SECRET`). Keep that key with your secrets, never next to the dumps.
* **Storage**: back up the `appdata` volume (`docker run --rm -v aadhi_appdata:/data -v $PWD/backups:/b
  debian tar czf /b/appdata-$(date +%F).tgz -C /data .`) or enable bucket versioning/replication for
  S3/R2. Assets are content-addressed and re-creatable, but sources, uploads and renders are not.
* Back up `.env` (or your secret store) separately: losing `JWT_SECRET` only logs everyone out;
  losing provider keys only stops generation; losing `CREDENTIALS_ENCRYPTION_KEY` (or `JWT_SECRET`
  while it is empty) means every key saved in the Studio has to be entered again.

### 4.6 Budgets and pricing

Every billable call is priced (`aadhi.usage.pricing`) and recorded. `MAX_COST_PER_LECTURE_USD`
caps a single generation job; `DAILY_BUDGET_USD_PER_USER` caps each user (admins can set a
per-user override). Built-in prices are approximate list prices; override them with a JSON file:

```json
{"llm": {"gemini-3.8-flash": {"input": 0.75, "output": 3.75},
         "claude-opus-5-5": {"input": 4.00, "output": 20.00}},
 "tts": {"elevenlabs": {"default": 200.0}},
 "image": {"gemini": {"default": 0.039}}}
```

and `PRICING_FILE=/data/app/pricing.json` (re-read when the file changes). Announced price changes are
built in and apply from their date (UTC): on 2027-01-01 Google doubles the Gemini 3.6-3.8 Flash and
3.8 TTS prices. A value in `PRICING_FILE` always wins over such a step, and `"scheduled": []` in the
file turns the built-in steps off. Gemini image models are priced per image plus their prompt and
thinking tokens (`image_tokens`, USD per 1M tokens, e.g.
`"image_tokens": {"gemini-nano-banana-2.1": {"input": 1.50, "output": 7.50}}`): Gemini 3 image models
always think, and Google bills that on top of the image. Claude prompt caching is
billed separately: cache writes cost 1.25x and cache reads 0.10x the model's input price unless the
model entry sets its own `cache_write` / `cache_read` price (USD per 1M tokens), e.g.
`"claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20}` (the built-in value).
Built-in prices added or re-read on 2026-10-10:

* ElevenLabs voices (elevenlabs.io/pricing/api, the same per-character rate on every plan): v4, v3 and
  Multilingual v2 $0.08 per 1K characters; v4 Turbo, v3 Conversational and Flash / Turbo $0.04; unlisted models
  $0.08. The temporary v4 promotion (until 12 Oct 2026) is deliberately not used, so budgets never under-estimate.
* Generated music, operation `music` (used from batch B3): Lyria 3.5 and Lyria 3 Pro $0.08 per song, Lyria 3 Clip
  $0.04 per 30 s clip (on the Gemini key; priced the same under the `lyria` and `gemini` provider names);
  ElevenLabs Music $0.15 per minute; procedural music free; an unknown music provider pays both rates. Override
  with `"music": {"<provider>": {"<model family>": {"unit": USD per song, "second": USD per second}}}`.
* Gemini Omni video (`omni`): $0.10136 per second of 720p video (17.50 USD per 1M video tokens at 5,792 tokens
  per second).
* **Unknown operations fail closed**: a usage whose operation has no price (a new paid call nobody priced) is
  charged every quantity it reports at the global default rates (tokens, characters, at least one unit, seconds)
  instead of nothing, and the log says `no pricing for operation '<op>'; charging the global default rates` once
  per operation and process. Add the operation to `PRICING_FILE` (or the code) when you see it. Only the `fake`
  provider stays free.

Spend is visible per user (`GET /api/usage/me`), per project (`GET /api/usage/projects/{id}`) and
for admins across all users (`GET /api/admin/usage`); per-user budgets are set with
`PATCH /api/admin/users/{id}` (`daily_budget_usd`).

Calls paid with a user's personal API key (section 3.1) are still priced and recorded, with
`billed_to = "user"` on the usage row. They do not count toward that user's daily budget, but
`MAX_COST_PER_LECTURE_USD` still caps the job, so a runaway lecture stops either way. Users see that
spend as `own_key_usd` in `GET /api/usage/me` (over the same period as `total_usd`; `today_usd`
there is server-billed spend only, i.e. what the daily budget measures), and admins see it per user
in `GET /api/admin/usage` (whose `today_usd` is server-billed spend too). Once a user's server
spend is over their daily budget, calls on their own keys and free voices or images still run; only
a paid, server-billed call is refused. Server-key and `.env`-key calls are `billed_to = "server"` and count
toward both budgets as before. AI videos (Veo and Gemini Omni) and Lyria music are paid by the Gemini key,
so with a personal Gemini key both the cost estimate checked before a clip and the recorded usage stay outside
the daily budget. The up-front budget check of a new lecture also counts the Dynamic options when they arrive
(generated music, premium voices from any provider, a pinned paid narrator and the voices' calibration).

Claude engine: `ANTHROPIC_TIMEOUT_SECONDS` (default 1800, minimum 60) caps each whole streamed
Claude call; `LLM_TIMEOUT_SECONDS` (default 600) applies only to Gemini and OpenAI. At the default
`ANTHROPIC_EFFORT=high` with `ANTHROPIC_MAX_TOKENS=64000` a long scene can stream for more than ten
minutes; at `xhigh`/`max` raise `ANTHROPIC_TIMEOUT_SECONDS` or lower the effort if Claude calls time
out. A connection dropped mid-stream is retried like a 5xx/529. With `ANTHROPIC_REFUSAL_FALLBACK`
on, a call that one model declined and a fallback model answered is billed for both attempts; spend
records one usage entry per attempt (`usage.iterations`), each at its own model's price. Claude
models without a built-in price are costed at the highest Claude list price ($15/$75 per 1M) until
you add them to `PRICING_FILE` (a warning names them).

Stalled AI calls (dead connections after a network or DNS drop): OpenAI and Claude calls stream, so a
request that gets no answer is retried after `LLM_FIRST_RESPONSE_TIMEOUT_SECONDS` (default 60, plus
about 1 s per 64 kB of request for uploading a large document) instead of waiting for the whole
per-attempt cap. Once an answer streams, `LLM_IDLE_TIMEOUT_SECONDS` (default 240) is the longest
silence before a retry: for OpenAI between stream events, except while a reasoning model reports that
it is thinking; for Claude it is the HTTP read timeout, and the API's keep-alive pings during long
thinking count as data. Healthy long answers are never cut short by these limits;
`LLM_TIMEOUT_SECONDS` / `ANTHROPIC_TIMEOUT_SECONDS` still cap each attempt and the retry budget is
unchanged. Gemini calls are not streamed (Gemini 2.5 and 3.x think before the first streamed chunk and the
SDK exposes no earlier sign of life), so they keep waiting up to `LLM_TIMEOUT_SECONDS` per attempt.
OpenAI lets only verified organizations stream some models (o3 and the gpt-5 family: HTTP 400 "Your
organization must be verified to stream this model"). On such a key the request is sent again
without streaming in the same attempt, and later calls to that model skip streaming; those calls
lose the early stall detection (only `LLM_TIMEOUT_SECONDS` and the notices below apply). Verify the
organization in the OpenAI dashboard, or pick a model it may stream, to get it back.
For every engine and for voice, image and video services the job log shows each retry ("The AI
service (OpenAI) didn't respond; trying again (attempt 2 of 4).", rate-limit waits) and, after a
minute without an answer, "Still waiting for the AI service ..." again after 2, 4 and 8 more
minutes, then every 10 minutes. A streamed request that has not started answering sends no such
notice in the half minute before its first-response limit, so a stall shows only the retry line.
The Studio shows the latest notice under the job's status line. Notices are throttled per job:
repeats within a minute are dropped, at most 12 "still waiting" lines and 40 retry or rate-limit
lines are kept.

### 4.7 Monitoring, health checks and logs

* `GET /healthz` (liveness, no dependencies) is the container `HEALTHCHECK`; `GET /readyz` checks
  the database and storage. Point the load balancer at `/readyz`.
* Job health: queue depth and failure rate are visible in the admin page;
  `python -m aadhi.cli purge-stale-jobs --dry-run` lists running jobs without heartbeats.
  Alert on: `/readyz` failures, jobs `queued` for more than a few minutes (no worker of that kind),
  rising `failed` jobs, disk usage of the data volume, Postgres connection saturation, and spend
  approaching budgets.
* Logs go to stderr: JSON lines in production (`{"ts","level","logger","msg",...}`), key=value in
  development. Docker keeps 5 x 20 MB per container (`docker compose logs -f api worker`); ship
  them to your log stack for retention.
* Log redaction: a logging filter replaces every configured secret value and `key=/token=/
  secret=/password=` values in messages and tracebacks; provider/job errors pass through
  `Settings.redact()` before they are stored or shown, and that also removes every API key
  decrypted from the database. Prompts are never logged at INFO.
* Saving, deleting and testing an API key in the Studio each log one line with the user, scope
  and provider (never the key), so you can audit who changed which key.
* `python -m aadhi.cli verify-assets` checks that every asset row still has its blob.
* Preview vs MP4 parity: `python evals/render_parity.py [--out DIR --keep] [--threshold 8]` renders a
  fixture lecture through the app and compares the MP4 with preview screenshots at the same instants
  (mascot masked; needs ffmpeg and Chromium; exit 1 when a sample differs more than the threshold).
  `--dynamic landscape|portrait` does the same for a Dynamic video (B1): the fixture with a synthetic presentation
  rendered by the motion engine against the live-mode stage at 20 seeded instants, every SSIM at least 0.98; each
  sample is split into compositor, JPEG-capture and x264 loss, so a failure shows where it comes from.

### 4.8 Upgrades

**Deploy order (from Dynamic video batch B0 on).** `docker compose up -d` alone restarts the services
independently, so an old worker could run a job against documents the new API already wrote. Stop the workers
first, start the API (which migrates), then start the workers on the same image:

```bash
git pull && docker compose build
docker compose stop worker render-worker   # 1. no job runs on the old code (+ manim-worker with --profile docker-sandbox)
docker compose up -d --no-deps api         # 2. migrations run at start; wait until `docker compose ps api` is healthy
docker compose up -d                       # 3. the workers start on the same image
```

**B0 is the rollback floor.** From batch B0 on, lectures, timelines and manifests that use a Dynamic-video field
are stored with `schema_version` 3 (section 3). Builds before B0 do not know those fields and would drop them
when they save a lecture, so once any version-3 document exists, never roll back to a build older than B0;
rolling back to B0 or later is safe, because those refuse documents they cannot read (409 `newer_document`, a
failed job with `error_code` `newer_document`) instead of rewriting them. A job failing with `newer_document`
means a worker runs an older image than the API or the worker that wrote the version: finish the deploy order
above, then retry the job.

The API applies migrations at start; workers restart after it is healthy. Read the release notes
for migrations that need a maintenance window (large table rewrites), and take a `pg_dump` first.

| Migration | Changes |
|---|---|
| `0001_initial_schema` | every table |
| `0002_api_credentials` | new `api_credentials` table (API keys saved in the Studio, encrypted); new `usage_events.billed_to` column (`server` or `user`, existing rows become `server`). Quick, no maintenance window. Set `CREDENTIALS_ENCRYPTION_KEY` (section 3.1) before anyone saves a key. |
| `0003_asset_claims` | new `asset_claims` table (cross-process claims on paid image / video generations). Quick, no maintenance window. Until it is applied, generation runs unclaimed (one warning). |
| `0004_claim_operation` | new nullable JSON column `asset_claims.operation` (the holder's AI-video operation record, so a job that takes over a dead claim resumes the paid video instead of paying again). This covers graceful stops too (worker shutdown or deploy, lost lease, cancel): such a holder leaves its claim expired with the record instead of deleting it. Quick, no maintenance window; development SQLite gets the column automatically. |
| `0005_library_items` | new `library_items` table (each user's media library: their words for a picture or clip, keyed by user and asset key; no foreign key to `assets`). Nothing is backfilled: uploads and builds add items from now on. Quick, no maintenance window; runs cleanly when a development start already created the table. |
| `0006_visual_reviews` | new `visual_reviews` table (the Visual Review sign-off of each scene's visual per version, deleted with its version). Quick, no maintenance window; runs cleanly when a development start already created the table. |

Batch 4 (hidden scenes, minimum durations, "Generated vs edited", the lesson stage, the Videos page) adds no
migration: the new scene fields live in the screenplay JSON (left out at their defaults) and everything else is
derived from existing rows.

Dynamic video batch B0 adds no migration either: the new fields live in the JSON documents, the new asset kinds
(`music`, `clip`, `clip_proxy`, `clip_sprite`, `waveform`, `voice_sample`, `thumbnail`, `narration_upload`, all
served like the other media) fit the existing string column, and new asset keys are authorised through the
existing `asset_refs` table. Follow the deploy order above all the same.

Dynamic video batch B2a adds no migration or setting. Its timelines are a new look-engine generation
(`presentation.engine_version` 3 since B2's demo phase; B2a's are 2 and the B1 ones 1), which has three one-time
effects after the deploy:

* **Timelines are refreshed once.** The timeline ETag carries the engine generation, so every player's cached
  timeline is fetched again (one 200 instead of a 304). A stored Dynamic timeline of the current revision built by B1
  is served rebuilt with the new resolver (the stored row is not written), exactly as a new MP4 is built, so the
  player and the MP4 agree; this costs about 10-45 ms per 40-scene lecture on every request that is not a 304, until
  the lecture is built again. With `DYNAMIC_ENABLED` off (the default) there are few or none.
* **Motion chunk caches miss once.** `CHUNK_CACHE_VERSION` is 3 (the chunk key now holds the background clock) and
  the stage bundle digest changes with `web/js/dynamic`. Chunks are reused only by a retry of the same render, so the
  effect is limited to a render retried across the deploy, which captures its chunks again.
* **Stage and server generations.** The stage refuses a timeline of a newer generation than it knows
  (`timeline_invalid`: the render page fails `ready`, the watch page shows "This lecture could not be played"). Run
  the API and every render worker on the same image (deploy order above), and reload open Studio and watch pages
  after the deploy. A stored engine-1 timeline that is stale (the lecture was edited since) is served and drawn as B1
  drew it until it is built again.

Dynamic video batch B2b adds no migration or setting, and no look changes: the look-engine generation stays 2, so no
timeline is rebuilt and the timeline ETag is unchanged.

* **New timelines store `presentation.fonts.indic`.** Timelines built after the deploy store the fonts as
  `{display, body, mono, indic}`, naming the faces the stage drew before. A display face B2a drew as Outfit (for
  example `poppins`) is now stored as `outfit`. Stored timelines keep their fonts and draw unchanged.
* **Motion chunk caches miss once.** The stage bundle digest covers `web/js`, `web/css` and `web/vendor`, which B2b
  changes (`fonts.js`, `faces.gen.js`, `dynamic-fonts.css`, the new faces). As in B2a, this affects only a render
  retried across the deploy, which captures its chunks again.
* **Rolling back to B2a** keeps working for these timelines by construction: B2a's schema keeps `fonts` as a free
  dict, and its stage reads only `display`, which B2b sets to a face B2a draws. This was not tested across images.
* **Image and installs.** The image gains the Dynamic faces in `web/vendor/fonts/dynamic/` and
  `/app/THIRD_PARTY_NOTICES`. `npm ci` (the image's `web` stage and CI) downloads about 62 MB more for the icon
  packages, none of which reaches the image.

Outside Docker, run `python -m aadhi.cli migrate` after pulling.

* **Development SQLite** (not production): on start the API creates missing tables and adds
  missing columns that are nullable or have a default (`aadhi/dev_schema.py`; for example
  `usage_events.billed_to`), so an older development database keeps working without `migrate`.
  Other schema changes still need `migrate`. Production and non-SQLite databases must be at the
  Alembic head, as before.
* **Database created without Alembic** (by an older development start): `python -m aadhi.cli
  migrate` stamps it at the head when it matches the models, first adds the missing tables and
  columns when that is all that differs, and refuses any other difference. `0002_api_credentials`
  also runs cleanly when a development start already created its table or column.

### 4.9 Security checklist

* `BASE_URL` https, `CORS_ORIGINS` exact https origins (or empty), Postgres not published.
* Rotate `JWT_SECRET` to sign everyone out; `python -m aadhi.cli set-password <user> --password-stdin`
  revokes that user's sessions.
* Keep `MANIM_ALLOW_FREEFORM=false` unless the docker sandbox runs on an isolated host.
* Set `CREDENTIALS_ENCRYPTION_KEY` and keep it out of the database backups; set
  `USER_API_KEYS_ENABLED=false` if teachers should not use their own provider accounts.
* `.env` is excluded from the image and from git (`.dockerignore`, `.gitignore`).
* The image copies an allow-list only (`aadhi/`, `alembic/`, `alembic.ini`, `server.py`, `web/`,
  `docker/app/`, the branding clips and their posters): v1 leftovers, docs, tests, evals and local
  data never reach it, even while they still exist in a checkout. Dev tools (pytest, ruff) are not
  installed in it.
* Keep `docker/app/chromium-seccomp.json` on the render worker (see 4.2); it is the only relaxation
  of Docker's default seccomp policy.

### 4.10 Branding clips and posters

`video_template/` holds the mascot clips (`compose.base.MASCOT_CLIPS`), the background, logo and BGM,
served at `/branding/` and copied into the image. Each clip has a poster,
`video_template/posters/<clip name>.jpg` (frame 0 at JPEG quality 3),
which the player shows until the clip has a frame and as its fallback; add one with the same name
for every new clip. The left clip is `aadhi_left_clean.mp4`: `aadhi_left.mp4` with its baked-in
black pillars and dark top line filled in by mirroring the neighbouring picture (from the median of
all frames, so the moving hand never smears into the bars), same size, frame rate, length and audio.
`python scripts/clean_mascot_clip.py video_template/aadhi_left.mp4 video_template/aadhi_left_clean.mp4`
reproduces it (`--poster-only CLIP POSTER` writes a poster). Stretching the edge columns instead was rejected:
it turns the hand into a streak once per loop. The original file stays: timelines stored before the
switch name it and are served the clean clip (`RETIRED_MASCOT_CLIPS`).

The right, center, popup and hidden positions use `aadhi_right_clean.mp4`, `aadhi_center_clean.mp4`,
`aadhi_popup_clean.mp4` and `no_aadhi_clean.mp4`: the same clips with the faint "Veo" watermark in the
bottom-right corner filled from a clean plate of the static floor (whiteboard tray in the popup clip)
beside it; same size, frame rate, 192 frames and audio. `python scripts/clean_mascot_clip.py --watermark
SRC DST --poster video_template/posters/<name>_clean.jpg` reproduces each one and refuses a clip in which
anything moves in that corner. The originals stay and are mapped to the clean files for stored timelines.
The Dockerfile copies `aadhi_*.mp4` and `no_aadhi*.mp4`; a branding test fails when a clip named in
`MASCOT_CLIPS` would be missing from the image.

The intro logo is `logo_animation_clean.mp4` (`compose.base.LOGO_VIDEO`): `logo_animation.mp4` with the
generator's four-pointed sparkle in the bottom-right corner (every frame) filled from the wall and floor
beside it, ramped so the wall's vignette continues; same size, 24 fps, 240 frames and audio. `python
scripts/clean_mascot_clip.py --watermark --box 1128,568,1192,632 video_template/logo_animation.mp4
video_template/logo_animation_clean.mp4` reproduces it. The original stays; timelines stored before the
switch name it and are served the clean file (`compose.base.RETIRED_BRANDING_FILES`). The Dockerfile copies
`logo_animation*.mp4`.

## 5. Upgrading from v1

v1 was a single `index.html` + `server.py` with a SQLite `projects.db`, shared credentials and
media folders in the repository. v2 keeps nothing of that runtime state implicitly.

1. **Back up** `projects.db`, `.env`, and the v1 media folders (`static_videos/`, `final_videos/`,
   `images/`) before upgrading.
2. **Rotate every credential v1 used.** v1 shipped a hardcoded password in `server.py`, a
   credential in `handoff.md`, and API keys in `.env`; treat all of them as compromised: issue new
   Gemini/OpenAI/ElevenLabs/GIPHY keys, revoke the old ones, and drop `APP_USERNAME` /
   `APP_PASSWORD` from `.env` (v2 has real accounts). Take down any static deployment created from
   the old `vercel.json`, which published the whole repository.
3. Install v2 (section 2 or 4) and run `python -m aadhi.cli migrate`.
4. Import v1 users and projects (the v1 database is opened read-only):

   ```bash
   python -m aadhi.cli import-legacy --db projects.db [--owner-fallback admin] [--latest-only]
   # one exported v1 lecture JSON (or a v2 screenplay JSON):
   python -m aadhi.cli import-json showcase_ohms_law.json --owner admin
   ```

   Imported lectures become v2 screenplays (board HTML converted to typed board items); assets are
   rebuilt by a `build_assets` job, so narration and media are regenerated with v2 pipelines.
   Databases and lesson files from the friend's fork of v1 (`origin/andryan`) import the same way: its
   extra tables are ignored, scenes hidden in its editor come in as hidden scenes, its per-scene minimum
   duration is kept (1–600 s), and its other additions (muted narration, captions off, its asset ids,
   presenter / style / Studio data) are listed as import warnings. That fork stores every save as a new
   row, so add `--latest-only` to import only the newest save of each lesson (same owner, subject, unit,
   session and title); the skipped rows are listed in `projects_superseded`.
5. **Rotate the admin password** after the import:
   `python -m aadhi.cli set-password admin --password-stdin --must-change`. Imported v1 users keep
   their bcrypt hashes; ask them to change passwords too (`--must-change`).
6. Check the imported projects in the studio, rebuild where `timeline_stale` is shown, and only then
   archive `projects.db` and the v1 media folders.
