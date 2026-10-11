"""Google Gemini Omni B-roll generation (``gemini-omni-1.1-flash`` through the Gemini Interactions API).

Google lists the Veo 3.1 previews (``VEO_MODEL``'s default ``veo-3.1-fast-generate-preview``) for shutdown
from 2026-10-22 with ``gemini-omni-1.1-flash`` as their replacement, which is served only through the
Interactions API (``client.aio.interactions`` in google-genai). Sources, read 2026-10-10 and checked
against google-genai 2.26: ai.google.dev/gemini-api/docs/omni, /docs/background-execution,
/docs/deprecations, /docs/pricing and the Interactions API reference.

Request (one interaction per clip):

* ``interactions.create(model=OMNI_MODEL, input=..., background=True, response_format={"type": "video",
  "aspect_ratio": "16:9" | "9:16", "resolution": "720p"})``. ``store`` keeps its default (on): background
  runs need it, and the interaction id is what a restarted worker resumes. The video is delivered inline
  (base64), which is also what ``interactions.get`` returns; a ``uri`` is downloaded with ``files.download``.
* ``input`` is the prompt; with a ``reference_image`` the image goes first (as in Google's image-to-video
  examples) and the model decides from the prompt whether it is the first frame or a reference.
* Omni takes no negative prompt, so the exclusions the Veo adapter sends as ``negative_prompt`` are
  written into the prompt. No clip length is sent (the API documents none): the model picks it.
* Omni generates an audio track by default; the player and the MP4 render keep B-roll muted.

Polling: ``interactions.get(id)`` every ``poll_interval_s`` until the status leaves ``queued`` /
``in_progress``; ``completed`` with an ``output_video`` is the clip, any other end state is a failure (a
safety wording in its errors is ``ContentBlocked``). A job given up at the local deadline is cancelled
(best effort): nobody will collect it.

Billing: Omni is billed per token: USD 17.50 per 1M video output tokens at 5,792 tokens per second of 720p
video (about USD 0.10 per second; ``aadhi.usage.pricing``, provider ``omni``), USD 1.50 per 1M input tokens.
``Usage.seconds`` is the interaction's video output tokens / 5,792 (the 720p-equivalent seconds the price
row is per), so the cost follows the clip Google actually made; input and text tokens are recorded but not
priced (well under 1% of a clip). Without token usage the estimate ``ESTIMATED_SECONDS`` is reported
(``meta.estimated``), which is also the budget estimate before a clip (``billed_seconds``).

The paid-job rules are the Veo adapter's (``aadhi.providers.video.veo``): usage is reported the moment the
interaction reports a video (before the download and probe), an estimated ``Usage`` with
``meta.status == "abandoned"`` is reported when an accepted job is given up (deadline, polling failure,
cancellation), ``checkpoint.submitted(interaction id, key fingerprint)`` is awaited before polling so a
restarted worker calls ``resume`` (which never submits) with the key that created the job, every reported
charge is noted on the checkpoint right away, and ``checkpoint.sending()`` / ``not_sent()`` surround each
create request so only a request without an answer leaves the record ``submitting`` (ambiguous). A failed
interaction that still reports output tokens is reported with ``meta.status == "failed"``.

Errors: the Interactions SDK raises its own error classes (``status_code``, ``body``). They are translated
to ``google.genai.errors.APIError`` and the builtin timeout / connection errors the Gemini plumbing
classifies, so retries, key rotation after a 429, personal-key rejection and redaction behave as for the
other Gemini adapters (``aadhi.providers.gemini_common``). The SDK's own retries are switched off on the
interactions resource: a silent re-send of a paid create after a transport error could start (and bill) a
second clip nobody follows. A 404 / 410 on create (the model is not served, e.g. ``OMNI_MODEL`` misspelt or
retired) is ``ProviderUnavailable``: the media chain moves on and tries omni after the others for a while.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
import sys
import time
from typing import Any

from ...config import Settings
from .._common import emit_usage, fingerprint, make_error, safe_attr
from .._lazy import import_off_loop
from .._retry import SleepFn, call_with_retries
from ..base import (
    ContentBlocked,
    ImageInput,
    InvalidMediaOutput,
    OperationCheckpoint,
    OperationLost,
    PaidSubmissionUncertain,
    ProviderError,
    ProviderUnavailable,
    Usage,
    UsageSink,
    VideoResult,
)
from ..gemini_common import GEMINI_HOST, ClientFactory, GeminiCaller, classify_gemini_error, gemini_error, genai_types
from ..image.verify import media_warnings
from ..media import probe_media, sniff_extension

log = logging.getLogger(__name__)

OMNI_ASPECTS = frozenset({"16:9", "9:16"})
RESOLUTION = "720p"  # Omni's default; 1080p and 4k are upscaled (and billed by their own token counts)
VIDEO_TOKENS_PER_SECOND = 5792  # output tokens per second of 720p video (Gemini API pricing page, 2026-10-09)
ESTIMATED_SECONDS = 10.0  # billing estimate of one clip without token usage (Omni makes up to 10 s per generation)
MIN_VIDEO_SECONDS = 0.5  # shorter output is not a usable clip
EXCLUSIONS = "No on-screen text, captions, subtitles, watermarks or logos; no cartoon mascot; no distorted faces."
RUNNING = frozenset({"queued", "in_progress"})
CANCEL_TIMEOUT_S = 15.0
_SAFETY = re.compile(r"(?i)(safety|responsible ai|\brai\b|filtered|blocked|prohibited|policy)")
_NEXTGEN_MODULE = "google.genai._gaos.google_genai"  # client.aio.interactions imports it on first use
_COMPAT_ERRORS_MODULE = "google.genai._gaos.lib.compat_errors"  # the Interactions SDK's error classes
_retries_warned = False


def _status(interaction: Any) -> str:
    return str(safe_attr(interaction, "status", default="") or "").lower()


def _sdk_error_classes() -> tuple[type, type | None, type | None] | None:
    """``(APIError, APIConnectionError, APITimeoutError)`` of the Interactions SDK once it is loaded."""
    module = sys.modules.get(_COMPAT_ERRORS_MODULE)
    base = getattr(module, "APIError", None)
    if not isinstance(base, type):
        return None
    conn, timeout = getattr(module, "APIConnectionError", None), getattr(module, "APITimeoutError", None)
    return base, conn if isinstance(conn, type) else None, timeout if isinstance(timeout, type) else None


def translate_error(exc: BaseException) -> BaseException:
    """An Interactions SDK error as the Gemini plumbing knows it (``exc`` itself for anything else).

    HTTP errors become ``google.genai.errors.ClientError`` / ``ServerError`` with the error body (so a
    ``RetryInfo`` delay and Gemini's invalid-key wording survive), a request timeout ``TimeoutError`` and a
    request without an answer ``ConnectionError``."""
    classes = _sdk_error_classes()
    if classes is None or not isinstance(exc, classes[0]):
        return exc
    _, conn, timeout = classes
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 400:
        from google.genai import errors as genai_errors

        body = getattr(exc, "body", None)
        payload = body if isinstance(body, dict) else {"error": {"code": status, "message": str(body or "")[:500]}}
        cls = genai_errors.ServerError if status >= 500 else genai_errors.ClientError
        return cls(status, payload)
    if timeout is not None and isinstance(exc, timeout):
        return TimeoutError("the Interactions API request timed out")
    if conn is not None and isinstance(exc, conn):
        return ConnectionError("the Interactions API request got no answer")
    return exc


async def _sdk(awaitable: Any) -> Any:
    """Await one Interactions SDK call, translating its errors (``translate_error``)."""
    try:
        return await awaitable
    except Exception as exc:
        translated = translate_error(exc)
        if translated is exc:
            raise
        raise translated from exc


def _no_sdk_retries(resource: Any) -> None:
    """Switch off the Interactions SDK's own retries on ``resource`` (this adapter retries itself, and only
    where a re-send cannot start a second paid job unnoticed). Never raises."""
    global _retries_warned
    config = getattr(resource, "sdk_configuration", None)
    if config is None:
        return
    try:
        from google.genai._gaos.utils import RetryConfig

        config.retry_config = RetryConfig("none", None, False)
    except Exception:  # noqa: BLE001 - another SDK layout: its own retries stay on
        if not _retries_warned:
            _retries_warned = True
            log.warning("omni: could not switch off the SDK's own retries; a create request that got no answer "
                        "may be re-sent by the SDK")


def interactions_of(client: Any) -> Any:
    """``client.aio.interactions`` with the SDK's own retries switched off."""
    resource = client.aio.interactions
    _no_sdk_retries(resource)
    return resource


def _video_tokens(usage: Any) -> int:
    """Video output tokens of an interaction's ``usage`` (all output tokens when there is no breakdown)."""
    by_modality = safe_attr(usage, "output_tokens_by_modality") or []
    if by_modality:
        return sum(int(safe_attr(m, "tokens", default=0) or 0) for m in by_modality
                   if str(safe_attr(m, "modality", default="")).lower() == "video")
    return int(safe_attr(usage, "total_output_tokens", default=0) or 0)


def _decode(text: str) -> bytes:
    """Base64 (standard, else URL-safe) to bytes."""
    text = text.strip()
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class OmniVideo:
    """``VideoProvider`` for Gemini Omni (``OMNI_MODEL``): one background interaction per clip, polled with
    the key that created it; resumable by interaction id."""

    name = "omni"
    paid = True
    aspects = OMNI_ASPECTS
    resumable = True
    reports_sending = True
    max_prompt_chars = None

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory | None = None,
        sleep: SleepFn | None = None,
        poll_interval_s: float = 10.0,
        max_attempts: int | None = None,
    ) -> None:
        self.settings = settings
        # per_loop: the Interactions resource keeps one httpx.AsyncClient per genai client, so a client cached
        # across jobs (each its own event loop) would reuse a connection of a closed loop: "Event loop is closed"
        # after the create was possibly sent (and billed). The poll, the download and a cancel run in the loop
        # of the create and so reuse its client; ``generate`` / ``resume`` hold a ``loop_lease``, so the last one
        # running in a job loop closes that loop's clients.
        self._caller = GeminiCaller(settings, timeout_s=120.0, client_factory=client_factory, sleep=sleep,
                                    max_attempts=max_attempts, scope="omni", per_loop=True)
        self._sleep = sleep or asyncio.sleep
        self._poll_interval_s = poll_interval_s

    @property
    def model(self) -> str:
        return self.settings.omni_model

    def billed_seconds(self) -> float:
        """Billed length of one clip for the pipeline's budget estimate (the actual charge follows the
        interaction's token usage)."""
        return ESTIMATED_SECONDS

    @staticmethod
    async def _ready() -> None:
        """Load the SDK, including the Interactions client, off the event loop."""
        await genai_types()
        try:
            await import_off_loop(_NEXTGEN_MODULE)
        except ImportError:  # another SDK layout: client.aio.interactions imports what it needs on first use
            pass

    def _request(self, model: str, prompt: str, aspect: str, reference_image: ImageInput | None) -> dict[str, Any]:
        text = f"{prompt.strip()}\n\n{EXCLUSIONS}"
        content: Any = text
        if reference_image is not None:
            content = [
                {"type": "image", "data": base64.b64encode(reference_image.data).decode("ascii"),
                 "mime_type": reference_image.mime or "image/png"},
                {"type": "text", "text": text},
            ]
        return {
            "model": model,
            "input": content,
            "background": True,
            "response_format": {"type": "video", "aspect_ratio": aspect if aspect in OMNI_ASPECTS else "16:9",
                                "resolution": RESOLUTION},
        }

    async def _with_retries(self, fn: Any, what: str) -> Any:
        try:
            return await call_with_retries(fn, classify=classify_gemini_error, max_attempts=5, base_delay=2.0,
                                           max_delay=30.0, sleep=self._sleep, label=f"omni {what}",
                                           service="video service (Gemini Omni)")
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to a redacted provider error
            raise gemini_error(exc, self.settings, what) from None

    def _usage(self, model: str, interaction: Any, *, status: str) -> Usage:
        """Billing record of one interaction: its video output tokens as 720p seconds, else the estimate."""
        usage = safe_attr(interaction, "usage")
        tokens = _video_tokens(usage)
        meta: dict[str, Any] = {"status": status, "estimated": tokens <= 0}
        if tokens > 0:
            meta["video_tokens"] = tokens
        return Usage(provider="omni", model=model, operation="video",
                     input_tokens=int(safe_attr(usage, "total_input_tokens", default=0) or 0),
                     output_tokens=int(safe_attr(usage, "total_output_tokens", default=0) or 0),
                     seconds=round(tokens / VIDEO_TOKENS_PER_SECOND, 3) if tokens > 0 else ESTIMATED_SECONDS,
                     units=1, meta=meta)

    @staticmethod
    def _estimate(model: str, status: str) -> Usage:
        return Usage(provider="omni", model=model, operation="video", seconds=ESTIMATED_SECONDS, units=1,
                     meta={"status": status, "estimated": True})

    async def _note_usage(self, checkpoint: OperationCheckpoint | None) -> None:
        """Tell the checkpoint that this job's charge was reported (best effort)."""
        if checkpoint is None:
            return
        try:
            await checkpoint.usage_recorded()
        except Exception:  # noqa: BLE001 - the original failure is what matters
            log.warning("omni: could not note the reported usage on the operation checkpoint", exc_info=True)

    async def _abandon(self, on_usage: UsageSink | None, model: str, checkpoint: OperationCheckpoint | None) -> None:
        """Report the likely charge of an accepted job we stop following; never raises."""
        try:
            await emit_usage(on_usage, self._estimate(model, "abandoned"))
        except Exception:  # noqa: BLE001 - best effort while stopping
            log.warning("omni: could not report usage of an abandoned video generation", exc_info=True)
            return
        await self._note_usage(checkpoint)

    async def _cancel(self, client: Any, ident: str) -> None:
        """Ask Google to stop a background job nobody will collect (best effort, never raises)."""
        try:
            await asyncio.wait_for(_sdk(interactions_of(client).cancel(ident)), timeout=CANCEL_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - the timeout is what gets reported
            log.info("omni: could not cancel an abandoned video job (%s)", type(exc).__name__)

    async def _poll(self, client: Any, interaction: Any, ident: str, deadline: float, timeout_s: int) -> Any:
        """Poll ``interaction`` until it leaves the running states; raises on deadline expiry (after asking
        Google to cancel the job) or exhausted polling retries."""
        while _status(interaction) in RUNNING:
            if not ident:
                raise make_error(ProviderError, "omni", "the video job has no id to follow", settings=self.settings)
            if time.monotonic() > deadline:
                await self._cancel(client, ident)
                raise make_error(ProviderError, "omni", f"video generation timed out after {int(timeout_s)}s",
                                 settings=self.settings, host=GEMINI_HOST)
            await self._sleep(self._poll_interval_s)
            interaction = await self._with_retries(lambda: _sdk(interactions_of(client).get(ident)),
                                                   "Omni video polling")
        return interaction

    def _failure(self, interaction: Any, status: str) -> ProviderError:
        """The error of an interaction that ended without a clip."""
        texts = [str(safe_attr(e, "message", default="") or safe_attr(e, "code", default="") or "")
                 for e in (safe_attr(interaction, "errors") or [])]
        texts.append(str(safe_attr(interaction, "output_text", default="") or ""))
        detail = "; ".join(t for t in texts if t)
        if _SAFETY.search(detail):
            return make_error(ContentBlocked, "omni", "video blocked by safety filters", settings=self.settings,
                              detail=detail)
        what = "finished without a video" if status == "completed" else f"video generation ended as {status or 'unknown'}"
        return make_error(ProviderError, "omni", what, settings=self.settings, host=GEMINI_HOST, detail=detail)

    async def _video_bytes(self, client: Any, video: Any) -> bytes:
        """The clip's bytes: inline base64 (checked against ``AI_MAX_VIDEO_BYTES`` before decoding), else
        downloaded from its ``uri``."""
        data = safe_attr(video, "data")
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
        if isinstance(data, str) and data.strip():
            if len(data) // 4 * 3 > self.settings.ai_max_video_bytes + 3:
                raise make_error(InvalidMediaOutput, "omni", f"video of about {len(data) // 4 * 3} bytes exceeds the "
                                 f"{self.settings.ai_max_video_bytes}-byte limit", settings=self.settings)
            try:
                return _decode(data)
            except (binascii.Error, ValueError):
                raise make_error(InvalidMediaOutput, "omni", "the video data is not valid base64",
                                 settings=self.settings) from None
        uri = safe_attr(video, "uri")
        if uri:
            downloaded = await self._with_retries(lambda: client.aio.files.download(file=str(uri)),
                                                  "Omni video download")
            return bytes(downloaded or b"")
        return b""

    async def _finish(self, client: Any, interaction: Any, model: str, aspect: str, on_usage: UsageSink | None, *,
                      usage_recorded: bool, checkpoint: OperationCheckpoint | None = None) -> VideoResult:
        """Check the finished interaction, report the clip, decode / download, validate and probe it."""
        model = str(safe_attr(interaction, "model", default="") or model)
        status = _status(interaction)
        video = safe_attr(interaction, "output_video")
        if status != "completed" or video is None:
            if not usage_recorded and _video_tokens(safe_attr(interaction, "usage")) > 0:
                await emit_usage(on_usage, self._usage(model, interaction, status="failed"))  # video was made
                await self._note_usage(checkpoint)
            raise self._failure(interaction, status)
        if not usage_recorded:
            await emit_usage(on_usage, self._usage(model, interaction, status="generated"))  # billed from here on
            await self._note_usage(checkpoint)  # a restart during the download / probe must not report it again
        data = await self._video_bytes(client, video)
        if not data:
            raise make_error(ProviderError, "omni", "the finished video job returned no data", settings=self.settings)
        if len(data) > self.settings.ai_max_video_bytes:
            raise make_error(InvalidMediaOutput, "omni", f"video of {len(data)} bytes exceeds the "
                             f"{self.settings.ai_max_video_bytes}-byte limit", settings=self.settings)
        if sniff_extension(data) != ".mp4":
            mime = str(safe_attr(video, "mime_type", default="") or "unknown")
            raise make_error(InvalidMediaOutput, "omni", f"the video is not an MP4 file ({mime})", settings=self.settings)
        info = await probe_media(data, settings=self.settings)
        if info.duration < MIN_VIDEO_SECONDS:
            raise make_error(InvalidMediaOutput, "omni", f"video is too short ({info.duration:.2f}s)",
                             settings=self.settings)
        return VideoResult(data=data, mime="video/mp4", duration=info.duration, width=info.width or 0,
                           height=info.height or 0, warnings=media_warnings(info.width, info.height, aspect))

    def _unavailable(self, exc: ProviderError, model: str) -> ProviderError:
        """A create refused with 404 / 410 means the model is not served: ``ProviderUnavailable``."""
        if exc.status in (404, 410) and not isinstance(exc, ProviderUnavailable):
            return make_error(ProviderUnavailable, "omni", f"the video model {model} is not available (OMNI_MODEL)",
                              settings=self.settings, status=exc.status, host=GEMINI_HOST, detail=str(exc))
        return exc

    async def generate(
        self,
        prompt: str,
        *,
        aspect: str = "16:9",
        reference_image: ImageInput | None = None,
        timeout_s: int = 420,
        on_usage: UsageSink | None = None,
        checkpoint: OperationCheckpoint | None = None,
    ) -> VideoResult:
        """Start a background interaction, poll it until done (or ``timeout_s``), decode and probe the MP4."""
        if not (prompt or "").strip():
            raise make_error(ProviderError, "omni", "empty prompt", settings=self.settings)
        async with self._caller.loop_lease():  # this job loop's clients, closed in it when its last clip is done
            return await self._generate(prompt, aspect=aspect, reference_image=reference_image, timeout_s=timeout_s,
                                        on_usage=on_usage, checkpoint=checkpoint)

    async def _generate(self, prompt: str, *, aspect: str, reference_image: ImageInput | None, timeout_s: int,
                        on_usage: UsageSink | None, checkpoint: OperationCheckpoint | None) -> VideoResult:
        model = self.settings.omni_model
        await self._ready()
        request = self._request(model, prompt, aspect, reference_image)
        deadline = time.monotonic() + max(10, int(timeout_s))
        from google.genai import errors as genai_errors  # loaded by _ready() above

        uncertain = False  # some create ended without an answer: it may have started (and billed) a job

        async def start(client: Any, key: str) -> tuple[Any, str]:
            nonlocal uncertain
            if uncertain:  # an earlier attempt may have created (and billed) the job: never send it twice
                raise make_error(PaidSubmissionUncertain, "omni", "the previous request to create this paid clip got no answer and may have been accepted and billed; not sending it again", settings=self.settings,
                                 host=GEMINI_HOST)
            resource = interactions_of(client)
            if checkpoint is not None and callable(getattr(checkpoint, "sending", None)):
                await checkpoint.sending()
            try:
                interaction = await _sdk(resource.create(**request))
            except genai_errors.APIError as exc:
                if getattr(exc, "code", None) in (408, 504):  # a gateway gave up waiting: the job may exist
                    uncertain = True
                elif checkpoint is not None and not uncertain and callable(getattr(checkpoint, "not_sent", None)):
                    await checkpoint.not_sent()  # Google answered with an error: no job was created
                raise
            except BaseException:  # timeout (wait_for), transport error, kill: no answer
                uncertain = True
                raise
            return interaction, key

        try:
            interaction, key = await self._caller.call(start, what="Omni video generation")
        except PaidSubmissionUncertain:
            raise
        except ProviderError as exc:
            if uncertain:  # the last attempt ended without an answer: possibly billed, needs a person
                raise make_error(PaidSubmissionUncertain, "omni", "the previous request to create this paid clip got no answer and may have been accepted and billed; not sending it again", settings=self.settings,
                                 status=exc.status, host=GEMINI_HOST, detail=str(exc)) from None
            raise self._unavailable(exc, model) from None
        client = await self._caller.client(key)
        ident = str(safe_attr(interaction, "id", default="") or "")
        try:
            if checkpoint is not None:  # durable before polling: a restart resumes instead of paying again
                await checkpoint.submitted(ident, fingerprint(key))
            interaction = await self._poll(client, interaction, ident, deadline, timeout_s)
        except ProviderError:
            # The accepted job may still finish (and be billed) after we stop waiting.
            await emit_usage(on_usage, self._estimate(model, "abandoned"))
            await self._note_usage(checkpoint)
            raise
        except asyncio.CancelledError:
            # the job was cancelled: report the likely charge, but never swallow the cancellation
            await self._abandon(on_usage, model, checkpoint)
            raise
        except Exception:  # e.g. the job lost its lease while saving the checkpoint
            await self._abandon(on_usage, model, checkpoint)
            raise
        return await self._finish(client, interaction, model, aspect, on_usage, usage_recorded=False,
                                  checkpoint=checkpoint)

    async def resume(
        self,
        operation: str,
        key_fingerprint: str,
        *,
        timeout_s: int = 420,
        on_usage: UsageSink | None = None,
        usage_recorded: bool = False,
        checkpoint: OperationCheckpoint | None = None,
    ) -> VideoResult:
        """Poll and collect the interaction ``operation`` started earlier (never submits a new one); a charge
        it reports is noted on ``checkpoint`` (``usage_recorded``)."""
        async with self._caller.loop_lease():
            return await self._resume(operation, key_fingerprint, timeout_s=timeout_s, on_usage=on_usage,
                                      usage_recorded=usage_recorded, checkpoint=checkpoint)

    async def _resume(self, operation: str, key_fingerprint: str, *, timeout_s: int, on_usage: UsageSink | None,
                      usage_recorded: bool, checkpoint: OperationCheckpoint | None) -> VideoResult:
        model = self.settings.omni_model
        key = next((k for k in self.settings.all_gemini_keys if fingerprint(k) == key_fingerprint), None)
        if not operation or key is None:
            raise make_error(OperationLost, "omni", "the video job started earlier cannot be followed: the API key "
                             "that started it is no longer configured", settings=self.settings)
        await self._ready()
        client = await self._caller.client(key)
        deadline = time.monotonic() + max(10, int(timeout_s))
        try:
            interaction = await self._with_retries(lambda: _sdk(interactions_of(client).get(operation)),
                                                   "Omni video polling")
            interaction = await self._poll(client, interaction, operation, deadline, timeout_s)
        except ProviderError as exc:
            if exc.status == 404:
                raise make_error(OperationLost, "omni", "the video job started earlier no longer exists",
                                 settings=self.settings, status=404, host=GEMINI_HOST) from None
            if not usage_recorded:
                await emit_usage(on_usage, self._estimate(model, "abandoned"))
                await self._note_usage(checkpoint)
            raise
        except asyncio.CancelledError:
            if not usage_recorded:
                await self._abandon(on_usage, model, checkpoint)
            raise
        return await self._finish(client, interaction, model, "", on_usage, usage_recorded=usage_recorded,
                                  checkpoint=checkpoint)
