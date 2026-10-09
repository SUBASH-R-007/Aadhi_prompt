"""OpenAI LLM adapter (openai 3.x ``AsyncOpenAI``; Responses API with a chat-completions fallback).

Every request streams (``stream=True``) so that a stalled request is noticed early instead of after
``LLM_TIMEOUT_SECONDS``: the Responses API sends ``response.created`` within seconds, so no first
event within ``LLM_FIRST_RESPONSE_TIMEOUT_SECONDS`` (plus an upload allowance for a large
document) or a silence longer than ``LLM_IDLE_TIMEOUT_SECONDS`` raises ``StreamStalled`` and the
attempt is retried (``_stream.StallGuard``). While a reasoning model reports an open reasoning
item it may think silently: only the per-attempt cap applies then. The answer is read from the
final event (``response.completed`` / ``response.incomplete``) exactly like the non-streamed
response before: text, usage, the incomplete reason and refusals. The SDK's ``responses.stream``
helper is not used because its ``get_final_response`` rejects an incomplete response (cut at
``max_output_tokens`` or by the content filter), which the structured-output loop must see; the
chat-completions fallback accumulates its chunks itself for the same reason.

Some models may only be streamed by a verified organization (o3 and the gpt-5 family on an
unverified key: HTTP 400 "Your organization must be verified to stream this model", ``param`` =
``stream``). Such a refusal is not an error: the same request is sent again without streaming,
within the same attempt, and the model is remembered (per provider instance, i.e. per key) so
later calls skip the refused stream. Those calls get no stall detection: only the per-attempt cap
(``LLM_TIMEOUT_SECONDS``) and the "still waiting" notices apply.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from collections.abc import Sequence
from typing import Any

from ...config import Settings
from .._common import make_error, safe_attr
from .._notify import hold_waiting, release_waiting
from .._retry import SleepFn
from .._stream import StallGuard, first_response_limit, payload_size
from ..base import FileInput, ImageInput, ProviderError, SemanticValidator, T, Usage, UsageSink
from ..openai_common import ClientFactory, OpenAICaller, OpenAIStreamError
from .schema import schema_name, to_provider_schema
from .structured import Completion, Turn, run_structured, settle

log = logging.getLogger(__name__)

_REASONING_MODEL = re.compile(r"^(o\d|gpt-5)", re.IGNORECASE)
_TEXT_MIMES = ("text/", "application/json", "application/xml")
INLINE_FILE_LIMIT = 20 * 1024 * 1024
DEFAULT_MODEL_PRO = "gpt-4.1"
DEFAULT_MODEL_FAST = "gpt-4.1-mini"
#: Final events of a streamed Responses API call; both carry the whole response.
_FINAL_EVENTS = ("response.completed", "response.incomplete")


def resolve_model(model: str) -> str:
    """Map Gemini model names (the settings defaults) onto OpenAI defaults."""
    if model.lower().startswith("gemini"):
        mapped = DEFAULT_MODEL_PRO if "pro" in model.lower() else DEFAULT_MODEL_FAST
        log.warning("openai provider got Gemini model %s; using %s (set LLM_MODEL_* for OpenAI)", model, mapped)
        return mapped
    return model


def _data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


async def _data_url_async(mime: str, data: bytes) -> str:
    """Base64 data URL; large payloads are encoded in a worker thread (CPU-bound)."""
    if len(data) > 1_000_000:
        return await asyncio.to_thread(_data_url, mime, data)
    return _data_url(mime, data)


def _stream_refused(exc: BaseException) -> bool:
    """HTTP 400 refusing ``stream=True`` for this model and key (e.g. "Your organization must be
    verified to stream this model", or a model that cannot stream), not the request itself."""
    import openai  # loaded off-loop by OpenAICaller.call

    if not isinstance(exc, openai.BadRequestError):
        return False
    message = str(getattr(exc, "message", "") or "").lower()
    return getattr(exc, "param", None) in ("stream", "stream_options") or "verified to stream" in message


def _output_text(output: list[Any]) -> str:
    """``Response.output_text`` of an output list (the SDK property, for a rebuilt output)."""
    return "".join(str(getattr(c, "text", "") or "") for item in output if getattr(item, "type", "") == "message"
                   for c in getattr(item, "content", []) or [] if getattr(c, "type", "") == "output_text")


class OpenAILLM:
    """``LLMProvider`` backed by ``client.responses.create`` (``text.format`` = ``json_schema``)."""

    name = "openai"

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory | None = None,
        sleep: SleepFn | None = None,
        max_attempts: int = 4,
    ) -> None:
        self.settings = settings
        self._caller = OpenAICaller(
            settings, timeout_s=settings.llm_timeout_seconds, client_factory=client_factory, sleep=sleep,
            max_attempts=max_attempts,
        )
        self._file_ids: dict[str, str] = {}
        self._no_stream: set[str] = set()  # models whose streaming the API refused for this key

    # --- request building --------------------------------------------------------------------
    async def _file_part(self, client: Any, f: FileInput, *, chat: bool) -> dict[str, Any]:
        name = f.name or "document"
        if f.mime.startswith(_TEXT_MIMES) or f.mime in ("text/markdown",):
            text = f"[{name}]\n" + f.data.decode("utf-8", "replace")
            return {"type": "text", "text": text} if chat else {"type": "input_text", "text": text}
        if f.mime != "application/pdf":
            raise make_error(ProviderError, self.name, f"unsupported file type {f.mime} (only PDF and text)",
                             settings=self.settings)
        if len(f.data) > INLINE_FILE_LIMIT:
            digest = await asyncio.to_thread(lambda: hashlib.sha256(f.data).hexdigest())
            file_id = self._file_ids.get(digest)
            if file_id is None:
                created = await client.files.create(file=(name, f.data, f.mime), purpose="user_data")
                file_id = str(created.id)
                self._file_ids[digest] = file_id
            return {"type": "file", "file": {"file_id": file_id}} if chat else {"type": "input_file", "file_id": file_id}
        data_url = await _data_url_async(f.mime, f.data)
        if chat:
            return {"type": "file", "file": {"filename": name, "file_data": data_url}}
        return {"type": "input_file", "filename": name, "file_data": data_url}

    async def _user_content(self, client: Any, prompt: str, files: Sequence[FileInput],
                            images: Sequence[ImageInput], *, chat: bool) -> list[dict[str, Any]]:
        content = [await self._file_part(client, f, chat=chat) for f in files]
        for im in images:
            url = _data_url(im.mime, im.data)
            content.append({"type": "image_url", "image_url": {"url": url}} if chat
                           else {"type": "input_image", "image_url": url, "detail": "auto"})
        content.append({"type": "text", "text": prompt} if chat else {"type": "input_text", "text": prompt})
        return content

    def _usage(self, model: str, operation: str, input_tokens: int, output_tokens: int, **meta: Any) -> Usage:
        return Usage(provider="openai", model=model, operation=operation, input_tokens=int(input_tokens or 0),
                     output_tokens=int(output_tokens or 0), meta={k: v for k, v in meta.items() if v})

    # --- streaming ---------------------------------------------------------------------------
    def _guard(self, kwargs: dict[str, Any], *, first: bool = True) -> StallGuard:
        """Stall limits of one streamed request (the first-response limit grows with its size).

        Until the first event the attempt's "still waiting" notices are held back near the
        first-response limit: a request that gets no answer is reported once, as a retry.
        """
        first_s: float | None = None
        if first:
            first_s = first_response_limit(self.settings.llm_first_response_timeout_seconds, payload_size(kwargs))
        hold_waiting(first_s)
        return StallGuard(first_s=first_s, idle_s=self.settings.llm_idle_timeout_seconds, on_alive=release_waiting)

    def _refused_stream(self, exc: BaseException, model: str) -> bool:
        """Whether ``exc`` refused streaming ``model`` (remembered: its later calls are not streamed)."""
        if not _stream_refused(exc):
            return False
        log.warning("openai: %s may not be streamed with this key; sending its requests without streaming "
                    "(no stall detection)", model)
        self._no_stream.add(model)
        release_waiting()  # the plain request shows no sign of life before its answer
        return True

    async def _stream_response(self, client: Any, kwargs: dict[str, Any]) -> tuple[Any, list[Any], str]:
        """``(final response, output items, output text)`` of a streamed Responses API call.

        The output comes from the final event; a final event without ``output`` is rebuilt from the
        ``response.output_item.done`` events (as the SDK's stream helper does). ``error`` events and
        ``response.failed`` raise ``OpenAIStreamError`` (``server_error`` / ``rate_limit_exceeded``
        are retried), and so does a stream that ends without a final event.
        """
        items: dict[int, Any] = {}
        reasoning: set[int] = set()  # open reasoning items: the model thinks silently
        final: Any = None
        async with self._guard(kwargs) as guard:
            stream = await client.responses.create(**kwargs, stream=True)
            async with stream:
                async for event in stream:
                    kind = str(getattr(event, "type", "") or "")
                    index = safe_attr(event, "output_index", default=-1)
                    if kind in _FINAL_EVENTS:
                        final = safe_attr(event, "response")
                        break  # never wait for the connection to close once the answer is complete
                    if kind == "error":
                        raise OpenAIStreamError(str(safe_attr(event, "code", default="") or ""),
                                                str(safe_attr(event, "message", default="") or ""))
                    if kind == "response.failed":
                        error = safe_attr(event, "response", "error")
                        raise OpenAIStreamError(str(safe_attr(error, "code", default="") or "response_failed"),
                                                str(safe_attr(error, "message", default="") or ""))
                    if kind == "response.output_item.added" and safe_attr(event, "item", "type") == "reasoning":
                        reasoning.add(index)
                    elif kind == "response.output_item.done":
                        reasoning.discard(index)
                        items[index] = safe_attr(event, "item")
                    guard.touch(expect_silence=bool(reasoning) or kind == "response.queued")
        if final is None:
            raise OpenAIStreamError("stream_ended", "the answer stopped before it was complete")
        output = safe_attr(final, "output")
        if output is not None:
            return final, list(output), str(getattr(final, "output_text", "") or "")
        rebuilt = [items[i] for i in sorted(items) if items[i] is not None]
        return final, rebuilt, _output_text(rebuilt)

    async def _responses(self, client: Any, *, model: str, system: str, user: list[dict[str, Any]],
                         followups: list[Turn], temperature: float, max_output_tokens: int | None,
                         json_schema: dict[str, Any] | None, name: str, operation: str) -> Completion:
        items: list[dict[str, Any]] = [{"role": "user", "content": user}]
        for t in followups:
            items.append({"role": "assistant" if t.role == "model" else "user", "content": t.text})
        kwargs: dict[str, Any] = {"model": model, "input": items}
        if system:
            kwargs["instructions"] = system
        if max_output_tokens:
            kwargs["max_output_tokens"] = int(max_output_tokens)
        if not _REASONING_MODEL.match(model):
            kwargs["temperature"] = temperature
        if json_schema is not None:
            kwargs["text"] = {"format": {"type": "json_schema", "name": name, "schema": json_schema, "strict": False}}
        streamed: tuple[Any, list[Any], str] | None = None
        if model not in self._no_stream:
            try:
                streamed = await self._stream_response(client, kwargs)
            except Exception as exc:
                if not self._refused_stream(exc, model):
                    raise
        if streamed is not None:
            resp, output, text = streamed
        else:  # this key may not stream the model: the same request, answered in one piece
            resp = await client.responses.create(**kwargs)
            output = list(safe_attr(resp, "output", default=[]) or [])
            text = str(getattr(resp, "output_text", "") or "")
        reason =str(safe_attr(resp, "incomplete_details", "reason", default="") or "")
        blocked = "content filter" if reason == "content_filter" else None
        for item in output:
            if getattr(item, "type", "") != "message":
                continue
            for c in getattr(item, "content", []) or []:
                if getattr(c, "type", "") == "refusal":
                    blocked = ("refused: " + str(getattr(c, "refusal", "") or "")).strip()
        usage = self._usage(
            model, operation,
            safe_attr(resp, "usage", "input_tokens", default=0),
            safe_attr(resp, "usage", "output_tokens", default=0),
            reasoning_tokens=safe_attr(resp, "usage", "output_tokens_details", "reasoning_tokens", default=0),
            cached_tokens=safe_attr(resp, "usage", "input_tokens_details", "cached_tokens", default=0),
        )
        return Completion(text=text, usage=usage, truncated=reason == "max_output_tokens", blocked=blocked)

    async def _chat(self, client: Any, *, model: str, system: str, user: list[dict[str, Any]], followups: list[Turn],
                    temperature: float, max_output_tokens: int | None, json_schema: dict[str, Any] | None, name: str,
                    operation: str) -> Completion:
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        for t in followups:
            messages.append({"role": "assistant" if t.role == "model" else "user", "content": t.text})
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if max_output_tokens:
            kwargs["max_completion_tokens"] = int(max_output_tokens)
        if not _REASONING_MODEL.match(model):
            kwargs["temperature"] = temperature
        if json_schema is not None:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": name, "schema": json_schema, "strict": False}}
        # Streamed; the usage comes in a last chunk without choices (include_usage). A reasoning model
        # may send its first chunk only after thinking: it gets no first-response limit here.
        texts: list[str] = []
        refusals: list[str] = []
        finish = ""
        usage_data: Any = None
        streamed = False
        if model not in self._no_stream:
            try:
                async with self._guard(kwargs, first=not _REASONING_MODEL.match(model)) as guard:
                    stream = await client.chat.completions.create(**kwargs, stream=True,
                                                                  stream_options={"include_usage": True})
                    async with stream:
                        async for chunk in stream:
                            guard.touch()
                            usage_data = safe_attr(chunk, "usage") or usage_data
                            choice = (safe_attr(chunk, "choices", default=[]) or [None])[0]
                            texts.append(str(safe_attr(choice, "delta", "content", default="") or ""))
                            refusals.append(str(safe_attr(choice, "delta", "refusal", default="") or ""))
                            finish = str(safe_attr(choice, "finish_reason", default="") or "") or finish
                streamed = True
            except Exception as exc:
                if not self._refused_stream(exc, model):
                    raise
        if not streamed:  # this key may not stream the model: the same request, answered in one piece
            resp = await client.chat.completions.create(**kwargs)
            choice = (safe_attr(resp, "choices", default=[]) or [None])[0]
            texts = [str(safe_attr(choice, "message", "content", default="") or "")]
            refusals = [str(safe_attr(choice, "message", "refusal", default="") or "")]
            finish = str(safe_attr(choice, "finish_reason", default="") or "")
            usage_data = safe_attr(resp, "usage")
        refusal = "".join(refusals)
        blocked = f"refused: {refusal}" if refusal else ("content filter" if finish == "content_filter" else None)
        usage = self._usage(model, operation, safe_attr(usage_data, "prompt_tokens", default=0),
                            safe_attr(usage_data, "completion_tokens", default=0))
        return Completion(text="".join(texts), usage=usage, truncated=finish == "length", blocked=blocked)

    async def _complete(self, *, model: str, system: str, prompt: str, files: Sequence[FileInput],
                        images: Sequence[ImageInput], followups: list[Turn], temperature: float,
                        max_output_tokens: int | None, json_schema: dict[str, Any] | None, name: str,
                        what: str) -> Completion:
        model = resolve_model(model)
        operation = "vision" if images else "llm"

        async def request(client: Any) -> Completion:
            chat = not hasattr(client, "responses")
            user = await self._user_content(client, prompt, files, images, chat=chat)
            call = self._chat if chat else self._responses
            return await call(client, model=model, system=system, user=user, followups=followups,
                              temperature=temperature, max_output_tokens=max_output_tokens, json_schema=json_schema,
                              name=name, operation=operation)

        return await self._caller.call(request, what=what)

    # --- LLMProvider ---------------------------------------------------------------------------
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
        """Structured generation (``json_schema`` format, ``strict: false``) + validation/re-ask loop."""
        json_schema = to_provider_schema(schema, "openai")
        name = schema_name(schema)

        async def complete(followups: list[Turn]) -> Completion:
            return await self._complete(model=model, system=system, prompt=prompt, files=files, images=images,
                                        followups=followups, temperature=temperature,
                                        max_output_tokens=max_output_tokens, json_schema=json_schema, name=name,
                                        what=f"{schema.__name__} generation")

        return await run_structured(complete=complete, schema=schema, validate=validate,
                                    validation_retries=validation_retries, on_usage=on_usage, provider=self.name,
                                    settings=self.settings)

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
        """Free-text generation."""
        completion = await self._complete(model=model, system=system, prompt=prompt, files=files, images=images,
                                          followups=[], temperature=temperature,
                                          max_output_tokens=max_output_tokens, json_schema=None, name="text",
                                          what="text generation")
        await settle(completion, on_usage=on_usage, provider=self.name, what="text generation",
                     settings=self.settings)
        if not completion.text.strip():
            raise make_error(ProviderError, self.name, "text generation returned no text", settings=self.settings)
        return completion.text
