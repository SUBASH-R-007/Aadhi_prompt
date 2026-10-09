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

# Frontend libraries and fonts -> web/vendor (npm ci runs scripts/vendor.mjs)
npm ci

# Optional: configuration (everything has a safe development default)
cp .env.example .env

# Run
.venv/Scripts/python.exe server.py      # http://127.0.0.1:8000
```

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
```

After changing `requirements.txt` / `requirements-dev.txt`: install, run the tests, then
`scripts/lock_requirements.py` rewrites the lock. `pip freeze` cannot see Linux-only dependencies on
Windows (for example `uvloop` from `uvicorn[standard]`); they are pinned with environment markers in
`PLATFORM_PINS`, and the script fails when the Linux dependency closure needs one that is missing.

Tests always force fake providers and blank every API key (`tests/conftest.py`), even when your
`.env` holds real keys. Tests that need the network are marked `network` and only run with
`AADHI_NETWORK_TESTS=1`.

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
`VIDEO_FALLBACK_PROVIDERS` (veo, fake) list backups, tried in order when the preferred provider is
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
  empty = the model's default). Check `VEO_MODEL` (default `veo-2.0-generate-001`) against Google's
  current models before relying on AI video.
* Restarts and Veo: the operation is saved in the job before the paid request and as soon as Veo
  accepts it, so a restarted (or retried) job polls the same operation instead of paying again; its
  charge is noted as soon as it is reported, so a restart during the download never bills it twice.
  Collecting such a clip is not blocked by the lecture or daily budget (its charge was already
  counted). Only a request still waiting for Veo's answer is outstanding (`submitting`): when Veo
  answered with an error (429, 5xx) the record is `pending` and a restart during the retry wait
  submits normally. A submission whose answer was never saved is **not** re-submitted automatically
  (the scene shows its still with `video.ambiguous_submission`); an explicit rebuild of that scene
  generates it once.
* Paid generations are claimed across worker processes (`asset_claims`, `GENERATION_CLAIM_*`), so two
  workers never pay for the same image or clip; a claim of a crashed worker expires after
  `GENERATION_CLAIM_TTL_SECONDS` and is taken over.
* When a medium is off or not configured, images and clips generated earlier for the same request
  are still used (info issue `assets.cached_media_used`), unless the lecture opted out.

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
   non-root user `aadhi`, tini as PID 1).
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
{"llm": {"gemini-2.5-flash": {"input": 0.30, "output": 2.50},
         "claude-opus-5-5": {"input": 4.00, "output": 20.00}},
 "tts": {"elevenlabs": {"default": 200.0}},
 "image": {"gemini": {"default": 0.039}}}
```

and `PRICING_FILE=/data/app/pricing.json` (re-read when the file changes). Claude prompt caching is
billed separately: cache writes cost 1.25x and cache reads 0.10x the model's input price unless the
model entry sets its own `cache_write` / `cache_read` price (USD per 1M tokens), e.g.
`"claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20}` (the built-in value).
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
toward both budgets as before. AI videos (Veo) are paid by the Gemini key, so with a personal
Gemini key both the cost estimate checked before a clip and the recorded usage stay outside the
daily budget.

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
unchanged. Gemini calls are not streamed (Gemini 2.5 thinks before its first streamed chunk and the
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

### 4.8 Upgrades

```bash
git pull && docker compose build && docker compose up -d
```

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
