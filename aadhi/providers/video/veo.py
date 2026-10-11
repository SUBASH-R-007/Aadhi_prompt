"""Google Veo B-roll generation (``generate_videos`` long-running operation, async polling).

Request per model generation (Veo 3.1 Fast is the default ``VEO_MODEL``):

* Aspect ratio ``16:9`` (default) or ``9:16``; resolution is not sent (720p, the default; 1080p and 4K
  would require 8-second clips and cost more).
* Clip length (``VEO_DURATION_SECONDS``): Veo 3.x makes 4, 6 or 8 seconds, so other values round up to
  the next allowed length (more than 8 = 8), and 8 is used with reference images. Veo 2 took 5-8 as given.
* A ``reference_image`` is an asset reference image on Veo 3.1 / 3.1 Fast and the first frame
  (image-to-video) on Veo 3.1 Lite (which takes no reference images) and older models.
* Veo 3.x always generates an audio track (the SDK refuses ``generate_audio`` on the Gemini API); the
  player and the MP4 render keep B-roll muted, so only the picture is used.
* ``person_generation`` is not sent: the allowed value differs by mode and region (``allow_all`` for
  text-to-video, ``allow_adult`` from an image or in the EU, UK, CH and MENA), so the API's default applies.

Veo bills per generated second as soon as the operation produces a video, whether or not this
process manages to download or probe it. Usage is therefore reported the moment the operation
reports a generated video (before download/ffprobe), and an estimated ``Usage`` with
``meta.status == "abandoned"`` is reported when the operation is given up (local deadline or
polling failure) after it was accepted, so budget checks never under-count retried jobs.

Checkpoints and resume: ``generate(checkpoint=...)`` awaits ``checkpoint.submitted(operation name, key
fingerprint)`` the moment Veo accepted the job, before polling, so a worker that restarts can call
``resume`` to poll that same operation (with the key that created it, found by fingerprint; the key
itself is never stored) instead of paying for a new clip. ``resume`` never submits; an operation that
cannot be found again (expired, or its key is no longer configured) raises ``OperationLost``. When the
abandoned estimate was already reported for the job (``checkpoint.usage_recorded``), the resume does not
report the clip again. Every reported charge (generated or abandoned, by ``generate`` or ``resume``) is noted
on the checkpoint right away, before the download and probe, so an interruption there never bills it twice.

Sends (``reports_sending``): ``checkpoint.sending()`` is awaited right before each ``generate_videos``
request and ``checkpoint.not_sent()`` when Veo answered it with an error (429, 5xx: no job was created), so
a restart during a retry wait submits normally. Only a request without an answer (a timeout, a transport
error, a kill) leaves the record ``submitting`` (ambiguous), and stays so for the later retries of the call.

A model Google no longer serves (``generate_videos`` answered 404 / 410, e.g. after the Veo 3.1 previews
shut down) raises ``ProviderUnavailable``: the media chain moves on to the next provider (``omni``, Gemini
Omni, by default: ``Settings._video_order``) and tries Veo after the others for
``MEDIA_PROVIDER_AUTH_COOLDOWN_SECONDS``. No job was created, so nothing is billed.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from types import ModuleType
from typing import Any

from ...config import Settings
from .._common import emit_usage, fingerprint, make_error, safe_attr
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
from ..gemini_common import (
    GEMINI_HOST,
    ClientFactory,
    GeminiCaller,
    classify_gemini_error,
    gemini_error,
    genai_types,
)
from ..image.verify import media_warnings
from ..media import probe_media

log = logging.getLogger(__name__)

VEO_ASPECTS = frozenset({"16:9", "9:16"})
NEGATIVE_PROMPT = "text, captions, subtitles, watermark, logo, cartoon mascot, distorted faces"
DEFAULT_VIDEO_SECONDS = 8.0  # Veo's default clip length (billing estimate when no duration is requested)
MIN_VIDEO_SECONDS = 0.5  # shorter output is not a usable clip
VEO3_DURATIONS = (4, 6, 8)  # clip lengths Veo 3.x accepts (seconds)
_REFERENCE_MODELS = re.compile(r"veo-3\.1(?!-lite)", re.IGNORECASE)  # asset reference images (not Lite)
_VEO3_MODELS = re.compile(r"veo-3\.", re.IGNORECASE)
_SAFETY = re.compile(r"(?i)(safety|responsible ai|rai|filtered|blocked|prohibited)")


def clip_seconds(model: str, requested: int | None, *, reference: bool = False) -> int | None:
    """``duration_seconds`` to request from ``model`` for ``VEO_DURATION_SECONDS`` = ``requested``.

    ``None`` = not sent (the model's default, 8 s). Veo 3.x accepts only 4, 6 or 8 seconds (8 with asset
    reference images), so another value rounds up to the next allowed length; older models get the
    value unchanged.
    """
    if not requested:
        return None
    if not _VEO3_MODELS.search(model or ""):
        return int(requested)
    if reference and _REFERENCE_MODELS.search(model):
        return VEO3_DURATIONS[-1]
    return next((d for d in VEO3_DURATIONS if d >= requested), VEO3_DURATIONS[-1])


class VeoVideo:
    """``VideoProvider`` for Veo models.

    A ``reference_image`` becomes the first frame (image-to-video) or, on Veo 3.1 models, an asset
    reference image. The operation is polled with the key that created it.
    """

    name = "veo"
    paid = True
    aspects = VEO_ASPECTS
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
        self._caller = GeminiCaller(settings, timeout_s=120.0, client_factory=client_factory, sleep=sleep,
                                    max_attempts=max_attempts, scope="video")
        self._sleep = sleep or asyncio.sleep
        self._poll_interval_s = poll_interval_s

    def _request_parts(self, types: ModuleType, prompt: str, aspect: str,
                       reference_image: ImageInput | None) -> tuple[Any, Any]:
        model = self.settings.veo_model
        config = types.GenerateVideosConfig(
            aspect_ratio=aspect if aspect in VEO_ASPECTS else "16:9",
            number_of_videos=1,
            negative_prompt=NEGATIVE_PROMPT,
        )
        image = None
        as_reference = reference_image is not None and bool(_REFERENCE_MODELS.search(model))
        if reference_image is not None:
            ref = types.Image(image_bytes=reference_image.data, mime_type=reference_image.mime)
            if as_reference:
                config.reference_images = [types.VideoGenerationReferenceImage(image=ref, reference_type="ASSET")]
            else:
                image = ref
        # VEO_DURATION_SECONDS; unset = the model's default length
        seconds = clip_seconds(model, self.settings.veo_duration_seconds, reference=as_reference)
        if seconds:
            config.duration_seconds = seconds
        return types.GenerateVideosSource(prompt=prompt, image=image), config

    async def _with_retries(self, fn: Any, what: str) -> Any:
        try:
            return await call_with_retries(fn, classify=classify_gemini_error, max_attempts=5, base_delay=2.0,
                                           max_delay=30.0, sleep=self._sleep, label=f"veo {what}",
                                           service="video service (Veo)")
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to a redacted provider error
            raise gemini_error(exc, self.settings, what) from None

    def _check_operation(self, op: Any) -> Any:
        error = safe_attr(op, "error")
        if error:
            message = str(error.get("message", "") if isinstance(error, dict) else error)
            cls = ContentBlocked if _SAFETY.search(message) else ProviderError
            raise make_error(cls, "veo", "video generation failed", settings=self.settings, host=GEMINI_HOST,
                             detail=message)
        response = safe_attr(op, "response") or safe_attr(op, "result")
        videos = safe_attr(response, "generated_videos") or []
        if not videos:
            reasons = safe_attr(response, "rai_media_filtered_reasons") or []
            if safe_attr(response, "rai_media_filtered_count", default=0) or reasons:
                raise make_error(ContentBlocked, "veo", "video blocked by safety filters", settings=self.settings,
                                 detail="; ".join(str(r) for r in reasons))
            raise make_error(ProviderError, "veo", "operation finished without a video", settings=self.settings)
        return safe_attr(videos[0], "video")

    @property
    def model(self) -> str:
        return self.settings.veo_model

    def billed_seconds(self) -> float:
        """Billed length of one clip (no reference image): what ``VEO_DURATION_SECONDS`` becomes for the
        configured model, else Veo's default. The pipeline's budget estimate before a clip uses it."""
        seconds = clip_seconds(self.settings.veo_model, self.settings.veo_duration_seconds)
        return float(seconds or DEFAULT_VIDEO_SECONDS)

    def _usage(self, model: str, config: Any, *, status: str) -> Usage:
        """Billing record for one clip: the requested duration (or Veo's default) in seconds."""
        requested = safe_attr(config, "duration_seconds")
        seconds = float(requested) if isinstance(requested, (int, float)) and requested > 0 else DEFAULT_VIDEO_SECONDS
        return Usage(provider="veo", model=model, operation="video", seconds=seconds, units=1,
                     meta={"status": status, "estimated": True})

    async def _poll(self, client: Any, op: Any, deadline: float, timeout_s: int) -> Any:
        """Poll ``op`` until done; raises on deadline expiry or exhausted polling retries."""
        while not safe_attr(op, "done", default=False):
            if time.monotonic() > deadline:
                raise make_error(ProviderError, "veo", f"video generation timed out after {int(timeout_s)}s",
                                 settings=self.settings, host=GEMINI_HOST)
            await self._sleep(self._poll_interval_s)
            current = op
            op = await self._with_retries(lambda current=current: client.aio.operations.get(current), "video polling")
        return op

    async def _note_usage(self, checkpoint: OperationCheckpoint | None) -> None:
        """Tell the checkpoint that this job's charge was reported (best effort)."""
        if checkpoint is None:
            return
        try:
            await checkpoint.usage_recorded()
        except Exception:  # noqa: BLE001 - the original failure is what matters
            log.warning("veo: could not note the reported usage on the operation checkpoint", exc_info=True)

    async def _abandon(self, on_usage: UsageSink | None, model: str, config: Any,
                       checkpoint: OperationCheckpoint | None) -> None:
        """Report the likely charge of an accepted job we stop following; never raises."""
        try:
            await emit_usage(on_usage, self._usage(model, config, status="abandoned"))
        except Exception:  # noqa: BLE001 - best effort while stopping
            log.warning("veo: could not report usage of an abandoned video generation", exc_info=True)
            return
        await self._note_usage(checkpoint)

    async def _finish(self, client: Any, op: Any, model: str, config: Any, aspect: str,
                      on_usage: UsageSink | None, *, usage_recorded: bool,
                      checkpoint: OperationCheckpoint | None = None) -> VideoResult:
        """Check the finished operation, report the clip, download, validate and probe it."""
        video = self._check_operation(op)
        if not usage_recorded:
            await emit_usage(on_usage, self._usage(model, config, status="generated"))  # billed from here on
            await self._note_usage(checkpoint)  # a restart during the download / probe must not report it again
        data = safe_attr(video, "video_bytes")
        if not data:
            downloaded = await self._with_retries(lambda: client.aio.files.download(file=video), "video download")
            data = downloaded or safe_attr(video, "video_bytes")
        if not data:
            raise make_error(ProviderError, "veo", "video download returned no data", settings=self.settings)
        data = bytes(data)
        if len(data) > self.settings.ai_max_video_bytes:
            raise make_error(InvalidMediaOutput, "veo", f"video of {len(data)} bytes exceeds the "
                             f"{self.settings.ai_max_video_bytes}-byte limit", settings=self.settings)
        info = await probe_media(data, settings=self.settings)
        if info.duration < MIN_VIDEO_SECONDS:
            raise make_error(InvalidMediaOutput, "veo", f"video is too short ({info.duration:.2f}s)",
                             settings=self.settings)
        return VideoResult(data=data, mime="video/mp4", duration=info.duration, width=info.width or 0,
                           height=info.height or 0, warnings=media_warnings(info.width, info.height, aspect))

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
        """Start a Veo operation, poll until done (or ``timeout_s``), download and probe the MP4."""
        if not (prompt or "").strip():
            raise make_error(ProviderError, "veo", "empty prompt", settings=self.settings)
        model = self.settings.veo_model
        types = await genai_types()
        source, config = self._request_parts(types, prompt, aspect, reference_image)
        deadline = time.monotonic() + max(10, int(timeout_s))
        from google.genai import errors as genai_errors  # loaded by genai_types() above

        uncertain = False  # some request ended without an answer: it may have created (and billed) a job

        async def start(client: Any, key: str) -> tuple[Any, str]:
            nonlocal uncertain
            if uncertain:  # an earlier attempt may have created (and billed) the job: never send it twice
                raise make_error(PaidSubmissionUncertain, "veo", "the previous request to create this paid clip got no answer and may have been accepted and billed; not sending it again", settings=self.settings,
                                 host=GEMINI_HOST)
            if checkpoint is not None and callable(getattr(checkpoint, "sending", None)):
                await checkpoint.sending()
            try:
                op = await client.aio.models.generate_videos(model=model, source=source, config=config)
            except genai_errors.APIError as exc:
                if getattr(exc, "code", None) in (408, 504):  # a gateway gave up waiting: Veo may have accepted
                    uncertain = True
                elif checkpoint is not None and not uncertain and callable(getattr(checkpoint, "not_sent", None)):
                    await checkpoint.not_sent()  # Veo answered with an error: no job was created
                raise
            except BaseException:  # timeout (wait_for), transport error, kill: no answer
                uncertain = True
                raise
            return op, key

        try:
            op, key = await self._caller.call(start, what="video generation")
        except PaidSubmissionUncertain:
            raise
        except ProviderError as exc:
            if uncertain:  # the last attempt ended without an answer: possibly billed, needs a person
                raise make_error(PaidSubmissionUncertain, "veo", "the previous request to create this paid clip got no answer and may have been accepted and billed; not sending it again", settings=self.settings,
                                 status=exc.status, host=GEMINI_HOST, detail=str(exc)) from None
            if exc.status in (404, 410) and not isinstance(exc, ProviderUnavailable):  # the model is not served
                raise make_error(ProviderUnavailable, "veo", f"the video model {model} is not available (shut down "
                                 "or renamed; VEO_MODEL)", settings=self.settings, status=exc.status, host=GEMINI_HOST,
                                 detail=str(exc)) from None
            raise
        client = await self._caller.client(key)
        try:
            if checkpoint is not None:  # durable before polling: a restart resumes instead of paying again
                await checkpoint.submitted(str(safe_attr(op, "name", default="") or ""), fingerprint(key))
            op = await self._poll(client, op, deadline, timeout_s)
        except ProviderError:
            # The accepted operation may still finish (and be billed) after we stop waiting.
            await emit_usage(on_usage, self._usage(model, config, status="abandoned"))
            await self._note_usage(checkpoint)
            raise
        except asyncio.CancelledError:
            # the job was cancelled: report the likely charge, but never swallow the cancellation
            await self._abandon(on_usage, model, config, checkpoint)
            raise
        except Exception:  # e.g. the job lost its lease while saving the checkpoint
            await self._abandon(on_usage, model, config, checkpoint)
            raise
        return await self._finish(client, op, model, config, aspect, on_usage, usage_recorded=False,
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
        """Poll and download the operation ``operation`` started earlier (never submits a new one); a charge
        it reports is noted on ``checkpoint`` (``usage_recorded``)."""
        model = self.settings.veo_model
        key = next((k for k in self.settings.all_gemini_keys if fingerprint(k) == key_fingerprint), None)
        if not operation or key is None:
            raise make_error(OperationLost, "veo", "the video job started earlier cannot be followed: the API key "
                             "that started it is no longer configured", settings=self.settings)
        types = await genai_types()
        config = types.GenerateVideosConfig()  # billing estimate only (the pipeline sends no reference images)
        seconds = clip_seconds(model, self.settings.veo_duration_seconds)
        if seconds:
            config.duration_seconds = seconds
        client = await self._caller.client(key)
        deadline = time.monotonic() + max(10, int(timeout_s))
        pending: Any = types.GenerateVideosOperation(name=operation)
        try:
            op = await self._with_retries(lambda: client.aio.operations.get(pending), "video polling")
            op = await self._poll(client, op, deadline, timeout_s)
        except ProviderError as exc:
            if exc.status == 404:
                raise make_error(OperationLost, "veo", "the video job started earlier no longer exists",
                                 settings=self.settings, status=404, host=GEMINI_HOST) from None
            if not usage_recorded:
                await emit_usage(on_usage, self._usage(model, config, status="abandoned"))
                await self._note_usage(checkpoint)
            raise
        except asyncio.CancelledError:
            if not usage_recorded:
                await self._abandon(on_usage, model, config, checkpoint)
            raise
        return await self._finish(client, op, model, config, "", on_usage, usage_recorded=usage_recorded,
                                  checkpoint=checkpoint)
