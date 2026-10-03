"""AI media providers (Phase 8): one interface in front of every AI image and video generator.

The rest of Aadhi asks for media with a provider-independent MediaRequest ("an AI video of ...") and
gets a GenerationResult back. Everything provider-specific stays in an adapter here: endpoints,
authentication, SDK calls, job polling, how models and parameters are named, how errors look.

  AIProvider            the interface: capabilities(), configuration(), settings_for(), check(), generate()
    SyncProvider        providers that answer in one call (run in a worker thread)
    JobProvider         providers that start a job and are polled until it finishes (bounded, cancellable)
  ProviderRegistry      the providers of this server, their configuration and status, and the choice of
                        provider for a request (preference, capabilities, availability, fallback order)
  ProviderError         a provider failure in one of a few categories (FAILURES) that decide whether it
                        is retried and whether another provider may be tried

What each provider is sent comes from ai_cache.PROVIDER_SETTINGS, the same values the AI media cache
puts in a generation's identity, so the cache and the generators cannot drift apart. Secrets come from
the environment only and never leave this module: statuses and errors are sanitized.

Adapters: "pollinations" (images, no key), "gemini-image" (images, Gemini API, opt-in), "veo" (videos,
Gemini API), "ltx" (videos, local LTX-Video pipeline), "manual" (the user makes the video), and the
test stand-ins "fake" / "fake-alt" (AI_FAKE_PROVIDER=1 only; then no real provider is registered).
"""
import asyncio
import datetime
import email.utils
import ipaddress
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field

import ai_cache

IMAGE, VIDEO = "image", "video"
PRESENTER = "presenter"  # Phase 12: a video of a presenter speaking a scene's narration (the file is a video)
MEDIA_TYPES = (IMAGE, VIDEO, PRESENTER)


def media_kind(media_type):
    """The kind of file a media type is (a presenter clip is a video)."""
    return VIDEO if media_type == PRESENTER else media_type

# Result states (GenerationResult.status and the generation runs of ai_media.py)
QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED, UNSUPPORTED = "queued", "running", "completed", "failed", "cancelled", "unsupported"


def _env_float(env, name, default):
    try:
        return float(env.get(name) or default)
    except ValueError:
        return float(default)


def _env_flag(env, name, default):
    value = env.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def _env_list(env, name, default):
    return [part.strip() for part in (env.get(name) or default).split(",") if part.strip()]


# ---- errors -----------------------------------------------------------------------------------

class Failure:
    CONFIGURATION = "configuration"    # not set up here (e.g. no API key): another provider may be tried
    AUTH = "auth"                      # the provider refused the credentials: never retried
    UNSUPPORTED = "unsupported"        # it cannot make this request (media type, model, size, duration)
    REJECTED = "rejected"              # it refused this prompt/request (policy, invalid): neither retried nor sent elsewhere
    RATE_LIMITED = "rate_limited"      # 429: retried after the wait it asks for (if short), then another provider
    UNAVAILABLE = "unavailable"        # 5xx, network, overloaded: retried, then another provider
    TIMEOUT = "timeout"                # it did not answer in time
    INVALID_OUTPUT = "invalid_output"  # it answered, but not with usable media
    CANCELLED = "cancelled"
    LOST = "lost"                      # it no longer knows a job it accepted (expired, removed)


FAILURES = (Failure.CONFIGURATION, Failure.AUTH, Failure.UNSUPPORTED, Failure.REJECTED, Failure.RATE_LIMITED,
            Failure.UNAVAILABLE, Failure.TIMEOUT, Failure.INVALID_OUTPUT, Failure.CANCELLED, Failure.LOST)
TRANSIENT = (Failure.RATE_LIMITED, Failure.UNAVAILABLE, Failure.TIMEOUT)
NO_FALLBACK = (Failure.REJECTED, Failure.CANCELLED)

_SECRET_PATTERNS = (
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),                                   # Google API keys
    re.compile(r"\b(sk|pk|rk)-[0-9A-Za-z_\-]{16,}"),                          # OpenAI-style keys
    re.compile(r"(?i)\bbearer\s+[0-9A-Za-z._\-~+/=]{8,}"),                    # authorization headers
    re.compile(r"(?i)((?:^|[?&;\s,(])(?:key|api_key|apikey|token|access_token|sig|signature|x-goog-signature|x-goog-credential)=)[^&\s'\",)]+"),
    re.compile(r"(?i)(x-goog-api-key|authorization)[\"']?\s*[:=]\s*[\"']?[^\s,'\"}]+"),
)


def secret_values(env=None):
    """The configured secrets (values of *_API_KEY / *_TOKEN / *_SECRET / *_PASSWORD variables), to mask exactly."""
    env = os.environ if env is None else env
    return [v for k, v in env.items() if v and len(v) >= 8 and re.search(r"(_API_KEY\d*|_KEY_\d+|_TOKEN|_SECRET|_PASSWORD)$", k)]


def sanitize(text, limit=300, env=None):
    """A provider message made safe to log and to show: no keys, tokens or signed URL parameters."""
    text = " ".join(str(text or "").split())
    for value in secret_values(env):
        text = text.replace(value, "[redacted]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + "[redacted]", text)
    return text[:limit]


class ProviderError(Exception):
    def __init__(self, category, message, *, provider=None, retryable=None, retry_after=None, status_code=None):
        assert category in FAILURES, category
        self.category = category
        self.message = sanitize(message)
        self.provider = provider
        self.retryable = (category in TRANSIENT) if retryable is None else retryable
        self.retry_after = retry_after
        self.status_code = status_code
        super().__init__(self.message)

    @property
    def fallback_ok(self):
        return self.category not in NO_FALLBACK

    def to_dict(self):
        data = {"provider": self.provider, "category": self.category, "message": self.message}
        if self.retry_after is not None:
            data["retry_after"] = self.retry_after
        return data


def parse_retry_after(value, now=None):
    """Seconds from a Retry-After header (seconds or an HTTP date), or None."""
    if value is None:
        return None
    value = str(value).strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return max(0.0, (when - now).total_seconds())


def error_for_status(status, message="", *, retry_after=None, provider=None):
    """A ProviderError for an HTTP status returned by a provider."""
    detail = f"HTTP {status}" + (f": {message}" if message else "")
    if status == 429:
        return ProviderError(Failure.RATE_LIMITED, f"rate limited ({detail})", provider=provider,
                             retry_after=retry_after, status_code=status)
    if status in (401, 403):
        return ProviderError(Failure.AUTH, f"the credentials were refused ({detail})", provider=provider, status_code=status)
    if status == 402:
        return ProviderError(Failure.AUTH, f"the provider requires payment or an account ({detail})", provider=provider,
                             status_code=status)
    if status in (404, 405, 413, 415, 501):
        return ProviderError(Failure.UNSUPPORTED, f"the provider cannot handle this request ({detail})",
                             provider=provider, status_code=status)
    if status in (408, 504):
        return ProviderError(Failure.TIMEOUT, f"the provider timed out ({detail})", provider=provider, status_code=status)
    if status >= 500 or status == 409:
        return ProviderError(Failure.UNAVAILABLE, f"the provider is unavailable ({detail})", provider=provider,
                             retry_after=retry_after, status_code=status)
    return ProviderError(Failure.REJECTED, f"the provider refused the request ({detail})", provider=provider, status_code=status)


def error_from_exception(exc, provider=None):
    """Classifies anything an adapter raised (HTTP errors, SDK API errors, network errors, bugs)."""
    if isinstance(exc, ProviderError):
        if exc.provider is None:
            exc.provider = provider
        return exc
    if isinstance(exc, asyncio.CancelledError):
        return ProviderError(Failure.CANCELLED, "the generation was cancelled", provider=provider)
    if isinstance(exc, urllib.error.HTTPError):
        return error_for_status(exc.code, getattr(exc, "reason", "") or "", provider=provider,
                                retry_after=parse_retry_after(exc.headers.get("Retry-After") if exc.headers else None))
    code = getattr(exc, "code", None)
    if isinstance(code, int) and 100 <= code < 600 and hasattr(exc, "message"):  # google.genai.errors.APIError
        return error_for_status(code, getattr(exc, "message", "") or "", provider=provider)
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return ProviderError(Failure.TIMEOUT, f"no answer in time ({exc})", provider=provider)
    if isinstance(exc, (urllib.error.URLError, ConnectionError, OSError)):
        return ProviderError(Failure.UNAVAILABLE, f"the provider could not be reached ({exc})", provider=provider)
    # Anything else is a fault in the adapter or its library: another provider may be tried, but it is not retried
    return ProviderError(Failure.UNAVAILABLE, f"{type(exc).__name__}: {exc}", provider=provider, retryable=False)


# ---- requests, results, capabilities -------------------------------------------------------------

@dataclass
class MediaRequest:
    """What Aadhi wants, in its own terms. Nothing here names an endpoint or a provider's parameters."""
    media_type: str
    prompt: str
    provider: str | None = None          # an explicit choice: honoured, never silently replaced ...
    allow_fallback: bool | None = None   # ... unless fallback is allowed (default: only for automatic choices)
    model: str | None = None             # a model the chosen provider must use
    aspect_ratio: str | None = None      # "16:9", "2:3", ... (None: the provider's usual shape)
    duration_seconds: float | None = None
    force_regenerate: bool = False
    purpose: str = "lesson-visual"
    project_id: int | None = None
    scene_index: int | None = None
    slot: str | None = None
    # Phase 12, presenter requests only: the normalized presenter request (presenters.py) - profile snapshot,
    # speech timeline identity and loudness envelope, behaviour, expression, gesture, background, reference assets.
    # Provider-specific options stay inside the adapters.
    presenter: dict | None = None
    # Phase 20: the id of the scene this is for (scene.scene_id), kept in the run's request so a result still finds its
    # scene after the editor moved it. Where it is for, not what is made: never part of the cache or request identity.
    scene_id: str | None = None

    def fallback_allowed(self):
        return self.allow_fallback if self.allow_fallback is not None else self.provider is None


@dataclass
class GenerationResult:
    status: str
    provider: str
    media_type: str
    model: str | None = None
    path: str | None = None
    provider_job_id: str | None = None
    metadata: dict = field(default_factory=dict)  # e.g. seed, safety-filtered count, usage (never secrets)
    error: ProviderError | None = None
    started: float | None = None
    finished: float | None = None

    @property
    def duration_ms(self):
        return int((self.finished - self.started) * 1000) if self.started and self.finished else None


@dataclass(frozen=True)
class Capabilities:
    media_types: tuple
    execution: str                      # "sync" (one call) | "job" (started, then polled) | "local" (this machine) | "manual"
    models: tuple = ()                  # model codes it may be asked for (the configured default first)
    aspect_ratios: tuple = ()           # shapes it can make ("w:h"); empty: only its usual shape
    durations: tuple | None = None      # (min, max) seconds a video may be asked to last; None: fixed length
    inputs: tuple = ("text",)           # what a request may give it (text today; images later for image-to-video)
    negative_prompt: bool = False
    audio: bool = False                 # its videos have a sound track
    max_prompt_chars: int | None = None
    paid: bool = False                  # each generation is billed by the provider
    # Presenter providers only (Phase 12). Only what the provider really does: nothing is claimed by default.
    lip_sync: bool = False              # the mouth follows the supplied narration audio (not a generic talking loop)
    audio_input: bool = False           # it can be given the narration audio to speak
    expressions: tuple = ()             # the normalized expressions it can show (presenters.EXPRESSIONS)
    gestures: tuple = ()                # the normalized gestures it can make (presenters.GESTURES)
    transparent_background: bool = False
    reference_image: bool = False       # a reference picture fixes the presenter's identity
    consistent_identity: bool = False   # the same presenter is recognisable across scenes
    cancellation: bool = False          # a running job can be cancelled at the provider

    def to_dict(self):
        data = {"media_types": list(self.media_types), "execution": self.execution, "models": list(self.models),
                "aspect_ratios": list(self.aspect_ratios), "durations": list(self.durations) if self.durations else None,
                "inputs": list(self.inputs), "negative_prompt": self.negative_prompt, "audio": self.audio,
                "max_prompt_chars": self.max_prompt_chars, "paid": self.paid}
        if PRESENTER in self.media_types:
            data["presenter"] = {"lip_sync": self.lip_sync, "audio_input": self.audio_input, "expressions": list(self.expressions),
                                 "gestures": list(self.gestures), "transparent_background": self.transparent_background,
                                 "reference_image": self.reference_image, "consistent_identity": self.consistent_identity,
                                 "cancellation": self.cancellation}
        return data


def ratio_of(aspect):
    try:
        w, h = (float(x) for x in aspect.split(":"))
        return w / h if w > 0 and h > 0 else None
    except (AttributeError, ValueError):
        return None


def size_for(aspect, width, height):
    """(w, h) with the pixel count of width x height in another shape, multiples of 16."""
    ratio = ratio_of(aspect)
    pixels = width * height
    h = (pixels / ratio) ** 0.5
    return int(round(h * ratio / 16) * 16), int(round(h / 16) * 16)


# ---- the provider interface ------------------------------------------------------------------------

class AIProvider:
    name = ""
    label = ""
    env_prefix = ""                 # NAME_ENABLED, NAME_MODEL, NAME_TIMEOUT, NAME_MAX_CONCURRENT
    default_timeout = 120.0
    default_max_concurrent = 2
    default_enabled = True

    def __init__(self, env=None):
        self.env = os.environ if env is None else env

    # -- description (no network) --
    def capabilities(self):
        raise NotImplementedError

    def configuration(self):
        """("available" | "not_configured" | "disabled", reason). Reads configuration only; never calls the provider."""
        if not self.enabled():
            return "disabled", f"switched off ({self.env_prefix}_ENABLED=0)"
        return "available", ""

    def enabled(self):
        return _env_flag(self.env, f"{self.env_prefix}_ENABLED", self.default_enabled)

    @property
    def timeout(self):
        return _env_float(self.env, f"{self.env_prefix}_TIMEOUT", self.default_timeout)

    @property
    def max_concurrent(self):
        return max(1, int(_env_float(self.env, f"{self.env_prefix}_MAX_CONCURRENT", self.default_max_concurrent)))

    model_env = None                # the variable that may choose another model (only providers that send one)

    def default_model(self, media_type):
        base = ai_cache.PROVIDER_SETTINGS.get((media_type, self.name)) or {}
        return (self.env.get(self.model_env) if self.model_env else None) or base.get("model")

    # -- what it is sent (the cache identity uses exactly this) --
    def settings_for(self, request):
        """{"model", "parameters"} for this request: the provider's usual settings (ai_cache.PROVIDER_SETTINGS),
        with the requested model, shape or length applied. Call check() first."""
        base = ai_cache.PROVIDER_SETTINGS[(request.media_type, self.name)]
        parameters = dict(base.get("parameters") or {})
        model = request.model or self.default_model(request.media_type)
        if request.aspect_ratio:
            parameters = self.shape_parameters(request, parameters)
        if request.duration_seconds is not None:
            parameters = self.duration_parameters(request, parameters)
        return {"model": model, "parameters": parameters}

    def shape_parameters(self, request, parameters):
        return parameters

    def duration_parameters(self, request, parameters):
        return parameters

    def usual_aspect(self, media_type, parameters):
        if parameters.get("aspect_ratio"):
            return parameters["aspect_ratio"]
        if parameters.get("width") and parameters.get("height"):
            return f"{parameters['width']}:{parameters['height']}"
        return None

    def check(self, request):
        """Raises ProviderError(UNSUPPORTED) when this provider cannot make the request."""
        caps = self.capabilities()
        why = None
        if request.media_type not in caps.media_types:
            why = f"it does not make {request.media_type}s"
        elif (request.media_type, self.name) not in ai_cache.PROVIDER_SETTINGS:
            why = f"it has no settings for {request.media_type}s"
        elif request.model and caps.models and request.model not in caps.models:
            why = f"it does not offer the model {request.model}"
        elif caps.max_prompt_chars and len(request.prompt) > caps.max_prompt_chars:
            why = f"the prompt is longer than its {caps.max_prompt_chars} characters"
        elif request.aspect_ratio and request.aspect_ratio not in caps.aspect_ratios:
            usual = self.usual_aspect(request.media_type, ai_cache.PROVIDER_SETTINGS[(request.media_type, self.name)].get("parameters") or {})
            if not (usual and ratio_of(usual) and ratio_of(request.aspect_ratio)
                    and abs(ratio_of(usual) - ratio_of(request.aspect_ratio)) < 0.02):
                why = f"it cannot make the {request.aspect_ratio} shape"
        elif request.duration_seconds is not None:
            if media_kind(request.media_type) != VIDEO:
                why = "only videos have a duration"
            elif not caps.durations:
                why = "its videos have a fixed length"
            elif not caps.durations[0] <= request.duration_seconds <= caps.durations[1]:
                why = f"it makes videos of {caps.durations[0]:g}-{caps.durations[1]:g} s"
        if why:
            raise ProviderError(Failure.UNSUPPORTED, why, provider=self.name)

    # -- generation --
    async def generate(self, request, settings, dest, progress=None):
        """Makes the media and writes it to `dest`; returns a COMPLETED GenerationResult or raises ProviderError.
        `progress(status, provider_job_id)` is told when a provider job starts running."""
        raise NotImplementedError

    def result(self, request, settings, dest, started, **extra):
        return GenerationResult(status=COMPLETED, provider=self.name, media_type=request.media_type, model=settings.get("model"),
                                path=dest, started=started, finished=time.time(), **extra)


class SyncProvider(AIProvider):
    """A provider that answers in one blocking call (run in a worker thread, bounded by its timeout)."""

    def produce(self, request, settings, dest):
        """Writes the media to `dest`; returns metadata (dict). Blocking."""
        raise NotImplementedError

    async def generate(self, request, settings, dest, progress=None):
        started = time.time()
        try:
            metadata = await asyncio.wait_for(asyncio.to_thread(self.produce, request, settings, dest), timeout=self.timeout + 5)
        except asyncio.TimeoutError:
            raise ProviderError(Failure.TIMEOUT, f"no answer within {self.timeout:g} s", provider=self.name)
        return self.result(request, settings, dest, started, metadata=metadata or {})


class JobProvider(AIProvider):
    """A provider that starts a job and is polled until it finishes: bounded by the timeout. Recoverable
    (Phase 9): the job's handle is saved as soon as the provider accepted it (`progress.submitted`), and
    after a restart `resume(handle, ...)` polls the same job instead of starting another one."""
    default_poll_seconds = 10.0
    recoverable = True        # its jobs can be found again from a saved handle
    cancel_supported = False  # it can be asked to stop a job (only where the provider really offers it)

    @property
    def poll_seconds(self):
        return _env_float(self.env, f"{self.env_prefix}_POLL_SECONDS", self.default_poll_seconds)

    def submit(self, request, settings):
        """Starts the job; returns a handle with a string `job_id(handle)`. Blocking."""
        raise NotImplementedError

    def job_id(self, job):
        return getattr(job, "name", None) or str(job)

    def job_handle(self, job):
        """What must be saved to find the job again after a restart (JSON-safe, no secrets)."""
        return {"job_id": self.job_id(job)}

    def restore_job(self, handle):
        """The job again from a saved handle. Raises ProviderError(LOST) when the provider no longer has it."""
        raise ProviderError(Failure.LOST, "this provider's jobs cannot be found again", provider=self.name)

    def poll(self, job):
        """Returns (job, done). Raises ProviderError when the job failed. Blocking."""
        raise NotImplementedError

    def fetch(self, job, request, settings, dest):
        """Writes the finished job's media to `dest`; returns metadata. Blocking."""
        raise NotImplementedError

    def cancel(self, job):
        """Asks the provider to stop the job, if it supports that (best effort). True when it did."""
        return False

    def cancel_handle(self, handle):
        """Cancels a job known only by its saved handle (recovery). True when the provider stopped it."""
        if not self.cancel_supported:
            return False
        try:
            return bool(self.cancel(self.restore_job(handle)))
        except ProviderError:
            return False

    async def generate(self, request, settings, dest, progress=None):
        started = time.time()
        job = await asyncio.to_thread(self.submit, request, settings)
        job_id = self.job_id(job)
        submitted = getattr(progress, "submitted", None)
        if submitted:
            submitted(job_id, self.job_handle(job))  # saved before anything else happens: recovery can find the job
        if progress:
            progress(RUNNING, job_id)
        return await self._finish(job, request, settings, dest, progress, started)

    async def resume(self, handle, request, settings, dest, progress=None):
        """Continues a job started before an interruption: polls it (never submits a new one), then fetches."""
        job = await asyncio.to_thread(self.restore_job, handle)
        return await self._finish(job, request, settings, dest, progress, time.time())

    async def _finish(self, job, request, settings, dest, progress, started):
        job_id = self.job_id(job)
        deadline = started + self.timeout
        checked = getattr(progress, "checked", None)
        while True:
            job, done = await asyncio.to_thread(self.poll, job)
            if checked:
                checked()
            if done:
                break
            if time.time() >= deadline:
                await asyncio.to_thread(self.cancel, job)
                # The job may still finish on the provider's side (and be billed): never resubmitted
                raise ProviderError(Failure.TIMEOUT, f"the job did not finish within {self.timeout:g} s",
                                    provider=self.name, retryable=False)
            await asyncio.sleep(min(self.poll_seconds, max(0.0, deadline - time.time())))
        downloading = getattr(progress, "downloading", None)
        if downloading:
            downloading()
        metadata = await asyncio.to_thread(self.fetch, job, request, settings, dest)
        return self.result(request, settings, dest, started, provider_job_id=job_id, metadata=metadata or {})


# ---- safe downloads --------------------------------------------------------------------------------

class UnsafeUrl(ValueError):
    pass


def check_url(url, allowed_hosts, resolve=socket.getaddrinfo):
    """Only https URLs on an allowed host that resolves to public addresses (no loopback, private,
    link-local or reserved targets): what a server may fetch on a provider's behalf."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        raise UnsafeUrl("only https downloads are allowed")
    if host not in allowed_hosts:
        raise UnsafeUrl(f"{host or 'the host'} is not an allowed download host")
    try:
        addresses = {info[4][0] for info in resolve(host, parts.port or 443, proto=socket.IPPROTO_TCP)}
    except OSError as e:
        raise ProviderError(Failure.UNAVAILABLE, f"{host} could not be resolved ({e})")
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeUrl(f"{host} resolves to a non-public address")
    return url


class _AllowedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts):
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_download(url, dest, *, allowed_hosts, max_bytes, timeout, headers=None, expect_prefix=None, provider=None):
    """Downloads a provider's output: allowed hosts only (redirects too), size-capped, time-bounded.
    Returns the response's content type."""
    try:
        check_url(url, allowed_hosts)
        opener = urllib.request.build_opener(_AllowedRedirects(allowed_hosts))
        request = urllib.request.Request(url, headers=headers or {})
        with opener.open(request, timeout=timeout) as response:
            ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if expect_prefix and not ctype.startswith(expect_prefix):
                raise ProviderError(Failure.INVALID_OUTPUT, f"expected {expect_prefix}* but got {ctype or 'no content type'}",
                                    provider=provider)
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ProviderError(Failure.INVALID_OUTPUT, "the result is larger than allowed", provider=provider)
            total = 0
            with open(dest, "wb") as out:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ProviderError(Failure.INVALID_OUTPUT, "the result is larger than allowed", provider=provider)
                    out.write(chunk)
            return ctype
    except UnsafeUrl as e:
        raise ProviderError(Failure.INVALID_OUTPUT, f"refused to download: {e}", provider=provider, retryable=False)
    except ProviderError:
        raise
    except Exception as e:  # noqa: BLE001 - classified (HTTP status, timeout, network)
        raise error_from_exception(e, provider)


def max_bytes_for(media_type, env=None):
    env = os.environ if env is None else env
    return int(_env_float(env, "AI_MAX_IMAGE_BYTES" if media_type == IMAGE else "AI_MAX_VIDEO_BYTES",
                          20 * 1024 ** 2 if media_type == IMAGE else 500 * 1024 ** 2))


def write_bytes(data, dest, media_type, provider):
    if not data:
        raise ProviderError(Failure.INVALID_OUTPUT, "the provider returned no data", provider=provider)
    if len(data) > max_bytes_for(media_type):
        raise ProviderError(Failure.INVALID_OUTPUT, "the result is larger than allowed", provider=provider)
    with open(dest, "wb") as f:
        f.write(data)


# ---- adapters ----------------------------------------------------------------------------------------

class PollinationsProvider(SyncProvider):
    """Images from image.pollinations.ai (no key). The seed is random per call, so it is provenance, not identity."""
    name, label, env_prefix = "pollinations", "Pollinations", "POLLINATIONS"
    default_timeout = 90.0
    default_max_concurrent = 4
    BASE = "https://image.pollinations.ai/prompt/"
    HOSTS = frozenset({"image.pollinations.ai"})
    ASPECTS = ("2:3", "3:2", "1:1", "3:4", "4:3", "9:16", "16:9")

    def capabilities(self):
        return Capabilities(media_types=(IMAGE,), execution="sync", aspect_ratios=self.ASPECTS, max_prompt_chars=1500)

    def shape_parameters(self, request, parameters):
        width, height = size_for(request.aspect_ratio, parameters["width"], parameters["height"])
        return {**parameters, "width": width, "height": height}

    def url(self, prompt, parameters, seed):
        query = urllib.parse.urlencode({"width": parameters["width"], "height": parameters["height"],
                                        "nologo": "true" if parameters.get("nologo") else "false", "seed": seed})
        return f"{self.BASE}{urllib.parse.quote(prompt, safe='')}?{query}"

    def produce(self, request, settings, dest):
        seed = int(time.time() * 1000) % 100000
        safe_download(self.url(request.prompt, settings["parameters"], seed), dest, allowed_hosts=self.HOSTS,
                      max_bytes=max_bytes_for(IMAGE, self.env), timeout=self.timeout, expect_prefix="image/",
                      headers={"User-Agent": "Mozilla/5.0 (compatible; AadhiEduEngine)"}, provider=self.name)
        return {"seed": seed}


def _genai():
    from google import genai
    from google.genai import types
    return genai, types


class _GeminiKey:
    key_env = "GEMINI_API_KEY"

    def api_key(self):
        return self.env.get(self.key_env) or ""

    def client(self):
        genai, types = _genai()
        return genai.Client(api_key=self.api_key(), http_options=types.HttpOptions(timeout=int(self.timeout * 1000)))


class GeminiImageProvider(_GeminiKey, SyncProvider):
    """Images from Gemini image models (generate_content with IMAGE output) through the Gemini API.
    Billed per image, so it is used only when switched on (GEMINI_IMAGE_ENABLED=1) and a key is set."""
    name, label, env_prefix = "gemini-image", "Gemini image", "GEMINI_IMAGE"
    model_env = "GEMINI_IMAGE_MODEL"
    default_enabled = False
    default_timeout = 120.0
    ASPECTS = ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9")
    REFUSALS = {"SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "IMAGE_PROHIBITED_CONTENT", "BLOCKLIST", "SPII",
                "RECITATION", "IMAGE_RECITATION"}

    def capabilities(self):
        return Capabilities(media_types=(IMAGE,), execution="sync", models=tuple(dict.fromkeys(
            [self.default_model(IMAGE)] + _env_list(self.env, "GEMINI_IMAGE_MODELS", ""))),
            aspect_ratios=self.ASPECTS, paid=True)

    def configuration(self):
        if not self.enabled():
            return "disabled", "billed per image: set GEMINI_IMAGE_ENABLED=1 to use it"
        if not self.api_key():
            return "not_configured", "GEMINI_API_KEY is not set"
        return "available", ""

    def shape_parameters(self, request, parameters):
        return {**parameters, "aspect_ratio": request.aspect_ratio}

    def produce(self, request, settings, dest):
        _, types = _genai()
        try:
            response = self.client().models.generate_content(
                model=settings["model"], contents=request.prompt,
                config=types.GenerateContentConfig(response_modalities=["IMAGE"],
                                                   image_config=types.ImageConfig(aspect_ratio=settings["parameters"]["aspect_ratio"])))
        except Exception as e:  # noqa: BLE001
            raise error_from_exception(e, self.name)
        feedback = getattr(response, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            raise ProviderError(Failure.REJECTED, f"the prompt was blocked ({feedback.block_reason})", provider=self.name)
        for candidate in response.candidates or []:
            for part in (candidate.content.parts if candidate.content and candidate.content.parts else []):
                blob = part.inline_data
                if blob is not None and blob.data and (blob.mime_type or "").startswith("image/"):
                    write_bytes(blob.data, dest, IMAGE, self.name)
                    return {"model_version": getattr(response, "model_version", None)}
            reason = str(getattr(candidate.finish_reason, "name", candidate.finish_reason) or "")
            if reason in self.REFUSALS:
                raise ProviderError(Failure.REJECTED, f"the image was refused ({reason})", provider=self.name)
        raise ProviderError(Failure.INVALID_OUTPUT, "no image was returned", provider=self.name)


class VeoProvider(_GeminiKey, JobProvider):
    """Videos from Google Veo through the Gemini API (generate_videos, then operations.get until done,
    then files.download). Billed per second of video: usable when the AI server was started with Gemini
    ("Start AI Server") or when VEO_ENABLED=1 allows it as a fallback."""
    name, label, env_prefix = "veo", "Google Veo", "VEO"
    model_env = "VEO_MODEL"
    default_timeout = 300.0
    default_poll_seconds = 10.0
    default_max_concurrent = 2

    def __init__(self, env=None, video_mode=lambda: None):
        super().__init__(env)
        self.video_mode = video_mode

    def enabled(self):
        return self.video_mode() == "veo" or _env_flag(self.env, "VEO_ENABLED", False)

    def configuration(self):
        if not self.api_key():
            return "not_configured", "GEMINI_API_KEY is not set"
        if not self.enabled():
            return "disabled", "start the AI server with Gemini, or set VEO_ENABLED=1 to allow it as a fallback"
        return "available", ""

    def capabilities(self):
        model = self.default_model(VIDEO) or ""
        return Capabilities(media_types=(VIDEO,), execution="job",
                            models=tuple(dict.fromkeys([model] + _env_list(self.env, "VEO_MODELS", ""))),
                            aspect_ratios=("16:9", "9:16"), durations=(5, 8) if model.startswith("veo-2") else None,
                            negative_prompt=True, audio=model.startswith("veo-3"), paid=True)

    def shape_parameters(self, request, parameters):
        return {**parameters, "aspect_ratio": request.aspect_ratio}

    def duration_parameters(self, request, parameters):
        return {**parameters, "duration_seconds": int(round(request.duration_seconds))}

    def submit(self, request, settings):
        _, types = _genai()
        params = settings["parameters"]
        config = {"aspect_ratio": params["aspect_ratio"], "person_generation": params["person_generation"]}
        for optional in ("duration_seconds", "negative_prompt"):
            if params.get(optional) is not None:
                config[optional] = params[optional]
        client = self.client()
        try:
            operation = client.models.generate_videos(model=settings["model"], prompt=request.prompt,
                                                      config=types.GenerateVideosConfig(**config))
        except Exception as e:  # noqa: BLE001
            raise error_from_exception(e, self.name)
        return {"client": client, "operation": operation}

    def job_id(self, job):
        return getattr(job["operation"], "name", None)

    def job_handle(self, job):
        return {"job_id": self.job_id(job), "operation": self.job_id(job)}

    def restore_job(self, handle):
        """The long-running operation again from its name (operations.get polls it by name)."""
        _, types = _genai()
        name = (handle or {}).get("operation")
        if not name:
            raise ProviderError(Failure.LOST, "no operation name was saved for this job", provider=self.name)
        return {"client": self.client(), "operation": types.GenerateVideosOperation(name=name)}

    def poll(self, job):
        try:
            job["operation"] = job["client"].operations.get(job["operation"])
        except Exception as e:  # noqa: BLE001
            error = error_from_exception(e, self.name)
            if error.status_code == 404:  # the operation is gone (expired or unknown)
                raise ProviderError(Failure.LOST, f"the provider no longer has this job ({error.message})", provider=self.name)
            raise error
        operation = job["operation"]
        if operation.done and operation.error:
            error = operation.error if isinstance(operation.error, dict) else {}
            code = error.get("code")
            message = error.get("message") or str(operation.error)
            if code == 3:  # INVALID_ARGUMENT
                raise ProviderError(Failure.REJECTED, f"the request was refused ({message})", provider=self.name)
            if code == 8:  # RESOURCE_EXHAUSTED
                raise ProviderError(Failure.RATE_LIMITED, f"quota exhausted ({message})", provider=self.name, retryable=False)
            raise ProviderError(Failure.UNAVAILABLE, f"the job failed ({message})", provider=self.name, retryable=False)
        return job, bool(operation.done)

    def fetch(self, job, request, settings, dest):
        response = job["operation"].response
        videos = list(getattr(response, "generated_videos", None) or [])
        if not videos:
            filtered = getattr(response, "rai_media_filtered_count", None)
            if filtered:
                raise ProviderError(Failure.REJECTED, f"the video was filtered by the provider's safety rules ({filtered})",
                                    provider=self.name)
            raise ProviderError(Failure.INVALID_OUTPUT, "no video was returned", provider=self.name)
        video = videos[0].video
        try:
            data = job["client"].files.download(file=video)
        except Exception as e:  # noqa: BLE001
            raise error_from_exception(e, self.name)
        write_bytes(data or getattr(video, "video_bytes", None), dest, VIDEO, self.name)
        return {}


class LtxProvider(SyncProvider):
    """Videos from the local LTX-Video pipeline, loaded by "Start AI Server" with a GPU profile."""
    name, label, env_prefix = "ltx", "LTX-Video (local GPU)", "LTX"
    default_timeout = 1800.0
    default_max_concurrent = 1

    def __init__(self, env=None, pipeline=lambda: None):
        super().__init__(env)
        self.pipeline = pipeline

    def loaded(self):
        pipe = self.pipeline()
        return pipe is not None and not isinstance(pipe, str)

    def configuration(self):
        if not self.enabled():
            return "disabled", "switched off (LTX_ENABLED=0)"
        if not self.loaded():
            return "not_configured", "start the AI server with an LTX (GPU) profile"
        return "available", ""

    def capabilities(self):
        return Capabilities(media_types=(VIDEO,), execution="local", models=(self.default_model(VIDEO),),
                            aspect_ratios=("16:9",), negative_prompt=True)

    def produce(self, request, settings, dest):
        params = settings["parameters"]
        try:
            import gc
            import torch
            from diffusers.utils import export_to_video
            frames = self.pipeline()(
                prompt=request.prompt, negative_prompt=params["negative_prompt"], width=params["width"], height=params["height"],
                num_frames=params["num_frames"], num_inference_steps=params["num_inference_steps"],
                guidance_scale=params["guidance_scale"], generator=torch.Generator().manual_seed(params["seed"])).frames[0]
            gc.collect()
            torch.cuda.empty_cache()
            export_to_video(frames, dest, fps=params["fps"])
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001 - e.g. out of GPU memory: another provider may be tried, not the same again
            raise ProviderError(Failure.UNAVAILABLE, f"the local pipeline failed ({type(e).__name__}: {e})",
                                provider=self.name, retryable=False)
        return {"seed": params["seed"]}


class ManualProvider(AIProvider):
    """The manual workflow: the user makes the video and saves it under the name the app gives."""
    name, label, env_prefix = "manual", "Manual (you make the video)", "MANUAL"

    def capabilities(self):
        return Capabilities(media_types=(VIDEO,), execution="manual")

    def check(self, request):
        super().check(request)
        if request.force_regenerate:
            raise ProviderError(Failure.UNSUPPORTED, "in the manual workflow you replace the video file yourself",
                                provider=self.name)


class FakeProvider(JobProvider):
    """Local stand-in for automated tests (AI_FAKE_PROVIDER=1): a small clip or picture made with ffmpeg,
    a new colour every time. Videos behave like a provider job (started, polled, fetched).

    AI_FAKE_FAIL="<provider>:<media>:<failure>[,...]" makes it fail, e.g. "fake:video:unavailable";
    failures: unavailable, rate_limited, timeout, rejected, auth (when the job is started), slow (the job
    never finishes), invalid_output (not media), blank (one flat colour), jobfail (the job fails on the
    provider's side), vanish (after a restart the provider no longer knows the job).

    Like a real provider, its jobs live outside the server process (one JSON file per job in
    AI_FAKE_STATE_DIR), so they survive a server restart and can be recovered; <NAME>_JOB_SECONDS makes
    them take that long, <NAME>_PAID=1 makes them count as billed (for the duplicate-billing rules)."""
    execution_media = (IMAGE, VIDEO)
    default_poll_seconds = 0.2
    default_timeout = 60.0
    default_max_concurrent = 4
    cancel_supported = True

    def __init__(self, name, label, env=None):
        super().__init__(env)
        self.name, self.label, self.env_prefix = name, label, name.upper().replace("-", "_")

    def capabilities(self):
        return Capabilities(media_types=(IMAGE, VIDEO), execution="job", models=(),
                            aspect_ratios=("16:9", "9:16", "1:1", "2:3", "3:2"), durations=(1, 10),
                            paid=_env_flag(self.env, f"{self.env_prefix}_PAID", False))

    # -- the stand-in's own job store (its "server side") --
    def _store(self):
        import tempfile
        path = self.env.get("AI_FAKE_STATE_DIR") or os.path.join(tempfile.gettempdir(), "aadhi-fake-provider")
        os.makedirs(path, exist_ok=True)
        return path

    def _job_file(self, job_id):
        return os.path.join(self._store(), f"{job_id}.json")

    def _save_job(self, job):
        import json as _json
        stored = {k: v for k, v in job.items() if k != "polls"}
        with open(self._job_file(job["id"]), "w", encoding="utf-8") as f:
            _json.dump(stored, f)

    def _load_job(self, job_id):
        import json as _json
        try:
            with open(self._job_file(job_id), encoding="utf-8") as f:
                return _json.load(f)
        except (OSError, ValueError):
            return None

    def shape_parameters(self, request, parameters):
        width, height = size_for(request.aspect_ratio, parameters["width"], parameters["height"])
        return {**parameters, "width": width, "height": height}

    def duration_parameters(self, request, parameters):
        return {**parameters, "seconds": round(request.duration_seconds, 1)}

    def failure(self, media_type):
        for rule in _env_list(self.env, "AI_FAKE_FAIL", ""):
            name, _, rest = rule.partition(":")
            media, _, kind = rest.partition(":")
            if name == self.name and media in (media_type, "*"):
                return kind
        return None

    def raise_failure(self, kind):
        if kind == "rate_limited":
            raise ProviderError(Failure.RATE_LIMITED, "rate limited (HTTP 429)", provider=self.name, retry_after=0.2)
        if kind in ("unavailable", "timeout", "rejected", "auth"):
            category = {"unavailable": Failure.UNAVAILABLE, "timeout": Failure.TIMEOUT, "rejected": Failure.REJECTED,
                        "auth": Failure.AUTH}[kind]
            raise ProviderError(category, f"stand-in failure: {kind}", provider=self.name)

    def make(self, media_type, params, dest, blank=False):
        colour = uuid.uuid4().hex[:6]
        if media_type == VIDEO:
            box = "" if blank else ",drawbox=x='mod(t*160,560)':y=140:w=80:h=80:c=white:t=fill"
            args = ["-f", "lavfi", "-i", f"color=c=0x{colour}:size={params['width']}x{params['height']}:rate=25:duration={params['seconds']}",
                    "-vf", f"format=yuv420p{box}", "-pix_fmt", "yuv420p", "-c:v", "libx264"]
        else:
            box = "" if blank else ",drawbox=x=iw/4:y=ih/4:w=iw/2:h=ih/2:c=white:t=fill"
            args = ["-f", "lavfi", "-i", f"color=c=0x{colour}:size={params['width']}x{params['height']}",
                    "-vf", f"format=yuv420p{box}", "-frames:v", "1"]
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, dest], check=True, timeout=60)

    def submit(self, request, settings):
        kind = self.failure(request.media_type)
        self.raise_failure(kind)
        job = {"id": f"{self.name}-{uuid.uuid4().hex[:10]}", "polls": 0, "kind": kind, "media_type": request.media_type,
               "created": time.time(), "ready_after": _env_float(self.env, f"{self.env_prefix}_JOB_SECONDS", 0),
               "cancelled": False}
        self._save_job(job)
        self.submitted = getattr(self, "submitted", 0) + 1  # provider-side jobs started (tests count duplicates)
        return job

    def job_id(self, job):
        return job["id"]

    def job_handle(self, job):
        return {"job_id": job["id"]}

    def restore_job(self, handle):
        job = self._load_job((handle or {}).get("job_id", ""))
        if job is None:
            raise ProviderError(Failure.LOST, "the provider has no such job", provider=self.name)
        if job.get("kind") == "vanish":  # the provider forgot it while the server was down
            os.remove(self._job_file(job["id"]))
            raise ProviderError(Failure.LOST, "the provider no longer knows this job", provider=self.name)
        job["polls"] = 0
        return job

    def cancel(self, job):
        job["cancelled"] = True
        if self._load_job(job["id"]) is not None:
            self._save_job(job)
        self.cancelled = getattr(self, "cancelled", 0) + 1
        return True

    def poll(self, job):
        job["polls"] += 1
        stored = self._load_job(job["id"])
        if stored is None:
            raise ProviderError(Failure.LOST, "the provider no longer knows this job", provider=self.name)
        if stored.get("cancelled"):
            raise ProviderError(Failure.CANCELLED, "the job was cancelled", provider=self.name, retryable=False)
        if job["kind"] == "jobfail":
            raise ProviderError(Failure.UNAVAILABLE, "the job failed on the provider's side", provider=self.name, retryable=False)
        if job["kind"] in ("slow", "vanish"):
            return job, False
        return job, job["polls"] >= 2 and time.time() - stored["created"] >= stored.get("ready_after", 0)

    def fetch(self, job, request, settings, dest):
        if job["kind"] == "invalid_output":
            with open(dest, "wb") as f:
                f.write(b"not a picture " * 64)
            return {}
        self.make(request.media_type, settings["parameters"], dest, blank=job["kind"] == "blank")
        return {}

    async def generate(self, request, settings, dest, progress=None):
        if request.media_type == IMAGE and not self.failure(IMAGE):
            started = time.time()  # pictures come back in one call, like most image providers
            await asyncio.to_thread(self.make, IMAGE, settings["parameters"], dest)
            return self.result(request, settings, dest, started)
        return await super().generate(request, settings, dest, progress)


class FakePresenterProvider(FakeProvider):
    """Local presenter stand-in for automated tests (AI_FAKE_PROVIDER=1): a drawn teacher whose mouth opens
    and closes with the loudness of the supplied narration (the speech timeline's envelope), the profile's
    colours, and simple poses for the gestures it declares. Jobs behave like the video stand-in's (a provider
    job that survives a restart, can be cancelled, can be made to fail with AI_FAKE_FAIL=fake-presenter:presenter:<kind>).
    It declares only what it really does: the mouth follows the audio's loudness (lip_sync), a few expressions
    and gestures, a solid background (no transparency), no reference image."""
    EXPRESSIONS = ("neutral", "friendly", "engaged", "happy", "thinking")
    GESTURES = ("none", "open_hand", "point", "explaining", "welcome")

    def capabilities(self):
        return Capabilities(media_types=(PRESENTER,), execution="job", models=(), aspect_ratios=("3:5",), durations=(0.5, 300),
                            inputs=("text", "audio"), paid=_env_flag(self.env, f"{self.env_prefix}_PAID", False),
                            lip_sync=True, audio_input=True, expressions=self.EXPRESSIONS, gestures=self.GESTURES,
                            transparent_background=False, reference_image=False, consistent_identity=True, cancellation=True)

    def settings_for(self, request):
        settings = super().settings_for(request)
        from presenters import EXPRESSION_FALLBACK, GESTURE_FALLBACK, map_vocab  # (presenters imports this module)
        p = request.presenter or {}
        speech = p.get("speech") or {}
        expression, e_note = map_vocab(p.get("expression"), self.EXPRESSIONS, EXPRESSION_FALLBACK, "neutral")
        gesture, g_note = map_vocab(p.get("gesture"), self.GESTURES, GESTURE_FALLBACK, "none")
        settings["parameters"].update({
            "presenter_id": p.get("presenter_id"), "profile_version": p.get("profile_version"), "appearance": p.get("appearance_hash"),
            "audio": speech.get("audio_sha256"), "behavior": p.get("behavior"), "expression": expression, "gesture": gesture,
            "background": p.get("background"), "fallbacks": [n for n in (e_note, g_note) if n]})
        return settings

    def make(self, media_type, params, dest, blank=False, request=None):
        p = (request.presenter if request else None) or {}
        palette = (p.get("appearance") or {}).get("palette") or ["#5B2A86", "#FFD700", "#F2C9A0"]
        blazer, accent, skin = (palette + ["#5B2A86", "#FFD700", "#F2C9A0"])[:3]
        hex_ = lambda c: "0x" + c.lstrip("#")[:6]  # noqa: E731
        w, h, seconds = params["width"], params["height"], params["seconds"]
        speech = p.get("speech") or {}
        fps = speech.get("fps") or 25
        envelope = speech.get("envelope") or []
        # Mouth-open intervals from the narration's loudness (merged; the filter expression stays short)
        spans, start = [], None
        for i, level in enumerate(envelope + [0]):
            if level >= 0.3 and start is None:
                start = i
            elif level < 0.3 and start is not None:
                spans.append((start / fps, i / fps))
                start = None
        merged = []
        for a, b in spans:
            if merged and a - merged[-1][1] < 0.08:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))
        merged = merged[:400]
        talking = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in merged) or "0"
        arm_y = {"welcome": 0.36, "point": 0.40, "open_hand": 0.50, "explaining": 0.46}.get(params.get("gesture"), 0.62)
        smile = 6 if params.get("expression") in ("friendly", "happy", "engaged") else 2
        boxes = [
            f"drawbox=x={w*0.2:.0f}:y={h*0.45:.0f}:w={w*0.6:.0f}:h={h*0.55:.0f}:c={hex_(blazer)}:t=fill",       # body
            f"drawbox=x={w*0.44:.0f}:y={h*0.45:.0f}:w={w*0.12:.0f}:h={h*0.2:.0f}:c={hex_(accent)}:t=fill",      # tie / collar
            f"drawbox=x={w*0.3:.0f}:y={h*0.12:.0f}:w={w*0.4:.0f}:h={h*0.3:.0f}:c={hex_(skin)}:t=fill",          # head
            f"drawbox=x={w*0.38:.0f}:y={h*0.22:.0f}:w={w*0.06:.0f}:h={h*0.03:.0f}:c=0x222222:t=fill",           # eyes
            f"drawbox=x={w*0.56:.0f}:y={h*0.22:.0f}:w={w*0.06:.0f}:h={h*0.03:.0f}:c=0x222222:t=fill",
            f"drawbox=x={w*0.42:.0f}:y={h*0.33:.0f}:w={w*0.16:.0f}:h={smile}:c=0x7a2a2a:t=fill",                # closed mouth
            f"drawbox=x={w*0.43:.0f}:y={h*0.31:.0f}:w={w*0.14:.0f}:h={h*0.06:.0f}:c=0x5a1010:t=fill:enable='{talking}'",  # open mouth
            f"drawbox=x={w*0.72:.0f}:y={h*arm_y:.0f}:w={w*0.2:.0f}:h={h*0.06:.0f}:c={hex_(blazer)}:t=fill",     # gesturing arm
        ]
        background = "0x1A0B2E" if p.get("background") != "light" else "0xEEEAF6"
        args = ["-f", "lavfi", "-i", f"color=c={background}:size={w}x{h}:rate={fps}:duration={seconds}",
                "-vf", "format=yuv420p," + ",".join([] if blank else boxes), "-pix_fmt", "yuv420p", "-c:v", "libx264", "-an"]
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args, dest], check=True, timeout=120)

    def fetch(self, job, request, settings, dest):
        if job["kind"] == "invalid_output":
            with open(dest, "wb") as f:
                f.write(b"not a video " * 64)
            return {}
        self.make(PRESENTER, settings["parameters"], dest, blank=job["kind"] == "blank", request=request)
        return {"lip_sync": True}

    async def generate(self, request, settings, dest, progress=None):
        return await JobProvider.generate(self, request, settings, dest, progress)


# ---- the registry ---------------------------------------------------------------------------------------

@dataclass
class Candidate:
    name: str
    usable: bool                  # may generate now
    reason: str = ""              # why not (or "")
    provider: AIProvider | None = None
    supported: bool = True        # can make this request at all (its earlier results can count as this request)
    cooling: bool = False         # failed recently: tried after the providers that did not (never excluded)

    def to_dict(self):
        return {"provider": self.name, "usable": self.usable, "supported": self.supported, "cooling": self.cooling,
                "reason": self.reason}


@dataclass
class Selection:
    media_type: str
    candidates: list
    explicit: bool
    fallback: bool

    @property
    def usable(self):
        """The providers to try, in order: those that did not fail recently first, then the ones that did
        (a provider that just failed is still tried when nothing else can make the request)."""
        return sorted((c for c in self.candidates if c.usable), key=lambda c: c.cooling)

    def explain(self):
        return [c.to_dict() for c in self.candidates]


class ProviderRegistry:
    """The providers of this server and the one place a provider is chosen for a request."""

    def __init__(self, env=None, video_mode=lambda: None, ltx_pipeline=lambda: None, fake=False):
        self.env = os.environ if env is None else env
        self.video_mode = video_mode      # the AI server's video provider ("Start AI Server"): veo | ltx | manual | fake | None
        self.fake = fake
        self._providers = {}
        self._cooldown = {}               # provider -> (until, reason): recent transient failures
        self._busy = {}                   # provider -> generations running now
        self._lock = threading.Lock()
        if fake:  # tests: only the stand-ins exist, so no real provider can ever be reached
            self.register(FakeProvider("fake", "Test stand-in", self.env))
            self.register(FakeProvider("fake-alt", "Test stand-in (backup)", self.env))
            self.register(FakePresenterProvider("fake-presenter", "Test presenter stand-in", self.env))
        else:
            self.register(PollinationsProvider(self.env))
            self.register(GeminiImageProvider(self.env))
            self.register(VeoProvider(self.env, video_mode))
            self.register(LtxProvider(self.env, ltx_pipeline))
            self.register(ManualProvider(self.env))

    def register(self, provider):
        self._providers[provider.name] = provider
        return provider

    def get(self, name):
        return self._providers.get(name)

    def names(self):
        return list(self._providers)

    # -- preference and order --
    def preferred(self, media_type):
        if media_type == PRESENTER:
            wanted = self.env.get("AI_PRESENTER_PROVIDER")
            order = self.order(PRESENTER, with_preferred=False)
            return wanted if wanted in order else (order[0] if order else None)
        if media_type == VIDEO:
            mode = self.video_mode()
            return mode if mode in self._providers else None
        wanted = self.env.get("AI_IMAGE_PROVIDER")
        if wanted in self._providers:
            return wanted
        return self.order(IMAGE, with_preferred=False)[0] if self.order(IMAGE, with_preferred=False) else None

    def order(self, media_type, with_preferred=True):
        """Preferred provider first, then the configured fallback order (AI_IMAGE_PROVIDERS / AI_VIDEO_PROVIDERS)."""
        if media_type == PRESENTER:
            # No provider on this server makes a presenter speak a supplied narration (Veo makes video from text or a
            # picture, not from our audio), so the order is empty unless one is configured (AI_PRESENTER_PROVIDERS)
            default = "fake-presenter" if self.fake else ""
            names = _env_list(self.env, "AI_PRESENTER_PROVIDERS", default)
            if self.fake:
                names = [n for n in names if n in self._providers] or [n for n in default.split(",") if n]
            if self.env.get("AI_PRESENTER_PROVIDER"):
                names = [self.env["AI_PRESENTER_PROVIDER"]] + names
        else:
            default = ("fake,fake-alt" if self.fake else ("pollinations,gemini-image" if media_type == IMAGE else "veo,ltx"))
            names = _env_list(self.env, "AI_IMAGE_PROVIDERS" if media_type == IMAGE else "AI_VIDEO_PROVIDERS", default)
            if self.fake:
                names = [n for n in names if n in self._providers] or default.split(",")
        if with_preferred and media_type == PRESENTER:
            with_preferred = False  # preferred(PRESENTER) is itself chosen from this order
        if with_preferred:
            first = self.preferred(media_type)
            names = ([first] if first else []) + names
        ordered = []
        for name in names:
            provider = self._providers.get(name)
            if provider and media_type in provider.capabilities().media_types and name not in ordered:
                if name == "manual" and ordered:  # the manual workflow is a choice, never a fallback
                    continue
                ordered.append(name)
        if ordered[:1] == ["manual"]:  # "No AI generation (manual workflow)": nothing is generated automatically
            return ["manual"]
        return ordered

    def fallback_enabled(self):
        return _env_flag(self.env, "AI_PROVIDER_FALLBACK", True)

    # -- availability (configuration and recent failures; never a network call) --
    def state(self, name):
        provider = self._providers[name]
        state, reason = provider.configuration()
        if state == "available":
            cooling = self.cooling(name)
            if cooling:
                return "temporarily_unavailable", cooling
        return state, reason

    def cooling(self, name):
        """Why the provider failed recently ("" if it did not): it is tried after the others for a while."""
        with self._lock:
            until, why = self._cooldown.get(name, (0, ""))
        if until > time.time():
            return f"{why} recently; tried after the others for {max(1, int(until - time.time()))} s"
        return ""

    def note_failure(self, name, error):
        if error.category in (Failure.RATE_LIMITED, Failure.UNAVAILABLE, Failure.TIMEOUT):
            wait = error.retry_after if error.retry_after is not None else _env_float(self.env, "AI_PROVIDER_COOLDOWN", 60)
            why = error.category.replace("_", " ")
        elif error.category == Failure.AUTH:  # refused credentials or payment: it will keep failing until someone acts
            wait = _env_float(self.env, "AI_PROVIDER_AUTH_COOLDOWN", 600)
            why = "payment or credentials refused" if error.status_code == 402 else "credentials refused"
        else:
            return
        with self._lock:
            self._cooldown[name] = (time.time() + wait, why)

    def note_success(self, name):
        with self._lock:
            self._cooldown.pop(name, None)

    # -- concurrency (works across threads and event loops; cancellation-safe) --
    async def slot(self, name, wait_seconds=None):
        provider = self._providers[name]
        deadline = time.time() + (wait_seconds if wait_seconds is not None else _env_float(self.env, "AI_PROVIDER_QUEUE_SECONDS", 600))
        while True:
            with self._lock:
                if self._busy.get(name, 0) < provider.max_concurrent:
                    self._busy[name] = self._busy.get(name, 0) + 1
                    return
            if time.time() >= deadline:
                raise ProviderError(Failure.UNAVAILABLE, "too many generations are running; try again shortly", provider=name)
            await asyncio.sleep(0.1)

    def release(self, name):
        with self._lock:
            self._busy[name] = max(0, self._busy.get(name, 0) - 1)

    # -- the choice --
    def select(self, request):
        """Every provider that could serve the request, in order, each marked usable or not (with why).
        Deterministic: preference, then the configured order; availability comes from configuration and
        recent failures only."""
        explicit = request.provider is not None
        fallback = request.fallback_allowed() and self.fallback_enabled()
        if explicit:
            names = [request.provider] + ([n for n in self.order(request.media_type) if n != request.provider] if fallback else [])
        else:
            names = self.order(request.media_type)
            if not fallback:
                names = names[:1]
        candidates = []
        for name in names:
            provider = self._providers.get(name)
            if provider is None:
                candidates.append(Candidate(name, False, "no such provider on this server", supported=False))
                continue
            state, reason = provider.configuration()
            try:
                provider.check(request)
            except ProviderError as e:
                candidates.append(Candidate(name, False, e.message, provider, supported=False))
                continue
            cooling = self.cooling(name) if state == "available" else ""
            candidates.append(Candidate(name, state == "available", reason if state != "available" else cooling, provider,
                                        cooling=bool(cooling)))
        return Selection(request.media_type, candidates, explicit, fallback)

    def status(self):
        """What an administrator may see: states, reasons, capabilities, order. Never a secret."""
        providers, presenters = [], []
        for name, provider in self._providers.items():
            state, reason = self.state(name)
            caps = provider.capabilities()
            entry = {"name": name, "label": provider.label, "state": state, "reason": sanitize(reason),
                     "media_types": list(caps.media_types), "models": [m for m in caps.models if m],
                     "paid": caps.paid, "execution": caps.execution, "max_concurrent": provider.max_concurrent,
                     "busy": self._busy.get(name, 0), "capabilities": caps.to_dict()}
            # Image / video providers as before (Phase 8); presenter providers (Phase 12) in their own section
            (presenters if set(caps.media_types) == {PRESENTER} else providers).append(entry)
        return {"providers": providers,
                "order": {m: self.order(m) for m in (IMAGE, VIDEO)},
                "preferred": {m: self.preferred(m) for m in (IMAGE, VIDEO)},
                "fallback": self.fallback_enabled(),
                "presenter": {"providers": presenters, "order": self.order(PRESENTER), "preferred": self.preferred(PRESENTER)}}
