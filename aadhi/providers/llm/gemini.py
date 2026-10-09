"""Google Gemini LLM adapter (google-genai, async API, native JSON-schema output).

Files and images are sent inline while the whole request stays under Gemini's 20 MB request cap
(inline bytes travel base64 encoded, +33 %); otherwise the largest parts go through the Files API
(uploaded once per key and content hash, reused for about 46 hours).

Stalled requests: unlike the OpenAI and Claude adapters this one does not stream, and each attempt
waits up to ``LLM_TIMEOUT_SECONDS``. Gemini 2.5 models think before their first streamed chunk
(minutes for a long plan), and the SDK offers no reliable earlier sign of life: thought summaries
(``include_thoughts``) are returned "only if ... thoughts are available", and the HTTP response
headers are not exposed by ``generate_content_stream``. A short first-response limit could
therefore cut off healthy calls, and streaming would not change the answer or its cost, so the
request mode is unchanged; the job log still shows every retry and "still waiting" notices
(``call_with_retries``).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import threading
import time
from collections.abc import Sequence
from typing import Any

from ...config import Settings
from .._common import fingerprint, make_error
from .._retry import SleepFn
from ..base import FileInput, ImageInput, ProviderError, SemanticValidator, T, UsageSink
from ..gemini_common import (
    ClientFactory,
    GeminiCaller,
    block_reason,
    genai_types,
    is_recitation,
    is_truncated,
    response_text,
    usage_from_response,
)
from .schema import to_provider_schema
from .structured import Completion, Turn, recitation_error, recitation_followups, run_structured, settle

#: Encoded-size budget for one request (API cap 20 MB; about 1 MB headroom for JSON framing/config).
INLINE_LIMIT_BYTES = 19_000_000
UPLOAD_TTL_S = 46 * 3600  # uploaded files expire after 48 h
UPLOAD_ACTIVE_TIMEOUT_S = 300.0
TEXT_RECITATION_RETRIES = 1  # generate_text: paraphrase re-asks after a recitation stop


def base64_size(n: int) -> int:
    """Size of ``n`` bytes after base64 encoding (how inline data travels in the JSON body)."""
    return 4 * ((max(0, n) + 2) // 3)


def plan_uploads(sizes: Sequence[int], fixed_bytes: int, budget: int | None = None) -> set[int]:
    """Indices of binary parts to send through the Files API so the request fits ``budget``.

    ``sizes`` are the raw sizes of the files/images (an inline part costs ``base64_size``);
    ``fixed_bytes`` is everything that is always inline (prompt, system, follow-ups, schema). The
    largest parts move to the Files API first until the rest fits.
    """
    limit = INLINE_LIMIT_BYTES if budget is None else budget
    total = fixed_bytes + sum(base64_size(n) for n in sizes)
    upload: set[int] = set()
    for i in sorted(range(len(sizes)), key=lambda j: (-sizes[j], j)):
        if total <= limit:
            break
        upload.add(i)
        total -= base64_size(sizes[i])
    return upload


class GeminiLLM:
    """``LLMProvider`` backed by ``client.aio.models.generate_content``."""

    name = "gemini"

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory | None = None,
        sleep: SleepFn | None = None,
        max_attempts: int | None = None,
        poll_interval_s: float = 2.0,
    ) -> None:
        self.settings = settings
        self._caller = GeminiCaller(
            settings,
            timeout_s=settings.llm_timeout_seconds,
            client_factory=client_factory,
            sleep=sleep,
            max_attempts=max_attempts,
            scope="llm",
        )
        self._poll_interval_s = poll_interval_s
        self._uploads: dict[tuple[str, str], tuple[str, str, float]] = {}
        self._uploads_lock = threading.Lock()
        # An asyncio.Lock binds to the loop that first waits on it: one set of locks per loop. The job
        # worker runs every job in a fresh loop, so the sets of closed loops are dropped on each lookup.
        self._upload_locks: dict[asyncio.AbstractEventLoop, dict[tuple[str, str], asyncio.Lock]] = {}

    # --- files -----------------------------------------------------------------------------
    def _lock_for(self, ident: tuple[str, str]) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with self._uploads_lock:
            for closed in [lp for lp in self._upload_locks if lp.is_closed()]:
                del self._upload_locks[closed]
            locks = self._upload_locks.setdefault(loop, {})
            return locks.setdefault(ident, asyncio.Lock())

    async def _uploaded_uri(self, client: Any, key: str, f: FileInput) -> tuple[str, str]:
        """Upload ``f`` with the Files API once per key and wait until it is ACTIVE."""
        types = await genai_types()
        digest = await asyncio.to_thread(lambda: hashlib.sha256(f.data).hexdigest())  # large file: hash off-loop
        ident = (fingerprint(key), digest)
        async with self._lock_for(ident):
            with self._uploads_lock:
                cached = self._uploads.get(ident)
            if cached and cached[2] > time.monotonic():
                return cached[0], cached[1]
            uploaded = await client.aio.files.upload(
                file=io.BytesIO(f.data),
                config=types.UploadFileConfig(mime_type=f.mime, display_name=(f.name or "document")[:120]),
            )
            deadline = time.monotonic() + UPLOAD_ACTIVE_TIMEOUT_S
            while str(getattr(uploaded.state, "name", uploaded.state) or "").upper() == "PROCESSING":
                if time.monotonic() > deadline:
                    raise make_error(ProviderError, "gemini", "uploaded file did not become ready in time",
                                     settings=self.settings)
                await asyncio.sleep(self._poll_interval_s)
                uploaded = await client.aio.files.get(name=uploaded.name)
            if str(getattr(uploaded.state, "name", uploaded.state) or "").upper() == "FAILED":
                raise make_error(ProviderError, "gemini", "file processing failed", settings=self.settings)
            uri, mime = str(uploaded.uri), str(uploaded.mime_type or f.mime)
            with self._uploads_lock:
                self._uploads[ident] = (uri, mime, time.monotonic() + UPLOAD_TTL_S)
            return uri, mime

    async def _user_parts(self, client: Any, key: str, prompt: str, files: Sequence[FileInput],
                          images: Sequence[ImageInput], *, fixed_bytes: int = 0) -> list[Any]:
        """User parts in order (files, images, prompt). Parts that would push the encoded request over
        ``INLINE_LIMIT_BYTES`` are uploaded through the Files API, largest first."""
        types = await genai_types()
        binaries = [*files, *(FileInput(data=im.data, mime=im.mime, name="image") for im in images)]
        upload = plan_uploads([len(b.data) for b in binaries], fixed_bytes + len(prompt.encode("utf-8")))
        parts: list[Any] = []
        for i, item in enumerate(binaries):
            if i in upload:
                uri, mime = await self._uploaded_uri(client, key, item)
                parts.append(types.Part.from_uri(file_uri=uri, mime_type=mime))
            else:
                parts.append(types.Part.from_bytes(data=item.data, mime_type=item.mime))
        parts.append(types.Part.from_text(text=prompt))
        return parts

    # --- core request -------------------------------------------------------------------------
    async def _complete(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        files: Sequence[FileInput],
        images: Sequence[ImageInput],
        followups: list[Turn],
        temperature: float,
        max_output_tokens: int | None,
        json_schema: dict[str, Any] | None,
        what: str,
    ) -> Completion:
        types = await genai_types()

        config_kwargs: dict[str, Any] = {"temperature": temperature}
        if system:
            config_kwargs["system_instruction"] = system
        if max_output_tokens:
            config_kwargs["max_output_tokens"] = int(max_output_tokens)
        if json_schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_json_schema"] = json_schema
        config = types.GenerateContentConfig(**config_kwargs)
        fixed_bytes = len(system.encode("utf-8")) + sum(len(t.text.encode("utf-8")) for t in followups)
        if json_schema is not None:
            fixed_bytes += len(json.dumps(json_schema, ensure_ascii=False).encode("utf-8"))

        async def request(client: Any, key: str) -> Any:
            parts = await self._user_parts(client, key, prompt, files, images, fixed_bytes=fixed_bytes)
            contents = [types.Content(role="user", parts=parts)]
            for turn in followups:
                contents.append(types.Content(role=turn.role, parts=[types.Part.from_text(text=turn.text)]))
            return await client.aio.models.generate_content(model=model, contents=contents, config=config)

        response = await self._caller.call(request, what=what)
        usage = usage_from_response(response, model=model, operation="vision" if images else "llm")
        return Completion(text=response_text(response), usage=usage, truncated=is_truncated(response),
                          blocked=block_reason(response), recitation=is_recitation(response))

    # --- LLMProvider ------------------------------------------------------------------------------
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
        """Structured generation with ``response_json_schema`` + validation/re-ask loop."""
        json_schema = to_provider_schema(schema, "gemini")

        async def complete(followups: list[Turn]) -> Completion:
            return await self._complete(
                model=model, system=system, prompt=prompt, files=files, images=images, followups=followups,
                temperature=temperature, max_output_tokens=max_output_tokens, json_schema=json_schema,
                what=f"{schema.__name__} generation",
            )

        return await run_structured(
            complete=complete,
            schema=schema,
            validate=validate,
            validation_retries=validation_retries,
            on_usage=on_usage,
            provider=self.name,
            settings=self.settings,
        )

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
    ) -> str:
        """Free-text generation (a recitation stop is re-asked with a paraphrase instruction)."""
        followups: list[Turn] = []
        for attempt in range(TEXT_RECITATION_RETRIES + 1):
            completion = await self._complete(
                model=model, system=system, prompt=prompt, files=files, images=images, followups=followups,
                temperature=temperature, max_output_tokens=max_output_tokens, json_schema=None,
                what="text generation",
            )
            await settle(completion, on_usage=on_usage, provider=self.name, what="text generation",
                         settings=self.settings)
            if not completion.recitation:
                break
            if attempt == TEXT_RECITATION_RETRIES:
                raise recitation_error(self.name, "text generation", self.settings)
            followups = recitation_followups(completion)
        if not completion.text.strip():
            raise make_error(ProviderError, self.name, "text generation returned no text", settings=self.settings)
        return completion.text
