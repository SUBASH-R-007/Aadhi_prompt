"""Provider interfaces (LLM, TTS, image, video, GIF).

Implementations live in ``aadhi/providers/{llm,tts,image,video,gif}/`` and are obtained via
``aadhi.providers.factory``. Rules:

* Every network call is async and non-blocking (sync SDK calls wrapped in ``asyncio.to_thread``).
* Every billable call reports a ``Usage`` through ``on_usage``. ``UsageSink`` callbacks MUST NOT
  block (the job context buffers them) and may raise ``BudgetExceeded`` to abort the job.
* Error messages never contain secrets: build them with ``Settings.redact`` and include only the
  HTTP status and a sanitised host.
* ``generate_json`` schemas are LLM-facing generation models (``aadhi.pipeline.gen_models``) —
  never ``Screenplay``/``SceneBase``. They must convert to provider JSON schema without
  ``oneOf``/``discriminator``/``prefixItems``/``additionalProperties: true``
  (``aadhi.providers.llm.schema.assert_llm_compatible`` checks this in tests).
* Every provider has a deterministic offline ``fake`` implementation.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


# --- Usage --------------------------------------------------------------------


@dataclass
class Usage:
    provider: str  # gemini | openai | anthropic | edge | elevenlabs | pollinations | veo | giphy | fake
    model: str
    operation: str  # llm | vision | tts | image | video | gif
    input_tokens: int = 0
    output_tokens: int = 0
    characters: int = 0
    seconds: float = 0.0
    units: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


UsageSink = Callable[[Usage], Any]


# --- Errors -------------------------------------------------------------------


class ProviderError(Exception):
    """Non-retryable provider failure (bad request, invalid output after retries...)."""

    def __init__(self, message: str, *, status: int | None = None, provider: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.provider = provider


class RateLimited(ProviderError):
    """429 / quota exhausted after retries and key rotation."""


class ContentBlocked(ProviderError):
    """Safety filter refused the request."""


class ProviderNotConfigured(ProviderError):
    """Missing API key / disabled feature."""


class ProviderUnavailable(ProviderError):
    """Account, payment or credential problem of a keyless or server-owned provider (HTTP 401/402/403,
    e.g. a paywalled free endpoint). Retrying soon cannot help: the media chain moves on to the next
    provider and puts this one in a long cooldown, and a scene that failed only because of it is not
    retried on every build. Personal (BYOK) key rejections keep their plain ``ProviderError`` status
    401/403 path (``aadhi.credentials.personal_key_rejection``); this class is a subclass, so that
    detection is unchanged either way."""


class InvalidMediaOutput(ProviderError):
    """The provider answered, but the media is unusable (not decodable, too small, too large, too
    short). Another provider may do better: the media chain may fall back."""


class OperationLost(ProviderError):
    """A checkpointed provider operation (e.g. a Veo job) cannot be found or polled again: it expired,
    or the API key that started it is no longer configured. Whether it was billed is unknown."""


# --- LLM ------------------------------------------------------------------------


@dataclass
class FileInput:
    """A file passed natively to a multimodal model (e.g. the uploaded PDF)."""

    data: bytes
    mime: str
    name: str = "document"


@dataclass
class ImageInput:
    data: bytes
    mime: str = "image/png"


# Extra semantic validation after pydantic validation: returns human-readable problems that are
# fed back to the model in the same re-ask loop (e.g. unknown concept ids, invalid template params).
SemanticValidator = Callable[[Any], list[str]]


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    async def generate_json(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[T],
        files: Sequence[FileInput] = (),
        images: Sequence[ImageInput] = (),
        temperature: float = 0.4,
        max_output_tokens: int | None = None,
        on_usage: UsageSink | None = None,
        validate: SemanticValidator | None = None,
        validation_retries: int = 2,
    ) -> T:
        """Structured generation.

        Native JSON-schema output mode, then ``schema.model_validate``, then ``validate(obj)``.
        On any validation problem the model is re-asked with the problems appended (up to
        ``validation_retries`` times) before raising ``ProviderError``.
        """
        ...

    async def generate_text(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        files: Sequence[FileInput] = (),
        images: Sequence[ImageInput] = (),
        temperature: float = 0.4,
        max_output_tokens: int | None = None,
        on_usage: UsageSink | None = None,
    ) -> str: ...


# --- TTS ------------------------------------------------------------------------


@dataclass
class WordTiming:
    text: str
    start: float  # seconds from the start of this clip
    end: float


@dataclass
class SpeechResult:
    audio: bytes
    mime: str  # audio/mpeg | audio/wav
    duration: float  # seconds (measured with ffprobe, not estimated)
    words: list[WordTiming]  # empty when the provider has no timings
    voice: str
    provider: str
    sample_rate: int | None = None


@runtime_checkable
class TTSProvider(Protocol):
    name: str
    supports_word_timings: bool

    def default_voice(self, language: str) -> str: ...

    def voices(self, language: str | None = None) -> list[dict[str, str]]:
        """[{id, label, language, gender}] for the UI."""
        ...

    async def synthesize(
        self,
        text: str,
        *,
        voice: str,
        language: str,
        rate: str = "+0%",
        on_usage: UsageSink | None = None,
    ) -> SpeechResult: ...


# --- Images / video / gifs ------------------------------------------------------


@dataclass
class ImageResult:
    data: bytes
    mime: str  # image/png | image/jpeg | image/webp
    width: int
    height: int
    # Soft quality findings (flat/blank picture, shape far from the requested aspect): the image is
    # kept, the pipeline shows them to the teacher as an issue. Empty for normal output.
    warnings: list[str] = field(default_factory=list)


# Optional class attributes of image/video adapters (read with ``getattr``, so adapters and test
# doubles without them keep working): ``paid: bool`` (bills per generation; the status panel shows
# it, and paid video jobs are never re-submitted automatically after a crash), ``aspects:
# frozenset[str]`` (aspect ratios served natively; others are clamped, so the media chain prefers a
# provider that supports the requested one), ``max_prompt_chars: int | None`` and ``resumable: bool``
# (a ``ResumableVideoProvider``).


@runtime_checkable
class ImageProvider(Protocol):
    name: str

    async def generate(
        self, prompt: str, *, aspect: str = "16:9", on_usage: UsageSink | None = None
    ) -> ImageResult: ...


@dataclass
class VideoResult:
    data: bytes
    mime: str
    duration: float
    width: int
    height: int
    warnings: list[str] = field(default_factory=list)  # soft quality findings (see ImageResult)


@runtime_checkable
class VideoProvider(Protocol):
    name: str

    async def generate(
        self,
        prompt: str,
        *,
        aspect: str = "16:9",
        reference_image: ImageInput | None = None,
        timeout_s: int = 420,
        on_usage: UsageSink | None = None,
    ) -> VideoResult: ...


class OperationCheckpoint(Protocol):
    """Durable record of a paid long-running provider job (``aadhi.providers.operations``).

    ``submitted`` is awaited the moment the provider accepted the job, before polling starts, so a
    restarted worker polls the same job instead of paying for a new one. ``usage_recorded`` notes that
    the job's (estimated) charge was already reported, so a resume does not report it twice. Both are
    best effort except for job-level conditions (a lost lease), which propagate.

    Optional hooks for an adapter with ``reports_sending = True``: ``sending()`` right before each request
    that may create the job and ``not_sent()`` when the provider answered it with an error (no job was
    created), so only a request without an answer (a timeout, a kill) leaves the record ambiguous.
    Adapters call them only when the checkpoint has them.
    """

    async def submitted(self, operation: str, key_fingerprint: str) -> None: ...

    async def usage_recorded(self) -> None: ...


@runtime_checkable
class ResumableVideoProvider(VideoProvider, Protocol):
    """A video provider whose jobs can be checkpointed and resumed (``resumable = True``)."""

    async def generate(
        self,
        prompt: str,
        *,
        aspect: str = "16:9",
        reference_image: ImageInput | None = None,
        timeout_s: int = 420,
        on_usage: UsageSink | None = None,
        checkpoint: OperationCheckpoint | None = None,
    ) -> VideoResult: ...

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
        """Poll and download a job started earlier; never submits. ``OperationLost`` when the job (or
        the key that started it) is gone. ``checkpoint.usage_recorded`` is awaited once the charge is
        reported, so a restart during the download does not report it again."""
        ...


@dataclass
class GifResult:
    url: str  # provider URL (hotlinked with attribution; never re-hosted)
    width: int
    height: int
    title: str = ""
    attribution: str = "Powered by GIPHY"
    link_url: str = ""


@runtime_checkable
class GifProvider(Protocol):
    name: str

    async def search(self, query: str, *, rating: str = "g", on_usage: UsageSink | None = None) -> GifResult: ...
