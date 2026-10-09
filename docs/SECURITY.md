# Aadhi EduEngine v2: Security

This document covers the threat model, the controls that are implemented (with the code that
implements each one), day-to-day security operations, and the clean-up after the v1 secret leak.
ARCHITECTURE.md §11 is the summary. When this document and the code disagree, the code is right.

---

## 1. What we protect

| Asset | Why it matters |
|---|---|
| Teacher/admin accounts | Admins control every project, user and budget. |
| Lecture content (sources, screenplays, renders) | Teachers' work. Source PDFs can be copyrighted or unpublished. |
| Provider API keys and budgets (Gemini, OpenAI, Anthropic, ElevenLabs, GIPHY, S3) | A leaked key or an uncapped job turns into real money. |
| API keys saved in the Studio (server keys and users' personal keys for Gemini, OpenAI, Anthropic; §3.7) | A server key bills the college's provider account. A personal key bills the teacher's own account, outside the server's daily budgets. |
| The servers | Manim (Python) and p5 (JavaScript) code is produced by an LLM, so it must never run with server or app-origin privileges. |
| Students | They watch through share links and never log in. They must not be exposed to injected scripts or tracking. |

## 2. Threat model

| Actor / vector | Capability | Main threats |
|---|---|---|
| Anonymous internet user | Reaches public endpoints and has share links | Credential stuffing, enumerating usernames, DoS with large bodies, scraping share links |
| Student with a share link | Uses the public player and analytics | Abusing the analytics endpoint, reaching other lectures |
| Editor (teacher) | Authenticated, owns projects | Reading or changing other teachers' projects (IDOR), running up costs, malicious uploads, spending another user's personal API key, using the key check to test stolen keys |
| Malicious source document | Its text is passed to LLMs | Prompt injection that makes the model emit hostile board HTML, TeX, Manim code or p5 code |
| Malicious upload | Files reach the parsers and the media origin | Polyglots (HTML/SVG served as an image), zip and decompression bombs, macro documents |
| Cross-site attacker | Gets a logged-in teacher's browser to send requests | CSRF on state-changing endpoints, login CSRF, clickjacking |
| Compromised or noisy dependency or provider | Error messages, logs | Secrets leaking into job errors, logs or UI messages |
| Leaked historical secrets | v1 git history | Logging in as the v1 admin, abusing the GIPHY key (§5) |
| Reader of a database dump or backup | Sees every table, but not the server's environment | Recovering saved API keys from `api_credentials` (§3.7) |

Out of scope: physical access to the host, a compromised operating system, and denial of service
large enough to need network-level protection (use the reverse proxy or CDN for that).

## 3. Controls and where they live

### 3.1 Authentication (`aadhi/auth/`)

* **Password hashing** (`passwords.py`) uses bcrypt 5 directly at 12 rounds in production. Hashes
  are standard `$2b$`, so v1 passlib hashes keep working. bcrypt ignores everything after byte 72;
  instead of truncating silently, longer passwords are rejected by the strength check and
  `verify_password` returns False for them. Weaker stored hashes are upgraded on the next login
  (`service.authenticate`).
* **Timing equalisation.** An unknown user, an empty or malformed hash, and an over-long password
  all still cost one bcrypt comparison against a dummy hash of the same cost. Login time does not
  reveal whether a username exists.
* **Strength rules** (`check_password_strength`) follow NIST 800-63B: a minimum length
  (`PASSWORD_MIN_LENGTH`, never below 8), at most 72 UTF-8 bytes, no control characters, at least 5
  distinct characters, no username inside the password, and a deny-list of common passwords and
  local words (`aadhi`, `rajalakshmi`, `rec`, ...) dressed up with digits or symbols.
* **Sessions** (`tokens.py`) are JWTs `{sub, tv, typ:"session", iat, exp, jti}`.
  * HS256 is pinned. The header `alg` is checked before decoding, and `none` and HS512 are refused.
  * Every claim is required, and there is 30 s of leeway.
  * The `sub` claim must be a numeric string and `tv` an int.
* **Revocation.** `tv` must equal `users.token_version`. Logout, password changes, and role or
  active-status changes bump it in one atomic `UPDATE ... RETURNING` (`service.bump_token_version`,
  `service.set_password`), which ends every outstanding session of that user at once.
* **Per-request checks** (`deps.py`). The user row is loaded on every request, so role, `is_active`
  and `token_version` changes apply immediately.
  * `get_current_user` returns 403 `password_change_required` while `must_change_password` is set.
  * `get_current_user_allow_pending` allows that state; it is only for `me`, change-password and
    logout.
  * `get_optional_user` treats pending accounts as anonymous.
  * `require_roles` returns 403 `forbidden`.
  * Errors are `AppHTTPException`s that carry a machine `code` (`errors.py`).
* **Credential precedence.** An `Authorization: Bearer` header, when present, is the only
  credential considered. A broken or empty Bearer header never falls back to the cookie. Other
  `Authorization` schemes (for example `Basic` from a reverse proxy that password-protects a
  staging box) are not ours: they are ignored and the session cookie still applies.
* **Settings source.** The dependencies and the rate limiter read the settings of the app that
  serves the request (`app.state.settings`, set by `create_app(settings)`), so sessions are decoded
  with the same JWT secret and cookie name they were issued with.
* **Cookies** (`cookies.py`) are `__Host-aadhi_session` on https (Secure, Path=/, no Domain) and
  `aadhi_session` on plain-http development. They are always HttpOnly and SameSite=Lax, and live
  for `JWT_TTL_HOURS`.
* **Scoped tokens** are used for the render worker.
  * Payload: `{typ:"scoped", scope, ..., iat, exp, jti}`, signed with
    `HMAC-SHA256(JWT_SECRET, "aadhi-scoped:" + scope)`.
  * Because the key and `typ` both differ, a scoped token can never act as a session (tests cover
    both directions), and a token for one scope never validates for another.
  * The TTL is between 1 s and 7 days. Reserved claim names are rejected.
  * The token travels in the URL **fragment**, so it is never sent to the server in a URL or a
    Referer header.
* **Bootstrap** (`bootstrap.py`) runs only when no admin exists.
  * It uses `ADMIN_PASSWORD` if set. In production that password must pass the strength rules. In
    development a weak one is accepted, but the account must change it at first login.
  * Otherwise it generates a one-time password and writes it to
    `DATA_DIR/initial_admin_password.txt`. The file is written with mode 0600 *before* the account
    is committed. The log shows the file path, never the password, and the account must change the
    password at first login.
  * In production, a missing `ADMIN_PASSWORD` is a startup error.

### 3.2 Request-level protections (`aadhi/security/`)

* **CSRF** (`csrf.py`, pure ASGI) applies to POST, PUT, PATCH and DELETE requests that carry a
  session cookie (either cookie name, parsed exactly the way Starlette parses it). Such a request
  must send `X-Aadhi-CSRF: 1`, and its origin must check out: either `Origin` equals the
  `BASE_URL` origin or one of `CORS_ORIGINS` (normalised for scheme, host and default port), or,
  when no usable `Origin` was sent, `Sec-Fetch-Site` is `same-origin` or `none`.
  * Requests that rely only on a Bearer token, or carry no credentials at all, are not CSRF targets.
  * Exempt paths (`/api/auth/login`) still refuse a foreign `Origin` (login CSRF).
  * A failure returns `403 {"code":"csrf"}`.
  * CORS uses exact origins only. `*` is rejected by `Settings`.
* **Security headers** (`headers.py`, pure ASGI) are added to every response without overriding
  headers the endpoint set itself. They are `nosniff`,
  `Referrer-Policy: strict-origin-when-cross-origin`, a restrictive `Permissions-Policy`,
  `X-Frame-Options: SAMEORIGIN` and `Cross-Origin-Opener-Policy: same-origin`, plus HSTS
  (2 years, includeSubDomains) in production.
* **CSP** (`build_csp`) depends on the path.
  * **App** (all other paths):

    ```
    default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self';
    img-src 'self' data: blob: https://*.giphy.com <CDN>; media-src 'self' blob: <CDN>; connect-src 'self';
    frame-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self';
    frame-ancestors 'self'
    ```

    `<CDN>` is the `CDN_BASE_URL` origin. With S3 and no CDN, it is the bucket's presign origins.
  * **`/sandbox/p5`** (the p5 runner):

    ```
    default-src 'none'; script-src 'self' 'unsafe-eval' 'unsafe-inline'; style-src 'unsafe-inline';
    img-src data: blob:; connect-src 'none'; frame-ancestors 'self'
    ```
  * **`/media/*`**: `sandbox; default-src 'none'`. 2xx responses that are not image, audio or
    video also get `Content-Disposition: attachment`.
  * **Companion sheet**: the endpoint sets
    `sandbox allow-scripts; default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; img-src 'self' data:; font-src 'self'`.
* **Body limits** (`bodylimit.py`). Bodies are capped at `MAX_JSON_BODY_MB`. Only multipart bodies
  sent to the upload routes (`POST /api/projects`, `/api/projects/import`, `/api/uploads`, `/api/library`; exact
  paths, configurable with `upload_paths`) may be `UPLOAD_MAX_MB` + 1 MiB. Claiming
  `multipart/form-data` anywhere else does not lift the cap, so routes that buffer the body before
  authenticating (login) cannot be used for memory amplification. `Content-Length` is checked
  first, and chunked bodies are counted while they stream. Exceeding the cap returns
  `413 {"code":"too_large"}`.
* **Client IP** (`client_ip.py`). `X-Forwarded-For` is honoured only when the direct peer is in
  `TRUSTED_PROXIES` (IPs or CIDRs). The chain is walked right to left, so spoofed entries on the
  left are ignored, and a malformed hop stops the walk.
* **Rate limits** (`ratelimit.py`) use a thread-safe, in-memory sliding window bounded by an LRU
  cap.
  * They come in two forms: the `rate_limit(bucket, per_minute=, per_hour=, key=)` dependency and
    imperative `check_rate_limit` calls (login uses username+IP).
  * Exceeding a limit returns `429 {"code":"rate_limited"}` with `Retry-After`.
  * Limits apply **per process**. The DB-backed budgets (§3.5) are the hard cost cap.
* **Uploads** (`uploads.py`):
  * Extension, MIME and magic-byte allow-list (png, jpg, gif, webp, mp4, webm, mp3, wav, pdf,
    docx, json, txt, md). HTML, SVG, XML, JS and executables are never accepted, and text files
    that are really HTML, SVG or XML documents are refused.
  * Images are checked with Pillow, restricted to the declared format, with
    `MAX_IMAGE_PIXELS = 50_000_000` and `DecompressionBombWarning` treated as an error, followed by
    `verify()` and a first-frame decode.
  * Polyglots are refused for every image format. The end of the image is found from its
    structure (PNG `IEND`; GIF block walk to the `;` trailer, since appended JavaScript can itself
    end in `;`; JPEG segment and scan walk to `EOI`; WebP RIFF size), and only zero padding may
    follow. JPEG may also carry the trailers phones append (a second JPEG for MPF, Ultra HDR or
    depth maps, a Motion Photo MP4, Samsung `SEFT` metadata), but only when they contain no markup.
    Any image that is also a valid zip archive (an end-of-central-directory record, even one hidden
    in a GIF comment or PNG chunk) is refused.
  * MP4 must have a sane box structure and a `moov`/`moof` box. WebM must have the `webm` doctype.
    MP3 and WAV headers are validated, and a PDF must start with `%PDF-` and contain `%%EOF`.
  * DOCX:
    * The archive must contain `word/document.xml` and `[Content_Types].xml`.
    * No `vbaProject.bin` and no macro-enabled content types.
    * No encrypted, duplicate or path-traversal entries.
    * At most 100 MB uncompressed in total, and a compression ratio of at most 100 (zip-bomb
      guard).
  * JSON must be strict UTF-8 and strict JSON: no `NaN`/`Infinity` literals and no numbers that
    overflow to infinity such as `1e400` (`aadhi/security/strict_json.py`). Text must be strict
    UTF-8 with no NUL or control characters.
  * Display names are sanitised (`safe_display_name`). Storage names are always generated by the
    server.
* JSON request bodies (`aadhi/schemas/jsonsafe.py`, `aadhi/api/storable.py`): FastAPI decodes bodies
  with Python's `json`, which accepts `NaN`/`Infinity`, reads `1e999` as infinity and keeps lone
  UTF-16 surrogates (`"\ud800"`). Stored, any of them turns every later read into a 500 (and
  PostgreSQL JSONB refuses them). Policy: refuse on write, sanitise on read. Schema floats refuse
  non-finite numbers (`allow_inf_nan=False` on `StrictModel`, plus `ManimSpec.params`); typed string
  fields refuse lone surrogates everywhere (pydantic); every stored body (`StorableBody`: screenplay,
  version actions, project patch, regenerate) and stored document (`ensure_storable`: options,
  imports) is checked for both anywhere, free-form fields included, with a 422 naming the JSON path.
  Rows stored before the check are served sanitised (`json_safe`: `null`, U+FFFD) and loaded by jobs
  with `validate_stored` (0, U+FFFD), never a 500. LLM output with a non-finite number fails
  canonicalisation (re-ask, or the panel is dropped).

### 3.3 Untrusted content (LLM output, imported lectures)

* Board content is typed data in "rich-lite" markup and is rendered with `textContent`. No model
  output is ever inserted as HTML. The v1 importer (`aadhi/legacy/`) parses v1 HTML with
  `html.parser` into typed items and drops scripts, styles and images.
* Source documents may contain text aimed at the model ("ignore the instructions above ..."). Ingest
  counts such instruction-like lines and logs a warning with the count and pages (never the text);
  the text is kept as content (a course about prompt injection must still be teachable). Fenced code
  is exempt. Every system prompt of a stage that reads the source says that text inside it is
  teaching content, never instructions to follow (`style.md`, `brief.md`, `critic.md`, `practice.md`;
  docs/PROMPTS.md). The source report flags such text too (`source.instruction_like_text`) and masks,
  in every text it shows, any value source scoping or the brief removed (an author's name, a code).
* Pasted notes ("Paste your notes" in the Studio) are cleaned of control characters and uploaded as a
  `.txt` file, so they pass the same upload validation, size limit and ingest as any file; pasted web
  pages and lecture JSON are refused in the browser.
* Deterministic text checks stay linear on crafted input: the rich-lite tokenizer finds bold and
  `[[keyword]]` closers in precomputed positions (an unclosed `**` or `[[` once made it quadratic), and
  the quality checks (`aadhi/pipeline/quality`) and the source report (`aadhi/pipeline/source_review.py`)
  bound their inputs (characters per field, markers, definition scans, near-duplicate postings).
* Imported JSON (v1 `projects.db` rows, `import-json`, the `import_legacy` job) is decoded with
  `loads_strict`, and v1 chart values that are not finite numbers become 0 with a warning. Non-finite
  numbers would otherwise make every response that returns the screenplay fail (Starlette renders
  with `allow_nan=False`) and are rejected by PostgreSQL JSONB.
* TeX: forbidden macros are rejected by the schema, and MathJax runs in safe mode (ARCHITECTURE
  §1.4). Graph functions must pass the math.js expression allow-list; the importer converts v1
  JavaScript functions or drops them.
* Manim code must pass an AST allow-list and runs only in the sandbox (docker:
  `--network none --read-only --cap-drop ALL ...`). Production refuses free-form code without
  docker (`Settings.validate_for_runtime`). Defence in depth inside every runner
  (`aadhi/manim/runtime/hardening.py`, injected before user code): a `sys.addaudithook` that denies
  sockets, `ctypes`, `winreg`, `webbrowser`, `os.*` exec/spawn/fork/kill/startfile/link/symlink,
  `subprocess` except LaTeX helpers resolved to absolute paths beforehand, and file reads/writes
  outside the work, media and temp dirs (reads also the Python install); refusals print
  `[AADHI_BLOCKED]`. A frame budget and a resolution/fps lock stop runaway renders, a disk watchdog,
  memory ceiling and output cap bound resources (Windows Job Object kill-on-close, POSIX
  `RLIMIT_CPU` of `4 x MANIM_TIMEOUT_SECONDS + 30` CPU-seconds, which only stops orphaned runaways while
  the wall-clock watchdog is the real limit, docker `timeout -s KILL` and `--ulimit fsize`); process
  creation through `subprocess` or `os.posix_spawn` is allowed only for the resolved TeX programs. The output must be a regular file
  inside the media dir (no symlinks) of the profile's exact size, and leftover temp dirs / expired
  containers are swept (only when the owner pid is dead or was reused). `python -m aadhi.cli manim-check`
  probes isolation (network, environment, a host file outside the work dir) with the audit hook as configured.
* Temporary work dirs live in the app's own scratch root (`aadhi/scratch.py`, `SCRATCH_DIR`; default
  `<system temp>/aadhi`, `aadhi-<uid>` on POSIX). On POSIX the default root is created 0700 and refused
  when it is a symlink or belongs to another user (another local user could otherwise swap the work dirs
  inside it). Host sweeps look only inside that root and never list the system temp dir, so another
  program's or another checkout's folders are never removed, whatever their names. Logs shown to a user
  or sent to a model hide the scratch root as `<scratch>`, like the temp, work, Python and home dirs.
* p5 code runs only in the opaque-origin iframe with `sandbox="allow-scripts"`, under the sandbox
  CSP with `connect-src 'none'`.

### 3.4 Storage and media

* Only allow-listed MIME types can be stored (`aadhi/storage/assets.py`). Blob keys contain a random
  segment, so public URLs cannot be guessed. Sources, extracts and generated-screenplay snapshots
  (kind `snapshot`, "Generated vs edited") live under `private/` and are never served: the snapshot is
  read only by the changes routes, through `load_version`.
* S3 (`aadhi/storage/s3.py`):
  * Writes are conditional (`IfNoneMatch="*"`), so nothing is ever overwritten. botocore retries
    timeouts and 5xx responses, so a `412` can answer the retry of our own write whose first
    response was lost. On `412` the object is checked with HEAD and accepted only when its size and
    single-part MD5 ETag equal what was sent. Otherwise the write fails with `FileExistsError`.
  * Stored and cached URLs are stable app paths that 302-redirect to short-lived presigned URLs, or
    stable CDN URLs `CDN_BASE_URL/<key>`. The CDN origin must map to `<bucket>/<S3_PREFIX>`, so the
    CDN path is exactly the storage key.
  * Downloads use `ResponseContentDisposition: attachment`.
  * Credentials are `SecretStr`. With no explicit key, the default credential chain (an instance
    role) is used.
* Generated media (`aadhi/providers/_http.py` `fetch_limited`): Pollinations output is fetched over
  https only, from an allow-listed host, following at most 3 redirects that are each re-checked,
  with `Content-Length` checked first and the stream capped (`AI_MAX_IMAGE_BYTES`). Generated images
  and clips are validated (size, dimensions, duration) before they are stored.

### 3.5 Secrets, logging and cost

* Every secret is a `SecretStr`. `Settings.redact()` is applied to every job, provider and error
  message. It also removes every API key decrypted from the database (the redaction registry,
  §3.7), and by pattern any `AIza…` key, `sk-`/`rk-` keys of 16+ characters, Bearer tokens,
  `sig=`/`signature=`/`x-goog-*` URL parameters and Basic/plain `Authorization` values, so a key
  that was never registered is still hidden.
* A paid Veo operation is checkpointed in the job payload with its operation name and a key
  *fingerprint* (never the key); a job started on a personal key is resumed only with a key of the
  same fingerprint, never the server key.
* `aadhi/logging_setup.py` installs a `RedactingFilter` on the only log handler. It removes every
  configured secret value plus `key=`, `token=`, `secret=` and `password=` values and Bearer tokens
  from messages, arguments, tracebacks, stack info and `extra=` fields. An extra whose name looks
  like a credential (`api_key`, `*_token`, `password`, `authorization`, ...) is replaced entirely.
  Records are single-line, and JSON in production.
* Subprocesses (Manim, ffmpeg, Chromium) get allow-listed environments, so provider keys are never
  inherited.
* Costs:
  * `aadhi/usage/pricing.py` prices every billable call, whichever AI engine a lecture picked. The
    prices are approximate list prices; override them with `PRICING_FILE`.
  * A lecture can only pick an AI engine that has a key for the requesting user (personal, Studio
    server key or `.env`; 422 `engine_not_configured`). Engines without a key never receive a
    request.
  * `aadhi/usage/service.py` enforces the per-job budget (`MAX_COST_PER_LECTURE_USD` or the job's
    own budget) and the per-user daily budget (UTC day), raising `BudgetExceeded`. The per-job
    budget counts every call. The daily budget counts only calls the server paid for
    (`usage_events.billed_to = "server"`); calls paid with the user's personal key
    (`billed_to = "user"`) are excluded. Only a paid, server-billed call can trip the daily limit,
    so once a user is over it their own key and free providers still work (the per-job budget
    still applies). The API skips its budget pre-check only when the lecture's engine and every
    paid voice / image / video provider the job may use (including a Gemini or Veo backup in the
    provider chain and the lecture's image provider) run on the user's personal keys.
  * Paid generations run once across worker processes (`asset_claims`), and a build stops before
    paying when the version changed meanwhile (still-wanted gate). A claim row carries its holder's
    AI-video operation record (`asset_claims.operation`: no key, only a key fingerprint); a job that
    takes over a dead claim resumes that operation only when it pays the same way (server key, or the
    same user's personal key), so nobody collects a clip on another user's key.
  * The optional AI terminology check (`QUALITY_AI_TERMINOLOGY`, off) is one call per lecture through
    the lecture's engine and counts toward the job budget like every model call.
  * Render admission limits (`RENDER_MAX_PER_USER`, `RENDER_MAX_QUEUED`, also on job retries) keep one
    user from monopolising the render workers.
* Production refuses to start with unsafe configuration (`Settings.validate_for_runtime`), for
  example a short `JWT_SECRET`, a non-https `BASE_URL`, local storage inside the source tree,
  free-form Manim without docker, or an offline stand-in (`fake`) provider unless
  `ALLOW_FAKE_PROVIDERS=true`.
* Diagnostics that reveal host details are admin-only: `/api/admin/media-providers` (configuration
  only, reasons redacted), `/api/admin/manim/sandbox` (booleans, no paths, rate limited), the reasons
  in `/api/renders/capabilities` and in a 503 `render_unavailable` (others get a generic message).

### 3.6 Database and migrations

* State changes are single atomic statements (compare-and-set, `UPDATE ... RETURNING`).
* `python -m aadhi.cli migrate` runs Alembic. On SQLite it runs with `PRAGMA foreign_keys=OFF`
  and finishes with `PRAGMA foreign_key_check`.
* The v1 importer opens `projects.db` **read-only** (`sqlite3` URI `mode=ro`). It never writes to
  it.
* The v1 importer never carries over the v1 `admin` credential, because that password is public in
  the v1 source. The account is created as `admin` with an unusable hash, so nobody can sign in to
  it, and the import result lists an `action_required` step. Teacher accounts keep their v1 bcrypt
  hashes and become editors.
* `python -m aadhi.cli purge-stale-jobs` hands off to the job system's reaper
  (`aadhi.jobs.queue.reap_stale`). That reaper cancels stale jobs whose cancellation was requested
  (they are never requeued, so a cancelled job cannot spend money again), fails jobs that are out of
  attempts, requeues the rest, writes job events and settles the version.

### 3.7 API keys saved in the Studio (`aadhi/credentials.py`)

Admins can save **server keys** (used for everyone, replacing the `.env` key) and every user can
save **personal keys** (used only for jobs that user starts) for `gemini`, `openai` and
`anthropic`. Rows live in `api_credentials`, one per owner and provider (`owner_key` is `server`
or `user:<id>`).

* **Encryption at rest** (`aadhi/security/credentials_crypto.py`). Keys are stored only as Fernet
  tokens (AES-128-CBC with an HMAC-SHA256 tag, so a tampered row fails to decrypt instead of
  yielding a wrong key).
  * The Fernet key is `CREDENTIALS_ENCRYPTION_KEY` (urlsafe base64 of 32 bytes). When it is empty,
    the key is derived from the resolved `JWT_SECRET` with HKDF-SHA256 (info
    `aadhi-api-credentials-v1`), so it is never the JWT signing key itself.
  * A database dump or backup therefore holds only ciphertext. Whoever also has the encryption key
    (or, when it is derived, `JWT_SECRET`) can decrypt it, so never store either next to the
    backups.
  * Changing the encryption key, or changing `JWT_SECRET` while `CREDENTIALS_ENCRYPTION_KEY` is
    empty, makes every saved key unreadable. Nothing crashes: unreadable rows are skipped (one
    redacted warning per row), resolution falls through to the next source as if the key were not
    saved, and the Studio shows the key as needing re-entry (`"readable": false`). There is no
    re-encryption step; the owners enter the keys again.
* **Never returned.** No endpoint returns a key or its ciphertext. Responses carry a hint
  (`credential_view`: the vendor prefix such as `sk-ant-`, `sk-proj-`, `sk-` or `AIza`, then
  `…` and at most the last 4 characters), timestamps, the last test result and `readable`. Keys are
  not put in job payloads, job events or usage rows: a worker decrypts them itself when the job
  starts (`resolve_keys` for `job.user_id`) and holds them only in memory.
* **Redaction registry** (`aadhi/security/redaction.py`). Every decrypted key is registered with
  `register_secret`, and `Settings.secret_values()` / `Settings.redact()` include the registered
  values for every `Settings` instance, including `get_settings()`, and the process-wide logging
  filter (`aadhi/logging_setup.py`) checks the registry on every record. A provider error that
  echoes the key is therefore redacted in job errors, job events, usage metadata, log lines and
  messages shown to users, exactly like a `.env` secret. A saved key is registered under its
  owner slot (`server:<provider>` or `user:<id>:<provider>`): replacing a key swaps that slot's
  value and deleting it frees the slot, and slot values are never evicted. So no user can grow the
  shared list (at most 3 values each) or push another user's key out of it by saving keys again
  and again. A key being tested is scrubbed from the test message directly and is not added to the
  list. Job events are additionally redacted with the job's own resolved settings.
* **Validation.** A key is trimmed and must be 16 to 512 letters, digits, `-` or `_` (the
  characters Gemini, OpenAI and Anthropic keys use; 422 otherwise). Paths, `name=value` pairs,
  model names with dots or e-mail addresses are refused, so ordinary log text cannot be registered
  for redaction and censored in everyone's logs. Unknown providers are 404.
* **Authorization.**
  * `/api/keys` acts only on the caller's own personal keys. The owner comes from the session, never
    from the request, and no endpoint lists, tests or uses another user's personal key, not even for
    an admin.
  * `/api/admin/keys` requires the `admin` role and manages only server keys. Deleting one leaves
    the `.env` key in place.
  * Both use the normal session, CSRF and per-user API rate-limit rules. The test endpoints have an
    extra per-user rate limit, so they cannot be used to check stolen keys in bulk.
  * Deleting a user deletes their personal keys (`ON DELETE CASCADE`).
* **Who pays.** A job resolves keys for the user who started it. Personal key first (when personal
  keys are enabled), then the server key saved in the Studio, then `.env`. An admin regenerating a
  teacher's lecture therefore uses the admin's keys, never the teacher's. Every usage row records
  `billed_to` (`user` when the call ran on the starter's personal key, else `server`).
* **No silent fallback.** When the provider rejects a personal key (HTTP 401/403, or Gemini's HTTP
  400 `API_KEY_INVALID` / "API key expired"; also when wrapped in another error), the job fails at
  once with `error_code="personal_key_rejected"` and a message telling the user to update or remove
  the key under API keys. The job is not retried and never runs on the server key instead. This
  covers every call of the job: a scene write, a translation, a voice, a generated image or an AI
  video on the refused key is not degraded to a fallback scene, kept source text or a skipped
  medium. Media produced with a personal key is de-duplicated only among that user's own jobs
  (`AssetStore.get_or_create(inflight_scope=...)`), so another user's concurrent job never receives
  the outcome of a refused key.
* **Test calls.** *Test* makes one authenticated list-models request with the provider's official
  SDK and a short timeout (`verify_key`). Nothing is generated, so nothing is billed. The redacted
  outcome is stored as `last_verified_at` / `last_error`. A failed test does not disable the key.
  Tests are limited to 10 per minute per user (personal and server tests share the limit); testing
  a saved key that can no longer be decrypted answers `ok: false` without calling the provider.
  The test suite always replaces `verify_key`, so tests never reach a provider.
* **Audit log.** Saving, deleting and testing a key each write one log line with the acting user,
  the scope (server or personal), the provider and the outcome. The key value is never logged.
* **Switches.** `STORED_API_KEYS_ENABLED=false` ignores every saved key (server and personal) and
  refuses new ones (403 `api_keys_disabled`). `USER_API_KEYS_ENABLED=false` does the same for
  personal keys only (testing a personal key is refused too). Deleting a saved key always works, so
  keys can be removed after the feature is switched off, and an admin can still test the `.env`
  key. The offline demo (`scripts/dev_demo.py`) turns saved keys off, so a saved key can never make
  it call a paid API.

### 3.8 Media library and Visual Review (`aadhi/library.py`, `aadhi/review.py`)

* **Per-user ownership.** A `library_items` row belongs to one user. Every `/api/library` route
  looks items up by the session user's id (`library.get_item`), so another user's item is 404,
  whether it exists or not, for everybody: admins see and use only their own library. No endpoint
  takes an asset key into a library. Items appear only because the user uploaded the bytes
  (`POST /api/uploads`, `POST /api/library`) or a build they started of their own lecture generated
  the media (`record_generated`). Builds use libraries only in their owner's own lecture: an
  admin-started build of someone else's lecture neither uses (`prefer_library_visuals`) nor fills
  any library, so no library media crosses between users.
* **Shared assets carry no ownership.** Assets are content-addressed and shared: identical bytes or
  identical generation inputs are one `assets` row for everybody. Ownership is therefore never derived
  from an `Asset` row (`created_by`, `meta`), and titles, descriptions and keywords live only in the
  user's own `library_items` row, so two teachers with the same picture keep separate words and never
  see each other's.
* **Using an item in a lecture.** `POST /api/library/{id}/attach` and the Visual Review's
  `choose_library` take the requester's own item and only the requester's own lecture (an admin
  acting on someone else's lecture gets 404 / 403 and nothing is attached). Attaching records an
  `asset_refs` row; screenplay keys stay authorised by `asset_refs` exactly as for uploads
  (`authorize_asset_keys`). Deleting an item removes the row only; lectures that use the media keep it.
* **Uploads.** `POST /api/library` uses the same `validate_upload` checks, MIME allow-list, size limit
  (it is one of the body-limit upload routes) and image / video probes as `/api/uploads`. Typed words
  are tidied (control, private-use and bidi-override characters removed) and bounded (title ≤ 120,
  description ≤ 1000, ≤ 20 keywords of ≤ 40 characters; lone surrogates refused with 422).
* **Media URLs.** Item and review previews use the same URLs as uploads and timelines: public asset
  blobs only (random segment in the key), through the media router or, on S3, its redirects to
  short-lived presigned URLs or the CDN (§3.4). Private blobs are never linked, and no response
  contains a storage credential.
* **Cost.** The optional AI description (`LIBRARY_AI_DESCRIBE_ENABLED`, off) is 6 calls a minute per
  user, budget-checked like a job start (skipped only on the user's own key), recorded as usage
  (`purpose: library_describe`, `billed_to` from the key that paid) and sends the model only that
  item's scaled picture or one clip frame plus its words. A provider failure is answered with a
  redacted message. Suggestions are deterministic word matching (no model), 30 a minute per user,
  and every response that scores a library is bounded: at most 1,000 candidate items, each item's
  profile cached by its words, and at most 20,000 (item, scene) scores per request
  (`library.MATCH_MAX_PAIRS`; a Visual Review write scores its own scene only). Visual Review reads
  are 60 a minute and its writes (sign-offs and actions) 60 a minute per user, on top of the API
  limit. A build with `prefer_library_visuals` loads one matcher per build, not one per scene.
  Visual Review's `new_version` / `retry` start a build with the budget pre-check and the hourly
  generation limit; an AI video that may already have been billed is submitted again only after the
  teacher confirms (`confirm_paid`), once per confirmation: a confirmed submission that stops again needs
  a new confirmation, and a job retry never carries the consent over.
* **Sign-offs** (`visual_reviews`) are advisory rows reached through `load_version` (owner or admin)
  and kept outside the screenplay, so they never change a build or a cache key. An approval sent with
  the revision the teacher saw is refused when the screenplay changed since, so it never covers a
  visual request nobody looked at. A `new_version` / `retry` that cannot change anything (generation
  off for the lecture or the requester, a settled fallback) is refused before any budget check or
  generation token, and so is one for a scene the teacher skipped in the video (`hidden`: the build
  leaves it out).
* **Retention.** The cleanup GC keeps an asset while any `library_items` row names it (besides
  `asset_refs`), so a teacher's library is not garbage collected under them.

### 3.9 Editor changes, lesson stage and videos (batch 4)

* **Same access as the version routes.** `GET /api/versions/{vid}/changes[/{scene_id}]` and `POST
  /api/versions/{vid}/scenes/{scene_id}/revert` use `load_version` (owner or admin, else 404); the
  revert is a mutation behind the CSRF middleware and the per-user rate limit, saved with the same
  revision compare-and-set, `version_busy` check, asset-key authorisation (`authorize_asset_keys`) and
  storable-JSON guard as `PUT /screenplay`. It only puts back a scene of the version's own snapshot or
  `scene_history`; the request never carries scene content.
* **Videos.** `GET /api/videos` lists only the session user's own, not deleted projects (administrators
  too; other teachers' videos stay reachable only through their projects). `preview_url` is the same
  capability (or short-lived presigned) URL timeline media uses, only for a succeeded render; the download
  stays the authenticated `/api/renders/{id}/download`.
* **Stage and next step** are derived from stored rows only; `next_step.href` is always a Studio route
  built by the server, and the Studio follows only `#/…` and same-site `/…` links.
* **Imports from the friend's fork.** Their lesson JSON is parsed like v1 lessons (strict JSON, the v1
  converter); their asset ids, `asset:` references and storage paths are never followed, only reported.
  Their extra database tables are ignored; the database is opened read-only as before.

---

## 4. Operations

### Rotating `JWT_SECRET`

Rotation immediately invalidates **every** session and every scoped render token.

1. Generate a new secret:
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
2. Put it in the secret store or environment of **every** API and worker process, then restart
   them together. Render jobs that are running fail and can be retried.
3. Users sign in again.

When `CREDENTIALS_ENCRYPTION_KEY` is empty, saved API keys are encrypted with a key derived from
`JWT_SECRET` (§3.7), so rotation also makes every saved key unreadable. To keep them, set
`CREDENTIALS_ENCRYPTION_KEY` before the first key is saved. Otherwise, after rotating, ask admins
and users to enter their keys again.

In development the secret is generated once and kept in `DATA_DIR/.jwt_secret`. Delete that file
to rotate it.

To end **one** user's sessions, rotate their password with
`python -m aadhi.cli set-password <user>`, or have an admin change their role, active status or
password (each bumps `token_version`).

### Saved API keys

* Production: generate `CREDENTIALS_ENCRYPTION_KEY` once
  (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
  and keep it in the secret store of **every** API and worker process, separate from the
  database backups.
* A leaked provider key: revoke it in the provider's console first, then delete or replace it on
  the Admin page (server key) or the API keys page (personal key). Deleting the row alone does not
  stop someone who already copied the key.
* To stop using saved keys at once (for example while investigating), set
  `STORED_API_KEYS_ENABLED=false` and restart. Jobs then use only the `.env` keys; the rows stay
  encrypted in the database.
* Changing `CREDENTIALS_ENCRYPTION_KEY` makes every saved key unreadable (§3.7); there is no
  re-encryption, so the keys must be entered again.

### Admin bootstrap and recovery

* Production: set `ADMIN_PASSWORD` to a strong value for the first start, sign in, change the
  password, then remove `ADMIN_PASSWORD` from the environment.
* Development: read `DATA_DIR/initial_admin_password.txt`, sign in, change the password, and
  **delete the file**.
* After `python -m aadhi.cli import-legacy`: the imported v1 `admin` cannot sign in, and because an
  admin now exists the `ADMIN_PASSWORD` bootstrap is skipped. Run
  `python -m aadhi.cli set-password admin` (the CLI prints this as `ACTION REQUIRED`).
* Lost admin password: run `python -m aadhi.cli set-password admin` or
  `python -m aadhi.cli create-admin --username <name>`. Passwords are read from a prompt or from
  `--password-stdin`, never from arguments.

### CSP changes

* Serve assets from a CDN by setting `CDN_BASE_URL`. Its origin is added to `img-src` and
  `media-src` only, never to `script-src`.
* Never add `'unsafe-inline'` or `'unsafe-eval'` to the app policy. Only `/sandbox/p5` has them,
  and that page has no network access and an opaque origin.
* Frontend libraries are self-hosted (`web/vendor`). Add new ones through `npm run vendor`, not a
  third-party script tag.

### Sandboxing

* Production: run with `MANIM_SANDBOX=docker` and keep the image minimal. Otherwise set
  `MANIM_ALLOW_FREEFORM=false` so only templates render.
* Run render workers (`WORKER_KINDS=render_video`) on hosts that hold no provider keys. Chromium
  keeps its sandbox, and its network is restricted to the app origin (only `RENDER_BASE_URL` when
  it is set, not the public origin).
* Check the Manim sandbox with `python -m aadhi.cli manim-check` after host or image changes; keep
  `MANIM_AUDIT_HOOK=true`.

### Deployment checklist

* Serve TLS only. `BASE_URL` must be `https://...`. Set `TRUSTED_PROXIES` to the reverse proxy
  address(es) only.
* Run Postgres with a dedicated role. Back up the database and the storage bucket.
* Set `CREDENTIALS_ENCRYPTION_KEY` before anyone saves an API key, and store it apart from the
  database backups.
* Run `python -m aadhi.cli verify-assets` after restores, and
  `python -m aadhi.cli purge-stale-jobs` if workers crashed.
* Keep `PRICING_FILE` current, and keep per-user budgets (`daily_budget_usd`) set for teachers.

---

## 5. Incident: secrets leaked by v1

**What happened.** The v1 code base (branch `main` and its history) contains two committed secrets.
Their values are deliberately not repeated here.

1. **The v1 admin password**, in plain text in `server.py`, `handoff.md` and `DOCUMENTATION.md`.
2. **A GIPHY API key**, hard-coded in `index.html` and `app_test.js`. Every visitor's browser
   received it.

Anyone with read access to the repository, or to any fork or clone of it, can still read both
values from history. Removing them in a new commit does **not** help. Rotation is what actually
closes the incident; purging history only limits further spread.

**Required actions (owner).**

1. **Rotate the admin password now.**
   * v2: `python -m aadhi.cli set-password admin`. This also signs out every existing session of
     that account.
   * The v1 importer never imports the v1 `admin` password hash. `must_change_password` alone would
     not protect the account, because changing a password only requires the current one, and that
     one is public. The imported `admin` gets an unusable hash, so nobody can sign in until
     `set-password admin` has been run. When a v2 `admin` account already exists, it is kept
     unchanged.
   * Any v1 deployment that is still running must have its admin password changed or be shut down.
   * Never reuse the leaked password anywhere, and check whether it was reused on other services.
2. **Revoke the GIPHY key.**
   * In the GIPHY developer dashboard (developers.giphy.com > your app), delete or regenerate the
     key. Create a new key only if GIF panels are needed.
   * Store the new key only as `GIPHY_API_KEY` in the server environment. v2 calls GIPHY from the
     server and never ships the key to browsers.
3. **Audit for other secrets** with a scanner such as `gitleaks detect` or `trufflehog git file://.`,
   run over the full history. Rotate anything else it finds (LLM keys, database URLs).
4. **Purge the history (optional, destructive).** This rewrites every commit hash and needs a
   **force-push**. Only the repository owner should run it, after announcing a freeze. Every
   collaborator must re-clone afterwards, and old clones must not be pushed again.

   ```bash
   # DESTRUCTIVE: rewrites history and force-pushes. Owner only, after rotating the secrets.
   pip install git-filter-repo
   git clone --mirror https://github.com/<owner>/Aadhi_prompt.git aadhi-mirror.git
   cd aadhi-mirror.git
   # replacements.txt (keep it OUTSIDE the repo; never commit it). One line per leaked value:
   #   <leaked admin password>==>REMOVED-SECRET
   #   <leaked giphy key>==>REMOVED-SECRET
   git filter-repo --replace-text ../replacements.txt
   # Optional: drop the v1-only files that carried the secrets from all history
   # git filter-repo --invert-paths --path app_test.js --path handoff.md
   git push --force --mirror origin
   ```

   After the push:
   * Ask GitHub Support to purge cached views and pull-request references.
   * Delete `replacements.txt` and the mirror clone.
   * Remember that forks and existing clones keep the old history, which is why steps 1 and 2 come
     first.
